# -*- coding: utf-8 -*-
"""
common/snapdiff.py —— 两份字节快照逐块求差(纯函数, 零依赖)

原先叫 meterlib.whitebox.aa80_snap_diff, 住在协议/白盒层 —— 但**它跟 AA80、跟串口、
跟画像都没有任何关系**: 输入是 {名: bytes|None} 与 [(名,地址,长)], 输出是 bool。
它只是被 AA80 快照顺手用了, 于是一直赖在那边, 逼得 SWD 包去 import 串口模块。
2026-09-10 迁到中立层。

留 `aa80_snap_diff` 别名: 旧调用点一字不改。新代码请用 `snap_diff`
(串口那一路的调用点在 `meterlib/watch.py`, 以 `watch.aa80_snap_diff` 的名字可用)。
"""

__all__ = ["snap_diff", "aa80_snap_diff"]


def snap_diff(pre, post, blocks):
    """两快照逐块字节 diff → changed(任意一块有字节变). 打印差异; 单块前/后读失败则跳过该块. 佐证用, 不作检查."""
    changed_any = False
    for name, _, _ in blocks:
        a, b = pre.get(name), post.get(name)
        if a is None or b is None:
            print("   %-22s 前后读取失败, 无法 diff" % name)
            continue
        if a == b:
            print("   %-22s diff: 无变化" % name)
            continue
        changed_any = True
        diffs = [(i, a[i] if i < len(a) else 0, b[i] if i < len(b) else 0)
                 for i in range(max(len(a), len(b))) if (i >= len(a) or i >= len(b) or a[i] != b[i])]
        print("   %-22s diff: 有变化(%d字节) 例 %s" % (
            name, len(diffs), " ".join("@%X:%02X→%02X" % d for d in diffs[:6])))
    return changed_any


aa80_snap_diff = snap_diff      # 旧名别名(历史调用点兼容), 逐步弃用
