#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""调试事故恢复：把被调试器停住的管理芯重新放开运行。

对外两个面
----------
* **库面** `release_debug(serial_no, clean_stray, check_only, probe) -> (ok, detail)`
  —— 给脚本 import 调用的入口(例: `帧收发基础/scripts/_restore_all.py` 的第一步)。
* **CLI 面** `python -m swdbg.restore [--check] [--keep-servers]`
  —— 人直接跑的薄壳, 打印 `[1/4]…[4/4]` 与退出码 0/1。

用武之地
--------
调试器(gdb / VS Code Cortex-Debug / GDB Server)异常退出时，管理芯会被
留在 halt 态——表现是**串口完全无应答**(645/698 帧发出去收不到回)。本模块把核心
重新放开，并顺手清掉残留的硬件断点。

安全
----
* 只做四件事：读 DHCSR 状态 → 清**调试单元**里残留的槽(FPB 断点单元 + DWT 数据观察点单元)
  → 放开运行 → 复核(核心在不在跑 **且** 那些槽是不是真空了)。
* **不改 RAM、不改 Flash、不复位**，表的数据(电量/表钟/事件库)不受影响。
* **本来就干净时一个字节都不写**(见 `release_debug` 里"核心运行中且单元全空"那条早退)。
* 连不上板子时只报错不改动，不会把好状态弄坏。

两端(2026-09-20 起)
--------------------
底下是 J-Link 还是 CMSIS-DAP 类探针, 对本模块同样透明 —— 该用哪一支由 `swdbg/probesel.py`
判(判据→候选→逐个证明→恰好一支)。**判据、早退、复核、话术全部共用一份**, 只有"怎么读、
怎么清、怎么放行"这三件动作按端分派(见 `_read_state` / `_clear_and_go`):

    J-Link 端    JLink.exe Commander 脚本(`mem32` 读 / `w4` 写 / `go` 放行)
    CMSIS-DAP 端 pyOCD 直接开会话(`connect_mode=attach`)读/写, 再 `target.resume()`

⚠ 两端都**不用 reset** —— 复位会打乱表钟与表内状态, 见文件头「不改 RAM、不复位」。
⚠ DAP 端**不新增任何要杀的进程名**: 它那个 gdbserver 的进程映像就是 `python.exe`,
  而 `kill_stray()` 是按**映像名**扫的(`taskkill /F /IM <名>`)—— 加进去会连本仓自己的
  解释器一起杀。DAP 端收尾只走「关会话」这一条路。见 `STRAY_PROCS` 上面那段。

⚠️ **为什么"核心在跑"不再等于"没事"(2026-09-17 实踩, 5-2 整场串口全 RX(0))**
   那天 FPB 四个槽全 0、`DHCSR=0x01000001`(核心在跑), 本模块照旧早退报"无需恢复" ——
   而**核一直被停住**: 残留的是 **DWT 数据观察点**(`DWT_COMP0=0x20007B20` 正是 `g_PowP[0]`
   的地址、`DWT_FUNCTION0=0x01000006` 使能位已关而 MATCHED 位还立着)。J-Link 一连上就把它
   认作"它不认识的数据观察点"并把核停住, 而那个地址**每周期**都被写一次 ⇒ 一放行就再停住。
   那是更早一次"在某个变量被写时停住"的实验(`-break-watch`)留下的。
   ⇒ 所以判据从"核心在不在跑"扩成 **"核心在不在跑 **且** 调试单元是不是空的"**。
   两套硬件互不相干: 断点走 FPB, "变量被写时停住"走 DWT —— 只清其中一处会漏掉另一处。

⚠️ 重要事实(2026-09-10 实测)：J-Link 会话**只要干净退出就会自动放行核心**。
   所以「连接一次再退出」本身就能把被撂住的核心救回来——上面那个 `go` 是双保险，
   多数情况下它报 `Error: CPU is not halted`(核心已自行恢复)，属正常，不是故障。
   反过来也意味着：**本模块没有真正「只看不改」模式**，`check_only=True` 同样会放行核心。
   真正会**把核心撂在 halt 不愿放手**的，是被强杀(SIGTERM/SIGKILL)的 GDB Server——
   这正是第 1 步要清掉残留进程的原因。

   也正因如此: swdbg/probe.py 那层**绝不 kill 任何进程**，只用干净 close 收尾，
   就是为了不制造需要本模块出场的局面。本模块是"万一"的兜底。

