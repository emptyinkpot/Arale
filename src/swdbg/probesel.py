# -*- coding: utf-8 -*-
"""
swdbg/probesel.py —— 「该用哪支探针」的**唯一解析器**, 与 `common/portsel.py` 刻意对称。

为什么要有它
------------
探针从"只有 J-Link"变成"J-Link 与 CMSIS-DAP 两种都能用"之后, "该用哪一支"就多了一层:
**用哪一端(jlink / cmsis-dap)** 与 **用那一端的哪一支**。这两种选择都不许静默猜 ——
换支探针之后"连不上"与"表挂了/线断了"的现象一模一样, 猜错等于把一个确定错换成
一个看着一样的错。这正是 `jlink.resolve_sn` 当初立下的规矩, 本模块把它扩成两端通吃。

判据四步(与串口侧 `portsel` 一字不差的口径)
--------------------------------------------
    ① 判据 → ② 候选 → ③ **逐个证明** → ④ 恰好一支
    · 判据: 卡带 `PROBE` 指名用哪一端(None = 两端都找); 每一端再用自己的
      `JLINK_SN` / `DAP_UID` 指名用哪一支(None = 该端恰好一支才认)。
    · 候选: 两端各自枚举后合并。
    · 证明: **真开一次最小会话, 读 CPUID `0xE000ED00`, 必须 == `0x410CC300`** ——
      枚举得到只说明"USB 上有个探针", 说明不了"它后面接的是本表"。这一条是整件事的关键,
      它同时挡掉了两种误认: 接着别的板子的探针、以及枚举到但已经连不上的探针。
      (旁证: 串口侧对应的做法是"逐个发 698 读钟", 同一个道理。)
    · 恰好一支才算数; 0 支 / 多支都抛, **并把每一个候选的失败原因列出来**。

⚠ 与本模块的邻居的分工
----------------------
`jlink.py` 仍是 **J-Link 那一端**的枚举/诊断/恢复(--doctor / --recover)的所在, 本模块不抢它。
本模块只管"选哪一支", 且**不结束任何进程**(铁律 3 在选探针这一步同样成立)。
"""
from __future__ import print_function



from common import faultlog
from common import machspec
from swdbg import probe_cmsis
from swdbg import probe_jlink

__all__ = ["ProbeSelectError", "BACKENDS", "candidates", "verify", "pick", "doctor", "main"]

# 两端。名字就是卡带 `PROBE` 字段里该写的那个字 —— 单一事实源, 三处(卡带/日志/错误信息)
# 都从这里取, 免得各写各的写法("cmsis_dap" / "DAPLink" / "dap" 混着来)。
BACKENDS = (probe_jlink.BACKEND, probe_cmsis.BACKEND)

# 目标核身份的判据(两端共用同一颗核, 所以这个数只写一份)。
PROBE_CPUID_ADDR = probe_cmsis.PROBE_CPUID_ADDR
PROBE_CPUID = probe_cmsis.PROBE_CPUID


class ProbeSelectError(RuntimeError):
    """选不出探针时抛这个, 带人话解释与候选清单。"""


def _declared_backend():
    """卡带声明用哪一端。None = 两端都找。**声明不认识的名字当场抛** —— 拼错一个字母
    就退化成"自动找", 那正是本模块要消灭的静默猜测。"""
    want = machspec.get("PROBE")
    if want is None:
        return None
    want = str(want).strip().lower()
    if want not in BACKENDS:
        msg = ("卡带 `PROBE` 写的是 %r, 不认识。只能写: %s(或 None = 自动找两端)。"
               % (want, " / ".join(BACKENDS)))
        faultlog.record("PROBE-SELECT", subsystem="probe", text=msg,
                   snapshot={"PROBE": want, "可用": list(BACKENDS)},
                   next_step="改 machine/<机器名>.py 的 PROBE 字段")
        raise ProbeSelectError(msg)
    return want


def _pin(backend):
    """这一端在卡带里的"钉死"字段(None = 该端恰好一支才认)。"""
    return machspec.get("JLINK_SN" if backend == probe_jlink.BACKEND else "DAP_UID")


