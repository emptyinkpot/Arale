# -*- coding: utf-8 -*-
"""在证什么: 645 0x1C 合闸进管理芯之后 —— 命令状态落到合闸方向(不低于 ST_RelayOn); 许可判定通过时
  『合闸』口 30200B0A 推进一条、写库位置真走到 Recd_CtrlRelay 的落库那一句(id=ID_RelayOn、
  记录体里操作者代码非空); 判定闭锁时不落库且 g_RelayBlk 置位。
会向表写什么: 645 0x1C 合闸**真动作** —— 继电器通负载; 台面电压 <75%Un 时固件按 DL/T698 防误跳闭锁,
  继电器不动。本脚本自己发 645 进厂内, 跑完表停在厂内态, 收尾不必再跑总复位。
跑法: `python project/tests/_test_5_10_relay_on.py`(真串口 + 真探针; `--no-gdb` 只做黑盒)。
结论怎么读: 账本末行「判据: 满足 n/N」, N=6; 退 0 = 通过 / 1 = 失败 / 2 = 未定论。
  差别只在命令帧的操作字)。
"""
from common import judge            # 观测种类常量(断点那几条证据标 DEBUG)
from common import trial            # 运行外壳: argv/日志/账本/串口/会话/收尾三连/退出码
from meterlib import cmd_bank       # 帧目录 + 语义动词积木箱(每步自带打印)
from meterlib import watch           # AA80 读内存(本脚本显式读的那几步就用它)
from project import CURRENT          # 本工程画像: 表号/.out 路径/喂狗点(库不认表, 只有脚本认)
from swdbg import breakpoint            # 断点级白盒(停核 + 读局部量); 没接 J-Link → open_or_none→None
from swdbg import gdbinit            # 整片 .out 地图: 开会话后解一次, 之后断点/注入点只查它

# ---- 子项参数(纯数据) ----
OP = "合"                 # 645 0x1C 操作字 0x1C = 直接合闸
RELAY_DELAY = 0           # 立即动作(固件 DAT5×5 秒)
WAIT = 3.0
BURST_WAIT = 1.0          # 第 4 步连发那 11 回之间只等它进一次任务, 不等动作判完
BURST = 11                # 第 4 步: 容量 10 条 + 1

# ---- 第 2 / 3 / 4 步记下的记录口几何(解释停点上读到的那四段与第 4 步的封顶) ----
# 布局出处 TaskRecord.c:1266-1273(Recd_CtrlRelay); 记录体总长 LEN_RelayOff = 41。
REC_CAP = CURRENT.RELAY_REC_CAP   # 事件记录容量(条) —— 画像单一事实源(RecdData.h:142-145)
REC_HEAD = (0, 6)         # 发生时刻 6 字节(:1266 Get_MeterTime(&buff[0]))
REC_OPR = (6, 10)         # 操作者代码 4 字节(:1267 Copy_Data(&buff[6], pOper, LEN_Operator) 的前 4 字节)
REC_SRC = 10              # 发生源 1 字节(:1267 那 5 字节的最后 1 字节)
REC_ENE = (11, 41)        # 六种总电能 30 字节(:1268-1273 六次 Read_CurkWh, 各 5 字节)
# 本记录**没有结束时刻**那一段(与 5-11 负荷开关误动作记录不同)。
REC_ID = "ID_RelayOn"    # 第 2 步期望的 id 枚举名 —— Recd_CtrlRelay :1253 那句按 action 二选一

