# -*- coding: utf-8 -*-
"""在证什么: 费率参数(时区表 / 时段表 / 费率数)被管理芯**读对、用对** —— ④a 费率数上限 12 生效且 13 被
  645 与 698 两条写入口各自拒; ② 逐段拨钟逼一次重算后, 全局镜像 `g_RateNo`/`g_SoltNo`/`g_ListNo`
  与表上那张时段表逐段一致; ③ 节假日 / 周休日 / 普通日三条取表路径各取到对的表号;
  ④b 重算兜底支; ⑤ 越界费率数被**静默回默认**(内存镜像回默认值而 EEPROM 不动);
  ⑥a/⑥b 写入口读底稿失败时把**厂默认当底稿整块写回**。
会向表写什么: `645.factory` 进厂内; 把表钟拨到 2026-09-10 当天并在这一天里逐段挪时刻(②③); 费率数**等值**
  写回逼重算; ④a 写 13 再写回原值; ⑤ 写 noWeekDay=0xFF; ⑥ 把整块 EEPROM 费率参数改掉再还原。
  参数区与 EEPROM 各支自带还原与复核。台面收尾走 `python scripts/_restore_all.py`。
跑法: `python project/tests/_test_3_1_rate_num.py`(真串口 + 真探针)。
结论怎么读: 账本末行「判据: 满足 n/N」, N=9; 退 0 = 通过 / 1 = 失败 / 2 = 未定论。
"""
from meterlib import cmd_bank       # 帧目录 + 语义动词积木箱(每步自带打印/判 PASS·FAIL)
from meterlib import watch           # AA80 读内存(本脚本下面那三个读原语就用它)
from common import trial           # 运行外壳: argv/日志/账本/串口/会话/收尾三连/退出码
from project import CURRENT          # 本工程画像: 表号/.out 路径/喂狗点(库不认表, 只有脚本认)
from swdbg import breakpoint            # 断点级白盒(停核 + 读函数内局部量); 没接 J-Link 会自动降级
# 整片 .out 地图: 开会话后解一次, 之后断点/注入点只查它(见 swdbg/gdbinit.py 文件头)
from swdbg import gdbinit

# ---- 子项参数(纯数据) ----
# ②③ 拨钟只在这**一天**里挪时刻(段中点都是当天), 故 ③ 的假日条目也写这天的 (日,月,年)。
DATE = "2026-09-10"
# 本表当前费率数(2026-09-10 实读 = 4): ② 每段用它做"等值写 ⇒ 强制重算"。库会拿表上的实际值复核,
# 不一致时**按表上的来**并打印(别让一个写死的常量把整轮测试判成"参数没写进去")。
RATE_N = 4
# 断点(纯数据; 源 = cmd_bank 3-1 段头那段"规格里原来的断点是不成立的断点"的核对断点记录)。
BP_A = ("call", "Run_TaskRate", "Calculate_RateNo", 1)   # `rateNo = Calculate_RateNo()` 调用点 @TaskRate.c:85(`bl` 之后那条指令)
BP_B = ("TaskRate.c", 86)      # `if (rateNo != g_RateNo[0])`(+98) —— rateNo 的 DWARF 活跃区间起点
VARS_A = ("backup",)           # :85 处 backup[4] 全活 = 重算前的 套号/表号/时段号/费率号
VARS_B = ("rateNo",)           # :86 处 rateNo 才开始可读(在 :85 那一句还不在 $r4 里)
# ④b 的注入观测(纯数据): 注入点由**名字**推(不许抄地址), 判据断点取兜底支与正常支都会走到的 :190。
# ⚠ 这两个是**符号名**不是断点(无线号可抄) ⇒ **有意不叫 `BP_*`**: `scripts/_check_anchors.py`
#   按命名约定把每个 `BP_*` 当 `(文件, 行号)` 去对源码核对断点, 叫 `BP_` 会让它报『形状不是 (文件, 行号)』
#   —— 那不是检查误报, 是它守的那条承重约定(名字写歪 ⇒ 这种观测静默地没人核过)。
INJ_FUNC = "Calculate_RateNo"         # 注入点所在函数
INJ_CALLEE = "Read_ParaData"          # 断点 = 紧跟这个调用之后(它会把 g_RatePara 整个重读 ⇒ 断点必须在它后面)
BP_C = ("prev", "Calculate_RateNo", "Fetch_CRC", 1)   # 兜底分支与正常分支**都**走到的 `Fetch_CRC` 那一句 @TaskRate.c:190
# ⚠ 判据断点读什么 = **本子项的数据**(不是库的): 逐元素读 `g_RatePara[0..7]`。
#   为什么不能整条读 —— gdb 对 `INT8U[10]`(实为 char 数组)**按 C 字符串打印**, 整条回来的是带转义的
#   **文本**不是 bytes(又难解又不稳); 逐元素读到的是标量, gdb 必打成整数(`1 '\001'`)。
#   仓里早记过这条(3-2 的 `swTime` 用同一招)。
VARS_C = ("g_RatePara[0]", "g_RatePara[1]", "g_RatePara[2]", "g_RatePara[3]",
          "g_RatePara[4]", "g_RatePara[5]", "g_RatePara[6]", "g_RatePara[7]")
