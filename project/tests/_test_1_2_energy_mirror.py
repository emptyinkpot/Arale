# -*- coding: utf-8 -*-
"""在证什么: 管理芯存进 g_CurkWh 的电能与计量芯送来的值、与 698 报出去的数对不对得上;
   以及同一张表在"清完零正常跑"与"断电 ≥5s 重启"两种状态下的差别。
会向表写什么: 645 进厂内 + 645 电表清零(**不可逆**: 台面累计电量/需量/冻结一起归零),
   外加一次**人工断电**(脚本只等, 不自己复位); 帧全部走库的帧目录, 没有一处现拼。
跑法: `python project/tests/_test_1_2_energy_mirror.py`(真串口 + 真探针; 跑到清零后会打提示, 按提示断电 ≥5 秒)
结论怎么读: 账本末行「判据: 满足 n/N」, N=11; 退 0 = 通过 / 1 = 失败 / 2 = 未定论。
"""
import time

from meterlib import cmd_bank       # 帧目录 + 语义动词积木箱(每步自带打印/判 PASS·FAIL)
from meterlib import watch           # AA80 读内存(本脚本显式读的那一步就用它)
from common import trial           # 运行外壳: argv/日志/账本/串口/会话/收尾三连/退出码
from project import CURRENT          # 本工程画像(库不认表, 只有脚本认)
from swdbg import breakpoint            # 断点级白盒; 没接 J-Link 会自动降级
from swdbg import gdbinit            # 整片 .out 地图: 开会话后解一次, 之后断点/注入点只查它

WAIT = 3.0                               # 每次 AA80 直读等应答的上限(秒)

# ---- 子项参数(纯数据) ----
# 判据③要对拍的是"698 读回 == 存值", 故必须挑**在 g_CurkWh 里有行**的电类(见 CB.CURKWH_OAD_ROW)。
# 画像那张表里 10 行都齐(行 0..9 各一个 OAD), 所以 698 对拍直接按它取全 —— 不挑、不漏。
# `00000400`(组合有功)在 g_CurkWh 里没有对应行(固件按组合方式现算) ⇒ 它只服务判据⑤, 不入③④。
OADS = tuple(cmd_bank.CURKWH_OAD_ROW) + (cmd_bank._ENE_COMB_FULL_OAD,)
N_ROUNDS = 3                                            # 判据⑤「多帧」的轮数

# ---- 清零 / 掉电段参数(纯数据) ----
SETTLE = 8.0        # 清零后 / 上电后先等这么久 —— 让计量帧与费率分摊各跑过几轮, 再看表里的值
POWER_WAIT = 300.0  # 等"表消失又回来"的总时长上限(秒), 超了 B 半记未证
POLL = 2.0          # 断电探测的轮询间隔(秒)

# ---- 断点观测参数(纯数据) ----
# `BP_<X>` ↔ `VARS_<X>` 是承重的(scripts/_check_anchors.py 靠它配对): `BP_B` 必须是**核过断点**的行。
BP_B = ("call", "Update_Rate_Energy", "Get_RateNo", 1)   # `rate_num = Get_RateNo()` 调用点 @kWhData.c:448(`bl` 之后那条指令); 此断点的取舍见库 1-2 段头
# 本种观测**真正"读的变量"只有这一个**(Run_TaskVessel 的静态局部, :987/:1022) —— 其余表达式都是
# 从它算出来的项地址(帧内每类 9 字节 / g_CurkWh 每 13 项一组的『总』项), 项地址换算是协议知识, 已收进库
# `CB.kwh_wb_vars()`, 故这里只列变量名, 完整表达式表另走 `WB_VARS_B`(生成物, 31 条)。
VARS_B = ("'Run_TaskVessel'::STR1_Index",)
WB_VARS_B = cmd_bank.kwh_wb_vars()


def _read_curkwh(ser):
    """AA80 直读整块 `g_CurkWh` → 原始字节, 或 None(读不成)。

    ⚠ 1300B 要分片读(AA80 单次负载 ≤128B), 所以这里是**逐片拼接的非原子快照**; 片长与总长由库规划
      (`cmd_bank.kwh_store_plan`, 地址从画像来, 脚本不摸地址); 任一片没答 0x91 就返回 None,
      不拿半截数据凑结论。解码走 `cmd_bank.energy_mirror_store_decode`。
    """
    plan = cmd_bank.kwh_store_plan()
    if not plan:
        print("   [白盒] 画像里没有 g_CurkWh 的地址 ⇒ 这一块读不成")
        return None
    addr, size, chunk = plan
    buf = bytearray()
    while len(buf) < size:
        n = min(chunk, size - len(buf))
        verdict, hexs = watch.read_mem_aa80(ser, 1, addr - watch.RAM_BASE + len(buf), n, wait=WAIT)
        if verdict != "PASS":
            return None
        buf += bytes.fromhex(hexs.replace(" ", ""))
    return bytes(buf)


