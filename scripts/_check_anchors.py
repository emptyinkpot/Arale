# -*- coding: utf-8 -*-
"""
_check_anchors.py —— 把测试脚本里的断点拿去**对源码**, 看要读的变量在断点那一行赋过值没有
位置: 帧收发基础/scripts/ (薄操作清单; 判定在 src/swdbg/breakpoint.py)

它替掉的那件手工活
-----------------
`project/tests/_test_*.py` 里写着 `BP_B = ("TaskFreeze.c", 823)` 这样的断点。
那个行号是**人从文档/源码上抄过来的**, 抄错了没有任何东西会发现 ——
gdb 停得住、变量读得出、打印"像样", 而读的是未初始化的栈值。实测踩过(4-6)。
本脚本把那些断点抠出来, 逐条问源码: 这个变量在断点那一行之前赋过值吗?

断点从脚本里**抠**, 不另立一份清单
-------------------------------
单一真源就是测试脚本本身(改断点只改一处)。按命名约定配对: `BP_<X>` ↔ `VARS_<X>`。
抠不出配对的、或 VARS 为空的, **列出来**而不是跳过 —— "断点没被查到"必须看得见。

断点的**写法**有三种(定义在 `swdbg/breakpoint.py`, 与运行时共用一份: `("文件", 行号)` /
`("call", 函数, 被调, 第几处)` / `("func", 函数名)`)。后两种不写行号 —— 运行时由 `.out` 解成
地址, 本检查则要把它们换回**源码行**才能问"这个变量赋过值没有"。换算是 `SrcIndex.resolve`,
`("call", …)` 那种还得先找出定义那个函数的 `.c`(它不写文件名)。**换不回来的当场列进 problems** ——
悄悄跳过的话, 那个断点就成了没人核过的一个。

只报事实, 不判红绿
------------------
输出每行的形状是「变量 / 首次出现行 / 断点前最后一次赋值行(或"无")」。
**没有 PASS/FAIL。** 一个把"断点在赋值之前"判成 FAIL 的检查, 和把它判成 PASS 的检查,
交付给人的信息一样少; 要人做决定的是"读出来这个值算不算数", 那是人的判断。
退出码: 0=跑完且每条断点都有配对 / 1=有断点抠不出配对或变量 / 2=前置失败。

行号也可以**直接从文档/表格里喂进来**(`--anchors`)
--------------------------------------------------
脚本常量那条路只覆盖"断点已经写进 _test_*.py"的情形。而行号被抄进 xlsx / 总纲 §10.6
的时候,**还没有任何脚本** —— 那正是错误最容易活下来的时刻(抄的人当场看不出对错)。
所以另给一条路: `文件:行号:变量1,变量2`, 可以给多次。这样交出去的清单在入册之前
就能先过一遍源码, 而不是等哪天真去跑断点才发现读出来的是垃圾。

运行(在 帧收发基础/ 下)
----------------------
    python scripts/_check_anchors.py                                  # 本仓全部 _test_*.py
    python scripts/_check_anchors.py project/tests/_test_4_6_aa80.py  # 只查一个
    python scripts/_check_anchors.py --anchors TaskFreeze.c:823:usekWh \\
                                     --anchors TaskFreeze.c:851:usekWh
    python scripts/_check_anchors.py --src-root 'E:\\...\\EZ315-...-APP'   # 不给就按卡带声明找
"""
import ast
import os
import sys

# ---- 仓根(向上找含 src/ 的那一级), **只用来定位文件** ----
_p = os.path.dirname(os.path.abspath(__file__))
while not os.path.isdir(os.path.join(_p, "src")) and os.path.dirname(_p) != _p:
    _p = os.path.dirname(_p)

from common.console import ensure_utf8_stdout
from swdbg import breakpoint          # 断点写法 + 源码侧事实(不连探针的那一半住 swdbg)
from swdbg import csrc                # C 源码结构索引(函数定义行/函数体那几行), 断点换算要用
from swdbg import gdbinit             # 锚点 → 地址(离线地图), 与运行时同一个换算
from swdbg.breakpoint import normalize, text


