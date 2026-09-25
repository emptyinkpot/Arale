# -*- coding: utf-8 -*-
"""
swdbg —— 管理芯 SWD 直读白盒库(第二条观察通路, 与串口 AA80 平行)

为什么单独建库
--------------
串口 AA80 白盒(`meterlib.watch`)与 JTAG 直读是**两条独立的观察通路**,
各有各的下游依赖:
    watch(AA80 观察) → p645(645 编解码) → common.portsel(串口)
    swdbg               → pylink-square → J-Link → SWD
硬揉进 meterlib 会让"纯协议层"背上 J-Link 这个可选重依赖(bench 上没 J-Link 的机器 import
meterlib 就炸)。故 swdbg 与 meterlib **平行**, 互不 import。

**零 meterlib 依赖(2026-09-10 达成)**: 原先本包为"地址解析不抄第二份", 反过来 import
`meterlib.whitebox._pvar_addr` —— 于是 SWD 通路硬挂在串口协议模块上, 想拿它指一块**陌生表**,
会拽着 meterlib 乃至那张表的画像一起进来。现已把解析提到中立层 `common/varresolve`,
meterlib 与 swdbg 各取所需、彼此不相识; "两边解析必须一致"于是由**结构**保证, 不再需要断言去守。
指一块陌生表: `common.varresolve.configure(out_path=r"...\某表.out")` 即可按名定址, 不需要任何画像。

分层
----
    probe.py       **门面**: 会话对象 + 读原语(不 halt / 显式速度 / 干净退出)。
                   派生读法(read_many/read_stable/halted/dhcsr)只写这一份, 两端共用。
    probe_jlink.py J-Link 端驱动(pylink-square)
    probe_cmsis.py CMSIS-DAP 端驱动(DAPLink + pyOCD)
    probesel.py    「该用哪支探针」的**唯一解析器**(判据→候选→逐个证明→恰好一支),
                   **兼双端体检入口**(--doctor)
    jlink.py      J-Link 那一端的枚举/判据 **+ 探针体检与恢复**(--doctor / --recover)
    restore.py   被撂在 halt 的核心救回来(自 _dbg/dbg_restore.py 迁入)

两端(2026-09-20 起): 底下是 J-Link 还是 CMSIS-DAP 类探针, **对上层完全透明** ——
`Probe()` 自己去认(判据在卡带, 证明靠读 CPUID `0xE000ED00` 必须 == `0x410CC300`)。
⚠ 两端各有一条**必须显式写出来**的安全前提: J-Link 端是"显式速度", CMSIS-DAP 端是
  `connect_mode=attach`(pyOCD 默认是 halt, 不写就把表停住)。见各自文件头。

⚠ 本包**不含**"AA80 vs SWD 逐字节对照"那个自检了 —— 那个比对天生要同时握着两条通路与那块表,
  是**表**的属性不是**通路**的属性 —— 那个对照现在住在 `scripts/_check_aa80_vs_swd.py`。

白盒脚本怎么用
--------------
    串口(AA80):  from meterlib import watch as W; W.watch_vars(ser, names)
探针那条路的读法见 `scripts/_check_aa80_vs_swd.py`(它握着两条通路做逐字节对照)。

常用命令(在 帧收发基础/ 下):
    python -m swdbg.restore              # 核心被撂在 halt 时救回来
    python -m swdbg.jlink --doctor       # 探针连不上: 查清是"不在 USB 上"还是"通信层坏了"
    python -m swdbg.jlink --recover      # 按诊断把探针弄回来(要管理员; --dry 先看计划)
真表 AA80↔SWD 逐字节对照(表自己的属性): python scripts/_check_aa80_vs_swd.py

⚠ **本包没有离线/模拟通路, 也没有自检入口** —— 不许拿假探针/假串口顶真表: 结论只能来自
  实物(真 J-Link、真表、真串口)。要证据就在 project/tests/ 里跑那一项。

注: 子模块**按需加载**(PEP 562)。若在此处 import 子模块, `python -m swdbg.restore` /
`python -m swdbg.jlink` 会因"模块已在 sys.modules 里"而打 RuntimeWarning —— 而那两个命令
正是本包的主要用法。
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
