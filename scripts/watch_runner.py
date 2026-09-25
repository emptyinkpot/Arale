# -*- coding: utf-8 -*-
"""
scripts/watch_runner.py —— AA80 用例执行器(project/knowledge/cases71.json 数据层 → 一键跑判过)

2026-09-18 从 `meterlib/` 搬到 `scripts/`。理由: 本文件是**入口工具**(有 `main(argv)`, 跑一次就完),
**全仓零个模块 import 它** —— 它不是库, 不该占 `meterlib` 的位子。`scripts/` 引 meterlib 有先例
(`_restore_all.py` 就是这么干的), 不越界。

把 71 项白盒测试里「可自动」的项(分类方案 A/B 桶, 见 project/knowledge/cases71.json meta)
数据化成 {banks 观察区 + readers 读回 + actions 触发步骤 + checks 判据},
由本执行器用 AA80(看内存)/协议读回(看应答)代替 IAR「断点+Watch」跑判.

心智映射(cmd_bank CASES/runcase 的 AA80 版):
    cmd_bank.run case(纯发帧判过)   -> 本 runner 再加: 动作前后 snapshot 冻结区/各镜像、
                                         触发前/后 读回记录(rec_advance)做落库证据.

运行位置: 帧收发基础/ 下。用例数据层的位置**由卡带声明**(`project/<表>.py` 的 `CASES_FILE`,
指向 `project/knowledge/cases71.json`)—— 2026-09-14 前写死在仓根, 那是共享引擎认了某块表的
文件布局, 已改正。目录断点定, 不依赖 cwd。
运行安全: 只有 `run` 开串口并真实触发(真钟/写入口类已标 low-reversal, 见 case.note);
`list` / `show` / `anchors` 只读数据层, 不开串口。

CLI(在 帧收发基础/ 下):
    python scripts/watch_runner.py list                       # ready/pending 一览
    python scripts/watch_runner.py show <case-id>             # 一条 case 全文
    python scripts/watch_runner.py run   <case-id> [--wait S] # 真实执行(开 COM3, 触发动作)
    python scripts/watch_runner.py anchors                    # 待打点断点清单(事件落库区等)
退出码(镜像 cmd_bank runcase): PASS=0 / FAIL=1 / TBD=2; 无此 case / 状态非 ready = 3。
"""
import argparse
import json
import os
import sys
import time

# ---- 仓根(向上找含 src/ 的那一级), **只用来定位文件** ----
_ROOT = os.path.dirname(os.path.abspath(__file__))
while not os.path.isdir(os.path.join(_ROOT, "src")) and os.path.dirname(_ROOT) != _ROOT:
    _ROOT = os.path.dirname(_ROOT)
from meterlib import cmd_bank                 # 语义动作 + 帧目录
from meterlib import watch                     # AA80 只读观察簇(WatchBank): 一条独立通路, 独立成文件
from common import profile              # 卡带取用入口: 用例数据层的位置由卡带声明


def _cases_path():
    """用例数据层(`project/knowledge/cases71.json`)的位置 —— **由卡带声明, 不硬编码**。

    2026-09-14 前这里写死 `仓根/project/knowledge/cases71.json`。那是个口子: 本模块是**共享引擎**(换表不改),
    却认了某块表的**文件布局** —— "库一概不认表" 于是只对了一半(不认表的数据, 认表的路径)。
    现在改走卡带:`project/<表>.py` 声明 `CASES_FILE`, 这里只解析。

    兜底保留旧布局(`仓根/project/knowledge/cases71.json`)—— 卡带没声明时(临时 checkout / 别的表族)不至于哑掉。
    """
    return profile.resolve("CASES_FILE") or os.path.join(_ROOT, "project/knowledge/cases71.json")


CASES71 = _cases_path()
_EMPTY_REC = {"verdict": "N/A", "reason": "未读(无此 reader)", "seq": None, "ts": None}
_EXIT = {"PASS": 0, "FAIL": 1, "TBD": 2}

# ============================ 数据层加载(project/knowledge/cases71.json) ============================
def load(path=None):
    path = path or CASES71
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def case_of(data, case_id):
    for c in data.get("cases", []):
        if c["id"] == case_id:
            return c
    return None


# ============================ 观察区/读回驱动(数据 -> 对象) ============================
def build_banks(data, names):
    """names: case.banks 里注册的观察区名 -> [(区名, WatchBank)]。区 points 符号定址由 WatchBank 折算偏移。"""
    reg = data.get("banks", {})
    out = []
    for n in names:
        spec = reg.get(n)
        if not spec:
            continue
        bk = watch.WatchBank(n)
        bk.add_specs(spec.get("points", []))
        out.append((n, bk))
    return out


