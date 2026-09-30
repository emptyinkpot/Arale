# -*- coding: utf-8 -*-
import datetime
from functools import partial


from meterlib import cmd_bank       # 帧目录 + 语义动词积木箱(每步自带打印/判 PASS·FAIL)
from common import trial           # 运行外壳: argv/日志/账本/串口/会话/收尾三连/退出码
from project import CURRENT          # 本工程画像: 表号/.out 路径/喂狗点(库不认表, 只有脚本认)
from swdbg import breakpoint            # 断点级白盒(停核 + 读函数内局部量); 没接 J-Link 会自动降级
# 整片 .out 地图: 开会话后解一次, 之后断点/注入点只查它(见 swdbg/gdbinit.py 文件头)
from swdbg import gdbinit

# ---- 断点观测参数(纯数据; `BP_<X>` ↔ `VARS_<X>` 这对命名是承重的, 见模板头) ----
BP_B = ("prev", "Save_DateTime_Data", "Set_MeterTime", 1)   # `Set_MeterTime(objtime)` 调用点 @TaskTime.c:1087(`bl` 之前一条 —— 实参 `objtime` 的最后一次可读处); 已核: 与旧行锚点 `("TaskTime.c", 1087)` 同址 0x28BB6
VARS_B = ("objtime",)              # 要写进管理芯的那个时刻(局部, 活跃区间含 0x28bb6)


def part_sync(ctx):
    """②③④ 一轮取全部条目: 两根触发通道(帧 / 注入)与两种观测共用同一段判据代码。"""
    cmd_bank.enter_factory(ctx.ser)                      # 凡写参/动作帧, 先 645.factory 进厂内
    # ⚠ 观测一律 attach, **禁用 launch**(launch 会复位表, RAM 态清零)。
    # ⚠ ③ 判据要注入 —— 写入口必须在**会话构造时**点名(`inject_allow`, 见本文件末尾的 `session_kw`),
    #   这是库的设计: "要注入什么"在开会话前就定死, 不能被跑到一半的脚本临时放宽。
    ctx.session()
    # 整片 .out 解一次(要停核遍历, 内部按块喂狗, 收尾放行) —— 之后 `ctx.bp()` /
    # `inject_anchor()` / `decision_anchor()` 一律查这张图, 谁都不再独立反汇编。
    if ctx.g is not None:
        gdbinit.build(ctx.g)
    # 触发通道两根轴(与"观察观测"正交, 见 CLAUDE.md「两种观测」):
    #   ② 走**帧**  —— `hit_bp` = 期望**命中**(跟随支真被走到);
    #   ③ 走**注入** —— 两个方向各一次: `inject_bp`(否定期望) / `inject_hit_bp`(期望命中)。
    # ⚠ `hit_bp` 没会话时**触发照发、返回 None**(降级只能降白盒, 不能降黑盒);
    #   而两个 inject_* 没会话时返回 None 记"没做成"(注入本身就是白盒那一半, 没有黑盒替身)。
    #   三者都不要在脚本里判空 —— 库已经写好了。
    bp = {"follow": BP_B, "vars": VARS_B}
    # ③ 的**判定断点**: 由 `.out` 反汇编现推(`Save_DateTime_Data` 里 `call Diff_Secs` 之后的
    # 第一条条件分支 = `cmp r0,#2` 后面那条 `bcc`)。**地址不出现在本脚本里** —— 它是推出来的,
    # 并随证据一起打印。没有会话时给 None, ③ 的四次据实记"没做成"。
    decide = cmd_bank.resolve_decide(ctx.g)
    ctx.hold(*cmd_bank.clock_sync_evidence(
        ctx.ser,
        hit_bp=partial(breakpoint.fire_hit, ctx.g),
        inject_bp=partial(breakpoint.inject_decide, ctx.g),   # ③ 四次共用(期望哪支由 follow= 给)
        decide=decide,
        # ③a 的不停核窗口**进窗前**放行核心: 上一段注入把它停在停核态, 不放行的话
        # 管理芯钟不走(软件走时)⇒ 窗口整段是个"冻住的读数", 什么都判不出来。
        # 没会话(`ctx.g is None`)时给 None ⇒ 库不调它(纯串口观测本来也没有停核)。
        # ⚠ `ensure_running` 是**会话方法**(不是模块级函数), 故这里取的是**绑好的** `ctx.g.ensure_running`;
        #   写成 `partial(GD.ensure_running, ctx.g)` 会在**调用期**炸 AttributeError(离线 ast 看不见)。
        release=(ctx.g.ensure_running if ctx.g is not None else None),
        bp=bp, wb_waived=ctx.waived), "2-1 同步段")


def cleanup_clock(ctx):
    """收尾段: 拨回**真实时间**(低风险; 拨完表钟就停在当下, 不用再人工校)。

    ⚠ 拨回这一次**必须照样发** —— ② 已经把计量芯钟拨到 +30s 了, 万一 ③ 那段抛出来
      (注入表达式写错就是当场炸, 那正是本仓要的行为), 不拨回的话表钟就停在"未来 30 秒",
      留给下一次跑一个**看着完全合理的错前提**。
    """
    try:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print("   -- 收尾: 把计量芯钟拨回真实时间 %s(管理芯自动跟随) --" % now)
        cmd_bank.set_meter_clock_set(ctx.ser, now, chip="计量芯")
    except Exception as exc:
        print("   !! 收尾拨钟失败(%r)—— 表钟可能停在拨偏后的时刻; "
              "收尾请跑 python scripts/_restore_all.py" % (exc,))


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "2-1 同步时钟·偏差计量芯钟不大于1s", cmd_bank.clock_sync_criteria,
        name="2_1_deviation_1s", parts=[("2-1 同步段", part_sync)],
        gdb=breakpoint, cleanup=[("收尾拨回真实时间", cleanup_clock)],
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV,
                        inject_allow=cmd_bank.inject_allow_names())))
