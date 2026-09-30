# -*- coding: utf-8 -*-
"""在证什么: 载波主站那一侧的帧从 485 口进来后, 管理芯把它**中继给计量芯**、再把计量芯的应答
  转回请求来的那一口 —— 五个停点上的入参与缓冲逐字节对上(入口归属 / 转发落点 / 透传帧文 / 回程)。
会向表写什么: 只发**只读**帧 —— 中继 698(读表钟)、中继 645(应答形态) 各若干次, 外加一帧
  `645.factory` 进厂内(645/698 读在厂内态下才稳)。参数区与 RAM 一个字节都不动, 也不落任何记录。
  台面收尾走 `python scripts/_restore_all.py`。
跑法: `python project/tests/_test_7_2_plc_relay.py`(真串口 + 真探针)。
结论怎么读: 账本末行「判据: 满足 n/N」, N=7; 退 0 = 通过 / 1 = 失败 / 2 = 未定论。
"""
from common import judge              # 观测种类常量 —— 本项七条判据全是断点观测
from common import trial              # 运行外壳: argv/日志/账本/串口/会话/收尾三连/退出码
from common.portsel import send_frame  # 发帧原语 —— 前置自证那一帧要自己发(它不是判据的一步)
from meterlib import cmd_bank         # 帧目录 + 语义动词积木箱(每步自带打印/判 PASS·FAIL)
from project import CURRENT           # 本工程画像(库不认表, 只有脚本认)
from swdbg import breakpoint          # 断点级白盒; 没接探针会自动降级
from swdbg import gdbinit             # 整片 .out 地图: 开会话后解一次, 之后断点/注入点只查它

# ---- 子项参数(纯数据; 要改的在这里改) ----
HIT_WAIT = 3.0      # 每一帧发出去之后, 等断点命中的最长秒数
BACK_WAIT = 6.0     # 回程那一帧的等待 —— 它要走完"转发走→计量芯答→转回 485", 比别的帧长
JOIN_EXTRA = 8.0    # 触发线程收尾的额外宽限(帧发完到线程退干净)

# ---- 断点: 模块级**字面量元组** ----
# `scripts/_check_anchors.py` 用 `ast.literal_eval` 抠 `BP_<X>`/`VARS_<X>` 两个名字逐条对源码核
# (那个变量在断点那一行赋过值没有); 写成 `cmd_bank.SOMETHING` 那种表达式就抠不出来, 等于没人核过。
# `VARS_<X>` 只列**顶层名** —— "按 port 取哪条缓冲""取几个字节"是协议知识, 由库的 `vars_bytes()` 解。
BP_698 = ("prev", "Analyse_698Prot", "CMD_Relay_Caculator_And_Master", 1)      # `CMD_Relay_Caculator_And_Master(port, frameLen+2, 0)` 那一句
VARS_698 = ("port", "g_AddrLog")              # 端口归属 + 地址特征判出来的逻辑地址位
BP_645 = ("DLT645Link.c", 484)            # 0x91/0x94/0xE1/0xE4 四档共用体的首条可执行语句
VARS_645 = ("port", "stAddr")                 # 端口归属 + 地址判定结果
BP_RELAY = ("Communicate.c", 1129)        # `Start_Relay_Frame` 体首条可执行 = `g_PreNum[port] = 0;`
VARS_RELAY = ("port", "Len")                  # 转发落点(哪个口) + 转过去的字节数
BP_BACK = ("prev", "CMD_Relay_Caculator_And_Master", "Start_Relay_Frame", 3)   # `Start_Relay_Frame(PT_485_M, Len);` —— 回程回 485 那一支
VARS_BACK = ("Len", "port")                   # 推回 485 的字节数 + 这一停所属的端口(=PT_UARTM)

