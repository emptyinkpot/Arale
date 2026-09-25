# -*- coding: utf-8 -*-
"""
discover/dossier.py —— 把四路探测并成**一份给人读的**报告: `project/knowledge/探测报告/<表名>_探测.md`。

这是本包的**唯一产物**。不是脚本、不是画像、不是 project/*.py ——
是"我和 AI 拿着它、配我现有的库, 就能给这块表写出调试固件"的那份文件。

四路料来自哪
-----------
    elf.py      .out  → 符号(名字/地址/大小/段) + DWARF(类型/volatile/声明处)
    source.py   源码  → 每个符号被谁读写的 `文件:行号`(纯指针)
    doc.py      总纲  → 帧清单 / OAD 映射 / 服务 × 打哪芯 / 厂内前置
    evidence.py 活体  → 借 swdbg 采样, 判「活 / 静止」(不给就整节留空, **不许编**)

报告骨架(§7 是灵魂)
-------------------
    §0 可复现性   .out 的 sha256 / 源码根 / 总纲 / 时刻 / 工具链 / J-Link SN / 采样参数
    §1 目标概览   这块表多大、探到多少、DWARF 有没有
    §2 内存地图   段布局 + **空闲洞**(别人往里放东西时要看) + 栈区警告
    §3 符号总表   名字/地址/大小/类型/volatile/源码足迹/活体/抽样值/候选角色  ← 主表
    §4 活体证据   谁在动、动多快、谁纹丝不动
    §5 协议语义对照  总纲怎么描述对外协议 ←→ 内部符号(按名字能对上的)
    §6 交叉验证   .symtab ↔ DWARF ↔ 段边界 三方对不上就是重大发现
    §7 开放问题 / 待判断  **下一次开会话建画像的任务清单**
    附 全量转储(默认折叠, 不碍事)

⚠ 两条铁律(违反其一这份文件就废了)
----------------------------------
1. **「探到的」与「猜的」必须分开写。** 地址/大小/类型/volatile/活体判定是探到的, 直接写;
   候选角色(WATCH/STABLE/…)是**推测**, 一律带 `(推测)` 并附判据 —— 判据就在同行那几列里,
   人不满意可以自己推翻。把猜的写成探到的, 比缺一列还坏。
2. **外泄硬约束**(CLAUDE.md: 厂商固件白盒反推, 外泄敏感): 报告里只允许出现
   **符号名、地址、类型、行号指针**。不许源码片段、不许反汇编。`source.py` 与 `elf.py`
   已经守住了各自的返回值, 本模块**只做拼接, 不新增任何源码正文**。

本体不 import project / meterlib。离线也能出报告(§4 整节留空并写明"未采活体")。
"""
from __future__ import print_function

import io
import os
import sys
import time


__all__ = ["build", "render", "write_report", "meter_name", "DEFAULT_DIR"]

# 报告目录的**兜底**默认值(2026-09-14 改)
# ---------------------------------------
# 原先写死 `仓根/探测报告`。可 `discover` 是**通用探测器**(不认表), 却认了某块表的文件布局 ——
# "报告放哪"本是**卡带**该声明的事。现在: 卡带声明的 `REPORTS_DIR` 优先(由 `_probe_all.py
# --from-project` 传下来), 本常量只是"没卡带/没给 --to-dir"时的兜底, 且落在**你跑它的地方**,
# 不假定仓布局。
DEFAULT_DIR = os.path.join(os.getcwd(), "探测报告")
GAP_MIN = 32                                    # 空闲洞小于这个字节就不值得报
TOP_N = 25                                      # §7 每类开放问题最多列几条

# 候选角色 → 判据(必须能自证, 不然就是瞎猜)。写进报告表头下方, 让人能反驳。
ROLE_RULES = [
    ("WATCH 候选(高频)", "源码里有写 + 活体高频", "计量采样量、秒级钟这类每周期都刷的"),
    ("WATCH 候选(事件驱动)", "源码里有写 + 活体低频", "多挂在校时/分钟/结算这类事件上"),
    ("STABLE 候选", "源码里有写 + 活体静止", "无显式命令不动 —— 正是 stable 类断言要的"),
    ("只读/常驻", "源码里只有读 + 活体静止", "多是配置/标定常量, 上电后不变"),
    ("疑似经指针改写", "源码里只有读, 但出现过 `&x`", "足迹不可信, 得靠活体"),
    ("只声明未使用", "源码里只有声明", "可能是预留/被改成指针访问/被 #if 掉"),
]


def meter_name(out_path):
    """`.out` 路径 → 表名(报告文件名用)。没有更深的信息可依赖, 就用文件名主干。"""
    b = os.path.basename(out_path)
    for ext in (".out", ".elf", ".axf"):
        if b.lower().endswith(ext):
            return b[: -len(ext)]
    return b


def is_ram(s):
    """一段是不是 RAM。**判据是"可写", 不是"有地址"。**

    踩过: 一开始只按 `ALLOC` 挑, 于是把 `P1 ro`(flash, 0x4000 起)也算了进去,
    §1 印出 "RAM 0x00004000 ~ 0x20014000, 524352 KB" 这种一眼假的东西。
    对 Cortex-M 来说 RAM 必然可写、flash 必然不可写, 所以 `SHF_WRITE` 是干净的判据,
    而且**不写死 0x20000000** —— 那块表要是把 RAM 映射到别处, 这里照样对。
    """
    return bool(s["flags"] & 0x2) and bool(s["flags"] & 0x1) and s["size"]


