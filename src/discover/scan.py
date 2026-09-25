# -*- coding: utf-8 -*-
"""
discover/scan.py —— **找目标**: 一个目录/一批文件 → "要探哪几块表、各喂哪四样输入"

为什么要有它
------------
`_init_meter.py` 一次只探一块表, 四样输入(`--out`/`--src`/`--ewp`/`--doc`)全靠人**手打**。
本模块把"手打"换成"找出来": 给一个目录, 它自己认出里面有几个工程、每块的 `.out` 在哪、
源码根在哪。批量探测(`scripts/_probe_all.py`)因此可能。

本事全在这层 —— 脚本只是壳(仓规: "测试脚本 = 薄操作清单; 非平凡函数一律进库")。

`.out` 凭什么找得准: 以 `.ewp` 的 `ExePath` 为准
--------------------------------------------
**不是**盲目递归找 `*.out`。实测三工程的目录布局**各不相同**, 靠猜必错:

    APP   Project\\X.ewp        + $PROJ_DIR$\\..\\Build\\X\\   → Build\\X\\X.out
    8611  Project\\X.ewp        + Debug\\Exe                   → Project\\Debug\\Exe\\X.out
    Boot  IAR_EWARM\\X.ewp      + Debug\\Exe                   → IAR_EWARM\\Debug\\Exe\\X.out

但三者的 `.ewp` 里都写着 `ExePath`, 于是同一条规则通吃:

    out = normpath(ewp_root / <ExePath> / (ewp 主干 + ".out"))

IAR 对 `ExePath` 的语义是"可执行文件放哪", 所以**以它为准**与 `source.py` "`.ewp` 是
'哪些文件真编译了'的权威"是同一个原则: 工程的自我声明 > 扫描启发式。

⚠ `ExePath` 在**每个 `.ewp` 里出现两次**(Debug 在前、Release 在后; 已亲验三个工程)。
  Release 那条通常**没有产物落盘**。所以规则是"**文档序第一个 `isfile` 命中的**",
  而不是"第一个" —— 否则会指向一个不存在的 Release 目录。
  多个都命中时 notes 会写明"另有 Release 的也在", 让人能自己判断拿没拿错版本。

`src_root` 凭什么: `.ewp` 文件清单的**公共祖先**
------------------------------------------------
**不是** `dirname(ewp)`。实测 8611 的 `.ewp` 在 `Project/`, 而 77 个源文件全在 `Source/` 下
—— 拿 `dirname(ewp)` 当根, 报告里每条足迹都要写成 `..\\Source\\App\\main.c:88`, 既丑又白白
多泄一层目录名。取公共祖先则得到 `App/main.c:88`。

公共祖先是**唯一自洽**的答案: 它只依赖"我实际索引了哪些文件", 与 `.ewp` 摆在哪无关; 而且
它天然是被索引集合的祖先, 所以**足迹里永远不会有 `..`**(这条是硬保证, 不用另加判断)。
"往上一两层当工程根"这种启发式没有通用判据——APP 的工程根正好是公共祖先, 8611 的却还高一层,
猜错就把所有相对路径写歪。

退化(清单为空 / 跨盘 / 公共祖先是盘根)时才回退 `dirname(ewp)`, 并把原因记进 notes。

不认表(硬边界)
--------------
本模块与 `discover/` 其余部分一样**不认识具体表**: 不读 `project/`、不加载画像、
不 import `meterlib`/`swdbg`。它只认**路径与 XML**, 不认哪个芯片是管理芯。
索引里因此只有镜像名, 没有"这块是管理芯"这种判断——那是 `project/` 的知识, 不属这里。

只吃 stdlib 与 `discover.source`(纯文本解析), **顶层不 import `discover.elf`**
(`elf` 要 pyelftools) —— 于是本模块只做路径与文本解析, 与探针/硬件无关。
"""
from __future__ import print_function

import io
import os
import re


from discover import source          # 复用 $PROJ_DIR$ 替换与 .ewp 清单(不重写第二份)

__all__ = ["Target", "TargetResult", "scan", "check_plan", "describe_target",
           "render_index", "write_index", "ewp_exe_paths", "ewp_candidates", "ewp_out",
           "src_root_of", "default_doc", "last_skipped",
           "EXE_EXTS", "DEFAULT_DEPTH", "PRUNE_DIRS"]

