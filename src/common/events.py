# -*- coding: utf-8 -*-
"""
common/events.py —— **事件流**: 与运行日志并列的第二路输出, 只给机器读。

一句话定位
----------
`runlog` 回答的是"这次跑**打印**了什么", 本模块回答的是"这次跑**发生**了什么"。
前者是给人读的排版(缩进树、`==` 取证行、汇总块), 后者是 `log/<名>_<时间戳>.jsonl` 里
一行一条 JSON。两路**并列, 不是替换** —— 把那些专门排过版的 print 塞进 `log.info()`
只会让人读的那一路变难读。

**为什么非要有它**(2026-09-17 定, 起因: 复盘 `5_2_overload` 那次失败)
--------------------------------------------------------------------------
翻 `log/` 下 172 份日志, 三个问题一个字都答不出来: 调试器**几点**停的 / 停在**哪里** /
停住那一刻 **watch 到的值是多少**。查下去发现根因**不是"哪一处忘了 `print`"**:

    库里根本没有"事件"这个概念。 "halt 发生了"这件事, 没有任何一处代码知道它值得记 ——
    普通 halt 走的路径上连个对象都不构造(只有"等断点命中"那条路才构造 `Hit`)。
    所以它不是**漏打**, 是**从来没被记过**。

因此本模块的边界是**收编已存在的数据, 只新造一条**:

    serial.tx / serial.rx   串口收发        来源 `common.portsel.tx_recv`(已打印)
    gdb.cmd / gdb.reply      MI 收发        来源 `_trace_sent` / `_trace_recv`(已留痕)
    srv                      J-Link server 来源 `_trace_srv`(已留痕)
    bp.set                   下断点          来源 `bp_set`(已打印)
    bp.hit                   断点命中        来源 `report()` / `record()`(已打印)
    inject                   受控注入        来源 `Session.inject` / `with_inject`(已打印)
    verdict                  本次结论        来源 `common.judge`(已打印)
    halt                     **核心停住**    ← **唯一新造的**。见下。

`halt` 为什么非新造不可: 上面那些事件里, "停在哪 / 读到什么"只在**等断点**那条路上有。
可 172 份日志里最常见的停住根本不是等来的 —— 是残留观察点把核撂停、是 `-exec-interrupt`、
是单步。那几种停住**当时没有任何一行输出**, 于是"调试器什么时候停的"在日志里不可回答。

三条不能违反的约束
------------------
① **绝不许改变 stdout 时序、绝不许加阻塞 I/O。**
   `breakpoint.with_trigger` 是"后台线程串口 + 主线程等断点"的交错, 串口时序错了整轮就废。
   故 `emit()` 只做: 组一个 dict → `json.dumps` → 一次 `write` + 一次 `flush`。
   **每条都 flush** 是有意的: 本仓要的是复盘, 硬杀之后**最后那几条恰恰是最想看的那些**;
   而一次 flush 是微秒量级, 串口那条路的时间尺度是**毫秒到秒**(轮询 `sleep(0.02)`、
   等应答 `wait=2.0`), 差三个数量级 —— 拿这点开销换"崩了也留得下", 划算。
   要优化它之前请先算这笔账, 别凭"看起来更安全"改成缓冲。

② **失败必须计数, 绝不静默丢。**
   "日志里没有"与"这件事没发生"必须分得开 —— 这是本仓反复治理的那一类错
   (见 `runlog` 模块头、`_trace_srv` 的"筛掉的要计数")。所以: 写失败、字段不可序列化、
   超上限、绑定失败, **每一类都进 `dropped()` / `为什么没绑上`**, 由 `runlog` 收尾写进人读日志。
   `emit()` 本身**永不抛异常** —— 打点把正在跑的测试搞崩, 比不打点坏得多。

③ **写出来的时刻与日志行首前缀必须是同一个字符串。**
   故时刻的格式**不在这里定义** —— 由宿主在 `bind(..., stamp=...)` 时注入
   (实际调用方 `runlog.run()` 注入的就是 `common.runlog.stamp`)。
   没注入 ⇒ **不记 `clock` 字段, 也不补一个默认格式**。理由同本仓"报告 §0 的指纹与探测前
   核对的指纹必须由同一份代码算": 两份各自实现的 `%02d:%02d:%02d` 会各改各的而没有任何东西会红。

依赖方向: 本模块只 import 标准库, 属于 `common` 层的中立积木 ——
`meterlib`(串口)与 `swdbg`(调试器)都要用, 而后者号称"零 meterlib 依赖", 所以它**只能**住这儿。
⚠ 本模块**不 import `runlog`**: 是 `runlog` import 它(开场 bind / 收尾 unbind),
反过来 import 会成环。

事件长什么样
------------
```json
{"t": 1758100242.09, "clock": "[18:30:42.090]", "seq": 17, "kind": "serial.tx",
 "n": 19, "tag": "645.factory", "hex": "FE FE FE FE 68 ..."}
```
  · `t`     epoch 秒(float) —— 排序、求差(「这一帧等应答等了多久」)用这个, 别解析字符串。
  · `clock` `[HH:MM:SS.mmm]` —— 与 `.log` 行首前缀**逐字相同**; 用来在 jsonl 里查到时刻之后
            去人读日志里搜同一串。**没有注入 stamp 的宿主就没这个键**(不补默认值)。
  · `seq`   同一份事件文件里的**到达序**(从 1 起)。为什么除了时刻还要它: 串口动作跑在后台线程,
            两个线程同时写, **毫秒会撞在一起** —— 那时 `t` 分不出先后, `seq` 分得出。
  · `kind`  事件名(下表那些)。`kind`/`t`/`clock`/`seq` 是本模块的**保留键**(见 `RESERVED`):
            `kind` 由**函数签名**保证顶不掉(它没有默认值, 传 `kind=` 当场 `TypeError`),
            其余三个靠 `emit` 里的**字典次序**。

用法(库侧)
----------
    from common import events as EV
    EV.emit("serial.tx", n=len(frame), tag=tag, hex=frame.hex(" ").upper())

热点路径上先问一句 `EV.enabled()`, 免得为一条注定丢掉的事件白拼一串 hex:
    if EV.enabled():
        EV.emit("serial.rx", n=len(buf), tag=tag, hex=buf.hex(" ").upper() or None)

命令行入口:
    python -m common.events          # 打本文件的说明(__doc__); 本模块没有别的命令行动作
"""
from __future__ import annotations

