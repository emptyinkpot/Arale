# -*- coding: utf-8 -*-
"""
_check_bench.py —— 台面体检: 一条命令问清 "串口和 SWD 两条链路现在还通不通"

位置: 帧收发基础/scripts/ (顶层运行脚本; 库在 src/common/bench.py)
运行: 在 帧收发基础/ 下 —— python scripts/_check_bench.py [--full] [--serial-only|--swd-only]

    python scripts/_check_bench.py               快检(默认): 离线断言 + 串口 + 探针
    python scripts/_check_bench.py --full        再加 SWD 三关(开 gdb 会话, 验断点/观察点)
    python scripts/_check_bench.py --serial-only 只跑离线断言 + 串口
    python scripts/_check_bench.py --swd-only    只跑离线断言 + 探针(加 --full 才带三关)

本脚本**不是** `project/tests/` 里的测试子项: 它不产生测试结论、不写 `log/`。它只回答台子通不通,
随时可以单独喊一声。以后 `project/tests/_suite.py` 要在每项之前插一次, 是 import 库里的
`common.bench.check_bench` 再调一下, 不必经过本脚本。

退出码: 0=要跑的步全过 / 2=有一步不过, 或者开关配得不对
"""
import sys
import time


from common.bench import check_bench                    # noqa: E402
from common.cli import guard_argv                       # noqa: E402
from common.console import ensure_utf8_stdout           # noqa: E402


def _scope(serial_only, probe_only):
    """这一次验的是哪几条链路 —— 打印用的话。"""
    if serial_only:
        return "串口那一半"
    if probe_only:
        return "SWD 那一半"
    return "两条链路"


def main(argv=None):
    ensure_utf8_stdout()
    argv = list(sys.argv[1:] if argv is None else argv)
    # 开关照实登记(漏登记=正常用法被误拦; 多登记=那道判定对它失效 —— 宁可吵不可静默)。
    guard_argv(argv, allow=("--full", "--serial-only", "--swd-only"))

    full = "--full" in argv
    serial_only = "--serial-only" in argv
    probe_only = "--swd-only" in argv
    if serial_only and probe_only:
        print("!! --serial-only 与 --swd-only 只能给一个。")
        return 2
    if serial_only and full:
        print("!! --full 是给探针那半边的(FPB 断点 + DWT 观察点), 配 --serial-only 没东西可跑。")
        return 2

    print("== 台面体检 ==")
    if serial_only or probe_only:
        print("   范围: 只跑 %s (离线断言照常先跑)。" % _scope(serial_only, probe_only))
    if full:
        print("   带 --full: 最后再加 SWD 三关(开一场 gdb 会话, 会停核/下断点/挂观察点)。")

    t = time.time()
    rep = check_bench(want_serial=not probe_only, want_probe=not serial_only, full=full)
    used = time.time() - t

    print("\n== 台面体检汇总 ==")
    for r in rep.rows:
        print("   %-10s %-6s %6.2fs" % (r.name, "通过" if r.ok else "**不过**", r.seconds))
        for line in r.why.splitlines():
            print("       %s" % line)
    if rep.skipped:
        print("   停在这儿: 后面 %d 步没跑 —— 它们必然也过不了, 不等了。" % rep.skipped)
    print("   台面体检共用 %.2fs。" % used)
    if rep.ok:
        print("   %s都通。" % _scope(serial_only, probe_only))
        return 0
    print("   上面**不过**那一行的说明就是原因, 按它查; 修完重跑本脚本。")
    return 2


if __name__ == "__main__":
    sys.exit(main())
