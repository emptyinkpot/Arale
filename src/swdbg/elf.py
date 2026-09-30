# -*- coding: utf-8 -*-
"""
swdbg/elf.py —— 把 .out(明文 ELF)当符号表读。纯离线, 不碰串口/IAR/源码。

.out 由调用方喂, 本模块不猜路径:
    elf.configure(路径)        # 组合根调一次
    elf.ram_objects(路径)      # 或每次显式传参, 覆盖已配置值
两者都没有 → 抛错。

.map 被 IAR TSD 加密(%TSD-Header-###% 开头), 脚本在 IAR 外读不了;
.out 是明文 ELF, pyelftools 直接解析。
DWARF 非必需 —— STT_OBJECT + 落在可写节就够当读地址。

CLI:
    python -m swdbg.elf --out <路径>
    python -m swdbg.elf --out <路径> Frez
    python -m swdbg.elf --out <路径> --json 'Recd|kWh|Time'

API:
    configure(out) -> None                            # 装默认 .out 路径(组合根调一次)
    configured_out() -> str | None                     # 已配置路径(没有则 None)
    load(out=None) -> ELFFile(进程内缓存)
    ram_objects(out=None) -> {name: (addr, size, section)}   # STT_OBJECT 且落 SHF_WRITE|SHF_ALLOC 节
    addr_of(name, out=None) -> int | None              # RAM 对象符号地址(仅精确名)
    any_addr(name, out=None) -> int | None             # 任意符号地址(含 FLASH 常量表; 仅精确名)
    find(pattern, out=None) -> dict                     # 名字 re.search(pattern, IGNORECASE) 过滤
    segments(out=None) -> [(vaddr, memsz, filesz, data)]      # PT_LOAD 段(问"字节是什么")
    image(lo, hi, fill=0xFF, out=None) -> (bytes, 覆盖字节数)  # 铺 [lo,hi) 的映像; 没段盖住处填 fill
    wordsum(data) -> int                               # 逐 32 位小端字相加(截 32 位), 纯函数
"""
import os
import re
import sys


# .out 路径由调用方注入(见文件头"依赖倒置")。**不在这里 import project**。
_CONFIGURED_OUT = None
_ELF_CACHE = {}


def configure(out):
    """装默认 .out 路径。组合根(入口脚本 / resolve.configure)调一次即可。"""
    global _CONFIGURED_OUT
    _CONFIGURED_OUT = out


def configured_out():
    """当前已配置的 .out 路径; 没配过返回 None(调用方据此判"要不要先 configure")。"""
    return _CONFIGURED_OUT


def _resolve_out(out):
    path = out or _CONFIGURED_OUT
    if not path:
        raise RuntimeError(
            "elf 未配置 .out 路径。入口脚本应先 elf.configure(<画像.OUT_PATH>), "
            "或调用时显式传 out=；也可由 swdbg.resolve.configure(out_path=...) 代喂。")
    return path


def load(out=None):
    """打开(并缓存).out 的 pyelftools ELFFile. 失败给可读错误(未配置/文件缺/pyelftools 缺/非 ELF)."""
    path = _resolve_out(out)
    key = os.path.normpath(path)
    if key in _ELF_CACHE:
        return _ELF_CACHE[key][0]
    try:
        from elftools.elf.elffile import ELFFile
    except ImportError:
        raise RuntimeError("缺 pyelftools: pip install pyelftools")
    if not os.path.isfile(key):
        raise RuntimeError(".out 不存在: %s" % key)
    try:
        f = open(key, "rb")
        elf = ELFFile(f)
    except Exception as e:
        raise RuntimeError("无法解析 .out(可能被 TSD 加密或非 ELF): %s -> %s" % (key, e))
    _ELF_CACHE[key] = (elf, f)
    return elf


