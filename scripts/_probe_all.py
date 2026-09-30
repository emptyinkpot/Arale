# -*- coding: utf-8 -*-
"""
_probe_all.py —— **批量**探测: 喂一个目录/一批 `.out` → 一镜像一份报告 + 一份总索引

位置: 帧收发基础/scripts/ (顶层运行脚本; 库在 src/discover/)
运行: 在 帧收发基础/ 下 —— python scripts/_probe_all.py <目录|.ewp|.out> [<...>] [开关]

与 _init_meter.py 的分工
------------------------
`_init_meter.py` **一次一块表**, 四样输入(`--out/--src/--doc/--ewp`)全靠人手打 —— 它管"探得准"。
本脚本管"**探得多**": 手打换成找。一个目录里所有工程, 它自己认出每块的 `.out` 在哪、
源码根在哪(规则见 `discover/scan.py` 的文件头), 然后逐个跑同一条 `dossier.build`。

    产物  project/knowledge/探测报告/<表名>_探测.md   ← **一个镜像一份**(与 _init_meter 同一份代码路径)
          project/knowledge/探测报告/索引.md          ← 一份总索引(派生品, 每次重写)

**本脚本只是一层壳**: 找目标、校验计划、渲染索引全在 `discover/scan.py` ——
仓规"测试脚本 = 薄操作清单; 非平凡函数一律进库"。

一块表 vs 一个镜像
------------------
本脚本按**镜像**(`.out`)计数, **不按表**: 一个 IAR 工程 = 一个 `.out` = 一份报告。
"这几份属于同一块物理表"这种归属判断需要工程知识, 而 `discover/` 一律不认表 ——
索引里因此只列镜像, 不臆造表的归属。

--live 为什么在批量下会被拒
---------------------------
物理上只接了**一块**表/一个 J-Link。给 N 个目标加 `--live`, 会把**同一块表**的活体数据
贴到 N 个名字上, 造出 N 份"有活体证据"的假报告 —— 这是**静默错误**, 比跑失败坏得多。
所以要采活体必须收窄到恰好一个:

    --live-only <表名>      # 只让这一个连表, 其余照常离线
    --only <表名> --live    # 整批收窄成一个

覆盖策略
--------
`write_report` 是**静默覆盖**。批量下默认**跳过已存在的报告**(打出来, 一个字节不动),
`--force` 才重写。于是重跑是安全的、可断点续的(一个失败, 重跑只补它)。
索引文件每次都重写 —— 它是派生品, 本来就没有"旧版"概念。

--from-project: 声明驱动(连目录都不用给)
----------------------------------------
默认那一路是**扫盘推**: 从 `.ewp` 的 `ExePath` 推 `.out`、从 `.ewp` 文件清单的公共祖先推
源码根。三个真工程都推对了, 但 `src_root_of` 留着一个 fallback —— 公共祖先退化到盘根时
退回 `.ewp` 所在目录, **那是猜**。

`--from-project` 换成**读卡带声明**(`project/firmware.py` → 画像 `OUT_PATH` + meta 的
`firmware` 块): 有声明用声明, 推导只当兜底 —— 与 `swdbg/resolve` 的「画像优先、`.out`
回退」同构。同时多出一道**指纹判定**: 声明的 `out_sha256` 与盘上 `.out` 实算不符就**拒绝探测**
(`--force` 才过), 把"读到旧固件"这个静默错误堵在入口。

⚠ `import project` **在这一支里**, 不在模块顶部。本脚本是**通用**批量探测器(喂任何表都能用),
顶部 import `project` 会让它绑死在一块卡带上 —— 那正是 `_init_meter.py` 刻意绕开的事。

声明与推导**逐字段交叉核对**, 不一致**大声打印并写进 `索引.md`**, 但**仍以声明为准**执行:
不一致说明有一方错了, 不能静默; 而推导正是会退化到"猜"的那一方, 不该让它覆盖声明。
这一步只有**脚本层**做得了 —— 只有它同时知道 `project/` 和 `discover/`。

退出码: 0=全成(或预期的跳过) / 1=至少一个目标失败 / 2=前置失败(参数/0 目标/live 违规/指纹不符)
"""
import os
import sys