def candidates(declared=None):
    """枚举候选 → `[{"backend":…, "ident":…, "desc":…}, …]`。

    `declared` = 用哪一端; 不传 ⇒ 问卡带。

    第二个返回值是**过程记账**(不是"错误清单"): 扫描过的每一端都要留下一行, 包括
    "枚举到 0 支"和"因为卡带钉死另一端, 压根没看它"。空着的记账是**看不出来**的 ——
    一份没有任何行的理由, 与"这一段根本没跑"长得一模一样, 那是本仓最防的一类错觉。
    """
    if declared is None:
        declared = _declared_backend()
    out, notes = [], []
    pinned_out = []
    jlink_enum = 0               # jlink 端**过钉子之前**枚举到的支数。下面那句"要不要再去问
                                 # CMSIS-DAP 那一端"只看它 —— 问它的代价见那段说明。
    seen = 0                     # 各端**枚举到的原始支数**之和(过钉子之前)。只看 `out` 分不出
                                 # "一支都没有"与"有, 但被卡带的钉子排除了" —— 那两件事的处置
                                 # 完全相反(一个要插探针, 一个要改卡带), 台账里也不能并成一栏。
    for be in BACKENDS:
        if declared is not None and be != declared:
            notes.append("%s 端: 没看(卡带 `PROBE` 钉死了用 %s 端)" % (be, declared))
            continue
        # ⚠ 卡带没指名两端时, **jlink 端已经枚举到探针就不再问 CMSIS-DAP 那一端**: 那一端的产出
        #   只有一行"滤掉一支不是 CMSIS-DAP 的探针"(本台恒定如此), 对"这一趟能不能连上"零信息量,
        #   却要在进程里多常驻一个 pylink 实例(pyOCD 的 `JLinkProbe._get_jlink` 建了它就不关)。
        #   ⚠ 但**别把这一刀当成"连不上"的解药**: 2026-09-21 查过 pyOCD 源码, 它的枚举只到
        #   `connected_emulators()`(列 USB), **不打开具体哪支探针** —— "两支实例抢探针"这个说法
        #   查无实据。探针被撂进"枚举得到却连不上"那个坏态的原因, 至今未证。
        if declared is None and be != probe_jlink.BACKEND and jlink_enum:
            notes.append("%s 端: 没看(jlink 端已枚举到 %d 支 ⇒ 再问它就得让 pyOCD 在同进程里"
                         "开一次那支 J-Link; 卡带 `PROBE` 指名这一端时才问)" % (be, jlink_enum))
            continue
        try:
            found = (probe_jlink.list_probes() if be == probe_jlink.BACKEND else probe_cmsis.list_probes())
        except Exception as exc:
            notes.append("%s 端: 枚举本身跑不了 —— %s" % (be, exc))
            # 枚举层自己报错 ≠ 没插探针: 前者是驱动/DLL 的事, 后者是台面的事, 处置完全不同。
            # 这两个码分开写在这里, 就是为了让台账不把"驱动坏了"记成"没插探针"。
            faultlog.record("PROBE-ENUM", subsystem="probe", exc=exc,
                       snapshot={"backend": be},
                       next_step="J-Link 端 python -m swdbg.jlink --doctor")
            continue
        notes.append("%s 端: 枚举到 %d 支%s"
                     % (be, len(found), ("" if found else "(探针不在 USB 上, 或全被别的进程占着)")))
        seen += len(found)
        if be == probe_jlink.BACKEND:
            jlink_enum = len(found)
        if be != probe_jlink.BACKEND:
            # pyOCD 的枚举会**把 J-Link 一起报回来**(它原生支持)。那支已在 `jlink 端`里,
            # 这里滤掉了 —— 必须说出来: 「枚举到 0 支」与「插着一支 J-Link 却来问这一端」
            # 是两件事, 前者要插探针, 后者要改卡带的 `PROBE`。静默滤掉会让人对着
            # "0 支"去插一根早就插好的线。
            for _s in probe_cmsis.skipped_last():
                notes.append("%s 端: 滤掉一支**不是 CMSIS-DAP** 的探针(%s) —— pyOCD 自己也认 "
                             "J-Link, 那一支该走 `jlink 端`。这是选错了端, 不是没插探针。"
                             % (be, _s))
        mine = []
        for p in found:
            mine.append({"backend": be,
                         "ident": int(p["sn"]) if be == probe_jlink.BACKEND else str(p["uid"]),
                         "desc": ("SN=%d" % p["sn"]) if be == probe_jlink.BACKEND
                                 else ("%s(%s)" % (p["uid"], p["desc"] or p["product"]))})
        # ★ 每一端各自的"钉死"字段: 声明了 SN/UID, 就**只留那一支** —— 剩下的不是"也考虑一下",
        #   而是"钉死了别人"。旧 `jlink.resolve_sn` 遇到这种情况是当场抛; 双端之后这里改成
        #   **剔除 + 记账**, 由"最后还剩几个候选"决定抛不抛(见 `pick`): 卡带 `PROBE=None`
        #   自动找两端时, 这一端没有、另一端有, 就该用另一端, 不该因为一端的钉子没插上而全盘失败。
        want = _pin(be)
        if want is not None:
            kept = [c for c in mine if str(c["ident"]) == str(want)]
            # ⚠ 只有"这一端确实插着别的探针"才叫**排除**。一支都没插时钉子落空是正常的
            #   (上面那行枚举记账已经说了"0 支"), 再喊一遍"不是它"只是噪声 —— 而噪声会让人
            #   开始忽略这一整段。钉子的作用是把"插着的别的探针"挡在外面, 不是骂机器没插。
            if not kept and mine:
                notes.append("卡带把 %s 端钉死成 %s, 但当前连着的**不是它**: %s —— "
                             "已按钉子把它排除在外(不静默换一支)"
                             % (be, want, _list(mine)))
                pinned_out.append("%s 端钉死 %s, 而连着的是 %s" % (be, want, _list(mine)))
            mine = kept
        out.extend(mine)

    # 一支候选都不剩 ⇒ 记一笔。**判在结论处, 不在每一端**: 卡带 `PROBE=None` 时"某一端 0 支"
    # 是常事(另一端有就够), 每次成功的跑都记一笔的话, 台账会被这种噪声灌满, 而灌满的台账
    # 等于没有台账。只有"最后真的一支都不剩"才是这次跑不动的根因。
    if not out:
        if seen == 0:
            faultlog.record("PROBE-ABSENT", subsystem="probe",
                       text="两端都没枚举到探针(看过的每一端都是 0 支)",
                       tried=notes, snapshot={"declared": declared},
                       next_step="插上探针; 若它插着却看不见, 查 USB 设备树而不是改卡带")
        else:
            faultlog.record("PROBE-SELECT", subsystem="probe",
                       text="枚举到探针, 但全被卡带的钉子排除在外",
                       tried=notes + pinned_out,
                       snapshot={"declared": declared, "枚举到的支数": seen},
                       next_step="改卡带的 JLINK_SN / DAP_UID, 或插上它点名的那一支")
    return out, notes


def _list(cands):
    if not cands:
        return ""
    return " ".join("%s:%s" % (c["backend"], c["desc"]) for c in cands)


def make_driver(backend, ident, speed=None, device=None, iface=None, trace=None):
    """按端造驱动。**造驱动不等于连** —— `open()` 才连。"""
    if backend == probe_jlink.BACKEND:
        return probe_jlink.JLinkDriver(serial_no=(probe_jlink.FROM_CARD if ident is None else ident),
                              device=device, speed=speed, iface=iface, trace=trace)
    if backend == probe_cmsis.BACKEND:
        return probe_cmsis.CmsisDapDriver(unique_id=ident, speed=speed, trace=trace)
    raise ProbeSelectError("不认识的探针端 %r" % (backend,))


def verify(backend, ident, speed=None, device=None, iface=None, trace=None):
    """**证明**这个候选后面就是本表那颗核 → `(ok, 说明)`。

    真开一次最小会话、读 CPUID、关掉。不开就不算证明 —— 枚举层答不了这一问。
    本函数**不抛**连接异常: 连不上是一个"没证明成功"的结果, 由调用方连原因一起报出去
    (抛掉的话, 多候选时后面那些候选就没机会被证明了)。
    """
    drv = make_driver(backend, ident, speed=speed, device=device, iface=iface, trace=trace)
    close_error = None
    try:
        drv.open()
        got = drv.cpu_id()
    except Exception as exc:
        # ⚠ `splitlines()[0][:160]` 这一刀只许切**给人看的那一行**。原文整串进台账 —— 2026-09-20
        #   白盒死掉那趟, 断在多行异常的第二行上的 `(原错误: …)` 就是这么没进日志的, 事后只能靠嘴对。
        faultlog.record(exc=exc, subsystem="probe",
                   tried=["开一次最小会话读 CPUID 0x%08X" % PROBE_CPUID],
                   snapshot={"backend": backend, "ident": str(ident), "speed": speed,
                             "device": device},
                   next_step="python -m swdbg.probesel --doctor")
        return False, "连不上/读不到: %s" % str(exc).strip().splitlines()[0][:160]
    finally:
        try:
            if drv.close() is False:
                close_error = getattr(drv, "close_error", None) or "驱动返回失败"
        except Exception as exc:
            close_error = exc
    # 读到 CPUID 但没有确认会话已释放，不能把这个候选交给下一场；否则它可能仍占着
    # 探针，下一次 open 反而报“通信超时/被占用”。验证连接的关闭也是验证契约的一部分。
    if close_error is not None:
        faultlog.record("PROBE-LINK", subsystem="probe", exc=close_error,
                   tried=["CPUID 证明后的会话关闭"],
                   snapshot={"backend": backend, "ident": str(ident)},
                   next_step="确认没有其他进程占用探针后重试")
        return False, "CPUID 已读到但会话关闭失败: %s" % str(close_error).strip().splitlines()[0][:160]
    if got == PROBE_CPUID:
        return True, "CPUID=0x%08X 命中" % got
    return False, "CPUID=0x%08X 不是本表那颗核(要 0x%08X)" % (got, PROBE_CPUID)


def pick(declared=None, speed=None, device=None, iface=None, trace=None,
         verify_target=True):
    """选出一支可用的探针 → `(backend, ident, 逐条说明)`。选不出就抛。

    说明里逐条记了每个候选**为什么没被用上**。`verify_target=True` 时会逐个真连读
    CPUID；生产路径传 False 时，唯一候选直接交给正式会话，避免重复 attach/detach。
    """
    if speed is None:
        speed = machspec.get("SPEED")
    if device is None:
        device = machspec.get("DEVICE")
    if iface is None:
        iface = machspec.get("IFACE")
    cands, issues = candidates(declared)
    report = list(issues)
    for c in cands:
        report.append("候选 %s:%s" % (c["backend"], c["desc"]))

    if not cands:
        raise ProbeSelectError(
            "没有可用的探针候选(可能是一支都没枚举到, 也可能是枚举到了但被卡带的钉子排除了 —— "
            "看下面逐条)。\n"
            + "\n".join("  · %s" % r for r in report)
            + "\n查法: J-Link 端 `python -m swdbg.jlink --doctor`; "
              "CMSIS-DAP 端看 USB(灯亮不亮)+ 有没有别的进程占着它。\n"
              "⚠ 若探针**插着却枚举不到**, 先看它是不是掉过 USB(见 jlink --doctor 那一套)。")

    # 普通生产会话不应先 attach/detach 一次再把同一支探针交给 GDB
    # server/直读驱动。唯一候选时，正式会话本身就是连接证明；多候选仍必须
    # 真读 CPUID 后再选，避免把“省一次连接”变成静默猜探针。
    if not verify_target:
        if len(cands) == 1:
            c = cands[0]
            report.append("  目标证明: 跳过预连接(唯一候选; 由正式会话完成唯一一次 attach)")
            return c["backend"], c["ident"], report
        msg = ("枚举到 %d 支探针, 生产连接不允许在未证明时猜测; "
               "请指定 PROBE + JLINK_SN / DAP_UID, 或用 --doctor 做 CPUID 证明。\n"
               % len(cands) + "\n".join("  · %s" % r for r in report))
        faultlog.record("PROBE-SELECT", subsystem="probe", text=msg,
                   tried=report, snapshot={"候选数": len(cands)},
                   next_step="指定 machine/<机器名>.py 的 PROBE 与 JLINK_SN / DAP_UID")
        raise ProbeSelectError(msg)

    proven = []
    for c in cands:
        ok, why = verify(c["backend"], c["ident"], speed=speed, device=device,
                         iface=iface, trace=trace)
        report.append("  证明 %s:%s → %s" % (c["backend"], c["desc"], why))
        if ok:
            proven.append(c)

    if len(proven) == 1:
        c = proven[0]
        return c["backend"], c["ident"], report
    if not proven:
        msg = ("枚举到 %d 支探针, 但**没有一支能证明它后面是本表**(读 CPUID 要 0x%08X)。\n"
               % (len(cands), PROBE_CPUID)
               + "\n".join("  · %s" % r for r in report)
               + "\n枚举得到只说明 USB 那头是活的; VTref 读得到只说明 **VTref/GND 那一对**是通的 ——\n"
                 "**SWDIO/SWCLK 通不通, 这两个读数一个字都没说**。所以下面两支**都还没排除**:\n"
                 "① 线: SWDIO / SWCLK 有没有压实(杜邦线搭着不算) —— 表已下电重上过就**先查这一支**;\n"
                 "② 表: 固件压住了调试口。驱动在普通连接失败后已经自动试过一次**复位下连接**\n"
                 "(会复位表一次, 日志里有 warn 行), 这一刀也没进去; 表若没真断过电, 这一支就还在。\n"
                 "手边可做的: 按探针的 RESET 引脚(⚠ 探针 RESET 脚没接到表上时, 上面那一刀等于没试),\n"
                 "或手工来一次: JLink.exe 里写 `si SWD` / `speed 1000` / `device Cortex-M0` /\n"
                 "`RSetType 3` / `connect`。\n"
                 "⚠ 把 USB 设备节点禁用+启用(重新枚举)**治不了这条** —— 2026-09-20 连做两次都没治, "
                 "别把它当第一刀。")
        # 这一条是**结论**, 不是根因(根因已由上面的 candidates 逐端记过)。两笔都要有:
        # 根因那笔说的是"哪一端出了什么事", 这笔说的是"所以这一趟用不了探针", 复盘时先看这笔。
        faultlog.record("PROBE-LINK", subsystem="probe", text=msg, tried=report,
                   snapshot={"候选数": len(cands), "speed": speed, "device": device},
                   next_step="python -m swdbg.jlink --doctor")
        raise ProbeSelectError(msg)
    msg = ("有 %d 支探针都证明了自己后面是本表, 而卡带没指定用哪一支:\n" % len(proven)
           + "\n".join("  · %s:%s" % (c["backend"], c["desc"]) for c in proven)
           + "\n→ 在 `machine/<机器名>.py` 里点名: `PROBE` 选哪一端, "
             "再用 `JLINK_SN` / `DAP_UID` 选那一端的哪一支(不点名就不猜)。")
    faultlog.record("PROBE-SELECT", subsystem="probe", text=msg, tried=report,
               snapshot={"候选数": len(cands), "证明通过": len(proven)},
               next_step="在 machine/<机器名>.py 里点名 PROBE + JLINK_SN / DAP_UID")
    raise ProbeSelectError(msg)


def doctor(declared=None, speed=None, log=print):
    """打印一份"探针选得出来吗"的完整诊断。返回退出码(0 = 可用)。

    这是**双探针**的体检入口 —— `python -m swdbg.jlink --doctor` 只管 J-Link 那一端。
    """
    log("== 探针自动识别诊断 ==")
    log("  卡带判据: PROBE=%r  JLINK_SN=%r  DAP_UID=%r"
        % (machspec.get("PROBE"), machspec.get("JLINK_SN"), machspec.get("DAP_UID")))
    try:
        be, ident, report = pick(declared=declared, speed=speed)
    except ProbeSelectError as exc:
        for line in str(exc).splitlines():
            log("  %s" % line)
        return 2
    for line in report:
        log("  · %s" % line)
    log("  选中: %s 端, %s" % (be, ident))
    return 0


def main(argv=None):
    """CLI: 只做真探针的事 —— `--doctor` 报"选得出/选不出、为什么"。"""
    import argparse
    from common.console import ensure_utf8_stdout
    ensure_utf8_stdout()
    ap = argparse.ArgumentParser(
        prog="python -m swdbg.probesel",
        description="探针自动识别: 两支都插时选哪一支(选不出会说清为什么)")
    ap.add_argument("--doctor", action="store_true", help="报候选、逐个证明、给结论")
    a = ap.parse_args(argv)
    if a.doctor:
        return doctor()
    ap.print_help()                    # 没有离线自检可跑: 不给参数就打用法
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
