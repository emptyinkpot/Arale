# -*- coding: utf-8 -*-
"""
swdbg/gdbinit.py —— **整片 .out 地图**: 一场会话解一次, 之后谁都不许再独立反汇编

为什么要有它
------------
"地址由 `.out` 推出来、不许手抄"这条纪律原先散成四处 —— `callees` / `inject_anchor` /
`inject_anchors` / `decision_anchor` 各反汇编各的, 而 `anchor_of` 自己还带一份
`_anchor_cache`。两个后果:

- 同一个函数被反复反汇编(`inject_anchor` 与紧随其后的 `decision_anchor` 各解一遍);
- **窗口是经验值**: `window=0x400` 是"大概率够"的猜测, 而"第 n 处调用"是位置敏感的 ——
  窗口小了会把后面的调用点静默切掉, 于是"第 2 处"永远找不到, 表象是"这个锚点解不出来",
  真因却是窗口(实测 `Check_BillFrezY` 的两处 `Write_FrezData` 落在 +1078 与 +1312,
  后者在 0x400 之外)。

本模块把这件事收成一步: 脚本开头 `gdbinit.build(ctx.g)` 把**整片代码段**解一遍, 建出一张
"函数 → 指令流 + 调用点"的图挂到会话上; 之后所有查询只读这张图, 不再有人反汇编。

地图(挂在 `g._elfmap`, 会话内不变, **不落盘、不跨会话**)
--------------------------------------------------------
    {"funcs": {"Run_TaskFreeze": {"addr": 0x320D8, "end": 0x32270,
                                  "insns": [(0x320D8, "bl 28088 <Get_MeterTime>"), …],
                                  "calls": [{"callee": "Get_MeterTime", "bl": 0x320D8,
                                             "prev": 0x320D4, "next": 0x320DC, "n": 1}, …]},
               …},
     "syms": {"g_CurTime": 0x200034D0, …},      # .symtab 全部符号(离线读, 不占停核时间)
     "lines": {("TaskFreeze.c", 425): 0x32578, …},   # DWARF 行表: 该行的起始地址
     "lo": …, "hi": …, "n_insns": …, "seconds": …, "collisions": {名字: [地址, …]}}

`insns` 是**整条指令流**, 不只是调用点 —— `decision_anchor` 要读判定句的**前一句**与
**后一句**, 只有 `calls` 满足不了它。

`lines` 与上面几项**不同源**: 它不走探针、不停核, 是 `arm-none-eabi-readelf
--debug-dump=decodedline` 读同一份 `.out` 的 DWARF 行表得来的。所以**行锚点也由图解**,
与 `call`/`prev`/`func` 一样没有第二个去处。同一行有多条起始地址时取**最小**那条 ——
与 gdb `info line` 的答案逐条比过, 全仓 67 个行锚点一致 67 / 不一致 0。

`insns` 每条形如 `(地址, 指令原文)`; 人读的 `loc`(`Run_TaskFreeze+28`)是查询时按
`地址 - 函数入口` 现算的 —— gdb 的 `offset` 字段是**十进制、相对函数入口**, 照它印出来的
就是同一个数, 但分块扫时不能靠它(gdb 只在它认得函数时才给, 填充区那条没有)。

代价与前提
----------
建图要把核**停在停住态**遍历整片(IWDT 约 8s 复位, 所以没有 watchdog 不许建图), 期间每块
喂一次狗。实测 0x47B98 字节的代码段 **26.7 s**(1416 函数 / 137452 条指令 / 3011 符号 / 23299 行)。
`build()` 收尾**把核放回运行态**。
"""

import os
import re
import subprocess
import time

from swdbg import elf as elfsym
from common import loglabel

__all__ = ["build", "get_map", "map_of", "calls_of", "func_entry", "insns_of",
           "disasm", "addr_tok", "GdbError",
           "vars_resolve", "resolve_spec", "offline_map", "src_at", "func_addr", "line_start", "line_addr"]

CHUNK = 0x200            # 每次 `-data-disassemble` 的窗口(实测 0.04s / 约 108 条指令)
FEED_EVERY = 1.0         # 建图时每隔这么多秒喂一次狗(IWDT 约 8s 复位, 留足余量)


