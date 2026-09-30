# -*- coding: utf-8 -*-
"""在证什么: 日冻结只在真实 0 点落一条、时标就是该 0 点、非日界那些分钟不落库; 校时跨多日那趟按 prd
  补冻; 自然跨过月界后那条落在次月 1 日 00:00:00。
会向表写什么: 只发 698 Set 40000200 拨五次表钟(不写任何参数), 外加一次**人工断电**(脚本只等,
  不自己复位); ⑩ 那一步在停住态把 `g_MaxDemand[0]/[1]` 注成非零(全局量, 会话收尾自动写回原值);
  跑完表钟停在「最新一条日冻结 + 8 天」的白天, 收尾走 `python scripts/_restore_all.py`
  —— 本脚本不发时钟收集尾。
跑法: `python project/tests/_test_4_4_day_frez.py`(真串口 + 真探针; `--no-gdb` 只做黑盒;
  跑到断电段会打提示, 按提示断表电 ≥5 秒再上电)
结论怎么读: 账本末行「判据: 满足 n/N」, N=13(⑨ 在本台声明为 unprovable, 恒记未证);
  退 0 = 通过 / 1 = 失败 / 2 = 未定论。
"""
import datetime
import time

from common import judge            # 观测种类常量(断点那几条证据标 DEBUG, 记录读回标 SERIAL)
from common import trial            # 运行外壳: argv/日志/账本/串口/会话/收尾三连/退出码
from meterlib import cmd_bank       # 帧目录 + 语义动词积木(每步自带打印/判 PASS·TBD·FAIL)
from meterlib import watch         # AA80 读内存(断电段的"表还在不在"探测用它)
from project import CURRENT          # 本工程画像: 表号/.out 路径/喂狗点(库不认表, 只有脚本认)
from swdbg import breakpoint            # 断点级白盒(停核 + 读函数内局部量); 没接 J-Link → None
from swdbg import gdbinit            # 整片 .out 地图: 开会话后解一次, 之后断点/注入点只查它
from swdbg.probe import Probe        # SWD 直读(读 FLASH 常量表; 不停核)

# ---- 子项参数(纯数据; 要改的在这里改) ----
SUB = CURRENT.DAYFREZ_SUBCLASS      # 日冻结记录子类(记录 OAD = 50 04 02 00)
IDX = CURRENT.DAYFREZ_INDEX         # `s_stFrzStorageInfo` 里日冻结那一项的序号(ID_DayFrez)
ADD = CURRENT.DAYFREZ_ADD           # TAB_FrezAdd[EM_Day] —— 校时那趟最多补写这么条
WAIT = 3.0                    # 记录读回 / 拨钟确认的单次等待
SETTLE = 3.0                  # 停在写库点之后等它落完再读回
HIT_TIMEOUT = CURRENT.DAYFREZ_WAIT          # 等断[A] 命中: 拨到日界前 1 分钟, 至多 ~3 分钟到点
WRITE_TIMEOUT = CURRENT.DAYFREZ_WAIT / 3.0  # 断[A] 命中后到断[B] 只差几十毫秒; 这条是"断[B] 确实没来"的等待
NEG_WIN = CURRENT.DAYFREZ_NEG_WIN           # ⑤ 否定期望窗口: 比一个整分间隔长, 保证窗口里至少跨过一个整分
# ⑧ 之前先等 ⑦ 那趟把 7 条补冻写完(挂早了会停在中间那一条上); 55s 后才自然跨月, 所以只用掉不到三分之一。
CATCHUP_SETTLE = cmd_bank.DAYFREZ_CATCHUP_SETTLE

# ---- 断电补冻段参数(纯数据) ----
POWER_WAIT = 300.0      # 等"表消失又回来"的总时长上限(秒); 超了那一条记未证(与 1-2 段同款)
POLL = 2.0              # 断电探测的轮询间隔(秒)
GAP_DAYS = ADD + 1      # 断电要造出的"跨过多少天": 比补冻上限多一天 ⇒ 上电那趟必被 clamp 到 ADD

# ---- 断点: 模块级**字面量元组** ----
# `scripts/_check_anchors.py` 用 `ast.literal_eval` 抠 `BP_<X>`/`VARS_<X>` 这两个名字逐条对源码
# (变量在断点那一行赋过值没有); 写成 `CB.SOMETHING` 那种表达式就抠不出来, 等于没人核过。
BP_GATE = ("prev", "Check_DayFrez", "Prep_ObjData", 1)     # 这一趟真要写 —— `Prep_ObjData(...)` 调用行 @TaskFreeze.c:493
BP_WRITE = ("prev", "Check_DayFrez", "Write_FrezData", 1)  # 写库点 `Write_FrezData(ID_DayFrez, &buff[0])` @TaskFreeze.c:510
# 那一停可读的量(位置表逐区间核过):
#   断[A]: `normal` = 这一趟的驱动源(自然分钟步进 → TRUE = 0xAA = 170; 其余源 → FALSE = 0x55 = 85)
#          `frezNum` = 这一趟补几条; `over` = 是否超过补冻上限(`:481-485`); `days[]` = 补哪几天
#   断[B]: `frezNum` / `i`(写到第几条) / `stInfo` / `buff[0..5]` = [秒(写死 0), 分(写死 0), 时(写死 0), 日, 月, 年偏移]
# ⚠ 必须是**字面量元组**, 且每个名字都要能在 C 源码里搜到(点号表达式抠不出/找不到)。
VARS_GATE = ("normal", "frezNum", "over", "days", "stInfo")
# 末一项是**文件级量**(TaskMetering.c:290 的 `g_MaxDemand[6]`, 画像里有它)) ——
# `_check_anchors.py` 对"本函数里断点前没赋值"的全局量只报事实、不报问题(见 breakpoint.facts 的 is_local)。
VARS_WRITE = ("frezNum", "i", "stInfo", "buff", "g_MaxDemand")

# ---- ⑩ 的注入(另一条触发通道): 把正/反向有功最大需量**趁停住写成非零**, 再跨日看它归没归零 ----
# 为什么非注入不可: 0 负载下固件算出来的需量恒 0, 而 `g_MaxDemand` 全固件只清零不写入
# (`TaskMetering.c:523` 上电 / `:3182` `:3210` 事件清零, `:3335` `:3873` 只读),
# 所以"清不清零"读出来一模一样 —— 只有先有一个非零值在, 这一条才答得出 falsify。
# 下标 0/1 = 正向/反向有功最大需量(与事件枚举 `EV_PAcDmdOver`/`EV_NAcDmdOver` 同序)。
# ⚠ `Session.inject` 只收**变量名**(拒裸地址), 且必须登记进 `session_kw` 的 `inject_allow` 白名单。
DMD_INJ = 0x000186A0             # 100000 个原始计数 —— 只要非零就分得开, 不追求物理含义
DMD_NAMES = ("g_MaxDemand[0]", "g_MaxDemand[1]")
DMD_ASSIGNS = tuple((n, DMD_INJ) for n in DMD_NAMES)

