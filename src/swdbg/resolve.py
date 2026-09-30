# -*- coding: utf-8 -*-
"""
swdbg/resolve.py —— 变量名 → (绝对地址, 长度) 的唯一解析实现。

地址源头 = 当前 `.out` 的符号表(swdbg.elf)。解析不到 → None(调用方跳过并打印, 不抛)。

    from swdbg import resolve as VR
    VR.configure(out_path=P.OUT_PATH)   # 组合根调一次
    VR.resolve("g_CurTime")        # -> (0x200034D0, 8) 或 None
    VR.blocks("g_CurTime", "g_RelaySta")   # -> [("g_CurTime", 0x200034D0, 8), ...]
"""

__all__ = ["configure", "wire_from_profile", "resolve", "blocks", "selftest"]

_OUT_PATH = None        # 当前 .out 路径
_WIRED_TO = None        # 当前喂进来的是哪份画像(幂等用; 见 _ensure_wired)
_EXPLICIT = object()    # 有人**自己调过** configure: 那是他明说的配方, 按需装配不许去顶掉它


def configure(out_path=None):
    """装配当前 .out 路径。传 None 表示不改; 要清空显式传 ""。

    ⚠ 手调过这一支之后 `_ensure_wired` 就不再按需装配。要交还, 调 `wire_from_profile()`。"""
    global _OUT_PATH, _WIRED_TO
    _WIRED_TO = _EXPLICIT
    if out_path is not None:
        _OUT_PATH = out_path
        from swdbg import elf              # 顺手喂给 elf, 免得调用方记两处
        elf.configure(out_path)


def wire_from_profile():
    """把**当前画像**的 OUT_PATH 喂给本模块 → 是否装上。

    画像不在时**先不装** —— 解析要的是 .out, 没有就解析不到。
    幂等: 画像没换就直接返回。
    """
    global _WIRED_TO
    from common import profile
    try:
        p = profile.current()
    except RuntimeError:
        return False
    if p is _WIRED_TO:
        return True
    configure(out_path=getattr(p, "OUT_PATH", ""))
    _WIRED_TO = p
    return True


def _ensure_wired():
    """解析前确保配方在。装过一次就零开销(一次 `is not None` 判断); 没画像时每次试一遍 ——
    那不是热路径(解析按名调, 一轮跑几十次), 换来的是"与 import 顺序无关"。"""
    if _WIRED_TO is None:
        wire_from_profile()


def resolve(name):
    """变量名 → (绝对addr, size) 或 None。源头 = 当前 .out 的符号表。"""
    _ensure_wired()
    if not _OUT_PATH:
        return None
    try:
        from swdbg import elf
        rec = elf.ram_objects(_OUT_PATH).get(name)
    except Exception:
        rec = None                     # .out 缺/pyelftools 缺/名字不在 —— 一律当"解析不到", 不抛
    return (rec[0], rec[1]) if rec else None


def blocks(*names, clamp=None):
    """变量名 → [(name, 绝对addr, size)]。解析不到的跳过并打印(与 watch.named_blocks 同形)。
    **纯解析、不碰硬件** —— 串口通路与 SWD 通路共用这一个实现。

    clamp: 每块 size 的上限(超长变量按此截读)。**纯数据折算, 不改画像里登记的真实长度** ——
           画像该写变量的真长(如 s_stFrzStorageInfo 240B), 用不用得完是读通路的事:
           AA80 单次负载 ≤128B, 不 clamp 就会读到 (无应答) 而误判成"这变量读不了"。"""
    out = []
    for name in names:
        addr_size = resolve(name)
        if not addr_size:
            print("   %-16s @(画像无此变量, 跳过)" % name)
            continue
        size = addr_size[1]
        if clamp is not None and size > clamp:
            print("   %-16s @(长%dB 超读通路上限, 截读前%dB)" % (name, size, clamp))
            size = clamp
        out.append((name, addr_size[0], size))
    return out


def selftest():
    """离线自检(不开串口/不连表): 只验解析机制本身, 不依赖任何具体画像。"""
    checks = []

    def chk(label, cond, extra=""):
        checks.append(bool(cond))
        print("%-40s %s%s" % (label, "OK" if cond else "FAIL", ("  " + extra) if extra else ""))

    saved = _OUT_PATH
    try:
        configure(out_path="")
        chk("无 .out → None", resolve("__nope") is None)
        chk("blocks 跳过解析不到的名字", blocks("__nope") == [])
    finally:
        configure(out_path=saved or "")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
