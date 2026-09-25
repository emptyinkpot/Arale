# -*- coding: utf-8 -*-
"""
swdbg/probe_cmsis.py —— 管理芯 SWD 直读原语, **CMSIS-DAP 端**(DAPLink + pyOCD)

与 `swdbg/probe_jlink.py` 是同一个身份的两个实现: 都只做「连上探针 / 按绝对地址读出字节 /
干净退出」三件事, 派生读法(`read_many` / `read_stable` / `halted` / `dhcsr`)一律在门面
`swdbg/probe.py` 里, **两边共用一份** —— 照抄两份必然漂移。

为什么是 pyOCD, 不是 OpenOCD
-----------------------------
管理芯(见下)的 CPUID partno 不在 OpenOCD 的 `cortex_m_parts[]` 表里, examine 时
`LOG_TARGET_ERROR` + `return ERROR_FAIL`, **没有跳过或强制的开关** —— target 建不起来,
`mdw` 与 gdb 断点全废。pyOCD 不按 partno 查表定架构(架构只看 CPUID 的 architecture 字段),
它自己的 `core_ids.py` 里也写着 `ARM_SC000 = 0xC30`。这是实测选型, 不是偏好。

这颗核是谁
----------
`CPUID = 0x410CC300` —— partno `0xC30` 是 ARM 给 **SecurCore SC000** 的号(不是 Cortex-M0)。
ROM 表里 DWT/BPU 报 Cortex-M0 是因为 SC000 复用了 M0 的组件 ID。SWD 与 ARMv6-M 指令集一致,
所以 J-Link 填泛型 `-device Cortex-M0` 一直能用。

⚠ 三条安全铁律(与 J-Link 端同源, 但这里的做法不同 —— 不许照搬 J-Link 的写法)
------------------------------------------------------------------------------
1. **显式给定速度**: 不给速度 pyOCD 会自己挑, 而速度是**这台机器的事实**, 必须由卡带说了算。
   本层一律显式传 `frequency`(卡带 `SPEED` 是 kHz, pyOCD 要 Hz, 乘 1000)。
2. **绝不停核** —— ⚠⚠ 这是 DAP 端**唯一一条必须显式写出来的**:
   **pyOCD 的 `connect_mode` 默认是 `halt`** (见 `pyocd/core/options.py`), 不显式改成
   `attach`, 一开会话就把表停住。2026-09-20 做过反证: 默认值下 DHCSR 读出
   `0x01030003`(S_HALT=1), 显式 `attach` 下是 `0x01000001`(S_HALT=0, S_RETIRE_ST=1)。
   停核 = 表完全不响应串口。**这不是洁癖, 是这条通路成不成立的前提。**
3. **绝不强杀**: 本层只用 `Session.close()` 干净退出(关会话即放行核心, pyOCD 的
   `resume_on_disconnect` 默认为真)。**不 kill 任何进程, 也不 kill 自己起的 gdbserver** ——
   收尾走「让 gdb 断开、它自己退」这一条路(实测退出码 0, 不留残进程)。

与 meterlib 的关系: 同 J-Link 端 —— 【零依赖 meterlib】。
⚠ 本模块与 `probe_jlink.py` 一样**没有离线/模拟通路**: 读不到就是读不到。
"""
from __future__ import print_function

import atexit

from common.probe_guard import acquire

# ---- 路径引导(全仓统一, 与 probe.py / jlink.py 同款) ----


__all__ = ["CmsisDapError", "BACKEND", "PROBE_CPUID", "PROBE_CPUID_ADDR",
           "list_probes", "diagnose_brief", "CmsisDapDriver"]

# 这一端的稳定标识 —— 写进卡带 `PROBE` 字段、写进日志、写在错误信息里, 三处必须是同一个字。
BACKEND = "cmsis-dap"

# 目标核身份的**判据**(实测真值, 不是照手册填的): 读 `0xE000ED00` 必须回这个数,
# 才算"探针后面接的是本表那颗核"。见模块头「这颗核是谁」。
PROBE_CPUID_ADDR = 0xE000ED00
PROBE_CPUID = 0x410CC300


class CmsisDapError(RuntimeError):
    """枚举不到 / 连不上 / 读不了时抛这个, 带人话解释。"""


def _load_pyocd():
    try:
        from pyocd.core.helpers import ConnectHelper
        return ConnectHelper
    except Exception as exc:                      # pragma: no cover - 环境问题
        raise CmsisDapError(
            "没装 pyocd。装法:\n"
            "    pip install pyocd -i https://pypi.tuna.tsinghua.edu.cn/simple\n"
            "(原错误: %s)" % exc)


