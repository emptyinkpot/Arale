# -*- coding: utf-8 -*-
"""在证什么: 过载事件的判据成立后固件走完去抖、落到 Recd_OverLoad 写库; 恢复走「记录结束」
  支把尾段补进上一行(不新开行); 去抖秒数 = clamp(参数 − C_EveFlt, ≥ C_EveDly); 时标取自当时
  表钟; 「发生」一笔的电量快照取自 g_EngyData; 连造 11 回后记录区条数封顶在 NUM_OverLoad(10) 上、
  最早那条被顶掉(规范 5-2 第 1 条「最近 10 次」那半)。
会向表写什么: 不动参数区、不发表钟帧。触发只有注入(见下面 `_banner`); 停核只在断点上, 跑完放行。
  表留下若干条过载记录(那是要证的产物), 台面停在厂内态 —— 收尾由 `scripts/_restore_all.py` 收拾。
跑法: `python project/tests/_test_5_2_overload.py`(要接 J-Link; 没接照样跑, 白盒那几条记未证)。
结论怎么读: 账本末行「判据: 满足 n/N」; 每条判据的 falsify 在 `cmd_bank.overload_criteria`。
"""
from functools import partial


from common import judge             # 观测种类 / 触发通道常量(黑盒标 SERIAL, 注入标 TRIG_INJECT)
from meterlib import cmd_bank       # 帧目录 + 语义动词积木箱(每步自带打印/判 PASS·TBD·FAIL)
from common import trial           # 运行外壳: argv/日志/账本/串口/会话/收尾三连/退出码
from project import CURRENT          # 本工程画像: 表号/.out 路径/喂狗点(库不认表, 只有脚本认)
from swdbg import breakpoint            # 断点级白盒(停核 + 读函数内局部量); 没接 J-Link → None
# 整片 .out 地图: 开会话后解一次, 之后断点/注入点只查它(见 swdbg/gdbinit.py 文件头)
from swdbg import gdbinit

# ---- 断点: 模块级**字面量元组** ----
# `scripts/_check_anchors.py` 用 `ast.literal_eval` 抠 `BP_<X>`/`VARS_<X>` 这两个名字逐条对源码
# (变量在断点那一行赋过值没有); 写成 `CB.SOMETHING` 那种表达式就抠不出来, 等于没人核过。
BP_JUDGE = ("line", "TaskMetering.c", 1882)   # 判据行 `limit = g_EventSet.OverLoadPlower;` @0x24822
# ⚠ 这一条是**纯赋值语句**、源码上没有调用结构可依, 所以留 `("line", …)` 规范形 —— 带判别字的那
#   三种锚点都要有 `bl` 才落得下来, 这一行没有。
VARS_JUDGE = ("g_SFlag", "g_PowP[0]", "g_EventSet.OverLoadPlower", "g_EventSet.OverLoadDelay",
              "g_EventSta[21]", "g_EventTmr[21]", "g_EventFlg[21]")
