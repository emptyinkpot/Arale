# -*- coding: utf-8 -*-
"""在证什么: 698 发一次请求动作瞬时冻结, 真的落进瞬时冻结那本账 —— 落库的时标就是触发那一刻的表钟,
  写进去的电量整列 == 当前读数, 且这本账只留最近 3 条(第 4 条把第 1 条顶掉)。
会向表写什么: 只发 4 次 698 请求动作瞬时冻结(OMD 50 00 03 00, 广播帧); 不改任何参数、不动表钟。
跑法: `python project/tests/_test_4_1_freeze_ping.py`(真串口 + 真探针; `--no-gdb` 只做黑盒)。
结论怎么读: 账本末行「判据: 满足 n/N」, N=6; 退 0 = 通过 / 1 = 失败 / 2 = 未定论。

断点为什么落在 `Save_FrezData` 体内的写库调用之前, 而不是规格写的 `Action_Freeze` 调用行:
  规格要的两个量(那一刻的表钟 5 个字节 / 当时的电量)在这条通路上只有 `Save_FrezData` 的局部
  `buff` 有位置表区间 —— 调用点的形参名是 `pTime`, 而它指向的 `g_SubTime` 是 `TaskTime.c` 里的
  static, 从 `DLT698App.c` 那一帧里查不到。同一点还能读到形参 `idFrez`, 正好用来证这一停走的是
  瞬时冻结那一路(等价于规格里那条 `idFrez==ID_ImmedFrez` 的断点条件)。
 那一刻的电量**不是另一个变量** —— 它就在同一个 `buff` 里: `Prep_ObjData(&buff[6], obj,
 NUM_FrezSelObj, LEN_ImmedFrez-6, normal)`(TaskFreeze.c:1271)在断点行(:1276
 `Write_FrezData(idFrez, &buff[0])`)之前已经返回, 这一条要落库的电量字节就填在 `buff[6:]`
 (第 k 个电能量对象在 `6+65k`, 其第 j 项在 `6+65k+5j`, 5B 小端原始计数; 依据: Prep_ObjData 对
 idx==0 && phs==0 的 OAD 写 `1+C_RateNum` 项、每项 5B, 与 `LEN_ImmedFrez` 的
 `(1+C_RateNum)*(8*5)` 相合)。可读性判据: `buff` 是 `Save_FrezData` 的局部数组
 (`INT8U buff[LEN_ImmedFrez]`, :1241)且地址被取(`&buff[0]` 传参) ⇒ 栈上有位置表区间;
 同一停点脚本**已经在读它**(第 122 行读到 `buff[0..5]` 的时标) —— 同一 PC 上同一 location,
 不是空洞区间。画像登记的那些是**当前**值, 不是写库那一刻的值, 替不了这一份。
"""
import time

from common import judge            # 观测种类常量(断点与探针那几条证据标 DEBUG)
from common import trial            # 运行外壳: argv/日志/账本/串口/会话/收尾三连/退出码
from meterlib import cmd_bank       # 帧目录 + 语义动词积木箱(每步自带打印)
from project import CURRENT         # 本工程画像: 子类/等待窗/.out/喂狗点的单一事实源
from swdbg import breakpoint        # 断点级白盒(停核 + 读函数内局部量); 没接 J-Link 会自动降级
from swdbg import gdbinit           # 整片 .out 地图: 开会话后解一次, 之后断点/注入点只查它
from swdbg.probe import Probe       # SWD 直读(读 FLASH 常量表; 不停核)

# ---- 子项参数(纯数据; 要改的在这里改) ----
SUB = cmd_bank.IMMED_FREZ_SUB   # 瞬时冻结记录子类 0x00(记录 OAD = 50 00 02 00)
OBJ_ROW = 0                     # 瞬时冻结用的对象表行号
WAIT = 3.0                      # 单次记录读回 / GET 读数的等待
SETTLE = 3.0                    # 放行之后等它把记录落完再读回
HIT_TIMEOUT = 30.0              # 一次触发等它停到写库点
N_SEND = 4                      # 连发次数: 比容量 3 多一次, 才看得见"顶掉最早那条"

