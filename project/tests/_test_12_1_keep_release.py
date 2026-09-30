# -*- coding: utf-8 -*-
from functools import partial

from common import judge            # 观测种类常量(断点那几条证据标 DEBUG)
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
#   `Set_RelayCmdR+120 @0x2d32c`; 该行同时可读 `cmd`(形参)与 `newSta`(:849 算出)。
#   ⚠ 别把断点挪回规格原写的 `:823` —— 那一行 `newSta` 一个位置区间都没有。
BP_STA = ("TaskRelay.c", 851)
VARS_STA = ("cmd", "newSta")

# ---- 本子项的 falsify(纯数据, 逐条对着 crit 用)-------------------------------------
# `*命中` 那几条是**断点那一次**的 falsify(挂在带断点发出去的那一帧上), 其余是判定那一条的。
FALSIFY = {
    "①命中": "固件把 0x80017F 解到别的支(算出别的 cmd)或时标没放行 ⇒ 停不到 %s "
              "或 cmd 不等于 CMD_InKeep(5)" % breakpoint.text(BP_STA),
    "①": "固件不走 OMD 0x80017F→CMD_InKeep 这一支(解错分支/时标没放行/裁决算错)⇒ 断点不命中, "
          "或 cmd≠5, 或 newSta 不落保电位",
    "③": "固件不判时标(或缺了 :11763 那一句)⇒ 不带时标也受理(DAR=0)且命令状态被改写; "
          "若回 DAR=3 则是 :11758 的表型闸先拦的, 不是时标闸",
    "④命中": "保电位的两个现态在裁决表第 1 行上都是 ST_Error0(:851/:855-859 该走 ER_RlyOffKeep), "
              "固件却受理了 ⇒ 停不到 %s 或 newSta 不是 ST_Error0" % breakpoint.text(BP_STA),
    "④": "保电位的两个现态在裁决表第 1 行上都是 ST_Error0, 固件却放行(受理/命令状态跳变)"
          "⇒ 保电闸失效",
    "⑤": "按现态分派的错误码不是 ER_RlyOffKeep(例: 落到 :881 的 default ⇒ 0x0004)"
          "⇒ 保电拦的不是这条规矩, 或裁决表那一格不是 ST_Error0",
    "②命中": "固件把 0x800180 解到别的支或时标没放行 ⇒ 停不到 %s 或 cmd≠CMD_OutKeep(6)" % breakpoint.text(BP_STA),
    "②": "固件不走 OMD 0x800180→CMD_OutKeep 这一支(解错分支/时标没放行/裁决算错)⇒ 断点不命中, "
          "或 cmd≠6, 或 newSta 没落回普通态 ⇒ 保电位撤不掉",
    "⑦": "Get_MeterRunSta 的 pStatus[1] |= 0x10 那句(:1006-1010)与 g_RelayCmd[0] 脱节"
          "⇒ 上报的保电位与实际命令状态不一致",
    "⑥命中": "保电已解除而拉闸仍被拦(命令状态不动)⇒ 解放了但拦截没撤; 或受理了却没落到表值 "
              "⇒ 解除与放行不是同一条裁决链",
    "⑥": "保电已解除而拉闸仍被拦(命令状态不动)⇒ 保电位撤了、拦截没撤; 或受理了没落表值 "
          "⇒ 解除与放行不是同一条裁决链",
}

# ---- 半途中止时要一次记全的条目(纯数据; 与正常路径同名同号**同观测**, 少记一条 = 分母变小)----
ENTRIES_ALL = (
    ("①", "698 保电入口走通(停 :851, cmd==CMD_InKeep)", judge.DEBUG),
    ("②", "698 解除入口走通(同一处 cmd==CMD_OutKeep)", judge.DEBUG),
    ("③", "698 这条入口的时标闸在起作用(不带时标被拒 DAR_TimeStamp)", judge.SERIAL),
    ("④", "保电期间拉闸被拒(线上 DAR_RefuseOp + 裁决 newSta==ST_Error0)", judge.DEBUG),
    ("⑤", "被拒原因是 ER_RlyOffKeep(g_CtrlStat[1] == 0x0020)", judge.SERIAL),
    ("⑥", "解除后拉闸放行", judge.DEBUG),
    ("⑦", "保电位在运行状态字3 上置位、解除后清零", judge.SERIAL),
)
def _pending(*done):
    """半途中止时要一次记全的条目 = `ENTRIES_ALL` 里除掉**已经记过**的那几条。

    `done` 就是认领号本身(纯数据), 在每一个中止点就地写"此刻已记过谁" —— 这样加一步、挪一步
    时, 分母跟着走的是这一处, 而不是某个隐含的序号。
    """
    return tuple(e for e in ENTRIES_ALL if e[0] not in done)


