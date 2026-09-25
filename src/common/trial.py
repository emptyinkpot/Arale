# -*- coding: utf-8 -*-
"""
common/trial.py —— 一个测试子项的**运行外壳**。

2026-09-18 从 `meterlib/` 搬来 `common/`。理由: 本文件**零 meterlib 依赖**(只用 cli/runlog/judge,
都在本包), 住在 `meterlib` 是住错包 —— 而且 `swdbg` 那条边界铁律(不许 import meterlib)曾让 swdbg
一侧用不上这个外壳; 搬过来之后引它不越界。搬动时一字未改逻辑。

为什么要有它
------------
每个 `_test_<子项>.py` 的 `main` 都长着同一段**固定开销**, 而这段开销里有两处**写错不报错**:

  1. **收尾次序**: `g.close()` → `J.feed_degradations(g)` → `ser.close()`。次序是承重的 ——
     核心停着时串口一条帧都发不出去, 而"串口像坏了"与"表真坏了"现象一样(CLAUDE.md 调试链纪律 5)。
     实测漂移已经发生: 5-8 把 `J.summary()` 打在 `finally` **里面**, 3-2 打在外面。
  2. **降级进账本**: 忘了 `feed_degradations` ⇒ "喂狗钩子没装上"这类**台面缺能力**在账本与退出码里
     一个字都没有 —— 那一轮与狗喂得好好的那轮, 退出码一样。

它包掉的是**外壳**(argv / runlog / Judge 构造 / 串口开一次 / 会话开关 / 收尾三连 / 汇总 / 退出码),
**不是判据**。判据仍住库动词与 `common/judge.py`, 段里只写"这一步发什么、拿回什么"。

⚠ 本模块**不许 import `swdbg`** —— `swdbg` 与 `meterlib` 是平行两包, 彼此不相识(CLAUDE.md「依赖
  倒置」)。调试会话由**脚本**把 `swdbg.breakpoint` 作为 `gdb=` 传进来; 不传就是纯黑盒(没会话 ≠ 降级,
  那种观测由 `Judge` 的 `skipped` 说)。
⚠ 它当然也不许 import `project` —— 画像由 `common.profile` 取(`common` 谁都不 import, 见 `common/__init__.py`)。

脚本一侧的完整形状
------------------
    from meterlib import cmd_bank as CB
    from common import trial as TR
    from swdbg import breakpoint as GD          # 只做黑盒时删掉这一行, 且不传 gdb=

    BP_A = ("TaskX.c", 82)                  # ⚠ `BP_<X>`/`VARS_<X>` 必须是**字面量元组**, 见下
    VARS_A = ("swTime[0]", "swTime[1]")

    def part_write(ctx):
        recs, why, scope = CB.<判过积木>(ctx.ser, ..., trig=ctx.trig(), bp=ctx.bp(BP_A))
        ctx.hold(recs, why, scope, "写")

    if __name__ == "__main__":
        raise SystemExit(TR.run_subitem("<子项标题>", CB.<子项>_criteria(),
                                        name="<短名>", parts=[("写", part_write)], gdb=GD))

⚠ **`BP_<X>` ↔ `VARS_<X>` 仍是承重命名, 且必须留在模块级**: `scripts/_check_anchors.py` 靠
  `ast.literal_eval` 从脚本源码里抠这两个名字配对, 拿断点去对源码核"要读的变量在断点那一行赋过值没有"。
  把它们塞进参数对象、或写成 `CB.SOMETHING`, 就抠不出来 —— 那种观测从此**静默地没人核过**。
"""
from __future__ import annotations

import sys
from functools import partial

from common.console import ensure_utf8_stdout
from common.cli import guard_argv
from common import faultlog
from common import judge
from common import runlog

Judge = judge.Judge


class Stop(Exception):
    """段里抛它 = **这一轮到此为止**(如前置条件不满足、只能记 TBD 并收场)。

    收尾照常跑(它继承 Exception, `finally` 兜得住), 然后正常出账本与退出码 —— 不要 `sys.exit`,
    那会把"为什么停"挡在日志外面。
    """


