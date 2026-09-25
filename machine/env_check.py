# -*- coding: utf-8 -*-
"""
machine/env_check.py —— "装包即验": 断言当前装机卡带(machine/CURRENT)声明的条件在本机是不是真的。

环境 = **这台机器**可用的条件(工具在不在、gdb 带不带 Python、包装没装、串口在不在册、launch.json
跟卡带对不对得上), 不是给人读的知识。逐条断言成 (ok, msg), 全部 ok = "装了这包, 这台机器能开工"。
不教书, 只报'在不在'。

**刻意只做离线**: 本模块不 import swdbg / meterlib —— 它们反过来经 `common.machspec` 取本卡带,
本模块要是再 import 它们, 依赖就绕成了圈。真连探针的活是 `python -m swdbg.probesel --doctor` 的事,
见文件末尾打印的"下一步"。**判据是"声明与盘上一致", 不是"探针通"** —— 后者是活体检查, 各有各的判定。
(2026-09-20 更正: 这里原先写的是 `python -m swdbg.probe`, 而 `probe.py` 是**纯库、没有入口** ——
 拿 `-m` 跑它只会 import 一下静默退 0, 什么也没验。真入口是 `probesel --doctor`。)

用法(在 帧收发基础/ 下):
    python -m machine.env_check
退出码: 0=全 OK / 2=有缺项(与 project/env_check.py 同口径)。
"""
import os
import sys

# ---- 仓根(向上找含 src/ 的那一级), **只用来定位文件** ----
_p = os.path.dirname(os.path.abspath(__file__))
while not os.path.isdir(os.path.join(_p, "src")) and os.path.dirname(_p) != _p:
    _p = os.path.dirname(_p)

_ROOT = _p

# 本包**不许**依赖的四家(见 machine/__init__.py 的依赖方向): 谁越界谁把轴拆错了。
_FORBIDDEN = ("meterlib", "swdbg", "discover", "project")


def _meta_path(P):
    """本卡带的 meta 清单路径 —— 与 `project/firmware.meta_path` 同款(画像名 + 目录)。"""
    return os.path.join(os.path.dirname(os.path.abspath(P.__file__)), P.__name__.split(".")[-1] + ".meta.json")


def _require_attrs(P):
    need = ["MACHINE", "OS", "GDB", "GDB_SERVER", "JLINK", "ARM_TOOLCHAIN", "JLINK_SN",
            "DEVICE", "IFACE", "SPEED", "GDB_PORT", "COM", "COM_MATCH", "BAUD", "PARITY",
            "MENGXI_ROOT", "MANAGE_CHIP_DIR", "METER_CHIP_DIR", "HAS_JLINK",
            # 探针两端(2026-09-20 起): 少了任何一项, 真连表时 `probesel` 会当场抛 —— 在这儿先点名。
            "PROBE", "DAP_UID", "DAP_TARGET", "DAP_VIDPID"]
    return [(n, hasattr(P, n)) for n in need]


# 发行名 → import 名。两边写法不一致的必须列(pyserial 的模块叫 serial); 其余也一律明写 ——
# "猜得出来"是发行方的事, 猜错的那天报的是"没装", 比不报还坏。
_IMPORT_NAME = {
    "pyserial": "serial",
    "pyelftools": "elftools",
    "pylink-square": "pylink",
    "pyocd": "pyocd",
    "openpyxl": "openpyxl",
    "zstandard": "zstandard",
}


def _declared_deps(root):
    """从仓根 `pyproject.toml` 读依赖声明 → (基础依赖, {可选组: [依赖]})。

    **不 import 那个文件**(它不是 .py): pip / setuptools 也是当数据解析的, 本模块照办。所以这里
    用 tomllib 而不是"import 一下取变量" —— 后者从一开始就不成立。
    """
    import tomllib
    with open(os.path.join(root, "pyproject.toml"), "rb") as fh:
        t = tomllib.load(fh)
    proj = t.get("project") or {}
    return (list(proj.get("dependencies") or []),
            {k: list(v) for k, v in (proj.get("optional-dependencies") or {}).items()})


