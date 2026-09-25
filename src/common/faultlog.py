# -*- coding: utf-8 -*-
"""common/faultlog.py —— **故障台账**: 一个故障一个稳定的码, 原话逐字, 跨跑次可查。

为什么要有它
------------
2026-09-20 白盒半边整条死掉, 事后能拿出来对质的东西只有 `.log` 里的一段打印, 而它答不了
四个问题:

  · 报错的多行消息只落了头一行和后段提示, `(原错误: …)` 那句**没进日志** —— 真错因到不了纸上;
  · "当时为什么没做断点"在 `log/<跑次>.jsonl` 里**一条事件都没有**(那趟只有 serial.tx/rx 与 verdict);
  · 半小时前白盒还好好的, 而没有任何一条记录写着"那一刻链路是什么状态";
  · 临时敲的裸 pylink / JLink.exe / pnputil 落在仓外, 没时刻、没编号、没进台账。

本模块把那四样补齐, 做法是**收编而不是另起**: 故障走 `common.events` 现有的事件流
(`kind="fault"`), 与那趟跑的事件同一个 `.jsonl`。不新开一路文件、不新起一套格式、不新增
一套行约定。

三条不能松的规矩
----------------
① **原话逐字。** `exc=` 取的是 `类型: 原文` 整串, 不截断、不折叠多行 —— 那天丢的就是这一句。
   调用方**不许**自己 `str(exc)[:80]`; 屏上要短句就从 `record()` 的返回值取, 那是另一回事。
② **一码一义。** 码表 `CODES` 是它的唯一家, 新增码只许加在这里, 不许在调用点拼字符串。
   盖不出码的故障**不编一个** —— 记 `code=None` + 异常类名, 台账里如实显示"未分类"。
③ **不许静默丢。** `events.emit` 没绑定时静默丢弃(那一层按自己的规矩计数, 见其模块头);
   故障不能这样 —— 没绑定就同时打一行到 stderr, 让人当场看见。

命令行入口
----------
    python -m common.faultlog --ledger        # 重扫所有 jsonl, 生成 log/故障台账.log
    python -m common.faultlog --show <跑次>    # 某跑次的故障, 原话逐字
    python -m common.faultlog --tail 20       # 最近 20 条(跨跑次)
    python -m common.faultlog --codes         # 打码表
    python -m common.faultlog --run <命令…>    # 跑一条**临时诊断命令**, 落成一条 diag 事件
"""
from __future__ import annotations

import argparse
import glob
import inspect
import json
import os
import subprocess
import sys

from common import events

__all__ = ["CODES", "SUBSYSTEMS", "record", "bench", "diag",
           "read_faults", "ledger", "main"]

# 故障码 —— **唯一家**。一个码一个意思, 不许有同义码; 新增只许加在这里。
# 只收"代码里真抛得出来"的那几种, **不预先造大全**: 造出来的码没人盖, 台账里只会多一列空槽,
# 而空槽看久了会让人以为"这类故障没发生过"。右列那句话是给人读的, 也是台账里"一句话"那一列。
CODES = {
    "PROBE-ENUM":    "探针枚举本身跑不了(JLinkARM.dll / 驱动层)",
    "PROBE-ABSENT":  "枚举得到, 但一支探针都没有",
    "PROBE-NODE":    "设备节点在, 而 Windows 报它有问题",
    "PROBE-LINK":    "探针枚举得到, 但连不上目标/读不到那颗核",
    "PROBE-SELECT":  "选不出该用哪一支探针",
    "GDB-SESSION":   "调试会话开不了",
    "GDB-COMMAND":   "MI 命令失败或超时",
    "SERIAL-PORT":   "串口选不出/打不开",
    "BENCH-MISSING": "台面缺件(降级)",
}

# 故障落在哪一路。与 `judge.degradation` 的"缺了什么"是两回事: 那条说台面, 这条说**坏了什么**。
SUBSYSTEMS = ("probe", "serial", "gdb", "target", "bench")