from discover import scan
from discover import dossier
from discover import elf

from common.console import ensure_utf8_stdout
from common.cli import guard_argv
from common import runlog

# ============================ 开关表(单一真源) ============================
# (名字, 带值?, 一句话说明)。guard_argv 的 allow/known 与 _usage() 都从这一张表派生。
# 理由: guard_argv 的失败模式是"漏登记一个真开关 → 正常用法被误拦"和"多登记一个不存在的
# → 那道判定对它失效(静默)"。一张表两个消费者, 物理上不可能漂移。
FLAGS = (
    ("--from-project", False,
     "从本仓卡带(project/)读**固件声明**拿齐四样输入, 不必给目录(声明优先, 推导只当兜底)"),
    ("--doc",      True,  "总纲路径(全局一份, 默认 project/knowledge/对表操作总纲.md)"),
    ("--no-doc",   False, "不给总纲(总纲不在本机时显式关掉, 免得每份报告都背着'未提供')"),
    ("--src",      True,  "源码根 —— **只补孤儿 .out**(没有 .ewp 推不出源码根的那些)"),
    ("--only",     True,  "只探这个名字(可重复); 重跑单个/配合 --live 收窄都用它"),
    ("--live-only", True, "只让这一个目标连 J-Link 采活体, 其余照常离线"),
    ("--live",     False, "采活体 —— **批量下会被拒**, 除非选中恰好 1 个目标"),
    ("--rounds",   True,  "活体采样轮数(默认 5)"),
    ("--gap",      True,  "活体采样间隔秒(默认 0.5)"),
    ("--pattern",  True,  "只探名字匹配此正则的符号(透传)"),
    ("--depth",    True,  "目录递归上限(默认 %d)" % scan.DEFAULT_DEPTH),
    ("--to-dir",   True,  "报告目录(默认 project/knowledge/探测报告/)"),
    ("--index",    True,  "索引文件路径(默认 <to-dir>/索引.md)"),
    ("--force",    False, "允许覆盖已存在的报告(默认跳过)"),
    ("--verbose",  False, "打印逐目标进度(默认只在目标边界报一行)"),
    ("--dry",      False, "只解析目标并打印, 不 build、不写盘"),
    ("--help",     False, "本说明"),
    ("-h",         False, "本说明"),
)
ALLOW = tuple(n for n, has, _h in FLAGS if not has)
KNOWN = tuple(n for n, has, _h in FLAGS if has)

STEP = "   "


def _has(flag):
    return flag in sys.argv


def _val(flag, default=""):
    if flag in sys.argv:
        i = sys.argv.index(flag) + 1
        if i < len(sys.argv):
            return sys.argv[i]
    return default


def _vals(flag):
    """可重复开关的全部取值(--only 甲 --only 乙)。"""
    out, i = [], 0
    while i < len(sys.argv):
        if sys.argv[i] == flag and i + 1 < len(sys.argv):
            out.append(sys.argv[i + 1])
            i += 2
        else:
            i += 1
    return out


def _positionals():
    """位置操作数 = 既不是开关、也不是某个带值开关的值。"""
    out, i = [], 1
    while i < len(sys.argv):
        a = sys.argv[i]
        if a in KNOWN:
            i += 2
            continue
        if a in ALLOW:
            i += 1
            continue
        if not a.startswith("-"):
            out.append(a)
        i += 1
    return out


def _usage():
    print(__doc__.strip().split("退出码:")[0].strip())
    print("")
    print("  开关:")
    for name, has, help_ in FLAGS:
        print("    %-14s %s" % (name + (" <值>" if has else ""), help_))
    print("")
    print("  例(在 帧收发基础/ 下):")
    print("    python scripts/_probe_all.py --from-project --dry             # 读卡带声明(不用给目录)")
    print("    python scripts/_probe_all.py --from-project                   # 真跑")
    print("    python scripts/_probe_all.py \"E:\\My Work\\MengXi\" --dry      # 先看认出哪几块表")
    print("    python scripts/_probe_all.py \"E:\\My Work\\MengXi\"           # 真跑(已存在的报告跳过)")
    print("    python scripts/_probe_all.py \"E:\\My Work\\MengXi\" --only EZ315-8611 --force")
    print("    python scripts/_probe_all.py <目录> --live-only EZ315-FM33A0610EV-APP --dry")
    return 0


