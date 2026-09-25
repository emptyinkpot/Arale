# -*- coding: utf-8 -*-
"""日志那一行的**字形**只在这里定义一次 —— 串口帧行与判定行。

**为什么要合出这个模块**(2026-09-18 定): 同一帧在 `log/3_1_rate_num_20260918_105031.log` 里
落过**三处、三种长相** ——

    TX(20) rd_04000201: FE FE FE FE 68 11 … 16          ← 传输层打的, 只有帧 id, **不标协议**
    == [发送→管理芯] 645 读费率参量 年时区数(zone, DI 04000201)   ← 语义层打的, 只有话
       TX FE FE FE FE 68 11 … 16                        ← 语义层又打一遍同一帧

于是"这一帧是 645 还是 698"在帧那一行上**答不出来**, 得回头看上一行; 而 `rd_04000201` /
`relay` / `read_clock` / `aa80_r2` 这些 frame id 有时带协议、有时不带, 带不带是**巧合**。

**合起来之后一帧两行**(发一行、收一行), 各带全套标签, 帧长与帧体一个字节不少:

    发:  [645] [发→管理芯] 读费率参量 年时区数(zone, DI 04000201) 20B  FE FE FE FE 68 11 … 16
    收:  [645] [收←管理芯] 读费率参量 年时区数(zone, DI 04000201) 21B  FE FE FE FE 68 11 … D3 16
    判:  [645] 读费率参量 年时区数(zone, DI 04000201) -> 值[1B]=01   [PASS]

⚠ **协议那一格不由库函数填, 也不由发送者传 —— 它是帧自己的属性**(见下面 `Frame`)。组帧那一刻
  (`frame_645`/`frame_698`)就知道自己是什么协议, `Frame` 把它钉在字节上, `tx_recv`/`_opout`
  从帧上取。所以"选了哪个组帧函数"与"日志上标哪个协议"是**同一次动作**, 中间没有任何一处
  可以漏标或标错。老写法里是由**调用点**用自由串传 `tag=`, 而 `tag=` 一个参数要扛四种意思
  (协议 / 动词 / 帧 id / 地址), 于是协议被 `tag="relay"` 顶掉 —— 那正是
  `log/3_1_rate_num_20260918_105031.log` 里"帧那一行不标 645/698"的根。

⚠ **`what`(功能名)是唯一的自由文本, 且不许含方括号头**。方向 / 对象 / 协议三格都由本模块出,
  调用点一个都不许写。2026-09-18 实表跑 3-1 抓到过反例: 9 处调用点把
  `"[发送→管理芯] 645 读费率参量 …"` 整串烤进功能名, 于是同一行的头打了两遍, 而且**收帧那一行
  也写着"发送"**(串里那个是硬编码的, 它不知道这是收)。检查在 `scripts/_check_loghead.py`。

⚠ **功能名与本次参数不在这里定**。功能名是各库函数自己的话(`_opout` 的 `head`), **原样**透传 ——
  本模块不缩写、不改写、不另起短名。2026-09-18 设计时吃过一次亏: 给 DI 04000201(年时区数)那帧
  顺手贴了个 `[费率数]`, 而费率数是 DI 04000204, **是另一个功能** —— `年时区数`/`费率数` 只差
  一个字, 一旦自己起短名就会串。所以这里只定**字形**, 不定**名词**。

用法: 本模块只出这两种行 —— 定形的字符串, 外加判定行那一次 `print`; 不 import 本仓任何东西
(不碰串口、不碰 project), 谁都能用。
"""
import sys

# ============================ 一、协议词汇(定义一次) ============================
# 与 `check_*.py` 里头的"硬性规矩"同一条纪律: 一个名字只在一处出现, 别处一律引用它 ——
# 免得哪天有人写出 "698协议" / "6.9.8" / "0698" 这种第三种写法, 而没有任何东西会红。
PROTO_645 = "645"
PROTO_698 = "698"
PROTO_AA80 = "AA80"          # AA80 走 645 封装直读管理芯 RAM, 是**本表私有**的一族, 单列一格
PROTOS = (PROTO_645, PROTO_698, PROTO_AA80)


# ============================ 二、判定三态 ============================
# 与本仓判据的三态口径一致: 有凭据说真 / 有凭据说假 / 一条凭据都没读到。
# ⚠ `None` 是"**判不出来**", 不是"没判" —— 不许悄悄当 FAIL 也不许悄悄当 PASS。
VERDICT = {True: "PASS", False: "FAIL", None: "TBD"}


