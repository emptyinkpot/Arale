# -*- coding: utf-8 -*-
"""common/judge.py —— 判据汇总: 预设条目 × 证据 → 满足计数 → 三态结论

**「这次测试到底证明了什么」的唯一出口。** 与哪块表、哪个协议、哪条子项都无关,
所以住在中立层: meterlib 的语义动词与 swdbg 的断点取证都往这里递**同一种记录**,
`project/tests/_test_*.py` 只在末尾把它打出来 + 拿退出码。

两条地基(改这个文件前先读)

一、**结论只对「预设条目」计数**。预设条目 = 测试**开始之前**就定死的判据清单(规格行
    「观察与判据」里 `判过:` 后面那几条), 抄成 `criteria = {"②": "…"}` 传进来。
    测完回头再总结出来的东西不算条目 —— 那样"满足了几条"就成了自己给自己出题。
    一个规格编号若其实断了两件事(如"切套了 **且** 备套内容=原套"), **拆成 `②a`/`②b`
    两条**, 各自独立计 —— 不拆的话, 半条证到就只能记"满足", 或者为了诚实整条记"未证",
    两种都不对。
    **证不到的条目照样列进来**: 列了才显示成「未证」, 不列就是悄悄把它从账上抹掉。已知本台
    证不了的, 条目写成 `{"text": …, "unprovable": "为什么/要证还得做什么"}` —— 结论里会把这句
    一并印出来(仍然记「未证」, 不是豁免)。

二、**一条证据要计入判据, 必须答得出「什么样的固件会让它 FAIL」**(`falsify=`)。
    这是抗假通过的**唯一**一条通用准则 —— 不是靠人自觉, 而是问一句比问十句管用:
      · "读得回" / "值合法" / "没报错" / "记录变多了" 这类**任何能跑的固件都成立**的检查,
        答不出 falsify, 于是它**不计入任何判据**(只留在日志里当参考, 不影响结论);
      · 答得出来的(例:"到点却没切 → g_*SwNo 仍是待切态")才是证据。
    ⚠ `falsify` 是**声明**, 机器验不了那句话的真假 —— 它逼作者写出一句能被旁人当场看破的
      坏固件形态。这是它能做到的极限, 别把它当自动化保证。

证据记录(本模块与 meterlib/swdbg 之间的**唯一契约**, 双方都按这个形状造, 见 `rec`)

    ok=True   —— 这条检查达成
    ok=False  —— **观察到了不对**(任何一条 False ⇒ 整项「失败」, 不论它挂在哪条判据上)
    ok=None   —— **没做成 / 没观察到**(没命中、没会话、读不到) —— **不是 FAIL**。
                 它不能支持任何判据(支撑不了就记"未证"), 但也不冤枉固件。

三态(不许多一个)与退出码

    "通过"         —— 预设条目**全部满足**, 且本次在范围内的观测都做成了
    "失败"         —— 有 ok=False 的证据
    "未定论"       —— 其余(条目未证 / 本次打算做的观测没做成)
    "不构成总结论" —— **有观测被明确排除在本次范围外**(如用户指定只做一套)。这不是"打了折的
                      成功", 而是**根本没有「总」这个量**: 不打总结论那行, 退出码只反映
                      "本次范围做成了没有"。
    ⇒ 只有前三个是结论; "不构成总结论"是"本次不产出结论"。

三、**触发通道与"观测"是两根正交的轴, 都要记**。`obs` 说的是"在哪条通道上**观察**"(串口/断点),
    `trig` 说的是"那个被观察的状态**是谁造出来的**"。默认是帧(645/698 发出去)⇒ 不标注;
    只有 `TRIG_INJECT`(调试器把值注进去)才标 —— 因为这类证据**不复现就看不出来**: 读到同一个
    值, 可能是固件自己算的, 也可能是我们刚改进去的。汇总里因此会多一行「触发通道: …」并把
    牵涉注入的条目逐条点出。**(2026-09-11 用户定: 判据要能说明"这条是靠注入触发的"。)**

四、**降级(degradation)必须能改变结论, 否则它就等于没发生。**(2026-09-11 加, 本仓最大的一个洞)

    "降级"= **台面没提供的、本来该有的能力** —— 喂狗钩子没装上、残留 FPB 没清掉、断点槽取不到。
    它与"证据 ok=None"是同族, 但**发生的位置不同**: 降级在**会话层**(`swdbg`), 早于任何一条判据;
    ok=None 在**证据层**。原先降级只走 `breakpoint.Session._warn` —— **只打印**, 于是:

        `log/` 里 100+ 份日志带着「喂狗钩子没装上」, 而账本、退出码里**一个字都没有**;
        一整轮"没喂到狗"的取证, 退出码与"狗喂得好好的那轮"**完全一样**。

    这不是"忘了判断", 是**降级通道根本没有接进判据**。所以:

    · `_warn(…, what=…)` 填了 `what` ⇒ 记进 `Session.degradations`(结构化, 见 `degradation()`);
      不填 ⇒ 只是**提醒**(台面发生了什么、且**做成了**), 记进 `Session.warnings`, **不影响结论**。
      1048 那条"残留 FPB **已清零**"属后者 —— 它当时也打 `[gdb] !!`, 被日志盘点一并算成降级, 是**过计**。
    · 脚本把 `Session.degradations` 交给 `Judge`(`J.feed_degradations(sess)`)。
    · **有降级 ⇒ 整项不许报「通过」**: 降成「未定论」(退出码 2), 理由里逐条点名。
      理由: 台面缺能力时取到的证据,**证不出"固件不对", 但也证不出"固件对"** —— 报绿就是拿
      "没喂到狗的那一轮"冒充证据。⚠ 它**只降 PASS**, 不动「失败」(有 FAIL 就是有 FAIL)与
      「不构成总结论」(本次范围之外的事, 见上)。

    **观察期 / 生效期**(本仓判据升级的统一两段, 不这么走的一律算未完成):
    `PHASE_OBSERVE` —— 降级照算、照印(`新=未定论`), **退出码先不动**; 人过一遍"由绿转灰"清单,
    逐条确认是**真的没证据**、不是判据写错。`PHASE_ENFORCE` —— 摘掉旧值, 退出码按新结论走。
    一次性切换的后果是一轮跑完大片由绿转灰, **人分不清"改对了"还是"改崩了"**。

**不许打分/百分比/覆盖率/置信度。** 这类东西一旦出现, 下一步就是拿它凑数。能说的只有
"哪几条满足、哪几条未证、未证的缺什么"。
"""