# ---- 风险①/②(工程师提的两条"源码里不存在这些风险(有校验)"; 见 cmd_bank 3-1 段尾那块 ⚠) ----
# ① 判据⑤ **免注入**: 645 写 noWeekDay=0xFF(写入口受理/守卫拒) ⇒ 内部镜像静默回默认, EEPROM 不动。
#    没有可调参数 —— 预置物/块地址都在库里(`CB.RATE_PARA_EE` / `CB.PLANT_NW`)。
# ② 判据⑥a/⑥b **注入造"读底稿失败"** + 698 对照组。三个断点都是**数据**:
#    · 注入点/判据断点 = `VerRd_EEprom` 里 `Read_EEprom` 的**两个调用点**(地址由 .out 反汇编推,
#      `GD.Session.inject_anchors`, **不手抄**); 第 0 处打坏主份 CRC, 第 1 处打坏备用份 ——
#      两处都坏才逼得出 `return OTHER`(`Platform/EEprom.c` 的双份+双 CRC)。
#    · 汇合点断点 = 两条写入口各自的 `if (TRUE != / == Read_ParaData(…))` 那一行(件内已 gdb 核过:
#      645 = `0x35362 <CMD_WriteData+3358>`, 698 = `0xE2C4 <Set_NormalData+2296>`)。
INJ_EE_FUNC = "VerRd_EEprom"          # 注入点所在函数(Platform/EEprom.c)
INJ_EE_CALLEE = "Read_EEprom"         # 断点 = 紧跟这个调用之后(两次调用 = 主份/备用份)
GATE_645 = ("prev", "CMD_WriteData", "Read_ParaData", 4)   # 645 费率数写入口的 `if (TRUE != Read_ParaData(…))` @DLT645App.c:1752
GATE_698 = ("prev", "Set_NormalData", "Read_ParaData", 4)   # 698 费率参数写入口的 `if (TRUE == Read_ParaData(…))` @DLT698App.c:10480(无 else)
# 注入白名单: 要点名登记 —— `g_RatePara[3]`(nRateNum 是第 4 个字段 ⇒ 下标 3) 给 ④b;
# `temp[0]`(VerRd_EEprom 的局部缓冲, 即被 CRC 覆盖的第一个字节) 给 ②。**exact-match**, 拼错一个字符
# 注入当场抛(那是脚本配置错, 该当场炸)。
INJECT_ALLOW = ("g_RatePara[3]", "temp[0]")

# ---- AA80 直读(读在本脚本; 库里只留纯解码与判读) ----
WAIT = 2.5                                   # 每次 AA80 直读等应答的上限(秒)
SETTLE = 1.6                                 # 拨完钟等管理芯跟钟(实测 <1.5s, 留余量)
TRACE_VARS = ("g_RateNo", "g_ListNo", "g_SoltNo", "g_RatePara", "g_ZoneSwNo", "g_SlotSwNo")


def _trace(ser, tag=""):
    """AA80 直读 3-1 要 Watch 的那几个全局量 → **原始字节快照** `{变量名: bytes|None}`。

    ⚠ **只读不解码**: 解码归库(`cmd_bank.rate_trace_decode`), 由 `rate_at`/`date_type_evidence`
      在拿回这份快照后各调一次。本函数若顺手解一次, 到库里就是**解第二遍**——在已解码的 dict 上
      找 `g_RateNo` 这些原始键必然全取不到, 于是 g_RateNo/g_ListNo/g_SoltNo 全变 None,
      看着像"读不回来"。读不出回 None 而不是 0(免与"真值就是 0"混)。

    读**全局**而不是在一次里读 `listNo` 局部量的理由, 见库 3-1 段头「断点观测」那两条 ⚠。"""
    return watch.watch_vars(ser, TRACE_VARS, tag=tag or "读费率镜像", wait=WAIT)


