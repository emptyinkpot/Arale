# -*- coding: utf-8 -*-
"""
discover/elf.py —— `.out` 里能拿到的一切: 符号(名字/地址/大小/段) **加上 DWARF 那层**
(类型 / 是否 volatile / 声明位置)。

为什么不是重写一份 ELF 解析
---------------------------
符号那一半 `common/elfsym.py` 早就做对了(pyelftools 读 .symtab, STT_OBJECT 落可写节 ⇒ RAM 对象),
本模块**直接复用, 不抄第二份**。本层只加 `.symtab` 拿不到的东西: **类型与 volatile**。
这个分工是刻意的 —— `.symtab` 只告诉你"有这么个地址、这么长"; 要判它是不是 `volatile`、
底层是什么类型, 只有 DWARF 有。

⚠ 疣(记着, 这轮不动): **ELF 解析其实不属协议层**, `elfsym` 长期该住在 `common/`(已迁)而非 meterlib。
   它现在的位置是对的; 这条留作"这轮没做的事"的记录, 不是待办。

降级(别指望 .out 一定有调试信息)
--------------------------------
`.out` 是**发布构建**的话很可能没有 `.debug_info` —— 那 DWARF 那半边就全空,
**报告里对应的列必须写"无调试信息", 不许猜**。所以本模块的 DWARF 一律**软失败**:
抓不到就返回空 dict, 绝不抛, 绝不编一个类型出来。

地址的两种来源(报告 §6 要核这个)
--------------------------------
    .symtab  → 符号地址(权威, 链接器给的)
    DWARF    → DW_AT_location 里的 DW_OP_addr
两者**必须一致**; 不一致 = 这份 .out 与源码不同步, 是重大发现, 要进报告 §7。
`cross_check()` 就是干这个的。

本模块不 import project / meterlib / swdbg —— 它只吃一个 .out 路径(见包 docstring)。
"""
from __future__ import print_function

import sys


from common import elfsym                              # 符号那一半: 复用, 不重写

__all__ = ["Symbol", "symbols", "dwarf", "describe", "cross_check", "sections", "sha256"]


class Symbol(object):
    """一个 RAM 全局符号。DWARF 拿不到时 type_name/volatile/decl_* 为 None —— **空就是空, 不许猜**。"""

    __slots__ = ("name", "addr", "size", "section", "type_name", "volatile",
                 "dwarf_size", "dwarf_addr", "decl_file", "decl_line")

    def __init__(self, name, addr, size, section):
        self.name = name
        self.addr = addr
        self.size = size
        self.section = section
        self.type_name = None       # DWARF: 类型名(可能带 const/volatile 前缀)
        self.volatile = None        # DWARF: True/False; None = 没有调试信息, 未知
        self.dwarf_size = None      # DWARF: DW_AT_byte_size 推出来的字节数
        self.dwarf_addr = None      # DWARF: DW_OP_addr
        self.decl_file = None
        self.decl_line = None

    # ---- 给报告用的三个派生判据 ----
    @property
    def size_verdict(self):
        """DWARF 的**类型大小** vs `.symtab` 声明的大小。**两者不等是常态, 但只有一半是问题。**

            None     无从比较(没有调试信息)
            "一致"
            "dwarf小"  → **正常**: .symtab 的 st_size 含对齐填充。例: g_CurTime 类型 INT8U[6],
                        `.symtab` 给 8 —— 6 字节的 char 数组按 4 对齐占了 8, 不是冲突。
            "dwarf大"  → **真冲突**: .symtab 声明的空间比类型还小, 读的时候会越界。要查, 进 §7。

        2026-09-10 实测: 本工程 .out 上 248 个可核符号里 "dwarf小" 是多数(49), "dwarf大" 才是要盯的。
        """
        if self.dwarf_size is None:
            return None
        if self.dwarf_size == self.size:
            return "一致"
        return "dwarf小" if self.dwarf_size < self.size else "dwarf大"

    @property
    def size_agrees(self):
        """DWARF 与 .symtab 的大小是否"不矛盾"(一致 或 dwarf小/对齐填充)。None = 无从比较。
        真冲突看 size_verdict == "dwarf大"。"""
        v = self.size_verdict
        return None if v is None else (v != "dwarf大")

    @property
    def addr_agrees(self):
        """`.symtab` 地址 vs DWARF 地址。None = 无从比较。"""
        if self.dwarf_addr is None:
            return None
        return self.dwarf_addr == self.addr

    def as_dict(self):
        """给报告用的一份扁平字典。**派生结论也要带上** —— `size_verdict` / `addr_agrees`
        是 property, 不在 `__slots__` 里, 只导 slots 的话报告会静默拿到 None
        (踩过: dossier §3 的"含对齐/⚠类型更大"整列不显示)。"""
        d = {k: getattr(self, k) for k in self.__slots__}
        d["size_verdict"] = self.size_verdict
        d["addr_agrees"] = self.addr_agrees
        return d

    def __repr__(self):
        return "<Symbol %s @0x%08X size=%d %s>" % (self.name, self.addr, self.size, self.section)


