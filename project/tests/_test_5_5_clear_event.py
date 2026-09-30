# -*- coding: utf-8 -*-
from meterlib import cmd_bank       # 帧目录 + 语义动词积木箱(每步自带打印/判 PASS·FAIL)
from common import trial           # 运行外壳: argv/日志/账本/串口/会话/收尾三连/退出码
from common import judge            # 三态判定 + 观测通道/触发通道那两个常量(记进账本用)
from project import CURRENT          # 本工程画像: 表号/.out 路径/喂狗点(库不认表, 只有脚本认)
from swdbg import breakpoint            # 断点级白盒(停核 + 读函数内局部量); 没接 J-Link 会自动降级
from swdbg import gdbinit            # 整片 .out 地图: 开会话后解一次, 之后断点/注入点只查它

# ---- 子项参数(纯数据) ----
EVENT_PRG = cmd_bank.CLR_EV_ID_PRG      # 0x12 『编程』—— ③a 的要读的那一条(前置写入)
EVENT_CLR = cmd_bank.CLR_EV_ID_CLR      # 0x15 『事件清零』—— ③b / ④b 的永久记录
EVENT_METER = "电表清零"                 # 0x13 —— 第 1 步抄条数时多带一块(它是永久记录, 全清不该动它)
PART_DIS = cmd_bank.CLR_EV_DIS_PART     # 0x033000FF 部分标识样本(⑤ 的探针; 本台应被 :3321 拒)
EVENT_DMD = 0x14                        # 『需量清零』事件编码(698 记录对象 30140B0A) —— 第 5 步的观测点
DMD_CMD = 0x19                          # 645 清最大需量的命令字; 应答命令字 = 0x99
DMD_DATA = bytes.fromhex("02 00 00 00 00 00 00 00")
#   数据域(解码后)8 字节 = [DI0]=0x02 厂内控制判定 + 4B 操作者代码, 与 `CLEAR_METER_DATA` 同形
#   (`DLT645App.c:3124-3126` 的本地路要求 LEN=0x08 且 DI0=0x02 且 Is_EnablePrg 为真)。
DMD_DI = "800004"            # 645 读「当前有功需量」—— 规范 6-3 要清的那一格在本固件可读的就是它
#   (`DLT645App.c:4799-4801` `case 0x800004: Read_RealData(ID_DmdP, …)`; 无功/视在是 800005/800006)。
#   ⚠ 698 侧那条路是死的: 0x20170200/20180200/20190200 的读分派在 DLT698App.c:13740-13744 整块被注释掉。

# ---- 第 1 步记下的记录口几何(解释第 3 步读回来的条数) ----
REC_CAP = 10                 # 事件清零记录容量(条)
REC_LEN = 14                 # 单条字节数 = 发生时刻 6 + 操作者代码 4 + 数据标识码 4(TaskRecord.c:941)
REC_IDX_BASE = 0x4942        # 索引区首址(外部存储绝对地址)
                             #   ⚠ 头 3 字节是**不封顶**的总次数, 后 2 字节才是**封顶在 10** 的条数

# ---- 断点(纯数据) ----
# `BP_<X>` ↔ `VARS_<X>` 是**承重的**(scripts/_check_anchors.py 靠它配对, 拿去对源码核
# "要读的变量在断点那一行赋过值没有"): 名字写歪 = 这种观测静默地没人核过; `VARS_<X>` 必须是**字面量元组**。
BP_STORE = ("prev", "CMD_ClearEvent", "Recd_ClrEvent", 1)   # 写库位置: Recd_ClrEvent(&DAT4, &DAT0) @DLT645App.c:3399
VARS_STORE = ("pFrame[18]", "pFrame[19]", "pFrame[20]", "pFrame[21]")
#   pFrame[18..21] = DAT4..DAT7(帧偏移 DLT645App.c:44-88 的 enum: DAT0=14 ⇒ DAT4=18),
#   也就是落在记录里那个**数据标识码**。第 2 步要核它被归一成了 `00 05 00 43`。
BP_GATE = ("line", "DLT645App.c", 3317)                      # 非编程态拒出口 `return ER_PSWD;` —— 旁证那一次
VARS_GATE = ("pFrame[9]", "pFrame[10]")
#   pFrame[9] = LEN 格, pFrame[10] = DI0 格 —— 那一帧自己的帧长与第一DI,
#   把 :3313-3315 那"三道判定"(LEN/DI0/Is_EnablePrg)收窄成读得到的那一条。
BP_DMD = ("line", "DLT645App.c", 3132)      # 第 5 步: 命令体末尾 `pFrame[LEN] = 0x00;` —— 判定全过了才会到这儿
VARS_DMD = ("pFrame[9]", "pFrame[10]")
#   停在这儿即证: 三道判定(LEN/DI0/Is_EnablePrg)全过、命令体走到了收尾那一句。
#   ⚠ 这一句**之后**没有任何调用了 —— 这正是第 5 步要证的那件事(命令体不含清零、不含落库)。
WAIT_BP = 30.0                    # 等 STORE 命中: 不是发帧等待, 发帧等待在库里; 命中前发帧线程阻塞
WAIT_GATE = 15.0                  # 等 GATE 命中(这一次**期望命中** —— 被拒就是走那个出口)
WAIT_DMD = 15.0                   # 等 DMD 命中 —— 同 WAIT_GATE, 期望命中


