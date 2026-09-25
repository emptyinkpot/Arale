# -*- coding: utf-8 -*-
"""
discover/source.py —— 符号 ←→ 源码足迹: 谁(哪个函数)在哪(`文件:行号`)读写它。

给报告 §3「符号总表」的 **源码足迹** 那一列供料。一个 RAM 符号孤零零地摆在
`0x200034D0 [8B] INT8U[6]` 是死的; 加上"被 `RtcTask` 每写一次、被 `DLT645App` 读三次",
人才知道它**在系统的哪个部位、值不值得进 WATCH_VARS**。

⚠ 外泄硬约束(CLAUDE.md: 白盒反推, 外泄敏感 —— 这是本模块的第一性质)
------------------------------------------------------------------
**本模块只输出指针, 不输出内容。** 一条足迹 = `(相对路径, 行号, 所在函数名)`;
行里写了什么、怎么算的、注释里那句中文 —— **一个字节都不许进本模块的返回值**。
函数名是符号名, 允许; 源码正文不允许。这条由 `selftest.py` 当断言守。

于是本模块**必须把注释和字符串先抠掉**才能判读写 —— 不是为了"干净", 是两件事:
  1. 这份固件里**中文注释极多**, 注释里常直接点名变量(`/* 当前费率号 g_RateNo */`),
     不抠就会把散文当代码, 足迹里凭空多出一堆假的读。
  2. 字符串里的名字(`"g_CurTime"` 出现在打印里)同理。
抠掉之后行号**不变**(整段替换成等长空格), 所以 `文件:行号` 依然准。

读写是怎么判的(启发式, 报告里会照实标注)
---------------------------------------
拿到一次出现, 看它**紧邻的上下文**:
    `x = ` / `x[i] = ` / `x += ` / `x++` / `++x`   → 写
    `&x`(取地址)                                    → 存疑: 可能被指针改写, 单列 addr_taken
    `sizeof(x)`                                     → 既非读也非写, 单列 sizeof
    其余                                            → 读
`==` 是读不是写(别被 `=` 骗了)。成员访问 `a.x` / `p->x` 右半边**跳过**, 否则
所有同名成员都会被算成全局符号的足迹。

⚠ 这是**文本级**分析, 不是编译级。宏包一层、`#if` 掉的死代码、被函数指针转手的写,
本模块**看不见** —— 它给的是"线索", 不是"证明"。所以报告 §3 的足迹列措辞是
"**提到**在 X 读写"而非"X 一定改它"。要坐实得靠 `evidence.py` 的活体采样。

为什么以 `.ewp` 为准而不是扫目录
--------------------------------
源码目录里躺着 `.bak` 备份和没被工程引用的头文件 —— 扫目录会把**没参与编译**的文件
算进来, 足迹凭空变多。IAR 的 `.ewp` 才是"哪些文件真的进了编译"的权威清单。
(实测: 目录里的 `.c` 多于此数; `.ewp` 列了 69 条 `<file>`, 其中 67 个 `.c`。)

本体不 import project / meterlib / swdbg —— 只吃源码目录(可带 .ewp)。

抠注释/认函数定义行/判读写这三件**不住本模块**: 它们住 `common/ctext.py`, 因为断点那条线
(`swdbg/csrc.py`)问的是同一批问题。两处各写一套必然漂移, 而漂移的表现是"同一个变量,
足迹说被写、断点体检说没写" —— 读的人无从判断该信哪一份。
"""
from __future__ import print_function

import io
import os
import re
import sys

from common import ctext


__all__ = ["ewp_files", "ewp_root", "resolve_ewp_rel", "index", "usage", "summary", "SOURCE_EXTS"]

SOURCE_EXTS = (".c", ".h")
# 每个符号最多留多少条足迹。超了截断并置 truncated —— 报告里照实写"仅前 N 条",
# 不许把截断当成"就这么点"。几百个符号全量转储会把报告撑爆, 而人真正要看的是前几条。
CAP_WRITE = 40
CAP_READ = 40


def ewp_root(ewp_path):
    """`$PROJ_DIR$` 的取值 = **.ewp 自己所在的目录**(绝对, IAR 的定义)。

    2026-09-10 从 `ewp_files` 抽出来: `scan.py` 要按同一条规则解析 `.ewp` 里的 `ExePath`
    (定位 `.out`)。`$PROJ_DIR$` 的含义只要出现两份拷贝就必然漂移 —— 三个工程的目录布局
    各不相同, 正是靠这一条才没走歪。**纯字符串→路径, 不判存在性。**
    """
    return os.path.dirname(os.path.abspath(ewp_path))