class GdbError(RuntimeError):
    """连不上 / 断点下不去 / MI 协议出错时抛这个, 带人话解释。"""


def addr_tok(text, default=None):
    """MI 里的**地址字段 → int**: 真 gdb 常把地址与符号名一起给(`'0x220c8 <Calculate_RateNo>'`,
    `'0x220da <Calculate_RateNo+18>'`) ⇒ 直接 `int(..., 0)` 会当场 ValueError。

    ⚠ 这是一处**真踩过的**坑(2026-09-11): `inject_anchor` 首跑真表就死在这里 —— 而离线的假会话
    当初喂的是干净值 `'0x220c8'`, 断言照样全绿。**假件必须长得像真件**(同 3-2 的 `chk` 元数、
    `with_trigger` 的 timeout: 文档里写了的东西, 得有一条断言真的走一遍)。"""
    m = re.match(r"\s*(0[xX][0-9a-fA-F]+)", str(text or ""))
    return int(m.group(1), 16) if m else default


# ============================================================================
# ① 反汇编原语 —— 全场唯一的 `-data-disassemble` 出口
# ============================================================================
def disasm(g, start, end, timeout=20.0):
    """反汇编 `[start, end)` —— **调用前核必须已在停住态**。返回 MI 的 `asm_insns` 列表。

    ⚠ **核在跑着发这条命令会挂死**(2026-09-11 实踩): `-data-disassemble` 要逐条读目标内存,
    而跑着的核不给读 ⇒ 命令**永远不回来**(实测 30s 超时, 之后连管道都废了 `OSError errno 22`)。
    停住之后同一条命令 **0.04s** 就回来(108 条)。所以调用方先 `ensure_stopped()`。

    本函数**自己不叫停** —— 整片扫描要发五百多次, 每次叫一遍会退化成五百多次
    `-exec-interrupt`; 而 `_stopped` 是不可信的(核真停着时它也可能报 False), 一旦报错
    就是每次白等到超时。叫停一次、留在停住态, 由调用方声明收尾。
    """
    r = g._cmd("-data-disassemble -s 0x%X -e 0x%X -- 0" % (start, end), timeout=timeout)
    if not r or r.get("_class") != "^done":
        raise GdbError("反汇编 0x%X..0x%X 失败: %s" % (start, end, (r or {}).get("msg", "无应答")))
    return [x for x in (r.get("asm_insns") or []) if isinstance(x, dict)]


def _norm_inst(x):
    """一条 MI 指令记录 → `(地址, 指令原文)`; 地址解析不了给 None。"""
    return addr_tok(x.get("address")), str(x.get("inst") or "").replace("\t", " ").strip()


# ============================================================================
# ② 符号表(纯离线读 .out, 不占停核时间)
# ============================================================================
def _read_syms(out):
    """`.out` 的符号表 → `(函数表, 全符号表, 重名表, 扫描上界, 函数长度表)`。

    `函数表` = `{名字: 地址}`(**STT_FUNC**, 静态函数也在内 —— 它们是 `bl` 的目标);
    地址重复的名字(同一 `static` 名落在两个 `.c` 里)取**地址最小的那个**, 并把重名记进
    `collisions` 打印出来 —— 静默合并会让"我打的是哪一个"无从回答, 而它只在运行期才露头。

    `扫描上界` 取 `最大(函数入口 + 长度)`, 不取"最大入口" —— 末一个函数体可能长过一块窗口。

    ⚠ 函数符号的 `st_value` 带 Thumb 位(实测 `Run_TaskFreeze` = `0x320BD`), 而 gdb 与 FPB
      比的是**取指地址**(`0x320BC`)⇒ 一律 `& ~1` 去掉。不去掉的话断点会插到奇数地址上,
      而 `gdb` 会不会替你圆回来是它的实现细节, 不该赌。
    """
    elf = elfsym.load(out)
    tab = elf.get_section_by_name(".symtab")
    if tab is None:
        raise GdbError("这个 .out 里没有 .symtab, 建不了地图: %s" % out)
    syms, funcs, collisions, top, sizes = {}, {}, {}, 0, {}
    for s in tab.iter_symbols():
        name, val = s.name, s["st_value"]
        if not name or not val:
            continue
        if name not in syms:
            syms[name] = val
        if s["st_info"]["type"] != "STT_FUNC":
            continue
        val &= ~1                      # ⚠ Thumb 位, 见上面那条
        if name in funcs:
            collisions.setdefault(name, [funcs[name]]).append(val)
            if val < funcs[name]:
                funcs[name] = val
                sizes[name] = s["st_size"]     # 长度跟着**选中那个**入口走(重名时别拿别人的)
        else:
            funcs[name] = val
            sizes[name] = s["st_size"]
        top = max(top, val + s["st_size"])
    if not funcs:
        raise GdbError("符号表里一个函数都没有, 建不了地图: %s" % out)
    return funcs, syms, collisions, top, sizes