def anchors_of(py):
    """测试脚本 → (断点清单, 问题清单)。断点清单 [(标签, **断点写法**, (变量...)), ...]。

    断点写法原样交出去(`("TaskFreeze.c", 823)` / `("call", 函数, 被调, 1)` / `("func", 名)`),
    换算成 `文件:行` 是 `SrcIndex.resolve` 的事 —— 因为 `("call", …)` 那种写法**不写文件名**,
    得先去源码根里找出定义那个函数的 `.c`。
    """
    with open(py, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=py)
    const = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            try:
                const[node.targets[0].id] = ast.literal_eval(node.value)
            except Exception:
                pass
    out, problems = [], []
    for name in sorted(const):
        if not name.startswith("BP_"):
            continue
        suffix = name[3:]
        vname = "VARS_" + suffix
        try:
            spec = normalize(const[name])
        except ValueError as e:
            problems.append("%s %s" % (name, e)); continue
        if vname not in const:
            problems.append("%s 找不到配对的 %s(断点查不到变量)" % (name, vname)); continue
        vs = const[vname]
        if not vs:
            problems.append("%s 的 %s 是空的(断点没说要读什么)" % (name, vname)); continue
        out.append((suffix, spec, tuple(str(v) for v in vs)))
    return out, problems


def parse_anchor_arg(s):
    """`文件:行号:变量1,变量2` → (文件, 行号, (变量...))。形状不对就 ValueError。"""
    parts = s.split(":")
    if len(parts) < 3:
        raise ValueError("要写成 文件:行号:变量1,变量2 —— 收到 %r" % s)
    fname, line, names = ":".join(parts[:-2]), parts[-2], parts[-1]
    vs = tuple(v.strip() for v in names.split(",") if v.strip())
    if not fname or not vs:
        raise ValueError("文件名或变量为空: %r" % s)
    return fname, int(line), vs


def find_src(root, fname):
    """`.c` 名 → 真路径。BP 里只写文件名(可带相对目录), 所以按 basename 在源码根下递归找。"""
    hits = []
    for dirpath, _dirs, files in os.walk(root):
        if fname in files:
            hits.append(os.path.join(dirpath, fname))
    return hits


class SrcIndex(object):
    """断点写法 → (`.c` 的真路径, 行号)。

    为什么非有不可: `("call", 函数, 被调, 第几处)` 这种断点**不写文件名**(名字才是它的身份),
    而"这个变量在断点之前赋过值没有"只有拿到源码行才问得出来。所以得先找出定义那个函数的 `.c`。
    `Check_BillFrezY` 一个函数被 4-7 / 11-1 / 11-2 三家脚本用着 —— 不缓存就把整个源码根读三遍。

    `("call"/"prev"/"func")` 那三种的行号**不是从 `.c` 文本上猜的**: 锚点指的是二进制里的某条
    指令("第 n 处调用之后那条"), 它属于哪一源码行只有 `.out` 的 DWARF 行表答得出 —— 拿源码
    文本来推, 实测全固件 5599 处可判定的调用里错 1142 处。所以那三种走 `map`(见 `map` 那段),
    换算是 `swdbg.gdbinit.resolve_spec` —— **与运行时同一个实现**, 检查器核的才是运行时真停的
    那一条指令。没给 `.out` 时那三种一律进 problems(不猜、不静默跳过)。
    """

    def __init__(self, root, out=None, map=None):
        self.root = root
        self.map = map             # 离线地图(`swdbg.gdbinit.offline_map` 的产物); 没给 = 解不了锚点
        self.out = out
        self._paths = None
        self._code = {}          # 路径 -> code_lines(csrc.load 的产物)
        # 提醒(**不是** problem): 断点仍然核过了, 只是可能对到了同名的另一份文件上。
        self.warn = []

    def paths(self):
        if self._paths is None:
            out = []
            for dp, _d, fs in os.walk(self.root):
                for f in sorted(fs):
                    if f.endswith(".c"):
                        out.append(os.path.join(dp, f))
            self._paths = out
        return self._paths

    def code(self, path):
        if path not in self._code:
            self._code[path] = csrc.load(path)[0]
        return self._code[path]

    def build_map(self, funcs=()):
        """把离线地图建起来(`.out` 上的那一张, 见 `swdbg.gdbinit.offline_map`)。

        没给 `.out`、或它建不起来 → `map` 留 None 并返回问题清单 —— 那之后每条锚点写法都会
        进 problems。**不退回"从 .c 文本上猜行号"**: 那种猜法实测错两成, 而它错的样子与"核过了"
        一模一样(见本文件头部)。
        """
        if self.map is not None:
            return []
        if not self.out:
            return ["没给 `.out` —— 锚点写法(call/prev/func)落在哪一源码行要从它反查; "
                    "用 --out 指定, 或让本脚本按卡带声明找"]
        try:
            self.map = gdbinit.offline_map(self.out, funcs)
        except (gdbinit.GdbError, OSError) as e:
            return ["离线地图建不起来(%s): %s" % (self.out, str(e).splitlines()[0])]
        return []

    def srcpath(self, fname):
        """`.c` 名 → 真路径(同名多份时用第一份并**记一条 warn**)。"""
        hits = find_src(self.root, os.path.basename(str(fname)))
        if not hits:
            raise ValueError("源码根下找不到 %s" % fname)
        if len(hits) > 1:
            w = ("%s 在源码根下有 %d 份同名文件, 用第一份(%s) —— 断点可能对错了文件"
                 % (fname, len(hits), hits[0]))
            if w not in self.warn:
                self.warn.append(w)
        return hits[0]

    def resolve(self, spec):
        """断点写法 → `(路径, 行号)`。绕不到源码行上就抛 `ValueError`(调用方记进 problems)。"""
        if spec[0] == "line":
            return self.srcpath(spec[1]), spec[2]
        if self.map is None:
            raise ValueError("%s 解不了: 没给 `.out` —— 锚点落在哪一源码行要从它反查(见本文件头部)"
                             % text(spec))
        try:
            hit = gdbinit.resolve_spec(self.map, spec)      # 与运行时同一个换算(单一事实源)
        except gdbinit.GdbError as e:
            raise ValueError(str(e).splitlines()[0])
        src = gdbinit.src_at(self.map, hit["addr"])
        if not src:
            raise ValueError("%s 落在 0x%X, 而行表里没有覆盖它的源码行" % (text(spec), hit["addr"]))
        fname, line = src.rsplit(":", 1)
        return self.srcpath(fname), int(line)