# ---- 断点: 模块级**字面量元组** ---------------------------------------------------
# `scripts/_check_anchors.py` 用 `ast.literal_eval` 抠这两个名字, 逐条对源码核"要读的变量在断点
#   那一行赋过值没有"; 写成 `CB.SOMETHING` 那种表达式就抠不出来, 等于那种观测静默地没人核过。
#
# 第 1 步那个落点 = 三条判据那一句(TaskRelay.c:282-284), 靠 `prev` 定在 `Get_CompFlag` 的**第一处**
#   调用之前 —— 那一处正是 `0xFF == Get_CompFlag(CMP_075Un, 4)`(:284)。
#   ⚠ 第 1 步原文要读的 `C_75Un` 读不到: 它是编译期宏(TaskMetering.c:51 `C_75Un (TAB_Standard.Un * 75)`),
#     不是变量, 两条白盒通路都按名字取不到 —— 所以这一停读的是判据里另外那一个量 `g_PlcState`。
BP_GATE = ("prev", "Run_TaskRelay", "Get_CompFlag", 1)
VARS_GATE = ("g_PlcState",)     # 判据左半(模块未插 0xFFFFFFFF / 停止通讯 低 4 位为 0)
#
# 第 2 步那个落点 = 落库那一句(`Write_RecdData(id, &buff[0], 0, LEN_RelayOff, 0)` @TaskRecord.c:1277):
#   走到这里 buff[0..40] 已经填满(时刻 :1266 / 操作者与发生源 :1267 / 六种电能 :1268-1273),
#   所以第 2 步要读的四段在**同一个停点**上一次读全。
# ⚠ 不落在 :1253 那一句: 那里只有 `id` 可读, buff 还是空的; 也不落在 Read_CurkWh 那六句上:
#   要读的是"落库那一条的完整记录体", 不是某一次电能读回。
BP_REC = ("prev", "Recd_CtrlRelay", "Write_RecdData", 1)
VARS_REC = ("id", "buff")
#   `id`(ST_SwOn 时是 ID_RelayOn)与 `buff` 都是 Recd_CtrlRelay 的函数内量。
#   合闸期望 `id=ID_RelayOn`; 拉闸那边期望 `ID_RelayOff`(见 _test_5_9_relay_off.py)。

# ---- 本子项的 falsify(纯数据, 逐条对着 crit 用)-------------------------------------
# `③命中` 是**断点那一次**的 falsify(挂在合闸那一帧上), 其余是各自那一条的。
FALSIFY = {
    "③命中": "命令被受理而固件不走 `Recd_CtrlRelay`(或落库前 return)⇒ 不会停在 %s"
             % (breakpoint.text(BP_REC)),
    "①": "0x1C 没进 Run_TaskRelay(:278 Upd_RelayCmd 未执行)或操作字解错方向 ⇒ "
         "g_RelayCmd[0] 停在原值/落到反方向",
    "②": "固件把方向判错(拉当成合)、action 传了第三个值(OTHER)或 id 取错那一支 ⇒ "
         "id 不是 ID_RelayOn, 记录落进反方向那条口",
    "⑤": "判定已通(≥75%Un 且载波未接)、继电器也动了, 而 `Recd_CtrlRelay` 没被调用或"
         "写库失败 ⇒ 这条口一动不动",
    "⑥通": "判定判为通而事件没落库 ⇒ 判定与写库位置之间还有别的条件(或写库失败被吞了)",
    "⑥锁": "判定判为闭锁却照样落库(防误跳失效), 或闭锁标志没置位而事件也没落(闭锁链断在别处)",
}


def _byte0(body):
    """`W.watch_vars` 读回的字节 → **首字节** int; 没读到 → `None`(不拿 0 冒充)。"""
    return body[0] if body else None


def _u32(body):
    """`W.watch_vars` 读回的 4 字节 → 无符号 int(小端, 与固件 `g_PlcState` 同序); 没读到 → `None`。"""
    return int.from_bytes(body[:4], "little") if body and len(body) >= 4 else None


def _fields(buff_txt):
    """断点停时读到的那一条记录体 → 四段字段; 解不出或短于记录体长度 → `{}`(不拿空串冒充)。"""
    b = cmd_bank.gdb_bytes(buff_txt) if buff_txt else None
    if not b or len(b) < REC_ENE[1]:
        return {}
    return {"时刻": b[REC_HEAD[0]:REC_HEAD[1]],
            "操作者代码": b[REC_OPR[0]:REC_OPR[1]],
            "发生源": b[REC_SRC],
            "六种总电能": b[REC_ENE[0]:REC_ENE[1]]}


def _rec_ts(b6):
    """记录体 [0..5] 那 6 字节 → 698 线上时标串; 取不全或落在空/无效时刻 → `None`。

    这是固件自己的编码器 `Spread_DateTime` 的逆(DLT698App.c:16116-16153, D_DateTimeS 那一支):
    内部 6 字节是**秒 分 时 日 月 年**(TaskTime.c:364 `Get_MeterTime` 的注释写明 `HEX码,秒分时日月年`),
    年那 1 字节是 2000 起的偏移; 线上是 `1C <年2B大端> <月> <日> <时> <分> <秒>`。所以两边**本来就该
    逐字段相等** —— 不是"字形不同无从比"。全 0 的年月日与 0xFF 的月日各是一类"空时刻", 线上编成 0/0xFFFF。
    ⚠ 不复用 `cmd_bank.frez_expect_ts`: 那个是冻结记录用的, 把秒**写死成 00**, 拿来比事件记录会把
      秒那一位永远比错。
    """
    if not b6 or len(b6) < 6:
        return None
    sec, mi, hh, dd, mo, yy = b6[:6]
    if yy == 0x00 and mo == 0x00 and dd == 0x00:
        return None                    # :16120 空时刻
    if mo == 0xFF and dd == 0xFF:
        return None                    # :16126 无效时刻
    return "%04d-%02d-%02d %02d:%02d:%02d" % (2000 + yy, mo, dd, hh, mi, sec)


