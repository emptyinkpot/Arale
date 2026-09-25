# -*- coding: utf-8 -*-
"""
meterlib/p698.py —— DL/T698.45 协议: 组帧 / 校验 / 解码 / 对象模型 / 开表口

**一个协议一个文件。** 本文件里只有 698。

装的是什么
----------
  · 组帧: `frame_698` 与一组 `build_*_apdu`(读 / 读记录 / 动作 / 写 / 校时)
  · 校验: `validate_698`(L / HCS / FCS / 尾16)
  · 解码: `split_apdu` / `decode_*` / `dar_from_ud` / `ud_record_count` / `DAR` 表
  · 对象模型: 记录 OAD / 冻结子类名 / 事件编码与事件记录 OAD / 标准对象号(原 obj698.py 的 698 段)
  · **开表口**: `open_com` 一族的"证明口后面是本表"那一段 —— 见下面

开表口为什么在这里(而不是在 common/portsel.py)
----------------------------------------------
"本机该用哪个串口"分两半:

    判据(哪个 USB-485 桥) + 枚举 + 收发   → `common/portsel.py`(零协议)
    **口后面是不是本表**                  → 这里

后半截**必须发一帧去问**, 而那一帧是 698 的(按本表服务器地址单播读表钟)。让 common 认识 698,
就把中立层弄脏了。所以这一段留在这里, 由本模块 import 时用 `portsel.set_probe()` 装进去
(装配点与 common/varresolve 的 configure 同一套做法)。

⚠ 将来换通路(比如 IIC), 要换的是 `handshake_clock` 这一句, 不是整个 `open_com`。

不在这里的东西(有意, 不是漏)
----------------------------
  · 645 的任何东西                    -> `meterlib/p645.py`
  · 串口对象 / 收发 / 选口            -> `common/portsel.py`
  · 读回怎么判过 / 用例怎么拼 / 记录怎么落库 -> `meterlib/cmd_bank.py`
"""
import time


# 画像经**中立层**取, 不 import project(2026-09-10 阶段二)。用 Proxy 而不是 `P = profile.current()`:
# 本模块在**模块级**取画像(ADDR_698), 直接取会在 import 期把画像冻住, 且强依赖"先 import project
# 再 import meterlib"。代理每次访问现取。
from common import profile
P = profile.Proxy()

from common import portsel            # 传输与选口(零协议的一半); 本模块往里装"探活"
from common import loglabel           # 协议词表 + Frame(协议随帧走, 见 loglabel 模块头)


# ============================ CRC-16/X.25 (HCS 与 FCS 均用) ============================
def crc_x25(data):
    """CRC-16/X.25: poly 0x8408(reflected 0x1021), init 0xFFFF, refin/refout true, xorout 0xFFFF"""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return (crc ^ 0xFFFF) & 0xFFFF


# ============================ 组帧 ============================
# 服务器地址 = AF(管理芯0x05) + 表号6B + CA(0xA1); AF/表号是工程事实, 收在画像里。
# ⚠ 没有画像时这两条**是 None 而不是抛** —— 服务器地址是每表事实, 没表就没有它, 这是实话;
#   而本模块的 CRC/组帧/解析是**通用协议知识**, 不该因为"手上没有某块表"就 import 不进来。
#   (与 swdbg 的"零画像"是同一条边界。)
_MANAGE_AF = profile.get("MANAGE_AF")
ADDR_698 = (bytes([_MANAGE_AF]) + P.SERVER_TAIL) if _MANAGE_AF is not None else None
KNOWN_READ_FRAME = bytes.fromhex(
    "6817004305111111111111A16892050103200A0000008EDB16")   # 读请求(APDU 05 01 03 200A0000 00)


def frame_698(apdu, ctrl=0x43, addr=None):
    """
    组装一帧 DL/T698.45 用户数据。
    帧: 68 + L(2LE) + 控制 + 服务器地址(8) + HCS(2) + APDU + FCS(2) + 16
      * L = 全帧(68..16 含)总字节数 - 2  (即 15 + len(APDU))
      * HCS 计算范围 = 帧[1:12]   (长度字节+控制+地址)  存 [12:14], 小端
      * FCS 计算范围 = 帧[1:-3]   (含HCS与APDU)         存 [-3:-1], 小端
      * 控制字: 0x43=请求(客户->服务器)  0xC3=应答(服务器->客户)
    addr 缺省 = 画像里的服务器地址; 没有画像(拿本库当通用 698 组帧器用)就**必须显式传**。
    """
    if addr is None:
        addr = ADDR_698
    if not addr:
        raise RuntimeError(
            "frame_698: 既没给 addr, 当前也没有画像。698 的服务器地址(AF+表号+CA)是每表事实, "
            "拿本库当通用组帧器用时请显式传 addr=bytes([AF])+表号+CA。")
    total = 17 + len(apdu)
    L = total - 2
    pre = bytes([L & 0xFF, (L >> 8) & 0xFF, ctrl]) + addr           # 帧[1:12]
    hcs = crc_x25(pre)
    hcs_b = bytes([hcs & 0xFF, (hcs >> 8) & 0xFF])
    fcs = crc_x25(pre + hcs_b + apdu)
    fcs_b = bytes([fcs & 0xFF, (fcs >> 8) & 0xFF])
    # 返回 `loglabel.Frame`: 它就是 bytes, 只是把"这一帧是 698"钉在字节上 —— 见 p645.frame_645
    return loglabel.Frame(b"\x68" + pre + hcs_b + apdu + fcs_b + b"\x16", loglabel.PROTO_698)