# 七条判据的 (crit, F 列字母, 名) —— `_no_session` / `_abort` 逐条记 `ok=None` 时用。
# ⚠ 每条名里的 `断[X]` **照着 F 列那四个字母写**(A=DLT698Link.c:307 中继入口, B=DLT645Link.c:484
#   透传入口, C=Communicate.c:1129 转发动作, D=Communicate.c:1108 回程)—— 判据名与 F 列说的是
#   同一件事, 别在这儿另编一套字母。
_ITEMS = (("①", "A", "698 中继入口命中并归 485 口"),
          ("②", "C", "698 转发动作发生"),
          ("③", "C", "698 转发帧文正确"),
          ("④", "B", "645 透传入口命中并归 485 口"),
          ("⑤", "C", "645 透传帧文正确"),
          ("⑥", "D", "回程回到请求来向那口"),
          ("⑦", "D", "回程推的字节数对得上那条应答帧"))


def _add(J, label, ok, why, crit, obs=judge.DEBUG):
    """记一条判据(`falsify` 按 `crit` 从库里的单点取)+ 打一行三态。"""
    J.add(label, ok, why, crit=crit, falsify=cmd_bank.PLC_RELAY_FALSIFY[crit], obs=obs)
    print("   [%s] %s —— %s" % ({True: "PASS", False: "FAIL", None: "TBD"}[ok], label, why))


def _blame(J, reason):
    """七条逐条记 `ok=None`(没做成) + 打一行 —— 半途中止/没会话时用, 不记失败。"""
    print("   [白盒] %s" % reason)
    for crit, tag, what in _ITEMS:
        _add(J, "断[%s] %s %s" % (tag, crit, what), None, reason, crit=crit)


