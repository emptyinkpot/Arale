# -*- coding: utf-8 -*-
"""
swdbg/jlink.py —— 「该用哪支 J-Link」的**唯一解析器**(枚举 + 判据, 不猜)。

为什么要有它
------------
`JLINK_SN` 原先在 `machine/win11_c07751.py` 里是个**死值**(`609788888`), 由 breakpoint / probe 直接
用 —— 换一支 J-Link 就静默连错(或者连不上), 而"连不上"的现象与"表挂了/线断了"长得一样。
本模块把那个值降级成**判据**: 卡带声明是哪一支就找哪一支, 没声明就要求"恰好一支"。

**为什么不放 common/**
`common/` 是零依赖中立层, 而本模块要 `pylink`(重依赖)。判据是机器事实、机制是 pylink 调用,
两者都属 swdbg(J-Link 通路自己的事)。方向仍是 `swdbg → common.machspec`, 不是 `swdbg → machine`。

**为什么不用 JLink.exe / JLinkGDBServerCL 来枚举**
`pylink.JLink().connected_emulators()` 走的是 `JLINKARM_EMU_GetList`(纯 USB 枚举), **不开会话、
不碰目标、不占探针** —— 枚举完进程就退。而 `JLink.exe -CommanderScript` 要真连一次, 那会与随后
要起的 GDB Server 抢探针。枚举就该是枚举。

⚠ 枚举**必须先构造 `pylink.JLink()`**(它在这一步载入 JLinkARM.dll)。直接在裸 `Library()` 上找
  `JLINKARM_EMU_GetList` 会 `AttributeError` —— 2026-09-14 实踩。

判据语义(与串口侧 `common/portsel` 同款, 刻意对称)
--------------------------------------------------
    声明了 SN  → 必须找到它; 找不到就抛, 并把**实际连着的**列出来
    没声明     → **恰好一支**才认; 0 支 / 多支都抛(多支时把 SN 列出来让人选)
**绝不"取第一个"** —— 那会把"确定错"换成"看着一样错"。
"""
import argparse

from common.probe_guard import acquire


# 机器条件(声明里那一支 SN)的取用入口。模块级读一次 —— 与 probe/breakpoint 同一形状:
# 没装卡带时是 None, import 照旧能(只是不报错), 真去连表时才报错。
from common import machspec
from common import winpnp

__all__ = ["JLinkError", "FROM_CARD", "list_probes", "resolve_sn",
           "PROBE_VID", "diagnose", "diagnose_brief", "plan_recovery", "recover",
           "doctor", "main"]

# SEGGER 的 USB 厂商号 —— 这是**通用事实**(每一支 J-Link 都挂在这个 VID 下), 不是这台机器的
# 条件, 所以它不进装机卡带。
PROBE_VID = "1366"


class JLinkError(RuntimeError):
    """枚举不到 / 判据不命中时抛这个, 带人话解释。"""


def _load_pylink():
    try:
        import pylink
        return pylink
    except Exception as exc:                      # pragma: no cover - 环境问题
        raise JLinkError(
            "没装 pylink-square(枚举 J-Link 要用它)。装法:\n"
            "    pip install pylink-square -i https://pypi.tuna.tsinghua.edu.cn/simple\n"
            "(原错误: %s)" % exc)


def list_probes():
    """枚举**当前连着**的 J-Link → `[{"sn": int, "usb_addr": int, "conn": int}, …]`。

    纯 USB 枚举, 不开会话、不碰目标、不占探针(见模块头)。枚举不到 = `[]`(「没插探针」是
    合法状态, 不是错误); 枚举**本身失败**(DLL 载不进来等) 才抛 `JLinkError`。
    """
    # connected_emulators() 也会进入 JLinkARM.dll；它不是无副作用的目录查询。
    # 与真实会话共用跨进程锁，避免 doctor/选探针和正在工作的会话同时碰 DLL。
    with acquire("jlink-enumeration"):
        pylink = _load_pylink()
        jl = None
        try:
            jl = pylink.JLink()
            infos = jl.connected_emulators()
        except Exception as exc:
            raise JLinkError(
                "J-Link 枚举失败(JLinkARM.dll 可能没装好, 或 SEGGER 驱动不在)。\n"
                "手查: 拿 JLink.exe 手动敲 `-CommanderScript` 看能不能列出探针。\n"
                "(原错误: %s)" % exc)
        finally:
            if jl is not None:
                try:
                    jl.close()
                except Exception:
                    # 枚举句柄关闭失败不能伪装成“无探针”。调用者会看到本次
                    # 仍完成了枚举，但后续会话会被 file lease 串行化。
                    pass
    out = []
    for i in infos:
        out.append({"sn": int(i.SerialNumber),
                    "usb_addr": int(getattr(i, "USBAddr", 0) or 0),
                    "conn": int(getattr(i, "Connection", 0) or 0)})
    return out


