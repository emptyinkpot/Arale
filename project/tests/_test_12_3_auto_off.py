# -*- coding: utf-8 -*-
"""
_test_12_3_auto_off.py —— 12-3「解除保电后本地费控根据剩余电费决定是否执行拉闸」测试脚本

规格:
  功能=保电功能 / 子项=解除保电后本地费控根据剩余电费决定是否执行拉闸 / 可达性=可测
  观察判据: 本地费控 —— 解除保电且余额低于透支门限 ⇒ 不等主站再发命令, 自己续拉;
            远程费控 —— 解除保电后续用电, 需主站再发拉闸

━━ 判据链在哪 ━━
  条目清单 = `CB.auto_off_criteria()` ①~④
  正文 = 本文件 `part_auto_off(ctx)`: 一行一个动作, 顺序即步骤
         (进厂内 → 走到现态 9 → ② 不透支阴性对照 → ① 注入造透支发 0x3B → ③ 同透支换发 0x3A),
         每发一帧之后紧跟它那几条判定 —— 判据文字 / crit / falsify 都写在这里。
  读内存那几步写在本脚本里(裸 `W.watch_vars` 读 `g_RelayCmd` / `g_CashStatus`), 库只给**纯规划与
         纯解码**: `CB.auto_off_walk_plan`(该发哪两帧) / `CB.auto_off_inject_intro`(注入前那一句) /
         `CB.auto_off_inject_decode`(注入记录 + 读回的现态 → 本步的 `step`) 与一排
         `CB.auto_off_judge_*`(各比一件事)。
  汇总判定 = `common/judge.py`(唯一出口: 只对预设条目计数 → 三态)

━━ 断点(模块级字面量元组 —— `scripts/_check_anchors.py` 靠它配对, 别改成表达式)━━
  `Set_RelayCmdR+684 @0x2d560`; 注入停在这句条件求值上 —— 改的正是它下一句要读的 `g_CashStatus`。
  `Set_RelayCmdR+994 @0x2d696`; `newSta` 在这里可读(区间 0x2d652-0x2d6ae)。
  ⚠ `:1016`(0x2d586)与 `:1020`(0x2d590)那两行上 `newSta` 落在**位置表的空洞区间**, 停在那儿读它
    只会得到一句"读不到" ⇒ 要读它的新值就停 `:1090`。**不是被优化掉了**: 换个行号就出来了。
  ⚠ `:1090` 在 `if (newSta != g_RelayCmd[0])` **里面** —— 状态不变时它根本不执行, 断点不命中;
    这既是 ③ 拿"状态不变"当阴性证据的根据, 也是"没命中"必须分两种解释的原因。

━━ 为什么每条都要断言 newSta, 而不是只看 g_RelayCmd[0] ━━
  表值 `kp_expect(KP_CMD_KEEPOFF, 9)` = `TAB_RelaySta[6][8]` = ST_RelayOn(8), 而续拉块写的是
  ST_RlyOffL(4)。`newSta` 是**裁决汇合点上的内部值**, 读到 4 只能是 :1016/:1020 写的;
  只看对外的 `g_RelayCmd[0]` 答不了"是裁决改的还是别处写回来的"。两条读的是同一件事的两端
  (内部值 / 对外落点), 互为对照物。

━━ 触发通道: ② 走帧, ①③ 走**注入**(两根轴都用上了)━━
  条件三(`Get_CashStatus() == ST_OvrCash2`)**帧通道造不出** —— 充值/退费/开户都过 `Read_Esam`
  的 MAC 校验(TaskRmtFee.c:2156-2163 / :2212-2218), 清零只落 ST_OvrCash1(3)。
  条件一(`TAB_MeterSty.style == TP_Local`)是**编译期常量**, 本台固件烘成本地表 —— 没有任何帧或
  注入能改它 ⇒ 判据 ④ 声明 `unprovable`(整项据此记未定论), 要证须烧一版 Local_Meter 未定义的固件。

━━ 副作用: 命令状态真变 ━━
  `g_RelayCmd[0]` 会被写到 4 并写参数区 ID_RelayCmd。但**驱动继电器那一步在许可判定之后**
  (`TaskRelay.c:282-284` 的 ≥75%Un), 本台 38V 闭锁 ⇒ 负载回路不动, 表自身由表内供电、通信不断。
  `g_CashStatus[0]` 的注入由 `inject()` 的 `close()` 自动还原(白名单点名了才写得进去)。
  跑完台面留在**厂内态**, 交给 `scripts/_restore_all.py` 收拾。

跑法(在 帧收发基础/ 下):
    python project/tests/_test_12_3_auto_off.py             # 全套: 串口观测 + 断点观测
    python project/tests/_test_12_3_auto_off.py --no-gdb    # **用户指定**只做串口观测(不报"总")
  ⚠ 两种跑法都会真开 COM3 对真表发 0x1C 操作字; 有断点观测时会**真接 J-Link**。
    **本脚本没有 dry 开关** —— 要只验语法/装配用 `ast.parse`(见 CLAUDE.md 工作约定)。
退出码: 通过→0 / 失败(有 ok=False 证据)→1 / 未定论(条目未证 / 观测没做成)→2。
"""
import time
from functools import partial