def _aa80_alive(ser):
    """表还应不应答 —— 只取 g_CurkWh 开头 4 个字节探一下(poll 用, 不能像整块读那样发 11 帧)。"""
    plan = cmd_bank.kwh_store_plan()
    if not plan:
        return False
    addr = plan[0]
    verdict, _hexs = watch.read_mem_aa80(ser, 1, addr - watch.RAM_BASE, 4, wait=1.5)
    return verdict == "PASS"


def _wait_power_cycle(ser):
    """等操作员把表断电再上电 —— **要看到"表消失过又回来"才算数**, 不听"我断过了"这句自述。

    判据是 AA80 读的成败: 先要连续 3 次读不成(那段窗口里表是停的), 再要一次读成(表回来了)。
    超时返回 False ⇒ B 半那两条记未证(不冒充"重启过")。
    ⚠ 只断表的电, USB 桥别拔 —— 桥掉了串口句柄就废了, 这会一直读到失败, 只能记未证收场。
    """
    print("\n   【请操作员动手】把电表的电断掉, 停 ≥5 秒再上电(只断表的电, 别拔 USB 线)。")
    print("   脚本在这里等 —— 必须先看到表连续 3 次不应答(证明真断了, 不是表还活着), 再看它应答回来。")
    t0 = time.time()
    miss, gone = 0, False
    while time.time() - t0 < POWER_WAIT:
        if _aa80_alive(ser):
            if gone:
                print("   [上电] 表回来了(第 %.1fs 处重新应答) —— 上电这一步看到了" % (time.time() - t0))
                return True
            miss = 0
        else:
            miss += 1
            if miss == 3 and not gone:
                gone = True
                print("   [断电] 表连续 3 次不应答(第 %.1fs 处) —— 断电这一步看到了" % (time.time() - t0))
        time.sleep(POLL)
    print("   [未证] %.0f 秒内没等到「表消失又回来」 —— B 半那两条记未证" % POWER_WAIT)
    return False


def part_mirror(ctx):
    """1-2 的第一段 —— **一行一步, 顺序即步骤**: 每行都是库 1-2 段里的一个积木
    (`CB.energy_mirror_*`); 读动词(带 ser 的)返回数据, 判据动词返回证据记录, 由 `J.extend` 收账。
    唯一的例外是存储侧底账那次 AA80 直读 —— 它由本脚本的 `_read_curkwh(ser)` 自己做(裸 `watch.*`),
    库只给读法规划 `kwh_store_plan()` 与纯解码 `energy_mirror_store_decode(blob)`。
    每个积木自己打印它读了什么、比了什么、判 PASS/TBD/FAIL —— 这一列注释就是全部流程, 不必再翻库。"""
    ser = ctx.ser
    cmd_bank.energy_mirror_intro()                                     # 开场: 在比什么 / g_CurkWh 长相 / 单位换算
    cmd_bank.enter_factory(ser)                                        # AA80 与 698 读都在厂内态下才稳
    ctx.session()                                                # 开调试会话(断点观测); 没接 J-Link → None

    if ctx.g is not None:
        # 整片 .out 解一次(停核遍历, 内部喂狗, 收尾放行) —— 之后一律查这张图
        gdbinit.build(ctx.g)

    # ⑤ 多帧稳定: 每个 OAD 连读 n 轮整列(698 普通 GET), 再逐字节比各轮是否相同
    cols = cmd_bank.energy_mirror_multiframe(ser, oads=OADS, n_rounds=N_ROUNDS)
    ctx.J.extend(cmd_bank.energy_mirror_stable(cols, oads=OADS, n_rounds=N_ROUNDS))

    # 存储侧底账(AA80 直读整块 g_CurkWh —— 读在本脚本, 解码在库): 读不到返回 None ⇒ 下面三条各自记
    # 「没做成」, 不拿空数据凑
    slots = cmd_bank.energy_mirror_store_decode(_read_curkwh(ser))
    ctx.J.extend(cmd_bank.energy_mirror_crc(slots))                            # ② 10 个总项 CRC 与值自洽
    ctx.J.extend(cmd_bank.energy_mirror_alloc(slots, cols, oads=OADS))         # ④a/④b 费率分摊的前提与等式
    ctx.J.extend(cmd_bank.energy_mirror_compare(slots, cols, oads=OADS))       # ③a/③b 698 读回 vs 存值

    # ① 来值 → 存值: **只有断点观测给得出**(AA80 读到的只是存值, 拿它当"来值"是假证据)。
    # ⚠ 断点**不预先下** —— 传 tuple 让 `with_trigger` 一次一挂、用完必撤 ⇒ 这里用 `ctx.anchor()`,
    #    **不是** `ctx.bp()`(那个是当场挂上, 给"断点跨几次复用"的用法)。
    # `kWhData.c:448` 是**高频行**(每个计量帧都过, 约 1 Hz): 预先挂上它而没人等在等命中 ⇒ 它自己
    # 把核撂停 ⇒ 其后每条串口帧整帧无应答, 表象与"串口坏了/表死机"一模一样。
    ctx.J.extend(cmd_bank.energy_mirror_fore(ser, trig=ctx.trig(),
                                       bp=ctx.anchor(BP_B), wb_vars=WB_VARS_B))


