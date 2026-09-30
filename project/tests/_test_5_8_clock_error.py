# -*- coding: utf-8 -*-
"""在证什么: 表钟被造出倒退 ⇒ 固件判『时钟故障』并在事件 0x2E 新增一条(判据①);
  645 广播校时成功后固件把最近一条故障的结束时间补上(判据②); 时标取自故障发生的那一刻(判据③);
  连造 11 回该口条数封顶在容量 10 上、最早那条被顶掉(判据⑦)。
会向表写什么: 645 广播校时(表钟 +120s, 当天只能成功一次)、645 写『日期及星期及时间』与写
  『日期及星期』(各把表钟设成本次读回的时刻)、注入改 g_MeterTime[5](年字节 −1)
  —— 固件按分支校正, 表钟可能被拨到冻结/掉电时刻; 收尾靠 scripts/_restore_all.py 拨回。
跑法: `python project/tests/_test_5_8_clock_error.py`(真串口 + 真探针; 没探针时判据① 那一半没得看)。
结论怎么读: 账本末行「判据: 满足 n/N」, N=4; 退 0 = 通过 / 1 = 失败 / 2 = 未定论。
"""
from common import judge            # 观测种类常量(断点那一半的证据标 DEBUG)
from common import trial           # 运行外壳: argv/日志/账本/串口/会话/收尾三连/退出码
from project import CURRENT          # 本工程画像: 表号/.out 路径/喂狗点(库不认表, 只有脚本认)
from meterlib import cmd_bank       # 帧目录 + 语义动词积木箱(每步自带打印/判 PASS·FAIL)
from swdbg import breakpoint            # 断点级白盒(停核 + 读函数内局部量); 没接 J-Link 会自动降级
from swdbg import gdbinit            # 整片 .out 地图: 开会话后解一次, 之后断点/注入点只查它

# ---- 子项参数(纯数据) ----
DELTA = cmd_bank.CE_CALI_DELTA                  # 广播校时的目标偏差 = +120s(须落在 [60,300] 窗口内)
WAIT = 3.0                # 普通串口往返
RTC_WAIT = 15.0           # 第 1 步三条判定: 每秒走一遍(Run_TaskTime :275 的 MSG_SecStep), 15s 够
BRANCH_WAIT = 15.0        # 校正支: 与判定同一拍内就走到, 15s 够
HIT_WAIT = 20.0           # 注入之后停在落库那一句(下一拍)
CB_WAIT = 20.0            # 两条 645 写帧的清除断点
ROUND_WAIT = 20.0         # 第 6 步每一回的注入
BURST_WAIT = 3.0          # 第 6 步每一回的闭合帧

# ---- 第 3 / 6 步记下的记录口几何(解释停点上读到的那五段与第 6 步的封顶) ----
# 布局出处 TaskRecord.c:1540-1548(发生支)。记录体总长 LEN_TimeError。
REC_CAP = cmd_bank.CE_REC_CAP   # 时钟故障口记录容量(条) —— 画像单一事实源(RecdData.h:170)
REC_HEAD = (0, 6)         # 发生时刻 6 字节(:1540 Get_MeterTime(&buff[0]))
REC_END = (6, 12)         # 结束时刻 6 字节(:1544 发生支刚清零 / :1563 结束支才填)
REC_ENE_A = (12, 22)      # 发生时刻那一侧的正反向有功总电能(:1545 正 &buff[12] / :1546 反 &buff[17])
REC_ENE_B = (22, 32)      # 结束时刻那一侧(:1547 发生支先清零 / :1569 &buff[22] / :1570 &buff[27])
REC_SRC = 32              # 发生源那一个字节(:1548 buff[32] = sour)
BURST = REC_CAP + 1       # 第 6 步: 容量 10 条 + 1

# ---- 第 5 步另外两条清除通道的帧(数据域要写**当前表钟**, 时间一变字节就变 ⇒ 静态登记不了) ----
# 数据域(解码后) = DI(4) + 密码(4) + 操作者代码(4) + 数据(N);  LEN = 12 + N(DLT645App.c:1557)。
# 密码首字节 0x02 = 厂内控制(:1538 的那一判), 其后的字节固件不看。
WRITE_DI_DT = bytes([0x0C, 0x01, 0x00, 0x04])   # 日期及星期及时间, DI 号 0400010C, 数据 7B
WRITE_DI_WK = bytes([0x01, 0x01, 0x00, 0x04])   # 日期及星期,     DI 号 04000101, 数据 4B
WRITE_HEAD = bytes([0x02, 0x00, 0x00, 0x00]) + bytes(4)   # 密码 + 操作者代码
WRITE_CMD = 0x14          # 645 写命令字; 应答命令字 = 0x94

