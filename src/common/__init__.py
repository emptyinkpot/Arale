# -*- coding: utf-8 -*-
"""
common/ —— 中立层: 不属于任何一方的共用积木(不认协议, 不认画像, 不认 SWD)

    snapdiff    两份字节快照求差 —— 纯函数(dict in / bool out)。
    console     控制台 UTF-8。
    cli         脚本入口参数守卫 guard_argv —— 纯 argv 处理。
    cardslot    **卡带槽**: "当前活动的那一份配方"的机制。
    profile     当前活动**表画像**的中立取用入口 = cardslot.Slot 的一个实例。
    machspec    当前**装机卡带**的中立取用入口 = 另一个 Slot 实例。
    jsonc       读带注释的 JSON(`.vscode/launch.json` 那种)。
    runlog      运行日志 tee, 写 `log/`。
    events      事件流, 写 `log/<名>.jsonl`。
    hashfile    文件内容指纹 sha256。
    winpnp      Windows 设备树: 查/动设备节点。

`common` **不 import meterlib / swdbg / discover / project / machine 中的任何一个**。
它只向下吃标准库与 pyelftools。卡带名只是 `cardslot` 里的**字符串默认值** —— 配方(具体值)在调用方。
"""
import importlib

_LAZY = {
    "snap_diff": "snapdiff",
    "aa80_snap_diff": "snapdiff",
    "ensure_utf8_stdout": "console",
    "guard_argv": "cli",
    "profile": "profile",          # 模块本身(名字 = 模块名, 见下面 __getattr__ 的回退)
    "machspec": "machspec",        # 同上; 用法 `from common import machspec` → machspec.get("GDB")
    "cardslot": "cardslot",        # 同上; 它是 profile/machspec 背后的**机制**(Slot)
    "jsonc": "jsonc",              # 同上; 用法 `from common import jsonc` → jsonc.load(p)
    "runlog": "runlog",            # 同上; 用法 `from common import runlog`
    "events": "events",            # 同上; 用法 `from common import events` → events.emit("halt", …)
    "hashfile": "hashfile",        # 同上; 用法 `from common import hashfile` → hashfile.sha256(p)
    "winpnp": "winpnp",            # 同上; Windows 设备树(查/动设备节点)。零协议零画像, 故住这层
}


def __getattr__(name):
    """PEP 562 惰性再导出: `from common import profile` / `common.machspec` 都可,
    且不触发 runpy 的 "found in sys.modules" 警告(照 swdbg/__init__.py 的样)。

    ⚠ 回退规则: `_LAZY` 的 value 是**模块名**, 默认要取该模块里**同名**的属性。
    但 `"profile": "profile"` 这种"名字就是模块名"的条目在模块里没有同名属性 —— 这时给**模块本身**。
    """
    mod = _LAZY.get(name)
    if mod is None:
        raise AttributeError("module 'common' has no attribute %r" % name)
    m = importlib.import_module("common.%s" % mod)
    value = getattr(m, name, None)
    if value is None:
        if mod != name:
            raise AttributeError("common.%s has no attribute %r (_LAZY 写错了?)" % (mod, name))
        value = m
    globals()[name] = value
    return value