class Ctx(object):
    """段拿到的东西: 串口 + 账本 + 会话开关 + 段与段之间的状态袋。

    `ctx.state` 是**同一个 dict**(整轮一份): 上一段的基线递给下一段走 `ctx.state["pre"] = …`,
    别用模块级全局量 —— 那种"段之间靠全局量传值"在段被单独跑时读到的就是上一轮的陈值。
    """

    def __init__(self, run):
        self._run = run
        self.ser = run.ser
        self.J = run.J
        self.waived = run.waived
        self.state = {}

    # ---- 会话(断点观测) ----
    @property
    def g(self):
        return self._run.session_obj

    def session(self, **kw):
        """按需开调试会话(一律 **attach**, 禁用 launch —— launch 会复位表、RAM 态清零)。

        没给 `gdb=` / 用户指定只做黑盒 ⇒ 返回 `None`, 且**一次都不去连**(不是连了再丢掉)。
        子项固定的那几个参数(`out=`/`watchdog=`/**`inject_allow=`**)走 `run_subitem(session_kw=…)`,
        段里只写"哪一步才需要会话"; 这一句之后 `ctx.bp()/trig()/inject()` 才有东西可取。
        ⚠ `inject_allow=` 必须在**会话构造时**点名(白名单精确匹配符号名, 事后补不了)。
        """
        return self._run.open_session(**kw)

    def trig(self):
        """`trig` 回调 —— 递给库动词的 `trig=` 那个参数。

        有会话 = 带断点读; **没会话 = `None`**, 库动词据此自己直呼动作并摘净控制参数
        (`timeout`/`_vars`/`_drop` 那些)。
        """
        g = self._run.session_obj
        return partial(self._run.gdb.trigger, g) if g is not None else None

    def trigger(self):
        """**自己要触发**一次动作时用它(而不是把回调递给库)。

        与 `trig()` 的分别只有一个: **没会话时它不是 `None`, 而是照样能调** ——
        `GD.trigger(None, …)` 会直接调动作、把控制参数摘净, 于是"这一次发帧"在黑盒观测下
        与有会话时走同一句代码。`trig()` 是给库判"有没有白盒通路"的, 少会话就该是 `None`;
        本方法要的是"黑盒观测不能跟着消失"(CLAUDE.md「两种观测」)。
        没给 `gdb=`(纯黑盒脚本)时才返 `None` —— 那时连 `GD` 都不在, 没得调。
        """
        if self._run.gdb is None:
            return None
        return partial(self._run.gdb.trigger, self._run.session_obj)

    def inject(self):
        """注入回调 —— 与 `trig()` 成对; 没会话时 `None`(注入**没有**可降级的黑盒替身)。"""
        g = self._run.session_obj
        return partial(self._run.gdb.inject_hit, g) if g is not None else None

    def with_inject(self):
        """`Session.with_inject` 的偏函数形状(自己给注入点、自己写值的那一路)。

        与 `inject()` 的分别只在**调用的原语不同**(`inject_hit` 是"给一次触发配一张注入表",
        `with_inject` 是"停在下断点处写一批值再等命中"), 没会话时两者一样是 `None` ——
        注入没有可降级的黑盒替身。

        ⚠ 取的是 `gdb.Session.with_inject` 这个**未绑定方法**(模块上**没有** `GD.with_inject`)。
        写成 `GD.with_inject(…)` 会在**调用期**炸 `AttributeError`, 而离线 `ast.parse` 与字符串式
        自检全绿也看不见 —— 1-3 首跑就是这么炸的, 故把这个形状收进外壳一份。
        """
        g = self._run.session_obj
        if g is None or self._run.gdb is None:
            return None
        return partial(self._run.gdb.Session.with_inject, g)

    def bp(self, anchor):
        """**现在就把断点挂上**, 返回 bpno; 没会话 ⇒ `None`。

        给"断点要跨几次复用"的用法(`wait_hit` 等一次 / `fire_hit` 触发一次 / 一个 bpno 用两回)。
        ⚠ 下不上时 `gdb.break_at` **抛** `GdbError` 不返回 None。
        ⚠ **高频行不要用这个** —— 预先挂上、又没人等在等命中时, 它自己会把核撂停, 其后每条串口帧
          整帧无应答, 表象与"串口坏了/表死机"一模一样。那种断点走 `anchor()`。

        `anchor` 收的是**一个**元组(脚本里那个模块级字面量 `BP_<X>`), 交给
        `breakpoint.to_bpno` 解 —— 收哪几种写法、各自怎么解, 见那一处(`swdbg/breakpoint.py`)。

        ⚠ 原先签名写成 `bp(self, *anchor)` 而调用点一律传单个元组 ⇒ 实参成了 `(("f.c", 120),)`,
        拆出去只有两个参数、`line` 缺位, **在调用期撞 TypeError**。**离线静态检查抓不到它**
        (原话: 当时那个干跑台一律带 `--no-gdb` ⇒ `session_obj is None` ⇒ 这一行根本不执行;
        与 4-6 的 `billday=d0` 同一类: 只在真开会话那条路上炸)。2026-09-16 由 1-2 真跑当场挡下。
        ⚠ 2026-09-18: 那条干跑台(`scripts/_regress.py`)已删。**这类错现在只有真跑能挡** ——
        改本函数签名时, 自己把"真开会话"那条路在脑子里过一遍。
        """
        g = self._run.session_obj
        # ⚠ 递给 `to_bpno` 的是**整个** `anchor`, 不许写成 `*anchor` —— 锚点四元组拆开就成了
        #   三个位置参数, 当场撞 TypeError; 而这条只在真开会话那条路上执行, 离线查不出来。
        return self._run.gdb.to_bpno(g, anchor)[0] if g is not None else None

    def anchor(self, anchor):
        """把模块级字面量断点 `(文件, 行号)` 递给库动词的 `bp=` —— **原样传, 不下断点**。

        与 `bp()` 的分野是承重的, 两个都写"断点"而做的事相反:
          · `bp()`     —— 现在挂上, 返回 bpno(给 `wait_hit`/`fire_hit` 那种"断点已挂好"的用法);
          · `anchor()` —— **不挂**, 把 `(文件, 行号)` 原样交给 `with_trigger` 家族, 由库
                          **当场挂、用完必撤**(命中与未命中两条路都撤)。

        **高频行(每个计量帧 / 每分钟都会过的行)必须走这一条**: 预先挂上而没人等在等命中时, 它自己
        把核撂停 ⇒ 其后每条串口帧整帧无应答, 与"串口坏了/表死机"现象一模一样
        (`breakpoint.Session.with_trigger` 的文档把这条写得很清楚; 1-2 的 `kWhData.c:448` 是那个
        反面样本, 2026-09-11 首跑整场 RX(0))。

        用户指定只做黑盒(`--no-gdb`)时给 `None` —— 库据此走"本次无断点会话"那一支,
        **如实记「没做成」, 不冒充成"断点没命中"**。
        """
        return None if self._run.waived else anchor

    # ---- 收证据 ----
    def take(self, recs):
        """只要记录(没有 `why`/`scope` 的那种返回形状)。"""
        self.J.extend(recs or [])

    def hold(self, recs, why, scope, label):
        """库动词的**三件套**返回 `(recs, why, scope)` 一次收干净。

        `scope is None` = 库给的结构信号"这一支半途中止" ⇒ 把原因写进汇总。判据是 `is None`,
        **不是**去嗅 `why` 的文案 —— 文案一改那种分支会静默失效(与已废掉的 verdict 字符串嗅探同类)。
        """
        self.J.extend(recs or [])
        for line in (why or []):
            print("      · %s" % line)
        if scope is None:
            self.J.note("%s: 半途中止(这一次没做成) —— %s" % (label, "; ".join(why or [])))