def _quiet_pyocd():
    """把 pyOCD 自己的日志压到 ERROR。

    理由: 本仓的日志有固定的字形与筛选规则(`common/loglabel`), pyOCD 的 INFO/WARN 行
    (例如 `Board ID DC1C is not recognized`)是**它自己格式的**, 混进来会污染逐行校验。
    "探针干了什么"由本层与门面的 `[SWD]` 留痕负责 —— 那是给人读的, 格式是统一的。
    ⚠ 只压级别, **不吞异常**: 真出错仍然抛, 由本层包成人话。
    """
    import logging
    for name in ("pyocd", "pyocd.core", "pyocd.probe", "pyocd.target", "pyocd.gdbserver"):
        logging.getLogger(name).setLevel(logging.ERROR)


_LAST_SKIPPED = []          # 上一次 list_probes() 滤掉的非 CMSIS-DAP 探针(见 skipped_last)


def _is_cmsis_dap(p):
    """这一支 pyOCD 探针是不是**真的** CMSIS-DAP。

    ⚠ 必须滤, 不许直接照单全收: `get_all_connected_probes()` 给的是
      **pyOCD 会用的全部探针**, 其中还包括它**原生支持**的 J-Link
      (`pyocd/probe/jlink_probe.py`)、ST-Link、TCP 探针。
      不过滤的后果(2026-09-20 实踩, 台上只有一支 J-Link, 卡带 `PROBE=None`):
        那一支 J-Link **同时**出现在 `jlink 端`与`cmsis-dap 端`两份候选里,
        `probesel.pick()` 看到"2 支探针都证明了自己后面是本表"而**当场抛** ——
        即"插着一支 J-Link 却用不了"。这正是重构前不会发生的事。
      判据用**类型**而不是型号字符串: 型号名是给眼睛看的, 类型才决定谁在跟谁说话。
    """
    try:
        from pyocd.probe.cmsis_dap_probe import CMSISDAPProbe
        return isinstance(p, CMSISDAPProbe)
    except Exception:                             # pragma: no cover - 环境问题
        # 拿不到类时退回按类名判 —— 宁可少滤, 也别把真的 CMSIS-DAP 漏掉
        return "cmsis" in type(p).__name__.lower()


def list_probes():
    """枚举**当前连着**的 CMSIS-DAP 探针 → `[{"uid":…, "desc":…, "product":…}, …]`。

    不开会话、不碰目标。枚举不到 = `[]`(「没插探针」是合法状态, 不是错误); 枚举**本身失败**才抛。

    ⚠ 它会**在进程里常驻一个 pylink 实例**(`JLinkProbe._get_jlink` 建了它就不关), 所以严格说
      不是"纯 USB 枚举"。但它**不打开具体哪支探针**(只到 `connected_emulators()` 那一步),
      抢探针谈不上 —— 2026-09-21 查过 pyOCD 源码。调用方(`probesel.candidates`)在 jlink 端
      已经枚举到探针时不再问它, 图的是那一次枚举对本表没有信息量, 不是图它抢探针。

    ⚠ 用 `get_all_connected_probes` 而**不是** `ConnectHelper.choose_probe` ——
      后者在不止一支时是**交互式**的(会等人敲键盘), 在这种被脚本调用的地方等于挂死。
    ⚠ 拿回来必须过 `_is_cmsis_dap` 滤一道 —— 那个函数会把 J-Link 一起报回来, 理由见它。
    """
    # pyOCD 的枚举也会加载 J-Link backend；与 J-Link 共用同一物理资源锁。
    with acquire("cmsis-enumeration"):
        ConnectHelper = _load_pyocd()
        _quiet_pyocd()
        try:
            probes = ConnectHelper.get_all_connected_probes(blocking=False)
        except Exception as exc:
            raise CmsisDapError(
                "CMSIS-DAP 枚举失败(pyOCD 装了没 / libusb 能不能用 / 驱动在不在)。\n"
                "手查: python -c \"from pyocd.core.helpers import ConnectHelper as C;"
                "print(C.get_all_connected_probes(False))\"\n"
                "(原错误: %s)" % exc)
    out, skipped = [], []
    for p in probes:
        if not _is_cmsis_dap(p):
            # 不是 CMSIS-DAP 的(典型: 一支 J-Link —— pyOCD 自己也认它)记下来, 由调用方
            # 决定要不要说。**不许静默丢**: "枚举到 0 支"与"枚举到 1 支但不是这一端"
            # 是两件事, 处置完全不同(前者要插探针, 后者是选错了端)。
            skipped.append(str(getattr(p, "description", "") or
                               getattr(p, "product_name", "") or type(p).__name__))
            continue
        out.append({"uid": str(p.unique_id),
                    "desc": str(getattr(p, "description", "") or ""),
                    "product": str(getattr(p, "product_name", "") or "")})
    _LAST_SKIPPED[:] = skipped
    return out


