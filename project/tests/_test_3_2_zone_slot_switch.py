# -*- coding: utf-8 -*-
"""在证什么: 两套时区表/时段表的切换 —— 到点自动切套(②a) / 切后当前套内容 == 原备用套(②b) /
  时区、时段各出一帧对应的切换冻结(③) / 切后费率归属按新套(④) / 设定被清故不重复切(⑤a) /
  未到点不误切(⑤b)。每一条 zone、slot 两支各判一次, 所以同一段跑两遍。
会向表写什么: 645 0x14 写切换设定(zone DI 04000106 / slot 04000107)—— 设成**过去时刻即当场切套**;
  另会改写**备用套**(zone 时区表第 1 项月 +1 / slot 当刻那张时段表费率逐段 +1, 这是 ②b/④ 有分辨力的前提),
  收尾把备用套写回原值并再切一次, 让当前套也回到原值。⚠ 每次写备用套会在表里留一条编程记录
  (Recd_PrgZoneTab / Recd_PrgSlotTab), 抹不平。本脚本自己发 645 进厂内, 跑完表停在厂内态。
跑法: `python project/tests/_test_3_2_zone_slot_switch.py`(真串口 + 真探针; 没接探针只做黑盒)。
结论怎么读: 账本末行「判据: 满足 n/N」, N=6; 退 0 = 通过 / 1 = 失败 / 2 = 未定论。
"""
import datetime
import time

from common import judge            # 观测种类常量(断点那几条证据标 DEBUG)
from common import trial            # 运行外壳: argv/日志/账本/串口/会话/收尾三连/退出码
from meterlib import cmd_bank       # 帧目录 + 语义动词积木箱(每步自带打印)
from meterlib import watch           # AA80 读内存(读 g_ZoneSwNo/g_SlotSwNo 就用它)
from project import CURRENT          # 本工程画像: 表号/.out 路径/喂狗点(库不认表, 只有脚本认)
from swdbg import breakpoint            # 断点级白盒(停核 + 读函数内局部量); 没接 J-Link → None
from swdbg import gdbinit            # 整片 .out 地图: 开会话后解一次, 之后断点/注入点只查它

# ---- 子项参数(纯数据) ----
KINDS = ("zone", "slot")        # 时区表切换 → 时段表切换(判据③要求"各出一帧", 故两支都要跑)
WAIT = 2.5                      # 每条 645/698 帧等应答的秒数
LEAD_MIN = 10                   # "未来设定"离表钟多远(分钟)
NEG_WIN = WAIT * 4 + 10         # 写未来那一次等"断[B] 不该命中"的窗口(秒)
WAIT_MINSTEP = 70               # 断[A] 等一个分钟步进汇合点的最长秒数(Run_TaskRate 约 1 次/分进一次)

# 断点。**行号都核过断点**(gdb info line/scope):
BP_A = ("prev", "Run_TaskRate", "Check_Switch", 1)   # Run_TaskRate 出口 g_ZoneSwNo = Check_Switch(0) @TaskRate.c:82
BP_B = ("prev", "Check_Switch", "Save_FrezData", 1)   # Check_Switch 切换体 Save_FrezData(TAB_Switch[id].idFrez, &swTime[0]) @TaskRate.c:323
# ⚠ 规格 F 列原写 TaskFreeze.c:1244 读 idFrez —— 那一断点**没用**在这里: 它是 Save_FrezData 体内,
#   而 :323 的实参表达式 `TAB_Switch[id].idFrez` 就是同一个值的来源, 在同一栈帧里现读现取即可
#   ⇒ 少占一个 Cortex-M0 硬件断点槽(共 4 个), 也避开"断[C] 命中时无人等在等、被 ensure_running
#     当作陈旧 *stopped 丢掉"的竞态(breakpoint.py:860 `self._pending = None`)。已在 J 列如实披露。
# ⚠ 规格 F 列原写的 :307 经核对断点**是假通过**(gdb 解析到 Check_Switch+6 = 函数首条可执行语句, 那时
#   swTime 还没被 :311 Read_ParaData 填过 → 读到栈垃圾), 与 4-6 的 :625/:842/:119 同一类。已后移到 :323。
# 各断点要读的量(纯数据; 地址由 .out/画像解析, 脚本不摸符号表)。
# **逐元素读 swTime, 不整条读** —— 它是 INT8U[5](实为 char), gdb 对 char 数组按字符串打印
# (`"0\016\n\t\032"`), 又难解又不稳; 逐元素读的是标量, 打印必为整数。这一条 + 下一条口径
# 的完整依据见 CB 里 `_ID_SW_FREZ` 上方那段注释(gdb 打印三坑的亲历记录)。
VARS_A = ("backup",)
# 断[A] 只判"命中"、值为辅(证 ID_TaskRate 汇合点真被消息调到, 与 4-6 断[A] 同义)。挑 `backup`
# 是因为它在 :82 的活跃区间内(0x21e68-0x21f24); ⚠ **`msg` 不能读** —— 核对断点实测它在 :82(0x21ea0)
# 已出活跃区间(只在 0x21e6e-0x21e72), 读回来是栈垃圾, 与 4-6 断[A] 的 `normal` 同类的"假通过"。
VARS_B = ("id", "swTime[0]", "swTime[1]", "swTime[2]", "swTime[3]", "swTime[4]",
          "TAB_Switch[id].idFrez")