# 第二路输出(机器读的事件流, 见 `events` 模块头): 本模块**只发一条**事件 —— `verdict`。
# 方向是 judge → events, **不能反过来**: events 只 import 标准库、不 import 本模块, 所以不成环
# (与 `runlog` 那边同一条规矩)。
# 本模块顶部原先一个 import 都没有(要用的全在函数里 import), 这一条为什么破例放顶部:
# 它是「本次结论」这一路机器的**写出口**, 而本模块的存在理由就是"结论只有一个出口" ——
# 埋进函数体里只会让"结论还从哪儿出去过"变得要翻代码才知道。
from common import events

SERIAL = "串口"
DEBUG = "断点"

# ---- 触发通道(与"观测"正交的第二根轴) ----
# 观测说的是"**在哪条通道上观察**"(串口/断点), 触发说的是"**那个被观察的状态是谁造出来的**"。
# 绝大多数证据的触发源是帧(645/698 发出去), 那是默认, **不标注**; 只有"靠注入造出来"的才标,
# 因为它是**唯一不复现就看不出来**的那一类: 读到同样的值, 可能是固件自己算出来的, 也可能是
# 我们刚用调试器改进去的 —— J 列回填与将来复核都必须能一眼分清(2026-09-11 用户定)。
TRIG_INJECT = "注入"