def _crosscheck(t, fw):
    """声明 vs 从 `.ewp` 推导 —— 逐字段比 → (差异清单:list[str], 推导出的配置名)。

    **不动 `t`**: 比出来的是"两边说法不同", 执行仍以声明为准(见模块头那段)。
    推导那侧用现成的 `ewp_out`/`src_root_of`(不另写一份启发式), 于是这里比的就是
    "扫盘那一路会得到什么" —— 差异一旦出现, 正是两份真相开始分岔的第一个信号。
    """
    diffs, cfg = [], None
    if t.ewp:
        got, _n, cfg = scan.ewp_out(t.ewp)
        if got and os.path.normpath(got) != os.path.normpath(t.out or ""):
            diffs.append(".out      声明 %s / 从 .ewp 的 ExePath 推 %s" % (t.out, got))
        root, _s = scan.src_root_of(t.ewp)
        if root and os.path.normpath(root) != os.path.normpath(t.src_root or ""):
            diffs.append("源码根    声明 %s / 从 .ewp 清单公共祖先推 %s" % (t.src_root, root))
    dname = dossier.meter_name(t.out) if t.out else ""
    if dname and fw.get("name") and dname != fw["name"]:
        diffs.append("表名      声明 %s / 从 .out 推 %s" % (fw["name"], dname))
    return diffs, cfg


def _declared(doc, force, doc_default=True):
    """`--from-project` → (targets, 交叉核对差异, 退出码|None; None = 继续)。

    ⚠ `import project` 在**这里**, 不在模块顶部 —— 本脚本是**通用**批量探测器,
      顶部 import 会让它绑死在一块卡带上(见模块头)。`--from-project` 是**显式选择**
      "这次读本仓的卡带", 所以在这一支里 import。
    """
    from project import firmware
    from common import profile
    fw = firmware.inputs(profile.current())
    if not fw["out"] or "out" in fw["missing"]:
        print("!! 卡带声明的 .out 不可用: %s"
              % ("; ".join(fw["notes"]) or "画像未给 OUT_PATH"))
        return [], [], 2

    # ---- 指纹判定: 把"探错固件"堵在入口 ----
    real = elf.sha256(fw["out"])
    if not fw["sha256"]:
        print("!! 卡带未钉固件指纹(meta 的 firmware.out_sha256 为空) → 无法核对"
              "'是不是声明的那份固件', 照跑。")
    elif real != fw["sha256"]:
        if not force:
            print("!! 固件指纹不符 —— **拒绝探测**")
            print(STEP + "声明(meta.firmware.out_sha256) %s" % fw["sha256"])
            print(STEP + "实算(%s) %s" % (os.path.basename(fw["out"]), real))
            print(STEP + "声明说的是**另一份固件**。要么把那份找回来, 要么把 out_sha256")
            print(STEP + "更新成实算值 —— 换了固件重编**本来就该人去改声明**, 不该让工具默默将就。")
            print(STEP + "确认就是要探盘上这一份 → 加 --force(结尾会再提醒你更新声明)。")
            return [], [], 2
        print("!! 固件指纹不符, --force 放行: 探的是**盘上这一份**。报告 §0 记的是它的实算值,")
        print(STEP + "而卡带声明的仍是另一份 —— **探完请把 out_sha256 更新掉**。")

    # 名字取 `meter_name(.out)` 而**不是**声明里的 name: 报告文件名必须与扫盘那一路**逐字一致**,
    # 否则 `--from-project` 会另生一份报告、绕开"已存在就跳过"的断点续跑。声明的名字不丢,
    # 它在下面的交叉核对里比(不一致就报出来)。
    t = scan.Target(name=dossier.meter_name(fw["out"]), kind="declared",
                      out=fw["out"], ewp=fw["ewp"], src_root=fw["src_root"], doc=doc,
                      missing=list(fw["missing"]), notes=list(fw["notes"]), explicit=True)
    t.notes.append("四样输入来自**卡带声明**(project/firmware.py), 不是扫盘推的")
    # ⚠ 总纲**不在声明里**(它是表族知识, 不是这版固件的属性; 放进去会暗示"换固件要换总纲")。
    #   所以这里要**照 `scan()` 的那三行原样兜一遍**, 否则同一次运行里, 扫盘目标有总纲、
    #   声明目标没有 —— 两份报告 §5 一节之差, `--from-project` 就成了个降级路径。
    #   漏过(2026-09-10): 第一版只写 `doc=doc`, 于是默认位置的总纲被静默丢掉。
    if t.doc is None:
        dflt = scan.default_doc() if doc_default else None
        if dflt:
            t.doc = dflt
        else:
            t.missing.append("doc")
            t.notes.append("总纲未提供(默认位置也没有) → §5 整节留空")

    diffs, cfg = _crosscheck(t, fw)
    t.config = cfg
    if diffs:
        print("!! 声明与推导**对不上**(仍以声明为准执行, 但有一方错了):")
        for d in diffs:
            print(STEP + d)
    else:
        print(STEP + "声明与推导一致(.out / 源码根 / 表名 三项)")
    return [t], diffs, None


