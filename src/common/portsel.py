# -*- coding: utf-8 -*-
"""
common/portsel.py —— 串口**传输与选口**层(与协议无关的一半)

本模块答两个问题, 都是"这台机器的事实", 都与 698/645 无关:

    ① **收发本身**: 一个串口对象长什么样(`RawCom`) / 一轮收发怎么做(`tx_recv`)
       / 线路参数从哪来(`_serial_kw`, 取自装机卡带)
    ② **本机该用哪个口**: 判据是哪几个候选(`candidates`) / 命中几个 / 打不开怎么报

**边界(另一半不在这里, 有意)**
    **口后面是不是那块表** → 不在本模块。那是**协议动作**(要发一帧去问), 见下面「探活」一节。
    本模块只到"这个口开得开、能收发"为止; "它后面是表吗"由协议层回答。

**为什么这层住 common/**
判据是"这台机器的事实"(卡带), 枚举是"纯系统调用", 收发是"字节进字节出" —— 三者都零协议、零画像。
把它们住进 meterlib, 就会让"换块表"和"桥插在哪"搅在一起; 住进 machine/ 卡带里, 卡带就不再是纯数据。

**为什么它认得出 698 / 645 / IIC / SPI**
它不认。凡是实现 `reset_input_buffer` / `write` / `flush` / `read` / `close` 五个动作的对象,
本模块一律当串口用(见 `tx_recv` 文件头的鸭子类型声明)。加一条新通路 = 照这五个动作实现一个类,
**协议层一个字不改**。

判据语义(与 `swdbg/jlink.py:resolve_sn` 刻意对称)
-------------------------------------------------
    match 里**填了**的字段 = 必须全满足; `None` = 不筛那一项
    0 个候选 → 合法状态(「桥没插」), `candidates()` 返回空表, 由调用方喊
    **绝不"取第一个"** —— 多个候选时由调用方(带握手的那一方)决定, 不许这里替它猜
"""
import os
import time

from common import faultlog


__all__ = ["PortselError", "list_ports", "candidates", "describe", "pick",
           "RawCom", "tx_recv", "send_frame", "open_com", "set_probe", "probe_installed"]

# match 里认得的键。多写一个键 = 拼错一个键而**静默不筛**, 故这里点名校验(见 candidates)。
_KEYS = ("vid", "pid", "serial", "desc")


class PortselError(RuntimeError):
    """枚举不可用 / 判据不命中 / 候选含糊时抛这个, 带人话解释。"""


# =====================================================================================
# 一、选口 —— 枚举
# =====================================================================================
def list_ports():
    """系统当前认得的所有串口 → pyserial 的 ListPortInfo 列表。

    枚举**本身**不可用(没装 pyserial / 底层报错) 才抛 —— 「一个口都没有」是 `[]`, 不是错误。
    """
    try:
        import serial.tools.list_ports
    except Exception as exc:                      # pragma: no cover - 环境问题
        raise PortselError(
            "没装 pyserial(枚举串口要用它)。装法:\n"
            "    pip install pyserial -i https://pypi.tuna.tsinghua.edu.cn/simple\n"
            "(原错误: %s)" % exc)
    try:
        return list(serial.tools.list_ports.comports())
    except Exception as exc:
        raise PortselError("串口枚举失败: %s" % exc)


def _one(p):
    """pyserial 的 ListPortInfo → 本模块的字典形状(字段缺失一律 None, 不缺就缺得明白)。"""
    return {"device": getattr(p, "device", None),
            "desc": getattr(p, "description", None) or "",
            "vid": getattr(p, "vid", None),
            "pid": getattr(p, "pid", None),
            "serial": getattr(p, "serial_number", None),
            "hwid": getattr(p, "hwid", None) or ""}


def _hit(c, match):
    """单个候选过判据? —— 填了的字段必须全中。`serial` 单独处理: 大小写/前后空白不算差异。"""
    if match.get("vid") is not None and c["vid"] != match["vid"]:
        return False
    if match.get("pid") is not None and c["pid"] != match["pid"]:
        return False
    if match.get("serial") is not None:
        want = str(match["serial"]).strip().upper()
        got = (c["serial"] or "").strip().upper()
        if got != want:
            return False
    if match.get("desc") is not None:
        if str(match["desc"]).lower() not in (c["desc"] or "").lower():
            return False
    return True