def _ram_range(secs):
    """可写 ALLOC 段 → (低, 高) 地址范围; 没有就 (None, None)。"""
    lo = hi = None
    for s in secs:
        if not is_ram(s) or not s["addr"]:
            continue
        lo = s["addr"] if lo is None else min(lo, s["addr"])
        hi = s["addr"] + s["size"] if hi is None else max(hi, s["addr"] + s["size"])
    return lo, hi


def _gaps(syms, lo, hi, min_gap=GAP_MIN):
    """符号之间未被任何符号占用的区间 —— 别人往里放探测暂存时要看这个。"""
    occ, out = [], []
    for s in sorted(syms, key=lambda x: x.addr):
        if s.size <= 0:
            continue
        if occ and s.addr < occ[-1][1]:             # 重叠(别名/联合体) 就并起来
            occ[-1][1] = max(occ[-1][1], s.addr + s.size)
        else:
            occ.append([s.addr, s.addr + s.size])
    # 两头各垫一个零宽区间, 好把"段头到第一个符号""最后一个符号到段尾"也算成洞。
    edges = ([[lo, lo]] if lo is not None else []) + occ
    edges = edges + ([[hi, hi]] if hi is not None else [])
    for (_, a1), (b0, _) in zip(edges, edges[1:]):
        if b0 - a1 >= min_gap:
            out.append({"addr": a1, "size": b0 - a1})
    return out


def _role(sym, live):
    """一个符号 → (候选角色, 判据)。**这是推测**, 判据必须能自证(报告里逐条列出反过来)。"""
    one = sym.get("src") or {}
    w, r = len(one.get("writes") or []), len(one.get("reads") or [])
    at = len(one.get("addr_taken") or [])
    v = (live or {}).get(sym["name"], {}).get("verdict")
    if w:
        if v == "静止":
            return "STABLE 候选", "有写 + 静止"
        if v == "高频":
            return "WATCH 候选(高频)", "有写 + 高频"
        if v == "低频":
            return "WATCH 候选(事件驱动)", "有写 + 低频"
        return "有写出点", "有写 + " + ("未采活体" if v is None else v)
    if r or at:
        if v == "静止":
            return "只读/常驻", "只读 + 静止"
        if at:
            return "疑似经指针改写", "只读但取过地址(&x)"
        return "只读", "只有读, " + ("未采活体" if v is None else v)
    if one.get("n_hits"):
        return "只声明未使用", "只有声明"
    return "查无足迹", "未在参与编译的源码里出现"


def build(out, src=None, doc=None, ewp=None, live=False, rounds=5, gap=0.5,
          pattern=None, progress=None, sn=None):
    """四路探测 → 结构化报告 dict(render 再把它变成 markdown)。

    每一路都是**可选**的: 只给 out 也能出一份(.out 单文件就够探出符号+类型+内存图),
    缺的那几路在报告里**明写"未提供"**, 不留空白也不编。
    """
    from discover import elf
    from discover import source

    t0 = time.time()
    rep = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "out": out,
           "src": src, "doc": doc, "ewp": ewp, "live": bool(live),
           "rounds": rounds, "gap": gap, "sn": sn,
           "caveats": [], "missing": [],
           "src_index": None, "doc_data": None, "live_data": None}

    # ---- §0 可复现性 ----
    rep["sha256"] = elf.sha256(out)
    rep["out_bytes"] = os.path.getsize(out)
    rep["python"] = sys.version.split()[0]
    try:
        import elftools
        rep["pyelftools"] = getattr(elftools, "__version__", "?")
    except Exception:
        rep["pyelftools"] = "?"
    try:
        import pylink
        rep["pylink"] = getattr(pylink, "__version__", "?")
    except Exception:
        rep["pylink"] = "未装"

    # ---- 符号(DWARF 打开会很慢, 但一次就够) ----
    reps = elf.symbols(out, pattern=pattern)
    xchk = elf.cross_check(out, pattern=pattern)
    secs = elf.sections(out)
    lo, hi = _ram_range(secs)
    rep["syms"] = [s.as_dict() for s in reps]
    rep["sections"] = secs
    # 冲突项留成 dict(不是光留名字) —— §6 要把**两边的地址并排**摆出来, 只留名字没法核。
    rep["cross"] = {"stats": xchk["stats"],
                    "addr_conflict": [x.as_dict() for x in xchk["addr_conflict"]],
                    "size_conflict": [x.as_dict() for x in xchk["size_conflict"]],
                    "size_padding": [x.as_dict() for x in xchk["size_padding"]]}
    rep["has_debug"] = elf.has_debug(out)
    rep["ram_lo"], rep["ram_hi"] = lo, hi
    rep["gaps"] = _gaps(reps, lo, hi)
    if not rep["has_debug"]:
        rep["caveats"].append("这份 .out **没有 DWARF** —— 类型/volatile/声明行号全部拿不到, "
                              "§3 那几列只能是空的。这不算探测失败, 是输入就没给。")

    # ---- §3 源码足迹 ----
    if src:
        r = source.index(src, names=[s.name for s in reps], ewp=ewp,
                        progress=(None if not progress else
                                  (lambda i, n, ap: progress("源码 %d/%d" % (i, n)))))
        rep["src_index"] = r
        rep["caveats"] += list(r["caveats"])
        rep["src_files"] = len(r["files"])
        rep["src_ewp"] = bool(r["ewp"])
    else:
        rep["missing"].append("源码目录(--src)未提供 → §3 的**源码足迹**列为空")
        rep["src_files"] = 0

    # ---- §5 总纲 ----
    if doc:
        import discover.doc            # 形参就叫 doc, 故只能走全限定名
        rep["doc_data"] = discover.doc.extract(doc)
        rep["caveats"] += list(rep["doc_data"]["caveats"])
    else:
        rep["missing"].append("总纲(--doc)未提供 → §5 整节留空")

    # ---- §4 活体 ----
    if live:
        from discover import evidence
        blocks = [(s.name, s.addr, s.size) for s in reps if s.size > 0]
        try:
            rep["live_data"] = evidence.classify(
                blocks, rounds=rounds, gap=gap,
                progress=(None if not progress else
                          (lambda i, n: progress("活体 %d/%d" % (i, n)))))
        except Exception as e:
            rep["live_data"] = None
            rep["caveats"].append("活体采样失败(J-Link/表在不在?): %s —— §4 整节留空, "
                                  "**没采就是没采**, 不许拿离线值充数" % e)
    else:
        rep["missing"].append("未开 --live → §4 整节留空(离线探测, 没采活体)")

    # ---- §3 的候选角色(推测, 但判据自证) ----
    live_d = rep["live_data"] or {}
    for s in rep["syms"]:
        s["src"] = (rep["src_index"]["index"].get(s["name"]) if rep["src_index"] else None)
        s["live"] = live_d.get(s["name"])
        s["role"], s["role_why"] = _role(s, live_d)
    rep["elapsed"] = time.time() - t0
    return rep