# ---- 断点(纯数据) ----
# `BP_<X>` ↔ `VARS_<X>` 是**承重的**(scripts/_check_anchors.py 靠它配对, 拿去对源码核
# "要读的变量在断点那一行赋过值没有"): 名字写歪 = 这种观测静默地没人核过; `VARS_<X>` 必须是**字面量元组**。
BP_INJ = ("call", "Run_TaskTime", "Fetch_CRC", 1)   # 注入停靠点 @TaskTime.c:275 <Run_TaskTime+54>
VARS_INJ = ("g_MeterTime", "g_MeterTime_Backup")     # 注入**前**的两个入参(对照用)
# 落点是**发生支的写库那一句**(`Write_RecdData(ID_TimeError, &buff[0], 0, LEN_TimeError, 0)` @TaskRecord.c:1549):
#   那是"判故障并落库"这一条路自己的一句, 走到这里 buff[0..5] 时刻、[6..11] 结束时刻、
#   [12..31] 两组电能、[32] 发生源都已经填过。
# ⚠ 不落在**结束支**那一句(:1571 `&buff[6]`, 三条清除通道走的就是它): 两条支各写各的,
#   落在发生支上, 停住就只可能是"判故障落库" —— 所以不必再读 `str` 去分辨这一次是哪一类调用。
BP_HIT = ("prev", "Recd_TimeError", "Write_RecdData", 1)
VARS_HIT = ("buff", "sour")               # buff = 那一条记录体; sour = 发生源(buff[32] 的来源)
BP_CLR = ("prev", "CMD_CaliTime", "Recd_TimeError", 1)   # 通道一: 645 广播校时的清除口 @DLT645App.c:734
# `setTime` = 解完 BCD 的 **HEX 目标时间** —— 见库内 ⚠: 不读 `pTime`(const 指针, 读出来只是个地址);
# `is698`/`mode` 是那一次调用的形态(广播 = FALSE / 0)。⚠ 必须是**字面量元组**。
VARS_CLR = ("setTime", "is698", "mode")

# ---- 第 1 步那三条判定: 各是 Check_And_Correct_RTC 里 `||` 链上的**一处调用点**(:1095-1097) ----
# 三条**每秒**走一遍(经 Run_TaskTime :275 的 MSG_SecStep 分支) —— 是每秒, 不是每分钟。
# 停点上能读的是**那一刻函数里的活值**: `Buff2` 是当前时间那个入参的地址、`PTime` 是备份那个;
#   函数刚进来那一句上, `g_MeterTime` 与 `g_MeterTime_Backup` 正分别是这两者所指的内容。
BP_RTC_BAD = ("prev", "Check_And_Correct_RTC", "Check_DateTime", 1)    # :1095 格式错乱
VARS_RTC_BAD = ("Buff2", "g_MeterTime")
BP_RTC_BACK = ("prev", "Check_And_Correct_RTC", "Comp_Data", 1)        # :1096 倒退
VARS_RTC_BACK = ("Buff2", "PTime", "g_MeterTime", "g_MeterTime_Backup")
BP_RTC_FAR = ("prev", "Check_And_Correct_RTC", "Diff_Days", 1)         # :1097 超前 1000 天
VARS_RTC_FAR = ("Buff2", "PTime", "g_MeterTime", "g_MeterTime_Backup")
# 校正支自己那一句(:1103 `Recd_TimeError(TRUE, 0)`)。它只在 :1095-1097 有一条成立时才走得到。
# ⚠ 停在这里时 `Buff2` 还是**被改过的时间**, 而 `PTime` 是好的备份 —— :1104 的
#   `Copy_Data(Buff2, PTime, 6)` 在 :1103 **之后** ⇒ 年字节差正好是 1(注入减掉的那个 1)。
BP_BRANCH = ("prev", "Check_And_Correct_RTC", "Recd_TimeError", 1)
VARS_BRANCH = ("Buff2", "PTime", "g_MeterTime", "g_MeterTime_Backup")

# ---- 第 5 步通道二 / 通道三: 两条 645 单播写的清除口(都在 CMD_WriteData 里) ----
BP_CLR_DT = ("prev", "CMD_WriteData", "Recd_TimeError", 1)   # 写『日期及星期及时间』@DLT645App.c:1592
VARS_CLR_DT = ("pFrame",)
BP_CLR_WK = ("prev", "CMD_WriteData", "Recd_TimeError", 2)   # 写『日期及星期』@DLT645App.c:1627
VARS_CLR_WK = ("pFrame",)

FALSIFY = {
    "①白盒": "固件在年字节倒退时不进判定(守卫被短路) ⇒ 走不到 Recd_TimeError 发生支的落库那一句 "
              "%s 不命中" % breakpoint.text(BP_HIT),
    "①黑盒": "固件判了故障却没写库, 或写库失败被吞 ⇒ 事件 0x2E 条数不增、最新一条还是原来那条",
    "②": "校时成功后不清故障(:734 的 Recd_TimeError(FALSE,0) 被删/被判定挡) ⇒ 结束时间保持空 "
         "且 :734 不命中",
    "②写日期及星期及时间": "这条 DI 不走 :1592 的 Recd_TimeError(FALSE,0) ⇒ 未闭合记录的结束时间保持空",
    "②写日期及星期": "这条 DI 不走 :1627 的 Recd_TimeError(FALSE,0) ⇒ 未闭合记录的结束时间保持空",
    "③发生": "时标取自**校正后**的时间而非故障发生的那一刻 ⇒ 记录的发生时间会等于校时时刻, "
              "而非注入时刻",
    "①③对照": "发生支落库那一刻的 buff[0..5] 不是故障发生的时刻 ⇒ 停点上读到的记录体与 698 "
               "读回那一条不是同一份",
}


