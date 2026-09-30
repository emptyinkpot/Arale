# -*- coding: utf-8 -*-
"""在证什么: 一帧 698 从 485 口或蓝牙口进来之后, 管理芯**收对了没有 / 认成什么协议 / 应答怎么推
  回去** —— 解析口 / 发送状态机的入参与缓冲逐字节对上(收帧 / 端口归属 / 派发 / 推的字节数 /
  这一包合法不合法 / 回程目的地地址 / 主机收不收得到)。
会向表写什么: 只发**只读**帧 698 读表钟若干次, 外加一帧 `645.factory` 进厂内(645/698 读在厂内
  态下才稳)。参数区与 RAM 一个字节都不动, 也不落任何记录。
  ⚠ 唯一的例外是**地址对照试验**那一小段: 它趁停住把回程那一帧的目的地地址改写 6 个字节、再用
  保留域补平校验和, 放行后看主机收不收得到 —— 收尾由 `with_inject` 自动写回原值。它动的是 RAM
  不是固件, 所以**不认领判据**, 只作对照记进日志。
  台面收尾走 `python scripts/_restore_all.py`。
跑法: `python project/tests/_test_7_1_frame_dispatch.py`(真串口 + 真蓝牙模组 + 真探针)。
结论怎么读: 账本末行「判据: 满足 n/N」, N=9(①~⑨); ①~④ 在 485 段判, ①②③ 另带 `(蓝牙支)`
  在蓝牙口再判一次、⑤~⑨ 只在蓝牙段判 —— 同一条判据两处各判一次, 账本按 crit 归并。
  退 0 = 通过 / 1 = 失败 / 2 = 未定论。
"""
from common import judge              # 观测种类常量: ①~⑧ 断点 + ⑨ 串口
from common import trial              # 运行外壳: argv/日志/账本/串口/会话/收尾三连/退出码
from meterlib import ble              # 蓝牙透传通道(与 portsel.RawCom 同形)
from meterlib import cmd_bank         # 帧目录 + 语义动词积木箱(每步自带打印/判 PASS·FAIL)
from meterlib import p698             # 协议活: 组帧 / 切 APDU / 校验(与 645 无关的那一半)
from project import CURRENT           # 本工程画像(库不认表, 只有脚本认)
from swdbg import breakpoint          # 断点级白盒; 没接探针会自动降级
from swdbg import gdbinit             # 整片 .out 地图: 开会话后解一次, 之后断点/注入点只查它

# ---- 子项参数(纯数据; 要改的在这里改) ----
HIT_WAIT = 3.0        # 每一帧发出去之后, 等断点命中的最长秒数
BLE_TRIES = 4         # 蓝牙那几停要重试几次 —— `:442` 是所有口共用的一句, 停在别的口上就得重来
BLE_WAIT = 10.0       # 蓝牙每一帧发出后等主机侧收字节的秒数(远大于 63 字节在 115200 上的发送时间)
JOIN_485 = 8.0        # 485 段触发线程收尾的额外宽限(帧发完到线程退干净)
JOIN_BLE = 12.0       # 蓝牙段同上的宽限 —— 比 485 段宽, 走的是模组那一段, 退得慢
CTRL_TRIES = 3        # 地址对照试验重试几次(②③ 必须停在**同一条**回程帧上, 就是靠它兜住)
CTRL_WAIT = 8.0       # 地址对照试验里每帧给主机侧的等待秒数

# ---- 断点: 模块级**字面量元组** ----
# `scripts/_check_anchors.py` 用 `ast.literal_eval` 抠 `BP_<X>`/`VARS_<X>` 两个名字逐条对源码核
# (那个变量在断点那一行赋过值没有); 写成 `cmd_bank.SOMETHING` 那种表达式就抠不出来, 等于没人核过。
# `VARS_<X>` 只列**顶层名** —— "按 port 取哪条缓冲""取几个字节"是协议知识, 由库的 `vars_bytes()` 解。
BP_PARSE = ("DLT698Link.c", 283)          # Analyse_698Prot 函数体首条可执行语句 = 每来一帧命中一次
VARS_PARSE = ("port", "pFrame", "g_ProtSt")   # 入参 2 个(端口归属 / 帧文) + 派发结果
BP_SEND = ("Communicate.c", 442)          # 开始把应答推给模组(`OpenTx`); 推多长那一刻已经定好了
VARS_SEND = ("port", "u16Len", "g_ComLen[port]", "g_ComAdr[port]")   # 组的帧多长 / 推多长 / 从哪起推

# 每段的 (crit, 判据名) —— 半途中止或没有会话时逐条记 `ok=None` 用。
# ⚠ 判据名里的 `断[X]` / `串口` **照着 F 列那些字母写**; 蓝牙去程那三条接 `(蓝牙支)`, 与 485 段
#   同名不同口 —— 两段走的是两条口, 各有各的"没进解析链"。
_ITEMS_485 = (("①", "断[B] ① 帧到即停"),
              ("②", "断[B] ② 端口归属"),
              ("③", "断[B] ③ 取数正确"),
              ("④", "断[B] ④ 派发认对协议"))

_ITEMS_BLE_FWD = (("①", "断[B] ① 帧到即停(蓝牙支)"),
                  ("②", "断[B] ② 端口归属(蓝牙支)"),
                  ("③", "断[B] ③ 取数正确(蓝牙支)"))

