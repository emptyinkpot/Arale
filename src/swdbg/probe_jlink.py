# -*- coding: utf-8 -*-
"""
swdbg/probe_jlink.py —— 管理芯 SWD 直读原语, **J-Link 端**(J-Link PLUS + pylink-square)

与 `swdbg/probe_cmsis.py` 是同一个身份的两个实现: 都只做「连上探针 / 按绝对地址读出字节 /
干净退出」三件事, 派生读法(`read_many` / `read_stable` / `halted` / `dhcsr`)一律在门面
`swdbg/probe.py` 里, **两边共用一份**。

本模块的代码是从 `probe.py` **整段搬过来的**(2026-09-20 拆双后端), 行为一字未改 ——
三条铁律是在这条路上用血的教训换来的(见 CLAUDE.md), 搬家时不许顺手"优化"。

⚠ 三条安全铁律(原文照搬, 一条都不许松)
--------------------------------------
1. **显式给定速度**: pylink 的 connect() 默认 speed='auto', 会触发 J-Link 自动测 CPU 时钟
   → 那一步会**往目标 RAM 下载自测代码**(实测报 "Verification of test code downloaded into
   RAM failed")。本层一律 connect(..., speed=SPEED) 把它钉死。这是必须的, 不是洁癖。
2. **绝不停核**: 纯读不需要 halt。AHB-AP 背景访问读内存不打断 CPU(已做对照实验: 47 次并发读
   + 串口冒烟 = HEALTHY)。停核 = 表完全不响应串口。
3. **绝不强杀**: SIGTERM/SIGKILL 掉 JLinkGDBServerCL 会把核心**撂在 halt 不放手**。
   本层只用 close() 干净退出(J-Link 干净退出会自动放行核心), 且 close 幂等、异常路径也保证执行
   (try/finally + atexit 双保险)。救被撂住的核心用 swdbg/restore.py。

与 meterlib 的关系: 同 CMSIS-DAP 端 —— 【零依赖 meterlib】。
⚠ 本模块与 `probe_cmsis.py` 一样**没有离线/模拟通路**: 读不到就是读不到。
"""
from __future__ import print_function

import atexit


from common import faultlog  # 故障台账: 连不上目标那一笔要连 SN 与 VTref 一起落账
from common.probe_guard import acquire
from swdbg import jlink      # 探针解析(枚举+判据); 本模块不直接信卡带那个 SN 死值

__all__ = ["JLinkProbeError", "BACKEND", "FROM_CARD", "list_probes", "diagnose_brief",
           "JLinkDriver"]

# 这一端的稳定标识 —— 写进卡带 `PROBE` 字段、写进日志、写在错误信息里, 三处必须是同一个字。
BACKEND = "jlink"

# 哨兵: "没传 declared ⇒ 去问卡带"。语义与 `jlink.FROM_CARD` 完全同一件事, 这里只是转出去,
# 让门面 `probe.py` 不必同时 import 两个模块才拿得到它。
FROM_CARD = jlink.FROM_CARD


class JLinkProbeError(RuntimeError):
    """连不上/读不了时抛这个, 带人话解释。"""


def _load_pylink():
    try:
        import pylink
        return pylink
    except Exception as exc:                      # pragma: no cover - 环境问题
        raise JLinkProbeError(
            "没装 pylink-square。装法:\n"
            "    pip install pylink-square -i https://pypi.tuna.tsinghua.edu.cn/simple\n"
            "(原错误: %s)" % exc)


def list_probes():
    """枚举当前连着的 J-Link —— 转发 `jlink.list_probes`(那是这一端枚举的唯一定义处)。

    留这个转发是为了让两个后端**对门面/probesel 长得一样**: 调用方不必知道
    "J-Link 的枚举在 jlink.py、CMSIS-DAP 的在 probe_cmsis.py"。
    """
    return jlink.list_probes()