# 最近一次 `scan()` 跳过了哪些东西, [(路径, 为什么)]。
# ⚠ 这是**模块级**的, 不是挂在 `scan` 函数上的属性 —— 踩过: 原先写 `scan.skipped = skipped`,
#   设的是**函数对象**的属性, 而调用方 `getattr(scan_module, "skipped")` 查的是**模块**属性,
#   于是"为什么跳过了某块表"这条信息一直在被**静默丢弃**(真表那次恰好零跳过, 所以没暴露)。
#   跳过理由是这个工具最有用的输出之一(它会告诉你"这块表为什么没被探"), 丢掉最不该。
LAST_SKIPPED = []


def last_skipped():
    """最近一次 `scan()` 的跳过清单 [(路径, 原因)]。空 = 一个都没跳。"""
    return list(LAST_SKIPPED)

EXE_EXTS = (".out", ".axf", ".elf")
DEFAULT_DEPTH = 4          # 实测 .out 最深的在第 4 层(Boot: 工程/IAR_EWARM/Debug/Exe/)
SKIP_MARK = ".bak"         # basename 含它就跳过 —— 与 source.index 扫目录回退同一判据
PRUNE_DIRS = {".git", ".svn", "__pycache__", ".vscode", "node_modules", "$RECYCLE.BIN"}
# 目录递归的剪枝。**刻意不把本仓的目录名写进去**: 那是路径偏好, 不是通用规则;
# 批量入口一旦被误指到 C:\ 或用户目录, 靠这张表 + --depth 上限兜住, 不靠猜。


# ============================ 目标 ============================

class Target(object):
    """一个探测目标 = 一块表的**一个镜像**(`.out`), 连同它那四样输入。

    用带 `__slots__` 的普通类而不是 dict: dict 在这里的失败模式是**拼错的键静默变 None**
    (与 `discover/__init__._LAZY` 那条注释反对的是同一种), 而属性拼错直接 AttributeError。
    """

    __slots__ = ("name", "kind", "out", "ewp", "src_root", "doc",
                 "config", "config_notes", "missing", "notes", "explicit")

    def __init__(self, name, kind, out=None, ewp=None, src_root=None, doc=None,
                 config=None, config_notes=None, missing=None, notes=None,
                 explicit=False):
        self.name = name                  # 表名 = dossier.meter_name(out)
        self.kind = kind                  # "ewp" | "out"
        self.out = out
        self.ewp = ewp
        self.src_root = src_root
        self.doc = doc
        self.config = config              # 选中的 ExePath 属于哪个配置(Debug/Release/None)
        self.config_notes = config_notes or []
        self.missing = list(missing or [])   # "out"/"ewp"/"src"/"doc" 的子集
        self.notes = list(notes or [])       # 给人看的 caveat
        self.explicit = explicit             # 是否由命令行**显式指名**(决定缺 .out 是不是前置错)

    def report_path(self, to_dir=None):
        from discover import dossier
        d = to_dir or dossier.DEFAULT_DIR
        return os.path.join(d, "%s_探测.md" % self.name)

    def __repr__(self):
        return "<Target %s %s%s>" % (self.name, self.kind,
                                     (" missing=%s" % self.missing) if self.missing else "")


class TargetResult(object):
    """一个目标跑完之后的结果(给汇总与索引渲染用)。"""

    __slots__ = ("target", "status", "report", "syms", "sections", "gaps",
                 "src_files", "sha256", "elapsed", "error", "live", "live_ok")

    # status: OK / SKIP-EXISTS / SKIP-NO-OUT / FAIL
    def __init__(self, target, status, report=None, error=None):
        self.target = target
        self.status = status
        self.report = report
        self.error = error
        self.syms = self.sections = self.gaps = self.src_files = 0
        self.sha256 = ""
        self.elapsed = 0.0
        self.live = False
        self.live_ok = None         # True/False/None(未采)

    @property
    def name(self):
        return self.target.name


# ============================ `.ewp` → ExePath → `.out` ============================

def ewp_exe_paths(ewp_path):
    """`.ewp` → [(配置名, ExePath 原始串)], **文档序**。解析失败/无此 option → []。

    配置名 = 该 option 之前**最近**的一个 `<configuration><name>X</name>` 的 X, 取不到为 ""。
    实测三个工程都是 Debug 在前、Release 在后。
    """
    try:
        with io.open(ewp_path, encoding="utf-8", errors="replace") as f:
            xml = f.read()
    except Exception:
        return []
    out = []
    for m in re.finditer(r"<name>\s*ExePath\s*</name>\s*<state>\s*([^<]*)</state>", xml, re.I):
        cfg = re.findall(r"<configuration>\s*<name>\s*([^<]+?)\s*</name>", xml[:m.start()], re.I)
        out.append(((cfg[-1] if cfg else ""), m.group(1).strip()))
    return out


