# -*- coding: utf-8 -*-
"""
project/env_check.py —— "装包即验": 断言当前工程画像(project/CURRENT)的环境条件是否齐全完好。

环境 = 机器可用的条件(地址/帧/清单/能连上表), 不是给人读的知识。env_check 把这些条件逐条断言成
(ok, msg), 返回结构化结果; 全部 ok = "装了这包, 这块表能开始调"。不教书, 只报'在不在'。

- env_check(online=False): 离线部分 —— 画像字段齐全 + 帧注册文件在且能载 + 环境清单在且能解析
    + (可选)画像 RAM_VARS vs .out: **地址**逐项一致, 长度只拦"画像比 .out 空间大"的越界
    (画像记类型长度、.out 的 st_size 含对齐填充, 两者不等是常态, 见第 4 节注释)
    + 固件声明完整(src_root/ewp 在盘上、out_sha256 与实算相符) + 边界铁律(project/ 不依赖 discover).
    全程不开串口.
- env_check(online=True): 在上者之上, 真冒烟该表 —— 开 COM3 读表钟/结算日, 断言当前表响应.
    ⚠ 会动真表(只读 + 进厂内), 按共享手册"跑=实表"纪律, 用户没说离线就是允许.

meterlib 是共享引擎(装一次); 这里只 import 现成的引擎去"验环境", 不重复任何协议逻辑。
用法:  python -c "from project import env_check; print(env_check.env_check())"
换表 = 换 project/__init__ 的 CURRENT; 本模块不动(它永远验 CURRENT).
"""
import os
import sys

# ---- 仓根(向上找含 src/ 的那一级), **只用来定位文件** ----
_p = os.path.dirname(os.path.abspath(__file__))
while not os.path.isdir(os.path.join(_p, "src")) and os.path.dirname(_p) != _p:
    _p = os.path.dirname(_p)

_RES = (True, False)


def _P():
    from project import CURRENT
    return CURRENT


def _meta_path(P):
    """环境清单路径。**实现在 `project/firmware.py`(2026-09-10), 这里只委托。**

    同一件事(画像目录 + META_FILE)原先这里写了一份、firmware.py 又要写一份 ——
    不留第二份实现: 两处哪天分岔, "读的清单"就不是"声明的那份"了, 而这正是本模块要断言的东西。
    `firmware.meta_path` 是它的公开名, 不是私有函数, 所以跨模块引它没有越界。
    """
    from project import firmware
    return firmware.meta_path(P)


def _require_attrs(P):
    need = ["PROJECT", "CHIP_AF", "MANAGE_AF", "METER_AF", "TABLE_ADDR", "SERVER_TAIL",
            "RAM_BASE", "RAM_VARS", "WATCH_VARS", "STABLE_VARS", "CLOCK_VARS", "OUT_PATH",
            "FRAMES_FILE", "META_FILE",
            # 卡带布局声明(2026-09-14): 通用层不再硬编码这些位置, 一律经 profile.resolve 取 ——
            # 所以"声明了没有"必须在这里兜住, 否则少一项只会在下游静默降级。
            "TESTS_DIR", "KNOWLEDGE_DIR", "MASTER_DOC", "CASES_FILE", "REPORTS_DIR"]
    return [(n, hasattr(P, n)) for n in need]