# ============================================================================
# ③ 行表(纯离线读 .out, 不占停核时间, 也不碰探针)
# ============================================================================
READELF = "arm-none-eabi-readelf.exe"     # 目录由机器卡带的 ARM_TOOLCHAIN 给, 名字是通用的


def _read_lines(out, readelf=None):
    """`.out` 的 DWARF 行表 → `{(文件basename, 行号): 该行的起始地址}`。

    行锚点要的是"这一行从哪个地址开始", 而这件事 gdb 只有 `info line` 一条路, 一问一行;
    整片地图要一次问出全部行, 所以改用 readelf 直接读同一份 `.out` 的 `.debug_line` ——
    离线、不停核、不需要探针。

    ⚠ **同一行可能有多条起始地址**(循环体那一行会被拆成几段), 这里取**最小**那条。
      这条选法不是推的: 全仓 67 个行锚点逐个与 gdb `info line` 的答案比过, 一致 67 / 不一致 0。

    ⚠ **该行没编译出指令时**(注释行、被优化掉的守卫), 行表里就没有这一行 —— 这里**不替 gdb
      猜**它该滑到哪一行, 那种写法落空就让人去换一行(见 `map_of`)。实测全仓 67 个行锚点
      一个都没踩到。
    """
    readelf = readelf or _readelf_path()
    try:
        r = subprocess.run([readelf, "--debug-dump=decodedline", out],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        raise GdbError("行表要 %s, 而它跑不起来: %s" % (READELF, exc))
    if r.returncode != 0:
        raise GdbError("%s 读行表失败(退出码 %d): %s"
                       % (READELF, r.returncode,
                          r.stderr.decode("utf-8", "replace").strip()[:200]))
    text = r.stdout.decode("utf-8", "replace")
    out_tab, line_re = {}, re.compile(r"^(\S+)\s+(-|\d+)\s+(0x[0-9a-fA-F]+)")
    for s in text.splitlines():
        m = line_re.match(s.strip())
        if not m or m.group(2) == "-":
            continue                      # `-` 是行号列的"序列结束", 不是一行代码
        # 文件列给的是**路径**(带 CU 目录), 而锚点写的是 basename —— 只留末一段
        k = (os.path.basename(m.group(1).replace("\\", "/")), int(m.group(2)))
        a = int(m.group(3), 16)
        if k not in out_tab or a < out_tab[k]:
            out_tab[k] = a
    if not out_tab:
        raise GdbError("这份 .out 里读出行表 0 行 —— 编译时没带调试行信息? (%s)" % out)
    return out_tab


def _readelf_path():
    """readelf 的路径 = 机器卡带 `ARM_TOOLCHAIN` 目录 + 通用文件名(不另立一个卡带字段)。"""
    from common import machspec                      # 机器事实只问卡带, 本模块不抄路径
    root = machspec.get("ARM_TOOLCHAIN")
    if not root:
        raise GdbError("机器卡带没声明 ARM_TOOLCHAIN, 拿不到 %s —— 行锚点解不了。\n"
                       "  换机器改 `machine/<机器名>.py`; 或在能跑 readelf 的机器上跑。"
                       % READELF)
    return os.path.join(root, READELF)


# ============================================================================
# ④ 建图
# ============================================================================
def build(g, quiet=None):
    """把整片 `.out` 解成地图挂到 `g._elfmap` 上, 并返回它。**每个脚本开头调一次。**

        from swdbg import gdbinit
        ...
        gdbinit.build(ctx.g)          # 之后 ctx.bp(...) / inject_anchor(...) 一律查这张图

    - **必须配 watchdog** —— 建图要把核停住遍历整片, 停着不喂狗约 8s 就被 IWDT 复位。
    - **收尾把核放回运行态**(不这么做, 后面每条串口帧都像"串口坏了")。
    - 同一会话重复调用**直接返回已建的那张**, 不重解。
    """
    if getattr(g, "_elfmap", None) is not None:
        return g._elfmap
    if not getattr(g, "watchdog", None):
        raise GdbError("建地图要把核停住遍历整片, 停着不喂狗约 8s 表会被 IWDT 复位。\n"
                       "  Session(out=..., watchdog=(IWDT_SERV地址, 0x12345A5A)) 之后才许建图。")
    quiet = g.quiet if quiet is None else quiet
    funcs, syms, collisions, top, _sizes = _read_syms(g.out)
    lo = min(funcs.values())
    hi = max(top, max(funcs.values()))
    t0 = time.time()
    insns, seen, last, by_func = [], set(), lo - 1, {}
    p, fed, chunks, stalled = lo, t0, 0, 0
    try:
        g.ensure_stopped()          # 只叫这一次; `disasm` 自己不叫(理由见它的 docstring)
        while p <= hi + CHUNK:
            if time.time() - fed >= FEED_EVERY:
                g.feed_watchdog()   # ⚠ 停核期间表不自己喂狗, 这一下是它唯一的活路
                fed = time.time()
            for x in disasm(g, p, min(p + CHUNK, hi + CHUNK)):
                a, txt = _norm_inst(x)
                if a is None or a > hi or a in seen or not txt:
                    continue        # 超出符号表上界的那几块读到的是填充(0xFFFFFFFF), 不收
                seen.add(a)
                insns.append((a, txt, str(x.get("func-name") or "")))
                last = max(last, a)
            chunks += 1
            if last <= p:           # 这一块没带来新指令(填充/空洞) ⇒ 往前挪一整块
                stalled += 1
                if stalled > 64:
                    raise GdbError("反汇编在 0x%X 处连续 %d 块没进展, 停手(符号表坏了?)"
                                   % (p, stalled))
                p += CHUNK
            else:
                stalled = 0
                p = last            # 从最后一条重开(它会被上面的去重丢掉), 不猜指令长度
    finally:
        g.ensure_running()          # ⚠ 不管成没成, 都不许留一个停住的核出去
    insns.sort(key=lambda t: t[0])
    nxt = [insns[i + 1][0] for i in range(len(insns) - 1)] + [None]
    for i, (a, txt, fn) in enumerate(insns):
        if fn in funcs:
            by_func.setdefault(fn, []).append((a, txt, nxt[i]))
    out_funcs = {}
    for fn, rows in by_func.items():
        start = funcs[fn]
        end = max([e for _, _, e in rows if e] or [rows[-1][0] + 4])
        out_funcs[fn] = {"addr": start, "end": end,
                         "insns": [(a, t) for a, t, _ in rows],
                         "calls": _calls_of(rows)}
    insn_at = {}
    for fn, f in out_funcs.items():
        for a, t in f["insns"]:
            insn_at[a] = (fn, t)
    m = {"funcs": out_funcs, "syms": syms, "insn_at": insn_at,
         "vars": _read_vars(g.out),
         "lines": _read_lines(g.out),
         "lo": insns[0][0] if insns else lo,
         "hi": (insns[-1][0] if insns else hi), "n_insns": len(insns),
         "seconds": time.time() - t0, "collisions": collisions}
    g._elfmap = m
    if not quiet:
        print("   %s" % loglabel.debug_line(
            loglabel.DEBUG_GDB, "地图 %d 个函数 / %d 条指令 / %d 个符号 / %d 行, 0x%X..0x%X, %.1fs"
            % (len(out_funcs), len(insns), len(syms), len(m["lines"]),
               m["lo"], m["hi"], m["seconds"])))
        for name, addrs in sorted(collisions.items()):
            print("   %s" % loglabel.debug_line(
                loglabel.DEBUG_GDB, "⚠ 重名函数 %s 有 %d 个入口(%s), 取最小那个 —— 打的是哪一个"
                                    "要看地址" % (name, len(addrs),
                                                   ", ".join("0x%X" % a for a in addrs))))
    return m


def _calls_of(rows):
    """一个函数体里的**每一个调用点** → `[{"callee","bl","prev","next","n"}, …]`。

    判据与旧的 `callees` 一字不差: 只认 `bl`/`blx`; 后面那条指令必须**还是本函数的**(尾调用
    后面不是本函数的指令, 断在那里没有意义); `n` 是**同一个被调函数的第几处**(1 起)。

    **三处地址各有用处**, 别只记 `next`:
      · `bl`   —— 这次调用本身;
      · `prev` —— `bl` **前一条**指令(函数体第一条 `bl` 就没有, 给 None)。要读**这次调用的实参**
        就得停在这儿: 实参的最后一次使用就是那条 `bl`, 于是它的 DWARF 位置恰在 `bl` 那一刻结束,
        `bl` 与 `bl` 之后都在空洞里(实测见 `_test_4_6_aa80.py` 的 BP_B/BP_C)。
      · `next` —— `bl` 之后那条。喂狗/注入点要的是它: 被调函数会把状态重读/重算, 断在调用之前必被覆盖。
    """
    out, seen = [], {}
    for i, (a, txt, _end) in enumerate(rows):
        m = re.match(r"^blx?\s", txt) and re.search(r"<([^>+]+)", txt)
        if not m or i + 1 >= len(rows):
            continue
        nxt_addr = rows[i + 1][0]
        if nxt_addr is None or nxt_addr <= a:
            continue
        callee = m.group(1)
        seen[callee] = seen.get(callee, 0) + 1
        out.append({"callee": callee, "bl": a, "prev": rows[i - 1][0] if i else None,
                    "next": nxt_addr, "n": seen[callee]})
    return out


# ============================================================================
# ④ 查询 —— `breakpoint` 那一层只走这四个口子, 谁都不许再反汇编
# ============================================================================
def _read_vars(out):
    """`{变量名: {addr, size, section, type}}` —— 前三样来自 .out 的符号表, type 来自 DWARF(没有则 "")。"""
    from swdbg import elf
    try:
        objs = elf.ram_objects(out)
    except Exception:
        return {}
    types = elf.var_types(out)
    return {n: {"addr": a, "size": sz, "section": sec, "type": types.get(n, "")}
            for n, (a, sz, sec) in objs.items()}


def vars_resolve(g, name):
    """变量名 → `{addr, size, section, type}` 或 None。查 `g._elfmap["vars"]`。"""
    return (get_map(g).get("vars") or {}).get(name)


def get_map(g):
    """取会话上那张图; 没建过就抛 —— 静默返回 None 会让每条锚点都变成"解不出来"。"""
    m = getattr(g, "_elfmap", None)
    if m is None:
        raise GdbError("这个会话还没有地图。脚本开头先 `from swdbg import gdbinit; "
                       "gdbinit.build(ctx.g)` —— 之后所有断点/注入点都从图上查。")
    return m


def func_addr(m, func):
    """函数入口地址(函数表给, 不收手抄的地址)。**重名时是地址最小的那个**(见 `_read_syms`)。

    ⚠ 取 `m["funcs"]` 而不是 `m["syms"]` —— 后者是 `.symtab` 的原值, 函数符号的 `st_value`
      带 Thumb 位(奇), 拿它下 `-break-insert "*0x…"` 会插到奇数地址上。FPB 比的是取指地址,
      插进去之后 gdb 的账本(偶)与探针上的比较器(奇)对不上, `-break-delete` 撤不掉, 残留比较器
      在放行后立刻又把核停住 ⇒ 串口从此全无应答(实测 `Set_CheckAutoRptStaFlag`: 全符号表给
      `0x1B7FF`, 函数表给 `0x1B7FE`, 而 `TaskReport.c:2229` 那条行锚点也落在 `0x1B7FE`)。
      去 Thumb 位只在 `_read_syms` 一处做, 表也因此分了这两份。
    """
    f = m["funcs"].get(func)
    if not f:
        raise GdbError("函数表里没有 %s —— 名字拼错? 还是被内联掉了?" % func)
    return f["addr"]


def line_start(m, srcfile, line):
    """源码行 → 该行**起始地址**(DWARF 行表给, 见 `_read_lines`)。

    ⚠ 行表里没有这一行就抛 —— 那种行(注释行、被优化掉的守卫)gdb 会**顺手滑到下一行**再下断点,
      于是断点落在一条你没打算测的语句上, 而日志里只写着文件名与行号, 看不出来。宁可当场停。
    """
    f, n = os.path.basename(str(srcfile)), int(line)
    a = m["lines"].get((f, n))
    if a is None:
        raise GdbError("行表里没有 %s:%d —— 这一行没编译出指令(注释? 被优化掉的守卫?), "
                       "所以它没有起始地址。\n"
                       '  换一行有指令的; 或者改用 ("call"/"prev", 函数, 被调, 第几处) 那种锚点。'
                       % (f, n))
    return a


def func_entry(g, func):
    """`func_addr` 配活会话的外壳(`breakpoint` 那一层走它)。"""
    return func_addr(get_map(g), func)


def line_addr(g, srcfile, line):
    """`line_start` 配活会话的外壳(`breakpoint` 那一层走它)。"""
    return line_start(get_map(g), srcfile, line)


def insns_of(g, func):
    """函数体整条指令流 → `[(地址, 指令原文), …]`; 函数不在图上给空表。"""
    f = get_map(g)["funcs"].get(func)
    return list(f["insns"]) if f else []


def calls_of(g, func):
    """`func` 里每一个紧跟 `call <被调>` 之后的指令 —— 与旧的 `callees` **同形**, 可直接喂
    `break_at_anchor`。`decision_anchor` 要的前后指令读 `insns_of`。"""
    f = get_map(g)["funcs"].get(func)
    if not f:
        raise GdbError("地图上没有函数 %s —— 名字拼错? 还是被内联掉了?" % func)
    idx = dict((a, i) for i, (a, _t) in enumerate(f["insns"]))
    out = []
    for c in f["calls"]:
        i = idx.get(c["next"])
        out.append({"loc": "%s+%d" % (func, c["next"] - f["addr"]), "addr": c["next"],
                    "insn": f["insns"][i][1] if i is not None else "",
                    "why": "紧跟 call %s 之后(被调函数会把状态重读/重算, 断点在它之前必被覆盖)"
                           % c["callee"],
                    "func": func, "callee": c["callee"], "n": c["n"]})
    return out


def resolve_spec(m, spec):
    """把**脚本里写的字面量锚点**解成地址 —— 由地图查出, 不是抄来的。

    收一张**地图**(`build()` 从目标上建的那张, 或 `offline_map()` 从 `.out` 上建的同形那张)
    与一条字面量。**这是锚点语义的唯一一处** —— 四种写法各代表哪条指令只在这里写: 两处各写一份
    的话, 静态检查器核的就不是运行时真停的那条指令, 而它核不出来的样子与"核过了"一模一样。

    四种写法(都是**纯字面量**, 故 `_check_anchors.py` 能 `ast.literal_eval` 抠出来核):

        ("call", <函数>, <被调>, <n>)   该函数里第 n 处 `call 被调` **之后**的下一条指令
        ("prev", <函数>, <被调>, <n>)   同一处调用**之前**的那一条指令
        ("func", <函数>)                函数入口
        ("line", <文件>, <行号>)        该行的起始地址(**归一后的长相**;
                                        `(文件, 行号)` 那个两元写法由 `normalize` 转成它)

    `call` 与 `prev` 是同一处调用的两侧, 用途相反 —— 选哪一侧由**断点停下来要干什么**定:
      · `call`  —— 喂狗 / 注入点。被调函数会把状态重读/重算, 断在调用**之前**必被覆盖。
      · `prev`  —— 要读**这次调用的实参**。实参的最后一次使用就是那条 `bl`, 它的 DWARF 位置
        恰在 `bl` 那一刻结束 ⇒ `bl` 自己与 `bl` 之后两条都在空洞里, 读回来是空的; 只有 `bl`
        前一条指令上它还在。这一条是量出来的, 不是推的: 4-6 的 `Check_BillFrezM` 位置表到
        `0x32DD8` 为止(空洞 `0x32DDC-0x32DEA`, 那条 `bl` 在 `0x32DDC`), `Chg_BillDayM` 到
        `0x32E56` 为止(空洞 `0x32E5A-0x32E6E`, 那条 `bl` 在 `0x32E5A`)。

    ⚠ 解不出来**抛**, 不返回 None —— 回 None 会被调用方当成"没断点可用"静默降级, 而真因
      多半是函数名/被调名拼错、处数写大、或者那个调用被内联掉了。
    """
    spec = tuple(spec)
    if spec[0] == "func":
        func = spec[1]
        return {"loc": "%s+0" % func, "addr": func_addr(m, func), "insn": "",
                "why": "函数 %s 的入口" % func, "func": func, "spec": spec}
    if spec[0] == "line":
        f, n = os.path.basename(str(spec[1])), int(spec[2])
        addr = line_start(m, f, n)
        func, insn = m["insn_at"].get(addr, (None, ""))
        return {"loc": "%s:%d" % (f, n), "addr": addr, "insn": insn,
                "why": "%s 第 %d 行的起始地址(DWARF 行表)%s"
                       % (f, n, ", 落在 %s 里" % func if func else ""), "spec": spec}
    field, side = ("next", "之后") if spec[0] == "call" else ("prev", "之前")
    func, callee, want = spec[1], spec[2], spec[3]
    f = m["funcs"].get(func)
    if not f:
        raise GdbError("地图上没有函数 %s —— 名字拼错? 还是被内联掉了?" % func)
    hits = [c for c in f["calls"] if c["callee"] == callee]
    for c in hits:
        if c["n"] != want:
            continue
        addr = c[field]
        if addr is None:
            raise GdbError("%s 里第 %d 处 `call %s` 是该函数体的第一条指令, 它前面没有指令 "
                           "—— `(\"prev\", …)` 在这里没有落点" % (func, want, callee))
        i = dict((a, k) for k, (a, _t) in enumerate(f["insns"])).get(addr)
        return {"loc": "%s+%d" % (func, addr - f["addr"]), "addr": addr,
                "insn": f["insns"][i][1] if i is not None else "",
                "why": "%s 里第 %d 处 `call %s` %s的指令" % (func, want, callee, side),
                "func": func, "callee": callee, "spec": spec}
    raise GdbError("在 %s 里找不到第 %d 处 `call %s` %s的指令(只找到 %d 处)\n"
                   "  函数名/被调名拼错? 被内联掉了? 还是处数写大了?\n"
                   "  该函数里能找到的调用点是: %s"
                   % (func, want, callee, side, len(hits),
                      ", ".join(sorted(set(c["callee"] for c in f["calls"]))) or "(一个都没有)"))


def map_of(g, spec):
    """`resolve_spec` 配活会话的外壳 —— 递过去的是 `build()` 从**目标上**建的那张地图。"""
    return resolve_spec(get_map(g), spec)


# ============================================================================
# ⑤ 离线地图 —— 不连目标, 只读 `.out`(给静态检查器用)
# ============================================================================
OFFLINE_FORMS = ("func", "call", "prev")


def offline_map(out, funcs=()):
    """只读 `.out` 建一张与 `build()` **同形**的地图 —— 不给 `disassemble` 下目标、不连探针。

    为什么要有它: 静态检查器(`scripts/_check_anchors.py`)要回答"这个锚点落在哪一源码行"。
    从前它拿 `.c` 文本去**猜**(`call_line` 只认"调用自己那一行"), 而运行时真停的那条指令经
    DWARF 行表算出来的行常与之差一条 —— 实测全固件 5599 处可判定的调用里,**猜法错 1142 处**。
    锚点的落点是**二进制里的事实**, 只能从 `.out` 上算, 不能从源码文本上推。

    与 `build()` 的两处差别, 都写在这儿:
      · 指令流来自 `gdb -batch` 对这个 `.out` 的反汇编(无 target), 不是从跑着的核上读的 ——
        反汇编器、符号表、归属规则仍是同一个 gdb、同一份 `.out`, 所以 `bl` 的判定与
        `prev`/`next` 的落点与运行时一致。
      · 函数体用**显式地址区间** `[入口, 入口+长度]` 给, 不按名字 `disassemble <名字>` ——
        重名函数(`static` 同名落在两个 `.c` 里, 如 `Check_Switch`)按名字反汇编会挑**另一个**
        (实测挑到 `0x2A994`), 而运行时取的是**地址最小**那个(`0x2232C`)。区间由 `_read_syms`
        的表给, 于是两边同一条规则。

    `funcs` 只给要用的那几个(检查器手里就这点) —— 一次 gdb 起落全解完。都要时给空元组。
    返回的 map 带 `"src_at"`: `[(地址, "文件.c:行"), …]` 升序, 供"地址 → 源码行"反查。
    """
    funcs_tab, syms, collisions, _top, sizes = _read_syms(out)
    want = [f for f in (funcs if funcs else funcs_tab) if f in funcs_tab]
    ins = _disassemble(out, [(f, funcs_tab[f], sizes.get(f) or 0) for f in want])
    out_funcs = {}
    for f in want:
        rows = ins.get(f) or []
        if not rows:
            continue
        base = funcs_tab[f]
        out_funcs[f] = {"addr": base, "end": rows[-1][0] + 4,
                        "insns": [(a, t) for a, t, _n in rows],
                        "calls": _calls_of(rows)}
    lines = _read_lines(out)
    return {"funcs": out_funcs, "syms": syms, "collisions": collisions,
            "insn_at": {}, "lines": lines, "src_at": _lines_by_addr(lines),
            "n_insns": sum(len(r) for r in ins.values())}


def _lines_by_addr(lines):
    """`{(文件, 行): 起始地址}` → `[(地址, "文件:行"), …]` 升序 —— 反查用(取 ≤ 地址的最大那条)。"""
    return sorted((a, "%s:%d" % (f, n)) for (f, n), a in lines.items())


def src_at(m, addr):
    """地址 → `"文件.c:行"`(取起始地址不超过它的最后一行); 表里没有任何一行在它前面 → `None`。"""
    rows = m.get("src_at") or []
    lo, hi, hit = 0, len(rows) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if rows[mid][0] <= addr:
            hit, lo = rows[mid][1], mid + 1
        else:
            hi = mid - 1
    return hit


def _disassemble(out, want):
    """`[(函数名, 入口, 长度)]` → `{函数名: [(地址, 指令原文, 下一条地址), …]}`。

    一次 gdb 起落解完:`-ex` 逐段 `disassemble <入口>,<入口+长度>`, 每段前 `echo` 一个标记好认领
    (输出里只有地址, 认不出这一段属于谁 —— 而重名函数正是靠显式区间才挑对的)。
    """
    if not want:
        return {}
    ex = ["file \"%s\"" % out]
    for name, base, size in want:
        ex += ["echo \\n### %s\\n" % name, "disassemble 0x%X,0x%X" % (base, base + max(size, 2))]
    r = subprocess.run([_gdb_path(), "-batch"] + [x for e in ex for x in ("-ex", e)],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    text = r.stdout.decode("utf-8", "replace")
    if r.returncode != 0 and "###" not in text:
        raise GdbError("反汇编解不出锚点(gdb 退出码 %d): %s"
                       % (r.returncode, r.stderr.decode("utf-8", "replace").strip()[:200]))
    out_tab, cur, acc = {}, None, []
    for s in text.splitlines():
        if s.startswith("### "):
            if cur:
                out_tab[cur] = _insn_rows(acc)
            cur, acc = s[4:].strip(), []
            continue
        if cur is not None:
            acc.append(s)
    if cur:
        out_tab[cur] = _insn_rows(acc)
    return out_tab


def _insn_rows(lines):
    """gdb 的反汇编文本 → `[(地址, 指令原文, 下一条地址), …]`(与 `build()` 里那几条同形)。"""
    rows = []
    for s in lines:
        m = re.match(r"^\s*(0x[0-9a-fA-F]+)\s+<[^>]*>:\s*(.*)$", s)
        if not m:
            continue
        rows.append((int(m.group(1), 16), m.group(2).replace("\t", " ").strip()))
    rows.sort(key=lambda t: t[0])
    return [(a, t, (rows[i + 1][0] if i + 1 < len(rows) else None))
            for i, (a, t) in enumerate(rows)]


def _gdb_path():
    """gdb 的路径 = 机器卡带 `GDB`(与调试链同一处声明, 本模块不抄路径)。"""
    from common import machspec
    p = machspec.get("GDB")
    if not p:
        raise GdbError("机器卡带没声明 GDB, 离线解锚点要它 —— 换机器改 `machine/<机器名>.py`。")
    return p