# 异常类名 → 码。**按名字映射, 不 import 那些类** —— `common/` 是零依赖中立层, 而 `swdbg.*`
# 是它的下游; 反过来 import 会把依赖方向拧反(与 `events` 不许 import `runlog` 是同一条)。
# 名字对不上(改了类名 / 是内建异常) ⇒ **不猜**, 记成未分类 —— 猜错一个码, 台账就会把两件
# 不同的事并成一栏, 而那正是这份台账存在的理由的反面。
_EXC_CODE = {
    "JLinkError": "PROBE-ENUM",
    "PnPError": "PROBE-NODE",
    "CmsisDapError": "PROBE-LINK",
    "JLinkProbeError": "PROBE-LINK",
    "ProbeError": "PROBE-LINK",
    "ProbeSelectError": "PROBE-SELECT",
    "GdbError": "GDB-SESSION",
    "PortselError": "SERIAL-PORT",
}

_BRIEF_MAX = 120


def _caller():
    """报这条故障的调用点 `文件:行号` —— **自动抓**, 不许调用方手填。

    手填的位置会写错、会在改动之后变成谎话, 而"谁报的"恰恰是复盘时要问的第一句。跳过本模块
    与 `events` 自己的帧, 取第一个真实调用点。
    """
    f = inspect.currentframe()
    if f is not None:
        f = f.f_back
    while f is not None:
        mod = f.f_globals.get("__name__", "")
        if mod != __name__ and not mod.startswith(__name__ + ".") and mod != "common.events":
            return "%s:%d" % (os.path.basename(f.f_code.co_filename), f.f_lineno)
        f = f.f_back
    return None


def _raised_at(exc):
    """异常**最后抛出的那一帧** `文件:行号`(顺着 traceback 走到头)。取不到 ⇒ None。"""
    tb = getattr(exc, "__traceback__", None)
    last = None
    while tb is not None:
        last = tb
        tb = tb.tb_next
    if last is None:
        return None
    return "%s:%d" % (os.path.basename(last.tb_frame.f_code.co_filename), last.tb_lineno)


def _brief(code, text):
    """给屏上看的**一行**短句(原话已经完整落进 jsonl 了)。没有原文 ⇒ 只给码。"""
    body = (text or "").strip().splitlines()
    head = body[0][:_BRIEF_MAX] if body else ""
    return "[%s] %s" % (code or "未分类", head)


def record(code=None, subsystem=None, exc=None, text=None, tried=None,
           snapshot=None, next_step=None, where=None):
    """记一条故障 → 返回**给屏上看的短句**(完整原话已落进 jsonl)。**永不抛异常。**

    `code`      故障码(取 `CODES` 的键); 不传 ⇒ 按 `exc` 的类名推(推不出 ⇒ 未分类)
    `subsystem` 哪一路(见 `SUBSYSTEMS`)
    `exc`       那个异常对象 —— **原话从这里取, 逐字**, 连同最后抛出的那一帧
    `text`      没有异常对象时给的原文(或要补一句现场话)
    `tried`     当场试过什么、结果如何。**这是止扯皮的那一栏**: 下次同一个症状再来, 先读它,
                不必把"是不是线的问题/要不要重新枚举"重新论证一遍
    `snapshot`  现场快照 dict(探针端+SN / 设备到达时间 / VTref / CPUID / 核 DHCSR…)
    `next_step` 下一步该跑的那条命令
    `where`     谁报的 `文件:行号`; 不传 ⇒ `_caller()` 自动抓

    ⚠ 本函数**不改动 stdout 时序、不阻塞**(`emit` 是一条 write + 一次 flush, 见其模块头)。
      故障记录发生在串口/触发流程里也不影响那一轮 —— 与 `emit` 同一笔账。
    """
    try:
        if code is None and exc is not None:
            code = _EXC_CODE.get(type(exc).__name__)
        fields = {"code": code,
                  "subsystem": subsystem,
                  "where": where or _caller(),
                  "exc_type": None if exc is None else type(exc).__name__}
        if exc is not None:
            # 规矩 ①: 整串, 不 strip 掉行、不截断。多行在 JSON 里会被转义成 \n, 一条仍是一行。
            fields["text"] = "%s: %s" % (type(exc).__name__, exc)
            at = _raised_at(exc)
            if at:
                fields["raised_at"] = at
        elif text is not None:
            fields["text"] = str(text)
        if tried:
            fields["tried"] = ([str(t) for t in tried]
                               if isinstance(tried, (list, tuple)) else [str(tried)])
        if snapshot:
            fields["snapshot"] = dict(snapshot)
        if next_step:
            fields["next_step"] = str(next_step)
        events.emit("fault", **fields)
        if not events.enabled():            # 规矩 ③
            sys.stderr.write("!! 故障(未入账: 没有事件文件) %s\n" % _brief(code, fields.get("text")))
        return _brief(code, fields.get("text"))
    except Exception:                    # 记故障这件事本身不许把正在跑的测试搞崩(同 emit ②)
        return None


