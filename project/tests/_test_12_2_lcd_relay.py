# -*- coding: utf-8 -*-
"""
_test_12_2_lcd_relay.py —— 12-2「保电功能·液晶是否显示拉闸」测试脚本(串口观测 + 断点观测)

规格:
  功能=保电功能 / 子项=液晶显示拉闸 / 可达性=可测
  观察判据: 五态阶梯逐级到位; 非合闸态在 :3872 画“拉闸”(偏移 = Status_Zone_Offset[6]); 合闸态不画;
            影子缓冲拉闸区随之置/清; 对照区无条件画; 合闸允许态走到 :1461 的 `return b_Blink`

━━ 判据链在哪 ━━
  条目清单 = `CB.lcd_relay_criteria()` ①~⑦
  正文 = 本文件 `part_lcd(ctx)`: 一行一个动作, 顺序即步骤(进厂内 → 显示态前置 → 归一现态 →
         五态阶梯逐级【发操作字 → 断[A] 看画没画 → 断[B] 看灯闪没闪 → 读影子缓冲窗口】→ 汇总判定),
         每发一帧之后紧跟它那几条判定 —— 判据文字 / crit / falsify 都写在这里。
  读内存那几步写在本脚本里(裸 `W.watch_vars` / `W.aa80_ram_snapshots`), 库只给**纯解码**与
         断点原语: `CB.lcd_ladder_decode`(发了帧、轮询过之后判到位没到位) / `CB.lcd_probe_draw` /
         `CB.lcd_probe_blink` / `CB.lcd_window_obs` 与一排 `CB.lcd_judge_*`(各比一件事)。
  汇总判定 = `common/judge.py`(唯一出口: 只对预设条目计数 → 三态)

━━ 断点(模块级字面量元组 —— `scripts/_check_anchors.py` 靠它配对, 别改成表达式)━━
  `Disp_Others+358 @0x31bfa`; 这一行前一行 :3871 才把 `Curr_Value` 填上。
  `Get_RelayTimer+74 @0x2f38a`; 这一行**没有局部量可读**, 读的是它要返回的全局 `b_Blink`。
  ⚠ 两个断点**不同时占槽**: 本核只有 4 个 FPB 槽, 且 ③/⑥ 要的是不同状态, 故逐态只下一个。

━━ 影子缓冲那两块窗口(地址一个都不写死)━━
  y=68 ⇒ 落在 page 8 与 page 9; “拉闸” n=85 ⇒ x 85..108(24B); “阶梯T1” n=109 ⇒ x 109..116(8B)。
  窗口 = (起点 85, 长 32), 前 24B = 拉闸区, 后 8B = 对照区。
  ⚠ 字 12 像素高、y=68 起, **跨 page 8/9 两页**, 只读一页会漏掉一半笔画; page 8 按整字节读会把
    y64..67 也读进来(y64 是主数字行 24×48 大字的最下一行, 随屏上数字变 ⇒ “各态逐字节相同”会被它
    顶成假 FAIL), 故 page 8 那一页过 `LCD_P0_MASK` —— 那是**把不属于该字形的像素剔出去**, 不是放宽判据。
  ⚠ 对照区是**反静默**用的: 它无条件画, 所以它全 0 = 整条显示路径没跑(或 :3877 被去掉), 而不是
    "没画拉闸"; 只看拉闸区时这两种在账本里长得一模一样。故 ④b 的『全 0』要 ⑤ 成立才作数。
  这些常量与窗口折算都在库里(`LCD_WIN` / `CB.lcd_window_blocks`), 脚本不碰地址。

━━ 前置: 显示状态机必须在这两个字的运行窗口里 ━━
  `TaskDisplay.c:3824 if (Is_StopDispStatus() == FALSE) return;` —— ⚠ 函数名与语义相反: 它返回真
  = 当前处于正常显示窗口(轮显/按显/固定/点播/插卡/金额 = 1..6); 0(上电全显)与 7(停显) 两支都直接
  return, 影子缓冲根本不更新。读不到、或不在 1..6 ⇒ 半途中止记未证, **不是固件错**。

━━ 副作用: 命令状态真变, 但负载不动 ━━
  `g_RelayCmd[0]` 落到阶梯末态(合闸 8)并写参数区 ID_RelayCmd。**驱动继电器那一步在许可判定之后**
  (`TaskRelay.c:282-284` 的 ≥75%Un), 本台 38V 闭锁 ⇒ 负载回路不动, 表自身由表内供电、通信不断。
  跑完台面留在**厂内态**(本条要 645 0x1C, 必须厂内), 交给 `scripts/_restore_all.py` 收拾。

跑法(在 帧收发基础/ 下):
    python project/tests/_test_12_2_lcd_relay.py             # 全套: 串口观测 + 断点观测
    python project/tests/_test_12_2_lcd_relay.py --no-gdb    # **用户指定**只做串口观测(不报"总")
  ⚠ 两种跑法都会真开 COM3 对真表发五个 0x1C 操作字; 有断点观测时会**真接 J-Link**。
    **本脚本没有 dry 开关** —— 要只验语法/装配用 `ast.parse`(见 CLAUDE.md 工作约定)。
退出码: 通过→0 / 失败(有 ok=False 证据)→1 / 未定论(条目未证 / 观测没做成)→2。
"""
import time
from functools import partial