def skipped_last():
    """上一次 `list_probes()` **滤掉**的非 CMSIS-DAP 探针的说明串。

    为什么要留着它: "枚举到 0 支"与"枚举到 1 支但不是这一端"是**两件事** ——
    后者说明**选错了端**(台上插着一支 J-Link, 却去问 CMSIS-DAP 那一端), 处置完全不同:
    前者要你去插探针, 后者要你改卡带的 `PROBE`。滤的时候记下来, 由
    `probesel.candidates()` 取走写进它的过程记账, **不许静默丢**。
    """
    return list(_LAST_SKIPPED)


def _fmt(probes):
    """候选清单 → 一行给人看的串。空表给『(无)』(与 jlink._fmt 同款)。"""
    if not probes:
        return "(无)"
    return " ".join("%s(%s)" % (p["uid"], p["desc"] or p["product"]) for p in probes)


def diagnose_brief(connect_error=None, probes=None):
    """**便宜版**诊断: 只做枚举那层。给出错现场用(与 `jlink.diagnose_brief` 对称)。

    答的是第一问: 「探针还在 USB 上吗」—— 这一问就能把"表挂了"这类猜测砍掉一大半。
    反面同样要紧: **枚举得到而连不上, 卡的是表**(跑着的固件压住调试口), 见下面那句。
    """
    try:
        if probes is None:
            probes = list_probes()
    except CmsisDapError as exc:
        return "探针诊断: CMSIS-DAP 枚举本身跑不了 ⇒ %s" % exc
    if probes and connect_error:
        return ("探针诊断: 枚举到 %d 支探针(%s), USB 这头是活的 ⇒ 卡的是**表**, 不是这一端的线。"
                "①先试**复位下连接** —— 表上跑着的固件会压住调试口, 只有连着复位才应答"
                "(⚠ 它会给表复位一次); "
                "②再查接线: SWDIO / SWCLK 有没有压实(杜邦线搭着不算); "
                "GND 有没有接 —— 地虚接的典型现象就是「读得到但读回来全是 1」; "
                "VTref 有没有接(探针靠它判目标电平)。"
                "⚠ USB 设备节点禁用+启用(重新枚举)治不了这条, 2026-09-20 连做两次都没治。"
                % (len(probes), _fmt(probes)))
    if not probes:
        return ("探针诊断: 一支 CMSIS-DAP 都没枚举到 ⇒ 先别怀疑表和线, "
                "查探针在不在 USB 上(灯亮不亮)。")
    return ""