# 控制台 UTF-8 单点: 实现收在 common/console.py(2026-09-10 迁自 meterlib)。
# 本包只依赖 common(不是 meterlib), 故可直接引 —— 原先各内联一份的理由(它住在协议层)已消失。
# 别名保原调用点 `_ensure_utf8_stdout()` 一字不改。
from common.console import ensure_utf8_stdout


def sha256(path):
    """报告 §0 可复现性要的指纹。任何一条都没法复现的探测都不可信。

    **实现已提到 `common/hashfile.py`(2026-09-10), 这里只委托。** 不是搬家洁癖:
    `project/env_check.py` 也要核指纹(它比对 meta 声明的 `out_sha256` 与盘上实算),
    而 `project/` 不许 import `discover`。若两处各留一份实现, 哪天在"二进制读/分块/编码"
    上分了歧, 那道判定就会在**报告 §0 记着另一个值**的时候放行 —— 而它存在的全部意义
    正是证明"探的确实是声明的那份固件"。委托一行, 调用点 `D_elf.sha256(out)` 与
    `discover.__init__` 的再导出一字不改。
    """
    from common import hashfile
    return hashfile.sha256(path)


def sections(out=None):
    """.out 的节概览 → [{name, type, addr, size, flags}]。报告 §2 内存地图用。"""
    elf = elfsym.load(out)
    out_l = []
    for s in elf.iter_sections():
        try:
            out_l.append({"name": s.name, "type": s["sh_type"], "addr": int(s["sh_addr"]),
                          "size": int(s["sh_size"]), "flags": int(s["sh_flags"])})
        except Exception:
            continue
    return out_l


def has_debug(out=None):
    """有没有 DWARF。没有就一个字都别编。"""
    elf = elfsym.load(out)
    return any(s.name.startswith(".debug_") and s["sh_size"] > 0 for s in elf.iter_sections())


# ============================ DWARF 层 ============================
def _str(v):
    """pyelftools 的 DW_AT_name 有时给 bytes 有时给 str, 统一成 str。"""
    if v is None:
        return None
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return str(v)


def _op_addr(die, elf):
    """从 DW_AT_location 里挖 DW_OP_addr 的地址 → int | None。

    ⚠ 实测(pyelftools 2.x, 本工程的 .out): `DW_AT_location` 是 `DW_FORM_block`,
    `.value` 给的是**裸字节 list**, 不是解析好的 op 对象 —— 例如 g_CtrlStat 拿到
    `[3, 52, 53, 0, 32]`, 即 `0x03`(DW_OP_addr) + 小端 4 字节 `0x20003534`, 与其画像地址吻合。
    所以这里**自己拆字节**; 同时留着"已经是 op 对象"的分支(换版本/换工具链时可能变)。
    ARMv6-M 是 4 字节地址, 但宽度按 ELF class 取(不写死 4)。"""
    loc = die.attributes.get("DW_AT_location")
    if loc is None:
        return None
    v = loc.value
    n = (getattr(elf, "elfclass", 32) or 32) // 8

    if isinstance(v, (list, tuple)) and v and all(isinstance(x, int) for x in v):
        ops = list(v)
        if ops and ops[0] == 0x03 and len(ops) >= 1 + n:      # DW_OP_addr
            return int.from_bytes(bytes(ops[1:1 + n]), "little")
        return None

    ops = v if isinstance(v, (list, tuple)) else [v]
    for i, op in enumerate(ops):
        if "addr" in type(op).__name__.lower() and i + 1 < len(ops):
            try:
                return int(ops[i + 1])
            except Exception:
                return None
    return None


def _type_die(die):
    """解 `die` 的 `DW_AT_type` 指向的类型 DIE。

    ⚠ **必须用 pyelftools 自己的 `die.get_DIE_from_attribute`, 不要手算偏移。**
    实测(IAR 的 .out): `DW_AT_type` 两种 form 混用 —— 顶层变量多是 `DW_FORM_ref_addr`(全局偏移),
    而数组的元素类型那种深一跳是 `DW_FORM_ref4`(CU 相对)。手算两种都错, 且**错得很危险**:

        dwarf.get_DIE_from_refaddr(v)                  → 静默给别的 DIE(实测: 该是 INT8U, 解出个枚举)
        dwarf.get_DIE_from_refaddr(cu.cu_offset + v)   → 报 "not in DIE range of CU"

    前者不抛异常, 会一路把**错的类型名和大小**写进报告 —— 比崩掉坏得多。别手算。
    """
    if not (getattr(die, "attributes", {}) or {}).get("DW_AT_type"):
        return None
    try:
        return die.get_DIE_from_attribute("DW_AT_type")
    except Exception:
        return None