def candidates(match, ports=None):
    """按判据筛 → `[{"device","desc","vid","pid","serial","hwid"}, …]`(按口名排序)。

    `ports` = 注入用(自检喂假口表, 不碰真系统); None ⇒ 现枚举。
    候选为空 = 合法(桥没插), **不抛** —— 抛不抛是调用方的分寸(见模块头)。
    """
    bad = [k for k in (match or {}) if k not in _KEYS]
    if bad:
        # 拼错的键**必须响** : 静默不筛 = 把判据写松了而没人知道。
        raise PortselError("判据里有不认识的键 %s(只认 %s)" % (bad, list(_KEYS)))
    raw = list_ports() if ports is None else list(ports)
    hits = [_one(p) for p in raw]
    hits = [c for c in hits if _hit(c, match or {})]
    hits.sort(key=lambda c: (c["device"] or ""))
    return hits


def describe(match):
    """判据 → 一句人话(报错与体检里用)。空判据明说"不筛", 别让人以为筛过了。"""
    if not match:
        return "(空判据 —— 任何串口都算候选)"
    bits = []
    for k in _KEYS:
        v = (match or {}).get(k)
        if v is None:
            continue
        bits.append("%s=%s" % (k, ("0x%04X" % v) if k in ("vid", "pid") else v))
    return " ".join(bits) if bits else "(判据全为 None —— 等于不筛)"


def pick(match, ports=None):
    """判据 → **恰好一个**候选的口名; 0 个 / 多个都抛。

    给"不做握手也要拿个口"的场合(体检报告、应急)。带握手的那条路(open_com)不用它 ——
    它要拿**候选列表**逐个证明, 见模块头。
    """
    hits = candidates(match, ports=ports)
    if len(hits) == 1:
        return hits[0]["device"]
    got = " ".join(c["device"] or "?" for c in hits) or "(无)"
    if not hits:
        raise PortselError(
            "没有串口满足判据 [%s]。检查: ①USB-485 桥插好没有; ②驱动装了没有"
            "(设备管理器里有没有 'Silicon Labs CP210x'); ③判据是不是写窄了。" % describe(match))
    raise PortselError(
        "有 %d 个串口都满足判据 [%s]: %s\n"
        "→ 把判据写窄(加 serial=)再跑 —— **不替你猜**。" % (len(hits), describe(match), got))


# =====================================================================================
# 二、传输 —— 一个串口对象要提供的五个动作
# =====================================================================================
# 任何通道对象只要实现这五个动作, 本模块就当串口用:
#     reset_input_buffer()  丢弃待读字节(发帧前清场)
#     write(b)              发字节, 返回发出几个
#     flush()               等发完
#     read(n)               读至多 n 字节(可以立刻返回空 —— 由调用方轮询)
#     close()               关
# 协议层(698 / 645)只认这五个, 不认"串口"这个名字。加 IIC / SPI 就是照它实现一个新类。
_PARITY = None                      # 首次用到时从 serial 取(见 _parity)
_COM_CACHE = []                     # 进程内缓存: 自动识别的结果(见 `_resolve_com` 的 ⚠)
_HANDSHAKE_TIMEOUT = 2.0            # 握手等应答的上限(秒)


def _parity_map():
    """parity 字母 → pyserial 常量。延迟取: 本模块的"选口"半边不该因为没装 pyserial 就 import 不了。"""
    global _PARITY
    if _PARITY is None:
        import serial
        _PARITY = {"E": serial.PARITY_EVEN, "O": serial.PARITY_ODD, "N": serial.PARITY_NONE}
    return _PARITY