def _hex(b):
    """一段字节 → `AA BB …`; 没读到 → 一行字(不拿空串冒充)。"""
    return "没读到" if b is None else " ".join("%02X" % x for x in b)


def part_relay(ctx):
    """一段 = 5-10 的四步: 判许可并读 75%Un 判据里那个量 → 发 0x1C(停在落库那一句读 id 与记录体)
    → 698 读『合闸』口比对 → 连发 11 回看条数顶掉。

    ⚠ 第 1 步与第 2 步各要**一次 0x1C**: 两条判据不同的断点(判据那一句 :282-284 与落库那一句 :1277)
      隔着整段动作, 而 `fire_hit` 一次只挂一个断点(见 `breakpoint.to_bpno`) —— 所以第 1 步那一停
      由**第 1 回** 0x1C 驱动、第 2 步那一停由**第 2 回**驱动。两回都进日志, 条数按"每回各加一条"记。
    ⚠ 观测一律 attach, **禁用 launch**(launch 会复位表, RAM 态清零); 见 CLAUDE.md 调试链纪律 1。
    ⚠ 递**断点元组**而不是已挂好的 bpno: `fire_hit` 见到元组会自己挂、命中与没命中**两条路都撤**。
      传 bpno 则撤不撤只由 `drop=` 管, 万一没命中就留在槽里 —— 那是"核被自己撂停、其后串口全哑"的来源。
    """
    ser = ctx.ser
    ctx.session()
    if ctx.g is not None:
        # 整片 .out 解一次(停核遍历, 内部喂狗, 收尾放行) —— 之后一律查这张图
        gdbinit.build(ctx.g)
    g = ctx.g
    have_wb = g is not None
    ev = cmd_bank.RELAY_EVENT[OP]

    # ---- 阶段一(基线): 进厂内 → 判动作许可 → 读命令状态/表钟/本方向事件口 ----
    # 进厂内: 记录读回受安全判定管(5-3 实踩: 厂外读记录会被打回), 且 0x1C 本身要过密码判定。
    cmd_bank.enter_factory(ser)
    allowed, why_gate = cmd_bank.relay_gate_decode(
        watch.watch_vars(ser, ["g_CompFlg"], tag="继电器动作许可 判前", wait=WAIT).get("g_CompFlg"))
    cmd_pre = _byte0(watch.watch_vars(ser, ["g_RelayCmd"], tag="g_RelayCmd 判前", wait=WAIT).get("g_RelayCmd"))
    t0 = cmd_bank.read_clock(ser, quiet=True)
    pre = cmd_bank.read_event_row(ser, ev, 1, wait=WAIT)
    cnt_pre = cmd_bank.event_area_count(ser, ev)
    print("   前置: 继电器动作许可 %s —— %s" % ("通" if allowed else "闭锁", why_gate))
    print("   基线: 表钟=%s | g_RelayCmd[0]=%s | %s 最新一条=%s | 条数=%s"
          % (t0, cmd_bank.n8txt(cmd_pre), ev, (pre or {}).get("ts"),
             "读不出" if cnt_pre is None else cnt_pre))

    # ---- 阶段二(第 1 步): 发第 1 回 0x1C, 停在三条判据那一句, 读判据里那个量 ----
    def do_cmd():
        cmd_bank.ctrl_relay(ser, OP, delay=RELAY_DELAY, wait=WAIT)     # fn 是纯动作, 不取值

    # 没会话(`g is None`)时它照样把 `do_cmd` 发出去, 只是返回 None(黑盒那一半不许跟着降级)。
    r_gate = breakpoint.fire_hit(g, BP_GATE, do_cmd,
                        label="断[G] 645 0x1C %s(第 1 回) → 停在三条判据那一句 %s(读 %s)"
                              % (ev, breakpoint.text(BP_GATE), " 与 ".join(VARS_GATE)),
                        vars=VARS_GATE, crit=None)
    cnt_gate = cmd_bank.event_area_count(ser, ev)

    # ---- 阶段三(第 2 步): 发第 2 回 0x1C, 停在落库那一句, 读 id 与记录体四段 ----
    r_rec = breakpoint.fire_hit(g, BP_REC, do_cmd,
                        label="断[A] 645 0x1C %s(第 2 回) → 停在落库那一句 %s(读 id 与记录体)"
                              % (ev, breakpoint.text(BP_REC)),
                        vars=VARS_REC, crit="③", falsify=FALSIFY["③命中"])
    cmd_post = _byte0(watch.watch_vars(ser, ["g_RelayCmd"], tag="g_RelayCmd 触发后", wait=WAIT).get("g_RelayCmd"))

    # ---- 阶段四(第 3 步): 698 读本方向事件口, 与阶段一的基线比对 ----
    post = cmd_bank.read_event_row(ser, ev, 1, wait=WAIT)
    cnt_post = cmd_bank.event_area_count(ser, ev)

    # ---- 第 1 步那一停的记账(判据那一句读到的那一个量; 不认领判据条目) ----
    # `C_75Un` 是宏读不到, 读的是判据里另外那一个量 —— 它与 AA80 那边读到的 `g_CompFlg` 合起来
    #   才是整条判据; 判定结果由 ⑥ 认领。
    _gv = (r_gate or {}).get("vars") or {}
    _plc = _u32(_gv.get("g_PlcState")) if _gv.get("g_PlcState") else None
    ctx.J.add("判据那一句上的 g_PlcState", None,
              "TaskRelay.c:282-284 三条判据(模块未插 0xFFFFFFFF 或 停止通讯 低4位为0) && 75%%Un; "
              "C_75Un 是宏(TaskMetering.c:51)读不到, 本台展开 165.0V; 停点读到 g_PlcState=%s; "
              "判定结果= %s(见 ⑥ 那一条)"
              % ("0x%08X" % _plc if _plc is not None
                 else ("没停到" if r_gate is not None else "本次无调试会话"), why_gate),
              crit=None,
              falsify="判据不在 Run_TaskRelay 里(或改成别的电压门限)⇒ 那三行源码对不上这次实测")

    # ---- ① 命令方向落了没有(AA80 直读 g_RelayCmd[0]; 它在许可判定之前执行, 闭锁时照样能判) ----
    if cmd_post is None:
        ctx.J.add("继电器命令状态 g_RelayCmd[0] 落在本方向(%s)" % cmd_bank.RELAY_CMD_DIR[OP], None,
                  "AA80 读不到 g_RelayCmd(基线=%s) ⇒ 这一次没做成" % cmd_bank.n8txt(cmd_pre),
                  crit="①", falsify="不做这一次时无从判 —— 本记录不作为固件证据")
    else:
        ok1 = cmd_post >= cmd_bank.ST_RELAY_ON      # 合闸方向 = 不低于 ST_RelayOn(见 relay_on_criteria ①)
        ctx.J.add("继电器命令状态 g_RelayCmd[0] 落在本方向(%s)" % cmd_bank.RELAY_CMD_DIR[OP], ok1,
                  "g_RelayCmd[0] %s→%s" % (cmd_bank.n8txt(cmd_pre), cmd_bank.n8txt(cmd_post)),
                  crit="①", falsify=FALSIFY["①"])

    # ---- ⑤ 本方向事件口推进(仅判定通过时适用) ----
    adv, why_adv = cmd_bank.event_advanced(post, pre, t0)
    if allowed:
        ctx.J.add("『%s』口 触发后最新一条" % ev, adv, why_adv, crit="⑤", falsify=FALSIFY["⑤"])
    else:
        ctx.J.add("『%s』口 触发后最新一条" % ev, None,
                  "判定闭锁(电压<75%%Un), 本次%s按防误跳不产生 —— 本条本次不适用" % ev, crit="⑤",
                  falsify="不做这一次时无从判 —— 本记录不作为固件证据")

    # ---- ⑥ 判定与落库结果一致(两支都要判, 否则"闭锁"会变成没人核的免检通道) ----
    blk = _byte0(watch.watch_vars(ser, ["g_RelayBlk"], tag="g_RelayBlk 触发后", wait=WAIT).get("g_RelayBlk"))
    if allowed:
        ctx.J.add("动作许可判定与落库结果一致", None if adv is None else (adv is True),
                  "判定通 ⇒ 应落库; %s" % why_adv, crit="⑥", falsify=FALSIFY["⑥通"])
    else:
        blk_set = None if blk is None else (blk == 0xAA)
        ctx.J.add("动作许可判定与落库结果一致",
                  None if (adv is None or blk_set is None) else ((adv is False) and blk_set),
                  "判定闭锁 ⇒ 应不落库且 g_RelayBlk 置位; %s; g_RelayBlk=%s" % (why_adv, cmd_bank.n8txt(blk)),
                  crit="⑥", falsify=FALSIFY["⑥锁"])

    # ---- 第 3 步的另一半: 条数加 1 —— 两回各一条, 与阶段一记下的数比(不认领条目, 只进日志) ----
    ctx.J.add("『%s』口 记录条数 触发后" % ev, None,
              "触发前=%s | 第 1 回后=%s | 第 2 回后=%s(容量 %d 条)"
              % ("读不出" if cnt_pre is None else cnt_pre,
                 "读不出" if cnt_gate is None else cnt_gate,
                 "读不出" if cnt_post is None else cnt_post, REC_CAP),
              crit=None,
              falsify="许可判定通而条数不增(或增了不止 1) ⇒ 一次动作落的记录不止一条/没落")

    # ---- ② ③ ④ 断点那一次的记账 ----
    # ③ 由 `fire_hit` 那一条自己认领(它只答"停到没停到")。② 与 ④ 是**同一个停点**说明的另外两件事,
    #   从这里按同一份 `vars` 各补一条 —— 三者是一次取证的三面, 不是三次。
    # ⚠ 这里**不能**读 `r_rec["hit"]`: judge 的 record 只有 name/ok/detail/crit/obs/falsify/trig 七键,
    #   `hit` 在 `record()` 里就被拿去定 `ok` 并折进 `detail` 了(5-3 判据② 因此吃过一次假 FAIL)。
    _vals = (r_rec or {}).get("vars") or (r_rec or {}).get("at_vals") or {}
    if r_rec is not None:
        print("      断[A] 记录: ok=%s | %s" % (r_rec.get("ok"), r_rec.get("detail")))
        print("      停时读到: %s" % _vals)
        ctx.J.extend([r_rec])           # ③ 整条原样收下 —— 它自己带的 ok/name/detail 就是这一次的读数
        # ② 方向: 落库那一句已经按 action 把 id 二选一了, 所以 id 就是**固件判出来的方向**。
        #   DWARF 里枚举是强类型, 打出来就是枚举名, 所以直接断名字, 不硬编码数字。
        _id = str(_vals.get("id", "")).strip() if _vals else ""
        ctx.J.add("落库那次 id 指向『%s』" % ev,
                  None if not _id else (_id == REC_ID),
                  "id=%s(应 %s)" % (_id or "没停到, id 没读到", REC_ID),
                  crit="②", obs=judge.DEBUG, falsify=FALSIFY["②"])
        # ④ 操作者: 记录体 [6..9] 那 4 字节(操作者代码)。非空 = 固件没把入参丢掉。
        _f = _fields(_vals.get("buff"))
        _opr = _f.get("操作者代码")
        ctx.J.add("记录体里的操作者代码非空",
                  None if not _opr else (any(_opr)),
                  "buff[%d..%d]=%s" % (REC_OPR[0], REC_OPR[1] - 1,
                                     "没停到, 记录体没读到" if not _opr else _hex(_opr)),
                  crit="④", obs=judge.DEBUG,
                  falsify="固件把空操作者写进记录(丢掉入参)⇒ 那 4 字节全 0")
        # 第 2 步要读的四段一起留痕(不认领条目): 时刻 / 操作者代码 / 发生源 / 六种总电能。
        ctx.J.add("停时读到的那一条记录体", None,
                  "发生时刻[%d..%d]=%s | 操作者代码[%d..%d]=%s | 发生源[%d]=%s | "
                  "六种总电能[%d..%d]=%s | 本记录无结束时刻"
                  % (REC_HEAD[0], REC_HEAD[1] - 1, "没读到" if not _f else _hex(_f["时刻"]),
                     REC_OPR[0], REC_OPR[1] - 1, "没读到" if not _f else _hex(_f["操作者代码"]),
                     REC_SRC, "没读到" if not _f else "0x%02X" % _f["发生源"],
                     REC_ENE[0], REC_ENE[1] - 1, "没读到" if not _f else _hex(_f["六种总电能"])),
                  crit=None,
                  falsify="记录体里那四段的位置挪了(时刻不在 [0..5] / 电能不在 [11..40]) ⇒ "
                          "第 2 步的字段布局与固件不符")
        # 第 3 步的时标对照: 记录里那 6 字节按固件编码器还原, 与 698 读回的时标**逐字段比**。
        _ts_rec = _rec_ts(_f.get("时刻"))
        _ts_wire = (post or {}).get("ts")
        ctx.J.add("记录里的发生时刻与 698 读回的时标", None if (_ts_rec is None or not _ts_wire)
                  else (_ts_rec == _ts_wire),
                  "断点处 buff[%d..%d]=%s → 还原=%s | 698 读回 ts=%s"
                  % (REC_HEAD[0], REC_HEAD[1] - 1, "没读到" if not _f else _hex(_f["时刻"]),
                     _ts_rec or "还原不出", _ts_wire or "读不出"),
                  crit=None,
                  falsify="两者指的不是同一条记录 ⇒ 698 读回的那一条不是断点上写进去的那一条")
    else:
        why = ("本次无调试会话 ⇒ 断点观测这一次没做成" if not have_wb
               else "断点没停到落库那一句 ⇒ 这一次没做成"
                    "(判定闭锁时它本来就不该被调到, 那种台面上这一停不到属正常)")
        for _c, _t in (("③", "写库位置 Recd_CtrlRelay 落库那一句(指令路径)"),
                       ("②", "落库那次 id 指向『%s』" % ev),
                       ("④", "记录体里的操作者代码非空")):
            ctx.J.add(_t, None, why, crit=_c, obs=judge.DEBUG,
                      falsify="不做这一次时无从判 —— 本记录不作为固件证据")

    # ---- 阶段五(第 4 步): 最近 10 次 —— 连发 11 回, 条数应停在容量上、最早那条被顶掉 ----
    for _ in range(BURST):
        cmd_bank.ctrl_relay(ser, OP, delay=RELAY_DELAY, wait=BURST_WAIT)
    cnt_burst = cmd_bank.event_area_count(ser, ev)
    oldest = cmd_bank.read_event_row(ser, ev, REC_CAP, wait=WAIT)
    _replaced = None if (oldest is None or pre is None) else (oldest.get("seq") != pre.get("seq"))
    ctx.J.add("⑦ 连发 %d 回后『%s』封顶在容量 %d 上、最早那条被顶掉" % (BURST, ev, REC_CAP),
              None if cnt_burst is None else (cnt_burst == REC_CAP and _replaced is True),
              "连发 %d 回(容量 %d 条): 触发前=%s → 连发后=%s | 第 %d 条(最早那条)=%s | 顶掉了吗=%s"
              % (BURST, REC_CAP, "读不出" if cnt_pre is None else cnt_pre, cnt_burst, REC_CAP,
                 cmd_bank.rec_row_txt(oldest) if oldest else "读不出",
                 "没读数" if _replaced is None else ("是" if _replaced else "否")),
              crit="⑦",
              falsify="容量不是 %d(固件只留 9 条或 11 条)或顶掉的不是最早那条 ⇒ "
                      "第 %d 回读回的条数与最早那条的序号对不上" % (REC_CAP, BURST))

    if not allowed:
        ctx.J.note("未证: 『每次成功%s落一条』这半支 —— 台面动作许可判定不满足(%s), 固件按 DL/T698 防误跳闭锁、"
                   "本事件不产生。要证须把台面电压加到 ≥75%%Un(本台 ≈165V) 后重跑" % (ev, why_gate))
    ctx.J.note("未证: 『操作方式**正确**』里的『正确』半支 —— 本脚本只证记录体里的操作者代码非空, 且固件取的是"
               "参数区存着的操作者代码(非本帧带来); 要证『与本帧一致』须先把参数区写成已知值")


def _banner():
    return ("== 5-10 合闸事件 | 工程=%s 表号=%s ==\n"
            ".. 触发=645 0x1C 合闸(操作字0x1C)×2回 + 连发%d回; 白盒停 %s 读 %s, 停 %s 读 id 与记录体"
            % (CURRENT.PROJECT, CURRENT.TABLE_ADDR.hex().upper(), BURST,
               breakpoint.text(BP_GATE), " 与 ".join(VARS_GATE), breakpoint.text(BP_REC)))


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "5-10 合闸(命令进了/方向对/写库位置与入参/落一条/许可判定与结果一致)",
        cmd_bank.relay_on_criteria,
        name="5_10_relay_on",
        parts=[("5-10 合闸段", part_relay)],
        gdb=breakpoint, banner=_banner(),
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV)))