STATUS_PASS = "通过"
STATUS_FAIL = "失败"
STATUS_TBD = "未定论"
STATUS_NO_TOTAL = "不构成总结论"

# 判过→退出码: 与 `meterlib.cmd_bank.verdict_exit` 同一套口径(PASS*→0 / FAIL*→1 / 其余→2),
# 只是这边的输入是记录而不是 verdict 字符串。"不构成总结论"→0: 本次范围之内做成了。
EXIT = {STATUS_PASS: 0, STATUS_FAIL: 1, STATUS_TBD: 2, STATUS_NO_TOTAL: 0}

# ---- 判据升级的两段(见模块头 四) ----
PHASE_OBSERVE = "观察期"     # 新判据照算照印, **退出码先不动**
PHASE_ENFORCE = "生效期"     # 退出码按新结论走
PHASE_DEFAULT = PHASE_ENFORCE


def degradation(what, detail=""):
    """造一条**降级**记录 —— 与 `rec()` 平行, 但说的是"台面缺了什么", 不是"观察到什么"。

    ⚠ 它与证据记录**不是一回事, 不许混进 records**: 一条降级不认领任何判据(它没有 falsify 可言,
    固件的任何形态都不会让它 FAIL)。它只在汇总层**把整项的 PASS 压成「未定论」**(见模块头 四)。

    `swdbg.breakpoint.Session` 按其自身形状造(`{"what","detail"}`), 本函数只是把这个形状的**定义处**
    收到中立层来 —— 两边各写各的 dict 字面量, 早晚会有一边改名而另一边不响。
    """
    return {"what": str(what), "detail": str(detail or what)}


def tri_eq(*pairs):
    """三态**逐项相等**: `tri_eq((值1, 期望1), (值2, 期望2), …)` → `True` / `False` / `None`。

    **任一个"值"是 `None` ⇒ 整个回 `None`** —— 不是 `False`。

    **为什么要有这个函数**(2026-09-11 立, 因为同一个错犯了三次):
    本仓"读不出来"一律回 `None`(见 `meterlib.cmd_bank.rate_trace_decode` 的 "读不出回 None 而不是 0
    —— 免与『真值就是 0』混")。但拿 `None` 去 `==` 会**当场变 `False`**, 于是
    **一次读取失败被记成"固件的值不对"**, 报出来还带着编好的理由。三次现场:
      · `rate_fallback_evidence` 的 ④b 判读 —— 镜像读回 `None`, 记成"越界值被留下了(守卫没拦)",
        三分钟后同一实验读出真值并通过(该次"失败"是假的);
      · `rate_attribution_evidence` 的逐段归属(判据②)—— 某一拍 `g_RateNo` 没读回来,
        记成"错位/错费率";
      · `bill_freeze_evidence` —— 序号/时标没读回来, 记成"序号不对/时标日不对"。
    **手写 `and` 链一定会再犯**, 所以把它收成一个**有名字**的东西: 判"读回来的值对不对"就过它。

    ⚠ 注意 `tri_eq` 的两边不同权: 只有**左边(实测值)** 为 `None` 才回 `None`;
    右边(期望值)为 `None` 是**调用方的编程错**, 照 `False` 处理(那种红是要人去看的)。
    """
    if any(got is None for got, _ in pairs):
        return None
    return all(got == want for got, want in pairs)


def tri_all(rows):
    """三态**汇总**一行行的判读: `[True/False/None, …]` → `True` / `False` / `None`。

    · 任一 `False`          ⇒ `False`(观察到了不对 —— 那才是固件错)
    · 无 False, 但空表/有 None ⇒ `None`(**"全都对"证不了**; 空表更不是"通过")
    · 全是 `True`            ⇒ `True`

    **为什么要有它**: `bool(rows) and all(rows)` 这个写法看着对, 实际上两个边界都错 ——
    空表判 FAIL、有项没读到也判 FAIL, 两者都把"没做成"说成了"固件不对"。与 `tri_eq` 同一个病。
    """
    if any(x is False for x in rows):
        return False
    if not rows or any(x is None for x in rows):
        return None
    return True