# ============================ 三、帧: 协议随身 ============================
class Frame(bytes):
    """一帧报文, **自带它属于哪个协议** —— `bytes` 原样, 只多一个 `proto`。

    **为什么协议挂在帧上, 而不是当参数传**: 组帧那一刻(`frame_645`/`frame_698`/`read_aa80_645`)
    就知道自己是什么协议, 那是**唯一一次**必须说对的地方; 之后再往下传, 每过一道手就多一次
    "传错了/忘传了"的机会。钉在帧上以后, `tx_recv` 与 `_opout` 都从同一个字节串上取, 两行
    (发/收)与判定行**不可能标成两个协议** —— 它们读的是同一个字段。

    ⚠ 它**就是** `bytes`: `ser.write(f)` / `len(f)` / `f.hex(" ")` 全照旧, 现有 28 个发帧调用点
      一行都不用改。代价是**切片会退回普通 `bytes`**(丢 `proto`) —— 只用整帧发, 不成问题。
    ⚠ `proto` **只许取上面词表里的值**, 写别的当场炸(`"645协议"` / `"6.9.8"` 这类第三种写法进不来)。
      空串是**允许**的: 通用组帧/手搓帧确实不知道协议, 那是"没标", 不是"标错"。
    """
    proto = ""

    def __new__(cls, raw, proto=""):
        if proto and proto not in PROTOS:
            raise ValueError(
                "Frame 的协议只认词表 %r 里的值, 实为 %r —— 别自造第三种写法" % (PROTOS, proto))
        self = super().__new__(cls, raw)
        self.proto = proto
        return self


def _head(arrow, proto, peer):
    """`[645] [发→管理芯]` —— 协议格 + 方向格。两格都可缺, 缺了就整格省掉(不留空括号)。"""
    out = []
    if proto:
        out.append("[%s]" % proto)
    if peer:
        out.append("[%s%s%s]" % (arrow, "→" if arrow == "发" else "←", peer))
    else:
        out.append("[%s]" % arrow)
    return " ".join(out)


def proto_of(frame):
    """从**帧**上取协议。取不到就是空串 —— 通用组帧器组的帧、手搓的帧、`None` 都是"没标",
    不是"标错"(区别见 `Frame` 的 docstring)。

    ⚠ 全仓只有这一处读 `proto` 这个属性。哪天载体换了(不再是 `bytes` 子类), 改这里一处。
    """
    return getattr(frame, "proto", "")


def frame_line(arrow, data, proto="", peer="", what=""):
    """一帧的日志行(收/发各一行)。`arrow` 只能是 `"发"` 或 `"收"`。

    `data` 为空(表压根没应)时打 `(无应答)` —— 与事件流那一路的口径一致: 那边记的是 `hex=None`,
      **不是** `"(无应答)"` 这个字符串, 于是读方一句话分得出"表没应"与"应了、内容恰好是这个"。
    `20B` 那个帧长**要留**: 它是"发出去多少字节"与"收回来多少字节"的凭据, 少一样就没法逐字节核帧。
    """
    if arrow not in ("发", "收"):
        raise ValueError("frame_line 的方向只认 '发'/'收', 实为 %r" % (arrow,))
    head = " ".join(filter(None, [_head(arrow, proto, peer), what]))
    tail = ("%dB  %s" % (len(data), bytes(data).hex(" ").upper())) if data else "(无应答)"
    return "%s %s" % (head, tail)


def result_line(head_text, res, ok=True, proto=""):
    """一次操作的判定行。`ok` 三态: True=PASS / False=FAIL / None=TBD。

    老的 `== ` 前缀在**没给协议**时照旧 —— 不是所有判定都跟在一帧后面(有 `frame=None` 的,
      比如"画像未登记 g_CompVal, 无法判前置"), 那些行仍要能一眼认出是"一次操作的结论"。
    """
    if ok not in VERDICT:
        raise ValueError("result_line 的 ok 只认 True/False/None 三态, 实为 %r" % (ok,))
    return "%s%s -> %s   [%s]" % (("[%s] " % proto) if proto else "== ",
                                 head_text, res, VERDICT[ok])


def opout(head_text, frame, res, ok=True, quiet=False):
    """一次语义操作的判定行 → 打印(**一行**)。`quiet=True` 只静默返回(轮询/快照用, 免刷屏)。

    它是 `result_line` 的出口, 不是第二份字形: 字形仍只上面那一处定义。
    ⚠ 帧**只用来取协议**(`proto_of`), 所以"没有帧"的判定(画像未登记、无法判前置)传 `None` 就行,
      与传空帧同效 —— 那种行以 `== ` 开头。
    ⚠ 它**不打帧**: 帧只在发出去的那一刻由 `portsel.tx_recv` 打一行(见模块头那段"一帧落三处")。
    """
    if quiet:
        return
    print(result_line(head_text, res, ok, proto=proto_of(frame)))