class RawCom:
    """pyserial 打不开/SetCommState 卡死时的 ctypes 直连 COM(实测 bench 曾 wedge).
    与 pyserial 暴露给 tx_recv 的接口一致: reset_input_buffer/write/flush/read/close.
    端口线路参数假设外部已配好(本表 8E1, CP210x 桥). 仅 Windows."""
    _PURGE = 0x000A          # PURGE_RXABORT | PURGE_RXCLEAR

    def __init__(self, port=None):
        import ctypes
        port = port or _resolve_com()
        if os.name != "nt":
            raise OSError("RawCom 仅在 Windows 可用")
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateFileW.restype = ctypes.c_void_p
        k.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                  ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
        k.SetCommTimeouts.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        k.ReadFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                               ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
        k.WriteFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                                ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
        k.PurgeComm.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        self._k = k
        self._ct = ctypes
        h = k.CreateFileW("\\\\.\\" + port, 0xC0000000, 0, None, 3, 0, None)
        if h in (0, ctypes.c_void_p(-1).value):
            raise OSError("RawCom CreateFileW(%s) err=%d" % (port, ctypes.get_last_error()))
        self.h = h
        self._closed = False
        self.raw_com = True   # 供冒烟/调用方探测: 本对象为 ctypes 直连而非 pyserial
        self.port = port      # 记下开的哪个口: 运行日志要凭它写"这一轮开的是什么串口"那行
        # ReadIntervalTimeout=MAXDWORD 其余0 => 非阻塞: 有数据立即回, 无数据立即返回空(由调用方轮询)
        ct = (ctypes.c_uint32 * 5)(0xFFFFFFFF, 0, 0, 0, 0)
        k.SetCommTimeouts(h, ct)

    def read(self, n=1):
        ctypes = self._ct
        r = (ctypes.c_ubyte * n)(); got = ctypes.c_uint32(0)
        if not self._k.ReadFile(self.h, r, n, ctypes.byref(got), None):
            return b""
        return bytes(r[:got.value])

    def write(self, b):
        ctypes = self._ct
        b = bytes(b)
        buf = (ctypes.c_ubyte * len(b)).from_buffer_copy(b)
        got = ctypes.c_uint32(0)
        self._k.WriteFile(self.h, buf, len(b), ctypes.byref(got), None)
        return got.value

    def flush(self):
        pass

    def reset_input_buffer(self):
        self._k.PurgeComm(self.h, self._PURGE)

    def close(self):
        if not self._closed:
            self._closed = True
            try:
                self._k.CloseHandle(self.h)
            except Exception:
                pass


def _serial_kw(baud=None):
    """串口参数 —— **从装机卡带取**(2026-09-14 起)。

    原先 `open_com` 把 9600/8/E/1 写死在函数体里, 而卡带声明的 `BAUD/PARITY/BYTESIZE/STOPBITS`
    **没人吃** —— 改了卡带不生效, 又一处"两处落笔"。现在以卡带为准; 卡带缺那一项(或压根没卡带)
    时用 645/698 的标准值 9600 8E1 —— 那是**函数默认值**, 不是第二份事实源(卡带在就一定以卡带为准)。
    """
    from common import machspec
    import serial

    def pick_(name, dflt):
        v = machspec.get(name)
        return dflt if v is None else v

    return {"baudrate": int(baud if baud is not None else pick_("BAUD", 9600)),
            "bytesize": int(pick_("BYTESIZE", 8)),
            "parity": _parity_map().get(str(pick_("PARITY", "E")).upper(), serial.PARITY_EVEN),
            "stopbits": int(pick_("STOPBITS", 1)),
            "timeout": 0.05, "write_timeout": 2}


