# -*- coding: utf-8 -*-
from functools import partial


from meterlib import cmd_bank       # 帧目录 + 语义动词积木箱(每步自带打印/判 PASS·TBD·FAIL)
from common import trial           # 运行外壳: argv/日志/账本/串口/会话/收尾三连/退出码
from project import CURRENT          # 本工程画像: 表号/.out 路径/喂狗点(库不认表, 只有脚本认)
from swdbg import breakpoint            # 断点级白盒(停核 + 读函数内局部量); 没接 J-Link → None
# 整片 .out 地图: 开会话后解一次, 之后断点/注入点只查它(见 swdbg/gdbinit.py 文件头)
from swdbg import gdbinit

# ---- 断点: 模块级**字面量元组** ----
# `scripts/_check_anchors.py` 用 `ast.literal_eval` 抠 `BP_<X>`/`VARS_<X>` 这两个名字逐条对源码
# (变量在断点那一行赋过值没有); 写成 `CB.SOMETHING` 那种表达式就抠不出来, 等于没人核过。
BP_JUDGE = ("TaskMetering.c", 1898)       # 反向支判据行 `limit = g_EventSet.RevPowerPlower;` @0x2486c
# ⚠ 停在 `:1898` 而不是 5-2 的 `:1882`: 那时**过载支(:1882-1894)已经算完**, 注入只落进反向支。
VARS_JUDGE = ("g_SFlag", "g_PowP[3]", "g_PowQuad", "g_EventSet.RevPowerPlower",
              "g_EventSet.RevPowerDelay", "g_EventSta[24]", "g_EventTmr[24]", "g_EventFlg[24]")
# ⚠ 上面七个量都是**全局量** ⇒ 在 `:1898` 那一停全部可读。它们给的是**注入前**的读数, 用来证明
#   "这一次真把值改掉了"(只看 injects 的话, "注入生效"与"恰好注在原值上"长得一样)。
#   `g_PowQuad` 尤其要看: ④ 证的就是"这一位为 0 时不落库"。
#   ⚠ **不许**把 `limit`/`i`/`sFlag` 加进来 —— `info scope Chk_OverLoad` 说那一停它们没有位置区间
#     (`i` 是空洞), 读回来是"读不到", 而那看起来像"固件没给值"。
BP_CALL = ("prev", "Chk_OverLoad", "Recd_OverLoad", 1)        # Recd_OverLoad 调用点 @0x24aa2(9 个事件共用 ⇒ 必核 `i`)
VARS_CALL = ("i", "delay")
BP_WRS = ("prev", "Recd_OverLoad", "Write_RecdData", 1)         # 「记录开始」写库位置 @0x25f562
VARS_WRS = ("idx", "id", "buff")
BP_WRE = ("prev", "Recd_OverLoad", "Write_RecdData", 3)         # 「记录结束」写库位置 @0x26002
VARS_WRE = ("idx", "id", "buff")
BP_GUARD = ("TaskMetering.c", 3611)       # **守卫行** @0x25edc(`Recd_OverLoad` 与 5-2 同一趟)
VARS_GUARD = ("sta", "id", "buff[3]", "buff[4]", "buff[89]", "buff[90]")
# ⚠ `buff` 是 `INT8U` 数组 —— gdb 按**字符串字面量**印(`"\001\002…"`), 不是 `{1,2,3}`。
#   库里的 `gdb_bytes` 就是为这个单列的(拿 `_gdb_ints` 抠会把八进制当十进制、把可打印字符整个丢掉)。
#   本项**不取电量快照**(9-2 的 ④ 是反向标志位, 不是快照)⇒ `VARS_WRS` 里不带 `g_EngyData`。
# ⚠ `sta` 在 0x25f3a 就死了 ⇒ 两个写库位置的 VARS 里都不能带它。