# ⚠ 上面七个量都是**全局量** ⇒ 在 `:1882` 那一停全部可读。它们给的是**注入前**的读数, 用来证明
#   "这一次真把值改掉了"(只看 injects 的话, "注入生效"与"恰好注在原值上"长得一样)。
#   `g_EventFlg[idx]` 是那个**去抖移位寄存器**: `:1984` 每拍左移一位、`:1985` 在 `state[i]==TRUE`
#   时置最低位、`:1993` 在低三位满时把 `state[i]` 抬成 TRUE —— 三者自锁, 一旦满就再不衰减。
#   稳态下 `g_EventSta[idx] != state[i]` 时去抖照样累加(`:2019`), 累到 delay 就走到调用点 ⇒
#   "这一次注入为什么没落地"要看它是不是满量程(见 `cmd_bank._meas_event_roundtrip` 的补完段)。
#   ⚠ **不许**把 `limit`/`i`/`sFlag` 加进来 —— `info scope Chk_OverLoad` 说那一停它们没有位置区间
#     (`i` 是空洞), 读回来是"读不到", 而那看起来像"固件没给值"。
BP_CALL = ("prev", "Chk_OverLoad", "Recd_OverLoad", 1)        # Recd_OverLoad 调用点 @0x24aa2
VARS_CALL = ("i", "delay")
BP_WRS = ("prev", "Recd_OverLoad", "Write_RecdData", 1)         # 「记录开始」写库位置 :3634 @0x25F56
VARS_WRS = ("idx", "id", "buff", "g_EngyData", "g_EventSec[21]")
BP_WRE = ("prev", "Recd_OverLoad", "Write_RecdData", 3)         # 「记录结束」写库位置 :3653 @0x26002
VARS_WRE = ("idx", "id", "buff", "g_EventSec[21]")
#   `g_EventSec[21]` = A 相过载这一路的累计秒数(`:3635`/`:3653` 处 `g_EventSec[idx]` 的那个量):
#   发生支刚在 :3635 被置 0, 恢复支在 :3653 把本次持续秒数交给写库。
BP_GUARD = ("call", "Recd_OverLoad", "Read_RecdData", 1)   # **守卫行** :3611 @0x25EDC(`Read_RecdData` 已返回、守卫还没算)
# ⚠ 这六个是**守卫走哪一支的全部输入**(判法照抄 `:3611-3614`: 头有值 且 尾 ∈{0x00,0xFF});
#   守卫走「记录开始」支(:3634)还是「记录结束」支(:3653)由它们决定 ⇒ 读到它们就不必赌断点命中。
#   `sta` **只有这一停读得到**(它在 0x25f3a 就死了, 见上面那条 ⚠)。
VARS_GUARD = ("sta", "id", "buff[3]", "buff[4]", "buff[89]", "buff[90]")
# ⚠ `buff`/`g_EngyData` 是 `INT8U` 数组 —— gdb 按**字符串字面量**印(`"\001\002…"`), 不是 `{1,2,3}`。
#   库里的 `gdb_bytes` 就是为这个单列的(拿 `_gdb_ints` 抠会把八进制当十进制、把可打印字符整个丢掉)。
# ⚠ `sta` 在 0x25f3a 就死了 ⇒ 两个写库位置的 VARS 里都不能带它。

# ---- 各段给的等待秒数(值 = 库默认, 明写在这儿是为了让读的人一眼看到这一趟的时间预算) ----
WAIT = 3.0            # 每次串口读回等应答的秒数
SAMPLE_GAP = 40.0     # 否定期望段「稳态」窗口的基准秒数; 实际窗口还会按**现读的去抖参数**抬
NAT_TIMEOUT = 150.0   # 「发生·自然去抖」等调用点的上限(要去抖几十拍)
JUMP_TIMEOUT = 60.0   # 各「跳时」段等写库口的上限
NUM_OVERLOAD = 10     # 过载记录区容量(画像 `OVL_SPEC["rec_quota"]` = NUM_OverLoad, RecdData.h:153)
N_BURST = NUM_OVERLOAD + 1   # 连造回合数 = 容量加 1(第 11 回该把最早那条顶掉)
BURST_WAIT = 20.0     # 连造每回等调用点的秒数(连造 11 回, 单回给长了整步会拖成十几分钟)