def tx_recv(ser, frame, wait=2.0, tag="", peer="", what="", quiet=False):
    """发送一帧并收应答(至静默0.15s或超时)。返回字节串(含可能FE前导)。

    `quiet=True` ⇒ **只不打印** 那两行(轮询/快照用, 免刷屏)。
    ⚠ `quiet` 关的是**给人看的那一路**, 事件流(`serial.tx`/`serial.rx`)**照发** —— 两路的分工见
      common/events 模块头: 人读的可以少, 机器读的那一路缺一条就是缺一条证据。

    ---- 人读那两行的字形(2026-09-18 定, 见 common/loglabel 模块头) ----
    `[协议] [方向+对象] 功能名 帧长 帧体`, 发一行、收一行, 各带全套标签。

    ⚠ **协议不是参数, 是帧自己的属性**(`loglabel.proto_of(frame)`)。所以这个函数里没有 `proto=`,
      也没有任何机会把它标成两个协议 —— 发那一行与收那一行读的是同一个字段。
      通用组帧器组的帧、手搓的帧没有这个属性 ⇒ 协议格整格省掉(那是"没标", 不是"标错")。
    ⚠ **字形只有这一种**(2026-09-18 撤掉老的 `TX(20) tag: <HEX>`)。撤得掉是因为协议不再依赖
      调用点传: 28 个发帧调用点一行不用改, 帧行上就带上了协议。全仓没有一处解析 `TX(`/`RX(`
      (只有本函数打), 所以撤掉不牵动任何读日志的代码。
    ⚠ `tag` **只管机器那一路**(事件流的 `tag` 字段), 不进人读的那一行。它历史上被当
      "协议/动词/帧 id/地址"四种东西用过, 那是"帧行不标协议"的病根; 那些语义现在各有其位:
      协议随帧走、对象是 `peer`、功能是 `what`、帧 id 归事件流的 `tag`。
    """
    from common import loglabel
    proto = loglabel.proto_of(frame)
    if not quiet:
        print(loglabel.frame_line("发", frame, proto=proto, peer=peer, what=what))
    # 事件流那一条(2026-09-17 立, 见 common/events 模块头): 上面那行 print **只排过版, 只够人看** ——
    # 复盘 `5_2_overload` 那次失败时, "这一帧几点发的 / 发的是哪一帧"在 172 份日志里一个字都答不出来。
    # ⚠ 三条不许动的规矩, 违反任何一条整轮就废(这一路跑在 `breakpoint.with_trigger` 的**后台线程**上,
    #   主线程同时在等断点): ① **print 先、emit 后**, stdout 的次序与时序一个字都不许改;
    #   ② 只组 dict + 一次写, 不加锁、不 sleep、不额外读串口; ③ 只记**手上已经有的**东西。
    # 先问 `enabled()` 再打: `.hex(" ").upper()` 要逐字节拼串, 没绑事件流时那是白花的(events 模块头的用法)。
    from common import events
    if events.enabled():
        events.emit("serial.tx", n=len(frame), tag=tag, hex=frame.hex(" ").upper())
    ser.reset_input_buffer()
    ser.write(frame); ser.flush()
    buf = bytearray(); t0 = time.time(); idle = 0.0
    while time.time() - t0 < wait:
        n = ser.read(256)
        if n:
            buf += n; idle = 0.0
        else:
            idle += 0.05
            if idle > 0.15 and buf:
                break
        time.sleep(0.02)
    if not quiet:
        print(loglabel.frame_line("收", bytes(buf), proto=proto, peer=peer, what=what))
    # 收不到时 `hex=None`, **不是** `"(无应答)"` —— 这一路是给机器读的, `None` 才是可判的:
    # 读方 `r["hex"] is None` 一句话就分得出"表压根没应"与"应了、内容恰好是这个字符串"。
    # `waited` = **实际**等了多久(不是 `wait=` 那个上限) —— 「这一帧为什么慢」只有它答得了;
    # 取的就是上面那个 `t0`, 不重新计时也不多读一次串口。`buf.hex(" ")` 空串是假值, `or None` 正好接住。
    if events.enabled():
        events.emit("serial.rx", n=len(buf), tag=tag, hex=buf.hex(" ").upper() or None,
                waited=round(time.time() - t0, 3))
    return bytes(buf)


def send_frame(ser, frame, wait=2.0, tag="", peer="", what=""):
    """底层一轮收发: 发已组好的任意帧(698/645 均可), 返回应答字节. ser 须由调用方开/关.

    **与 `tx_recv` 的分别只有一条**: 本函数要求现成串口对象, 没有就抛 —— 它答的是"发一帧"
    这个动作, 不负责开/关口。零协议: 帧是 645 还是 698 由帧自己带着(`loglabel.Frame`)。

    `peer`(发给谁)与 `what`(这是什么功能)是**人读那两行的标签**, 由调用它的库函数填。
    ⚠ **没有 `proto=`** —— 协议是帧自己的属性, 由 `frame_645`/`frame_698`/`read_aa80_645`
      在组帧时钉上。原先这里收一个 `proto=` 参数, 是把它当"发送者的参数"看;
      那样每过一道手就多一次漏传/传错的机会, 而漏传的症状恰好是"帧行上不标协议"——
      一个没人会当场发现、只会在复盘时才发现缺证据的症状。现在它随帧走, 漏不了。
    """
    if ser is None:
        raise ValueError("send_frame 需现成串口对象(ser), 本层不负责开/关串口")
    # ⚠ **原样传, 不许 `bytes(frame)`** —— 那会把 `Frame` 降级回裸 `bytes`, 协议字段随之丢光,
    #   症状就是"帧行上又不标协议了"。它本来就是 bytes, 不需要转。
    return tx_recv(ser, frame, wait=wait, tag=tag, peer=peer, what=what)


