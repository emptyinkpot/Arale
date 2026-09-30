# -*- coding: utf-8 -*-
from functools import partial

from common import judge            # 观测种类常量(断点那一条证据标 DEBUG)
from common import trial            # 运行外壳: argv/日志/账本/串口/会话/收尾三连/退出码
from meterlib import cmd_bank       # 帧目录 + 语义动词积木箱(每步自带打印)
from meterlib import watch           # AA80 读内存(本脚本显式读的那几步就用它)
from project import CURRENT          # 本工程画像: 表号/.out 路径/喂狗点(库不认表, 只有脚本认)
from swdbg import breakpoint            # 断点级白盒(停核 + 读局部量); 没接 J-Link → open_or_none→None
from swdbg import gdbinit            # 整片 .out 地图: 开会话后解一次, 之后断点/注入点只查它

WAIT = 3.0                                # 每次 AA80 直读等应答的上限(秒)

# ---- 断点: 模块级**字面量元组** ---------------------------------------------------
# `scripts/_check_anchors.py` 用 `ast.literal_eval` 抠这两个名字, 逐条对源码核"要读的变量在断点
#   那一行赋过值没有"; 写成 `CB.SOMETHING` 那种表达式就抠不出来, 等于那种观测静默地没人核过。
#   `Set_RelayCmdR+120 @0x2d32c`; 该行是裁决表查完、按现态分派错误之前那一句,
#   同时可读 `cmd`(形参, 可读区间 0x2d314-0x2d35c)与 `newSta`(:849 算出, 0x2d32c-0x2d338)。
#   ⚠ 别把断点挪回规格原写的 `:823` —— 那一行的 PC 区间是 0x2d2bc-0x2d2c2, `newSta` 一个位置区间都没有。
#   ⚠ F 列另一个候选 `:859`(`Set_CtrlStat(ER_RlyOffKeep)`)同样用不得: `cmd` 在那一行是**空洞**
#     (0x2d35c-0x2d364), 读回来是栈垃圾。
#   ⚠ F 列另一条 `断[A]`(645 遥控入口 `DLT645App.c:3434`)本脚本不用它: 每步只发一帧 645 0x1C,
#     停在 `:851` 读到的 `cmd` 就是那一帧算出来的操作字, 已经证明该帧走完了整条入口+分发; 再在入口
#     停一次是同一个事实的第二次观测, 而 FPB 只有 4 个槽(见 CLAUDE.md「调试链」关键纪律 2)。
#   ⚠ 四步**全都会经过 `:851`** ⇒ 断点那一条必须拿**当次那一个操作字**去对, 不能只看"停住了"。
BP_STA = ("TaskRelay.c", 851)
VARS_STA = ("cmd", "newSta")

# ---- 本子项的 falsify(纯数据, 逐条对着 crit 用)-------------------------------------
FALSIFY = {
    "①命中": "操作字在这一支里没走到裁决层(帧更前面就被拒了, 或走了报警那支)"
              "⇒ 不会停在 %s" % (breakpoint.text(BP_STA)),
    "①": "固件不走 TAB_RelaySta[cmd][现态-1](裁决表抄错/下标错/操作字解错行)⇒ newSta 与表值不等",
    "⑤受理": "保电裁决出错误码 ⇒ 整条链后面的三步都无从谈",
    "⑤落位": "保电裁决算出保电位却没写进 g_RelayCmd[0](:1090 的写入口没走到/被别的支改掉)",
    "⑤落回": "解除裁决算出的新态没落进 g_RelayCmd[0] ⇒ 保电位撤不掉, 拉闸会被永久拦着",
    "⑤放行": "保电已解除而拉闸仍被拦(应答被拒、命令状态停在合闸侧)⇒ 保电位撤了、拦截没撤; "
              "或受理了却停在合闸侧 / 落进错误态 ⇒ 放行只落在应答上, 没落到命令状态",
    "②": "保电位的两个现态在裁决表第 1 行上都是 ST_Error0(:851/:855-859 该走 ER_RlyOffKeep), "
          "固件却受理了(0x9C)⇒ 保电闸失效",
    "③": "按现态分派的错误码不是 ER_RlyOffKeep(例: 落到 :881 的 default ⇒ 0x0004)"
          "⇒ 保电拦的不是这条规矩, 或裁决表那一格不是 ST_Error0",
    "④": "命令被拒而命令状态仍被改写(:851 的错误分支没拦住写入口)⇒ 表里对不上的格子会静默生效",
    "⑥": "Get_MeterRunSta 的 pStatus[1] |= 0x10 那句(:1006-1010)与 g_RelayCmd[0] 脱节"
          "⇒ 上报的保电位与实际命令状态不一致",
}

