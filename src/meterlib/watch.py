# -*- coding: utf-8 -*-
"""meterlib/watch.py —— 串口白盒观察簇: AA80 直读管理芯 RAM

**一条通道一个文件**: 本文件只放"把 IAR 手工人机交互里『可自动观察』那半搬上 485"的东西。
CPU 全程不停(AA80 是固件自己留的读后门, 走 645 的 `C=0x11`), 与 SWD 直读、gdb 断点是**并列的
另外两条观察通路** —— 三条互相独立、可互替。

与 IAR 的心智映射
----------------
    IAR 设断点(断点一个全局符号)   -> WatchPoint(名字, symbol=全局变量名, length=...)
    IAR Go(跑一个动作)           -> 发触发帧(前置/动作帧由调用方走 cmd_bank 的语义动词)
    IAR 停在断点看 Watch         -> WatchBank.snapshot(); WatchBank.diff(pre, post)
    等真实时钟/冻结自然到点      -> wait_change() 轮询到某观察区发生变化

AA80 语义铁律
------------
负载地址 = **区内偏移** 非绝对 —— RAM 区读 0x2000xxxx 变量须传 `(0x2000xxxx - RAM_BASE)`;
传绝对地址越界读进 APB/未映射 → 总线挂 → **整帧静默**(不是 D4 错误码)。

只读得到**管理芯本区**, 读不到计量芯寄存器(双芯边界, 见 审计纪要 §6)。

冻结/结算记录写库**不进** `g_Frez*` RAM 台账(经验总结 §15) ⇒ 本簇的 RAM 快照只作**佐证**不作检查;
落库检查走记录读回(GetRequestRecord)+ 时钟变量对齐。

判读在上层(某变量前后是否变 / 值与记录是否对齐), 本簇只取变量值、做 diff, **不判过**。

依赖方向
--------
本文件只 import `common.*` 与 `meterlib.p645` —— **不 import `meterlib.cmd_bank`**(那是它的使用者,
不是它的下层)。反向 import 会成环: cmd_bank 要用本簇, 本簇又要用 cmd_bank。
"""
import time

from common import profile
from swdbg import elf as elfsym
from swdbg import resolve as varresolve
from common.loglabel import opout
from common.portsel import send_frame, tx_recv
from common.snapdiff import aa80_snap_diff
from meterlib.p645 import read_aa80_645, decode_645_reply, abs_to_aa80, AA80_MAX_LEN

P = profile.Proxy()

RAM_BASE = P.RAM_BASE    # 芯片 SRAM 基址;AA80 负载=区内偏移(绝对 - RAM_BASE),传绝对越界→整帧静默(经验总结 §14)


def read_mem_aa80(ser, region, addr, length, wait=2.0):
    """发送 → 管理芯: 645 AA80 直读本地内存(CMD 0x11 LEN 0x09 DI=[区号,AA,80,04], 区号 0..6)
    → (verdict, 数据hex|原因). 区域: 0内部FLASH 1RAM 2APB 3外部EEPROM 4INFO 5外部FLASH 6ESAM.
    ⚠ addr=【区内偏移】非绝对(RAM 绝对需 -0x20000000, 见 经验总结 §14; 传绝对越界→整帧静默).
    只读得到管理芯本地区(AA80 形), 读不到计量芯寄存器(双芯边界, 见 审计纪要 §6). 本函数自打印."""
    head = "直读 区%d 偏移0x%X 长%d" % (region, addr, length)
    frame = read_aa80_645(region, addr, length)
    # 协议那一格是 AA80 而不是 645, 但**这里一个字都不用写** —— `read_aa80_645` 组帧时已经钉上
    # (`frame_645(..., proto=PROTO_AA80)`), 见 loglabel 模块头。
    rx = send_frame(ser, frame, wait=wait, tag="aa80_r%d" % region, peer="管理芯", what=head)
    cmd, seg, _ = decode_645_reply(rx)
    if cmd == 0x91:
        body = seg[4:] if len(seg) >= 4 else seg          # 应答=4B地址回显+数据
        opout(head, frame, "数据 %s" % body.hex(" ").upper())
        return "PASS", body.hex(" ").upper()
    if cmd == 0xD4:
        opout(head, frame, "AA80 被拒 D4 (可能越界/未开编程)", ok=False)
        return "FAIL", "AA80 被拒 D4 (可能越界/未开编程)"
    opout(head, frame, "AA80 无 0x91 应答", ok=False)
    return "FAIL", "AA80 无 0x91 应答"