def bench(**fields):
    """记一条**链路体检** → `kind="bench"`。**每次实跑的头尾各一条**, 答的是"那一刻通不通"。

    没有这一条, 「刚才还通」就只能靠嘴说 —— 2026-09-20 白盒从 11:35:37 还能跑到 11:42:46
    全线连不上, 中间没有人碰过硬件, 而当时没有任何一条记录能作证。
    字段随现场给(探针端+SN / 设备到达时间 / CPUID / VTref / 核 DHCSR / 串口), 不设死 schema:
    不同入口当时手上有的事实不一样, 硬凑齐会逼着人写假值。
    """
    events.emit("bench", **fields)


def diag(argv, rc, out=None, launched=True):
    """记一条**临时诊断动作** → `kind="diag"`: 命令、有没有跑起来、退出码、完整输出。

    收的是"为了查故障临时敲的那一下"(裸 pylink / JLink.exe 脚本 / pnputil)。它们原先落在仓外,
    没时刻、没编号, 事后翻不回来 —— 而"当时到底试过什么"正是复盘时最容易被各说各话的一段。

    ⚠ `rc` **必填, 不给默认值**: 命令跑完了, 退出码是手上就有的事实。给它一个 `None` 默认值,
      会造出一个看着像"起不来"的坑 —— 那是两件不同的事(一个说表没应答, 一个说命令名打错了),
      在台账里长得一模一样。所以: `launched=True` 必须给 `rc`; 命令压根没启动才是
      `launched=False`, 那时**没有** rc 这个字段, 不拿 `rc=None` 冒充一个退出码。
    """
    fields = {"argv": [str(a) for a in argv], "launched": bool(launched)}
    if rc is not None:
        fields["rc"] = int(rc)
    if out is not None:
        fields["out"] = str(out)
    events.emit("diag", **fields)


def read_faults(logdir=None, run=None):
    """扫 `log/*.jsonl` 取出所有 `kind="fault"` → `([记录…], [读不动的…])`。

    `run` = 只看某一跑次(文件主干名, 如 `4_6_aa80_20260920_151830`); 不传 = 全扫。
    每条记录会被补一个 `run` 键(它是哪个跑次的)。
    ⚠ 读不动的行**不静默跳**: 归到第二个返回值里报出来 —— "读不出来"与"那里没有故障"必须
      分得开, 否则一份残缺的台账看起来和一份干净的台账一模一样。
    """
    from common import runlog
    d = logdir or runlog.default_logdir()
    files = sorted(glob.glob(os.path.join(d, (run + ".jsonl") if run else "*.jsonl")))
    out, bad = [], []
    for p in files:
        rid = os.path.splitext(os.path.basename(p))[0]
        try:
            fh = open(p, encoding="utf-8", errors="replace")
        except Exception as exc:
            bad.append("%s: 打不开(%s)" % (rid, exc))
            continue
        with fh:
            for i, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    bad.append("%s 第 %d 行不是 JSON" % (rid, i))
                    continue
                if rec.get("kind") == "fault":
                    rec["run"] = rid
                    out.append(rec)
    return out, bad


def _one_line(rec):
    """一条故障压成台账里的一行摘要(原话只取头一行, 全文仍在它自己的 jsonl 里)。"""
    text = (rec.get("text") or "").strip().splitlines()
    return "%s @ %s  %s" % (rec.get("clock") or "—", rec.get("where") or "—",
                            text[0] if text else "(没有原文)")