def _fmt(probes):
    """候选清单 → 一行给人看的串。空表给『(无)』—— 别打一个空字符串让人以为打印坏了。"""
    if not probes:
        return "(无)"
    return " ".join("SN=%d" % p["sn"] for p in probes)


FROM_CARD = object()       # 哨兵: "没传 declared ⇒ 去问卡带"。**不能用 None** —— 卡带里
                           # `JLINK_SN = None` 本身就是合法声明(自动), 两者必须能分开。
                           # 公开: breakpoint/probe 的 `serial_no=` 默认值就用它。

VTREF_MIN_MV = 1500        # 目标参考电压(mV)的**判活门限**。本表实测 3300 上下(2026-09-20
                           # 量到 3327~3332), 取 1500 留足余量: 3.3V 表掉到 1.5V 以下已经不能叫
                           # "有电", 那种读数就该去查线。它只用来分一件事 —— **VTref 有电而连接
                           # 失败 ⇒ 排线里 VTref/GND 是通的, 卡的是表那头, 别再叫人去重接一根好线**。
                           # 用它的地方: `probe_jlink.JLinkDriver.open()`。


def resolve_sn(declared=FROM_CARD, probes=None):
    """按判据拿"该用哪支 J-Link"的序列号(int)。见模块头「判据语义」。

    `declared` = 卡带声明的那一支; **不传** ⇒ 现问 `common.machspec`(卡带的 `JLINK_SN`)。
                 显式传 None = 强制自动(恰好一支才认), 不看卡带 —— 调用方覆盖用。
    `probes`   = 注入用(调用方喂现成清单, 不真连探针); None ⇒ 现枚举。
    """
    if declared is FROM_CARD:
        declared = machspec.get("JLINK_SN")            # 没显式传 → 问卡带; 卡带也没有 → 仍是 None
    if probes is None:
        probes = list_probes()

    if declared is not None:
        want = int(declared)
        if any(p["sn"] == want for p in probes):
            return want
        raise JLinkError(
            "卡带声明用 J-Link SN=%d, 但当前连着的**不是它**: %s\n"
            "①换回那支探针; ②或改 `machine/<机器名>.py` 的 `JLINK_SN`; "
            "③或把它置 None = 自动(恰好一支才认)。" % (want, _fmt(probes)))

    if len(probes) == 1:
        return probes[0]["sn"]
    if not probes:
        raise JLinkError(
            "没枚举到任何 J-Link。检查: ①USB 线插好没有(灯亮不亮); "
            "②残留的 JLinkGDBServerCL.exe / JLink.exe 有没有占着它。")
    raise JLinkError(
        "连着 %d 支 J-Link, 而卡带没声明用哪一支: %s\n"
        "→ 在 `machine/<机器名>.py` 的 `JLINK_SN` 里点名一支(不点名就不猜)。"
        % (len(probes), _fmt(probes)))


