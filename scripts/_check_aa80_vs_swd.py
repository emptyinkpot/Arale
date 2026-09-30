# -*- coding: utf-8 -*-
"""
_check_aa80_vs_swd.py —— 「两条观察通路等价」对照: 同一刻 AA80(串口 COM3) vs SWD(J-Link) 逐字节比

为什么归在 scripts/ 而不是 project/tests/ 或 swdbg/
--------------------------------------------------
不进 `tests/`: 它不认领任何子项 —— 它验的是**通路**(两条路读同一批变量该不该逐字节一致),
不是某个子项的判据, 而 `tests/` 的位子是"一个子项一个脚本"。
不进 `swdbg/`: 这个比对要**同时握着两条通路和那块表** —— 要 COM3、要画像的 P.WATCH_VARS/P.STABLE_VARS。
它是**表的属性**, 不是**通路的属性**; 留在 swdbg 里, 本包就永远离不开 project
(想拿它指一块陌生表必全红), 而那正是 swdbg 存在的意义。
swdbg 侧原先还留过一档"换任何表都成立"的通路不变量自检(`python -m swdbg.selftest`)—— 那档随
离线/模拟那批入口一起删了(见 `src/swdbg/__init__.py`: 本包没有离线/模拟通路, 也没有自检入口)。
于是"两条通路等价"现在**只剩这个脚本在证**。

它证什么
--------
"`swdbg` 与串口白盒返回值可无缝互替"这句话不能靠看代码相信, 只能靠两条通路**在同一时刻读同一批
变量、逐字节比对**来证。这是本脚本存在的唯一理由。

判据(表在跑, 这点很关键)
------------------------
每个变量按 SWD → AA80 → SWD 顺序读三次:
    SWD 两次相同      → 该变量此刻静止, 则 **AA80 必须与它逐字节相等**, 不等即 FAIL(真不等价)
    SWD 两次不同      → 该变量此刻在动(g_HisTime 这种秒表), 三读无法定案 → 记"漂移", 对 watch 变量属正常;
                        但 stable 变量(结算绝不能碰的稳定态)出现漂移本身就是异常 → FAIL。
判据与三态判定都在库内 `CB.aa80_vs_swd_compare()`; 本文件只留参数与段。

⚠ 本脚本**不认领任何子项**(它是通路等价性的一条自证, 不是某个子项的判据), 故住 `scripts/`、不进册子。
⚠ 全程**只读不发帧**(连 `645.factory` 也不发): AA80 直读不停核、不写表。
⚠ 要**真接 J-Link**(`swdbg.probe.Probe` 直连探针), 假串口顶不了 —— 而**假串口这条路本仓已删**
   (2026-09-18), 所以本脚本没有"跳过的离线跑法"可言: 不接探针就只有跑到一半失败。

跑法(在 帧收发基础/ 下):
    python scripts/_check_aa80_vs_swd.py                   # 比全部画像变量(WATCH_VARS + STABLE_VARS)
    python scripts/_check_aa80_vs_swd.py --vars g_CurTime,g_HisTime
退出码: 通过→0 / 失败(有 ok=False 证据)→1 / 未定论(条目未证)→2。
"""
import sys


from common import loglabel        # 字形(调试行 `[调试] [SWD] …`)的唯一定义处
from meterlib import cmd_bank       # 帧目录 + 语义动词积木(本脚本只用 aa80_vs_swd_compare)
from common import trial          # 运行外壳: argv/日志/账本/串口/退出码
from swdbg import resolve as varresolve      # 变量名→地址: 全仓唯一那份解析器
from project import CURRENT          # 本工程画像: 变量集与地址的单一事实源
from swdbg.probe import Probe             # J-Link 会话(只读原语 read_abs)


def _names():
    """`--vars a,b` → 变量名清单; 没给就比画像里的全部(WATCH_VARS + STABLE_VARS)。"""
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--vars" and i + 1 < len(argv):
            return [s.strip() for s in argv[i + 1].split(",") if s.strip()]
    return None


def part_aa80_vs_swd(ctx):
    """一段 = 本脚本的全部条目: 逐变量三次读 + 逐条判定(判定在库内)。"""
    # ⚠ 地址解析已在 import `ez_meter` 时装配(它是全仓唯一的 `swdbg.resolve` 装配点):
    #   于是探针侧与串口侧取的是**同一个**解析器, 不可能一边按 A 地址读、一边按 B 地址读。
    names = _names() or (list(CURRENT.WATCH_VARS) + list(CURRENT.STABLE_VARS))
    with Probe() as pb:
        # ⚠ 字形不在这里拼 —— 与 `breakpoint.open_or_none` 的横幅同一件事(都是"这场会话什么样"),
        #   走同一个落笔处(2026-09-18 并)。
        print("   %s" % loglabel.debug_line(loglabel.DEBUG_SWD, pb.describe()))
        ctx.hold(*cmd_bank.aa80_vs_swd_compare(
            ctx.ser, names, pb, stable=CURRENT.STABLE_VARS, resolve=varresolve.resolve),
            "AA80↔SWD 对照段")


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "AA80 ↔ SWD 两条观察通路逐字节对照",
        cmd_bank.aa80_vs_swd_criteria,
        name="aa80_vs_swd",
        parts=[("AA80↔SWD 对照段", part_aa80_vs_swd)],
        allow=("--vars",), known=("--vars",),
        banner="== AA80(串口) vs SWD(J-Link) 逐字节对照 | 工程=%s 表号=%s =="
               % (CURRENT.PROJECT, CURRENT.TABLE_ADDR.hex().upper())))