def part_relay(ctx):
    """①②③④⑤⑥⑦ —— 一轮取全部条目。

    ⚠ 观测一律 attach, **禁用 launch**(launch 会复位表, RAM 态清零) —— 见 CLAUDE.md 调试链纪律 1。
    ⚠ 四个断点都是**发帧触发**、`with_trigger` 当场挂当场撤(命中与没命中**两条路都撤**) ——
      **不是** `ctx.bp()` 预挂: 预挂上又没人等在等命中时, 它自己会把核撂停, 其后每条串口帧整帧
      无应答(与"表死机"一模一样)。这一条对 `:1129` 与 `:1108` 尤其要紧: 两处都在转发路径上,
      表只要在转帧就会撞上它们。

    五帧的顺序**就是判据本身**: 前三帧各停一处看入口与转发落点, 末一帧等计量芯的应答走回程。
    """
    J, ser = ctx.J, ctx.ser
    print("\n===== 7-2 管理芯中继透传 (485 口注入转发给计量芯的帧) =====")

    # ---- 前置: 进厂内 —— 645/698 读在厂内态下才稳 ----
    cmd_bank.enter_factory(ser)

    ctx.session()
    g = ctx.g
    if g is not None:
        # 整片 .out 解一次(停核遍历, 内部喂狗, 收尾放行) —— 之后一律查这张图
        gdbinit.build(g)
    if g is None:
        # ⚠ 本项七条判据**全是断点观测**: 五帧确实发得出去, 但"它走的是中继那条路不是管理芯
        #   自己应答""转过去的字节对不对""转到了哪个口"这三件事串口都答不出(串口只看得见
        #   回没回帧, 看不见帧在管理芯里怎么走的) ⇒ 没有会话就一条都做不成, 也没有黑盒替身。
        _blame(J, "本次没有断点会话(没接探针) ⇒ 判①②③④⑤⑥⑦ 没做成")
        return

    # ---- 五帧的帧文(协议知识在库里: 哪条地址特征走中继、645 用哪个功能码) ----
    oad = cmd_bank.DISP_OAD
    f698 = cmd_bank.relay_698_frame(oad)
    f645 = cmd_bank.relay_645_frame()
    raw645 = bytes(f645).lstrip(b"\xfe")      # 收帧层丢掉 4 个前导 FE(与 7-3 同一件事)
    # ⑤ 的期望 = **收到的线上字节原样**(去 FE 那一份), 不是"数据域再加一次 0x33":
    # 解析口减过一次、转发前 DLT645Link.c:490 又加回去 —— 一减一加回到原样。⚠ 拿"线上数据域再
    # +0x33"当期望是**错的口径**, 那等于要求固件多加一次, 会把合法的原样透传判成 FAIL。
    exp645 = raw645
    print("   第 1/2/5 帧中继 698 %d 字节: %s" % (len(f698), bytes(f698).hex(" ").upper()))
    print("   第 3/4 帧中继 645 %d 字节(含 4 个前导 FE): %s"
          % (len(f645), bytes(f645).hex(" ").upper()))
    print("   645 转给计量芯前应是(去 FE, 与收到的线上字节原样) %d 字节: %s"
          % (len(exp645), exp645.hex(" ").upper()))

    # 停住时要读的量。**逐字节列 key 而不是读整数组** —— `kwh_num` 吃的就是
    # `-data-evaluate-expression` 的单值文本, 整数组的文本形态是另一回事。
    # `ukeys` 按**两条帧里更长的那个**取: 698 那条 25 字节、645 那条去 FE 后 16 字节, 一趟读够。
    pkeys698 = tuple("pFrame[%d]" % i for i in range(len(f698)))
    pkeys645 = tuple("pFrame[%d]" % i for i in range(len(raw645)))
    ukeys = tuple("g_UARTMBuf[%d]" % i for i in range(max(len(f698), len(raw645))))
    # 回程那一停读的是**回 485 的目标缓冲**(`g_RS485Buf`), 不是计量芯口那条 —— 地址 `:1100`
    # 就把它从 `g_UARTMBuf` 灌过去了。
    bkeys = tuple("g_RS485Buf[%d]" % i for i in range(cmd_bank.DISP_BUF_DUMP))

    # ---- 前置: 链路自证(不认领任何条目, 只为把"没命中"里的台面那一半摘出去) ----
    # 一帧**管理芯自己应答**的 698 读表钟(地址特征 = 管理芯本表, 逻辑地址位 0 ⇒ 不走中继那一支)。
    # 它答得上, 就同时证死三件事: 表在跑、485 通、管理芯 698 栈工作 —— 此后各条中继帧的断点
    # "没命中"就只剩下"固件没走那条路"一种解释, 于是那记 `False`(之后每一条都按这条尺子判)。
    _ctl = cmd_bank.frame_698(cmd_bank.build_read_apdu(0x03, oad))
    print("\n   前置 链路自证帧(管理芯自答, 不判条目) %d 字节: %s"
          % (len(_ctl), _ctl.hex(" ").upper()))
    _rx = bytes(send_frame(ser, _ctl, wait=HIT_WAIT, tag="relay_preflight",
                           what="前置链路自证: 管理芯自答的 698 读表钟") or b"")
    # ⚠ 判"答了没有"**先剥前导 0xFE**: 线上那条应答是 `FE FE FE FE 68 …`(4 个前导里 1 个由
    #   `OpenTx_UART4` 直接写、3 个由发送空中断写)。拿 `startswith(b"\x68")` 判, 答得再好也判成
    #   "没成" —— 实测踩过: 表回了 39 字节的合格应答, 整项却被压成未定论。
    if not _rx.lstrip(b"\xfe").startswith(b"\x68"):
        _blame(J, "前置链路自证没成: 管理芯对自己应答的 698 读表钟帧回的是 %s ⇒ 表没在跑 / "
                  "485 不通 / 管理芯 698 栈不工作 —— 这一轮的中继判据一条都判不了"
                  % (_rx.hex(" ").upper()[:24] or "(一个字都没回)"))
        return

    # ---- 第 1 帧 698: ① 中继入口 ----
    r1 = g.with_trigger(BP_698, cmd_bank.send_relay_698, ser, f698, oad, timeout=HIT_WAIT,
                        _join=JOIN_EXTRA, _vars=VARS_698 + pkeys698,
                        _pair_name="7-2 第 1 帧 中继 698")
    h1, v1 = r1.get("hit"), r1.get("vars") or {}
    if h1 is None and r1.get("error") is None:
        # 前置已过 ⇒ 不是台面问题 ⇒ 这帧没走中继那一支, 判不通过(不记"没做成")。
        _add(J, "断[A] ① 698 中继入口命中并归 485 口", False,
             "中继 698 帧(地址特征 %02X)发出后 %.1fs 内 %s 没命中 ⇒ 这帧**没被认成"
             "转发给计量芯的帧**(逻辑地址位没取到, 或在更早的地方就被丢掉/自己应答了)"
             % (CURRENT.MANAGE_AF | cmd_bank.RELAY_AF_LOGIC, HIT_WAIT, breakpoint.text(BP_698)),
             crit="①")
    elif h1 is None:
        _add(J, "断[A] ① 698 中继入口命中并归 485 口", None,
             "触发那一侧抛了异常, 帧可能没发出去: %s ⇒ 本次没做成" % r1.get("error"),
             crit="①")
    else:
        print("   [白盒] 断[A] 第 1 帧 停在 %s | port=%s | g_AddrLog=%s"
              % (h1.where(), v1.get("port"), v1.get("g_AddrLog")))
        got1 = cmd_bank.vars_bytes(v1, pkeys698)
        print("   [白盒] 解析层拿到的 %d 字节 = %s"
              % (len(f698), got1.hex(" ").upper() if got1 else "(读不全)"))
        port1 = cmd_bank.kwh_num(v1.get("port"))
        _add(J, "断[A] ① 698 中继入口命中并归 485 口", port1 == cmd_bank.DISP_PORT_485,
             "%s 在 %.1fs 内命中(停在 %s); 入参 port=%s(期望 %d=PT_485_M); "
             "g_AddrLog=%s(置位就该是 1); 这帧的地址特征 = %02X"
             % (breakpoint.text(BP_698), HIT_WAIT, h1.where(), v1.get("port"),
                cmd_bank.DISP_PORT_485, v1.get("g_AddrLog"),
                CURRENT.MANAGE_AF | cmd_bank.RELAY_AF_LOGIC),
             crit="①")

    # ---- 第 2 帧 698: ② 转发动作 + ③ 转发帧文 ----
    r2 = g.with_trigger(BP_RELAY, cmd_bank.send_relay_698, ser, f698, oad, timeout=HIT_WAIT,
                        _join=JOIN_EXTRA, _vars=VARS_RELAY + ukeys,
                        _pair_name="7-2 第 2 帧 中继 698(转发落点)")
    h2, v2 = r2.get("hit"), r2.get("vars") or {}
    if h2 is None:
        print("   [白盒] 第 2 帧发出后 %.1fs 内没等到 %s 命中"
              % (HIT_WAIT, breakpoint.text(BP_RELAY)))
        _o2 = None if r2.get("error") else False
        _d2 = ("触发那一侧抛了异常, 帧可能没发出去: %s ⇒ 本次没做成" % r2.get("error")
               if _o2 is None else
               "第 2 帧(同一条中继 698)发出后 %.1fs 内 %s 没命中 ⇒ 转发动作**没有被调到**"
               " —— 要么 ① 那一支根本没往下走, 要么 %s 的落点条件不成立(计量芯口不在空闲态)"
               % (HIT_WAIT, breakpoint.text(BP_RELAY), breakpoint.text(BP_698)))
        for _c, _t in (("②", "698 转发动作发生"), ("③", "698 转发帧文正确")):
            _add(J, "断[C] %s %s" % (_c, _t), _o2, _d2, crit=_c)
    else:
        print("   [白盒] 断[C] 第 2 帧 停在 %s | port=%s | Len=%s"
              % (h2.where(), v2.get("port"), v2.get("Len")))
        port2, len2 = cmd_bank.kwh_num(v2.get("port")), cmd_bank.kwh_num(v2.get("Len"))
        _add(J, "断[C] ② 698 转发动作发生",
             port2 == cmd_bank.RELAY_PORT_METER and len2 == len(f698),
             "转发动作被调到(落在 %s), 入参 port=%s(期望 %d=PT_UARTM 计量芯口) / Len=%s(期望 %d)"
             % (h2.where(), v2.get("port"), cmd_bank.RELAY_PORT_METER, v2.get("Len"), len(f698)),
             crit="②")

        gotu = cmd_bank.vars_bytes(v2, ukeys[:len(f698)]) if len2 == len(f698) else None
        if gotu is None:
            _add(J, "断[C] ③ 698 转发帧文正确", None,
                 "计量芯口缓冲这一次没读全(或 Len 不是 %d, 读的区间没对齐) ⇒ 本次没做成"
                 % len(f698), crit="③")
        else:
            same3 = (gotu == bytes(f698))
            _add(J, "断[C] ③ 698 转发帧文正确", same3,
                 "计量芯口 buff 起 %d 字节 %s; 发出的是 %s; 逐字节%s"
                 % (len(gotu), gotu.hex(" ").upper(), bytes(f698).hex(" ").upper(),
                    "全等" if same3 else "**不等**"), crit="③")

    # ---- 第 3 帧 645: ④ 透传入口 ----
    r3 = g.with_trigger(BP_645, cmd_bank.send_relay_645, ser, f645, timeout=HIT_WAIT,
                        _join=JOIN_EXTRA, _vars=VARS_645 + pkeys645,
                        _pair_name="7-2 第 3 帧 中继 645")
    h3, v3 = r3.get("hit"), r3.get("vars") or {}
    if h3 is None and r3.get("error") is None:
        _add(J, "断[B] ④ 645 透传入口命中并归 485 口", False,
             "中继 645 帧(CMD 0x%02X, 地址=本表地址)发出后 %.1fs 内 %s 没命中 ⇒ 这帧**没进"
             " 0x91/0x94/0xE1/0xE4 四档共用那段体**(功能码没分派到它, 或地址没判成 ADR_ABS)"
             % (cmd_bank.RELAY_CMD_645_REPLY, HIT_WAIT, breakpoint.text(BP_645)), crit="④")
    elif h3 is None:
        _add(J, "断[B] ④ 645 透传入口命中并归 485 口", None,
             "触发那一侧抛了异常, 帧可能没发出去: %s ⇒ 本次没做成" % r3.get("error"), crit="④")
    else:
        got3 = cmd_bank.vars_bytes(v3, pkeys645)
        print("   [白盒] 断[B] 第 3 帧 停在 %s | port=%s | stAddr=%s"
              % (h3.where(), v3.get("port"), v3.get("stAddr")))
        print("   [白盒] 解析口拿到的 %d 字节 = %s"
              % (len(raw645), got3.hex(" ").upper() if got3 else "(读不全)"))
        port3 = cmd_bank.kwh_num(v3.get("port"))
        _add(J, "断[B] ④ 645 透传入口命中并归 485 口", port3 == cmd_bank.DISP_PORT_485,
             "%s 在 %.1fs 内命中(停在 %s); 入参 port=%s(期望 %d=PT_485_M); "
             "地址判定 stAddr=%s(ADR_ABS 才进这个 if); 解析口拿到 %s"
             % (breakpoint.text(BP_645), HIT_WAIT, h3.where(), v3.get("port"),
                cmd_bank.DISP_PORT_485, v3.get("stAddr"),
                got3.hex(" ").upper() if got3 else "(读不全)"),
             crit="④")

    # ---- 第 4 帧 645: ⑤ 透传帧文正确 ----
    r4 = g.with_trigger(BP_RELAY, cmd_bank.send_relay_645, ser, f645, timeout=HIT_WAIT,
                        _join=JOIN_EXTRA, _vars=VARS_RELAY + ukeys,
                        _pair_name="7-2 第 4 帧 中继 645(转发落点)")
    h4, v4 = r4.get("hit"), r4.get("vars") or {}
    if h4 is None and r4.get("error") is None:
        _add(J, "断[C] ⑤ 645 透传帧文正确", False,
             "第 4 帧(同一条中继 645)发出后 %.1fs 内 %s 没命中 ⇒ 转发动作**没有被调到**"
             " —— 要么 ④ 那一支根本没往下走, 要么 %s 的落点条件不成立(计量芯口不在空闲态)"
             % (HIT_WAIT, breakpoint.text(BP_RELAY), breakpoint.text(BP_645)), crit="⑤")
    elif h4 is None:
        _add(J, "断[C] ⑤ 645 透传帧文正确", None,
             "触发那一侧抛了异常, 帧可能没发出去: %s ⇒ 本次没做成" % r4.get("error"), crit="⑤")
    else:
        len4 = cmd_bank.kwh_num(v4.get("Len"))
        got4 = cmd_bank.vars_bytes(v4, ukeys[:len(exp645)]) if len4 == len(exp645) else None
        if got4 is None:
            _add(J, "断[C] ⑤ 645 透传帧文正确", None,
                 "计量芯口缓冲这一次没读全(或 Len=%s 不是 %d[=645 帧去 FE 后的长度], 读的区间"
                 "没对齐) ⇒ 本次没做成" % (v4.get("Len"), len(exp645)), crit="⑤")
        else:
            same5 = (got4 == exp645)
            _add(J, "断[C] ⑤ 645 透传帧文正确", same5,
                 "停在 %s, Len=%s; 计量芯口 buff 起 %d 字节 %s; 期望(收到的线上字节原样) %s; 逐字节%s"
                 % (h4.where(), v4.get("Len"), len(got4), got4.hex(" ").upper(),
                    exp645.hex(" ").upper(), "全等" if same5 else "**不等**"), crit="⑤")

    # ---- 第 5 帧 中继 698: ⑥⑦ 回程回到请求来向那口 ----
    # ⚠ 这一帧与前四帧是**同一条**: 中继 698 本来就是"主站要发给计量芯"的样子, 计量芯认它就答,
    #   答回来的东西在 `:1096 else if (port == PT_UARTM)` 那一支里被搬回 485。停的那一句
    #   `Start_Relay_Frame(PT_485_M, Len);` 端口是**写死在参数里的字面量**, 所以"命中"本身
    #   就答了"回到的是 485 那一口"(回载波口的是 :1120, 另一行, 停不到这儿)。
    r5 = g.with_trigger(BP_BACK, cmd_bank.send_relay_698, ser, f698, oad, timeout=BACK_WAIT,
                        _join=JOIN_EXTRA, _vars=VARS_BACK + bkeys,
                        _pair_name="7-2 第 5 帧 中继 698(等回程)")
    h5, v5 = r5.get("hit"), r5.get("vars") or {}
    back5 = b""
    try:
        back5 = bytes(r5.get("result") or b"")
    except Exception:
        back5 = b""
    if h5 is None or r5.get("error"):
        # ⑥ 判不判得死, 取决于**回程有没有被走到**: 前面 ② 或 ⑤ 命中 = 管理芯确实把帧转给了
        # 计量芯, 那之后应答没被转回 485 就是整表这一路不通 ⇒ 判不通过。前面就断了(一个转发
        # 落点都没命中)时, 回程根本没被走到, 这一条无从谈起 ⇒ 记"没做成"(本轮的账已由 ②⑤ 认领)。
        _fwd = (h2 is not None) or (h4 is not None)
        _why5 = ("%s 在 %.1fs 内没命中" % (breakpoint.text(BP_BACK), BACK_WAIT)
                 if h5 is None else "命中后读变量出错: %s" % r5.get("error"))
        _tail5 = ("; 485 口这一次收到 %d 字节: %s" % (len(back5), back5.hex(" ").upper())
                  if back5 else "; 485 口这一次没收到任何字节")
        if r5.get("error"):
            _o6 = None
            _d6 = "触发那一侧抛了异常, 帧可能没发出去: %s ⇒ 本次没做成" % r5.get("error")
        elif _fwd:
            _o6 = False
            _d6 = ("%s ⇒ 管理芯已经把帧转给了计量芯(②/⑤ 命中过), 却没有帧走到“回 485”"
                   "那一支%s —— **回程段(计量芯→管理芯→485)没转回来**" % (_why5, _tail5))
        else:
            _o6 = None
            _d6 = ("%s ⇒ 本次一个转发落点都没命中, 回程根本无从走到 ⇒ 本次没做成"
                   "(这一轮的账由 ②⑤ 认领)%s" % (_why5, _tail5))
        _add(J, "断[D] ⑥ 回程回到请求来向那口", _o6, _d6, crit="⑥")
        _add(J, "断[D] ⑦ 回程推的字节数对得上那条应答帧", None,
             "⑥ 没命中 ⇒ 没有 Len 可比 ⇒ 本次没做成", crit="⑦")
    else:
        print("   [白盒] 断[D] 第 5 帧 停在 %s | port=%s | Len=%s"
              % (h5.where(), v5.get("port"), v5.get("Len")))
        _add(J, "断[D] ⑥ 回程回到请求来向那口", True,
             "%s 在 %.1fs 内命中(停在 %s): 这一句是 Start_Relay_Frame(PT_485_M, Len), "
             "端口是字面量 ⇒ 这条应答回到的是 485 那一口(请求的来向); "
             "485 口总共收到 %d 字节: %s"
             % (breakpoint.text(BP_BACK), BACK_WAIT, h5.where(), len(back5),
                back5.hex(" ").upper() if back5 else "(串口这一侧没收到, 但断点先命中了)"),
             crit="⑥")

        len5 = cmd_bank.kwh_num(v5.get("Len"))
        got5 = cmd_bank.vars_bytes(v5, bkeys[:len5]) if (len5 and len5 <= len(bkeys)) else None
        if got5 is None:
            _add(J, "断[D] ⑦ 回程推的字节数对得上那条应答帧", None,
                 "Len=%s 读不出或大于一次能读的 %d 字节 ⇒ 本次没做成"
                 % (v5.get("Len"), len(bkeys)), crit="⑦")
        else:
            self5 = cmd_bank.reply_frame_len(got5)
            if self5 is None:
                _add(J, "断[D] ⑦ 回程推的字节数对得上那条应答帧", None,
                     "推回 485 的头 %d 字节 %s 认不出帧头(645 要 [7]==0x68, 698 要 [0]==0x68)"
                     " ⇒ 没做成(推的不是可解析的帧)" % (len(got5), got5.hex(" ").upper()),
                     crit="⑦")
            else:
                same7 = (self5 == len5)
                _add(J, "断[D] ⑦ 回程推的字节数对得上那条应答帧", same7,
                     "停在 %s, Len=%s(固件实际推回 485 的字节数); 目标缓冲起 %d 字节 %s; "
                     "这条帧自己声明 %d 字节 ⇒ %s"
                     % (h5.where(), v5.get("Len"), len(got5), got5.hex(" ").upper(), self5,
                        "对得上" if same7 else "**对不上**(推少了=半条帧, 推多了=连缓冲残渣一起推)"),
                     crit="⑦")


def _banner():
    return "== 7-2 载波·管理芯中继透传 | 工程=%s 表号=%s ==" % (
        CURRENT.PROJECT, CURRENT.TABLE_ADDR.hex().upper())


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "7-2 载波·管理芯中继透传", cmd_bank.plc_relay_criteria,
        name="7_2_plc_relay", parts=[("7-2 管理芯中继透传段", part_relay)],
        gdb=breakpoint, banner=_banner(),
        # ⚠ **本脚本只有断点观测**: 七条判据读的全是停住那一刻的入参/全局量 —— 五帧是**发出去**的,
        #   但"它走的是中继那条路不是管理芯自己应答""转过去的字节对不对""转到了哪个口"这三件事
        #   串口都答不出(串口只看得见回没回帧, 看不见帧在管理芯里怎么走的)。
        #   不照实写就会凭空多出一条"串口观测被跳过了"(`_test_4_1_freeze_ping.py` 是这条规矩的另一半:
        #   纯黑盒的那份照实写 `obs=(JS.SERIAL,)`)。
        obs=(judge.DEBUG,),
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV)))
