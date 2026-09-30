# -*- coding: utf-8 -*-
"""在证什么: 分钟冻结只在通道周期 prd 的整数倍分钟落库、只写 0 号通道 1 条, 时标就是那个边界分钟。
会向表写什么: 698 Set 40000200 拨两次表钟; ⑨ 两次 698 Set 50020300 试图把通道间隔写成 0 / 61
  (期望都被拒、一个字都不落库); ⑫ **真改表配置** —— 用 698 Action 50020500 把通道 0 的关联对象
  删空、再用 50020400 把间隔设成 1 与 60, 收尾按出厂配置重建(间隔 15 / 深度 35040 / 18 个对象)。
⚠ ⑫ 那段动的是关联对象表, 它住 EEPROM、AA80 三个区够不到 —— 重建失败时 `_restore_all.py` 也够不到,
  只能人工重配(脚本会当场出声)。跑完表钟停在第二次目标时刻, 收尾走
  `python scripts/_restore_all.py` —— 本脚本不发时钟收集尾。
跑法: `python project/tests/_test_4_2_minute_frez.py`(真串口 + 真探针; `--no-gdb` 只做黑盒)。
  加 `--fill-year` 才跑末段 ⑬(真铺满 35040 条)—— 那一格约 2 秒、整段连跑十几小时, 默认一行不发。
结论怎么读: 账本末行「判据: 满足 n/N」, 默认跑次 N=12、带 `--fill-year` 的跑次 N=13
  (⑪ 电量对照要写库那一刻的 buff ⇒ 无会话时未证; ⑫ 只走帧通道, 有没有会话都判得出);
  ⑩ 按固件自报的记录长/区首地址离线算, ⑬ 是真写 35040 条 —— 两条证的不是同一件事
"""
import datetime
import sys
import time

from common import faultlog         # 失败就地记账(第 22 条): 铺满段卡住时先落一笔再停
from common import judge            # 观测种类常量(断点那几条证据标 DEBUG)
from common import trial            # 运行外壳: argv/日志/账本/串口/会话/收尾三连/退出码
from meterlib import cmd_bank       # 帧目录 + 语义动词积木箱(每步自带打印)
from project import CURRENT          # 本工程画像: 记录子类/AA80 块/等待窗/.out/喂狗点的单一事实源
from swdbg import breakpoint            # 断点级白盒(停核 + 读函数内局部量); 没接 J-Link 会自动降级
from swdbg import gdbinit            # 整片 .out 地图: 开会话后解一次, 之后断点/注入点只查它
from swdbg.probe import Probe        # SWD 直读(读 FLASH 常量表; 不停核)

# ---- 子项参数(纯数据; 要改的在这里改) ----
SUB = CURRENT.MINFREZ_SUBCLASS      # 分钟冻结记录子类(记录 OAD = 50 02 02 00)
WAIT = 3.0                    # 记录读回 / 拨钟确认的单次等待
SETTLE = 3.0                  # 停在写库点之后等它落完再读回
HIT_TIMEOUT = CURRENT.MINFREZ_WAIT  # 等 :374 命中: 拨到边界前 1 分钟, 至多 ~3 分钟到点
NEG_WIN = CURRENT.MINFREZ_NEG_WIN   # ⑤ 否定期望窗口: 比一个整分间隔长, 保证窗口里至少跨过一个整分

# ---- 断点: 模块级**字面量元组** ---------------------------------------------------
# `scripts/_check_anchors.py` 用 `ast.literal_eval` 抠 `BP_<X>`/`VARS_<X>` 这两个名字, 逐条对源码核
#   "要读的变量在断点那一行赋过值没有"; 写成 `CB.SOMETHING` 那种表达式就抠不出来, 等于没人核过。
BP_WRITE = ("prev", "Check_MinuteFrez", "Write_FrezData", 1)   # 写库点 `Write_FrezData(ID_MinuteFrez0+typ, &buff[0])` @TaskFreeze.c:374
# 那一停可读的五个量(位置表逐区间核过):
#   `normal`  = 这一趟的驱动源(自然分钟步进 → TRUE = 0xAA = 170; 校时 → FALSE)
#   `typ`     = 通道号(写的是 ID_MinuteFrez0+typ)
#   `frezNum` = 这一趟补几条
#   `stInfo`  = 该通道的存储信息。⚠ 结构体指针本身可读, 但**成员 `prd` 在 :374 是位置表空洞**
#               (优化版固件 DWARF 里它那一格不在任何区间) ⇒ 读它的成员 `u16Period`, 与 AA80 回读互对
#   `buff[0..5]` = 本次冻结的时标 `[秒(写死 0), 分, 时, 日, 月, 年偏移]`
# ⚠ 必须是**字面量元组**, 且每个名字都要能在 C 源码里搜到(点号表达式抠不出/找不到)。
VARS_WRITE = ("normal", "typ", "frezNum", "stInfo", "buff")