def resolve_ewp_rel(rel, ewp_path):
    """`.ewp` 里一条相对写法 → 绝对路径(normpath)。

    `$PROJ_DIR$` 与其余 `$*_DIR$`(如 `$TOOLKIT_DIR$`)一律替换成 `ewp_root(ewp_path)`,
    反斜杠转正斜杠 —— 与 `ewp_files` 历史上那句一字不差, 只是搬进了函数。

    ⚠ **替换完还不是绝对路径的, 一律按 `$PROJ_DIR$` 相对解**(IAR 的语义: `.ewp` 里的相对
    路径就是相对工程目录)。踩过(2026-09-10 写 `scan.py` 时): `ExePath` 有两种写法 ——
    APP 写 `$PROJ_DIR$\\..\\Build\\X\\`(自带断点位置), 8611/Boot 写裸的 `Debug\\Exe`。
    漏了这一步, 后者会被 normpath 成一个**相对串**, `isfile` 永远不命中 → 那两个工程的
    `.out` 全都定位不到, 退化成"孤儿"。当时 APP 侥幸对了, 只因为它的写法恰好带宏。
    **不判文件是否存在**(调用方各自决定: `ewp_files` 要 isfile, `scan.ewp_out` 要挑第一个命中的)。
    """
    s = str(rel).replace("\\", "/")
    base = ewp_root(ewp_path).replace("\\", "/")
    s = s.replace("$PROJ_DIR$", base)
    s = re.sub(r"\$[A-Z_]+_DIR\$", base, s)
    if not os.path.isabs(s):
        s = os.path.join(base, s)
    return os.path.normpath(s)


def ewp_files(ewp_path):
    """.ewp → 真正参与编译的源文件绝对路径 list。

    IAR 的路径写成 `$PROJ_DIR$\\..\\Application\\main.c`, `$PROJ_DIR$` = **.ewp 自己所在的目录**
    (实测 = `<工程>/Project`)。只收 `.c`/`.h`, 其余(`.s` 汇编等)跳过 —— 本模块分析的是 C。
    解析失败/文件不在 → 返回空 list, 由调用方回退到扫目录(并把这件事记进 caveats)。
    """
    try:
        with io.open(ewp_path, encoding="utf-8", errors="replace") as f:
            xml = f.read()
    except Exception:
        return []
    out, seen = [], set()
    for m in re.finditer(r"<name>\s*([^<]*?\.[ch])\s*</name>", xml, re.I):
        ap = resolve_ewp_rel(m.group(1), ewp_path)
        if os.path.isfile(ap) and ap not in seen:
            seen.add(ap)
            out.append(ap)
    return out


def index(src_root, names=None, ewp=None, exts=SOURCE_EXTS, progress=None):
    """源码 → {name: {"writes":[...], "reads":[...], "decls":[...], "addr_taken":[...],
                     "sizeof":[...], "n_hits": int, "truncated": bool}}

    一条足迹 = {"file": 相对 src_root 的路径, "line": 行号, "func": 所在函数名或 None}。
    **没有第四项, 永远不会有** —— 源码正文/反汇编不许出现在返回值里(见文件头硬约束)。

    names=None → 索引所有出现的标识符(很占内存, 一般给 elf.py 探到的符号表);
    给了 names 就只留这些名字的足迹。

    ewp: 给了就**以 .ewp 的清单为准**(只扫真参与编译的文件); 不给则扫 src_root 下所有
    `.c/.h`。回退原因写进返回值的 caveats。
    """
    src_root = os.path.abspath(src_root)
    res = {"root": src_root, "ewp": ewp, "files": [], "caveats": [], "index": {}}
    want = set(names) if names else None
    ctr = {}

    def rec(name):
        d = ctr.get(name)
        if d is None:
            d = ctr[name] = {"writes": [], "reads": [], "decls": [],
                             "addr_taken": [], "sizeof": [], "n_hits": 0, "truncated": False}
        return d

    files = ewp_files(ewp) if ewp else []
    if not files:
        if ewp:
            res["caveats"].append("`.ewp` 没解析出文件(%s) —— 回退为扫目录, 足迹可能含"
                                  "未参与编译的 .bak/孤立头文件" % ewp)
        for dp, _dn, fns in os.walk(src_root):
            for fn in sorted(fns):
                if fn.lower().endswith(exts) and ".bak" not in fn.lower():
                    files.append(os.path.join(dp, fn))
    files.sort()
    res["files"] = [os.path.relpath(f, src_root).replace("\\", "/") for f in files]

    for i, ap in enumerate(files):
        if progress:
            progress(i + 1, len(files), ap)
        rel = os.path.relpath(ap, src_root).replace("\\", "/")
        try:
            with io.open(ap, encoding="utf-8", errors="replace") as f:
                raw = f.read().splitlines()
        except Exception as e:
            res["caveats"].append("读不了 %s: %s" % (rel, e))
            continue

        in_block, cur_func = False, None
        for lineno, ln in enumerate(raw, 1):
            code, in_block = ctext.strip_comments(ln, in_block)
            fn = ctext.func_of(code)
            if fn:
                cur_func = fn
            # 先按标识符切; 只看确实是我们关心的名字(名字集合可能上千, 逐行 token 查表最快)。
            for m in ctext.IDENT.finditer(code):
                nm = m.group(0)
                if want is not None and nm not in want:
                    continue
                kind = ctext.kind(code, m.start(), m.end())
                if kind == "skip":
                    continue
                d = rec(nm)
                d["n_hits"] += 1
                if ctext.decl_line(code, nm):
                    d["decls"].append({"file": rel, "line": lineno, "func": cur_func})
                    continue
                bucket = {"write": "writes", "read": "reads",
                          "addr_taken": "addr_taken", "sizeof": "sizeof"}[kind]
                if len(d[bucket]) >= (CAP_WRITE if bucket in ("writes", "reads") else 20):
                    if bucket in ("writes", "reads"):
                        d["truncated"] = True
                    continue
                d[bucket].append({"file": rel, "line": lineno, "func": cur_func})

    res["index"] = ctr
    if not files:
        res["caveats"].append("一个源文件都没扫到 —— 检查 --src 指向哪")
    # 说明散文里刻意不写 `;` `{` `}` —— 好让 selftest 的外泄断言能无条件地
    # "返回值里任一叶子字符串都不许带代码味字符", 不用给说明文字开豁免口子。
    res["caveats"].append("足迹是**文本级**线索: 宏包一层、`#if` 死代码、经函数指针转手的写, "
                          "本模块看不见, 函数归属亦为启发式。措辞是「提到在 X 读写」"
                          "而非「X 一定改它」")
    return res