class _Run(object):
    """整轮的可变部分(会话对象 / 串口 / 账本), 由 `run_subitem` 建、由 `Ctx` 用。"""

    def __init__(self, J, waived, gdb, session_kw=None):
        self.J = J
        self.waived = waived
        self.gdb = gdb
        self.ser = None
        self.session_obj = None
        self.session_kw = dict(session_kw or {})

    def open_session(self, **kw):
        if self.waived or self.gdb is None:
            return None
        if self.session_obj is None:
            _kw = dict(self.session_kw)
            _kw.update(kw)
            self.session_obj = self.gdb.open_or_none(**_kw)
        return self.session_obj


def run_subitem(title, criteria, *, name, parts, allow=("--no-gdb", "--observe"), known=(),
                obs=None, gdb=None, obs_waived_note="用户指定只做黑盒", banner=None, logdir=None,
                session_kw=None, cleanup=None):
    """跑完一个子项并返回**退出码**。

    · `title`      = 子项标题(进账本表头)
    · `criteria`   = **测试前定死**的预设条目(库导出的 `CB.<子项>_criteria`)
    · `name`       = 日志短名(`log/<name>_<日期>_<时刻>.log`)
    · `parts`       = `[(标签, fn(ctx)), …]` —— 一个子项的**全部**条目都在这份清单里, 一行一段
    · `gdb`        = `swdbg.breakpoint` 模块(不传 = 纯黑盒)。**由脚本传**不是洁癖: 本模块不许 import swdbg
    · `session_kw` = 开会话时那几样**子项固定**的参数(`out=`/`watchdog=`/`inject_allow=`), 段里
      只写"哪一步才需要会话"(`ctx.session()`), 不重复抄这些
    · `cleanup`    = `[(标签, fn(ctx)), …]` —— **收尾段**(见下)
    · `allow`      = 放行的开关(未识别参数**拦死** —— 否则拼错的 `--dry` 会被 Python 静默忽略, 脚本照常真发帧)
    · `known`      = 放行的**带值**开关(`--vars a,b` 这种): 其后一个 token 当值放行。只登记 `allow`
      会把值当未识别参数误拦 —— 而"吵但安全"这一侧同样是错, 它让一个真开关用不了。两个集合都照实登记。
    · `obs`        = 本子项**实际有**哪几种观测(默认 `(JS.SERIAL, JS.DEBUG)`)。纯黑盒的脚本
      (全程不发断点、不读局部量)照实写 `obs=(JS.SERIAL,)`: 它没有断点观测这一项可"被排除",
      于是 `--no-gdb` 对它就是个**空动作**, 而账本不会凭空多出一条"这个观测被跳过了"。
    · `logdir`     = 日志落哪(默认 `log/`)。**只有测试脚本自己会改它**(把它指到临时目录),
      理由是: 试跑不是实测记录, 不许往 `log/` 里混(那儿是跑过的实况记录, 掺进去等于篡改记录)。
      ⚠ 2026-09-18: 原先会改它的那个离线干跑台(`scripts/_regress.py`)已删, 这条钉子的用处不减反增
      —— 现在没有哪一层替你兜住"混进 log/"这件事了。

    **收尾段**与普通段的分别只有一个: 它**无论前面是否抛异常都会跑**, 且跑在
    `g.close()` **之后**、`ser.close()` **之前**。那是"把表恢复原状的兜底"该在的位置 ——
    核心已放行(串口发得出去), 会话已关(要断点观测的收尾不属于这一档)。典型用途:
    4-6 的结算日兜底(链B 把结算日改成 alt, 写回那次没确认成功就得在这一档再写一次)。
    ⚠ 收尾段抛异常只打印(不吞成"没事", 也不让它顶掉本轮的结论)。

    `--no-gdb`  = **用户明确指定只做黑盒**(那是"本次范围"= 做全了, 不是"降级"= 没做完) ⇒ 有观测被
    排除 ⇒ 记账本时**不报"总"**; `--observe` = 观察期(降级照算照印, 退出码暂不动)。

    段里抛 `Stop` 即"这一轮到此为止": 收尾照跑, 账本与退出码照出。
    """
    ensure_utf8_stdout()
    guard_argv(sys.argv[1:], allow=allow, known=known)
    waived = "--no-gdb" in sys.argv
    unproven = None            # 非 None = 这一轮**没拿到实测凭据**(口没开成 / 表没应答探活帧)
    with runlog.run(name, logdir=logdir) as log_path:
        _obs = tuple(obs) if obs else (judge.SERIAL, judge.DEBUG)
        # 只有"本子项本来就有断点观测"时, `--no-gdb` 才排除得掉一样东西。
        _drop_debug = waived and judge.DEBUG in _obs
        J = Judge(title, criteria(),
                  obs=(tuple(o for o in _obs if o != judge.DEBUG) if _drop_debug else _obs),
                  skipped=[(judge.DEBUG, obs_waived_note)] if _drop_debug else [],
                  phase=judge.PHASE_OBSERVE if "--observe" in sys.argv else judge.PHASE_ENFORCE)
        run = _Run(J, waived, gdb, session_kw=session_kw)
        if banner:
            print(banner)
        try:
            run.ser = _open_serial()
        except Exception as exc:
            # 口都开不出来 ⇒ 这一轮**没有表可跑**。段一行都不跑, 记录也不许留在 `log/` 根下
            # (见本文件头「实测凭据」) —— 挪走发生在 `with` 之后(那时文件已关)。
            run.ser = None
            print(_serial_note(None))
            print("!! 串口没开成: %s" % exc)
            faultlog.record("SERIAL-PORT", subsystem="serial", text=str(exc),
                       next_step="查 USB-485 桥与表通电; 或核卡带的 COM 判据")
            unproven = "串口没开成: %s" % exc
        if run.ser is None:
            # 没有表可跑: 段一行都不跑, 账本照出(条目全部未证 ⇒ 未定论), 记录稍后挪出 `log/`。
            ctx = None
        else:
            print(_serial_note(run.ser))
            pf = _live_proof()
            if not (pf and pf.get("probe")):
                # 口开着而**表没答探活帧**(或压根没问) —— "桥插着"不等于"口后面是那块表"。
                unproven = "表没应答探活帧(%s)" % ((pf or {}).get("probe_note") or "没开过口")
            _bench("开始", run.ser, None)
            ctx = Ctx(run)
            try:
                for _label, fn in parts:
                    try:
                        fn(ctx)
                    except Stop as e:
                        print("      · 本轮到此为止: %s" % e)
                        break
            finally:
                # ⚠ **次序承重**: 会话先关(放行核心 + 复核 DHCSR), 降级再进账本(收尾本身也会添降级),
                #   收尾段在串口关闭之前跑(兜底要发帧, 而核心这时已放行), 串口最后关 ——
                #   核心停着时串口一条帧都发不出去, 而现象与"表坏了"一样。
                g = run.session_obj
                if g is not None:
                    g.close()
                    J.feed_degradations(g)
                for _label, _fn in (cleanup or []):
                    try:
                        _fn(ctx)
                    except Exception as exc:
                        # 收尾段出错不许顶掉本轮的结论, 也不许静默: 那一行"表可能没恢复原状"必须看得见。
                        print("      · 收尾段「%s」抛了异常: %s: %s —— 表可能没恢复原状, 请人工核"
                              % (_label, type(exc).__name__, exc))
                run.ser.close()
                _bench("结束", run.ser, g)
        # 汇总在 `finally` **之外**: 提前结束的那条路(段抛 Stop / 口没开成)也要带上结论。
        for line in J.summary():
            print(line)
        print("== %s 退出码: %d  (总: %s) ==" % (name, J.exit_code(), J.status()))
        code = J.exit_code()
    # `with` 之后: 两个文件都关了, 这时才挪得动。没拿到实测凭据的一律挪出 `log/` 根 ——
    # 那个名字空间只装"口后面确实是本表"的跑次(判据在 `common.portsel.live_proof`)。
    if unproven is not None:
        print("!! 这一轮**没拿到实测凭据**: %s" % unproven)
        print("   记录已挪出 `log/`(去 `log/未实测/`), 它不是实测记录, 不许拿它回填实测日志。")
        print("LOG 实际落点: %s" % runlog.quarantine(log_path, unproven))
    else:
        print("LOG 已存: %s" % log_path)
    return code