def aa80_ram_snapshots(ser, blocks, tag="snap", wait=2.0):
    """读一组 RAM 绝对地址块(block=(name, 绝对0x2000xxxx, 长)) → {name: bytes|None}; 打印每块读回摘要.
    AA80 负载偏移在库内折算(RAM_BASE). 冻结写库台账(g_Frez*/s_stFrzStorageInfo)结算路径实测不动(经验总结 §15),
    故此类快照只作佐证不作检查. 判读是上层的事(diff / 字节人工核)."""
    print("[AA80 快照 %s]" % tag)
    out = {}
    for name, absaddr, ln in blocks:
        off = absaddr - RAM_BASE
        # `what` 里带 `tag`(调用方给的这一段快照的名字, 如 "基线"/"预置后") —— 一块 RAM 读一行,
        #   没有它就分不出这一行属于哪一次快照; 而 `[AA80 快照 …]` 那个块头**不带时间戳以外的位置**
        #   信息, 只说明"从这儿开始是一组".
        #   帧长那一格由 `loglabel` 按**实际发出去多少字节**填, 所以 `what` 里**不再写 ln** ——
        #   写了会看着像"8B … 19B"两个长度打架(ln 是要读多少, 19B 是整帧含帧头帧尾多少)。
        rx = send_frame(ser, read_aa80_645(1, off, ln), wait=wait,
                        tag="aa80_%s_%s" % (tag, name), peer="管理芯",
                        what="直读 RAM %s(=0x%08X-基址)  [%s]" % (name, absaddr, tag))
        cmd, seg, _ = decode_645_reply(rx)
        body = seg[4:] if (cmd == 0x91 and len(seg) >= 4) else None
        out[name] = body
        print("   %-22s %s" % (name, body.hex(" ").upper() if body else "(无应答)"))
    return out


def _pvar_addr(name):
    """变量名 → (绝对addr, size) 或 None。源头 = 当前 `.out` 的符号表(swdbg.elf)。"""
    _wire()          # 换过画像就重装一次(没换则零开销, 见 varresolve._ensure_wired)
    return varresolve.resolve(name)


def _wire():
    """把当前画像的 .out 路径喂给解析器; 换过画像就重装一次, 没换则零开销。"""
    return varresolve.wire_from_profile()


def watch_vars(ser, names, tag="vars", wait=2.0):
    """读一组【命名】管理芯 RAM 变量(Watch 等价, 免 IAR 断点): 地址+长度按名解析(见 _pvar_addr),
    AA80 区1(RAM)逐变量直读 → {name: bytes|None}; 打印  name @绝对地址 [长B] = hex.
    ⚠ 只读管理芯本区变量(AA80 读不到计量芯); 画像/符号都无 → 跳过并打印原因.
    白盒判读在上层(看某变量前后是否变/值与记录对齐), 本函数只取变量值."""
    print("[AA80 Watch %s]" % tag)
    out = {}
    for name in names:
        addr_size = _pvar_addr(name)
        if not addr_size:                                 # 画像未登记 且 非 .out RAM 对象
            print("   %-16s @(画像无此变量, 跳过)" % name)
            out[name] = None
            continue
        absaddr, size = addr_size
        body = aa80_ram_snapshots(ser, [(name, absaddr, size)], tag="watch_%s" % name, wait=wait)[name]
        out[name] = body
    return out


def named_blocks(*names, clamp=None):
    """变量名 → [(name, 绝对addr, size)] 供 aa80_ram_snapshots/aa80_snap_diff.
    地址/长度按名解析(见 _pvar_addr)。
    脚本只给变量名、不摸地址; 结算等"绝不能碰的稳定态"变量在画像里登记 role=stable,
    保证 bench 无 .out 也能定址校表。解析缺失 → 跳过并打印。纯解析, 不碰串口.

    clamp: 每块 size 上限(AA80 单次负载 ≤128B; 超长变量按此截读, 不改画像里的真实长度)。

    处理逻辑与 SWD 通路共用同一份 swdbg/resolve.blocks, 本函数是薄壳。"""
    return varresolve.blocks(*names, clamp=clamp)


# ---- 观察点 / 观察组(替代 IAR Watch 窗口的一组变量) ----
class WatchPoint:
    """一个观察点 = 一个"源码全局"在 AA80 里的(区, 偏移, 长)。
    定址方式三选一:
      symbol=名           从 .out(elfsym)取绝对地址 -> RAM 区1 自动折偏移;
      addr=绝对地址        按区间自动归区折偏移;
      region=..,offset=..  显式给(EEPROM/外部FLASH/ESAM 等 raw 区/已知偏移)。
    定址失败不抛, 记到 .err; snapshot 里该点返回 None —— 读的人必须看得见这个 None, 不许当 0 用。"""

    def __init__(self, name, symbol=None, addr=None, region=None, offset=None, length=1):
        self.name = name
        self.length = length
        self.err = ""
        if not (1 <= length <= AA80_MAX_LEN):
            raise ValueError("%s: AA80 单条读长需 1..%d, 实为 %d(超长请分段登记)"
                             % (name, AA80_MAX_LEN, length))
        if symbol is not None:
            a = elfsym.addr_of(symbol)
            if a is None:
                self.err = "符号 %r 不在 .out RAM 对象表" % symbol
                return
            self.region, self.offset = abs_to_aa80(a)
        elif addr is not None:
            self.region, self.offset = abs_to_aa80(int(addr))
        elif region is not None and offset is not None:
            self.region, self.offset = int(region), int(offset)
        else:
            self.err = "缺定址: 需 symbol / addr / (region+offset) 之一"
            return
        if not (0 <= self.region <= 6):
            self.err = "区号 %d 超 0..6" % self.region
            return
        if not (0 <= self.offset < 0x10000000):
            self.err = "偏移 0x%X 疑似越界(非绝对?" % self.offset

    def describe(self):
        ok = "OK " if not self.err else "ERR"
        return "%s %-8s 区%d 偏移0x%06X 长%d %s" % (ok, self.name, self.region, self.offset,
                                                   self.length, self.err)


