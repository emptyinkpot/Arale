# -*- coding: utf-8 -*-
"""在证什么: 阶梯结算冻结(子类 0x11)由 `Check_BillFrezY` 在**跨结算边界**上生成 ——
  断[风格判定] 看判定体真被走到、那刻风格是本地(放行); 没有跨边界时, 注入一张固件认它有效、
  而按那一刻表钟**还没走到**的结算日期表, 两个写点(月支 / 年支)仍都不该命中;
  **拨钟自然跨年**那一趟年支写点该命中且落下跨过的那个结算点; **注入月份差**那一趟月支写点该
  连停两次(一次写两条)且两条的结算点分别是上一个月与当月。
会向表写什么: 645 进厂内; 拨两次表钟(先拨到 12 月 31 日 23:59:30 造跨年, 再拨回真实时间);
  注入改栈上 `buff` 与 `g_HisTime`(`TaskFreeze.c:143` 每趟结尾会刷回真值, 固件自愈), 参数区一个
  字节不动。产物是 11 条阶梯结算冻结记录(年支一条、⑤ 月支那趟两条; ⑨ 两轮各落 4 条 = 一次补冻上限)。
  台面收尾走 `python scripts/_restore_all.py`。
跑法: `python project/tests/_test_4_7_billfrez_y.py`(真串口 + 真探针; `--no-gdb` 只做黑盒半边)。
结论怎么读: 账本末行「判据: 满足 n/N」, N=9; 退 0 = 通过 / 1 = 失败 / 2 = 未定论。
"""
import datetime

from common import judge              # 观测种类常量(黑盒标 SERIAL, 白盒那几条标 DEBUG)
from common import trial              # 运行外壳: argv/日志/账本/串口/会话/收尾三连/退出码
from meterlib import cmd_bank         # 帧目录 + 语义动词积木箱(每步自带打印/判 PASS·TBD·FAIL)
from project import CURRENT           # 本工程画像: 表号/.out 路径/喂狗点(库不认表, 只有脚本认)
from swdbg import breakpoint          # 断点级白盒(停核 + 注入 + 读函数内局部量); 没接 J-Link → None
# 整片 .out 地图: 开会话后解一次, 之后断点/注入点只查它(见 swdbg/gdbinit.py 文件头)
from swdbg import gdbinit
from swdbg.probe import Probe         # SWD 直读(读 FLASH 常量表; 不停核)

# ---- 子项参数(纯数据; 要改的在这里改) ----
WAIT = 3.0                     # 记录读回 / 拨钟确认的单次等待
GATE_WAIT = cmd_bank.BFY_WAIT          # 等一个自然分钟步进停在注入点(分钟步进 ≤60s 一次)
INJ_WAIT = cmd_bank.BFY_INJ_WAIT       # 注入放行后等写点命中(同样只等下一趟分钟步进)
SAME_WIN = cmd_bank.BFY_SAME_WIN       # 否定期望窗口(注入那一停放行后的同一趟)
CROSS_TRIES = cmd_bank.BFY_CROSS_TRIES # 拨钟后认"跨年那一趟"至多等几个自然分钟步进
SUBCLASS = cmd_bank.BFY_SUBCLASS       # 阶梯结算冻结记录子类 0x11
OBJ_ROW = 10                   # 阶梯结算冻结用的对象表行号(TAB_FrezObj 第 10 行)
POS_MAX = CURRENT.BILLFREZY_DEPTH      # 记录区格数 = 存储深度(6)
RING_PER_ROUND = CURRENT.BILLFREZY_ADD # 一轮月结算边界落几条 = **固件一次补冻上限**: 4 =
                                       # `Config/MengXi/UserCfg.c:196` 那张 `TAB_FrezAdd[] =
                                       # {0,0,0,7,0,0,12,4}` 的**末位**(下标 7), 也就是固件
                                       # `TaskFreeze.c:993` 取的 `frezAdd`; 与日档那一位 7 /
                                       # 时档 0 / 分档 0 是同一张表的不同下标。
RING_EXTRA = 2                 # ⑨ 再跨几次月结算边界(每轮落 RING_PER_ROUND 条): 6 格装 4×2=8 条 ⇒ 第 1 轮就顶掉最早 4 条

# ---- 断点: 模块级**字面量元组** ----
# `scripts/_check_anchors.py` 用 `ast.literal_eval` 抠 `BP_<X>`/`VARS_<X>` 这两个名字逐条对源码核
# (变量在断点那一行赋过值没有); 写成 `cmd_bank.SOMETHING` 那种表达式就抠不出来, 等于没人核过。
# ⚠ 断点一律**结构定位**: 写 `("prev"|"call", 函数, 被调, 第几处)`, 不写行号 —— 行号随源码改动漂,
#   这两种写法由 `.out` 反汇编解出地址(`gdbinit.resolve_spec`)。两处落点离线核过
#   (`info scope Check_BillFrezY` + `disassemble`, 2026-09-24):
#   · `("prev", …, "Read_ParaData", 1)` = `0x32e9e` —— 风格判定已放行(TP_Remote 会在它前面
#     `0x32e9a` 直接跳到函数尾, 到不了这儿);
#   · `("call", …, "Copy_Data", 1)` = `0x32eb2` —— 即 `dateNum = 0;` 那条指令(参数刚读进 `buff`、
#     日期循环还没跑)。
BP_GATE = ("prev", "Check_BillFrezY", "Read_ParaData", 1)
VARS_GATE = ("TAB_MeterSty.style",)       # 那刻的风格值(判据①的硬证)
BP_INJ = ("call", "Check_BillFrezY", "Copy_Data", 1)
BP_MONTH = ("prev", "Check_BillFrezY", "Write_FrezData", 1)  # 月支写点 @TaskFreeze.c:1041
BP_YEAR = ("prev", "Check_BillFrezY", "Write_FrezData", 2)   # 年支写点 @TaskFreeze.c:1075
# 两个写点那一刻可读的两个量。由 `info scope Check_BillFrezY` 的**位置表逐区间核过**(2026-09-16):
#   两处都落在 `frezNum`/`buff` 的同一个可读区间 `0x331ec-0x33418` 里。
#   ⚠ **别把 `flag`/`over`/`flg`/`off` 加进来** —— 它们在两处的**空洞区间**内(`0x33224-0x3341c` 等),
#     读回来是"读不到", 而那看起来像"固件没给值"(取证时会把停错地方误读成固件问题)。
#   `buff[0..5]` = 本次冻结的时标 `[秒分时日月年]`(4-4 的 J 列有同类取证)。
#   ⚠ `frezNum`/`buff` 都是**寄存器驻留**(`frezNum` 在 `$r6`), 只能读、不能按地址写回。
VARS_MONTH = ("frezNum", "buff")
VARS_YEAR = ("frezNum", "buff")
# 注入那一停(`:887`)读的量 —— 由 `info scope Check_BillFrezY` 的位置表核过(2026-09-16):
#   `g_CurTime`/`g_HisTime`(全局量)与 `buff`(栈上 12B, `$sp+8`)在 0x32eb2 处都有位置。
#   ⚠ **别把 `dateNum` 加进来** —— 那一处它还没有位置表区间(区间从 0x32eb4 起、值当下在 `$r7` 里),
#     读回来是"读不到", 而那看起来像"固件没给值"。
#   ⚠ **不收 `flag`**(2026-09-17 去掉): 位置表说它在 `$sp-124` 有区间, 但 :887 那一停它还没被赋过
#     —— 声明 `BOOL flag = OTHER;` 在 :871, 真值要到 :895/:903 才写 ⇒ 读回来恒是那个占位值,
#     做注入前对照量**不带任何信息**。位置可读 ≠ 值算数, 这正是断点体检要分的那两件事。
# ⚠ 必须是**字面量元组**(不能写成 `cmd_bank.BFY_AT_VARS`): `_check_anchors.py` 抠的就是字面量。
VARS_INJ = ("g_CurTime", "g_HisTime", "buff")