def ram_objects(out=None):
    """{name: (addr, size, section)} —— RAM 里所有 STT_OBJECT 全局对象符号.
    判 RAM: 节同时带 SHF_ALLOC|SHF_WRITE(.data/.bss 这类), 跳过未定义/特殊节."""
    elf = load(out)
    secs = list(elf.iter_sections())
    SHF_WRITE, SHF_ALLOC = 0x1, 0x2
    out_d = {}
    for sec in secs:
        if sec["sh_type"] != "SHT_SYMTAB":
            continue
        for sym in sec.iter_symbols():
            name = sym.name
            if not name:
                continue
            sh = sym["st_shndx"]
            if not isinstance(sh, int) or not (0 <= sh < len(secs)):
                continue
            s = secs[sh]
            if (s["sh_flags"] & (SHF_WRITE | SHF_ALLOC)) != (SHF_WRITE | SHF_ALLOC):
                continue
            if sym["st_info"]["type"] != "STT_OBJECT":
                continue
            out_d[name] = (int(sym["st_value"]), int(sym["st_size"]), s.name)
    return out_d


def addr_of(name, out=None):
    """精确名 -> RAM 地址(int) 或 None."""
    return (ram_objects(out).get(name) or (None,))[0]


def any_addr(name, out=None):
    """精确名 -> 地址(int) 或 None —— **不限 RAM**, FLASH 里的常量表也查得到。

    `addr_of` / `ram_objects` 的判据是"落在 SHF_WRITE|SHF_ALLOC 节"(那是 RAM 可写量的判据),
    冻结对象表这类 `const` 常量住在只读段 —— 拿 `addr_of` 查它**静默给 None**, 看着像"没有这个符号"。
    要读那些表(按绝对地址经探针读)就得走这一条。

    同名多份时 STT_OBJECT 优先(节符号/SECTION 这类同名物不是要的那个), 去重后取第一个。
    """
    elf = load(out)
    hit = None
    for sec in elf.iter_sections():
        if sec["sh_type"] != "SHT_SYMTAB":
            continue
        for sym in sec.iter_symbols():
            if sym.name != name:
                continue
            val = int(sym["st_value"])
            if val == 0:
                continue
            if sym["st_info"]["type"] == "STT_OBJECT":
                return val          # 对象符号优先, 命中即回
            if hit is None:
                hit = val
    return hit


def find(pattern, out=None):
    """按名字 regex(IGNORECASE)过滤 RAM 对象 -> {name: (addr, size, section)}."""
    rx = re.compile(pattern, re.IGNORECASE)
    return {n: v for n, v in ram_objects(out).items() if rx.search(n)}


# ---- .out 的**程序映像**(PT_LOAD 段)与"逐字累加"(2026-09-17 建) ----
# 动因 = 16-2『软件比对』的判据「三段校验和逐字可算」: 固件把三个 FLASH 区的累加和当"程序集成
# 标识"报出来, 要证它算得对, 就得**我们自己**按同一口径从固件映像重算一遍再比。那份映像是 .out,
# 所以这件事落在这儿(与 `ram_objects` 同一份文件、同一个缓存, 只是问的东西不同: 那边问"符号住哪",
# 这边问"那些地址上的字节是什么")。
#
# ⚠ **本段不知道固件的累加口径是什么** —— `wordsum` 只实现"给定字节、按 32 位小端字相加"这个**纯函数**;
#   "0xFF3002/03/04 用的是这个口径"是**调用方的知识**(住 meterlib/cmd_bank.py 的 16-2 段, 附离线依据)。
#   放这儿的判据同 `elfsym` 其余部分: 零协议、零画像、零具体子项。


def segments(out=None):
    """PT_LOAD 段 → [(vaddr, memsz, filesz, data)] 按 vaddr 升序。

    `data` 是**文件里**那一段(长度 = p_filesz); `memsz - filesz` 的尾巴(.bss 那类只占内存的)
    在 FLASH 映像里没有内容, 这里**不补** —— 补什么由 `image()` 的 `fill` 定, 由调用方声明。
    """
    elf = load(out)
    segs = []
    for seg in elf.iter_segments():
        if seg["p_type"] != "PT_LOAD":
            continue
        data = seg.data()
        segs.append((int(seg["p_vaddr"]), int(seg["p_memsz"]), len(data), data))
    segs.sort(key=lambda s: s[0])
    return segs