# =====================================================================================
# 三、探活 —— "口后面是不是那块表"(由**协议层**装进来, 不是本模块自己带的)
# =====================================================================================
# 本模块不知道 698/645, 也不该知道。但"筛出来的候选口到底哪个后面是本表"**必须发一帧去问**,
# 而"发什么帧"是协议知识。所以这里只留一个**装配点**(与 swdbg/resolve 的 configure 同一套做法):
#
#     meterlib/p698.py 在 import 时调 `portsel.set_probe(handshake_clock)`。
#
# ⚠ 没装探针时 `open_com()` **不许静默放过** —— 那会把"确定错"换成"看着一样错"(本仓反复
#   治理的那类静默错误)。它抛, 并说清"该 import 谁"。
_PROBE = None
_PROBE_WHERE = ""


def set_probe(fn, where=""):
    """装上探活函数(全仓只应有 p698 一处调它)。`fn(ser) -> (bool, 人话)`。"""
    global _PROBE, _PROBE_WHERE
    if fn is not None and _PROBE is not None and fn is not _PROBE:
        raise PortselError(
            "探活函数已经被 %s 装过了, 现在又想装 %s —— 两份配方 = 分叉的开始。"
            % (_PROBE_WHERE or "(未记名)", where or "(未记名)"))
    _PROBE = fn
    _PROBE_WHERE = where


def probe_installed():
    """探活装上了没有(自检与体检报告用)。"""
    return _PROBE is not None


def _probe_missing_msg():
    """没装探针时要说的话(单独一句, 好让自检能验它、不必真去装一个探针)。"""
    return (
        "还没有探活函数 —— 「口后面是不是本表」要靠发一帧去问, 那是协议层的事。\n"
        "  → 在脚本里 `from meterlib import p698`(它 import 时会把探活装上), 或用 `probe=False`。")


def _require_probe():
    if _PROBE is None:
        raise PortselError(_probe_missing_msg())
    return _PROBE


# =====================================================================================
# 三之二、实测凭据 —— 这一次开出来的口, 是不是**当场问过表、表也答了**
# =====================================================================================
# 「这一轮是不是跑在真表上」原先只有一句自述(`trial._serial_note` 的"真串口"),
# 而那句话是**字**, 谁都能写。这里把它换成**当场从设备上读出来的东西**:
#   · 桥的 VID:PID 与 USB 序列号 —— 从 `list_ports()` 现查, 桥不在位上查不到;
#   · 探活那一帧的应答 —— `handshake_clock` 是**单播到本表地址**的读钟帧,
#     答了才说明口后面是本表, 而且**答出来的表钟是从表上现读的时间串**(随时间变)。
# 凭据由 `open_com` 登记, 读侧只认这一份(见 `live_proof`)。谁也别在别处重算一遍 ——
# 重算就是第二份判据, 而"两条通路各判各的"正是本仓反复治理的那类分叉。
_LIVE = {}


def live_proof():
    """最近一次开串口时的实测凭据(进程内单点) → dict 或 None(这一轮压根没开过口)。

    形状: `{"port": "COM3", "bridge": {"vidpid": "10C4:EA60", "sn": "...", "desc": "..."},
            "probe": True, "probe_note": "应了, 表钟=2026-09-22 10:33:05", "when": "..."}`
    `probe=False` ⇒ 口开着, 但**表答没答不知道** —— 拿这种凭据去当"实测"就是把
    "桥插着"当成"表在", 正是本仓不认的那一步。"""
    return dict(_LIVE) if _LIVE else None


def _bridge_fingerprint(device):
    """`device` 那条口的桥指纹, 从**当场枚举到的**设备树取; 取不到就是 None(不编)。"""
    try:
        for p in list_ports():
            if p.device == device:
                vid, pid = getattr(p, "vid", None), getattr(p, "pid", None)
                return {"vidpid": ("%04X:%04X" % (vid, pid)) if vid and pid else None,
                        "sn": getattr(p, "serial_number", None),
                        "desc": getattr(p, "description", None) or ""}
    except Exception as exc:
        return {"vidpid": None, "sn": None, "desc": "查不到: %s" % exc}
    return {"vidpid": None, "sn": None, "desc": "枚举里没有这条口"}