def crit_text(v):
    """条目 → 一句话。条目可以是纯文本, 也可以是 `{"text": …, "unprovable": …}`(见 `crit_unprovable`)。"""
    return v if isinstance(v, str) else str(v.get("text") or "")


def crit_unprovable(v):
    """条目 → **本台声明不可证的理由**(没有声明 → None)。

    为什么要有它: 条目没人认领时, 机器分不清"忘了测"与"知道测不了"。声明过的条目报
    "本台不可证: <理由>" —— 把"要证还得做什么"摆在结论里, 而不是让人看见一句没头没脑的
    "没有证据认领它"。**声明不等于豁免**: 它照记「未证」, 整项照样是「未定论」。
    """
    return None if isinstance(v, str) else (v.get("unprovable") or None)


def rec(name, ok, detail="", crit=None, obs=SERIAL, falsify=None, trig=None):
    """造一条证据记录 —— **本模块与 meterlib/swdbg 之间的唯一契约**。

    name    : 一行短标签(打印用; 不要在这里重复判据号/观测名 —— 那些有专门的字段)
    ok      : True 达成 / False 观察到不对 / None 没做成(见模块头"证据记录")
    detail  : 这条的读数与期望(给人复核用)
    crit    : 认领哪条预设条目(键名如 "②a"); None = 参考证据, 不计入任何条目。
              **一次观察同时说明两件事**时给元组 `("②a", "⑤a")` —— 比分两条记录诚实:
              那本来就是同一个读数, 拆成两条会让人以为取过两次证。
    obs     : 哪种观测(本模块只认 SERIAL/DEBUG 两个字符串, 不解释含义)
    falsify : 「什么样的固件会让它 FAIL」—— 答不出就别填, 于是它不计入条目
    trig    : **触发通道**(见模块头 TRIG_INJECT)。`None` = 帧/常规路径, **不标注**;
              只有"这个状态是注入造出来的"才填 `TRIG_INJECT` —— 断言里看得见
              `[断点/注入]`, 于是"读到值"与"值是谁造的"不会被混为一谈。
    """
    return {"name": name, "ok": ok, "detail": detail, "crit": crit,
            "obs": obs, "falsify": falsify, "trig": trig}


def obs_tag(r):
    """记录 → 取证前缀(`观测` 或 `观测/触发通道`)。注入造出来的状态**必须**在这里显形。"""
    return "%s/%s" % (r.get("obs"), r["trig"]) if r.get("trig") else str(r.get("obs"))


def injected(records):
    """本次有哪些证据是靠**注入**造出状态才取到的(→ list)。"""
    return [r for r in records if r.get("trig")]


def claims(r):
    """记录认领的条目号 → 元组(没认领 → 空元组)。`crit` 可写单个键或元组。"""
    c = r.get("crit")
    if not c:
        return ()
    return tuple(c) if isinstance(c, (tuple, list, set)) else (c,)


def is_evidence(r):
    """这条记录**算不算证据** —— 唯一判据就是它答没答出 falsify。"""
    return bool(r.get("falsify"))