def env_check(online=False):
    """装包即验 -> list[(ok, msg)]. 不抛异常; 每项独立, 记哪缺哪."""
    out = []
    # 1) 画像字段齐全
    try:
        P = _P()
    except Exception as e:
        return [(False, "project.CURRENT 无法 import: %s" % e)]
    out.append((True, "画像 import: %s" % getattr(P, "PROJECT", "?")))
    for name, ok in _require_attrs(P):
        out.append((ok, "画像字段 %-12s %s" % (name, "在" if ok else "缺失")))

    # 2) 帧注册文件(叠加层)在且能载
    try:
        from meterlib import cmd_bank
        fp = cmd_bank._OVERLAY_PATH
        exist = os.path.isfile(fp)
        out.append((exist, "帧注册文件在: %s" % fp))
        if exist:
            st = getattr(cmd_bank, "_OVERLAY_STATS", {})
            out.append((st.get("problems", 1) == 0,
                        "帧注册载入 loaded=%s problems=%s" % (st.get("loaded"), st.get("problems"))))
    except Exception as e:
        out.append((False, "加载帧目录失败: %s" % e))

    # 3) 环境清单(meta)在且能解析
    try:
        import json
        mp = _meta_path(P)
        ok = os.path.isfile(mp)
        out.append((ok, "环境清单在: %s" % mp))
        if ok:
            with open(mp, encoding="utf-8") as f:
                meta = json.load(f)
            out.append((True, "环境清单解析: firmware=%s dualchip.manage_af=%s"
                        % (meta["firmware"]["name"], meta["dualchip"]["manage_af"])))
    except Exception as e:
        out.append((False, "环境清单解析失败: %s" % e))

    # 3b) 卡带声明的**布局**在盘上(2026-09-14)
    #
    # ⚠ 这条非有不可: 通用层现在按卡带声明去取知识(`profile.resolve("CASES_FILE")` 等), 而
    #   **取不到是静默降级的** —— watch_runner 落回旧路径、discover 那节明写"未提供"、
    #   断点体检跳过。每一处单独看都"有兜底", 合起来就是"整块知识悄悄没接上, 判定还全绿"。
    #   所以在这儿硬判。
    #   判「在不在盘上」用 resolve 的结果: 声明是相对名时它拼到卡带包目录, 是绝对路径就原样。
    try:
        from common import profile
        # ⚠ `REPORTS_DIR` **不在**此列(2026-09-15): 其余四个是引擎**读**的输入, 取不到就是静默降级;
        #   而它是 `discover` **写**的产出目录 —— 没有它只说明"还没探过", 不是"知识没接上"。
        #   要一个产出目录预先存在, 等于逼仓里常驻一份过期产物来保判定绿, 与本仓"产物可重生成"相悖。
        #   (写方 `discover.dossier.write_report` 自己 `os.makedirs`, 不依赖它预先存在。)
        _want = [("TESTS_DIR", "目录"), ("KNOWLEDGE_DIR", "目录"),
                 ("MASTER_DOC", "文件"), ("CASES_FILE", "文件")]
        _miss, _ok = [], []
        for _name, _kind in _want:
            _res = profile.resolve(_name)          # ⚠ 别叫 _p: 那是模块级"本包目录", 覆盖了会让下面全错
            _exists = bool(_res) and (os.path.isdir(_res) if _kind == "目录" else os.path.isfile(_res))
            if _exists:
                _ok.append(_name)
            else:
                _miss.append("%s=%s" % (_name, _res or "(未声明)"))
        out.append((not _miss,
                    "卡带布局在盘: %d/%d%s" % (len(_ok), len(_want),
                        "" if not _miss else "  **缺** " + "; ".join(_miss))))
    except Exception as e:
        out.append((False, "卡带布局核对不可用: %s" % e))

    # 4) (离线可选) 画像 RAM_VARS vs .out —— **地址硬判, 长度只拦越界**
    #
    # ⚠ 判据改过(2026-09-10)。原先是一行"地址和长度都必须逐字相等", 于是它**必然报警**:
    #   画像 RAM_VARS 里记的 size 是**类型长度**(人按固件数组核的, 如 g_RateNo = INT8U[1+2] = 3),
    #   而 elfsym.ram_objects 给的 st_size 是 **IAR 圆整到 4 的倍数后的分配空间**(同一个 3 → 报 4)。
    #   两个量本就不同, 直接比 ⇒ 一块**完全正确**的表也被判"不符"。实测: 293 个 RAM 对象里
    #   49 个是这种"symtab 比类型大"的对齐填充, 且**没有一个是反过来的**(symtab 从不越过下一个
    #   符号, dwarf 比 symtab 大的真冲突 = 0)。所以"更小"是常态不是病。
    #   一个"装包即验"的判定报假警, 比不报还坏 —— 人学会无视它, 真问题也就一起被无视了。
    #   现在的判据与 discover.elf.Symbol.size_verdict 同构:
    #     地址不符          → 真问题(找错变量, 硬判红)
    #     画像 size > .out  → 真问题(按它读会越界, 硬判红)
    #     画像 size < .out  → **正常**(画像记类型长度, .out 含对齐填充), 只作提示列出
    try:
        from common import elfsym
        objs = elfsym.ram_objects(P.OUT_PATH)      # 显式传路径: env_check 手上有画像, 不倚赖"已被别处配置过"
        missing, bad_addr, too_long, padded = [], [], [], []
        for n, rv in P.RAM_VARS.items():
            o = objs.get(n)
            if o is None:
                missing.append(n)
                continue
            if o[0] != rv[0]:
                bad_addr.append("%s(画像 %#x / .out %#x)" % (n, rv[0], o[0]))
                continue
            if rv[1] > o[1]:
                too_long.append("%s(画像 %d > .out %d)" % (n, rv[1], o[1]))
            elif rv[1] < o[1]:
                padded.append(n)
        _hit = len(P.RAM_VARS) - len(missing) - len(bad_addr)
        out.append((not (missing or bad_addr or too_long),
                    "画像 RAM_VARS vs .out: 地址一致 %d/%d%s%s%s" % (
                        _hit, len(P.RAM_VARS),
                        "" if not missing else ", 缺 %d(%s)" % (len(missing), " ".join(missing[:3])),
                        "" if not bad_addr else ", **地址不符** %d(%s)" % (len(bad_addr), "; ".join(bad_addr[:3])),
                        "" if not too_long else ", **画像比 .out 空间大会越界** %d(%s)"
                        % (len(too_long), "; ".join(too_long[:3])))))
        if padded:
            out.append((True, "画像长度按类型记(故小于 .out 含填充的 st_size, 属正常) %d 项: %s"
                        % (len(padded), " ".join(sorted(padded)[:6]))))
    except Exception as e:
        out.append((False, "画像地址 vs .out 比对不可用: %s" % e))

    # 5) 固件声明完整 —— **装包即验**, 逮"固件被挪走 / 换了版本"
    # ⚠ 这条不是洁癖: 探测四样输入里三样(src_root/ewp/指纹)靠 meta 的 firmware 块给,
    #   声明写错或路径失效时, 后续探测会**静默**少两列(源码足迹/编译单元)或**探错固件**。
    #   所以缺路径当场红; 指纹**实算**比一遍, 不符也红(不是"提示一下")。
    #   指纹用 `common.hashfile` —— 与 discover.elf.sha256 是**同一份实现**(见 hashfile.py 那段;
    #   两份实现分岔的话, 这道判定会在报告记着另一个值时放行)。project/ 不许 import discover,
    #   所以这个函数必须住中立层, 不能内联一份 hashlib 在这里。
    try:
        from common import hashfile
        from project import firmware
        fw = firmware.inputs(P)
        bad = [m for m in fw["missing"] if m in ("out", "ewp", "src")]
        if bad:
            out.append((False, "固件声明不全(missing=%s): %s" % (bad, "; ".join(fw["notes"]) or "见 meta 的 firmware 块")))
        elif not fw["sha256"]:
            out.append((True, "固件声明齐全, 但 out_sha256 为空 → 未钉指纹, 探测前无法核对'是不是这份固件'"))
        else:
            _real = hashfile.sha256(fw["out"])
            out.append((_real == fw["sha256"],
                        "固件指纹%s: 声明 %s / 实算 %s(%s)"
                        % ("" if _real == fw["sha256"] else " **不符**",
                           fw["sha256"][:12], _real[:12], os.path.basename(fw["out"]))))
    except Exception as e:
        out.append((False, "固件声明校验不可用: %s" % e))

    # 6) launch.json 的 `executable` 与本表画像的 .out 一致 —— **表的那一根轴**
    # ⚠ 与 `machine/env_check.py` 第 7 节**分工**: 那边核 launch.json 的**机器**字段(serverpath /
    #   gdbPath / SN / device / interface / armToolchainPath), 这边核**表**的字段(executable = 哪个
    #   .out)。各核各的轴 —— 这正是"表"与"机器"拆成两盘卡带的意义, 两个包谁都不 import 对方。
    #   不核的下场很静默: launch.json 指着上一版固件的 .out, 断点行号全对不上, 而人以为固件写错了。
    #   归一规则用 `common.jsonc.canon` —— 两边必须是**同一条**规则, 否则"核过了"不可信。
    try:
        from common import jsonc
        lp = os.path.join(_p, ".vscode", "launch.json")
        if not os.path.isfile(lp):
            out.append((False, "launch.json 不在: %s" % lp))
        else:
            cfgs = jsonc.load(lp).get("configurations", [])
            bad = ["%s(%s)" % (c.get("name", "?"), c.get("executable"))
                   for c in cfgs
                   if jsonc.canon(c.get("executable", "")) != jsonc.canon(P.OUT_PATH)]
            out.append((not bad, "launch.json executable == 画像 .out(%d 个配置: %s)%s"
                        % (len(cfgs), os.path.basename(P.OUT_PATH),
                           "" if not bad else " **不符**: " + "; ".join(bad[:3]))))
    except Exception as e:
        out.append((False, "launch.json executable 核对不可用: %s" % e))

    # 7) 边界铁律: `project/*.py` **不许 import discover**
    # ⚠ 方向是"每个包守自己不许向上依赖"(与 discover/selftest 的 _boundary 同款), 不是"一个包替全仓守"。
    #   为什么不放进 discover/selftest: 那样等于让**探测器**的自检要求 `project/` 必须存在 ——
    #   而 discover/ 的立身之本正是"能独立拿去探任何表"。它一依赖卡带, 这条就没了(用户原话)。
    #   卡带一依赖探测器, 同样没了: `帧收发基础` 不再是对任何表都能用的工具, 只配这一块表。
    #   用 AST 查而不是 grep: grep "discover" 会命中本文件这些注释和字符串, 满屏假阳;
    #   AST 只看**真的 import 语句**, 一条不多一条不少。
    try:
        import ast
        _pdir = os.path.dirname(os.path.abspath(__file__))
        offenders = []
        for _f in sorted(os.listdir(_pdir)):
            if not _f.endswith(".py"):
                continue
            try:
                with open(os.path.join(_pdir, _f), encoding="utf-8") as fh:
                    tree = ast.parse(fh.read(), filename=_f)
            except Exception:
                continue                       # 语法坏的文件不是本条的管辖(别把它的错记成越界)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    _mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    _mods = [node.module or ""]
                else:
                    continue
                if any(m.split(".")[0] == "discover" for m in _mods):
                    offenders.append("%s:%d" % (_f, node.lineno))
        out.append((not offenders,
                    "边界: project/ 不依赖 discover %s"
                    % ("(越界 %s)" % " ".join(offenders) if offenders else "(铁律成立)")))
    except Exception as e:
        out.append((False, "边界扫描不可用: %s" % e))

    # 8) (在线) 真冒烟该表
    if online:
        try:
            from meterlib import cmd_bank
            from common.portsel import open_com
            ser = open_com()
            try:
                cmd_bank.enter_factory(ser)
                ct = cmd_bank.read_clock(ser)
                out.append((ct is not None, "在线冒烟: 表钟=%s" % (ct if ct else "(无应答)")))
            finally:
                ser.close()
        except Exception as e:
            out.append((False, "在线冒烟异常: %s" % e))
    return out


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    online = "--online" in (argv or sys.argv[1:])
    rows = env_check(online=online)
    for ok, msg in rows:
        print("[%s] %s" % ("OK  " if ok else "MISS", msg))
    allok = all(o for o, _ in rows)
    print("ENV CHECK:", "READY" if allok else "INCOMPLETE")
    return 0 if allok else 2


if __name__ == "__main__":
    raise SystemExit(main())
