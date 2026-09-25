# -*- coding: utf-8 -*-
"""
meterlib/p645.py —— DL/T645-07 协议: 组帧 / 校验 / 解码 / 标准号

**一个协议一个文件。** 本文件里只有 645 —— 它的帧怎么组、怎么校、怎么解、标准 DI 与指令码,
以及 AA80 直读这个入口。

AA80 为什么在这里(而不是独立成第三个协议文件)
--------------------------------------------
看一眼 `read_aa80_645` 的组帧就知道: 它走的就是 645 的 `C=0x11` 读, DI 尾三字节固定 `AA 80 04`,
负载 = 4B 区内偏移(LE) + 1B 长度。它不是另一个协议, 是 645 的一个**寻址入口**(厂商留的远程
调试通道)。它自带的只有"绝对地址 → (区号, 区内偏移)"这条折算, 那是 AA80 的语义, 一并收在这里。

不在这里的东西(有意, 不是漏)
----------------------------
  · 串口怎么开、怎么收发             -> `common/portsel.py`(传输层, 与协议无关)
  · "这个口后面是不是本表"           -> `meterlib/p698.py`(要靠发一条 698 读时钟帧证明)
  · 读回怎么判过 / 用例怎么拼 / 记录怎么落库 -> `meterlib/cmd_bank.py`(语义层与用例层)
  · 冻结/事件记录的对象号与记录 OAD  -> `meterlib/p698.py`(那些是 698 的对象模型)

运行(在 帧收发基础/src 下, 与其他 `python -m` 同规矩):
    python -m meterlib.p645          # 离线自检(不开串口)
"""
import sys


# 画像经**中立层**取, 不 import project(2026-09-10 阶段二)。用 Proxy 而不是 `P = profile.current()`:
# 本模块在**模块级**取画像(TABLE_ADDR), 直接取会在 import 期把画像冻住, 且强依赖"先 import project
# 再 import meterlib"。代理每次访问现取。没有画像时 TABLE_ADDR 是 None 而不是抛 —— 645 的组帧与
# 校验是**通用协议知识**, 不该因为"手上没有某块表"就 import 不进来(与 p698 的 ADDR_698 同一条边界)。
from common import profile
from common import loglabel          # 协议词表 + Frame(协议随帧走, 见 loglabel 模块头)
P = profile.Proxy()

# 本表 645 绝对地址(画像单一源); 无画像时 None, 同 p698.ADDR_698
TABLE_ADDR = profile.get("TABLE_ADDR")
BROADCAST_ADDR = b"\x99" * 6      # 645 广播地址(协议标准, 通用)


# ============================ 组帧 ============================
def frame_645(cmd, data, addr=None, preamble=True, proto=loglabel.PROTO_645):
    """
    645 帧: [FE*4] 68 + 地址(6) + 68 + 控制码 + 长度L + 数据域 + CS + 16
      * 数据域线上编码 = 值 +0x33/字节 (收帧先 -0x33)
      * CS = 全帧(含两个68,不含末尾16)逐字节累加和取低字节   [本表实测, 见经验总结]
    例(进入厂内): cmd=0x1F data=0F 55 FF addr=AA*6

    ⚠ 返回的是 `loglabel.Frame` —— 它**就是** `bytes`, 只是把"这一帧是哪个协议"钉在字节上
      (发帧/判定两行都从它取, 所以两处不可能标成两个协议)。默认 `645`; **AA80 那一族**走
      645 封装但要单列一格, 由 `read_aa80_645` 传 `proto=PROTO_AA80` —— 那是全仓唯一一处例外,
      因为"读的是私有 RAM 区"与"读一块普通 DI"在日志里必须一眼分得出。
    """
    addr = addr if addr else b"\xAA" * 6
    enc = bytes((b + 0x33) & 0xFF for b in data)                    # +0x33 编码
    body = b"\x68" + addr + b"\x68" + bytes([cmd, len(enc)]) + enc
    cs = (sum(body) & 0xFF)
    fr = body + bytes([cs]) + b"\x16"
    return loglabel.Frame((b"\xFE\xFE\xFE\xFE" + fr) if preamble else fr, proto)


def validate_645(b, allow_fe=True):
    """校验一帧 645-07 帧(容忍前置 FE×4). 返回 (ok, msg). 校验 双68/长度/CS累加低字节/尾16."""
    b = bytes(b)
    while allow_fe and b[:1] == b"\xfe":
        b = b[1:]
    if len(b) < 12 or b[0] != 0x68 or b[7] != 0x68:
        return False, "非 645 帧(缺68头/第二68/过短)"
    L = b[9]
    if len(b) != L + 12:
        return False, "长度 L=%d 与实际 %d 不符" % (L, len(b))
    if b[-1] != 0x16:
        return False, "缺帧尾 16"
    if (sum(b[0:10 + L]) & 0xFF) != b[10 + L]:
        return False, "CS 校验不符"
    return True, "645 帧校验通过"


