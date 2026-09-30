# -*- coding: utf-8 -*-
"""在证什么: 时钟故障记档之后, Chk_ReportSta 把它排进主动上报队列并按 C_AutoRptNum(3) 次一轮组帧 ——
  断[A] 看队列计数与那笔事件的 OAD、断[B] 看帧壳/事件列表/跟随上报状态字/次数与周期。
会向表写什么: 645 广播校时(表钟 +%ds, 给最近一条未闭合的 0x2E 补结束时间, 当天只能成功一次);
  再**注入**造时钟故障(g_MeterTime 月字节 −1)并三轮驱动组帧。表钟被 −1 个月那一次不还原,
  台面留厂内态 —— 收尾走 `python scripts/_restore_all.py`。
跑法: `python project/tests/_test_13_1_auto_rpt.py`(真串口 + 真探针; `--no-gdb` 只做两个前置)。
结论怎么读: 账本末行「判据: 满足 n/N」, N=6(⑥ 本台不可证 —— 上送走载波, 台上没有对端收帧);
  退 0 = 通过 / 1 = 失败 / 2 = 未定论。
"""
import time

from common import judge            # 观测种类常量(白盒那五条标 DEBUG, 触发通道标注入)
from common import trial            # 运行外壳: argv/日志/账本/串口/会话/收尾三连/退出码
from meterlib import cmd_bank       # 帧目录 + 语义动词积木(每步自带打印/判 PASS·TBD·FAIL)
from meterlib import watch           # AA80 只读观察簇(串口白盒通路: 读管理芯 RAM)
from project import CURRENT          # 本工程画像: 表号/.out 路径/喂狗点(库不认表, 只有脚本认)
from swdbg import breakpoint            # 断点级白盒(停核 + 注入 + 读函数内局部量); 没接探针 → None
# 整片 .out 地图: 开会话后解一次, 之后断点/注入点只查它(见 swdbg/gdbinit.py 文件头)
from swdbg import gdbinit

# ---- 子项参数(纯数据; 要改的在这里改) ----
WAIT = 2.0                    # 记录读回 / AA80 直读的单次等待
SETTLE = CURRENT.AR_SETTLE          # 注入造完事件之后等它记档落库
SEND_WAIT = CURRENT.AR_SEND_WAIT    # 一轮里 断[A] 注入完到 断[B] 组帧的等待

# ---- 断点: 模块级**字面量元组** ----
# `scripts/_check_anchors.py` 用 `ast.literal_eval` 抠 `BP_<X>`/`VARS_<X>` 这两个名字逐条对源码核
# (变量在断点那一行赋过值没有); 写成 `CB.SOMETHING` 那种表达式就抠不出来, 等于没人核过。
BP_TIME = ("call", "Run_TaskTime", "Fetch_CRC", 1)   # 事件注入停靠点 @TaskTime.c:275 <Run_TaskTime+54>
VARS_TIME = ("g_MeterTime", "g_MeterTime_Backup")        # 注入**前**的两个入参(对照用)
BP_RPT = ("TaskReport.c", 1632)           # 断[A] 排完队、下帧前 @0x1ac44 <Chk_ReportSta+676>
# `rptnum`/`event` 只在 `:1632` 这一行活跃; OAD 那两格是**自描述**读法 —— 判据①认的是
# `TAB_ReportObj[g_ReportIdx[3][0].bit.eve].OAD == 0x302E0200`, 不认那个下标(它是排布, 换版本会漂)。
VARS_RPT = ("rptnum", "event", "g_ReportIdx[3][0].bit.eve",
            "TAB_ReportObj[g_ReportIdx[3][0].bit.eve].OAD", "g_FollowMod")
BP_SEND = ("TaskReport.c", 2050)          # 断[B] Send_Report 入口 @0x1b554 <Auto_Report+1732>
# ⚠ `TAB_RevByte` 与 `g_FollowSta[3]` 一起读: 判据③比的是"组帧块用的那张变换表"对不对,
#   把表本身当输入读回来, 固件改了表也照样判得对; 硬编码那张表就是把固件常量抄成第二份。
VARS_SEND = ("buff", "len", "TAB_RevByte", "g_FollowSta[3]", "g_AutoRptNum", "g_AutoRptGap")

