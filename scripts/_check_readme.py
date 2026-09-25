#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_check_readme.py —— README 的文件树 == 仓库里真实的文件?(检查, 2026-09-14 建)

README 是本仓的**入口地图**, 但地图画的是"当时"的仓。搬走一个文件、删掉一个模块、
新添一个脚本而忘了改 README —— 地图就与地不符, 而**没有任何东西会报错**:
照 README 找 `归档/probe_frez_readback.py` 会扑空, 照它建新脚本会漏掉刚加的积木。
这正是本仓反复治理的那一类错 —— **静默**。

于是把"README 要跟着仓走"从一句约定变成一道能跑的检查:

    README 树里列出的**每一个**路径, 盘上必须有;
    盘上**每一个**受版本控制 / 未忽略的文件, 树里必须列。

**靠什么解析得准**: 树里每个条目的**名字不许多含空格**(注释段随便写, 空格隔开即可)。
于是 `│   ├── <名字>   <注释>` 里, "名字"就是紧跟树线之后的那一串非空白字符;
层级 = 树线之前那段前缀的长度 ÷ 4(`│   ` 与 `    ` 都是 4 字符)。本仓的文件名本来就不带空格,
这条约定零成本 —— 代价只是别在名字里写空格。

⚠ 这是**仓级**检查(不认表、不认机器), 所以住 `scripts/` 而不是任何一盘卡带里。

**唯一的例外**: 围栏标记写成 ```` ```apitree ```` 的块**不参与比对**。
README 里有一节画的是"哪个函数住在哪个 .py 下面"—— 那也是树线, 但叶子是 API 不是文件,
硬按文件树解析会凭空多出几百条不存在的路径。标记是**显式**的: 没写标记的块照样一比一咬,
所以这条例外只放行"自己声明了不是文件树"的块, 不是放松判据。

用法:
    python scripts/_check_readme.py            # 0=树与仓一致, 1=有漂移
    python scripts/_check_readme.py --json
"""
import os
import re
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
# ---- 仓根(向上找含 src/ 的那一级), **只用来定位文件** ----
_ROOT = _HERE
while not os.path.isdir(os.path.join(_ROOT, "src")) and os.path.dirname(_ROOT) != _ROOT:
    _ROOT = os.path.dirname(_ROOT)

from common.console import ensure_utf8_stdout  # noqa: E402

README = os.path.join(_ROOT, "README.md")

# 一棵树条目的样子: 前缀(│ 与空格, 每级 4 字符) + 树线 + 空格 + 名字(非空白串)
_ENTRY = re.compile(r"^(?P<pre>[│ ]*)(?:├──|└──) (?P<name>\S+)")
# 引言里那个"全仓 N 个文件"
_COUNT = re.compile(r"\*\*(\d+)\*\*\s*个文件")
# 声明"我不是文件树"的围栏标记(大小写不认, 见文件头)。改这里 = 改唯一那份例外名单。
_SKIP_TAGS = {"apitree"}


def _on_disk():
    """盘上真实存在的文件(=已跟踪 ∪ 未跟踪未忽略, 再滤掉"索引里还在但盘上已删"的)。

    有意**不**只信 `git ls-files`: 它会把已从盘上删掉、但还没提交的条目也算进来,
    而 README 描述的是**盘上现在有什么**。`--exclude-standard` 让 .gitignore 说了算,
    于是 `log/`、`_dbg/`、`__pycache__/` 这些一次性产物不会来捣乱。
    """
    r = subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-files", "-z",
         "--cached", "--others", "--exclude-standard"],
        cwd=_ROOT, capture_output=True)
    if r.returncode != 0:
        raise SystemExit("git ls-files 失败(这不是一个 git 仓?): %s"
                         % r.stderr.decode("utf-8", "replace").strip())
    names = r.stdout.decode("utf-8", "surrogateescape").split("\0")
    return {n.replace("\\", "/") for n in names
            if n and os.path.isfile(os.path.join(_ROOT, n.replace("\\", "/")))}


def _declared(text):
    """从 README 的围栏代码块里解析出所有**文件**路径(目录条目只用来拼前缀, 不进结果)。"""
    files, stack, bad = set(), {}, []
    in_fence, skip = False, False
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            if in_fence:
                in_fence, skip = False, False
            else:
                in_fence = True
                skip = stripped[3:].strip().lower() in _SKIP_TAGS
            continue
        if not in_fence or skip:
            continue
        m = _ENTRY.match(line)
        if not m:
            continue
        pre, name = m.group("pre"), m.group("name")
        if len(pre) % 4:
            bad.append((lineno, line))
        depth = len(pre) // 4
        base = "/".join(stack[i] for i in range(depth) if i in stack)
        full = (base + "/" + name) if base else name
        if name.endswith("/"):
            stack[depth] = name[:-1]
            for k in [k for k in stack if k > depth]:   # 同级或更深的前一条, 出了作用域
                del stack[k]
        else:
            files.add(full)
    return files, bad


def main(argv):
    ensure_utf8_stdout()
    text = open(README, encoding="utf-8").read()
    declared, bad_prefix = _declared(text)
    actual = _on_disk()

    only_readme = sorted(declared - actual)      # README 画了, 盘上没有
    only_disk = sorted(actual - declared)        # 盘上有, README 没画
    declared_count = next((int(m.group(1)) for m in [_COUNT.search(text)] if m), None)
    count_bad = declared_count is not None and declared_count != len(actual)

    ok = not (only_readme or only_disk or bad_prefix or count_bad)
    if "--json" in argv:
        import json
        print(json.dumps({
            "ok": ok, "n_disk": len(actual), "n_declared": len(declared),
            "declared_count": declared_count,
            "only_in_readme": only_readme, "only_on_disk": only_disk,
            "bad_prefix": [ln for ln, _ in bad_prefix],
        }, ensure_ascii=False, indent=2))
        return 0 if ok else 1

    for ln, line in bad_prefix:
        print(f"[FAIL] README.md:{ln} 树线前缀不是 4 的整数倍(层级算不出来): {line!r}")
    if count_bad:
        print(f"[FAIL] README 写的是 {declared_count} 个文件, 盘上实际 {len(actual)} 个")
    for p in only_readme:
        print(f"[FAIL] README 画了, 盘上没有: {p}")
    for p in only_disk:
        print(f"[FAIL] 盘上有, README 没画: {p}")

    if ok:
        print(f"README CHECK: OK ({len(actual)} 个文件, 文件树与仓库一致)")
        return 0
    print(f"\nREADME CHECK: DRIFT —— 树 {len(declared)} 条 / 盘 {len(actual)} 个。"
          f"\n改完 README 再跑一次; 这条检查的全部意义就是不许树和仓各说各话。")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
