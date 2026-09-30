# -*- coding: utf-8 -*-
from meterlib import cmd_bank       # 帧目录 + 语义动词积木箱(每步自带打印/判 PASS·FAIL)
from common import trial           # 运行外壳: argv/日志/账本/串口/会话/收尾三连/退出码
from common import judge            # 三态判定 + 观测通道/触发通道那两个常量(记进账本用)
from project import CURRENT          # 本工程画像: 表号/.out 路径/喂狗点(库不认表, 只有脚本认)
from swdbg import breakpoint            # 断点级白盒(停核 + 读函数内局部量); 没接 J-Link 会自动降级
from swdbg import gdbinit            # 整片 .out 地图: 开会话后解一次, 之后断点/注入点只查它

# ---- 子项参数(纯数据) ----
EVENT = "电表清零"            # 事件编码 0x13, 记录 OAD 30130B0A —— 判据①/第 4 步的观测对象
EVENT_KEEP = "编程"          # 事件编码 0x12。它落在『电表清零』记录**之后**, 故是被清/留的分水岭:
                             #   ID_AllRecd → 没了; ID_AllMeter → 还在。判据④ 的观测对象。
ELEC_DI = "00000000"         # 组合有功总电能(645 DI) —— 判据② 的观测对象
SUB_SETTLE = 0x05            # 结算冻结 —— 判据③ 主观测(:9257 Clear_FrezData(ID_AllFrez) 的要读的那一条)
SUB_HOUR = 0x03              # 小时冻结 —— 判据③ 副观测(两个子类里任一个被清/重置即算数)

# ---- 第 1/3 步要抄读的六样 698 参数量(OAD 与固件分派表对上的行号见各自注释) ----
# 六处都是普通 GET, 读回来的是 `read_oad_ud` 的原始 ud, 数据域由 `cmd_bank.oad_ud_data` 切出。
# 六样都压在 `Clear_MeterData` 的五处写参量上(:9262/:9264/:9271/:9278/:9279), 所以清后该全是 0 ——
#   这就是判据 ⑥ 的期望。⚠ md 第 1 步列的十样里, 需量那一半**走 645 读, 不走 698** ——
#   `0x20170200/20180200/20190200` 的 698 读分派在 DLT698App.c:13740-13744 整块被注释掉,
#   而 645 侧 DI `800004` 读得到(DLT645App.c:4799 Read_RealData(ID_DmdP))。
#   规范 6-1 明写电表清零要清掉最大需量, 所以它必须进 ⑥ 的合取里, 不能因为一条通道死了就把它划掉。
OAD_MONTH_KWH = "20310200"   # 月度用电量, 源 = Get_UsedkWh(ID_MonthkWhZ)(DLT698App.c:13786 读分派)
                             #   固件 :9264 清的就是 ID_MonthkWhZ 那一笔(kWhData.c:314 上月末总电量)
OAD_YEAR_KWH = "20320200"    # 阶梯结算用电量, 源 = Get_UsedkWh(ID_YearkWh)(DLT698App.c:13790)
                             #   对应固件 :9278 那一笔(本地表态才走)
OAD_BATT_WORK = "20130200"   # 电池工作时间(DLT698App.c:13720 读分派 → Read_ParaData(ID_BattWorkT))
                             #   对应固件 :9271 那一笔(厂内态才走)
OAD_NOLAW = "30271D00"       # 非法插卡总次数(DLT698App.c:9607-9611 读属性 29 → Read_ParaData(ID_NoLawNum))
                             #   对应固件 :9279 那一笔(本地表态才走)
OAD_GREEN_MON = "280C0400"   # 当月绿码个数(DLT698App.c:1077-1078, 读分派 :13816)
OAD_GREEN_ALL = "280C0500"   # 总绿码个数(DLT698App.c:1078, 读分派 :7528)
                             #   对应固件 :9262 Clear_GreenCode_Data_And_Save() 那一笔
