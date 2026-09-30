# -*- coding: utf-8 -*-
"""在证什么: 从 485 口进来的帧被管理芯**按协议认对、并按原样收进缓冲** —— 645 帧走得到「认下 645」那一句,
  收帧缓冲起 len(frame) 个字节逐字节就是发出的那一帧; 698 帧原样进解析链且端口归属正确;
  而 CS 被改坏的乱帧被 645 支挡住, 且不改写派发状态 `g_ProtSt`。
会向表写什么: 只发**只读**帧(645 读日期时间 / 698 读表钟 / 同一帧改坏 CS), 外加一帧 `645.factory`
  进厂内(645/698 读在厂内态下才稳)。参数区与 RAM 一个字节都不动, 也不落任何记录。
  台面收尾走 `python scripts/_restore_all.py`。
跑法: `python project/tests/_test_7_3_rs485_dispatch.py`(真串口 + 真探针)。
结论怎么读: 账本末行「判据: 满足 n/N」, N=6; 退 0 = 通过 / 1 = 失败 / 2 = 未定论。
"""
from common import judge              # 观测种类常量 —— 本项六条判据全是断点观测
from common import trial              # 运行外壳: argv/日志/账本/串口/会话/收尾三连/退出码
from meterlib import cmd_bank         # 帧目录 + 语义动词积木箱(每步自带打印/判 PASS·FAIL)
from project import CURRENT           # 本工程画像: 表号/.out 路径/喂狗点(库不认表, 只有脚本认)
from swdbg import breakpoint          # 断点级白盒(停核 + 读函数内局部量); 没接探针会自动降级
from swdbg import gdbinit             # 整片 .out 地图: 开会话后解一次, 之后断点/注入点只查它

# ---- 子项参数(纯数据; 要改的在这里改) ----
HIT_WAIT = 3.0      # 每一帧发出去之后, 等断点命中的最长秒数
JOIN_EXTRA = 8.0    # 触发线程收尾的额外宽限(帧发完到线程退干净)
JUNK_WAIT = 2.0     # 乱帧那一帧的发送等待; 它同时是**留给固件重同步的时间**, 见 ③ 那一步的说明

# ---- 断点: 模块级**字面量元组** ----
# `scripts/_check_anchors.py` 用 `ast.literal_eval` 抠 `BP_<X>`/`VARS_<X>` 两个名字逐条对源码核
# (那个变量在断点那一行赋过值没有); 写成 `cmd_bank.SOMETHING` 那种表达式就抠不出来, 等于没人核过。
# `VARS_<X>` 只列**顶层名** —— "按 port 取哪条缓冲""取几个字节"是协议知识, 由库的 `vars_bytes()` 解。
BP_698 = ("DLT698Link.c", 283)            # Analyse_698Prot 函数体首条可执行语句 = 每来一帧命中一次
VARS_698 = ("port", "pFrame", "g_ProtSt")     # 入参 2 个(端口归属 / 帧文) + 上一帧的派发结果
BP_645 = ("Communicate.c", 338)           # `g_ProtSt[port] = 0` —— 只在 `sta645 == ST_NeedAck` 支里
VARS_645 = ("port",)                      # 端口 —— 由它认得这帧归哪个 485 口(② 的读数归 ② 那个停点)
BP_645_RX = ("prev", "Comm_Service", "Analyse_645Prot", 1)        # `Analyse_645Prot` 调用行 —— 请求帧还没被应答原地覆盖
VARS_645_RX = ("port", "g_ComAdr", "g_RS485Buf")   # 端口 / 收帧长度 / 收帧缓冲


def _add(J, label, ok, why, crit, obs=judge.DEBUG):
    """记一条判据(`falsify` 按 `crit` 从库里的单点取)+ 打一行三态。"""
    J.add(label, ok, why, crit=crit, falsify=cmd_bank.RS485_FALSIFY[crit], obs=obs)
    print("   [%s] %s —— %s" % ({True: "PASS", False: "FAIL", None: "TBD"}[ok], label, why))