def _record_proof(device, ok, why):
    """登记这一次的凭据(唯一写入口)。`ok` = 探活那一帧**表答了没有**。"""
    _LIVE.clear()
    _LIVE.update({"port": device, "bridge": _bridge_fingerprint(device),
                  "probe": bool(ok), "probe_note": why or "",
                  "when": time.strftime("%Y-%m-%d %H:%M:%S")})


def proof_line():
    """凭据 → 日志头那一行(没有凭据就返回 None, **不编**)。

    字形只在这里出一次: 写侧(`trial._serial_note`)、读侧(`runlog.real_run_of`)认的是同一串字。"""
    p = live_proof()
    if not p:
        return None
    b = p["bridge"]
    return ("桥 %s SN=%s | 探活: %s"
            % (b["vidpid"] or "?", b["sn"] or "?",
               p["probe_note"] or ("应了" if p["probe"] else "**没应答**")))


def _probe_port(device, probe_fn):
    """开 `device` 跑一次探活 → (bool, 说明)。开不了就是 False —— **不抛**(还要接着试别的候选)。

    用完**立刻关**; 让 OS 把口还回去再让 `open_com` 正式开(Windows 上即刻重开同一 COM 偶发失败,
    故关后小睡一下 —— 这一觉只花在自动识别这条路上, 显式传口的一条不走这里)。
    """
    import serial
    try:
        ser = serial.Serial(device, **_serial_kw())
    except Exception as exc:
        if os.name != "nt":
            return False, "打不开: %s" % exc
        try:
            ser = RawCom(device)                 # 与 open_com 同款回退
        except Exception as exc2:
            return False, "打不开: %s" % exc2
    try:
        return probe_fn(ser)
    finally:
        try:
            ser.close()
        except Exception:
            pass
        time.sleep(0.2)


def _resolve_com(probe=True):
    """拿"本机该用哪个串口"。三级(口径与 `machine/win11_c07751.py` 的 `COM` 注释同一份):

        ① 卡带**钉死**(`COM="COM3"`) → 就用它(人工逃生判定, 不筛)
        ② 卡带**判据**(`COM=None`)   → 枚举筛 + 逐个探活 → **唯一**命中才返回
        ③ **没装装机卡带**           → 抛(与 2026-09-10 起的口径一致: 不静默退回某个字面量)

    ⚠ 0 候选 / 多候选 / 全探活失败 **一律抛**, 并把候选与每个候选的结果打出来 ——
      **绝不"取第一个"** (那会把"确定错"换成"看着一样错")。
    ⚠ 结果**进程内缓存**: 一轮跑里开两次口不该探两次(台面是同一块)。换了线请重开进程。
    """
    from common import machspec
    if _COM_CACHE:
        return _COM_CACHE[0]
    try:
        card = machspec.current()
    except RuntimeError as exc:
        raise RuntimeError(
            "说不了本机该用哪个串口: 没有装机卡带。\n"
            "  ① 装卡带(见 machine/); ② 或者显式指定: open_com(\"COM3\")。\n(原错误: %s)" % exc)

    pinned = getattr(card, "COM", None)
    if pinned:
        print("[串口] 卡带钉死 %s(跳过自动识别)" % pinned)
        _COM_CACHE.append(pinned)
        return pinned

    match = getattr(card, "COM_MATCH", None) or {}
    hits = candidates(match)
    if not hits:
        msg = ("自动识别串口失败: 没有口满足判据 [%s]。\n"
               "  ① USB-485 桥插好没有; ② 驱动装了没有(设备管理器里有没有 CP210x); "
               "③ 或把 `machine/<机器名>.py` 的 `COM` 钉死。" % describe(match))
        faultlog.record("SERIAL-PORT", subsystem="serial", text=msg,
                   snapshot={"判据": describe(match), "候选数": 0},
                   next_step="查 USB-485 桥与驱动; 或把卡带的 COM 钉死")
        raise RuntimeError(msg)

    if not probe:
        if len(hits) == 1:
            print("[串口] 判据 [%s] 命中 1 个候选 → %s(**未握手**)" % (describe(match), hits[0]["device"]))
            _COM_CACHE.append(hits[0]["device"])
            return hits[0]["device"]
        raise RuntimeError(
            "判据 [%s] 命中 %d 个候选(%s), 又关掉了握手(probe=False) —— 不替你猜。"
            % (describe(match), len(hits), " ".join(h["device"] for h in hits)))

    probe_fn = _require_probe()
    print("[串口] 判据 [%s] 命中 %d 个候选, 逐个证明『口后面是本表』…"
          % (describe(match), len(hits)))
    why_by_dev = {}                    # 口 → 探活那句说明。**按口名对上**, 不靠两个列表的下标
    passed = []
    for h in hits:
        ok, why = _probe_port(h["device"], probe_fn)
        print("   · %-6s %s" % (h["device"], why))
        why_by_dev[h["device"]] = why
        if ok:
            passed.append(h["device"])
    if len(passed) == 1:
        print("[串口] 用它: %s" % passed[0])
        _COM_CACHE.append(passed[0])
        # 凭据登记在**判定出口**: 唯一命中且表答了, 才有一份"口后面是本表"的实测凭据。
        _record_proof(passed[0], True, why_by_dev[passed[0]])
        return passed[0]
    if not passed:
        msg = ("自动识别串口失败: 判据命中 %s, 但**没有一个**应答本表读钟。\n"
               "  ① 表通电没有 / 485 的 A/B 接反了也不应答; ② 或把卡带的 `COM` 钉死绕过识别。"
               % " ".join(h["device"] for h in hits))
        # 逐口那句"为什么没应"原先只打在屏上、过目即忘 —— 而"某个口应了、另一个没应"与
        # "全都没应"是两件事, 后者才指向表/线, 前者指向选错了口。收进 tried 就是为了下次不必重跑一遍。
        faultlog.record("SERIAL-PORT", subsystem="serial", text=msg,
                   tried=["%s: %s" % (d, w) for d, w in why_by_dev.items()],
                   snapshot={"判据": describe(match), "候选": [h["device"] for h in hits]},
                   next_step="查表通电与 485 的 A/B; 或把卡带的 COM 钉死绕过识别")
        raise RuntimeError(msg)
    msg = ("自动识别串口失败: %s **都**应答了本表读钟(台上不止一块同地址的表?) —— 不替你猜。\n"
           "  → 把 `machine/<机器名>.py` 的 `COM` 钉死到你要的那一个。" % " ".join(passed))
    faultlog.record("SERIAL-PORT", subsystem="serial", text=msg,
               tried=["%s: %s" % (d, w) for d, w in why_by_dev.items()],
               snapshot={"判据": describe(match), "都应答了": list(passed)},
               next_step="把卡带的 COM 钉死到你要的那一个")
    raise RuntimeError(msg)


