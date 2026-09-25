# -*- coding: utf-8 -*-
"""扫全仓 .py: 找出**真做了 % 格式化**的字符串里, 那个不是合法转换符的 `%`。

由来 —— 2026-09-16 跑 5-9(拉闸) 时, `meterlib/cmd_bank.py` 的 `relay_roundtrip`
(5-9 转成脚本显式步骤后已删, 那段文字现住 `project/tests/_test_5_9_relay_off.py`) 里有一句:

    "判定闭锁(电压<75%Un), 本次%s按防误跳不产生 —— 本条本次不适用" % ev

`75%Un` 那个 `%` 没写成 `%%`, Python 把 `%U` 当转换符, 当场
`ValueError: unsupported format character 'U' (0x55) at index 10`, 那一趟整个跑挂
(退出码 2 / 未定论, log `log/5_9_relay_off_20260916_144416.log`)。09-16 已改成 `%%`,
09-18 复跑不再现。

要钉它是因为**坑长在中文措辞里**: 本仓满篇 `75%Un` / `60%Un` / `120%Un`(继电器动
作许可判定那一族), 注释和 docstring 里写 `75%Un` 是理所当然的; 只要哪一句碰巧被搬进
一个 `%` 格式化的上下文, 就会再犯一次, 而且报错点在**别人写的措辞**里、不在改的那
行逻辑里, 找起来要回看 traceback 才认得出来。

**只认真的做了格式化的串**(是 `%` 运算的左操作数)。注释、docstring、普通字符串里的
`75%Un` 一律不报 —— 它们不参与格式化, 本来就不是错。所以没有误报面。

⚠ 那句踩坑的写法**从没进过版本库** —— 09-16 只活在当天的 working tree 里, 提交前就
改成 `%%` 了。所以这条钉子钉不了"历史那个 commit", 只能钉"这一类写法"; 它自己能不能
报得出来, 由 `--selftest` 当场造一个反例来证(照本仓老规矩: 改一个字节应校验不过,
否则"校验通过"是句空话)。

用法:
    python scripts/_check_fmt.py            # 扫全仓
    python scripts/_check_fmt.py <目录>      # 扫指定目录
    python scripts/_check_fmt.py --selftest # 只验这条钉子自己报不报得出来
退出码 0 = 干净, 1 = 有发现。
"""
import ast
import io
import os
import re
import sys

# Python 认的转换符(printf 那套): d i o u x X e E f F g G c r s a %
_CONV = set("diouxXeEfFgGcrsa%")

# 一个转换说明 = % + [映射键] + [旗标] + [宽度] + [.精度] + [长度修饰] + 转换符
_SPEC = re.compile(r"%(\([^)]*\))?([-+ #0]*)(\*|\d+)?(\.(\*|\d+))?([hlL])?(.)", re.S)

_SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules"}


def bad_conversions(s):
    """返回 [(位置, 整个转换说明, 那个转换符)] —— 只挑转换符不合法的。"""
    out = []
    for m in _SPEC.finditer(s):
        c = m.group(7)
        if c not in _CONV:
            out.append((m.start(), m.group(0), c))
    return out


def check_source(path, text):
    """返回 [(行号, 整个转换说明, 那个转换符)]。语法错误交给调用方。"""
    tree = ast.parse(text)
    hits = []
    for n in ast.walk(tree):
        # 只看 `字面量 % 参数` 这种真在做格式化的
        if not (isinstance(n, ast.BinOp) and isinstance(n.op, ast.Mod)):
            continue
        left = n.left
        if not isinstance(left, ast.Constant):
            continue
        # str 和 bytes 都会走 % 格式化、也都会抛同一个 ValueError, 两个都收
        if not isinstance(left.value, (str, bytes)):
            continue
        s = left.value.decode("latin-1") if isinstance(left.value, bytes) else left.value
        for _pos, spec, c in bad_conversions(s):
            hits.append((left.lineno, spec, c))
    return hits


# 自检样本: 第一句是**对的**(踩坑当天改成的形态), 第二句是 09-16 那个**错的**,
# 第三句的 `75%Un` 只在 docstring 里、没参与格式化, 不该报。
_SAMPLE = '''\
def f(ev):
    """电压<75%Un 时这条不适用。"""
    a = "判定闭锁(电压<75%%Un), 本次%s按防误跳不产生" % ev
    b = "判定闭锁(电压<75%Un), 本次%s按防误跳不产生" % ev
    return a, b
'''


def selftest():
    """验这条钉子**报得出来** —— 反例是现造的一句话, 不是历史里那个 commit。"""
    ok = True

    def chk(label, cond, extra=""):
        print("%-46s %s%s" % (label, "OK" if cond else "FAIL", ("  " + extra) if extra else ""))
        return bool(cond)

    hits = check_source("<sample>", _SAMPLE)
    ok &= chk("反例被抓住(1 处)", len(hits) == 1, "抓到 %d 处" % len(hits))
    if hits:
        ok &= chk("抓的正是转换符 U", hits[0][2] == "U", "抓到 %r" % (hits[0][2],))
        ok &= chk("报在对的那一行(第 4 行)", hits[0][0] == 4, "报在第 %d 行" % hits[0][0])
    ok &= chk("docstring 里的 75%Un 不误报", not [h for h in hits if h[0] == 2])

    # 正例: 当天修好的形态(`%%Un`)必须一处都不报
    good = check_source("<good>",
                        'x = "电压<75%%Un, 本次%s不适用" % ev\n')
    ok &= chk("正例(%%Un)不报", not good, "%s" % (good,))

    # 老规矩: 转换符合法的都得放过, 别把 `%5.2f` 这种也当错
    for spec in ("%d", "%5.2f", "%-8s", "%(k)s", "%x", "%%"):
        g = check_source("<s>", 'x = "a%sb" %% (1,)\n' % spec)
        ok &= chk("合法 %s 不报" % spec, not g, "%s" % (g,))

    print("SELFTEST _check_fmt:", "ALL PASS" if ok else "HAS FAILURE")
    return ok


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
            for lineno, spec, c in hits:
                nh += 1
                print("%s:%d  %%-转换符 %r 不合法  <-  %r"
                      % (os.path.relpath(p, root), lineno, c, spec))
    print("FORMAT CHECK: %s (%d 个 .py, %d 处)"
          % ("OK" if nh == 0 else "FAIL", nf, nh))
    return 1 if nh else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