def _no_session(J):
    """没有断点会话 ⇒ 六条一条都做不成 —— 逐条记 `ok=None`(不做成), 不记失败。"""
    for crit, what in (("①", "645 帧认下"),
                       ("②", "645 帧取数正确"),
                       ("③", "派发认对协议(645 支)"),
                       ("④", "698 帧原样进出与端口归属"),
                       ("⑤", "乱帧被 645 支挡住"),
                       ("⑥", "乱帧不改派发状态")):
        _add(J, "断[B/C] %s %s" % (crit, what), None,
             "本次没有断点会话(没接探针) ⇒ 这一条没做成", crit=crit)


def part_dispatch(ctx):
    """①②③④⑤⑥ —— 一轮取全部条目。

    ⚠ 观测一律 attach, **禁用 launch**(launch 会复位表, RAM 态清零) —— 见 CLAUDE.md 调试链纪律 1。
    ⚠ 四个断点都是**发帧触发**、`with_trigger` 当场挂当场撤(它收 `(文件,行号)` 元组时, 命中与
      没命中**两条路都撤**) —— **不是**预挂: 预挂上又没人等在等命中时, 它自己会把核撂停,
      其后每条串口帧整帧无应答(与"表死机"一模一样, 见 CLAUDE.md 调试链纪律 6)。

    四帧的顺序**就是判据本身**: 每一步读的是**上一帧**留在固件里的状态。
    """
    J, ser = ctx.J, ctx.ser
    print("\n===== 7-3 RS485 帧进出与派发 (485 口注入 645/698/乱帧) =====")

    # ---- 前置: 进厂内 —— 645/698 读在厂内态下才稳 ----
    cmd_bank.enter_factory(ser)

    ctx.session()
    g = ctx.g
    if g is not None:
        # 整片 .out 解一次(停核遍历, 内部喂狗, 收尾放行) —— 之后一律查这张图
        gdbinit.build(g)
    if g is None:
        # ⚠ 本项六条判据**全是断点观测**: 帧确实是发出去的, 但"它进没进解析链""被哪条分支派的"
        #   "乱帧有没有改写派发状态"这三件事串口都答不出(串口只看得见应答, 看不见走的是哪条分支;
        #   乱帧那一帧干脆连应答都没有) ⇒ 没有会话就一条都做不成, 也没有可降级的黑盒替身。
        _no_session(J)
        return

    # ---- 四帧: 顺序就是判据本身(见函数 docstring) ----
    f645 = cmd_bank.read_time645_dt()                     # 645 读日期时间(单播本表地址)
    raw645 = bytes(f645).lstrip(b"\xfe")                  # 收帧层从第一个 0x68 起才往缓冲里存, 前导 FE 不算帧体
    f698 = cmd_bank.frame_698(cmd_bank.build_read_apdu(0x03, cmd_bank.DISP_OAD))   # 698 读表钟
    fjunk = cmd_bank.corrupt_cs(f645)                     # 第 3 帧乱帧 = 第 1 帧的同一条, 只把 CS 改坏
    print("   第 1 帧 645 %d 字节(含 4 个前导 FE): %s" % (len(f645), bytes(f645).hex(" ").upper()))
    print("   第 2/4 帧 698 %d 字节: %s" % (len(f698), bytes(f698).hex(" ").upper()))
    print("   第 3 帧乱帧(CS 改坏) %d 字节: %s" % (len(fjunk), bytes(fjunk).hex(" ").upper()))

    # 停住时要读的量。**逐字节列 key 而不是读整数组** —— `kwh_num` 吃的就是
    # `-data-evaluate-expression` 的单值文本, 整数组的文本形态是另一回事。
    keys645 = tuple("g_RS485Buf[%d]" % i for i in range(len(raw645)))
    keys698 = tuple("pFrame[%d]" % i for i in range(len(f698)))

    # ---- 第 1 帧 645(第一次发): ② 收帧缓冲取数正确 ----
    # ⚠ 这一窗的停点是**解析入口**(`Analyse_645Prot` 的调用行), **不是** ① 那个 `:338` —— 实测
    #   (7-3 首跑): `Analyse_645Prot` 把应答**原地写回同一个缓冲**(`pBuff = g_RS485Buf`,
    #   Communicate.c:318; `:622` 的发送口也从它取), 走到 `:338` 时缓冲里装的已经是**应答**,
    #   与发出的请求永远不等。那是读点错不是固件错 —— 拿它判固件, 任何正确固件都不满足。
    r0 = g.with_trigger(BP_645_RX, cmd_bank.send_rs485_645, ser, f645,
                        timeout=HIT_WAIT, _join=JOIN_EXTRA,
                        _vars=("port", "g_ComAdr[port]") + keys645,
                        _pair_name="7-3 第 1 帧 645 收帧缓冲(解析入口)")
    h0, v0 = r0.get("hit"), r0.get("vars") or {}
    if r0.get("error") is not None:
        print("   !! 触发那一侧抛了异常(帧可能没发出去): %s" % r0.get("error"))
    if h0 is None:
        _add(J, "断[C] ② 645 帧取数正确", None,
             "645 帧发出后 %.1fs 内没等到 %s 命中 ⇒ 本次没做成(收帧缓冲无从读起)"
             % (HIT_WAIT, breakpoint.text(BP_645_RX)), crit="②")
    else:
        print("   断[D] 收帧停在 %s | port=%s | g_ComAdr[port]=%s"
              % (h0.where(), v0.get("port"), v0.get("g_ComAdr[port]")))
        got645 = cmd_bank.vars_bytes(v0, keys645)
        print("   645 收帧缓冲起 %d 字节 = %s"
              % (len(raw645), got645.hex(" ").upper() if got645 else "(读不全)"))
        if cmd_bank.kwh_num(v0.get("port")) != cmd_bank.DISP_PORT_485:
            _add(J, "断[C] ② 645 帧取数正确", None,
                 "停住的那一帧不归 485 口(port=%s) ⇒ 本次不据此判固件" % (v0.get("port"),), crit="②")
        elif got645 is None:
            _add(J, "断[C] ② 645 帧取数正确", None,
                 "g_RS485Buf 这一次没读全 ⇒ 本次没做成", crit="②")
        else:
            same2 = (got645 == raw645)
            _add(J, "断[C] ② 645 帧取数正确", same2,
                 "收帧缓冲 %d 字节 %s; 发出的是(去 FE) %s; 逐字节%s; g_ComAdr[port]=%s(帧长 %d); "
                 "读点 %s(解析入口, 应答还没写回缓冲)"
                 % (len(got645), got645.hex(" ").upper(), raw645.hex(" ").upper(),
                    "全等" if same2 else "**不等**", v0.get("g_ComAdr[port]"), len(raw645),
                    h0.where()), crit="②")

    # ---- 第 1 帧 645(再发一次): ① 认下 ----
    r1 = g.with_trigger(BP_645, cmd_bank.send_rs485_645, ser, f645,
                        timeout=HIT_WAIT, _join=JOIN_EXTRA, _vars=("port",),
                        _pair_name="7-3 第 1 帧 645 读日期时间")
    h1, v1 = r1.get("hit"), r1.get("vars") or {}
    if r1.get("error") is not None:
        print("   !! 触发那一侧抛了异常(帧可能没发出去): %s" % r1.get("error"))
    if h1 is None:
        # 半途中止: 已经拿到读数的那一条(②)不许被覆盖成"没做成" —— 那是拿"没做成"盖住"做成了"。
        for crit, what in (("①", "645 帧认下"),
                           ("③", "派发认对协议(645 支)"),
                           ("④", "698 帧原样进出与端口归属"),
                           ("⑤", "乱帧被 645 支挡住"),
                           ("⑥", "乱帧不改派发状态")):
            _add(J, "断[B/C] %s %s" % (crit, what), None,
                 "半途中止: 645 帧发出后 %.1fs 内没等到 %s 命中"
                 % (HIT_WAIT, breakpoint.text(BP_645)), crit=crit)
        return
    print("   断[C] 第 1 帧停在 %s | port=%s" % (h1.where(), v1.get("port")))
    _add(J, "断[C] ① 645 帧认下", True,
         "%s 在 %.1fs 内命中(停在 %s); 注入帧 %d 字节(去 FE 后 %d 字节)"
         % (breakpoint.text(BP_645), HIT_WAIT, h1.where(), len(f645), len(raw645)), crit="①")

    # ---- 第 2 帧 698: ③ 派发认对协议 + ④ 原样进出(读的都是第 1 帧跑完时留下的状态) ----
    r2 = g.with_trigger(BP_698, cmd_bank.send_rs485_698, ser, f698, cmd_bank.DISP_OAD,
                        timeout=HIT_WAIT, _join=JOIN_EXTRA,
                        _vars=("port", "len", "g_ProtSt[port]") + keys698,
                        _pair_name="7-3 第 2 帧 698 读表钟")
    h2, v2 = r2.get("hit"), r2.get("vars") or {}
    if r2.get("error") is not None:
        print("   !! 触发那一侧抛了异常(帧可能没发出去): %s" % r2.get("error"))
    if h2 is None:
        print("   第 2 帧发出后 %.1fs 内没等到 %s 命中" % (HIT_WAIT, breakpoint.text(BP_698)))
        for crit, what in (("③", "派发认对协议(645 支)"),
                           ("④", "698 帧原样进出与端口归属")):
            _add(J, "断[B] %s %s" % (crit, what), None,
                 "第 2 帧 698 发出后没等到 %s 命中 ⇒ 本次没做成(第 1 帧的派发结果无从读起)"
                 % breakpoint.text(BP_698), crit=crit)
    else:
        print("   断[B] 第 2 帧 停在 %s | port=%s | len=%s | g_ProtSt[port]=%s"
              % (h2.where(), v2.get("port"), v2.get("len"), v2.get("g_ProtSt[port]")))
        proto2 = cmd_bank.kwh_num(v2.get("g_ProtSt[port]"))
        _add(J, "断[B] ③ 派发认对协议(645 支)", proto2 == cmd_bank.DISP_PROTO_645,
             "第 2 次停住时 g_ProtSt[%s]=%s(装的是第 1 帧 645 跑完时固件写下的派发结果); "
             "0=645帧 1=698帧 ⇒ %s"
             % (v2.get("port"), v2.get("g_ProtSt[port]"),
                "固件把它当 645 派发了" if proto2 == cmd_bank.DISP_PROTO_645
                else "**读回 %s** —— 没被认作 645" % (proto2,)), crit="③")

        got698 = cmd_bank.vars_bytes(v2, keys698)
        len2 = cmd_bank.kwh_num(v2.get("len"))
        if got698 is None:
            _add(J, "断[B] ④ 698 帧原样进出与端口归属", None,
                 "入参 pFrame 这一次没读全 ⇒ 本次没做成", crit="④")
        else:
            ok4 = (cmd_bank.kwh_num(v2.get("port")) == cmd_bank.DISP_PORT_485
                   and got698 == bytes(f698) and len2 == len(f698))
            _add(J, "断[B] ④ 698 帧原样进出与端口归属", ok4,
                 "port=%s(期望 %d) | len=%s(期望 %d) | 解析口拿到的 %d 字节 %s, 逐字节%s"
                 % (v2.get("port"), cmd_bank.DISP_PORT_485, v2.get("len"), len(f698), len(got698),
                    got698.hex(" ").upper(),
                    "全等" if got698 == bytes(f698) else "**不等**"), crit="④")

    # ---- 第 3 帧 乱帧: ⑤ `:338` 不命中 ----
    # `JUNK_WAIT` 那一等是**留给固件重同步的时间**: 这帧会被判 `ST_ProtErr`, Communicate.c:371
    # 那一段要腾出手来找下一个 0x68 重新对齐, 期间通道不在"收帧等待"态。发完就撤, 下一帧会被
    # 那截残帧粘住 —— 所以多等一会儿, 让那套重同步跑完。
    r3 = g.with_trigger(BP_645, cmd_bank.send_rs485_junk, ser, fjunk, JUNK_WAIT,
                        timeout=HIT_WAIT, _join=JOIN_EXTRA, _vars=("port",),
                        _pair_name="7-3 第 3 帧 乱帧(CS 改坏)")
    h3 = r3.get("hit")
    if r3.get("error") is not None and h3 is None:
        # 触发线程自己抛了(如串口写失败) —— 那这帧**根本没发出去**, 不命中就不是固件挡住的
        _add(J, "断[C] ⑤ 乱帧被 645 支挡住", None,
             "乱帧那一侧触发报错(%s), 这帧可能没发出去 ⇒ 本次没做成" % (r3.get("error"),), crit="⑤")
    else:
        _add(J, "断[C] ⑤ 乱帧被 645 支挡住", h3 is None,
             "%s 在 %.1fs 内%s; 乱帧 = 第 1 帧的同一条 645 帧, 只把 CS(倒数第二字节)改成 %02X"
             % (breakpoint.text(BP_645), HIT_WAIT, "未命中" if h3 is None else "**命中了**",
                bytes(fjunk)[-2]), crit="⑤")

    # ---- 第 4 帧 698: ⑥ 乱帧没改派发状态(读的是第 2 帧留下的值) ----
    r4 = g.with_trigger(BP_698, cmd_bank.send_rs485_698, ser, f698, cmd_bank.DISP_OAD,
                        timeout=HIT_WAIT, _join=JOIN_EXTRA, _vars=("port", "g_ProtSt[port]"),
                        _pair_name="7-3 第 4 帧 698 读表钟(乱帧之后的见证帧)")
    h4, v4 = r4.get("hit"), r4.get("vars") or {}
    if h4 is None:
        _add(J, "断[B] ⑥ 乱帧不改派发状态", None,
             "第 4 帧 698 发出后 %.1fs 内没等到 %s 命中 ⇒ 本次没做成(乱帧是否改写了派发状态"
             "无从读起; 固件在乱帧之后要重同步一次, 也可能就是那一下没接上)"
             % (HIT_WAIT, breakpoint.text(BP_698)), crit="⑥")
    else:
        proto4 = cmd_bank.kwh_num(v4.get("g_ProtSt[port]"))
        _add(J, "断[B] ⑥ 乱帧不改派发状态", proto4 == cmd_bank.DISP_PROTO_698,
             "第 4 次停住时 g_ProtSt[%s]=%s(装的是第 2 帧 698 跑完时写下的 1; 乱帧若被误派发"
             "成 645 就会在这儿读回 0) ⇒ %s"
             % (v4.get("port"), v4.get("g_ProtSt[port]"),
                "乱帧没改它" if proto4 == cmd_bank.DISP_PROTO_698
                else "**乱帧把它改成了 %s**" % (proto4,)), crit="⑥")


def _banner():
    return "== 7-3 485·管理芯帧进出与派发 | 工程=%s 表号=%s ==" % (
        CURRENT.PROJECT, CURRENT.TABLE_ADDR.hex().upper())


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "7-3 485·管理芯帧进出与派发", cmd_bank.rs485_dispatch_criteria,
        name="7_3_rs485_dispatch", parts=[("7-3 485 帧进出/派发/乱帧段", part_dispatch)],
        gdb=breakpoint, banner=_banner(),
        # ⚠ **本脚本只有断点观测**: 六条判据读的全是停住那一刻的入参/全局量 —— 四帧是**发出去**的,
        #   但"它进没进解析链""被哪条分支派的""乱帧有没有改写派发状态"这三件事串口都答不出
        #   (串口只看得见应答, 看不见它走的是哪条分支; 乱帧那一帧干脆连应答都没有)。
        #   不照实写就会凭空多出一条"串口观测被跳过了"(`_test_4_1_freeze_ping.py` 是这条规矩的另一半:
        #   纯黑盒的那份照实写 `obs=(JS.SERIAL,)`)。
        obs=(judge.DEBUG,),
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV)))