def image(lo, hi, fill=0xFF, out=None):
    """把 .out 的 PT_LOAD 段铺成 `[lo, hi)` 的连续映像 → `(bytes, 被段盖住的字节数)`。

    `fill` 是**没有任何段盖住**的那些字节。用哪一种**不是本函数的知识**: 未编程的 FLASH 通常读回
    0xFF, 但那要由调用方按它要问的问题声明, 并自己说清用的是哪一种 —— 填充猜错会**静默**改变
    累加和, 而累加和正是这里的判据本身。

    返回的覆盖计数是给调用方判断"这段到底被铺满了没有"用的: 铺满(计数 == hi-lo)时**一个填充字节
    都用不到**, 于是那处的结论与 `fill` 无关 —— 这正是 16-2 应用区那一段的情形(四段首尾相接,
    `[0x4000, 0x80000)` 100% 被盖住)。
    """
    lo, hi = int(lo), int(hi)
    if hi <= lo:
        raise ValueError("image: hi 必须大于 lo, 收到 [%#x, %#x)" % (lo, hi))
    buf = bytearray([int(fill) & 0xFF]) * (hi - lo)
    covered = 0
    for vaddr, _memsz, _filesz, data in segments(out):
        for k, byte in enumerate(data):
            a = vaddr + k
            if lo <= a < hi:
                if buf[a - lo] == (int(fill) & 0xFF):
                    covered += 1        # 只把"从填充变成真字节"的算覆盖, 重复段不重复计
                buf[a - lo] = byte
    return bytes(buf), covered


def wordsum(data):
    """逐 **32 位小端**字相加, 只留低 32 位(截尾) → int。

    长度不是 4 的整数倍时, 末尾不足一个字的部分**丢掉**(而不是补零): 补零与丢掉在"和"上等价,
    但补零会让调用方误以为那几位参与了运算 —— 这里用丢掉, 并要求调用方先保证区间按字对齐。
    """
    b = bytes(data)
    n = len(b) // 4
    if n == 0:
        return 0
    total = 0
    for i in range(n):
        total = (total + int.from_bytes(b[i * 4:i * 4 + 4], "little")) & 0xFFFFFFFF
    return total


# ---- DWARF 类型(.out 带调试信息时才有; 读不到一律退空, 不抛) ----
def _str(v):
    """pyelftools 的 DW_AT_name 有时给 bytes 有时给 str, 统一成 str。"""
    if v is None:
        return None
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return str(v)


def _type_die(die):
    """解 `die` 的 `DW_AT_type` 指向的类型 DIE。

    MUST 用 pyelftools 的 `get_DIE_from_attribute`, 不许手算偏移: IAR 的 .out 里 `DW_AT_type`
    两种 form 混用 —— 顶层变量多是 ref_addr(全局偏移), 深一跳的元素类型是 ref4(CU 相对)。
    手算两种都错, 且**不抛异常、静默给别的 DIE**(该是 INT8U 解出个枚举), 比崩掉坏得多。
    """
    if not (getattr(die, "attributes", {}) or {}).get("DW_AT_type"):
        return None
    try:
        return die.get_DIE_from_attribute("DW_AT_type")
    except Exception:
        return None


def _array_count(td):
    """数组元素个数。`DW_TAG_array_type` 自己没有 `DW_AT_byte_size`, 长度只在
    `DW_TAG_subrange_type` 子 DIE 上(count 或 upper_bound+1)。"""
    try:
        for ch in td.iter_children():
            if ch.tag != "DW_TAG_subrange_type":
                continue
            a = ch.attributes
            if "DW_AT_count" in a:
                return int(a["DW_AT_count"].value)
            if "DW_AT_upper_bound" in a:
                return int(a["DW_AT_upper_bound"].value) + 1
    except Exception:
        pass
    return None