def _pinned_verdict(device):
    """显式指定的口: 也探一次, 但**只报不拦** —— 人工逃生判定的意义就是"我说了算"。
    返回 (bool, 说明); 报告由 `open_com` 打印, 让人一眼看见"这个口后面到底有没有本表"。"""
    try:
        return _probe_port(device, _require_probe())
    except Exception as exc:                     # 极端情形(比如 RawCom 的构造也炸)
        return False, "握手跑不起来: %s" % exc


def open_com(port=None, baud=None, raw=False, probe=True):
    """EZ315 管理芯串口: 默认 9600 8E1(参数从装机卡带取, 见 `_serial_kw`)。
    pyserial 打开失败/SetCommState 卡死时自动回退 RawCom(ctypes 直连, 需端口已配 8E1)。

    `port=None`   ⇒ **自动识别**(见 `_resolve_com`): 按卡带判据筛 + 探活, 唯一命中才用。
    `port="COM3"` ⇒ 人工指定: 跳过筛选, 但**仍探活**(证明口后面是本表) —— 只报不拦, 除非 `probe=False`。
    `probe=False` ⇒ 连探活也不做(要一个"纯裸口"时用)。
    两者都失败(识别不出 / 打不开) 时**抛**, 并说清是哪一级出了什么问题。
    """
    import serial
    if port is None:
        port = _resolve_com(probe=probe)
        if not probe:
            _record_proof(port, False, "没做探活(probe=False)")
    elif probe:
        ok, why = _pinned_verdict(port)
        print("[串口] 显式指定 %s —— 握手: %s" % (port, why if ok else "**%s**" % why))
        _record_proof(port, ok, why)
    else:
        _record_proof(port, False, "没做探活(probe=False)")
    if raw and os.name == "nt":
        return RawCom(port)
    try:
        return serial.Serial(port, **_serial_kw(baud))
    except Exception:
        if os.name == "nt":
            return RawCom(port)
        raise


