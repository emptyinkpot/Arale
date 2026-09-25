# -*- coding: utf-8 -*-
"""
common/runlog.py —— 测试运行日志(集中 tee, 一处定义, 脚本/动词零改动)

2026-09-10 自 meterlib/ 迁来: 本模块只 import datetime/os/sys/time, 零协议零画像成分 ——
按 common/__init__.py 自己写的判据(那些"不属于任何一方的共用积木"), 它和当年迁走的
elfsym/console/cli 是同一类。迁它的直接动因是批量探测: 一次跑多块表, 完整运行日志的价值
比单次大, 而 discover/ 层不能为此 import meterlib(那会破坏"探测器不认识具体表")。
迁后 _base_dir() 的"向上找含 src/ 的目录"判据不变(common/ 与 meterlib/ 同层), 日志仍落
帧收发基础/log/, 不与历史日志分裂。

把"跑测试脚本"变成会【落盘】的完整日志, 而不是只靠终端滚动:
  用法(脚本 main 包一层即可):
      from common import runlog
      def main():
          with runlog.run("4_6_aa80") as path:   # 自动建 log/ 夹 + 时间戳文件名 + 双写
              ... 原本的 CB.* 一行行, 一个不用改 ...

语义动词本来就 print 到 stdout, 本模块只做一件事: 运行期把 sys.stdout 换成"tee"——同样内容
既打到屏幕(真 stdout)、又写进 log/<name>_<时间戳>.log。所以 CB 动词/测试脚本都不必为"记日志"
改任何一行(符合"脚本薄 + 纪律=不变式"架构)。收尾补 run 头/尾(时间、脚本名、耗时), 异常也记。

- 默认日志夹 = 帧收发基础/log/(相对本模块自动算, 不依赖 cwd), 已 gitignore.
- 文件 = <name>_<YYYYmmdd_HHMMSS>.log; run 头写 name/开始时间, 尾写结束时间/耗时.
- 中途崩(异常): stdout 已 restore、文件仍开着, 补错误尾后重抛; 已打内容都留盘.

边界: tee **stdout 与 stderr 两路**(2026-09-15 起; 原先只接 stdout). 要分文件/分级另行扩展,
别为记日志去动动词/脚本.

**为什么 stderr 非接不可**(2026-09-15): 有一整类输出是往 stderr 走的, 而它们恰恰是"要人看一眼"
的那类 —— `breakpoint.Session._warn` 在 `quiet` 会话里的**降级**、录播带落盘失败, 都在 stderr 上。
只接 stdout 时这些内容**屏幕上看得到、日志里查不到**, 于是"复盘时日志是全的"这个假设不成立。

--- 读侧 `scan()`(2026-09-11 加)---

**为什么要有读侧**: 在此之前本模块**只有写侧**, 没有任何东西会拒绝一份**没写结尾**的日志 ——
于是"崩溃留下的半截文件"与"完整跑完的文件"在数据上**无法区分**, 只能靠人眼去找结尾那行
(`3_2_..._分析_2026-09-11.md` §9 末尾把这条记成了盲区)。读侧就是把"这份日志到底跑完没有、
里面有没有降级"变成**可判**的。

**⚠ 一条实测纠正**(2026-09-11, 全量盘点 104 份真日志后定的口径): 崩溃**不等于**半截。
异常从 `with runlog.run(...)` 里抛出来时 `__exit__` **照样会跑**, 所以崩溃日志**有** `==== END`,
只是 END 上带 `| 异常: TypeError` 且正文里有 `!! 异常:` 块(实测 104 份里 6 份如此, 含 L2 那次
`TypeError` 崩溃)。**真正的半截是另一种**: 进程被硬杀(`__exit__` 没机会跑), 那种才**没有** END
(实测 104 份里 1 份, 只有 58 字节, 一个 RUN 头就没了)。
所以 `complete` 判"有没有 END", `crashed` 另外判 —— **两者是两回事, 别用一个字段糊**。

**降级怎么认**: 本仓的"出了事/没做成/要人看一眼"一律打成 `!! ` 前缀(实测 20+ 种形态,
横跨 breakpoint/cmd_bank/各脚本)。`scan` 因此**不猜语义**, 只做两级结构判定:
  · `alerts`       = 所有含 `!!` 的行(原样, 带行号)
  · `degradations` = 其中以 `MARK_DEGRADED`(`[调试] [gdb] !! `)开头的那些 —— 这个前缀是 `breakpoint.Session._warn` 的**唯一出口**,
                     所以它精确对应"本轮调试会话能力被削减了"(喂狗钩子没装上 / 打不开会话 / 清残留 FPB 失败 …)。
    ⚠ 二级划分不是洁癖: `!! 表钟停在 2026-09-10 当天 —— 收尾请跑 _restore_all.py` 这类是**台面状态提醒**,
      不是本轮降级, 混成一个字段会让"降级"这个量失去意义。
    ⚠ `_warn` 在 `quiet` 会话里走 **stderr** —— 写侧自 2026-09-15 起**两路都 tee**(stdout + stderr),
      所以那种情况下降级照样进日志。在此之前只接 stdout, 那一类降级是**屏幕上有、日志里没有**。
      `scan` 这条前缀判据因此一直成立, 不必猜。

**边界**: `scan` 是**纯函数** —— 只读文件、只出事实, **不产任何判据、不改任何 verdict**。
它绝不是"看日志反推测试过没过"的通路(那个真源是 `common/judge.py` 的账本)。
`status`/`n_*` 只是把日志里**已经打过**的汇总行抠出来给人对账用, 抠不到就是 `None`, 不许补默认值。
"""
from __future__ import annotations

