"""链路自检: 一次连上, 分三关证明 探针 / 断点(FPB) / 观察点(DWT) 各自还能用。

用法(装了 `pip install -e .` 之后, 从任何目录):
    python -m swdbg.selfcheck

与 `python -m swdbg.probesel --doctor` 的分工: 那个只证到"探针后面的 CPUID 读得到", 断点与
观察点一个都不碰。而**断点槽(FPB)与观察点槽(DWT)是两套硬件**, 坏法不一样(跨会话残留那两节
各是一种), 必须分开验 —— 本模块补的正是这两样。

三关的顺序是死的: 前关不通, 后关必然不通, 那就不要白等。所以第一关(探针 + SWD)一失败就
就地收摊, 把时间花在报错上, 不花在等超时上。

⚠ 本模块**不碰串口、不改表内数据**: 断点只打在一条高频只读行上, 观察点只挂在一条被反复读写的
全局量上; 打完即撤, 撤完把核放回运行态。它不产出任何测试结论, 只回答"链路通不通"。

⚠ 本模块**不往 `log/` 写一个字**。`log/` 只收实表跑出来的测试记录; 自检不是测试。所以这里
      刻意不走 `breakpoint` 那套 `record()`/`wait_hit()` 记录层, 只用 `Session` 的低层原语并打到
      stdout —— 与 `probesel --doctor`、`restore --check` 同一类输出。
"""

import argparse
import sys
import time
import traceback

from swdbg import breakpoint
from swdbg.breakpoint import GdbError, addr_tok

# ---------------------------------------------------------------------------
# 三个钉子。换固件/换画像时只需要动这里。
# ---------------------------------------------------------------------------

# 断点打在**函数入口**上, 不打源码行 —— 这一关要验的是"断点单元(FPB)还能不能比中",
# 不是"表此刻跑到哪一行"。`Run_TaskRate` 在 `main.c:44` 的主循环里每轮都调, 入口恒过。
# ⚠ 2026-09-20 实测: 原先钉的 `TaskRate.c:85`(在 Run_TaskRate 体内)在表空闲时**一次都不中**,
#   而同一函数的入口每次都中 ⇒ 钉行号会把"表换了个状态"报成"FPB 不通"。
BP_FUNC = "Run_TaskRate"

# 观察点挂的全局量, 按顺序取第一个**符号能解析出来**的。
# `g_PowP[0]` 是实测过的: `RevCopy_Data`(Common.c:310)每周期从 SPI 缓冲拷回来, 读写都发生。
WATCH_CANDS = ("g_PowP[0]", "g_PowP")

WAIT_BP = 3.0     # 等断点命中的上限(高频行, 正常是毫秒级)
WAIT_WP = 3.0     # 等观察点命中的上限


class Report(object):
    """三关的结果与耗时。`halt_on_fail` = 这一关不过后面就不做了。"""

    def __init__(self):
        self.steps = []
        self._t0 = time.time()

    def step(self, no, name, ok, detail="", halt_on_fail=False):
        dt = time.time() - self._t0
        self.steps.append((no, name, ok, detail, dt))
        print("[%d/3] %-22s %s  %6.2fs" % (no, name, "通过" if ok else "**不过**", dt))
        for line in str(detail).splitlines():
            print("      %s" % line)
        sys.stdout.flush()
        return ok

    @property
    def ok(self):
        return bool(self.steps) and all(s[2] for s in self.steps)


def _short(exc, n=6):
    """异常 → 几行给人读的说明。traceback 只留最后几帧, 免得把真正那行顶出屏幕。"""
    lines = [l for l in traceback.format_exception_only(type(exc), exc) if l.strip()]
    tb = traceback.format_exc().rstrip().splitlines()
    return "".join(lines).rstrip() + "\n" + "\n".join(tb[-n:])


# ===========================================================================
# 第 2 关: FPB 硬件断点
# ===========================================================================
def _check_break(g):
    """下一处断点, 放行, 等它命中, 撤掉。返回 (ok, 说明)。

    撤断点只有**停住那一刻**能做(`clear_breaks` 的 ⚠), 而命中之后核正好停着 —— 所以整段
    就是"停住态进出停住态", 收尾用 `ensure_running()` 放回运行态, 否则后面串口全哑。"""
    bp = g.break_at_func(BP_FUNC)
    try:
        g.go()
        hit = g.wait_hit(WAIT_BP)
    finally:
        # 不论命中没命中, 都要把断点撤干净再放行 —— 留着槽跑起来就是"表死机"那种现象。
        try:
            g.ensure_stopped()
            g.clear_breaks()
            g.ensure_running()
        except Exception as exc:
            return False, "撤断点失败, 链路可能留在停住态: %s" % _short(exc, 3)
    if hit is None:
        return False, ("断点下在 %s 入口, 下得去但 %.1fs 内没命中。主循环每轮都调它, 正常是毫秒级 —— "
                       "命中不了说明断点单元(FPB)不通。" % (BP_FUNC, WAIT_BP))
    return True, "打在 %s 入口, %.1fs 内命中。" % (BP_FUNC, WAIT_BP)


