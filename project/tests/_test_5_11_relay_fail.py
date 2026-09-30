# -*- coding: utf-8 -*-
"""在证什么: 命令与实测不符 ⇒ 走 Recd_RelayFail 的「记录开始」支落一笔失败事件(序号推进 1、新行结束时刻为空),
   恢复一致 ⇒ 走「记录结束」支把尾巴补进上一行(序号不推进), 两笔时标 == 当时表钟,
   写库那一刻 buff[12] 与当时的开关状态量同向(④a; 「0/1 与合/分同向」那半本台证不了, 见判据表 ④b), 一致时写库调用点 :521 不被走到(不误记),
   且 :521 读回的 `stat` 与 :501-514 那条现算规则一致。
   另记两样不进判据表的: g_RelayFlg 左移攒到几拍(第 1 步)与一拍几秒 ⇒ 4 拍与规格「持续 5s 以上」
   的差(第 2 步); 连造 11 回看容量封顶与最早那条被顶掉(第 6 步)。
会向表写什么: 只发 645 进厂内(记录读回受 Chk_SafeMode 管, 厂外每条都被 DAR=20 打回)。
  **没有一个帧造得出状态** —— 本台交流 0V ⇒ g_RelayFlg 恒分闸、75%Un 窗口常闭, 「命令≠实测」只能靠注入:
  停 断[A] 把 g_CompFlg[2]/[3] 的**低 4 位**改掉(写库判定开、5 位比对判定仍关, 不会真的动继电器),
  注入的只是 g_RelayFlg 的副本内容, 记录的时刻与电量快照仍是固件现读的; 参数区一个字节不动。
  产物就是那几笔负荷开关误动作记录; 台面收尾走 `python scripts/_restore_all.py`。
跑法: `python project/tests/_test_5_11_relay_fail.py`(真串口 + 真探针; 断点与注入都要会话)。
结论怎么读: 账本末行「判据: 满足 n/N」, N=6; 退 0 = 通过 / 1 = 失败 / 2 = 未定论。
"""
import time

from common import judge              # 观测种类常量(黑盒标 SERIAL, 白盒那几条标 DEBUG)
from common import trial              # 运行外壳: argv/日志/账本/串口/会话/收尾三连/退出码
from meterlib import cmd_bank         # 帧目录 + 语义动词积木箱(每步自带打印/判 PASS·TBD·FAIL)
from project import CURRENT           # 本工程画像: 表号/.out 路径/喂狗点(库不认表, 只有脚本认)
from swdbg import breakpoint          # 断点级白盒(停核 + 注入 + 读函数内局部量); 没接 J-Link → None
from swdbg import gdbinit            # 整片 .out 地图: 开会话后解一次, 之后断点/注入点只查它

# ---- 子项参数(纯数据; 要改的在这里改) ----
WAIT = 3.0                     # 记录读回 / 读表钟的单次等待
NEG_WINDOW = 4.0               # ⑤ 否定期望的窗口(秒): 一致的那 N 秒里写库调用点不许被走到
SETTLE_TIMEOUT = 12.0          # 归位两段的收敛等待(基线的悬空行补完 / temp 归一)
HIT_TIMEOUT = 60.0             # 三次等写库位置的等待
RF_CAP = CURRENT.RELAY_REC_CAP  # 负荷开关误动作记录容量(条) —— 画像单一事实源(RecdData.h:142)
RF_BURST = RF_CAP + 1          # 第 6 步: 容量 10 条 + 1
PASS_N = 5                     # 第 1、2 步: 连读 g_RelayFlg 的拍数(低 4 位全同判 ⇒ 4 拍 + 1 拍余量)
PASS_WAIT = 4.0                # 第 1、2 步: 等下一拍的上限(拍间隔约 1 秒)

# ---- 断点: 模块级**字面量元组** ----
# `scripts/_check_anchors.py` 用 `ast.literal_eval` 抠 `BP_<X>`/`VARS_<X>` 这两个名字逐条对源码核
# (变量在断点那一行赋过值没有); 写成 `CB.SOMETHING` 那种表达式就抠不出来, 等于没人核过。
# ⚠ 库内 `5-3` 的 `LP_BP_*` 就是反例(断点住在库里 ⇒ 那个扫描器看不见) —— 本项**一个断点都不住库里**。
BP_JUDGE = ("prev", "Run_TaskRelay", "Get_CompFlag", 4)   # 写库判定那一句 @TaskRelay.c:498(**注入停靠点**)
VARS_JUDGE = ("g_RelayCmd[0]", "g_RelayFlg", "g_CompFlg[2]", "g_CompFlg[3]",
              "g_FailStat[0]", "temp")
# ⚠ 上面六个量都给的是**注入前**的读数, 用来证明"这一次真把值改掉了"(只看 injects 的话,
#   "注入生效"与"恰好注在原值上"长得一样)。**不许**把 `stat` 加进来 —— `:498` 那一刻它还没活。
BP_CALL = ("prev", "Run_TaskRelay", "Recd_RelayFail", 2)   # Recd_RelayFail 调用点 @TaskRelay.c:521
VARS_CALL = ("stat", "g_RelayCmd[0]", "g_RelayFlg")
# ⚠ 判据⑥ 要拿 `stat` 与"命令 + g_RelayFlg 低 4 位"现算的值对拍, 所以这两样必须一起读。
BP_WRS = ("prev", "Recd_RelayFail", "Write_RecdData", 1)   # 发生支**落库那一句**的前一条(checker 报落点 = TaskRecord.c:1330)
VARS_WRS = ("buff", "g_RelaySta")
# ⚠ 判据④ 要的就是"写库那一刻 buff[12] 按 g_RelaySta 现写" ⇒ 这两样必须同一次读回。
# ⚠ 落点选在**落库那一句**的 prev, 不选 `Read_CurkWh#1`: 走到这里 `:1328` 的 buff[12] 与
#   `:1330/:1331` 的 buff[13..22] 都已经写好 ⇒ 第 4 步要读的字段一次读到。
#   落在 `Read_CurkWh#1` 上则 buff[13..22] 还没填, 那一段只能空着。
#   (`buff[23..32]` 是 `:1329` 清零的, 结束支才由 `:1348/:1349` 填 —— 发生支这一停读到的就是 0。)
BP_WRE = ("prev", "Recd_RelayFail", "Read_CurkWh", 3)   # 恢复支写库语句首行 @TaskRecord.c:1348(真写调用 @0x1ecea ⇒ 同上)
VARS_WRE = ("buff",)
# ⚠ `buff` 是 `INT8U` 数组 —— gdb 按**字符串字面量**印(`"\001\002…"`), 不是 `{1,2,3}`。
#   库里的 `gdb_bytes` 就是为这个单列的(拿 `gdb_ints` 抠会把八进制当十进制、把可打印字符整个丢掉)。
# ⚠ `end` 在这两个写库位置的 PC 区间里没有位置(死了)⇒ 两个 VARS 里都不许带它。