def _open_serial():
    """开串口。**在函数里 import** —— 本模块是外壳, 不在 import 期把传输层拽进来
    (`trial` 被每支 `_test_*.py` 调; 函数内 import 让依赖图单向、可读)。"""
    from common.portsel import open_com
    return open_com()


def _live_proof():
    """这一次开串口**当场从设备上读到的**凭据(桥指纹 + 探活那帧表答没答) → dict 或 None。

    凭据的本体与唯一写入口都在 `common.portsel`(它开的口, 它登记) —— 这里只取, 不重算。"""
    from common.portsel import live_proof
    return live_proof()


def _bench(phase, ser, g):
    """实跑的头尾各记一条 `kind="bench"`: **那一刻链路是什么状态**。

    为什么非有: 「半小时前还能跑, 中间没人碰过硬件」这句话原先只能靠嘴说 —— 2026-09-20 白盒
    11:35:37 还能跑, 到 11:42:46 就全线连不上, 而事后的 jsonl 里没有任何一条记录写着当时通不通。

    只记**这一刻手上真有的**事实(串口名、是不是真串口、会话开没开、走的是哪一支探针)。探针侧
    的事实要从会话对象上取, 会话没开过就如实写"没开过" —— **不编**: 编出来的现场比没有现场更坏,
    因为它看着像证据。
    """
    port = getattr(ser, "port", None)
    fields = {"阶段": phase, "串口": port, "真串口": bool(port), "会话": "没开过"}
    if g is not None:
        fields["会话"] = "%s:%s" % (getattr(g, "backend", "?"),
                                   getattr(g, "probe_ident", getattr(g, "serial_no", "?")))
        fields["会话端口"] = getattr(g, "port", None)
    faultlog.bench(**fields)