# ---- 半途中止时要一次记全的条目(纯数据; 与正常路径同名同号**同观测**, 少记一条 = 分母变小)----
ENTRIES_ALL = (
    ("①", "停在 TaskFreeze.c:493 那一刻 normal == TRUE", judge.DEBUG),
    ("②", "跨日那一趟只写 1 条(frezNum == 1)", judge.DEBUG),
    ("③", "写库那一刻 buff 的秒/分/时为 0、日是 prd 的整数倍, 且日月年 == 跨日那天的日月年",
     judge.DEBUG),
    ("④", "698 读回最新一条的时标 == 写库那一刻 buff 的时标", judge.SERIAL),
    ("⑤", "%.0fs 窗口里 :510 不命中, 且窗口里表钟跨过一个整分" % NEG_WIN, judge.DEBUG),
    ("⑥", "相邻两条读回记录的时标相差 prd 天", judge.DEBUG),
    ("⑦", "校时那趟补冻 %d 条且 days[] 两两相差 prd" % ADD, judge.DEBUG),
    ("⑧", "自然跨月那条时标 == 次月 1 日 00:00:00", judge.DEBUG),
    ("⑨", "冻结电能 == 前一日累计(本台 0 负载, 证不了)", judge.SERIAL),
    ("⑩", "跨过日界那一趟把当日正反向有功最大需量归零(注入非零再跨日)", judge.DEBUG),
    ("⑪", "TAB_FrezObj 第 3 行翻出的 OAD == 出厂表登记的那 12 项", judge.DEBUG),
    ("⑫", "日冻结的存储深度 == %d, 且一次补冻上限 == %d" % (CURRENT.DAYFREZ_DEPTH, ADD), judge.DEBUG),
    ("⑬", "断电跨 %d 天再上电, 上电那一路补出来的条数一样是 %d" % (GAP_DAYS, ADD), judge.SERIAL),
)

OBJ_ROW = 3                     # 日冻结用的对象表行号(TAB_FrezObj 第 3 行)


def _stop_unproven(ctx, why):
    """半途中止的统一收场: 条目一次记全(`ok=None`) → 出声 → 停住(退 2, 不是失败)。"""
    ctx.J.extend(cmd_bank.unproven_records(ENTRIES_ALL, why, cmd_bank.DAYFREZ_FALSIFY))
    ctx.J.note("4-4 日冻结段: 半途中止(这一次没做成) —— %s" % why)
    raise trial.Stop("%s ⇒ 未定论(退 2), 不是失败" % why)


def _arm(ctx, with_gate=True):
    """挂断点并放行 → `(bp_g, bp_w)`; 没会话 ⇒ `(None, None)`。

    `with_gate=False` = 这一趟**只挂写点**(⑧ 跨月那趟): 断[A] 那一趟早过了 `:493`, 挂着它只会在下一趟
    把核撂停, 而核一停就再也等不到写库。

    ⚠ **两个都挂好才放行**: 断[A] 停完一放行, 核几十微秒就到 断[B] —— 那时再挂已经晚了, 它会掠过一次
      写库, 而现象是"断[B] 从没命中过"(与断点压根没挂上一模一样)。
    ⚠ **⑦⑧ 挂完紧接着就发拨钟帧**, 所以放行这一步不能省: `break_at` 下断点前把核叫停, 自己不恢复 ——
      核停着时那一帧发出去表根本不处理(2026-09-22 实踩: Set 无 SetResponse, 两个断点双双 TBD)。
    ⚠ `ctx.bp` 下不上断点会抛 `GdbError`, 不返回 None。
    """
    bp_g = ctx.bp(BP_GATE) if with_gate else None
    bp_w = ctx.bp(BP_WRITE)
    if bp_g is not None or bp_w is not None:
        ctx.g.ensure_running()          # 少了它: 核停在下断点那一步, 后面每一帧都没有应答
    return bp_g, bp_w


def _wait(ctx, bp_g, bp_w, label):
    """等这两个断点各停一次(先断[A] 后断[B]) → `(r_gate, r_write)`; `bp_g is None` = 本趟不等 断[A]。

    ⚠ 断[B] 的停产记录常在读断[A] 那几百毫秒里就到队列了 —— `wait_only` 认"队列里躺着同一个断点号"
      就不放行, 所以这里能收到; 顺序反过来(先断[B])会把它当陈旧记录丢掉。
    ⚠ 断[B] 超时时 `drop` 不生效(它只在命中支撤) ⇒ 补撤, 否则核跑着撞上它自己会被撂停。
    """
    r_g = None
    if bp_g is not None:
        r_g = breakpoint.wait_hit(ctx.g, bp_g, HIT_TIMEOUT, vars=VARS_GATE,
                          label="%s 断[A] 要写了(%s)" % (label, breakpoint.text(BP_GATE)),
                          falsify="固件没在日界前那一分钟走到 %s, 或那条路被优化掉 "
                                  "⇒ 至多 %.0fs 内停不到 %s"
                                  % (breakpoint.text(BP_GATE), HIT_TIMEOUT, breakpoint.text(BP_GATE)))
        if (r_g or {}).get("ok") is not True:
            ctx.g._disarm(bp_g)
    r_w = breakpoint.wait_hit(ctx.g, bp_w, WRITE_TIMEOUT, vars=VARS_WRITE,
                      label="%s 断[B] 写库(%s)" % (label, breakpoint.text(BP_WRITE)),
                      falsify="到了日界却没走到 %s(写库判定失效) ⇒ 至多 %.0fs 内停不到 %s"
                              % (breakpoint.text(BP_WRITE), WRITE_TIMEOUT, breakpoint.text(BP_WRITE)))
    if (r_w or {}).get("ok") is not True:
        ctx.g._disarm(bp_w)
    return r_g, r_w


def _hit_any(r_g, r_w):
    """两个断点里**有没有一个确实命中**(三态只认 `ok is True` —— 库造的记录没有 `hit` 这个键)。"""
    return (r_w or {}).get("ok") is True or (r_g or {}).get("ok") is True


def _dmd_words(blob):
    """`g_MaxDemand` 那 24 字节 → 6 个小端 u32(下标 0/1 = 正向/反向有功最大需量);
    字节数不够 24 ⇒ None(不许拿前几字节凑一个"看起来的数")。"""
    if not blob or len(blob) < 24:
        return None
    b = bytes(blob)
    return [int.from_bytes(b[4 * i:4 * i + 4], "little") for i in range(6)]


def _clock(ctx):
    """读一次表钟 → `datetime` 或 `None`(读不出来时出声并说清是哪一步)。"""
    now = cmd_bank.read_clock(ctx.ser, chip="计量芯", wait=WAIT, quiet=True)
    try:
        return datetime.datetime.strptime(now or "", "%Y-%m-%d %H:%M:%S"), now
    except ValueError:
        return None, now


