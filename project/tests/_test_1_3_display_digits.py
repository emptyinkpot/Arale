# -*- coding: utf-8 -*-
from meterlib import cmd_bank       # 帧目录 + 语义动词积木箱(每步自带打印/判 PASS·TBD·FAIL)
from meterlib import watch           # AA80 读内存(收尾净零核对那一步就用它)
from common import trial           # 运行外壳: argv/日志/账本/串口/会话/收尾三连/退出码
from project import CURRENT          # 本工程画像(库不认表, 只有脚本认)
from swdbg import breakpoint            # 断点观测 + 注入; 没接 J-Link 会自动降级
# 整片 .out 地图: 开会话后解一次, 之后断点/注入点只查它(见 swdbg/gdbinit.py 文件头)
from swdbg import gdbinit

# ---- 子项参数(纯数据) ----
OI = cmd_bank.DISP_OI_HARM                      # 反谐波有功: 本台空载下唯一非零的电类
DOT_POINTS = (0, 2, 4)                    # ① 的三个点: 0/2 在配置范围(TAB_DispParaLmt)内, 4 是注入态
BIG_U64 = cmd_bank.DISP_BIG_U64                 # ②b 造进位用的注入值(见库段头的门槛推算)

# ---- 断点观测参数(纯数据) ----
# `BP_<X>` ↔ `VARS_<X>` 是**承重的**(scripts/_check_anchors.py 靠它配对, 拿去对源码核
# "要读的变量在断点那一行赋过值没有"): 名字写歪 = 这种观测静默地没人核过。
# 这三对**真的驱动本轮的断点**, 经 `disp_digit_dote/borrow_floor/borrow_write(bp_*=…)` 传进去,
# 不是写在这里做样子的(库里的同名常量只当缺省)。
BP_SW = ("TaskDisplay.c", 3137)           # 注入停靠点: switch(g_DispPara[DotE]) 的载入点
VARS_SW = ("u64",)                        # 此断点上可读的实参/局部(:3103 已把 u64 拼好)
BP_END = ("prev", "Disp_Energy", "Decode_MainLine", 2)   # 判据断点: Decode_MainLine(buff, num, dot) @TaskDisplay.c:3199, 即位数定局那一刻
VARS_END = ("dot", "num", "u64")          # :3149/:3188 定 dot, :3189 定 num, :3178-3185 改 u64
BP_SETDP = ("prev", "Set_DispPara", "Fetch_CRC", 1)   # Set_DispPara 内的 Fetch_CRC @TaskDisplay.c:1849(:3171 的借位回写落到这)
VARS_SETDP = ("para", "index")            # 形参: 值由调用方(:3171)给, 断点前无体赋值属正常


def _read_gdispara(ser):
    """AA80 直读 `g_DispPara`(16B) → 原始字节, 或 None(读不到)。解码走 `cmd_bank.disp_gdispara_decode`。

    这一块是**本脚本唯一一次 AA80 直读**(收尾净零核对): ②b 让固件自己写 EEPROM 之后, 读回来对一眼
    看表有没有被改脏。读不成返回 None ⇒ 收尾那一条记「未经核对」, 不拿没读到的当"已净零"。"""
    name = cmd_bank.DISP_VAR
    return watch.watch_vars(ser, [name], tag="收尾 g_DispPara", wait=2.0).get(name)


def part_digits(ctx):
    """1-3 的全部条目 —— **一行一步, 顺序即步骤**: 每行都是库 1-3 段里的一个积木
    (`CB.disp_digit_*`); 读动词(带 ser 的)返回数据, 判据动词返回证据记录, 由 `J.extend` 收账。
    唯一的例外是收尾那次 AA80 直读 —— 它由本脚本的 `_read_gdispara(ser)` 自己做(裸 `watch.*`),
    库只给纯解码 `disp_gdispara_decode(raw)`。
    每个积木自己打印它读了什么、比了什么、判 PASS/TBD/FAIL —— 这一列注释就是全部流程, 不必再翻库。"""
    ser = ctx.ser
    cmd_bank.disp_digit_intro()                                        # 开场: 本项在比什么
    cmd_bank.enter_factory(ser)                                        # AA80 与 698 读都在厂内态下才稳
    ctx.session()                                                # 开调试会话(断点/注入观测); 没接 J-Link → None
    # 整片 .out 解一次(要停核遍历, 内部按块喂狗, 收尾放行) —— 之后 `ctx.bp()` /
    # `inject_anchor()` / `decision_anchor()` 一律查这张图, 谁都不再独立反汇编。
    if ctx.g is not None:
        gdbinit.build(ctx.g)

    # ③ 读数侧量纲(串口观测): 位数对象族标量 GET, 再按 4位==6位//100 / 尾数==6位%100 / 2位==6位//10000 判
    got = cmd_bank.disp_digit_readings(ser, oi=OI, wait=2.0)
    ctx.J.extend(cmd_bank.disp_digit_scale(got))

    # ①②a②b 是 `Disp_Energy` 的函数内局部量 ⇒ **只有断点/注入观测给得出**(外部改参口全注释)。
    # `inj` 是 `with_inject` 的偏函数形状: 没会话时是 None, 三条各自记「没做成」(没有可降级的黑盒替身)。
    inj = ctx.with_inject()
    ctx.J.extend(cmd_bank.disp_digit_dote(inj, dot_points=DOT_POINTS, bp_sw=BP_SW, bp_end=BP_END))
    ctx.J.extend(cmd_bank.disp_digit_borrow_floor(inj, bp_sw=BP_SW, bp_end=BP_END))
    # ⚠ 这一次固件自己会把 Borrow 写进 EEPROM(`Set_DispPara:1850`), 本台没有反向复位口 ——
    #   靠库里的 `then_assigns` 在 `:1849` 改回 0, 让固件亲手写回原值; 收尾再直读核对一次。
    ctx.J.extend(cmd_bank.disp_digit_borrow_write(inj, big_u64=BIG_U64, bp_sw=BP_SW, bp_setdp=BP_SETDP))
    # 收尾净零核对(AA80 直读 g_DispPara —— 读在本脚本, 解码在库)
    ctx.J.extend(cmd_bank.disp_digit_netzero(cmd_bank.disp_gdispara_decode(_read_gdispara(ser))))


def _banner():
    return "== 1-3 电能数据·支持 2,4 位小数与尾数 | 工程=%s 表号=%s ==" % (
        CURRENT.PROJECT, CURRENT.TABLE_ADDR.hex().upper())


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "1-3 电能数据·支持 2,4 位小数与尾数", cmd_bank.disp_digit_criteria,
        name="1_3_display_digits", parts=[("1-3 位数段", part_digits)],
        gdb=breakpoint, banner=_banner(),
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV,
                        inject_allow=cmd_bank.DISP_INJ_VARS)))
