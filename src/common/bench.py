# -*- coding: utf-8 -*-
"""
bench.py —— 台面体检: 一条命令问清 "串口和 SWD 两条链路现在还通不通"。

入口脚本是 `scripts/_check_bench.py`(顶层运行脚本, 与 `_restore_all.py` 同类, **不是**
`project/tests/` 那本子册子里的测试子项) —— 随时可单独喊, 不跟着某一项测试走。
`project/tests/_suite.py` 要在每项之前插一次, 直接 import 本模块调 `check_bench` 就行。

本模块是**组装点**: 它把底下三样已经各自成家的东西按次序装起来, 自己不持有任何东西 ——
画像断言在 `project.env_check`, 串口参数与选口在 `common.portsel`, 探针判据与选探针在
`swdbg.probesel`。换一块表、换一支探针, 本文件一个字不用改。

四步各答一个问题:

    ① 离线断言   画像字段 + 帧清单 + 固件声明与 .out 指纹   —— 不碰硬件
    ② 串口       COM3 后面是不是本表(证明本身含读一次表钟) —— `portsel.open_com`
    ③ 探针       探针后面是不是本表那颗核(读 CPUID)        —— `probesel.pick`
    ④ SWD 三关   FPB 断点 + DWT 观察点还挂得上吗           —— `swdbg.selfcheck`(仅 full)

⚠ ① 不许省掉: 画像或 `.out` 被换过时, ② ③ 照样会通 —— 那时"通了"是假象, 后面每一条测试都在
  对着一份过期的声明跑。所以离线断言不过就停在那儿, 不去碰硬件。

⚠ 任一步不过就**就地收摊**, 后面的步不跑: 它们必然也过不了, 接着跑只是白等超时。

本模块**不打印, 也不往 `log/` 写一个字** —— 打印是调用方的事(入口脚本读 `Report` 来说),
`log/` 是测试结论的地方, 而这里答的是"台子通不通"。
"""
import time


def check_env():
    """① 离线断言 → (ok, 说明)。不碰硬件。"""
    from project import env_check
    rows = list(env_check.env_check(online=False))
    bad = [m for ok, m in rows if not ok]
    if bad:
        return False, "\n".join("· %s" % m for m in bad)
    return True, "%d 条断言全过。" % len(rows)


def check_serial():
    """② 串口: 开 COM3 并证明口后面是本表 → (ok, 说明)。

    那个证明本身就含读一次表钟 —— `portsel` 会把 "· COM3 应了, 表钟=…" 打在它自己那两行
    `[串口]` 里。所以这里**不再重复读一遍**(同一件事读两次只是多占一次连接)。
    """
    from common.portsel import open_com
    ser = open_com()
    try:
        return True, "口开起来了, 证明通过(表钟见上面那行 `[串口]` 记录)。"
    finally:
        try:
            ser.close()
        except Exception:
            pass


def check_probe():
    """③ 探针: 证明探针后面是本表那颗核(读 CPUID) → (ok, 说明)。

    走到这儿就说明已经证明过了 —— `pick()` 是 "判据 → 候选 → 逐个真连读 CPUID → 恰好一支",
    读不到就抛, 不会带着没证明过的探针往下走。
    """
    from swdbg import probesel
    backend, ident, _report = probesel.pick()
    return True, "选中 %s 端 %s(CPUID 读到了)。" % (backend, ident)


def check_swd():
    """④ SWD 三关(开一场 gdb 会话): 探针 / FPB 断点 / DWT 观察点 → (ok, 说明)。

    交给 `swdbg.selfcheck`, 不在这里重写一遍 —— 一处实现, 免得两边哪天对不上。
    """
    from swdbg import selfcheck
    rc = selfcheck.main([])
    return rc == 0, "`python -m swdbg.selfcheck` 退出码 %d。" % rc


class Step:
    """一步: 叫什么 + 干什么。名字只用于打印, 不参与分派。

    步骤函数只回 `(ok, 说明)` 两样, 名字与耗时由 `check_bench` 补上 —— 所以这两样要加字段,
    只动这一处, 不必回头改每个步骤函数。
    """

    def __init__(self, name, run):
        self.name = name
        self.run = run


class Result:
    """一步跑完的结论。**没跑的步不产生 Result** —— 少了几步见 `Report.skipped`。"""

    def __init__(self, name, ok, why, seconds):
        self.name = name
        self.ok = ok
        self.why = why
        self.seconds = seconds


class Report:
    """一次台面体检的结论。

    `rows` 只含**真跑过的**步; 因为前面不过而没跑的步数在 `skipped`。不往 `rows` 里塞一行
    假结果标"没跑": "没跑"与"跑了没过"必须一眼分得开。
    """

    def __init__(self, ok, rows, skipped):
        self.ok = ok
        self.rows = rows
        self.skipped = skipped


def _steps(want_serial, want_probe, full):
    """这一次要跑的步, 按次序。`full` 只对探针那半边有意义。"""
    todo = [Step("离线断言", check_env)]
    if want_serial:
        todo.append(Step("串口", check_serial))
    if want_probe:
        todo.append(Step("探针", check_probe))
        if full:
            todo.append(Step("SWD 三关", check_swd))
    return todo


def check_bench(want_serial=True, want_probe=True, full=False):
    """跑一遍台面体检 → `Report`。

    两半至少要开一半: 都关掉等于什么都没验却回一句"通过", 那种静默的错答案比报错危险。
    """
    if not (want_serial or want_probe):
        raise ValueError("串口与探针至少要开一半 —— 两半都不开就没有任何东西被验过。")
    todo = _steps(want_serial, want_probe, full)
    rows = []
    for st in todo:
        t = time.time()
        try:
            ok, why = st.run()
        except Exception as exc:
            ok, why = False, "%s: %s" % (type(exc).__name__, exc)
        rows.append(Result(st.name, ok, why, time.time() - t))
        if not ok:
            break
    return Report(all(r.ok for r in rows), rows, len(todo) - len(rows))
