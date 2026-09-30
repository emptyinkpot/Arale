# -*- coding: utf-8 -*-
import time

from common import judge            # 观测种类常量(本子项全部证据标 DEBUG)
from common import trial            # 运行外壳: argv/日志/账本/串口/会话/收尾三连/退出码
from meterlib import ble           # 蓝牙透传通道(与 portsel.RawCom 同形; 本项只走它发帧)
from meterlib import cmd_bank       # 帧目录 + 语义动词积木箱(每步自带打印)
from project import CURRENT          # 本工程画像(库不认表, 只有脚本认)
from swdbg import breakpoint            # 断点级白盒; 没接 J-Link 会自动降级
from swdbg import gdbinit            # 整片 .out 地图: 开会话后解一次, 之后断点/注入点只查它

# ---- 断点观测参数(纯数据) ----
# `BP_<X>` ↔ `VARS_<X>` 是承重的(scripts/_check_anchors.py 靠它配对): `BP_<X>` 必须是**核过断点**的行,
# `VARS_<X>` 列**真正读的那几个变量**(只列顶层名)。
BP_POST = ("prev", "Analyse_698Prot", "Post_Message", 1)           # 蓝牙口那一支里投通信唤醒消息那一句(`if (port == PT_BLE_M)` 内)
VARS_POST = ("port",)                     # 入参 1 个: 这帧归哪条口(命中即蓝牙口时该是 4)

# ---- 本子项的参数(纯数据) ----
LIGHT_WAIT = 1.5        # ① 命中放行后先等它一会儿再回读计时器(那一下是 TurnOnLampLed 之后的几拍)
OFF_EXTRA = 1.0         # ④ 在"判出的时长"之外再多等 1 s, 免得卡在边界上
HIT_WAIT = 3.0          # ① 触发帧发出后等命中的上限(秒)
JOIN_EXTRA = 8.0        # 触发那个后台线程的收尾余量(秒)
DISP_OAD = cmd_bank.DISP_OAD          # 触发帧读的对象: 40000200 = 表钟(与 7-1 同一份常量, 不另起一份)
BLE_PORT = cmd_bank.DISP_PORT_BLE     # 这帧在管理芯里算哪条口进来 —— 必须与发帧走的那条对上, 否则 ① 停不下来

# ---- 每条判据的 falsify(纯数据, 逐条对着 crit 用) ----
FALSIFY = {
    "①": "蓝牙帧没走到 698 分析口 ⇒ 断点不命中; 端口索引串了 ⇒ port 不是 %d" % BLE_PORT,
    "②": "通信帧那条路没人调 TurnOnLampLed(TaskDisplay.c:3936) ⇒ 计时器一直是 0",
    "③": "置上的是别的秒数 ⇒ 计时器不等于 g_DispPara[SlctT]×C_SysTick(64)",
    "④": "INT_LampLedTicker(TaskDisplay.c:3921) 没在递减 ⇒ 到时回读仍非 0",
    "⑤": "唤醒只投了消息没换显示状态 ⇒ 回读仍是 ST_StopDisp",
}

# ---- 半途中止时要一次记全的条目(纯数据; 与正常路径同名同号, 少记一条 = 分母变小)----
ENTRIES = (
    ("①", "通信唤醒的投递点被执行"),
    ("②", "背光计时器被置上"),
    ("③", "亮显时长等于该表参数"),
    ("④", "到时熄灭"),
    ("⑤", "液晶从息屏态被唤醒"),
)


def _stop_unproven(ctx, why):
    """半途中止的统一收场: 条目一次记全(`ok=None`) → 出声 → 停住(退 2, 不是失败)。"""
    ctx.J.extend(cmd_bank.lamp_unproven(ENTRIES, FALSIFY, why))
    ctx.J.note("8-7 通信唤醒点亮背光段: 半途中止(这一次没做成) —— %s" % why)
    raise trial.Stop("%s ⇒ 未定论(退 2), 不是失败" % why)