import json
import os
import threading
import time

# 一份事件文件最多写多少条。**超了就停止写入并计数**, 不是无限涨 ——
# 一个失控的轮询循环(每毫秒一条 MI 命令)能把盘写满, 而那种情况下这份文件也没人看。
# 上限本身不是"静默": 撞上它会在收尾被打成 `!!` 行(见 `unbind` 与 `runlog.__exit__`)。
MAX_EVENTS = 200000

# 由本模块写入的键。**这是给读方的契约**(写 jsonl 的解析脚本照它认那几个结构字段),
# 不是摆设 —— 每条事件都必须带全它们。
# 调用方**顶不掉**它们: `kind` 由函数签名保证(传 `kind=` 会 TypeError), 其余三个见 `emit` 里的注释。
RESERVED = ("kind", "t", "clock", "seq")

_LOCK = threading.Lock()
# **一路事件文件 = 栈上的一层**, 栈顶那层收货(见 `bind`/`unbind`)。
# 为什么是栈而不是"当前那一个": 2026-09-17 自检实踩 —— 起初写的是 `_sess = 最新 bind 的那个`,
# 于是**内层 bind 会把外层那一路直接关掉**。表现极隐蔽: 外层后半程的事件**一条都没写进去**,
# 而 jsonl 文件仍然存在、仍是非空、也不报任何错(我以为在测"中文不被转义", 实际是在测一个
# 早就被关掉的文件 —— 它当然是空的)。这正是本仓反复治理的那类"静默丢东西"。
# 嵌套是真实存在的: 一个脚本里调另一个脚本的 `main()`(带自己的 `runlog.run`)就会发生。
_STACK = []
_bind_error = None      # 绑定失败的原因(str)或 None —— **不静默**: 由收尾报出来


def _sess():
    """栈顶那一路(收货方); 空栈 ⇒ None。"""
    return _STACK[-1] if _STACK else None


class _Session:
    """一路事件文件。`bind()` 造它, `unbind()` 关它。"""

    __slots__ = ("path", "fh", "stamp", "seq", "dropped", "full", "fail", "why")

    def __init__(self, path, fh, stamp):
        self.path = path
        self.fh = fh
        self.stamp = stamp      # 注入的时刻函数; None = 不记 clock 字段(见模块头 ③)
        self.seq = 0            # 已写条数
        self.dropped = 0        # 因超限/写失败丢掉的条数
        self.full = False       # 撞过 MAX_EVENTS
        self.fail = 0           # 写失败/不可序列化的次数
        self.why = ""           # 最后一次失败的原因(只留第一条, 免得同一个病刷屏)


def enabled():
    """现在 `emit()` 会不会真被记下来。热点路径上用它免掉白拼 payload。"""
    return bool(_STACK)


