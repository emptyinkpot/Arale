# -*- coding: utf-8 -*-
from functools import partial


from meterlib import cmd_bank       # 帧目录 + 语义动词积木箱(每步自带打印/判 PASS·TBD·FAIL)
from common import trial           # 运行外壳: argv/日志/账本/串口/会话/收尾三连/退出码
from project import CURRENT          # 本工程画像: 表号/.out 路径/喂狗点(库不认表, 只有脚本认)
from swdbg import breakpoint            # 断点级白盒(停核 + 读函数内局部量); 没接 J-Link → None
# 整片 .out 地图: 开会话后解一次, 之后断点/注入点只查它(见 swdbg/gdbinit.py 文件头)
from swdbg import gdbinit

# ---- 断点: 模块级**字面量元组** ----
# `scripts/_check_anchors.py` 用 `ast.literal_eval` 抠 `BP_<X>`/`VARS_<X>` 这对名字逐条对源码
# (变量在断点那一行赋过值没有); 写成 `CB.SOMETHING` 那种表达式就抠不出来, 等于没人核过。
# ⚠ 规格 F 列写的「:875-878」不能照抄 —— `:877` 那条裸 `return` 没有独立机器码(行表条目数为 0),
#   只有 `:875` 断得上。
BP_GATE = ("TaskFreeze.c", 875)           # 风格判定: 判定体本身, 本行无调用(同 4-7) @TaskFreeze.c:875
VARS_GATE = ("TAB_MeterSty.style",)       # 那刻的风格值(判据①的硬证)
BP_INJ = ("TaskFreeze.c", 887)            # 注入停点 `dateNum = 0;`: 本行无调用(同 4-7) @TaskFreeze.c:887
# 停点处的**注入前对照量**(证注入确实改了状态)。⚠ **不收 `flag`**: 位置表说它在 `$sp-124` 可读,
# 但 :887 那一停它还没被赋过 —— 声明 `BOOL flag = OTHER;` 在 :871, 真值要到 :895/:903 才写,
# 读回来恒是那个占位值 ⇒ 它做对照量**不带任何信息**(断点体检会照实点出来)。
VARS_INJ = ("g_CurTime", "g_HisTime", "buff")
BP_MONTH = ("prev", "Check_BillFrezY", "Write_FrezData", 1)  # 月支写点 @TaskFreeze.c:1041
VARS_MONTH = ("frezNum", "buff")          # 判据③要比的就是这一停读回来的 `frezNum`
BP_YEAR = ("prev", "Check_BillFrezY", "Write_FrezData", 2)   # 年支写点 @TaskFreeze.c:1075(判据④盯它)
VARS_YEAR = ("frezNum", "buff")
# ⚠ 两个写点分处 `if (flag == FALSE)` 的两支, **同一趟只会过一个** —— 判据④ 的根据就在这里。
# ⚠ `frezNum`/`buff` 在两个写点都落在可读区间 `0x331ec-0x33418` 里(位置表逐区间核过)。
#   **别把 `flag`/`over`/`flg`/`off` 加进来** —— 那两个断点处它们是**空洞区间**, 读回来是"读不到",
#   而那看起来像"固件没给值"(取证时会把停错地方误读成固件问题)。
#   ⚠ `frezNum` 是**寄存器驻留**(`$r6`), 只能读、不能按地址写回。


def _wb(ctx):
    """会话偏函数袋 —— **只做会话线的接线, 不含任何步骤**; 没会话时整个是 `None`。

    递**断点元组**而不是已挂好的 bpno: 库原语见到元组都会自己挂、**命中与没命中两条路都撤**。
    ⚠ 本子项比 4-7 多一个键 `"miss"`(`inject_miss`)—— 判据④"年支写点仍未被走到"只有它给得出。
    ⚠ 不能改用 `inject_hit` 再做一次否定期望: 它在 `:1041` 命中后**放行才返回**, 再去挂 `:1075` 时
      那一趟早在放行的微秒级里跑完了 ⇒ 那个窗口必然"超时达成", 看着是通过、其实什么都没看。
      `inject_miss` 在注入**之前**就把断点挂好, 没有这个竞态。
    """
    g = ctx.g
    if g is None:
        return None
    return {"arm": lambda a: ctx.bp(a),
            "wait": partial(breakpoint.wait_hit, g),
            "neq": partial(breakpoint.expect_no_hit, g),
            "inj": partial(breakpoint.inject_hit, g),
            "miss": partial(breakpoint.inject_miss, g)}


