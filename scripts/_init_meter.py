# -*- coding: utf-8 -*-
"""
_init_meter.py —— 探测一块**陌生表**, 产出一份《探测报告》。`discover` 包的唯一入口。

位置: 帧收发基础/scripts/ (顶层运行脚本; 库在 src/discover/)
运行: 在 帧收发基础/ 下 —— python scripts/_init_meter.py --out <表.out> [--src 源码目录]
                                            [--doc 总纲.md] [--ewp x.ewp] [--live]

它做什么
--------
喂进去三样东西, 出来**一份 markdown**:

    输入   <表>.out          汇编产物。有 DWARF 最好(能拿到类型/volatile/声明行号)
           固件源码目录      可选。给了就能有「源码足迹」: 谁在哪读写这个符号
           project/knowledge/对表操作总纲.md   可选。给了就能有「协议语义对照」
    产物   project/knowledge/探测报告/<表名>_探测.md

**就这一份产物。** 本脚本不生成画像、不写 `project/*.py`、不碰 `CURRENT` ——
那份报告是给人(和你手边的 AI)读的, 人拿它去写适配这块表的调试固件。

为什么要有它(用户原话)
----------------------
    "任何的表都能用这个来探测所有的内部结构, 我这个程序不是专门为某个表设计的,
      只有 project/ 是专门配合来适应这个表的。"

所以本脚本**一律不认表**: 不读 `project/`、不加载任何画像。喂哪块表的 `.out`, 就探哪块表。

离线与在线
----------
    (默认)  **离线**: 只读 .out / 源码 / 总纲。随时可跑, 不碰硬件。§4 整节留空。
    --live  **在线**: 额外连 J-Link 采一轮活体(只读不停核), 把 §4 填上。
             **采样前先跑 --dry** —— 先看清楚要采哪些地址、采几轮, 再去碰真表。
             (仓规: 对真表动作前先 dry 复核; 采样虽只读, 也照这条走。)

安全
----
* 只读。本脚本**绝不写目标内存、绝不停核**——采样通路复用的是 `swdbg/probe.py`,
  它的三条铁律(绝不停核 / 干净退出 / 不打断运行)在那里已经实测过。
* 连不上 J-Link → §4 留空并在报告里写明原因, **不静默降级**。
* 「读失败」与「静止」在报告里分列 —— 读不到绝不当成稳定态。

外泄
----
内容属厂商固件白盒反推, **外泄敏感**。报告与中间产物里只允许出现
**符号名、地址、类型、行号指针**, 不许源码片段、不许反汇编 ——
这条由 `discover.selftest` 当断言守(它会在源码里逐行反查)。

退出码: 0=报告已生成 / 2=前置失败(输入不对/连不上表)
"""
import os
import sys


from discover import dossier
from discover import elf


# 控制台 UTF-8 单点: 实现收在 common/console.py(2026-09-10 收敛, 原先本文件内联一份)。
# 别名保原调用点 `_utf8_stdout()` 一字不改; 幂等 + 带非可重配流回退, 比原内联版强。
from common.console import ensure_utf8_stdout
from common.cli import guard_argv                  # 入口参数守卫(机制, 不认表)


def _has(flag):
    return flag in sys.argv


def _val(flag, default=""):
    """手写取参(`--flag 值`)。与仓里其它顶层脚本同一风格。"""
    if flag in sys.argv:
        i = sys.argv.index(flag) + 1
        if i < len(sys.argv):
            return sys.argv[i]
    return default


def _usage():
    print(__doc__.strip().split("退出码:")[0].strip())
    print("")
    print("  例(离线, 在 帧收发基础/ 下):")
    print("    python scripts/_init_meter.py --out <表.out> --src <源码目录> --doc <总纲.md>")
    print("  例(在线, 先 dry):")
    print("    python scripts/_init_meter.py --out <表.out> --live --dry")
    print("    python scripts/_init_meter.py --out <表.out> --live")
    return 0