# ---- 半途中止时要一次记全的条目(纯数据; 与正常路径同名同号**同观测**, 少记一条 = 分母变小)----
ENTRIES_ALL = (
    ("①", "命令与实测**不符** ⇒ 走到「记录开始」写库语句首行(TaskRecord.c:1330)", judge.DEBUG),
    ("①", "串口 该口序号推进 1、新行结束时刻为空(落库与否只认这一条)", judge.SERIAL),
    ("②", "命令与实测**一致** ⇒ 走到「记录结束」写库语句首行(TaskRecord.c:1348)", judge.DEBUG),
    ("②", "串口 结束时刻补进上一行、序号不推进(结束不新开行)", judge.SERIAL),
    ("③", "两笔时标 == 当时表钟(开始 @buff[0..5] / 结束 @buff[6..11])", judge.SERIAL),
    ("④a", "写库那一刻 buff[12] 与当时的开关状态量同向", judge.DEBUG),
    ("⑤", "命令与实测一致时写库调用点(TaskRelay.c:521)不被走到, 记录一条都不新增", judge.DEBUG),
    ("⑥", "在 :521 读回的 `stat` == 按「命令 + g_RelayFlg 低 4 位」现算的值", judge.DEBUG),
    ("⑦", "连造 11 回: 条数封顶在容量 10 上、最早那条被顶掉", judge.SERIAL),
)


def _add(J, label, ok, why, crit, obs=judge.SERIAL, falsify=None, trig=None):
    """记一条判据 + 打一行三态(`falsify` 逐条给 —— 同一个 crit 的两个通道答的假条件不同)。"""
    J.add(label, ok, why, crit=crit, obs=obs, falsify=falsify, trig=trig)
    print("   [%s] %s —— %s" % ({True: "PASS", False: "FAIL", None: "TBD"}[ok], label, why))


def _seg(bv, a, b):
    """`buff` 的第 [a, b) 段 → 一行十六进制文本; 段不满 → 写"没读到"(不拿空串冒充)。"""
    return " ".join("%02X" % x for x in bv[a:b]) if len(bv) >= b else "没读到"


def _tri(halves):
    """几半支合成一条的三态: 有 False ⇒ False; 否则有 None ⇒ None; 全 True ⇒ True。"""
    if any(x is False for x in halves):
        return False
    if any(x is None for x in halves):
        return None
    return True


def _shift_run(flg):
    """`g_RelayFlg` 低 4 位的**从 bit0 起连续相同位数** = 这一停是左移攒下的第几拍。

    第 1 步要的"第几拍"就是这个: 固件按低 4 位全同才判(`RFL_FLG_MASK` = 0x0F), 每秒左移一位
    ⇒ 位数攒到 4 才是"攒满 4 拍", 那时才落记录。读不出 → `None`(不猜)。
    """
    if flg is None:
        return None
    for k in range(1, 5):
        if ((flg >> (k - 1)) & 1) != (flg & 1):
            return k - 1
    return 4


def _rfl_rec_ts(b6):
    """记录体里那 6 字节时刻(秒分时日月年) → 钟串; 空时刻与无效时刻都是 `None`。"""
    if not b6 or len(b6) < 6:
        return None
    s, mi, h, d, mo, y2 = b6[:6]
    if not (y2 or mo or d or h or mi or s):
        return None
    if mo == 0xFF or d == 0xFF:
        return None
    return "%04d-%02d-%02d %02d:%02d:%02d" % (2000 + y2, mo, d, h, mi, s)


def _stop_unproven(ctx, why):
    """半途中止的统一收场: 条目一次记全(`ok=None`) → 出声 → 停住(退 2, 不是失败)。"""
    ctx.J.extend(cmd_bank.unproven_records(ENTRIES_ALL, why))
    ctx.J.note("5-11 负荷开关误动作段: 半途中止(这一次没做成) —— %s" % why)
    raise trial.Stop("%s ⇒ 未定论(退 2), 不是失败" % why)