def crit_states(criteria, records):
    """逐条预设条目 → [{no,text,state,why,ev,weak}]。state ∈ 满足/失败/未证。

    满足 ⟺ 至少一条**算证据**的认领记录 ok=True, 且没有一条 ok=False。
    失败 ⟺ 有认领记录 ok=False。
    未证 ⟺ 其余(没人认领 / 认领的全是弱证据或 ok=None)。
    """
    out = []
    for no, text in criteria.items():
        mine = [r for r in records if no in claims(r)]
        strong = [r for r in mine if is_evidence(r)]
        weak = [r for r in mine if not is_evidence(r)]
        bad = [r for r in mine if r.get("ok") is False]
        good = [r for r in strong if r.get("ok") is True]
        if bad:
            state = STATUS_FAIL
            why = "认领它的证据里有 FAIL: %s" % "; ".join(
                "%s(%s)" % (r["name"], r["detail"]) for r in bad)
        elif good:
            state = "满足"
            why = "证据: %s" % "; ".join("[%s] %s —— %s" % (obs_tag(r), r["name"], r["detail"] or "达成")
                                        for r in good)
        else:
            state = "未证"
            un = crit_unprovable(text)
            if un:
                why = "本台不可证(条目里已声明): %s(%d 条弱证据留作参考)"
                why = why % (un, len(weak))
            elif not mine:
                why = "没有证据认领它"
            elif not strong:
                why = ("认领它的 %d 条都答不出『什么固件会让它 FAIL』⇒ 任何能跑的固件下都成立, "
                       "不算证据: %s" % (len(weak), "; ".join(r["name"] for r in weak)))
            else:
                why = "认领它的证据没做成(ok=None): %s" % "; ".join(
                    "%s(%s)" % (r["name"], r["detail"]) for r in strong)
        out.append({"no": no, "text": crit_text(text), "state": state, "why": why,
                    "ev": strong, "weak": weak})
    return out


def obs_states(obs, records):
    """逐种观测 → [{obs,state,n_ok,n}]。state ∈ PASS / FAIL / 未做。

    未做 = 这种观测**一条 ok=True 都没有**(没记录、或全没观察到) —— 与 FAIL 分开:
    "没做成"不该被读成"固件不对"。
    """
    out = []
    for obs in obs:
        mine = [r for r in records if r.get("obs") == obs]
        n_ok = sum(1 for r in mine if r.get("ok") is True)
        n_fail = sum(1 for r in mine if r.get("ok") is False)
        if n_fail:
            state, why = STATUS_FAIL, "%d 条 FAIL" % n_fail
        elif n_ok:
            state, why = "PASS", ""
        else:
            state, why = "未做", ("没有证据" if not mine else "有记录但一条都没达成")
        out.append({"obs": obs, "state": state, "n_ok": n_ok, "n": len(mine), "why": why})
    return out


def orphans(criteria, records):
    """认领了**预设里没有的**条目的记录 —— 脚本与库对不上号时会这样, 必须显形。"""
    return [r for r in records if any(c not in criteria for c in claims(r))]


def decide(criteria, records, obs, skipped=(), degradations=()):
    """→ (status, reason, crit_states, obs_states)。判定顺序即优先级, 见模块头"三态"。

    `degradations` 是**会话层的降级**(见模块头 四): 它**只在最后**把「通过」压成「未定论」——
    排在所有既有优先级之后, 因为"有 FAIL"是更强的结论(固件确实不对), 不该被台面问题盖住。
    """
    cs = crit_states(criteria, records)
    ls = obs_states(obs, records)
    n_ok, n = sum(1 for c in cs if c["state"] == "满足"), len(cs)
    cnt = "判据: 满足 %d/%d" % (n_ok, n)
    bad = [r for r in records if r.get("ok") is False]
    if bad:
        return (STATUS_FAIL, "%s; 有 %d 条 FAIL(首条: %s —— %s)"
                % (cnt, len(bad), bad[0]["name"], bad[0]["detail"]), cs, ls)
    if skipped:
        tail = "本次不做的观测: %s" % "、".join("%s(%s)" % (l, w) for l, w in skipped)
        if all(l["state"] == "PASS" for l in ls):
            return (STATUS_NO_TOTAL, "%s; %s —— 本次范围之内的观测都做成了" % (tail, cnt), cs, ls)
        # **范围里有观测没做成时不许报"不构成总结论"就收摊**: 那会连"本次范围做成了没有"都答不清,
        # 退出码还会是 0(本次范围 != 做成了)。降成「未定论」, 由 reason 说清两件事。
        return (STATUS_TBD, "%s; 且**本次范围里**也有观测没做成; %s" % (tail, cnt), cs, ls)
    idle = [l for l in ls if l["state"] != "PASS"]
    if idle:
        return (STATUS_TBD, "打算做的观测没做成: %s; %s"
                % ("、".join("%s(%s)" % (l["obs"], l["why"]) for l in idle), cnt), cs, ls)
    if n_ok == n:
        if degradations:
            # 判据全满足, 但台面缺了能力 ⇒ **不许报「通过」**(模块头 四)。
            # 只压 PASS: 走到这里说明既没有 FAIL, 也没有被排除在外的观测。
            return (STATUS_TBD, "%s —— 预设条目全部满足, 但**本轮的台面是降级的**("
                    "%d 条, 见下), 取到的证据支撑不了「通过」: %s"
                    % (cnt, len(degradations),
                       "、".join(str(d.get("what")) for d in degradations)), cs, ls)
        return (STATUS_PASS, "%s —— 预设条目全部满足" % cnt, cs, ls)
    tbd = [c for c in cs if c["state"] != "满足"]
    return (STATUS_TBD, "%s; 未证: %s" % (cnt, "、".join(c["no"] for c in tbd)), cs, ls)