# ---- 半途中止时要一次记全的条目(纯数据; 与正常路径同名同号**同观测**, 少记一条 = 分母变小)----
ENTRIES_ALL = (
    ("①", "13-1 ① 非掉电事件排进队列", judge.DEBUG),
    ("②", "13-1 ② 帧里含该事件对象", judge.DEBUG),
    ("③", "13-1 ③ 帧里的跟随上报状态字", judge.DEBUG),
    ("④", "13-1 ④ 次数与周期", judge.DEBUG),
    ("⑤", "13-1 ⑤ 帧壳自洽", judge.DEBUG),
)


def _add(J, label, ok, why, crit, obs=judge.DEBUG):
    """记一条判据(`falsify` 按 `crit` 从库里的单点取)+ 打一行三态。

    ⚠ `trig` 一律 `JS.TRIG_INJECT`: 本项被观察的状态是**注入**造出来的(事件造不出、成组也是注入驱动),
      不是发帧造的 —— 触发通道是这一栏的承重信息。
    """
    J.add(label, ok, why, crit=crit, falsify=cmd_bank.AR_FALSIFY[crit], obs=obs, trig=judge.TRIG_INJECT)
    print("   [%s] %s —— %s" % ({True: "PASS", False: "FAIL", None: "TBD"}[ok], label, why))


def _stop_unproven(ctx, why):
    """半途中止的统一收场: 条目一次记全(`ok=None`) → 出声 → 停住(退 2, 不是失败)。"""
    ctx.J.extend(cmd_bank.unproven_records(ENTRIES_ALL, why, cmd_bank.AR_FALSIFY))
    ctx.J.note("13-1 主动上报段: 半途中止(这一次没做成) —— %s" % why)
    raise trial.Stop("%s ⇒ 未定论(退 2), 不是失败" % why)


def _frame_of(f):
    """断[B] 那一停的 `(buff, len)`; 任一读不到 → `(b"", None)`。"""
    v = (f or {}).get("vars") or {}
    return bytes(x & 0xFF for x in cmd_bank.gdb_bytes(v.get("buff"))), cmd_bank.gb1(v.get("len"))


def _post_u8(f, name):
    """帧后 AA80 那一份里的单个 u8; 读不到 → None。"""
    b = ((f or {}).get("_post") or {}).get(name)
    return b[0] if b else None