def _walk_type(dwarf, cu, td, depth=0):
    """沿类型链走一层层收 → (类型名, 字节大小, volatile, const)。

    实测的典型链(IAR): volatile_type → array_type → typedef(INT8U) → base_type(unsigned char, 1B)
    命名取**链上第一个有名字的**(typedef 名如 INT8U 比底层 unsigned char 有信息);
    数组则组合成 `元素名[N]` 并把尺寸算成 元素×个数。
    """
    if td is None or depth > 24:                        # 防自引用结构体成环
        return None, None, None, None
    tag = getattr(td, "tag", "")
    a = getattr(td, "attributes", {}) or {}

    volatile = True if tag == "DW_TAG_volatile_type" else None
    const = True if tag == "DW_TAG_const_type" else None

    inner_name = inner_size = None
    if "DW_AT_type" in a:
        in_name, in_size, in_vol, in_const = _walk_type(dwarf, cu, _type_die(td), depth + 1)
        inner_name, inner_size = in_name, in_size
        if in_vol:
            volatile = True
        if in_const:
            const = True

    if tag == "DW_TAG_array_type":
        n = _array_count(td)
        name = "%s[%s]" % (inner_name or "?", n if n else "?")
        size = (inner_size * n) if (inner_size and n) else inner_size
    else:
        nm = _str(a["DW_AT_name"].value) if "DW_AT_name" in a else None
        name = nm or inner_name
        size = None
        if "DW_AT_byte_size" in a:
            try:
                size = int(a["DW_AT_byte_size"].value)
            except Exception:
                size = None
        if size is None:
            size = inner_size
    return name, size, volatile, const


def _resolve_type(dwarf, cu, die):
    """变量 DIE → (类型名, 字节大小, volatile, const)。没有 DW_AT_type 或解不动 ⇒ 全 None。"""
    return _walk_type(dwarf, cu, _type_die(die))


def var_types(out=None):
    """{name: 类型名} —— 从 DWARF 的 DW_TAG_variable 读。.out 没带调试信息就返回 {}。"""
    elf_ = load(out)
    try:
        dw = elf_.get_dwarf_info()
    except Exception:
        return {}
    types = {}
    try:
        for cu in dw.iter_CUs():
            for die in cu.iter_DIEs():
                if die.tag != "DW_TAG_variable":
                    continue
                if "DW_AT_name" not in die.attributes:
                    continue
                try:
                    nm = _str(die.attributes["DW_AT_name"].value)
                    tn = _resolve_type(dw, cu, die)[0]
                except Exception:
                    continue        # 单个变量解不动就跳过, 不许把整趟扫描掐断
                if nm and tn:
                    types[nm] = tn
    except Exception:
        return types
    return types


# ---- 关键"远程 Watch"候选(先列出来, 供探针/打点脚本用) ----
CORE_WATCH = ["FrezData", "FrezData2", "RecdData", "g_CurTime", "g_HisTime", "kWhData"]


def _fmt_row(name, v):
    addr, size, sec = v
    return "0x%08X  size=%-6d  %-12s  %s" % (addr, size, sec, name)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ensure_utf8_stdout()
    out = None
    if "--out" in argv:
        i = argv.index("--out")
        out = argv[i + 1]
        del argv[i:i + 2]
    as_json = "--json" in argv
    if as_json:
        argv.remove("--json")
    try:
        objs = ram_objects(out)
    except RuntimeError as e:
        print("!! %s" % e)
        return 2
    pat = argv[0] if argv else None

    if pat is not None:
        sub = find(pat, out)
        if as_json:
            import json
            print(json.dumps({n: {"addr": v[0], "size": v[1], "section": v[2]}
                              for n, v in sorted(sub.items())}, ensure_ascii=False, indent=2))
            return 0
        print("== .out RAM 对象符号 匹配 /%s/i : %d 个 ==" % (pat, len(sub)))
        for n, v in sorted(sub.items()):
            print("  " + _fmt_row(n, v))
        return 0

    print(".out: %s" % (out or _CONFIGURED_OUT))
    print("RAM 对象符号总数: %d" % len(objs))
    print("== 远程 Watch 核心候选 ==")
    for n in CORE_WATCH:
        if n in objs:
            print("  " + _fmt_row(n, objs[n]))
        else:
            print("  (未找到 RAM 对象) %s" % n)
    return 0


from common.console import ensure_utf8_stdout


if __name__ == "__main__":
    sys.exit(main())
