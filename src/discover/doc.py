# -*- coding: utf-8 -*-
"""
discover/doc.py —— 从《对表操作总纲》里抽出机读的部分: DI 码 / OAD 对象 / 厂内前置 / 帧清单。

给报告 §5「协议语义对照」供料: 把「总纲怎么描述这块表的对外协议」与
「elf.py 从 .out 探到的内部符号」并排放, 好让人看出哪条命令对应哪个变量。

总纲不是给机器写的, 所以本模块**只抽它最规整的那几块**, 其余明确标"需人读"
(`human_only`), 不许硬凑 —— 报告里宁可写"这节得人看", 也不要抽出一堆似而非的字段。

2026-09-10 实测摸清的结构(靠这个写的, 不是猜的)
-----------------------------------------------
    体积   142,670 B / 1,576 行, 全读无碍。
    骨架   主体 = 原文一~九 + 「十、对表操作补充」(10.1~10.6) + 6 个并入段。

    **最规整的三张表**(全部按**表头文字**定位, 见下"定位法"):
      · 帧全索引(10.2.1)   `| 帧 id（=send 参数） | 用途 | 前置 / 注意 |`   3 列 —→ frames
      · OAD 全映射(审计纪要§2) `| 记录 | ID (FrezData.h) | 行 OAD | 子类 | 请求 OAD |` 5 列 —→ oads
      · 服务种类×由谁答      `| 服务 | 由谁答 |`(4 列, 见 367 行)            —→ services

三个坑(踩过才知道, 都写进 `caveats` 一起交给报告)
-------------------------------------------------
1. **`### 10.7` 标题不存在** —— 目录里引用了 10.7, 但正文漏写标题, 那张待办表裸挂在 10.6
   后面。按 `### 10.x` 扫会**整个漏掉**。本模块不依赖 10.7 存在。
2. **并入段让 `##` 编号重复**(两个"一、"、两个"## 8.")。定位一律靠**表头文字/整行标题**,
   绝不靠编号 —— 靠编号必然错位。
3. **DI 码在总纲里极少(仅 7 处)**。帧实体不在总纲, 在 `project/<表>.frames.json`(旧名
   user_frames.json)与 `meterlib/cmd_bank.py`。**要一份完整的 DI 清单得去 frames.json,
   本模块抽不出** —— 这是设计边界, 不是缺陷; 报告 §5 会注明。

厂内前置怎么判
--------------
总纲**没有机器 token**(没有 `needs_factory` 这种东西), 全是中文散文。唯一干净的机读信号是
帧索引表第 3 列: **含「需厂内」⇒ 该帧需 `645.factory` 前置**。本模块据此置 `needs_factory`。
(反例判据成对出现: 无前置 → 698 动作回 DAR=MatchAuth(0x14), 645 写回 0xD4(ER_PSWD)。)

本体不 import project / meterlib / swdbg —— 只吃一个 markdown 路径(见包 docstring)。
"""
from __future__ import print_function

import io
import os
import re
import sys


__all__ = ["extract", "find_table", "DI_RE", "OAD_RE"]

# 按表头文字定位这三张表 —— 不靠编号(并入段会重置编号, 见文件头坑 2)。
HDR_FRAMES = ("帧 id", "用途", "前置")           # 10.2.1 现成帧全索引
HDR_OADS = ("记录", "行 OAD", "子类", "请求 OAD")  # 审计纪要 §2
HDR_SERVICES = ("服务", "打哪芯")                 # 10.2.3(表头原文: `| 服务（APDU 首字节） | 含义 | 打哪芯 | 例帧 |`;
                                                #  "由谁答"只在标题里, 不在表头 —— 别拿它当判据)

# DI 写法定死不只一种: 反引号包着的 `DI 04000B01`, 也有裸对象号 0x40000200。
DI_RE = re.compile(r"`DI\s+([0-9A-Fa-f]{8})`")
OBJ_RE = re.compile(r"0x([0-9A-Fa-f]{8})\b")
# 行内 OAD: 裸 8hex, 或空格分组的线上请求形 `50 05 02 00`
OAD_RE = re.compile(r"`([0-9A-Fa-f]{2}(?:\s+[0-9A-Fa-f]{2}){3})`")

# 已知"机器抽不动"的节: 标题片段 → 为什么。报告原样转述, 免得下游以为漏抽了。
PROSE_SECTIONS = [
    ("10.6 实测日志", "单元格是几百字中文叙事, DI/结果/根因全塞在一格, 只能人读"),
    ("指令测试与汇总", "并入段的离线验证记录, 叙述体"),
    ("八、4-6 结算日冻结", "箭头链执行故事, 混排叙述"),
]
_LONG_CELL = 200            # 单元格超这个字数 = 该表偏叙事, 标"需人读"