_ITEMS_BLE_BACK = (("⑤", "断[B] ⑤ 回程推得不多不少"),
                   ("⑥", "断[C] ⑥ 管理芯把这一包推完了"),
                   ("⑦", "断[C] ⑦ 回程那一包是合法 698 应答"),
                   ("⑧", "断[C] ⑧ 回程目的地地址对"),
                   ("⑨", "串口 ⑨ 主机侧收到那条应答"))


def _add(J, label, ok, why, crit, obs=judge.DEBUG):
    """记一条判据(`falsify` 按 `crit` 从库里的单点取)+ 打一行三态。"""
    J.add(label, ok, why, crit=crit, falsify=cmd_bank.DISP_FALSIFY[crit], obs=obs)
    print("   [%s] %s —— %s" % ({True: "PASS", False: "FAIL", None: "TBD"}[ok], label, why))


def _blame(J, items, reason):
    """`items` 里那几条逐条记 `ok=None`(没做成) + 打一行 —— 半途中止/没会话时用, 不记失败。"""
    print("   [白盒] %s" % reason)
    for crit, label in items:
        _add(J, label, None, reason, crit=crit)


def _read_keys(frame):
    """停住时要读的量。**逐字节列 key 而不是读整数组** —— `kwh_num` 吃的就是
    `-data-evaluate-expression` 的单值文本, 整数组的文本形态是另一回事。"""
    return tuple("pFrame[%d]" % i for i in range(len(frame)))


def part_dispatch(ctx):
    """485 口的 ①②③④ —— 本轮取全部条目, 所以 ④ 在这里做。

    ⚠ 观测一律 attach, **禁用 launch**(launch 会复位表, RAM 态清零) —— 见 CLAUDE.md 调试链纪律 1。
    ⚠ 断点都是**发帧触发**、`with_trigger` 当场挂当场撤(命中与没命中**两条路都撤**), **不是**
      `ctx.bp()` 预挂: 预挂上又没人等在等命中时, 它自己会把核撂停, 其后每条串口帧整帧无应答
      (与"表死机"一模一样)。`:283` 尤其要紧 —— 它是"每来一帧就停"的高频口。
    """
    J, ser = ctx.J, ctx.ser
    print("\n===== 7-1 485 口 帧进出与派发 =====")

    cmd_bank.enter_factory(ser)                     # 698 读在厂内态下才稳
    ctx.session()
    g = ctx.g
    if g is not None:
        # 整片 .out 解一次(停核遍历, 内部喂狗, 收尾放行) —— 之后一律查这张图
        gdbinit.build(g)
    if g is None:
        # ⚠ 这四条**全是断点观测**: 帧确实发得出去, 但"它进没进解析链 / 归哪条口 / 被当成 698
        #   还是 645 派发"串口都答不出(串口只看得见回没回帧) ⇒ 没有会话就一条都做不成。
        _blame(J, _ITEMS_485, "本次没有断点会话(没接探针) ⇒ 判①②③④ 没做成")
        return

    oad = cmd_bank.DISP_OAD
    port_exp = cmd_bank.DISP_PORT_485
    frame = p698.frame_698(p698.build_read_apdu(0x03, oad))
    pkeys = _read_keys(frame)
    keys1 = ("port", "len", "g_ComAdr[port]", "g_ProtSt[port]") + pkeys
    print("   发帧走 %s 口, 注入帧 %d 字节: %s"
          % (_port_name(port_exp), len(frame), bytes(frame).hex(" ").upper()))

    # ---- 第 1 帧: ① 帧到即停 + ② 端口归属 + ③ 取数正确 ----
    r1 = g.with_trigger(BP_PARSE, cmd_bank.send_clock_698, ser, frame, oad, timeout=HIT_WAIT,
                        _join=JOIN_485, _vars=keys1,
                        _pair_name="7-1 第 1 帧 698 读表钟")
    h1, v1 = r1.get("hit"), r1.get("vars") or {}
    if r1.get("error") is not None:
        print("   !! 触发那一侧抛了异常(帧可能没发出去): %s" % r1.get("error"))
    if h1 is None:
        _blame(J, _ITEMS_485,
               "第 1 帧发出后 %.1fs 内没等到 %s 命中 ⇒ 本次没做成"
               % (HIT_WAIT, breakpoint.text(BP_PARSE)))
        return
    print("   [白盒] 断[B] 第 1 帧 停在 %s | port=%s | len=%s | g_ComAdr[port]=%s | "
          "g_ProtSt[port] 基线=%s"
          % (h1.where(), v1.get("port"), v1.get("len"),
             v1.get("g_ComAdr[port]"), v1.get("g_ProtSt[port]")))
    got = cmd_bank.vars_bytes(v1, pkeys)
    print("   [白盒] 解析口拿到的 %d 字节 = %s"
          % (len(frame), got.hex(" ").upper() if got else "(读不全)"))
    got_port = cmd_bank.kwh_num(v1.get("port"))
    base = cmd_bank.kwh_num(v1.get("g_ProtSt[port]"))

    # ---- ① 帧到即停 ----
    _add(J, "断[B] ① 帧到即停", True,
         "%s 在 %.1fs 内命中(停在 %s); 注入帧 %d 字节 %s"
         % (breakpoint.text(BP_PARSE), HIT_WAIT, h1.where(), len(frame),
            bytes(frame).hex(" ").upper()),
         crit="①")

    # ---- ② 端口归属 ----
    if got_port is None:
        _add(J, "断[B] ② 端口归属", None, "入参 port 这一次没读到 ⇒ 本次没做成", crit="②")
    else:
        ok2 = (got_port == port_exp)
        _add(J, "断[B] ② 端口归属", ok2,
             "port=%d; 发帧走的填的是 %s ⇒ %s"
             % (got_port, _port_name(port_exp),
                "对上" if ok2 else "**对不上** —— 这帧不是从那条口进来的"),
             crit="②")

    # ---- ③ 取数正确 ----
    if got_port != port_exp:
        _add(J, "断[B] ③ 取数正确", None, "② 没判过(这帧不归发帧那条口) ⇒ 本次不据此判固件",
             crit="③")
    elif got is None:
        _add(J, "断[B] ③ 取数正确", None,
             "入参 pFrame 这一次没读全(断点停在 pFrame 的空洞区间?) ⇒ 本次没做成", crit="③")
    else:
        same = (got == bytes(frame))
        _add(J, "断[B] ③ 取数正确", same,
             "解析口拿到的 %d 字节 %s; 发出的是 %s; 逐字节%s; g_ComAdr[port]=%s / len=%s"
             % (len(got), got.hex(" ").upper(), bytes(frame).hex(" ").upper(),
                "全等" if same else "**不等**", v1.get("g_ComAdr[port]"), v1.get("len")),
             crit="③")

    # ---- ④ 派发认对协议(第 2 帧; 读的是第 1 帧跑完时写下的值) ----
    # ⚠ 停住这一刻本帧还没判完, 读到的 `g_ProtSt[port]` 是**上一帧**跑完时固件写下的 ⇒ 它答的
    #   正是"第 1 帧被当成什么派发的"。第 1 帧停住时那一次读的是**基线** —— 否则"它本来就是 1"
    #   会被当成判过。
    r2 = g.with_trigger(BP_PARSE, cmd_bank.send_clock_698, ser, frame, oad, timeout=HIT_WAIT,
                        _join=JOIN_485, _vars=("g_ProtSt[port]", "port"),
                        _pair_name="7-1 第 2 帧 698 读表钟")
    h2, v2 = r2.get("hit"), r2.get("vars") or {}
    if h2 is None:
        _add(J, "断[B] ④ 派发认对协议", None,
             "第 2 帧发出后 %.1fs 内没等到 %s 命中 ⇒ 本次没做成(第 1 帧的派发结果无从读起)"
             % (HIT_WAIT, breakpoint.text(BP_PARSE)), crit="④")
    else:
        proto = cmd_bank.kwh_num(v2.get("g_ProtSt[port]"))
        _add(J, "断[B] ④ 派发认对协议", proto == cmd_bank.DISP_PROTO_698,
             "第 2 次停住时 g_ProtSt[%s]=%s(装的是第 1 帧跑完时固件写下的派发结果; "
             "第 1 次停住时基线=%s); 1=698帧 0=645帧 ⇒ %s"
             % (v2.get("port"), v2.get("g_ProtSt[port]"), base,
                "固件把它当 698 派发了" if proto == cmd_bank.DISP_PROTO_698
                else "**读回 %s** —— 没被认作 698" % (proto,)),
             crit="④")