def _stop_unproven(ctx, entries, why):
    """半途中止的统一收场: 条目一次记全(`ok=None`) → 出声 → 停住(退 2, 不是失败)。"""
    ctx.J.extend(cmd_bank.unproven_records(entries, why, FALSIFY))
    ctx.J.note("12-1 保电/解除段: 半途中止(这一次没做成) —— %s" % why)
    raise trial.Stop("%s ⇒ 未定论(退 2), 不是失败" % why)


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


def _cmd_step698(ser, fire, omd, param, op_txt, label, *, crit, falsify, tagged=True):
    """一步一次 698 Action: 读现态 → 取时标 → 发帧(带断点) → 读新态与控制状态字 → 装配。

    `tagged=False` = **这一次不带时标**(时标域就是一个 `00` 字节): 那是判据③ 要的那一帧, 不是
      "忘了给" —— 保电/解除/跳闸三支都先判时标, 不带时标的帧根本到不了裁决层。
    **表钟读不出 ⇒ 返回 `(recs, None)`** —— 那一次没做成(带时标的动作发不出去), 由脚本按半途中止处理。
    ⚠ 没会话时 `fire` 是 `None`: 这一次**照样发帧**(黑盒观测不能跟着消失)。
    """
    s_before = _read_state(ser, "%s · 前" % op_txt)
    tag = None
    if tagged:
        tag = cmd_bank.keep698_timetag(ser, wait=WAIT)
        if tag is None:
            return [], None
    if fire is not None:
        hit = fire(BP_STA, lambda: cmd_bank.keep698_send(ser, omd, param, timetag=tag, wait=WAIT, head=op_txt),
                   label="%s 698 %s → 停在 %s(读 cmd/newSta)"
                         % (label or op_txt, op_txt, breakpoint.text(BP_STA)),
                   vars=VARS_STA, crit=crit, falsify=falsify)
        res = (hit or {}).get("result")
    else:
        hit = None
        res = cmd_bank.keep698_send(ser, omd, param, timetag=tag, wait=WAIT, head=op_txt)
    s_after = _read_state(ser, "%s · 后" % op_txt)
    ctrl = _read_ctrlstat(ser, "%s · 后" % op_txt)
    step = cmd_bank.keep698_step_decode(omd, op_txt, res, s_before, s_after, ctrl, hit)
    print("   %s: DAR=%s | g_RelayCmd[0] %s→%s | g_CtrlStat[1]=%s | 断点 cmd=%s newSta=%s"
          % (op_txt, step["dar"], step["s_before_txt"], step["s_after_txt"],
             "读不到" if ctrl is None else "0x%04X" % ctrl,
             "读不到" if step["cmd_got"] is None else step["cmd_got"],
             cmd_bank.kp_state_txt(step["new_sta"])))
    return ([hit] if hit is not None else []), step