def build_read_apdu(pIID, oad_hex, follow=0x00):
    """读一个普通对象属性: 05 01 <PIID> <OAD4> 00(无时标标志)。OAD 例 '200A0000'."""
    return bytes([0x05, 0x01, pIID]) + bytes.fromhex(oad_hex) + bytes([follow])


# ==================== 冻结/事件记录读回: GetRequestRecord (记录必须走它, 普通 GET 只回空) ====================
# OAD 记录形态 = [0x50|0x30][子类/编码][属性][0x00]; 应答服务字节 = 0x03(GetResponseRecord 85 03)
# 子类: 0x00=瞬时冻结, 0x02=分钟冻结, 0x03=小时冻结 (读回走法原见 归档/probe_frez_readback.py / CLAUDE 硬性规矩4)
#
# ⚠ **属性字节按记录的表项类别分三路, 不是恒 0x02**(2026-09-17 实表 A/B + 源码定位, 见下)。
#   规范书写形 `30<编码>0B<属性>`(本模块 EVENT_REC_OAD) 的第3字节 0x0B 是**类别标记**不是属性
#   (表头注释 DLT698App.c:2938:「xxxx0B0A 表示事件记录类, xxxx0F0A 表示自定义事件记录类」),
#   直接发会被拒 —— 请求里的属性字节另由 `TAB_RecordObj[].Class` 决定。
#   `CMD_GetRequestRecord`(DLT698App.c:4746, 判定在 :4806-4832)三支:
#     · Class == 9(冻结记录) / Class == 7(普通事件: 编程/清零/校时/开盖/拉合闸/负荷开关误动…)
#         → `if (pOAD[2] != 2) DAR = DAR_Undefined;`          ⇒ 属性 = **0x02**
#     · Class == 24(A/B/C 类事件: 失压/欠压/过压/断相/失流/过流/过载/功率反向/不平衡…)
#         → `if (pOAD[2] >= 10 - evenum && pOAD[2] <= 9)`      ⇒ 属性 ∈ **[10-evenum, 9]**
#           且 `id = TAB_RecordObj[sch].id + pOAD[2] + evenum - 10` ⇒ **属性选的是事件级别**,
#           `10-evenum` 就是表里那一项的**基级**(A 相 / 总)。过载(evenum=3)⇒ 7=A/8=B/9=C;
#           功率反向(VER_20Edit 下 evenum=4)⇒ 6=总/7=A/8=B/9=C。
#         ⚠ 这一支发 0x02 得到的正是 `DAR_Undefined`(=4, TaskComm.h) —— 现象是"读回被拒 DAR=4"。
#   冻结 OAD 本就是 `50<子类>0200`, 归一化后不变(幂等)。
#   实表 A/B(2026-09-17, 进厂内后): 过载/失压 属性 02/06 ⇒ DAR=4, 07/08/09 ⇒ 规范 85 03 无 DAR;
#   功率反向 06/07/08/09 ⇒ 规范 85 03 —— 边界恰随 evenum 由 3 变 4 左移一格, 与 `10-evenum` 吻合。
FREEZE_SUB = {"immed": 0x00, "min": 0x02, "hour": 0x03}
FREEZE_RCSD = bytes.fromhex("02 00 20 23 02 00 00 20 21 02 00")  # 只选两列: 记录序号 + 冻结时间
# 事件记录读回默认列(编程等"写参/定义类"事件): 记录序号 20220200 + 发生时间 201E0200 (OAD 见本模块)
EVENT_RCSD = bytes.fromhex("02 00 20 22 02 00 00 20 1E 02 00")


def record_req_oad(oad4, attr=0x02, index=0x00):
    """规范书写 OAD → 记录型对象请求 OAD(前两字节照抄, 第3字节=属性, 第4字节=索引)。

    `attr` **默认 0x02** —— 那是 Class 9(冻结)/Class 7(普通事件)的正确值, 也是 T 表的通用口径;
    **Class 24(A/B/C 类事件)必须另给**(过载 0x07、功率反向 0x06), 理由与取值见上段说明。
    调用方拿不准时先用 0x02 发一次, 若回 `DAR=4` 且该事件属 Class 24 ⇒ 换 `10-evenum`。
    幂等(attr/index 默认时), 见上段说明。
    """
    o = bytes.fromhex(oad4) if isinstance(oad4, str) else bytes(oad4)
    if len(o) != 4:
        raise ValueError("OAD 须 4 字节, 收到 %r" % (oad4,))
    return bytes([o[0], o[1], attr & 0xFF, index & 0xFF])


def build_getrecord_apdu_oad(pIID, oad4, rsd=b"\x09\x01", rcsd=FREEZE_RCSD, follow=0x00,
                             attr=0x02, index=0x00):
    """GetRequestRecord 通用版: 05 03 <PIID> <OAD4> <RSD> <RCSD> 00.
    冻结 OAD=50<子类>0200, 事件 OAD 书写形 30<编码>0B<属性>(见本模块 EVENT_REC_OAD) ——
    发前经 record_req_oad 归一化。
    `attr` = 请求里的属性字节, 按记录表项类别取(冻结/普通事件 2; Class 24 事件 10-evenum), 见上段说明。
    供 read_freeze_row/read_event_row 复用."""
    return (bytes([0x05, 0x03, pIID]) + record_req_oad(oad4, attr=attr, index=index)
            + bytes(rsd) + bytes(rcsd) + bytes([follow]))