def _ts_internal(b):
    """固件内部钟 6 字节(秒分时日月年, HEX 码) → 'YYYY-MM-DD HH:MM:SS'。

    读不到/不足 6 字节/字段越界 → `None`(不猜)。与 `_rec_ts` 是同一个编码器的正反两面。
    """
    if not b or len(b) < 6:
        return None
    s, mi, h, d, mo, y2 = b[:6]
    if not (1 <= mo <= 12 and 1 <= d <= 31 and h < 24 and mi < 60 and s < 60):
        return None
    return "%04d-%02d-%02d %02d:%02d:%02d" % (2000 + y2, mo, d, h, mi, s)


def _rec_ts(b6):
    """记录里那 6 字节时刻 → 钟串; 空时刻与无效时刻都是 `None`(固件按设计没写这一格)。"""
    if not b6 or len(b6) < 6:
        return None
    s, mi, h, d, mo, y2 = b6[:6]
    if not (y2 or mo or d or h or mi or s):
        return None
    if mo == 0xFF or d == 0xFF:
        return None
    return "%04d-%02d-%02d %02d:%02d:%02d" % (2000 + y2, mo, d, h, mi, s)


def _hex(b):
    """字节段 → 空格分开的十六进制; 空 → '(空)'。"""
    return " ".join("%02X" % x for x in b) if b else "(空)"


def _fields(buff):
    """停点上读到的记录体 → 五段; 取不到那五段 → `{}`(由调用方按三态判)。"""
    if not buff or len(buff) <= REC_SRC:
        return {}
    return {"发生时刻": bytes(buff[REC_HEAD[0]:REC_HEAD[1]]),
            "结束时刻": bytes(buff[REC_END[0]:REC_END[1]]),
            "发生侧电能": bytes(buff[REC_ENE_A[0]:REC_ENE_A[1]]),
            "结束侧电能": bytes(buff[REC_ENE_B[0]:REC_ENE_B[1]]),
            "发生源": buff[REC_SRC]}


def _tri(halves):
    """几半支合成一条的三态: 有 False ⇒ False; 否则有 None ⇒ None; 全 True ⇒ True。"""
    if any(x is False for x in halves):
        return False
    if any(x is None for x in halves):
        return None
    return True


def _row_txt(rec):
    """一条记录读数 → 一行文本(`None` 写成"读不出", 不写成 0)。"""
    r = rec or {}
    occ = (r.get("occur") or {})
    return "序号=%s 发生时刻=%s 结束时刻=%s" % (
        occ.get("seq") if occ.get("seq") is not None else "读不出",
        occ.get("ts") or "读不出",
        (r.get("end") or {}).get("ts") or "读不出")


def _stop_txt(r):
    """一次断点观测的记录 → 一行文本(没停到就写清是哪一半没做成)。"""
    if r is None:
        return "没有调试会话 ⇒ 这一半没做成"
    return "停点 ok=%s | %s" % (r.get("ok"), r.get("detail") or "")


def _vals(r):
    """一次断点观测的记录 → 停点上读到的量(字典)。"""
    return (r or {}).get("vars") or {}


def _body_dt(ct):
    """『日期及星期及时间』的数据域 7B = BCD[秒,分,时,**星期**,日,月,年]。

    :1580 `Copy_Data(&pFrame[DAT11], &pFrame[DAT12], 3)` 把 日月年 左移一格盖掉星期
    —— 所以真的写进去的 6 字节是 秒分时日月年, 星期只在第 4 位上过一手(固件不校验它的值)。
    """
    y2, mo, d, h, mi, s, wk = cmd_bank._tp(ct, with_week=True)
    return bytes([cmd_bank._bcd(s), cmd_bank._bcd(mi), cmd_bank._bcd(h), cmd_bank._bcd(wk),
                  cmd_bank._bcd(d), cmd_bank._bcd(mo), cmd_bank._bcd(y2)])


def _body_wk(ct):
    """『日期及星期』的数据域 4B = BCD[星期,日,月,年]。:1622 用当前表钟补时分秒 ⇒ 这一帧不改时刻。"""
    y2, mo, d, _h, _mi, _s, wk = cmd_bank._tp(ct, with_week=True)
    return bytes([cmd_bank._bcd(wk), cmd_bank._bcd(d), cmd_bank._bcd(mo), cmd_bank._bcd(y2)])


def _send_di(ser, di, body, what, wait=WAIT):
    """发一帧自组的 645 写参量帧 → `(应答命令字, 应答数据域)`; 没收到应答 = `(None, b"")`。"""
    frame = cmd_bank.frame_645(WRITE_CMD, di + WRITE_HEAD + body, addr=cmd_bank.TABLE_ADDR)
    rx = cmd_bank.send_frame(ser, frame, wait=wait, tag="645.write", peer="管理芯", what=what)
    cmd, seg, _note = cmd_bank.decode_645_reply(rx)
    return cmd, bytes(seg or b"")