def ewp_candidates(ewp_path):
    """`.ewp` → [(配置, ExePath 原始串, 候选产物绝对路径)], **文档序**, 全部展开。

    一个 `.ewp` 通常**两条 ExePath**(Debug/Release), 所以候选不止一个。这个函数把
    "这个工程**可能**产出的所有 .out"一次列全 —— `ewp_out` 从中挑一个, `scan` 用整份清单
    认出"某个 .out 只是本工程的**另一个配置**"(见 scan 里对非选中配置的处理)。
    """
    stem = os.path.splitext(os.path.basename(ewp_path))[0]
    out = []
    for cfg, state in ewp_exe_paths(ewp_path):
        if not state.strip():
            continue                # 空 ExePath 不该推出 dirname(ewp)/x.out
        d = source.resolve_ewp_rel(state, ewp_path)
        for ext in EXE_EXTS:
            out.append((cfg, state, os.path.normpath(os.path.join(d, stem + ext))))
    return out


def ewp_out(ewp_path):
    """`.ewp` → (out 绝对路径 | None, notes:list[str], config:str|None)

    选法: 对每条 ExePath 算 `normpath(ewp_root / state / (ewp主干 + ext))`, ext 依次试
    `.out`/`.axf`/`.elf`; 取**文档序第一个 `isfile` 命中的**。
    一个都不存在 → 用文档序第一个(notes 记"未落盘")并**返回该路径**(不是 None)
    —— "没落盘"与"没这个信息"是两件事, 报告/跳过理由要能分开说。
    """
    items = ewp_exe_paths(ewp_path)
    if not items:
        return None, ["这个 .ewp 里没有 ExePath, 无法据此定位 .out"], None

    cands = ewp_candidates(ewp_path)
    if not cands:
        return None, ["这个 .ewp 的 ExePath 是空的, 无法据此定位 .out"], None

    hit = [c for c in cands if os.path.isfile(c[2])]
    chosen = hit[0] if hit else cands[0]
    notes = []
    if not hit:
        notes.append("ExePath 指向的 .out 未落盘(可能没编译过): %s" % chosen[2])
    if len(hit) > 1:
        notes.append("另有 %s 的 .out 也在磁盘上, 本次取文档序第一个(配置 %s)"
                     % ("/".join(sorted({c[0] or "?" for c in hit[1:]})), chosen[0] or "?"))
    if chosen[0]:
        notes.append("ExePath 取自配置 %s" % chosen[0])
    return chosen[2], notes, (chosen[0] or None)


def src_root_of(ewp_path, files=None):
    """`.ewp` → (src_root | None, notes:list[str]) —— 公共祖先, 退化才回退。

    files 缺省 = `D_src.ewp_files(ewp_path)`(即"真参与编译"那份清单)。
    退化判据(任一命中 → 回退 `dirname(ewp)` 并记 notes):
      · 清单为空
      · commonpath 抛 ValueError(跨盘)
      · 公共祖先是盘根(文件散到整块盘) —— 拿它当根会把足迹写成又长又泄结构的指针
    """
    if files is None:
        files = source.ewp_files(ewp_path)
    fallback = os.path.dirname(os.path.abspath(ewp_path))
    if not files:
        return fallback, ["`.ewp` 没解析出参与编译的源文件 → 源码根回退为 .ewp 所在目录"]
    try:
        cp = os.path.commonpath([os.path.dirname(os.path.abspath(f)) for f in files])
    except ValueError:
        return fallback, ["源文件跨盘 → 取不了公共祖先, 源码根回退为 .ewp 所在目录"]
    if os.path.dirname(cp) == cp:          # 盘根, 如 "E:\\"
        return fallback, ["源文件的公共祖先是盘根 → 太宽, 源码根回退为 .ewp 所在目录"]
    return cp, []


# ============================ 目录 → 目标 ============================