def build_getrecord_apdu(pIID, sub_byte, rsd=b"\x09\x01", rcsd=FREEZE_RCSD, follow=0x00):
    """
    GetRequestRecord: 05 03 <PIID> <OAD[50][子类][02][00]> <RSD> <RCSD> 00
      RSD 默认 09 01 = 方法9 取最新1条; RCSD 默认只选 记录序号+冻结时间 两列。
      固件 CMD_GetRequestRecord 反推(见 归档/probe_frez_readback.py)。sub_byte 0x00瞬时/0x02分/0x03时。
    """
    oad = "50%02X0200" % (sub_byte & 0xFF)
    return build_getrecord_apdu_oad(pIID, oad, rsd=rsd, rcsd=rcsd, follow=follow)


def build_timetag698(y2, mo, d, h, mi, s, ti=1, gap=1):
    """时间标签(11 字节): `01 <年2B大端 2000+y2> <月 日 时 分 秒> <TI> <gap2B大端>`。

    布局与判定照固件 `Check_TimeTag`(DLT698App.c:16476-16533) 抄:
      · 长度必须恰 `tag + 1+7+3 == apduIn`(:16477) —— 少一字节多一字节都回 `DAR_ErrorApdu`(253);
      · 年必须在 2000..2999, 且月日时分秒要过 `Check_DateTime`, 否则回 `DAR_TimeStamp`(32);
      · `TI` = 时间单位: 0秒 / 1分 / 2时 / 3日 / 4月 / 5年(:16505-16526), gap 按它乘;
      · **放行条件只有一条**: `sec1(当前表钟) <= sec0(标签时刻) + gap`(:16530)。
        表钟**快过**"标签时刻 + gap"就回 `DAR_TimeStamp`; 表钟**慢**过标签时刻不判。
        ⇒ 拿刚读回的表钟当标签、`ti=1, gap=1`(一分钟窗口), 这一次交换就在窗口内。
    ⚠ 这个门是**单向**的: 想造"被时标拒掉"的那一次, 把标签时刻写到**表钟之前**超过 gap 即可
      (那不是台面缺陷, 是固件设计如此)。
    """
    year = 2000 + (y2 & 0xFF)
    return bytes([0x01, (year >> 8) & 0xFF, year & 0xFF, mo & 0xFF, d & 0xFF,
                  h & 0xFF, mi & 0xFF, s & 0xFF, ti & 0xFF,
                  (gap >> 8) & 0xFF, gap & 0xFF])


def build_action_apdu(pIID, omd_hex, param=None, timetag=None):
    """
    操作一个对象方法: 07 01 <PIID> <OMD4> [参数数据元素...] <时标域>。
    参数必须是一个完整"数据元素"(类型字节+内容),由 Check_TimeTag 解析长度。
    例: 广播瞬时冻结 OMD=50000300 参数=延时long-unsigned(0x12 0000)
        -> 07 01 03 50 00 03 00 12 00 00 00

    `timetag` 缺省 = 一字节 `00`(无时标)。**有些 OMD 明文要求带时标** ——
    `Action_Control`(DLT698App.c:11652) 里 0x80007F/0x800080/0x800081/0x800082/0x800083/
    0x80017F/0x800180 **每一支**都先判 `g_TimeTag[0] != 0x01` 就 `DAR = DAR_TimeStamp`(:11763 保电、
    :11778 解除), 于是不带时标的帧**根本到不了** `Set_RelayCmdR` —— 线上是 DAR=32, 断点不命中。
    带时标就用 `build_timetag698(...)` 造的那 11 字节。
    """
    out = bytes([0x07, 0x01, pIID]) + bytes.fromhex(omd_hex)
    if param:
        out += param
    return out + (bytes(timetag) if timetag else bytes([0x00]))


# ==================== 校时 (审计纪要 §4, 时间编码见其 §4) ====================
# 时间编码: 内部 g_MeterTime[6]=[秒分时日月年](年=2位,2000基); RTC BCD.
#   698 线 D_DateTimeS = 1C <绝对年2B大端2000+y2> <月日时分秒>;  645 线 = BCD 2 位年(见 p645).
def build_set_apdu(pIID, oad4, data):
    """698 Set-Request-Normal(服务 0x06) **通用**: `06 01 <PIID> <OAD4> <数据域> 00`。
    `data` = 已编码好的数据域字节(类型外壳由调用方按对象给, 本函数不猜) —— 写任意对象都走这一个,
    别再为每个 OAD 抄一份 `06 01 …`(build_settime_apdu 就是它在时间对象上的特化)。
    ⚠ OAD 必须传 **4 字节**(OI2 + 属性1 + 索引1, 如 `40150201`); 只传 3 字节(`401502`)会组出
      长度不足的 APDU, 固件 `DLT698App.c:16270` 的 `if (apduIn <= tag) return DAR_ErrorApdu;`
      直接回 **DAR=253 错误APDU** —— 不是"对象不存在"也不是"拒绝操作", 别误读成"这对象写不了"。
    应答形 = `86 01 <PIID> <OAD4> <DAR> 00 00`(判读用 decode_set_ack)。"""
    return (bytes([0x06, 0x01, pIID & 0xFF]) + bytes.fromhex(oad4.replace(" ", ""))
            + bytes(data) + bytes([0x00]))


def build_settime_apdu(pIID, y2, mo, d, h, mi, s):
    """698 Set 写表钟 OAD 40000200 —— OOPT 成功校钟的命令形(实测日志 TestResult2609081735).
    时间 = 1C <年2B绝对大端 2000+y2> <月 日 时 分 秒>; 应答 = 86 01 <PIID> 40 00 02 00 <DAR> 00 00.
    只发到 计量芯(AF=0x15)才被接受(应答 DAR=00); 管理芯(AF=0x05)同帧被拒(DAR=FF, 其 40000200 Set 已注释禁用).
    区别: 方法127 Action 校时(0x40007F00)有 60-300s/同日/每日一次 明文判定; Set 写对象直落, 无该判定(计量芯)."""
    year = 2000 + (y2 & 0xFF)
    elem = bytes([0x1C, (year >> 8) & 0xFF, year & 0xFF, mo & 0xFF, d & 0xFF,
                  h & 0xFF, mi & 0xFF, s & 0xFF])
    return build_set_apdu(pIID, "40000200", elem)