# ---- 六条的标签(半途中止时也要一次记全, 见各 _add 调用点) ----
L3 = "③ 年支: 拨钟自然跨到次年 1 月 1 日 0 点, 那一停把 buff 写成有效年结算日期表 ⇒ 年支写点命中且 frezNum==1"
L4 = "④ 698 读回: 跨年那一趟之后最新一条序号**恰加 1**, 且时标 == 次年 1 月 1 日 0 点"
L5 = ("⑤ 月支: 拨回真实时间后那一停把 buff 写成有效月结算日期表、g_HisTime 月份回退两格 "
      "⇒ 月支写点**连停两次**(这一趟写两条), 每次都 frezNum==2, 且两次 buff[0..5] 分别是 "
      "0/0/0/1/上一个月/当年 与 0/0/0/1/当月/当年")
L6 = "⑥ 698 读回: 月支那一趟之后最新一条序号**推进两格**, 且时标 == 当月 1 日 0 点"


def _add(J, label, ok, why, crit, obs=judge.SERIAL):
    """记一条判据(`falsify` 按 `crit` 从库里的单点取)+ 打一行三态。"""
    J.add(label, ok, why, crit=crit, falsify=cmd_bank.BFY_FALSIFY[crit], obs=obs)
    print("   [%s] %s —— %s" % ({True: "PASS", False: "FAIL", None: "TBD"}[ok], label, why))


def _hexb(b):
    """字节 → 空格分隔的十六进制(**读不出来就明写"读不到"**, 不拿空串冒充)。"""
    return " ".join("%02X" % x for x in b) if b else "读不到"


def _write(g, assigns):
    """在**停住态**按注入表逐条写 → 记下 (表达式, 新值, 旧值)。

    走库的 `Session.inject`: 白名单 / 拒裸地址 / 记旧值 / 收尾自动写回, 一行一条。
    """
    done = []
    for expr, val in assigns:
        rec = g.inject(expr, val)
        old = rec[3] if isinstance(rec, tuple) and len(rec) > 3 else None
        done.append((expr, str(val), old.decode("utf-8", "replace") if old else None))
    return done


def _stop_at(g, bp, timeout, vars_, label):
    """等这个断点命中, **停着不放行** → `(hit, vals)`。

    `Session.wait_break` 是唯一留住停住态的等待原语(`wait_hit`/`wait_only` 读完值就放行),
    而"这一停是不是跨年那一趟 / 要不要在**这一停**上写东西"必须停着才判得出来。
    ⚠ 断点写法走 `breakpoint.to_bpno`(结构元组归它解) —— `Session.break_at` 只吃**行号**,
      传 4 元组进去当场 `TypeError`。
    ⚠ 自己挂的断点超时那一路不会撤(`drop=True` 只在命中支生效, 见 `to_bpno` 的 ⚠: 4-7 就是
      这么踩的) ⇒ 补撤, 不然它留在槽里、核跑着撞上就把自己撂停 —— 那是"其后串口全哑"的来源。
    """
    bpno, own = breakpoint.to_bpno(g, bp)
    hit, vals = g.wait_break(bpno, timeout, vars=vars_, drop=True)
    if hit is None:
        if own:
            g._disarm(bpno)
        print("   !! %s 没停到(等满 %.0fs) → 白盒这一次不做" % (label, timeout))
    return hit, vals