OAD_ALL = (OAD_MONTH_KWH, OAD_YEAR_KWH, OAD_BATT_WORK, OAD_NOLAW, OAD_GREEN_MON, OAD_GREEN_ALL)

DMD_DI = "800004"            # 645 读「当前有功需量」—— 规范 6-1 要清的那一格, 本固件可读的就是它
#   (`DLT645App.c:4799-4801` `case 0x800004: Read_RealData(ID_DmdP, …)`; 无功/视在是 800005/800006)

# ---- 第 1 步记下的记录口几何(用来核第 4 步读回来的条数、第 5 步的封顶) ----
REC_CAP = 10                 # 电表清零记录容量(条)
REC_LEN = 80                 # 单条字节数 = 时刻 6 + 操作者 4 + 70 字节清零范围
REC_IDX_BASE = 0x4947        # 索引区首址(外部存储绝对地址)
                             #   ⚠ 头 3 字节是**不封顶**的总次数, 后 2 字节才是**封顶在 10** 的条数

# ---- 断点 ----
BP_CMD = ("line", "DLT645App.c", 3148)   # CMD_ClearMeter 命令体第一句 if(md 第 2 步的落点一)
BP_MAIN = ("call", "Clear_MeterData", "Is_EnablePrg", 2)
# 落点 = :9247 那句 `if (TRUE != Is_EnablePrg())` —— 正是 md 第 2 步要停的那一行(`offline_map` 换算实测)。
#   ⚠ 别改成 `("prev", …, 2)`: 那个落在 :9244 —— 正是 `id = Is_EnablePrg()? …` 那条赋值语句自己,
#     停在那儿 `id` 还没存进去, 读回来是栈垃圾(脚本里 `id` 的判据会静默失准)。
VARS_CMD = ("pFrame", "pFrame[0]", "pFrame[1]")
#   pFrame       = 形参(收到帧的指针 —— 证明命令真的收到了)
#   pFrame[0]    = 帧长字节(LEN 宏所指的那一格), md 第 2 步要核它是 0x08
#   pFrame[1]    = 第一DI 字节(DI0 宏所指的那一格), md 第 2 步要核它是 0x02
#   ⚠ 这两格用数字下标是**帧是字节数组**这一件事本身决定的(宏名在 gdb 里不一定展开),
#     不是拿位置当身份 —— 帧长度字节永远在第 0 格, 见 DLT645App.c 的 pFrame[LEN]/pFrame[DI0] 用法。
VARS_MAIN = ("id", "pOper")
#   id    = :9244 的赋值(枚举 ID_AllRecd/ID_AllMeter, 它编码了 Is_EnablePrg())
#   pOper = 形参(清零帧数据域指针 —— 证明触发帧的载荷真的到了主体)
WAIT_BP = 30.0               # 等断点命中的最长秒数。**不是发帧等待** —— 发帧等待在 CB.clear_meter 里;
                             #   命中前发帧线程一直阻塞(核心停着, 应答出不来), 所以这里要给得比 3s 松。


def _oad_zero(data):
    """普通 GET 的数据域 → 该对象是不是零值: True/False; 没读成(域空) → None。

    数据域首字节是类型码(`06` 无符号双长整型 4B, `0F` 单字节 …), 判定只看类型码之后的载荷;
    类型码认不出时退回"整个域全 0"。**None 是"没读到", 不是"值不对"** —— 两者必须分开记,
    不然一次静默会被记成"固件没清"(CLAUDE.md 第 15 条)。
    """
    if not data:
        return None
    body = data[1:] if data[0] in (0x06, 0x0F, 0x10, 0x11, 0x12, 0x16) else data
    return not any(body)


def _tri(halves):
    """一串三态半支 → 一个三态: 有 False 就是 False; 全 True 才是 True; 否则 None(没做成)。"""
    if any(x is False for x in halves):
        return False
    if any(x is None for x in halves):
        return None
    return True


