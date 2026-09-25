# -*- coding: utf-8 -*-
"""csrc.py —— C 源码的**结构索引**: 一个文件里有哪些函数、某一行归哪个函数、某个名字在哪几行出现。

管的是"这段源码长什么样" —— 不认断点、不认表、不读 `.out`、不连探针。
断点那一侧要问的"这个变量在断点那一行赋过值没有"由它供料, 但问题本身不在这里
(见同目录 `breakpoint.py`)。分开的理由: 索引与提问是两件事, 换一种提问(比如"这条调用链经过谁")
不该把索引再写一遍。

判读的**原语**(抠注释/认函数定义行/判一次出现是读还是写)住 `common/ctext.py` ——
`discover/source.py` 的足迹分析与本模块用的是同一份, 免得同一个变量一边说被写、一边说没写。

已知限度
--------
- 判"这一行是不是函数定义"复用 `ctext.func_of` 的正则(名字在行首或紧跟类型), 跨行的 K&R 签名看不见;
  这个函数到底在哪收尾靠**花括号配平**定, 不按缩进猜。
- 判"是不是声明"复用 `ctext.decl_line`, 于是 `INT8U a = 1, b;` 里第二个声明符 `b` 认不出来
  —— 本工程罕见此写法, 不为它放宽。
"""
import io
import re

from common import ctext

__all__ = ["load", "functions", "enclosing", "params", "name_base", "occurrences",
           "passed_out_re", "decl_info"]


def load(path):
    """`.c` → (code_lines, raw_lines), 1-based: index 0 对应第 1 行。

    `code_lines` 走 `ctext.strip_comments`(等长空格替换), 所以**行号与列偏移都不变**;
    `raw_lines` 保留原文, 供人看。事实判定只看 code_lines。

    解码: IAR 在中文 Windows 上的工程源码通常是 **GBK**(实测 TaskFreeze.c 不是 UTF-8),
    按 utf-8 → gbk → gb18030 回退, 全失败才 replace。解错的直接后果是注释被当成代码读。
    """
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            with io.open(path, encoding=enc) as f:
                text = f.read()
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        with io.open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    raw = text.split("\n")
    code, in_block = [], False
    for ln in raw:
        c, in_block = ctext.strip_comments(ln, in_block)
        code.append(c)
    return code, raw


def _brace_end(code, start):
    """从 start 行起做花括号配平 → 块结束行(1-based)。找不到闭合返回 None。"""
    depth, seen = 0, False
    for i in range(start - 1, len(code)):
        for ch in code[i]:
            if ch == "{":
                depth += 1; seen = True
            elif ch == "}":
                depth -= 1
                if seen and depth == 0:
                    return i + 1
    return None


def functions(code):
    """→ [(函数名, 定义行, 结束行)]。定义行怎么判**复用 `ctext.func_of`**;
    范围由**大括号配平**定, 不是按缩进猜函数尾。"""
    out = []
    for i, ln in enumerate(code):
        name = ctext.func_of(ln)
        if not name:
            continue
        brace = None
        for k in range(i, min(i + 4, len(code))):
            if "{" in code[k]:
                brace = k; break
            if code[k].strip() and k > i:
                break
        if brace is None:
            continue
        end = _brace_end(code, brace + 1)
        if end:
            out.append((name, i + 1, end))
    return out


def enclosing(code, line):
    """包住 line 的那个函数 → (名字, 定义行, 结束行) 或 None。**取最内层的那个。**"""
    best = None
    for f in functions(code):
        if f[1] <= line <= f[2] and (best is None or (f[2] - f[1]) < (best[2] - best[1])):
            best = f
    return best


def params(code, def_line):
    """函数定义行的形参名集合。形参是另一回事: 值由调用方给。"""
    ln = code[def_line - 1]
    i, j = ln.find("("), ln.rfind(")")
    if i < 0 or j <= i:
        return frozenset()
    out = set()
    for part in re.split(r",(?![^()]*\))", ln[i + 1:j]):
        m = ctext.IDENT.findall(part)
        # 末一个标识符才是名字(前面是类型词/星号); 无参函数那里是单独的 `void`
        if m and m[-1] != "void":
            out.add(m[-1])
    return frozenset(out)


# 控制流关键字长得像函数调用(`if (mode)`), 不能当成"传给被调函数"
CALL_KW = r"(?:if|for|while|switch|return|sizeof|defined)"


