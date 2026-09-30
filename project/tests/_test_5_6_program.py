# -*- coding: utf-8 -*-
"""在证什么: 第1结算日成功写进去之后, 『编程』与『结算日编程』两条事件口各落一条; 写参那一刻固件真走到
  写库位置 Recd_Program, 且停住时读得到 b_PrgStart / pOper / pDIs(前者决定这一次是新增一条还是就地改写);
  退厂内后同一写帧被安全判定打回, 两条口的序号与时刻都不动。
会向表写什么: 645 0x14 把第1结算日先写成 d0±1 —— 同一上电周期内**写两次**, 跑完在 finally 里写回 d0 并读回验,
  表无净变; 本脚本自己发 645 进厂内 / 退厂内, 收尾不必再跑总复位。跑完表停在厂内态。
跑法: `python project/tests/_test_5_6_program.py`(真串口 + 真探针; `--no-gdb` 只做黑盒)。
结论怎么读: 账本末行「判据: 满足 n/N」, N=5; 退 0 = 通过 / 1 = 失败 / 2 = 未定论。
"""
from common import judge            # 观测种类常量(断点那几条证据标 DEBUG)
from common import trial            # 运行外壳: argv/日志/账本/串口/会话/收尾三连/退出码
from meterlib import cmd_bank       # 帧目录 + 语义动词积木箱(每步自带打印)
from project import CURRENT          # 本工程画像: 表号/.out 路径/喂狗点(库不认表, 只有脚本认)
from swdbg import breakpoint            # 断点级白盒(停核 + 读局部量); 没接 J-Link → open_or_none→None
from swdbg import gdbinit            # 整片 .out 地图: 开会话后解一次, 之后断点/注入点只查它

# ---- 第 1 步记下的记录口几何(解释第 5、6、7 步读回来的序号与条数) ----
REC_CAP = 10                 # 『编程』口容量(条)
REC_LEN = 50                 # 单条字节数 = 发生时刻 6 + 操作者 4 + 10 个标识各 4
REC_IDX_BASE = 0x4889        # 索引区首址(外部存储绝对地址)
                             #   ⚠ 头 3 字节是**不封顶**的总次数, 后 2 字节才是**封顶在 10** 的条数
OAD_PROG = "30120B0A"        # 事件 0x12『编程』
OAD_PROG_SUB = "301A0B0A"    # 事件 0x1A『结算日编程』(每写一次结算日必新增一条, 无合并语义)

# ---- 断点: 模块级**字面量元组** ---------------------------------------------------
# `scripts/_check_anchors.py` 用 `ast.literal_eval` 抠这两个名字, 逐条对源码核"要读的变量在断点
#   那一行赋过值没有"; 写成 `CB.SOMETHING` 那种表达式就抠不出来, 等于那种观测静默地没人核过。
#   落点是 `Recd_Program()` 里 `if (FALSE == b_PrgStart)` 那一句(:824 @0x1e416)。该行上
#   `b_PrgStart`/`pOper`/`pDIs` 可读(`info scope` 实测), 而**入口那一行(:821 @0x1e40e)是空洞**
#   —— 停在那儿一个量都读不出来, 别把断点挪回去。这一句本来就是"新建 / 改写"两支的分岔点,
#   所以一个断点同时给三样: 调用发生了(指令路径) + 走哪一支(b_PrgStart) + 入参是什么(pOper/pDIs)。
BP_REC = ("line", "TaskRecord.c", 824)
VARS_REC = ("b_PrgStart", "pOper", "pDIs")

WAIT = 3.0

# ---- 本子项的 falsify(纯数据, 逐条对着 crit 用)-------------------------------------
# `*命中` 那一条是**断点那一次**的 falsify(挂在写参那一帧上), 其余是判定那一条的。
FALSIFY = {
    "③命中": "写参成功而固件不走 `Recd_Program`(或落库前 return)⇒ 不会停在 %s" % (breakpoint.text(BP_REC)),
    "①": "写参成功而 Recd_Program 没被调用(或落库前 return)⇒ 这条口一动不动",
    "②": "`Recd_PrgCntDay` 也被 b_PrgStart 那套合并语义短路 ⇒ 第二、三次写结算日不新增",
    "⑤": "安全判定没拦住厂外写参(verdict 仍 PASS)⇒ 无权限也能改参数",
}