# ============================ 渲染 ============================
def _fmt_addr(a):
    return "—" if a is None else "0x%08X" % a


def _oad_key(cell):
    """总纲 OAD 表 `ID` 单元格 → 干净的标识符, 用来跟符号名碰。

    单元格里混着**总纲自己的 markdown 记号**: `**ID_BillFrezM=12** (`FrezData.h:35`)`,
    还有区间写法 `ID_MinuteFrez0..7=1..8`。不洗掉就永远匹配不上 —— 那是假阴性,
    不是"名字改了"(踩过)。
    """
    s = (cell or "")
    for ch in ("*", "`", "\\", '"'):
        s = s.replace(ch, "")
    for cut in ("(", "=", ".."):
        s = s.split(cut)[0]
    s = s.strip()
    return s.lower() if s[:1].isalpha() or s[:1] == "_" else ""


def _kv(rows):
    return "\n".join("| %s | %s |" % (k, v) for k, v in rows)


def _cell(v):
    """单元格 → 安全文本。`|` 会截断 markdown 表格, 换行会把表格劈成两半 —— 都挡掉。"""
    s = "—" if v is None else str(v)
    return s.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _tbl(header, rows):
    out = ["| " + " | ".join(_cell(h) for h in header) + " |",
           "|" + "|".join(["---"] * len(header)) + "|"]
    out += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def _live_cell(s):
    v = s.get("live")
    if not v:
        return "—"
    return "%s(变%d)" % (v["verdict"], v["changed"])


def _val_cell(s):
    """抽样值 —— 只在**静止**时才打印。动着的值没有意义, 打了反而误导。"""
    v = s.get("live")
    if not v or v["last"] is None:
        return "—"
    if v["verdict"] in ("高频", "低频"):
        return "(在变, 不印值)"
    return v["last"].hex(" ").upper()


def _footprint_cell(s):
    one = s.get("src")
    if one is None:
        return "—"
    if not one["n_hits"]:
        return "查无"
    bits = []
    if one["writes"]:
        bits.append("写%d" % len(one["writes"]))
    if one["reads"]:
        bits.append("读%d" % len(one["reads"]))
    if one["addr_taken"]:
        bits.append("&%d" % len(one["addr_taken"]))
    if not bits:
        bits.append("仅声明")
    if one["truncated"]:
        bits.append("截断")
    return " ".join(bits)


def _where(s, kind, n=2):
    """足迹 → `文件:行号<函数>` 字符串(纯指针)。"""
    one = s.get("src")
    if not one:
        return []
    return ["`%s:%d`%s" % (p["file"], p["line"],
                           " <%s>" % p["func"] if p["func"] else "")
            for p in (one.get(kind) or [])[:n]]