def _port_name(port):
    """口号 → 人认得出的名字。**认不出就照原样报号**, 不猜(PORT_ENUM 里还有别的口)。"""
    names = {cmd_bank.DISP_PORT_485: "PT_485_M", cmd_bank.DISP_PORT_BLE: "PT_BLE_M"}
    return "%s=%s" % (names[port], port) if port in names else str(port)


def _peer_addr(g, link, frame, oad, tries):
    """停 断[B] 读**模组发给管理芯**那一帧的地址域(外壳偏移 8..13); 没取到返回 `None`。

    ⑧ 的对照物在模组那一侧 —— 回程那一帧里的地址对不对, 只有跟"模组用的是哪个"比才答得出。
    多来几次是为了把链路连起来(`BleLink` 空闲会掉线), 也是为了让停在别的口上的那几次重来。
    """
    bkeys = tuple("g_BLEMBuff[%d]" % i for i in range(cmd_bank.DISP_BUF_DUMP))
    for k in range(tries):
        r = g.with_trigger(BP_PARSE, cmd_bank.send_clock_698, link, frame, oad, timeout=HIT_WAIT,
                           _join=JOIN_BLE, _vars=("port",) + bkeys,
                           _pair_name="7-1 蓝牙去程 读模组发来的地址域")
        v, h = r.get("vars") or {}, r.get("hit")
        if h is None:
            print("   取地址第 %d 次: %.1fs 内没等到 %s 命中"
                  % (k + 1, HIT_WAIT, breakpoint.text(BP_PARSE)))
            continue
        if cmd_bank.kwh_num(v.get("port")) != cmd_bank.DISP_PORT_BLE:
            print("   取地址第 %d 次: 停在 %s, 不是 %s"
                  % (k + 1, h.where(), _port_name(cmd_bank.DISP_PORT_BLE)))
            continue
        b0 = cmd_bank.vars_bytes(v, bkeys)
        if b0 is None or len(b0) < cmd_bank.DISP_ADDR_OFF + cmd_bank.DISP_ADDR_BYTES:
            print("   取地址第 %d 次: g_BLEMBuff 没读出来" % (k + 1))
            continue
        addr = bytes(b0[cmd_bank.DISP_ADDR_OFF:cmd_bank.DISP_ADDR_OFF + cmd_bank.DISP_ADDR_BYTES])
        print("   [白盒] 模组发给管理芯那一帧的地址域 = %s(停在 %s)"
              % (addr.hex(" ").upper(), h.where()))
        return addr
    print("   [白盒] 没取到模组用的那个地址 ⇒ ⑧ 本次没做成")
    return None