# ============================ 离线自检(不碰真串口) ============================
class _P(object):
    """假 ListPortInfo —— 自检只喂它, 一条真串口都不枚举。"""

    def __init__(self, device, vid=None, pid=None, serial_number=None, description=""):
        self.device, self.vid, self.pid = device, vid, pid
        self.serial_number, self.description = serial_number, description
        self.hwid = "USB VID:PID=%04X:%04X" % ((vid or 0), (pid or 0))


def _selftest():
    from common.console import ensure_utf8_stdout
    ensure_utf8_stdout()
    CP = dict(vid=0x10C4, pid=0xEA60, description="Silicon Labs CP210x USB to UART Bridge")
    chk = []
    A = [_P("COM3", serial_number="0001", **CP)]
    B = [_P("COM3", serial_number="0001", **CP), _P("COM1", serial_number="0002", **CP)]
    X = [_P("COM1", vid=0x0403, pid=0x6001, description="FTDI FT232R")]

    chk.append(([c["device"] for c in candidates({"vid": 0x10C4, "pid": 0xEA60}, A)] == ["COM3"],
                "VID:PID 命中"))
    chk.append((candidates({"vid": 0x10C4, "pid": 0xEA60}, B) != [] and
                [c["device"] for c in candidates({"vid": 0x0403}, B)] == [],
                "别的桥(FTDI)不被误收"))
    chk.append(([c["device"] for c in candidates({"serial": "0002"}, B)] == ["COM1"],
                "serial 钉死 → 唯一"))
    chk.append(([c["device"] for c in candidates({"desc": "cp210x"}, B)] == ["COM1", "COM3"],
                "desc 子串命中且**大小写无关**(结果按口名排序)"))
    chk.append((candidates({"vid": 0x10C4}, X) == [], "候选为空 = 合法(不抛)"))

    def _raises(fn):
        try:
            fn()
            return None
        except PortselError as e:
            return str(e)

    e = _raises(lambda: pick({"vid": 0x10C4}, B))
    chk.append((e is not None and "COM1" in e and "COM3" in e, "多候选 pick → 抛且列出(**不取第一个**)"))
    e = _raises(lambda: pick({"vid": 0x10C4}, X))
    chk.append((e is not None and "没有串口满足" in e, "0 候选 pick → 抛, 不静默返回"))
    e = _raises(lambda: pick({"VID": 0x10C4}, A))
    chk.append((e is not None and "不认识的键" in e, "拼错的键 → 抛(不许静默不筛)"))
    chk.append((pick({"serial": "0001"}, A) == "COM3", "单候选 pick → 就它"))

    # 传输半边: tx_recv 的鸭子类型契约(替身只需五个动作), 且 quiet 只关 stdout、不关事件流
    # ⚠ 2026-09-20 改: 原先钉的是**前五个位置参数的名字**(`co_varnames[:5]`), 于是日志字形那次
    #   给 tx_recv 加 `peer`/`what` 两个参数时, 这个自检红了一条 —— 红得**没有道理**: 它想证的
    #   "quiet 是一份实现的一个开关"与'它排在第几位'无关。改判它**在不在参数表里、默认是不是 False**
    #   (默认 False = 照常打印, 这是"quiet 只关给人看的那一路"的落点), 并把该有的几个参数一并点名。
    _args = tx_recv.__code__.co_varnames[:tx_recv.__code__.co_argcount]
    chk.append((set(("ser", "frame", "wait", "tag", "peer", "what", "quiet")) <= set(_args)
                and tx_recv.__defaults__[-1] is False,
                "tx_recv 收 quiet=(轮询免刷屏, 一份实现一个开关)"))
    chk.append((callable(set_probe) and callable(probe_installed), "探活装配点齐备"))
    # 「没装探针时 open_com 不许静默放过」: 只判"那句话说没说清楚", **不真去装** ——
    # 自检不许改全局状态(装了别的探针会把后来 p698 的装配顶成"分叉")。
    chk.append(("没有探活函数" in str(_probe_missing_msg()), "没装探针 → 有话说(**不许静默放过**)"))

    ok = all(c for c, _ in chk)
    for c, name in chk:
        print("  [%s] %s" % ("PASS" if c else "FAIL", name))
    print("common.portsel 自检: %s (%d/%d)" % ("OK" if ok else "FAIL",
                                             sum(1 for c, _ in chk if c), len(chk)))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