def _run_one(t, live, rounds, gap, pattern, verbose, to_dir=None):
    """一个目标 → TargetResult。**失败隔离**: 任何异常都变成 FAIL 结果, 不往外抛。"""
    r = scan.TargetResult(t, "OK")
    say = (lambda m: print(STEP + "… " + m)) if verbose else (lambda m: None)
    try:
        rep = dossier.build(t.out, src=t.src_root, doc=t.doc, ewp=t.ewp,
                       live=live, rounds=rounds, gap=gap, pattern=pattern, progress=say)
        if live:
            # 报告离开这台机器后, 读者必须能判断 §4 属于哪块物理表 ——
            # J-Link 只有一个且没指定 SN, 这条不写, 一份填满 §4 的报告看起来就像"都验过活体"。
            rep["caveats"].append(
                "本 §4 活体采自本次**唯一在线目标** %s(J-Link 只有一个, 未指定 SN)" % t.name)
        r.sha256 = rep.get("sha256", "")
        r.syms = len(rep.get("syms") or [])
        r.sections = len(rep.get("sections") or [])
        r.gaps = len(rep.get("gaps") or [])
        r.src_files = rep.get("src_files", 0) or 0
        r.elapsed = rep.get("elapsed", 0.0)
        r.live = bool(live)
        r.live_ok = (rep.get("live_data") is not None) if live else None
        # ⚠ `to_dir` 必须传下去。原先这里写的是 `t.report_path()`(不给参数 = 默认目录),
        #   于是 `--to-dir X` 只在"跳不跳过"那一句生效, **真写的时候还是写回默认目录** ——
        #   给了 --to-dir 反而更危险: 跳过检查查的是 X(不存在), 写却落在默认目录,
        #   于是"以为另存一份"的那次运行**静默盖掉了默认目录里的既有报告**(踩过 2026-09-10)。
        r.report = dossier.write_report(rep, t.report_path(to_dir))
    except Exception as e:
        r.status = "FAIL"
        r.error = "%s: %s" % (type(e).__name__, e)
        if verbose:
            import traceback
            traceback.print_exc()
    return r