def part_ble(ctx):
    """蓝牙口的 ①②③(去程) + ⑤⑥⑦⑧⑨(回程) + 地址对照试验。

    ⚠ **蓝牙口不做 ④** —— ④ 读的 `g_ProtSt[port]==1` 是 485 口那条路写的
      (`Communicate.c:357`); 蓝牙帧走 `Blue_645_analysedata` 剥壳那条, 它在 `:338` 把同一个量
      写成 0。拿 ④ 判蓝牙口, 判出来的是"这条通路不写那个标记", 不是派发结果。蓝牙口"有没有被
      当 698 认"由回程的 ⑤ 答: 认不出 698 就走不到发送状态机, 停不下来。
    ⚠ 链路由**本段自己开关**: 模组空闲 60 s 内会掉线, 所以不带跨段的常开链路
      (`BleLink.write` 每次发帧前现连, 见 `meterlib/ble.py` 模块头)。
    ⚠ 进厂内走的是 485 口(蓝牙那条口上跑的是同族 698 帧) —— 上面 `part_dispatch` 已经进过,
      这一段不重复发。跑法上本脚本是两段连着跑的, 单跑某一段不在支持范围内。
    """
    J = ctx.J
    link = ble.open_link()
    try:
        print("\n===== 7-1 蓝牙口 去程 + 回程 =====")
        ctx.session()
        g = ctx.g
        if g is not None:
            gdbinit.build(g)
        if g is None:
            _blame(J, _ITEMS_BLE_FWD + _ITEMS_BLE_BACK,
                   "本次没有断点会话(没接探针) ⇒ 判①②③(蓝牙支)与⑤⑥⑦⑧⑨ 没做成")
            return
        _ble_forward(J, link, g)
        _ble_reply(J, link, g)
        _ble_addr_control(J, link, g)
    finally:
        link.close()


def _ble_forward(J, link, g):
    """去程 ①②③(蓝牙支): 帧从蓝牙口进来, 走的是与 485 口同一段的解析链。

    ⚠ 这一段**不做 ④**(见 `part_ble` 的 docstring), 所以它只发一帧。
    """
    oad = cmd_bank.DISP_OAD
    port_exp = cmd_bank.DISP_PORT_BLE
    frame = p698.frame_698(p698.build_read_apdu(0x03, oad))
    pkeys = _read_keys(frame)
    keys1 = ("port", "len", "g_ComAdr[port]", "g_ProtSt[port]") + pkeys
    print("\n-- 蓝牙去程 (注入 698 读表钟 / 停 %s) --" % breakpoint.text(BP_PARSE))
    print("   发帧走 %s 口, 注入帧 %d 字节: %s"
          % (_port_name(port_exp), len(frame), bytes(frame).hex(" ").upper()))

    r1 = g.with_trigger(BP_PARSE, cmd_bank.send_clock_698, link, frame, oad, timeout=HIT_WAIT,
                        _join=JOIN_485, _vars=keys1,
                        _pair_name="7-1 蓝牙去程 第 1 帧 698 读表钟")
    h1, v1 = r1.get("hit"), r1.get("vars") or {}
    if r1.get("error") is not None:
        print("   !! 触发那一侧抛了异常(帧可能没发出去): %s" % r1.get("error"))
    if h1 is None:
        _blame(J, _ITEMS_BLE_FWD,
               "蓝牙支第 1 帧发出后 %.1fs 内没等到 %s 命中 ⇒ 本次没做成"
               % (HIT_WAIT, breakpoint.text(BP_PARSE)))
        return
    print("   [白盒] 断[B] 蓝牙第 1 帧 停在 %s | port=%s | len=%s"
          % (h1.where(), v1.get("port"), v1.get("len")))
    got = cmd_bank.vars_bytes(v1, pkeys)
    print("   [白盒] 解析口拿到的 %d 字节 = %s"
          % (len(frame), got.hex(" ").upper() if got else "(读不全)"))
    got_port = cmd_bank.kwh_num(v1.get("port"))

    _add(J, "断[B] ① 帧到即停(蓝牙支)", True,
         "%s 在 %.1fs 内命中(停在 %s); 注入帧 %d 字节 %s"
         % (breakpoint.text(BP_PARSE), HIT_WAIT, h1.where(), len(frame),
            bytes(frame).hex(" ").upper()), crit="①")

    if got_port is None:
        _add(J, "断[B] ② 端口归属(蓝牙支)", None, "入参 port 这一次没读到 ⇒ 本次没做成", crit="②")
    else:
        ok2 = (got_port == port_exp)
        _add(J, "断[B] ② 端口归属(蓝牙支)", ok2,
             "port=%d; 发帧走的填的是 %s ⇒ %s"
             % (got_port, _port_name(port_exp),
                "对上" if ok2 else "**对不上** —— 这帧不是从那条口进来的"), crit="②")

    if got_port != port_exp:
        _add(J, "断[B] ③ 取数正确(蓝牙支)", None,
             "② 没判过(这帧不归发帧那条口) ⇒ 本次不据此判固件", crit="③")
    elif got is None:
        _add(J, "断[B] ③ 取数正确(蓝牙支)", None,
             "入参 pFrame 这一次没读全(断点停在 pFrame 的空洞区间?) ⇒ 本次没做成", crit="③")
    else:
        same = (got == bytes(frame))
        _add(J, "断[B] ③ 取数正确(蓝牙支)", same,
             "解析口拿到的 %d 字节 %s; 发出的是 %s; 逐字节%s; g_ComAdr[port]=%s / len=%s"
             % (len(got), got.hex(" ").upper(), bytes(frame).hex(" ").upper(),
                "全等" if same else "**不等**", v1.get("g_ComAdr[port]"), v1.get("len")),
             crit="③")


