# -*- coding: utf-8 -*-
"""
swdbg —— 管理芯 SWD 直读白盒库

    elf.py        .out(ELF) 符号表读取 + DWARF 类型(纯离线)
    resolve.py    变量名 → (绝对地址, 长度) 的唯一解析实现(源头 = .out 符号表)
    probe.py      门面: 会话对象 + 读原语(不 halt / 显式速度 / 干净退出)
    probe_jlink.py J-Link 端驱动(pylink-square)
    probe_cmsis.py CMSIS-DAP 端驱动(DAPLink + pyOCD)
    probesel.py   「该用哪支探针」的唯一解析器(判据→候选→逐个证明→恰好一支), 兼双端体检入口
    jlink.py      J-Link 端的枚举/判据 + 探针体检与恢复(--doctor / --recover)
    restore.py    被撂在 halt 的核心救回来
    gdbinit.py    **整片 .out 地图**: 一场会话解一次, 挂 `g._elfmap`
    breakpoint.py 断点级白盒(停核 + 读局部量 + 受控注入)

两端(2026-09-20 起): 底下是 J-Link 还是 CMSIS-DAP 类探针, 对上层完全透明 ——
`Probe()` 自己去认(判据在卡带, 证明靠读 CPUID `0xE000ED00` 必须 == `0x410CC300`)。
⚠ 两端各有一条**必须显式写出来**的安全前提: J-Link 端是"显式速度", CMSIS-DAP 端是
  `connect_mode=attach`(pyOCD 默认是 halt, 不写就把表停住)。见各自文件头。

常用命令(在 帧收发基础/ 下):
    python -m swdbg.restore              # 核心被撂在 halt 时救回来
    python -m swdbg.jlink --doctor       # 探针连不上: 查清是"不在 USB 上"还是"通信层坏了"
    python -m swdbg.jlink --recover      # 按诊断把探针弄回来(要管理员; --dry 先看计划)

⚠ **本包没有离线/模拟通路, 也没有自检入口** —— 不许拿假探针/假串口顶真表: 结论只能来自
  实物(真 J-Link、真表、真串口)。
"""
import importlib

# 名字 → 子模块。对外仍可 `from swdbg import Probe`, 但只有真用到才去 import 那个子模块。
_LAZY = {
    "Probe": "probe", "ProbeError": "probe",
    "SN": "probe", "DEVICE": "probe", "IFACE": "probe", "SPEED": "probe", "DHCSR": "probe",
    # 二级通路: 断点级白盒(停核 + 读局部量 + 受控注入)。与 probe 并列, 不是替代 —— 能不停核
    # 解决的判据一律走 probe; 只在"要停在某文件:行才成立"时用它。见 breakpoint 文件头。
    "Session": "breakpoint", "Hit": "breakpoint", "GdbError": "breakpoint", "session": "breakpoint",
    # 注: 不登记 "breakpoint" 这个名字 —— 本 __getattr__ 没有"取模块本身"的回退(与 common/__init__ 不同),
    # 登记了反而 AttributeError。要模块就 `import swdbg.breakpoint`(或 `from swdbg import breakpoint`)。
}

__all__ = list(_LAZY)


def __getattr__(name):
    mod = _LAZY.get(name)
    if mod is None:
        raise AttributeError("module 'swdbg' has no attribute %r" % name)
    value = getattr(importlib.import_module("swdbg.%s" % mod), name)
    globals()[name] = value            # 缓存, 后续访问不再走 __getattr__
    return value


def __dir__():
    return sorted(__all__)