def _tri(halves):
    """一串三态半支 → 一个三态: 有 False 就是 False; 全 True 才是 True; 否则 None(没做成)。"""
    if any(x is False for x in halves):
        return False
    if any(x is None for x in halves):
        return None
    return True


def _ev_counts(ser):
    """各事件块**条数**(698 读记录区) → {事件名: 条数|None}。第 1 步的基线就是它。"""
    return dict((nm, cmd_bank.event_area_count(ser, code))
                for nm, code in (("编程", EVENT_PRG), ("事件清零", EVENT_CLR), ("电表清零", EVENT_METER)))


def _count_why(counts):
    """{事件名: 条数} → 一行文本(`None` 写成"读不出", 不写成 0)。"""
    return " / ".join("%s=%s" % (nm, "读不出" if n is None else n) for nm, n in counts.items())


def part_clear(ctx):
    """5-5 事件清零: 抄各事件块条数 → 编程态全清并停在写库位置 → 回读比对 → 退厂内再发被拒 → 发 645 0x19 看落不落记录。"""
    # ---- 阶段一(第 1 步): 进厂内 + 抄各事件块条数, 再写一个参数落一条编程记录 ----
    cmd_bank.enter_factory(ctx.ser)
    counts0 = _ev_counts(ctx.ser)
    # 预置基准值: 645 0x14 写结算日再写回(自带恢复 ⇒ 表无净变, 但固件必落一条『编程』记录) ——
    #   这一条就是 ③a 要观察"被全清抹掉"的那个。
    planted, _d0 = cmd_bank.billday_rw_roundtrip(ctx.ser)
    t0 = cmd_bank.read_clock(ctx.ser, quiet=True)                      # 判"记录是不是本次落的"下界
    pre_prg = cmd_bank.read_event_row(ctx.ser, EVENT_PRG, 1)           # ③a 的要读的那一条
    pre_ev = cmd_bank.read_event_row(ctx.ser, EVENT_CLR, 1)            # ③b / ④b
    # crit=None: 判据表 5-5 的条目是 ①②③a③b④a④b⑤ —— "各事件块条数"没有对应条目, 只进日志。
    ctx.J.add("各事件块条数(清前)", None, _count_why(counts0), crit=None,
              falsify="条数读不出 ⇒ 清后没有对照, ③a/③b 只能靠单条记录判")

    # ---- 开会话(断点观测)。台面没接 J-Link / 用户指定只做黑盒 ⇒ None, 串口观测照跑 ----
    ctx.session()
    if ctx.g is not None:
        # 整片 .out 解一次(停核遍历, 内部喂狗, 收尾放行) —— 之后一律查这张图
        gdbinit.build(ctx.g)

    # ---- 阶段二(第 2 步): 编程态发 645 0x1B 全清(LEN=0x0C, 帧 18..21 写字面 FFFFFFFF), 停在 :3399 ----
    # `ctx.trigger()` = 本次自己触发一次动作: 没会话时**照样发帧**(黑盒观测不能跟着消失),
    # 返回里 `hit=None`; `result` 带着触发函数的真返回值(判据① 的串口读数就从这儿来 ——
    # 这是它比 `GD.fire_hit` 强的一点: fire_hit 只回记录, 不回被触发函数的返回值)。
    # `_vars`/`_drop`/`timeout` 是给 with_trigger 的控制参数(没会话时被 trigger 摘净, 不会漏给 fn)。
    # `drop=True`: 命中与没命中都必撤(**不只是腾 4 个硬件槽** —— 留在槽里的断点会自己命中把核撂停,
    # 后面串口全哑)。
    r1 = ctx.trigger()(BP_STORE, cmd_bank.clr_event_send, ctx.ser, timeout=WAIT_BP,
                       _vars=VARS_STORE, _drop=True)
    ctx.take([cmd_bank.clr_event_accept_evidence(r1), cmd_bank.clr_event_id_evidence(r1)])

    # ---- 阶段三(第 3 步): 698 清后回读, 与第 1 步记下的数比 ----
    post_prg = cmd_bank.read_event_row(ctx.ser, EVENT_PRG, 1)          # 期望: 没了
    post_ev = cmd_bank.read_event_row(ctx.ser, EVENT_CLR, 1)           # 期望: 还在, 还多一条、时刻推进
    counts1 = _ev_counts(ctx.ser)
    ctx.J.add("各事件块条数(清后)", None, _count_why(counts1), crit=None,
              falsify="清前后条数一字未动 ⇒ 全清没抹掉事件块")
    ctx.take([cmd_bank.clr_event_block_evidence(pre_prg, post_prg),
              cmd_bank.clr_event_keep_evidence(pre_ev, post_ev, t0)])
    if not planted:
        ctx.J.note("5-5: 前置预置基准值(结算日读写闭环)没成功 ⇒ ③a 的要读的那一条可能没写进去, 该条会记未证")

    # ---- 阶段四(第 4 步): ⑤ 的探针(必须厂内发) → 退厂内再发一次 → 回厂内读记录 ----
    # 顺序是承重的: ⑤ 必须厂内发(厂外会先撞 :3317 的判定, 答的是 ER_PSWD 不是 ER_D0D1);
    # ④a/④b 必须厂外发(那一对测的就是"非编程态被拒")。
    # ⚠ 旁证是 `crit=None`(不认领条目) ⇒ `judge.render` **不会**把它印进汇总
    #   (它只印"认领了条目却没有 falsify"的那种弱证据)。所以这里显式 `note` 出来 ——
    #   不然"旁证取到了没有"这件事在输出里彻底看不见, 只能去翻 log。
    _probe = cmd_bank.clr_event_partial_probe(ctx.ser, dis=PART_DIS)
    ctx.take([_probe])
    ctx.J.note("旁证(不认领条目) %s: %s" % (_probe["name"], _probe["detail"]))

    cmd_bank.exit_factory(ctx.ser)                                     # 退厂内 → 非编程态
    r2 = ctx.trigger()(BP_GATE, cmd_bank.clr_event_send, ctx.ser, timeout=WAIT_GATE,
                       _vars=VARS_GATE, _drop=True)
    _gate = cmd_bank.clr_event_gate_evidence(r2)                       # 旁证(crit=None)
    ctx.take([_gate])
    ctx.J.note("旁证(不认领条目) %s: %s" % (_gate["name"], _gate["detail"]))
    cmd_bank.enter_factory(ctx.ser)                                    # 回厂内: 记录读回要在这儿做
    post_ev2 = cmd_bank.read_event_row(ctx.ser, EVENT_CLR, 1)
    ctx.take(cmd_bank.clr_event_reject_evidence((r2 or {}).get("result"), post_ev, post_ev2))

    # ---- 阶段五(第 5 步): 645 0x19 清最大需量 —— 按规范判, 不按固件现状判 ----
    # **尺子是规范, 不是这份固件的反汇编**:
    #   规范 6-3 第 1 条 清空电能表管理芯内当前的最大需量及发生的日期、时间等数据
    #   规范 5-5 第 1 条 应记录需量清零、事件清零的总次数, 以及最近 10 次需量清零、事件清零的时刻
    # 所以期望是**两件都做到**: 需量被清空 + 落一条『需量清零』记录。
    # ⚠ 本固件两件都没做 —— CMD_ClearMaxDmd(DLT645App.c:3102) 的命令体只有判定, 收尾是
    #   `pFrame[LEN] = 0x00; return OK_FRAME;`, 既无清零调用也无任何 Recd_*; 唯一候选记录口
    #   30140B0A 容量 NUM_ClrDemand=0u(RecdData.h:127)。所以这两条**期望就是 FAIL**。
    #   把固件的"没做"抄成判据的"应当如此", 等于给缺陷发合格证 —— 判据的任务是把它报出来。
    def send_dmd():
        f = cmd_bank.frame_645(DMD_CMD, DMD_DATA, addr=cmd_bank.TABLE_ADDR)
        rx = cmd_bank.send_frame(ctx.ser, f, wait=WAIT_DMD, tag="clr_dmd",
                                 peer="管理芯", what="0x19 清最大需量")
        return cmd_bank.decode_645_reply(rx)

    dmd0 = cmd_bank.read_param_di(ctx.ser, DMD_DI, quiet=True)
    n_dmd0 = cmd_bank.event_area_count(ctx.ser, EVENT_DMD)
    r5 = ctx.trigger()(BP_DMD, send_dmd, timeout=WAIT_DMD, _vars=VARS_DMD, _drop=True)
    dmd1 = cmd_bank.read_param_di(ctx.ser, DMD_DI, quiet=True)
    n_dmd1 = cmd_bank.event_area_count(ctx.ser, EVENT_DMD)

    _res5 = (r5 or {}).get("result")
    _cmd5 = _res5[0] if isinstance(_res5, tuple) else None
    _acc5 = True if _cmd5 == (DMD_CMD | 0x80) else (False if _cmd5 is not None else None)   # 旁证: 受理

    def _hx(b):
        return b.hex(" ").upper() if b else "(无应答)"

    # ⚠ 没受理的时候, 下面两条量的是"没受理"而不是"受理了没做" ⇒ 记 None, 别把两件事记成一件。
    _noacc = "645 0x19 未被受理(应答 %s) ⇒ 不构成『受理后没做』的证据" % (_cmd5 if _cmd5 is not None else "未读到")

    # ⑥a: 需量被清空 —— 清后必须是 0
    if _acc5 is False:
        _a5, _why_a = None, _noacc
    elif dmd1 is None or dmd0 is None:
        _a5, _why_a = None, ("需量 DI %s 清前=%s 清后=%s —— 有一头没读成, 观测没打通"
                             % (DMD_DI, _hx(dmd0), _hx(dmd1)))
    elif not any(dmd1):
        _a5, _why_a = True, "需量 DI %s 清前=%s → 清后=%s(清空)" % (DMD_DI, _hx(dmd0), _hx(dmd1))
    elif not any(dmd0):
        _a5, _why_a = None, ("需量 DI %s 清前=%s 本就是 0 —— 0 需量台上『清过』与『没清』读数同形, "
                             "本条无区分力(未证), 要证须先让需量非零" % (DMD_DI, _hx(dmd0)))
    else:
        _a5, _why_a = False, "需量 DI %s 清前=%s → 清后=%s 未被清空" % (DMD_DI, _hx(dmd0), _hx(dmd1))
    ctx.J.add("第 5 步 645 0x19 受理后需量被清空", _a5, _why_a, crit="⑥a", obs=judge.SERIAL,
              falsify="受理了却清前清后读数一字未变(且清前非零) ⇒ 违反规范 6-3 第 1 条, 本条不满足")

    # ⑥b: 落一条『需量清零』记录 —— 该口总次数必须 +1
    if _acc5 is False:
        _b5, _why_b = None, _noacc
    elif n_dmd0 is None or n_dmd1 is None:
        _b5, _why_b = None, ("『需量清零』记录口(30140B0A)条数 %s→%s —— 有一头读不出"
                             % (n_dmd0, n_dmd1))
    elif n_dmd1 > n_dmd0:
        _b5, _why_b = True, "记录口条数 %s→%s(新增)" % (n_dmd0, n_dmd1)
    else:
        _b5, _why_b = False, ("记录口条数 %s→%s —— 受理了却没落记录, 违反规范 5-5 第 1 条"
                              % (n_dmd0, n_dmd1))
    ctx.J.add("第 5 步 645 0x19 受理后落一条『需量清零』记录", _b5, _why_b, crit="⑥b", obs=judge.SERIAL,
              falsify="受理了该口条数却不增(固件这边是 NUM_ClrDemand=0u, 容量 0, 永远落不下) "
                      "⇒ 违反规范 5-5 第 1 条, 本条不满足")

    ctx.J.note("第 5 步旁证(不认领条目): 应答命令字=%s(期望 0x99) / 断[%s] %s"
               % (_cmd5 if _cmd5 is not None else "未读到", breakpoint.text(BP_DMD),
                  "命中(命令体走到了收尾那一句)" if (r5 or {}).get("ok") is True
                  else ("没停到" if r5 is not None else "无调试会话")))


def _banner():
    return ("== 5-5 事件清零 | 工程=%s 表号=%s ==\n"
            "!! 本跑会清空台上事件记录库(永久记录『事件清零』『电表清零』保留); 结算日读写自带恢复"
            % (CURRENT.PROJECT, CURRENT.TABLE_ADDR.hex().upper()))


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "5-5 事件清零(全清抹事件块, 永久记录保留且新增, 非编程态被拒)",
        cmd_bank.clear_event_criteria,
        name="5_5_clear_event",
        parts=[("5-5 全清段", part_clear)],
        gdb=breakpoint, banner=_banner(),
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV)))
