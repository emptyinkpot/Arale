# -*- coding: utf-8 -*-
"""
common/profile.py —— 当前活动画像(那块表的"机器条件")的**中立取用入口**。

为什么要有它(阶段二去耦合的枢纽)
--------------------------------
原先 `cmd_bank` / `ez_meter` / `whitebox` 三个共享引擎模块各自写着
`from project import CURRENT as P`。方向是**反的**: 共享引擎(换表不变)依赖了
每表专属的环境包(换表就换)。后果很具体: **换一块表要改的是 project/,
可 meterlib 也得跟着 import 一个可能不存在的 project**, 于是"引擎"和"卡带"焊死了。

解法与 `swdbg/resolve` 完全同构(那儿是"配方/机制"分离, 这儿是"画像/机制"分离):
    机制(怎么用画像)留在 meterlib; 配方(**哪份**画像)由组合根喂进来。
依赖变成:  project ──→ common.profile ←── meterlib
两边都指向中立层, 彼此不相识。**本模块不 import meterlib / swdbg / project 中任何一个。**

⚠ 2026-09-11: 槽机制已提到 `common/cardslot.py` —— 因为本仓多了**第二盘正交卡带**
(`machine/` 装机卡带), 两盘要的是同一件事。**本文件现在只剩"这盘卡带叫什么"这一件事**:
`_SLOT = cardslot.Slot(("project","CURRENT"), label="画像")`。要改槽的行为请去 cardslot。

谁装配
------
组合根(入口脚本 / `project/__init__.py` 自装)调一次:

    from common import profile
    profile.configure(project.CURRENT)

`project/__init__.py` 已经这么做了 —— 那句话就是"装卡带"这个动作的正式落点
(它的 docstring 本就写着"装它 = 有调这张表的机器条件")。测试脚本里那些
`from project import CURRENT as P` **照旧可用**, 它们读的是同一份数据, 只是走了另一条路。

怎么避开"装配有时序"
--------------------
画像属性可能在**模块级**被读(如 `ADDR_698 = ... P.SERVER_TAIL` 这类模块级派生常量 ——
换表前它们就长在 meterlib 里)。若直接 `P = profile.current()` 在 import 期取值, 就要求
**先 import project 再 import meterlib**, 极脆。

所以给的是 `Proxy`(见 cardslot): `P = profile.Proxy()`, 属性**每次访问现取**。
模块级那句在 import 时求值 → 触发自举(默认卡带), 效果与今天完全一致;
而 `configure()` 换过画像之后, 函数体里的 `P.XXX` 也会跟着换 —— 模块级与函数级一视同仁。

没卡带时的分寸
--------------
`p698` 的 `ADDR_698` / `p645` 的 `TABLE_ADDR` 落成 `None`(服务器地址本就是每表事实, 没表就没有它),
**纯协议部分(CRC / 组帧 / 解析)照旧可用**; 而白盒监视(`meterlib.watch` 的 watch_vars / WatchBank)
无表即无意义, 就报清楚的 RuntimeError。**不静默降级**, 也不拿"没表"当借口把整层锁死。

没装配时报什么
-------------
`current()` 抛 RuntimeError 并写清怎么装配, **不静默返回 None** —— 否则下游会拿到一串
莫名其妙的 AttributeError, 顺着往上找只会找到"None 没有这个属性", 找不到真因。

用法
----
    from common import profile

    P = profile.Proxy()                # ← meterlib 里就长这样: P.RAM_BASE 照旧写

    profile.current().RAM_BASE         # 画像属性(每次现取)
    profile.get("OUT_PATH")            # 拿不到给 None(可选属性用这个)
    profile.need("OUT_PATH")   # 要一串必备属性, 缺了就抛(装配期就炸)
"""
from common import cardslot

# "默认卡带"：没显式 configure 过时, 去哪找画像。**这里是个字符串, 不是 import 语句** ——
# 于是 meterlib 静态上不依赖 project(它只知道 common.profile), 换部署只改这一处或调
# set_default_source()/configure()。想彻底不带卡带跑(比如纯离线解析 .out), 调
# set_default_source(None) 即可, 届时不开 --out 的调用会拿到 current() 的清楚报错。
_SLOT = cardslot.Slot(("project", "CURRENT"), label="画像",
                       howto="组合根要先调 `common.profile.configure(project.CURRENT)`")

__all__ = ["configure", "current", "get", "need", "has", "clear", "bootstrap",
           "set_default_source", "Proxy", "resolve"]

configure = _SLOT.configure
clear = _SLOT.clear
set_default_source = _SLOT.set_default_source
bootstrap = _SLOT.bootstrap
current = _SLOT.current
has = _SLOT.has
get = _SLOT.get
need = _SLOT.need
Proxy = _SLOT.Proxy
resolve = _SLOT.resolve      # 卡带声明的路径 → 绝对路径(相对卡带包目录; 见 cardslot.resolve_in)


def __getattr__(name):
    """PEP 562: 把槽的**内部状态**照旧透出去 —— `profile._DEFAULT_SOURCE` / `._PROFILE` / `._TRIED`。

    ⚠ 必须**动态透传**, 不能在 import 期复制成模块级常量: 复制品不会跟着 `configure()` /
    `set_default_source()` 变, 那就成了一份**假的事实源** —— 而 `meterlib/selftest.py` 正拿
    `profile._DEFAULT_SOURCE == ("project","CURRENT")` 当断言在守"卡带名只是字符串默认值"这条。
    读得到、且读的是**活的**那一个, 这才对。写入不走这里(走 `set_default_source` 等函数)。
    """
    _dyn = {"_DEFAULT_SOURCE": "default_source", "_PROFILE": "obj", "_TRIED": "tried"}
    attr = _dyn.get(name)
    if attr is None:
        raise AttributeError("module 'common.profile' has no attribute %r" % name)
    return getattr(_SLOT, attr)