def _mirror8(ser, tag="", wait=WAIT):
    """AA80 直读 `g_RatePara` 的**前 8 字节**(= 8 个字段本体) → bytes|None。**不经 EEPROM**。

    ⚠ 画像里该变量 size 写的 12、DWARF 实测是 `INT8U[10]` ⇒ 多要的那 2 字节是**相邻变量**的内容
      (与本字段无关), 切片 `[:8]` 正是要避开它。
    ⚠ `watch_vars` 的签名是 `(ser, names, tag, wait)` —— **没有 `quiet`**, 别照 `read_rate_para` 塞。"""
    v = watch.watch_vars(ser, ["g_RatePara"], tag=tag or "读 g_RatePara 镜像", wait=wait).get("g_RatePara")
    return None if v is None else v[:8]


def _ee8(ser, ee=cmd_bank.RATE_PARA_EE, tag="", wait=WAIT):
    """AA80 直读**外部 EEPROM** 里的费率参数 8 字节块(区号/偏移/长度见 `CB.RATE_PARA_EE`) → bytes|None。

    ⚠ **这条读法的区内偏移在本台未经验证**: 仓里只实测过区 1(RAM, 基址 0x20000000)的偏移换算;
      区 3 传错偏移的下场在固件里是**整帧静默**(不是 D4), 于是"读不到"与"读错了地方"长得一样。
      故它只**报账**, 不下结论 —— 用它的那几条判据必须拿它与**另一条通道**(645 读回 = 固件自己从
      同一块 EEPROM 解的)互核(`cmd_bank._ee_xcheck`), 一致才认它。"""
    region, off, ln = ee
    verdict, payload = watch.read_mem_aa80(ser, region, off, ln, wait=wait)
    if verdict != "PASS" or not payload:
        return None
    try:
        b = bytes.fromhex(payload.replace(" ", ""))
    except ValueError:
        return None
    return b if len(b) == ln else None