def _walk_files(root, depth):
    """root 下递归至多 depth 层, 剪掉 PRUNE_DIRS 与点目录 → 文件绝对路径。"""
    root = os.path.abspath(root)
    base = root.rstrip("\\/").count(os.sep)
    for dp, dns, fns in os.walk(root):
        d = dp.rstrip("\\/").count(os.sep) - base
        if d >= depth:
            dns[:] = []
        else:
            dns[:] = [x for x in dns if x not in PRUNE_DIRS and not x.startswith(".")]
        for fn in fns:
            yield os.path.join(dp, fn)


def _collect(root, depth):
    """> 一个目录里所有 .ewp / 可执行产物(都排除 .bak)。"""
    ewps, outs = [], []
    for ap in _walk_files(root, depth):
        fn = os.path.basename(ap).lower()
        if SKIP_MARK in fn:
            continue                       # *.bak* 一律不作候选(踩过 .out.bak_formal_*)
        if fn.endswith(".ewp"):
            ewps.append(ap)
        elif fn.endswith(EXE_EXTS):
            outs.append(ap)
    return sorted(ewps), sorted(outs)


def _find_ewp_for_out(out, depth):
    """给一个**直接指名**的 `.out`, 往上找"解析出来正好是它"的那个 `.ewp`。

    只查每一级祖先目录里的 `*.ewp` 与 `*/*.ewp` —— 实测三种布局都落在这个范围内
    (`.ewp` 要么在 out 的祖先目录里, 要么在祖先的下一层工程子目录里)。
    **比名字像不算数, 必须解析出的路径相等** —— 否则会认错工程。
    """
    cur = os.path.dirname(os.path.abspath(out))
    for _ in range(depth + 1):
        cands = []
        for pat in ("*.ewp", os.path.join("*", "*.ewp")):
            try:
                import glob
                cands += glob.glob(os.path.join(cur, pat))
            except Exception:
                pass
        for c in cands:
            if SKIP_MARK in os.path.basename(c).lower():
                continue
            got, _n, _c = ewp_out(c)
            if got and os.path.normpath(got) == os.path.normpath(os.path.abspath(out)):
                return c
        nxt = os.path.dirname(cur)
        if nxt == cur:
            break
        cur = nxt
    return None


def _from_ewp(ewp, explicit=False, doc=None, src=None):
    """一个 `.ewp` → Target(或 None + 跳过原因: 没有 .out / .out 未落盘)。"""
    out, onotes, cfg = ewp_out(ewp)
    if out is None:
        return None, "这个 .ewp 里没有 ExePath(或为空)"
    if not os.path.isfile(out):
        return None, "ExePath 解析出的 .out 未落盘(可能没编译过): %s" % out
    root, snotes = src_root_of(ewp)
    from discover import dossier
    return Target(name=dossier.meter_name(out), kind="ewp", out=out, ewp=ewp,
                  src_root=src or root, doc=doc,
                  config=cfg, config_notes=onotes, notes=(onotes + snotes),
                  explicit=explicit), None


def _from_out(out, explicit=False, doc=None, src=None):
    """一个**孤儿** `.out`(没有 `.ewp`)→ Target。**不猜 src_root**。"""
    from discover import dossier
    ewp = _find_ewp_for_out(out, DEFAULT_DEPTH)
    if ewp:
        t, _why = _from_ewp(ewp, explicit=explicit, doc=doc, src=src)
        if t:
            return t
    t = Target(name=dossier.meter_name(out), kind="out", out=out, ewp=None,
               src_root=src, doc=doc, explicit=explicit,
               missing=[m for m in ("ewp", "src") if m != "src" or not src],
               notes=["没有对应 .ewp → 源码足迹那几列只能空着(往上找了, 没找到"
                      "解析出这个 .out 的工程)"])
    if src:
        t.notes.append("源码根由 --src 指定(不是从 .ewp 推的)")
    return t