# ============================ 帧校验(供叠加层/调用方校验) ============================
def validate_698(b, allow_fe=True):
    """校验一帧 698 用户数据(容忍前置 FE×4). 返回 (ok, msg). 逐字节校验 L/HCS/FCS/尾16."""
    b = bytes(b)
    while allow_fe and b[:1] == b"\xfe":
        b = b[1:]
    if len(b) < 17 or b[0] != 0x68:
        return False, "非 698 帧(无68头或过短)"
    L = b[1] | (b[2] << 8)
    if len(b) != L + 2:
        return False, "长度字节 L=%d 与实际 %d 不符" % (L, len(b))
    if b[-1] != 0x16:
        return False, "缺帧尾 16"
    if (b[12] | (b[13] << 8)) != crc_x25(b[1:12]):
        return False, "HCS 校验不符"
    if (b[-3] | (b[-2] << 8)) != crc_x25(b[1:-3]):
        return False, "FCS 校验不符"
    return True, "698 帧校验通过"


# ============================ 应答解码 ============================
def split_apdu(rx):
    """从应答帧里切出 APDU(去掉 68 L C 8addr HCS 与 FCS+16)。失败返回 b''."""
    i = rx.find(b"\x68")
    if i < 0 or len(rx) - i < 15:
        return b""
    return rx[i + 14: len(rx) - 3]


def decode_action_ack(rx):
    """
    正常动作应答 APDU: 87 01 <PIID> <OMD4> DAR dataflag [data]
    异常(高层)应答 APDU: EE 00 (DAR-252) ...   (ErrorApdu=253->01, NotSupport=254->02)
    返回 dict 或 None.
    """
    ud = split_apdu(rx)
    if not ud:
        return None
    if ud[0] == 0x87:
        return {"service": "ActionResponse", "piid": ud[2],
                "omd": ud[3:7].hex(" ").upper(), "dar": ud[7],
                "dataflag": ud[8] if len(ud) > 8 else None}
    if ud[0] == 0xEE:
        return {"service": "ErrorResponse", "dar": 252 + ud[2]}
    return {"service": "?", "raw": ud.hex(" ")}


def decode_set_ack(rx):
    """
    SetResponse 应答 APDU: 86 01 <PIID> <OAD4> <DAR> 00 00  (DAR 在偏移7)
    异常(高层)应答 APDU: EE 00 (DAR-252) ...   (ErrorApdu=253->01, NotSupport=254->02)
    返回 dict 或 None.
    """
    ud = split_apdu(rx)
    if not ud:
        return None
    if ud[0] == 0x86 and len(ud) >= 8:
        return {"service": "SetResponse", "piid": ud[2],
                "oad": ud[3:7].hex(" ").upper(), "dar": ud[7]}
    if ud[0] == 0xEE:
        return {"service": "ErrorResponse", "dar": 252 + ud[2]}
    return {"service": "?", "raw": ud.hex(" ")}


def decode_ts_698(b, j):
    """j 指向时标类型字节 0x1C → 其后的 7B 是 <年2B大端 月 日 时 分 秒> → 返回 'YYYY-MM-DD HH:MM:SS'.
    年=2B 大端(2025 → 07 E9), 后 5B 各 1B: 月 日 时 分 秒."""
    t = b[j + 1:j + 8]
    if len(t) < 7:
        return None
    return "%04d-%02d-%02d %02d:%02d:%02d" % ((t[0] << 8) | t[1], t[2], t[3], t[4], t[5], t[6])


def decode_clock(rx):
    """698 读时钟应答 → 时间串或 None. 应答 GetResponse 85 01 携带属性 40 00 02 00 的值,
    值数据元素 = 类型字节 D_DateTimeS(0x1C) + 7B时间; 在应答数据里定位 0x1C 即读到表钟,
    把位置交给 decode_ts_698 解析成 'YYYY-MM-DD HH:MM:SS'."""
    ud = split_apdu(rx or b"")
    b = ud if ud else (rx or b"")
    j = b.find(b"\x1c")
    return decode_ts_698(b, j) if j >= 0 else None


def record_seq_at(ud):
    """在**已切好的 APDU**里定位记录序号元素 `06 <序号4B大端>` 的起点 → 那个 `06` 的下标; 没有 → None.
    序号列的取法是 `ud[i + 4]`(元素末字节 = 序号低字节), 调用方照这个下标取.

    为什么不能一行 `rfind(b"\\x06\\x00\\x00\\x00")` 了事: 那个四字节串在**时标里会自己长出来**。
    时标是 `1C <年2><月><日><时><分><秒>`, 当"分"=0x06、"秒"=0x00 时, 连上时标后面那两个 00,
    就凑出一个假的; `rfind` 从尾部找, 正好先撞上假的, 紧随的序号字节落到界外。
    2026-09-20 实测: 一条时标 16:06:00、序号 151 的冻结记录把 `decode_freeze_row` 崩在 IndexError.

    判真的凭据: 这个元素的**后面还得有时标的 0x1C** —— 假的那些后面跟着的是时标尾部,
    没有下一段元素可找。取**最后一个**真的(多行应答取尾部那一条)。"""
    i = -1
    k = ud.find(b"\x06\x00\x00\x00")
    while k >= 0:
        if k + 5 < len(ud) and ud.find(b"\x1c", k + 5) >= 0:
            i = k                       # 真的: 记下, 继续往后找(多行应答取最后一行)
        k = ud.find(b"\x06\x00\x00\x00", k + 1)
    return i if i >= 0 else None