def _serial_note(ser):
    """给日志留一行"这一轮开的是什么串口"。**判据是对象本身, 不是某一句话**。

    为什么非要这一行: 干跑(假串口)与真跑写出来的日志,**从内容上分不开** —— 干跑时对端一个字
    都不回, 真跑遇到核心被 halt 时也一个字都不回, 两者的 `RX(0)` 一模一样。2026-09-16 真发生过:
    干跑的日志目录钉漏一处, 9 份干跑记录落进了 `log/`(那是跑过的实况记录), 而它们与一次真跑的
    记录长得一样 —— 照它回填实测日志就等于**伪造一条实测**。

    这一行把"开的是不是真串口"变成**日志里可读的证据**: 真串口报口名(pyserial 的 `.port`,
    ctypes 直连的 `RawCom` 也记), 替身没有口名 ⇒ 自报"这不是真串口"。`scripts/_backfill.py`
    据此**拒收**这类日志, 不靠谁记得别去回填它。

    2026-09-22 起这一行还带**当场从设备读出来的**凭据(桥的 VID:PID 与 USB 序列号、探活那帧
    表答没答 —— 它由 `common.portsel` 登记, 字形由 `portsel.proof_line` 出, 这里只拼) ——
    因为"真串口"三个字原先**是自述, 谁都能写**。判据与成色见 `common.runlog.real_run_of`
    与 `proof_level`。"""
    from common.portsel import proof_line
    cls = type(ser).__name__
    port = getattr(ser, "port", None)
    if not port:
        return ("串口: **%s** —— 这不是真串口, 这一份日志**不是一次实测记录**(假串口/替身/"
                "口没开成), 别拿它回填实测日志" % ("没开成" if ser is None else cls))
    pl = proof_line()
    return ("串口: %s (%s) —— 真串口 | %s" % (port, cls, pl) if pl else
            "串口: %s (%s) —— 真串口 | 探活: **没做**(拿不到实测凭据) ⇒ 这一份日志不算实测记录"
            % (port, cls))