def main():
    # ⚠ UTF-8 必须在 runlog.run() **之前** —— _Tee 把当时的 sys.stdout 当 console,
    #   顺序反了, tee 里那半边还是非 UTF-8 的旧流。
    ensure_utf8_stdout()
    guard_argv(sys.argv[1:], allow=ALLOW, known=KNOWN, positional=True)
    if not sys.argv[1:] or _has("--help") or _has("-h"):
        return _usage()

    paths = _positionals()
    from_project = _has("--from-project")
    if not paths and not from_project:
        print("!! 至少给一个目录(或 .ewp / .out); 或 --from-project 从卡带声明读")
        print("   例: python scripts/_probe_all.py \"E:\\My Work\\MengXi\" --dry")
        return 2

    doc = _val("--doc")
    if doc and not os.path.isfile(doc):
        print("!! --doc 不存在: %s" % doc)
        return 2
    src = _val("--src")
    if src and not os.path.isdir(src):
        print("!! --src 不是目录: %s" % src)
        return 2
    depth = int(_val("--depth", str(scan.DEFAULT_DEPTH)) or scan.DEFAULT_DEPTH)
    rounds = int(_val("--rounds", "5") or 5)
    gap = float(_val("--gap", "0.5") or 0.5)
    pattern = _val("--pattern") or None
    only = _vals("--only")
    live_only = _val("--live-only") or None
    live = _has("--live")
    dry = _has("--dry")
    force = _has("--force")
    verbose = _has("--verbose")
    to_dir = _val("--to-dir")
    if not to_dir:
        # 报告落在哪 **由卡带声明**(`project/<表>.py` 的 `REPORTS_DIR` → project/knowledge/探测报告)。
        # 只认 `--from-project` 这一支: 它本就是"探**本仓声明的这块表**", 产物自然归这块表的知识
        # 卡带。给了目录(探**陌生表**)则落 cwd 下的 project/knowledge/探测报告/ —— 别把别人的报告写进本表的卡带。
        from common import profile
        to_dir = (profile.resolve("REPORTS_DIR") if from_project else None) or dossier.DEFAULT_DIR
    index_path = _val("--index") or os.path.join(to_dir, "索引.md")

    # 落 `log/工具/` —— `log/` 根下只装表应答过的子项跑次(见 `log/README.md`)。
    with runlog.run("_probe_all", logdir=os.path.join(runlog.default_logdir(), "工具")) as log_path:
        rc = _main(paths, doc=doc, src=src, depth=depth, rounds=rounds, gap=gap,
                   pattern=pattern, only=only, live_only=live_only, live=live,
                   dry=dry, force=force, verbose=verbose, to_dir=to_dir,
                   index_path=index_path, log_path=log_path,
                   from_project=from_project)
    return rc