def _which_extras(P):
    """这一趟**该**装哪些可选组 —— 判据取自卡带, 不在体检里另立一套。

    基础依赖恒要; 两个探针组按 `HAS_JLINK` 与 `PROBE` 判 —— 台上只插 DAPLink 却报"pylink 没装"
    是假警报, 而假警报会让人开始无视体检。
    """
    want = {"install"}                       # scripts/_install_gdb.py 解 MSYS2 包时要, 装了不亏
    if P.HAS_JLINK and P.PROBE in (None, "jlink"):
        want.add("jlink")
    if P.PROBE in (None, "cmsis-dap"):
        want.add("cmsis-dap")
    return want


def _deps_section(P, root):
    """5) Python 包 —— 清单来自仓根 `pyproject.toml`, 不再来自卡带(2026-09-20 改)。

    "装了没"问 `importlib.metadata`(**看的正是 pip 装的那个东西**), 再对写得出模块名的补一次
    import —— 发行包装着而模块 import 不进来, 是另一类病, 值得分开报。
    """
    out = []
    try:
        base, extras = _declared_deps(root)
    except Exception as e:
        return [(False, "读不到仓根 pyproject.toml 的依赖声明: %s" % e)]

    want = _which_extras(P)
    todo = [(".", d) for d in base]
    for grp in sorted(extras):
        if grp in want:
            todo += [(".%s" % grp, d) for d in extras[grp]]

    import importlib
    import importlib.metadata
    for tag, spec in todo:
        # 声明里可能带版本约束("pyserial>=3.5")或 extra; 取名字那一段。
        name = spec.split("[")[0].split(">=")[0].split("==")[0].split("<")[0].split(";")[0].strip()
        how = 'pip install -e ".[%s]"' % "+".join(want) if tag != "." else "pip install -e ."
        try:
            ver = importlib.metadata.version(name)
        except Exception:
            out.append((False, "python 包 %s %s **没装** —— 装法: %s" % (tag, name, how)))
            continue
        mod = _IMPORT_NAME.get(name)
        if not mod:
            out.append((True, "python 包 %s %s 在 (%s)" % (tag, name, ver)))
            continue
        try:
            importlib.import_module(mod)
            out.append((True, "python 包 %s %s %s 在 (模块 %s 进得来)" % (tag, name, ver, mod)))
        except Exception as e:
            out.append((False, "python 包 %s %s %s 装了, 但模块 %s **import 不进来**: %s"
                        % (tag, name, ver, mod, e)))
    return out


# 探针的两端。**这是卡带字段值的语法**, 与 `swdbg/probesel.py` 的 `BACKENDS` 必须一致 ——
# 本包不许 import swdbg(见第 9 节铁律), 所以只能在这儿重述一遍; 不一致时体检会点名。
_ENDS = ("jlink", "cmsis-dap")


def _end_label(P, backend):
    """这一端在报告里的写法, 顺带把厂家号写出来 —— 探针出事时第一问就是"它还在 USB 上吗"，
    而那一问只有厂家号问得动(见 `_probe_why`)。"""
    if backend == "jlink":
        return "J-Link(VID 1366)"
    vp = getattr(P, "DAP_VIDPID", None)
    if vp:
        return "CMSIS-DAP(VID %04X:PID %04X)" % (int(vp[0]), int(vp[1]))
    return "CMSIS-DAP"


def _end_vid(P, backend):
    """这一端在设备树里查哪个厂家号(4 位十六进制, 见 winpnp.devices)。"""
    if backend == "jlink":
        return "1366"
    vp = getattr(P, "DAP_VIDPID", None)
    return ("%04X" % int(vp[0])) if vp else ""