# ---- 半途中止时要一次记全的条目(纯数据; 与正常路径同名同号**同观测**, 少记一条 = 分母变小)----
# ⑤ 占两条 —— 正常路径上它本来就是两条记录(被拒 / 被拒的那次两条口都不新增)。
ENTRIES_ALL = (
    ("①", "成功写参落一条『编程』记录(30120B0A): 触发后该口最新一条**序号推进**(新增一条); "
           "若该口已置 `b_PrgStart`(同一上电周期内第二次及以后写参)则**就地改写**同一条 —— "
           "此形态下该口序号与时刻都不动, 由『写库位置真被调用』或『结算日编程口同一次写参推进』认定",
     judge.SERIAL),
    ("②", "『结算日编程』(301A0B0A) 每次成功写结算日**必新增**一条 —— 序号 +1(该口无 b_PrgStart 合并语义)",
     judge.SERIAL),
    ("③", "写库位置真走到 Recd_Program 调用(TaskRecord.c:824): 写参那一刻停在那儿", judge.DEBUG),
    ("④", "Recd_Program 收到的两个入参非空(pOper 操作者 / pDIs 参数项)—— 即这两样确实被传进去了",
     judge.DEBUG),
    ("⑤", "无权限写被拒(退厂内后同一写帧被安全判定打回)", judge.SERIAL),
    ("⑤", "被拒的那一次两条事件口都不新增", judge.SERIAL),
)


def _pending(*done):
    """半途中止时要一次记全的条目 = `ENTRIES_ALL` 里除掉**已经记过**的那几条。

    `done` 就是认领号本身(纯数据), 在每一个中止点就地写"此刻已记过谁" —— 这样加一步、挪一步
    时, 分母跟着走的是这一处, 而不是某个隐含的序号。
    """
    return tuple(e for e in ENTRIES_ALL if e[0] not in done)


def _stop_unproven(ctx, entries, why):
    """半途中止的统一收场: 条目一次记全(`ok=None`) → 出声 → 停住(退 2, 不是失败)。"""
    ctx.J.extend(cmd_bank.unproven_records(entries, why, FALSIFY))
    ctx.J.note("5-6 编程段: 半途中止(这一次没做成) —— %s" % why)
    raise trial.Stop("%s ⇒ 未定论(退 2), 不是失败" % why)


def _row_txt(r):
    """一条事件记录读数 → 一行文本(`None` 写成"读不出", 不写成 0)。"""
    r = r or {}
    return "序号=%s 发生时刻=%s" % (r.get("seq") if r.get("seq") is not None else "读不出",
                                    r.get("ts") or "读不出")