from common import judge            # 观测种类常量(断点那几条证据标 DEBUG)
from common import trial            # 运行外壳: argv/日志/账本/串口/会话/收尾三连/退出码
from meterlib import cmd_bank       # 帧目录 + 语义动词积木箱(每步自带打印)
from meterlib import watch           # AA80 读内存(本脚本显式读的那几步就用它)
from project import CURRENT          # 本工程画像: 表号/.out 路径/喂狗点(库不认表, 只有脚本认)
from swdbg import breakpoint            # 断点级白盒(停核 + 读局部量); 没接 J-Link → open_or_none→None
from swdbg import gdbinit            # 整片 .out 地图: 开会话后解一次, 之后断点/注入点只查它

WAIT = 2.0                                # 每次 AA80 直读等应答的上限(秒)
SETTLE = 8.0                              # 发完一帧之后等 g_RelayCmd[0] 落进目标态的上限(秒)

# ---- 断点: 模块级**字面量元组** ---------------------------------------------------
# `scripts/_check_anchors.py` 用 `ast.literal_eval` 抠这两个名字, 逐条对源码核"要读的变量在断点
#   那一行赋过值没有"; 写成 `CB.SOMETHING` 那种表达式就抠不出来, 等于那种观测静默地没人核过。
BP_DRAW = ("TaskDisplay.c", 3872)
VARS_DRAW = ("Curr_Value",)
BP_BLINK = ("TaskDisplay.c", 1461)
VARS_BLINK = ("b_Blink",)

# ---- 五态阶梯(纯数据; 顺序即步骤)---------------------------------------------------
# 每步 = (档名, 645 0x1C 操作字, 该落进哪些现态)。`want` 给一组而不是一个数: 拉闸那一步先落
# ST_WaitOffR(10) 再倒计时翻 1, 而这两态在显示闸上结论相反 ⇒ 它要的是"落进非合闸侧这一整片"。
LADDER = (
    ("非合闸侧(拉闸后)", cmd_bank.KP_CMD_OFF,     cmd_bank.LCD_LOW_STATES),
    ("合闸允许",        cmd_bank.KP_CMD_INDIR,   (cmd_bank.KP_ST_ALLOWON,)),
    ("保电允许",        cmd_bank.KP_CMD_KEEP,    (cmd_bank.KP_ST_ALLOWONKP,)),
    ("合闸保电",        cmd_bank.KP_CMD_ON,      (cmd_bank.KP_ST_RELAYONKP,)),
    ("合闸",            cmd_bank.KP_CMD_KEEPOFF, (cmd_bank.KP_ST_RELAYON,)),
)

# ---- 本子项的 falsify(纯数据, 逐条对着 crit 用)-------------------------------------
FALSIFY = {
    "①": "645 0x1C 被受理(9C)而 g_RelayCmd[0] 不动或落到别的态 ⇒ 某一级没到位",
    "②": "非合闸态停在 %s 读到 Curr_Value != 85 ⇒ 画的是别的偏移(或 :3871 被改); "
          "压根没命中 ⇒ :3872 没被执行" % breakpoint.text(BP_DRAW),
    "③": "命令态 ≥ ST_RelayOn(8) 时 :3871/:3872 还被执行 ⇒ 那道闸 `Get_RelayCmd() < ST_RelayOn` "
          "没起作用(合闸了还在画“拉闸”)",
    "④a": "非合闸态该画“拉闸”, 却读到 48B 全 0 ⇒ :3869 那道闸判反了/画字被去掉; 或同一命令态两次"
           "读到的字节不同 ⇒ 缓冲不是每秒重画的那一份(读错了区/表在动)",
    "④b": "命令态 ≥ ST_RelayOn(8) 时拉闸区还有笔画 ⇒ `Get_RelayCmd() < ST_RelayOn` 那道闸失效, "
           "合闸了还在屏上显示“拉闸”",
    "⑤": "对照区是**无条件**画的(:3877 不在 volt 块里), 它全 0 ⇒ 整条显示路径没跑"
          "(Clear_DispBuf→Disp_Others→Refresh 断在半路)或 :3877 被去掉 —— 此时 ④b 的『全 0』不作数",
    "⑥": "合闸允许态(:1458/:1459 那一支成立)却走不到 %s 的 `return b_Blink` ⇒ Get_RelayTimer "
          "的判链被改, 拉闸灯不闪" % breakpoint.text(BP_BLINK),
}