def _aa80_alive(ser):
    """表还应不应答 —— 只读 `g_HisTime` 头 4 字节探一下(轮询用, 不能像整块读那样发多帧)。
    `g_HisTime` 不在画像里/解析不到 ⇒ 认 False(探不出来 = 当"没应答", 由超时那一支记未证)。"""
    blk = watch.named_blocks("g_HisTime")
    if not blk:
        print("   [白盒] 画像里没有 g_HisTime 的地址 ⇒ 表在不在探不出来")
        return False
    _name, addr, _size = blk[0]
    verdict, _hexs = watch.read_mem_aa80(ser, 1, addr - watch.RAM_BASE, 4, wait=1.5)
    return verdict == "PASS"


def _wait_power_cycle(ser):
    """等操作员把表断电再上电 —— **要看到"表消失过又回来"才算数**, 不听"我断过了"这句自述。

    判据与 `_test_1_2_energy_mirror.py` 的同名函数**同一个**: AA80 先连续 3 次读不成(那段窗口里
    表是停的), 再一次读成(表回来了)。超时返回 False ⇒ ⑬ 记未证(不冒充"重启过")。
    ⚠ 只断表的电, USB 桥别拔 —— 桥掉了串口句柄就废了, 这会一直读到失败, 只能记未证收场。
    """
    print("\n   【请操作员动手】把电表的电断掉, 停 ≥5 秒再上电(只断表的电, 别拔 USB 线)。")
    print("   ⚠ 要在拨钟后的**这一分钟内**断电: 拖过一个整分, 校时那一路会先把那 %d 条补掉," % ADD)
    print("     那时上电就无账可补, 这一段只能记未证(分不开那几条是谁写的)。")
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
    print("   [未证] %.0f 秒内没等到「表消失又回来」 —— ⑬ 记未证" % POWER_WAIT)
    return False