def part_keep698(ctx):
    """一段 = 12-1 的全部条目: 进厂内 → 基线 → 时标闸那帧 → 保电 → 拉闸被拦 → 解除 → 拉闸放行。

    ⚠ 观测一律 attach, **禁用 launch**(launch 会复位表, RAM 态清零); 见 CLAUDE.md 调试链纪律 1。
    ⚠ 时标闸那一步(**不带时标**的同一 OMD)必须排在最前: 它引用的是"命令状态没动"这个读数,
      后面任何一步一跑, 基线就没了。
    """
    ser = ctx.ser
    ctx.session()
    if ctx.g is not None:
        # 整片 .out 解一次(停核遍历, 内部喂狗, 收尾放行) —— 之后一律查这张图
        gdbinit.build(ctx.g)
    g = ctx.g
    # 递**断点元组**而不是已挂好的 bpno: `fire_hit` 见到元组会自己挂、命中与没命中**两条路都撤**。
    # 传 bpno 则撤不撤只由 `drop=` 管, 万一没命中就留在槽里 —— 那是"核被自己撂停、其后串口全哑"的来源。
    fire = partial(breakpoint.fire_hit, g) if g is not None else None

    cmd_bank.keep698_intro(fire is not None, ctx.waived)     # 开场: 本次走哪两种观测
    cmd_bank.keep_goto_factory(ser)                          # 进厂内(698 动作在厂内态下才过安全判定 :6412)
    s_pre = _read_state(ser, "基线")                    # 起始现态 g_RelayCmd[0](后面每步的现态基线)
    ctrl_pre = _read_ctrlstat(ser, "基线")              # 起始 g_CtrlStat[1](判⑤要拿它排除"基线已置")
    cmd_bank.keep_read_sta3(ser, "基线")                      # 起始运行状态字3(只打印, 不作判据)
    if s_pre is None:
        _stop_unproven(ctx, _pending(), "AA80 读不到起始现态 g_RelayCmd[0] ⇒ 整轮没做成")

    # ---- 第③步(排在最先): 同一个 OMD、**不带时标**。时标闸该在这里把帧挡住 ----
    _r3, st3 = _cmd_step698(ser, None, cmd_bank.KEEP698_OMD_ON, cmd_bank.KEEP698_NULL,
                            "保电 Action 0x80017F(不带时标, 应被时标闸拒)", "",
                            crit="③", falsify=FALSIFY["③"], tagged=False)
    ctx.J.add("③ 698 这条入口的时标闸在起作用: 不带时标 ⇒ DAR_TimeStamp(32) 且 "
              "g_CtrlStat[1]==0x0004(ER_InvalidTmr 被归并成 ER_Password 那位), 命令状态不动",
              *cmd_bank.keep698_judge_timetag(st3, s_pre), crit="③", falsify=FALSIFY["③"])
    if st3["dar"] == 3:
        _stop_unproven(ctx, _pending("③"),
                       "DAR_RefuseOp(3) = Action_Control :11758 的 `TAB_MeterSty.relay ∈ {TP_In,TP_Ex}` "
                       "那道闸拦下的 ⇒ 698 保电/解除在本表型上**结构不可达**")

    # ---- 第①步: 698 保电(0x80017F, 带时标)。表第 6 行每一列都是 7 或 9 ⇒ 从任意现态都应受理 ----
    recs1, st1 = _cmd_step698(ser, fire, cmd_bank.KEEP698_OMD_ON, cmd_bank.KEEP698_NULL,
                              "保电 0x80017F(带时标)", "断[①]",
                              crit="①", falsify=FALSIFY["①命中"])
    if st1 is None:
        _stop_unproven(ctx, _pending("③"), "表钟读不出, 造不出时标(带时标的 698 动作发不出去)")
    ctx.J.extend(recs1)                                # 断点那一次的记录(没会话时空)
    ctx.J.add("① 698 保电入口走通: 0x80017F 这一帧走到裁决层, cmd == CMD_InKeep(5), "
              "newSta ∈ 保电位两态",
              *cmd_bank.keep698_judge_action(st1, cmd_bank.KP_CMD_ROW[cmd_bank.KP_CMD_KEEP], cmd_bank.KP_KP_STATES),
              crit="①", obs=judge.DEBUG, falsify=FALSIFY["①"])
    a_keep = cmd_bank.keep_read_sta3(ser, "保电态")           # 保电位应置 bit4(解除后由⑦一起比)
    if st1["dar"] != 0:
        _stop_unproven(ctx, _pending("③", "①"),
                       "698 保电没受理(DAR=%s) ⇒ 保电态没造成, 其后三步按设计无从重复" % st1["dar"])

    # ---- 第④步: 保电态下 698 拉闸(0x800081)。表第 1 行在保电两列(idx 6/8)上都是 0 ⇒ 必被拦 ----
    recs2, st2 = _cmd_step698(ser, fire, cmd_bank.KEEP698_OMD_DROP, cmd_bank.keep698_drop_param(),
                              "拉闸 0x800081(保电态下, 应被拦)", "断[④]",
                              crit="④", falsify=FALSIFY["④命中"])
    if st2 is None:
        _stop_unproven(ctx, _pending("③", "①"), "表钟读不出, 造不出时标")
    ctx.J.extend(recs2)
    ctx.J.add("④ 保电期间拉闸被拒: 线上 DAR_RefuseOp(3), 裁决定在 newSta==ST_Error0, "
              "且操作字解到拉闸那一行",
              *cmd_bank.keep698_judge_action(st2, cmd_bank.KP_CMD_ROW[cmd_bank.KP_CMD_OFF], cmd_bank.KP_ST_ERROR0,
                                       dar_want=3),
              crit="④", obs=judge.DEBUG, falsify=FALSIFY["④"])
    ctx.J.add("⑤ 被拒的具体原因是 ER_RlyOffKeep(g_CtrlStat[1] bit5), 不是密码判定 bit2",
              *cmd_bank.keep_judge_ctrlstat(st2, ctrl_pre), crit="⑤", falsify=FALSIFY["⑤"])

    # ---- 第②步: 698 解除(0x800180, 带时标)。表第 7 行在保电两列上是 6/8 ⇒ 受理, 落回普通合闸态 ----
    exp3 = cmd_bank.kp_expect(cmd_bank.KP_CMD_KEEPOFF, st2["s_after"])
    recs3, st3b = _cmd_step698(ser, fire, cmd_bank.KEEP698_OMD_OFF, cmd_bank.KEEP698_NULL,
                               "解除 0x800180(带时标)", "断[②]",
                               crit="②", falsify=FALSIFY["②命中"])
    if st3b is None:
        _stop_unproven(ctx, _pending("③", "①", "④", "⑤"), "表钟读不出, 造不出时标")
    ctx.J.extend(recs3)
    ctx.J.add("② 698 解除入口走通: 0x800180 这一帧走到裁决层, cmd == CMD_OutKeep(6), "
              "newSta 落回普通合闸态",
              *cmd_bank.keep698_judge_action(st3b, cmd_bank.KP_CMD_ROW[cmd_bank.KP_CMD_KEEPOFF], exp3),
              crit="②", obs=judge.DEBUG, falsify=FALSIFY["②"])
    a_free = cmd_bank.keep_read_sta3(ser, "解除后")           # 保电位 bit4 应已清
    ctx.J.add("⑦ 保电位在运行状态字3 上置位(保电态时)、解除后清零",
              *cmd_bank.keep_judge_sta3(a_keep, a_free), crit="⑦", falsify=FALSIFY["⑦"])

    # ---- 第⑥步: 拉闸(0x800081)。此时已在普通态 ⇒ 表第 1 行该列非 0 ⇒ 应放行 ----
    exp4 = cmd_bank.kp_expect(cmd_bank.KP_CMD_OFF, st3b["s_after"])
    recs4, st4 = _cmd_step698(ser, fire, cmd_bank.KEEP698_OMD_DROP, cmd_bank.keep698_drop_param(),
                              "拉闸 0x800081(解除后, 应放行)", "断[⑥]",
                              crit="⑥", falsify=FALSIFY["⑥命中"])
    if st4 is None:
        _stop_unproven(ctx, _pending("③", "①", "④", "⑤", "②", "⑦"), "表钟读不出, 造不出时标")
    ctx.J.extend(recs4)
    ctx.J.add("⑥ 解除后拉闸放行: 线上 DAR_Success(0) 且裁决定在表值上(落的是表值, 不是「方向大致对」)",
              *cmd_bank.keep698_judge_action(st4, cmd_bank.KP_CMD_ROW[cmd_bank.KP_CMD_OFF], exp4, dar_want=0),
              crit="⑥", obs=judge.DEBUG, falsify=FALSIFY["⑥"])

    cmd_bank.keep698_end_notes(fire is not None, ctx.waived)  # 收尾: 本次够不到的那两块


def _banner():
    return ("== 12-1 保电/解除 | 工程=%s 表号=%s ==\n"
            ".. 触发=698 Action 0x80017F/0x800180/0x800081; 白盒停 %s 读 cmd/newSta"
            % (CURRENT.PROJECT, CURRENT.TABLE_ADDR.hex().upper(), breakpoint.text(BP_STA)))


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "12-1 保电/解除(两条入口走通/时标闸/保电期间拉闸被拦/解除后放行/保电位上报)",
        cmd_bank.keep698_criteria,
        name="12_1_keep_release",
        parts=[("12-1 保电/解除段", part_keep698)],
        gdb=breakpoint, banner=_banner(),
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV)))
