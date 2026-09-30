# -*- coding: utf-8 -*-
"""在证什么: 小时冻结只在真实整点落一条、时标就是该整点、非整点那些分钟不落库, 跨月也不丢。
会向表写什么: 698 Set 40000200 拨钟(不写任何参数) —— 前三次是整点 / 第二个整点 / 月末, ⑩ 环回再逐条
  补满 254 条(每轮把表钟往前推一个整点)。跑完表钟停在 ⑦ 那个月末整点之后又推进了约 254 小时,
  收尾走 `python scripts/_restore_all.py` —— 本脚本不发时钟收集尾。
跑法: `python project/tests/_test_4_3_hour_frez.py`(真串口 + 真探针; `--no-gdb` 只做黑盒)。
结论怎么读: 账本末行「判据: 满足 n/N」, N=10; 退 0 = 通过 / 1 = 失败 / 2 = 未定论。
"""
import datetime
import time

from common import judge            # 观测种类常量(断点那几条证据标 DEBUG)
from common import trial            # 运行外壳: argv/日志/账本/串口/会话/收尾三连/退出码
from meterlib import cmd_bank       # 帧目录 + 语义动词积木(每步自带打印/判 PASS·TBD·FAIL)
from project import CURRENT          # 本工程画像: 表号/.out 路径/喂狗点(库不认表, 只有脚本认)
from swdbg import breakpoint            # 断点级白盒(停核 + 读函数内局部量); 没接 J-Link → None
from swdbg import gdbinit            # 整片 .out 地图: 开会话后解一次, 之后断点/注入点只查它
from swdbg.probe import Probe        # SWD 直读(读 FLASH 常量表; 不停核)

# ---- 子项参数(纯数据; 要改的在这里改) ----
SUB = CURRENT.HOURFREZ_SUBCLASS     # 小时冻结记录子类(记录 OAD = 50 03 02 00)
IDX = CURRENT.HOURFREZ_INDEX        # `s_stFrzStorageInfo` 里小时冻结那一项的序号(ID_HourFrez)
WAIT = 3.0                    # 记录读回 / 拨钟确认的单次等待
SETTLE = 3.0                  # 停在写库点之后等它落完再读回
HIT_TIMEOUT = CURRENT.HOURFREZ_WAIT         # 等断[A] 命中: 拨到整点前 1 分钟, 至多 ~3 分钟到点
WRITE_TIMEOUT = CURRENT.HOURFREZ_WAIT / 3.0 # 断[A] 命中后到断[B] 只差几十毫秒; 这条是"断[B] 确实没来"的等待
NEG_WIN = CURRENT.HOURFREZ_NEG_WIN          # ⑤ 否定期望窗口: 比一个整分间隔长, 保证窗口里至少跨过一个整分

# ---- ⑩ 环回参数(纯数据) ----
RING_FILL = CURRENT.HOURFREZ_DEPTH  # ⑩ 补满整一圈的条数 = 记录区深度 254 ⇒ 恰好把 254 格写满一轮
RING_LEAD = 50               # ⑩ 每轮拨到「整点 − 1 分钟」的 :RING_LEAD 秒处 ⇒ 真实 10s 后自然跨过整点
RING_TRY = 30.0              # ⑩ 睡到点之后等那一条落库(轮询 pos1 看序号前进)的窗口

# ---- 断点: 模块级**字面量元组** ----
# `scripts/_check_anchors.py` 用 `ast.literal_eval` 抠 `BP_<X>`/`VARS_<X>` 这两个名字逐条对源码
# (变量在断点那一行赋过值没有); 写成 `CB.SOMETHING` 那种表达式就抠不出来, 等于没人核过。
BP_GATE = ("prev", "Check_HourFrez", "Prep_ObjData", 1)     # 判定已过、这一趟真要写 —— `Prep_ObjData(...)` 调用行 @TaskFreeze.c:425
BP_WRITE = ("prev", "Check_HourFrez", "Write_FrezData", 1)  # 写库点 `Write_FrezData(ID_HourFrez, &buff[0])` @TaskFreeze.c:436
# 那一停可读的量(位置表逐区间核过):
#   断[A]: `normal` = 这一趟的驱动源(自然分钟步进 → TRUE = 0xAA = 170; 校时那趟在 :399 就 return 了)
#          `frezNum` = 这一趟补几条; `hours[]` = 补哪几个整点; `stInfo` = 该通道的存储信息
#   断[B]: `frezNum` / `stInfo` / `buff[0..5]` = 本次冻结的时标 [秒(写死 0), 分(写死 0), 时, 日, 月, 年偏移]
# ⚠ 必须是**字面量元组**, 且每个名字都要能在 C 源码里搜到(点号表达式抠不出/找不到)。
VARS_GATE = ("normal", "frezNum", "hours", "stInfo")
VARS_WRITE = ("frezNum", "stInfo", "buff")