def part_day_frez(ctx):
    """一段 = 4-4 的全部条目: 读周期 → 拨钟 → 等跨日写库 → 698 读回 → 否定期望 → 第二个日界 →
    钉 g_HisTime → 补冻 + 跨月。

    ⚠ 观测一律 attach(**禁用 launch**: launch 会复位表、RAM 态清零); 见 CLAUDE.md 调试链纪律 1。
    """
    J, ser = ctx.J, ctx.ser
    print("\n===== 4-4 日冻结: 拨到日界前等自然跨日 → 断点看写库 → 698 读回 =====")

    # ---- ⑪ 冻结对象表第 3 行(FLASH 常量表, 探针按符号地址读; 不停核) ----
    # ⚠ 探针那一次读排在开会话**之前** —— 探针与 gdb 会话抢同一支 J-Link, 用完即关(`with Probe()`)。
    fb = so = None
    if not ctx.waived:
        try:
            with Probe() as pb:
                fb, so = cmd_bank.freobj_read(pb)
        except Exception as exc:            # 探针开不起来 / 读不成 → 这一条记"没做成"
            print("   !! 探针不可用(%s) ⇒ 对象表这一次读不到" % exc)
    rows_ob = cmd_bank.freobj_row(fb, so, OBJ_ROW)
    ok_ob, why_ob = cmd_bank.freobj_check(rows_ob, OBJ_ROW, tag="4-4 日冻结行")
    J.add("⑪ TAB_FrezObj 第 3 行翻出的 OAD == 出厂表登记的那 12 项",
          ok_ob, ("用户指定只做黑盒 ⇒ 这一条不做" if ctx.waived else why_ob),
          crit="⑪", falsify=cmd_bank.DAYFREZ_FALSIFY["⑪"], obs=judge.DEBUG)

    # ---- 前置 · AA80 读日冻结的存储信息(排边界日要用 prd; 拿不到就排不出, 别拿 1 硬猜) ----
    print("\n[前置] AA80 读日冻结的存储信息(%s[%d]) ..." % (CURRENT.FREZ_STORE_BLOCK, IDX))
    store = cmd_bank.frez_store_read(ser, CURRENT.FREZ_STORE_BLOCK, [IDX],
                                     clamp=CURRENT.FREZ_STORE_CLAMP, tag="日存储信息")
    info = (store or {}).get(IDX) or {}
    prd = info.get("prd")

    # ---- ⑫ 存储深度 == 365、一次补冻上限 == 7(与规范『应可存储 365 天』『最多补最近 7 个』对得上) ----
    # 深度那条的单一来源是固件自己的存储信息(`FrezGetStorageInfo` 也读它); 上限那条来自
    # `TAB_FrezAdd[EM_Day]`, 由 ⑦ 那一趟的实测条数印证 —— 这里只把两个数摆在一起记账。
    ok_dp, why_dp = cmd_bank.frez_store_depth_evidence(
        store, IDX, want_depth=CURRENT.DAYFREZ_DEPTH, tag="4-4 日冻结")
    J.add("⑫ 日冻结的存储深度 == %d, 且一次补冻上限 == %d" % (CURRENT.DAYFREZ_DEPTH, ADD),
          ok_dp, "%s; 本项补冻上限 = TAB_FrezAdd[EM_Day] = %d(由 ⑦ 那一趟实测条数印证)" % (why_dp, ADD),
          crit="⑫", falsify=cmd_bank.DAYFREZ_FALSIFY["⑫"], obs=judge.DEBUG)

    if not prd or prd == 0xFFFF:
        _stop_unproven(ctx, "日通道(表项 %d)的周期读不到或不可用(prd=%s) ⇒ 排不出边界日"
                            % (IDX, prd))
    print("   表项 %d: prd=%d 天 depth=%s size=%s" % (IDX, prd, info.get("depth"), info.get("size")))

    # ---- 前置 · 进厂内(记录读回受安全判定管; 且厂内态不更新冻结时刻, 补冻才做得出来)+ 现最新一条 ----
    cmd_bank.enter_factory(ser)
    pre = cmd_bank.read_freeze_row(ser, SUB, 1, wait=WAIT, empty_ok=True)
    print("   基线: 日冻结(子类 0x%02X) 最新一条 = %s" % (SUB, cmd_bank.rec_row_txt(pre)))

    # ---- 开调试会话(断点观测)。台面没接 J-Link ⇒ None, 白盒那几条如实记"没做成" ----
    ctx.session()
    if ctx.g is not None:
        # 整片 .out 解一次(停核遍历, 内部喂狗, 收尾放行) —— 之后一律查这张图
        gdbinit.build(ctx.g)
    g = ctx.g
    have_wb = g is not None
    if not have_wb:
        print("\n   !! 本次无调试会话 → 断点观测(白盒)整体**未做**; 本次范围 = 仅黑盒"
              "(黑盒只能答『记录有没有推进、时标对不对』, 答不了『写点判的是不是 0 点』)")

    # ---- 拨钟到第一个日界的前一日 23:59:05 ----
    now_dt, now = _clock(ctx)
    if now_dt is None:
        _stop_unproven(ctx, "表钟读不出来(%r) ⇒ 排不出边界日" % now)
    b1 = cmd_bank.frez_day_boundary(now_dt, prd)
    tgt = cmd_bank.frez_day_target(now_dt, b1)
    print("\n[拨钟] 表钟=%s → 目标=%s(日界 = 绝对天数 %d, 整除 prd %d 天)" % (now, tgt, b1, prd))
    cmd_bank.set_meter_clock_set(ser, tgt, chip="计量芯", wait=WAIT)

    # ---- ①②③ 等自然跨日停在写库点; 同一趟在 断[A] 那一刻把正/反向有功最大需量注成非零(⑩ 的触发) ----
    normal = frez_num_g = frez_num = prd_bp = None
    buff = []
    r_g = r_w = None
    vw = {}          # 断[B] 那一停读到的一组量(没有会话时保持空 dict, ⑩ 要它)
    inj = None       # 第一次注入的返回(⑩ 凭 `injected` 认"字真写进去了")
    if have_wb:
        print("\n[等] 自然分钟步进走到日界写库 + 注非零需量(断[A] %s → 注 %s → 断[B] %s, 至多 %.0fs) ..."
              % (breakpoint.text(BP_GATE), "/".join(DMD_NAMES), breakpoint.text(BP_WRITE), HIT_TIMEOUT))
        # ⚠ 这里用 `with_inject` 而不是 `_arm`+`_wait`: 注入只在**停住态**成立, 而 `wait_hit` 读完就
        #   放行 —— "停 断[A] → 写 需量 → 放行 → 停 断[B]" 这四步的时序只能由同一个原语管。
        #   注入点在 断[A] `:493`(`Prep_ObjData` 之前), 所以整趟写库都在它**之后**, 跨日那趟动的
        #   就是注入进去的那个值。收尾由 `close()` 自动写回原值(注入的是全局量)。
        inj = g.with_inject(BP_GATE, DMD_ASSIGNS, watch=BP_WRITE,
                            at_vars=VARS_GATE, watch_vars=VARS_WRITE, timeout=HIT_TIMEOUT)
        # 返回值映射成 `_wait` 那个形状(`ok`/`vars`/`detail`), 后面几条判据与打印一行不用改。
        at_hit, w_hit = inj.get("at_hit"), inj.get("hit")
        r_g = {"ok": at_hit is not None, "vars": inj.get("at_vals") or {},
               "detail": "断[A] 命中 %s" % at_hit.where() if at_hit is not None
                         else "断[A] 没停到(注入点没到)"}
        r_w = {"ok": w_hit is not None, "vars": inj.get("vars") or {},
               "detail": "断[B] 命中 %s" % w_hit.where() if w_hit is not None
                         else "断[B] 没停到"}
        if inj.get("note"):
            print("   注入: %s" % inj["note"])
        vg = (r_g or {}).get("vars") or {}
        vw = (r_w or {}).get("vars") or {}
        normal = cmd_bank.st_int(vg.get("normal"))
        frez_num_g = cmd_bank.st_int(vg.get("frezNum"))
        frez_num = cmd_bank.st_int(vw.get("frezNum"))
        prd_bp = cmd_bank.st_field(vw.get("stInfo"), "u16Period")
        buff = cmd_bank.gdb_bytes(vw.get("buff"))
        print("   现场: %s / %s | normal=%s frezNum=%s over=%s days=%s stInfo.u16Period=%s buff=%s"
              % ((r_g or {}).get("detail") or "断[A] 没停到",
                 (r_w or {}).get("detail") or "断[B] 没停到", normal, frez_num,
                 cmd_bank.st_int(vg.get("over")), cmd_bank.gdb_ints(vg.get("days")), prd_bp,
                 " ".join("%02X" % x for x in buff[:6]) if buff else "读不到"))
    else:
        print("\n[等] 无调试会话 ⇒ 写库点这一半不做(不假装等过)")

    # ---- ① 驱动源: 自然分钟步进 ⇒ normal == TRUE(0xAA = 170) ----
    # ⚠ `int` 不是 `bool`: 0 也是"读到了值", 拿 `if normal:` 判会把"读到 0"当成"没读到"。
    J.add("① 停在 %s 那一刻 normal == TRUE" % breakpoint.text(BP_GATE),
          None if normal is None else (normal == 170),
          ("没有调试会话 ⇒ 这一半不做" if not have_wb else
           "normal=%s(%s)%s" % (normal, {170: "TRUE", 85: "FALSE"}.get(normal, "?"),
                                "" if normal == 170 else
                                (" —— 没读回 normal" if normal is None else
                                 " —— 停到的那一趟不是自然跨日"))),
          crit="①", falsify=cmd_bank.DAYFREZ_FALSIFY["①"], obs=judge.DEBUG)

    # ---- ② 跨日那趟只写 1 条 ----
    J.add("② 跨日那一趟只写 1 条(frezNum == 1)",
          None if frez_num is None else (frez_num == 1),
          ("没有调试会话 ⇒ 这一半不做" if not have_wb else
           "frezNum=%s(写的是 ID_DayFrez = 表项 %d; 断[A] 处读到的 frezNum=%s)"
           % (frez_num, IDX, frez_num_g)),
          crit="②", falsify=cmd_bank.DAYFREZ_FALSIFY["②"], obs=judge.DEBUG)

    # ---- ③ 时标落在 0 点: 秒/分/时全 0, 日是 prd 的整数倍, 日月年 == 跨日那天 ----
    sec_b = buff[0] if len(buff) > 0 else None
    min_b = buff[1] if len(buff) > 1 else None
    hour_b = buff[2] if len(buff) > 2 else None
    day_b = buff[3] if len(buff) > 3 else None
    mon_b = buff[4] if len(buff) > 4 else None
    yr_b = buff[5] if len(buff) > 5 else None
    # 跨的是**日界那天**= `b1`(第 195 行算出的绝对天数)那一天 —— 校时目标停在它前一日 23:59:05,
    # 55s 后自然跨到它 00:00。`frez_day_date` 是固件 `Locate_Days` 的同一套算法(基准 2000-01-01),
    # 故三年月年本该逐字相等; 拿它当对照物, 不看"像不像 0 点"。
    _cday = cmd_bank.frez_day_date(b1)
    want_ymd = (_cday.day, _cday.month, _cday.year - 2000) if b1 else None
    # ⚠ `prd_bp` 读到 0 时取模会抛(固件在 :466 已把 prd==0 挡掉, 但那是**另一条**读法) —— 先判它。
    ok3 = (None if (None in (sec_b, min_b, hour_b, day_b, mon_b, yr_b) or not prd_bp)
           else (sec_b == 0 and min_b == 0 and hour_b == 0 and day_b % prd_bp == 0
                 and (day_b, mon_b, yr_b) == want_ymd))
    J.add("③ 写库那一刻 buff 的秒/分/时为 0、日是 prd 的整数倍, 且日月年 == 跨日那天的日月年", ok3,
          ("写库那一刻 buff 读不到 ⇒ 没有对照物, 这一条没做成" if sec_b is None else
           "buff[:6]=%s ⇒ 时=%s 日=%s 月=%s 年=%s prd=%s(跨日那天 %s)(%s)"
           % (" ".join("%02X" % x for x in buff[:6]), hour_b, day_b, mon_b, yr_b, prd_bp,
              _cday, "0 点且在网格上、日月年对得上" if ok3 else "**不是 0 点 / 不在网格上 / 日月年不对**")),
          crit="③", falsify=cmd_bank.DAYFREZ_FALSIFY["③"], obs=judge.DEBUG)

    # ---- ④ 698 读回: 最新一条的时标 == 那一刻 buff 解出的 0 点 ----
    if SETTLE:
        time.sleep(SETTLE)          # 停在断[B](Write_FrezData 的**调用行**) ⇒ 等它落完
    # ---- ⑩ 的两次读数: 写库那一刻(断[B] 经 gdb 读到的 g_MaxDemand)与放行之后(AA80 读回) ----
    # 写库那一刻读的是"注入写进去了没有", 放行之后读的是"跨完日界它归没归零" —— 两次都要。
    # ⚠ 放行必须在 698/AA80 之前(`with_inject` 命中后自动放行, 上面那 SETTLE 已覆盖放行窗口)。
    dmd_stop = (vw or {}).get("g_MaxDemand")
    dmd_after = watch.watch_vars(ser, ["g_MaxDemand"], tag="4-4 需量").get("g_MaxDemand")
    print("   [需量] g_MaxDemand 写库那一刻=%s 放行后=%s"
          % (" ".join("%02X" % x for x in dmd_stop) if dmd_stop else "读不到",
             " ".join("%02X" % x for x in dmd_after) if dmd_after else "读不到"))
    post = cmd_bank.read_freeze_row(ser, SUB, 1, wait=WAIT, empty_ok=True)
    want_ts = cmd_bank.frez_expect_ts(buff)
    got_ts = (post or {}).get("ts")
    J.add("④ 698 读回最新一条的时标 == 写库那一刻 buff 的时标",
          None if (want_ts is None or got_ts is None) else (got_ts == want_ts),
          ("写库那一刻 buff 读不到 ⇒ 没有对照物, 这一条没做成" if want_ts is None else
           "记录 %s; 写库那一刻 buff[:6] 解出的时标 = %s%s"
           % (cmd_bank.rec_row_txt(post), want_ts,
              "" if got_ts == want_ts else " —— 读回的是 %s" % (got_ts or "读不到"))),
          crit="④", falsify=cmd_bank.DAYFREZ_FALSIFY["④"])

    # ---- ⑤ 非边界日的那些分钟里不落库(否定期望) ----
    if have_wb:
        c0 = cmd_bank.read_clock(ser, chip="计量芯", wait=WAIT, quiet=True)
        print("\n[等] 否定期望: %.0fs 窗口里 %s 不该命中(窗口比一个整分间隔长) ..."
              % (NEG_WIN, breakpoint.text(BP_WRITE)))
        r5 = breakpoint.expect_no_hit(g, BP_WRITE, NEG_WIN, vars=VARS_WRITE,
                              label="4-4 非日界不落库(否定期望)",
                              falsify=cmd_bank.DAYFREZ_FALSIFY["⑤"])
        c1 = cmd_bank.read_clock(ser, chip="计量芯", wait=WAIT, quiet=True)
        crossed = cmd_bank.frez_crossed(c0, c1)
        # 窗口里没跨过整分 ⇒ 这次否定期望**空转**(什么都没看), 记"没做成" —— 不许记达成。
        J.add("⑤ %.0fs 窗口里 %s 不命中, 且窗口里表钟跨过一个整分" % (NEG_WIN, breakpoint.text(BP_WRITE)),
              None if (r5 is None or crossed is None) else (r5.get("ok") is True and crossed is True),
              "窗口内表钟 %s → %s(%s); %s"
              % (c0, c1, "跨过整分" if crossed else "**没跨过整分, 这次否定期望空转**",
                 (r5 or {}).get("detail") or "没做成"),
              crit="⑤", falsify=cmd_bank.DAYFREZ_FALSIFY["⑤"], obs=judge.DEBUG)
    else:
        J.add("⑤ %.0fs 窗口里 %s 不命中, 且窗口里表钟跨过一个整分" % (NEG_WIN, breakpoint.text(BP_WRITE)), None,
              "没有调试会话 ⇒ 这一半不做(黑盒没有『没落库』的替身: 读不到记录与没落库分不开)",
              crit="⑤", falsify=cmd_bank.DAYFREZ_FALSIFY["⑤"], obs=judge.DEBUG)

    # ---- ⑥ 相邻两条记录的时标相差 prd 天 ----
    # 第二个日界是**拨钟推进 prd 天**造出来的(不是真等): 它证"写点落在整数倍 prd 的天上、时标就是
    # 那个 0 点", **不证实时周期** —— 实时周期那半支由 ⑤ 的否定期望窗口担着。
    if have_wb:
        d2, now2 = _clock(ctx)
        if d2 is None:
            J.add("⑥ 相邻两条读回记录的时标相差 prd 天", None,
                  "表钟读不出来(%r) ⇒ 第二个日界排不出" % now2,
                  crit="⑥", falsify=cmd_bank.DAYFREZ_FALSIFY["⑥"], obs=judge.DEBUG)
        else:
            b2 = b1 + prd
            tgt2 = cmd_bank.frez_day_target(d2, b2)
            print("\n[拨钟] 第二个日界: 表钟=%s → 目标=%s(日界 绝对天数 %d = 上一个 + prd 天)"
                  % (now2, tgt2, b2))
            cmd_bank.set_meter_clock_set(ser, tgt2, chip="计量芯", wait=WAIT)
            bp_g2, bp_w2 = _arm(ctx)
            r_g2, r_w2 = _wait(ctx, bp_g2, bp_w2, "4-4 第二个日界")
            if SETTLE:
                time.sleep(SETTLE)
            p1 = cmd_bank.read_freeze_row(ser, SUB, 1, wait=WAIT, empty_ok=True)
            p2 = cmd_bank.read_freeze_row(ser, SUB, 2, wait=WAIT, empty_ok=True)
            gap = cmd_bank.frez_ts_gap(p1, p2)
            want_gap = prd * 1440          # `frez_ts_gap` 给的是**分钟**
            J.add("⑥ 相邻两条读回记录的时标相差 prd 天",
                  None if gap is None else (gap == want_gap),
                  "pos1 %s / pos2 %s ⇒ 相差 %s 分钟(prd=%d 天 = %d 分钟); %s"
                  % (cmd_bank.rec_row_txt(p1), cmd_bank.rec_row_txt(p2),
                     gap if gap is not None else "比不出来", prd, want_gap,
                     (r_w2 or {}).get("detail") or "第二个日界没停到"),
                  crit="⑥", falsify=cmd_bank.DAYFREZ_FALSIFY["⑥"], obs=judge.DEBUG)
    else:
        J.add("⑥ 相邻两条读回记录的时标相差 prd 天", None,
              "没有调试会话 ⇒ 第二个日界造不出来(拨不到既定的那个 0 点)", crit="⑥",
              falsify=cmd_bank.DAYFREZ_FALSIFY["⑥"], obs=judge.DEBUG)

    # ---- ⑦⑧ 校时跨多日补冻 + 自然跨月: 先钉住 g_HisTime, 再拨到未来最近的月末 ----
    lbl_catch = "⑦ 校时那趟补冻 %d 条且 days[] 两两相差 prd" % ADD
    lbl_month = "⑧ 自然跨月那条时标 == 次月 1 日 00:00:00"
    if have_wb:
        # 第一步: 同一天内的校时(day1 == day2 ⇒ 一条都不写), 只为把 g_HisTime 钉在白天。
        # ⚠ 必须做: `:140` 每趟收尾把 g_HisTime 置为当前时间, 若紧接着在 00:00:0x 那一刻去拨月末,
        #   day2 可能是当天 00:00:00 也可能是上一分钟的 23:59:59(差一天 ⇒ 补冻条数差 1 ⇒ ⑦ 冤红)。
        d_mid, now_mid = _clock(ctx)
        mid = None if d_mid is None else cmd_bank.frez_midday_target(d_mid)
        if mid is not None:
            print("\n[拨钟] 钉住 g_HisTime: 表钟=%s → 目标=%s(同一天内, 这一趟 day1 == day2 ⇒ 不写库)"
                  % (now_mid, mid))
            cmd_bank.set_meter_clock_set(ser, mid, chip="计量芯", wait=WAIT)
        if SETTLE:
            time.sleep(SETTLE)
        d3, now3 = _clock(ctx)
        plan = None if d3 is None else cmd_bank.frez_catchup_plan(d3, prd, ADD)
        if plan is None:
            J.add(lbl_catch, None, "表钟读不出来或 24 个月内找不到合适的月末 ⇒ 这一步没做成",
                  crit="⑦", falsify=cmd_bank.DAYFREZ_FALSIFY["⑦"], obs=judge.DEBUG)
            J.add(lbl_month, None, "同上 ⇒ 自然跨月那条造不出来",
                  crit="⑧", falsify=cmd_bank.DAYFREZ_FALSIFY["⑧"], obs=judge.DEBUG)
        else:
            print("   g_HisTime 钉在 %s; 月末计划: 目标=%s 边界日=%d 补冻那天=%d 该补 %d 条(上限 %d, over=%s)"
                  % (now3, plan["tgt"], plan["d1"], plan["trip_day"], plan["count"],
                     ADD, plan["want_over"]))
            # ⚠ **先挂断点再拨钟** —— 那一趟是"拨完钟、下一个分钟步进就写"; 拨完再挂就漏掉了它,
            #   而漏掉的表现是"没命中"(与断点没挂上长得一样)。
            # 断电段要用"最新一条日冻结"当天做基准, 所以这 7 条落库前的条数先记一份基线
            n_before = cmd_bank.frez_area_count(ser, SUB)
            bp_g7, bp_w7 = _arm(ctx)
            cmd_bank.set_meter_clock_set(ser, plan["tgt"], chip="计量芯", wait=WAIT)
            r_g7, r_w7 = _wait(ctx, bp_g7, bp_w7, "4-4 拨钟跨多日")
            want_days = [plan["trip_day"] - k * prd for k in range(plan["want"])]
            vg7 = (r_g7 or {}).get("vars") or {}
            got_days = cmd_bank.gdb_ints(vg7.get("days"))
            n_seen = cmd_bank.st_int(vg7.get("frezNum"))
            # ---- ⑧ 自然跨月那条: ⑦ 拨到的是本月末, 跨的是**这个**月界(上面那一趟 already 跨了) ----
            #   所以这一条另拨一次: ⑦ 取证完(补冻那 7 条写完)再拨到**下一个月末** 23:59:05,
            #   55s 后表钟自然跨到次月 1 日 00:00:00, 那一趟自己走一遍月界。
            # ⑦ 的黑盒那半边: 断点看到的是"它打算补 7 条", 落没落、落在哪一天, 只有 698 数得出来。
            # 等它写完再数 —— 与 ⑧ 之前那一段是同一个等待, 挪到这里只是提前十几秒, 不影响 ⑧ 的排期。
            if SETTLE:
                time.sleep(CATCHUP_SETTLE)
            n_after = cmd_bank.frez_area_count(ser, SUB)
            rows7 = [cmd_bank.read_freeze_row(ser, SUB, k, wait=WAIT, empty_ok=True)
                     for k in range(1, plan["want"] + 1)]
            got_ts7 = [(r or {}).get("ts") for r in rows7]
            want_ts7 = [cmd_bank.frez_day_date(d).strftime("%Y-%m-%d 00:00:00") for d in want_days]
            n_ok7 = None if (n_before is None or n_after is None) \
                else (n_after - n_before == plan["want"])
            ts_ok7 = None if any(t is None for t in got_ts7) else (got_ts7 == want_ts7)
            # 三个半支各有"没做成"的可能(条数读不出/记录读不出) ⇒ 那一支记未证, 不折成 FAIL
            hit_ok7 = (n_seen == plan["want"] and got_days[:plan["want"]] == want_days)
            ok7 = None if None in (n_ok7, ts_ok7, hit_ok7) else bool(hit_ok7 and n_ok7 and ts_ok7)
            why7 = ("补冻 frezNum=%s(期望 %s); days[:%d]=%s(期望 %s); 现场: normal=%s over=%s; "
                    "698 条数 %s→%s(期望 +%d, 多出这么多 ⇒ 第 %d 天前那条没补); "
                    "最新 %d 条时标 %s(期望 %s)%s"
                    % (n_seen, plan["want"], plan["want"], got_days[:plan["want"]], want_days,
                       cmd_bank.st_int(vg7.get("normal")), cmd_bank.st_int(vg7.get("over")),
                       n_before, n_after, plan["want"], plan["want"] + 1,
                       plan["want"], got_ts7, want_ts7,
                       "" if ok7 else (" —— 没读全, 记未证" if ok7 is None else " —— 对不上")))
            J.add(lbl_catch, ok7, why7, crit="⑦", falsify=cmd_bank.DAYFREZ_FALSIFY["⑦"], obs=judge.DEBUG)

            d4, now4 = _clock(ctx)
            plan8 = None if d4 is None else cmd_bank.frez_catchup_plan(d4, prd, ADD)
            if plan8 is None:
                J.add(lbl_month, None, "表钟读不出来或 24 个月内找不到下一个合适的月末 ⇒ 这一步没做成",
                      crit="⑧", falsify=cmd_bank.DAYFREZ_FALSIFY["⑧"], obs=judge.DEBUG)
            else:
                print("\n[拨钟] 下一个跨月窗口: 表钟=%s 目标=%s(55s 后自然跨到 %s)"
                      % (now4, plan8["tgt"], cmd_bank.frez_day_date(plan8["trip_day"])))
                # ⚠ 这一趟**只挂写点**(`with_gate=False`)—— 断[A] 那边挂了也没用(那趟早过了 :493)。
                bp_g8, bp_w8 = _arm(ctx, with_gate=False)
                cmd_bank.set_meter_clock_set(ser, plan8["tgt"], chip="计量芯", wait=WAIT)
                r_g8, r_w8 = _wait(ctx, bp_g8, bp_w8, "4-4 跨月跨日")
                if SETTLE:
                    time.sleep(SETTLE)
                pm = cmd_bank.read_freeze_row(ser, SUB, 1, wait=WAIT, empty_ok=True)
                # 期望的时标: 次月 1 日 00:00:00 —— 由拨钟目标直接算出(月末那天 23:59:05 + 55s)
                exp8 = (datetime.datetime.strptime(plan8["tgt"], "%Y-%m-%d %H:%M:%S")
                        + datetime.timedelta(minutes=1)).strftime("%Y-%m-%d 00:00:00")
                got8 = (pm or {}).get("ts")
                J.add(lbl_month, None if got8 is None else (got8 == exp8 and _hit_any(r_g8, r_w8)),
                      "记录 %s; 期望 %s(跨月那一刻 buff 解出的是 %s); %s"
                      % (cmd_bank.rec_row_txt(pm), exp8,
                         cmd_bank.frez_expect_ts(cmd_bank.gdb_bytes(((r_w8 or {}).get("vars") or {}).get("buff")))
                         or "读不到",
                         (r_w8 or {}).get("detail") or "断[B] 没停到"),
                      crit="⑧", falsify=cmd_bank.DAYFREZ_FALSIFY["⑧"], obs=judge.DEBUG)
    else:
        J.add(lbl_catch, None, "没有调试会话 ⇒ 这一条不做",
              crit="⑦", falsify=cmd_bank.DAYFREZ_FALSIFY["⑦"], obs=judge.DEBUG)
        J.add(lbl_month, None, "没有调试会话 ⇒ 这一条不做(黑盒看不出那条是谁写的)",
              crit="⑧", falsify=cmd_bank.DAYFREZ_FALSIFY["⑧"], obs=judge.DEBUG)

    # ---- ⑨ 冻结电能 == 前一日累计: 本台 0 负载, 答不出 falsify(声明在 criteria 里) ----
    # 这一条**任何固件都满足** ⇒ 不构成证据, 恒记"没做成"; 判据表里它自己写着 unprovable。
    J.add("⑨ 冻结电能 == 前一日累计(本台 0 负载, 证不了)", None,
          "本台 0 负载 ⇒ 前一日累计与当前累计都是 0, 任何把 0 写进去的固件都满足; 库里有现成的"
          "`check_freeze_snapshot`(读记录电能列 + 读当前电能对象整列逐字节对拍), 但它证的是"
          "『记录里那两列与当前电能量对象一致』, 0 负载下同样分不出『真 0』与『写坏成全 0』, "
          "换不掉本条的结论; 要证须带非零负载跑满一日, 或把电能对象在 RAM 里注入成非零再跨日",
          crit=None, obs=judge.SERIAL)

    # ---- ⑩ 跨过日界那一趟把当日正反向有功最大需量归零 ----
    # 0 负载下需量恒 0, 且 `g_MaxDemand` 全固件只清零不写入 ⇒ 帧通道下"清没清"读出来一样。
    # 所以这一条只能靠注入: 在 断[A] `:493` 那一刻把两格写成非零(整趟写库都在它之后), 放行让这一趟
    # 走完, 再读回看它归没归零。falsify: 跨完日界这两格还是那个非零值。
    dmd_s, dmd_a = _dmd_words(dmd_stop), _dmd_words(dmd_after)
    if not have_wb:
        ok10, why10 = None, "没有调试会话 ⇒ 注入做不成(注入只在停住态成立), 这一条不做"
    elif not (inj or {}).get("injected"):
        ok10, why10 = None, ("非零需量没写进去(%s) ⇒ 跨日前后都是 0, 这一次分不开"
                             % ((inj or {}).get("note") or (inj or {}).get("trigger_error")
                                or "没停到注入点"))
    elif dmd_a is None:
        ok10, why10 = None, "放行后读不回 g_MaxDemand ⇒ 没有对照物, 这一条没做成"
    else:
        ok10 = (dmd_a[0] == 0 and dmd_a[1] == 0)
        why10 = ("注入 %s = 0x%X(各 %d); 写库那一刻读到 [0]=%s [1]=%s; 跨完日界读回 [0]=%s [1]=%s%s"
                 % (" / ".join(DMD_NAMES), DMD_INJ, DMD_INJ,
                    dmd_s[0] if dmd_s else "读不到", dmd_s[1] if dmd_s else "读不到",
                    dmd_a[0], dmd_a[1],
                    "" if ok10 else " —— 跨完日界还是那个非零值, 这一趟没把它归零"))
    J.add("⑩ 跨过日界那一趟把当日正反向有功最大需量归零(注入非零再跨日)", ok10, why10,
          crit="⑩", falsify=cmd_bank.DAYFREZ_FALSIFY["⑩"], obs=judge.DEBUG)