# ===========================================================================
# 二、探针体检与恢复 —— 「连不上」到底是什么坏了
# ===========================================================================
# 为什么要有这一段
# ----------------
# 2026-09-17 探针从 USB 上掉了下来, 而现场能被说出口的解释至少有三种(线没插好 / 驱动坏了 /
# 表挂了), 三种的处置完全不同。当时是靠临时敲 PowerShell 才定案的 —— 敲完就没了。
# 这里把那套判据固定成代码, 让"探针连不上"从一句猜测变成一条结论。
#
# 一条判据分成两层, 因为两层的代价差一个数量级:
#   · **枚举层**(`list_probes`, 几十毫秒): J-Link 自己的 DLL 看不看得见探针。
#     这一层就答得出"探针在不在 USB 上", 也是出错现场唯一该立刻做的检查。
#   · **设备树层**(`common.winpnp`, 数秒): Windows 认不认这个设备、有没有报错、
#     什么时候掉的 —— 答的是"**为什么**"。只在真要定案时(doctor / recover)才去查。
#
# ⚠ 本段**不结束任何进程**。探针掉线的元凶正是强杀握着 J-Link 的进程
#   (JLink.exe / JLinkGDBServerCL.exe / 经 pylink 打开 DLL 的 Python 进程),
#   所以恢复手段一律走设备节点(pnputil), 不走 taskkill —— 这是本模块的安全铁律:
#   恢复计划里**永远**不许出现"结束进程"这一类(见 `plan_recovery` 的 kind 只有
#   none/manual/scan/restart/cycle)。

# 状态 → 人话。键是稳定标识(给脚本/日志), 值是给人看的。
STATES = {
    "OK": "探针在, 枚举得到",
    "WRONG_PROBE": "枚举到的是**别的**探针",
    "COMM_BROKEN": "探针在, 枚举得到, 但连接/通信失败",
    "NODE_PRESENT_INVISIBLE": "Windows 认这个设备, 而 J-Link 枚举不到它",
    "PROBLEM_NODE": "设备节点在, 但 Windows 报它有问题",
    "ENUM_FAILED": "USB 占位节点在, 但设备描述符请求失败(Code 43)",
    "DROPPED": "探针**不在 USB 上**",
    "NO_USB_HISTORY": "设备树里没有这个 VID 的任何节点",
    "ENUM_UNAVAILABLE": "枚举本身跑不了(JLinkARM.dll / 驱动层)",
    "UNKNOWN": "查不清",
}


def _norm(probes):
    return sorted(int(p["sn"]) for p in (probes or []))


def _is_probe(n):
    """这个设备节点是不是"那种探针"。"""
    return ("VID_%s" % PROBE_VID) in (n.get("instance_id") or "").upper()


def _pick_instance(diag):
    """要动的是哪个设备节点 —— 先挑探针自己的节点, 再退到别的。"""
    nodes = diag.get("nodes") or []
    for group in ([n for n in nodes if _is_probe(n)], nodes):
        for n in group:
            if n["present"]:
                return n["instance_id"]
        if group:
            return group[0]["instance_id"]
    return None


def _declared_sn(declared):
    if declared is FROM_CARD:
        declared = machspec.get("JLINK_SN")
    return None if declared is None else int(declared)


