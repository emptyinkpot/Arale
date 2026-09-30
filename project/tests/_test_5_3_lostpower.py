# -*- coding: utf-8 -*-
"""在证什么: 掉电判据成立且去抖满时落『发生』一笔(:3991, 写全行、结束时刻清零), 判据翻假且去抖满时
  落『恢复』一笔(:4006, 只写尾段、把结束时刻与累计秒补进上一行), 两笔时标 == 当时表钟,
  『发生』那一笔写入后置上报标志(:3994 → TaskReport.c:2229 bFlag=TRUE), 稳态判据与现有状态一致时
  去抖计时复位(:2442)所以记录数不增, Recd_LostPower 开头那道守卫(:3958-3974)按"上一行完不完整"
  与 sta 分工, 两条出口各挡一类; 698 读回的那一行上「发生时刻」与「结束时刻」两列都真有值;
  连造到容量(NUM_LostPower=10)之后有效条数停在 10 而索引区头 3 字节的总次数照加。
会向表写什么: 只发 645 进厂内(记录读回受安全判定管, 厂外每条都被打回)。**没有一个帧造得出状态** ——
  『恢复』靠停判据行 :2415 把 `g_Volt[0]` 写成 140000、同时把 `g_EventSta[EV_LostPower]` 写成 TRUE
  (判据翻假 ⇒ 同拍 g_EventFlg 移位寄存器成"混合" ⇒ state 取 g_EventSta 的反 ⇒ 同拍去抖满);
  注入**只改触发条件**, 记录的时刻与电量快照仍是固件现读的。『发生』**不注入**, 下一拍 SPI 把
  g_Volt 刷回 ~124V 后固件自己到点。参数区一个字节不动; 产物就是那几笔掉电记录。
  台面收尾走 `python scripts/_restore_all.py`(它会退厂内并放核)。
跑法: `python project/tests/_test_5_3_lostpower.py`(要接 J-Link; 没接照样跑, 白盒那几条记未证)。
结论怎么读: 账本末行「判据: 满足 n/N」, N=7; 退 0 = 通过 / 1 = 失败 / 2 = 未定论。

断点为什么落在这几处(`scripts/_check_anchors.py` 逐行核过):
  `:2415` 是判据行本身, 它是一句纯判断、源码上没有调用结构可依 ⇒ 留 `("line", …)` 规范形;
  另外五处都带被调函数的判别字(`("prev", …)` 停在写库那条 `bl` 之前, `("call", …)` 停在调用返回之处)。
  ⚠ 递**断点元组**而不是已挂好的 bpno: 库原语见到元组会自己挂、命中与没命中两条路都撤。
"""
import time

from common import judge              # 观测种类常量(黑盒标 SERIAL, 白盒那几条标 DEBUG)
from common import trial              # 运行外壳: argv/日志/账本/串口/会话/收尾三连/退出码
from meterlib import cmd_bank         # 帧目录 + 语义动词积木箱(每步自带打印/判 PASS·TBD·FAIL)
from project import CURRENT           # 本工程画像: 事件编码/等待窗/.out/喂狗点(库不认表, 只有脚本认)
from swdbg import breakpoint          # 断点级白盒(停核 + 注入 + 读函数内局部量); 没接 J-Link → None
from swdbg import gdbinit             # 整片 .out 地图: 开会话后解一次, 之后断点/注入点只查它

# ---- 子项参数(纯数据; 要改的在这里改) ----
WAIT = 3.0                     # 记录读回 / 读表钟的单次等待
SAMPLE_GAP = 5.0               # ⑤ 稳态对照的采样间隔(秒): 判据恒真、状态恒 TRUE 的这 N 秒里不许新增记录
INJ_WAIT = 30.0                # 注入抬压后等「记录结束」写库位置
START_WAIT = 25.0              # 注入后等 SPI 把 g_Volt 刷回、固件自然去抖满走「记录开始」
WAIT_RPT = 45.0                # 再注入一次后等『发生』那一笔走到置上报标志, 给足余量
NUM_LOSTPOWER = 10             # 掉电记录区容量(Platform/RecdData.h:124 `#define NUM_LostPower 10u`)
N_ROUND = NUM_LOSTPOWER + 1    # 第 6 步连造的回合数 = 容量加 1(第 11 回该把最早那条顶掉)
ROUND_END_WAIT = 20.0          # 每回合等「记录结束」写库位置
ROUND_START_WAIT = 25.0        # 每回合等「记录开始」写库位置(自然到点)

# ---- 断点: 模块级**字面量元组** ---------------------------------------------------
# `scripts/_check_anchors.py` 用 `ast.literal_eval` 抠 `BP_<X>`/`VARS_<X>` 这两个名字, 先按
# `swdbg.gdbinit` 与运行时同一个换算把锚点解成源码行, 再逐条对源码核"要读的变量在那一行赋过值没有";
# 写成 `cmd_bank.LP_BP_JUDGE` 那种表达式就抠不出来, 等于没人核过(画像里那份 `LP_BP_*` 正是这么留的盲区)。
BP_JUDGE = ("line", "TaskMetering.c", 2415)   # 掉电判据行 @0x24ecc Chk_LostPower+4 —— **注入停靠点**
VARS_JUDGE = ("g_Volt[0]", "g_EventSta[EV_LostPower]", "g_EventTmr[EV_LostPower]",
              "TAB_Standard.Un", "TAB_LostPDly[0]", "TAB_LostPDly[1]")
