# -*- coding: utf-8 -*-
"""4-6 结算日冻结 —— 按规范四条组织成四步。

  第一步 结算数据   结算日发生时该转存什么、转存到哪、记录是否形成
  第二步 结算边界   什么时间允许转存 + 改结算日是否也触发一次
  第三步 最大需量   首结算日需量复零 / 非首结算日需量补 NULL
  第四步 停电补全   错过结算时刻后能不能补回来

跑法: `python project/tests/_test_4_6_aa80.py`(真串口 + 真探针; `--no-gdb` 只做黑盒)。
结论怎么读: 账本末行「判据: 满足 n/N」; 退 0 = 通过 / 1 = 失败 / 2 = 未定论。
会向表写什么:
  - 645 改写第1结算日(测完写回 d0 号);
  - 第一步 698 Set 40000200 拨到结算日前夜;
  - 第四步-1 698 Set 40000200 往前跨 13 个月, 验证历史补冻上限;
  - 第四步-2 698 Set 40000200 拨到真实停电测试的结算日前时间, **人工断电**跨过结算时刻后重新上电;
  - 收尾段把表钟拨回真实时间、结算日复原成 d0 号。
"""
import datetime
import time


from meterlib import cmd_bank       # 帧目录 + 语义操作(每步自带打印)
from meterlib import watch           # AA80 只读观察簇(串口白盒通路)
from common import judge            # 观测种类常量
from common import trial          # 运行外壳: argv/日志/账本/串口/会话/收尾三连/退出码
from project import CURRENT          # 本工程画像: 变量集/结算日 DI/.out/喂狗点
from swdbg import breakpoint            # 断点级白盒(停核 + 读函数内局部量)
from swdbg import gdbinit            # 整片 .out 地图: 开会话后解一次, 之后断点只查它
from swdbg.probe import Probe        # SWD 直读(读 FLASH 常量表; 不停核)

# ---- 子项参数(纯数据; 要改的在这里改) ----
SUB_SETTLE = 0x05        # 结算冻结(子类 0x05, OAD 50 05 02 00)
WRITE_SLEEP = 1.5        # 0x14 写后等 EEPROM 回落再读回
TIMEOUT_CROSS = 150      # 第一步等自然跨次日00:00 最长秒数
CATCHUP_MONTHS = 13      # 第四步: 拨钟一次往前跨这么多个月(> 12 条上限)
CATCHUP_HIT = 30.0       # 第四步拨钟那一趟等第一次停住的最长秒数
CATCHUP_STEP = 3.0       # 第 2..12 次停住: 循环里两写之间只有几条指令, 短窗口就够
CLOCK_SKEW = 3           # 收尾回拨: 目标 = 本机墙钟 + 这么多秒(要严格晚于"现在")
POWER_CROSS_LEAD = 90    # 真实停电测试: 结算日前预留秒数, 给人工断电操作留窗口
POWER_ON_WAIT = 8        # 上电后等待启动/补冻完成
POWER_OFF_TIMEOUT = 180  # 断电/上电各自的轮询上限(秒)
POWER_MIN_OFF = 120      # 确认断电后**强制**保持断电的秒数(让表内 RTC 跨过结算日 0 点)

# 断点(纯数据) —— 全走**地图锚点**: 地址由 `gdbinit.build(ctx.g)` 建的整片地图查, 不抄行号。
# 字面量元组是 `(写法, 函数, 被调, 第几处)`。
# ⚠ B/C 要的是**这次调用的实参** `usekWh`(马上就写进 ID_MonthUsed) ⇒ `("prev", …)`:
#   实参的最后一次使用就是那条 `bl`, 它的 DWARF 位置恰在 `bl` 那一刻结束 ⇒ 只有 `bl` 之前那条读得到。
BP_B = ("prev", "Check_BillFrezM", "Write_ParaData", 1)    # 结算取数落库 @TaskFreeze.c:823
BP_C = ("prev", "Chg_BillDayM",   "Write_ParaData", 1)     # 改结算日入口 @TaskFreeze.c:851
BP_D = ("prev", "Check_BillFrezM", "Write_FrezData", 1)    # 补冻那条的写库点 @TaskFreeze.c:748
VARS_B = ("usekWh",)                    # :823 上只有它活跃
VARS_C = ("usekWh",)                    # :851 上它是活跃实参(转存月用电量真值)
VARS_D = ("frezNum", "i", "buff", "over")  # frezNum=这一趟补几条 / i=正写第几条 / buff[0..5]=该条时标

# 观察集: 地址/长度单一源 = 当前 .out 的符号表(swdbg.elf), 按名定址 AA80 直读。
VARS = list(CURRENT.WATCH_VARS)          # 表钟锁存副本 + 分钟冻结 FIFO 台账
STABLE_NAMES = list(CURRENT.STABLE_VARS)  # 结算绝不能碰的配置/状态变量(基线→终点快照)
CLOCK_NAMES = list(CURRENT.CLOCK_VARS)   # 时钟派生量: 只观测, 不参与"没野写"的佐证集

OBJ_ROW = 4        # 结算日冻结用的对象表行号(TAB_FrezObj 第 4 行)
DAY_LO = 1         # 第二步: 结算日范围下界(规范: 每月 1 日至 28 日内的整点)
DAY_HI = 28        # 第二步: 范围上界
DAY_BAD = 29       # 第二步: 界外值 —— 必须被拒

# ---- 判据: 规范四条 = 四个阶段, 每条下面挂若干测试点 -----------------------
S1 = "第一阶段：存储上 12 个结算日的各费率电能数据"
S2 = "第二阶段：数据转存分界时刻(每月 1 日至 28 日内整点)"
S3 = "第三阶段：月最大需量"
S4 = "第四阶段：停电错过结算时刻, 上电时应补全 12 个结算日数据"


