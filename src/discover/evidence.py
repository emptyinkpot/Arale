# -*- coding: utf-8 -*-
"""
discover/evidence.py —— 活体验证: 借 swdbg 采样, 判一个符号是「活」还是「静止」。

这一层回答的是报告 §4 那个问题: **谁在动、动多快、谁纹丝不动**。
它是 WATCH / STABLE 候选的**原料, 不是结论** —— "这个变量在动"是探到的事实;
"所以它该进 WATCH_VARS"是**判断**, 归 §3 的候选角色(且必须明标推测)。

为什么借 swdbg 而不是自己开 J-Link
-----------------------------------
swdbg 已经把"连上、按绝对地址读、干净退出、绝不停核"这四件事做对并实测过了
(三条安全铁律见 swdbg/probe.py)。本层复用它, 只做**采样策略与判定**这一件事。

采样策略(表在跑, 这点决定判据)
-------------------------------
一个块读 `rounds` 次、每次间隔 `gap` 秒, 看**几轮之间变了**:
    全同            → 静止(stable 候选的原料): 无显式命令它不动
    变了但没次次变   → 低频/偶动: 多半挂在校时/分钟/结算这类事件上
    次次都在变       → 高频: 计量采样量(AFE 每周期刷)、或秒级走表的钟
    读不到          → 读失败(不是"静止"! 不要把读失败当稳定态, 那是假通过)

⚠ 高频量可能**撕裂**: 多字节读不是原子的, 计数字段跨读会低字节已更新高字节还没
(swdbg.read_stable 用"连读到两次相同"缓解, 但持续在变的量本来就收敛不了)。
所以本层对"变了的"只报**变化次数**, 不报"值是多少"——值对动着的量没意义。

离线
----
不连表时本层根本不被调用; 报告里 §4 整节留空并注明"离线探测, 未采活体"。
**不许编** —— 没采就是没采。

用法
----
    from discover import evidence
    blocks = [("g_CurTime", 0x200034D0, 8), ...]        # 地址来自 elf.py 或画像
    got = evidence.classify(blocks, rounds=5, gap=0.5)   # 连表采样(只读, 不停核)
    python -m discover.evidence --out <某表.out> Frez     # 独立 CLU: 按名从 .out 定址再采
"""
from __future__ import print_function

import sys
import time


from swdbg.probe import Probe                        # 只读通路(绝不停核, 见其文件头铁律)

# 判定词。**"读失败"必须与"静止"分开** —— 把读不到当成稳定态是最典型的假通过。
STATIC = "静止"
SLOW = "低频"
FAST = "高频"
FAILED = "读失败"

__all__ = ["classify", "sample_once", "STATIC", "SLOW", "FAST", "FAILED"]


def sample_once(blocks, probe=None, tries=3, gap=0.02):
    """采一轮: blocks = [(name, 绝对addr, size)] → {name: bytes|None}。
    probe=None 就自己开一次会话(读完即断); 传 probe 供多轮复用同一会话(省 J-Link 开关开销)。"""
    if probe is not None:
        return {n: _read(probe, a, s, tries, gap) for n, a, s in blocks}
    with Probe() as pb:
        return {n: _read(pb, a, s, tries, gap) for n, a, s in blocks}


def _read(probe, addr, size, tries, gap):
    try:
        return probe.read_stable(addr, size, tries=tries, gap=gap)
    except Exception:
        return None                                      # 读不到记 None, 不中断其余