# ---------------- 配对号: 回答"**这一帧 ↔ 这一停读到了什么**" ----------------
# 一次触发窗口里**同时**发生两类事: 后台线程在发**串口帧**, 主线程在等**断点停**。复盘要回答的
# 恰恰是"停住那一刻读到的值, 对应的是哪一帧" —— 而这两件事分属两个包(串口在 meterlib,
# 触发窗口在 swdbg), 且 **swdbg 不许 import meterlib**(边界铁律)。所以配对号不能靠调用方
# 传参穿过去, 只能由本模块提供一个**环境值**: 谁发事件, 谁自动带上当前那一段的号。
#
# ⚠ 三条不许改的:
#   ① **必须是全局的, 不是 `threading.local()`**: 触发流程**故意**把串口动作放在后台线程上
#      (`with_trigger` 的理由是"放主线程会死锁"), 线程局部的话后台那半程一条都盖不上章 ——
#      而"哪一帧"恰恰就在那半程里。
#   ② **号是整数, 名字单独记一条**: 同一个脚本会把同一段跑好几遍(5-2 就是), 拿名字当号
#      分不开"哪一次"; 而光有号又读不出人话, 故 `pair.begin` 一条给号↔名对照。
#      窗口可**嵌套**(外层"哪段/干什么", 内层是机制), 故那条对照还带 `parent` 父号 ——
#      事件盖的是最内层的号, 只留名字的话最有人话的那句会被埋掉。
#   ③ **调用方显式传了 `pair=` 就按它算**(`setdefault`), 环境值只是默认 —— 见 `emit`。
_PAIR = []              # 栈: [(号, 名字)]; 可嵌套, 退出还原上一层
_PAIR_N = 0             # 已发过多少个配对号(一轮内单调)


class _Pairing:
    """`pairing()` 的返回物。**不直接用**, 见 `pairing()`。"""

    __slots__ = ("name", "no")

    def __init__(self, name):
        self.name = name
        self.no = None

    def __enter__(self):
        global _PAIR_N
        _PAIR_N += 1
        self.no = _PAIR_N
        # ⚠ **先取父号, 再压栈** —— 反过来取到的就是自己, 于是每条 `pair.begin` 都自称是自己的爹。
        parent = _PAIR[-1][0] if _PAIR else None
        _PAIR.append((self.no, self.name))
        # 号 → 名字的那一条对照。**先发它、再发这一段里别的事件** —— 次序就是"先有这一段"。
        # ⚠ `parent` 不是装饰: 窗口是**套着**开的(`inject_hit` 套 `with_inject`, 外层那个的名字
        #   带着"哪段/干什么"的人话, 内层那个只有机制名)。事件盖的是**最内层**的号, 于是
        #   光看名字, 最有信息量的那句反而丢了(2026-09-18 在 5-2 的真跑日志上量到: 外层窗口
        #   只有 2 条、内层 302 条 —— 信息量多的名字几乎没人用)。带上父号, 读的一侧顺着链往上
        #   就能把名字接全; 不必让任何调用方知道自己在不在别人的窗口里。
        emit("pair.begin", pair=self.no, name=self.name, parent=parent)
        return self

    def __exit__(self, *exc):
        # ⚠ 只弹自己那一层: 里面若还开过别的配对, 它自己已收干净(栈式, 与 `bind`/`unbind` 同律)。
        if _PAIR and _PAIR[-1][0] == self.no:
            _PAIR.pop()
        else:                                   # 有人乱序退栈 —— 按号找出来摘掉, 不留脏层
            for i in range(len(_PAIR) - 1, -1, -1):
                if _PAIR[i][0] == self.no:
                    del _PAIR[i]
                    break
        return False


def pairing(name):
    """上下文: **这一段里发的所有事件盖同一个配对号**。用法 ——

        with EV.pairing("断[C] TaskRate.c:85"):
            ...    # 期间 serial.tx/rx、halt、bp.hit、inject 全带 `pair=7`

    开在**触发窗口**里(由 `breakpoint` 自己开, 不劳测试脚本) —— 窗口的边界正是"这一帧为的
    是哪一停"这句话的边界。名字写**人认得出的那一件**(断点位置 + 干什么), 因为它是给
    读日志的人用的; 机器只认那个号。
    """
    return _Pairing(name)


def current_pairing():
    """当前配对号(int) —— 没在配对里 ⇒ None。"""
    return _PAIR[-1][0] if _PAIR else None


