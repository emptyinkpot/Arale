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
BP_GATE = ("TaskFreeze.c", 875)           # 风格判定: 判定体本身, 本行无调用(同 4-7) @TaskFreeze.c:875
VARS_GATE = ("TAB_MeterSty.style",)       # 那刻的风格值(判据①的硬证)
BP_INJ = ("TaskFreeze.c", 887)            # 注入停点 `dateNum = 0;`: 本行无调用(同 4-7) @TaskFreeze.c:887
# 停点处的**注入前对照量**(证注入确实改了状态)。⚠ **不收 `flag`**: 位置表说它在 `$sp-124` 可读,
# 但 :887 那一停它还没被赋过 —— 声明 `BOOL flag = OTHER;` 在 :871, 真值要到 :895/:903 才写,
# 读回来恒是那个占位值 ⇒ 它做对照量**不带任何信息**(断点体检会照实点出来)。
VARS_INJ = ("g_CurTime", "g_HisTime", "buff")
BP_OVER = ("TaskFreeze.c", 1008)          # `if (over == TRUE)`: 结转趟与超档趟**两趟都要停** —— 行首在 `cmp over,#170` 之前, 换到附近那条 Sch_FrezObj 调用会只剩 TRUE 那一趟 @TaskFreeze.c:1008
VARS_OVER = ("frezNum", "buff")
BP_CLEAR = ("prev", "Check_BillFrezY", "Set_Data", 1)        # 清零句 `Set_Data(&buff[off], 0x00, 4);`, off 是这次调用的实参 @TaskFreeze.c:1012
VARS_CLEAR = ("frezNum", "buff", "off")   # `off` 在 `$r0`, 位置区间 0x33206-0x33210 ⇒ 这一停可读
BP_MONTH = ("prev", "Check_BillFrezY", "Write_FrezData", 1)  # 月支写点 @TaskFreeze.c:1041
VARS_MONTH = ("frezNum", "buff")
BP_YEAR = ("prev", "Check_BillFrezY", "Write_FrezData", 2)   # 年支写点 @TaskFreeze.c:1075
VARS_YEAR = ("frezNum", "buff")
# ⚠ `frezNum`/`buff` 在 `:1008`/`:1012`/`:1041`/`:1075` 四处都落在同一个可读区间
#   `0x331ec-0x33418` 里(由 `info scope Check_BillFrezY` 的位置表逐区间核过)。
#   **别把 `flag`/`over`/`flg` 加进来** —— 它们在那些断点处是**空洞区间**, 读回来是"读不到",
#   而那看起来像"固件没给值"(取证时会把停错地方误读成固件问题)。
#   ⚠ `frezNum` 是**寄存器驻留**(`$r6`), 只能读、不能按地址写回。

# ---- 判过汇总(纯数据; 每条判据的文字 / crit / falsify 都住这儿, 库不替本子项起名) ----
FALSIFY = {
    "①": "风格判定方向反了(本台实为 TP_Remote) ⇒ 判定体在 :875-878 就 return, `TaskFreeze.c:875` "
         "之后一条指令都不执行, 年阶梯这一路永不执行",
    "②": "边界判定无条件成立 ⇒ 没有跨结算边界也落记录: 写点 :1041/:1075 在普通分钟步进里命中, "
         "或 698 的阶梯结算冻结记录(子类 0x11)记录序号自己推进",
    "③": "未超档那趟也被清零(把 `frezNum != 0` 当成了『要清』) ⇒ 记录尾部那一列被抹平, "
         "写在 :1008 那一停的剂量特征在记录里找不到",
    "④": "超档那趟不清零 ⇒ 或清零点 `TaskFreeze.c:1012` 不被走到, 或走到了却清的不是那一列"
         "(记录尾部仍带着剂量)",
}
LABEL_GATE = "① 风格判定那个断点(TaskFreeze.c:875)停到 + style==TP_Local"
LABEL_ADV = "② 没有跨结算边界 ⇒ 阶梯结算冻结记录不推进"
LABEL_CARRY = "③ 未超档那趟(`over == FALSE`)档位电量结转"
LABEL_OVER = "④ 超档那趟(`frezNum > frezAdd`)清零被执行且记录那一列被清零"


