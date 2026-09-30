# -*- coding: utf-8 -*-
"""
scripts/_backfill.py —— 把**一次跑**的结论回填到总纲 §10.6, 并顺带核一遍地图。

它解决的重复
------------
每跑一个子项, 收尾都要手工做两件事: ①总纲 §10.6 加一行 ②`scripts/_check_readme.py` 核地图。
②是纯机械的, ①是抄一遍时间/项/结论 —— 本工具把②跑掉、把①的机械部分补上。

§10.6 那行里的「帧序列」「证据」两栏是**描述性**的, 本工具只写它能从日志里**读出来**的东西:
时间、项、结论状态与判据计数、日志文件名。别的留给人工补 —— 编不出来就不编。

用法(在仓根跑)
--------------
    python scripts/_backfill.py 5-8 --log log/5_8_clock_error_20260916_093938.log
    python scripts/_backfill.py 5-8 --log <该日志> --no-gates      # 只追加, 不跑地图检查
    python scripts/_backfill.py 5-8 --log <该日志> --print-only   # 一个文件都不动(含检查): 先看要写什么

幂等: 该日志文件名已在 §10.6 里出现过就**跳过追加**(再跑一百次也还是一行), 但检查照跑。
退出码: 0=没出问题; 1=检查红了; 2=参数/文件有问题(日志不存在、编号不在册子里、表结构没找到)。
"""
import os
import re
import subprocess
import sys

# ---- 仓根(向上找含 src/ 的那一级), **只用来定位文件** ----
_p = os.path.dirname(os.path.abspath(__file__))
while not os.path.isdir(os.path.join(_p, "src")) and os.path.dirname(_p) != _p:
    _p = os.path.dirname(_p)

from common.console import ensure_utf8_stdout       # noqa: E402
from common.runlog import strip_ts                  # noqa: E402
# 「这一份是不是一次实测」的**读法与判据**都借 `common.runlog`(scan + 它里面的 real_run_of),
# 本文件不再自留一份 —— 理由见 `_read_log()` 里那段 ⚠。这里只 import 那个读侧入口。
from common.runlog import scan          # noqa: E402
from common.runlog import proof_level               # noqa: E402


def _read_log(path):
    """把一份运行日志读成回填要用的那几样。**只认日志里真有的东西**, 读不到就是 None。"""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    # 日志**正文**自 2026-09-17 起带行首墙钟前缀(`[HH:MM:SS.mmm] `), 下面那些判据却全是**按行首**认的
    # (`串口: ` / `[串口] ` / `LOG 已存: ` / `== … 退出码: ` / 找到 `== … 本次范围` 那行 / `总:` / `观测:`)。
    # ⚠ 2026-09-17 实测: 不剥的话这几处**全部静默变成 None** —— 其中 `serial` 是"这份日志到底是不是一次实测"
    #   的一号凭据, 下游那道检查据此**拒收** ⇒ 后果是**每一份新日志都被当成干跑拒掉**, 而它不报错。
    # 剥前缀一律走 `common.runlog.strip_ts`(全仓唯一实现), 不在这里另写正则 —— 否则"写出来的格式"与
    # "读回来的格式"又会各改各的(本仓治过多次的那种)。`==== RUN`/`==== END` 两行本就不带前缀, 原样返回。
    lines = [strip_ts(ln)[1] for ln in lines]
    out = {"name": None, "ts": None, "title": None, "range": None, "saved": None,
           "verdict": None, "crit_line": None, "summary": [], "abnormal": None,
           # 这三样**不在本函数里读** —— 见循环之后那段。
           "serial": None, "legacy_serial": None, "real_run": None}

    for ln in lines:
        m = re.match(r"^==== RUN (\S+) @ (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", ln)
        if m:
            out["name"], out["ts"] = m.group(1), m.group(2)
        # 落盘那一行是**日志自己的绝对路径** —— 总纲里该写哪个相对路径以它为准, 不靠猜。
        m = re.match(r"^LOG 已存: (.+)$", ln)
        if m:
            out["saved"] = m.group(1).strip()
        m = re.match(r"^== \S+ 退出码: (\d+)\s+\(总: ([^)]*)\) ==$", ln)
        if m:
            out["verdict"] = "退出码 %s / 总: %s" % (m.group(1), m.group(2))
        m = re.match(r"^==== END .*异常: (\w+) ====", ln)
        if m:
            out["abnormal"] = m.group(1)

    # ---- 「这一份到底是不是一次实测」: 三样全**借** `common.runlog`, 本函数不留第二份实现 ----
    # 取凭据的本体是 `scan()`, 由凭据得三态的判据是 `real_run_of()`。这里只把结果抄过来。
    # ⚠ **为什么这两句非借不可**(2026-09-17): 原先本函数自己读那两句、自己判, 而判据是个
    #   **包含式**写法(`.find("真串口") >= 0`) —— 替身那行 `串口: **SilentSerial** ——
    #   这不是真串口, …` **否定句里含着同样四个字** ⇒ 干跑会被这道本该拒收它的检查**放行**。
    #   同一天 `common.runlog` 里也犯了一次同形的错(被它自己的自检当场红住)才认出这是同一个
    #   形状。各留一份的下场不是"多几行", 而是**把干跑当实测回填 = 伪造一条实测记录**。
    _sc = scan(path)
    out["serial"], out["legacy_serial"] = _sc["serial"], _sc["legacy_serial"]
    out["real_run"] = _sc["real_run"]

    # 判据汇总块: 标题行(带「本次范围」) 起, 到 END/空行为止。
    start = next((i for i, ln in enumerate(lines)
                  if ln.startswith("== ") and "本次范围" in ln), None)
    if start is not None:
        for ln in lines[start:]:
            if ln.startswith("==== END") or not ln.strip():
                break
            out["summary"].append(ln)
            s = ln.strip().lstrip("─").strip()
            if s.startswith("总:") or s.startswith("观测:"):
                out["crit_line"] = (out["crit_line"] or "") + s + " "
        head = lines[start]
        out["title"] = head.split("|")[0].replace("==", "").strip()
        if "本次范围:" in head:
            out["range"] = head.split("本次范围:", 1)[1].rstrip("= ").strip()
    return out


