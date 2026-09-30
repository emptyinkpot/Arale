# -*- coding: utf-8 -*-
"""在证什么: 厂外写第1结算日 == 被权限判定打回 —— 停在写参量的**拒绝点** `DLT645App.c:1548`
  (`return ER_PSWD`, 该行只有被拒那一支到得了), 且那一趟**没走到写库位置**、两条事件口都不新增;
  厂内写同一帧 == 落一条编程记录(停在 `TaskRecord.c:824`), 入参非空, 『结算日编程』口序号 +1;
  另加 698 读 `0xFF3005`(内部软件版本)的正文 == 本机发布标识。
会向表写什么: 645 进/退厂内(记录读回受 Chk_SafeMode 管, 厂外读一律被 DAR=20 打回, 5-3 实踩),
  加两次厂外写 + 一次厂内写。**厂外那两趟不产生副作用** —— `:1548` 在任何写入之前 return, 结算日不动;
  另一个有副作用的口(`:1512` 软编程设置支会调 `Set_PrgTimer`)被**四个 DI 字节全等** `04 CC 00 01`
  卡住, 本项的 DI `04 00 0B 01` 进不去, 所以敢对同一帧发两次。厂内那次把结算日写成 alt,
  收尾**写回 d0 并读回验**(在 `finally` 里)。本项**不注入** —— 权限这条路帧通道造得出来。
  台面收尾走 `python scripts/_restore_all.py`。
跑法: `python project/tests/_test_16_1_prog_auth.py`(真串口 + 真探针; `--no-gdb` 只做黑盒半边)。
结论怎么读: 账本末行「判据: 满足 n/N」, N=7; 退 0 = 通过 / 1 = 失败 / 2 = 未定论。
"""
from common import judge              # 观测种类常量(黑盒标 SERIAL, 白盒那几条标 DEBUG)
from common import trial              # 运行外壳: argv/日志/账本/串口/会话/收尾三连/退出码
from meterlib import cmd_bank         # 帧目录 + 语义动词积木箱(每步自带打印/判 PASS·TBD·FAIL)
from project import CURRENT           # 本工程画像: 表号/.out 路径/喂狗点(库不认表, 只有脚本认)
from swdbg import breakpoint          # 断点级白盒(停核 + 读局部量); 没接 J-Link → open_or_none→None
# 整片 .out 地图: 开会话后解一次, 之后断点/注入点只查它(见 swdbg/gdbinit.py 文件头)
from swdbg import gdbinit

# ---- 子项参数(纯数据; 要改的在这里改) ----
WAIT = 3.0                     # 每次记录读回 / 写参应答的单次等待
NOHIT_WIN = 3.0                # 厂外写第二趟的否定期望窗口(写参应答本身只等 WAIT)

# ---- 断点: 模块级**字面量元组** ----
# `scripts/_check_anchors.py` 用 `ast.literal_eval` 抠 `BP_<X>`/`VARS_<X>` 这两个名字逐条对源码核
# (变量在断点那一行赋过值没有); 写成 `cmd_bank.PROG_DENY_BP` 那种表达式就抠不出来, 等于没人核过。
# 断[B] `:1548` 是 `return ER_PSWD;` —— 只读 `pFrame`(形参, 全程活着); `datLen` 要到 :1557 才赋值,
#   停在这儿读它是**栈垃圾**, 故不进 VARS。
# 断[A] `TaskRecord.c:824` 是 `Recd_Program` 体内首条可执行(`Recd_Program+8` @0x1e416);
#   入口那一行(:821 @0x1e40e)是**空洞**, 一个量都读不出来, 别把断点挪回去。
BP_DENY = ("DLT645App.c", 1548)       # `return ER_PSWD` —— 无权限写参的拒绝点 @0x34742
VARS_DENY = ("pFrame",)
BP_REC = ("TaskRecord.c", 824)        # `Recd_Program` 体内首条可执行 —— 写库位置 + 入参 @0x1e416
VARS_REC = ("pOper", "pDIs")