# ⚠ 前三个是**全局量**, 在 :2415 那一停全部可读, 给的是**注入前**的读数 —— 用来证明"这一次真把值改掉了"
#   (只看 injects 的话, "注入生效"与"恰好注在原值上"长得一样)。
# ⚠ `C_60Un` **读不到** —— 它是宏 `#define C_60Un (TAB_Standard.Un * 60)`(TaskMetering.c:53), 不是符号;
#   它的两个乘数都是真的 `.out` 符号, 所以读 `TAB_Standard.Un` 再乘 60 —— 门槛值不是猜的。
BP_WR_START = ("prev", "Recd_LostPower", "Write_RecdData", 1)   # 「记录开始」写库位置 :3991 @0x26782
VARS_WR_START = ("sta", "buff")
BP_WR_END = ("prev", "Recd_LostPower", "Write_RecdData", 2)     # 「记录结束」写库位置 :4006 @0x267ce
VARS_WR_END = ("sta", "buff", "secs")
# 各停点那一刻可读的量(位置表逐区间核过):
#   `buff` 在 0x26778-0x267bc 与 0x267bc-0x267e6 两段都有位置 ⇒ :3991 与 :4006 两处都读得到;
#   `secs` 只在 0x267ce-0x267da 一段 ⇒ **只有 :4006 读得到**(:3991 读回来是"读不到")。
#   `sta` 在 $r4 的 0x26710-0x267ea ⇒ 两处都读得到, 它是"这一趟写的是哪一支"的直接目击。
#   `buff[0..5]` = 发生时刻、`buff[6..11]` = 结束时刻(秒分时日月年, 见 DLT698App.c:2447-2449)。
BP_RPTSTA = ("func", "Set_CheckAutoRptStaFlag")   # `g_CheckAutoRptSta = bFlag` @0x1b7fe(bFlag 在 $r0)
VARS_RPTSTA = ("bFlag", "g_CheckAutoRptSta")
# ⚠ 全仓 grep `Set_CheckAutoRptStaFlag` 只有 TaskMetering.c:3994 一处调用(『记录开始』支的收尾)
#   ⇒ "停到这就等于刚落的正是那一笔", 没有别的调用者冒充。
BP_GUARD_RET = ("line", "TaskMetering.c", 3972)   # 守卫第二出口(上一行已完整 而 sta 为真 ⇒ return;)
VARS_GUARD_RET = ("sta", "buff[3]", "buff[4]", "buff[9]", "buff[10]")
# ⚠ 这五个是**守卫走哪一支的全部输入**(判法照抄 :3958-3961): 发生时刻的月日(buff[3]/buff[4])有效、
#   结束时刻的月日(buff[9]/buff[10])为 0 或 0xFF ⇒ 有头无尾 ⇒ 走第一出口(:3965);
#   否则走第二出口(:3972)。读到它们就不必赌断点命中。
BP_IDXNUM = ("prev", "Write_RecdData", "Write_EEprom", 3)   # 写回索引区之前 :670(序号/次数那 5 个字节)
VARS_IDXNUM = ("id", "idx", "num", "buff")
# ⚠ 这一停的三样各答一问: `buff[0..2]` = 要写回的总次数(无封顶), `buff[3..4]` = 要写回的有效条数,
#   `num` = **实际写的字节数** —— RecdData.c:669 在有封顶时把它改成 3(只写总次数那 3 个字节),
#   所以"条数封顶了没有"看的是 `num` 是不是 3, 不是看 buff 里的值(那个是封顶**之前**算出来的)。


def _add(J, label, ok, why, crit, obs=judge.SERIAL, falsify=None, trig=None):
    """记一条判据 + 打一行三态(`falsify` 逐条给 —— 同一个 crit 的两个通道答的假条件不同)。"""
    J.add(label, ok, why, crit=crit, obs=obs, falsify=falsify, trig=trig)
    print("   [%s] %s —— %s" % ({True: "PASS", False: "FAIL", None: "TBD"}[ok], label, why))


def _stop_unproven(ctx, why):
    """半途中止的统一收场: 条目一次记全(`ok=None`) → 出声 → 停住(退 2, 不是失败)。"""
    ctx.J.extend(cmd_bank.unproven_records(ENTRIES_ALL, why))
    ctx.J.note("5-3 掉电段: 半途中止(这一次没做成) —— %s" % why)
    raise trial.Stop("%s ⇒ 未定论(退 2), 不是失败" % why)


# ---- 半途中止时要一次记全的条目(纯数据; 与正常路径同名同号**同观测**, 少记一条 = 分母变小)----
ENTRIES_ALL = (
    ("①", "判据成立且去抖满时落『发生』一笔(:3991 记录开始支)", judge.DEBUG),
    ("①", "掉电序号推进(新落『发生』行)且新行未结束", judge.SERIAL),
    ("②", "判据翻假且去抖满时落『恢复』一笔(:4006 记录结束支)", judge.DEBUG),
    ("②", "上一行的结束时刻被补上 = 落了『恢复』一笔(未新开行)", judge.SERIAL),
    ("③", "新『发生』行的发生时刻落在本次窗口(=当时表钟)", judge.SERIAL),
    ("④", "『发生』一笔走到置上报标志, 且 bFlag == TRUE", judge.DEBUG),
    ("⑤", "稳态 Ns: 掉电记录未新增(去抖计时复位 ⇒ 不误记)", judge.SERIAL),
    ("⑥", "上一行已完整却仍报『要发生』时由守卫挡回(:3972 出口), 不重开一行", judge.DEBUG),
    ("⑥", "守卫挡回之后记录条数与序号都不动", judge.SERIAL),
    ("⑦", "记录里『发生时刻』与『结束时刻』两列都真落下去", judge.SERIAL),
    ("⑧", "连造 N_ROUND 回: 索引区总次数加 N_ROUND(不封顶), 有效条数封顶", judge.DEBUG),
)