# ---- 半途中止时要一次记全的条目(纯数据; 与正常路径同名同号**同观测**, 少记一条 = 分母变小)----
ENTRIES_ALL = (
    ("①", "五态阶梯逐级到位", judge.SERIAL),
    ("②", "非合闸态 :3872 被执行且 Curr_Value == Status_Zone_Offset[6](85)", judge.DEBUG),
    ("③", "合闸态(8/9) :3872 不执行", judge.DEBUG),
    ("④a", "非合闸态影子缓冲拉闸区非全 0 且各态逐字节相同", judge.SERIAL),
    ("④b", "合闸态影子缓冲拉闸区全 0", judge.SERIAL),
    ("⑤", "对照区每个观察态都非全 0", judge.SERIAL),
    ("⑥", "合闸允许态走到 :1461 的 `return b_Blink`", judge.DEBUG),
)


def _read_byte(ser, name, tag):
    """AA80 直读画像里某个 RAM 变量的**首字节** → int 或 None(读不到)。"""
    body = watch.watch_vars(ser, [name], tag=tag, wait=WAIT).get(name)
    return body[0] if body else None


def _trig(ser):
    """白盒那一次的**触发**动作: 再读一次 `g_RelayCmd[0]`, 给核一个动作。

    显示路径每秒走一趟, 所以命中的是下一个秒沿; `expect_no_hit` 那一路也靠它顺带叫一次
    `ensure_running()`, 免得核被上次残留撂停时"窗口内没命中"变成一次假通过。
    """
    return lambda: watch.watch_vars(ser, ["g_RelayCmd"], tag="断点触发", wait=WAIT)


def _ladder_step(ser, tag, op, want):
    """阶梯一步: 发一个 645 0x1C 操作字, 轮询到 `g_RelayCmd[0]` 落进 `want` 之一 → `(现态, ok, why)`。

    ⚠ 为什么要轮询: 拉闸那一步先落 ST_WaitOffR(10) 再倒计时翻 1, 而这两态在显示闸上结论相反
      (10 不画、1 画)。发完立刻读会读到 10, 把固件的正常行为读成"不画拉闸"。
    """
    verdict = cmd_bank.ctrl_relay(ser, op, wait=WAIT)
    t0, st = time.time(), None
    while True:
        st = _read_byte(ser, "g_RelayCmd", "%s · 轮询" % tag)
        if st is not None and st in want:
            break
        if time.time() - t0 >= SETTLE:
            break
        time.sleep(0.4)
    return cmd_bank.lcd_ladder_decode(tag, op, verdict, st, want, SETTLE)


def _read_window(ser, st, tag):
    """AA80 读液晶影子缓冲那两块窗口 → 本态的观察点(读在脚本, 地址折算与拆窗口在库)。"""
    got = watch.aa80_ram_snapshots(ser, cmd_bank.lcd_window_blocks(tag), tag="lcd_%s" % tag, wait=WAIT)
    return cmd_bank.lcd_window_obs(got, st, tag)


def _stop_unproven(ctx, entries, why):
    """半途中止的统一收场: 条目一次记全(`ok=None`) → 出声 → 停住(退 2, 不是失败)。"""
    ctx.J.extend(cmd_bank.unproven_records(entries, why, FALSIFY))
    ctx.J.note("12-2 拉闸字/拉闸灯段: 半途中止(这一次没做成) —— %s" % why)
    raise trial.Stop("%s ⇒ 未定论(退 2), 不是失败" % why)