def _rel(path, base):
    """相对路径, **跨盘符时不炸**(`os.path.relpath` 在 Windows 上跨盘抛 ValueError)。

    本脚本的用途就是"把任意文件指给它看", 指到别的盘是常事 —— 在打印一行标题上崩掉,
    等于这条工具在最需要它的时候(临时拿一个清单来核)直接不可用。
    """
    try:
        return os.path.relpath(path, base)
    except ValueError:
        return path


def src_root_from_cartridge():
    """默认源码根: 读本仓卡带声明。**只在这里** import project —— 本脚本要能拿去探任何表。"""
    return _cartridge().get("src_root") or ""


def out_from_cartridge():
    """默认 `.out`: 同一个来源(锚点落在哪一源码行要从它的行表反查)。"""
    return _cartridge().get("out") or ""


def _cartridge():
    """卡带声明的固件输入(`firmware.inputs()`); 取不到(不在本仓里/没卡带) → 空表。"""
    try:
        from project import firmware
        return firmware.inputs()
    except Exception:
        return {}


def collect(scripts, src_root, given=(), out=None):
    """断点体检的**结构化**结果(`--json` 给程序吃)。

    为什么要有这个"可被程序吃"的出口: 本脚本的文本输出是给人读的, 而检查要拿它**自动判**。
    检查去解析那些中文字串就又是一种脆弱耦合 —— 给一份 JSON, 契约就一条。

    返回 {"src_root", "problems": [...], "rows": [ {script, tag, file, line, func, range,
         vars: [ {name, first_seen, last_before, assigned_at, is_param, passed_before, ...} ]} ]}
    **仍然只装事实, 没有一个 PASS/FAIL** —— 判"这个断点可不可信"是**检查**的事(它有判据口径),
    不是本模块的事(它只负责如实报出赋值点)。分工和文本路径完全一样。
    """
    problems, rows = [], []
    idx = SrcIndex(src_root, out=out)
    per, want = [], []
    for py in scripts:
        got, probs = anchors_of(py)
        per.append((py, got, probs))
        for _suffix, spec, _vs in got:
            if spec[0] != "line" and spec[1] not in want:
                want.append(spec[1])
    if want:
        problems += idx.build_map(want)
    for py, got, probs in per:
        for p in probs:
            problems.append("%s: %s" % (_rel(py, _p), p))
        by_path = {}
        for suffix, spec, vs in got:
            try:
                path, line = idx.resolve(spec)
            except ValueError as e:
                problems.append("%s: 断[%s] %s" % (_rel(py, _p), suffix, e))
                continue
            by_path.setdefault(path, []).append((suffix, line, vs))
        for path in sorted(by_path):
            for r in breakpoint.facts(path, by_path[path]):
                r["script"] = _rel(py, _p)
                rows.append(r)
    for fname, line, vs in given:
        try:
            path, line = idx.resolve(("line", fname, line))
        except ValueError as e:
            problems.append("--anchors 给的断点: %s" % e)
            continue
        for r in breakpoint.facts(path, [(str(line), line, vs)]):
            r["script"] = "(命令行 --anchors)"
            rows.append(r)
    return {"src_root": src_root, "problems": problems, "warnings": idx.warn, "rows": rows}


