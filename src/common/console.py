# -*- coding: utf-8 -*-
"""console.py —— 控制台编码单点(与协议无关; 谁要往 stdout 打中文都引这里)

本模块这段逻辑在 meterlib 里被复制过 5 份(cmd_bank/aa80/elf/watch_runner/whitebox),
其中 cmd_bank 那份后来单独修强了(幂等 + 非可重配流回退), 另外 4 份还是简化版 —— 典型的"复制粘贴各自演化"。
收敛到一处, 以后只改这里。

⚠ 与 swdbg 的关系(2026-09-18 更新): 真正还成立的边界是【swdbg 不 import meterlib】(见 CLAUDE.md
  「swdbg」节)—— swdbg 引 `common` 是允许的(machspec/winpnp/events/judge/varresolve/snapdiff 都在引),
  本文件属 `common`, 所以 swdbg 引它不越界。原先这里写着「swdbg 的 probe.py/selftest.py 各留一份
  自己的实现」—— 那两条现在都不成立了: `swdbg/selftest.py` 已随离线/模拟那批一起删掉, 剩下的
  probe.py 里也没有第二份编码实现。别把 meterlib 拖进 swdbg 的依赖里, 这条不变。

实现要点(比简化版强在哪):
  ① 幂等: 已经是 utf-* 就直接返回, 不反复包壳;
  ② 回退: stdout 没有 reconfigure(如被重定向/被别的工具包过)时, 用 TextIOWrapper 包 buffer;
  ③ 静默: 任何异常都吞掉 —— 编码设置失败不该让测试脚本挂掉。
"""
import io
import sys


def ensure_utf8_stdout():
    """把 sys.stdout 尽量切成 UTF-8(errors=replace)。幂等、带回退、不抛异常。

    仅对命令行入口有意义: 库被 import 时不要调它(会动宿主进程的 stdout)。
    """
    so = sys.stdout
    try:
        enc = getattr(so, "encoding", "") or ""
        if enc.lower().startswith("utf"):
            return
        if hasattr(so, "reconfigure"):
            so.reconfigure(encoding="utf-8", errors="replace")
            return
        if getattr(so, "buffer", None) is not None:
            sys.stdout = io.TextIOWrapper(so.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass
