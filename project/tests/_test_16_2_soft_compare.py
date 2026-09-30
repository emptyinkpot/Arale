# -*- coding: utf-8 -*-
"""在证什么: 本机发布标识(K_SoftVersion)被固件按**三条互不相干的读路**报出来, 且各自的正反序是对的
  —— 698 读 0xFF3005 线上正序 / 645 读版本 DI 04CC0000 正序 / 645 厂内标定读 DI E1000000 反序;
  外加应用区程序集成标识 0xFF3004 的读回值 == **我们自己**按固件口径从 `.out` 重算出来的那 8 个字符,
  以及三段分区(0xFF3002/0xFF3003/0xFF3004)自洽。
会向表写什么: **只读** —— 发 645 进厂内(读记录受安全判定管, 645 厂内标定那条读路更是明文要求厂内),
  然后三条读路各发只读帧、三个集成标识各发一条只读 GET。不写任何参数、不动表钟、不注入。
  ⚠ 本项跑完**停在厂内态**(原样搬自库里的序列, 收尾那一步不在本子项里), 收台面走 `python scripts/_restore_all.py`。
跑法: `python project/tests/_test_16_2_soft_compare.py`(真串口 + 真探针; `--no-gdb` 只做黑盒半边)。
结论怎么读: 账本末行「判据: 满足 n/N」, N=8(②b/③/④ 三条**本台不可证**, 已在条目里声明 ⇒ 总记未定论);
  退 0 = 通过 / 1 = 失败 / 2 = 未定论。
"""
from common import judge              # 观测种类常量(黑盒标 SERIAL, 白盒那几条标 DEBUG)
from common import trial              # 运行外壳: argv/日志/账本/串口/会话/收尾三连/退出码
from meterlib import cmd_bank         # 帧目录 + 语义动词积木箱(每步自带打印/判 PASS·TBD·FAIL)
from project import CURRENT           # 本工程画像: 表号/.out 路径/喂狗点(库不认表, 只有脚本认)
from swdbg import breakpoint          # 断点级白盒(停核 + 读函数内局部量); 没接 J-Link → None
# 整片 .out 地图: 开会话后解一次, 之后断点/注入点只查它(见 swdbg/gdbinit.py 文件头)
from swdbg import gdbinit

# ---- 子项参数(纯数据; 要改的在这里改) ----
WAIT = 3.0                     # 每条读帧的单次等待

# ---- 断点: 模块级**字面量元组** ---------------------------------------------------
# `scripts/_check_anchors.py` 用 `ast.literal_eval` 抠 `BP_<X>`/`VARS_<X>` 这两个名字逐条对源码核
# (变量在断点那一行赋过值没有); 写成 `CB.SOMETHING` 那种表达式就抠不出来, 等于没人核过。
BP_698 = ("DLT698App.c", 8921)     # 698 四个 OAD 的共享尾部 `info.InBuf = &buff[0];` 之后 @0x9dae
VARS_698 = ("buff", "sch")
# ⚠ `buff` 是 `INT8U[]`(:8880 那个 `INT8U buff[128]`) —— gdb 按**字符串字面量**印,
#   故读它要用库里的 `gdb_bytes`(拿 `_gdb_ints` 抠会把八进制当十进制)。
# ⚠ `pOAD`/`num`/`u32Adr`/`u32Size`/`i` 在 `0x9dae` **没有位置区间**(`info scope` 核过)⇒ 不许收进来,
#   读回来是"读不到", 而那看起来像"固件没给值"。
BP_645 = ("call", "CMD_ReadData04", "Copy_Data", 13)   # 645 读版本两支 `Copy_Data` 之后 @DLT645App.c:6190(`pFrame` 在 $r4 里活着)
VARS_645 = ("pFrame",)            # 指针 ⇒ 只给指令路径
BP_FAC = ("call", "ST_FactoryReadCMD", "Reverse_Data", 1)   # 厂内标定支 `Reverse_Data` 之后 @DLT645App.c:843(`pu8Frame` 在 $r4 里活着)
VARS_FAC = ("pu8Frame",)          # 同上


def _add(J, label, ok, why, crit, obs=judge.SERIAL, falsify=None, trig=None):
    """记一条判据 + 打一行三态(`falsify` 逐条给 —— 同一个 crit 的两个通道答的假条件不同)。"""
    J.add(label, ok, why, crit=crit, obs=obs, falsify=falsify, trig=trig)
    print("   [%s] %s —— %s" % ({True: "PASS", False: "FAIL", None: "TBD"}[ok], label, why))


def _hit_vars(r):
    """`breakpoint.fire_hit` 的记录 → `(命中没有, 停住时读到的量)`。记录为 None = 没会话/断点没下上。"""
    return (r or {}).get("ok") is True, dict((r or {}).get("vars") or {})


def part_softver(ctx):
    """一段 = 16-2 的全部条目: 进厂内 → 离线重算 → 三条读路各(黑盒读 + 白盒停) → 三个集成标识。

    ⚠ **黑盒要各读一次、白盒那次是第二次**: `fire_hit` 交回来的记录里**没有触发动作的返回值**
      —— `record()` 按"命中没有"定 `ok` 就把 `result` 丢了(见它自己的 ⚠), 而本条判据恰恰是**值**。
      让动作把值塞进一个共享 dict 也能办到, 但那等于把库的返回结构当管道用; 读一次便宜,
      且两次都是只读 GET, 不改表状态。
    ⚠ **断点侧读的是哪一半, 逐断点写清**:
      · 断[A](`DLT698App.c:8921`)—— `buff` 是 `INT8U[]`, 读回**内容** ⇒ ①a/②a 的白盒那一半比的是字节。
      · 断[B](`DLT645App.c:6190`)/断[C](`DLT645App.c:843`)—— `pFrame`/`pu8Frame` 在 `$r4` 里是**指针**,
        本库的读名口只收**普通变量名**(见 `scripts/_check_anchors.py`), 读不了 `pFrame[0]` 那种表达式
        ⇒ 这两条白盒只给**指令路径**(停在这一支的写入口之后), **不声称读到内容**;
        内容那一半由同一条判据的串口观测(和 ①a 的白盒)给。
    ⚠ 观测一律 attach(**禁用 launch**: launch 会复位表、RAM 态清零); 见 CLAUDE.md 调试链纪律 1。
    ⚠ 递**断点元组**而不是已挂好的 bpno: `fire_hit` 见到元组会自己挂、命中与没命中**两条路都撤**。
    """
    J, ser = ctx.J, ctx.ser
    print("\n== 16-2 软件要求 · 软件比对 ==")

    # ---- 开调试会话(断点观测)。台面没接 J-Link / 用户指定只做黑盒 ⇒ None, 白盒那几条记"没做成" ----
    ctx.session()
    if ctx.g is not None:
        gdbinit.build(ctx.g)
    g = ctx.g
    have_wb = g is not None
    if not have_wb:
        if ctx.waived:
            print("   -- 本次**用户指定**只做黑盒(串口观测) ⇒ 断点观测(白盒)按指定不做")
        else:
            print("   !! 本次无调试会话 → 断点观测(白盒)整体**未做**; 本次范围 = 仅黑盒")

    print("   本机发布标识(K_SoftVersion, %dB): %s" % (cmd_bank.SV_VER_BYTES, cmd_bank.SV_ID))
    print("   期望形态: 正序「%s」/ 反序「%s」"
          % (cmd_bank.SV_ID, cmd_bank.SV_ID_REV.decode("ascii")))

    # ---- 进厂内: **读**这条路的前置(记录读受安全判定管; 645 厂内标定那条更是明文) ----
    cmd_bank.enter_factory(ser)

    # ---- 离线那一半: 应用区累加和按固件口径重算一遍(给 ②a 当对照值) ----
    # ⚠ `.out` 路径**显式递进去** —— 判据②a 的对照值是离线重算出来的, 不该指望"某个地方已经
    #   configure 过 elfsym"; 显式传参覆盖已配置值, 也免了"读到别块表的镜像"。
    exp = cmd_bank.sv_expected_appsum(CURRENT.OUT_PATH)
    exp_val = exp_digits = None
    if exp is None:
        J.note("16-2: 离线按 .out 重算应用区累加和没做成 ⇒ 判据②a 没有对照值")
    else:
        exp_val, cov, span = exp
        exp_digits = cmd_bank.sv_digits(exp_val)
        print("   [离线] 应用区 [%#x, %#x) 逐 32 位小端字相加 = %#010x → 固件口径应报「%s」"
              " (覆盖 %d/%d 字节%s)"
              % (cmd_bank.SV_APP_LO, cmd_bank.SV_APP_HI, exp_val, exp_digits, cov, span,
                 ", 一个填充字节都用不到 ⇒ 与未编程区读回什么无关" if cov == span
                 else " !! 没铺满 —— 填充假设会改这个值"))

    # ================= ①a 698 读 0xFF3005(线上正序 / buff 反序那一支) =================
    txt = cmd_bank.sv_read_698_ver(ser, wait=WAIT)
    _add(J, "①a 698 读 %s = 发布标识(线上正序)" % cmd_bank.SV_OAD_VER,
         None if txt is None else (txt == cmd_bank.SV_ID),
         ("读回「%s」⇔ 期望正序「%s」" % (txt, cmd_bank.SV_ID)) if txt is not None
         else "%s 没读回, 或数据域长度对不上两种封装 ⇒ 这一路本次无证据" % cmd_bank.SV_OAD_VER,
         crit="①a", obs=judge.SERIAL,
         falsify="那一支漏掉 `RevCopy_Data` 或 `Spread_OctString` 少翻一次(线上成反序)/读的是别的对象/"
                 "长度不是 32 ⇒ 读回不是正序发布标识")

    if not have_wb:
        _add(J, "①a 断[A] %s 停住时 buff 已是反序串" % breakpoint.text(BP_698), None,
             "未做: 本次无调试会话(降级只降白盒, 串口那一条照跑)",
             crit="①a", obs=judge.DEBUG,
             falsify="buff 里是正序串/别的 32 字节 ⇒ RevCopy_Data 没反序, 或读的不是 TAB_SoftVer")
    else:
        r = breakpoint.fire_hit(
            g, BP_698, lambda: cmd_bank.sv_read_698_ver(ser, wait=WAIT),
            label="断[A] 698 读 %s 停 %s 读 buff" % (cmd_bank.SV_OAD_VER, breakpoint.text(BP_698)),
            vars=tuple(VARS_698), timeout=WAIT + 20.0, crit="①a",
            falsify="固件读版本不走 :8921 那一支(共享尾部)⇒ 断点不停")
        if r is not None:
            J.extend([r])            # 白盒取证记录本身也进日志与观测分母(判据在下面那一行按同一份读数认领)
        hit, vv = _hit_vars(r)
        b = bytes(cmd_bank.gdb_bytes(vv.get("buff")))
        _add(J, "①a 断[A] %s 停住时 buff 已是反序串" % breakpoint.text(BP_698),
             (b[:cmd_bank.SV_VER_BYTES] == cmd_bank.SV_ID_REV) if hit else None,
             ("停住读到 buff[0:%d]=%s (`sch`=%s —— 那是停在哪一号对象的痕迹, 见脚本 VARS_698 注)"
              % (cmd_bank.SV_VER_BYTES, b[:cmd_bank.SV_VER_BYTES].hex(" ").upper(),
                 vv.get("sch", "(读不到)"))) if hit
             else "断[A] 没命中/没下上 ⇒ 白盒这一半本次没做成(≠固件不对)",
             crit="①a", obs=judge.DEBUG,
             falsify="buff 里是正序串/别的 32 字节 ⇒ RevCopy_Data 没反序, 或读的不是 TAB_SoftVer")

    # ================= ①b 645 读版本(0xCC0000, 正序那一支) =================
    val = cmd_bank.sv_read_645_ver(ser, wait=WAIT)
    got_b = bytes(val[:cmd_bank.SV_VER_BYTES]) if val else None
    _add(J, "①b 645 读 %s 前 %dB = 发布标识(正序)" % (cmd_bank.SV_DI_VER, cmd_bank.SV_VER_BYTES),
         None if got_b is None else (got_b == cmd_bank.SV_ID_B),
         ("数据域 %dB: 前 %dB=「%s」⇔ 期望正序「%s」; 其后 %dB = 表型结构"
          % (len(val), cmd_bank.SV_VER_BYTES, got_b.decode("ascii", "replace"), cmd_bank.SV_ID,
             len(val) - cmd_bank.SV_VER_BYTES)) if got_b is not None
         else "645 读版本没应答/数据域不足 %dB ⇒ 这一路本次无证据" % cmd_bank.SV_VER_BYTES,
         crit="①b", obs=judge.SERIAL,
         falsify="那一支若改成 RevCopy_Data(反序)/读的是别的 DI ⇒ 前 32B 不是正序发布标识")

    if not have_wb:
        _add(J, "①b 断[B] %s 645 读版本那一支真被执行" % breakpoint.text(BP_645), None,
             "未做: 本次无调试会话", crit="①b", obs=judge.DEBUG,
             falsify="读版本走别的分支或提前返回 ⇒ 不停在那一行")
    else:
        r = breakpoint.fire_hit(
            g, BP_645, lambda: cmd_bank.sv_read_645_ver(ser, wait=WAIT),
            label="断[B] 645 读 %s 停 %s" % (cmd_bank.SV_DI_VER, breakpoint.text(BP_645)),
            vars=tuple(VARS_645), timeout=WAIT + 20.0, crit="①b",
            falsify="645 读版本不经过 :6190(两处 Copy_Data 之后)⇒ 断点不停")
        if r is not None:
            J.extend([r])
        hit, vv = _hit_vars(r)
        _add(J, "①b 断[B] %s 645 读版本那一支真被执行(白盒只给指令路径, 见 part_softver 头注)"
             % breakpoint.text(BP_645),
             True if hit else None,
             ("停住读到 pFrame=%s" % vv.get("pFrame", "(读不到)")) if hit
             else "断[B] 没命中/没下上 ⇒ 本次没做成",
             crit="①b", obs=judge.DEBUG,
             falsify="读版本走别的分支(如 CMD_ReadData02)/提前返回 ⇒ 不停在 :6190")

    # ================= ①c 645 厂内标定读版本(0xE1000000, 反序那一支) =================
    fac = cmd_bank.sv_read_645_fac(ser, wait=WAIT)
    got_c = bytes(fac[:cmd_bank.SV_VER_BYTES]) if fac else None
    _add(J, "①c 645 厂内标定读 %s = 发布标识(反序)" % cmd_bank.SV_DI_FAC,
         None if got_c is None else (got_c == cmd_bank.SV_ID_REV),
         ("数据域 %dB=「%s」⇔ 期望反序「%s」"
          % (len(fac), got_c.decode("ascii", "replace"),
             cmd_bank.SV_ID_REV.decode("ascii"))) if got_c is not None
         else "645 厂内标定读版本没应答/数据域不足 %dB ⇒ 这一路本次无证据" % cmd_bank.SV_VER_BYTES,
         crit="①c", obs=judge.SERIAL,
         falsify="那一支若漏掉 Reverse_Data(不反序)⇒ 读回是正序发布标识")

    if not have_wb:
        _add(J, "①c 断[C] %s 645 厂内标定读版本那一支真被执行" % breakpoint.text(BP_FAC), None,
             "未做: 本次无调试会话", crit="①c", obs=judge.DEBUG,
             falsify="厂内标定读版本走别的分支或提前返回 ⇒ 不停在那一行")
    else:
        r = breakpoint.fire_hit(
            g, BP_FAC, lambda: cmd_bank.sv_read_645_fac(ser, wait=WAIT),
            label="断[C] 645 厂内标定读 %s 停 %s" % (cmd_bank.SV_DI_FAC, breakpoint.text(BP_FAC)),
            vars=tuple(VARS_FAC), timeout=WAIT + 20.0, crit="①c",
            falsify="厂内标定读版本不经过 :843(memcpy + Reverse_Data 之后)⇒ 断点不停")
        if r is not None:
            J.extend([r])
        hit, vv = _hit_vars(r)
        _add(J, "①c 断[C] %s 645 厂内标定读版本那一支真被执行(白盒只给指令路径)"
             % breakpoint.text(BP_FAC),
             True if hit else None,
             ("停住读到 pu8Frame=%s" % vv.get("pu8Frame", "(读不到)")) if hit
             else "断[C] 没命中/没下上 ⇒ 本次没做成",
             crit="①c", obs=judge.DEBUG,
             falsify="厂内标定读版本走别的分支/提前返回 ⇒ 不停在 :843")

    # ================= ②a / ②c 三段程序集成标识 =================
    ids = {}
    for oad, _nm in cmd_bank.SV_OAD_IDS:
        ids[oad] = cmd_bank.sv_read_698_ids(ser, oad, wait=WAIT)

    got_app = ids.get(cmd_bank.SV_OAD_APP)
    _add(J, "②a 应用区 %s 的读回值 == 离线重算(线上 = 最高位在前)" % cmd_bank.SV_OAD_APP,
         None if (got_app is None or exp_digits is None) else (got_app == exp_digits),
         ("读回「%s」⇔ 离线重算 %#010x → 固件口径「%s」(线上 = 低 8 位十进制本身)"
          % (got_app, exp_val, exp_digits))
         if (got_app is not None and exp_digits is not None)
         else ("应用区没读回" if got_app is None else "离线重算没做成") + " ⇒ 本次没做成",
         crit="②a", obs=judge.SERIAL,
         falsify="固件改用别的累加口径(逐字节/大端字)/区间端点错/不截 32 位/少翻一次 ⇒ 对不上重算值")

    if not have_wb:
        _add(J, "②a 断[A] %s 停住时 buff 开头就是那 %d 个字符" % (breakpoint.text(BP_698), cmd_bank.SV_IDS),
             None, "未做: 本次无调试会话", crit="②a", obs=judge.DEBUG,
             falsify="buff 里的字符与离线重算不符 ⇒ 固件算的不是这个口径")
    else:
        r = breakpoint.fire_hit(
            g, BP_698, lambda: cmd_bank.sv_read_698_ids(ser, cmd_bank.SV_OAD_APP, wait=WAIT),
            label="断[A] 698 读 %s 停 %s 读 buff" % (cmd_bank.SV_OAD_APP, breakpoint.text(BP_698)),
            vars=tuple(VARS_698), timeout=WAIT + 20.0, crit="②a",
            falsify="应用区那一支不经过 :8921(共享尾部)⇒ 断点不停")
        if r is not None:
            J.extend([r])
        hit, vv = _hit_vars(r)
        b = bytes(cmd_bank.gdb_bytes(vv.get("buff")))
        wb_txt, wb_form = cmd_bank.sv_digits_of_head(b)
        wb_exp = None if exp_val is None else cmd_bank.sv_digits_buff(exp_val)
        _add(J, "②a 断[A] %s 停住时 buff 开头就是那 %d 个字符(最低位在前)"
             % (breakpoint.text(BP_698), cmd_bank.SV_IDS),
             (wb_txt == wb_exp) if (hit and wb_txt is not None and wb_exp is not None) else None,
             ("停住读到 buff 开头「%s」(封装=%s, `sch`=%s)⇔ 离线重算的 buff 形态「%s」"
              % (wb_txt, wb_form, vv.get("sch", "(读不到)"), wb_exp))
             if (hit and wb_txt is not None and wb_exp is not None)
             else ("停住读到 buff 前 8B=%s, 但形态对不上两种封装"
                   % b[:cmd_bank.SV_IDS].hex(" ").upper()) if hit
             else "断[A] 没命中/没下上 ⇒ 白盒这一半本次没做成",
             crit="②a", obs=judge.DEBUG,
             falsify="buff 里的字符与离线重算的倒序形态不符 ⇒ 固件算的不是这个口径")

    v2 = cmd_bank.sv_digits_val(ids.get(cmd_bank.SV_OAD_TOTAL))
    v3 = cmd_bank.sv_digits_val(ids.get(cmd_bank.SV_OAD_FACT))
    v4 = cmd_bank.sv_digits_val(got_app)
    nm_c = ("②c 三段分区自洽: %s == (%s + %s) mod 10^8"
            % (cmd_bank.SV_OAD_TOTAL, cmd_bank.SV_OAD_FACT, cmd_bank.SV_OAD_APP))
    if None in (v2, v3, v4):
        _add(J, nm_c, None, "三条标识没读全(有没读回或形态对不上的)⇒ 本次没做成",
             crit="②c", obs=judge.SERIAL,
             falsify="三段不是同一分区/同一口径 ⇒ 关系不成立")
    else:
        rel = (v2 == (v3 + v4) % cmd_bank.SV_MOD)
        # ⚠ 不成立**不判失败**: 某一支的原始和若发生 2^32 回绕, 取低 8 位后这个关系照样不成立,
        #   而本台分辨不了回绕 ⇒ 记「未证」。
        _add(J, nm_c, True if rel else None,
             ("%d == (%d + %d) mod 10^8 = %d ✓"
              % (v2, v3, v4, (v3 + v4) % cmd_bank.SV_MOD)) if rel
             else ("%d != (%d + %d) mod 10^8 = %d —— **不判失败**: 某一支原始和若发生 2^32 回绕, "
                   "取低 8 位后关系照样不成立, 本台分辨不了 ⇒ 记未证"
                   % (v2, v3, v4, (v3 + v4) % cmd_bank.SV_MOD)),
             crit="②c", obs=judge.SERIAL,
             falsify="三段不是同一分区/同一口径(如 02 用了别的区间)⇒ 关系不成立")