def part_overload(ctx):
    """一段 = 5-2 的全部条目:

    在证什么: 判据成立(功率过门槛)后固件走完去抖、落到 Recd_OverLoad 写库; 恢复走「记录结束」
      支把尾段补进上一行(不新开行); 去抖秒数 = clamp(参数 − C_EveFlt, ≥ C_EveDly); 时标取自当时
      表钟; 「发生」一笔的电量快照取自 g_EngyData。
    会向表写什么: 不动参数区、不发表钟帧。**触发只有注入**(g_PowP 恒 0 + 相启动判定常闭 ⇒ 帧
      通道造不出过载), 停核只在断点上, 跑完放行。表留下**两到四条**过载记录(那是要证的产物),
      台面停在厂内态 —— 收尾由 `scripts/_restore_all.py` 收拾。
    跑法: `python project/tests/_test_5_2_overload.py`(要接 J-Link; 没接照样跑, 白盒那几条记未证)。
    结论怎么读: 账本末行「判据: 满足 n/N」; 每条判据的 falsify 在 `cmd_bank.overload_criteria`。

    下面每一行是一次**库的语义动作**, 顺序承重 —— 有头无尾守卫(:3611-3627)要求
    发生 → 恢复 → 发生 → 恢复, 所以这几段的次序不许调。

    第 5 步**连造 11 回**(规范 5-2 第 1 条的「最近 10 次」那半): 每回注一次过载条件、停在调用点
    `Recd_OverLoad`(证明固件判定该记一笔), 连造前后各读一次记录区条数与第 10 条。**本固件的
    过载口写不进记录区**(尾段 `:3653` 的 off+len 越过 `TAB_Recd[ID_OverLoadA].len`, 撞上
    `Write_RecdData` 的越界守卫), 所以这一步的期望值不按固件现状给 —— 它按规范给, 跑出来是 FAIL
    就是 FAIL。⚠ 「总次数不封顶」那半(判据 ⑧)在本台读不到, 已按 `unprovable` 声明在判据表里。
    """
    # ---- 前置: 开调试会话(断点观测)。台面没接 J-Link / 用户指定只做黑盒 ⇒ None, 白盒那几条记"没做成" ----
    # ⚠ 观测一律 attach(**禁用 launch**: launch 会复位表、RAM 态清零); 见 CLAUDE.md 调试链纪律 1。
    ctx.session()
    # 整片 .out 解一次(要停核遍历, 内部按块喂狗, 收尾放行) —— 之后断点/注入点只查这张图。
    if ctx.g is not None:
        gdbinit.build(ctx.g)
    g = ctx.g
    # 递**断点元组**而不是已挂好的 bpno: 库原语见到元组都会自己挂、**命中与没命中两条路都撤**。
    # 传 bpno 则撤不撤只由调用方管, 万一没命中就留在槽里 —— 那是"核被自己撂停、其后串口全哑"的来源。
    # `hit`(`wait_hit`)只在 **pre-flight** 用: 先停一次 BP_JUDGE 读参数区门槛, 好知道
    # `:1883 if (limit != 0)` 这个判定在本台是开着还是关着 —— 关着的话, 靠注入造触发条件的那几条
    # 否定期望段会"如期"报"没走到"(`inject_miss` 的 ok=True), 那是**假通过**。库据此改记"本台证不了"。
    wb = {"inj": partial(breakpoint.inject_hit, g),
          "neq": partial(breakpoint.expect_no_hit, g),
          "hit": partial(breakpoint.wait_hit, g),
          # ⚠ `hold`(逐拍重注)不是可选项: 「发生」那一趟的判据条件每周期被数据通路刷回原值,
          #   单次注入只成立一拍 ⇒ 回退到 `inj` 只会得到"没走到判据断点"。
          #   不给它, 库会打印一行警告并照旧回退 —— 那是兜底, 不是等价物。
          "hold": partial(breakpoint.inject_hold, g)} if g is not None else None
    # ⚠ 注入表达式表(`OVL_INJ_ON/OFF/_FAST/_HEAL`)**不往这里传** —— 它们住库里, 且
    #   `CB.ovl_inject_allow()`(下面 `session_kw` 那句)就是从它们推出来的白名单。
    #   再递一份进来 = 同一件事有两个事实源, 而两者一旦分叉, 白名单会放行一个表里没有的表达式,
    #   `Session.inject()` 当场抛 `GdbError`(精确串匹配), 现象却是"注入没做"。
    run = cmd_bank.EvtRun(
        ctx.ser, cmd_bank.OVL_SPEC, wb=wb, wb_waived=ctx.waived,
        wait=WAIT, sample_gap=SAMPLE_GAP, nat_timeout=NAT_TIMEOUT, jump_timeout=JUMP_TIMEOUT,
        bp_judge=BP_JUDGE, bp_call=BP_CALL, bp_wrs=BP_WRS, bp_wre=BP_WRE, bp_guard=BP_GUARD,
        judge_vars=VARS_JUDGE, call_vars=VARS_CALL, wrs_vars=VARS_WRS, wre_vars=VARS_WRE,
        guard_vars=VARS_GUARD)
    # ---- 第 1 步: 基线读(空记录区是合规起点, 不是故障) ----
    # 基线读不出 / 无对照 ⇒ 半途中止, 那一趟的结论已经在 run.recs 里, 别再往下走。
    if not run.baseline():
        ctx.hold(run.recs, run.details, None, "5-2 过载事件段")
        return
    # ---- 第 1 步(续): 基线「有头无尾」补完 —— 停卫行读它真正的输入 ----
    run.heal()
    # ---- 第 1 步(续): 读参数区门槛 ⇒ 该支在本台启不启用 ----
    run.preflight()
    # ---- 第 2 步: 否定期望段(表驱动: spec["neg_legs"]) ----
    run.neg_legs()
    # ---- 第 2 步(续): 判据成立 → 自然去抖满 → 落在调用点 ----
    run.happen_natural()
    # ---- 第 3 步: 串口复核(条数加 1 / 最新一条结束时刻空 / 时标落窗口) ----
    run.ser_after_happen()
    # ---- 第 4 步: 判据翻假 → 落到「记录结束」写库位置 ----
    run.recover_jump()
    # ---- 第 4 步(续): 串口复核(条数不变 / 尾段补进上一行 / 序号不推进) ----
    run.ser_after_recover()
    # ---- 第 2 步(复真): 判据复真 → 落到「记录开始」写库位置(兼取电量快照) ----
    run.happen_jump()
    # ---- 第 4 步(末段): 末段恢复: 跑完记录完整、g_EventSta[idx] = FALSE ----
    run.recover_last()
    # ---- 第 5 步: 连造 11 回, 看「最近 10 次」封顶在容量上(判据 ⑦) ----
    _burst(ctx, g)
    recs, details, scope = run.finish()
    ctx.hold(recs, details, scope, "5-2 过载事件段")


