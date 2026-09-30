# -*- coding: utf-8 -*-
"""在证什么: 管理芯与计量芯之间那条 SPI 链路是活的、且送来的量是直拷出来的 —— ⑤ 698 读 A 相电压读得到
  且非零; ① 每转一圈发出去的 29 字节请求逐字节恒定且形态对(前导 5A5A5A5A / [4]=0x68 / [28]=0x16);
  ② 每发一次请求都有帧回来并落位(轮询闭合); ③ 帧内日期时间域逐拍更新; ④ 帧内电压 4 字节与同一次
  落位刚落下的 `g_Volt[0]` 逐字节对上(直拷, 管理芯不自算)。
会向表写什么: 一帧 `645.factory` 进厂内(698 读在厂内态下才稳) + 一帧 698 读 A 相电压。**只读**,
  参数区与 RAM 一个字节都不动, 也不落任何记录。台面收尾走 `python scripts/_restore_all.py`。
跑法: `python project/tests/_test_7_4_spi_link.py`(真串口 + 真探针)。
结论怎么读: 账本末行「判据: 满足 n/N」, N=5; 退 0 = 通过 / 1 = 失败 / 2 = 未定论。
"""
from common import judge              # 观测种类常量 —— ⑤ 是串口观测, ①~④ 是断点观测
from common import trial              # 运行外壳: argv/日志/账本/串口/会话/收尾三连/退出码
from meterlib import cmd_bank         # 帧目录 + 语义动词积木箱(每步自带打印/判 PASS·FAIL)
from project import CURRENT           # 本工程画像(库不认表, 只有脚本认)
from swdbg import breakpoint          # 断点级白盒; 没接 J-Link 会自动降级
from swdbg import gdbinit             # 整片 .out 地图: 开会话后解一次, 之后断点/注入点只查它

# ---- 子项参数(纯数据; 要改的在这里改) ----
N_ROUNDS = 3        # ①要三拍、②要两拍间隔、③④要两拍
TIMEOUT = 60.0      # 等一次断点命中的上限 —— 三个口都是**自然停**(没有触发动作), 只有等
WAIT = 3.0          # ⑤ 那一帧 698 读等应答的上限

# ---- 断点: 模块级**字面量元组** ----
# `scripts/_check_anchors.py` 用 `ast.literal_eval` 抠 `BP_<X>`/`VARS_<X>` 两个名字逐条对源码核
# (那个变量在断点那一行赋过值没有); 写成 `cmd_bank.SOMETHING` 那种表达式就抠不出来, 等于没人核过。
# `VARS_<X>` 只列**顶层名** —— "帧内哪个偏移是时间域/电压值""要发的那 29 字节怎么取"是协议知识,
# 收在库 `CB.spi_req_vars()` / `CB.spi_land_vars()` / `CB.spi_volt_vars()` 里, 不在这儿再抄一遍。
BP_REQ = ("prev", "Run_TaskVessel", "SpiWriteDMA", 1)          # 唯一的 SpiWriteDMA 调用点 = 每转一圈命中一次
VARS_REQ = ("g_SPIStep", "g_SPIMBuff")    # 要发出去的 29 字节就在 g_SPIMBuff 头 29 个上
BP_LAND = ("prev", "Run_TaskVessel", "Save_Caculator_Data", 1)         # Save_Caculator_Data 调用点 = 每收住一帧命中一次
VARS_LAND = ("'Run_TaskVessel'::STR1_Index", "g_Volt")   # 起始符偏移(静态局部量) + 已落位的电压
# ⚠ ④ 的停点**不在 `:1039`** —— 那一刻 `Save_Caculator_Data` 还没跑, `g_Volt` 装的是上一帧的
#   结果, "来值 == 存值"只能跨拍配, 而两拍之间夹着几次落位不由我们决定(实测踩过)。挪到电压段
#   落位**之后**那一行, 来值与存值就在同一次落位里, 不用任何跨拍假设。理由见 `CB.spi_volt_vars()`。
BP_VOLT = ("TaskMetering.c", 5035)        # 电压段 `Spread_StructArray` 之后、下一个域的 info.Type 那一行
VARS_VOLT = ("g_Volt", "g_SPIMBuff")      # 刚落位的存值 + 帧缓冲(帧内那 4 字节按 STR1_Index 定位)