def _probe_why(backend, vid):
    """某一端**枚举不到**时, 去 Windows 设备树里把"为什么"问出来 → 一串人话(拿不到就给一句说明)。

    ⚠ 走中立层 `common.winpnp`(**不是** swdbg): 本包不许 import swdbg, 而这一层零协议零画像,
    本来就该两边共用。这一段**只用只读查询**, 一个设备节点都不动 —— 要动是
    `python -m swdbg.jlink --recover` 的事(它要管理员, 得当着一个知道自己在干什么的人跑)。

    ⚠ 这一层**与探针是哪一端无关**: 设备树只认"某个 VID 的节点在不在位、坏没坏、什么时候掉的",
    所以两端共用同一段查询, 只是喂进去的厂家号不同。
    """
    if not vid:
        return ["卡带没声明这一端的厂家号(`DAP_VIDPID`), 设备树查不了"]
    try:
        from common import winpnp
    except Exception as e:
        return ["设备树查不了(common.winpnp 取不到: %s)" % e]
    try:
        nodes = winpnp.devices(vid)
    except Exception as e:
        return ["设备树查不了(%s)" % e]
    if not nodes:
        return ["设备树里没有 VID_%s 的任何节点 —— 这一端的探针插在本机没有? 驱动装了没有?"
                "(⚠ 非管理员会话读不到历史节点, 『没看到』不等于『从没接过』)" % vid]
    lines = [winpnp.describe_node(n) for n in nodes]
    sm = winpnp.summarize(nodes)
    if sm["returned_bad"]:
        lines.append("指纹: 移除后 %.0fs 上来一个 Problem=%d 的节点 ⇒ 同一个东西掉了又回"
                     % (sm["returned_bad"]["seconds"], sm["returned_bad"]["node"]["problem"]))
        lines.append("⇒ 别急着怪线 —— 设备树说它是自己掉的")
    lines.append("再往下查: %s" % ("python -m swdbg.jlink --doctor" if backend == "jlink"
                                   else "python -m swdbg.probesel --doctor"))
    return lines


def _enumerate(backend):
    """枚举**当前连着**的这一端的探针 → 身份串列表。枚举本身跑不了(库没装/驱动坏了)时抛。

    ⚠ **直接用 pylink / pyocd**, 不经 `swdbg.*` —— 本包不许 import swdbg(第 9 节铁律),
      与第 4 节"gdb 三连"直接用 subprocess 问 gdb 同理: **体检复算, 不借被测方的手**。
    ⚠ 只枚举: 不开会话、不读 CPUID、不碰目标(见文件头"刻意只做离线")。
    """
    if backend == "jlink":
        import pylink
        return ["%d" % int(i.SerialNumber) for i in pylink.JLink().connected_emulators()]
    # CMSIS-DAP 端 —— ⚠ 用 `get_all_connected_probes` 而**不是** `choose_probe`: 后者在不止一支
    #   时是**交互式**的(会等人敲键盘), 在这种被脚本调用的地方等于挂死(与 probe_cmsis 同款)。
    import logging
    from pyocd.core.helpers import ConnectHelper
    for name in ("pyocd", "pyocd.core", "pyocd.probe"):
        logging.getLogger(name).setLevel(logging.ERROR)
    return [str(p.unique_id) for p in ConnectHelper.get_all_connected_probes(blocking=False)]