def part_lamp_wake(ctx):
    """8-7 的 ①~⑤: 蓝牙口发一帧 698 读表钟 → 停在唤醒投递点 → 放行后回读计时器 → 到时再回读。

    ⚠ 观测一律 attach, **禁用 launch**(launch 会复位表, RAM 态清零)。
    ⚠ 断点是**发帧触发**、`with_trigger` 当场挂当场撤(库内) —— **不是** `ctx.bp()` 预挂:
      预挂上又没人等在等命中时, 它自己会把核撂停, 其后每条串口帧整帧无应答(与"表死机"一模一样)。
    ⚠ 链路由**本段自己开关**: 模组空闲 60 s 内会掉线, 所以不带跨段的常开链路
      (`BleLink.write` 每次发帧前现连, 见 `meterlib/ble.py` 模块头)。
    """
    link = ble.open_link()
    ctx.session()                               # attach 进会话; 没接 J-Link → None(白盒那几条记"没做成")

    if ctx.g is not None:
        # 整片 .out 解一次(停核遍历, 内部喂狗, 收尾放行) —— 之后一律查这张图
        gdbinit.build(ctx.g)
    sess = ctx.g
    try:
        cmd_bank.lamp_intro(sess is not None)         # 开场: 发哪条口、触发帧长什么样、本次有没有会话
        if sess is None:
            _stop_unproven(ctx, "本次没有断点会话(没接 J-Link) ⇒ 判①②③④⑤ 没做成")

        # ---- 基线: 发帧前的 u32LampTicker / g_DispStatus / g_DispPara[SlctT] ----
        b0 = cmd_bank.lamp_read_base(sess)            # ②③要拿它排除"上一次亮显还没走完", ⑤要拿它判息屏前置
        if b0 is None:
            _stop_unproven(ctx, "发帧前的基线没读到(叫停/读那一步抛了) ⇒ 判②③⑤ 没做成")

        # ---- 第①步: 蓝牙口发一帧 698 读表钟, 并发中停在投递点 DLT698Link.c:324 ----
        r1 = cmd_bank.lamp_trigger_post(sess, link, BP_POST, oad=DISP_OAD, port=BLE_PORT,
                                  timeout=HIT_WAIT, join=JOIN_EXTRA)
        if (r1 or {}).get("hit") is None:
            _stop_unproven(ctx, "触发帧发出后 %.1fs 内没等到 %s 命中"
                           % (HIT_WAIT, breakpoint.text(BP_POST)))
        ctx.J.add("① 通信唤醒的投递点被执行",
                  *cmd_bank.lamp_judge_post(r1, BP_POST, BLE_PORT, HIT_WAIT),
                  crit="①", obs=judge.DEBUG, falsify=FALSIFY["①"])

        # ---- 第②步: 放行后等 LIGHT_WAIT 秒, 回读计时器与显示状态 ----
        m1 = cmd_bank.lamp_read_after_wake(sess)
        tick0, tick1 = b0["tick"], None if m1 is None else m1["tick"]
        st0, st1 = b0["stat"], None if m1 is None else m1["stat"]
        ctx.J.add("② 背光计时器被置上", *cmd_bank.lamp_judge_ticker(tick0, tick1, LIGHT_WAIT),
                  crit="②", obs=judge.DEBUG, falsify=FALSIFY["②"])
        ctx.J.add("③ 亮显时长等于该表参数", *cmd_bank.lamp_judge_duration(tick1, b0["slct"]),
                  crit="③", obs=judge.DEBUG, falsify=FALSIFY["③"])
        ctx.J.add("⑤ 液晶从息屏态被唤醒", *cmd_bank.lamp_judge_disp(st0, st1),
                  crit="⑤", obs=judge.DEBUG, falsify=FALSIFY["⑤"])

        # ---- 第③步: 到时熄灭 —— ② 没置上就无从谈起(与判②同一支), 那一支不空等 ----
        if tick1 in (None, 0):
            ctx.J.add("④ 到时熄灭", *cmd_bank.lamp_judge_off(tick1, None, b0["slct"], 0.0),
                      crit="④", obs=judge.DEBUG, falsify=FALSIFY["④"])
        else:
            hold = max(0.0, float(b0["slct"]) - LIGHT_WAIT) if b0["slct"] is not None else 0.0
            time.sleep(hold + OFF_EXTRA)                        # 等满"③ 判出的时长 + 1 s"
            ctx.J.add("④ 到时熄灭",
                      *cmd_bank.lamp_judge_off(tick1, cmd_bank.lamp_read_tick(sess), b0["slct"],
                                         LIGHT_WAIT + hold + OFF_EXTRA),
                      crit="④", obs=judge.DEBUG, falsify=FALSIFY["④"])
    finally:
        # 兜底撤断点 —— 命中那一路 `with_trigger` 已自撤, 这里防"已命中但后面某步抛了"的残局;
        # 留着断点它自己会把核撂停, 于是其后每条串口帧整帧无应答(与"表死机"一模一样)。
        cmd_bank.lamp_clear_breaks(ctx.g)
        link.close()


def _banner():
    return "== 8-7 显示功能·通信唤醒点亮背光 | 工程=%s 表号=%s | 蓝牙口 %s ==" % (
        CURRENT.PROJECT, CURRENT.TABLE_ADDR.hex().upper(), ble.MODULE_ADDR)


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "8-7 显示功能·通信唤醒点亮背光", cmd_bank.lamp_wake_criteria,
        name="8_7_lamp_wake",
        parts=[("8-7 通信唤醒点亮背光段", part_lamp_wake)],
        gdb=breakpoint, banner=_banner(),
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV),
        # ①~⑤ 全是**断点观测**: 帧虽是发出去的, 但"这条帧走没走到该唤醒的那一句 / 背光计时器有没有
        #   被置上 / 亮了多久"串口自己答不出 —— 串口只看得见应答, 看不见它走哪条分支。
        obs=(judge.DEBUG,)))