def part_auto_rpt(ctx):
    """一段 = 13-1 的全部条目: 前置(闭合 0x2E / 上报使能) → 注入造事件 → 三轮 停断[A]注入 → 等断[B]读帧。

    ⚠ 观测一律 attach(**禁用 launch**: launch 会复位表、RAM 态清零); 见 CLAUDE.md 调试链纪律 1。
    ⚠ 事件与成组都靠**注入**驱动(`TaskTime.c:275` / `TaskReport.c:1632`)—— 帧通道造不出"表判定的那一刻",
      所以没会话时五条一起记『没做成』, 本项**没有可降级的黑盒替身**。
    """
    J, ser = ctx.J, ctx.ser
    print("\n===== 13-1 主动上报: 注入造时钟故障 → 三轮组帧 × 断点看队列/载荷/计数 =====")

    # ---- 开会话(断点观测)。台面没接探针 ⇒ None, 五条如实记「没做成」, 不冒充成"固件不上报" ----
    ctx.session()
    # 整片 .out 解一次(要停核遍历, 内部按块喂狗, 收尾放行) —— 之后 `ctx.bp()` /
    # `inject_anchor()` / `decision_anchor()` 一律查这张图, 谁都不再独立反汇编。
    if ctx.g is not None:
        gdbinit.build(ctx.g)
    g = ctx.g
    have_wb = g is not None

    cmd_bank.enter_factory(ser)

    # ---- 前置①: 最近一条 0x2E 必须闭合 ----
    #   `Recd_TimeError` 体首有一道固件自己的判定(`TaskRecord.c:1508-1526`): 最新一条 0x2E 若
    #   「头有、尾无」且这次是**判故障要记新的**(str=TRUE) ⇒ 直接返 FALSE、一个字都不写 ——
    #   于是 :1544 不写记录、:1553 的 `Rpt_ReportSta(ID_TimeError, 1)` 也不执行, 事件进不了队列。
    #   那是**台面态**(台上躺着一条未闭合记录), 不是固件坏; 5-8 同款前置(见 CE_ 块首那张说明)。
    pre = cmd_bank.read_clock_error_event(ser, wait=WAIT)
    e_occ = ((pre.get("occur") or {}).get("ts") or "").strip()[:19]
    e_end = ((pre.get("end") or {}).get("ts") or "").strip()[:19]
    if (not cmd_bank.ce_blank(e_occ)) and cmd_bank.ce_blank(e_end):
        print("\n[前置] 最近一条 0x2E 未闭合(发生 %s / 结束 %r)⇒ 先发 645 广播校时给它补尾巴"
              % (e_occ, e_end))
        t0 = cmd_bank.read_clock(ser, quiet=True)
        tgt = cmd_bank.clock_add(t0, cmd_bank.CE_CALI_DELTA) if t0 else None
        if tgt is None:
            _stop_unproven(ctx, "表钟读不回 ⇒ 摆不出校时目标, 收不掉那条未闭合的 0x2E 记录 ⇒ "
                                "事件进不了队列, 五条本次都不做")
        cmd_bank.send("645.time.broadcast_sync", wait=WAIT, _json=True, ser_shared=ser, param=tgt)
        t1 = cmd_bank.read_clock(ser, quiet=True)
        _d0, _d1 = cmd_bank.clock_dt(t0), cmd_bank.clock_dt(t1)
        print("[前置] 校时目标 %s; 校时后表钟 %s(应走到目标附近, 实测 Δ=%s) —— 广播帧无应答属正常, "
              "受理没受理看表钟走没走到目标, 不看有没有回帧"
              % (tgt, t1, ("%ds" % (_d1 - _d0).total_seconds()) if (_d0 and _d1) else "读不回"))
        pre = cmd_bank.read_clock_error_event(ser, wait=WAIT)
        e_end = ((pre.get("end") or {}).get("ts") or "").strip()[:19]
        if cmd_bank.ce_blank(e_end):
            _stop_unproven(ctx, "645 广播校时没给那条未闭合记录补上结束时间(仍 %r)⇒ 固件 "
                                "`TaskRecord.c:1508-1526` 那道判定会把新的 0x2E 入队关掉, 事件进不了"
                                "队列。⚠ 为什么没受理**未钉死**: 5-8 判据② 逐行核过, `:734` 之前只剩"
                                "两道 `return ER_OTHER`(:713 当日判定 / :720 写不进), 台账记的是"
                                "『两者之一』, 别再往下断或归因" % (e_end,))
    print("\n[前置] 最近一条 0x2E: 发生 %r / 结束 %r ⇒ 判定开着(可以记新故障)"
          % (e_occ or "(空)", e_end or "(空)"))

    # ---- 前置②: 上报使能必须开着(`:1773` 关着就整条 return, 一帧都发不出) ----
    en = watch.watch_vars(ser, ["g_ReportEn"], tag="上报使能 g_ReportEn", wait=WAIT).get("g_ReportEn")
    if en is None or len(en) < 4 + cmd_bank.AR_PLC_M:
        _stop_unproven(ctx, "`g_ReportEn` AA80 读不回(或长度不足)⇒ 证不了上报使能开着, "
                            "五条本次都不做")
    en1, en3 = en[1], en[3 + cmd_bank.AR_PLC_M]
    print("[前置] g_ReportEn[1]=%d, g_ReportEn[%d]=%d(都要 == 1, 否则 Auto_Report 在 :1773 直接 return)"
          % (en1, 3 + cmd_bank.AR_PLC_M, en3))
    if en1 != 1 or en3 != 1:
        _stop_unproven(ctx, "上报使能关着(g_ReportEn[1]=%d, g_ReportEn[%d]=%d)⇒ `Auto_Report` 在 "
                            "TaskReport.c:1773 直接 return, 一帧都发不出, 五条本次都不做"
                            % (en1, 3 + cmd_bank.AR_PLC_M, en3))

    if not have_wb:
        print("   !! 本次无调试会话 ⇒ ①②③④⑤ **都不做**: 事件由注入造(%s), `rptnum` 与 "
              "`buff`/`len` 都是函数内局部量, 串口读不回; 本项没有可降级的黑盒替身"
              % (breakpoint.text(BP_TIME)))
        for k, _txt, _obs in ENTRIES_ALL:
            _add(J, "13-1 %s(需调试会话)" % k, None,
                 "没有调试会话 ⇒ 事件造不出、`rptnum` 与 `buff`/`len` 读不回 ⇒ 这一条不做"
                 "(本项没有可降级的黑盒替身)", crit=k)
        J.note("13-1 本次范围 = 仅两个前置; ①②③④⑤ 都没做")
        return

    # ---- 造事件: 停 TaskTime.c:275 把 g_MeterTime 月字节 −1(时钟倒退) ----
    print("\n[造事件] 停 %s 注入 %s(跑完必须 `_restore_all` 拨钟)"
          % (breakpoint.text(BP_TIME), cmd_bank.AR_ASSIGN_TIME))
    r_t = breakpoint.inject_hit(g, BP_TIME, cmd_bank.AR_ASSIGN_TIME, at_vars=VARS_TIME,
                        label="13-1 造事件: 注入时钟倒退", timeout=SETTLE + 20.0)
    r_t = r_t or {}
    _ah_t = r_t.get("at_hit")
    print("   注入点 %s; 注入账 %s" % (_ah_t.where() if _ah_t else "未命中(造事件)",
                                       r_t.get("injects")))
    if _ah_t is None:
        _stop_unproven(ctx, "注入点 %s 没停到 ⇒ 时钟故障没造出来, 事件进不了队列, 五条本次都不做"
                            % (breakpoint.text(BP_TIME)))
    time.sleep(SETTLE)

    # ---- 三轮: 停 断[A] 注入状态字/周期 → 等 断[B] 读组帧缓冲 ----
    frames = []
    for k in range(1, cmd_bank.AR_MAX_SEND + 1):
        print("\n[第 %d 轮] 停 断[A] %s 注入 %s; 等 断[B] %s"
              % (k, breakpoint.text(BP_RPT), cmd_bank.AR_ASSIGN_RPT, breakpoint.text(BP_SEND)))
        r = breakpoint.inject_hit(g, BP_RPT, cmd_bank.AR_ASSIGN_RPT, watch=BP_SEND, watch_vars=VARS_SEND,
                          at_vars=VARS_RPT,
                          label="13-1 第 %d 轮 停断[A]注入状态字 → 等断[B]组帧" % k,
                          timeout=SEND_WAIT)
        r = r or {}
        _ah, _wh = r.get("at_hit"), r.get("watch_hit")
        print("   断[A] %s; 断[B] %s" % (_ah.where() if _ah else "未命中",
                                        _wh.where() if _wh else "未命中"))
        post = dict(watch.watch_vars(ser, cmd_bank.AR_POST_VARS, tag="13-1_post%d" % k, wait=WAIT))
        print("   帧后 AA80: %s" % " ".join(
            "%s=%s" % (n, (b.hex(" ").upper() if b else "读不到")) for n, b in post.items()))
        r["_post"] = post
        frames.append(r)

    f1 = frames[0]
    w1 = f1.get("vars") or {}          # 断[B] 那一停的 watch_vars
    a1 = f1.get("at_vals") or {}       # 断[A] 那一停的 at_vars

    # ---- ① 非掉电事件排进队列(读 断[A] 的栈上局部 `rptnum`) ----
    _rp = cmd_bank.ar_u8s(a1.get("rptnum"), 2)
    rpt0, rpt1 = (_rp[0], _rp[1]) if _rp else (None, None)
    oad_ev = cmd_bank.ar_u32(str((a1.get("TAB_ReportObj[g_ReportIdx[3][0].bit.eve].OAD") or "")))
    _fm = cmd_bank.ar_u32s(a1.get("g_FollowMod"), 2)
    fm0, fm1 = (_fm[0], _fm[1]) if _fm else (None, None)
    _ah1 = f1.get("at_hit")
    d1 = ("断[A] %s; rptnum=%s(期望 [0, %d]); event=%s; 队列里那笔的 OAD=%s(期望 0x%08X); "
          "g_FollowMod=%s"
          % (_ah1.where() if _ah1 else "未命中", _rp, cmd_bank.AR_RPT_NUM, a1.get("event"),
             ("0x%08X" % oad_ev) if oad_ev is not None else "读不到", cmd_bank.AR_OAD_TIMERROR, _fm))
    if rpt1 is None or rpt0 is None:
        _add(J, "13-1 ① 非掉电事件排进队列", None,
             "断[A] 的 `rptnum` 读不到(位置表空洞 / 没停到)⇒ 这一条没做成: " + d1, crit="①")
    elif fm0 is None or fm1 is None:
        _add(J, "13-1 ① 非掉电事件排进队列", None,
             "`g_FollowMod` 读不到 ⇒ 分不开 `rptnum[1]` 是 :1588 事件路给的还是 :1605 跟随路给的"
             "⇒ 这一条没做成: " + d1, crit="①")
    elif fm0 != 0:
        _add(J, "13-1 ① 非掉电事件排进队列", None,
             "台面 `g_FollowMod[0]=%d ≠ 0` ⇒ `:1599-1605` 那条跟随入队支也够得着, `rptnum[1]` 不再"
             "只由事件路给 ⇒ 本次不构成对 ① 的观测(不是固件坏): %s" % (fm0, d1), crit="①")
    else:
        _add(J, "13-1 ① 非掉电事件排进队列", (rpt1 == cmd_bank.AR_RPT_NUM and rpt0 == 0), d1, crit="①")

    # ---- ② 断[B] 那一帧里含该事件对象 ----
    bb, ln = _frame_of(f1)
    ev_mark = bytes.fromhex(cmd_bank.AR_APDU_EVENT)
    arr_mark = bytes.fromhex(cmd_bank.AR_APDU_ARRAY)
    i_ev = bb.find(ev_mark)
    i_arr = bb.find(arr_mark)
    cnt = bb[i_arr + len(arr_mark)] if (0 <= i_arr < len(bb) - len(arr_mark)) else None
    d2 = ("断[B] %s; buff=%dB len=%s; 列表头 %s @%s; 计数=%s; `51 30 2E 02 00` @%s; APDU[0:%d]=%s"
          % ((f1.get("watch_hit").where() if f1.get("watch_hit") else "未命中"), len(bb), ln,
             arr_mark.hex(" ").upper(), i_arr, cnt, i_ev, min(len(bb), 24),
             bb[:24].hex(" ").upper()))
    if not bb or ln is None:
        _add(J, "13-1 ② 帧里含该事件对象", None,
             "断[B] 的 `buff`/`len` 读不到 ⇒ 这一条没做成: " + d2, crit="②")
    else:
        _add(J, "13-1 ② 帧里含该事件对象",
             (i_ev >= 0 and cnt is not None and cnt >= 1 and i_ev > i_arr), d2, crit="②")

    # ---- ③ 跟随上报状态字(期望值由本机按固件那份 TAB_RevByte 现算, 不硬编码) ----
    rev = cmd_bank.ar_u8s(w1.get("TAB_RevByte"), 16)
    sta = cmd_bank.ar_u32(str(w1.get("g_FollowSta[3]") or ""))
    fol_mark = bytes.fromhex(cmd_bank.AR_APDU_FOLLOW)
    i_f = bb.find(fol_mark)
    got4 = bb[i_f + len(fol_mark):i_f + len(fol_mark) + 4] if i_f >= 0 else b""
    want4 = cmd_bank.ar_rev4(sta, rev) if (sta is not None and rev is not None) else None
    d3 = ("断[B] `g_FollowSta[PT_PLC_M]=%s`(期望注入值 0x%08X); `TAB_RevByte`=%s; 本机算式给 %s; "
          "帧里 `20 15 02 00 01 04 20` @%s 后 4B=%s"
          % (("0x%08X" % sta) if sta is not None else "读不到", cmd_bank.AR_FOLLOW_BIT,
             rev if rev else "读不到", want4.hex(" ").upper() if want4 else "算不出",
             i_f, got4.hex(" ").upper() if got4 else "读不到"))
    if not bb or want4 is None or not got4:
        _add(J, "13-1 ③ 帧里的跟随上报状态字", None,
             "断[B] 的 `buff` / `g_FollowSta[3]` / `TAB_RevByte` 有一样读不到 ⇒ 这一条没做成: " + d3,
             crit="③")
    elif fm1 != 0:
        _add(J, "13-1 ③ 帧里的跟随上报状态字", None,
             "台面 `g_FollowMod[1]=%r ≠ 0` ⇒ `:1911` 那道跟随组帧支整条不进, 帧里不会有那一段"
             "(不是固件坏)⇒ 本次不构成对 ③ 的观测: %s" % (fm1, d3), crit="③")
    elif sta != cmd_bank.AR_FOLLOW_BIT:
        _add(J, "13-1 ③ 帧里的跟随上报状态字", None,
             "断[B] 读到的 `g_FollowSta[PT_PLC_M]` 不是本轮的注入值(读回 %s, 期望 0x%08X)⇒ "
             "`g_FollowMod[0]==0` 时 `:1564-1565` 每拍都会把它清掉, 注入没活到组帧那一刻 ⇒ "
             "这一条没做成: %s" % (("None" if sta is None else "0x%08X" % sta), cmd_bank.AR_FOLLOW_BIT, d3),
             crit="③")
    else:
        _add(J, "13-1 ③ 帧里的跟随上报状态字", (got4 == want4), d3, crit="③")

    # ---- ⑤ 帧链路层自洽 ----
    if not bb or ln is None:
        _add(J, "13-1 ⑤ 帧壳自洽", None,
             "断[B] 的 `buff`/`len` 读不到 ⇒ 这一条没做成: " + d2, crit="⑤")
    else:
        l_lo = bb[1] if len(bb) > 2 else None
        l_hi = bb[2] if len(bb) > 2 else None
        tail_i = (ln + 1) if isinstance(ln, int) else -1
        got = {"68": bb[0] == 0x68,
               "len": (l_lo is not None and (l_lo | (l_hi << 8)) == ln),
               "83": len(bb) > 3 and bb[3] == cmd_bank.AR_FRAME_CMD,
               "05": len(bb) > 4 and bb[4] == cmd_bank.AR_FRAME_AF,
               "CA": len(bb) > 11 and bb[11] == cmd_bank.AR_FRAME_CA,
               "16": 0 <= tail_i < len(bb) and bb[tail_i] == cmd_bank.AR_FRAME_TAIL}
        d5 = ("断[B] buff=%dB len=%s ⇒ 起 %s | LEN(2B)=%s(与 len %s) | CMD=%s | AF=%s | CA=%s | "
              "尾[%d]=%s"
              % (len(bb), ln, bb[:1].hex(" ").upper(),
                 ("%02X %02X" % (l_lo, l_hi)) if l_lo is not None else "读不到",
                 "自洽" if got["len"] else "不符",
                 ("%02X" % bb[3]) if len(bb) > 3 else "读不到",
                 ("%02X" % bb[4]) if len(bb) > 4 else "读不到",
                 ("%02X" % bb[11]) if len(bb) > 11 else "读不到",
                 tail_i, ("%02X" % bb[tail_i]) if (0 <= tail_i < len(bb)) else "越界"))
        _add(J, "13-1 ⑤ 帧壳自洽", all(got.values()), d5, crit="⑤")

    # ---- ④ 次数与周期 ----
    seq, gaps_stop, gaps_post = [], [], []
    for f in frames:
        _n = cmd_bank.ar_u8s((f.get("vars") or {}).get("g_AutoRptNum"), 2)
        seq.append(_n[1] if _n else None)
        gaps_stop.append(cmd_bank.ar_u8s((f.get("vars") or {}).get("g_AutoRptGap"), 1))
        gaps_post.append(_post_u8(f, "g_AutoRptGap"))
    gaps_stop = [g[0] if g else None for g in gaps_stop]
    _ln_ = cmd_bank.ar_u8s((frames[-1].get("_post") or {}).get("g_AutoRptNum"), 2)
    last_num = _ln_[1] if _ln_ else None
    d4 = ("断[B] 逐轮 g_AutoRptNum[1]=%s(期望 %s); 断[B] 停时 g_AutoRptGap=%s(计数器刚走完, 应为 0); "
          "帧后 AA80 g_AutoRptGap=%s(末轮之前期望 (0, %d], 末轮期望 > %d —— :2061-2065 的 += 5); "
          "末轮帧后 g_AutoRptNum[1]=%s(期望 0)"
          % (seq, list(range(cmd_bank.AR_RPT_NUM, 0, -1)), gaps_stop, gaps_post, cmd_bank.AR_GAP_IDLE,
             cmd_bank.AR_GAP_IDLE, last_num))
    _want = list(range(cmd_bank.AR_RPT_NUM, 0, -1))
    _c4 = (seq == _want
           and all(g is not None and 0 < g <= cmd_bank.AR_GAP_IDLE for g in gaps_post[:-1])
           and (gaps_post[-1] is not None and gaps_post[-1] > cmd_bank.AR_GAP_IDLE)
           and last_num == 0)
    if None in seq or any(g is None for g in gaps_post):
        _add(J, "13-1 ④ 次数与周期", None,
             "三轮里有读不回的数(`g_AutoRptNum`/`g_AutoRptGap`)⇒ 这一条没做成: " + d4, crit="④")
    else:
        _add(J, "13-1 ④ 次数与周期", _c4, d4, crit="④")

    J.note("13-1 本次范围 = 两个前置 + 注入造事件 + 三轮组帧(白盒); ⑥ 本台不可证(上送走载波, "
           "台上无对端收帧)")


def _banner():
    return ("== 13-1 主动上报 | 工程=%s 表号=%s ==\n"
            "!! 本跑先 645 广播校时(表钟 +%ds、给最近一条未闭合的 0x2E 补结束时间, 当天只能成功一次), "
            "再注入造时钟故障并三轮驱动组帧; 表钟被 −1 个月那一次不还原, 台面留厂内态 —— "
            "收尾靠 scripts/_restore_all.py"
            % (CURRENT.PROJECT, CURRENT.TABLE_ADDR.hex().upper(), cmd_bank.CE_CALI_DELTA))


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "13-1 主动上报(注入造时钟故障 ⇒ 排进队列 → 三轮组帧; 断点看 rptnum/载荷/计数)",
        cmd_bank.auto_rpt_criteria,
        name="13_1_auto_rpt",
        parts=[("13-1 主动上报段", part_auto_rpt)],
        gdb=breakpoint, banner=_banner(),
        # ⚠ `inject_allow` 必须在**会话构造时**点名 —— 白名单精确匹配符号名, 事后补不了。
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV,
                        inject_allow=cmd_bank.ar_inject_allow())))