# ⚠ **对拍用二进制, 不用线上 BCD**: 固件内部 swTime 是二进制(写路 DLT645App.c:1664 `Is_nBCD`+
#   `nBCD_nHEX` 转过, 读路 :5365/:5382 `nHEX_nBCD` 转回), 所以期望值是 `CB.swset_tuple(时刻)`
#   (分,时,日,月,年) 的**二进制**形态 —— 拿线上 BCD 字节去比会恒 FAIL。
AT_B = "断[B] TaskRate.c:323"   # 断[B] 在记录与打印里的名字

# ---- 中止那一趟的交代: 六条都要有, 与正常路径**同名同号同观测** ----
# 少记一条、或把某条记到别的观测名下, 这一趟的分母就跟着变样(CB.unproven_records 的 ⚠)。
ENTRIES_ALL = (
    ("②a", "到点自动切套(固件消费掉设定 ⇒ g_*SwNo 转第1套)", judge.SERIAL),
    ("②b", "切后的当前套内容 == 原备用套内容(整表逐字节对拍)", judge.SERIAL),
    ("③", "时区/时段各出一帧对应的切换冻结", judge.DEBUG),
    ("④", "切后费率归属按新套(slot 支认领; zone 支无此条)", judge.SERIAL),
    ("⑤a", "设定被清 = 不重复切", judge.SERIAL),
    ("⑤b", "未到点不误切", judge.SERIAL),
)
FALSIFY_ALL = {
    "②a": "到点却没切 ⇒ g_*SwNo 仍是 0xAA(待切)",
    "②b": "切套体没把备用套覆盖进当前套(或覆盖错表/漏拷子表) ⇒ 两份整表逐字节不同",
    "③": "切套体没落冻结 ⇒ 序号不变(或该 kind 那条路径根本没通)",
    "④": "切套后费率仍按旧套算/没重算 ⇒ 读到的是旧套费率而非预置后的新套费率",
    "⑤a": "固件不消费设定(图省事的实现只读不清) ⇒ 设定仍 == 所写值, 此后每个分钟步进都会再切",
    "⑤b": "未到点也执行切换体 ⇒ 会多落一条切换冻结, 序号推进",
}


def _sw_no(ser, kind, tag):
    """AA80 直读 g_ZoneSwNo / g_SlotSwNo → `(原始字节|None, 说明)`。

    读是脚本的事, 解码是库的事(`CB.swset_sw_no_decode`: 0x55=第1套 / 0xAA=第2套, 别的值不猜)。
    """
    var = "g_ZoneSwNo" if kind == "zone" else "g_SlotSwNo"
    return cmd_bank.swset_sw_no_decode(kind, watch.watch_vars(ser, [var], tag=tag, wait=WAIT).get(var))