# 写库那一刻 `buff[6:]` 就是这一条要落库的电量(源: TaskFreeze.c:1271 `Prep_ObjData(&buff[6], obj, …)`,
# 它在断点行 :1276 `Write_FrezData(idFrez, &buff[0])` 之前返回)。
FREZ_BODY_OBJ_OFF = cmd_bank.FREZ_BODY_OBJ_OFF   # 对象数据自 buff[6] 起(4-2 的 ⑪ 用同一个字面量)
FREZ_BODY_ENE_STEP = cmd_bank.KWH_WIRE_BYTES * cmd_bank.CURKWH_STRIDE   # 每个电能量对象 65B = (1+C_RateNum) 项 × 5B
                                # (Prep_ObjData 对 idx==0&&phs==0 写 1+C_RateNum 项;
                                #  与 LEN_ImmedFrez 的 `(1+C_RateNum)*(8*5)` 相合)

# ---- 断点: 模块级**字面量元组** ---------------------------------------------------
# `scripts/_check_anchors.py` 用 `ast.literal_eval` 抠 `BP_<X>`/`VARS_<X>` 这两个名字, 逐条对源码核
#   "要读的变量在断点那一行赋过值没有"; 写成 `CB.SOMETHING` 那种表达式就抠不出来, 等于没人核过。
BP_WRITE = ("prev", "Save_FrezData", "Write_FrezData", 1)   # 写库那一句 `Write_FrezData(idFrez, &buff[0])` 之前一条指令
# 那一停可读的两个量(位置表逐区间核过):
#   `idFrez`      = 这一趟写的是哪本账(应 == ID_ImmedFrez = 13)
#   `buff[0..5]`  = 这一条落库的时标 `[秒(写死 0), 分, 时, 日, 月, 年偏移]`(秒在 :1231 被写死 0)
#   `buff[6:]`    = 这一条要落库的**电量**(那一刻的口径): 8 个电能量对象依次 65B, 第 j 项 5B 小端原始计数
# ⚠ 必须是**字面量元组**, 且每个名字都要能在 C 源码里搜到(点号表达式抠不出/找不到)。
#   `buff[6:]` 是同一个名字的更深位置, **不新增名字** ⇒ VARS_WRITE 一个字不改(改了反而抠不出锚点)。
VARS_WRITE = ("idFrez", "buff")

# ---- 半途中止时要一次记全的条目(纯数据; 与正常路径同名同号**同观测**, 少记一条 = 分母变小)----
ENTRIES_ALL = (
    ("①", "落库记录里的时标 == 触发那一刻读到的表钟", judge.DEBUG),
    ("②", "TAB_FrezObj 第 0 行翻出的 OAD == 规范要的那批对象", judge.DEBUG),
    ("③", "698 读回瞬时冻结记录应答 85 03, 记录电量整列与当前电能对象逐字节一致", judge.SERIAL),
    ("③b", "触发一次之后恰好多出一条(最新一条的序号 == 基线序号 + 1)", judge.SERIAL),
    ("③c", "记录里的电量 == 停住那一刻 buff[6:] 里的电量项(白盒)", judge.DEBUG),
    ("④", "连发 4 次后条数封顶 3, 最早那条被顶掉", judge.SERIAL),
)


def _stop_unproven(ctx, why):
    """半途中止的统一收场: 条目一次记全(`ok=None`) → 出声 → 停住(退 2, 不是失败)。"""
    ctx.J.extend(cmd_bank.unproven_records(ENTRIES_ALL, why, cmd_bank.IMMED_FREZ_FALSIFY))
    ctx.J.note("4-1 瞬时冻结段: 半途中止(这一次没做成) —— %s" % why)
    raise trial.Stop("%s ⇒ 未定论(退 2), 不是失败" % why)


def _idfrez_is_immed(text):
    """gdb 打印的 `idFrez` 文本是不是瞬时冻结那一路 → True/False/None(读不回)。"""
    s = str(text or "")
    if not s or "optimized out" in s or "No symbol" in s:
        return None
    return ("ID_ImmedFrez" in s) or (cmd_bank.st_int(s) == 13)