class CmsisDapDriver(object):
    """一次 CMSIS-DAP(pyOCD)SWD 会话 —— **只有开/关/读**。

    调用方是门面 `swdbg/probe.py`(它负责留痕、派生读法、atexit 兜底)。
    本类不 import 门面, 免得绕成环。
    """

    def __init__(self, unique_id=None, speed=None, trace=None):
        self.unique_id = unique_id      # None = 交给 probesel 解析; 具体值 = 钉死这一支
        self.speed = speed              # kHz(卡带口径), 用之前乘 1000 成 Hz
        self._session = None
        self._lease = None
        self._hook = None
        self.close_ok = None
        self.close_error = None
        self._trace = trace or (lambda text: None)

    # ---------------------------- 生命周期 ----------------------------
    def _release_lease(self):
        lease, self._lease = self._lease, None
        if lease is not None:
            lease.release()

    def open(self, unique_id=None):
        """连上探针并 attach 目标(**不停核** —— 见文件头铁律 2)。幂等。"""
        if self._session is not None:
            return self
        uid = unique_id if unique_id is not None else self.unique_id
        if self.speed is None:
            raise CmsisDapError(
                "没给速度。本层**必须显式给速度**(铁律 1)—— 卡带的 `SPEED` 是 kHz, "
                "这里要 Hz。")
        hz = int(self.speed) * 1000
        self._lease = acquire("cmsis-session")
        try:
            ConnectHelper = _load_pyocd()
        except Exception:
            self._release_lease()
            raise
        _quiet_pyocd()

        opts = {
            # ★ 铁律 2: pyOCD 的默认值是 halt, **不写这一行就会把表停住**。见文件头。
            "connect_mode": "attach",
            # 断开时放行核心。这正是 pyOCD 的默认值, 写出来是为了它一旦改了能被看见。
            "resume_on_disconnect": True,
        }
        try:
            session = ConnectHelper.session_with_chosen_probe(
                blocking=False,
                unique_id=uid,          # 钉死这一支; None 时由 probesel 保证"恰好一支"
                auto_open=False,        # 开这一步自己做, 好把失败包成人话
                target_override="cortex_m",
                frequency=hz,
                options=opts,
            )
        except Exception as exc:
            self._release_lease()
            raise CmsisDapError("开会话失败(uid=%s): %s\n%s"
                                % (uid, exc, diagnose_brief(exc)))
        if session is None:
            self._release_lease()
            raise CmsisDapError(
                "没选中任何探针(uid=%s)。%s" % (uid, diagnose_brief(None)))
        try:
            session.open()
        except Exception as exc:
            try:
                session.close()
            except Exception:
                self._session = session
                self.close_ok, self.close_error = False, exc
                self._hook = lambda: self.close()
                atexit.register(self._hook)
                raise CmsisDapError(
                    "CMSIS-DAP 会话打开失败且关闭未确认，已保留占用以便重试: %s" % exc)
            self._release_lease()
            raise CmsisDapError(
                "连不上目标(uid=%s, %d kHz)。\n%s\n(原错误: %s)"
                % (uid, self.speed, diagnose_brief(exc), exc))
        self._session = session
        self.unique_id = uid
        # ★ 铁律 3 的兜底: 就算调用方忘了 with / 抛了未捕获异常, 进程退出时也把会话关干净。
        self._hook = lambda: self.close()
        atexit.register(self._hook)
        self._trace("连上 CMSIS-DAP uid=%s / %d kHz / connect_mode=attach(核心未停)"
                    % (uid, self.speed))
        return self

    def close(self):
        """干净断开 —— **关会话即放行核心**, 绝不 kill 任何进程(铁律 3)。幂等。"""
        if self._session is None:
            self._release_lease()
            return self.close_ok is not False
        # 关闭失败时保留会话和 atexit 兜底，允许调用方重试；不能把仍占着探针的
        # 会话先从对象里抹掉，再对外宣称已经放行核心。
        session = self._session
        self.close_ok, self.close_error = True, None
        try:
            session.close()            # pyOCD 关会话 ⇒ 目标放行(resume_on_disconnect)
        except Exception as exc:
            self.close_ok, self.close_error = False, exc
            self._trace("⚠ CMSIS-DAP 会话关闭失败，不能宣称核心已放行: %s" % exc)
            return False
        self._session = None
        self._release_lease()
        if self._hook is not None:
            try:
                atexit.unregister(self._hook)
            except Exception:
                pass
            self._hook = None
        self._trace("断开(会话已关 ⇒ 核心放行)")
        return True

    # ---------------------------- 读原语 ----------------------------
    def read_abs(self, addr, size):
        """读绝对地址 size 字节 → bytes。失败抛 CmsisDapError。

        ⚠ 这是 AHB-AP **背景访问**: 不停核(实测 S_HALT=0, 且读的同时串口冒烟 HEALTHY)。
        """
        if self._session is None:
            raise CmsisDapError("会话未打开, 先 with Probe() as pb: 或 pb.open()")
        n = int(size)
        if n <= 0:
            raise ValueError("size 必须为正, 收到 %r" % (size,))
        try:
            units = self._session.target.read_memory_block8(int(addr), n)
        except Exception as exc:
            raise CmsisDapError("读 0x%08X [%dB] 失败: %s" % (int(addr), n, exc))
        if len(units) != n:            # 短读要当失败(与 J-Link 端同一口径)
            raise CmsisDapError("读 0x%08X 期望 %dB 实得 %dB(短读)"
                                % (int(addr), n, len(units)))
        return bytes(bytearray(units))

    # ---------------------------- 身份证明 ----------------------------
    def cpu_id(self):
        """读 CPUID(`0xE000ED00`)。`probesel` 用它证明"探针后面就是本表那颗核"。"""
        return int.from_bytes(self.read_abs(PROBE_CPUID_ADDR, 4), "little")

    def describe_connection(self):
        """给日志/诊断用的一行 —— 这一端到底连在什么上。"""
        t = self._session.target
        return "pyOCD / %s / uid=%s" % (getattr(t, "part_number", "?"), self.unique_id)