def _one_kind(ctx, kind, bp_b):
    """一段 = 3-2 一个 kind 的全部条目, 按**写表之前 / 写表那一刻 / 写表之后**排:

      ①基线 → ⓪预置备用套 → ②写未来(对照: 断[B] 应**不**命中) → ③写过去(当场切: 断[B] 应命中)
      → ②b 整表对拍 / ④ 费率归属 → 收拾(备用套写回原值 + 再切一次把当前套也切回来)。

    ⚠ 断[B] 递的是**已有 bpno**(不是 `(文件,行号)` 元组): 本段要用它三次(对照一次、触发一次、
      收拾一次)。传元组的话每一次都自己挂/自己撤, 第二次就是一个新槽 —— 而 Cortex-M0 只有 4 个;
      传 bpno 则撤不撤由 `drop=` 定, 这里三次都 `drop=False`, 撤在**两支都跑完之后**(见 `part_switch`)。
    """
    ser, J = ctx.ser, ctx.J
    name = cmd_bank.swset_name(kind)
    wb = bp_b is not None
    print("\n===== 3-2 %s切换: 对照半支 + 触发半支 =====" % name)
    if not wb:
        # **降级要喊出来**(不许静默): 没有会话时白盒判据一律不做, 而不是做了判 FAIL。
        print("   %s 本次无调试会话 → 断点观测(白盒)整体**未做**; 本次范围 = 仅黑盒"
              "(判过的只是对外行为, 内部指令路径未取证)"
              % ("-- 用户指定只做黑盒" if ctx.waived else "!!"))

    # 表钟基准: 固件用的是 pGet_Time(EM_Min)(管理芯跟随计量芯主钟), 读管理芯 RTC 足够(±分 余量足够大)。
    # 读一次不成再补一次 —— 切套刚落了 Save_FrezData、表在写外部 FLASH 时, 紧跟着的一两帧可能整帧无应答,
    # 单次失败就中止会把整种观测误判成"没做成", 而链路其实只是忙。
    clock = (cmd_bank.read_clock(ser, chip="管理芯", quiet=True)
             or cmd_bank.read_clock(ser, chip="管理芯", wait=4.0, quiet=True))
    if not clock:
        # 记 `ok=None`(没做成)而不是 False: **没跑成不是"固件不对"**, 于是它只让这一项变未定论。
        why = "%s段 表钟读不出, 无法定位过去/未来时刻 —— 中止(黑盒观测未跑完, 白盒观测未开始)" % name
        J.extend(cmd_bank.unproven_records(ENTRIES_ALL, why, FALSIFY_ALL))
        raise trial.Stop("%s ⇒ 未定论(退 2), 不是失败" % why)
    now = datetime.datetime.strptime(clock[:19], "%Y-%m-%d %H:%M:%S")
    t_future, t_past = now + datetime.timedelta(minutes=LEAD_MIN), now - datetime.timedelta(minutes=2)
    print("   表钟=%s ⇒ 未来设定=%s / 过去设定=%s" % (now, t_future, t_past))

    # ---- ① 基线 ----
    pre_set = cmd_bank.read_zone_slot_switch(ser, kind, wait=WAIT)
    pre_no, pre_no_s = _sw_no(ser, kind, "读 g_*SwNo 基线")
    pre_fr = cmd_bank.read_switch_frez(ser, kind, pos=1, wait=WAIT)
    print("   基线: 设定=%s | %s | 冻结序号=%s" % (pre_set, pre_no_s, pre_fr.get("seq")))
    # ↑ 这是**前置**(不认领任何条目): 判据条目里没有一条叫"能读回基线"。它红了会让整项「失败」,
    #   这是有意的 —— 基线都读不出时, 后面的"没变"全是没有对照的假通过。
    J.add("基线读取(设定/g_*SwNo/切换冻结三条都取到)",
          pre_set is not None and pre_no is not None, "缺一条则后面的对照无意义")

    # ---- ⓪ 前置预置: 把**备用套**写成与当前套**不同**的内容 —— 判据②b/④ 的**前提** ----
    # 没有这一步, 两套内容同形(TaskRate.c:125-126 编译期同表), 切与不切数值上一样, 那两条**分辨不出**。
    # ⚠ 种不进去时下面**不产出** ②b/④ 的记录(⇒ Judge 记"未证" → 未定论), **绝不写"通过"**;
    #   也绝不把"我没做这一步"说成"台面证不了"。
    snap_cur0 = cmd_bank.read_tab_whole(ser, kind, "cur", wait=WAIT)
    snap_bak0 = cmd_bank.read_tab_whole(ser, kind, "bak", wait=WAIT)
    slot_which = 1
    if kind == "slot":
        got = cmd_bank.which_slot_at(cmd_bank.zone_tab_whole_parse(snap_cur0), now.month, now.day)
        if got:
            slot_which = got
    plant_ok, plant_why, plant_orig = cmd_bank.plant_bak_tabs(ser, kind, which=slot_which, wait=WAIT)
    print("   前置预置(备用套写成与当前套不同): %s" % plant_why)
    if not plant_ok:
        print("   !! 前置预置没成 ⇒ 判据②b/④ 本次**不产出记录**(记未证, 不是 FAIL 也不是通过)")

    # ---- ② 对照半支: 写未来 → 应"待切但不切" ----
    # 未来时刻**不** Post_Message(MSG_MinStep)(固件 TaskRate.c:429-431 只在"设定≤现在"时发),
    # 故这一次本就不该命中断[B] —— `GD.expect_no_hit` 正是为这种**否定期望**立的原语:
    #   对 `wait_hit` 来说"没命中"是没做成, 对它来说"没命中"才是要证的那件事。
    # `vars=VARS_B`: 这一次期望不命中, 但万一命中了(固件真误切), 得能立刻看出停在哪儿、读到什么。
    if wb:
        r_f = breakpoint.expect_no_hit(ctx.g, bp_b, NEG_WIN,
                               label="写未来: 断[B] 未命中 ⇒ 切换体那条语句没被执行到",
                               trigger=cmd_bank.set_zone_slot_switch,
                               trigger_args=(ser, kind, t_future, WAIT), vars=VARS_B, join=WAIT,
                               crit="⑤b", falsify="固件在未到点时也执行切换体 ⇒ 会停在 %s" % AT_B)
    else:
        # 没会话: `expect_no_hit` 照样把触发帧发出去(降级只降白盒, 不降黑盒), 只是不产出记录。
        cmd_bank.set_zone_slot_switch(ser, kind, t_future, wait=WAIT)
        r_f = None
    f_set = cmd_bank.read_zone_slot_switch(ser, kind, wait=WAIT)
    f_no, f_no_s = _sw_no(ser, kind, "读 g_*SwNo 写未来后")
    f_fr = cmd_bank.read_switch_frez(ser, kind, pos=1, wait=WAIT)
    # 这三条都认领 ⑤b"未到点不误切"。**前两条是后一条的前提**: 设定没写进待切态的话, "冻结没新增"
    # 是句空话(没触发的动作不会造成状态, 证不了任何事) —— 让它们同挂 ⑤b, 一条红了整条就记未满足。
    # ⚠ 它们同时是上面 `expect_no_hit` 那条的**门槛**: 触发帧没落地时, "没命中"不是固件的功劳。
    J.add("对照 写未来: 设定读回==所写(非空)",
          f_set is not None and f_set != (0, 0, 0, 0, 0) and f_set == cmd_bank.swset_tuple(t_future),
          "读到 %s, 期望 %s" % (f_set, cmd_bank.swset_tuple(t_future)),
          crit="⑤b", falsify="写参量帧没落库/被拒 ⇒ 读回 != 所写(此时『未误切』无意义, 不构成证据)")
    J.add("对照 写未来: g_*SwNo==0xAA(第2套=待切)", f_no == 0xAA, f_no_s,
          crit="⑤b", falsify="固件没把设定置成待切态 ⇒ 读到的不是 0xAA")
    J.add("对照 写未来: 切换冻结未新增(未到点不误切)",
          f_fr.get("seq") is None or f_fr.get("seq") == pre_fr.get("seq"),
          "冻结序号 %s → %s" % (pre_fr.get("seq"), f_fr.get("seq")),
          crit="⑤b", falsify="未到点也执行切换体 ⇒ 会多落一条切换冻结, 序号推进")
    if r_f is not None:
        # 白盒对照半支: 串口只能证"冻结没新增"; 断点观测证的是**切换体那条语句根本没被执行到**。
        J.extend([r_f])

    # ---- ③ 触发半支: 写过去 → 当场切 ----
    # 过去时刻会 Post_Message(MSG_MinStep)(:429-431) → 任务循环下一轮走 Check_Switch → 到点 → 命中断[B]。
    # 切前快照 = 判据②b 的对拍基准(`TaskRate.c:326-353` 切套体 = 备用套 → 覆盖当前套)。
    snap_bak_before = cmd_bank.read_tab_whole(ser, kind, "bak", wait=WAIT) if plant_ok else b""
    r_p = breakpoint.fire_hit(ctx.g, bp_b, cmd_bank.set_zone_slot_switch, ser, kind, t_past,
                      label="断[B] 命中 ⇒ 到点判定成立 + 切换体真被执行",
                      timeout=40.0, vars=VARS_B, wait=WAIT, crit="③",
                      falsify="到点判定不成立 / 切换体那条语句没被执行 ⇒ 不会停在 %s" % AT_B)
    time.sleep(WAIT)                     # 等分节拍消息被任务循环消费(Post_Message 是异步的)
    p_set = cmd_bank.read_zone_slot_switch(ser, kind, wait=WAIT)
    p_no, p_no_s = _sw_no(ser, kind, "读 g_*SwNo 写过去后")
    p_fr = cmd_bank.read_switch_frez(ser, kind, pos=1, wait=WAIT)
    # 这两条同挂 ②a: 设定被固件**消费掉**(清零)且状态翻到第 1 套 —— 同一个读数说明两件事, 所以
    # 认领两条(**元组**, 别拆成两条记录 —— 那会让人以为取过两次证):
    #   ②a"到点自动切套" —— 固件把设定**消费**掉了;
    #   ⑤a"设定被清=不重复切" —— 清掉之后每个分钟步进再走 Check_Switch 也没东西可切了(这就是
    #      "不重复切"的机制; 直接证"再走一次不落第二条"还要再等一个分钟步进, 本台没做那一步)。
    J.add("触发 写过去: 设定被固件清零(全0)",
          p_set is not None and p_set == (0, 0, 0, 0, 0), "读到 %s" % (p_set,),
          crit=("②a", "⑤a"),
          falsify="固件不消费设定(图省事的实现只读不清) ⇒ 设定仍==所写值, 此后每个分钟步进都会再切")
    J.add("触发 写过去: g_*SwNo==0x55(第1套=已切)", p_no == 0x55, p_no_s,
          crit="②a", falsify="到点却没切 ⇒ 仍是 0xAA(待切)")
    # 这一条 + 下面四条白盒同认领 ③"时区/时段各出一帧对应的切换冻结"(逐 kind 各跑一次 ⇒ 两支都要有成)。
    J.add("触发 写过去: 切换冻结新增(切套体真跑了)",
          p_fr.get("seq") is not None and p_fr.get("seq") != pre_fr.get("seq"),
          "冻结序号 %s → %s" % (pre_fr.get("seq"), p_fr.get("seq")),
          crit="③", falsify="切套体没落冻结 ⇒ 序号不变(或该 kind 那条路径根本没通)")
    if wb:
        # 白盒触发半支: 这一次才把"切换体真被执行"从**推断**变成**观察**。
        # ③ 由 `fire_hit` 那一条自己认领(它只答"停到没停到"); 下面三条是**同一个读数**说明的另外三件事,
        # 按同一份 `vars` 各补一条 —— 四者是一次取证的四面, 不是四次。
        J.extend([r_p])
        wv = (r_p or {}).get("vars") or {}
        print("      断[B] 记录: ok=%s | %s" % ((r_p or {}).get("ok"), (r_p or {}).get("detail")))
        print("      停时读到: %s" % wv)
        # ⚠ 判"这一次停到没有"只能看 `ok`(True=命中): `fire_hit` 的记录里**没有** `hit` 键 ——
        #   `record()` 早把它拿去定 `ok` 并折进 `detail` 了(5-3 判据② 因此吃过一次假 FAIL)。
        #   没命中 ⇒ 下面三条不产出(与库里的 `if hit_p is not None:` 同一条界), 不拿空值顶数。
        if (r_p or {}).get("ok") is True:
            want_id = 0 if kind == "zone" else 1     # TAB_Switch[] 的下标: 0=时区支 / 1=时段支
            J.add("断[B] id == %d(%s支, 证停在的是这一支)" % (want_id, name),
                  cmd_bank.gdb_ints(wv.get("id")) == [want_id], "id=%s" % wv.get("id"),
                  crit="③", obs=judge.DEBUG,
                  falsify="停在了另一支 ⇒ id != %d(时区/时段两条路径串了)" % want_id)
            # swTime = 固件 :311 从参数区读回来的那份(分 时 日 月 年)。**与线上发出的同一份时刻** ——
            # 只是编码不同: 线上是 BCD, 固件内部是二进制(写路上 :1664 `nBCD_nHEX` 转过)。
            # 它相等 = "表到点判定用的就是我们写的那个时刻", 这是串口读回证不到的
            # (读回只证"参数存进去了", 证不了"判定用的是它")。
            want_t = list(cmd_bank.swset_tuple(t_past))
            got_t = [(cmd_bank.gdb_ints(wv.get("swTime[%d]" % i)) or [None])[0] for i in range(5)]
            J.add("断[B] swTime == 所写设定(表判定用的就是它)", got_t == want_t,
                  "读到 %s(二进制 分时日月年), 期望 %s" % (got_t, want_t),
                  crit="③", obs=judge.DEBUG,
                  falsify="表判定用的不是所写设定(读到旧值/没被 :311 刷进 swTime) ⇒ swTime != 期望")
            ename = "ID_%sSwFrez" % ("Zone" if kind == "zone" else "Slot")
            ekind = cmd_bank.swset_frez_id(kind)
            got_en = cmd_bank.gdb_sym(wv.get("TAB_Switch[id].idFrez"))
            J.add("断[B] TAB_Switch[id].idFrez == %s(%d)" % (ename, ekind),
                  got_en in (ename, str(ekind)),
                  "读到 %s —— 它就是 %s 要落进 Save_FrezData 的冻结类型号"
                  % (wv.get("TAB_Switch[id].idFrez"), AT_B),
                  crit="③", obs=judge.DEBUG,
                  falsify="要落的冻结类型不是切换冻结 ⇒ idFrez 是别的枚举(如瞬时/周期冻结)")

    # ---- ②b 判据②b「切后当前套内容 == 原备用套内容」—— **整表逐字节对拍**(比只比单项强得多) ----
    # 前置预置已把备用套写成与当前套不同 ⇒ 这份对拍**有分辨力**(同内容的固件下必然"碰巧相等",
    # 但那种固件下前置预置就读不到差异值, 报告里 plant 那行会先红)。
    if plant_ok:
        snap_cur_after = cmd_bank.read_tab_whole(ser, kind, "cur", wait=WAIT)
        J.add("切后当前套整表 == 切前备用套整表(逐字节)",
              snap_cur_after != b"" and snap_cur_after == snap_bak_before,
              "切前备用套 %dB → 切后当前套 %dB, %s; (切前当前套 %dB, 与之%s)"
              % (len(snap_bak_before), len(snap_cur_after),
                 "逐字节一致" if snap_cur_after == snap_bak_before else "不一致",
                 len(snap_cur0), "相同" if snap_cur0 == snap_bak_before else "不同=预置生效"),
              crit="②b",
              falsify="切套体没把备用套覆盖进当前套(或覆盖错表/漏拷子表) ⇒ 两份内容不同")
        # 参考(不计入条目, crit=None): "有分辨力"的前提 —— 若它不成, 上面那条就是碰巧相等的假通过。
        J.add("参考: 前置预置确实让切前备用套 != 切前当前套(上面那条才有分辨力)",
              snap_bak_before != snap_cur0, "切前 备用套%dB vs 当前套%dB, %s"
              % (len(snap_bak_before), len(snap_cur0),
                 "不同" if snap_bak_before != snap_cur0 else "**相同** ⇒ ②b 本次无分辨力"))
    else:
        print("   !! 前置预置没成 ⇒ 判据②b 本次不产出记录(记未证)")

    # ---- ④ 判据④「切后费率归属按新套」—— 串口观测可直接观察: 同一分节拍内 `Calculate_RateNo()`
    #   (TaskRate.c:85-94) 会用**换过之后**的当前套时段表重算 g_RateNo ⇒ 写进去的费率号应当出现。
    #   预置是"逐段费率 +1"(不是只改一段) ⇒ 当刻落在哪一段都得到与旧值不同的费率, 不会因跨段而假通过。
    #   读由本脚本发(裸 `watch.watch_vars`), 库只解码 —— 与上面 断[A] 那两行同一个形状。
    rate = cmd_bank.rate_no_decode(
        watch.watch_vars(ser, ["g_RateNo"], tag="读当前费率号", wait=WAIT).get("g_RateNo"))
    if plant_ok and kind == "slot":
        clock2 = cmd_bank.read_clock(ser, chip="管理芯", quiet=True) or clock
        hh, mm = int(clock2[11:13]), int(clock2[14:16])
        want = cmd_bank.rate_of([(h, mi, r % cmd_bank.RATE_NUM + 1) for h, mi, r in (plant_orig or [])], (hh, mm))
        old = cmd_bank.rate_of(plant_orig or [], (hh, mm))
        J.add("切后 g_RateNo == 新套时段表在当刻的费率(预置值)",
              rate is not None and want is not None and rate == want,
              "表钟 %s:%s ⇒ 读回费率=%s, 新套应为 %s(旧套该时刻是 %s, 两者不同 ⇒ 本条有分辨力)"
              % (clock2[11:13], clock2[14:16], rate, want, old),
              crit="④",
              falsify="切套后费率仍按旧套算/没重算 ⇒ 读到的是旧套费率 %s 而非 %s" % (old, want))
    else:
        J.add("费率号读得出且合法(参考证据, 不计入 ④)", rate is not None, "费率号=%s" % rate)

    # ---- 收拾: 备用套写回原值, 并**再切一次**让当前套也回到原值(当前套写被固件拒, 只能靠切) ----
    # ⚠ 收拾失败会把表留在"当前套 = 预置值"——**这是改动了表态**, 必须喊出来。故这里**重试**并复核到底。
    if plant_ok:
        rok, rwhy = cmd_bank.restore_bak_tabs(ser, kind, plant_orig, which=slot_which, wait=WAIT)
        print("   恢复备用套原值: %s" % rwhy)
        if rok:
            sw_ok = False
            for attempt in (1, 2, 3):
                # 再切: 当前套 ← 已复原的备用套。**必须也走 `GD.trigger`** —— 收拾这一次会第二次命中
                # 断[B](切换体 :323), 直呼的话没人在等这个停点 ⇒ 核心停住无人放行 ⇒ 此后每一帧整帧无应答
                # (表象: "写无 0x94 应答" + 表钟读不出)。走 trigger 就把停点接住。
                # 不用 `_drop` —— 断[B] 后一个 kind 的 ③ 还要用。
                _rc = breakpoint.trigger(ctx.g, bp_b, cmd_bank.set_zone_slot_switch, ser, kind, t_past,
                                 wait=4.0, timeout=40.0, _vars=VARS_B)
                sw_msg = ("触发动作报错: %s" % _rc["error"] if _rc.get("error") is not None
                          else ("断[B] 命中(第2次切也真走了切换体)" if _rc.get("hit") is not None
                                else "断[B] 未命中"))
                time.sleep(WAIT + 1)
                cur_back = cmd_bank.read_tab_whole(ser, kind, "cur", wait=4.0)
                # 切套体 = 备用套覆盖当前套 ⇒ 复原后当前套应 == **原备用套**; 表两套原值本就相同 ⇒ 也是原当前套
                sw_ok = cur_back != b"" and cur_back == snap_bak0
                if sw_ok:
                    print("   收拾复核: 当前套整表 == 原值  PASS；本表两套原值%s"
                          % ("本就相同" if snap_cur0 == snap_bak0 else "**本就不同**: 已复原到原备用套"))
                    break
                print("   !! 收拾第%d次未复原(%s; 当前套整表%s原值) —— 重试"
                      % (attempt, sw_msg, "==" if cur_back == snap_bak0 else "!="))
            if not sw_ok:
                print("   !!⚠ 收拾三次都没把当前套切回原值 —— **表被留在预置后的状态**;"
                      " 请再发一次「645 0x14 写切换设定为过去时刻」或跑 scripts/_restore_all.py")