def _body_match(ser, body, pos=1, oads=None, wait=WAIT):
    """判据③c 的那一步: 记录第 `pos` 条里那几列 == **写库那一刻** `buff[6:]` 里对应的 5 字节项 → `(ok, 说明)`。

    ok: True=比到的列全一致 / False=有列不一致 / None=没做成(记录读不回 / 那一刻的 buff 读回的字节不够 /
        该 OAD 没登记去路换算 ⇒ 拒算)。本函数自打印: 把"那一刻记下的"与"记录里读回的"并排打出来。

    两侧口径必须同一把尺子(否则比的是两件事):
      · 那一刻: 第 k 个电能量对象在 `body[6+65k]`, 第 j 项在 `6+65k+5j`(5B **小端**原始计数) ——
        `Read_CurkWh`(kWhData.c:183)只搬低 5 字节, 这 5 字节就是存的那 40 位(`kwh_slot_val` 同一律)。
      · 记录里: 本版 `VER_20Edit` 已定义 ⇒ 列里项数 = `1+Get_RatePara(3)`(DLT698App.c:5817 的 `#else`),
        逐项 `Copy_Data(&temp[k*j], &buff[pos+k*5], 5)` 后 `Convert_EnyData(j=8, dot=4, HEX,
        sign=TAB_EnySign[OAD>>20])`(:5838) ⇒ 线上那几项就是从这 5 个字节换算出来的。
      ⇒ 期望值一律走 `cmd_bank.kwh_to_698val`(按 OAD 查 `KWH_OAD_698CONV`, **查不到就拒算**)。
    ⚠ 项数**由线上给**(`energy_col_parse` 的 count), 不写死 —— 本台是 5, 换表/改费率会变。
    ⚠ 本台 0 负载 ⇒ 两侧多为 0: 这一条分开的是「写进去的那份字节是不是写库那一刻的那份」,
      分不开「0 真的」与「空快照恰好也是 0」(同 4-1 ② 那条台面限制)。
    """
    oads = oads or cmd_bank.IMMED_FREZ_ENE_OADS
    print("\n   数值判据(瞬时冻结 pos%d 记录里那几列 == 写库那一刻 buff[6:]) [4-1] ..." % pos)
    off, step = FREZ_BODY_OBJ_OFF, FREZ_BODY_ENE_STEP
    last = off + step * (len(oads) - 1)
    if not body or len(body) <= last:
        print("   !! 写库那一刻的 buff 只读回 %d 字节, 连第 %d 个电能量对象的起点(第 %d 字节)都不到 ⇒ 这一半没做成"
              % (len(body or b""), len(oads), last))
        return None, ("写库那一刻的 buff 只读回 %d 字节 ≤ 第 %d 字节(最后一个电能量对象的起点) ⇒ 这一半没做成"
                      % (len(body or b""), last))
    rec = cmd_bank.read_record_ud(
        ser, SUB, pos,
        cmd_bank.rcsd(cmd_bank.REC_SEQ_OAD, cmd_bank.REC_TIME_OAD, *oads), wait=wait)
    if not rec or rec[:2] != b"\x85\x03":
        return None, "记录第 %d 条没读回(或应答不是 85 03) ⇒ 这一半没做成" % pos
    cols = cmd_bank.record_energy_cols(rec, len(oads))
    if not cols:
        return None, "记录第 %d 条抽不出那 %d 列整列 ⇒ 这一半没做成" % (pos, len(oads))
    bad, skipped = [], []
    for k, (oad, col) in enumerate(zip(oads, cols)):
        base = off + step * k
        par = cmd_bank.energy_col_parse(col)
        if par is None:
            return None, "记录第 %d 条的 %s 列不是 `01 <count>` 形态 ⇒ 这一半没做成" % (pos, oad)
        n = par["count"]
        if base + cmd_bank.KWH_WIRE_BYTES * n > len(body):
            return None, "那一刻的 buff 在 %s 这一列上短于 %d 项 ⇒ 这一半没做成" % (oad, n)
        raw = [int.from_bytes(body[base + cmd_bank.KWH_WIRE_BYTES * j: base + cmd_bank.KWH_WIRE_BYTES * (j + 1)],
                              "little") for j in range(n)]
        exp = [cmd_bank.kwh_to_698val(x, oad) for x in raw]
        got = [v for _lead, v in par["slots"]]
        print("   %s 那一刻 buff[%d:] 的 %d 项原始计数 %s → 应 %s | 记录 %s" % (oad, base, n, raw, exp, got))
        if any(e is None for e in exp):
            skipped.append(oad)              # 该 OAD 没登记去路换算 ⇒ 拒算(不是"通过")
        elif exp != got:
            bad.append(oad)
    if bad:
        return False, "%d/%d 列与写库那一刻的电量不一致: %s" % (len(bad), len(oads), ", ".join(bad))
    if skipped:
        return None, ("比到的 %d/%d 列全一致; 但 %s 没登记去路换算(KWH_OAD_698CONV) ⇒ 那几列没证"
                      % (len(oads) - len(skipped), len(oads), ", ".join(skipped)))
    return True, "%d/%d 列与写库那一刻 buff[6:] 的电量逐项一致" % (len(oads), len(oads))