# ---- 半途中止时要一次记全的条目(纯数据; 与正常路径同名同号, 少记一条 = 分母变小)----
ENTRIES_ALL = (
    ("①", "裁决逐格对表(两个组合的 newSta 对 TAB_RelaySta)", judge.SERIAL),
    ("②", "保电期间拉闸线上被拒(0xDC + 0x04)", judge.SERIAL),
    ("③", "被拒原因是 ER_RlyOffKeep(g_CtrlStat[1] == 0x0020)", judge.SERIAL),
    ("④", "被拦时命令状态原地不动", judge.SERIAL),
    ("⑤", "保电/解除/放行的落位", judge.SERIAL),
    ("⑥", "保电位在运行状态字3 上置位、解除后清零", judge.SERIAL),
)
ENTRIES_HALF = (
    ("⑤", "保电解除/拉闸放行的落位", judge.SERIAL),
    ("⑥", "保电位在运行状态字3 上置位、解除后清零", judge.SERIAL),
)
ENTRIES_NO_KEEP = (
    ("②", "保电期间拉闸线上被拒(0xDC + 0x04)", judge.SERIAL),
    ("③", "被拒原因是 ER_RlyOffKeep(g_CtrlStat[1] == 0x0020)", judge.SERIAL),
    ("④", "被拦时命令状态原地不动", judge.SERIAL),
) + ENTRIES_HALF


def _byte0(body):
    """`W.watch_vars` 读回的字节 → **首字节** int; 没读到 → `None`(不拿 0 冒充)。"""
    return body[0] if body else None


def _read_state(ser, tag):
    """AA80 直读 `g_RelayCmd[0]` → 0..255 或 None(读不到)。"""
    v = _byte0(watch.watch_vars(ser, ["g_RelayCmd"], tag=tag, wait=WAIT).get("g_RelayCmd"))
    print("   %s: g_RelayCmd[0]=%s" % (tag, cmd_bank.kp_state_txt(v)))
    return v


def _read_ctrlstat(ser, tag):
    """AA80 直读 `g_CtrlStat` → `[1]` 那个字(2 字节小端) 或 None(读不到)。"""
    v = cmd_bank.kp_ctrlstat_word(watch.watch_vars(ser, ["g_CtrlStat"], tag=tag, wait=WAIT).get("g_CtrlStat"))
    print("   %s: g_CtrlStat[1]=%s" % (tag, "读不到" if v is None else "0x%04X" % v))
    return v


def _cmd_step(ser, fire, code, op_txt, label):
    """一步一次 645 0x1C(操作字 `code`): 读现态 → 发帧(带断点) → 读新态与控制状态字 → 装配。

    ⚠ **每一步都重读一遍现态**, 不跨步沿用上一次的新态 —— 固件自己会改 `g_RelayCmd[0]`,
      沿用上一个读数就把"这一步真落了没有"判成了上一步的结果。
    ⚠ 没会话时 `fire` 是 `None`: 这一次**照样发帧**(黑盒观测不能跟着消失)。
    """
    sta_before = _read_state(ser, "%s · 前" % op_txt)
    if fire is not None:
        hit = fire(BP_STA, lambda: cmd_bank.ctrl_relay_reply(ser, code, wait=WAIT),
                   label="%s 645 0x1C=0x%02X → 停在 %s(读 cmd/newSta)"
                         % (label or op_txt, code, breakpoint.text(BP_STA)),
                   vars=VARS_STA, crit="①", falsify=FALSIFY["①命中"])
        res = (hit or {}).get("result")
    else:
        hit = None
        res = cmd_bank.ctrl_relay_reply(ser, code, wait=WAIT)
    sta_after = _read_state(ser, "%s · 后" % op_txt)
    ctrl = _read_ctrlstat(ser, "%s · 后" % op_txt)
    step = cmd_bank.kp_step_decode(code, res, sta_before, sta_after, ctrl, hit)
    print("   %s: %s | g_RelayCmd[0] %s→%s | g_CtrlStat[1]=%s | 断点 cmd=%s newSta=%s"
          % (op_txt, step["kind"] or "无应答", step["sta_before_txt"], step["sta_after_txt"],
             "读不到" if ctrl is None else "0x%04X" % ctrl,
             "读不到" if step["cmd_got"] is None else step["cmd_got"],
             cmd_bank.kp_state_txt(step["new_sta"])))
    return ([hit] if hit is not None else []), step