# ---- 半途中止时要一次记全的条目(纯数据; 与正常路径同名同号**同观测**, 少记一条 = 分母变小)----
ENTRIES_ALL = (
    ("①", "厂外写参停在权限拒绝点 %s:%d" % BP_DENY, judge.DEBUG),
    ("②a", "厂外写参没走到写库位置(否定期望)", judge.DEBUG),
    ("②b", "厂外写参之后两条事件口都不新增", judge.SERIAL),
    ("③", "厂内写参停在写库位置 %s:%d" % BP_REC, judge.DEBUG),
    ("④", "Recd_Program 入参非空(pOper 操作者 / pDIs 参数项)", judge.DEBUG),
    ("⑤", "『结算日编程』口 301A0B0A 厂内写之后序号 +1", judge.SERIAL),
    ("⑥", "698 读 0xFF3005(内部软件版本) 正文 == 本机发布标识", judge.SERIAL),
)


def _add(J, label, ok, why, crit, obs=judge.SERIAL, falsify=None, trig=None):
    """记一条判据 + 打一行三态(`falsify` 逐条给 —— 同一个 crit 的两个通道答的假条件不同)。"""
    J.add(label, ok, why, crit=crit, obs=obs, falsify=falsify, trig=trig)
    print("   [%s] %s —— %s" % ({True: "PASS", False: "FAIL", None: "TBD"}[ok], label, why))


def _stop_unproven(ctx, why):
    """半途中止的统一收场: 条目一次记全(`ok=None`) → 出声 → 停住(退 2, 不是失败)。"""
    ctx.J.extend(cmd_bank.unproven_records(ENTRIES_ALL, why))
    ctx.J.note("16-1 权限/记录段: 半途中止(这一次没做成) —— %s" % why)
    raise trial.Stop("%s ⇒ 未定论(退 2), 不是失败" % why)