def _ble_reply(J, link, g):
    """回程 ⑤⑥⑦⑧⑨: 管理芯把组好的应答推给模组, 推的字节对不对 / 这一包合法不合法 /
    目的地写成了谁 / 主机到底收不收得到。

    · ⑤ —— 停 `Communicate.c:442`(`ComFun[port].OpenTx(C_PreCode);` = 开始推给模组), 停住时读
      `u16Len` / `g_ComLen[port]` / `g_ComAdr[port]`; 判过 = 从缓冲区偏移 0 起送 且
      `g_ComLen[port] == u16Len + 3`。⚠ 能停在这里本身也是一条事实: 这条帧被判过"需要应答"
      才会进发送状态机 —— 那正是蓝牙口"有没有被当 698 认"的回答。
    · ⑥ —— 放行之后回读 `g_ComLen[port]`: 判过 = 0(那一整包已逐字节写进 UART4 发送寄存器)。
    · ⑦ —— ⑤ 那一停读到的那一整包按 698 规则逐字节校验, 再切 APDU 认服务与对象。
    · ⑧ —— 停 断[B] 取模组用的地址域, 与回程那一帧同一个域逐字节比。
    · ⑨ —— 断[C] 那一停放行之后, 主机侧收到 ≥1 字节(串口观测: 看的是对外行为)。
    """
    oad = cmd_bank.DISP_OAD
    frame = p698.frame_698(p698.build_read_apdu(0x03, oad))
    bkeys = tuple("g_BLEMBuff[%d]" % i for i in range(cmd_bank.DISP_BUF_DUMP))
    keys = ("port", "u16Len", "g_ComLen[port]", "g_ComAdr[port]") + bkeys
    print("\n-- 蓝牙回程 (停 %s 看推给模组的长度 + 主机侧轮询) --" % breakpoint.text(BP_SEND))

    # ---- 先取"模组用的是哪个地址"(⑧ 的对照物) ----
    peer_addr = _peer_addr(g, link, frame, oad, BLE_TRIES)

    # ⚠ 缓冲区那一段**必须走 `_vars` 逐字节读**(读发生在核停着的那一刻): 实测踩过 —— 在
    #   `with_trigger` 返回后再读, 那时核已经被放行, gdb 回 `Cannot execute this command while
    #   the target is running`, 缓冲区一个字节都没读到。
    hit_v, rx, tries_done = None, b"", 0
    for k in range(BLE_TRIES):
        tries_done = k + 1
        r = g.with_trigger(BP_SEND, cmd_bank.send_clock_698, link, frame, oad, timeout=HIT_WAIT,
                           _join=JOIN_BLE, _vars=keys, wait=BLE_WAIT,
                           _pair_name="7-1 蓝牙回程 应答推给模组")
        if r.get("error") is not None:
            print("   !! 触发那一侧抛了异常(帧可能没发出去): %s" % r.get("error"))
        rx += r.get("result") or b""
        h, v = r.get("hit"), r.get("vars") or {}
        if h is None:
            print("   第 %d 次: %.1fs 内没等到 %s 命中"
                  % (k + 1, HIT_WAIT, breakpoint.text(BP_SEND)))
            continue
        p = cmd_bank.kwh_num(v.get("port"))
        print("   第 %d 次: 停在 %s | port=%s | u16Len=%s | g_ComLen[port]=%s | g_ComAdr[port]=%s"
              % (k + 1, h.where(), v.get("port"), v.get("u16Len"),
                 v.get("g_ComLen[port]"), v.get("g_ComAdr[port]")))
        if p == cmd_bank.DISP_PORT_BLE:
            # ⚠ 停在蓝牙口还不算数 —— `:442` 是**所有口共用**的那一句, 蓝牙口上还会推别的帧
            #   (实测撞上过一次 `u16Len=22` 的连接类帧, 缓冲区里根本没有那条 698 应答)。
            #   判据读的是"刚组好的那条应答", 所以优先取缓冲区里确实躺着它的那一停;
            #   一次都没撞上时**仍用最后一次** —— 否则"固件把这一包组坏了"会被记成"没做成",
            #   ⑦ 就永远判不出 FAIL(把 FAIL 降级成"没做成"是最坏的一种改法)。
            _got, _ul = cmd_bank.vars_bytes(v, bkeys), cmd_bank.kwh_num(v.get("u16Len"))
            if hit_v is None:
                hit_v = v
            if _got is not None and cmd_bank.disp_reply_frame_ok(_got, _ul, oad)[0]:
                hit_v = v
                break
            print("   第 %d 次: 停在蓝牙口, 但缓冲区里不是刚组好的那条 698 应答 ⇒ 再发一帧重等"
                  % (k + 1))
            continue
        print("   第 %d 次: 停的不是 %s, 再发一帧重等"
              % (k + 1, _port_name(cmd_bank.DISP_PORT_BLE)))

    # ---- ⑤ 回程推得不多不少(断点那一半) ----
    if hit_v is None:
        _add(J, "断[B] ⑤ 回程推得不多不少", None,
             "%d 次触发里没有一次停在 %s 的蓝牙口上 ⇒ 本次没做成(没等到的原因不止一种: "
             "这帧没让管理芯产生应答 / 应答没走到发送状态机 / 停到的都是别的口)"
             % (tries_done, breakpoint.text(BP_SEND)), crit="⑤")
        _add(J, "断[C] ⑦ 回程那一包是合法 698 应答", None,
             "⑤ 那一次没停在蓝牙口 ⇒ 没有可校验的那一包, 本次没做成", crit="⑦")
        _add(J, "断[C] ⑧ 回程目的地地址对", None,
             "⑤ 那一次没停在蓝牙口 ⇒ 没有可读的那一停, 本次没做成", crit="⑧")
    else:
        ul, ln, ad = (cmd_bank.kwh_num(hit_v.get("u16Len")),
                      cmd_bank.kwh_num(hit_v.get("g_ComLen[port]")),
                      cmd_bank.kwh_num(hit_v.get("g_ComAdr[port]")))
        # 缓冲区这一段是**佐证**: 读到了就照实记, 读不到不影响判据本身。
        got_buf = cmd_bank.vars_bytes(hit_v, bkeys)
        tail = ("; g_BLEMBuff 起 %d 字节 = %s" % (len(got_buf), got_buf.hex(" ").upper())
                if got_buf else "; g_BLEMBuff 这一次没读出来")
        want = (ul + cmd_bank.DISP_TX_PRE) if ul is not None else None
        ok5 = (ad == 0 and want is not None and ln == want)
        _add(J, "断[B] ⑤ 回程推得不多不少", ok5,
             "停 %s 蓝牙口那一停: u16Len=%s(固件组好的那一整包多长) / g_ComLen[port]=%s"
             "(中断里要推的字节数) / g_ComAdr[port]=%s(从缓冲区第几个字节起取, STR1=0 即从头)%s ⇒ %s"
             % (breakpoint.text(BP_SEND), hit_v.get("u16Len"), hit_v.get("g_ComLen[port]"),
                hit_v.get("g_ComAdr[port]"), tail,
                "从缓冲区偏移 0 起送、字节数 = 整包 + %d 个中断前导"
                "(线上 = 4 个前导 0xFE + 那一整包, 共 %s 字节)" % (cmd_bank.DISP_TX_PRE, want) if ok5
                else "**字节数与设计不符**: 实际 %s, 应为 整包 + %d = %s"
                     % (ln, cmd_bank.DISP_TX_PRE, want)),
             crit="⑤")

        # ---- ⑦ 这一包的内容对不对(⑤ 只管长度与起点) ----
        if not got_buf:
            ok7, why7 = None, "g_BLEMBuff 这一次没读出来 ⇒ 没有可校验的那一包, 本次没做成"
        else:
            ok7, why7 = cmd_bank.disp_reply_frame_ok(got_buf, ul, oad)
        _add(J, "断[C] ⑦ 回程那一包是合法 698 应答", ok7, why7, crit="⑦")

        # ---- ⑧ 回程那一帧的目的地地址(外壳偏移 8..13)对不对 ----
        rep_addr = (bytes(got_buf[cmd_bank.DISP_ADDR_OFF:
                                 cmd_bank.DISP_ADDR_OFF + cmd_bank.DISP_ADDR_BYTES])
                    if got_buf is not None
                    and len(got_buf) >= cmd_bank.DISP_ADDR_OFF + cmd_bank.DISP_ADDR_BYTES else None)
        if peer_addr is None:
            _add(J, "断[C] ⑧ 回程目的地地址对", None,
                 "没取到模组用的那个地址(去程那一停没成) ⇒ 没有对照物, 本次没做成", crit="⑧")
        elif rep_addr is None:
            _add(J, "断[C] ⑧ 回程目的地地址对", None,
                 "回程那一帧的外壳地址域(g_BLEMBuff 偏移 %d 起)这一次没读出来 ⇒ 本次没做成"
                 % cmd_bank.DISP_ADDR_OFF, crit="⑧")
        else:
            same8 = (rep_addr == peer_addr)
            _add(J, "断[C] ⑧ 回程目的地地址对", same8,
                 "模组发给管理芯那一帧的地址域 = %s; 回程那一帧(g_BLEMBuff 偏移 %d 起)= %s ⇒ %s"
                 % (peer_addr.hex(" ").upper(), cmd_bank.DISP_ADDR_OFF,
                    rep_addr.hex(" ").upper(),
                    "一致, 模组认得出这条应答是给它的" if same8 else
                    "**不一致** —— 该把模组用的那个地址抄回去, 实际写的是别的"
                    "(`Application\\TaskBluet.c:303-304` 的透传回程把地址填成了常量)"),
                 crit="⑧")

    # ---- ⑨ 主机侧收到那条应答(串口观测: 看的是对外行为) ----
    print("   [台面] 主机侧 %d 次触发各等 %s 秒共收到 %d 字节" % (tries_done, BLE_WAIT, len(rx)))
    _add(J, "串口 ⑨ 主机侧收到那条应答", len(rx) > 0,
         "%d 次触发各等 %s 秒, 主机侧共收到 %d 字节: %s ⇒ %s"
         % (tries_done, BLE_WAIT, len(rx), rx.hex(" ").upper() if rx else "(一个字节都没有)",
            "应答走到了主机" if rx else
            "**一个字节都没有** —— 回程那一帧的目的地不是模组用的那个地址时, 模组丢掉它"
            "(地址对照试验见下: 把地址改成模组用的那个, 主机侧就收得到)"),
         crit="⑨", obs=judge.SERIAL)

    # ---- ⑥ 管理芯把这一包推完了没有(发送之后回读) ----
    # ⚠ 读法: `read_vars` 要求**停住态**(核跑着时 gdb 拒答), 所以自己叫停一次、读完立刻放行;
    #   停的是"这一包早就推完"之后的稳态 —— `BLE_WAIT` 秒远大于 63 字节在 115200 上的发送时间。
    #   `g_ComAdr[port]` 一并记进明细当佐证, **但它不作判据**: 收帧也推着它走。
    ok6, why6 = None, ""
    cl_expr = "g_ComLen[%d]" % cmd_bank.DISP_PORT_BLE
    ad_expr = "g_ComAdr[%d]" % cmd_bank.DISP_PORT_BLE
    if hit_v is None:
        why6 = "⑤ 那一次没停在蓝牙口 ⇒ 没有可绑定的那一次发送, 本次没做成"
    else:
        try:
            g.ensure_stopped()
            try:
                post = g.read_vars((cl_expr, ad_expr))
            finally:
                g.ensure_running()
            cl, ad = cmd_bank.kwh_num(post.get(cl_expr)), cmd_bank.kwh_num(post.get(ad_expr))
            ok6 = (cl == 0)
            why6 = ("发送放行、主机侧等满 %s 秒之后回读: %s=%s(推完了应是 0) / %s=%s(佐证, 不作判据)"
                    % (BLE_WAIT, cl_expr, cl, ad_expr, ad))
            why6 += (" ⇒ 管理芯把这一包逐字节推给了模组" if ok6 else
                     " ⇒ **这一包没被送完**(发送中断链在 :442 之后停住了)")
        except Exception as e:                    # noqa: BLE001 —— 读不成就是"没做成", 不判 FAIL
            ok6 = None
            why6 = "回读没做成(停/读那一步抛了): %s" % str(e).strip().splitlines()[0]
    _add(J, "断[C] ⑥ 管理芯把这一包推完了", ok6, why6, crit="⑥")