def _burst(ctx, g):
    """第 5 步: 连造 N_BURST 回过载, 判据 ⑦(规范 5-2 第 1 条「最近 10 次」那半)。

    每回 = 一次注入(判据条件 + 钉 `g_EventSta[21]` 与 `g_EventTmr[21]`)停在调用点 `Recd_OverLoad`
    —— 停到它就说明固件判定这一笔该记(守卫随后才分流)。连造前后各读一次记录区条数与第
    NUM_OVERLOAD 条。

    ⚠ 期望值取自**规范**, 不取自本固件的现状: 本固件过载口的记录尾段写不进去(`:3653` 的
      off+len=172 越过 `TAB_Recd[ID_OverLoadA].len=95`, 撞上 `Write_RecdData` 的越界守卫),
      于是这 11 回每一回都被守卫挡在「记录开始」支之外 —— 条数停在连造前那一条上。
      那是**固件不满足规范**, 这一条就该判 FAIL(见 `cmd_bank.overload_criteria` 那条注)。
    """
    ser = ctx.ser
    cnt0 = cmd_bank.event_area_count(ser, cmd_bank.OVL_EV_CODE, wait=WAIT)
    seq0 = (cmd_bank.read_event_row(ser, cmd_bank.OVL_EV_CODE, NUM_OVERLOAD, wait=WAIT) or {}).get("seq")
    n_fire = 0
    for k in range(1, N_BURST + 1):
        if g is None:
            break
        r = breakpoint.inject_hold(
            g, BP_JUDGE, cmd_bank.OVL_INJ_ON_FAST, cmd_bank.EVT_LAND_TICKS,
            at_vars=VARS_JUDGE, watch=BP_CALL, watch_vars=VARS_CALL,
            watch_timeout=cmd_bank.EVT_HOLD_WATCH, budget=BURST_WAIT,
            label="断[A] 连造第 %d/%d 回: 注入 %s ⇒ 等调用点 %s"
                  % (k, N_BURST, cmd_bank.OVL_SPEC["on_txt"], breakpoint.text(BP_CALL)))
        if (r or {}).get("ok") is True:
            n_fire += 1
        else:
            print("      连造第 %d/%d 回没停到调用点 —— 这一回没做成" % (k, N_BURST))
    cnt1 = cmd_bank.event_area_count(ser, cmd_bank.OVL_EV_CODE, wait=WAIT)
    seq1 = (cmd_bank.read_event_row(ser, cmd_bank.OVL_EV_CODE, NUM_OVERLOAD, wait=WAIT) or {}).get("seq")
    # 「最早那条被顶掉」= 第 NUM_OVERLOAD 条的序号换了一个。两个序号缺一个 ⇒ 没读数(不判)。
    replaced = None if (seq0 is None or seq1 is None) else (seq1 != seq0)
    ok, why = None, None
    if g is None:
        why = "没有调试会话(或用户指定只做黑盒)⇒ 连造这一趟没做成(过载条件只有注入造得出)"
    elif n_fire == 0 or cnt0 is None or cnt1 is None:
        why = ("连造 %d 回一次都没停到调用点 ⇒ 本次没证成(记录区条数 连造前=%s 连造后=%s)"
               % (N_BURST, cnt0, cnt1))
    else:
        ok = (n_fire == N_BURST and cnt1 == NUM_OVERLOAD and replaced is True)
        why = ("连造 %d 回(停到调用点 %d 回): 记录区条数 %s → %s(规范要停在 %d); "
               "第 %d 条(最早那条)序号 %s → %s(顶掉了吗=%s)"
               % (N_BURST, n_fire, cnt0, cnt1, NUM_OVERLOAD, NUM_OVERLOAD,
                  seq0, seq1, "没读数" if replaced is None else ("是" if replaced else "否")))
    ctx.J.add("⑦ 连造 %d 回后最近 %d 次封顶在容量上、最早那条被顶掉(规范 5-2 第 1 条)"
              % (N_BURST, NUM_OVERLOAD), ok, why, crit="⑦", obs=judge.SERIAL,
              trig=None if g is None else judge.TRIG_INJECT,
              falsify="容量不是 %d(固件只留 9 条或 11 条)、或最早那条没被顶掉 ⇒ "
                      "连造后读回的条数与第 %d 条的序号对不上" % (NUM_OVERLOAD, NUM_OVERLOAD))
    print("   [%s] ⑦ 连造 %d 回 —— %s" % ({True: "PASS", False: "FAIL", None: "TBD"}[ok], N_BURST, why))