def _find_row_bounds(lines):
    """定位 §10.6 那张表的数据行区间 `(第一行下标, 最后一行下标+1, 表头下标)`。

    返回 `None` = 没找到 —— 那说明总纲结构变了。**这时停下报错, 不猜一个地方插进去**
    (插错位置 = 把一条实测记录混进别的章节)。"""
    head = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith("### 10.6"):
            head = i
            break
    if head is None:
        return None
    body = None
    for i in range(head, min(head + 40, len(lines))):
        if lines[i].lstrip().startswith("| 时间") and "项" in lines[i]:
            body = i
            break
    if body is None:
        return None
    j = body + 2                       # 跳过表头与分隔行
    while j < len(lines) and lines[j].startswith("|"):
        j += 1
    return body + 2, j, body


def _subitem_of(item):
    """编号 → 子项文字。从 `_suite.py` 的 `ITEMS` **现抠**(AST, 不 import —— 那是个脚本)。

    子项的唯一住处就是那份册子; 本工具不另抄一份。
    """
    import ast
    from common import profile
    try:
        p = os.path.join(profile.resolve("TESTS_DIR"), "_suite.py")
    except Exception:
        return None
    try:
        tree = ast.parse(open(p, encoding="utf-8").read())
    except (OSError, SyntaxError):
        return None
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", None) == "ITEMS":
            for e in n.value.elts:
                if ast.literal_eval(e.elts[0]) == item:
                    return ast.literal_eval(e.elts[2])
    return None


def _run_gates():
    """派生检查, 逐条真跑。返回 `[(名字, 退出码, 尾部输出), …]`。"""
    gates = []
    # ⚠ 它住 `scripts/`(仓级检查), **不在仓根** —— `_p` 是仓根, 拼成 `_p/_check_readme.py`
    #   会让 python 以"打不开文件"退出 2, 而 2 被当成"检查红了"报出来。2026-09-16 修:
    #   此前每次回填都白报一次"仓里真有不一致", 真红与拼错路径在汇总里长得一样(本仓反复治理的那类)。
    gates.append(("_check_readme.py",
                  [sys.executable, os.path.join(_p, "scripts", "_check_readme.py")]))
    out = []
    for nm, cmd in gates:
        _env = dict(os.environ, PYTHONIOENCODING="utf-8")
        r = subprocess.run(cmd, cwd=_p, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=_env)
        tail = "\n".join((r.stdout or "").rstrip().splitlines()[-6:])
        if not tail:
            # 红得**没有理由**比红更坏: 检查自己没跑起来(路径错/解释器错)那类, 话都在 stderr 上。
            tail = "\n".join((r.stderr or "").rstrip().splitlines()[-6:])
        out.append((nm, r.returncode, tail))
    return out


def _declared_run_names(item):
    """本子项的脚本**自己声明的**跑次名(`run_subitem(..., name="…")`) → ({名: 脚本文件名}, 脚本目录)。

    从源码 AST 抠, 不 import `project/tests/_suite.py`(它是个脚本, import 会把报表整个跑起来)。
    抠不到就返回空 dict —— **不猜**, 由调用处拒收。
    """
    import ast
    import glob
    from common import profile
    try:
        tests_dir = profile.resolve("TESTS_DIR")
    except Exception:
        return {}, "(TESTS_DIR 解析不出)"
    out = {}
    for f in sorted(glob.glob(os.path.join(tests_dir, "_test_%s*.py" % item.replace("-", "_")))):
        try:
            tree = ast.parse(open(f, encoding="utf-8").read())
        except Exception:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fname = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if fname != "run_subitem":
                continue
            for kw in node.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                    out[kw.value.value] = os.path.basename(f)
    return out, tests_dir