def part_immed_frez(ctx):
    """一段 = 4-1 的全部条目: 探针读对象表 → 进厂内 → 4 次触发(每次停写库点)→ 698 读回。

    ⚠ 观测一律 attach(**禁用 launch**: launch 会复位表、RAM 态清零); 见 CLAUDE.md 调试链纪律 1。
    ⚠ 探针那一次读排在开会话**之前** —— 探针与 gdb 会话抢同一支 J-Link, 用完即关(`with Probe()`)。
    """
    J, ser = ctx.J, ctx.ser
    print("\n===== 4-1 瞬时冻结: 探针读对象表 → 4 次触发(每次停写库点)→ 698 读回 =====")

    # ---- ② 冻结对象表第 0 行(FLASH 常量表, 探针按符号地址读; 不停核) ----
    fb = so = None
    if not ctx.waived:
        try:
            with Probe() as pb:
                fb, so = cmd_bank.freobj_read(pb)
        except Exception as exc:            # 探针开不起来 / 读不成 → 这一条记"没做成"
            print("   !! 探针不可用(%s) ⇒ 对象表这一次读不到" % exc)
    rows0 = cmd_bank.freobj_row(fb, so, OBJ_ROW)
    ok2, why2 = cmd_bank.freobj_check(rows0, OBJ_ROW, tag="4-1 瞬时冻结行")
    J.add("② TAB_FrezObj 第 0 行翻出的 OAD == 规范要的 8 个电能量 + 有功/无功功率(共 10 项)",
          ok2,
          ("用户指定只做黑盒 ⇒ 这一条不做" if ctx.waived else why2),
          crit="②", falsify=cmd_bank.IMMED_FREZ_FALSIFY["②"], obs=judge.DEBUG)

    # ---- 前置 · 进厂内(记录读回受安全判定管)+ 现最新一条(基线) ----
    cmd_bank.enter_factory(ser)
    pre = cmd_bank.read_freeze_row(ser, SUB, 1, wait=WAIT, empty_ok=True)
    print("   基线: 瞬时冻结(子类 0x%02X) 最新一条 = %s" % (SUB, cmd_bank.rec_row_txt(pre)))
    if pre.get("answered") is False:
        _stop_unproven(ctx, "读记录一个字都没回来(串口/Link 断了) ⇒ 后面三次触发也无从核对")

    # ---- 开调试会话(断点观测)。台面没接 J-Link ⇒ None, 白盒那几条如实记"没做成" ----
    ctx.session()
    if ctx.g is not None:
        gdbinit.build(ctx.g)                # 整片 .out 解一次(停核遍历, 内部喂狗, 收尾放行)
    g = ctx.g
    have_wb = g is not None
    if not have_wb:
        print("\n   !! 本次无调试会话 → 断点观测(白盒)整体**未做**; 本次范围 = 仅黑盒"
              "(黑盒只能答『记录有没有推进、时标对不对』, 答不了『停的是写库点、写的是瞬时冻结』)")

    # ---- 四次触发: 每次都由断点取证, 每次之后读回 pos1 ----
    stamp = None            # 第 1 次停在写库点时 `buff[0..5]`(那一条落库的时标)
    id_txt = None           # 第 1 次停住时的 `idFrez`
    rows = []               # 每次触发之后 pos1 的读数(序号/时标)
    body = None             # 第 1 次停在写库点时**整条** `buff`(电量字节自 buff[6] 起; ③c 的对照物)
    for i in range(1, N_SEND + 1):
        print("\n[触发 %d/%d] 698 请求动作瞬时冻结(OMD 50 00 03 00, 广播) ..." % (i, N_SEND))
        if have_wb:
            r = g.with_trigger(BP_WRITE, cmd_bank.send, "698.action.freeze.immed",
                               ser_shared=ser, wait=WAIT, timeout=HIT_TIMEOUT,
                               _vars=VARS_WRITE, _pair_name="4-1 第%d次瞬时冻结" % i)
            v = (r or {}).get("vars") or {}
            hit = (r or {}).get("hit")
            print("   停写库点: %s" % ("命中 @%s" % hit.addr if hit is not None else "没停到"))
            if i == 1:
                id_txt = v.get("idFrez")
                body = cmd_bank.gdb_bytes(v.get("buff"))     # 整条 buff(读回多少字节要看日志——③c 的门槛)
                stamp = body[:6] if body else None           # 前 6B 是时标(① 的对照物)
                print("   现场: idFrez=%s buff[:6]=%s 电量起于 buff[%d] 读回 %d 字节"
                      % (id_txt or "读不到",
                         " ".join("%02X" % x for x in stamp) if stamp else "读不到",
                         FREZ_BODY_OBJ_OFF, len(body or b"")))
        else:
            cmd_bank.send("698.action.freeze.immed", ser_shared=ser, wait=WAIT)
        if SETTLE:
            time.sleep(SETTLE)              # 停的是写库**调用之前**那一条 ⇒ 等它落完再读回
        row = cmd_bank.read_freeze_row(ser, SUB, 1, wait=WAIT, empty_ok=True)
        rows.append(row)
        print("   触发 %d 之后 pos1: %s" % (i, cmd_bank.rec_row_txt(row)))

    # ---- ① 落库时标 == 触发那一刻的表钟 ----
    immed = _idfrez_is_immed(id_txt)
    want_ts = cmd_bank.frez_expect_ts(stamp or [])
    got_ts = rows[0].get("ts") if rows else None
    if not have_wb:
        ok1, why1 = None, "没有调试会话 ⇒ 写库点这一半不做(黑盒没有『那一刻的表钟』这个对照物)"
    elif immed is None:
        ok1, why1 = None, "停住时 idFrez 没读回来(%r) ⇒ 不知道这一停写的是哪本账, 比不了" % id_txt
    elif not immed:
        ok1, why1 = False, "停住时 idFrez=%s, 不是 ID_ImmedFrez ⇒ 这一停不是瞬时冻结那一路" % id_txt
    elif want_ts is None:
        ok1, why1 = None, "停住时 buff 没读回来 ⇒ 没有对照物, 这一条没做成"
    else:
        ok1 = (got_ts == want_ts)
        why1 = ("idFrez=%s; 记录 %s; 写库那一刻 buff[:6] 解出的时标 = %s%s"
                % (id_txt, cmd_bank.rec_row_txt(rows[0]), want_ts,
                   "" if ok1 else " —— 读回的是 %s" % (got_ts or "读不到")))
    J.add("① 落库记录里的时标 == 触发那一刻的表钟", ok1, why1,
          crit="①", falsify=cmd_bank.IMMED_FREZ_FALSIFY["①"], obs=judge.DEBUG)

    # ---- ③ 记录里的电量整列 == 当前读数(读在库动词里, 比也在它里面) ----
    ok3, why3 = cmd_bank.immed_frez_snapshot(ser, tag="4-1", pos=1, wait=WAIT)
    J.add("③ 698 读回瞬时冻结记录应答 85 03, 记录电量整列与当前电能对象逐字节一致",
          ok3, why3, crit="③", falsify=cmd_bank.IMMED_FREZ_FALSIFY["③"])

    # ---- ③b 触发一次之后恰好多出一条: 最新一条的序号 == 基线序号 + 1 ----
    # 序号是固件自增的写库下标(记录里读回的那一格); 一次动作只许 +1: +2 = 一次写了两条 /
    # 不变 = 没写 / 回退 = 记错账, 三种当场不满足。若实跑显示序号是在容量内回绕的, 这一条要
    # 改成「条数 +1」口径(那时用 immed_frez_count 两端各数一次)。
    # ⚠ 不拿 `immed_frez_count` 的返回值当基准: 它把「序号 0」当空行(现存行为), 而序号 0 是这本账
    #   第 1 条的合法号(seqs_join 的 ⚠ 同一条); 条数封顶那件事由 ④ 管。
    pre_seq = pre.get("seq")
    new_seq = rows[0].get("seq") if rows else None
    if pre.get("answered") is False or pre_seq is None or new_seq is None:
        ok3b = None
        why3b = "基线序号=%s / 第 1 次触发后序号=%s ⇒ 少一头, 恰好多一条比不了" % (pre_seq, new_seq)
    else:
        ok3b = (new_seq == pre_seq + 1)
        why3b = ("基线 pos1 序号 %s → 第 1 次触发后 %s; 4 次触发的 pos1 序号依次 %s%s"
                 % (pre_seq, new_seq, seqs_join(rows),
                    "" if ok3b else " —— 差 %s, 不是恰好多一条" % (new_seq - pre_seq)))
    J.add("③b 触发一次之后恰好多出一条(最新一条的序号 == 基线序号 + 1)", ok3b, why3b,
          crit="③b", falsify=cmd_bank.IMMED_FREZ_FALSIFY["③b"])
    # ---- ③c 记录里那几列 == 写库那一刻 buff[6:] 里的电量(这一步的读回与比都在本脚本 `_body_match` 里) ----
    ok3c, why3c = _body_match(ser, body, pos=1, wait=WAIT)
    J.add("③c 记录里的电量整列 == 第 1 步停住那一刻 buff[6:] 里的电量项", ok3c, why3c,
          crit="③c", falsify=cmd_bank.IMMED_FREZ_FALSIFY["③c"], obs=judge.DEBUG)

    # ---- ④ 连发 4 次后条数封顶 3, 且最早那条被顶掉 ----
    n, detail = cmd_bank.immed_frez_count(ser, SUB, upto=N_SEND + 2, wait=WAIT)
    ends = [cmd_bank.read_freeze_row(ser, SUB, p, wait=WAIT, empty_ok=True) for p in (1, 2, 3)]
    seqs = [r.get("seq") for r in ends]
    first = rows[0].get("seq") if rows else None
    if n is None:
        ok4 = None
        why4 = "条数数不出来(%s)" % "; ".join(detail)
    elif first is None:
        ok4 = None
        why4 = "第 1 次触发之后 pos1 序号读不到 ⇒ 顶没顶掉比不了; %s" % "; ".join(detail)
    else:
        ok4 = (n == 3 and first not in seqs and seqs[0] == rows[-1].get("seq"))
        why4 = ("数到 %d 条(%s); 4 次触发的 pos1 序号依次 %s; 收尾 pos1..3 序号 %s; "
                "第 1 次那条(序号 %s)%s"
                % (n, "; ".join(detail), seqs_join(rows), seqs, first,
                   "已被顶掉" if first not in seqs else "**还在账上**"))
    J.add("④ 连发 4 次后条数封顶 3, 最早那条被顶掉", ok4, why4,
          crit="④", falsify=cmd_bank.IMMED_FREZ_FALSIFY["④"])


