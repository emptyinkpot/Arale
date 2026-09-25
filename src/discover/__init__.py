# -*- coding: utf-8 -*-
"""
discover —— 通用探测包: 从 `.out` + 源码 + 总纲, 探出一块**陌生表**的内部结构, 产出一份描述文件。

它要解决什么(用户原话)
-----------------------
    "任何的表都能用这个来探测所有的内部结构, 我这个程序不是专门为某个表设计的,
      只有 project/ 是专门配合来适应这个表的。"

所以本包**一律不认表**: 不 import project、不读 CURRENT、不碰任何画像。喂什么 `.out` 就探什么表。
`project/` 是**产物之外**的适配层 —— 人(和 AI)拿本包产出的报告, 自己去写那块表的调试固件/画像。
**本包不生成画像、不写 project/*.py、不改 CURRENT**(用户的边界: "我不是让你直接生成脚本")。

输入 → 产物
-----------
    输入: <表>.out   +  固件源码目录  +  project/knowledge/对表操作总纲.md
    产物: project/knowledge/探测报告/<表名>_探测.md        ← 就这一份, 给人读的

一句话流水线:
    elf.py      .out → 符号(名字/地址/大小) + DWARF(类型/volatile)
    source.py   源码 → 每个符号被谁读写的 文件:行号(**只留指针, 不留源码**)
    doc.py      总纲 → DI 码 / OAD 对象 / 厂内前置 / 语义动词
    evidence.py 活体 → 借 swdbg 采样, 判「活 / 静止」(离线则这列空着)
    dossier.py  合成 → 上面四路并成一份带**逐字段证据与置信度**的报告
    scan.py     **找目标** → 一个目录 → 认出有几块表、各喂哪四样输入(批量入口用; 纯路径层)

⚠ 外泄硬约束(CLAUDE.md: 内容属厂商固件白盒反推, 外泄敏感)
---------------------------------------------------------
**证据可以引用 `文件:行号`, 但不能把源码内容抄进仓库。** 报告与一切中间产物里
**只允许出现符号名、地址、类型、行号指针** —— 不许出现源码片段、不许出现反汇编。
**没有离线断言可跑了, 这条靠出报前逐行核。** 本包不提供离线/模拟入口: 任何结论只能来自实物。

⚠ 与 swdbg 的关系
-----------------
本包**下游依赖** `swdbg`(只读采样通路)与 `common`(elfsym/snapdiff)。反过来不成立:
swdbg 与 common 都不知道 discover 的存在。分层单向, 别倒过来。

⚠ 报告里可以有"候选角色", 但**必须明标为推测并附依据**(§3); 归不了的、解释不了的,
一律进 §7「开放问题 / 待判断」—— 那一节是这份文件的**灵魂**, 是下次开会话建画像的任务清单。
最忌讳的是把"猜的"写成"探到的"。

用法
----
    python scripts/_init_meter.py --out <路径.out> --src <源码目录> --doc <总纲.md> [--live]
    python scripts/_probe_all.py  <目录|.ewp|.out> [<...>]     # 批量: 一镜像一份报告 + 索引.md

模块按需加载(PEP 562): 若在此处 import 子模块, `python -m discover.<子模块>` 会因"模块已在
sys.modules 里"打 RuntimeWarning, 而那个用法正是本包的主要用法。
"""
import importlib

# 名字 → 子模块。对外可 `from discover import build_report`, 但只有真用到才去 import 那个子模块。
_LAZY = {
    # elf.py —— .out 符号 + DWARF 类型层(名字须与 elf.__all__ 对得上)
    "symbols": "elf", "Symbol": "elf", "dwarf": "elf", "describe": "elf",
    "cross_check": "elf", "sections": "elf", "sha256": "elf",
    # source.py —— 符号 ←→ 源码 文件:行号
    "index": "source", "usage": "source",
    # doc.py —— 总纲抽取
    "extract": "doc",
    # evidence.py —— 活体采样
    "classify": "evidence",
    # dossier.py —— 合成报告
    "build": "dossier", "render": "dossier", "write_report": "dossier",
    # scan.py —— 找目标(批量入口用: 一个目录 → 要探哪几块表、各喂哪四样输入)
    # ⚠⚠ **刻意不登记 "scan" 这个名字**(与 source/elf/dossier 同一约定):
    #    这张表是 name→module 映射, 一旦写上 "scan": "scan", `from discover import scan as SC`
    #    拿到的就是**函数** scan 而不是**模块** scan, 于是 `SC.resolve_ewp_rel` 当场 AttributeError。
    #    踩过(2026-09-10): 加完这条, 当时的离线自检立刻红 —— 而"每个名字都解析得出来"
    #    那条守判定**查不出来**, 它只验可解析、不验遮蔽。子模块就得靠 import 系统自己给。
    # ⚠ 也别加 "describe" —— 已被 elf.describe 占用, 撞名会**静默改掉** elf 那条的行为。
    "Target": "scan", "TargetResult": "scan",
    "check_plan": "scan", "describe_target": "scan", "render_index": "scan",
    "write_index": "scan", "default_doc": "scan", "last_skipped": "scan",
}

__all__ = list(_LAZY)


def __getattr__(name):
    mod = _LAZY.get(name)
    if mod is None:
        raise AttributeError("module 'discover' has no attribute %r" % name)
    m = importlib.import_module("discover.%s" % mod)
    # 回退到模块本身, 但**只对"名字 = 模块名"那一条**(本表眼下没有这种名字)。其余名字对不上
    # 就照抛 AttributeError —— 用 `getattr(m, name, m)` 一把兜住会把**拼错的名字**变成
    # "静默返回模块", 那比直接报错坏得多。
    # (踩过: `_LAZY` 里曾写 "build_report", 而 dossier 里只有 `build`, 取属性时才炸。
    #  改 `_LAZY` 时请核对子模块的 `__all__`。)
    value = getattr(m, name, None)
    if value is None:
        if mod != name:
            raise AttributeError("discover.%s has no attribute %r (_LAZY 写错了?)"
                                 % (mod, name))
        value = m
    globals()[name] = value            # 缓存, 后续访问不再走 __getattr__
    return value


def __dir__():
    return sorted(__all__)