def main(argv):
    ensure_utf8_stdout()
    item = log = None
    no_gates = print_only = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--log" and i + 1 < len(argv):
            i += 1
            log = argv[i]
        elif a == "--no-gates":
            no_gates = True
        elif a == "--print-only":
            print_only = True
        elif a in ("-h", "--help"):
            print(__doc__)
            return 0
        elif a.startswith("-"):
            print("!! 未识别参数 %r(只认 <编号> --log <路径> --no-gates --print-only)" % a)
            return 2
        elif item is None:
            item = a
        else:
            print("!! 多给了一个位置参数 %r" % a)
            return 2
        i += 1
    if not item or not log:
        print("!! 用法: python scripts/_backfill.py <编号> --log <日志路径> [--no-gates|--print-only]")
        return 2
    if not os.path.isfile(log):
        print("!! 日志不存在: %s" % log)
        return 2

    from common import profile
    doc = profile.resolve("MASTER_DOC")
    logbase = os.path.basename(log)

    info = _read_log(log)
    if info["name"] is None:
        print("!! 这份日志里没有 `==== RUN …` 头 —— 它不是走 `run_subitem` 跑出来的日志, "
              "本工具认不出它的时间/项/结论。**不猜**, 请人工回填。")
        return 2
    # **实测记录的检查**: 没有"开的是真串口"那一行的日志, 一律不当实测记录。
    # 依据是 `trial._serial_note` 写下的那一行(真串口报口名; 替身自报"不是一次实测记录"),
    # 不是"我记得那轮没干跑" —— 干跑的日志与真跑的从内容上分不开(对端不答时都是 RX(0)),
    # 2026-09-16 真漏过 9 份进 `log/`。老的(加这一行之前的)日志没有这行 ⇒ 也**拒收**, 请人工核。
    # ⚠ **判据不在本文件里** —— 它在 `common.runlog.real_run_of`, 而且是**三态**
    #   (True 有凭据说真 / False 有凭据说假 / None **一条凭据都没读到 ⇒ 判不出来**)。
    #   2026-09-17 之前这里是自己写的一份 `.find("真串口") >= 0`, 那是个**包含式**错误:
    #   替身那行 `串口: **SilentSerial** —— 这不是真串口…` 里**含着同样四个字** ⇒ 干跑会被这道
    #   本该拒收它的检查**放行**。同一天 `common.runlog` 里也犯了一次同形的错(被它自己的自检
    #   当场红住)才认出是同一个形状 —— 于是收成一个函数, 两处都 import 它。
    # 凭据的**成色**要报出来: 强 = 桥指纹 + 探活那帧表答了(`common.portsel` 当场读的);
    # 弱 = 2026-09-22 之前那种只有"真串口"三个字的自述。弱不等于假, 但读的人有权知道手上是哪种。
    _lvl = proof_level(info["serial"])
    if _lvl:
        print("凭据成色: %s —— %s" % (_lvl, info["serial"]))
    if info["real_run"] is True and info["serial"] is None and info["legacy_serial"]:
        # 老日志: 选口那两句在 ⇒ 那一轮真开了硬件。把**凭据出处**写上, 免得后面的人以为读到了口名。
        info["serial"] = "%s(老日志: 凭 [串口] 选口那行认)" % info["legacy_serial"]
    if info["real_run"] is not True:
        # `False`(有凭据说不是真跑)与 `None`(凭据一条都没读到)在这里**处置相同**(都拒收),
        # 但话要说清是哪种 —— 前者是"这份日志自报干跑", 后者是"判不出来, 要人看"。
        print("!! 这份日志%s(读到的是: %s) —— **不能当实测记录回填**。"
              % ("自报不是一次实测" if info["real_run"] is False else "里读不到『开的是真串口』的凭据",
                 info["serial"] or "什么凭据都没有"))
        print("   两种可能, 都得人工看一眼再决定, 本工具不替你猜:")
        print("     · 干跑/假串口的日志 —— 那就**根本不该出现在 `log/`**, 更不该进 §10.6;")
        print("     · 别的来路的日志 —— 核过再手填。")
        return 2

    # ---- 跑次名 ↔ 子项: 这一份**是不是这个子项**跑出来的 ----
    # 光判"是实测"不够: 把 4-3 的日志按 4-4 回填, 每一步都是实测, 落下来的却是**甲子项的结论写在
    # 乙子项头上**。判据取自脚本自己声明的那一行(`run_subitem(..., name=…)`) —— 与 `_suite.py`
    # 登记的是同一件事, 但这里从源码抠, 不 import 那个脚本。
    declared, tests_dir = _declared_run_names(item)
    if not declared:
        print("!! 认不出子项 %s 的脚本声明了哪个跑次名(在 %s 里找 `_test_%s*.py` 的 "
              "`run_subitem(..., name=…)`) —— **不敢替你认**, 人工核过再手填。"
              % (item, tests_dir, item.replace("-", "_")))
        return 2
    if info["name"] not in declared:
        print("!! 这份日志的跑次名 %r **不是子项 %s 的** —— %s 的脚本声明的是: %s。**拒收**。"
              % (info["name"], item, item,
                 ", ".join("%s(%s)" % (n, f) for n, f in sorted(declared.items()))))
        print("   这一份属于哪个子项, 就按哪个编号回填; 若它本来就不是本仓的跑次, 更不该进 §10.6。")
        return 2
    print("跑次名核对: %r ← %s" % (info["name"], declared[info["name"]]))

    sub = _subitem_of(item)
    if sub is None:
        print("!! 册子 `_suite.py` 的 ITEMS 里没有编号 %r —— 先把它登记进去再回填。" % item)
        return 2

    with open(doc, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    bounds = _find_row_bounds(lines)
    if bounds is None:
        print("!! 总纲里找不到 §10.6 的表结构(### 10.6 + `| 时间 | 项 |` 表头), **不猜位置**, 人工回填")
        return 2
    first, last, _body = bounds

    # ---- 幂等: 这份日志的**文件名**已经在这张表里出现过 ⇒ 不再追加 ----
    already = any(logbase in ln for ln in lines[first:last])

    # ---- 新行: **只写从日志里读得出来的东西** ----
    verdict = info["verdict"] or "(日志里没有退出码行)"
    if info["abnormal"]:
        verdict += " · 抛异常 %s" % info["abnormal"]
    # 日志路径以**日志自己写的落盘路径**为准(拿不到才退到命令行给的那串)。
    shown = logbase
    if info["saved"]:
        try:
            rel = os.path.relpath(info["saved"], _p)
            if not rel.startswith(".."):
                shown = rel.replace("\\", "/")
        except ValueError:
            pass
    new_row = "| %s | %s %s | 见日志 `%s` | %s | 本次范围=%s; %s 逐条证据见日志同名块 |" % (
        (info["ts"] or "")[:16], item, sub, shown, verdict,
        info["range"] or "(未读到)", info["crit_line"] or "(未读到判据行)")

    print("== _backfill: 子项 %s | 日志 %s ==" % (item, logbase))
    print("   日志读出的: 名字=%s 时间=%s 范围=%s 判据行=%s 串口=%s"
          % (info["name"], info["ts"], info["range"],
             "有" if info["crit_line"] else "没有", info["serial"]))
    print("   退出码行: %s" % (info["verdict"] or "(没读到 —— 那行是 run_subitem 出的, 老日志没有)"))
    if info["abnormal"]:
        print("   !! 这份日志末尾有异常 —— 那一轮**没跑完**, 更要如实记")
    print("   将追加到总纲 §10.6: %s" % ("(跳过 —— 这份日志已记过)" if already else new_row))

    if print_only:
        print("\n--print-only: 一个文件都没改, 检查也没跑")
        return 0
    elif already:
        print("\n== §10.6 已记过这份日志, 不重复追加(幂等)")
    else:
        lines[last:last] = [new_row]
        with open(doc, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print("\n== 已追加一行到总纲 §10.6(%s)" % doc)

    # ---- 本次判据汇总: 照抄日志里那一块, 由人写进结论 ----
    print("\n== 子项 %s 本次的判据汇总(照抄日志同名块, 工具不代写结论) ==" % item)
    for ln in info["summary"]:
        print("     %s" % ln)

    if no_gates:
        print("\n--no-gates: 检查没跑")
        return 0
    print("\n== 派生检查 ==")
    bad = []
    for nm, rc, tail in _run_gates():
        print("   %-18s 退出码 %d  %s" % (nm, rc, "OK" if rc == 0 else "红"))
        if rc != 0:
            bad.append(nm)
            for ln in tail.splitlines():
                print("        | %s" % ln)
    if bad:
        print("\n!! 有检查红了: %s —— 照上面那几行逐条看; 先分清是『仓里真不一致』还是"
              "『这道检查自己没跑起来(路径/解释器)』" % ", ".join(bad))
        return 1
    print("== 检查全绿 ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
