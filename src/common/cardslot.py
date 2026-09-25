# -*- coding: utf-8 -*-
"""
common/cardslot.py —— **卡带槽**: "当前活动的那一份配方"的中立取用入口(**机制**)。

为什么要有它(2026-09-11 从 profile.py 提出来)
---------------------------------------------
本仓有两盘**正交**的卡带, 换表与换机器互不相干:

    project/   "本表环境包"   —— 这块表的事实(表号 / 双芯AF / RAM 地址图 / .out 路径)
    machine/   "装机卡带"     —— 这台机器的事实(gdb / J-Link / 串口 / 工具链路径)

两者要的是**同一件事**: 「模块级常量 + 组合根把它装上去 + 谁都不许反向 import 那盘卡带」。
2026-09-11 之前这套机制只有 `common/profile.py` 一份(cmdbank/ez_meter/whitebox 靠它取表画像)。
加机器卡带时若照抄一份, 仓里就有了两套卡带槽 —— 而其中最难写对的两处(Proxy 的 dunder 语义、
"没卡带时报清楚的 RuntimeError 而不是返回 None")本来只该被写对一次。故提出来:
**`Slot` 是机制, `common/profile.py` 与 `common/machspec.py` 各持一个实例。**

本模块不 import meterlib / swdbg / project / machine 中任何一个, 也不含任何具体值 ——
配方(具体是哪块表、哪台机器)在调用方, 机制(怎么装、怎么取)在这里。

怎么避开"装配有时序"
--------------------
配方属性可能在**模块级**被读(如 meterlib 里 `ADDR_698 = bytes([P.MANAGE_AF]) + P.SERVER_TAIL`)。
若直接 `P = slot.current()` 在 import 期取值, 就要求**先 import 卡带再 import 引擎**, 极脆。
所以给的是 `Proxy`: 属性**每次访问现取**(见 `Slot.Proxy`)。

没装配时报什么
-------------
`current()` 抛 RuntimeError 并写清怎么装配, **不静默返回 None** —— 否则下游会拿到一串
莫名其妙的 AttributeError, 顺着往上找只会找到"None 没有这个属性", 找不到真因。
"""
import importlib
import os

__all__ = ["Slot", "resolve_in"]


def resolve_in(pdir, rel):
    """把卡带里声明的一条路径解析成绝对路径(**纯函数**, 不碰槽)。

    约定(与 `FRAMES_FILE`/`META_FILE`/`MASTER_DOC` 同款): 卡带里只写**相对名**, 目录由调用方
    给出 —— 于是**整盘卡带可以整体搬走**。绝对路径原样规整(给"东西在仓外"的场景留的口子)。

    `pdir` = 卡带所在包目录(通常是 `dirname(画像.__file__)`); `rel` 空 → None。

    为什么单列成纯函数(而不是只做 `Slot.resolve` 的方法): `project/firmware.py` 解析的是
    **meta.json 里的一个值**, 手上没有"槽", 只有"卡带目录 + 那条路径" —— 两处要的是同一个
    约定, 就该是同一份实现, 否则哪天约定一改, 只有一个地方跟着改。
    """
    if not rel:
        return None
    s = str(rel)
    if os.path.isabs(s):
        return os.path.normpath(s)
    return os.path.normpath(os.path.join(pdir, s))