def part_program(ctx):
    """一段 = 5-6 的全部条目: 进厂内 → 基线 → 写参(断点看 b_PrgStart/入参) → 同一上电周期再写一次
    → 两条事件口 → 退厂内写被拒 → 写回 d0。

    ⚠ 观测一律 attach, **禁用 launch**(launch 会复位表, RAM 态清零); 见 CLAUDE.md 调试链纪律 1。
    ⚠ 第 4 步(退厂内被拒)**排在最后**, 与 md 的步号次序不同: 记录读回同样受 `Chk_SafeMode` 管,
      厂外读一律被打回, 先退厂内后面就全是"读不回来"。第 5 步(读两条口)因此也必须在厂内做。
    ⚠ 写回 d0 那一次在 `finally` 里: 中途抛异常/提前返回都会让表留在 alt 号上, 而
      `scripts/_restore_all.py` 明文"不动结算日" —— 那个假设的兑现点在这里。
    """
    ser = ctx.ser

    # ---- 阶段一(第 1 步): 进编程态 + 读基线 ----
    cmd_bank.enter_factory(ser)                       # 0x14 受控写的前提(Is_EnablePrg); 读记录同受 Chk_SafeMode 管
    bd = cmd_bank.read_billday(ser, wait=WAIT)
    if not bd:
        _stop_unproven(ctx, _pending(), "读第1结算日无应答(d0 不可得)⇒ 写参的载体都没确认住, 中止")
    d0 = bd[1]
    alt = d0 + 1 if d0 < 31 else d0 - 1
    t0 = cmd_bank.read_clock(ser, chip="管理芯", quiet=True)
    pre = cmd_bank.read_event_row(ser, cmd_bank.PROG_EVENT, 1, wait=WAIT)
    pre_sub = cmd_bank.read_event_row(ser, cmd_bank.PROG_EVENT_SUB, 1, wait=WAIT)
    print("   基线: 表钟=%s | 结算日 d0=%d → 本次写 alt=%d | %s 最新一条[%s] | %s 最新一条[%s]"
          % (t0, d0, alt, OAD_PROG, _row_txt(pre), OAD_PROG_SUB, _row_txt(pre_sub)))
    # crit=None: 判据表 5-6 的条目是 ①②③④⑤ —— "记录口几何"没有对应条目, 只进日志。
    ctx.J.add("记录口几何(%s / %s)" % (OAD_PROG, OAD_PROG_SUB), None,
              "『编程』容量 %d 条 / 单条 %d 字节(发生时刻 6 + 操作者 4 + 10 个标识各 4) / 索引区首址 0x%04X; "
              "『结算日编程』%s 无合并语义"
              % (REC_CAP, REC_LEN, REC_IDX_BASE, OAD_PROG_SUB), crit=None,
              falsify="条数与首址若是别的数 ⇒ 第 6、7 步读回来的标识个数与封顶位置对不上")

    # ---- 开会话(断点观测)。台面没接 J-Link / 用户指定只做黑盒 ⇒ None, 串口观测照跑 ----
    ctx.session()
    if ctx.g is not None:
        # 整片 .out 解一次(停核遍历, 内部喂狗, 收尾放行) —— 之后一律查这张图
        gdbinit.build(ctx.g)
    g = ctx.g
    have_wb = g is not None

    w1, w2 = {}, {}                             # 两次写参各自的 verdict —— 写没写成功决定 ①② 能不能判
    r_rec = r_rec2 = None
    post = post_sub = post2 = post_sub2 = None

    try:
        # ---- 阶段二(第 2、3 步): 写 alt 那一次停在 Recd_Program 里, 读 b_PrgStart / pOper / pDIs ----
        def write_alt():
            # **取值**(与"纯动作"的 fn 不同): 写本身没成时 ① ② 是"没做成"而不是"固件没落库"。
            w1["v"], w1["n"] = cmd_bank.write_billday(ser, alt, wait=WAIT)

        # 递**断点元组**而不是已挂好的 bpno: `fire_hit` 见到元组会自己挂、命中与没命中**两条路都撤**。
        # 传 bpno 则撤不撤只由 `drop=` 管, 万一没命中就留在槽里 —— 那是"核被自己撂停、其后串口全哑"的来源。
        # 没会话(`g is None`)时它照样把 `write_alt` 发出去, 只是返回 None(黑盒那一半不许跟着降级)。
        r_rec = breakpoint.fire_hit(g, BP_REC, write_alt,
                            label="断[A] 写第1结算日=%d → 停在 Recd_Program %s(读 b_PrgStart / 入参)"
                                  % (alt, breakpoint.text(BP_REC)),
                            vars=VARS_REC, crit="③", falsify=FALSIFY["③命中"])

        # ---- 阶段三(第 5 步前半): 两条口触发后的最新一条 ----
        post = cmd_bank.read_event_row(ser, cmd_bank.PROG_EVENT, 1, wait=WAIT)
        post_sub = cmd_bank.read_event_row(ser, cmd_bank.PROG_EVENT_SUB, 1, wait=WAIT)
        print("   写#1 后: %s [%s] | %s [%s]" % (OAD_PROG, _row_txt(post), OAD_PROG_SUB, _row_txt(post_sub)))

        # ---- 阶段四(第 5 步后半): 同一上电周期内再写一次 —— 『编程』口应就地改写(序号不动),
        #      『结算日编程』口应再新增一条(序号再 +1)。pDIs 每停一次都读一遍, 看它怎么排。 ----
        def write_alt_again():
            w2["v"], w2["n"] = cmd_bank.write_billday(ser, alt, wait=WAIT)

        r_rec2 = breakpoint.fire_hit(g, BP_REC, write_alt_again,
                            label="断[B] 同一上电周期再写一次第1结算日=%d(旁证: 第二次仍走到 Recd_Program)"
                                  % alt,
                            vars=VARS_REC, crit=None, obs=judge.DEBUG,
                            falsify="固件第二次写参没走 Recd_Program ⇒ 那一支的落库位置与第一次不是同一处")
        post2 = cmd_bank.read_event_row(ser, cmd_bank.PROG_EVENT, 1, wait=WAIT)
        post_sub2 = cmd_bank.read_event_row(ser, cmd_bank.PROG_EVENT_SUB, 1, wait=WAIT)
        print("   写#2 后: %s [%s] | %s [%s]"
              % (OAD_PROG, _row_txt(post2), OAD_PROG_SUB, _row_txt(post_sub2)))
    finally:
        # ---- 复原: 写回 d0 并读回验(表无净变的兜底) ----
        w3, _n3 = cmd_bank.write_billday(ser, d0, wait=WAIT)
        v2 = cmd_bank.read_billday(ser, wait=WAIT)
        ok_back = bool(v2 and v2[1] == d0)
        print("   复原: 写回 d0=%d → %s; 读回=%s ⇒ %s"
              % (d0, w3, (v2[1] if v2 else None), "已复原" if ok_back else "**没复原**, 请人工核"))
        if not ok_back:
            ctx.J.add("结算日写回 d0 未确认", False,
                      "写回应答=%s / 读回=%s —— 表可能留在 alt=%s 号上, 需人工核"
                      % (w3, (v2[1] if v2 else None), alt))

    # ---- ③ ④ 断点那一次的记账 ----
    # ③ 由 `fire_hit` 那一条自己认领(它只答"停到没停到")。④ 是**同一个读数**说明的另一件事
    #   (两个入参非空), 从这里按同一份 `vars` 补一条 —— ③ 与 ④ 是**一次取证的两面**, 不是两次。
    # ⚠ 这里**不能**读 `r_rec["hit"]`: judge 的 record 只有 name/ok/detail/crit/obs/falsify/trig 七键,
    #   `hit` 在 `record()` 里就被拿去定 `ok` 并折进 `detail` 了(5-3 判据② 因此吃过一次假 FAIL)。
    _vals = (r_rec or {}).get("vars") or (r_rec or {}).get("at_vals") or {}
    if r_rec is not None:
        print("      断[A] 记录: ok=%s | %s" % (r_rec.get("ok"), r_rec.get("detail")))
        print("      停时读到: %s" % _vals)
        ctx.J.extend([r_rec])           # ③ 整条原样收下 —— 它自己带的 ok/name/detail 就是这一次的读数
        # `fire_hit` 没停到时 `vars` 是空的 ⇒ ④ 也记"没做成", 不冒充成"入参为空"。
        ctx.J.add("Recd_Program 入参非空(pOper 操作者 / pDIs 参数项)",
                  (all(cmd_bank.nn(v) for v in _vals.values()) if _vals else None),
                  "读到 %s" % (_vals or "没停到, 入参没读到"), crit="④", obs=judge.DEBUG,
                  falsify="固件把空操作者/空参数项传进 Recd_Program(丢掉入参)⇒ 两个指针里出现 0")
    else:
        why = ("本次无调试会话 ⇒ 断点观测这一次没做成"
               if not have_wb else "断点没停到 Recd_Program ⇒ 这一次没做成")
        ctx.J.add("写库位置 Recd_Program 调用(指令路径)", None, why, crit="③", obs=judge.DEBUG,
                  falsify="不做这一次时无从判 —— 本记录不作为固件证据")
        ctx.J.add("Recd_Program 入参非空(pOper 操作者 / pDIs 参数项)", None, why, crit="④",
                  obs=judge.DEBUG, falsify="不做这一次时无从判 —— 本记录不作为固件证据")
    # 断[B] 是**旁证**(crit=None, 不认领条目): judge 不会把它印进汇总, 所以显式 note 出来。
    if r_rec2 is not None:
        _v2b = (r_rec2 or {}).get("vars") or (r_rec2 or {}).get("at_vals") or {}
        ctx.J.extend([r_rec2])
        ctx.J.note("旁证(不认领条目) 断[B] 第二次写参: ok=%s | %s | 停时读到 %s"
                   % (r_rec2.get("ok"), r_rec2.get("detail"), _v2b or "没停到"))

    # ---- ①② : 两条口在两次写参上各读数一次 ----
    adv, why = cmd_bank.event_advanced(post, pre, t0)
    adv2, why2 = cmd_bank.event_advanced(post2, post, t0)
    adv_sub, why_sub = cmd_bank.event_advanced(post_sub, pre_sub, t0)
    adv_sub2, why_sub2 = cmd_bank.event_advanced(post_sub2, post_sub, t0)
    # ⚠ 写参本身没被受理(verdict != PASS) ⇒ 触发没发生, ①② 记"没做成", **不许**记 FAIL。
    #   "写被拒"是 ⑤ 那一条判据的事, 别让它顺带把 ① 判成"固件没落库"。
    trig_ok = (w1.get("v") == "PASS")
    trig_ok2 = (w2.get("v") == "PASS")
    # ---- ① 的两支合法形态 ----
    #   新增一条: 序号推进(adv=True)。
    #   **就地改写**: 同一上电周期内第二次及以后写参, `b_PrgStart`(TaskRecord.c:47)已置位 ⇒
    #     固件走 off=10 那支改写同一条, 序号与时刻**都**保持首次那刻 ⇒ 该口读数与"没落库"同形。
    #     这一支要另找凭据才判得出, 手上只有三样(任一成立即认):
    #       · 写库位置真被调用 —— 断点那一次停在了 `Recd_Program`(③ 的读数, 同一次取证的另一个方面);
    #       · 『结算日编程』口同一次写参推进了 —— 写参这条路是通的, 只是这条口按语义合并了;
    #       · 断[B] 那一次(同一上电周期第二次写)也停住了 —— 第二次写照样走到写库位置。
    #   ⚠ 不认这一支的后果是**必然**的假 FAIL: 一台已上电跑过一整天的表, 第一支永远不成立
    #     (5-6 实跑就是这么撞上的)。
    wb_hit = ((r_rec or {}).get("ok") is True)
    wb_hit2 = ((r_rec2 or {}).get("ok") is True)
    if not trig_ok:
        ctx.J.add("『编程』口 30120B0A 触发后最新一条", None,
                  "写第1结算日=%d 本身没成(verdict=%s / %s)⇒ 触发没发生, 本条未证"
                  % (alt, w1.get("v"), w1.get("n")), crit="①",
                  falsify="不做这一次时无从判 —— 本记录不作为固件证据")
    elif adv is True:
        ctx.J.add("『编程』口 30120B0A 触发后最新一条", True,
                  "第1次写参新增一条(序号推进): %s" % why, crit="①", falsify=FALSIFY["①"])
    elif adv is False and (wb_hit or wb_hit2 or adv_sub is True):
        ctx.J.add("『编程』口 30120B0A 触发后最新一条", True,
                  "就地改写(b_PrgStart 已置位 ⇒ 同一上电周期内不新增、序号与时刻都不动): %s; "
                  "另据「%s」认定这一次写参确实经写库位置落库"
                  % (why, "断点停在 Recd_Program %s" % breakpoint.text(BP_REC) if wb_hit
                     else ("断[B] 第二次写参也停在 Recd_Program %s" % breakpoint.text(BP_REC)
                           if wb_hit2 else "『结算日编程』口同一次写参推进(%s)" % why_sub)),
                  crit="①", falsify=FALSIFY["①"])
    else:
        ctx.J.add("『编程』口 30120B0A 触发后最新一条", adv, why, crit="①", falsify=FALSIFY["①"])

    # ---- ② : 『结算日编程』口每次成功写结算日必新增一条; 本跑写了两回, 两回都应推进 ----
    if not trig_ok:
        ctx.J.add("『结算日编程』口 301A0B0A 触发后最新一条", None,
                  "写第1结算日=%d 本身没成(verdict=%s)⇒ 触发没发生, 本条未证" % (alt, w1.get("v")),
                  crit="②", falsify=FALSIFY["②"])
    elif adv_sub is None:
        ctx.J.add("『结算日编程』口 301A0B0A 触发后最新一条", None,
                  "第1次写参后读不回该口(%s)⇒ 本条未证" % why_sub, crit="②", falsify=FALSIFY["②"])
    elif adv_sub is False:
        ctx.J.add("『结算日编程』口 301A0B0A 触发后最新一条", False,
                  "第1次写参受理了, 该口却没新增(%s) —— 该口无合并语义, 报代码侧" % why_sub,
                  crit="②", falsify=FALSIFY["②"])
    elif not trig_ok2:
        ctx.J.add("『结算日编程』口 301A0B0A 触发后最新一条", True,
                  "第1次写参新增一条(%s); 第2次写参没成(verdict=%s)⇒ 只证了第1次, 未证『每次必新增』"
                  % (why_sub, w2.get("v")), crit="②", falsify=FALSIFY["②"])
    elif adv_sub2 is True:
        ctx.J.add("『结算日编程』口 301A0B0A 触发后最新一条", True,
                  "两次写各新增一条: 第1次 %s; 第2次 %s" % (why_sub, why_sub2),
                  crit="②", falsify=FALSIFY["②"])
    elif adv_sub2 is None:
        ctx.J.add("『结算日编程』口 301A0B0A 触发后最新一条", True,
                  "第1次写参新增一条(%s); 第2次写参后读不回该口(%s)⇒ 只证了第1次"
                  % (why_sub, why_sub2), crit="②", falsify=FALSIFY["②"])
    else:
        ctx.J.add("『结算日编程』口 301A0B0A 触发后最新一条", False,
                  "第2次写参受理了, 该口却没再新增(%s) —— 同一上电周期内第二次写结算日被合并了, "
                  "报代码侧" % why_sub2, crit="②", falsify=FALSIFY["②"])
    # 断[B] 那一次读到的 pDIs 与第 1 次怎么排 —— 旁证, 不认领条目。
    ctx.J.note("旁证(不认领条目) 两次写参的『编程』口读数: 写#1 [%s] → 写#2 [%s]; 序号 %s"
               % (_row_txt(post), _row_txt(post2),
                  "不动(就地改写)" if (post or {}).get("seq") == (post2 or {}).get("seq")
                  else "变了" if post2 else "第2次读不回"))

    # ---- 阶段五(第 4 步): ⑤ 无权限写被拒且不记录 —— 退厂内后同一写帧应被安全判定打回 ----
    t5 = cmd_bank.read_clock(ser, chip="管理芯", quiet=True)
    pre5 = cmd_bank.read_event_row(ser, cmd_bank.PROG_EVENT, 1, wait=WAIT)
    pre5_sub = cmd_bank.read_event_row(ser, cmd_bank.PROG_EVENT_SUB, 1, wait=WAIT)
    vf = cmd_bank.exit_factory(ser)
    v_free = None
    if not vf or vf != "PASS":
        ctx.J.add("退厂内(无权限写的前置)", None,
                  "645 0x1F 未受理(verdict=%s)⇒ 这一次没做成" % vf, crit="⑤",
                  falsify="不做这一次时无从判 —— 本记录不作为固件证据")
    else:
        v_free, note_free = cmd_bank.write_billday(ser, alt, wait=WAIT)
        ctx.J.add("厂外写参被拒(verdict=%s, %s)" % (v_free, note_free), v_free != "PASS",
                  "厂内态下同一帧是 PASS, 厂外应被安全判定打回(DLT645App.c 的写参安全判定; "
                  "期望 645 应答带 ER_PSWD, 错误码 4)", crit="⑤", falsify=FALSIFY["⑤"])
    # ⚠ **回厂内之后才读记录**: 记录读回同样受 `Chk_SafeMode` 管(5-3 实踩), 厂外读一律被 DAR=20
    #   打回 —— 拿"没读到"当"没新增"就是假通过。改到这儿读, 并让三态显形(读不回来就记"没做成")。
    cmd_bank.enter_factory(ser)           # 退厂内是暂时的: 把台面交回厂内态, 免得后面几步全被安全判定打回
    if v_free is None:
        ctx.J.add("被拒的那一次两条事件口都不新增", None,
                  "退厂内 / 厂外写参那一步没做成 ⇒ 没有『被拒的那一次』可判, 本条未证", crit="⑤",
                  falsify="不做这一次时无从判 —— 本记录不作为固件证据")
    else:
        post5 = cmd_bank.read_event_row(ser, cmd_bank.PROG_EVENT, 1, wait=WAIT)
        post5_sub = cmd_bank.read_event_row(ser, cmd_bank.PROG_EVENT_SUB, 1, wait=WAIT)
        ok5a, why5a = cmd_bank.event_advanced(post5, pre5, t5)
        ok5b, why5b = cmd_bank.event_advanced(post5_sub, pre5_sub, t5)
        ctx.J.add("被拒的那一次两条事件口都不新增",
                  (ok5a is False and ok5b is False) if (ok5a is not None and ok5b is not None) else None,
                  "编程: %s; 结算日编程: %s" % (why5a, why5b), crit="⑤",
                  falsify="写被拒了但 Recd_Program 照样落库 ⇒ 事件记录与被拒动作不成对应(记了一条没发生的编程)")

    # ---- 阶段六(第 6、7 步): 十个标识与条数封顶 —— 本脚本没做, 记 `ok=None`(不认领条目) ----
    # 第 6 步要"同一上电周期里连写 11 个**不同**的参数项"; 第 7 步要"凑够 11 个上电周期各写一次"。
    # 两步都要 11 个可写参数项与各自的合法取值, 本脚本手上没有那份清单 —— 在这里下单等于拿
    # 想当然的 DI 去写真表。故记"没做", 不拿第 5 步那两次同参数项写的读数去顶(那证的是另一件事)。
    ctx.J.add("同一上电周期连写 11 个不同参数项 ⇒ 记录里只留最后 10 个标识", None,
              "本步未做: 要 11 个可写参数项与各自合法取值, 本脚本没有那份清单; "
              "已知『编程』单条 %d 字节里留给标识的是 10 个各 4 字节(第 1 个被顶掉的位置在这 10 个之内)"
              % REC_LEN, crit=None,
              falsify="连写 11 个不同标识而记录里留住了第 1 个 ⇒ 固件按别的规则截断")
    ctx.J.add("连续 11 个上电周期 ⇒ 条数封顶 10、最早一条被顶掉、总次数不封顶", None,
              "本步未做: 要退厂内再回厂内凑 11 个上电周期; 索引区首址 0x%04X 头 3 字节是总次数(不封顶)、"
              "后 2 字节是条数(封顶 %d)" % (REC_IDX_BASE, REC_CAP), crit=None,
              falsify="条数涨过 %d 而最早那条还在 ⇒ 固件没按封顶顶掉" % REC_CAP)

    ctx.J.note("未证: 『操作者/项目**正确**』里的『正确』半支 —— 本脚本只证两个入参非空, "
               "值对不对要拿 DL/T645 规范里操作者串与参数项列表的期望编码去比, 手上没有那份期望值")


def _banner():
    return ("== 5-6 编程事件 | 工程=%s 表号=%s ==\n"
            ".. 触发=同一上电周期内写第1结算日 d0→alt 两次, 末尾写回 d0(自恢复); "
            "白盒停 %s 读 b_PrgStart / Recd_Program 入参"
            % (CURRENT.PROJECT, CURRENT.TABLE_ADDR.hex().upper(), breakpoint.text(BP_REC)))


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "5-6 编程(成功写参落一条、两条事件口、写库位置与入参、无权限写被拒)",
        cmd_bank.program_criteria,
        name="5_6_program",
        parts=[("5-6 编程段", part_program)],
        gdb=breakpoint, banner=_banner(),
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV)))