def part_clear_power(ctx):
    """1-2 的第二段 —— **一次跑完的 A/B 对照**:

      A 半 · 645 电表清零后正常跑  → 分摊功能在**干净态**上成不成立(⑥a/⑥b)
      B 半 · 同一跑次断电 ≥5s 再上电 → 同一张表在**掉电重启后**还成不成立(⑦a/⑦b)

    两半同一把尺子(与 ④ 同一份算式 `kwh_alloc_scan`), 差别只在"掉电"这一个动作上 ——
    **A 满足而 B 不满足, 就是这一步造成的**。两半各自成不成立也照实记账, 不做推断。

    ⚠ **顺序是承重的**: 每一态都**先读 698 整列、再 AA80 直读 RAM**。698 那一路经
      `Read_CurkWh → Get_CurkWh` 会把校验码不配对的格从 EEPROM 修回来(那正是"表正常跑过一遍"的样子);
      先读 RAM 会把"还没被任何正常路径碰过"的中间态当成稳定态。
    """
    ser = ctx.ser
    cmd_bank.enter_factory(ser)

    # ---- A 半 ----
    pre = cmd_bank.energy_mirror_store_decode(_read_curkwh(ser))       # 清前基线(旁证, 不作判据)
    cmd_bank.clear_meter(ser)                                          # ⚠ 不可逆: 台面累计电量/需量/冻结一起归零
    imm = cmd_bank.energy_mirror_store_decode(_read_curkwh(ser))       # 清完当场(旁证)
    time.sleep(SETTLE)                                                 # 让计量帧与分摊跑过几轮
    cols_a = cmd_bank.energy_mirror_multiframe(ser, oads=OADS, n_rounds=N_ROUNDS)
    a_slots = cmd_bank.energy_mirror_store_decode(_read_curkwh(ser))
    ctx.J.extend(cmd_bank.energy_mirror_cleared_evidence(pre, imm, a_slots, cols_a, oads=OADS))

    # ---- B 半: 掉电重启(人工断电, 脚本只等) ----
    if not _wait_power_cycle(ser):
        ctx.J.extend(cmd_bank.energy_mirror_restart_evidence(a_slots, None, None, oads=OADS))
        return
    cmd_bank.enter_factory(ser)                                        # 上电后厂内态要重新进
    time.sleep(SETTLE)
    cols_b = cmd_bank.energy_mirror_multiframe(ser, oads=OADS, n_rounds=N_ROUNDS)
    b_slots = cmd_bank.energy_mirror_store_decode(_read_curkwh(ser))
    ctx.J.extend(cmd_bank.energy_mirror_restart_evidence(a_slots, b_slots, cols_b, oads=OADS))


def _banner():
    return "== 1-2 电能数据·与计量芯保持一致 | 工程=%s 表号=%s ==" % (
        CURRENT.PROJECT, CURRENT.TABLE_ADDR.hex().upper())


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "1-2 电能数据·与计量芯保持一致", cmd_bank.energy_mirror_criteria,
        name="1_2_energy_mirror",
        parts=[("1-2 镜像段", part_mirror), ("1-2 清零与掉电段", part_clear_power)],
        gdb=breakpoint, banner=_banner(),
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV)))
