# -*- coding: utf-8 -*-
"""ctext.py —— C 源码的**文本原语**: 抠注释与字符串、认函数定义行、判一次出现是读还是写。

管的全是"这一行字长什么样" —— 不含断点、不含协议、不含画像, 所以按本层判据住中立层。
原先这几件只住在 `discover/source.py`(足迹分析要用)。断点那条线
(`swdbg/csrc.py` + `swdbg/breakpoint.py`)要问的是**同一批问题**: 这一行是不是声明、
这次出现算不算写。两处各写一套必然漂移, 而漂移的表现是"同一个变量, 足迹说被写、
断点体检说没写" —— 读的人无从判断该信哪一份。

⚠ **等长替换**是这一层的关键性质: 注释与字符串整段换成**等长空格**, 所以行号与列偏移都不变。
调用方按下标取字(`code[start:end]`)那类写法因此照旧成立; 而"注释里的赋值不算赋值"
"字符串里的名字不算名字"这两条也就同时成立了。

这里是**文本级**分析, 不是编译级: 宏包一层、`#if` 掉的死代码、经函数指针转手的写, 都看不见。
"""
import re

__all__ = ["IDENT", "strip_comments", "func_of", "skip_subscripts", "kind", "decl_line"]

IDENT = re.compile(r"[A-Za-z_]\w*")

# 声明行: 前面若干"类型词/限定词", 紧跟 NAME。用来把 `extern INT8U g_CurTime[6];`
# 之类从"读"里摘出来 —— 声明不算读。
QUAL = r"(?:static|extern|volatile|const|register|unsigned|signed|struct|union|enum)\s+"
# ⚠ 类型词的位置**必须挡住控制关键字**。踩过: `DECL_TPL` 原本写成裸的
# `[A-Za-z_]\w*\s+`, 于是 `return g_RateNo[0];` 里的 `return` 被当成类型名、
# `g_RateNo` 被当成被声明的变量 —— 足迹里凭空多出一条"声明"(实测 TaskRate.c:688)。
NOTKW = r"(?!(?:return|if|while|for|switch|else|case|do|break|continue|goto)\b)"
DECL_TPL = (r"^\s*(?:" + QUAL + r"|" + NOTKW + r"[A-Za-z_]\w*\s+|[A-Za-z_]\w*\s*\*\s*)+"
            r"[\s\*]*\b%s\b\s*(?:\[|;|=|,)")

# 函数定义行。IAR 工程的风格是名字在行首(或紧跟类型), 且行尾 `{`/`)` 后面没有 `;`
# (有 `;` 就是原型)。控制关键字不算。
CTRL = ("if", "for", "while", "switch", "return", "else", "do", "case", "sizeof")
FUNC_TPL = re.compile(r"^(?:[A-Za-z_]\w*[\s\*]+)+([A-Za-z_]\w*)\s*\(")

# 复合赋值 / 自增自减 —— 都算写(它们同时读也写, 但结论上"会改它")。
ASSIGN_OPS = ("+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>=")


def strip_comments(line, in_block):
    """一行源码 → 只留代码(注释/字符串**等长空格**替换掉), 行号与列位置都不变。

    等长替换是关键: 行号与后面 `code[start:end]` 的偏移都还是对的。
    返回 (code_only, in_block) —— 块注释状态跨行, 必须由调用方接着传。
    """
    out, i, n = [], 0, len(line)
    while i < n:
        if in_block:
            j = line.find("*/", i)
            if j < 0:
                out.append(" " * (n - i))
                i = n
            else:
                out.append(" " * (j + 2 - i))
                i = j + 2
                in_block = False
            continue
        c = line[i]
        if c == "/" and line.startswith("/*", i):
            in_block = True
            out.append("  ")
            i += 2
            continue
        if c == "/" and line.startswith("//", i):
            out.append(" " * (n - i))          # 行注释吃掉到行尾
            i = n
            continue
        if c == '"' or c == "'":
            q, j = c, i + 1
            while j < n:
                if line[j] == "\\":
                    j += 2
                    continue
                if line[j] == q:
                    j += 1
                    break
                j += 1
            out.append(" " * (j - i))          # 字符串/字符字面量整体抹平
            i = j
            continue
        out.append(c)
        i += 1
    return "".join(out), in_block


