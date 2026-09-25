# -*- coding: utf-8 -*-
"""
common/ —— 中立层: 不属于任何一方的共用积木(不认协议, 不认画像, 不认 SWD)

为什么要有它
------------
meterlib(645/698/AA80 协议) 与 swdbg(J-Link SWD) 是两个平行通路, 但有几样东西**两边都要**,
且都不该由对方提供:

    elfsym      .out(ELF) 符号表读取 —— 纯文件解析, 与协议无关。原先住在 meterlib,
                纯属历史误会; 它一住那儿, SWD 包想按名定址就被迫依赖串口协议模块。
    snapdiff    两份字节快照求差 —— 纯函数(dict in / bool out), 与通路无关。
    varresolve  变量名 → (绝对地址, 长度) —— **两条通路共用的唯一解析实现**。
    console     控制台 UTF-8 —— 原来是 meterlib/console.py(2026-09-10 迁来)。同 elfsym 的历史误会:
                它零协议成分, 住在 meterlib 里的后果是 swdbg / discover 各自内联了 7 份副本。
    cli         脚本入口参数守卫 guard_argv —— 纯 argv 处理(2026-09-10 自 cmd_bank 迁来)。同因:
                它一住 L2, "不认表"的 _init_meter.py 想用它就得 import cmd_bank、连带拽进 project 画像。
    cardslot    **卡带槽**(2026-09-11): "当前活动的那一份配方"的**机制**。原先这套只有 profile.py
                一份实现(取表画像); 加机器卡带时若照抄一份, 仓里就有两套卡带槽 —— 而最难写对的
                两处(Proxy 的 dunder 语义、"没卡带时报清楚的 RuntimeError")只该被写对一次。
    profile     当前活动**表画像**的中立取用入口 = `cardslot.Slot` 的一个实例(2026-09-10 阶段二)。
                原先 cmd_bank / ez_meter / whitebox 各自 `from project import CURRENT as P` ——
                共享引擎反向依赖了每表环境包。现在它们只认识 common.profile, 画像由组合根
                (project/__init__.py)喂进来。名字本身是**字符串默认值**, 不是 import 语句。
    machspec    当前**装机卡带**(这台机器的事实: gdb / J-Link / 串口 / 工具链)的中立取用入口 = 另一个
                Slot 实例(2026-09-11)。与 profile **正交**: 换表换 profile, 换机器换 machspec。
                原先 breakpoint / probe / restore / _install_gdb 各抄一份"gdb 在哪、SN 多少",
                launch.json 里还有三份 —— 换一台电脑要满仓找。现在只认识 common.machspec。
    jsonc       读带注释的 JSON(`.vscode/launch.json` 那种)。两个 env_check 都要读它核对
                "卡带声明的值 == 盘上写的值", 所以解析与比较(`canon`)必须是**同一套**规则。
    runlog      运行日志 tee(2026-09-10 自 meterlib 迁来)。只 import datetime/os/sys/time,
                零协议零画像 —— 同 elfsym/console/cli 的判据。批量探测要一份完整运行日志,
                而 discover/ 不能为此 import meterlib。
    events      **事件流**(2026-09-17 立): 与人读日志并列的**第二路输出**, 写 `log/<名>.jsonl`。
                点：`runlog` 记的是"打印了什么", 本模块记的是"**发生**了什么" —— 复盘 5-2 那次失败时
                172 份日志答不出"几点停的/停在哪/watch 读到多少", 根因是 `halt` 这类事实**从未被当作
                一个事件**。住这里的理由同 runlog/elfsym: 三个通路(串口/AA80/SWD)都要记, 谁也不该
                为此依赖别人; 它本身只 import json/os/time(零协议零画像零硬件)。
                ⚠ 只跟 `runlog` 单向相连: **runlog → events**(借它的时刻串, 见 `runlog.stamp`),
                反向 import 即循环。
    hashfile    文件内容指纹 sha256(2026-09-10 自 discover/elf.py 提来)。**第六次同款搬迁**。
                动因是 project/env_check.py 要核"声明的 out_sha256 == 盘上 .out 实算",
                而 project/ 不许 import discover。**关键是不留第二份实现**: 报告 §0 的指纹
                与探测前核对用的指纹必须是同一份代码算的, 否则判定可能在报告记着另一个值时放行。
    winpnp      Windows 设备树: 查某个 VID 的节点在不在位 / 坏没坏 / 什么时候掉的, 以及
                (要管理员)重新枚举 / 重启 / 禁用启用设备节点。**第七次同款搬迁**, 动因是
                2026-09-17 探针从 USB 上掉了下来 —— 当时那几条 PowerShell 是临时敲的, 敲完就
                没了, 下次还得从头推一遍(并且很可能又推错)。它零协议零画像, 按本层判据就属这儿;
                住这儿的直接好处是装机卡带 `machine/env_check.py` 用得上它, 而那一包不许
                import swdbg。

依赖方向(这条是硬约束, 改 import 前先看)
---------------------------------------
        project(本表卡带)  ──┐
                             ├──→   common   ←──  meterlib
        machine(装机卡带)  ──┘        ↑       ←──  swdbg
                                     └────── ←──  scripts / discover

两盘卡带是**两个正交的轴**: `project/` 认"哪块表", `machine/` 认"哪台机器"。换表只换前者,
换机器只换后者 —— 共享引擎(meterlib/swdbg)对两者都不相识, 各自经 profile / machspec 取。
`cardslot` 是这两盘共用的**机制**(槽), 故它自己也住中立层。

`common` **不 import meterlib / swdbg / discover / project / machine 中的任何一个**。
它只向下吃标准库与 pyelftools。要画像自己喂进来(varresolve.configure), 要 .out 路径也自己喂
(elfsym.configure), 卡带名只是 `cardslot` 里的**字符串默认值** —— 配方(具体值)在调用方,
机制(怎么解析/怎么装/怎么报错)在这里。这是"依赖倒置"在本仓的落点, 两盘卡带各用一次。
"""
import importlib

_LAZY = {
    "elfsym": "elfsym",
    "snap_diff": "snapdiff",
    "aa80_snap_diff": "snapdiff",
    "resolve": "varresolve",
    "var_blocks": "varresolve",
    "configure_vars": "varresolve",
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
    """PEP 562 惰性再导出: `from common import elfsym` / `common.resolve` / `common.profile` 都可,
    且不触发 runpy 的 "found in sys.modules" 警告(照 swdbg/__init__.py 的样)。

    ⚠ 回退规则: `_LAZY` 的 value 是**模块名**, 默认要取该模块里**同名**的属性
    (`"resolve": "varresolve"` → `varresolve.resolve`)。但 `"profile": "profile"` 这种
    "名字就是模块名"的条目在模块里没有同名属性 —— 这时给**模块本身**。
    踩过: discover/__init__.py 起初没有这条回退, `_LAZY` 里两个名字直接 AttributeError。
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