import datetime
import fnmatch
import json
import os
import re
import sys
import threading
import time
import traceback

# 第二路输出(机器读的事件流)。**方向是 runlog → events, 不能反过来** —— events 不 import 本模块,
# 反过来会成环(见 events 模块头"三条不能违反的约束"的 ③: 时刻格式由本模块**注入**给它)。
from common import events
from common import loglabel    # 字形(含调试行词头/来源格)的唯一定义处; 本模块只引用, 不自己拼


def _base_dir():
    """仓根 = 含 src/ 的那一级(= 帧收发基础). 不依赖调用方 cwd.

    ⚠ 2026-09-10 迁 src/ 时这里踩过一次: 原实现是"本模块上两级", 迁库前成立(帧收发基础/meterlib/),
    迁后变成 src/(src/meterlib/) → 日志就分叉到 src/log/ 去了, 与历史日志所在的 帧收发基础/log/ 分裂。
    改为向上找含 src/ 的目录, 与全仓其余脚本的路径引导同一判据 ——
    这条判据正是 meterlib/ → common/ 搬迁**不用再改这里**的原因(同深度, 同样爬到 帧收发基础/)。"""
    p = os.path.dirname(os.path.abspath(__file__))
    while not os.path.isdir(os.path.join(p, "src")) and os.path.dirname(p) != p:
        p = os.path.dirname(p)
    return p


def default_logdir():
    """日志夹 —— `log/<名>_<时刻>.log` 与同名的 `.jsonl` 都落这里。

    ⚠ **全仓唯一定义处**: 写日志的、扫日志的、读台账的一律经这里取, 不许各自再写一遍
    `os.path.join(_base_dir(), "log")`。那种写法 2026-09-20 之前散在三处(写/扫/命令行默认),
    谁改一处另外两处不响, 而表现是"日志分叉到了另一个夹子"、没有东西会红。
    """
    return os.path.join(_base_dir(), "log")


# ===================== 行级墙钟前缀(2026-09-17 加) =====================
#
# **为什么要有**: `log/` 下 172 份历史日志**一行时间都没有** —— 只有文件头的
# `==== RUN <名> @ <时间>` 与文件尾的"耗时 Ns"。于是"调试器几点停的""某一帧等应答等了多久"
# 这类问题在日志里**不可回答**(2026-09-17 复盘 `5_2_overload` 那次失败时实踩: 满篇 `RX(0)`,
# 却说不清它是几点开始的)。
#
# ⚠ **这是日志格式的改动, 不是"多打一行"**: 全仓凡"按行首读日志正文"的代码都会**静默失效**
#   (实测 `scripts/_backfill.py` 有 6 处; 它会因此把每一份新日志都当成"读不到真串口凭据"**拒收**)。
#   所以**读方一律走本模块的 `strip_ts()`**, 不许各自写正则 —— 与"指纹判定与报告 §0 必须由同一份
#   代码算"是同一条纪律: 写出来的格式与读回来的格式, 由**同一个名字**保证一致。
#
# **只加进日志文件, 控制台不加**: 屏幕上的排版是给人当场看的(缩进/对齐是排过的), 加前缀会挤掉它。
# 要连控制台一起加: 把 `STAMP_CONSOLE` 改成 True。
STAMP_CONSOLE = False
# 毫秒 —— 调试链一次往返常在毫秒量级, 到秒不够用。
TS_PREFIX = re.compile(r"^\[(\d{2}:\d{2}:\d{2}\.\d{3})\] ")


