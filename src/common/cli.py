# -*- coding: utf-8 -*-
"""cli.py —— 顶层脚本的命令行入口守卫(纯 argv 处理, 与协议/画像/硬件全无关)

为什么住在 common/ 而不是 meterlib:
    它是**机制**(怎么判断参数认不认得), 毫无 645/698/AA80/画像成分 —— 与 console.py 同一性质。
    原先长在 `meterlib/cmd_bank.py` 里, 后果是"不认表"的入口脚本想用这道判定, 就得 import cmd_bank,
    而 cmd_bank 顶部有 `from project import CURRENT as P` —— 为 20 行参数守卫把当前表的画像
    拖进一个**本该能指任何一块表**的探测脚本, 不值当(2026-09-10 实测: scripts/_init_meter.py 正是此例)。
    搬到 common 后, meterlib 与 discover/scripts 都从这一份取, 谁都不必认表。

`meterlib.cmd_bank` 仍以 `CB.guard_argv` 的名字重导出本函数(9 个 _test_*.py 的调用点与
CLAUDE.md 的写法都保持不变)。

为什么非有不可
--------------
脚本没实现 `--dry` 时, `python project/tests/_test_5_5_clear_event.py --dry` 的参数会被 Python
**静默忽略**, 脚本**照常连真表发帧** —— 人以为在预演, 表已经被清了(破坏性的 5-4 电表清零 /
5-5 事件清零照清, 表钟也可能被拨走)。**要预演用库的 dry**: `cmd_bank dry <帧id>` /
`runcase <用例> --dry`, 不认脚本开关。

两个参数
--------
`allow=` **无值开关**(`--dry`); `known=` **带值开关**(`--to`), 其后一个 token 当值放行 ——
但**只在那个 token 不以 `-` 开头时才吃**(否则 `--to --bogus` 会把拼错的开关当值吞掉, 从判定
底下溜过去)。

已覆盖 8 个 `_test_*.py` + `_restore_all.py`(会真拨表钟) + `_init_meter.py`。
**新写顶层入口脚本一律补一行** ——
凡用 `_has(flag)` / 手写取参的脚本, 拼错一个字母就等于静默换行为。
"""


def guard_argv(argv, allow=(), known=(), positional=False):
    """脚本入口守卫: 出现未识别参数 → 打印解释并 SystemExit(2)。

    ⚠ 为什么需要这道判定(2026-09-10 实踩, 不是洁癖):
      脚本本体若没实现 --dry, `python project/tests/_test_5_5_clear_event.py --dry` 里的参数会被 Python
      **静默忽略**, 然后脚本【照常连真表发帧】—— 人以为在预演, 表已经被清了。当天因此误跑 7 个脚本,
      含破坏性的 5-4 电表清零 / 5-5 事件清零, 并把表钟拨到 2026-10-05(后经 _restore_all.py 拨回)。
      宁可入口拦死, 也不要"以为是预演、实际动了表"。要预演请走库自己的 dry:
        python -m meterlib.cmd_bank dry <帧id>       # 离线打印将发的字节
        python -m meterlib.cmd_bank runcase <id> --dry

    allow: 本脚本【真的实现了】的【无值开关】(如 ("--dry",)); 其余一律拦。
    known: 本脚本【真的实现了】的【带值开关】(如 ("--to",)) —— 其后一个 token 当值放行, 不拦。
           (_restore_all.py / _init_meter.py 这类 `--flag 值` 风格脚本用; 只写 allow 会把值当未识别参数误拦。)
    positional: 本脚本收不收**位置操作数**(如 `_probe_all.py <目录>`)。默认 False = 一律不收,
           于是任何裸词都被拦 —— 这是 `--dry`/`--to` 那批脚本要的严格。
           收位置参数的脚本必须显式 `positional=True`, 否则 `python x.py 某目录` 会被当未识别参数拦死
           (2026-09-10 写 `_probe_all.py` 时踩到: 在此之前所有脚本都是纯开关式, 这个缺口没暴露过)。
           ⚠ 判据是"**不以 `-` 开头**"才算操作数; `-x`/`--x` 无论何时都要在 allow/known 里登记。

    两集合都必须**照实登记**: 漏登记一个真开关 → 正常用法被误拦(吵但安全); 多登记一个不存在的
    → 那道判定对它就失效(静默)。宁可吵, 不可静默 —— 这是本函数的取向。
    """
    argv = list(argv)
    bad, i = [], 0
    while i < len(argv):
        a = argv[i]
        if a in allow:
            i += 1
            continue
        if a in known:
            # 带值开关: 吃掉后面那个 token 当值 —— 但**只在它不像开关时才吃**。
            # 否则 `--to --bogus` 会把 --bogus 当值吞掉, 拼错的开关就此从这道判定底下溜过去
            # (本函数的取向是宁可吵不可静默; 日期/时间/路径都不会以 '-' 开头, 不会误伤真值)。
            nxt = argv[i + 1] if i + 1 < len(argv) else None
            i += 2 if (nxt is not None and not nxt.startswith("-")) else 1
            continue
        if positional and not a.startswith("-"):
            i += 1
            continue                       # 位置操作数(目录/.out 路径), 不是开关
        bad.append(a)
        i += 1
    if not bad:
        return
    print("!! 未识别参数: %s" % " ".join(bad))
    print("   本脚本未实现这些开关 —— 它们会被 Python 静默忽略, 脚本【照常连真表发帧】!")
    print("   确实要用 → 把开关补进本脚本入口的 guard_argv(allow=/known=); 要离线预演请用库的 dry:")
    print("     python -m meterlib.cmd_bank dry <帧id> / runcase <用例id> --dry")
    raise SystemExit(2)