def part_clock(ctx):
    """一段 = 5-8 的六步。

    ⚠ **顺序是承重的**: 先跑第 5 步里那条广播校时(判据②), 再注入(第 1、2 步)。固件自己的判定
      (`TaskRecord.c:1512-1526`)在"已有未闭合记录"时让发生支的 `Recd_TimeError` 提前返回 ⇒
      不先把那条尾巴补上, 第 3 步那个写库点根本不会命中(那是判定挡着, **不是固件不判故障**)。
      同理: 阶段五、阶段六两条清除通道, 都要**先有一条未闭合记录**才走得到它们各自的清除口。
    ⚠ 观测一律 attach(**禁用 launch**: launch 会复位表、RAM 态清零); 见 CLAUDE.md 调试链纪律 1。
    """
    ser = ctx.ser

    # ---- 阶段一(基线): 进厂内 → 开会话 → 抄表钟/最近一条时钟故障/条数 ----
    # 进厂内: 记录读回受安全判定管(5-3 实踩: 厂外读记录会被打回); 两条 645 写帧也要求厂内态(:1538)。
    cmd_bank.enter_factory(ser)
    t0 = cmd_bank.read_clock(ser, quiet=True)
    ctx.session()
    if ctx.g is not None:
        # 整片 .out 解一次(停核遍历, 内部喂狗, 收尾放行) —— 之后一律查这张图
        gdbinit.build(ctx.g)
    g = ctx.g
    have_wb = g is not None
    if not have_wb:
        print("   -- 本次没有调试会话 ⇒ 白盒那一半整体没做成; 注入没有黑盒替身, 所以判据① 的两半"
              "都取不到, 判据② 只剩通道一那条黑盒读数")
    pre = cmd_bank.read_clock_error_event(ser)          # 本次是一条**未闭合**的
    cnt0 = cmd_bank.event_area_count(ser, cmd_bank.CE_EVENT)
    print("   基线: 表钟=%s | 时钟故障口 条数=%s | 最新一条 %s"
          % (t0 or "读不出", "读不出" if cnt0 is None else cnt0, _row_txt(pre)))

    # ---- 阶段二(第 1 步): Check_And_Correct_RTC 里那三条判定各停一次 ----
    # 三条各是一个**调用点**, 靠 `prev` 定死; 它们每秒走一遍, 所以"等一次自然命中"就够,
    #   没有可发的触发帧。停点时读的是那一刻的活值 —— 常态下 Buff2 与备份逐字节相同。
    _rtc = []
    for _bp, _v, _what in ((BP_RTC_BAD, VARS_RTC_BAD, "格式错乱 Check_DateTime"),
                           (BP_RTC_BACK, VARS_RTC_BACK, "倒退 Comp_Data"),
                           (BP_RTC_FAR, VARS_RTC_FAR, "超前 1000 天 Diff_Days")):
        r = None
        if have_wb:
            print("   第 1 步: 等 %s 的调用点 %s" % (_what, breakpoint.text(_bp)))
            r = breakpoint.wait_hit(g, _bp, RTC_WAIT, vars=_v,
                                    label="断[R] 第 1 步「%s」%s" % (_what, breakpoint.text(_bp)))
        _jv = _vals(r)
        _cur = _ts_internal(cmd_bank.gdb_bytes(_jv.get("g_MeterTime"))) if _jv else None
        _bak = _ts_internal(cmd_bank.gdb_bytes(_jv.get("g_MeterTime_Backup"))) if _jv else None
        _rtc.append(r)
        ctx.J.add("第 1 步 判定「%s」的调用点(指令路径)" % _what,
                  None if r is None else r.get("ok"),
                  "源 = TaskTime.c:1095-1097 那条 `||` 链上的这一处调用, 每秒一遍"
                  "(Run_TaskTime :275 的 MSG_SecStep 分支 —— 每秒, 不是每分钟); "
                  "停点 %s | 停时读到的量: %s | 当前钟=%s 备份钟=%s ⇒ %s"
                  % (breakpoint.text(_bp), _jv or "(读不到)", _cur or "读不出", _bak or "读不出",
                     _stop_txt(r)),
                  crit=None, obs=judge.DEBUG, falsify=FALSIFY["①③对照"])
    _same = None
    if _rtc[0] is not None:
        _a = cmd_bank.gdb_bytes(_vals(_rtc[0]).get("g_MeterTime"))
        _b = cmd_bank.gdb_bytes(_vals(_rtc[0]).get("g_MeterTime_Backup"))
        if _a and _b:
            _same = (list(_a[:6]) == list(_b[:6]))
    ctx.J.add("第 1 步 常态下当前钟与备份钟逐字节相同(静止态基线)",
              _same,
              "格式错乱那条停点上: g_MeterTime=%s | g_MeterTime_Backup=%s ⇒ 六字节%s"
              % (_hex(cmd_bank.gdb_bytes(_vals(_rtc[0]).get("g_MeterTime"))) if _rtc[0] else "读不到",
                 _hex(cmd_bank.gdb_bytes(_vals(_rtc[0]).get("g_MeterTime_Backup"))) if _rtc[0] else "读不到",
                 "相同" if _same else ("不同" if _same is False else "读不齐")),
              crit=None, obs=judge.DEBUG,
              falsify="常态下两份钟就不一样 ⇒ 后面拿它俩的年字节差判『走了倒退那一支』不成立")

    # ---- 阶段三(第 5 步通道一): 645 广播校时 ⇒ 固件给最近一条故障补结束时间 ----
    # `trig` 让**同一次发帧**既驱动固件、又停在清除口上; 没会话时给 None ⇒ 走纯黑盒那条路,
    # 那时白盒段如实记『没有调试会话 ⇒ 没做成』(**不冒充成"固件不清故障"**)。
    # ⚠ `ctx.anchor()` 而**不是** `ctx.bp()`: 库这个动词的 `bp=` 收的是带判别字的锚点, 由
    #   `with_trigger` 当场挂、用完必撤(`_drop=True`)。`ctx.bp()` 是当场就挂上, 那是给"断点跨几次
    #   复用"的用法 —— 用错会在下一次触发之前先把核撂停, 其后串口帧整帧无应答。
    ctx.hold(*cmd_bank.calitime_bc645_evidence(
        ser, delta=DELTA, pre=pre, trig=ctx.trig(), bp=ctx.anchor(BP_CLR),
        bp_vars=VARS_CLR), "5-8 清除段")

    # ---- 阶段四(第 2、3 步): 注入造『时间倒退』⇒ 停在发生支的落库那一句, 读记录体五段 ----
    # 注入改的是 `g_MeterTime[5]`(年字节) —— 它就是 `Buff2` 所指的那份当前时间, 而备份没动
    #   ⇒ `Comp_Data(Buff2, PTime, 6) < 0` 成立, 固件走校正支并调 Recd_TimeError(TRUE, 0)。
    # 基线**在校时之后重读**: 判定这时才开, 新的那一条才是"应新增"的比对起点。
    pre_inj = cmd_bank.read_clock_error_event(ser)
    t_inj = cmd_bank.read_clock(ser, quiet=True)
    r_hit = None
    if have_wb:
        r_hit = breakpoint.inject_hit(
            g, BP_INJ, cmd_bank.CE_INJ_ASSIGN,
            watch=BP_HIT, watch_vars=VARS_HIT, at_vars=VARS_INJ, timeout=HIT_WAIT,
            label="断[A] 注入 g_MeterTime[5] 年字节 −1 → 停在落库那一句 %s"
                  % breakpoint.text(BP_HIT), crit=None)
        print("   第 2、3 步: 注入造倒退, 停到落库那一句 → %s" % _stop_txt(r_hit))
    _buff = cmd_bank.gdb_bytes(_vals(r_hit).get("buff")) if r_hit else []
    _f = _fields(_buff)
    post = cmd_bank.read_clock_error_event(ser)
    cnt1 = cmd_bank.event_area_count(ser, cmd_bank.CE_EVENT)
    print("   读回: 时钟故障口 条数=%s | 最新一条 %s" % ("读不出" if cnt1 is None else cnt1,
                                                        _row_txt(post)))
    if _f:
        print("   第 3 步 停点读到记录体: 发生时刻=%s(%s) 结束时刻=%s(%s) 发生源=%s"
              % (_hex(_f["发生时刻"]), _rec_ts(_f["发生时刻"]) or "还原不出",
                 _hex(_f["结束时刻"]), _rec_ts(_f["结束时刻"]) or "空",
                 _f["发生源"]))
        print("                        发生侧电能=%s" % _hex(_f["发生侧电能"]))
        print("                        结束侧电能=%s" % _hex(_f["结束侧电能"]))

    # 判据① 白盒那一半: 停在**发生支自己的**写库那一句 ⇒ "判故障并落库"这条路真走过
    ctx.J.add("① 固件在年字节倒退时走到发生支的落库那一句(TaskRecord.c:1549 的 prev)",
              None if r_hit is None else r_hit.get("ok"),
              "注入 g_MeterTime[5] 减 1 之后停在 %s; 停时 buff=%s sour=%s"
              % (breakpoint.text(BP_HIT), _hex(_buff) if _buff else "(读不到)",
                 _vals(r_hit).get("sour") or "读不到"),
              crit="①", obs=judge.DEBUG, falsify=FALSIFY["①白盒"])

    # 判据① 黑盒那一半: 事件 0x2E 新增一条(698 读回)。
    # ⚠ 基线的最近一条若还是**未闭合**的, 固件自己的判定(:1512-1526)本就不放行 ⇒ 观测不到写库,
    #   那一半记 `ok=None`(没做成), **不是固件不判故障** —— 与"读数说它没写"要分开。
    adv, why_adv = cmd_bank.event_advanced(post.get("occur"), pre_inj.get("occur"), t_inj)
    _pe = ((pre_inj.get("end") or {}).get("ts") or "").strip()
    if adv is None:
        ok1b, note1b = None, "这一个字都没读回 ⇒ 没做成"
    elif adv:
        ok1b, note1b = True, ""
    elif cmd_bank.ce_blank(_pe):
        ok1b, note1b = None, ("基线里最近一条是**未闭合**的(结束时间 %r)⇒ TaskRecord.c:1512-1526 的判定"
                              "本就不放行 ⇒ 观测不到写库, 这一半记没做成(不是固件不判故障)" % (_pe or "(空)"))
    else:
        ok1b, note1b = False, "基线里没有未闭合记录(判定是开的)⇒ 固件判了却没写库"
    ctx.J.add("① 事件 0x2E 新增一条(698 读回; 注入前后各读一次)",
              ok1b, why_adv + (" —— " + note1b if note1b else ""),
              crit="①", falsify=FALSIFY["①黑盒"])

    # 判据③ 发生侧那一半: 记录的发生时间 = 故障发生那一刻, 而不是校正之后的时刻。
    #   两问合一: ① 停点上读到的 buff[0..5] 与 698 读回那一条的时标对得上(是同一份);
    #             ② 那个时刻落在注入窗口内(±CE_TOL 秒)。
    _ts_rec = _rec_ts(_f.get("发生时刻"))
    _ts_wire = ((post.get("occur") or {}).get("ts") or "").strip()[:19] or None
    _lo = cmd_bank.clock_add(t_inj, -cmd_bank.CE_TOL)
    _hi = cmd_bank.clock_add(t_inj, cmd_bank.CE_TOL)
    _same_ts = None if (_ts_rec is None or _ts_wire is None) else (_ts_rec == _ts_wire)
    _in_win = None if (_ts_wire is None or _lo is None or _hi is None) else (_lo <= _ts_wire <= _hi)
    ctx.J.add("③ 时标取自故障发生的那一刻(停点记录体 ↔ 698 读回对拍 + 注入窗口)",
              _tri([_same_ts, _in_win]),
              "停点 buff[0..5]=%s → 按固件编码器还原=%s | 698 读回 ts=%s | 注入时刻=%s, "
              "窗口=[%s, %s] ⇒ %s"
              % (_hex(_f.get("发生时刻")), _ts_rec or "还原不出", _ts_wire or "读不出",
                 t_inj or "读不出", _lo or "算不出", _hi or "算不出",
                 "落在窗口内" if _in_win else ("窗口外" if _in_win is False else "没做成")),
              crit="③", obs=judge.DEBUG, falsify=FALSIFY["③发生"])

    # ---- 阶段五(第 1 步的结论): 停一次校正支, 读倒退的两个操作数 ----
    # 注入之后 `Comp_Data(Buff2, PTime, 6) < 0` 成立 ⇒ 走 :1098 那一支, 由 :1103 用备份把钟拨回来。
    # ⚠ 这一停上 `Buff2` 还是**被改小的那个**: :1104 的 `Copy_Data(Buff2, PTime, 6)` 在它之后。
    #   ⇒ g_MeterTime 的年字节应比 g_MeterTime_Backup 小 1(注入减掉的那个 1), 其余五字节相同。
    r_br = None
    if have_wb:
        r_br = breakpoint.wait_hit(g, BP_BRANCH, BRANCH_WAIT, vars=VARS_BRANCH,
                                   label="断[R] 第 1 步 校正支 :1103(用备份把钟拨回来)")
    _bv = _vals(r_br)
    _gm = cmd_bank.gdb_bytes(_bv.get("g_MeterTime")) if _bv else []
    _gb = cmd_bank.gdb_bytes(_bv.get("g_MeterTime_Backup")) if _bv else []
    _diff = (_gb[5] - _gm[5]) if (len(_gm) >= 6 and len(_gb) >= 6) else None
    _rest = (list(_gm[1:5]) == list(_gb[1:5])) if (len(_gm) >= 6 and len(_gb) >= 6) else None
    ctx.J.add("第 1 步 三条判定里哪一条成立(倒退) —— 走 :1103 的校正支",
              _tri([None if r_br is None else r_br.get("ok"),
                    None if _diff is None else (_diff == 1),
                    _rest]),
              "停点 %s; 停时 g_MeterTime=%s(=%s) 比 g_MeterTime_Backup=%s(=%s) 的年字节小 %s, "
              "其余五字节%s | %s"
              % (breakpoint.text(BP_BRANCH), _hex(_gm), _ts_internal(_gm) or "读不出",
                 _hex(_gb), _ts_internal(_gb) or "读不出",
                 _diff if _diff is not None else "读不齐",
                 "相同" if _rest else ("不同" if _rest is False else "读不齐"), _stop_txt(r_br)),
              crit=None, obs=judge.DEBUG, falsify=FALSIFY["①③对照"])

    # ---- 阶段六(第 5 步通道二): 645 单播写『日期及星期及时间』⇒ 最近一条被结束掉 ----
    # 此刻记录区里最新那条是注入造出来的, 它**未闭合** —— 这正是 `Recd_TimeError(FALSE, 0)` 能过
    #   :1512-1526 那道判定的前提(已有未闭合记录 且 str=FALSE 才放行)。
    _chan(ser, ctx, g, have_wb, "D", "日期及星期及时间", WRITE_DI_DT, _body_dt, BP_CLR_DT,
          VARS_CLR_DT, CB_WAIT)

    # ---- 阶段七(第 5 步通道三): 先注入造一条新的未闭合记录, 再写『日期及星期』 ----
    # ⚠ 通道二刚把上一条闭合掉了, 判定这时又关着 ⇒ 必须先再造一条(每一条清除通道都要各自
    #   先有一条未闭合记录, 才走得到它自己的那个清除口)。
    r_hit2 = None
    if have_wb:
        r_hit2 = breakpoint.inject_hit(
            g, BP_INJ, cmd_bank.CE_INJ_ASSIGN, watch=BP_HIT, watch_vars=VARS_HIT,
            at_vars=VARS_INJ, timeout=ROUND_WAIT, crit=None,
            label="断[A] 通道三前置: 再注入一次造一条未闭合记录")
        print("   通道三前置: 再注入一次 → %s" % _stop_txt(r_hit2))
    _chan(ser, ctx, g, have_wb, "E", "日期及星期", WRITE_DI_WK, _body_wk, BP_CLR_WK, VARS_CLR_WK,
          CB_WAIT)

    # 第 5 步的第四条通道: 698 侧那条清除路径是死代码(`case 0x400002` 整块被注释罩住)⇒ 不发帧。
    #   那一条本脚本证不了(没有帧可发, 也没有可停的调用点), 只把 md 的这条事实记在账本上。
    ctx.J.add("第 5 步 698 侧那条清除路径(死代码, 不发帧)", None,
              "md 第 5 步写明: 698 侧那条清除路径是死代码, `case 0x400002` 整块被注释罩住 ⇒ "
              "本脚本不发它, 也无从停点。三条 645 通道(广播校时 / 写日期及星期及时间 / 写日期及星期)"
              "已逐条验过", crit=None,
              falsify="那条 case 其实没被注释罩住而能走到 ⇒ 698 侧还有第四条清除通道, "
                      "与固件的这三条摆位不符")

    # ---- 阶段八(第 6 步): 最近 10 次 —— 连造 11 回, 条数应停在容量上、最早那条被顶掉 ----
    # 每一回 = 注入造一条 + 用 645 写『日期及星期』把上一条闭合 —— 固件自己的判定在"最新一条
    #   未闭合"时不让发生支再开一行, 所以这两下必须成对, 一回只推进一条。
    oldest_pre = cmd_bank.read_clock_error_event(ser, REC_CAP, wait=WAIT)
    rounds = 0
    if not have_wb:
        print("   !! 第 6 步要 %d 次注入 ⇒ 没有调试会话时整步不做" % BURST)
    else:
        for k in range(1, BURST + 1):
            rk = breakpoint.inject_hit(g, BP_INJ, cmd_bank.CE_INJ_ASSIGN,
                                       watch=BP_HIT, watch_vars=VARS_HIT, at_vars=VARS_INJ,
                                       timeout=ROUND_WAIT, crit=None,
                                       label="断[A] 第 6 步 第 %d/%d 回 注入" % (k, BURST))
            if (rk or {}).get("ok") is not True:
                print("   !! 第 6 步 第 %d 回: 没停在落库那一句 ⇒ 连造停在这一回" % k)
                break
            ctk = cmd_bank.read_clock(ser, quiet=True)
            if not ctk:
                print("   !! 第 6 步 第 %d 回: 表钟读不出 ⇒ 组不出闭合帧, 连造停在这一回" % k)
                break
            _c, _s = _send_di(ser, WRITE_DI_WK, _body_wk(ctk),
                              "645 写日期及星期(第 %d 回的闭合)" % k, wait=BURST_WAIT)
            if _c != 0x94:
                print("   !! 第 6 步 第 %d 回: 闭合帧没被受理(应答命令字 %s) ⇒ 连造停在这一回"
                      % (k, "无" if _c is None else "0x%02X" % _c))
                break
            rounds += 1
        print("   第 6 步: 连造成功 %d/%d 回" % (rounds, BURST))
    cnt_burst = cmd_bank.event_area_count(ser, cmd_bank.CE_EVENT)
    oldest_post = cmd_bank.read_clock_error_event(ser, REC_CAP, wait=WAIT)
    _cap_ok = None if cnt_burst is None else (cnt_burst == REC_CAP)
    _o1 = ((oldest_pre or {}).get("occur") or {})
    _o2 = ((oldest_post or {}).get("occur") or {})
    _repl = None if (not _o1 or not _o2) else ((_o1.get("seq"), _o1.get("ts")) != (_o2.get("seq"), _o2.get("ts")))
    ctx.J.add("⑦ 连造 %d 回后『时钟故障』封顶在容量 %d 上、最早那条被顶掉" % (BURST, REC_CAP),
              None if (not have_wb or rounds < BURST) else _tri([_cap_ok, _repl]),
              "连造成功 %d/%d 回(每回 = 一次注入 + 一次闭合); 条数 触发前=%s → 连造后=%s "
              "(容量 %d); 第 %d 位那条(最早) 前=%s 后=%s"
              % (rounds, BURST, "读不出" if cnt0 is None else cnt0,
                 "读不出" if cnt_burst is None else cnt_burst, REC_CAP, REC_CAP,
                 _row_txt(oldest_pre), _row_txt(oldest_post)),
              crit="⑦", obs=judge.DEBUG,
              falsify="容量不是 %d(固件只留 9 条或 11 条)或顶掉的不是最早那条 ⇒ 第 %d 回读回的"
                      "条数与最早那条的序号/时标对不上" % (REC_CAP, BURST))

    # 不认领判据条目: ①②③ 要判的是"新增了没有/结束时间补上没有/时标取自哪一刻",
    # 条数是解释那三条读数用的, 只进日志(⑦ 已在上一条认领)。
    ctx.J.add("时钟故障口 条数 注入后 / 连造后", None,
              "校时前=%s → 注入后=%s → 连造 %d 回后=%s(容量 %d 条)"
              % ("读不出" if cnt0 is None else cnt0, "读不出" if cnt1 is None else cnt1,
                 BURST, "读不出" if cnt_burst is None else cnt_burst, REC_CAP), crit=None,
              falsify="判据① 命中而条数不增(或增了不止 1) ⇒ 一次故障落的记录不止一条/没落")
    ctx.J.add("停时读到的那一条记录体(五段位置)", None,
              "发生时刻[%d..%d] / 结束时刻[%d..%d] / 两侧正反向有功总电能各 %d 字节"
              "([%d..%d] 与 [%d..%d]) / 发生源 buff[%d]; 值见上面第 2、3 步那一行"
              % (REC_HEAD[0], REC_HEAD[1] - 1, REC_END[0], REC_END[1] - 1, REC_ENE_B[0] - REC_ENE_A[0],
                 REC_ENE_A[0], REC_ENE_A[1] - 1, REC_ENE_B[0], REC_ENE_B[1] - 1, REC_SRC),
              crit=None, obs=judge.DEBUG,
              falsify="五段的位置挪了(结束时刻不在 [6..11] / 电能不在 [12..31]) ⇒ "
                      "第 3 步的字段布局与固件不符")


def _chan(ser, ctx, g, have_wb, tag, what, di, body_fn, bp, bp_vars, wait):
    """第 5 步的一条清除通道: 先抄未闭合的那一条 → 停住发这一帧 → 再抄一次看结束时间补上没。

    四半支合一: 停到了这个清除口 / 帧被受理(应答 0x94) / 写之前确实有一条未闭合的 /
      写之后结束时间真的补上了。哪一半缺了都记 `ok=None` —— 那不是"固件不清故障",
      是这一次没观测成。
    ⚠ **发帧走 `fire_hit`**, 于是"停在清除口"与"帧被受理"是同一次动作的两面, 不是两次触发。
    """
    ct = cmd_bank.read_clock(ser, quiet=True)
    if not ct:
        print("   !! 通道「%s」: 表钟读不出 ⇒ 组不出这一帧(它的数据域要写当前表钟)" % what)
        ctx.J.add("② 通道「%s」" % what, None, "表钟读不出 ⇒ 没做成", crit=None)
        return
    before = cmd_bank.read_clock_error_event(ser)
    res = []

    def do_send():
        res.append(_send_di(ser, di, body_fn(ct), "645 写%s" % what, wait=WAIT))

    r = None
    if have_wb:
        r = breakpoint.fire_hit(g, bp, do_send, timeout=wait, vars=bp_vars, crit=None,
                                label="断[%s] 645 写「%s」→ 停在清除口 %s"
                                      % (tag, what, breakpoint.text(bp)))
        print("   第 5 步 通道「%s」: %s | 应答=%s"
              % (what, _stop_txt(r), "无" if not res else ("0x%02X" % res[0][0] if res[0][0] is not None else "无")))
    else:
        do_send()
    after = cmd_bank.read_clock_error_event(ser)
    _hit = None if r is None else r.get("ok")
    _cmd = res[0][0] if res else None
    _acc = True if _cmd == 0x94 else (False if _cmd is not None else None)
    _be, _af = ((before.get("end") or {}).get("ts") or "").strip(), ((after.get("end") or {}).get("ts") or "").strip()
    _was_blank = True if cmd_bank.ce_blank(_be) else False
    _now_filled = True if (not cmd_bank.ce_blank(_af)) else False
    ok = _tri([_hit, _acc, _was_blank, _now_filled])
    ctx.J.add("② 通道「%s」把最近一条故障的结束时间补上" % what, ok,
              "停点 %s | 应答命令字=%s | 写之前结束时间=%r | 写之后结束时间=%r | %s"
              % (breakpoint.text(bp), "无" if _cmd is None else "0x%02X" % _cmd,
                 _be or "(空)", _af or "(空)", _stop_txt(r)),
              crit="②", obs=judge.DEBUG, falsify=FALSIFY["②写" + what])


