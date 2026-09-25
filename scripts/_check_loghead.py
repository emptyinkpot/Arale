# -*- coding: utf-8 -*-
"""扫全仓 .py: 抓「调用点自己往功能名里写日志头」。

**这条钉子是为什么立的**
    2026-09-18 实表跑 3-1, 帧行打出来是这样:

        [发→管理芯] [发送→管理芯] 645 读费率参量 年时区数(zone, DI 04000201) 20B  FE FE …

    头**打了两遍**, 而且收帧那一行也写着"发送"。根因: 有 9 处调用点把
    `"[发送→管理芯] 645 读费率参量 …"` 整串当成**功能名**传进去(`head=`/`what=`),
    而 `loglabel` 又按字段加了一次头。
    这不是"手滑", 是**两条通道说同一件事**——字段(`proto`/`peer`)一条, 自由文本一条。
    两条通道必然打架, 而且自由文本那条永远赢(它后到、原样拼接)。所以这 9 处改对**不算修好**:
    第 10 处照样写得出来, 而且写出来的东西**看着完全合理**。

**判据(四条, 全部针对 `head=`/`what=`/`name=` 这类"功能名"实参)**
    ① 以 `[` 开头                        —— 这是"头的形状"
    ② 含 `→管理芯` / `←管理芯` / `→计量芯` / `←计量芯`  —— 方向格是 loglabel 出的
    ③ 以 `645` / `698` / `AA80` 开头      —— 协议格是帧自己的属性(`loglabel.Frame`)
    ④ 含 `[645]` / `[698]` / `[AA80]`     —— 同上, 换个写法也一样
    ⑤ 表达式里取了 `["id"]`               —— 帧 id 拼进功能名(如 `"%s | %s" % (s["id"], s["name"])`)
                                            这一条**静态看不出协议词**, 只能按结构抓: 帧 id 第一位
                                            就是协议(`645.factory`), 拼进去等于把协议格又说了一遍

    ⚠ 不禁 `[` 本身: `"…  [watch_g_RateNo]"` 那种方括号是**功能名的一部分**, 不是头。
      禁的只是"头那个位置"(行首)与"头那三个词"。

用法
------
    python scripts/_check_loghead.py             # 扫全仓 src/
    python scripts/_check_loghead.py <目录>       # 扫指定目录
    python scripts/_check_loghead.py --selftest  # 只验这条钉子自己报不报得出来
"""
import ast
import io
import os
import sys

_SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules"}

# 这些关键字实参, 装的是**给人读的功能名**
NAME_KWARGS = {"what", "head", "name"}

# 这些函数, **第一个位置实参**也是功能名(`_opout(head, frame, res, …)`)
NAME_FIRST_ARG = {"_opout"}

# 头里那几个词 —— 用了词表常量表达, 免得这里写第二份
_DIRS = ("→管理芯", "←管理芯", "→计量芯", "←计量芯")
_GRID = ("[645]", "[698]", "[AA80]")
_PROTOS = ("645", "698", "AA80")


def _strings(node):
    """把一个实参表达式里**所有**字符串常量摘出来(含 `%` 右值、相邻拼接、f-string 的字面段)。

    只要有一片像头, 就报 —— 不必判断它是不是最终那个格式串: 头出现在哪一片都是头。
    """
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.append(n.value)
    return out


def _takes_id(node):
    """表达式里有没有 `X["id"]` —— 帧 id 拼进功能名的那种(判据⑤)。"""
    for n in ast.walk(node):
        if isinstance(n, ast.Subscript):
            sl = n.slice
            if isinstance(sl, ast.Constant) and sl.value == "id":
                return True
    return False


def _looks_like_head(s):
    """返回命中的判据号, 没命中返回 None。"""
    t = s.lstrip()
    if t.startswith("["):
        return "①以 `[` 开头"
    for d in _DIRS:
        if d in s:
            return "②含方向格 `%s`" % d
    for p in _PROTOS:
        if t.startswith(p):
            return "③以协议词 `%s` 开头" % p
    for g in _GRID:
        if g in s:
            return "④含协议格 `%s`" % g
    return None