def _ble_addr_control(J, link, g):
    """**受控对照**: 『主机收不到, 是不是因为回程那一帧的目的地地址写错了』。

    **它不认领判据**(动的是 RAM 不是固件, 答不出"什么固件会让它 FAIL"): 它答的是 ⑨ 那条 FAIL
    该记在谁头上 —— 把地址改成模组用的那个之后主机收到字节 ⇒ 模组与空口这一段是好的, 病就在
    固件写死的地址; 改了还是收不到 ⇒ 病不止地址那一条。
    三次停: ① 停 断[B] 取模组用的地址; ② 停 断[C] 读回程原文(同时是"不改时主机收多少"的对照);
    ③ 停 断[C] 把地址写成①取到的那个, 再用保留域第 1 个字节(`g_BLEMBuff[14]`)把校验和补平 ——
    校验和是整头加数据的 8 位和(`Blue_645_checksum`), 地址一动它就变, 模组据此丢帧。
    """
    oad = cmd_bank.DISP_OAD
    frame = p698.frame_698(p698.build_read_apdu(0x03, oad))
    bkeys = tuple("g_BLEMBuff[%d]" % i for i in range(cmd_bank.DISP_BUF_DUMP))
    keys = ("port", "u16Len", "g_ComLen[port]") + bkeys
    label = "串口 回程地址对照试验(台面, 不认领判据)"
    print("\n-- 蓝牙回程地址对照试验 (停 %s 改回程地址再放行 + 主机侧轮询) --"
          % breakpoint.text(BP_SEND))

    def _stop_once(assigns):
        """停 bp_send 一次: 可选注入, 放行后把主机侧收到的字节交回来。"""
        res = g.with_inject(BP_SEND, assigns, at_vars=keys,
                            trigger=cmd_bank.send_clock_698,
                            trigger_args=(link, frame, oad, CTRL_WAIT),
                            timeout=HIT_WAIT, join=JOIN_BLE)
        if res.get("trigger_error") is not None:
            print("   !! 触发那一侧抛了异常(帧可能没发出去): %s" % res.get("trigger_error"))
        av = res.get("at_vals") or {}
        return av, cmd_bank.vars_bytes(av, bkeys), res.get("result") or b""

    peer = _peer_addr(g, link, frame, oad, CTRL_TRIES)
    if peer is None:
        print("   [白盒] ① 没取到模组用的那个地址 ⇒ 这一条没做成")
        J.add(label, None, "① 没取到模组用的那个地址 ⇒ 没有可对的东西, 这一条没做成")
        return

    # ⚠ ② 与 ③ 必须是**同一条**回程帧: `:442` 是所有口共用的一句, 蓝牙口上还会推别的帧
    #   —— 撞错帧就会把补偿量算错、模组丢帧、主机收 0 字节, 整场对照试验变成一次假阴性。
    #   所以停下来之后先认帧(`u16Len` 截断 + 内嵌 698 校验), 不认就再发一帧重等。
    got_plain = got_fixed = cur = None
    for k in range(CTRL_TRIES):
        av0, buf0, rx0 = _stop_once([])
        if buf0 is None or not cmd_bank.disp_reply_frame_ok(buf0, cmd_bank.kwh_num(av0.get("u16Len")),
                                                            oad)[0]:
            print("   第 %d 次: ② 那一停不是刚组好的那条 698 应答(port=%s, u16Len=%s) ⇒ 重来"
                  % (k + 1, av0.get("port"), av0.get("u16Len")))
            continue
        cur, res0 = (buf0[cmd_bank.DISP_ADDR_OFF:
                          cmd_bank.DISP_ADDR_OFF + cmd_bank.DISP_ADDR_BYTES], buf0[14])
        delta = sum(peer) - sum(cur)
        assigns = ([(cmd_bank.DISP_BUF_ADDR[i], peer[i]) for i in range(cmd_bank.DISP_ADDR_BYTES)]
                   + [(cmd_bank.DISP_BUF_RESERVE, (res0 - delta) & 0xFF)])
        print("   ① 模组用的地址 = %s; ② 回程原文的地址 = %s(保留域 %02X) ⇒ 主机收到 %d 字节"
              % (peer.hex(" ").upper(), cur.hex(" ").upper(), res0, len(rx0)))
        print("   ③ 把地址改成 %s(用保留域补 %d 保持校验和不变)再放行"
              % (peer.hex(" ").upper(), (res0 - delta) & 0xFF))

        av1, buf1, rx1 = _stop_once(assigns)
        if buf1 is None or not cmd_bank.disp_reply_frame_ok(buf1, cmd_bank.kwh_num(av1.get("u16Len")),
                                                            oad)[0]:
            print("   第 %d 次: ③ 注入那一停不是那条 698 应答(port=%s, u16Len=%s) ⇒ 重来"
                  % (k + 1, av1.get("port"), av1.get("u16Len")))
            continue
        got_plain, got_fixed = rx0, rx1
        break

    if got_fixed is None:
        print("   [白盒] ②③ 几次都没停在「刚组好的那条 698 应答」上 ⇒ 这一条没做成")
        J.add(label, None, "②③ 几次都没停在「刚组好的那条 698 应答」上 ⇒ 这一条没做成")
        return
    print("   ③ 停在 %s | 主机收到 %d 字节 %s"
          % (breakpoint.text(BP_SEND), len(got_fixed),
             got_fixed.hex(" ").upper() if got_fixed else ""))
    J.add(label, None,
          "同一台表、同一条链路, 只改回程那一帧的目的地地址: 原文 %s ⇒ 主机收到 %d 字节; "
          "改成模组用的那个 %s ⇒ 主机收到 %d 字节 —— %s"
          % (cur.hex(" ").upper(), len(got_plain), peer.hex(" ").upper(), len(got_fixed),
             "模组与空口这一段是好的, 主机收到与否只取决于那个地址写成了谁 "
             "(⇒ ⑨ 那条 FAIL 记在固件写死的地址上)" if got_fixed else
             "**改了地址主机还是收不到** ⇒ 病不止地址那一条(模组侧还有别的原因)"),
          obs=judge.SERIAL)