def part_relayfail(ctx):
    """一段 = 5-11 的六步。

    段内注释按 md 步号分节: 基线 → 第 1、2 步(连读拍数, 不下注入、不发帧) → 归位两段(把台面收成
    『无悬空行』) → ⑤ 一致时的否定期望 → ⑥ 不符时 :521 的现算 stat → ①③ 第一笔「记录开始」
    → ②③ 一致时「记录结束」补尾巴 → ①④ 第二笔「记录开始」读 buff 五段 → ② 末段一致
    → 第 5 步(698 读回与停点记录体对拍) → 第 6 步(连造看封顶)。

    ⚠ **顺序是承重的**(有头无尾守卫 TaskRecord.c:1303-1317): 必须 不符 → 一致 → 不符 → 一致,
      且末段停在「记录结束」。反序的话第二笔会被守卫静默吃掉。
      第 6 步那几回同理 —— 每回都是「不符 → 一致」一对, 少一半就被守卫挡回, 一回只推进一行。
    ⚠ 观测一律 attach(**禁用 launch**: launch 会复位表、RAM 态清零); 见 CLAUDE.md 调试链纪律 1。
    ⚠ 递**断点元组**而不是已挂好的 bpno: `inject_hit`/`inject_miss` 见到元组会自己挂、
      命中与没命中**两条路都撤**。传 bpno 则撤不撤只由调用方管, 万一没命中就留在槽里
      —— 那是"核被自己撂停、其后串口全哑"的来源。
    """
    J, ser = ctx.J, ctx.ser
    print("\n===== 5-11 负荷开关误动作: 归位 → 一致(否定) → 不符 → 一致 → 不符 → 末段一致 =====")

    # ---- 开调试会话(断点观测)。台面没接 J-Link / 用户指定只做黑盒 ⇒ None, 白盒那几条记"没做成" ----
    ctx.session()
    if ctx.g is not None:
        # 整片 .out 解一次(停核遍历, 内部喂狗, 收尾放行) —— 之后一律查这张图
        gdbinit.build(ctx.g)
    g = ctx.g
    have_wb = g is not None
    if not have_wb:
        if ctx.waived:
            print("   -- 本次**用户指定**只做黑盒(串口观测) ⇒ 断点观测(白盒)按指定不做"
                  "(J 列须记明本次范围)")
        else:
            print("   !! 本次无调试会话 → 断点观测(白盒)整体**未做**; 本次范围 = 仅黑盒"
                  "(对外行为可判, 内部现算规则 / 两条写库路径 / 开关状态位未取证 —— "
                  "见 CLAUDE.md「凭什么算「真实测试」·两种观测」)")

    # ---- 前置: 进厂内 —— **读记录同样受 Chk_SafeMode 管**(见文件头) ----
    cmd_bank.enter_factory(ser)

    # ---- 基线: 表钟 + 负荷开关误动作记录区最新一条 ----
    t0 = cmd_bank.read_clock(ser, chip="管理芯", wait=WAIT, quiet=True)
    rows0 = cmd_bank.read_relayfail_rows(ser, (1, 2, 3), wait=WAIT)
    if rows0.get(1) is None:
        # 空表与"读不成"必须分清: 空表是**正常起点**, 不是中止理由(见 cmd_bank.relayfail_list_state)。
        _lst0 = cmd_bank.relayfail_list_state(ser, wait=WAIT)
        if _lst0 is None:
            _stop_unproven(ctx, "表钟或负荷开关误动作记录读**不成**(表钟=%s; 最新一条的读回既不是"
                                "「有行」也不是「空表应答」)⇒ 基线无对照" % t0)
    by0 = cmd_bank.rows_by_seq(rows0)
    seq0 = cmd_bank.rfl_top(by0)
    _sh0 = ("记录区当前为空" if rows0.get(1) is None
            else "序号=%s 发生=%s 结束=%s" % (seq0, rows0[1]["t_start"] or "-",
                                             rows0[1]["t_end"] or "(未结束)"))
    print("   基线: 表钟=%s | %s" % (t0, _sh0))
    _add(J, "参考: 基线形态(记录区空 / 最新一条是否有头无尾 —— 归位那两段在任何形态下都收敛)",
         None, _sh0, crit=None)

    # ---- 第 1、2 步: 数 g_RelayFlg 左移的拍数与节拍 ----
    # 第 1 步要的"这是左移的第几拍"= 低 4 位的连续相同位数(`_shift_run`)。第 2 步要的是把拍数
    #   与规格的「持续 5s 以上」对上: 固件按低 4 位全同才判 ⇒ 攒满要 4 拍, 一拍几秒要看实测。
    # ⚠ 这一段**不下任何注入、不发任何帧**: 只连等同一个断点若干拍, 每拍读一次寄存器。
    #   所以它证的是"稳态下这个寄存器长什么样、几秒走一拍", 不是"它在跳变时会怎么填" ——
    #   本台交流 0V, 开关状态恒定, 寄存器在稳态下本来就是满的, 帧通道造不出跳变。
    _pass = []
    if have_wb:
        _tpre = cmd_bank.read_clock(ser, chip="管理芯", wait=WAIT, quiet=True)
        print("   第 1、2 步: 连等 %d 拍, 每拍读 g_RelayFlg / g_RelayCmd[0] 并记拍间隔" % PASS_N)
        for k in range(PASS_N):
            _k0 = time.monotonic()
            r = breakpoint.wait_hit(g, BP_JUDGE, PASS_WAIT, vars=VARS_JUDGE,
                                    label="断[R] 第 1 步 第 %d/%d 拍" % (k + 1, PASS_N))
            _dt = time.monotonic() - _k0
            _pv = (r or {}).get("vars") or {}
            _fv = cmd_bank.gdb_ints(_pv.get("g_RelayFlg"))
            _cv = cmd_bank.gdb_ints(_pv.get("g_RelayCmd[0]"))
            _pass.append({"ok": (r or {}).get("ok"), "flg": _fv[0] if _fv else None,
                          "cmd": _cv[0] if _cv else None, "gap": _dt})
            print("      第 %d 拍: g_RelayFlg=%s g_RelayCmd[0]=%s ⇒ 左移第 %s 拍; 与上一拍隔 %.2fs"
                  % (k + 1, _pv.get("g_RelayFlg") or "读不到", _pv.get("g_RelayCmd[0]") or "读不到",
                     _shift_run(_fv[0] if _fv else None), _dt))
        _tpost = cmd_bank.read_clock(ser, chip="管理芯", wait=WAIT, quiet=True)
    else:
        _tpre = _tpost = None

    _flgs = [p["flg"] for p in _pass]
    _runs = [_shift_run(f) for f in _flgs]
    _gaps = [round(p["gap"], 2) for p in _pass[1:]]
    _tap = None
    if _gaps and all(0.6 <= g <= 2.5 for g in _gaps):
        _tap = 1.0                     # 每拍落在 [0.6, 2.5] 秒 ⇒ 判它是一秒一拍(停核开销与任务抖动)
    _span = None
    if _tpre and _tpost:
        _a, _b = cmd_bank.clock_dt(_tpre), cmd_bank.clock_dt(_tpost)
        if _a and _b:
            _span = int((_b - _a).total_seconds())
    _add(J, "第 1 步 g_RelayFlg 左移的拍数(连读 %d 拍, 每拍读 g_RelayFlg 与 g_RelayCmd[0])" % PASS_N,
         None if (not have_wb or any(f is None for f in _flgs)) else True,
         "逐拍 g_RelayFlg=%s ⇒ 各是左移第 %s 拍; 命令 g_RelayCmd[0]=%s; 读回时刻 %s → %s(共 %s 秒)"
         % (_flgs or "没读到", _runs or "算不出", [p["cmd"] for p in _pass] or "没读到",
            _tpre or "读不出", _tpost or "读不出", _span if _span is not None else "算不出"),
         crit=None, obs=judge.DEBUG,
         falsify="低 4 位全同(RFL_FLG_MASK)不是 :501-514 那四条现算规则用的那个窗口 "
                 "⇒ 拍数与固件的判定口径对不上")
    _add(J, "第 2 步 规格「持续 5s 以上」与固件的「低 4 位全同」拍数对不上",
         None if _tap is None else True,
         "固件按低 4 位全同才判 ⇒ 攒满要 4 拍; 实测相邻两拍的间隔=%s 秒, 连读 %d 拍期间表钟走了 "
         "%s 秒 ⇒ 一拍约 %s 秒, 4 拍 = %s 秒, 比规格的 5 秒少 %s 秒。⚠ 这一条**不在判据表里**"
         "(md 第 2 步明写), 只进日志"
         % (_gaps or "读不出", PASS_N, _span if _span is not None else "算不出",
            _tap if _tap else "量不出", (4 * _tap) if _tap else "算不出",
            (5 - 4 * _tap) if _tap else "算不出"),
         crit=None, obs=judge.DEBUG,
         falsify="拍间隔不是 1 秒(相邻两拍隔 != 1 秒)⇒ 『4 拍 = 4 秒』这个算式与固件不符, "
                 "该以实测拍间隔为准")
    if _tap is None:
        print("   !! 第 2 步: 拍间隔没量成一秒一拍(实测 %s 秒), 『4 拍 = ? 秒』这一半没做成"
              % (_gaps or "没读到"))

    # ---- 归位① / 归位②: 两段收敛到「无悬空行 + temp 为真」 ----
    r_h0 = r_h1 = None
    if have_wb:
        r_h0 = breakpoint.inject_hit(
            g, BP_JUDGE, cmd_bank.RFL_INJ_DIFF, watch=BP_CALL, watch_vars=VARS_CALL,
            at_vars=VARS_JUDGE, timeout=SETTLE_TIMEOUT,
            label="断[A] 归位①: 停 %s 注入 命令=合闸·实测=分闸(不符) ⇒ 等 %s"
                  "(注入前那一停读回 temp / g_FailStat[0], 那是基线的「上一次记下的状态」)"
                  % (breakpoint.text(BP_JUDGE), breakpoint.text(BP_CALL)),
            falsify="命令与实测不符也不进比对 ⇒ 走不到 %s(即便如此, 归位仍然成立)"
                    % breakpoint.text(BP_CALL))
        _h0v = (r_h0.get("at_vals") or {}) if r_h0 is not None else {}
        print("      断[A] 归位①: ok=%s | %s" % ((r_h0 or {}).get("ok"), (r_h0 or {}).get("detail")))
        print("      注入前那一停读到: %s" % _h0v)
        _add(J, "参考: 归位①(注入 命令=合闸·实测=分闸) —— 基线 temp=%s / g_FailStat[0]=%s / "
                "g_RelayFlg=%s" % (_h0v.get("temp"), _h0v.get("g_FailStat[0]"), _h0v.get("g_RelayFlg")),
             None, "命中=%s(命中 ⇔ 基线 temp 为真 ⇒ 这一段真的调了一次 Recd_RelayFail)"
                   % ((r_h0 or {}).get("ok"),), crit=None)

        r_h1 = breakpoint.inject_hit(
            g, BP_JUDGE, cmd_bank.RFL_INJ_SAME, watch=BP_WRE, watch_vars=VARS_WRE,
            at_vars=VARS_JUDGE, timeout=SETTLE_TIMEOUT,
            label="断[A] 归位②: 停 %s 注入 命令=合闸·实测=合闸(一致) ⇒ 等「记录结束」"
                  "写库位置 %s(把可能的悬空行补完; 基线本来就干净时这一段等不到, 那不是失败)"
                  % (breakpoint.text(BP_JUDGE), breakpoint.text(BP_WRE)),
            falsify="基线有悬空行而「记录结束」支仍走不到 ⇒ 不会停到 %s" % breakpoint.text(BP_WRE))
        print("      断[A] 归位②: ok=%s | %s" % ((r_h1 or {}).get("ok"), (r_h1 or {}).get("detail")))
        _add(J, "参考: 归位②(注入 命令=合闸·实测=合闸)", None,
             "命中=%s; %s" % ((r_h1 or {}).get("ok"), (r_h1 or {}).get("detail") or "-"), crit=None)
    else:
        _add(J, "参考: 归位(不符 → 一致 两段)", None,
             "没有调试会话(或用户指定只做黑盒)⇒ 本次没做", crit=None)

    # ---- ⑤ 一致时不误记(否定期望) ----
    # ⚠ 为什么这一段**必成立**: 归位那两段之后 temp 恒为真, 而"命令=合闸·实测=合闸"
    #   ⇒ :501-514 现算 stat = TRUE == temp ⇒ :515 那一支不进 ⇒ :521 走不到。若它**照样被走到**,
    #   那就是"一致却记失败" —— 正是规格「待核」那半句要查的形态。
    # ⚠ ⑤ 的**比较基准取在此刻这一读回**, 不是基线 `seq0` —— 归位① 在"基线 temp 为真"时
    #   会**合法地**落一笔「记录开始」把序号推进一格, 拿基线比会恒判"一致时也记失败"。
    _seq_pre = cmd_bank.rfl_top(cmd_bank.rows_by_seq(
        cmd_bank.read_relayfail_rows(ser, (1, 2, 3), wait=WAIT, quiet=True)))
    r_miss = None
    if have_wb:
        r_miss = breakpoint.inject_miss(
            g, BP_JUDGE, cmd_bank.RFL_INJ_SAME, watch=BP_CALL, window=NEG_WINDOW,
            timeout=SETTLE_TIMEOUT, at_vars=VARS_JUDGE,
            label="断[A] 否定期望: 注入 命令=合闸·实测=合闸(一致) ⇒ %.0fs 内 %s **不该**被走到"
                  % (NEG_WINDOW, breakpoint.text(BP_CALL)),
            crit="⑤",
            falsify="命令与实测一致时也进 stat != temp 那一支(或去抖计时没被复位)"
                    "⇒ :521 照样被走到, 一致状态每拍都在记失败")
    else:
        time.sleep(NEG_WINDOW)
    rows_a = cmd_bank.read_relayfail_rows(ser, (1, 2, 3), wait=WAIT, quiet=True)
    _seq_a = cmd_bank.rfl_top(cmd_bank.rows_by_seq(rows_a))
    if r_miss is not None:
        print("      断[A] 否定那一趟: ok=%s | %s" % (r_miss.get("ok"), r_miss.get("detail")))
        print("      注入前那一停读到: %s" % (r_miss.get("at_vals") or {}))
        _add(J, "断[A] 命令与实测一致时, 写库调用点(:521)**未被走到**(该指令路径不存在)",
             r_miss.get("ok"), r_miss.get("detail") or "没做成 —— 未证",
             crit="⑤", obs=judge.DEBUG, trig=judge.TRIG_INJECT,
             falsify="一致时也走到 %s ⇒ 一致状态每拍都在记失败(规格「待核」那一形态)"
                     % breakpoint.text(BP_CALL))
    else:
        _add(J, "断[A] 一致时不走到写库调用点", None,
             "没有调试会话(或用户指定只做黑盒)⇒ 白盒这一次没做成", crit="⑤", obs=judge.DEBUG,
             falsify="不做这一次时无从判 —— 本记录不作为固件证据")
    _add(J, "串口 命令与实测一致: 记录未新增(序号不推进)",
         (_seq_a == _seq_pre) if (_seq_a is not None and _seq_pre is not None) else None,
         "序号 %s → %s(基准取在否定窗口之前那一次读回; 基线曾为 %s, 归位段合法推进不计入)"
         % (_seq_pre, _seq_a, seq0),
         crit="⑤",
         falsify="一致状态也推进序号 ⇒ 判定体在命令与实测相符时照样落库")

    # ---- ⑥ 不符: 停 :498 注"不符", 等 :521 读现算出来的 stat ----
    # ⚠ ③ 那个窗口的**下界必须读在触发之前** —— `inject_hit` 返回时记录已经写完, 下界读在它之后
    #   等于"窗口从写完那一刻之后才开始", 时标正确的固件也落窗外。
    t_diff0 = cmd_bank.read_clock(ser, chip="管理芯", wait=WAIT, quiet=True) or t0
    r_diff = None
    if have_wb:
        r_diff = breakpoint.inject_hit(
            g, BP_JUDGE, cmd_bank.RFL_INJ_DIFF, watch=BP_CALL, watch_vars=VARS_CALL,
            at_vars=VARS_JUDGE, timeout=HIT_TIMEOUT,
            label="断[A] 注入 命令=合闸·实测=分闸(不符) ⇒ 等 %s(Recd_RelayFail 调用点, "
                  "读 stat / 命令 / g_RelayFlg)" % breakpoint.text(BP_CALL),
            crit="⑥",
            falsify="命令与实测不符也不进比对 ⇒ 走不到 %s" % breakpoint.text(BP_CALL))
    t_diff1 = cmd_bank.read_clock(ser, chip="管理芯", wait=WAIT, quiet=True)
    if r_diff is not None:
        _dv = r_diff.get("vars") or {}
        print("      断[A] 不符那一趟: ok=%s | %s" % (r_diff.get("ok"), r_diff.get("detail")))
        print("      停时读到: %s" % _dv)
        for _ln in (r_diff.get("injects") or []):
            print("      注入账: %s" % _ln)
        print("      注入前那一停读到: %s" % (r_diff.get("at_vals") or {}))
        _cmd_v = cmd_bank.gdb_ints(_dv.get("g_RelayCmd[0]"))
        _flg_v = cmd_bank.gdb_ints(_dv.get("g_RelayFlg"))
        _stat_got = cmd_bank.gdb_bool(_dv.get("stat"))
        _stat_hope = cmd_bank.rfl_stat_expect(_dv.get("g_RelayCmd[0]"), _dv.get("g_RelayFlg"), None)
        _add(J, "断[A] :501-514 现算的 stat 与「命令 + g_RelayFlg 低 4 位」现算值相同"
                "(且走的是合闸方向那两条)",
             (_stat_got == _stat_hope) if (_stat_got is not None and _stat_hope is not None
                                           and _cmd_v == [cmd_bank.RFL_CMD_ON]) else None,
             "读到 stat=%s; 命令=%s g_RelayFlg=%s ⇒ 现算应为 %s"
             % (_dv.get("stat"), _cmd_v or "读不到", _flg_v or "读不到", _stat_hope),
             crit="⑥", obs=judge.DEBUG, trig=judge.TRIG_INJECT,
             falsify="固件不按 :501-514 那四条规则算 stat(如直接比 g_RelaySta / 比 g_RelayFlg 整字节 / "
                     "方向搞反)⇒ 读回的 stat 与现算值不同")
    else:
        _add(J, "断[A] 现算的 stat 与四条规则一致", None,
             "没有调试会话(或用户指定只做黑盒)⇒ 注入观测这一次没做成(注入没有可降级的黑盒替身)",
             crit="⑥", obs=judge.DEBUG,
             falsify="不做这一次时无从判 —— 本记录不作为固件证据")

    # ---- ① ③ 串口: 「记录开始」那一笔(序号推进 + 新行未结束 + 时标落窗) ----
    rows_b = cmd_bank.read_relayfail_rows(ser, (1, 2, 3), wait=WAIT)
    by_b = cmd_bank.rows_by_seq(rows_b)
    _new_b = sorted(s for s in by_b if s > seq0)
    seq_b = _new_b[-1] if _new_b else None
    t_b = cmd_bank.read_clock(ser, chip="管理芯", wait=WAIT, quiet=True)
    _add(J, "串口 负荷开关误动作序号推进(新落「记录开始」行)且新行未结束",
         None if not by_b else (bool(_new_b) and by_b[seq_b]["t_end"] is None),
         "序号 %s → %s(新行 %s; 新行结束时刻=%s)"
         % (seq0, cmd_bank.rfl_top(by_b), _new_b or "无",
            (by_b[seq_b]["t_end"] if seq_b else "-") or "(未结束)"),
         crit="①",
         falsify="固件在命令与实测不符时不落「记录开始」⇒ 序号不推进 / 新行结束时刻不为空")
    _add(J, "串口 新「记录开始」行的发生时刻落在本次窗口(=当时表钟)",
         cmd_bank.lp_in_window(by_b[seq_b]["t_start"] if seq_b else None,
                               t_diff0, cmd_bank.clock_add(t_b or t_diff0, 15)),
         "发生时刻=%s; 窗口=[%s, %s+15s]" % (by_b[seq_b]["t_start"] if seq_b else None,
                                             t_diff0, t_b or t_diff0),
         crit="③",
         falsify="时标取的不是当时表钟(Get_MeterTime 写错偏移 / 用了旧时间)⇒ 落窗外")

    # ---- ② 一致: 停 :498 注"一致", 等 :1348(「记录结束」写库位置) ----
    t_same0 = cmd_bank.read_clock(ser, chip="管理芯", wait=WAIT, quiet=True) or t_b or t0
    r_end = None
    if have_wb:
        r_end = breakpoint.inject_hit(
            g, BP_JUDGE, cmd_bank.RFL_INJ_SAME, watch=BP_WRE, watch_vars=VARS_WRE,
            at_vars=VARS_JUDGE, timeout=HIT_TIMEOUT,
            label="断[A] 注入 命令=合闸·实测=合闸(一致) ⇒ 等「记录结束」写库位置 %s"
                  % breakpoint.text(BP_WRE),
            crit="②",
            falsify="固件没有「记录结束」这条路径 ⇒ 命令与实测恢复一致后走不到 %s"
                    % breakpoint.text(BP_WRE))
    t_same1 = cmd_bank.read_clock(ser, chip="管理芯", wait=WAIT, quiet=True)
    if r_end is not None:
        print("      断[A] 一致那一趟: ok=%s | %s" % (r_end.get("ok"), r_end.get("detail")))
        print("      停时读到: %s" % (r_end.get("vars") or {}))
        for _ln in (r_end.get("injects") or []):
            print("      注入账: %s" % _ln)
        print("      注入前那一停读到: %s" % (r_end.get("at_vals") or {}))
        _add(J, "断[A] 命令与实测恢复一致 ⇒ 落到「记录结束」写库位置(%s)" % breakpoint.text(BP_WRE),
             r_end.get("ok"), r_end.get("detail") or "没命中 —— 未证",
             crit="②", obs=judge.DEBUG, trig=judge.TRIG_INJECT,
             falsify="固件不走「记录结束」支, 或那一行已有头有尾被守卫拦掉 ⇒ 不会停到 %s"
                     % breakpoint.text(BP_WRE))
    else:
        _add(J, "断[A] 命令与实测恢复一致 ⇒ 落「记录结束」一笔", None,
             "没有调试会话(或用户指定只做黑盒)⇒ 白盒这一次没做成", crit="②", obs=judge.DEBUG,
             falsify="不做这一次时无从判 —— 本记录不作为固件证据")

    # ---- ② ③ 串口: 上一行的结束时刻被补上, 序号不推进 ----
    rows_c = cmd_bank.read_relayfail_rows(ser, (1, 2, 3), wait=WAIT)
    by_c = cmd_bank.rows_by_seq(rows_c)
    _row_b = by_c.get(seq_b) if seq_b is not None else None
    _t_end_b = (_row_b or {}).get("t_end")
    _win_c = (t_same0, cmd_bank.clock_add(t_same1 or t_same0, 12))
    _add(J, "串口 「记录开始」行(序号=%s)的结束时刻被补上 = 落了「记录结束」一笔(未新开行)" % seq_b,
         None if _row_b is None else (_t_end_b is not None and cmd_bank.lp_in_window(_t_end_b, *_win_c)),
         "结束时刻=%s; 注入窗口=[%s, %s+12s]" % (_t_end_b or "(未结束)", _win_c[0], t_same1 or t_same0),
         crit="②",
         falsify="固件不把结束时刻补进上一行(或另开一行)⇒ 该行结束时刻仍为空 / 序号多推进一次")
    _add(J, "串口 序号在「记录结束」后不推进(结束不新开一行)",
         (None if (not by_c or seq_b is None) else (max(by_c) == seq_b)),
         "最大序号 %s(与「记录开始」后应同为 %s)" % (cmd_bank.rfl_top(by_c), seq_b),
         crit="②",
         falsify="结束也新开一行 ⇒ 最大序号再推进一格")
    _add(J, "串口 「记录开始」行的结束时刻落在本次窗口(=当时表钟)",
         cmd_bank.lp_in_window(_t_end_b, *_win_c),
         "结束时刻=%s; 窗口=[%s, %s+12s]" % (_t_end_b or "(未结束)", _win_c[0], t_same1 or t_same0),
         crit="③",
         falsify="结束时刻取的不是当时表钟(Get_MeterTime 写错偏移)⇒ 落窗外")

    # ---- ① ④ 不符·二次: 停 :498 注"不符", 等 :1330 读 buff 与 g_RelaySta ----
    # ⚠ 同上: ③ 窗口的下界读在触发之前。
    t_start0 = cmd_bank.read_clock(ser, chip="管理芯", wait=WAIT, quiet=True) or t_same1 or t0
    r_start = None
    _stop_b6 = None                # 停点上读到的记录体前 6 字节(第 5 步要拿它跟 698 读回对拍)
    if have_wb:
        r_start = breakpoint.inject_hit(
            g, BP_JUDGE, cmd_bank.RFL_INJ_DIFF, watch=BP_WRS, watch_vars=VARS_WRS,
            at_vars=VARS_JUDGE, timeout=HIT_TIMEOUT,
            label="断[A] 再注入 命令=合闸·实测=分闸(不符) ⇒ 等「记录开始」写库位置 %s"
                  "(读 buff 与 g_RelaySta)" % breakpoint.text(BP_WRS),
            crit="①",
            falsify="固件不走「记录开始」支 ⇒ 走不到 %s" % breakpoint.text(BP_WRS))
    t_start1 = cmd_bank.read_clock(ser, chip="管理芯", wait=WAIT, quiet=True)
    if r_start is not None:
        _sv = r_start.get("vars") or {}
        print("      断[A] 二次不符那一趟: ok=%s | %s" % (r_start.get("ok"), r_start.get("detail")))
        print("      停时读到: g_RelaySta=%s" % _sv.get("g_RelaySta"))
        for _ln in (r_start.get("injects") or []):
            print("      注入账: %s" % _ln)
        print("      注入前那一停读到: %s" % (r_start.get("at_vals") or {}))
        _add(J, "断[A] 命令与实测不符 ⇒ 落到「记录开始」写库位置(%s)" % breakpoint.text(BP_WRS),
             r_start.get("ok"), r_start.get("detail") or "没命中 —— 未证",
             crit="①", obs=judge.DEBUG, trig=judge.TRIG_INJECT,
             falsify="固件不走「记录开始」支 ⇒ 不会停到 %s" % breakpoint.text(BP_WRS))
        # ④ 操作后开关状态: `:1328 buff[12] = (TRUE == Get_RelaySta())? 0: 1;`
        #   `Get_RelaySta()` 的正文就是 `return g_RelaySta;` ⇒ 读全局量即是那一刻的值。
        _bv = cmd_bank.gdb_bytes(_sv.get("buff"))
        _stop_b6 = _bv[:6] if len(_bv) >= 6 else None
        _b12 = _bv[12] if len(_bv) > 12 else None
        _sta = cmd_bank.gdb_bool(_sv.get("g_RelaySta"))
        _want = None if _sta is None else (0 if _sta else 1)
        _add(J, "断[A] ④a 「操作后开关状态」buff[12] 与当时的开关状态量同向",
             (_b12 == _want) if (_b12 is not None and _want is not None) else None,
             "buff[12]=%s; g_RelaySta=%s ⇒ 应为 %s; buff 长 %d"
             % (_b12, _sv.get("g_RelaySta"), _want, len(_bv)),
             crit="④a", obs=judge.DEBUG, trig=judge.TRIG_INJECT,
             falsify="固件不按 g_RelaySta 现写那一位(写反 / 写错偏移 / 沿用上一笔)⇒ buff[12] 与现算值不同")
        # 第 4 步要读的五段一起留痕(不认领条目): 时刻 / 结束时刻 / 后状态 / 两组电能 / 关联对象。
        _add(J, "参考: 停点上那一条记录体的五段(buffer 长 %d)" % len(_bv), None,
             "发生时刻[0..5]=%s | 结束时刻[6..11]=%s | 误动作后状态[12]=%s | 事件前电能[13..22]=%s | "
             "事件后电能[23..32]=%s(发生支 :1329 刚把它清零, 结束支 :1348/:1349 才填) | 关联对象[33..52]=%s"
             % (_seg(_bv, 0, 6), _seg(_bv, 6, 12), _seg(_bv, 12, 13), _seg(_bv, 13, 23),
                _seg(_bv, 23, 33), _seg(_bv, 33, 53)), crit=None)
    else:
        _add(J, "断[A] 命令与实测不符 ⇒ 落「记录开始」一笔", None,
             "没有调试会话(或用户指定只做黑盒)⇒ 白盒这一次没做成", crit="①", obs=judge.DEBUG,
             falsify="不做这一次时无从判 —— 本记录不作为固件证据")
        _add(J, "断[A] ④a 「操作后开关状态」buff[12] 与当时的开关状态量同向", None,
             "没有调试会话(或用户指定只做黑盒)⇒ 白盒这一次没做成", crit="④a", obs=judge.DEBUG,
             falsify="不做这一次时无从判 —— 本记录不作为固件证据")

    # ---- ① ③ 串口: 第二次「记录开始」那一笔 ----
    rows_d = cmd_bank.read_relayfail_rows(ser, (1, 2, 3), wait=WAIT)
    by_d = cmd_bank.rows_by_seq(rows_d)
    _new_d = sorted(s for s in by_d if s > cmd_bank.rfl_top(by_c))
    seq_d = _new_d[-1] if _new_d else None
    t_d = cmd_bank.read_clock(ser, chip="管理芯", wait=WAIT, quiet=True)
    _add(J, "串口 第二次「记录开始」: 序号再推进一格且新行未结束",
         None if not by_d else (bool(_new_d) and by_d[seq_d]["t_end"] is None),
         "序号 %s → %s(新行 %s; 新行结束时刻=%s)"
         % (cmd_bank.rfl_top(by_c), cmd_bank.rfl_top(by_d), _new_d or "无",
            (by_d[seq_d]["t_end"] if seq_d else "-") or "(未结束)"),
         crit="①",
         falsify="第二笔「记录开始」不落库 ⇒ 序号不再推进 / 新行结束时刻不为空")
    _add(J, "串口 第二次「记录开始」行的发生时刻落在本次窗口(=当时表钟)",
         cmd_bank.lp_in_window(by_d[seq_d]["t_start"] if seq_d else None,
                               t_start0, cmd_bank.clock_add(t_d or t_start0, 15)),
         "发生时刻=%s; 窗口=[%s, %s+15s]" % (by_d[seq_d]["t_start"] if seq_d else None,
                                             t_start0, t_d or t_start0),
         crit="③",
         falsify="时标取的不是当时表钟 ⇒ 落窗外")

    # ---- 第 5 步: 698 读回那一条的条数与时标, 与第 4 步停点上读到的记录体对拍 ----
    _cnt5 = cmd_bank.event_area_count(ser, cmd_bank.RFL_EV_CODE, wait=WAIT)
    _ts_stop = _rfl_rec_ts(_stop_b6)
    _ts_wire = (by_d.get(seq_d) or {}).get("t_start") if seq_d is not None else None
    _add(J, "第 5 步 698 读回该口条数加 1、且新一条的发生时刻 == 第 4 步停点上 buff[0..5]",
         None if (_ts_stop is None or not _ts_wire) else (_ts_stop == _ts_wire),
         "条数=%s(容量 %d); 停点 buff[0..5]=%s → 按固件编码器还原=%s | 698 读回 序号=%s 发生=%s"
         % ("读不出" if _cnt5 is None else _cnt5, RF_CAP, _seg(_stop_b6 or [], 0, 6),
            _ts_stop or "还原不出", seq_d, _ts_wire or "读不出"),
         crit="③", obs=judge.DEBUG, trig=judge.TRIG_INJECT,
         falsify="停点上写进记录体的时刻与 698 读回的不是同一份 ⇒ 记录体与读回对不上"
                 "(或读回落在了别的那一条上)")

    # ---- ② 末段: 再落一次「记录结束」, 让跑完的记录是完整的、g_FailStat[0] 停在偶数 ----
    r_end2 = None
    if have_wb:
        r_end2 = breakpoint.inject_hit(
            g, BP_JUDGE, cmd_bank.RFL_INJ_SAME, watch=BP_WRE, watch_vars=VARS_WRE,
            at_vars=VARS_JUDGE, timeout=HIT_TIMEOUT,
            label="断[A] 末段: 注入 命令=合闸·实测=合闸(一致) ⇒ 等「记录结束」写库位置 %s"
                  "(跑完记录完整、g_FailStat[0] 停在偶数)" % breakpoint.text(BP_WRE),
            crit="②",
            falsify="固件不走「记录结束」支(或那一行已有头有尾被守卫拦掉)⇒ 走不到 %s"
                    % breakpoint.text(BP_WRE))
    t_last = cmd_bank.read_clock(ser, chip="管理芯", wait=WAIT, quiet=True)
    rows_e = cmd_bank.read_relayfail_rows(ser, (1, 2, 3), wait=WAIT)
    by_e = cmd_bank.rows_by_seq(rows_e)
    if r_end2 is not None:
        print("      断[A] 末段一致: ok=%s | %s" % (r_end2.get("ok"), r_end2.get("detail")))
        print("      注入前那一停读到: %s" % (r_end2.get("at_vals") or {}))
        _add(J, "断[A] 末段一致落到「记录结束」写库位置(%s)" % breakpoint.text(BP_WRE),
             r_end2.get("ok"), r_end2.get("detail") or "没命中 —— 未证",
             crit="②", obs=judge.DEBUG, trig=judge.TRIG_INJECT,
             falsify="固件不走「记录结束」支 ⇒ 不会停在 %s" % breakpoint.text(BP_WRE))
    else:
        _add(J, "断[A] 末段一致落「记录结束」一笔", None,
             "没有调试会话(或用户指定只做黑盒)⇒ 白盒这一次没做成", crit="②", obs=judge.DEBUG,
             falsify="不做这一次时无从判 —— 本记录不作为固件证据")
    _add(J, "串口 末段「记录结束」把第二笔的结束时刻补上(序号不推进)",
         None if seq_d is None or by_e.get(seq_d) is None
         else (by_e[seq_d]["t_end"] is not None and cmd_bank.rfl_top(by_e) == cmd_bank.rfl_top(by_d)),
         "序号 %s → %s; 该行结束时刻=%s"
         % (cmd_bank.rfl_top(by_d), cmd_bank.rfl_top(by_e),
            ((by_e.get(seq_d) or {}).get("t_end") or "(未结束)") if seq_d is not None else "-"),
         crit="②",
         falsify="末段「记录结束」没补上尾巴 / 另开了一行 ⇒ 结束时刻仍为空 / 序号又推进")

    # ---- crit=None 的参考记录(答不出 falsify, 不进判据计数, 只进日志) ----
    _e2 = by_e.get(cmd_bank.rfl_top(by_e)) if by_e else None
    _add(J, "参考: 跑完最新一条负荷开关误动作记录的形状(末段停在「记录结束」⇒ 应当是有头有尾)",
         None, "序号=%s 发生=%s 结束=%s" % ((_e2 or {}).get("seq"), (_e2 or {}).get("t_start") or "-",
                                           (_e2 or {}).get("t_end") or "(未结束)"), crit=None)
    _add(J, "参考: 表钟在本次窗口内正常走时(记录时标可与之对拍)", None,
         "起=%s 末=%s" % (t0, t_last), crit=None)
    if r_end2 is not None:
        _fv = (r_end2.get("at_vals") or {})
        _add(J, "参考: 末段注入前那一停的 g_FailStat[0]=%s(跑完它停在偶数 ⇒ temp 为真 = 「无故障」)"
                % _fv.get("g_FailStat[0]"), None,
             "该值由固件 :523-528 自己写回参数区, `_restore_all.py` 不还原它", crit=None)

    # ---- 第 6 步: 最近 10 次 —— 连造 %d 回, 条数应停在容量上、最早那条被顶掉 ----
    # 每一回 = 注入「不符」(落一笔「记录开始」)+ 注入「一致」(把尾巴补上) ⇒ 推进一行。
    # ⚠ 两下必须成对(有头无尾守卫 TaskRecord.c:1303-1317 在"最新一行还悬着"时不让再开新行),
    #   所以一回只推进一条, 不像 5-9/5-10 那样连发帧就能堆。
    _old_pre = (cmd_bank.read_relayfail_rows(ser, (RF_CAP,), wait=WAIT, quiet=True) or {}).get(RF_CAP)
    _rounds = 0
    if have_wb:
        print("   第 6 步: 连造 %d 回(每回 = 一次「记录开始」+ 一次「记录结束」)" % RF_BURST)
        for k in range(1, RF_BURST + 1):
            ra = breakpoint.inject_hit(
                g, BP_JUDGE, cmd_bank.RFL_INJ_DIFF, watch=BP_WRS, watch_vars=VARS_WRS,
                at_vars=VARS_JUDGE, timeout=HIT_TIMEOUT, crit=None,
                label="断[A] 第 6 步 第 %d/%d 回 「记录开始」" % (k, RF_BURST))
            if (ra or {}).get("ok") is not True:
                print("   !! 第 6 步 第 %d 回: 「记录开始」没停到 ⇒ 连造停在这一回" % k)
                break
            rb = breakpoint.inject_hit(
                g, BP_JUDGE, cmd_bank.RFL_INJ_SAME, watch=BP_WRE, watch_vars=VARS_WRE,
                at_vars=VARS_JUDGE, timeout=HIT_TIMEOUT, crit=None,
                label="断[A] 第 6 步 第 %d/%d 回 「记录结束」" % (k, RF_BURST))
            if (rb or {}).get("ok") is not True:
                print("   !! 第 6 步 第 %d 回: 「记录结束」没停到 ⇒ 连造停在这一回" % k)
                break
            _rounds += 1
        print("   第 6 步: 连造成功 %d/%d 回" % (_rounds, RF_BURST))
    else:
        print("   !! 第 6 步要 %d 次注入 ⇒ 没有调试会话时整步不做" % RF_BURST)
    _cnt6 = cmd_bank.event_area_count(ser, cmd_bank.RFL_EV_CODE, wait=WAIT)
    _old_post = (cmd_bank.read_relayfail_rows(ser, (RF_CAP,), wait=WAIT, quiet=True) or {}).get(RF_CAP)
    _cap_ok = None if _cnt6 is None else (_cnt6 == RF_CAP)
    _repl = None if (_old_pre is None or _old_post is None) else (
        (_old_pre.get("seq"), _old_pre.get("t_start")) != (_old_post.get("seq"), _old_post.get("t_start")))
    _add(J, "⑦ 连造 %d 回: 条数封顶在容量 %d 上、最早那条被顶掉" % (RF_BURST, RF_CAP),
         None if (not have_wb or _rounds < RF_BURST) else _tri([_cap_ok, _repl]),
         "连造成功 %d/%d 回; 条数=%s(容量 %d); 第 %d 位那条(最早) 前=序号%s 发生%s / 后=序号%s 发生%s"
         % (_rounds, RF_BURST, "读不出" if _cnt6 is None else _cnt6, RF_CAP, RF_CAP,
            (_old_pre or {}).get("seq") or "读不出", (_old_pre or {}).get("t_start") or "-",
            (_old_post or {}).get("seq") or "读不出", (_old_post or {}).get("t_start") or "-"),
         crit="⑦", obs=judge.DEBUG,
         falsify="容量不是 %d(固件只留 9 条或 11 条)或顶掉的不是最早那条 ⇒ 第 %d 回读回的条数与"
                 "第 %d 位那条的序号对不上" % (RF_CAP, RF_BURST, RF_CAP))

    # ---- 本项够不着的那几半, 写在账本上(不是判据条目, 不进判据表的分母) ----
    # ⚠ 非有不可: 没有这三行, 读日志的人会把"判据全满足"当成"这一项全测过了" ——
    #   而 ⑥ 只覆盖了合闸方向那两条规则, 另两条现算规则一次都没走到。
    J.note("未证: 「真电压下硬件反馈不跟随」那一半 —— 本台交流 0V, g_RelayFlg 恒分闸、"
           "75%Un 窗口常闭, 「命令≠实测」这个状态是**注入造出来的**; 要证须把台面电压加到 "
           "≥75%Un(本台 ≈165V) 让开关状态真的与命令不符")
    J.note("未证: 「拉闸方向」那两条现算规则(TaskRelay.c:501/:506)与 :461-480 那半支限次重发 —— "
           "TAB_Function.againrcd == TRUE 只对合闸方向免掉 g_FailStat[0] < 2; 要证须先把参数区的 "
           "againrcd 改成 FALSE(动参数区)。判据⑥ 因此只覆盖合闸方向那两条")
    J.note("未证: 「低 4 位是混合态 ⇒ stat 沿用 temp」那一路(:508/:513) —— 本项注入的两张表都把低 4 位"
           "铺成全 0 或全 1, 走不到混合态")