def render(rep):
    """结构化报告 → markdown 全文。"""
    name = meter_name(rep["out"])
    L = []
    A = L.append

    A("# %s —— 探测报告" % name)
    A("")
    A("> 本文件由 `discover` 包从 **.out + 固件源码 + 对表操作总纲** 探测生成, 是给**人**读的。")
    A("> 拿它可以配现有的库(`meterlib` / `swdbg`)给这块表写调试固件与画像 —— 它本身**不是**脚本,")
    A("> 也**不**生成画像。")
    A(">")
    A("> 通篇区分两件事: **探到的**(地址/大小/类型/volatile/活体 —— 有据可查)与")
    A("> **猜的**(§3 的「候选角色」, 一律带 `(推测)`, 判据写在旁边, 你可以推翻)。")
    A("")
    if rep["missing"]:
        A("**本次缺的料**(相关章节已留空, 不是漏写):")
        for m in rep["missing"]:
            A("- %s" % m)
        A("")

    # ---------------- §0 ----------------
    A("## 0. 可复现性")
    A("")
    A("复现不了的探测没有价值, 所以先把指纹钉在这儿。")
    A("")
    A(_tbl(["项", "值"], [
        ["`.out`", "`%s`" % rep["out"]],
        ["sha256", "`%s`" % rep["sha256"]],
        [".out 字节数", "{:,}".format(rep["out_bytes"])],
        ["源码目录", "`%s`" % rep["src"] if rep["src"] else "**未提供**"],
        ["源码清单来源", ("IAR `.ewp`(只含真参与编译的文件)" if rep.get("src_ewp")
                          else ("扫目录(非 .ewp 清单)" if rep["src"] else "—"))],
        ["参与编译的源文件数", rep.get("src_files") or "—"],
        ["`.ewp`", "`%s`" % rep["ewp"] if rep["ewp"] else "—"],
        ["总纲", "`%s`" % rep["doc"] if rep["doc"] else "**未提供**"],
        ["探测时刻", rep["generated"]],
        ["DWARF 调试信息", "有" if rep["has_debug"] else "**无**"],
        ["工具链", "Python %s / pyelftools %s / pylink %s"
         % (rep["python"], rep["pyelftools"], rep["pylink"])],
        ["J-Link SN", rep["sn"] or "(未指定, 见 swdbg 默认)"],
        ["活体采样参数", ("%d 轮 x %.2fs 间隔" % (rep["rounds"], rep["gap"])) if rep["live"]
         else "**未开 --live**"],
    ]))
    A("")
    if rep["caveats"]:
        A("**本次探测已知的坑**(交给下游, 免得当成「漏抽了」):")
        A("")
        for c in rep["caveats"]:
            A("- %s" % c)
        A("")

    # ---------------- §1 ----------------
    A("## 1. 目标概览")
    A("")
    n_ram = len(rep["syms"])
    occ = sum(s["size"] for s in rep["syms"] if s["size"] > 0)
    rng = (rep["ram_hi"] - rep["ram_lo"]) if (rep["ram_lo"] is not None) else 0
    pct = (100.0 * occ / rng) if rng else 0.0
    gap_bytes = sum(g["size"] for g in rep["gaps"])
    A(_tbl(["项", "值"], [
        ["RAM 地址范围", "%s ~ %s" % (_fmt_addr(rep["ram_lo"]), _fmt_addr(rep["ram_hi"]))],
        ["RAM 跨度", "{:,} B ({:.1f} KB)".format(rng, rng / 1024.0)],
        ["探到的 RAM 对象", "%d 个" % n_ram],
        ["被符号覆盖", "{:,} B ({:.2f}%)".format(occ, pct)],
        ["空闲洞(≥%d B)" % GAP_MIN,
         "%d 处, 合计 {:,} B".format(gap_bytes) % len(rep["gaps"])],
        ["带 DWARF 类型的", "%d 个" % rep["cross"]["stats"]["dwarf_matched"]],
        [".out 节数", "%d 个(其中可载入 %d、可写=RAM %d)"
         % (len(rep["sections"]), sum(1 for s in rep["sections"] if s["flags"] & 0x2),
            sum(1 for s in rep["sections"] if is_ram(s)))],
    ]))
    A("")
    if rng and pct < 40:
        A("> 被符号覆盖的比例不高是**正常**的 —— `.symtab` 只给全局/静态对象, 栈、堆、"
          "编译器临时区、以及只出现在 DWARF 里的局部变量都不占符号, 但它们确实占 RAM。")
        A("> 所以「空闲洞」不等于「真能随便用」, 见 §2.2 的告诫。")
        A("")

    # ---------------- §2 ----------------
    A("## 2. 内存地图")
    A("")
    A("### 2.1 段布局(链接器视角, 权威)")
    A("")
    A("`区` 一列是**可写判据**推出来的: 可写 ⇒ RAM, 只读有地址 ⇒ Flash/ROM。")
    A("")
    rows = [["**RAM**" if is_ram(s) else "Flash/ROM", s["name"], _fmt_addr(s["addr"]),
             "{:,}".format(s["size"]),
             "R" if s["flags"] & 0x2 else "-",
             "W" if s["flags"] & 0x1 else "-",
             "X" if s["flags"] & 0x4 else "-"]
            for s in sorted(rep["sections"], key=lambda x: (x["addr"], x["name"]))
            if s["flags"] & 0x2 and s["size"]]
    A(_tbl(["区", "节名", "起始地址", "大小(B)", "载入", "可写", "可执行"], rows) if rows
      else "_没有带地址的 ALLOC 段 —— 这份 .out 的段表是空的?_")
    A("")
    stack = [s for s in rep["sections"] if is_ram(s)
             and any(k in s["name"].upper() for k in ("STACK", "CSTACK", "SVC"))]
    if stack:
        A("> ⚠ **栈区**: %s。往 RAM 里放探测用的暂存时**避开它** ——"
          % ", ".join("`%s`(%s, %s B)" % (s["name"], _fmt_addr(s["addr"]), s["size"])
                      for s in stack))
        A("> 读写栈区会把跑着的任务打乱, 而且值本来就没有意义。")
        A("")
    else:
        A("> ⚠ 段表里**没有认出栈区**(没有名字带 STACK/CSTACK 的可写段)。这不代表没有栈 ——")
        A("> 多半是栈被算进了别的段里。**下面 §2.2 的「空闲洞」因此可能有一部分其实是栈**, ")
        A("> 往里写会踩坏跑着的任务。用之前先用 `swdbg` 读一遍看它在不在动。")
        A("")
    A("### 2.2 空闲洞(未被任何符号占用的区间, ≥ %d B)" % GAP_MIN)
    A("")
    if rep["gaps"]:
        A(_tbl(["起始地址", "大小(B)"],
               [[_fmt_addr(g["addr"]), "{:,}".format(g["size"])] for g in rep["gaps"]]))
        A("")
        A("> 洞是「没有符号覆盖」, 不等于「没人用」 —— 栈、堆、DMA 缓冲可能就落在里面。")
        A("> 真要用, 先用 `swdbg` 读一遍确认它在动/不动。")
    else:
        A("_没有 ≥ %d B 的空闲洞(符号把 RAM 铺满了, 或被栈/堆占着没进符号表)。_" % GAP_MIN)
    A("")

    # ---------------- §3 ----------------
    A("## 3. 符号总表")
    A("")
    A("**这是主表。** 按地址排序。`类型`/`vol`/`声明处` 来自 DWARF; `地址`/`大小` 来自 `.symtab`;")
    A("`源码足迹` 是*提到*读写它的地方(**文本级线索**, 见 §0 的坑); `活体` 来自 §4 的采样。")
    A("")
    A("**「候选角色」是推测**, 由「源码有没有写」×「活体动不动」交叉得出, 判据在最后两列:")
    A("")
    for role, why, note in ROLE_RULES:
        A("- `%s` ← %s —— %s" % (role, why, note))
    A("")
    hdr = ["名字", "地址", "大小", "类型", "vol", "声明处", "源码足迹", "活体", "抽样值", "候选角色(推测)"]
    rows = []
    for s in sorted(rep["syms"], key=lambda x: x["addr"]):
        decl = "`%s:%d`" % (s["decl_file"], s["decl_line"]) if s.get("decl_line") else "—"
        sz = str(s["size"])
        if s.get("size_verdict") == "dwarf小":
            sz += " *(含对齐)*"
        elif s.get("size_verdict") == "dwarf大":
            sz += " **⚠类型更大**"
        rows.append([s["name"], _fmt_addr(s["addr"]), sz, s.get("type_name") or "—",
                     "**v**" if s.get("volatile") else "—", decl,
                     _footprint_cell(s), _live_cell(s), _val_cell(s),
                     "%s" % s["role"]])
    A(_tbl(hdr, rows) if rows else "_没探到 RAM 符号。_")
    A("")

    # ---------------- §4 ----------------
    A("## 4. 活体证据")
    A("")
    if not rep["live"] or not rep["live_data"]:
        A("**离线探测 —— 未采活体。** 这一节故意留空。")
        A("")
        A("表和 J-Link 就位后, 加 `--live` 重跑, 本节会填上「谁在动、动多快、谁纹丝不动」。")
        A("活体是**唯一**能区分「源码里有人写它」和「它真的会变」的手段 —— §3 的候选角色")
        A("少了这一路, 只能退化成「有写出点」这种含糊说法。")
    else:
        from discover import evidence
        tally, lines = evidence.summarize(rep["live_data"])
        A("采样 %d 轮、间隔 %.2fs, 全程**只读不停核**(swdbg 铁律)。" % (rep["rounds"], rep["gap"]))
        A("")
        A("> ⚠ 多字节读不是原子的 —— 高频量可能**撕裂**(低字节已更新、高字节还没)。")
        A("> 所以本表对「在变」的量**只报变化次数, 不报值**: 动着的值没有意义。")
        A("")
        A(_tbl(["判定", "个数"], [[k, v] for k, v in sorted(tally.items())]))
        A("")
        A("```")
        for ln in lines:
            A(ln)
        A("```")
        A("")
        A("> 「读失败」与「静止」是**两回事**, 本表分开列 —— 把读不到当成稳定态是最典型的假通过。")
    A("")

    # ---------------- §5 ----------------
    A("## 5. 协议语义对照")
    A("")
    if not rep["doc_data"]:
        A("**未提供总纲(--doc)。** 这一节留空。")
        A("")
        A("总纲是「表对外怎么说话」的唯一书面依据; 没有它, 这份报告只能说清**内部有什么**, ")
        A("说不出**外部怎么碰它**。")
    else:
        d = rep["doc_data"]
        A("来自 `%s`(%s B / %s 行)。" % (d["path"], "{:,}".format(d["bytes"]), d["lines"]))
        A("")
        if d["services"]:
            A("### 5.1 服务种类 × 打哪芯")
            A("")
            A("**这张表决定了每条命令该往哪个芯发** —— 发错芯是最常见的「没反应」。")
            A("")
            A(_tbl(["服务", "含义", "打哪芯", "例帧"],
                   [[s["service"] + (" `%s`" % s["name"] if s["name"] else ""),
                     s["meaning"], s["chip"], "`%s`" % s["example"]] for s in d["services"]]))
            A("")
        if d["frames"]:
            A("### 5.2 帧全索引(总纲 10.2.1)")
            A("")
            nf = sum(1 for f in d["frames"] if f["needs_factory"])
            A("共 %d 条, 其中 **%d 条需 `645.factory` 前置** —— 不加前置会回 DAR=MatchAuth(0x14) / 0xD4(ER_PSWD)。" % (len(d["frames"]), nf))
            A("")
            A(_tbl(["帧 id", "用途", "前置 / 注意", "需厂内"],
                   [["`%s`" % f["id"], f["use"], f["prereq"],
                     "**是**" if f["needs_factory"] else "—"] for f in d["frames"]]))
            A("")
        if d["oads"]:
            A("### 5.3 OAD 全映射(审计纪要 §2)")
            A("")
            A("`ID` 列来自 `FrezData.h` —— **是 C 标识符**, 所以能和 §3 的符号表对名字(见下)。")
            A("")
            A(_tbl(["记录", "ID (FrezData.h)", "行 OAD", "子类", "请求 OAD"],
                   [[r["record"], "`%s`" % r["id"], r["line_oad"], r["subclass"],
                     "`%s`" % r["req_oad"]] for r in d["oads"]]))
            A("")
            # 名字对得上就并排放; 对不上就**解释为什么对不上** —— 不硬凑, 也不含糊。
            byname = {s["name"].lower(): s for s in rep["syms"]}
            hits, n_key = [], 0
            for r in d["oads"]:
                key = _oad_key(r["id"])
                if not key:
                    continue
                n_key += 1
                cand = [v for k, v in byname.items() if key in k or k in key]
                if len(cand) == 1:
                    hits.append([r["record"], "`%s`" % r["id"], "`%s`" % cand[0]["name"],
                                 _fmt_addr(cand[0]["addr"]), cand[0].get("type_name") or "—"])
            if hits:
                A("#### 名字能对上的(推测 —— 仅按标识符相似, 未验证语义)")
                A("")
                A(_tbl(["记录", "总纲里的 ID", "内部符号", "地址", "类型"], hits))
                A("")
            else:
                A("_解析出 %d 个 ID, **一个都没能在 RAM 符号表里对上**。_" % n_key)
                A("")
                A("> 这多半**不是**「名字改了」, 而是**层次不同**: 这些 `ID_*` 是 `FrezData.h` 里的")
                A("> **枚举常量** —— 编译后是代码里的立即数, **根本不占 RAM**, 所以本报告那张")
                A("> 「RAM 符号表」里永远不会有它们。硬去对名字只会得到假阴性。")
                A(">")
                A("> 要把「记录种类」落到具体存储上, 得顺着 §3 里 `FrezData` 相关的符号")
                A("> (比如 `s_stFrzStorageInfo`) 去 `Platform/FrezData.c` 看那张存储表怎么排的。")
                A("> **这一步机器抽不出来, 得人读。**")
                A("")
        if d["dis"]:
            A("### 5.4 总纲里散落的 DI / 对象号")
            A("")
            A("**只有 %d 个** —— 帧实体不在总纲, 在 `project/<表>.frames.json` 与 "
              "`meterlib/cmd_bank.SPECS`。要完整 DI 清单得去那两处。" % len(d["dis"]))
            A("")
            A(_tbl(["码", "写法", "总纲行"], [["`%s`" % x["code"], x["form"], str(x["lineno"])]
                                             for x in d["dis"]]))
            A("")
        if d["human_only"]:
            A("### 5.5 机器抽不动、**必须人读**的节")
            A("")
            A("这些节是叙述体/超长单元格, 硬抽只会抽出似而非的字段。照实列出来:")
            A("")
            A(_tbl(["标题", "总纲行", "为什么抽不动"],
                   [["%s" % h["title"], str(h["lineno"]), h["why"]] for h in d["human_only"]]))
            A("")

    # ---------------- §6 ----------------
    A("## 6. 交叉验证")
    A("")
    st = rep["cross"]["stats"]
    A("三路互核: `.symtab` 的地址/大小 ←→ DWARF 的地址/大小 ←→ 段边界。")
    A("")
    A(_tbl(["项目", "数", "含义"], [
        ["RAM 符号", st["ram_symbols"], "`.symtab` 里落在 RAM 的全局/静态对象"],
        ["DWARF 匹配上", st["dwarf_matched"], "能在 DWARF 里找到同名变量的"],
        ["地址可核", st["dwarf_addr"], "两边都有地址、可比对的"],
        ["大小可核", st["dwarf_size"], "两边都有大小、可比对的"],
        ["**地址冲突**", "**%d**" % st["addr_conflict"],
         "**两边地址不同 = 这份 .out 与源码不同步, 重大发现**"],
        ["大小真冲突", st["size_type_bigger"], "类型比 `.symtab` 给的空间还大 —— 越界风险"],
        ["对齐填充", st["size_padding"], "类型比空间小 = 链接器对齐填充, **正常, 不是冲突**"],
    ]))
    A("")
    if st["addr_conflict"]:
        A("### ⚠ 地址冲突(必须人看)")
        A("")
        A(_tbl(["名字", ".symtab 地址", "DWARF 地址"],
               [[c["name"], _fmt_addr(c["addr"]), _fmt_addr(c.get("dwarf_addr"))]
                for c in rep["cross"]["addr_conflict"][:TOP_N]]))
        A("")
    if st["size_type_bigger"]:
        A("### ⚠ 大小真冲突(类型比分配的空间大)")
        A("")
        A(_tbl(["名字", "地址", ".symtab 大小", "DWARF 类型"],
               [[s["name"], _fmt_addr(s["addr"]), s["size"], s.get("type_name") or "—"]
                for s in rep["syms"] if s.get("size_verdict") == "dwarf大"][:TOP_N]))
        A("")
    # 段边界核对 —— 纯靠 .out 自己就能做的第三路校验
    outside = [s for s in rep["syms"]
               if s["size"] > 0 and rep["ram_lo"] is not None
               and not (rep["ram_lo"] <= s["addr"] and s["addr"] + s["size"] <= rep["ram_hi"])]
    A("### 6.1 段边界核对")
    A("")
    if outside:
        A("**⚠ %d 个符号落在 RAM 段范围之外**(%s ~ %s):"
          % (len(outside), _fmt_addr(rep["ram_lo"]), _fmt_addr(rep["ram_hi"])))
        A("")
        A(_tbl(["名字", "地址", "大小"], [[s["name"], _fmt_addr(s["addr"]), s["size"]]
                                         for s in outside[:TOP_N]]))
        A("")
        A("要么是外设寄存器映射(正常), 要么段表不全(不正常)。逐个看地址落在哪。")
    else:
        A("全部 %d 个符号都落在 RAM 段范围内 —— 干净。" % len(rep["syms"]))
    A("")

    # ---------------- §7 ----------------
    A("## 7. 开放问题 / 待判断")
    A("")
    A("**这一节是这份文件的灵魂。** 下面全是探测**没能定论**的东西 —— 它就是下次开会话、")
    A("建画像时的任务清单。每一条都附了判据, 能直接顺着查; 没有判据的猜测不往这儿放。")
    A("")
    idx = (rep["src_index"] or {}).get("index") or {}
    issues = []

    def add(title, why, items, fmt):
        if items:
            issues.append((title, why, [fmt(x) for x in items[:TOP_N]], len(items)))

    add("源码查无足迹", "`.symtab`/DWARF 里有它, 但参与编译的源码里一次都没提到 —— "
        "可能是库/汇编里用的、只经指针访问、或被 `#if` 掉了。它的**语义从源码里问不出来**。",
        [s for s in rep["syms"] if s.get("src") is not None and not s["src"]["n_hits"]],
        lambda s: "`%s` @%s %s" % (s["name"], _fmt_addr(s["addr"]), s.get("type_name") or ""))

    # 注意排除 `&x` 的: 那些归 §7.2。两边都列的话, 同一个符号会同时被贴上
    # "没见用处"和"取过地址会被指针改写"两个相反的说法。
    add("只声明, 没见用处", "源码里只有声明行, 没有任何读写 —— 预留? 被改成指针访问? 被条件编译掉了?",
        [s for s in rep["syms"] if s.get("src") and s["src"]["n_hits"]
         and not (s["src"]["writes"] or s["src"]["reads"] or s["src"]["addr_taken"])],
        lambda s: "`%s` @%s 声明于 `%s`" % (s["name"], _fmt_addr(s["addr"]),
                                          (s["src"]["decls"] or [{}])[0].get("file", "?")))

    add("取过地址(&x) —— 源码足迹不可信",
        "它被 `&` 取过地址, 可能经指针被改写, 而**指针那一路文本分析看不见**。"
        "这类符号的「只读」判定是假的, 必须靠活体。",
        [s for s in rep["syms"] if s.get("src") and s["src"]["addr_taken"]],
        lambda s: "`%s` @%s 取址于 %s" % (s["name"], _fmt_addr(s["addr"]),
                                        ", ".join(_where(s, "addr_taken", 1))))

    if rep["live_data"]:
        add("静止但源码里有写 —— **STABLE 类的正主**",
            "不动, 但源码里确实有人写它 ⇒ 有**显式命令/事件**才会变。"
            "这正是 stable 类断言要的: 不碰它就必须纹丝不动。要定的是**触发条件**。",
            [s for s in rep["syms"] if (s.get("live") or {}).get("verdict") == "静止"
             and s.get("src") and s["src"]["writes"]],
            lambda s: "`%s` @%s 写点 %s" % (s["name"], _fmt_addr(s["addr"]),
                                           ", ".join(_where(s, "writes", 2)) or "?"))

        add("高频在动但源码里找不到写",
            "次次采样都在变, 源码里却没有写它的地方 ⇒ 多半是**中断/DMA/指针**在改。"
            "这类量可以进 WATCH, 但**不能用「写它来触发」的思路去测**。",
            [s for s in rep["syms"] if (s.get("live") or {}).get("verdict") == "高频"
             and not (s.get("src") or {}).get("writes")],
            lambda s: "`%s` @%s 变%d次" % (s["name"], _fmt_addr(s["addr"]),
                                          s["live"]["changed"]))

        add("读失败 —— 地址可能无效",
            "逐轮都读不到。地址可能落在未映射区、或这份 .out 与在跑的表不是同一版。"
            "**这不是「静止」**, 别当成稳定态用。",
            [s for s in rep["syms"] if (s.get("live") or {}).get("verdict") == "读失败"],
            lambda s: "`%s` @%s 大小 %d" % (s["name"], _fmt_addr(s["addr"]), s["size"]))
    else:
        issues.append(("活体一路没采", "**未开 --live** ⇒ 谁在动、动多快全部未知。"
                       "§3 的候选角色因此只能退化成「有写出点/只读」这种含糊说法, "
                       "STABLE 与 WATCH 分不开。这是当前最大的一个洞。", [], 0))

    add("没有 DWARF 类型", "只有 `.symtab` 的名字/地址/大小, 类型、volatile、声明行号全没有。"
        "volatile 拿不到就意味着**无法从静态信息判断它会不会被意外读写**。",
        [s for s in rep["syms"] if not s.get("type_name")],
        lambda s: "`%s` @%s %d B" % (s["name"], _fmt_addr(s["addr"]), s["size"]))

    add("大小真冲突(类型比空间大)", "声明的类型比 `.symtab` 分配的空间还大 —— 读写会越界。"
        "要么 .out 与源码不同步, 要么链接脚本有问题。**动手前先查清**。",
        [s for s in rep["syms"] if s.get("size_verdict") == "dwarf大"],
        lambda s: "`%s` @%s `.symtab` %d B vs 类型 %s" % (s["name"], _fmt_addr(s["addr"]),
                                                        s["size"], s.get("type_name")))

    if rep["doc_data"]:
        d = rep["doc_data"]
        need = [f for f in d["frames"] if f["needs_factory"]]
        if need:
            issues.append((
                "需 `645.factory` 前置的帧(%d 条)" % len(need),
                "不加厂内前置就发, 会回 DAR=MatchAuth(0x14) / 0xD4(ER_PSWD) —— 现象像"
                "「命令不支持」, 实际是没授权。跑这些用例前必须先 `enter_factory`。"
                "另注: **退出厂内的办法随固件而异** —— 有的表留了串口退出口(发一条同族命令即"
                "复位设置计时器), 有的只能断电/复位。别假定「没有」: 按 .out 符号与源码行号查准,"
                "免得把可收拾的台面留成必须断电的状态。",
                ["`%s` %s" % (f["id"], f["use"]) for f in need], len(need)))

    for i, (title, why, items, n) in enumerate(issues, 1):
        A("### 7.%d %s" % (i, title))
        A("")
        A(why)
        A("")
        # items 为空**不等于"没这回事"** —— 手工加进来的那条(比如"活体一路没采")
        # 本来就是"没有具体符号, 但整条路缺了", 它的 why 已经把话说完了。
        # 在这儿补一句"本次没有这一类"会自相矛盾(踩过)。
        if items:
            A("共 **%d** 个%s:" % (n, ", 下面列前 %d 个" % TOP_N if n > TOP_N else ""))
            A("")
            for it in items:
                A("- %s" % it)
        A("")

    A("---")
    A("")
    A("## 附录 A. 全量转储")
    A("")
    A("机器可读的那一份。**不含源码正文** —— 只有符号名、地址、类型、`文件:行号` 指针")
    A("(外泄硬约束, 见 `discover/__init__.py`)。")
    A("")
    A("<details>")
    A("<summary>展开 §3 全部 %d 个符号的完整字段</summary>" % len(rep["syms"]))
    A("")
    A(_tbl(["名字", "地址", "大小", "段", "类型", "vol", "声明", "写", "读", "&", "活体"],
           [[s["name"], _fmt_addr(s["addr"]), s["size"], s.get("section") or "—",
             s.get("type_name") or "—", "v" if s.get("volatile") else "—",
             "`%s:%s`" % (s["decl_file"], s["decl_line"]) if s.get("decl_line") else "—",
             len((s.get("src") or {}).get("writes") or []) if s.get("src") else "—",
             len((s.get("src") or {}).get("reads") or []) if s.get("src") else "—",
             len((s.get("src") or {}).get("addr_taken") or []) if s.get("src") else "—",
             (s.get("live") or {}).get("verdict", "—")]
            for s in sorted(rep["syms"], key=lambda x: x["addr"])]))
    A("")
    A("</details>")
    A("")
    A("<details>")
    A("<summary>展开全部源码足迹指针(文件:行号)</summary>")
    A("")
    for s in sorted(rep["syms"], key=lambda x: x["addr"]):
        one = s.get("src")
        if not one or not one["n_hits"]:
            continue
        A("**`%s`** @%s" % (s["name"], _fmt_addr(s["addr"])))
        A("")
        for kind, label in (("decls", "声明"), ("writes", "写"), ("reads", "读"),
                            ("addr_taken", "取址"), ("sizeof", "sizeof")):
            for p in one.get(kind) or []:
                A("- %s `%s:%d`%s" % (label, p["file"], p["line"],
                                      " <%s>" % p["func"] if p["func"] else ""))
        A("")
    A("</details>")
    A("")
    A("---")
    A("")
    A("*生成于 %s, 耗时 %.1fs。本文件由 `discover` 包产生 —— 要重跑见 §0 的参数。*"
      % (rep["generated"], rep["elapsed"]))
    A("")
    return "\n".join(L)