def _neg_trip(ctx, kind, label):
    """② 一趟否定期望: 停注入点 → 注"还没走到"的结算日期表 → 放行 → 两个写点都不许命中。

    返回 `(ok, why)`, `ok` 三态 —— None = 这一趟没做成(停不到 / 注入值在那台面态下造不出),
    True = 两个写点都没命中(本条达成), False = 命中了一个(固件在没有边界时也写了记录)。

    ⚠ 出厂那份 12 个 99 的参数表会让 `:917` 每趟早退, 两个写点对**任何**固件都不命中 —— 照它跑
      出来的"没命中"是恒真, 证不了东西。所以这一趟必须**注一张固件认它有效的表**进去, 让判定体
      真的算过一遍再决定不写(`bfy_neg_trip` 连同"算出来确实是 0"一起交给我们核)。
    ⚠ 两个写点**同时**挂上: `wait_break` 只认自己等的那个号, 别的命中了会被静默掠过并放行
      (见它的 docstring) —— 只盯年支会漏掉"月支写了"。
    """
    g = ctx.g
    hit, vals = _stop_at(g, BP_INJ, GATE_WAIT, VARS_INJ, label + "的注入点")
    if hit is None:
        return None, "没停到注入点(等满 %.0fs) ⇒ 这一趟的边界没造出来" % GATE_WAIT
    gcur = cmd_bank.gdb_bytes(vals.get("g_CurTime"))
    ghis = cmd_bank.gdb_bytes(vals.get("g_HisTime"))
    trip = cmd_bank.bfy_neg_trip(kind, gcur, ghis)
    if trip is None:
        return None, ("那一刻 `g_CurTime`/`g_HisTime` 读不全(%s / %s) ⇒ 注入表算不出"
                      % (_hexb(gcur), _hexb(ghis)))
    assigns, (dn, flag, fn) = trip
    if dn < 1 or fn != 0:
        return None, ("那一刻造不出『还没走到』的边界: 注进去的表的 dateNum=%s flag=%s, 而固件算式"
                      "给出 frezNum=%s(要 dateNum≥1 且 frezNum==0) ⇒ 这一趟**不许硬跑**, 记未证"
                      % (dn, flag, fn))
    g_bp, m_bp = ctx.bp(BP_YEAR), ctx.bp(BP_MONTH)
    n0 = len(g.other_hits)
    g.go(settle=False)                        # 放行: 写点只差这一趟剩下的那几条指令
    hit_y, _vals = g.wait_break(g_bp, SAME_WIN, vars=VARS_YEAR, drop=True)
    crossed = len(g.other_hits) - n0
    # 两个写点都撤掉, 撤完核在跑(`_disarm` 自己 ensure_stopped → drop → ensure_running):
    # 命中那一路 drop=True 已撤, 它当场返回; 超时那一路还挂着 —— 留着的话它每分钟步进自己
    # 命中一次把核撂停, 后面每一帧都像"串口坏了"(见 `_disarm` 的 ⚠)。
    g._disarm(g_bp)
    g._disarm(m_bp)
    if hit_y is not None:
        return False, ("年支写点命中了(停在 %s) —— 那一刻的边界离线算作没走到(frezNum=0), "
                       "固件却写了记录" % hit_y.where())
    if crossed:
        return False, ("年支写点没命中, 但同一窗口里**另一个写点**命中 %d 次(被 `wait_break` "
                       "掠过放行了) ⇒ 依然是没跨边界也写了记录" % crossed)
    return True, ("注入 %d 处(表的 dateNum=%s flag=%s 而固件算式给 frezNum=0), 放行后 %.0fs "
                  "窗口内两个写点都没命中" % (len(assigns), dn, flag, SAME_WIN))


def _month_round(ctx, tag):
    """再跨一次月结算边界: 拨回真实时间 → 停在注入点注入月表 → 月支写点该连停 `RING_PER_ROUND` 次。

    与 ⑤ 那一趟同形(只是月份差由 `back=RING_PER_ROUND` 造、要等 `RING_PER_ROUND` 次命中), 不再逐
    停读值(那几条判据已由 ⑤ 认领) —— 它要的是**条数**, 给 ⑨ 造环回。
    `hits` = `RING_PER_ROUND` = 这一趟落了这么多条; None = 这一趟没做成(`why` 说清是哪一步)。
    ⚠ 注入前先拿 `bfy_boundary_check` **离线核这一轮该落几条** —— 月份差没写成 `RING_PER_ROUND`
      的话, "停了几次"与"固件该写几条"对不上, 而那在账本里长得与"固件写少了"一样。
    ⚠ 停在注入点却算不出注入表时**必须放行**(`g.go()`): 停着不放行会撂住核, 其后每条串口帧
      整帧无应答 —— 现象与"串口坏了"一模一样。
    """
    g, ser = ctx.g, ctx.ser
    cmd_bank.set_meter_clock_set(ser, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                 chip="计量芯", wait=WAIT)
    hit, vals = _stop_at(g, BP_INJ, GATE_WAIT, VARS_INJ, "4-7 %s 的自然分钟步进" % tag)
    if hit is None:
        return None, "没停到注入点(等满 %.0fs)" % GATE_WAIT
    gcur = cmd_bank.gdb_bytes(vals.get("g_CurTime"))
    if cmd_bank.bfy_ymd(gcur) is None:
        g.go()
        return None, "那一刻 g_CurTime 读不出来(%s) ⇒ 注入表算不出" % _hexb(gcur)
    my = cmd_bank.bfy_months_back(gcur, RING_PER_ROUND)
    chk = None if my is None else cmd_bank.bfy_boundary_check(
        cmd_bank.BFY_MONTH_BYTES, gcur, bytes(gcur[:4]) + bytes((my[0], my[1])))
    if chk is None:
        g.go()
        return None, "那一刻的月结算表算不出(g_CurTime 取不全) ⇒ 这一轮不做"
    if chk[2] != RING_PER_ROUND:
        g.go()
        return None, ("离线核这一轮该落 %s 条, 而注入表要的是 %d 条(dateNum=%s flag=%s) ⇒ "
                      "这一轮**不许硬跑**, 记未证" % (chk[2], RING_PER_ROUND, chk[0], chk[1]))
    bp_m = ctx.bp(BP_MONTH)             # ⚠ 趁停着下(`resume` 之后 gdb 拒插断点)
    _write(g, cmd_bank.bfy_month_assign(gcur, back=RING_PER_ROUND))
    g.go(settle=False)                  # 放行: 写点只差这一趟剩下的那几条指令
    hits, wheres = [], []
    for k in range(RING_PER_ROUND):
        # ⚠ 只有最后一次才 `drop=True` —— 中途撤了断点后几次永远等不到, 现象与"固件只写一条"一样。
        h, _v = g.wait_break(bp_m, INJ_WAIT, vars=VARS_MONTH,
                             drop=(k == RING_PER_ROUND - 1))
        if h is None:
            break
        hits.append(h)
        wheres.append(h.where())
        if len(hits) < RING_PER_ROUND:
            g.go(settle=False)          # 走循环: `++buff[4]` 之后紧接着下一次写
    # 收尾两种走法不一样: 最后一次命中那一路 `drop=True` 已撤断点、`_disarm` 当场返回而核还停着
    # ⇒ 必须显式放行; 中途超时那一路断点还挂着 ⇒ `_disarm` 自己"叫停→撤→放行"。同 ⑤。
    g._disarm(bp_m)
    if len(hits) == RING_PER_ROUND:
        g.go()
    if not hits:
        return None, "月支写点没命中(等满 %.0fs) ⇒ 这一趟一条都没落" % INJ_WAIT
    if len(hits) < RING_PER_ROUND:
        return None, ("月支写点只停了 %d 次(%s), 第 %d 次没等到(等满 %.0fs) ⇒ 这一趟该写 %d 条"
                      % (len(hits), " / ".join(wheres), len(hits) + 1, INJ_WAIT, RING_PER_ROUND))
    return RING_PER_ROUND, "月支写点停 %d 次(%s)" % (len(hits), " / ".join(wheres))