def diagnose(declared=FROM_CARD, probes=None, nodes=None, connect_error=None, deep=True):
    """查清"探针到底怎么了"。返回一个 dict(不是布尔 —— 布尔答不了"然后呢")。

    参数(都可注入 —— 不注入就现查真探针/真设备树):
        declared       卡带声明的那一支; 不传 = 现问卡带
        probes         `list_probes()` 的结果; None = 现枚举
        nodes          设备树节点; None = 需要时现查(deep=False 时不查)
        connect_error  连接失败的原始文本(调用方抓到的异常)。它**只用来把
                       COMM_BROKEN 认下来**, 不能把"探针不在 USB 上"说成通信坏 ——
                       反过来说错会把人引去修错的东西。
        deep           True 才去查设备树(数秒)。出错现场先用 False 拿第一刀。

    返回键: `state` / `summary` / `evidence`(逐条事实) / `probes` / `nodes` / `issues`。
    """
    ev, issues = [], []
    want = _declared_sn(declared)

    # --- 第一层: 枚举 ---
    enum_err = None
    if probes is None:
        try:
            probes = list_probes()
        except JLinkError as exc:
            enum_err, probes = str(exc), []
    sns = _norm(probes)
    if enum_err:
        ev.append("J-Link 枚举跑不了: %s" % enum_err)

    # --- 第二层: 设备树(只在需要时) ---
    nodes_supplied = nodes is not None
    nodes = [] if nodes is None and not deep else nodes
    if nodes is None and deep:
        try:
            nodes = winpnp.devices(PROBE_VID)
        except Exception as exc:                     # 非 Windows / PowerShell 不可用
            issues.append("设备树查不了(%s) —— 下面的结论只看得到枚举那一层" % exc)
            nodes = []
    nodes = nodes or []
    # J-Link 真正掉线后，Windows 不一定再给它保留 VID_1366；常见形态是
    # 同一端口变成 VID_0000&PID_0002 / Code 43。把这个当前在位节点并入诊断，
    # 否则恢复程序会拿历史 VID_1366 实例去 restart，得到 RC=1167“设备不在”。
    if deep and not nodes_supplied:
        try:
            bad_usb = [n for n in winpnp.problems()
                       if "VID_0000&PID_0002" in (n.get("instance_id") or "").upper()
                       and int(n.get("problem") or 0) == 43]
            known = {n.get("instance_id") for n in nodes}
            nodes.extend(n for n in bad_usb if n.get("instance_id") not in known)
        except Exception as exc:
            issues.append("Code 43 占位节点查不了(%s)" % exc)
    sm = winpnp.summarize(nodes) if nodes else None
    for n in nodes:
        ev.append("设备树: %s" % winpnp.describe_node(n))
    if sm and sm["returned_bad"]:
        ev.append("指纹: 某节点移除后 %.0fs 上来一个 Problem=%d 的节点 ⇒ 同一个东西掉了又回"
                  % (sm["returned_bad"]["seconds"], sm["returned_bad"]["node"]["problem"]))

    # ⚠ `nodes` 是**整棵 USB 树的相关部分**: 既有这个 VID 的节点, 也可能有那个"掉了又回"的
    #   坏节点(它的 VID 与探针无关 —— 2026-09-17 那次就是个 `VID_0000&PID_0002`)。判"探针在不在"
    #   只许看**探针自己的**节点, 否则会把"旁边有个坏设备"误读成"探针坏着"。
    vid_present = [n for n in nodes if n["present"] and _is_probe(n)]

    # --- 定案(顺序即优先级) ---
    enum_failed_nodes = [n for n in nodes if n.get("problem") == 43 and
                         "VID_0000&PID_0002" in (n.get("instance_id") or "").upper()]
    if enum_err:
        state = "ENUM_UNAVAILABLE"
    elif enum_failed_nodes:
        state = "ENUM_FAILED"
    elif want is not None and sns and want not in sns:
        state = "WRONG_PROBE"
    elif connect_error and sns:
        state = "COMM_BROKEN"
    elif sns and (want is None or want in sns):
        state = "OK"
    elif not nodes and not deep:
        state = "UNKNOWN"                            # 浅查且枚举为空: 到此为止
    elif any(n["problem"] != 0 for n in vid_present):
        state = "PROBLEM_NODE"
    elif vid_present:
        state = "NODE_PRESENT_INVISIBLE"
    elif nodes:
        state = "DROPPED"
    else:
        state = "NO_USB_HISTORY" if deep else "UNKNOWN"

    # --- 补几行人话 ---
    extra = []
    if state == "OK":
        extra.append("连着: %s" % _fmt(probes))
    elif state == "WRONG_PROBE":
        extra.append("要的是 SN=%s, 而连着的是 %s —— **不自动换支**, 换不换由人定" % (want, _fmt(probes)))
    elif state == "COMM_BROKEN":
        extra.append("枚举层是活的(连着 %s) ⇒ 问题在通信层, 不在 USB 上" % _fmt(probes))
        extra.append("连接报的原话: %s" % str(connect_error).strip().splitlines()[0][:200])
    elif state == "NODE_PRESENT_INVISIBLE":
        extra.append("Windows 说这些节点没问题, J-Link 却看不见 ⇒ 病因在 DLL/驱动或被别的进程占着")
    elif state == "PROBLEM_NODE":
        bad = [n for n in vid_present if n["problem"] != 0][0]
        extra.append("节点 %s 报 Problem=%d(%s)" % (bad["instance_id"], bad["problem"],
                                                  bad["problem_desc"] or bad["status"]))
    elif state == "ENUM_FAILED":
        bad = enum_failed_nodes[0]
        extra.append("当前端口节点 %s 报 Code 43：设备描述符请求失败；这不是旧 VID_1366 节点可 restart 的状态"
                     % bad["instance_id"])
    elif state == "DROPPED":
        extra.append("探针不在 USB 上; 最后一次移除: %s"
                     % (sm["last_removal"].strftime("%Y-%m-%d %H:%M:%S") if sm and sm["last_removal"] else "看不到(非管理员会话常读不到历史节点)"))
        if sm and sm["returned_bad"]:
            extra.append("**别急着说『线没插好』** —— 设备树显示它是自己掉的(见上面那条指纹)")
    elif state == "NO_USB_HISTORY":
        extra.append("⚠ 非管理员会话读不到『幽灵节点』, 『没看到』不等于『从没接过』")

    return {"state": state, "summary": STATES.get(state, state),
            "evidence": ev + extra, "probes": probes, "nodes": nodes,
            "issues": issues, "want_sn": want}