def scan(paths, doc=None, doc_default=True, to_dir=None, depth=DEFAULT_DEPTH,
         progress=None):
    """一批路径(目录 / `.ewp` / `.out`, 混着给也行) → list[Target]。

    规则:
      · 目录      → 递归至多 depth 层收 `.ewp` 与产物; 一个 `.ewp` 一个目标,
                    剩下的产物(没被任何 .ewp 认领)作**孤儿目标**。
      · `.ewp`    → 直接出目标; `.out` 缺失 → 也出 Target 但 missing 含 "out"
                    (`explicit=True`, 由 `check_plan` 升级成前置错)。
      · `.out`    → 往上找解析出它的 `.ewp`; 找不到 → 孤儿。
    返回按 name 排序、按 realpath(out) 去重。
    """
    targets, skipped = [], []

    def _push(t):
        if t is None:
            return
        targets.append(t)

    for p in paths:
        ap = os.path.abspath(p)
        low = os.path.basename(ap).lower()
        if os.path.isdir(ap):
            ewps, outs = _collect(ap, depth)
            if progress:
                progress("扫到 %d 个 .ewp / %d 个产物" % (len(ewps), len(outs)))
            claimed = set()
            declared = {}                          # realpath(候选产物) → ewp, 含**所有**配置
            for ewp in ewps:
                for _cfg, _st, p in ewp_candidates(ewp):
                    declared.setdefault(os.path.realpath(p), ewp)
                t, why = _from_ewp(ewp, doc=doc)
                if t is None:
                    skipped.append((ewp, why))
                    continue
                claimed.add(os.path.realpath(t.out))
                _push(t)
            for out in outs:
                rp = os.path.realpath(out)
                if rp in claimed:
                    continue
                # 被某个 .ewp 声明过、但**不是它选中的那一个** → 是同一个工程的另一个配置
                # (典型: 三个真工程都声明了 Debug+Release; Debug 是被选中的那个)。
                # 这种 .out **不能**当独立目标: 它与选中项**同主干** → 同一份报告名 → 后写的
                # 静默覆盖先写的(实测合成树上 ProjB 直接撞出重名, 整批被 check_plan 拦下)。
                # 真要探它, 得**单独指名**这个 .out —— 那时它是显式目标, 不走这条。
                if rp in declared:
                    skipped.append((
                        out, "属于 %s 的**另一个配置**(非文档序第一个命中项) —— 同一工程只取一个, "
                             "否则两份报告同名互相覆盖; 要它请单独把该 .out 指给本脚本"
                             % os.path.basename(declared[rp])))
                    continue
                t = _from_out(out, doc=doc)        # 真·孤儿(没有任何 .ewp 声明过它)
                if t.ewp:
                    claimed.add(rp)
                _push(t)
        elif low.endswith(".ewp"):
            t, why = _from_ewp(ap, explicit=True, doc=doc)
            if t is None:
                # 显式指名却没有 .out → 仍然出目标(带 out 路径以便报错), 由 check_plan 拦
                from discover import dossier
                out, onotes, cfg = ewp_out(ap)
                t = Target(name=dossier.meter_name(out or ap), kind="ewp", out=out, ewp=ap,
                           src_root=src_root_of(ap)[0], doc=doc, config=cfg,
                           config_notes=onotes, notes=onotes + [why],
                           missing=(["out"] if not (out and os.path.isfile(out)) else ["src"]),
                           explicit=True)
                targets.append(t)
                continue
            _push(t)
        elif low.endswith(EXE_EXTS):
            _push(_from_out(ap, explicit=True, doc=doc))
        else:
            skipped.append((ap, "既不是目录, 也不是 .ewp/.out → 跳过"))

    # 去重(同一 .out 因软链/.. 写法不同而重复出现)
    seen, uniq = set(), []
    for t in targets:
        key = os.path.realpath(t.out) if t.out else ("ewp", os.path.realpath(t.ewp or t.name))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(t)

    # doc: 全局一份(总纲不随工程变), 一个默认值 + 一个覆盖点
    dflt = default_doc() if doc_default else None
    for t in uniq:
        if t.doc is not None:
            continue
        if dflt:
            t.doc = dflt
        else:
            t.missing.append("doc")
            t.notes.append("总纲未提供(默认位置也没有) → §5 整节留空")
    uniq.sort(key=lambda t: t.name)
    del LAST_SKIPPED[:]
    LAST_SKIPPED.extend(skipped)
    return uniq


def default_doc():
    """总纲的默认位置 —— **由卡带声明**(画像的 `MASTER_DOC`), 本函数只解析, 不认布局。

    2026-09-14 前这里写着 `仓根/project/knowledge/对表操作总纲.md`(外加一条"上一级"的兜底) —— 那是**通用探测器
    认了某块表的文件布局**: 本包的第一性质是"任何的表都能用这个来探测", 而"总纲放哪"是那块表
    自己的事。现在总纲随卡带走(`project/knowledge/对表操作总纲.md`), 位置由卡带声明。

    `common.profile` 是**中立槽**(不是 `project`), 取不到(没装卡带 = 在探一块陌生表)就返回 None
    —— 报告照常出, §协议语义对照 那节按既有规则明写"未提供"。显式 `--doc` 永远优先。
    """
    from common import profile
    return profile.resolve("MASTER_DOC")