# ============================ 读帧(标准 DI) ============================
# 时钟/电能/温度这几条是"本表可直接读回"的标准量, 语义名固定、DI 固定, 故收在协议层。
def read_time645_dt():
    """645 读 日期时间 DI 0400010C -> 应答7B BCD [秒分时周日月年]."""
    return frame_645(0x11, bytes.fromhex("0C 01 00 04"), addr=TABLE_ADDR)


def read_time645_date():
    """645 读 日期及星期 DI 04000101 -> [周日月年]."""
    return frame_645(0x11, bytes.fromhex("01 01 00 04"), addr=TABLE_ADDR)


def read_time645_time():
    """645 读 时间 DI 04000102 -> [秒分时]."""
    return frame_645(0x11, bytes.fromhex("02 01 00 04"), addr=TABLE_ADDR)


def read_elect645_total():
    """645 读 组合有功总电能 DI 00000000 (管理芯镜像值 == 计量芯 Watch)."""
    return frame_645(0x11, bytes.fromhex("00 00 00 00"), addr=TABLE_ADDR)


def read_temp645_e0():
    """645 0xE0 厂商读温度(子码 E0800007, 本地实现, s16 LE)."""
    return frame_645(0x11, bytes.fromhex("07 00 80 E0"), addr=TABLE_ADDR)


# ============================ AA80 直读入口(645 的一个寻址模式, 不是第三个协议) ============================
# 区基址/偏移常量(与 FM33A0XXEV.h 821-823 / CpuCfg.c 一致)
SRAM_BASE = 0x20000000
PERIPH_BASE = 0x40000000
INFO_BASE = 0x00080000
SRAM_TOP = 0x20040000      # FM33A0610 SRAM 上限(估; 超出会 base 判失败进而按 flash raw 处理)
AA80_MAX_LEN = 128


def abs_to_aa80(addr):
    """绝对地址 -> (region, offset)。按地址所在区间自动归区(只认本机能直读的三段 + flash raw)。"""
    if INFO_BASE <= addr < SRAM_BASE:                      # INFO 0x00080000 段
        return 4, addr - INFO_BASE
    if SRAM_BASE <= addr < SRAM_TOP:                       # 内部 RAM
        return 1, addr - SRAM_BASE
    if PERIPH_BASE <= addr:                                # APB
        return 2, addr - PERIPH_BASE
    return 0, addr                                         # flash 基址0 = raw


def read_aa80_645(region, addr, length, table_addr=None):
    """AA80 直读管理芯本地内存(真实外部直读入口, 非0xE0): CMD 0x11 LEN09.
    DI=[区号DI0,0xAA,0x80,0x04]; 负载=4B地址LE + 1B长度(≤128).
    ⚠ 地址=【区内偏移】非绝对: 固件 CpuCfg.c Read_*Data 按区加基址再读(RAM 0x20000000/APB 0x40000000/
    INFO 0x00080000; flash 基址0=绝对; EEPROM/外部flash/ESAM 用原始地址). 传绝对 RAM 地址会越界读挂死→整帧静默
    (2026-09-09 实测根因). .out 符号=绝对, 用前减基址. 区号: 0内部FLASH 1内部RAM 2APB 3外部EEPROM
    4INFO 5外部FLASH 6ESAM. 应答=4B地址回显+N字节原样. 见 对表操作总纲 并入「经验总结.md」§14."""
    if not (0 <= region <= 6):
        raise ValueError("AA80 区号 0..6, 实为 %r" % region)
    length = max(1, min(AA80_MAX_LEN, int(length)))
    pl = bytes([region, 0xAA, 0x80, 0x04]) + int(addr).to_bytes(4, "little") + bytes([length])
    # 全仓唯一一处 645 封装但要单列协议格的: 读私有 RAM/FLASH 区, 见 frame_645 的 docstring
    return frame_645(0x11, pl, addr=(table_addr or TABLE_ADDR), proto=loglabel.PROTO_AA80)