def render(title, criteria, records, obs, skipped=(), notes=(), degradations=(),
           phase=PHASE_DEFAULT):
    """→ 给人读的汇总行(list[str])。**"总"那行只在两种观测都在范围内时才出现**(用户 2026-09-10 定:
    只做一种观测时不报"总成功" —— 那种情况下根本没有"总"这个量)。

    `phase=PHASE_OBSERVE` ⇒ 照印新结论, 但**多打一行**把"旧=X 新=Y"并列出来(见模块头 四)。
    判据升级必须先"变红"再"变绿": 人要能一眼看出这一轮**哪些结论因为降级变了**, 而不是
    等退出码突然从 0 变成 2 才发现。
    """
    status, reason, cs, ls = decide(criteria, records, obs, skipped, degradations)
    legacy = decide(criteria, records, obs, skipped)[0] if degradations else status
    mark = {"满足": "[满足]", STATUS_FAIL: "[失败]", "未证": "[未证]"}
    out = ["== %s | 本次范围: %s ==" % (title, "+".join(obs) or "(无)")]
    for t in notes:
        out.append("   [注] %s" % t)
    inj = injected(records)
    if inj:
        # 触发通道显形: 注入造出来的状态**不是固件自己产生的** —— 读到同样的值, 将来复核必须知道
        # 它是怎么来的。只在这一行里点出, 不动结论(结论只认预设条目, 见模块头)。
        out.append("   [注] 触发通道: %d 条证据靠「%s」造出被观察的状态(其余走帧): %s"
                   % (len(inj), TRIG_INJECT, "、".join(r["name"] for r in inj)))
        for c in cs:
            if any(r.get("trig") for r in c["ev"]):
                out.append("        └ %s 的达成**牵涉注入**(复核时须照同一造法复现)" % c["no"])
    for c in cs:
        out.append("   %s %s %s" % (mark[c["state"]], c["no"], c["text"]))
        out.append("          └ %s" % c["why"])
    for r in orphans(criteria, records):
        out.append("   [!] 证据「%s」认领的 crit=%r 不在预设条目里 → 不计入任何条目"
                   % (r["name"], r["crit"]))
    for r in [r for r in records if claims(r) and not is_evidence(r)]:
        out.append("   [–] 证据「%s」认领 %s 但没写 falsify → 不计入(见 judge 模块头 二)"
                   % (r["name"], "/".join(claims(r))))
    out.append("   观测: %s" % " | ".join(
        "%s %s(%d/%d)" % (l["obs"], l["state"], l["n_ok"], l["n"]) for l in ls))
    for obs, why in skipped:
        out.append("   观测: %s **本次不做**(%s)" % (obs, why))
    if degradations:
        # 降级必须**看得见**才谈得上"改变结论"(模块头 四)。逐条印出来, 一条不糊。
        out.append("   [降级] 本轮台面缺了 %d 项能力(这些**不是**证据, 不认领任何判据):"
                   % len(degradations))
        for d in degradations:
            out.append("          · %s —— %s" % (d.get("what"), d.get("detail")))
        if phase == PHASE_OBSERVE:
            # 观察期: 结论照新算、照印, 退出码先不动。这行是人过"由绿转灰"清单的依据。
            out.append("   [降级] **观察期**: 旧=%s 新=%s(退出码暂按旧值 %d; 生效期后为 %d)"
                       % (legacy, status, EXIT[legacy], EXIT[status]))
        else:
            out.append("   [降级] 生效期: 有降级 ⇒ 不报「通过」(退出码 %d)" % EXIT[status])
    if status == STATUS_NO_TOTAL:
        # **只做一种观测时没有"总成功"可言**(用户 2026-09-10 定) ⇒ 这里不打"总:"那行。
        out.append("   ── 本次不构成总结论(%s) —— **不代表本子项证完**" % reason)
    else:
        out.append("   ── 总: %s(%s)" % (status, reason))
    return out