def _probe_section(P):
    """第 6b 节: 探针(**离线枚举, 按端**) → list[(ok, msg)]。

    本节只做"判据四步"的**前两步的一半**: 判据(卡带 `PROBE` 用哪一端 + 各端钉子用哪一支)与
    **候选**(枚举)。"逐个证明"要真开会话读 CPUID(活体), "恰好一支"要等证明完才谈得上 ——
    两条都不归本模块(见文件头"刻意只做离线")。所以本节答的是「**声明的探针在不在盘上**」,
    不是「连得上连不上」, 与第 6 节串口同一条分寸。
    """
    out = []
    want = getattr(P, "PROBE", None)
    if want is None:
        ends = list(_ENDS)
    elif str(want).strip().lower() in _ENDS:
        ends = [str(want).strip().lower()]
    else:
        # ⚠ 拼错一个字母就退化成"两端都找" —— 那正是 probesel 要消灭的静默猜测。此处必红。
        return [(False, "探针: 卡带 `PROBE` 写的是 %r, 不认识 —— 只能写 None(两端都找) / %s"
                        % (want, " / ".join("'%s'" % e for e in _ENDS)))]
    if "jlink" in ends and not getattr(P, "HAS_JLINK", False):
        out.append((True, "探针·J-Link: 卡带声明本机**没有**这一端(HAS_JLINK=False), 跳过"))
        ends = [e for e in ends if e != "jlink"]

    # ---- 第一遍: 各端枚举 + 按各自的钉子筛。**先算清总量再定每一行的绿红** —— "这一端没有"
    #      是不是缺口, 取决于另一端有没有: `PROBE=None` 的含义本就是"哪端有就用哪端"。
    scans = []
    for be in ends:
        pin = P.JLINK_SN if be == "jlink" else getattr(P, "DAP_UID", None)
        try:
            found, err = _enumerate(be), None
        except Exception as exc:
            found, err = [], str(exc).strip().splitlines()[0][:160]
        kept = found if pin is None else [x for x in found if str(x) == str(pin)]
        # 只在"枚举不满意"时才值得花几秒去问设备树(一次查询要数秒)—— 与原先同一条经济账。
        why = (err is None) and ((not found) or (pin is not None and not kept and found))
        scans.append({"be": be, "pin": pin, "found": found, "kept": kept, "err": err, "why": why})
    pool = sum(len(s["kept"]) for s in scans)

    # ---- 第二遍: 逐端报 ----
    for s in scans:
        lab = _end_label(P, s["be"])
        if s["err"] is not None:
            # 枚举跑不了(库没装 / 驱动坏了)是**真缺陷**, 与"没插探针"是两件事 —— 一律点名。
            out.append((False, "探针·%s: 枚举跑不了 —— %s" % (lab, s["err"])))
            continue
        out.append((True, "探针·%s: 枚举到 %d 支%s"
                    % (lab, len(s["found"]),
                       "" if s["found"] else "(不在 USB 上, 或全被别的进程占着)")))
        if s["pin"] is None:
            continue
        if s["kept"]:
            out.append((True, "探针·%s: 卡带**钉死** %s, 在" % (lab, s["pin"])))
        elif s["found"]:
            out.append((True, "探针·%s: 卡带**钉死** %s, 但当前连着的**不是它**: %s —— "
                              "已按钉子排除在外(不静默换一支)"
                        % (lab, s["pin"], " ".join(s["found"]))))
        else:
            out.append((True, "探针·%s: 卡带**钉死** %s, 而这一端一支都没有(钉子落空)"
                        % (lab, s["pin"])))

    if not pool:
        out.append((False, "探针: 在找的端合起来**一支可用候选都没有**(卡带 PROBE=%r) —— 探针插好"
                           "没有? 见下面各端那一行。" % (want,)))
        for s in scans:
            if s["why"]:
                for line in _probe_why(s["be"], _end_vid(P, s["be"])):
                    out.append((False, "探针·%s 为什么不在: %s" % (_end_label(P, s["be"]), line)))
    elif pool > 1 and all(s["pin"] is None for s in scans):
        out.append((True, "探针: 在找的端合起来 %d 支候选而卡带**没点名** —— 真连时由 probesel "
                          "逐个读 CPUID 证明, 恰好剩一支才认; 若都证明成功会**当场抛**(要么拔掉"
                          "多余的, 要么在卡带点名 PROBE / JLINK_SN / DAP_UID)" % pool))
    else:
        out.append((True, "探针: 在找的端合起来 %d 支可用候选(选哪一支由 probesel 活体证明)"
                    % pool))
    return out


