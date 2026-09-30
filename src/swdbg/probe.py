# -*- coding: utf-8 -*-
"""
swdbg/probe.py —— 管理芯 SWD 直读原语(**门面**: 自动认出该用哪支探针, 再照读)

这是 swdbg 的【最底层】: 只负责"连上探针、按绝对地址读出字节、干净退出"。
不含任何电表语义(不知道什么是 g_FrezNum, 也不知道 698) —— 变量名→地址的解析在中立层
swdbg.resolve; 与串口 AA80 白盒的对照在
scripts/_check_aa80_vs_swd.py(那是表的属性, 刻意不放在本包里)。

两端(2026-09-20 起)
-------------------
底下有两种探针、两种驱动, 对上层**完全透明**:
    swdbg/probe_jlink.py   J-Link PLUS   (pylink-square)
    swdbg/probe_cmsis.py   CMSIS-DAP 类  (DAPLink + pyOCD)
"该用哪一支"由 `swdbg/probesel.py` 判(判据→候选→逐个证明→恰好一支), 本模块只是拿它的结论。
派生读法(`read_many` / `read_stable` / `halted` / `dhcsr`)只写这一份, 两端共用 ——
照抄两份必然漂移。

与 meterlib 的关系
------------------
【零依赖 meterlib】。本模块只吃 pylink / pyOCD + 本工程的探针参数。swdbg 是"第二条观察通路",
不是协议层的下游 —— 串口断了/AA80 被拒时它照样能读, 这正是它存在的理由。

⚠ 三条安全铁律(2026-09-10 用血的教训换来, 见 CLAUDE.md)
------------------------------------------------------------------
1. **显式给定速度**: 不给速度, pylink 会退化成 speed='auto' 去测 CPU 时钟(**往目标 RAM 下载
   自测代码**), pyOCD 会自己挑一个 —— 两者都不行, 速度是**这台机器的事实**。两端一律显式传。
2. **绝不停核**: 纯读不需要 halt。AHB-AP 背景访问读内存不打断 CPU(实测: 连读 + 串口冒烟 = HEALTHY)。
   停核 = 表完全不响应串口。
   ⚠ CMSIS-DAP 端这条要**显式写出来**才算数: pyOCD 的 `connect_mode` **默认为 halt**,
     见 `probe_cmsis.py` 文件头铁律 2(那里有 2026-09-20 的反证实测数据)。
3. **绝不强杀**: SIGTERM/SIGKILL 掉 JLinkGDBServerCL 会把核心**撂在 halt 不放手**。
   本层只用 close() 干净退出, 且 close 幂等、异常路径也保证执行(try/finally + atexit 双保险)。
   救被撂住的核心用 swdbg/restore.py。

本层没有独立入口: 它只提供 `Probe` 这个会话对象, 真去连表读一次是
`scripts/_check_aa80_vs_swd.py` 与 `project/tests/` 里那些脚本的事。
⚠ **本层没有离线/模拟通路, 也没有自检入口** —— 读不到就是读不到, 结论只能来自实物
(真探针、真表)。探针在不在 / 核心停没停, 走 `python -m swdbg.probesel --doctor`(双端)、
`python -m swdbg.jlink --doctor` 与 `python -m swdbg.restore --check`。
"""
from __future__ import print_function

import atexit


# ---- 机器条件(= **这台机器**的事实) ----
# **单一事实源在 `machine/` 装机卡带**(2026-09-11 收敛, 见 machine/env_check.py 那道"装包即验")。
# 原先本文件与 breakpoint.py / restore.py 各抄一份, 换一台电脑要满仓找。
# 方向: swdbg → common.machspec(卡带的名字只是槽里的字符串默认值), **不是** swdbg → machine。
# ⚠ 模块级读一次, 给下面 `Probe()` 的默认参数用。没装卡带时是 None —— `import` 照旧能
#   (只是不报错), 真去连表时 `open()` 会先过一遍 `machspec.ready(...)` 并点名缺了哪几项。
from common import loglabel   # 字形(含调试行 `[调试] [SWD] …`)的唯一定义处
from common import machspec
from swdbg import jlink      # 只为一个名字: `FROM_CARD` 哨兵(见下)
from swdbg import probe_cmsis  # 两端的 backend 名与异常类; 只取名字, 连接仍经 probesel
from swdbg import probe_jlink
from swdbg import probesel   # 「该用哪一支探针」的唯一定义处