def run_reader(data, ser, reader_spec):
    """records 注册表 -> 读回一行记录。现仅 freeze_row(698 GetRequestRecord); 事件类驱动待打点后扩充。"""
    reg = data.get("records", {})
    rec = reg.get(reader_spec["rec"])
    if not rec:
        return dict(_EMPTY_REC, verdict="N/A", reason="records 注册表无 %r(待登记)" % reader_spec["rec"])
    if rec.get("driver") != "freeze_row":
        return dict(_EMPTY_REC, verdict="N/A", reason="driver %r 未实现(待打点)" % rec.get("driver"))
    return cmd_bank.read_freeze_row(ser, int(rec["subclass"]), int(reader_spec.get("pos", 1)))


def dispatch_semantic(data, ser, act, wait):
    """语义动作: cmd_bank 现成函数名 + act 里携带的 kwargs -> (verdict, reason)。"""
    name = act["semantic"]
    fn = getattr(cmd_bank, name, None)
    if fn is None:
        return "FAIL", "语义 %r 不在 cmd_bank(无此函数)" % name
    if name == "settle_across_master":
        fn(ser, act.get("target"), timeout=act.get("timeout", 150),
           wait=wait, billday=act.get("billday", 5))
        return "PASS", "语义 settle_across_master 已执行(自打印) —— 结算日冻结判读看其上输出"
    if name == "read_clock":
        ts = cmd_bank.read_clock(ser, chip=act.get("chip"), wait=wait)
        return ("PASS" if ts else "FAIL"), ("读钟=%s" % ts if ts else "读钟无应答")
    return "FAIL", "语义 %r 尚无专门适配(需补 dispatch_semantic 分支)" % name


# ============================ 判据(checks) ============================
def _rec_changed(pre, post):
    """记录序号/时标是否前移(1B 序号会回绕, 用「不等即变」)。None=无记录。"""
    if post is None or post.get("seq") is None:
        return False
    if pre is None or pre.get("seq") is None:
        return True                      # 无 -> 有 = 落库
    return post["seq"] != pre["seq"] or post.get("ts") != pre.get("ts")


def _eval_check(op, pre_shots, post_shots, pre_recs, post_recs):
    """判一条 check op。pre/post_shots: {区名: {点: bytes|None}}; pre/post_recs: {记录名: read_freeze_row返回}。
    返回 (pass:bool, 说明)。"""
    if "bank_changed" in op:
        name = op["bank_changed"]
        pre, post = pre_shots.get(name), post_shots.get(name)
        if not pre or not post:
            return False, "bank_changed(%s) 缺快照(区在 case.banks? 或读全失败)" % name
        changed = {}
        for pname, a in pre.items():
            b = post.get(pname)
            if a is None or b is None:
                changed[pname] = (a is not None or b is not None, [])
                continue
            dd = [(i, a[i] if i < len(a) else 0, b[i] if i < len(b) else 0)
                  for i in range(max(len(a), len(b)))
                  if (i >= len(a) or i >= len(b) or a[i] != b[i])]
            changed[pname] = (bool(dd), dd)
        desc = "; ".join("%s%s" % (k, "(%d字节变)" % len(v[1]) if v[0] else "不变") for k, v in changed.items())
        return any(v[0] for v in changed.values()), "bank_changed(%s)  %s" % (name, desc)
    if "rec_advance" in op:
        rname = op["rec_advance"]
        pre, post = pre_recs.get(rname), post_recs.get(rname)
        ok = _rec_changed(pre, post)

        def t(x):
            return (x or {}).get("seq")
        return ok, "rec_advance(%s) 前置seq=%s -> 后置seq=%s%s" % (
            rname, t(pre), t(post), "" if ok else " (未前移)")
    return False, "未知 check op(仅 bank_changed / rec_advance)"