def _banner():
    return "== 7-1 通信·管理芯帧进出与派发 | 工程=%s 表号=%s | 485 口 + 蓝牙口 %s ==" % (
        CURRENT.PROJECT, CURRENT.TABLE_ADDR.hex().upper(), ble.MODULE_ADDR)


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "7-1 通信·管理芯帧进出与派发", cmd_bank.frame_dispatch_criteria,
        name="7_1_frame_dispatch",
        parts=[("7-1 485 帧进出/派发段", part_dispatch), ("7-1 蓝牙去程段", part_ble)],
        gdb=breakpoint, banner=_banner(),
        # ①~⑧ 是**断点观测**(读的是停住那一刻的入参/全局量): 帧虽是发出去的, 但"它进没进解析链 /
        #   推完了没有 / 目的地写成了谁"串口自己答不出 —— 串口只看得见应答, 看不见它走哪条分支。
        # ⑨ 是**串口观测**: 判的是"这条应答有没有走到主机", 看的是对外行为, 不落在任何变量上。
        obs=(judge.DEBUG, judge.SERIAL),
        # ⚠ `inject_allow` 必须在**会话构造时**点名 —— 白名单精确匹配符号名, 事后补不了。
        #   地址对照试验写回程那一帧的地址域与补校验和的保留域, 别的一律不许从这个脚本写。
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV,
                        inject_allow=cmd_bank.disp_inject_allow())))