def record_time_at(ud, seq_at):
    """序号元素之后第一个时标 `1C` 的下标; 没有 → -1.

    **时标只许从序号元素之后起找。** 反过来也一样会翻车: 序号的低字节本身可能就是 0x1C
    (序号 28、284 …), 从头 `find(b"\\x1c")` 命中的是它, 不是时标, 切出来的整列就整体错位。
    `seq_at` 由 `record_seq_at` 给出; 元素占 `seq_at .. seq_at + 4`(一个类型字节 + 4B 序号),
    所以下一段元素自 `seq_at + 5` 起。"""
    return ud.find(b"\x1c", seq_at + 5)


def decode_freeze_row(rx):
    """698 读记录应答(GetResponseRecord 85 03) → (记录序号, 时标串|None) 或 None。

    走 `record_rows` 逐列解，不再作字形扫描。**记录序号取第一列** —— 那是本仓构造 RCSD 的
    约定（`EVENT_RCSD` 与冻结的列选都把序号列放在最前），不是对固件的猜测；时标取**第一个
    解出时间的列**（事件读回与冻结读回各只有一列时间列，故这里不必按 OAD 挑）。
    多行应答取**尾部那一行**（与旧实现一致：`record_seq_at` 也取最后一个）。
    **时标列回 D_NULL 时 `ts=None`** —— 那是"固件按设计没写这一格"，不是解不出行。"""
    rows = record_rows(split_apdu(rx or b""))
    if not rows:
        return None
    row = rows[-1]
    vals = list(row.values())
    seq = vals[0] if vals else None
    ts = next((v for v in vals if isinstance(v, str)), None)
    return (seq, ts)


def dar_from_ud(ud):
    """**已切好的 APDU**(`split_apdu` 的产物) 里判"记录读回被拒" → DAR 值(int) 或 None。

    单独拆出来是因为调用方手里未必还有原始 RX: `cmd_bank.read_lostpower_rows` 拿到的就是 ud
    (它经 `read_event_ud` 已切过)。**判据只有这一份** —— `decode_getrecord_dar` 也调它,
    否则"挨了 DAR 打回"会在两条路上各判一次, 迟早一条说"被拒"、另一条说"没记录"。
    """
    if len(ud) < 10 or ud[0] != 0x85 or ud[1] != 0x03 or ud[7] == 0:
        return None
    off = 8 + ud[7] * 5              # PIID(3)+OAD(4)+列数(1)+列(5n) 之后
    if len(ud) <= off + 1 or ud[off] != 0x00:
        return None
    return ud[off + 1]


def decode_getrecord_dar(rx):
    """记录读回被拒时的判读(原始应答帧入口)。错误应答 = 85 03 <PIID> <OAD4> <RCSD(1+n*5)> 00 <DAR>
    (固件 CMD_GetRequestRecord 收尾: DAR!=成功 → choice=0x00 + DAR)。返回 DAR 值(int);
    不是错误应答(即成功应答 choice=0x01, 或帧不全)返回 None。2026-09-10 用真表 DAR=4 校准。"""
    return dar_from_ud(split_apdu(rx or b""))


def ud_record_count(ud):
    """成功应答(`85 03`)里的**记录条数** → int; 不是成功应答 → None(那种情况归 `dar_from_ud` 管)。

    源 = 固件 `CMD_GetRequestRecord` 收尾: RCSD 回显之后 `pOutput[outAddr++] = 1`(记录集头, :5630)
    → `numAddr = outAddr` → `pOutput[outAddr++] = 0` 占位(:5633) → 循环里数 → `pOutput[numAddr]
    = ReadRecordNum` 回填(:5922)。故**条数 = RCSD 回显之后第 2 个字节**。错误应答那一支(:5943-5949)
    把同一个位置写成 `0x00` + `DAR` ⇒ 两者靠这一字节的 1/0 分开, 与 `dar_from_ud` 同一处判据。
    ⚠ `off` 算式**只有一份**(这里与 `dar_from_ud` 各写一遍就迟早分叉: 一条说"被拒"、一条说"空")。
    条数 0 = **记录区为空**(该事件一条都没有)—— 那是**台面事实**, 与"形状不认识/解不出"(
    我们解码器的锅)必须分得开: 混成一句会把"基线本来就没有记录"说成"读不回来"。
    """
    if len(ud) < 10 or ud[0] != 0x85 or ud[1] != 0x03 or ud[7] == 0:
        return None
    off = 8 + ud[7] * 5
    if len(ud) <= off + 1 or ud[off] != 0x01:
        return None
    return ud[off + 1]