class Judge:
    """一面收集记录、最后算一次总账。脚本里一个子项一个。

        J = Judge("3-2 两套费率时段切换", CB.zone_slot_switch_criteria(),
                  obs=(JS.SERIAL, JS.DEBUG), skipped=[(JS.DEBUG, "用户指定只做黑盒")])
        J.add("写回==所写", ok, "读到 %s" % got, crit="②a", falsify="…")
        J.extend(CB.disp_digit_scale(got))                 # 库动词返回的记录
        for line in J.summary(): print(line)
        return J.exit_code()
    """

    def __init__(self, title, criteria, obs=(SERIAL,), skipped=(), phase=PHASE_DEFAULT):
        self.title = title
        self.criteria = dict(criteria)
        self.obs = tuple(obs)
        self.skipped = list(skipped)
        self.records = []
        self.notes = []
        self.degradations = []      # 会话层的降级(见模块头 四) —— **不是**证据, 不进 records
        self.phase = phase
        self._verdict_sent = False  # 本次跑的 `verdict` 事件发过没有(见 `exit_code`: 一次跑只发一条)

    def note(self, text):
        """挂一条汇总注(如「本次中止: 表钟读不出」)—— **只印给人看, 不参与判定**(判定只看记录)。"""
        self.notes.append(text)
        return text

    def add(self, name, ok, detail="", crit=None, obs=SERIAL, falsify=None, trig=None):
        r = rec(name, ok, detail, crit=crit, obs=obs, falsify=falsify, trig=trig)
        self.records.append(r)
        return r

    def extend(self, records):
        """并入一批证据记录。**`None` 条目直接跳过**。

        为什么要有这条: 断点观测的两半(`GD.wait_hit` 等一次 / `GD.fire_hit` 触发一次)在"台面没接
        J-Link / 这一次没做成"时返回的就是 `None`。有了这条, 每个调用点都长成同一个样子
        `J.extend([GD.<hit>(…)])`, 不必各写一句 `if x is not None`, 更不必写成 `[...] or []`
        —— 那个写法是**静默错的**: `[None]` 是真值, `or []` 兜不住它, 空记录照进不误。

        ⚠ 跳过的只是"**没产出记录**", 不是"没做这一次"(那一次该不该发、发没发, 由调用方与库
        各自打印说明); 账本里该记的「未证」由条目三态给出, 不靠塞一条空记录顶。
        """
        self.records.extend(r for r in records if r is not None)

    def feed_degradations(self, sess):
        """把**调试会话的降级**接进账本 —— `J.feed_degradations(sess)`。

        传 `None`(没开会话 / 走 `--no-gdb`)是**合法的**, 不是"没接": 没开会话就没有会话层的降级,
        与"开了会话但降级了"是两回事。传进来的东西只需有 `.degradations`(见 `Session`)。

        ⚠ 为什么要有这条、以及它为什么改变结论: 见模块头 四 —— 原先是 `_warn` 只打印,
        100+ 份日志带着「喂狗钩子没装上」而账本里一个字都没有。
        """
        if sess is None:
            return 0
        for d in (getattr(sess, "degradations", None) or []):
            self.degradations.append(d)
        return len(self.degradations)

    def status(self):
        """本次结论(含降级的影响, 见模块头 四)。"""
        return decide(self.criteria, self.records, self.obs, self.skipped,
                      self.degradations)[0]

    def status_without_degradations(self):
        """**不算降级**的结论 —— 观察期打"旧=X"那一栏要用它(见模块头 四)。"""
        return decide(self.criteria, self.records, self.obs, self.skipped)[0]

    def exit_code(self):
        """→ 0/1/2。观察期按**旧**结论给码(判据升级的两段, 见模块头 四)。

        **本次跑唯一发 `verdict` 事件的地方**(见函数里那段注释)。
        """
        # 观察期按**旧**结论给码 ⇒ 那一路算的是"不带降级"的结论(见模块头 四)。这里自己把
        # "喂给判定的是哪些降级"挑出来、**只算一次 `decide`**: 事件里那几个字段必须与返回的码
        # **同源**。分两次算(先 `status()` 拿码、再 `decide()` 掏理由与计数)是同一个判定被实现两遍,
        # 哪天优先级改了一处、另一处不响 —— 那正是本仓反复治理的那类错。
        dgs = [] if (self.phase == PHASE_OBSERVE and self.degradations) else self.degradations
        status, reason, cs, _ = decide(self.criteria, self.records, self.obs, self.skipped, dgs)
        code = EXIT[status]
        # ---- `verdict` 事件: 结论**已经定下来、且只走一次**的那一处(第二路输出, 见 events 模块头) ----
        # 为什么是这里而不是 `decide()`: `decide()` 是**纯函数**, 被 `status()` /
        # `status_without_degradations()` / `render()` 反复调用, emit 放进它一次跑要发好几条 ——
        # 而"一次跑恰好一条结论"正是这一路存在的理由。这里是脚本收尾取码的那一步: 到这儿
        # `phase` 与降级的影响都已经算进去了, 发出来的就是**返回的那个码对应的那个结论**。
        # ⚠ 为什么还要 `_verdict_sent` 兜一道(**别删**): `common.trial` 收尾**连调两次**
        #   `exit_code()`(它先打"退出码: N"那一行、再 return 同一个码), 于是"emit 放在这个函数里"
        #   并不等于"只走了一次"。这与本仓反复治理的"以为只走一次、实际走了两次"是同一类错,
        #   故用实例上的标志钉死, 不指望调用方只调一次。
        # 降级要报**两个**数, 不许只报一个(**2026-09-17 改的**, 原来只报参与判定的那个):
        #   `degradations`         = 本次跑**真有几处**降级 —— 这是"这轮干不干净"的唯一答案。
        #   `degradations_counted` = 其中喂进这次判定的条数(观察期按旧结论给码 ⇒ 0)。
        #   早先只报后者, 于是**观察期**跑出 `degradations: 0`, 而同时人读日志的 `[降级]` 块里
        #   明明躺着两条 —— 机器记录里出现一句平白的假话("这轮没降级"), 正是本次要治的那类病
        #   (见 events 模块头: 记成"什么都没发生"比不记更坏)。两个数都不与 status/exit_code 冲突:
        #   "码 0"的来历是 `phase` 这个字段说清的, 不是靠把降级条数抹成 0 来遮。
        if not self._verdict_sent:
            self._verdict_sent = True
            events.emit("verdict", title=self.title, status=status, exit_code=code, reason=reason,
                    phase=self.phase,
                    degradations=len(self.degradations),
                    degradations_counted=len(dgs),
                    counts={k: sum(1 for c in cs if c["state"] == k)
                            for k in ("满足", STATUS_FAIL, "未证")})
        return code

    def summary(self):
        return render(self.title, self.criteria, self.records, self.obs, self.skipped, self.notes,
                      self.degradations, self.phase)