def stamp(t=None):
    """一个时刻的**内容**: `[HH:MM:SS.mmm]`(**不带**尾随空格)。

    ⚠ 时刻的**字面量只在这里出现一次** —— 两个用处共用它:
      · 日志行首前缀 = 本串 + 一个空格(见 `_stamp`);
      · `common/events.py` 写进 jsonl 的 `clock` 字段 = 本串**原样**(那一路由 `runlog.run()`
        在开场时注入, 见 `_RunLog.__enter__` 里的 `EV.bind(..., stamp=stamp)`)。
    于是两个文件里是**同一个字符串**: 在 jsonl 里看到 `18:30:42.090`, 去 `.log` 里搜同一串能对上。
    分开各写一份 `%02d:%02d:%02d` 的话, 两边会**各改各的**而没有任何东西会红 ——
    这正是本仓"报告 §0 的指纹与探测前核对的指纹必须是同一份代码算的"那条纪律的同一形状。
    """
    t = t or datetime.datetime.now()
    return "[%02d:%02d:%02d.%03d]" % (t.hour, t.minute, t.second, t.microsecond // 1000)


def _stamp(t=None):
    """一个时刻的前缀串(带尾随空格)。**同一次 write 里的多行共用它** —— 不为同一句话的每行各取一次钟。"""
    return stamp(t) + " "


def strip_ts(line):
    """剥掉行首的墙钟前缀 → `(时刻字符串 或 None, 剩下的原文)`。

    读日志的地方**一律**经这里, 不要各自写正则 —— 理由见上面那段 ⚠。
    2026-09-17 之前跑的日志没有前缀 ⇒ 返回 `(None, 原样)`, 新旧日志用同一套读法都读得出来。
    ⚠ 它对**物理行**用(前缀在最行首), 剥完剩下的缩进还在 ⇒ 调用方该 `.strip()` 就自己 strip。
    """
    m = TS_PREFIX.match(line)
    if m is None:
        return None, line
    return m.group(1), line[m.end():]


class _Tee:
    """同时写给 真流(屏幕) 和 日志文件 的写对象(够 write/flush 即可当 sys.stdout 用).

    `console` 收 stdout 也收 stderr —— 两路都用这一个类(2026-09-15 起).
    **进日志文件的每一行带墙钟前缀**(2026-09-17 起), 屏幕那一路不带 —— 见上面那段。"""
    __slots__ = ("console", "file", "_local")

    def __init__(self, console, file):
        self.console = console
        self.file = file
        self._local = threading.local()          # 见 `_stamped` 的 ③

    def write(self, s):
        # ⚠ `STAMP_CONSOLE` 为真时**只剥一次** —— 两路各调一次 `_stamped` 会取两次钟,
        #   同一句话在屏幕和文件里会差上几毫秒。
        if STAMP_CONSOLE:
            stamped = self._stamped(s)
            self.console.write(stamped)
            self.file.write(stamped)
        else:
            self.console.write(s)
            self.file.write(self._stamped(s))

    def _stamped(self, s):
        """逐行加前缀。三件事要一起想清楚, 少想一件都只表现为"某一行的时刻是别人的", 不报错:

          ① 一次 `write` 可能是**半行** —— `print(x, end="")` 在 CPython 里是先 `write("x")`,
             再 `write("")`; 而且 `print("a", "b")` 是 `write("a")`/`write(" ")`/`write("b")`/`write("\\n")`
             四次。所以**不能**在每个 `write` 前无条件挂前缀, 否则一句话会被挂上四个时刻。
          ② 一次 `write` 也可能是**多行** —— 一句话里带 `\\n`。只挂开头会漏掉后面那几行。
          ③ `breakpoint.with_trigger` 的串口动作跑在**后台线程**, 与主线程的 gdb 打印同时写这一个文件。
             "现在是不是行首"因此**按线程各记一份**(`threading.local`); 共用一份的话,
             甲线程开了一半的行会被乙线程的时间戳插进去。
        """
        if not s:
            return s
        st = self._local
        parts = s.split("\n")
        out, now = [], None
        mid = getattr(st, "mid_line", False)
        for i, seg in enumerate(parts):
            last = (i == len(parts) - 1)
            if last and not seg:
                break                # 行尾那个 \n 之后没有内容, 不必再挂一个前缀
            if not mid and seg:      # 空段(整行只是换行)不挂 —— 那只是排版用的空行
                now = now or datetime.datetime.now()
                out.append(_stamp(now))
            out.append(seg)
            if not last:
                out.append("\n")
            mid = (last and bool(seg))
        st.mid_line = mid
        return "".join(out)

    def flush(self):
        self.console.flush()
        self.file.flush()

    # ---- 流的形态转发给 console(2026-09-18) ----
    # 为什么必须有: 本类被装成 `sys.stdout`, 而 `common.console.ensure_utf8_stdout()` 是**照着
    # `sys.stdout` 的形态**决定怎么切 UTF-8 的 —— 它认三种: 已经utf-8 / 有 `reconfigure` / 有 `buffer`。
    # 本类原先三样都不露, 于是那个函数**静默什么都不做**(它的契约就是"失败不抛"), 底下真流
    # 还是 Windows 中文台的 cp936 → 一打出 `↔` 这类字符就 `UnicodeEncodeError` 把报表打断。
    # 转发这几个属性, 是"既然当 stdout 用, 就得答得上 stdout 该答的话"。
    # ⚠ 只转发**形态**, 不转发 write/flush —— 那两路是本类自己的语义。
    @property
    def encoding(self):
        return getattr(self.console, "encoding", "utf-8")

    @property
    def errors(self):
        return getattr(self.console, "errors", "replace")

    @property
    def buffer(self):
        return getattr(self.console, "buffer", None)

    def reconfigure(self, **kw):
        return self.console.reconfigure(**kw)

    def isatty(self):
        return bool(getattr(self.console, "isatty", lambda: False)())

    def fileno(self):
        return self.console.fileno()

    def writable(self):
        return True


class _RunLog:
    """with runlog.run(name) → context manager: 进入建文件+tee, 退出恢复+写尾."""

    def __init__(self, name, logdir=None):
        self.name = name
        self.logdir = logdir or default_logdir()
        self.path = None
        self.event_path = None        # 第二路(事件流)的落点; 见 __enter__
        self._ev = None               # 事件流句柄(bind 返回的那个); None = 没绑上
        self._real = None
        self._real_err = None
        self._t0 = 0.0

    def __enter__(self):
        os.makedirs(self.logdir, exist_ok=True)
        now = datetime.datetime.now()
        self.path = os.path.join(self.logdir,
                                 "%s_%s.log" % (self.name, now.strftime("%Y%m%d_%H%M%S")))
        self._file = open(self.path, "w", encoding="utf-8", errors="replace")
        self._file.write("==== RUN %s @ %s | PID %d ====\n"
                         % (self.name, now.strftime("%Y-%m-%d %H:%M:%S"), os.getpid()))
        self._file.flush()
        # ---- 第二路: 事件流(2026-09-17 加) ----
        # **与 .log 同一个主干名, 只换后缀** —— 于是"这次跑的两路输出"靠**文件名**就配得上,
        # 不需要另写一张对照表(写对照表的那种做法迟早会不同步)。也顺手避开了计划里 `log/<名>.jsonl`
        # 那个写法的问题: 同名的第二次跑会把第一次的**覆盖掉**。
        # `stamp=stamp` 注入本模块的时刻实现 —— 见 `stamp()` 的 ⚠: jsonl 的 `clock` 与 .log 的行首
        # 前缀于是是**同一个字符串**, 查到一个就能在另一个里搜到。
        self.event_path = os.path.splitext(self.path)[0] + ".jsonl"
        self._ev = events.bind(self.event_path, stamp=stamp)
        self._real = sys.stdout
        sys.stdout = _Tee(self._real, self._file)   # 进 tee: 动词 print → 屏幕 + 文件
        # ⚠ **stderr 也要接**(2026-09-15 补, 原先只换 stdout)。不接的话有一整类东西
        #   永远进不了日志, 而它们恰恰是"要人看一眼"的那类 —— 实测两处:
        #     · `breakpoint.Session._warn` 在 `quiet` 会话里走 stderr ⇒ **降级不在日志里**(见模块头那段 ⚠);
        #     · 录播带落盘失败(`breakpoint.py` 里 `[gdb] !! 录播带没写成…`)走 stderr ⇒ 也进不去。
        #   接上之后, "屏幕上看得到、日志里查不到"这件事不再存在。
        self._real_err = sys.stderr
        sys.stderr = _Tee(self._real_err, self._file)
        self._t0 = time.time()
        return self.path

    def __exit__(self, exc_type, exc, tb):
        sys.stdout = self._real                     # 先恢复屏幕(两个流都恢复, 再写尾巴)
        sys.stderr = self._real_err
        try:
            if exc is not None:
                import traceback
                self._file.write("!! 异常:\n%s\n" % "".join(traceback.format_exception(exc_type, exc, tb)))
            # ---- 事件流收尾: **丢了东西必须说出来**(2026-09-17) ----
            # 不报的话, "jsonl 里没有这条事件"与"这件事没发生"就分不开 —— 那正是本仓反复治理的
            # 那类静默。"绑不上"也一样要报: 绑不上 ⇒ 整份 jsonl 是空的, 而空文件与"这次真没发生
            # 什么"在数据上长得一模一样。
            # ⚠ **两条都直写 `self._file`**, 不是 `print`: 此刻 stdout / stderr 都已经换回真屏幕了
            #   (上面那两行 restore), 那时 print 出来的东西**进不了日志**。这与 `==== RUN`/`==== END`
            #   两行直写文件是同一个理由。
            # ⚠ `unbind` 只在**真绑上过**时才调: `unbind(None)` 的语义是"关栈顶", 而我们没绑上时
            #   栈顶是**外层那次跑**的那一层(嵌套跑真实存在, 见 events 的 `_STACK`) —— 调了就把
            #   外层的文件关了, 而外层后半程的事件会**静默消失**。
            but_why = events.why_not_bound()
            if self._ev is None:
                self._file.write("!! 事件流没绑上(%s) —— 本次**一条事件都没记**, "
                                 "那不是『什么都没发生』\n" % (but_why or "原因不明"))
            else:
                n_drop, why = events.unbind(self._ev)
                if n_drop:
                    self._file.write("!! 事件流丢了 %d 条: %s\n" % (n_drop, why or "原因不明"))
            self._file.write("==== END %s | 耗时 %.1fs%s ====\n"
                             % (self.name, time.time() - self._t0,
                                " | 异常: %s" % type(exc).__name__ if exc else ""))
        finally:
            self._file.close()
        return False                                 # 异常照常向上抛


def run(name, logdir=None):
    """便捷工厂 → 上下文管理器."""
    return _RunLog(name, logdir=logdir)


# ============================ 读侧(2026-09-11 加) ============================
# ⚠ 行首墙钟前缀的**格式**不在这一组里 —— 它由上面那段(`TS_PREFIX` / `strip_ts`)定, 因为写侧
#   `_Tee` 也要用它; 本组只管"头部/尾部/标记"这些**每行内容**的字面量。
# 头部/尾部/标记的**字面量只在这里出现一次** —— 写侧 `_RunLog.__enter__/__exit__` 用同一组常量,
# 于是"写出来的格式"与"读回来的格式"由**同一个名字**保证一致, 不会各改各的(那是本仓
# "指纹判定与报告 §0 必须由同一份代码算"那条纪律的同一形状)。
TAG_RUN = "==== RUN "
TAG_END = "==== END "
MARK_ALERT = "!!"                 # 本仓"出了事/没做成/要人看一眼"的统一前缀
# ⚠ 下面两条的**字形**由 `loglabel.debug_line` 出(2026-09-18 并: 原先这里手写 `"[gdb] !!"`,
#   而 `breakpoint._warn` 又手写了一遍 `"[gdb] !! "` —— 来源写死成 gdb, 且同一件事两个落笔处)。
MARK_DEGRADED = loglabel.debug_line(loglabel.DEBUG_GDB, "!! ")   # breakpoint.Session._warn 的**降级**出口前缀
MARK_NOTE = loglabel.debug_line(loglabel.DEBUG_GDB, "注 ")       # 同上函数的**提醒**出口(做成了 ⇒ 不算降级)
MARK_LEDGER_DEGRADED = "[降级]"   # common/judge.py 汇总里的降级行(账本侧对同一件事的复述)
MARK_SATISFIED = "[满足]"
MARK_FAILED = "[失败]"
MARK_UNPROVEN = "[未证]"
MARK_TOTAL = "── 总: "
MARK_NO_TOTAL = "── 本次不构成总结论"


def proof_level(serial):
    """那一行凭据是**强**的还是**弱**的(三态: "强" / "弱" / None)。

    · **强** = 行里有 `桥 …SN… | 探活: 应了…` —— 那是 `common.portsel` 当场从设备上读的:
      桥的 VID:PID 与 USB 序列号(桥不在位上枚举不到), 探活那一帧**是本表单播读钟、答出来的是表钟**。
    · **弱** = 只有 `—— 真串口` 三个字(2026-09-22 之前写的日志) —— 那是**自述**, 谁都能写;
      老日志不追认成"假", 但回填时要说清它是弱凭据。
    ⚠ 这条**不是**"能不能回填"的判据(`real_run_of` 才是) —— 它只报凭据的成色, 由读的人决定要不要另找证据。
    """
    if serial is None:
        return None
    s = serial.rstrip()
    if "| 探活: " in s:
        return "强" if "探活: 应了" in s else None
    if s.endswith("真串口"):
        return "弱"
    return None


def quarantine(path, why=""):
    """把一份**没拿到实测凭据**的记录挪出 `log/` 根 → `log/未实测/`(与同名 `.jsonl` 一起挪)。

    为什么要挪而不是删: `log/<名>_<时刻>.log` 这个名字空间**只装表应答过的跑次** ——
    名字本身就是判据, 那样"这份记录是不是实测"不必靠读内容、也不必靠谁记得。挪走之后再往里写
    一份"为什么没算实测"的一行, 让这份记录还能当诊断材料用, 但它不在实测的名字空间里了。

    返回挪过去之后的 `.log` 路径(挪不动就返回原路径, 并**如实说明**, 不静默)。
    """
    src = os.path.abspath(path)
    d = os.path.dirname(src)
    dest = os.path.join(d, "未实测")
    try:
        os.makedirs(dest, exist_ok=True)
        moved = src
        for ext in (".log", ".jsonl"):
            base = os.path.splitext(src)[0] + ext
            if os.path.exists(base):
                tgt = os.path.join(dest, os.path.basename(base))
                os.replace(base, tgt)
                if ext == ".log":
                    moved = tgt
        with open(moved, "a", encoding="utf-8", errors="replace") as fh:
            fh.write("!! 这一份不是实测记录(已挪出 `log/` 根): %s\n" % (why or "没读到实测凭据"))
        return moved
    except Exception as exc:                      # 挪不动也不许装成挪动了
        return "%s(没挪成: %s)" % (src, exc)


def real_run_of(serial, legacy_serial):
    """两条凭据 → "这一轮真开了硬件吗"。**这条规则只在这里写一份**(三态)。

    入参就是 `scan()` 返回的那两个字段(取日志里原样读到的正文), 出参:
      True  = 有凭据说"真开了硬件"
      False = 有凭据说"没开"(替身/干跑自报的那一行)
      None  = **一条凭据都没读到** ⇒ 判不出来。**不许塌成 False** —— "判不出来"要人看一眼,
              "判出来是假"直接拒收, 两种处置不同。

    凭据那行的字形(三段)与判法:
      · `串口: COM3 (Serial) —— 真串口 | 桥 10C4:EA60 SN=… | 探活: 应了, 表钟=…`
        ⇒ **强凭据**: 桥指纹是从设备树当场枚举的(桥不在位上枚举不到), 探活那帧是本表单播读钟、
        答出来的是表上现读的时间串。**取"探活: 应了"那一段**, 不是取"真串口"三个字。
      · `串口: COM3 (Serial) —— 真串口`(2026-09-22 之前写的) ⇒ **弱凭据**: 只有自述, 不追认成假,
        成色由 `proof_level` 报出来。
      · 探活那一格不是"应了"(没应答 / 没做 / 口没开成) ⇒ False, 直接拒收。
    ⚠ **别写成 `"真串口" in serial`** —— 不是真口的那一行里**含着同样四个字**
      (`… 这不是真串口, 这一份日志不是一次实测记录 …`), 那样写会把干跑判成真跑, 而这条判据
      存在的全部意义就是拒收它。2026-09-17 犯过这个包含式错误(自检当场红住才改对), 同一处错误
      当时也在 `scripts/_backfill.py` 里 ⇒ 收成一个函数, 那边 import 它。
    """
    if serial is not None:
        s = serial.rstrip()
        if "| 探活: " in s:
            # 2026-09-22 起的写法: 那一行里带**当场从设备读出来的**凭据(桥的 VID:PID 与 USB 序列号、
            # 探活那帧表答没答)。判据取"探活那一段是不是应了" —— 表没应答(或压根没问)的一律不是实测,
            # **哪怕口是真口、桥也真插着**: "桥插着"不等于"口后面是那块表", 那正是本仓不认的那一步。
            return "真串口" in s and "探活: 应了" in s
        if s.endswith("真串口"):
            # 老日志(加探活凭据之前): 只有自述, 没有侧证。**不追认成假** —— 那时全仓就是这么写的;
            # 但成色弱, 读的人可以另找证据(见 `proof_level`)。
            return True
        return False               # 读到了那一行, 而它不是"真串口" ⇒ 替身/干跑, 判出来是假
    if legacy_serial is not None:
        # 老日志兜底: 加 `_serial_note` 之前, 选口时打 `[串口] 用它: COMx`; 而干跑把选口那一步
        # **整个换掉了** ⇒ 干跑日志里一句都没有。所以"读得到"就够当凭据, 不必知道它是不是真口名。
        return True
    return None                    # 什么都没有 ⇒ 判不出来


def scan(path):
    """读**一份**运行日志 → 事实。纯函数(只读文件, 不产判据; 见模块头「读侧」那段)。

    返回 dict:
      path/name/n_lines/bytes
      complete     : 有 `==== END` 吗(= 收尾那段代码跑到了)
      crashed      : 有 `!! 异常:` 块 或 END 上带 `| 异常: ` 吗(**与 complete 是两回事**)
      exception    : 异常类型名(str) 或 None
      duration_s   : END 上的**耗时**秒(float) 或 None
      alerts       : [{"line_no": int, "text": str, "ts": str|None}] —— 全部含 `!!` 的行
      degradations : 上面的子集, 只留 `MARK_DEGRADED`(`[调试] [gdb] !! `)开头的(= 本轮调试会话降级)
                     `ts` = 该行的墙钟 `HH:MM:SS.mmm`; 2026-09-17 之前跑的日志没有前缀 ⇒ None
                     (**不补默认值** —— 抠不到就是"这份日志没记过时刻")
      status       : "通过"/"失败"/"未定论"/"不构成总结论" 或 None(**抠不到就是 None, 不补默认**)
      counts       : {"满足": n, "失败": n, "未证": n}
      exit_code    : 日志里自报的退出码(int) 或 None
      serial       : `trial._serial_note` 那行的原文(真串口报口名; 替身自报"不是一次实测记录") 或 None
      legacy_serial: 老日志的兜底凭据 —— `[串口] 用它:` / `[串口] 显式指定:` 挑到的口名 或 None
      real_run     : 由上面两条**凭据**判出来的"这一轮真开了硬件" 或 None(两条都没有 ⇒ 判不出来)
                     ⚠ 干跑与真跑在对端不答时**都是 RX(0)**, 从别处分不开 ⇒ 只能靠这两条。
                       规则住这里(2026-09-17), `scripts/_backfill.py` 那道"实测记录检查"与
                       `project/tests/_suite.py` 的报表**共用它** —— 各写一份就迟早分叉,
                       而分叉的后果是"把干跑当实测回填"这类**伪造记录**。
    """
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()

    out = {"path": os.path.abspath(path), "name": None, "n_lines": len(lines),
           "bytes": os.path.getsize(path),
           "complete": False, "crashed": False, "exception": None, "duration_s": None,
           "alerts": [], "degradations": [],
           "status": None, "counts": {MARK_SATISFIED[1:-1]: 0, MARK_FAILED[1:-1]: 0,
                                      MARK_UNPROVEN[1:-1]: 0},
           "exit_code": None,
           "serial": None, "legacy_serial": None, "real_run": None}

    for i, ln in enumerate(lines, 1):
        # 先剥行首的墙钟前缀(2026-09-17 起日志正文带它), 再 strip 缩进 —— **次序不能反**:
        # 前缀在整个物理行的最行首, 而正文行本身是缩进过的(`   [满足] …`);
        # 先 strip 会把缩进与前缀一起处理得看不出先后, 容易写成一个"有时候对"的判据。
        ts, _rest = strip_ts(ln)
        s = _rest.strip()
        if s.startswith(TAG_RUN):
            # `==== RUN <name> @ <时间> | PID <n> ====`
            rest = s[len(TAG_RUN):]
            out["name"] = rest.split(" @ ", 1)[0].strip() or None
        elif s.startswith(TAG_END):
            out["complete"] = True
            # 耗时是 END 行上的固定字段(`==== END <name> | 耗时 X.Xs[ | 异常: T] ====`)。
            # 抠不到留 None —— 它由写侧保证格式, 这里替它补 0 就是把"没读到"变成"很快"。
            _m = re.search(r"耗时\s+([0-9.]+)s", s)
            if _m:
                out["duration_s"] = float(_m.group(1))
            if "| 异常: " in s:
                out["crashed"] = True
                out["exception"] = s.rsplit("| 异常: ", 1)[1].strip().rstrip("=").strip() or None
        # ---- 「这份日志到底是不是一次实测」的两条凭据(见返回值里的 serial/legacy_serial)----
        # 这条判据的**对象是行首那几句话本身**, 不是"我记得那一轮没干跑"。2026-09-16 真漏过:
        # 干跑日志落进了 `log/`(那是跑过的实况记录), 它与一次真跑的记录**长得一样**。
        if s.startswith("串口: "):
            out["serial"] = s[len("串口: "):].strip()
        _sm = re.match(r"^\[串口\] (?:用它|显式指定):? ?(\S+?)(?:\s|$)", s)
        if _sm:
            # 老日志兜底: 加 `_serial_note` 之前, 选口时打这两句; 而干跑把选口那一步**整个换掉了**
            # ⇒ 干跑日志里一句都没有。所以"读得到"就够当凭据, 不必知道它是不是真口名。
            out["legacy_serial"] = _sm.group(1).strip("——")
        # 判据本体收在 `real_run_of()` 里(三态) —— 这里只把两条凭据递过去, 不在这儿重写一遍:
        # `scripts/_backfill.py` 那道实测检查 import 的是**同一个函数**, 于是"日志侧怎么判"与
        # "回填侧怎么判"由结构保证一致, 不靠"两边记得一起改"。
        out["real_run"] = real_run_of(out["serial"], out["legacy_serial"])
        if MARK_ALERT in ln:
            rec = {"line_no": i, "text": s, "ts": ts}
            out["alerts"].append(rec)
            if s.startswith(MARK_DEGRADED):
                out["degradations"].append(rec)
        # 账本侧的降级行(`judge.render` 打的 `[降级] …`) —— 与上面那档**同一件事的第二个出处**。
        # 为什么要收两处: `[gdb] !!` 是**会话当场**喊的, `[降级]` 是**收尾算账时**复述的;
        # 同一轮里两条都该在。只认一处的话, 一旦哪天 `_warn` 改了前缀、或会话没喊而账本喊了,
        # 盘点就会**静默漏掉一整类降级**(本模块存在的全部理由就是防这个)。
        # ⚠ `[降级]` 那几行**不含** `!!`(它们是账本行, 不是告警), 所以这里在 `if` **外面**判。
        if s.startswith(MARK_LEDGER_DEGRADED):
            out["degradations"].append({"line_no": i, "text": s, "ts": ts})
            out["alerts"].append({"line_no": i, "text": s, "ts": ts})
        if s.startswith("!! 异常"):            # __exit__ 补的错误尾(异常类型下一行才给)
            out["crashed"] = True
        if out["exception"] is None and s.startswith("!! 异常") and i < len(lines):
            _t = re.match(r"^([A-Za-z_][A-Za-z0-9_.]*):", lines[i].strip())
            if _t:
                out["exception"] = _t.group(1)
        for mk in (MARK_SATISFIED, MARK_FAILED, MARK_UNPROVEN):
            if s.startswith(mk):
                out["counts"][mk[1:-1]] += 1
        if MARK_NO_TOTAL in s:
            out["status"] = "不构成总结论"
        elif MARK_TOTAL in s:
            # `── 总: 失败(判据: 满足 6/8; …)` —— 取冒号后到第一个 `(` 之前那个词
            out["status"] = s.split(MARK_TOTAL, 1)[1].split("(", 1)[0].strip() or None
        _e = re.search(r"退出码[:：]\s*([0-9]+)", s)
        if _e:
            out["exit_code"] = int(_e.group(1))
    return out


def scan_dir(logdir=None, pattern="*.log"):
    """扫一整个日志夹 → [scan(…), …], 按文件名排序(文件名带时间戳 ⇒ 顺序即时间序)。"""
    logdir = logdir or default_logdir()
    return [scan(os.path.join(logdir, fn))
            for fn in sorted(f for f in os.listdir(logdir) if fnmatch.fnmatch(f, pattern))]


def _census(rows):
    """盘点汇总 → (整体计数 dict, 逐条异常行 list)。给人看的那几行也在这里定死。"""
    n = len(rows)
    bad = [r for r in rows if not r["complete"]]
    cr = [r for r in rows if r["crashed"]]
    dg = [r for r in rows if r["degradations"]]
    tot = {"日志数": n, "未写完(无 END)": len(bad), "跑崩(有异常)": len(cr),
           "带调试会话降级": len(dg), "带任何 !! 告警": sum(1 for r in rows if r["alerts"])}
    return tot, bad, cr, dg


def _main(argv):
    """`python -m common.runlog [日志夹或单份日志]` → 盘点(只读, 不产判据)。"""
    from common.console import ensure_utf8_stdout
    ensure_utf8_stdout()
    target = argv[0] if argv else default_logdir()
    if os.path.isdir(target):
        rows = scan_dir(target)
    else:
        rows = [scan(target)]
    tot, bad, cr, dg = _census(rows)
    print("== 运行日志盘点: %s ==" % target)
    for k, v in tot.items():
        print("   %-18s %d" % (k, v))
    # ⚠ 未写完 / 跑崩的**逐条点名**(它们正是"看上去像证据、其实半截"的那一类);
    #   降级只报涉及份数 —— 它在本仓常态存在, 逐条列会淹掉真问题。
    for label, sel in (("未写完(无 END)", bad), ("跑崩(有异常)", cr)):
        for r in sel:
            print("   [%s] %s  (%.0f B%s)" % (
                label, os.path.basename(r["path"]), r["bytes"],
                "" if r["exception"] is None else " | 异常: %s" % r["exception"]))
    if tot["带调试会话降级"]:
        print("   注: %d 份带 `[gdb] !!` 调试会话降级(喂狗钩子没装上等) —— 见 breakpoint.Session._warn"
              % tot["带调试会话降级"])
    return 0 if not bad and not cr else 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