# ---------------------------- 记录应答的**行解码**（单一实现） ----------------------------
# 源 = 固件 `CMD_GetRequestRecord` 的应答写法（DLT698App.c:5624-5820 逐列写）＋ 真表应答实据
#      （4-6 的冻结行 / 5-3 的掉电行 / 5-8 的两趟：发生时间列有值、结束时间列 D_NULL）。
# 行里的列**按类型字节定长**，所以逐列走一遍就切得准 —— 不必再靠"找 06 00 00 00 再找 1C"
# 那种字形扫描：字形会被时标里凑出来的字节骗到（见 `record_seq_at` 那段），而列长不会。
# ⚠ 还有两处**按位置**切列的老代码没并过来：`cmd_bank.record_energy_cols` 与
#   `cmd_bank.freeze_settle_kwh_raw`。它们要的是某一列的**原始字节**，本节交回的是解出来的值；
#   应答骨架与元素长度的定义只有本节这一份，那两处另外只依赖"时标之后紧跟哪一列"。
REC_TAIL = 2      # 行之后固定在应答末尾的 2B 外壳（`split_apdu` 已经剥掉 FCS 与结束符，这 2B 还在）
# 类型字节 → 值长度（不含类型字节自身）。只收**已证实**的三种，别的一律当"这一行不认识"（返回
# None），**不按别处的长度凑数** —— 凑出来的长度会把整行的列界带偏，错得还很像真的。
ELEM_LEN = {0x00: 0,      # D_NULL：没有值
            0x06: 4,      # D_DoubleLongUn
            0x1C: 7}      # D_DateTimeS
OAD_EV_OCCUR = 0x201E0200     # 事件·发生时间列
OAD_EV_END = 0x20200200       # 事件·结束时间列


def record_head(ud):
    """成功应答（`85 03`）的骨架 → `(列 OAD 列表, 条数, 第一行起点)`；不是成功应答 → None。

    骨架 = `85 03 <PIID> <OAD4> <列数n> <n × (列选择 00 + OAD4)> 01 <条数> <行…> <2B 外壳>`。
    条数有 1B（< 128）/ `81` +1B / `82` +2B 三种写法（源 = 固件 DLT698App.c:5920-5936），
    三种都得认 —— 只认 1B 的那种，条数过了 127 就会把行的起点算到条数字节里去。"""
    if not ud or len(ud) < 10 or ud[0] != 0x85 or ud[1] != 0x03:
        return None
    n = ud[7]
    if n == 0:
        return None
    end_cols = 8 + n * 5
    if len(ud) <= end_cols + 1 or ud[end_cols] != 0x01:
        return None
    oads = []
    for i in range(n):
        if ud[8 + i * 5] != 0x00:            # 列选择的 CHOICE 只认 0(=OAD)
            return None
        oads.append(int.from_bytes(ud[9 + i * 5:13 + i * 5], "big"))
    k = end_cols + 1
    b = ud[k]
    if b < 0x80:
        cnt, k = b, k + 1
    elif b == 0x81 and len(ud) > k + 1:
        cnt, k = ud[k + 1], k + 2
    elif b == 0x82 and len(ud) > k + 2:
        cnt, k = (ud[k + 1] << 8) | ud[k + 2], k + 3
    else:
        return None
    if len(ud) < k + REC_TAIL:
        return None
    return oads, cnt, k


def record_rows(ud):
    """`85 03` 应答 → `[{列OAD: 值|None}, …]`（按行序）；骨架不是成功应答 → None。

    某一列的类型不在 `ELEM_LEN` 里（或剩余长度不够）时，**这一行就停在那一列**，交回已经走通
    的那些列 —— 不整行作废。理由：要的两列（序号、时标）在各处列选里都排在最前，而冻结行末尾
    那一列（电量整列）本仓没有它的类型长度；为这个缺口把整行判成"解不出"，就是拿解码器的缺口
    去否固件的应答。调用方按 OAD 取列，取不到与该列回 `D_NULL` 都读成 None（都表示"这一格没值"）。

    值：`D_DateTimeS` → `'YYYY-MM-DD HH:MM:SS'`；`D_DoubleLongUn` → int；
    **`D_NULL` → None**。三者必须分得开 —— NULL 是**固件按设计没写这一格**（如最新一条事件
    还没结束，固件在 DLT698App.c:5788-5798 给结束时间列写 NULL），把它读成"解不出行"会把一个
    合设计的应答判成协议侧故障（5-8 实踩：旧解码器要"序号元素后面还得有个 1C"，NULL 那一格
    没有 1C，于是整行被判 FAIL）。"""
    head = record_head(ud)
    if head is None:
        return None
    oads, cnt, k = head
    tail = len(ud) - REC_TAIL
    out = []
    for _ in range(cnt):
        row = {}
        for oad in oads:
            if k >= tail:
                break
            t = ud[k]
            n = ELEM_LEN.get(t)
            if n is None or k + 1 + n > tail:
                break                  # 类型不在表里 / 剩余长度不够 ⇒ 这一列没走通
            if t == 0x00:
                row[oad] = None
                k += 1
            elif t == 0x1C:
                row[oad] = decode_ts_698(ud, k)
                k += 8
            else:
                row[oad] = int.from_bytes(ud[k + 1:k + 1 + n], "big")
                k += 1 + n
        if not row:
            break
        out.append(row)
        if len(row) < len(oads):
            # 这一行**没走完** ⇒ 下一行的起点不可知, 到此为止。交回走通的那些列, **不整行作废**:
            # 要的两列(序号、时标)在各处列选里都排在最前, 而冻结行尾部那一列(电量整列)本仓没有
            # 它的类型长度 —— 为解码器的这个缺口把整行判成"解不出", 就是拿缺口去否固件的应答。
            break
        if k >= tail:
            break
    return out or None


DAR = {0: "成功Success", 1: "硬件失效HardWare", 2: "临时失败TempFail", 3: "拒绝操作RefuseOp",
       4: "其它错误Undefined", 5: "类不符ClassId", 6: "对象不存在NoObject", 7: "类型不匹配WrongType",
       8: "越界OverRegion", 0x0B: "密码/未认证MatchAuth", 20: "安全认证不匹配MatchAuth",
       32: "时间标签无效TimeStamp",
       252: "不应答", 253: "错误APDU", 254: "服务不支持"}