def part_billfrez_y(ctx):
    """一段 = 4-7 的全部条目: 黑盒基线 → 否定期望 → 风格判定 → 拨钟自然跨年(年支) → 注入造月支。

    ⚠ 观测一律 attach(**禁用 launch**: launch 会复位表、RAM 态清零); 见 CLAUDE.md 调试链纪律 1。
    """
    J, ser = ctx.J, ctx.ser
    print("\n===== 4-7 阶梯结算冻结: 自然分钟步进(否定期望) + 拨钟自然跨年 + 注入造月支边界 =====")

    # ---- ⑦ 冻结对象表第 10 行(FLASH 常量表, 探针按符号地址读; 不停核) ----
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
    _add(J, "⑦ TAB_FrezObj 第 10 行翻出的 OAD == 规范要的那 1 项(805→20320200), 第 1 槽起为空",
         ok_ob, ("用户指定只做黑盒 ⇒ 这一条不做" if ctx.waived else why_ob),
         crit="⑦", obs=judge.DEBUG)

    ctx.session()
    # 整片 .out 解一次(要停核遍历, 内部按块喂狗, 收尾放行) —— 之后 `ctx.bp()` /
    # `inject_anchor()` / `decision_anchor()` 一律查这张图, 谁都不再独立反汇编。
    if ctx.g is not None:
        gdbinit.build(ctx.g)
    g = ctx.g
    have_wb = g is not None
    if not have_wb:
        if ctx.waived:
            print("   -- 本次**用户指定**只做黑盒(串口观测) ⇒ 断点观测(白盒)按指定不做"
                  "(J 列须记明本次范围)")
        else:
            print("   !! 本次无调试会话 → 断点观测(白盒)整体**未做**; 本次范围 = 仅黑盒"
                  "(黑盒只能答『记录有没有推进』, 答不了『判定体走到没有、风格判定朝哪边』 —— "
                  "见 CLAUDE.md「凭什么算「真实测试」·两种观测」)")

    # ---- 前置: 进厂内 —— 记录读回受安全判定管(5-3 实踩: 厂外读记录会被打回) ----
    cmd_bank.enter_factory(ser)

    # ---- ⑧ 的前半: AA80 读阶梯结算冻结的存储信息(深度那一格) ----
    store_y = cmd_bank.frez_store_read(ser, CURRENT.FREZ_STORE_BLOCK, [CURRENT.BILLFREZY_INDEX],
                                       clamp=CURRENT.FREZ_STORE_CLAMP, tag="阶梯存储信息")

    # ---- 黑盒基线: 阶梯结算冻结记录最新一条 ----
    pre = cmd_bank.read_freeze_row(ser, SUBCLASS, 1, wait=WAIT, empty_ok=True)
    print("   基线: 阶梯结算冻结(子类 0x%02X) 最新一条 = %s" % (SUBCLASS, cmd_bank.rec_row_txt(pre)))

    # ---- ① 风格判定方向: 等自然分钟步进把判定体走到, 读那刻的 style ----
    if have_wb:
        bpno = ctx.bp(BP_GATE)
        r = breakpoint.wait_hit(g, bpno, GATE_WAIT, vars=VARS_GATE,
                                label="4-7 风格判定放行那一停(它后面那句 Read_ParaData 之前)",
                                crit=None, falsify=None)
        # ⚠ 超时那一路不会撤(`wait_hit` 的 drop 只在命中支用) —— 补撤。留着的话它每分钟步进
        #   自己命中一次把核撂停, 后面每一帧都像"串口坏了"(见 `to_bpno` 的 ⚠)。
        g._disarm(bpno)
        hit_detail = (r or {}).get("detail") or "没停到"
        style_raw = ((r or {}).get("vars") or {}).get("TAB_MeterSty.style")
        st = cmd_bank.bfy_style_txt(style_raw)
        _L1 = "① 风格判定放行(走到它后面那句 Read_ParaData) + style==TP_Local"
        if r is None or r.get("ok") is not True:
            _add(J, _L1, None,
                 "没停到那个锚点(等满 %.0fs) ⇒ 判定体走到没有这件事本次没做成 —— %s"
                 % (GATE_WAIT, hit_detail), crit="①", obs=judge.DEBUG)
        elif st is None:
            _add(J, _L1, None,
                 "停在锚点了(%s), 但 `TAB_MeterSty.style` 读不出来(值=%r) ⇒ 判定朝哪边这次判不了"
                 % (hit_detail, style_raw), crit="①", obs=judge.DEBUG)
        else:
            _add(J, _L1, st,
                 "停在锚点(%s), 那刻 TAB_MeterSty.style=%s ⇒ 风格判定%s"
                 % (hit_detail, style_raw, "放行" if st else "**拦掉**(判定体不执行)"),
                 crit="①", obs=judge.DEBUG)
    else:
        _add(J, "① 风格判定放行(走到它后面那句 Read_ParaData) + style==TP_Local", None,
             "本次%s ⇒ 判定体走到没有、风格判定朝哪边，这一次都没做成(本半支没有可降级的黑盒替身)"
             % ("用户指定只做黑盒" if ctx.waived else "无调试会话"), crit="①", obs=judge.DEBUG)

    # ---- ② 白盒: 两趟否定期望 —— 各注一张"固件认它有效、而那一刻还没走到"的结算日期表 ----
    # ⚠ 出厂那份参数表是 12 个 99(固件在 `:917` 每趟早退), 拿它当"没有边界"的证据是**恒真**:
    #   两个写点对任何固件都不命中。必须注一张**有效**的表进去, 让判定体真的算过一遍
    #   (`frezNum == 0`)才决定不写 —— 这一条才证得出东西。
    for kind, name in (("year", "年支"), ("month", "月支")):
        L = ("② 没有跨结算边界时不产生(%s那一趟: 注入一张『那一刻还没走到』的结算日期表 ⇒ "
             "月支/年支两个写点都不命中)" % name)
        if have_wb:
            ok, why = _neg_trip(ctx, kind, name)
        else:
            ok, why = None, ("本次%s ⇒ 这一趟没做成(白盒观测没开, 本半支没有可降级的黑盒替身)"
                             % ("用户指定只做黑盒" if ctx.waived else "无调试会话"))
        _add(J, L, ok, why, crit="②", obs=judge.DEBUG)

    # ---- ② 黑盒半边: 这两趟都没有跨边界 ⇒ 记录不推进 ----
    mid = cmd_bank.read_freeze_row(ser, SUBCLASS, 1, wait=WAIT, empty_ok=True)
    adv0 = cmd_bank.rec_advanced(pre, mid)
    _add(J, "② 没有跨结算边界 ⇒ 阶梯结算冻结记录不推进", cmd_bank.bfy_neg_ok(adv0),
         "基线 %s → 走完普通分钟步进 %s (没推进 ⇒ 本条达成)%s"
         % (cmd_bank.rec_row_txt(pre), cmd_bank.rec_row_txt(mid),
            " —— !! 这一趟**推进了**, 而没有跨结算边界, 固件不该产生" if adv0 is True else ""),
         crit="②")

    # ---- ③④ 年支: 拨钟到 12 月 31 日 23:59:30, 等它自然跨到次年 1 月 1 日 0 点 ----
    # ⚠ 拨钟本身(校时)那一趟**也会走到同一个停点**(本台 `TAB_FrezAdd[7]=4 ≠ 0`,
    #   `:878` 那道"非自然整分就返回"的判定放行) ⇒ 停到之后要读 `g_CurTime` 认跨没跨,
    #   没跨就放行等下一趟。认错了的下场是"在 12 月 31 日那一趟注入", 年差为 0 ⇒ 写点不命中,
    #   而那个现象与"固件没走年支"长得一模一样(**静默假阴性**)。
    inj3 = None          # 年支写点那一停的记录(③ 的账)
    hit3 = None          # 年支写点那一停本身(④ 的前提 = "这一趟真写了一条")
    vals3 = {}           # 年支写点那一停读到的量
    exp3_ts = None       # 跨过的那个结算点(由拨钟目标算, 不从记录里反读)
    if have_wb:
        now0 = cmd_bank.read_clock(ser, chip="计量芯", wait=WAIT, quiet=True)
        try:
            now0_dt = datetime.datetime.strptime(now0 or "", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            now0_dt = None
        if now0_dt is None:
            _add(J, L3, None, "表钟读不出来(%r) ⇒ 拨钟目标排不出, 这一条没做成" % now0,
                 crit="③", obs=judge.DEBUG)
        else:
            tgt3 = "%04d-12-31 23:59:30" % now0_dt.year
            exp3_ts = "%04d-01-01 00:00:00" % (now0_dt.year + 1)
            print("\n[拨钟] 造跨年边界: 表钟=%s → 目标=%s(等它自然跨到 %s)" % (now0, tgt3, exp3_ts))
            cmd_bank.set_meter_clock_set(ser, tgt3, chip="计量芯", wait=WAIT)
            for k in range(CROSS_TRIES):
                hit, vals = _stop_at(g, BP_INJ, GATE_WAIT, VARS_INJ,
                                     "4-7 第 %d 次等的自然分钟步进" % (k + 1))
                if hit is None:
                    break
                gcur = cmd_bank.gdb_bytes(vals.get("g_CurTime"))
                if cmd_bank.bfy_at_newyear(gcur) is not True:
                    print("   第 %d 次停在 %s, g_CurTime=%s ⇒ 还没跨年, 放行等下一趟"
                          % (k + 1, hit.where(), _hexb(gcur)))
                    g.go()
                    continue
                print("   第 %d 次停在 %s, g_CurTime=%s ⇒ 这一趟就是跨年那一趟, 就地注入"
                      % (k + 1, hit.where(), _hexb(gcur)))
                bp_y = ctx.bp(BP_YEAR)          # ⚠ 趁停着下(`resume` 之后 gdb 拒插断点)
                writes = _write(g, cmd_bank.BFY_YEAR_ASSIGN)
                print("   注入 %d 处: %s" % (len(writes), "; ".join("%s=%s" % (e, v) for e, v, _o in writes)))
                g.go(settle=False)              # 放行; 年支写点只差下一条指令
                hit_y, vals_y = _stop_at(g, bp_y, INJ_WAIT, VARS_YEAR, "4-7 年支写点")
                hit3, vals3 = hit_y, vals_y or {}
                inj3 = breakpoint.record(
                    "4-7 年支写点(跨年那一趟, 已注入年结算日期表)",
                    {"hit": hit_y, "vars": vals_y}, crit="③",
                    falsify=cmd_bank.BFY_FALSIFY["③"], trig=judge.TRIG_INJECT,
                    detail=("停在 %s" % hit_y.where()) if hit_y is not None
                           else "跨年那一趟没走到年支写点(等满 %.0fs)" % INJ_WAIT)
                inj3["injects"] = writes
                g.go()                          # 放行, 让那一条记录落库
                break
            else:
                print("   !! 等了 %d 个自然分钟步进都没等到跨年那一趟" % CROSS_TRIES)

    # ⚠ 值从 `vals3` 取, **不从 `inj3` 取** —— `breakpoint.record()` 返回的是账本记录
    #   (`judge.rec` 形状), 它只有名字/三态/理由, **不带 `vars`**; 照它取会一律取到 None。
    fn3 = cmd_bank.st_int(vals3.get("frezNum"))
    if inj3 is None:
        _add(J, L3, None, "跨年那一趟没停到(见上面那几行) ⇒ 边界没造出来, 这一条没做成",
             crit="③", obs=judge.DEBUG)
    elif hit3 is None:
        # 停到了跨年那一趟、也注了有效的年结算日期表, 而年支写点**没**命中 ⇒ 年份差算出来了
        # 却没进年支(或进了却没写)。这一趟的边界是我们自己造的, 不是台面够不到 —— 记 FAIL。
        _add(J, L3, False,
             "跨年那一趟停在 %s, 注入的也是有效年表, 但年支写点没命中(等满 %.0fs) ⇒ "
             "跨了年却不落记录" % (inj3.get("detail") or "?", INJ_WAIT),
             crit="③", obs=judge.DEBUG)
    elif fn3 is None:
        _add(J, L3, None, "停在年支写点了, 但 `frezNum` 读不出来 ⇒ 这一刻的值判不了",
             crit="③", obs=judge.DEBUG)
    else:
        _add(J, L3, fn3 == 1,
             "停在年支写点(%s), 那刻 frezNum=%s, buff=%s ⇒ 年支写点%s"
             % (inj3.get("detail") or "?", fn3, _hexb(cmd_bank.gdb_bytes(vals3.get("buff"))),
                "被走到, 且只写 1 条" if fn3 == 1 else "被走到了, 但条数不是 1"),
             crit="③", obs=judge.DEBUG)

    # ---- ④ 黑盒: 跨年那一趟之后记录**恰加 1**, 且时标是跨过的那个结算点 ----
    post3 = cmd_bank.read_freeze_row(ser, SUBCLASS, 1, wait=WAIT, empty_ok=True)
    # ⚠ 一个布尔("推进了吗")分不出"恰加 1"与"一次结转了好几条" —— 必须比序号差。
    d3 = cmd_bank.rec_seq_delta(mid, post3)
    ok4 = None if d3 is None else (d3 == 1)
    premise3 = (hit3 is not None)      # 前提 = 跨年那一趟真写了一条, 不是"③ 判过了"
    _add(J, L4,
         cmd_bank.bfy_ts_match(cmd_bank.bfy_pos_ok(ok4, premise3), (post3 or {}).get("ts"), exp3_ts),
         ("%s → %s; 序号差 %s(期望恰加 1), 期望时标 %s"
          % (cmd_bank.rec_row_txt(mid), cmd_bank.rec_row_txt(post3), d3, exp3_ts)
          if premise3 else
          "③ 那一次没走到年支写点 ⇒ 这一趟固件**本就不该生成**, 读数 %s → %s 既不能支持也不能否定"
          "本条 ⇒ 记未证, 不记失败" % (cmd_bank.rec_row_txt(mid), cmd_bank.rec_row_txt(post3))),
         crit="④")

    # ---- ⑤ 月支: 拨回真实时间, 在同一个停点把 buff 写成月结算日期表 + g_HisTime 月份回退两格 ----
    # ⚠ 月支那一支的 `frezNum` 按 `Diff_Months(g_CurTime, g_HisTime)` 算, 所以边界靠**回退月份**造,
    #   与年支那趟无关; 表第 0 组的"月"留 99 ⇒ 落月形态(`flag=FALSE`)、走月支写点。
    # ⚠ 回退**两格**(不是一格): 差 1 时固件算出来的条数就是 1 —— "条数按月份差算"这件事测不出来,
    #   固定写一条的固件也过。差 2 才逼出"同一趟连写两条", 两条各自的时标于是也成了判据。
    inj5 = None
    hit5w = None         # 月支写点第一次命中(⑥ 的前提 = "这一趟真写了两条记录")
    exp5_ts = None
    if have_wb:
        now1 = cmd_bank.read_clock(ser, chip="计量芯", wait=WAIT, quiet=True)
        real = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print("\n[拨钟] 拨回真实时间: 表钟=%s → 目标=%s" % (now1, real))
        cmd_bank.set_meter_clock_set(ser, real, chip="计量芯", wait=WAIT)
        hit5, vals5 = _stop_at(g, BP_INJ, GATE_WAIT, VARS_INJ, "4-7 月支那一趟的自然分钟步进")
        if hit5 is None:
            _add(J, L5, None, "月支那一趟没停到注入点(等满 %.0fs) ⇒ 边界没造出来, 这一条没做成"
                 % GATE_WAIT, crit="⑤", obs=judge.DEBUG)
        else:
            gcur5 = cmd_bank.gdb_bytes(vals5.get("g_CurTime"))
            ymd = cmd_bank.bfy_ymd(gcur5)
            if ymd is None:
                _add(J, L5, None, "那一刻 g_CurTime 读不出来(%s) ⇒ 期望的月结算点算不出"
                     % _hexb(gcur5), crit="⑤", obs=judge.DEBUG)
            else:
                # 两条记录的期望时标(照 `:1020-1048` 那六行算, 不从记录里反读):
                # `diffMon = frezNum - dateStr` = 2 − 1 = 1 ⇒ 第一次写的月 = 表钟当月 − 1(跨年借位),
                # 循环里 `++buff[4]` 之后第二次写的才是**当月**。所以期望的时标 = 当月 1 日 0 点。
                prev = cmd_bank.bfy_months_back(gcur5, 1)
                exp_buff1 = (0, 0, 0, 1, prev[0], prev[1])
                exp_buff2 = (0, 0, 0, 1, gcur5[4], gcur5[5])
                exp5_ts = cmd_bank.frez_expect_ts(exp_buff2)
                print("   停在 %s, g_CurTime=%s ⇒ 期望两条 = %s(上一个月) 与 %s(当月, 表钟在 "
                      "%d 年 %d 月)" % (hit5.where(), _hexb(gcur5), exp5_ts,
                                      cmd_bank.frez_expect_ts(exp_buff1), ymd[5], ymd[4]))
                bp_m = ctx.bp(BP_MONTH)         # ⚠ 趁停着下
                writes5 = _write(g, cmd_bank.bfy_month_assign(gcur5))
                print("   注入 %d 处: %s" % (len(writes5), "; ".join("%s=%s" % (e, v) for e, v, _o in writes5)))
                g.go(settle=False)              # 放行: 写点只差这一趟剩下的那几条指令
                # ⚠ 这一趟写**两条**, 所以同一个断点要连停两次 —— 不能用 `_stop_at`(它 `drop=True`,
                #   第一次命中就把断点撤了, 第二次永远等不到, 而现象与"固件只写一条"一模一样)。
                hit_m1, vals_m1 = g.wait_break(bp_m, INJ_WAIT, vars=VARS_MONTH, drop=False)
                hit_m2, vals_m2 = None, {}
                if hit_m1 is not None:
                    g.go(settle=False)          # 走循环: `++buff[4]` 之后紧接着第二次写
                    hit_m2, vals_m2 = g.wait_break(bp_m, INJ_WAIT, vars=VARS_MONTH, drop=True)
                # ⚠ 两种收尾不一样: 第二次命中那一路 `drop=True` 已把断点撤走, `_disarm` 当场返回、
                #   核还停着 ⇒ 必须显式放行, 否则两条记录只落前一条, 现象与"固件只写一条"一样;
                #   超时那一路断点还挂着 ⇒ `_disarm` 自己"叫停→撤→放行"(见它的 ⚠)。
                if hit_m2 is not None:
                    g._disarm(bp_m)
                    g.go()                      # 放行: 让两条记录都落库
                else:
                    g._disarm(bp_m)
                hit5w = hit_m1
                inj5 = breakpoint.record(
                    "4-7 月支写点(拨回真实时间那一趟, 已注入月结算日期表 + g_HisTime 月份回退两格)",
                    {"hit": hit_m1, "vars": vals_m1}, crit="⑤",
                    falsify=cmd_bank.BFY_FALSIFY["⑤"], trig=judge.TRIG_INJECT,
                    detail=("第一次停在 %s" % hit_m1.where()) if hit_m1 is not None
                           else "那一趟没走到月支写点(等满 %.0fs)" % INJ_WAIT)
                inj5["injects"] = writes5
                e1 = tuple(cmd_bank.gdb_bytes((vals_m1 or {}).get("buff"))[:6])
                e2 = tuple(cmd_bank.gdb_bytes((vals_m2 or {}).get("buff"))[:6])
                f1 = cmd_bank.st_int((vals_m1 or {}).get("frezNum"))
                f2 = cmd_bank.st_int((vals_m2 or {}).get("frezNum"))
                if hit_m1 is None:
                    ok5 = False
                    why5 = ("那一趟没走到月支写点(等满 %.0fs) ⇒ 跨月边界造出来了却没落到月支"
                            % INJ_WAIT)
                elif hit_m2 is None:
                    ok5 = False
                    why5 = ("月支写点只停了一次(%s), 第二次没等到(等满 %.0fs) ⇒ 这一趟该写两条"
                            % (hit_m1.where(), INJ_WAIT))
                elif len(e1) < 6 or len(e2) < 6 or f1 is None or f2 is None:
                    ok5 = None
                    why5 = "停了两次, 但 `frezNum`/`buff` 读不出来 ⇒ 这两刻的值判不了"
                else:
                    ok5 = (f1 == 2 and f2 == 2 and e1 == exp_buff1 and e2 == exp_buff2)
                    why5 = ("两次都停在月支写点(%s / %s), 那刻 frezNum=%s / %s, buff[:6]=%s / %s; "
                            "期望 %s / %s ⇒ %s"
                            % (hit_m1.where(), hit_m2.where(), f1, f2, _hexb(e1), _hexb(e2),
                               _hexb(exp_buff1), _hexb(exp_buff2),
                               "条数与两条的结算点都对" if ok5 else "**与期望不符**"))
                _add(J, L5, ok5, why5, crit="⑤", obs=judge.DEBUG)

    # ---- ⑥ 黑盒: 月支那一趟之后记录**推进两格**, 最新一条的时标就是当月的结算点 ----
    # ⚠ 这一趟写两条 ⇒ 一个布尔("推进了吗")分不出"写了一条"与"写了两条", 必须比序号差。
    post5 = cmd_bank.read_freeze_row(ser, SUBCLASS, 1, wait=WAIT, empty_ok=True)
    delta5 = cmd_bank.rec_seq_delta(post3, post5)
    premise5 = (hit5w is not None)     # 前提 = 月支写点真停到了, 不是"⑤ 判过了"
    if not premise5:
        _add(J, L6, None,
             "⑤ 那一次没走到月支写点 ⇒ 这一趟固件**本就不该生成**, 读数 %s → %s 既不能支持也不能"
             "否定本条 ⇒ 记未证, 不记失败" % (cmd_bank.rec_row_txt(post3), cmd_bank.rec_row_txt(post5)),
             crit="⑥")
    elif delta5 is None:
        _add(J, L6, None,
             "两次读数比不出序号差(%s → %s) ⇒ 推进了几格判不了"
             % (cmd_bank.rec_row_txt(post3), cmd_bank.rec_row_txt(post5)), crit="⑥")
    else:
        _add(J, L6,
             cmd_bank.bfy_ts_match(delta5 == 2, (post5 or {}).get("ts"), exp5_ts),
             "%s → %s; 序号差 %s(期望 2), 期望时标 %s"
             % (cmd_bank.rec_row_txt(post3), cmd_bank.rec_row_txt(post5), delta5, exp5_ts),
             crit="⑥")

    # ---- ⑨ 容量环回: 再跨 RING_EXTRA 次月结算边界(每轮落 RING_PER_ROUND 条) ⇒ 6 格装不下, 最早
    # 那几条被顶掉 ----
    # 判据是「写满不停写」的反面: 序号仍按每轮 RING_PER_ROUND 推进(不是写满就不写了)、第 7 格读不到
    # (不是往后加格)、最早那几条换人了(顶掉而不是原地不动)。三条一起看才分得出环回与"写到满就停"。
    ring_before = [cmd_bank.read_freeze_row(ser, SUBCLASS, p, wait=WAIT, empty_ok=True)
                   for p in range(1, POS_MAX + 1)]
    pre_seq = [r.get("seq") for r in ring_before if r and r.get("seq") is not None]
    # 第 1 轮落满之后被顶掉的条数 = 原先占着的格数 + 这一轮落的 − 格数(没满格时一条都不掉)。
    n_evict = max(0, len(pre_seq) + RING_PER_ROUND - POS_MAX)
    evict_ok = None
    rounds, r_why = [], []
    if have_wb:
        for k in range(RING_EXTRA):
            hits, why = _month_round(ctx, "⑨ 第 %d 轮环回" % (k + 1))
            rounds.append(hits)
            r_why.append(why)
            print("   [环回 %d/%d] %s" % (k + 1, RING_EXTRA, why))
            if hits is None:
                break
            if k == 0:
                # 就地回读: 最早那 n_evict 格该换人(序号前进), 而不是原地不动 —— 环回的正面证据。
                ring_mid = [cmd_bank.read_freeze_row(ser, SUBCLASS, p, wait=WAIT, empty_ok=True)
                            for p in range(1, POS_MAX + 1)]
                d_evict = [cmd_bank.rec_seq_delta(ring_before[i], ring_mid[i])
                           for i in range(n_evict)]
                evict_ok = None if any(d is None for d in d_evict) else all(d > 0 for d in d_evict)
    ring_after = [cmd_bank.read_freeze_row(ser, SUBCLASS, p, wait=WAIT, empty_ok=True)
                  for p in range(1, POS_MAX + 1)]
    pos_over = cmd_bank.read_freeze_row(ser, SUBCLASS, POS_MAX + 1, wait=WAIT, empty_ok=True)

    # ---- ⑧ 容量: 深度 == 6(AA80) + 698 读回 pos 1..6 各有记录、pos 7 读不到 ----
    ok_dp, why_dp = cmd_bank.frez_store_depth_evidence(
        store_y, CURRENT.BILLFREZY_INDEX, want_depth=CURRENT.BILLFREZY_DEPTH, tag="4-7 阶梯结算")
    # ⚠ "该格没有记录"的判据取**序号读不读得到** —— 空数组与"被拒"这两种回法都算"没有",
    #   而"一个字都没回"(`answered=False`)不算(那是没读到, 与本条无关)。
    filled = [r for r in ring_after if r and r.get("answered") is not False and r.get("seq") is not None]
    over_ok = bool(pos_over) and pos_over.get("answered") is not False and pos_over.get("seq") is None
    ok8 = None if (ok_dp is None or len(ring_after) < POS_MAX) else (
        ok_dp and len(filled) == POS_MAX and over_ok)
    _add(J, "⑧ 阶梯结算冻结的存储深度 == %d, 且 698 读回 pos 1..%d 各有记录、pos %d 读不到"
         % (CURRENT.BILLFREZY_DEPTH, POS_MAX, POS_MAX + 1), ok8,
         "%s; 读回 pos 1..%d = %s; pos %d = %s(该格按设计不存在)"
         % (why_dp, POS_MAX, " | ".join(cmd_bank.rec_row_txt(r) for r in ring_after),
            POS_MAX + 1, cmd_bank.rec_row_txt(pos_over)),
         crit="⑧", obs=judge.DEBUG)

    # ---- ⑨ 环回: 序号仍按每轮 RING_PER_ROUND 推进 + 最早那几条被顶掉 + 第 7 格仍读不到 ----
    d_new = cmd_bank.rec_seq_delta(ring_before[0], ring_after[0])   # 最新那格该推进 RING_PER_ROUND×轮数
    seqs = [r.get("seq") for r in ring_after]
    desc = (all(s is not None for s in seqs)
            and all(seqs[i] > seqs[i + 1] for i in range(len(seqs) - 1)))
    ok9 = None if (None in rounds or d_new is None or evict_ok is None) else (
        all(h == RING_PER_ROUND for h in rounds)
        and d_new == RING_PER_ROUND * RING_EXTRA and evict_ok and desc and over_ok)
    _add(J, "⑨ 写满 %d 格之后环回顶掉最早那条(不是停止写): 再跨 %d 次月结算边界落 %d 条之后, "
            "记录区最早那 %d 条被顶掉, 且格数仍是 %d、序号单调递增不回退"
         % (POS_MAX, RING_EXTRA, RING_PER_ROUND * RING_EXTRA, n_evict, POS_MAX), ok9,
         ("每轮 %s; 最新那格 %s → %s(序号差 %s, 期望 %d); 第 1 轮后最早 %d 条顶掉=%s; "
          "pos1..%d 的序号 %s(%s); pos %d %s")
         % ("/".join("写%d条" % h if h else "没做成" for h in rounds),
            cmd_bank.rec_row_txt(ring_before[0]), cmd_bank.rec_row_txt(ring_after[0]),
            d_new, RING_PER_ROUND * RING_EXTRA,
            n_evict, {True: "是", False: "**否(原地不动)**", None: "没做成"}[evict_ok],
            POS_MAX, seqs, "降序不回退" if desc else "**不是降序/有读不到**",
            POS_MAX + 1, "读不到(对)" if over_ok else "**读得到(记录区多长出了一格)**"),
         crit="⑨", obs=judge.DEBUG)


def _banner():
    return ("== 4-7 阶梯结算冻结 | 工程=%s 表号=%s ==\n"
            ".. 驱动=自然分钟步进(不发帧); 白盒四个停点 %s(风格判定)/%s(注入停点)/%s(月支写点)/%s(年支写点); "
            "年支边界靠**拨钟到 12 月 31 日 23:59:30 等它自然跨年**造(那一停写 %d 处 buff), "
            "月支边界靠停 %s 注入造(写 %d 处: g_HisTime 月份回退 %d 格 + buff), 参数区一个字节都不动; "
            "⚠ 跑完表钟停在真实时间, 收尾走 scripts/_restore_all.py"
            % (CURRENT.PROJECT, CURRENT.TABLE_ADDR.hex().upper(),
               breakpoint.text(BP_GATE), breakpoint.text(BP_INJ),
               breakpoint.text(BP_MONTH), breakpoint.text(BP_YEAR),
               len(cmd_bank.BFY_YEAR_ASSIGN), breakpoint.text(BP_INJ),
               len(cmd_bank.BFY_MONTH_BUFF_ASSIGN) + len(cmd_bank.BFY_HIS_FIELDS),
               RING_PER_ROUND))


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "4-7 阶梯结算冻结(风格判定放行/无边界不产生/拨钟自然跨年生成并结转/注入造月支边界生成/698 读回)",
        cmd_bank.billfrez_y_criteria,
        name="4_7_billfrez_y",
        parts=[("4-7 阶梯结算冻结段", part_billfrez_y)],
        gdb=breakpoint, banner=_banner(),
        # ⚠ `inject_allow` 必须在**会话构造时**点名 —— 白名单精确匹配符号名, 事后补不了。
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV,
                        inject_allow=cmd_bank.bfy_step_inject_allow())))