def _stop_at(sess, bp, timeout, vars_, label, crit, falsify):
    """在 `bp` 上停一次并读 `vars_` —— 给纯读的那些白盒停点用(不注入)。

    ⚠ `ctx.bp()` 现挂现等, `wait_hit(drop=True)` 一命中就撤; 没命中也不留在槽里。
    """
    return breakpoint.wait_hit(sess, bp, timeout, vars=vars_, label=label,
                               crit=crit, falsify=falsify)


def part_lostpower(ctx):
    """一段 = 5-3 的全部条目。

    下面每一行的编号是 5-3 那六步的步号; 段内顺序按**可达性**排,
    不按步号排 —— 守卫的第二个出口只在"最新一行已完整"那一刻可达, 而那一刻只存在于某次恢复
    与下一次发生之间, 所以第 4 步排在最后(那时先补一次恢复把最新行补完整)。

    ⚠ 观测一律 attach(**禁用 launch**: launch 会复位表、RAM 态清零); 见 CLAUDE.md 调试链纪律 1。
    """
    J, ser = ctx.J, ctx.ser
    print("\n===== 5-3 掉电事件: 判据行常量 → 恢复 → 发生 → 698 对账 → 连造 N 回 → 守卫分岔 =====")

    # ---- init: 开调试会话 + 解 .out 地图 + 进厂内 ----
    ctx.session()
    if ctx.g is not None:
        gdbinit.build(ctx.g)              # 整片 .out 解一次(停核遍历, 内部喂狗, 收尾放行)
    g = ctx.g
    have_wb = g is not None
    if not have_wb:
        if ctx.waived:
            print("   -- 本次**用户指定**只做黑盒(串口观测) ⇒ 断点观测(白盒)按指定不做")
        else:
            print("   !! 本次无调试会话 → 断点观测(白盒)整体**未做**; 本次范围 = 仅黑盒"
                  "(黑盒只能答『记录有没有推进、时标对不对』, 答不了『走的是哪一支写库路径、"
                  "守卫从哪个出口返回、上报标志置没置』 —— 见 CLAUDE.md「凭什么算「真实测试」·两种观测」)")
    # ⚠ 进厂内是前置: **读记录同样受 Chk_SafeMode 管**(厂外每条记录读回都被 DAR=20 打回, 而现象
    #   与『表里没记录』一模一样: 帧合法、应答照回, 负载里却是 00 14 而非记录)。
    cmd_bank.enter_factory(ser)

    # ---- 第 1 步: 判据行的三个常量 + 判据的另一半 Is_PowerOff() ----
    # `Is_PowerOff()` 是宏 `(SET != SVD_ISR_SVDO_Chk())`(CpuCfg.h:38), 走不了断点也读不了符号 ——
    # 它读的是寄存器, 所以走串口那条只读通路(AA80 区2)。它的值是**台面事实**(这台表此刻算有电
    # 还是没电), 好固件坏固件都读得出, 落不进判据表(CLAUDE.md 第 27 条) —— 只作证据行。
    # ⚠ `read_svd_isr` 自己打一行 `== SVD->ISR = 0x… ⇒ Is_PowerOff()=…` —— 不许再拼第二行
    #   (同一件事两条通道, 读日志的人得自己判哪条作数)。它读到的值本脚本不参与判定。
    cmd_bank.read_svd_isr(ser, wait=WAIT)
    v_judge = None
    if have_wb:
        _bp = ctx.bp(BP_JUDGE)
        v_judge = breakpoint.wait_hit(g, _bp, 15.0, vars=VARS_JUDGE,
                                      label="断[C] 判据行 %s 读门槛与去抖参数" % breakpoint.text(BP_JUDGE),
                                      crit=None,
                                      falsify="判据行不每秒走到 ⇒ 停不到 %s" % breakpoint.text(BP_JUDGE))
    _jv = (v_judge or {}).get("vars") or {}
    _un = cmd_bank.gdb_ints(_jv.get("TAB_Standard.Un"))
    print("   第 1 步 判据行现场: g_Volt[0]=%s g_EventSta=%s g_EventTmr=%s | "
          "TAB_Standard.Un=%s(C_60Un = Un*60 = %s mV) TAB_LostPDly=%s/%s"
          % (_jv.get("g_Volt[0]"), _jv.get("g_EventSta[EV_LostPower]"),
             _jv.get("g_EventTmr[EV_LostPower]"), _jv.get("TAB_Standard.Un"),
             ("%d" % (_un[0] * 60)) if _un else "读不到",
             _jv.get("TAB_LostPDly[0]"), _jv.get("TAB_LostPDly[1]")))
    # ⚠ 门槛与去抖参数**不进判据表**: 它们答的是"本台此刻处在什么条件", 不是"固件合不合规"。
    #   规范 5-3 第 4 条要的"发生与结束判定延时均为 0"与本台 TAB_LostPDly=(4,1) 的出入,
    #   记在下面这一行证据里, 由读日志的人对着规范判。

    # ---- 基线: 表钟 + 掉电记录区最新一条 ----
    # ⚠ **空记录区是合规的起点, 不是故障** —— `resolve_event_baseline` 对空区给 seq0=0。
    t0 = cmd_bank.read_clock(ser, chip="管理芯", wait=WAIT, quiet=True)
    rows0 = cmd_bank.read_lostpower_rows(ser, (1, 2, 3), wait=WAIT)
    if not t0:
        _stop_unproven(ctx, "表钟读不出 ⇒ 基线无对照")
    seq0, why0 = cmd_bank.resolve_event_baseline(ser, rows0, CURRENT.LP_EV_CODE, "掉电",
                                                 chip=None, wait=WAIT)
    if why0 is not None:
        _stop_unproven(ctx, why0)
    by_seq0 = cmd_bank.lp_rows_by_seq(rows0)
    b0 = rows0.get(1)
    if b0 is None:
        print("   基线: 表钟=%s | 掉电记录区**为空**(0 条)⇒ 干净起点(seq0=0, 真记录序号从 1 起)" % t0)
    else:
        print("   基线: 表钟=%s | 掉电 最新一条 序号=%s 发生=%s 结束=%s"
              % (t0, seq0, b0["t_start"], b0["t_end"] or "(未结束)"))

    # ---- 第 5 步(稳态臂): ⑤ 稳态不误记 ----
    time.sleep(SAMPLE_GAP)
    rows1 = cmd_bank.read_lostpower_rows(ser, (1, 2, 3), wait=WAIT, quiet=True)
    r1 = rows1.get(1)
    _add(J, "稳态 %.0fs: 掉电记录未新增(去抖计时复位 ⇒ 不误记)" % SAMPLE_GAP,
         (r1 or {}).get("seq") == seq0 if r1 is not None else None,
         "序号 %s → %s%s" % (seq0, (r1 or {}).get("seq"),
                             "" if r1 is not None else "(本轮读不回 ⇒ 没做成)"),
         crit="⑤",
         falsify="去抖计时不被 :2442 复位(稳态也一路累加) ⇒ 每个 delay 秒会乱落一条, 序号持续推进")

    # ---- 第 2 步: 注入抬压 ⇒ 「恢复」(记录结束支 :4006) ----
    # ⚠ 本台 g_Volt[0]≈124.0V < C_60Un(132.0V) ⇒ 判据每秒恒真、事件永远锁在「发生」, 自然恢复路径
    #   一次都走不到; 帧通道也造不出(交流源在台面外)。故这一笔**只能**走注入。
    t_inj1 = cmd_bank.read_clock(ser, chip="管理芯", wait=WAIT, quiet=True) or t0
    r_end = None
    if have_wb:
        r_end = breakpoint.inject_hit(
            g, BP_JUDGE, cmd_bank.LP_INJECT_ASSIGNS,
            watch=BP_WR_END, watch_vars=VARS_WR_END, at_vars=VARS_JUDGE, timeout=INJ_WAIT,
            label="断[C] 注入抬压 ⇒ 停在「记录结束」写库位置(%s)" % breakpoint.text(BP_WR_END),
            crit="②",
            falsify="固件没有『记录结束』这条路径, 或退出延时不是 TAB_LostPDly[1]=1(去抖不满)"
                    " ⇒ 注入抬压后走不到 %s" % breakpoint.text(BP_WR_END))
        if r_end is not None:
            print("      停时读到: %s" % (r_end.get("vars") or {}))
            for _ln in (r_end.get("injects") or []):
                print("      注入账: %s" % _ln)
            print("      注入前那一停读到: %s" % (r_end.get("at_vals") or {}))
    t_inj1b = cmd_bank.read_clock(ser, chip="管理芯", wait=WAIT, quiet=True)
    # ⚠ `inject_hit` 交回来的是 **judge 记录**, 它只有 `name/ok/detail/crit/obs/falsify/trig`
    #   —— **没有 `hit`**。判"命中没有"只认 `ok` 三态: True 命中 / None 没做成。
    _add(J, "② 注入抬压 ⇒ 落到「记录结束」写库位置(TaskMetering.c:4006)",
         None if r_end is None else r_end.get("ok"),
         (r_end or {}).get("detail") or ("没有调试会话(或用户指定只做黑盒)⇒ 注入观测这一次没做成"
                                         "(注入没有可降级的黑盒替身)"),
         crit="②", obs=judge.DEBUG, trig=None if r_end is None else judge.TRIG_INJECT,
         falsify="固件不走『记录结束』支 ⇒ 不会停在 %s" % breakpoint.text(BP_WR_END))

    # ---- 第 3 步: 自然掉压 ⇒ 「发生」(记录开始支 :3991) ----
    # ⚠ `ctx.bp()` 现挂现等: 这一次**自己会撤**(`wait_hit(drop=True)`), 不留残留。
    r_start = None
    if have_wb:
        _bp = ctx.bp(BP_WR_START)
        r_start = breakpoint.wait_hit(g, _bp, START_WAIT, vars=VARS_WR_START,
                                      label="断[C] SPI 刷回低压后自然去抖满 ⇒ 停在「记录开始」写库位置(%s)"
                                            % breakpoint.text(BP_WR_START),
                                      crit="①",
                                      falsify="判据不复真 / 进延时不是 TAB_LostPDly[0]=4"
                                              " ⇒ 自然那一笔走不到 %s" % breakpoint.text(BP_WR_START))
        if r_start is not None:
            print("      停时读到: %s" % (r_start.get("vars") or {}))
    # `buff` 是 `INT8U` 数组 —— gdb 按字符串字面量印, 故走 `gdb_bytes`(拿 `gdb_ints` 抠会把八进制
    # 当十进制、把可打印字符整个丢掉)。这一停的五行各答一问(偏移见 DLT698App.c:2445-2454):
    #   buff[0..5]  发生时刻(本支刚由 :3984 Get_MeterTime 写进去)
    #   buff[6..11] 结束时刻 —— **本支刚在 :3985 清零**, 不是 0 就说明那一句没执行(而新行会被
    #               上一次的结束时刻污染, 守卫从此把这一行当"有头有尾")
    #   buff[12..21] 发生时刻电能快照(:3986-3987 读的, md 第 3 步点名要看的就是这一段)
    _bstart = cmd_bank.gdb_bytes((r_start or {}).get("vars", {}).get("buff"))
    _end6 = _bstart[6:12] if _bstart and len(_bstart) >= 12 else None
    print("      原始行: 发生时刻 buff[0..5]=%s | 结束时刻 buff[6..11]=%s | 电能 buff[12..21]=%s"
          % (_bstart[0:6] if _bstart else "读不到", _end6 if _end6 else "读不到",
             _bstart[12:22] if (_bstart and len(_bstart) >= 22) else "读不到"))
    t_after = cmd_bank.read_clock(ser, chip="管理芯", wait=WAIT, quiet=True)
    rows2 = cmd_bank.read_lostpower_rows(ser, (1, 2, 3), wait=WAIT)
    by_seq2 = cmd_bank.lp_rows_by_seq(rows2)
    new_seqs = sorted(s for s in by_seq2 if s > seq0)
    _add(J, "① 自然掉压 ⇒ 落到「记录开始」写库位置(TaskMetering.c:3991)",
         None if r_start is None else
         (r_start.get("ok") if (r_start.get("ok") is not True or _end6 is None)
          else all(b == 0 for b in _end6)),
         "%s; 结束时刻 buff[6..11]=%s(本支应在 :3985 清零)"
         % ((r_start or {}).get("detail") or "没有调试会话(或用户指定只做黑盒)⇒ 白盒这一次没做成",
            _end6 if _end6 else "读不到"),
         crit="①", obs=judge.DEBUG,
         falsify="判据不复真或去抖累加门限不对 ⇒ 不会停在 %s; 停到了而 buff[6..11] 不为 0 "
                 "⇒ :3985 的清零没执行, 新行带着上一行的结束时刻落库"
                 % breakpoint.text(BP_WR_START))

    # ---- 第 3 步(串口臂): 序号推进 + 新行未结束 ----
    _add(J, "① 串口 掉电序号推进(新落「发生」行)且新行未结束",
         None if not by_seq2 else bool(new_seqs) and by_seq2[new_seqs[-1]]["t_end"] is None,
         "序号 %s → %s(新行 %s; 新行结束时刻=%s)"
         % (seq0, (rows2.get(1) or {}).get("seq"), new_seqs or "无",
            (by_seq2[new_seqs[-1]]["t_end"] if new_seqs else "-") or "(未结束)"),
         crit="①",
         falsify="固件不在去抖满后落『发生』(或复真后那段路径不通) ⇒ 序号不推进 / 新行结束时刻不为空")

    # ---- 第 3 步(时标臂): 新「发生」行的发生时刻落在本次窗口 ----
    _t_new = by_seq2[new_seqs[-1]]["t_start"] if new_seqs else None
    _add(J, "③ 串口 新「发生」行的发生时刻落在本次窗口(=当时表钟)",
         cmd_bank.lp_in_window(_t_new, t0, cmd_bank.clock_add(t_after or t0, 15)),
         "发生时刻=%s; 窗口=[%s, %s+15s]" % (_t_new, t0, t_after or t0),
         crit="③",
         falsify="时标取的不是当时表钟(Get_MeterTime 写错偏移/用了旧时间) ⇒ 落窗外或比窗口早")

    # ---- 第 2 步(串口臂): 旧行的结束时刻被补上(恢复不新开行, 只补尾) ----
    # ⚠ 三处兜底: 本次新读的 → 基线那份 → **基线本来就没有行**(空区, seq0=0)时给空 dict。
    _row_old = by_seq2.get(seq0) or by_seq0.get(seq0) or rows0.get(1) or {}
    _t_end1 = _row_old.get("t_end") if isinstance(_row_old, dict) else None
    _add(J, "② 串口 基线行(序号=%s%s)的结束时刻被补上 = 落了「恢复」一笔(未新开行)"
         % (seq0, ", 基线时记录区为空" if not by_seq0 else ""),
         _t_end1 is not None
         and cmd_bank.lp_in_window(_t_end1, t0, cmd_bank.clock_add(t_inj1b or t_inj1, 10)) is not False,
         "结束时刻=%s; 注入窗口=[%s, %s+10s](基线时它是%s)"
         % (_t_end1 or "(未结束)", t_inj1, t_inj1b or t_inj1,
            (by_seq0.get(seq0) or {}).get("t_end") or "未结束"),
         crit="②",
         falsify="固件不把结束时刻补进上一行(或另开一行) ⇒ 基线行结束时刻仍为 0 / 序号多推进一次")

    # ---- 第 3 步(尾): 再注入一次 ⇒ 停在置上报标志(唯一调用点 :3994) ----
    r_rpt = None
    if have_wb:
        r_rpt = breakpoint.inject_hit(
            g, BP_JUDGE, cmd_bank.LP_INJECT_ASSIGNS,
            watch=BP_RPTSTA, watch_vars=VARS_RPTSTA, at_vars=VARS_JUDGE, timeout=WAIT_RPT,
            label="断[C] 再注入抬压 ⇒ 等「发生」那一笔走到置上报标志 %s" % breakpoint.text(BP_RPTSTA),
            crit="④",
            falsify="Set_CheckAutoRptStaFlag 没被 :3994 调到 / 形参不是 TRUE ⇒ 不会停到 "
                    "%s 或 bFlag != TRUE" % breakpoint.text(BP_RPTSTA))
        if r_rpt is not None:
            print("      停时读到: %s" % (r_rpt.get("vars") or {}))
    # ⚠ 这一条**不是**把注入记录原样递进账本: 判据要的是"停在 :2229 **且** bFlag == TRUE" ——
    #   bFlag 是调用方传进来的实参(在 $r0), 得按枚举名/十进制逐一认(见 CLAUDE.md 调试链纪律 4)。
    _bv = (r_rpt or {}).get("vars", {}).get("bFlag")
    _tok = cmd_bank.gdb_sym(_bv) or ""
    _iv = cmd_bank.gdb_ints(_bv)
    _add(J, "④ 『发生』一笔走到置上报标志, 且 bFlag == TRUE",
         (None if r_rpt is None else
          r_rpt.get("ok") if r_rpt.get("ok") is not True
          else (_tok in CURRENT.LP_TRUE_TOKENS or _iv == [170] or _iv == [1])),
         (r_rpt or {}).get("detail") or "没有调试会话(或用户指定只做黑盒)⇒ 白盒这一次没做成"
         if r_rpt is None else
         "%s; bFlag=%s(枚举名=%s); 停时 g_CheckAutoRptSta=%s"
         "(那一停本行赋值尚未执行, 读到的是**上一笔或上报任务**留下的值 ⇒ 只作参考, "
         "判据只认 bFlag = 调用方传进来的实参)"
         % (r_rpt.get("detail") or "没命中 —— 未证", _bv, _tok or "-",
            (r_rpt.get("vars") or {}).get("g_CheckAutoRptSta")),
         crit="④", obs=judge.DEBUG,
         trig=None if r_rpt is None else judge.TRIG_INJECT,
         falsify="固件不置这个标志(或置 FALSE) ⇒ 不会停到 %s / bFlag 读出来不是 TRUE" % breakpoint.text(BP_RPTSTA))

    # ---- 第 5 步: 698 读回对账(两列都落下去 = ⑦) ----
    # ⚠ 规格 5-3 第 5 步把"序号、次数、累计时长、记录数据"写成四次 698 读。固件这边对不上:
    #   `TAB_LostPower`(DLT698App.c:2445-2454)登记的就是**七列**(见 `LP_COLS7`), 里面**没有**
    #   次数列也没有累计时长列; 那两样住在记录口的**索引区**(`TAB_Recd[id].iAddr` 起 5 字节:
    #   头 3 字节总次数、后 2 字节有效条数; 再往后 4 字节总累计时间, 见 Platform/RecdData.c:141-166
    #   与 :461-475)。所以"次数/累计时长"这一臂做成**索引区白盒读**(第 6 步), 698 侧只读它
    #   真有的那些列; 而规范要的"记录数据"落在**两列都得有值**上(⑦), 不是"登记表里有这两列"。
    ud7 = cmd_bank.read_event_ud(ser, CURRENT.LP_EV_CODE, pos=1, rcsd=cmd_bank.rcsd(*CURRENT.LP_COLS7),
                                 wait=WAIT)
    cols = cmd_bank.event_cols(ud7) if ud7 else None
    # 列集合只是**参考**: 它比的是"读回的列 == 固件自己登记的那张表"(`TAB_LostPower`),
    # 而那张表也是这条读回路自己查的 —— 两边同源, 固件把登记表写错它照样绿 ⇒ 不进判据表。
    _add(J, "参考: 掉电事件对象读回的列集合 == TAB_LostPower 登记的七列",
         None if cols is None else (set(cols) == set(int(x, 16) for x in CURRENT.LP_COLS7)),
         "要求七列 %s; 读回 %s"
         % ("/".join(CURRENT.LP_COLS7),
            ("/".join("%08X" % c for c in cols) if cols is not None
             else cmd_bank._rec_none_reason(ud7, "掉电"))),
         crit=None,
         falsify="对象登记的列与固件写库的布局对不上(某一列没登记/类型不符) ⇒ 那一列解不出来, "
                 "读回的列集合就少一项")

    # ⑦ 规范 5-3 第 1 条要的是**两列都落下去**, 不是"登记表里有这两列":
    #   同一行上发生时刻(行首 6 字节)与结束时刻(+6 偏移那 6 字节)都得有值 —— 只写发生时刻、
    #   结束时刻留 D_NULL 的固件, 列集合照样对得上。
    _o7 = by_seq2.get(max(by_seq2)) if by_seq2 else None
    _ts7, _te7 = (_o7 or {}).get("t_start"), (_o7 or {}).get("t_end")
    _add(J, "⑦ 记录里『发生时刻』与『结束时刻』两列都真落下去: 698 读回的行首 6 字节非空, "
            "且『恢复』那一笔落下之后 +6 偏移那 6 字节也非空",
         None if _o7 is None else (bool(_ts7) and bool(_te7)),
         "最新一条(序号=%s) 发生时刻=%s 结束时刻=%s"
         % ((_o7 or {}).get("seq"), _ts7 or "(空)", _te7 or "(空)"),
         crit="⑦",
         falsify="某一列没落下去(只写发生时刻不补结束时刻, 或两列写到同一处偏移) ⇒ "
                 "那一格读回来是空(D_NULL)")

    # ---- 第 6 步: 连造 N_ROUND 回, 看有效条数封顶而总次数不封顶 ----
    # 每一回合 = 一次恢复(注入)加一次发生(自然到点): 「发生」才加序号与次数(RecdData.c:659-673),
    # 「恢复」只补尾段并累加累计时间(:675-691) ⇒ 回合数与总次数增量一一对应。
    rows_before = cmd_bank.read_lostpower_rows(ser, (1, 2), wait=WAIT, quiet=True)
    idx_before, num_write_before = _read_index(ctx, g, have_wb)
    n_round_ok = 0
    idx_last, num_write_last = idx_before, num_write_before
    if have_wb:
        for k in range(1, N_ROUND + 1):
            print("\n   [第 6 步 第 %d/%d 回] 注入抬压 ⇒ 恢复 ..." % (k, N_ROUND))
            r1k = breakpoint.inject_hit(
                g, BP_JUDGE, cmd_bank.LP_INJECT_ASSIGNS,
                watch=BP_WR_END, watch_vars=VARS_WR_END, at_vars=VARS_JUDGE, timeout=ROUND_END_WAIT,
                label="断[C] 第 %d 回 恢复(停在 %s)" % (k, breakpoint.text(BP_WR_END)),
                crit=None,
                falsify="恢复那一笔不落库 ⇒ 等不到 %s; 后面每一回都随之失效"
                        % breakpoint.text(BP_WR_END))
            if (r1k or {}).get("ok") is not True:
                print("   [第 6 步] 第 %d 回的恢复没落库 ⇒ 连造停在第 %d 回" % (k, k))
                break
            _bp = ctx.bp(BP_WR_START)
            r2k = breakpoint.wait_hit(g, _bp, ROUND_START_WAIT,
                                      label="断[C] 第 %d 回 发生(停在 %s)"
                                            % (k, breakpoint.text(BP_WR_START)),
                                      crit=None,
                                      falsify="判据不复真 ⇒ 等不到 %s" % breakpoint.text(BP_WR_START))
            if (r2k or {}).get("ok") is not True:
                print("   [第 6 步] 第 %d 回的发生没落库 ⇒ 连造停在第 %d 回" % (k, k))
                break
            n_round_ok += 1
        idx_last, num_write_last = _read_index(ctx, g, True)
    rows_after = cmd_bank.read_lostpower_rows(ser, (1, 2), wait=WAIT, quiet=True)
    n_area = cmd_bank.event_area_count(ser, CURRENT.LP_EV_CODE, wait=WAIT)

    # 索引区那一条: 总次数加 N_ROUND, 有效条数封顶在容量(写的字节数掉到 3 = 只写总次数那 3 个字节)
    _d_idx = (None if (idx_before is None or idx_last is None) else idx_last - idx_before)
    _add(J, "⑧ 连造 %d 回: 索引区总次数加 %d(不封顶), 有效条数封顶在容量 %d 上"
         % (N_ROUND, N_ROUND, NUM_LOSTPOWER),
         None if (_d_idx is None or num_write_last is None) else
         (_d_idx == N_ROUND and num_write_last == 3),
         "停住时读: 连造前 总次数=%s(本次写回 %s 字节) → 连造后 总次数=%s(本次写回 %s 字节); "
         "实跑成 %d/%d 回; 698 读回记录区条数=%s"
         % (idx_before, num_write_before, idx_last, num_write_last, n_round_ok, N_ROUND, n_area),
         crit="⑧", obs=judge.DEBUG,
         falsify="总次数封顶 ⇒ 连造 %d 回后它不再等于 %d; 有效条数不封顶 ⇒ 写回索引区的字节数"
                 "一直是 5, 记录区会越写越多" % (N_ROUND, N_ROUND))

    # ---- 第 4 步: 守卫第二个出口(上一行已完整 而 sta 为真 ⇒ :3972 返回, 不重开一行) ----
    # ⚠ 可达性: 守卫的第二个出口要求"最新一行**已完整**", 而那一刻只存在于一次恢复与它之后那次
    #   发生之间 ⇒ 先补一次恢复(把第 6 步最后一回留下的"有头无尾"补完整), 再照同一个注入来一遍。
    #   两趟用的 `LP_INJECT_ASSIGNS` 是**同一份**: 抬压让该拍判据翻假, 同时把 g_EventSta 写成 TRUE
    #   ⇒ 守卫看到的 sta 为真。差别全在"上一行完不完整"。
    r_fix = None
    if have_wb:
        r_fix = breakpoint.inject_hit(
            g, BP_JUDGE, cmd_bank.LP_INJECT_ASSIGNS,
            watch=BP_WR_END, watch_vars=VARS_WR_END, at_vars=VARS_JUDGE, timeout=ROUND_END_WAIT,
            label="断[C] 补一次恢复(把最新行补完整) ⇒ 停在 %s" % breakpoint.text(BP_WR_END),
            crit="⑥",
            falsify="恢复不落库 ⇒ 最新一行仍是有头无尾, 守卫的第二出口就不可达")
        print("      补的那一笔: %s" % ((r_fix or {}).get("detail") or "没做成"))
    rows_g0 = cmd_bank.read_lostpower_rows(ser, (1, 2), wait=WAIT, quiet=True)
    r_guard = None
    if have_wb:
        r_guard = breakpoint.inject_hit(
            g, BP_JUDGE, cmd_bank.LP_INJECT_ASSIGNS,
            watch=BP_GUARD_RET, watch_vars=VARS_GUARD_RET, at_vars=VARS_JUDGE, timeout=INJ_WAIT,
            label="断[C] 上一行已完整却仍报『要发生』 ⇒ 守卫从 %s 返回"
                  % breakpoint.text(BP_GUARD_RET),
            crit="⑥",
            falsify="守卫链不在 ⇒ 不由 %s 挡回, 而是照开一行新的(序号推进、条数加 1)"
                    % breakpoint.text(BP_GUARD_RET))
        if r_guard is not None:
            print("      停时读到: %s" % (r_guard.get("vars") or {}))
    rows_g1 = cmd_bank.read_lostpower_rows(ser, (1, 2), wait=WAIT, quiet=True)
    _add(J, "⑥ 上一行已完整却仍报『要发生』时由守卫挡回(:3972 出口), 不重开一行",
         None if r_guard is None else r_guard.get("ok"),
         (r_guard or {}).get("detail") or "没有调试会话(或用户指定只做黑盒)⇒ 白盒这一次没做成",
         crit="⑥", obs=judge.DEBUG, trig=None if r_guard is None else judge.TRIG_INJECT,
         falsify="守卫不在 ⇒ 走不到 %s, 那一笔会照开一行新的" % breakpoint.text(BP_GUARD_RET))
    _seq_g0 = (rows_g0.get(1) or {}).get("seq")
    _seq_g1 = (rows_g1.get(1) or {}).get("seq")
    _add(J, "⑥ 串口 守卫挡回之后记录条数与序号都不动",
         None if (rows_g0.get(1) is None or rows_g1.get(1) is None) else _seq_g1 == _seq_g0,
         "挡回前最新条序号=%s, 挡回后=%s" % (_seq_g0, _seq_g1),
         crit="⑥",
         falsify="守卫没挡住 ⇒ 挡回之后多了(或少了一条)记录, 最新条序号推进")

    # ---- 收尾: 把"这一趟的形状"打成一行给人看(参考证据, 不计入任何条目) ----
    _n_last = sorted(s for s in cmd_bank.lp_rows_by_seq(rows_g1) if s > seq0)
    print("   收尾: 本次新增发生行序号=%s | 698 记录区条数=%s | 白盒连造成 %d/%d 回"
          % (_n_last or "无", n_area, n_round_ok, N_ROUND))