def plan_recovery(diag):
    """由诊断结果算"该动什么" —— **只算不做**: 计划与实际动手分开, 计划可以先审。

    返回 `[{"kind":…, "why":…, "instance_id":…}, …]`。kind 取:
        none    不用动 / 不该自动动
        manual  需要人动手(物理重新插拔 / 换一支 / 修驱动)
        scan    `pnputil /scan-devices`(重新枚举)
        restart `pnputil /restart-device <id>`
        cycle   `pnputil /disable-device` + `/enable-device <id>`

    ⚠ 这里**没有** "kill" 这一类, 而且永远不会有 —— 见本段开头那段纪律。
    """
    st = diag["state"]
    inst = _pick_instance(diag)

    if st == "OK":
        return [{"kind": "none", "why": "枚举得到, 不用动", "instance_id": None}]
    if st == "WRONG_PROBE":
        return [{"kind": "manual", "why": "连着的是另一支探针; 换不换由人定(不静默换)",
                 "instance_id": None}]
    if st == "DROPPED":
        return [{"kind": "scan", "why": "先让 Windows 重新枚举一遍", "instance_id": inst},
                {"kind": "restart", "why": "重新枚举没找回来的话, 重启那个设备节点", "instance_id": inst},
                 {"kind": "manual", "why": "还不行就**物理重新插拔一次 USB** —— 探针要重新上电",
                  "instance_id": inst}]
    if st == "ENUM_FAILED":
        return [{"kind": "scan", "why": "先刷新设备树；不要对已消失的 VID_1366 历史节点 restart",
                 "instance_id": None},
                {"kind": "manual", "why": "Code 43 发生在 USB 描述符阶段；软件不能替代 VBUS 断电，需真正让端口掉电后再枚举",
                 "instance_id": None}]
    if st == "COMM_BROKEN":
        # ⚠ 这一支**不把"禁用+启用设备节点"排在前面**: 2026-09-20 实测连做两次, 节点重新枚举到
        #   了(到达时间每次都变), 而 connect 照样 Unspecified error —— 枚举那一层从来就不是病灶。
        #   治得了它的是**复位下连接**(连着复位去连, 表上跑着的固件会压住调试口), 正常会话里那一步
        #   由 `probe_jlink.JLinkDriver.open()` 在普通连接失败后**自动**走一次(会复位表, 所以留痕)。
        #   走到本函数说明那一刀也试过且没成 —— 这里给的是人动手的那一手。
        return [{"kind": "manual",
                 "why": "枚举得到而连不上 —— **两支都还没排除**: ①SWDIO/SWCLK 那两根线(枚举与 VTref "
                        "都证明不了它们通) ②表那头固件压住了调试口。**复位下连接**已由驱动自动试过"
                        "(见日志 warn 行)且没成, 所以驱动层已经没招了。人动手: 先压实 SWDIO / SWCLK, "
                        "再表下电重上, 或按探针的 RESET 引脚; 想手工再来一次就在 JLink.exe 里写 "
                        "`si SWD` / `speed 1000` / `device Cortex-M0` / `RSetType 3` / `connect`"
                        "(探针 RESET 脚没接到表上时, 这一步给不了复位)",
                 "instance_id": inst}]
    if st in ("PROBLEM_NODE", "NODE_PRESENT_INVISIBLE"):
        return [{"kind": "cycle", "why": "禁用+启用设备节点(实测只有这一样治得了通信层坏了)",
                 "instance_id": inst},
                {"kind": "manual", "why": "还不行就物理重新插拔一次 USB", "instance_id": inst}]
    if st == "ENUM_UNAVAILABLE":
        return [{"kind": "manual", "why": "枚举跑不了 ⇒ 先修 JLinkARM.dll / SEGGER 驱动, "
                                          "动设备节点没有用", "instance_id": None}]
    if st == "NO_USB_HISTORY":
        return [{"kind": "manual", "why": "设备树里没有这个 VID 的节点 ⇒ 先确认探针插在本机、"
                                          "驱动装了没有", "instance_id": None}]
    return [{"kind": "manual", "why": "查不清 ⇒ 先跑 --doctor 拿完整诊断", "instance_id": None}]