def _banner():
    return ("== 5-11 事件记录·负荷开关误动作 | 工程=%s 表号=%s ==\n"
            ".. 触发=**注入**(75%%Un 命令判定 :282-284 与写库判定 :498-499 常关 ⇒ 帧通道改不动 "
            "g_RelayFlg); 停 %s 一次写 %d 样(只开 g_CompFlg 的**低 4 位** ⇒ 写库判定开、"
            "5 位比对判定仍关, 不会真的动继电器), 参数区一个字节都不动; "
            "白盒另停 %s(调用点)/%s(发生支写库语句首行)/%s(恢复支写库语句首行); "
            "净读的那两次: 第 1、2 步连等 %d 拍(不下注入、不发帧), 第 6 步连造 %d 回"
            % (CURRENT.PROJECT, CURRENT.TABLE_ADDR.hex().upper(),
               breakpoint.text(BP_JUDGE), len(cmd_bank.RFL_INJ_DIFF),
               breakpoint.text(BP_CALL), breakpoint.text(BP_WRS), breakpoint.text(BP_WRE),
               PASS_N, RF_BURST))


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "5-11 事件记录·负荷开关误动作(不符⇒记「记录开始」/恢复⇒把尾巴补进上一行/时标/操作后开关状态位)",
        cmd_bank.relayfail_criteria,
        name="5_11_relay_fail",
        parts=[("5-11 负荷开关误动作段", part_relayfail)],
        gdb=breakpoint, banner=_banner(),
        # ⚠ `inject_allow` 必须在**会话构造时**点名 —— 白名单精确匹配符号名, 事后补不了。
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV,
                        inject_allow=cmd_bank.rfl_inject_allow())))