# ===========================================================================
# 第 3 关: DWT 数据观察点
# ===========================================================================
def _watch_addr(g):
    """挑一个能解析出地址的全局量 → (符号名, 地址); 都不行 → (None, None)。"""
    for name in WATCH_CANDS:
        v = g.read_vars(["&(%s)" % name]).get("&(%s)" % name)
        a = addr_tok(v)
        if a:
            return name, a
    return None, None


def _live_slot(g, window=0x100, wait=1.0):
    """找一个**当场证明在这 `wait` 秒里被写过**的栈上 4 字节槽 → 地址; 找不到 → None。

    要它是因为: 观察点没把核撂停时, "DWT 停不了核"与"挑的那个量根本没被写"是两回事, 结论差得远。
    栈是主循环每轮都在压/弹的地方, 所以拿它当阳性对照 —— 一段确定在变的地址都停不住, 才能把
    "观察点不通"说死。"""
    if not g.stopped:
        g.ensure_stopped()
    msp = g.read_regs(["msp"]).get("msp")
    if not msp:
        return None
    base = msp - window
    hold = g._read_mem(base, window)
    if not hold:
        return None
    g.ensure_running()
    time.sleep(wait)
    try:
        g.ensure_stopped()
        now = g._read_mem(base, window)
    finally:
        g.ensure_running()
    if not now:
        return None
    for i in range(0, window, 4):
        if hold[i:i + 4] != now[i:i + 4]:
            return base + i
    return None


def _check_watch(g):
    """挂一个 DWT 观察点, 放行, 等它把核撂停, 清掉。返回 (ok, 说明)。

    通过 GDB MI 的 `-break-watch -a` 让 J-Link GDB Server 自己配置 DWT；
    不直接写 COMP/FUNCTION，避免 continue 时被 server 的断点状态同步覆盖。
    """
    if not g.stopped:
        g.ensure_stopped()                      # 读写 DWT 都在停住态做
    name, addr = _watch_addr(g)
    if addr is None:
        return None, ("%s 这几个符号一个都解析不出地址 ⇒ 观察点未验(不是不过, 是没验成)。"
                      "把 WATCH_CANDS 换成这一版固件里确实高频读写的全局量。"
                      % " / ".join(WATCH_CANDS))

    # DWT_CTRL.NUMCOMP 是目标实际实现的比较器数量。观察点由 GDB Server 管理；
    # 直接写 COMP/FUNCTION 会在 continue 时被 server 的断点状态同步覆盖。
    ctrl = g._read_u32(0xE0001000)
    if ctrl is not None and ((ctrl >> 28) & 0xF) == 0:
        return None, "DWT_CTRL.NUMCOMP=0，目标没有可用的 DWT 比较器。"
    wpt = None
    try:
        # `-a` 是 access-watchpoint：读/写都触发，保持原来 FUNCTION=6 的语义，
        # 不会因为目标把同一个值重复写回而被 GDB 的 value-change 过滤掉。
        rec = g._cmd("-break-watch -a *0x%08X" % addr)
        if not rec or rec.get("_class") != "^done":
            return False, "GDB Server 下 DWT 观察点失败: %s" % (rec or "无应答")
        # GDB MI 用 `wpt` 表示普通写观察点，用 `hw-awpt` 表示 access-watchpoint。
        wpt_info = rec.get("wpt") or rec.get("hw-awpt") or {}
        wpt = str(wpt_info.get("number") or "")
        if not wpt:
            return False, "GDB Server 没返回观察点号: %r" % rec
        g.ensure_running()
        hit = g.wait_hit(WAIT_WP)
    finally:
        try:
            g.ensure_stopped()
            if wpt:
                g._cmd("-break-delete %s" % wpt)
            g._clear_stale_dwt()
            g.ensure_running()
        except Exception as exc:
            return False, "清观察点失败, 链路可能留在停住态: %s" % _short(exc, 3)

    if hit is None:
        live = _live_slot(g)
        if live is None:
            return None, ("观察点挂在 %s(0x%08X)上没停住核, 而**阳性对照也没做成**(栈区那一秒内"
                          "一个槽都没变)⇒ 这一关没法判。" % (name, addr))
        # 只证明栈在变还不够；这里真的再挂一个阳性观察点，避免把自检实现缺口报成硬件结论。
        pos_wpt = None
        try:
            g.ensure_stopped()
            rec = g._cmd("-break-watch -a *0x%08X" % live)
            if not rec or rec.get("_class") != "^done":
                return False, "候选量未命中，阳性对照挂载失败: %s" % (rec or "无应答")
            pos_info = rec.get("wpt") or rec.get("hw-awpt") or {}
            pos_wpt = str(pos_info.get("number") or "")
            g.ensure_running()
            pos_hit = g.wait_hit(WAIT_WP)
            return False, ("观察点挂在 %s(0x%08X)上 %.1fs 未命中；阳性对照 0x%08X 在变且观察点%s。"
                           % (name, addr, WAIT_WP, live,
                              "命中" if pos_hit is not None else "也未命中"))
        finally:
            try:
                g.ensure_stopped()
                if pos_wpt:
                    g._cmd("-break-delete %s" % pos_wpt)
                g._clear_stale_dwt()
                g.ensure_running()
            except Exception:
                pass
    return True, "挂在 %s(0x%08X)上, %.1fs 内撂停。" % (name, addr, WAIT_WP)