def part_rate(ctx):
    """断[A] 旁证 + ②③④a④b + 风险①/②(⑤⑥a⑥b) —— 一轮取全部条目。

    AA80 读内存那几步由**本脚本**发(上面那三个读原语, 全是裸 `watch.*`), 库只收它们回来的字节
    去解码与判读: ② 的 8 拍与 ③ 的三条路径各读一次走 `_trace`, ④b/⑤ 的镜像走 `_mirror8`,
    ⑤/⑥a/⑥b 的 EEPROM 块走 `_ee8`。

    ⚠ 次序是承重的: 风险① 必须排在 风险② 之前(见下)。
    """
    cmd_bank.enter_factory(ctx.ser)                     # 0x14 受控写的前提(费率参量全走 0x14)

    # ---- 开后调试会话(断点观测)。台面没接 J-Link → None, 后面自动降级(并大声说明) ----
    ctx.session()
    # 整片 .out 解一次(要停核遍历, 内部按块喂狗, 收尾放行) —— 之后 `ctx.bp()` /
    # `inject_anchor()` / `decision_anchor()` 一律查这张图, 谁都不再独立反汇编。
    if ctx.g is not None:
        gdbinit.build(ctx.g)
    inj_at = None
    inj_ee = None
    if ctx.g is not None:
        # ④b 的注入点: **由名字从 .out 推**(那个时机没有行号可抄)。
        # 推不出来只让 ④b 这一半不做(记 ok=None), 不拖垮其余判据。
        try:
            inj_at = ctx.g.inject_anchor(INJ_FUNC, INJ_CALLEE)
        except Exception as exc:
            print("   !! 注入点推不出(%s) ⇒ ④b 注入观测这一半不做" % exc)
        # ② 的两个注入点: `VerRd_EEprom` 里 `Read_EEprom` 的**两个**调用点。用**复数形**
        # `inject_anchors` —— 单数形只回第一处, 而这里主份/备用份两处都要打坏
        # (双份+双 CRC 的结构, 只坏一处会被另一处救回来 ⇒ 造不出"读失败")。
        # ⚠ 两处**缺一不可**: 少于 2 处就把整支记"未做"(ok=None), 绝不硬凑一个地址上去。
        try:
            _aa = ctx.g.inject_anchors(INJ_EE_FUNC, INJ_EE_CALLEE)
            if len(_aa) >= 2:
                inj_ee = {"at": _aa[0], "watch": _aa[1]}
            else:
                print("   !! %s 里只推出 %d 处 `call %s`(要主份+备用份两处)"
                      " ⇒ ② 注入这一半不做" % (INJ_EE_FUNC, len(_aa), INJ_EE_CALLEE))
        except Exception as exc:
            print("   !! ② 注入点推不出(%s) ⇒ ② 注入这一半不做" % exc)
    if not ctx.waived:
        # 断[A] 旁证: 拿一次"等值写费率数"当扳机, 证 ID_TaskRate 的汇合点**真走到了**
        # Calculate_RateNo 的调用点, 并顺带给出重算**前**的四元组快照。
        # `crit=None` —— 它是旁证(判据②③④里没有"周期在跑"这一条), 红了也不会把哪条记成失败,
        # 但会进记录、进日志, 人要能看见。⚠ 台面没接 J-Link 时这一次**照样发**(只是不取证) ——
        # 那正是"降级只能降白盒、不能降黑盒"那条铁律(见 breakpoint.fire_hit 的 ⚠)。
        # ⚠ **断点给 `(文件,行号)` 元组、不预先 break_at**: 由 breakpoint 当场挂、用完必撤。
        #   理由: 本断点在主循环高频行上(见下), 断点活过自己那一次就会自命中撂停核心。
        ctx.take([breakpoint.fire_hit(ctx.g, BP_A, cmd_bank.force_rate_recalc, ctx.ser, RATE_N, wait=2.5,
                              label="断[A] 汇合点 Run_TaskRate:85", vars=VARS_A, drop=True)])

    # `bp` 里的断点**只用于**② 那一拍的"强制重算"那一次 —— ② 有 8 段各一次, 一次里同时拿到写应答与 rateNo。
    # ⚠ 送的是 `"at"` 而**不是**预先挂好的 `"no"`: ② 那 8 次之间夹着"没人管断点"的串口读,
    #   若断点跨次留着, 它会在那些空隙里**自己命中**并把核心撂停 —— 之后每条帧都收不到回,
    #   基线读出全 None、整轮半途中止(2026-09-10 3-1 首跑实测)。`at` ⇒ breakpoint 一次一挂一撤。
    bp = {"at": BP_B, "vars": VARS_B} if ctx.g is not None else None

    # ---- 白盒(断点观测)本次做不做 —— 大声说明, 不让"没取证"悄悄混进结论 ----
    if bp is None and not ctx.waived:
        print("   !! 本次无调试会话 → 断点观测(白盒)整体**未做**; 本次范围 = 仅黑盒"
              "(判过的只是对外行为, 内部指令路径未取证)")
    elif bp is None:
        print("   -- 本次**用户指定**只做黑盒(串口观测) ⇒ 断点观测(白盒)按指定不做(内部指令路径未取证)")

    # ---- 前置: 读表上当前配置(费率参数 / 生效时段表号 / 那张表的内容) ----
    # ② 的期望值全部**从表上读**, 不从常量来 —— 常量写死的话, 表被改过配置就会判成固件错。
    def _why(line):
        print("      · %s" % line)

    orig = cmd_bank.read_rate_para(ctx.ser, wait=WAIT)
    _why("基线费率参数: %s" % {k: orig.get(k) for k in
                             ("zone", "list", "slot", "rate", "holiday", "step", "zWeekDay", "noWeekDay")})
    if orig.get("rate") is None or orig.get("zone") is None:
        ctx.J.note("3-1 费率段: 半途中止(这一次没做成) —— 费率参数读不出 ⇒ 不写不动")
        return
    rate_n = RATE_N
    if orig["rate"] != RATE_N:
        _why("⚠ 传入 rate_n=%s 与表上费率数 %s 不一致 ⇒ 按表上的来" % (RATE_N, orig["rate"]))
        rate_n = orig["rate"]
    _d, _m, _y = cmd_bank.ymd_of(DATE)
    list_exp, alt = cmd_bank.active_slot_no(ctx.ser, _m, _d, wait=WAIT + 0.5)
    _why(alt)
    if list_exp is None:
        ctx.J.note("3-1 费率段: 半途中止(这一次没做成) —— 生效时段表号读不出, ② 无期望值可比")
        return
    entries = cmd_bank.read_slot_tab(ctx.ser, "cur", list_exp, wait=WAIT + 0.5)
    _why("第%d号时段表: %s" % (list_exp, ("%d 段" % len(entries)) if entries else "读不出"))
    if not entries:
        ctx.J.note("3-1 费率段: 半途中止(这一次没做成) —— 生效时段表内容读不出, ② 无期望值可比")
        return

    # ---- ④a: 费率数上限 12 生效(做完即把费率数写回原值) ----
    r4, l4 = cmd_bank.rate_num_limit_evidence(ctx.ser, orig["rate"], wait=WAIT)
    ctx.take(r4)
    for _line in l4:
        _why("【④a】%s" % _line)

    # ---- ②: 逐段拨钟 → 强制重算 → 读全局镜像, 比 rate/solt/list ----
    # `read_trace=_trace` = 把 AA80 读原语交给库: ② 的 8 拍与 ③ 的三条路径, 每次读都从**本脚本**这一行
    # 发出去(裸 `watch.watch_vars`), 库只拿回来的字节去解码与判读。
    r2, l2 = cmd_bank.rate_attribution_evidence(
        ctx.ser, entries, list_exp, DATE, rate_n, settle=SETTLE, wait=WAIT,
        trig=ctx.trig(), bp=bp, read_trace=_trace)
    ctx.take(r2)
    _why("【②】共 %d 段:" % len(entries))
    for _line in l2:
        _why(_line)

    # ---- ③: 三条取表路径(节假日/周休日/普通日), 自带还原 ----
    r3, l3 = cmd_bank.date_type_evidence(
        ctx.ser, "%s 10:00:00" % DATE, rate_n, list_exp, settle=SETTLE, wait=WAIT, read_trace=_trace)
    ctx.take(r3)
    _why("【③】")
    for _line in l3:
        _why(_line)

    # ---- ④b: 注入观测(帧路造不出那个状态 ⇒ 换触发通道) ----
    # `inj` 的两种台面: 有会话 = `ctx.inject()`(打 trig=注入 标记); 没会话 = None
    # ⇒ 库返回空证据并**说明 ④b 未做**(不记 FAIL —— "台面没接"与"固件不对"不是一回事)。
    r4b, l4b = cmd_bank.rate_fallback_evidence(
        ctx.ser, inj=ctx.inject(), at=inj_at, watch=BP_C, mirror=VARS_C, read_mirror=_mirror8)
    ctx.take(r4b)
    for line in l4b:
        print("      · %s" % line)

    # ---- ⚠ 次序是承重的: ① 必须排在 ② 之前 ----
    # ② 的兜底会把**整块** EEPROM 费率参数写成出厂默认, 会把 ① 写进去的那个 0xFF 冲掉;
    # 反过来(② 在前)则 ② 的基线被 ① 的残留污染。两支各自内部都会还原+复核, 但**互相**不认账
    # ⇒ 顺序错了不是"脏一点", 是两支的读数都不可信。
    # ---- 风险①: 越界参数被静默回默认(免注入, 真故障) ----
    # 两条读都从本脚本发: EEPROM 块走 `_ee8`(裸 `watch.read_mem_aa80`), 内存镜像走 `_mirror8`。
    ctx.hold(*cmd_bank.rate_silent_default_evidence(
        ctx.ser, read_ee=_ee8, read_mirror=_mirror8), "①(判据⑤)")

    # ---- 风险②: 写入口读底稿失败 ⇒ 拿厂默认当底稿整块写回(注入造故障) + 698 对照 ----
    # `inj` 没会话 = None ⇒ 库返回空证据并说明"未做"(不记 FAIL)。
    # 三个断点全是纯数据: 两处注入点 `(inj_ee["at"], inj_ee["watch"])` + 两条写入口各自的汇合点行。
    ctx.hold(*cmd_bank.rate_write_fallback_evidence(
        ctx.ser, inj=ctx.inject(),
        at=(inj_ee or {}).get("at"), watch=(inj_ee or {}).get("watch"),
        gate645=GATE_645, gate698=GATE_698, rate_n=RATE_N, read_ee=_ee8), "②(判据⑥a/⑥b)")


def _banner():
    return ("== 3-1 最多12费率 | 工程=%s 表号=%s ==\n"
            "!! 表钟停在 %s 当天 —— 收尾请跑: python scripts/_restore_all.py"
            % (CURRENT.PROJECT, CURRENT.TABLE_ADDR.hex().upper(), DATE))


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "3-1 最多12费率", cmd_bank.rate_para_criteria,
        name="3_1_rate_num", parts=[("3-1 费率段", part_rate)],
        gdb=breakpoint, banner=_banner(),
        # ⚠ `inject_allow` 必须在**会话构造时**点名 —— 白名单精确匹配符号名, 事后补不了。
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV, inject_allow=INJECT_ALLOW)))