def _read_oads(ser):
    """第 1/3 步共用的**六样 698 参数量**抄读 → {OAD: 数据域 bytes}(读不成的是 b'')。"""
    return dict((oad, cmd_bank.oad_ud_data(cmd_bank.read_oad_ud(ser, oad))) for oad in OAD_ALL)


def _oad_rows(pre, post, ctx, pre_dmd, post_dmd):
    """六样参数量 + 需量 清前/清后: 逐样出一条(crit=None, 只进日志), 再合出一条认领判据 ⑥。"""
    oks, detail = [], []
    for oad, name, fals in (
            (OAD_MONTH_KWH, "上月总电量", "固件 :9264 落了 ID_MonthkWhZ 那一笔, 读回却仍非零"),
            (OAD_YEAR_KWH, "上年总电量", "固件 :9278 落了 ID_YearkWh 那一笔, 读回却仍非零"),
            (OAD_BATT_WORK, "电池工作时间", "厂内态下 :9271 那一笔落了, 读回却仍非零"),
            (OAD_NOLAW, "非法插卡次数", "本地表态下 :9279 那一笔落了, 读回却仍非零"),
            (OAD_GREEN_MON, "当月绿码个数", "固件 :9262 Clear_GreenCode_Data_And_Save() 落了, 读回却仍非零"),
            (OAD_GREEN_ALL, "总绿码个数", "固件 :9262 Clear_GreenCode_Data_And_Save() 落了, 读回却仍非零")):
        z0, z1 = _oad_zero(pre[oad]), _oad_zero(post[oad])

        def hx(b):
            return b.hex(" ").upper() if b else "(无应答)"

        if z0 is None or z1 is None:
            ok, why = None, ("OAD %s 清前=%s 清后=%s —— 有一头没读成, 只有一种观测(未证)"
                             % (oad, hx(pre[oad]), hx(post[oad])))
        elif z1 and not z0:
            ok, why = True, "OAD %s 清前=%s → 清后=%s(归零)" % (oad, hx(pre[oad]), hx(post[oad]))
        elif z1 and z0:
            ok, why = None, ("OAD %s 清前=%s 本就是零 —— 0 值台上『清过』与『没清』读数一样, "
                             "本条无区分力(未证)" % (oad, hx(pre[oad])))
        elif pre[oad] == post[oad]:
            ok, why = False, "OAD %s 清前=%s → 清后=%s 一字未动" % (oad, hx(pre[oad]), hx(post[oad]))
        else:
            ok, why = False, ("OAD %s 清前=%s → 清后=%s 没归零" % (oad, hx(pre[oad]), hx(post[oad])))
        ctx.J.add("%s(OAD %s)" % (name, oad), ok, why, crit=None, falsify=fals)
        oks.append(ok)
        detail.append("%s=%s" % (name, "--" if z1 is None else ("0" if z1 else "非0")))

    # 需量那一格走 645 DI 800004, 与上面六样不同通道, 所以单列一条; 它也进 ⑥ 的合取。
    _z0, _z1 = _oad_zero(pre_dmd), _oad_zero(post_dmd)
    _hx = lambda b: b.hex(" ").upper() if b else "(无应答)"
    if _z1 is None or _z0 is None:
        _okd, _whyd = None, "需量 DI %s 清前=%s 清后=%s —— 有一头没读成" % (DMD_DI, _hx(pre_dmd), _hx(post_dmd))
    elif _z1 and not _z0:
        _okd, _whyd = True, "需量 DI %s 清前=%s → 清后=%s(归零)" % (DMD_DI, _hx(pre_dmd), _hx(post_dmd))
    elif _z1 and _z0:
        _okd, _whyd = None, ("需量 DI %s 清前=%s 本就是 0 —— 0 需量台上『清过』与『没清』读数同形, "
                             "本条无区分力(未证)" % (DMD_DI, _hx(pre_dmd)))
    else:
        _okd, _whyd = False, "需量 DI %s 清前=%s → 清后=%s 未被清空" % (DMD_DI, _hx(pre_dmd), _hx(post_dmd))
    ctx.J.add("需量(DI %s)" % DMD_DI, _okd, _whyd, crit=None,
              falsify="规范 6-1 要求电表清零清掉最大需量, 读回却仍非 0")
    oks.append(_okd)
    detail.append("需量=%s" % ("--" if _z1 is None else ("0" if _z1 else "非0")))

    # 判据 ⑥ 是一条**合取**条目(七样全归零才算满足), 所以合成一条记录交给 judge。
    #   ⚠ 不许把上面六条各自挂 crit="⑥": judge 认的是"认领该条的记录里至少一条 True、且无 False"
    #     (common/judge.crit_states), 平铺上去之后六样里坏五样、好一样也报满足。
    ctx.J.add("清后参数量归零(六样 OAD + 需量)", _tri(oks), " / ".join(detail),
              crit="⑥参数", obs=judge.SERIAL,
              falsify="任一样固件写进去了、读回却仍非 0(逐样数值见上面六条记录与 log); "
                      "某一样清前本就是 0 ⇒ 那一样无区分力, 整条只能记未证, 不许据此报满足")