def _add(J, label, ok, why, crit, obs=judge.DEBUG):
    """记一条判据(`falsify` 按 `crit` 从库里的单点取)+ 打一行三态。"""
    J.add(label, ok, why, crit=crit, falsify=cmd_bank.SPI_FALSIFY[crit], obs=obs)
    print("   [%s] %s —— %s" % ({True: "PASS", False: "FAIL", None: "TBD"}[ok], label, why))


def _no_session(J):
    """没有断点会话 ⇒ ①~④ 一条都做不成 —— 逐条记 `ok=None`(不做成), 不记失败。

    ⚠ `falsify` 这几条**照样得写**(`_add` 从库里取): 缺了它, 判据会打出"认领它的证据答不出
      『什么固件会让它 FAIL』"—— 那是对**没做成**的制度性抱怨, 与本次没接 J-Link 无关,
      读的人会当成固件嫌疑。"""
    for crit, what in (("①", "请求恒为固定读"),
                       ("②", "轮询闭合"),
                       ("③", "落位值随帧更新"),
                       ("④", "落位是直拷不自算")):
        _add(J, "断[%s] %s" % (crit, what), None,
             "本次没有断点会话(没接 J-Link, 或用户指定只做黑盒) ⇒ 这一条没做成", crit=crit)


def part_link(ctx):
    """⑤ + ①②③④ —— 一轮取全部条目。

    ⚠ 观测一律 attach, **禁用 launch**(launch 会复位表, RAM 态清零) —— 见 CLAUDE.md 调试链纪律 1。
    ⚠ 三个断点都是**自然停**、没有发帧触发: 走 `wait_only`(库内), **不是** `ctx.bp()` 预挂 ——
      预挂上又没人等在等命中时, 它自己会把核撂停, 其后每条串口帧整帧无应答(与"表死机"一模一样,
      见 CLAUDE.md 调试链纪律 6)。
    """
    J, ser = ctx.J, ctx.ser
    print("\n== 7-4 计量芯 SPI 链路冒烟 (Communicate.c:1006 发请求 / :1039 落位) ==")
    if N_ROUNDS < 2:
        # 这条是脚本自己的参数守卫: 循环在本脚本里, 拍数不够就没有"多拍/相邻两拍"可谈。
        raise ValueError("N_ROUNDS 至少要 2 —— ①要比多拍、②要比相邻两拍、③④要两拍")

    # ---- 前置: 进厂内 —— 698 读在厂内态下才稳 ----
    cmd_bank.enter_factory(ser)

    ctx.session()
    g = ctx.g
    if g is not None:
        # 整片 .out 解一次(停核遍历, 内部喂狗, 收尾放行) —— 之后断点一律查这张图
        gdbinit.build(g)

    # ---------- ⑤ SPI 送来的量在 698 上出得出(串口观测) ----------
    # ⚠ **先做**: 之后停核会让串口帧变慢甚至丢, 反过来的顺序会把这一条做成假 FAIL。
    ud = cmd_bank.read_oad_ud(ser, cmd_bank.SPI_VOLT_OAD, wait=WAIT)
    uv = cmd_bank.spi_volt_from_ud(ud)
    print("   698 GET %s(A相电压) → %s" % (
        cmd_bank.SPI_VOLT_OAD,
        ("整数 %d (报文单位 0.1V ⇒ %.1f V)" % (uv, uv / 10.0)) if uv is not None
        else "没读到单标量应答, 原文 %s" % (bytes(ud or b"").hex(" ").upper() or "(静默)")))
    _add(J, "⑤ 698 读 %s(A相电压)读得到且非零" % cmd_bank.SPI_VOLT_OAD,
         None if uv is None else (uv != 0),
         ("读到 %d(%.1f V)" % (uv, uv / 10.0)) if uv is not None
         else "没读到单标量应答(原文 %s)" % (bytes(ud or b"").hex(" ").upper() or "(静默)"),
         crit="⑤", obs=judge.SERIAL)

    if g is None:
        _no_session(J)
        return

    # ---------- ①②③④ 断点观测 ----------
    REQ_VARS, LAND_VARS, VOLT_VARS = cmd_bank.spi_req_vars(), cmd_bank.spi_land_vars(), cmd_bank.spi_volt_vars()
    _rk = cmd_bank.spi_req_keys()
    _lkk = cmd_bank.spi_land_keys()
    _i, _dk, _vk, _gk = _lkk["index"], _lkk["dt"], _lkk["volt"], _lkk["gvolt"]
    no_req = g.break_at_anchor(BP_REQ)
    no_land = g.break_at_anchor(BP_LAND)
    no_volt = g.break_at_anchor(BP_VOLT)
    print("   [白盒] 断[A] %s → bp %s (SPI 固定读请求口) / 断[B] %s → bp %s (数据落位口) / "
          "断[C] %s → bp %s (电压段落位之后)"
          % (breakpoint.text(BP_REQ), no_req, breakpoint.text(BP_LAND), no_land,
             breakpoint.text(BP_VOLT), no_volt))
    print("   [白盒] 三个口都是**自然停**、没有触发动作: 断[A] 每转一圈一次, 断[B] 每收住一帧一次, "
          "断[C] 紧跟断[B] 同一次落位之内。")
    reqs, lands, volts, closed = [], [], [], []
    n_hits0 = len(g.other_hits)     # 本段开跑前的掠过数 —— 结束时按批号数出整场发:落
    try:
        for k in range(N_ROUNDS):
            last_req = (k == N_ROUNDS - 1)
            r = g.wait_only(no_req, timeout=TIMEOUT, vars=REQ_VARS, drop=last_req)
            reqs.append(r)
            hit = r.get("hit")
            if hit is None:
                print("   [白盒] 断[A] 第 %d 拍没等到(%s 在 %.0fs 内没命中)"
                      % (k + 1, breakpoint.text(BP_REQ), TIMEOUT))
            else:
                b = cmd_bank.vars_bytes(r.get("vars") or {}, _rk)
                print("   [白盒] 断[A] 第 %d 拍 停在 %s | g_SPIStep=%s | 要发出去的 29 字节 = %s"
                      % (k + 1, hit.where(), (r.get("vars") or {}).get("g_SPIStep"),
                         b.hex(" ").upper() if b else "(读不全)"))
            if last_req:
                break
            n0 = len(g.other_hits)
            # 末一次落位口顺手撤掉(drop=True = 趁停住撤, 见 breakpoint.clear_breaks 的 ⚠)
            l = g.wait_only(no_land, timeout=TIMEOUT, vars=LAND_VARS, drop=(k == N_ROUNDS - 2))
            lands.append(l)
            skipped = [h.bkptno for h in g.other_hits[n0:]]
            closed.append(str(no_req) not in skipped)
            lh = l.get("hit")
            if lh is None:
                print("   [白盒] 断[B] 第 %d 拍没等到(%s 在 %.0fs 内没命中)"
                      % (k + 1, breakpoint.text(BP_LAND), TIMEOUT))
            else:
                v = l.get("vars") or {}
                print("   [白盒] 断[B] 第 %d 拍 停在 %s | STR1_Index=%s | 帧内时间域 %s | "
                      "帧内电压 %s | g_Volt[0] %s"
                      % (k + 1, lh.where(), v.get(_i),
                         (cmd_bank.vars_bytes(v, _dk) or b"").hex(" ").upper() or "(读不全)",
                         (cmd_bank.vars_bytes(v, _vk) or b"").hex(" ").upper() or "(读不全)",
                         (cmd_bank.vars_bytes(v, _gk) or b"").hex(" ").upper() or "(读不全)"))
                print("      ↑ 停住那一刻 `Save_Caculator_Data` 还没跑, 故 `g_Volt` 装的是**上一帧**的结果")
            # 断[C]: 同一次落位之内、电压段**已经**落位之后 —— ④ 的来值与存值在这一刻同帧可比。
            vt = g.wait_only(no_volt, timeout=TIMEOUT, vars=VOLT_VARS, drop=(k == N_ROUNDS - 2))
            volts.append(vt)
            vh = vt.get("hit")
            if vh is None:
                print("   [白盒] 断[C] 第 %d 拍没等到(%s 在 %.0fs 内没命中)"
                      % (k + 1, breakpoint.text(BP_VOLT), TIMEOUT))
            else:
                vv = vt.get("vars") or {}
                print("   [白盒] 断[C] 第 %d 拍 停在 %s | 帧内电压 %s | 刚落位的 g_Volt[0] %s"
                      % (k + 1, vh.where(),
                         (cmd_bank.vars_bytes(vv, _vk) or b"").hex(" ").upper() or "(读不全)",
                         (cmd_bank.vars_bytes(vv, _gk) or b"").hex(" ").upper() or "(读不全)"))

        # ---- ① 请求恒为固定读 ----
        miss = sum(1 for r in reqs if r.get("hit") is None)
        if miss:
            _add(J, "断[A] ① 请求恒为固定读", None,
                 "%d/%d 拍没等到 %s 命中 ⇒ 本次没做成, 不据此判固件"
                 % (miss, N_ROUNDS, breakpoint.text(BP_REQ)), crit="①")
        else:
            bs = [cmd_bank.vars_bytes(r.get("vars") or {}, _rk) for r in reqs]
            if any(x is None for x in bs):
                _add(J, "断[A] ① 请求恒为固定读", None,
                    "有拍数的 29 字节没读全(断点停在 g_SPIMBuff 的空洞区间?) ⇒ 本次没做成", crit="①")
            else:
                same = all(x == bs[0] for x in bs)
                pre_ok = bs[0][0:4] == bytes([cmd_bank.SPI_REQ_PRE] * 4)
                head_ok = bs[0][4] == cmd_bank.SPI_FRAME_HEAD
                tail_ok = bs[0][-1] == cmd_bank.SPI_FRAME_TAIL
                _add(J, "断[A] ① 请求恒为固定读", same and pre_ok and head_ok and tail_ok,
                     "%d 拍各 %d 字节逐字节%s相同; 首拍 %s; 前导 5A5A5A5A=%s [4]=0x68=%s [28]=0x16=%s"
                     % (N_ROUNDS, cmd_bank.SPI_REQ_LEN, "" if same else "**不**",
                        bs[0].hex(" ").upper(),
                        "对" if pre_ok else "**不对**", "对" if head_ok else "**不对**",
                        "对" if tail_ok else "**不对**"), crit="①")

        # ---- ② 轮询闭合 ----
        # 判的是"发出去要有帧回来并落位"(`got`)。**不判"每拍都无重发"** —— 那条把
        # `Communicate.c:1035`(尾字节非 0x16)与 `:1037`(CRC 不过)两支的"帧不合格就 g_SPIStep
        # 归零、重发、不落位"当成故障, 而它是源码里写明的**设计支路**, 链路上有一次抖动就会走到。
        # 重发照记不误(`closed` + 发:落计数), 记作**台面事实**摆在 detail 里, 不让它定固件的红绿。
        # ⚠ "只发不收"的固件仍然红: 每一次等 `:1039` 都会超时 ⇒ `got` 全 False。
        # ⚠ 这两个数是**下界**: `other_hits` 只收"等在别的断点上时掠过"的那些, 没人轮询队列的
        #   那一小窗里落下的命中收不到。所以它们只配当台面事实报, 不许当比值用。
        # ⚠ `bkptno` 从 MI 来的是**字符串**, 比它一律 `str(...)` 对 `str(...)`。
        n_send_hit = (sum(1 for h in g.other_hits[n_hits0:] if str(h.bkptno) == str(no_req))
                      + sum(1 for r in reqs if r.get("hit") is not None))
        n_land_hit = (sum(1 for h in g.other_hits[n_hits0:] if str(h.bkptno) == str(no_land))
                      + sum(1 for l in lands if l.get("hit") is not None))
        if not lands:
            _add(J, "断[A] ② 轮询闭合", None,
                 "一拍都没等到落位口, 配不出间隔 ⇒ 本次没做成", crit="②")
        else:
            got = [(l.get("hit") is not None) for l in lands]
            _add(J, "断[A] ② 轮询闭合", all(got),
                 "%d 个间隔: 每次请求之后等到落位口 = %s; "
                 "整场 :1006 至少 %d 次 / :1039 至少 %d 次(下界, 见 sess.other_hits); "
                 "每拍是否无重发 = %s(重发是固件对不合格帧的设计支路, 只记台面事实, 不参与判定)"
                 % (len(got), got, n_send_hit, n_land_hit, closed), crit="②")

        # ---- ③ 落位值随帧更新 ----
        seen = [l for l in lands if l.get("hit") is not None]
        if len(seen) < 2:
            _add(J, "断[B] ③ 落位值随帧更新", None,
                 "只等到 %d 拍落位口(要 2 拍) ⇒ 本次没做成, 不据此判固件" % len(seen), crit="③")
        else:
            dts = [cmd_bank.vars_bytes(l.get("vars") or {}, _dk) for l in seen[:2]]
            ks = [None if x is None else cmd_bank.spi_dt_key(x) for x in dts]
            if any(x is None for x in dts) or any(k is None for k in ks):
                _add(J, "断[B] ③ 落位值随帧更新", None,
                     "帧内时间域这一次没读全、或不是 D_DateTimeS(%#04x + 7B 值) 形态(%s / %s) "
                     "⇒ 本次没做成, 不据此判固件"
                     % (cmd_bank.SPI_DT_TYPE,
                        "没读全" if any(x is None for x in dts) else dts[0].hex(" ").upper(),
                        "没读全" if any(x is None for x in dts) else dts[1].hex(" ").upper()),
                     crit="③")
            else:
                k1, k2 = ks
                _add(J, "断[B] ③ 落位值随帧更新", k2 > k1,
                     "第 1 拍帧内时间域 %s → (年,月,日,时,分,秒)=%s; 第 2 拍 %s → %s; 第二拍%s第一拍"
                     % (dts[0].hex(" ").upper(), k1, dts[1].hex(" ").upper(), k2,
                        "晚于" if k2 > k1 else "**不晚于**"), crit="③")

        # ---- ④ 落位是直拷不自算(在**同一次落位之内**比 —— 不跨拍配) ----
        # 停点是电压段落位之后的 `TaskMetering.c:5035`: 帧内那 4 字节与刚落位的 `g_Volt[0]`
        # 是同一帧、同一刻, 不需要任何"这两拍是相邻轮"的假设(旧写法就栽在那个假设上, 见
        # `spi_volt_vars` 的 ⚠)。逐拍都比, 全对上才算。
        vs = [v for v in volts if v.get("hit") is not None]
        pairs = []
        for v in vs:
            vv = v.get("vars") or {}
            f, gg = cmd_bank.vars_bytes(vv, _vk), cmd_bank.vars_bytes(vv, _gk)
            if f is not None and gg is not None:
                pairs.append((f, gg))
        if not pairs:
            _add(J, "断[C] ④ 落位是直拷不自算", None,
                 "等到 %d 拍落位口, 但一拍也没等到电压段落位之后的那个停点(或读数没读全) "
                 "⇒ 本次没做成, 不据此判固件" % len(vs), crit="④")
        else:
            # 帧内 4 字节大端 = 来值; `g_Volt[0]` 内存小端 = 存值(`RevCopy_Data` 一次倒序直拷)。
            cmp_ = [(int.from_bytes(f, "big"), int.from_bytes(gg, "little")) for f, gg in pairs]
            bad = [(f, gg, a, b) for (f, gg), (a, b) in zip(pairs, cmp_) if a != b]
            f0, g0 = pairs[0]
            a0, b0 = cmp_[0]
            _add(J, "断[C] ④ 落位是直拷不自算", not bad,
                 "%d 拍各比一次(同一次落位之内): 第 1 拍帧内 +%d 起 4 字节 %s(大端 = %d) ⇔ "
                 "刚落位的 g_Volt[0] 四字节 %s(小端 = %d); %s"
                 % (len(pairs), cmd_bank.SPI_VOLT_VAL_OFF, f0.hex(" ").upper(), a0,
                    g0.hex(" ").upper(), b0,
                    "每一拍都逐字节对上(直拷)" if not bad
                    else "**%d/%d 拍对不上**(如帧内 %s = %d ⇔ g_Volt %s = %d) —— 落位这一侧改过值, "
                         "不是直拷" % (len(bad), len(pairs), bad[0][0].hex(" ").upper(), bad[0][2],
                                       bad[0][1].hex(" ").upper(), bad[0][3])), crit="④")
    finally:
        # 撤断点只能在停住态(见 `breakpoint.clear_breaks` 的 ⚠: 核一跑起来 MI 命令就石沉大海)。
        # 正常路径上两处 `drop=True` 已经撤干净, 这里是**兜底**: 某一拍没等到时断点还挂在槽里,
        # 留着它自己会把核撂停, 其后每条串口帧整帧无应答 —— 表象与"表死机"一模一样。
        try:
            if g.breakpoints():
                g.ensure_stopped()
                g.clear_breaks()
        finally:
            g.ensure_running()


def _banner():
    return "== 7-4 计量芯 SPI 链路冒烟 | 工程=%s 表号=%s ==" % (
        CURRENT.PROJECT, CURRENT.TABLE_ADDR.hex().upper())


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "7-4 计量芯 SPI 链路冒烟", cmd_bank.spi_link_criteria,
        name="7_4_spi_link", parts=[("7-4 SPI 链路段", part_link)],
        gdb=breakpoint, banner=_banner(),
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV)))