# ---- 半途中止时要一次记全的条目(纯数据; 与正常路径同名同号**同观测**, 少记一条 = 分母变小)----
ENTRIES_ALL = (
    ("①", "停在 TaskFreeze.c:425 那一刻 normal == TRUE", judge.DEBUG),
    ("②", "那一趟只写 1 条(frezNum == 1)且写的是小时冻结(ID_HourFrez)", judge.DEBUG),
    ("③", "写库那一刻 buff 的秒/分为 0、时是 prd 的整数倍, 且日月年就是拨钟算出的那个整点", judge.DEBUG),
    ("④", "698 GetRequestRecord 子类 0x03 最新一条的冻结时标 == 那一刻 buff[1..5] 解出的整点", judge.SERIAL),
    ("⑤", "95 秒窗口里 TaskFreeze.c:436 不命中, 且窗口里表钟跨过一个整分", judge.DEBUG),
    ("⑥", "698 读回相邻两条记录的时标相差 stInfo.u16Period 小时且序号接着上一号", judge.DEBUG),
    ("⑦", "拨到月末最后一个整点前, 表钟自然跨过月界后 :436 仍命中, 且那一条的时标 == 次月 1 日 00:00:00", judge.DEBUG),
    ("⑧", "TAB_FrezObj 第 2 行翻出的 OAD == 规范要的那 2 项(正向有功总电能、反向有功总电能)", judge.DEBUG),
    ("⑨", "小时冻结的存储深度 == 254", judge.DEBUG),
    ("⑩", "记录区装满 254 格后环回顶掉最早那条(不是停止写): 拨钟逐条补满 254 条之后最新一条的序号恰好前进 254, 最旧那一条的序号是补之前最新那条的下一个, 且第 255 格读不到", judge.SERIAL),
)


OBJ_ROW = 2                     # 小时冻结用的对象表行号(TAB_FrezObj 第 2 行)


def _stop_unproven(ctx, why):
    """半途中止的统一收场: 条目一次记全(`ok=None`) → 出声 → 停住(退 2, 不是失败)。"""
    ctx.J.extend(cmd_bank.unproven_records(ENTRIES_ALL, why, cmd_bank.HOURFREZ_FALSIFY))
    ctx.J.note("4-3 小时冻结段: 半途中止(这一次没做成) —— %s" % why)
    raise trial.Stop("%s ⇒ 未定论(退 2), 不是失败" % why)


def _arm_both(ctx):
    """挂断[A]/断[B] 并放行 → `(bp_g, bp_w)`; 没会话 ⇒ `(None, None)`。

    ⚠ **两个都挂好才放行**: 断[A] 停完一放行, 核几十微秒就到 断[B] —— 那时再挂已经晚了, 它会掠过一次
      写库, 而现象是"断[B] 从没命中过"(与断点压根没挂上一模一样)。
    ⚠ `ctx.bp` 下不上断点会抛 `GdbError`, 不返回 None; 这两个断点只有"整点那一趟"才走到, 不是高频行。
    """
    bp_g = ctx.bp(BP_GATE)
    bp_w = ctx.bp(BP_WRITE)
    if bp_g is not None:
        ctx.g.ensure_running()          # 少了它: 核停在下断点那一步, 后面每一帧都没有应答
    return bp_g, bp_w


def _wait_both(ctx, bp_g, bp_w, label):
    """等这两个断点各停一次(先断[A] 后断[B]) → `(r_gate, r_write)`。

    ⚠ 断[B] 的停产记录常在读断[A] 那几百毫秒里就到队列了 —— `wait_only` 认"队列里躺着同一个断点号"
      就不放行, 所以这里能收到; 顺序反过来(先断[B])会把它当陈旧记录丢掉。
    ⚠ 两个断点**哪一个超时**都 `drop` 不生效(它只在命中支撤) ⇒ 各自补撤, 否则核跑着撞上自己会被撂停。
    """
    r_g = breakpoint.wait_hit(ctx.g, bp_g, HIT_TIMEOUT, vars=VARS_GATE,
                      label="%s 断[A] 要写了(%s)" % (label, breakpoint.text(BP_GATE)),
                      falsify="固件没在整点前那一分钟走到 %s, 或那条路被优化掉 "
                              "⇒ 至多 %.0fs 内停不到 %s"
                              % (breakpoint.text(BP_GATE), HIT_TIMEOUT, breakpoint.text(BP_GATE)))
    r_w = breakpoint.wait_hit(ctx.g, bp_w, WRITE_TIMEOUT, vars=VARS_WRITE,
                      label="%s 断[B] 写库(%s)" % (label, breakpoint.text(BP_WRITE)),
                      falsify="到了整点却没走到 %s(写库判定失效) ⇒ 至多 %.0fs 内停不到 %s"
                              % (breakpoint.text(BP_WRITE), WRITE_TIMEOUT, breakpoint.text(BP_WRITE)))
    if (r_g or {}).get("ok") is not True:
        ctx.g._disarm(bp_g)
    if (r_w or {}).get("ok") is not True:
        ctx.g._disarm(bp_w)
    return r_g, r_w