def _banner():
    return ("== 5-8 时钟故障 | 工程=%s 表号=%s ==\n"
            "!! 本跑先广播校时(表钟 +%ds、给最近一条故障补结束时间, 当天只能成功一次), 再注入改 "
            "g_MeterTime[5](年字节 −1; 固件按分支校正, 表钟可能被拨到冻结/掉电时刻), 接着写"
            "『日期及星期及时间』与『日期及星期』各一次(表钟被设成本次读回的时刻), 最后连造 %d 回; "
            "收尾靠 scripts/_restore_all.py 拨回。白盒停 %s / %s / %s"
            % (CURRENT.PROJECT, CURRENT.TABLE_ADDR.hex().upper(), DELTA, BURST,
               breakpoint.text(BP_CLR), breakpoint.text(BP_HIT), breakpoint.text(BP_CLR_DT)))


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "5-8 时钟故障(注入造倒退 ⇒ 判故障写库; 三条 645 清除通道各补一次结束时间; 连造 %d 回看封顶)"
        % BURST,
        cmd_bank.clock_error_criteria,
        name="5_8_clock_error",
        parts=[("5-8 时钟故障段", part_clock)],
        gdb=breakpoint, banner=_banner(),
        # ⚠ `inject_allow` 必须在**会话构造时**点名 —— 白名单精确匹配符号名, 事后补不了。
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV,
                        inject_allow=cmd_bank.ce_inject_allow())))