# ===========================================================================
# 主流程
# ===========================================================================
def main(argv=None):
    # Windows PowerShell may expose a GBK stdout; trace lines contain ASCII
    # arrows and other Unicode markers, so make the CLI reliable without
    # requiring callers to set PYTHONIOENCODING themselves.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    ap = argparse.ArgumentParser(
        prog="python -m swdbg.selfcheck",
        description="三关验链路: 探针 + SWD / FPB 断点 / DWT 观察点。")
    ap.add_argument("--out", default=None, help=".out 路径(默认取画像 CURRENT.OUT_PATH)")
    args = ap.parse_args(argv)

    from project import CURRENT
    out = args.out or CURRENT.OUT_PATH

    print("== 链路自检 ==")
    print("   .out: %s" % out)
    print("   断点钉在: %s 入口" % BP_FUNC)
    r = Report()

    # ---- 第 1 关: 探针 + SWD ----
    # 开一场会话这条路本身就包含了探针识别(构造 `Session` 会先 `probesel.pick()` 真连一次读
    # CPUID 证明后面是本表)、起 gdbserver、接 gdb。任何一处断在这条路上, 都是"SWD 这条线不通"。
    g = None
    t = time.time()
    try:
        g = breakpoint.Session(out=out, watchdog=CURRENT.IWDT_SERV, inject_allow=())
        g.open()
    except Exception as exc:
        r.step(1, "探针 + SWD", False, _short(exc), halt_on_fail=True)
        print("\n   第一关就断了, 后面两关不做了 —— 它们必然也过不了。")
        # 这一关把两件不同的事并在一起了: "探针拾起来但连不上" 与 "端口被占/会话开不起来"。
        # 报错文字里已经写明是哪一件, 但提示不能一律叫去查线 —— 端口被占时查线是白查。
        _msg = "%s" % exc
        if "2331" in _msg or "占用" in _msg:
            print("   2331 被占: 先确认没有别的实表脚本/VS Code 调试会话在跑; 确认没有之后")
            print("   跑 `python -m swdbg.restore` 清残留 server, 再回来重跑本模块。")
        else:
            print("   探针这段线不通。先跑 `python -m swdbg.probesel --doctor`(它报逐条原因),")
            print("   按它的提示查线: SWDIO / SWCLK 有没有压实、GND 有没有接、VTref 有没有接。")
        return 2
    r.step(1, "探针 + SWD", True, "会话开起来了(%.2fs)。" % (time.time() - t))

    try:
        # ---- 第 2 关: FPB 断点 ----
        try:
            ok, why = _check_break(g)
        except Exception as exc:
            ok, why = False, _short(exc)
        r.step(2, "FPB 硬件断点", ok, why)
        if not ok:
            # 断点不通, 观察点也没必要试 —— 两者共用"停下/放行"这套动作, 前一个已经证明走不通。
            print("\n   第 2 关断了, 第 3 关不做了 —— 它走的是同一套停下/放行动作。")
            return 2

        # ---- 第 3 关: DWT 观察点 ----
        try:
            ok3, why3 = _check_watch(g)
        except Exception as exc:
            ok3, why3 = False, _short(exc)
        r.step(3, "DWT 数据观察点", ok3 is not False, why3)
        if ok3 is None:
            print("\n   观察点**没验成**(不是不过): 换一个符号再跑一遍。")
        elif not ok3:
            return 2
    finally:
        try:
            g.close()
        except Exception as exc:
            print("   关会话时报错: %s" % _short(exc, 3))
            return 2

    print("\n链路通了: 探针 / 断点 / 观察点三样都在。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