def _hit_any(r_g, r_w):
    """两个断点里**有没有一个确实命中**(三态只认 `ok is True` —— 库造的记录没有 `hit` 这个键)。"""
    return (r_w or {}).get("ok") is True or (r_g or {}).get("ok") is True


def _seq_next(newer, older):
    """记录序号是不是**接着上一号**(`newer == older + 1`) → True / False; 任一个读不到 → None。

    ⚠ 三态: 读不到时交回 None(没做成) —— 不许拿"读不到"当"不接着"(那是把没读到判成固件不连续)。
    """
    if newer is None or older is None:
        return None
    return newer == older + 1


def _fill_one(ser, prd, i, total):
    """⑩ 环回的一轮: 拨钟造一个整点 → 等它自然落一条 → `(记录行|None, why)`。

    ⚠ 拨钟走**现成的** 698 拨钟动词、拨到**计量芯**(管理芯那一路写被固件注释禁用, 见 `set_meter_clock_set`);
      目标 = 「下一个整点 − 1 分钟」的 :RING_LEAD 秒处 —— 表钟按真实速率走, 真实时间
      (60 − RING_LEAD) 秒后自然步进落到那个整点, 于是落**恰好一条**。
    ⚠ 拨完必须**回读管理芯表钟**确认它跟到了那一分: 计量芯收下 Set 不等于管理芯跟着走, 而冻结是管理芯
      按自己那份表钟落的 —— 没跟到就白等一轮(这一轮记"没做成", 不当成"没补上")。
    返回值: 与 `read_freeze_row` 同形的记录行 —— 调用处拿它的 `seq` 认"这一条补上了没有"(这一轮里已经
      拿拨钟前那一眼比过); 没做成时交回 `None`, `why` 写清断在哪一步(拨钟被拒 / 管理芯没跟到 / 读不到)。
    """
    base = cmd_bank.read_freeze_row(ser, SUB, 1, wait=WAIT, empty_ok=True)
    if (base or {}).get("answered") is False:
        return None, "第 %d/%d 轮: 拨钟前那一眼没读到(无应答)" % (i, total)
    b0 = (base or {}).get("seq")
    now = cmd_bank.read_clock(ser, wait=WAIT, quiet=True)
    try:
        dt = datetime.datetime.strptime(now or "", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        dt = None
    if dt is None:
        return None, "第 %d/%d 轮: 表钟读不出来(%r) ⇒ 这个整点排不出" % (i, total, now)
    b = cmd_bank.frez_boundary(cmd_bank.frez_abs_min(dt), prd * CURRENT.HOURFREZ_UNIT_MIN)
    tgt_i = cmd_bank.frez_target(dt, b, sec=RING_LEAD)
    v, note, _dar = cmd_bank.set_meter_clock_set(ser, tgt_i, chip="计量芯", wait=WAIT)
    if v != "PASS":
        return None, "第 %d/%d 轮: 拨到 %s 被拒(%s)" % (i, total, tgt_i, note)
    back = cmd_bank.read_clock(ser, wait=WAIT, quiet=True)
    if (back or "")[:16] != tgt_i[:16]:      # 分位一致 = 管理芯跟到了那一分(秒位由它自己走)
        return None, ("第 %d/%d 轮: 拨到 %s 后管理芯表钟读到 %r ⇒ 没跟到那一分"
                      % (i, total, tgt_i, back))
    time.sleep(60 - RING_LEAD + 2)       # 拨的是「整点 − 1 分钟」的 :RING_LEAD ⇒ 10s 到点, 再留 2s 落库
    row, t0 = None, time.time()
    while True:
        row = cmd_bank.read_freeze_row(ser, SUB, 1, wait=WAIT, empty_ok=True)
        if row.get("answered") is False:
            return None, "第 %d/%d 轮: 睡醒后读 pos1 没收到应答" % (i, total)
        if row.get("seq") is not None and row.get("seq") != b0:
            return row, ("第 %d/%d 轮: 拨到 %s ⇒ %s"
                         % (i, total, tgt_i, cmd_bank.rec_row_txt(row)))
        if time.time() - t0 >= RING_TRY:
            return None, ("第 %d/%d 轮: 拨到 %s 后 %.0fs 内 pos1 的序号没前进(还是 %s)"
                          % (i, total, tgt_i, RING_TRY, b0))
        time.sleep(1.0)


def part_hour_frez(ctx):
    """一段 = 4-3 的全部条目: 读存储信息 → 进厂内 → 拨钟 → 等整点写库 → 698 读回 → 否定期望 → 第二个整点 → 跨月。

    ⚠ 观测一律 attach(**禁用 launch**: launch 会复位表、RAM 态清零); 见 CLAUDE.md 调试链纪律 1。
    """
    J, ser = ctx.J, ctx.ser
    print("\n===== 4-3 小时冻结: 拨到整点前等自然分钟步进 → 断点看写库 → 698 读回 =====")

    # ---- ⑧ 冻结对象表第 2 行(FLASH 常量表, 探针按符号地址读; 不停核) ----
    # ⚠ 探针那一次读排在开会话**之前** —— 探针与 gdb 会话抢同一支 J-Link, 用完即关(`with Probe()`)。
    fb = so = None
    if not ctx.waived:
        try:
            with Probe() as pb:
                fb, so = cmd_bank.freobj_read(pb)
        except Exception as exc:            # 探针开不起来 / 读不成 → 这一条记"没做成"
            print("   !! 探针不可用(%s) ⇒ 对象表这一次读不到" % exc)
    rows_ob = cmd_bank.freobj_row(fb, so, OBJ_ROW)
    ok_ob, why_ob = cmd_bank.freobj_check(rows_ob, OBJ_ROW, tag="4-3 小时冻结行")
    J.add("⑧ TAB_FrezObj 第 2 行翻出的 OAD == 规范要的那 2 项(正向有功总电能、反向有功总电能)",
          ok_ob, ("用户指定只做黑盒 ⇒ 这一条不做" if ctx.waived else why_ob),
          crit="⑧", falsify=cmd_bank.HOURFREZ_FALSIFY["⑧"], obs=judge.DEBUG)

    # ---- 前置 · AA80 读小时冻结的存储信息(排边界整点要用 prd; 拿不到就排不出, 别拿 1 硬猜) ----
    print("\n[前置] AA80 读小时冻结的存储信息(%s[%d]) ..." % (CURRENT.FREZ_STORE_BLOCK, IDX))
    store = cmd_bank.frez_store_read(ser, CURRENT.FREZ_STORE_BLOCK, [IDX],
                                     clamp=CURRENT.FREZ_STORE_CLAMP, tag="小时存储信息")
    info = (store or {}).get(IDX) or {}
    prd = info.get("prd")

    # ---- ⑨ 存储深度 == 254(与规范『应可存储 254 个数据』对得上) ----
    ok_dp, why_dp = cmd_bank.frez_store_depth_evidence(
        store, IDX, want_depth=CURRENT.HOURFREZ_DEPTH, tag="4-3 小时冻结")
    J.add("⑨ 小时冻结的存储深度 == %d" % CURRENT.HOURFREZ_DEPTH, ok_dp, why_dp,
          crit="⑨", falsify=cmd_bank.HOURFREZ_FALSIFY["⑨"], obs=judge.DEBUG)

    if not prd or prd == 0xFFFF:
        _stop_unproven(ctx, "小时通道(表项 %d)的周期读不到或不可用(prd=%s) ⇒ 排不出边界整点"
                            % (IDX, prd))
    print("   表项 %d: prd=%d 小时 depth=%s size=%s" % (IDX, prd, info.get("depth"), info.get("size")))

    # ---- 前置 · 进厂内(记录读回受安全判定管)+ 现最新一条(基线) ----
    cmd_bank.enter_factory(ser)
    pre = cmd_bank.read_freeze_row(ser, SUB, 1, wait=WAIT, empty_ok=True)
    print("   基线: 小时冻结(子类 0x%02X) 最新一条 = %s" % (SUB, cmd_bank.rec_row_txt(pre)))

    # ---- 开调试会话(断点观测)。台面没接 J-Link ⇒ None, 白盒那几条如实记"没做成" ----
    ctx.session()
    if ctx.g is not None:
        # 整片 .out 解一次(停核遍历, 内部喂狗, 收尾放行) —— 之后一律查这张图
        gdbinit.build(ctx.g)
    g = ctx.g
    have_wb = g is not None
    if not have_wb:
        print("\n   !! 本次无调试会话 → 断点观测(白盒)整体**未做**; 本次范围 = 仅黑盒"
              "(黑盒只能答『记录有没有推进、时标对不对』, 答不了『写点判的是不是整点』)")

    # ---- 拨钟到第一个整点前 1 分钟(:05 发 ⇒ 55s 后自然分钟步进正好落到整点那一分钟) ----
    now = cmd_bank.read_clock(ser, chip="计量芯", wait=WAIT, quiet=True)
    try:
        now_dt = datetime.datetime.strptime(now or "", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        now_dt = None
    if now_dt is None:
        _stop_unproven(ctx, "表钟读不出来(%r) ⇒ 排不出边界整点" % now)
    b1 = cmd_bank.frez_boundary(cmd_bank.frez_abs_min(now_dt), prd * CURRENT.HOURFREZ_UNIT_MIN)
    tgt = cmd_bank.frez_target(now_dt, b1)
    print("\n[拨钟] 表钟=%s → 目标=%s(整点边界 = 绝对分钟数 %d, 整除 %d = prd %d 小时)"
          % (now, tgt, b1, prd * CURRENT.HOURFREZ_UNIT_MIN, prd))
    cmd_bank.set_meter_clock_set(ser, tgt, chip="计量芯", wait=WAIT)

    # ---- ①②③ 等自然分钟步进停在整点写库 ----
    normal = frez_num = prd_bp = None
    buff = []
    r_g = r_w = None
    if have_wb:
        bp_g, bp_w = _arm_both(ctx)
        print("\n[等] 自然分钟步进走到整点写库(断[A] %s → 断[B] %s, 至多 %.0fs) ..."
              % (breakpoint.text(BP_GATE), breakpoint.text(BP_WRITE), HIT_TIMEOUT))
        r_g, r_w = _wait_both(ctx, bp_g, bp_w, "4-3 第一个整点")
        vg = (r_g or {}).get("vars") or {}
        vw = (r_w or {}).get("vars") or {}
        normal = cmd_bank.st_int(vg.get("normal"))
        frez_num = cmd_bank.st_int(vw.get("frezNum"))
        prd_bp = cmd_bank.st_field(vw.get("stInfo"), "u16Period")
        buff = cmd_bank.gdb_bytes(vw.get("buff"))
        print("   现场: %s / %s | normal=%s frezNum=%s stInfo.u16Period=%s buff=%s"
              % ((r_g or {}).get("detail") or "断[A] 没停到",
                 (r_w or {}).get("detail") or "断[B] 没停到", normal, frez_num, prd_bp,
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
                                 " —— 停到的那一趟不是自然分钟步进"))),
          crit="①", falsify=cmd_bank.HOURFREZ_FALSIFY["①"], obs=judge.DEBUG)

    # ---- ② 一趟只写 1 条 ----
    J.add("② 那一趟只写 1 条(frezNum == 1)", None if frez_num is None else (frez_num == 1),
          ("没有调试会话 ⇒ 这一半不做" if not have_wb else
           "frezNum=%s(写的是 ID_HourFrez = 表项 %d)" % (frez_num, IDX)),
          crit="②", falsify=cmd_bank.HOURFREZ_FALSIFY["②"], obs=judge.DEBUG)

    # ---- ③ 时标落在整点上: 秒 0 / 分 0 / 时是 prd 的整数倍, 且日月年就是拨钟算出的那个整点 ----
    sec_b = buff[0] if len(buff) > 0 else None
    min_b = buff[1] if len(buff) > 1 else None
    hour_b = buff[2] if len(buff) > 2 else None
    day_b = buff[3] if len(buff) > 3 else None
    mon_b = buff[4] if len(buff) > 4 else None
    yr_b = buff[5] if len(buff) > 5 else None
    # ⚠ `prd_bp` 读到 0 时取模会抛(固件在 :403 已把 prd==0 挡掉, 但那是**另一条**读法) —— 先判它。
    # 期望整点 = 拨钟目标(tgt = 「整点 − 1 分钟」的 :05)+ 1 分钟 —— 与 ⑦ 的 exp7 同一算法, 不另造时间算子。
    exp_pt = (datetime.datetime.strptime(tgt, "%Y-%m-%d %H:%M:%S")
              + datetime.timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:00")
    on_grid = (None if (sec_b is None or min_b is None or hour_b is None or not prd_bp)
               else (sec_b == 0 and min_b == 0 and hour_b % prd_bp == 0))
    got_pt = (None if (on_grid is None or day_b is None or mon_b is None or yr_b is None)
              else "%04d-%02d-%02d %02d:%02d:00" % (2000 + yr_b, mon_b, day_b, hour_b, min_b))
    ok3 = None if got_pt is None else (on_grid and got_pt == exp_pt)
    J.add("③ 写库那一刻 buff 的秒/分为 0、时是 prd 的整数倍, 且日月年就是拨钟算出的那个整点", ok3,
          ("写库那一刻 buff 读不到 ⇒ 没有对照物, 这一条没做成" if got_pt is None else
           "buff[:6]=%s ⇒ 时=%s prd=%s(%s), 日月年解出 %s(期望 %s)%s"
           % (" ".join("%02X" % x for x in buff[:6]), hour_b, prd_bp,
              "整点" if on_grid else "**不是整点**", got_pt, exp_pt,
              "" if got_pt == exp_pt else " —— **日月年与那个整点不符**")),
          crit="③", falsify=cmd_bank.HOURFREZ_FALSIFY["③"], obs=judge.DEBUG)

    # ---- ④ 698 读回: 最新一条的时标 == 那一刻 buff 解出的整点 ----
    if SETTLE:
        time.sleep(SETTLE)          # 停在断[B](Write_FrezData 的**调用行**) ⇒ 等它落完
    post = cmd_bank.read_freeze_row(ser, SUB, 1, wait=WAIT, empty_ok=True)
    want_ts = cmd_bank.frez_expect_ts(buff)
    got_ts = (post or {}).get("ts")
    J.add("④ 698 读回最新一条的时标 == 写库那一刻 buff 的时标",
          None if (want_ts is None or got_ts is None) else (got_ts == want_ts),
          ("写库那一刻 buff 读不到 ⇒ 没有对照物, 这一条没做成" if want_ts is None else
           "记录 %s; 写库那一刻 buff[:6] 解出的时标 = %s%s"
           % (cmd_bank.rec_row_txt(post), want_ts,
              "" if got_ts == want_ts else " —— 读回的是 %s" % (got_ts or "读不到"))),
          crit="④", falsify=cmd_bank.HOURFREZ_FALSIFY["④"])

    # ---- ⑤ 非整点的那些分钟里不落库(否定期望) ----
    if have_wb:
        c0 = cmd_bank.read_clock(ser, chip="计量芯", wait=WAIT, quiet=True)
        print("\n[等] 否定期望: %.0fs 窗口里 %s 不该命中(窗口比一个整分间隔长) ..."
              % (NEG_WIN, breakpoint.text(BP_WRITE)))
        r5 = breakpoint.expect_no_hit(g, BP_WRITE, NEG_WIN, vars=VARS_WRITE,
                              label="4-3 非整点不落库(否定期望)",
                              falsify=cmd_bank.HOURFREZ_FALSIFY["⑤"])
        c1 = cmd_bank.read_clock(ser, chip="计量芯", wait=WAIT, quiet=True)
        crossed = cmd_bank.frez_crossed(c0, c1)
        # 窗口里没跨过整分 ⇒ 这次否定期望**空转**(什么都没看), 记"没做成" —— 不许记达成。
        J.add("⑤ %.0fs 窗口里 %s 不命中, 且窗口里表钟跨过一个整分" % (NEG_WIN, breakpoint.text(BP_WRITE)),
              None if (r5 is None or crossed is None) else (r5.get("ok") is True and crossed is True),
              "窗口内表钟 %s → %s(%s); %s"
              % (c0, c1, "跨过整分" if crossed else "**没跨过整分, 这次否定期望空转**",
                 (r5 or {}).get("detail") or "没做成"),
              crit="⑤", falsify=cmd_bank.HOURFREZ_FALSIFY["⑤"], obs=judge.DEBUG)
    else:
        J.add("⑤ %.0fs 窗口里 %s 不命中, 且窗口里表钟跨过一个整分" % (NEG_WIN, breakpoint.text(BP_WRITE)), None,
              "没有调试会话 ⇒ 这一半不做(黑盒没有『没落库』的替身: 读不到记录与没落库分不开)",
              crit="⑤", falsify=cmd_bank.HOURFREZ_FALSIFY["⑤"], obs=judge.DEBUG)

    # ---- ⑥ 相邻两条记录的时标相差 prd 小时 ----
    # 第二个整点是**拨钟推进 prd 小时**造出来的(不真等): 它证"写点落在整数倍 prd 的小时上、时标就是
    # 那个整点", **不证实时周期** —— 实时周期那半支由 ⑤ 的否定期望窗口担着。
    if have_wb:
        now2 = cmd_bank.read_clock(ser, chip="计量芯", wait=WAIT, quiet=True)
        try:
            d2 = datetime.datetime.strptime(now2 or "", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            d2 = None
        if d2 is None:
            J.add("⑥ 698 读回相邻两条记录的时标相差 prd 小时且序号接着上一号", None,
                  "表钟读不出来(%r) ⇒ 第二个整点排不出" % now2,
                  crit="⑥", falsify=cmd_bank.HOURFREZ_FALSIFY["⑥"], obs=judge.DEBUG)
        else:
            b2 = b1 + prd * CURRENT.HOURFREZ_UNIT_MIN
            tgt2 = cmd_bank.frez_target(d2, b2)
            print("\n[拨钟] 第二个整点: 表钟=%s → 目标=%s(整点边界 %d = 上一个 + prd 小时)"
                  % (now2, tgt2, b2))
            cmd_bank.set_meter_clock_set(ser, tgt2, chip="计量芯", wait=WAIT)
            bp_g2, bp_w2 = _arm_both(ctx)
            r_g2, r_w2 = _wait_both(ctx, bp_g2, bp_w2, "4-3 第二个整点")
            if SETTLE:
                time.sleep(SETTLE)
            p1 = cmd_bank.read_freeze_row(ser, SUB, 1, wait=WAIT, empty_ok=True)
            p2 = cmd_bank.read_freeze_row(ser, SUB, 2, wait=WAIT, empty_ok=True)
            gap = cmd_bank.frez_ts_gap(p1, p2)
            want_gap = prd * CURRENT.HOURFREZ_UNIT_MIN
            seq_ok = _seq_next((p1 or {}).get("seq"), (p2 or {}).get("seq"))
            J.add("⑥ 698 读回相邻两条记录的时标相差 prd 小时且序号接着上一号",
                  None if (gap is None or seq_ok is None) else (gap == want_gap and seq_ok is True),
                  "pos1 %s / pos2 %s ⇒ 相差 %s 分钟(prd=%d 小时); 序号 %s; %s"
                  % (cmd_bank.rec_row_txt(p1), cmd_bank.rec_row_txt(p2),
                     gap if gap is not None else "比不出来", prd,
                     "接着上一号" if seq_ok else ("读不到" if seq_ok is None else "**不接着上一号**"),
                     (r_w2 or {}).get("detail") or "第二个整点没停到"),
                  crit="⑥", falsify=cmd_bank.HOURFREZ_FALSIFY["⑥"], obs=judge.DEBUG)
    else:
        J.add("⑥ 698 读回相邻两条记录的时标相差 prd 小时且序号接着上一号", None,
              "没有调试会话 ⇒ 第二个整点造不出来(拨不到既定的那个整点)", crit="⑥",
              falsify=cmd_bank.HOURFREZ_FALSIFY["⑥"], obs=judge.DEBUG)

    # ---- ⑦ 跨月跨日: 拨到未来最近的月末 23:59:05, 等自然跨过月界 ----
    if have_wb:
        now3 = cmd_bank.read_clock(ser, chip="计量芯", wait=WAIT, quiet=True)
        try:
            d3 = datetime.datetime.strptime(now3 or "", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            d3 = None
        if d3 is None:
            J.add("⑦ 跨月跨日那条 %s 仍命中且时标 == 次月 1 日 00:00:00" % breakpoint.text(BP_WRITE), None,
                  "表钟读不出来(%r) ⇒ 排不出月末时刻" % now3,
                  crit="⑦", falsify=cmd_bank.HOURFREZ_FALSIFY["⑦"], obs=judge.DEBUG)
        else:
            tgt3 = cmd_bank.hourfrez_month_target(d3)
            b3 = cmd_bank.frez_abs_min(datetime.datetime.strptime(tgt3, "%Y-%m-%d %H:%M:%S")) + 1
            # 月末那一分钟的绝对小时数是不是 prd 的整数倍 —— prd != 1 时可能不是, 那时这条**证不了**
            # (固件按"绝对小时数整除 prd"挑边界, 月末整点不一定是它的边界)。记"没做成", 不记 FAIL。
            on_grid = b3 % (prd * CURRENT.HOURFREZ_UNIT_MIN) == 0
            print("\n[拨钟] 月末: 表钟=%s → 目标=%s(边界分钟数 %d)" % (now3, tgt3, b3))
            if not on_grid:
                J.add("⑦ 跨月跨日那条 %s 仍命中且时标 == 次月 1 日 00:00:00" % breakpoint.text(BP_WRITE), None,
                      "本表 prd=%d 小时 ⇒ 月末整点不是它的边界(绝对分钟数不整除 %d), 这条本次证不了"
                      % (prd, prd * CURRENT.HOURFREZ_UNIT_MIN),
                      crit="⑦", falsify=cmd_bank.HOURFREZ_FALSIFY["⑦"], obs=judge.DEBUG)
            else:
                cmd_bank.set_meter_clock_set(ser, tgt3, chip="计量芯", wait=WAIT)
                bp_g3, bp_w3 = _arm_both(ctx)
                r_g3, r_w3 = _wait_both(ctx, bp_g3, bp_w3, "4-3 跨月跨日")
                if SETTLE:
                    time.sleep(SETTLE)
                pm = cmd_bank.read_freeze_row(ser, SUB, 1, wait=WAIT, empty_ok=True)
                # ⚠ 断点记录里 `buff` 是 gdb 的原样字符串, 不是字节 —— 必须先过 `gdb_bytes`,
                #   否则 `frez_expect_ts` 拿字符去加 2000, 真跑当场 TypeError(离线静态检查看不见)。
                ts7 = cmd_bank.frez_expect_ts(cmd_bank.gdb_bytes(((r_w3 or {}).get("vars") or {}).get("buff")))
                # 期望的时标: 次月 1 日 00:00:00 —— 由拨钟目标直接算出(月末那天 23:59:05 + 55s)
                exp7 = (datetime.datetime.strptime(tgt3, "%Y-%m-%d %H:%M:%S")
                        + datetime.timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:00")
                got7 = (pm or {}).get("ts")
                J.add("⑦ 跨月跨日那条 %s 仍命中且时标 == 次月 1 日 00:00:00" % breakpoint.text(BP_WRITE),
                      None if got7 is None else (got7 == exp7 and _hit_any(r_g3, r_w3)),
                      "记录 %s; 期望 %s(写库那一刻 buff 解出的是 %s); 断[A] %s / 断[B] %s"
                      % (cmd_bank.rec_row_txt(pm), exp7, ts7 or "读不到",
                         (r_g3 or {}).get("detail") or "没停到",
                         (r_w3 or {}).get("detail") or "没停到"),
                      crit="⑦", falsify=cmd_bank.HOURFREZ_FALSIFY["⑦"], obs=judge.DEBUG)
    else:
        J.add("⑦ 跨月跨日那条 %s 仍命中且时标 == 次月 1 日 00:00:00" % breakpoint.text(BP_WRITE), None,
              "没有调试会话 ⇒ 这条不做", crit="⑦", falsify=cmd_bank.HOURFREZ_FALSIFY["⑦"], obs=judge.DEBUG)

    # ---- ⑩ 环回: 拨钟逐条补满 RING_FILL 条 ⇒ 序号推进 RING_FILL、最旧那格换成 +1 号、第 RING_FILL+1 格读不到 ----
    # 判据是「写满不停写」的反面: 序号仍在推进(不是写满就不写了)、最旧那格换人了(顶掉而不是原地不动)、
    # 第 RING_FILL+1 格读不到(不是往后加格)。三条一起看才分得出环回与"写到满就停"。
    # 耗时(**推算, 不是实测**): 每轮 ≈ 读钟 0.5 + 拨钟 0.6 + 回读 0.5 + 睡 ~12 + 读记录 0.5 ≈ 14.1s
    #   ⇒ 254 × 14.1 ≈ 60 分钟(还要加上轮询那几秒)。
    # ⚠ 若沿用现有 `frez_target(sec=5)` 的那条 55s 等待, 一段就是 ≈ 4 小时 —— 所以 ⑩ 用 RING_LEAD 拨到
    #   「整点 − 1 分钟」的 :50 秒处(真实 10s 后自然跨过整点)。
    before = cmd_bank.read_freeze_row(ser, SUB, 1, wait=WAIT, empty_ok=True)
    s0 = (before or {}).get("seq")
    filled = 0
    ring_log = ["补之前最新一条: %s" % cmd_bank.rec_row_txt(before)]
    for k in range(1, RING_FILL + 1):
        row, why = _fill_one(ser, prd, k, RING_FILL)
        ring_log.append(why)
        if row is None or row.get("seq") is None:
            print("   [环回 %d/%d] %s" % (k, RING_FILL, why))
            break
        filled = k
        if k == 1 or k % 25 == 0 or k == RING_FILL:
            print("   [环回 %d/%d] %s" % (k, RING_FILL, why))
    top = cmd_bank.read_freeze_row(ser, SUB, 1, wait=WAIT, empty_ok=True)
    oldest = cmd_bank.read_freeze_row(ser, SUB, RING_FILL, wait=WAIT, empty_ok=True)
    over = cmd_bank.read_freeze_row(ser, SUB, RING_FILL + 1, wait=WAIT, empty_ok=True)
    s_top = (top or {}).get("seq")
    s_old = (oldest or {}).get("seq")
    # 半途中止 / 任何一次读回没收到字节 / 序号读不到 ⇒ 未证(不折成 FAIL); 三条一起满足才算这一条过。
    read_ok = all(r.get("answered") is not False for r in (before, top, oldest, over))
    ok10 = (None if (filled < RING_FILL or not read_ok
                     or s0 is None or s_top is None or s_old is None)
            else (s_top == s0 + RING_FILL and s_old == s0 + 1 and over.get("empty") is True))
    J.add("⑩ 记录区装满 %d 格后环回顶掉最早那条(不是停止写): 拨钟逐条补满 %d 条之后最新一条的"
          "序号恰好前进 %d, 最旧那一条的序号是补之前最新那条的下一个, 且第 %d 格读不到"
          % (RING_FILL, RING_FILL, RING_FILL, RING_FILL + 1),
          ok10,
          ("补满 %d/%d 条; 最新 %s(补之前 %s, 期望 +%d); 第 %d 格(最旧) %s(期望 %s); "
           "第 %d 格 %s; 尾三行: %s")
          % (filled, RING_FILL, cmd_bank.rec_row_txt(top), cmd_bank.rec_row_txt(before),
             RING_FILL, RING_FILL, cmd_bank.rec_row_txt(oldest),
             (s0 + 1) if s0 is not None else "补之前最新那条 + 1",
             RING_FILL + 1, cmd_bank.rec_row_txt(over), " | ".join(ring_log[-3:])),
          crit="⑩", falsify=cmd_bank.HOURFREZ_FALSIFY["⑩"], obs=judge.SERIAL)


def _banner():
    return ("== 4-3 小时冻结 | 工程=%s 表号=%s ==\n"
            ".. 驱动=拨钟到「整点 − 1 分钟」的 :05 后等**自然分钟步进**(校时那趟自己不落库); "
            "白盒两个断点 %s(读 %s) → %s(读 %s); 记录读回走 698 GetRequestRecord 子类 0x%02X; "
            "⑩ 环回另按 RING_LEAD 拨到「整点 − 1 分钟」的 :%d 逐条补满 %d 条; "
            "⚠ 跑完表钟停在 ⑦ 那个月末整点之后(⑩ 又推进了约 %d 小时), 收尾走 scripts/_restore_all.py"
            % (CURRENT.PROJECT, CURRENT.TABLE_ADDR.hex().upper(),
               breakpoint.text(BP_GATE), "/".join(VARS_GATE),
               breakpoint.text(BP_WRITE), "/".join(VARS_WRITE), cmd_bank.HOURFREZ_SUBCLASS,
               RING_LEAD, RING_FILL, RING_FILL))


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "4-3 小时冻结(整点边界写库/一趟 1 条/时标与 698 读回一致/非整点不落库/相邻两条差 prd 小时/跨月跨日/记录区环回)",
        cmd_bank.hour_frez_criteria,
        name="4_3_hour_frez",
        parts=[("4-3 小时冻结段", part_hour_frez)],
        gdb=breakpoint, banner=_banner(),
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV)))