# ---- 半途中止时要一次记全的条目(纯数据; 与正常路径同名同号**同观测**, 少记一条 = 分母变小)----
ENTRIES_ALL = (
    ("①", "停在 TaskFreeze.c:374 那一刻 normal == TRUE", judge.DEBUG),
    ("②", "那一趟写的分钟是通道周期的整数倍", judge.DEBUG),
    ("③", "那一趟写的是 0 号分钟通道且只写 1 条", judge.DEBUG),
    ("④", "698 读回最新一条的时标 == 那一刻 buff 解出的整分", judge.SERIAL),
    ("⑤", "95 秒窗口里 :374 不命中, 且窗口里表钟跨过一个整分", judge.DEBUG),
    ("⑥", "698 读回相邻两条记录的时标相差 prd 分钟", judge.DEBUG),
    ("⑦", "TAB_FrezObj 第 1 行翻出的 OAD == 规范要的 18 项", judge.DEBUG),
    ("⑧", "0 号分钟通道的存储深度 == 35040、间隔 == 15", judge.DEBUG),
    ("⑨", "间隔的判定界卡在 1~60: 写 0 与写 61 都被拒且回读不变", judge.DEBUG),
    ("⑩", "条数上限: 按固件自报的记录长/深度/区首地址算, 记录区装得下 35040 条", judge.DEBUG),
    ("⑪", "698 读回最新一条的 12 个单项电量列 == 写库那一刻 buff[6..] 的同序槽", judge.DEBUG),
    ("⑫", "间隔可设: 通道 0 的对象删空后把间隔设成 1 / 60 都收下", judge.DEBUG),
)

OBJ_ROW = 1                     # 分钟冻结用的对象表行号(TAB_FrezObj 第 1 行)
PRD_WANT = 15                   # 出厂间隔(与 NUM_MinuteFrez/35040 配套; 规范默认 15min)
DEPTH_WANT = 35040              # 出厂深度(UserCfg.h 的 NUM_MinuteFrez)
REFUSE = 3                      # 698 DAR_RefuseOp —— ⑨ 期望的拒绝码(p698.DAR[3])
FREZ_PAGE = CURRENT.FREZ_PAGE_SIZE  # 记录区一页(扇区)的字节数(FH_PageSize = 4096) —— ⑩ 的折页式用
FREZ_END = CURRENT.FREZ_END_ADDR    # 分冻结记录区末扇区(FH_FrezEnd = FH_Capacity − 64 = 4032)
TGT_SEC = 35                    # 校时目标时刻的秒位(= 拨完到自然步进落到边界还剩 25s; 规范要 ≤30s)

# ---- ⑬ 铺满 365 天那一段的参数(只有 --fill-year 模式用) ----
# 一格一轮: 落点取「边界后 59 秒」, 让**紧随的那次自然分钟步进**只剩 1 秒就动手。
# 落点取「边界后 5 秒」的话, 下一次步进还有 55 秒, 实测那一轮要等 56.5s 才落库。
FILL_LEAD = 59                  # 校时落点 = 边界之后多少秒
FILL_LAG = 0.8                  # 落点之后留给固件那次步进动手的余量(随后的读回帧还要 0.5s)
FILL_EVERY = 96                 # 每多少格出一行进度(96 格 = 表上的一天)
FILL_RETRY = 1                  # 某一格没落库时重跳几次(实测真会遇上, 不是预防性代码)
# ⑬ 的账本条目标签: 与 `minfrez_fill_criteria()` 那条逐字一致(账本靠它认领条目)
FILL_LABEL = "⑬ " + cmd_bank.minfrez_fill_criteria()["⑬"]


def _stop_unproven(ctx, why):
    """半途中止的统一收场: 条目一次记全(`ok=None`) → 出声 → 停住(退 2, 不是失败)。"""
    ctx.J.extend(cmd_bank.unproven_records(ENTRIES_ALL, why, cmd_bank.MINFREZ_FALSIFY))
    ctx.J.note("4-2 分钟冻结段: 半途中止(这一次没做成) —— %s" % why)
    raise trial.Stop("%s ⇒ 未定论(退 2), 不是失败" % why)