# ⚠ `SN` 是卡带**声明**的那一支 J-Link(None = 自动), **不是**最终要连的那一支 —— 真连表时由
#   `swdbg/probesel.py` 枚举解析(换支探针时"连不上"与"表挂了"长得一样, 不许静默猜)。
#   保留这个名字只为对外兼容(`swdbg/__init__.py` 的懒加载表里有它)。
SN = machspec.get("JLINK_SN")     # J-Link 声明的序列号(None = 自动, 恰好一支才认)
DEVICE = machspec.get("DEVICE")   # 泛型即可(FM33A0610 = Cortex-M0 r0p0 / armv6s-m; IAR 的 .jlink 也写这个)
IFACE = machspec.get("IFACE")
SPEED = machspec.get("SPEED")     # kHz, 必须显式, 见文件头铁律 1

# ---- Cortex-M0 调试寄存器(只读查状态用) ----
DHCSR = 0xE000EDF0        # bit1 C_HALT / bit17 S_HALT
RAM_BASE = 0x20000000     # FM33A0610 SRAM 基址(与画像一致)

__all__ = ["Probe", "SN", "DEVICE", "IFACE", "SPEED", "DHCSR", "RAM_BASE"]


class ProbeError(RuntimeError):
    """连不上/读不了时抛这个, 带人话解释。

    两端驱动抛的是各自的异常(`JLinkProbeError` / `CmsisDapError`), 门面统一翻成本类型 ——
    上层的容错(`read_many` 单块失败记 None、`restore` 的复核)只认这一个名字。
    """


# 会被翻成 ProbeError 的两端异常。**做成动态取**, 免得在此处写死 import 顺序。
def _backend_errors():
    out = [probesel.ProbeSelectError]
    for mod in (probe_jlink, probe_cmsis):
        for name in ("JLinkProbeError", "CmsisDapError"):
            exc = getattr(mod, name, None)
            if exc is not None:
                out.append(exc)
    return tuple(out)


class Probe(object):
    """一次 SWD 会话(**探针是哪一支由卡带判据自动认出**)。上下文管理器;
    进程崩了也由 atexit 兜底放行核心。

    用法:
        with Probe() as pb:
            raw = pb.read_abs(0x200090B2, 2)
            got = pb.read_many([("g_FrezNum", 0x200090B2, 2)])
        # 出 with 即已断开, 核心保持运行

    只读方向有意做全: 本层**不提供任何写内存/写寄存器/复位**接口。
    调试控制(下断点/go/复位)不属于白盒观测通路, 若将来要, 另开 at_breakpoint 层并单独评审。
    """

    def __init__(self, serial_no=jlink.FROM_CARD, device=DEVICE, speed=SPEED, iface=IFACE,
                 probe=None):
        self.serial_no = serial_no
        # `probe=(backend, ident)` —— **已经选好了**的那一支(由 `probesel.pick()` 得出的结论)。
        # 给它是为了"别重挑一遍": 挑选时要**真开一次会话读 CPUID**, 而 gdb 那条路
        # (`breakpoint.Session`) 每做一次收尾复核都要读一次 DHCSR —— 每次都重挑等于每次都多开一次
        # 会话, 且可能与刚放下的 server 抢探针。默认 None = 照常自己判。
        self.probe = probe
        self.device = device
        self.speed = speed
        self.iface = iface
        self.backend = None            # 连上之后才知道("jlink" / "cmsis-dap")
        self._drv = None
        self.close_ok = None
        self.close_error = None
        # ---- 强制留痕(2026-09-15) ----
        # 与 `breakpoint.Session` 同一个目的、同一个做法: **不要到处补 print, 钉在卡口上**。
        # 本层的卡口只有一个 —— `read_abs()`, 它是**唯一的读原语**: `read_many`/`read_stable`/
        # `halted`/`dhcsr` 全从它出去。于是"SWD 这一路读了哪个地址、读回来什么"必定留痕。
        # 连上/断开各留一行 —— 换探针、连错表这类问题, 病因就在这一头一尾。
        # (与 breakpoint 不同的是: 这里**没有**开关, SWD 通路的输出本来就少, 筛它没有意义。)
        self.trace = True

    def _trace(self, text):
        # ⚠ 字形**不在这里拼** —— 走 `loglabel.debug_line`(2026-09-18 并: 原先这里手写 `[SWD]`,
        #   是"调试侧留痕"这个维度上第三条各拼各的通路, 另两条在 `breakpoint._trace_emit`/`_warn`)。
        if getattr(self, "trace", False):
            print("   %s" % loglabel.debug_line(loglabel.DEBUG_SWD, text))

    def _trace_sel(self, text):
        """「该用哪一支探针」那一路的留痕 —— 来源格是 `[探针]`, **不是** `[SWD]`。

        分工见 `loglabel` 里那一行: **读**归 SWD(连上之后读了哪个地址、读回来什么),
        **选**归探针(枚举到几支、哪一支读 CPUID 证明成功、最后选中谁)。合成一条的话,
        "探针连上了但读不到"与"探针压根没选出来"在日志里就长得一样了。
        """
        if getattr(self, "trace", False):
            print("   %s" % loglabel.debug_line(loglabel.DEBUG_PROBE, text))

    # ---------------------------- 生命周期 ----------------------------
    def _resolve(self):
        """卡带/调用方 → `(backend, ident, 逐条说明)`。

        四种入参, 与旧口径**一一对应**(双端之后"自动"的含义从"J-Link 恰好一支"扩成
        "两端合起来恰好一支"):
            `probe=(backend, ident)` ⇒ 用**已经证明过**的那一支, 不再挑(见 `__init__`)
            `serial_no` 显式给了序列号 ⇒ 钉死 J-Link 那一支(旧行为, 一字未改)
            `serial_no is None`       ⇒ 强制自动: 两端都找, 恰好一支才认
            其余(默认 `FROM_CARD`)    ⇒ 问卡带: 先看 `PROBE` 用哪一端, 再看该端的 `JLINK_SN`/`DAP_UID`
        """
        # 挑选阶段只枚举候选；唯一候选时不预先 attach。正式 driver.open() 才是本会话
        # 的唯一一次连接，避免“证明连接”与“实际连接”之间留下 J-Link 状态。
        if self.probe is not None:
            be, ident = self.probe
            return be, ident, ["用已选定的探针: %s 端 %s(本次不再重挑)" % (be, ident)]
        if self.serial_no is jlink.FROM_CARD:
            declared = None                       # None = 让 probesel 去问卡带的 `PROBE`
            return probesel.pick(declared=declared, speed=self.speed,
                            device=self.device, iface=self.iface, trace=self._trace_sel,
                            verify_target=False)
        if self.serial_no is None:
            return probesel.pick(declared=None, speed=self.speed,
                            device=self.device, iface=self.iface, trace=self._trace_sel,
                            verify_target=False)
        # 显式给了序列号: 只可能是 J-Link 那一种口径(旧调用方就是这么用的)。
        # 调用方显式传入的序列号必须优先于卡带配置；不能让 pick() 又回读
        # machine.JLINK_SN，造成“指定 A、实际连 B”。
        try:
            ident = jlink.resolve_sn(self.serial_no)
        except Exception as exc:
            raise ProbeError("解析调用方指定的 J-Link SN=%s 失败: %s"
                             % (self.serial_no, exc))
        return probe_jlink.BACKEND, ident, [
            "使用调用方显式指定的 J-Link SN=%s(不读取卡带 SN)" % ident]

    def open(self):
        """认出该用哪支探针 → 连上 → attach 目标。已在连接状态则直接返回 self(幂等)。

        **返回时本会话的"首笔 SRAM 事务"已经用掉**(见方法末尾那段预热读)—— 这是本层的
        一条不变式: 调用方拿到的第一次 `read_abs()` 就已经是可靠的读, 不必自己先试一次。
        """
        if self._drv is not None:
            return self
        try:
            backend, ident, report = self._resolve()
        except Exception as exc:
            raise ProbeError(str(exc))
        for line in report:
            self._trace_sel(line)
        # 机器条件齐不齐 —— 在连之前核(两端共用的那几项; 探针自己那一支已由 probesel 证明过)。
        machspec.ready(DEVICE=self.device, SPEED=self.speed, IFACE=self.iface)
        self.backend = backend
        drv = probesel.make_driver(backend, ident, speed=self.speed, device=self.device,
                              iface=self.iface, trace=self._trace)
        try:
            drv.open()
        except Exception as exc:
            raise ProbeError("连探针失败(%s 端, %s): %s" % (backend, ident, exc))
        self._drv = drv

        # ★ 预热读 —— **本会话对 SRAM 区的第一笔 AHB-AP 事务不可靠**(2026-09-16 实测, 在
        #   J-Link 那条路上)。症状: 连上后第一次读 `RAM_BASE` 偶尔回一个**按位取反**的值
        #   (0xA5A5A5A5 → 0x5A5A5A5A), 而同一次会话的后续读、以及紧邻的 `RAM_BASE+4` 全部
        #   正常 ⇒ 不是 RAM 在变、也不是这个地址有什么特别, 是**首笔事务本身**。
        #   复现率约 1/4~1/5 场。后果不止"读错一个变量": `read_abs()` 是 SWD 通路的**唯一读
        #   原语**, 上层(`_check_aa80_vs_swd`)拿它当"真值"去和 AA80 对拍, 一个只在
        #   首笔出现的取反值会被当成"两条通路不一致" —— 那是最难查的一类结论。
        #   所以在这里**先读一次丢掉**。⚠ 这条是在 J-Link 端量出来的; CMSIS-DAP 端**没有重新
        #   量过**(2026-09-20 记), 这里两端都做, 是**保守**而非已证 —— 它失败只留痕不中止,
        #   代价是每场会话多一笔读, 远小于"首笔取反被判成两路不一致"的代价。
        try:
            self.read_abs(RAM_BASE, 4)
            self._trace("↑ 上面这笔是**预热读**, 丢掉 —— 本会话对 SRAM 的第一笔事务不可靠")
        except Exception as exc:
            self._trace("预热读没做成(只留痕, 不中止): %s" % exc)
        return self

    def close(self):
        """干净断开。幂等; 绝不 kill 任何进程(见文件头铁律 3)。"""
        if self._drv is None:
            return self.close_ok is not False
        # 底层关闭失败时保留驱动对象，下一次 close()/atexit 还能重试；先丢掉它
        # 会让门面进入“看起来已关、实际仍占探针”的不可恢复状态。
        drv = self._drv
        self.close_ok, self.close_error = True, None
        try:
            ok = drv.close()
            if ok is False or getattr(drv, "close_ok", None) is False:
                self.close_ok = False
                self.close_error = getattr(drv, "close_error", None)
        except Exception as exc:
            self.close_ok, self.close_error = False, exc
        if not self.close_ok:
            self._trace("⚠ %s 端关闭失败，资源/核心状态未确认: %s"
                        % (self.backend or "?", self.close_error or "驱动返回失败"))
            return False
        self._drv = None
        self._trace("断开(%s 端已干净退出 ⇒ 核心放行)" % (self.backend or "?"))
        return True

    def __enter__(self):
        return self.open()

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False                   # 不吞异常

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    # ---------------------------- 读原语 ----------------------------
    def read_abs(self, addr, size):
        """读绝对地址 size 字节 → bytes。读失败抛 ProbeError(pylink 的读异常包一层人话)。"""
        if self._drv is None:
            raise ProbeError("会话未打开, 先 with Probe() as pb: 或 pb.open()")
        n = int(size)
        if n <= 0:
            raise ValueError("size 必须为正, 收到 %r" % (size,))
        try:
            out = self._drv.read_abs(int(addr), n)
        except ValueError:
            raise
        except Exception as exc:
            # ⚠ 失败也要留痕(在抛之前): "没读成"与"没读"在日志里必须分得开 ——
            #   只打成功的那些, 一份全失败的日志会与"这一段根本没跑"长得一模一样。
            self._trace("← 0x%08X [%dB] 读失败: %s" % (int(addr), n, exc))
            raise ProbeError("读 0x%08X [%dB] 失败: %s" % (int(addr), n, exc))
        if len(out) != n:              # 短读要当失败(驱动也会查, 这里再兜一道)
            self._trace("← 0x%08X [%dB] 短读, 实得 %dB" % (int(addr), n, len(out)))
            raise ProbeError("读 0x%08X 期望 %dB 实得 %dB(短读)"
                             % (int(addr), n, len(out)))
        # ★ 留痕的**唯一卡口**: 本层所有读(many/stable/halted/dhcsr)都从这儿出去, 所以
        #   "读了哪个地址、读回来什么"必定留痕 —— 见 `__init__` 里那段。
        self._trace("← 0x%08X [%dB] = %s" % (int(addr), n, out.hex(" ").upper()))
        return out

    def read_many(self, blocks):
        """blocks = [(name, 绝对addr, size)] → {name: bytes|None}。单块失败记 None 不中断其余
        (与 AA80 侧 aa80_ram_snapshots 的容错语义对齐: 读不到就是 None, 由上层判读)。"""
        out = {}
        for name, addr, size in blocks:
            try:
                out[name] = self.read_abs(addr, size)
            except ProbeError:
                out[name] = None
        return out

    def read_stable(self, addr, size, tries=3, gap=0.02):
        """连读到两次结果相同才认 → bytes。**表在跑, 多字节读不是原子的**, 计数字段跨读会撕裂
        (例: g_FrezLen 低字节已更新、高字节还没)。绝大多数要判读的变量值很稳, 连读两次即收敛;
        像 g_HisTime 这种一直走的, tries 次后返回最后一次, 由上层决定怎么用。"""
        import time
        last = None
        for i in range(max(1, int(tries))):
            cur = self.read_abs(addr, size)
            if last is not None and cur == last:
                return cur
            last = cur
            if i + 1 < tries:
                time.sleep(gap)
        return last

    def halted(self):
        """核心是否处于 halt(C_HALT 或 S_HALT)。halt 了表不会应答串口。"""
        v = int.from_bytes(self.read_abs(DHCSR, 4), "little")
        return bool(v & 0x2) or bool(v & 0x20000)

    def dhcsr(self):
        return int.from_bytes(self.read_abs(DHCSR, 4), "little")

    def describe(self):
        d = self.dhcsr()
        return "%s DHCSR=0x%08X 核心%s" % (self.backend or "?", d,
                                          "HALT(表不应答串口!)" if self.halted() else "运行中")