⚠️ 为什么 J-Link 端走 JLink.exe Commander 而不是 pylink(经评审保留, 勿"顺手统一")：
   * 本实现是现场验证过能救回表的；
   * 它需要**写**寄存器(FP_CTRL / FP_COMP)并发 `go`，而 probe.py 有意是**纯只读**的
     —— 那条性质本身有价值，不该为了统一而破坏。
   故两套并存: probe.py 只读走 pylink, 本模块恢复走 Commander。
   ⚠ CMSIS-DAP 端**没有** Commander 这一层(它是 SEGGER 的东西), 直接用 pyOCD 开会话读写 ——
     见 `_dap_session`。两端各用各的原生手段, 不为了"统一"把哪一端塞进另一端的壳子里。

用法(在 帧收发基础/ 下)
----------------------
    python -m swdbg.restore            # 查看状态并恢复(日常用这个)
    python -m swdbg.restore --check    # 只报告状态(核心在不在跑 + 调试单元里有没有残留)
"""
from __future__ import print_function

import argparse
import os
import re
import subprocess
import sys
import tempfile


# ---- 机器条件(= **这台机器**的事实) ----
# **单一事实源在 `machine/` 装机卡带**(2026-09-11 收敛, 见 machine/env_check.py)。原先本文件与
# breakpoint.py / probe.py 各抄一份, 换一台电脑要满仓找。方向: swdbg → common.machspec。
from common import loglabel   # 字形(含调试行 `[调试] [探针] …`)的唯一定义处
from common import machspec
from common.probe_guard import ProbeBusyError, acquire
from swdbg import probe_cmsis  # 只为 CMSIS-DAP 那一端的 backend 名
from swdbg import probesel    # 「该用哪支探针」的唯一定义处(两端通吃)

JLINK = machspec.get("JLINK")     # J-Link Commander(本模块用它发包)
DEVICE = machspec.get("DEVICE")   # 泛型即可, 依据 IAR 的 *_Debug.jlink 也写 Cortex-M0(见 probe_cmsis.py)
IFACE = machspec.get("IFACE")
SPEED = machspec.get("SPEED")     # kHz
SN = machspec.get("JLINK_SN")     # J-Link **声明**的序列号(None = 自动)。本模块不解析它(见 release_debug)
# CMSIS-DAP 端的目标名(取卡带)。⚠ 不是 `DEVICE` —— 那个 "Cortex-M0" 是 **J-Link 的泛型器件名**,
# 管理芯实为 SecurCore SC000(CPUID 0x410CC300), pyOCD 要的是 `cortex_m`。见 probe_cmsis.py 文件头。
DAP_TARGET = machspec.get("DAP_TARGET")

# ---- 管理芯的调试寄存器(这颗核是 SecurCore SC000 / ARMv6-M, 调试寄存器与 Cortex-M0 同址) ----
DHCSR = 0xE000EDF0        # bit1 C_HALT / bit17 S_HALT
FP_CTRL = 0xE0002000      # bit0 ENABLE, bit1 KEY
FP_COMP = [0xE0002008 + 4 * i for i in range(4)]   # 断点比较器只有 4 个(实测: ROM 表报 4 个 FPB)
# **第二套硬件**: "在某个变量被写时停住"(J-Link 的 `-break-watch`)用的是数据观察点单元,
# 与上面那四个断点槽互不相干 —— 只清 FPB 会漏掉它。见 `_stale_slots` 与文件头 ⚠️。
DWT_COMP = [0xE0001020 + 0x10 * i for i in range(4)]    # 比较器: 要比的**地址**
DWT_FUNC = [0xE0001028 + 0x10 * i for i in range(4)]    # 功能: 使能位与 MATCHED 位都在这儿

# 调试单元里**凡有内容就说明有残留**的槽(不含 FP_CTRL —— 它单独立着触发不了任何东西)。
UNIT_SLOTS = tuple(FP_COMP) + tuple(DWT_COMP) + tuple(DWT_FUNC)
# 读取时一并读上, 只为把现场报清楚(不参与"算不算残留"的判断)。
UNIT_REPORT = (FP_CTRL,) + UNIT_SLOTS

# 清理掉可能残留的调试进程(强杀 GDB Server 正是 halt 的元凶)。
#
# ⚠⚠ **这份名单是按"映像名"去扫的**(`kill_stray` 走 `taskkill /F /IM <名>`), 所以往这里加一个
#    名字 = 授权全机器范围内杀掉所有叫这个名字的进程。**CMSIS-DAP 端的 gdbserver 一律不许加** ——
#    它是 `python -m pyocd gdbserver`, 进程映像就是 **`python.exe`**, 加进去会把**本仓自己的
#    解释器**(以及这台机器上任何别的 python 程序)一起杀掉, 而 `kill_stray` 通常在
#    `release_debug` 的**第一步**跑 —— 那等于脚本一进门先自杀。
#    DAP 端收尾靠「关会话 / 让 gdb 断开使其自退」, 不靠杀; 详见 `_clear_and_go` 与 probe_cmsis.py。
STRAY_PROCS = ["JLinkGDBServerCL.exe", "JLinkGDBServer.exe", "arm-none-eabi-gdb.exe",
               "gdb-multiarch.exe"]

__all__ = ["release_debug", "kill_stray", "main", "SN", "DEVICE", "IFACE", "SPEED", "DHCSR",
           "FP_CTRL", "FP_COMP", "DWT_COMP", "DWT_FUNC", "UNIT_SLOTS", "STRAY_PROCS",
           "DAP_TARGET"]


def utf8_stdout():
    try:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass


def kill_stray():
    """只盘点残留调试进程，绝不按映像名强杀。

    旧实现的 ``taskkill /F /IM`` 会误杀别的会话，且杀掉 J-Link Server
    会把核心留在 halt、甚至把 USB/DLL 状态打坏。返回值保留旧 API 形状，
    但现在表示“发现了什么”，不是“杀掉了什么”。
    """
    found = []
    for name in STRAY_PROCS:
        try:
            p = subprocess.Popen(["tasklist", "/FO", "CSV", "/NH",
                                  "/FI", "IMAGENAME eq %s" % name],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 text=True, errors="replace")
            out, _ = p.communicate()
            if any(name.lower() in line.lower() and "No tasks" not in line
                   for line in (out or "").splitlines()):
                found.append(name)
        except Exception:
            pass
    return found


def run_jlink(script_lines, timeout=60):
    """跑一段 J-Link Commander 脚本，返回 (ok, 输出文本)。

    ``timeout`` 仅为兼容旧调用方保留；这里不能用 ``subprocess.run(timeout=...)``，
    因为超时会隐式 kill JLink.exe。Commander 脚本必须自行 ``Exit``，若它不退，
    本函数宁可继续等待并保留资源所有权，也不把“未知状态”伪装成已释放。
    """
    fd, path = tempfile.mkstemp(suffix=".jlink", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(script_lines) + "\n")
        p = subprocess.Popen([JLINK, "-CommanderScript", path],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, errors="replace")
        out, _ = p.communicate()
        r = type("Result", (), {"returncode": p.returncode, "stdout": out or ""})
        return r.returncode in (0, 1), r.stdout
    except FileNotFoundError:
        return False, "找不到 J-Link: %s" % JLINK
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass


def _word(text, addr):
    """从 J-Link Commander 的输出里抠出某个地址那一行的值; 没读到**返回 None**。

    ⚠ "读不到"与"读到 0"必须分得开 —— 合成一个 0 会让"这次没读成"伪装成"这个槽是空的",
    于是残留被当成干净、早退报"无需恢复"(正是 2026-09-17 那场事故的形状)。
    """
    m = re.search(r"%08X\s*=\s*([0-9A-Fa-f]{8})" % addr, text)
    return int(m.group(1), 16) if m else None


def parse_dhcsr(text):
    return _word(text, DHCSR)


def describe(dhcsr):
    halt = bool(dhcsr & (1 << 1))       # C_HALT
    s_halt = bool(dhcsr & (1 << 17))    # S_HALT
    return halt or s_halt


def _unit_snapshot(text):
    """把一次读取里的调试单元各项读成 `{地址: 值}`(读不到是 `None`)。"""
    return dict((a, _word(text, a)) for a in UNIT_REPORT)


def _stale_slots(snap):
    """从快照里挑出**残留的**槽 -> `(fpb, dwt)` 两个地址列表。

    判据是"槽**里有内容**" —— 而不是"上一场是谁留下的"(查不清, 也没用)。一场调试在动任何
    东西之前, 这些槽按定义都该是空的, 凡有内容的一律清掉, 不可能误伤。

    ⚠ 只看 `UNIT_SLOTS`(比较器), **不看 `FP_CTRL`**: 使能位单独立着而没有比较器时一个断点
    也触发不了, 那种情况下不该去动它(与 `breakpoint._clear_stale_fpb` 同一条分寸)。
    读不到(`None`)的槽**不算残留** —— 拿不准就不动。
    """
    fpb = [a for a in FP_COMP if snap.get(a)]
    dwt = [a for a in tuple(DWT_COMP) + tuple(DWT_FUNC) if snap.get(a)]
    return fpb, dwt


def _slots_txt(addrs, snap):
    return ", ".join("0x%08X=0x%08X" % (a, snap.get(a) or 0) for a in addrs)


def _tail(text, n=12):
    return text.splitlines()[-n:]


# ============================================================================
# 按端的三件动作 —— 「怎么读 / 怎么清 / 怎么放行」
# ============================================================================
# 上面那一整套**判据**(什么算残留、什么算干净、什么时候早退、怎么复核)**两端共用**;
# 底下这三件**动作**按端分派。分开的理由与 probe.py 拆两端时一样: 判据抄两份必然漂移,
# 而动作本来就长在两个不同的程序上。
class _Unreachable(RuntimeError):
    """连不上探针 / 连不上目标板 —— 这一趟到此为止, 由 `release_debug` 翻成 detail。"""


def _jl_head():
    """J-Link Commander 的连接 + 只读现场那一段(读与清、复核都用它, 免得三处各写一份)。"""
    head = ["si %s" % IFACE, "speed %d" % SPEED, "device %s" % DEVICE, "connect"]
    head += ["mem32 0x%08X 1" % a for a in (DHCSR,) + UNIT_REPORT]
    return head


def _dap_session(ident):
    """开一次 pyOCD 会话(**attach** —— 绝不因为"开了个会话来救场"而把表停住)。调用方负责关。

    ⚠ `connect_mode="attach"` **必须写**: pyOCD 的默认值是 `halt`, 不写的话这个"救火工具"
      一进门就先往表上浇一桶油(2026-09-20 反证实测: 默认下 DHCSR=0x030003 / S_HALT=1)。
      与 `probe_cmsis.CmsisDapDriver.open` 同一条铁律, 理由与实测数据见那个文件头。
    ⚠ 速度必须显式(铁律 1): 卡带 `SPEED` 是 kHz, pyOCD 要 Hz。
    """
    try:
        from pyocd.core.helpers import ConnectHelper
    except Exception as exc:
        raise _Unreachable("没装 pyocd(本模块的 CMSIS-DAP 端要用它): %s" % exc)
    import logging
    for name in ("pyocd", "pyocd.core", "pyocd.probe", "pyocd.target"):
        logging.getLogger(name).setLevel(logging.ERROR)   # 它自己的 INFO/WARN 混进日志会搅逐行校验
    opts = {"connect_mode": "attach", "resume_on_disconnect": True}
    try:
        sess = ConnectHelper.session_with_chosen_probe(
            blocking=False, unique_id=ident, auto_open=False,
            target_override=DAP_TARGET, frequency=int(SPEED) * 1000, options=opts)
    except Exception as exc:
        raise _Unreachable("开会话失败(uid=%s): %s" % (ident, exc))
    if sess is None:
        raise _Unreachable("没选中任何探针(uid=%s) —— 探针还在 USB 上吗" % ident)
    try:
        sess.open()
    except Exception as exc:
        try:
            sess.close()
        except Exception:
            pass
        raise _Unreachable("连不上目标(uid=%s, %d kHz): %s" % (ident, SPEED, exc))
    return sess


def _dap_word(target, addr):
    """读一个 32 位调试寄存器; **读不到返回 None**(与 `_word` 同一条分寸 —— 读不到 ≠ 读到 0)。"""
    try:
        return int(target.read_memory_block32(addr, 1)[0])
    except Exception:
        return None


def _read_state(backend, ident):
    """读一次现场 → `(dhcsr:int|None, snap:{地址: 值|None}, raw:[str])`。连不上抛 `_Unreachable`。

    ⚠ "读不到"与"读到 0"必须分得开(见 `_word` 的 ⚠): 读不到的槽在快照里留 `None`,
      由 `_stale_slots` 判"不算残留", 而不是被当成空。
    """
    if backend == probe_cmsis.BACKEND:
        sess = _dap_session(ident)
        try:
            t = sess.target
            dhcsr = _dap_word(t, DHCSR)
            snap = dict((a, _dap_word(t, a)) for a in UNIT_REPORT)
            raw = ["%s 端 uid=%s: DHCSR=0x%08X" % (backend, ident, dhcsr or 0)]
            raw += ["  0x%08X = %s" % (a, "读不到" if snap[a] is None else "0x%08X" % snap[a])
                    for a in UNIT_REPORT]
            return dhcsr, snap, raw
        finally:
            try:
                sess.close()
            except Exception:
                pass
    ok, out = run_jlink(_jl_head() + ["Exit"])
    if not ok:
        raise _Unreachable(out)
    if "Cannot connect" in out or ("ERROR" in out.upper() and "CPU is not halted" not in out):
        if "Cannot connect" in out or "VTref" not in out:
            raise _Unreachable("连不上目标板。请检查: J-Link USB、目标板供电、SWD 排线。")
    return parse_dhcsr(out), _unit_snapshot(out), _tail(out)


def _clear_and_go(backend, ident, stale_fpb, stale_dwt):
    """清掉那些残留的槽并放行核心 → `(ok, raw)`。**本来就空的槽一个字节都不写**。

    ⚠ 清法两端**同一条硬件口径**(FPB 与 DWT 是两套互不相干的单元, 只清一处会漏另一处, 见文件头):
        FPB: FP_CTRL 写 0x2(KEY=1 允许写 / ENABLE=0 关闭)再逐个 FP_COMP 写 0
        DWT: COMP 与 FUNCTION 都写 0(FUNCTION 里既有使能位也有 MATCHED 位)
    """
    if backend == probe_cmsis.BACKEND:
        sess = _dap_session(ident)
        try:
            t = sess.target
            if stale_fpb:
                t.write_memory_block32(FP_CTRL, [0x00000002])
                for a in stale_fpb:
                    t.write_memory_block32(a, [0x00000000])
            for a in stale_dwt:
                t.write_memory_block32(a, [0x00000000])
            # 核心本就在跑时不发 resume —— 那一步在跑着的核上是空动作/报错, 与 J-Link 端
            # `go` 报 "CPU is not halted" 是同一种情形, 两边都不把它当失败。
            if describe(_dap_word(t, DHCSR) or 0):
                t.resume()
            return True, ["%s 端 uid=%s: 已清 %d 个 FPB 槽 / %d 个 DWT 槽, 核心按需放行"
                          % (backend, ident, len(stale_fpb), len(stale_dwt))]
        except Exception as exc:
            return False, ["%s 端 uid=%s 清除失败: %s" % (backend, ident, exc)]
        finally:
            try:
                sess.close()          # 关会话即放行(resume_on_disconnect)
            except Exception:
                pass
    rest = ["si %s" % IFACE, "speed %d" % SPEED, "device %s" % DEVICE, "connect"]
    if stale_fpb:
        rest.append("w4 0x%08X 0x00000002" % FP_CTRL)
        for a in stale_fpb:
            rest.append("w4 0x%08X 0x00000000" % a)
    for a in stale_dwt:
        rest.append("w4 0x%08X 0x00000000" % a)
    rest += ["go", "Exit"]             # 核心本就在跑时 `go` 会报 "CPU is not halted", 正常
    ok, out = run_jlink(rest)
    return ok, [out]


def _resolve_probe(probe):
    """该用哪一支 → `(backend, ident, 逐条记账)`; 选不出抛 `_PS.ProbeSelectError`(带逐条原因)。"""
    if probe is not None:
        backend, ident = probe
        return backend, ident, ["用已选定的探针: %s 端 %s(本次不再重挑)" % (backend, ident)]
    # 恢复动作本身会在 _read_state/_clear_and_go 中真实连接；不要在选探针时
    # 先开关一次 J-Link，避免恢复工具反而制造新的占用状态。
    return probesel.pick(speed=SPEED, verify_target=False)


def _release_debug_locked(serial_no=SN, clean_stray=True, check_only=False, probe=None):
    """把被撂在 halt 的管理芯放开, 并清空 FPB 断点槽。**库面入口**。

    参数:
        serial_no    **已废弃/未使用** —— 本函数走 J-Link Commander(不指定 SN, 认唯一那支探针),
                     这个形参是历史遗留, 传什么都不影响。留着只为不改动现有调用点。
        clean_stray  先清掉残留的 JLinkGDBServerCL / gdb 进程(强杀它们正是 halt 的元凶)。
                     默认 True; 调用方若有别的 J-Link 会话在用, 传 False。
        check_only   只读状态、不做恢复。注意: 探针会话干净退出**本身就会放行核心**,
                     所以本模式并非真正"只看不改"(见文件头 ⚠️)。
        probe        `(backend, ident)` —— **已经选好**的那一支(见 `swdbg/probesel.py`)。
                     不给就自己挑(会真开一次会话读 CPUID 证明"探针后面就是本表那颗核")。
                     `breakpoint.Session` 收尾复核时会把它自己那一支显式传进来, 免得重挑一遍。

    返回 (ok: bool, detail: dict)。detail 键:
        backend       str       实际用的是哪一端("jlink" / "cmsis-dap")
        ident         int|str   那一端的具体那一支(SN / UID)
        reachable     bool       能否连上探针 / 目标板
        was_halted    bool|None  恢复前是否处于 halt(None = 没读到)
        dhcsr_before  int|None
        dhcsr_after   int|None   恢复后复核值(None = 未复核)
        released      bool       最终确认核心在运行
        stale_fpb     [int]      恢复前 FPB 里残留的槽(地址)
        stale_dwt     [int]      恢复前 DWT 里残留的槽(地址)
        cleared_fpb   bool       是否执行过清 FPB
        cleared_dwt   bool       是否执行过清 DWT
        units_clear   bool|None  复核: 调试单元是不是真的空了(None = 没读到, 不敢说空)
        stray         [str]      被清掉的残留进程名
        probe_report  [str]      探针挑选的逐条记账(枚举到几支 / 哪一支证明成功 / 选中谁)
        fatal         str|None   "jlink_failed" / "unreachable" / "no_dhcsr" / None
        reason        str        人话说明(可直接打印)
        raw_tail      [str]      J-Link 原始输出尾部(排障用)

    ok=True 的判据: 连上了 **且** 最终确认核心在运行 **且** 调试单元是空的。
    核心在跑、单元也空 ⇒ 一个字节都不写, 直接 ok=True —— 这是最常见的情况, 不是错误。
    """
    d = {"backend": None, "ident": None, "reachable": False, "was_halted": None,
         "dhcsr_before": None,
         "dhcsr_after": None, "released": False, "cleared_fpb": False, "cleared_dwt": False,
         "units_clear": None, "stale_fpb": [], "stale_dwt": [],
         "stray": [], "probe_report": [], "fatal": None, "reason": "", "raw_tail": []}

    # ---- 该用哪一支(两端通吃)----
    # 选不出 = "没有可用的探针", 不是"探针坏了" —— 逐条原因由 ProbeSelectError 一路带上来。
    try:
        backend, ident, report = _resolve_probe(probe)
        d["probe_report"] = list(report)
    except probesel.ProbeSelectError as e:
        d["fatal"], d["reason"] = "no_probe", "选不出探针: %s" % e
        return False, d
    except RuntimeError as e:
        d["fatal"], d["reason"] = "no_machine", "机器条件不齐: %s" % e
        return False, d
    d["backend"], d["ident"] = backend, ident

    # 机器条件齐不齐 —— 在动探针之前核。**这里接住异常而不让它冒出去**: 本函数的契约是
    # "返回 (ok, detail), 不抛"(`_restore_all.py` 拿它当第 1 步的总复位入口, 一个 traceback
    # 会把"机器没配好"报成"脚本崩了")。
    # ⚠ **按端核对应的那几项**: 在 DAP 端核 `JLINK`/`DEVICE`/`IFACE` 是拿别人的机器条件
    #   去拦自己(DAP 端一个都用不上), 反之亦然 —— 与 `breakpoint.Session.__init__` 同一条。
    try:
        if backend == probe_cmsis.BACKEND:
            machspec.ready(DAP_UID=ident, DAP_TARGET=DAP_TARGET, SPEED=SPEED)
        else:
            machspec.ready(JLINK=JLINK, DEVICE=DEVICE, IFACE=IFACE, SPEED=SPEED)
    except RuntimeError as e:
        d["fatal"], d["reason"] = "no_machine", "机器条件不齐: %s" % e
        return False, d

    if clean_stray:
        d["stray"] = kill_stray()

    # ---- 读状态(只读一遍现场: DHCSR 核心在不在跑 + 调试单元各项有没有残留) ----
    try:
        dhcsr, snap, raw = _read_state(backend, ident)
    except _Unreachable as e:
        d["fatal"], d["reason"] = "unreachable", str(e)
        return False, d
    d["raw_tail"] = raw

    if dhcsr is None:
        d["fatal"] = "no_dhcsr"
        d["reason"] = "读不到 DHCSR，无法判定状态。"
        return False, d

    d["reachable"] = True
    d["dhcsr_before"] = dhcsr
    d["was_halted"] = describe(dhcsr)
    d["stale_fpb"], d["stale_dwt"] = _stale_slots(snap)

    if not d["was_halted"] and not d["stale_fpb"] and not d["stale_dwt"]:
        # 核心在跑 **且** 调试单元是空的 —— 这才是真"无事可做"。
        # ⚠ 判据里那第二个条件 2026-09-17 才补上: 只看"核心在不在跑"时, 一个残留的 DWT
        #   数据观察点会被整段放过去(核心是跑的, 可它在每次被写时又被停住) —— 见文件头 ⚠️。
        d["released"] = True
        d["units_clear"] = True
        d["reason"] = "核心运行中(正常), 调试单元也是空的, 无需恢复"
        return True, d

    if check_only:
        if d["was_halted"]:
            d["reason"] = ("读到的是 HALT；但这次探针会话退出时已把核心放行, "
                           "核心现在多半已在运行")
        else:
            d["reason"] = ("核心在运行, 但调试单元里还留着 %d 个槽(FPB: %s / DWT: %s) —— "
                           "去掉 --check 再跑一次清掉它"
                           % (len(d["stale_fpb"]) + len(d["stale_dwt"]),
                              _slots_txt(d["stale_fpb"], snap) or "空",
                              _slots_txt(d["stale_dwt"], snap) or "空"))
        return False, d                           # 与 CLI 的原语义一致: --check 返回 1

    # ---- 恢复：清残留的调试单元 + 放行(动作按端分派, 判据共用) ----
    # **只写查出来有内容的那些槽** —— 本来就干净的不碰(见 docstring 的"一个字节都不写")。
    # 写 PPB 寄存器是在**核心运行中**做的, 写没写进去一律以随后的复核为准, 不在这里假定。
    ok2, raw2 = _clear_and_go(backend, ident, d["stale_fpb"], d["stale_dwt"])
    if not ok2:
        d["raw_tail"] = raw2
        d["reason"] = "清调试单元/放行失败: %s" % " | ".join(raw2)[:400]
        return False, d
    d["cleared_fpb"] = bool(d["stale_fpb"])
    d["cleared_dwt"] = bool(d["stale_dwt"])

    # ---- 复核: 核心在跑 **且** 那些槽真的空了(读回来验, 不假定写成功) ----
    # ⚠ 复核**也走 `_read_state`**, 与读状态那一趟是同一条路 —— 两端各写一份复核的话,
    #   "清完了"这个结论就会有两个来源, 而它们迟早会不一样。
    try:
        dhcsr2, snap2, _raw3 = _read_state(backend, ident)
    except _Unreachable as e:
        d["raw_tail"] = [str(e)]           # 只有复核**失败**时才用复核这一趟的输出替掉现场
        d["reason"] = "复核读取失败，请手动再跑一次 --check"
        return False, d
    if dhcsr2 is None:
        d["reason"] = "复核读取失败，请手动再跑一次 --check"
        return False, d
    d["dhcsr_after"] = dhcsr2
    d["released"] = not describe(dhcsr2)
    left_fpb, left_dwt = _stale_slots(snap2)
    # ⚠ 复核**也**要能读得出那些槽, 否则"读不到"会被 `_stale_slots` 当成"空了" ⇒ 假通过。
    readable = all(snap2.get(a) is not None for a in d["stale_fpb"] + d["stale_dwt"])
    d["units_clear"] = (not left_fpb and not left_dwt) if readable else None

    wrote = []
    if d["cleared_fpb"]:
        wrote.append("FPB %d 槽" % len(d["stale_fpb"]))
    if d["cleared_dwt"]:
        wrote.append("DWT %d 槽" % len(d["stale_dwt"]))
    # 只为放行而进来的一趟(核心被 halt、槽本来就空): 一个槽都没写, 话就别提"写"。
    did = ("已清 %s 并放行" % "、".join(wrote)) if wrote else "调试单元本来就空, 直接放行"
    if d["units_clear"] is None:
        d["reason"] = ("%s, 但复核时读不回那些槽 —— 清没清掉**不敢说**, "
                       "请手动再跑一次 --check" % did)
    elif not d["units_clear"]:
        d["reason"] = ("%s, 复核**仍在**: FPB %s / DWT %s"
                       % (did, _slots_txt(left_fpb, snap2) or "空",
                          _slots_txt(left_dwt, snap2) or "空"))
    elif d["released"]:
        d["reason"] = "%s, 复核核心运行中且调试单元已空" % did
    else:
        d["reason"] = "放行后复核仍 HALT"
    return (d["released"] and bool(d["units_clear"])), d


def release_debug(serial_no=SN, clean_stray=True, check_only=False, probe=None):
    """串行化整个恢复过程；不允许诊断/恢复与真实会话并发碰探针。"""
    try:
        with acquire("restore-debug"):
            return _release_debug_locked(serial_no=serial_no,
                                         clean_stray=clean_stray,
                                         check_only=check_only,
                                         probe=probe)
    except ProbeBusyError as exc:
        d = {"backend": None, "ident": None, "reachable": False,
             "was_halted": None, "dhcsr_before": None, "dhcsr_after": None,
             "released": False, "stale_fpb": [], "stale_dwt": [],
             "cleared_fpb": False, "cleared_dwt": False, "units_clear": None,
             "stray": [], "probe_report": [], "fatal": "probe_busy",
             "reason": str(exc), "raw_tail": []}
        return False, d


# ============================ CLI 薄壳(输出与重构前逐字一致) ============================
def main(argv=None):
    utf8_stdout()
    ap = argparse.ArgumentParser(description="把被调试器停住的管理芯放开运行")
    ap.add_argument("--check", action="store_true",
                    help="只报告状态(核心在不在跑、调试单元里有没有残留槽)，一个字节都不写。"
                         "注意: 探针会话退出本身会放行核心，故 --check 读到 HALT "
                         "并不等于『现在还是 HALT』")
    ap.add_argument("--keep-servers", action="store_true",
                    help="不要清理残留的调试进程")
    args = ap.parse_args(argv)

    # 横幅先说"这台机器上该找哪一端"(卡带 `PROBE`), 再在 `[2/4]` 报**实际用的是哪一支** ——
    # 两支探针的现场长得不一样, 复盘时要一眼看得出这一趟底下是谁。
    print("=" * 62)
    print("管理芯调试状态恢复  (卡带 PROBE=%s, SPEED=%s kHz)"
          % (machspec.get("PROBE") or "自动找两端", SPEED))
    print("=" * 62)

    ok, d = release_debug(clean_stray=not args.keep_servers, check_only=args.check)

    if args.keep_servers:
        print("[1/4] 按要求跳过进程清理")
    elif d["stray"]:
        print("[1/4] 发现残留调试进程(未强杀): %s" % ", ".join(d["stray"]))
    else:
        print("[1/4] 无残留调试进程")

    if d["fatal"] == "probe_busy":
        print("[2/4] 探针正在被另一个会话占用，未执行恢复: %s" % d["reason"])
        return 1
    if d["fatal"] in ("no_probe", "no_machine"):
        print("[2/4] %s" % d["reason"])
        return 1
    if d["fatal"] == "jlink_failed":
        print("[2/4] 连接失败: %s" % d["reason"])
        return 1
    if d["fatal"] == "unreachable":
        print("[2/4] %s" % d["reason"])
        print("-" * 62)
        for line in d["raw_tail"]:
            print("   " + line)
        return 1
    if d["fatal"] == "no_dhcsr":
        print("[2/4] %s 原始输出尾部:" % d["reason"])
        for line in d["raw_tail"]:
            print("   " + line)
        return 1

    print("[2/4] 探针 = %s 端 / %s" % (d["backend"], d["ident"]))
    # 探针**怎么选出来的**逐条记账 —— 来源格是 `[探针]`, 与 `[SWD]`(连上之后读了什么)分工不同:
    # 没有这一段的话, 日志里只剩上面那一行结论, 过程(枚举到几支、哪一支读 CPUID 证明了)不留痕。
    for _line in (d.get("probe_report") or []):
        print("   %s" % loglabel.debug_line(loglabel.DEBUG_PROBE, _line))

    print("[2/4] DHCSR = 0x%08X  ->  %s"
          % (d["dhcsr_before"],
             "核心处于 HALT(表不会应答串口)" if d["was_halted"] else "核心运行中(正常)"))
    if d["stale_fpb"] or d["stale_dwt"]:
        print("      调试单元里有残留: FPB %d 槽%s / DWT %d 槽%s"
              % (len(d["stale_fpb"]),
                 ("(%s)" % ", ".join("0x%08X" % a for a in d["stale_fpb"])) if d["stale_fpb"] else "",
                 len(d["stale_dwt"]),
                 ("(%s)" % ", ".join("0x%08X" % a for a in d["stale_dwt"])) if d["stale_dwt"] else ""))

    if not d["was_halted"] and not d["stale_fpb"] and not d["stale_dwt"]:
        print("[3/4] 无需恢复")
        print("[4/4] ✅ 核心已在运行、调试单元也是空的，串口应可正常收发")
        return 0

    if args.check:
        print("[3/4] --check 模式，跳过清调试单元 / go")
        print("[4/4] ⚠️  %s" % d["reason"])
        return 1

    if not d["cleared_fpb"] and not d["cleared_dwt"]:
        print("[3/4] %s" % d["reason"])          # 只为放行而来的一趟(核心 halt、单元本来就空)
        if not d["released"]:
            return 1
    else:
        print("[3/4] 已清 %s 并发出 go"
              % "、".join(x for x in (
                  ("FPB %d 槽" % len(d["stale_fpb"])) if d["cleared_fpb"] else "",
                  ("DWT %d 槽" % len(d["stale_dwt"])) if d["cleared_dwt"] else "") if x))

    if d["dhcsr_after"] is None:
        print("[4/4] ⚠️  %s" % d["reason"])
        return 1
    print("[4/4] 复核 DHCSR = 0x%08X  ->  %s | 调试单元: %s"
          % (d["dhcsr_after"], "运行中 ✅" if d["released"] else "仍 HALT ❌",
             "已空 ✅" if d["units_clear"] else
             ("读不回来 ⚠️" if d["units_clear"] is None else "仍有残留 ❌")))
    if not (d["released"] and d["units_clear"]):
        print("      %s" % d["reason"])
        return 1
    print()
    print("已恢复正常。建议接着跑一次串口冒烟确认:")
    print("    cd 帧收发基础 && python -m meterlib.cmd_bank smoke")
    return 0


if __name__ == "__main__":
    sys.exit(main())