def _array_count(td):
    """数组元素个数。**`DW_TAG_array_type` 自己没有 `DW_AT_byte_size`**(实测), 长度只在
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

    实测的典型链(IAR):
        volatile_type → array_type → typedef(INT8U) → base_type(unsigned char, byte_size=1)
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
        in_name, in_size, in_vol, in_const = _walk_type(
            dwarf, cu, _type_die(td), depth + 1)
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


def _decl(dwarf, cu, die):
    """DW_AT_decl_file/decl_line → (文件名, 行号)。文件名要过 CU 的 line program 文件表。"""
    a = getattr(die, "attributes", {}) or {}
    line = a.get("DW_AT_decl_line")
    lineno = None
    if line is not None:
        try:
            lineno = int(line.value)
        except Exception:
            lineno = None
    f = a.get("DW_AT_decl_file")
    fname = None
    if f is not None:
        try:
            idx = int(f.value)
            lp = dwarf.line_program_for_CU(cu)
            # 文件表在 lp.header 上(不是 lp 本身); 索引 1-based。实测 IAR 可用。
            hdr = getattr(lp, "header", None) or (lp or {}).get("header")
            entries = hdr["file_entry"] if hdr else None
            if entries and 1 <= idx <= len(entries):
                fname = _str(entries[idx - 1].name)
        except Exception:
            fname = None
    return fname, lineno


def dwarf(out=None):
    """DWARF 全局变量层 → {name: {addr, byte_size, type_name, volatile, decl_file, decl_line}}。

    **软失败**: 没有 .debug_info / pyelftools 抽不动 ⇒ 返回 {} 并在 stderr 之外没有任何副作用。
    调用方据此在报告里写"无调试信息", 而不是编一个类型。"""
    try:
        elf = elfsym.load(out)
        if not has_debug(out):
            return {}
        d = elf.get_dwarf_info()
    except Exception:
        return {}

    res = {}
    for cu in d.iter_CUs():
        try:
            top = cu.get_top_DIE()
        except Exception:
            continue
        # IAR 按"每个函数一个 CU"发(本工程 2161 个 CU), 全局量就在 CU 顶层子 DIE 上。
        # 限深是刻意的: 无限下潜会走进结构体成员/函数内局部量, 那些不是我们要的全局量
        # (而且局部量的 location 是寄存器/栈偏移, 根本不是绝对地址)。
        stack = [(top, 0)]
        while stack:
            die, depth = stack.pop()
            if depth < 3:
                try:
                    for ch in die.iter_children():
                        stack.append((ch, depth + 1))
                except Exception:
                    pass
            if getattr(die, "tag", "") != "DW_TAG_variable":
                continue
            a = getattr(die, "attributes", {}) or {}
            name = _str(a["DW_AT_name"].value) if "DW_AT_name" in a else None
            if not name:
                continue
            addr = _op_addr(die, elf)
            if addr is None:
                continue                    # 只收有**绝对地址**的 = 全局/静态量; 局部量跳过
            tname, tsize, vol, _const = _resolve_type(d, cu, die)
            fname, lineno = _decl(d, cu, die)
            res[name] = {"addr": addr, "byte_size": tsize, "type_name": tname,
                         "volatile": vol, "decl_file": fname, "decl_line": lineno}
    return res


# ============================ 合起来 ============================
def symbols(out=None, pattern=None, with_dwarf=True):
    """RAM 全局符号 → [Symbol]。地址/大小来自 `.symtab`(权威), 类型那半来自 DWARF(没有就是 None)。

    pattern: 只留名字 re.search(pattern, IGNORECASE) 命中的(与 elfsym.find 同语义)。"""
    objs = elfsym.ram_objects(out)
    if pattern:
        import re
        rx = re.compile(pattern, re.IGNORECASE)
        objs = {n: v for n, v in objs.items() if rx.search(n)}
    dw = dwarf(out) if with_dwarf else {}

    out_l = []
    for name, (addr, size, sec) in sorted(objs.items(), key=lambda kv: kv[1][0]):
        s = Symbol(name, addr, size, sec)
        rec = dw.get(name)
        if rec:
            s.type_name = rec["type_name"]
            s.volatile = rec["volatile"]
            s.dwarf_size = rec["byte_size"]
            s.dwarf_addr = rec["addr"]
            s.decl_file = rec["decl_file"]
            s.decl_line = rec["decl_line"]
        out_l.append(s)
    return out_l


def cross_check(out=None, pattern=None):
    """`.symtab` vs DWARF 的一致性核对 → 报告 §6 的原料。

    **地址对不上** = 这份 .out 与源码不同步(重大发现)。
    **大小**: 只有 `dwarf大` 是冲突; `dwarf小` 是对齐填充, 单列出来只为透明, 不是告警。
    """
    syms = symbols(out, pattern=pattern)
    addr_bad, size_big, size_small = [], [], []
    n_dw_var = n_dw_addr = n_dw_size = 0
    for s in syms:
        if s.dwarf_size is not None:
            n_dw_size += 1
        if s.dwarf_addr is not None:
            n_dw_addr += 1
        if s.type_name or s.volatile is not None or s.decl_line is not None:
            n_dw_var += 1
        if s.addr_agrees is False:
            addr_bad.append(s)
        v = s.size_verdict
        if v == "dwarf大":
            size_big.append(s)
        elif v == "dwarf小":
            size_small.append(s)
    return {
        "stats": {"ram_symbols": len(syms), "dwarf_matched": n_dw_var,
                  "dwarf_addr": n_dw_addr, "dwarf_size": n_dw_size,
                  "addr_conflict": len(addr_bad),
                  "size_type_bigger": len(size_big),      # ← 真冲突
                  "size_padding": len(size_small)},       # ← 对齐填充, 正常
        "addr_conflict": addr_bad,
        "size_conflict": size_big,
        "size_padding": size_small,
    }


def describe(out=None):
    """给报告 §1/§2 的概览: 器件规模 / 段布局 / 符号量 / DWARF 有无。"""
    objs = elfsym.ram_objects(out)
    secs = sections(out)
    prev, bss, noinit, other = 0, 0, 0, 0
    for s in secs:
        if not (s["flags"] & 0x2):                      # 非 ALLOC 不占地址空间
            continue
        if s["name"].startswith(".bss"):
            bss += s["size"]
        elif "no_init" in s["name"].lower() or "noinit" in s["name"].lower():
            noinit += s["size"]
        else:
            other += s["size"]
    for s in secs:
        if s["flags"] & 0x2 and s["addr"]:
            prev += s["size"]
    return {"ram_objects": len(objs), "sections": len(secs),
            "has_debug": has_debug(out), "bss": bss, "noinit": noinit,
            "alloc_bytes": prev, "other": other}


def main(argv=None):
    """python -m discover.elf --out <路径.out> [--json] [<name正则>]"""
    ensure_utf8_stdout()
    import argparse
    ap = argparse.ArgumentParser(description=".out 符号 + DWARF 类型层(只读, 纯离线)")
    ap.add_argument("--out", required=True)
    ap.add_argument("pattern", nargs="?", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        d = describe(args.out)
        syms = symbols(args.out, pattern=args.pattern)
        ck = cross_check(args.out, pattern=args.pattern)
    except Exception as e:
        print("!! %s" % e)
        return 2

    if args.json:
        import json
        print(json.dumps({"describe": d, "cross_check": ck["stats"],
                          "symbols": [s.as_dict() for s in syms]},
                         ensure_ascii=False, indent=2, default=str))
        return 0

    print("== .out 探测: %s ==" % args.out)
    print("   sha256 %s" % sha256(args.out))
    print("   RAM 对象 %d 个; DWARF 调试信息: %s" % (d["ram_objects"], "有" if d["has_debug"] else "**没有**"))
    print("   .bss %d B / .no_init %d B / 其它可分配 %d B" % (d["bss"], d["noinit"], d["other"]))
    st = ck["stats"]
    print("   DWARF 匹配 %d / 地址可核 %d / 大小可核 %d" % (st["dwarf_matched"], st["dwarf_addr"], st["dwarf_size"]))
    print("   地址冲突 %d; 大小真冲突(类型比 .symtab 大) %d; 对齐填充 %d(正常)"
          % (st["addr_conflict"], st["size_type_bigger"], st["size_padding"]))
    if st["addr_conflict"] or st["size_type_bigger"]:
        print("   ⚠ 有真冲突, 进报告 §7")
    print("   -- 符号(最多 30 条) --")
    for s in syms[:30]:
        print("   %-30s @0x%08X [%3dB] %-10s %-18s %s"
              % (s.name, s.addr, s.size, s.section, s.type_name or "-",
                 "volatile" if s.volatile else ("const" if s.volatile is False else "?")))
    if len(syms) > 30:
        print("   … 另有 %d 条" % (len(syms) - 30))
    return 0


if __name__ == "__main__":
    sys.exit(main())