def emit(kind, **fields):
    """记一条事件。**永不抛异常**; 没有绑定 / 写不进去 ⇒ 静默丢弃并计数, 见模块头 ②。

    ⚠ **不确定时刻在这里取**(`time.time()`), 不是让调用方传 —— 调用方多一个"忘了传时刻"
    的可能, 而时刻恰恰是这一路存在的理由。要的是"这件事**什么时候**被记下的", 不是"调用方
    觉得现在几点"。
    """
    s = _sess()                  # ⚠ 不加锁: 栈的 append/pop 本身是原子的, 而 emit 在热点路径上
    if s is None:
        return
    try:
        # ⚠ 字典**先放调用方的字段、再放保留键** —— 反过来的话调用方传个 `t=` 就把时刻顶掉了,
        #   而那种顶替**不会报错**, 只表现为"这条事件的时刻是别的东西"。保留键由本模块写。
        d = dict(fields)
        # 配对号(若在某个触发窗口里)。**`setdefault`, 不是赋值** —— 调用方显式传了 `pair=`
        # 就按它算: 显式压环境, 与"保留键由本模块写"是两回事(那个是**不许**调用方顶)。
        # 也不在这里抛异常: 本函数在串口后台线程上跑, 抛出去会打断那一帧(见模块头 ①)。
        if _PAIR:
            d.setdefault("pair", _PAIR[-1][0])
        d["kind"] = kind
        d["t"] = round(time.time(), 3)
        if s.stamp is not None:
            d["clock"] = s.stamp()
        d["seq"] = s.seq + 1
        line = json.dumps(d, ensure_ascii=False, separators=(",", ":")) + "\n"
    except Exception as exc:                 # 字段不可序列化(如传了个对象)
        _note_fail(s, "字段不可序列化: %s" % exc)
        return
    with _LOCK:
        if s.full:
            s.dropped += 1
            return
        try:
            s.fh.write(line)
            s.fh.flush()                    # 每条都冲 —— 见模块头 ① 的那笔账
        except Exception as exc:
            _note_fail(s, "写失败: %s" % exc)
            return
        s.seq += 1
        if s.seq >= MAX_EVENTS:
            s.full = True                   # 到此为止: 再写就只计数(见 MAX_EVENTS)


def _note_fail(s, why):
    """记一次写失败。**同一个病只留第一条原因** —— 刷屏会把真问题淹掉(本仓治过的那类)。"""
    s.fail += 1
    s.dropped += 1
    if not s.why:
        s.why = why


def bind(path, stamp=None):
    """开一路事件文件 `path` → 句柄(给 `unbind` 认), **压在栈顶**。开不成 ⇒ 返回 None 并**记下原因**。

    `stamp` 是注入的时刻函数(→ `"[HH:MM:SS.mmm]"`), 由宿主给 —— 实际调用方是
    `runlog.run()`, 注入 `common.runlog.stamp`(见模块头 ③)。不给 ⇒ 这一路不记 `clock`。

    ⚠ **不关已有的那一路** —— 新开的一层只是压在上面(栈的语义, 见 `_STACK` 那段)。
    内层退出(或忘了解绑)之后, 外层那一路照旧收货。**别改回"关掉上一个"**: 那会让嵌套时
    外层后半程的事件静默消失, 而文件还在、也不报错。
    """
    global _bind_error
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        # 与 .log 同一套: utf-8 + errors=replace。**不要**加 `newline=""` —— 那在 Windows 上
        # 会写出裸 `\n`, 而本仓的读侧一律 `splitlines()`, 两种都读得动, 没必要特意。
        fh = open(path, "w", encoding="utf-8", errors="replace")
    except Exception as exc:
        _bind_error = "打不开 %s: %s" % (path, exc)
        return None
    s = _Session(path, fh, stamp)
    _STACK.append(s)
    _bind_error = None
    return s


def unbind(token=None):
    """关掉 `token` 指的那一路 → `(丢了几条, 为什么)`; 0 与 None 表示这一路干净。

    `token` 是 `bind` 返回的句柄: **只关那一层**, 不管它是不是栈顶 —— 找不着(已关过 /
    是别人的句柄)⇒ 什么都不做, 返回 `(0, None)`。给 `None` = 关栈顶(收尾兜底用, 见
    `runlog.__exit__`: 那时只认"最上面这一层是我的")。
    """
    if token is None:
        s = _sess()
        if s is None:
            return 0, None
        _STACK.pop()
        return _close(s)
    for i in range(len(_STACK) - 1, -1, -1):
        if _STACK[i] is token:
            del _STACK[i]
            return _close(token)
    return 0, None


def depth():
    """现在压着几层("有没有忘了解绑"的查法)。"""
    return len(_STACK)


def _close(s):
    """关一路并回报 `(丢了几条, 为什么)`。"""
    try:
        s.fh.close()
    except Exception as exc:
        if not s.why:
            s.why = "关闭失败: %s" % exc
    if s.full and not s.why:
        s.why = "撞上上限 %d 条, 后面的没写" % MAX_EVENTS
    return s.dropped, (s.why or None)


def why_not_bound():
    """上一次 `bind` 为什么没绑上(`None` = 绑上了 / 还没绑过)。

    为什么要有这个出口: 绑不上意味着**整份 jsonl 是空的**, 那与"这次跑真的什么都没发生"
    在文件层面长得一模一样 —— 不报出来就是一个静默的空证据。由 `runlog.__exit__` 收尾打 `!!`。
    """
    return _bind_error


if __name__ == "__main__":
    print(__doc__)
    raise SystemExit(0)