def env_check():
    """装包即验 -> list[(ok, msg)]. 不抛异常; 每项独立, 记哪缺哪."""
    out = []
    # 1) 画像字段齐全
    try:
        from common import machspec
        P = machspec.current()
    except Exception as e:
        return [(False, "machine.CURRENT 无法 import: %s" % e)]
    out.append((True, "画像 import: %s (%s)" % (getattr(P, "MACHINE", "?"), getattr(P, "OS", "?"))))
    for name, ok in _require_attrs(P):
        out.append((ok, "画像字段 %-16s %s" % (name, "在" if ok else "缺失")))

    # 2) meta 验收清单在且能解析
    meta = None
    try:
        import json
        mp = _meta_path(P)
        ok = os.path.isfile(mp)
        out.append((ok, "验收清单在: %s" % mp))
        if ok:
            with open(mp, encoding="utf-8") as f:
                meta = json.load(f)
            out.append((True, "验收清单解析: name=%s" % meta["machine"]["name"]))
    except Exception as e:
        out.append((False, "验收清单解析失败: %s" % e))

    # 3) 工具在盘上
    # ⚠ 只报"在不在", 不报"能不能跑" —— 后者是第 4 节 gdb 三连的事。
    tools = [("gdb", P.GDB), ("JLink GDB Server", P.GDB_SERVER),
             ("J-Link Commander", P.JLINK), ("ARM 工具链目录", P.ARM_TOOLCHAIN)]
    for label, path in tools:
        out.append((os.path.exists(path), "%s: %s" % (label, path if os.path.exists(path) else "**不在**: " + str(path))))
    if P.ARM_TOOLCHAIN:
        _gcc = os.path.join(P.ARM_TOOLCHAIN, "arm-none-eabi-gcc.exe")
        out.append((os.path.isfile(_gcc), "交叉编译器: %s" % (("在" if os.path.isfile(_gcc) else "**不在**: " + _gcc))))

    # 4) gdb 三连验收 —— **换 gdb 后必须重跑这一节**(缺一不可, 见 CLAUDE.md「调试链」)
    #    ① --configuration 声明了全目标 + Python scripting; ② 真能跑 python; ③ 载 ARM 的 .out 后架构对。
    #    第 ① 条只是"它说它有", 第 ② 条才是"真有" —— 只查 --configuration 会放过一个假货。
    exp = (meta or {}).get("expect", {})
    if os.path.isfile(P.GDB or ""):
        try:
            import subprocess
            r = subprocess.run([P.GDB, "--configuration"], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, timeout=30)
            cfg = (r.stdout or b"").decode("utf-8", "replace")
            for s in exp.get("gdb_substr", []):
                out.append((s in cfg, "gdb --configuration 含 %s" % s))
        except Exception as e:
            out.append((False, "gdb --configuration 跑不起来: %s" % e))
        try:
            import subprocess
            r = subprocess.run([P.GDB, "--batch", "-ex", "python print('PYTHON-OK')"],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
            txt = (r.stdout or b"").decode("utf-8", "replace")
            out.append(("PYTHON-OK" in txt,
                        "gdb 能跑 Python(喂狗钩子的前提) %s"
                        % ("" if "PYTHON-OK" in txt else "**答: %s**" % txt.strip()[:80])))
        except Exception as e:
            out.append((False, "gdb python 探测跑不起来: %s" % e))
        _outs = _find_out(P)
        if not _outs:
            out.append((True, "gdb 载 .out 验架构: 跳过(没找到 .out, 不judge)"))
        else:
            try:
                import subprocess
                r = subprocess.run([P.GDB, "--batch", "-ex", 'file "%s"' % _outs[0],
                                    "-ex", "show architecture"],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
                txt = (r.stdout or b"").decode("utf-8", "replace")
                want = exp.get("arch_after_load", "")
                out.append((want in txt, "gdb 载 .out 后架构 = %s(期望 %s)  [%s]"
                            % (_arch_of(txt), want, os.path.basename(_outs[0]))))
            except Exception as e:
                out.append((False, "gdb 架构探测跑不起来: %s" % e))
    else:
        out.append((False, "gdb 不在盘上, 三连验收整节跳过"))

    # 5) Python 包 —— 清单来自**仓根 pyproject.toml**(2026-09-20 起; 原先挂在卡带的 PY_PACKAGES 上,
    #    那是把"这个软件依赖什么"按"这台机器什么条件"记了, 错轴)。
    out.extend(_deps_section(P, _ROOT))

    # 6) 串口 / 探针 —— **离线识别**(只枚举 + 筛, 不开口、不发帧、不连探针)
    # ⚠ 这一节**有意只答一半**: 它答"驱动层认不认得出候选", **不答"口后面是不是那块表"** ——
    #   后者要发帧, 是 `python -m project.env_check --online` 与本文件末尾"下一步"里那两个命令的事。
    #   把"桥插着"当成"表在", 正是本仓反复治理的那种静默错误。
    try:
        from common import portsel
        if P.COM:
            ports = [p.device for p in portsel.list_ports()]
            out.append((P.COM in ports, "串口: 卡带**钉死** %s %s(系统认得的口: %s)"
                        % (P.COM, "在册" if P.COM in ports else "**不在册**",
                           " ".join(ports) or "无")))
        else:
            hits = portsel.candidates(P.COM_MATCH or {})
            out.append((len(hits) >= 1,
                        "串口: 自动识别 [%s] → %d 个候选 %s"
                        % (portsel.describe(P.COM_MATCH or {}), len(hits),
                           " ".join(h["device"] for h in hits)
                           or "**一个都没有**(USB-485 桥插好没有? 驱动装了没有?)")))
    except Exception as e:
        out.append((False, "串口识别不可用: %s" % e))

    # 6b) 探针(**离线枚举, 按端**) —— 卡带 `PROBE` 说这一次用哪一端(None = 两端都找)。
    # ⚠ 本节**只枚举**: 不开会话、不读 CPUID、不碰目标 —— "探针后面是不是本表那颗核"要真连去
    #   证明, 那是活体的事(见文件头"刻意只做离线")。判据与逐端明细都在 `_probe_section`。
    try:
        out.extend(_probe_section(P))
    except Exception as e:
        out.append((False, "探针枚举不可用: %s" % e))

    # 7) launch.json 镜像一致 —— 把"静默的重复"变成"被核对的重复"(见 meta 的 debugger_mirror.why)
    try:
        from common import jsonc
        mir = (meta or {}).get("debugger_mirror", {})
        lp = os.path.join(_ROOT, mir.get("path", ".vscode/launch.json"))
        if not os.path.isfile(lp):
            out.append((False, "镜像文件不在: %s" % lp))
        else:
            cfgs = jsonc.load(lp).get("configurations", [])
            bad, checked = [], 0
            for c in cfgs:
                for f in mir.get("fields", []):
                    if f["key"] not in c:
                        continue
                    checked += 1
                    got = jsonc.canon(c[f["key"]])
                    want = jsonc.canon(getattr(P, f["spec"], ""))
                    if got != want:
                        bad.append("%s.%s(%s≠卡带 %s)" % (c.get("name", "?"), f["key"], c[f["key"]], getattr(P, f["spec"], None)))
            out.append((not bad, "launch.json 与卡带一致(%d 个配置, 核对 %d 处)%s"
                        % (len(cfgs), checked, "" if not bad else " **不符**: " + "; ".join(bad[:4]))))
    except Exception as e:
        out.append((False, "launch.json 核对不可用: %s" % e))

    # 8) 工作区 / 两块芯片的固件工程在盘
    # 只核"工作区在哪"这一件事(MENGXI_ROOT) + 两个子目录名 —— 具体某块表的 .out/源码根属
    # project/*.meta.json 的 firmware 块, 不在这儿重复。
    for label, name in (("管理芯", P.MANAGE_CHIP_DIR), ("计量芯", P.METER_CHIP_DIR)):
        d = os.path.join(P.MENGXI_ROOT or "", name or "")
        out.append((os.path.isdir(d), "%s工程目录: %s" % (label, d if os.path.isdir(d) else "**不在**: " + d)))

    # 9) 边界铁律: `machine/*.py` 不许 import meterlib / swdbg / discover / project
    # ⚠ 与 project/env_check.py 那条同款(AST 查真 import, 不用 grep —— 注释里到处在谈这些名字)。
    # 本包是**最底下那盘卡带**: 它一依赖引擎, "换台机器照用同一套引擎"这条立身之本就没了,
    # 而且 swdbg 反过来经 common.machspec 取本包 ⇒ 绕成圈。
    try:
        import ast
        offenders = []
        mdir = os.path.dirname(os.path.abspath(__file__))
        for _f in sorted(os.listdir(mdir)):
            if not _f.endswith(".py"):
                continue
            try:
                with open(os.path.join(mdir, _f), encoding="utf-8") as fh:
                    tree = ast.parse(fh.read(), filename=_f)
            except Exception:
                continue                       # 语法坏的文件不是本条的管辖
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    _mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    _mods = [node.module or ""]
                else:
                    continue
                if any(m.split(".")[0] in _FORBIDDEN for m in _mods):
                    offenders.append("%s:%d" % (_f, node.lineno))
        out.append((not offenders, "边界: machine/ 不依赖 %s %s"
                    % ("/".join(_FORBIDDEN), ("(越界 %s)" % " ".join(offenders)) if offenders else "(铁律成立)")))
    except Exception as e:
        out.append((False, "边界扫描不可用: %s" % e))

    return out


def _find_out(P):
    """在工作区里找管理芯的 .out(给第 4 节验架构用)。找不到就返回 [](那一节跳过, 不judge)。"""
    base = os.path.join(P.MENGXI_ROOT or "", P.MANAGE_CHIP_DIR or "")
    hits = []
    for dp, _dn, fn in os.walk(base) if os.path.isdir(base) else []:
        for f in fn:
            if f.endswith(".out"):
                hits.append(os.path.join(dp, f))
    hits.sort(key=lambda p: (("Debug" not in p), -os.path.getmtime(p) if os.path.exists(p) else 0))
    return hits[:1]


def _arch_of(text):
    """从 `show architecture` 的输出里抠出**实际**架构名, 只用于显示。

    ⚠ 措辞踩过一次(2026-09-11 首跑): 本机 gdb 17.2 输出的是
        The target architecture is set to "auto" (currently "armv6s-m").
    —— 若只按老措辞抠 `set to (\\S+)`, 会抠出 `"auto"`, 于是一块**完全正确**的配置也显示成
    『期望 armv6s-m 实得 auto』, 看着像失败而断言其实是绿的(它比的是整段输出里有没有 armv6s-m)。
    **一个骗人的显示比不显示坏**: 人学会无视它, 真不符也就一起被无视了。故先认括号里那句。
    """
    import re
    for pat in (r'currently\s+"([^"]+)"',
                r'architecture is (?:set to|assumed to be)\s+"?([A-Za-z0-9_.-]+)"?'):
        m = re.search(pat, text)
        if m:
            return m.group(1)
    return text.strip().splitlines()[-1][:60] if text.strip() else "?"


def main(argv=None):
    from common.cli import guard_argv
    from common.console import ensure_utf8_stdout
    ensure_utf8_stdout()
    # ⚠ 本模块**有意没有 `--online`**: 真连探针是 `python -m swdbg.probesel --doctor` 的事(见文件头"刻意只做离线")。
    #   所以任何参数都拦死 —— 否则有人敲 `--online` 会**静默拿到一份离线报告**还以为验过活体。
    guard_argv(argv if argv is not None else sys.argv[1:])
    rows = env_check()
    for ok, msg in rows:
        print("[%s] %s" % ("OK  " if ok else "MISS", msg))
    allok = all(o for o, _ in rows)
    print("-" * 68)
    print("MACHINE CHECK:", "READY" if allok else "INCOMPLETE")
    if allok:
        print("下一步(活体, 与本判定无关): python -m swdbg.probesel --doctor   /   python -m meterlib.cmd_bank smoke")
    return 0 if allok else 2


if __name__ == "__main__":
    raise SystemExit(main())