def main():
    ensure_utf8_stdout()
    argv = sys.argv[1:]
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__); return 0
    as_json = "--json" in argv
    if as_json:
        argv.remove("--json")
    src_root, given, out = "", [], ""
    while "--out" in argv:
        i = argv.index("--out")
        if i + 1 >= len(argv):
            print("!! --out 后面要跟 .out 的路径"); return 2
        out = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    while "--src-root" in argv:
        i = argv.index("--src-root")
        if i + 1 >= len(argv):
            print("!! --src-root 后面要跟路径"); return 2
        src_root = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    # `--anchors` 可以给多次(每条一个断点), 与位置参数(测试脚本)**可以同时给**
    while "--anchors" in argv:
        i = argv.index("--anchors")
        if i + 1 >= len(argv):
            print("!! --anchors 后面要跟 文件:行号:变量1,变量2"); return 2
        try:
            given.append(parse_anchor_arg(argv[i + 1]))
        except ValueError as e:
            print("!! %s" % e); return 2
        argv = argv[:i] + argv[i + 2:]
    if argv and (argv[0].startswith("-")):
        print("!! 不认识的开关: %s" % " ".join(argv)); return 2
    if argv:
        scripts = argv
    elif given:
        scripts = []                       # 只用命令行喂的断点, 不去扫测试脚本
    else:
        # 测试脚本目录 **由卡带声明**(`project/<表>.py` 的 `TESTS_DIR`) —— 2026-09-14 前这里写死
        # `仓根/project/tests`。写死布局的下场很隐蔽: 扫不到就当成"没有断点要查", 一声不响地空过。
        from common import profile
        _tdir = profile.resolve("TESTS_DIR") or os.path.join(_p, "project", "tests")
        scripts = [os.path.join(dp, f)
                   for dp, _d, fs in os.walk(_tdir)
                   for f in sorted(fs) if f.startswith("_test_") and f.endswith(".py")]
    if not scripts and not given:
        print("!! 没找到 _test_*.py, 也没给 --anchors"); return 2
    if not out:
        out = out_from_cartridge()
        if out and not as_json:
            print("地图: %s (来自卡带 project/firmware.py)" % out)
    if not src_root:
        src_root = src_root_from_cartridge()
        if src_root and not as_json:
            print("源码根: %s (来自卡带 project/firmware.py)" % src_root)
        elif not src_root:
            print("!! 拿不到源码根 —— 用 --src-root 指定(卡带没声明或不在本仓)")
            return 2
    if not os.path.isdir(src_root):
        print("!! 源码根不在盘上: %s" % src_root); return 2

    # `--json`: 一条契约给程序吃。**它不做任何判红绿** ——
    # 和文本路径一样只报事实; "这个断点可不可信"由检查按自己的判据口径去判。
    if as_json:
        import json
        r = collect(scripts, src_root, given, out)
        r["ok"] = not r["problems"]
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r["ok"] else 1

    rc, idx, per, want = 0, SrcIndex(src_root, out=out), [], []
    for py in scripts:
        got, problems = anchors_of(py)
        per.append((py, got, problems))
        for _suffix, spec, _vs in got:
            if spec[0] != "line" and spec[1] not in want:
                want.append(spec[1])
    for w in idx.build_map(want):
        print("!! %s" % w); rc = 1
    for py, got, problems in per:
        print("\n== %s ==" % _rel(py, _p))
        for p in problems:
            print("   !! %s" % p); rc = 1
        if not got:
            if not problems:
                print("   (脚本里没有 BP_* 断点)")
            continue
        by_path = {}
        for suffix, spec, vs in got:
            try:
                path, line = idx.resolve(spec)
            except ValueError as e:
                # 换算不到源码行 = 这个断点**核不了** —— 说出来, 别让它悄悄溜过去
                print("   !! 断[%s] %s(%s)" % (suffix, e, text(spec)))
                rc = 1
                continue
            by_path.setdefault(path, []).append((suffix, line, vs))
        for path in sorted(by_path):
            print(breakpoint.render(breakpoint.facts(path, by_path[path])))

    # 命令行喂进来的断点(文档/xlsx 上抄的清单, 还没进任何脚本)
    if given:
        print("\n== 命令行给的断点(--anchors) ==")
        rows = []
        for fname, line, vs in given:
            try:
                path, line = idx.resolve(("line", fname, line))
            except ValueError as e:
                print("   !! %s" % e); rc = 1
                continue
            rows += breakpoint.facts(path, [(str(line), line, vs)])
        if rows:
            print(breakpoint.render(rows))
    for w in idx.warn:
        print("   -- %s" % w)
    return rc


if __name__ == "__main__":
    sys.exit(main())