def part_revpower(ctx):
    """一段 = 9-2 的全部条目: 读门槛 → 稳态/④ 否定期望 → 自然发生 → 恢复 → 跳时发生 → 末段恢复。"""
    # ---- 开调试会话(断点观测)。台面没接 J-Link / 用户指定只做黑盒 ⇒ None, 白盒那几条记"没做成" ----
    # ⚠ 观测一律 attach(**禁用 launch**: launch 会复位表、RAM 态清零); 见 CLAUDE.md 调试链纪律 1。
    ctx.session()
    # 整片 .out 解一次(要停核遍历, 内部按块喂狗, 收尾放行) —— 之后 `ctx.bp()` /
    # `inject_anchor()` / `decision_anchor()` 一律查这张图, 谁都不再独立反汇编。
    if ctx.g is not None:
        gdbinit.build(ctx.g)
    g = ctx.g
    # 递**断点元组**而不是已挂好的 bpno: 库原语见到元组都会自己挂、**命中与没命中两条路都撤**。
    # 传 bpno 则撤不撤只由调用方管, 万一没命中就留在槽里 —— 那是"核被自己撂停、其后串口全哑"的来源。
    # `hit`(`wait_hit`)只在库的 pre-flight 用: 先停一次 BP_JUDGE 读参数区门槛, 好知道
    # `:1923 if (limit != 0)` 这个判定在本台是开着还是关着 —— 关着的话, ④ 那条注入段会
    # "如期"报"没走到"(`inject_miss` 的 ok=True), 那是**假通过**。库据此改记"本台证不了"。
    wb = {"inj": partial(breakpoint.inject_hit, g),
          "neq": partial(breakpoint.expect_no_hit, g),
          "hit": partial(breakpoint.wait_hit, g),
          # ⚠ `hold`(逐拍重注)不是可选项 —— **与 5-2 / 9-3 那两份一字不差**: 三个子项走同一段
          #   固件、同一支「发生」段, 判据条件同样每周期被数据通路刷回原值, 单次注入只成立一拍。
          #   2026-09-18 实测 9-2 / 9-3 都漏过这一行: 那两趟回退到 `inj`, 日志里打印了「本次没给
          #   hold ⇒ 回退到单次注入 … 那不是固件没落库的证据」, 于是 ① 判失败 —— 而它不是固件的账。
          "hold": partial(breakpoint.inject_hold, g)} if g is not None else None
    ctx.hold(*cmd_bank.revpower_roundtrip(
        ctx.ser, wb=wb, wb_waived=ctx.waived,
        bp_judge=BP_JUDGE, bp_call=BP_CALL, bp_wrs=BP_WRS, bp_wre=BP_WRE, bp_guard=BP_GUARD,
        judge_vars=VARS_JUDGE, call_vars=VARS_CALL, wrs_vars=VARS_WRS, wre_vars=VARS_WRE,
        guard_vars=VARS_GUARD),
        "9-2 功率反向事件段")
    # ⚠ 注入表达式表(`RVP_INJ_*`)**不往这里传** —— 它们住库里, 且
    #   `CB.rvp_inject_allow()`(下面 `session_kw` 那句)就是从它们推出来的白名单。
    #   再递一份进来 = 同一件事有两个事实源, 而两者一旦分叉, 白名单会放行一个表里没有的表达式,
    #   `Session.inject()` 当场抛 `GdbError`(精确串匹配), 现象却是"注入没做"。


def _banner():
    return ("== 9-2 测量及监测·功率反向 | 工程=%s 表号=%s ==\n"
            ".. 触发=**注入**(g_PowP 恒 0 + 相启动判定常闭 ⇒ 帧通道造不出反向); "
            "停 %s 一次写 %d 样(PowP[3] 越限 + 置 g_PowQuad 的 bit3 + 清 g_SFlag 的总相位 + 去抖计时), "
            "参数区一个字节都不动; 白盒停 %s(调用点/读 delay)/%s(「记录开始」写库位置)/%s"
            "(「记录结束」写库位置)"
            % (CURRENT.PROJECT, CURRENT.TABLE_ADDR.hex().upper(),
               breakpoint.text(BP_JUDGE), len(cmd_bank.RVP_INJ_ON_FAST),
               breakpoint.text(BP_CALL), breakpoint.text(BP_WRS), breakpoint.text(BP_WRE)))


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "9-2 测量及监测·功率反向(反向判据带标志位 `&&` 那一半/发生走「记录开始」支/"
        "恢复补进上一行/去抖秒数/时标)",
        cmd_bank.revpower_criteria,
        name="9_2_rev_power",
        parts=[("9-2 功率反向事件段", part_revpower)],
        gdb=breakpoint, banner=_banner(),
        # ⚠ `inject_allow` 必须在**会话构造时**点名 —— 白名单精确匹配符号名, 事后补不了。
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV,
                        inject_allow=cmd_bank.rvp_inject_allow())))