def ledger(logdir=None, path=None):
    """把所有跑次的 `kind="fault"` 汇总成 `log/故障台账.log` → `(落点, 条数, 读不动的)`。

    ⚠ **生成物, 每次整份重写**, 不是追加 —— 追加会把同一个故障按跑次重复计一遍, 而台账要回答的
      恰恰是"这个病见过几回"。原始记录永远在各自的 `<跑次>.jsonl` 里, 一行都不动(那一层天然
      是 append-only: 一个跑次一个文件)。文件头写明这一条, 免得有人手改它。
    """
    from common import runlog
    from common import judge
    d = logdir or runlog.default_logdir()
    recs, bad = read_faults(logdir=d)
    by_code = {}
    for r in recs:
        by_code.setdefault(r.get("code"), []).append(r)

    lines = []
    lines.append("==== 故障台账 ====")
    lines.append("本文件是**生成物**: python -m common.faultlog --ledger 每次整份重写, 别手改。")
    lines.append("原始记录在各跑次的 log/<跑次>.jsonl 里(kind=\"fault\"), 一行都没动过。")
    lines.append("故障 %d 条, 涉及跑次 %d 个, 分类 %d 种%s"
                 % (len(recs), len(set(r.get("run") for r in recs)), len(by_code),
                    ("; 读不动的 %d 条(见文末)" % len(bad)) if bad else ""))
    lines.append("")
    # 见过的排前面(按条数), 没见过的码不占位置 —— 空栏会让人以为"这类没发生过"
    for code in sorted(by_code, key=lambda c: (-len(by_code[c]), str(c))):
        group = by_code[code]
        runs = sorted(set(r.get("run") for r in group))
        lines.append("-- %s  %d 条" % (code or "未分类", len(group)))
        # ⚠ 两种"码表里查不到"要分开说, 它们处置相反:
        #   code is None  = **按规矩没编码**(异常类名不在 `_EXC_CODE` 里), 补码表即可;
        #   有码却查不到  = 调用点自己拼的字符串, 那才是必须收回 CODES 的错。
        #   混成一句话的话, 前者会被当成后者挨一顿骂, 而它本来是对的。
        if code is None:
            lines.append("   码表: 未分类 —— 异常类名不在码表里, 不是调用点拼的码; "
                         "要么补进 `_EXC_CODE`, 要么这一笔本来就盖不出码")
        else:
            lines.append("   码表: %s" % CODES.get(
                code, "**码表里没有这个码** —— 调用点自己拼的, 该收回 CODES"))
        lines.append("   跑次: %s" % (", ".join(runs) if runs else "—"))
        subs = sorted(set(r.get("subsystem") for r in group if r.get("subsystem")))
        if subs:
            lines.append("   哪一路: %s" % ", ".join(subs))
        for r in group[-3:]:
            lines.append("   · %s" % _one_line(r))
            if r.get("next_step"):
                lines.append("     下一步: %s" % r["next_step"])
        if len(group) > 3:
            lines.append("   (只列最近 3 条; 全在这几个跑次的 jsonl 里)")
        lines.append("")

    if bad:
        lines.append("-- 读不动的(台账残缺, 不等于那里没有故障)")
        for b in bad:
            lines.append("   · %s" % b)
        lines.append("")

    lines.append("判据口径与各跑次结论无关: 本台账只记**坏了什么**, 跑次过不过看 `kind=\"verdict\"`。")
    lines.append("三态词表: %s / %s / %s" % (judge.STATUS_PASS, judge.STATUS_FAIL, judge.STATUS_TBD))

    p = path or os.path.join(d, "故障台账.log")
    with open(p, "w", encoding="utf-8", errors="replace") as fh:
        fh.write("\n".join(lines) + "\n")
    return p, len(recs), bad


def _print_faults(recs, title):
    print("== %s ==" % title)
    if not recs:
        print("   (一条都没有)")
        return 0
    for r in recs:
        print("[%s] %s  %s" % (r.get("code") or "未分类", r.get("run") or "—",
                               r.get("clock") or "—"))
        print("   报自: %s%s" % (r.get("where") or "—",
                                 ("  抛自: %s" % r["raised_at"]) if r.get("raised_at") else ""))
        for line in (r.get("text") or "(没有原文)").splitlines():
            print("   原话: %s" % line)
        for t in (r.get("tried") or []):
            print("   已试: %s" % t)
        if r.get("snapshot"):
            print("   现场: %s" % json.dumps(r["snapshot"], ensure_ascii=False))
        if r.get("next_step"):
            print("   下一步: %s" % r["next_step"])
        print("")
    return 0