def diagnose_brief(connect_error=None, probes=None, vtarget=None):
    """**便宜版**诊断: 只做枚举那层, 不查设备树(几十毫秒)。给出错现场用。

    完整版是 `doctor()` / `--doctor`(要数秒)。这里答的是第一问: 「探针还在 USB 上吗」
    —— 这一问就能把"表挂了/线断了"这类猜测砍掉一大半。

    `vtarget` = 目标参考电压(mV), 调用方连不上时**顺手量到的那个数**, 读不到传 None。
    它把"枚举到了但连不上"再切一刀: 有电压 ⇒ VTref/GND 通着, **别往接线那条路上引**;
    没电压 ⇒ 那才轮到查线。这一刀是 2026-09-20 加的 —— 当时的原始报错里枚举、电压、
    连接异常三条都在手边, 却没有电压这一条, 于是人拿到的话术指向了错的地方。
    """
    try:
        if probes is None:
            probes = list_probes()
    except JLinkError as exc:
        return "探针诊断: J-Link 枚举本身跑不了 ⇒ %s" % exc
    if probes and connect_error:
        if vtarget is None or vtarget < VTREF_MIN_MV:
            # 电压读不到 / 低于门限 ⇒ VTref 那一路本身就可疑, 这一档才轮到查线。
            return ("探针诊断: 枚举到 %d 支探针(%s), 但连接失败, 且目标参考电压是 %s ⇒ "
                    "先怀疑**排线**: ①SWDIO / SWCLK 有没有压实(杜邦线搭着不算) ②GND 有没有接 "
                    "③VTref 有没有接。完整诊断: python -m swdbg.jlink --doctor"
                    % (len(probes), _fmt(probes),
                       "读不到" if vtarget is None else "%d mV(低于 %d)" % (vtarget, VTREF_MIN_MV)))
        return ("探针诊断: 枚举到 %d 支探针(%s), 但连接失败; 目标 **VTref=%d mV 有电** —— "
                "这只说明 **VTref/GND 那一对**接通了, **SWDIO/SWCLK 通不通它答不了**。"
                "所以两支都还在: ①SWDIO/SWCLK 有没有压实(杜邦线搭着不算) ②表上跑着的固件压住了"
                "调试口。驱动已经自动试过一次**复位下连接**且没成(见报错正文的 warn 行)。"
                "手边可做的: 表下电重上(真断过电 —— 只按复位键不算)、按探针的 RESET 引脚、"
                "压实 SWDIO/SWCLK。完整诊断: python -m swdbg.jlink --doctor"
                % (len(probes), _fmt(probes), vtarget))
    if not probes:
        return ("探针诊断: 一支都没枚举到 ⇒ 先别怀疑表和线, 查探针在不在 USB 上。"
                "完整诊断: python -m swdbg.jlink --doctor")
    return ""


def _reverify(declared):
    """动完设备节点之后的复验 —— 再枚举一次。"""
    try:
        return _norm(list_probes())
    except JLinkError:
        return None