def _read_lines(path):
    with io.open(path, encoding="utf-8", errors="replace") as f:
        return f.read().splitlines()


def _cells(line):
    """markdown 表行 → 单元格 list(去掉首尾空串, 各段 strip)。非表行返回 None。"""
    t = line.strip()
    if not t.startswith("|"):
        return None
    parts = [c.strip() for c in t.split("|")]
    if parts and parts[0] == "":
        parts = parts[1:]
    if parts and parts[-1] == "":
        parts = parts[:-1]
    return parts or None


def _is_sep(line):
    """表头下的 `|---|---|` 分隔行。"""
    c = _cells(line)
    if not c:
        return False
    return all(set(x) <= set("-: ") and "-" in x for x in c)


def find_table(lines, header_keys, near=0):
    """按表头含全部关键词定位一张表 → (header_idx, header_cells, rows) 或 None。
    rows = [[cell,...], ...]。**只认第一个命中**, 靠表头文字不靠编号(见文件头坑 2)。"""
    for i, ln in enumerate(lines):
        c = _cells(ln)
        if not c:
            continue
        joined = " ".join(c)
        if not all(k in joined for k in header_keys):
            continue
        if i + 1 >= len(lines) or not _is_sep(lines[i + 1]):
            continue                                    # 表头下面必须是分隔行, 否则是散文里的假命中
        rows, j = [], i + 2
        while j < len(lines):
            r = _cells(lines[j])
            if not r:
                break
            rows.append(r)
            j += 1
        return i, c, rows
    return None


def _split_code(cell):
    """`` `05 01` GetRequestNormal `` → ("05 01", "GetRequestNormal")。
    单元格常常是"反引号包着码 + 后面跟中文/英文名", 直接 strip('`') 会把中间那个反引号留在串里。"""
    c = (cell or "").strip()
    m = re.match(r"`([^`]+)`\s*(.*)$", c)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return c.strip("`").strip(), ""


def _sections(lines):
    """顶层骨架: [{level, title, lineno}]。level = '#' 的个数。"""
    out = []
    for i, ln in enumerate(lines):
        s = ln.rstrip()
        if s.startswith("#"):
            n = len(s) - len(s.lstrip("#"))
            title = s[n:].strip()
            if title:
                out.append({"level": n, "title": title, "lineno": i + 1})
    return out


def _enclosing(sections, lineno):
    """某行落在哪个标题下(取最靠上、level 最小的最近标题)。"""
    cur = None
    for sec in sections:
        if sec["lineno"] <= lineno:
            cur = sec
        else:
            break
    return cur