def main():
    ensure_utf8_stdout()
    # 未识别开关=拦死。本脚本的 _has()/_val() 只认【写死的开关名】—— 拼错(如 --lives)不报错,
    # 而是静默退化成离线跑。仓规是"对真表动作前先 dry", 那就不能让拼错悄悄改变行为。
    # ⚠ 守卫从 common.cli 取,**不是** CB.guard_argv: 本脚本"一律不认表", 而 cmd_bank 顶部
    #   有 `from project import CURRENT as P` —— 为这道判定把当前表画像拖进来, 本脚本就废了。
    guard_argv(sys.argv[1:],
                  allow=("--live", "--dry", "--help", "-h"),
                  known=("--out", "--src", "--doc", "--ewp", "--to",
                         "--rounds", "--gap", "--pattern"))
    if not sys.argv[1:] or _has("--help") or _has("-h"):
        return _usage()

    out = _val("--out")
    if not out:
        print("!! 必须给 --out <某块表的 .out>")
        print("   先看看有哪些: 探哪块表就指哪个 .out, 本脚本不认识表名")
        return 2
    if not os.path.isfile(out):
        print("!! .out 不存在: %s" % out)
        return 2

    src = _val("--src")
    if src and not os.path.isdir(src):
        print("!! --src 不是目录: %s" % src)
        return 2
    ewp = _val("--ewp")
    if ewp and not os.path.isfile(ewp):
        print("!! --ewp 不存在: %s" % ewp)
        return 2
    doc = _val("--doc")
    if doc and not os.path.isfile(doc):
        print("!! --doc 不存在: %s" % doc)
        return 2

    live = _has("--live")
    dry = _has("--dry")
    rounds = int(_val("--rounds", "5") or 5)
    gap = float(_val("--gap", "0.5") or 0.5)
    pattern = _val("--pattern") or None
    to = _val("--to")

    print("== 探测: %s ==" % os.path.basename(out))
    print("   .out  %s" % out)
    print("   源码  %s" % (src or "(未给 —— §3 的源码足迹列为空)"))
    print("   总纲  %s" % (doc or "(未给 —— §5 整节留空)"))
    print("   活体  %s" % ("采(%d 轮 x %.2fs, 只读不停核)" % (rounds, gap) if live
                           else "不采(离线; 加 --live 才连表)"))

    if live and dry:
        # dry 只把"要碰哪些地址"摆出来, **不连 J-Link**。仓规: 对真表动作前先 dry。
        print("")
        print("-- --dry: 只列出采样计划, 不连 J-Link --")
        try:
            reps = elf.symbols(out=out, pattern=pattern)
        except Exception as e:
            print("!! 解析 .out 失败: %s" % e)
            return 2
        print("   将按绝对地址逐个读 %d 个符号, 每轮 %d 次连读取稳定值:" % (len(reps), rounds))
        for s in reps[:15]:
            print("     0x%08X  %-6d B  %s" % (s.addr, s.size, s.name))
        if len(reps) > 15:
            print("     … 另有 %d 个" % (len(reps) - 15))
        print("   全程只读。要真跑就去掉 --dry。")
        return 0

    def say(msg):
        print("   … %s" % msg)

    try:
        rep = dossier.build(out, src=src or None, doc=doc or None, ewp=ewp or None,
                       live=live, rounds=rounds, gap=gap, pattern=pattern, progress=say)
    except Exception as e:
        print("!! 探测失败: %s" % e)
        import traceback
        traceback.print_exc()
        return 2

    try:
        path = dossier.write_report(rep, to or None)
    except Exception as e:
        print("!! 报告写不出去: %s" % e)
        return 2

    md = dossier.render(rep)
    print("")
    print("== 报告: %s ==" % path)
    print("   符号 %d / 段 %d / 空闲洞 %d 处 / 文档 %s 字符"
          % (len(rep["syms"]), len(rep["sections"]), len(rep["gaps"]), "{:,}".format(len(md))))
    if rep["live_data"]:
        from discover import evidence
        tally, _ = evidence.summarize(rep["live_data"])
        print("   活体: %s" % "  ".join("%s=%d" % (k, v) for k, v in sorted(tally.items())))
    for m in rep["missing"]:
        print("   缺: %s" % m)
    print("")
    print("   下一步: 打开那份报告读 §7(开放问题) —— 那是建画像的任务清单。")
    print("           要自查外泄与判定逻辑: python -m discover.selftest --out <该.out> --src <源码目录>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