def part_clear(ctx):
    """5-4 电表清零: 抄读基线 → 编程态发 645 0x1A 并停两处取证 → 回读比对 → 记录口条数不动 → 容量离线核。"""
    # ---- 阶段一(第 1 步): 进编程态 + 抄读基线 ----
    # 两处冻结记录要求台面在清之前已有(一处整点冻结、一处结算日冻结) —— 那是台上条件, 不是脚本能造出来的。
    cmd_bank.enter_factory(ctx.ser)
    t0 = cmd_bank.read_clock(ctx.ser, quiet=True)

    pre_evt = cmd_bank.read_event_row(ctx.ser, EVENT, 1)               # 判据①/第 4 步: 电表清零记录
    pre_prg = cmd_bank.read_event_row(ctx.ser, EVENT_KEEP, 1)          # 判据④: 编程(分水岭)
    pre_n = cmd_bank.event_area_count(ctx.ser, EVENT)                  # 第 4 步: 记录口**条数**
    pre_frz = {s: cmd_bank.read_freeze_row(ctx.ser, s, 1) for s in (SUB_SETTLE, SUB_HOUR)}   # 判据③
    pre_elec = cmd_bank.read_param_di(ctx.ser, ELEC_DI)                # 判据②
    pre_oad = _read_oads(ctx.ser)                                      # 第 1 步: 六样参数量
    pre_dmd = cmd_bank.read_param_di(ctx.ser, DMD_DI, quiet=True)      # 第 1 步: 需量(645 那一格)

    # ---- 开调试会话(断点观测)。台面没接 J-Link → 返回 None, 后面 trigger 自动直呼 ----
    ctx.session()
    if ctx.g is not None:
        # 整片 .out 解一次(停核遍历, 内部喂狗, 收尾放行) —— 之后一律查这张图
        gdbinit.build(ctx.g)

    # ---- 阶段二(第 2 步): 编程态下停两处 ----
    # 两处都 crit=None(旁证): 它们证的是"这一次打进了那个函数:行、那三格是什么值", 证不了分区取值 ——
    #   把 crit 挂在它们身上会让"命中"单独把 判据④ 记成满足, 而 `id` 读回来是垃圾也照样绿。
    #   判据④ 的认领只在 CB.clear_library_evidence() 那一处(它要求黑盒白盒两种观测都在)。
    wb_id = None                       # 断[B] 读回的 `id` 原文; 没接 J-Link 时保持 None
    for tag, bp, vars_, note, fals in (
            ("A", BP_CMD, VARS_CMD, "命令(645 0x1A 进了 CMD_ClearMeter 没有; pFrame[0]=帧长, pFrame[1]=DI0)",
             "645 0x1A 没进命令 ⇒ 不会停在 :3148"),
            ("B", BP_MAIN, VARS_MAIN, "清零主体(过了 :3178 那道判定、进了 Clear_MeterData 没有)",
             "清零不走 Clear_MeterData(被 :3178 那道判定挡回) ⇒ 不会停在 :9247")):
        # 一行一次: 下断点 → 发帧 → 等命中 → 读量 → 包成证据记录, 全在 `GD.fire_hit` 里。
        # `drop=True`: 趁停住把断点撤了再放行 —— 腾槽(Cortex-M0 只有 4 个), 也让下一次的断点
        #   一定是新下的, 不靠上一次残留。
        rec = breakpoint.fire_hit(ctx.g, bp, cmd_bank.clear_meter, ctx.ser, timeout=WAIT_BP,
                                  vars=vars_, drop=True,
                                  label="断[%s] %s %s" % (tag, breakpoint.text(bp), note), falsify=fals)
        # `rec is None` = 台面没接 J-Link(或 `--no-gdb`), **不是"没命中"**: 这一次的清零帧
        #   已经发出去了(fire_hit 里那句直呼), 黑盒观测照常; 断点下不上时库会大声打印。
        ctx.take([rec])
        if tag == "B" and rec is not None:
            wb_id = rec["vars"].get("id")
        if tag == "A" and rec is not None:
            # 第 2 步要的三道判定里的两道: pFrame[0]=0x08(帧长), pFrame[1]=0x02(DI0)。
            #   第三道 Is_EnablePrg() 就在 断[B] 停的那一行上, 读回来为真才可能停到 :9247 之后。
            v = rec["vars"]
            ctx.J.add("编程态判定帧长/DI0(pFrame[0]=%s, pFrame[1]=%s)" % (v.get("pFrame[0]"), v.get("pFrame[1]")),
                      None, "判据数字由第 3 步的黑盒结果与断[B] 的命中位置共同确定; 这两格只作留痕",
                      crit=None, falsify="帧长不是 0x08 或 DI0 不是 0x02 ⇒ :3171 那道判定回 ER_PSWD")

    # ---- 阶段三(第 3 步): 与第 1 步记下的数逐一比 ----
    post_evt = cmd_bank.read_event_row(ctx.ser, EVENT, 1)
    post_prg = cmd_bank.read_event_row(ctx.ser, EVENT_KEEP, 1)
    post_n = cmd_bank.event_area_count(ctx.ser, EVENT)
    post_frz = {s: cmd_bank.read_freeze_row(ctx.ser, s, 1) for s in (SUB_SETTLE, SUB_HOUR)}
    post_elec = cmd_bank.read_param_di(ctx.ser, ELEC_DI)
    post_oad = _read_oads(ctx.ser)
    post_dmd = cmd_bank.read_param_di(ctx.ser, DMD_DI, quiet=True)

    # 判据①: 本台已知不可达(条目里声明了为什么) ⇒ ok 记 **None 而不是 False** ——
    #   "没落记录"是本台三条准入路都够不着, 不是"观察到固件不对"; 记 False 会让整项判「失败」, 反过来冤枉固件。
    #   crit=None: 该条声明了 unprovable, 不许有认领者。
    adv, why = cmd_bank.event_advanced(post_evt, pre_evt, t0)
    ctx.J.add("电表清零记录 %s" % ("落了" if adv else "未新增"), True if adv else None, why,
              crit=None, falsify="清零受理了却没落记录 ⇒ 与『永久记录必落』不符")
    # 判据④: 黑盒(分水岭在不在) × 白盒(:9247 的 id 是哪个分区) 是否自洽
    ctx.take([cmd_bank.clear_library_evidence(pre_prg, post_prg, cmd_bank.clear_partition(wb_id),
                                              tag="记录库清空范围(黑盒×白盒)")])
    # 判据③ ×2: 结算/小时冻结 序号 清前→清后
    for s, name in ((SUB_SETTLE, "结算"), (SUB_HOUR, "小时")):
        ctx.take([cmd_bank.clear_freeze_evidence(name, pre_frz[s], post_frz[s], tag="%s冻结被清" % name)])
    # 判据②: 电量归零(0 负载台面上本就没有区分力, 库里会如实记未证)
    ctx.take([cmd_bank.clear_elec_evidence(pre_elec, post_elec, ELEC_DI)])
    # 第 3 步余下的参数量(六样 OAD + 需量): 逐样一条只进日志, 合起来那条认领判据 ⑥
    _oad_rows(pre_oad, post_oad, ctx, pre_dmd, post_dmd)

    # ---- 阶段四(第 4 步): 厂内态清零不产生电表清零记录 —— 记录口条数与序号都不动 ----
    # 写在 Clear_MeterData 里那几处 Write_RecdData(ID_ClearMeter, …) 只有非厂内态那一道走得到,
    #   所以本步的期望是"一动不动"。录 ok=None: 本台够不到"落得下"那一半, 只能证"没动",
    #   证不了"该落的时候落得下"(参见判据①的不可达理由)。
    ctx.J.add("清零记录口 30130B0A 条数 %s→%s(序号 %s→%s)"
              % (pre_n, post_n, (pre_evt or {}).get("seq"), (post_evt or {}).get("seq")),
              None, "本台期望『不动』; 读回条数/序号若动了反而是异常 —— 见下一条 falsify 口径, 本条只留痕",
              crit=None,
              falsify="条数或序号推进了 ⇒ 厂内态本不该落这条记录(DLT645App.c:9247 起那几处只有非厂内态走得到)")

    # ---- 阶段五(第 5 步): 容量与不封顶的总次数 —— 本台不上表, 几何记在这里备下回离线核 ----
    # 清零不可逆, 本台不连做 11 回。容量 %d 条、单条 %d 字节、索引区首址 0x%04X(头 3 字节不封顶的总次数、
    #   后 2 字节封顶在 %d)是 md 第 5 步记下的几何, 也是"上表那几步读到条数时"的解释口径;
    #   连做 11 回的封顶观察须另安排台面(本项这一半本轮未测)。
    ctx.J.add("电表清零记录口几何", None,
              "容量 %d 条 / 单条 %d 字节 / 索引区首址 0x%04X(头 3B 总次数不封顶, 后 2B 封顶在 %d) —— "
              "连做 11 回看顶掉这一半本轮未测(清零不可逆, 本台不连做 11 回)"
              % (REC_CAP, REC_LEN, REC_IDX_BASE, REC_CAP),
              crit=None, falsify="上表读到条数超过 %d ⇒ 封顶没生效" % REC_CAP)

    # ---- 判据⑤: 清后再落一条记录, 看存储重建没有 ----
    # 必须排在 post_prg **之后**(本步自己会新增『编程』记录, 早做就把判据④ 的分水岭污染了)。
    # 触发 = 写结算日再写回(库动词自带恢复, 表无净变); 写库判定只有 comSta==OK_FRAME
    # (DLT645App.c:2926), 与判据① 那些准入条件无关 ⇒ 不需任何密钥。
    pre_prog2 = cmd_bank.read_event_row(ctx.ser, EVENT_KEEP, 1)
    write_ok, _d0 = cmd_bank.billday_rw_roundtrip(ctx.ser)
    post_prog2 = cmd_bank.read_event_row(ctx.ser, EVENT_KEEP, 1)
    ctx.take([cmd_bank.clear_rebuild_evidence(pre_prog2, write_ok, post_prog2,
                                              tag="清后记录库重建(写参量→读编程记录)")])


def _banner():
    return "== 5-4 电表清零 | 工程=%s 表号=%s ==" % (CURRENT.PROJECT, CURRENT.TABLE_ADDR.hex().upper())


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "5-4 电表清零(电量/需量/冻结被清, 清零事件永久保留)",
        cmd_bank.clear_meter_criteria,
        name="5_4_clear_meter",
        parts=[("5-4 清零段", part_clear)],
        gdb=breakpoint, banner=_banner(),
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV)))