from common import judge            # 观测种类常量(断点/注入那几条证据标 DEBUG)
from common import trial            # 运行外壳: argv/日志/账本/串口/会话/收尾三连/退出码
from meterlib import cmd_bank       # 帧目录 + 语义动词积木箱(每步自带打印)
from meterlib import watch           # AA80 读内存(本脚本显式读的那几步就用它)
from project import CURRENT          # 本工程画像: 表号/.out 路径/喂狗点(库不认表, 只有脚本认)
from swdbg import breakpoint            # 断点级白盒(停核 + 注入 + 读局部量); 没接 J-Link → None
# 整片 .out 地图: 开会话后解一次, 之后断点/注入点只查它(见 swdbg/gdbinit.py 文件头)
from swdbg import gdbinit

WAIT = 2.0                                # 每次 AA80 直读等应答的上限(秒)
SETTLE = 8.0                              # 发完一帧之后等 g_RelayCmd[0] 落进目标态的上限(秒)

# ---- 断点: 模块级**字面量元组** ---------------------------------------------------
# `scripts/_check_anchors.py` 用 `ast.literal_eval` 抠这两个名字, 逐条对源码核"要读的变量在断点
#   那一行赋过值没有"; 写成 `CB.SOMETHING` 那种表达式就抠不出来, 等于那种观测静默地没人核过。
BP_INJ = ("TaskRelay.c", 1009)
VARS_INJ = ("g_RelayCmd",)                # 注入停点那刻可读的现场量 —— 现态, 三条件之一的前置
BP_DECIDE = ("TaskRelay.c", 1090)
VARS_DECIDE = ("newSta",)                 # 裁决汇合点上的新态 —— 读 4 只能是 :1016/:1020 写的

# ---- 本子项的 falsify(纯数据, 逐条对着 crit 用)-------------------------------------
FALSIFY = {
    "①": "本地表处于 ST_RelayOnKp(9)、收到 645 0x3B、费控为 ST_OvrCash2, 三条件齐时裁决却没落 "
          "ST_RlyOffL(4): 停 :1090 读到 newSta == 8(= 表值), 说明 :1013-1022 那段续拉块没生效; "
          "压根没停到, 说明那一帧没走到裁决点",
    "②": "不透支(`g_CashStatus[0] != ST_OvrCash2`)时 0x3B 仍落 ST_RlyOffL(4), 说明 "
          "`Get_CashStatus() == ST_OvrCash2` 那道闸失效(好固件该走表值 ST_RelayOn=8)",
    "③": "透支在位、同现态, 换发 0x3A(CMD_InKeep) 仍落 ST_RlyOffL(4), 说明 :1010 的 "
          "`cmd == CMD_OutKeep` 那半句失效(好固件进不了那块, 状态该停在 ST_RelayOnKp=9)",
}

# ---- 半途中止时要一次记全的条目(纯数据; 与正常路径同名同号**同观测**, 少记一条 = 分母变小)----
# ④ 不在表里: 它是**本台永远证不了**(编译期常量), 与"这一次中止了"无关 —— 它的状态由 criteria
#   的 `unprovable` 声明给, 不由中止路径给。
ENTRIES_ALL = (
    ("①", "本地表处在 ST_RelayOnKp(9)、收到 0x3B、费控为 ST_OvrCash2 时裁决当场落 ST_RlyOffL(4)", judge.DEBUG),
    ("②", "不透支时 0x3B 走表值 ST_RelayOn(8), 不落 ST_RlyOffL", judge.SERIAL),
    ("③", "透支在位换发 0x3A 时裁决不落 ST_RlyOffL, g_RelayCmd[0] 仍是 ST_RelayOnKp(9)", judge.DEBUG),
)