# ============================ 698 标准对象模型(通用协议知识, 不跟某块表绑) ============================
# 存放电表领域【任何合规表都一样】的寻址元: 698 对象号(OAD)、冻结记录子类号→名、事件编码→记录 OAD。
# 这些是协议层的通用知识(换表不变), 语义动词/解码器按"语义名"从这里取号, 不在某表环境包(project/)
# 里重复声明 —— project 只留机器条件(表号/双芯分工/RAM 符号), 见 project/__init__.py。
#
# 与 cmd_bank.SPECS 的关系: SPECS 是"帧目录"(每条成品帧自带自己的 oad/omd 文本字段, 供检索/组帧,
# 属帧数据); 本段是"按语义名查号"的常量库(供语义函数/解码按名取号)。二者都属通用层(meterlib)。
# 冻结/电能/校时等号两处均有 —— SPECS 存帧、本段存语义常量; cmd_bank 里与 frame_698(...)/
# FREEZE_APDU 逐字节比对的自检兜底保证二者一致。
#
# 换一块表: 不重抄这些号(合规表通用); project 只覆写"这台表实测出的可达性/负知识", 不重声明标准号。
REC_SEQ_OAD       = "20230200"   # 冻结记录·记录序号列
REC_TIME_OAD      = "20210200"   # 冻结记录·冻结时间列
ENE_COMB_FULL_OAD = "00000400"   # 组合有功(4位小数)整列
ENE_FWD_FULL_OAD  = "00100400"   # 正向有功(4位小数)整列
# 写/动作触发用对象号(标准对象, 合规表同一号)。
CLOCK_SET_OAD   = "40000200"     # 表钟 698 Get/Set 对象
FREEZE_OMD      = "50000300"     # 广播瞬时冻结动作 OMD

# 冻结记录子类号 → 名(698 记录对象子类规范)。
# 全表出处 = 固件冻结记录对象表(DLT698App.c:3030-3045), 记录 OAD = 50 <子类> 02 00:
#   瞬时 0x50000303 / 分钟 0x500203FF / 小时 0x500303FE / 日 0x5004033E / 月结 0x5005030C /
#   月冻结 0x5006030C / 时区切换 0x50080302 / 时段切换 0x50090302 / 费率切换 0x500A0302 /
#   阶梯切换 0x500B0302 / 阶梯结算 0x50110304(即 ID_BillFrezY)。
FREEZE_SUBCLASS_NAME = {
    0x00: "瞬时", 0x02: "分钟", 0x03: "小时", 0x04: "日", 0x05: "月结算", 0x06: "月冻结",
    0x08: "时区切换", 0x09: "时段切换", 0x0A: "费率切换", 0x0B: "阶梯切换", 0x11: "阶梯结算",
}

# ==================== 事件记录(698 读回): 记录对象 OAD = 30 <事件编码> 0B <属性> ====================
# 与冻结同走 GetRequestRecord(服务 0x03), 但类字节 0x30(DLT698App.c:4810 显判 pOAD[0]∈{0x30,0x50}).
# 编码 byte1 = DL/T698.45 事件类型码; 多数 0B0A(记录属性0B·当前0A), 掉电例外用 0B64.
# OAD 行号源: DLT698App.c TAB_RecordObj[](事件表: 过载2967 掉电2981 编程2982 清零2983/2984/2985
#   校时2986 拉闸2995 合闸2996 磁干扰3006 继电器故障3007/3008 时钟故障3011 VER_20Edit).
# 注意: 类 0x33 对象是"事件上报/上报状态"(DLT698App.c:1091/2230), 不是存储记录读回, 勿用.
EVENT_CODE_NAME = {0x05: "过流", 0x06: "断流", 0x07: "功率反向", 0x08: "过载",
                   0x09: "正向有功需量超限", 0x0A: "反向有功需量超限", 0x0B: "象限无功需量超限",
                   0x0C: "功率因数超限", 0x0D: "全失压", 0x0F: "电压逆相序", 0x10: "电流逆相序",
                   0x11: "掉电", 0x12: "编程", 0x13: "电表清零", 0x14: "需量清零", 0x15: "事件清零",
                   0x16: "校时", 0x17: "时段表编程", 0x18: "时区表编程", 0x19: "周休日编程",
                   0x1A: "结算日编程", 0x1B: "开表盖", 0x1C: "开端钮盖", 0x1D: "电压不平衡",
                   0x1E: "电流不平衡", 0x1F: "拉闸", 0x20: "合闸", 0x21: "节假日编程",
                   0x22: "有功组合编程", 0x23: "无功组合编程", 0x24: "费率表编程", 0x25: "阶梯表编程",
                   0x26: "密钥更新", 0x27: "异常插卡", 0x28: "购电记录", 0x29: "退费记录",
                   0x2A: "磁干扰", 0x2B: "负荷开关误动作", 0x2C: "电源异常", 0x2D: "电流严重不平衡",
                   0x2E: "时钟故障(VER_20Edit)", 0x3C: "广播校时"}