class Slot(object):
    """一盘卡带的槽。

    `default_source` = `(包名, 属性名)`, 或 None = **本机没有默认卡带**(要用它的地方就该报错)。
    它是个**字符串**, 不是 import 语句 —— 这正是"卡带槽留在机器上, 而不是把某盘卡带焊进机器"。
    `label`/`howto` 只用来写报错的人话, 不影响行为。
    """

    def __init__(self, default_source, label="卡带", howto=None):
        self.obj = None                       # 当前装上的那份(模块对象)
        self.tried = False                    # 自举只试一次(失败也不反复重试)
        self.default_source = default_source
        self.label = label
        self.howto = howto or "组合根要先调 `configure(<卡带>.CURRENT)`"

    # ---- 装配方(组合根)的口 -------------------------------------------------
    def configure(self, mod):
        """装配方(组合根)调一次。传**模块对象**即可 —— 配方就是一堆模块级常量, 模块天然是命名空间。

        幂等: 重复调就是换一份配方(测试里想中途换表/换机器也能用)。传 None 表示清空(复位用)。
        **显式装配优先于自举**: 装配过之后 `bootstrap()` 就不再按默认卡带找了。
        """
        self.obj = mod
        self.tried = True

    def clear(self):
        """清空装配(复位用)。**连"自举过了"也一并复位**, 否则下次 current() 不会再去找卡带。"""
        self.obj = None
        self.tried = False

    def set_default_source(self, source):
        """换"默认卡带"。`source` = `(包名, 属性名)`, 或 None = **不要默认卡带**。

        给两种场合: ①那盘卡带的包不叫默认名; ②纯离线跑(比如只解析 .out), 压根没有配方,
        想让 `current()` 老实报"你没装配"而不是顺手拖一个包进来。
        """
        self.default_source = source

    def bootstrap(self, source=None):
        """按"默认卡带"自举一次(幂等): `import <包>; obj = <包>.<属性>`。

        **为什么放在中立层**: 引擎静态上只能 import `common.cardslot` —— 它不认识 "project" /
        "machine" 这些名字。名字作为**字符串默认值**住在各自槽的实例里, 换部署改一处即可。

        找不到卡带**不抛**: 本机可能压根没有那盘卡带(拿 swdbg 去指一块陌生表就是这种),
        这时候硬炸会把"用不着配方"的场合也一起弄死。真正用到时 `current()` 会给出清楚报错。
        """
        if self.obj is not None or self.tried:
            return self.obj
        self.tried = True
        src = self.default_source if source is None else source
        if not src:
            return None
        pkg, attr = src
        try:
            self.obj = getattr(importlib.import_module(pkg), attr)
        except Exception:
            self.obj = None               # 没有卡带/卡带坏了 —— 留给 current() 报, 别在这里炸
        return self.obj

    def current(self):
        """当前配方模块。没装配就先按默认卡带自举一次; 还是没有就抛 —— 见文件头。"""
        if self.obj is None:
            self.bootstrap()
        if self.obj is None:
            if self.default_source:
                pkg = self.default_source[0]
                tail = ("  常规情况下 `import %s` 会自己装上(%s/__init__.py 里那句 configure),\n"
                        "  或者默认卡带 %r 存在时本槽会自动装上。\n"
                        "  若你看到这条, 多半是: ①本机没有 %s 包(不是故意的 —— 故意的不装卡带见下);\n"
                        % (pkg, pkg, self.default_source, pkg))
            else:
                tail = ("  本机被声明为**没有默认卡带**(`set_default_source(None)`) —— 若这不是你想要的,\n"
                        "  把默认卡带设回来, 或由组合根显式 configure 一份。\n")
            raise RuntimeError(
                "%s未装配：%s\n" % (self.label, self.howto) + tail +
                "  ②clear() 之后没再装配; ③在模块级直接用了 current()(该用 Proxy())。")
        return self.obj

    def Proxy(self):
        """`from project import CURRENT as P` 这类写法的**替身**: `P = slot.Proxy()`。

        之后原有的 `P.MANAGE_AF` / `P.GDB` 一字不改就能用 —— 这是把引擎从卡带上摘下来时
        **改动面最小**的写法, 也刻意保住了模块级那句 `ADDR_698 = bytes([P.MANAGE_AF]) + ...`。

        为什么是代理而不是 `obj = current()`: 后者在 import 期就把配方**冻住**, 之后
        `configure()` 换卡带, 模块级那份快照不跟着变。代理每次访问现取, 于是"换卡带"这件事
        对模块级与函数级一视同仁 —— 换一份配方即刻生效, 不必重新 import。
        """
        return _Proxy(self)

    # ---- 取用方的口 ---------------------------------------------------------
    def resolve(self, name, default=None):
        """卡带声明的属性 `name` 是一条路径 → 绝对路径(相对**卡带包目录**解析)。

        为什么要有这个口(2026-09-14): 通用层原先直接硬编码了表私有文件的位置
        (`watch_runner` 的 `project/knowledge/cases71.json`、`discover` 的 `project/knowledge/探测报告/` 与 `project/knowledge/对表操作总纲.md`)——
        "库一概不认表" 因此漏了个口子: 库不认表的**数据**, 却认了表的**文件布局**。
        现在改成**路径由卡带声明, 通用层只解析**(`profile.resolve("CASES_FILE")`)。

        取不到该属性/未装配 → `default`(不抛); 声明了但盘上没有 → **照拼不误** ——
        "在不在盘上"是调用方的事, 本方法只做"拼路"这一件事。
        """
        rel = self.get(name, None)
        if not rel:
            return default
        f = getattr(self.obj, "__file__", None)
        pdir = os.path.dirname(os.path.abspath(f)) if f else os.getcwd()
        return resolve_in(pdir, rel)

    def has(self, name):
        """配方里有没有这个属性(不触发 AttributeError)。"""
        self.bootstrap()
        return self.obj is not None and hasattr(self.obj, name)

    def get(self, name, default=None):
        """配方属性 → 值; 没有/未装配 → default。**可选属性**用这个, 别用 current()。"""
        self.bootstrap()
        if self.obj is None:
            return default
        return getattr(self.obj, name, default)

    def need(self, *names):
        """要一串**必备**属性 → 值元组。缺任何一个就抛, 并把**缺了哪几个**写清楚。

        给那些"少了就根本没法干活"的属性用(机器卡带的 GDB / JLINK_SN …)。好处是**装配期就炸**,
        而不是跑到一半某个角落才 AttributeError。
        """
        p = self.current()
        missing = [n for n in names if not hasattr(p, n)]
        if missing:
            raise RuntimeError(
                "%s %r 缺少必备属性: %s\n"
                "  要么这份配方不完整, 要么本模块要的东西它就没打算提供 —— 两件事都得人看。"
                % (self.label, getattr(p, "__name__", p), ", ".join(missing)))
        return tuple(getattr(p, n) for n in names)

    def require(self, *names):
        """取一批**必备**属性 → 值元组。与 `need()` 的唯一区别: **属性值为 None 也算没配好**。

        为什么单开一个(而不是把 need 改严): `need()` 判的是"这盘卡带**声明没声明**这个字段";
        而机器卡带上 `JLINK_SN = None` 是"声明了、但本机没填" —— 这两件事对不同调用方意义不同。
        对 `Probe` 而言后者是要命的: 拿 None 去连 J-Link, pylink 会连上**随便一块探针**
        (serial_no=None 的语义就是"不挑"), 那是**静默连错**, 比当场报错坏得多。
        """
        self.bootstrap()
        p = self.current()
        bad = [n for n in names if getattr(p, n, None) is None]
        if bad:
            raise RuntimeError(
                "%s %r 这几项没配好(缺属性, 或值为 None = 声明了但本机没填): %s\n"
                "  机器条件住在本仓的装机卡带(machine/), 见 machine/env_check.py 逐条核对。"
                % (self.label, getattr(p, "__name__", p), ", ".join(bad)))
        return tuple(getattr(p, n) for n in names)

class _Proxy(object):
    """见 `Slot.Proxy()`。只持一个槽的引用, 所有属性访问现取。"""

    __slots__ = ("_slot",)

    def __init__(self, slot):
        self._slot = slot

    def __getattr__(self, name):
        # dunder 探测(copy/deepcopy/pickle/hasattr)不触发自举: 让 hasattr() 老实回答,
        # 也不为了一个探测把 RuntimeError 抛出来。`__file__` 是 dunder 但真有人用
        # (cmd_bank 拿它定位仓根), 所以已装配时照常给。
        if name.startswith("__") and name.endswith("__"):
            if self._slot.obj is None:
                raise AttributeError(name)
            return getattr(self._slot.obj, name)
        return getattr(self._slot.current(), name)

    def __repr__(self):
        return "<cardslot.Proxy -> %s>" % (
            getattr(self._slot.obj, "__name__", None) if self._slot.obj is not None else "(未装配)")