def _banner():
    return ("== 5-2 事件记录·过载 | 工程=%s 表号=%s ==\n"
            ".. 触发=**注入**(g_PowP 恒 0 + 相启动判定常闭 ⇒ 帧通道造不出过载); "
            "停 %s, **发生那一趟逐拍重注** %d 样(PowP 越限 + 清 g_SFlag 的 A 相位)"
            "@%d 拍上限, 参数区一个字节都不动; "
            "白盒停 %s(调用点/读 delay)/%s(「记录开始」写库位置)/%s(「记录结束」写库位置)"
            % (CURRENT.PROJECT, CURRENT.TABLE_ADDR.hex().upper(),
               breakpoint.text(BP_JUDGE), len(cmd_bank.OVL_INJ_ON), cmd_bank.EVT_HOLD_TICKS,
               breakpoint.text(BP_CALL), breakpoint.text(BP_WRS), breakpoint.text(BP_WRE)))


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "5-2 事件记录·过载(发生走「记录开始」支/恢复补进上一行/去抖秒数/时标/电量快照取自 g_EngyData)",
        cmd_bank.overload_criteria,
        name="5_2_overload",
        parts=[("5-2 过载事件段", part_overload)],
        gdb=breakpoint, banner=_banner(),
        # ⚠ `inject_allow` 必须在**会话构造时**点名 —— 白名单精确匹配符号名, 事后补不了。
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV,
                        inject_allow=cmd_bank.ovl_inject_allow())))