# ============================ 执行 ============================
def run(case_id, wait=3.0):
    """跑一条 case: 开串口, 按序触发动作, 前后快照 + 读回判过。"""
    data = load()
    case = case_of(data, case_id)
    if not case:
        print("无 case %r(用 list 看可用 id)" % case_id)
        return 3
    if case["status"] != "ready":
        print("== %s | %s ==\n状态=%s 未接线, 不可 run。挂起原因: %s\n(该类项去 anchors 看待打点清单)"
              % (case["id"], case["name"], case["status"], case.get("pending_reason", "无")))
        return 3
    name = "%s | %s" % (case["id"], case["name"])
    print("\n########## %s [class=%s] ##########" % (name, case["class"]))

    bks = build_banks(data, case.get("banks", []))
    readers_pre = [dict(r, name=r["rec"] + "@pre") for r in case.get("readers_pre", [])]
    readers_post = [dict(r, name=r["rec"] + "@post") for r in case.get("readers_post", [])]
    actions = case.get("actions", [])

    # ---- 真实执行(开 COM3 一次) ----
    ser = None
    try:
        from common.portsel import open_com
        ser = open_com()
        if case.get("factory"):
            v = cmd_bank.enter_factory(ser)
            print("前置 645.factory -> %s" % v)
        # 1) 动作前快照 + 前置读回
        pre_shots = {}
        for n, bk in bks:
            pre_shots[n] = bk.snapshot(ser, quiet=True, wait=wait)
            print("[观察前] %s: %s" % (n, watch.WatchBank.fmt_shot(pre_shots[n])))
        pre_recs = {}
        for rp in readers_pre:
            pre_recs[rp["rec"]] = run_reader(data, ser, rp)
        # 2) 动作步骤
        send_worst = "PASS"
        for a in actions:
            if "send" in a:
                res = cmd_bank.send(a["send"], wait=wait, ser_shared=ser)
                send_worst = _worse(send_worst, res["verdict"])
                if a.get("note"):
                    print("  注: %s" % a["note"])
            elif "wait_s" in a:
                time.sleep(float(a["wait_s"]))
                if a.get("note"):
                    print("  等 %ss: %s" % (a["wait_s"], a["note"]))
            elif "semantic" in a:
                v, why = dispatch_semantic(data, ser, a, wait)
                send_worst = _worse(send_worst, v)
                print("  语义 %s -> %s %s" % (a["semantic"], v, why))
            elif "note" in a:
                print("  (说明) %s" % a["note"])
        # 3) 动作后快照 + 后置读回
        post_shots = {}
        for n, bk in bks:
            post_shots[n] = bk.snapshot(ser, quiet=True, wait=wait)
            print("[观察后] %s: %s" % (n, watch.WatchBank.fmt_shot(post_shots[n])))
        post_recs = {}
        for rp in readers_post:
            post_recs[rp["rec"]] = run_reader(data, ser, rp)
        # 4) 判据
        checks = case.get("checks", [])
        if checks:
            allpass = True
            for op in checks:
                ok, why = _eval_check(op, pre_shots, post_shots, pre_recs, post_recs)
                allpass &= ok
                print("[判据] %-4s %s" % ("PASS" if ok else "FAIL", why))
            verdict = "PASS" if allpass else "FAIL"
        else:
            verdict = send_worst           # 无显式判据 -> 以动作步 verdict 汇总统
        print("----\nRESULT: %s\n判过口径: %s" % (verdict, case.get("pass", "")))
        return _EXIT[verdict]
    except Exception as e:
        print("run %s 异常: %r" % (case_id, e))
        return 1
    finally:
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass


def _worse(a, b):
    """verdict 汇总: FAIL > TBD > PASS(有任一更差取更差)。"""
    rank = {"PASS": 0, "TBD": 1, "FAIL": 2}
    return a if rank.get(a, 1) >= rank.get(b, 1) else b


# ============================ 待打点清单 ============================
def anchors_pending(data=None):
    data = data or load()
    lines = []
    for c in data.get("cases", []):
        if c["status"] != "ready":
            lines.append("CASE %-5s %-14s [%s] 欠: %s" % (
                c["id"], c["name"], c["class"], c.get("pending_reason", "")))
    for bn, bs in (data.get("banks") or {}).items():
        if not bs.get("points"):
            lines.append("BANK %-5s %-14s 观察区空: %s" % ("-", bn, bs.get("note", "待打点")))
    return lines


# ============================ CLI ============================
# 控制台 UTF-8 单点(原先本文件内联一份, 2026-09-10 收敛到 common/console.py;
# 别名保原调用点 `_ensure_utf8_stdout()` 一字不改)
from common.console import ensure_utf8_stdout


def _p(args):
    data = load()
    if args.cmd == "list":
        print("== cases71(自动化目标 27 = A5+B22) ==")
        for c in data.get("cases", []):
            print("  %-5s %-28s [%-7s][class %s] %s" % (
                c["id"], c["name"], c["status"], c["class"],
                "" if c["status"] == "ready" else "→ " + (c.get("pending_reason") or "")[:66]))
        print("\nX 不测/排除共 %d 条、16-3 特殊(整包升级)见 excluded/special 段(供开发复盘)"
              % len(data.get("excluded", [])))
    elif args.cmd == "show":
        c = case_of(data, args.id)
        if not c:
            print("无 case %r" % args.id)
            return 3
        print(json.dumps(c, ensure_ascii=False, indent=2))
    elif args.cmd == "run":
        return run(args.id, wait=args.wait)
    elif args.cmd == "anchors":
        print("== 待打点/待接线断点 ==")
        for ln in anchors_pending(data):
            print("  " + ln)
        print("\n打完点把 CASE.banks/readers 填上、状态置 ready。")
    return 0


def main(argv=None):
    ensure_utf8_stdout()
    ap = argparse.ArgumentParser(prog="watch_runner",
                                 description="cases71 AA80 用例执行器(实表执行)")
    ap.add_argument("cmd", choices=["list", "show", "run", "anchors"])
    ap.add_argument("id", nargs="?", help="case id(4-1/1-1/...)")
    ap.add_argument("--wait", type=float, default=3.0, help="每帧等待秒(默认3.0)")
    a = ap.parse_args(argv)
    if a.cmd in ("show", "run") and not a.id:
        ap.error("cmd %r 需要 <case-id>" % a.cmd)
    return _p(a)


if __name__ == "__main__":
    sys.exit(main())
