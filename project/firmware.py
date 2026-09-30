# -*- coding: utf-8 -*-
"""project/firmware.py —— 把本卡带的**固件声明**读成探测要的输入(纯数据, 零 discover 依赖)

它解决什么
----------
探测一块表要四样输入: `.out` / 源码根 / `.ewp` / 总纲。原先 `_probe_all.py` 只能**扫盘推**:
从 `.ewp` 的 `ExePath` 推 `.out`、从 `.ewp` 文件列表的**公共祖先**推源码根。三个真工程都推对了,
但 `scan.src_root_of` 留着一个 fallback —— 公共祖先退化到盘根时退回 `.ewp` 所在目录, **那是猜**。

卡带本来就该知道自己的固件在哪。本模块把这份声明读出来交给脚本, 于是:
**有声明用声明, 推导只当兜底**(与 `swdbg/resolve` 的「画像优先、.out 回退」同构)。

声明在哪(两处, 刻意的)
----------------------
    .out          ← 画像 `CURRENT.OUT_PATH`。它是**运行期引擎要读的那个文件**,
                     `swdbg`/`cmd_bank` 共用, 归画像(见画像里那段注释)。
    src_root/ewp  ← 本包同目录的 `<画像名>.meta.json` 的 `firmware` 块。
    out_sha256    ← 同上。它说"必须是那一份固件", 是把"探错固件"堵死在入口的凭据。
    (总纲**不声明** —— `project/knowledge/对表操作总纲.md` 是表族知识、不是这版固件的属性, 走脚本的默认位置。)

两处的缝由本模块封住: 调用方只看见 `inputs()` 一个接口, 不必知道哪个字段来自哪。

⚠ 分层铁律(改 import 前先看)
---------------------------
**本模块不许 import `discover`。** 依赖方向是 `project → common`, 而 `discover` 是**下游工具**:
卡带一旦依赖探测器, "`帧收发基础` 对任何表都能用、只有 `project/` 配合某块表"这条立身之本就没了
(用户原话)。所以这里只返回**纯字符串**; 构造 `discover.scan.Target` 是脚本层的事。
这条由 project 的边界断言守着, 不是口头约定。

用法
----
    from project import firmware
    fw = firmware.inputs()
    # {'name','out','src_root','ewp','sha256','missing','notes'}
"""
import json
import os

# ---- 仓根(向上找含 src/ 的那一级), **只用来定位文件** ----
_p = os.path.dirname(os.path.abspath(__file__))
while not os.path.isdir(os.path.join(_p, "src")) and os.path.dirname(_p) != _p:
    _p = os.path.dirname(_p)

from common import profile

__all__ = ["meta_path", "declaration", "inputs"]


def _current(P=None):
    """当前画像(调用方可显式传, 便于自检/换表)。"""
    return P if P is not None else profile.current()


def meta_path(P=None):
    """本卡带的环境清单路径 = **画像所在包目录 / 画像自己声明的 META_FILE**。

    判据与 `FRAMES_FILE` 同款(见画像里那段注释): 这两个 JSON 与画像同目录、
    随包一起搬, 所以存**文件名**、在这里拼目录 —— 不写绝对路径, 包整体可搬。
    """
    P = _current(P)
    f = getattr(P, "__file__", None)
    pdir = os.path.dirname(os.path.abspath(f)) if f else _p
    return os.path.join(pdir, getattr(P, "META_FILE", "meta.json"))


def _resolve(rel, P):
    """声明里的一条路径 → 绝对路径。

    **绝对路径原样用; 相对路径相对画像所在包目录解析**(与 `FRAMES_FILE`/`META_FILE` 同一约定)。
    写成相对是给"固件就在仓内/就在旁边"的场景留的口子; 本表固件在仓外, 所以实际是绝对路径。
    """
    if not rel:
        return None
    s = str(rel)
    if os.path.isabs(s):
        return os.path.normpath(s)
    f = getattr(P, "__file__", None)
    pdir = os.path.dirname(os.path.abspath(f)) if f else _p
    return os.path.normpath(os.path.join(pdir, s))


def declaration(P=None):
    """meta.json 的 `firmware` 块原文 + 解析后的路径。读不到就返回空壳(不抛)。"""
    P = _current(P)
    mp = meta_path(P)
    blk = {}
    try:
        with open(mp, encoding="utf-8") as f:
            blk = (json.load(f) or {}).get("firmware") or {}
    except Exception:
        blk = {}
    return {"meta_path": mp, "raw": blk,
            "name": blk.get("name") or "", "sha256": blk.get("out_sha256") or "",
            "src_root": _resolve(blk.get("src_root"), P),
            "ewp": _resolve(blk.get("ewp"), P)}


def inputs(P=None):
    """四样输入(缺的如实记在 `missing`, 不猜、不抛)。

    返回:
        name     声明的固件名(不是从 .out 推的 —— 声明优先; 两者不一致由脚本层交叉核对报出来)
        out      画像的 OUT_PATH
        src_root 声明的源码根(可能为 None)
        ewp      声明的 .ewp(可能为 None)
        sha256   声明的指纹(空串 = 未钉, 不阻断)
        missing  "out"/"ewp"/"src" 的子集 —— **与 scan.Target.missing 同一套词**,
                 好让 `check_plan`/`describe_target` 原样吃得下
        notes    给人看的 caveat
    """
    P = _current(P)
    d = declaration(P)
    out = getattr(P, "OUT_PATH", None)
    missing, notes = [], []

    if not out:
        missing.append("out")
    elif not os.path.isfile(out):
        missing.append("out")
        notes.append("画像 OUT_PATH 指的文件不在盘上: %s" % out)

    if not d["ewp"]:
        missing.append("ewp")
        notes.append("meta 的 firmware.ewp 未声明 → 编译单元数/配置那几列只能空着")
    elif not os.path.isfile(d["ewp"]):
        missing.append("ewp")
        notes.append("meta 声明的 .ewp 不在盘上: %s" % d["ewp"])

    if not d["src_root"]:
        missing.append("src")
        notes.append("meta 的 firmware.src_root 未声明 → 源码足迹那几列只能空着")
    elif not os.path.isdir(d["src_root"]):
        missing.append("src")
        notes.append("meta 声明的 src_root 不是目录: %s" % d["src_root"])

    if not d["sha256"]:
        notes.append("meta 的 firmware.out_sha256 为空 → 未钉指纹, 探测前无法核对'是不是这份固件'")

    return {"name": d["name"] or "", "out": out, "src_root": d["src_root"], "ewp": d["ewp"],
            "sha256": d["sha256"], "missing": missing, "notes": notes,
            "meta_path": d["meta_path"]}