def part_auth(ctx):
    """一段 = 16-1 的全部条目: 厂内读基线 → 厂外写两趟 → 回厂内读回 → 厂内写 → 复原 d0 → 读版本。

    ⚠ **台面态的次序是承重的**: ① 要的是**厂外那一趟**, 而"不落记录"要靠两条事件口判, 记录读回又受
      `Chk_SafeMode` 管(厂外读一律被 DAR=20 打回) ⇒ 基线只能在**厂内**读。于是:
      厂内读基线 → 退厂内 → 厂外写两趟 → 回厂内读回 → 厂内有权限写 → 复原结算日 → 退厂内 → 读版本。
    ⚠ 观测一律 attach(**禁用 launch**: launch 会复位表、RAM 态清零); 见 CLAUDE.md 调试链纪律 1。
    ⚠ 递**断点元组**而不是已挂好的 bpno: `fire_hit`/`expect_no_hit` 见到元组会自己挂、命中与没命中
      **两条路都撤**。传 bpno 则撤不撤只由调用方管, 万一没命中就留在槽里 —— 那是"核被自己撂停、
      其后串口全哑"的来源。
    """
    J, ser = ctx.J, ctx.ser
    print("\n===== 16-1 参数设置权限: 厂外写被拒(停拒绝点) + 厂内写落记录 + 软件标识 =====")

    ctx.session()
    if ctx.g is not None:
        # 整片 .out 解一次(停核遍历, 内部喂狗, 收尾放行) —— 之后一律查这张图
        gdbinit.build(ctx.g)
    g = ctx.g
    have_wb = g is not None
    if not have_wb:
        if ctx.waived:
            print("   -- 本次**用户指定**只做黑盒(串口观测) ⇒ 断点观测(白盒)按指定不做"
                  "(J 列须记明本次范围)")
        else:
            print("   !! 本次无调试会话 → 断点观测(白盒)整体**未做**; 本次范围 = 仅黑盒"
                  "(对外行为可判, 『停在哪个拒绝点 / 写库位置走没走到』未取证 —— "
                  "见 CLAUDE.md「凭什么算「真实测试」·两种观测」)")

    # ---- 0. 进厂内读基线: 记录读回受 Chk_SafeMode 管, 厂外读一律被 DAR=20 打回 ----
    cmd_bank.enter_factory(ser)
    bd = cmd_bank.read_billday(ser, wait=WAIT)
    if not bd:
        _stop_unproven(ctx, "读第1结算日无应答(d0 不可得)⇒ 写参的载体都没确认住")
    d0 = bd[1]
    alt = d0 + 1 if d0 < 31 else d0 - 1
    t0 = cmd_bank.read_clock(ser, chip="管理芯", wait=WAIT, quiet=True)
    pre = cmd_bank.read_event_row(ser, cmd_bank.PROG_EVENT, 1, wait=WAIT)
    pre_sub = cmd_bank.read_event_row(ser, cmd_bank.PROG_EVENT_SUB, 1, wait=WAIT)
    print("   基线: 表钟=%s | 结算日 d0=%d → 本次写 alt=%d | 编程 最新一条=%s 结算日编程 最新一条=%s"
          % (t0, d0, alt, (pre or {}).get("ts"), (pre_sub or {}).get("ts")))

    w_free = {}
    try:
        # ============ 厂外那两趟(① ②a) ============
        vf = cmd_bank.exit_factory(ser)
        if not vf or vf != "PASS":
            _add(J, "厂外写参停在权限拒绝点 %s:%d" % BP_DENY, None,
                 "645 0x1F 未受理(verdict=%s)⇒ 厂外那一趟没做成" % vf,
                 crit="①", obs=judge.DEBUG,
                 falsify="不做这一次时无从判 —— 本记录不作为固件证据")
            _add(J, "厂外写参没走到写库位置(否定期望)", None,
                 "同上, 这一次没做成", crit="②a", obs=judge.DEBUG,
                 falsify="不做这一次时无从判 —— 本记录不作为固件证据")
        else:
            # ---- 厂外写 #1: 停在权限拒绝点 ----
            # ⚠ 触发帧包一层小函数, 为的是**留下它的应答** —— `fire_hit` 只交回 judge 记录,
            #   不带被调函数的返回值, 而 ① 要拿"受理还是被拒"去分辨"是谁拦下的"。
            def write_free1():
                w_free["v"], w_free["n"] = cmd_bank.write_billday(ser, alt, wait=WAIT)

            deny_rec = None
            if have_wb:
                deny_rec = breakpoint.fire_hit(
                    g, BP_DENY, write_free1, vars=VARS_DENY,
                    label="断[B] 厂外写第1结算日=%d → 权限拒绝点 %s"
                          % (alt, breakpoint.text(BP_DENY)))
                print("      断[B] 记录: ok=%s | %s" % ((deny_rec or {}).get("ok"),
                                                        (deny_rec or {}).get("detail")))
                # crit=None: 只进日志; 判据在下面按"停到"+"被拒"一起认
                J.extend([deny_rec])
            else:
                write_free1()
            w_ok = (w_free.get("v") == "PASS")
            _deny_lab = "厂外写参停在权限拒绝点 %s:%d" % BP_DENY
            if not have_wb:
                _add(J, _deny_lab, None, "本次无调试会话 ⇒ 断点观测这一次没做成", crit="①",
                     obs=judge.DEBUG, falsify="不做这一次时无从判 —— 本记录不作为固件证据")
            elif (deny_rec or {}).get("ok") is True:
                _add(J, _deny_lab, True,
                     "停在 %s(`return ER_PSWD`); 该次写回应答=%s(非受理)"
                     % (breakpoint.text(BP_DENY), w_free.get("v")), crit="①", obs=judge.DEBUG,
                     falsify="厂外写参被放行(权限判定不生效)⇒ 走不到 `:1548` 的拒绝支, 断点不命中")
            elif w_ok:
                _add(J, _deny_lab, False,
                     "断点没命中, 而该次写参被**受理**了(应答=%s)⇒ 权限判定没拦住厂外写参"
                     % w_free.get("v"), crit="①", obs=judge.DEBUG,
                     falsify="厂外写参被放行(权限判定不生效)⇒ 走不到 `:1548` 的拒绝支, 断点不命中")
            else:
                _add(J, _deny_lab, None,
                     "断点没命中, 而该次写参确被拒(应答=%s)⇒ 拒绝发生了, 但不是这一句拦的"
                     "(或断点没下上/停在了别处), 这一条没做成" % w_free.get("v"),
                     crit="①", obs=judge.DEBUG,
                     falsify="厂外写参被放行(权限判定不生效)⇒ 走不到 `:1548` 的拒绝支, 断点不命中")

            # ---- 厂外写 #2: 写库位置的否定期望 ----
            def write_free2():
                w_free["v2"], w_free["n2"] = cmd_bank.write_billday(ser, alt, wait=WAIT)

            if have_wb:
                J.extend([breakpoint.expect_no_hit(
                    g, BP_REC, NOHIT_WIN, vars=VARS_REC,
                    label="断[A] 厂外写第1结算日=%d → 写库位置 %s 否定期望(%.1fs)"
                          % (alt, breakpoint.text(BP_REC), NOHIT_WIN),
                    crit="②a",
                    falsify="厂外写参也走到了写库位置 ⇒ 无权限也落记录",
                    trigger=write_free2)])
            else:
                write_free2()
                _add(J, "厂外写参没走到写库位置(否定期望)", None,
                     "本次无调试会话 ⇒ 断点观测这一次没做成", crit="②a", obs=judge.DEBUG,
                     falsify="不做这一次时无从判 —— 本记录不作为固件证据")

        # ============ 回厂内读回: 两条口都不新增(②b) ============
        # ⚠ 必须**回厂内之后**才读(厂外读一律被 DAR=20 打回, 拿"没读到"当"没新增"是假通过)。
        cmd_bank.enter_factory(ser)
        post = cmd_bank.read_event_row(ser, cmd_bank.PROG_EVENT, 1, wait=WAIT)
        post_sub = cmd_bank.read_event_row(ser, cmd_bank.PROG_EVENT_SUB, 1, wait=WAIT)
        a1, y1 = cmd_bank.event_advanced(post, pre, t0)
        a2, y2 = cmd_bank.event_advanced(post_sub, pre_sub, t0)
        _add(J, "厂外写参之后两条事件口都不新增",
             (a1 is False and a2 is False) if (a1 is not None and a2 is not None) else None,
             "编程: %s; 结算日编程: %s" % (y1, y2), crit="②b",
             falsify="厂外写参也落了记录 ⇒ 这两条口的序号/时刻会动")

        # ============ 厂内写参: 停在写库位置(③ ④) ============
        w_res = {}

        def write_alt():
            w_res["v"], w_res["n"] = cmd_bank.write_billday(ser, alt, wait=WAIT)

        r_rec = None
        if have_wb:
            r_rec = breakpoint.fire_hit(
                g, BP_REC, write_alt, vars=VARS_REC,
                label="断[A] 厂内写第1结算日=%d → 停在写库位置 %s(读入参)"
                      % (alt, breakpoint.text(BP_REC)))
        else:
            write_alt()
            _add(J, "厂内写参停在写库位置 %s:%d" % BP_REC, None,
                 "本次无调试会话 ⇒ 断点观测这一次没做成", crit="③", obs=judge.DEBUG,
                 falsify="不做这一次时无从判 —— 本记录不作为固件证据")
            _add(J, "Recd_Program 入参非空(pOper 操作者 / pDIs 参数项)", None,
                 "本次无调试会话 ⇒ 断点观测这一次没做成", crit="④", obs=judge.DEBUG,
                 falsify="不做这一次时无从判 —— 本记录不作为固件证据")
        if r_rec is not None:
            print("      断[A] 记录: ok=%s | %s" % (r_rec.get("ok"), r_rec.get("detail")))
            # ⚠ 不读 `r_rec["hit"]`: judge 的 record 只有七键, `hit` 在 `record()` 里就被拿去定
            #   `ok` 并折进 `detail` 了(5-3 判据② 因此吃过一次假 FAIL)。
            _vals = r_rec.get("vars") or r_rec.get("at_vals") or {}
            print("      停时读到: %s" % _vals)
            J.extend([r_rec])            # crit=None: 判据在下面按同一份读数认领
            hit3 = (r_rec.get("ok") is True)
            _add(J, "厂内写参停在写库位置 %s:%d" % BP_REC,
                 True if hit3 else (False if w_res.get("v") == "PASS" else None),
                 ("停下: %s" % r_rec.get("detail")) if hit3 else
                 ("写参被受理(应答=%s)而没停到写库位置 ⇒ 成功写参没落库" % w_res.get("v"))
                 if w_res.get("v") == "PASS" else
                 ("写参本身没被受理(应答=%s)⇒ 触发没发生, 本条没做成" % w_res.get("v")),
                 crit="③", obs=judge.DEBUG,
                 falsify="写参被受理而固件不走 `Recd_Program`(或落库前 return)⇒ 不停在 %s:%d" % BP_REC)
            nonnull = (all(cmd_bank.nn(v) for v in _vals.values()) if _vals else None)
            _add(J, "Recd_Program 入参非空(pOper 操作者 / pDIs 参数项)", nonnull,
                 "读到 %s" % (_vals or "没停到, 入参没读到"), crit="④", obs=judge.DEBUG,
                 falsify="固件把空操作者/空参数项传进 Recd_Program(丢掉入参)⇒ 两个指针里出现 0")

        # ============ 厂内写之后『结算日编程』口 +1(⑤) ============
        post_alt = cmd_bank.read_event_row(ser, cmd_bank.PROG_EVENT_SUB, 1, wait=WAIT)
        trig_ok = (w_res.get("v") == "PASS")
        a3, y3 = cmd_bank.event_advanced(post_alt, post_sub, t0)
        _add(J, "『结算日编程』口 301A0B0A 厂内写之后序号 +1", a3 if trig_ok else None,
             y3 if trig_ok else
             "厂内写第1结算日=%d 本身没成(应答=%s)⇒ 触发没发生, 本条未证" % (alt, w_res.get("v")),
             crit="⑤",
             falsify="`Recd_PrgCntDay` 也被 b_PrgStart 那套合并语义短路 ⇒ 成功写结算日不新增")
    finally:
        # ---- 复原: 写回 d0 并读回验 ----
        # ⚠ 这一段**必须在 `finally` 里**: 厂内那次把结算日改成了 alt, 而 `_restore_all.py` 明文
        #   "不动结算日"(依据是"测试脚本跑完已自恢复")—— 那个假设的兑现点就在这里。`d0` 只存在于
        #   本次调用的内存里, 事后没人知道原值 ⇒ 只能由发过帧的这一方兜。
        w2, _n2 = cmd_bank.write_billday(ser, d0, wait=WAIT)
        v2 = cmd_bank.read_billday(ser, wait=WAIT)
        ok_back = bool(v2 and v2[1] == d0)
        print("   复原: 写回 d0=%d → %s; 读回=%s ⇒ %s"
              % (d0, w2, (v2[1] if v2 else None), "已复原" if ok_back else "**没复原**, 请人工核"))
        if not ok_back:
            _add(J, "结算日写回 d0 未确认", False,
                 "写回应答=%s / 读回=%s —— 表可能留在 alt=%s 号上, 需人工核"
                 % (w2, (v2[1] if v2 else None), alt), crit=None, obs=judge.SERIAL)

    # ---- 收尾: 把台面交回厂外态(本项起点也是厂外; 需要厂内的那几步已经做完) ----
    cmd_bank.exit_factory(ser)

    # ============ 软件标识(⑥) ============
    tsv = cmd_bank.read_clock(ser, chip="管理芯", wait=WAIT, quiet=True)
    sv = cmd_bank.sv_read_698_ver(ser, wait=WAIT)
    _add(J, "698 读 0xFF3005(内部软件版本) 正文 == 本机发布标识",
         None if sv is None else (sv == cmd_bank.SV_ID),
         ("读到「%s」, 本机发布标识「%s」%s"
          % (sv, cmd_bank.SV_ID, "" if sv == cmd_bank.SV_ID else " —— **不等**"))
         if sv is not None else "没读回(或形态对不上两种封装)⇒ 本条没做成",
         crit="⑥",
         falsify="固件返回的版本串与本机发布标识不一致(填错/截断/取自别的区)⇒ 逐字节不等")
    print("   (读版本时表钟=%s)" % tsv)


def _banner():
    return ("== 16-1 参数设置权限 | 工程=%s 表号=%s ==\n"
            ".. 厂外写第1结算日 → 停权限拒绝点 %s; 厂内写 → 停写库位置 %s; 再 698 读 0xFF3005"
            % (CURRENT.PROJECT, CURRENT.TABLE_ADDR.hex().upper(),
               breakpoint.text(BP_DENY), breakpoint.text(BP_REC)))


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "16-1 参数设置权限(厂外写被拒停拒绝点、厂外不落记录、厂内写落一条、入参、软件标识)",
        cmd_bank.prog_auth_criteria,
        name="16_1_prog_auth",
        parts=[("16-1 权限/记录段", part_auth)],
        gdb=breakpoint, banner=_banner(),
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV)))