# ============================ 计划校验(动手之前的那道判定) ============================

def check_plan(targets, live=False, live_only=None):
    """build 之前必须过这道判定 → problems:list[str], 空 = 通过。

    这几条都是**只有批量才会犯**的错, 单表手工输入碰不到:
      · 空目标集
      · **重名** —— 两块不同的表主干相同 → 同一份 `project/knowledge/探测报告/<名>_探测.md`, 后写的静默覆盖先写的
      · `--live` 给了多个目标 —— 物理上只有一个 J-Link, 会把同一块表的活体数据贴到 N 个名字上
      · `live_only` 不命中恰好 1 个
      · 显式指名的目标缺 `.out`
    """
    probs = []
    if not targets:
        return ["没找到任何目标 —— 给个目录, 或用 --depth 放宽; 也看看上面的跳过理由"]

    by_name = {}
    for t in targets:
        if t.out and not os.path.isfile(t.out):
            by_name.setdefault(t.name, []).append(t)
    for name, ts in sorted(by_name.items()):
        probs.append("目标 %s 的 .out 不存在: %s" % (name, ts[0].out))

    names = {}
    for t in targets:
        names.setdefault(t.name, []).append(t)
    for name, ts in sorted(names.items()):
        if len(ts) > 1:
            probs.append("**重名**: %d 个目标的表名都是 %s —— 它们会写进同一份报告互相覆盖。"
                         "用 --only 挑一个, 或 --to-dir 分开存。涉及: %s"
                         % (len(ts), name, " | ".join(sorted({t.out or t.ewp for t in ts}))))

    if live_only is not None:
        hit = [t for t in targets if t.name == live_only]
        if len(hit) != 1:
            probs.append("--live-only %s 命中了 %d 个目标(必须恰好 1 个)" % (live_only, len(hit)))
    elif live:
        if len(targets) > 1:
            probs.append(
                "--live 不适用于批量: 本次选中 %d 个目标, 但物理上只有一个 J-Link / 一块表。"
                "照跑会把同一块表的活体数据贴到 %d 个名字上, 造出 %d 份'有活体证据'的假报告。"
                "要采活体请二选一: --live-only <表名>(只让这一个连表) 或 --only <表名> --live"
                % (len(targets), len(targets), len(targets)))

    for t in targets:
        if t.explicit and "out" in t.missing:
            probs.append("显式指名的目标 %s 缺 .out —— 点名要的东西不存在, 不能假装成功" % t.name)
    return probs


# ============================ 给人看: --dry 与索引 ============================

def describe_target(t, verbose=False, to_dir=None):
    """一个目标 → 人读的多行文本(--dry 用)。每行 3 空格缩进, 与 _init_meter 的打印风格一致。

    `to_dir` 要**跟着真正写盘的那个目录传进来** —— 否则"报告"那一行显示的是兜底默认目录,
    而实际写盘用的是 `--to-dir`/卡带声明的位置, 人照着这行去找文件会扑空(2026-09-14 修)。
    """
    L = ["[目标] %s   (%s)" % (t.name, t.kind)]
    if t.out and os.path.isfile(t.out):
        L.append("   .out    %s   (%s B)" % (t.out, "{:,}".format(os.path.getsize(t.out))))
    else:
        L.append("   .out    %s" % (t.out or "—"))
    if t.src_root:
        n = len(source.ewp_files(t.ewp)) if t.ewp else 0
        L.append("   源码根  %s   (公共祖先%s)" % (t.src_root, ", %d 个编译单元" % n if n else ""))
    else:
        L.append("   源码根  — (无 .ewp, 未猜)")
    L.append("   .ewp    %s%s" % (t.ewp or "—",
                                  "" if not t.config else "   (配置 %s)" % t.config))
    L.append("   总纲    %s" % (t.doc or "— (未提供)"))
    L.append("   报告    %s" % t.report_path(to_dir))
    if t.missing:
        L.append("   缺      %s" % " ".join(t.missing))
    for n in t.notes:
        L.append("   注意    %s" % n)
    if verbose:
        # `config_notes` 是"选配置"那一支的原始产出, 而 `_from_ewp` 已经把**同样这几条**
        # 放进了 `t.notes`(= onotes + snotes), 上面"注意"一行早就原样打过了 ——
        # 这里若照单再打一遍, `--verbose` 下每个目标都会多出一行重复的
        # "ExePath 取自配置 Debug"(踩过 2026-09-10)。所以只补**还没说过**的。
        for n in t.config_notes:
            if n not in t.notes:
                L.append("   配置    %s" % n)
    return "\n".join(L)