def extract(doc_path):
    """总纲 → 机读结构。

    → {"path", "bytes", "lines", "sections", "tables", "frames", "oads", "services",
       "dis", "human_only", "caveats"}
    抽不到的字段一律**空 list + caveats 里写明原因**, 不猜。
    """
    lines = _read_lines(doc_path)
    secs = _sections(lines)
    res = {"path": doc_path, "bytes": os.path.getsize(doc_path), "lines": len(lines),
           "sections": secs, "tables": [], "frames": [], "oads": [], "services": [],
           "dis": [], "human_only": [], "caveats": []}

    # ---- 1. 三张规整表 ----
    t = find_table(lines, HDR_FRAMES)
    if t:
        i, hdr, rows = t
        res["frames"] = [{"id": r[0].strip("`"), "use": r[1] if len(r) > 1 else "",
                          "prereq": r[2] if len(r) > 2 else "",
                          "needs_factory": ("需厂内" in r[2]) if len(r) > 2 else False}
                         for r in rows]
    else:
        res["caveats"].append("没找到「现成帧全索引」表(表头 %s) —— 帧清单为空" % (HDR_FRAMES,))

    t = find_table(lines, HDR_OADS)
    if t:
        i, hdr, rows = t
        res["oads"] = [{"record": r[0], "id": r[1] if len(r) > 1 else "",
                        "line_oad": r[2] if len(r) > 2 else "",
                        "subclass": r[3] if len(r) > 3 else "",
                        "req_oad": r[4].strip("`") if len(r) > 4 else ""}
                       for r in rows]
    else:
        res["caveats"].append("没找到「OAD 全映射」表(表头 %s)" % (HDR_OADS,))

    t = find_table(lines, HDR_SERVICES)
    if t:
        i, hdr, rows = t
        res["services"] = [{"service": _split_code(r[0])[0], "name": _split_code(r[0])[1],
                            "meaning": r[1] if len(r) > 1 else "",
                            "chip": r[2] if len(r) > 2 else "",
                            "example": r[3].strip("`") if len(r) > 3 else ""} for r in rows]
    else:
        res["caveats"].append("没找到「服务种类 × 由谁答」表(表头 %s)" % (HDR_SERVICES,))

    # ---- 2. 散落的 DI / 对象号(极少, 见文件头坑 3) ----
    seen = set()
    for i, ln in enumerate(lines):
        for m in DI_RE.finditer(ln):
            code = m.group(1).upper()
            if code not in seen:
                seen.add(code)
                res["dis"].append({"code": code, "form": "DI 反引号", "lineno": i + 1})
        for m in OBJ_RE.finditer(ln):
            code = m.group(1).upper()
            if code not in seen:
                seen.add(code)
                res["dis"].append({"code": code, "form": "0x 裸对象号", "lineno": i + 1})
    if len(res["dis"]) < 20:
        res["caveats"].append(
            "总纲里 DI 只有 %d 个 —— 帧实体不在总纲, 在 project/<表>.frames.json 与 cmd_bank.SPECS; "
            "要完整 DI 清单得去那两处" % len(res["dis"]))

    # ---- 3. 全部表格的概览(含"是不是叙事表"的机器判据) ----
    i = 0
    while i < len(lines):
        c = _cells(lines[i])
        if c and i + 1 < len(lines) and _is_sep(lines[i + 1]):
            rows, j = [], i + 2
            while j < len(lines):
                r = _cells(lines[j])
                if not r:
                    break
                rows.append(r)
                j += 1
            longest = max([len(x) for r in rows for x in r] or [0])
            sec = _enclosing(secs, i + 1)
            res["tables"].append({
                "lineno": i + 1, "header": c, "ncols": len(c), "nrows": len(rows),
                "longest_cell": longest, "human_heavy": longest > _LONG_CELL,
                "under": sec["title"] if sec else ""})
            i = j
            continue
        i += 1

    # ---- 4. 明确标"需人读"的节 ----
    for title, why in PROSE_SECTIONS:
        for sec in secs:
            if title in sec["title"]:
                res["human_only"].append({"title": sec["title"], "lineno": sec["lineno"],
                                          "why": why})
                break
    for tb in res["tables"]:
        if tb["human_heavy"] and not any(h["title"] == tb["under"] for h in res["human_only"]):
            res["human_only"].append(
                {"title": tb["under"], "lineno": tb["lineno"],
                 "why": "表里有超长单元格(最长 %d 字), 偏叙事" % tb["longest_cell"]})

    # ---- 5. 结构坑: 一并交给报告, 免得下游以为抽漏了 ----
    if not any(s["title"].strip().startswith("10.7") for s in secs):
        res["caveats"].append("总纲没有 `### 10.7` 标题(目录引用了但正文漏写), 待办表裸挂在 10.6 后; "
                              "按标题扫会漏, 本模块不依赖它")
    n_h1 = sum(1 for s in secs if s["level"] == 1)
    if n_h1 > 1:
        res["caveats"].append("有 %d 个一级标题(原文 + 并入段各带), `##` 编号会重复 —— "
                              "定位一律用表头文字/整行标题, 别靠编号" % n_h1)
    return res


# 控制台 UTF-8 单点: 实现收在 common/console.py(2026-09-10 迁自 meterlib)。
# 本包只依赖 common(不是 meterlib), 故可直接引 —— 原先各内联一份的理由(它住在协议层)已消失。
# 别名保原调用点 `_ensure_utf8_stdout()` 一字不改。
from common.console import ensure_utf8_stdout


def main(argv=None):
    """python -m discover.doc <总纲.md> [--json]  —— 看本模块从总纲里抽到了什么。"""
    ensure_utf8_stdout()
    import argparse
    import json
    ap = argparse.ArgumentParser(description="从《对表操作总纲》抽机读部分(DI/OAD/厂内前置/帧清单)")
    ap.add_argument("doc", help="总纲 markdown 路径")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    r = extract(args.doc)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    print("== 总纲抽取: %s ==" % r["path"])
    print("   %d B / %d 行 / %d 个标题 / %d 张表" % (r["bytes"], r["lines"], len(r["sections"]), len(r["tables"])))
    print("   帧 %d 条(需厂内 %d)  OAD 映射 %d 条  服务 %d 条  DI/对象号 %d 个"
          % (len(r["frames"]), sum(1 for f in r["frames"] if f["needs_factory"]),
             len(r["oads"]), len(r["services"]), len(r["dis"])))
    for c in r["caveats"]:
        print("   ⚠ %s" % c)
    print("   -- 需人读的节 --")
    for h in r["human_only"]:
        print("      L%-5d %s  (%s)" % (h["lineno"], h["title"], h["why"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