def _main(paths, doc, src, depth, rounds, gap, pattern, only, live_only, live,
          dry, force, verbose, to_dir, index_path, log_path, from_project=False):
    print("== 批量探测 ==")
    for p in paths:
        print(STEP + "扫描   %s" % p)
    if from_project:
        print(STEP + "声明   --from-project: 从本仓卡带(project/)读固件声明")
    print(STEP + "深度   %d 层" % depth)
    print(STEP + "总纲   %s" % (doc or ("(显式关掉)" if _has("--no-doc") else "默认位置")))
    print(STEP + "活体   %s" % ("--live-only %s" % live_only if live_only else
                                ("--live" if live else "不采(离线)")))
    print(STEP + "覆盖   已存在的报告默认跳过; --force 才盖写")

    targets, cc = [], []
    if from_project:
        targets, cc, rc = _declared(doc or None, force, doc_default=not _has("--no-doc"))
        if rc is not None:
            return rc

    if paths:
        found = scan.scan(paths, doc=(doc or None), doc_default=not _has("--no-doc"),
                            to_dir=to_dir, depth=depth,
                            progress=(lambda m: print(STEP + "… " + m)) if verbose else None)
        # 声明目标与扫出来的**同一份 .out** 不算两个目标(否则 check_plan 的"重名"会把
        # 本该成立的一次运行拦下)。声明那一路显式指名, 所以留下它、丢掉扫出来的那一份。
        _declared_outs = {os.path.realpath(t.out) for t in targets if t.out}
        dup = [t for t in found if t.out and os.path.realpath(t.out) in _declared_outs]
        if dup:
            print(STEP + "扫出来的 %s 与声明目标是同一份 .out → 并入声明目标(不重复)"
                  % " ".join(t.name for t in dup))
        targets += [t for t in found if t not in dup]

    if only:
        want = set(only)
        targets = [t for t in targets if t.name in want]
        missed = sorted(want - {t.name for t in targets})
        if missed:
            print("!! --only 没命中: %s" % " ".join(missed))
            return 2

    probs = scan.check_plan(targets, live=live, live_only=live_only)
    if probs:
        for p in probs:
            print("!! %s" % p)
        return 2

    print("")
    print("-- 认出 %d 个目标 --" % len(targets))
    for i, t in enumerate(targets, 1):
        print("[%d/%d] %s" % (i, len(targets), scan.describe_target(t, verbose=verbose, to_dir=to_dir)
                              .replace("[目标] ", "")))
    sk = scan.last_skipped()
    if sk:
        print("")
        print("-- 跳过 %d 项(不生成报告) --" % len(sk))
        for p, why in sk:
            print(STEP + "%s\n%s   → %s" % (p, STEP, why))

    if dry:
        print("")
        print("-- --dry: 只解析目标, 不 build、不写盘 --")
        print(STEP + "将生成 %d 份报告 + %d 份索引到 %s" % (len(targets), 1, to_dir))
        print(STEP + "要真跑就去掉 --dry。")
        return 0

    results = []
    for i, t in enumerate(targets, 1):
        live_this = (live_only == t.name) if live_only else live
        rp = t.report_path(to_dir)
        print("")
        print("== [%d/%d] %s ==" % (i, len(targets), t.name))
        if os.path.exists(rp) and not force:
            r = scan.TargetResult(t, "SKIP-EXISTS")
            r.report = rp
            results.append(r)
            print(STEP + "跳过(已存在: %s; 要盖写加 --force)" % rp)
            continue
        r = _run_one(t, live_this, rounds, gap, pattern, verbose, to_dir=to_dir)
        results.append(r)
        if r.status == "FAIL":
            print(STEP + "!! 失败: %s" % r.error)
            continue
        print(STEP + "报告 %s" % r.report)
        print(STEP + "符号 %d / 段 %d / 空闲洞 %d / 源码文件 %d / 耗时 %.1fs"
              % (r.syms, r.sections, r.gaps, r.src_files, r.elapsed))
        if r.live and r.live_ok is False:
            print(STEP + "!! §4 没采到活体(见报告 caveats)")

    # ---- 索引 ----
    others = sorted(f for f in (os.listdir(to_dir) if os.path.isdir(to_dir) else [])
                    if f.endswith("_探测.md")
                    and f not in {os.path.basename(r.report) for r in results if r.report})
    extra = {"扫描根": "  ".join(paths) or "(未给目录)",
             "目标数": str(len(targets)),
             "活体": (live_only or ("--live" if live else "未采(离线)")),
             "报告目录": to_dir, "日志": log_path or "(未记录)",
             "未覆盖的报告": others}
    if from_project:
        extra["声明"] = "--from-project(读 project/ 卡带声明)"
        if cc:
            # 索引是"这次到底发生了什么"的唯一遗迹 —— 差异只打在终端上, 关上窗就没了。
            extra["声明与推导的差异"] = cc
    scan.write_index(index_path, scan.render_index(results, extra))

    # ---- 汇总 ----
    n_ok = sum(1 for r in results if r.status == "OK")
    n_skip = sum(1 for r in results if r.status.startswith("SKIP"))
    n_fail = sum(1 for r in results if r.status == "FAIL")
    print("")
    print("== 汇总 ==")
    for r in results:
        if r.status == "FAIL":
            note = r.error
        elif r.status == "SKIP-EXISTS":
            note = "已存在(要盖写加 --force)"
        else:
            note = "符号 %d / 报告 %s" % (r.syms, os.path.basename(r.report or ""))
        print("   %-32s %-14s %s" % (r.name, r.status, note))
    print("   -- OK %d / 跳过 %d / 失败 %d / 共 %d --" % (n_ok, n_skip, n_fail, len(results)))
    print("")
    print("== 索引: %s ==" % index_path)
    if log_path:
        print("== 日志: %s ==" % log_path)
    print("")
    print("   下一步: 打开 索引.md 看覆盖是否完整; 再读各份报告的 §7(开放问题) ——")
    print("           那是建画像的任务清单。")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