def part_year_step(ctx):
    """11-1 的全部条目 —— **一行一步, 顺序即步骤**: 每行都是库 11-1 段里的一个积木
    (`CB.year_step_*`); 读动词返回数据, 判据动词返回证据记录, 由 `ctx.J.extend` 收账。
    每个积木自己打印它读了什么、比了什么 —— 这一列注释就是全部流程, 不必再翻库。"""
    ser = ctx.ser
    ctx.session()                          # 观测一律 attach(**禁用 launch**: launch 复位表、RAM 清零)
    # 整片 .out 解一次(要停核遍历, 内部按块喂狗, 收尾放行) —— 之后 `ctx.bp()` /
    # `inject_anchor()` / `decision_anchor()` 一律查这张图, 谁都不再独立反汇编。
    if ctx.g is not None:
        gdbinit.build(ctx.g)
    g = ctx.g
    # 递**断点元组**而不是已挂好的 bpno: 库原语见到元组都会自己挂、**命中与没命中两条路都撤**。
    wb = {"arm": lambda a: ctx.bp(a),
          "wait": partial(breakpoint.wait_hit, g),
          "neq": partial(breakpoint.expect_no_hit, g),
          "inj": partial(breakpoint.inject_hit, g)} if g is not None else None
    cmd_bank.year_step_intro(wb is not None, ctx.waived)              # 开场: 本段在造什么
    cmd_bank.enter_factory(ser)                                       # 发送 → 管理芯: 进厂内(记录读回受安全判定管)
    pre = cmd_bank.year_step_read_row(ser, "基线")                     # 读: 记录最新一条(② 与它比推进)
    base_raw = cmd_bank.year_step_read_raw(ser, "基线")                # 读: 那一列原始字节(③④ 与它比剂量)
    # ② 白盒半边: 普通分钟步进里两个写点都不该命中(窗口 70s, 必然含一个自然分钟步进)
    ctx.J.extend(cmd_bank.year_step_neg(wb, bp_month=BP_MONTH, bp_year=BP_YEAR, write_vars=VARS_MONTH,
                                  crit="②", falsify=FALSIFY["②"]))
    # ① 风格判定方向: 等 :875 自然命中, 读那刻的 TAB_MeterSty.style
    ctx.J.extend(cmd_bank.year_step_gate(wb, bp_gate=BP_GATE, gate_vars=VARS_GATE, crit="①",
                                   falsify=FALSIFY["①"], label=LABEL_GATE))
    mid = cmd_bank.year_step_read_row(ser, "普通分钟步进走完")          # ② 黑盒半边: 没推进才算达成
    ctx.J.extend(cmd_bank.year_step_advance(pre, mid, crit="②", falsify=FALSIFY["②"], label=LABEL_ADV))
    # ③ 结转那趟: 停 :887 注入年份 −1 ⇒ 等 :1008 那一停写非零剂量
    # ⚠ 为什么非注入不可: 跨年边界帧通道造不出(没有口能写 `g_HisTime`, 也没有口写记录正文那一列);
    #   本台那一列当前值是 0, 源为 0 时「结转」与「清零」写出来一模一样 ⇒ 光读记录分不开。
    # ⚠ 剂量只能写在 :1008 那一停 —— :1004 `Prep_ObjData` 会把记录正文重新填一遍, 写在 :887 会被它覆盖。
    recs, r3 = cmd_bank.year_step_carry(wb, bp_inj=BP_INJ, bp_over=BP_OVER, at_vars=VARS_INJ,
                                  write_vars=VARS_MONTH, assigns=cmd_bank.BFY_INJ_ASSIGN,
                                  dose=cmd_bank.YST_DOSE_ASSIGN, crit="③", falsify=FALSIFY["③"],
                                  label=LABEL_CARRY)
    ctx.J.extend(recs)
    ctx.J.extend(cmd_bank.year_step_judge_carry(ser, r3, base_raw, crit="③", falsify=FALSIFY["③"],
                                          label=LABEL_CARRY))
    # ④ 超档那趟: 停 :887 注入年份 −5 ⇒ 等清零句 :1012 命中
    # ⚠ 不另开一个「清零点未命中」的否定期望: `inject_hit` 在 :1008 写完剂量就放行了, 那时 :1012 的
    #   断点还没挂上 ⇒ 那种窗口必然「超时达成」, 是静默无效的。改用等价的黑盒读数 —— 唯一能抹掉那
    #   4 个字节的地方就是 :1012(:1075 只读不写) ⇒ 「记录尾部还找得到剂量」与「:1012 没被执行」同一件事。
    recs4, r4 = cmd_bank.year_step_over(wb, bp_inj=BP_INJ, bp_clear=BP_CLEAR, at_vars=VARS_INJ,
                                  clear_vars=VARS_CLEAR, assigns=cmd_bank.BFY_INJ_ASSIGN,
                                  dose=cmd_bank.YST_DOSE_ASSIGN, crit="④", falsify=FALSIFY["④"],
                                  label=LABEL_OVER)
    ctx.J.extend(recs4)
    ctx.J.extend(cmd_bank.year_step_judge_over(ser, r4, crit="④", falsify=FALSIFY["④"], label=LABEL_OVER))


def _banner():
    return ("== 11-1 年阶梯(与 4-7 共用 Check_BillFrezY) | 工程=%s 表号=%s ==\n"
            ".. 驱动=自然分钟步进(不发表钟帧); 本台 frezAdd=%d(年差 ≥%d 才超档); "
            "白盒停 %s(风格判定)/%s(月支写点否定期望)/%s(年支写点否定期望); "
            "两趟靠停 %s 注入造(年字节回退 + 12B 日期表写在栈上 `buff`, 参数区一个字节都不动), "
            "剂量写在 %s 那一停(等 %s 命中)"
            % (CURRENT.PROJECT, CURRENT.TABLE_ADDR.hex().upper(), cmd_bank.BFY_FREZ_ADD, cmd_bank.BFY_FREZ_ADD + 1,
               breakpoint.text(BP_GATE), breakpoint.text(BP_MONTH), breakpoint.text(BP_YEAR),
               breakpoint.text(BP_INJ), breakpoint.text(BP_OVER),
               breakpoint.text(BP_CLEAR)))


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "11-1 年阶梯(未超档结转/超档清零/698 读回那一列)",
        cmd_bank.year_step_criteria,
        name="11_1_year_step",
        parts=[("11-1 年阶梯段", part_year_step)],
        gdb=breakpoint, banner=_banner(),
        # ⚠ `inject_allow` 必须在**会话构造时**点名 —— 白名单精确匹配符号名, 事后补不了。
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV,
                        inject_allow=cmd_bank.year_step_inject_allow())))