CRITERIA = {
    # 第一阶段
    "冻结对象表": {"stage": S1, "text": "TAB_FrezObj 第 4 行翻出的 OAD == 出厂表登记的那 16 项"},
    "存储深度": {"stage": S1, "text": "结算冻结的存储深度 == %d(至少存得下 12 个结算日)"
             % CURRENT.BILLFREZ_DEPTH},
    "冻结边界": {"stage": S1, "text": "自然跨结算点生成一条结算冻结(序号恰 +1)"},
    "冻结账期": {"stage": S1, "text": "冻结时标日 == 该结算日"},
    "冻结快照": {"stage": S1, "text": "电量整列 47B 逐字节 == 当前电能量对象整列"},

    # 第二阶段
    "结算日1号": {"stage": S2, "text": "结算日可设为 %d 号(被收下且回读命中)" % DAY_LO},
    "结算日28号": {"stage": S2, "text": "结算日可设为 %d 号(被收下且回读命中)" % DAY_HI},
    "结算日29号": {"stage": S2, "text": "结算日写 %d 号被拒(回读仍是原值)" % DAY_BAD},
    "结算日写触发转存": {"stage": S2, "text": "值真变了的那两次写各恰 +1 条冻结(被拒那次一条不落)"},
    "改结算日触发": {"stage": S2, "text": "改结算日每次改动各恰 +1 条(且停在 Chg_BillDayM)"},

    # 第三阶段
    "需量转存": {"stage": S3, "text": "第 1 结算日转存月最大需量"},
    "需量复零": {"stage": S3, "text": "第 1 结算日转存后当月最大需量复零"},
    "需量不转存": {"stage": S3, "text": "非第 1 结算日不转存需量"},
    "需量补NULL": {"stage": S3, "text": "非第 1 结算日 698 读出补 NULL"},

    # 第四阶段
    "补冻上限": {"stage": S4, "text": "一次补冻上限 == %d(补冻写点连停 %d 次, 每次 frezNum == %d)"
             % (CURRENT.BILLFREZ_ADD, CURRENT.BILLFREZ_ADD, CURRENT.BILLFREZ_ADD)},
    "补冻读回条数": {"stage": S4, "text": "698 读回 pos 1..%d 各有记录、pos %d 读不到"
               % (CURRENT.BILLFREZ_ADD, CURRENT.BILLFREZ_ADD + 1)},
    "补冻时标范围": {"stage": S4, "text": "补出的正是连续 %d 个结算日 0 点(日 == 结算日、时分秒全 0)"
               % CURRENT.BILLFREZ_ADD},
    "停电补全": {"stage": S4, "text": "实际停电跨过结算点后, 上电补全这 12 个结算日"},
}

FALSIFY = {
    "冻结对象表": "出厂冻结对象表的第 4 行不是出厂登记的那批电量/金额对象, 或对象号查错了表 ⇒ 逐项比当场不符",
    "存储深度": "出厂存储信息的深度不是 12 ⇒ 与规范『至少能存储上 12 个结算日』对不上",
    "冻结边界": "跨 0 点没生成冻结 / 生成两条 ⇒ 序号不恰 +1",
    "冻结账期": "冻结时标不是结算日 0 点 ⇒ 账期对不上",
    "冻结快照": "记录里没写电量整列/写错对象(lead 位不是 组合14+正向15)/与当前电能量对不上",
    "结算日1号": "1 号被拒 ⇒ 范围判定下界反了或范围收窄",
    "结算日28号": "28 号被拒 ⇒ 范围判定上界不是 28",
    "结算日29号": "29 号被收下 ⇒ 范围判定没拦住界外值",
    "结算日写触发转存": "值真变了却不触发转存(序号不推进) / 被拒的那次也落了记录(序号多走一格)",
    "改结算日触发": "改结算日不触发(或一次改动落两条) ⇒ 序号不恰 +1; 或不经 Chg_BillDayM ⇒ 停不到 :851",
    "需量转存": "源码中没有月最大需量的实际更新/写入链, 冻结对象表及冻结记录也没有需量载体 ⇒ "
             "第1结算日无法形成规范要求的月最大需量转存",
    "需量复零": "源码中仅有 g_MaxDemand 清零代码, 未发现月最大需量实际生成/更新及结算后清零的完整"
             "调用链 ⇒ 规范要求的复零功能未实现",
    "需量不转存": "非首结算日没有可配置的结算冻结需量列, 无法形成规范要求的非首结算日需量不转存数据结构",
    "需量补NULL": "698 冻结记录没有需量列; 虽存在 0xFF→D_NULL 的通路, 当前冻结对象表不含对应需量对象 "
              "⇒ 非首结算日需量 NULL 功能实际未形成",
    "补冻上限": "一次补冻补的条数不等于上限(少的: 中途写库失败 / 多的: 上限没截住) ⇒ frezNum 或停的次数对不上",
    "补冻读回条数": "存储区装不满 12 格(补冻在写满前停住 / 每格顶掉一条) ⇒ 读回的条数对不上",
    "补冻时标范围": "补出来的时标不是往回排的连续整月(起点月错 / 日不是结算日 / 带上了时分秒 / "
              "同一个月重复写) ⇒ 与期望集对不上",
    "停电补全": "停电前后新增冻结记录不是连续12条, 或新增记录的时标不是错过结算点后补出的连续12个结算日",
}

# 结算日号 → 测试点键(第二步逐号分开报)
_DAY_KEY = {DAY_LO: "结算日1号", DAY_HI: "结算日28号", DAY_BAD: "结算日29号"}


def _stop_unproven(ctx, why):
    """当前步骤无法继续取证: 出声 → 停住(退 2, 不是失败)。

    没做到的那几条判据**不需要在这里逐条登记** —— 账本按"有没有认领它的证据"自动判未证。
    """
    ctx.J.note("4-6 结算段: 半途中止(这一次没做成) —— %s" % why)
    raise trial.Stop("%s ⇒ 未定论(退 2), 不是失败" % why)