def classify(blocks, rounds=5, gap=0.5, probe=None, progress=None):
    """连读 `rounds` 轮判活/静止 → {name: verdict}。

        blocks = [(name, 绝对addr, size)]
        verdict = {"verdict": 静止|低频|高频|读失败,
                   "rounds": 实采轮数, "changed": 相邻两轮不同的次数, "distinct": 出现过几种值,
                   "first": bytes|None, "last": bytes|None}

    progress: 可选回调 progress(i, rounds), 供 CLI 打点(长采样时人能看到还在动)。
    表在跑, 全程只读、不停核(swdbg/probe.py 铁律 2)。"""
    blocks = list(blocks)
    rounds = max(1, int(rounds))
    seq = {n: [] for n, _, _ in blocks}

    for i in range(rounds):
        if progress:
            progress(i + 1, rounds)
        one = sample_once(blocks, probe=probe)
        for n, _, _ in blocks:
            seq[n].append(one.get(n))
        if i + 1 < rounds:
            time.sleep(max(0.0, float(gap)))

    out = {}
    for n, addr, size in blocks:
        vals = seq[n]
        got = [v for v in vals if v is not None]
        if not got:
            out[n] = {"verdict": FAILED, "rounds": rounds, "changed": 0, "distinct": 0,
                      "first": None, "last": None, "addr": addr, "size": size}
            continue
        changes = sum(1 for a, b in zip(vals, vals[1:]) if a != b)
        distinct = len(set(got))
        if changes == 0:
            verdict = STATIC
        elif changes >= rounds - 1:
            verdict = FAST
        else:
            verdict = SLOW
        out[n] = {"verdict": verdict, "rounds": rounds, "changed": changes,
                  "distinct": distinct, "first": got[0], "last": got[-1],
                  "addr": addr, "size": size}
    return out


def summarize(verdicts):
    """{name: verdict} → (计数 dict, 人类可读多行) 供报告 §4 直接用。"""
    tally = {}
    for v in verdicts.values():
        tally[v["verdict"]] = tally.get(v["verdict"], 0) + 1
    lines = []
    for name, v in sorted(verdicts.items(), key=lambda kv: (kv[1]["verdict"], kv[0])):
        val = "—" if v["last"] is None else v["last"].hex(" ").upper()
        lines.append("   %-28s %-6s 变%-3d 种%-3d 末值 %s"
                     % (name, v["verdict"], v["changed"], v["distinct"], val))
    return tally, lines


# ============================ 独立 CLI ============================
# 控制台 UTF-8 单点: 实现收在 common/console.py(2026-09-10 迁自 meterlib)。
# 本包只依赖 common(不是 meterlib), 故可直接引 —— 原先各内联一份的理由(它住在协议层)已消失。
# 别名保原调用点 `_ensure_utf8_stdout()` 一字不改。
from common.console import ensure_utf8_stdout


def main(argv=None):
    """python -m discover.evidence --out <某表.out> [--rounds N] [--gap S] [<name正则>]
    按名从 .out 定址(或 --addr)再真连表采样。只读, 不停核。"""
    ensure_utf8_stdout()
    import argparse
    ap = argparse.ArgumentParser(description="活体采样: 判符号是「活」还是「静止」(借 swdbg, 只读不停核)")
    ap.add_argument("--out", default="", help=".out 路径(按名定址用)")
    ap.add_argument("pattern", nargs="?", default="", help="只采名字匹配此正则的 RAM 对象")
    ap.add_argument("--rounds", type=int, default=5, help="采样轮数(默认 5)")
    ap.add_argument("--gap", type=float, default=0.5, help="轮间隔秒(默认 0.5)")
    ap.add_argument("--limit", type=int, default=0, help="最多采几个(0=不限; 几百个符号全采很慢)")
    args = ap.parse_args(argv)

    from discover import elf
    try:
        blocks = elf.symbols(out=args.out or None, pattern=args.pattern or None)
    except Exception as e:
        print("!! 取符号失败: %s" % e)
        return 2
    blocks = [(b.name, b.addr, b.size) for b in blocks]
    if args.limit:
        blocks = blocks[:args.limit]
    if not blocks:
        print("!! 没有符号可采(检查 --out / 正则)")
        return 2

    print("== 活体采样 %d 个符号 x %d 轮 (gap=%.2fs, 表在跑, 只读不停核) =="
          % (len(blocks), args.rounds, args.gap))
    try:
        got = classify(blocks, rounds=args.rounds, gap=args.gap,
                       progress=lambda i, n: print("   [%d/%d] 采样…" % (i, n)))
    except Exception as e:
        print("!! 连表失败(J-Link/表在不在?): %s" % e)
        return 2
    tally, lines = summarize(got)
    for ln in lines:
        print(ln)
    print("   -- 汇总: %s --" % "  ".join("%s=%d" % (k, v) for k, v in sorted(tally.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