# 编码即记录 OAD 的 byte1; 表源 = 固件 TAB_RecordObj[] (DLT698App.c:2952-3010, 2026-09-10 逐个抄录)
# ⚠ **0x2E 之后不是表尾**(2026-09-16 核): `DLT698App.c:3010` 起是 `#ifdef VER_20Edit` 分支, 而
#   **本版固件这个宏是开着的**(实证: 事件 0x2E「时钟故障(VER_20Edit)」实跑读得回) ⇒ 该分支里的
#   0x2F(计量芯片故障) / **0x3C(广播校时, :3013 `303C0B64`)** / 0x40 / 0x48 / 0x5A-0x5D / 0x65-0x67
#   **同样存在**。本表只补了 5-8 要用到的 0x3C; 其余**尚未登记**(是已知缺口, 不是"固件没有") ——
#   要用时按同一行源逐个补, 别照"表里没有就是没有"下结论。`0x5B 时钟电池欠压` 与
#   `DLT645App.c:607` 的 RPT_BattVolt36 判定相关, 补的时候一并核。
EVENT_REC_OAD = {  # 事件编码(byte1) → 完整事件记录对象 OAD(读回走 GetRequestRecord 85 03)
    0x05: "30050B0A", 0x06: "30060B0A", 0x07: "30070B0A", 0x08: "30080B0A",
    0x09: "30090B0A", 0x0A: "300A0B0A", 0x0B: "300B0B0A", 0x0C: "300C0B0A",
    0x0D: "300D0B0A", 0x0F: "300F0B0A", 0x10: "30100B0A", 0x11: "30110B64",
    0x12: "30120B0A", 0x13: "30130B0A", 0x14: "30140B0A", 0x15: "30150B0A",
    0x16: "30160B0A", 0x17: "30170B0A", 0x18: "30180B0A", 0x19: "30190B0A",
    0x1A: "301A0B0A", 0x1B: "301B0B0A", 0x1C: "301C0B0A", 0x1D: "301D0B0A",
    0x1E: "301E0B0A", 0x1F: "301F0B0A", 0x20: "30200B0A", 0x21: "30210B0A",
    0x22: "30220B0A", 0x23: "30230B0A", 0x24: "30240B0A", 0x25: "30250B0A",
    0x26: "30260B0A", 0x27: "30271D0A", 0x28: "30280B0A", 0x29: "30290B0A",
    0x2A: "302A0B0A", 0x2B: "302B0B0A", 0x2C: "302C0B0A", 0x2D: "302D0B0A",
    0x2E: "302E0B0A",
    0x3C: "303C0B64",   # 广播校时事件(源 DLT698App.c:3013); byte3=0x64 同 0x11 掉电 —— 非 0x0A 那批
}
# ⚠ 上表是【规范书写形】(byte2=0x0B 是**类别标记**「事件记录类」, byte3=属性/子号 —— 见 DLT698App.c:2938
#   表头注释); 真正发出去的请求 OAD 由 record_req_oad() 重写第 3 字节 = **请求属性字节**。
#   属性**不恒为 2**: 冻结(Class 9)与普通事件(Class 7) 要 2; Class 24 的 A/B/C 类事件(过载/功率反向/失压…)
#   要 `10-evenum`(过载 7 / 功率反向 6)。取错一律被回 `DAR=4`。判据与实测见 cmd_bank 的 `EVENT_REQ_ATTR`。
# 事件记录读回默认列(OAD 与冻结不同; 编程等"写参/定义类"事件同用此三列, DLT698App.c TAB_Program:2456-2464)
EVENT_REC_SEQ_OAD  = "20220200"   # 事件记录·记录序号列(生成值, 非存储)
EVENT_REC_TIME_OAD = "201E0200"   # 事件记录·事件发生时间列(存储偏移0, 6B DateTimeS)
EVENT_REC_DEF_OAD  = "33020206"   # 事件记录·事件附加定义列(编程=被写参数 OAD 列表, 存储偏移10, 40B)


# ============================ 开表口(选口与收发的另一半: 证明口后面是本表) ============================
def handshake_clock(ser):
    """证明"口后面是**本表**": 发一帧 698 读表钟(**按本表地址单播**) → 解出时间串? → (bool, 人话)。

    为什么是读钟, 不是读记录/读冻结:
      · **读钟不受 698 安全判定**(`Chk_SafeMode`)管 —— 读记录/冻结受(不在厂内态一律 DAR=20 打回,
        2026-09-14 实踩), 而握手时你**还不知道表在什么态**。依据: `scripts/_restore_all.py` 第 [5] 步
        是"**退厂内之后**读双芯钟 + smoke", 它期望成功 ⇒ 读钟在厂外成立。
      · **单播即身份**: 帧里带的是本表服务器地址(AF+表号+CA), 别的表不会应。
        ⚠ 若台上恰好有**两块地址相同的试验表**, 这一条证不了 —— 那时把卡带的 `COM` 钉死。
    """
    try:
        frame = frame_698(build_read_apdu(0x03, "40000200"))
    except RuntimeError as exc:                 # 没画像 ⇒ 组不出本表地址
        return False, "组帧不了(没有画像, 不知道本表地址): %s" % exc
    try:
        ser.reset_input_buffer()
        ser.write(frame)
        ser.flush()
    except Exception as exc:
        return False, "发不出: %s" % exc
    buf, t0 = bytearray(), time.time()
    while time.time() - t0 < portsel._HANDSHAKE_TIMEOUT:
        n = ser.read(256)
        if n:
            buf += n
            if decode_clock(bytes(buf)):
                break
        else:
            time.sleep(0.02)
    ts = decode_clock(bytes(buf))
    return bool(ts), ("应了, 表钟=%s" % ts if ts else "无应答(或应答里解不出表钟)")


# 装配点(全仓唯一): 本模块一 import, `portsel.open_com` 就有探活可用了。
# ⚠ 只有这一处装配; 别在别处再 set_probe 一遍(两份配方 = 分叉的开始, portsel.set_probe 会拦)。
portsel.set_probe(handshake_clock, "meterlib/p698.py")