def passed_out_re(name):
    """**传出**(赋值发生在被调函数里)的行: 取址 `&x`, 或作为实参 `f(… x …)`。

    这是"断点前无赋值"的两条已知盲区之一, 必须报出来而不是沉默: `obj` 交给 `Read_FrezObj`
    后由它填, 这时说"断点前无赋值"就是假警报。`&x` 那一半 `ctext.kind` 已会判(addr_taken),
    这里合起来看; 数组名退化成指针那种(`f(obj)`)只有本条能看见。
    """
    n = re.escape(name)
    return re.compile(r"(?:&[ \t]*\b%s\b)"
                      r"|(?<![A-Za-z0-9_])(?!(?:%s)\b)[A-Za-z_]\w*[ \t]*\([^;{}]*\b%s\b"
                      % (n, CALL_KW, n))


def name_base(nm):
    """脚本里写的变量名 → 在源码里用来找"出现"的标识符基名。

    `swTime[0]` → `swTime`(逐元素读, 读的仍是那块存储); `TAB_Switch[id].idFrez` → `TAB_Switch`
    (成员/下标背后是那个基对象)。

    ⚠ **不归一化 = 一次也匹配不上** —— 2026-09-10 实测踩到: `occurrences` 拿整串 `swTime[0]`
    去比源码里的标识符(`ctext.IDENT`), 那是永远比不中的, 于是 `first_seen=None`、`assigned_at=[]`,
    报告说"断点前无赋值(读到的是未初始化值)" —— 而实际上 :311 `Read_ParaData(…, &swTime[0])`
    早把它填过了, 断点观测实测值 `[54,14,10,9,26]` 与所写设定一致。**假警报比不报更坏**:
    它会教人无视这一栏, 真假通过就跟着溜过去。
    """
    return nm.split("[", 1)[0].split(".", 1)[0].strip()


def occurrences(code, line, name):
    """一行里 name 的每次出现 → [(start, end)]。**成员访问右半边不算**(`a.name` 里的 name)。"""
    hits = []
    for m in ctext.IDENT.finditer(code[line - 1]):
        if m.group(0) != name:
            continue
        if ctext.kind(code[line - 1], m.start(), m.end()) == "skip":
            continue
        hits.append((m.start(), m.end()))
    return hits


def decl_info(code, lo, hi, base):
    """名字在**本函数体内**有没有声明 → (声明行, 初值形状)。

    这是"断点前无赋值"的**另一条已知盲区**(2026-09-17 补): 只在函数体范围内扫,
    于是**文件级量(全局/静态量)与别的函数的局部量**在本函数里怎么看都是"没赋过值" ——
    而它们的值是在**别处**算出来的。实测 14 处告警里 13 处是这一类
    (`TAB_MeterSty.style`/`g_CurTime`/`g_HisTime` 是文件级, `'Run_TaskVessel'::STR1_Index`
    是同一文件里另一个函数的静态量)。"本函数里没有它的声明" 是**可判的语法事实**,
    判出来就不该再说"断点前无赋值"。

    初值形状分两档, 都是语法事实、不含源码正文:
      · `None` —— 声明无初值(`INT32U used;`), 下面等着被赋;
      · `"常量"` —— 初值是单个字面量/标识符(`BOOL flag = OTHER;`), 是**占位**,
        断点位上读到的就是它;
      · `"表达式"` —— 初值是算出来的(`ID_RECD id = (TRUE == …) ? … : …;`), 那**就是真值**,
        读到它不叫"读的是初值"。
    这一档正是"断点前唯一赋值 == 声明行"那条判据要用的: 带表达式初值的声明被误判过
    (`DLT645App.c:9244` 的 `id`)。
    """
    for i in range(lo - 1, hi):
        ln = code[i]
        if not ctext.decl_line(ln, base):
            continue
        occ = occurrences(code, i + 1, base)
        if not occ:
            continue
        start, end = occ[0]
        tail = ctext.skip_subscripts(ln[end:].lstrip())
        if not tail.startswith("="):
            return (i + 1, None)
        init, depth, j = tail[1:], 0, 0
        while j < len(init):                       # 初值文本 = 到本层 `;` / `,` 为止
            ch = init[j]
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                if depth == 0:
                    break
                depth -= 1
            elif depth == 0 and ch in ";,":
                break
            j += 1
        simple = bool(re.fullmatch(r"[A-Za-z_]\w*|[-+]?(?:0[xX][0-9a-fA-F]+|\d+)",
                                   init[:j].strip()))
        return (i + 1, "常量" if simple else "表达式")
    return (None, None)