class WatchBank:
    """一组观察点 + 快照/前后对比(替代 IAR Watch 窗口的一组变量)。"""

    def __init__(self, name="watch"):
        self.name = name
        self.points = []          # [WatchPoint]

    # ---- 登记 ----
    def add(self, *args, **kwargs):
        wp = WatchPoint(*args, **kwargs)
        self.points.append(wp)
        return wp

    def add_specs(self, specs):
        """specs = [{"sym":名|"addr":绝对|("region"+offset), "len":N}, ...] (project/knowledge/cases71.json banks 用的形状)."""
        for sp in specs:
            kw = dict(symbol=sp.get("sym"), addr=sp.get("addr"),
                      region=sp.get("region"), offset=sp.get("offset"),
                      length=sp.get("len", 1))
            self.add(sp.get("name") or str(kw.get("symbol") or kw.get("addr") or "p%d" % len(self.points)), **kw)
        return self

    def errors(self):
        return [(p.name, p.err) for p in self.points if p.err]

    # ---- 快照 ----
    def snapshot(self, ser, quiet=True, wait=1.8):
        """读全部观察点 -> {name: bytes|None}. 定址失败/应答异常的点为 None."""
        out = {}
        for p in self.points:
            out[p.name] = self._read(ser, p, quiet=quiet, wait=wait)
        return out

    def _read(self, ser, p, quiet=True, wait=1.8):
        if p.err:
            return None
        fr = read_aa80_645(p.region, p.offset, p.length)      # 表地址默认 0x11×6 (TABLE_ADDR)
        # ⚠ 原样传 `fr`, **不许 `bytes(fr)`** —— 那会把 Frame 降级回裸 bytes, 协议字段丢光
        #   (同 send_frame 里那条注)。它本来就是 bytes。
        rx = tx_recv(ser, fr, wait=wait, tag="aa80_" + p.name, peer="管理芯",
                     what="直读 观察点 %s(区%d 偏移0x%X %dB)" % (p.name, p.region, p.offset, p.length),
                     quiet=quiet)                             # quiet 只关"给人看的那一路", 事件流照发
        cmd, seg, _ = decode_645_reply(rx)
        if cmd == 0x91 and len(seg) >= 4:
            return bytes(seg[4:])                              # 应答 = 4B 地址回显 + N 原样字节
        return None

    # ---- 前后对比 ----
    def diff(self, pre, post):
        """pre/post 都是 snapshot() 输出 -> {name: (changed, [(i, old, new), ...])}.
        None 视为缺席: 缺席<->有值 = changed(出现/消失)。"""
        out = {}
        for p in self.points:
            n = p.name
            a, b = pre.get(n), post.get(n)
            if a is None or b is None:
                out[n] = (a is not None or b is not None, [])
                continue
            diffs = [(i, a[i] if i < len(a) else 0, b[i] if i < len(b) else 0)
                     for i in range(max(len(a), len(b)))
                     if (i >= len(a) or i >= len(b) or a[i] != b[i])]
            out[n] = (bool(diffs), diffs)
        return out

    @staticmethod
    def fmt_shot(shot):
        return {n: (b.hex(" ").upper() if b else "(无)") for n, b in shot.items()}


def wait_change(ser, bank, baseline=None, timeout=60, poll=1.5, quiet=True, wait=1.8):
    """轮询 bank.snapshot 直到与 baseline 相比有变化(或出现值), 用于"等冻结/等事件落库/等跨点"。
    返回 (changed: bool, last_shot). baseline=None -> 以第一次快照为基线(变化相对它)."""
    t0 = time.time()
    shot = None
    while time.time() - t0 < timeout:
        shot = bank.snapshot(ser, quiet=quiet, wait=wait)
        base = baseline if baseline is not None else shot
        if baseline is None:
            return True, shot                     # 第一次即基线
        d = bank.diff(base, shot)
        if any(changed for changed, _ in d.values()):
            return True, shot
        time.sleep(poll)
    return False, (shot if shot is not None else {})


def hexd(b):
    return b.hex(" ").upper() if b else "(无)"