def part_lcd(ctx):
    """一段 = 12-2 的全部条目: 进厂内 → 显示态前置 → 归一现态 → 五态阶梯逐级取证 → 汇总判定。"""
    ser = ctx.ser
    ctx.session()
    if ctx.g is not None:
        # 整片 .out 解一次(停核遍历, 内部喂狗, 收尾放行) —— 之后一律查这张图
        gdbinit.build(ctx.g)
    g = ctx.g
    # 递**断点元组**而不是已挂好的 bpno: 两个原语见到元组都自己挂、**用完必撤**(传 bpno 则撤不撤
    #   只由 `drop=` 管, 没命中就留在槽里 —— 那是"核被自己撂停、其后串口全哑"的来源)。
    fire = partial(breakpoint.fire_hit, g) if g is not None else None
    nohit = partial(breakpoint.expect_no_hit, g) if g is not None else None
    trig = _trig(ser)                                    # 白盒那两次的触发动作(读 g_RelayCmd[0])

    cmd_bank.lcd_relay_intro(fire is not None, ctx.waived)     # 开场: 本次走哪两种观测
    cmd_bank.enter_factory(ser)                                # 进厂内(645 0x1C 的密码判定 :3456)
    _disp = _read_byte(ser, "g_DispStatus", "显示状态机前置")   # 前置: 显示状态机在哪个态
    _disp, gate_ok = cmd_bank.lcd_disp_decode(_disp)           # 在不在运行窗口(1..6)
    if not gate_ok:
        _stop_unproven(ctx, ENTRIES_ALL,
                       "显示路径整条不跑(或 g_DispStatus 读不到)⇒ 影子缓冲不会更新, 本次没做成")

    st0 = _read_byte(ser, "g_RelayCmd", "起点")          # 归一: 起点在 9 时先解除保电, 否则第 1 级走不下去
    if cmd_bank.lcd_norm_needed(st0):
        _ladder_step(ser, "归一(解除保电)", cmd_bank.KP_CMD_KEEPOFF, (cmd_bank.KP_ST_RELAYON,))

    landed, missed, obs = [], [], []
    for tag, op, want in LADDER:
        st, ok, why = _ladder_step(ser, tag, op, want)           # 发一个操作字 + 轮询落态
        ctx.J.add("阶梯·%s 到位" % tag, ok, why)
        (landed if ok else missed).append(tag)
        if not ok:
            continue                                             # 这一态没到位 ⇒ 它的判据本次不做
        ctx.J.extend(cmd_bank.lcd_probe_draw(fire, nohit, trig, BP_DRAW, VARS_DRAW, st,
                                       "②", "③", FALSIFY["②"], FALSIFY["③"], SETTLE))   # 断[A]: 画没画
        ctx.J.extend(cmd_bank.lcd_probe_blink(fire, trig, BP_BLINK, VARS_BLINK, st,
                                        "⑥", FALSIFY["⑥"], SETTLE))                     # 断[B]: 灯闪没闪
        obs.append(_read_window(ser, st, tag))                                       # 影子缓冲两块窗口

    ctx.J.add("① 五态阶梯逐级到位: 645 0x1C 的 0x1A/0x1B/0x3A/0x1C/0x3B 各发一次, g_RelayCmd[0] "
              "依次落进 非合闸侧→ST_AllowOn(6)→ST_AllowOnKp(7)→ST_RelayOnKp(9)→ST_RelayOn(8)",
              *cmd_bank.lcd_judge_ladder(landed, missed), crit="①", falsify=FALSIFY["①"])
    ctx.J.add("④a 影子缓冲拉闸区(两页各前 24B, 共 48B; 页0 只取高 4 位)在**非合闸态**非全 0 且各态逐字节相同",
              *cmd_bank.lcd_judge_lz_low(obs), crit="④a", falsify=FALSIFY["④a"])
    ctx.J.add("④b 影子缓冲拉闸区在**合闸态**(8/9)该 48B **全 0**",
              *cmd_bank.lcd_judge_lz_high(obs), crit="④b", falsify=FALSIFY["④b"])
    ctx.J.add("⑤ 对照区(两页各后 8B, 共 16B, “阶梯T1”那块 8×12 —— 它无条件画)在每一个观察态都非全 0 "
              "⇒ 显示路径整条在跑, ④ 的『全 0』不是「没画」与「没跑」混在一起",
              *cmd_bank.lcd_judge_ctrl(obs), crit="⑤", falsify=FALSIFY["⑤"])
    cmd_bank.lcd_relay_end_notes(fire is not None, ctx.waived)   # 收尾: 本次够不到的那两块


def _banner():
    return ("== 12-2 液晶是否显示拉闸 | 工程=%s 表号=%s ==\n"
            ".. 触发=645 0x1C 五态阶梯(0x1A/0x1B/0x3A/0x1C/0x3B); "
            "白盒停 %s 读 Curr_Value, %s 读 b_Blink"
            % (CURRENT.PROJECT, CURRENT.TABLE_ADDR.hex().upper(),
               breakpoint.text(BP_DRAW), breakpoint.text(BP_BLINK)))


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "12-2 液晶是否显示拉闸(五态阶梯 × 影子缓冲窗口 × 断点看 :3872/:1461)",
        cmd_bank.lcd_relay_criteria,
        name="12_2_lcd_relay",
        parts=[("12-2 拉闸字/拉闸灯段", part_lcd)],
        gdb=breakpoint, banner=_banner(),
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV)))