# ============================ 四、调试行: 一个维度, 来源在格内 ============================
# 2026-09-18 并: 原先 `[gdb]` / `[srv]` / `[SWD]` **各占一个词头**, 可它们本来是**同一个维度**
# —— 调试侧留痕 —— 的几条来源(2026-09-20 双探针起再加一条 `探针`)。各拼各的字形的下场,
# 与帧行当初一模一样(见模块头):
#   · `breakpoint.Session._warn` 的降级/提醒行把来源**写死**成 `[gdb]`;
#   · `swdbg/probe.py` 的 SWD 那一路**根本不走** `_trace_emit`, 自己 `print`。
# 于是"哪条来源"既多了一个可以写错的地方, 又没有一处是它的唯一定义。
# 现在跟帧行同一套文法: **词头一格 + 来源格 + 自由文本**。读的人只需学一种形状。
DEBUG_GDB = "gdb"
DEBUG_SRV = "srv"            # gdbserver 的原声 stdout(不是 MI 流) —— 两端都是这一条
                             #   (J-Link 端是 JLinkGDBServerCL, CMSIS-DAP 端是 pyocd gdbserver)
DEBUG_SWD = "SWD"            # 探针直读(SWD), 不经 gdb —— **读原语**那一路
DEBUG_PROBE = "探针"          # 「该用哪一支探针」—— 枚举 / 逐个证明 / 选中(2026-09-20 双端起)
DEBUG_SOURCES = (DEBUG_GDB, DEBUG_SRV, DEBUG_SWD, DEBUG_PROBE)


def debug_line(source, text):
    """调试侧留痕的一行: `[调试] [来源] 内容`。

    ⚠ 来源是**格**, 不是自由文本 —— 只认上面词表里的四条, 写别的当场炸。要新开一路,
      先在这里登记, 否则就又长出第五种词头(这正是这条纪律要治的东西)。
    ⚠ `SWD` 与 `探针` 的分工(2026-09-20): **读**归 `SWD`(连上之后读了哪个地址、读回来什么),
      **选**归 `探针`(枚举到几支、哪一支证明成功、最后选中谁)。合成一条会让人分不清
      "探针连上了但读不到"与"探针压根没选出来"。
    ⚠ 来源**留在行内**, 不并进词头: 既有文档(`breakpoint`)反复叫人在日志里搜
      `[srv] ...Target halted`, 那个子串在新字形里**原样还在**, 老的搜法不作废。
    """
    if source not in DEBUG_SOURCES:
        raise ValueError(
            "调试行的来源只认 %r, 实为 %r —— 新开一路先在这里登记" % (DEBUG_SOURCES, source))
    return "[调试] [%s] %s" % (source, text)