def _name_literals(tree):
    """`head = "[发送→管理芯] 645 …"` —— 先装进变量、再传给调用点的, 静态也得抓得到。

    做法很土: 文件内 `名字 = <表达式>` 一律记下 (赋值行, 字符串常量);
    按名字查, **不按作用域查**。所以同名两处会互相牵连 —— 于是**报赋值行、不报调用行**:
    一行一行地报"这里写了个像头的字符串", 才是能直接照着改的东西。
    (`head, peer = "读表钟", …` 这种元组赋值也收: 逐个元素摘。)
    """
    m = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        names = []
        for tgt in node.targets:
            if isinstance(tgt, ast.Name):
                names.append((tgt.id, node.value))
            elif isinstance(tgt, (ast.Tuple, ast.List)):
                vals = list(node.value.elts) if isinstance(node.value, (ast.Tuple, ast.List)) else []
                for j, e in enumerate(tgt.elts):
                    if isinstance(e, ast.Name) and j < len(vals):
                        names.append((e.id, vals[j]))
        for nm, val in names:
            for s in _strings(val):
                m.setdefault(nm, []).append((val.lineno, s))
    return m


def check_source(path, text):
    """→ [(行号, 判据, 那片原文), …]"""
    hits = []
    tree = ast.parse(text)
    named = _name_literals(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else (
            fn.attr if isinstance(fn, ast.Attribute) else None)

        args = []
        for kw in node.keywords:
            if kw.arg in NAME_KWARGS:
                args.append((kw.value.lineno, kw.value))
        if name in NAME_FIRST_ARG and node.args:
            args.append((node.args[0].lineno, node.args[0]))

        for lineno, val in args:
            hit = False
            for s in _strings(val):
                why = _looks_like_head(s)
                if why:
                    hits.append((lineno, why, s, ""))
                    hit = True
                    break
            if not hit and _takes_id(val):
                hits.append((lineno, "⑤功能名里拼了帧 id", ast.unparse(val)[:60], ""))
            if isinstance(val, ast.Name):
                # 传的是变量 ⇒ 追它的赋值, **报赋值那一行**(那才是落笔处, 也是该改的那一行)
                for aline, s in named.get(val.id, []):
                    why = _looks_like_head(s)
                    if why:
                        hits.append((aline, why, s, "  ← 赋给 `%s`, 报的是**赋值行**" % val.id))
    return sorted(set(hits))                         # 同一个赋值行会被多个调用点追到, 只报一次


def selftest():
    """照本仓老规矩: 这条钉子得**当场证明它报得出来** —— 造个反例让它自己红一次。"""
    bad = (
        'def f():\n'
        '    head = "[发送→管理芯] 645 读费率参量 年时区数(zone, DI 04000201)"\n'
        '    _opout(head, None, "x")\n'
        '    g(what="[645] 读表钟")\n'
        '    h(what="698 Set 写 400C0204")\n'
        '    k(name="读表钟 →管理芯")\n'
        '    m(what="%s | %s" % (s["id"], s["name"]))\n'
    )
    hits = check_source("<反例>", bad)
    # 5 处, 行 2 = `head` 的**赋值行**(不是调用行 3), 4/5/6 = 三个字面量实参, 7 = 拼了帧 id
    lines = sorted(h[0] for h in hits)
    if len(hits) != 5 or lines != [2, 4, 5, 6, 7]:
        print("SELFTEST FAIL: 反例应报 5 处(行 2/4/5/6/7), 实报 %r" % (hits,))
        return False

    good = (
        'def f():\n'
        '    head = "读费率参量 年时区数(zone, DI 04000201)"\n'
        '    _opout(head, frame, "值[1B]=01")\n'
        '    g(what="直读 RAM g_RateNo(=0x20009088-基址)  [基线]")\n'
        '    h(what="读 DI %s" % di)\n'
        '    k(name=None)\n'
    )
    hits = check_source("<正例>", good)
    if hits:
        print("SELFTEST FAIL: 干净的写法被误报: %r" % (hits,))
        return False

    print("SELFTEST _check_loghead: ALL PASS")
    return True


def main(argv):
    if "--selftest" in argv:
        return 0 if selftest() else 1
    root = argv[1] if len(argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    nf = 0
    nh = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in sorted(filenames):
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            nf += 1
            text = io.open(p, encoding="utf-8").read()
            try:
                hits = check_source(p, text)
            except SyntaxError as e:
                print("!! 读不动(语法错) %s: %s" % (p, e))
                nh += 1
                continue
            for lineno, why, s, note in hits:
                nh += 1
                print("%s:%d  %s%s  <-  %r" % (os.path.relpath(p, root), lineno, why, note, s))
    print("LOGHEAD CHECK: %s (%d 个 .py, %d 处)"
          % ("OK" if nh == 0 else "有 %d 处头写在了调用点" % nh, nf, nh))
    return 0 if nh == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