def recover(declared=FROM_CARD, dry=False, deep=True, log=print):
    """按诊断结果把探针弄回来。返回 `(ok, diag)`。

    `dry=True` 只打印计划、不动设备节点。真动的那几步要管理员: 本函数会请一次 UAC
    (**用户取消 UAC 是正常结果**, 会如实报出来, 不当崩溃)。
    """
    d = diagnose(declared=declared, deep=deep)
    log("探针状态: %s —— %s" % (d["state"], d["summary"]))
    for line in d["evidence"]:
        log("  · %s" % line)
    plan = plan_recovery(d)
    log("恢复计划:")
    for a in plan:
        log("  [%s] %s%s" % (a["kind"], a["why"],
                            ("  <-- %s" % a["instance_id"]) if a["instance_id"] else ""))
    if dry or d["state"] in ("OK", "WRONG_PROBE"):
        return (d["state"] == "OK"), d

    for a in plan:
        if a["kind"] == "manual":
            log("  需要人动手: %s" % a["why"])
            break
        if not a["instance_id"]:
            log("  [%s] 没有可用的设备实例 ID, 跳过" % a["kind"])
            continue
        if a["kind"] == "scan":
            ok, text = winpnp.scan_devices()
        elif a["kind"] == "restart":
            ok, text = winpnp.restart_device(a["instance_id"])
        elif a["kind"] == "cycle":
            ok, text = winpnp.cycle_device(a["instance_id"])
        else:
            continue
        log("  [%s] %s: %s" % (a["kind"], "成功" if ok else "失败", (text or "").strip()[:300]))
        sns = _reverify(declared)
        if sns is None:
            log("  复验: 枚举跑不了")
        elif sns:
            log("  复验: 枚举到了 %s ⇒ 探针回来了" % " ".join("SN=%d" % s for s in sns))
            return True, diagnose(declared=declared, deep=False)
    ok = d["state"] == "OK"
    return ok, d


def doctor(declared=FROM_CARD, deep=True, log=print):
    """打印一份完整诊断。返回退出码(0 = 探针可用)。"""
    d = diagnose(declared=declared, deep=deep)
    log("== J-Link 探针诊断 ==")
    log("  结论: %s —— %s" % (d["state"], d["summary"]))
    log("  证据:")
    for line in d["evidence"] or ["(没有)"]:
        log("    · %s" % line)
    for line in d["issues"]:
        log("  ⚠ %s" % line)
    log("  该怎么办:")
    for a in plan_recovery(d):
        log("    [%s] %s" % (a["kind"], a["why"]))
    log("  (恢复: python -m swdbg.jlink --recover; 先看计划加 --dry)")
    return 0 if d["state"] == "OK" else 2


def main(argv=None):
    """CLI: 只做真探针的事 —— `--doctor` 查状态 / `--recover` 弄回来; 不给参数打用法。

    ⚠ 这里**没有** `--selftest` 这一类: 本仓不给离线/模拟入口, 结论只能来自实物。
    """
    from common.console import ensure_utf8_stdout
    ensure_utf8_stdout()
    ap = argparse.ArgumentParser(
        prog="python -m swdbg.jlink",
        description="J-Link 探针: 在不在、为什么不在、怎么弄回来")
    ap.add_argument("--doctor", action="store_true", help="查清探针状态(会查 Windows 设备树, 数秒)")
    ap.add_argument("--recover", action="store_true", help="按诊断把探针弄回来(要管理员, 会请一次 UAC)")
    ap.add_argument("--dry", action="store_true", help="配 --recover: 只打印计划, 不动设备节点")
    ap.add_argument("--shallow", action="store_true", help="只查枚举层(几十毫秒), 不查设备树")
    ap.add_argument("--json", action="store_true", help="诊断结果输出 JSON")
    a = ap.parse_args(argv)

    if a.recover:
        ok, d = recover(dry=a.dry, deep=not a.shallow)
        return 0 if ok else 2
    if a.doctor:
        d = diagnose(deep=not a.shallow)
        if a.json:
            import json
            print(json.dumps({k: v for k, v in d.items() if k != "nodes"},
                             ensure_ascii=False, indent=2))
            return 0 if d["state"] == "OK" else 2
        return doctor(deep=not a.shallow)
    ap.print_help()                    # 没有离线自检可跑了: 不给参数就打用法
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