# ============================ 五、自检 ============================
# 照本仓老规矩: 改一个字节应校验不过, 否则"校验通过"是句空话。
def selftest():
    """现造反例证明字形**真的**按上面写的那样出 —— 不是"跑过了就算对"。"""
    ok = True

    def chk(label, got, want):
        good = (got == want)
        print("%-34s %s" % (label, "OK" if good else "FAIL\n    实得: %r\n    应为: %r" % (got, want)))
        return good

    f = bytes.fromhex("FEFEFEFE68111111111168" "1104343533371E16")
    ok &= chk("发帧整行",
              frame_line("发", f, proto=PROTO_645, peer="管理芯", what="读表钟"),
              "[645] [发→管理芯] 读表钟 %dB  %s" % (len(f), f.hex(" ").upper()))
    ok &= chk("收帧箭头朝回",
              frame_line("收", f, proto=PROTO_645, peer="管理芯", what="读表钟"),
              "[645] [收←管理芯] 读表钟 %dB  %s" % (len(f), f.hex(" ").upper()))
    ok &= chk("无应答",
              frame_line("收", b"", proto=PROTO_698, peer="计量芯", what="读表钟"),
              "[698] [收←计量芯] 读表钟 (无应答)")
    ok &= chk("无对象时光方向",
              frame_line("发", f, proto=PROTO_645),
              "[645] [发] %dB  %s" % (len(f), f.hex(" ").upper()))
    ok &= chk("三格全空(老调用点)",
              frame_line("发", f),
              "[发] %dB  %s" % (len(f), f.hex(" ").upper()))

    ok &= chk("判定行", result_line("读表钟", "值[1B]=01", True, proto=PROTO_645),
              "[645] 读表钟 -> 值[1B]=01   [PASS]")
    ok &= chk("判定行无协议(留 ==)",
              result_line("读表钟", "无应答", None), "== 读表钟 -> 无应答   [TBD]")

    # 反例 1: 方向写错必须当场炸, 不许悄悄出一行看着正常的日志
    try:
        frame_line("发射", f)
        ok &= chk("方向写错被抓", "没炸", "ValueError")
    except ValueError:
        ok &= chk("方向写错被抓", "ValueError", "ValueError")

    # 反例 2: ok 传了"三态以外"的东西也必须炸(比如把 "FAIL" 这个字符串传进来)
    try:
        result_line("读表钟", "x", ok="FAIL")
        ok &= chk("ok 非三态被抓", "没炸", "ValueError")
    except ValueError:
        ok &= chk("ok 非三态被抓", "ValueError", "ValueError")

    # 反例 3: 协议词表必须**真的**被当回事 —— 谁绕过常量、自己写个没登记的协议串, 这里要看得出来
    ok &= chk("协议词表长度", len(PROTOS), 3)
    ok &= chk("没登记的协议串不在词表里", "645协议" in PROTOS, False)

    # ---- Frame: 协议随帧走(2026-09-18 加) ----
    fr645 = Frame(b"\x68\x11\x16", PROTO_645)
    ok &= chk("帧自带协议", proto_of(fr645), PROTO_645)
    ok &= chk("帧没标协议 ⇒ 空串(是『没标』不是『标错』)", proto_of(Frame(b"\x68")), "")
    ok &= chk("手搓的裸 bytes / None ⇒ 空串", proto_of(b"\x68\x11") + "|" + proto_of(None), "|")
    ok &= chk("它**就是** bytes(发帧/长度/hex 全照旧)", isinstance(fr645, bytes) and len(fr645) == 3,
              True)
    # 反例 4: 自造第三种协议写法必须当场炸 —— 否则词表只是注释
    try:
        Frame(b"\x68", "645协议")
        ok &= chk("自造协议写法被抓", "没炸", "ValueError")
    except ValueError:
        ok &= chk("自造协议写法被抓", "ValueError", "ValueError")

    # ---- 调试行: 一个维度, 来源在格内(2026-09-18 并) ----
    ok &= chk("调试行·gdb", debug_line(DEBUG_GDB, "← ^done"), "[调试] [gdb] ← ^done")
    ok &= chk("调试行·srv", debug_line(DEBUG_SRV, "Target halted (PC = 0x159FA)"),
              "[调试] [srv] Target halted (PC = 0x159FA)")
    ok &= chk("调试行·SWD", debug_line(DEBUG_SWD, "读 0x20009088 长4 → BB C3 33 33"),
              "[调试] [SWD] 读 0x20009088 长4 → BB C3 33 33")
    ok &= chk("调试行·探针", debug_line(DEBUG_PROBE, "选中 cmsis-dap 端 DC1C128D…"),
              "[调试] [探针] 选中 cmsis-dap 端 DC1C128D…")
    # 老搜法不作废: 文档里叫人在日志里搜 `[srv] ...Target halted`, 那个子串得原样还在
    ok &= chk("老的 [srv] 搜法仍能命中", "[srv] Target halted" in debug_line(DEBUG_SRV, "Target halted"),
              True)
    ok &= chk("来源词表长度", len(DEBUG_SOURCES), 4)
    # 反例 5: 自造一个来源必须当场炸 —— 否则"来源是格"只是句注释
    #   ⚠ 这里拿 `"jlink"` 当非法串: 它是**探针端**的名字(见 swdbg/probesel.py 的 BACKENDS),
    #     与来源词表是两回事 —— 端名写进来源格正是最容易犯的那一个错。
    try:
        debug_line("jlink", "x")
        ok &= chk("自造来源被抓", "没炸", "ValueError")
    except ValueError:
        ok &= chk("自造来源被抓", "ValueError", "ValueError")

    print("SELFTEST loglabel:", "ALL PASS" if ok else "HAS FAILURE")
    return ok


if __name__ == "__main__":
    # ⚠ 本行是 2026-09-20 补的, **不是装饰**: 自检里那些标签带 `→` / `⇒` 这类字符, 而
    #   Windows 控制台默认是 GBK —— 直接跑会在第 215 行抛 UnicodeEncodeError, 人看到的是
    #   "自检崩了"而不是"某条判据不成立"。**一个跑不起来的自检等于没有自检**(本仓老账)。
    #   库被 import 时**不要**调它(会动宿主进程的 stdout)—— 故只钉在 `__main__` 这一支上。
    #   ⚠ 2026-09-20 删掉了这里原先那三行路径引导: 那时 `python src/common/loglabel.py` 直接跑,
    #   sys.path[0] 是 src/common(不是 src/), `common.console` 取不到。现在 `common` 由仓根
    #   pyproject.toml 声明、`pip install -e .` 装上, 直接跑也进得来, 不必再自己塞 sys.path。
    from common.console import ensure_utf8_stdout
    ensure_utf8_stdout()
    sys.exit(0 if selftest() else 1)