def func_of(code):
    """这一行是不是函数定义的头 → 函数名; 否则 None。

    粗糙但够用: 名字必须是行内第一个 `(` 之前那个标识符, 且该行不以 `;` 收尾(原型),
    也不以控制关键字起头。跨行的 K&R 风格签名会被漏掉 —— 调用方照实标"函数归属为启发式"。
    """
    t = code.strip()
    if not t or t.startswith("#"):
        return None
    head = t.split("{")[0]
    if ";" in head:
        return None                            # 有 `;` = 声明/原型, 不是定义
    first = IDENT.match(t)
    if first and first.group(0) in CTRL:
        return None
    m = FUNC_TPL.match(t)
    if not m:
        return None
    name = m.group(1)
    return None if name in CTRL else name


def skip_subscripts(s):
    """去掉开头的下标组(`[i]`、`[i][j]`、含嵌套的 `[TAB[i].k]`)。没有下标就原样返回。

    ⚠ **没有这一步, `arr[i] = v` 会被判成读** —— 2026-09-10 实测踩到(`Run_TaskRate` 里
    `backup[0] = g_SlotSwNo;` 四行全被判 read), 后果不止断点体检: 一个**只靠元素写入**的数组,
    它整个 `writes` 足迹是空的, 于是"这个变量从没被写过"这种结论会凭空出现。
    括号不配对(跨行/宏)时**不敢猜**, 原样返回 —— 猜错等于把读说成写, 比漏报坏。
    """
    i, n = 0, len(s)
    while i < n and s[i] == "[":
        depth, j = 0, i
        while j < n:
            if s[j] == "[":
                depth += 1
            elif s[j] == "]":
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            j += 1
        if depth != 0:
            return s
        i = j
        while i < n and s[i] in " \t":
            i += 1
    return s[i:]


def kind(code, start, end):
    """一次出现 → 'write' | 'read' | 'addr_taken' | 'sizeof' | 'skip'。"""
    # 成员访问的右半边不算(g_A.fld 里的 fld)。否则同名成员会污染全局符号的足迹。
    if start > 0 and code[start - 1] == ".":
        return "skip"
    if code[max(0, start - 2):start] == "->":
        return "skip"

    before = code[:start].rstrip()
    after = code[end:]
    arest = after.lstrip()
    # 判"读还是写"看**下标之后**的那一段: `x[i] =` 与 `x =` 同义, `x[i] ==` 与 `x ==` 同义。
    # 没有下标时 asub == arest, 行为与历史一致。
    asub = skip_subscripts(arest)

    if before.endswith("sizeof("):
        return "sizeof"
    if before.endswith("++") or before.endswith("--"):
        return "write"                         # ++x / --x
    if before.endswith("&") and not before.endswith("&&"):
        return "addr_taken"                    # &x —— 可能被指针改写, 单列存疑
    if asub.startswith("++") or asub.startswith("--"):
        return "write"                         # x++ / x[i]++
    if asub.startswith("=="):
        return "read"                          # 比较是读, 别被 `=` 骗了
    if asub.startswith("="):
        return "write"                         # x = ... / x[i] = ... (上面已挡掉 ==)
    for op in ASSIGN_OPS:
        if asub.startswith(op):
            return "write"                     # x += ... / x[i] += ...
    return "read"


def decl_line(code, name):
    """这一行的这次出现是不是**声明**(而非读写)? 声明不算足迹。"""
    return bool(re.match(DECL_TPL % re.escape(name), code))