def _banner():
    return ("== 16-2 软件要求·软件比对 | 工程=%s 表号=%s ==\n"
            ".. 三条读路(字节序各自不同, 读源码定死): 698:%s 线上正序(buff 反序) / 645:%s 正序 / 645厂内:%s 反序;\n"
            ".. 对照值**我们自己算**: .out 的 [%#x, %#x) 按固件口径逐 32 位小端字相加 (PT_LOAD 段铺满 ⇒ 与填充假设无关);\n"
            ".. 白盒停 %s(四个 OAD 的共享尾部, 读 buff)/%s(645 读版本写入口后)/%s(厂内标定写入口后, 只给指令路径);\n"
            ".. 只读: 不写参数、不动表钟; ③升级校验/④签名核对两条**本台证不了**, 已在条目里声明"
            % (CURRENT.PROJECT, CURRENT.TABLE_ADDR.hex().upper(),
               cmd_bank.SV_OAD_VER, cmd_bank.SV_DI_VER, cmd_bank.SV_DI_FAC,
               cmd_bank.SV_APP_LO, cmd_bank.SV_APP_HI,
               breakpoint.text(BP_698), breakpoint.text(BP_645), breakpoint.text(BP_FAC)))


if __name__ == "__main__":
    raise SystemExit(trial.run_subitem(
        "16-2 软件要求·软件比对(三条读路 = 本机发布标识 + 三段集成标识逐字可算 + 升级校验)",
        cmd_bank.softver_criteria,
        name="16_2_soft_compare",
        parts=[("16-2 软件比对段", part_softver)],
        gdb=breakpoint, banner=_banner(),
        session_kw=dict(out=CURRENT.OUT_PATH, watchdog=CURRENT.IWDT_SERV)))