def _read_index(ctx, g, have_wb):
    """停一次写库点, 读回索引区**将要写出去**的总次数与写回字节数 → `(idx, 写的字节数)`。

    落点 = `Write_RecdData` 里写回索引区那条 `Write_EEprom`(:670) 之前。那里的三样:
      `buff[0..2]` = 总次数(3 字节小端, 不封顶) / `buff[3..4]` = 有效条数 /
      `num` = 本次真写几个字节 —— `RecdData.c:669` 在条数到顶时把它改成 3。
    ⚠ 判"封顶了没有"看的是 `num`, 不是 `buff[3..4]`: 后者是封顶**之前**算出来的值。
    没会话 / 没停到 → `(None, None)`。
    """
    if not have_wb:
        return None, None
    r = _stop_at(g, ctx.bp(BP_IDXNUM), 20.0, VARS_IDXNUM,
                 "断[C] 写回索引区之前读总次数/有效条数(%s)" % breakpoint.text(BP_IDXNUM),
                 crit=None,
                 falsify="记录写库不走索引区那一条 Write_EEprom ⇒ 停不到 %s"
                         % breakpoint.text(BP_IDXNUM))
    v = (r or {}).get("vars") or {}
    b = cmd_bank.gdb_bytes(v.get("buff"))
    idx = None
    if b and len(b) >= 3:
        idx = b[0] | (b[1] << 8) | (b[2] << 16)
    nbytes = cmd_bank.gdb_ints(v.get("num"))
    print("      索引区: 总次数 buff[0..2]=%s ⇒ %s; 有效条数 buff[3..4]=%s; 本次写回字节数=%s"
          % (b[:3] if b else "读不到", idx,
             ("%d" % (b[3] | (b[4] << 8))) if (b and len(b) >= 5) else "读不到",
             nbytes[0] if nbytes else "读不到"))
    return idx, (nbytes[0] if nbytes else None)


def _banner():
    return ("== 5-3 掉电事件 | 工程=%s 表号=%s ==\n"
            ".. 本台 g_Volt≈124V < C_60Un(132V) ⇒ 判据恒真,事件锁在『发生』; "
            "『恢复』走注入通道(停 %s 写 g_Volt 与 g_EventSta),『发生』自然到点(停 %s 等); "
            "上报标志停 %s, 守卫停 %s, 连造 %d 回"
            % (CURRENT.PROJECT, CURRENT.TABLE_ADDR.hex().upper(),
               breakpoint.text(BP_JUDGE), breakpoint.text(BP_WR_START),
               breakpoint.text(BP_RPTSTA), breakpoint.text(BP_GUARD_RET), N_ROUND))


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "5-3 掉电事件(去抖满后发生/恢复两笔、时标、上报标志、守卫两个出口、列集合、索引区封顶)",
        cmd_bank.lostpower_criteria,
        name="5_3_lostpower",
        parts=[("5-3 掉电段", part_lostpower)],
        gdb=breakpoint, banner=_banner(),
        # ⚠ `inject_allow` 必须在**会话构造时**点名 —— 白名单精确匹配符号名, 事后补不了。
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV,
                        inject_allow=cmd_bank.lp_inject_allow())))
