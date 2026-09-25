# -*- coding: utf-8 -*-
"""
common/varresolve.py —— 变量名 → (绝对地址, 长度) 的**唯一解析实现**

为什么要有它(这是本次去耦合的枢纽)
----------------------------------
原先这份解析叫 `meterlib.whitebox._pvar_addr`。串口白盒通路用它, SWD 通路
为了"不抄第二份、避免分叉", 反过来 import 它 —— 于是 SWD 包硬挂在串口协议模块上。
后果很具体: **想拿 swdbg 去指一块陌生表, 它会拽着 meterlib(乃至这块表的画像)一起进来。**

解法不是让 swdbg 自己实现一遍(那就真分叉了), 而是把解析器**提到中立层, 两条通路共用**:
现在的依赖是 meterlib 与 swdbg 各自 → common, 彼此不相识。单一事实源保住了, 反方向没了。

解析次序(与迁出前的 _pvar_addr 逐字同义)
----------------------------------------
    ① 画像 RAM_VARS(硬编码单一源, 校表稳定; 换工程=换画像)
    ② 画像未登记 → 回退 .out 符号表(仅临时/归档探针用; bench 无 .out 时这类名字读不了, 属预期)
    ③ 都没有 → None(调用方跳过并打印, 不抛)

配方(画像/路径)由调用方喂, 机制(怎么解析)在这里 —— 依赖倒置。
**本模块不 import meterlib / swdbg / project 中任何一个。**

用法
----
    from common import varresolve as VR
    VR.configure(ram_vars=P.RAM_VARS, out_path=P.OUT_PATH)   # 组合根调一次
    VR.resolve("g_CurTime")        # -> (0x200034D0, 8) 或 None
    VR.blocks("g_CurTime", "g_RelaySta")   # -> [("g_CurTime", 0x200034D0, 8), ...]
"""

__all__ = ["configure", "wire_from_profile", "resolve", "blocks", "selftest"]

_RAM_VARS = {}          # 画像: {name: (addr, size, role, note)} —— 只取前两位
_OUT_PATH = None        # .out 路径(回退用)
_WIRED_TO = None        # 当前喂进来的是哪份画像(幂等用; 见 _ensure_wired)
_EXPLICIT = object()    # 有人**自己调过** configure: 那是他明说的配方, 按需装配不许去顶掉它


def configure(ram_vars=None, out_path=None):
    """装配方。两参数都可选: 只画像(有 .out 也不用回退)、只 .out(陌生表, 无画像)、或都给。
    传 None 表示"这一项不改"。若要清空, 显式传 {} / ""。

    ⚠ 手调过这一支之后, `_ensure_wired` 就**不再**按需装画像 —— 你明说的配方优先,
      别让一条自动路径把你刚喂进去的画像顶掉。要交还给按需装配, 调 `wire_from_profile()`。"""
    global _RAM_VARS, _OUT_PATH, _WIRED_TO
    _WIRED_TO = _EXPLICIT
    if ram_vars is not None:
        _RAM_VARS = dict(ram_vars)
    if out_path is not None:
        _OUT_PATH = out_path
        from common import elfsym          # 顺手喂给 elfsym, 免得调用方记两处
        elfsym.configure(out_path)


def wire_from_profile():
    """把**当前画像**的 RAM_VARS / OUT_PATH 喂给本模块 → 是否装上。

    为什么不是一个裸的模块级 `configure(...)`: 那样**没有卡带就 import 不了本模块** ——
    连 `resolve("g_RateNo")` 这种纯查表用法都被画像绑死。所以画像不在时**先不装**,
    本模块自己就有"没配方 = 解析不到"的语义, 不会误解析。等装配好了再调一次即可。

    幂等: 画像没换就直接返回, 不必反复 configure。

    ⚠ 这里是**唯一**的自动装配点(2026-09-18 起)。原先它叫 `meterlib.ez_meter.wire_varresolve`,
      靠"ez_meter 是 L0、谁用谁就会 import 它"来保证顺序 —— 那是一条**隐形的 import 次序依赖**:
      哪天有人先用了 varresolve 再 import 协议库, 解析就静默地什么都查不到。现在装配由 `resolve()`
      自己按需触发(见 `_ensure_wired`), 与 import 顺序无关。
    """
    global _WIRED_TO
    from common import profile
    try:
        p = profile.current()
    except RuntimeError:
        return False
    if p is _WIRED_TO:
        return True
    configure(ram_vars=getattr(p, "RAM_VARS", {}), out_path=getattr(p, "OUT_PATH", ""))
    _WIRED_TO = p
    return True


def _ensure_wired():
    """解析前确保配方在。装过一次就零开销(一次 `is not None` 判断); 没画像时每次试一遍 ——
    那不是热路径(解析按名调, 一轮跑几十次), 换来的是"与 import 顺序无关"。"""
    if _WIRED_TO is None:
        wire_from_profile()


def resolve(name):
    """变量名 → (绝对addr, size) 或 None。次序见文件头。"""
    _ensure_wired()
    rec = _RAM_VARS.get(name)
    if rec:
        return (rec[0], rec[1])
    if not _OUT_PATH:
        return None
    try:
        from common import elfsym
        rec2 = elfsym.ram_objects(_OUT_PATH).get(name)
    except Exception:
        rec2 = None                    # .out 缺/pyelftools 缺/名字不在 —— 一律当"解析不到", 不抛
    if rec2:
        return (rec2[0], rec2[1])
    return None


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

    saved = (_RAM_VARS, _OUT_PATH)
    try:
        configure(ram_vars={"__t": (0x20000010, 4, "watch", "自检用")}, out_path="")
        chk("画像命中", resolve("__t") == (0x20000010, 4), str(resolve("__t")))
        chk("未登记且无 .out → None", resolve("__nope") is None)
        chk("blocks 跳过解析不到的名字", [b[0] for b in blocks("__t", "__nope")] == ["__t"])
        chk("blocks 返回 (名, addr, size) 三元组",
            all(isinstance(n, str) and isinstance(a, int) and isinstance(s, int)
                for n, a, s in blocks("__t")))
    finally:
        configure(ram_vars=saved[0], out_path=saved[1] or "")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    # ⚠ 2026-09-20 删掉了这里原先那三行路径引导: 那时 `python src/common/varresolve.py` 直接跑,
    #   sys.path[0] 是 src/common(**不是** src/), `from common import elfsym` 会 ModuleNotFoundError,
    #   人看到的是"自检崩了"而不是"解析器坏了"。现在 `common` 由仓根 pyproject.toml 声明、
    #   `pip install -e .` 装上, 直接跑那一路也进得来, 不必再自己塞 sys.path。
    import sys
    sys.exit(selftest())