def write_report(rep, path=None):
    """报告 dict → 落盘 `project/knowledge/探测报告/<表名>_探测.md`(默认) → 返回实际路径。"""
    if path is None:
        d = DEFAULT_DIR
        if not os.path.isdir(d):
            os.makedirs(d)
        path = os.path.join(d, "%s_探测.md" % meter_name(rep["out"]))
    else:
        d = os.path.dirname(os.path.abspath(path))
        if d and not os.path.isdir(d):
            os.makedirs(d)
    md = render(rep)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(md)
    return path


# ============================ 独立 CLI ============================
# 控制台 UTF-8 单点: 实现收在 common/console.py(2026-09-10 迁自 meterlib)。
# 本包只依赖 common(不是 meterlib), 故可直接引 —— 原先各内联一份的理由(它住在协议层)已消失。
# 别名保原调用点 `_ensure_utf8_stdout()` 一字不改。
from common.console import ensure_utf8_stdout


def main(argv=None):
    """python -m discover.dossier --out <某表.out> [--src 源码目录] [--doc 总纲.md]
                                 [--ewp x.ewp] [--live] [--to 输出.md] [<名字正则>]

    只给 --out 也能出报告(符号+类型+内存图+§7 的一部分)。
    """
    ensure_utf8_stdout()
    import argparse
    ap = argparse.ArgumentParser(description="合成探测报告 project/knowledge/探测报告/<表名>_探测.md")
    ap.add_argument("--out", required=True)
    ap.add_argument("--src", default="")
    ap.add_argument("--doc", default="")
    ap.add_argument("--ewp", default="")
    ap.add_argument("--to", default="", help="输出路径(默认 project/knowledge/探测报告/<表名>_探测.md)")
    ap.add_argument("--live", action="store_true", help="连表采活体(只读不停核)")
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--gap", type=float, default=0.5)
    ap.add_argument("pattern", nargs="?", default=None)
    args = ap.parse_args(argv)

    def say(msg):
        print("   … %s" % msg)

    rep = build(args.out, src=args.src or None, doc=args.doc or None, ewp=args.ewp or None,
                live=args.live, rounds=args.rounds, gap=args.gap, pattern=args.pattern,
                progress=say)
    p = write_report(rep, args.to or None)
    n7 = render(rep).count("\n### 7.")
    print("== 探测报告: %s ==" % p)
    print("   符号 %d 个 / 段 %d 个 / 空闲洞 %d 处 / §7 开放问题 %d 类 / 耗时 %.1fs"
          % (len(rep["syms"]), len(rep["sections"]), len(rep["gaps"]), n7, rep["elapsed"]))
    if rep["missing"]:
        print("   缺: %s" % " / ".join(rep["missing"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