def _cell(v):
    """markdown 表格单元: 竖线与换行必须转义, 否则一格能把整张表撑散。"""
    s = "" if v is None else str(v)
    return s.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def render_index(results, extra=None):
    """一批评测结果 → `索引.md` 全文(markdown)。

    索引是**派生品**(可随时重生成), 所以它只做一件事: 让人一眼判断
    "这次是不是漏了一块表"。**不硬编码芯片角色**(管理芯/计量芯) ——
    `discover/` 不认表, 那是 `project/` 的知识, 写进索引就是越界。
    """
    import time
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    extra = extra or {}
    L = ["# 探测报告索引", "",
         "> 由 `scripts/_probe_all.py` 生成于 %s —— 本文件是**派生**的, 可随时重生成。"
         % now, ""]

    L += ["## 本次运行", ""]
    for k in ("扫描根", "声明", "目标数", "活体", "报告目录", "日志"):
        if extra.get(k):
            L.append("| %s | %s |" % (k, _cell(extra[k])))
    L.append("")

    L += ["## 镜像一览", "",
          "| 表名 | 配置 | .out sha256 | 符号 | 段 | 空闲洞 | 源码文件 | 活体 | 状态 | 报告 |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        t = r.target
        rep = os.path.basename(r.report) if r.report else "—"
        L.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            _cell(t.name), _cell(t.config or "—"),
            (r.sha256[:12] if r.sha256 else "—"),
            r.syms or "—", r.sections or "—", r.gaps or "—", r.src_files or "—",
            _live_cell(r), _cell(r.status), _cell(rep)))
    L.append("")

    # ⚠ 这里原先写的是 `getattr(scan, "skipped", [])` —— 在本模块内, 全局名 `scan` 是**函数**,
    #   于是永远取到默认的 `[]`, "本次跳过了什么"这一整节**从来没渲染出来过**(静默丢弃)。
    #   正是上面 LAST_SKIPPED 那段注释立的规矩, 却在唯一的下游漏掉了。改成模块级的真清单。
    sk = last_skipped()
    if sk:
        L += ["## 本次跳过的(未生成报告)", "", "| 路径 | 为什么 |", "|---|---|"]
        for p, why in sk:
            L.append("| %s | %s |" % (_cell(p), _cell(why)))
        L.append("")

    # 调用方(脚本层)报上来的差异 —— 例如 `_probe_all.py --from-project` 比出来的
    # "卡带声明 vs 从 .ewp 推导"。本模块不认 project, 也不解释这些字符串, 只负责**别把它丢了**
    # (一个只有调用方知道、却没有任何槽位可放的字段, 结局一定是静默消失)。
    diffs = extra.get("声明与推导的差异")
    if diffs:
        L += ["## 声明与推导对不上", "",
              "以**声明**为准执行了, 但不一致说明有一方错了:", ""]
        for d in diffs:
            L.append("- %s" % _cell(d))
        L.append("")

    others = extra.get("未覆盖的报告")
    if others:
        L += ["## 目录里未被本次覆盖的报告", "",
              "（让「这次是不是漏了一块表」一眼可判）", ""]
        for o in others:
            L.append("- %s" % _cell(o))
        L.append("")

    L += ["## 怎么用", "",
          "每份报告的 **§7 开放问题**是建画像的任务清单; §3 符号总表带源码足迹与候选角色。",
          "",
          "⚠ 报告里**只许有符号名/地址/类型/行号指针, 不许出现源码片段或反汇编** —— "
          "这是本仓的硬约束, 出报告前逐行核(本包不给离线入口跑)。", ""]
    return "\n".join(L)


def _live_cell(r):
    if not r.live:
        return "—"
    if r.live_ok is False:
        return "!! 未采到"
    return "是"


def write_index(path, text):
    """索引落盘。**UTF-8 + `\\n` 写死, 不经 print 也不经 runlog** ——
    它是给人读的确定性产物, 不该跟终端编码/运行流水纠缠。"""
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path