def part_power_catchup(ctx):
    """4-4 步骤 3 的后半: 断电跨过「补冻上限 + 1 天」再上电, 上电那一路补出来的条数一样是上限。

    ⚠ 两条路(校时那趟 / 上电那趟)写出的记录**内容一模一样** —— 同一个 Check_DayFrez、同一套补冻
      集合, 光看条数分不出是谁写的。要分开只能靠时序, 所以这一段分三步:
        ① 先拨表钟到「最新那条日冻结 + GAP_DAYS 天」, 拨完**立刻**断电(还没跨过一个整分);
        ② 上电后**抢在第一个分钟步进之前**读一次条数 —— 仍等于断电前的数, 证明上电那一刻还没补;
        ③ 跨过第一个分钟步进之后再读一次 —— 多出 ADD 条, 那才是上电那一路补的(上电时
           `Chk_FrezStamp` 从最新记录重建 g_HisTime, 下一趟 Check_DayFrez 发现落了 8 天)。
      抢不到 ②(上电后第一次读就已经多出 ADD 条)⇒ 这一次分不开, 记未证, 不记达成也不记失败。
    ⚠ 期望条数 = ADD 的前提是"最新的冻结记录就是日冻结那条"(`Chk_FrezStamp` 取各冻结类里最新的一条
      重建 g_HisTime)。上电后先读一次 pos1: 它的日期不是拨钟目标那天 ⇒ 别的类先写过, 该补几天要
      另算, 记未证。
    ⚠ 断电后不再发任何 gdb 命令(目标被复位, 会话关系可能失稳); 收尾由 trial 的 close() 走
      `python -m swdbg.restore` 兜底 —— 与 `_test_1_2_energy_mirror.py` 的断电段同款。
    """
    J, ser = ctx.J, ctx.ser
    lbl = "⑬ 断电跨 %d 天再上电, 上电那一路补出来的条数一样是 %d" % (GAP_DAYS, ADD)
    print("\n===== 4-4 断电补冻: 拨到「最新一条 + %d 天」→ 人工断电 ≥5s → 上电补冻 =====" % GAP_DAYS)
    cmd_bank.enter_factory(ser)
    top = cmd_bank.read_freeze_row(ser, SUB, 1, wait=WAIT, empty_ok=True)
    try:
        top_dt = datetime.datetime.strptime((top or {}).get("ts") or "", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        J.add(lbl, None, "最新一条日冻结读不出时标(%s) ⇒ 排不出「+%d 天」的目标时刻"
              % (cmd_bank.rec_row_txt(top), GAP_DAYS),
              crit="⑬", falsify=cmd_bank.DAYFREZ_FALSIFY["⑬"], obs=judge.SERIAL)
        return
    n0 = cmd_bank.frez_area_count(ser, SUB)
    tgt = (top_dt + datetime.timedelta(days=GAP_DAYS)).strftime("%Y-%m-%d ") \
        + CURRENT.DAYFREZ_MIDDAY_TOD
    print("\n[拨钟] 最新一条 = %s; 目标 = %s(比它晚 %d 天) —— 拨完**立刻**按提示断电"
          % (top_dt, tgt, GAP_DAYS))
    cmd_bank.set_meter_clock_set(ser, tgt, chip="计量芯", wait=WAIT)
    if not _wait_power_cycle(ser):
        J.add(lbl, None, "%.0f 秒内没等到「表消失又回来」⇒ 这一次没做成(不冒充重启过)" % POWER_WAIT,
              crit="⑬", falsify=cmd_bank.DAYFREZ_FALSIFY["⑬"], obs=judge.SERIAL)
        return
    cmd_bank.enter_factory(ser)                      # 上电后厂内态要重新进
    _d_up, now_up = _clock(ctx)
    n_early = cmd_bank.frez_area_count(ser, SUB)     # ② 第一个分钟步进之前
    top_up = cmd_bank.read_freeze_row(ser, SUB, 1, wait=WAIT, empty_ok=True)
    top_day = ((top_up or {}).get("ts") or "")[:10]
    t_end = time.time() + 90.0                       # ③ 等表钟的"分"变一次(第一个分钟步进过去)
    while time.time() < t_end:
        _d_step, now_step = _clock(ctx)
        if now_step and now_up and now_step[:16] != now_up[:16]:
            break
        time.sleep(3)
    if SETTLE:
        time.sleep(SETTLE)
    n_late = cmd_bank.frez_area_count(ser, SUB)
    why_base = ("断电前 %s 条; 上电后第一次读 %s 条(当时表钟 %s); 跨过第一个分钟步进后 %s 条; "
                "上电后 pos1 = %s(期望 %s 那天)"
                % (n0, n_early, now_up, n_late, cmd_bank.rec_row_txt(top_up), tgt[:10]))
    if None in (n0, n_early, n_late):
        ok13, why13 = None, why_base + " ⇒ 条数没读全, 这一次没做成"
    elif now_up is None or now_up < tgt:
        ok13, why13 = None, ("台面事实: 上电后表钟 %s 没到拨钟目标 %s(断电期间时钟没走稳或丢了) ⇒ "
                             "上电那一路的前提『当前时间 > 历史时间』(TaskFreeze.c:127-128)不成立; %s"
                             " ⇒ 这一次没做成" % (now_up, tgt, why_base))
    elif n_early != n0:
        ok13, why13 = None, ("上电后第一次读就已经是 %s 条(断电前 %s) ⇒ 没抢在第一个分钟步进之前, "
                             "分不开这些条是校时那一路还是上电那一路写的; %s ⇒ 记未证"
                             % (n_early, n0, why_base))
    elif top_day != tgt[:10]:
        ok13, why13 = None, ("上电后最新一条的日期 %s 不是拨钟目标 %s ⇒ 别的冻结类先写过, 该补几天"
                             "要另算(不是 %d); %s ⇒ 这一次没做成" % (top_day, tgt[:10], ADD, why_base))
    else:
        ok13 = (n_late - n0 == ADD)
        why13 = ("%s ⇒ 多出的 %d 条是上电那一路补的(期望 %d = TAB_FrezAdd[EM_Day])%s"
                 % (why_base, n_late - n0, ADD, "" if ok13 else " —— 对不上"))
    J.add(lbl, ok13, why13, crit="⑬", falsify=cmd_bank.DAYFREZ_FALSIFY["⑬"], obs=judge.SERIAL)


def _banner():
    return ("== 4-4 日冻结 | 工程=%s 表号=%s ==\n"
            ".. 驱动=拨钟到「日界前一日 23:59:05」后等**自然分钟步进**(拨钟那一帧自己不写); "
            "白盒两个断点 %s(读 %s) → %s(读 %s); 第一个日界那一趟在 断[A] 处注入 %s = 0x%X(⑩); "
            "记录读回走 698 GetRequestRecord 子类 0x%02X; "
            "⚠ 跑完表钟停在「最新一条日冻结 + %d 天」的白天(中途有一次人工断电), 收尾走 "
            "scripts/_restore_all.py"
            % (CURRENT.PROJECT, CURRENT.TABLE_ADDR.hex().upper(),
               breakpoint.text(BP_GATE), "/".join(VARS_GATE),
               breakpoint.text(BP_WRITE), "/".join(VARS_WRITE), "/".join(DMD_NAMES), DMD_INJ,
               cmd_bank.DAYFREZ_SUBCLASS, GAP_DAYS))


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "4-4 日冻结(0 点边界写库/一趟 1 条/时标与 698 读回一致/非日界不落库/相邻两条差 prd 天/"
        "拨钟跨多日补冻/跨月跨日)",
        cmd_bank.day_frez_criteria,
        name="4_4_day_frez",
        parts=[("4-4 日冻结段", part_day_frez), ("4-4 断电补冻段", part_power_catchup)],
        gdb=breakpoint, banner=_banner(),
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV,
                        inject_allow=DMD_NAMES)))