def seqs_join(rows):
    """每次触发之后 pos1 的序号(读不到写 `?`), 给证据行用。

    ⚠ 判"读不到"要 `is None` —— 序号 0 是合法值(第 1 条冻结记录就是这个号),
      写成 `or "?"` 会把它当空值印成 `?`。
    """
    return "/".join("?" if (r or {}).get("seq") is None else str(r["seq"]) for r in rows)


def _banner():
    return ("== 4-1 瞬时冻结 | 工程=%s 表号=%s ==\n"
            ".. 触发=698 请求动作瞬时冻结(OMD 50000300, 广播, 连发 %d 次); "
            "白盒停 %s 读 %s; 对象表走探针直读 FLASH 常量表(不停核); "
            "记录读回走 698 GetRequestRecord 子类 0x%02X"
            % (CURRENT.PROJECT, CURRENT.TABLE_ADDR.hex().upper(), N_SEND,
               breakpoint.text(BP_WRITE), "/".join(VARS_WRITE), SUB))


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "4-1 瞬时冻结(转瞬写库点/落库时标==触发时刻表钟/电量整列==当前读数/连发 4 次封顶 3)",
        cmd_bank.immed_frez_criteria,
        name="4_1_freeze_ping",
        parts=[("4-1 瞬时冻结段", part_immed_frez)],
        gdb=breakpoint, banner=_banner(),
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV)))