def part_switch(ctx):
    """断[A] 旁证 + ②③④⑤a⑤b(zone/slot 两支) —— 一轮取全部条目。

    ⚠ 观测一律 attach, **禁用 launch**(launch 会复位表, RAM 态清零); 见 CLAUDE.md 调试链纪律 1。
    """
    cmd_bank.enter_factory(ctx.ser)                     # 0x14 受控写前提(写切换设定走的就是 0x14)

    # ---- 开后调试会话(断点观测)。台面没接 J-Link → None, 后面自动降级(并大声说明) ----
    ctx.session()
    if ctx.g is not None:
        # 整片 .out 解一次(停核遍历, 内部喂狗, 收尾放行) —— 之后一律查这张图
        gdbinit.build(ctx.g)
    # `ctx.bp()`: 没会话 → None。⚠ **原先这里还有一句"调试会话在、但断点没下上 → 断点观测同样不做"
    # 的打印 —— 它是死码**: `Session.break_at` 下不上就**抛 `GdbError`**, 从不返回 None。
    bp_a = ctx.bp(BP_A)        # 断点 → TaskRate.c:82
    bp_b = ctx.bp(BP_B)        # 断点 → TaskRate.c:323
    if bp_a is not None or bp_b is not None:
        # ⚠ 两个都挂好才放行 —— 挂第二个断点那一步会把核停住; 不放行的话后面每一帧 645 都没有应答,
        #   现象与"串口坏了"一模一样(见 4-4 的同名一行)。
        ctx.g.ensure_running()

    # ---- 断点观测 ①: 证切换判定汇合点真的被分钟步进消息调到(断[A]) ----
    # 断[A] 是"自然到达"的断点(每分一次), 没有可发的触发帧 → `GD.wait_hit` = `GD.fire_hit` 的对偶:
    # 一个等、一个触发, 各自把那一次包成证据记录。一行一次, 包装/收尾(撤断点)全在库里。
    # `crit=None`: 它是**旁证**(路径通不通), 不认领任何预设条目 —— 所以它红也不会把某条判据记成失败,
    # 但会进记录、进日志, 人要能看见。
    if bp_a is not None:
        ctx.take([breakpoint.wait_hit(ctx.g, bp_a, WAIT_MINSTEP, VARS_A,
                              "断[A] 汇合点 Run_TaskRate:82",
                              falsify="分钟步进消息没投到该任务 ⇒ 不会停在 :82")])

    # ---- 串口观测 + 断点观测 ②③④⑤: 写未来(应不命中) / 写过去(应当场切并命中断[B]) ----
    for kind in KINDS:
        _one_kind(ctx, kind, bp_b)

    # 断[B] 是本段自己挂的, 它**跨两个 kind**(三次触发: 对照一次、触发一次、收拾一次)才用完 ——
    # 所以撤在**两支都跑完之后**, 不能放在 `_one_kind` 段末(那样时区表那一支用完就把它撤了,
    # 时段表那一支的 ③ 白盒取证必然落空: 断点不在槽里 ⇒ `fire_hit` 记 ok=None)。
    # 不撤也不行: 它插在 Check_Switch 切换体上, 下一次切换会把核撂停而没人在等(`Session._disarm` 的 ⚠)。
    if bp_b is not None:
        ctx.g._disarm(bp_b)


def _banner():
    return "== 3-2 两套费率时段切换 | 工程=%s 表号=%s ==" % (CURRENT.PROJECT, CURRENT.TABLE_ADDR.hex().upper())


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "3-2 两套费率时段切换(zone/slot)", cmd_bank.zone_slot_switch_criteria,
        name="3_2_zone_slot_switch", parts=[("3-2 切换段", part_switch)],
        gdb=breakpoint, banner=_banner(),
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV)))