def diagnose_brief(connect_error=None, probes=None, vtarget=None):
    """便宜版诊断 —— 转发 `jlink.diagnose_brief`(同上, 只为两端形状一致)。

    `vtarget` 一并转过去: 它是分开"线的问题"与"目标不应答"的唯一一个读数, 转发时漏掉它,
    下游就只能把话头指向接线(见 `jlink.diagnose_brief`)。
    """
    return jlink.diagnose_brief(connect_error=connect_error, probes=probes, vtarget=vtarget)


class JLinkDriver(object):
    """一次 J-Link SWD 会话 —— **只有开/关/读**。

    调用方是门面 `swdbg/probe.py`(它负责留痕、派生读法、atexit 兜底)。
    """

    def __init__(self, serial_no=FROM_CARD, device=None, speed=None, iface=None, trace=None):
        self.serial_no = serial_no      # FROM_CARD = 问卡带; None = 强制自动; 具体值 = 钉死
        self.device = device
        self.speed = speed
        self.iface = iface
        self._jl = None
        self._lease = None
        self._hook = None
        self.close_ok = None
        self.close_error = None
        self._trace = trace or (lambda text: None)

    @staticmethod
    def _vtarget_mv(jl):
        """读目标参考电压 VTref(mV)。读不到返回 None。

        它是分开"线的问题"与"目标的问题"的**唯一一个读数**: 有电压说明排线里 VTref/GND 是通的,
        那连接失败就只能是目标那头不应答; 没电压才轮到查线。
        """
        try:
            return int(jl.hardware_status.VTarget)
        except Exception:
            return None

    def _connect_under_reset(self, pylink, jl):
        """退路: 按住复位连进去。返回成没成, 不抛。

        **这一档会给表复位** —— RAM 里的状态(写过的时钟、跑到一半的态)会丢。所以只允许在普通
        连接**已经失败之后**走一次, 且必须留痕(见 open())。

        为什么它能治: 2026-09-20 实测, 目标固件进了"不应答 SWD"的态时 —— 探针枚举正常、VTref
        3.33 V、10 / 1000 / 4000 kHz 三档全报 `Unspecified error` —— 禁用+启用 USB 设备节点
        (重新枚举)**连做两次都没用**, 只有这一档当场连上(SW-DP 0x0BB11477 / CPUID 0x410CC300);
        连上之后普通连接也随之恢复。同一现象在 SEGGER 自家 `JLink.exe` 上重现, 与本仓、与 pylink
        都无关, 所以这不是软件层能"修好"的东西, 只能在这里兜住。
        """
        try:
            jl.set_reset_strategy(
                pylink.enums.JLinkResetStrategyCortexM3.CONNECT_UNDER_RESET)
            jl.connect(self.device, speed=self.speed)
        except Exception:
            return False
        # 这一档连上后核可能停在复位向量上。铁律 2 是"绝不停核", 所以当场放行。
        try:
            if jl.halted():
                jl.restart()
                self._trace("复位下连接后核是停的 ⇒ 已放行(铁律 2)")
        except Exception:
            pass
        return True

    def _open_fresh(self, pylink):
        """打开一枚新的 J-Link 句柄并设置接口；失败时返回 None。

        这个辅助函数不接触旧句柄。这样旧句柄已经关闭、而新句柄又没打开时，
        调用方不会误把一个失效对象继续拿去做复位下连接。
        """
        new = None
        try:
            new = pylink.JLink()
            new.open(serial_no=self.serial_no)
            new.set_tif(getattr(pylink.enums.JLinkInterfaces, self.iface.upper()))
            return new
        except Exception:
            if new is not None:
                try:
                    new.close()
                except Exception:
                    pass
            return None

    def _reopen(self, pylink, jl):
        """把这个会话**扔掉, 换一支新开的**重试。返回新的 JLink; 打不开就返回 None。

        为什么第一刀是重开而不是换策略: 2026-09-21 实测 ——
          · 在一支**已经连失败**的会话上 `set_reset_strategy` 再 `connect`(本文件下面那条退路
            就是这么写的), 与在同一支上换速度一样, 一次都没进去;
          · 而**每试一次就 close 掉、新开一支**的扫法, 4000 / 1000 / 100 kHz 乘五种复位策略
            13 次全过, 第一次就进。
        连失败的会话带着脏状态, 换策略治不了它, 得把它扔掉重开。这一步**不给表复位**,
        所以它排在"复位下连接"前面。
        """
        try:
            jl.close()
        except Exception:
            self._trace("⚠ 连接失败后的旧 J-Link 句柄无法关闭；不再创建第二个句柄")
            self._reopen_close_failed = True
            return None
        return self._open_fresh(pylink)

    # ---------------------------- 生命周期 ----------------------------
    def _release_lease(self):
        lease, self._lease = self._lease, None
        if lease is not None:
            lease.release()

    def open(self):
        """连上 J-Link 并 attach 目标。已在连接状态则直接返回 self(幂等)。"""
        if self._jl is not None:
            return self
        # JLinkARM.dll 是进程外的共享资源；同一支探针只能有一个 owner。
        self._lease = acquire("jlink-session")
        # ⚠ 先卡住"三项没配"这一档(2026-09-20 踩到): 卡带读不到时 device/speed/iface 全是 None,
        #   一路走到下面的 `%d kHz` 会抛 `TypeError: %d format: a real number is required, not
        #   NoneType` —— 那句把人指向格式化, 与真正的病因(卡带没读上)毫无关系, 而它顶上来的
        #   位置正好是"连不上"的报告位。本仓最忌的就是报告功能自己变成新问题。
        missing = [n for n, v in (("device", self.device), ("speed", self.speed),
                                  ("iface", self.iface)) if v is None]
        if missing:
            raise JLinkProbeError(
                "驱动这三项没配齐: %s。它们来自 `common.machspec`(卡带), 而**卡带是按当前工作目录"
                "找的** —— 多半是没从仓库根目录跑。先在仓库根目录下试: python -c \"from common "
                "import machspec as M; print(M.get('DEVICE'), M.get('SPEED'), M.get('IFACE'))\""
                % ", ".join(missing))
        # 探针解析: 卡带声明哪一支就找哪一支(声明的那支不在 / 没声明又不止一支 → 当场抛)。
        # 不传 `serial_no` = 问卡带; 显式传 None = 强制自动(恰好一支才认)。
        try:
            self.serial_no = jlink.resolve_sn(self.serial_no)
            pylink = _load_pylink()
        except Exception:
            self._release_lease()
            raise
        try:
            jl = pylink.JLink()
        except Exception:
            self._release_lease()
            raise
        try:
            jl.open(serial_no=self.serial_no)
        except Exception as exc:
            # pylink 在 open() 部分成功后抛异常时也可能留下 DLL 句柄；close()
            # 对未打开对象是幂等的，统一调用可避免失败重试逐渐积累占用。
            try:
                jl.close()
            except Exception:
                # 关闭未确认时不释放 lease；这支句柄可能仍握着 DLL。
                self._jl = jl
                self.close_ok, self.close_error = False, exc
                self._hook = lambda: self.close()
                atexit.register(self._hook)
                raise JLinkProbeError(
                    "J-Link open 失败且句柄无法确认关闭，已保留占用以便重试: %s" % exc)
            self._release_lease()
            raise JLinkProbeError(
                "打不开 J-Link(SN %s)。常见原因: ①另一个进程占着它 —— 残留的\n"
                "JLinkGDBServerCL.exe / J-Link.exe / arm-none-eabi-gdb.exe 没退干净;\n"
                "②USB 没插好。若怀疑核心被撂在 halt, 先跑: python -m swdbg.restore\n"
                "%s\n(原错误: %s)" % (self.serial_no, diagnose_brief(exc), exc))
        connect_exc = None
        try:
            tif = getattr(pylink.enums.JLinkInterfaces, self.iface.upper())
            jl.set_tif(tif)
            # ★ 铁律 1: 显式速度。写成 connect(device) 会退化成 speed='auto' → 往 RAM 下自测代码。
            jl.connect(self.device, speed=self.speed)
        except Exception as exc:
            connect_exc = exc

        if connect_exc is not None:
            # ★ 第一刀是**换一支新开的会话**, 不是在这支上换策略 —— 实测见 `_reopen`。
            #   这一刀不给表复位, 所以排在"复位下连接"前面。
            first_exc = connect_exc
            self._reopen_close_failed = False
            fresh = self._reopen(pylink, jl)
            if fresh is not None:
                jl = fresh
                try:
                    jl.connect(self.device, speed=self.speed)
                    connect_exc = None
                    self._trace("⚠ 第一次连接失败(%s) —— **重开一支会话**后连上(未复位表)"
                                % (str(first_exc).strip().splitlines()[0][:120],))
                except Exception as exc2:
                    connect_exc = exc2
            else:
                if self._reopen_close_failed:
                    self._jl = jl
                    self.close_ok, self.close_error = False, first_exc
                    self._hook = lambda: self.close()
                    atexit.register(self._hook)
                    raise JLinkProbeError(
                        "第一次连接失败且旧句柄无法确认关闭；已停止重试以避免并发占用: %s" % first_exc)
                # _reopen 已经关闭了旧句柄；不能再把那个失效对象交给
                # VTref/CONNECT_UNDER_RESET。再独立申请一枚句柄，失败才算
                # 真正没有可用的重试会话。
                jl = self._open_fresh(pylink)
                if jl is not None:
                    try:
                        jl.connect(self.device, speed=self.speed)
                        connect_exc = None
                        self._trace("⚠ 第一次连接失败(%s) —— 第二枚新会话普通连接成功"
                                    % (str(first_exc).strip().splitlines()[0][:120],))
                    except Exception as exc3:
                        connect_exc = exc3

        if connect_exc is not None:
            # 退路只有一条: 复位下连接。判据、代价与来历都写在 `_connect_under_reset` 里。
            # VTref 是那一步的闸门 —— 读不到电压就说明轮不到它, 该去查线。
            vtarget = self._vtarget_mv(jl)
            if (vtarget is not None and vtarget >= jlink.VTREF_MIN_MV
                    and self._connect_under_reset(pylink, jl)):
                self._trace("⚠ 普通连接失败(%s) —— 退回**复位下连接**成功; "
                            "已给表复位一次(VTref=%d mV), 核已放行" % (connect_exc, vtarget))
            else:
                try:
                    jl.close()
                except Exception:
                    self._jl = jl
                    self.close_ok, self.close_error = False, connect_exc
                    self._hook = lambda: self.close()
                    atexit.register(self._hook)
                    raise JLinkProbeError(
                        "连接失败且 J-Link 句柄无法确认关闭，已保留占用以便重试: %s" % connect_exc)
                self._release_lease()
                # ⚠ 2026-09-20 修: 这里原先把 `self.serial_no`(一个 **int**)当**第二个位置参数**传了下去,
                #   而那个位置是 `probes`(探针清单)。于是 `jlink.diagnose_brief` 走到 `len(probes)`
                #   当场抛 `TypeError: object of type 'int' has no len()`, **把真正的错因整个顶掉** ——
                #   现场看到的是一句"连不上/读不到: object of type 'int' has no len()", 而它下面接着的
                #   却是"查 ①SWDIO/SWCLK 有没有压实 ②GND ③VTref"那段**接线**提示。
                #   后果: 2026-09-20 跑 4-6 时, 真错因(`jl.connect` 抛 `JLinkException: Unspecified error`)
                #   一个字都没露出来, 而人会被支去重接一根好线。本仓最忌的就是这类"报告一个问题"的功能
                #   自己变成新问题。`probes` 宁可不传: 让 `diagnose_brief` 自己现枚举(它本来就是这么设计的)。
                #   连 `vtarget` 一起报下去: 有电压就不该再提"查线", 那会把话头指向错的地方。
                msg = ("连接目标失败(device=%s, %s, %d kHz, VTref=%s mV): %s\n%s"
                       % (self.device, self.iface, self.speed,
                          "读不到" if vtarget is None else vtarget,
                          connect_exc,
                          diagnose_brief(connect_error=connect_exc, vtarget=vtarget)))
                # ★ 全仓唯一在同一处同时握着 **SN 与 VTref** 的地方 —— 这两个数正是"那一刻
                #   链路什么状态"要的证据: 有电压说明表和线都好、病灶在别处; 读不到才轮到查线。
                #   原话(`connect_exc`)整串进台账, `msg` 只切屏上那一行给人看(规矩见 faultlog)。
                faultlog.record("PROBE-LINK", subsystem="probe", exc=connect_exc,
                           text=msg,
                           tried=["普通 connect(@%s, %d kHz)" % (self.iface, self.speed),
                                  "复位下连接" if vtarget is not None
                                  and vtarget >= jlink.VTREF_MIN_MV else
                                  "复位下连接(未试: VTref 不够或读不到)"],
                           snapshot={"探针端": "jlink", "SN": self.serial_no,
                                     "device": self.device, "iface": self.iface,
                                     "speed_kHz": self.speed, "VTref_mV": vtarget},
                           next_step="python -m swdbg.jlink --doctor")
                raise JLinkProbeError(msg)
        self._jl = jl
        # ★ 铁律 3 的兜底: 就算调用方忘了 with / 抛了未捕获异常, 进程退出时也把会话关干净。
        self._hook = lambda: self.close()
        atexit.register(self._hook)
        self._trace("连上 J-Link SN %s / %s / %s / %s kHz(核心未停)" % (
            self.serial_no, self.device, self.iface, self.speed))
        return self

    def close(self):
        """干净断开。幂等; 绝不 kill 任何进程(铁律 3)。"""
        if self._jl is None:
            self._release_lease()
            return self.close_ok is not False
        # 只有底层真正关掉后才丢句柄/注销 atexit。先清空会把“关闭失败、探针仍被占用”
        # 伪装成干净状态，后续既无法重试也无法在进程退出时补救。
        jl = self._jl
        self.close_ok, self.close_error = True, None
        try:
            jl.close()                 # J-Link 干净退出 ⇒ 核心自动放行
        except Exception as exc:
            self.close_ok, self.close_error = False, exc
            self._trace("⚠ J-Link 关闭失败，不能宣称核心已放行: %s" % exc)
            return False
        self._jl = None
        self._release_lease()
        if self._hook is not None:
            try:
                atexit.unregister(self._hook)
            except Exception:
                pass
            self._hook = None
        self._trace("断开(J-Link 干净退出 ⇒ 核心放行)")
        return True

    # ---------------------------- 读原语 ----------------------------
    def read_abs(self, addr, size):
        """读绝对地址 size 字节 → bytes。读失败抛 JLinkProbeError(pylink 的读异常包一层人话)。"""
        if self._jl is None:
            raise JLinkProbeError("会话未打开, 先 with Probe() as pb: 或 pb.open()")
        n = int(size)
        if n <= 0:
            raise ValueError("size 必须为正, 收到 %r" % (size,))
        try:
            units = list(self._jl.memory_read(int(addr), n))
        except Exception as exc:
            raise JLinkProbeError("读 0x%08X [%dB] 失败: %s" % (int(addr), n, exc))
        if len(units) != n:            # ReadMemEx 返回实际读到的单元数, 短读要当失败
            raise JLinkProbeError("读 0x%08X 期望 %dB 实得 %dB(短读)"
                                  % (int(addr), n, len(units)))
        return bytes(bytearray(units))

    # ---------------------------- 身份证明 ----------------------------
    def cpu_id(self):
        """读 CPUID(`0xE000ED00`)。`probesel` 用它证明"探针后面就是本表那颗核"。"""
        return int.from_bytes(self.read_abs(0xE000ED00, 4), "little")

    def describe_connection(self):
        """给日志/诊断用的一行 —— 这一端到底连在什么上。"""
        return "pylink / %s / %s / SN=%s" % (self.device, self.iface, self.serial_no)