def _stop_unproven(ctx, entries, why):
    """半途中止的统一收场: 条目一次记全(`ok=None`) → 出声 → 停住(退 2, 不是失败)。"""
    ctx.J.extend(cmd_bank.unproven_records(entries, why, FALSIFY))
    ctx.J.note("12-3 本地续拉段: 半途中止(这一次没做成) —— %s" % why)
    raise trial.Stop("%s ⇒ 未定论(退 2), 不是失败" % why)


def _read_byte(ser, name, tag):
    """AA80 直读画像里某个 RAM 变量的**首字节** → int 或 None(读不到)。"""
    body = watch.watch_vars(ser, [name], tag=tag, wait=WAIT).get(name)
    return body[0] if body else None


def _goto(ser, op, want, tag):
    """发一个 645 0x1C 操作字, 轮询到 `g_RelayCmd[0]` 落进 `want` 之一 → 落到的现态。

    ⚠ 为什么要轮询: 0x3A 落 7 还是 9 由裁决表决定, 发完立刻读会读到过渡态。
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
    print("   · %s: 操作字 0x%02X 应答=%s → g_RelayCmd[0]=%s%s"
          % (tag, op, verdict, cmd_bank.kp_state_txt(st),
             "" if (st is not None and st in want) else "(未落进目标态, 至多等 %.0fs)" % SETTLE))
    return st


def _walk_to_kp(ser):
    """前置: 把现态走到 `ST_RelayOnKp(9)` → 现态(读在脚本, 该发哪两帧由库规划)。"""
    st = _read_byte(ser, "g_RelayCmd", "前置·现态")
    for op, want, tag in cmd_bank.auto_off_walk_plan(st):
        if st == cmd_bank.KP_ST_RELAYONKP:
            break
        st = _goto(ser, op, want, tag)
    print("\n[前置] 现态 g_RelayCmd[0]=%s(目标 ST_RelayOnKp(9))" % cmd_bank.kp_state_txt(st))
    return st


def _inject_step(ser, inj, op, label, *, crit=None, falsify=None, bp_decide=None, decide_vars=()):
    """①③ 的取证: 读现态 → 注入造透支 + 发一个 0x1C → 读回现态 → 装配。"""
    st = _read_byte(ser, "g_RelayCmd", "注入前现态")
    cmd_bank.auto_off_inject_intro(st, BP_INJ, bp_decide)
    rec = inj(BP_INJ, tuple(cmd_bank.AO_INJ_ASSIGN), watch=bp_decide, watch_vars=tuple(decide_vars),
              at_vars=VARS_INJ, label=label, crit=crit, falsify=falsify,
              timeout=SETTLE + 20.0,
              trigger=lambda: cmd_bank.ctrl_relay(ser, op, wait=WAIT))
    got = _read_byte(ser, "g_RelayCmd", "注入后现态")
    return cmd_bank.auto_off_inject_decode(rec, got)


def part_auto_off(ctx):
    """一段 = 12-3 的全部条目: 进厂内 → 走到现态 9 → ②(纯帧) → ①(注入) → ③(注入)。"""
    ser = ctx.ser
    ctx.session()                                        # 开调试会话(断点观测); 没接 J-Link → None
    # 整片 .out 解一次(要停核遍历, 内部按块喂狗, 收尾放行) —— 之后 `ctx.bp()` /
    # `inject_anchor()` / `decision_anchor()` 一律查这张图, 谁都不再独立反汇编。
    if ctx.g is not None:
        gdbinit.build(ctx.g)
    g = ctx.g
    # 递**断点元组**而不是已挂好的 bpno: `inject_hit` 见到元组自己挂、**用完必撤**(传 bpno 则撤不撤
    #   只由 `drop=` 管, 没命中就留在槽里 —— 那是"核被自己撂停、其后串口全哑"的来源)。
    inj = partial(breakpoint.inject_hit, g) if g is not None else None

    cmd_bank.auto_off_intro(inj is not None, ctx.waived)       # 开场: 本次走哪两种观测
    cmd_bank.enter_factory(ser)                                # 进厂内(645 0x1C 的密码判定, DLT645App.c:3456)
    st0 = _walk_to_kp(ser)                               # 前置: 把现态走到 ST_RelayOnKp(9)
    if st0 != cmd_bank.KP_ST_RELAYONKP:
        _stop_unproven(ctx, ENTRIES_ALL,
                       "走不到 ST_RelayOnKp(9)(现态 %s)⇒ 645 0x1C 被拒或没落进目标态, 本项三条判据"
                       "本次都没做成(不是固件不续拉)" % cmd_bank.KP_ST_TXT.get(st0, "?"))

    cash = _read_byte(ser, "g_CashStatus", "② 费控状态字")   # ② 阴性对照: 不透支, 发 0x3B(纯帧)
    resp = cmd_bank.ctrl_relay(ser, cmd_bank.KP_CMD_KEEPOFF, wait=WAIT)
    got = _read_byte(ser, "g_RelayCmd", "② 发出后现态")
    neg = cmd_bank.auto_off_neg_decode(cash, resp, got)
    ctx.J.add("② 阴性对照: 同一现态(9)、同一命令(0x3B)、但不透支(实测 g_CashStatus[0] != "
              "ST_OvrCash2(4)), 裁决走表值 ST_RelayOn(8)(= TAB_RelaySta[6][8]), 不落 ST_RlyOffL",
              *cmd_bank.auto_off_judge_neg(neg), crit="②", falsify=FALSIFY["②"])

    _walk_to_kp(ser)                                     # ② 那一帧已经把现态带到 8, ① 要的起点是 9
    step1 = _inject_step(ser, inj, cmd_bank.KP_CMD_KEEPOFF,
                         "12-3 ① 注入造透支后发 0x3B, 停裁决汇合点读 newSta",
                         crit="①", falsify=FALSIFY["①"],
                         bp_decide=BP_DECIDE, decide_vars=VARS_DECIDE) if inj is not None else None
    if step1 is None:
        ctx.J.extend(cmd_bank.unproven_records(ENTRIES_ALL[0:1] + ENTRIES_ALL[2:3],
                                         "没有调试会话 ⇒ 透支态(ST_OvrCash2)造不出来 ⇒ 这两条不做"
                                         "(本项没有可降级的黑盒替身)", FALSIFY))
    else:
        ctx.J.add("① 本地表处在 ST_RelayOnKp(9)、收到 645 0x3B(解除保电)、费控为 ST_OvrCash2"
                  "(低于透支门限), 裁决当场落 ST_RlyOffL(4), 不等主站再发命令",
                  *cmd_bank.auto_off_judge_ovr(step1),
                  crit="①", obs=judge.DEBUG, falsify=FALSIFY["①"], trig=judge.TRIG_INJECT)

    if step1 is None:
        cmd_bank.auto_off_end_notes(False, ctx.waived, BP_INJ, BP_DECIDE)
        return

    _walk_to_kp(ser)                                     # ① 那一帧把现态带到了 4, ③ 要的起点同样是 9
    step3 = _inject_step(ser, inj, cmd_bank.KP_CMD_KEEP,
                         "12-3 ③ 注入造透支后发 0x3A, 看状态不动")   # 这一次只造透支, 不看裁决点
    ctx.J.add("③ `cmd == CMD_OutKeep` 是那块续拉的必要条件: 透支在位、同现态, 换发 0x3A"
              "(CMD_InKeep 保电), 裁决不落 ST_RlyOffL, g_RelayCmd[0] 仍是 ST_RelayOnKp(9)(状态不变)",
              *cmd_bank.auto_off_judge_keep(step3, step1["ns"]),
              crit="③", obs=judge.DEBUG, falsify=FALSIFY["③"], trig=judge.TRIG_INJECT)
    cmd_bank.auto_off_end_notes(True, ctx.waived, BP_INJ, BP_DECIDE)   # 收尾: 本次够不到的那两块


def _banner():
    return ("== 12-3 解除保电后的本地续拉 | 工程=%s 表号=%s ==\n"
            ".. 触发=645 0x1C(0x3B 解除保电 / 0x3A 保电); 注入停 %s 写 g_CashStatus[0]=%d 造透支, "
            "断 %s 读 newSta"
            % (CURRENT.PROJECT, CURRENT.TABLE_ADDR.hex().upper(),
               breakpoint.text(BP_INJ), cmd_bank.AO_CASH_OVR, breakpoint.text(BP_DECIDE)))


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "12-3 解除保电后本地费控决定是否拉闸(注入造透支 × 断点看裁决 × 不透支阴性对照)",
        cmd_bank.auto_off_criteria,
        name="12_3_auto_off",
        parts=[("12-3 本地续拉段", part_auto_off)],
        gdb=breakpoint, banner=_banner(),
        # ⚠ `inject_allow` 必须在**会话构造时**点名 —— 白名单精确匹配符号名, 事后补不了。
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV,
                        inject_allow=cmd_bank.ao_inject_allow())))