def _resolve_exe(tok):
    """把命令的第一个词换成**真起得来**的路径。

    为什么非要有这一步: Windows 的 `subprocess` 拿**相对路径 + 正斜杠**会直接
    `FileNotFoundError`(2026-09-20 实测: `.venv/Scripts/python.exe` 起不来, 同一串换成绝对路径
    就起来了), 而本仓的命令多数是在 Git Bash 里手敲的 —— 那种写法正是"相对路径 + 正斜杠"。
    不修的话 `--run` 报的是"系统找不到指定的文件", 那个错看着像"命令不存在", 会把人支去查
    命令名或 PATH, 而问题在路径的写法上。
    """
    if ("/" in tok or os.sep in tok):
        p = os.path.abspath(tok)
        if os.path.exists(p):
            return p
    return tok


def _run(argv):
    """跑一条临时诊断命令, 落成一条 `diag` 事件(连同这条命令自己的 .log / .jsonl)。

    ⚠ **不给它套超时**: 本仓的硬件命令可能握着 J-Link, 而"超时就杀掉"正是把探针撂坏的已知
      成因(见 CLAUDE.md 调试链那条铁律)。要限时就传工具**自己**的超时参数。
    """
    from common import runlog
    argv = [_resolve_exe(argv[0])] + list(argv[1:])
    name = "diag_%s" % (os.path.splitext(os.path.basename(argv[0]))[0] or "cmd")
    with runlog.run(name) as log_path:
        print("$ %s" % " ".join(argv))
        try:
            p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            out = (p.stdout or b"").decode("utf-8", "replace")
            rc = p.returncode
        except Exception as exc:
            out, rc = "跑不起来: %s: %s" % (type(exc).__name__, exc), None
        sys.stdout.write(out)
        if out and not out.endswith("\n"):   # 免得"LOG 已存"粘在被诊断命令的最后一行后面
            sys.stdout.write("\n")
        diag(argv, rc=rc, out=out, launched=rc is not None)
        print("LOG 已存: %s" % log_path)
        return 0 if rc == 0 else 2


def main(argv=None):
    from common.console import ensure_utf8_stdout
    ensure_utf8_stdout()
    ap = argparse.ArgumentParser(
        prog="python -m common.faultlog",
        description="故障台账: 一个故障一个码, 原话逐字, 跨跑次可查")
    ap.add_argument("--ledger", action="store_true", help="重扫所有 jsonl, 生成 log/故障台账.log")
    ap.add_argument("--show", metavar="跑次", help="某跑次的故障(文件主干名)")
    ap.add_argument("--tail", type=int, metavar="N", help="最近 N 条(跨跑次)")
    ap.add_argument("--codes", action="store_true", help="打码表")
    ap.add_argument("--run", nargs=argparse.REMAINDER, metavar="命令",
                    help="跑一条临时诊断命令并把它入账")
    a = ap.parse_args(argv)

    if a.codes:
        for c in sorted(CODES):
            print("%-14s %s" % (c, CODES[c]))
        return 0
    if a.ledger:
        p, n, bad = ledger()
        print("故障台账已生成: %s  (%d 条%s)" % (p, n, (", 读不动的 %d 条" % len(bad)) if bad else ""))
        return 0
    if a.show:
        recs, bad = read_faults(run=a.show)
        _print_faults(recs, "跑次 %s 的故障" % a.show)
        for b in bad:
            print("⚠ 读不动的: %s" % b)
        return 0
    if a.tail is not None:
        recs, _ = read_faults()
        _print_faults(recs[-a.tail:], "最近 %d 条故障" % a.tail)
        return 0
    if a.run is not None:
        if not a.run:                    # `--run` 后面空着 → REMAINDER 给的是 []
            ap.error("--run 后面要跟命令")
        return _run(a.run)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