def part_month_step(ctx):
    """11-2 的全部条目 —— **一行一步, 顺序即步骤**: 每行都是库 11-2 段里的一个积木
    (`CB.month_step_*`); 读动词返回数据, 判据动词返回证据记录, 由 `J.extend` 收账。
    每个积木自己打印它停了哪一行、读到什么、判 PASS/TBD/FAIL —— 这一列注释就是全部流程。"""
    ser = ctx.ser
    ctx.session()                          # 观测一律 attach(**禁用 launch**: launch 复位表、RAM 清零)
    # 整片 .out 解一次(要停核遍历, 内部按块喂狗, 收尾放行) —— 之后 `ctx.bp()` /
    # `inject_anchor()` / `decision_anchor()` 一律查这张图, 谁都不再独立反汇编。
    if ctx.g is not None:
        gdbinit.build(ctx.g)
    wb = _wb(ctx)
    cmd_bank.month_step_intro(wb is not None, ctx.waived)              # 开场: 本段在造什么
    cmd_bank.enter_factory(ser)                                        # 白盒基线读与 698 读都在厂内态下才稳
    pre = cmd_bank.month_step_read_row(ser, "基线")                     # 黑盒基线(第五步与它比)
    ctx.J.extend(cmd_bank.month_step_gate(wb, bp_gate=BP_GATE, gate_vars=VARS_GATE))      # ① 风格判定
    # ② 的白盒半边: 普通分钟步进里月支写点不该命中(那趟固件在 :917 就 return)
    ctx.J.extend(cmd_bank.month_step_neg(wb, bp_month=BP_MONTH, write_vars=VARS_MONTH))
    # ②③ 第一趟: 停 :887 注入月形态 ⇒ 月支写点应命中, 命中那刻读 frezNum 按月差比
    recs, r2 = cmd_bank.month_step_leg_month(wb, bp_inj=BP_INJ, bp_month=BP_MONTH,
                                       at_vars=VARS_INJ, write_vars=VARS_MONTH,
                                       assigns=cmd_bank.MST_INJ_ASSIGN)
    ctx.J.extend(recs)
    ctx.J.extend(cmd_bank.month_step_judge_freznum(r2))
    # ④ 第二趟: 再注入一次月形态 ⇒ 年支写点仍不该被走到(与年阶梯不混)
    recs4, _r4 = cmd_bank.month_step_leg_year(wb, bp_inj=BP_INJ, bp_year=BP_YEAR,
                                        at_vars=VARS_INJ, write_vars=VARS_MONTH,
                                        assigns=cmd_bank.MST_INJ_ASSIGN)
    ctx.J.extend(recs4)
    post = cmd_bank.month_step_read_row(ser, "⑤ 两趟之后")              # ⑤ 黑盒读回
    ctx.J.extend(cmd_bank.month_step_advance(pre, post, r2 is not None and r2.get("ok") is True))


def _banner():
    return ("== 11-2 月阶梯(与 4-7/11-1 共用 Check_BillFrezY) | 工程=%s 表号=%s ==\n"
            ".. 驱动=自然分钟步进(不发表钟帧); 白盒停 %s(风格判定)/%s(月支写点)/%s(年支写点); "
            "两趟靠停 %s 注入造(月字节回退 + 12B 表写成月形态, 参数区一个字节都不动)"
            % (CURRENT.PROJECT, CURRENT.TABLE_ADDR.hex().upper(),
               breakpoint.text(BP_GATE), breakpoint.text(BP_MONTH), breakpoint.text(BP_YEAR),
               breakpoint.text(BP_INJ)))


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "11-2 月阶梯(月形态走月支/frezNum 按月差/与年阶梯不混/698 读回)",
        cmd_bank.month_step_criteria,
        name="11_2_month_step",
        parts=[("11-2 月阶梯段", part_month_step)],
        gdb=breakpoint, banner=_banner(),
        # ⚠ `inject_allow` 必须在**会话构造时**点名 —— 白名单精确匹配符号名, 事后补不了。
        #   本项那份比 4-7/11-1 多一个 `g_HisTime[4]`(月字节), 别照抄 4-7 的。
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV,
                        inject_allow=cmd_bank.month_step_inject_allow())))
