# -*- coding: utf-8 -*-
"""
common/machspec.py —— 当前**装机卡带**(这台机器的条件)的中立取用入口。

它和 `common/profile.py` 是**同一台机器的两个正交卡带槽**(机制都在 `common/cardslot.py`):

    profile     "本表环境包"  —— 这块表的事实(表号 / 双芯AF / RAM 地址图 / .out 路径)
    machspec    "装机卡带"    —— 这台机器的事实(gdb / J-Link / 串口 / 工具链路径)

换表换 profile, 换机器换 machspec, 两者互不相干 —— 这正是把轴拆开的理由:
`launch.json`、`breakpoint.py`、`probe.py`、`restore.py`、`_install_gdb.py` 原先各抄一份
"gdb 在哪 / SN 多少 / 哪个 COM", 一处改动要满仓找。现在它们只认识本模块。

为什么它住 `common/` 而不是 `machine/`
-------------------------------------
住 `machine/` 的话, 引擎(swdbg/meterlib)就得 import 那盘卡带 —— 方向又反了(阶段一/二的同一个毛病)。
住这里, 引擎只认识 `common.machspec`, 卡带的名字作为**字符串默认值**藏在槽里(见下 `_SLOT`);
`machine/__init__.py` 是组合根, 它把卡带装上来。依赖方向:

    machine ──→ common.machspec ←── swdbg / meterlib / scripts

没装卡带时报什么
---------------
本模块**不静默降级**: `get()` 给默认值(可选属性), `require()` 缺字段**或字段值为 None** 就抛,
并点名缺了哪几个。**别把它端成"没卡带也能跑"** —— 能跑的是纯协议部分, 要连 J-Link 的地方
本来就该有机器条件。

用法
----
    from common import machspec as MS

    MS.get("COM")                          # 可选属性: 没有给 None
    MS.require("GDB", "JLINK_SN")          # 必备: 缺了当场抛, 点名是哪几个
    M = MS.Proxy()                         # 引擎侧写法: M.GDB 每次现取(与 profile.Proxy 同)
    M.COM
"""
from common import cardslot

# "默认卡带"：没显式 configure 过时, 去哪找这台机器的条件。**这里是个字符串, 不是 import 语句**
# —— 于是 swdbg 静态上不依赖 machine(它只知道 common.machspec), 换机器只改这一处或调
# set_default_source()/configure()。想彻底不带卡带跑(比如纯离线解析 .out), 调
# set_default_source(None) 即可, 届时 require() 会给出清楚的报错。
_SLOT = cardslot.Slot(("machine", "CURRENT"), label="装机卡带",
                       howto="组合根要先调 `common.machspec.configure(machine.CURRENT)`")

__all__ = ["configure", "current", "get", "need", "require", "ready", "has", "clear",
           "bootstrap", "set_default_source", "Proxy"]

configure = _SLOT.configure
clear = _SLOT.clear
set_default_source = _SLOT.set_default_source
bootstrap = _SLOT.bootstrap
current = _SLOT.current
has = _SLOT.has
get = _SLOT.get
need = _SLOT.need
require = _SLOT.require
Proxy = _SLOT.Proxy
resolve = _SLOT.resolve      # 卡带声明的路径 → 绝对路径(相对卡带包目录; 见 cardslot.resolve_in)


def ready(**vals):
    """`ready(JLINK_SN=serial_no, DEVICE=device, …)` —— 把**实际要用**的机器条件传进来,
    值为 None 的当场抛并点名(实现在 `cardslot.Slot.require`)。齐了就原样返回。

    为什么这么设计: **"哪个引擎需要哪几项"由调用方自己说** —— `probe` 压根不用 gdb, 不该被
    要求本机有 gdb; 而卡带也不必知道世上有哪些消费方(那会把依赖又接反)。机制(怎么报错)
    只有这一份, 清单在调用处, 读代码的人一眼看得见他依赖了哪些机器条件。

    谁调、什么时候调: **需要连探针的地方, 在连之前**。理由只有一个 —— `serial_no=None` 传给
    J-Link 的语义是**不挑探针**(会连上随便一块), 那是静默连错, 本仓最防的一类错。
    """
    _bad = [k for k, v in vals.items() if v is None]
    if _bad:
        _SLOT.require(*_bad)
    return vals


def __getattr__(name):
    """PEP 562: 把槽的**内部状态**照旧透出去 —— `machspec._DEFAULT_SOURCE` 等。

    与 `common/profile.py` 同款, 理由一样: 必须**动态透传**, 复制成模块级常量就成了假的事实源
    (它不会跟着 `configure()`/`set_default_source()` 变)。
    """
    _dyn = {"_DEFAULT_SOURCE": "default_source", "_PROFILE": "obj", "_TRIED": "tried"}
    attr = _dyn.get(name)
    if attr is None:
        raise AttributeError("module 'common.machspec' has no attribute %r" % name)
    return getattr(_SLOT, attr)