# ============================ 应答解码 ============================
def decode_645_reply(rx):
    """645 应答 → (命令字节, 数据域明文, 说明). 0x94=写成功 0x91=读 0xD4=错(下一字节=错误码).
    例: 0x14 写成功答 68 11 11 11 11 11 11 68 94 00 CA 16(CS=CA) → 返回 (0x94, b'', '')."""
    rx = rx or b""
    i = rx.find(b"\x68")
    if i < 0 or len(rx) < i + 11 or rx[i + 7] != 0x68:
        return None, b"", "无 645 应答帧"
    cmd = rx[i + 8]
    seg = bytes((x - 0x33) & 0xFF for x in rx[i + 10:i + 10 + rx[i + 9]])
    if cmd == 0xD4:
        return cmd, seg, ("ER_OTHER(0x01)" if seg and seg[0] == 0x01 else "err=0x%02X" % (seg[0] if seg else 0))
    return cmd, seg, ""


# ============================ 645 标准数据标识 / 指令码 ============================
# 出处: 原 meterlib/obj698.py 的 645 段(那是"698 + 645 混装"的常量库, 2026-09-18 按协议分家时归到这里)。
BILLDAY_DI_HEX  = "04000B01"     # 第1结算日 DI(0x11 读 / 0x14 写)
BILLDAY_READ_DI = bytes.fromhex("01 0B 00 04")   # 0x11 读第1结算日的数据域(与 overlay 同字节)
CLEAR_645_ALL   = 0x1A           # 645 电表清零(0x03 编程态)
CLEAR_645_EV    = 0x1B           # 645 事件清零


# ============================ 离线自检(不开串口) ============================
def selftest():
    """只验"字节长什么样": 组帧、校验、AA80 折算、解码。不碰串口、不需要画像(645 的组帧与表无关)。"""
    ok = True

    def chk(label, cond, extra=""):
        print("%-42s %s%s" % (label, "OK" if cond else "FAIL", ("  " + extra) if extra else ""))
        return bool(cond)

    # 1) 645 厂内帧应重建出已验证请求
    f = frame_645(0x1F, bytes.fromhex("0F 55 FF"))
    want = bytes.fromhex("FE FE FE FE 68 AA AA AA AA AA AA 68 1F 03 42 88 32 EA 16")
    ok &= chk("645 厂内帧逐字节", f == want, f.hex(" ") if f != want else "")

    # 2) 帧校验器应能通过已核实帧
    vok, vmsg = validate_645(frame_645(0x1F, bytes.fromhex("0F 55 FF")))
    ok &= chk("validate_645 通过已核实帧", vok, vmsg)

    # 3) 改一个字节应校验不过(否则"校验通过"是句空话)
    bad = bytearray(frame_645(0x1F, bytes.fromhex("0F 55 FF")))
    bad[13] ^= 0x01
    nok, _ = validate_645(bytes(bad))
    ok &= chk("validate_645 篡改一字节即不过", not nok)

    # 4) 绝对地址 → (区, 偏移) 折算(RAM 偏移必须 = 绝对 - 0x20000000)
    for a, want_rc in [(0x200089E4, (1, 0x89E4)), (0x200090B0, (1, 0x90B0)),
                       (0x00080000, (4, 0)), (0x40000000, (2, 0)), (0x0000A000, (0, 0xA000))]:
        got = abs_to_aa80(a)
        ok &= chk("abs_to_aa80 0x%08X" % a, got == want_rc,
                  "区%d 偏移0x%06X" % got if got == want_rc else "得到 %s, 应为 %s" % (got, want_rc))

    # 5) AA80 组帧(区1 RAM 偏移 0x9000): 是 645 帧, 且数据域里带了区号与长度
    fr = read_aa80_645(1, 0x9000, 8)
    ok &= chk("read_aa80_645 组帧(区1)", isinstance(fr, bytes) and fr.startswith(b"\xfe") and len(fr) > 20,
              "%dB" % len(fr))

    # 6) 解码: 读应答 0x91 要能取回明文; 错应答 0xD4 要能报出错误码
    ans = bytes.fromhex("68 11 11 11 11 11 11 68 91 00 CA 16")
    cmd, seg, _ = decode_645_reply(ans)
    ok &= chk("decode_645_reply 读应答 0x91", cmd == 0x91 and seg == b"")
    err = bytes.fromhex("68 11 11 11 11 11 11 68 D4 01 CE 16")
    cmd2, seg2, why = decode_645_reply(err)
    ok &= chk("decode_645_reply 错应答 0xD4", cmd2 == 0xD4 and len(seg2) == 1, why)

    print("SELFTEST p645:", "ALL PASS" if ok else "HAS FAILURE")
    return ok


def main(argv=None):
    from common.console import ensure_utf8_stdout
    ensure_utf8_stdout()
    print("== meterlib.p645 离线自检(不开串口) ==")
    ok = selftest()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