def _plus_months(clock_str, k, day, tod="12:00:00"):
    """表钟串所在月往前跨 k 个月(日取 day、时刻取 tod) → 目标串; 基准读不出给 None(**不拿本机时间兜底**)。

    纯算术(月年各一格), 与 `cmd_bank.clock_add` 同律: 读不出基准就返回 None —— 它的返回值会被
    `set_meter_clock_set` **写进真表**, 兜底成"本机当前时刻"会造出一个看着合理的假目标。
    """
    base = cmd_bank.clock_dt(clock_str)
    if base is None:
        return None
    m = base.month - 1 + int(k)
    return "%04d-%02d-%02d %s" % (base.year + m // 12, m % 12 + 1, day, tod)


# ============================================================================
# 第一步 —— 结算数据
# ============================================================================
def step_settlement_data(ctx):
    """结算日发生时该转存什么、转存到哪、记录是否形成。

    对象表(转存什么) → 存储深度(装得下几条) → 自然跨结算点(结算日发生)
    → 断点读 usekWh(结算时刻取值) → 记录判据(边界/账期/快照)。
    """
    J, ser = ctx.J, ctx.ser
    ctx.state["d0"] = None       # 第1结算日现值 = 收尾段的恢复目标; 只有真读到才非 None
    ctx.state["dirty"] = False   # 是否"写过非 d0 的结算日值且尚未确认写回"

    # ---- 结算日(后续每一步都要用) ----
    cmd_bank.enter_factory(ser)                    # 发送 → 管理芯: 进厂内(0x14 写的前提)
    bd = cmd_bank.read_billday(ser)                # 发送 → 管理芯: 读第1结算日
    if not bd:
        _stop_unproven(ctx, "读第1结算日无应答(后续结算边界无从确定)")
    d0 = bd[1]
    ctx.state["d0"] = d0

    # ---- 对象表: 结算日冻结到底是哪些对象(探针读 FLASH 常量表; 不停核) ----
    # ⚠ 探针那一次读排在开会话**之前** —— 探针与 gdb 会话抢同一支 J-Link, 用完即关(`with Probe()`)。
    fb = so = None
    if not ctx.waived:
        try:
            with Probe() as pb:
                fb, so = cmd_bank.freobj_read(pb)
        except Exception as exc:            # 探针开不起来 / 读不成 → 这一条记"没做成"
            print("   !! 探针不可用(%s) ⇒ 对象表这一次读不到" % exc)
    rows_ob = cmd_bank.freobj_row(fb, so, OBJ_ROW)
    ok_ob, why_ob = cmd_bank.freobj_check(rows_ob, OBJ_ROW, tag=cmd_bank.FREZOBJ_ROW_TAG[OBJ_ROW])
    J.add("TAB_FrezObj 第 %d 行翻出的 OAD == 出厂表登记的那 16 项" % OBJ_ROW,
          ok_ob, ("用户指定只做黑盒 ⇒ 这一条不做" if ctx.waived else why_ob),
          crit="冻结对象表", falsify=FALSIFY["冻结对象表"], obs=judge.DEBUG)

    # ---- 存储深度: 装得下几条 ----
    store = cmd_bank.frez_store_read(ser, CURRENT.FREZ_STORE_BLOCK, [CURRENT.BILLFREZ_INDEX],
                                     clamp=CURRENT.FREZ_STORE_CLAMP, tag="结算存储信息")
    ok_dp, why_dp = cmd_bank.frez_store_depth_evidence(
        store, CURRENT.BILLFREZ_INDEX, want_depth=CURRENT.BILLFREZ_DEPTH, tag="4-6 结算冻结")
    J.add("结算冻结的存储深度 == %d" % CURRENT.BILLFREZ_DEPTH, ok_dp,
          "%s; 本项补冻上限 = TAB_FrezAdd[EM_BillFrezM] = %d(UserCfg.c:196)"
          % (why_dp, CURRENT.BILLFREZ_ADD),
          crit="存储深度", falsify=FALSIFY["存储深度"], obs=judge.DEBUG)

    # ---- 基线与会话 ----
    stable = watch.named_blocks(*STABLE_NAMES)   # 符号名→(绝对地址,size): 库经 .out 解析, 脚本不摸地址
    watch.watch_vars(ser, CLOCK_NAMES, "时钟派生量基线")
    base_seq = (cmd_bank.read_freeze_row(ser, SUB_SETTLE, 1) or {}).get("seq")
    watch.watch_vars(ser, VARS, "改前基线")
    pre_stable = watch.aa80_ram_snapshots(ser, stable, "稳定态基线")

    ctx.session()
    if ctx.g is not None:
        # 整片 .out 解一次(要停核遍历, 内部按块喂狗, 收尾放行) —— 之后断点只查这张图。
        gdbinit.build(ctx.g)

    # ---- 自然跨结算点 ----
    ct = cmd_bank.read_clock(ser)                          # 发送 → 管理芯: 读表钟(定目标)
    tgt, cross_day = cmd_bank.next_billday_eve(ct, d0) if ct else (None, None)
    if not tgt:
        _stop_unproven(ctx, "表钟读不出 ⇒ 自然跨结算点这一趟做不成")

    bpB = ctx.bp(BP_B)                                       # 地图锚点 → TaskFreeze.c:823
    J.extend([breakpoint.fire_hit(ctx.g, bpB, cmd_bank.settle_across_master, ser, tgt,
                          timeout=TIMEOUT_CROSS, billday=d0, vars=VARS_B, join=20.0,
                          drop=True, crit=None,
                          label="结算日前夜自然跨结算点(应经 Check_BillFrezM)",
                          falsify="结算支没被执行 ⇒ 不会停在 :823(串口只能证『记录多了条』)")])
    #  发送 → 计量芯: 698 Set 40000200 拨主钟到结算日前夜 → 等自然跨0点 → 读结算冻结判0点时标
    #  断[B] 应停在 Check_BillFrezM:823(结算取数落库那行, **只在真走结算支时才到**),
    #  读 usekWh(即将写进 ID_MonthUsed 的月用电量转存值) —— 结算时刻取值, 串口拿不到
    rA = cmd_bank.read_freeze_row(ser, SUB_SETTLE, 1)   # 发送 → 管理芯: 读跨点后最新1条
    J.extend([cmd_bank.bill_freeze_evidence(rA, base_seq=base_seq, want_ts_day=cross_day,
                                      tag="跨 %s 0 点后" % cross_day,
                                      crit=("冻结边界", "冻结账期"),
                                      falsify=FALSIFY["冻结边界"])])
    J.add("结算快照整列 == 当前电能量整列", *cmd_bank.check_freeze_snapshot(
        ser, tag="自然跨点后", subclass=SUB_SETTLE),
        crit="冻结快照", falsify=FALSIFY["冻结快照"])
    watch.watch_vars(ser, VARS, "跨点后当刻")

    watch.watch_vars(ser, CLOCK_NAMES, "时钟派生量终点")
    post_stable = watch.aa80_ram_snapshots(ser, stable, "稳定态终点")
    # **佐证, 不是检查**: 逐块打印谁变了, 交给人/日志判。全块"无变化"是佐证, 不能反过来当判过。
    watch.aa80_snap_diff(pre_stable, post_stable, stable)

    ctx.state["base_seq"] = (rA or {}).get("seq")


# ============================================================================
# 第二步 —— 结算边界
# ============================================================================
def step_settlement_boundary(ctx):
    """什么时间允许转存(1..28) + 改结算日是否也触发一次。"""
    J, ser = ctx.J, ctx.ser
    d0 = ctx.state["d0"]

    # ---- 结算日范围: 1 号 / 28 号被收下, 29 号被拒 ----
    # ⚠ 三次写完必须**回到 d0** 才交给改日那一段 —— 改日靠"值真变了"才走 Chg_BillDayM,
    #   若停在 28 而 alt 恰好也是 28, 那一次写会被静默跳过, 「改日」会跟着假红。
    ctx.state["dirty"] = True          # 先记脏: 这以后到写回 d0 之前挂掉, 收尾段兜得回来
    range_ev, seq_ev = [], []
    cur6 = d0                          # 表上现值; 只有"写进去且与它不同"才算值真变了
    pre6 = cmd_bank.read_freeze_row(ser, SUB_SETTLE, 1)      # 发送 → 管理芯: 写前最新1条(基线序号)
    for day, want in ((DAY_LO, True), (DAY_HI, True), (DAY_BAD, False)):
        ev = cmd_bank.billday_range_evidence(ser, day, want=want, tag="结算日范围")
        time.sleep(WRITE_SLEEP)                              # 等 EEPROM 回落再读记录
        post6 = cmd_bank.read_freeze_row(ser, SUB_SETTLE, 1)  # 发送 → 管理芯: 写后最新1条
        range_ev.append((day, ev))
        seq_ev.append((cur6, day, want, ev[0], pre6, post6))
        if ev[0] is not None and ((ev[0] is True) if want else (ev[0] is False)):
            cur6 = day                                           # 这次写被收下 ⇒ 现值跟着改
        pre6 = post6                     # 下一轮"写前"= 这一轮"写后"
    if any(ev[0] is None for _d, ev in range_ev):
        _stop_unproven(ctx, "写 1/28/29 号那次读回没做成(无应答) ⇒ 范围判定无从对照")
    for day, ev in range_ev:           # 逐号分开报: 一眼看出是哪一个号失败的
        _k = _DAY_KEY[day]
        J.add(CRITERIA[_k]["text"], ev[0], ev[1], crit=_k, falsify=FALSIFY[_k])
    # 序号恰 +1: 被收下且**值真变了**的那两次各 +1; 被拒那次(值没变)一次都不许落。
    _p6, _d6 = [], []
    for pre_day, day, want, acc, pre, post in seq_ev:
        before = (pre or {}).get("seq")
        after = (post or {}).get("seq")
        taken = None if acc is None else ((acc is True) if want else (acc is False))
        delta = None if taken is None else (1 if (taken and day != pre_day) else 0)
        _d6.append("%d 号: 序号 %s→%s(该次%s ⇒ 该 %s)"
                   % (day, before, after,
                      "被收下" if taken else ("被拒" if taken is False else "没做成"),
                      "+1" if delta else ("+0" if delta == 0 else "?")))
        if before is not None and after is not None and delta is not None:
            _p6.append((after, before + delta))
    J.add("改结算日当场触发一次转存: 值真变了的那两次各恰 +1 条、被拒的 %d 号一次都不落" % DAY_BAD,
          None if len(_p6) < len(seq_ev) else judge.tri_eq(*_p6), "; ".join(_d6),
          crit="结算日写触发转存", falsify=FALSIFY["结算日写触发转存"], obs=judge.SERIAL)
    # 把结算日留在 28 号 ⇒ 写回 d0 并回读命中才撤脏标记(没命中就留给收尾段兜)。
    cmd_bank.write_billday(ser, d0)
    time.sleep(WRITE_SLEEP)
    rb6 = cmd_bank.read_billday(ser)
    if rb6 and rb6[1] == d0:
        ctx.state["dirty"] = False
    else:
        print("   !! 结算日收尾写回 d0=%d 未命中(实为 %s), 留给收尾段兜底" % (d0, rb6))

    # ⚠ 基线必须**在这一刻**重取: 上面 1 号/28 号两次被收下的写已经各落了一条记录,
    #   沿用第一步的序号会让下面「每改一次恰 +1」当场假红。
    base_seq = (cmd_bank.read_freeze_row(ser, SUB_SETTLE, 1) or {}).get("seq")

    # ---- 改结算日 → 应经 Chg_BillDayM 并各恰 +1 条 ----
    # ⚠ alt 必须落在 **1..28** —— 645 写结算日有硬门槛(DLT645App.c:2227):
    #   只收 `时<=23 && 日1..28`; 且**值真变了**(:2234)才走 Chg_BillDayM(:2246, 仅 DI0==1)。
    alt = next(x for x in (d0 + 1, d0 - 1, 1, 2) if 1 <= x <= 28 and x != d0)
    ctx.state["dirty"] = True          # **先记脏再发帧**: 万一途中挂了, 收尾段照样兜得回来
    bpC = ctx.bp(BP_C)                 # 地图锚点 → TaskFreeze.c:851
    J.extend([breakpoint.fire_hit(ctx.g, bpC, cmd_bank.write_billday, ser, alt, vars=VARS_C, crit=None,
                          label="改结算日到 %d 号(应经 Chg_BillDayM)" % alt,
                          falsify="改结算日不经 Chg_BillDayM ⇒ 不会停在 :851")])
    #  发送 → 管理芯: 写第1结算日=每月 alt 号(应恰+1条)
    time.sleep(WRITE_SLEEP)                  # 等 EEPROM 回落
    r1 = cmd_bank.read_freeze_row(ser, SUB_SETTLE, 1)   # 发送 → 管理芯: 读结算冻结最新1条
    cmd_bank.read_freeze_row(ser, SUB_SETTLE, 2)   # 发送 → 管理芯: 读倒数第2条(应=旧顶, 供人工核)
    J.extend([cmd_bank.bill_freeze_evidence(r1, base_seq=base_seq, tag="改到 %d 号后" % alt,
                                      crit="改结算日触发", falsify=FALSIFY["改结算日触发"])])
    J.add("结算快照整列 == 当前电能量整列", *cmd_bank.check_freeze_snapshot(
        ser, tag="改到 %d 后" % alt, subclass=SUB_SETTLE),
        crit="冻结快照", falsify=FALSIFY["冻结快照"])
    watch.watch_vars(ser, VARS, "改到 %d 当刻" % alt)
    base_seq = (r1 or {}).get("seq")         # 下一次的基线 = 这一次的新顶

    J.extend([breakpoint.fire_hit(ctx.g, bpC, cmd_bank.write_billday, ser, d0, vars=VARS_C, drop=True,
                          crit=None, label="改结算日写回 %d 号(应经 Chg_BillDayM)" % d0,
                          falsify="改结算日不经 Chg_BillDayM ⇒ 不会停在 :851")])
    #  发送 → 管理芯: 写回每月 d0 号(恢复, 应再+1条); _drop: 趁停住撤该断点
    time.sleep(WRITE_SLEEP)                  # 等 EEPROM 回落
    r2 = cmd_bank.read_freeze_row(ser, SUB_SETTLE, 1)   # 发送 → 管理芯: 读结算冻结最新1条(序号应再+1)
    cmd_bank.read_freeze_row(ser, SUB_SETTLE, 2)   # 发送 → 管理芯: 读倒数第2条(应=旧顶, 供人工核)
    J.extend([cmd_bank.bill_freeze_evidence(r2, base_seq=base_seq, tag="写回 %d 号后" % d0,
                                      crit="改结算日触发", falsify=FALSIFY["改结算日触发"])])
    J.add("结算快照整列 == 当前电能量整列", *cmd_bank.check_freeze_snapshot(
        ser, tag="写回 %d 后" % d0, subclass=SUB_SETTLE),
        crit="冻结快照", falsify=FALSIFY["冻结快照"])
    watch.watch_vars(ser, VARS, "写回 %d 当刻" % d0)
    # 回读命中才撤脏标记 —— 写回那次打了不等于真的生效, 没命中就留给收尾段兜。
    rb = cmd_bank.read_billday(ser)
    if rb and rb[1] == d0:
        ctx.state["dirty"] = False
    else:
        print("   !! 结算日回读 != d0=%d (实为 %s), 留给收尾段兜底" % (d0, rb))


# ============================================================================
# 第三步 —— 最大需量
# ============================================================================
def step_demand(ctx):
    """验证规范第 3 条: 月最大需量。

    这里**不把"没有需量载体"记成未证**。按源码核查, 当前固件缺的是实现本身:

      · `g_MaxDemand[]` 只有清零写入, 没找到实际最大需量生成/更新的写入
      · `g_MaxDmdTmr[][]` 也只有清零
      · `Chk_DmdOver` 只有声明, 没有定义/调用
      · `Clear_DayFreCurDmd` 只有声明, 没有定义/调用
      · `TAB_FrezObj` / `TAB_SelObj` 的结算冻结对象里没有需量 OAD
      · 698 结算冻结记录没有需量列
      · 现有 0xFF → D_NULL 通路没有对应的结算需量对象可以命中

    所以规范这四条不是"测试条件不足", 而是**当前固件实现不满足** —— 逐条登记 FAIL。
    """
    J = ctx.J
    J.add(CRITERIA["需量转存"]["text"], False,
          "源码核查: g_MaxDemand[] 未发现实际最大需量更新写入; TAB_FrezObj/TAB_SelObj 的结算冻结"
          "对象中无需量 OAD; 结算冻结记录无需量列 ⇒ 第1结算日不存在可转存的月最大需量载体",
          crit="需量转存", falsify=FALSIFY["需量转存"], obs=judge.DEBUG)
    J.add(CRITERIA["需量复零"]["text"], False,
          "源码核查: g_MaxDemand[] 虽存在清零代码, 但未发现月最大需量实际生成/更新链及结算后"
          "Clear_DayFreCurDmd 调用; Clear_DayFreCurDmd 仅声明无定义无调用 ⇒ 复零链未实现",
          crit="需量复零", falsify=FALSIFY["需量复零"], obs=judge.DEBUG)
    J.add(CRITERIA["需量不转存"]["text"], False,
          "源码核查: 结算冻结对象表没有需量 OAD, Check_BillFrezM 的非首结算日 0xFF 写入分支"
          "没有可命中的结算需量对象 ⇒ 非首结算日不存在规范要求的需量不转存机制",
          crit="需量不转存", falsify=FALSIFY["需量不转存"], obs=judge.DEBUG)
    J.add(CRITERIA["需量补NULL"]["text"], False,
          "源码核查: 698 结算冻结记录由 TAB_FrezFixObj/TAB_SelObj 组包, 当前记录没有需量字段; "
          "虽有部分 0xFF→D_NULL 处理通路, 但没有对应结算需量对象进入记录 ⇒ 698 无法按规范读出 NULL",
          crit="需量补NULL", falsify=FALSIFY["需量补NULL"], obs=judge.DEBUG)


# ============================================================================
# 第四步 —— 停电补全
# ============================================================================
def step_poweroff_catchup(ctx):
    """错过结算时刻后能不能补回来。

    当前做的是**前半段: 历史补全能力/容量** —— 拨钟往前跨 CATCHUP_MONTHS 个月, 看补冻写点
    连停几次、回读是不是填满 12 格、时标是不是往回排的连续结算日 0 点。

    **真正的"断电跨月再上电"这一半当前未实现** —— 本工程没有控制真实断电/上电的接口, 不伪造。
    """
    J, ser = ctx.J, ctx.ser
    d0 = ctx.state["d0"]

    lbl7b = "一次补冻上限 == %d: 拨钟往前跨 %d 个月那趟, 补冻写点连停 %d 次、每次都 frezNum == %d" \
            % (CURRENT.BILLFREZ_ADD, CATCHUP_MONTHS, CURRENT.BILLFREZ_ADD, CURRENT.BILLFREZ_ADD)
    lbl7c = "结算冻结区装得下 %d 条: 698 读回 pos 1..%d 各有记录、pos %d 读不到" \
            % (CURRENT.BILLFREZ_ADD, CURRENT.BILLFREZ_ADD, CURRENT.BILLFREZ_ADD + 1)
    lbl7d = "补出来的正是连续 %d 个结算日 0 点(日 == %d 号、时分秒全 0), 最新那条落在拨钟目标那个月" \
            % (CURRENT.BILLFREZ_ADD, d0)

    ct7 = cmd_bank.read_clock(ser)                                  # 发送 → 管理芯: 读表钟(算目标)
    tgt7 = None if ct7 is None else _plus_months(ct7, CATCHUP_MONTHS, d0)
    if tgt7 is None:
        for _txt in (lbl7b, lbl7c, lbl7d):
            J.add(_txt, None, "表钟读不出来(%r) ⇒ 拨钟目标算不出, 这一趟没做成" % ct7,
                  crit="补冻上限", falsify=FALSIFY["补冻上限"], obs=judge.DEBUG)
        return

    print("\n[拨钟] 历史补全: 往前跨 %d 个月 —— 表钟=%s → 目标=%s(日取结算日 %d 号, 时刻 12:00:00)"
          % (CATCHUP_MONTHS, ct7, tgt7, d0))
    hits = []
    if ctx.g is None:
        # 台面没接探针: 白盒那半不做, 但**这一帧照发** —— 黑盒那两条(条数/时标)全靠它
        cmd_bank.set_meter_clock_set(ser, tgt7, chip="计量芯")
    else:
        bpD = ctx.bp(BP_D)              # 地图锚点 → TaskFreeze.c:748 那条 Write_FrezData 之前
        ctx.g.ensure_running()          # ⚠ 下断点把核叫停了 —— 不放行这条拨钟帧表根本不处理
        r0 = breakpoint.fire_hit(ctx.g, bpD, cmd_bank.set_meter_clock_set, ser, tgt7,
                                 chip="计量芯", timeout=CATCHUP_HIT, vars=VARS_D, drop=False,
                                 label="补冻写点第 1/%d 次" % CURRENT.BILLFREZ_ADD,
                                 falsify="拨钟那趟没走结算支 ⇒ 至多 %.0fs 内停不到 %s"
                                         % (CATCHUP_HIT, breakpoint.text(BP_D)), crit=None)
        if (r0 or {}).get("ok") is True:
            hits.append(r0)
        for k in range(1, CURRENT.BILLFREZ_ADD):
            if len(hits) < k:
                break                   # 上一停就没停到 ⇒ 后面不必再等
            rk = breakpoint.wait_hit(ctx.g, bpD, CATCHUP_STEP, vars=VARS_D,
                                     drop=(k == CURRENT.BILLFREZ_ADD - 1),
                                     label="补冻写点第 %d/%d 次" % (k + 1, CURRENT.BILLFREZ_ADD),
                                     falsify="补 %d 条的中途停了(写库失败提前 return / 条数截错)"
                                             % CURRENT.BILLFREZ_ADD, crit=None)
            if (rk or {}).get("ok") is not True:
                break
            hits.append(rk)
        if len(hits) < CURRENT.BILLFREZ_ADD:
            ctx.g._disarm(bpD)          # ⚠ 没停满 ⇒ 断点还在槽里, 它自己命中会把核撂停
    time.sleep(WRITE_SLEEP)             # 等这一趟的 12 条落完再读回

    # 白盒: 停几次 / 每次都读到 frezNum == 上限、i 依次 0..11
    vs7 = [((h or {}).get("vars") or {}) for h in hits]
    fn7 = [cmd_bank.st_int(v.get("frezNum")) for v in vs7]
    ix7 = [cmd_bank.st_int(v.get("i")) for v in vs7]
    b7 = cmd_bank.gdb_bytes(vs7[0].get("buff")) if vs7 else []
    ts7 = ("%04d-%02d-%02d %02d:%02d:%02d"
           % (b7[5] + 2000, b7[4], b7[3], b7[2], b7[1], b7[0])) if len(b7) >= 6 else "读不到"
    if ctx.g is None:
        ok7b = None
        why7b = "没有调试会话 ⇒ 写点这一半不做(黑盒那两条照做)"
    elif len(hits) != CURRENT.BILLFREZ_ADD or None in fn7 or None in ix7:
        ok7b = None
        why7b = ("写点只停了 %d 次(要 %d 次)" % (len(hits), CURRENT.BILLFREZ_ADD)
                 if len(hits) != CURRENT.BILLFREZ_ADD else
                 "停到了但 frezNum/i 读不到(那一停的位置表空洞) ⇒ 那一刻的值没拿到")
    else:
        ok7b = (all(z == CURRENT.BILLFREZ_ADD for z in fn7)
                and ix7 == list(range(CURRENT.BILLFREZ_ADD)))
        why7b = "停 %d 次; frezNum=%s(期望 %d); i=%s(期望 0..%d); 第一次那刻 buff[:6]=%s(时标 %s)" % (
            len(hits), fn7, CURRENT.BILLFREZ_ADD, ix7, CURRENT.BILLFREZ_ADD - 1,
            " ".join("%02X" % x for x in b7[:6]) if b7 else "读不到", ts7)
    J.add(lbl7b, ok7b, why7b, crit="补冻上限", falsify=FALSIFY["补冻上限"], obs=judge.DEBUG)

    # ⚠ 关键: **没观察到本次补冻完整写入时, EEPROM 里原有的记录不能当证据**。
    #   否则会出这种事: 写点一次没停(本次补冻压根没发生) → 但 EEPROM 里恰好有 12 条旧记录
    #   → 读到 12 条 → 判"补冻读回 12 条 PASS"。那是拿历史冒充本次。
    if ok7b is not True:
        J.add(lbl7c, None,
              "本次补冻写点没有完整观察到 %d 次 Write_FrezData, 现有冻结记录不能证明属于本次补冻 ⇒ 未证"
              % CURRENT.BILLFREZ_ADD,
              crit="补冻读回条数", falsify=FALSIFY["补冻读回条数"], obs=judge.SERIAL)
        J.add(lbl7d, None,
              "本次补冻写点没有完整观察到 %d 次 Write_FrezData, 不能用 EEPROM 中已有记录证明"
              "本次补冻的时标范围 ⇒ 未证" % CURRENT.BILLFREZ_ADD,
              crit="补冻时标范围", falsify=FALSIFY["补冻时标范围"], obs=judge.SERIAL)
    else:
        # 黑盒: 读回 12 条、第 13 格读不到; 12 条的时标 == 目标月往回排的 12 个结算日 0 点
        rows7 = [cmd_bank.read_freeze_row(ser, SUB_SETTLE, p, empty_ok=True)
                 for p in range(1, CURRENT.BILLFREZ_ADD + 1)]              # 发送 × 12
        over7 = cmd_bank.read_freeze_row(ser, SUB_SETTLE, CURRENT.BILLFREZ_ADD + 1, empty_ok=True)
        unread = [p for p, r in zip(range(1, CURRENT.BILLFREZ_ADD + 1), rows7)
                  if not r or r.get("answered") is False]
        filled = [r for r in rows7
                  if r and r.get("answered") is not False and r.get("seq") is not None]
        # ⚠ "该格没有记录"的判据取**序号读不读得到**: 空数组与被拒都算"没有",
        #   而"一个字都没回"(answered=False)不算 —— 那是没读到, 与本条无关。
        over_ok = bool(over7) and over7.get("answered") is not False and over7.get("seq") is None
        J.add(lbl7c, None if unread else (len(filled) == CURRENT.BILLFREZ_ADD and over_ok),
              ("pos %s 那几次没读到任何字节 ⇒ 条数无从对照" % unread) if unread else
              "pos 1..%d = %s; pos %d = %s"
              % (CURRENT.BILLFREZ_ADD, " | ".join(cmd_bank.rec_row_txt(r) for r in rows7),
                 CURRENT.BILLFREZ_ADD + 1, cmd_bank.rec_row_txt(over7)),
              crit="补冻读回条数", falsify=FALSIFY["补冻读回条数"], obs=judge.SERIAL)
        exp7 = [_plus_months(tgt7, 1 - p, d0, "00:00:00")
                for p in range(1, CURRENT.BILLFREZ_ADD + 1)]          # pos1=目标月, pos12=往回 11 个月
        got7 = [r.get("ts") for r in rows7]
        J.add(lbl7d, None if (unread or any(t is None for t in got7)) else (got7 == exp7),
              "pos 1..%d 实读 %s; 期望 %s" % (CURRENT.BILLFREZ_ADD, got7, exp7),
              crit="补冻时标范围", falsify=FALSIFY["补冻时标范围"], obs=judge.SERIAL)


# ============================================================================
# 第四步-2 —— 真实停电跨结算点
# ============================================================================
def step_poweroff_real(ctx):
    """真实停电跨结算点 → 上电 → 启动补冻。

    **不能用软件复位代替真实掉电。**

      1. 确认结算日已恢复为 d0;
      2. 读停电前冻结记录最新序号 before_seq;
      3. 表钟拨到 d0 日 00:00 前约 POWER_CROSS_LEAD 秒;
      4. **人工切断被测表真实电源**;
      5. 断电跨过 d0 00:00;
      6. 人工重新上电;
      7. 等启动补冻完成;
      8. 读 before_seq **之后新增**的记录;
      9. 只检查本次新增记录是不是连续 12 个结算日。

    ⚠ 不拿断电前已有的记录冒充本次补冻; 不把 Reset_CPU()/软件复位当成停电。
    ⚠ 断电/上电由测试人员完成, 本函数负责验证结果。
    """
    J, ser = ctx.J, ctx.ser
    d0 = ctx.state["d0"]
    lbl = CRITERIA["停电补全"]["text"]

    def _give(ok, why):
        J.add(lbl, ok, why, crit="停电补全", falsify=FALSIFY["停电补全"], obs=judge.SERIAL)

    # ---- 结算日必须是原值 ----
    rb = cmd_bank.read_billday(ser)
    if not rb or rb[1] != d0:
        _give(False, "真实停电测试前结算日不是原始 d0=%d, 当前=%s ⇒ 不允许继续做停电补全" % (d0, rb))
        return

    # ---- 停电前冻结序号基线 ----
    before = cmd_bank.read_freeze_row(ser, SUB_SETTLE, 1, empty_ok=True)
    before_seq = (before or {}).get("seq")
    if before_seq is None:
        _give(None, "停电前最新冻结记录序号读不到 ⇒ 无法区分上电补冻的新增记录和原有记录")
        return

    # ---- 算"结算日前"目标时间 ----
    ct = cmd_bank.read_clock(ser)
    if not ct:
        _give(None, "停电测试前表钟读不到 ⇒ 无法计算跨结算点目标时间")
        return
    eve = cmd_bank.next_billday_eve(ct, d0)
    target = eve[0] if eve else None
    if not target:
        _give(None, "无法计算 d0=%d 的结算日前时间" % d0)
        return
    base = cmd_bank.clock_dt(target)
    if base is None:
        _give(None, "结算日前目标时间无法解析: %r" % target)
        return
    power_target = (base - datetime.timedelta(seconds=POWER_CROSS_LEAD)).strftime("%Y-%m-%d %H:%M:%S")

    print("\n" + "=" * 72)
    print("真实停电补全测试")
    print("  结算日       : 每月 %d 日" % d0)
    print("  停电前目标钟 : %s" % power_target)
    print("  停电前序号   : %s" % before_seq)
    print("请执行: 1) 脚本即将把表钟拨到结算日前; 2) 立即切断被测表真实电源;")
    print("        3) 保持断电, 跨过每月 %d 日 00:00; 4) 重新上电; 5) 回车继续。" % d0)
    print("=" * 72)

    v, note, _dar = cmd_bank.set_meter_clock_set(ser, power_target, chip="计量芯")
    if v != "PASS":
        _give(False, "停电前拨钟失败: Set=%s(%s)" % (v, note))
        return
    time.sleep(0.5)
    check_clock = cmd_bank.read_clock(ser, chip="计量芯")
    if not check_clock or check_clock[:16] != power_target[:16]:
        _give(False, "停电前表钟没有到目标: 实读=%s, 目标=%s" % (check_clock, power_target))
        return

    # ---- 必阻塞: 等**真实断电** ----
    # 不靠键盘 —— 靠"表还答不答"这个**可观测**的条件。它是必要阻塞: 没断电就一直等, 不放行;
    # 而且它顺带就是证据: 表从"应"变成"不应", 是断电真的发生过的直接观察。
    print("\n>>> 现在切断被测表真实电源 <<<")
    t_end = time.time() + POWER_OFF_TIMEOUT
    while time.time() < t_end:
        if cmd_bank.read_clock(ser, wait=1.0, quiet=True) is None:
            break
        time.sleep(1.5)
    else:
        _give(False, "%.0fs 内表一直在应答 ⇒ 没有断电 ⇒ 本步中止(停电补全没做成)"
              % POWER_OFF_TIMEOUT)
        return
    print("   [OK] 已确认断电(表不再应答)")

    # ---- 强制保持断电时长, 不让"没跨过 0 点就上电"蒙混过去 ----
    print("   保持断电 %d 秒(让表内 RTC 跨过每月 %d 日 00:00)..." % (POWER_MIN_OFF, d0))
    time.sleep(POWER_MIN_OFF)

    # ---- 必阻塞: 等**重新上电** ----
    print(">>> 现在可以重新上电了 <<<")
    t_end = time.time() + POWER_OFF_TIMEOUT
    while time.time() < t_end:
        if cmd_bank.read_clock(ser, wait=1.0, quiet=True) is not None:
            break
        time.sleep(1.5)
    else:
        _give(False, "%.0fs 内表一直不应答 ⇒ 没有上电 ⇒ 本步中止" % POWER_OFF_TIMEOUT)
        return
    print("   [OK] 已确认上电(表恢复应答)")
    # ⚠ 上电后表已退出厂内模式(断电会丢) —— 读冻结记录前必须重新进厂内,
    #   否则每一笔读都回 DAR=20(安全认证不匹配), 看着像"读不到记录"。
    cmd_bank.enter_factory(ser)
    time.sleep(0.5)

    # ---- 留证 + 定性: 断电期间钟走没走 ----
    # 断电前表钟 = power_target; 断电至少 POWER_MIN_OFF 秒 ⇒ 上电后钟**只该更晚**, 绝不会更早。
    #   钟 ≥ power_target → 钟保住了(有 RTC) ⇒ "错过结算时刻"成立, 补冻该发生
    #   钟 <  power_target → 钟倒退了       ⇒ 断电期间钟没走 ⇒ 这个硬件上根本没有"错过",
    #                                            补冻无从触发(不是固件不补, 是没东西可补)
    ct_after = cmd_bank.read_clock(ser, chip="计量芯")      # 不 quiet: 这个值必须进日志
    print("   断电后表钟 = %s   (断电前 = %s; 断电 ≥ %d 秒, 只该更晚)"
          % (ct_after, power_target, POWER_MIN_OFF))
    _a, _b = cmd_bank.clock_dt(ct_after), cmd_bank.clock_dt(power_target)
    if _a is None or _b is None:
        _give(None, "上电后表钟读不到/解析不了 ⇒ 断没断电期间走时定性不了, 停电补全判不了")
        return
    if _a < _b:
        _give(False, "断电后表钟倒退了(断电前 %s → 上电后 %s) ⇒ 表在断电期间不保持走时 ⇒ "
                     "'错过结算时刻'这个前提在本硬件上不成立, 补冻无从触发 —— "
                     "这不是固件没补, 是没东西可补"
              % (power_target, ct_after))
        return
    print("   [结论] 钟保住了(断电期间继续走, %s → %s) ⇒ '错过结算时刻'成立, 继续看补冻"
          % (power_target, ct_after))

    print("   上电后等待启动补冻 %d 秒..." % POWER_ON_WAIT)
    time.sleep(POWER_ON_WAIT)

    # ---- 轮询最新序号, 不靠固定 sleep 猜补冻做完没有 ----
    deadline = time.time() + POWER_OFF_TIMEOUT
    after = None
    while time.time() < deadline:
        after = cmd_bank.read_freeze_row(ser, SUB_SETTLE, 1, empty_ok=True)
        seq = (after or {}).get("seq")
        if seq is not None and seq != before_seq:
            break
        time.sleep(2.0)
    after_seq = (after or {}).get("seq")
    if after_seq is None:
        _give(False, "真实上电后在 %.0fs 内没读到新的结算冻结记录; 停电前序号=%s, 上电后序号=%s"
              % (POWER_OFF_TIMEOUT, before_seq, after_seq))
        return

    # ---- 只读"本次新增"的记录 ----
    rows = []
    for pos in range(1, CURRENT.BILLFREZ_DEPTH + 1):
        r = cmd_bank.read_freeze_row(ser, SUB_SETTLE, pos, empty_ok=True)
        if r and r.get("answered") is not False and r.get("seq") is not None:
            rows.append(r)

    # 序号是循环存储 ⇒ 不能简单写 `seq > before_seq`。
    # ⚠ 这里按**16 位环形**算距离 —— 这个位宽目前没有依据(今天观察到的序号只有 68~95), 待核。
    def _seq_delta(a, b):
        if a is None or b is None:
            return None
        return (int(a) - int(b)) & 0xFFFF

    new_rows = [r for r in rows
                if _seq_delta(r.get("seq"), before_seq) != 0
                and _seq_delta(r.get("seq"), before_seq) <= CURRENT.BILLFREZ_ADD]
    uniq = {}
    for r in new_rows:
        uniq[r.get("seq")] = r
    new_rows = sorted(uniq.values(), key=lambda r: _seq_delta(r.get("seq"), before_seq))

    if len(new_rows) != CURRENT.BILLFREZ_ADD:
        _give(False, "真实停电上电后新增冻结记录只有 %d 条, 期望 %d 条; 停电前 seq=%s, 上电后最新 "
                     "seq=%s; 新增=%s"
              % (len(new_rows), CURRENT.BILLFREZ_ADD, before_seq, after_seq,
                 [r.get("seq") for r in new_rows]))
        return

    latest_ts = new_rows[-1].get("ts")
    if latest_ts is None:
        _give(False, "新增 12 条已找到, 但最新记录时标读不到 ⇒ 无法证明补出的账期")
        return
    exp_power = [_plus_months(latest_ts, -(CURRENT.BILLFREZ_ADD - 1) + i, d0, "00:00:00")
                 for i in range(CURRENT.BILLFREZ_ADD)]
    got_power = [r.get("ts") for r in new_rows]
    ok = (got_power == exp_power
          and all(ts is not None and ts[11:19] == "00:00:00" for ts in got_power)
          and all(cmd_bank.clock_dt(ts) is not None and cmd_bank.clock_dt(ts).day == d0
                  for ts in got_power))
    _give(ok, "停电前 seq=%s, 上电后 seq=%s; 本次新增=%d 条; 新增时标=%s; 期望=%s"
          % (before_seq, after_seq, len(new_rows), got_power, exp_power))


# ============================================================================
# 收尾段
# ============================================================================
def cleanup_billday(ctx):
    """写回结算日: 改日那段把结算日改成了 alt —— 写回那次没确认成功就得在这一档再写一次。

    跑在 `g.close()` **之后**(核心已放行, 串口发得出去)、`ser.close()` **之前**; 无论上面那段
    是正常跑完、抛 `Stop`、还是抛异常, 它都会跑。

    ⚠ 总复位 `scripts/_restore_all.py` 明文"不动结算日", 依据是"测试脚本跑完已自恢复" ——
      本函数就是那个假设的兑现点。
    ⚠ **兜底被触发 = 本轮已经失败**: 恢复是 housekeeping, 判过是 correctness, 两件事不能互相顶替。
    """
    ser = ctx.ser
    d0, dirty = ctx.state.get("d0"), ctx.state.get("dirty")
    if d0 is None or not dirty:
        return
    # 记 ok=False(不是 None): 这不只是"没做成", 而是**观察到不对** —— 结算日历史已经被破坏。
    ctx.J.add("结算日兜底被触发(写回 d0 那次没确认成功)", False,
              "恢复是 housekeeping、判过是 correctness, 两件事不能互相顶替 ⇒ 本轮判失败")
    try:
        print("\n== 结算日兜底: 写回每月%d号 ==" % d0)
        print("   !! 兜底被触发 = 本轮已失败(此条不判过); 下面只负责把表恢复原状")
        cmd_bank.enter_factory(ser)                    # 兜底自带前置, 不假设前面走到哪一步
        cmd_bank.write_billday(ser, d0)
        rbf = cmd_bank.read_billday(ser)
        if rbf and rbf[1] == d0:
            print("   兜底结果: 表已恢复每月%d号(状态 OK, 但本轮仍判 FAIL)" % d0)
        else:
            print("   !! 兜底回读未命中(实为 %s) —— 表结算日可能仍非 %d 号, 请人工核" % (rbf, d0))
    except Exception as exc:
        print("   !! 结算日兜底失败: %s —— 表结算日可能已非 %d 号, 请人工处理" % (exc, d0))


def cleanup_clock(ctx):
    """把表钟拨回真实时间(自然跨点与补全那两趟各把钟拨到了别处)。

    真实时间 = **本机墙钟 + `CLOCK_SKEW` 秒**(与 `scripts/_restore_all.py` 第 [3] 步同一个算式、
    同一个理由: 目标必须严格晚于"现在", 否则那一写就成了往回拨); **不从表上读**。
    确认口径同 `_restore_all.py`: 双芯读回与目标**比到分**(`[:16]`), 秒差不算。
    ⚠ 拨钟走 698 Set 40000200 到**计量芯**(管理芯 AF=0x05 同帧回 DAR=0xFF, 只有计量芯那一颗收)。
    """
    ser = ctx.ser
    target = (datetime.datetime.now()
              + datetime.timedelta(seconds=CLOCK_SKEW)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        print("\n== 表钟兜底: 拨回真实时间 %s(本机墙钟 +%d 秒) ==" % (target, CLOCK_SKEW))
        cmd_bank.enter_factory(ser)                        # 兜底自带前置(理由同 cleanup_billday)
        v, note, _dar = cmd_bank.set_meter_clock_set(ser, target, chip="计量芯")
        time.sleep(0.5)
        got_m = cmd_bank.read_clock(ser, chip="计量芯")     # 发送 → 计量芯: 主钟回读
        got_n = cmd_bank.read_clock(ser, chip="管理芯")     # 发送 → 管理芯: 跟随那颗回读
        hit = bool(got_m and got_m[:16] == target[:16] and got_n and got_n[:16] == target[:16])
        if v != "PASS" or not hit:
            ctx.J.add("表钟兜底: 拨回真实时间没确认成功", False,
                      "Set=%s(%s); 双芯回读 主钟=%s / 管理芯=%s, 目标 %s —— 表钟可能仍停在测试拨到的"
                      "时刻, 请人工核" % (v, note, got_m, got_n, target))
        else:
            print("   表钟兜底结果: 主钟=%s 管理芯=%s —— 都已拨回真实时间" % (got_m, got_n))
    except Exception as exc:
        ctx.J.add("表钟兜底: 拨回真实时间没做成", False,
                  "异常: %s —— 表钟可能仍停在测试拨到的时刻, 请人工核" % exc)


def _banner():
    return "== 4-6 结算日冻结(结算数据 / 结算边界 / 最大需量 / 停电补全) | 工程=%s ==" % CURRENT.PROJECT


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "4-6 结算日冻结", lambda: CRITERIA,
        name="4_6_aa80",
        parts=[("结算数据", step_settlement_data),
               ("结算边界", step_settlement_boundary),
               ("最大需量", step_demand),
               ("停电补全-历史补冻", step_poweroff_catchup),
               ("停电补全-真实掉电", step_poweroff_real)],
        gdb=breakpoint, banner=_banner(),
        cleanup=[("结算日兜底", cleanup_billday), ("表钟兜底", cleanup_clock)],
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV)))