def usage(one):
    """一个符号的足迹 → 一行的**简短**摘要(报告表格用)。"""
    if not one:
        return "—"
    w, r = len(one["writes"]), len(one["reads"])
    if not one["n_hits"]:
        return "无(未被这两处源码提到)"
    bits = []
    if w:
        bits.append("写%d" % w)
    if r:
        bits.append("读%d" % r)
    if not bits:
        bits.append("仅声明")
    if one["truncated"]:
        bits.append("截断")
    return " ".join(bits)


def summary(res):
    """索引结果 → (计数 dict, 人类可读多行) —— 报告 §3 的概览用。"""
    idx = res["index"]
    tally = {"符号数": len(idx), "完全无足迹": 0, "有写": 0, "只读": 0, "仅声明": 0}
    lines = []
    for name in sorted(idx):
        one = idx[name]
        if not one["n_hits"]:
            tally["完全无足迹"] += 1
        elif one["writes"]:
            tally["有写"] += 1
        elif one["reads"]:
            tally["只读"] += 1
        else:
            tally["仅声明"] += 1
        if one["n_hits"]:
            first = (one["writes"] or one["reads"] or one["decls"])[0]
            lines.append("   %-30s %-14s 首见 %s:%d%s"
                         % (name, usage(one), first["file"], first["line"],
                            "  <%s>" % first["func"] if first["func"] else ""))
    return tally, lines


# ============================ 独立 CLI ============================
# 控制台 UTF-8 单点: 实现收在 common/console.py(2026-09-10 迁自 meterlib)。
# 本包只依赖 common(不是 meterlib), 故可直接引 —— 原先各内联一份的理由(它住在协议层)已消失。
# 别名保原调用点 `_ensure_utf8_stdout()` 一字不改。
from common.console import ensure_utf8_stdout


def main(argv=None):
    """python -m discover.source --src <源码目录> [--ewp <x.ewp>] [--out <某表.out>] [<名字正则>]

    给了 --out 就只索引那张表 `.out` 里的 RAM 符号(推荐 —— 否则会把全工程标识符都收进来)。
    **只打印 文件:行号 指针, 不打印源码** —— 这条在本模块的 CLI 上同样是硬约束。
    """
    ensure_utf8_stdout()
    import argparse
    ap = argparse.ArgumentParser(description="符号 ←→ 源码足迹(只输出 文件:行号 指针, 不含源码)")
    ap.add_argument("--src", required=True, help="固件源码目录")
    ap.add_argument("--ewp", default="", help="IAR .ewp(给了就以它的文件清单为准, 不扫目录)")
    ap.add_argument("--out", default="", help=".out —— 只索引它里面的 RAM 符号")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("pattern", nargs="?", default="", help="只索引名字匹配此正则的符号")
    args = ap.parse_args(argv)

    if not args.out:
        print("!! 需要 --out —— 不给就不知道要索引哪些符号, 会把全工程标识符都收进来。"
              "先 `python -m discover.elf <某表.out>` 看有哪些符号")
        return 2
    from discover import elf
    names = [b.name for b in elf.symbols(out=args.out, pattern=args.pattern or None)]
    if not names:
        print("!! 那张 .out 里没有匹配的符号(检查 --out / 正则)")
        return 2

    r = index(args.src, names=names, ewp=args.ewp or None,
              progress=lambda i, n, ap_: print("   [%d/%d] %s" % (i, n, os.path.basename(ap_))))
    if args.json:
        import json
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    tally, lines = summary(r)
    print("== 源码足迹: %s ==" % r["root"])
    print("   扫了 %d 个文件%s" % (len(r["files"]), "(.ewp 清单)" if r["ewp"] else "(扫目录)"))
    for ln in lines:
        print(ln)
    print("   -- %s --" % "  ".join("%s=%d" % (k, v) for k, v in sorted(tally.items())))
    for c in r["caveats"]:
        print("   ⚠ %s" % c)
    return 0


if __name__ == "__main__":
    sys.exit(main())