def part_keep(ctx):
    """一段 = 10-1 的全部条目: 进厂内 → 基线 → 保电 → 保电态下拉闸(应被拦) → 解除 → 拉闸(应放行)。"""
    ser = ctx.ser
    # ---- 开调试会话(断点观测)。台面没接 J-Link → None, 白盒那几条自动记"没做成" ----
    # ⚠ 观测一律 attach, **禁用 launch**(launch 会复位表, RAM 态清零); 见 CLAUDE.md 调试链纪律 1。
    ctx.session()
    if ctx.g is not None:
        # 整片 .out 解一次(停核遍历, 内部喂狗, 收尾放行) —— 之后一律查这张图
        gdbinit.build(ctx.g)
    g = ctx.g
    fire = partial(breakpoint.fire_hit, g) if g is not None else None

    cmd_bank.keep_intro(fire is not None, ctx.waived)           # 开场: 本次走哪两种观测
    cmd_bank.keep_goto_factory(ser)                             # 进厂内: 0x3A/0x3B/0x1A 不在密码 bypass 里
    st_pre = _read_state(ser, "基线")                      # 起始现态 g_RelayCmd[0](后面每步的现态基线)
    ctrl_pre = _read_ctrlstat(ser, "基线")                 # 起始 g_CtrlStat[1](判③要拿它排除"基线已置")
    cmd_bank.keep_read_sta3(ser, "基线")                         # 起始运行状态字3(只打印, 不作判据)

    if st_pre is None:
        why = "AA80 读不到起始现态 g_RelayCmd[0] ⇒ 整轮没做成"
        ctx.J.extend(cmd_bank.unproven_records(ENTRIES_ALL, why))
        ctx.J.note("10-1 遥控裁决段: 半途中止(这一次没做成) —— %s" % why)
        raise trial.Stop("%s ⇒ 未定论(退 2), 不是失败" % why)

    # ---- 第①步: 保电(0x3A)。裁决表第 6 行**每一列都是 7 或 9**(保电位两态) ⇒ 从任意现态都应受理 ----
    exp1 = cmd_bank.kp_expect(cmd_bank.KP_CMD_KEEP, st_pre)           # 表值: 这一步该落的新态
    recs1, st1 = _cmd_step(ser, fire, cmd_bank.KP_CMD_KEEP, "保电", "断[①]")
    ctx.J.extend(recs1)                                   # 断点那一次的记录(没会话时空)
    ctx.J.add("裁决逐格对表: 0x%02X 在现态 %s 上 → newSta 对 TAB_RelaySta"
              % (cmd_bank.KP_CMD_KEEP, st1["sta_after_txt"]),
              *cmd_bank.keep_judge_table(st1, exp1),
              crit="①", obs=judge.DEBUG, falsify=FALSIFY["①"])
    ctx.J.add("保电受理(裁决表第 6 行全列非错 ⇒ 任何现态都该受理)",
              *cmd_bank.keep_judge_reply(st1, "受理"), crit="⑤", falsify=FALSIFY["⑤受理"])
    ctx.J.add("保电后命令状态落到保电位",
              *cmd_bank.keep_judge_state_in(st1, cmd_bank.KP_KP_STATES), crit="⑤", falsify=FALSIFY["⑤落位"])
    a_keep = cmd_bank.keep_read_sta3(ser, "保电态")              # 保电位应置 bit4(解除后由⑥一起比)
    if st1["kind"] != "受理":
        why = "第①步保电没受理(%s) ⇒ 保电态没造成, 其后三步按设计无从重复" % (st1["kind"] or "无应答")
        ctx.J.extend(cmd_bank.unproven_records(ENTRIES_NO_KEEP, why))
        ctx.J.note("10-1 遥控裁决段: 半途中止(这一次没做成) —— %s" % why)
        raise trial.Stop("%s ⇒ 未定论(退 2), 不是失败" % why)

    # ---- 第②步: 保电态下拉闸(0x1A)。表第 1 行在保电两列上都是 0=ST_Error0 ⇒ 必被拦 ----
    exp2 = cmd_bank.kp_expect(cmd_bank.KP_CMD_OFF, st1["sta_after"])  # 表值(被拒时只进①的对表)
    recs2, st2 = _cmd_step(ser, fire, cmd_bank.KP_CMD_OFF, "保电态下拉闸", "断[②]")
    ctx.J.extend(recs2)
    ctx.J.add("裁决逐格对表: 0x%02X 在现态 %s 上 → newSta 对 TAB_RelaySta"
              % (cmd_bank.KP_CMD_OFF, st2["sta_after_txt"]),
              *cmd_bank.keep_judge_table(st2, exp2),
              crit="①", obs=judge.DEBUG, falsify=FALSIFY["①"])
    ctx.J.add("保电态下拉闸被拒(线上 0xDC + 错误位 0x04 = 1<<ER_PSWD)",
              *cmd_bank.keep_judge_reply(st2, "被拒", err=cmd_bank.KP_ERR_BYTE_PSWD),
              crit="②", falsify=FALSIFY["②"])
    ctx.J.add("被拒的具体原因是 ER_RlyOffKeep(g_CtrlStat[1] bit5), 不是密码判定 bit2",
              *cmd_bank.keep_judge_ctrlstat(st2, ctrl_pre), crit="③", falsify=FALSIFY["③"])
    ctx.J.add("被拦时命令状态原地不动(:1090 只在放行时才写)",
              *cmd_bank.keep_judge_hold(st2), crit="④", falsify=FALSIFY["④"])
    if st2["kind"] != "被拒":
        why = "第②步没被拒(%s) ⇒ 保电拦截半支没证到, 其后两步不构成完整的优先级对照" % (st2["kind"] or "无应答")
        ctx.J.extend(cmd_bank.unproven_records(ENTRIES_HALF, why))
        ctx.J.note("10-1 遥控裁决段: 半途中止(这一次没做成) —— %s" % why)
        raise trial.Stop("%s ⇒ 未定论(退 2), 不是失败" % why)

    # ---- 第③步: 保电解除(0x3B)。表第 7 行在保电两列上是 6/8 ⇒ 受理, 且落回普通合闸态 ----
    exp3 = cmd_bank.kp_expect(cmd_bank.KP_CMD_KEEPOFF, st2["sta_after"])
    recs3, st3 = _cmd_step(ser, fire, cmd_bank.KP_CMD_KEEPOFF, "保电解除", "断[③]")
    ctx.J.extend(recs3)
    ctx.J.add("裁决逐格对表: 0x%02X 在现态 %s 上 → newSta 对 TAB_RelaySta"
              % (cmd_bank.KP_CMD_KEEPOFF, st3["sta_after_txt"]),
              *cmd_bank.keep_judge_table(st3, exp3),
              crit="①", obs=judge.DEBUG, falsify=FALSIFY["①"])
    ctx.J.add("保电解除后命令状态落回普通态",
              *cmd_bank.keep_judge_state_is(st3, exp3), crit="⑤", falsify=FALSIFY["⑤落回"])
    a_free = cmd_bank.keep_read_sta3(ser, "解除后")              # 保电位 bit4 应已清
    ctx.J.add("保电位在运行状态字3 上置位(保电态时)、解除后清零",
              *cmd_bank.keep_judge_sta3(a_keep, a_free), crit="⑥", falsify=FALSIFY["⑥"])

    # ---- 第④步: 拉闸(0x1A)。此时已在普通态 ⇒ 表第 1 行该列非 0 ⇒ 应放行(优先级高者生效的正面半支) ----
    exp4 = cmd_bank.kp_expect(cmd_bank.KP_CMD_OFF, st3["sta_after"])
    recs4, st4 = _cmd_step(ser, fire, cmd_bank.KP_CMD_OFF, "解除后拉闸", "断[④]")
    ctx.J.extend(recs4)
    ctx.J.add("裁决逐格对表: 0x%02X 在现态 %s 上 → newSta 对 TAB_RelaySta"
              % (cmd_bank.KP_CMD_OFF, st4["sta_after_txt"]),
              *cmd_bank.keep_judge_table(st4, exp4),
              crit="①", obs=judge.DEBUG, falsify=FALSIFY["①"])
    ctx.J.add("解除后拉闸放行(优先级高者生效的正面半支): 应答受理且命令状态跨到拉闸侧",
              *cmd_bank.keep_judge_release(st4, exp4), crit="⑤", falsify=FALSIFY["⑤放行"])

    cmd_bank.keep_end_notes(fire is not None, ctx.waived)       # 收尾: 本次够不到的那两块(断点/698 入口)


def _banner():
    return ("== 10-1 费控·远程 | 工程=%s 表号=%s ==\n"
            ".. 触发=645 0x1C 操作字 0x3A 保电/0x1A 拉闸/0x3B 解除; 白盒停 %s 读 cmd/newSta"
            % (CURRENT.PROJECT, CURRENT.TABLE_ADDR.hex().upper(), breakpoint.text(BP_STA)))


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "10-1 费控·远程(保电/保电态拉闸被拦/解除后放行; 裁决逐格对表、控制状态字、运行状态字3)",
        cmd_bank.keep_criteria,
        name="10_1_keep_remote",
        parts=[("10-1 遥控裁决段", part_keep)],
        gdb=breakpoint, banner=_banner(),
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV)))