def part_minute_frez(ctx):
    """一段 = 4-2 的全部条目: 读周期 → 拨钟 → 等写库点 → 698 读回 → 否定期望 → 第二条边界。

    ⚠ 观测一律 attach(**禁用 launch**: launch 会复位表、RAM 态清零); 见 CLAUDE.md 调试链纪律 1。
    ⚠ 拨钟那一刻距目标分钟至少留 `P.MINFREZ_LEAD` 秒 —— 校时帧 + 计量芯 SPI 跟随要时间;
      `CB.frez_boundary` 每回取"≥ 当下 + 3 分钟"的那个边界, 所以这一条自动满足。
    """
    J, ser = ctx.J, ctx.ser
    print("\n===== 4-2 分钟冻结: 拨到边界前等自然分钟步进 → 断点看写库 → 698 读回 =====")

    # ---- ⑦ 冻结对象表第 1 行(FLASH 常量表, 探针按符号地址读; 不停核) ----
    # ⚠ 探针那一次读排在开会话**之前** —— 探针与 gdb 会话抢同一支 J-Link, 用完即关(`with Probe()`)。
    fb = so = None
    if not ctx.waived:
        try:
            with Probe() as pb:
                fb, so = cmd_bank.freobj_read(pb)
        except Exception as exc:            # 探针开不起来 / 读不成 → 这一条记"没做成"
            print("   !! 探针不可用(%s) ⇒ 对象表这一次读不到" % exc)
    rows_ob = cmd_bank.freobj_row(fb, so, OBJ_ROW)
    ok_ob, why_ob = cmd_bank.freobj_check(rows_ob, OBJ_ROW, tag="4-2 分钟冻结行")
    J.add("⑦ TAB_FrezObj 第 1 行翻出的 OAD == 规范要的 18 项",
          ok_ob, ("用户指定只做黑盒 ⇒ 这一条不做" if ctx.waived else why_ob),
          crit="⑦", falsify=cmd_bank.MINFREZ_FALSIFY["⑦"], obs=judge.DEBUG)

    # ---- 前置 · AA80 读通道周期(排边界时刻要用它; 拿不到就排不出, 别拿 15 硬猜) ----
    print("\n[前置] AA80 读各分钟通道的存储信息 ...")
    store = cmd_bank.frez_store_read(
        ser, CURRENT.FREZ_STORE_BLOCK,
        range(CURRENT.MINFREZ_CHANNEL0, CURRENT.MINFREZ_CHANNEL0 + CURRENT.MINFREZ_CHANNELS),
        clamp=CURRENT.FREZ_STORE_CLAMP, tag="minfrez_store")
    ch0 = (store or {}).get(CURRENT.MINFREZ_CHANNEL0) or {}
    prd = ch0.get("prd")

    # ---- ⑧ 0 号通道的存储深度与间隔 == 出厂值(与规范『15min 间隔下不少于 365 天』配套) ----
    ok_dp, why_dp = cmd_bank.frez_store_depth_evidence(
        store, CURRENT.MINFREZ_CHANNEL0, want_prd=PRD_WANT, want_depth=DEPTH_WANT, tag="4-2 分钟冻结")
    J.add("⑧ 0 号分钟通道的存储深度 == %d、间隔 == %d" % (DEPTH_WANT, PRD_WANT), ok_dp, why_dp,
          crit="⑧", falsify=cmd_bank.MINFREZ_FALSIFY["⑧"], obs=judge.DEBUG)

    if not prd:
        _stop_unproven(ctx, "通道 %d 的周期读不到(AA80 无应答或该表项读出来是 0) ⇒ 排不出边界时刻"
                            % CURRENT.MINFREZ_CHANNEL0)
    print("   通道 %d: prd=%d depth=%s size=%s; 其余通道 prd=%s"
          % (CURRENT.MINFREZ_CHANNEL0, prd, ch0.get("depth"), ch0.get("size"),
             [store[i]["prd"] for i in sorted(store) if i != CURRENT.MINFREZ_CHANNEL0]))

    # ---- ⑩ 条数上限: 离线算记录区装不装得下(折页式逐字抄固件 FrezData.c:878 那一支) ----
    # 末扇区 = 区首 + (深度-1)//(页大小//记录长) + 2; 判据 = 末扇区 <= 记录区末扇区(固件同一处闸 :884)。
    # ⚠ 它证的是「固件自报的这份布局排得下 35040 条」, **不是**「35040 条真铺满了」——
    #   铺满要表自然走过 365 天, 本台做不到; 那半件事由 ⑧ 的深度值替着, 不许把这一条读成"已经走过 365 天"。
    ok_cap, why_cap = cmd_bank.frez_store_capacity(
        store, CURRENT.MINFREZ_CHANNEL0, DEPTH_WANT, FREZ_PAGE, FREZ_END, tag="4-2 分钟冻结")
    J.add("⑩ 条数上限: 记录区装得下 %d 条" % DEPTH_WANT, ok_cap, why_cap,
          crit="⑩", falsify=cmd_bank.MINFREZ_FALSIFY["⑩"], obs=judge.DEBUG)

    # ---- 前置 · 进厂内(记录读回受安全判定管)+ 现最新一条(基线) ----
    cmd_bank.enter_factory(ser)
    pre = cmd_bank.read_freeze_row(ser, SUB, 1, wait=WAIT, empty_ok=True)
    print("   基线: 分钟冻结(子类 0x%02X) 最新一条 = %s" % (SUB, cmd_bank.rec_row_txt(pre)))

    # ---- ⑨ 间隔的判定界卡在 1~60: 写 0 / 写 61 都被拒, 且回读没变 ----
    # 判据是「被拒 **且** 回读不变」两半一起: 只判 DAR 的话, 一个把整条 Set 通路都关掉的固件
    # 也满足 —— 那正是这一条要与之区别的另一本账。发在进厂内**之后**, 于是唯一的拒绝理由
    # 就是固件自己的 prd 判定(DLT698App.c:12728 的 `(prd==0)||(prd>60)`), 不是安全判定。
    # 探针对象取本行头一个已登对象, 不新增对象、不改对象表。
    # ⚠ **只能在已配通道上写非法值**: 固件同一行还有 `(stInfo[mod].u16Period != 0) && (!= prd)`
    #   那道坎, 已配通道改成任何别的合法值也一律 RefuseOp ⇒ 这一条判的是**判定界**, 不是可设性。
    #   可设性那半是 ⑫: 它先把通道删空(间隔归零)再设, 造的正是这里造不出的那个态。
    oad_probe = cmd_bank.FREZOBJ_SPEC[OBJ_ROW][0][1]
    tried = []
    for bad_prd in (0, 61):
        _vd, _note, bad_dar = cmd_bank.set_minfrez_obj(
            ser, oad_probe, bad_prd, DEPTH_WANT, mod=0, tag="间隔判定界 写%d" % bad_prd)
        tried.append((bad_prd, bad_dar, _note))
    store_2 = cmd_bank.frez_store_read(
        ser, CURRENT.FREZ_STORE_BLOCK, [CURRENT.MINFREZ_CHANNEL0],
        clamp=CURRENT.FREZ_STORE_CLAMP, tag="间隔判定界_写后")
    prd_2 = ((store_2 or {}).get(CURRENT.MINFREZ_CHANNEL0) or {}).get("prd")
    rejected = all(dar == REFUSE for _p, dar, _n in tried)
    same = (prd is not None and prd_2 is not None and prd == prd_2)
    J.add("⑨ 间隔的判定界卡在 1~60: 写 0 与写 61 都被拒且回读不变",
          None if prd_2 is None else (rejected and same),
          "写 0 → %s; 写 61 → %s; 通道 %d 的间隔 写前 %s → 写后 %s%s"
          % (tried[0][2], tried[1][2], CURRENT.MINFREZ_CHANNEL0, prd, prd_2,
             "" if (rejected and same) else
             (" —— 回读不到间隔" if prd_2 is None else
              (" —— 有一次被收下了" if not rejected else " —— 间隔跟着变了"))),
          crit="⑨", falsify=cmd_bank.MINFREZ_FALSIFY["⑨"], obs=judge.DEBUG)

    # ---- 开调试会话(断点观测)。台面没接 J-Link ⇒ None, 白盒那几条如实记"没做成" ----
    # ⚠ 必须排在拨钟**之前**: 开会话 + `gdbinit.build` 整片停核遍历合计约 28s, 比拨钟留的
    #   25s 余量长。排在后面时(本行原先的位置), 断点挂到核上的时刻已经晚于它要抓的那个
    #   边界分钟 —— 那一趟就是"窗口里一个命中都没有, 记录却照样写进去"(`log/` 里 16:41 那趟)。
    ctx.session()
    if ctx.g is not None:
        # 整片 .out 解一次(停核遍历, 内部喂狗, 收尾放行) —— 之后一律查这张图
        gdbinit.build(ctx.g)
    g = ctx.g
    have_wb = g is not None
    if not have_wb:
        print("\n   !! 本次无调试会话 → 断点观测(白盒)整体**未做**; 本次范围 = 仅黑盒"
              "(黑盒只能答『记录有没有推进、时标对不对』, 答不了『写点判的是不是通道周期』)")

    # ---- 拨钟到第一个边界前 1 分钟(:05 发 ⇒ 55s 后自然步进正好落到边界那一分钟) ----
    now = cmd_bank.read_clock(ser, chip="计量芯", wait=WAIT, quiet=True)
    try:
        now_dt = datetime.datetime.strptime(now or "", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        now_dt = None
    if now_dt is None:
        _stop_unproven(ctx, "表钟读不出来(%r) ⇒ 排不出边界时刻" % now)
    b1 = cmd_bank.frez_boundary(cmd_bank.frez_abs_min(now_dt), prd)
    tgt = cmd_bank.frez_target(now_dt, b1, sec=TGT_SEC)
    print("\n[拨钟] 表钟=%s → 目标=%s(边界分钟数 %d = 绝对分钟数整除 %d; 距边界 %ds)"
          % (now, tgt, b1, prd, 60 - TGT_SEC))
    cmd_bank.set_meter_clock_set(ser, tgt, chip="计量芯", wait=WAIT)

    # ---- 等自然分钟步进停在写库点(①②③ 三个量的现场) ----
    normal = typ = frez_num = prd_bp = None
    buff = []
    if have_wb:
        print("\n[等] 自然分钟步进走到写库点(%s, 至多 %.0fs) ..."
              % (breakpoint.text(BP_WRITE), HIT_TIMEOUT))
        r1 = breakpoint.wait_hit(g, BP_WRITE, HIT_TIMEOUT, vars=VARS_WRITE,
                         label="4-2 写库点(%s)" % breakpoint.text(BP_WRITE),
                         falsify="固件没在通道周期的整数倍分钟上调写库(或任务调度变了)"
                                 " ⇒ 至多 %.0fs 内停不到 %s" % (HIT_TIMEOUT, breakpoint.text(BP_WRITE)))
        v = (r1 or {}).get("vars") or {}
        normal = cmd_bank.st_int(v.get("normal"))
        typ = cmd_bank.st_int(v.get("typ"))
        frez_num = cmd_bank.st_int(v.get("frezNum"))
        prd_bp = cmd_bank.st_field(v.get("stInfo"), "u16Period")
        buff = cmd_bank.gdb_bytes(v.get("buff"))
        print("   现场: %s | normal=%s typ=%s frezNum=%s stInfo.u16Period=%s buff=%s"
              % ((r1 or {}).get("detail") or "没停到", normal, typ, frez_num, prd_bp,
                 " ".join("%02X" % x for x in buff[:6]) if buff else "读不到"))
    else:
        print("\n[等] 无调试会话 ⇒ 写库点这一半不做(不假装等过)")

    # ---- ① 驱动源: 自然分钟步进 ⇒ normal == TRUE(0xAA = 170) ----
    # ⚠ `int` 不是 `bool`: 0 也是"读到了值", 拿 `if normal:` 判会把"读到 0"当成"没读到"。
    J.add("① 停在 :374 那一刻 normal == TRUE", None if normal is None else (normal == 170),
          ("没有调试会话 ⇒ 这一半不做" if not have_wb else
           "normal=%s(%s)%s" % (normal, {170: "TRUE", 85: "FALSE"}.get(normal, "?"),
                                "" if normal == 170 else
                                (" —— 没读回 normal" if normal is None else
                                 " —— 停到的那一趟不是自然分钟步进"))),
          crit="①", falsify=cmd_bank.MINFREZ_FALSIFY["①"], obs=judge.DEBUG)

    # ---- ② 写库那一分钟是通道周期的整数倍 ----
    min_byte = buff[1] if len(buff) > 1 else None
    J.add("② 写的分钟是通道周期 prd 的整数倍",
          None if (min_byte is None or prd_bp is None) else (min_byte % prd_bp == 0),
          ("没有调试会话 ⇒ 这一半不做" if not have_wb else
           "buff[1]=%s(分钟, 二进制) stInfo.u16Period=%s(AA80 读出的 prd=%s)%s"
           % (min_byte, prd_bp, prd,
              "" if (min_byte is None or prd_bp is None or min_byte % prd_bp == 0)
              else " —— 这一分钟不是 prd 的整数倍")),
          crit="②", falsify=cmd_bank.MINFREZ_FALSIFY["②"], obs=judge.DEBUG)

    # ---- ③ 写的是 0 号通道且只写 1 条 ----
    J.add("③ 写 0 号分钟通道且只写 1 条(typ == 0 且 frezNum == 1)",
          None if (typ is None or frez_num is None) else (typ == 0 and frez_num == 1),
          ("没有调试会话 ⇒ 这一半不做" if not have_wb else
           "typ=%s frezNum=%s(表项 = ID_MinuteFrez0+typ = %s)"
           % (typ, frez_num, None if typ is None else CURRENT.MINFREZ_CHANNEL0 + typ)),
          crit="③", falsify=cmd_bank.MINFREZ_FALSIFY["③"], obs=judge.DEBUG)

    # ---- ④ 698 读回: 最新一条的时标 == 那一刻 buff 解出的整分 ----
    if SETTLE:
        time.sleep(SETTLE)          # 写库那一刻停在 :374(Write_FrezData 的**调用行**) ⇒ 等它落完
    post = cmd_bank.read_freeze_row(ser, SUB, 1, wait=WAIT, empty_ok=True)
    want_ts = cmd_bank.frez_expect_ts(buff)
    got_ts = (post or {}).get("ts")
    J.add("④ 698 读回最新一条的时标 == 写库那一刻 buff 的时标",
          None if (want_ts is None or got_ts is None) else (got_ts == want_ts),
          ("写库那一刻 buff 读不到 ⇒ 没有对照物, 这一条没做成" if want_ts is None else
           "记录 %s; 写库那一刻 buff[:6] 解出的时标 = %s%s"
           % (cmd_bank.rec_row_txt(post), want_ts,
              "" if got_ts == want_ts else " —— 读回的是 %s" % (got_ts or "读不到"))),
          crit="④", falsify=cmd_bank.MINFREZ_FALSIFY["④"])

    # ---- ⑪ 记录里的单项电量列 == 写库那一刻 buff[6..] ----
    # 与 4-1③c 同一件事、同一套比法(那是"整列 5 项/47B", 这里是"单项 1 项/11B")。两侧形态与折算
    # 见 `cmd_bank.minfrez_energy_match` 的 docstring; 折算登记在画像 `KWH_OAD_698CONV`。
    ok11, why11 = cmd_bank.minfrez_energy_match(ser, buff, pos=1, wait=WAIT, tag="4-2 分钟冻结")
    J.add("⑪ 698 读回最新一条记录里的 12 个单项电能量列 == 写库那一刻 buff[6..] 的同序槽",
          (None if not have_wb else ok11),
          ("没有调试会话 ⇒ 写库那一刻的 buff 读不到, 这一条没做成" if not have_wb else why11),
          crit="⑪", falsify=cmd_bank.MINFREZ_FALSIFY["⑪"], obs=judge.DEBUG)

    # ---- ⑤ 非边界整分的窗口里不落库(否定期望) ----
    if have_wb:
        c0 = cmd_bank.read_clock(ser, chip="计量芯", wait=WAIT, quiet=True)
        print("\n[等] 否定期望: %.0fs 窗口里 :374 不该命中(窗口比一个整分间隔长) ..." % NEG_WIN)
        r5 = breakpoint.expect_no_hit(g, BP_WRITE, NEG_WIN, vars=VARS_WRITE,
                              label="4-2 非边界整分不落库(否定期望)",
                              falsify=cmd_bank.MINFREZ_FALSIFY["⑤"])
        c1 = cmd_bank.read_clock(ser, chip="计量芯", wait=WAIT, quiet=True)
        crossed = cmd_bank.frez_crossed(c0, c1)
        # 窗口里没跨过整分 ⇒ 这次否定期望**空转**(什么都没看), 记"没做成" —— 不许记达成。
        J.add("⑤ %.0fs 窗口里 :374 不命中, 且窗口里表钟跨过一个整分" % NEG_WIN,
              None if (r5 is None or crossed is None) else (r5.get("ok") is True and crossed is True),
              "窗口内表钟 %s → %s(%s); %s"
              % (c0, c1, "跨过整分" if crossed else "**没跨过整分, 这次否定期望空转**",
                 (r5 or {}).get("detail") or "没做成"),
              crit="⑤", falsify=cmd_bank.MINFREZ_FALSIFY["⑤"], obs=judge.DEBUG)
    else:
        J.add("⑤ %.0fs 窗口里 :374 不命中, 且窗口里表钟跨过一个整分" % NEG_WIN, None,
              "没有调试会话 ⇒ 这一半不做(黑盒没有『没落库』的替身: 读不到记录与没落库分不开)",
              crit="⑤", falsify=cmd_bank.MINFREZ_FALSIFY["⑤"], obs=judge.DEBUG)

    # ---- ⑥ 相邻两条记录的时标相差 prd ----
    # 第二条边界是**拨钟推进 prd 分钟**造出来的(不真等 15 分钟): 它证"写点落在整数倍 prd 的分钟上、
    # 时标就是那个边界", **不证实时周期** —— 实时周期那半支由 ⑤ 的否定期望窗口担着。
    if have_wb:
        now2 = cmd_bank.read_clock(ser, chip="计量芯", wait=WAIT, quiet=True)
        try:
            d2 = datetime.datetime.strptime(now2 or "", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            d2 = None
        if d2 is None:
            J.add("⑥ 相邻两条读回记录的时标相差 prd 分钟, 且序号接着上一号", None,
                  "表钟读不出来(%r) ⇒ 第二条边界排不出" % now2,
                  crit="⑥", falsify=cmd_bank.MINFREZ_FALSIFY["⑥"], obs=judge.DEBUG)
        else:
            b2 = b1 + prd
            tgt2 = cmd_bank.frez_target(d2, b2, sec=TGT_SEC)
            print("\n[拨钟] 第二条边界: 表钟=%s → 目标=%s(边界分钟数 %d = 上一条 + prd; 距边界 %ds)"
                  % (now2, tgt2, b2, 60 - TGT_SEC))
            cmd_bank.set_meter_clock_set(ser, tgt2, chip="计量芯", wait=WAIT)
            r6 = breakpoint.wait_hit(g, BP_WRITE, HIT_TIMEOUT, vars=VARS_WRITE,
                             label="4-2 第二个写库点(%s)" % breakpoint.text(BP_WRITE),
                             falsify="拨到第二条边界前 1 分钟后没被自然分钟步进调到写库"
                                     " ⇒ 至多 %.0fs 内停不到 %s" % (HIT_TIMEOUT, breakpoint.text(BP_WRITE)))
            if SETTLE:
                time.sleep(SETTLE)
            p1 = cmd_bank.read_freeze_row(ser, SUB, 1, wait=WAIT, empty_ok=True)
            p2 = cmd_bank.read_freeze_row(ser, SUB, 2, wait=WAIT, empty_ok=True)
            gap = cmd_bank.frez_ts_gap(p1, p2)
            s1, s2 = (p1 or {}).get("seq"), (p2 or {}).get("seq")
            # 相邻 = 时标相差 prd **且** 序号接着上一号(pos1 是最新那条, 序号比 pos2 大 1)。
            # ⚠ 判"读不到"一律 `is None` —— 序号 0 是合法值(第 1 条冻结记录就是这个号)。
            ok6 = (None if (gap is None or s1 is None or s2 is None)
                   else (gap == prd and s1 == s2 + 1))
            J.add("⑥ 相邻两条读回记录的时标相差 prd 分钟, 且序号接着上一号", ok6,
                  "pos1 %s / pos2 %s ⇒ 时标相差 %s 分钟(prd=%d)、序号差 %s; %s"
                  % (cmd_bank.rec_row_txt(p1), cmd_bank.rec_row_txt(p2),
                     gap if gap is not None else "比不出来", prd,
                     "比不出来" if (s1 is None or s2 is None) else (s1 - s2),
                     (r6 or {}).get("detail") or "第二个写库点没停到"),
                  crit="⑥", falsify=cmd_bank.MINFREZ_FALSIFY["⑥"], obs=judge.DEBUG)
    else:
        J.add("⑥ 相邻两条读回记录的时标相差 prd 分钟, 且序号接着上一号", None,
              "没有调试会话 ⇒ 第二条边界造不出来(拨不到既定的那个边界分钟)",
              crit="⑥", falsify=cmd_bank.MINFREZ_FALSIFY["⑥"], obs=judge.DEBUG)

    # ---- ⑫ 间隔可设: 把通道 0 删空(间隔随之归零)后, 设成 1 与设成 60 都被收下 ----
    # 出厂通道 0 已配 18 个对象(间隔 15), 而固件对**已配**通道的改值一律 RefuseOp(:12729 那道坎) ⇒
    # "间隔被改成功"这个态, 帧通道只能这么造: 先把该通道的对象删到一个不剩(固件遂把该通道的存储信息
    # 整段清零, :12764-12779, 间隔随之归零), 再按新间隔重新设。
    # 删走 **Action 的单项删减**(`50020500`), 不走 Set 的批量设置(`FREZOBJ_SET_OAD`): 后者会把 8 条
    # 通道的对象表一起推平(DLT698App.c:11201 落 `act=1`), 而那些对象表存在 EEPROM, AA80 三个区
    # 够不到 ⇒ 推平了本脚本还原不回来。
    # ⚠ 本段**真改表配置**, 且每成功一条都会连带清掉分钟冻结记录区(`Clear_FrezData`, :12805) ——
    #   所以它排在最后(④⑥ 用的记录读完了), 且收尾必须按出厂配置重建通道 0 并回读证实。
    oad0 = cmd_bank.MINFREZ_ROW_OADS[0]

    def _delete_then_set(want, oads):
        """⑫ 的一步: 删掉 `oads` → 把间隔设成 `want` → `(ok, detail)`。"""
        c_ok, c_why = cmd_bank.minfrez_clear_channel(ser, oads, tag="4-2 ⑫ 设 %d 前删空通道 0" % want)
        if c_ok is not True:
            return None, "设 %d 之前没能让通道 0 的间隔归零(%s) ⇒ 这一步没做成" % (want, c_why)
        return cmd_bank.minfrez_set_period(ser, oad0, want, DEPTH_WANT, tag="4-2 ⑫ 设间隔 %d" % want)

    print("\n[⑫] 间隔可设: 先删空通道 0 的 %d 个关联对象, 再把间隔分别设成 %d 与 %d ..."
          % (len(cmd_bank.MINFREZ_ROW_OADS), cmd_bank.FREZOBJ_SET_MIN_PRD, cmd_bank.FREZOBJ_SET_MAX_PRD))
    # 第一步删全部 18 个(把出厂那套清掉); 第二步只删第一步加回来的那一个。
    r_lo, w_lo = _delete_then_set(cmd_bank.FREZOBJ_SET_MIN_PRD, cmd_bank.MINFREZ_ROW_OADS)
    r_hi, w_hi = _delete_then_set(cmd_bank.FREZOBJ_SET_MAX_PRD, (oad0,))
    J.add("⑫ 间隔可设: 通道 0 的对象删空后把间隔设成 1 / 60 都收下",
          (True if (r_lo is True and r_hi is True)
           else (False if (r_lo is False or r_hi is False) else None)),
          "设 %d: %s | 设 %d: %s"
          % (cmd_bank.FREZOBJ_SET_MIN_PRD, w_lo, cmd_bank.FREZOBJ_SET_MAX_PRD, w_hi),
          crit="⑫", falsify=cmd_bank.MINFREZ_FALSIFY["⑫"], obs=judge.DEBUG)

    # 收尾: 不管上面成败, 都按出厂配置把通道 0 重建回来并回读证实
    print("\n[⑫] 收尾: 按出厂配置重建通道 0(间隔 %d / 深度 %d / %d 个对象) ..."
          % (PRD_WANT, DEPTH_WANT, len(cmd_bank.MINFREZ_ROW_OADS)))
    rb_ok, rb_why = cmd_bank.minfrez_restore_channel(ser, PRD_WANT, DEPTH_WANT, tag="4-2 ⑫ 重建通道 0")
    print("   重建结果: %s —— %s"
          % ({True: "已还原", False: "!! 没还原", None: "!! 没做成"}[rb_ok], rb_why))
    if rb_ok is not True:
        print("   !! 通道 0 现在不是出厂配置 —— 收尾的 `_restore_all.py` 够不到关联对象表(住 EEPROM),"
              " 只能人工重配")


def part_fill_year(ctx):
    """⑬ 铺满 365 天: 一格一轮把 0 号分钟通道填到 35040 条。

    ⚠ 这一条是**真写**, 不是算 —— ⑧ 是固件自报的深度值、⑩ 是照那个深度离线算排不排得下,
      两条都不写一条记录; 这一段真写 35040 条, 写完读第 35040 条(最旧)还在不在。
    ⚠ 表钟一路前推 365 天, 中途不拨回; 跑完走 `scripts/_restore_all.py`(它会拨回真实时间)。
    ⚠ 默认**不跑**: 一格约 2 秒(校时帧 0.63s + 落点后等步进 + 读回), 35040 格要连跑十几小时。
      要跑必须在命令行显式给 `--fill-year`; 不给时这一段一行不发, ⑬ 也不进本次分母。
    """
    J, ser = ctx.J, ctx.ser
    print("\n===== 4-2 铺满 365 天: 逐格前推表钟, 把 0 号分冻结通道填到 %d 条 =====" % DEPTH_WANT)

    if "--fill-year" not in sys.argv:
        print("   没给 --fill-year ⇒ 这一段不跑(它要连跑十几小时); ⑬ 不进本次分母")
        return

    def _fill_unproven(why):
        """这一段没做成的统一收场 —— 记 `ok=None`(不是通过, 也不是失败)。"""
        J.add(FILL_LABEL, None, why, crit="⑬",
              falsify=cmd_bank.MINFREZ_FALSIFY["⑬"], obs=judge.SERIAL)

    store = cmd_bank.frez_store_read(
        ser, CURRENT.FREZ_STORE_BLOCK, [CURRENT.MINFREZ_CHANNEL0],
        clamp=CURRENT.FREZ_STORE_CLAMP, tag="铺满段_存储信息")
    ch0 = (store or {}).get(CURRENT.MINFREZ_CHANNEL0) or {}
    prd, depth = ch0.get("prd"), ch0.get("depth")
    if not prd or not depth:
        _fill_unproven("0 号通道的间隔/深度读不到(prd=%s depth=%s) ⇒ 排不出格子" % (prd, depth))
        return

    # 起点。记录区空着(上一段 ⑫ 删空通道时把记录区一起清了) ⇒ 第 1 格 = 表钟之后的下一个边界;
    # 记录区已有内容(断线重来) ⇒ 从「最新一条的时标 + prd」接着填 —— 重填一格是空转一轮。
    cur = cmd_bank.read_freeze_row(ser, SUB, 1, wait=WAIT, empty_ok=True)
    last_ts = (cur or {}).get("ts")
    if last_ts:
        base_dt = (datetime.datetime.strptime(last_ts, "%Y-%m-%d %H:%M:%S")
                   + datetime.timedelta(minutes=prd))
        print("   记录区非空: 接着最新一条 %s 的下一格填" % last_ts)
    else:
        now = cmd_bank.read_clock(ser, chip="计量芯", wait=WAIT, quiet=True)
        try:
            base_dt = datetime.datetime.strptime(now or "", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            base_dt = None
        if base_dt is None:
            _fill_unproven("表钟读不出来(%r) ⇒ 排不出第一格" % now)
            return
        print("   记录区空着: 从表钟 %s 之后的下一个边界起填" % now)

    t0 = time.time()
    last_bdt = None
    for n in range(1, depth + 1):
        bdt, tgt = cmd_bank.frez_step_target(base_dt, prd, lead=FILL_LEAD)
        want = bdt.strftime("%Y-%m-%d %H:%M:%S")
        got = None
        for k in range(FILL_RETRY + 1):
            cmd_bank.set_meter_clock_set(ser, tgt, chip="计量芯", wait=WAIT)
            time.sleep(FILL_LAG)
            got = (cmd_bank.read_freeze_row(ser, SUB, 1, wait=WAIT, empty_ok=True) or {}).get("ts")
            if got == want:
                break
            print("   !! 第 %d 格(%s)这一跳没落库(读回 %s) —— 第 %d 次重跳"
                  % (n, want, got or "读不到", k + 1))
        if got != want:
            faultlog.record(subsystem="serial",
                            text="4-2 铺满段第 %d 格 %s 重跳 %d 次仍没落库(读回 %s)"
                                 % (n, want, FILL_RETRY + 1, got),
                            tried="重跳同一个落点 %d 次" % (FILL_RETRY + 1),
                            next_step="重跑本段会从最新一条接着填; 若最新一条也读不到, 查厂内态是否过期")
            _fill_unproven("第 %d 格(%s)重跳 %d 次都没落库 ⇒ 填到第 %d 格断了"
                           % (n, want, FILL_RETRY + 1, n - 1))
            return
        base_dt = bdt + datetime.timedelta(seconds=FILL_LEAD)
        last_bdt = bdt
        if n % FILL_EVERY == 0 or n == depth:
            el = time.time() - t0
            print("   [%d/%d] 这一格 %s | 已跑 %.1f 分钟, 均 %.2f 秒/格"
                  % (n, depth, bdt, el / 60.0, el / n))

    # 填满了: 读第 1 条与第 `depth` 条(最旧), 看 35040 格是不是一条不落地都还在。
    print("\n[核] 读第 1 条与第 %d 条(最旧), 看这 %d 格是不是一条不落地都在 ..." % (depth, depth))
    p1 = cmd_bank.read_freeze_row(ser, SUB, 1, wait=WAIT, empty_ok=True)
    pN = cmd_bank.read_freeze_row(ser, SUB, depth, wait=WAIT, empty_ok=True)
    gap = cmd_bank.frez_ts_gap(p1, pN)
    s1, sN = (p1 or {}).get("seq"), (pN or {}).get("seq")
    want_ts = last_bdt.strftime("%Y-%m-%d %H:%M:%S")
    got_ts = (p1 or {}).get("ts")
    want_gap, want_seq = prd * (depth - 1), depth - 1
    if gap is None or s1 is None or sN is None:
        _fill_unproven("最旧那条读不到(pos=%d)或比不出来: 最新 %s / 最旧 %s"
                       % (depth, cmd_bank.rec_row_txt(p1), cmd_bank.rec_row_txt(pN)))
        return
    ok13 = (got_ts == want_ts and gap == want_gap and (s1 - sN) == want_seq)
    J.add(FILL_LABEL, ok13,
          "最新第 1 条 %s(最后填的那格是 %s); 最旧第 %d 条 %s ⇒ 时标相差 %s 分钟(要 %d)、"
          "序号相差 %s(要 %d)%s"
          % (cmd_bank.rec_row_txt(p1), want_ts, depth, cmd_bank.rec_row_txt(pN),
             gap, want_gap, s1 - sN, want_seq,
             "" if ok13 else " —— 最旧那条被提前挤掉了(记录区装不下 %d 条), 或最新那条不是最后填的那一格"
                             % depth),
          crit="⑬", falsify=cmd_bank.MINFREZ_FALSIFY["⑬"], obs=judge.SERIAL)
    print("\n   !! 表钟现在停在 %s(365 天后) —— 收尾必须走 `python scripts/_restore_all.py` 拨回真实时间"
          % want_ts)


def _banner():
    return ("== 4-2 分钟冻结 | 工程=%s 表号=%s ==\n"
            ".. 驱动=拨钟到「边界分钟 − 1」的 :05 后等**自然分钟步进**(校时那趟自己不落库); "
            "白盒停 %s 看 %s; 记录读回走 698 GetRequestRecord 子类 0x%02X; "
            "⚠ 跑完表钟停在第二次目标时刻, 收尾走 scripts/_restore_all.py; "
            "⚠ 末段 ⑫ 用 Action 50020500/50020400 改写通道 0 的关联对象表(删空→设 1→设 60), "
            "跑完按出厂配置重建(间隔 %d / 深度 %d)—— 重建失败本脚本会出声; "
            "⚠ 再末一段 ⑬(真铺满 %d 条)只认 --fill-year, 跑它要连跑十几小时且表钟一路前推 365 天"
            % (CURRENT.PROJECT, CURRENT.TABLE_ADDR.hex().upper(),
               breakpoint.text(BP_WRITE), "/".join(VARS_WRITE), SUB, PRD_WANT, DEPTH_WANT, DEPTH_WANT))


def _criteria():
    """本次跑次的条目表。⚠ ⑬(真铺满 35040 条)只在 `--fill-year` 模式下进分母 —— 默认跑次
    的分母是 12、它不在里面, 报「满足 n/N」时必须说清是哪一种跑次(CLAUDE.md 第 28 条)。"""
    c = cmd_bank.minute_frez_criteria()
    if "--fill-year" in sys.argv:
        c.update(cmd_bank.minfrez_fill_criteria())
    return c


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "4-2 分钟冻结(通道周期边界写库/0 号通道每条 1 条/时标与 698 读回一致/非边界整分不落库)",
        _criteria,
        name="4_2_minute_frez",
        parts=[("4-2 分钟冻结段", part_minute_frez),
               ("4-2 铺满 365 天段", part_fill_year)],
        allow=("--no-gdb", "--observe", "--fill-year"),
        gdb=breakpoint, banner=_banner(),
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV)))
