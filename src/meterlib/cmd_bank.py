# -*- coding: utf-8 -*-
"""
cmd_bank.py -- EZ315 管理芯白盒测试「帧资源库 + 用例层 + 发帧 CLI」(AI 侧使用)
位置: E:\\My Work\\MengXi\\帧收发基础
目的: 把所有要发的帧收敛成"数据"(帧是资源, 不是代码)。用户/AI 只需说发哪一帧/跑哪个用例,
     程序从目录选帧、构造、发送、按期望自动判过(PASS/TBD/FAIL), 不再每次临时拼帧改代码;
     回头重测 = 翻库重放, 字节级可复现。
帧来源:
  - SPECS(本文件 Python 目录): 唯一权威. 配方式/参数化帧在此用 p698/p645 构建器定义.
  - 每表 .frames.json(该表环境帧, project/<画像名>.frames.json): 纯静态"已验证帧"在此贴 hex 登记,
      不动 .py. 当前活动表由 project/CURRENT 指到谁, 就合并谁的帧文件. 通用规范帧留 SPECS.
记录读回纪律: 698 冻结/事件记录必须走 GetRequestRecord(服务0x03, OAD属性字节=02),
     普通读(0x01)对记录对象只回空 -> 见 归档/probe_frez_readback.py(已归档) / 经验总结.md.

用法(通常由 Claude 调用, 也可命令行):
    python -m meterlib.cmd_bank list [tag] [--json]        列帧(可按 tag 筛)
    python -m meterlib.cmd_bank search <kw> [--json]       按 id/名/别名/tag/kind/oad/omd/di 搜
    python -m meterlib.cmd_bank show  <id|别名|名字> [--json]
    python -m meterlib.cmd_bank dry   <id|别名|名字> [--json]   离线打印将发帧(不碰串口)
    python -m meterlib.cmd_bank send  <id|别名|名字> [--wait S] [--json]   COM3 发送+自动判过
    python -m meterlib.cmd_bank cases [--json]             列用例
    python -m meterlib.cmd_bank verbs [词] [--json]        列语义动词积木(活查询, 拼脚本用)
    python -m meterlib.cmd_bank plan  [用例id|tag]          打印用例/相关帧
    python -m meterlib.cmd_bank runcase <用例id> [--dry] [--wait S] [--json]  按序执行用例步骤
    python -m meterlib.cmd_bank smoke [--actions] [--raw] [--repeat N] [--wait S] [--json]
        在线冒烟验收: 默认只读+进厂内 电池, 传输层硬判过(0x85/应答尾/DAR),
        全 PASS=>HEALTHY(exit 0). --actions 加发会写冻结记录的瞬时冻结动作;
        --raw 直接走 ctypes RawCom(pyserial 卡死时用); --repeat N 连跑 N 轮.
    选芯(双芯寻址): 698 帧可指定发给管理芯/计量芯(只差 AF 字节, HCS/FCS 自动重算)
        python -m meterlib.cmd_bank dry 698.read.time --chip 计量芯         # 预览改发计量芯(AF=0x15)
        python -m meterlib.cmd_bank send 698.read.freeze.immed_block --chip 计量芯
        --chip 只对 698 帧有效(管理芯/计量芯 或 05/15); 645 无第二芯片, 指定即报错.
        计量芯往返 = 管理芯整帧透传、自己不应答, 计量芯回帧带 AF bit4 由管理芯桥回;
        该往返文档标"尚未实发", 首次打计量芯需实表确认收发形态.
    校时写表钟(动表钟, 需用户确认): SPECS 不再收校时写帧——老三条候选已实测全废,
        统一走 698 Set 40000200→计量芯 语义函数(set_meter_clock_set / settle_across_master);
        负知识归档(工程根 归档/, 2026-09-09 起): 归档/校时旧路_归档.md
中文别名示例: send 总电量 / send 瞬时冻结 / dry 读分钟冻结块 / show 进厂内

帧字段:
  id     稳定名(命令里引用它)
  name   中文用途
  alias  中文别名(可直接当命令参数)
  tags   对应测试子项/场景(plan/search 按此筛选)
  kind   645 | action(698动作) | read(698读/记录读) | raw
  hex    静态已验证整帧 bytes; 或 build=可调用 -> 帧 bytes(配方式, 优先)
  oad/omd/di  698读/698动作/645 的数据对象标记(检索与文档用)
  check  机器判过规则(可选, 未给按 kind 默认): {expect, param, silent_ok, needs_external}
  expect 期望应答文本(人工口径)
  evidence  串口无法单独证明时所需的外部证据(IAR 断点/回读)
  needs_factory 该动作需先 645.factory(进厂内/开盖) 才放行
  note   踩坑/依据
判读词表: PASS=串口层符合期望; TBD=串口不足以判定(需 evidence); FAIL=应答违背期望/无应答

──────────── 本文件结构索引(人/AI 导航用)────────────
分层定位(引擎/数据): 本文件 = 语义层(帧库 + 用例 + CLI + 语义动词, 属"引擎")。
  编解码底子按**协议分家**(一个协议一个文件): p698(build_* 组帧 与 decode_* 拆帧成对 + 698 对象模型/
  记录 OAD)、p645(645 组帧/校验/解码 + AA80 直读入口 + 645 标准 DI 与指令码); 串口开关与选口在
  common/portsel.py(传输层, 与协议无关)。本表机器条件放 project/CURRENT。
  加新帧/新活按下方分节落, 别往文件尾部堆长 if/函数。
分节(行号为当前快照, 会随编辑漂移, 以 def/常量名断点为准):
  [传输/选芯胶水]   _tp … send_frame          双芯 chip 换算/重定目标 + 直接发帧原语
  [帧目录 SPECS]    SPECS + overlay 加载      唯一权威帧数据(通用配方) + 合并 project/<表>.frames.json
  [检索/解析/判过]  by_id … machine_verdict    选帧/搜帧/机器判过(PASS/TBD/FAIL)
  [动作引擎]        send / smoke              发一帧(自动判过) / 在线冒烟验收
  [用例层 + CLI]    CASES … cmd_cases        用例数据 + plan/runcase + list/search/show/dry 命令
  [语义动词层]      _chip_name … aa80_vs_swd_compare  真正"干活的活"(每域自成一节, 见下)
      · 共享打印   _chip_name / _opout
      · 工厂/进厂内  enter_factory
      · 时钟        read_clock / set_meter_clock_set / settle_across_master
      · 结算日       read_billday / write_billday / next_billday_eve
      · 冻结/记录    read_freeze_row / rcsd / read_record_ud / check_freeze_snapshot
                 (judge_freeze_inc / all_passed 已于 2026-09-11 删除 —— 无调用点且是反面模板, 见其原位注释)
      · 通用读       read_oad_ud(任意 OAD 的 698 读——加"新读"不用写新代码)
      · 电能/整列     norm_energy47 / energy_slot_zero / record_energy_cols / energy_ele_of
加新活 = 在语义动词层对应域节加一个独立小函数(只 import p698/p645 的 build/decode + 标准对象号 +
  project 的机器条件)。
⚠ **白盒观察不在这里** —— AA80 直读那一簇(read_mem_aa80 / watch_vars / WatchBank / wait_change 等)
  住 `meterlib/watch.py`: 它是一条**独立的观察通路**, 与 SWD 直读、gdb 断点并列, 不与语义动词同住。
  本文件要用就 `W.xxx(...)`, 加新观察工具去 watch.py 加, 别往这里塞 —— 不为拆而拆
  (见 CLAUDE.md 目录地图)。
"""
import datetime, io, json, os, re, sys, time

# ---- 仓根(向上找含 src/ 的那一级), **只用来定位文件** ----
_p = os.path.dirname(os.path.abspath(__file__))
while not os.path.isdir(os.path.join(_p, "src")) and os.path.dirname(_p) != _p:
    _p = os.path.dirname(_p)
# ---- 传输层(零协议的一半): 串口对象 / 收发 / 选口 ----
# `open_com` 的"证明口后面是本表"那一段由 `meterlib.p698` 装配(它发的是 698 读钟帧), 见 portsel 模块头。
from common.portsel import open_com, tx_recv, send_frame
# ---- 日志那一行的字形: 协议词表 + 帧行/判定行的唯一渲法(2026-09-18) ----
# 为什么要有、以及"为什么协议由发帧函数注入而不是调用点传"见 common/loglabel 模块头。
# 上面 tx_recv 的 proto/peer/what 三格与下面 _opout 的判定行都走它 —— 别处**不许**自己拼这两种行。
from common import loglabel
from common.loglabel import opout    # 判定行的出口(字形仍只在 loglabel 一处定义)
# ---- 协议层: 一个协议一个文件。本层是它们的**调用方**, 不反过来 ----
from meterlib.p698 import (frame_698, build_read_apdu,
                      build_action_apdu, build_timetag698, build_getrecord_apdu, decode_action_ack,
                      decode_set_ack, build_set_apdu,
                      split_apdu, DAR, KNOWN_READ_FRAME, validate_698,
                      build_settime_apdu, decode_ts_698, decode_clock,
                      decode_freeze_row, decode_getrecord_dar, dar_from_ud, ud_record_count,
                      record_rows, OAD_EV_OCCUR, OAD_EV_END,
                      record_seq_at, record_time_at,
                      record_req_oad,
                      build_getrecord_apdu_oad, EVENT_RCSD,
                      crc_x25, ADDR_698,
                      REC_SEQ_OAD, REC_TIME_OAD, ENE_COMB_FULL_OAD, ENE_FWD_FULL_OAD,
                      FREEZE_SUBCLASS_NAME, EVENT_CODE_NAME, EVENT_REC_OAD)
from meterlib.p645 import (frame_645, validate_645, decode_645_reply,
                      read_time645_dt, read_time645_date, read_time645_time,
                      read_elect645_total, read_temp645_e0, read_aa80_645,
                      TABLE_ADDR, BROADCAST_ADDR,
                      BILLDAY_DI_HEX, BILLDAY_READ_DI,
                      SRAM_BASE, AA80_MAX_LEN, abs_to_aa80)
# ---- 白盒观察簇(AA80 直读管理芯 RAM): 一条独立通路, 一个文件 ----
# 它只依赖 common.* 与 meterlib.p645, **不依赖本文件** —— 所以本文件对它是使用者, 不是中转:
# 下面调用点写 `W.xxx(...)`, 脚本也直接 `from meterlib import watch as W` 调同一个面。
from meterlib import watch
# 画像经**中立层**取, 不 import project(2026-09-10 阶段二)。原先是 `from project import
# CURRENT as P` —— 共享引擎(换表不变)依赖了每表环境包(换表就换)。现在只有 common.profile
# 这一条路, 画像由组合根 project/__init__.py(卡带自己)装上, 名字收在 profile._DEFAULT_SOURCE。
# Proxy 让本文件几十处 `P.XXX`(含 412 行的模块级那两处)**一字不改**就能用。
from common import profile
P = profile.Proxy()               # 本工程画像(机器条件: 表号/双芯AF/RAM基址/变量地址/.out 等单一源)
# 判据汇总的地基现住 common/judge.py(零协议/零画像 ⇒ 属中立层, 与哪块表无关); 重导出供脚本
# `J = CB.Judge(...)` 调用点用。**判定只在那一个文件里实现** —— 本层只负责"造记录"(见 zone_slot_switch_*)。
from common import judge
Judge, rec = judge.Judge, judge.rec
# `.out` 的**程序映像**(PT_LOAD 段)与"逐字累加" —— 16-2『软件比对』要**我们自己**按固件口径
# 把应用区的累加和重算一遍(2026-09-17)。实现住中立层: 那边只实现"给定字节按 32 位小端字相加"
# 这个**纯函数**, **不知道**固件用的是这个口径 —— 口径是 16-2 段的知识, 附在那边注释里。
from common import elfsym

# ============================ 模块级常量(SPECS 组帧引用, 勿删) ============================
FACTORY_645 = bytes.fromhex("FE FE FE FE 68 AA AA AA AA AA AA 68 1F 03 42 88 32 EA 16")
# 698 广播瞬时冻结(4-1): OMD 0x50000300, 参数 long-unsigned(0x12) 延时0 -> 静默
FREEZE_APDU = build_action_apdu(0x03, "50000300", bytes.fromhex("12 00 00"))

# ---- 5-x 事件记录: 645 触发帧的数据域常量(源=固件 DLT645App.c, 见下 5-x 段说明) ----
CLEAR_METER_DATA = bytes.fromhex("02 00 00 00 00 00 00 00")              # [DI0]=02 判定 + 4B 操作者代码(DAT0)
CLEAR_EVENT_DIS_ALL = 0xFFFFFFFF                                        # 简单路径只认全清(DLT645App.c:3355)
CLEAR_EVENT_DATA = bytes.fromhex("02 00 00 00 00 00 00 00 FF FF FF FF")  # [DI0]判定 + 4B操作者 + [DAT4]=标识全清

# ============================ 参数化校时帧: 目标时间解析(帧仍是数据, 时间经 --param 注入) ============================
DEMO_TIME = "2099-12-31 23:59:59"   # 仅供 dry 预览组帧, 勿真发


def _tp(p, with_week=False):
    """把 'YYYY-MM-DD [HH:MM:SS]' 解析成 (y2,mo,d,h,mi,s[,week]). 校时帧的 build 参数."""
    if not (p and str(p).strip()):
        raise ValueError("校时帧需 --param \"YYYY-MM-DD [HH:MM:SS]\" 提供目标时间")
    t = str(p).strip()
    if " " not in t:
        t += " 00:00:00"
    try:
        dt = datetime.datetime.strptime(t, "%Y-%m-%d %H:%M:%S")
    except Exception as e:
        raise ValueError("时间格式错 %r(需 YYYY-MM-DD HH:MM:SS): %s" % (p, e))
    y2 = dt.year % 100
    base = (y2, dt.month, dt.day, dt.hour, dt.minute, dt.second)
    if with_week:
        base = base + (dt.isoweekday(),)
    return base


# ============================ 5-x 事件记录: 645 触发帧(电表清零/事件清零/跳合闸) ============================
# 数据域布局源 = 固件 DLT645App.c 的三个处理器: CMD_ClearMeter:3144 / CMD_ClearEvent:3288 / CMD_CtrlRelay:3428。
# 帧索引宏见该文件头 enum: STR1=0..ADR5=6, STR2=7, CMD=8, LEN=9, DI0=10..DI3=13, DAT0=14..(即 DI0=数据域首字节)。
# 整段数据域线上 +0x33 编码, 由 frame_645 统一加, 本层传【解码后】明文值。
# 时间域字节序(Platform/DateTime.c Check_DateTime→Point_Secs): [0]秒 [1]分 [2]时 [3]日 [4]月 [5]年 (645-07 标准序)。
# ⚠ 三帧均【未实测】(2026-09-10 由固件逐字节定死, 尚未上表验证): 首次发前先 dry; 清零/拉合闸为高风险动作。
RELAY_CMD_R = P.RELAY_CMD_R

RELAY_OP_CODE = {"拉": 0x1A, "拉闸": 0x1A, "合": 0x1C, "合闸": 0x1C}             # 常用两只(5-9/5-10)


def _bcd(n):
    """整数 → 1B BCD(如 25→0x25). 跳合闸执行时间域用。"""
    n = int(n)
    return ((n // 10) << 4) | (n % 10)


def _relay_frame(op, delay=0, t6=None):
    """645 0x1C 跳合闸数据域(16B 解码后) = 密码4B | 操作者代码4B | 操作字DAT4 | 延时DAT5(×5秒) | 执行时间DAT6..11(6B BCD)。
    op: '拉'/'合' 或 直接操作字 int(见 RELAY_CMD_R); t6 = (年2,月,日,时,分,秒) 或 None(占位全 0)。
    只组帧; 执行时间真值注入见 ctrl_relay()(须≥当前表钟, 固件 :3471)。"""
    code = op if isinstance(op, int) else RELAY_OP_CODE[op]
    y, mo, d, h, mi, s = t6 or (0, 0, 0, 0, 0, 0)
    data = (bytes(4) + bytes(4) + bytes([code & 0xFF, (int(delay) // 5) & 0xFF])
            + bytes([_bcd(s), _bcd(mi), _bcd(h), _bcd(d), _bcd(mo), _bcd(y)]))
    return frame_645(0x1C, data, addr=TABLE_ADDR)


def _bc645_calitime(y2, mo, d, h, mi, s):
    """645 广播校时 C=0x08 数据域 = 6B BCD[秒分时日月年](广播地址 99×6)。

    入口 `DLT645Link.c:363-368`: 地址须 ADR_699/ADR_ABS 且 LEN==0x06 → `CMD_CaliTime(&pFrame[DAT0],
    FALSE, 0)`(mode=0 广播)。645 侧要 **BCD**: `CMD_CaliTime` 的 `is698 != TRUE` 支先
    `Is_nBCD(setTime,6)` 再 `nBCD_nHEX`(固件 :598/:600)。
    ⚠ 本帧**未实测**(2026-09-16 由固件定死): 首次发前先 dry。
    """
    return frame_645(0x08, bytes([_bcd(s), _bcd(mi), _bcd(h), _bcd(d), _bcd(mo), _bcd(y2)]),
                     addr=BROADCAST_ADDR)


# ============================ 双芯选芯 / 通用发帧(底层, 见 project/knowledge/对表操作总纲.md《双芯寻址》) ============================
# 698 选芯 = 服务器地址首字节 AF 的 bit4(逻辑地址). 645 无第二芯片地址(645 只到管理芯).
#   AF=0x05 → 逻辑地址0 → 管理芯(管理芯本地应答; 此前所有帧)
#   AF=0x15 → 逻辑地址1 → 计量芯(管理芯整帧透传、自己不应答; 计量芯回帧带 AF bit4 由管理芯桥回)
# 用法: 组好/取到一帧后 send_frame(ser, frame) 或对目录帧 send <id> --chip 计量芯.
def chip_addr(chip=None):
    """按目标芯片组 698 服务器地址 8B: AF(管理芯/计量芯, 工程事实) + 表号(6) + CA(A1)."""
    return bytes([_norm_chip(chip)]) + ADDR_698[1:]


def _norm_chip(chip):
    """chip 归一成 AF 值. 记法: None/''=管理芯; 管理芯/计量芯/管理/计量; <hex AF>; manage/meter.
    AF 值(管理芯/计量芯)是工程事实, 读自 project/CURRENT.CHIP_AF."""
    _caf = {k: v for k, v in P.CHIP_AF.items()}            # 本工程: 管理芯→0x05, 计量芯→0x15
    _caf.setdefault(P.MANAGE_AF, "管理芯"); _caf.setdefault(P.METER_AF, "计量芯")  # int→名(展示用)
    afs = set(P.CHIP_AF.values())
    if chip is None:
        return P.MANAGE_AF
    if isinstance(chip, int):
        return chip if chip in afs else P.MANAGE_AF
    t = str(chip).strip(); low = t.lower()
    mp = {**{k.lower(): v for k, v in P.CHIP_AF.items()},
          "管理": P.MANAGE_AF, "计量": P.METER_AF,
          "manage": P.MANAGE_AF, "management": P.MANAGE_AF,
          "meter": P.METER_AF, "metric": P.METER_AF}
    if low in mp:
        return mp[low]
    try:
        v = int(t, 16)
    except ValueError:
        raise ValueError("chip 需为 管理芯/计量芯(或 %02X/%02X), 实为 %r" % (P.MANAGE_AF, P.METER_AF, chip))
    if v not in afs:
        raise ValueError("chip 值只能 %02X(管理芯)/%02X(计量芯), 实为 %r" % (P.MANAGE_AF, P.METER_AF, chip))
    return v


def chip_of(frame):
    """从一帧 698 里读出它当前选芯(管理芯/计量芯). 非 698 帧返回 None."""
    b = bytes(frame)
    while b[:1] == b"\xfe":
        b = b[1:]
    if len(b) < 15 or b[0] != 0x68:
        return None
    return "计量芯" if b[4] == P.METER_AF else "管理芯"


def retarget_frame(frame, chip=None):
    """把一帧 698 用户数据改成发到 chip(只改 AF 字节 05↔15, 重算 HCS/FCS). 645 帧/坏帧抛 ValueError.
    管理芯帧与计量芯帧除 AF 与 CRC 外逐字节一致(双芯寻址 §五)."""
    b = bytes(frame)
    if not b:
        raise ValueError("retarget_frame: 空帧")
    pre = b
    nfe = 0
    while pre[:1] == b"\xfe":
        pre = pre[1:]
        nfe += 1
    if len(pre) < 15 or pre[0] != 0x68:
        raise ValueError("retarget_frame 只支持 698 帧(68 开头, 可带 FE 前导), 实际开头=%s"
                         % (pre[:4].hex(" ").upper() or "(空)"))
    L = pre[1] | (pre[2] << 8)
    if len(pre) != L + 2:
        raise ValueError("retarget_frame: 698 长度 L=%d 与实际 %d 不符" % (L, len(pre)))
    head = pre[1:4] + chip_addr(chip)         # 长度(2)+控制(1)+新地址8B = 11B, HCS 范围同 frame_698
    apdu = pre[14:-3]
    hcs = crc_x25(head)
    hcs_b = bytes([hcs & 0xFF, (hcs >> 8) & 0xFF])
    fcs = crc_x25(head + hcs_b + apdu)
    fcs_b = bytes([fcs & 0xFF, (fcs >> 8) & 0xFF])
    return b"\xfe" * nfe + b"\x68" + head + hcs_b + apdu + fcs_b + b"\x16"


# 注: 原先这里有个 `send_frame` 薄壳(守卫 + `tx_recv`)。它零协议, 是**传输层**的动作不是语义动作
#   —— 已下沉到 `common/portsel.send_frame`, 与 `tx_recv` 同住(见该函数的 docstring: 两者只差
#   "没有现成串口算不算错")。本文件仍以 `send_frame` 名字用它(下面三十来处调用点一字未改)。
#   为什么非下沉不可: `meterlib/watch.py` 也要发帧, 留在这里它就只好反过来 import 本文件 —— 成环。
# ============================ 帧目录(唯一权威源) ============================
SPECS = [
    # ---- 通用/前置 ----
    dict(id="645.factory", name="进厂内(645 广播·工厂模式)",
         tags=["common", "factory", "4-1", "4-2", "4-3", "4-4", "5", "6", "10", "12", "16"],
         alias=["进厂内", "进入厂内", "工厂模式", "厂内"],
         kind="645", hex=FACTORY_645, di="0F55FF",
         check={"expect": "tail", "param": "9F00D516"},
         expect="应答尾 9F 00 D5 16 = OK(厂内常驻)。仅厂内/开盖才放行明文 698 写/动作(Chk_SafeMode)。",
         note="已验证可复现(经验总结 §4.2)。前置动作, 超时重发≤3次。"),
    dict(id="645.exit_factory", name="退厂内(645 广播·退出编程态)",
         tags=["common", "factory", "5-4"],
         alias=["退厂内", "退出厂内", "退出编程态", "退编程态"],
         kind="645", build=lambda: frame_645(0x1F, EXIT_FACTORY_645, addr=b"\xaa" * 6), di="0FAA00",
         check={"expect": "tail", "param": "9F00D516"},
         expect="应答尾 9F 00 D5 16 = 受理(已 Set_PrgTimer(0))。",
         note="2026-09-10 实测证伪旧文档『厂内态无串口退出路径』: 发前 g_PrgTimer[0]=255 → 发后 0。"
              "⚠ 发完 698 动作/0x14 写会被安全判定打回(ER_PSWD / DAR_MatchAuth) —— 收拾台面时才发。"),
    # ---- 5-x 事件记录: 645 触发帧(清零/事件清零/跳合闸; 布局源见文件头 5-x 段) ----
    dict(id="645.clear.meter", name="5-4 电表清零(645 0x1A)",
         tags=["5-4", "clear", "645"],
         alias=["电表清零", "清零电表", "清电表"],
         needs_factory=True, kind="645", di="",
         build=lambda: frame_645(0x1A, CLEAR_METER_DATA, addr=TABLE_ADDR),
         check={"expect": "tail", "param": "9A00D016"},
         expect="应答尾 9A 00 D0 16(9A=1A|0x80, LEN=0)。固件 :3148 CMD_ClearMeter→:3178 Clear_MeterData→:9249 Recd_ClrMeter 落『电表清零』永久记录。",
         note="数据域(解码后)=02 00 00 00|00 00 00 00: 首字节 0x02 过固件 DI0 判定(:3166), 后 4B=操作者代码(DAT0)。前置 enter_factory(Is_EnablePrg)。⚠ 高风险: 清电量/需量/冻结。未实测。"),
    dict(id="645.clear.event", name="5-5 事件清零(645 0x1B)",
         tags=["5-5", "clear", "event", "645"],
         alias=["事件清零", "清事件", "清事件记录"],
         needs_factory=True, kind="645", di="",
         build=lambda: frame_645(0x1B, CLEAR_EVENT_DATA, addr=TABLE_ADDR),
         check={"expect": "tail", "param": "9B00D116"},
         expect="应答尾 9B 00 D1 16(9B=1B|0x80, LEN=0)。固件 :3294 CMD_ClearEvent→:3399 Recd_ClrEvent(&DAT4标识,&DAT0操作者)落『事件清零』永久记录。",
         note="数据域(解码后)=02 00 00 00|00 00 00 00|FF FF FF FF: [DI0]=0x02 判定(:3310), [DAT0..3]=操作者, [DAT4..7]=事件标识(帧 index18-21)。⚠ 简单路径只认 0xFFFFFFFF 全清(:3355), 按标识部分清只能走远程/加密 LEN=0x1C 路。前置 enter_factory。未实测。"),
    dict(id="645.time.broadcast_sync", name="5-8 645 广播校时(C=0x08, 写表钟)",
         tags=["5-8", "clock", "645"],
         alias=["645广播校时", "广播校时"],
         kind="645", di="", param_time=True,
         build=lambda p: _bc645_calitime(*_tp(p)),
         check={"silent_ok": True},
         expect="广播不回复 ⇒ 判过靠**回读**: 698.read.time 变成目标值。固件 DLT645Link.c:363-368 → "
                "CMD_CaliTime(mode=0); 成功后 :734 Recd_TimeError(FALSE,0)(给『时钟故障』记录补结束时间)"
                " + :735 Chg_FrezStamp(TRUE)。",
         note="写表钟。判定(|Δ|/同自然日/不跨结算日/不跨年计)见 CMD_CaliTime; **当日不重复校时**"
              "(:705 读 ID_LastChTime, 成功即写 :721)⇒ **当天只能成功一次**。数据域 = BCD[秒分时日月年]+0x33。"
              "⚠ 归档文《校时旧路_归档.md》说这条『全废』的**语境是拨钟跨月**(过不了 60~300s 窗), "
              "不是清事件 —— 5-8 的 Δ 只有 120s, 正在窗内。 --param 目标时间。"),
    dict(id="645.ctrl.relay", name="5-9/5-10 跳合闸控制(645 0x1C)",
         tags=["5-9", "5-10", "relay", "645"],
         alias=["拉闸", "合闸", "跳合闸", "继电器控制"],
         needs_factory=True, kind="645", di="", param_time=True,
         build=lambda p: _relay_frame("拉", 0, _tp(p)),
         check={"expect": "tail", "param": "9C00D216"},
         expect="应答尾 9C 00 D2 16(9C=1C|0x80, LEN=0)。固件 :3428 CMD_CtrlRelay→:3504 Set_RelayCmdR→TaskRelay.c:278 Upd_RelayCmd→:335 Recd_CtrlRelay 落拉/合记录。",
         note="数据域(16B 解码后)=密码4B|操作者代码4B|操作字DAT4|延时DAT5(×5s)|执行时间DAT6..11(6B BCD 秒分时日 月年)。操作字: 0x1A 拉闸/0x1C 直接合闸(TAB_RelayCmdR)。本目录帧固定 拉闸+延时0, 仅作 dry/目录展示; 真发请用 CB.ctrl_relay(ser, op, delay, at)(执行时间须≥当前表钟 :3471)。前置 enter_factory 或密码。未实测。"),
    # ---- 4-1 瞬时冻结 ----
    dict(id="698.action.freeze.immed", name="4-1 广播瞬时冻结(698 Action)",
         tags=["4-1", "freeze"],
         alias=["瞬时冻结", "广播瞬时冻结"],
         kind="action", omd="50000300", needs_factory=True,
         build=lambda: frame_698(FREEZE_APDU),
         check={"expect": "dar0", "silent_ok": True},
         expect="广播动作通常无应答(静默)。判过= IAR 断点 Save_FrezData(r0=ID_ImmedFrez) / 回读瞬时冻结块见新时刻。",
         evidence="IAR Save_FrezData/Write_FrezData(r0=13) 断点; 或回读瞬时冻结块 50000303 见新时标",
         note="OMD 0x50000300; APDU 07 01 03 50 00 03 00 12 00 00 00(11B)。"),
    dict(id="698.read.freeze.immed_block", name="读瞬时冻结记录(4-1 落库确认, GetRequestRecord)",
         tags=["4-1", "freeze", "read"],
         alias=["读瞬时冻结", "读瞬时冻结块"],
         kind="read", oad="50000200",
         build=lambda: frame_698(build_getrecord_apdu(0x03, 0x00)),
         check={"expect": "get_rec", "needs_external": True},
         expect="GetResponseRecord(85 03) 取最新1条瞬时冻结(记录序号+冻结时间); 有行=已落库, 行时间为触发后=确认。",
         evidence="应答 85 03 带记录行且冻结时间晚于触发时刻=已落库; 科学判据另加 IAR Save_FrezData 命中",
         note="记录对象须走 GetRequestRecord(服务字节03, OAD 属性字节=02, CLAUDE 硬性规矩4)。普通 GET(0x01)对记录只回空 85 01..06 00 00。OAD=[50][子类00][02][00]。2026-09-08 实测: 广播瞬时冻结后此读回 85 03 带 1 行记录。"),
    dict(id="698.read.freeze.min_block", name="读分钟冻结记录(4-2 落库比对, GetRequestRecord)",
         tags=["4-2", "freeze", "read"],
         alias=["读分钟冻结", "读分钟冻结块"],
         kind="read", oad="50020200",
         build=lambda: frame_698(build_getrecord_apdu(0x03, 0x02)),
         check={"expect": "get_rec", "needs_external": True},
         expect="GetResponseRecord(85 03) 取最新1条分钟冻结(记录序号+冻结时间), 与固件 Write_FrezData(:374) 写库比对; 无行=未落库/通道未投运。",
         evidence="记录冻结时间落在真实整分且与固件 Write_FrezData 写库一致",
         note="记录对象须走 GetRequestRecord(服务字节03, OAD 属性字节=02)。OAD=[50][子类02][02][00]。分钟冻结为固件真实整分自然触发(MSG_MinStep), 2026-09-08 实测回 85 03 带记录行。"),
    # ---- 通用读(链路复现/电能量) ----
    dict(id="698.read.elect.total_200A0000", name="读电能量·总(698)",
         tags=["1-1", "read", "common"],
         alias=["总电量", "读总电量"],
         kind="read", oad="200A0000", hex=KNOWN_READ_FRAME,
         check={"expect": "get_resp"},
         expect="有应答即链路通; 数值按对象表解析(带时标/小数位见 1-3)。",
         note="已验证与 OOPT 逐字节一致(经验总结 §4.1), 最小链路复现帧。"),
    # ---- 读时钟/时间(只读; 校时前置比对 / 1-2 / 2-1) ----
    dict(id="698.read.time", name="读表时钟(698 日期时间对象)",
         tags=["read", "clock", "1-2", "2-1", "5", "common"],
         alias=["读表时钟", "读时钟", "当前时间", "读时间698"],
         kind="read", oad="40000200",
         build=lambda: frame_698(build_read_apdu(0x03, "40000200")),
         check={"expect": "get_resp"},
         expect="GetResponse 85 01 … 01 1C <年2B绝对> <月日时分秒>. 年=2000+2B大端(2026→07 EA). 时间编码见 审计纪要 §4.",
         note="只读. OAD 40000200=日期时间·属性2(时间值). 也可与 645 读时间交叉比对."),
    dict(id="645.read.time_dt", name="读 日期时间(BCD 7B)",
         tags=["read", "645", "clock", "1-2", "2-1", "5", "common"],
         alias=["读时间645", "645读日期时间"],
         kind="645", di="0400010C",
         build=lambda: read_time645_dt(),
         check={"expect": "rx"},
         expect="应答 0x91 回显 DI + 7B BCD [秒分时周日月年](-0x33 后). 周由固件 Generate_Week 生成.",
         note="只读. 本表地址 111111111111. 645 线时间=BCD 2 位年. 见 审计纪要 §5."),
    # ---- 冻结记录读回(结算/日) ----
    dict(id="698.read.freeze.billmonth", name="读月结算冻结记录(4-6, GetRequestRecord)",
         tags=["4-6", "freeze", "read"],
         alias=["读月结算冻结", "结算冻结记录"],
         kind="read", oad="50050200",
         build=lambda: frame_698(build_getrecord_apdu(0x03, 0x05)),
         check={"expect": "get_rec", "needs_external": True},
         expect="GetResponseRecord(85 03) 取最新1条月结算冻结(序号+冻结时间); 有行=已生成结算冻结.",
         evidence="应答 85 03 记录行的冻结时间/冻结电能量与结算时刻及计量芯值核对",
         note="子类05=月结算冻结 ID_BillFrezM(=12, FrezData.h:35). OAD=[50][05][02][00]. 默认列=序号+时间; 带电量列需扩 RCSD(审计纪要 §3)."),
    dict(id="698.read.freeze.billmonth.last8", name="读月结算冻结记录·最近8条(4-6 观测B判据)",
         tags=["4-6", "freeze", "read", "billday"],
         alias=["结算冻结最近8条", "读结算冻结8条"],
         kind="read", oad="50050200",
         build=lambda: frame_698(build_getrecord_apdu(0x03, 0x05, rsd=b"\x09\x08")),
         check={"expect": "get_rec", "needs_external": True},
         expect="GetResponseRecord(85 03) 取最近8条月结算冻结(序号+冻结时间). 观测B判据: 每次改结算日恰新增1条, 最新一条时标≈改动当刻, 序号末字节逐次+1.",
         note="实测 09 08 只回1条且内容=倒数第8条→ 方法09第2字节=从最新往回数的位置(非条数). 单行版= 698.read.freeze.billmonth."),
    dict(id="698.read.freeze.billmonth.pos2", name="读月结算冻结·倒数第2条(4-6 恰1条佐证)",
         tags=["4-6", "freeze", "read", "billday"],
         alias=["结算冻结倒数第2条"],
         kind="read", oad="50050200",
         build=lambda: frame_698(build_getrecord_apdu(0x03, 0x05, rsd=b"\x09\x02")),
         check={"expect": "get_rec", "needs_external": True},
         expect="GetResponseRecord(85 03) 取倒数第2条(改结算日前的旧顶). 观测B佐证: 改日1次→新顶进09 01, 旧顶落09 02.",
         note="方法09第2字节=位置(实测坐实). 序号末字节与 09 01 配合判每次恰1条."),
    # ---- 645 厂商读/直读(只读) ----
    dict(id="645.read.elect_total", name="读 组合有功总电能",
         tags=["read", "645", "1-1", "电能", "common"],
         alias=["读电能645", "645总电量"],
         kind="645", di="00000000",
         build=lambda: read_elect645_total(),
         check={"expect": "rx"},
         expect="应答 0x91 回显 DI + 电能数值字节(管理芯镜像值==计量芯 Watch). 数值宽/定标需实测对拍.",
         note="只读. DI 00000000=当前·总费率·组合有功. 审计纪要 §6.4 Frame B."),
    dict(id="645.aa80.read_eeprom0", name="直读 EEPROM 0x0000 长4(链路冒烟)",
         tags=["read", "645", "厂商读", "AA80"],
         alias=["读EEPROM头", "AA80读EEPROM"],
         kind="645", di="00AA8004",
         build=lambda: read_aa80_645(3, 0x0000, 4),
         check={"expect": "rx"},
         expect="应答 0x91: 数据域=4B地址回显(00000000)+4B 原样 EEPROM 内容.",
         note="只读. 区号3=外部EEPROM. 真实外部直读内存入口是 AA80 形(非 0xE0), 见 审计纪要 §6.2."),
    dict(id="645.temp_0xE0800007", name="0xE0 厂商读温度(链路存在性冒烟)",
         tags=["read", "645", "厂商读"],
         alias=["读温度", "E0温度"],
         kind="645", di="E0800007",
         build=lambda: read_temp645_e0(),
         check={"expect": "rx"},
         expect="应答 0x91 回显 DI + 2B 温度 s16 LE. 0xE0 本地读活着即证厂商读链路通.",
         note="只读. DI3=0xE0 ST_FactoryReadCMD 本地实现(温度). 审计纪要 §6.1."),
    # 校时写表钟不再收 SPECS 帧: 三条老候选(645 广播/698 Action 方法127/645 0x14)已实测全废,
    # 统一走 698 Set 40000200→计量芯(set_meter_clock_set / settle_across_master). 负知识归档在工程根: 归档/校时旧路_归档.md
]

# ============================ 表帧叠加层(每表环境帧, 不碰 .py) ============================
# 每表"已验证帧"注册在工程画像同名的 .frames.json(project/CURRENT 指到谁就合并谁的)。
# 通用规范帧留 SPECS; 这是该表实测验证帧的单一住处。CURRENT 没声明 FRAMES_FILE 时回退到引擎旁
# 同名文件(兼容旧布局/临时无画像帧), 找不到 = 无叠加层(静默)。
def _repo_root():
    """仓根 = 含 src/ 的那一级(= 帧收发基础). 2026-09-10 迁 src/ 后, "本模块上两级"会落到 src/。"""
    p = os.path.dirname(os.path.abspath(__file__))
    while not os.path.isdir(os.path.join(p, "src")) and os.path.dirname(p) != p:
        p = os.path.dirname(p)
    return p


def _overlay_path():
    """本表叠加层 .frames.json 的绝对路径: 画像所在目录 / 画像声明的 FRAMES_FILE。

    ⚠ **这是本模块唯一在模块级读画像的地方**(下面 _OVERLAY_PATH 那行), 因为叠加层是 import
    期就并进 SPECS 的。于是它读的是 **import 那一刻** 的画像 —— 之后再 configure() 换画像,
    这个路径不会自己跟着动, 要跟着动就调 reload_overlay()。
    其余几十处 `P.XXX` 都在函数体里走 Proxy, 是**每次现取**, 不受这条限制。
    """
    p = profile.current()
    f = getattr(p, "__file__", None)
    pdir = os.path.dirname(os.path.abspath(f)) if f else _repo_root()
    return os.path.join(pdir, getattr(p, "FRAMES_FILE", "user_frames.json"))


_OVERLAY_PATH = _overlay_path()
_OVERLAY_WARN = []
_OVERLAY_ERR = None
_OVERLAY_STATS = {"loaded": 0, "problems": 0}


def _clean_hex(s):
    return "".join(str(s or "").split())


def _overlay_entry(fr):
    """校验并规范化一条叠加帧. 返回 (entry|None, err|None)."""
    if not isinstance(fr, dict):
        return None, "非对象"
    fid = (fr.get("id") or "").strip()
    name = (fr.get("name") or "").strip()
    kind = (fr.get("kind") or "").strip()
    if not fid:
        return None, "缺 id"
    if not name:
        return None, "缺 name"
    if kind not in ("645", "action", "read", "raw"):
        return None, "kind 需为 645|action|read|raw, 实为 %r" % kind
    try:
        raw = bytes.fromhex(_clean_hex(fr.get("hex")))
    except Exception:
        return None, "hex 非法: %r" % (str(fr.get("hex"))[:40])
    if not raw:
        return None, "hex 为空"
    return {
        "id": fid, "name": name, "kind": kind, "hex": raw,
        "tags": list(fr.get("tags") or []),
        "alias": [str(a) for a in (fr.get("alias") or [])],
        "oad": (fr.get("oad") or "").strip(), "omd": (fr.get("omd") or "").strip(),
        "di": (fr.get("di") or "").strip(),
        "expect": fr.get("expect") or "", "note": fr.get("note") or "",
        "evidence": fr.get("evidence") or "",
        "check": fr.get("check") if isinstance(fr.get("check"), dict) else {},
        "needs_factory": bool(fr.get("needs_factory", False)),
        "_from_overlay": True,
    }, None


def _load_overlay():
    """把 user_frames.json 合并进 SPECS(静默; 坏文件只记错, import 永不抛)."""
    global _OVERLAY_ERR
    if not os.path.isfile(_OVERLAY_PATH):
        return
    try:
        with open(_OVERLAY_PATH, encoding="utf-8-sig") as f:
            data = json.load(f)
    except Exception as e:
        _OVERLAY_ERR = "user_frames.json 读取/解析失败: %s" % e
        return
    frames = (data or {}).get("frames") if isinstance(data, dict) else None
    if not isinstance(frames, list):
        _OVERLAY_ERR = "user_frames.json 顶层需为对象且含 frames 数组"
        return
    seen = set()
    for i, fr in enumerate(frames):
        entry, err = _overlay_entry(fr)
        if entry is None:
            _OVERLAY_STATS["problems"] += 1
            _OVERLAY_WARN.append("第%d条 %s: %s" % (i + 1, (fr or {}).get("id", "?"), err))
            continue
        fid = entry["id"]
        if fid in seen:
            _OVERLAY_WARN.append("overlay 内部重复 id=%s(取首条)" % fid)
            continue
        seen.add(fid)
        for j, old in enumerate(SPECS):
            if old["id"] == fid:
                _OVERLAY_WARN.append("id=%s 覆盖内置同 id 帧" % fid)
                del SPECS[j]
                break
        SPECS.append(entry)
        _OVERLAY_STATS["loaded"] += 1


_load_overlay()


def reload_overlay():
    """按**当前**画像重算叠加层路径, 卸掉上一份、再装一份。

    给两种场合: ①`profile.configure(别的表)` 之后想让帧目录也跟着换; ②测试里注入假画像,
    验"画像真的驱动着 cmd_bank"。幂等: 卸的时候认 `_from_overlay` 标记, 不碰内置 SPECS
    (被 overlay 覆盖掉的内置同 id 帧**不复活** —— 那需要整套 SPECS 的深拷贝, 代价不值;
    换画像这种动作本来就该是一次性的, 不是在同一个进程里来回切)。"""
    global _OVERLAY_PATH, _OVERLAY_WARN, _OVERLAY_ERR, _OVERLAY_STATS, _BY_ID
    SPECS[:] = [s for s in SPECS if not s.get("_from_overlay")]
    # 计数与告警**整份换新**(不是逐个清零): 原先写成 `del _OVERLAY_STATS["problems"]`,
    # 第二次调就 KeyError —— 键删掉就没了。换新字典既干净又幂等(踩过, 自检里抓到)。
    _OVERLAY_WARN = []
    _OVERLAY_STATS = {"loaded": 0, "problems": 0}
    _OVERLAY_ERR = None
    _OVERLAY_PATH = _overlay_path()
    _load_overlay()
    _BY_ID = {s["id"]: s for s in SPECS}
    return _OVERLAY_PATH


# ============================ 查询/检索 ============================
_BY_ID = {s["id"]: s for s in SPECS}


def by_id(i):
    return _BY_ID.get(i)


def specs_for(tag):
    return [s for s in SPECS if tag in s.get("tags", [])]


def _frame(s, param=None):
    if "hex" in s:
        return s["hex"]
    if s.get("param_time"):
        if param is None:
            raise ValueError("帧 %s 为参数化帧, 需 --param \"YYYY-MM-DD [HH:MM:SS]\" 提供目标时间" % s["id"])
        return s["build"](param)
    return s["build"]()


def _hex(s, param=None):
    """目录项 → 线上字节。**协议在这一刻钉上** —— 对自组帧(`frame_645`/`frame_698`)来说,
    组帧那一刻就是它们自己; 对目录帧来说, "组帧那一刻"**就是这里**, 所以协议也在这里归宿。

    `build` 出来的帧若已自带协议(它内部调了 `frame_645`/`frame_698`), 原样留着, 不覆盖;
    只有裸 `hex=` 常量那种(没有组帧函数, 协议一个字都没写)才按 id 前缀补。
    ⚠ id 前缀(`"645.factory"` / `"698.action.…"`)是**目录项自己的声明**, 不是从帧字节反推的 ——
      前缀没登记过就当场炸(`_spec_proto`), 静默标错协议比不标更坏。
    """
    b = _frame(s, param)
    if not isinstance(b, (bytes, bytearray)):
        b = bytes(b)
    if not loglabel.proto_of(b):
        b = loglabel.Frame(bytes(b), _spec_proto(s["id"]))
    return b


def _searchable(s):
    return " ".join([s["id"], s["name"], s.get("kind", ""),
                     ",".join(s.get("tags", [])), " ".join(s.get("alias", [])),
                     s.get("oad", ""), s.get("omd", ""), s.get("di", "")]).lower()


def resolve(token):
    """按 id/名/别名 精确优先, 否则子串(含 tags/kind/oad/omd/di). 返回候选列表."""
    t = (token or "").strip().lower()
    if not t:
        return []
    for s in SPECS:
        if s["id"].lower() == t:
            return [s]
    byname = [s for s in SPECS if s["name"].strip().lower() == t]
    if byname:
        return byname
    byalias = [s for s in SPECS if any(a.strip().lower() == t for a in s.get("alias", []))]
    if byalias:
        return byalias
    return [s for s in SPECS if t in _searchable(s)]


def _one(token, verb):
    m = resolve(token)
    if len(m) == 1:
        return m[0]
    if not m:
        raise KeyError("找不到帧/用例 %r, 试试: search %s" % (token, token))
    print("'%s %s' 有歧义, 匹配 %d 条:" % (verb, token, len(m)))
    for x in m:
        print("  %s | %s" % (x["id"], x["name"]))
    raise SystemExit(2)


def _snapshot(s, param=None):
    try:
        hx = _hex(s, param)
        hs, ln = hx.hex(" ").upper(), len(hx)
    except ValueError:
        hs, ln = "", 0
    return {"id": s["id"], "name": s["name"], "kind": s.get("kind", ""),
            "alias": list(s.get("alias", [])), "tags": list(s.get("tags", [])),
            "oad": s.get("oad", ""), "omd": s.get("omd", ""), "di": s.get("di", ""),
            "hex": hs, "len": ln,
            "param_time": bool(s.get("param_time", False)),
            "expect": s.get("expect", ""), "note": s.get("note", ""),
            "evidence": s.get("evidence", ""),
            "needs_factory": bool(s.get("needs_factory", False)),
            "check": s.get("check") or {},
            "from_overlay": bool(s.get("_from_overlay", False))}


# ============================ 机器判过(PASS / TBD / FAIL) ============================
def _default_rule(kind):
    if kind == "action":
        return {"expect": "dar0", "silent_ok": True}
    if kind == "read":
        return {"expect": "get_resp", "silent_ok": False}
    return {"expect": "rx", "silent_ok": False}


def _merged_check(s):
    d = _default_rule(s.get("kind"))
    d.update(s.get("check") or {})
    return d


def machine_verdict(s, rx):
    """按规则给一帧应答判 PASS/TBD/FAIL + 理由. 词表含义见模块 docstring."""
    rx = rx or b""
    c = _merged_check(s)
    if c.get("expect") == "silent":
        return ("PASS", "预期静默(无应答)") if not rx else ("FAIL", "预期静默却收到应答")
    if not rx:
        if c.get("silent_ok"):
            return ("TBD", "无应答——广播动作常静默; 需证据: " + (s.get("evidence") or "IAR 断点/回读"))
        return ("FAIL", "无应答")
    exp = c.get("expect", "rx")
    if exp == "tail":
        tail = bytes.fromhex(_clean_hex(c.get("param", "")))
        if tail and rx.endswith(tail):
            return ("PASS", "应答尾符合 " + tail.hex(" ").upper())
        return ("FAIL", "应答尾不符期望")
    if exp == "has":
        sub = bytes.fromhex(_clean_hex(c.get("param", "")))
        if sub and sub in rx:
            return ("PASS", "RX 含 " + sub.hex(" ").upper())
        return ("FAIL", "RX 不含期望字节")
    if exp == "dar0":
        d = decode_action_ack(rx)
        if d and d.get("service") == "ActionResponse" and d.get("dar") == 0:
            return ("PASS", "ActionResponse DAR=0 成功")
        if d and d.get("service") == "ErrorResponse":
            return ("FAIL", "ErrorResponse DAR=%d(%s)" % (d["dar"], DAR.get(d["dar"], "?")))
        return ("FAIL", "非预期动作应答")
    if exp == "get_resp":
        ud = split_apdu(rx)
        if ud and ud[0] == 0x85:
            msg = "GetResponse(0x85) 应答通"
            if c.get("needs_external"):
                return ("TBD", msg + "; 内容/落库需证据: " + (s.get("evidence") or "人工判读"))
            return ("PASS", msg)
        if ud and ud[0] == 0xEE:
            return ("FAIL", "ErrorResponse DAR=%d(%s)" % (252 + ud[2], DAR.get(252 + ud[2], "?")))
        return ("FAIL", "应答形态非 0x85/0xEE")
    if exp == "get_rec":
        # 冻结/事件"记录"读回: 必须 GetResponseRecord 85 03 (服务字节=03). 普通 GET(85 01)只回空 -> 硬 FAIL
        ud = split_apdu(rx)
        if ud and ud[0] == 0x85 and len(ud) > 1 and ud[1] == 0x03:
            msg = "GetResponseRecord(85 03) 记录对象可达"
            if c.get("needs_external"):
                return ("TBD", msg + "; 记录内容/时标核对需证据: " + (s.get("evidence") or "人工判读"))
            return ("PASS", msg)
        if ud and ud[0] == 0x85:
            svc = ud[1] if len(ud) > 1 else 0
            return ("FAIL", "记录对象应答服务字节=0x%02X, 需走 GetRequestRecord(应答 85 03); 普通 GET 对记录只回空" % svc)
        if ud and ud[0] == 0xEE:
            return ("FAIL", "ErrorResponse DAR=%d(%s)" % (252 + ud[2], DAR.get(252 + ud[2], "?")))
        if not rx:
            return ("FAIL", "无应答")
        return ("FAIL", "应答形态非 85 03/0xEE")
    return ("PASS", "收到应答")


def verdict_exit(verdict):
    """判过词 → 进程退出码: 全局单一契约, send / runcase / smoke / 测试脚本共用.

    PASS*  → 0   串口层符合期望
    FAIL*  → 1   应答违背期望或应答而缺席
    其余    → 2   TBD(串口不足判定) 与用法错同码, 均表示"未定论"

    ⚠ 判过词表见模块 docstring。曾出现的旧 bug: `_cli` 的 send 分支恒 `return 0`,
      把 FAIL 吞成成功 —— 任何靠退出码做检查的自动化都会被它骗过。本函数即该契约的单点。

    **也收一整个序列**(2026-09-10 加): 一份操作清单里每一次各有一个 verdict, 进程只能退一个码 ——
    取**最坏的那个**(有 FAIL→1 > 有 TBD/其它→2 > 全 PASS→0)。顺序不是"谁最后跑谁说了算":
    一次 FAIL 就整轮不判过, 后面几次全 PASS 也盖不掉它。空序列 = 没判过任何东西 → 2(未定论),
    **不是 0** —— "没证据"与"通过"是两回事。
    """
    if isinstance(verdict, (list, tuple, set)):
        codes = [verdict_exit(v) for v in verdict]
        if not codes:
            return 2
        return 1 if 1 in codes else (2 if 2 in codes else 0)
    v = (verdict or "").strip().upper()
    if v.startswith("PASS"):
        return 0
    if v.startswith("FAIL"):
        return 1
    return 2


# ============================ 发送 ============================
def _print_kind_decode(s, rx):
    if not rx or s.get("kind") != "action":
        return
    d = decode_action_ack(rx)
    if d:
        print("应答: 服务=%s DAR=%s(%s)" % (d.get("service"), d.get("dar"),
                                          DAR.get(d.get("dar"), hex(d.get("dar", -1)))))


def send(spec_id, wait=3.0, _json=False, ser_shared=None, param=None, chip=None):
    """COM3 发一帧并打印 TX/RX + 自动判过. 返回结构化 dict(供 --json / runcase).
    param: 校时类参数化帧的目标时间 'YYYY-MM-DD [HH:MM:SS]'.
    chip : 管理芯/计量芯(仅 698 帧). 指定计量芯 → 帧改 AF=0x15(管理芯整帧透传), 645 帧指定即报错."""
    s = _one(spec_id, "send")
    frame = _hex(s, param)
    chipnote = ""
    if chip is not None:
        if s["kind"] not in ("action", "read"):
            raise ValueError("--chip 只对 698 帧有效, 帧 %s 是 %s(645), 645 无第二芯片地址" % (s["id"], s["kind"]))
        frame = retarget_frame(frame, chip)
        chipnote = "  [发给 %s, AF=0x%02X]" % (chip_of(frame), _norm_chip(chip))
    ser, own = ser_shared, False
    if ser is None:
        ser = open_com()
        own = True
    what = s["name"]           # 只给功能名 —— 协议格/方向格由 loglabel 出, 帧 id 不进功能名
    try:
        rx = tx_recv(ser, frame, wait=wait, tag=s["id"],
                     peer=_chip_name(chip) if chip is not None else "", what=what)
    finally:
        if own:
            ser.close()
    verdict, reason = machine_verdict(s, rx)
    res = {"id": s["id"], "name": s["name"], "kind": s["kind"],
           "tx": frame.hex(" ").upper(), "tx_len": len(frame),
           "rx": rx.hex(" ").upper() if rx else "", "rx_len": len(rx),
           "verdict": verdict, "reason": reason,
           "expect": s.get("expect", ""), "evidence": s.get("evidence", ""),
           "apdu": split_apdu(rx).hex(" ").upper() if (rx and split_apdu(rx)) else ""}
    if chipnote:
        res["chip"] = chip_of(frame)
    if not _json:
        print("\n== send %s | %s%s" % (s["id"], s["name"], chipnote))
        _print_kind_decode(s, rx)
        print("期望:", s.get("expect", ""))
        print("VERDICT: %s   理由: %s" % (verdict, reason))
    return res


# ============================ 在线冒烟(工程健康验收) ============================
# 默认电池 = 只读 + 进厂内(覆盖 645 与 698 两条协议路径, 不写任何 EEPROM/冻结记录).
# --actions 才追加会写冻结记录的瞬时冻结动作帧(4-1 触发, 会留库, 默认不开).
SMOKE_DEFAULT = ["645.factory",
                 "698.read.elect.total_200A0000",
                 "698.read.freeze.immed_block",
                 "698.read.freeze.min_block"]
SMOKE_ACTIONS_EXTRA = ["698.action.freeze.immed"]


def smoke_verdict(s, rx):
    """冒烟专用判读: 只证"链路+组帧+应答结构"健康, 不看 needs_external/落库证据.
    与 machine_verdict 的区别: 冻结读块等 needs_external 帧在此按传输层硬判,
    结构应答(0x85)即 PASS; 0xEE/无应答/形态错 = FAIL."""
    rx = rx or b""
    kind = s.get("kind")
    if kind == "action":
        if not rx:
            return ("PASS", "广播动作静默属常态(是否真冻结看 runcase/回读/断点)")
        d = decode_action_ack(rx)
        if d and d.get("service") == "ActionResponse" and d.get("dar") == 0:
            return ("PASS", "ActionResponse DAR=0")
        if d and d.get("service") == "ErrorResponse":
            return ("FAIL", "ErrorResponse DAR=%d(%s)" % (d["dar"], DAR.get(d["dar"], "?")))
        return ("FAIL", "动作应答形态非 ActionResponse/ErrorResponse")
    if kind == "read":
        ud = split_apdu(rx)
        rec = (_merged_check(s).get("expect") == "get_rec")   # 记录读须 GetResponseRecord 85 03
        if ud and ud[0] == 0x85:
            if rec:
                if len(ud) > 1 and ud[1] == 0x03:
                    return ("PASS", "GetResponseRecord(85 03) 记录对象可读(存在性即说明可回读)")
                svc = ud[1] if len(ud) > 1 else 0
                return ("FAIL", "记录对象服务字节=0x%02X, 须走 GetRequestRecord(85 03); 普通 GET 只回空" % svc)
            return ("PASS", "GetResponse(0x85) 链路+对象可达(内容/落库不在冒烟范围)")
        if ud and ud[0] == 0xEE:
            return ("FAIL", "ErrorResponse DAR=%d(%s); 链路已通, 疑对象/状态, 查 needs_factory"
                    % (252 + ud[2], DAR.get(252 + ud[2], "?")))
        if not rx:
            return ("FAIL", "无应答")
        return ("FAIL", "应答形态非 0x85/0xEE: " + rx[:16].hex(" ").upper())
    if kind == "645":
        c = _merged_check(s)
        if c.get("expect") == "tail":
            tail = bytes.fromhex(_clean_hex(c.get("param", "")))
            if tail and rx.endswith(tail):
                return ("PASS", "应答尾 " + tail.hex(" ").upper())
            return ("FAIL", "应答尾不符" + ((", 期望 " + tail.hex(" ").upper()) if tail else ""))
        return ("PASS", "有应答") if rx else ("FAIL", "无应答")
    return ("PASS", "有应答") if rx else ("FAIL", "无应答")


def smoke(wait=3.0, actions=False, raw=False, repeat=1, _json=False):
    """在线冒烟验收: 开一次 COM3, 按电池逐帧发送并用传输层硬判过.
    全部 PASS -> HEALTHY(exit 0); 有 FAIL -> UNHEALTHY(exit 1). --actions 才发冻结动作."""
    ids = list(SMOKE_DEFAULT) + (list(SMOKE_ACTIONS_EXTRA) if actions else [])
    specs = []
    for i in ids:
        m = resolve(i)
        if len(m) != 1:
            print("冒烟: 帧 %r 在目录无唯一匹配(%d 条), 先修 SPECS" % (i, len(m)))
            return 2
        specs.append(m[0])
    try:
        ser = open_com(raw=raw)
    except Exception as e:
        reason = "open_com 失败: %s" % e
        print("\n==== SMOKE RESULT: UNHEALTHY ====")
        print(reason)
        if _json:
            _pjson({"result": "UNHEALTHY", "reason": reason, "items": []})
        return 1
    transport = "RawCom(ctypes 直连)" if getattr(ser, "raw_com", False) else "pyserial COM3 9600 8E1"
    rounds = max(1, repeat)
    print("冒烟: %d 帧 x %d 轮 | 传输: %s%s" % (len(specs), rounds, transport,
                                            "  [含写记录动作]" if actions else ""))
    items = []
    try:
        for rnd in range(1, rounds + 1):
            if rounds > 1:
                print("\n--- 第 %d 轮 ---" % rnd)
            for s in specs:
                frame = _hex(s)
                rx = tx_recv(ser, frame, wait=wait, tag=s["id"],
                             peer=s.get("peer", ""), what=s["name"])
                v, reason = smoke_verdict(s, rx)
                items.append({"round": rnd, "id": s["id"], "name": s["name"],
                              "kind": s.get("kind"), "transport": transport,
                              "tx_len": len(frame), "rx_len": len(rx),
                              "rx": rx.hex(" ").upper() if rx else "",
                              "verdict": v, "reason": reason})
                tag = str(rnd) if rounds > 1 else ""
                print("SMOKE %-3s %-38s %-5s %s" % (tag, s["id"], v, reason))
    finally:
        ser.close()

    order, byid = [], {}
    for row in items:
        if row["id"] not in byid:
            byid[row["id"]] = []
            order.append(row["id"])
        byid[row["id"]].append(row)
    print("\n--- 汇总(按帧, %d 轮) ---" % rounds)
    nfail = 0
    for fid in order:
        rows = byid[fid]
        worst = "FAIL" if any(r["verdict"] == "FAIL" for r in rows) else "PASS"
        nfail += (worst == "FAIL")
        print("  %-38s %s  (%d/%d 轮 PASS)" % (fid, worst,
                                               sum(r["verdict"] == "PASS" for r in rows), len(rows)))
    healthy = nfail == 0
    print("\n==== SMOKE RESULT: %s ====" % ("HEALTHY" if healthy else "UNHEALTHY"))
    print("说明: 硬判只证 链路+组帧+应答结构 健康; 数值/是否真落库看 runcase + IAR evidence。")
    if _json:
        _pjson({"result": "HEALTHY" if healthy else "UNHEALTHY", "transport": transport,
                "rounds": rounds, "items": items})
    return 0 if healthy else 1


# ============================ 用例层(替换旧 SEQUENCES) ============================
CASES = {
    "4-1": {
        "name": "瞬时冻结: 进厂内 -> 广播瞬时冻结 -> 落库读回",
        "pre": "需开盖或已在厂内。4-1 是唯一需发触发帧的冻结事件。",
        "steps": [
            {"send": "645.factory", "expect": "应答尾 9F 00 D5 16 = 进厂内 OK(超时重发≤3)",
             "note": "前置动作: 后续冻结动作需厂内安全判定放行"},
            {"wait": 1.0, "note": "刚开/重开串口后稍等, 防 CP210x 桥瞬时静默"},
            {"send": "698.action.freeze.immed",
             "expect": "ActionResponse DAR=0 或静默; 判过= Save_FrezData(r0=13) 命中",
             "evidence": "IAR 断点 Save_FrezData/Write_FrezData (r0=13) 命中"},
            {"wait": 3.0, "note": "广播冻结触发表写 EEPROM, 会短暂不响应; 等回落再读回(实测约几十秒内恢复)"},
            {"send": "698.read.freeze.immed_block",
             "expect": "应答 85 03 含瞬时冻结记录(序号+冻结时间); 与触发前比出现新时标=已落库",
             "note": "记录读须走 GetRequestRecord; 时标需人工/IAR 核对"},
        ],
        "pass": "所有串口步骤无 FAIL; 落库由「GetRequestRecord 回读出现新冻结时标」或「IAR Save_FrezData 命中」二者取一证明。",
        "evidence": ["IAR Save_FrezData(r0=13) 命中", "回读记录 OAD 50000200 出现新时标"],
    },
    "4-2": {
        "name": "分钟冻结: 真实整分自然触发 -> 落库读回比对",
        "pre": "已在厂内或能开盖; 触发是固件真实整分边界(MSG_MinStep), 无触发帧, 需等待。",
        "steps": [
            {"note_only": True,
             "text": "不发送触发帧: 等待真实整分边界自然到点(Run_TaskFreeze 收 MSG_MinStep)。跨 1-2 个整分以累积记录; 观察 IAR Write_FrezData(:374) 写库。"},
            {"send": "698.read.freeze.min_block",
             "expect": "应答 85 03 含分钟冻结记录; 与 :374 写库内容比对; 无行=未落库或该通道未投运",
             "evidence": "时标落在真实整分且电量与固件写库一致"},
        ],
        "pass": "GetRequestRecord 读回时标落在真实整分且电量与固件 Write_FrezData 写库一致。",
        "evidence": ["回读记录 OAD 50020200", "IAR 断点 Write_FrezData(TaskFreeze.c:374)"],
    },
}


def _print_case(cid):
    c = CASES[cid]
    print("== 用例 %s | %s" % (cid, c["name"]))
    print("前置:", c.get("pre", ""))
    for i, st in enumerate(c["steps"], 1):
        if "send" in st:
            m = resolve(st["send"])
            extra = (" | %s" % m[0]["name"]) if len(m) == 1 else ""
            print("[%d] 发送 %s%s" % (i, st["send"], extra))
            if st.get("expect"):
                print("     期望: %s" % st["expect"])
            if st.get("evidence"):
                print("     证据: %s" % st["evidence"])
            if st.get("note"):
                print("     注: %s" % st["note"])
        elif "wait" in st:
            print("[%d] 等待 %.1fs" % (i, st["wait"]))
        elif st.get("note_only"):
            print("[%d] (不发帧) %s" % (i, st.get("text", "")))
    print("判过口径:", c.get("pass", ""))
    if c.get("evidence"):
        print("证据来源:", " ; ".join(c["evidence"]))


def plan(task=None):
    """打印用例/相关帧序列(不发). task 如 '4-1'/'4-2'/'common'."""
    if task is None:
        print("== 用例 ==")
        for cid, c in CASES.items():
            print("  %-8s %s" % (cid, c["name"]))
        print("用法: plan <用例id|tag> ; 例: plan 4-1")
        return 0
    if task in CASES:
        _print_case(task)
        return 0
    matched = specs_for(task)
    print("任务 %s 无预定义用例; 该 tag 相关帧:" % task)
    if not matched:
        print("  (无)  可用: cases / list / plan 4-1 / plan 4-2")
        return 0
    for s in matched:
        print("  - %-38s %s" % (s["id"], s["name"]))
        print("      期望: %s" % s.get("expect", ""))
    return 0


def runcase(case_id, wait=3.0, dry=False, _json=False):
    """按序执行一个用例的帧步骤(整个用例只开一次串口). --dry 全程不碰 COM3."""
    if case_id not in CASES:
        raise KeyError("未知用例 %r; 可用: %s" % (case_id, ", ".join(CASES)))
    case = CASES[case_id]
    print("== runcase %s | %s%s" % (case_id, case["name"], "  [DRY]" if dry else ""))
    print("前置:", case.get("pre", ""))
    ser = None
    saw_factory = False
    any_fail = any_tbd = False
    n_send = 0
    rows = []
    try:
        if not dry:
            ser = open_com()
        for i, st in enumerate(case["steps"], 1):
            row = {"step": i}
            if "wait" in st:
                row["type"] = "wait"
                row["sec"] = st["wait"]
                print("[%d] 等待 %.1fs" % (i, st["wait"]))
                if ser:
                    time.sleep(st["wait"])
            elif st.get("note_only"):
                row["type"] = "note"
                row["text"] = st.get("text", "")
                print("[%d] (不发帧) %s" % (i, st.get("text", "")))
            else:
                row["type"] = "send"
                m = resolve(st["send"])
                if len(m) != 1:
                    row["verdict"], row["reason"] = "FAIL", "目录无/歧义帧 %r(匹配 %d)" % (st["send"], len(m))
                    any_fail = True
                    print("[%d] SEND %s -> FAIL: %s" % (i, st["send"], row["reason"]))
                    rows.append(row)
                    continue
                s = m[0]
                n_send += 1
                if s["id"] == "645.factory":
                    saw_factory = True
                frame = _hex(s)
                row["id"], row["name"] = s["id"], s["name"]
                row["tx"] = frame.hex(" ").upper()
                if dry:
                    row["verdict"] = "DRY"
                    shown = frame.hex(" ").upper()
                    print("[%d] [DRY] send %-38s (%dB) %s" % (i, s["id"], len(frame), shown))
                    print("     期望: %s" % (st.get("expect") or s.get("expect", "")))
                    rows.append(row)
                    continue
                # 步骤头(`[%d]` 那个步号是**编排上下文**, 帧行上没有)与帧行并存:
                #   步骤头说"这是第几步", 帧行说"这一帧是什么协议/发给谁/干什么".
                print("[%d] send %s | %s" % (i, s["id"], s["name"]))
                rx = tx_recv(ser, frame, wait=wait, tag=s["id"],
                             peer=s.get("peer", ""), what=s["name"])
                v, reason = machine_verdict(s, rx)
                row["rx"] = rx.hex(" ").upper() if rx else ""
                row["verdict"], row["reason"] = v, reason
                if s.get("needs_factory") and not saw_factory:
                    print("     WARN: 该帧需厂内(needs_factory); 若未先 645.factory 会 DAR=MatchAuth")
                print("     期望: %s" % (st.get("expect") or s.get("expect", "")))
                print("     VERDICT: %-4s %s" % (v, reason))
                any_fail = any_fail or v == "FAIL"
                any_tbd = any_tbd or v == "TBD"
            rows.append(row)
    finally:
        if ser:
            ser.close()

    if dry:
        overall = "DRY"
    elif any_fail:
        overall = "FAIL"
    elif any_tbd:
        overall = "TBD"
    elif n_send == 0:
        overall = "PASS(无发送步骤)"
    else:
        overall = "PASS(串口侧)"
    print("\n--- case %s 汇总: %d 个 send 步骤" % (case_id, n_send))
    print("RESULT: %s" % overall)
    print("判过口径(科学判据, 需人工核对):", case.get("pass", ""))
    if case.get("evidence"):
        print("证据来源:", " ; ".join(case["evidence"]))
    if _json:
        _pjson({"case": case_id, "name": case["name"], "result": overall, "steps": rows})
    if overall == "DRY":
        return 0          # 预演成功本身即是成功(不碰串口, 无 PASS/FAIL 语义)
    return verdict_exit(overall)   # 契约单点: PASS*→0 / FAIL*→1 / TBD 等→2


# ============================ 命令输出 ============================
def _pjson(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def cmd_list(tag, as_json):
    rows = specs_for(tag) if tag else SPECS
    if as_json:
        _pjson({"frames": [_snapshot(s) for s in rows]})
        return 0
    if not rows:
        print("(无帧)")
        return 0
    for s in rows:
        print("%-38s %-22s kind=%-6s tags=%s" % (s["id"], s["name"], s.get("kind", ""),
                                                 ",".join(s.get("tags", []))))
    print("共 %d 条; 叠加层: %s" % (len(rows), _OVERLAY_PATH))
    return 0


def cmd_search(kw, as_json):
    kw = (kw or "").strip()
    hits = [s for s in SPECS if not kw or kw.lower() in _searchable(s)]
    if as_json:
        _pjson({"query": kw, "matches": [_snapshot(s) for s in hits]})
        return 0
    if not hits:
        print("search %r: 无匹配" % kw)
        return 1
    print("search %r: %d 条" % (kw, len(hits)))
    for s in hits:
        print("  %-38s %-22s kind=%-6s oad=%-9s omd=%-9s tags=%s"
              % (s["id"], s["name"], s.get("kind", ""), s.get("oad", ""),
                 s.get("omd", ""), ",".join(s.get("tags", []))))
    return 0


def cmd_show(token, as_json):
    s = _one(token, "show")
    if as_json:
        _pjson(_snapshot(s))
        return 0
    print("%s | %s" % (s["id"], s["name"]))
    print("kind=%s tags=%s alias=%s" % (s["kind"], ",".join(s.get("tags", [])),
                                        ",".join(s.get("alias", []))))
    if s.get("oad"):
        print("oad=%s" % s["oad"])
    if s.get("omd"):
        print("omd=%s" % s["omd"])
    hx = _hex(s)
    print("hex(%d B): %s" % (len(hx), hx.hex(" ").upper()))
    print("expect:", s.get("expect", ""))
    if s.get("evidence"):
        print("evidence:", s["evidence"])
    if s.get("needs_factory"):
        print("needs_factory: True")
    if s.get("check"):
        print("check:", s["check"])
    if s.get("note"):
        print("note:", s["note"])
    return 0


def cmd_dry(token, as_json, param=None, chip=None):
    s = _one(token, "dry")
    try:
        hx = _hex(s, param)
    except ValueError as e:
        print("DRY %s 无法组帧: %s" % (s["id"], e))
        return 2
    note = ""
    if chip is not None:
        if s["kind"] not in ("action", "read"):
            print("DRY %s: --chip 只对 698 帧有效, 该帧是 %s(645), 645 无第二芯片地址" % (s["id"], s["kind"]))
            return 2
        hx = retarget_frame(hx, chip)
        note = "  [改发 %s, AF=0x%02X]" % (chip_of(hx), _norm_chip(chip))
    if as_json:
        snap = _snapshot(s, param)
        snap["hex"], snap["len"] = hx.hex(" ").upper(), len(hx)
        if note:
            snap["chip"] = chip_of(hx)
        _pjson(snap)
        return 0
    print("DRY %s (%dB): %s%s" % (s["id"], len(hx), hx.hex(" ").upper(), note))
    print("kind=%s expect: %s" % (s["kind"], s.get("expect", "")))
    return 0


def cmd_cases(as_json):
    if as_json:
        _pjson({"cases": [{"id": cid, "name": c["name"]} for cid, c in CASES.items()]})
        return 0
    print("== 用例 ==")
    for cid, c in CASES.items():
        print("  %-8s %s" % (cid, c["name"]))
    print("执行: runcase <id> [--dry]")
    return 0


# ============================ 对表操作(语义层: main/脚本直接调, 不拼帧) ============================
# 底层(chip_addr/retarget_frame/send_frame)管"帧+选芯", 本层管"一个对表操作=发什么+怎么判".
# 通用约定: 都需外部开好的 ser; 默认管理芯, 要打计量芯就传 chip="计量芯"(698 才有 chip 维度, 645 无).
# 判过 = 结构层判定(PASS/TBD/FAIL); 数值/真落库靠回读或 IAR evidence(见判读词表).


_FREEZE_SUBCLASS_NAME = FREEZE_SUBCLASS_NAME   # 子类号→名字, 来自标准对象模型 p698(通用)
_EVENT_CODE_NAME = EVENT_CODE_NAME             # 事件编码→名(698 事件类型码); 编码即 OAD 记录 byte1
_EVENT_REC_OAD = EVENT_REC_OAD                 # 事件编码 → 事件记录对象 OAD(30<码>0B<属性>)

# 事件记录**请求属性字节**: 只有 Class 24(A/B/C 类事件)与别不同, 其余一律 0x02。
# 源 = 固件 `DLT698App.c:2951+` `TAB_RecordObj[]` 的 Class/evenum 两列, 规则在 `CMD_GetRequestRecord`
#      (:4806-4832): Class 9/7 ⇒ 必须 ==2; Class 24 ⇒ 必须 ∈ [10-evenum, 9], 且
#      `id = 表项.id + pOAD[2] + evenum - 10` ⇒ **属性字节选的是事件级别**, `10-evenum` = 表里那一项的基级。
#    2026-09-17 实表 A/B 证实(进厂内后): 过载/失压 02·06 ⇒ DAR=4, 07·08·09 ⇒ 规范 85 03;
#    功率反向 06·07·08·09 ⇒ 规范 85 03 —— 边界恰随 evenum 3→4 左移一格, 与 10-evenum 吻合。
# ⚠ **不在本表里的编码一律按 0x02 发**(Class 9 冻结与 Class 7 普通事件都吃这个值 —— 它们才是多数)。
#    判"该不该进本表"的唯一凭据是固件那张表的 Class 列, **不是**"发 02 被拒了就试别的":
#    被拒的现象是 `DAR=4(DAR_Undefined)`, 与"这个事件根本没登记/OAD 写错"长得不一样才怪(都可能是 4),
#    所以宁可来这儿查表。0x00-0x04 未登记进 EVENT_REC_OAD(读不了), 属性先记着, 补登记时直接取用。
EVENT_REQ_ATTR = {
    0x00: 0x07,   # 失压 30000B0A Class24 evenum3 ⇒ 基级=A相 ⇒ 7
    0x01: 0x07,   # 欠压 30010B0A Class24 evenum3
    0x02: 0x07,   # 过压 30020B0A Class24 evenum3
    0x03: 0x07,   # 断相 30030B0A Class24 evenum3
    0x04: 0x07,   # 失流 30040B0A Class24 evenum3
    0x05: 0x07,   # 过流 30050B0A Class24 evenum3
    0x06: 0x07,   # 断流 30060B0A Class24 evenum3
    0x07: 0x06,   # 功率反向 30070B0A Class24 **evenum4**(VER_20Edit 取 #else 支) ⇒ 基级=总 ⇒ 6
    0x08: 0x07,   # 过载 30080B0A Class24 evenum3 ⇒ 基级=A相(ID_OverLoadA=21) ⇒ 7
    0x0B: 0x06,   # 反向无功需量超限 300B0B0A Class24 evenum4 ⇒ 6
}


def _event_req_attr(code):
    """事件编码 → 请求属性字节(不在 `EVENT_REQ_ATTR` 里的一律 0x02)。见该表上方说明。"""
    return EVENT_REQ_ATTR.get(_event_code(code), 0x02)


def _rec_none_reason(ud, what="记录"):
    """读记录**拿不到行**时的一句原因 —— 四种必须分得开(读得出 ud 与没读到是两回事): 静默 /
    被 DAR 打回 / **记录区为空** / 解不出行。**判据只这一份**: 各读回函数各写一遍的话,
    同一个现象会在不同子项里叫不同名字, 而"基线本来就没有记录"与"我们解码器不认"最容易被混成一句。

    `what` 只进文案(如 '过载')。返回 str; 调用方拿去做 `rec(...)` 的 why, **不参与判判定**。
    """
    if not ud:
        return "静默(ud 空)"
    _dar = dar_from_ud(ud)
    if _dar is not None:
        return "读回被拒 DAR=%d(%s)" % (_dar, DAR.get(_dar, "?"))
    _n = ud_record_count(ud)
    if _n == 0:
        return "记录区为空(%s 0 条)" % what
    if _n is not None:
        return "应答里有 %d 条记录却解不出行(ud=%s)" % (_n, ud.hex(" "))
    return "解不出记录行(ud=%s)" % ud.hex(" ")


def _event_code(code):
    """把 事件名(中文/别名) 或 事件编码(int/hex-str) 归一成 事件编码 int. 例 '编程'/0x12/'12'/'0x12'."""
    if not isinstance(code, str):
        return int(code) & 0xFF
    s = code.strip().lower()
    # ① 中文名(取括号前基础名; 容忍 时钟故障(VER_20Edit) 这类带后缀名)
    for k, n in _EVENT_CODE_NAME.items():
        if n.split('(')[0] == s or n == s:
            return k
    # ② 十六进制文本: '12' / '0x12' 或 补零 '12'
    return int(s, 16)


def _chip_name(chip):
    """chip 参数 → 中文芯片名(0x15=计量芯, 否则管理芯). 语义操作打印头用."""
    return "计量芯" if _norm_chip(chip) == 0x15 else "管理芯"


# 脚本入口参数守卫: 实现已迁 common/cli.py(2026-09-10) —— 它是**机制**(argv 认不认), 零协议/零画像成分,
# 长在 L2 语义层的后果是"不认表"的入口脚本(_init_meter.py)想用它就得 import 本模块、连带拽进 project 画像。
# 仍以 CB.guard_argv 的名字可用(9 个 _test_*.py 与 CLAUDE.md 的写法不变); 详见 common/cli.py 的模块 docstring。
from common.cli import guard_argv  # noqa: E402  (重导出, 供 CB.guard_argv 调用点)


# 注: 原先这里有个 `_opout` —— 判定行的打印出口。字形定义本来就在 common/loglabel, 它只是
#   `if quiet: return` + 一次 print, 已一并下沉为 `common.loglabel.opout`(见该函数 docstring)。
#   本文件仍以 `_opout` 别名用它(下面三十来处调用点一字未改), 与 `guard_argv` 同一套做法。
def enter_factory(ser, wait=3.0):
    """发送 → 管理芯: 进厂内(645 广播 0x1F 工厂模式) → 返回 verdict('PASS'/'FAIL'/'TBD').
    发出的帧 = SPECS '645.factory'(广播 AA 通配), 逐字节:
      FE FE FE FE 68 AA AA AA AA AA AA 68 1F 03 42 88 32 EA 16
    应答应答尾 9F 00 D5 16 = OK(厂内常驻, 安全判定 Chk_SafeMode 放行 0x14 写/698 动作)."""
    return send("645.factory", wait=wait, ser_shared=ser)["verdict"]


# 645 退厂内/退编程态: 0x1F / LEN=3 / DI=[0F AA 00], 体 `42 DD 33`(= 明文 0F AA 00 加 0x33)。
# 通路的**证伪历史**: 旧文档写"厂内态无串口退出路径, 只能断电/复位"(本仓 CLAUDE.md/总纲 §10.2/
# _restore_all.py 文件头各一份), 2026-09-10 实测推翻 —— 见 exit_factory 的 docstring。
EXIT_FACTORY_645 = bytes.fromhex("0F AA 00")


def exit_factory(ser, wait=3.0):
    """发送 → 管理芯: **退出**厂内/编程态(645 0x1F 0F AA 00) → 返回 verdict('PASS'/'FAIL'/'TBD').

    发出帧 = SPECS '645.exit_factory'(广播 AA 通配):
      FE FE FE FE 68 AA AA AA AA AA AA 68 1F 03 42 DD 33 40 16
    应答应答尾 9F 00 D5 16 = 受理。

    通路(DLT645Link.c:476 `case 0x1F:` → CMD_ReadPLCAddr(须 LEN==0x02&&DI0==0xAF, 否则 ER_FRAME;
    本帧 LEN=3 故它回 ER_FRAME 被忽略) → 落到 CMD_ExtendIns0x1F_FDW → `case 0x0f: FDW_FacMode(pFrame)`):
      DLT645App.c:3566 `FDW_FacMode`: `pFrame[LEN]==3 && pFrame[DI1]==0xaa` 那一支 → `Set_PrgTimer(0)`。
      **这一支没有任何判定**(进厂内那支 `DI1==0x55` 才要合盖/已在编程态) ⇒ 只要发得出就退得了。
    实测(2026-09-10): 发前 g_PrgTimer[0]=255 → 发后 =0; 回读是 SWD 直读画像 `RAM_VARS["g_PrgTimer"]`。

    ⚠ 用它的时机: 它一发, 后续 698 动作/0x14 写全会被安全判定打回 ER_PSWD / DAR_MatchAuth ——
      凡"跑完测试收拾台面"要排在所有受控步骤**之后**(见 scripts/_restore_all.py 第 [5a] 步)。
    """
    return send("645.exit_factory", wait=wait, ser_shared=ser)["verdict"]


def read_clock(ser, chip=None, wait=3.0, quiet=False):
    """发送 → <管理芯/计量芯>: 读表钟(698 GetRequest OAD 40000200) → 时间串或 None(本函数自打印).
    发出的帧(管理芯, AF=0x05), 逐字节:
      68 17 00 43 05 11 11 11 11 11 11 A1 68 92 05 01 03 40 00 02 00 00 2B 13 16
      APDU = 05 01 03 GetRequest PIID=03 40 00 02 00 时钟OAD 00 无时标标志.
    chip='计量芯' → 同帧只把 AF 0x05 改 0x15(管理芯整帧透传, 应答由 计量芯 出), 逐字节:
      68 17 00 43 15 11 11 11 11 11 11 A1 10 C9 05 01 03 40 00 02 00 00 2B 13 16
    (本帧字节 == SPECS '698.read.time'; chip='计量芯' 时的帧 == 同一帧改 AF 后之帧.)"""
    head, peer = "读表钟", _chip_name(chip)
    frame = frame_698(build_read_apdu(0x03, "40000200"), addr=chip_addr(chip))
    rx = send_frame(ser, frame, wait=wait, tag="read_clock", peer=peer, what=head)
    ts = decode_clock(rx)
    opout(head, frame, (ts if ts else "读钟无应答(非 85 01 带 1C 时标)"),
           ok=bool(ts), quiet=quiet)
    return ts


# ============================ 参量读写通用积木(写参量能力 · 2026-09-09) ============================
# 给"调节/配置写"(阶梯·费率·结算日等)用的通用 645 载体: 任意 DI 都能 0x11 读 / 0x14 受控写。
# 域结构(受控字节/值排布)随 DI 家族各异 → 由上层按语义组好 domain_hex 传入, 本载体只管发+判 0x94;
# 具体 DI 的语义仍落 p698 / 各类型化的高层动词(如 write_billday), 脚本不拼域。费控风格 style 无
# 645/698 写入口(出厂烙死, 见 TaskFreeze.c:875 只读当 TAB_DispPara[style] 下标), 不在本载体覆盖范围。
def _di_wire(di_hex):
    """645 DI 人类串(高组先, 如 '04000B01') → 线上字节(低组先, 反序 01 0B 00 04).
    p645 里 BILLDAY_DI_HEX=04000B01(显示) 与 BILLDAY_READ_DI=010B0004(线上) 的差别即此。
    通用载体统一吃人类串、内部反转, 反转只落这一处(协议单点)。"""
    return bytes.fromhex(di_hex)[::-1]


def read_param_di(ser, di_hex, wait=2.0, head=None, quiet=False):
    """发送 → 管理芯: 645 0x11 读任意 DI → 值字节(自 DI 回显 4B 后起)或 None(本函数自打印).
    di_hex: 人类串(高组先, 如 '04000B01'), 内部反转为线上序。协议知识: 0x11 读 / 应答 0x91 回显 DI;
    组帧自动 +0x33。值结构随 DI 家族各异(结算日=时/日), 由调用方按语义解; 本载体只取「DI 之后的值字节」,
    不猜格式(协议单点)。"""
    # 功能名**不带协议** —— 协议那一格是帧自己的属性(`frame_645` 组帧那一刻钉上的), 别在这儿再写一遍
    head = head or ("读 DI %s" % di_hex.upper())
    frame = frame_645(0x11, _di_wire(di_hex), addr=TABLE_ADDR)
    rx = send_frame(ser, frame, wait=wait, tag="rd_" + di_hex, peer="管理芯", what=head)
    cmd, seg, note = decode_645_reply(rx)
    if cmd != 0x91 or len(seg) < 4:
        opout(head, frame,
               "应答非 0x91(实 0x%02X) 或无 DI 回显" % (cmd if cmd is not None else 0), ok=False,
               quiet=quiet)
        return None
    val = seg[4:]          # 跳过 DI 回显 4B(线上序)
    opout(head, frame,
           "值[%dB]=%s" % (len(val), val.hex(" ").upper()), quiet=quiet)
    return val


def write_param_di(ser, di_hex, value_hex, wait=2.0, tag="wr", head=None, quiet=False):
    """发送 → 管理芯: 645 0x14 受控写任意 DI(需先 enter_factory) → (verdict, note). 本函数自打印.
    di_hex: 人类串(高组先, 如 '04000B01'), 内部反转为线上序作帧头; value_hex: 该 DI 数据域【DI 之后】的
    值明文 hex(受控字节 + 值排布随 DI 家族各异, 结算日 = 02 + 00×8 + 日, 见 _billday_value), 由上层按语义
    组好传入, 本载体只发 + 判 0x94, 不猜值结构(协议单点)。协议知识: 0x14 写 / 应答 0x94=成功 /
    0xD4=拒(带错误码); 组帧自动 +0x33。"""
    head = head or ("0x14 写 DI %s 值=%s%s" % (
        di_hex.upper(), value_hex[:8].upper(), "…" if len(value_hex) > 8 else ""))
    frame = frame_645(0x14, _di_wire(di_hex) + bytes.fromhex(value_hex), addr=TABLE_ADDR)
    rx = send_frame(ser, frame, wait=wait, tag=tag, peer="管理芯", what=head)
    cmd, seg, note = decode_645_reply(rx)
    if cmd == 0x94:
        opout(head, frame, "0x14 写成功(94=14|0x80)", quiet=quiet)
        return "PASS", "0x14 写成功"
    if cmd == 0xD4:
        note = "0x14 被拒 D4 " + (note or seg.hex(" "))
        opout(head, frame, note, ok=False, quiet=quiet)
        return "FAIL", note
    opout(head, frame, "写无 0x94 应答" + (("(命令 0x%02X)" % cmd) if cmd is not None else ""),
           ok=False, quiet=quiet)
    return "FAIL", "无 0x94 应答"


def read_billday(ser, wait=2.0):
    """发送 → 管理芯: 读第1结算日(645 0x11 读 DI 04000B01) → (时, 日) 或 None(本函数自打印).
    当前应为 (0,5)=每月5号0点. 发出的帧(与 overlay 'user.rd.billday.d1' 同字节), 逐字节:
      FE FE FE FE 68 11 11 11 11 11 11 68 11 04 34 3E 33 37 27 16
      数据域 -0x33 = 01 0B 00 04(DI) | 00 05(值: 时0/每月5号0点)."""
    head = "读第1结算日"
    frame = frame_645(0x11, BILLDAY_READ_DI, addr=TABLE_ADDR)   # 645 结算日 DI 来自标准对象模型 p645
    rx = send_frame(ser, frame, wait=wait, tag="read_billday", peer="管理芯", what=head)
    cmd, seg, _ = decode_645_reply(rx)
    if cmd != 0x91 or len(seg) < 6:
        opout(head, frame, "应答非 0x91(实 0x%02X) 或无值" % (cmd if cmd is not None else 0),
               ok=False)
        return None
    h, d = seg[-2], seg[-1]
    opout(head, frame, "时=%d 日=%d → 每月%d号0点" % (h, d, d))
    return (h, d)


def _billday_value(day):
    """0x14 写第1结算日 DI=04000B01 的【DI 之后】值明文(与 overlay v05/v06 解密一致, DI 由 write_param_di
    从人类串反序生成), 逐字节:
    02(DAT0=受控写, 需厂内) | 00×7(空) | 00 <day>(值: 时0/每月<day>号0点)."""
    day = int(day)
    if not (1 <= day <= 31):
        raise ValueError("结算日 day 需 1..31, 实为 %r" % day)
    return b"\x02" + b"\x00" * 7 + bytes([0x00, day])


def write_billday(ser, day, wait=2.0):
    """发送 → 管理芯: 写第1结算日=每月 <day> 号0点(645 0x14 受控写, 需先 enter_factory)
    → (verdict, 说明). 本函数自打印 TX. 发出的帧(day=6, 与 overlay 'user.wr.billday.d1.v06' 同字节), 逐字节:
      FE FE FE FE 68 11 11 11 11 11 11 68 14 0E 34 3E 33 37 35 33 33 33 33 33 33 33 33 39 3A 16
      day=5 与 overlay 'user.wr.billday.d1.v05' 同字节:
      FE FE FE FE 68 11 11 11 11 11 11 68 14 0E 34 3E 33 37 35 33 33 33 33 33 33 33 33 38 39 16
      数据域 -0x33 = 01 0B 00 04(DI 线上序) | 02 | 00×7 | 00 <day>(两帧只差末值字节与重算的 CS); 其它 day 同理.
    判过: 应答命令 0x94(94=14|0x80, 写成功); 0xD4=被拒(带错误码). 组帧/判过经通用载体
    write_param_di 单点(DI 反序 + _billday_value), 本函数只给 DI 人类串 + 值 + 头标."""
    head = "写第1结算日=每月%d号0点(0x14 DI 04000B01)" % day
    return write_param_di(ser, "04000B01", _billday_value(day).hex(), wait=wait,
                          tag="wr_billday_%02d" % day, head=head)


def billday_range_evidence(ser, day, want=None, settle=1.0, wait=2.0, tag=""):
    """写第1结算日 = `day` 并回读 → `(ok, detail)`; 读不到 → `ok=None`(**没做成**, 不是 FAIL)。

    `want=True` 期望这次写被收下(回读 == day); `want=False` 期望被拒(回读 != day);
    `want=None` 不预设, 只如实报"回读等于/不等于所写值"。
    规范那条界是「1 日至 28 日内的整点」, 固件的界在 `DLT645App.c:2227`(只收 `时<=23 && 日 1..28`)。
    """
    before = read_billday(ser)
    if not before:
        return None, "写前读结算日无应答 ⇒ 这一条没做成(%s)" % (tag or "")
    _v, note = write_billday(ser, day, wait=wait)
    time.sleep(settle)
    after = read_billday(ser)
    if not after:
        return None, "写后读结算日无应答 ⇒ 这一条没做成(%s)" % (tag or "")
    hit = after[1] == day
    detail = "%s写 %d 号: 写前 %d → 写后 %d(应答 %s)" % (
        ("%s " % tag) if tag else "", day, before[1], after[1], note)
    if want is None:
        return hit, detail
    return hit == bool(want), detail + " ← 期望%s" % ("收下" if want else "被拒")




def billday_rw_roundtrip(ser, settle=1.0, wait=2.0):
    """【写参量能力自证】用第1结算日(唯一本台可写的费控 DI 04000B01)证通用 0x14 写 + 0x11 读载体真能读写
    并恢复原样: 读现值 → 写一个与原值不同、且落在 1..31 内的日期 → 读回验等于所写值(据此分辨写生效还是卡住没写动) → 写回原值 → 读回验等于原值。
    判据(库内单点, 脚本不留): ①写应答 0x94 ②读回值字节==刚写 ③写回后==原值。逐步骤自打印 PASS/FAIL。
    返回 (all_ok, d0)。只动结算日参数, 需先 enter_factory; 不改表钟/其它参数, 跑完结算日恢复 d0(表无净变)。

    ⚠ **自恢复(try/finally, 2026-09-10 加)**: 本函数自己把结算日改成了 alt。若中途异常、或"写回 d0"
      那次没确认成功, 表会**留在 alt 号**上 —— 而 `scripts/_restore_all.py` 明文"不动结算日"
      (依据是"测试脚本跑完已自恢复"), 那个假设的兑现点就在这里。`d0` 只存在于本次调用的内存里,
      事后没人知道原值 ⇒ 只能由**发过帧的这一方**兜。恢复是 housekeeping, 与判过无关:
      res 该 False 还是 False, 兜底**不把失败吞成成功**(见本函数末尾)。
    """
    bd = read_billday(ser, wait=wait)
    if not bd:
        print("   !! 读结算日无应答(d0 不可得), 中止")
        return (False, None)
    d0 = bd[1]
    alt = d0 + 1 if d0 < 31 else d0 - 1
    res, back_ok = True, False      # back_ok = "写回 d0 那次确认成功了吗" = 兜底是否该启动
    try:
        print("\n== 结算日读写闭环: 现值 d0=%d → alt=%d → 恢复 d0=%d (判过全在库) ==" % (d0, alt, d0))
        for step, day, want in (("写 alt=%d" % alt, alt, alt), ("写回恢复 d0=%d" % d0, d0, d0)):
            v, note = write_billday(ser, day, wait=wait)      # 0x14(库内打印 0x94/0xD4)
            time.sleep(settle)                                # 等 EEPROM 回落
            rb = read_param_di(ser, BILLDAY_DI_HEX, wait=wait)   # 0x11 读回(库内打印值)
            got = rb[-1] if rb and len(rb) >= 2 else None
            okk = (v == "PASS" and got == want)
            print("   %s → 读回=%s(应=%d)  %s" % (step, got if got is not None else "-", want, "PASS" if okk else "FAIL"))
            res = res and okk
            back_ok = okk          # 循环末次 = 写回 d0 那次
        print("== 结算日读写闭环: %s   (净停在 d0=%d, 表无净变) ==" % ("PASS" if res else "FAIL", d0))
        return (res, d0)
    finally:
        if not back_ok:
            # 表可能停在 alt —— 不把状态恢复就出判定是**静默改参数**, 比跑失败坏。
            # ⚠ 这里**只恢复、不改判**: 走到这儿时 res 必为 False(或本就是异常退出), 失败信号已经在了;
            #   若在此把 res 抹成 True, 就正是"兜底把错误藏起来"。故本块不碰 res, 只打印。
            try:
                print("\n   !! 结算日兜底: 写回 d0 那次没确认成功, 表可能停在 %d 号 —— 再写一次每月%d号" % (alt, d0))
                write_billday(ser, d0, wait=wait)
                rb = read_param_di(ser, BILLDAY_DI_HEX, wait=wait)
                got = rb[-1] if rb and len(rb) >= 2 else None
                if got == d0:
                    print("   兜底结果: 表已恢复每月%d号(状态 OK, 但本轮判过不受影响)" % d0)
                else:
                    print("   !! 兜底回读未命中(实为 %s) —— 表结算日可能仍非 %d 号, 请人工核" % (got, d0))
            except Exception as exc:
                print("   !! 结算日兜底失败: %s —— 表结算日可能已非 %d 号, 请人工处理" % (exc, d0))


def rate_no_decode(raw):
    """`g_RateNo` 那一次 AA80 读回的**原始字节** → 费率号(1..C_RateNum)或 None. 本函数自打印.

    读由调用方做(裸 `watch.watch_vars(ser, ["g_RateNo"], tag="读当前费率号")`), 这里只解码 ——
    与 3-1 的 `rate_trace_decode` 同一条分家法。
    固件 g_RateNo=INT8U[1+2]: [0]=当前费率号值、[1][2]=CRC(TaskRate.c); 合法 1..C_RateNum(C_RateNum=4)。
    ⚠ 画像 g_RateNo size 已按固件数组 3B 核对; 只取 [0] 当费率号, 不把 CRC/邻居当值。"""
    if not raw or len(raw) < 1 or raw[0] == 0 or raw[0] > 4:
        print("   → 当前费率号读不出/g_RateNo[0] 非法(%r)" % (raw.hex(" ") if raw else None))
        return None
    print("   → 当前费率号 = 费率%d (g_RateNo[0], 合法 1..4)" % raw[0])
    return raw[0]


# ==================== 3-x 两套表切换设定(时区/时段 · 2026-09-10) ====================
# 为什么 3-2 与 11-1/11-2 都可建(同处一个 switch, 判定不一样, 但**两道判定在本台都放行**):
#   固件 DLT645App.c:1661 写「两套时区表切换时间」DI 000106、:1672 写「时段表切换时间」DI 000107,
#   **二者无 style 判定**; 同段 :1669 的费率/阶梯切换(000108/000109)写着
#   `if (TAB_MeterSty.style != TP_Local) return ER_D0D1`。
#   ⚠ **2026-09-16 订正**: 本节原写「本台非本地表, 故 11-1/11-2 死、3-2 活」——**那句是错的**。
#   本台 `TAB_MeterSty.style == TP_Local`(证据: `Config/MengXi/UserCfg.h:102` 走 `#define Local_Meter`
#   活动分支, `:344-345` 定义 `TP_Local 1`; `.out` 直读该字段得 1)。⇒ 两道判定**都放行**,
#   11-1/11-2 与 3-2 一样可测。同一条订正见本文件 4-7 段(`Check_BillFrezY` 的风格判定同理)。
# 整条链(改这段前先照这四条):
#   ① 645 0x14 写 → :1663 Is_nBCD(5) → nBCD_nHEX(5) → Set_ZoneSlotSw(id, &DAT8)
#   ② Set_ZoneSlotSw(TaskRate.c:407): Check_YYMMDDhhmm → Write_ParaData 落库 →
#        :429 若「当前表钟 >= 设定」→ Post_Message(MSG_MinStep) 立即发分节拍 + g_*SwNo = NO_1(FALSE=85);
#        :441 否则(设定在未来) → g_*SwNo = NO_2(TRUE=170), **不切**。
#   ③ 分节拍 → Run_TaskRate(:82-83) → Check_Switch(:302): 到点 → :323 Save_FrezData(切换冻结)
#        → :324-325 **把设定清零** → :326-353 备用套覆盖当前套; :319 未到点则 NO_2 直接返回不切。
#   ④ ⇒ **"写一个已过去的时刻" = 当场切套, 不必拨表钟**(与 4-6 的 settle_across_master 不同, 零钟残留);
#        "写一个未来时刻" = 只置设定不切 —— 正是判据⑤「未到点不误切」的对照半支。
# 帧字节口径(enum 见 DLT645App.c:41-88, 索引以 STR1=0 起算): LEN=9, DI0..DI3=10..13, DAT0=14。
#   写侧分派 = `datLen | DI0<<8 | DI1<<16 | DI2<<24`, 而 `datLen = pFrame[LEN] - 12`, 又 LEN = 4(DI)+值长
#   ⇒ **datLen = 值长 - 8**, 即「值」= 8B 受控前缀 + 真值, 处理函数一律读 **&pFrame[DAT8]**(=值偏移 8)。
#   旁证: 已跑通的结算日 `_billday_value` = `02 | 00×7` + 2B 真值(=10B), datLen=2; 费率数 case 0x00020401
#   读 pFrame[DAT8] 且 datLen=1 ⇒ 值 9B。**本族一律沿用 `02 | 00×7` 这个 8B 前缀**(受控写, 需厂内)。
#   ⚠ 读写偏移不对称: **读**侧把值放 `&pFrame[DAT0]`(:5358, 紧跟 DI), **写**侧取 `&pFrame[DAT8]`。
#     故读回 5B、写要 13B, 别把两侧的值长度搞混。
# 值格式: DateTime.c:112 明写「数据入口(HEX码,分时日月年)」, 线上经 Is_nBCD/nBCD_nHEX 走 **BCD**;
#   故 5B = 分 时 日 月 年, 每字节 BCD(两 nibble 各 ≤9), 年取 2 位(2000 基)。
# 观测三条(全串口, 免 IAR):
#   · `g_ZoneSwNo`/`g_SlotSwNo`(AA80 watch_vars, 画像 0x200090D7/D8): 85=FALSE=第1套 / 170=TRUE=第2套。
#     三态可辨: 未设定→85; 写未来(待切)→170; 写过去(已切)→85。⚠ 前后两个 85 不同因, 要结合设定/冻结一起看。
#   · 切换冻结记录 OAD `0x50080302`(时区)/`0x50090302`(时段)(DLT698App.c:3038-3039; 645 侧 6410-6411 同源,
#     645 DI 50080200/50090200)。**只由 Check_Switch 切换体 :323 产生** ⇒ 它新增 = 切套体真跑了。
#   · 设定读回: 切换发生后被固件清零(:324-325), 且读入口对"非待切"态一律回 00×5(:5362-5366)
#     ⇒ 读到全 0 = 未设定或已切; 读到非 0 = 有待切设定(此时 g_*SwNo 必为 170)。
_SWSET_KIND = {"zone": ("04000106", "时区表"), "slot": ("04000107", "时段表")}
_SWSET_FREZ_OAD = {"zone": "50080302", "slot": "50090302"}
_SWSET_PREFIX = b"\x02" + b"\x00" * 7          # 8B 受控前缀, 与 _billday_value 同一约定


def _swset_value(when):
    """切换设定时刻 → 645 写帧的「值」 = 8B 受控前缀 + 5B BCD(分 时 日 月 年)。共 13B。
    when: 'YYYY-MM-DD HH:MM[:SS]' 或 datetime。逐字节 BCD(固件 Is_nBCD 会拒非 BCD)。
    字节序依据 Platform/DateTime.c:112 与该处 Check_YYMMDDhhmm(:118)。"""
    if isinstance(when, str):
        s = when.strip()
        when = datetime.datetime.strptime(s, "%Y-%m-%d %H:%M" if len(s) == 16 else "%Y-%m-%d %H:%M:%S")
    fields = (when.minute, when.hour, when.day, when.month, when.year % 100)
    return _SWSET_PREFIX + bytes(((f // 10) << 4) | (f % 10) for f in fields)


def _swset_str(when):
    """datetime/串 → 回显用统一格式。**只到分** —— 切换设定线上只带 分时日月年(无秒),
    打印带秒会让人以为发了秒级精度(2026-09-10 实踩: 头打 14:16:58 而线上是 14:16)。"""
    if isinstance(when, str):
        s = when.strip()
        return s[:16] if len(s) >= 16 else s
    return when.strftime("%Y-%m-%d %H:%M")


def set_zone_slot_switch(ser, kind, when, wait=2.0):
    """发送 → 管理芯: 645 0x14 写「两套时区表/时段表切换时间」→ (verdict, note). 本函数自打印.
    kind: 'zone'=时区表(DI 04000106) / 'slot'=时段表(04000107); when: 'YYYY-MM-DD HH:MM[:SS]' 或 datetime。
    **设成已过去的时刻 = 当场切套**(固件 TaskRate.c:429-431 立刻发分节拍 → Check_Switch), 不必拨表钟 ——
    本动词因此是本仓唯一「零钟残留」的切换触发法。需先 enter_factory(0x14 受控写)。
    判过只证"写入被受理"(应答 0x94); **"切了没"必须另看** g_*SwNo(85/170)与切换冻结记录(read_switch_frez)。
    固定格式理由见本节头注。"""
    di, name = _SWSET_KIND[kind]
    head = "0x14 写%s切换时间 = %s(过去=当场切/未来=待切)" % (name, _swset_str(when))
    return write_param_di(ser, di, _swset_value(when).hex(), wait=wait,
                          tag="wr_swset_" + kind, head=head)


def read_zone_slot_switch(ser, kind, wait=2.0):
    """发送 → 管理芯: 645 0x11 读切换设定 → (分,时,日,月,年) 或 None(本函数自打印). 纯读, 无副作用.
    ⚠ 读入口对「非待切」态一律回 00×5(DLT645App.c:5362-5366: 第1套且值非全99 → 清零返回), 故:
      全 0  = 未设定(Init 装入的编译期表 UserCfg.c:322-323 本就全零) **或** 切换已发生被固件清零(:324-325);
      非 0  = 确有一条待切设定(此时 g_*SwNo 必为 170=第2套)。两种"全 0"靠切换冻结记录区分, 别只看这一条。"""
    di, name = _SWSET_KIND[kind]
    val = read_param_di(ser, di, wait=wait, head="读%s切换设定(DI %s)" % (name, di))
    if val is None or len(val) < 5:
        return None
    bcd = val[:5]
    if bcd == b"\x00" * 5:
        print("   → 设定为空(未设定 / 已切后被固件清零) —— 看切换冻结记录区分")
    else:
        try:
            t = tuple(((int(b) >> 4) & 0xF) * 10 + (int(b) & 0xF) for b in bcd)
            print("   → 待切设定 = %02d-%02d-%02d %02d:%02d(分时日月年 BCE 解码)" % (t[4], t[3], t[2], t[1], t[0]))
        except Exception:
            t = None
            print("   → 设定 %s 非 BCD, 不猜解码" % bcd.hex(" ").upper())
        return t
    return (0, 0, 0, 0, 0)


def read_switch_frez(ser, kind, pos=1, chip=None, wait=3.0):
    """发送 → 管理芯: 698 GetRequestRecord 读「时区表/时段表切换冻结」第 pos 条 → {verdict,reason,seq,ts}.
    kind: 'zone'/'slot'; OAD 0x50080302 / 0x50090302(DLT698App.c:3038-3039)。行布局同冻结, 复用 decode_freeze_row。
    这是 3-2 判据③的**直接证据**: 该记录只由 Check_Switch 切换体 TaskRate.c:323 Save_FrezData 产生 ⇒
    它新增 = 切套体真跑了; 对照跑(写未来时刻)里它必须**不新增**。本函数自打印。"""
    oad = _SWSET_FREZ_OAD[kind]
    name = _SWSET_KIND[kind][1]
    head = "读%s切换冻结 pos%d(%s) OAD=%s" % (name, pos, "最新" if pos == 1 else "第%d条" % pos, oad)
    rsd = bytes([0x09, int(pos) & 0xFF])
    apdu = build_getrecord_apdu_oad(0x03, oad, rsd=rsd)      # rcsd 用默认 FREEZE_RCSD(序号+时标)
    frame = frame_698(apdu, addr=chip_addr(chip))
    rx = send_frame(ser, frame, wait=wait, tag="swfrez_%s_pos%d" % (kind, pos),
                    peer="管理芯", what=head)
    r = decode_freeze_row(rx)
    if r is None:
        dar = decode_getrecord_dar(rx)
        reason = _rec_none_reason(split_apdu(rx or b""), name) if dar is None \
            else "记录读回被拒 DAR=%d(%s)" % (dar, DAR.get(dar, "?"))
        opout(head, frame, reason, ok=False)
        return {"verdict": "FAIL", "reason": reason, "seq": None, "ts": None, "dar": dar}
    line = "序号=%s 切换时标=%s" % (r[0], r[1] if r[1] else "无1C时标")
    opout(head, frame, line)
    return {"verdict": "PASS", "reason": line, "seq": r[0], "ts": r[1]}


# ==================== 3-2 前置: 两套时区表/时段表 的**内容**读写(2026-09-10 建)====================
# 为什么要有这一节: 3-2 判据②b"切后的当前套内容 == 原备用套内容"、④"切后费率归属按新套" —— 编译期
#   `TaskRate.c:125-126` 把**同一个** TAB_ZoneTab 同时装进 Zone1Tab/Zone2Tab, 两套内容同形 ⇒ 切与不切
#   数值上一样, 判据不可分辨(旧结论)。出路不是"证不了", 而是**先把备用套写成不一样**(备用套此刻不在用,
#   改它不影响表的现行行为)。本节的积木就是那个前置动作, 2026-09-10 在真表上实测走通(见 log/
#   probe_zone_tab_write_*.log): 两处写 DAR 均 = 0, 读回确为改后内容, 恢复读回 == 原值。
# 固件依据(改本节前先照这四条):
#   ① 写检查: 当前套 401402/401602 = DAR_RefuseOp(拒); **备用套 401502/401702 放行**(DLT698App.c:10566-10638);
#      时段表**整表**(索引 0)也拒, 只收单项。
#   ② 单项**索引字节就是条目号**(1 起): 时区表条目 1..14(NUM_ZoneDiv), 时段表 1..8(NUM_SlotTab)
#      (读侧 DLT698App.c:7862-7884 同口径)。
#   ③ 切套体 `TaskRate.c:326-353` = `Read_ParaData(备用套) → Write_ParaData(当前套)`(时段表 8 张全拷)
#      ⇒ **切后当前套 == 切前备用套**, 判据②b 于是可直接对拍。同一分节拍内 `Calculate_RateNo()`
#      (`TaskRate.c:85-94`) 会用换过之后的两套表重算 g_RateNo ⇒ 判据④也可直接观察。
#   ④ 数据域字节口径(实测坐实, 与固件 Sort 校验互证):
#      · 应答 ud = `85 01 <PIID> <OAD4> <1B> <数据域> 00 00` ⇒ **数据域 = ud[8:-2]**(电能整列同此约定)
#      · 时区表条目 8B = `02 03 11 <月> 11 <日> 11 <时段表号>`; Sort 在 reverse 后读到 (表号,日,月)
#        ⇒ 数据域顺序 = 月/日/表号。月 1..12, 日 ≤ TAB_DayOfMonth[月], 表号 1..8(TaskRate.c:562-572)
#      · 时段表 单项 = `01 <n>` + n×`02 03 11 <时> 11 <分> 11 <费率号>`; Sort 在 reverse 后读到
#        (费率号,分,时) ⇒ 数据域顺序 = 时/分/费率号。费率号 1..C_RateNum(UserCfg.h:288 = 4), 分 ≤59, 时 ≤23
#        ⚠ 时段表**必须 Sort 得过**(升序 + 时刻不重复), 否则固件静默 `break` 而 DAR 停在入口的
#          DAR_Success ⇒ **写坏了也回成功**(DLT698App.c:10070 与 :10626-10629)—— 所以写完一律**读回对拍**。
_TAB_SET_OAD = {"zone": {"cur": "401402", "bak": "401502"},     # 当前套 / 备用套 时区表
                "slot": {"cur": "401602", "bak": "401702"}}     # 当前套 / 备用套 时段表
RATE_NUM = 4                                                   # C_RateNum(UserCfg.h:288); 换编译档要跟着改


def oad_ud_data(ud):
    """698 普通 GET 应答 ud → **数据域**(ud[8:-2]: ud[7]=结果字节, 末 2B = 应答尾). 太短回 b''。
    同一约定电能整列早就在用(energy_ele_of 取 ud[8:] 再截 47)。"""
    return bytes(ud[8:-2]) if len(ud) >= 12 else b""


def zone_tab_item_data(month, day, no):
    """时区表条目 → 数据域 8B = `02 03 11 <月> 11 <日> 11 <时段表号>`(顺序依据见本节头注④)。"""
    return b"\x02\x03" + bytes([0x11, month, 0x11, day, 0x11, no])


def zone_tab_item_parse(data):
    """数据域 → (月, 日, 时段表号); 形态不符回 None(不猜)。"""
    if len(data) != 8 or data[0:2] != b"\x02\x03":
        return None
    return (data[3], data[5], data[7])


def slot_tab_data(entries):
    """[(时,分,费率号), ...] → 时段表 单项 数据域 = `01 <n>` + n×`02 03 11 <时> 11 <分> 11 <费率号>`。"""
    body = b"".join(b"\x02\x03" + bytes([0x11, h, 0x11, mi, 0x11, r]) for h, mi, r in entries)
    return bytes([0x01, len(entries)]) + body


def slot_tab_parse(data):
    """时段表 单项 数据域 → [(时,分,费率号), ...]; 形态不符回 None(不猜)。"""
    if len(data) < 2 or data[0] != 0x01:
        return None
    n = data[1]
    if len(data) != 2 + n * 8:
        return None
    out = []
    for i in range(n):
        e = data[2 + i * 8:10 + i * 8]
        if e[0:2] != b"\x02\x03":
            return None
        out.append((e[3], e[5], e[7]))
    return out


def rate_of(entries, hhmm):
    """纯函数: 该时刻落在哪个时段 → 费率号。entries=升序 [(时,分,费率号)...], hhmm=(时,分)。
    规则同固件 Calculate_RateNo: 取**时刻 ≤ 当刻的最后一个**条目(无则回 None)。"""
    hit = None
    for h, mi, r in entries:
        if (h, mi) <= tuple(hhmm):
            hit = r
        else:
            break
    return hit


def read_tab_whole(ser, kind, set_="cur", chip=None, wait=3.0):
    """发送 → 读 <当前套/备用套> <时区表/时段表> **整表** → 数据域 bytes(b'' 表示读不到). 静默。
    拿它做**整表对拍**(判据②b: 切后当前套整表 == 切前备用套整表), 比只比单项强。"""
    oad = _TAB_SET_OAD[kind][set_] + "00"
    return oad_ud_data(read_oad_ud(ser, oad, chip=chip, wait=wait))


def zone_tab_whole_parse(data):
    """时区表整表数据域 → [(月, 日, 时段表号), ...]; 形态不符回 None(不猜)。"""
    if len(data) < 2 or data[0] != 0x01:
        return None
    n = data[1]
    if len(data) != 2 + n * 8:
        return None
    out = []
    for i in range(n):
        e = data[2 + i * 8:10 + i * 8]
        if e[0:2] != b"\x02\x03":
            return None
        out.append((e[3], e[5], e[7]))
    return out


def which_slot_at(zone_entries, month, day):
    """时区表条目(升序 [(月,日,时段表号)]) + 当日(月,日) → **生效的时段表号**。
    规则同固件: 取 (月,日) ≤ 当日的**最后一条**; 一条都不 ≤ 则取第一条。"""
    if not zone_entries:
        return None
    hit = zone_entries[0][2]
    for m, d, no in zone_entries:
        if (m, d) <= (month, day):
            hit = no
    return hit


def read_zone_tab_item(ser, set_="cur", item=1, chip=None, wait=2.0):
    """发送 → 读 <当前套/备用套> 时区表第 item 项 → (月, 日, 时段表号) 或 None。本函数自打印。"""
    kind, name = "zone", ("当前套" if set_ == "cur" else "备用套")
    oad = _TAB_SET_OAD[kind][set_] + "%02X" % int(item)
    d = oad_ud_data(read_oad_ud(ser, oad, chip=chip, wait=wait))
    got = zone_tab_item_parse(d)
    # 方向/对象/协议三格**不在这里写** —— 上面那两行帧行由 loglabel 出(见其模块头)。
    print("== 读%s时区表第%d项 OAD=%s → %s" % (
        name, item, oad,
        ("月=%d 日=%d 时段表号=%d" % got) if got else "读回形态不符(%s)" % d.hex(" ")))
    return got


def write_zone_tab_item(ser, set_, item, month, day, no, chip=None, wait=3.0):
    """发送 → 写 <当前套/备用套> 时区表第 item 项(月/日/时段表号) → (verdict, note, dar). 本函数自打印。
    **当前套会被固件拒(DAR=3 拒绝操作)** —— 判据②b/④ 的预置只能落在备用套。需先 enter_factory。"""
    kind, name = "zone", ("当前套" if set_ == "cur" else "备用套")
    oad = _TAB_SET_OAD[kind][set_] + "%02X" % int(item)
    v, note, dar = write_oad_ud(ser, oad, zone_tab_item_data(month, day, no), chip=chip, wait=wait,
                                tag="[写%s时区表第%d项 月%d日%d表号%d]" % (name, item, month, day, no))
    return v, note, dar


def read_slot_tab(ser, set_="cur", which=1, chip=None, wait=3.0):
    """发送 → 读 <当前套/备用套> 第 which 张时段表 → [(时,分,费率号), ...] 或 None。本函数自打印。"""
    kind, name = "slot", ("当前套" if set_ == "cur" else "备用套")
    oad = _TAB_SET_OAD[kind][set_] + "%02X" % int(which)
    got = slot_tab_parse(oad_ud_data(read_oad_ud(ser, oad, chip=chip, wait=wait)))
    print("== 读%s第%d张时段表 OAD=%s → %s" % (
        name, which, oad,
        ("%d 段: %s" % (len(got), " ".join("%02d:%02d=费率%d" % e for e in got))) if got
        else "读回形态不符"))
    return got


def write_slot_tab(ser, set_, which, entries, chip=None, wait=3.0):
    """发送 → 写 <当前套/备用套> 第 which 张时段表(整张替换) → (verdict, note, dar). 本函数自打印。
    ⚠ 固件 Sort 不过时**静默 break 而 DAR 仍= 成功**(见本节头注④) ⇒ 调用方写完**必须读回对拍**。
    **当前套被拒(DAR=3)**; 整表(索引0)也被拒, 只能逐张写。需先 enter_factory。"""
    kind, name = "slot", ("当前套" if set_ == "cur" else "备用套")
    oad = _TAB_SET_OAD[kind][set_] + "%02X" % int(which)
    v, note, dar = write_oad_ud(ser, oad, slot_tab_data(entries), chip=chip, wait=wait,
                                tag="[写%s第%d张时段表 %d段]" % (name, which, len(entries)))
    return v, note, dar


def plant_bak_tabs(ser, kind, which=1, chip=None, wait=3.0):
    """3-2 前置(单点): 把**备用套**写成与当前套**不同**的内容, 并读回坐实。
    kind='zone' → 改时区表第1项的**月**(+1 回绕, 今天在 09 月 ⇒ 行为不受影响, 只当"内容不同"的标记);
    kind='slot' → 改第 which 张时段表里**每一段**的费率号(r → r%4+1) —— 逐段都改是为了让
      "切后费率归属"这一条**不受当刻落在哪一段**影响(判据④)。
    返回 `(ok, 说明, 原值)`; 原值是恢复用的(哪个 kind 给哪个)。写不进(非 0 DAR)⇒ ok=False。
    ⚠ 备用套此刻不在用, 改它不动表的现行行为; 但**必须恢复**(用 restore_bak_tabs)。"""
    if kind == "zone":
        orig = read_zone_tab_item(ser, "bak", 1, chip=chip, wait=wait)
        if orig is None:
            return False, "备用套时区表第1项读不出, 没法在此基础上改", None
        want = (orig[0] % 12 + 1, orig[1], orig[2])
        v, note, _ = write_zone_tab_item(ser, "bak", 1, *want, chip=chip, wait=wait)
        back = read_zone_tab_item(ser, "bak", 1, chip=chip, wait=wait)
        ok = v == "PASS" and back == want
        return ok, "备用套时区表第1项 月 %d→%d; 写=%s 读回=%s(期望月=%d)  %s" % (
            orig[0], want[0], v, back, want[0], "PASS" if ok else "FAIL"), orig
    orig = read_slot_tab(ser, "bak", which, chip=chip, wait=wait)
    if not orig:
        return False, "备用套第%d张时段表读不出, 没法在此基础上改" % which, None
    want = [(h, mi, r % RATE_NUM + 1) for h, mi, r in orig]
    v, note, _ = write_slot_tab(ser, "bak", which, want, chip=chip, wait=wait)
    back = read_slot_tab(ser, "bak", which, chip=chip, wait=wait)
    ok = v == "PASS" and back == want
    return ok, "备用套第%d张时段表 费率逐段+1; 写=%s 读回%s(期望 %s)  %s" % (
        which, v, "==所写" if back == want else "!=所写 %s" % (back,), want,
        "PASS" if ok else "FAIL"), orig


def restore_bak_tabs(ser, kind, orig, which=1, chip=None, wait=3.0):
    """把备用套写回 `plant_bak_tabs` 拿到的原值, 并读回。返回 (ok, 说明)。"""
    if orig is None:
        return False, "没有原值可恢复"
    if kind == "zone":
        v, _, _ = write_zone_tab_item(ser, "bak", 1, *orig, chip=chip, wait=wait)
        back = read_zone_tab_item(ser, "bak", 1, chip=chip, wait=wait)
        ok = v == "PASS" and back == orig
    else:
        v, _, _ = write_slot_tab(ser, "bak", which, orig, chip=chip, wait=wait)
        back = read_slot_tab(ser, "bak", which, chip=chip, wait=wait)
        ok = v == "PASS" and back == orig
    return ok, "备用套恢复 写=%s 读回%s原值  %s" % (v, "==" if ok else "!=", "PASS" if ok else "FAIL")


# BOOL 的**内存值**(不是 DWARF 显示名): Config/TypeDef.h:26 `FALSE = (INT8U)0x55` ⇒ TRUE=0xAA。
# 判据直接认这两个字节; 见别的值一律报"未登记值, 不猜"(AA80 读到的是原始 RAM, 没有 DWARF 帮你翻译)。
_SW_BOOL = {0x55: "第1套(NO_1=FALSE)", 0xAA: "第2套(NO_2=TRUE)"}


def swset_sw_no_decode(kind, raw):
    """g_ZoneSwNo/g_SlotSwNo 的**原始字节** → (字节|None, 说明)。**只解码不读**。

    读是脚本的事(`W.watch_vars`), 解码是库的事 —— 与 `scripts/_check_aa80_vs_swd.py` 是同一分工。
    `raw` = AA80 读回的那条字节串, 读不到给 None。判据直接认**内存里的 BOOL 字节**(0x55/0xAA),
    别的值一律报"未登记值, 不猜"(见 `_SW_BOOL` 上方那段)。
    """
    var = "g_ZoneSwNo" if kind == "zone" else "g_SlotSwNo"
    if not raw:
        return None, "%s 读不出(AA80 无应答)" % var
    return raw[0], "%s=0x%02X(%s)" % (var, raw[0], _SW_BOOL.get(raw[0], "未登记值, 不猜"))




def swset_tuple(when):
    """时刻 → 与 read_zone_slot_switch 同序的 (分,时,日,月,年), 供对拍。"""
    return (when.minute, when.hour, when.day, when.month, when.year % 100)


# 切换冻结的类型号 —— FrezData.h:20-42 的 ID_FREZ 枚举(首项 ID_ErrFrez=0 起数):
#   ... ID_BillFrezM=12, ID_ImmedFrez=13, ID_CycleFrez=14, **ID_ZoneSwFrez=15, ID_SlotSwFrez=16** ...
# 它就是 TaskRate.c:323 `Save_FrezData(TAB_Switch[id].idFrez, &swTime[0])` 的实参来源。
_ID_SW_FREZ = {"zone": 15, "slot": 16}


def swset_name(kind):
    """kind('zone'/'slot') → 这一种切换的表名(『时区表』/『时段表』)。"""
    return _SWSET_KIND[kind][1]


def swset_frez_id(kind):
    """kind('zone'/'slot') → 该切换冻结的类型号(ID_ZoneSwFrez=15 / ID_SlotSwFrez=16)。

    脚本拿它对拍断点读到的 `TAB_Switch[id].idFrez` —— 期望值按**符号名**比对, 不硬编码数字(见 :2314)。
    """
    return _ID_SW_FREZ[kind]


# 断[A]/断[B] 各自"要读哪些量" = **脚本的数据**, 不在本层:
# `project/tests/_test_3_2_zone_slot_switch.py` 的 `VARS_A`/`VARS_B`(命名约定 `BP_<X>` ↔ `VARS_<X>`),
# 由 `帧收发基础/scripts/_check_anchors.py` 抠出来逐条对源码核对断点(变量在断点那一行赋过值没有)。
# **为什么不放在这里**: 它是"这块表这个子项的断点在哪儿、要读什么" —— 子项数据, 与"断点观测怎么跑"
# 无关; 放库里会让 `_check_anchors.py` 抠不出配对(它只认脚本里的字面量), 于是那种观测静默地没人核过。
# 本层只管两件事: 怎么把 gdb 的打印解成值(`gdb_ints`/`gdb_sym`)、以及该等于什么(`_ID_SW_FREZ`)。
#
# ⚠⚠ 写断点的人照办这两条(2026-09-10 断点观测实测得出):
#   ① `swTime` **逐元素读, 不整条读** —— 它是 `INT8U[5]`(实为 char), gdb 对 char 数组**按字符串打印**,
#      整条读回是 `"0\016\n\t\032"` 这种转义串, 又难解又不稳。逐元素读的是标量, 打印必为整数。
#      (同理: `id` 会打成 `1 '\001'` = 十进制数 + 字符字面量, 解析要先剥字符字面量。)
#   ② **固件内部的 swTime 是二进制, 不是线上的 BCD** —— 写路上 DLT645App.c:1664
#      `Is_nBCD(&pFrame[DAT8],5)` + `nBCD_nHEX(...)` 把线上 BCD **转成二进制**才交给 Set_ZoneSlotSw;
#      :311 读回来的自然也是二进制; :319 `Comp_Data(pGet_Time(EM_Min), &swTime[0], 5)` 拿它跟 RTC 的
#      二进制时刻直接比。读回路上 :5365/:5382 `nHEX_nBCD` 再转回 BCD 上线。
#      ⇒ 对拍值 = `swset_tuple(二进制 (分,时,日,月,年))`, **不是** `_swset_value`(线上的 BCD 字节)。


def gdb_ints(text):
    """gdb 打印出来的整型文本 → int 列表。

    `{48, 20, 16, 9, 38}`(整型数组)、`{0x30, 0x14}`、裸标量 `15` 都能吃。
    读不到(None / 含 `optimized out`)→ 空列表, 由调用方判。

    ⚠ **先剥掉字符字面量** —— 2026-09-10 实跑踩到: gdb 打 `INT8U` 形参会写成 `1 '\\001'`
    (十进制数 + 该值的字符写法), 不剥的话 `\\001` 会被当成数字 `1`, 于是 `id` 解析成 `[1, 1]`,
    "id == 1" 这条断言**假 FAIL**(固件没错, 是解析错)。
    """
    if not text or "optimized out" in text or "No symbol" in text:
        return []
    body = re.sub(r"'(\\.|[^'])*'", "", str(text))      # 剥 '\\001' 这类字符字面量
    out = []
    for tok in re.findall(r"0x[0-9a-fA-F]+|\d+", body):
        out.append(int(tok, 16) if tok.lower().startswith("0x") else int(tok))
    return out


def gb1(text):
    """gdb 打印出来的**单个**标量(`17 '\\021'` / `0x11`) → int; 不是单个就 None。

    为什么要一个"单个"的版: `gdb_ints` 是给数组/列表用的, 对单个标量它照样返回列表 ——
    而调用方(守卫行的 `buff[3]` 这类)**读不到时必须是 None**, 不能是 `[]` 也不是 0:
    守卫那两个字节组 `0x00`/`0xFF` 都是**有意义的值**(表示"空"), 把"读不到"当 0 会让
    『有头无尾』的判定凭空成立 —— 那看起来就是"固件没落库", 而其实是这次没读到。
    """
    v = gdb_ints(text)
    return v[0] if len(v) == 1 else None


def gdb_sym(text):
    """gdb 打印出来的**枚举量/符号名** → 名字字符串; 不是名字就原样返回。

    为什么单列一个: DWARF 里枚举是强类型的, gdb 打 `TAB_Switch[id].idFrez` 回的是
    `ID_SlotSwFrez` **不是数字** —— 硬编码数字去比会恒 FAIL(CLAUDE.md 调试链纪律 4:
    "判据请直接断言枚举名, 勿硬编码数字")。数字形态也认, 免得换个 gdb 版本就红。
    """
    if not text or "optimized out" in text or "No symbol" in text:
        return None
    m = re.search(r"[A-Za-z_]\w*", str(text).strip())
    return m.group(0) if m else str(text).strip()


_GDB_ESC = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11,
            "\\": 92, "'": 39, '"': 34, "?": 63}

# `'\000' <repeats 80 times>` / `"\000" <repeats 80 times>` —— gdb 对长数组省着印时用的写法。
# 单元是**一个引号段**(单引号或双引号), 后面紧跟 `<repeats N times>`。
_REP_QUOTED = re.compile(
    r"('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")\s*<repeats\s+(\d+)\s+times>")


def _expand_quoted_repeats(m):
    """把 `'\\000' <repeats 80 times>` 摊成 80 个 `'\\000'`(以 `, ` 相接), 供逐段解析。"""
    unit, n = m.group(1), int(m.group(2))
    return ", ".join([unit] * n)


def _gdb_lit_bytes(seg):
    """**一段**(不含外层引号)字面量正文 → 字节列表: 处理 `\\ooo` 八进制、`\\xhh` 与常规转义。

    从 `gdb_bytes` 的形态① 里抽出来 —— 现在要按**段**解析(见 `_REP_QUOTED` 那段 ⚠)。
    """
    out = []
    i, n = 0, len(seg)
    while i < n:
        c = seg[i]
        if c != "\\":
            out.append(ord(c) & 0xFF)              # 可打印字符原样(按 Latin-1 取低字节)
            i += 1
            continue
        i += 1
        if i >= n:
            break
        c = seg[i]
        if c in "01234567":                        # \ooo 最多三位八进制
            j, k = i, i
            while k < n and k < i + 3 and seg[k] in "01234567":
                k += 1
            out.append(int(seg[j:k], 8) & 0xFF)
            i = k
        elif c == "x":                             # \xhh(有的 gdb 对高位字节用它)
            j, k = i + 1, i + 1
            while k < n and k < i + 3 and seg[k] in "0123456789abcdefABCDEF":
                k += 1
            out.append(int(seg[j:k], 16) & 0xFF if k > j else ord("x"))
            i = k
        else:
            out.append(_GDB_ESC.get(c, ord(c)) & 0xFF)
            i += 1
    return out


def gdb_bytes(text):
    """gdb 打印出来的 **`unsigned char`(`INT8U`) 数组** → 字节列表; 取不到 / 不是数组 → `[]`。

    ⚠ **gdb 把 `INT8U` 数组按字符串字面量印, 不是 `{1, 2, 3}`** —— 2026-09-17 对着本表 `.out`
    离线实测: `print (unsigned char[3]) {1,2,3}` 回的是 `"\\001\\002\\003"`(不可打印字节印成
    三位八进制转义, 可打印字符原样写)。拿 `gdb_ints` 去抠那种文本会**静默得到一个错的序列**:
    它只认十进制/`0x`, 于是 `"\\012\\015"` 被读成 `[12, 15]`(真值 `[10, 13]` —— 把八进制当十进制),
    而 `"AB"` 直接读成 `[]`(字母里没有数字)。后果不是"报错", 是"**读到的不是要读的东西**":
    「写库那一刻记录正文与固件那份电量数据逐字节相同」这类断言会**看着算过**, 其实比的是碎片。
    这就是单列一个解析器的全部理由(与 `gdb_ints` 先剥字符字面量同一类坑)。

    吃三种形态: ① gdb 的字符串字面量(含 `\\ooo` 八进制转义与常规转义);
    ② `{...}` 整型数组(别的 gdb 版本 / `char` 没被当字符串时); ③ `{... <repeats N times>}`(零填充)。
    读不到(`None` / `optimized out` / `No symbol`)一律 `[]`, 由调用方判三态。
    """
    s = str(text or "")
    if "optimized out" in s or "No symbol" in s:
        return []
    s = s.strip()
    if not s:
        return []
    # ---- `<repeats N times>` 的**引号段**形态 ----
    # ⚠ 2026-09-18 实踩(守卫探针第一次把 95 字节的 `buff` 整条读回来):
    #   gdb 对长数组会印成 `"\0318\021\021\t\032", '\000' <repeats 80 times>, "\377…"` ——
    #   中间那一段是**引号段 + repeats**, 而形态① 原先从第一个 `"` 抠到**最后一个** `"`,
    #   于是 `' <repeats 80 times>, ` 这 27 个字符被当作**正文字节**逐个收下:
    #   实测 94 字节的数组读成 **42 字节**, 且从第 7 个字节起整个错位
    #   (读回 `[25,56,17,17,9,26, 34,44,32,39,0,39,…,34, 255,…]` —— 中间那串是 `", '\0', <repeat` 的 ASCII)。
    #   它是**静默**的: 长度会变、字节会错位, 但两样都"像样地打印" ——
    #   在判据里只表现为"两边不同", 于是「写库那一刻 buff 与 g_EngyData 逐字节相同」这类
    #   对拍会**看着算过 / 看着不同**, 而其实比的根本不是要读的东西。
    #   故**先展开重复段再解析**(形态②③ 那个 `{0 <repeats 80 times>}` 是同一件事的另一种写法)。
    s = _REP_QUOTED.sub(_expand_quoted_repeats, s)
    out = []
    if s.startswith('"'):
        # ---- 形态①: 字符串字面量(展开后是**多段**, 逐段解析再首尾相接) ----
        # ⚠ 2026-09-18 第二踩: 同一行里 gdb **两种引号混着用** —— 常规段用 `"…"`, 零填充段用 `'…'`。
        #   只 findall 双引号那一路, 展开出来的 80 个 `'\000'` **一个都收不到**(实测仍读成 14 字节,
        #   头 6 + 尾 8, 中间 80 个凭空消失 —— 而它不报错, 只表现为"两边不同")。
        #   故**两种引号一次扫、按出现次序**收: 分两次 findall 会把段落次序打乱(尾部那段双引号会
        #   跑到前头去), 读回来的仍然是错的字节序。
        for _m in re.finditer(r'"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'', s):
            out.extend(_gdb_lit_bytes(_m.group(1) if _m.group(1) is not None else _m.group(2)))
        return out
    if "{" in s and "}" in s:
        # ---- 形态②③: `{...}` 整型数组(先把 `<repeats N times>` 展开, 再按 `gdb_ints` 抠数) ----
        body = s[s.index("{") + 1:s.rindex("}")]

        def _expand(m):
            # ⚠ 整个匹配(值 + `<repeats …>`)一起换掉 —— 只删 `<repeats …>` 的话那个值会留下来,
            #   于是 `{0 <repeats 80 times>}` 变成 **81** 个 0(静默多一个, 对拍时按"不同"处理)。
            _v = gdb_ints(m.group(1))
            return ", ".join([str(_v[-1] & 0xFF) if _v else "0"] * int(m.group(2)))

        body = re.sub(r"([^,{}]*?)\s*<repeats\s+(\d+)\s+times>", _expand, body)
        # `{…}` 里混进**引号段**的情形(展开后成一片 `'\000'`) ⇒ 先把每段字面量换成等价的
        # 十进制序列, 再交给 `gdb_ints` —— 它不认引号, 会**静默**丢掉那些段。
        if '"' in body or "'" in body:
            body = re.sub(r'"((?:[^"\\]|\\.)*)"',
                          lambda m: ", ".join(str(v) for v in _gdb_lit_bytes(m.group(1))), body)
            body = re.sub(r"'((?:[^'\\]|\\.)*)'",
                          lambda m: ", ".join(str(v) for v in _gdb_lit_bytes(m.group(1))), body)
        out.extend(v & 0xFF for v in gdb_ints(body))
        return out
    return []


def _direct_trigger(bpno, fn, *a, **kw):
    """**没有调试会话**时的 `trig` 形状 —— 与 `swdbg.breakpoint.trigger(sess=None, …)` 同形同义。

    为什么库里要备一份: `cmd_bank` 属 meterlib, **不许 import swdbg**(它是与 meterlib **平行**的另一条
    通路的可选重依赖, 见 CLAUDE.md「工程1」)。所以会话由**脚本**持有, 库只收一个
    `trig(bpno, fn, *a, **kw) -> {hit, vars, result, error, trigger_alive}` 回调 ——
    台面接了 J-Link, 脚本传绑好会话的那支; 没接, 传本函数。**两种观测因此走同一段判据代码, 不会分叉。**

    控制参数(`timeout` 与所有 `_` 前缀)必须先摘干净再直呼 —— 不摘会一路漏进串口动词的签名里。
    (4-6 实踩: 漏摘 `_drop` → `write_billday()` 收到意外关键字 → TypeError 被吞进 `error` 字段 →
    **写回那一次静默失败**, 表被留在错误号上而没人知道。)
    """
    kw.pop("timeout", None)
    for k in [k for k in kw if k.startswith("_")]:
        kw.pop(k)
    try:
        return {"hit": None, "vars": {}, "result": fn(*a, **kw), "error": None, "trigger_alive": False}
    except Exception as exc:
        return {"hit": None, "vars": {}, "result": None, "error": exc, "trigger_alive": False}


def _wbline(tag, r, hit_expected=True):
    """把 `trig()` 的返回打成**一行取证** → (hit|None, {量: 文本})。与 swdbg.breakpoint.report 同形,
    只是库内不 import 它(见 `_direct_trigger` 的说明)。打印完不改判, 判由调用方做。"""
    hit, vals = r.get("hit"), r.get("vars") or {}
    if r.get("error") is not None:
        print("   == 断点取证 %s: FAIL(触发动作自己报错: %s)" % (tag, r["error"]))
        return None, vals
    if hit is None:
        print("   == 断点取证 %s: %s%s" % (tag, "TBD(没命中)" if hit_expected else "未命中(期望如此)",
                                       "(触发线程仍在跑)" if r.get("trigger_alive") else ""))
        return None, vals
    body = "  ".join("%s=%s" % (k, v if v is not None else "(该行读不到)") for k, v in vals.items())
    print("   == 断点取证 %s: 命中 %s bkpt=%s  |  %s" % (tag, hit.where(), hit.bkptno, body))
    return hit, vals


def _bpok(x):
    """`x` 是不是**断点写法**——四种之一, 判别字与 `swdbg.breakpoint.normalize` 同一套。

    为什么库里要备一份: 同 `_direct_trigger` —— `cmd_bank` 属 meterlib, 不许 import swdbg。
    用途只有一个: 脚本把断点写法传进来时**当场拦住写错的字面量**。不拦就得等下游抛,
    而那时往往已经开过口、进过厂内。
    """
    if not isinstance(x, (tuple, list)):
        return False
    if len(x) == 2:
        return True                       # ("func", 函数名) 或 (文件, 行号)
    if len(x) == 3:
        return x[0] == "line"
    if len(x) == 4:
        return x[0] in ("call", "prev")
    return False


def _bptxt(spec):
    """断点写法 → 人读的那串(与 `swdbg.breakpoint.text` 同形同义, 库内不 import 它, 见 `_bpok`)。

    认不出的照 `repr` 出来、**不抛**: 这几处都在打提醒/取证的行上, 抛了会把一次已经跑起来的
    观测变成未定论; 写法收不收由 `_bpok` 与 `break_at_anchor` 各在自己的入口上管。
    """
    if isinstance(spec, (tuple, list)):
        if len(spec) == 3 and spec[0] == "line":
            return "%s:%d" % (spec[1], spec[2])
        if len(spec) == 2 and spec[0] == "func":
            return '("func", %s)' % (spec[1],)
        if len(spec) == 4 and spec[0] in ("call", "prev"):
            return '("%s", %s, %s, %d)' % (spec[0], spec[1], spec[2], spec[3])
        if len(spec) == 2:
            return "%s:%d" % (spec[0], spec[1])
    return str(spec)


# 3-2 的**预设判据条目** —— 测试**之前**就定死(源 = ledger.md 3-2 的 I 列「判过: ②③④⑤」), 供脚本
# `J = CB.Judge("3-2 …", CB.zone_slot_switch_criteria(), …)` 用。**为什么是函数不是全局常量**:
# 它得跟下面的 roundtrip 摆在一起(判据与取证同处一读就懂), 但本层不该往外多一个全局名。
#
# ⚠ 两条拆分, 都是被实测逼出来的(不是洁癖 —— 不拆就会记出假通过):
#   · `②a`/`②b` —— 规格原文"②到点自动切套、当前套=原备用"断的其实是**两件事**: "切套动作发生了"
#     (g_*SwNo 翻 + 设定被消费, 可证) 与 "切后的内容 == 原备用的内容"(**本台不可证**: 编译期
#     TaskRate.c:125-126 把同一个 TAB_ZoneTab 同时装进 Zone1Tab/Zone2Tab, 两套在数值上同形)。
#     合成一条的话, 证到前一半就只好记"满足", 那半条就成了白捡的。
#   · `⑤a`/`⑤b` —— 同理: "设定被清=不重复切"与"未到点不误切"是两次独立观察(后者更要紧:
#     一个"永远切"的坏实现, 前者照样过)。
#   · ④ 不拆 —— 它整条今天就**不可证**(见 ④ 那条 add 的说明), 拆也拆不出证据来。
def zone_slot_switch_criteria():
    """3-2 的预设判据条目 {条目号: 一句话|{"text":…, "unprovable":…}}。

    键名 = 脚本/库之间认领证据的号(见 common/judge.py)。
    ⚠ 2026-09-10 改口径: ②b/④ **原先声明 unprovable**, 理由是"编译期两套表同内容 ⇒ 切与不切同形"。
      那个理由只说明**当前表态**分辨不出, 不说明**证不了** —— 备用套可写(固件放行 401502/401702),
      先把两套写成不同内容再切, 两条就都能直接观察。已实测走通(log/probe_zone_tab_write_*.log),
      故撤销 unprovable, 由 roundtrip 的**前置预置**把这两条变成普通可证条目。
      要证不了也得是"写被拒"这种**台面事实**, 不能是"我没做那一步"——后者叫省略, 不叫不可证。
    """
    return {
        "②a": "到点自动切套(固件消费掉设定 ⇒ g_*SwNo 转第1套)",
        "②b": "切后的当前套内容 == 原备用套内容(整表对拍; 前置: 先把备用套写成不同内容)",
        "③": "时区/时段各出一帧对应的切换冻结",
        "④": "切后费率归属按新套(前置: 备用套时段表费率逐段改成与当前套不同)",
        "⑤a": "设定被清 = 不重复切",
        "⑤b": "未到点不误切",
    }


def read_freeze_row(ser, subclass=0x05, pos=1, chip=None, wait=3.0, empty_ok=False):
    """发送 → <管理芯/计量芯>: 698 GetRequestRecord 读记录第 pos 条(1=最新) → {verdict,reason,seq,ts}.
    subclass: 见 p698.FREEZE_SUBCLASS_NAME(0x00瞬时/0x02分钟/0x03小时/0x04日/0x05月结算/
    0x06月冻结/0x08时区切换/0x09时段切换/0x0A费率切换/0x0B阶梯切换/0x11阶梯结算);
    OAD = 50 <子类> 02 00. 本函数自打印.
    empty_ok=False(默认): 应答 85 01 空数组 ⇒ 判 FAIL("该记录对象无内容") —— 适用于"该有却没有"的场合.
    empty_ok=True: 空数组 ⇒ 判 TBD 并回报 empty=True —— 适用于"按设计就不该产生"的**否定期望**半支
      (如 4-7: 没跨结算边界那趟本就不该落记录), 那时空记录是**达成**不是失败, 由调用方按判据定性.
    ⚠ 两种"读不到"必须分得开(2026-09-16 修, 返回值里由 `answered` 承载): `answered=True`+`empty=True`
      = **收到了应答且它说是空数组**(该记录对象确实没内容); `answered=False` = **这一趟没收到任何字节**
      (没读到, 与"该对象有内容"与否无关)。原先两者都落进"空数组"支 ⇒ 断链/干跑会被否定期望半支
      读成达成。**用 `empty` 之前先看 `answered`**。
    发出的帧(结算冻结 subclass=0x05, pos=1, 管理芯), 逐字节:
      68 24 00 43 05 11 11 11 11 11 11 A1 14 FF 05 03 03 50 05 02 00 09 01 02 00 20 23 02 00 00 20 21 02 00 00 59 8D 16
      APDU = 05 03 03 GetRequestRecord PIID=03 | 50 05 02 00(结算冻结OAD) | 09 01(RSD: 方法09取第1条=最新)
             | 02 00 20 23 02 00 00 20 21 02 00 00(RCSD: 回2列=记录序号OAD+冻结时间OAD) | 00
    pos=2(倒数第2条) 只改 RSD 成 09 02, 逐字节:
      68 24 00 43 05 11 11 11 11 11 11 A1 14 FF 05 03 03 50 05 02 00 09 02 02 00 20 23 02 00 00 20 21 02 00 00 AE 83 16
    (最新一条/倒数第 2 条 字节 == SPECS '698.read.freeze.billmonth' / '.pos2'.)
    应答应为 85 03(GetResponseRecord) 带记录行; 应答只回 85 01 空数组 = 该记录对象无内容(判 FAIL)."""
    cls = _FREEZE_SUBCLASS_NAME.get(subclass, "sub%02X" % subclass)
    peer = _chip_name(chip)
    head = "读%s冻结 pos%d(%s)" % (cls, pos, "最新" if pos == 1 else "第%d条" % pos)
    rsd = bytes([0x09, int(pos) & 0xFF])
    apdu = build_getrecord_apdu(0x03, subclass, rsd=rsd)
    frame = frame_698(apdu, addr=chip_addr(chip))
    rx = send_frame(ser, frame, wait=wait, tag="rec_sub%02x_pos%d" % (subclass, pos),
                    peer=peer, what=head)
    r = decode_freeze_row(rx)
    if r is None:
        dar = decode_getrecord_dar(rx)
        # ⚠ **没应答 ≠ 该记录对象无内容**(2026-09-16 修): 判据是**对象本身**(这一趟到底收没收到字节),
        #   不是"解码器没解出 DAR" —— 原先两者都落 `dar is None`, 于是"对端一个字都没回"被读成
        #   "空记录", 在**否定期望**半支里静默变成达成(干跑/断链都会那样绿)。`answered` 把两件事分开。
        answered = bool(rx)
        reason = ("记录读回被拒 DAR=%d(%s)" % (dar, DAR.get(dar, "?"))) if dar is not None \
            else "无 85 03 记录行(或该记录对象无内容)"
        if not answered:
            opout(head, frame, "无任何应答 ⇒ 这一次**没读到**(不是『该记录对象无内容』)", ok=None)
            return {"verdict": "TBD", "reason": "无应答: 没读到", "seq": None, "ts": None,
                    "dar": None, "empty": False, "answered": False}
        if empty_ok and dar is None:
            # 否定期望半支: 该对象按设计就不该有记录 ⇒ 空数组是达成, 不是失败; 定性交给调用方.
            opout(head, frame, reason + " —— 本次按『不该产生』口径收, 是否为达成由判据定")
            return {"verdict": "TBD", "reason": reason, "seq": None, "ts": None, "dar": None,
                    "empty": True, "answered": True}
        opout(head, frame, reason, ok=False)
        return {"verdict": "FAIL", "reason": reason, "seq": None, "ts": None, "dar": dar,
                "empty": dar is None, "answered": True}
    line = "序号=%s 冻结时标=%s" % (r[0], r[1] if r[1] else "无1C时标")
    opout(head, frame, line)
    return {"verdict": "PASS", "reason": line, "seq": r[0], "ts": r[1], "dar": None,
            "empty": False, "answered": True}


def frez_area_count(ser, subclass=0x05, chip=None, wait=3.0):
    """该**冻结记录区**有几条(698 GetRequestRecord 应答里的记录条数) → int; 读不出 → None。

    与 `read_freeze_row` 分工: 那一个取"第 pos 条那一行"的内容, 这一个只发**一帧**问条数, 用来判
    "某个动作之后条数增了几条"; `immed_frez_count` 又是另一件事(逐条读 pos=1.. 直到读空, 判"第 N 条
    还读不读得出来"), 三者不可互替。
    条数由 `p698.ud_record_count` 从 `85 03` 里解(1B / 81+nB / 82+nB 三种写法都认), 与固件
    `CMD_GetRequestRecord` 收尾那处回填同一判据。
    ⚠ 无应答 / DAR 打回 ⇒ None(**没读成**, 不是"0 条"); 拿它做减法前先判 None。
    """
    ud = read_record_ud(ser, subclass, 1, rcsd(_REC_SEQ_OAD, _REC_TIME_OAD), chip=chip, wait=wait)
    n = ud_record_count(ud)
    print("   记录区条数(子类 0x%02X): %s" % (int(subclass), n if n is not None else "**读不出**"))
    return n


def read_event_row(ser, code, pos=1, rcsd=None, chip=None, wait=3.0, col=None):
    """发送 → 698 GetRequestRecord 读 事件记录 第 pos 条(1=最新) → {verdict,reason,seq,ts} 或 raw.
    code = 事件名(如 '编程'/'拉闸') 或 事件编码 int(如 0x12); OAD 书写形 = 30<编码>0B<属性>(p698 EVENT_REC_OAD),
    发前经 record_req_oad 归一化成 30<编码><属性>00 —— **属性**按该事件表项的类别取(`_event_req_attr`):
    Class 7/9 ⇒ 2, Class 24(A/B/C 类事件: 过载/功率反向/失压…) ⇒ 10-evenum, 取错会被回 DAR=4(见 p698 该段说明).
    应答应为 85 03(GetResponseRecord); 默认 RCSD 只选 记录序号 20220200 + 发生时间 201E0200 两列.
    行布局与冻结一致(TAB_Program: 0x20220200 T_DoubleLongUn + 0x201E0200 T_DateTimeS), 故复用 decode_freeze_row。
    DAR!=0 判 FAIL(不再是 TBD); 无 85 03 才回 TBD 并打印原始 RX 供校准. 本函数自打印."""
    code = _event_code(code)
    oad = _EVENT_REC_OAD.get(code)
    # `col` 只改**打印标签**: 同一段解码换个 RCSD 读的就是另一列(如结束时间列),
    # 标签照旧写"发生时刻"会让人把两列读串(日志里两行长得一样)。默认不变。
    name = _EVENT_CODE_NAME.get(code, "未知编码0x%02X" % code)
    peer = _chip_name(chip)
    if oad is None:
        # `frame=None`: 这一趟**根本没发帧**(连 OAD 都没登记), 判定行只能自己站着 —— 所以不传 proto
        opout("事件编码0x%02X(%s) 未登记读回 OAD" % (code, name), None, "FAIL", ok=False)
        return {"verdict": "FAIL", "reason": "未登记事件OAD", "seq": None, "ts": None}
    attr = _event_req_attr(code)
    head = "读%s事件 pos%d(%s) OAD=%s(发 %s)" % (
        name, pos, "最新" if pos == 1 else "第%d条" % pos, oad,
        record_req_oad(oad, attr=attr).hex(" ").upper())
    rsd = bytes([0x09, int(pos) & 0xFF])
    apdu = build_getrecord_apdu_oad(0x03, oad, rsd=rsd, rcsd=rcsd or EVENT_RCSD, attr=attr)
    frame = frame_698(apdu, addr=chip_addr(chip))
    rx = send_frame(ser, frame, wait=wait, tag="event_%02x_pos%d" % (code, pos),
                    peer=peer, what=head)
    r = decode_freeze_row(rx)
    if r is None:
        dar = decode_getrecord_dar(rx)
        if dar is not None:
            reason = "记录读回被拒 DAR=%d(%s)" % (dar, DAR.get(dar, "?"))
            opout(head, frame, reason, ok=False)
            return {"verdict": "FAIL", "reason": reason, "seq": None, "ts": None, "dar": dar,
                    "rx": rx.hex(" ")}
        # 不是 DAR 打回: 分清**记录区为空**(台面本来就没有这条记录)与**行解码对不上**(协议侧),
        # 两者都要打印完整原始应答(空的那种也留着, 复核时能自证)。见 `_rec_none_reason`。
        why = _rec_none_reason(split_apdu(rx or b""), name)
        # `ok=None`(印 `[TBD]`)与下面 return 的 verdict 对齐 —— 这一支是**没做成**(行没解出来),
        # 印成 `[FAIL]` 会让读日志的人把它当成一条"固件坏了"的结论(5-8 实踩)。
        opout(head, frame, "%s(见原始 RX: %s)" % (why, rx.hex(" ")), ok=None)
        return {"verdict": "TBD", "reason": why, "seq": None, "ts": None, "rx": rx.hex(" ")}
    # `r[1] is None` ⟺ **选中的那一列回的是 D_NULL**。行解不出来时 `decode_freeze_row` 交回 None,
    # 上面那一支已经接走了 ⇒ 能走到这里而时标是空的, 只可能是"固件按设计没写这一格"
    # (最新一条事件尚未结束: DLT698App.c:5788-5798 给结束时间列写 NULL), 不是"没读到"。
    line = "序号=%s %s=%s" % (r[0], col or "发生时刻", r[1] if r[1] else "空(固件按设计没写这一格)")
    opout(head, frame, line)
    return {"verdict": "PASS", "reason": line, "seq": r[0], "ts": r[1], "rx": rx.hex(" ")}


def event_advanced(rec, pre=None, not_before=None):
    """判『这条记录是本次触发新落的』→ (ok, 说明)。5-x 各子项共用的纯数据比较(不带协议知识)。

    **比较口径: 序号优先, 序号比不出来才比时刻**(2026-09-16 修, 起因是 5-6 实跑)。
      记录序号由固件递增(与 `rec_advanced` 同一句事实), 它是**与表钟无关**的推进证据;
      时刻则会被表钟本身带偏 —— 5-6 首跑(2026-09-16 14:03)那台表钟被上一轮留在 2026-09-10,
      写结算日**确实新增了记录**(序号 3→5), 而新记录盖的是当时那台表的钟 ⇒ 后时刻反而更小
      ⇒ 旧口径读成"未推进"。那是**与事实相反**的读数: 表钟被拨过之后, 比时刻量的是钟, 不是记录。
      序号相同则**不**直接判"没推进", 仍退回比时刻 —— 两种情形要分开: ①同一条没被改过
      (如 5-6 的合并语义就地改写) ⇒ 时刻也不动 ⇒ False; ②记录被清了重建/序号回绕, 恰好又是同一个
      号 ⇒ 时刻是新的 ⇒ True。混作一条会把②读成"没推进"。
      ⚠ `not_before`(本次触发时刻)仍在**退回比时刻**那一路生效; 序号能给出推进证据时不再拿钟去否它
      —— 那正是这一处要修的东西。

    **`ok` 是三态**(2026-09-16 修, 与 5-8 判据③ 那个假 FAIL 同源):
      · `True`  —— 记录解出了发生时刻, 且比 `not_before`/`pre` 都新 ⇒ 本次触发落了库;
      · `False` —— **读到了**记录, 但它是库里的旧记录(时刻早于本次触发 / 与触发前一样) ⇒ 真结论;
      · `None`  —— **一个字都没读到**。那既可能是命令被拒, 也可能是串口没应答 —— **没做成**。

    改这一处的由来: 原先"读不到"返 `False`(记 FAIL, 理由"报代码侧"), 而干跑整轮的表象正是
    "什么都读不到" ⇒ 一次干跑就把 5-5 判成「失败」, 而它的 `why` 自己就列着"命令被拒 / 未落库 /
    读回被拒"三种可能 —— 那是**分不清**, 不是**判得出错**。5-8 判据③ 上真发生过同类假 FAIL
    (拿旧记录去比注入窗口), 当时已定为库侧缺陷。⚠ 调用点必须显式认 `None`(`if ok is None:`),
    写成 `if ok:` 会把"没做成"并进"失败"那一支 —— 那正是这处要修掉的东西。

    `pre` 无记录(库刚被清过)不算失败 —— 只要 `rec` 足够新就说明本次触发落了库; 这比"必须前后
    两条都读到"稳(实测 5-5 事件库被清后前条即无)。"""
    ts = ((rec or {}).get("ts") or "").strip()[:19]
    pts = ((pre or {}).get("ts") or "").strip()[:19]
    seq, pseq = (rec or {}).get("seq"), (pre or {}).get("seq")
    if seq is not None and pseq is not None and seq != pseq:
        return True, "序号推进(前=%s 后=%s)" % (pseq, seq)
    if not ts:
        return None, ("读不到记录(命令被拒 / 未落库 / 读回被拒 / 串口没应答) —— 没做成, "
                      "不据此判固件")
    # ⚠ 先于 `not_before` 判这一支: 序号相同且时刻也没动 = **同一条记录**, 直接说"还是同一条"。
    #   放到 `not_before` 之后就会被写成"是旧记录" —— 结论一样(都是 False), 但那是另一件事:
    #   5-6 的合并语义就是"同一条被就地改写", 而"旧记录"听起来像"读错了对象"。
    if seq is not None and seq == pseq and pts and ts <= pts:
        return False, "序号一动没动(同为 %s)且时刻未推进(前=%s 后=%s) ⇒ 还是同一条记录" % (seq, pts, ts)
    if not_before:
        nb = str(not_before).strip()[:19]
        if ts < nb:
            return False, "最新记录 %s 早于本次触发 %s → 是旧记录(未新增)" % (ts, nb)
    if pts and ts <= pts:
        return False, "时刻未推进(前=%s 后=%s)" % (pts, ts)
    return True, ("前=(无记录) 后=%s" % ts) if not pts else ("前=%s 后=%s" % (pts, ts))


# ==================== 3-1『最多12费率』: 费率参数读写 + 三条取表路径(2026-09-10 建) ====================
# 判据源 = project/knowledge/_whitebox_ledger/ledger.md 3-1「观察与判据」: ②各费率时段归属=所设费率号、无错位;
#   ③不同日期类型取表正确(节假日/周休日/普通日); ④nRateNum>12 被 :184 拦→兜底、12 上限生效。
#
# ---- 判据④的实测结论(2026-09-10 探针 log/probe_rate_num_*.log; 一处静态推断被推翻) ----
#   写 12 → 645 回 0x94 且读回 12(受理); 写 13 → 645 回 0xD4 错误标志 **0x40**、698 回 DAR=19(DAR_OverRate)。
#   ⇒ 两道写入口各自先把 >C_RateNum 拒了(DLT645App.c:1750 / DLT698App.c:10458-10473),
#     于是 `g_RatePara[nRateNum] > 12` 这个**状态协议侧不可达** ⇒ 判据④拆两半:
#       ④a「12 上限生效 / 13 被拒」= 帧路可测(rate_num_limit_evidence 直接证);
#       ④b「:184 拦 → :188 兜底」= **帧路造不出**那个状态(不是"证不了") —— 绕开写入口用**注入**
#          造它: rate_fallback_evidence() 走 `Session.with_inject`(停着把 g_RatePara[3] 写成 13),
#          判据断点取兜底支与正常支都会走到的 :190, 读回整 8 字段 == TAB_RatePara 默认即证兜底执行过。
#          ⚠ 2026-09-11 校正: 这里原先写着『声明 unprovable … swdbg 有意不提供写内存』—— 那是把
#          **本库自己的收窄设计说成了台面事实**(只读只是 `probe.py` 自己的边界, 从不是全库规矩)。
#          纪律「先证能力再改判据」在这里被用反了: 该先问"库能不能给这条通道", 而不是拿库的现状
#          去宣布台面不可证。**"帧路造不出" ≠ "证不了"**。
#   🔴 **645 错误标志是位图, 不是枚举值**: DLT645Link.c:519 `pFrame[DAT0] = 0x01 << comSta`
#     ⇒ ER_RATE(=6) 线上是 **0x40**。拿线上字节去比 ER_* 常量会恒判错。
#   ⚠ 645 写值 = **8B 受控前缀 `02 | 00×7` + 真值**(LEN = 4B DI + 值 ⇒ `datLen = LEN-12` = 真值字节数);
#     而**读侧值紧接 DI 回显之后**(&pFrame[DAT0]) ⇒ 读写偏移**不对称**, 别搞混。
#
# ---- 字节序(逐条核定过源码, 改本节前先照这四条) ----
#   ① 公共假日单条 值 4B = `[时段表号, 日, 月, 年]`(全 00 / 全 FF = **擦除**): 写侧 `Check_Date(&pFrame[DAT9])`,
#      而 Platform/DateTime.c 的 Check_Date 读 pDate[0]=日 / pDate[1]=月 / pDate[2]=年;
#      计算侧 TaskRate.c:201-203 用 temp[1]↔EM_Day / temp[2]↔EM_Month / temp[3]↔EM_Year 对上 —— 两侧一致。
#   ② 公共假日 DI = `040300` + `%02X`%条目号: 内层 key 0x0300 取 `DI1|DI2<<8`, **条目号落在 DI0**(最低组)
#      ⇒ 人类串的**末字节**才是条目号(写侧 :2847 `pFrame[DI0]`、读侧 :6295 同为证)。
#   ③ 时段表条目 数据域 = 时/分/费率号(见 3-2 段头注④); 费率号必须 1..C_RateNum, 校验在
#      `Sort_ZoneSlotTab` TaskRate.c:555, 且 `num>14` 直接 FALSE ⇒ **12 段可种、每段费率可到 12**。
#   ④ C_RateNum = **12**(UserCfg.h:290, VER_20Edit 分支在 :15 打开)。⚠ 3-2 段头注引的 ":288 = 4" 是
#      `#ifndef VER_20Edit` 那一支 —— 行号错、值也错(本项实测: 写 12 受理、写 13 被拒)。那份
#      `RATE_NUM = 4` 只当"预置时换个有效费率号"的模数用, 行为无害, 此处按真值 12 记。
#
# ---- 判据②③**为什么不切套预置**(2026-09-10 实读本表后有意这么选) ----
#   ② 要的是「算出来的」对上「配下去的」。当前套的时段表**读得到**(698 401602xx), 本表 8 段、
#   费率 1-4 循环 ⇒ **逐段拨钟对拍就够** —— 不改表、不切套, 破坏面最小(切套那套 3-2 已覆盖)。
#   注: 本表 8 张时段表内容全同 ⇒ "取表正确"在 ③ 只能从 `g_ListNo`(取到第几号表)观察 ——
#   而那正是规格点名要 Watch 的量, 也正是该判据的可观测面。
#   ③ 要分辨三条取表路径, 只需**直接写**这三个参量(写侧直落 RAM/EEPROM, **不受套限制**):
#   公共假日条目与假日数、周休日特征字、周休使用时段表表号 —— 写完都 `Post_Message(ID_TaskRate,MSG_MinStep)`
#   ⇒ **立即重算**。三步互斥地只留一条路能给"表号2":
#     ③a 特征字=0x7F(无周休) + 假日数=1(条目表号=2)   ⇒ 只有**节假日**路径能给 2
#     ③b 假日数=0 + 特征字=0x00(天天周休) + 周休表号=2 ⇒ 只有**周休**路径能给 2
#     ③c 假日数=0 + 特征字=0x7F                        ⇒ 只剩**时区表**路径(本表 [(1,1,1)] ⇒ 1)
#   ⚠ 本表**周休日特征字出厂 = 0x7F ⇒ 周休路径永不触发**; 不种特征字, ③ 会假通过(以为测了, 该分支一次没走)。
#     同一个坑: 公共假日数出厂 = 0 ⇒ 节假日路径同样从不执行。
#
# ---- 触发重算(②逐段拨钟的动作) ----
#   Run_TaskRate 只在 `Is_Message(msg,MSG_MinStep)` 且 `Check_DateTime(pGet_Time(EM_Sec))` 时重算
#   (TaskRate.c:73)⇒ **秒必须有效**; 而 645 每个费率参量写入口写完都 `Post_Message(ID_TaskRate,MSG_MinStep)`
#   ⇒ **等值写费率数 = 一个确定性的"立刻重算"按钮**, 不必等自然分节拍(约 1 次/分)。
#   `g_ListNo`/`g_SoltNo` 在 Calculate_RateNo 里**无条件**赋值(:258/:264) ⇒ 重算过就一定是最新的。
#   拨钟: 698 Set 40000200 只认 →计量芯, 管理芯 **<1.5s 跟上**(2026-09-10 实测) ⇒ 每段=拨钟→等→重算→读。
#
# ---- 断点观测:**规格里原来的断点 :178 是不成立的断点, 已后移**(核对断点依据 gdb `info line` / `info scope`) ----
#   :178 → `Calculate_RateNo+4` = 函数**首条可执行语句**(Get_MeterTime 调用), 那时 curTime 还没填
#     ⇒ 与 3-2 的 :307、4-6 的 :625/:842/:119 同一类"停得住、变量也是栈垃圾"的假通过。**弃用**。
#   :274 → 注释行, gdb 说 "contains no code"。**弃用**。
#   ⇒ 改用这一对(同函数相邻两句, 只占 2 个断点槽; Cortex-M0 共 4 槽):
#     断[A] TaskRate.c:85 = `rateNo = Calculate_RateNo()` 调用点(+96) ⇒ 读 `backup[4]`
#        = 重算**前**的 [g_SlotSwNo, g_ListNo, g_SoltNo, g_RateNo[0]](:75-78 填的)。
#     断[B] TaskRate.c:86 = `if (rateNo != g_RateNo[0])`(+98) ⇒ 读 `rateNo` = **计算体的返回值**
#        (上面那条注释说"只占 2 项"是按 3-2 的写法; 这一对互相印证: 前=旧、后=新)。
#   ⚠ `msg`(:62) 不能读: `info scope` 实测它只在 0x21e6e-0x21e72 活, 到 :85(0x21eba) 早已出区间。
#   ⚠ `listNo`(:176) 也不能在一次里读: 它最后活区间 0x2227e-0x2228a, 与 `rateNo` 的 0x222ac+
#     **不相交** ⇒ 想同时看"取了哪张表"与"算出哪个费率"**必须**走全局镜像 `g_ListNo`/`g_SoltNo`
#     (AA80 路) —— 这正说明两种观测读的是同一个值的两条通道, 不是两套判据。
#   断[A]/断[B] 都**只在重算那一拍**命中(等值写费率数触发), 不占自然分节拍 —— 每段拨钟后开一次。

C_RATENUM_MAX = P.C_RATENUM_MAX

ERRFLAG_NAME = {0: "其他错误", 1: "无请求数据", 2: "密码错/未授权", 3: "通信速率不能更改",
                4: "年时区数超限", 5: "日时段数超限", 6: "费率数超限", 7: "保留"}


def errflag_of(note):
    """645 0xD4 应答 note → 错误标志 int(**位图**) 或 None。"""
    m = re.search(r"err=0x([0-9A-Fa-f]{2})", note or "")
    return int(m.group(1), 16) if m else None


def errflag_name(fl):
    """错误标志位图 → 人话。**多位同时置位不猜**(那种值本仓没见过, 编不出名字)。"""
    if fl is None:
        return "(未解出)"
    if fl == 0:
        return "无标志"
    if fl & (fl - 1):
        return "多位同置=0x%02X, 不猜" % fl
    b = fl.bit_length() - 1
    return "ER_%d(%s)" % (b, ERRFLAG_NAME.get(b, "保留"))


# 8 个费率参量: key → (645 DI 人类串, 写值真值字节数, 范围, 名称)。范围取自**写入口自己的判定**
# (DLT645App.c:1695-1800 / :2087-2117)。
# ⚠ **写入口域 ≠ 计算体守卫域** —— 别再把这两者当同一套口径(旧注释就是这么写的, 已按实测改):
#   `noWeekDay` 两个写入口(645:2100 / 698:10550)**都明写 `|| (值==0xFF)` 收 0xFF**, 而计算体
#   `TaskRate.c:186` 的守卫是 `(==0) || (>8)` ⇒ **拒 0xFF**。两者不合 ⇒ 见 `_RATE_PARA_EXTRA`。
RATE_PARA_FIELDS = [
    ("zone",      "04000201", 1, (1, 14),   "年时区数"),
    ("list",      "04000202", 1, (1, 8),    "日时段表数"),
    ("slot",      "04000203", 1, (1, 14),   "日时段数"),
    ("rate",      "04000204", 1, (1, 12),   "费率数(上限 C_RateNum=12)"),
    ("holiday",   "04000205", 2, (0, 9),    "公共假日数"),
    ("step",      "04000207", 1, (0, 7),    "阶梯数(非本地表 ⇒ ER_D0D1, 本表非本地 ⇒ 死)"),
    ("zWeekDay",  "04000801", 1, (0, 0xFF), "周休日特征字(**位图**: 置位=工作日, 清位=周休日)"),
    ("noWeekDay", "04000802", 1, (1, 8),    "周休使用时段表表号(1..8; **0xFF=无, 写入口收而守卫拒** —— 见 _RATE_PARA_EXTRA)"),
]
RATE_PARA_DI = {k: di for k, di, _nb, _rg, _nm in RATE_PARA_FIELDS}
_RATE_PARA_NB = {k: nb for k, _di, nb, _rg, _nm in RATE_PARA_FIELDS}
_RATE_PARA_RANGE = {k: rg for k, _di, _nb, rg, _nm in RATE_PARA_FIELDS}
_RATE_PARA_NAME = {k: nm for k, _di, _nb, _rg, nm in RATE_PARA_FIELDS}
# 例外: 编/解码**不走 BCD** 的字段。其余数值字段写侧收 BCD、读回也是 BCD。
#   · `zWeekDay` = 位图, 本就不该 BCD(0x7F 曾被编成 0x27, 见 rate_val 的 ⚠); 写读两侧都原样。
#   · `noWeekDay` = **写侧原样、读侧 BCD** —— 固件两侧不对称(2026-09-10 探针实测):
#       写 `DLT645App.c:2099-2101` 直接拿 `pFrame[DAT8]` 比 `1..8 || ==0xFF`、`pFrame[DAT16]=pFrame[DAT8]`
#       原样搬(对照同处 `case 0x00020401` 费率数走的是 `BCD_HEX`) ⇒ **0xFF 存得进去**;
#       读 `DLT645App.c:5685-5690` 却是 `pFrame[DAT0] = HEX_BCD(pFrame[DAT7])` ⇒ **按十进制转换**,
#       而 `HEX_BCD` 是 `Common.c:35` 那个 `hex %= 100` 的**静默截位**版 ⇒ **写 0xFF、读回 0x55**。
#     归 RAW 仅为让 0xFF **编得出**(bcd8 会抛); 解码侧两法同值 —— 1..8 时原样与 BCD 同形,
#     0xFF 时 RAW 给 85、BCD 解 0x55 也给 85 ⇒ **读回数值不受此归类影响**(实测 85 就是表报的)。
RATE_PARA_RAW = ("zWeekDay", "noWeekDay")
# 例外: **写入口受理但不在"主域"里的散值**。`noWeekDay` 的 0xFF 是标准里"周休日不使用时段表"义,
# 645:2100 / 698:10550 两个写入口都专门写了 `|| (值==0xFF)` 收它 ⇒ 工具必须发得出去(旧 range=(1,8)
# 把它挡在判定外, 于是"这条固件缺陷从我的工具里根本发不出来", 3-1 的 ④b 就被误记成"协议侧造不出")。
# ⚠ 发 0xFF 之后**别指望读回 0xFF**: 计算体守卫每轮把它判越界、`:188` 把整 8 字段 `Copy_Data` 成
#   `TAB_RatePara` 默认 ⇒ 镜像(g_RatePara / Get_RatePara 给的数组长度)变默认, 而 EEPROM(对外读)
#   仍是 0xFF —— **读写分叉**(2026-09-11 实探)。
_RATE_PARA_EXTRA = {"noWeekDay": (0xFF,)}
# ⚠ 公共假日数范围写 (0,9) 而非规格说的 0..50: 写入口的判定是
#   `(0x00 == pFrame[DAT9]) && ((pFrame[DAT8]&0x0F) <= 0x09)` ⇒ **高位字节必须为 0**, 线上只认个位。
#   0..50 是**表内容量**(LEN_Holiday/4 = 50 条), 不是"假日数"字段能取的数。本表只证到 9;
#   要往 10 以上写, 先回源码核那个判定再说 —— 别按 0..50 硬发(会白花一帧还被拒)。
# `g_RatePara` 镜像是**二进制/HEX 形态**(不是 BCD): TaskRate.c:180-186 直接拿它与 14/8/12 这些
# **十进制常数**比大小, 中间不带 BCD 转换。实测 `01 02 08 04 00 00 7F 01` 对上
# 年时区1/日时段表2/日时段8/费率4/假日0/阶梯0/特征字0x7F/周休表号1 —— 与 645 读回的 BCD 值互证。
_RP_ORDER = ("zone", "list", "slot", "rate", "holiday", "step", "zWeekDay", "noWeekDay")


def bcd8(n):
    """n(0..99) → 1B BCD。固件写侧 `Is_nBCD` 拒非 BCD、用 `BCD_HEX` 解。"""
    if not (0 <= n <= 99):
        raise ValueError("bcd8 只吃 0..99, 实为 %r(超了就是**十进制位数超一个字节**, 别静默截)" % (n,))
    return ((n // 10) << 4) | (n % 10)


def rate_val(n, nb=1, key=None):
    """费率参量值 → 645 写值 hex = 8B 受控前缀 + nb B 真值(**低位在前**)。

    **位图字段**(`RATE_PARA_RAW`, 如周休日特征字)**原样直出**, 其余数值字段按 BCD 编。
    ⚠ 2026-09-10 实踩(这颗雷很贵): 写侧原先**一律 BCD**, 于是想写 0x7F 的位图被
    `bcd8((0x7F // 1) % 100)` = `bcd8(27)` 编成 **0x27**(0b00100111) —— 把"七天全是工作日"改成了
    "周日/周一/周二/周五工作日"。而**读侧 `dec_rate_val` 本来就有 RAW 分支**、读回 0x27 也自洽,
    于是脚本一路跑到 ③c 才以「期望表号 1 实得 2」的形式暴露(周四被当成周休日 ⇒ 走了周休表),
    归因极难: `bcd8` 的 `% 100` 把一个越界值**静默截位**, 而 0x7F 恰好在范围判定 (0,0xFF) 之内。
    修法: 写侧认同一个 `RATE_PARA_RAW`, 且越界一律**抛**不截(见 `bcd8` 的新判定)。
    """
    if key in RATE_PARA_RAW:
        if not (0 <= int(n) <= 0xFF):
            raise ValueError("%s 是位图, 值须 0..0xFF, 实为 %r" % (key, n))
        return (_SWSET_PREFIX + bytes([int(n) & 0xFF])).hex()
    if not (0 <= int(n) < 100 ** nb):
        raise ValueError("%s=%r 超出 %d 字节 BCD 能表示的范围(0..%d)" % (key, n, nb, 100 ** nb - 1))
    return (_SWSET_PREFIX + bytes(bcd8((n // (100 ** i)) % 100) for i in range(nb))).hex()


def dec_rate_val(key, val, nb):
    """费率参量 645 读回值 → int; 非 BCD 或长度不够回 None(**不猜**)。特征字位图原样解。"""
    if not val or len(val) < nb:
        return None
    b = val[:nb]
    if key in RATE_PARA_RAW:
        return b[0]
    n = 0
    for i in range(nb):
        hi, lo = b[i] >> 4, b[i] & 0x0F
        if hi > 9 or lo > 9:
            return None          # 非 BCD ⇒ 不猜(拿它当数用会把 0x7F 读成 7)
        n += (hi * 10 + lo) * (100 ** i)
    return n


def read_rate_param(ser, key, wait=2.0, quiet=False):
    """645 读**一个**费率参量 → int 或 None。本函数自打印(quiet=True 只静默)。"""
    di, nb = RATE_PARA_DI[key], _RATE_PARA_NB[key]
    v = read_param_di(ser, di, wait=wait, quiet=quiet,
                      head="读费率参量 %s(%s, DI %s)" % (_RATE_PARA_NAME[key], key, di))
    return dec_rate_val(key, v, nb)


def read_rate_para(ser, wait=2.0, quiet=False):
    """645 读**全部 8 个**费率参量 → {key: int|None}。本函数自打印(逐条)。"""
    return {k: read_rate_param(ser, k, wait=wait, quiet=quiet) for k, _di, _nb, _rg, _nm in RATE_PARA_FIELDS}


def write_rate_param(ser, key, n, wait=2.5, quiet=False):
    """645 受控写一个费率参量(需 enter_factory) → (verdict, note)。本函数自打印。
    ⚠ 每个费率写入口**成功后都 Post_Message(ID_TaskRate,MSG_MinStep)** ⇒ 本调用顺带 = 确定性重算触发。
    越界**不下发**(写入口自己也会拒, 但别为此白花一帧且多落一条记录)。
    ⚠ 判定的**唯一来源是写入口**, 不是计算体守卫 —— 两者在 `noWeekDay` 上不合(`_RATE_PARA_EXTRA`)。"""
    lo, hi = _RATE_PARA_RANGE[key]
    extra = _RATE_PARA_EXTRA.get(key, ())
    if not (lo <= n <= hi or n in extra):
        return "FAIL", "越界不下发: %s=%d 不在 %d..%d%s" % (
            key, n, lo, hi, (" ∪ %s" % (list(extra),)) if extra else "")
    di, nb = RATE_PARA_DI[key], _RATE_PARA_NB[key]
    return write_param_di(ser, di, rate_val(n, nb, key=key), wait=wait, quiet=quiet, tag="wr_rate_" + key,
                          head="写费率参量 %s(%s) = %d" % (_RATE_PARA_NAME[key], key, n))


def holiday_di(item):
    """公共假日第 item 条(1..LEN_Holiday/4) → 645 DI 人类串。**条目号在最低组** ⇒ 人类串的末字节。"""
    return "040300%02X" % int(item)


def read_holiday(ser, item, wait=2.0, quiet=False):
    """645 读公共假日第 item 条 → {"raw":…, "val":(表号,日,月,年)|None, "empty":bool}。
    读侧 `nHEX_nBCD` 把表内 HEX 转回 BCD(DLT645App.c:6301-6306) ⇒ **线上是 BCD**。
    非 BCD 就 val=None(不猜); 全 00/全 FF = 空条目(empty=True)。"""
    v = read_param_di(ser, holiday_di(item), wait=wait, quiet=quiet,
                      head="读公共假日第%d条" % item)
    b = (v or b"")[:4]
    out = {"raw": b.hex(" ").upper() if b else "", "val": None, "empty": False}
    if len(b) < 4:
        return out
    if b == b"\x00" * 4 or b == b"\xff" * 4:
        out["empty"] = True
        return out
    if any((x >> 4) > 9 or (x & 0x0F) > 9 for x in b):
        return out               # 非 BCD ⇒ val 留 None
    out["val"] = (b[0], b[1], b[2], b[3])        # (表号, 日, 月, 年) —— 顺序依据见段头注①
    return out


def write_holiday(ser, item, tno, day, month, year, wait=2.5, quiet=False):
    """645 写公共假日第 item 条 = (时段表号, 日, 月, 年) BCD → (verdict, note)。写后固件自动重算。
    ⚠ 副作用: 写侧 `Recd_PrgHoliday` 会落一条**编程记录**(与 3-2 写切换设定同性质)。"""
    return write_param_di(ser, holiday_di(item),
                          (_SWSET_PREFIX + bytes(bcd8(x) for x in (tno, day, month, year))).hex(),
                          wait=wait, quiet=quiet, tag="wr_holiday_%d" % item,
                          head="写公共假日第%d条 = 表号%d 日%d 月%d 年%d"
                               % (item, tno, day, month, year))


def erase_holiday(ser, item, wait=2.5, quiet=False):
    """645 擦除公共假日第 item 条(值写全 00 —— 固件 :2853 `Comp_Value(...,0x00,4)` 即擦除支)。"""
    return write_param_di(ser, holiday_di(item), (_SWSET_PREFIX + b"\x00" * 4).hex(),
                          wait=wait, quiet=quiet, tag="erase_holiday_%d" % item,
                          head="擦除公共假日第%d条(写全00)" % item)


def rate_trace_decode(rv):
    """AA80 那次读回的**原始字节快照** → dict。**读由调用方做**(脚本里那几行裸 `watch.watch_vars`),
    这里只解码; 传进来的快照缺项/为 None, 对应的键就是 None(不拿 0 冒充, 免与"真值就是 0"混)。
    键: rate(g_RateNo[0]) / list(g_ListNo) / solt(g_SoltNo) / para(8 字段 dict) / zone_sw / slot_sw。
    为什么读**全局**而不是在一次里读 `listNo` 局部量: 见段头「断点观测」那两条 ⚠ —— `listNo` 与
    `rateNo` 的 DWARF 活跃区间**不相交**, 一次读不到两者; 而这两个全局就是 :258/:264 无条件赋的镜像。"""
    rv = rv or {}
    out = {"rate": None, "list": None, "solt": None, "para": None, "zone_sw": None, "slot_sw": None}
    b = rv.get("g_RateNo")
    if b:
        out["rate"] = b[0]           # [0]=费率号, [1][2]=Fetch_CRC(不是费率的一部分)
    for src, dst in (("g_ListNo", "list"), ("g_SoltNo", "solt"),
                     ("g_ZoneSwNo", "zone_sw"), ("g_SlotSwNo", "slot_sw")):
        b = rv.get(src)
        if b:
            out[dst] = b[0]
    p = rv.get("g_RatePara")
    if p and len(p) >= 8:
        out["para"] = dict(zip(_RP_ORDER, p[:8]))
    return out


def force_rate_recalc(ser, rate_n, wait=2.5, quiet=False):
    """确定性触发一次费率重算: **等值写费率数** ⇒ 固件 `Post_Message(ID_TaskRate, MSG_MinStep)`。
    为什么不用自然分节拍: Run_TaskRate 约 1 次/分才进一次(TaskRate.c:74) ⇒ 8 段要等 ~8 分钟;
    而写入口自带的那条 Post_Message 是**同一条消息**, 只是时机由我们掌握。"""
    return write_rate_param(ser, "rate", rate_n, wait=wait, quiet=quiet)


def rate_at(ser, when, rate_n, settle=1.6, wait=2.5, trig=None, bp=None, *, read_trace):
    """拨钟到 when → 等管理芯跟钟 → 强制重算 → 读全局镜像 → dict(trace)。
    带会话时: 那一次"重算"**走断点动作**(`trig`), 于是同一拍里既拿到串口的写应答、又拿到固件内
    `rateNo`/`backup` 的读数 —— 两种观测共用这一拍, 不是跑两遍。

    `bp` 的两种写法(breakpoint 的 `with_trigger` 两种都收, 差别在断点**活多久**):
      · `{"at": <断点字面量, 四种写法见 _bpok>, "vars": (…)}` —— **②③ 这种要连打 8 次以上的一律用这种**:
        breakpoint **当场挂、用完必撤**(命中撤、没命中补撤), 断点一次只活一次。
      · `{"no": bpno, "vars": (…)}` —— 调用方**自己**预先挂好的断点(为跨次复用)。
        ⚠ 断点横跨"没有轮询在管"的那几秒时, 它**自己会命中**并把核心撂停, 此后每条 645/698 帧
        都收不到回(2026-09-10 实测 `TaskRate.c:85` 高频行: 挂上什么都不干, 第 5 秒就停;
        3-1 首跑整轮哑掉即此)。**能用 `at` 就别用 `no`。**

    `read_trace` = **调用方给的 AA80 读原语**(`(ser, tag=…)` → 那次读回的原始字节快照): 读在脚本
    (裸 `watch.watch_vars`), 解码在 `rate_trace_decode`。
    """
    set_meter_clock_set(ser, when, chip="计量芯", wait=wait)
    time.sleep(settle)                     # 管理芯跟主钟实测 <1.5s; 给 1.6s 余量
    b = bp or {}
    at = b.get("at") or b.get("no")        # `at`=(文件,行号) → 由 breakpoint 当场挂/用完撤
    r = (trig or _direct_trigger)(at, force_rate_recalc, ser, rate_n,
                                  wait=wait, timeout=(wait * 4 + 10), _vars=b.get("vars") or (),
                                  _drop=bool(b.get("at")))
    return rate_trace_decode(read_trace(ser, tag="拨钟到 %s 后" % when)), r


def seg_midpoints(entries):
    """时段表(升序 [(时,分,费率号)]) → [(段号(1起), 'HH:MM:SS', 该段费率号)] —— 每段取**中点**时刻。
    取中点而非段首: 段首与上一段边界同刻, 边界归属规则(:267 用 `>=`)会让"算哪一段"变成对实现的
    猜测; 取中点则**任何**合理实现都落在本段内 ⇒ 判据测的是归属, 不夹带边界约定。"""
    out = []
    for i, (h, mi, r) in enumerate(entries):
        start = h * 60 + mi
        end = (entries[i + 1][0] * 60 + entries[i + 1][1]) if i + 1 < len(entries) else 24 * 60
        mid = (start + end) // 2 if end > start else start
        out.append((i + 1, "%02d:%02d:00" % (mid // 60, mid % 60), r))
    return out


def active_slot_no(ser, month, day, chip=None, wait=3.0):
    """本表当前生效的**时段表号** = 时区表(当前套)按 (月,日) 选出的那条 → (表号|None, 说明)。
    同时**就是"普通日路径"的期望值**(判据③c) —— 所以它不从常量来, 从表上读。"""
    zt = zone_tab_whole_parse(read_tab_whole(ser, "zone", "cur", chip=chip, wait=wait))
    if zt is None:
        return None, "当前套时区表读回形态不符, 无法判定生效表号"
    no = which_slot_at(zt, month, day)
    return no, "当前套时区表 %s ⇒ (%d,%d) 生效表号=%s" % (zt, month, day, no)


def ymd_of(date_str):
    """'YYYY-MM-DD' → (日, 月, 年%100)。**顺序与公共假日值的字节序一致**(段头注①)。"""
    y, m, d = (int(x) for x in date_str.split("-"))
    return d, m, y % 100


def rate_num_limit_evidence(ser, orig_rate, wait=2.5):
    """判据④a 的证据: 12 上限生效 + 13 被**两条独立写入口**拒。返回 (recs, lines)。
    只认领 ④a; 返回后**费率数已写回 orig_rate**。"""
    recs, lines = [], []
    # ① 上边界 12 必须受理 —— 这一半不能省: 只测"13 被拒"的话, 一个"任何值都拒"的坏固件照样过。
    v12, n12 = write_rate_param(ser, "rate", C_RATENUM_MAX, wait=wait)
    back12 = read_rate_param(ser, "rate", wait=wait)
    ok12 = (v12 == "PASS") and (back12 == C_RATENUM_MAX)
    lines.append("写 %d: %s 读回=%s %s" % (C_RATENUM_MAX, v12, back12,
                                          "✓ 受理且落库" if ok12 else "✗ 未落库"))
    # ② 越界 13: 645 侧 —— ⚠ **必须走载体 `write_param_di` 把帧真发出去**。
    #    `write_rate_param` 自带越界守卫(「越界不下发」), 对正常调用是好习惯, 但在这里它会让
    #    **我们自己**把帧拦下 ⇒ 回执里没有固件的错误标志 ⇒ `errflag_of` 回 None ⇒ ④a 恒 FAIL。
    #    本句要证的恰恰是"**固件的写入口自己拦不拦**"(DLT645App.c:1750 的 C_RateNum 判定), 故绕开守卫。
    #    (2026-09-10 实踩: 首跑 ④a 就这么假 FAIL 的, 报「错误标志=0x00 (未解出)」。)
    v13, n13 = write_param_di(ser, RATE_PARA_DI["rate"], rate_val(13, 1, key="rate"),
                              wait=wait, tag="wr_ratenum_13")
    fl = errflag_of(n13)
    ok13c = (v13 == "FAIL") and (fl == (1 << 6))
    lines.append("645 写 13: %s errflag=0x%02X %s [回执 %s] ⇒ %s" % (
        v13, fl or 0, errflag_name(fl), n13 or "",
        "拒且理由是费率数超限" if ok13c else "**与期望不符**"))
    back13 = read_rate_param(ser, "rate", wait=wait)
    ok13b = back13 == C_RATENUM_MAX
    lines.append("写 13 后读回=%s %s" % (back13, "✓ 13 未落库" if ok13b else "✗ 13 落库了!"))
    # ③ 越界 13: 698 侧(第二条独立判定)
    v698, n698, dar698 = write_oad_ud(ser, "400C0204", bytes([0x11, 13]), wait=wait, tag="wr698_ratenum_13")
    ok698 = dar698 == 19          # DAR_OverRate
    lines.append("698 写 13: %s DAR=%s %s ⇒ %s" % (
        v698, dar698, "DAR_OverRate" if ok698 else "", "与 645 同一道判定(拒)" if ok698 else "**与 645 不一致, 要核**"))
    recs.append(rec("费率数上限 12 生效(写12受理落库、写13被两路写入口拒)",
                    ok12 and ok13c and ok13b and ok698,
                    "; ".join(lines), crit="④a",
                    falsify="上限不是 12(写 12 被拒) / 写 13 被受理并落库 ⇒ 第 13 费率会生效"))
    # ---- 写回原值 ----
    vr, nr = write_rate_param(ser, "rate", orig_rate, wait=wait)
    got = read_rate_param(ser, "rate", wait=wait)
    lines.append("还原费率数 %s → %s 读回=%s %s" % (orig_rate, vr, got,
                                                 "✓ 无净变" if got == orig_rate else "✗ 需人工核!"))
    if got != orig_rate:
        recs.append(rec("费率数还原为原值 %s" % orig_rate, False, "读回=%s" % got, crit=None))
    return recs, lines


def rate_attribution_evidence(ser, entries, list_exp, date, rate_n, settle=1.6, wait=2.5,
                              trig=None, bp=None, *, read_trace):
    """判据② 的证据: 对当前套时段表**逐段**取段中点拨钟 → 重算 → 对比 rate/solt/list。返回 (recs, lines)。
    `read_trace` = 调用方给的 AA80 读原语(读在脚本, 解码在 `rate_trace_decode`), 逐段透传给 `rate_at`。
    一条 rec 认领 ②(串口观测); 若带会话, 每一拍的 `rateNo` 另出一条 obs=DEBUG 的 ② 记录 —— 两条通道
    各自递证据, 判据仍然是**那一条**(见 common/judge.py 的 rec 契约)。"""
    recs, lines = [], []
    exp = seg_midpoints(entries)
    rows, dbg = [], []
    for idx, hhmmss, want_rate in exp:
        tr, r = rate_at(ser, "%s %s" % (date, hhmmss), rate_n, settle=settle, wait=wait, trig=trig,
                        bp=bp, read_trace=read_trace)
        got = (tr["rate"], tr["solt"], tr["list"])
        # ⚠ 三态: `rate_trace_decode` 读不出来就是 `None`(见它的 docstring), 拿 `None` 去 `==` 会当场
        #   变 False ⇒ **一次读取失败被记成"错位/错费率"**。与下一条 `if dbg or …` 那个 DEBUG 记录
        #   同一个道理(那边作者已经写对了: "有会话但一拍都没读出 ⇒ ok=None, 绝不当 FAIL")。
        unread = [n for n, v in (("费率", tr["rate"]), ("段号", tr["solt"]), ("表号", tr["list"]))
                  if v is None]
        ok = judge.tri_eq((tr["rate"], want_rate), (tr["solt"], idx), (tr["list"], list_exp))
        rows.append(ok)
        lines.append("  第%d段 @%s 期望(费率%d 段号%d 表号%d) 实得%s %s"
                     % (idx, hhmmss, want_rate, idx, list_exp,
                        "(%s, %s, %s)" % got,
                        "**读不回来**(%s 为 None) ⇒ 这一拍没做成" % "/".join(unread) if unread
                        else "✓" if ok else "✗ 错位/错费率"))
        if bp is not None:
            # 注意: bp is None(无会话)时这一次是 `_direct_trigger` 发的, 没有任何白盒读数
            # ⇒ 不打印"未命中"那行(否则黑盒跑法会平白多出 8 行噪音, 让人以为断点观测"试过了")。
            hit, wv = _wbline("断[B] @%s" % hhmmss, r, hit_expected=False)
            if hit is not None and wv.get("rateNo") is not None:
                got_dbg = gdb_ints(wv["rateNo"])
                if got_dbg:
                    dbg.append(got_dbg[0] == want_rate)
                    lines.append("     断点读 rateNo=%s(期望 %d) %s"
                                 % (got_dbg[0], want_rate, "✓" if got_dbg[0] == want_rate else "✗"))
    # 汇总也是三态(原写法 `bool(rows) and all(rows)` 有两个坑: 空表直接判 FAIL; 有段没读到也判 FAIL):
    #   · 有段**错位/错费率**   ⇒ False(那才是固件错)
    #   · 一段没错、但有段没读到 ⇒ None(**"每段都对"证不了**, 不是"不对")
    #   · 一段都没有            ⇒ None(什么都没判, 不是通过)
    _done = [x for x in rows if x is not None]
    _nfail, _nunread = sum(1 for x in _done if not x), len(rows) - len(_done)
    ok_all = judge.tri_all(rows)
    recs.append(rec("逐段归属: 每段的 g_RateNo/g_SoltNo/g_ListNo == 所配",
                    ok_all, "共 %d 段(%d 段读不回来), %d 段相符 | %s"
                    % (len(rows), _nunread, sum(1 for x in _done if x), "; ".join(lines)),
                    crit="②",
                    falsify="时段表算出来的费率号与所配不符(错费率) / 段号与所在段不符(错位) ⇒ 被判 FAIL"))
    if dbg or bp is not None:
        # 有会话但一拍都没读出 rateNo ⇒ ok=None(没做成, 绝不当 FAIL); 读到了就按相符与否判。
        recs.append(rec("断点观测: Calculate_RateNo 的返回值 == 该段所配费率号",
                        all(dbg) if dbg else None,
                        ("%d/%d 拍相符" % (sum(dbg), len(dbg))) if dbg
                        else "有会话但一拍都没读回 rateNo(变量未命中/断点未命中)",
                        crit="②", obs=judge.DEBUG,
                        falsify="计算体返回的费率号与所配不符 ⇒ 被判 FAIL"))
    return recs, lines


def date_type_evidence(ser, base_when, rate_n, list_exp_normal, settle=1.6, wait=2.5, *, read_trace):
    """判据③ 的证据: 三条取表路径互斥分辨。返回 (recs, lines)。
    期望值: ③a 节假日→2 / ③b 周休→2 / ③c 普通日→list_exp_normal(从表上读的时区表选号)。
    末尾把写进去的东西**全部还原**(假日条目/假日数/特征字/周休表号)。
    `read_trace` = 调用方给的 AA80 读原语(读在脚本, 解码在 `rate_trace_decode`), 三条路径各读一次。"""
    recs, lines = [], []
    d, m, y = ymd_of(base_when[:10])
    # ---- 存原值(还原用) ----
    o_item = read_holiday(ser, 1, wait=wait)
    o_cnt = read_rate_param(ser, "holiday", wait=wait)
    o_zw = read_rate_param(ser, "zWeekDay", wait=wait)
    o_nw = read_rate_param(ser, "noWeekDay", wait=wait)
    lines.append("原值: 假日1=%s 假日数=%s 特征字=%s 周休表号=%s"
                 % (o_item["val"] if o_item["val"] else ("空" if o_item["empty"] else o_item["raw"]),
                    o_cnt, o_zw, o_nw))
    set_meter_clock_set(ser, base_when, chip="计量芯", wait=wait)
    time.sleep(settle)
    # ---- ③a 节假日路径: 只有它能给出表号 2(特征字=0x7F ⇒ 周休路径关着) ----
    def _step(name, crit, want, setup, falsify):
        for fn in setup:
            fn()
        force_rate_recalc(ser, rate_n, wait=wait, quiet=True)
        tr = rate_trace_decode(read_trace(ser, tag=name))
        ok = tr["list"] == want
        lines.append("%s: g_ListNo=%s(期望%d) g_RateNo=%s g_SoltNo=%s %s"
                     % (name, tr["list"], want, tr["rate"], tr["solt"], "✓" if ok else "✗"))
        recs.append(rec(name, ok, "g_ListNo=%s 期望=%s" % (tr["list"], want), crit=crit, falsify=falsify))
        return tr
    _step("③a 节假日路径(特征字0x7F关掉周休 + 假日数1指表号2)", "③a", 2,
          [lambda: write_holiday(ser, 1, 2, d, m, y, wait=wait, quiet=True),
           lambda: write_rate_param(ser, "holiday", 1, wait=wait, quiet=True),
           lambda: write_rate_param(ser, "zWeekDay", 0x7F, wait=wait, quiet=True)],
          "节假日表被忽略/日期比对不成立 ⇒ 取不到所配表号2(会落到时区表给的表号)")
    # ---- ③b 周休路径: 特征字=0x00(天天周休) + 假日数=0 ⇒ 只有它能给出表号 2 ----
    _step("③b 周休日路径(假日数0 + 特征字0x00 + 周休表号2)", "③b", 2,
          [lambda: erase_holiday(ser, 1, wait=wait, quiet=True),
           lambda: write_rate_param(ser, "holiday", 0, wait=wait, quiet=True),
           lambda: write_rate_param(ser, "zWeekDay", 0x00, wait=wait, quiet=True),
           lambda: write_rate_param(ser, "noWeekDay", 2, wait=wait, quiet=True)],
          "周休日判定不成立/周休表号被忽略 ⇒ 取不到 2(会落到时区表给的表号)")
    # ---- ③c 普通日路径: 特征字=0x7F + 假日数=0 ⇒ 只剩时区表路径 ----
    _step("③c 普通日(时区表)路径(假日数0 + 特征字0x7F)", "③c", list_exp_normal,
          [lambda: write_rate_param(ser, "zWeekDay", 0x7F, wait=wait, quiet=True)],
          "时区表选号不生效/被前两条路径错误覆盖 ⇒ 取不到时区表给的 %s" % list_exp_normal)
    # ---- 还原 ----
    lines.append("还原: 假日1/假日数/特征字/周休表号")
    if o_item["val"]:
        write_holiday(ser, 1, *o_item["val"], wait=wait, quiet=True)
    else:
        erase_holiday(ser, 1, wait=wait, quiet=True)
    for k, v in (("holiday", o_cnt), ("zWeekDay", o_zw), ("noWeekDay", o_nw)):
        if v is not None:
            write_rate_param(ser, k, v, wait=wait, quiet=True)
    back = {k: read_rate_param(ser, k, wait=wait, quiet=True) for k in ("holiday", "zWeekDay", "noWeekDay")}
    b_item = read_holiday(ser, 1, wait=wait, quiet=True)
    same = (back["holiday"] == o_cnt and back["zWeekDay"] == o_zw and back["noWeekDay"] == o_nw
            and b_item["val"] == o_item["val"] and b_item["empty"] == o_item["empty"])
    lines.append("还原复核: 假日1=%s 假日数=%s 特征字=%s 周休表号=%s %s"
                 % (b_item["val"] or b_item["raw"], back["holiday"], back["zWeekDay"], back["noWeekDay"],
                    "✓ 无净变" if same else "✗ 需人工核!"))
    if not same:
        recs.append(rec("③ 预置物还原为原值", False, "复核不一致: %s" % back, crit=None))
    return recs, lines


def _h8(b):
    """8 字节镜像 → 'AA BB …' 可读串(None → 'None')。

    ⚠ **不许假设 `b` 是字节序列** —— 2026-09-11 09:25 实跑: gdb 对 `INT8U[10]`(实为 char 数组)
    **按 C 字符串打印**, 镜像整条读回来的就是**文本**(`"\\001\\002\\b…"`),
    这里 `"%02X" % x for x in b` 当场 `TypeError: %X format: an integer is required, not str`,
    **整趟跑挂掉、不落退出码**(`log/3_1_rate_num_20260911_092503.log`)。

    本函数是**纯打印工具** —— 唯一职责是"把拿到的东西打成人能看的", **绝不能是崩溃点**:
    它一崩, 崩的是整场测试, 而现场只剩一份写到一半的日志。
    拿到非字节序列就 `repr` 原样打出来, 让人**从日志里看见 gdb 到底回了什么形态**。
    """
    if b is None:
        return "None"
    if isinstance(b, (bytes, bytearray)):
        return " ".join("%02X" % x for x in b)
    return repr(b)


# ⚠ 镜像的 8 个字段**逐元素读, 不整条读 `g_RatePara`**(2026-09-11 3-1 实跑踩到):
#   gdb 对 `INT8U[10]`(实为 char 数组)**按 C 字符串打印**, 整条回来的是带转义的**文本**
#   (`"\001\002\b\004\000\000\177\001@\241"`) —— 我按 bytes 用, `_h8` 当场 `TypeError: %X
#   format: an integer is required, not str`。逐元素读到的是标量, gdb 必打成整数。
#   仓里早记过这条(见本文件 `gdb_ints` 上面那段 ⚠: 3-2 的 `swTime` 用同一招)。离线假件
#   喂的是 bytes, 所以假通过到真表才炸 —— 又一条"**假件必须长得像真件**"。
MIRROR8_EXPRS = tuple("g_RatePara[%d]" % i for i in range(8))


def _mirror8_from_gdb(vals, exprs=MIRROR8_EXPRS):
    """gdb 读回的 `g_RatePara[0..7]` 文本 → 8 字节; 任一个读不到 → None(不猜 0)。

    `exprs` 由**调用方(脚本)**给(`VARS_C`)—— "这个断点上读什么"是子项数据, 与"怎么把 gdb 的
    打印解成值"分开(见本文件 3-1 段头那条"各自的量 = 脚本的数据")。缺省值只是兜底。
    """
    out = []
    for e in exprs:
        v = gdb_ints((vals or {}).get(e))
        if not v:
            return None
        out.append(v[0] & 0xFF)
    return bytes(out)


# ---- ④b 的纯数据(源: TaskRate.c:180-190 的守卫链 / UserCfg.c:321 TAB_RatePara) ----
FALLBACK_MARK = 0x05     # 标记值: 写进 `zWeekDay` 的**合法且非默认**值(该字段守卫不查, 不会被拉回)
FALLBACK_RATE = 13       # 越界费率数: 帧路两个写入口都拒(实测), 只有注入送得进去
FALLBACK_DEFAULT8 = bytes.fromhex("0102080400007F01")   # TAB_RatePara 出厂默认(兜底后镜像应等于它)


def _fallback_verdict(ctrl, av8, wv8, raw=None):
    """④b 的判读: (对照成立?, 注入前那一停的镜像, 注入后那一停的镜像) → `(ok, detail)`。

    **为什么单列成纯函数**: 这段三分支原先长在 `rate_fallback_evidence()` 体里 ——
    它要串口+断点+注入才走得到, **离线测不着**。于是"`None` 被记成 `False`"那个错
    一路活到 2026-09-11 09:29 才炸在台面上(那次把一个探针读取失败说成了固件没兜底)。
    抽出来之后, 三种结局都能在 `__main__` 自检里逐条钉住 —— 不靠真表。

    **本函数只判读数, 不碰串口、不碰 judge。** 三种结局互斥:
      · `ctrl` 为假  ⇒ `None` —— 断点不可信, **本次没做成**(不是固件不对)
      · `wv8 is None` ⇒ `None` —— **镜像读不回来**, 同上
      · 否则          ⇒ `wv8 == 出厂默认` 的真假 —— 到这一步才轮到判固件
    `detail` 里**只写到那一步为止确定的事**, 不许替固件下结论。
    """
    if not ctrl:
        return None, (("对照读不回来(gdb 只回了 %s) ⇒ 断点的位置无从判断, 本次没做成" % _h8(av8))
                      if av8 is None else
                      ("断点疑: 注入点读到的镜像 %s 不含标记值 ⇒ 可能停在重读之前(静默假阴性)"
                       % _h8(av8)))
    if wv8 is None:
        return None, ("注入后镜像**读不回来**(gdb 那 8 个表达式有值没解出来) ⇒ 本次没做成, "
                      "**不判固件**。逐元素原始回读: %s" % (raw,))
    if wv8 == FALLBACK_DEFAULT8:
        return True, ("注入 %d 后镜像 %s ⇒ 整 8 字段 == TAB_RatePara 默认 ⇒ :188 兜底**执行过**"
                      % (FALLBACK_RATE, _h8(wv8)))
    return False, ("注入 %d 后镜像 %s ⇒ **未兜底**: 越界值 13 被留下了(守卫没拦)"
                   % (FALLBACK_RATE, _h8(wv8)))


def rate_fallback_evidence(ser, inj=None, at=None, watch=("TaskRate.c", 190), mirror=MIRROR8_EXPRS,
                           wait=2.5, settle=1.2, *, read_mirror):
    """判据④b 的证据: **注入** `g_RatePara[3]=13` → `:184` 拦 → `:188` 兜底 → 同拍停在 `:190` 读值。

    为什么要绕这一圈(而不是"发一帧写 13"): 两个写入口各自先按 `C_RateNum` 把 13 拒了
    (`rate_num_limit_evidence` 已实测), 帧路**造不出**这个状态。注入是**另一条触发通道**
    (CLAUDE.md「触发也有两个通道」), 于是"帧路不可达"与"证不了"第一次被分开 —— 本项不再是 unprovable。

    **分辨力从哪来**(没分辨力的对拍不算证据, 3-2 记过这条): 兜底把整 8 字段写成 `TAB_RatePara` 默认,
    而本表**台上现配置恰与默认同值** ⇒ 直接比"变没变"是同形。故先预置一个标记值: 把 `zWeekDay` 写成
    **合法且非默认**的 `FALLBACK_MARK`。于是同一次读数就能分辨 ——
      · 走了兜底        ⇒ 镜像 == `FALLBACK_DEFAULT8`(特征字 0x05→**0x7F**、费率数 13→**4**)
      · 没走兜底(坏固件) ⇒ 镜像保留注入值(费率数 **13**、特征字 **0x05**)
    判据断点取 `:190`(兜底支与正常支**都会**走到的那条 `Fetch_CRC`): 所以"停到 :190"本身**不构成证据**,
    **证据是停在那儿读到的值** —— 这正是本记录答得出 falsify 的原因。

    `at` 由**脚本**给(`GD.inject_anchor("Calculate_RateNo", "Read_ParaData")` 的产物): 该 `||` 链上
    编译器行号有洞(`:181-187` 整段无代码), 唯一可注入的时机**没有行号** ⇒ 只能从反汇编推。
    `at_vars` 读"注入**之前**"那一停的镜像 —— 它证"我们停在重读**之后**"; 若停在重读之前, 注入下一句
    就被覆盖, 表象是"固件没走那条分支"(**静默假阴性**), 故这条对照是承重的: 对照不成立时本记录记
    `ok=None`(没做成), **绝不记 FAIL**(那会冤枉固件)。

    末端**还原标记值**(注入只改 RAM, 下一拍 `:180` 重读 EEPROM 即自复原; 要还原的是写在 EEPROM 的
    `zWeekDay`)。返回 `(recs, lines)`。

    `read_mirror` = 调用方给的 AA80 读原语(`(ser, tag=…)` → `g_RatePara` 前 8 字节的原始字节):
    读在脚本(裸 `watch.watch_vars`), 库只拿它回来的字节去判。
    """
    recs, lines = [], []
    if inj is None or at is None:
        lines.append("无注入通道(台面没接 J-Link / 脚本没给注入点) ⇒ ④b 这一半**未做**, 不记 FAIL")
        return recs, lines
    base = read_rate_para(ser, wait=wait)
    o_zw = base.get("zWeekDay")
    lines.append("注入前基线: zWeekDay=%s 费率数=%s 镜像=%s"
                 % (o_zw, base.get("rate"), _h8(read_mirror(ser, wait=wait))))
    try:
        write_rate_param(ser, "zWeekDay", FALLBACK_MARK, wait=wait, quiet=True)
        time.sleep(settle)
        m0 = read_mirror(ser, tag="预置标记值后", wait=wait)
        planted = bool(m0) and len(m0) >= 8 and m0[6] == FALLBACK_MARK
        # ⚠ `m0 is None`(读不回来) 与 `planted is False`(读到了、标记值不在)**不是一件事**:
        #   前者是**探针没读到**, 后者才是"写入口没让它落"。混成一句会把读取失败说成"种不上"。
        lines.append("预置标记值 zWeekDay=%#04x: 镜像=%s ⇒ %s"
                     % (FALLBACK_MARK, _h8(m0),
                        "**镜像读不回来** ⇒ 本次不做判据(探针的事, 不是写入口的事)"
                        if m0 is None else
                        "标记值到位" if planted else "**没写进去**(本次不做判据)"))
        # ⚠ **必须给触发**: `Calculate_RateNo` **不是热点** —— 它由 `Post_Message(ID_TaskRate,
        #   MSG_MinStep)` 驱动(写一次费率参量才跑一轮)。没有触发时 `with_inject` 只会干等超时,
        #   记出来是"没停到注入点", 与"断点错了/固件不对"长得一模一样(2026-09-11 实踩, 首跑 ④b 就
        #   是这么记成 ok=None 的)。这里用**等值写费率数**当驱动: 写入口成功后自己 Post 那条消息。
        #   触发走后台线程(库内会起), 因为它是阻塞式串口动作、而应答要等核心在跑才出得来。
        r = inj(at, [("g_RatePara[3]", FALLBACK_RATE)], watch=watch,
                watch_vars=mirror,             # ⚠ 逐元素读, 见 MIRROR8_EXPRS 上面的 ⚠
                at_vars=mirror,
                label="断[C] 注入 nRateNum=%d ⇒ 停在兜底之后的 %s"
                      % (FALLBACK_RATE, _bptxt(watch)),
                crit="④b" if planted else None,
                falsify="守卫不拦越界 nRateNum ⇒ 不跳 :188 兜底, 镜像保留注入值(%d)与标记值(%#04x)"
                        % (FALLBACK_RATE, FALLBACK_MARK),
                trigger=force_rate_recalc, trigger_args=(ser, base.get("rate")))
        if r is None:
            lines.append("注入观测没做成(judge 侧不挂判据) ⇒ ④b 未证")
            return recs, lines
        av8 = _mirror8_from_gdb(r.get("at_vals"), mirror)
        wv8 = _mirror8_from_gdb(r.get("vars"), mirror)
        # 对照: 注入点那一停的镜像必须**已带标记值**(= 被调函数的重读已经跑过), 且费率数仍是表上的值。
        ctrl = bool(av8) and av8[6] == FALLBACK_MARK and av8[3] == base.get("rate")
        lines.append("对照(注入前那一停): 镜像=%s ⇒ %s"
                     % (_h8(av8),
                        "**读不回来** ⇒ 对照不成立(探针的事, 不是断点的事)" if av8 is None else
                        "停在**重读之后**(注入不会被覆盖)" if ctrl else
                        "**可疑**: 断点可能落在重读之前 ⇒ 注入当场被覆盖, 本次不判"))
        lines.append("注入后停在 %s: 镜像=%s" % (_bptxt(watch), _h8(wv8)))
        # ⚠ 判读收在 `_fallback_verdict()` 里(纯函数, 离线可测)。2026-09-11 09:29 的假 FAIL
        #   (`log/3_1_rate_num_20260911_092928.log`, 退 1) 就是这段长在函数体里、只能靠真表跑到
        #   才漏出去的: 镜像**读不回来**被记成"越界值 13 被留下了(守卫没拦)"。三分钟后同一实验
        #   读出真值并**通过** —— 那次"失败"是假的。**探针没做成 ≠ 固件不对。**
        if r.get("ok") is True:
            r["ok"], r["detail"] = _fallback_verdict(ctrl, av8, wv8, raw=r.get("vars"))
        # 结论行**三态跟着记录的 ok 走**, 不跟着"镜像变没变"走: 读不回来时后者恒假, 照它写就会
        # 把"没读到"打成"未见兜底" —— 与上面那条假 FAIL 是同一个错的另一面。
        lines.append("④b 结论: %s" % (
            "兜底执行过(镜像回默认)" if r.get("ok") is True else
            "**未见兜底**" if r.get("ok") is False else
            "**本次没做成** ⇒ ④b 未证(不判固件)"))
        recs.append(r)
        return recs, lines
    finally:
        # 还原标记值(注入只改 RAM, 下一拍自复原; 要还原的是 EEPROM 里的 zWeekDay)。
        if o_zw is not None:
            write_rate_param(ser, "zWeekDay", o_zw, wait=wait, quiet=True)
            back = read_rate_param(ser, "zWeekDay", wait=wait)
            lines.append("还原 zWeekDay=%s → 读回 %s %s"
                         % (o_zw, back, "✓ 无净变" if back == o_zw else "✗ 需人工核!"))


# ============================================================================================
# 风险①/② 的证据动词(2026-09-14 建): 把「费率参数静默兜底」这两条**在真表上造出来**
# ============================================================================================
# 出处: 工程师对 3-1 提了两条"源码里不存在这些风险(有校验)"。核过源码后结论是
# **校验确实有, 但它的产物是静默兜底** —— 于是这两条不是"文档里的一句话", 是**可造可测的故障**。
#
#   · ①(判据⑤): 参数值不合法 ⇒ 表内部**静默恢复出厂默认继续跑**, **不改 EEPROM、不通知主站、不自愈**。
#     源码依据: `Calculate_RateNo` 的守卫链 `TaskRate.c:180-189` 与 `Get_RatePara` `TaskRate.c:699-715`,
#     守卫不过时都只 `Copy_Data(&g_RatePara[0], TAB_RatePara, LEN_RatePara)` —— **两条函数体内都没有
#     `Write_ParaData`** ⇒ 只改 RAM 镜像, EEPROM 一动不动。
#     ⚠ **工程师那句"主站读 EEPROM 表实为运行 RAM 的值"实测不成立**(2026-09-14 实跑校正): 645 读
#     `noWeekDay` 走 `DLT645App.c:5685` 的 `Read_ParaData(ID_RatePara,…)` ⇒ `Platform/ParaData.c:271`
#     ⇒ `VerRd_EEprom` = **EEPROM 本体**, 不经过 `Get_RatePara` 那份镜像。于是缺陷的真实形状是
#     **两边各说各话**: 内部算费率用回默认后的 RAM, 主站读到的却是 EEPROM 里的越界原值(且出帧前先被
#     `Platform/Common.c:35` 的 `HEX_BCD` 按 `%100` 截位, `0xFF`→`0x55`=85)。详见
#     `rate_silent_default_evidence` 的 docstring。
#   · ②(判据⑥a/⑥b): 主站发**写**时表自动读整块底稿, 读失败就拿默认值初始化整块再把主站那一个值
#     写上去, **不报错**。**645 侧成立 / 698 侧相反**(这个"相反"正是工程师那句"有校验"的对错各一半):
#       645 `DLT645App.c:1752`(费率数写入口的 `if (TRUE != Read_ParaData(…))`)**等 7 处**都是
#         `Copy_Data(&pFrame[DAT9], TAB_RatePara, LEN_RatePara)` 之后照写、`comSta = OK_FRAME`(回成功);
#       698 `DLT698App.c:10480` 是 `if (TRUE == Read_ParaData(…)) {…}`(**没有 else**)⇒ 读失败时
#         `DAR` 留在 `:10441` 的 `DAR_OtherErr`, **报错且一字不写**。
#
# ---- 纯数据(区号/偏移/长度/标记值; 全部来自 .out 与源码, 不手抄地址) ----
# 费率参数块在**外部 EEPROM** 里的位置 = `TAB_Para[ID_RatePara] = {len=0x8, addr=0x92A}`(Platform/ParaData.c)。
# ⚠ 区号 3 的**区内偏移**在本台从未实测核对过(仓里只实测过区 1 RAM 的偏移换算), 故那次直读(`read_ee`,
#   实现住 3-1 脚本的 `_ee8`)的读数**必须**先与 645 读回互核才准用 —— 见 `_ee_xcheck`。
RATE_PARA_EE = (3, 0x92A, 8)
RATE_PARA_698_ALL = "400C0200"      # 698 读费率参数**整块**(应答数据域布局未核 ⇒ 只作观察, 不解读)
RATE_PARA_698_RATE = "400C0204"     # 698 写费率数单项(④a 已实测: 越界 13 回 DAR_OverRate=19)
_RP_IDX = {k: i for i, k in enumerate(_RP_ORDER)}   # 字段名 → 块内下标(enum @ TaskRate.c:160-169)
# 区3 偏移互核**只认这 5 个字段**: 它们两侧的编码是同一个二进制值, 一比就该相等。
# 不比 `holiday`(645 线上 2B / 表内 1B) · `step`(写入口死, 值恒 0) · `noWeekDay`(① 正是要种它)。
_EE_XCHECK = ("zone", "list", "slot", "rate", "zWeekDay")
_RP_WRITE_SKIP = ("step",)          # 非本地表 ⇒ 阶梯数写入口回 ER_D0D1(死); 出厂默认也是 0 ⇒ 还原时跳过
PLANT_NW = 0xFF                     # ① 的预置物: 两个写入口都**明写受理** 0xFF, 而守卫 `:186` (==0)||(>8) **拒**
PLANT_ZONE = 7                      # ② 的标记值: 合法(1~14) ⇒ 守卫不拒 ⇒ 它活没活下来就能分辨兜底发没发生
INJ_EE_BREAK = ("temp[0]", "temp[0] ^ 0xFF")   # ② 的注入: 打坏 CRC 覆盖的第一个字节(见下 ⚠)


def _ee_xcheck(base, e0):
    """`(645 读回, AA80 直读块)` → 对不上的字段名列表。**空列表 = AA80 区3 读法可用**。"""
    return [k for k in _EE_XCHECK
            if base.get(k) is not None and e0 is not None and base[k] != e0[_RP_IDX[k]]]


def _rate_write_eq(ser, key, n, wait=25.0):
    """② 的触发: **等值写**一个费率参量 —— 写入口成功后自己 `Post_Message(ID_TaskRate, MSG_MinStep)`,
    于是"我们发的那一帧"就是驱动那次调用的扳机(不靠自然分节拍)。

    ⚠ **串口超时必须给足**: 这一次里核心要停**三次**(汇合点 / 主份 / 备用份, 每次 1~3s), 而应答要等
      我们放行之后才出得来。按帧路缺省的 2.5s 会把它记成"超时", 而**帧其实成功了** ——
      `with_inject` 的 `trigger_error` 是**环境**那一类, 不是固件不对(见它的 docstring)。"""
    return write_rate_param(ser, key, n, wait=wait)


def _rate_write_eq_698(ser, oad, val, wait=25.0):
    """② 对照组(698 写入口)的触发: 同一件"等值写费率数", 走 698 的 OAD 写入口 → `(verdict, note, dar)`。
    `0x11` = unsigned 外壳(出处: 2026-09-10 实测的应答形态 + ④a 已实跑的那一发)。"""
    return write_oad_ud(ser, oad, bytes([0x11, val]), wait=wait, tag="wr698_rate_eq")


def rate_silent_default_evidence(ser, ee=RATE_PARA_EE, plant=PLANT_NW, wait=2.5, settle=1.5,
                                 *, read_ee, read_mirror):
    """判据⑤ 的证据(风险①): 越界参数进 EEPROM ⇒ 表内部**静默回默认继续跑**, 而 EEPROM 原文不动、
    写入口报成功、永不自愈 ⇒ **主站看到的数与表自用的数分叉**。返回 `(recs, why, scope)`,
    `scope=None` = 这一支半途中止。

    **免注入的真故障**(这是本支的可贵之处: 不需要 J-Link, `--no-gdb` 台面上照样做得成):
    两个写入口都**受理** `noWeekDay = 0xFF`(标准里"周休日不使用时段表"义, `DLT645App.c:2100` /
    `DLT698App.c` 同款 `|| (值==0xFF)`), 而计算体守卫 `TaskRate.c:186` 是 `(==0)||(>8)` ⇒ **拒它**。
    于是: 写入口把它原样写进 EEPROM ⇒ 下一拍重算时守卫判越界 ⇒ `Copy_Data(&g_RatePara[0], TAB_RatePara,
    LEN_RatePara)` **把内部镜像整个换回出厂默认**继续跑。全程 `Write_ParaData` **一次都没调**
    ⇒ EEPROM 里的 0xFF 原地不动 ⇒ **永不自愈**。

    ⚠ **分叉的方向, 实测与初判相反(2026-09-14 实跑后校正, 别再按旧说法写)**:
      工程师那句"主站读 EEPROM 表**实为运行 RAM** 的值"——就本判据用的这个 DI 而言**不成立**:
      645 读 `noWeekDay` 走的是 `DLT645App.c:5685`(`case 0x000802`, `Read_ParaData(ID_RatePara,…)`
      ⇒ `Platform/ParaData.c:271` ⇒ `VerRd_EEprom` = **外部 EEPROM 本体**), 没有走 `Get_RatePara`
      那份 RAM 镜像。所以主站读到的**恰恰是 EEPROM 里那个越界值**, 只是它出帧前先过了
      `Platform/Common.c:35` 的 `HEX_BCD`(`hex %= 100` 截位): `HEX_BCD(0xFF)` = **0x55 = 85** ——
      这解释了实测那一次 `645 读回=85` 的**来历**(85 不是厂默认 1, 也不是写进去的 255)。
      ⇒ **真正的缺陷形状是**: 内部悄悄改用默认值算费率, 主站却照 EEPROM 原样读到一个越界字节
      (还被截位成一个**看着像数**的 85), 两边自此各说各话, 且**没有任何一方被告知**。

    **四个面都要, 缺一面就不成证据**:
      · 写入口受理且回成功(`v == "PASS"`)              ⇒ 不是"被拒所以没事"
      · EEPROM 里那一字节**仍是 0xFF**                ⇒ 没被修正(AA80 直读区3, 先与 645 互核过)
      · 内部镜像 == 出厂默认(第 7 字节 = 1)           ⇒ 计算侧确实回默认了
      · 645 读回 **≠ 内部镜像的值**(实测 85 = `HEX_BCD(0xFF)`, 且 85 ≠ 1) ⇒ 两边分叉
    再**重算一次**, 四个面一模一样 ⇒ 不自愈。

    ⚠ **镜像是 AA80 直读 `g_RatePara`(区1)**, 与断点停在 `TaskRate.c:190` 读 `g_RatePara[0..7]` 是
      **同一个值的两条通道** —— 这里用 AA80 是为了本支在没接 J-Link 时也做得成: 风险①的证据全在
      "对外读数 vs EEPROM 本体"的分叉上, 两者都从串口取, 不需要停核。
    ⚠ 库里**不解读** 698 整块读(`RATE_PARA_698_ALL`)的应答 —— 它的数据域布局没核过。两次读数
      原样打进日志**只作观察**, 不参与判据(别把没核过的东西拿来下结论)。

    `read_ee` / `read_mirror` = 调用方给的两条 AA80 读原语(EEPROM 费率参数块 / `g_RatePara` 前 8 字节):
    读在脚本(裸 `watch.read_mem_aa80` / `watch.watch_vars`), 库只拿回来的字节去判。
    """
    recs, why = [], []
    base = read_rate_para(ser, wait=wait)
    if base.get("noWeekDay") is None or base.get("rate") is None:
        return [], why + ["费率参量读不出(645 无应答?) ⇒ 不写不动, 半途中止"], None
    e0 = read_ee(ser, ee, tag="基线", wait=wait)
    m0 = read_mirror(ser, tag="基线 g_RatePara 镜像", wait=wait)
    raw0 = read_oad_ud(ser, RATE_PARA_698_ALL, wait=wait)
    why.append("基线: 645 %s" % {k: base[k] for k in _RP_ORDER})
    why.append("      EEPROM(AA80 区%d 0x%X)=%s | 内存镜像=%s | 698 整块=%s"
               % (ee[0], ee[1], _h8(e0), _h8(m0),
                  (raw0 or b"").hex(" ").upper() or "(无应答/只作观察)"))
    if e0 is None:
        return [], why + ["AA80 读不到外部 EEPROM 的费率参数块(整帧静默 ⇒ 越界或区3 偏移不对) "
                          "⇒ 本支**未做**(记不了'EEPROM 没被改'), 半途中止"], None
    bad = _ee_xcheck(base, e0)
    why.append("互核(区3 偏移验证, 比 %s): %s" % (list(_EE_XCHECK),
               "✓ 一致 ⇒ AA80 区3 读法可用" if not bad else
               "✗ 不一致 %s ⇒ **AA80 区3 的区内偏移未验, 本支不作数**" % bad))
    if bad:
        return [], why + ["AA80 直读块与 645 读回对不上 ⇒ 不能拿它当 EEPROM 的证据, 半途中止"], None
    e_nw, m_nw = _RP_IDX["noWeekDay"], _RP_IDX["noWeekDay"]
    o_nw = e0[e_nw]
    if o_nw == (plant & 0xFF):
        return [], why + ["基线 noWeekDay 已经是 %#04x —— 写下去毫无变化, 本支造不出故障, 半途中止"
                          % (plant & 0xFF)], None
    recs = []
    try:
        # ---- 预置(正常 645 写入口; 落一条编程记录, 与原有写参数同性质) ----
        v, n = write_rate_param(ser, "noWeekDay", plant, wait=wait)
        time.sleep(settle)                     # 写入口成功后自己 Post 了消息; 给重算 ~1.5s
        why.append("预置: 645 写 noWeekDay=%#04x → %s %s" % (plant, v, n or ""))
        force_rate_recalc(ser, base["rate"], wait=wait, quiet=True)
        time.sleep(settle)
        e1 = read_ee(ser, ee, tag="预置后", wait=wait)
        m1 = read_mirror(ser, tag="预置后镜像", wait=wait)
        b1_raw = read_param_di(ser, RATE_PARA_DI["noWeekDay"], wait=wait, quiet=True)
        b1 = dec_rate_val("noWeekDay", b1_raw, _RATE_PARA_NB["noWeekDay"])
        raw1 = read_oad_ud(ser, RATE_PARA_698_ALL, wait=wait)
        why.append("预置后: EEPROM 第7字节=%s | 镜像=%s | 645 读回=%s(原始 %s) | 698 整块=%s"
                   % ((("%#04x" % e1[e_nw]) if e1 is not None else "读不到"), _h8(m1), b1,
                      ("%02X" % b1_raw[0]) if b1_raw else "-",
                      (raw1 or b"").hex(" ").upper() or "(无应答/只作观察)"))
        # ---- 再重算一次: 现象重演 = 不自愈 ----
        force_rate_recalc(ser, base["rate"], wait=wait, quiet=True)
        time.sleep(settle)
        e2 = read_ee(ser, ee, tag="第二次重算后", wait=wait)
        m2 = read_mirror(ser, tag="第二次重算后镜像", wait=wait)
        b2 = dec_rate_val("noWeekDay", read_param_di(ser, RATE_PARA_DI["noWeekDay"],
                                                    wait=wait, quiet=True), _RATE_PARA_NB["noWeekDay"])
        # ---- 四个面 ----
        d_nw = FALLBACK_DEFAULT8[_RP_IDX["noWeekDay"]]
        f_write = (v == "PASS")
        f_ee = (e1 is not None) and (e1[e_nw] == (plant & 0xFF))
        f_mirror = (m1 is not None) and (m1 == FALLBACK_DEFAULT8)
        f_read = (b1 is not None) and (b1 != d_nw)
        f_replay = (e2 is not None and m2 is not None and b2 is not None
                    and e2 == e1 and m2 == m1 and b2 == b1)
        why.append("四面: 写入口成功=%s | EEPROM 仍=%#04x=%s | 镜像==出厂默认=%s | 主站读回=%s(!= %d)=%s"
                   " | 重算重演=%s"
                   % (f_write, plant & 0xFF, f_ee, f_mirror, b1, d_nw, f_read, f_replay))
        # 三态: 探针没读回来 ⇒ **没做成**(None), 不是"固件没这个现象"
        probe_ok = (e1 is not None and m1 is not None and b1 is not None)
        ok = (f_write and f_ee and f_mirror and f_read and f_replay) if probe_ok else None
        detail = ("; ".join(why[-2:])) + (
            "" if probe_ok else " | **探针没读回来** ⇒ 本支没做成, 不判固件")
        recs.append(rec("⑤ 越界 noWeekDay 被静默回默认: EEPROM 原文不动、主站读回≠内部自用、重算重演",
                        ok, detail, crit="⑤",
                        falsify="若固件拒收 0xFF、或把内部默认**回写修正**到 EEPROM、或上报错误/落告警, "
                                "则 EEPROM 那一字节会被改 / 镜像仍是 0xFF / 回帧不是成功 —— 任一成立即本条不成立"))
    finally:
        # ---- 自还原 + 复核(走无注入的正常写入口) ----
        vr, nr = write_rate_param(ser, "noWeekDay", o_nw, wait=wait)
        time.sleep(settle)
        eR = read_ee(ser, ee, tag="还原后", wait=wait)
        bR = dec_rate_val("noWeekDay", read_param_di(ser, RATE_PARA_DI["noWeekDay"],
                                                    wait=wait, quiet=True), _RATE_PARA_NB["noWeekDay"])
        same = (eR is not None) and (eR[_RP_IDX["noWeekDay"]] == o_nw) and (bR is not None)
        why.append("还原 noWeekDay=%#04x → %s; 复核 EEPROM 第7字节=%s 645 读回=%s ⇒ %s"
                   % (o_nw, vr, ("%#04x" % eR[_RP_IDX["noWeekDay"]]) if eR is not None else "读不到",
                      bR, "✓ 无净变" if same else "✗ **需人工核!**"))
        if not same:
            recs.append(rec("① 预置物已还原为原值", False, why[-1], crit=None))
    return recs, why, True


def rate_write_fallback_evidence(ser, inj=None, at=None, watch=None,
                                 gate645=("DLT645App.c", 1752), gate698=("DLT698App.c", 10480),
                                 ee=RATE_PARA_EE, plant_zone=PLANT_ZONE, rate_n=None,
                                 timeout=25.0, wait=2.5, settle=1.5, *, read_ee):
    """判据⑥a/⑥b 的证据(风险②): "写入口读底稿失败 ⇒ 拿厂默认当底稿把主站那一个值写上去、不报错"。
    **645 侧成立(⑥a)/ 698 侧相反(⑥b, 同一故障下报错且一字不写)**。返回 `(recs, why, scope)`。

    为什么要这一圈(而不是"发一帧把 EEPROM 读坏"): 帧路**造不出**"读底稿失败" ——
    `Read_ParaData` 走 `VerRd_EEprom` 的**双份 + 双 CRC** 结构(`Platform/EEprom.c`), 只有**主份和
    备用份的 CRC 同时**不过才 `return OTHER`, 而 `Read_ParaData` 把 `OTHER` 映射成 `FALSE`。
    故换一条触发通道: 在两个可注入瞬间各打坏 CRC 覆盖的第一个字节(`temp[0] ^= 0xFF`)。
    两个瞬间都由 `.out` 反汇编推(`inject_anchors("VerRd_EEprom","Read_EEprom")` 的第 0/第 1 处),
    **不收手抄地址**。

    ⚠ **`gate` 是承重的**(本参数就是为这一支加的): `VerRd_EEprom` 是**热路径**(每个参数读都过它),
      不管住就会先命中**别人的**一次调用, 打坏的是无关参数 —— 而表象是"我们要造的状态没出现",
      也就是本仓最忌讳的**静默假阴性**。故先在**写入口那一行**停一下(那一行**只可能**由我们发的那一帧
      造成), 把"这一次调用"认下来, 再挂注入点。

    **标记值**: 先把 `zone` 写成合法的 `plant_zone`(守卫不拒 ⇒ 它**必须活下来**)。于是同一次读数
    就能分辨: 兜底发生了 ⇒ 整块 == `FALLBACK_DEFAULT8`(我预置的 7 被顶掉); 没发生 ⇒ 块里还是 7。
    ⚠ 预置必须**先复核到位**(见下 `planted`), 否则"基线本来就是厂默认 + 注入没生效"会**假通过**。

    **判据怎么落**(每条只在对照齐备时才认领, 缺一样记 `ok=None` = 没做成, **绝不记 FAIL**):
      · ⑥a: 两处都注成 + 汇合点命中 + 回帧**成功** + 块 == 厂默认;
      · ⑥b: 同一故障打 698 写入口(汇合点换成 `DLT698App.c:10480`)⇒ 回帧**错误**(DAR≠0)且块**一字未动**。

    `read_ee` = 调用方给的 AA80 读原语(`(ser, ee, tag=…)` → EEPROM 费率参数块 8 字节): 读在脚本
    (裸 `watch.read_mem_aa80`), 库只拿回来的字节去判。
    """
    recs, why = [], []
    if inj is None or at is None or watch is None:
        why.append("无注入通道(台面没接 J-Link / 脚本没给注入点) ⇒ ⑥a/⑥b 这两半**未做**, 不记 FAIL")
        return recs, why, None
    base = read_rate_para(ser, wait=wait)
    if base.get("zone") is None or base.get("rate") is None:
        return [], why + ["费率参量读不出(645 无应答?) ⇒ 不写不动, 半途中止"], None
    if rate_n is None:
        rate_n = base["rate"]
    e0 = read_ee(ser, ee, tag="基线", wait=wait)
    why.append("基线: 645 %s | EEPROM=%s" % ({k: base[k] for k in _RP_ORDER}, _h8(e0)))
    bad = _ee_xcheck(base, e0)
    if e0 is None or bad:
        why.append("AA80 区3 这一通道本次不可用(%s)—— 判读改以 **645 读回**为唯一通道"
                   % ("整帧静默/读不到" if e0 is None else "与 645 读回对不上: %s" % bad))
    z_i = _RP_IDX["zone"]
    shot = []

    def _plant():
        """预置标记值 zone=plant_zone(合法 ⇒ 守卫不拒) → 复核到位。回 (到位?, 说明)。"""
        v, n = write_rate_param(ser, "zone", plant_zone, wait=wait, quiet=True)
        time.sleep(settle)
        eb = read_ee(ser, ee, tag="预置 zone 后", wait=wait)
        rb = read_rate_param(ser, "zone", wait=wait, quiet=True)
        hit_ee = (eb is not None) and (eb[z_i] == plant_zone)
        hit_645 = (rb == plant_zone)
        return (v == "PASS") and (hit_ee or hit_645), \
            ("zone=%d 写→%s; EEPROM 首字节=%s; 645 读回=%s"
             % (plant_zone, v, (("%#04x" % eb[z_i]) if eb is not None else "读不到"), rb))

    def _after(tag):
        """一次之后: 块 + 645 读回。回 (块|None, {字段:值}, 说明)。"""
        eb = read_ee(ser, ee, tag=tag, wait=wait)
        rb = read_rate_para(ser, wait=wait, quiet=True)
        return eb, rb, ("EEPROM=%s | 645 读回=%s" % (_h8(eb), {k: rb[k] for k in _RP_ORDER}))

    try:
        # ================= ⑥a: 645 写入口 =================
        okp, info = _plant()
        why.append("【⑥a】预置标记值: " + info)
        if not okp:
            why.append("【⑥a】**标记值没写进去** ⇒ 本半支不做(写不进去时'块变成厂默认'分辨不出兜底发生没发生)")
        else:
            # ⚠ `then_assigns` 是**承重的, 不是锦上添花**: 只打坏主份 ⇒ `VerRd_EEprom` 拿**备用份**
            #   一试就过 ⇒ `Read_ParaData` 回 TRUE ⇒ 兜底根本不发生, 而表象是"我预置的标记值还在",
            #   也就是把"本次没把故障造出来"读成"固件没有这个缺陷"。**双份都要打坏**。
            r = inj(at, [INJ_EE_BREAK], watch=watch, then_assigns=[INJ_EE_BREAK],
                    label="断[D/E] 打坏 VerRd_EEprom 双份 CRC(645 写入口)",
                    timeout=timeout, gate=gate645,
                    trigger=_rate_write_eq, trigger_args=(ser, "rate", rate_n, timeout))
            shot.append(r)
            tr = (r or {}).get("trigger_result")
            why.append("【⑥a】触发帧(645 等值写费率数)=%s | 汇合点=%s | 注入点=%s | 判据断点=%s | 注入=%s | 判据断点补注=%s"
                       % (tr, (r or {}).get("gate_hit"),
                          "命中" if (r or {}).get("at_hit") else "未命中",
                          (r or {}).get("watch_hit") or "未命中",
                          "; ".join((r or {}).get("injects") or []) or "(无)",
                          "; ".join((r or {}).get("then_injects") or []) or "(无)"))
            eb, rb, info = _after("⑥a 之后")
            why.append("【⑥a】" + info)
            # 三态: 探针/注入这一次没做成 ⇒ None; 做成了才轮到判固件。
            if r is None or r.get("trigger_error") is not None:
                why.append("【⑥a】**触发里抛了**(%s)⇒ 这一次没做成(环境那一类, 不是固件)"
                           % (r or {}).get("trigger_error"))
                recs.append(rec("⑥a 645 写入口读底稿失败 ⇒ 拿厂默认当底稿整块写回并回成功",
                                None, "触发未成功收线: %s" % why[-1], crit="⑥a",
                                trig=judge.TRIG_INJECT,
                                falsify="若 645 在底稿读失败时不写、或回错误码, 则我预置的标记值仍在 / 主站收到错误"))
            elif r.get("ok") is not True:
                why.append("【⑥a】**没停到两处**(汇合点/注入点/判据断点有一处没到)⇒ 一个字都没改成, 本半支没做成")
                recs.append(rec("⑥a 645 写入口读底稿失败 ⇒ 拿厂默认当底稿整块写回并回成功",
                                None, r.get("detail") or "未停到", crit="⑥a",
                                trig=judge.TRIG_INJECT,
                                falsify="若 645 在底稿读失败时不写、或回错误码, 则我预置的标记值仍在 / 主站收到错误"))
            else:
                blk = (eb == FALLBACK_DEFAULT8) if eb is not None else None
                gone = (rb.get("zone") is not None) and (rb["zone"] != plant_zone)
                ok = bool(gone and (blk is not False))
                # ⚠ 三停**照实报**(别写死"三停全到"): 2026-09-14 实跑那一版就是写死的, 而同一份
                #   记录里 `判据断点` 因读错键印成 `None` —— 日志自相矛盾, 复核的人只能信写死的那半句。
                stops = "+".join([("汇合点" if r.get("gate_hit") else "**汇合点没到**"),
                                  ("注入点" if r.get("at_hit") else "**注入点没到**"),
                                  ("判据断点" if r.get("watch_hit") else "**判据断点没到**")])
                detail = ("三停: %s | 回帧=%s | 标记值 zone=%d "
                          "%s | 整块==厂默认=%s" % (stops, tr, plant_zone,
                                                  "**已被顶掉**" if gone else "**仍在**",
                                                  blk if blk is not None else "(AA80 通道本次不可用)"))
                recs.append(rec("⑥a 645 写入口读底稿失败 ⇒ 拿厂默认当底稿整块写回并回成功",
                                ok, detail, crit="⑥a", trig=judge.TRIG_INJECT,
                                falsify="若 645 在底稿读失败时不写、或回错误码, 则我预置的标记值仍在(块==厂默认 "
                                        "不成立)/ 主站收到错误"))
        # ================= ⑥b: 698 写入口(对照组) =================
        okp, info = _plant()
        why.append("【⑥b】重新预置标记值: " + info)
        if not okp:
            why.append("【⑥b】**标记值没写进去** ⇒ 本半支不做")
        else:
            r = inj(at, [INJ_EE_BREAK], watch=watch, then_assigns=[INJ_EE_BREAK],
                    label="断[D/E] 打坏 VerRd_EEprom 双份 CRC(698 写入口)",
                    timeout=timeout, gate=gate698,
                    trigger=_rate_write_eq_698, trigger_args=(ser, RATE_PARA_698_RATE, rate_n, timeout))
            shot.append(r)
            tr = (r or {}).get("trigger_result")
            dar = tr[2] if isinstance(tr, (tuple, list)) and len(tr) >= 3 else None
            why.append("【⑥b】触发帧(698 等值写费率数 %s)=%s | 汇合点=%s | 注入点=%s | 判据断点=%s | DAR=%s"
                       % (RATE_PARA_698_RATE, tr, (r or {}).get("gate_hit"),
                          "命中" if (r or {}).get("at_hit") else "未命中",
                          (r or {}).get("watch_hit") or "未命中", dar))
            eb, rb, info = _after("⑥b 之后")
            why.append("【⑥b】" + info)
            # ⚠ 698 的 `DAR` 与 645 的**错误标志位图**(`errflag_name`) 是两套编码, 别混着解读
            #   —— 本支只断言 `DAR != 0`(= 报了错), 不去解释那一位叫什么。
            if r is None or r.get("trigger_error") is not None:
                why.append("【⑥b】**触发里抛了**(%s)⇒ 这一次没做成" % (r or {}).get("trigger_error"))
                recs.append(rec("⑥b 698 写入口(对照): 同一故障下回错误且 EEPROM 一字未动", None,
                                "触发未成功收线", crit="⑥b", trig=judge.TRIG_INJECT,
                                falsify="若 698 也走 645 那条兜底, 则它回成功且整块变厂默认"))
            elif r.get("ok") is not True:
                why.append("【⑥b】**没停到两处** ⇒ 本半支没做成")
                recs.append(rec("⑥b 698 写入口(对照): 同一故障下回错误且 EEPROM 一字未动", None,
                                r.get("detail") or "未停到", crit="⑥b", trig=judge.TRIG_INJECT,
                                falsify="若 698 也走 645 那条兜底, 则它回成功且整块变厂默认"))
            else:
                kept = (eb is not None and eb[z_i] == plant_zone) or (rb.get("zone") == plant_zone)
                err = (dar is not None and dar != 0)
                ok = bool(kept and err)
                detail = ("注入同样打坏两处 CRC | 回帧=%s DAR=%s | 标记值 zone=%d %s"
                          % (tr, dar, plant_zone, "**仍在**(一字未动)" if kept else "**没了(被写了!)**"))
                recs.append(rec("⑥b 698 写入口(对照): 同一故障下回错误且 EEPROM 一字未动",
                                ok, detail, crit="⑥b", trig=judge.TRIG_INJECT,
                                falsify="若 698 也走 645 那条兜底, 则它回成功(DAR=0)且整块变厂默认"))
    finally:
        # ---- 整块还原 + 复核(走无注入的正常写入口) ----
        if base.get("zone") is not None:
            for k in _RP_ORDER:
                if k in _RP_WRITE_SKIP or base.get(k) is None:
                    continue
                write_rate_param(ser, k, base[k], wait=wait, quiet=True)
            time.sleep(settle)
            eR = read_ee(ser, ee, tag="还原后", wait=wait)
            rR = read_rate_para(ser, wait=wait, quiet=True)
            same645 = all(rR.get(k) == base.get(k) for k in _RP_ORDER if base.get(k) is not None)
            same_ee = (eR == e0) if (eR is not None and e0 is not None) else None
            same = same645 and (same_ee is not False)
            why.append("还原(逐字段写回基线; 跳过不可写的 %s): 645 复核=%s | EEPROM 复核=%s ⇒ %s"
                       % (list(_RP_WRITE_SKIP), "✓" if same645 else "✗ %s" % {k: rR[k] for k in _RP_ORDER},
                          {True: "✓ 与基线逐字节相同", False: "✗ %s != %s" % (_h8(eR), _h8(e0)),
                           None: "(本次 AA80 通道不可用)"}[same_ee],
                          "✓ 无净变" if same else "✗ **需人工核!**"))
            if not same:
                recs.append(rec("② 费率参数整块已还原为基线", False, why[-1], crit=None))
    return recs, why, True


def rate_para_criteria():
    """3-1 的预设判据条目(源 = project/knowledge/_whitebox_ledger/ledger.md 3-1 的 I 列「判过: ②③④」)。

    拆分说明(**不是洁癖, 不拆就会记出假通过**):
      · ② 一条: "归属=所设费率号"与"无错位"是同一次读数的两个面(rate/solt 一起看), 合成一条;
        一个只错位不错费率的固件照样会 FAIL 这条 —— 没必要拆。
      · ③ 拆 a/b/c: 规格原文"不同日期类型取表正确"断的是**三件独立的事**(节假日/周休日/普通日三条
        分支)。本表**出厂特征字=0x7F、假日数=0 ⇒ 前两条分支一次都不走** ⇒ 合成一条的话, 只测普通日
        也能记"满足", 那两条就成了白捡的。
      · ④ 拆 a/b: ④a(12 上限生效/13 被拒)= **帧路**能证(两个写入口各自先拦); ④b(:184 拦→:188 兜底)
        要 `g_RatePara[nRateNum]>12` 这个**帧路不可达**的状态 —— 于是它**换一条触发通道**去证:
        `rate_fallback_evidence()` 用**注入**在 `Read_ParaData` 重读之后、守卫之前把那个值写进 RAM。
        ⚠ **"帧路造不出" ≠ "证不了"**(2026-09-11 校正): 早先这里写的是 unprovable, 理由里夹了
        "swdbg 有意不提供写内存接口" —— 那把**我自己的收窄设计**说成了台面事实。事实是: 状态可达
        (注入观测造得出), 只是**不经帧**。所以 ④b 照常有人认领, 且认领它的记录带 `trig=注入` 标记
        (`common/judge.py` 会把它显形成 `[断点/注入]`, 复核时必须照同一造法复现)。
    """
    return {
        "②": "各费率时段归属 = 所设费率号、且无错位(g_RateNo/g_SoltNo 逐段对上所配时段表)",
        "③a": "节假日路径取表正确(假日条目指向的表号被取到)",
        "③b": "周休日路径取表正确(周休特征字/周休表号生效)",
        "③c": "普通日路径取表正确(时区表按当日选出的表号被取到)",
        "④a": "费率数上限 12 生效: 写 12 受理落库、写 13 被两条写入口拒",
        # ④b **触发通道 = 注入**(不是帧路: 两个写入口按 C_RateNum 先拦, 帧根本送不进 >12)。
        # 认领者在 `rate_fallback_evidence()`; 它的记录带 `trig=注入`, 汇总里显形 `[断点/注入]`。
        "④b": "nRateNum>12 时 :184 判越界 → :188 兜底默认(第 13 费率不生效)",
        # ⑤/⑥a/⑥b = 工程师提的"源码里不存在这些风险(有校验)"那两条。核过源码后结论是
        # **校验确实有, 但它的产物是静默兜底** —— 于是这三条不是"文档里的一句话", 是**可造可测的故障**:
        #   ⑤ 免注入(写入口受理 0xFF、守卫拒它, 见 `rate_silent_default_evidence`);
        #   ⑥a/⑥b 用注入造"读底稿失败"(帧路造不出: 双份+双 CRC, 见 `rate_write_fallback_evidence`)。
        # ⚠ 与 ④b 同一分寸: **本体是缺陷, 条目就断言缺陷现象** —— `ok=True` 意思是"缺陷在"(复现了),
        #   不是"表通过了"。所以每条都把 falsify 写成"什么固件会让它不成立"。
        "⑤": "越界 noWeekDay(写入口受理/守卫拒)被静默回默认: EEPROM 原文不动、"
              "主站读回(EEPROM 原值经 HEX_BCD)≠ 内部自用值、回帧成功、重算重演(不自愈)",
        "⑥a": "645 写入口读底稿失败时拿厂默认当底稿把整块写回(主站那一个值照写), 且回**写成功**",
        "⑥b": "698 写入口(对照)在**同一故障**下回错误(DAR≠0)且 EEPROM **一字未动**",
    }


def rate_para_roundtrip(ser, date, rate_n, trig=None, bp=None, wb_waived=False,
                        settle=1.6, wait=2.5, *, read_trace):
    """3-1 全流程 + 判据(库内单点, 脚本不留): ④a 上限 → ② 逐段归属 → ③ 三条取表路径 → 汇总。
    返回 (recs, why, scope) —— 与 3-2 同形: `scope is None` 表示**半途中止**(不靠文案嗅探)。
    参数: date='YYYY-MM-DD'(②③ 拨钟用的日期); rate_n=重算时等值写回的费率数(=表当前配置值)。
    `read_trace` = 调用方给的 AA80 读原语(读在脚本, 解码在 `rate_trace_decode`), 透传给 ②③。
    """
    why = []
    # ---- 断点观测接线: 脚本持会话, 库只收回调(库不许 import swdbg, 见 _direct_trigger) ----
    trig = trig or _direct_trigger
    # 有会话看 `at`(当场挂/用完撤)或 `no`(调用方预先挂好)任一个在。
    wb = bool((bp or {}).get("no") or (bp or {}).get("at"))
    if not wb and not wb_waived:
        print("   !! 本次无调试会话 → 断点观测(白盒)整体**未做**; 本次范围 = 仅黑盒"
              "(判过的只是对外行为, 内部指令路径未取证 —— 见 _direct_trigger 与 CLAUDE.md)")
    elif not wb:
        print("   -- 本次**用户指定**只做黑盒(串口观测) ⇒ 断点观测(白盒)按指定不做(内部指令路径未取证)")

    # ---- 前置: 读当前配置(费率参数 + 生效时段表号 + 那张表的内容) ----
    orig = read_rate_para(ser, wait=wait)
    why.append("基线费率参数: %s" % {k: orig[k] for k in _RP_ORDER})
    if orig.get("rate") is None or orig.get("zone") is None:
        return [], why + ["费率参数读不出 ⇒ 不写不动, 半途中止"], None
    if orig["rate"] != rate_n:
        why.append("⚠ 传入 rate_n=%s 与表上费率数 %s 不一致 ⇒ 按表上的来" % (rate_n, orig["rate"]))
        rate_n = orig["rate"]
    d, m, _y = ymd_of(date)
    list_exp, alt = active_slot_no(ser, m, d, wait=wait + 0.5)
    why.append(alt)
    if list_exp is None:
        return [], why + ["当前套时区表读不出/形态不符 ⇒ 无法定②的期望表号, 半途中止"], None
    entries = read_slot_tab(ser, "cur", list_exp, wait=wait + 0.5)
    why.append("第%d号时段表: %s" % (list_exp, ("%d 段 %s" % (len(entries), entries)) if entries else "读不出"))
    if not entries:
        return [], why + ["生效时段表内容读不出 ⇒ ②无期望值可比, 半途中止"], None

    recs = []
    # ---- 判据④a(做完即把费率数写回原值) ----
    r4, l4 = rate_num_limit_evidence(ser, orig["rate"], wait=wait)
    recs += r4
    why += ["【④a】" + x for x in l4]
    # ---- 判据②(逐段拨钟) ----
    r2, l2 = rate_attribution_evidence(ser, entries, list_exp, date, rate_n,
                                       settle=settle, wait=wait, trig=trig, bp=bp, read_trace=read_trace)
    recs += r2
    why += ["【②】共 %d 段:" % len(entries)] + l2
    # ---- 判据③(三条路径, 自带还原) ----
    r3, l3 = date_type_evidence(ser, "%s 10:00:00" % date, rate_n, list_exp, settle=settle, wait=wait,
                                read_trace=read_trace)
    recs += r3
    why += ["【③】"] + l3
    return recs, why, True


# ============================ 5-4『电表清零』判据(纯数据, 2026-09-10) ============================
# 与 bill_freeze_criteria/bill_freeze_evidence(4-6) 同一套: 预设条目 + 库内证据函数 → common/judge.py 汇总。
# **判据本体放库里**(测试脚本是薄操作清单), 脚本只认领/打印/拿退出码。
def clear_meter_criteria():
    """5-4「电表清零」的预设判据条目(源 = project/knowledge/_whitebox_ledger/ledger.md 5-4 的 I 列「判过: …」)。

    与 3-2/4-6 同一套用法(见 common/judge.py): 测试**前**定死, 测试后只数它满足了几条。

    **条目与"谁认领它"是一对**: 下面四个 `clear_*_evidence()` 各认领一条; 声明了 `unprovable` 的那两条
    (①记录 / ⑦永久项)**不许**有认领者 —— 既说"证不了"又说"证到了"是两套话。
    声明不可证的条目照记「未证」并把理由印进结论, **不是豁免**。
    """
    return {
        "①记录": {"text": "清零落一条『电表清零』永久记录(清零事件为永久记录)",
                  "unprovable": "本台三条准入路都够不着(2026-09-10 实测坐实, 非推断):"
                                "①本地明文路(DLT645App.c:3171)受理要求 Is_EnablePrg() 为真, 而 :9247 落"
                                "Recd_ClrMeter 要求它为假 —— 同一帧里不可能既真又假 ⇒ 必跳过 :9249"
                                "(实测: 0x1A 受理回 9A 00 后读 30130B0A 恒 0 条);"
                                "②远程长帧路(:3148, 须 TAB_MeterSty.style==TP_Remote 且 LEN=0x1C 且"
                                "DI0=0x98)要求 g_KeyStat==KEY_Testing —— 本台**满足**(实测 g_KeyStat="
                                "0x12345678=KEY_Testing; ⚠ :3155 行内注释写的是「私钥下不允许」, 但比较的"
                                "常量是 KEY_Testing, 注释与代码不符, 别照抄注释) —— 可它还要求"
                                "g_AuthTimer!=0, 那只有过一次 645 安全认证会话才非 0(CMD645_DecodeCmd 首行"
                                "TaskRmtFee.c:1491 `g_AuthTimer==0` 即 ER_PSWD), 无认证密钥起不来会话;"
                                "③698 侧 430003 数据初始化: 时标/TP_Remote/KEY_Testing 三道判定本台全过, 但卡在"
                                "Chk_SafeMode → Get_ConnectSta(DLT698App.c:3635) 要求 ESAM 与服务器双认证"
                                "连接, 实测 DAR=0x14(MatchAuth)。要证须厂商给认证密钥, 或澄清本地路该不该落记录"},
        "②电量": "电量/需量被清(组合有功总电能 645 DI 00000000 归零)",
        "③冻结": "冻结记录被清(DLT645App.c:9257 Clear_FrezData(ID_AllFrez))",
        "④分区": "清空范围与固件选的分区**自洽**: 黑盒(分水岭记录还在不在) × 白盒(断[B] 读回的 `id` "
                 "是哪个分区)两种观测讲同一件事 —— 打架就是有一方错, 报代码侧",
        "⑤重建": "清后存储重建正常(记录对象仍可正常读写)",
        "⑥参数": "清后参数量归零: 上月总电量(20310200) / 上年总电量(20320200) / 电池工作时间(20130200) / "
                 "非法插卡次数(30271D00) / 当月绿码个数(280C0400) / 总绿码个数(280C0500) 六样全为 0"
                 "(DLT645App.c:9262 Clear_GreenCode_Data_And_Save / :9264 ID_MonthkWhZ / :9271 ID_BattWorkT / "
                 ":9278 ID_YearkWh / :9279 ID_NoLawNum —— 五处都落在 Clear_MeterData 的本地态分支里)",
        # 规范那一句「永久记录与序号基准保留」在 5-4 规格原文里, 而它在本台**判不了** ——
        #   「永久」那一档在固件里就是 `IE_ClrMeter..IE_RecdEnd` 这一段(只装 ID_ClrMeter 一条),
        #   `ID_AllMeter` 清到 IE_ClrMeter 为止、`ID_AllRecd` 清到 IE_RecdEnd(RecdData.c:815-822)。
        #   本台走本地路(编程态) ⇒ 固件选的是 `ID_AllRecd`(自定义清零)⇒ 那一段**本来就该被清**,
        #   照它记「永久项被误清」是拿固件自己的分支当尺子。要判这一条得走非编程态那一路。
        "⑦永久项": {"text": "规范「永久记录与序号基准保留」: 清后 `IE_ClrMeter` 那一档(装的是 ID_ClrMeter "
                           "『电表清零』那一条)仍在、序号基准不回退到 0",
                   "unprovable": "这一档在固件里就是 `IE_ClrMeter..IE_RecdEnd`(只装 ID_ClrMeter 一条), "
                                 "而本台只走得通本地路(编程态)⇒ 固件按 Is_EnablePrg() 选 `ID_AllRecd`"
                                 "(:9244), 它清到 IE_RecdEnd(RecdData.c:819-822)⇒ 那一段本就被清, "
                                 "读它「没了」只说明走了自定义清零, 说明不了规范违没违。"
                                 "要证须走非编程态那一路 —— 远程长帧(:3148)与 698 430003 两条都要认证, "
                                 "与判据 ① 同一个卡口(见 ① 的 unprovable)"},
    }


def clear_partition(id_text):
    """断[B] 读回的量 `id`(文本) → 清零主体走的那条记录分区: `"all"`=整库 / `"meter"`=截至电表清零 / None=认不出。

    定址: 该量的取值由 DLT645App.c:9244 按 Is_EnablePrg() 二选一给出(ID_AllRecd / ID_AllMeter),
    :9244→:9247 之间无人再写它, 所以 :9247 上读到的是刚算出来的那个。
    **认枚举名不认数字**: DWARF 里 gdb 回的是 `ID_AllRecd` 这样的名字; 硬编码数字去比会在换 gdb 版本
    或重编后恒不中(CLAUDE.md 调试链纪律 4)。认不出回 None ⇒ 判据判「未定论」, **不许猜** ——
    猜出来的分区会拿去当"记录库被清到哪"的判据, 那正是假通过。
    """
    s = (id_text or "").strip()
    if "ID_AllRecd" in s:
        return "all"
    if "ID_AllMeter" in s:
        return "meter"
    return None


_CLEAR_FALSIFY = {
    "④分区": "本地路不清整库(或非编程态却整库清) ⇒ 与 :9244 选的 id 分支矛盾; "
              "或两种观测讲的分区不一样",
    "③冻结": "清完后冻结记录序号一字未动(且非本台观测口径所致) / 序号反而推进 ⇒ 与 :9257 不符",
    "②电量": "有负载读数却清完没归零 ⇒ 与 :9255 Clear_CurkWh 不符",
    "⑤重建": "清完记录区后写参量落不下新记录(写本身回了 OK_FRAME 而 30120B0A 恒空) "
              "⇒ 清零把记录区的写指针/链表头一起废了, 存储没重建",
    "⑥上月用电量": "Clear_MeterData 落了 ID_MonthUsed 那一笔, 读回却仍非零",
    "⑦绿码个数": "Clear_MeterData 调了 Clear_GreenCode_Data_And_Save, 读回却仍非零",
    "⑧电池工作时间": "厂内态下 :9271 那一笔落了, 读回却仍非零(或该笔本该只走厂内态却走了厂外)",
    "⑨非法插卡次数": "本地表下 :9279 那一笔落了, 读回却仍非零(或该笔本该只走本地表却走了非本地表)",
    "⑪全程未中途返回": "任一步返回 FALSE ⇒ 停在 :3180 的 return ER_OTHER 而不到 :3186",
}


def clear_library_evidence(pre_keep, post_keep, partition, tag=""):
    """5-4 判据④「记录库被清到哪」: 黑盒(分水岭记录还在不在) × 白盒(`id` 是哪个分区) 是否自洽 → rec。

    为什么这条是 5-4 的要害: 判据①(清零记录新增)在本台**不可达**, 而"不可达"是个论断。本判据把它
    变成**可证伪的预测** —— DLT645App.c:9244 的 `id` 与 :9247 那道判定读的是同一个 Is_EnablePrg(), 于是:
      · id=ID_AllRecd → 走 :9247 为假那条 ⇒ :9249 落库必被跳过, 且 Platform/RecdData.c:819-822 把
        IE_RecdStr..IE_RecdEnd **整段**写 0 ⇒ 分水岭记录**必须没了**;
      · id=ID_AllMeter → 走 :9247 为真那条 ⇒ 只清到 IE_ClrMeter 为止(RecdData.c:815-817) ⇒ 分水岭
        记录**必须还在**, 且判据①本应可达。
    分水岭记录 = **落在电表清零记录之后**的那条(脚本取其一条稳定存在的, 如『编程』)。

    两种观测是两次**独立观测**(一条串口、一条断点), 一致才算数 —— 只看一边是假通过。
    **ok=None = 两种观测没凑齐**(没接 J-Link / 清前没基线): 那时条目记「未证」, **不拿"另一边看着对"顶上**。

    pre_keep/post_keep: `read_event_row` 的返回(dict, 取它的 "seq")。
    partition:          `clear_partition()` 的结果(None = 白盒没读回 id ⇒ 缺一种观测)。
    """
    bad = _CLEAR_FALSIFY["④分区"]
    s0 = (pre_keep or {}).get("seq")
    if s0 is None:
        return rec(tag or "记录库清空范围", None,
                   "清前读不到分水岭记录(本台无基线)—— 分水岭观测不到, 本判据无从判",
                   crit="④分区", falsify=bad)
    s1 = (post_keep or {}).get("seq")
    gone = s1 is None
    if partition is None:
        return rec(tag or "记录库清空范围", None,
                   "黑盒: 分水岭 清前 序号=%s 清后 %s; 白盒: 断[B] 没读回 `id`(没接 J-Link / 没命中)"
                   " —— 只有一种观测, 判不了" % (s0, "没了(整库被清)" if gone else "还在(序号=%s)" % s1),
                   crit="④分区", falsify=bad)
    if partition == "all":
        if gone:
            return rec(tag or "记录库清空范围", True,
                       "白盒 id=ID_AllRecd × 黑盒 分水岭 序号=%s→无 —— 两种观测一致: 本地路(编程态)走整库"
                       "分支 ⇒ :9247 的判定必被跳过 ⇒ 判据①在本台不可达**被实测坐实**(不再是推断)" % s0,
                       crit="④分区", falsify=bad)
        return rec(tag or "记录库清空范围", False,
                   "白盒 id=ID_AllRecd(应整库清) × 黑盒 分水岭 序号=%s→%s **没被清** —— 两种观测打架, "
                   "必有一方错, 报代码侧" % (s0, s1), crit="④分区", falsify=bad)
    if gone:
        return rec(tag or "记录库清空范围", False,
                   "白盒 id=ID_AllMeter(只清到电表清零为止) × 黑盒 分水岭 序号=%s→**没了** —— 实清范围"
                   "比白盒说的宽, 两种观测打架, 报代码侧" % s0, crit="④分区", falsify=bad)
    return rec(tag or "记录库清空范围", True,
               "白盒 id=ID_AllMeter × 黑盒 分水岭 序号=%s→%s 仍在 —— 两种观测一致: 非编程态分区 ⇒ :9247 的"
               "判定为真 ⇒ 判据①(清零记录新增)在本台**本应可达**, 请核判据①那一行" % (s0, s1),
               crit="④分区", falsify=bad)


def clear_freeze_evidence(cls_name, pre, post, tag=""):
    """5-4 判据③「冻结记录被清」(DLT645App.c:9257 Clear_FrezData(ID_AllFrez)) → rec。"""
    nm = tag or "%s冻结被清" % cls_name
    bad = _CLEAR_FALSIFY["③冻结"]
    s0 = (pre or {}).get("seq")
    if s0 is None:
        return rec(nm, None, "%s冻结 清前就读不到记录 —— 本台无基线, 无从比对" % cls_name,
                   crit="③冻结", falsify=bad)
    s1 = (post or {}).get("seq")
    if s1 is None:
        return rec(nm, True, "%s冻结 清前 序号=%s → 清后读不到该记录(已被清)" % (cls_name, s0),
                   crit="③冻结", falsify=bad)
    if s1 < s0:
        return rec(nm, True, "%s冻结 清前 序号=%s → 清后 序号=%s(被清/重置)" % (cls_name, s0, s1),
                   crit="③冻结", falsify=bad)
    if s1 == s0:
        # **ok=None 而不是 True/False** 的理由必须跟着记录走 —— 否则这一条会被读成"冻结没被清 = 固件坏了"。
        return rec(nm, None,
                   "%s冻结 清前 序号=%s → 清后 序号=%s 一字未动, 与 :9257 Clear_FrezData(ID_AllFrez) "
                   "的预期不符。不判过也不判不过: 本表结算/小时冻结记录写【外部存储】(画像 NOTES 第1条"
                   "『管理芯 RAM 无命名台账顶序号』), 若 698 读回的正是外部存储那份, 那它就不是固件刚清"
                   "的那份 —— 这种口径下本台**看不到**清没清。要证须换观测口径" % (cls_name, s0, s1),
                   crit="③冻结", falsify=bad)
    return rec(nm, False, "%s冻结 序号 由 %s **升到** %s —— 清零后反而多出一条, 报代码侧" % (cls_name, s0, s1),
               crit="③冻结", falsify=bad)


def clear_elec_evidence(pre, post, di="00000000", tag=""):
    """5-4 判据②「总电能归零」 → rec。0 负载台面上这条**没有区分力**, 如实记 ok=None(不许当通过)。"""
    def hx(b):
        return b.hex(" ").upper() if b else "(无应答)"
    nm = tag or "总电能 %s 归零" % di
    bad = _CLEAR_FALSIFY["②电量"]
    if pre is None or post is None:
        return rec(nm, None, "读不到(清前=%s 清后=%s)" % (hx(pre), hx(post)), crit="②电量", falsify=bad)
    if any(pre) and not any(post):
        return rec(nm, True, "清前=%s → 清后=%s(归零)" % (hx(pre), hx(post)), crit="②电量", falsify=bad)
    if not any(pre):
        return rec(nm, None,
                   "清前=%s 本就是全 0 —— 0 负载台面上『清过』与『没清』读数一模一样, 本判据在本台"
                   "**无区分力**; 照报, 不当通过" % hx(pre), crit="②电量", falsify=bad)
    return rec(nm, False, "清前=%s → 清后=%s **未归零**, 报代码侧" % (hx(pre), hx(post)),
               crit="②电量", falsify=bad)


def clear_rebuild_evidence(pre_prog, write_ok, post_prog, tag=""):
    """5-4 判据⑤「清后存储重建正常」: 清完记录库**再落一条新记录**, 看它落不落得下 → rec。

    证法(不需要任何密钥, 台面今天就能跑): 清零后调一次 645 0x14 写参量 —— 该路落库只以
    `comSta == OK_FRAME` 为判定(DLT645App.c:2926 `Recd_Program645`), 与判据①那些准入条件无关;
    写完读 30120B0A(编程记录)。落得下 ⇒ 记录对象清完仍可读写; 落不下而写本身成功 ⇒ :9247 把记录区
    (Platform/RecdData.c:819-822 整段写 0)的写指针/链表头一并废了, 那正是"清完再也记不下事"的真实
    故障形态 —— 本判据把它变成可证伪的预测。

    pre_prog/post_prog: `read_event_row(ser, PROGRAM_EV)` 的返回(清后写前 / 写完读); 清后写前通常是
      `seq=None`(库刚被清), `event_advanced` 已把"前条无记录"当正常, 不算失败。
    write_ok: 写参量这件事本身成没成(`billday_rw_roundtrip` 的 all_ok)。**没成就记 ok=None** ——
      前置没发生 → 既不是"重建成功"也不是"重建失败", 别拿它充数。
    """
    nm = tag or "清后记录库重建"
    bad = _CLEAR_FALSIFY["⑤重建"]
    if not write_ok:
        return rec(nm, None,
                   "落库触发(645 0x14 写参量)本身没成 / 被拒 —— 没产生新记录, 本条**未证**; "
                   "这既不是重建成功, 也不构成重建失败(前置就没走通)", crit="⑤重建", falsify=bad)
    ok, why = event_advanced(post_prog, pre=pre_prog)
    # ⚠ 这里 `ok is None` **不单列一支**: 上面 `write_ok` 已经确立"落库触发被受理了"这个前提
    #   (串口层 OK_FRAME), 前提成立之后"读不回记录"就是代码侧的事(存储没重建), 不是"没做成"。
    #   别把 `event_advanced` 的三态无脑照搬 —— 它自己不知道调用方手上有没有那个前提。
    if ok:
        return rec(nm, True,
                   "清后写参量落库读回正常(编程记录 %s) —— 记录对象清零后仍可正常读写" % why,
                   crit="⑤重建", falsify=bad)
    return rec(nm, False,
               "写参量成功了(串口层 OK_FRAME)却落不下编程记录(%s) —— 记录区清零后写不进去, "
               "存储没重建, 报代码侧" % why, crit="⑤重建", falsify=bad)


# ============================ 5-x 事件触发积木(清零/事件清零/跳合闸 · 2026-09-10) ============================
# 三个 645 触发动作: 发命令(低风险/高风险各异) → 由固件自动落一条事件记录 → 再用 read_event_row 读回判过。
# 帧布局源与常量见文件头 5-x 段; 三帧均【未实测】, 首次发前先 dry。
def clear_meter(ser, wait=3.0):
    """发送 → 管理芯: 645 0x1A 电表清零(触发 5-4『电表清零』事件记录) → verdict. 本函数自打印.
    ⚠ 高风险动作: 清空 电量/需量/冻结, 并落一条永久『电表清零』记录(DLT645App.c:9249 Recd_ClrMeter)。
    前置: 先 enter_factory(编程态 Is_EnablePrg 才过 :3166 判定)。"""
    return send("645.clear.meter", wait=wait, ser_shared=ser)["verdict"]


def clear_event(ser, wait=3.0):
    """发送 → 管理芯: 645 0x1B 事件清零(触发 5-5『事件清零』记录) → verdict. 本函数自打印.
    ⚠ 固件简单路径【只认全清 0xFFFFFFFF】(DLT645App.c:3355: dis!=FFFFFFFF→ER_D0D1); 按标识部分清
    (如 0x033000FF)只能走远程/加密 LEN=0x1C 路, 本台不可用 → 本动词只发全清。
    前置 enter_factory。副作用: 清事件块 + 落一条永久『事件清零』记录(Recd_ClrEvent)。"""
    return send("645.clear.event", wait=wait, ser_shared=ser)["verdict"]


# ==================== 5-5『事件清零』判据与证据(2026-09-11 建) ====================
# 判据源 = project/knowledge/_whitebox_ledger/ledger.md 5-5「观察与判据」:
#   只清指定块或全清与帧标识一致; 永久记录保留且新增一条; 无权限被拒(返回拒绝且原事件未动)。
#
# ---- 源码口径(Application/DLT645App.c `CMD_ClearEvent`, 2026-09-11 逐行核过) ----
#   · 命令判定(:3313-3318): 简单路径须 `LEN==0x0C` 且 `DI0==0x02` 且 `Is_EnablePrg()==TRUE`,
#     否则 `return ER_PSWD`。⚠ **这一处 return 与 LEN/DI0 两处共用同一出口** —— 反汇编实测
#     三条路都跳到 0x36482(`movs r0,#2`), 所以"停在 ER_PSWD 出口"不能单独区分是哪一条不满足;
#     本次发的是标准 LEN=0x0C/DI0=0x02 帧, 于是到达该出口只能是非编程态判定 —— 但要**明说**。
#   · :3321 `if (dis != 0xFFFFFFFF) return ER_D0D1;` —— **无条件**, 在查表(:3328)之前, 且**在
#     if/else 之外** ⇒ **两条路(简单 + 远程)都过它**。远程路 :3309 `Copy_Data(DAT4,DAT12,4)` 先把
#     解密后的标识搬进 DAT4, :3320 仍从 DAT4 取 dis ⇒ 照样撞这句。**结论: 部分清整条是死代码**,
#     不是"本台缺通道"(2026-09-11 订正, 见 ⑤ 的 unprovable 声明)。
#   · :3349 `Clear_RecdData(ID_AllEvent)` → Platform/RecdData.c:801-804 把 EEPROM
#     `[IE_RecdStr, IE_ClrEvent)` 整段写 0 ⇒ **清掉全部事件块**; 而 ID_ClrEvent(事件清零, 枚举 90)/
#     ID_ClrMeter 两条记录在区界**之外** ⇒ 天然保留(RecdData.h:113 注释: ID_ClrEvent 必须倒数第二,
#     "要清掉的事件都在它前面") ⇒ 这就是判据③b"永久记录保留"的**结构**依据, 不是表态断言。
#   · :3392-3393 全清支把帧内标识**归一化**为 `0x43000500`(698 事件起始化 OAD)后才落库。
#   · :3399 `Recd_ClrEvent(&pFrame[DAT4], &pFrame[DAT0])` → TaskRecord.c:941 落 14B 记录
#     (发生时刻.6 + 操作者代码.4 + **数据标识码.4**), 标识码取的就是 DAT4..DAT7。
#
# 帧偏移(DLT645App.c:44-88 的 enum, 2026-09-11 核对):
#   STR1=0 ADR0..5 CMD=8 LEN=9 DI0=10 DI1..3 DAT0=14 … **DAT4=18** DAT5=19 DAT6=20 DAT7=21
#   ⇒ 帧内标识 = `pFrame[18..21]` 小端; 操作者 = `pFrame[14..17]`。这正是规格「帧 index18-21」的来处。
CLR_EV_DIS_ALL = P.CLR_EV_DIS_ALL
CLR_EV_DIS_SAVED = P.CLR_EV_DIS_SAVED
CLR_EV_DIS_PART = P.CLR_EV_DIS_PART
CLR_EV_ER_PSWD = P.CLR_EV_ER_PSWD
CLR_EV_ER_D0D1 = P.CLR_EV_ER_D0D1
CLR_EV_ST_PSWD = P.CLR_EV_ST_PSWD
CLR_EV_ST_D0D1 = P.CLR_EV_ST_D0D1
CLR_EV_CMD_OK = P.CLR_EV_CMD_OK
CLR_EV_CMD_ERR = P.CLR_EV_CMD_ERR
CLR_EV_ID_PRG = P.CLR_EV_ID_PRG
CLR_EV_ID_CLR = P.CLR_EV_ID_CLR
CLR_EV_IDX_DIS = P.CLR_EV_IDX_DIS
CLR_EV_BPT_STORE = P.CLR_EV_BPT_STORE
CLR_EV_BPT_GATE = P.CLR_EV_BPT_GATE
CLR_EV_VARS_DIS = P.CLR_EV_VARS_DIS


_CLR_EV_FALSIFY = {
    "①": "固件把全清也判成无权限/参数错 ⇒ 回 0xDB 或 0xD4 而不是 0x9B, 且 :3399 根本不会被执行",
    "②": "若 :3392-3393 的归一化没执行 ⇒ 读到 0xFFFFFFFF(帧内原值); 若改写成别的值 ⇒ 读到那个值",
    "③a": "全清不抹事件块(只清了个别块, 或 Clear_RecdData 的实现只写自己那一块) ⇒ 清前写下的『编程』记录还在",
    "③b": "『事件清零』记录自己被清掉(NUM_ClrEvent=0 或区界算错 ⇒ ID_ClrEvent 落在被抹区间内), "
           "或受理了却没落新记录(Recd_ClrEvent 的判定没走通)",
    "④a": "非编程态也放行(判定失效/Is_EnablePrg 恒真) ⇒ 应答 0x9B 而不是 0xDB, 或状态字节不是 0x04",
    "④b": "被拒前已经动过记录库(拒之前先清) ⇒ 『事件清零』记录的时刻/序号会变",
}


def clear_event_criteria():
    """5-5 的**预设条目**(源 = ledger.md 5-5 的「观察与判据」I 列)。测试前定死, 证据照它认领。

    ⚠ ⑤ 声明 `unprovable`, 且理由比"本台缺通道"更硬 —— **固件结构性封死**(2026-09-11 逐行核过,
      订正了本文件先前那句错误的归因): :3321 的 `if (dis != 0xFFFFFFFF) return ER_D0D1;` 在 if/else
      **之外**、一层缩进、无 #ifdef 包着 ⇒ **两条路都得过它**; 远程路(LEN==0x1C)在 :3309 先
      `Copy_Data(&pFrame[DAT4], &pFrame[DAT12], 4)`(`Copy_Data(pDest,pSour,len)` ⇒ 把 DAT12 拷进
      DAT4), :3320 照样从 DAT4 取 `dis`, **还是撞 :3321**。于是任何 645 帧下 `dis` 只能是 0xFFFFFFFF,
      表里的部分清分支与 :3337 的 style 判定**全是死代码** —— 密钥/认证/加密机都改变不了。
      实测复核(「先证能力再改判据」): 发 0x033000FF 得 0xDB + 状态字节 0x02=ER_D0D1。
      ⚠ 698 侧**不是同一件事**: `430005 事件起始化`(DLT698App.c:11347)同为无条件全清;
      另一条 OMD `way==1`(:11410-11428)是按**记录对象**逐个清(粒度 OAD、且显式跳过 ID_ClrMeter),
      与 645 的 4B 标识不是同一条判据。
    """
    return {
        "①": "编程态发 645 0x1B 全清(帧内标识 0xFFFFFFFF)被受理(应答 0x9B 且数据域长 0)",
        "②": "落库标识与固件口径一致: :3392-3393 把全清标识归一为 0x43000500 后交 Recd_ClrEvent(:3399)",
        "③a": "全清抹掉事件块: 清前写下的『编程』记录(0x12)清后消失",
        "③b": "永久『事件清零』记录在区界之外 ⇒ 保留且新增一条(发生时刻推进)",
        "④a": "非编程态被拒: 应答 0xDB 且错误状态字节 0x04(= 1<<ER_PSWD, DLT645Link.c:519)",
        "④b": "被拒时原事件未动(『事件清零』记录的时刻/序号一字未变)",
        "⑤": {"text": "按标识部分清(如 0x033000FF)只清该块、其余不动",
               "unprovable": "**固件结构性封死**(2026-09-11 订正 —— 早先记作『本台缺远程/加密通道』, "
                             "那是把结论说轻了): :3321 的条件句 `if (dis != 0xFFFFFFFF) return ER_D0D1;` "
                             "在 if/else **之外**、一层缩进、无 #ifdef ⇒ **两条路都得过它**。"
                             "远程路(LEN==0x1C 且 DI0==0x98)在 :3309 先 "
                             "`Copy_Data(&pFrame[DAT4], &pFrame[DAT12], 4)`(`Copy_Data(pDest,pSour,len)` "
                             "⇒ 把 DAT12 拷进 DAT4), :3320 照样从 DAT4 取 dis, **还是撞 :3321**。"
                             "⇒ 任何 645 帧下 dis 只能是 0xFFFFFFFF: 表里 0x031100FF/0x033000FF/0x033014FF/"
                             "0x033500FF/0x033600FF 那些分支、以及 :3337 按 style 拦费率时段/阶梯/插卡事件的"
                             "那道判定,**全是死代码**; 密钥、安全认证、加密机都改变不了这件事。"
                             "要证须**改固件**(把那句挪进 else 支, 或让远程路按 DAT12 的标识放行)。"
                             "⚠ 698 侧不是同一件事: `430005 事件起始化`(DLT698App.c:11347)同为无条件全清; "
                             "另一条 OMD way==1(:11410-11428)是按**记录对象**逐个清(粒度是 OAD、且显式跳过 "
                             "ID_ClrMeter), 与 645 的 4B 标识不是同一条判据。"},
        "⑥a": "645 0x19 清最大需量 受理后, 管理芯内当前的需量及发生时间被清空(规范 6-3 需量清零第 1 条)",
        "⑥b": "每次需量清零落一条记录: 该口总次数 +1、最近 10 次时刻可读(规范 5-5 事件清零第 1 条)",
    }


def clr_event_send(ser, wait=2.5, dis=None, quiet=False):
    """发 645 0x1B 事件清零 → `{cmd, err, rx, tx, dis}`。**不判**(判定在证据积木里, 免得两套口径)。

    打印: 传输层的帧行(收/发各一行, `tx_recv` 自带)+ 本函数的一行操作头 —— 判定留白, 由 `clr_event_*_evidence` 给。
    dis=None  → 发目录里那条全清帧(`645.clear.event`, 帧内标识 0xFFFFFFFF) —— 走 `send()` 的
                单一组帧点, 免得在库里再拼一遍(`_json=True` 只压它的自判自印, 帧行照出)。
    dis=0x... → 组一条**只换标识**的同形帧(`DAT4..DAT7` = dis 小端), 供"部分清"的负向复核用。
    err = 645 异常应答的状态字节(0xD4/0xDB 才有; 已由 `decode_645_reply` 解掉 +0x33)。
    """
    if dis is None:
        if not quiet:
            print("\n== 5-5 发送 645 0x1B 事件清零 | 帧内标识 = 0x%08X(全清) ==" % CLR_EV_DIS_ALL)
        res = send("645.clear.event", wait=wait, _json=True, ser_shared=ser)
        rx_hex, tx = res.get("rx") or "", res.get("tx") or ""
        rx = bytes.fromhex(rx_hex.replace(" ", ""))
    else:
        if not quiet:
            print("\n== 5-5 发送 645 0x1B 事件清零 | 帧内标识 = 0x%08X(按标识部分清) ==" % int(dis))
        data = bytearray(CLEAR_EVENT_DATA)
        data[8:12] = int(dis).to_bytes(4, "little")     # CLEAR_EVENT_DATA[8:12] = DAT4..DAT7
        frame = frame_645(0x1B, bytes(data), addr=TABLE_ADDR)
        rx = send_frame(ser, frame, wait=wait, tag="clr_ev_dis%08X" % dis, peer="管理芯",
                        what="0x1B 事件清零 帧内标识=0x%08X(按标识部分清) 原始应答" % int(dis))
        tx = frame.hex(" ").upper()
    cmd, seg, _note = decode_645_reply(rx)
    err = seg[0] if (seg and cmd in (0xD4, 0xDB)) else None
    return {"cmd": cmd, "err": err, "rx": rx.hex(" ").upper(), "tx": tx, "dis": dis}


def clr_event_dis_read(vals):
    """断点读回的 `pFrame[18..21]` → `(dis, 文本)`; 拼不成(缺字节/读不到)回 `(None, 原因)`。

    小端: `pFrame[18] | [19]<<8 | [20]<<16 | [21]<<24`(与固件 :3320 那行同序)。
    `gdb_ints` 归一出整数(它认 `0x..`/十进制/`15 '\\017'`, 并把 `<optimized out>`/`No symbol`
    归成空 ⇒ 这里读不到就返回 None, **不猜 0**) —— 与 3-2 的 `swTime`/`g_RatePara` 同一招。
    """
    out, raw = 0, []
    for i in (0, 1, 2, 3):
        e = "pFrame[%d]" % CLR_EV_IDX_DIS[i]
        got = gdb_ints(vals.get(e))
        if not got:
            return None, "%s 没读回(值=%r)" % (e, vals.get(e))
        n = got[0] & 0xFF
        out |= n << (8 * i)
        raw.append("%02X" % n)
    return out, "pFrame[18..21]=%s → 0x%08X" % (" ".join(raw), out)


def clr_event_accept_evidence(trig_r, tag=""):
    """5-5 判据① → rec: 全清帧被**受理**(应答 0x9B 且数据域长 0, 即无异常状态字节)。

    吃的是 `GD.trigger` 的**整份返回**(库认识自己的返回结构; 与 5-4 的 `clear_library_evidence`
    吃 `id` 原文同一处分寸): 串口观测的读数在 `result`(= `clr_event_send` 的 dict), 断点观测的在
    `hit`/`vars`。本块**只看串口那一半** —— "表受理了没有"本就属对外行为。
    无 `result`(触发器抛错)= 没做成 ⇒ `ok=None`, 不写"通过"。
    """
    bad = _CLR_EV_FALSIFY["①"]
    nm = tag or "全清受理(645 0x1B 应答)"
    r = (trig_r or {}).get("result")
    if not isinstance(r, dict):
        return rec(nm, None, "这一次没有串口读数(触发器抛错: %r) —— 未证"
                   % ((trig_r or {}).get("error"),), crit="①", falsify=bad)
    cmd, err = r.get("cmd"), r.get("err")
    if cmd == CLR_EV_CMD_OK:
        return rec(nm, True, "应答 0x%02X 且数据域长 0(无异常状态字节) —— 受理" % cmd,
                   crit="①", falsify=bad)
    if cmd is None:
        return rec(nm, None, "无 645 应答帧(表没回; RX=%s) —— 未证" % r.get("rx"),
                   crit="①", falsify=bad)
    return rec(nm, False, "应答 0x%02X + 异常状态字节 %s, 期望 0x%02X —— 未受理"
               % (cmd, ("0x%02X" % err) if err is not None else "(无)", CLR_EV_CMD_OK),
               crit="①", falsify=bad)


def clr_event_id_evidence(trig_r, tag=""):
    """5-5 判据② → rec: 断点停在 :3399 读到的**落库标识**是否 == 固件归一化值 0x43000500。

    这是「与帧标识一致」的**白盒那一半**, 比串口读回强在: 串口只能读回"记录落没落", 读不到固件
    **交给落库函数的那个 4B 标识**(记录里的标识码列要另配 RCSD 才读得到, 本台未校准)。
    吃 `GD.trigger` 的整份返回; `hit is None` ⇒ 没命中/没接 J-Link ⇒ `ok=None`(没做成), **不写"通过"**。
    """
    bad = _CLR_EV_FALSIFY["②"]
    nm = tag or "落库标识(:3399 Recd_ClrEvent 实参)"
    hit, vals = (trig_r or {}).get("hit"), ((trig_r or {}).get("vars") or {})
    if hit is None:
        return rec(nm, None, "断点没命中 / 没接 J-Link ⇒ 落库入参未取证", crit="②",
                   obs=judge.DEBUG, falsify=bad)
    dis, txt = clr_event_dis_read(vals or {})
    if dis is None:
        return rec(nm, None, "命中了但标识读不回: %s" % txt, crit="②", obs=judge.DEBUG, falsify=bad)
    if dis == CLR_EV_DIS_SAVED:
        return rec(nm, True, "%s == 0x%08X(帧内 0x%08X 经 :3392-3393 归一) —— 落库标识与固件口径一致"
                   % (txt, CLR_EV_DIS_SAVED, CLR_EV_DIS_ALL), crit="②", obs=judge.DEBUG, falsify=bad)
    if dis == CLR_EV_DIS_ALL:
        return rec(nm, False, "%s == 0x%08X = **帧内原值** —— 归一化(:3392-3393)没执行, 报代码侧"
                   % (txt, CLR_EV_DIS_ALL), crit="②", obs=judge.DEBUG, falsify=bad)
    return rec(nm, False, "%s == 0x%08X, 既不是归一值 0x%08X 也不是帧内值 0x%08X —— 标识被改错, 报代码侧"
               % (txt, dis, CLR_EV_DIS_SAVED, CLR_EV_DIS_ALL), crit="②", obs=judge.DEBUG, falsify=bad)


def clr_event_block_evidence(pre_prg, post_prg, tag=""):
    """5-5 判据③a → rec: 全清**抹掉事件块**没有 —— 用清前**种下的**『编程』记录当要读的那一条。

    为什么要种: 本台事件库可能本就是空的, 空 → 空**与**"清了"长得一模一样(假通过)。所以前置
    用 `billday_rw_roundtrip` 写一次参量(自带恢复 ⇒ 表无净变, 但固件必落一条 645 编程记录),
    那一次既是"要读的那一条"又是它的**前提自证**。种不进去 ⇒ `ok=None`(没做成), 不写"通过"。
    """
    bad = _CLR_EV_FALSIFY["③a"]
    nm = tag or "全清抹掉事件块(编程记录为要读的那一条)"
    s0, s1 = (pre_prg or {}).get("seq"), (post_prg or {}).get("seq")
    if s0 is None:
        return rec(nm, None, "清前种不下『编程』记录(基线为空)⇒ 清后为空没有区分力, 本条未证",
                   crit="③a", falsify=bad)
    if s1 is None:
        return rec(nm, True, "清前『编程』记录 序号=%s、时刻=%s → 清后无记录 —— 事件块被抹"
                   % (s0, (pre_prg or {}).get("ts")), crit="③a", falsify=bad)
    return rec(nm, False, "清前『编程』记录 序号=%s → 清后 **还在** 序号=%s(时刻=%s) —— 全清没抹到该块, "
               "报代码侧(:3349 Clear_RecdData(ID_AllEvent) 的区界或实现不符)"
               % (s0, s1, (post_prg or {}).get("ts")), crit="③a", falsify=bad)


def clr_event_keep_evidence(pre_ev, post_ev, t0=None, tag=""):
    """5-5 判据③b → rec: 永久『事件清零』记录**保留且新增一条**。

    复用 `event_advanced`(它已把"前条读不到(库刚被清)"当正常, 判"本次触发落了新记录")。
    与 ③a 合起来才是完整的「它被清、我留下」——单看本条会让"什么都没清"也通过。
    时刻下界 `t0` 取触发前表钟 ⇒ 挡住"读到的是库里的旧记录"。
    """
    bad = _CLR_EV_FALSIFY["③b"]
    nm = tag or "永久『事件清零』记录保留且新增"
    ok, why = event_advanced(post_ev, pre=pre_ev, not_before=t0)
    if ok is None:
        return rec(nm, None, "%s —— 本条未证" % why, crit="③b", falsify=bad)
    if ok:
        # ⚠ `event_advanced` 的 `why` **自己就带**「前=… 后=…」 —— 别再前缀一次(会印成
        #   「前=(无记录) 后=前=(无记录) 后=…」, 2026-09-11 自检输出里当场看见)。
        return rec(nm, True, why, crit="③b", falsify=bad)
    return rec(nm, False, "%s —— 全清把『事件清零』记录自己也抹了, 或受理了却没落新记录, 报代码侧" % why,
               crit="③b", falsify=bad)


def clr_event_reject_evidence(rej, pre_ev, post_ev, tag=""):
    """5-5 判据④a/④b → (rec, rec): 非编程态被拒(错状态字节=1<<ER_PSWD) **且** 原事件未动。

    两半**必须同挂**, 且 ④b 的"未动"要拿 ④a 的"被拒"当前提 —— 只发"记录没变"是空话(没触发的动作不会造成状态, 证不了任何事): 固件若**根本没回拒**而是安静地没清, ④b 照样绿。故 ④a 不成立时 ④b 记
    `ok=None`(没测成), 不记 True。
    """
    bad_a, bad_b = _CLR_EV_FALSIFY["④a"], _CLR_EV_FALSIFY["④b"]
    cmd, err = (rej or {}).get("cmd"), (rej or {}).get("err")
    rej_ok = (cmd == CLR_EV_CMD_ERR and err == CLR_EV_ST_PSWD)
    if cmd is None:
        rec_a = rec("非编程态被拒(645 应答)", None, "无 645 应答帧(表没回) —— 未证", crit="④a", falsify=bad_a)
    elif cmd == CLR_EV_CMD_OK:
        rec_a = rec("非编程态被拒(645 应答)", False,
                    "应答 0x9B = **受理**了 —— 非编程态也放行 ⇒ 判定失效, 报代码侧", crit="④a", falsify=bad_a)
    elif rej_ok:
        rec_a = rec("非编程态被拒(645 应答)", True,
                    "应答 0x%02X + 状态字节 0x%02X = 1<<ER_PSWD(DLT645App.c:3317 那处 return ER_PSWD) "
                    "⇒ 拒在 :3313-3318 命令判定" % (cmd, err), crit="④a", falsify=bad_a)
    else:
        rec_a = rec("非编程态被拒(645 应答)", False,
                    "应答 0x%02X + 状态字节 %s, 期望 0x%02X/0x%02X(=1<<ER_PSWD) —— 拒的理由不是『未授权』"
                    % (cmd, ("0x%02X" % err) if err is not None else "(无)", CLR_EV_CMD_ERR,
                       CLR_EV_ST_PSWD), crit="④a", falsify=bad_a)
    s0, s1 = (pre_ev or {}).get("seq"), (post_ev or {}).get("seq")
    t0, t1 = (pre_ev or {}).get("ts"), (post_ev or {}).get("ts")
    if not rej_ok:
        rec_b = rec("被拒时原事件未动", None,
                    "前置(被拒)没成立 ⇒ 『原事件未动』无从谈起(没触发的动作不会造成状态, 证不了任何事); "
                    "本次读得 前=(%s,%s) 后=(%s,%s)" % (s0, t0, s1, t1), crit="④b", falsify=bad_b)
    elif (s0, t0) == (None, None) and (s1, t1) == (None, None):
        # ⚠ 防假通过: 两次都**读不回**时 (None,None)==(None,None) 也成立 —— 若不加这一支,
        #   "表没答"会被记成"记录一字未变"。与 4-6/3-2 那些"读到栈垃圾"同类的静默假通过。
        rec_b = rec("被拒时原事件未动", None,
                    "两次都读不回『事件清零』记录(表没答/记录读被拒) ⇒ 『未动』无从比对 —— 未证",
                    crit="④b", falsify=bad_b)
    elif (s0, t0) == (s1, t1):
        rec_b = rec("被拒时原事件未动", True, "『事件清零』记录 序号=%s 时刻=%s 前后一字未变" % (s0, t0),
                    crit="④b", falsify=bad_b)
    else:
        rec_b = rec("被拒时原事件未动", False,
                    "被拒了但记录动了: 前=(%s,%s) → 后=(%s,%s) —— 拒之前先清过, 报代码侧"
                    % (s0, t0, s1, t1), crit="④b", falsify=bad_b)
    return rec_a, rec_b


def clr_event_gate_evidence(trig_r, tag=""):
    """旁证(不认领条目): 那一次**真的停在了** :3317 的 ER_PSWD 出口 —— 给 ④a 的串口结论配一次白盒落点。

    ⚠ **crit=None 是刻意的, 不是漏填**: 0x36482 这个出口被**三条路共用**(LEN!=0x0C / DI0!=0x02 /
    Is_EnablePrg 为假 —— 反汇编实测 0x3646e-0x36482 三段全跳到它), 所以"停在这儿"本身**证不了**
    是哪一条不满足, 答不出 falsify ⇒ 按 common/judge.py 的口径不算证据。它证的是"拒**不是**发生在
    更早的调度层, 而是发生在 CMD_ClearEvent 自己的判定里" —— 有用的一步, 但不够格认领条目。
    ④a 的认领只在串口那半(异常状态字节 0x04 = 1<<ER_PSWD)。
    (本帧 LEN=0x0C / DI0=0x02 已固定, 另两条不满足不了 ⇒ 实际只剩 Is_EnablePrg 一条, 但这属推理,
     不是读数, 所以仍旧只作旁证。)

    ⚠ **没命中记 `ok=None`, 不许记 False**: `judge.decide` 的 `bad` 是**全量记录**扫 `ok is False`
    (不看 crit/falsify), 于是任何一条 ok=False 都会把整轮判成「失败」。而"这个旁证没取到"是**没做成**
    (没接 J-Link / 拒发生在别处), 不是"固件不对" —— 记 False 就成了拿旁证给固件定罪。
    """
    hit, vals = (trig_r or {}).get("hit"), ((trig_r or {}).get("vars") or {})
    if hit is None:
        return rec(tag or "旁证 拒在 CMD_ClearEvent 的命令判定(:3317 ER_PSWD 出口)", None,
                   "没命中 —— 拒可能发生在更早的调度层, 或断点没下上/没接 J-Link(未取到, 不判不下)",
                   crit=None, falsify=None)
    # 停住时读这一帧自己的 LEN/DI0 —— 它们把"三条路共用同一个出口"这个不确定性**收窄掉**:
    # 若读到 LEN==0x0C 且 DI0==0x02, 那么没满足的就只剩 Is_EnablePrg 一条(另外两条已被读数排除)。
    # 这一步才是这条旁证真正的分辨力所在; 只报"停在某个地址"等于什么都没说。
    ln = gdb_ints(vals.get("pFrame[9]"))
    d0 = gdb_ints(vals.get("pFrame[10]"))
    ln = (ln[0] & 0xFF) if ln else None
    d0 = (d0[0] & 0xFF) if d0 else None
    if ln == 0x0C and d0 == 0x02:
        return rec(tag or "旁证 拒在 CMD_ClearEvent 的命令判定(:3317 ER_PSWD 出口)", True,
                   "停在 %s; 且读得帧内 LEN=0x0C、DI0=0x02 ⇒ 另两条(长度/子命令)排除了, "
                   "这个出口只能是 Is_EnablePrg()==FALSE 落下来的" % hit.where(),
                   crit=None, falsify=None)
    return rec(tag or "旁证 拒在 CMD_ClearEvent 的命令判定(:3317 ER_PSWD 出口)", None,
               "停在 %s, 但帧内 LEN=%s、DI0=%s(期望 0x0C/0x02)未读回或不符 ⇒ "
               "分不清是哪一条判定落的(未取到, 不判不下)"
               % (hit.where(), ("0x%02X" % ln) if ln is not None else "读不到",
                  ("0x%02X" % d0) if d0 is not None else "读不到"),
               crit=None, falsify=None)


def clr_event_partial_probe(ser, wait=2.5, dis=None):
    """旁证(不认领条目): 按标识部分清在该台面**被 :3323 拒** —— 给 ⑤ 的 unprovable 提供实测支撑。

    「先证能力再改判据」: 说"本台做不到"之前, 先把那个动作真发一遍。期望 0xDB + 状态字节 0x02
    (=1<<ER_D0D1, 即 `dis != 0xFFFFFFFF` 那道无条件判)。
    `crit=None` —— ⑤ 已声明 unprovable ⇒ **不许有认领者**(既说"证不了"又说"证到了"是两套话)。
    """
    dis = CLR_EV_DIS_PART if dis is None else dis
    r = clr_event_send(ser, wait=wait, dis=dis)
    cmd, err = r["cmd"], r["err"]
    ok = (cmd == CLR_EV_CMD_ERR and err == CLR_EV_ST_D0D1)
    return rec("旁证 部分标识 0x%08X 被拒(⑤ 的实测支撑)" % dis, True if ok else None,
               "应答 0x%02X + 状态字节 %s(期望 0x%02X = 1<<ER_D0D1, 出自 :3323 无条件判)"
               % (cmd if cmd is not None else -1,
                  ("0x%02X" % err) if err is not None else "(无)", CLR_EV_ST_D0D1),
               crit=None, falsify=None)


# ==================== 5-8『时钟故障』: 注入造故障 + 645 广播校时清故障(2026-09-16 建) ====================
# 判据源 = project/knowledge/_whitebox_ledger/ledger.md 5-8「观察与判据」:
#   ① 超差/倒退/超前正确判故障并写库  ② 恢复/校时后事件清除  ③ 时标正确
#
# ---- 为什么触发只能走注入(结构性, 不是"本台缺通道") ----
#   `Run_TaskTime` 的每秒分支(`TaskTime.c:265-275`): :272 把上一秒的 `g_MeterTime` 拷进
#   `g_MeterTime_Backup`, :273 从 RTC 重读 `g_MeterTime`, :275 把这两者交给
#   `Check_And_Correct_RTC(PTime=备份, Buff2=当前)`。判故障的三个条件(:1095-1097)就是比这两者:
#   `Check_DateTime(Buff2)!=TRUE`(无效) / `Comp_Data(Buff2,PTime,6)<0`(倒退) / `Diff_Days>1000`。
#   **所有写钟帧都经 `Set_MeterTime`, 它同时更新 RAM 镜像与 RTC 硬件** ⇒ 下一轮这两个值恒等
#   ⇒ `Comp_Data == 0` ⇒ 帧通道**永不判故障**。这是《调试器注入》§1 那个"写入口把值判定住"的
#   标准形状 ⇒ 不是"证不了", 是**触发通道缺一条**。
#   (645 0x14 直写管理芯 RTC 看似能绕开, 但归档文实测它"几秒内被计量芯 SPI3 回拨拉回", 与本仓每秒
#    的 `MSG_SecStep` 是**同量级的抽签** —— 正是 2-1 判据③ 踩过的那个形状, 故不用它。)
#
# ---- 断点(全部经 gdb `info line` 现核, 2026-09-16) ----
#   TaskTime.c:275    0x27f88 <Run_TaskTime+54>            注入停靠点(参数求值之前)
#   TaskTime.c:1103   0x28c30 <Check_And_Correct_RTC+76>   判据断点: 分支①(用备份时间校正)
#   DLT645App.c:734   0x33c6a <CMD_CaliTime+966>           清除断点 `Recd_TimeError(FALSE,0)`
#   ⚠ 为什么断点在 :275 而不是 :273 之后: :274 `Fetch_CRC(g_MeterTime, 6)` 会重算尾部两字节, 注在它
#     之前会被覆盖 ⇒ 判据断点永不命中 ⇒ **静默假阴性**。:275 这一停在参数求值之前, 改的是将要传进去的
#     那个数组; 且 `Check_And_Correct_RTC` 只校验 `PTime` 的 CRC(:1099) **不校验 Buff2**。
#   ⚠ 不给 `gate`: 本项**没有触发帧**(状态是我们自己造的) ⇒ 不存在"别人的一次调用"; :275 每秒执行
#     一次, 任何一秒都同等合格。(`gate` 的用途见《调试器注入》§2.3 —— 那是热路径**且有帧**时的事。)
#
# ---- 判据②的观测口径(容易判错, 单独钉住) ----
#   `Recd_TimeError(FALSE, 0)`(`TaskRecord.c:1503` 的 `str==FALSE` 支)的语义是**给这条记录补结束时间**
#   (`buff[6..11]` 写结束时间、`buff[22]/[27]` 写结束时刻电量), **不是删记录**。
#   ⇒ 「事件清除」在本固件的可观测形态是「最近一条的**结束时间**由空变成校时时刻」, **不是条数回落**。
#   ⚠ 规格「不清→核 698/:10204」那半句指向的是**死代码**: `DLT698App.c` 的整块 `case 0x400002` 被块注释
#     罩住(`/*` 起于 :10190 之前, `*/` 收于 :10254 之后) ⇒ **698 侧根本没有清除路径, 只有 645 侧**。
#     故本项不列"698 清除"条目 —— 列了就是把"测不到"伪装成"待测"。
# ---- 判据① 的"写库"那一半**受固件自己的判定管**(2026-09-16 实跑定死, 别当成固件不判故障) ----
#   `Recd_TimeError` 体首有一道判定(`TaskRecord.c:1508-1526`): 读最新一条, 若「头有(发生时间)、
#   尾无(结束时间)」⇒ `str=TRUE`(判故障要记新的一条)时**直接返 FALSE、一个字都不写**;
#   反过来, 没有未闭合记录时 `str=FALSE`(清故障要补尾巴)也返 FALSE。
#   ⇒ 台上只要已经躺着一条**未闭合**的时钟故障记录, 三个分支的 `Recd_TimeError(TRUE,0)`
#     (`:1103`/`:1115`/`:1125`)全都写不进去 ⇒ 「事件 0x2E 新增一条」这个观测口径
#     **结构上不成立** —— 它不是"固件不判故障", 是**台面状态把判定关上了**。
#   故本项**跑法是 ② 先、① 后**: 先让 645 广播校时给那条未闭合记录补上尾巴(判定就开了),
#   再注入 —— 此时"判故障并写库"才观测得到。基线里没有未闭合记录时判定本就是开的, 顺序无关。
CE_EVENT = P.CE_EVENT
CE_RCSD_END = P.CE_RCSD_END
CE_REC_CAP = P.CE_REC_CAP
CE_BPT_INJ = P.CE_BPT_INJ
CE_BPT_HIT = P.CE_BPT_HIT
CE_BPT_HIT_VARS = P.CE_BPT_HIT_VARS
CE_BPT_CLR = P.CE_BPT_CLR
CE_INJ_VARS = P.CE_INJ_VARS
CE_INJ_ASSIGN = P.CE_INJ_ASSIGN
CE_CALI_DELTA = P.CE_CALI_DELTA
CE_TOL = P.CE_TOL
CE_VARS_CLR = P.CE_VARS_CLR
CE_BP_WAIT = P.CE_BP_WAIT

AR_PLC_M = P.AR_PLC_M
AR_RPT_NUM = P.AR_RPT_NUM
AR_OAD_TIMERROR = P.AR_OAD_TIMERROR
AR_INJ_TIME = P.AR_INJ_TIME
AR_BPT_RPT = P.AR_BPT_RPT
AR_BPT_SEND = P.AR_BPT_SEND
AR_AT_VARS = P.AR_AT_VARS
AR_VARS_SEND = P.AR_VARS_SEND
AR_TIME_VARS = P.AR_TIME_VARS
AR_POST_VARS = P.AR_POST_VARS
AR_SETTLE = P.AR_SETTLE
AR_KEEP_ZERO = P.AR_KEEP_ZERO
AR_FOLLOW_BIT = P.AR_FOLLOW_BIT
AR_ASSIGN_TIME = P.AR_ASSIGN_TIME
AR_ASSIGN_RPT = P.AR_ASSIGN_RPT
AR_APDU_EVENT = P.AR_APDU_EVENT
AR_APDU_FOLLOW = P.AR_APDU_FOLLOW
AR_FRAME_TAIL = P.AR_FRAME_TAIL
AR_FRAME_CMD = P.AR_FRAME_CMD
AR_FRAME_AF = P.AR_FRAME_AF
AR_FRAME_CA = P.AR_FRAME_CA
AR_MAX_SEND = P.AR_MAX_SEND
AR_GAP_LAST = P.AR_GAP_LAST
AR_GAP_IDLE = P.AR_GAP_IDLE
AR_SEND_WAIT = P.AR_SEND_WAIT
AR_APDU_ARRAY = P.AR_APDU_ARRAY


_CE_FALSIFY = {
    "①": "固件在年字节倒退时不进判定(守卫被短路) ⇒ 不调 Recd_TimeError(TRUE,…)(TaskRecord.c:1508 "
         "不命中), 且事件 0x2E 不新增一条",
    "②": "校时成功后不清故障(:734 的 Recd_TimeError(FALSE,0) 被删/被判定挡) ⇒ 结束时间保持空 且 :734 不命中",
    "③": "时标取自**校正后**的时间而非故障发生的那一刻 ⇒ 发生时间会等于校时时刻, 而非注入时刻",
}


def clock_error_criteria():
    """5-8 的**预设条目**(源 = ledger.md 5-8 的「观察与判据」I 列)。测试前定死, 证据照它认领。

    ⚠ 四条都**可证**(①②③ 靠注入造故障, ⑦ 靠连造), 故没有 `unprovable` 条目。台面没接 J-Link 时
      ① 落 `ok=None`(造不出故障 ⇒ 没做成)→ 未定论 —— 那与"固件不判故障"是两件事, 不许混。
      ⑦ 只走帧通道(注入 + 645 闭合帧成对推进), 没会话时同样 `ok=None`。
    ⚠ ① 的**写库那一半**要先把台面上那条未闭合记录收掉(固件自己的判定, 见本节头那张说明)
      ⇒ 本项**跑法是 ② 先、① 后**; 没先跑 ② 时 ① 的写库半边如实记 `ok=None`, 不是 FAIL。
    """
    return {
        "①": "表钟被造出倒退 ⇒ 固件判『时钟故障』(调 `Recd_TimeError(str=TRUE)`, `TaskRecord.c:1508`)"
             "并在事件 0x2E 新增一条",
        "②": "校时成功后事件清除(:734 Recd_TimeError(FALSE,0)): 最近一条的**结束时间**被补上",
        "③": "时标正确: 故障的发生时间 ≈ 触发时刻, 结束时间 ≈ 校时时刻",
        # 规范「事件记录」那一节的「最近 N 次」: 容量在固件里就是 `NUM_TimeError`(= CE_REC_CAP,
        #   RecdData.h:170)。本条判它**封顶在容量上**且顶掉的是最早那条。
        "⑦": "最近 %d 次『时钟故障』封顶在容量上: 连造 %d 回后该口条数停在 %d, 且第 %d 条"
             "(最早那条)被顶掉 —— 其序号与发生时刻不再是连造前那对"
             % (CE_REC_CAP, CE_REC_CAP + 1, CE_REC_CAP, CE_REC_CAP),
    }


def ce_inject_allow():
    """本子项要注入的表达式名字 —— 脚本构造会话时喂 `inject_allow=`(库**不替脚本开会话**)。

    白名单按名字点名、不按值: 改注入什么值(今天 −1 年, 明天换个方向)名单一个字都不用动。
    """
    return tuple(e for e, _v in CE_INJ_ASSIGN)


def ce_blank(ts):
    """结束时间列『还没补』的两种形态: 解不出(None) 或 全零时标('0000-00-00 00:00:00')。"""
    return (not ts) or str(ts).startswith("0000-00-00")


def _ce_secs(a, b):
    """两个钟串的秒差(b − a); 任一读不出 → None(**不猜 0**)。
    解析与平移一律走库里的 `clock_dt`/`clock_add`(通用钟串积木), 本函数只做减法。"""
    da, db = clock_dt(a), clock_dt(b)
    return None if (da is None or db is None) else (db - da).total_seconds()


def read_clock_error_event(ser, pos=1, chip=None, wait=3.0):
    """读事件 0x2E『时钟故障』第 pos 条 → `{"occur": rec, "end": rec}`(两次记录读回)。

    列定义源 = 固件 `TAB_TimeError`(`DLT698App.c:2739`): 序号 `20220200` /
    **发生时间 `201E0200`(存储偏移 0)** / **结束时间 `20200200`(偏移 6)** / 事件源 `20240200`。
    两列都是 `T_DateTimeS`, 各发一次 GetRequestRecord(**只换 RCSD 那一列**), 复用 `read_event_row`
    的解码(它取记录头之后第一个 `0x1C`)。请求 OAD 经 `record_req_oad` 归一成 `302E0200`。
    ⚠ 结束时间列若解不出/为全零, 就是"还没补" —— 由 `ce_blank` 统一判, **不猜 0**。
    """
    return {"occur": read_event_row(ser, CE_EVENT, pos, chip=chip, wait=wait, col="发生时间"),
            "end": read_event_row(ser, CE_EVENT, pos, rcsd=CE_RCSD_END, chip=chip, wait=wait,
                                  col="结束时间")}


def clock_error_inject_evidence(ser, inj, at=None, watch=None, assigns=None,
                                at_vars=None, watch_vars=(), timeout=30.0, pre=None, rd=None):
    """5-8 判据① + ③(发生侧) —— 注入造出『时间倒退』, 固件应判故障并写库。

    返回 `(recs, why, scope)`: recs = 证据记录列表, why = 逐条说明, `scope=None` = 半中途中止(没做成)。
    `inj is None`(没接 J-Link)⇒ 返回 `(None, …, None)`: **故障造不出来 ⇒ 这一半不做**。
    不许拿"读不到故障事件"去写"固件没判" —— 那是拿没做成的观测给固件定罪。
    ⚠ 这一半在本固件**没有可降级的黑盒替身**(与 5-3/5-5 不同): 事件不会自己产生,
      没会话时不是"换个通道看", 是**什么都没得看**。
    ⚠ **写库那一半受固件自己的判定管**(本节头那张说明): `pre` 里最近一条若**未闭合**, 固件在
      `TaskRecord.c:1508-1526` 就返 FALSE 了 ⇒ 事件不会新增 —— 那是**台面属性**, 记 `ok=None`,
      不是 FAIL。要观测到写库, 调用方须**先跑判据②**(让校时给那条尾巴补上), 再调本函数。

    `rd` = 读回回调二元组 `(读表钟, 读事件)`; 缺省即真函数。**这是给"只想验判定逻辑"的调用方
      留的口子** —— 判定逻辑(三态分不分得开)本身与 698 应答编码无关, 不该为了验它去搭一台真表。
    """
    at = at or CE_BPT_INJ
    watch = watch or CE_BPT_HIT
    watch_vars = tuple(watch_vars or CE_BPT_HIT_VARS)
    assigns = assigns or CE_INJ_ASSIGN
    at_vars = tuple(at_vars or CE_INJ_VARS)
    if inj is None:
        return None, "5-8: 没接 J-Link ⇒ 注入造不出『时间倒退』⇒ 故障事件不会产生, 这一半不做", None
    _clock, _event = rd or (read_clock, read_clock_error_event)
    if pre is None:
        pre = _event(ser)
    t0 = _clock(ser, quiet=True)                           # 触发时刻的下界(与记录时标比)
    r = inj(at, assigns, watch=watch, watch_vars=tuple(watch_vars), at_vars=at_vars,
            label="注入 g_MeterTime 年字节 −1(造倒退)", timeout=timeout,
            crit="①", falsify=_CE_FALSIFY["①"])
    post = _event(ser)
    recs, why = [], []

    # ---- ① 白盒: 注入点停到 + 判据断点(TaskRecord.c:1508)命中, 且停时 `str=TRUE` ----
    # ⚠ `inj`(`GD.inject_hit`)交回来的是 **judge 记录**, 它只有 `name/ok/detail/crit/obs/falsify/trig`
    #   —— **没有 `hit`** 那个键(`hit` 在 `record()` 里就被拿去定 `ok` 并折进 `detail` 了)。
    #   判"停到没有"只认 `ok` 三态: True 命中 / **None 没做成**(与"固件不判"分开) 。
    #   两次停靠的实录在 `at_hit`(注入点)/`watch_hit`(判据断点)——那两键由 `inject_hit` 专门补上。
    # ⚠ 断点在汇合点 `Recd_TimeError` 上 ⇒ 它会**同时**被"判故障"(str=TRUE)与"清故障"(str=FALSE)两类
    #   调用走到 —— 所以"停到了"本身还不够, 必须读 `str` 认形态。读不到/不是 TRUE ⇒ 记『没做成』,
    #   **不许**把一次清故障的停靠记成"判故障成立"(那是拿别人的一次执行当证据)。
    okw = (r or {}).get("ok")
    if okw is None:
        d = "注入点或判据断点没停到 ⇒ 这一次**没做成**(不是固件不判): %s" % ((r or {}).get("detail") or "无详情")
        recs.append(rec("断[:1508] 固件判定为时钟故障(Recd_TimeError str=TRUE)", None, d, crit="①",
                        obs=judge.DEBUG, falsify=_CE_FALSIFY["①"], trig=judge.TRIG_INJECT))
        why.append(d)
        return recs, why, None
    _ah, _wh = (r or {}).get("at_hit"), (r or {}).get("watch_hit")
    _rv = (r or {}).get("vars") or {}
    _sv = str(_rv.get("str") or "").strip().upper()
    d = ("注入点停在 %s; 判据断点停在 %s; 注入账 %s; 停时 str=%s sour=%s"
         "(年字节被改小 ⇒ Comp_Data(Buff2,PTime,6)<0 ⇒ 该进判定支)"
         % (_ah.where() if _ah else "未命中", _wh.where() if _wh else "未命中",
            (r or {}).get("injects"), _sv or "读不到", _rv.get("sour")))
    if not _sv:
        _okw, _note = None, " —— 停时 `str` 读不到 ⇒ 是『判故障』还是『清故障』证不了(记『没做成』)"
    elif _sv != "TRUE":
        _okw, _note = None, (" —— `str=%s` 不是 TRUE ⇒ 那一次是**清故障**那一类调用, 不是判故障"
                             "(记『没做成』)" % _sv)
    else:
        _okw, _note = True, ""
    recs.append(rec("断[:1508] 固件判定为时钟故障(Recd_TimeError str=TRUE)", _okw, d + _note,
                    crit="①", obs=judge.DEBUG, falsify=_CE_FALSIFY["①"], trig=judge.TRIG_INJECT))
    why.append(d + _note)

    # ---- ① 黑盒: 事件 0x2E 新增一条(用现成的 event_advanced —— 它认"库刚被清过"这种情形) ----
    # ⚠ 没新增**分两种**, 别一律判 FAIL(2026-09-16 实跑定死): 基线里那条若**未闭合**, 固件自己的判定
    #   (`TaskRecord.c:1508-1526`) 本就不放行 ⇒ 这一次**观测不到**写库, 是台面属性, 记 `ok=None`。
    #   基线里没有未闭合记录而仍没新增, 才是"固件判了却没写库", 那才报代码侧。
    ok_new, d = event_advanced(post.get("occur"), pre.get("occur"), t0)
    _po = ((pre.get("occur") or {}).get("ts") or "").strip()
    _pe = ((pre.get("end") or {}).get("ts") or "").strip()
    if ok_new is None:
        # 一个字节都没读到 ⇒ 没做成。**不许**掉进最下面那支"判定是开的**却**没写库"——那句话
        # 的前提是"基线读到了、判定的状态知道", 而这里连基线都没读到。
        recs.append(rec("事件 0x2E 新增一条(读回)", None, d, crit="①",
                        falsify=_CE_FALSIFY["①"], trig=judge.TRIG_INJECT))
    elif ok_new:
        recs.append(rec("事件 0x2E 新增一条(读回)", True, d, crit="①",
                        falsify=_CE_FALSIFY["①"], trig=judge.TRIG_INJECT))
    elif _po and ce_blank(_pe):
        d += (" —— 但基线里最近一条是**未闭合**的(发生时间 %s / 结束时间 %r)⇒ `TaskRecord.c:1508-1526` "
              "的判定(`str=TRUE` 且已有未闭合记录 ⇒ 直接返 FALSE)本就不放行 ⇒ 这一次**观测不到**写库, "
              "记『没做成』(**不是固件不判故障**; 要观测须先让判据② 把这条尾巴补上)"
              % (_po, _pe or "(空)"))
        recs.append(rec("事件 0x2E 新增一条(读回)", None, d, crit="①",
                        falsify=_CE_FALSIFY["①"], trig=judge.TRIG_INJECT))
    else:
        d += " —— 基线里没有未闭合记录(判定是开的)⇒ 固件判了故障却没写库, 报代码侧"
        recs.append(rec("事件 0x2E 新增一条(读回)", False, d, crit="①",
                        falsify=_CE_FALSIFY["①"], trig=judge.TRIG_INJECT))
    why.append(d)

    # ---- ③ 发生侧: 记录的**发生时间**应落在 [触发前, 触发后 + 容差] ----
    # ⚠ **先认「到底有没有新增」**(2026-09-16 第二轮实跑定死, 首轮那个假 FAIL 就是这里): 判定挡着时
    #   固件一个字都没写, 此时 `post` 里那条仍是**旧记录** —— 拿它的时间戳去比注入窗口, 会判出
    #   一条**假 FAIL**(实跑里旧记录 `2026-09-11 17:47:45` 落在窗口外, 于是记了"时标取自别处,
    #   报代码侧"), 那不是"固件把时标取错了", 是**这一次没有可比的时标**。与 ① 的写库半边同源
    #   (同一道判定 `TaskRecord.c:1508-1526`), 故同样记『没做成』, **不许**判 False。
    ts = ((post.get("occur") or {}).get("ts") or "").strip()[:19]
    lo, hi = clock_add(t0, -CE_TOL), clock_add(t0, CE_TOL)
    if ok_new is None:
        d = ("这一次**根本没读到记录**(与上一条同因)⇒ 没有可比的时标 —— "
             "记『没做成』(**不是固件把时标取错了**)")
        recs.append(rec("时标 发生时间 ≈ 触发时刻", None, d, crit="③", falsify=_CE_FALSIFY["③"],
                        trig=judge.TRIG_INJECT))
    elif not ok_new:
        d = ("这一次**没有新记录**(与上一条同因)⇒ 最近一条 %r 是**旧的**, 没有可比的时标 —— "
             "记『没做成』(**不是固件把时标取错了**)" % (ts,))
        recs.append(rec("时标 发生时间 ≈ 触发时刻", None, d, crit="③", falsify=_CE_FALSIFY["③"],
                        trig=judge.TRIG_INJECT))
    elif lo is None or hi is None:
        # 触发前后的表钟没读回 ⇒ 摆不出窗口。**不许拿本机时钟顶**(那会把一个与表无关的时刻
        # 当成判据基准 —— 2-1 判据③ 实测踩过这个坑, 见 `clock_add` 的 ⚠)。
        d = "触发时刻读不回(t0=%r)⇒ 摆不出比对窗口, ③ 发生侧未证" % (t0,)
        recs.append(rec("时标 发生时间 ≈ 触发时刻", None, d, crit="③", falsify=_CE_FALSIFY["③"],
                        trig=judge.TRIG_INJECT))
    elif clock_dt(ts) is None:
        d = "故障记录的发生时间读不回(ts=%r)⇒ ③ 发生侧未证" % ts
        recs.append(rec("时标 发生时间 ≈ 触发时刻", None, d, crit="③", falsify=_CE_FALSIFY["③"],
                        trig=judge.TRIG_INJECT))
    elif lo <= ts <= hi:
        d = "发生时间 %s ∈ [%s, %s](触发时刻 %s)" % (ts, lo, hi, t0)
        recs.append(rec("时标 发生时间 ≈ 触发时刻", True, d, crit="③", falsify=_CE_FALSIFY["③"],
                        trig=judge.TRIG_INJECT))
    else:
        d = "发生时间 %s 不在 [%s, %s] 内(触发时刻 %s)—— 时标取自别处, 报代码侧" % (ts, lo, hi, t0)
        recs.append(rec("时标 发生时间 ≈ 触发时刻", False, d, crit="③", falsify=_CE_FALSIFY["③"],
                        trig=judge.TRIG_INJECT))
    why.append(d)
    return recs, why, True


def clock_error_clear_path_evidence(sres, accepted):
    """5-8 判据②(白盒段) —— 停在 `DLT645App.c:734 Recd_TimeError(FALSE,0)` = "清故障"那条指令真的执行了。

    与黑盒那条(回读结束时间)认领**同一个 ②**: 一条说"库里补上了", 一条说"补库那句真跑过"。
    黑盒是结论、白盒是路径 —— 两条都成 ② 才算硬证。

    `accepted=False`(校时被判定拒)时 `:734` **本就不该命中** ⇒ 记旁证(crit=None): 它是旁证不是判据,
    免得"没命中"在账本里被当成"固件不清故障"。
    ⚠ 旁证**也要把 `:734` 到底停没停印出来**(2026-09-16 定): 被判定拒时"没命中"是**预期且一致**的,
      而"命中了却没起作用"是**矛盾**——两种情形都必须看得出是哪一种, 不许因为"反正不认领判据"
      就把这条事实扔掉(库拿到 `sres` 却不报, 等于白停一次)。
    """
    _w = "校时被受理却没走到 :734 ⇒ :734 那句没执行(或断点没挂上/残留断点搅局)⇒ 未证"
    if sres is None:
        return rec("断[:734] Recd_TimeError 执行(清故障)", None,
                   "没有调试会话(或用户指定只做黑盒)⇒ 断点观测这一次没做成", crit="②",
                   obs=judge.DEBUG, falsify=_CE_FALSIFY["②"])
    if not accepted:
        _h = sres.get("hit")
        if _h:
            _f = ("**停靠实录: :734 命中了** %s(停时 setTime/is698/mode = %s)—— 这与『校时被判定拒』"
                  "**矛盾**: 判定拒了就本走不到这一句。要么这一帧其实被受理了(那黑盒那边看错了表钟), "
                  "要么停的不是这一处(残留断点/停错地方) —— 两种都要先查清再谈固件"
                  % (_h.where(), sres.get("vars") or {}))
        else:
            _f = ("停靠实录: :734 **未命中** —— 与『校时被判定拒』一致(判定拒了本就走不到这一句); %s"
                  % (sres.get("detail") or ""))
        return rec("断[:734] Recd_TimeError 执行(清故障)", None,
                   "校时被判定拒 ⇒ 这一帧根本没走到 :734, 谈不上固件清没清(旁证, 不认领判据)。%s" % _f,
                   crit=None, obs=judge.DEBUG)
    if sres.get("ok") is None:
        return rec("断[:734] Recd_TimeError 执行(清故障)", None, _w, crit="②",
                   obs=judge.DEBUG, falsify=_CE_FALSIFY["②"])
    return rec("断[:734] Recd_TimeError 执行(清故障)", True,
               "停在 %s; 停时 setTime/is698/mode = %s"
               % ((sres.get("hit").where() if sres.get("hit") else "?"), sres.get("vars") or {}),
               crit="②", obs=judge.DEBUG, falsify=_CE_FALSIFY["②"])


def calitime_bc645_evidence(ser, delta=CE_CALI_DELTA, pre=None, wait=3.0, quiet=False, rd=None,
                            trig=None, bp=None, bp_vars=()):
    """5-8 判据② + ③(结束侧) —— 645 广播校时成功后, 固件应给故障记录**补上结束时间**。

    返回 `(recs, why, scope)`; `scope=None` = 半中途中止(没做成)。

    判定(`DLT645App.c CMD_CaliTime` mode=0, 2026-09-16 逐行核过):
      |Δ|∈[limit0,limit1] = [60,300]s(:624/:628, `TAB_CaliTPara` — `Config/MengXi/UserCfg.c:581`) /
      同自然日(:634 `Comp_Data(setTime[EM_Day], curTime[EM_Day], 3)`) / 不跨结算日(:639-653) /
      不跨年计(**仅 `TAB_MeterSty.style==TP_Local`**, :655-700) /
      **当日不重复校时**(:713 读 `ID_LastChTime`; 成功才在 :729 写当天)⇒ **当天只能成功一次**。
    被任何一道判定拒 ⇒ 记 `ok=None`『未做成』(**不判 FAIL**): 那一帧根本没走到 :734, 谈不上固件清没清。

    ⚠ **`|Δ|` 那道窗口拒不了 ±120s**(2026-09-16 核 `Platform/DateTime.c:226-240`): `Diff_Secs` 两支
      都返回大减小、返回类型是 `INT32U` —— **方向被丢掉**, 送 +120 与送 −120 得到的都是 `120`。
      故本项 delta=120 必然过这道窗口; 真正会拒它的只有 `:713` 与 `:720`(见下)。
    ⚠ **`:607-611` 那个"试校时"旁路在本台永不生效**: `TAB_CaliNum = {0}`(`Config/MengXi/UserCfg.c:580`)
      ⇒ `s_CaliNum < TAB_CaliNum[0]` 恒假 ⇒ 严格判定链每次都要走。
    ⚠ **`:720 Recd_CaliTimeBC` 写的是另一条记录**(`ID_CaliTimeBC` = 698 事件 **0x3C「广播校时」**,
      `DLT698App.c:3013` OAD `303C0B64`, 列定义 `TAB_CaliTimeBC:2765` 与时钟故障事件前十列逐条相同)。
      2026-09-16 实读 第 1 到第 5 个单元 **全是空单元**(固件"该单元没有值"的形态 = 数据段只有 `01 00 00 00`;
      对照: 5-3 掉电事件 最新一条 有值、倒数第 2 条/3 就是这个形状)⇒ **本台广播校时一次都没走通过 `:720`**。
      这是复核清除段被拒时的第一条线索, 记在 `ledger.md` 的 J 里。

    `rd` = 读回回调二元组 `(读表钟, 读事件)`; 缺省即真函数(给只想验判定逻辑的调用方留的口子, 同注入段)。
    `trig`/`bp`/`bp_vars` = 白盒段: 给出时**发帧那一步经 `trig` 下断点**(`bp` = `(文件,行号)`),
      于是同一次发帧既驱动了固件、又停在了清除口上。不给就是纯黑盒(那条记录记『没做成』)。
    """
    bad = _CE_FALSIFY["②"]
    _clock, _event = rd or (read_clock, read_clock_error_event)
    if pre is None:
        pre = _event(ser)
    t0 = _clock(ser, quiet=True)
    tgt = clock_add(t0, delta)          # 目标 = 当前 + delta(**基准读不出就返 None, 不拿本机钟顶**)
    if tgt is None:
        d = "校时前读不回表钟(%r)⇒ 摆不出目标时刻, 清除这一半不做" % (t0,)
        return [rec("645 广播校时受理(回读表钟)", None, d, crit="②", falsify=bad)], [d], None
    sres = None
    if trig is not None and bp is not None:
        sres = trig(bp, send, "645.time.broadcast_sync", timeout=CE_BP_WAIT, wait=wait,
                    _json=True, ser_shared=ser, param=tgt, _vars=tuple(bp_vars), _drop=True)
    else:
        send("645.time.broadcast_sync", wait=wait, _json=True, ser_shared=ser, param=tgt)
    t1 = _clock(ser, quiet=True)
    post = _event(ser)
    recs, why = [], []
    dlt = _ce_secs(t0, t1)
    if dlt is None or abs(dlt - delta) > CE_TOL:
        d = ("校时后表钟 %s → %s, 未按预期 +%ds ⇒ 这一帧**没被受理**。"
             "逐行核过(2026-09-16)⇒ `:734` 之前只剩两道 `return ER_OTHER`: "
             "**当日不重复校时**(`:713` 读 `ID_LastChTime`, 成功才在 `:729` 写当天)与 "
             "**广播校时记录写不进**(`:720 Recd_CaliTimeBC` 里的 `Write_RecdData`)——"
             "本台实测该记录块 第 1 到第 5 个单元 **全是空单元**, 本条线索指向后者。"
             "⚠ **不是 |Δ| 那道窗口**: `Diff_Secs` 取的是**绝对值**、方向被丢掉, 本帧 |Δ|=%d "
             "正落在 [60,300] 内(见 `Platform/DateTime.c:226-240`)。"
             "没走到 :734 ⇒ 谈不上固件清没清, 记『没做成』" % (t0, t1, delta, abs(delta)))
        recs.append(rec("645 广播校时受理(回读表钟)", None, d, crit="②", falsify=bad))
        why.append(d)
        recs.append(clock_error_clear_path_evidence(sres, False))
        return recs, why, None
    d = "表钟 %s → %s(目标 %s, 实走 %s 秒)" % (t0, t1, tgt, dlt)
    recs.append(rec("645 广播校时受理(回读表钟)", True, d, crit="②", falsify=bad))
    why.append(d)
    recs.append(clock_error_clear_path_evidence(sres, True))
    why.append(recs[-1]["detail"])

    # ---- ② 结束时间被补上(⚠ 不是看条数 —— 见本节头) ----
    e0 = ((pre.get("end") or {}).get("ts") or "").strip()[:19]
    e1 = ((post.get("end") or {}).get("ts") or "").strip()[:19]
    if clock_dt(e1) is None and not ce_blank(e1):
        d = "结束时间列读不回(e1=%r, 原始 RX 见上)⇒ 未证" % e1
        recs.append(rec("清除: 结束时间被补上", None, d, crit="②", falsify=bad))
    elif ce_blank(e1):
        d = ("校时被受理了, 但最近一条的结束时间仍是 %r(未补)⇒ `:734 Recd_TimeError(FALSE,0)` "
             "没执行或没落库, 报代码侧" % (e1 or "(空)",))
        recs.append(rec("清除: 结束时间被补上", False, d, crit="②", falsify=bad))
    else:
        d = "结束时间 %s → %s" % (e0 or "(空)", e1)
        recs.append(rec("清除: 结束时间被补上", True, d, crit="②", falsify=bad))
    why.append(d)

    # ---- ③ 结束侧: 结束时间 ≈ 校时时刻 ----
    gap = _ce_secs(t1, e1)
    if clock_dt(e1) is None:
        d = "结束时间 %r 不成形 ⇒ ③ 结束侧未证" % (e1,)
        recs.append(rec("时标 结束时间 ≈ 校时时刻", None, d, crit="③", falsify=_CE_FALSIFY["③"]))
    elif gap is not None and abs(gap) <= CE_TOL:
        d = "结束时间 %s ≈ 校时后表钟 %s(差 %s 秒)" % (e1, t1, gap)
        recs.append(rec("时标 结束时间 ≈ 校时时刻", True, d, crit="③", falsify=_CE_FALSIFY["③"]))
    else:
        d = "结束时间 %s 与校时后表钟 %s 差 %s 秒(容差 %ds)—— 时标取自别处, 报代码侧" % (e1, t1, gap, CE_TOL)
        recs.append(rec("时标 结束时间 ≈ 校时时刻", False, d, crit="③", falsify=_CE_FALSIFY["③"]))
    why.append(d)
    return recs, why, True


# ---- 继电器动作许可(固件判定, 决定 5-9/5-10 能不能落事件) ----
# 定址(FW TaskRelay.c, 2026-09-10 源码+实表坐实): Run_TaskRelay 每条动作路径都要求
#   (g_PlcState==0xFFFFFFFF 载波未接 || (g_PlcState&0xF)==0 停通讯) 且 Get_CompFlag(CMP_075Un,4)==0xFF。
# 不满足 → g_RelayBlk=TRUE(闭锁): 继电器不动作、Recd_CtrlRelay 不调用 → 5-9/5-10 事件【不产生】。
# 这是 DL/T698 的防误跳要求(低于 75%Un 不允许跳闸), 属【固件正确行为】, 不是缺陷。
# 台面含义: 给表加的电压须 ≥75%Un(本台 C_Un=220V → ≥165V), 否则 5-9/5-10 只能用 IAR 证"被闭锁"这半支。
CMP_075UN_IDX = P.CMP_075UN_IDX



def relay_gate_decode(body):
    """管理芯 `g_CompFlg` 的**读回字节** → (允许动作?, 说明)。**只解码不读**。

    判据 = `g_CompFlg[CMP_075Un]` 低 4 位(连续 4 个比较周期)全 1 → 电压≥75%Un。
    读由调用点自己发: 脚本走 `W.watch_vars(ser, ["g_CompFlg"])`, `relay_precheck` 走 `W.read_mem_aa80`
    —— 这里只认字节, 故两条通路共用同一份判据文字。
    `body=None`(本次没读到)与"读到了但没到 75%Un"(固件按防误跳闭锁)**都返 `False`**, 说明里分得开。
    """
    if body is None:
        return False, "读不到 g_CompFlg(画像未登记 / AA80 无应答)"
    flag = body[CMP_075UN_IDX] if len(body) > CMP_075UN_IDX else 0
    txt = " ".join("%02X" % b for b in body)
    if (flag & 0x0F) == 0x0F:
        return True, "电压≥75%%Un, 允许动作(g_CompFlg=%s)" % txt
    return False, ("电压<75%%Un → 固件闭锁(g_RelayBlk=TRUE), 继电器不动作、5-9/5-10 事件不产生 "
                   "(g_CompFlg=%s; 本台需 ≥75%%Un≈165V 才放行)" % txt)


def relay_precheck(ser, wait=2.0):
    """读 → 管理芯: 继电器动作许可(AA80 直读 g_CompFlg, Watch 等价) → (ok, 说明). 本函数自打印.
    判据文字与解码在 `relay_gate_decode`, 本函数只管把那一读发出去。
    只有 ok=True 时 ctrl_relay 才可能真动作并落 5-9/5-10 事件; 否则固件按防误跳闭锁(见上段)。"""
    head = "继电器动作许可(AA80 g_CompFlg[%d]=75%%Un)" % CMP_075UN_IDX
    rec = watch._pvar_addr("g_CompFlg")
    if not rec:
        opout(head, None, "画像未登记 g_CompFlg, 无法判前置", ok=False)
        return False, "画像未登记 g_CompFlg"
    verdict, data = watch.read_mem_aa80(ser, 1, rec[0] - watch.RAM_BASE, rec[1], wait=wait)
    if verdict != "PASS":
        return False, "AA80 读 g_CompFlg 失败"
    ok, note = relay_gate_decode(bytes.fromhex(data.replace(" ", "")))
    opout(head, None, note, ok=ok)
    return ok, note


def _relay_at_now(ser, plus=5):
    """读表钟 → (年2,月,日,时,分,秒) 并推后 plus 秒(固件要求执行时间≥当前表钟, DLT645App.c:3471)。"""
    ct = read_clock(ser, quiet=True)
    if not ct:
        return None
    dt = datetime.datetime.strptime(ct.strip(), "%Y-%m-%d %H:%M:%S") + datetime.timedelta(seconds=plus)
    return (dt.year % 100, dt.month, dt.day, dt.hour, dt.minute, dt.second)


def _relay_head(op, delay):
    """645 0x1C 跳合闸那一族的**功能名**。帧行与判定行共用这一份 —— 别在两处各拼一遍
    (原先 `ctrl_relay_reply` 的"读不到表钟"那条自己拼了个短名 `645 0x1C(拉)`, 而 `ctrl_relay`
    的判定行拼的是长名, 同一次操作在日志里两个长相)。"""
    code = op if isinstance(op, int) else RELAY_OP_CODE.get(op, 0x1A)
    return "0x1C 跳合闸=%s(操作字0x%02X %s) 延时%ds" % (op, code, RELAY_CMD_R.get(code, "?"), delay)


def ctrl_relay_reply(ser, op="拉", delay=0, at=None, wait=3.0):
    """发送 → 管理芯: 645 0x1C **原样**(跳合闸/保电/解除/预跳闸/报警) → `(操作字, 应答命令字节, 应答数据域, 帧)`。

    与 `ctrl_relay` 的分工: 那个把应答**收成**一个 verdict 串(PASS/FAIL/TBD, 给"发一发看看"用);
    这个把应答**原样交出来** —— 判据要区分"受理(0x9C)"与"被拒(0xDC)与为什么拒"时, 一个 FAIL
    不够: 无应答也是 FAIL, 而"无应答"与"被拒"是两件不同的事。判读一律由调用方做, 本函数不判。

    应答形状(源码依据 `DLT645Link.c:515-544`): 受理 ⇒ `pFrame[CMD] |= 0x80` → **0x9C**;
    被拒 ⇒ `|= 0xC0` → **0xDC**, 数据域首字节 = `1<<comSta`(错误位)。`comSta` 是 `Set_RelayCmdR`
    的返回值, 拦截那三条路**都**回 `ER_PSWD`(TaskRelay.c:885) ⇒ 错误位恒 `0x04` ——
    「具体被谁拦的」装不进这一字节, 只在 `g_CtrlStat[1]` 的位上(见 10-1 段头注)。
    ⚠ `back`(LEN==0x1C 那种帧才取)本帧恒 0 ⇒ 应答**不带**控制状态字, 数据域就 1 字节。

    返回 `cmd is None` = 没收到应答(超时/串口哑), `seg = b""`。
    ⚠ 副作用与前置同 `ctrl_relay`(继电器真动作 / 需 `enter_factory`)。"""
    head = _relay_head(op, delay)
    if at is None:
        at = _relay_at_now(ser)
        if at is None:
            opout(head, None, "读不到表钟, 无法定执行时间", ok=False)
            return (op if isinstance(op, int) else RELAY_OP_CODE.get(op, 0x1A)), None, b"", None
    code = op if isinstance(op, int) else RELAY_OP_CODE.get(op, 0x1A)
    frame = _relay_frame(op, delay, at)
    rx = send_frame(ser, frame, wait=wait, tag="relay", peer="管理芯", what=head)
    cmd, seg, _note = decode_645_reply(rx)
    return code, cmd, bytes(seg or b""), frame


def ctrl_relay(ser, op="拉", delay=0, at=None, wait=3.0):
    """发送 → 管理芯: 645 0x1C 跳合闸控制(触发 5-9 拉闸 / 5-10 合闸 事件) → verdict. 本函数自打印.
    op: '拉'/'拉闸'(操作字 0x1A) 或 '合'/'合闸'(0x1C 直接合闸), 亦可直接给操作字(见 RELAY_CMD_R)。
    delay: 延时秒(固件 DAT5×5)。at: (年2,月,日,时,分,秒) 执行时间; None=读表钟+5s(须≥当前钟 :3471)。
    ⚠ 副作用: 继电器真动作(拉闸断负载/合闸通负载)。前置 enter_factory 或密码(:3456)。
    ⚠ 另需台面 TAB_MeterSty.relay 配成内置/外置继电器, 否则 :3492 回 ER_OTHER。"""
    code, cmd, seg, frame = ctrl_relay_reply(ser, op, delay=delay, at=at, wait=wait)
    head = _relay_head(op, delay)          # 与发帧那一行**同一份**功能名, 见 _relay_head 的 docstring
    if frame is None:
        return "TBD"
    if cmd == 0x9C:
        opout(head, frame, "0x1C 已受理(9C=1C|0x80); 动作→TaskRelay.c:278 Upd_RelayCmd→:335 Recd_CtrlRelay")
        return "PASS"
    if cmd == 0xDC:
        note = "0x1C 被拒 DC " + (seg.hex(" ").upper() or "无数据域")
    else:
        note = "无 0x9C 应答" + (("(命令 0x%02X)" % cmd) if cmd is not None else "")
    opout(head, frame, note, ok=False)
    return "FAIL"


# ============================ 记录行/电能整列判据(通用: 各冻结/事件记录做「电量快照==当前」字节对拍) ============================
# 记录默认列(OAD 见对表操作总纲 §1/审计纪要 §3): 序号 20230200 / 时间 20210200 / 组合整列 00000400 / 正向整列 00100400.
# 4位小数电能「整列」元素规范形态 = 01 05 + 5 个 9 字节的电能(每项 1 字节首码 + 8 字节数值) = 47B; 应答行/GET 在整行或列尾带 2B 外壳 00 00,
# 故对拍 = 截到规范 47B 再比, 避开列尾噪音。整列 OAD 读的是满集合元素, 首个字节是对象位: 组合 0x14 / 正向 0x15(2026-09-09 探针坐实)。
_REC_SEQ_OAD = REC_SEQ_OAD        # 冻结记录·记录序号列 (p698 标准对象模型)
_REC_TIME_OAD = REC_TIME_OAD      # 冻结记录·冻结时间列
_ENE_COMB_FULL_OAD = ENE_COMB_FULL_OAD  # 组合有功(4位小数)整列
_ENE_FWD_FULL_OAD = ENE_FWD_FULL_OAD    # 正向有功(4位小数)整列


def rcsd(*oad_hex):
    """GetRequestRecord 列选 RCSD = 列数1B + 每列 [00][4B 列OAD]. oad_hex 例 '20230200'/'00000400'."""
    out = bytearray([len(oad_hex)])
    for o in oad_hex:
        out += b"\x00" + bytes.fromhex(o)
    return bytes(out)


def read_record_ud(ser, subclass, pos, rcsd, chip=None, wait=3.0):
    """发送 → 698 GetRequestRecord(RSD 09 pos 单行, 自定义 RCSD 列选) → ud(APDU) 或 b''.
    subclass: 0x00瞬时/0x02分钟/0x03小时/0x05结算, OAD=50 <子类> 02 00. 静默(判读由上层做)."""
    apdu = build_getrecord_apdu(0x03, subclass, rsd=bytes([0x09, int(pos) & 0xFF]), rcsd=bytes(rcsd))
    rx = send_frame(ser, frame_698(apdu, addr=chip_addr(chip)), wait=wait,
                    tag="rec_ud_r%02x" % int(subclass), peer=_chip_name(chip),
                    what="GetRequestRecord 子类0x%02X 第%d条 (原始 ud, 上层判读)" % (int(subclass), int(pos)))
    return split_apdu(rx or b"")


def read_oad_ud(ser, oad, chip=None, wait=2.0):
    """发送 → 698 普通 GET 读一个 OAD → ud(APDU) 或 b''. 静默(判读由上层做)."""
    rx = send_frame(ser, frame_698(build_read_apdu(0x05, oad), addr=chip_addr(chip)), wait=wait,
                    tag="get_" + oad, peer=_chip_name(chip),
                    what="GET 读 OAD %s (原始 ud, 上层判读)" % oad)
    return split_apdu(rx or b"")


def norm_energy47(b):
    """电能整列元素规范到 47B(01 05 + 5 个 9 字节的电能), 截掉应答行/GET 列尾常带的 2B 00 00 外壳. 短于47按原样(异常由上层判)."""
    return bytes(b)[:47]


def energy_slot_zero(b):
    """规范47B整列载荷是否全 0(=0.0000, 4位小数): 5 个 9 字节电能各 = 对象位(14/15) + 8 字节零. → True/False/None(形态不规)."""
    if len(b) != 47 or b[0:2] != b"\x01\x05":
        return None
    leads = [b[i] for i in range(2, 47, 9)]
    vals = b"".join(b[i + 1:i + 9] for i in range(2, 47, 9))
    return bool(leads and all(x in (0x14, 0x15) for x in leads)) and vals == b"\x00" * 40


def record_energy_cols(ud, n=2):
    """从 GetRequestRecord 应答行 ud 抽 n 个电能整列(默认 组合+正向), 各规范 47B → [47B,...] 或 None.
    定位: 序号元素由 `p698.record_seq_at` 定位, 冻结时标(1C+7B)由 `p698.record_time_at`
    从序号之后取, 再往后找 n 个 '01 05' 整列数据头(载荷不含 01/05, 可作列界);
    段长不定(两列间夹下一列 OAD), 故各段截 47B 即得该列完整元素.
    ⚠ 时标不许从整条应答的头 `find(b"\\x1c")` —— 序号低字节自己就可能是 0x1C(序号 28、284 …),
      那样 `tail` 从序号里起头, 后面的列界全跟着错位(理由见 `p698.record_time_at`)."""
    if not ud:
        return None
    _i = record_seq_at(ud)
    t = record_time_at(ud, _i) if _i is not None else -1
    if t < 0:
        return None
    tail = ud[t + 8:]
    marks = []
    i = tail.find(b"\x01\x05")
    while i >= 0 and len(marks) < n:
        marks.append(i)
        i = tail.find(b"\x01\x05", i + 2)
    if len(marks) < n:
        return None
    out = []
    for k in range(n):
        end = marks[k + 1] if k + 1 < n else len(tail)
        out.append(norm_energy47(tail[marks[k]:end]))
    return out


def record_cols_chained(ud, n):
    """GetRequestRecord 应答里**序号列/时间列之后的 n 个单项电量列**的 9B 元素 → `[bytes,…]` 或 None。

    应答数据段的应有形态(按 698 记录行的通行形态定, 不按某一次实测字节定):
      `85 03` <OAD4> <RCSD 回显> 之后 = `01`(SEQUENCE OF 行) + 行数1B + 每行;
      行 = 各列值**按 RCSD 顺序背靠背**(RCSD 只在应答头回显一次, 不逐列夹在数据里, DLT698App.c:5624)。
      列值: 序号列 = `06`+4B / 时间列 = `1C`+7B / 电量列 = **1B 对象位(0x14 或 0x15) + 8B 4位小数数值**。
    故本函数只切"单项电量列"这一支: 自时间列末字节起每 9B 一列, 共 n 列。
    整列多项(总+费率 5 项)时外面再套 `01 05` 头 + 5×9B —— 那是 `record_energy_cols` 那一支。

    本函数只认形态, **不判对象位该是哪一个** —— 那个字节是判据的料(有符号该 0x14 / 无符号该 0x15,
    `DLT698App.c:143-144`), 交给 `minfrez_energy_match` 逐列比。放在这里比会把"符号性翻了"记成
    "形态不认识"(= 未证), 与"这一列不符"(= 失败) 混成一样(CLAUDE.md 第 30 条)。

    ⚠ 早先按"每列 `01 xx` 头 + xx×9B"链式切, 而那个头在单项列上**不存在**(实测 12 列一个都没有),
      于是那一版恒返回 None(4-2 ⑪ 记未证的真因)。现在按定长切, 并用**列后余量**自检: 切完 n 列
      剩下的必须是行尾那 ≤2B 的 `00` 外壳, 否则算形态不认(返回 None, 不硬解, 也不往前挪一格再试)。
    """
    if not ud:
        return None
    _i = record_seq_at(ud)
    t = record_time_at(ud, _i) if _i is not None else -1
    if t < 0:
        return None
    p, out = t + 8, []
    for _ in range(int(n)):
        if p + 9 > len(ud) or ud[p] not in (0x14, 0x15):
            return None
        out.append(bytes(ud[p:p + 9]))
        p += 9
    rest = bytes(ud[p:])
    if len(rest) > 2 or rest.strip(b"\x00"):
        return None
    return out


def energy_ele_of(ud):
    """普通 GET 电能对象应答 ud 里的整列值元素(规范 47B). 应答: 85 01 <PIID> <OAD4> 01 <01 05 ...>; 值元素自 ud[8:] 起."""
    return norm_energy47(ud[8:]) if len(ud) >= 8 else None


def check_freeze_snapshot(ser, tag="", subclass=0x05, chip=None, pos=1,
                          comb_oad=_ENE_COMB_FULL_OAD, fwd_oad=_ENE_FWD_FULL_OAD, wait=3.0):
    """判据: 冻结记录内「电量快照」整列(组合/正向) 与 当前电能量对象整列 规范化 47B 逐字节对拍 → (ok, 说明).
    ok: True=字节一致+lead 对象位对 / False=不一致 / None=无法对拍(记录无行或 GET 无元素). 本函数自打印判定.
    内部: GetRequestRecord RCSD 扩列选(序号+时间+组合+正向整列) 读记录 pos 条 → 抽两整列;
          普通 GET 同 OAD 当前整列 → 各自截 47B(_norm47) 逐字节比. 字节同构由 probe(2026-09-09) 坐实。
    ⚠ 0 负载台面「真0」与「空快照(全0写坏)」不可分——判定=字节一致+列存在+lead 对象位对, 见返回说明。"""
    cls = _FREEZE_SUBCLASS_NAME.get(subclass, "sub%02X" % subclass)
    print("\n   数值判据(%s冻结 pos%d 记录电量快照 == 当前) [%s] ..." % (cls, pos, tag or "untagged"))
    rec = read_record_ud(ser, subclass, pos, rcsd(_REC_SEQ_OAD, _REC_TIME_OAD, comb_oad, fwd_oad),
                         chip=chip, wait=wait)
    pair = record_energy_cols(rec, 2) if rec else None
    cc = energy_ele_of(read_oad_ud(ser, comb_oad, chip=chip, wait=wait))
    cf = energy_ele_of(read_oad_ud(ser, fwd_oad, chip=chip, wait=wait))
    if not pair:
        print("   !! 记录%d列读回无行(ud空/无1C/无 01 05 双列界), 无法对拍" % pos)
        return None, "记录读回无行"
    if cc is None or cf is None:
        print("   !! 当前整列电能 GET 无元素")
        return None, "当前整列GET无元素"
    ok_c = (pair[0] == cc and pair[0][2] == 0x14)
    ok_f = (pair[1] == cf and pair[1][2] == 0x15)
    shape_ok = (len(pair[0]) == 47 and len(pair[1]) == 47
                and pair[0][0:2] == b"\x01\x05" and pair[1][0:2] == b"\x01\x05")
    zc, zf = energy_slot_zero(pair[0]), energy_slot_zero(pair[1])
    zero_payload = (zc is True and zf is True)
    print("   记录组合整列[%dB]=%s.. | 当前=%s..  -> %s" % (
        len(pair[0]), pair[0][:6].hex(" ").upper(), cc[:6].hex(" ").upper() if cc else "(无)",
        "一致" if ok_c else "不一致"))
    print("   记录正向整列[%dB]=%s.. | 当前=%s..  -> %s" % (
        len(pair[1]), pair[1][:6].hex(" ").upper(), cf[:6].hex(" ").upper() if cf else "(无)",
        "一致" if ok_f else "不一致"))
    print("   判定: %s (字节一致=%s, lead位=%s; 载荷=%s -> 4位小数对象 %s)" % (
        "PASS" if (ok_c and ok_f and shape_ok) else "FAIL/TBD",
        ok_c and ok_f,
        "组合14/正向15" if (shape_ok and pair[0][2] == 0x14 and pair[1][2] == 0x15) else "异常",
        "全0" if zero_payload else ("非0" if shape_ok else "形态异常"),
        "0.0000kWh" if zero_payload else "见上"))
    if not shape_ok:
        print("   ⚠ 记录电能整列形态不符(非47B/无01 05)——可能列错位, 对上面原始字节人工核")
        return False, "记录电能整列形态不符"
    return (ok_c and ok_f), ("组合/正向快照==当前%s" % ("(0.0000)" if ok_c and ok_f else "不一致"))


# ============================ 2-1『同步时钟·偏差计量芯钟不大于1s』(纯数据 + 判据, 2026-09-11) ============================

# ⚠ `CLOCK_BIG` 取 90(不是"一眼看得出跳没跳"的 30)是**为判据④服务的**: 瞬时冻结记录的时标是
#   **分钟粒度**(首跑实测: 10:33:47 触发, 记录写 10:33:00), 而 30s 的拨偏很可能跨不过分钟边界 ——
#   那就成了"改前改后同一分钟", 拿分钟去比**没有分辨力**。90s 保证跨过至少一个分钟。
CLOCK_BIG = P.CLOCK_BIG
CLOCK_SMALL = P.CLOCK_SMALL
CLOCK_SETTLE = P.CLOCK_SETTLE
CLOCK_ALIGN_SETTLE = P.CLOCK_ALIGN_SETTLE
INJECT_AT = P.INJECT_AT
INJECT_WATCH = P.INJECT_WATCH
INJECT_DECIDE = P.INJECT_DECIDE



def inject_assigns(delta):
    """把 `objtime` 造成『比 `g_MeterTime` 快 `delta` 秒』的一串赋值 —— `delta` 可 0、可负。

    只动第 0 字节(秒), 其余五字节照抄 `g_MeterTime`(依据见上面那段布局实证)。
    ⚠ `delta` 为负时 `g_MeterTime[0] + (-1)` 在秒 = 0 处会**绕成 255**(字段是 `unsigned char`)
      —— 那不是"差 1 秒"而是"差 255 秒" ⇒ 会**误跟随**, 把"不该动"读成"动了"。
      调用方必须先 `wait_second_at_least()` 把秒抬到安全区(见 `clock_sync_evidence` 的 ③e)。
    """
    head = (("objtime[0]", "g_MeterTime[0] + (%d)" % delta),) if delta else \
           (("objtime[0]", "g_MeterTime[0]"),)
    return head + tuple(("objtime[%d]" % i, "g_MeterTime[%d]" % i) for i in range(1, 6))


INJECT_ASSIGNS = inject_assigns(1)        # ③b 那一次(名字保留: 旧调用点仍指它)

# ③ 每一次的**观察超时**(秒) —— 不是"窗口": 判定指令是**放行后第一停**, 微秒级的事。给这几秒
# 只是让"没停到"这条路有个上限(断点下不上/核没放起来), 与"归谁"无关(那把尺子已换成就地读判定)。
# ⚠ 旧的两个常量 `INJECT_WINDOW=1.5` / `INJECT_ATTRIB=0.5` 已**删掉**(2026-09-11): 窗口法连同
#   "拿放行后的秒数判这一停归谁"一起被推翻(见 `INJECT_DECIDE` 上面那段)。留着它们会变成两个
#   **读着像现行规矩的死常量**, 下次有人照着调参就白调。
INJECT_TIMEOUT = P.INJECT_TIMEOUT
INJECT_SHOTS_STEADY = P.INJECT_SHOTS_STEADY
INJECT_SHOTS_SHIFT = P.INJECT_SHOTS_SHIFT
INJECT_SHOTS = P.INJECT_SHOTS
CLOCK_STAMP_SECS = P.CLOCK_STAMP_SECS



def clock_dt(s):
    """读回的钟串 → datetime; 解析不了给 None(**不抛** —— 与两种观测同律: 读不到记「没做成」)。"""
    try:
        return datetime.datetime.strptime(str(s).strip()[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def clock_add(clock_str, secs):
    """钟串 + N 秒 → 钟串; **基准读不出就返回 `None`** —— 不拿 `datetime.now()` 兜底。

    ⚠ 这里**有意不**沿用探针那套"算不出就从现在起算"。本函数的返回值会被
      `set_meter_clock_set(..., chip="计量芯")` **写进真表**: 基准读失败时兜底成"本机当前时刻",
      造出来的是一个**看着完全合理的假目标**(2-1 首跑 ③ 实测: `m3` 读失败 → 目标被算成
      `2026-09-11 10:48:06`, 比刚对齐好的 10:49:01 还早, 与表上那颗钟毫无关系)。写进去不但
      测不出东西, 还**主动把表钟拨到一个错的时刻** —— 静默错误里最坏的一类。
      宁可让调用方拿到 `None` 自己决定"这一半不做"。
    """
    base = clock_dt(clock_str)
    if base is None:
        return None
    return (base + datetime.timedelta(seconds=secs)).strftime("%Y-%m-%d %H:%M:%S")


def _spec_proto(frame_id):
    """目录帧 id → `common.loglabel` 里那个协议常量。id 一律写成 `<协议>.<名>`。

    ⚠ 没登记过的前缀**当场抛** —— 这条是钉子, 不是防御: 加了一条新前缀的目录项, 就该停下来想一下
      "这一族在日志上该标什么", 而不是让它悄悄出一行没有协议格的日志。
    """
    pre = frame_id.split(".", 1)[0]
    if pre not in _SPEC_PROTO:
        raise ValueError("帧目录 id 前缀 %r 未登记协议 —— 请在 _SPEC_PROTO 里补一条(见 common/loglabel)"
                         % pre)
    return _SPEC_PROTO[pre]


# 目录 id 前缀 → 协议。全 18 条目录项只有这两种前缀; 加新一族时**必须**在这儿加一行(见 _spec_proto)。
_SPEC_PROTO = {"645": loglabel.PROTO_645, "698": loglabel.PROTO_698}


def send_frame_id(ser, token, wait=3.0, quiet=False):
    """发**目录里已有**的帧: 按 id/别名解析 → 组帧 → 发 → 与 CLI `send` **同一套**机器判过。

    → `{verdict, reason, tx, rx, id}`; 本函数自打印(`quiet=True` 只静默返回)。

    为什么要有它: 脚本要点目录里某条帧(如 `698.action.freeze.immed` 广播瞬时冻结)时, 原先只有两条
    路 —— 在脚本里现拼帧(组帧是库的事), 或者在脚本里摸 `SPECS`(解析也是库的事)。
    本函数把 `resolve → _hex → tx_recv → machine_verdict` 这条链收成一个动词。**与 CLI `send`
    共用同一份 `SPECS` 与同一个 `machine_verdict`** —— 改判过规则只改一处, 不会两边分叉。
    """
    s = _one(token, "send")
    frame = _hex(s)          # 协议在 `_hex` 里就钉在帧上了, 这里不必也不再自己判断
    # ⚠ 功能名**只用 `name`**, 不带 `id` —— 原先写的是 `"645.factory | 进厂内(…)"`, 于是 `645`
    #   在帧行上出现两遍(协议格一次、名字里一次)。id 归事件流的 `tag`, 不进人读的那一行。
    what = s["name"]
    # ⚠ `peer` 按目录项声明取, **没声明就不写** —— 目录帧里有广播帧(698 瞬时冻结走 0xAA/0x99 地址),
    #   不声明时那一格空着出 `[发]`, 好过一律瞎填"管理芯"。要补就在 SPECS 那条上加 `peer="广播"`.
    rx = tx_recv(ser, frame, wait=wait, tag=s["id"],
                 peer=s.get("peer", ""), what=what)
    v, reason = machine_verdict(s, rx)
    if not quiet:
        opout(what, frame, "%s: %s" % (v, reason), ok=(v != "FAIL"))
    return {"verdict": v, "reason": reason, "tx": frame, "rx": rx, "id": s["id"]}


def clock_sync_criteria():
    """2-1 的预设判据条目(源 = `project/knowledge/_whitebox_ledger/ledger.md` 2-1 的 I 列「判过: ②③④」)。

    拆分说明(**不是洁癖, 不拆就会记出假通过**):
      · ② 拆 a/b: ②a「管理芯钟真被改了」= **对外行为**, 串口读双芯钟即得; ②b「跟随支真被执行」
        = **指令路径**, 只有断点观测给得出。合成一条的话, 台面没接 J-Link 时 ②b 的证据根本不存在,
        而 ②a 一过整条就记「满足」—— 内部路径没人证过却报了绿。
      · ③ 同理拆 a/b, 而且 ③ 的**多半重量在 b**: 跟随窗口只有 |差| ≤ 1s, 而 RTC 读数是**秒粒度**,
        "跟了"与"没跟"在串口上只差 1 秒 —— 与串口往返噪声同量级(②a 那种 30s 的跳变才一眼可辨)。
        所以 ③a 是**佐证**、③b/c/d/e(判定指令**就地读出的落点** = 指令路径硬证)才是 ③ 的承重墙。
      · ③ 的边界点是**四点不是一点**: 判据是 `> 1`, 秒是整数 ⇒ 边界只有 {0, 1, 2}。只证 `差 1 不跟随`
        等于只证了阈值的一个侧面(阈值仍可能被写成 ≥1、或写成 ≥5 —— 后者单看 ③b 与 ② 都发现不了)。
        ⇒ 拆 ③b(差 1 不跟随)/ ③c(差 2 **跟随**, 与 ③b 配对把阈值夹死在 (1, 2])/ ③d(差 0 不动)/
        ③e(差 −1 不动)。四条各有各的 falsify, 合成一条就只剩"这个窗口没动"这一个观感。
      · ④ 一条: 「改后时标正常」是一个整体观感(时标既要是新钟、又要落得下来), 不拆。
        ⚠ 它**不是** ②a 的复述: ②a 只证"钟被改了", ④ 证"改完之后**拿这颗钟给记录打戳**还是对的"
        (RTC 写坏/时标基址跑偏这类故障只在记录时标上显形)。本函数跑**两次**, 分别覆盖判据接受的
        "当分钟"与"自然跨下一分钟"两支 —— 只是同一判据的两条样本, 不是两条判据。
    """
    return {
        "②a": "计量芯钟被拨偏 >1s ⇒ 管理芯钟跳到计量芯钟(自动跟随, 对外行为; **正反两个方向各一次**"
               " —— 判据没写方向, 只测正向分辨不出『有符号当无符号比较』与『只认正向』这两类实现)",
        "②b": "断点观测: 断[A] TaskTime.c:1087 Set_MeterTime(objtime) 被执行(跟随支真被走到)",
        "③a": "计量芯钟偏差 ≤1s ⇒ 管理芯**本地钟未被拉走**。量法 = **不停核窗口**: 把两芯摆到 |差| ≤ 1 的稳态, 然后连采 `管理芯−墙钟` 12 点(约 42s), **全程不碰调试器**; 窗口前 1/3 点 与后 1/3 点各取 max, 之差 = 阶跃, 阈值 0.5s(跟随即被拉 ±1s)。两个窗口各走一侧 —— **自然稳态** 与 **把计量芯拨到管理芯另一侧** —— 跟随的方向两侧相反, 只测一侧看不出『只认一侧』的实现",
        "③b": "断点观测: 差恰 1s ⇒ 判定指令(`cmp r0,#2` 后的 `bcc`)就地读出**落点 = 跳走那一支**"
               "(= `Set_MeterTime` 没被执行; 边界内不跟随)",
        "③c": "断点观测: 差恰 2s ⇒ 判定指令就地读出**落点 = 被保护那一支** —— 与『差恰 1s ⇒ "
               "落点 = 跳走那一支』那一条配对, 把阈值夹死在 (1, 2]",
        "③d": "断点观测: 差恰 0s ⇒ 判定指令就地读出落点 = 跳走那一支(没被执行)",
        "③e": "断点观测: 差恰 −1s(反方向)⇒ 判定指令就地读出落点 = 跳走那一支(判据两个方向对称)",
        "④":  "跟随改钟后, 新落的瞬时冻结记录时标 == 新钟(改后时标正常; 两次覆盖 '当分钟' 与"
               "'自然跨下一分钟' 两支)",
    }


def clock_offset_samples(ser, n=3, gap=1.0, wait=2.0, tag=""):
    """连读 n 次双芯钟 → 每次的 `(计量芯 − 管理芯)` 秒差列表(读不回来的那次跳过)。

    为什么要有它: 2-1 要判的偏差只有 **1 秒**, 而 RTC 读数是**秒粒度** —— 单次读数差 1 秒与量化
    噪声同量级。多采几次才敢说"差一直在那儿"(见 `clock_sync_evidence` 里 ③a 的说明)。
    """
    offs, lines = [], []
    for i in range(n):
        m = read_clock(ser, wait=wait, quiet=True)
        g = read_clock(ser, chip="计量芯", wait=wait, quiet=True)
        dm, dg = clock_dt(m), clock_dt(g)
        if dm is None or dg is None:
            lines.append("   样本%d: 读不回(管理芯=%s 计量芯=%s) → 跳过" % (i + 1, m, g))
            continue
        offs.append((dg - dm).total_seconds())
        lines.append("   样本%d: 管理芯=%s 计量芯=%s ⇒ 差 %+.0fs" % (i + 1, m, g, offs[-1]))
        if i != n - 1:
            time.sleep(gap)
    if tag:
        print("   -- %s 双芯差采样(%d/%d 有效) --" % (tag, len(offs), n))
        for x in lines:
            print(x)
    return offs, lines


def _wall_series(ser, n=8, gap=2.0, wait=3.0):
    """连读 n 次**管理芯**钟 + 墙钟 → `(偏移列表, 明细行)`。**一份采样, 两个用法**。

    `wall_offset_samples`(返 max)与 ③a 的**计分窗口**(`clock_hold_evidence` —— 它要把窗口切成
    前/后两段各取 max, 需要**整条序列**而不是那一个 max)共用这一段。抄第二份迟早分叉。
    """
    offs, lines = [], []
    for i in range(n):
        t0 = time.time()
        m = read_clock(ser, wait=wait, quiet=True)
        t1 = time.time()
        g = read_clock(ser, chip="计量芯", wait=wait, quiet=True)
        dm, dg = clock_dt(m), clock_dt(g)
        if dm is None:
            lines.append("   样本%d: 管理芯读不回(%r) → 跳过" % (i + 1, m))
        else:
            # 墙钟取**往返中点**: 管理芯回的钟是它组帧那一刻的, 取中点把往返延迟对折。
            o = (dm - datetime.datetime.fromtimestamp((t0 + t1) / 2.0)).total_seconds()
            offs.append(o)
            lines.append("   样本%d: 墙钟=%s 管理芯=%s ⇒ 管理芯−墙钟=%+.2fs   双芯差=%s(仅诊断)"
                         % (i + 1, datetime.datetime.fromtimestamp(t0).strftime("%H:%M:%S.%f")[:-3],
                            m, o, ("%+.0fs" % (dg - dm).total_seconds()) if dg else "读不回"))
        if i != n - 1:
            time.sleep(gap)
    return offs, lines


def wall_offset_samples(ser, n=8, gap=2.0, wait=3.0, tag=""):
    """连读 n 次**管理芯**钟, 每次配一个本机墙钟 → 返回 `(偏移估计, 明细行)`。

    **为什么要换这把尺子**(2026-09-11 实探):
    ③a 原先量的是 `计量芯−管理芯` 的秒差, 而那个数**量不动 1 秒**:
      · `read_clock(chip="计量芯")` 读的是**管理芯手里的 SPI 时间对象**(约 1 Hz 更新), 不是计量芯当场
        ⇒ 系统性滞后;
      · 两端都是**整秒字段**, 差的量化误差 ∈ (−1, +1)s。
    实探里同一段"没扰动"的稳态中, 那个差在 0/1/2 之间跳 —— **噪声底(±1s)与信号(1s)同量级**, 判不出来。
    而 `管理芯−墙钟` 那一列极稳, 因为管理芯钟是我们**自己发的帧**读回来的, 不经镜像、不掺第二颗钟。

    **为什么取 max 而不是均值**: 管理芯钟是**整秒截断**(floor), 单次 `管理芯−墙钟` 的误差落在 `(−1, 0]`
    —— 是个**单边**偏置。取 max 就把这个单边误差从 1s 压到 `≤1/n`(n=8 ⇒ ≤0.125s), 于是 **1 秒的阶跃**
    分辨得开。均值**做不到**(单边偏置不随 n 收敛)。
    ⚠ 判据要的是"两段估计的**差**", 往返延迟带来的固定偏置在相减时自己抵消 —— 所以这里只求"同一把
      尺子前后一致", 不追求偏移本身是个绝对真值。
    ⚠ 本函数**只采样、不判断**, 且**不碰调试器** —— ③a 的窗口就是靠这一点成立的(见 `clock_hold_evidence`)。

    返回 `(估计值或 None, 明细行列表)`。顺带把 `计量芯−管理芯` 也打出来 —— 它当诊断看还行, **不当判据**。
    """
    offs, lines = _wall_series(ser, n=n, gap=gap, wait=wait)
    est = max(offs) if offs else None
    if tag:
        print("   -- %s 管理芯−墙钟采样(%d/%d 有效; 取 max 作估计) --" % (tag, len(offs), n))
        for x in lines:
            print(x)
        if est is not None:
            print("   ⇒ %s 偏移估计 = %+.2fs(单次误差 ≤1s 且单边, 故取 max: n=%d ⇒ ≤%.2fs)"
                  % (tag, est, n, 1.0 / n))
    return est, lines


def _align_chips(ser, why, settle=CLOCK_ALIGN_SETTLE, wait=3.0):
    """把**计量芯**钟拨到管理芯钟当前值 ⇒ 两芯对齐 ⇒ **停核造成的滞后从 0 起算**。返回是否做成。

    **为什么要它**(2026-09-11 第二次实跑抓出来的, 是 ③b/③d/③e 假命中的根因):
    注入**每一次都要停核**。停核期间管理芯的**时钟会停**(软件走时), 而计量芯照跑 ⇒ 每停一次,
    管理芯就落后计量芯约 0.3s。这个滞后**不会自己消掉**: 跟随把管理芯设成 `objtime` = **SPI 镜像**
    那一刻的计量芯时间, 而镜像本身就滞后 ~1s ⇒ 跟完仍有 ~1s 差, 恰好在 `> 1` 的边界内、不再纠正。

    于是**连停三次, 滞后就能攒到 1.5s 以上** —— 而 `Diff_Secs` 是**秒粒度**的整数比较, 1.5s 的滞后
    有一半时候算出 **2 > 1** ⇒ **固件按自己的规矩正常跟随**, `:1087` 被走到 ⇒ 我的 1.5s 观察窗
    把这次**自然跟随**记成了"注入那一次被跟随了"(假命中, 实跑 ③d/③e 就是这么红的)。
    这不是固件问题, 是**触发通道把被测的量本身推过了阈值**。

    对策: **每次执行前把两芯拨齐**(写计量芯 = 管理芯)。滞后于是每次都从 0 起算, 单次只攒 ~0.3s
    ⇒ 永远够不到 `> 1`, 自然跟随在窗口里**不可能**发生 —— 窗口里剩下的任何命中, 都只能来自
    我们注入的那一次执行。⚠ 对齐**只动计量芯**, 管理芯不受影响(差 ≈0 就不会触发跟随)⇒ 不会
    扰动 ③a 那把量 `管理芯−墙钟` 的尺子。
    ⚠ 读不回管理芯钟 ⇒ 当场说清楚并返回 False(**不静默**): 对齐没做上, 这一次的假命中风险照旧在。
    """
    m = read_clock(ser, wait=wait)
    d = clock_dt(m)
    if d is None:
        why.append("   !! 对齐: 管理芯钟读不回 ⇒ 这一次**没对齐**, 停核滞后会照旧累积"
                   "(假命中风险仍在)")
        return False
    tgt = d.strftime("%Y-%m-%d %H:%M:%S")
    v, note, dar = set_meter_clock_set(ser, tgt, chip="计量芯", wait=wait)
    why.append("   -- 对齐: **计量芯**钟 ← 管理芯钟 %s (%s / %s, DAR=%s)⇒ 停核滞后从 0 起算 --"
               % (tgt, v, note, dar))
    if settle:
        time.sleep(settle)
    return True


# ---- ③a 的**不停核窗口**参数(纯数据) ----
HOLD_SAMPLES = P.HOLD_SAMPLES
HOLD_GAP = P.HOLD_GAP
HOLD_SETTLE = P.HOLD_SETTLE
HOLD_THRESH = P.HOLD_THRESH
HOLD_CONFIRM = P.HOLD_CONFIRM



def _chip_diff(ser, wait=3.0):
    """读一次双芯差 `(计量芯 − 管理芯)` 秒数, 读不回给 None。**只作诊断**。

    ⚠ **不当判据**: `read_clock(chip="计量芯")` 读的是管理芯手里的 SPI 镜像(约 1 Hz 更新),
      系统性滞后约 1s ⇒ 这个数**偏负**、且量化 ±1s。判据用它就是把噪声当信号。
    """
    m = read_clock(ser, wait=wait, quiet=True)
    g = read_clock(ser, chip="计量芯", wait=wait, quiet=True)
    dm, dg = clock_dt(m), clock_dt(g)
    return (dg - dm).total_seconds() if (dm is not None and dg is not None) else None


def _fs(x):
    return ("%+.2fs" % x) if x is not None else "未知"


def clock_hold_evidence(ser, release=None, crit="③a", samples=HOLD_SAMPLES, gap=HOLD_GAP,
                        wait=3.0, settle=HOLD_SETTLE, align_settle=CLOCK_ALIGN_SETTLE,
                        tag="", shift=None, tries=3, thresh=HOLD_THRESH, confirm=HOLD_CONFIRM):
    """③a 的**不停核**量法 —— 在"窗口内一次都不停核"的前提下, 看管理芯本地钟有没有被拉走。

    **为什么要换掉旧量法**(2026-09-11 定案; 2-1 连卡五跑的根因就在这儿):
      旧量法在 ③ 的注入动作**前后**各采一段 `管理芯−墙钟`, 拿两段的差判"有没有阶跃"。可 ③ 的动作
      **每一次都要停核**(注入只在停住态成立), 而管理芯钟是**软件走时** —— 停核期间它不走, 于是
      **观察手段本身把被测的量动掉了**: 实测单次停核 0.24~4.8s, 比 1s 的判据还大, 而且抖。
      拿"同样次数空停"的对照段去扣也救不了 —— 对照段量到的只是**它自己那几次**的账, 两段账差一倍。
      ⇒ 卡住的**不是这条判据证不了**, 是**旧量法把停核放进了窗口**。

    **新量法**: 窗口里一次也不停核, 也不用 J-Link —— 只走串口读管理芯钟(CPU 一直在跑)。
      ① **摆状态**: `_align_chips`(只动计量芯)把两芯拨齐; `shift=k` 时再把计量芯拨到管理芯的
         **另一侧**(写 `管理芯 + k s`);
      ② 等 `settle` 秒 —— 系统**自稳**: 差若 > 1 固件会跟随, 而跟随把管理芯设成**滞后 SPI 镜像
         ~1s** 的那个值 ⇒ 落回 |差| ≤ 1;
      ③ **先证明它稳了再计分**: 采 `confirm` 点, 全读到才算稳, 否则再等一轮(最多 `tries` 轮)——
         这是**前提的确认**, 不是判据本身; 摆不到稳态就记「没做成」, **绝不当 PASS**;
      ④ **计分窗口**: 连采 `samples` 点(`_wall_series`), 全程**无任何调试器动作**;
      ⑤ **判**: 窗口**前 1/3 点的 max** 与**后 1/3 点的 max** 之差 = 阶跃。跟随一定伴随阶跃
         (管理芯被拉到 `objtime` 那一刻); 不跟随则两段同相位、差 ≈ 0。
         ⚠ 取 **abs**: 反向跟随是**倒退**(阶跃为负), 单边阈值会把误动读成"没动"。
         ⚠ 取 **max 不取均值**: 管理芯钟是整秒截断, 单点误差**单边**落在 (−1, 0], 均值不收敛;
           max 把它压到 ≤1/n(n=12 ⇒ ≤0.083s), 阈值 0.5s 留 6 倍裕度。

    `shift`: `None` ⇒ 自然稳态那一侧; 整数 ⇒ 把计量芯拨到管理芯**另一侧**。**两侧都要测**: 判据是
      `|差| ≤ 1`, 而跟随的方向两侧相反(管理芯被拉向前 / 向后)——只测一侧看不出"只认一侧"的实现。

    `release`: 可选回调, **进窗口前**调一次(脚本传 `GD.ensure_running`)—— 把上一段注入留下的停住态
      放掉。库不认调试会话, 所以这根线由调用方给; 没给就不调(纯串口观测本来也不需要)。

    返回 `(recs, lines)`。**证得成报 True/False, 前提摆不出来报 None(没做成)** —— 三态如实。
    """
    lines, recl = [], []
    name = "③a 偏差 ≤1s 时管理芯**本地钟**未被拉走(不停核窗口%s)" % (
        "·自然稳态" if shift is None else "·把计量芯拨到另一侧")

    def _fail(msg):
        recl.append(rec(name, None, "【没做成】%s" % msg, crit=crit))
        return recl, lines

    if release is not None:
        try:
            release()
        except Exception as exc:
            lines.append("   !! release 回调抛了 %r ⇒ 本窗口可能停在停核态(读数会整段失踪)" % (exc,))

    # ---- ① 摆状态 ----
    _align_chips(ser, lines, settle=align_settle, wait=wait)
    if shift is not None:
        m0 = read_clock(ser, wait=wait, quiet=True)
        d0 = clock_dt(m0)
        if d0 is None:
            return _fail("窗口前读不回管理芯钟(%r) ⇒ 摆不出状态" % m0)
        tgt = (d0 + datetime.timedelta(seconds=shift)).strftime("%Y-%m-%d %H:%M:%S")
        v, note, dar = set_meter_clock_set(ser, tgt, chip="计量芯", wait=wait)
        lines.append("   -- %s: 把计量芯拨到 %s(管理芯 %s 的另一侧) ⇒ %s / %s (DAR=%s) --"
                     % (name, tgt, m0, v, note, dar))

    # ---- ② + ③ 等自稳, 并**先证明它稳了** ----
    ready = False
    for k in range(tries):
        time.sleep(settle)
        offs_c, ls_c = _wall_series(ser, n=confirm, gap=gap, wait=wait)
        lines.append("   -- %s 第 %d/%d 次确认稳态(%d 点全读到才算稳) 双芯差(诊断)=%s --"
                     % (name, k + 1, tries, confirm, _fs(_chip_diff(ser, wait=wait))))
        lines += ls_c
        if offs_c:
            ready = True
            break
    if not ready:
        return _fail("%d 轮都没读到管理芯钟 ⇒ 摆不出稳态" % tries)

    # ---- ④ 计分窗口(全程不停核) ----
    offs, ls = _wall_series(ser, n=samples, gap=gap, wait=wait)
    lines.append("   -- %s 计分窗口(%d 点, **全程不停核**) --" % (name, samples))
    lines += ls
    if len(offs) < 2 * max(confirm, 1):
        return _fail("计分窗口只读到 %d 点(要 ≥%d) ⇒ 分不了前后两段" % (len(offs), 2 * confirm))
    k3 = max(confirm, len(offs) // 3)
    early, late = max(offs[:k3]), max(offs[-k3:])
    step = late - early
    ok = abs(step) < thresh
    recl.append(rec(name, ok,
                    "计分窗口 %d 点(**全程不停核**); 前 %d 点 max = %s, 后 %d 点 max = %s ⇒ "
                    "阶跃 = %s(阈值 %.1fs); 双芯差(诊断)=%s。跟随一定伴随阶跃 —— 管理芯被拉到 "
                    "`objtime` 那一刻; 不跟随则两段同相位、差 ≈ 0。⚠ 窗口内**没有任何停核** ⇒ "
                    "旧量法那个『观察手段把被测的量动掉』的坑在这里不存在"
                    % (len(offs), k3, _fs(early), k3, _fs(late), _fs(step), thresh,
                       _fs(_chip_diff(ser, wait=wait))),
                    crit=crit,
                    falsify="固件在 |差| ≤ 1s 时就跟随 ⇒ 管理芯被拉到 objtime ⇒ 阶跃 ≈ ±1s"))
    return recl, lines


def resolve_decide(sess):
    """把 `INJECT_DECIDE`(函数名 + 被调名)解析成**判定断点** —— 没有会话就给 `None`。

    为什么这一步在**脚本**里(而解析器在库里的 swdbg): 会话在脚本手里, 而本层(L2 语义层)不许
    import swdbg —— 那是与 `meterlib` **平行**的独立包, 一行都不该被拽进来(见 CLAUDE.md「swdbg」)。
    于是本函数只是个**名字口径的单点**: 脚本写 `decide = CB.resolve_decide(g)`, 换表/换固件改
    `INJECT_DECIDE` 一处即可; 地址仍由 `.out` 反汇编推出并打进证据, 脚本里**不出现任何 `0x…`**。
    """
    if sess is None:
        return None
    d = sess.decision_anchor(*INJECT_DECIDE)
    # ⚠ 反汇编**要停住态**(`_disasm` 的 ⚠: 核跑着发 `-data-disassemble` 会挂死), 于是这一步
    #   返回时核是**停着的** —— 而调用方紧接着就要发串口帧(读钟/写钟), 停着的核**一条都不应答**,
    #   现象与"串口坏了"一模一样(CLAUDE.md 调试链纪律 5)。在本函数里放行, 别指望每个调用点记得。
    sess.ensure_running()
    return d


def inject_allow_names():
    """本子项要注入的表达式名字 —— 会话构造时喂 `inject_allow=`(库**不替脚本开会话**)。

    四个边界点(delta = 2/1/0/−1)写的都是同一批名字(`objtime[0..5]`)—— **白名单按名字点名,
    不按值**: 换一个 delta 只是换写进去的表达式, 名单一个都不用改。
    """
    return tuple(e for e, _v in INJECT_ASSIGNS)


def wait_second_at_least(ser, lo=3, wait=3.0, tries=8):
    """等表钟的**秒** ≥ `lo` 再返回; 返回读到的钟串(读不回/等不到 → None)。

    为什么非有不可(③e 的负方向那一次): 注入表达式 `objtime[0] = g_MeterTime[0] + (-1)` 在
    **秒 = 0** 时会绕成 255(`unsigned char`) —— 造出来的不是"差 1 秒"而是"差 255 秒", 固件**该跟随**
    ⇒ ③e 会把它读成"反方向也跟随", **冤枉固件**。这是**触发通道自身的坑**, 不是固件属性, 所以在这里
    把它堵掉, 而不是事后在判据里加解释。
    秒是 1 Hz 走的, 等一两秒必然跨过去; `tries` 次还不行就认输返回 None(调用方记"没做成")。
    """
    for _ in range(tries):
        s = read_clock(ser, wait=wait, quiet=True)
        d = clock_dt(s)
        if d is not None and d.second >= lo:
            return s
        time.sleep(1.0)
    return None


def clock_sync_evidence(ser, hit_bp=None, inject_bp=None, bp=None, decide=None,
                       release=None,
                        wb_waived=False, big=CLOCK_BIG, small=CLOCK_SMALL,
                        settle=CLOCK_SETTLE, wait=3.0, samples=8, wall_gap=2.0,
                        align_settle=CLOCK_ALIGN_SETTLE):
    """2-1 判据②③④ 的证据(库内单点, 脚本不留): 拨偏计量芯钟 **>1s 看跟随 / ≤1s 看误不误动**。

    **② 走帧、③ 走注入 —— 两条触发通道**(与「观察观测」正交的那根轴, 见 CLAUDE.md「两种观测」):
      · ② 的偏差**只能从计量芯侧制造**: `698 Set 40000200` → **计量芯**(它的写入口 DAR=0), 管理芯靠
        SPI 自动跟随(管理芯的 40000200 写已被注释禁用, AF=0x05 回 DAR=0xFF —— `set_meter_clock_set`
        的 docstring 有实测)。这与 2-1 规格「判不过/待核」那条"无双向写佐证"一致。
      · ③ 的偏差**造不出精确值**(同一条写路径落地要等 SPI 送达, 1~3s 抖动, 与 1s 判据同量级 —— 两次
        实跑一次命中一次没命中), 故改用**注入**: 在 `TaskTime.c:1085` 停住直接改写 `objtime`,
        四个边界点见 `INJECT_SHOTS_STEADY`/`INJECT_SHOTS_SHIFT`。

    返回 `(recs, why, scope)` —— 与 3-1/3-2 同形: **`scope is None` 表示半途中止**(不靠文案嗅探)。

    两种观测的分工(库不 import swdbg, 会话由脚本持有, 这里只收回调 —— 见 `_direct_trigger`):
      · `hit_bp(bp, fn, *a, **kw)`      = `partial(GD.fire_hit, g)`      —— **帧**触发 + **期望命中**(②b);
      · `inject_bp(at, assigns, **kw)`  = `partial(GD.inject_decide, g)` —— ③ 的四个边界点**共用**这一个
        (期望哪一支由 `follow=` 给, 见 `_inject_shot`); `decide` = `CB.resolve_decide(g)` 的产物。
    ⚠ `hit_bp` 给 `None` 时写钟仍会发出去(直呼 `set_meter_clock_set`)——**降级只能降白盒**;
      而 `inject_bp` 给 `None` 时那几次**没有可降级的替身**(注入本身就是白盒那一半), 记"没做成"。
    ⚠ `hit_bp` **没会话时照样返回 None 且触发照发**(`fire_hit` 就是这么设计的), 所以脚本可以直接传
      `partial(GD.fire_hit, g)` 而 g 就是 None —— **不要**在脚本里写 `if g:`。
    ⚠ 注入要 `inject_allow=` 点名(会话构造时给, 见 `inject_allow_names()`), 开会话是**脚本**的事。
    """
    recs, why = [], []
    bp = bp or {}
    follow_at = bp.get("follow")
    wb = bool(follow_at and bp.get("vars") is not None)
    if not wb and not wb_waived:
        print("   !! 本次无调试会话 → 断点观测(白盒)整体**未做**; 本次范围 = 仅黑盒"
              "(②b/③b 的内部指令路径未取证 —— 见 _direct_trigger 与 CLAUDE.md『两种观测』)")
    elif not wb:
        print("   -- 本次**用户指定**只做黑盒(串口观测) ⇒ 断点观测(白盒)按指定不做")

    # ---- 基线: 双芯钟各读一次(偏差起点) ----
    m0 = read_clock(ser, wait=wait)
    g0 = read_clock(ser, chip="计量芯", wait=wait)
    dm0, dg0 = clock_dt(m0), clock_dt(g0)
    why.append("基线双芯钟: 管理芯=%s 计量芯=%s" % (m0, g0))
    if dm0 is None or dg0 is None:
        return [], why + ["双芯钟读不出 ⇒ 偏差起点无从定位, 不写不动, 半途中止"], None
    why.append("基线双芯差 = %+.0fs(计量芯 − 管理芯)" % (dg0 - dm0).total_seconds())

    # ================= 判据②: 偏差 > 1s ⇒ 应跟随(**两个方向都测**) =================
    # ⚠ 为什么补负方向(2026-09-11): 规格写的是"偏差 > 1s 才跟随", **没写方向**; `Diff_Secs` 的
    #   docstring 声称返回绝对差 —— 但**声称不是证据**。只测正向时, 下面两类实现都分辨不出来:
    #     · `Diff_Secs` 其实是有符号、比较却当无符号用 ⇒ 差 −90 绕成巨大正数 ⇒ 照样跟随(看着"对");
    #     · 哪天改成"只认正向偏差" ⇒ **+90 那一次照旧绿**。
    #   ③e 只在小尺度(−1s)上验了对称; 这里补**大偏差的负方向**: 管理芯钟必须**倒退** 90s 才跟得上。
    #   顺带一个好处: 正反两次抵掉 ⇒ ② 跑完两芯钟**净变 ≈ 0**(不是"停在 +90s 等收尾")。
    for _dir, _sgn in (("正", 1), ("反", -1)):
        m_pre = read_clock(ser, wait=wait)
        d_pre = clock_dt(m_pre)
        if d_pre is None:
            why.append("【②%s】拨偏前管理芯钟读不回 ⇒ 这一次不做" % _dir)
            recs.append(rec("②a 拨偏%+ds(%s向)后管理芯钟跟上了" % (big * _sgn, _dir), None,
                            "拨偏前管理芯钟读不回 ⇒ 这一次没做成", crit="②a",
                            falsify="管理芯不跟随 ⇒ 钟纹丝不动/只走了 %.0f 秒(位移仍 ≈ 0)" % settle))
            continue
        tgt = clock_add(m_pre, big * _sgn)
        why.append("【②%s】把**计量芯**钟拨到 管理芯钟 %+ds → %s" % (_dir, big * _sgn, tgt))
        if hit_bp is not None:
            # 这一次**既是 ② 的写、又是 ②b 的取证** —— 下 :1087 断点 → 发 Set → 等命中。
            r = hit_bp(follow_at, set_meter_clock_set, ser, tgt,
                       vars=("objtime",),
                       label="断[B] %s 跟随支 Set_MeterTime(objtime)[%s向]" % (_bptxt(follow_at), _dir),
                       crit="②b",
                       falsify="固件不跟随(阈值写成 >30s 之类、或只认正向)或跟随支被注释掉 ⇒ :1087 永不命中")
            if r is not None:
                recs.append(r)
                why.append("   断点观测: 停在 :1087 ⇒ objtime=%s" % (r.get("vars") or {}).get("objtime"))
        else:
            v, note, dar = set_meter_clock_set(ser, tgt, chip="计量芯", wait=wait)
            why.append("   写计量芯钟(无会话, 直呼): %s / %s (DAR=%s)" % (v, note, dar))
        time.sleep(settle)
        m1 = read_clock(ser, wait=wait)
        dm1 = clock_dt(m1)
        d_tgt = clock_dt(tgt)
        gap = (dm1 - d_tgt).total_seconds() if (dm1 and d_tgt) else None     # 落点相对目标
        moved = (dm1 - d_pre).total_seconds() if dm1 else None               # 位移(仅打印)
        why.append("   等 %.0fs 后: 管理芯=%s ⇒ 落点相对目标 %s(位移 %s)"
                   % (settle, m1,
                      ("%+.0fs" % gap) if gap is not None else "读不回",
                      ("%+.0fs" % moved) if moved is not None else "读不回"))
        # ⚠ **判落点, 不判位移**(2026-09-11 实跑改正, 反向那一次就是这么被冤枉的): 跟随之后管理芯
        #   是**从落点继续自然走时**的, 所以"相对拨偏前的位移"里**混着 `settle` 那几秒**:
        #     正向那一次位移 = +big + settle(偏大, 看着更"对");  反向那一次 = −big + settle(偏小)。
        #   同一条固件行为, 两个方向算出两个数 —— 而 `位移×方向 ≥ big−2` 只对正向成立。
        #   实测: 反向把计量芯拨到 11:48:54, 管理芯老老实实跟到 **11:49:01**(落点 +7s), 却被
        #   "位移 −83s < −88s" 判 FAIL —— **罚的是 settle, 不是固件**。
        #   落点判据与方向无关: 跟随 ⇒ 管理芯跳到目标、之后只往**后**走 ⇒ `0 ≤ gap ≤ big/2`;
        #   不跟随 ⇒ 钟停在原处、只走了 settle ⇒ `gap ≈ settle − big×方向`(正向 ≈ −big, 反向 ≈ +big)。
        #   `-2` 是给**秒粒度**留的余量(目标串与读回的钟都截到秒)。
        ok_big = (gap is not None) and (-2.0 <= gap <= big * 0.5)
        recs.append(rec("②a 拨偏%+ds(%s向)后管理芯钟跟上了" % (big * _sgn, _dir),
                        ok_big if gap is not None else None,
                        "把**计量芯**钟拨到 %s(拨偏前管理芯 %s); 等 %.0fs 后管理芯=%s ⇒ 落点 %s"
                        % (tgt, m_pre, settle, m1,
                           ("%+.0fs" % gap) if gap is not None else "读不回"),
                        crit="②a" if gap is not None else None,
                        falsify="管理芯不跟随 ⇒ 钟**停在原处**、只走完 settle %ds ⇒ 落点 ≈ %+.0fs, "
                                "远在 [−2, +%ds] 之外(跟随则会落到目标上、再自然往前走几秒)"
                                % (int(settle), settle - big * _sgn, int(big * 0.5))))

    # ================= 判据④: 改后时标正常(跟随后落瞬时冻结, 两次覆盖判据接受的两支) =================
    # 两次各自**重新写一次钟**并把 want 的**秒位钉死**(`CLOCK_STAMP_SECS`), 而不是复用 ② 的 `tgt_big`:
    #   `tgt_big` 的秒位是"当时读到多少就是多少", 记录落在哪一支全看运气 —— 首轮恰好落在 "+1 分" 支,
    #   但换个时刻(比如 m0 的秒是 10)同一句就会落在"当分钟"支。**要证"两支都成立"就不能靠运气**。
    # `want` 一律取 `floor(当前分钟) + 2min + 秒位` ⇒ 与写钟前的钟**至少差 60s**(必然跨分钟, 有分辨力)。
    why.append("【④】两次: want 秒位 %s(判据接受『当分钟』与『自然跨下一分钟』两支, 各走一支)"
               % "/".join("%s=%d" % (tag, sec) for sec, tag in CLOCK_STAMP_SECS))
    for sec, tag in CLOCK_STAMP_SECS:
        m_pre = read_clock(ser, wait=wait)
        d_pre = clock_dt(m_pre)
        if d_pre is None:
            why.append("【%s】写钟前读不回(管理芯=%r) ⇒ 这一次不做" % (tag, m_pre))
            recs.append(rec(("④%s 新记录时标落在新钟那一分钟" % ((" " + tag) if tag else "")), None,
                            "写钟前读不回 ⇒ 这一次没做成", crit="④",
                            falsify="RTC 写没生效 ⇒ 新记录时标落在**改前那一分钟**"))
            continue
        tgt4 = d_pre.replace(second=0, microsecond=0) + datetime.timedelta(minutes=2, seconds=sec)
        tgt4s = tgt4.strftime("%Y-%m-%d %H:%M:%S")
        v, note, dar = set_meter_clock_set(ser, tgt4s, chip="计量芯", wait=wait)
        why.append("【%s】写**计量芯**钟 ← %s(改前 %s): %s / %s (DAR=%s)"
                   % (tag, tgt4s, m_pre, v, note, dar))
        time.sleep(settle)                       # 等 SPI 送达、管理芯跟随(探针实测 ≤3s)
        r4, l4 = clock_stamp_evidence(ser, tgt4s, since=m_pre, tag=tag, wait=wait)
        recs += r4
        why += ["【%s】%s" % (tag, x) for x in l4]

    # ================= 判据③: 偏差 ≤1s ⇒ 不应误动 =================
    # **触发通道 = 注入**(2026-09-11 定, 实现 `swdbg.inject_miss`)。早先靠"写计量芯钟造一个 ≤1s 的
    #   偏差", 那条路**不可用**(2026-09-11 实测): 698 往返只有
    #   0.69s, 可计量芯的新时间要等 SPI 时间对象下一次送达(~1 Hz)管理芯才**看得见** —— 这段 1~3s
    #   抖动与 1s 的判据**同量级**。两次实跑因此一次 `:1087` 命中一次没命中(命中的那次管理芯钟还
    #   **后退了 2.56s**, 说明落地偏差远超 +1 的意图) —— 测的是 SPI 时序抽签, 不是固件。
    #   现改为在 `TaskTime.c:1085`(判据那句)停住, 把 `objtime` 直接写成 `g_MeterTime ± 1s`:
    #   精确到字节、不经过任何传输, 也不碰表钟(净变更小)。
    # ⚠ **每次执行前仍要对齐两芯**(`_align_chips`)—— ⚠ 旧注释在这里写过"② 的跟随已经把两芯拉齐,
    #   不再需要对齐", **那句是错的**(2026-09-11 第二次实跑当场推翻): 跟随只把管理芯拉到 `objtime`
    #   = **滞后 ~1s 的 SPI 镜像**, 跟完仍差 ~1s, 恰在阈值内不再纠正; 此后**每一次的停核**又在这
    #   个差上再加 ~0.3s ⇒ 连停三次就把自然跟随推进观察窗里(③d/③e 假命中的根因)。
    why.append("   -- ③ 的注入动作每一次都要**停核**, 故它的前后**量不了**本地钟的阶跃"
               "(观察手段把被测的量动掉了); ③a 因此改成**窗口内不停核**的独立量法 --")
    if inject_bp is None:
        why.append("   !! 注入回调是 None ⇒ ③ 的四次全部没做成(注入没有可降级的黑盒替身)")
    elif decide is None:
        why.append("   !! 注入回调有了、但判定断点没解析出来(`CB.resolve_decide(g)`)"
                   " ⇒ ③ 的四次没做成(不知道停在哪条指令上读判定)")
    # 四个边界点**一次一条记录**(形状与 `fire_hit`/`wait_hit` 同族)。每次自带:
    #   · `at_vars` = **注入之前**那一刻的对照量 —— 首轮实跑里计量芯本来就快 ~1s, 于是
    #     `objtime[0]` 原本就是 `g_MeterTime[0]+1`, **注入恰好写在原值上**; 不把原状态留在记录里,
    #     "注入生效"与"注入是个空操作"在账本里分不出来(`injects` 逐条带原值, `at_vals` 带两串字节)。
    #   · `inject_allow_names()` 已经把这批名字点进白名单(开会话时给的), 换 delta 不用改名单。
    # ⚠ **每次执行前先把两芯拨齐** —— 这是 2026-09-11 第二次实跑抓出来的假命中根因(③d/③e 红,
    #   而固件是对的): 注入**每次都停核**, 管理芯钟是**软件走时**(停核期间不走)而计量芯照跑 ⇒
    #   每停一次管理芯就落后约 0.3s, 而跟随只能把它拉到 `objtime` = **滞后 ~1s 的 SPI 镜像** ⇒
    #   滞后**不归零**、**连停三次攒过 1s**。`Diff_Secs` 是秒粒度的整数比较, 1.5s 的滞后有一半
    #   时候算出 `2 > 1` ⇒ **固件按自己的规矩正常跟随**, `:1087` 被走到 ⇒ 我的 1.5s 观察窗把这次
    #   **自然跟随**记成了"注入那一次被跟随了"。**这不是固件的问题, 是触发通道把被测的量推过了阈值。**
    #   对齐(写计量芯 = 管理芯)让滞后每次都从 0 起算 ⇒ 单次只攒 ~0.3s, 永远够不到 `> 1` ⇒
    #   窗口里剩下的任何命中**只可能**来自我们注入的那一次执行。详见 `_align_chips`。
    #   ⚠ 只在对齐**有意义**时才做(有注入会话 = 真会停核); 没会话时不做也**照说**一句, 不静默。
    if inject_bp is not None:
        steady_recs = []
        for shot in INJECT_SHOTS_STEADY:
            _align_chips(ser, why, settle=align_settle, wait=wait)
            steady_recs.append(_inject_shot(ser, shot, inject_bp, why, decide=decide))
    else:
        why.append("   !! 注入会话缺席 ⇒ 这三次没停核, 也**没对齐**(滞后无从谈起)")
        steady_recs = [_inject_shot(ser, shot, inject_bp, why, decide=decide)
                       for shot in INJECT_SHOTS_STEADY]
    recs += steady_recs
    # ================= 判据③a: 偏差 ≤1s ⇒ 本地钟未被拉走(**不停核**窗口) =================
    # ⚠ 位置是承重的: 它必须排在三次**之后**(那三次每次都停核 —— 停核期间管理芯钟不走, 落在窗口里
    #   就是"观察手段把被测的量动掉"), 又要排在 ④c **之前**(那一次期望跟随, 一定会把管理芯钟前推)。
    # ⚠ 两个窗口各走一侧: 自然稳态(对齐后系统自落在 |差| ≤ 1)/ 把计量芯拨到管理芯另一侧。跟随的
    #   方向两侧相反(管理芯被拉向前 / 向后), 只测一侧看不出"只认一侧"的实现。
    for _sh, _tag in ((None, "自然稳态"), (2, "计量芯拨到另一侧")):
        _rh, _lh = clock_hold_evidence(ser, release=release, crit="③a", samples=samples,
                                       gap=wall_gap, wait=wait, settle=settle,
                                       align_settle=align_settle, tag=_tag, shift=_sh)
        recs += _rh
        why += _lh
    # ---- ④c: 期望**跟随**的那一次。排在三次之后: 它一定会把管理芯钟前推, 不能落在 ③a 的窗口里 ----
    for shot in INJECT_SHOTS_SHIFT:
        recs.append(_inject_shot(ser, shot, inject_bp, why, decide=decide))
    why.append("   ⚠ ③a 用的是**不停核窗口**(窗口里一次都不碰调试器) —— 旧量法把停核的账"
               "(0.24~4.8s/次, 比 1s 的判据还大)算进了被测的量里, 那是**量法的毛病**, 不是"
               "台面的: 管理芯钟是软件走时, 停核它就停走")
    why.append("   ⚠ ③ 的承重墙是 ③b/③c/③d/③e —— 注入把差钉在 +2/+1/0/−1, 再**停在判定指令上"
               "就地读**: 单步落点落到 `fallthru`(= `Set_MeterTime` 的第一个字节)⇔ 跟随支被执行;"
               " 落到 `target` ⇔ 没被执行。**判的是那条指令自己**, 没有窗口、没有『这一停归谁』")
    return recs, why, True


def _inject_shot(ser, shot, cb, why, decide=None):
    """③ 的一个边界点 = 一次。期望跟随与否由 `shot["expect"]` 给出(`inject_decide(..., follow=…)`)。

    为什么四个边界点现在**共用同一个回调**(`GD.inject_decide`): 旧口径下"期望命中"与"否定期望"
    是两个函数(`inject_hit`/`inject_miss`), 因为它们各自要拿 `stop_latency` 与窗口比 —— 而**那把
    尺子已经整条换掉了**(见 `INJECT_DECIDE`)。新尺子是"停在判定指令上就地读", 期望哪一支只是
    `follow=True/False` 一个参数 ⇒ 分成两个函数已无意义, 分成两条口径(=两处口径可能分叉)更危险。
    """
    delta, crit = shot["delta"], shot["crit"]
    want_follow = shot["expect"] == "hit"
    name = "③%s 差 %+ds ⇒ 判定指令就地读判定: %s" % (
        crit[-1], delta, "该跟随(差 > 1)" if want_follow else "不该跟随(差 ≤ 1)")
    falsify = ("固件把阈值写成 >%d ⇒ 差 %+ds 时判定不跟随 ⇒ 这一次**落点跑到跳走那一支**"
               % (abs(delta), delta)
               if want_follow else
               "固件把阈值写成 ≥%d 或无条件跟随 ⇒ 差 %+ds 时判定照样跟随 ⇒ 这一次**落点落在"
               "被保护那一支**" % (max(abs(delta), 2), delta))
    if cb is None or decide is None:
        print("   !! %s —— 没有注入回调/没解析出判定断点 ⇒ 这一次不做(注入没有可降级的黑盒替身)" % name)
        return rec(name, None, "没有调试会话(或判定断点没解析出)⇒ 注入观测这一次没做成",
                   crit=crit, falsify=falsify)
    # ⚠ `delta < 0` 必须等**秒足够大**再执行: `g_MeterTime[0] + (-1)` 在秒 = 0 处会绕成 255
    #   (`unsigned char`)⇒ 造出来的是"差 255 秒"而不是"差 1 秒", 固件**该**跟随 ⇒ 会**冤枉固件**。
    if delta < 0:
        s = wait_second_at_least(ser, lo=3)
        if s is None:
            return rec(name, None, "等不到秒 ≥3(避免 -1 绕成 255)⇒ 这一次没做成",
                       crit=crit, falsify=falsify)
        why.append("   ③%s(差 %+ds): 先等秒 ≥3(读到 %s), 免得 -1 绕成 255" % (crit[-1], delta, s))
    r = cb(INJECT_AT, inject_assigns(delta), decide=decide, follow=want_follow,
           at_vars=("objtime", "g_MeterTime"), crit=crit, falsify=falsify,
           timeout=INJECT_TIMEOUT,
           label="注入 %s objtime←g_MeterTime%+ds → 停在判定指令看落点"
                 % (_bptxt(INJECT_AT), delta))
    if r is None:
        return rec(name, None, "没有调试会话 ⇒ 没做成", crit=crit, falsify=falsify)
    if r.get("injects"):
        why.append("   ③%s(差 %+ds)注入账: %s" % (crit[-1], delta, "; ".join(r["injects"])))
        why.append("      注入前那一停读到: objtime=%s  g_MeterTime=%s"
                   % ((r.get("at_vals") or {}).get("objtime"),
                      (r.get("at_vals") or {}).get("g_MeterTime")))
    # 判定的原始读数**无条件打出来** —— 它是这条判据的地基, 事后复核要能自己看:
    # 判定指令落在哪、前一句比的是什么、寄存器读出多少、单步落点归哪一支。打出来, 结论就不是我拍的。
    _d = r.get("decide") or {}
    if r.get("injects"):
        why.append("      判定就地读: %s @%s(前一句 %r)  r0=%s  单步落点=%s(fallthru=%s / target=%s)"
                   % (_d.get("loc"), ("0x%08X" % _d["addr"]) if _d.get("addr") else "?",
                      _d.get("prev"),
                      ("0x%X" % r["regs"]["r0"]) if (r.get("regs") or {}).get("r0") is not None else "?",
                      ("0x%08X" % r["step_pc"]) if r.get("step_pc") is not None else "**没走成**",
                      ("0x%08X" % _d["fallthru"]) if _d.get("fallthru") else "?",
                      ("0x%08X" % _d["target"]) if _d.get("target") else "?"))
    return r



def clock_stamp_evidence(ser, want_ts, since=None, pre=None, tag="跟随改钟后", crit="④",
                         falsify=None, settle=6.0, wait=3.0):
    """判据④「改后时标正常」的证据: 跟随后**新落一条瞬时冻结**, 其记录时标落在**新钟那一分钟**。

    为什么这么证(而不是读 `Set_MeterTime` 自己): `Set_MeterTime`(TaskTime.c:351)只做
    `Copy_Data` + `Set_RTCTime`, **不产生任何记录** —— "改完钟, 时标还正常吗"在它身上看不出来。
    改看**下一条落库记录的时间戳**: 它由同一颗 RTC 打戳, 时标没生效/打戳跑偏都会在这里显形。
    这条**不是** ②a 的复述: ②a 只证"钟被改了", ④ 证"改完之后拿这颗钟给记录打戳还是对的"。

    用**瞬时冻结**而不是分钟冻结: 本表分钟冻结实测是 **15 分钟**粒度(`read_freeze_row(subclass=2)`
    读到 10:15:00/10:00:00/09:45:00), "等它落一条"要十几分钟; 触发型的瞬时冻结当场就落
    (`698.action.freeze.immed` 广播动作 → OAD 50000200 回读)。

    ⚠ **比到分钟为止, 不是秒**(2026-09-11 首跑踩到): 瞬时冻结记录的时标是**分钟粒度** —— 实测
    10:33:47 触发, 落库写成 `10:33:00`(秒被清零)。按 ±3s 比秒会把它误判成 FAIL。
    ⚠ **而"落在哪一分钟"必须容得下自然跨分钟**(2026-09-11 第二跑踩到): 从"写钟"到"记录落库"
      中间隔着 settle 秒的**真实时间**, 表钟自己会往前走。写下去那一刻若离分钟边界不足 settle 秒
      (实测 `want=11:12:58`, 8 秒后表钟已跨进 11:13), 记录就落在**下一分钟** —— 那是**固件正常**
      (它拿自己那一刻的钟打戳, 打的对). 判"必须同一分钟"会把这条正常行为记成 FAIL(那一次就是这么红的)。
      ⇒ 判的是 `记录那一分钟 − want 那一分钟 ∈ {0, +1 分}`: 同一分钟或**自然跨过一分钟**;
        落在 want **之前**的那些分钟一律不算 —— 那正是本判据要抓的(写没生效 ⇒ 仍拿旧钟打戳)。
      · `want_ts` = 拨到的新钟(即**改后**的钟);
      · `since`   = **改之前**的钟 —— 用来守"这次比较有没有分辨力": 若改前改后**同一分钟**,
        那"记录落在新钟那一分钟"是改不改钟都成立的 (**假通过**), 此时记 `ok=None`(没造成可分辨状态)
        而不是记满足。所以 `CLOCK_BIG` 取 90s 让它必然跨分钟。
    `pre` 可传入**触发之前**读到的 最新一条(更严: 强制要求序号变化); 不给则本函数当场读一条。
    返回 `(recs, lines)`。
    """
    falsify = falsify or ("RTC 写没生效 / 记录打戳用的是旧钟 ⇒ 新记录时标仍落在**改前那一分钟**"
                          "(或读不到新记录)")
    recs, lines = [], []
    if pre is None:
        pre = read_freeze_row(ser, subclass=0x00, pos=1, wait=wait)
    lines.append("④基线 瞬时冻结最新一条: %s" % pre.get("reason"))
    r = send_frame_id(ser, "698.action.freeze.immed", wait=wait)
    lines.append("④触发广播瞬时冻结: %s / %s" % (r["verdict"], r["reason"]))
    time.sleep(settle)
    post = read_freeze_row(ser, subclass=0x00, pos=1, wait=wait)
    lines.append("④改后 瞬时冻结最新一条: %s" % post.get("reason"))
    dpost, dwant, dsince = clock_dt(post.get("ts")), clock_dt(want_ts), clock_dt(since)
    same_min = lambda a, b: bool(a and b and a.strftime("%Y-%m-%d %H:%M") == b.strftime("%Y-%m-%d %H:%M"))
    seq_ok = (post.get("seq") is not None and pre.get("seq") is not None
              and str(post["seq"]) != str(pre["seq"]))
    if dpost is None or dwant is None:
        recs.append(rec(("④%s 新记录时标落在新钟那一分钟" % ((" " + tag) if tag else "")), None,
                        "时标读不回(改后=%s 期望=%s) ⇒ 本次不做判据" % (post.get("ts"), want_ts),
                        crit=crit, falsify=falsify))
        return recs, lines
    # "记录落在哪一分钟" —— 允许**自然跨过一分钟**(见 docstring 的 ⚠)。`drift` 单位=分钟数差:
    # 0 = 同一分钟; 60 = 写钟后表钟自己走进了下一分钟(写钟→落库之间隔了 settle 秒, 正常);
    # 负值 = 记录落在 want **之前** ⇒ 拿旧钟打的戳(本判据要抓的 FAIL)。
    want_min = dwant.replace(second=0, microsecond=0)
    drift = (dpost.replace(second=0, microsecond=0) - want_min).total_seconds()
    ok_on_minute = 0.0 <= drift <= 60.0
    discriminating = not same_min(dsince, dwant)      # 改前/改后不同分钟 ⇒ 这次比较才有分辨力
    detail = ("改后时标=%s 拨到的新钟=%s ⇒ 记录那一分钟在 want %s; 序号 %s→%s"
              % (post.get("ts"), want_ts,
                 "当分钟" if drift == 0.0 else ("+1 分(写钟后自然走进, 正常)" if drift == 60.0
                                              else "**之前 %+.0f 分**" % (drift / 60.0)),
                 pre.get("seq"), post.get("seq")))
    if not discriminating:
        detail += "; ⚠ 改前(%s)与改后同一分钟 ⇒ 这次比较没有分辨力" % since
        lines.append("   ⚠ 拨偏量没有跨过分钟边界 ⇒ 本判据这次证不了(记未定论, 不记 FAIL)")
    if not seq_ok:
        detail += "; ⚠ 序号没变 ⇒ 没落**新**记录(拿旧记录比时标不算证据)"
    ok = (ok_on_minute and seq_ok and discriminating) if discriminating else None
    lines.append("   " + detail)
    recs.append(rec(("④%s 新记录时标落在新钟那一分钟" % ((" " + tag) if tag else "")), ok, detail, crit=crit, falsify=falsify))
    return recs, lines


def bill_freeze_criteria():
    """4-6「结算日冻结」的预设判据条目(源 = ledger.md 4-6 的 I 列「判过: …」)。

    与 3-2 同一套用法(见 common/judge.py): 测试**前**定死, 测试后只数它满足了几条。
    """
    return {
        "①边界": "结算日边界自然生成一条结算冻结(链A 自然跨 0 点)",
        "②快照": "电量 = 结算时刻的快照(记录整列 47B 逐字节 == 当前电能量对象整列)",
        "③账期": "账期对得上(冻结时标日 == 该结算日)",
        "④改日": "改结算日也触发一次(链B 每次改动恰 +1 条)",
        "⑤对象表": "TAB_FrezObj 第 4 行翻出的 OAD == 出厂表登记的那 16 项(电能集合 00000400 + "
                 "8 个分项电能量 + 4 个基波/谐波总电能 + 20310200 + 2E600200 + 2E610200)",
        "⑥结算日范围": "转存边界落在每月 1 日至 28 日内的整点: 写 1 号、28 号被收下且回读命中; "
                    "写 29 号被拒(回读仍是原值) —— 与规范『或在每月的 1 日至 28 日内的整点时刻』一致",
        "⑦容量": "存储上 12 个结算日: 结算冻结的存储深度 == 12, 且一次补冻上限 == 12",
        "⑧需量复零": {"text": "每月第 1 结算日转存的同时当月最大需量复零",
                  "unprovable": "本机固件没有可观测的需量载体: 冻结对象表 12 行里 0x10 开头的 OAD "
                                "一个都没有(探针读 TAB_FrezObj/TAB_SelObj 逐行核过), 结算冻结记录里"
                                "没有需量列, `Clear_DayFreCurDmd` 在 TaskMetering.h 有声明、全固件无"
                                "定义无调用 ⇒ 本台量不到『复零的是哪一个量』, 这一条答不出 falsify; "
                                "要证须先由固件补出需量载体(变量或记录列), 再按它观测"},
        "⑨需量补NULL": {"text": "非第 1 结算日的那几条记录里需量读回补 NULL",
                    "unprovable": "同上: 结算冻结记录里根本没有需量列, 698 按需量 OAD 读不出这一列, "
                                  "所以『不转存』与『补 NULL』在本台都无从观测 —— 固件 `TaskFreeze.c` "
                                  "里那段 `Set_Data(…, 0xFF, …)` 也因需量对象不在对象表里而恒不命中; "
                                  "要证须先由固件补出需量列"},
    }


BILLFREZ_FALSIFY = {
    "①边界": "跨 0 点没生成冻结 / 生成两条 ⇒ 序号不恰 +1; 或时标不是结算日 0 点 ⇒ 账期对不上",
    "②快照": "记录里没写电量整列/写错对象(lead 位不是 组合14+正向15)/与当前电能量对不上",
    "③账期": "跨 0 点没生成冻结 / 生成两条 ⇒ 序号不恰 +1; 或时标不是结算日 0 点 ⇒ 账期对不上",
    "④改日": "改结算日不触发(或一次改动落两条) ⇒ 序号不恰 +1",
    "⑤对象表": "出厂冻结对象表的第 4 行不是出厂登记的那批电量/金额对象, 或对象号查错了表 ⇒ 逐项比当场不符",
    "⑥结算日范围": "结算日的范围判定不是 1..28(29 号也被收下), 或不看范围就写 ⇒ 29 号写后回读变了; "
                "范围判定反了 ⇒ 1 号/28 号反而被拒",
    "⑦容量": "出厂存储信息的深度不是 12、或补冻上限不是 12 ⇒ 与规范『至少能存储上 12 个结算日』对不上",
    "⑧需量复零": "本机没有可观测的需量载体 ⇒ 这一条不作为固件证据(见 criteria 里那条的 unprovable)",
    "⑨需量补NULL": "本机没有可观测的需量列 ⇒ 这一条不作为固件证据(见 criteria 里那条的 unprovable)",
}


def bill_freeze_evidence(row, base_seq=None, want_ts_day=None, tag="", crit=None, falsify=None):
    """把 `read_freeze_row`/`settle_across_master` 的**一次读数**判成证据记录(common/judge.rec 形状)。

    判的是两件与 4-6 判据直接对应的事(各自的"什么固件会让它 FAIL"由调用方经 `falsify=` 说清):
      · `base_seq` 给定时: 最新结算冻结序号应恰 == base_seq + 1(每次事件**恰落一条**)。
        ⚠ 结算冻结序号是**单调计数**(实测 42→43→44 / …→9 / …→45), 不是 3-2 切换冻结那种 2 格
          环形缓冲 —— 所以这里能直接比 +1; 若哪天固件改成环形, 这里会红(红比静默好)。
      · `want_ts_day`("YYYY-MM-DD")给定时: 记录时标日应 == 该日(判据③账期: 账期无独立列,
        由冻结时标隐含 —— 见总纲 §616)。
    `row` 为 None(读回无行/触发没走成) ⇒ `ok=None`(**没做成**, 不是 FAIL)。
    """
    if not row:
        return judge.rec(tag or "结算冻结读回", None, "无记录行(读回无应答/触发没走成)",
                          crit=crit, falsify=falsify)
    if base_seq is None:
        # **没有基线就没有"恰 +1"可言** —— 不静默跳过这一半(跳过 = 偷偷把判据降级成"只要有条记录")。
        return judge.rec(tag or "结算冻结", None, "基线序号没读到(基线那一次无行) ⇒ 判不了『恰 +1 条』",
                          crit=crit, falsify=falsify)
    seq, ts = row.get("seq"), row.get("ts")
    got, want = [], []
    if base_seq is not None:
        got.append("序号 %s(基线 %s)" % (seq, base_seq))
        want.append("序号 == %s+1" % base_seq)
    if want_ts_day:
        got.append("时标 %s" % ts)
        want.append("时标日 == %s" % want_ts_day)
    # ⚠ 三态: 序号/时标**没读回来**(`None`)时, 手写的 `and` 链会当场变 False ⇒ 记成"序号不对/
    #   时标日不对" —— 那是把探针没读到说成固件错。走 `tri_eq` 让它回 `None`(未定论)。
    #   (`row` 本身为假、`base_seq` 没基线的两条已在上面各自 return None, 到这里的 row 是有行的。)
    _pairs = [(seq, base_seq + 1)]
    if want_ts_day:
        _pairs.append((ts[:10] if ts else None, want_ts_day))
    ok = judge.tri_eq(*_pairs)
    return judge.rec(tag or "结算冻结", ok, "; ".join(got) + " ← 期望 " + " 且 ".join(want),
                      crit=crit, falsify=falsify)


def next_billday_eve(clock_str, billday, force=None):
    """算链A目标 → (前夜 'YYYY-MM-DD 23:59:40', 结算日 'YYYY-MM-DD').
    force 给定时把 force 直接当"结算日前夜"、只补结算日=次日(脚本 TARGET_FORCE 手写覆盖用, clock_str 可 None);
    否则按表钟 clock_str 取"严格晚于现在"的下一个 d0 号结算日(跨月/跨年含 billday=1 eve 落月末, 交给 datetime)。
    纯函数, 不碰串口。"""
    if force:
        ev = datetime.datetime.strptime(force, "%Y-%m-%d %H:%M:%S")
        return force, (ev + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    cur = datetime.datetime.strptime(clock_str, "%Y-%m-%d %H:%M:%S")
    y, m = cur.year, cur.month

    def bill_dt(yy, mm):
        import calendar
        dd = min(billday, calendar.monthrange(yy, mm)[1])
        return datetime.datetime(yy, mm, dd)

    cand = bill_dt(y, m)
    if cand <= cur:
        m += 1
        if m > 12:
            m, y = 1, y + 1
        cand = bill_dt(y, m)
    eve = cand - datetime.timedelta(days=1)
    return eve.replace(hour=23, minute=59, second=40).strftime("%Y-%m-%d %H:%M:%S"), cand.strftime("%Y-%m-%d")


# ⚠ 2026-09-11 **删掉**了两个死函数 `judge_freeze_inc` 与 `all_passed`(无任何调用点)。
#   删它们的理由不是"占地方", 是**它们会当模板**: 两个都紧挨着已修好的 `bill_freeze_evidence`,
#   下一个写冻结判据的人最容易照抄隔壁那个"看着挺像"的:
#     · `judge_freeze_inc` 返回**裸 bool 无三态** —— "最新一条 无读回"记成 `False`(固件不对),
#       而"无基线"直接 `ok = True`(凭空判过)。两处正是 `judge.py` 模块头点名批的折法。
#     · `all_passed` 写着 `bool(hard) and all(hard)`: **空表判 FAIL**, 而 `None` 被 `hard` 筛掉、
#       **不进分母** ⇒ "读不回来"这一档在计数里直接蒸发, 只剩"全过/没过"两态。
#       这正是 `judge.tri_all` 那个函数的立身之因(见其 docstring: "空表判 FAIL、有项没读到也判 FAIL,
#       两者都把'没做成'说成了'固件不对'")。
#   要判"读回来的值对不对"走 `common.judge.tri_eq` / 汇总走 `tri_all`, 别再长出第三个。


def set_meter_clock_set(ser, target, chip="计量芯", wait=3.0):
    """发送 → <管理芯/计量芯>: 698 Set 写表钟 OAD 40000200 → (verdict, reason, dar|None). 本函数自打印.
    这是 OOPT 校钟用的命令形(实测日志 TestResult2609081735, 目标=计量芯 AF=0x15 才 DAR=0;
    管理芯 AF=0x05 同帧回 DAR=0xFF——该对象写已注释禁用)。APDU 逐字节:
      06 01 07 40 00 02 00 1C <年2B大端 2000+y2> <mo> <da> <h> <mi> <s> 00
    判过: 应答 SetResponse(86) DAR=0。回读是否真停住由调用方 read_clock 证实。"""
    peer = _chip_name(chip)
    head = "Set 写表钟 40000200 -> %s" % target
    t = _tp(target)                          # (y2,mo,d,h,mi,s)
    apdu = build_settime_apdu(0x07, *t)
    frame = frame_698(apdu, addr=chip_addr(chip))
    rx = send_frame(ser, frame, wait=wait, tag="set_clock_" + target, peer=peer, what=head)
    d = decode_set_ack(rx)
    if d is None:
        opout(head, frame, "无 SetResponse(86) 应答", ok=False)
        return "FAIL", "无 SetResponse(86) 应答", None
    if d["service"] == "ErrorResponse":
        opout(head, frame, "ErrorResponse DAR=%d(%s)" % (d["dar"], DAR.get(d["dar"], "?")),
               ok=False)
        return "FAIL", "ErrorResponse DAR=%d" % d["dar"], d["dar"]
    ok = d["dar"] == 0
    note = "SetResponse DAR=%s(%s)" % (d["dar"], DAR.get(d["dar"], "?"))
    opout(head, frame, note, ok=ok)
    return ("PASS" if ok else "FAIL"), note, d["dar"]


def write_oad_ud(ser, oad, data, chip=None, wait=3.0, tag=""):
    """发送 → <管理芯/计量芯>: 698 Set 写**任意** OAD(通用; 与 set_meter_clock_set 同一个 build_set_apdu)
    → (verdict, note, dar|None). 本函数自打印。
    oad: **必须 4 字节**(OI2+属性1+索引1, 如 `40150201`=备用套时区表第1项); 给 3 字节会组出长度不足的 APDU,
         固件 `DLT698App.c:16270` 回 DAR=253 错误APDU —— 那时是"帧没组对", 不是"这对象写不了"。
    data: 已编码好的**数据域**字节(bytes 或 hex 串, 类型外壳由调用方按对象给)。最省事的用法 = 先
         `read_oad_ud` 同一 OAD, 取应答 `ud[8:]` 原样回写 → **等值写**(状态零变化) —— 这是探
         "这对象能不能写"的标准手段, 不必先搞懂数据域语义。
    判过: 应答 SetResponse(86) DAR=0。**写没真落库由调用方回读证实**(本函数只证固件收下了)。
    常见非 0 DAR: 3 拒绝操作(该对象写被注释禁用) / 6 对象不存在 / 0x0B·20 需厂内(先 645.factory) /
         253 错误APDU(多半是 OAD 少给了一字节) / 254 服务不支持。"""
    o = oad.replace(" ", "")
    if len(o) != 8:
        print("   !! write_oad_ud 需 4 字节 OAD(8 hex), 收到 %r → 组出的 APDU 必被固件回 253 错误APDU" % oad)
    body = bytes.fromhex(data) if isinstance(data, str) else bytes(data)
    apdu = build_set_apdu(0x07, o, body)
    frame = frame_698(apdu, addr=chip_addr(chip))
    peer = _chip_name(chip)
    head = "Set 写 %s +%dB%s" % (o, len(body), ("  " + tag) if tag else "")
    rx = send_frame(ser, frame, wait=wait, tag="set_" + o, peer=peer, what=head)
    d = decode_set_ack(rx)
    if d is None:
        opout(head, frame, "无 SetResponse(86) 应答", ok=False)
        return "FAIL", "无 SetResponse(86) 应答", None
    if d["service"] == "ErrorResponse":
        note = "ErrorResponse DAR=%d(%s)" % (d["dar"], DAR.get(d["dar"], "?"))
        opout(head, frame, note, ok=False)
        return "FAIL", note, d["dar"]
    ok = d["dar"] == 0
    note = "SetResponse DAR=%s(%s)" % (d["dar"], DAR.get(d["dar"], "?"))
    opout(head, frame, note, ok=ok)
    return ("PASS" if ok else "FAIL"), note, d["dar"]


def read_write_equal(ser, oad, chip=None, wait=3.0, tag=""):
    """探"这个 OAD 到底能不能写"的**标准手段**: 读该 OAD → 把应答数据域原样回写(等值写, 状态零变化)
    → (verdict, note, dar, data). 本函数自打印。data = 读回的数据域(bytes)或 b''。
    为什么等值: 不必先搞懂数据域语义就能证"写通路通不通", 且**不改变表上任何值**。
    ⚠ 读回为空(对象不存在/读被拒)→ 直接 FAIL, 没东西可回写。"""
    ud = read_oad_ud(ser, oad, chip=chip, wait=wait)
    if len(ud) < 8:
        print("== [等值写探针] %s: 读回空(ud=%s) → 无法回写" % (oad, ud.hex() if ud else ""))
        return "FAIL", "读回空, 无数据域可回写", None, b""
    data = bytes(ud[8:])
    v, note, dar = write_oad_ud(ser, oad, data, chip=chip, wait=wait,
                                tag="[等值写探针 %s]" % (tag or oad))
    print("   读回数据域 %s(%dB) 原样回写" % (data.hex(" "), len(data)))
    return v, note, dar, data


def settle_across_master(ser, target, timeout=150, wait=3.0, billday=5):
    """4-6 阶段二汇合点: 拨计量芯主钟到结算日前夜 target → 自然跨结算日 0 点 → 读结算冻结判 0 点时标(自打印)
    → 返回最新结算冻结记录 dict(read_freeze_row 结果)或 None(任一步失败/未到跨点)。
    封成一步因为这是"改真钟"而非管理芯 0x14(0x14 只改管理芯 RTC, 会被计量芯 SPI3 回拨;
    而 Set 40000200 → 计量芯 动的是主钟, 管理芯自动跟随, 表钟真正停在 target 等自然跨 0 点)。
    流程: 改钟(Set→计量芯) → 回读双芯确认停住 → 静默轮询到结算日 00:00:00 → 读结算冻结 最新一条 判时标日。
    调用方可用返回 dict 的 ts 与期望结算日比对, 不必再自己读一遍。"""
    # 0) 发送 → 管理芯: 进厂内(Set 写对象过 Chk_SafeMode; OOPT 日志也是先 645.factory 再 Set)
    enter_factory(ser)
    # 1) 发送 → 计量芯: 698 Set 把主钟拨到 target(结算日前夜)
    v, note, dar = set_meter_clock_set(ser, target, chip="计量芯")
    if v != "PASS":
        print("[4-6阶段二] 停: 计量芯 Set 拒写(%s)——改不了主钟" % note)
        return None
    # 2) 回读双芯确认主钟停住 & 管理芯跟随(防被回拨), 比对到"分"
    got_m = read_clock(ser, chip="计量芯")
    got_n = read_clock(ser, chip="管理芯")
    if not got_m or got_m[:16] != target[:16]:
        print("[4-6阶段二] 停: 计量芯回读 %s ≠ 目标 %s(疑似被拒/回拨)" % (got_m, target))
        return None
    print("[4-6阶段二] 主钟已拨定=%s; 管理芯跟随=%s" % (got_m, got_n))
    # 3) 计算结算日 0 点 = target 所在自然日次日 00:00:00
    ev = datetime.datetime.strptime(target, "%Y-%m-%d %H:%M:%S")
    cross = ev.replace(hour=0, minute=0, second=0) + datetime.timedelta(days=1)
    assert cross.day == billday, "目标夜应是结算日前夜, 跨点日=%d ≠ billday=%d" % (cross.day, billday)
    print("[4-6阶段二] 等表钟自然跨 %s(结算日 0 点, 最长 %ss)..." % (cross, timeout))
    t0 = time.time(); reached = False
    while time.time() - t0 < timeout:
        now = read_clock(ser, chip="计量芯", wait=1.2, quiet=True)
        if now and datetime.datetime.strptime(now, "%Y-%m-%d %H:%M:%S") >= cross:
            print("[4-6阶段二] 表钟已达 %s" % now)
            reached = True
            break
        time.sleep(1)
    if not reached:
        print("[4-6阶段二] 停: %s 秒内未自然跨到 %s" % (timeout, cross))
        return None
    # 4) 发送 → 管理芯: 读结算冻结最新1条(应在跨 0 点当刻生成; 序号/时标由 read_freeze_row 打印)
    time.sleep(3)   # 等冻结任务把 0 点记录写完
    r = read_freeze_row(ser, 0x05, 1)
    good = bool(r and r["ts"] and r["ts"][:10] == cross.strftime("%Y-%m-%d"))
    print("[4-6阶段二] 期望冻结时标日 == %s(结算日 0 点)  判定: %s" % (
        cross.strftime("%Y-%m-%d"), "命中" if good else "未命中"))
    return r


# ============================ 5-3『事件记录·掉电』判据与证据(2026-09-11 建) ============================
# 判据链(源码 TaskMetering.c; 本工程 VER_20Edit 已定义 ⇒ 三处 `#ifndef VER_20Edit` 块编译掉。
#        2026-09-11 逐行核过, 行号/地址见 LP_BP_* 一行一处):
#   :591-607 秒心跳 Run_TaskMetering(MSG_SecStep) → :595 `if (Is_PowerOff()) return;`
#            → 依次 Cmp_CompFlag / Chk_OverLoad / **Chk_LostPower** …
#   :2413 state = FALSE
#   :2415 if ((g_Volt[0] < C_60Un) || Is_PowerOff())      ← **判据行**(真代码 @0x24ecc)
#   :2423-2427 g_EventFlg[EV_LostPower] <<= 1; 条件真则 |= 0x01  ← 三秒移位寄存器(去抖之一)
#   :2428-2438 C_EveFltMask(=0x07) 判读: 低三位全 1 ⇒ state=TRUE / 全 0 ⇒ state=FALSE /
#              混合 ⇒ :2438 翻转(取 g_EventSta 的反)
#   :2440-2461 第二级去抖 TAB_LostPDly[{进 4, 退 1}]: 与 g_EventSta 一致则 Tmr=0(:2442);
#              否则 ++Tmr >= delay 时 Recd_LostPower() + g_EventSta=state + Tmr=0
#   :3950-4008 Recd_LostPower(): `sta = g_EventSta[EV_LostPower]`(**旧状态**)
#              sta==FALSE ⇒ **记录开始**(:3991 写全行: 发生时刻@buff[0], 结束时刻清零 :3985,
#                            电量快照 :3986-3987, 上报状态 :3992, 置自动上报标志 :3994)
#              sta==TRUE  ⇒ **记录结束**(:4004 Get_MeterTime(&buff[6]) 写结束时刻,
#                            :4005 secs=Diff_Secs 累计时长, :4006 只写尾段 buff[6..])
#              ⇒ **「恢复」不是新开一行, 是把上一行的结束时刻补上** —— 这就是"发生/恢复两笔"的形状。
# ⚠ 有头无尾守卫 :3958-3974 是**顺序承重**的: 最新行的结束时刻为 0 时, `sta==FALSE` 的"记录开始"
#   直接 return ⇒ 必须**先恢复(补上结束时刻)、再发生**; 反序第二笔会被守卫吃掉(静默少记一笔)。
# ------------------------------------------------------------------------------------------------
# 本台事实(SWD 只读实测 2026-09-11): g_Volt[0]≈124.0V 恒定; C_60Un = TAB_Standard.Un*60,
#   由"判据恒真 + g_EventSta[52] 常驻 TRUE"反推 Un > 206.9V ⇒ 220V 表 ⇒ **C_60Un = 132.0V**。
#   ⇒ 判据每秒恒真 ⇒ g_EventSta[52]=TRUE(170)、g_EventTmr[52] 恒 0 ⇒ 掉电事件在本台**永远锁在「发生」**,
#     自然恢复路径一次都走不到(要 g_Volt[0] ≥ 132V 连续 ≥3 拍 + 4s 去抖), 帧通道也造不出(交流源在台面外)。
#   ⇒ 「恢复」那一笔改走**注入**通道(CLAUDE.md「触发也有两个通道」): 在判据行 :2415 停住写
#     g_Volt[0]=140000, 该拍判据翻假 ⇒ 同拍去抖满(退出延时=1)⇒ 走 :4006 落「记录结束」。
#     **注入只改触发条件** —— 记录的时刻与电量快照仍是固件现读的。
#   ⇒ 其后 SPI 把 g_Volt 刷回 ~124V ⇒ 约 4s(3 拍移位 + 进延时 4s)后固件**自己**走 :3991 落「发生」。
#     这一笔**不注入**, 是真·自然到点 —— 判据①因此有两个通道的证据(串口 + 自然断点)。
LP_EV_CODE = P.LP_EV_CODE
LP_EV_IDX = P.LP_EV_IDX
LP_DLY = P.LP_DLY
LP_VOLT_HIGH = P.LP_VOLT_HIGH
LP_BP_JUDGE = P.LP_BP_JUDGE
LP_BP_WR_START = P.LP_BP_WR_START
LP_BP_WR_END = P.LP_BP_WR_END
LP_BP_RPTSTA = P.LP_BP_RPTSTA
LP_VARS_JUDGE = P.LP_VARS_JUDGE
LP_INJECT_ASSIGNS = P.LP_INJECT_ASSIGNS
LP_RCSD3 = P.LP_RCSD3
LP_COLS7 = P.LP_COLS7
SVD_ISR_ADDR = P.SVD_ISR_ADDR
LP_TRUE_TOKENS = P.LP_TRUE_TOKENS
LP_CRIT_HEAD = P.LP_CRIT_HEAD



def lp_inject_allow():
    """本子项要注入的表达式名字 —— 脚本构造会话时喂 `inject_allow=`(库**不替脚本开会话**)。"""
    return tuple(e for e, _v in LP_INJECT_ASSIGNS)


def lostpower_criteria():
    """5-3 的**预设条目**(源 = ledger.md 5-3 的「观察与判据」I 列)。测试前定死, 证据照它认领。

    拆分说明(每条都答得出 falsify, 见 `lostpower_roundtrip` 内各 add 的 falsify):
      · ① / ② 拆开 —— 「发生」与「恢复」是**两条独立的写库路径**(:3991 与 :4006)。合成一条的话,
        一个"只会记发生、恢复那半根本不通"的固件照样记"满足" —— 而本台恰好就是这种形状
        (124V 台面上恢复路径一次都走不到), 所以这条拆分**当场就有分辨力**, 不是洁癖。
      · ③ 时标单列 —— "落没落库"(:3991/:4006 命中)与"落进去的时刻对不对"是两件事:
        固件若把 Get_MeterTime 写到错偏移, 前两条照样绿。
      · ④ 上报标志 —— 由**唯一调用点** :3994 置起。它既是"发生落库"的旁证, 也是一条独立对外后果。
      · ⑤ 不误记 —— 来自规格「判不过/待核: 乱记→核判据与去抖计时复位」。稳态下 :2442 必须把
        Tmr 复位成 0; 否则计时一路累上去, 每个 delay 秒乱落一条(而 ① 照样绿 ⇒ 不拆就漏)。
      · ⑥ / ⑦ / ⑧ 补的是**规范正文写了、前五条都没量**的三处:
        · ⑥ 规格说「先恢复再发生, 反序第二条不会记上」, 说的是 Recd_LostPower 开头那道守卫
          (:3958-3974)。它的两个出口分工不同: 上一行**有头无尾**而 sta 为假时从 :3965 返回,
          上一行**已完整**而 sta 为真时从 :3972 返回。本台判据恒真 ⇒ 行一有尾 sta 就跟着走,
          后一种组合自然走不到, 所以走注入(在 :2415 把 g_EventSta 写成 TRUE)。
          falsify = 守卫链不在 ⇒ 不命中 :3972, 而是照开一行新的。
        · ⑦ 规范 5-3 第 1 条要「最近 10 次**发生及结束时刻**」—— 两列都得真的落进记录里。
          本条判的就是这两列取不取得到, 与对象登记了哪几列无关(登记表是读回那一路自己的
          查询源, 拿它当期望等于自己跟自己比)。
        · ⑧ 规范 5-3 第 1 条还要「掉电事件的总次数」—— 与容量 10 是两回事: 最近 10 次封顶,
          总次数按次累加不封顶。本条判这两件事同时成立。
    """
    return {
        "①": "判据成立且去抖满时落『发生』一笔: 走 Recd_LostPower 的「记录开始」支(:3991)",
        "②": "判据翻假且去抖满时落『恢复』一笔: 走「记录结束」支(:4006)把结束时刻/累计秒补进上一行",
        "③": "两笔的时标 == 当时表钟(发生时刻在行首 buff[0], 结束时刻在 +6 偏移 buff[6])",
        "④": "『发生』一笔写入后置『需检查主动上报状态』标志(:3994 → TaskReport.c:2229 bFlag=TRUE)",
        "⑤": "稳态不误记: 判据与现有状态一致时去抖计时复位(:2442), 记录数不增",
        "⑥": "上一行已完整却仍报『要发生』时由守卫挡回(Recd_LostPower 的 :3972 出口), 不重开一行",
        "⑦": "记录里『发生时刻』与『结束时刻』两列都真落下去: 698 读回的行首 6 字节非空, "
              "且『恢复』那一笔落下之后 +6 偏移那 6 字节也非空(规范 5-3 第 1 条)",
        "⑧": "掉电事件总次数按次累加且不封顶, 最近 10 次封顶在容量上: 连造 11 回后总次数加满 11, "
              "而索引区实际写回的字节数掉到 3(规范 5-3 第 1 条)",
    }


def read_event_ud(ser, code, pos=1, rcsd=None, chip=None, wait=3.0):
    """发送 → 698 GetRequestRecord 读**事件记录**第 pos 条 → 原始 ud(APDU) 或 b''(静默, 判读归上层)。

    与 `read_record_ud` 只差 OAD 与**属性字节**: 事件记录用 `30<编码><属性>00`, 冻结用 `50<子类>0200`。
    属性按 `_event_req_attr`(Class 24 事件 = 10-evenum, 其余 2) —— 取错会被回 DAR=4。
    RCSD 由调用方给(列选随子项而异) —— 不给则与 `read_event_row` 同口径(序号 + 发生时刻)。
    """
    _code = _event_code(code)
    oad = _EVENT_REC_OAD.get(_code)
    if oad is None:
        return b""
    apdu = build_getrecord_apdu_oad(0x03, oad, rsd=bytes([0x09, int(pos) & 0xFF]),
                                    rcsd=bytes(rcsd) if rcsd else EVENT_RCSD,
                                    attr=_event_req_attr(_code))
    rx = send_frame(ser, frame_698(apdu, addr=chip_addr(chip)), wait=wait,
                    tag="ev_ud_%02x_p%d" % (_event_code(code), pos), peer=_chip_name(chip),
                    what="读事件记录 pos%d 原始 ud (OAD=%s, 上层判读)" % (int(pos), oad))
    return split_apdu(rx or b"")


def event_row3(ud):
    """事件记录单行 ud → {"seq","t_start","t_end","n_ts","raw"} 或 None(不是 85 03 / 无行)。

    定位走 `p698.record_rows`(**逐列按类型字节定长切**) —— 不再作 `06 00 00 00`/`1C` 那种
    字形扫描: 字形会被时标里凑出来的字节骗到(见 `p698.record_seq_at` 那段), 而列长不会。
    两列时标按**列 OAD** 认(`201E0200` = 发生 / `20200200` = 结束), 不再按先后位置认 ——
    列选一换(比如只选 序号 + 结束)位置法就会把结束当成发生读。
    ⚠ 结束时刻回 `D_NULL`(全 0)或全零时标都归成 `None` —— 那是掉电行"有头无尾"的**正常**形态
      (固件在 DLT698App.c:5788-5798 显式写 NULL, :3985 显式清零), 不是读失败;
      把它与"没读到"混成一句, 就会把"未结束"说成"读不回来"。
    `raw` 原样带出: 列选/解码一旦与固件对不上, 复核时能拿它自己看(不靠猜)。
    """
    rows = record_rows(ud)
    if not rows:
        return None
    row = rows[-1]
    vals = list(row.values())

    def _blank(s):
        return None if (not isinstance(s, str) or s.startswith("0000-")) else s

    times = [_blank(v) for v in vals if isinstance(v, str)]
    t_start = row.get(OAD_EV_OCCUR, times[0] if times else None)
    t_end = row.get(OAD_EV_END, times[1] if len(times) > 1 else None)
    return {"seq": vals[0] if vals else None, "t_start": _blank(t_start),
            "t_end": _blank(t_end), "n_ts": len(times), "raw": ud.hex(" ")}


def read_lostpower_rows(ser, positions=(1, 2, 3), chip=None, wait=3.0, quiet=False):
    """读 掉电事件 第 1/2/3 条(1=最新) → `{pos: row|None}`; 每行自打印一行摘要(quiet 关掉)。

    列选 = 序号 + 发生时刻 + 结束时刻(`LP_RCSD3`)。掉电记录布局: 发生时刻 @buff[0..5],
    结束时刻 @buff[6..11](DLT698App.c:2445-2454 `TAB_LostPower`)。
    """
    out = {}
    for p in positions:
        ud = read_event_ud(ser, LP_EV_CODE, pos=p, rcsd=rcsd(*LP_RCSD3), chip=chip, wait=wait)
        row = event_row3(ud) if ud else None
        out[p] = row
        if not quiet:
            # ⚠ 四种"读不出"必须分清, 混成一句会把**固件状态错**说成"表里没记录"(2026-09-14 实踩):
            #   · DAR 打回(:14914 MatchAuth 等) —— **表还在, 是这一问被安全判定拒了**(帧完全合法)
            #   · 记录区为空(条数 0)        —— 该事件一条都没有(台面事实), 与"读不回来"不是一回事
            #   · ud 非空但解不出行         —— 列选/布局与固件对不上(协议侧问题)
            #   · ud 空                    —— 真·静默(总线/串口)
            if row is None:
                print("   [读回] 掉电 pos%d: %s" % (p, _rec_none_reason(ud, "掉电")))
            else:
                print("   [读回] 掉电 pos%d: 序号=%s 发生=%s 结束=%s"
                      % (p, row["seq"], row["t_start"] or "-", row["t_end"] or "(未结束)"))
    return out


def read_svd_isr(ser, wait=2.0, quiet=False):
    """AA80 区2 读 SVD->ISR(APB 0x4001280C) → int 或 None。`Is_PowerOff()` = bit8 为 0(CpuCfg.h:38)。

    这是掉电判据 `(g_Volt[0] < C_60Un) || Is_PowerOff()`(:2415) 的**另一半**。读它是**台面事实**
    (这台表此刻算"有电"还是"没电"), 不是判据 —— 好固件坏固件都读得出, 落不进判据表(CLAUDE.md 第 27 条)。
    区内偏移 = 绝对地址减 APB 基址 0x40000000; 传绝对地址会越界读挂死、整帧静默(见 p645 那份说明)。
    """
    frame = read_aa80_645(2, SVD_ISR_ADDR - 0x40000000, 4)
    rx = send_frame(ser, frame, wait=wait, tag="svd_isr", peer="管理芯",
                    what="读 SVD 中断状态寄存器(掉电判据的另一半)")
    _cmd, seg, _note = decode_645_reply(rx)
    val = int.from_bytes(seg[4:8], "little") if len(seg) >= 8 else None
    if not quiet:
        print("== SVD->ISR = %s ⇒ Is_PowerOff()=%s"
              % ("0x%08X" % val if val is not None else "读不回",
                 ("假" if val & 0x100 else "真") if val is not None else "-"))
    return val


def event_cols(ud):
    """事件记录第 1 条的 ud → `{列OAD: 值}`; 骨架不是成功应答 / 无行 → None。

    与 `event_row3` 的分工: 那个按列名取**三个量**, 这个交出**列集合本身** —— 5-3 判据⑦ 量的
    是"对象登记了哪几列", 所以取不回值时回 None(这一问没做成), 不许拿空 dict 冒充"没有列"。
    """
    rows = record_rows(ud)
    return dict(rows[-1]) if rows else None


def lp_rows_by_seq(rows):
    """`{pos: row}` → `{seq: row}`(丢掉没读到的), 供"按序号找那一行"用(位置会随新记录漂移)。"""
    return dict((r["seq"], r) for r in (rows or {}).values() if r)


def lp_in_window(ts, lo, hi):
    """时标串 ts 是否落在 [lo, hi](三个都是 'YYYY-MM-DD HH:MM:SS')。任一为空 → None(判不了)。"""
    if not ts or not lo or not hi:
        return None
    return (str(lo)[:19] <= str(ts)[:19] <= str(hi)[:19])


def lostpower_roundtrip(ser, wb=None, wb_waived=False, wait=3.0, sample_gap=5.0,
                        rpt_timeout=40.0):
    """5-3 全流程 + 判据(库内单点, 脚本不留): 稳态对照 + 注入抬压(恢复) + 自然掉压(发生) + 上报标志。

    **触发通道**(与"观察观测"正交的那根轴, 见 CLAUDE.md「触发也有两个通道」)—— 本项两条都用到:
      · 「恢复」走**注入** —— 本台交流恒定 124.0V < 132.0V, 判据每秒恒真, `g_EventSta[52]` 常驻 TRUE
        ⇒ 自然恢复路径一次都走不到; 帧通道也造不出(交流源在台面外)。注入点 `:2415`(判据行)只改
        **触发条件** —— 记录的时刻与电量快照仍是固件现读的, 录进证据的 `injects` 逐条写着改了什么。
      · 「发生」**不注入** —— 注入只改 RAM, 下一拍 SPI 把 g_Volt 刷回 ~124V ⇒ 判据自然复真, 约 4s
        (3 拍移位 + `LP_DLY[0]`=4s)后固件**自己**走 :3991 落「发生」。这一笔是真·自然到点。

    **顺序是承重的**(有头无尾守卫 :3958-3974): 最新行的结束时刻为 0 时, `sta==FALSE` 的"记录开始"
    直接 return ⇒ 必须**先恢复、再发生**; 反序第二笔会被守卫静默吃掉。

    **记录行怎么变**(串口侧的形状): 恢复**不新开一行**, 是把上一行的结束时刻补上(:4006 只写尾段,
    带累计秒 secs); 发生才新开一行(:3991 写全行, 结束时刻清零)。故"两笔" = **序号推进 1 +
    旧行的结束时刻从 (未结束) 变成注入那一刻**。

    **三条白盒动作**(库不 import swdbg, 会话由脚本持有, 这里只收回调):
      `wb = {"inj": partial(GD.inject_hit, g), "hit": partial(GD.wait_hit, g),
             "bp": partial(GD.break_at_or_none, g)}`
      · 断[C] 注入抬压 → 停在 `:4006`(「记录结束」写库位置)   —— ②
      · 断[C] 自然掉压 → 停在 `:3991`(「记录开始」写库位置)   —— ①
      · 断[C] 再注入一次 → 停在 `TaskReport.c:2229`(置上报标志) —— ④(第二次发生顺带补 ② 的下一轮)

    返回 `(recs, details, scope)` —— 与 3-1/3-2/2-1 同形: **`scope is None` = 半途中止**(结构信号,
    别让脚本去嗅 `startswith("中止")` 那种文案)。**本函数不总结论** —— 判定只有 `common/judge.py` 一处。
    **厂内态是前置, 因为"读记录"也是受安全判定管的**(2026-09-14 实踩纠错): 本函数原写着
    "不需要 enter_factory —— 没有一个动作帧", **那句是错的**。`CMD_GetRequestRecord` 照样调
    `DLT698App.c:4818 Chk_SafeMode(OI, 0=GET, …)`, 而 `Chk_SafeMode` 的**第一行**
    (`:14752 if (TRUE == Is_EnablePrg()) return DAR_Success;`) 就是"编程/厂内态直接放行";
    不在厂内态时才会走到 `:14914 default: return DAR_MatchAuth`(事件记录 OI=0x3011/0x3012/
    0x3015 都不在 `TAB_SafeModDef` 的免检列里) ⇒ **每条记录读回都被 DAR=20 打回**。
    现象极具欺骗性: **帧结构完全合法、应答照回**, 只是负载里是 `00 14` 而非记录 —— 与"表里没记录"
    长得一样(真正的空是 `01 00 00 00`)。本台上一次 5-3 能读出记录, 是因为那时表**还在厂内态**
    (前一阶段留下的); `scripts/_restore_all.py` 第 [5a] 步第一次真发 `645.exit_factory` 之后就废了。
    故本函数**自己进厂内**(与 `_test_1_2_energy_mirror.py:84` 的既定写法一致), 且**不退** ——
    退厂内是 `_restore_all.py` 第 [5a] 步的事, 必须排在拨钟/合闸/复核之后。
    """
    print("\n===== 5-3 掉电事件: 稳态对照 + 注入抬压(恢复) + 自然掉压(发生) + 上报标志 =====")
    recs = []

    # 进厂内: **读记录同样受 Chk_SafeMode 管**(见 docstring) —— 这一步不是可选前提, 缺了整种观测读空。
    enter_factory(ser)

    def add(label, ok, why, crit=None, falsify=None, obs=judge.SERIAL):
        """`ok` 三态: True 达成 / False 观察到不对 / **None 没做成**(没命中、没读到) —— 见 judge 模块头。"""
        recs.append(rec(label, ok, why, crit=crit, obs=obs, falsify=falsify))
        print("   [%s] %s —— %s" % ({True: "PASS", False: "FAIL", None: "TBD"}[ok], label, why))

    # 断点观测接线: 没有会话时三次全不做(注入没有可降级的黑盒替身), 但**要说全**。
    wb = wb or {}
    inj, hit, bp_of = wb.get("inj"), wb.get("hit"), wb.get("bp")
    have_wb = all(x is not None for x in (inj, hit, bp_of))
    if not have_wb and not wb_waived:
        print("   !! 本次无调试会话 → 断点观测(白盒)整体**未做**; 本次范围 = 仅黑盒"
              "(对外行为可判, 内部指令路径与上报标志未取证 —— 见 CLAUDE.md「凭什么算「真实测试」·两种观测」)")
    elif not have_wb:
        print("   -- 本次**用户指定**只做黑盒(串口观测) ⇒ 断点观测(白盒)按指定不做(J 列须记明本次范围)")

    # ---- ① 前置: 基线 ----
    # ⚠ **空记录区是合规的起点, 不是故障** —— 见 `resolve_event_baseline` 与 5-2 同一处的注释。
    t0 = read_clock(ser, chip="管理芯", quiet=True)
    rows0 = read_lostpower_rows(ser, (1, 2, 3), wait=wait)
    seq0, _bwhy = resolve_event_baseline(ser, rows0, LP_EV_CODE, "掉电",
                                         chip=None, wait=wait) if t0 else (None, "表钟读不出")
    if not t0 or _bwhy is not None:
        why = "%s(表钟=%s)" % (_bwhy or "基线无对照", t0)
        print("   !! %s" % why)
        return [rec("基线读取 ⇒ 中止", None, why, obs=judge.SERIAL)], [why], None
    by_seq0 = lp_rows_by_seq(rows0)
    _b0 = rows0.get(1)
    if _b0 is None:
        print("   基线: 表钟=%s | 掉电记录区**为空**(0 条)⇒ 干净起点(seq0=0, 真记录序号从 1 起)" % t0)
    else:
        print("   基线: 表钟=%s | 掉电 最新一条 序号=%s 发生=%s 结束=%s"
              % (t0, seq0, _b0["t_start"], _b0["t_end"] or "(未结束)"))

    # ---- ⑤ 稳态不误记: 判据与状态一致的这 %ds 里, 记录一条都不该多 ----
    time.sleep(sample_gap)
    rows1 = read_lostpower_rows(ser, (1, 2, 3), wait=wait, quiet=True)
    same = (rows1.get(1) or {}).get("seq") == seq0
    add("稳态 %ds: 掉电记录未新增(去抖计时复位 ⇒ 不误记)" % sample_gap,
        same if rows1.get(1) is not None else None,
        "序号 %s → %s%s" % (seq0, (rows1.get(1) or {}).get("seq"),
                            "" if rows1.get(1) is not None else "(本轮读不回 ⇒ 没做成)"),
        crit="⑤",
        falsify="去抖计时不被 :2442 复位(稳态也一路累加) ⇒ 每个 delay 秒会乱落一条, 序号持续推进")

    # ---- ② 注入抬压 ⇒ 「恢复」(记录结束支 :4006) ----
    t_inj1 = read_clock(ser, chip="管理芯", quiet=True) or t0
    r_end = None
    if have_wb:
        r_end = inj(LP_BP_JUDGE, LP_INJECT_ASSIGNS, watch=LP_BP_WR_END, watch_vars=("secs",),
                    at_vars=LP_VARS_JUDGE, timeout=30.0,
                    label="断[C] 注入 g_Volt[0]=%d(判据翻假) ⇒ 停在「记录结束」写库位置 %s"
                          % (LP_VOLT_HIGH, _bptxt(LP_BP_WR_END)),
                    crit="②",
                    falsify="固件没有『记录结束』这条路径, 或退出延时不是 TAB_LostPDly[1]=1(去抖不满)"
                            " ⇒ 注入抬压后走不到 %s" % _bptxt(LP_BP_WR_END))
    t_inj1b = read_clock(ser, chip="管理芯", quiet=True)
    if r_end is not None:
        # ⚠ `inject_hit` 交回来的是 **judge 记录**(`common/judge.rec`), 它只有
        #   `name/ok/detail/crit/obs/falsify/trig` —— **没有 `hit`**。`hit` 在 `record()` 里就被
        #   拿去定 `ok` 并折进 `detail` 了。2026-09-14 实踩: 这里原先读 `r_end.get("hit")`,
        #   恒为 None ⇒ 明明停在了 :4006(gdb 层自己打的横幅是 PASS), 判据② 却记成 **FAIL**,
        #   把一次成功的取证冤枉成固件有问题。判"命中没有"只认 `ok` 三态: True 命中 / None 没做成。
        print("      断[C] 记录: ok=%s | %s" % (r_end.get("ok"), r_end.get("detail")))
        print("      停时读到: %s" % (r_end.get("vars") or {}))
        for _ln in (r_end.get("injects") or []):
            print("      注入账: %s" % _ln)
        print("      注入前那一停读到: %s" % (r_end.get("at_vals") or {}))
        add("断[C] 注入抬压后落到「记录结束」写库位置(:4006)",
            r_end.get("ok"), r_end.get("detail") or "没命中 —— 未证",
            crit="②", obs=judge.DEBUG,
            falsify="固件不走『记录结束』支 ⇒ 不会停在 %s" % _bptxt(LP_BP_WR_END))
    else:
        add("断[C] 注入抬压 ⇒ 恢复写库", None,
            "没有调试会话(或用户指定只做黑盒)⇒ 注入观测这一次没做成(注入没有可降级的黑盒替身)",
            crit="②", obs=judge.DEBUG,
            falsify="不做这一次时无从判 —— 本记录不作为固件证据")

    # ---- ① 自然掉压 ⇒ 「发生」(记录开始支 :3991) ----
    r_start = None
    if have_wb:
        _bp = bp_of(*LP_BP_WR_START)          # ⚠ 现挂现等: 这一次自己不撤, 命中即由 wait_hit drop
        r_start = hit(_bp, 25.0, vars=(),
                      label="断[C] SPI 刷回低压后自然去抖满 ⇒ 停在「记录开始」写库位置 %s"
                            % _bptxt(LP_BP_WR_START), crit="①",
                      falsify="判据不复真 / 进延时不是 TAB_LostPDly[0]=4 ⇒ 自然那一笔走不到 %s"
                              % _bptxt(LP_BP_WR_START))
    t_after = read_clock(ser, chip="管理芯", quiet=True)
    rows2 = read_lostpower_rows(ser, (1, 2, 3), wait=wait)
    by_seq2 = lp_rows_by_seq(rows2)
    new_seqs = sorted(s for s in by_seq2 if s > seq0)
    if r_start is not None:
        add("断[C] 自然掉压后落到「记录开始」写库位置(:3991)", r_start.get("ok"),
            r_start.get("detail") or "没命中 —— 未证",     # ← judge 记录没有 `hit`(见上 ⚠)
            crit="①", obs=judge.DEBUG,
            falsify="判据不复真或去抖累加门限不对 ⇒ 不会停在 %s" % _bptxt(LP_BP_WR_START))
    else:
        add("断[C] 自然掉压 ⇒ 发生写库", None,
            "没有调试会话(或用户指定只做黑盒)⇒ 白盒这一次没做成", crit="①", obs=judge.DEBUG,
            falsify="不做这一次时无从判 —— 本记录不作为固件证据")

    # ---- ① 串口: 序号推进 + 新行时标 ----
    add("串口 掉电序号推进(新落「发生」行)且新行未结束",
        None if not by_seq2 else bool(new_seqs) and by_seq2[new_seqs[-1]]["t_end"] is None,
        "序号 %s → %s(新行 %s; 新行结束时刻=%s)"
        % (seq0, (rows2.get(1) or {}).get("seq"), new_seqs or "无",
           (by_seq2[new_seqs[-1]]["t_end"] if new_seqs else "-") or "(未结束)"),
        crit="①",
        falsify="固件不在去抖满后落『发生』(或复真后那段路径不通) ⇒ 序号不推进 / 新行结束时刻不为空")
    _t_new = by_seq2[new_seqs[-1]]["t_start"] if new_seqs else None
    add("串口 新「发生」行的发生时刻落在本次窗口(=当时表钟)",
        lp_in_window(_t_new, t0, clock_add(t_after or t0, 15)),
        "发生时刻=%s; 窗口=[%s, %s+15s]" % (_t_new, t0, t_after or t0),
        crit="③",
        falsify="时标取的不是当时表钟(Get_MeterTime 写错偏移/用了旧时间) ⇒ 落窗外或比窗口早")

    # ---- ② 串口: 旧行的结束时刻被补上(恢复不新开行, 只补尾) ----
    # ⚠ 三处兜底: 本次新读的 → 基线那份 → **基线本来就没有行**(空区, seq0=0)时给空 dict。
    #   旧写法末位直接 `rows0[1]`, 空区基线会在**这一行**炸 KeyError —— 而报出来的是"这支脚本接口错"。
    _row_old = by_seq2.get(seq0) or by_seq0.get(seq0) or rows0.get(1) or {}
    _t_end1 = _row_old.get("t_end") if isinstance(_row_old, dict) else None
    add("串口 基线行(序号=%s%s)的结束时刻被补上 = 落了「恢复」一笔(未新开行)"
        % (seq0, ", 基线时记录区为空" if not by_seq0 else ""),
        _t_end1 is not None and lp_in_window(_t_end1, t0, clock_add(t_inj1b or t_inj1, 10)) is not False,
        "结束时刻=%s; 注入窗口=[%s, %s+10s](基线时它是%s)"
        % (_t_end1 or "(未结束)", t_inj1, t_inj1b or t_inj1,
           (by_seq0.get(seq0) or {}).get("t_end") or "未结束"),
        crit="②",
        falsify="固件不把结束时刻补进上一行(或另开一行) ⇒ 基线行结束时刻仍为 0 / 序号多推进一次")

    # ---- ④ 再注入一次 ⇒ 停在置上报标志(唯一调用点) ----
    r_rpt = None
    if have_wb:
        r_rpt = inj(LP_BP_JUDGE, LP_INJECT_ASSIGNS, watch=LP_BP_RPTSTA,
                    watch_vars=("bFlag", "g_CheckAutoRptSta"), at_vars=LP_VARS_JUDGE,
                    timeout=rpt_timeout,
                    label="断[C] 再注入抬压 ⇒ 等「发生」那一笔走到置上报标志 %s"
                          % _bptxt(LP_BP_RPTSTA),
                    crit="④",
                    falsify="Set_CheckAutoRptStaFlag 没被 :3994 调到 / 形参不是 TRUE ⇒ 不会停到 "
                            "%s 或 bFlag != TRUE" % _bptxt(LP_BP_RPTSTA))
    t_last = read_clock(ser, chip="管理芯", quiet=True)
    rows3 = read_lostpower_rows(ser, (1, 2, 3), wait=wait)
    if r_rpt is not None:
        _bv = (r_rpt.get("vars") or {}).get("bFlag")
        _tok = gdb_sym(_bv) or ""
        _iv = gdb_ints(_bv)
        add("断[C] 『发生』一笔走到置上报标志, 且 bFlag == TRUE",
            r_rpt.get("ok") if r_rpt.get("ok") is not True else (_tok in LP_TRUE_TOKENS or _iv == [170] or _iv == [1]),
            "%s; bFlag=%s(枚举名=%s); 停时 g_CheckAutoRptSta=%s"
            "(那一停本行赋值尚未执行, 读到的是**上一笔或上报任务**留下的值 ⇒ 只作参考, "
            "判据只认 bFlag = 调用方传进来的实参)"
            % (r_rpt.get("detail") or "没命中 —— 未证",
               _bv, _tok or "-", (r_rpt.get("vars") or {}).get("g_CheckAutoRptSta")),
            crit="④", obs=judge.DEBUG,
            falsify="固件不置这个标志(或置 FALSE) ⇒ 不会停到 %s / bFlag 读出来不是 TRUE"
                    % _bptxt(LP_BP_RPTSTA))
    else:
        add("断[C] 置上报标志", None,
            "没有调试会话(或用户指定只做黑盒)⇒ 白盒这一次没做成", crit="④", obs=judge.DEBUG,
            falsify="不做这一次时无从判 —— 本记录不作为固件证据")

    # ---- 收尾: 再读一次, 把"两轮"的形状打成一行给人看 ----
    _n3 = sorted(s for s in lp_rows_by_seq(rows3) if s > seq0)
    print("   收尾: 表钟=%s | 本次新增发生行序号=%s | 基线行结束时刻=%s"
          % (t_last, _n3 or "无",
             ((lp_rows_by_seq(rows3).get(seq0) or {}).get("t_end") or "(未结束)")))
    add("参考: 表钟在本次窗口内正常走时(记录时标可与之对拍)",
        clock_add(t0, 10) is not None and t_last is not None,
        "起=%s 末=%s" % (t0, t_last))

    if not have_wb:
        scope = "仅黑盒(用户指定)" if wb_waived else "仅黑盒(无调试会话)"
    else:
        scope = "黑盒+白盒"
    n_fail = sum(1 for r in recs if r["ok"] is False)
    n_tbd = sum(1 for r in recs if r["ok"] is None)
    print("===== 5-3 掉电事件: %d 条证据(FAIL=%d 没做成=%d)  [本次范围: %s] ====="
          % (len(recs), n_fail, n_tbd, scope))
    details = ["%s: %s" % (r["name"], r["detail"]) for r in recs]
    if not have_wb:
        details.append("未做: 断点观测(白盒) —— %s; 要证『记录开始/结束两条写库路径与置上报标志』"
                       "需接 J-Link 重跑(断点见 ledger.md 5-3 的 F 列)"
                       % ("用户本次指定只做黑盒" if wb_waived else "本次无调试会话"))
    return recs, details, scope


# ==================== 5-2『事件记录·过载』判据与证据(2026-09-17 建) ====================
# 判据源 = project/knowledge/_whitebox_ledger/ledger.md 5-2「观察与判据」:
#   发生/恢复两笔 + 去抖秒数 + 时标 + 电量快照正确。
#
# ---- 判据链(源码 Application\TaskMetering.c; 行号逐条核过 `info line`, 命中地址一并记下) ----
#   :591-607 秒心跳 Run_TaskMetering(MSG_SecStep) → 依次 Cmp_CompFlag / **Chk_OverLoad** / Chk_LostPower …
#   :1869 Chk_OverLoad()
#   :1877-1880 state[0..8] 每拍先清零
#   :1882 limit = g_EventSet.OverLoadPlower   ← **注入停靠点 @0x24822**(功率触发下限)
#   :1883 if (limit != 0)                     功能启用判定(本台 ≠ 0)
#   :1887 if ((g_SFlag & TAB_Mask[i]) != TAB_Mask[i])     **相启动判定**
#   :1889 if (g_PowP[i] > limit)              state[0+i] = TRUE(A/B/C 相过载)
#   :1898-1944 反向有功那一路: 另需 (g_PowQuad & (0x01<<i)), 本台 g_PowQuad 恒 0 ⇒ 关着
#   :1948-1968 逆相序那一路: 另需 g_Volt/g_Curr 与计量芯片标志 ⇒ 与本项无关
#   :1984-2000 三拍移位寄存器 g_EventFlg(掩码 C_EveFltMask = 0x07): 全 0⇒FALSE / 全 1⇒TRUE / 混合⇒翻转
#   :2004-2006 与 g_EventSta 一致 ⇒ g_EventTmr = 0(去抖计时复位)
#   :2010 delay = *((INT8U*)&g_EventSet + TAB_RecdId[EV_OverLoadA+i].dly)
#   :2011-2018 delay -= C_EveFlt(3); 若 < C_EveDly(4) 则抬到 4
#   :2019-2023 ++g_EventTmr >= delay ⇒ Recd_OverLoad(idx) @0x24a9e(调用点) → g_EventSta = state → Tmr = 0
#   :3610-3627 Recd_OverLoad 入口: 先读出上一条记录, 过**有头无尾守卫**
#   :3629-3647 「记录开始」支(sta == FALSE): :3631 时刻@buff[0] / :3632 电量@buff[6..85] /
#              :3634 Write_RecdData(id, &buff[0], 0, 86, 0)  @0x25f4a —— **发生写库位置**
#   :3649-3666 「记录结束」支(sta == TRUE):  :3651 时刻@buff[86..91] / :3652 电量@buff[92..] /
#              :3653 Write_RecdData(id, &buff[86], 86, 86, g_EventSec[idx]) @0x25fea —— **恢复写库位置**
#   ⇒ **恢复不新开一行**, 是把上一行的结束时刻补上。故"两笔" = 序号推进 1 + 旧行的结束时刻
#     从「未结束」变成注入那一刻。
#
# ---- 本台事实(2026-09-17 离线核: 源码 + `info line`/`info scope`/`ptype /o`; 无一处靠猜) ----
#   · `g_EventSet.OverLoadPlower`(TP_Eve 偏移 45)≠ 0 ⇒ 过载功能**已在参数区启用**;
#     `g_EventSet.OverLoadDelay` 在偏移 **49**, 与 `TAB_RecdId[21].dly`(源码 :116 那一行的 49)一致
#     —— 这是 `:2010` 那个"按偏移取延时"的确指(`ptype /o TP_Eve` 逐字段核过: 45=OverLoadPlower,
#     49=OverLoadDelay, 结构体 140 字节)。
#   · `g_SFlag` = `0x0E00`(只由 `Init_TaskMetering:407` 上电写一次), 而 `TAB_Mask` = {0x0200, 0x0400,
#     0x0800, 0x0E00}(:1023) ⇒ `(g_SFlag & TAB_Mask[i]) == TAB_Mask[i]` 对 i=0/1/2 **恒成立**
#     ⇒ `!=` 恒假 ⇒ **A/B/C 相过载分支被固件自己这道判定关着**(state[0..2] 恒 FALSE)。
#     ⇒ 只注入 `g_PowP[0]` 不会有任何反应 —— 必须**同时**把 `g_SFlag` 的 A 相那一位(0x0200)清掉。
#     这不是"固件没实现": 判定的上游是计量芯片状态标志。本版 `g_SFlag` 的运行期写者本来有一个
#     (`Clr_CompFlag:1484`), 而 **`Clr_CompFlag` 在本版镜像里是死代码** —— 全树只有它的声明
#     (`:352`)与定义(`:1463`), **一个调用点都没有**。
#   · `g_PowP[]` 同理: 运行期只有 `Init_TaskMetering:456` 与 `Clr_CompFlag:1495` 两个写者,
#     后者是死代码 ⇒ 本台镜像里 `g_PowP` 恒 0、`g_PowP[0] > limit` **永不成立**。
#     ⇒ 「过载」在帧通道下**一次都不会自然发生** —— 这就是"非用注入不可"的结构性理由
#     (与 5-3 的抬压、4-7 的跨结算边界同类: 不是"证不了", 是**触发通道缺一条**)。
#   · 注入**改的是 RAM**, 而这两个量都没有活着的运行期写者 ⇒ 写进去就**持续**到 `close()` 写回。
#   · `g_EventSta[21..29]` 上电全 `0x00`, 秒心跳第一拍由 `Cmp_CompFlag:1403-1408` 归一成 FALSE
#     (0x00 既不是 TRUE=170 也不是 FALSE=85, 正好落进那个 if) ⇒ 判定体从干净的起点开始。
#   · `b_EngyData` 是 `g_EngyData` 的"本周期已填"标志(`Get_RecdData:4218`) ⇒ 稳态下它已被填过,
#     `:4228 Copy_Data(pBuff, g_EngyData, 80)` 拷的是**冻结的那一份**(判据④对拍的前提)。
#
# ---- ⚠ 为什么**不去注入 `g_EventSet`**(哪怕它能把去抖秒数压短) ----
#   `g_EventSet` 受 **CRC 每秒复检**: 秒心跳第一件事 `Cmp_CompFlag:1364`
#   `if (TRUE != Check_CRC((INT8U*)&g_EventSet, LEN_EventSet))` → 不通过就
#   `:1366 Read_ParaData(ID_EventSet, …)` 把参数区**整个读回来覆盖**, 再 `:1398 Fetch_CRC` 重算。
#   `LEN_EventSet = 138`(ParaData.h:284, = 结构体 140 − 2; `Fetch_CRC` 把两字节校验码写在末尾,
#   Common.c:485-487), 而 `OverLoadDelay` 在偏移 49 < 138 ⇒ 对它注入**活不过一秒**, 下一拍的复检
#   就把参数区读回来盖掉 —— 而那一刻去抖还远没累加到。⇒ 本项**一个字节的参数区都不碰**。
#
# ---- 去抖那一段怎么在一分钟内跑完, 又不失真 ----
#   `delay = clamp(g_EventSet.OverLoadDelay − C_EveFlt(3), ≥ C_EveDly(4))`, 本台 = clamp(60−3, ≥4) = 57s。
#   · **第一段(发生)走自然累加**: 只注入判据侧两样, 让固件自己一拍一拍数到 57 ⇒ 判据⑤ 读的
#     `delay` 是固件当场算的, 而且"去抖 57 秒"这件事**真发生了**(注入到命中的墙钟一并记进证据)。
#   · 后面三段为省时间, 在**同一个停点**额外把 `g_EventTmr[21]` 写高: `:2019` 那一拍 `++` 后即达标
#     ⇒ 一趟就落库。⚠ 这一路**只缩短了等待**: `delay` 的计算没被动过, 停在 `:2021` 读回来的
#     仍是固件按参数区现算的值。`g_EventTmr` 不是参数区、不受 CRC 管, 也没有别的复检会盖它
#     (`:2004-2006` 只在 `g_EventSta == state` 时清零 —— 那一趟两者正相反, 所以不清)。
#   · 写多高都行(`:2019` 判的是 `++Tmr >= delay`"够不够", 不是"等不等"): 取 `EVT_TMR_JUMP`。
#
# ---- 有头无尾守卫是**顺序承重**的(:3611-3627) ----
#   读出的上一条记录: 开始时刻月日有效 && 结束时刻月日无效(0x00/0xFF) ⇒ **该行未结束**, 此时只许
#   `sta == TRUE`(写"恢复"把尾巴补上); 否则只许 `sta == FALSE`(写"发生"开新行)。
#   ⇒ 四段必须 发生 → 恢复 → 发生 → 恢复 交替。**末段停在「恢复」**, 于是跑完记录是完整的、
#     `g_EventSta[21] = FALSE`, 下一次跑仍是干净的起点。
#   ⚠ 这不是顺手: 若停在"发生"之后(中断/异常也会造成), 该行有头无尾, 而上电后
#     `g_EventSta[21] = FALSE` ⇒ 守卫那两支**都进不去** —— 过载记录从此写不进去, 只剩
#     "事件清零"一条路(那条本仓明令不碰)。故基线那一步**专门查这个形态**并如实记一条参考记录,
#     有会话时先把它补完(写 `g_EventSta[21]=1` 让守卫放行"恢复"那一支)。
#
# ---- 触发通道: 本项**只有注入**(CLAUDE.md「触发也有两个通道」) ----
#   停 `:1882` 一次写三样: `g_PowP[0] = OverLoadPlower + 1`(判据成立) /
#   `g_SFlag = g_SFlag & ~0x0200`(把相启动判定打开) / 必要时 `g_EventTmr[21]`(缩短等待)。
#   「恢复」= 同一停点写 `g_PowP[0] = 0`(相启动判定那一位不必再动 —— 它没有活着的运行期写者)。
#   判定体随后**自己**走完移位 + 计时 + 写库: 除了进厂内与读记录, 一个帧都不发。
#
# ---- ⚠ 固件里查到的一处越界写(源码级; 与本项判据无关, 但白盒那一段就停在它旁边) ----
#   `INT8U buff[LEN_OverLoad]`(:3604) 而 `LEN_OverLoad = 55 + aLEN_OverLoad(40) = 95`
#   (RecdData.h:280 / :220)—— 但 `Get_RecdData` **不分 typ、恒定拷 80 字节**:
#   `:4228 Copy_Data(pBuff, g_EngyData, 80)`。于是
#     · 「记录开始」支 `:3632 Get_RecdData(&buff[6], 0)` 写 buff[6..85]    —— 刚好装得下;
#     · 「记录结束」支 `:3652 Get_RecdData(&buff[92], 0)` 写 buff[92..171] —— **越界 77 字节**。
#   ⇒ 本项在 `:3653` 那一停只读 `buff[86..91]`(结束时刻, 是正文), 不去读那截被写坏的区。
#   另: `:4222-4225` 用 `g_EngyData[i*20+n]` 而局部量 `i` **从未赋值**(声明在 :4213)—— 判据④
#   拿 `g_EngyData` 与 `buff[6..85]` 对拍, 这一条对拍**两处都盖上这个影子**(所以它证的是
#   "快照取自那一份", 不是"那一份的值对不对")。
# ---------------------------------------------------------------------------
# 测量类事件(过载 0x08 / 功率反向 0x07)的**共用件** —— 两者同一个断点:
#   `Chk_OverLoad`(TaskMetering.c:1869-2027) 一趟算完 **9 个索引**(过载 A/B/C + 反向 总/A/B/C
#   + 电压逆相序 + 电流逆相序), 落到同一个 `Recd_OverLoad` 写库(:3610-3656)。
#   所以"怎么触发 / 怎么读回 / 怎么算去抖"只有一份, 差别全在下面两张 `*_SPEC` 表里。
# ---------------------------------------------------------------------------
TAB_MASK = P.TAB_MASK
EVT_C_EVEFLT = P.EVT_C_EVEFLT
EVT_C_EVDLY = P.EVT_C_EVDLY
EVT_TMR_JUMP = P.EVT_TMR_JUMP
EVT_HOLD_TICKS = P.EVT_HOLD_TICKS
EVT_HOLD_BUDGET = P.EVT_HOLD_BUDGET
EVT_HOLD_WATCH = P.EVT_HOLD_WATCH
EVT_LAND_TICKS = P.EVT_LAND_TICKS
EVT_RCSD3 = P.EVT_RCSD3
OVL_EV_CODE = P.OVL_EV_CODE
OVL_EV_IDX = P.OVL_EV_IDX
OVL_MASK_A = P.OVL_MASK_A
OVL_RCSD3 = P.OVL_RCSD3
OVL_INJ_ON = P.OVL_INJ_ON
OVL_INJ_OFF = P.OVL_INJ_OFF
OVL_INJ_ON_FAST = P.OVL_INJ_ON_FAST
OVL_INJ_OFF_FAST = P.OVL_INJ_OFF_FAST
OVL_INJ_HEAL = P.OVL_INJ_HEAL
RVP_EV_CODE = P.RVP_EV_CODE
RVP_EV_IDX = P.RVP_EV_IDX
RVP_MASK_T = P.RVP_MASK_T
RVP_QUAD_T = P.RVP_QUAD_T
RVP_POW_T = P.RVP_POW_T
RVP_RCSD3 = P.RVP_RCSD3
RVP_INJ_ON = P.RVP_INJ_ON
RVP_INJ_NOQUAD = P.RVP_INJ_NOQUAD
RVP_INJ_OFF = P.RVP_INJ_OFF
RVP_INJ_ON_FAST = P.RVP_INJ_ON_FAST
RVP_INJ_ON_NOQUAD_FAST = P.RVP_INJ_ON_NOQUAD_FAST
RVP_INJ_OFF_FAST = P.RVP_INJ_OFF_FAST
RVP_INJ_HEAL = P.RVP_INJ_HEAL



def ovl_inject_allow():
    """本子项要注入的表达式名字 —— 脚本构造会话时喂 `inject_allow=`(库**不替脚本开会话**)。

    由 `OVL_INJ_*` 五张表单点导出(去重、保序)。白名单**精确匹配符号名**、事后补不了,
    所以这里多写一个名字的代价是"写不进去", 少写一个的代价是"调用期当场抛"。
    """
    return _evt_inject_allow(OVL_INJ_ON, OVL_INJ_OFF, OVL_INJ_ON_FAST,
                             OVL_INJ_OFF_FAST, OVL_INJ_HEAL)


def rvp_inject_allow():
    """9-2 功率反向要注入的表达式名字(同 `ovl_inject_allow` 的规矩: 少一个 = 调用期当场抛)。"""
    return _evt_inject_allow(RVP_INJ_ON, RVP_INJ_NOQUAD, RVP_INJ_OFF, RVP_INJ_ON_FAST,
                             RVP_INJ_ON_NOQUAD_FAST, RVP_INJ_OFF_FAST, RVP_INJ_HEAL)


def _evt_inject_allow(*tables):
    """若干注入表 → 去重保序的表达式名元组(单点: 上面两个具名白名单口都走它)。"""
    out = []
    for _t in tables:
        for _e, _v in _t:
            if _e not in out:
                out.append(_e)
    return tuple(out)


# ===========================================================================
# 两个测量类事件子项的 **spec 表** —— `_meas_event_roundtrip` 的全部差异都在这里
# ===========================================================================
# `neg_legs` 的一条是 dict: `crit` / `txt` / `inj`(注入表或 None) / `win` / `falsify`;
# `win` 写的是**驱动入参的符号名**(`sample_gap` / `jump_timeout`), 由驱动现查现取 ——
# 写死秒数的话, 调用方传了别的值而表里还是老数, 两者会不一致(静默)。
# `inj` 非 None 的那几段依赖"该支在本台启用": 支被 `limit != 0` 关掉时, 注入造不出触发条件,
# `inject_miss` 会**如期**报"没走到" —— 那是**假通过**(它证的是"没触发就不落库", 不是固件对)。
# 故驱动先做一次 **pre-flight**(停 `bp_judge` 读参数区门槛), 门槛读回 0 就把这几段整体记 `None`
# ("本台证不了"), 见下面的 `_branch_off`。
OVL_SPEC = P.OVL_SPEC
RVP_SPEC = P.RVP_SPEC



def _neg_claims(spec):
    """`spec["neg_legs"]` 声称认领的判据号 —— 表驱动段的认领方只有这里。

    为什么需要这个口子: 否定期望段是**表驱动**的(驱动里 `crit=_nl["crit"]`), 认领号不在调用点
    写死, 靠抠源码字面量认领号的办法看不见它 ⇒ 那几条会被误当成"没人认领"。
    ⚠ 这样取出来的号**验不了出处**, 故调用方各带两条断言:
      号都在预设条目里 / 每段都写得出 falsify。**不写那两条就别调这个口子。**
    """
    return tuple(l["crit"] for l in spec["neg_legs"])


def ovl_neg_claims():
    """5-2 表驱动的否定期望段认领的号(見 `_neg_claims`)。"""
    return _neg_claims(OVL_SPEC)


def rvp_neg_claims():
    """9-2 表驱动的否定期望段认领的号(見 `_neg_claims`)。"""
    return _neg_claims(RVP_SPEC)


def overload_criteria():
    """5-2 / 9-3 的**预设条目**(源 = ledger.md 5-2 的「观察与判据」I 列 + 操作步骤 H 列)。测试前定死。

    **9-3(附录E 复验)与本项同条目** —— ledger.md 9-3 的判据栏原文就是「判据同 5-2」, 且规格 G 列
    明写「与 5-2 过载同一个断点」。故 `_test_9_3_overload.py` 直接调本函数、**不另抄一份**:
    抄一份的话, 两处条目一旦分叉, 两个脚本**单看都绿**而"附录 E 复验的是不是同一批条目"没人回答得了。
    ⚠ 首行那两个编号是**认领声明**: 5-2 与 9-3 都对账到本函数 ⇒ 两个都得写在首行里,
    改首行时别把它们删掉。

    拆分说明(每条都答得出 falsify, 见 `overload_roundtrip` 内各 add 的 falsify):
      · ① / ② 拆开 —— 「发生」与「恢复」是**两条独立的写库路径**(:3634 与 :3653)。合成一条的话,
        一个"只会记发生、恢复那半根本不通"的固件照样记"满足"。
      · ③ 时标单列 —— "落没落库"与"落进去的时刻对不对"是两件事: 固件若把 Get_MeterTime 写到
        错偏移, 前两条照样绿。
      · ④a / ④b 拆开 —— 规格写的是"电量快照正确", 那是两个问: ④a 问"拷的是不是那一份数据"
        (`buff[6..85] == g_EngyData`), ④b 问"那份数据等不等于当时真实电能"。④b 拿 g_EngyData
        当期望值是**同源**, 固件把那笔账本身算错照样绿 ⇒ 声明本台不可证(要记录列解码器),
        **不是**把它从账上抹掉。
      · ⑤ 去抖秒数 —— 规格单列的一条, 也是本项唯一能把 `:2011-2018` 那两句算式钉住的地方。
      · ⑥ 稳态不误记 —— 判据不成立时 :2004-2006 必须把 Tmr 复位成 0; 否则计时一路累上去,
        每个 delay 秒乱落一条(而 ① 照样绿)。
      · ⑦ / ⑧ 拆开 —— 规范 5-2 第 1 条要的是两件事:「总次数、总时间」与「最近 10 次」。
        ⑦ 是近次那半(封顶在容量上、最早的被顶掉), 串口读回条数就判得了;
        ⑧ 是总量那半, 而总次数只写在索引区、698 读回不带它 ⇒ 本条声明本台不可证
        (理由与要证须做什么写在条目里), **不是**把它从账上抹掉。
    """
    _cap = OVL_SPEC["rec_quota"]        # 该口的记录区容量 = NUM_OverLoad(RecdData.h:153)
    return {
        "①": "判据成立且去抖满时落『发生』一笔: 走 Recd_OverLoad 的「记录开始」支(:3634 写库位置被执行); "
              "串口侧该口序号推进 1, 且新行的结束时刻为空",
        "②": "判据翻假且去抖满时落『恢复』一笔: 走「记录结束」支(:3653 写库位置被执行), "
              "把结束时刻补进**上一行**(序号不推进 —— 恢复不新开一行)",
        "③": "两笔的时标 == 当时表钟(发生时刻在行首 buff[0..5], 结束时刻在 buff[86..91])",
        "④a": "『发生』一笔的电量快照**取自固件那份电量数据**: 写库那一刻 buff[6..85] 与 "
               "g_EngyData 逐字节相同(指令路径: 固件把那份数据拷进记录正文, 不是拷别处的东西)",
        "④b": {"text": "电量快照的值 == 当时真实电能(规范『电量快照正确』那半)",
               "unprovable": "buff[6..85] 与 g_EngyData 比只证了『拷的是那一份』—— 两边同源, "
                             "固件把 g_EngyData 本身算错照样绿。要证须把记录正文那 80 字节按列 OAD "
                             "解回六种电能, 与同一刻 698 读回的六种电能比 ⇒ 要先有一个记录列解码器"
                             "(本仓还没有)"},
        "⑤": "去抖秒数 = clamp(g_EventSet.OverLoadDelay − C_EveFlt(3), 不低于 C_EveDly(4)): "
              "在 :2021 读回的 `delay` 与按停点处现读的 OverLoadDelay 现算的值相同 —— "
              "**下钳位那一支(OverLoadDelay ≤ 7)在本台证不了**(要改参数区; 参数区受 Cmp_CompFlag "
              "的 CRC 每秒复检 ⇒ 注入活不过一秒, 走 645 写参又会动表参数)",
        "⑥": "稳态不误记: 判据不成立时去抖计时被复位(:2004-2006), 记录一条都不新增",
        "⑦": "最近 %d 次过载记录封顶在容量上: 连造 %d 回(每回都停到调用点, 证明固件判定该记一笔)"
              "后记录区条数停在 %d, 且第 1 条(最早那条)被顶掉 —— 其序号不再是连造前那一个"
              "(规范 5-2 第 1 条)" % (_cap, _cap + 1, _cap),
        "⑧": {"text": "过载总次数按次累加且不封顶: 连造 %s 回后索引区首址那 3 字节的总次数加满 %s"
                      "(规范 5-2 第 1 条)"
                      % (_cap + 1, _cap + 1),
              "unprovable": "总次数只写在记录口的索引区(EEPROM), 而 698 记录读回只带条数"
                            "(`ReadRecordNum`)不带总次数 ⇒ 唯一的观测途径是 `Write_RecdData` 里"
                            "写回索引区那一停; 本固件过载口的记录尾段结构性写不进去(`:3653` 的 "
                            "off+len=172 越过 `TAB_Recd[ID_OverLoadA].len=95`, 撞上 "
                            "Platform/RecdData.c 的越界守卫), 于是那条写库路径一次都走不到 "
                            "⇒ 本台读不到。要证须先修那条长度(见判据 ① 的注)"},
    }


def revpower_criteria():
    """9-2『事件记录·功率反向』的**预设条目**(源 = ledger.md 9-2 的「观察与判据」I 列)。

    与 5-2 的条目**同形不同内容**: 两条事件共用 `Chk_OverLoad` 与 `Recd_OverLoad`, 故 ①②③⑤⑥
    说的其实是同一段代码; 真正属于 9-2 的只有 **④** —— 它独有的那半部门。
      · ④ 反向标志位 —— 反向支的判定是 `(g_PowP[3] > limit) && (g_PowQuad & 0x01<<3)`(:1927/:1937)。
        9-2 的 ④ 走的是"前半满足、后半不满足 ⇒ 一行都不该落", 与 ①(两半都满足 ⇒ 落一笔)合起来
        才钉得住那个 `&&` —— 单看 ① 的话, 一个把 `&&` 写成 `||` 的固件照样绿。
        ⚠ `g_PowQuad` 的 bit3 是"总有功功率反向"这一位(`:262` 那 8 位按 A/B/C/总 排, 总在 bit3)。
      · 索引是 **24(EV_RevPowerT, 总)** 而不是 25/26/27(A/B/C): `VER_20Edit` 在
        `Config/MengXi/UserCfg.h:15` **是打开的**(本工程唯一一份 `UserCfg.h`, 该目录在 .ewp 的
        包含路径上) ⇒ `DLT698App.c:2951+` `TAB_RecordObj[]` 里那两个 OI `0x30070B0A` 条目取
        **`#else` 那一支**(field5=4) ⇒ 落库的事件号是 `EV_RevPowerT=24`。读回也是这个 OI。
    """
    return {
        "①": "判据成立且去抖满时落『发生』一笔: 走 Recd_OverLoad 的「记录开始」支(:3634 写库位置被执行), "
              "且写的是 `EV_RevPowerT`(索引 24, 总); 串口侧该口序号推进 1, 新行结束时刻为空",
        "②": "判据翻假且去抖满时落『恢复』一笔: 走「记录结束」支(:3653 写库位置被执行), "
              "把结束时刻补进**上一行**(序号不推进 —— 恢复不新开一行)",
        "③": "两笔的时标 == 当时表钟(发生时刻在行首 buff[0..5], 结束时刻在 buff[86..91])",
        "④": "反向标志位是判定的一半: `g_PowP[3]` 过门槛**但** `g_PowQuad` 的 bit3 为 0 时, "
              "一行都不落(反向支的判定是 `&&`, 不是 `||`)—— 与『过门槛**且**反向位为 1 就落发生』"
              "那一条合起来才钉得住那个 `&&`",
        "⑤": "去抖秒数 = clamp(g_EventSet.RevPowerDelay − C_EveFlt(3), 不低于 C_EveDly(4)): "
              "在 :2021 读回的 `delay` 与按停点处现读的 RevPowerDelay 现算的值相同 —— "
              "**下钳位那一支(RevPowerDelay ≤ 7)在本台证不了**(要改参数区; 参数区受 Cmp_CompFlag "
              "的 CRC 每秒复检 ⇒ 注入活不过一秒, 走 645 写参又会动表参数)",
        "⑥": "稳态不误记: 判据不成立时去抖计时被复位(:2004-2006), 记录一条都不新增",
    }


def read_overload_rows(ser, positions=(1, 2, 3), code=OVL_EV_CODE, name="过载",
                       chip=None, wait=3.0, quiet=False):
    """读**测量类事件记录**第 1/2/3 条(1=最新) → `{pos: row|None}`; 每行自打印摘要(quiet 关掉)。

    列选 = 序号 + 发生时刻 + 结束时刻(`EVT_RCSD3`)。过载/功率反向的记录布局相同(发生时刻
    @buff[0..5], 结束时刻 @buff[86..91]), 且**三个列 OAD 与 5-3 完全一样**(同一张事件记录表项)——
    两条事件共用同一个写库函数 `Recd_OverLoad`, 差别只在 `code`(⇒ 记录 OI 0x3008 / 0x3007)。
    所以 `read_event_ud`/`event_row3`/`rcsd` 直接复用, 只是编码与列选组合换了。
    ⚠ 函数名留着 `overload`(5-2 先建) —— 改名的收益抵不上改调用点的风险。
    """
    out = {}
    for p in positions:
        ud = read_event_ud(ser, code, pos=p, rcsd=rcsd(*EVT_RCSD3), chip=chip, wait=wait)
        row = event_row3(ud) if ud else None
        out[p] = row
        if not quiet:
            # ⚠ 四种"读不出"必须分清(5-3 实踩的同一处): DAR 打回 / **记录区为空** / 解不出行 / 静默。
            #   混成一句会把**固件状态错**说成"表里没记录", 也会把"基线本来就没有记录"说成"读不回来"。
            if row is None:
                print("   [读回] %s pos%d: %s" % (name, p, _rec_none_reason(ud, name)))
            else:
                print("   [读回] %s pos%d: 序号=%s 发生=%s 结束=%s"
                      % (name, p, row["seq"], row["t_start"] or "-", row["t_end"] or "(未结束)"))
    return out


def event_area_count(ser, code, chip=None, wait=3.0):
    """该事件**记录区有几条** → int; 读不出(静默 / DAR 打回 / 不是成功应答) → None。

    与 `read_overload_rows`(→ `{pos: row|None}`)合用, 才能把两种"最新一条 是 None"分开:
      · **记录区为空**(0 条)—— 台面本来一条都没有, 是**干净的基线**, 不是故障;
      · **有记录却解不出行** —— 协议侧真问题, 那时中止才是对的。
    只问"最新一条 是不是 None"时两者长得一模一样, 于是一块干净台面会被判成"基线无对照, 中止"
    —— 而"中止"读起来像表有问题, 实际上一次动作都还没发。
    """
    return ud_record_count(read_event_ud(ser, code, pos=1, rcsd=rcsd(*EVT_RCSD3),
                                        chip=chip, wait=wait))


def resolve_event_baseline(ser, rows, code, name, chip=None, wait=3.0):
    """基线行 → `(seq0, why)`:`seq0` 有值 = 有得对照(空记录区给 **0**); `why` 有值 = 该中止。

    `rows` = 已读过的 `read_overload_rows(...)` 返回; 只在"最新一条 读不出"时**再发一帧只读**
    问记录区条数, 据此把"空区"与"解码失败"分开(见 `event_area_count`)。
    **空区给 seq0=0**: 固件应答里的序号是 `rcd.idx - s_Sch + 1`(`DLT698App.c:5686`),
    真实记录**从 1 起**, 0 不会与任何真记录撞号 ⇒ 可当"无基线行"的哨兵,
    下游那句 `序号不推进 = (_seq_n == seq0)` 与 `新序号 = {s > seq0}` 因此照旧成立。
    """
    top = rows.get(1)
    if top is not None:
        return top["seq"], None
    n = event_area_count(ser, code, chip=chip, wait=wait)
    if n == 0:
        return 0, None
    return None, ("%s记录读不出(第1条解不出, 而记录区%s)⇒ 基线无对照, 中止"
                  % (name, "有 %d 条" % n if n is not None else "条数也读不出"))


def rows_by_seq(rows):
    """`{pos: row}` → `{seq: row}`(丢掉没读到的), 供"按序号找那一行"用(位置会随新记录漂移)。"""
    return dict((r["seq"], r) for r in (rows or {}).values() if r)


def _ovl_dly_expect(text):
    """参数区读回来的**该事件**的去抖参数文本(`OverLoadDelay` / `RevPowerDelay` 都行) → 固件**应当**
    算出的 `delay`; 读不到 → None。

    就是 `TaskMetering.c:2011-2018` 那两句: 先减 `C_EveFlt`, 再钳到不低于 `C_EveDly`。
    那两行在**一个 9 索引共用的循环**里, 取哪个参数由 `TAB_RecdId[i].dly` 定 ⇒ 算式只有这一份,
    两条事件(过载/反向)共用本函数。
    ⚠ 纯函数, 离线可自检(见 `_overload_checks`)。判据⑤ 的期望值由它算, **不许在调用处内联** ——
      否则"算式写错了"与"固件不按算式走"在账本里长得一样。
    """
    v = gdb_ints(text)
    if not v:
        return None
    d = v[0] - EVT_C_EVEFLT
    return d if d >= EVT_C_EVDLY else EVT_C_EVDLY


def _meas_event_roundtrip(ser, spec, wb=None, wb_waived=False, wait=3.0, sample_gap=40.0,
                          nat_timeout=150.0, jump_timeout=60.0,
                          bp_judge=None, bp_call=None, bp_wrs=None, bp_wre=None, bp_guard=None,
                          judge_vars=(), call_vars=(), wrs_vars=(), wre_vars=(), guard_vars=()):
    """**测量类事件**(过载 / 功率反向)全流程 + 判据(库内单点, 脚本不留)。

    两个子项只有这一份实现 —— 它们**同一个断点**: `Chk_OverLoad`(:1869-2027) 一趟算完 9 个索引,
    落到同一个 `Recd_OverLoad`(:3610-3656) 写库; 差别全在 `spec` 里(见 `OVL_SPEC` / `RVP_SPEC`)。
    抄第二份的下场在 5-11 已写下过: 一处改了另一处没改, 而两个脚本**单看都绿**。

      `spec` 的键(全部必给):
        `code`/`name`      事件编码与中文名(`read_overload_rows`)
        `idx`/`idx_txt`    事件索引(`EV_OverLoadA+i` 里的 `i`)与它的说法
        `dly_field`        去抖参数在 `g_EventSet` 里的字段名(判据⑤ 现算用)
        `inj_*`            七张注入表(ON / NOQUAD / OFF / 各自的 FAST / HEAL)
        `on_txt`/`off_txt` 注入表的一句话说法(进 label)
        `neg_legs`         否定期望段表(每条是 dict: `crit`/`txt`/`inj`/`win`/`falsify`)
        `snapshot`         True ⇒ 发生·跳时那一段兼取判据④(电量快照); False ⇒ 不取

    流程(顺序承重 —— 有头无尾守卫 :3611-3627 要求 发生 → 恢复 → 发生 → 恢复):
      基线读 → 基线体检(有头无尾则补完) → **pre-flight**(读参数区门槛, 定该支启不启用)
      → 各否定期望段(⑥ / ④这类) → ①⑤ 发生·自然去抖 → ② 恢复·跳时 → ①④ 发生·跳时
      → 末段 恢复 ⇒ 跑完记录完整、`g_EventSta[idx] = FALSE`。

    **四个断点与四组变量由脚本递进来**(库里**不留第二份**)—— 这是承重约定, 不是风格:
    `scripts/_check_anchors.py` 只扫**脚本**里的 `BP_*`/`VARS_*`, 断点写进库里那条路它看不见
    (5-3 的 `LP_BP_*` 就是这么留下的盲区: 判定全绿, 而"停在哪一行"没人核过)。

      `bp_judge`  **注入停靠点**(每个子项不同):
                  过载 `:1882` `limit = g_EventSet.OverLoadPlower;` @0x24822;
                  反向 `:1898` `limit = g_EventSet.RevPowerPlower;` @0x2486c ——
                  取 `:1898` 的理由: 那时**过载支(:1882-1894)已经算完** `state[0..2]`,
                  于是注入造出的状态只会落进反向支, 9-2 与 5-2 互不串味。
                  该停 `limit`/`delay`/`sFlag` **无位置区间**、`i` 是空洞 ⇒ `judge_vars` 只收全局量。
      `bp_call`   `:2021` Recd_OverLoad 调用点 @0x24a9e(`i`/`delay` 在位置区间内)✓
                  ⚠ 这一处在 `for (i=0;i<9;i++)` 里, **9 个事件共用** ⇒ 命中时 **必须核对 `i`**
                    (`call_vars` 里带上 `i`), 否则会把隔壁索引的一次执行当成自己的证据。
      `bp_wrs`    `:3634` 「记录开始」写库位置 @0x25f4a(`idx`/`id`/`buff`/`g_EngyData` 可读)✓
      `bp_wre`    `:3653` 「记录结束」写库位置 @0x25fea(`idx`/`id`/`buff` 可读)✓
      `bp_guard`  `:3611` **守卫行** @0x25edc(`Read_RecdData` 已返回、守卫还没算)——
                  唯一能读到 `sta` 与 `buff[3]/[4]/[89]/[90]` 的地方 ⇒ `guard_vars` 收这六个。
                  ⚠ **`sta` 只有在这里活着**: 它在 `0x25f3a` 就死了 ⇒ **不许**放进
                    `wrs_vars`/`wre_vars`(读回来是"读不到", 而那看起来像"固件没给值")。
                  ⚠ 这里是**补完段的判据所在地**: 守卫走哪一支、落哪个写库口, 完全由这六个量决定
                    ⇒ 读到它们就不必再赌 `:3634`/`:3653` 谁命中。
                  ⚠ 补完段还会**自己往 `guard_vars` 后面接一个** `TAB_Recd[TAB_RecdId[idx].id].len`
                    (`_rlex`): 守卫放行 `sta=TRUE` 后那一支要写 `off=86,len=86`, 而 `Write_RecdData`
                    开头(`Platform/RecdData.c:566`)就是 `off + len > TAB_Recd[id].len ⇒ return FALSE`
                    —— 两个操作数在手, 那一笔**进不进得去记录区是算得出来的**, 于是"守卫放行了、
                    串口却说没写"能落到确切的因上, 而不是一句"串口读回没变"。过载块实测 95
                    ⇒ `172 > 95` ⇒ 拒。**这段的 `None` 与 `False` 是两回事**: 长度读不到 ⇒ `None`。
                  ⚠ 但**过载这一支写不进去是两处拦着**, 报的时候两处都要报: 除了上面那道越界守卫,
                    还有走在前头的 `:3652 Get_RecdData(&buff[92], 0)` —— 里头
                    `Copy_Data(pBuff, g_EngyData, 80)` 要写 `buff[92..171]`, 而记录块只有 95 字节
                    ⇒ 越界 77 字节, 正压在 `push` 存下的 `{r4,r5,r6,lr}` 上(`_probe_wre_gap.py` 记了
                    这段离线核对)。**实测 `:3653` 这个写库口一次都没被执行过**, 对得上的正是这一处;
                    只报越界守卫会让人以为"把守卫那道放宽就好", 而放宽了照样到不了那一步。
      ⚠ 规格 F 列的 `:3602`/`:3604`/`:3649`/`:1869` 四行 `info line` 说 **contains no code**,
        **不能作断点** —— 抄下来 gdb 照样停得住、变量照样"读得出", 读回来却是别处的现场。

    **触发通道**: 本类子项**只有注入**(段头: `g_PowP` 恒 0、相启动判定常闭, 帧通道造不出触发条件)。
      ⚠ 且这**一次注入不够**: 被注入的 `g_PowP[0]` **每周期被固件自己的数据通路刷回原值** ——
      停核现场抓到的是 `RevCopy_Data(pDest=g_PowP, pSour=g_SPIMBuff+77, 4)`(`Platform/Common.c:310`,
      走**指针形参** ⇒ 按变量名 grep 全固件只搜到"两处初始化写 0", 于是"注进去的值 2 秒内变 0"
      一度被读成"有人在清它")。真来路是计量芯经 SPI 推来的 698 帧
      (`SpiReadDMA → Save_variable_Data → Spread_StructArray(SET698) → RevCopy_Data`);本台面
      **无负载** ⇒ 计量芯恒推 0 ⇒ 单次注入只让判据条件在**那一拍**成立。而去抖要
      `C_EveFlt=3` 拍锁存 + `clamp(去抖参数−3, ≥4)` 拍计时(5-2 实测 57 拍, 探针逐拍重注 58 拍
      才把 `g_EventSta[21]` 由 FALSE 翻 TRUE)⇒ **单次注入永远攒不满**, 表象却是"没命中判据断点"。
      故 ①⑤ 走 `hold`(见 `breakpoint.inject_hold`): 在 `bp_judge` 上**逐拍重注**, 把那条件按住过整个窗口。

    **白盒动作**(库不 import swdbg, 会话由脚本持有, 这里只收回调):
      `wb = {"inj": partial(GD.inject_hit, g), "neq": partial(GD.expect_no_hit, g),
             "hit": partial(GD.wait_hit, g), "hold": partial(GD.inject_hold, g)}`
      · `hit` 只在 **pre-flight** 用(等一个自然到达的 `bp_judge` 读门槛)。给不出就别给 ——
        此时该支启不启用无从得知, "本台证不了"那一层会退化成不写(照实少写, 不猜)。
      · 各否定期望段: `inj`(带注入) 或 `neq`(不带注入), 断点在 `bp_call` —— 该指令路径**不该**被走到
      · 发生·自然: **逐拍重注**(`hold`)按住判据条件跨过整段去抖, 等 `:2021`(读 `i`/`delay`) —— ① ⑤
      · 补完·基线残行: **逐拍重注**后先停 `bp_guard`(守卫行)读它**真正的输入**
        (`buff[3]/[4]/[89]/[90]` + `sta` + 记录块长) ⇒ 按那四个字节算出守卫要走哪一支、要哪个 `sta`;
        注的 `sta` 若与它要的不符(**它当场 return, 本次不写库**), 换它要的那个再跑一趟,
        等对应的写库口; 最后由**串口**判落没落 —— 走「记录结束」支时还要拿 `off+len=86+86` 与
        那一刻读回的记录块长对一下(见上 `bp_guard` 那条): 进不去就是**固件缺陷**, 与"没补"是两回事
        (**两处拦着, 两处都报**: 越界守卫 + 前头那句 `Get_RecdData(&buff[92], 0)` 的越界写)  —— 基线
      · 恢复·跳时: **逐拍重注** OFF + `g_EventSta[idx]=TRUE` + Tmr, 等 `:3653`               —— ②
      · 发生·跳时: **逐拍重注** ON + `g_EventSta[idx]=FALSE` + Tmr, 等 `:3634`(读 buff)      —— ① (④)
      · 恢复·跳时: 同上再接一趟, 于是跑完记录完整、`g_EventSta[idx] = FALSE`                 —— ②
      ⚠ 上面三条**等写库口**的白盒段, 没命中一律记 `None`(本次没证成): 注入按不按得住、守卫放不放行
        都在固件之外 ⇒ 一次没命中说明不了固件动没动。它们只提供**加强**证据, 判据的账由串口那几条认领。
      ⚠ 这四段**都必须逐拍重注**(经 `land`)、且注入表里**必须带上 `g_EventSta[idx]`**:
        它们注的 `g_EventTmr[idx]=56` 死在 `:2004 if (g_EventSta[idx] == state[i]) g_EventTmr[idx] = 0;`
        上 —— 那一句在**同一趟里**紧跟注入点(`:1882` 在前), 先写、后清, **该等式成立就是稳态**:
        重注多少次都一样。修正只可能来自把 `g_EventSta[idx]` 钉到 `state[i]` 的反面(理由见
        `OVL_INJ_*_FAST` 的段头)。`hold` 给不出时回退到 `inj` 并当场说明它证不了什么(见 `land`)。

    **记录行怎么变**(串口侧的形状): 恢复**不新开一行**, 是把上一行的结束时刻补上(:3653 只写尾段,
    带累计秒 `g_EventSec[idx]`); 发生才新开一行(:3634 写全行, 结束时刻为空)。
    故"两笔" = **序号推进 1 + 旧行的结束时刻从 (未结束) 变成注入那一刻**。

    **厂内态是前置**(与 5-3 同一处): `CMD_GetRequestRecord` 照样过 `Chk_SafeMode`, 不在厂内态时
    事件记录 OI 全落 `DAR_MatchAuth` ⇒ **每条读回都被 20 打回**, 而现象是"帧完全合法、应答照回、
    负载里是 `00 14`", 与"表里没记录"长得一样。故本函数**自己进厂内**且**不退** —— 退厂内是
    `_restore_all.py` 第 [5a] 步的事, 必须排在拨钟/合闸/复核之后。

    **本函数不碰参数区、不发表钟帧、不改表钟**; 跑完表留下**两到四条**该事件记录(那是要证的产物),
    台面停在厂内态, 收尾由 `scripts/_restore_all.py` 收拾。**不碰** `clear_meter` / `clear_event`。

    返回 `(recs, details, scope)` —— 与 3-1/3-2/5-3/4-7 同形: **`scope is None` = 半途中止**
    (结构信号, 别让脚本去嗅文案)。**本函数不总结论** —— 判定只有 `common/judge.py` 一处。
    """
    _name, _idx = spec["name"], spec["idx"]
    _txt = spec.get("idx_txt") or ("索引 %d" % _idx)
    print("\n===== %s事件: 稳态对照 → 自然去抖发生 → 恢复 → 跳时发生/恢复 → 698 读回 =====" % _name)
    # ---- 记录区容量: 先摆出来。这一段是"落库那条路通不通"的**前提**, 排在所有段之前 ----
    # 容量 0 = 这个口被屏蔽: 存不进也读不出, 但帧侧照样报得出这个 OI(所以读回是"空区"不是报错)。
    # 不先说清的话, 后面那一串"没命中写库口 / 读回为空"会被读成固件不落库 —— 那是**归错人**。
    _rq = spec.get("rec_quota")
    if _rq is not None:
        _rq_txt = spec.get("rec_quota_txt") or "容量宏"
        if _rq == 0:
            print("   !! 本台『%s』的**记录区容量 = 0**(%s) ⇒ 这条事件在本固件上**一条都落不了库**:" % (_name, _rq_txt))
            print("      `Read_RecdData`(RecdData.c:448-455)第一段就 `return FALSE` **且一个字节都不写 pBuff**;")
            print("      `Write_RecdData` 同样出不去 ⇒ 后面『写库口没命中 / 698 读回为空』是**这个原因**, ")
            print("      不是固件把判据算错了。⚠ 连带一处在 :3610:`Recd_OverLoad` 不查 Read_RecdData 的返回值, ")
            print("      :3611 的守卫拿**未初始化的 buff**(95 字节局部数组)判『有头无尾』⇒ 何时早退看栈上残留。")
            print("      判据里**判据那一半**(功率过门槛/去抖/调用点)照样证得了; 『落一定落着库』那半边要先把")
            print("      %s 改成非 0 再编一版固件。" % _rq_txt)
        else:
            print("   记录区容量: %s = %d 条 ⇒ 落库那条路**有位置可落**(不等于能落对 —— 那是下面各段的事)"
                  % (_rq_txt, _rq))
    recs = []

    def reads(ser_, positions=(1, 2, 3), **kw):
        """本事件记录读回(`code`/`name` 由 spec 定, 调用处不再重复写一遍)。"""
        return read_overload_rows(ser_, positions, code=spec["code"], name=_name, **kw)

    # 进厂内: **读记录同样受 Chk_SafeMode 管**(见 docstring) —— 缺了这一步整种观测读空。
    enter_factory(ser)

    def add(label, ok, why, crit=None, falsify=None, obs=judge.SERIAL):
        """`ok` 三态: True 达成 / False 观察到不对 / **None 没做成**(没命中、没读到)。"""
        recs.append(rec(label, ok, why, crit=crit, obs=obs, falsify=falsify))
        print("   [%s] %s —— %s" % ({True: "PASS", False: "FAIL", None: "TBD"}[ok], label, why))

    # 断点观测接线: 没有会话时两次注入全不做(注入没有可降级的黑盒替身), 但**要说全**。
    wb = wb or {}
    inj, neq, hit = wb.get("inj"), wb.get("neq"), wb.get("hit")
    hold = wb.get("hold")                    # 逐拍重注(见段头「触发通道」); 给不出就回退到 `inj`
    have_wb = inj is not None and neq is not None

    def land(at, assigns, watch, watch_vars_, label):
        """**只需落地一拍**的那几段的注入: 逐拍重注 `EVT_LAND_TICKS` 拍, 等到 `watch` 就收工。

        重注的用处是把**每周期被数据通路刷回**的注入按住(见段头「触发通道」)。但**光靠重注不够**:
        这四段注的是 `g_EventTmr[idx]=56`, 而 `:2004 if (g_EventSta[idx] == state[i]) g_EventTmr[idx] = 0;`
        在同一趟里紧跟其后(注入点 `:1882` 在前, `:2004` 在后) —— 先写、后清。该等式若成立,
        **每一拍都成立**(那是稳态, 不是"恰好"), 重注多少次都留不下痕迹, 表象只剩"等写库口超时",
        读起来像固件不落库。故这四段的注入表里**必须把 `g_EventSta[idx]` 一并钉到 `state[i]` 的
        反面**(`OVL_INJ_*_FAST` / `OVL_INJ_HEAL` 的段头), 这里只负责"多按几拍"这一半。
        `hold` 给不出(用户没接会话而只给了 `inj`)时回退到单次注入并当场说明它证不了什么。
        """
        if hold is not None:
            return hold(at, assigns, EVT_LAND_TICKS, at_vars=judge_vars, watch=watch,
                        watch_vars=watch_vars_, watch_timeout=EVT_HOLD_WATCH,
                        budget=max(jump_timeout, EVT_HOLD_BUDGET), label=label)
        print("      !! 本次没给 `hold`(逐拍重注)⇒ 这四段回退到单次注入; 而单次注入的 "
              "`g_EventTmr[idx]` 会被 `:2004` 在同一趟里原样清掉 ⇒ 到不了写库口时**分不清**"
              "是固件没走那条路, 还是这一次注入刚好被清掉 —— 那不是固件没落库的证据。")
        return inj(at, assigns, watch=watch, watch_vars=watch_vars_, at_vars=judge_vars,
                   timeout=jump_timeout, label=label)
    if not have_wb and not wb_waived:
        print("   !! 本次无调试会话 → 断点观测(白盒)整体**未做**; 本次范围 = 仅黑盒"
              "(对外行为可判, 内部指令路径 / 去抖秒数 / 电量快照未取证 —— 见 CLAUDE.md「凭什么算「真实测试」·两种观测」)")
    elif not have_wb:
        print("   -- 本次**用户指定**只做黑盒(串口观测) ⇒ 断点观测(白盒)按指定不做(J 列须记明本次范围)")

    # ---- 基线 ----
    # ⚠ **空记录区是合规的起点, 不是故障** —— 一块从没发生过过载的台面本来就一条都没有。
    #   旧写法把 `最新一条 is None` 一律当成"基线无对照, 中止", 于是这种台面**一帧都还没发就停**,
    #   而打印读起来像表有问题。现在按 `resolve_event_baseline` 三态分: 空区 ⇒ seq0=0 照跑。
    t0 = read_clock(ser, chip="管理芯", quiet=True)
    # ⚠ 走本函数自己的 `reads()` —— 它把 `code=spec["code"]` / `name=_name` 带齐。
    #   2026-09-18 实测栽过: 这一行原先写的是 `read_overload_rows(ser, (1, 2, 3), wait=wait)`,
    #   于是 `code` 取了默认值 **0x08(过载)** ⇒ **9-2 把过载的记录区当成了功率反向的基线**:
    #   日志里打出「功率反向 最新一条 序号=0 发生=2026-09-17 17:56:25 结束=(未结束)」,
    #   而那一条其实是**过载**的; 下一行的「有头无尾」体检据此发了一趟 heal, 注的是
    #   `g_EventSta[24]`(= 功率反向)去补一条 **9-2 自己区域里并不存在**的残行 ——
    #   守卫自然不放行, 那一趟白等 60s。总格那条路(`reads`)是对的, 只有基线这一处漏了。
    rows0 = reads(ser, wait=wait)
    seq0, _bwhy = resolve_event_baseline(ser, rows0, spec["code"], _name,
                                         chip=None, wait=wait) if t0 else (None, "表钟读不出")
    if not t0 or _bwhy is not None:
        why = "%s(表钟=%s)" % (_bwhy or "基线无对照", t0)
        print("   !! %s" % why)
        return [rec("基线读取 ⇒ 中止", None, why, obs=judge.SERIAL)], [why], None
    _b0 = rows0.get(1)
    if _b0 is None:
        print("   基线: 表钟=%s | %s记录区**为空**(0 条)⇒ 这是干净起点, 第 1 条将由本次「发生」写下"
              " (seq0=0, 真记录序号从 1 起)" % (t0, _name))
    else:
        print("   基线: 表钟=%s | %s 最新一条 序号=%s 发生=%s 结束=%s"
              % (t0, _name, seq0, _b0["t_start"] or "-", _b0["t_end"] or "(未结束)"))

    # ---- 基线体检: 「有头无尾」的遗留行(见段头) ----
    # ⚠ 只在"开始时刻有值而结束时刻为空"时才算 —— 空记录区两者都是 None(而且那一行本身就不存在),
    #   那是**正常**的空, 别把它也读成"有头无尾"(那会把一个干净的起点说成"上次跑中断了")。
    if _b0 is not None and _b0["t_start"] is not None and _b0["t_end"] is None:
        _healed, _hl, _h_ok = False, "无会话或用户指定只做黑盒, 本次没补", None
        _h_why = _hl
        if have_wb and bp_guard is None:
            # 没递守卫行锚点 ⇒ 这一段**无从判**: 上面那句黑盒的"有头无尾"不是守卫看见的那一行,
            # 拿它当断言就是把台面的账记到固件头上。如实记"没做成"并点名缺的是哪个锚点。
            _h_ok = None
            _h_why = ("脚本没递 `bp_guard`(守卫行 `TaskMetering.c:3611` 锚点)⇒ 读不到守卫真正的输入, "
                      "这一段判不了 —— 守卫走哪一支由它自己读到的那四个字节决定, 与黑盒读回的那一行"
                      "未必是同一行")
            print("   !! %s" % _h_why)
            _healed = False
        elif have_wb:
            # ⚠ 顺序承重: **先停在守卫行读到它真正的输入, 再判该注哪个 sta、该落哪个写库口**。
            #   为什么不能拿上面那句黑盒的"基线有头无尾"直接当守卫的输入: 守卫看见的是
            #   `Read_RecdData(id,&buff[0],1,0,92)` 交给它的那一行, 而 `RecdData.c:510`
            #   `if (lst > num) Set_Data(pBuff,0x00,len);` 在**该 id 还没产生过记录**时把 buff
            #   整个清成 0x00 —— 那时守卫看见的是"无头无尾", 只放行 `sta=FALSE` 的「记录开始」支;
            #   而黑盒读回"有头无尾"只说明**那个位置上**有一行, 两者未必是同一行。
            #   把两者当成一行 ⇒ 断言"守卫必须放行"⇒ 没放行就记 FAIL ⇒ 把台面的账记到固件头上
            #   (§24: 判固件之前先分清是固件还是脚本)。读到输入之后, 走哪一支、落哪个写库口
            #   就是**算得出来的**(`:3611-3627` 只看这四个字节与 `sta`), 不必再赌写库口断点。
            # 同一次停里把**这条记录块的长度**也读出来(`TAB_Recd[TAB_RecdId[idx].id].len`)——
            # 它和写库口那句的 `off+len` 一起决定那一笔**能不能写进去**(`Write_RecdData` 自己的
            # 越界守卫, `Platform/RecdData.c:566`)。不读它, "守卫放行了、串口却说没写"就只能记成
            # 未证 —— 而它其实是**算得出来的**。
            _rlex = "TAB_Recd[TAB_RecdId[%d].id].len" % _idx
            _rg = land(bp_judge, spec["inj_heal"], bp_guard, tuple(guard_vars) + (_rlex,),
                       "断[A] 补完读数: 停 %s 逐拍重注 g_EventSta[%d]=TRUE + Tmr, 停在**守卫行** "
                       "%s 读它真正的输入(buff[3]/[4]/[89]/[90] + sta/id + 记录块长)"
                       % (_bptxt(bp_judge), _idx, _bptxt(bp_guard)))
            _hl = "ok=%s | %s" % ((_rg or {}).get("ok"), (_rg or {}).get("detail") or "-")
            print("      断[A] 补完读数那一趟: %s" % _hl)
            _gv = (_rg or {}).get("vars") or {}
            print("      守卫行读到: %s" % _gv)
            _gd, _gm = gb1(_gv.get("buff[3]")), gb1(_gv.get("buff[4]"))
            _gd2, _gm2 = gb1(_gv.get("buff[89]")), gb1(_gv.get("buff[90]"))
            _gsta = gdb_sym(_gv.get("sta"))
            _rlen = gb1(_gv.get(_rlex))      # 记录块长(见 `_rlex` 那两行); 读不到是 None, 不当 0
            # 守卫那两个字节组的判法照抄原文(`:3611-3614`): 头有值(非 0x00/0xFF) 且 尾为空(0x00/0xFF)。
            # ⚠ 四个字节**读不到时不能当 0** —— 0x00 在原文里是**有意义的值**("空"), 当 0 会让
            #   "有头无尾"凭空成立。读不到 ⇒ `_known=None` ⇒ 这一段记"本次没做成"。
            _known = all(_x is not None for _x in (_gd, _gm, _gd2, _gm2))
            _gopen = bool(_known and ((_gd not in (0, 0xFF)) or (_gm not in (0, 0xFF)))
                          and (_gd2 in (0, 0xFF)) and (_gm2 in (0, 0xFF)))
            _gneed = "TRUE" if _gopen else "FALSE"
            _gsite, _gsv = (bp_wre, wre_vars) if _gopen else (bp_wrs, wrs_vars)
            # ⚠ 只有**读到了名字**才判"注错了 sta"; 读不到(被优化掉/这一停没有它的位置)不算 ——
            #   否则会白跑一趟补注, 而打印出来的理由是编的。
            if (_rg or {}).get("ok") is True and _known and _gsta in ("TRUE", "FALSE") \
                    and _gsta != _gneed:
                # 这一趟注的 `sta` 不是守卫要的那个 ⇒ 它在 `:3616`/`:3623` 当场 return ⇒ 本次不写库。
                # **这不是"固件不落库"**: 换守卫要的那个再跑一趟(所需的 `state[i]` 也已按下面对齐)。
                print("      守卫行: sta=%s, 而 buff 四字节(buff[3]=%s buff[4]=%s buff[89]=%s "
                      "buff[90]=%s)判出『有头无尾』=%s ⇒ 这一支要的是 sta=%s —— 上一趟被守卫当场"
                      "打回, 改注 %s 再跑一趟" % (_gsta, _gd, _gm, _gd2, _gm2, _gopen, _gneed, _gneed))
                # 注 sta=TRUE 走「记录结束」(:3653)时要 `state[i]=FALSE`(否则 `:2004` 每拍把计时清零);
                # 注 sta=FALSE 走「记录开始」(:3634)时要 `state[i]=TRUE` —— 两张表(`inj_heal` /
                # `inj_on_fast`)各自把这一半也钉好了, 这里只按守卫要的 sta 选表。
                _rg = land(bp_judge, spec["inj_heal"] if _gopen else spec["inj_on_fast"],
                           _gsite, _gsv,
                           "断[A] 补完落库: 注守卫要的 sta=%s ⇒ 等写库口 %s"
                           % (_gneed, _bptxt(_gsite)))
                print("      断[A] 补完落库那一趟: ok=%s | %s"
                      % ((_rg or {}).get("ok"), (_rg or {}).get("detail") or "-"))
            _r2 = reads(ser, wait=wait, quiet=True)
            if _r2.get(1) is not None:
                rows0 = _r2
                seq0 = _r2[1]["seq"]
            _closed = bool(rows0.get(1) and rows0[1].get("t_end"))
            if _gopen and not _closed:
                # 守卫说要写尾段、串口却说这一行还没结束 —— 两条通道打架时**先让写盘与读回各走完
                # 一轮再判**, 不拿"刚写完那一刻的读回"当终值。
                time.sleep(wait)
                _r3 = reads(ser, wait=wait, quiet=True)
                if _r3.get(1) is not None:
                    rows0 = _r3
                    seq0 = _r3[1]["seq"]
                _closed = bool(rows0.get(1) and rows0[1].get("t_end"))
            _gwhy = ("守卫行 %s 读到 buff[3]=%s buff[4]=%s buff[89]=%s buff[90]=%s ⇒ 『有头无尾』=%s, "
                     "该支要 sta=%s, 当时 `sta=%s`" % (_bptxt(bp_guard), _gd, _gm, _gd2, _gm2,
                                                       _gopen, _gneed, _gsta))
            # 三态归因: 按**守卫自己看见的输入**算它该不该写、该写哪一支, 再拿串口读回对。固件的账只
            # 在"守卫该放行、写库口也走到了, 而串口读回说没落"时才算 —— 且那时才是两条通道打架。
            if not _known or (_rg or {}).get("ok") is not True:
                _h_ok = None
                _h_why = "这一次没读到守卫的输入 —— %s" % ((_rg or {}).get("detail") or "没停到守卫行")
            elif _gopen:
                # 守卫放行了 `sta=TRUE` ⇒ 走「记录结束」支 ⇒ 紧接着那一句是
                # `:3653 Write_RecdData(id, &buff[86], off=86, len=86, g_EventSec[idx])`。
                # **这一笔落不落得下去, 是算得出来的**: `Write_RecdData` 开头第一道守卫
                # (`Platform/RecdData.c:566`) 就是 `off + len > TAB_Recd[id].len ⇒ return FALSE`,
                # 而它两个操作数这会儿都在手里(记录块长见 `_rlex`)。别再让这一段止步于
                # "串口读回没变" —— 那读起来像"固件不落库", 说不出是哪一步拦的。
                _woff, _wlen = 86, 86
                if _rlen is None:
                    _h_ok = None
                    _h_why = ("%s ⇒ 落「记录结束」支, 但记录块长(`%s`)这一次没读到 ⇒ 那一笔过不过得了 "
                              "`Write_RecdData` 的越界守卫算不出来 —— 本次不据此判固件; 串口读回结束=%s"
                              % (_gwhy, _rlex, rows0[1]["t_end"] or "(未结束)"))
                elif _woff + _wlen > _rlen:
                    _h_ok = False
                    # ⚠ 这一支写不进去**有两处拦着, 两处都已落实**(别只报一处: 只报①会让人以为把②修了
                    #   就好, 只报②会让人以为①没发生 —— 而实测是 `:3653` 这个写库口**一次都没被执行过**,
                    #   正是①该负责的那一段):
                    #   ① `:3652 Get_RecdData(&buff[92], 0)` 里头 `Copy_Data(pBuff, g_EngyData, 80)`
                    #      要写 `buff[92..171]`, 而这条记录块只有 `_rlen` 字节 ⇒ 越界, 正压到 `push`
                    #      存下的 `{r4,r5,r6,lr}` 上(`_probe_wre_gap.py` 记了这段离线核对)。
                    #   ② 就算走到了 `:3653`, `off+len=86+86=172 > _rlen` ⇒ `Write_RecdData` 拒绝。
                    _h_why = ("%s ⇒ 落「记录结束」支。这一支写不进去, 本固件上**两处**拦着: "
                              "① 它先做 `:3652 Get_RecdData(&buff[92], 0)` —— 里头是 "
                              "`Copy_Data(pBuff, g_EngyData, 80)`, 要写 `buff[92..171]`, 而 `buff` 连同"
                              "这条记录块只有 %d 字节 ⇒ 越界 %d 字节, 正压在 `push` 存下的 "
                              "`{r4,r5,r6,lr}` 上; ② 就算走到 `:3653 Write_RecdData(id, &buff[%d], "
                              "off=%d, len=%d, g_EventSec[idx])`, 也是 `off+len=%d > %d` ⇒ 在 "
                              "`Platform/RecdData.c:566` 的越界守卫上 `return FALSE`。两处都在源码与镜像上"
                              "落实(见本函数 docstring 的 `bp_guard` 那条) ⇒ 这一笔**写不进记录区**是"
                              "**固件缺陷**, 不是本次没补; 串口读回结束=%s"
                              % (_gwhy, _rlen, 171 - (_rlen - 1), _woff, _woff, _wlen,
                                 _woff + _wlen, _rlen, rows0[1]["t_end"] or "(未结束)"))
                else:
                    _h_ok = bool(_closed)
                    _h_why = ("%s ⇒ 落「记录结束」支, 期望旧行的结束时刻由空变有值; 记录块长=%d, "
                              "`off+len=%d` 过得了越界守卫 ⇒ 这一笔该写得进去; 串口读回结束=%s"
                              % (_gwhy, _rlen, _woff + _wlen, rows0[1]["t_end"] or "(未结束)"))
            else:
                # 守卫看见的不是"有头无尾" ⇒ 这一段的**前提**(黑盒基线说有头无尾、故该补完)在
                # 守卫那一侧不成立。要么两者不是同一行(`Read_RecdData` 的 `lst` 与黑盒读的不是一条),
                # 要么 `RecdData.c:510` 把 buff 清成了 0x00。**都不许据此判固件** —— 记本次没做成,
                # 并把守卫的四个字节原样留下, 让下一趟拿它对齐"守卫读的是哪一行"。
                _h_ok = None
                _h_why = ("%s ⇒ 守卫**不认为**上一行有头无尾, 于是它只放行「记录开始」支; 而黑盒读回的"
                          "是『有头无尾』⇒ **两条通道看见的不是同一行**(或 `RecdData.c:510` 的 "
                          "`lst > num` 把 buff 清成了 0x00)。本次不据此判固件。" % _gwhy)
            _healed = bool(_h_ok)
        add("参考: 基线最新一条%s记录**有头无尾**(上一次跑中断留下) —— %s"
            % (_name, "已补完" if _healed else "本次没补"), _h_ok,
            "%s; 序号=%s 发生=%s 结束=%s" % (_h_why, seq0, rows0[1]["t_start"] or "-",
                                            rows0[1]["t_end"] or "(未结束)"))

    # ---- pre-flight: 先弄清"该支在本台启不启用", 再让否定期望段跑 ----
    # 为什么必须排在否定期望段**之前**: 依靠注入造出触发条件的那几段(9-2 的 ④),
    # 在该支被 `:1923 if (limit != 0)` 关掉时会"注入照写、调用点照旧走不到" ⇒ `inject_miss`
    # 报 `ok=True` —— 可它证的是"没触发就不落库"(任何能跑的固件都成立), **不是固件对**。
    # 先把门槛读出来, 那几段才能据实记 `None`(本台证不了) 而不是假通过。
    # 读法用 `wait_hit`: 停 `bp_judge` 时 `limit` 已被 :1882/:1898 填过, `:1882` 那句在
    # ~1 Hz 的秒心跳里执行一次, 等几秒必到; 读不到就老实返回 None(下面据 None 判 `_branch_off`)。
    pre = None
    if have_wb and hit is not None:
        pre = hit(bp_judge, 6.0, vars=judge_vars,
                  label="断[A] pre-flight: 停 %s 读 %s(判该支在本台启不启用)"
                        % (_bptxt(bp_judge), spec.get("limit_field") or "参数区门槛"),
                  crit=None)
        if pre is not None:
            print("      pre-flight 停 %s: ok=%s | %s"
                  % (_bptxt(bp_judge), pre.get("ok"), pre.get("detail")))
            print("      停时读到: %s" % (pre.get("vars") or {}))

    # 门槛读回 0 ⇒ `:1923 if (limit != 0)` 常假, 该支整段被跳过。**不是固件错, 是本台证不了**
    # (要证须先写参数区, 而参数区受 Cmp_CompFlag 的 CRC 每秒复检; 走 645 写参又会动表参数)。
    _lv = gdb_ints(((pre or {}).get("vars") or {}).get(spec.get("limit_field")) or "") \
        if spec.get("limit_field") else []
    _branch_off = bool(_lv) and _lv[0] == 0
    if _lv:
        print("      参数区 %s 读回 %s ⇒ 该支%s"
              % (spec["limit_field"], _lv[0], "**在本台不启用**" if _branch_off else "在本台启用"))

    # 各否定期望段的窗口秒数: 现查**驱动入参**, 不写第二份。
    # ⚠ `sample_gap`(稳态那一条)**必须 ≥ 该事件的去抖秒数** —— 由 pre-flight 现读的参数现算。
    #   理由: 稳态下去抖计时照样在累加(`:2019`), `g_EventSta[idx]` 与 `state[i]` 一旦不相等,
    #   累到 `delay` 就会走 `Recd_OverLoad` 调用点; 故"调用点不该被走到"只在**窗口跨过一整个
    #   `delay`** 时才证得了。窗口短于 `delay` 时它必然"没命中", 而那**不是**固件对的证据 ——
    #   2026-09-20 实测: 40s 窗口对着 57s 的去抖, 记了一条 `:2004` 明明没在复位的 PASS。
    _dly_gap = _ovl_dly_expect(((pre or {}).get("vars") or {}).get(spec.get("dly_field"))) \
        if spec.get("dly_field") else None
    _WIN = {"sample_gap": max(sample_gap, _dly_gap + 15.0) if _dly_gap else sample_gap,
            "jump_timeout": jump_timeout, "nat_timeout": nat_timeout}

    # ---- 各否定期望段: 判据不成立的这段时间里, bp_call 一次都不该被走到 ----
    # 单张段表驱动(spec["neg_legs"]): 5-2 只有"稳态"一条; 9-2 另有"反向标志位为 0 时过门槛"一条。
    # ⚠ 每段都配一次串口复核(序号不推进) —— 两条通道都要看, 别只留白盒那半。
    for _nl in spec["neg_legs"]:
        _n_crit, _n_txt, _n_inj = _nl["crit"], _nl["txt"], _nl["inj"]
        _n_fal = _nl["falsify"]
        _n_secs = _WIN[_nl["win"]]           # 未知窗口名 ⇒ KeyError, 当场炸(静默取默认值是不行的)
        # 该段靠注入造触发条件、而该支在本台不启用 ⇒ 这一趟证不了任何事(见 pre-flight 那段)。
        _n_skip = _n_inj is not None and _branch_off
        _n_rec = None
        if _n_skip:
            _n_why = ("**本台证不了**: %s 读回 0 ⇒ `:1923 if (limit != 0)` 常假、该支整段被跳过 "
                      "⇒ 注入造不出触发条件, 这一趟只会得到『没触发所以没落库』—— 那不是固件对的证据。"
                      "要证须先写参数区" % spec["limit_field"])
            print("      !! 否定期望段(%s) 本次不做: %s" % (_n_txt, _n_why))
        elif have_wb:
            if _n_inj is None:
                _n_rec = neq(bp_call, _n_secs, vars=("i", "delay"),
                             label="断[A] 否定期望: %s: %.0fs 内 %s(Recd_OverLoad 调用点)"
                                   "**不该**被走到" % (_n_txt, _n_secs, _bptxt(bp_call)),
                             crit=_n_crit, falsify=_n_fal)
            else:
                _n_rec = inj(bp_judge, _n_inj, watch=bp_call, watch_vars=("i", "delay"),
                             at_vars=judge_vars, timeout=_n_secs,
                             label="断[A] 否定期望: %s: 停 %s 注入后等 %.0fs, %s"
                                   "**不该**被走到"
                                   % (_n_txt, _bptxt(bp_judge), _n_secs,
                                      _bptxt(bp_call)),
                             crit=_n_crit, falsify=_n_fal)
        else:
            time.sleep(_n_secs)
        rows_n = reads(ser, wait=wait, quiet=True)
        _seq_n = (rows_n.get(1) or {}).get("seq")
        if _n_skip:
            add("断[A] 否定期望 —— %s ⇒ Recd_OverLoad 调用点未被走到" % _n_txt, None, _n_why,
                crit=_n_crit, obs=judge.DEBUG,
                falsify="不做这一次时无从判 —— 本记录不作为固件证据")
        elif _n_rec is not None:
            print("      断[A] 否定那一趟(%s): ok=%s | %s"
                  % (_n_txt, _n_rec.get("ok"), _n_rec.get("detail")))
            for _ln in (_n_rec.get("injects") or []):
                print("      注入账: %s" % _ln)
            add("断[A] 否定期望 —— %s ⇒ Recd_OverLoad 调用点未被走到" % _n_txt,
                _n_rec.get("ok"), _n_rec.get("detail") or "没做成 —— 未证",
                crit=_n_crit, obs=judge.DEBUG, falsify=_n_fal)
        else:
            add("断[A] 否定期望 —— %s" % _n_txt, None,
                "没有调试会话(或用户指定只做黑盒)⇒ 白盒这一次没做成",
                crit=_n_crit, obs=judge.DEBUG,
                falsify="不做这一次时无从判 —— 本记录不作为固件证据")
        add("串口 否定期望 —— %s ⇒ %s记录未新增" % (_n_txt, _name),
            (None if _n_skip else (_seq_n == seq0))
            if _seq_n is not None else None,
            "序号 %s → %s%s" % (seq0, _seq_n, "" if _seq_n is not None else "(本轮读不回 ⇒ 没做成)"),
            crit=_n_crit, falsify=_n_fal)

    # ---- ① ⑤ 发生·自然去抖: 停 bp_judge 只注判据那几样, 等 :2021 读 i/delay ----
    t_nat0 = read_clock(ser, chip="管理芯", quiet=True) or t0
    _t_inj = time.time()
    r_nat = None
    if have_wb and not _branch_off:
        _lab_nat = ("断[A] %s ⇒ 自然去抖满后等 %s(Recd_OverLoad 调用点, 读 i/delay)"
                    % (spec["on_txt"], _bptxt(bp_call)))
        _fal_nat = ("判据成立后不去抖落库(移位/计时那两段不通)⇒ 注入后走不到 %s"
                    % (_bptxt(bp_call)))
        if hold is not None:
            # ⚠ 这一支**必须逐拍重注**: 判据条件每周期被数据通路刷回(见段头「触发通道」),
            #   单次注入只成立一拍, 而去抖要几十拍 —— 回退到 `inj` 的那条路在本台面攒不满。
            r_nat = hold(bp_judge, spec["inj_on"], EVT_HOLD_TICKS,
                         at_vars=judge_vars, watch=bp_call, watch_vars=call_vars,
                         watch_timeout=EVT_HOLD_WATCH,
                         budget=max(nat_timeout, EVT_HOLD_BUDGET),
                         label=_lab_nat)
            if r_nat is not None:
                r_nat["crit"], r_nat["falsify"], r_nat["name"] = "①", _fal_nat, _lab_nat
        else:
            print("      !! 本次没给 `hold`(逐拍重注)⇒ 回退到单次注入 —— 而本支的判据条件每周期被"
                  "数据通路刷回原值, 单次注入攒不满去抖, 这一趟只会得到『没走到判据断点』。"
                  "那不是固件没落库的证据。")
            r_nat = inj(bp_judge, spec["inj_on"], watch=bp_call, watch_vars=call_vars,
                        at_vars=judge_vars, timeout=nat_timeout, label=_lab_nat,
                        crit="①", falsify=_fal_nat)
            # ⚠ **没走进判据断点这一半, 不许记成 FAIL** —— 2026-09-18 立的规矩, 起因是 9-2 / 9-3
            #   两个脚本漏给了 `wb["hold"]`, 那两趟正是走这条路: 上面那行警告已经写明"那不是固件
            #   没落库的证据", 可下面仍按 `crit="①"` 记了 FAIL, 于是账本里躺着一条**归错人**的结论
            #   (5-2 那一趟有 `hold`、① 同样是 FAIL, 两条看着一模一样, 没人分得出来)。
            #   口径与 judge 模块头一致: **证不了 ≠ 证不过** ⇒ 这里改成"没做成"(ok=None)。
            #   反向不对称是**有意**的: 单次注入**走到了**断点 —— 那是硬证(条件成立过、去抖满、降到了
            #   调用点), 照样算 ① 满足; 只有"没走到"才不可归因。
            if r_nat is not None and r_nat.get("ok") is False:
                r_nat["ok"] = None
                r_nat["detail"] = ("单次注入只成立一拍、攒不满去抖 ⇒ 没走到判据断点; 本趟没给 `hold`"
                                   " ⇒ 库按『没做成』记账(**不是固件没落库的证据**) · 原记录: %s"
                                   % (r_nat.get("detail") or ""))
    _t_trip = time.time()
    t_nat1 = read_clock(ser, chip="管理芯", quiet=True)
    # ⚠ `inject_hit` 交回来的是 **judge 记录**(`common/judge.rec`), 键只有
    #   `name/ok/detail/crit/obs/falsify/trig` —— **没有 `hit`**。判"命中没有"只认 `ok` 三态。
    _delay_txt = ((r_nat or {}).get("vars") or {}).get("delay")
    _i_txt = ((r_nat or {}).get("vars") or {}).get("i")
    _i_nums = gdb_ints(_i_txt)
    # ⚠ 停在**调用点**读到的是 `i` —— 那是**循环计数器**(0..8), **不是事件号**: `:2021` 调的是
    #   `Recd_OverLoad(EV_OverLoadA+i)`。事件的真相 = `EV_OverLoadA + i`。
    #   **2026-09-18 实测栽过**: 这一处早先写成 `gdb_ints(_i_txt) == [_idx]`(拿循环计数器直接比
    #   事件号), 于是 5-2 / 9-2 / 9-3 三个子项**各记一条归错人的 FAIL** —— 日志里读回的 `i=0`(过载)
    #   与 `i=3`(反向 24−21) 全是对的, 错的是判据。库内凡"在调用点读 i"的地方都得加这个基址。
    _i_at = [spec["loop_base"] + n for n in _i_nums] if _i_nums else []
    _dlyf_txt = ((r_nat or {}).get("at_vals") or {}).get(spec["dly_field"])
    _dly_hope = _ovl_dly_expect(_dlyf_txt)
    _dly_got = gdb_ints(_delay_txt)
    # 参数为 0 ⇒ `:1923 if (limit != 0)` 那一判定常闭, 该支整个不启用 —— 这不是"固件错", 是**本台证不了**
    # (要证须先写参数区, 而参数区受 Cmp_CompFlag 的 CRC 每秒复检、走 645 写参又会动表参数)。
    # `_branch_off` 已由 pre-flight 定过; 这里只兜底 —— pre-flight 没跑(调用方没给 `hit`,
    # 或本次没会话)时, 从"发生那一停"的对照值里补看一眼, 好让 `details` 里的"本台证不了"照实写。
    # ⚠ 兜底只补 `details`, **补不了否定期望段** —— 那几段在 pre-flight 之前就跑了。
    if not _lv and spec.get("limit_field"):
        _lv = gdb_ints(((r_nat or {}).get("at_vals") or {}).get(spec["limit_field"]) or "")
        _branch_off = bool(_lv) and _lv[0] == 0
    if r_nat is not None:
        print("      断[A] 发生那一趟: ok=%s | %s" % (r_nat.get("ok"), r_nat.get("detail")))
        print("      停时读到: %s" % (r_nat.get("vars") or {}))
        _ins = r_nat.get("injects") or []
        # 逐拍重注有**几十条**注入账 —— 全打会把这一趟的证据淹掉, 全吞又丢了证据。取头尾各几条
        # + 总数: 「条件被每一拍重按一遍」这件事由头尾 + 条数就能看清(条数本身就是"按了几拍")。
        _show = _ins if len(_ins) <= 12 else \
            _ins[:4] + ["…(逐拍重注: 中间 %d 条省略)" % (len(_ins) - 6)] + _ins[-2:]
        for _ln in _show:
            print("      注入账: %s" % _ln)
        _vl = r_nat.get("vals_log") or []
        if len(_vl) > 1:
            print("      逐拍读数(共 %d 拍): 第 1 拍 %s | 末拍 %s"
                  % (len(_vl), _vl[0], _vl[-1]))
        print("      注入前那一停读到: %s" % (r_nat.get("at_vals") or {}))
        add("断[A] 判据成立 + 去抖满 ⇒ 执行到 Recd_OverLoad 调用点(:2021), 且本次命中的正是 %s" % _txt,
            (r_nat.get("ok") if r_nat.get("ok") is not True
             else (_i_at == [_idx] if _i_at else None)),
            "%s; 循环计数器 i=%s ⇒ 事件号 EV_OverLoadA+i = %s(应为 %d —— 该调用点在 "
            "for(i=0;i<9;i++) 里, 9 个事件共用)"
            % (r_nat.get("detail") or "没命中 —— 未证", _i_txt,
               ("[%s]" % ", ".join(str(x) for x in _i_at)) if _i_at else "读不到", _idx),
            crit="①", obs=judge.DEBUG,
            falsify="判据成立也不落库(去抖不满 / 移位那一段不通)⇒ 不会停在 %s" % _bptxt(bp_call))
        # ⑤ 的期望值由 `_ovl_dly_expect` 按**停点处现读**的参数现算 —— 不是写死一个数。
        # ⚠ `delay` 是 `Chk_OverLoad` 的**局部量**, 编译器在 `:2021` 那一停上不保证留得下位置
        #   (2026-09-20 实跑读回来是空的) ⇒ 只认那一个变量的话, 这一条会一直挂在"没做成"。
        #   故补一条**同量的另一路测量**: 发生那一趟注的是判据条件、**没有**注 `g_EventTmr`,
        #   计时是固件自己从 0 累上去的(稳态下 `g_EventSta==state` ⇒ `:2004` 每拍清零, 故起点是 0);
        #   停到调用点的那一拍正是 `++g_EventTmr >= delay` 第一次成立的那一拍 ⇒ **命中拍数 = delay**。
        #   两条证据各自独立(一条读变量、一条数拍), 有一即可判, 两条都在就互相印证。
        _dly_ticks = (r_nat or {}).get("at_hit")
        if _dly_got and _dly_hope is not None:
            _dly_ok = _dly_got[0] == _dly_hope
        elif _dly_ticks and _dly_hope is not None and r_nat.get("ok") is True:
            _dly_ok = _dly_ticks == _dly_hope
        else:
            _dly_ok = None
        add("断[A] 去抖秒数 = clamp(%s − %d, ≥ %d): 读回 delay=%s / 停到调用点的拍数=%s, "
            "按停点处 %s=%s 现算应为 %s"
            % (spec["dly_field"], EVT_C_EVEFLT, EVT_C_EVDLY, _dly_got or "读不到",
               _dly_ticks if _dly_ticks else "读不到", spec["dly_field"], _dlyf_txt, _dly_hope),
            _dly_ok,
            "delay=%s; 本趟没注 Tmr ⇒ 拍数即去抖拍数(墙钟 注入→命中 = %.1fs)"
            % (_delay_txt, _t_trip - _t_inj),
            crit="⑤", obs=judge.DEBUG,
            falsify="固件不减 C_EveFlt / 不按 TAB_RecdId[idx].dly 取参 / 不钳 C_EveDly "
                    "⇒ 读回的 delay(或命中拍数)与现算值不同")
    else:
        _why_no = ("没有调试会话(或用户指定只做黑盒)⇒ 注入观测这一次没做成"
                   "(注入没有可降级的黑盒替身)")
        if _branch_off:
            _why_no = ("**该支在本台不启用**: %s 读回 0 ⇒ `:1923 if (limit != 0)` 常假, "
                       "该支整段被跳过 ⇒ 注入造不出 `state[%d]`。这不是固件错, 是**本台证不了** —— "
                       "要证须先写参数区(受 Cmp_CompFlag 的 CRC 每秒复检; 走 645 写参会动表参数)"
                       % (spec["limit_field"], _idx))
        add("断[A] 判据成立 ⇒ 执行到 Recd_OverLoad 调用点", None, _why_no,
            crit="①", obs=judge.DEBUG,
            falsify="不做这一次时无从判 —— 本记录不作为固件证据")
        add("断[A] 去抖秒数 = clamp(%s − %d, ≥ %d)" % (spec["dly_field"], EVT_C_EVEFLT, EVT_C_EVDLY),
            None, _why_no, crit="⑤", obs=judge.DEBUG,
            falsify="不做这一次时无从判 —— 本记录不作为固件证据")

    # ---- ① ③ 串口: 序号推进 + 新行未结束 + 时标落在窗口 ----
    t_after = read_clock(ser, chip="管理芯", quiet=True)
    rows2 = reads(ser, wait=wait)
    by2 = rows_by_seq(rows2)
    new_seqs = sorted(s for s in by2 if s > seq0)
    seq_new = new_seqs[-1] if new_seqs else None
    add("串口 %s序号推进(新落「发生」行)且新行未结束" % _name,
        None if not by2 else (bool(new_seqs) and by2[seq_new]["t_end"] is None),
        "序号 %s → %s(新行 %s; 新行结束时刻=%s)"
        % (seq0, (rows2.get(1) or {}).get("seq"), new_seqs or "无",
           (by2[seq_new]["t_end"] if seq_new else "-") or "(未结束)"),
        crit="①",
        falsify="固件不在去抖满后落『发生』⇒ 序号不推进 / 新行结束时刻不为空")
    add("串口 新「发生」行的发生时刻落在本次窗口(=当时表钟)",
        lp_in_window(by2[seq_new]["t_start"] if seq_new else None,
                      t_nat0, clock_add(t_after or t_nat0, 15)),
        "发生时刻=%s; 窗口=[%s, %s+15s]" % (by2[seq_new]["t_start"] if seq_new else None,
                                            t_nat0, t_after or t_nat0),
        crit="③",
        falsify="时标取的不是当时表钟(Get_MeterTime 写错偏移 / 用了旧时间)⇒ 落窗外")

    # ---- ② 恢复·跳时: 停 bp_judge 注 OFF + Tmr, 等 :3653(「记录结束」支写库位置) ----
    t_inj2 = read_clock(ser, chip="管理芯", quiet=True) or t_after or t0
    r_end = None
    if have_wb:
        r_end = land(bp_judge, spec["inj_off_fast"], bp_wre, wre_vars,
                     "断[A] 注入 %s(判据翻假)并把 g_EventSta[%d] 钉在 TRUE(否则 :2004 每拍把计时清零)"
                     "⇒ 等「记录结束」写库位置 %s"
                     % (spec["off_txt"], _idx, _bptxt(bp_wre)))
    t_inj2b = read_clock(ser, chip="管理芯", quiet=True)
    if r_end is not None:
        print("      断[A] 恢复那一趟: ok=%s | %s" % (r_end.get("ok"), r_end.get("detail")))
        print("      停时读到: %s" % (r_end.get("vars") or {}))
        for _ln in (r_end.get("injects") or []):
            print("      注入账: %s" % _ln)
        print("      注入前那一停读到: %s" % (r_end.get("at_vals") or {}))
        _eidx = (r_end.get("vars") or {}).get("idx")
        # ⚠ **没命中 = 这一次没证成, 不是固件没动**: 注入按不按得住、守卫放不放行, 都在固件之外。
        #   此前这里没命中记 FAIL 并写"固件仍未动" —— 那是拿台面的账记固件(§24)。写库口这一路
        #   **只有命中**时才谈得上"固件走到了哪一支"; 没命中记 `None`, 本判据的账由串口那两条认领。
        add("断[A] 判据翻假 ⇒ 落到「记录结束」写库位置(:3653), 且写的 idx 正是 %s" % _txt,
            (None if r_end.get("ok") is not True
             else (gdb_ints(_eidx) == [_idx] if gdb_ints(_eidx) else None)),
            "%s; %s; idx=%s id=%s(应为 %d)"
            % (r_end.get("detail") or "没命中 —— 未证",
               "写库口没命中 ⇒ 本次没证成(按不按得住、放不放行都在固件之外), 不据此判固件"
               if r_end.get("ok") is not True else "写库口命中",
               _eidx, (r_end.get("vars") or {}).get("id"), _idx),
            crit="②", obs=judge.DEBUG,
            falsify="固件不走『记录结束』支, 或写的是别的事件号(idx≠%d)⇒ 不会停到 %s / idx 不匹配"
                    % (_idx, _bptxt(bp_wre)))
    else:
        add("断[A] 判据翻假 ⇒ 恢复写库", None,
            "没有调试会话(或用户指定只做黑盒)⇒ 白盒这一次没做成", crit="②", obs=judge.DEBUG,
            falsify="不做这一次时无从判 —— 本记录不作为固件证据")

    # ---- ② ③ 串口: 上一行的结束时刻被补上, 序号不推进 ----
    rows3 = reads(ser, wait=wait)
    by3 = rows_by_seq(rows3)
    _row_new = by3.get(seq_new) if seq_new is not None else None
    _t_end_new = (_row_new or {}).get("t_end")
    _win3 = (t_inj2, clock_add(t_inj2b or t_inj2, 12))
    add("串口 『发生』行(序号=%s)的结束时刻被补上 = 落了「恢复」一笔(未新开行)" % seq_new,
        None if _row_new is None else (_t_end_new is not None and lp_in_window(_t_end_new, *_win3)),
        "结束时刻=%s; 注入窗口=[%s, %s+12s]" % (_t_end_new or "(未结束)", _win3[0], t_inj2b or t_inj2),
        crit="②",
        falsify="固件不把结束时刻补进上一行(或另开一行)⇒ 该行结束时刻仍为空 / 序号多推进一次")
    # ⚠ 这一条**只在『发生』那一行确实落了**的时候才成立:`seq_new is None` / 那一行读不回时,
    #   "最大序号没变"是**空判**(什么都没落当然没变)。2026-09-20 实跑里它就是这么记了一条 PASS ——
    #   而那一趟一个字节都没落库。空判的 PASS 比 FAIL 更难查, 故没有立足点时记 `None`。
    add("串口 序号在「恢复」后不推进(恢复不新开一行)",
        None if (_row_new is None or not by3) else (max(by3) == seq_new),
        "最大序号 %s(恢复后应与发生后同为 %s)%s"
        % (max(by3) if by3 else "-", seq_new,
           "" if _row_new is not None else " —— 『发生』那一行没落/读不回 ⇒ 本判据无立足点"),
        crit="②",
        falsify="恢复也新开一行 ⇒ 最大序号再推进一格")
    add("串口 新「发生」行的结束时刻落在本次窗口(=当时表钟)",
        lp_in_window(_t_end_new, *_win3),
        "结束时刻=%s; 窗口=[%s, %s+12s]" % (_t_end_new or "(未结束)", _win3[0], t_inj2b or t_inj2),
        crit="③",
        falsify="结束时刻取的不是当时表钟(Get_MeterTime 写错偏移)⇒ 落窗外")

    # ---- ① (④) 发生·跳时: 停 bp_judge 注 ON + Tmr, 等 :3634 ----
    r_start = None
    if have_wb:
        r_start = land(bp_judge, spec["inj_on_fast"], bp_wrs, wrs_vars,
                       "断[A] 再注入 %s(判据复真)并把 g_EventSta[%d] 钉在 FALSE(否则 :2004 每拍把计时清零)"
                       "⇒ 等「记录开始」写库位置 %s%s"
                       % (spec["on_txt"], _idx, _bptxt(bp_wrs),
                          "(读 buff/g_EngyData)" if spec.get("snapshot") else ""))
    if r_start is not None:
        print("      断[A] 二次发生那一趟: ok=%s | %s" % (r_start.get("ok"), r_start.get("detail")))
        _sv = r_start.get("vars") or {}
        print("      停时读到: idx=%s id=%s" % (_sv.get("idx"), _sv.get("id")))
        for _ln in (r_start.get("injects") or []):
            print("      注入账: %s" % _ln)
        print("      注入前那一停读到: %s" % (r_start.get("at_vals") or {}))
        # ⚠ 同 ② 那条: 没命中 ⇒ `None`(本次没证成), 不记 "固件仍未动"。
        add("断[A] 判据复真 ⇒ 落到「记录开始」写库位置(:3634), 且写的 idx 正是 %s" % _txt,
            (None if r_start.get("ok") is not True
             else (gdb_ints(_sv.get("idx")) == [_idx] if gdb_ints(_sv.get("idx")) else None)),
            "%s; %s; idx=%s id=%s(应为 %d)"
            % (r_start.get("detail") or "没命中 —— 未证",
               "写库口没命中 ⇒ 本次没证成(按不按得住、放不放行都在固件之外), 不据此判固件"
               if r_start.get("ok") is not True else "写库口命中",
               _sv.get("idx"), _sv.get("id"), _idx),
            crit="①", obs=judge.DEBUG,
            falsify="固件不走『记录开始』支, 或写的是别的事件号 ⇒ 不会停到 %s / idx 不匹配"
                    % _bptxt(bp_wrs))
        if spec.get("snapshot"):
            # ④ 电量快照: 写库那一刻 buff[6..85] 应当**逐字节等于**固件那份 g_EngyData。
            #   ⚠ 两个量都是 `INT8U[]`, gdb 按**字符串字面量**印(不是 `{1,2,3}`)⇒ 必须用 `gdb_bytes`,
            #     拿 `gdb_ints` 抠会静默读出差一截的序列(见 `gdb_bytes` 的说明)。
            _bb = gdb_bytes(_sv.get("buff"))
            _be = gdb_bytes(_sv.get("g_EngyData"))
            _snap = _bb[6:86] if len(_bb) >= 86 else []
            _allzero = bool(_be) and not any(_be)
            add("断[A] ④a 「发生」一笔的电量快照取自 g_EngyData(写库那一刻 buff[6..85] 与之逐字节相同)",
                (_snap == _be) if (_snap and _be) else None,
                "buff[6..85] 长 %d / g_EngyData 长 %d; 两者%s; g_EngyData %s"
                % (len(_snap), len(_be), "逐字节相同" if (_snap and _snap == _be) else "不同或读不出",
                   "**全 0**(⇒ 本台『快照非空壳』那一半证不了)" if _allzero else "非全 0"),
                crit="④a", obs=judge.DEBUG,
                falsify="固件不把 g_EngyData 拷进记录正文(或拷到别的偏移)⇒ 那一段与它不同")
    else:
        add("断[A] 判据复真 ⇒ 发生写库", None,
            "没有调试会话(或用户指定只做黑盒)⇒ 白盒这一次没做成", crit="①", obs=judge.DEBUG,
            falsify="不做这一次时无从判 —— 本记录不作为固件证据")
        if spec.get("snapshot"):
            add("断[A] ④a 电量快照取自 g_EngyData", None,
                "没有调试会话(或用户指定只做黑盒)⇒ 白盒这一次没做成", crit="④a", obs=judge.DEBUG,
                falsify="不做这一次时无从判 —— 本记录不作为固件证据")

    # ---- ② 末段: 再落一次「恢复」, 让跑完的记录是完整的、g_EventSta[idx] = FALSE ----
    r_end2 = None
    if have_wb:
        r_end2 = land(bp_judge, spec["inj_off_fast"], bp_wre, wre_vars,
                      "断[A] 末段: 注入 %s 并把 g_EventSta[%d] 钉在 TRUE ⇒ 等「记录结束」写库位置 %s"
                      "(跑完记录完整、状态复位)"
                      % (spec["off_txt"], _idx, _bptxt(bp_wre)))
    t_last = read_clock(ser, chip="管理芯", quiet=True)
    rows4 = reads(ser, wait=wait)
    by4 = rows_by_seq(rows4)
    if r_end2 is not None:
        print("      断[A] 末段恢复: ok=%s | %s" % (r_end2.get("ok"), r_end2.get("detail")))
        # ⚠ 同 ② 那条: 没命中 ⇒ `None`。
        add("断[A] 末段恢复落到「记录结束」写库位置(:3653)",
            r_end2.get("ok") if r_end2.get("ok") is True else None,
            "%s; %s" % (r_end2.get("detail") or "没命中 —— 未证",
                        "写库口没命中 ⇒ 本次没证成, 不据此判固件" if r_end2.get("ok") is not True
                        else "写库口命中, 且写的 idx 是 %s" % (r_end2.get("vars") or {}).get("idx")),
            crit="②", obs=judge.DEBUG,
            falsify="固件不走『记录结束』支 ⇒ 不会停在 %s" % _bptxt(bp_wre))
    else:
        add("断[A] 末段恢复写库", None,
            "没有调试会话(或用户指定只做黑盒)⇒ 白盒这一次没做成", crit="②", obs=judge.DEBUG,
            falsify="不做这一次时无从判 —— 本记录不作为固件证据")

    # ---- crit=None 的参考记录(答不出 falsify, 不进判据计数, 只进日志) ----
    _o2 = by4.get(max(by4)) if by4 else None
    add("参考: 跑完最新一条%s记录的形状(末段停在「恢复」⇒ 应当是有头有尾)" % _name,
        None, "序号=%s 发生=%s 结束=%s" % ((_o2 or {}).get("seq"), (_o2 or {}).get("t_start") or "-",
                                          (_o2 or {}).get("t_end") or "(未结束)"))
    add("参考: 表钟在本次窗口内正常走时(记录时标可与之对拍)",
        None, "起=%s 末=%s" % (t0, t_last))
    if spec.get("snapshot"):
        add("参考(源码级, 与判据无关): Recd_OverLoad 的「记录结束」支有越界写 —— "
            "`INT8U buff[95]`(:3604; RecdData.h:280 = 55+40) 而 :3652 `Get_RecdData(&buff[92], 0)` "
            "按 :4228 `Copy_Data(pBuff, g_EngyData, 80)` 写 buff[92..171](越界 77 字节); "
            "另 :4222-4225 用了从未赋值的局部量 `i`", None,
            "证据 = 本项白盒那一段停 :3653 时读到的 buff 长度与 :4228 那次 Copy_Data 的常量; "
            "本项不修改、不据此下任何结论")

    if not have_wb:
        scope = "仅黑盒(用户指定)" if wb_waived else "仅黑盒(无调试会话)"
    else:
        scope = "黑盒+白盒"
    n_fail = sum(1 for r in recs if r["ok"] is False)
    n_tbd = sum(1 for r in recs if r["ok"] is None)
    print("===== %s事件: %d 条证据(FAIL=%d 没做成=%d)  [本次范围: %s] ====="
          % (_name, len(recs), n_fail, n_tbd, scope))
    details = ["%s: %s" % (r["name"], r["detail"]) for r in recs]
    if not have_wb:
        details.append("未做: 断点观测(白盒) —— %s; 要证『发生/结束两条写库路径 / 去抖秒数%s』"
                       "需接 J-Link 重跑(断点见 ledger.md 该项的 F 列)"
                       % ("用户本次指定只做黑盒" if wb_waived else "本次无调试会话",
                          " / 电量快照取自 g_EngyData" if spec.get("snapshot") else ""))
    if spec.get("snapshot"):
        details.append("未证: 电量快照『值等于当时真实电能』那一半 —— 要记录列解码器"
                       "(把 80 字节电量正文按列 OAD 解回六个电能): 本项只证它取自 g_EngyData")
    details.append("未证: 去抖秒数的**下钳位**那一支(%s ≤ %d ⇒ delay 被抬到 %d) —— "
                   "要改参数区; 参数区受 Cmp_CompFlag 的 CRC 每秒复检, 注入活不过一秒, 走 645 写参"
                   "又会动表参数 ⇒ 本台不建这一支"
                   % (spec["dly_field"], EVT_C_EVEFLT + EVT_C_EVDLY, EVT_C_EVDLY))
    if _branch_off:
        details.append("未证: **%s 整支**在本台不启用(%s 读回 0 ⇒ `:1923 if (limit != 0)` 常假) —— "
                       "不是固件错, 是台面参数为默认值; 要证须先写参数区" % (_name, spec["limit_field"]))
    if _rq == 0:
        details.append("未证(且**在本台证不了**): 『%s 的记录一定落着库』那一半 —— 本固件这一口的"
                       "**记录区容量 = 0**(%s), `Read_RecdData`(RecdData.c:448-455)直接 FALSE 且不写 pBuff、"
                       "`Write_RecdData` 同样出不去 ⇒ 一条都存不下; 而帧侧照样登着这个 OI, 读回是一段"
                       "**空区应答**而不是报错(别把『空』读成『固件不落库』)。判据算得对不对那半边不受影响"
                       "(判据行、去抖、调用点都照证)。要证这一半须先把 %s 改成非 0 重编固件。"
                       % (_name, spec.get("rec_quota_txt") or _rq, spec.get("rec_quota_txt") or "该容量宏"))
    return recs, details, scope


class EvtRun:
    """测量类事件(过载 / 功率反向)的**分段驱动**: 一个实例跑一趟, 每段一个方法。

    与 `_meas_event_roundtrip` 是**同一趟流程的两份写法** —— 那份一次调用跑完, 这份让脚本把顺序
    逐行写出来。每段的代码与那份逐字相同; 流程、四个断点各是什么、为什么逐拍重注、
    守卫行那一段的坑, 全部写在 `_meas_event_roundtrip` 的 docstring 里, 这里不重复。
    ⚠ **改一处必须同时改另一处** —— 两条路各给一份结论, 分叉了不会有人报。

    ⚠ 断点与变量元组由**脚本**递进构造函数, 库里不留第二份 —— 这是承重约定, 理由见
      `_meas_event_roundtrip` docstring 的「四个断点与四组变量由脚本递进来」那一段。

    段顺序(脚本里逐行调用; 顺序承重 —— 有头无尾守卫 :3611-3627 要求 发生 → 恢复 → 发生 → 恢复):
      `baseline` → `heal` → `preflight` → `neg_legs` → `happen_natural` → `ser_after_happen`
      → `recover_jump` → `ser_after_recover` → `happen_jump` → `recover_last` → `finish`

    `baseline()` 返回 False = 半途中止(表钟读不出 / 基线无对照): 那一趟的结论已经进了 `recs`,
    脚本**不要再往下走**, 直接把自己那段交给 `ctx.hold(...)`。
    """

    def __init__(self, ser, spec, wb=None, wb_waived=False, wait=3.0, sample_gap=40.0,
                 nat_timeout=150.0, jump_timeout=60.0,
                 bp_judge=None, bp_call=None, bp_wrs=None, bp_wre=None, bp_guard=None,
                 judge_vars=(), call_vars=(), wrs_vars=(), wre_vars=(), guard_vars=()):
        self.ser, self.spec, self.wait = ser, spec, wait
        self.sample_gap, self.nat_timeout, self.jump_timeout = sample_gap, nat_timeout, jump_timeout
        self.wb_waived = wb_waived
        self.bp_judge, self.bp_call = bp_judge, bp_call
        self.bp_wrs, self.bp_wre, self.bp_guard = bp_wrs, bp_wre, bp_guard
        self.judge_vars, self.call_vars = judge_vars, call_vars
        self.wrs_vars, self.wre_vars, self.guard_vars = wrs_vars, wre_vars, guard_vars

        self._name, self._idx = spec["name"], spec["idx"]
        self._txt = spec.get("idx_txt") or ("索引 %d" % self._idx)
        print("\n===== %s事件: 稳态对照 → 自然去抖发生 → 恢复 → 跳时发生/恢复 → 698 读回 ====="
              % self._name)
        # ---- 记录区容量: 先摆出来。这一段是"落库那条路通不通"的**前提**, 排在所有段之前 ----
        # 容量 0 = 这个口被屏蔽: 存不进也读不出, 但帧侧照样报得出这个 OI(所以读回是"空区"不是报错)。
        # 不先说清的话, 后面那一串"没命中写库口 / 读回为空"会被读成固件不落库 —— 那是**归错人**。
        self._rq = spec.get("rec_quota")
        if self._rq is not None:
            _rq_txt = spec.get("rec_quota_txt") or "容量宏"
            if self._rq == 0:
                print("   !! 本台『%s』的**记录区容量 = 0**(%s) ⇒ 这条事件在本固件上**一条都落不了库**:" % (self._name, _rq_txt))
                print("      `Read_RecdData`(RecdData.c:448-455)第一段就 `return FALSE` **且一个字节都不写 pBuff**;")
                print("      `Write_RecdData` 同样出不去 ⇒ 后面『写库口没命中 / 698 读回为空』是**这个原因**, ")
                print("      不是固件把判据算错了。⚠ 连带一处在 :3610:`Recd_OverLoad` 不查 Read_RecdData 的返回值, ")
                print("      :3611 的守卫拿**未初始化的 buff**(95 字节局部数组)判『有头无尾』⇒ 何时早退看栈上残留。")
                print("      判据里**判据那一半**(功率过门槛/去抖/调用点)照样证得了; 『落一定落着库』那半边要先把")
                print("      %s 改成非 0 再编一版固件。" % _rq_txt)
            else:
                print("   记录区容量: %s = %d 条 ⇒ 落库那条路**有位置可落**(不等于能落对 —— 那是下面各段的事)"
                      % (_rq_txt, self._rq))
        self.recs = []

        # 进厂内: **读记录同样受 Chk_SafeMode 管**(见 `_meas_event_roundtrip` docstring) ——
        # 缺了这一步整种观测读空。
        enter_factory(ser)

        # 断点观测接线: 没有会话时两次注入全不做(注入没有可降级的黑盒替身), 但**要说全**。
        wb = wb or {}
        self.inj, self.neq, self.hit = wb.get("inj"), wb.get("neq"), wb.get("hit")
        self.hold = wb.get("hold")               # 逐拍重注(见段头「触发通道」); 给不出就回退到 `inj`
        self.have_wb = self.inj is not None and self.neq is not None
        if not self.have_wb and not wb_waived:
            print("   !! 本次无调试会话 → 断点观测(白盒)整体**未做**; 本次范围 = 仅黑盒"
                  "(对外行为可判, 内部指令路径 / 去抖秒数 / 电量快照未取证 —— 见 CLAUDE.md「凭什么算「真实测试」·两种观测」)")
        elif not self.have_wb:
            print("   -- 本次**用户指定**只做黑盒(串口观测) ⇒ 断点观测(白盒)按指定不做(J 列须记明本次范围)")

        # 逐段留在实例上的中间量(段与段之间靠它们接续; 都是"这一段测到的事实", 不是配置)
        self.t0 = self.seq0 = self.rows0 = self._b0 = None
        self._lv, self._branch_off, self._WIN = None, False, None
        self.seq_new, self.by2 = None, {}
        self.t_nat0 = self._t_inj = self.r_nat = self._t_trip = self.t_nat1 = None
        self.t_inj2 = self.r_end = self.t_inj2b = None
        self._row_new = self._t_end_new = self._win3 = None
        self.r_start = self.r_end2 = self.t_last = None
        self.by4 = {}

    # ---- 两个小口子: 读回与记账(原 `_meas_event_roundtrip` 里的 `reads` / `add`) ----
    def _reads(self, positions=(1, 2, 3), **kw):
        """本事件记录读回(`code`/`name` 由 spec 定, 调用处不再重复写一遍)。"""
        return read_overload_rows(self.ser, positions, code=self.spec["code"],
                                  name=self._name, **kw)

    def _add(self, label, ok, why, crit=None, falsify=None, obs=judge.SERIAL):
        """`ok` 三态: True 达成 / False 观察到不对 / **None 没做成**(没命中、没读到)。"""
        self.recs.append(rec(label, ok, why, crit=crit, obs=obs, falsify=falsify))
        print("   [%s] %s —— %s" % ({True: "PASS", False: "FAIL", None: "TBD"}[ok], label, why))

    def _land(self, at, assigns, watch, watch_vars_, label):
        """**只需落地一拍**的那几段的注入: 逐拍重注 `EVT_LAND_TICKS` 拍, 等到 `watch` 就收工。

        逐拍重注的用处、以及"这四段注的 `g_EventTmr[idx]=56` 会被 `:2004` 在同一趟里清掉"这条坑,
        见 `_meas_event_roundtrip` docstring 的「白盒动作」那一段(段里也标了 ⚠)。
        """
        if self.hold is not None:
            return self.hold(at, assigns, EVT_LAND_TICKS, at_vars=self.judge_vars, watch=watch,
                             watch_vars=watch_vars_, watch_timeout=EVT_HOLD_WATCH,
                             budget=max(self.jump_timeout, EVT_HOLD_BUDGET), label=label)
        print("      !! 本次没给 `hold`(逐拍重注)⇒ 这四段回退到单次注入; 而单次注入的 "
              "`g_EventTmr[idx]` 会被 `:2004` 在同一趟里原样清掉 ⇒ 到不了写库口时**分不清**"
              "是固件没走那条路, 还是这一次注入刚好被清掉 —— 那不是固件没落库的证据。")
        return self.inj(at, assigns, watch=watch, watch_vars=watch_vars_, at_vars=self.judge_vars,
                        timeout=self.jump_timeout, label=label)

    # ------------------------------------------------------------------
    # 段 1: 基线读(空记录区是合规起点, 不是故障)
    # ------------------------------------------------------------------
    def baseline(self):
        """读表钟 + 本事件最新一条 ⇒ `False` = 表钟读不出 / 基线无对照, 半途中止。"""
        # ⚠ **空记录区是合规的起点, 不是故障** —— 一块从没发生过过载的台面本来就一条都没有。
        #   旧写法把 `最新一条 is None` 一律当成"基线无对照, 中止", 于是这种台面**一帧都还没发就停**,
        #   而打印读起来像表有问题。现在按 `resolve_event_baseline` 三态分: 空区 ⇒ seq0=0 照跑。
        ser, wait = self.ser, self.wait
        self.t0 = read_clock(ser, chip="管理芯", quiet=True)
        # ⚠ 走本实例的 `_reads()` —— 它把 `code=spec["code"]` / `name=_name` 带齐。
        #   2026-09-18 实测栽过: 这一行原先写的是 `read_overload_rows(ser, (1, 2, 3), wait=wait)`,
        #   于是 `code` 取了默认值 **0x08(过载)** ⇒ **9-2 把过载的记录区当成了功率反向的基线**:
        #   日志里打出「功率反向 最新一条 序号=0 发生=2026-09-17 17:56:25 结束=(未结束)」,
        #   而那一条其实是**过载**的; 下一行的「有头无尾」体检据此发了一趟 heal, 注的是
        #   `g_EventSta[24]`(= 功率反向)去补一条 **9-2 自己区域里并不存在**的残行 ——
        #   守卫自然不放行, 那一趟白等 60s。总格那条路(`_reads`)是对的, 只有基线这一处漏了。
        self.rows0 = self._reads(wait=wait)
        self.seq0, _bwhy = resolve_event_baseline(ser, self.rows0, self.spec["code"], self._name,
                                                  chip=None, wait=wait) \
            if self.t0 else (None, "表钟读不出")
        if not self.t0 or _bwhy is not None:
            why = "%s(表钟=%s)" % (_bwhy or "基线无对照", self.t0)
            print("   !! %s" % why)
            self.recs.append(rec("基线读取 ⇒ 中止", None, why, obs=judge.SERIAL))
            self.details = [why]
            self.scope = None
            return False
        self._b0 = self.rows0.get(1)
        if self._b0 is None:
            print("   基线: 表钟=%s | %s记录区**为空**(0 条)⇒ 这是干净起点, 第 1 条将由本次「发生」写下"
                  " (seq0=0, 真记录序号从 1 起)" % (self.t0, self._name))
        else:
            print("   基线: 表钟=%s | %s 最新一条 序号=%s 发生=%s 结束=%s"
                  % (self.t0, self._name, self.seq0, self._b0["t_start"] or "-",
                     self._b0["t_end"] or "(未结束)"))
        return True

    # ------------------------------------------------------------------
    # 段 2: 基线体检 —— 「有头无尾」的遗留行(没递 bp_guard 就记"本次没做成")
    # ------------------------------------------------------------------
    def heal(self):
        """基线那一行若「有头无尾」则补完它。判法与两处拦着的报法见 `_meas_event_roundtrip`。"""
        ser, wait, spec = self.ser, self.wait, self.spec
        _b0, guard_vars = self._b0, self.guard_vars
        # ⚠ 只在"开始时刻有值而结束时刻为空"时才算 —— 空记录区两者都是 None(而且那一行本身就不存在),
        #   那是**正常**的空, 别把它也读成"有头无尾"(那会把一个干净的起点说成"上次跑中断了")。
        if _b0 is None or _b0["t_start"] is None or _b0["t_end"] is not None:
            return
        _healed, _hl, _h_ok = False, "无会话或用户指定只做黑盒, 本次没补", None
        _h_why = _hl
        if self.have_wb and self.bp_guard is None:
            # 没递守卫行锚点 ⇒ 这一段**无从判**: 上面那句黑盒的"有头无尾"不是守卫看见的那一行,
            # 拿它当断言就是把台面的账记到固件头上。如实记"没做成"并点名缺的是哪个锚点。
            _h_ok = None
            _h_why = ("脚本没递 `bp_guard`(守卫行 `TaskMetering.c:3611` 锚点)⇒ 读不到守卫真正的输入, "
                      "这一段判不了 —— 守卫走哪一支由它自己读到的那四个字节决定, 与黑盒读回的那一行"
                      "未必是同一行")
            print("   !! %s" % _h_why)
            _healed = False
        elif self.have_wb:
            _healed, _h_ok, _h_why = self._heal_with_wb()
        self._add("参考: 基线最新一条%s记录**有头无尾**(上一次跑中断留下) —— %s"
                  % (self._name, "已补完" if _healed else "本次没补"), _h_ok,
                  "%s; 序号=%s 发生=%s 结束=%s"
                  % (_h_why, self.seq0, self.rows0[1]["t_start"] or "-",
                     self.rows0[1]["t_end"] or "(未结束)"))

    def _heal_with_wb(self):
        """`heal()` 有会话那一半: 先停在守卫行读它真正的输入, 再判该注哪个 sta、该落哪个写库口。

        ⚠ 顺序承重, 理由(为什么不能拿黑盒那句"有头无尾"直接当守卫的输入, 以及那笔写不进去
          **两处**拦着)全在 `_meas_event_roundtrip` 的 docstring 与它那一段的注释里。
        返回 `(healed, ok, why)`。
        """
        ser, wait, spec = self.ser, self.wait, self.spec
        guard_vars = self.guard_vars
        _rlex = "TAB_Recd[TAB_RecdId[%d].id].len" % self._idx
        _rg = self._land(self.bp_judge, spec["inj_heal"], self.bp_guard,
                         tuple(guard_vars) + (_rlex,),
                         "断[A] 补完读数: 停 %s 逐拍重注 g_EventSta[%d]=TRUE + Tmr, 停在**守卫行** "
                         "%s 读它真正的输入(buff[3]/[4]/[89]/[90] + sta/id + 记录块长)"
                         % (_bptxt(self.bp_judge), self._idx, _bptxt(self.bp_guard)))
        _hl = "ok=%s | %s" % ((_rg or {}).get("ok"), (_rg or {}).get("detail") or "-")
        print("      断[A] 补完读数那一趟: %s" % _hl)
        _gv = (_rg or {}).get("vars") or {}
        print("      守卫行读到: %s" % _gv)
        _gd, _gm = gb1(_gv.get("buff[3]")), gb1(_gv.get("buff[4]"))
        _gd2, _gm2 = gb1(_gv.get("buff[89]")), gb1(_gv.get("buff[90]"))
        _gsta = gdb_sym(_gv.get("sta"))
        _rlen = gb1(_gv.get(_rlex))      # 记录块长; 读不到是 None, 不当 0
        # 守卫那两个字节组的判法照抄原文(`:3611-3614`): 头有值(非 0x00/0xFF) 且 尾为空(0x00/0xFF)。
        # ⚠ 四个字节**读不到时不能当 0** —— 0x00 在原文里是**有意义的值**("空"), 当 0 会让
        #   "有头无尾"凭空成立。读不到 ⇒ `_known=None` ⇒ 这一段记"本次没做成"。
        _known = all(_x is not None for _x in (_gd, _gm, _gd2, _gm2))
        _gopen = bool(_known and ((_gd not in (0, 0xFF)) or (_gm not in (0, 0xFF)))
                      and (_gd2 in (0, 0xFF)) and (_gm2 in (0, 0xFF)))
        _gneed = "TRUE" if _gopen else "FALSE"
        _gsite, _gsv = (self.bp_wre, self.wre_vars) if _gopen else (self.bp_wrs, self.wrs_vars)
        # ⚠ 只有**读到了名字**才判"注错了 sta"; 读不到(被优化掉/这一停没有它的位置)不算 ——
        #   否则会白跑一趟补注, 而打印出来的理由是编的。
        if (_rg or {}).get("ok") is True and _known and _gsta in ("TRUE", "FALSE") \
                and _gsta != _gneed:
            # 这一趟注的 `sta` 不是守卫要的那个 ⇒ 它在 `:3616`/`:3623` 当场 return ⇒ 本次不写库。
            # **这不是"固件不落库"**: 换守卫要的那个再跑一趟。
            print("      守卫行: sta=%s, 而 buff 四字节(buff[3]=%s buff[4]=%s buff[89]=%s "
                  "buff[90]=%s)判出『有头无尾』=%s ⇒ 这一支要的是 sta=%s —— 上一趟被守卫当场"
                  "打回, 改注 %s 再跑一趟" % (_gsta, _gd, _gm, _gd2, _gm2, _gopen, _gneed, _gneed))
            # 注 sta=TRUE 走「记录结束」(:3653)时要 `state[i]=FALSE`(否则 `:2004` 每拍把计时清零);
            # 注 sta=FALSE 走「记录开始」(:3634)时要 `state[i]=TRUE` —— 两张表各自把这一半也钉好了,
            # 这里只按守卫要的 sta 选表。
            _rg = self._land(self.bp_judge,
                             spec["inj_heal"] if _gopen else spec["inj_on_fast"], _gsite, _gsv,
                             "断[A] 补完落库: 注守卫要的 sta=%s ⇒ 等写库口 %s"
                             % (_gneed, _bptxt(_gsite)))
            print("      断[A] 补完落库那一趟: ok=%s | %s"
                  % ((_rg or {}).get("ok"), (_rg or {}).get("detail") or "-"))
        _r2 = self._reads(wait=wait, quiet=True)
        if _r2.get(1) is not None:
            self.rows0 = _r2
            self.seq0 = _r2[1]["seq"]
        _closed = bool(self.rows0.get(1) and self.rows0[1].get("t_end"))
        if _gopen and not _closed:
            # 守卫说要写尾段、串口却说这一行还没结束 —— 两条通道打架时**先让写盘与读回各走完
            # 一轮再判**, 不拿"刚写完那一刻的读回"当终值。
            time.sleep(wait)
            _r3 = self._reads(wait=wait, quiet=True)
            if _r3.get(1) is not None:
                self.rows0 = _r3
                self.seq0 = _r3[1]["seq"]
            _closed = bool(self.rows0.get(1) and self.rows0[1].get("t_end"))
        _gwhy = ("守卫行 %s 读到 buff[3]=%s buff[4]=%s buff[89]=%s buff[90]=%s ⇒ 『有头无尾』=%s, "
                 "该支要 sta=%s, 当时 `sta=%s`" % (_bptxt(self.bp_guard), _gd, _gm, _gd2, _gm2,
                                                   _gopen, _gneed, _gsta))
        # 三态归因: 按**守卫自己看见的输入**算它该不该写、该写哪一支, 再拿串口读回对。固件的账只
        # 在"守卫该放行、写库口也走到了, 而串口读回说没落"时才算 —— 且那时才是两条通道打架。
        if not _known or (_rg or {}).get("ok") is not True:
            return False, None, "这一次没读到守卫的输入 —— %s" % ((_rg or {}).get("detail") or "没停到守卫行")
        if not _gopen:
            # 守卫看见的不是"有头无尾" ⇒ 这一段的**前提**(黑盒基线说有头无尾、故该补完)在
            # 守卫那一侧不成立。**都不许据此判固件** —— 记本次没做成, 并把守卫的四个字节原样留下。
            return False, None, (
                "%s ⇒ 守卫**不认为**上一行有头无尾, 于是它只放行「记录开始」支; 而黑盒读回的"
                "是『有头无尾』⇒ **两条通道看见的不是同一行**(或 `RecdData.c:510` 的 "
                "`lst > num` 把 buff 清成了 0x00)。本次不据此判固件。" % _gwhy)
        # 守卫放行了 `sta=TRUE` ⇒ 走「记录结束」支 ⇒ 紧接着那一句是
        # `:3653 Write_RecdData(id, &buff[86], off=86, len=86, g_EventSec[idx])`。
        # **这一笔落不落得下去, 是算得出来的**: `Write_RecdData` 开头第一道守卫
        # (`Platform/RecdData.c:566`) 就是 `off + len > TAB_Recd[id].len ⇒ return FALSE`,
        # 而它两个操作数这会儿都在手里(记录块长见 `_rlex`)。别再让这一段止步于
        # "串口读回没变" —— 那读起来像"固件不落库", 说不出是哪一步拦的。
        _woff, _wlen = 86, 86
        if _rlen is None:
            return False, None, (
                "%s ⇒ 落「记录结束」支, 但记录块长(`%s`)这一次没读到 ⇒ 那一笔过不过得了 "
                "`Write_RecdData` 的越界守卫算不出来 —— 本次不据此判固件; 串口读回结束=%s"
                % (_gwhy, _rlex, self.rows0[1]["t_end"] or "(未结束)"))
        if _woff + _wlen > _rlen:
            # ⚠ 这一支写不进去**有两处拦着, 两处都已落实**(别只报一处: 只报①会让人以为把②修了
            #   就好, 只报②会让人以为①没发生 —— 而实测是 `:3653` 这个写库口**一次都没被执行过**,
            #   正是①该负责的那一段):
            #   ① `:3652 Get_RecdData(&buff[92], 0)` 里头 `Copy_Data(pBuff, g_EngyData, 80)`
            #      要写 `buff[92..171]`, 而这条记录块只有 `_rlen` 字节 ⇒ 越界, 正压到 `push`
            #      存下的 `{r4,r5,r6,lr}` 上。
            #   ② 就算走到了 `:3653`, `off+len=86+86=172 > _rlen` ⇒ `Write_RecdData` 拒绝。
            return False, False, (
                "%s ⇒ 落「记录结束」支。这一支写不进去, 本固件上**两处**拦着: "
                "① 它先做 `:3652 Get_RecdData(&buff[92], 0)` —— 里头是 "
                "`Copy_Data(pBuff, g_EngyData, 80)`, 要写 `buff[92..171]`, 而 `buff` 连同"
                "这条记录块只有 %d 字节 ⇒ 越界 %d 字节, 正压在 `push` 存下的 "
                "`{r4,r5,r6,lr}` 上; ② 就算走到 `:3653 Write_RecdData(id, &buff[%d], "
                "off=%d, len=%d, g_EventSec[idx])`, 也是 `off+len=%d > %d` ⇒ 在 "
                "`Platform/RecdData.c:566` 的越界守卫上 `return FALSE`。两处都在源码与镜像上"
                "落实 ⇒ 这一笔**写不进记录区**是**固件缺陷**, 不是本次没补; 串口读回结束=%s"
                % (_gwhy, _rlen, 171 - (_rlen - 1), _woff, _woff, _wlen,
                   _woff + _wlen, _rlen, self.rows0[1]["t_end"] or "(未结束)"))
        return bool(_closed), bool(_closed), (
            "%s ⇒ 落「记录结束」支, 期望旧行的结束时刻由空变有值; 记录块长=%d, "
            "`off+len=%d` 过得了越界守卫 ⇒ 这一笔该写得进去; 串口读回结束=%s"
            % (_gwhy, _rlen, _woff + _wlen, self.rows0[1]["t_end"] or "(未结束)"))

    # ------------------------------------------------------------------
    # 段 3: pre-flight —— 先弄清"该支在本台启不启用", 再让否定期望段跑
    # ------------------------------------------------------------------
    def preflight(self):
        """停 `bp_judge` 读参数区门槛, 再按**驱动入参**现算各段窗口秒数。

        为什么必须排在否定期望段**之前**、以及窗口为什么必须 ≥ 该事件的去抖秒数:
        见 `_meas_event_roundtrip` 里 `pre-flight` 与 `_WIN` 那两段注释。
        """
        spec = self.spec
        self._lv, _pre = None, None
        if self.have_wb and self.hit is not None:
            _pre = self.hit(self.bp_judge, 6.0, vars=self.judge_vars,
                            label="断[A] pre-flight: 停 %s 读 %s(判该支在本台启不启用)"
                                  % (_bptxt(self.bp_judge), spec.get("limit_field") or "参数区门槛"),
                            crit=None)
            if _pre is not None:
                print("      pre-flight 停 %s: ok=%s | %s"
                      % (_bptxt(self.bp_judge), _pre.get("ok"), _pre.get("detail")))
                print("      停时读到: %s" % (_pre.get("vars") or {}))
        # 门槛读回 0 ⇒ `:1923 if (limit != 0)` 常假, 该支整段被跳过。**不是固件错, 是本台证不了**
        # (要证须先写参数区, 而参数区受 Cmp_CompFlag 的 CRC 每秒复检; 走 645 写参又会动表参数)。
        self._lv = gdb_ints(((_pre or {}).get("vars") or {}).get(spec.get("limit_field")) or "") \
            if spec.get("limit_field") else []
        self._branch_off = bool(self._lv) and self._lv[0] == 0
        if self._lv:
            print("      参数区 %s 读回 %s ⇒ 该支%s"
                  % (spec["limit_field"], self._lv[0],
                     "**在本台不启用**" if self._branch_off else "在本台启用"))
        self.pre = _pre
        self._pre_vars = (_pre or {}).get("vars") or {}
        # 各否定期望段的窗口秒数: 现查**驱动入参**, 不写第二份。
        # ⚠ 2026-09-20 实测: 40s 窗口对着 57s 的去抖, 记了一条 `:2004` 明明没在复位的 PASS。
        _dly_gap = _ovl_dly_expect(self._pre_vars.get(spec.get("dly_field"))) \
            if spec.get("dly_field") else None
        self._WIN = {"sample_gap": max(self.sample_gap, _dly_gap + 15.0) if _dly_gap else self.sample_gap,
                     "jump_timeout": self.jump_timeout, "nat_timeout": self.nat_timeout}

    # ------------------------------------------------------------------
    # 段 4: 各否定期望段(表驱动) —— 判据不成立的这段时间里, bp_call 一次都不该被走到
    # ------------------------------------------------------------------
    def neg_legs(self):
        """`spec["neg_legs"]` 逐条: 白盒那次"不该被走到" + 串口那次"记录未新增"。"""
        ser, wait, spec = self.ser, self.wait, self.spec
        for _nl in spec["neg_legs"]:
            self._neg_leg(_nl)

    def _neg_leg(self, _nl):
        ser, wait, spec = self.ser, self.wait, self.spec
        _n_crit, _n_txt, _n_inj = _nl["crit"], _nl["txt"], _nl["inj"]
        _n_fal = _nl["falsify"]
        _n_secs = self._WIN[_nl["win"]]      # 未知窗口名 ⇒ KeyError, 当场炸(静默取默认值是不行的)
        # 该段靠注入造触发条件、而该支在本台不启用 ⇒ 这一趟证不了任何事(见 pre-flight 那段)。
        _n_skip = _n_inj is not None and self._branch_off
        _n_rec = None
        if _n_skip:
            _n_why = ("**本台证不了**: %s 读回 0 ⇒ `:1923 if (limit != 0)` 常假、该支整段被跳过 "
                      "⇒ 注入造不出触发条件, 这一趟只会得到『没触发所以没落库』—— 那不是固件对的证据。"
                      "要证须先写参数区" % spec["limit_field"])
            print("      !! 否定期望段(%s) 本次不做: %s" % (_n_txt, _n_why))
        elif self.have_wb:
            if _n_inj is None:
                _n_rec = self.neq(self.bp_call, _n_secs, vars=("i", "delay"),
                                  label="断[A] 否定期望: %s: %.0fs 内 %s(Recd_OverLoad 调用点)"
                                        "**不该**被走到" % (_n_txt, _n_secs, _bptxt(self.bp_call)),
                                  crit=_n_crit, falsify=_n_fal)
            else:
                _n_rec = self.inj(self.bp_judge, _n_inj, watch=self.bp_call, watch_vars=("i", "delay"),
                                  at_vars=self.judge_vars, timeout=_n_secs,
                                  label="断[A] 否定期望: %s: 停 %s 注入后等 %.0fs, %s"
                                        "**不该**被走到"
                                        % (_n_txt, _bptxt(self.bp_judge), _n_secs,
                                           _bptxt(self.bp_call)),
                                  crit=_n_crit, falsify=_n_fal)
        else:
            time.sleep(_n_secs)
        rows_n = self._reads(wait=wait, quiet=True)
        _seq_n = (rows_n.get(1) or {}).get("seq")
        if _n_skip:
            self._add("断[A] 否定期望 —— %s ⇒ Recd_OverLoad 调用点未被走到" % _n_txt, None, _n_why,
                      crit=_n_crit, obs=judge.DEBUG,
                      falsify="不做这一次时无从判 —— 本记录不作为固件证据")
        elif _n_rec is not None:
            print("      断[A] 否定那一趟(%s): ok=%s | %s"
                  % (_n_txt, _n_rec.get("ok"), _n_rec.get("detail")))
            for _ln in (_n_rec.get("injects") or []):
                print("      注入账: %s" % _ln)
            self._add("断[A] 否定期望 —— %s ⇒ Recd_OverLoad 调用点未被走到" % _n_txt,
                      _n_rec.get("ok"), _n_rec.get("detail") or "没做成 —— 未证",
                      crit=_n_crit, obs=judge.DEBUG, falsify=_n_fal)
        else:
            self._add("断[A] 否定期望 —— %s" % _n_txt, None,
                      "没有调试会话(或用户指定只做黑盒)⇒ 白盒这一次没做成",
                      crit=_n_crit, obs=judge.DEBUG,
                      falsify="不做这一次时无从判 —— 本记录不作为固件证据")
        self._add("串口 否定期望 —— %s ⇒ %s记录未新增" % (_n_txt, self._name),
                  (None if _n_skip else (_seq_n == self.seq0))
                  if _seq_n is not None else None,
                  "序号 %s → %s%s" % (self.seq0, _seq_n,
                                     "" if _seq_n is not None else "(本轮读不回 ⇒ 没做成)"),
                  crit=_n_crit, falsify=_n_fal)

    # ------------------------------------------------------------------
    # 段 5: ① ⑤ 发生·自然去抖 —— 停 bp_judge 只注判据那几样, 等 :2021 读 i/delay
    # ------------------------------------------------------------------
    def happen_natural(self):
        """自然去抖那一趟(必须逐拍重注, 理由见 `_meas_event_roundtrip` 的「触发通道」)。"""
        ser, spec = self.ser, self.spec
        self.t_nat0 = read_clock(ser, chip="管理芯", quiet=True) or self.t0
        self._t_inj = time.time()
        self.r_nat = None
        if self.have_wb and not self._branch_off:
            self._inject_natural()
        self._t_trip = time.time()
        self.t_nat1 = read_clock(ser, chip="管理芯", quiet=True)
        self._report_natural()

    def _inject_natural(self):
        """`happen_natural` 里"真去注入"那一半(该支在本台启用、且有会话时才走)。"""
        spec = self.spec
        _lab_nat = ("断[A] %s ⇒ 自然去抖满后等 %s(Recd_OverLoad 调用点, 读 i/delay)"
                    % (spec["on_txt"], _bptxt(self.bp_call)))
        _fal_nat = ("判据成立后不去抖落库(移位/计时那两段不通)⇒ 注入后走不到 %s"
                    % _bptxt(self.bp_call))
        if self.hold is not None:
            # ⚠ 这一支**必须逐拍重注**: 判据条件每周期被数据通路刷回(见段头「触发通道」),
            #   单次注入只成立一拍, 而去抖要几十拍 —— 回退到 `inj` 的那条路在本台面攒不满。
            self.r_nat = self.hold(self.bp_judge, spec["inj_on"], EVT_HOLD_TICKS,
                                   at_vars=self.judge_vars, watch=self.bp_call,
                                   watch_vars=self.call_vars, watch_timeout=EVT_HOLD_WATCH,
                                   budget=max(self.nat_timeout, EVT_HOLD_BUDGET),
                                   label=_lab_nat)
            if self.r_nat is not None:
                self.r_nat["crit"], self.r_nat["falsify"], self.r_nat["name"] = "①", _fal_nat, _lab_nat
        else:
            print("      !! 本次没给 `hold`(逐拍重注)⇒ 回退到单次注入 —— 而本支的判据条件每周期被"
                  "数据通路刷回原值, 单次注入攒不满去抖, 这一趟只会得到『没走到判据断点』。"
                  "那不是固件没落库的证据。")
            self.r_nat = self.inj(self.bp_judge, spec["inj_on"], watch=self.bp_call,
                                  watch_vars=self.call_vars, at_vars=self.judge_vars,
                                  timeout=self.nat_timeout, label=_lab_nat,
                                  crit="①", falsify=_fal_nat)
            # ⚠ **没走进判据断点这一半, 不许记成 FAIL** —— 2026-09-18 立的规矩, 起因是 9-2 / 9-3
            #   两个脚本漏给了 `wb["hold"]`, 那两趟正是走这条路: 上面那行警告已经写明"那不是固件
            #   没落库的证据", 可下面仍按 `crit="①"` 记了 FAIL, 于是账本里躺着一条**归错人**的结论
            #   (5-2 那一趟有 `hold`、① 同样是 FAIL, 两条看着一模一样, 没人分得出来)。
            #   口径与 judge 模块头一致: **证不了 ≠ 证不过** ⇒ 这里改成"没做成"(ok=None)。
            #   反向不对称是**有意**的: 单次注入**走到了**断点 —— 那是硬证, 照样算 ① 满足;
            #   只有"没走到"才不可归因。
            if self.r_nat is not None and self.r_nat.get("ok") is False:
                self.r_nat["ok"] = None
                self.r_nat["detail"] = ("单次注入只成立一拍、攒不满去抖 ⇒ 没走到判据断点; 本趟没给 `hold`"
                                        " ⇒ 库按『没做成』记账(**不是固件没落库的证据**) · 原记录: %s"
                                        % (self.r_nat.get("detail") or ""))

    def _report_natural(self):
        """`happen_natural` 的记账那一半(读到的 i/delay → ① ⑤ 两条)。"""
        spec = self.spec
        r_nat = self.r_nat
        # ⚠ `inject_hit` 交回来的是 **judge 记录**(`common/judge.rec`), 键只有
        #   `name/ok/detail/crit/obs/falsify/trig` —— **没有 `hit`**。判"命中没有"只认 `ok` 三态。
        _delay_txt = ((r_nat or {}).get("vars") or {}).get("delay")
        _i_txt = ((r_nat or {}).get("vars") or {}).get("i")
        _i_nums = gdb_ints(_i_txt)
        # ⚠ 停在**调用点**读到的是 `i` —— 那是**循环计数器**(0..8), **不是事件号**: `:2021` 调的是
        #   `Recd_OverLoad(EV_OverLoadA+i)`。事件的真相 = `EV_OverLoadA + i`。
        #   **2026-09-18 实测栽过**: 这一处早先写成 `gdb_ints(_i_txt) == [_idx]`, 于是 5-2 / 9-2 / 9-3
        #   三个子项**各记一条归错人的 FAIL** —— 读回的 `i=0`(过载) 与 `i=3`(反向 24−21) 全是对的。
        _i_at = [spec["loop_base"] + n for n in _i_nums] if _i_nums else []
        _dlyf_txt = ((r_nat or {}).get("at_vals") or {}).get(spec["dly_field"])
        _dly_hope = _ovl_dly_expect(_dlyf_txt)
        _dly_got = gdb_ints(_delay_txt)
        # 参数为 0 ⇒ `:1923 if (limit != 0)` 那一判定常闭, 该支整个不启用 —— 这不是"固件错", 是**本台证不了**
        # `_branch_off` 已由 pre-flight 定过; 这里只兜底 —— pre-flight 没跑时从"发生那一停"的对照值里补看一眼。
        # ⚠ 兜底只补 `details`, **补不了否定期望段** —— 那几段在 pre-flight 之前就跑了。
        if not self._lv and spec.get("limit_field"):
            self._lv = gdb_ints(((r_nat or {}).get("at_vals") or {}).get(spec["limit_field"]) or "")
            self._branch_off = bool(self._lv) and self._lv[0] == 0
        if r_nat is None:
            _why_no = ("没有调试会话(或用户指定只做黑盒)⇒ 注入观测这一次没做成"
                       "(注入没有可降级的黑盒替身)")
            if self._branch_off:
                _why_no = ("**该支在本台不启用**: %s 读回 0 ⇒ `:1923 if (limit != 0)` 常假, "
                           "该支整段被跳过 ⇒ 注入造不出 `state[%d]`。这不是固件错, 是**本台证不了** —— "
                           "要证须先写参数区(受 Cmp_CompFlag 的 CRC 每秒复检; 走 645 写参会动表参数)"
                           % (spec["limit_field"], self._idx))
            self._add("断[A] 判据成立 ⇒ 执行到 Recd_OverLoad 调用点", None, _why_no,
                      crit="①", obs=judge.DEBUG,
                      falsify="不做这一次时无从判 —— 本记录不作为固件证据")
            self._add("断[A] 去抖秒数 = clamp(%s − %d, ≥ %d)"
                      % (spec["dly_field"], EVT_C_EVEFLT, EVT_C_EVDLY),
                      None, _why_no, crit="⑤", obs=judge.DEBUG,
                      falsify="不做这一次时无从判 —— 本记录不作为固件证据")
            return
        print("      断[A] 发生那一趟: ok=%s | %s" % (r_nat.get("ok"), r_nat.get("detail")))
        print("      停时读到: %s" % (r_nat.get("vars") or {}))
        _ins = r_nat.get("injects") or []
        # 逐拍重注有**几十条**注入账 —— 全打会把这一趟的证据淹掉, 全吞又丢了证据。取头尾各几条
        # + 总数: 「条件被每一拍重按一遍」这件事由头尾 + 条数就能看清(条数本身就是"按了几拍")。
        _show = _ins if len(_ins) <= 12 else \
            _ins[:4] + ["…(逐拍重注: 中间 %d 条省略)" % (len(_ins) - 6)] + _ins[-2:]
        for _ln in _show:
            print("      注入账: %s" % _ln)
        _vl = r_nat.get("vals_log") or []
        if len(_vl) > 1:
            print("      逐拍读数(共 %d 拍): 第 1 拍 %s | 末拍 %s" % (len(_vl), _vl[0], _vl[-1]))
        print("      注入前那一停读到: %s" % (r_nat.get("at_vals") or {}))
        self._add("断[A] 判据成立 + 去抖满 ⇒ 执行到 Recd_OverLoad 调用点(:2021), 且本次命中的正是 %s"
                  % self._txt,
                  (r_nat.get("ok") if r_nat.get("ok") is not True
                   else (_i_at == [self._idx] if _i_at else None)),
                  "%s; 循环计数器 i=%s ⇒ 事件号 EV_OverLoadA+i = %s(应为 %d —— 该调用点在 "
                  "for(i=0;i<9;i++) 里, 9 个事件共用)"
                  % (r_nat.get("detail") or "没命中 —— 未证", _i_txt,
                     ("[%s]" % ", ".join(str(x) for x in _i_at)) if _i_at else "读不到", self._idx),
                  crit="①", obs=judge.DEBUG,
                  falsify="判据成立也不落库(去抖不满 / 移位那一段不通)⇒ 不会停在 %s"
                          % _bptxt(self.bp_call))
        # ⑤ 的期望值由 `_ovl_dly_expect` 按**停点处现读**的参数现算 —— 不是写死一个数。
        # ⚠ `delay` 是 `Chk_OverLoad` 的**局部量**, 编译器在 `:2021` 那一停上不保证留得下位置
        #   (2026-09-20 实跑读回来是空的) ⇒ 只认那一个变量的话, 这一条会一直挂在"没做成"。
        #   故补一条**同量的另一路测量**: 发生那一趟注的是判据条件、**没有**注 `g_EventTmr`,
        #   计时是固件自己从 0 累上去的; 停到调用点的那一拍正是 `++g_EventTmr >= delay` 第一次
        #   成立的那一拍 ⇒ **命中拍数 = delay**。两条证据各自独立(一条读变量、一条数拍)。
        _dly_ticks = r_nat.get("at_hit")
        if _dly_got and _dly_hope is not None:
            _dly_ok = _dly_got[0] == _dly_hope
        elif _dly_ticks and _dly_hope is not None and r_nat.get("ok") is True:
            _dly_ok = _dly_ticks == _dly_hope
        else:
            _dly_ok = None
        self._add("断[A] 去抖秒数 = clamp(%s − %d, ≥ %d): 读回 delay=%s / 停到调用点的拍数=%s, "
                  "按停点处 %s=%s 现算应为 %s"
                  % (spec["dly_field"], EVT_C_EVEFLT, EVT_C_EVDLY, _dly_got or "读不到",
                     _dly_ticks if _dly_ticks else "读不到", spec["dly_field"], _dlyf_txt, _dly_hope),
                  _dly_ok,
                  "delay=%s; 本趟没注 Tmr ⇒ 拍数即去抖拍数(墙钟 注入→命中 = %.1fs)"
                  % (_delay_txt, self._t_trip - self._t_inj),
                  crit="⑤", obs=judge.DEBUG,
                  falsify="固件不减 C_EveFlt / 不按 TAB_RecdId[idx].dly 取参 / 不钳 C_EveDly "
                          "⇒ 读回的 delay(或命中拍数)与现算值不同")

    # ------------------------------------------------------------------
    # 段 6: ① ③ 串口 —— 序号推进 + 新行未结束 + 时标落在窗口
    # ------------------------------------------------------------------
    def ser_after_happen(self):
        ser, wait = self.ser, self.wait
        t_after = read_clock(ser, chip="管理芯", quiet=True)
        rows2 = self._reads(wait=wait)
        self.by2 = rows_by_seq(rows2)
        new_seqs = sorted(s for s in self.by2 if s > self.seq0)
        self.seq_new = new_seqs[-1] if new_seqs else None
        self.t_after = t_after
        self._add("串口 %s序号推进(新落「发生」行)且新行未结束" % self._name,
                  None if not self.by2 else (bool(new_seqs) and self.by2[self.seq_new]["t_end"] is None),
                  "序号 %s → %s(新行 %s; 新行结束时刻=%s)"
                  % (self.seq0, (rows2.get(1) or {}).get("seq"), new_seqs or "无",
                     (self.by2[self.seq_new]["t_end"] if self.seq_new else "-") or "(未结束)"),
                  crit="①",
                  falsify="固件不在去抖满后落『发生』⇒ 序号不推进 / 新行结束时刻不为空")
        self._add("串口 新「发生」行的发生时刻落在本次窗口(=当时表钟)",
                  lp_in_window(self.by2[self.seq_new]["t_start"] if self.seq_new else None,
                               self.t_nat0, clock_add(t_after or self.t_nat0, 15)),
                  "发生时刻=%s; 窗口=[%s, %s+15s]"
                  % (self.by2[self.seq_new]["t_start"] if self.seq_new else None,
                     self.t_nat0, t_after or self.t_nat0),
                  crit="③",
                  falsify="时标取的不是当时表钟(Get_MeterTime 写错偏移 / 用了旧时间)⇒ 落窗外")

    # ------------------------------------------------------------------
    # 段 7: ② 恢复·跳时 —— 停 bp_judge 注 OFF + Tmr, 等 :3653(「记录结束」支写库位置)
    # ------------------------------------------------------------------
    def recover_jump(self):
        ser, spec = self.ser, self.spec
        self.t_inj2 = read_clock(ser, chip="管理芯", quiet=True) or self.t_after or self.t0
        self.r_end = None
        if self.have_wb:
            self.r_end = self._land(
                self.bp_judge, spec["inj_off_fast"], self.bp_wre, self.wre_vars,
                "断[A] 注入 %s(判据翻假)并把 g_EventSta[%d] 钉在 TRUE(否则 :2004 每拍把计时清零)"
                "⇒ 等「记录结束」写库位置 %s"
                % (spec["off_txt"], self._idx, _bptxt(self.bp_wre)))
        self.t_inj2b = read_clock(ser, chip="管理芯", quiet=True)
        if self.r_end is not None:
            print("      断[A] 恢复那一趟: ok=%s | %s" % (self.r_end.get("ok"), self.r_end.get("detail")))
            print("      停时读到: %s" % (self.r_end.get("vars") or {}))
            for _ln in (self.r_end.get("injects") or []):
                print("      注入账: %s" % _ln)
            print("      注入前那一停读到: %s" % (self.r_end.get("at_vals") or {}))
            _eidx = (self.r_end.get("vars") or {}).get("idx")
            # ⚠ **没命中 = 这一次没证成, 不是固件没动**: 注入按不按得住、守卫放不放行, 都在固件之外。
            #   写库口这一路**只有命中**时才谈得上"固件走到了哪一支"; 没命中记 `None`, 本判据的账由
            #   串口那两条认领。
            self._add("断[A] 判据翻假 ⇒ 落到「记录结束」写库位置(:3653), 且写的 idx 正是 %s" % self._txt,
                      (None if self.r_end.get("ok") is not True
                       else (gdb_ints(_eidx) == [self._idx] if gdb_ints(_eidx) else None)),
                      "%s; %s; idx=%s id=%s(应为 %d)"
                      % (self.r_end.get("detail") or "没命中 —— 未证",
                         "写库口没命中 ⇒ 本次没证成(按不按得住、放不放行都在固件之外), 不据此判固件"
                         if self.r_end.get("ok") is not True else "写库口命中",
                         _eidx, (self.r_end.get("vars") or {}).get("id"), self._idx),
                      crit="②", obs=judge.DEBUG,
                      falsify="固件不走『记录结束』支, 或写的是别的事件号(idx≠%d)⇒ 不会停到 %s / idx 不匹配"
                              % (self._idx, _bptxt(self.bp_wre)))
        else:
            self._add("断[A] 判据翻假 ⇒ 恢复写库", None,
                      "没有调试会话(或用户指定只做黑盒)⇒ 白盒这一次没做成", crit="②", obs=judge.DEBUG,
                      falsify="不做这一次时无从判 —— 本记录不作为固件证据")

    # ------------------------------------------------------------------
    # 段 8: ② ③ 串口 —— 上一行的结束时刻被补上, 序号不推进
    # ------------------------------------------------------------------
    def ser_after_recover(self):
        wait = self.wait
        rows3 = self._reads(wait=wait)
        by3 = rows_by_seq(rows3)
        _row_new = by3.get(self.seq_new) if self.seq_new is not None else None
        self._row_new = _row_new
        self._t_end_new = (_row_new or {}).get("t_end")
        self._win3 = (self.t_inj2, clock_add(self.t_inj2b or self.t_inj2, 12))
        self._add("串口 『发生』行(序号=%s)的结束时刻被补上 = 落了「恢复」一笔(未新开行)" % self.seq_new,
                  None if _row_new is None
                  else (self._t_end_new is not None and lp_in_window(self._t_end_new, *self._win3)),
                  "结束时刻=%s; 注入窗口=[%s, %s+12s]"
                  % (self._t_end_new or "(未结束)", self._win3[0], self.t_inj2b or self.t_inj2),
                  crit="②",
                  falsify="固件不把结束时刻补进上一行(或另开一行)⇒ 该行结束时刻仍为空 / 序号多推进一次")
        # ⚠ 这一条**只在『发生』那一行确实落了**的时候才成立:`seq_new is None` / 那一行读不回时,
        #   "最大序号没变"是**空判**(什么都没落当然没变)。2026-09-20 实跑里它就是这么记了一条 PASS ——
        #   而那一趟一个字节都没落库。空判的 PASS 比 FAIL 更难查, 故没有立足点时记 `None`。
        self._add("串口 序号在「恢复」后不推进(恢复不新开一行)",
                  None if (_row_new is None or not by3) else (max(by3) == self.seq_new),
                  "最大序号 %s(恢复后应与发生后同为 %s)%s"
                  % (max(by3) if by3 else "-", self.seq_new,
                     "" if _row_new is not None else " —— 『发生』那一行没落/读不回 ⇒ 本判据无立足点"),
                  crit="②",
                  falsify="恢复也新开一行 ⇒ 最大序号再推进一格")
        self._add("串口 新「发生」行的结束时刻落在本次窗口(=当时表钟)",
                  lp_in_window(self._t_end_new, *self._win3),
                  "结束时刻=%s; 窗口=[%s, %s+12s]"
                  % (self._t_end_new or "(未结束)", self._win3[0], self.t_inj2b or self.t_inj2),
                  crit="③",
                  falsify="结束时刻取的不是当时表钟(Get_MeterTime 写错偏移)⇒ 落窗外")

    # ------------------------------------------------------------------
    # 段 9: ① (④) 发生·跳时 —— 停 bp_judge 注 ON + Tmr, 等 :3634
    # ------------------------------------------------------------------
    def happen_jump(self):
        spec = self.spec
        self.r_start = None
        if self.have_wb:
            self.r_start = self._land(
                self.bp_judge, spec["inj_on_fast"], self.bp_wrs, self.wrs_vars,
                "断[A] 再注入 %s(判据复真)并把 g_EventSta[%d] 钉在 FALSE(否则 :2004 每拍把计时清零)"
                "⇒ 等「记录开始」写库位置 %s%s"
                % (spec["on_txt"], self._idx, _bptxt(self.bp_wrs),
                   "(读 buff/g_EngyData)" if spec.get("snapshot") else ""))
        if self.r_start is not None:
            print("      断[A] 二次发生那一趟: ok=%s | %s"
                  % (self.r_start.get("ok"), self.r_start.get("detail")))
            _sv = self.r_start.get("vars") or {}
            print("      停时读到: idx=%s id=%s" % (_sv.get("idx"), _sv.get("id")))
            for _ln in (self.r_start.get("injects") or []):
                print("      注入账: %s" % _ln)
            print("      注入前那一停读到: %s" % (self.r_start.get("at_vals") or {}))
            # ⚠ 同 ② 那条: 没命中 ⇒ `None`(本次没证成), 不记 "固件仍未动"。
            self._add("断[A] 判据复真 ⇒ 落到「记录开始」写库位置(:3634), 且写的 idx 正是 %s" % self._txt,
                      (None if self.r_start.get("ok") is not True
                       else (gdb_ints(_sv.get("idx")) == [self._idx] if gdb_ints(_sv.get("idx")) else None)),
                      "%s; %s; idx=%s id=%s(应为 %d)"
                      % (self.r_start.get("detail") or "没命中 —— 未证",
                         "写库口没命中 ⇒ 本次没证成(按不按得住、放不放行都在固件之外), 不据此判固件"
                         if self.r_start.get("ok") is not True else "写库口命中",
                         _sv.get("idx"), _sv.get("id"), self._idx),
                      crit="①", obs=judge.DEBUG,
                      falsify="固件不走『记录开始』支, 或写的是别的事件号 ⇒ 不会停到 %s / idx 不匹配"
                              % _bptxt(self.bp_wrs))
            if spec.get("snapshot"):
                # ④ 电量快照: 写库那一刻 buff[6..85] 应当**逐字节等于**固件那份 g_EngyData。
                #   ⚠ 两个量都是 `INT8U[]`, gdb 按**字符串字面量**印(不是 `{1,2,3}`)⇒ 必须用 `gdb_bytes`,
                #     拿 `gdb_ints` 抠会静默读出差一截的序列(见 `gdb_bytes` 的说明)。
                _bb = gdb_bytes(_sv.get("buff"))
                _be = gdb_bytes(_sv.get("g_EngyData"))
                _snap = _bb[6:86] if len(_bb) >= 86 else []
                _allzero = bool(_be) and not any(_be)
                self._add("断[A] ④a 「发生」一笔的电量快照取自 g_EngyData(写库那一刻 buff[6..85] 与之逐字节相同)",
                          (_snap == _be) if (_snap and _be) else None,
                          "buff[6..85] 长 %d / g_EngyData 长 %d; 两者%s; g_EngyData %s"
                          % (len(_snap), len(_be), "逐字节相同" if (_snap and _snap == _be) else "不同或读不出",
                             "**全 0**(⇒ 本台『快照非空壳』那一半证不了)" if _allzero else "非全 0"),
                          crit="④a", obs=judge.DEBUG,
                          falsify="固件不把 g_EngyData 拷进记录正文(或拷到别的偏移)⇒ 那一段与它不同")
        else:
            self._add("断[A] 判据复真 ⇒ 发生写库", None,
                      "没有调试会话(或用户指定只做黑盒)⇒ 白盒这一次没做成", crit="①", obs=judge.DEBUG,
                      falsify="不做这一次时无从判 —— 本记录不作为固件证据")
            if spec.get("snapshot"):
                self._add("断[A] ④a 电量快照取自 g_EngyData", None,
                          "没有调试会话(或用户指定只做黑盒)⇒ 白盒这一次没做成", crit="④a", obs=judge.DEBUG,
                          falsify="不做这一次时无从判 —— 本记录不作为固件证据")

    # ------------------------------------------------------------------
    # 段 10: ② 末段 —— 再落一次「恢复」, 让跑完的记录是完整的、g_EventSta[idx] = FALSE
    # ------------------------------------------------------------------
    def recover_last(self):
        ser, wait, spec = self.ser, self.wait, self.spec
        self.r_end2 = None
        if self.have_wb:
            self.r_end2 = self._land(
                self.bp_judge, spec["inj_off_fast"], self.bp_wre, self.wre_vars,
                "断[A] 末段: 注入 %s 并把 g_EventSta[%d] 钉在 TRUE ⇒ 等「记录结束」写库位置 %s"
                "(跑完记录完整、状态复位)" % (spec["off_txt"], self._idx, _bptxt(self.bp_wre)))
        self.t_last = read_clock(ser, chip="管理芯", quiet=True)
        rows4 = self._reads(wait=wait)
        self.by4 = rows_by_seq(rows4)
        if self.r_end2 is not None:
            print("      断[A] 末段恢复: ok=%s | %s"
                  % (self.r_end2.get("ok"), self.r_end2.get("detail")))
            # ⚠ 同 ② 那条: 没命中 ⇒ `None`。
            self._add("断[A] 末段恢复落到「记录结束」写库位置(:3653)",
                      self.r_end2.get("ok") if self.r_end2.get("ok") is True else None,
                      "%s; %s" % (self.r_end2.get("detail") or "没命中 —— 未证",
                                  "写库口没命中 ⇒ 本次没证成, 不据此判固件" if self.r_end2.get("ok") is not True
                                  else "写库口命中, 且写的 idx 是 %s" % (self.r_end2.get("vars") or {}).get("idx")),
                      crit="②", obs=judge.DEBUG,
                      falsify="固件不走『记录结束』支 ⇒ 不会停在 %s" % _bptxt(self.bp_wre))
        else:
            self._add("断[A] 末段恢复写库", None,
                      "没有调试会话(或用户指定只做黑盒)⇒ 白盒这一次没做成", crit="②", obs=judge.DEBUG,
                      falsify="不做这一次时无从判 —— 本记录不作为固件证据")

    # ------------------------------------------------------------------
    # 段 11: 参考记录 + 本次范围 + 未证的话 ⇒ (recs, details, scope)
    # ------------------------------------------------------------------
    def finish(self):
        """收尾: 三条 `crit=None` 的参考记录(不进判据计数)、本次范围与未证的话。

        返回 `(recs, details, scope)`; **不总结论** —— 判定只有 `common/judge.py` 一处。
        """
        spec = self.spec
        _o2 = self.by4.get(max(self.by4)) if self.by4 else None
        self._add("参考: 跑完最新一条%s记录的形状(末段停在「恢复」⇒ 应当是有头有尾)" % self._name,
                  None, "序号=%s 发生=%s 结束=%s"
                  % ((_o2 or {}).get("seq"), (_o2 or {}).get("t_start") or "-",
                     (_o2 or {}).get("t_end") or "(未结束)"))
        self._add("参考: 表钟在本次窗口内正常走时(记录时标可与之对拍)",
                  None, "起=%s 末=%s" % (self.t0, self.t_last))
        if spec.get("snapshot"):
            self._add("参考(源码级, 与判据无关): Recd_OverLoad 的「记录结束」支有越界写 —— "
                      "`INT8U buff[95]`(:3604; RecdData.h:280 = 55+40) 而 :3652 `Get_RecdData(&buff[92], 0)` "
                      "按 :4228 `Copy_Data(pBuff, g_EngyData, 80)` 写 buff[92..171](越界 77 字节); "
                      "另 :4222-4225 用了从未赋值的局部量 `i`", None,
                      "证据 = 本项白盒那一段停 :3653 时读到的 buff 长度与 :4228 那次 Copy_Data 的常量; "
                      "本项不修改、不据此下任何结论")

        if not self.have_wb:
            self.scope = "仅黑盒(用户指定)" if self.wb_waived else "仅黑盒(无调试会话)"
        else:
            self.scope = "黑盒+白盒"
        recs = self.recs
        n_fail = sum(1 for r in recs if r["ok"] is False)
        n_tbd = sum(1 for r in recs if r["ok"] is None)
        print("===== %s事件: %d 条证据(FAIL=%d 没做成=%d)  [本次范围: %s] ====="
              % (self._name, len(recs), n_fail, n_tbd, self.scope))
        details = ["%s: %s" % (r["name"], r["detail"]) for r in recs]
        if not self.have_wb:
            details.append("未做: 断点观测(白盒) —— %s; 要证『发生/结束两条写库路径 / 去抖秒数%s』"
                           "需接 J-Link 重跑(断点见 ledger.md 该项的 F 列)"
                           % ("用户本次指定只做黑盒" if self.wb_waived else "本次无调试会话",
                              " / 电量快照取自 g_EngyData" if spec.get("snapshot") else ""))
        if spec.get("snapshot"):
            details.append("未证: 电量快照『值等于当时真实电能』那一半 —— 要记录列解码器"
                           "(把 80 字节电量正文按列 OAD 解回六个电能): 本项只证它取自 g_EngyData")
        details.append("未证: 去抖秒数的**下钳位**那一支(%s ≤ %d ⇒ delay 被抬到 %d) —— "
                       "要改参数区; 参数区受 Cmp_CompFlag 的 CRC 每秒复检, 注入活不过一秒, 走 645 写参"
                       "又会动表参数 ⇒ 本台不建这一支"
                       % (spec["dly_field"], EVT_C_EVEFLT + EVT_C_EVDLY, EVT_C_EVDLY))
        if self._branch_off:
            details.append("未证: **%s 整支**在本台不启用(%s 读回 0 ⇒ `:1923 if (limit != 0)` 常假) —— "
                           "不是固件错, 是台面参数为默认值; 要证须先写参数区"
                           % (self._name, spec["limit_field"]))
        if self._rq == 0:
            details.append("未证(且**在本台证不了**): 『%s 的记录一定落着库』那一半 —— 本固件这一口的"
                           "**记录区容量 = 0**(%s), `Read_RecdData`(RecdData.c:448-455)直接 FALSE 且不写 pBuff、"
                           "`Write_RecdData` 同样出不去 ⇒ 一条都存不下; 而帧侧照样登着这个 OI, 读回是一段"
                           "**空区应答**而不是报错(别把『空』读成『固件不落库』)。判据算得对不对那半边不受影响"
                           "(判据行、去抖、调用点都照证)。要证这一半须先把 %s 改成非 0 重编固件。"
                           % (self._name, spec.get("rec_quota_txt") or self._rq,
                              spec.get("rec_quota_txt") or "该容量宏"))
        self.details = details
        return recs, details, self.scope


def overload_roundtrip(ser, wb=None, wb_waived=False, wait=3.0, sample_gap=40.0,
                       nat_timeout=150.0, jump_timeout=60.0,
                       bp_judge=None, bp_call=None, bp_wrs=None, bp_wre=None, bp_guard=None,
                       judge_vars=(), call_vars=(), wrs_vars=(), wre_vars=(), guard_vars=()):
    """**5-2 过载事件**(也供 **9-3** 复用 —— 规格明写「与 5-2 过载同一个断点(附录E 复验)」)。

    全流程 + 判据全在 `_meas_event_roundtrip` 里(两个子项共用那一份); 本函数只钉住过载的 `spec`。
    `bp_judge` = `TaskMetering.c:1882`(`limit = g_EventSet.OverLoadPlower;` @0x24822)。
    """
    return _meas_event_roundtrip(ser, OVL_SPEC, wb=wb, wb_waived=wb_waived, wait=wait,
                                 sample_gap=sample_gap, nat_timeout=nat_timeout,
                                 jump_timeout=jump_timeout, bp_judge=bp_judge, bp_call=bp_call,
                                 bp_wrs=bp_wrs, bp_wre=bp_wre, bp_guard=bp_guard,
                                 judge_vars=judge_vars, call_vars=call_vars, wrs_vars=wrs_vars,
                                 wre_vars=wre_vars, guard_vars=guard_vars)


def revpower_roundtrip(ser, wb=None, wb_waived=False, wait=3.0, sample_gap=40.0,
                       nat_timeout=150.0, jump_timeout=60.0,
                       bp_judge=None, bp_call=None, bp_wrs=None, bp_wre=None, bp_guard=None,
                       judge_vars=(), call_vars=(), wrs_vars=(), wre_vars=(), guard_vars=()):
    """**9-2 功率反向(总)** —— 与 `overload_roundtrip` 同一份驱动, 只换 `spec`。

    `bp_judge` = `TaskMetering.c:1898`(`limit = g_EventSet.RevPowerPlower;` @0x2486c)——
    **不是** `:1882`: 停在 `:1898` 时过载支已算完, 注入只落进反向支(9-2 与 5-2 互不串味)。
    其余三个断点与它们读的变量与 5-2 完全相同(同一趟 `Recd_OverLoad`)。
    """
    return _meas_event_roundtrip(ser, RVP_SPEC, wb=wb, wb_waived=wb_waived, wait=wait,
                                 sample_gap=sample_gap, nat_timeout=nat_timeout,
                                 jump_timeout=jump_timeout, bp_judge=bp_judge, bp_call=bp_call,
                                 bp_wrs=bp_wrs, bp_wre=bp_wre, bp_guard=bp_guard,
                                 judge_vars=judge_vars, call_vars=call_vars, wrs_vars=wrs_vars,
                                 wre_vars=wre_vars, guard_vars=guard_vars)


# ==================== 5-6『编程』: 成功写参 → Recd_Program 落库(2026-09-16 建) ====================
# 判据源 = project/knowledge/_whitebox_ledger/ledger.md 5-6「观察与判据」:
#   每次成功写参落一条; 操作者/项目/时标正确; 无权限写被拒且不记录。
#
# ---- 触发源(纯串口能造出、且表无净变的那一条) ----
#   成功的 645 受控写参 = 第1结算日 d0 → alt → d0。选结算日是因为它是本台**唯一被暴露为可写**
#   的费控参量(DI 04000B01), 且写回原值 ⇒ 跑完表无净变、不动表钟。
#
# ---- 固件语义: 为什么"两条口"必须分开看(它决定了条目怎么拆) ----
#   ① 「编程」事件(`Recd_Program`, TaskRecord.c:821) 带 static BOOL `b_PrgStart`(TaskRecord.c:47, 住 RAM):
#      上电后**首次**写参 → 新增一条; 同一上电周期内再写 → **就地改写**同一条(时刻保持首次编程那刻)。
#      ⇒ "每次写参都新增一条"**只在上电后首次成立**。拿它当唯一判据, 会把正常固件判成 FAIL。
#   ② 「结算日编程」事件(`Recd_PrgCntDay`, TaskRecord.c:1224; 触发点 DLT645App.c:2237) **无合并语义**,
#      每次写结算日**必新增一条**, 时刻取当下。
#   ⇒ 两条口各认领一条条目, 且口径写清: ①只证"落了一条", ②才证"每次必新增"。
#
# ---- 断点的订正(2026-09-16 离线核对断点: `gdb-multiarch -ex "info line/info scope"`) ----
#   规格 F 列写的是 `DLT645App.c:2928`。核对断点结论分两半:
#     · `:2928` → 0x36254 `CMD_WriteData+7184`, **是真可执行语句**(不是 `:307` 那种赋值空档)
#       ⇒ 它能证**指令路径**("执行到这条调用才成立");
#     · 但**读不到 `Recd_Program` 的入参** —— 那一停可读的局部量只有 `comSta`/`pFrame`/`u32`,
#       而 `pOper`/`pDIs` 住在**被调函数**的栈帧里 ⇒ 规格那句「Watch Recd_Program 入参」在 :2928 上**落空**。
#   ⇒ 入参改在**被调函数里面**读: `TaskRecord.c:824` → 0x1e416 `Recd_Program+8`,
#     该停可读 `pOper`/`pDIs`/`buff`(核对断点实测)。签名 `Recd_Program(const INT8U *pOper, const INT8U *pDIs)`。
#     ⚠ 入口那一行(:821 → 0x1e40e)是**空洞**, 可读变量为空 —— 停在那儿一个量都读不出来。
#       这正是 CLAUDE.md 那句「变量读不到通常不是被优化掉了, 而是断点停在了它的空洞区间」。
#   ⇒ **一个断点停 `:824` 同时给两样**: 调用**发生了**(= 指令路径) + 入参**是什么**(= 操作者/参数项)。
#     Cortex-M0 只有 4 个 FPB 槽, 合成一个断点比在两个地址各停一次省一口。
PROG_EVENT = P.PROG_EVENT
PROG_EVENT_SUB = P.PROG_EVENT_SUB



def program_criteria():
    """5-6 的预设条目(源 = ledger.md 5-6「观察与判据」三句话, 拆成五条)。

    ⚠ ④ 只证**入参确实被传进去**(非空), **不证值对不对** —— "操作者/项目正确"里的"正确"要拿
      DL/T645 规范里这两个串的**期望编码**去比, 本脚本手上没有那份期望值。那半支在 J 列明写未证,
      不拿"指针非空"去顶(见 CLAUDE.md「证不到的半支照实写」)。
    """
    return {
        "①": "成功写参落一条『编程』记录(30120B0A): 触发后该口最新一条**序号推进**(新增一条); "
              "若该口已置 `b_PrgStart`(同一上电周期内第二次及以后写参)则**就地改写**同一条 —— "
              "此形态下该口序号与时刻都不动, 由『写库位置真被调用』或『结算日编程口同一次写参推进』认定",
        "②": "『结算日编程』(301A0B0A) 每次成功写结算日**必新增**一条 —— 序号 +1(该口无 b_PrgStart 合并语义)",
        "③": "写库位置真走到 Recd_Program 调用(TaskRecord.c:824): 写参那一刻停在那儿",
        "④": "Recd_Program 收到的两个入参非空(pOper 操作者 / pDIs 参数项)—— 即这两样确实被传进去了",
        "⑤": "无权限写被拒且不记录: 退厂内后同一写帧被拒, 且两条事件口都不新增",
    }


def program_roundtrip(ser, wb=None, wb_waived=False, bp_rec=None, vars_rec=(), wait=3.0):
    """5-6 全流程 + 判据(库内单点, 脚本不留): 写结算日 alt(断点看入参) → 读两条口 → 写回 d0 → 验无权限写。

    **触发通道**: 帧(645 0x14 受控写)。本项**不注入** —— 写参这条路帧通道造得出来。

    **白盒那一次**(库不 import swdbg, 会话由脚本持有, 这里只收回调):
      `wb = {"fire": partial(GD.fire_hit, g)}` —— `fire_hit` 收**断点元组**而不是已挂好的 bpno:
      传元组时 `own=True`, 它自己挂、命中与没命中**两条路都撤**(传 bpno 只由 `drop=` 管,
      万一没命中就留在槽里 —— 那是"核被自己撂停、后面串口全哑"的来源, 见 `fire_hit` 的 ⚠)。
      · 断[A] 停 `bp_rec`(= 脚本的 `BP_REC`, `TaskRecord.c:824`) —— 触发帧发出去的**同一次写参**
        停在写库位置 ⇒ 一个读数认领 ③(指令路径) 与 ④(入参)。

    **表无净变的兜底**: 本函数把结算日写成 alt。写回 d0 那一次**必须在 `finally` 里** ——
      中途抛异常/提前返回都会让表留在 alt 号上, 而 `scripts/_restore_all.py` 明文"不动结算日"
      (它的依据就是"测试脚本跑完已自恢复"), 那个假设的兑现点在这里(与 `billday_rw_roundtrip`
      同一条理由, 详见其 docstring)。

    返回 `(recs, details, scope)` —— **`scope is None` = 半途中止**(结构信号, 别让脚本去嗅文案)。
    **本函数不总结论** —— 判定只有 `common/judge.py` 一处。
    """
    print("\n===== 5-6 编程事件: 写参 → Recd_Program 落库(断点看入参) + 无权限写被拒 =====")
    recs = []

    # 进厂内: 0x14 受控写的前提(Is_EnablePrg)。读记录同样受 Chk_SafeMode 管, 故这一步不是可选项。
    enter_factory(ser)

    def add(label, ok, why, crit=None, falsify=None, obs=judge.SERIAL, trig=None):
        recs.append(rec(label, ok, why, crit=crit, obs=obs, falsify=falsify, trig=trig))
        print("   [%s] %s —— %s" % ({True: "PASS", False: "FAIL", None: "TBD"}[ok], label, why))

    fire = (wb or {}).get("fire")
    have_wb = fire is not None
    if not have_wb and not wb_waived:
        print("   !! 本次无调试会话 → 断点观测(白盒)整体**未做**; 本次范围 = 仅黑盒"
              "(对外行为可判, 『写库位置走到哪儿、入参是什么』未取证 —— 见 CLAUDE.md「凭什么算「真实测试」·两种观测」)")
    elif not have_wb:
        print("   -- 本次**用户指定**只做黑盒(串口观测) ⇒ 断点观测(白盒)按指定不做(J 列须记明本次范围)")

    # ---- 基线 ----
    bd = read_billday(ser, wait=wait)
    if not bd:
        why = "读第1结算日无应答(d0 不可得)⇒ 写参的载体都没确认住, 中止"
        print("   !! %s" % why)
        return [rec("基线读取 ⇒ 中止", None, why, obs=judge.SERIAL)], [why], None
    d0 = bd[1]
    alt = d0 + 1 if d0 < 31 else d0 - 1
    t0 = read_clock(ser, chip="管理芯", quiet=True)
    pre = read_event_row(ser, PROG_EVENT, 1, wait=wait)
    pre_sub = read_event_row(ser, PROG_EVENT_SUB, 1, wait=wait)
    print("   基线: 表钟=%s | 结算日 d0=%d → 本次写 alt=%d | 编程 最新一条=%s 结算日编程 最新一条=%s"
          % (t0, d0, alt, (pre or {}).get("ts"), (pre_sub or {}).get("ts")))

    r_rec = None
    w_res = {}                          # 触发帧(写 alt)的 verdict —— 写没写成功决定 ① ② 能不能判
    try:
        # ---- 触发 + 断点观测: 写 alt 那一次停在 Recd_Program 里 ----
        def write_alt():
            # **取值**(与别的 `fn` 不同): 写本身没成时 ① ② 是"没做成"而不是"固件没落库"。
            w_res["v"], w_res["n"] = write_billday(ser, alt, wait=wait)

        if have_wb:
            r_rec = fire(bp_rec, write_alt,
                         label="断[A] 写第1结算日=%d → 停在 Recd_Program %s(读入参)"
                               % (alt, _bptxt(bp_rec)),
                         vars=tuple(vars_rec), crit="③",
                         falsify="写参成功而固件不走 `Recd_Program`(或落库前 return)⇒ 不会停在 %s"
                                 % _bptxt(bp_rec))
        else:
            write_alt()

        # ---- ① ② 判据: 两条口触发后的最新一条 ----
        post = read_event_row(ser, PROG_EVENT, 1, wait=wait)
        post_sub = read_event_row(ser, PROG_EVENT_SUB, 1, wait=wait)
        adv, why = event_advanced(post, pre, t0)
        adv_sub, why_sub = event_advanced(post_sub, pre_sub, t0)
        # ⚠ 写参本身没被受理(verdict != PASS) ⇒ 触发没发生, ①② 记"没做成", **不许**记 FAIL。
        #   "写被拒"是 ⑤ 那一条判据的事, 别让它顺带把 ① 判成"固件没落库"。
        trig_ok = (w_res.get("v") == "PASS")
        # ---- ① 的两支合法形态 ----
        #   新增一条: 序号推进(adv=True)。
        #   **就地改写**: 同一上电周期内第二次及以后写参, `b_PrgStart`(TaskRecord.c:47)已置位 ⇒
        #     固件走 off=10 那支改写同一条, 序号与时刻**都**保持首次那刻 ⇒ 该口读数与"没落库"同形。
        #     这一支要另找凭据才判得出, 手上只有两样(任一成立即认):
        #       · 写库位置真被调用 —— 断点那一次停在了 `Recd_Program`(③ 的读数, 同一次取证的另一个方面);
        #       · 『结算日编程』口同一次写参推进了 —— 写参这条路是通的, 只是这条口按语义合并了。
        #   ⚠ 不认这一支的后果是**必然**的假 FAIL: 一台已上电跑过一整天的表, 第一支永远不成立
        #     (5-6 实跑就是这么撞上的), 而这与本段头注"拿它当唯一判据会把正常固件判成 FAIL"是同一件事。
        wb_hit = ((r_rec or {}).get("ok") is True)
        if not trig_ok:
            add("『编程』口 30120B0A 触发后最新一条", None,
                "写第1结算日=%d 本身没成(verdict=%s / %s)⇒ 触发没发生, 本条未证"
                % (alt, w_res.get("v"), w_res.get("n")), crit="①",
                falsify="不做这一次时无从判 —— 本记录不作为固件证据")
        elif adv is False and (wb_hit or adv_sub is True):
            add("『编程』口 30120B0A 触发后最新一条", True,
                "就地改写(b_PrgStart 已置位 ⇒ 同一上电周期内不新增、序号与时刻都不动): %s; "
                "另据「%s」认定这一次写参确实经写库位置落库"
                % (why, "断点停在 Recd_Program %s" % _bptxt(bp_rec) if wb_hit
                   else "『结算日编程』口同一次写参推进(%s)" % why_sub),
                crit="①", falsify="写参成功而 Recd_Program 没被调用(或落库前 return)⇒ 这条口一动不动")
        else:
            add("『编程』口 30120B0A 触发后最新一条", adv, why, crit="①",
                falsify="写参成功而 Recd_Program 没被调用(或落库前 return)⇒ 这条口一动不动")
        add("『结算日编程』口 301A0B0A 触发后最新一条", adv_sub if trig_ok else None,
            why_sub if trig_ok else
            "写第1结算日=%d 本身没成(verdict=%s)⇒ 触发没发生, 本条未证" % (alt, w_res.get("v")),
            crit="②",
            falsify="`Recd_PrgCntDay` 也被 b_PrgStart 那套合并语义短路 ⇒ 第二、三次写结算日不新增")
    finally:
        # ---- 复原: 写回 d0 并读回验(见 docstring 那段"表无净变的兜底") ----
        w2, _n2 = write_billday(ser, d0, wait=wait)
        v2 = read_billday(ser, wait=wait)
        ok_back = bool(v2 and v2[1] == d0)
        print("   复原: 写回 d0=%d → %s; 读回=%s ⇒ %s"
              % (d0, w2, (v2[1] if v2 else None), "已复原" if ok_back else "**没复原**, 请人工核"))
        if not ok_back:
            recs.append(rec("结算日写回 d0 未确认", False,
                            "写回应答=%s / 读回=%s —— 表可能留在 alt=%s 号上, 需人工核"
                            % (w2, (v2[1] if v2 else None), alt), obs=judge.SERIAL))

    # ---- ③ ④ 断点那一次的记账 ----
    # ③ 由 `fire_hit` 那一条自己认领(它只答"停到没停到")。④ 是**同一个读数**说明的另一件事
    #   (两个入参非空), 从这里按同一份 `vars` 补一条 —— ③ 与 ④ 是**一次取证的两面**, 不是两次。
    # ⚠ 这里**不能**读 `r_rec["hit"]`: judge 的 record 只有 name/ok/detail/crit/obs/falsify/trig 七键,
    #   `hit` 在 `record()` 里就被拿去定 `ok` 并折进 `detail` 了(5-3 判据② 因此吃过一次假 FAIL)。
    _vals = (r_rec or {}).get("vars") or (r_rec or {}).get("at_vals") or {}
    if r_rec is not None:
        print("      断[A] 记录: ok=%s | %s" % (r_rec.get("ok"), r_rec.get("detail")))
        print("      停时读到: %s" % _vals)
        recs.append(r_rec)
        # `fire_hit` 没停到时 `vars` 是空的 ⇒ ④ 也记"没做成", 不冒充成"入参为空"。
        _nonnull = (all(nn(v) for v in _vals.values()) if _vals else None)
        add("Recd_Program 入参非空(pOper 操作者 / pDIs 参数项)", _nonnull,
            "读到 %s" % (_vals or "没停到, 入参没读到"), crit="④", obs=judge.DEBUG,
            falsify="固件把空操作者/空参数项传进 Recd_Program(丢掉入参)⇒ 两个指针里出现 0")
    else:
        why = ("本次无调试会话 ⇒ 断点观测这一次没做成"
               if not have_wb else "断点没停到 Recd_Program ⇒ 这一次没做成")
        add("写库位置 Recd_Program 调用(指令路径)", None, why, crit="③", obs=judge.DEBUG,
            falsify="不做这一次时无从判 —— 本记录不作为固件证据")
        add("Recd_Program 入参非空(pOper 操作者 / pDIs 参数项)", None, why, crit="④",
            obs=judge.DEBUG, falsify="不做这一次时无从判 —— 本记录不作为固件证据")

    # ---- ⑤ 无权限写被拒且不记录: 退厂内后同一写帧应被安全判定打回 ----
    t5 = read_clock(ser, chip="管理芯", quiet=True)
    pre5 = read_event_row(ser, PROG_EVENT, 1, wait=wait)
    pre5_sub = read_event_row(ser, PROG_EVENT_SUB, 1, wait=wait)
    vf = exit_factory(ser)
    v_free = None
    if not vf or vf != "PASS":
        add("退厂内(无权限写的前置)", None, "645 0x1F 未受理(verdict=%s)⇒ 这一次没做成" % vf,
            crit="⑤", falsify="不做这一次时无从判 —— 本记录不作为固件证据")
    else:
        v_free, note_free = write_billday(ser, alt, wait=wait)
        add("厂外写参被拒(verdict=%s, %s)" % (v_free, note_free), v_free != "PASS",
            "厂内态下同一帧是 PASS, 厂外应被安全判定打回", crit="⑤",
            falsify="安全判定没拦住厂外写参(verdict 仍 PASS)⇒ 无权限也能改参数")
    # ⚠ **回厂内之后才读记录**: 记录读回同样受 `Chk_SafeMode` 管(5-3 实踩), 厂外读一律被 DAR=20
    #   打回。2026-09-16 实跑第一遍那两次读就落在退厂内之后 ⇒ 读回来的是"被拒", 而当时那个把三态
    #   压成 bool 的小助手(`_ev_newer`, 已删)把"读不到"压成 False ⇒ 「两条口都不新增」是在
    #   **一个字节都没读到**的前提下记的 PASS。
    #   那是假通过: 拿"没读到"当"没新增"。改到这儿读, 并让三态显形(读不回来就记"没做成")。
    enter_factory(ser)                      # 退厂内是暂时的: 把台面交回厂内态, 免得后面几步全被安全判定打回
    if v_free is None:
        add("被拒的那一次两条事件口都不新增", None,
            "退厂内 / 厂外写参那一步没做成 ⇒ 没有『被拒的那一次』可判, 本条未证", crit="⑤",
            falsify="不做这一次时无从判 —— 本记录不作为固件证据")
    else:
        post5 = read_event_row(ser, PROG_EVENT, 1, wait=wait)
        post5_sub = read_event_row(ser, PROG_EVENT_SUB, 1, wait=wait)
        ok5a, why5a = event_advanced(post5, pre5, t5)
        ok5b, why5b = event_advanced(post5_sub, pre5_sub, t5)
        add("被拒的那一次两条事件口都不新增",
            (ok5a is False and ok5b is False) if (ok5a is not None and ok5b is not None) else None,
            "编程: %s; 结算日编程: %s" % (why5a, why5b), crit="⑤",
            falsify="写被拒了但 Recd_Program 照样落库 ⇒ 事件记录与被拒动作不成对应(记了一条没发生的编程)")

    n_fail = sum(1 for r in recs if r["ok"] is False)
    n_tbd = sum(1 for r in recs if r["ok"] is None)
    scope = "黑盒+白盒" if have_wb else ("仅黑盒(用户指定)" if wb_waived else "仅黑盒(本次无调试会话)")
    print("===== 5-6 编程事件: %d 条证据(FAIL=%d 没做成=%d)  [本次范围: %s] ====="
          % (len(recs), n_fail, n_tbd, scope))
    details = ["%s: %s" % (r["name"], r["detail"]) for r in recs]
    if not have_wb:
        details.append("未做: 断点观测(白盒) —— %s; 要证『写库位置走到 Recd_Program 且入参非空』"
                       "需接 J-Link 重跑(断点见 ledger.md 5-6 的 F 列, 已订正为 TaskRecord.c:824)"
                       % ("用户本次指定只做黑盒" if wb_waived else "本次无调试会话"))
    details.append("未证: 『操作者/项目**正确**』里的『正确』半支 —— 本脚本只证两个入参非空, "
                   "值对不对要拿 DL/T645 规范里操作者串与参数项列表的期望编码去比, 手上没有那份期望值")
    return recs, details, scope


def nn(txt):
    """gdb 表达式读回的文本 → 「非空」→ bool。**读不懂的当非空**, 不拿"我看不懂"当"它是 0"。

    gdb 对指针的 `value` 形态不止一种(`0x20000abc <Recd_Program>` / `(INT8U *) 0x0` / 十进制),
    故取**第一个数**去判 0; 一个数都抠不出来(例如 `optimized out` 之外的新形态)时**返 True** ——
    这条判据只用来认领 ④「入参非空」, 把它变成"看不懂就算入参为空"会造出假 FAIL。
    """
    s = str(txt if txt is not None else "").strip()
    if not s or "optimized out" in s or "No symbol" in s:
        return False
    hexes = re.findall(r"0x[0-9a-fA-F]+", s)
    if hexes:
        return int(hexes[0], 0) != 0
    # 没有十六进制 ⇒ 取**最后一个**十进制数: 强制转型的文本里也带数字
    # (`(INT8U *) 0` 里的 `8` 是类型名的一部分, 取第一个会把 0 读成非零 —— 这正是本函数
    #  反向用例当场抓到的那一条)。gdb 把值印在末尾, 故取末尾那个。
    decs = re.findall(r"\d+", s)
    return True if not decs else (int(decs[-1]) != 0)


# ==================== 16-1『软件要求·参数设置权限 / 编程记录 / 软件标识』(2026-09-21 建) ====================
# 判据源 = project/knowledge/_whitebox_ledger/ledger.md 16-1「观察与判据」三句话:
#   无权限写被拒且不记录; 成功写落一条编程记录; 软件标识读取/比对。
#
# ---- 与 5-6 / 16-2 的关系(先说清, 免得把互证读成新覆盖) ----
#   「权限/记录」半支与 5-6 走**同一条代码路径**(`CMD_WriteData` 收尾 → `Recd_Program` 落库),
#   「软件标识」半支与 16-2 是**同一批对象**(0xFF3005)。本项**独有的新观测只有 ①**:
#   正面停在**权限拒绝点**上。5-6 的 ⑤ 只有否定期望(厂外写 → `Recd_Program` 没被走到)+ 串口被拒 ——
#   那证明不了"是权限判定这一句挡下的": 帧在链路层被丢、或帧格式错被更早打回, 在否定期望下同形。
#   ⇒ ① 是新的; ②③④⑤ 与 5-6 互证(同一条路径的可复现), ⑥ 与 16-2 互证。
#
# ---- 断点(F 列订正: 2026-09-21 离线 `info line` 逐行核) ----
#   规格 F 列的 `断[B] DLT645App.c:2952 Is_EnablePrg(无权限拒绝点)` **锚错了**:
#     `:2952` → 0x36282 `CMD_WriteAddr+14` —— 那是**另一个命令**(645 0x15 写通信地址)体内的判定,
#     写参量(0x14)不经过它。照它下断点, 厂外写参那一趟永远等不到(每趟白等满 30 s 超时)。
#   写参量自己的权限判定与拒绝点在 `CMD_WriteData`(体界 `:1468`~`:2937`)里, 逐行核对:
#     · `:1538` → 0x34720 `CMD_WriteData+220`  `if ((pFrame[DAT0] != 0x02) || (TRUE != Is_EnablePrg()))`
#     · `:1539` → **无代码**(复合条件的后一行, `info line` 报 contains no code) —— 打不上
#     · `:1548` → 0x34742 `CMD_WriteData+254`  `return ER_PSWD;`   ← **本项断[B] 取这一行**
#   为什么不取 `:1538`: 它是**复合条件**, 两个半句任一成立都停在它那儿 —— 放行也要过这一行,
#     停住分不出这一趟是被拒还是放行。`:1548` 只有**被拒那一支**到得了(唯一的例外是信号强度 DI
#     `0x001301`, 它在 :1542 被摘走; 本项不发它) ⇒ 停在它 = 这一次写参被权限判定打回, 是硬证。
#   `ER_PSWD` = 2(`Application\TaskComm.h:13`), 链路层把它翻成帧上的错误位:
#     `DLT645Link.c:517 pFrame[CMD] |= 0xC0` + `:519 pFrame[DAT0] = 1<<comSta` ⇒ 应答控制码 `D4`、数据域 `04`。
#
# ---- 台面态的次序(为什么不能像 5-6 那样先厂内再厂外) ----
#   ① 要的是**厂外那一趟**; 而"不落记录"要靠**两条事件口**判, 记录读回又受 `Chk_SafeMode` 管
#   (厂外读一律被 DAR=20 打回 —— 5-3 实踩、5-6 的 ⑤ 为此改过一次)。⇒ 基线只能在**厂内**读, 于是次序:
#   厂内读基线 → 退厂内 → 厂外写两趟 → 回厂内读回比对 → 厂内有权限写 → 复原结算日 → 退厂内 → 读版本。
#   ⚠ 厂外那两趟**不产生副作用**: `:1548` 在任何写入之前 return, 结算日不会被改。另一个有副作用的口
#     (`:1512` 的软编程设置支会调 `Set_PrgTimer`)被**四个 DI 字节全等** `04 CC 00 01` 卡住, 本项的 DI
#     `04 00 0B 01` 进不去 —— 所以敢对同一帧发两次。
PROG_DENY_BP = ("DLT645App.c", 1548)      # `return ER_PSWD` —— 无权限写参的拒绝点
PROG_ALT_BP = ("TaskRecord.c", 824)       # `Recd_Program` 体内首条可执行 —— 写库位置 + 入参


def prog_auth_criteria():
    """16-1 的预设条目(源 = ledger.md 16-1「观察与判据」三句话, 拆成七条)。

    ⚠ ① 是本项**独有**的新覆盖; ②a/②b/③④⑤ 与 5-6 互证(同一条代码路径的可复现), ⑥ 与 16-2 互证。
    ⚠ ④ 只证**入参被传进去**(非空), **不证值对不对** —— 与 5-6 ④ 同口径; "记录里的操作者/参数项
      内容"那半支未证, 要一份记录列解码器。
    """
    return {
        "①": "无权限(厂外)写参被权限判定打回: 写参那一刻停在 `DLT645App.c:1548`"
              "(`return ER_PSWD`, `CMD_WriteData+254` @0x34742)—— 该行只有被拒那一支到得了",
        "②a": "厂外写参那一趟**没走到写库位置** `TaskRecord.c:824`(否定期望: 窗口内不命中)",
        "②b": "厂外写参之后两条事件口(『编程』`30120B0A` / 『结算日编程』`301A0B0A`)都**不新增**",
        "③": "有权限(厂内)写参落一条编程记录: 写参那一刻停在写库位置 `TaskRecord.c:824`",
        "④": "落库那次 `Recd_Program` 收到的两个入参非空(pOper 操作者 / pDIs 参数项)",
        "⑤": "厂内写参之后『结算日编程』口 `301A0B0A` 序号 +1(该口无 `b_PrgStart` 合并语义)",
        "⑥": "软件标识读取/比对: 698 读 `0xFF3005`(内部软件版本)的正文 == 本机发布标识 `K_SoftVersion`",
    }


def prog_auth_roundtrip(ser, wb=None, wb_waived=False, bp_deny=PROG_DENY_BP, vars_deny=(),
                        bp_rec=PROG_ALT_BP, vars_rec=(), nohit_win=3.0, wait=3.0):
    """16-1 全流程 + 判据(库内单点, 脚本不留)。

    **触发通道**: 帧(645 0x14 受控写)。本项**不注入** —— 权限这条路帧通道造得出来。

    **白盒三停**(库不 import swdbg, 会话由脚本持有, 这里只收回调):
      `wb = {"fire": partial(GD.fire_hit, g), "no_hit": partial(GD.expect_no_hit, g)}`
      · 断[B] 停 `bp_deny`(`:1548` 拒绝点)—— 厂外写**第一**趟;
      · 断[A] 否定期望 `bp_rec`(`TaskRecord.c:824`)—— 厂外写**第二**趟, 该点不该命中;
      · 断[A] 停 `bp_rec` —— 厂内有权限写那一趟, 读 `pOper`/`pDIs`。

    **表无净变的兜底**: 厂内那次把结算日写成 alt, 写回 d0 **必须在 `finally` 里**(与
      `program_roundtrip` / `billday_rw_roundtrip` 同一条理由: `_restore_all.py` 明文"不动结算日",
      那个假设的兑现点就在这里)。收尾把台面**交回厂外态** —— 本项起点也是厂外。

    返回 `(recs, details, scope)` —— **`scope is None` = 半途中止**(结构信号, 别让脚本去嗅文案)。
    **本函数不总结论** —— 判定只有 `common/judge.py` 一处。
    """
    print("\n===== 16-1 参数设置权限: 厂外写被拒(停拒绝点) + 厂内写落记录 + 软件标识 =====")
    recs = []

    def add(label, ok, why, crit=None, falsify=None, obs=judge.SERIAL, trig=None):
        recs.append(rec(label, ok, why, crit=crit, obs=obs, falsify=falsify, trig=trig))
        print("   [%s] %s —— %s" % ({True: "PASS", False: "FAIL", None: "TBD"}[ok], label, why))

    fire = (wb or {}).get("fire")
    no_hit = (wb or {}).get("no_hit")
    have_wb = fire is not None
    if not have_wb and not wb_waived:
        print("   !! 本次无调试会话 → 断点观测(白盒)整体**未做**; 本次范围 = 仅黑盒"
              "(对外行为可判, 『停在哪个拒绝点 / 写库位置走没走到』未取证 —— 见 CLAUDE.md「凭什么算「真实测试」·两种观测」)")
    elif not have_wb:
        print("   -- 本次**用户指定**只做黑盒(串口观测) ⇒ 断点观测(白盒)按指定不做(J 列须记明本次范围)")

    # ---- 0. 进厂内读基线: 记录读回受 Chk_SafeMode 管, 厂外读一律被 DAR=20 打回 ----
    enter_factory(ser)
    bd = read_billday(ser, wait=wait)
    if not bd:
        why = "读第1结算日无应答(d0 不可得)⇒ 写参的载体都没确认住, 中止"
        print("   !! %s" % why)
        return [rec("基线读取 ⇒ 中止", None, why, obs=judge.SERIAL)], [why], None
    d0 = bd[1]
    alt = d0 + 1 if d0 < 31 else d0 - 1
    t0 = read_clock(ser, chip="管理芯", quiet=True)
    pre = read_event_row(ser, PROG_EVENT, 1, wait=wait)
    pre_sub = read_event_row(ser, PROG_EVENT_SUB, 1, wait=wait)
    print("   基线: 表钟=%s | 结算日 d0=%d → 本次写 alt=%d | 编程 最新一条=%s 结算日编程 最新一条=%s"
          % (t0, d0, alt, (pre or {}).get("ts"), (pre_sub or {}).get("ts")))

    w_free = {}
    try:
        # ============ 厂外那两趟(① ②a) ============
        vf = exit_factory(ser)
        if not vf or vf != "PASS":
            add("退厂内(无权限写的前置)", None, "645 0x1F 未受理(verdict=%s)⇒ 厂外那一趟没做成" % vf,
                crit="①", falsify="不做这一次时无从判 —— 本记录不作为固件证据")
            add("厂外写参没走到写库位置(否定期望)", None, "同上, 这一次没做成", crit="②a",
                obs=judge.DEBUG, falsify="不做这一次时无从判 —— 本记录不作为固件证据")
        else:
            # ---- 厂外写 #1: 停在权限拒绝点 ----
            def write_free1():
                w_free["v"], w_free["n"] = write_billday(ser, alt, wait=wait)

            deny_rec, hit_ok = None, False
            if fire is not None:
                deny_rec = fire(bp_deny, write_free1,
                                label="断[B] 厂外写第1结算日=%d → 权限拒绝点 %s"
                                      % (alt, _bptxt(bp_deny)),
                                vars=tuple(vars_deny))
                hit_ok = (deny_rec or {}).get("ok") is True
                print("      断[B] 记录: ok=%s | %s" % ((deny_rec or {}).get("ok"),
                                                        (deny_rec or {}).get("detail")))
                recs.append(deny_rec)     # crit=None: 只进日志; 判据在下面按"停到"+"被拒"一起认
            else:
                write_free1()
            w_ok = (w_free.get("v") == "PASS")
            _deny_lab = "厂外写参停在权限拒绝点 %s" % _bptxt(bp_deny)
            if not have_wb:
                add(_deny_lab, None, "本次无调试会话 ⇒ 断点观测这一次没做成", crit="①",
                    obs=judge.DEBUG, falsify="不做这一次时无从判 —— 本记录不作为固件证据")
            elif hit_ok:
                add(_deny_lab, True,
                    "停在 %s(`return ER_PSWD`); 该次写回应答=%s(非受理)"
                    % (_bptxt(bp_deny), w_free.get("v")), crit="①", obs=judge.DEBUG,
                    falsify="厂外写参被放行(权限判定不生效)⇒ 走不到 `:1548` 的拒绝支, 断点不命中")
            elif w_ok:
                add(_deny_lab, False,
                    "断点没命中, 而该次写参被**受理**了(应答=%s)⇒ 权限判定没拦住厂外写参"
                    % w_free.get("v"), crit="①", obs=judge.DEBUG,
                    falsify="厂外写参被放行(权限判定不生效)⇒ 走不到 `:1548` 的拒绝支, 断点不命中")
            else:
                add(_deny_lab, None,
                    "断点没命中, 而该次写参确被拒(应答=%s)⇒ 拒绝发生了, 但不是这一句拦的"
                    "(或断点没下上/停在了别处), 这一条没做成" % w_free.get("v"),
                    crit="①", obs=judge.DEBUG,
                    falsify="厂外写参被放行(权限判定不生效)⇒ 走不到 `:1548` 的拒绝支, 断点不命中")

            # ---- 厂外写 #2: 写库位置的否定期望 ----
            def write_free2():
                w_free["v2"], w_free["n2"] = write_billday(ser, alt, wait=wait)

            if no_hit is not None:
                recs.append(no_hit(bp_rec, nohit_win,
                                   label="断[A] 厂外写第1结算日=%d → 写库位置 %s 否定期望(%.1fs)"
                                         % (alt, _bptxt(bp_rec), nohit_win),
                                   crit="②a",
                                   falsify="厂外写参也走到了写库位置 ⇒ 无权限也落记录",
                                   trigger=write_free2))
            else:
                write_free2()
                add("厂外写参没走到写库位置(否定期望)", None,
                    "本次无调试会话 ⇒ 断点观测这一次没做成", crit="②a", obs=judge.DEBUG,
                    falsify="不做这一次时无从判 —— 本记录不作为固件证据")

        # ============ 回厂内读回: 两条口都不新增(②b) ============
        # ⚠ 必须**回厂内之后**才读(厂外读一律被 DAR=20 打回, 拿"没读到"当"没新增"是假通过)。
        enter_factory(ser)
        post = read_event_row(ser, PROG_EVENT, 1, wait=wait)
        post_sub = read_event_row(ser, PROG_EVENT_SUB, 1, wait=wait)
        a1, y1 = event_advanced(post, pre, t0)
        a2, y2 = event_advanced(post_sub, pre_sub, t0)
        add("厂外写参之后两条事件口都不新增",
            (a1 is False and a2 is False) if (a1 is not None and a2 is not None) else None,
            "编程: %s; 结算日编程: %s" % (y1, y2), crit="②b",
            falsify="厂外写参也落了记录 ⇒ 这两条口的序号/时刻会动")

        # ============ 厂内写参: 停在写库位置(③ ④) ============
        w_res = {}

        def write_alt():
            w_res["v"], w_res["n"] = write_billday(ser, alt, wait=wait)

        r_rec = None
        if fire is not None:
            r_rec = fire(bp_rec, write_alt,
                         label="断[A] 厂内写第1结算日=%d → 停在写库位置 %s(读入参)"
                               % (alt, _bptxt(bp_rec)),
                         vars=tuple(vars_rec))
        else:
            write_alt()
            add("厂内写参停在写库位置 %s" % _bptxt(bp_rec), None,
                "本次无调试会话 ⇒ 断点观测这一次没做成", crit="③", obs=judge.DEBUG,
                falsify="不做这一次时无从判 —— 本记录不作为固件证据")
            add("Recd_Program 入参非空(pOper 操作者 / pDIs 参数项)", None,
                "本次无调试会话 ⇒ 断点观测这一次没做成", crit="④", obs=judge.DEBUG,
                falsify="不做这一次时无从判 —— 本记录不作为固件证据")
        if r_rec is not None:
            print("      断[A] 记录: ok=%s | %s" % (r_rec.get("ok"), r_rec.get("detail")))
            # ⚠ 不读 `r_rec["hit"]`: judge 的 record 只有七键, `hit` 在 `record()` 里就被拿去定
            #   `ok` 并折进 `detail` 了(5-3 判据② 因此吃过一次假 FAIL)。
            _vals = r_rec.get("vars") or r_rec.get("at_vals") or {}
            print("      停时读到: %s" % _vals)
            recs.append(r_rec)           # crit=None: 判据在下面按同一份读数认领
            hit3 = (r_rec.get("ok") is True)
            add("厂内写参停在写库位置 %s" % _bptxt(bp_rec),
                True if hit3 else (False if w_res.get("v") == "PASS" else None),
                ("停下: %s" % r_rec.get("detail")) if hit3 else
                ("写参被受理(应答=%s)而没停到写库位置 ⇒ 成功写参没落库" % w_res.get("v"))
                if w_res.get("v") == "PASS" else
                ("写参本身没被受理(应答=%s)⇒ 触发没发生, 本条没做成" % w_res.get("v")),
                crit="③", obs=judge.DEBUG,
                falsify="写参被受理而固件不走 `Recd_Program`(或落库前 return)⇒ 不停在 %s" % _bptxt(bp_rec))
            nonnull = (all(nn(v) for v in _vals.values()) if _vals else None)
            add("Recd_Program 入参非空(pOper 操作者 / pDIs 参数项)", nonnull,
                "读到 %s" % (_vals or "没停到, 入参没读到"), crit="④", obs=judge.DEBUG,
                falsify="固件把空操作者/空参数项传进 Recd_Program(丢掉入参)⇒ 两个指针里出现 0")

        # ============ 厂内写之后『结算日编程』口 +1(⑤) ============
        post_alt = read_event_row(ser, PROG_EVENT_SUB, 1, wait=wait)
        trig_ok = (w_res.get("v") == "PASS")
        a3, y3 = event_advanced(post_alt, post_sub, t0)
        add("『结算日编程』口 301A0B0A 厂内写之后序号 +1", a3 if trig_ok else None,
            y3 if trig_ok else
            "厂内写第1结算日=%d 本身没成(应答=%s)⇒ 触发没发生, 本条未证" % (alt, w_res.get("v")),
            crit="⑤",
            falsify="`Recd_PrgCntDay` 也被 b_PrgStart 那套合并语义短路 ⇒ 成功写结算日不新增")
    finally:
        # ---- 复原: 写回 d0 并读回验(见 docstring "表无净变的兜底") ----
        w2, _n2 = write_billday(ser, d0, wait=wait)
        v2 = read_billday(ser, wait=wait)
        ok_back = bool(v2 and v2[1] == d0)
        print("   复原: 写回 d0=%d → %s; 读回=%s ⇒ %s"
              % (d0, w2, (v2[1] if v2 else None), "已复原" if ok_back else "**没复原**, 请人工核"))
        if not ok_back:
            recs.append(rec("结算日写回 d0 未确认", False,
                            "写回应答=%s / 读回=%s —— 表可能留在 alt=%s 号上, 需人工核"
                            % (w2, (v2[1] if v2 else None), alt), obs=judge.SERIAL))

    # ---- 收尾: 把台面交回厂外态(本项起点也是厂外; 需要厂内的那几步已经做完) ----
    exit_factory(ser)

    # ============ 软件标识(⑥; 与 16-2 ①a 同一对象、同一口径) ============
    tsv = read_clock(ser, chip="管理芯", quiet=True)
    sv = sv_read_698_ver(ser, wait=wait)
    add("698 读 0xFF3005(内部软件版本) 正文 == 本机发布标识",
        None if sv is None else (sv == SV_ID),
        ("读到「%s」, 本机发布标识「%s」%s" % (sv, SV_ID, "" if sv == SV_ID else " —— **不等**"))
        if sv is not None else "没读回(或形态对不上两种封装)⇒ 本条没做成",
        crit="⑥",
        falsify="固件返回的版本串与本机发布标识不一致(填错/截断/取自别的区)⇒ 逐字节不等")
    print("   (读版本时表钟=%s)" % tsv)

    n_fail = sum(1 for r in recs if r["ok"] is False)
    n_tbd = sum(1 for r in recs if r["ok"] is None)
    scope = "黑盒+白盒" if have_wb else ("仅黑盒(用户指定)" if wb_waived else "仅黑盒(本次无调试会话)")
    print("===== 16-1 参数设置权限: %d 条证据(FAIL=%d 没做成=%d)  [本次范围: %s] ====="
          % (len(recs), n_fail, n_tbd, scope))
    details = ["%s: %s" % (r["name"], r["detail"]) for r in recs]
    details.append("台面态: 起于厂外 → 厂外写两趟(均被拒) → 回厂内读回 → 厂内写 alt → 复原 d0 → **收尾交回厂外**")
    if not have_wb:
        details.append("未做: 断点观测(白盒) —— %s; 要证『停在哪个拒绝点 / 写库位置走没走到 / 入参是什么』"
                       "需接 J-Link 重跑(断点见 ledger.md 16-1 的 F 列; 断[B] 已由 `:2952` 订正为 `:1548`)"
                       % ("用户本次指定只做黑盒" if wb_waived else "本次无调试会话"))
    details.append("未证: 『记录里的操作者/参数项**内容**』半支 —— 本项只证两个入参非空(与 5-6 ④ 同口径), "
                   "值对不对要一份记录列解码器")
    return recs, details, scope


# ==================== 5-9/5-10『拉闸 / 合闸』: 645 0x1C → Recd_CtrlRelay 落库(2026-09-16 建) ====================
# 判据源 = project/knowledge/_whitebox_ledger/ledger.md:
#   5-9 「观察与判据」= 每次成功拉闸一条, 含操作方式
#   5-10「观察与判据」= 每次成功合闸一条、方式正确
# 两条同源 —— 固件里拉/合共用 `Run_TaskRelay` 一条链, 只在命令帧操作字上分向。故证据函数只写一份,
#   以 `op`(拉/合)为参数; 两条子项各有自己的 `criteria` 函数(条目文案不同), 各自过双向对表。
#
# ---- 触发源 ----
#   645 0x1C 跳合闸(操作字 0x1A=拉闸 / 0x1C=直接合闸), 库动词 `ctrl_relay`。
#
# ---- 固件语义: 这条链上三个读数各证明什么(它决定了条目怎么拆) ----
#   `Run_TaskRelay`(TaskRelay.c:265) 由 main 每主循环调一次, 只在**有消息**时往下走:
#     :278 `Upd_RelayCmd()` —— 先把命令状态落进 RAM 的 `g_RelayCmd[]`(画像 0x20009090)。
#           **这一步在动作许可判定之前** ⇒ 判定闭锁时它照样执行。
#     :282-284 动作许可判定 = `((g_PlcState==0xFFFFFFFF) || (g_PlcState&0xF)==0) && Get_CompFlag(CMP_075Un,4)==0xFF`
#     :352     判定不过 → `g_RelayBlk=TRUE`【闭锁】: 继电器不动作、**不调** Recd_CtrlRelay ⇒ 本事件【不产生】。
#     判定过 → 驱动继电器 → `recd` 按新状态翻转 → 调用点 `Recd_CtrlRelay(&oper[0], recd)`
#   ⇒ `g_RelayCmd[0]` 与那道判定是**两件事**: 前者证"命令进来了、定了向"(即使被闭锁), 后者证"允不允许动"。
#     两者分得开, 才不会把"命令压根没到"错读成"被防误跳闭锁" —— 那正是本项历史上记 TBD 的那条链。
#
# ---- 断点的订正(2026-09-16 离线核对断点 + 读调用点: `gdb-multiarch -ex "info line"`) ----
#   规格 F 列写 `断[B] TaskRelay.c:335(动作参数=拉→写库 Recd_CtrlRelay, def TaskRecord.c:1249)`。两处订正:
#     · `TaskRelay.c:335` 是**调用点**, 读不到被调函数的入参(与 5-6 那条同源: 入参住在被调函数栈帧里);
#     · `TaskRecord.c:1249` 是**函数入口行** —— `info line` 实测它解析到 `Recd_CtrlRelay` 首地址,
#       而该行前后是空行/注释, 那一停可读变量为空。**真写点是 `:1253`**(`id = (ST_SwOff==action)?…`)。
#   ⇒ 断点改订 `TaskRecord.c:1253` → 0x1ea90 `Recd_CtrlRelay+6`; 该停可读 `pOper`/`action`/`buff`/`eveObj`。
#     **一个断点同时给三样**: 调用**发生了**(= 指令路径) + 入参 **pOper 是什么** + **动作方向**(action)。
#   ⚠ `action` 就是拉/合判据, 不是别的: `ptype BOOL` = `enum {FALSE=85, TRUE=170, OTHER=102}`,
#     而该函数头注释逐字写着 `action |__FALSE, 拉闸记录 |__TRUE, 合闸记录`, 函数体第一行据它选记录口
#     (`ST_SwOff == action ⇒ ID_RelayOff`, 即 301F0B0A 那条口)。
#   ⚠ `pOper` **不是本帧带来的**: 调用点前一行是 `Read_ParaData(ID_Operator, &oper[0])`,
#     即固件从**参数区**取存着的操作者代码。故 ④ 只证"非空", 证不了"与本帧操作者一致"。
RELAY_EVENT = P.RELAY_EVENT
RELAY_ACT_TXT = P.RELAY_ACT_TXT
RELAY_CMD_DIR = P.RELAY_CMD_DIR
ST_RELAY_ON = P.ST_RELAY_ON
RELAY_REC_CAP = P.RELAY_REC_CAP



def _relay_criteria(op):
    """5-9/5-10 的预设条目(源 = ledger.md 那两条「观察与判据」, 各拆成六条)。

    ⚠ ④ 只证**入参确实被传进去**(pOper 非空), **不证值对不对** —— 固件取的是**参数区**里存着的
      操作者代码(调用点前一行 `Read_ParaData(ID_Operator, …)`), 不是本帧带来的那个; "与本帧一致"
      这半支要先把参数区写成已知值才谈得上, 本脚本手上没有那个已知值。
    ⚠ ⑤ 只在动作许可判定通过时才成立; 判定闭锁时本事件**按设计不该产生**, 那不算"没满足", 记未证。
    """
    ev = RELAY_EVENT[op]
    oad = _EVENT_REC_OAD.get(_event_code(ev))
    return {
        "①": "645 0x1C %s 受理后继电器命令状态落到本方向: g_RelayCmd[0] %s(AA80 直读)" % (ev, RELAY_CMD_DIR[op]),
        "②": "落库那次 action 指向『%s』: Recd_CtrlRelay 的 BOOL action = %s" % (ev, RELAY_ACT_TXT[op]),
        "③": "写库位置真走到 Recd_CtrlRelay 调用(TaskRecord.c:1253): %s 那一刻停在那儿" % ev,
        "④": "记录里的操作者代码非空(TaskRecord.c:1253 读 pOper; 固件取自参数区 ID_Operator, 非本帧带来)",
        "⑤": "每次成功%s落一条『%s』记录(%s): 判定通过时触发后最新一条的序号/时标推进" % (ev, ev, oad),
        "⑥": "动作许可判定与落库结果一致: 判定通 ⇒ 落库; 判定闭锁 ⇒ 不落库且 g_RelayBlk 置位(DL/T698 防误跳)",
        # 规范「事件记录」那一节的「最近 N 次」: 容量这一档在固件里就是 `NUM_Relay{Off,On}`
        #   = RELAY_REC_CAP(RecdData.h:142-145)。本条判它**封顶在容量上**且顶掉的是最早那条。
        "⑦": "最近 %d 次『%s』封顶在容量上: 连发 %d 回后该口条数停在 %d, 且第 %d 条(最早那条)"
              "被顶掉 —— 其序号不再是连发前那一个" % (RELAY_REC_CAP, ev, RELAY_REC_CAP + 1,
                                                  RELAY_REC_CAP, RELAY_REC_CAP),
    }


def relay_off_criteria():
    """5-9『拉闸』的预设条目。"""
    return _relay_criteria("拉")


def relay_on_criteria():
    """5-10『合闸』的预设条目。"""
    return _relay_criteria("合")


def n8txt(v):
    """AA80 读回的字节 → 打印文本(**读不到就明写"读不到"**, 不拿 0 冒充)。

    ⚠ 不加下划线: 脚本自己读回那个字节时(5-9/5-10 的 `g_RelayCmd[0]` 那一路)要用同一个字形,
      抄一份到脚本里就有了第二处, 两处迟早对不上。
    """
    return "读不到" if v is None else "0x%02X" % v


def gdb_bool(txt):
    """gdb 印出来的**三态布尔** → `True`/`False`; 读不懂(含 `OTHER`/`optimized out`/空) → `None`。

    认的形态: `TRUE`/`FALSE`(DWARF 带枚举名时 gdb 印这个) / `ST_SwOn`/`ST_SwOff` / `0xAA`/`0x55`
    / `170`/`85` / `1`/`0`。**读不懂返 `None`(没做成), 不猜** —— 拿"我看不懂"当"值是假"
      会造出假 FAIL。

    为什么非有不可: 本表 `g_RelaySta` 的类型是 `enum {FALSE=85, TRUE=170, OTHER=102}`
      (TaskRelay.h:30-59), Debug 配置的 DWARF 带着枚举名 ⇒ gdb 印**枚举名**而不是数字。
      ⚠ **不许拿 `gdb_ints` 顶**: `gdb_ints("TRUE")` 返回 `[]`, 而那看起来像"固件没给值"
      —— 同一个坑见 CLAUDE.md 调试链纪律 4(判据直接断言枚举名, 勿硬编码数字)。
    """
    s = str(txt if txt is not None else "").strip()
    if not s or "optimized out" in s or "No symbol" in s:
        return None
    up = s.upper()
    if "TRUE" in up and "FALSE" not in up:
        return True
    if "FALSE" in up:
        return False
    if "SWON" in up and "SWOFF" not in up:
        return True
    if "SWOFF" in up:
        return False
    nums = re.findall(r"0x[0-9a-fA-F]+|\d+", s)
    if not nums:
        return None
    return {0x55: False, 0xAA: True}.get(int(nums[-1], 0))   # ST_SwOff=FALSE=0x55 / ST_SwOn=TRUE=0xAA


def relay_act_ok(txt, op):
    """断点读回的 `action` 文本 → 「方向对不对」→ **三态**(解析交 `gdb_bool`, 这里只比方向)。"""
    got = gdb_bool(txt)
    if got is None:
        return None
    return got == (op == "合")


# ==================== 10-1『费控功能·远程』遥控裁决(2026-09-17 建) ====================
# 判据源 = project/knowledge/_whitebox_ledger/ledger.md 10-1「观察与判据」:
#   判过:  优先级高者生效; 保电期间拉闸被拦且回明确错误(ER_RlyOffKeep); 状态与事件正确
#   判不过: 某组合放行错 → 对照 TAB_RelaySta 逐格核期望裁决
#   ⇒ 前者与后者是**同一把尺子**的两个方向, 故判据① 就把整张表逐格对(见下"逐格转抄")。
#
# ---- 触发源(两条入口, 都汇合点到同一个裁决函数) ----
#   645: CMD_CtrlRelay(DLT645App.c:3428) LEN=0x10, DAT4 = 操作字(TAB_RelayCmdR :3416) → :3504 Set_RelayCmdR
#   698: Action_Control(DLT698App.c:11652) 保电 0x80017F(:11757) / 解除 0x800180(:11772) → :11798 Set_RelayCmdR
#   **本项走 645 那一条** —— 库的 `ctrl_relay_reply` 就是它。698 那条入口本脚本**没有建通道**
#   (本库没有 698 Action 组帧), 未证, 写在本段里(不是判据条目 —— 条目只出规格那一行)。
#
# ---- 裁决层(本项的真对象): TaskRelay.c:818 Set_RelayCmdR ----
#   :823 操作字越界早退;  :828-846 报警(0x2A/0x2B)走**另一支** —— 写参数直接 OK_FRAME, 不经裁决表
#   :848 Upd_RelayCmd() 刷新现态
#   :849 newSta = TAB_RelaySta[cmd][g_RelayCmd[0]-1]        ← **整张表**, 裁决的唯一出处
#   :851 新态是 ST_Error0 或 ≥ST_Error1 ⇒ 按**现态**分派错误码:
#        :855-859 现态 ∈ {ST_AllowOnKp, ST_RelayOnKp}(保电位的两个态) 且 cmd==CMD_RelayOff → ER_RlyOffKeep
#        :861-864 同两个现态收 预跳闸1/2 → ER_AdvOffKeep
#        :870-879 现态 ∈ {5 个拉闸态} 收 预跳闸 → ER_AdvOffOff
#        :881 default → ER_Password
#        :885 return ER_PSWD      ← **三条错路回的是同一个 ST_COM 值**
#   :1026-1045 外置继电器(TP_Ex)的后置改写 —— 本台若不是 TP_Ex 则一格都不生效(判据① 的对表前提)
#   :1075/:1090 只有 newSta != 现态 才写 g_RelayCmd[0]  ← 被拦时命令状态**原地不动**(判据④)
#
# ---- 为什么"回明确错误(ER_RlyOffKeep)"必须拆成两条判据(本项最要紧的一条事实) ----
#   线上**分不出** ER_RlyOffKeep。应答形状由 DLT645Link.c:515-532 定: 错误 ⇒ CMD |= 0xC0(=0xDC)、
#   LEN=0x01、DAT0 = 0x01<<comSta —— 而 `comSta` 是 Set_RelayCmdR 的返回值, **密码判定被拦(:3478)与
#   保电被拦(:859) 都 `return ER_PSWD`(=2)** ⇒ 线上这两处**同形**: DAT0 恒 0x04。
#   (:525 那个"捎带控制状态字"的形态只在 LEN==0x1C 的帧上开 —— 本帧 LEN=0x10, 于是 `back` 恒 0
#    (DLT645Link.c:219 初始化 / :447 只在 LEN==0x1C 时赋) ⇒ 数据域就 1 字节, 装不下状态字。)
#   具体**按哪条规矩**拦的, 只落在 `g_CtrlStat[1]`: Set_CtrlStat(TaskComm.c:1242-1260) 拿 sta-16 作位号,
#   ER_RlyOffKeep=(16+5) ⇒ **bit5 = 0x0020**; ER_Password=(16+2) ⇒ 0x0004。
#   ⇒ 判据② = 线上被拒(0xDC 且 DAT0==0x04);  判据③ = g_CtrlStat[1] 读出 0x0020(不是 0x0004)。
#   ②证"拒了", ③证"按保电那条规矩拒的" —— 不是重复。
#   ⚠ Set_CtrlStat 会**把另一半清零**(sta<16 写 [0] 清 [1], 否则写 [1] 清 [0]) ⇒ 读 [1] 那一半即可,
#     但**被拒之前**该位若已经置着(上一轮跑剩的), 本次就分不出是谁置的 ⇒ ③ 记"没做成"(三态, 不是 FAIL)。
#
# ---- TAB_RelaySta(TaskRelay.c:51-66)逐格转抄 ----
#   行 = cmd(CMD_RelayOff..CMD_OutKeep, TaskRelay.h:9-23 的序号 0..6), 列 = **现态-1**(现态取 ST_Relay* 的
#   枚举值 1..15)。下面按**符号名**转抄成数字, 抄错一格**不会当场报错**, 只会在实跑时把"放行/拦截"判反
#   ⇒ `_keep_checks` 拿源码那一行一行的符号名反查了三条不变量(现态=保电两列、cmd 表的行号、越界防护)。
#   ⚠ 第 8/9 行(报警 0x2A/0x2B)**不在**这张表里(任务表只留 CMD_RemoteNum+2 = 9 行, 后两行另有分支)。
#     本项不测报警 —— 规格判据里没有它。
KP_CMD_OFF = P.KP_CMD_OFF
KP_CMD_INDIR = P.KP_CMD_INDIR
KP_CMD_ON = P.KP_CMD_ON
KP_CMD_OFF1 = P.KP_CMD_OFF1
KP_CMD_OFF2 = P.KP_CMD_OFF2
KP_CMD_KEEP = P.KP_CMD_KEEP
KP_CMD_KEEPOFF = P.KP_CMD_KEEPOFF
KP_REMOTE_NUM = P.KP_REMOTE_NUM
KP_CMD_ROW = P.KP_CMD_ROW
KP_ST_ERROR0 = P.KP_ST_ERROR0
KP_ST_ERROR1 = P.KP_ST_ERROR1
KP_ST_RLYOFFR = P.KP_ST_RLYOFFR
KP_ST_ALLOWON = P.KP_ST_ALLOWON
KP_ST_RELAYON = P.KP_ST_RELAYON
KP_ST_WAITOFFR = P.KP_ST_WAITOFFR
KP_ST_ALLOWONKP = P.KP_ST_ALLOWONKP
KP_ST_RELAYONKP = P.KP_ST_RELAYONKP
KP_ST_RLYOFFL = P.KP_ST_RLYOFFL
KP_KP_STATES = P.KP_KP_STATES
KP_ST_TXT = P.KP_ST_TXT
KP_STA_TAB = P.KP_STA_TAB
KP_ERR_BYTE_PSWD = P.KP_ERR_BYTE_PSWD
KP_CTRLSTAT_RLYKEEP = P.KP_CTRLSTAT_RLYKEEP
KP_STA3_DI = P.KP_STA3_DI
KP_STA3_KP_BIT = P.KP_STA3_KP_BIT
AO_CASH_OVR = P.AO_CASH_OVR
AO_INJ_ASSIGN = P.AO_INJ_ASSIGN
AO_AT_VARS = P.AO_AT_VARS
AO_WATCH_VARS = P.AO_WATCH_VARS



def kp_expect(code, state):
    """(操作字, 现态) → 裁决表算出来的新态(int); 查不到(操作字不在表里/现态不在 1..15) → None。

    ⚠ `state` 是 `g_RelayCmd[0]` 的读数, 而固件是拿它**直接减一当下标**(TaskRelay.c:849) —— 读到 0
      (`ST_Error0`) 时固件自己就是越界读。这里返 None 而不是复现那次越界: 那格的期望值**不可知**,
      记"没做成"才诚实。同理 TP_Ex 台面 :1026-1045 会再改写新态, 本函数算的是**表值**, 不是终值。
    """
    row = KP_CMD_ROW.get(code)
    if row is None or not (1 <= int(state) <= 15):
        return None
    return KP_STA_TAB[row][int(state) - 1]


def kp_reply_kind(cmd, seg=b""):
    """645 0x1C 应答 → "受理"(0x9C) / "被拒"(0xDC) / None(没收到或不是这两种)。

    `seg` 只用于把错误位并进文案; **判"被拒"不看 seg** —— 0xDC 本身就够了(错误位另有专门判据)。
    """
    if cmd == 0x9C:
        return "受理"
    if cmd == 0xDC:
        return "被拒"
    return None


def kp_state_txt(v):
    """`g_RelayCmd[0]` 的读数 → 打印文本(`ST_RelayOnKp(9)` / 明写读不到)。脚本读完之后自己打这一句。"""
    if v is None:
        return "读不到"
    return "%s(%d)" % (KP_ST_TXT.get(v, "?"), v)


def _kp_gdb_u8(txt):
    """gdb 印出来的 **INT8U 标量** → int; 读不懂/读回来不是单个数 → None(三态, 不猜)。

    ⚠ 不吃字符串字面量: `INT8U` 形参 gdb 会写成 `5 '\\005'`, `gdb_ints` 先剥掉那截再抠(它的头注
      讲了为什么) —— 不剥的话八进制转义会被当十进制, 于是 `cmd` 静默解成一个错的行号。
    """
    n = gdb_ints(txt)
    return n[0] if len(n) == 1 else None


def _kp_sta3(ser, wait=2.0):
    """645 读运行状态字3(DI 04000503) → 值字节(bytes) 或 None。判据⑦ 只看它的第二字节 bit4。

    ⚠ 拿到的就是**已去 0x33 的字节**, 直接返回 —— `decode_645_reply` 已经做过那一层
      (`p645.py` 收帧即 -0x33), 再解一次会把 `00 C2` 变成 `CD 8F`, bit4 位跟着翻。
      本台基线实测 `00 C2`(bit4=0, 保电位未置), 与"跑在保电之前"相符。
    """
    val = read_param_di(ser, KP_STA3_DI, wait=wait, quiet=True,
                        head="读运行状态字3(DI %s, 保电位 bit4)" % KP_STA3_DI)
    if not val:
        return None
    return bytes(val)


def _kp_sta3_ok(a_keep, a_free):
    """(保电位下的读数, 解除后的读数) → 保电位 bit 该置的置了、该清的清了 → 三态。"""
    if a_keep is None or a_free is None or len(a_keep) < 2 or len(a_free) < 2:
        return None
    return bool(a_keep[1] & KP_STA3_KP_BIT) and not (a_free[1] & KP_STA3_KP_BIT)


# ---- 10-1 的九步: 一步一个积木, 脚本正文一行一步(顺序即步骤, 见 project/tests/_test_10_1_keep_remote.py) ----
# 与 4-6 同一形状: 判据文字 / crit / falsify 由脚本给(那是本子项的判过汇总, 库不替它起名);
# 本段每个动词只做一件事 —— 一次读 / 一次发(连它那一次读回) / 一次比, 各自打印读过什么、比了什么。
# `fire` = 脚本给的断点观测偏函数(`partial(GD.fire_hit, g)`), 没会话时是 `None`。
#
# ⚠ 白盒那一次的四个断点都是**同一个** `TaskRelay.c:851`(四步全过这一行) ⇒ 断点那条必须拿
#   **当次那一个操作字**去对, 不能只看"停住了"; 这条对照在 `keep_judge_table` 里做。


def kp_ctrlstat_word(body):
    """`W.watch_vars` 读回的 `g_CtrlStat` 原始字节 → `g_CtrlStat[1]`(偏移 2 起 2 字节小端) 或 None。

    读在脚本(`W.watch_vars`)、解在这里。读不到 / 回来太短 → None(不拿 0 冒充 "没置位")。
    """
    if not body or len(body) < 4:
        return None
    return int.from_bytes(body[2:4], "little")


def kp_step_decode(code, res, sta_before, sta_after, ctrl, hit):
    """把**脚本当场读过的那几个数**装配成本段的 `step` —— 纯解码, 一次串口都不碰。

    `sta_before` / `sta_after` = 那两次 `g_RelayCmd[0]` 的读数(int 或 None);
    `ctrl` = `kp_ctrlstat_word` 解出的 `g_CtrlStat[1]`; `res` = `ctrl_relay_reply` 的应答四元组;
    `hit` = 断点那一次的记录(没会话时 None)。返回的形状与 `keep_judge_*` 逐条比的那份一致。
    """
    _code, cmd, seg, _frame = res if res else (None, None, b"", None)
    vals = (hit or {}).get("vars") or {}
    return {
        "code": code, "kind": kp_reply_kind(cmd, seg),
        "dat0": seg[0] if len(seg) >= 1 else None,
        "sta_before": sta_before, "sta_after": sta_after, "ctrl": ctrl,
        "new_sta": _kp_gdb_u8(vals.get("newSta")), "cmd_got": _kp_gdb_u8(vals.get("cmd")),
        "hit": hit,
        "sta_before_txt": kp_state_txt(sta_before), "sta_after_txt": kp_state_txt(sta_after)}


def keep_intro(have_wb, wb_waived=False):
    """第一步 · 开场: 本段在造什么、走哪条入口 —— 只打印, 不判。"""
    print("\n===== 10-1 费控·远程: 645 0x1C 保电 → 保电态下拉闸(应被拦) → 解除 → 拉闸(应放行) =====")
    if not have_wb and not wb_waived:
        print("   !! 本次无调试会话 → 断点观测(白盒)整体**未做**; 本次范围 = 仅黑盒"
              "(对外行为可判, 『裁决算出来的新态是什么』未取证 —— 见 CLAUDE.md「凭什么算「真实测试」·两种观测」)")
    elif not have_wb:
        print("   -- 本次**用户指定**只做黑盒(串口观测) ⇒ 断点观测(白盒)按指定不做(J 列须记明本次范围)")


def keep_goto_factory(ser):
    """第二步 · 进厂内 —— 0x3A/0x3B/0x1A 都不在密码 bypass 里(DLT645App.c:3454-3456)。"""
    enter_factory(ser)


def keep_read_sta3(ser, tag, *, wait=3.0):
    """(保电态 / 解除后)各读一次运行状态字3(645 DI 04000503) → 值字节或 None。"""
    v = _kp_sta3(ser, wait=wait)
    print("   %s: 运行状态字3=%s" % (tag, "读不到" if not v else v.hex(" ").upper()))
    return v


def keep_judge_table(step, expect):
    """判据① · 那一次的双向对表: 断点的 `cmd` 应是查表得出的行, `newSta` 应是那一格的表值。"""
    row = KP_CMD_ROW.get(step["code"])
    if step["new_sta"] is None:
        return None, "断点没停到/没读到 newSta ⇒ 这一格没做成"
    ok = (step["new_sta"] == expect) and (step["cmd_got"] == row)
    return ok, "断点 cmd=%s(应 %s) newSta=%s(表值 %s)" % (
        step["cmd_got"], row, kp_state_txt(step["new_sta"]), kp_state_txt(expect))


def keep_judge_reply(step, want, *, err=None):
    """这一次的对外应答: 该受理的受理了没有 / 该被拒的被拒了没有(给了 `err` 就连错误位一起对)。"""
    k, d = step["kind"], step["dat0"]
    if k is None:
        return None, "应答=无应答"
    ok = (k == want) and (err is None or d == err)
    if d is None:
        return ok, "应答=%s" % k
    return ok, "应答=%s 错误位=0x%02X%s" % (k, d, "(应 0x%02X)" % err if err is not None else "")


def keep_judge_state_in(step, states):
    """这一步之后命令状态落进 `states` 里没有(保电位是两个态, 故收一个集合)。"""
    s = step["sta_after"]
    if s is None:
        return None, "g_RelayCmd[0] 读不到 ⇒ 这一格没做成"
    return s in states, "g_RelayCmd[0]=%s(应 ∈ %s)" % (kp_state_txt(s), [kp_state_txt(v) for v in states])


def keep_judge_state_is(step, expect):
    """这一步之后命令状态**等于** `expect`(裁决表算出来的那一格)没有。"""
    s = step["sta_after"]
    if s is None:
        return None, "g_RelayCmd[0] 读不到 ⇒ 这一格没做成"
    return s == expect, "g_RelayCmd[0] %s→%s(表值 %s)" % (
        step["sta_before_txt"], kp_state_txt(s), kp_state_txt(expect))


def keep_judge_hold(step):
    """这一步命令状态**原地不动**没有 —— 被拒时 `:1090` 的写入口不该被走到。"""
    if step["sta_after"] is None or step["sta_before"] is None:
        return None, "g_RelayCmd[0] 读不到 ⇒ 这一格没做成"
    return step["sta_after"] == step["sta_before"], "g_RelayCmd[0] %s→%s" % (
        step["sta_before_txt"], step["sta_after_txt"])


def keep_judge_ctrlstat(step, base):
    """被拒的**具体原因**是 ER_RlyOffKeep 没有(基线那一位若已置, 本次分不出是谁置的)。"""
    c = step["ctrl"]
    if c is None:
        return None, "g_CtrlStat[1] 读不到 ⇒ 这一格没做成"
    base_txt = "基线 读不到" if base is None else "基线 0x%04X" % base
    tail = " —— 基线该位已置, 本次分不出是谁置的" if base == KP_CTRLSTAT_RLYKEEP else ""
    return (c == KP_CTRLSTAT_RLYKEEP and base != KP_CTRLSTAT_RLYKEEP), \
        "g_CtrlStat[1]=%s(应 0x%04X; %s%s)" % ("0x%04X" % c, KP_CTRLSTAT_RLYKEEP, base_txt, tail)


def keep_judge_release(step, expect):
    """判据⑤ 的正面半支: 应答受理 且 命令状态**跨到拉闸侧**(不是错误态)。

    ⚠ 不判"落的是不是表值那一格" —— 现态在合闸侧时表值算出来是瞬态 `ST_WaitOffR`, 状态机下一拍
      就推进到 `ST_RlyOffR`, 串口那趟读到的是推进过的值; 那一格由判据①在断点上判(两处不重不漏)。
    """
    k, s = step["kind"], step["sta_after"]
    if k is None or s is None:
        return None, "应答=%s / g_RelayCmd[0]=%s ⇒ 这一格没做成" % (k or "无应答", kp_state_txt(s))
    return (k == "受理" and s < KP_ST_RELAYON and s != KP_ST_ERROR0), \
        "应答=%s | g_RelayCmd[0] %s→%s(拉闸侧 = < %s 且非 %s; 裁决那一格表值 %s)" % (
            k, step["sta_before_txt"], kp_state_txt(s), kp_state_txt(KP_ST_RELAYON), kp_state_txt(KP_ST_ERROR0),
            kp_state_txt(expect))


def keep_judge_sta3(a_keep, a_free):
    """保电位在运行状态字3 上该置的置了、该清的清了没有(两趟读数一起比)。"""
    return _kp_sta3_ok(a_keep, a_free), \
        "保电态状态字3=%s(bit4 应置) / 解除后=%s(bit4 应清)" % (
            "读不到" if not a_keep else a_keep.hex(" ").upper(),
            "读不到" if not a_free else a_free.hex(" ").upper())


def keep_end_notes(have_wb, wb_waived=False):
    """收尾把本次够不到的那两块讲明白 —— 只打印, 不记条目(它们不是判据, 是本次范围)。"""
    if not have_wb:
        print("   · 未做: 断点观测(白盒) —— %s; 要证『裁决算出来的 newSta 就是 TAB_RelaySta 那一格、"
              "操作字解到的行号也对』需接 J-Link 重跑(断点 = TaskRelay.c:851, 见脚本 BP_STA)"
              % ("用户本次指定只做黑盒" if wb_waived else "本次无调试会话"))
    print("   · 未证: 698 那条入口(断[A] 的 698 半支, DLT698App.c:11652 → :11798) —— 本库没有 698 Action "
          "组帧, 本次只走 645 0x1C。两条入口汇合点到同一个 Set_RelayCmdR(:3504 / :11798), 故裁决本身的"
          "结论对两条都适用; 『698 那个入口也真能到得了』这一句没证。")


def keep_criteria():
    """10-1『远程』的预设条目(源 = ledger.md 10-1「观察与判据」, 拆成七条)。

    ⚠ ② 与 ③ **不是重复**: 线上应答只装得下"被拒"(0xDC + 错误位 0x04), 而**具体拒在哪条规矩上**只落在
      `g_CtrlStat[1]` 的位上 —— ②证"拒了", ③证"按保电那条规矩拒的"。依据见本段头注。
    ⚠ ⑤ 与 ⑥ 也不是重复: ⑤ 看**命令状态**(g_RelayCmd[0], AA80), ⑥ 看**对外上报**(645 运行状态字3) ——
      两者由不同的代码写(裁决层 :1090 写前者, Get_MeterRunSta :1006 读前者算后者)。
    ⚠ ⑦ 本台证不了: 遥控事件经 `Recd_CtrlRelay` 落库, 而那在 TaskRelay.c:282-284 的**≥75%Un 许可判定**里面
      —— 本台实测 38V 够不到 ⇒ g_RelayBlk=TRUE, 继电器不动、事件按设计不产生(与 5-9/5-10 判据⑤ 同一条理由)。
    """
    return {
        "①": "裁决逐格对表(判过『优先级高者生效』与判不过『某组合放行错』的同一把尺子): "
              "每个组合的 newSta == TAB_RelaySta[cmd][现态-1], 且 cmd 由操作字查表得出",
        "②": "保电期间拉闸**线上被拒**: 645 0x1C 答 0xDC 且错误位 == 0x04(1<<ER_PSWD)",
        "③": "被拒的具体原因是 ER_RlyOffKeep 而不是密码判定: g_CtrlStat[1] == 0x0020(bit5)",
        "④": "被拦时命令状态原地不动: 被拒前后 g_RelayCmd[0] 相同",
        "⑤": "优先级高者生效的**落位**: 保电落到 {ST_AllowOnKp, ST_RelayOnKp}、解除落回普通态、"
              "解除后拉闸被放行 —— 落的是表值, 不是「方向大致对」",
        "⑥": "状态上报正确: 保电位下 645 运行状态字3(DI 04000503)第二字节 bit4 置位, 解除后清零",
        "⑦": {"text": "事件正确: 保电/解除/拉闸各落一条对应事件记录",
              "unprovable": "遥控事件经 Recd_CtrlRelay 落库(TaskRelay.c:335), 而那在 :282-284 的 ≥75%Un "
                            "许可判定里面 ⇒ 本台 38V 够不到, 继电器不动、事件按设计不产生。要证须把台面电压"
                            "加到 ≥75%Un(本台 ≈165V)后重跑 —— 与 5-9/5-10 判据⑤ 同一条台面理由"},
    }


# ==================== 12-1『保电功能·保电/解除』698 Action 通道(2026-09-21 建) ====================
# 判据源 = project/knowledge/_whitebox_ledger/ledger.md 12-1「观察与判据」:
#   判过: 保电期间拉闸被拒且回明确错误; 解除后命令可执行; 保电位正确上报
#
# ---- 与 10-1 的关系: 同一段裁决, 两条不同的入口 ----
#   10-1 走 **645** CMD_CtrlRelay(DLT645App.c:3428 → :3504, 操作字 DAT4 = 0x3A 保电 / 0x3B 解除);
#   12-1 走 **698** Action_Control(DLT698App.c:11652, OMD 0x80017F 保电 / 0x800180 解除 → :11798)。
#   两条入口汇合到**同一个** Set_RelayCmdR(TaskRelay.c:818) ⇒ 裁决层那几条两边同形(兄弟复用);
#   12-1 独有的是**698 这条入口本身** —— 10-1 的积木只建了 645 半支, 它自己在 details 里写着
#   「698 那条入口本库没有建通道, 未证」。本段就是把那半支补上。
#
# ---- 698 Action 那帧长什么样(逐字节, 源 = 本表固件) ----
#   APDU = `07 01 <PIID> <OMD4> <参数数据元素> <时标域>`
#     · Check_TimeTag(DLT698App.c:16411-16437) 从 tag=3 起 +4 跳掉 OMD, 再用 Spread_StructArray
#       量出参数长度 —— 所以**时标域紧跟在参数之后**;
#     · 时标域 = **11 字节** `01 <年2B大端> <月 日 时 分 秒> <TI> <gap2B大端>`(:16476-16483),
#       长度必须恰 `tag + 11 == apduIn`, 否则 DAR_ErrorApdu(253);
#     · 不带时标 = 一个 `00` 字节, 长度必须恰 `tag + 1 == apduIn`。
#
# ⚠ **时标是这条入口的闸, 不是可选装饰** —— Action_Control 里 跳闸(:11682)/保电(:11763)/解除(:11778)
#   每一支都先判 `if (g_TimeTag[0] != 0x01) { DAR = DAR_TimeStamp; break; }`, 于是**不带时标的帧
#   根本到不了 Set_RelayCmdR**。这正是 `p698.build_action_apdu` 原先那个恒定 `00` 尾巴在这几个 OMD
#   上做不出东西的原因(它做冻结没问题: 冻结那一支不判时标)。放行条件只有一条(:16530)
#   `表钟 <= 标签时刻 + gap`, 单向 —— 表钟快过窗口才被拒 ⇒ 时标取**刚读回来的表钟**加
#   `TI=1(分)/gap=1` 的一分钟窗口即可(见 `keep698_timetag`)。
#
# ⚠ **保电/解除在 698 上还多一道 645 上没有的闸**: 两支都先判 `TAB_MeterSty.relay ∈ {TP_In, TP_Ex}`,
#   不是就 `DAR = DAR_RefuseOp(3)` 早退(:11758 / :11773)。645 那条入口没有这道判定
#   (:3452 判的是厂内/密码) ⇒ 本台若 relay 不属于这两种, 698 保电**结构上做不成** ——
#   那是表型/台面事实, 判据③ 会把 DAR 原样记进 detail 而不是记成固件错。
KEEP698_OMD_ON = "80017F00"          # 投入保电(参数::NULL)
KEEP698_OMD_OFF = "80018000"         # 解除保电(参数::NULL)
KEEP698_OMD_DROP = "80008100"        # 遥控跳闸(参数::array<struct{OAD,unsigned,long-unsigned,bool}>)
KEEP698_NULL = bytes([0x00])         # 「参数::NULL」的数据元素 = 一个类型字节
KEEP698_DAR_TIMESTAMP = 32           # DAR_TimeStamp(TaskComm.h)
# 时标被拒时 `Action_Control` 调 `Set_CtrlStat(ER_InvalidTmr)`(:11805)。⚠ 但那一位**落不到 bit4**:
# `Set_CtrlStat`(TaskComm.c:1251-1255)把 `ER_ErrorMAC` 与 `ER_InvalidTmr` **归并成 `ER_Password`**
# 再取位 —— 于是线上拒绝理由从"时间无效"被有意改写成"密码错", g_CtrlStat[1] = 1<<2 = 0x0004。
# 本台实测值就是 0x0004(见 log/), 不是 0x0010; 0x0010 是按 ER_InvalidTmr=16+4 硬推的错值。
KEEP698_CTRLSTAT_TMR = 0x0004


def keep698_drop_param(lag=0, duration=0, auto=0, oad="00000000"):
    """遥控跳闸(0x800081)的参数 —— 照固件 `T_RelayOff`(DLT698App.c:607-615) 逐字节编码。

    `T_RelayOff` = `D_Array,1, D_Struct,4, D_OAD, D_Unsigned, D_LongUnsigned, D_Bool` ⇒ 线上是
    `01 01 | 02 04 | 51 <OAD4> | 11 <告警延时> | 12 <限电时间2B> | 03 <自动合闸>`(共 16B)。
    ⚠ **OAD 那格是 `51` 不是 `09`** —— 类型码 `D_OAD = 81 = 0x51`(DLT698App.c:158 的 `D_` 枚举)。
      `0x09` 是 `D_OctetString`, 在 `Spread_NormalData`(:15893-15937)的 switch 里**没有 case**,
      落到 `default: len = 0`; 于是 APDU 预扫那一步(:16411-16437)算出 `length == 0` 当场
      `return DAR_ErrorApdu(253)`, 帧**根本进不了 Action_Control**, 断点自然一次都不命中。
      实测指纹: 同一条 OMD 换 `51` 之前回 253、换之后 DAR=0 —— 预扫与 OMD 无关、时标字节两帧相同,
      所以 参数是唯一的变量。
    ⚠ **这不是猜的**: Action_Control 直接按 `pInput[14]` / `pInput[16..17]` / `pInput[19]` 取这三个值
      (:11687 / :11690 / :11695)。把上面那串按 pInput[4] 起数一遍, 三个下标**恰好**落在编码好的
      三格里 —— 对得上就是这份布局的凭据;**对不上就取到类型字节**, 于是 lag/duration 读出离谱的值
      或走错分支。所以这条编码本身是**可证伪**的, 不是照着注释抄的。
    `duration == 0` ⇒ `CMD_RelayOff`(直接拉闸); 非 0 且 auto==1 ⇒ 预跳闸1(:11695-11701)。
    """
    o = bytes.fromhex(oad.replace(" ", "")) if isinstance(oad, str) else bytes(oad)
    if len(o) != 4:
        raise ValueError("继电器 OAD 须 4 字节, 收到 %r" % (oad,))
    return (bytes([0x01, 0x01, 0x02, 0x04, 0x51]) + o
            + bytes([0x11, lag & 0xFF])
            + bytes([0x12, (duration >> 8) & 0xFF, duration & 0xFF])
            + bytes([0x03, 1 if auto else 0]))


def keep698_timetag(ser, wait=2.0):
    """时标 = **刚读回来的表钟**, `TI=1(分) / gap=1`(一分钟窗口) → 11 字节; 读不到表钟 → None。

    为什么取表钟而不取本机时间: 窗口是拿**表钟**跟标签时刻比的(:16528 `pGet_Time(EM_Sec)`),
    取本机时间就得先信"两块表钟同步"。读回来多少就写多少, 窗口开一分钟, 一次交换稳稳落在里面。
    """
    ts = read_clock(ser, wait=wait, quiet=True)
    if not ts:
        return None
    return build_timetag698(int(ts[0:4]) - 2000, int(ts[5:7]), int(ts[8:10]),
                            int(ts[11:13]), int(ts[14:16]), int(ts[17:19]), ti=1, gap=1)


def keep698_send(ser, omd, param, timetag=None, wait=3.0, head=""):
    """发一帧 698 Action(管理芯) + 解 `87 01` 应答 → `(dar, ack, frame)`; dar=None = 没收到/认不出。

    `timetag=None` 传下去就是 p698 的"不带时标"形(一字节 `00`), 不是"忘了给" —— 判据③ 要的正是那一次。
    """
    frame = frame_698(build_action_apdu(0x03, omd, param, timetag=timetag))
    rx = send_frame(ser, frame, wait=wait, tag="keep698_" + omd[:6],
                    peer=_chip_name(None), what=head)
    ack = decode_action_ack(rx)
    dar = ack.get("dar") if ack else None
    opout(head, frame, ("DAR=%d(%s)" % (dar, DAR.get(dar, "?"))) if dar is not None
           else "无应答 / 不是 87 01 动作应答", ok=(dar == 0))
    return dar, ack, frame


def keep698_intro(have_wb, wb_waived=False):
    """第一步 · 开场: 本段走哪条入口、本次有没有断点会话 —— 只打印, 不判。"""
    print("\n===== 12-1 保电·解除: 698 Action 0x80017F/0x800180 (裁决锚点 TaskRelay.c:851) =====")
    if not have_wb and not wb_waived:
        print("   !! 本次无调试会话 → 断点观测(白盒)整体**未做**; 本次范围 = 仅黑盒"
              "(对外行为可判, 『裁决算出来的新态是什么』未取证 —— 见 CLAUDE.md「凭什么算「真实测试」·两种观测」)")
    elif not have_wb:
        print("   -- 本次**用户指定**只做黑盒(串口观测) ⇒ 断点观测(白盒)按指定不做(J 列须记明本次范围)")


def keep698_step_decode(omd, op_txt, res, s_before, s_after, ctrl, hit):
    """把**脚本当场读过的那几个数**装配成这一步的 `step` —— 纯解码, 一次串口都不碰。

    `s_before` / `s_after` = 那两次 `g_RelayCmd[0]` 的读数(int 或 None);
    `ctrl` = `kp_ctrlstat_word` 解出的 `g_CtrlStat[1]`; `res` = `keep698_send` 的应答三元组;
    `hit` = 断点那一次的记录(没会话时 None)。
    """
    dar, _ack, _fr = res if res else (None, None, None)
    vals = (hit or {}).get("vars") or {}
    return {
        "omd": omd, "op_txt": op_txt, "dar": dar, "ctrl": ctrl,
        "s_before": s_before, "s_after": s_after,
        "new_sta": _kp_gdb_u8(vals.get("newSta")), "cmd_got": _kp_gdb_u8(vals.get("cmd")),
        "hit": hit,
        "s_before_txt": kp_state_txt(s_before), "s_after_txt": kp_state_txt(s_after)}


def keep698_judge_action(step, cmd_want, expect, *, dar_want=None):
    """判①/②/④/⑥ · 这一帧的结果: 断点解出的行号 == `cmd_want`, 新态落在 `expect`, 线上 DAR 对得上。

    `expect` 给一个值 = 落的就是表值那一格; 给一组 = 落在其中即可(保电位是两态)。
    `dar_want=None` = 这一条不比线上应答(①/② 只判"入口走通"这件事, 线上那半由别的条目管)。
    """
    if step["new_sta"] is None:
        return None, "断点没停到/没读到 newSta ⇒ 这一格没做成"
    want = tuple(expect) if isinstance(expect, (tuple, list, set, frozenset)) else (expect,)
    ok = (step["cmd_got"] == cmd_want) and (step["new_sta"] in want)
    if dar_want is not None:
        ok = ok and (step["dar"] == dar_want)
    return ok, "断点 cmd=%s(应 %s) newSta=%s(应 %s)%s | 线上 DAR=%s | g_RelayCmd[0] %s→%s" % (
        step["cmd_got"], cmd_want, kp_state_txt(step["new_sta"]), [kp_state_txt(v) for v in want],
        "" if dar_want is None else "(应 %s)" % dar_want, step["dar"],
        step["s_before_txt"], step["s_after_txt"])


def keep698_judge_timetag(step, s_pre, dar_want=KEEP698_DAR_TIMESTAMP,
                          ctrl_want=KEEP698_CTRLSTAT_TMR):
    """判③ · 这条入口的时标闸在起作用: 不带时标那帧应回 `DAR_TimeStamp`, 控制状态字落下原因位,
    且命令状态原地不动(闸把它挡在裁决层之前, `:11763` 的时标判定在做 `Set_RelayCmdR` 之前)。
    """
    if step["dar"] is None:
        return None, "不带时标那帧没收到认得出的应答 ⇒ 这一次没做成"
    ok = (step["dar"] == dar_want) and (step["ctrl"] == ctrl_want) \
        and (step["s_after"] == s_pre) and (s_pre is not None)
    return ok, "DAR=%s | g_CtrlStat[1]=%s(应 0x%04X) | g_RelayCmd[0] %s→%s" % (
        step["dar"], "读不到" if step["ctrl"] is None else "0x%04X" % step["ctrl"], ctrl_want,
        step["s_before_txt"], step["s_after_txt"])


def keep698_end_notes(have_wb, wb_waived=False):
    """收尾把本次够不到的那两块讲明白 —— 只打印, 不记条目(它们不是判据, 是本次范围)。"""
    if not have_wb:
        print("   · 未做: 断点观测(白盒) —— %s; 要证『OMD 解出来的就是那一个 cmd、裁决算出来的 "
              "newSta 就是 TAB_RelaySta 那一格』需接探针重跑(断点 = TaskRelay.c:851, 见脚本 BP_STA)"
              % ("用户本次指定只做黑盒" if wb_waived else "本次无调试会话"))
    print("   · 本次走的是 **698 Action** 那条入口(DLT698App.c:11652 → :11798); 645 那条入口"
          "(DLT645App.c:3428 → :3504)是 10-1 的地盘。两条入口汇合到同一个 Set_RelayCmdR, "
          "裁决层那几条两处同形 —— 本条只证 698 这一条真能到得了。")


def keep698_criteria():
    """12-1『保电功能·保电/解除』的预设条目(源 = ledger.md 12-1「观察与判据」, 拆成八条)。

    ⚠ ①/②/③ 是**本条独有**的: 它们证的只是"698 这条入口", 而 10-1 的积木只建了 645 半支
      (10-1 自己的 details 里写着 698 那条未证)。④⑤⑥⑦ 与 10-1 的 ②③⑤⑥ 同形 ——
      同一段裁决代码(TaskRelay.c:818)的两个触发器, 兄弟复用, 不是重测。
    ⚠ ④ 与 ⑤ **不是重复**: 线上应答只装得下"被拒"(DAR_RefuseOp), 而**具体拒在哪条规矩上**只落在
      `g_CtrlStat[1]` 的位上 —— ④证"拒了", ⑤证"按保电那条规矩拒的"。依据见 10-1 段头注。
    ⚠ ④/⑥ 的 `newSta` 与 `cmd` 都读自同一个断点(`TaskRelay.c:851`) —— 那是 `Set_RelayCmdR` 里
      `cmd` 与 `newSta` **唯一**同时读得到的点(`info scope` 核过; `:859` 两者都在空洞里)。
    ⚠ ⑧ 本台证不了: 遥控事件经 `Recd_CtrlRelay` 落库, 而那在 TaskRelay.c:282-284 的 **≥75%Un 许可判定**
      里面 —— 本台实测 38V 够不到 ⇒ 继电器不动、事件按设计不产生(与 10-1 判据⑦ 同一条台面理由)。
    """
    return {
        "①": "698 保电入口走通: Action 0x80017F 走到裁决层 —— 停 TaskRelay.c:851 且 "
              "cmd == CMD_InKeep(5)、newSta ∈ {ST_AllowOnKp, ST_RelayOnKp}",
        "②": "698 解除入口走通: Action 0x800180 在同一处 cmd == CMD_OutKeep(6), "
              "newSta 落回普通合闸态(表第 7 行 6/8)",
        "③": "698 这条入口的时标闸在起作用: 同一 OMD **不带时标** ⇒ 线上 DAR_TimeStamp(32)、"
              "g_CtrlStat[1] == 0x0004(ER_InvalidTmr 在 Set_CtrlStat 里被归并成 ER_Password 那位)、"
              "命令状态原地不动",
        "④": "保电期间拉闸**(698 0x800081)** 被拒: 线上 DAR_RefuseOp(3), 裁决定在 newSta == ST_Error0, "
              "操作字解到拉闸那一行(cmd == CMD_RelayOff(0) = TAB_RelaySta 第 1 行)",
        "⑤": "被拒的具体原因是 ER_RlyOffKeep 而不是密码判定: g_CtrlStat[1] == 0x0020(bit5)",
        "⑥": "解除后拉闸放行: 线上 DAR_Success(0) 且裁决定在表值上(TAB_RelaySta[拉闸][解除后现态-1])",
        "⑦": "保电位正确上报: 保电态下 645 运行状态字3(DI 04000503)第二字节 bit4 置位, 解除后清零",
        "⑧": {"text": "事件正确: 保电/解除/拉闸各落一条对应事件记录",
              "unprovable": "遥控事件经 Recd_CtrlRelay 落库(TaskRelay.c:335), 而那在 :282-284 的 ≥75%Un "
                            "许可判定里面 ⇒ 本台 38V 够不到, 继电器不动、事件按设计不产生。要证须把台面电压"
                            "加到 ≥75%Un(本台 ≈165V)后重跑 —— 与 5-9/5-10 判据⑤、10-1 判据⑦ 同一条台面理由"},
    }


# ==================== 12-2『保电功能·液晶是否显示拉闸』判据与证据(2026-09-22 建) ====================
# 判据源 = project/knowledge/_whitebox_ledger/ledger.md 12-2「观察与判据」(= F/G/H/I 四列):
#   命令态为"非合闸类"时液晶上显“拉闸”两字; 与实际液晶目测一致。
#
# ---- 判据链(源码; 行号逐条离线核过 `info line` / `info scope`) ----
#   TaskDisplay.c:699   Run_TaskDisplay 收到 MSG_SecStep(每秒一次)整块重画:
#                       `Clear_DispBuf()` → `Disp_Others()` → `ST75263S_Refresh()`。
#                       这条路径上**没有** g_LampTimer 判据(那条只在 :919 Run_StopDisp 停电环里)
#                       ⇒ 本台带电, 影子缓冲每秒被重画一次, AA80 读到的就是最新一帧。
#   TaskDisplay.c:3824  Disp_Others 的总闸: `if (Is_StopDispStatus() == FALSE) return;`
#   TaskDisplay.c:3961  Is_StopDispStatus() 判的是 g_DispStatus(画像已登记该变量); 名字与语义相反 ——
#                       它真 = 处于正常显示窗口(`>ST_FullDisp(0) && <ST_StopDisp(7)` = 1..6),
#                       故 :3824 那句的真意是"不在 1..6 就不画"
#   TaskDisplay.c:3867  `if (Get_VoltStatus() == TRUE) {`
#   TaskDisplay.c:3869    `if (Get_RelayCmd() < ST_RelayOn) {`      ← **本条判据的那道闸**
#   TaskDisplay.c:3871      `Curr_Value = Status_Zone_Offset[6];`
#   TaskDisplay.c:3872      `Fill_La__Zha_(Curr_Value);`
#   TaskDisplay.c:3876/77   `Curr_Value = Status_Zone_Offset[7]; Fill_Jie_Ti_x(Curr_Value, 0);`
#                       ⚠ :3877 **不在** :3867 那个 volt 块里(缩进与 :3867 同级, `cat -A` 核过)
#                         —— 它无条件画, 故拿它当**对照区**(理由见下面那组常量的注)。
#   TaskDisplay.c:3817  `Status_Zone_Offset[9] = {0, 9, 19, 28, 37, 49, 85, 109, 193}`
#                       ⇒ [6] = 85(“拉闸”左端) / [7] = 109(“阶梯T1”左端)
#   LCDcode.h:192       `Fill_La__Zha_(n)`  = DrawChar(n,68,3,…) + DrawChar(n+12,68,4,…) 两块 12×12
#   LCDcode.h:197       `Fill_Jie_Ti_x(n,m)` = DrawChar(n,68,m,Font_Status_Zone_Ladder_Tx_8x12P,…) 一块 8×12
#   ST75263S.c:9        `INT8U lcd_buffer[LCD_BUFFER_SIZE]`  (LCD_WIDTH 208 / LCD_PAGES 10)
#   ST75263S.c(~490)    DrawPixel: `page = y/8; bit = y%8; addr = page*208 + x;` 置位/清位
#   TaskRelay.c:51-66   TAB_RelaySta[命令行][现态-1] —— 阶梯每一步的落点由它算(库内走 `kp_expect`)
#   DLT645App.c:3416    TAB_RelayCmdR = {0x1A,0x1B,0x1C,0x1D,0x1E,0x3A,0x3B,0x2A,0x2B}
#
# ⚠ **拉闸那一步的落点钉不住, 只能钉谓词** —— TaskRelay.c:887-920: 收到拉闸命令时 newSta 先落
#   ST_WaitOffR(10); `g_RelayTmr = u16Lag_Cutoff`(= ID_SwOffDly, 读不到时 TAB_SwOffDly[0]) 落在
#   1..9999 就停在 10, 否则当场翻 ST_RlyOffR(1); 停在 10 的再由 :577 的状态机倒计时翻到 1。
#   1 与 10 都满足那道显示闸(`< ST_RelayOn`), 所以判据不关心是哪一个。
# ⚠ 但**"非合闸"≠"≠合闸"**: :3869 是个纯数值比较 `Get_RelayCmd() < ST_RelayOn(8)`, 而
#   ST_WaitOffR = 10 > 8 ⇒ 停在 10 的那一瞬**不画**“拉闸”。故阶梯每一步发完都要**轮询到落进谓词**
#   再读数, 不能发完就立刻读(否则读到的可能是 10, 会误判成"固件不画")。
#
# ---- 影子缓冲里那两块窗口(全部由下面的常量算出, 一个数都不写死) ----
#   y=68 ⇒ 落在 page 8 与 page 9; “拉闸” n=85 ⇒ x 85..108(24B);
#   “阶梯T1” n=109 ⇒ x 109..116(8B)。窗口 = (起点 85, 长 32), 前 24B = 拉闸区, 后 8B = 对照区。
#   ⚠ 字是 12 像素高、y=68 起, **跨 page 8/9 两页**, 只读一页会漏掉一半笔画 —— 故两页都读。
#   ⚠ page 8 按整字节读会把 **y64..67 也读进来**: DrawPixel 是 `page=y/8; bit=y%8`,
#     所以 page 8 覆盖 y64..71, 而这两个字只占 y68..79(= page 8 的高 4 位 + page 9 全 8 位)。
#     y64 恰好是主数字行(`Decode_MainLine` 的 24×48 大字, y=17..64)的最下一行, 它随屏上
#     数字变 —— 不掩掉的话, “拉闸字各态逐字节相同”会被这行像素顶成假 FAIL。故 page 8
#     那一页的字节一律过 LCD_P0_MASK。⚠ 这是**把不属于该字形的像素剔出去**, 不是放宽判据。
#   ⚠ 对照区是**反静默**用的: 它无条件画, 所以它全 0 = 整条显示路径没跑(或 :3877 被去掉),
#     而不是"没画拉闸" —— 只看拉闸区时, 这两种在账本里长得一模一样。
LCD_WIDTH_PX = 208             # ST75263S.c 的 LCD_WIDTH(一页的字节数)
LCD_PAGE_H   = 8               # 一页几位
LCD_P0_MASK  = 0xF0            # page 8 上只有高 4 位(y68..71)属于这两个字; 低 4 位是 y64..67
LCD_STATUS_Y = 68              # Fill_La__Zha_ / Fill_Jie_Ti_x 的 y
LCD_LZ_X     = 85              # Status_Zone_Offset[6]
LCD_LZ_W     = 24              # 两块 12×12
LCD_LADDER_X = 109             # Status_Zone_Offset[7]
LCD_LADDER_W = 8               # 一块 8×12
LCD_WIN = tuple(((LCD_STATUS_Y // LCD_PAGE_H + p) * LCD_WIDTH_PX + LCD_LZ_X,
                 LCD_LZ_W + LCD_LADDER_W)
                for p in (0, 1))                     # → ((1749, 32), (1957, 32))


def lcd_window_blocks(tag="lcd"):
    """影子缓冲那两块窗口 → `[(名, 绝对地址, 长度)]` 供 `W.aa80_ram_snapshots`(**纯规划, 不碰串口**)。

    ⚠ 地址一律现算: 基址取画像 `RAM_VARS["lcd_buffer"]`(单一事实源), 块内偏移由上面那组常量算出
      —— 别在这儿写死 `0x200090E0` 或 `1749`。AA80 那条路的**区内偏移**折算在 `aa80_ram_snapshots` 里做。
    基址解析不到 → 空表(读只读得到空表, 由 `lcd_window_obs` 记成"读不到")。
    """
    rec_ = watch._pvar_addr("lcd_buffer")
    if not rec_:
        return []
    return [("lcd_%s_p%d" % (tag, i), rec_[0] + LCD_WIN[i][0], LCD_WIN[i][1]) for i in (0, 1)]


def lcd_window_obs(got, state, tag="lcd"):
    """`W.aa80_ram_snapshots` 读回的两页 → 本态的观察点 `{"state", "tag", "lz", "ladder"}`。

    拉闸区 = 两页各前 24B 拼起来(48B); 对照区 = 两页各后 8B 拼起来(16B)。取两页是因为字跨页(见上)。
    page 8 那一页的字节过 `LCD_P0_MASK`, 只留字真正占的高 4 位(理由见上面那段 ⚠)。
    读不到的那一块给 `None`(不是空字节串) —— 判据那边靠 `is None` 分"没读到"与"读到全 0",
    这两件事结论相反(见 `lcd_judge_lz_low` 的 falsify)。
    """
    lz, ladder = b"", b""
    for pg in (0, 1):
        b = (got or {}).get("lcd_%s_p%d" % (tag, pg))
        if b is None or len(b) < LCD_LZ_W + LCD_LADDER_W:
            lz, ladder = None, None
            break
        if pg == 0:
            b = bytes(v & LCD_P0_MASK for v in b)   # 剔掉 y64..67(主数字行), 见上面那段 ⚠
        lz += b[:LCD_LZ_W]
        ladder += b[LCD_LZ_W:LCD_LZ_W + LCD_LADDER_W]
    print("   影子缓冲[%s]: 拉闸区=%s | 对照区=%s"
          % (tag, lz.hex(" ").upper() if lz else "(读不到)",
             ladder.hex(" ").upper() if ladder else "(读不到)"))
    return {"state": state, "tag": tag, "lz": lz, "ladder": ladder}


def lcd_relay_criteria():
    """12-2『保电功能·液晶是否显示拉闸』的预设条目(源 = ledger.md 12-2「观察与判据」)。库内单点。

    ⚠ ⑦ 本台证不了: 判据要的是"**实际液晶**上看见“拉闸”", 而四条通路读的都是内存里的影子缓冲
      (`lcd_buffer`) —— 那是**驱动液晶的那一份数据**, 不是玻璃上真出现的像素(驱动断线/背光坏/段码
      没接, 影子缓冲照样是对的)。要证须有人在台边目测, 或接液晶驱动输出做电气观测。
      故写 `unprovable`, 整项据此记未定论 —— 不是 `_suite.py` 的`台面`项: 本项其余七条都做得出,
      按硬性规矩 27「只有半段够不到就仍记`可跑`」。
    """
    return {
        "①": "五态阶梯逐级到位: 645 0x1C 的 0x1A/0x1B/0x3A/0x1C/0x3B 各发一次, "
              "g_RelayCmd[0] 依次落进 非合闸侧→ST_AllowOn(6)→ST_AllowOnKp(7)→ST_RelayOnKp(9)→ST_RelayOn(8)",
        "②": "非合闸态(6/7 与拉闸侧) `TaskDisplay.c:3872` **被执行**, 且那一刻 `Curr_Value == "
              "Status_Zone_Offset[6]`(= 85, “拉闸”两字的左端)",
        "③": "合闸态(8/9) `TaskDisplay.c:3872` **不**被执行(否定期望) —— 阳性对照是 ②",
        "④a": "影子缓冲拉闸区(两页各前 24B, 共 48B; 页0 只取高 4 位)在**非合闸态**非全 0 且各态**逐字节相同**",
        "④b": "影子缓冲拉闸区在**合闸态**(8/9)该 48B **全 0**",
        "⑤": "对照区(两页各后 8B, 共 16B, “阶梯T1”那块 8×12 —— 它无条件画)在每一个观察态都非全 0 "
              "⇒ 显示路径整条在跑, ④ 的『全 0』不是「没画」与「没跑」混在一起",
        "⑥": "合闸允许态(6/7) `Get_RelayTimer()` 走到 `TaskDisplay.c:1461 return b_Blink`(拉闸灯闪) —— "
              "`g_DispStatus` 不在停显区间内时该支才到得了",
        "⑦": {"text": "实际液晶屏上目测到与影子缓冲一致的“拉闸”显示",
              "unprovable": "四条通路读的都是内存里的影子缓冲, 那是驱动液晶的那份数据, 不是玻璃上的像素 —— "
                            "驱动断线/背光坏/段码没接时影子缓冲照样对。要证须台边目测或接液晶驱动输出做电气观测"},
    }


# ---- 12-2 的六步: 一步一个积木, 脚本正文一行一步(顺序即步骤) ----
# 每步只做一件事: 开场说明 / 读显示状态机前置 / 归一现态 / 阶梯一步 / 读影子缓冲窗口 /
# 若干条判定。判据文字、crit、falsify 都写在脚本里(见 project/tests/_test_12_2_lcd_relay.py);
# 下面每个函数只说自己那一步做什么、为什么这么做。

def lcd_relay_intro(have_wb, wb_waived=False):
    """第一步 · 开场: 本次走哪两种观测 —— 只打印, 不判。"""
    print("\n===== 12-2 液晶是否显示拉闸: 645 0x1C 五态 × 影子缓冲(AA80) + 断点 =====")
    if not have_wb and not wb_waived:
        print("   !! 本次无调试会话 → 断点观测(白盒)整体**未做**; 本次范围 = 仅黑盒"
              "(影子缓冲可判『画没画』, 判不了『:3872 那一行执行没执行』与『画的哪个偏移』"
              " —— 见 CLAUDE.md「凭什么算「真实测试」·两种观测」)")
    elif not have_wb:
        print("   -- 本次**用户指定**只做黑盒(串口观测) ⇒ 断点观测(白盒)按指定不做(J 列须记明本次范围)")


def lcd_disp_decode(disp):
    """第二步 · 前置(纯解码): 脚本读回的 `g_DispStatus` → `(那个值, 在运行窗口里没)`。

    `TaskDisplay.c:3824` `if (Is_StopDispStatus() == FALSE) return;`, 而 `:3961 Is_StopDispStatus()`
    判的是 `g_DispStatus > ST_FullDisp(0) && < ST_StopDisp(7)` —— **函数名与语义相反**: 它返回真
    = 当前处于正常显示窗口(轮显/按显/固定/点播/插卡/金额 = 1..6)。0(上电全显)与 7(停显) 两支
    都直接 return, 影子缓冲不会更新。

    ⚠ 读不到、或不在 1..6 时调用点**必须中止**: 那时整条显示路径不跑, 再往下读全是全 0,
      会被读成"固件不画拉闸"。本函数只回事实, 中止与否由脚本定。
    """
    print("   前置: g_DispStatus=%s(0全显/1轮显/2按显/3固定/4点播/5插卡/6金额/7停显/8测试)" % n8txt(disp))
    if disp is None:
        print("   !! g_DispStatus 读不到 ⇒ 无法确认显示路径在跑, 本项本次**没做成**(不是固件不对)")
        return None, False
    if not (1 <= disp <= 6):
        print("   !! g_DispStatus=%d 不在 Disp_Others 的运行窗口(1..6)⇒ 它一进门就 return,"
              " 影子缓冲不会更新; 本项本次**没做成** —— 先让表进到轮显/按显一类的正常显示态再跑" % disp)
        return disp, False
    return disp, True


# 非合闸侧 = 现态 `Get_RelayCmd() < ST_RelayOn(8)`: 这一侧才该画"拉闸"(裁决表那一行与显示闸同界)
LCD_LOW_STATES = tuple(range(ST_RELAY_ON))


def lcd_norm_needed(st0):
    """第三步 · 归一(纯解码): 脚本读回的起点 → 要不要先解除保电再往下走。

    为什么: 起点在 `ST_RelayOnKp(9)` 时拉闸会被 `TAB_RelaySta` 第 1 行第 9 列判成 `ST_Error0`
    (**拒**, 状态不动), 阶梯第一级就走不下去; 起点在 8/10..15 时拉闸直接有效, 不动它。
    """
    print("\n[阶梯] 起点 g_RelayCmd[0]=%s" % n8txt(st0))
    return st0 == KP_ST_RELAYONKP


def lcd_ladder_decode(tag, op, verdict, st, want_states, settle=8.0):
    """第四步 · 阶梯一步(**纯解码**): 脚本发过帧、也轮询过了 → 这一步到位没到位。

    返回 `(现态, ok, 详情)`: `ok is True` = 到位, `ok is None` = **没到位**(这一态的判据本次不做;
    ⚠ 不是 FAIL —— 落不进目标态是台面/前置没打通, 与"固件不画拉闸"是两件事)。
    `want_states` 给一组而不是一个数: 拉闸那一步先落 ST_WaitOffR(10) 再倒计时翻 1, 而这两态在
    显示闸上结论相反 ⇒ 它要的是"落进非合闸侧这一整片"。
    """
    print("   · %s: 操作字 0x%02X 应答=%s → g_RelayCmd[0]=%s%s"
          % (tag, op, verdict, n8txt(st),
             "" if (st is not None and st in want_states) else "(未落进目标态, 至多等 %.0fs)" % settle))
    if st is not None and st in want_states:
        why = "g_RelayCmd[0]=%s(%s)" % (n8txt(st), KP_ST_TXT.get(st, "?"))
        print("   [PASS] 阶梯·%s 到位 —— %s" % (tag, why))
        return st, True, why
    why = ("操作字 0x%02X 应答=%s, 但 g_RelayCmd[0]=%s 没落进目标态(至多等 %.0fs) ⇒ 这一态的判据本次不做"
           % (op, verdict, n8txt(st), settle))
    print("   [TBD] 阶梯·%s 到位 —— %s" % (tag, why))
    return st, None, why


def lcd_probe_draw(fire, nohit, trig, bp, vars_, st, crit, crit_nohit, falsify, falsify_nohit,
                   settle=8.0):
    """第五步之一 · 断[A] 一次: 非合闸态读 `Curr_Value`(判"该画"), 合闸态等"不该被走到"(否定期望)。

    `trig` = 白盒那一次的**触发**动作(由脚本给, 它就是脚本里那一行再读一次 `g_RelayCmd[0]`):
    对 `fire_hit` 而言它是"让核跑起来、跑到断点停住"的那一下(本态的帧在阶梯里已发过, 这里只是
    重新给核一个动作 —— 显示路径每秒走一趟, 所以命中的是下一个秒沿); 对 `expect_no_hit` 而言
    它顺带叫一次 `ensure_running()`, 免得核被上次残留撂停时"窗口内没命中"变成一次**假通过**。

    返回记录表(0 或 1 条)。没会话时给一条 `ok=None` 的记录(只进日志, 不认领条目) —— 这一半没做成。
    ⚠ 递**断点元组**而不是已挂好的 bpno: `fire_hit` / `expect_no_hit` 见到元组都自己挂、命中与
      没命中**两条路都撤**(传 bpno 则撤不撤只由 `drop=` 管, 没命中就留在槽里 —— 那是"核被自己
      撂停、其后串口全哑"的来源)。
    """
    if fire is None or nohit is None:
        return [rec("断[A] %s %s" % (_bptxt(bp), "不该被走到" if st >= ST_RELAY_ON else "读 Curr_Value"),
                    None, "没有调试会话 ⇒ 这一半不做", obs=judge.DEBUG)]
    if st >= ST_RELAY_ON:
        r = nohit(bp, settle + 2.0, label="12-2 断[A] %s 不该被走到" % _bptxt(bp),
                  crit=crit_nohit, trigger=trig, trigger_args=(), vars=tuple(vars_),
                  falsify=falsify_nohit)
    else:
        r = fire(bp, trig, label="12-2 断[A] %s(读 Curr_Value)" % _bptxt(bp),
                 timeout=settle + 2.0, vars=tuple(vars_), crit=crit, falsify=falsify)
    if r is not None:
        print("      断[A] 记录: ok=%s | %s" % (r.get("ok"), r.get("detail")))
        print("      停时读到: %s" % (r.get("vars") or {}))
    return [r] if r is not None else []


def lcd_probe_blink(fire, trig, bp, vars_, st, crit, falsify, settle=8.0):
    """第五步之二 · 断[B] 一次: 只有合闸允许态(6/7)才该走到 `:1461` 那个 `return b_Blink`。

    `trig` 同 `lcd_probe_draw`(脚本给)。
    别的态返回**空表** —— 这一条判的就是"合闸允许态才走到那一行", 在别的态上探它答不出任何事。
    没会话时给一条 `ok=None` 的记录(只进日志)。
    """
    if st not in (KP_ST_ALLOWON, KP_ST_ALLOWONKP):
        return []
    if fire is None:
        return [rec("断[B] %s 读 b_Blink" % _bptxt(bp), None, "没有调试会话 ⇒ 这一半不做",
                    obs=judge.DEBUG)]
    r = fire(bp, trig, label="12-2 断[B] %s(读 b_Blink)" % _bptxt(bp),
             timeout=settle + 2.0, vars=tuple(vars_), crit=crit, falsify=falsify)
    if r is not None:
        print("      断[B] 记录: ok=%s | %s" % (r.get("ok"), r.get("detail")))
    return [r] if r is not None else []


def lcd_judge_ladder(landed, missed):
    """判① · 五态阶梯**全**到位才算满足 → `(ok, 详情)`。

    ⚠ 整条**只记一次**, 不逐级各记一条 `crit="①"`: `judge.crit_states` 的判据是「至少一条认领记录
      ok=True 且没有 ok=False」, 逐级记的话"到了 4 级、差 1 级"照样算满足。差的那一级记 `ok=None`
      (不是 FAIL —— 落不进目标态是台面/前置没打通)。
    """
    if not missed:
        return True, "五级全到位: %s" % "/".join(landed)
    return None, ("差 %d 级没到位(%s), 已到位: %s ⇒ 没到位的那些态本次不取证"
                  % (len(missed), "/".join(missed), "/".join(landed) or "无"))


def _lcd_sides(obs):
    """把观察点按现态分成 (`非合闸侧`, `合闸侧`) —— 两半各自只看**读到了窗口**的那些点。"""
    got = [o for o in obs if o["lz"] is not None and o["ladder"] is not None]
    return ([o for o in got if o["state"] < ST_RELAY_ON],
            [o for o in got if o["state"] >= ST_RELAY_ON])


def lcd_judge_lz_low(obs):
    """判④a · 非合闸态: 拉闸区非全 0 且各态逐字节相同 → `(ok, 详情)`; 没有这样的观察点 → 没做成。"""
    low, _high = _lcd_sides(obs)
    if not low:
        return None, "本次没有落到非合闸态的观察点(或窗口读不到) ⇒ 这一条没做成"
    nz = all(any(o["lz"]) for o in low)
    same = all(o["lz"] == low[0]["lz"] for o in low)
    return (nz and same), ("非全 0=%s; 逐字节相同=%s(基准=%s)"
                           % (nz, same, low[0]["lz"].hex(" ").upper()))


def lcd_judge_lz_high(obs):
    """判④b · 合闸态(8/9): 拉闸区 48B **全 0** → `(ok, 详情)`; 没有这样的观察点 → 没做成。"""
    _low, high = _lcd_sides(obs)
    if not high:
        return None, "本次没有落到合闸态的观察点(或窗口读不到) ⇒ 这一条没做成"
    all0 = all(not any(o["lz"]) for o in high)
    return all0, "; ".join("%s=%s" % (o["tag"], "全 0" if not any(o["lz"]) else o["lz"].hex(" ").upper())
                           for o in high)


def lcd_judge_ctrl(obs):
    """判⑤ · 对照区("阶梯T1"那块 8×12, 它**无条件**画)在每一个观察态都非全 0 → `(ok, 详情)`。

    反静默用的: 它全 0 ⇒ 整条显示路径没跑(或 `:3877` 被去掉), 而不是"没画拉闸" —— 只看拉闸区时,
    这两种在账本里长得一模一样。所以 ④b 的『全 0』要这一条成立才作数。
    """
    _low, _high = _lcd_sides(obs)
    got = [o for o in obs if o["lz"] is not None and o["ladder"] is not None]
    if not got:
        return None, "本次一个窗口都没读成 ⇒ 这一条没做成"
    nz_all = all(any(o["ladder"]) for o in got)
    return nz_all, "; ".join("%s=%s" % (o["tag"], "非全 0" if any(o["ladder"]) else "**全 0**") for o in got)


def lcd_relay_end_notes(have_wb, wb_waived=False):
    """收尾: 本次够不到的那两块讲明白 —— 只打印, 不记条目(它们不是判据, 是本次范围)。"""
    if not have_wb:
        print("   · 未做: 断点观测(白盒) —— %s; 要证『:3872 那一行执行没执行』与『画的哪个偏移』"
              "需接 J-Link 重跑(断点 = TaskDisplay.c:3872, 见脚本 BP_DRAW)"
              % ("用户本次指定只做黑盒" if wb_waived else "本次无调试会话"))
    print("   · 未证: ⑦『实际液晶屏上目测到与影子缓冲一致的“拉闸”显示』—— 四条通路读的都是内存里的"
          "影子缓冲(驱动液晶的那份数据), 不是玻璃上的像素; 驱动断线/背光坏/段码没接时影子缓冲照样对。"
          "要证须台边目测或接液晶驱动输出做电气观测。")


# ==================== 12-3『保电功能·解除后本地费控根据剩余电费决定是否执行拉闸』判据与证据 ====================
# 判据源 = project/knowledge/_whitebox_ledger/ledger.md 12-3「观察与判据」(= F/G/H/I 四列):
#   本地费控 —— 解除保电且余额低于透支门限 ⇒ 不等主站再发命令, 自己续拉;
#   远程费控 —— 解除保电后续用电, 需主站再发拉闸。
#
# ---- 源码现场(Application\TaskRelay.c, 函数 Set_RelayCmdR; 行号逐条核过 `info line`) ----
#   :848  Upd_RelayCmd();
#   :849  newSta = TAB_RelaySta[cmd][g_RelayCmd[0]-1];   ← 裁决表算出的新态(七行见 KP_STA_TAB)
#   :851  if (newSta == ST_Error0 || newSta >= ST_Error1) ⇒ :885 return ER_PSWD(非法转移当场拒)
#   :1009 else if ((TAB_MeterSty.style == TP_Local)      ← 条件一: 本地表(**编译期常量**)
#   :1010     && (cmd == CMD_OutKeep))                   ← 条件二: 收到的是"解除保电"
#   :1011     && (Get_CashStatus() == ST_OvrCash2))      ← 条件三: 低于透支金额门限
#   :1013   if ((TAB_MeterSty.relay == TP_In) && (g_RelayCmd[0] == ST_AllowOnKp)) → :1016 newSta = ST_RlyOffL;
#   :1018   if (g_RelayCmd[0] == ST_RelayOnKp)                                    → :1020 newSta = ST_RlyOffL;
#   :1075 if (newSta != g_RelayCmd[0])                   ← **只有真发生转换才往下走**
#   :1090   g_RelayCmd[0] = newSta;                      ← **裁决汇合点**(读 newSta 停在这)
#   :1091   Fetch_CRC(&g_RelayCmd[0], LEN_RelayCmd);  :1092 Write_ParaData(ID_RelayCmd, …);
#
# ⚠ `:1090` 在 `if (newSta != g_RelayCmd[0])` **里面** —— 状态不变时它根本不执行, 断点不命中。
#   这既是判据③拿"状态不变"当阴性证据的根据, 也是"没命中"必须分两种解释的原因(见 ③ 的记录口径)。
# ⚠ `:1016`(0x2d586)与 `:1020`(0x2d590)这两行上 `newSta` **落在 DWARF 位置表的空洞区间**
#   (`info scope` 给的区间是 0x2d560-0x2d586 与 0x2d652-0x2d6ae), 停在那儿读它只会得到一句"读不到"
#   ⇒ 要读它的新值是 `:1090`。**不是被优化掉了**: 换个行号就出来了(见 CLAUDE.md〈断点〉末段)。
#
# ---- 三个条件里哪一个证得了 ----
#   条件一(TP_Local): **证不了** —— `TAB_MeterSty` 是编译期 const(UserCfg.c:26),
#     `Local_Meter` 在 UserCfg.h:102 已经 define ⇒ 本台固件烘成 TP_Local, 没有任何帧或注入能改它。
#     要证须烧一版 Local_Meter 未定义的固件重跑 ⇒ 判据④声明 `unprovable`(整项据此记未定论)。
#   条件三(ST_OvrCash2): **帧通道造不出** —— 充值/退费/开户都过 `Read_Esam` 的 MAC 校验
#     (TaskRmtFee.c:2156-2163 / :2212-2218), 清零只落 ST_OvrCash1(3) ⇒ 只能用**注入**:
#     停在 `:1009`(条件正在求值那一行)、把 `g_CashStatus[0]` 写成 ST_OvrCash2。
#     ⚠ 改的是**当时正在求值的那个条件**, 值只须活过微秒级 ⇒ 不必重算 `Fetch_CRC`
#       (LEN_CashStatus=3: [0]=状态, [1][2]=`Fetch_CRC` 的 sum^0x55 / xor^0xAA),
#       也遇不上 `Run_TaskLclFee` 的 MSG_SecStep 守卫(TaskLclFee.c:224-233)。
#   条件二(cmd == CMD_OutKeep): 判据③ —— 同样透支、同现态, 换发 0x3A(CMD_InKeep 保电)
#     ⇒ 裁决**不**落 ST_RlyOffL。
#
# ---- 为什么每条都要断言 `newSta`, 而不是只看 `g_RelayCmd[0]` ----
#   表值 `kp_expect(KP_CMD_KEEPOFF, 9)` = `TAB_RelaySta[6][8]` = ST_RelayOn(8), 而续拉块写的是
#   ST_RlyOffL(4)。`newSta` 是**裁决汇合点上的内部值**, 读到 4 只能是 :1016/:1020 写的;
#   只看对外的 `g_RelayCmd[0]` 答不了"是裁决改的还是别处写回来的"。两条读的是同一件事的两端
#   (内部值 / 对外落点), 互为对照物 —— 这正是"读到的字节要外接一个对照物"那条(two 观测)。

def ao_inject_allow():
    """12-3 要注入的表达式名字 —— 脚本构造会话时喂 `inject_allow=`(库**不替脚本开会话**)。

    白名单按名字点名、不按值(同 5-8 的 `ce_inject_allow`): 改注入什么值, 名单一个字都不用动。
    """
    return tuple(e for e, _v in AO_INJ_ASSIGN)


def auto_off_criteria():
    """12-3『保电功能·解除后本地费控根据剩余电费决定是否执行拉闸』的预设条目
    (源 = ledger.md 12-3「观察与判据」)。库内单点, 测试前定死。

    ⚠ ④ 本台证不了: 三个条件里的 `TAB_MeterSty.style == TP_Local` 是**编译期常量**
      (UserCfg.c:26; `Local_Meter` 在 UserCfg.h:102 已 define)⇒ 没有任何口能把这块表改成远程表,
      "远程费控需主站再发"这一半跑不出来。要证须烧一版 Local_Meter 未定义的固件重跑。
      故写 `unprovable`, 整项据此记未定论 —— 不是 `_suite.py` 的`台面`项:
      本项其余三条都做得出, 按硬性规矩 27「只有半段够不到就仍记`可跑`」。
    ⚠ ①③ 都要**注入**(透支态帧通道造不出)。台面没接 J-Link 时两条一起落 `ok=None`(没做成),
      与"固件不续拉"是两件事 —— 本项**没有可降级的黑盒替身**: 不透支时帧发下去在好固件上
      也只会走表值 8, 读到的 8 什么也证不了。
    """
    return {
        "①": "本地表处在 ST_RelayOnKp(9)、收到 645 0x3B(解除保电)、费控为 ST_OvrCash2(低于透支门限), "
              "裁决当场落 ST_RlyOffL(4), 不等主站再发命令。证据: 停 `TaskRelay.c:1090` 读到 "
              "`newSta == ST_RlyOffL`, 且 `g_RelayCmd[0]` 读回 4",
        "②": "阴性对照: 同一现态(9)、同一命令(0x3B)、但不透支(实测 `g_CashStatus[0] != ST_OvrCash2`), "
              "裁决走表值 `ST_RelayOn(8)`(= `TAB_RelaySta[6][8]`), 不落 ST_RlyOffL",
        "③": "`cmd == CMD_OutKeep` 是那块续拉的必要条件: 透支在位、同现态, 换发 0x3A(CMD_InKeep 保电), "
              "裁决不落 ST_RlyOffL, `g_RelayCmd[0]` 仍是 ST_RelayOnKp(9)(状态不变)",
        "④": {"text": "远程费控表: 解除保电后续用电, 需主站再发拉闸(不自动续拉)",
              "unprovable": "`TAB_MeterSty.style == TP_Local` 是编译期常量(UserCfg.c:26; "
                            "`Local_Meter` 在 UserCfg.h:102 已 define), 本台固件烘成本地表, "
                            "没有任何帧或注入能把它改成远程表。要证须烧一版 Local_Meter 未定义的固件重跑"},
    }


# ---- 12-3 的分步积木: 一步一个, 脚本正文一行一步(顺序即步骤) ----
# 每步只做一件事: 开场说明 / 走到现态 / ①③ 的注入+发帧一次 / ② 的读数+发帧一次 / 若干条判定。
# 判据文字、crit、falsify 都写在脚本里(见 project/tests/_test_12_3_auto_off.py);
# 下面每个函数只说自己那一步做什么、为什么这么做。

def auto_off_intro(have_wb, wb_waived=False):
    """第一步 · 开场: 本次走哪两种观测 —— 只打印, 不判。"""
    print("\n===== 12-3 解除保电后的本地续拉: 645 0x3B/0x3A × 注入造透支 × 断点看裁决 =====")
    if not have_wb and not wb_waived:
        print("   !! 本次无调试会话 → ①③ **不做**: 透支态(ST_OvrCash2)过 Read_Esam MAC, 帧造不出,"
              " 本项没有可降级的黑盒替身; 本次范围 = 仅黑盒的 ② 一条"
              "(见 CLAUDE.md「凭什么算「真实测试」·两种观测」)")
    elif not have_wb:
        print("   -- 本次**用户指定**只做黑盒(串口观测) ⇒ ①③ 按指定不做(J 列须记明本次范围)")


def auto_off_walk_plan(st):
    """第二步 · 走到 `ST_RelayOnKp(9)` 的**规划**(纯数据): 按序该发的那两帧 → `[(操作字, 目标态组, 档名)]`。

    0x3A 从任何态都落 7 或 9(裁决表 row 5 全 7, 列 8-15 落 9); 落在 7 时再发一次 0x1C(row 2 列 7 → 9)。
    已经在 9 → 空表(一帧都不用发)。**读与轮询在脚本**: 脚本读一次现态, 再按这张表逐条发帧、逐条轮询。
    ⚠ 走不到 9 时调用点**必须中止**: 前置没达成 ⇒ 三条判据都没做成, 与"固件不续拉"是两件事。
    """
    if st == KP_ST_RELAYONKP:
        return []
    return [(KP_CMD_KEEP, KP_KP_STATES, "归一到保电侧"),
            (KP_CMD_ON, (KP_ST_RELAYONKP,), "合闸保电")]


def auto_off_neg_decode(cash, resp, got):
    """第三步 · ②的装配(**纯解码**): 脚本读过的两个数 + 那一帧的应答 → `{"cash", "resp", "got", "exp"}`。

    `exp` = 表值 `TAB_RelaySta[6][8]`, 查不到给 `None`。
    ⚠ 它只回事实, 成不成立由调用点判: `cash` 读不到、或它**本来就**是 ST_OvrCash2 时,
      "不透支"这个前提不成立, 阴性对照不成立。
    """
    exp = kp_expect(KP_CMD_KEEPOFF, KP_ST_RELAYONKP)
    print("\n[② 阴性对照] 先读费控状态字, 再发 0x3B(解除保电) —— 现态 9, 期望落 %s" % kp_state_txt(exp))
    return {"cash": cash, "resp": resp, "got": got, "exp": exp}


def auto_off_inject_intro(st, bp_inj, bp_decide):
    """第四步之一 · ①③的注入前一句(**只打印**): 现态多高、要停在哪儿写什么。

    **触发通道**: 透支态帧造不出(见本节头) ⇒ 这一条走**注入**。条件三改的是**当时正在求值的那
    个条件**(停在 `:1009`), 值只须活过微秒级 ⇒ 不必重算 `Fetch_CRC`(LEN_CashStatus=3:
    [0]=状态, [1][2]=sum^0x55 / xor^0xAA), 也遇不上 `Run_TaskLclFee` 的 MSG_SecStep 守卫。

    ⚠ 调用点发的那一帧(`ctrl_relay`)是**必需的**而不是顺手: `Set_RelayCmdR` 由 `MSG_CtrlRelay`
      驱动, 不发帧它永远不跑。`with_inject` 把触发放后台线程(它的 ⚠ 那一段), 故不会与等待死锁。
    ⚠ 断点按**元组**递进去(`bp_inj` / `bp_decide`), 由 `inject_hit` 当场挂、用完必撤 ——
      传 bpno 则撤不撤只由 `drop=` 管, 没命中就留在槽里; 本核只有 4 个 FPB 槽。
    """
    print("\n[注入] 现态 %s; 停在 %s 把 g_CashStatus[0] 写成 ST_OvrCash2(%d)%s"
          % (kp_state_txt(st), _bptxt(bp_inj), AO_CASH_OVR,
             ", 停 %s 读 newSta" % (_bptxt(bp_decide)) if bp_decide else ""))


def auto_off_inject_decode(rec, got):
    """第四步之二 · ①③的装配(**纯解码**): 注入那一次的记录 + 脚本读回的现态 → 本步的 `step`。

    返回 `{"rec", "hit", "watch", "injects", "ns", "state"}`:
      `rec` = 注入那一次的记录(没做成给 `None`);
      `hit` / `watch` = 注入停点 / 裁决汇合点的命中(`None` = 没停到);
      `injects` = 注入账(改前 → 改后); `ns` = 裁决点读到的 `newSta`(没读成给 `None`);
      `state` = 帧之后脚本读回的 `g_RelayCmd[0]`。
    """
    _ns = gdb_ints(((rec or {}).get("vars") or {}).get("newSta"))
    return {"rec": rec, "hit": (rec or {}).get("at_hit"), "watch": (rec or {}).get("watch_hit"),
            "injects": (rec or {}).get("injects"),
            "ns": (_ns[0] if _ns else None), "state": got}


def auto_off_judge_neg(obs):
    """判② · 不透支时 0x3B 走表值 → `(ok, 详情)`。"""
    cash, exp, got = obs["cash"], obs["exp"], obs["got"]
    if cash is None:
        return None, "`g_CashStatus` 读不到 ⇒ 证不了本次**真的**不透支, 阴性对照不成立, 这一条没做成"
    if cash == AO_CASH_OVR:
        return None, ("台面上 `g_CashStatus[0]=%d` **本来就是** ST_OvrCash2(低于透支门限)"
                      "⇒ 阴性对照不成立, 这一条没做成(先把费控状态弄回不透支再跑)" % cash)
    if exp is None:
        return None, "裁决表 `TAB_RelaySta[6][8]` 查不到 ⇒ 期望值不可知, 这一条没做成"
    return got == exp, ("g_CashStatus[0]=%d(非 ST_OvrCash2=%d)⇒ 确实不透支 | 0x3B 应答=%s | "
                        "g_RelayCmd[0]=%s, 期望 %s(= TAB_RelaySta[6][8])"
                        % (cash, AO_CASH_OVR, obs["resp"], kp_state_txt(got), kp_state_txt(exp)))


def auto_off_judge_ovr(step):
    """判① · 三条件齐时发 0x3B ⇒ 裁决当场落 `ST_RlyOffL(4)` → `(ok, 详情)`。

    ⚠ 要断言的是**裁决汇合点上的内部值** `newSta`, 不是只看对外的 `g_RelayCmd[0]`:
      表值 `kp_expect(KP_CMD_KEEPOFF, 9)` = `TAB_RelaySta[6][8]` = ST_RelayOn(8), 而续拉块写的是
      ST_RlyOffL(4)。读到 4 只能是 `:1016`/`:1020` 写的; 只看对外值答不了"是裁决改的还是别处写回来的"。
    """
    ns, hit, watch = step["ns"], step["hit"], step["watch"]
    d = ("停 %s 注入(%s); 裁决点 %s; newSta=%s; 注入账 %s; g_RelayCmd[0]=%s(期望 ST_RlyOffL=%d, "
         "表值 ST_RelayOn=%s)"
         % (hit.where() if hit else "未命中(注入点)", step["injects"],
            watch.where() if watch else "未命中(裁决点)",
            kp_state_txt(ns) if ns is not None else "读不到", step["injects"],
            kp_state_txt(step["state"]), KP_ST_RLYOFFL, kp_state_txt(kp_expect(KP_CMD_KEEPOFF, KP_ST_RELAYONKP))))
    if hit is None:
        return None, "注入点没停到 ⇒ 透支态没造出来, 这一次没做成(**不是固件不续拉**): " + d
    if ns is None:
        return None, "裁决点 `newSta` 读不到(位置表空洞 / 没命中)⇒ 这一条没做成: " + d
    return ns == KP_ST_RLYOFFL, d


def auto_off_judge_keep(step, ovr_ns):
    """判③ · 透支在位、换发 0x3A ⇒ 状态不动 → `(ok, 详情)`。

    ⚠ 这一条的阴性**只有 ① 演示过续拉之后才作数**: 若 ① 读到表值 8(那块根本没生效), 则
      "发 0x3A 也不动"是理所当然的, 证不了 `:1010` 那半句 ⇒ 记 `ok=None`(没做成),
      **不许**记成"③ 通过"(那是拿一个恒真的观测冒充证据)。`ovr_ns` = ① 在裁决点读到的值。
    """
    hit = step["hit"]
    d = ("停 %s 注入(%s); 0x3A 应答后 g_RelayCmd[0]=%s(期望停在 ST_RelayOnKp=%d)"
         % (hit.where() if hit else "未命中(注入点)", step["injects"],
            kp_state_txt(step["state"]), KP_ST_RELAYONKP))
    if ovr_ns != KP_ST_RLYOFFL:
        return None, ("① 没演示出续拉(newSta=%s)⇒ 『换 0x3A 也不动』是理所当然的, 证不了 `:1010` "
                      "那半句 —— 这一次没做成。%s"
                      % (kp_state_txt(ovr_ns) if ovr_ns is not None else "读不到", d))
    if hit is None:
        return None, "注入点没停到 ⇒ 透支态没造出来, 这一次没做成: " + d
    return step["state"] == KP_ST_RELAYONKP, d


def auto_off_end_notes(have_wb, wb_waived=False, bp_inj=None, bp_decide=None):
    """收尾: 本次够不到的那两块讲明白 —— 只打印, 不记条目(它们不是判据, 是本次范围)。"""
    if not have_wb:
        print("   · 未做: ①③(需注入造透支) —— %s; 透支态(ST_OvrCash2)过 Read_Esam 的 MAC 校验,"
              " 帧通道造不出, 本项**没有可降级的黑盒替身**; 要证须接 J-Link 重跑"
              "(停 %s 写 g_CashStatus[0], 停 %s 读 newSta)"
              % ("用户本次指定只做黑盒" if wb_waived else "本次无调试会话",
                 _bptxt(bp_inj), _bptxt(bp_decide)))
    print("   · 未证: ④『远程费控表: 解除保电后续用电, 需主站再发拉闸(不自动续拉)』—— "
          "`TAB_MeterSty.style == TP_Local` 是编译期常量(UserCfg.c:26; `Local_Meter` 在 "
          "UserCfg.h:102 已 define), 本台固件烘成本地表, 没有任何帧或注入能把它改成远程表。"
          "要证须烧一版 Local_Meter 未定义的固件重跑。")
# ==================== 13-1『主动上报』判据与证据 ====================
# 判据源 = project/knowledge/_whitebox_ledger/ledger.md 13-1「观察与判据」。
# 判过: 事件发生 → 按周期上送 → 应答/超时后标志复位; 帧内容含该事件对象与状态字。
#
# ---- 源码现场(Application\TaskReport.c; 行号逐条核过 `info line`) ----
#   `Chk_ReportSta` 与 `Auto_Report(C_AutoRptGap)` 在同一拍背靠背(:865-866), 前者汇聚、后者组帧:
#     :1562-1566 for (port…) { g_FollowSta[port] &= TAB_RptMask; g_FollowSta[port] &= g_FollowMod[0]; }
#     :1582-1589 掉电事件 → rptnum[0] = C_AutoRptNum;  其余事件 → rptnum[1] = C_AutoRptNum
#     :1599-1605 `(g_FollowSta[PT_PLC_M] & g_FollowMod[0]) != 0` 那条**跟随入队**支
#     :1613-1621 g_ReportEn[1] != 1 或 g_ReportEn[3+PT_PLC_M] != 1 → event = FALSE
#     :1624-1631 两个 rptnum 各自钳到 ≤ C_AutoRptNum
#     :1632-1638 `if (g_AutoRptNum[i] < rptnum[i]) g_AutoRptNum[i] = rptnum[i];`(**只抬不降**)
#   `Auto_Report`:
#     :1773 if ((g_ReportEn[1] != 1) || (g_ReportEn[3+PT_PLC_M] != 1)) { g_AutoRptGap = 0; return; }
#     :1793-1802 钳到 u8AutoRptGap → 非 0 则 −−; (g_AutoRptGap != 0) || (两个 g_AutoRptNum 都为 0) → return
#     :1808-1819 掉电支: PIID = C_AutoRptNum − g_AutoRptNum[0]
#     :1848-1862 非掉电支: 88 01 <PIID> 01 33 20 02 00 01 01 <计数占位> 51 <OAD 4B 大端>
#     :1865-1902 事件循环: .bit.rpt = 1(:1875) → 计数++(:1891) → 51 + OAD(:1892-1896)
#     :1904-1909 计数 == 0 → 把计数与列表头一起撤掉
#     :1911-1932 跟随支(**g_FollowMod[1] == 0 且 g_FollowSta[PT_PLC_M] != 0 才进**):
#                `20 15 02 00 01 04 20` + 4B(= `sta` 按 TAB_RevByte 逐字节变换, 低字节先)
#     :1934-1938 计数 == 0 → g_AutoRptNum[1] = 0; return
#     :2033-2049 帧壳: len = outAddr − 1 + 2; 68 <len LE 2B> 83 05 <表号 6B> 00 … <FCS> 16
#     :2050-2065 Send_Report(buff, len+2) 之后**才**递减 g_AutoRptNum[i]、复位 g_AutoRptGap = u8AutoRptGap;
#                两个计数都为 0 时再 g_AutoRptGap += 5
#
# ---- 三条判据各归哪种观测 ----
#   ①②③④⑤ **都只有断点观测给得出**: 事件靠注入造(见下), 而 `rptnum`(Chk_ReportSta 的栈上局部)与
#   `buff`/`len`(Auto_Report 的栈上局部)在串口上根本读不到; AA80 只读得到全局量。
#   ⑥ 本台**证不了**(见 auto_rpt_criteria 的 ⑥)。本项**没有可降级的黑盒替身**。
#
# ---- 为什么事件必须注入造 ----
#   时钟故障的帧通道是"发一帧校时"(645 0x08 广播 / 0x14 写 DI 0400010C), 而**每条校时路都走
#   Set_MeterTime**, 它同时刷 RAM 镜像与 RTC 硬件 ⇒ 帧通道只能造出真故障, 造不出"表判定的那一刻"。
#   注入点 `TaskTime.c:275` 与 5-8 同一条(把 `g_MeterTime` 的月字节 −1 造时钟倒退)。
#
# ---- 三轮帧为什么不靠真实周期等 ----
#   `g_AutoRptGap` 每拍 −1, 初值 10 ⇒ 帧间隔 ~10s, 三轮要等 ~20s。而"周期"这条判据问的是
#   「帧送完之后 `g_AutoRptGap` 被复位为 u8AutoRptGap」(:2060), 与"等多久"无关 ——
#   故每轮在 :1632 把 `g_AutoRptGap` 一起注入成 0, 让当拍就成组。这是**压缩等待**, 不碰任何判据
#   阈值: 帧数仍由固件自己的 `g_AutoRptNum[1]` 递减决定, 复位仍读同一个变量。
#
# ---- 跑完台面留下什么 ----
#   注入由 `inject()` 的 `close()` 自动还原(白名单点名了才写得进去); 但**表钟被 −1 个月**的那一次
#   不还原(那是它自己走的 Set_MeterTime 路径), 故跑完必须 `python scripts/_restore_all.py` 拨钟。
#   跑中会先 `enter_factory`, 台面留厂内态 —— 同一条复位命令收。
AR_FALSIFY = {
    "①": "时钟故障已记档, 但 Chk_ReportSta 没把它排进主动上报队列 —— 停 断[A] `TaskReport.c:1632` "
          "读到 `rptnum[1] != C_AutoRptNum(3)`(或 `rptnum[0] != 0`): :1582-1589 那一路的判定没过, "
          "或 :1613-1621 `g_ReportEn` 把它整条否掉",
    "②": "断[B] 那一帧的 APDU 里搜不到 `51 30 2E 02 00`, 或事件列表计数为 0 —— 说明 :1865-1902 "
          "那段事件循环没把这笔事件写进载荷(帧壳组对了, 内容却是空的)",
    "③": "同一帧里搜不到 `20 15 02 00 01 04 20`, 或其后 4 字节与本机按 `TAB_RevByte` 算出的 "
          "`g_FollowSta[PT_PLC_M]` 变换值不同 —— :1918-1931 那段组帧块或位序变换写错了",
    "④": "三轮帧的 `g_AutoRptNum[1]` 不是 3→2→1、或帧后 `g_AutoRptGap` 没落回 (0, u8AutoRptGap] "
          "—— :2051-2065 那段递减/复位写错或漏执行",
    "⑤": "交给 `Send_Report` 的那一段帧壳不合规: 起头不是 `68`、CMD≠`83`、AF≠`05`、尾≠`16`、"
          "CA≠`00`, 或两个 LEN 字节与 `len` 对不上 —— :2033-2049 组帧写错",
}


def ar_inject_allow():
    """13-1 要注入的表达式名字 —— 脚本构造会话时喂 `inject_allow=`(库**不替脚本开会话**)。

    白名单按名字点名、不按值(同 5-8 / 12-3): 改注入什么值, 名单一个字都不用动。
    ⚠ 两处注入名单里 `g_AutoRptGap` 各出现一次(造事件置 10 / 每轮成组置 0), 故去重。
    """
    out = []
    for e, _v in tuple(AR_ASSIGN_TIME) + tuple(AR_ASSIGN_RPT):
        if e not in out:
            out.append(e)
    return tuple(out)


def auto_rpt_criteria():
    """13-1『主动上报』的预设条目(源 = ledger.md 13-1「观察与判据」)。库内单点, 测试前定死。

    ⚠ ⑥ 本台**证不了**: 上送走载波(`TAB_PortOAD[PLC_M] = F2090201`, DLT698App.c:3227),
      台上无集中器/载波主站收帧。故写 `unprovable`, 整项据此记未定论 —— 不记 `_suite.py` 的`台面`项:
      本项其余五条都做得出, 按硬性规矩 27「只有半段够不到就仍记`可跑`」。
    ⚠ ①②③④⑤ 都要**注入 + 断点**(事件造不出、局部量读不回)⇒ 台面没接探针时五条一起落
      `ok=None`(没做成), 与"固件不上报"是两件事。本项**没有可降级的黑盒替身**。
    """
    return {
        "①": "Chk_ReportSta 汇聚: 非掉电事件排进主动上报队列, `rptnum[1] == C_AutoRptNum(3)` "
              "且 `rptnum[0] == 0`(掉电那一路空)",
        "②": "上报内容/新增上报事件列表: 断[B] 那一帧的 APDU 里, 事件列表 D_Array 的计数 ≥ 1, "
              "且列表里含 D_OAD 标签 `51` 与时钟故障 OAD `30 2E 02 00`",
        "③": "上报内容/跟随上报状态字: 同一帧里含 `20 15 02 00 01 04 20` 与紧随的 4 字节, "
              "且这 4 字节等于同一刻 `g_FollowSta[PT_PLC_M]` 按 "
              "`TAB_RevByte[tmp&0x0F]<<4 | TAB_RevByte[tmp>>4]` 逐字节变换(低字节先)的结果",
        "④": "上报次数与周期: 一轮事件对应 C_AutoRptNum(3) 帧; 每送出一帧 `g_AutoRptNum[1]` "
              "递减(3→2→1→0), `g_AutoRptGap` 复位为 u8AutoRptGap(10s)",
        "⑤": "帧链路层自洽: 交给 Send_Report 的那一段起 `68`、CMD=`83`、AF=`05`、尾 `16`, "
              "两个 LEN 字节与 `len` 自洽, CA=`00`",
        "⑥": {"text": "帧真上送到对端、且对端应答后标志复位",
              "unprovable": "上送走载波(TAB_PortOAD[PLC_M] = F2090201, DLT698App.c:3227), "
                            "台上无集中器/载波主站收帧, Send_Report 送出去的那一帧没有任何东西能收。"
                            "要证须在台上接一台载波主站录帧, 或改烧一版走 485 口的固件重跑"},
    }




def ar_u8s(text, n):
    """gdb 打印的一个 `INT8U` 数组 → 定长 `[int]`; 读不到 / 凑不齐 → None。

    ⚠ **必须走 `gdb_bytes`**: 本表 gdb 把 `INT8U[]` 印成字符串字面量(`"\\000\\003"`),
      拿 `gdb_ints` 去抠会把八进制当十进制(见 `gdb_bytes` 的 ⚠)。`rptnum`/`g_AutoRptNum`
      正是 `INT8U[]`, 这一条是承重的。
    """
    t = str(text or "").strip()
    if not t or "optimized out" in t or "No symbol" in t:
        return None
    if t.startswith('"') or t.startswith("'"):
        b = gdb_bytes(t)          # ⚠ 字符串字面量: 三位八进制必须按八进制解
        return [x & 0xFF for x in b[:n]] if len(b) >= n else None
    iv = gdb_ints(t)              # 标量与 `{…}` 两种印法都走这条(数组/单个 `INT8U` 都在内)
    return [x & 0xFF for x in iv[:n]] if len(iv) >= n else None


def ar_u32s(text, n):
    """gdb 打印的一个 `INT32U` 数组 → 定长 `[int]`; 读不到 / 凑不齐 → None。

    元素是 32 位、gdb 印成十进制数, 故直接抠数(`g_FollowMod` 是 `INT32U[3]`)。
    """
    t = str(text or "")
    if not t or "optimized out" in t or "No symbol" in t:
        return None
    iv = gdb_ints(t)
    return [x & 0xFFFFFFFF for x in iv[:n]] if len(iv) >= n else None


def ar_u32(text):
    """gdb 打印的**一个 32 位量** → int; 读不到 → None。

    吃三种印法: `16384` / `0x4000`(标量) 与 `"\\000@"`(万一被印成字符串字面量)。
    ⚠ 与 `gdb_ints` 的分野: 那个对单个标量也返回列表, 而调用方要"读不到必须是 None"。
    """
    t = str(text or "").strip()
    if not t or "optimized out" in t or "No symbol" in t:
        return None
    if t.startswith('"') or t.startswith("'") or "{" in t:
        b = gdb_bytes(t)
        if len(b) >= 4:
            return sum((b[i] & 0xFF) << (8 * i) for i in range(4))
    iv = gdb_ints(t)
    if len(iv) >= 4:
        return sum((iv[i] & 0xFF) << (8 * i) for i in range(4))
    return (iv[0] & 0xFFFFFFFF) if iv else None


def ar_rev4(sta, rev):
    """按固件 `TaskReport.c:1925-1931` 把状态字变换成那 4 个字节(低字节先)。

    逐字节: `tmp = sta; sta >>= 8; out = TAB_RevByte[tmp & 0x0F] << 4 | TAB_RevByte[tmp >> 4]`。
    ⚠ `tmp` 在固件里是 `INT8U`(否则 `tmp >> 4` 会越出那张 16 项表) —— 这里照它的宽度取低 8 位。
    """
    out = bytearray()
    for _ in range(4):
        t = sta & 0xFF
        out.append(((rev[t & 0x0F] << 4) | rev[t >> 4]) & 0xFF)
        sta >>= 8
    return bytes(out)





# ==================== 5-11『事件记录·负荷开关误动作』判据与证据(2026-09-17 建) ====================
# 判据源 = project/knowledge/_whitebox_ledger/ledger.md 5-11「观察与判据」(= F/G/H/I 四列):
#   命令与实测不符 ⇒ 记失败事件; 一致 ⇒ 正常、不误记; 操作方式/时标正确。
#   「待核」那半句(一致却记失败 ⇒ 核比对判据与去抖 / 看是否误判硬件反馈)落在判据⑤ 的文字里。
#
# ---- 判据链(源码 Application\TaskRelay.c; 行号逐条核过 `info line`, 命中地址一并记下) ----
#   :261-265 TaskRelay() 局部量: MSG msg; INT16U delay; INT8U newSta, oper[LEN_Operator];
#            BOOL recd, temp, stat;
#   :267 Is_PowerOff() 早退;  :271 只对 TAB_MeterSty.relay ∈ {TP_In, TP_Ex} 干活
#   :275-278 收 MSG_CtrlRelay ⇒ Upd_RelayCmd()
#   :282-284 645 0x1C 命令的下发判定: (g_PlcState 全 F 或低 4 位为 0) && Get_CompFlag(CMP_075Un, 4)==0xFF
#   :286-316 按 g_RelayCmd[0] 分派: 拉闸 SwOff_Pulse() / 合闸 SwOn_Pulse(); g_RelayFlg = 0xAA / 0x55
#   :320-355 继电器回读与联动记录: :328 recd ^= 0xFF; :329 Write_ParaData(ID_RelayRcd, …)
#            :334 Read_ParaData(ID_Operator, &oper[0]); :335 Recd_CtrlRelay(&oper[0], recd)
#            :337-340 if (FALSE == Get_RelayFail()) Recd_RelayFail(TRUE)
#            :341-346 g_FailStat[0]=0 复位并写回;  :348 / :352 g_RelayBlk = FALSE / TRUE
#   :407 g_RelayFlg <<= 1;                       ← **实测反馈移位**(三拍移位寄存器)
#   :409-415 分/合那一位的置位条件(电平型 rlychk == TP_LevelL/H 要 4/5 拍, 脉冲型要 1/5 拍)
#   :416-423 (g_RelayFlg & 0x07) 全 1 ⇒ g_RelaySta = ST_SwOn; 全 0 ⇒ g_RelaySta = ST_SwOff
#   :426-428 **比对段的判定**: (g_PlcState 全 F 或低 5 位为 0) && Get_CompFlag(CMP_075Un, 5)==0xFF
#            ⚠ 这里是 **5**, 而 :282-284 那条命令判定用的是 **4** —— 两个不同的位数, 别对调
#   :430-431 比对: ((cmd < ST_RelayOn) && (g_RelaySta == ST_SwOff)) ||
#                  ((cmd >= ST_RelayOn) && (g_RelaySta == ST_SwOn))
#   :432-460 **一致支**: 复位 g_CheckSec / g_CheckNum, 上报开关变位
#   :461-480 **不符支**: ++g_CheckSec >= TAB_RelayRep[0] ⇒ 复位计数, 再按 TAB_RelayRep[1] 限次
#            重发 MSG_CtrlRelay(并把 g_RelayFlg 重写成 0xAA / 0x55)
#   :484 temp = Get_RelayFail();                 ← **上一次记下的状态**
#   :485-497 if ((relay == TP_Ex) && (cmd >= ST_RelayOn)) {…} else {…}
#            ⚠ 本台 relay == TP_In(Config/MengXi/UserCfg.c:26-61 的 TAB_MeterSty) ⇒ **恒走 else**
#              ⇒ :498 那一句**每拍都到**
#   :498-499 **写库判定**: (Get_CompFlag(CMP_075Un, 4)==0xFF) && (Get_CompFlag(CMP_120Un, 4)==0x00)
#   :501-514 现算 stat 的四条规则(Flg = g_RelayFlg, 比的是**低 4 位**):
#              拉闸命令 && (Flg&0x0F)==0x00  ||  合闸命令 && (Flg&0x0F)==0x0F   ⇒ stat = TRUE (一致)
#              拉闸命令 && (Flg&0x0F)==0x0F  ||  合闸命令 && (Flg&0x0F)==0x00   ⇒ stat = FALSE(不符)
#              低 4 位是"混合态"(移位还没走满)                                  ⇒ stat = temp(沿用)
#   :515 if (stat != temp)                        ← 只有"状态翻了"才记
#   :517-519 if (((cmd >= ST_RelayOn) && (TAB_Function.againrcd == TRUE)) || (g_FailStat[0] < 2))
#   :521 Recd_RelayFail(stat);                     ← **写库调用点 @0x2ce38**(判据的落点)
#   :523-525 if (++g_FailStat[0] > 3) g_FailStat[0] = 2;
#   :527-528 Fetch_CRC(&g_FailStat[0], …) + Write_ParaData(ID_FailStat, …)
#   :533-635 状态机: newSta = g_RelayCmd[0] 之后按 cmd 分派 —— **没有 case ST_RelayOn**
#            ⇒ default: break(即"合闸"这个命令值不产生任何继电器动作);
#            :635 只在值变了时才写回 ID_RelayCmd
#   :1277-1289 Upd_RelayCmd(): CRC 不过 / 值为 ST_Error0(0) / >= ST_Error1 ⇒ **从参数区重读**
#   :1308-1325 Get_RelaySta(): 前半段全注释掉, **正文就是 `return g_RelaySta;`**
#   :1337-1358 Get_RelayFail(): 先 Check_CRC(g_FailStat) 不过或值 > 3 ⇒ 从参数区重读;
#            末了 if (g_FailStat[0]==1 || ==3) return FALSE; else return TRUE
#            ⇒ **temp 是"参数区那一份"的反映 —— 注入 g_FailStat 骗不到它**(每秒复检)
#   ---- 落库那一头(Application\TaskRecord.c; 行号逐条核过 `info line`) ----
#   :1292 布局注释: 时间.6 + 时间.6 + 开关状态.1 + 事件前正反向有功总电能.10 + 事件后正反向有功总电能.10
#   :1294 Recd_RelayFail(BOOL end) —— ⚠ 这一行 `info line` 说 **contains no code**, 不能作断点
#   :1299 if (TRUE != Read_RecdData(ID_RelayFail, &buff[0], 1, 0, LEN_RelayFail)) return FALSE;
#   :1303-1317 **有头无尾守卫**: 上一条"开始时刻月日有效 && 结束时刻月日为空" ⇒ 只许 end == TRUE;
#            否则(空表 / 上一行完整) ⇒ 只许 end == FALSE。两支都不满足 ⇒ 直接 return FALSE
#            (**一个字都不写**, 但调用方 :523 那个 ++ 照样发生)
#   :1318-1337 「记录开始」支(end == FALSE):
#              :1320 Set_Data(&buff[33], 0x00, 20);   :1321-1322 事件对象写入
#              :1326 Get_MeterTime(&buff[0]);        :1327 Set_Data(&buff[6], 0x00, 6)
#              :1328 buff[12] = (TRUE==Get_RelaySta())? 0: 1;   ← **操作后开关状态**
#              :1329 Set_Data(&buff[23], 0x00, 10)
#              :1330-1332 (Read_CurkWh→buff[13]) && (Read_CurkWh→buff[18]) && Write_RecdData(…, 0, 53, 0)
#              ⇒ :1330 = **「记录开始」支那条写库语句的首行 @0x1ec40**(真 Write_RecdData
#                 是 :1332 那一句 = **@0x1ec70**, 中间夹两次 Read_CurkWh; 任一失败就在
#                 @0x1ec84 提前 return、一个字都不写 ⇒ **断点命中 ≠ 已写库**。
#                 ⚠ 断点只能打 :1330 —— 行表里 :1332 **无条目**, 见上面「不能作断点」那条注)
#   :1339-1356 「记录结束」支(end == TRUE):
#              ⚠ :1341 那句"清 buff[33..52]"是**注释掉的** ⇒ 这一支不动事件对象区
#              :1342-1343 事件对象; :1347 Get_MeterTime(&buff[6])
#              :1348-1350 (Read_CurkWh→buff[23]) && (Read_CurkWh→buff[28]) && Write_RecdData(&buff[6], 6, 47, 0)
#              ⇒ :1348 = **「记录结束」支那条写库语句的首行 @0x1ecb8**(真 Write_RecdData
#                 是 :1350 那一句 = **@0x1ecea**, 失败分支 @0x1ecfe; 同上, 命中 ≠ 已写库)
#   ⇒ **恢复不新开一行**: 「记录结束」只从 buff[6] 起写 47 字节 ⇒ 是把结束时刻补进**上一行**。
#     故"两笔" = 序号推进 1 + 旧行的结束时刻从(未结束)变成注入那一刻。
#   :1268-1273 Recd_CtrlRelay 要**六个** Read_CurkWh(正/反向 + Q1..Q4)全成 —— 那是 Recd_RelayFail
#            这两个的**超集** ⇒ 5-9/5-10 记得成的表, 本项这两个也读得成。
#
# ---- 本台事实(2026-09-17 离线核: 源码 + `info line`/`info scope`/`ptype /o`; 无一处靠猜) ----
#   · 台面交流 0V ⇒ 实测反馈永远"分闸"(`g_RelayFlg` 低 4 位恒 0x00), 而命令可以是"合闸"
#     ⇒ 看上去天然满足"命令≠实测"。**但它造不出来**: 命令得先发得出去, 而 :282-284 那条命令判定要
#     `Get_CompFlag(CMP_075Un, 4) == 0xFF`, 75%Un 在本台是 0V ⇒ **恒关着** ⇒ 帧通道下 645 0x1C
#     连 g_RelayFlg 都改不动。这就是"非用注入不可"的结构性理由(与 5-2 的 g_PowP 恒 0、
#     5-3 的抬压同类): **不是"证不了", 是触发通道缺一条**。
#   · 三处"电压窗口"判定用的是同一个量的**不同位数**: :282-284(命令判定, 4 位) / :426-428(比对判定, 5 位)
#     / :498-499(写库判定, 4 位, 且要 120%Un 那一位为 0)。
#     ⇒ 停 :498 一处把 `g_CompFlg[2]` 的**低 4 位**置 1、`g_CompFlg[3]` 的低 4 位清 0:
#         · `Get_CompFlag(CMP_075Un, 4)` 看低 4 位 ⇒ 0x0F == 掩码 ⇒ 返 0xFF ⇒ 写库判定开 ✓
#         · `Get_CompFlag(CMP_075Un, 5)` 看低 5 位 ⇒ 0x0F ≠ 掩码 ⇒ 返 0xAA ⇒ **比对判定仍关着**
#           ⇒ :463 那一次重发与 `SwOn_Pulse()` **不会发生**(这是本项注入的一个**结构性安全界**:
#             只开低 4 位, 不是"顺手把整个字节铺满"。铺满会打开比对判定, 那才会真的动继电器。)
#     · 这两处写进的是**全局 RAM**, 而 `g_CompFlg` 由 `Cmp_CompFlag` 每秒按实测量重算
#       ⇒ 注入**只活到下一次刷新(≤1s)**; 但"停住 → 注入 → 落库"在**同一拍内**走完, 够用。
#       ⇒ 本项**一个字节的参数区都不碰**。
#   · `TAB_Function.againrcd == TRUE`(Config/MengXi/UserCfg.c:67, 注释原文: 内置表合闸时误动记录:
#     FALSE=不重复; TRUE=重复) ⇒ :517-519 那个限次判定对**合闸方向**恒放行
#     (`(cmd >= ST_RelayOn) && againrcd` 那一支为真), 不受 `g_FailStat[0] < 2` 约束。
#     ⚠ 这就是把命令方向钉在**合闸**的结构性理由: 拉闸方向只剩 `g_FailStat[0] < 2` 一条路,
#       而 :523-525 每记一次就 ++ ⇒ 交替注"不符/一致"时第二条"不符"会被限次判定挡掉。
#       代价如实写在判据⑥ 的文字里: :501-514 那四条规则**只走到合闸方向那两条**。
#   · 命令值取 `ST_RELAY_ON = 8`(TaskRelay.h:30-59 的枚举)—— 它是**唯一**既 ≥ ST_RelayOn
#     (满足 :430-431 与 :501-514 的 `cmd >= ST_RelayOn`)、又**不会真的动继电器**的值:
#     :533 那个状态机 switch 里**没有 case ST_RelayOn** ⇒ default: break ⇒ 不发脉冲;
#     且 newSta == g_RelayCmd[0] ⇒ :635 那句写回也不执行。⇒ 注入 8 是**旁路**。
#   · 注入 `g_RelayCmd[0]` 靠得住: 它坏了 g_RelayCmd 的 CRC ⇒ 下一次 `Upd_RelayCmd:1279-1288` 从参数区
#     重读 ⇒ 值自己回去(那一步只在收到消息时才跑, 所以本次注入在此之前一直有效)。
#   · `Get_RelaySta()` 的正文就是 `return g_RelaySta;` ⇒ :1330 那一停读全局量 `g_RelaySta` 就是
#     :1328 那一位用的值(同一个 tick, 中间没有写点)。它是枚举型 ⇒ 必须用 `gdb_bool` 判读。
#   · ⚠ **不注入 `g_FailStat`**: :484 的 `Get_RelayFail()` 拿 `Check_CRC` 复检它, 注入的字节过不了
#     那道校验 ⇒ 当场被参数区那一份覆盖 ⇒ 骗不到 `temp`。**没有任何必要去碰参数区。**
#
# ---- 顺序为什么承重: 有头无尾守卫(:1303-1317) ----
#   读出的上一条记录: 开始时刻月日有效 && 结束时刻月日为空 ⇒ **该行未结束**, 此时只许 end == TRUE
#   (把尾巴补上); 否则只许 end == FALSE(开新行)。
#   ⇒ 段必须 **不符 → 一致 → 不符 → 一致** 交替, 且**末段停在「记录结束」**: 跑完记录完整、
#     `g_FailStat[0]` 停在偶数(= temp 真, 即"无故障"), 下一次跑仍是干净的起点。
#   ⚠ 与 5-2 不同: 本项**不需要**先探基线有没有悬空行 —— 归位那两段(先"不符"后"一致",
#     `RFL_INJ_DIFF` / `RFL_INJ_SAME`)在任何基线下都收敛到"无悬空行 + temp 为真", 逐条推过:
#       · 基线有悬空行 + temp 真  → "不符"被守卫拒(不写, 但 :523 的 ++ 照样发生 ⇒ temp 翻假),
#                                  接着"一致"把悬空行的尾巴补上;
#       · 基线有悬空行 + temp 假  → "不符"两者相同 ⇒ 不调; 接着"一致"补完;
#       · 基线空/完整 + 任一 temp → 最坏是多写一对**完整**的记录, 不留悬空行。
#     (逐条按 :1303-1317 / :501-514 / :515 / :523-525 推的; 这也是"第一段是 DIFF 而不是 SAME"的理由。)
#
# ---- 触发通道: 本项**只有注入**(CLAUDE.md「触发也有两个通道」) ----
#   停 `:498` 一次写四样: `g_CompFlg[2] |= 0x0F`(开写库判定) / `g_CompFlg[3] &= ~0x0F`(关 120%Un 那一位)
#   / `g_RelayCmd[0] = 8`(命令=合闸) / `g_RelayFlg = 0x0F`(实测=合闸) 或 `0x00`(实测=分闸)。
#   判定体随后**自己**走完 :501-514 现算 → :515 比对 → :521 调 Recd_RelayFail → 落库。
#   除了进厂内与读记录, 一个帧都不发。
#
# ---- ⚠ 本项**未证**的两半支(如实写进 details, 不许拿"另一种观测过了"去顶) ----
#   ① 「真电压下硬件反馈不跟随」—— 本台交流 0V, `g_RelayFlg` 恒分闸、75%Un 窗口常闭,
#      "命令 vs 实测不符"这个状态是**注入造出来的**。要证须把台面电压加到 ≥75%Un(本台 ≈165V),
#      让开关状态**真的**与命令不符。
#   ② 「拉闸方向」那两条规则(:501/:506)与 :461-480 那半支限次重发 —— 见上(`againrcd == TRUE`
#      只对合闸方向免掉 `g_FailStat[0] < 2`)。要证须先把参数区的 againrcd 改成 FALSE(动参数区)。
#
# ---- 副作用 ----
#   **不碰参数区、不发表钟动作、不改表钟**。跑完表留下**两到三条**负荷开关误动作记录(那是要证的
#   产物), 台面停在**厂内态**, 收尾由 `scripts/_restore_all.py` 收拾。**不碰** clear_meter/clear_event。
#   ⚠ 固件自己会在每次 `stat != temp` 时 `++g_FailStat[0]` 并**写回参数区**(:523-528)——那是
#     "上一次记下的状态"的**持久**值。末段停在「记录结束」⇒ 跑完它停在**偶数**(0 或 2, 即 temp 真);
#     具体是 0 还是 2 取决于起点, 脚本在最末一段的 `at_vals` 里如实记下。
#     `_restore_all.py` 不还原它(它不在那 6 步里) —— **照实说明, 不悄悄过**。
RFL_EV_CODE = P.RFL_EV_CODE
RFL_RCSD3 = P.RFL_RCSD3
RFL_CMP_075UN = P.RFL_CMP_075UN
RFL_CMP_120UN = P.RFL_CMP_120UN
RFL_CMP_NUM = P.RFL_CMP_NUM
RFL_CMP_MASK = P.RFL_CMP_MASK
RFL_CMD_ON = P.RFL_CMD_ON
RFL_FLG_MASK = P.RFL_FLG_MASK
RFL_FLG_ON = P.RFL_FLG_ON
RFL_FLG_OFF = P.RFL_FLG_OFF
RFL_REC_CAP = P.RELAY_REC_CAP     # 该口容量(条) = RecdData.h:142 `NUM_RelayFail` 10u

_RFL_WIN = (("g_CompFlg[2]", "g_CompFlg[2] | 0x%02X" % RFL_CMP_MASK),
            ("g_CompFlg[3]", "g_CompFlg[3] & ~0x%02X" % RFL_CMP_MASK))
RFL_INJ_DIFF = _RFL_WIN + (("g_RelayCmd[0]", str(RFL_CMD_ON)),
                           ("g_RelayFlg", "0x%02X" % RFL_FLG_OFF))    # 命令=合闸, 实测=分闸 ⇒ 不符
RFL_INJ_SAME = _RFL_WIN + (("g_RelayCmd[0]", str(RFL_CMD_ON)),
                           ("g_RelayFlg", "0x%02X" % RFL_FLG_ON))     # 命令=合闸, 实测=合闸 ⇒ 一致


def rfl_inject_allow():
    """本子项要注入的表达式名字 —— 脚本构造会话时喂 `inject_allow=`(库**不替脚本开会话**)。

    由 `RFL_INJ_*` 两表单点导出(去重、保序)。白名单**精确匹配符号名**、事后补不了,
    所以这里多写一个名字的代价是"写不进去", 少写一个的代价是"调用期当场抛"。
    """
    out = []
    for _t in (RFL_INJ_DIFF, RFL_INJ_SAME):
        for _e, _v in _t:
            if _e not in out:
                out.append(_e)
    return tuple(out)


def relayfail_criteria():
    """5-11 的**预设条目**(源 = ledger.md 5-11 的「观察与判据」+ 操作步骤 + 待核那半句)。测试前定死。

    拆分说明(每条都答得出 falsify, 见 `relayfail_roundtrip` 内各 add 的 falsify):
      · ① / ② 拆开 —— 「不符」与「一致」是**两条独立的写库路径**(TaskRecord.c:1330 与 :1348)。
        合成一条的话, 一个"只会记开始、结束那半根本不通"的固件照样记"满足"。
      · ③ 时标单列 —— "落没落库"与"落进去的时刻对不对"是两件事: 固件若把 Get_MeterTime 写到
        错偏移, 前两条照样绿。
      · ④a / ④b 拆开 —— `Recd_RelayFail` **没有操作者字段**(布局见 TaskRecord.c:1292:
        时间 + 时间 + **开关状态.1** + 电能), 所以规格里「操作方式正确」那一列的可核对项只有
        `buff[12]`。④a 问"它写的是实测状态还是本帧命令"(本次两者相反 ⇒ 分得开);
        ④b 问"那一位的 0/1 与规范的合/分同向"。④b 拿 `:1328` 那一句(被验对象自己)当定义
        ⇒ 两边同源, 声明本台不可证(要 ≥75%Un 造出真的合闸态), **不是**把它从账上抹掉。
        ⚠ 与 5-9/5-10 的 `Recd_CtrlRelay` 不同 —— 那一支才有 `pOper`。
      · ⑤ 一致时不误记 —— 否定期望那一条(判据⑤ 的出处就是规格「待核」那半句: 一致却记失败 ⇒
        核比对判据与去抖 / 看是否误判硬件反馈)。它同时钉住 `:515` 那个 `stat != temp` 与
        `:2004-2006` 那类去抖复位 —— 否则一致状态每拍都在记。
      · ⑥ 现算值 —— 规格写的是"命令与实测不符"; 而固件判的是 `:501-514` 那四条**现算规则**
        (输入 = 命令 + `g_RelayFlg` 低 4 位 + 上一次的 temp), 不是直接比 `g_RelaySta`。
        这一条把这两者的差钉住: 在 :521 读回的 `stat` 必须与按四条规则现算的值相同。
        ⚠ **本台只走合闸方向那两条**(:502/:511) —— 拉闸方向那两条(:501/:506)要 `againrcd == FALSE`
          或 `g_FailStat[0] < 2` 才走到, 见段头「未证」。这一条**不覆盖**拉闸方向。
    """
    return {
        # ⚠ 这两条的正文与 ledger.md 的 J 列**逐字相同**。
        #   2026-09-18 改口径: 「:1330/:1348 写库位置被执行」是**过claim** —— 那两个断点是写库
        #   **语句的首行**(真调用 @0x1ec70/@0x1ecea 在其后), 任一 Read_CurkWh 失败就提前
        #   return、一个字都不写, 而断点已经停过 ⇒ 落库与否**只认串口侧**。
        "①": "命令与实测**不符** ⇒ 落一笔失败事件(走 Recd_RelayFail 的「记录开始」支): "
              "串口侧该口序号推进 1、且新行的结束时刻为空 —— **落库与否只认串口侧这一条**; "
              "白盒停 断[C] TaskRecord.c:1330 只证走到那条写库语句的首行(真写调用 @0x1ec70 "
              "在其后, 两次 Read_CurkWh 任一失败就提前 return, 而断点已经停过)⇒ "
              "**命中 ≠ 已写库**",
        "②": "命令与实测**一致** ⇒ 落「恢复」一笔(走「记录结束」支; 白盒停 断[D] :1348 "
              "同样只证走到那条写库语句的首行 —— **命中 ≠ 已写库**), 把结束时刻补进"
              "**上一行**(序号不推进 —— 结束不新开一行; 落库认串口侧读回)",
        "③": "两笔的时标 == 当时表钟(「记录开始」支的时刻在行首 buff[0..5], "
              "「记录结束」支的时刻在 buff[6..11])",
        "④a": "「操作后开关状态」写库那一刻 buff[12] 与**固件当时的开关状态量**同向: 本次命令=合闸、"
               "实测=分闸, 而写进去的那一位与 `g_RelaySta`(连同 `g_RelayFlg` 低 4 位)所指的分闸"
               "状态一致 —— 写反 / 写错偏移 / 沿用上一笔都报这里",
        "④b": {"text": "①那一位的 0 / 1 与规范定义的「合 / 分」同向; ②固件认定的开关状态 == "
                        "开关的物理位置(记录里那一列要反映的是实际位置)",
               "unprovable": "本台 0V ⇒ 两个都分辨不了: ⓵实测只有「分」这一种(`g_RelayFlg` 恒分闸、"
                             "75%Un 窗口常闭), 拿不到一个真的「合」去对拍, 0/1 的指向无从分辨 —— "
                             "本台只有 `TaskRecord.c:1328` 那一句 `(TRUE==Get_RelaySta())? 0: 1` 可看, "
                             "而它正是被验的对象; ⓶本台没有开关位置的独立读数(判据拿的 `g_RelaySta` "
                             "就是固件自己那一份), 要证须接到辅助触点/开关位置反馈。"
                             "两条都要 ≥75%Un(本台 ≈165V) 才谈得上"},
        "⑤": "命令与实测**一致**时不误记: 那一段里写库调用点(:521)**不被走到**, 记录一条都不新增。"
              "查不过时应核比对判据与去抖 —— 一致却记失败就是这里报出来的",
        "⑥": "判据不是直接比 g_RelaySta, 而是 :501-514 那条现算规则: 在 :521 读回的 `stat` 必须与按"
              "「命令 + g_RelayFlg 低 4 位」现算的值相同, 且那一次走的是**合闸方向**那两条规则"
              "(:502/:511)—— **拉闸方向那两条(:501/:506)本台未走**(要 againrcd==FALSE 或"
              " g_FailStat[0] < 2, 见段头「未证」); 低 4 位是混合态时固件沿用 temp 那一路也未走",
        # 规范「事件记录」那一节的「最近 N 次」: 容量这一档在固件里就是 `NUM_RelayFail`(=10)
        "⑦": "最近 %d 次『负荷开关误动作』封顶在容量上: 连造 %d 回后该口条数停在 %d, 且第 %d 位"
              "那条(最早)被顶掉 —— 其序号与发生时刻不再是连造前那一对"
              % (RFL_REC_CAP, RFL_REC_CAP + 1, RFL_REC_CAP, RFL_REC_CAP),
    }


def read_relayfail_rows(ser, positions=(1, 2, 3), chip=None, wait=3.0, quiet=False):
    """读 负荷开关误动作 第 1/2/3 条(1=最新) → `{pos: row|None}`; 每行自打印一行摘要(quiet 关掉)。

    列选 = 序号 + 发生时刻 + 结束时刻(`RFL_RCSD3`)。记录布局与掉电不同(发生时刻 @buff[0..5],
    结束时刻 @buff[6..11]), 但**三个列 OAD 与 5-3 完全一样**(同一张事件记录表项), 所以
    `read_event_ud`/`event_row3`/`rcsd` 直接复用, 只是编码与列选组合换了。
    """
    out = {}
    for p in positions:
        ud = read_event_ud(ser, RFL_EV_CODE, pos=p, rcsd=rcsd(*RFL_RCSD3), chip=chip, wait=wait)
        row = event_row3(ud) if ud else None
        out[p] = row
        if not quiet:
            # ⚠ 四种"读不出"必须分清(5-3 实踩的同一处): DAR 打回 / 记录区为空 / 解不出行 / 真静默。
            #   混成一句会把**固件状态错**说成"表里没记录"。
            if row is None:
                print("   [读回] 负荷开关误动作 pos%d: %s"
                      % (p, _rec_none_reason(ud, "负荷开关误动作")))
            else:
                print("   [读回] 负荷开关误动作 pos%d: 序号=%s 发生=%s 结束=%s"
                      % (p, row["seq"], row["t_start"] or "-", row["t_end"] or "(未结束)"))
    return out


def relayfail_list_state(ser, chip=None, wait=3.0):
    """负荷开关误动作记录表的状态 → `"empty"` / `"has"` / `None`(读不成)。

    ⚠ **非有不可**: 本项**从来没跑过**, 0x2B 这张列表在跑之前**很可能是空的** —— 而空表的应答
      与 5-2/5-3 那两张"已经有记录"的表**长得不一样**: `event_row3` 对空列表同样返回 `None`,
      与"DAR 打回 / 静默"混在一起。基线那一步若把"空表"读成"读不出"就会**整体中止**,
      现象是"本项没做成", 而那看起来像固件问题。
    判据是**记录条数**(`ud_record_count`), 不是"应答是不是 `85 03` 开头"(2026-09-17 收紧):
      "`85 03` 却解不出行"里其实挤着两种情形 —— **真 0 条**(空表)与**有条数却解不出行**(协议侧
      真问题), 把它们都叫"空表"就是把一个故障说成"本来就没有"。条数只认 `85 03` 里的那一个字节,
      读不出 / DAR 打回 / 别的应答一律 `None`(不猜)。
    """
    ud = read_event_ud(ser, RFL_EV_CODE, pos=1, rcsd=rcsd(*RFL_RCSD3), chip=chip, wait=wait)
    n = ud_record_count(ud)
    if n is None:
        return None
    if n == 0:
        return "empty"
    return "has" if event_row3(ud) is not None else None


def rfl_top(by):
    """按序号归位后的 `{seq: row}` → 当前**最大序号**; 空表记 `0`(记录区还没开时的起点)。"""
    return max(by) if by else 0


def rfl_stat_expect(cmd_txt, flg_txt, temp_txt=None):
    """`g_RelayCmd[0]` / `g_RelayFlg` 的 gdb 文本 → 固件**应当**算出的 `stat`; 读不懂 → `None`。

    就是 `TaskRelay.c:501-514` 那四条规则(比的是 `g_RelayFlg & RFL_FLG_MASK`), 低 4 位是混合态时
    固件沿用 `temp` —— 那时只有 `temp_txt` 给得出来才答得出, 否则返 `None`(不猜)。
    ⚠ 纯函数, 离线可自检(见 `_relayfail_checks`)。判据⑥ 的期望值由它算, **不许在调用处内联** ——
      否则"算式写错了"与"固件不按算式走"在账本里长得一样。
    """
    c = gdb_ints(cmd_txt)
    f = gdb_ints(flg_txt)
    if not c or not f:
        return None
    cmd = c[0]
    win = f[0] & RFL_FLG_MASK
    if (cmd < RFL_CMD_ON and win == RFL_FLG_OFF) or (cmd >= RFL_CMD_ON and win == RFL_FLG_ON):
        return True                                   # 命令与实测一致
    if (cmd < RFL_CMD_ON and win == RFL_FLG_ON) or (cmd >= RFL_CMD_ON and win == RFL_FLG_OFF):
        return False                                  # 命令与实测不符
    return gdb_bool(temp_txt)                        # 混合态: 固件沿用 temp(读不到就 None)


def relayfail_roundtrip(ser, wb=None, wb_waived=False, wait=3.0, neg_window=4.0,
                        settle_timeout=12.0, hit_timeout=60.0,
                        bp_judge=None, bp_call=None, bp_wrs=None, bp_wre=None,
                        judge_vars=(), call_vars=(), wrs_vars=(), wre_vars=()):
    """5-11 全流程 + 判据(库内单点, 脚本不留): 归位 → 一致(否定) → 不符 → 一致 → 不符 → 末段一致。

    **四个断点与四组变量由脚本递进来**(库里**不留第二份**)—— 这是承重约定, 不是风格:
    `scripts/_check_anchors.py` 只扫**脚本**里的 `BP_*`/`VARS_*`, 断点写进库里那条路它看不见
    (5-3 的 `LP_BP_*` 就是这么留下的盲区: 判定全绿, 而"停在哪一行"没人核过)。

      `bp_judge`  `TaskRelay.c:498` 写库判定那一句 @0x2cda8 —— **注入停靠点**
                  该停 `stat` 还没活(`:501` 才起)⇒ `judge_vars` **不许收 `stat`**;
                  `temp` 可读(0x2cda8-0x2ce20 ✓), 其余用全局量。
      `bp_call`   `:521` Recd_RelayFail 调用点 @0x2ce38(`stat` 可读 ✓)
      `bp_wrs`    `TaskRecord.c:1330` 「记录开始」写库位置 @0x1ec40(`buff` 可读 ✓)
      `bp_wre`    `TaskRecord.c:1348` 「记录结束」写库位置 @0x1ecb8(`buff` 可读 ✓)
      ⚠ `TaskRecord.c:1294`(函数定义行)/:1332/:1339/:1350 四行 `info line` 说 **contains no code**,
        **不能作断点** —— 抄下来 gdb 照样停得住、变量照样"读得出", 读回来却是别处的现场。
      ⚠ `end` 这个形参在两个写库位置的 PC 区间里**没有位置**(死了)⇒ **不许**放进
        `wrs_vars`/`wre_vars`。

    **触发通道**: 本项**只有注入**(见段头 —— 命令判定与写库判定都吃 75%Un, 而本台 0V ⇒ 恒关)。

    **白盒动作**(库不 import swdbg, 会话由脚本持有, 这里只收回调):
      `wb = {"inj": partial(GD.inject_hit, g), "miss": partial(GD.inject_miss, g)}`
      · 归位①·不符: 停 :498 注"命令=合闸·实测=分闸", 等 :521(读调用点)              —— 参考
      · 归位②·一致: 停 :498 注"命令=合闸·实测=合闸", 等 :1348(可能把悬空行补完)      —— 参考
      · 一致·否定:  停 :498 注"一致" ⇒ :521 **不该**被走到                           —— ⑤
      · 不符:       停 :498 注"不符" ⇒ 等 :521(读 stat / 命令 / g_RelayFlg)          —— ⑥
      · 一致:       停 :498 注"一致" ⇒ 等 :1348(「记录结束」写库位置)                   —— ②
      · 不符·二次:  停 :498 注"不符" ⇒ 等 :1330(「记录开始」写库位置, 读 buff/状态)      —— ① ④
      · 一致·末段:  同上再走一趟, 跑完记录完整、g_FailStat[0] 停在偶数               —— ②

    **顺序是承重的**(有头无尾守卫 TaskRecord.c:1303-1317): 必须 不符 → 一致 → 不符 → 一致,
    且**末段停在「记录结束」**(见段头那段逐条推导)。

    **记录行怎么变**(串口侧的形状): 「记录结束」**不新开一行**, 是把上一行的结束时刻补上
    (:1348 只从 buff[6] 起写 47 字节); 「记录开始」才新开一行(:1330 写全行, 结束时刻为空)。
    故"两笔" = **序号推进 1 + 旧行的结束时刻从 (未结束) 变成注入那一刻**。

    **厂内态是前置**(与 5-3/5-2 同一处): `CMD_GetRequestRecord` 照样过 `Chk_SafeMode`, 不在厂内态时
    事件记录 OI 全落 `DAR_MatchAuth` ⇒ **每条读回都被 20 打回**, 而现象是"帧完全合法、应答照回、
    负载里是 `00 14`", 与"表里没记录"长得一样。故本函数**自己进厂内**且**不退** —— 退厂内是
    `_restore_all.py` 第 [5a] 步的事, 必须排在拨钟/合闸/复核之后。

    **本函数不碰参数区、不发表钟帧、不改表钟**; 跑完表留下**两到三条**负荷开关误动作记录(那是要证的
    产物), 台面停在厂内态, 收尾由 `scripts/_restore_all.py` 收拾。**不碰** clear_meter / clear_event。

    返回 `(recs, details, scope)` —— 与 3-1/3-2/5-2/5-3/4-7 同形: **`scope is None` = 半途中止**
    (结构信号, 别让脚本去嗅文案)。**本函数不总结论** —— 判定只有 `common/judge.py` 一处。
    """
    print("\n===== 5-11 负荷开关误动作: 归位 → 一致(否定) → 不符 → 一致 → 不符 → 末段一致 → 698 读回 =====")
    recs = []

    # 进厂内: **读记录同样受 Chk_SafeMode 管**(见 docstring) —— 缺了这一步整种观测读空。
    enter_factory(ser)

    def add(label, ok, why, crit=None, falsify=None, obs=judge.SERIAL):
        """`ok` 三态: True 达成 / False 观察到不对 / **None 没做成**(没命中、没读到)。"""
        recs.append(rec(label, ok, why, crit=crit, obs=obs, falsify=falsify))
        print("   [%s] %s —— %s" % ({True: "PASS", False: "FAIL", None: "TBD"}[ok], label, why))

    # 断点观测接线: 没有会话时六次注入全不做(注入没有可降级的黑盒替身), 但**要说全**。
    wb = wb or {}
    inj, miss = wb.get("inj"), wb.get("miss")
    have_wb = inj is not None and miss is not None
    if not have_wb and not wb_waived:
        print("   !! 本次无调试会话 → 断点观测(白盒)整体**未做**; 本次范围 = 仅黑盒"
              "(对外行为可判, 内部现算规则 / 两条写库路径 / 开关状态位未取证 —— "
              "见 CLAUDE.md「凭什么算「真实测试」·两种观测」)")
    elif not have_wb:
        print("   -- 本次**用户指定**只做黑盒(串口观测) ⇒ 断点观测(白盒)按指定不做(J 列须记明本次范围)")

    # ---- 基线 ----
    t0 = read_clock(ser, chip="管理芯", quiet=True)
    rows0 = read_relayfail_rows(ser, (1, 2, 3), wait=wait)
    lst0 = None
    if rows0.get(1) is None:
        # 空表与"读不成"必须分清(见 `relayfail_list_state` 的 ⚠): 空表是**正常起点**, 不是中止理由。
        lst0 = relayfail_list_state(ser, wait=wait)
        if lst0 is None:
            why = ("表钟或负荷开关误动作记录读**不成**(表钟=%s; 最新一条 的读回既不是「有行」也不是"
                   "「空表应答」)⇒ 基线无对照, 中止" % t0)
            print("   !! %s" % why)
            return [rec("基线读取 ⇒ 中止", None, why, obs=judge.SERIAL)], [why], None
    by0 = rows_by_seq(rows0)     # 同形复用(它只碰 r["seq"], 无子项色彩; 本仓已另有 lp_rows_by_seq, 不再添第三份)
    seq0 = rfl_top(by0)
    _sh0 = ("记录区当前为空" if rows0.get(1) is None
            else "序号=%s 发生=%s 结束=%s" % (seq0, rows0[1]["t_start"] or "-",
                                             rows0[1]["t_end"] or "(未结束)"))
    print("   基线: 表钟=%s | %s" % (t0, _sh0))
    add("参考: 基线形态(记录区空 / 最新一条是否有头无尾 —— 归位那两段在任何形态下都收敛)",
        None, _sh0)

    # ---- 归位① / 归位②(见段头: 两段收敛到「无悬空行 + temp 为真」) ----
    r_h0 = r_h1 = None
    if have_wb:
        r_h0 = inj(bp_judge, RFL_INJ_DIFF, watch=bp_call, watch_vars=call_vars,
                   at_vars=judge_vars, timeout=settle_timeout,
                   label="断[A] 归位①: 停 %s 注入 命令=合闸·实测=分闸(不符) ⇒ 等 %s"
                         "(注入前那一停读回 temp / g_FailStat[0], 那是基线的「上一次记下的状态」)"
                         % (_bptxt(bp_judge), _bptxt(bp_call)),
                   crit=None,
                   falsify="命令与实测不符也不进比对 ⇒ 走不到 %s(即便如此, 归位仍然成立)" % _bptxt(bp_call))
        _h0v = (r_h0.get("at_vals") or {}) if r_h0 is not None else {}
        print("      断[A] 归位①: ok=%s | %s" % ((r_h0 or {}).get("ok"), (r_h0 or {}).get("detail")))
        print("      注入前那一停读到: %s" % _h0v)
        add("参考: 归位①(注入 命令=合闸·实测=分闸) —— 基线 temp=%s / g_FailStat[0]=%s / "
            "g_RelayFlg=%s" % (_h0v.get("temp"), _h0v.get("g_FailStat[0]"), _h0v.get("g_RelayFlg")),
            None, "命中=%s(命中 ⇔ 基线 temp 为真 ⇒ 这一段真的调了一次 Recd_RelayFail)"
                  % ((r_h0 or {}).get("ok"),))
        r_h1 = inj(bp_judge, RFL_INJ_SAME, watch=bp_wre, watch_vars=wre_vars,
                   at_vars=judge_vars, timeout=settle_timeout,
                   label="断[A] 归位②: 停 %s 注入 命令=合闸·实测=合闸(一致) ⇒ 等「记录结束」"
                         "写库位置 %s(把可能的悬空行补完; 基线本来就干净时这一段等不到, "
                         "那不是失败)" % (_bptxt(bp_judge), _bptxt(bp_wre)),
                   crit=None,
                   falsify="基线有悬空行而「记录结束」支仍走不到 ⇒ 不会停到 %s" % _bptxt(bp_wre))
        print("      断[A] 归位②: ok=%s | %s" % ((r_h1 or {}).get("ok"), (r_h1 or {}).get("detail")))
        add("参考: 归位②(注入 命令=合闸·实测=合闸)", None,
            "命中=%s; %s" % ((r_h1 or {}).get("ok"), (r_h1 or {}).get("detail") or "-"))
    else:
        add("参考: 归位(不符 → 一致 两段)", None, "没有调试会话(或用户指定只做黑盒)⇒ 本次没做")

    # ---- ⑤ 一致时不误记(否定期望) ----
    # ⚠ 为什么这一段**必成立**: 归位那两段之后 temp 恒为真(逐条推过, 见段头), 而"命令=合闸·实测=合闸"
    #   ⇒ :501-514 现算 stat = TRUE == temp ⇒ :515 那一支不进 ⇒ :521 走不到。若它**照样被走到**,
    #   那就是"一致却记失败"—— 正是规格「待核」那半句要查的形态。
    # ⚠ ⑤ 的**比较基准取在此刻这一读回**, 不是基线 `seq0` —— 归位① 在"基线 temp 为真"时
    #   会**合法地**落一笔「记录开始」把序号推进一格(归位那两段的读数即证据), 拿基线比会恒判
    #   "序号推进 ⇒ 一致时也记失败", 而固件根本没在一致时记过(2026-09-21 首跑实测: 序号 10 → 11,
    #   新行发生=08:54:41 / 结束=08:54:42 正落归位①窗口, 与一致那一趟无关)。
    _seq_pre = rfl_top(rows_by_seq(
        read_relayfail_rows(ser, (1, 2, 3), wait=wait, quiet=True)))
    r_miss = None
    if have_wb:
        r_miss = miss(bp_judge, RFL_INJ_SAME, watch=bp_call, window=neg_window,
                      timeout=settle_timeout, at_vars=judge_vars,
                      label="断[A] 否定期望: 注入 命令=合闸·实测=合闸(一致) ⇒ %.0fs 内 %s **不该**被走到"
                            % (neg_window, _bptxt(bp_call)),
                      crit="⑤",
                      falsify="命令与实测一致时也进 stat != temp 那一支(或去抖计时没被复位)"
                              "⇒ :521 照样被走到, 一致状态每拍都在记失败")
    else:
        time.sleep(neg_window)
    rows_a = read_relayfail_rows(ser, (1, 2, 3), wait=wait, quiet=True)
    _seq_a = rfl_top(rows_by_seq(rows_a))
    if r_miss is not None:
        print("      断[A] 否定那一趟: ok=%s | %s" % (r_miss.get("ok"), r_miss.get("detail")))
        print("      注入前那一停读到: %s" % (r_miss.get("at_vals") or {}))
        add("断[A] 命令与实测一致时, 写库调用点(:521)**未被走到**(该指令路径不存在)",
            r_miss.get("ok"), r_miss.get("detail") or "没做成 —— 未证",
            crit="⑤", obs=judge.DEBUG,
            falsify="一致时也走到 %s ⇒ 一致状态每拍都在记失败(规格「待核」那一形态)" % _bptxt(bp_call))
    else:
        add("断[A] 一致时不走到写库调用点", None,
            "没有调试会话(或用户指定只做黑盒)⇒ 白盒这一次没做成", crit="⑤", obs=judge.DEBUG,
            falsify="不做这一次时无从判 —— 本记录不作为固件证据")
    add("串口 命令与实测一致: 记录未新增(序号不推进)",
        (_seq_a == _seq_pre) if (_seq_a is not None and _seq_pre is not None) else None,
        "序号 %s → %s(基准取在否定窗口之前那一次读回; 基线曾为 %s, 归位段合法推进不计入)"
        % (_seq_pre, _seq_a, seq0),
        crit="⑤",
        falsify="一致状态也推进序号 ⇒ 判定体在命令与实测相符时照样落库")

    # ---- ⑥ 不符: 停 :498 注"不符", 等 :521 读现算出来的 stat ----
    r_diff = None
    # ⚠ ③ 那个窗口的**下界必须读在触发之前** —— `inj` 返回时记录已经写完, 下界读在它之后
    #   等于"窗口从写完那一刻之后才开始", 时标正确的固件也落窗外(2026-09-21 首跑实测: 发生
    #   时刻 08:54:58 落进窗口 [08:55:00, …]; 同一场里 ② 的「结束时刻」窗口取在触发两侧 ⇒ 通过)。
    t_diff0 = read_clock(ser, chip="管理芯", quiet=True) or t0
    if have_wb:
        r_diff = inj(bp_judge, RFL_INJ_DIFF, watch=bp_call, watch_vars=call_vars,
                     at_vars=judge_vars, timeout=hit_timeout,
                     label="断[A] 注入 命令=合闸·实测=分闸(不符) ⇒ 等 %s(Recd_RelayFail 调用点, "
                           "读 stat / 命令 / g_RelayFlg)" % (_bptxt(bp_call)),
                     crit="⑥",
                     falsify="命令与实测不符也不进比对 ⇒ 走不到 %s" % _bptxt(bp_call))
    t_diff1 = read_clock(ser, chip="管理芯", quiet=True)
    if r_diff is not None:
        _dv = r_diff.get("vars") or {}
        print("      断[A] 不符那一趟: ok=%s | %s" % (r_diff.get("ok"), r_diff.get("detail")))
        print("      停时读到: %s" % _dv)
        for _ln in (r_diff.get("injects") or []):
            print("      注入账: %s" % _ln)
        print("      注入前那一停读到: %s" % (r_diff.get("at_vals") or {}))
        _cmd_v = gdb_ints(_dv.get("g_RelayCmd[0]"))
        _flg_v = gdb_ints(_dv.get("g_RelayFlg"))
        _stat_got = gdb_bool(_dv.get("stat"))
        _stat_hope = rfl_stat_expect(_dv.get("g_RelayCmd[0]"), _dv.get("g_RelayFlg"), None)
        add("断[A] :501-514 现算的 stat 与「命令 + g_RelayFlg 低 4 位」现算值相同(且走的是合闸方向那两条)",
            (_stat_got == _stat_hope) if (_stat_got is not None and _stat_hope is not None
                                          and _cmd_v == [RFL_CMD_ON]) else None,
            "读到 stat=%s; 命令=%s g_RelayFlg=%s ⇒ 现算应为 %s"
            % (_dv.get("stat"), _cmd_v or "读不到", _flg_v or "读不到", _stat_hope),
            crit="⑥", obs=judge.DEBUG,
            falsify="固件不按 :501-514 那四条规则算 stat(如直接比 g_RelaySta / 比 g_RelayFlg 整字节 / "
                    "方向搞反)⇒ 读回的 stat 与现算值不同")
    else:
        add("断[A] 现算的 stat 与四条规则一致", None,
            "没有调试会话(或用户指定只做黑盒)⇒ 注入观测这一次没做成(注入没有可降级的黑盒替身)",
            crit="⑥", obs=judge.DEBUG,
            falsify="不做这一次时无从判 —— 本记录不作为固件证据")

    # ---- ① ③ 串口: 「记录开始」那一笔(序号推进 + 新行未结束 + 时标落窗) ----
    rows_b = read_relayfail_rows(ser, (1, 2, 3), wait=wait)
    by_b = rows_by_seq(rows_b)
    _new_b = sorted(s for s in by_b if s > seq0)
    seq_b = _new_b[-1] if _new_b else None
    t_b = read_clock(ser, chip="管理芯", quiet=True)
    add("串口 负荷开关误动作序号推进(新落「记录开始」行)且新行未结束",
        None if not by_b else (bool(_new_b) and by_b[seq_b]["t_end"] is None),
        "序号 %s → %s(新行 %s; 新行结束时刻=%s)"
        % (seq0, rfl_top(by_b), _new_b or "无",
           (by_b[seq_b]["t_end"] if seq_b else "-") or "(未结束)"),
        crit="①",
        falsify="固件在命令与实测不符时不落「记录开始」⇒ 序号不推进 / 新行结束时刻不为空")
    add("串口 新「记录开始」行的发生时刻落在本次窗口(=当时表钟)",
        lp_in_window(by_b[seq_b]["t_start"] if seq_b else None,
                      t_diff0, clock_add(t_b or t_diff0, 15)),
        "发生时刻=%s; 窗口=[%s, %s+15s]" % (by_b[seq_b]["t_start"] if seq_b else None,
                                            t_diff0, t_b or t_diff0),
        crit="③",
        falsify="时标取的不是当时表钟(Get_MeterTime 写错偏移 / 用了旧时间)⇒ 落窗外")

    # ---- ② 一致: 停 :498 注"一致", 等 :1348(「记录结束」写库位置) ----
    t_same0 = read_clock(ser, chip="管理芯", quiet=True) or t_b or t0
    r_end = None
    if have_wb:
        r_end = inj(bp_judge, RFL_INJ_SAME, watch=bp_wre, watch_vars=wre_vars,
                    at_vars=judge_vars, timeout=hit_timeout,
                    label="断[A] 注入 命令=合闸·实测=合闸(一致) ⇒ 等「记录结束」写库位置 %s"
                          % (_bptxt(bp_wre)),
                    crit="②",
                    falsify="固件没有「记录结束」这条路径 ⇒ 命令与实测恢复一致后走不到 %s" % _bptxt(bp_wre))
    t_same1 = read_clock(ser, chip="管理芯", quiet=True)
    if r_end is not None:
        print("      断[A] 一致那一趟: ok=%s | %s" % (r_end.get("ok"), r_end.get("detail")))
        print("      停时读到: %s" % (r_end.get("vars") or {}))
        for _ln in (r_end.get("injects") or []):
            print("      注入账: %s" % _ln)
        print("      注入前那一停读到: %s" % (r_end.get("at_vals") or {}))
        add("断[A] 命令与实测恢复一致 ⇒ 落到「记录结束」写库位置(%s)"
            % (_bptxt(bp_wre)), r_end.get("ok"),
            r_end.get("detail") or "没命中 —— 未证", crit="②", obs=judge.DEBUG,
            falsify="固件不走「记录结束」支, 或那一行已有头有尾被守卫拦掉 ⇒ 不会停到 %s" % _bptxt(bp_wre))
    else:
        add("断[A] 命令与实测恢复一致 ⇒ 落「记录结束」一笔", None,
            "没有调试会话(或用户指定只做黑盒)⇒ 白盒这一次没做成", crit="②", obs=judge.DEBUG,
            falsify="不做这一次时无从判 —— 本记录不作为固件证据")

    # ---- ② ③ 串口: 上一行的结束时刻被补上, 序号不推进 ----
    rows_c = read_relayfail_rows(ser, (1, 2, 3), wait=wait)
    by_c = rows_by_seq(rows_c)
    _row_b = by_c.get(seq_b) if seq_b is not None else None
    _t_end_b = (_row_b or {}).get("t_end")
    _win_c = (t_same0, clock_add(t_same1 or t_same0, 12))
    add("串口 「记录开始」行(序号=%s)的结束时刻被补上 = 落了「记录结束」一笔(未新开行)" % seq_b,
        None if _row_b is None else (_t_end_b is not None and lp_in_window(_t_end_b, *_win_c)),
        "结束时刻=%s; 注入窗口=[%s, %s+12s]" % (_t_end_b or "(未结束)", _win_c[0], t_same1 or t_same0),
        crit="②",
        falsify="固件不把结束时刻补进上一行(或另开一行)⇒ 该行结束时刻仍为空 / 序号多推进一次")
    add("串口 序号在「记录结束」后不推进(结束不新开一行)",
        (None if (not by_c or seq_b is None) else (max(by_c) == seq_b)),
        "最大序号 %s(与「记录开始」后应同为 %s)" % (rfl_top(by_c), seq_b),
        crit="②",
        falsify="结束也新开一行 ⇒ 最大序号再推进一格")
    add("串口 「记录开始」行的结束时刻落在本次窗口(=当时表钟)",
        lp_in_window(_t_end_b, *_win_c),
        "结束时刻=%s; 窗口=[%s, %s+12s]" % (_t_end_b or "(未结束)", _win_c[0], t_same1 or t_same0),
        crit="③",
        falsify="结束时刻取的不是当时表钟(Get_MeterTime 写错偏移)⇒ 落窗外")

    # ---- ① ④ 不符·二次: 停 :498 注"不符", 等 :1330 读 buff 与 g_RelaySta ----
    r_start = None
    # ⚠ 同上: ③ 窗口的下界读在触发之前(2026-09-21 首跑: 读在 inj 之后 ⇒ 发生时刻 08:55:07
    #   落进窗口 [08:55:08, …], 与上一段同病)。
    t_start0 = read_clock(ser, chip="管理芯", quiet=True) or t_same1 or t0
    if have_wb:
        r_start = inj(bp_judge, RFL_INJ_DIFF, watch=bp_wrs, watch_vars=wrs_vars,
                      at_vars=judge_vars, timeout=hit_timeout,
                      label="断[A] 再注入 命令=合闸·实测=分闸(不符) ⇒ 等「记录开始」写库位置 %s"
                            "(读 buff 与 g_RelaySta)" % (_bptxt(bp_wrs)),
                      crit="①",
                      falsify="固件不走「记录开始」支 ⇒ 走不到 %s" % _bptxt(bp_wrs))
    t_start1 = read_clock(ser, chip="管理芯", quiet=True)
    if r_start is not None:
        _sv = r_start.get("vars") or {}
        print("      断[A] 二次不符那一趟: ok=%s | %s" % (r_start.get("ok"), r_start.get("detail")))
        print("      停时读到: g_RelaySta=%s" % _sv.get("g_RelaySta"))
        for _ln in (r_start.get("injects") or []):
            print("      注入账: %s" % _ln)
        print("      注入前那一停读到: %s" % (r_start.get("at_vals") or {}))
        add("断[A] 命令与实测不符 ⇒ 落到「记录开始」写库位置(%s)" % (_bptxt(bp_wrs)),
            r_start.get("ok"), r_start.get("detail") or "没命中 —— 未证",
            crit="①", obs=judge.DEBUG,
            falsify="固件不走「记录开始」支 ⇒ 不会停到 %s" % _bptxt(bp_wrs))
        # ④ 操作后开关状态: `:1328 buff[12] = (TRUE == Get_RelaySta())? 0: 1;`
        #   `Get_RelaySta()` 的正文就是 `return g_RelaySta;`(TaskRelay.c:1308-1325)⇒ 读全局量即是那一刻的值。
        _bv = gdb_bytes(_sv.get("buff"))
        _b12 = _bv[12] if len(_bv) > 12 else None
        _sta = gdb_bool(_sv.get("g_RelaySta"))
        _want = None if _sta is None else (0 if _sta else 1)
        add("断[A] ④a 「操作后开关状态」buff[12] 与当时的开关状态量同向",
            (_b12 == _want) if (_b12 is not None and _want is not None) else None,
            "buff[12]=%s; g_RelaySta=%s ⇒ 应为 %s; buff 长 %d"
            % (_b12, _sv.get("g_RelaySta"), _want, len(_bv)),
            crit="④a", obs=judge.DEBUG,
            falsify="固件不按 g_RelaySta 现写那一位(写反 / 写错偏移 / 沿用上一笔)⇒ buff[12] 与现算值不同")
    else:
        add("断[A] 命令与实测不符 ⇒ 落「记录开始」一笔", None,
            "没有调试会话(或用户指定只做黑盒)⇒ 白盒这一次没做成", crit="①", obs=judge.DEBUG,
            falsify="不做这一次时无从判 —— 本记录不作为固件证据")
        add("断[A] ④a 「操作后开关状态」buff[12] 与当时的开关状态量同向", None,
            "没有调试会话(或用户指定只做黑盒)⇒ 白盒这一次没做成", crit="④a", obs=judge.DEBUG,
            falsify="不做这一次时无从判 —— 本记录不作为固件证据")

    # ---- ① ③ 串口: 第二次「记录开始」那一笔 ----
    rows_d = read_relayfail_rows(ser, (1, 2, 3), wait=wait)
    by_d = rows_by_seq(rows_d)
    _new_d = sorted(s for s in by_d if s > rfl_top(by_c))
    seq_d = _new_d[-1] if _new_d else None
    t_d = read_clock(ser, chip="管理芯", quiet=True)
    add("串口 第二次「记录开始」: 序号再推进一格且新行未结束",
        None if not by_d else (bool(_new_d) and by_d[seq_d]["t_end"] is None),
        "序号 %s → %s(新行 %s; 新行结束时刻=%s)"
        % (rfl_top(by_c), rfl_top(by_d), _new_d or "无",
           (by_d[seq_d]["t_end"] if seq_d else "-") or "(未结束)"),
        crit="①",
        falsify="第二笔「记录开始」不落库 ⇒ 序号不再推进 / 新行结束时刻不为空")
    add("串口 第二次「记录开始」行的发生时刻落在本次窗口(=当时表钟)",
        lp_in_window(by_d[seq_d]["t_start"] if seq_d else None,
                      t_start0, clock_add(t_d or t_start0, 15)),
        "发生时刻=%s; 窗口=[%s, %s+15s]" % (by_d[seq_d]["t_start"] if seq_d else None,
                                            t_start0, t_d or t_start0),
        crit="③",
        falsify="时标取的不是当时表钟 ⇒ 落窗外")

    # ---- ② 末段: 再落一次「记录结束」, 让跑完的记录是完整的、g_FailStat[0] 停在偶数 ----
    r_end2 = None
    if have_wb:
        r_end2 = inj(bp_judge, RFL_INJ_SAME, watch=bp_wre, watch_vars=wre_vars,
                     at_vars=judge_vars, timeout=hit_timeout,
                     label="断[A] 末段: 注入 命令=合闸·实测=合闸(一致) ⇒ 等「记录结束」写库位置 %s"
                           "(跑完记录完整、g_FailStat[0] 停在偶数)" % (_bptxt(bp_wre)),
                     crit="②",
                     falsify="固件不走「记录结束」支(或那一行已有头有尾被守卫拦掉)⇒ 走不到 %s" % _bptxt(bp_wre))
    t_last = read_clock(ser, chip="管理芯", quiet=True)
    rows_e = read_relayfail_rows(ser, (1, 2, 3), wait=wait)
    by_e = rows_by_seq(rows_e)
    if r_end2 is not None:
        print("      断[A] 末段一致: ok=%s | %s" % (r_end2.get("ok"), r_end2.get("detail")))
        print("      注入前那一停读到: %s" % (r_end2.get("at_vals") or {}))
        add("断[A] 末段一致落到「记录结束」写库位置(%s)" % (_bptxt(bp_wre)),
            r_end2.get("ok"), r_end2.get("detail") or "没命中 —— 未证",
            crit="②", obs=judge.DEBUG,
            falsify="固件不走「记录结束」支 ⇒ 不会停在 %s" % _bptxt(bp_wre))
    else:
        add("断[A] 末段一致落「记录结束」一笔", None,
            "没有调试会话(或用户指定只做黑盒)⇒ 白盒这一次没做成", crit="②", obs=judge.DEBUG,
            falsify="不做这一次时无从判 —— 本记录不作为固件证据")
    add("串口 末段「记录结束」把第二笔的结束时刻补上(序号不推进)",
        None if seq_d is None or by_e.get(seq_d) is None
        else (by_e[seq_d]["t_end"] is not None and rfl_top(by_e) == rfl_top(by_d)),
        "序号 %s → %s; 该行结束时刻=%s"
        % (rfl_top(by_d), rfl_top(by_e),
           ((by_e.get(seq_d) or {}).get("t_end") or "(未结束)") if seq_d is not None else "-"),
        crit="②",
        falsify="末段「记录结束」没补上尾巴 / 另开了一行 ⇒ 结束时刻仍为空 / 序号又推进")

    # ---- crit=None 的参考记录(答不出 falsify, 不进判据计数, 只进日志) ----
    _e2 = by_e.get(rfl_top(by_e)) if by_e else None
    add("参考: 跑完最新一条负荷开关误动作记录的形状(末段停在「记录结束」⇒ 应当是有头有尾)",
        None, "序号=%s 发生=%s 结束=%s" % ((_e2 or {}).get("seq"), (_e2 or {}).get("t_start") or "-",
                                          (_e2 or {}).get("t_end") or "(未结束)"))
    add("参考: 表钟在本次窗口内正常走时(记录时标可与之对拍)", None,
        "起=%s 末=%s" % (t0, t_last))
    if r_end2 is not None:
        _fv = (r_end2.get("at_vals") or {})
        add("参考: 末段注入前那一停的 g_FailStat[0]=%s(跑完它停在偶数 ⇒ temp 为真 = 「无故障」)"
            % _fv.get("g_FailStat[0]"), None,
            "该值由固件 :523-528 自己写回参数区, `_restore_all.py` 不还原它 —— 见段头「副作用」")

    if not have_wb:
        scope = "仅黑盒(用户指定)" if wb_waived else "仅黑盒(无调试会话)"
    else:
        scope = "黑盒+白盒"
    n_fail = sum(1 for r in recs if r["ok"] is False)
    n_tbd = sum(1 for r in recs if r["ok"] is None)
    print("===== 5-11 负荷开关误动作: %d 条证据(FAIL=%d 没做成=%d)  [本次范围: %s] ====="
          % (len(recs), n_fail, n_tbd, scope))
    details = ["%s: %s" % (r["name"], r["detail"]) for r in recs]
    if not have_wb:
        details.append("未做: 断点观测(白盒) —— %s; 要证『两条写库路径 / 现算规则 / 开关状态位』"
                       "需接 J-Link 重跑(断点见 ledger.md 5-11 的 F 列)"
                       % ("用户本次指定只做黑盒" if wb_waived else "本次无调试会话"))
    details.append("未证: 「真电压下硬件反馈不跟随」那一半 —— 本台交流 0V, g_RelayFlg 恒分闸、"
                   "75%%Un 窗口常闭, 「命令≠实测」这个状态是**注入造出来的**; 要证须把台面电压加到 "
                   "≥75%%Un(本台 ≈165V) 让开关状态真的与命令不符")
    details.append("未证: 「拉闸方向」那两条现算规则(TaskRelay.c:501/:506)与 :461-480 那半支限次重发 —— "
                   "TAB_Function.againrcd == TRUE 只对合闸方向免掉 g_FailStat[0] < 2; 要证须先把参数区的 "
                   "againrcd 改成 FALSE(动参数区)。判据⑥ 因此只覆盖合闸方向那两条")
    details.append("未证: 「低 4 位是混合态 ⇒ stat 沿用 temp」那一路(:508/:513) —— 本项注入的两张表都把低 4 位"
                   "铺成全 0 或全 1, 走不到混合态")
    return recs, details, scope


# 规格 = ledger.md 4-7。**前提订正(2026-09-16)**: 规格的 E/H/I/K 四列都写「本台非本地表 ⇒
#   `TaskFreeze.c:875-878` 直接返回不产生」, 那是**错的**。两条独立证据:
#     ① 源码 `Config/MengXi/UserCfg.h:102` 走的是 `#define Local_Meter` 那条活动分支,
#        `:344-345` 定义 `TP_Remote 0` / `TP_Local 1`;
#     ② 从 `.out` 直读 `TAB_MeterSty.style` 得 **1**(== TP_Local)。
#   ⇒ 风格判定在本台是**放行**的, 判定体与写点 `:1041`/`:1075` 都可达 ⇒ **本项整项可测**,
#     不再只是"证被拦掉"那半支。同一条订正一并废掉本文件 3-x 段头那句「本台非本地表,
#     故 11-1/11-2 死、3-2 活」—— 11-1/11-2 与 4-7 共用 `Check_BillFrezY`, 同样可测。
#
# 断点(2026-09-16 离线实核 gdb `info line`, 逐条记地址):
#   `TaskFreeze.c:875`  → 0x32e80 <Check_BillFrezY+16>   风格判定所在, **有码**
#   `TaskFreeze.c:877`  → **无独立机器码**(那条 return 并进 :875 的区间) ⇒ **不可作断点**
#   `TaskFreeze.c:1041` → 0x332a2 <Check_BillFrezY+1074> 月支写点
#   `TaskFreeze.c:1075` → 0x3338c <Check_BillFrezY+1308> 年支写点
#
# 触发通道 = **自然分钟步进**, 零写动作、不碰表钟:
#   `TaskTime.c:287` 分钟一变就 `Post_Message(ID_TaskFreeze, MSG_MinStep)` ⇒ `Run_TaskFreeze`
#   (`TaskFreeze.c:105`, 汇合点 :112-118)⇒ `Check_BillFrezY` 在 :138 被调(每分钟一次)。
#   跨结算边界那一趟**造不出来**(帧通道没有写 `g_HisTime` 的口) ⇒ 走**注入**:
#   停在 :887 改 `buff`(那份参数拷贝)与 `g_HisTime` 的月/年 ⇒ 固件照自己的判据算出条数并落记录。
#   这一趟结尾 `Copy_Data(g_HisTime, g_CurTime, 6)`(`TaskFreeze.c:143`)会把 `g_HisTime` 刷回真值
#   ⇒ **固件自愈**, 不需要额外还原(注入账仍照记)。
#   `g_HisTime` 的字节序 = `[秒,分,时,日,月,年]`(与 `g_CurTime` 同; 4-4 的 J 列有同类取证) ⇒ 年 = 下标 5。
#
# ⚠ **光回退年份造不出边界**(2026-09-16 实探定案):
#   年支除"年份差"之外还要一张**有效的年结算日期表**, 而本台参数区那份是**出厂无效值**
#   (12 个 99) ⇒ 停在 :917 读到 `dateNum=0`、`flag=OTHER` ⇒ `:917 if (dateNum==0) return;`
#   每趟都成立, 月支 :1041 与年支 :1075 **谁也到不了**。所以注入点放在 **:887**
#   (`dateNum = 0;` @0x32eb2 —— 参数刚读完 :883-886、循环 :888 还没跑), 在**同一个停点**写两样:
#   · `buff[0..11]`(那份参数拷贝) 写成一个**有效的年结算日期**;
#   · `g_HisTime` 的月/年(造月份差, 或把差写成 0)。
#   于是 :888 的循环照固件自己的判据算出 `dateNum=1` ⇒ 写点 :1041/:1075 才是活的。
#   ⚠ 4-7 的 ③(年支正向)不走这条路 —— 它靠**拨钟自然跨年**造年份差, 只写 `buff`(`BFY_YEAR_ASSIGN`);
#     回退年字节那一版(`BFY_INJ_ASSIGN`)留给了 11-1。4-7 那几张表见本段 `bfy_neg_trip()` /
#     `bfy_month_assign()`。
#   这不是"改表参数": 改的是**栈上那份拷贝**, 参数区一个字节都没动, 函数返回即消失。
#   `buff` 在 :887 处有位置表区间(`info scope`: 0x32e9c-0x32fc0, $sp+8, 12B) ⇒ 按地址可写;
#   `dateNum` 在那一处**还没有**位置(它的区间从 0x32eb4 起、在 `$r7` 里) ⇒ 别把它写进 at_vars。

BFY_SUBCLASS = P.BFY_SUBCLASS
BFY_STYLE_LOCAL = P.BFY_STYLE_LOCAL
BFY_TABLE_TRIPLE = P.BFY_TABLE_TRIPLE
BFY_PARAM_BYTES = P.BFY_PARAM_BYTES
BFY_INJ_ASSIGN = P.BFY_INJ_ASSIGN
BFY_YEAR_ASSIGN = P.BFY_YEAR_ASSIGN
BFY_MONTH_BUFF_ASSIGN = P.BFY_MONTH_BUFF_ASSIGN
BFY_YEAR_BYTES = P.BFY_YEAR_BYTES
BFY_MONTH_BYTES = P.BFY_MONTH_BYTES
BFY_GATE_VARS = P.BFY_GATE_VARS
BFY_AT_VARS = P.BFY_AT_VARS


BFY_HIS_FIELDS = ("g_HisTime[4]", "g_HisTime[5]")     # `g_HisTime` 的月 / 年两个字节
BFY_BUFF_FIELDS = tuple("buff[%d]" % _i for _i in range(12))
BFY_MONTH_BACK = 2        # ⑤ 月支那一趟把 `g_HisTime` 的月份回退几个月(造 `frezNum == 2`)


def bfy_months_back(gcur, k):
    """`g_CurTime` 的 6 字节往前 `k` 个月 → `(月, 年字节)`; 跨年借位。取不全 → `None`。

    ⚠ 年给的是**字节原值**(2000 起的偏移), 不是 `bfy_ymd` 那种四位年份 —— 这个数要原样写回
      `g_HisTime[5]`。与固件落时标那两句同式(`:1029-1030`): 月减到 0 以下就 +12、年 −1。
    """
    if not gcur or len(gcur) < 6:
        return None
    m, y = gcur[4] - k, gcur[5]
    while m <= 0:
        m += 12
        y -= 1
    return m, y & 0xFF


def _bfy_his_assign(my):
    """`(月, 年字节)` → 写 `g_HisTime` 那两格的一对注入项。"""
    return [(BFY_HIS_FIELDS[0], str(my[0])), (BFY_HIS_FIELDS[1], str(my[1]))]


def _bfy_neg_head(kind, gcur):
    """② 那两趟各自要注进去的 12 字节表的第 0 组 `(时, 日, 月)` —— 固件认它**有效**、而按这一刻
    表钟**还没走到**的那个结算点。后 3 组一律留 99(`:911` 那支对非第 0 组的三元组不做)。

    月支那个固定 `(0, 1, 99)`: 月那一格 99 落月结算形态(`:891` 收 时 ≤ 23 且 日 1..28), 而它的
    比较只带 `时 | 日<<8` —— 日 1 早被当月跨过, 两个端点于是都比它大。
    年形态那个要与表钟**同月往后**排: 当月不是 12 月就取下一月 1 日 0 点; 是 12 月就取本月里比
    当天更晚的一天; 12 月 31 日 23 时之后再没有更晚的时点 ⇒ `None`(那一趟不做)。
    """
    if kind == "month":
        return (0, 1, 99)
    h, d, m = gcur[2], gcur[3], gcur[4]
    if m < 12:
        return (0, 1, m + 1)
    if d < 31:
        return (0, d + 1, 12)
    if h < 23:
        return (23, 31, 12)
    return None


def bfy_neg_trip(kind, gcur, ghis):
    """② 一趟否定期望的**整张注入表** + 离线算出的 `(dateNum, flag, frezNum)`; 取不全 → `None`。

    `kind` 认 `"year"`(年支那一趟) 与 `"month"`(月支那一趟)。两趟注入的表**都是固件认它有效**
    的 —— 出厂那份 12 个 99 的参数表会让 `:917` 每趟早退, 两个写点对**任何**固件都不命中, ② 于
    是成了恒真(这正是本次改动要拆掉的)。

    注入同时把 `g_HisTime` 的月/年改成表钟所在的月年 ⇒ 月份差为 0。不这么做的话那一趟本就有条数
    要写, "写点没命中"就判不出是"还没走到"还是"被别的什么挡住了"。

    ⚠ 返回的那个 `frezNum` 是**照固件算式离线算的**, 调用方拿它当"这一趟确实还没走到"的凭据:
      `dateNum ≥ 1 且 frezNum == 0`。算不出 0 就**不许硬跑** —— 那样跑出来的"写点没命中"与
      "表注错了"在账本里长得一样(本仓最忌讳的那类)。
    """
    head = _bfy_neg_head(kind, gcur) if gcur and len(gcur) >= 6 else None
    my = bfy_months_back(gcur, 0)
    if head is None or my is None or not ghis or len(ghis) < 6:
        return None
    b12 = tuple(head) + (99,) * 9
    chk = bfy_boundary_check(b12, gcur, bytes(ghis[:4]) + bytes((my[0], my[1])))
    if chk is None:
        return None
    assigns = [("buff[%d]" % i, "%d" % v) for i, v in enumerate(b12)] + _bfy_his_assign(my)
    return (assigns, chk)


def bfy_boundary_check(b12, gcur, ghis):
    """一张 12 字节表 + 那一刻的 `g_CurTime`/`g_HisTime` → `(dateNum, flag, frezNum)`; 取不全 → `None`。

    照 `TaskFreeze.c:888-995` 把固件那一趟会算出的三条离线算一遍。② 要的是
    **`dateNum ≥ 1`(表有效、固件认) 而 `frezNum == 0`(那一刻还没走到结算点)** —— 不核这一下的
    话, 注错了值(日超了那个月的天数、月份差没写成 0)会让固件照旧早退, 于是"两个写点都没命中"
    对**任何**固件都成立, ② 又成了恒真。
    """
    if not b12 or not gcur or not ghis or len(gcur) < 6 or len(ghis) < 6:
        return None
    dates, flag = _bfy_dates_from_bytes(b12)
    if not dates:
        return (0, flag, 0)
    c = (gcur[2], gcur[3], gcur[4], gcur[5])
    h = (ghis[2], ghis[3], ghis[4], ghis[5])
    n = _bfy_year_freznum(c, h, dates) if flag else _bfy_month_freznum(c, h, dates)
    return (len(dates), flag, n)


def bfy_month_assign(gcur, back=BFY_MONTH_BACK):
    """⑤ 月支那一趟的整张注入表: 月结算日期表 + `g_HisTime` 月份回退 `back` 个月。

    ⚠ ⑤ 那一趟回退**两个月**而不是一个月: 差 1 时固件算出来的条数就是 1, "条数按差值算"这件事
      测不出来 —— 固定写一条的固件也过。差 2 才逼出"同一趟连写两条", 两条各自的时标于是也成了判据。
    ⚠ `back` 是给 ⑨ 造环回用的: 那一趟要落到固件的**一次补冻上限**(`TAB_FrezAdd[7]` ⇒
      `CURRENT.BILLFREZY_ADD`)上 —— 每轮落这么多条, 6 格才真的装不下。
    """
    my = bfy_months_back(gcur, back)
    return None if my is None else (BFY_MONTH_BUFF_ASSIGN + _bfy_his_assign(my))


def bfy_step_inject_allow():
    """4-7 三个注入表要写的表达式名字(值随表钟变, 名字不变)。"""
    return BFY_HIS_FIELDS + BFY_BUFF_FIELDS



# 每月天数 —— 只为**核注入值**而抄的一份(`TaskFreeze.c:891` 的 `TAB_DayOfMonth[]`)。
# 我们注进去的月是 9(30 天), 两种抄法都一样; 换注入月时再看它二月是不是 29, 不然这条离线
# 判据会比固件松一格。
_BFY_DOM = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30, 7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}


def _bfy_dates_from_bytes(b):
    """把那 12 字节表按 `TaskFreeze.c:888-915` 的判据算出 `date[]` 与 `flag` —— **纯函数**。

    照抄固件的三条合法性判据(时 ≤ 23 / 日 ≥ 1 且 ≤ `TAB_DayOfMonth[月]` / 月 1..12; 第 0 组
    若只满足"日 ≤ 28"则按**月结算**形态收、且立刻停)。`TAB_DayOfMonth` 用真实月份天数(`_BFY_DOM`)。
    用它离线核"注进去的那份表固件认不认、算出几条", 免得注错了值只看到一句"写点没命中"。
    """
    dates, flag = [], 0
    for i in range(len(b) // 3):
        t, d, m = b[3 * i], b[3 * i + 1], b[3 * i + 2]
        if t <= 23 and 1 <= d <= _BFY_DOM.get(m, 0) and 1 <= m <= 12:
            dates.append(t | d << 8 | m << 16)
            flag = 1                      # TRUE = 年结算形态
        elif i == 0:
            if t <= 23 and 1 <= d <= 28:
                dates.append(t | d << 8)
                flag = 0
            elif t == 99 and d == 99 and m == 99:
                continue
            break
    return dates, flag


def _bfy_year_freznum(cur, his, dates):
    """年支那一趟的 `frezNum`(照 `TaskFreeze.c:977-995`, 纯函数)。`cur`/`his` = (时,日,月,年)。

    值为 0 ⇒ 那一趟**不写记录**(`:1000 if (frezNum != 0)`), 写点 :1075 也就不会命中。
    这个函数的作用是离线核"注进去的年/表到底造不造得出边界" —— 造不出时 ③ 只会报"没命中",
    而"没命中"与"注入值不对"在账本里长得一样(本仓最忌讳的那类)。
    """
    n = (cur[3] - his[3]) * len(dates)
    for d in dates:
        if d <= (cur[0] | cur[1] << 8 | cur[2] << 16):
            n += 1
        if d <= (his[0] | his[1] << 8 | his[2] << 16):
            n -= 1
    return n


def _bfy_month_freznum(cur, his, dates):
    """月支那一趟的 `frezNum`(照 `TaskFreeze.c:963-973`, 纯函数)。`cur`/`his` = (时,日,月,年)。

    两处与年支不同, 抄的时候别顺手拉齐:
      · 月份差由 `Diff_Months`(`Platform/DateTime.c`) 给, 取**绝对值**(它两边都乘 12 再相减),
        所以 `diffMon`/`dateStr` 那套建立在"回退出来的月份差是正的"上面;
      · `date[0]` 只带 `时 | 日<<8`、**不带月** —— 月形态那个 99 的月那一格根本不进比较。
    """
    n = abs((cur[3] * 12 + cur[2]) - (his[3] * 12 + his[2]))
    ch, hh = cur[0] | cur[1] << 8, his[0] | his[1] << 8
    for d in dates:
        if d <= ch:
            n += 1
        if d <= hh:
            n -= 1
    return n

# 两个写点那一刻可读的量 —— 由 `info scope Check_BillFrezY` 的位置表逐区间核过(2026-09-16):
#   两处都落在 `frezNum`/`buff`/`obj`/`date` 的同一个可读区间 `0x331ec-0x33418` 里;
#   ⚠ `flag`/`over`/`flg`/`off` 在两处的**空洞区间**内(`0x33224-0x3341c` 等) ⇒ 读不回来,
#     别把它们写进 VARS 里 —— 那会得到一句"读不到", 而它看起来像"固件没给值"。
#   `buff[0..5]` = 本次冻结的时标 `[秒分时日月年]`(4-4 的 J 列有同类取证)。
BFY_WRITE_VARS = P.BFY_WRITE_VARS
BFY_NEG_WIN = P.BFY_NEG_WIN
BFY_SAME_WIN = P.BFY_SAME_WIN
BFY_WAIT = P.BFY_WAIT
BFY_INJ_WAIT = P.BFY_INJ_WAIT
BFY_CROSS_TRIES = 3                      # 拨钟之后认跨年那一趟至多等几个自然分钟步进
                                         # (校时那趟自己也会走到同一个停点, 要放它过去)


BFY_FALSIFY = {
    "①": "风格判定方向反了(本台实为 TP_Remote) ⇒ 判定体在风格判定那两句就 return, 它之后那条"
         "指令一条都不执行、写点永不命中",
    "②": "边界判定无条件成立 ⇒ 没有跨结算边界也落记录: 注进一张『那一刻还没走到』的结算日期表"
         "之后, 月支/年支写点仍命中, 或 698 的阶梯结算冻结记录(子类 0x11)记录序号自己推进",
    "③": "表钟自然跨到次年 1 月 1 日 0 点之后仍不进年支(年份差算不出来、或被 `:917` 早退挡掉) "
         "⇒ 年支写点不命中, 或命中那刻 `frezNum` 不等于 1",
    "④": "跨年那一趟的写点被走到了, 记录却不按跨过的结算点落: 序号**不是恰加 1**(不推进, 或一趟"
         "结转了好几条), 或最新一条的时标不是次年 1 月 1 日 0 点(落成了当场那一刻)",
    "⑤": "跨月边界造出来之后不进月支(边界判定被短路、或那趟落进了年支) ⇒ 月支写点不命中、只停一次, "
         "或命中那刻 `frezNum` 不等于 2, 或两次 `buff[0..5]` 不是 0/0/0/1/上一个月/当年 与 "
         "0/0/0/1/当月/当年(条数不按差值算、或两条的时标写成同一个)",
    "⑥": "月支那一趟之后 698 读回不推进两格, 或最新一条的时标不是当月 1 日 0 点"
         "(与那一刻 `buff[0..5]` 解出的结算点对不上)",
    "⑦": "出厂冻结对象表的第 10 行不是规范要的那个阶梯结算记录对象, 或对象号查错了表 ⇒ 逐项比当场不符",
    "⑧": "出厂存储信息的深度不是 6(容量被改过), 或 698 读回能读出第 7 格 ⇒ 与规范『应可存储 6 次』对不上",
    "⑨": "写满之后停止写(序号不再推进), 或第 7 条落在第 7 格而不是顶掉第 1 格, 或最旧那一格原地不动"
         " ⇒ 环形覆盖没实现",
}


def billfrez_y_criteria():
    """4-7 的**预设条目**(源 = ledger.md 4-7 的「观察与判据」I 列 + 操作步骤 H 列)。测试前定死。

    ⚠ 规格 I 列原文两条是「非本地表不产生; 本地表在年/阶梯结算边界正确生成并结转」。**非本地表
      那半支在本台不适用** —— 本台是本地表(见本节头的前提订正), 风格判定放行, 所以"不产生"要证的
      对象换成了**没有跨边界时也不产生**(②); 而"被风格判定拦掉"这件事在本台**证不了**(需要一块真
      TP_Remote 的表), 不列条目、也不假称证过。

    ⚠ 九条都**可证**(①②③⑤ 要停核的断点观测, ④⑥ 纯黑盒, ⑦⑧⑨ 走探针直读与 AA80、都不停核),
      故没有 `unprovable` 条目。台面没接 J-Link 时 ①②③⑤⑦⑨ 落 `ok=None`(没做成)→ 未定论 ——
      那与"固件不生成"是两件事, 不许混
      (④⑥ 的判据本身是黑盒的, 但它们的前提是 ③⑤ 真把边界造出来; 前提不在同样记 `ok=None`)。

    ⚠ ③④ 与 ⑤⑥ 是**同一条记录的两个支**: 本台年结算日期表是出厂无效值, 两支的边界都得自己造
      (`:917 if (dateNum == 0) return;` 每趟早退) —— 年支靠**拨钟自然跨年**, 月支靠**注入月份差**。
    """
    return {
        "①": "风格判定方向: `Check_BillFrezY` 判定体真被走到(风格判定放行、走到它后面那句 "
             "`Read_ParaData`), 且那刻 `TAB_MeterSty.style == TP_Local`(=1) —— 本台是**放行**",
        "②": "没有跨结算边界时不产生: 年支一趟、月支一趟各注入一张固件认它有效、但按那一刻表钟"
             "**还没走到**的结算日期表(月份差写成 0), 月支/年支两个写点都不命中, 且 698 的"
             "阶梯结算冻结记录(子类 0x11)记录序号不推进",
        "③": "跨年结算边界上生成: 拨表钟到 12 月 31 日 23:59:30 等它自然跨到次年 1 月 1 日 0 点, "
             "在 `buff` 刚读进来、日期循环还没跑的那一停把 `buff` 写成有效的年结算日期表"
             "(时 0 / 日 1 / 月 1) ⇒ 年支写点命中, 那刻 `frezNum == 1`",
        "④": "698 读回: 跨年那一趟之后阶梯结算冻结记录最新一条的序号**恰加 1**(一次结转只落一条), "
             "且时标 == 跨过的那个结算点(次年 1 月 1 日 0 点), 不是当场那一刻",
        "⑤": "跨月结算边界上生成: 拨回真实时间后, 在同一个停点把 `buff` 写成只带时日的月结算日期表"
             "(时 0 / 日 1 / 月 99)、并把 `g_HisTime` 的月份回退两个月(这一改只用来造 `frezNum` 的"
             "月份差)⇒ 月支写点**连停两次**(这一趟写两条), 每次都 `frezNum == 2`, 且两次 "
             "`buff[0..5]` 分别是 0/0/0/1/上一个月/当年 与 0/0/0/1/当月/当年",
        "⑥": "698 读回: 月支那一趟之后最新一条的序号**推进两格**, 且时标 == 当月 1 日 0 点",
        "⑦": "TAB_FrezObj 第 10 行翻出的 OAD == 规范要的那 1 项(阶梯结算冻结的记录对象: 805 → "
             "20320200), 第 1 槽起为空(0xFFFF)",
        "⑧": "阶梯结算冻结的存储深度 == 6(与规范『应可存储 6 次』一致), 且 698 读回子类 0x11 的 "
             "pos 1..6 各有记录、pos 7 读不到",
        "⑨": "写满 6 格之后环回顶掉最早那条(不是停止写): 再跨 2 次月结算边界、每轮落 4 条(固件一次"
             "补冻上限)之后, 记录区最早那几条被顶掉(原先那几格换人), 且格数仍是 6、序号单调递增不回退",
    }


def bfy_inject_allow():
    """本子项要注入的表达式名字 —— 脚本构造会话时喂 `inject_allow=`(库**不替脚本开会话**)。

    白名单按**名字**点名、不按值: 改注入什么值(今天年份 −1, 明天换个方向)名单一个字都不用动。
    """
    return tuple(e for e, _v in BFY_INJ_ASSIGN)


def bfy_style_txt(v):
    """断点读回的 `TAB_MeterSty.style` → **三态** True(本地) / False(非本地) / None(读不懂)。

    它是 `INT8U`, gdb 印数字(`0x01`/`1`/`'\\001'`); 优化后也可能印枚举名。**读不懂返 None
    (没做成), 不猜** —— 拿"我看不懂"当"判定拦掉了"会造出假 FAIL(与 `relay_act_ok` 同一条口径)。
    """
    s = str(v if v is not None else "").strip()
    if not s or "optimized out" in s or "No symbol" in s:
        return None
    up = s.upper()
    if "TP_LOCAL" in up:
        return True
    if "TP_REMOTE" in up:
        return False
    nums = re.findall(r"0x[0-9a-fA-F]+|\d+", s)
    if not nums:
        return None
    # ⚠ 逐个试、**别用 `int(tok, 0)`**(2026-09-16 实跑修): gdb 对枚举/字符型印的是
    #   `1 '\001'` 这种"值 + 字符形", 那个字符形的转义写成 `\001`(反斜杠+三位数字),
    #   正则抠出来的最后一个数字串是 `001`, 而 `int("001", 0)` **抛 ValueError**
    #   (base=0 不收前导零) —— 被 except 吞成 None ⇒ 判定方向判不了, ① 白记一次"未证"。
    #   改成按进制逐个数、取第一个落在 0/1 的: 值位与字符位(`'\001'` 十进制也是 1)本就同值,
    #   不会读出矛盾; 全都不在 0/1 才算读不懂(如 `2 '\002'`)。
    for tok in nums:
        try:
            n = int(tok, 16) if tok[:2].lower() == "0x" else int(tok, 10)
        except ValueError:
            continue
        if n in (0, 1):
            return n == BFY_STYLE_LOCAL
    return None


def rec_row_txt(row):
    """`read_freeze_row` 的一条读数 → 一行人话(**读不到就明写"读不到"**, 不拿 0 冒充)。

    ⚠ 通用小助手(4-7 与 AA80 冻结打点共用), 不带某一子项的色彩。
    """
    if row is None:
        return "读不到"
    if row.get("answered") is False:
        return "读不到(无应答)"
    if row.get("empty"):
        return "无记录(该记录对象无内容)"
    return "序号=%s 时标=%s" % (row.get("seq"), row.get("ts") or "无1C时标")


def rec_advanced(pre, post):
    """两次记录读数比『有没有推进』 → **三态**: True 有新条 / False 没有 / None 比不出来。

    判据: 序号不同即推进(记录序号由固件递增); 序号都读不到时退回比时标。
    **两边任一读不到 ⇒ None**(没做成), 不拿"读不到"当"没推进"。
    ⚠ "没收到应答"这一种读不到要挡在 `empty` 之前 —— `read_freeze_row` 收不到字节时
      `empty=False`(它没资格断定"该记录对象无内容"), 放它往下走就会拿两个 `seq=None` 比出
      "没推进", 而那正是② 的半支判据 ⇒ 断链会被读成"固件确实没生成记录"。
    """
    if pre is None or post is None:
        return None
    if pre.get("answered") is False or post.get("answered") is False:
        return None
    if post.get("empty") or pre.get("empty"):
        # 由『无记录』变成有一条 = 推进; 两条都无记录 = 没推进; 本来有却变没有 = 异常(记 None)。
        if pre.get("empty") and not post.get("empty"):
            return True
        if pre.get("empty") and post.get("empty"):
            return False
        return None
    a, b = pre.get("seq"), post.get("seq")
    if a is not None and b is not None:
        return a != b
    ta, tb = pre.get("ts"), post.get("ts")
    if ta and tb:
        return ta != tb
    return None


def rec_seq_delta(pre, post):
    """两次记录读数之间**推进了几格** → int 或 None(比不出来)。

    ⚠ 与 `rec_advanced` 分工: 那个答"有没有推进"(一个布尔就够, ② 用它), 这个答"推进了几格"
      —— 4-7 的 ⑥ 要的是「**两**格」(一次写两条), 一个布尔分不出"写了一条"与"写了两条"。
    序号绕回会让差值变负 —— 那正是"不是两格", 如实返回, 不在这里替它圆回来。
    """
    if pre is None or post is None:
        return None
    if pre.get("answered") is False or post.get("answered") is False:
        return None
    if pre.get("empty") or post.get("empty"):
        return None
    a, b = pre.get("seq"), post.get("seq")
    if a is None or b is None:
        return None
    try:
        return int(b) - int(a)
    except (TypeError, ValueError):
        return None


def bfy_neg_ok(adv):
    """**否定期望**半支的条目值: `rec_advanced` 出来的"推进了吗" → "这条判据满足了吗"。

    判据原文是「没有跨结算边界 ⇒ 记录**不**推进」⇒ 两者**反相关**: `adv is False`(没推进)
    才是达成。2026-09-16 实跑暴露: 原先直接把 `adv` 当 `ok` 交给账本, 于是"没推进"记成 FAIL,
    而本台正是因为没跨边界才没记录 —— **拿正确固件记了一条失败**。
    `None`(比不出来)原样传下去: 那是"这一次没做成", 不是"固件不对"(judge 三态)。
    """
    return None if adv is None else (adv is False)


def bfy_pos_ok(adv, premise):
    """**肯定期望**半支的条目值(④): "推进了吗" → "这条判据满足了吗" —— 但要先问**前提在不在**。

    ④ 的判据原文是「跨结算边界那一趟之后, 记录推进(或由无记录变为有一条)」⇒ 与 `bfy_neg_ok`
    正相反, **推进了才算达成**。可它的前提是 ③ 那一次注入**真造出了边界**(年支写点被执行到);
    前提不在(③ 没命中 / 本次没有调试会话)时, 那一趟固件**本来就该不生成** ——
    2026-09-16 实跑里就撞上这一条: ③ 没造出边界, ④ 拿"两边都没记录"记了一条 **FAIL**,
    等于**拿一次没做成的触发判固件错**(与 ② 那条极性错同一个病, 只是方向反的)。
    前提不在 ⇒ `None`(这一次没做成), 不是"固件不对"(judge 三态)。
    """
    if premise is not True:
        return None
    return None if adv is None else (adv is True)


def bfy_at_newyear(gcur):
    """`g_CurTime` 的 6 字节(`[秒,分,时,日,月,年]`)→ 这一趟**是不是跨年那一趟**(已到 1 月 1 日 0 点)。

    拨到 12 月 31 日 23:59:30 之后, 校时那一趟也会走到同一个停点 —— 拿它把"跨年那一趟"从
    "别的那几趟"里挑出来。读不全 → `None`(没做成), 与 `False`(读到了、但不是那一趟)分开。
    """
    if not gcur or len(gcur) < 6:
        return None
    return gcur[1] == 0 and gcur[2] == 0 and gcur[3] == 1 and gcur[4] == 1


def bfy_ymd(gcur):
    """`g_CurTime` 的 6 字节 → `(秒, 分, 时, 日, 月, 2000+年偏移)`; 取不全 → `None`。

    给打印与"期望的月结算点"算算术用。年份字节是 2000 起的偏移, 与 `frez_expect_ts` 同一套基准。
    """
    if not gcur or len(gcur) < 6:
        return None
    return (gcur[0], gcur[1], gcur[2], gcur[3], gcur[4], 2000 + gcur[5])


def bfy_ts_match(ok, got, want):
    """黑盒条目的值(序号推进了吗)与"读回时标 == 期望结算点"合起来 → 三态。

    两边任一没做成 ⇒ `None`; 序号没推进 / 时标对不上 ⇒ `False`。时标是**独立算出来的**
    (由拨钟目标或那一刻读到的表钟), 不是从记录里再读一遍 —— 不然同义反复, 证不出东西。
    """
    if ok is None or want is None or got is None:
        return None
    return bool(ok) and got == want




# ==================== 阶梯电价: 11-1 年阶梯 / 11-2 月阶梯(与 4-7 共用 Check_BillFrezY) ====================
# 两者与 4-7 **同一个入口**(`TaskFreeze.c:875 Check_BillFrezY`)、**同一个子类号**(0x11 阶梯结算冻结)、
# **同一套断点**(:875 风格判定 / :887 注入停点 / :1041 月支写点 / :1075 年支写点), 差别只在
# 那份年结算日期表(`ID_YearCount1`)被固件判成什么形态:
#   · **年形态**(第 0 组三元组的"月"落在 1..12) ⇒ `flag=TRUE` ⇒ 走**年支**写点 `:1075` —— **11-1**;
#   · **月形态**(第 0 组"月"不合法、但"时≤23 且 日 1..28") ⇒ `flag=FALSE` ⇒ 走**月支**写点 `:1041` —— **11-2**。
# 判据: `TaskFreeze.c:888-915` 那段循环(逐字读过, 见 4-7 段头)。**这是"两支各自独立、互不串判定"的根据**:
# 两个写点分处 `if (flag == FALSE)` 的两支, 同一趟只会过一个。
#
# 与 4-7 的**分工**(别把三项测重了):
#   · 4-7 证的是「跨结算边界 ⇒ 判定体走到 ⇒ 年支写点命中 ⇒ 记录推进」;
#   · **11-1** 证的是「**年边界上档位电量这一列**怎么处理」—— 未超档时**结转**(原值写进记录)、
#     超档(`frezNum > frezAdd`)时**清零**;
#   · **11-2** 证的是「**月形态走的是月支**且 `frezNum` 按**月差**算、与年阶梯不混」。
#
# ⚠ **本台的那一列当前值 = 0**(2026-09-16 实测:
#   普通 GET `0x20320200` 与记录里那一列都是 `06 00 00 00 00 00 00`)。**源为 0 时"结转"与"清零"
#   写出来一模一样** —— 所以 11-1 不能只"读一读看变没变", 必须**先注入一个非零的源**再判。
#   注入的两处(都在同一趟里, 靠 `with_inject` 的 `then_assigns` 串起来):
#     · 停 **`:887`** 写:`g_HisTime[5]` 年份回退 + `buff[0..11]` = 一份有效的年结算日期表(造边界);
#     · 等 **`:1008`** 命中, **在那一停**写 `buff[6..9]` = 非零的"源"(`then_assigns`)。
#   `:1008` 是 `if (over == TRUE)` 那一句(`0x331ec <Check_BillFrezY+892>`, 离线核过有独立机器码),
#   那一刻 `Prep_ObjData`(`:1004`)刚把源填进来、清零(`:1012`)与写点(`:1041`/`:1075`)都还没跑。
#   ⇒ 停这儿既能**改源**, 又让固件自己决定清不清。
#
# ⚠ **判"清没清"用指令路径, 不去解析 `over` 的枚举形态**(`over` 是 `TRUE`=170/`FALSE`=85 那一类,
#   gdb 印成 `170 '\252'` 还是 `TRUE` 随版本变; 拿不准就别猜 —— 与 `bfy_style_txt` 同一条分寸):
#   · `TaskFreeze.c:1012 Set_Data(&buff[off], 0x00, 4)` 是**清零那一句本身的地址**(`0x33206`),
#     它**只在 `over == TRUE` 支里**, ⇒ 它命中就是"清零被执行"的硬证、不命中就是"没清";
#   · `off = Sch_FrezObj(obj, 0x20320200)`(`:1010`)返回的是该对象在记录正文里的位置, 本台对象表里
#     只选了这一个 ⇒ 正文 10B = 时标 6B + 这一列 4B ⇒ `off == 6`, 清的正是 `buff[6..9]`。
#
# ⚠ 另一条边界(`:878`): `if ((TAB_FrezAdd[7] == 0) && (normal != TRUE)) return;` ——
#   本台 `TAB_FrezAdd[7] == 4`(非 0) ⇒ 这道判定放行。它也是超档判据里的 `frezAdd`(`:993`):
#   `frezAdd = (TAB_FrezAdd[7] != 0)? TAB_FrezAdd[7]: 1` ⇒ 本台 `frezAdd = 4`。
BFY_FREZ_ADD = P.BFY_FREZ_ADD
_MST_FALSIFY = {
    "①": "风格判定方向反了(本台实为 TP_Remote) ⇒ 判定体在 :875-878 就 return, 月阶梯这一路永不执行",
    "②": "月形态被判成年形态(或整条被 `:917 if (dateNum == 0) return;` 挡掉) ⇒ 月支写点 "
         "`TaskFreeze.c:1041` 不被走到",
    "③": "`frezNum` 按**年差**算(把月形态当成『年差 × 条数』) ⇒ 那一停读到的 `frezNum` "
         "与回退的月数不符",
    "④": "两支串判定(月形态却走年支) ⇒ 年支写点 `TaskFreeze.c:1075` 在那一趟被走到",
    "⑤": "走到月支却没落库 ⇒ 那一趟之后 698 的阶梯结算冻结记录(子类 0x11)记录序号不推进、也没有新记录",
}
# 阶梯结算冻结记录正文尾部那一列 = 「数据结算用电量」(源 `Platform/FrezData.h:76 LEN_BillFrezY=(6+4)`;
# 类型 `DLT698App.c:2186` = T_DoubleLongUn 4B)。判 11-1 的"结转/清零"看的就是它。
ST_OAD_SETTLE_KWH = P.ST_OAD_SETTLE_KWH

ST_RCSD_KWH = rcsd(REC_SEQ_OAD, REC_TIME_OAD, ST_OAD_SETTLE_KWH)


def freeze_settle_kwh_raw(ud):
    """从记录读回应答(已切好的 ud)**取尾部那一列**(数据结算用电量)的**原始编码字节**(bytes) 或 None。

    切法: 序号元素由 `p698.record_seq_at` 定位, 时标再由 `p698.record_time_at` 从序号之后取;
    它后面固定 7B 是 `<年2B大端 月 日 时 分 秒>`(`decode_ts_698` 的判据), 再往后就是那一列。
    ⚠ 两头都不许图省事, 各栽在一个没验过反面的假定上:
      · `rfind(b"\\x06\\x00\\x00\\x00")` —— 3 列布局下那个标记在序号列和这一列各出现一次,
        `rfind` 取最后只能拿到半个标记(2026-09-16 探针实据);
      · `find(b"\\x1c")` —— **序号的低字节自己就可能是 0x1C**(序号 28、284 …),
        从头找命中的是它, 时标被跳过, 整列切错。
    ⚠ 返回的是**原始字节**、不是解出来的数值: 本仓没有这一列的编码表(T_DoubleLongUn 的载荷形态
      未实测校准), 而 11-1 要判的是"**注入的那个非零源有没有被原样留下**", 拿字节比字节就够 ——
      去猜编码反而会造出一个没人核过的解析器。
    """
    b = bytes(ud or b"")
    i = record_seq_at(b)
    j = record_time_at(b, i) if i is not None else -1
    if j < 0 or len(b) < j + 8:
        return None
    return b[j + 8:]


# ---------------------------- 11-1 年阶梯 ----------------------------
# 注入(两处, 同一趟): :887 造跨年边界, :1008 改档位电量那一列的源。
# 年份回退数写成常量 —— 超档那条的判定是 `frezNum > frezAdd`, 而 `frezNum = 年差 × dateNum`,
# 本台 dateNum=1、frezAdd=4 ⇒ **年差 ≥ 5 才超档**。两个场景一个 1、一个 5, 判据离线能算清。
YST_YEAR_BACK_CARRY = P.YST_YEAR_BACK_CARRY
YST_YEAR_BACK_OVER = P.YST_YEAR_BACK_OVER
YST_DOSE_BYTES = P.YST_DOSE_BYTES
YST_DOSE_ASSIGN = P.YST_DOSE_ASSIGN



def year_step_inject_allow():
    """11-1 要注入的表达式名字 —— 与 4-7 **同一份名单**(同一停点 :887、同一批名字)。

    两处注入(`:887` 的年字节+日期表、`:1008` 的 `buff[6..9]`)合起来要点的名字就是这些:
    `buff[6..9]` 本来就含在 `buff[0..11]` 里。白名单按**名字**点名、与值无关, 故两个场景
    (年份 −1 / 年份 −5)与两组写值**共用这一份**。
    """
    return bfy_inject_allow()


def year_step_dose_allow():
    """11-1 `:1008` 那一停补写的名字(`then_assigns`) —— 白名单要并进会话里。"""
    return tuple(e for e, _v in YST_DOSE_ASSIGN)


def _yst_expected(date_bytes, back, frez_add=BFY_FREZ_ADD):
    """**纯函数**: 本台这一趟(时钟同步、只把年份回退 `back` 年)固件会算出的 `frezNum` 与 `over`。

    照 `TaskFreeze.c:975-996`: `frezNum = 年差 × dateNum`, 再对每条 date 按 cur/his 各 ±1;
    **cur 与 his 除年份外同值**(`g_HisTime` 每趟结尾被 `Copy_Data` 刷成 `g_CurTime`) ⇒ 那些 ±1
    两两抵消, 只剩 `back × len(dates)`。返回 `(frezNum, over)`。
    用它离线核"注进去的年数到底超没超档"—— 注错了只会看到一句"写点没命中", 而那与"固件没走这支"
    在账本里长得一样(本仓最忌讳的那类)。
    """
    dates, _flag = _bfy_dates_from_bytes(date_bytes)
    n = int(back) * len(dates)
    return (frez_add if n > frez_add else n), (n > frez_add)


def _yst_raw_kept(raw, dose_bytes=YST_DOSE_BYTES):
    """记录尾部原始字节里**还找不找得到**注入源的特征字节 → 三态 True/False/None(读不到)。

    `dose_bytes` 里的 0 不参与判定(0 到处都是, 没有区分力)。`raw is None`(没读到记录)返 None ——
    那是"这一次没做成", 不是"源没被保留"(judge 三态)。
    """
    if raw is None:
        return None
    return all(any(_b == _x for _x in raw) for _b in dose_bytes if _b)


def year_step_criteria():
    """11-1 的**预设条目**(源 = ledger.md 11-1 的「观察与判据」I 列 + 操作步骤 H 列)。测试前定死。

    ⚠ 规格 I 列原文三条:「年边界档位电量结转正确; 冻结一条; 档状态与规范一致(远程费控不适用)」。
      照 4-7 的口径:
        · 「远程费控不适用」= 本台**是**本地表(4-7 已证 `style==TP_Local`), 那半支在本台不构成条目;
        · 「前提 style==TP_Local」写在 I 列的「判不过/待核」里 ⇒ 它是 ①, 照 4-7 的做法用断点读 style;
        · 「档状态与规范一致」落到**超档那一支**(`:993-996` 截到 frezAdd 并置 `over`) ⇒ ④。
      ⚠ 「结转」那一条**必须靠注入造出一个非零的源**(本台那一列当前值是 0, 结转与清零写出来同形)——
        这不是"证不了", 是**触发通道缺一条**(帧通道没有写 `g_HisTime`、也没有写那一列的口)。
    """
    return {
        "①": "前提: `Check_BillFrezY` 判定体真被走到(`TaskFreeze.c:875` 命中), 且那刻 "
             "`TAB_MeterSty.style == TP_Local`(=1) —— 年阶梯只对本地表成立",
        "②": "没有跨结算边界时不产生: 普通分钟步进里两个写点 `:1041`/`:1075` 都不命中, 且 698 的"
             "阶梯结算冻结记录(子类 0x11)记录序号不推进",
        "③": "未超档那趟(`over == FALSE`)档位电量**结转**: 清零点 `TaskFreeze.c:1012` **不**被执行, "
             "且那趟记录尾部那一列**原样保留**当时的值(注入源的特征字节还找得到)",
        "④": "超档那趟(`frezNum > frezAdd`)档状态与规范一致: 清零点 `TaskFreeze.c:1012` **被执行**, "
             "且那趟记录尾部那一列**被清零**(注入源的特征字节找不到)",
    }


# ---- 11-1 的八步: 一步一个积木, 脚本正文一行一步(顺序即步骤, 见 project/tests/_test_11_1_year_step.py) ----
# 与 4-6 同一形状: 判据文字 / crit / falsify 由脚本给(那是本子项的判过汇总, 库不替它起名);
# 本段每个动词只做一件事 —— 一次读 / 一次发 / 一次比, 返回数据或证据记录, 各自打印读过什么、
# 比了什么。`wb` = 脚本给的那个会话偏函数袋(`arm`/`wait`/`neq`/`inj`), 没会话时整个是 `None`。
# ⚠ **两个场景的顺序**: 结转在前、超档在后。两趟都会各写一条记录、各把序号推一格, 而判据看的是
#   **各自那趟之后读回的那一列**, 与顺序无关; 定这个次序只是让"先看正常档、再看越界档"读日志顺一点。
# ⚠ **③ 为什么不另开一个"清零点未命中"的否定期望**(与 4-7 的 ② 不同): `inject_hit` 在 `:1008`
#   那一停写完剂量就 `resume` 了, 而此刻清零点 `:1012` 的断点**还没挂上** —— 中间隔着从 :1008 走到
#   :1012 的那几条指令(微秒级)。等它返回再去挂, 那一趟早已跑过 ⇒ 那种"否定期望"**必然超时达成**,
#   是静默无效的。改用**等价的黑盒读数**: 剂量写在 `:1008`(那时 `Prep_ObjData` 已填完、写入点还没跑),
#   而**唯一**能抹掉那 4 个字节的地方就是 `:1012 Set_Data`(`:1075` 只读不写) ⇒
#   「记录尾部还找得到剂量」与「`:1012` 没被执行」是同一件事, 且可黑盒读到。故 ③ 不用断点否定期望。
# ⚠ ② 的两个否定期望反过来**要**开满 `neg_win`(70s): 它们盯的是**普通趟**(窗口里必然含一个自然
#   分钟步进), 断点在整段窗口里一直挂着, 没有上面那种竞态。


def year_step_intro(have_wb, wb_waived=False):
    """第一步 · 开场: 本段在造什么、走哪条支 —— 只打印, 不判。"""
    print("\n===== 11-1 年阶梯: 自然分钟步进(否定期望) + 注入造跨年边界(结转/超档两趟) =====")
    print("   本台 %s = %d(:993 的 frezAdd; 年差 ≥ %d 才超档)"
          % ("TAB_FrezAdd[7]", BFY_FREZ_ADD, BFY_FREZ_ADD + 1))
    if not have_wb and not wb_waived:
        print("   !! 本次无调试会话 → 断点观测(白盒)整体**未做**; 本次范围 = 仅黑盒 (③④ 的"
              "「清零点被执行没有」没有可降级的黑盒替身 —— 台面那一列当前值是 0, 光读记录分不开"
              "结转与清零; 见 CLAUDE.md「凭什么算「真实测试」·两种观测」)")
    elif not have_wb:
        print("   -- 本次**用户指定**只做黑盒(串口观测) ⇒ 断点观测(白盒)按指定不做"
              "(J 列须记明本次范围)")


def year_step_read_row(ser, tag, *, subclass=BFY_SUBCLASS, wait=3.0):
    """第二步/第五步 · 黑盒读一条: 阶梯结算冻结最新一条(比"记录推进了没有"用)。"""
    row = read_freeze_row(ser, subclass, 1, wait=wait, empty_ok=True)
    print("   %s: 阶梯结算冻结(子类 0x%02X) 最新一条 = %s" % (tag, subclass, rec_row_txt(row)))
    return row


def year_step_read_raw(ser, tag, *, subclass=BFY_SUBCLASS, wait=3.0):
    """第二步 · 黑盒读那一列: 数据结算用电量的**原始字节**(比"注入的剂量还在不在"用)。"""
    raw = freeze_settle_kwh_raw(read_record_ud(ser, subclass, 1, ST_RCSD_KWH, wait=wait))
    print("   %s: 那一列(数据结算用电量 %s)原始字节 = %s"
          % (tag, ST_OAD_SETTLE_KWH, raw.hex(" ").upper() if raw else "读不到"))
    return raw


def year_step_neg(wb, *, bp_month, bp_year, write_vars=(), neg_win=BFY_NEG_WIN,
                  crit=None, falsify=None):
    """第三步 · ② 的白盒半边: 两个写点在**普通趟**都不该命中(窗口里必然含一个自然分钟步进)。

    本台参数区那份年结算日期表是出厂无效值(12 个 99) ⇒ 普通趟在 `:917` 就 return,
    **月支/年支谁也到不了** ⇒ 这两个否定期望证的是"没有跨边界就不走到写点"。
    """
    recs, why, _add = _rec_bag()
    if wb is None:
        return recs
    write_vars = tuple(write_vars or BFY_WRITE_VARS)
    for bp, nm in ((bp_month, "月支 :1041"), (bp_year, "年支 :1075")):
        if bp is None:
            continue
        r = wb["neq"](bp, neg_win, vars=write_vars,
                      label="11-1 %s 写点否定期望(普通分钟步进)" % nm,
                      crit=crit, falsify=falsify)
        if r is not None:
            recs.append(r)          # ⚠ 并进账本, 不是只打给人看: 这一条才是 ② 的**白盒半边**
            print("      %s: %s" % (nm, r.get("detail") or ""))
    return _rec_close(recs, why)


def year_step_gate(wb, *, bp_gate, gate_vars=(), timeout=BFY_WAIT, label="", crit=None, falsify=None):
    """第四步 · ① 判定: 风格判定那个断点停到没有 + 那刻 `TAB_MeterSty.style` 是不是 TP_Local。"""
    recs, why, add = _rec_bag()
    if wb is None:
        add(label, None, "本次无调试会话(或用户指定只做黑盒) ⇒ 判定体走到没有这件事本次没做成",
            crit=crit, falsify=falsify, obs=judge.DEBUG)
        return _rec_close(recs, why)
    gate_vars = tuple(gate_vars or BFY_GATE_VARS)
    bpno = wb["arm"](bp_gate)
    r = wb["wait"](bpno, timeout, vars=gate_vars,
                   label="11-1 风格判定那个断点(TaskFreeze.c:875)", crit=None, falsify=None)
    hit_detail = (r or {}).get("detail") or "没停到"
    style_txt = ((r or {}).get("vars") or {}).get("TAB_MeterSty.style")
    st = bfy_style_txt(style_txt)
    if r is None or r.get("ok") is not True:
        add(label, None, "没停到 :875(等满 %.0fs) ⇒ 判定体走到没有这件事本次没做成 —— %s"
            % (timeout, hit_detail), crit=crit, falsify=falsify, obs=judge.DEBUG)
    elif st is None:
        add(label, None, "停在 :875 了(%s), 但 `TAB_MeterSty.style` 读不出来(值=%r) ⇒ 判定朝哪边这次判不了"
            % (hit_detail, style_txt), crit=crit, falsify=falsify, obs=judge.DEBUG)
    else:
        add(label, st, "停在 :875(%s), 那刻 TAB_MeterSty.style=%s ⇒ 风格判定%s"
            % (hit_detail, style_txt, "放行" if st else "**拦掉**(判定体不执行)"),
            crit=crit, falsify=falsify, obs=judge.DEBUG)
    return _rec_close(recs, why)


def year_step_advance(pre, mid, *, label="", crit=None, falsify=None):
    """第五步 · ② 的黑盒半边: 走完普通分钟步进, 记录序号**没有**推进。"""
    recs, why, add = _rec_bag()
    adv0 = rec_advanced(pre, mid)
    add(label, bfy_neg_ok(adv0),
        "基线 %s → 走完普通分钟步进 %s (没推进 ⇒ 本条达成)%s"
        % (rec_row_txt(pre), rec_row_txt(mid),
           " —— !! 这一趟**推进了**, 而没有跨结算边界, 固件不该产生" if adv0 is True else ""),
        crit=crit, falsify=falsify)
    return _rec_close(recs, why)


def _yst_carry_label(label):
    """③ 那一条的判据文字 —— 三条路径(注入成了 / 注入点没配齐 / 没会话)共用同一句, 不许只在一支里写。"""
    return label or "③ 未超档那趟(`over == FALSE`)档位电量结转"


def year_step_carry(wb, *, bp_inj, bp_over, at_vars=(), write_vars=(), assigns=None, dose=None,
                    inj_timeout=BFY_INJ_WAIT, label="", crit=None, falsify=None):
    """第六步 · ③ 结转那趟(年份 −1 ⇒ over=FALSE): 停 `:887` 注入年份回退, 等 `:1008` 写剂量。

    ⚠ `watch=:1008` 有两个用处: ① 它是"判定体真走到这一步"的硬证(那一停 `Prep_ObjData` 已填完
      记录正文、清零与写入点都还没跑); ② 它是**唯一**能改"源"的地方 —— 剂量写在那一停
      (`then_assigns`, 写在 `resume()` 之前), 才不会被 `:1004` 的 `Prep_ObjData` 覆盖掉。
      `:1008` 那句 `if (over == TRUE)` 两个场景都过(它是 `if` 本身, 不是分支体), 故两趟共用。

    返回 `(recs, r)` —— `r` 是那一停的记录, 第七步据它读回那一列来判(造不出来时是 `None`)。
    """
    recs, why, add = _rec_bag()
    at_vars = tuple(at_vars or BFY_AT_VARS)
    write_vars = tuple(write_vars or BFY_WRITE_VARS)
    assigns = list(assigns or BFY_INJ_ASSIGN)
    dose = list(dose or YST_DOSE_ASSIGN)
    label = _yst_carry_label(label)
    r = None
    if wb is None:
        add(label, None, "本次无调试会话(或用户指定只做黑盒) ⇒ 这一半不做 —— 本台那一列当前值是 0, "
            "光靠读记录分不开『结转』与『清零』, 没有可降级的黑盒替身", crit=crit, falsify=falsify,
            obs=judge.DEBUG)
    elif bp_inj is None or bp_over is None:
        add(label, None, "脚本没把注入点配齐(`bp_inj`=%r / `bp_over`=%r) ⇒ 跨年边界与那一列要注入的"
            "源都造不出来, 这一半不做" % (bp_inj, bp_over), crit=crit, falsify=falsify,
            obs=judge.DEBUG)
    else:
        r = wb["inj"](bp_inj, [("g_HisTime[5]", "g_CurTime[5] - %d" % YST_YEAR_BACK_CARRY)] + assigns[1:],
                      watch=bp_over, at_vars=at_vars, watch_vars=write_vars,
                      label="11-1 停 :887 注入(年份 −%d) → 等 :1008 写剂量" % YST_YEAR_BACK_CARRY,
                      timeout=inj_timeout, crit=crit, falsify=falsify, then_assigns=dose)
        if r is not None:
            recs.append(r)
            print("      %s" % (r.get("detail") or ""))
    return _rec_close(recs, why), r


def year_step_judge_carry(ser, r, base_raw, *, subclass=BFY_SUBCLASS, wait=3.0,
                          label="", crit=None, falsify=None):
    """第七步 · ③ 判定: 那趟之后记录尾部那一列**原样保留**了注入源没有。"""
    recs, why, add = _rec_bag()
    if r is None:
        return recs
    raw = freeze_settle_kwh_raw(read_record_ud(ser, subclass, 1, ST_RCSD_KWH, wait=wait))
    kept = _yst_raw_kept(raw)
    n, _over = _yst_expected(BFY_PARAM_BYTES, YST_YEAR_BACK_CARRY)
    add(_yst_carry_label(label), kept,
        "注入前 %s → 那趟后 %s ; 离线算 frezNum=%d ≤ frezAdd=%d ⇒ over=FALSE, 不应清零; "
        "剂量 %s 写在 :1008 那一停, 而唯一能抹掉它的地方就是 :1012 ⇒ 记录尾部还找得到它 "
        "与『:1012 没被执行』是同一件事。实测%s"
        % (base_raw.hex(" ").upper() if base_raw else "读不到",
           raw.hex(" ").upper() if raw else "读不到", n, BFY_FREZ_ADD,
           bytes(YST_DOSE_BYTES[:2]).hex(" ").upper(),
           "保留 ⇒ 达成" if kept else
           ("**没保留** ⇒ 那一趟把剂量清了(或剂量没写进去)" if kept is False else "读不到 ⇒ 未证")),
        crit=crit, falsify=falsify)
    return _rec_close(recs, why)


def year_step_over(wb, *, bp_inj, bp_clear, at_vars=(), clear_vars=(), assigns=None, dose=None,
                   inj_timeout=BFY_INJ_WAIT, label="", crit=None, falsify=None):
    """第八步 · ④ 超档那趟(年份 −5 ⇒ over=TRUE): 停 `:887` 注入, 等**清零句** `:1012` 命中。

    与第六步同形, 只换三样: 回退年数(`YST_YEAR_BACK_OVER`, 离线算 `frezNum > frezAdd`)、
    等的那个断点(`bp_clear`, 清零点本身)、那刻要读的量(多一个 `off`)。返回 `(recs, r)`。
    """
    recs, why, add = _rec_bag()
    at_vars = tuple(at_vars or BFY_AT_VARS)
    clear_vars = tuple(clear_vars or (tuple(BFY_WRITE_VARS) + ("off",)))
    assigns = list(assigns or BFY_INJ_ASSIGN)
    dose = list(dose or YST_DOSE_ASSIGN)
    r = None
    if wb is None:
        add(label, None, "本次无调试会话(或用户指定只做黑盒) ⇒ 这一半不做"
            "(清零点被没被执行只有指令路径答得了)", crit=crit, falsify=falsify, obs=judge.DEBUG)
    elif bp_inj is None or bp_clear is None:
        add(label, None, "脚本没把注入点配齐(`bp_inj`=%r / `bp_clear`=%r) ⇒ 超档那一趟没造出来, "
            "这一半不做" % (bp_inj, bp_clear), crit=crit, falsify=falsify, obs=judge.DEBUG)
    else:
        r = wb["inj"](bp_inj, [("g_HisTime[5]", "g_CurTime[5] - %d" % YST_YEAR_BACK_OVER)] + assigns[1:],
                      watch=bp_clear, at_vars=at_vars, watch_vars=clear_vars,
                      label="11-1 停 :887 注入(年份 −%d) → 等清零句 :1012" % YST_YEAR_BACK_OVER,
                      timeout=inj_timeout, crit=crit, falsify=falsify, then_assigns=dose)
        if r is not None:
            recs.append(r)
            print("      %s" % (r.get("detail") or ""))
    return _rec_close(recs, why), r


def year_step_judge_over(ser, r, *, subclass=BFY_SUBCLASS, wait=3.0, label="", crit=None, falsify=None):
    """第九步 · ④ 判定: 清零点命中没有 + 那趟记录尾部那一列**被清零**没有。

    未截前的年份差(= 回退年数 × 有效日期组数)与 `_yst_expected` 返回的 `n` 分开写 ——
    `n` 是**被 frezAdd 截过**的那个数, 直接写进「n > frezAdd」会读成 4>4(假话)。
    """
    recs, why, add = _rec_bag()
    if r is None:
        return recs
    raw = freeze_settle_kwh_raw(read_record_ud(ser, subclass, 1, ST_RCSD_KWH, wait=wait))
    kept = _yst_raw_kept(raw)
    n_pre = YST_YEAR_BACK_OVER * len(_bfy_dates_from_bytes(BFY_PARAM_BYTES)[0])
    # ⚠ 判"命中没有"只认 `ok` 三态 —— `record()` 把 `hit` 折进 detail/ok 之后**不再返还**
    #   它(`common/judge.rec` 只有七键), `r.get("hit")` 恒 None(见 `_rec_hit` 的 ⚠)。
    hit = r.get("ok") is True
    off = st_int((r.get("vars") or {}).get("off"))
    add(label, None if not hit else (kept is False),
        "停 :887 注入年份 −%d ⇒ 离线算年份差 %d > frezAdd=%d ⇒ over=TRUE ⇒ 清零点 "
        "`TaskFreeze.c:1012` 应被执行; 实测%s; 那一停的 `off`=%r(清零落在 `buff[off..off+3]`); "
        "那趟后记录尾部 %s (剂量 %s %s)"
        % (YST_YEAR_BACK_OVER, n_pre, BFY_FREZ_ADD,
           "**命中了 :1012**" if hit
           else "**没命中** ⇒ 超档支没走到(这一半没做成)",
           off,
           raw.hex(" ").upper() if raw else "读不到", bytes(YST_DOSE_BYTES[:2]).hex(" ").upper(),
           "已清掉 ⇒ 达成" if kept is False else
           ("**还在** ⇒ 没清(或清的偏移不是那一列)" if kept is True else "读不到 ⇒ 未证")),
        crit=crit, falsify=falsify, obs=judge.DEBUG)
    return _rec_close(recs, why)



# ---------------------------- 11-2 月阶梯 ----------------------------
# 月形态注入值: 第 0 组 = (时 0, 日 5, 月 0)。月 0 **逃过年形态**那条 `月 1..12` 的判据, 而
# `时 ≤ 23 且 日 1..28` 成立 ⇒ 落进 `else if (i == 0)` 的**月形态**支 ⇒ `date[0] = 0 | 5<<8`,
# `flag = FALSE` ⇒ 走月支写点 `:1041`。后 3 组留 99(无效值, 固件 `continue` 跳过)。
# ⚠ 日取 5 而不是 1: 本台运行期是当月中旬, 日 5 ≤ 当日 ⇒ 月支那两条 `date[0] <= …` 都成立
#   ⇒ `frezNum` 稳定等于**月差**; 取 1 会让它随日期抖动(判据就得多写几条分支, 而多出来的分支
#   没有一个能在离线核过)。
MST_PARAM_BYTES = P.MST_PARAM_BYTES
MST_MONTH_BACK_EXPR = P.MST_MONTH_BACK_EXPR
MST_YEAR_BACK_EXPR = P.MST_YEAR_BACK_EXPR
MST_INJ_ASSIGN = P.MST_INJ_ASSIGN



def month_step_inject_allow():
    """11-2 要注入的表达式名字 —— 与 4-7/11-1 **不同的一份**(多一个 `g_HisTime[4]` 月字节)。"""
    return tuple(e for e, _v in MST_INJ_ASSIGN)


def _mst_expected(date_bytes, cur, his):
    """**纯函数**: 月支那一趟固件会算出的 `frezNum`(照 `TaskFreeze.c:963-971`)。

    `cur`/`his` 各 = `(时, 日, 月, 年)`(从 `g_CurTime`/`g_HisTime` 的 `[2],[3],[4],[5]` 取)。
    `Diff_Months(New, Old)`(`Platform/DateTime.c:318-332`)吃的是 `&g_xTime[EM_Month]`, 那两个字节
    依次是 (月, 年) ⇒ `New[1]*12 + New[0]` = `年*12 + 月`, 返回**绝对差**(`_bfy_year_freznum` 的
    年支用法与此不同, 别混)。随后按 `date[0] = 时|日<<8` 与 cur/his 各 ±1。
    """
    dates, _flag = _bfy_dates_from_bytes(date_bytes)
    if not dates:
        return None
    d0 = dates[0]
    n = abs((cur[3] * 12 + cur[2]) - (his[3] * 12 + his[2]))
    if d0 <= (cur[0] | cur[1] << 8):
        n += 1
    if d0 <= (his[0] | his[1] << 8):
        n -= 1
    return n


def _mst_expected(month_back=1, date_bytes=MST_PARAM_BYTES):
    """**纯函数**: 月支那一趟固件会算出的 `frezNum` —— 在本支注入下**恒等于回退的月数**。

    ⚠ 用不着读表钟(这是它与 `_yst_expected` 的分工): 照 `TaskFreeze.c:963-971`,
      `frezNum = |Diff_Months(月)|`, 然后 `date[0]` 与 cur/his **各**比一次 (`:965` 加一、`:970` 减一)。
      而 `g_HisTime` 每趟结尾都被 `Copy_Data(g_HisTime, g_CurTime, 6)` 刷成 `g_CurTime`
      (`TaskFreeze.c:143`), 本支只改它的**月**(1 月还要连年一起回退) ⇒ cur 与 his 的**时/日一模一样**
      ⇒ 那两条比较结果必然相同 ⇒ 一加一减相消。`Diff_Months` 返回的是 `年*12+月` 的**绝对差**
      (`Platform/DateTime.c:318-332`), 12 月→次年 1 月也正好是 1 ⇒ **恒等于回退的月数**。
    返回 None = 注进去的那份表固件不认(不是月形态) —— 那时写点也不会命中, 两件事在账本里要分得开。
    """
    dates, flag = _bfy_dates_from_bytes(date_bytes)
    if not dates or flag:
        return None
    return int(month_back)


def month_step_criteria():
    """11-2 的**预设条目**(源 = ledger.md 11-2 的「观察与判据」I 列 + 操作步骤 H 列)。测试前定死。

    ⚠ 规格 I 列原文三条:「月阶梯按月结转正确; 与年阶梯不混; 远程费控不适用」, 「判不过/待核」里
      写着「误按年→核 ID_YearCount1 组别」。落成五条:
        · 「远程费控不适用」同 4-7/11-1 —— 本台是本地表, 那半支不构成条目;
        · 「前提 style==TP_Local」= ①(同 11-1);
        · 「按月结转正确」拆成 **②走月支**(写点 `:1041` 命中) + **③`frezNum` 按月差算**;
        · 「与年阶梯不混」= **④** 那**同一趟**年支写点 `:1075` 不命中 —— 两个写点分处 `flag` 的两支,
          同一趟只会过一个, 所以这是**同一个断点阴性对照**;
        · 「冻结一条」= ⑤ 黑盒记录推进。
    """
    return {
        "①": "前提: `Check_BillFrezY` 判定体真被走到(`TaskFreeze.c:875` 命中), 且那刻 "
             "`TAB_MeterSty.style == TP_Local`(=1) —— 月阶梯只对本地表成立",
        "②": "月形态(`ID_YearCount1` 第 0 组只带 时/日)走的是**月支**: 月支写点 "
             "`TaskFreeze.c:1041` 被走到",
        "③": "`frezNum` 按**月差**算: 月支写点那一刻的 `frezNum` == 离线按 "
             "`Diff_Months(月)`+`date[0]` 两条比较算出来的值",
        "④": "与年阶梯不混: **同一趟**里年支写点 `TaskFreeze.c:1075` 不被走到(两个写点分处 "
             "`flag == FALSE` 的两支)",
        "⑤": "698 读回: 该趟之后阶梯结算冻结记录(子类 0x11)最新一条记录推进(或由『无记录』变为有一条)",
    }


# ---- 11-2 的九步: 一步一个积木, 脚本正文一行一步(顺序即步骤, 见 project/tests/_test_11_2_month_step.py) ----
# 读动词(首参 ser)返回数据, 判据动词返回证据记录 —— 与 1-2 段同一形状。
# `wb` = 脚本给的那个会话偏函数袋(五个键 arm/wait/neq/inj/miss), 没会话时整个是 `None` ——
# 那样本段每一步各自记「没做成」, 不冒充成"断点没命中"。
# ⚠ ② 与 ④ 要各注入一趟(各占一个自然分钟步进, 各写一条记录):
#   · ② 用 `inject_hit(watch=:1041)` —— 命中了就是"月支被走到";
#   · ④ 用 `inject_miss(watch=:1075)` —— **造出月形态之后, 年支写点仍未被走到**。
# ⚠ ④ 为什么必须用 `inject_miss` 而不能"命中 :1041 之后另开一个否定期望": 后者要等 `inject_hit`
#   返回才去挂 `:1075` 的断点, 而那一趟早已在放行后的微秒级里跑完了 ⇒ 那个窗口里永远静默、
#   必然"超时达成" —— 看着是通过, 其实什么都没看(与 4-7 段头记的"窗口开错"同一类)。


def month_step_intro(have_wb, wb_waived=False):
    """第一步 · 开场: 本段在造什么、走哪条支 —— 只打印, 不判。"""
    print("\n===== 11-2 月阶梯: 自然分钟步进(否定期望) + 注入月形态造跨月边界 =====")
    if not have_wb and not wb_waived:
        print("   !! 本次无调试会话 → 断点观测(白盒)整体**未做**; 本次范围 = 仅黑盒 (②③④ 都答不了"
              "『走的是哪一支』, 见 CLAUDE.md「凭什么算「真实测试」·两种观测」)")
    elif not have_wb:
        print("   -- 本次**用户指定**只做黑盒(串口观测) ⇒ 断点观测(白盒)按指定不做"
              "(J 列须记明本次范围)")


def month_step_read_row(ser, tag, *, subclass=BFY_SUBCLASS, wait=3.0):
    """第二步/第五步 · 黑盒读数: 读阶梯结算冻结最新一条。

    `tag` 是**打屏用的那一格**(第二趟要读同一行时, 它得说清这是哪一趟 —— 别名混着读,
    读数才比得明白)。
    """
    row = read_freeze_row(ser, subclass, 1, wait=wait, empty_ok=True)
    print("   %s: 阶梯结算冻结(子类 0x%02X) 最新一条 = %s" % (tag, subclass, rec_row_txt(row)))
    return row


def month_step_gate(wb, *, bp_gate=("TaskFreeze.c", 875), gate_vars=(), timeout=BFY_WAIT):
    """第三步 · ① 判定: 风格判定那个断点停到没有 + 那刻 `TAB_MeterSty.style` 是不是 TP_Local。"""
    recs, why, add = _rec_bag()
    if wb is None:
        add("① 风格判定那个断点(TaskFreeze.c:875)停到 + style==TP_Local", None,
            "本次无调试会话(或用户指定只做黑盒) ⇒ 这一半不做", crit="①",
            falsify=_MST_FALSIFY["①"], obs=judge.DEBUG)
        return _rec_close(recs, why)
    gate_vars = tuple(gate_vars or BFY_GATE_VARS)
    bpno = wb["arm"](bp_gate)
    r = wb["wait"](bpno, timeout, vars=gate_vars,
                   label="11-2 风格判定那个断点(TaskFreeze.c:875)", crit=None, falsify=None)
    hit_detail = (r or {}).get("detail") or "没停到"
    style_txt = ((r or {}).get("vars") or {}).get("TAB_MeterSty.style")
    st = bfy_style_txt(style_txt)
    if r is None or r.get("ok") is not True or st is None:
        add("① 风格判定那个断点(TaskFreeze.c:875)停到 + style==TP_Local", None,
            "没停到 :875 或 style 读不出来(等满 %.0fs, 值=%r) ⇒ 判定朝哪边这次判不了 —— %s"
            % (timeout, style_txt, hit_detail), crit="①", falsify=_MST_FALSIFY["①"],
            obs=judge.DEBUG)
    else:
        add("① 风格判定那个断点(TaskFreeze.c:875)停到 + style==TP_Local", st,
            "停在 :875(%s), 那刻 TAB_MeterSty.style=%s ⇒ 风格判定%s"
            % (hit_detail, style_txt, "放行" if st else "**拦掉**(判定体不执行)"),
            crit="①", falsify=_MST_FALSIFY["①"], obs=judge.DEBUG)
    return _rec_close(recs, why)


def month_step_neg(wb, *, bp_month=("TaskFreeze.c", 1041), write_vars=(), neg_win=BFY_NEG_WIN):
    """第四步 · ② 的白盒半边: 普通分钟步进里, 月支写点**不该**命中(那一趟固件在 :917 就 return)。

    ⚠ 这条否定期望要开满 `BFY_NEG_WIN` —— 它盯的是普通分钟步进, 断点在整段窗口里一直挂着。
    """
    recs, why, add = _rec_bag()
    if wb is None:
        return recs
    write_vars = tuple(write_vars or BFY_WRITE_VARS)
    r = wb["neq"](bp_month, neg_win, vars=write_vars,
                  label="11-2 月支写点否定期望(TaskFreeze.c:1041, 普通分钟步进)",
                  crit="②", falsify=_MST_FALSIFY["②"])
    if r is not None:
        recs.append(r)              # ② 的白盒半边并进账本
        print("      ②(普通趟): %s" % (r.get("detail") or ""))
    return _rec_close(recs, why)


def month_step_leg_month(wb, *, bp_inj, bp_month, at_vars=(), write_vars=(),
                         assigns=None, inj_timeout=BFY_INJ_WAIT):
    """第五步 · ②③ 的第一趟: 停 `:887` 注入月形态(月字节回退 + 12B 表写成月形态) ⇒ 月支写点应命中。

    返回 `(recs, r2)` —— `r2` 是那一停的记录, 第六步据它比 `frezNum`(造不出来时是 `None`)。
    """
    recs, why, add = _rec_bag()
    at_vars = tuple(at_vars or BFY_AT_VARS)
    write_vars = tuple(write_vars or BFY_WRITE_VARS)
    assigns = list(assigns or MST_INJ_ASSIGN)
    r2 = None
    if wb is not None and bp_inj is not None and bp_month is not None:
        r2 = wb["inj"](bp_inj, assigns, watch=bp_month, at_vars=at_vars, watch_vars=write_vars,
                       label="11-2 停 :887 注入: g_HisTime 月 −1 + buff[0..2]=月形态日期 (0,5,0)",
                       timeout=inj_timeout, crit="②", falsify=_MST_FALSIFY["②"])
        if r2 is not None:
            recs.append(r2)
            print("      ②: %s" % (r2.get("detail") or ""))
    else:
        add("② 月形态走月支: 写点 TaskFreeze.c:1041 被走到", None,
            "本次无调试会话(或用户指定只做黑盒)或注入点没配齐 ⇒ 跨月边界**造不出来**, 这一半不做 —— "
            "本半支没有可降级的黑盒替身(帧通道没有写 g_HisTime 的口)", crit="②",
            falsify=_MST_FALSIFY["②"], obs=judge.DEBUG)
    return _rec_close(recs, why), r2


def month_step_judge_freznum(r2):
    """第六步 · ③ 判定: 那一停读回来的 `frezNum` 与离线按月差算的期望值是否一致。"""
    recs, why, add = _rec_bag()
    hit2 = r2 is not None and r2.get("ok") is True   # ⚠ 只认三态, 不读 `hit`(见 11-1 ④ 那条 ⚠)
    vv = (r2 or {}).get("vars") or {}
    got = st_int(vv.get("frezNum"))
    want = _mst_expected()
    if not hit2:
        add("③ `frezNum` 按**月差**算", None,
            "月支写点 :1041 没命中 ⇒ 这一趟没走到月支, `frezNum` 没得比(这一半没做成)",
            crit="③", falsify=_MST_FALSIFY["③"], obs=judge.DEBUG)
    elif got is None:
        add("③ `frezNum` 按**月差**算", None,
            "命中 :1041 了, 但那一刻的 `frezNum` 读不出来(值=%r) ⇒ 这一条判不了"
            % (vv.get("frezNum"),), crit="③", falsify=_MST_FALSIFY["③"], obs=judge.DEBUG)
    else:
        add("③ `frezNum` 按**月差**算", got == want,
            "命中 :1041(@%s) 那刻 `frezNum`=%r; 离线按月差算 = %r(`Diff_Months` 绝对差 = 回退的"
            "月数, 而 `date[0]` 与 cur/his 的两条比较因时/日相同而相消) ⇒ %s"
            % ("命中" if hit2 else "未命中", got, want,
               "一致" if got == want else "**不一致**: 不是按月差算的"),
            crit="③", falsify=_MST_FALSIFY["③"], obs=judge.DEBUG)
    return _rec_close(recs, why)


def month_step_leg_year(wb, *, bp_inj, bp_year, at_vars=(), write_vars=(),
                        assigns=None, same_win=BFY_SAME_WIN, inj_timeout=BFY_INJ_WAIT):
    """第七步 · ④ 第二趟: 再造一次月形态, 年支写点**仍不该被走到**(与年阶梯不混)。"""
    recs, why, add = _rec_bag()
    at_vars = tuple(at_vars or BFY_AT_VARS)
    write_vars = tuple(write_vars or BFY_WRITE_VARS)
    assigns = list(assigns or MST_INJ_ASSIGN)
    r4 = None
    if wb is not None and bp_inj is not None and bp_year is not None:
        r4 = wb["miss"](bp_inj, assigns, watch=bp_year, at_vars=at_vars, watch_vars=write_vars,
                        window=same_win, timeout=inj_timeout,   # ⚠ 两个尺度: 等注入点 100s(分钟步进), 观察窗口 4s
                        label="11-2 停 :887 注入月形态 → 年支写点 :1075 应仍不被走到",
                        crit="④", falsify=_MST_FALSIFY["④"])
        if r4 is not None:
            recs.append(r4)
            print("      ④: %s" % (r4.get("detail") or ""))
    else:
        add("④ 与年阶梯不混: 同一趟里年支写点 TaskFreeze.c:1075 不被走到", None,
            "本次无调试会话(或用户指定只做黑盒)或断点没配齐 ⇒ 这一半不做(`inject_miss` 是它的唯一通道)",
            crit="④", falsify=_MST_FALSIFY["④"], obs=judge.DEBUG)
    return _rec_close(recs, why), r4


def month_step_advance(pre, post, premise):
    """第八步 · ⑤ 判定: 黑盒读回 —— 那两趟之后记录有没有推进(或由无记录变有)。

    `premise` = 跨月边界那趟真造出来了吗(注入命中才算)。没造出来时固件**本就不该生成**记录,
    那时读数既不能支持也不能否定本条 ⇒ 记未证, 不记失败。
    """
    recs, why, add = _rec_bag()
    adv1 = rec_advanced(pre, post)
    if premise:
        why5 = "边界那趟前 %s → 后 %s" % (rec_row_txt(pre), rec_row_txt(post))
    else:
        why5 = ("注入没造出跨月边界(月支写点未走到) ⇒ 这一趟固件**本就不该生成**, "
                "读数 %s → %s 既不能支持也不能否定本条 ⇒ 记未证, 不记失败"
                % (rec_row_txt(pre), rec_row_txt(post)))
    add("⑤ 698 读回: 阶梯结算冻结记录最新一条推进(或由无记录变为有一条)", bfy_pos_ok(adv1, premise),
        why5, crit="⑤", falsify=_MST_FALSIFY["⑤"])
    return _rec_close(recs, why)



# 强类型枚举在 DWARF 里印成**枚举名**, 数字一个都不给(实测: `normal=TRUE`)。固件的
# `enum {FALSE = 85, TRUE = 170}` 只有这两个名字当值用过, 所以这里就只认这两个。
# ⚠ 别的枚举名(如 `ID_ImmedFrez`)照样读不懂 → None; 要判它们的判据请**直接断言名字**,
#   别往这张表里堆 —— 堆下去它就成了枚举字典的第二份, 与源码里的那份迟早对不上。
_ENUM_NUM = {"TRUE": 170, "FALSE": 85}


def st_int(v):
    """断点读回的一个整型量 → int 或 None(读不懂就 None, 别猜)。

    ⚠ 与 `bfy_style_txt` 同一条分寸, **不做**通用枚举解释。两种印法各认一种:
      数字: gdb 印成 `170 '\\252'`(值 + 字符形), 故按进制逐个试、取第一个能转的;
      枚举名: `TRUE`/`FALSE` —— 按 `_ENUM_NUM` 那一条收窄的映射换回数字。
    """
    s = str(v if v is not None else "").strip()
    if not s or "optimized out" in s or "No symbol" in s:
        return None
    for tok in re.findall(r"0x[0-9a-fA-F]+|\d+", s):
        try:
            return int(tok, 16) if tok[:2].lower() == "0x" else int(tok, 10)
        except ValueError:
            continue
    m = re.match(r"[A-Za-z_]\w*", s)
    return _ENUM_NUM.get(m.group(0).upper()) if m else None


# ============================ 分钟冻结(4-2) ============================
# 本表事实(离线读固件源码得到, 判据②/③/⑥ 靠它):
#   `Tab_MinfrezPrd[8] = {15,0,…}`(UserCfg.c:568) ⇒ **只有 0 号通道**周期非 0, 其余 7 个在 `:321`
#     被 `continue` 挡掉, 永不落库;
#   `TAB_FrezAdd[EM_Min] == 0`(UserCfg.c:196) ⇒ 一趟最多补 1 条(`:332` 的 `frezAdd = 1`), 且
#     `normal != TRUE` 那趟(校时消息 MSG_ChgTime)在 `:307` 直接 return —— **校时不落库**;
#   自然分钟步进那趟 `normal = TRUE`(TaskFreeze.c:121)。
# 边界 = 绝对分钟数是 `prd` 整数倍的那一分钟。绝对分钟数基准 2000-01-01 00:00, 与固件 `Point_Mins`
#   同一算法(`Point_Days` 已离线逐日比过 2026-09-21: 两边都是 9760 天)。
# ⚠ 排边界时刻**必须先拿到 prd**(脚本自己 AA80 读 `s_stFrzStorageInfo`, 再交 `frez_store_decode` 解) ——
#   拿不到就排不出, 那时如实记"没做成", 别拿 15 硬猜(猜错的表现是"断点没命中", 而它看起来像固件问题)。
MINFREZ_SUBCLASS = P.MINFREZ_SUBCLASS
FREZ_STORE_BLOCK = P.FREZ_STORE_BLOCK
FREZ_STORE_CLAMP = P.FREZ_STORE_CLAMP
FREZ_STORE_ENTRY_LEN = P.FREZ_STORE_ENTRY_LEN
MINFREZ_CHANNEL0 = P.MINFREZ_CHANNEL0
MINFREZ_CHANNELS = P.MINFREZ_CHANNELS
MINFREZ_WRITE_VARS = P.MINFREZ_WRITE_VARS
MINFREZ_WAIT = P.MINFREZ_WAIT
MINFREZ_NEG_WIN = P.MINFREZ_NEG_WIN
MINFREZ_LEAD = P.MINFREZ_LEAD

_MINFREZ_BASE = datetime.datetime(2000, 1, 1)

# 条目 ↔ 证据的极性: falsify = "什么样的固件会让这条判 FAIL"(答不出就不算证据, judge 的规矩)。
MINFREZ_FALSIFY = {
    "①": "校时那趟也停到 :374(`normal` 没被用来早退) ⇒ 停到的那一趟可能是校时而不是自然分钟步进",
    "②": "写库不看通道周期(每个整分都写) ⇒ 那分钟的 `buff[1]` 不是 `stInfo.u16Period` 的整数倍",
    "③": "写点没按 `ID_MinuteFrez0+typ` 定位(写到别的通道), 或一趟补写多于 1 条 ⇒ `typ != 0` 或 `frezNum != 1`",
    "④": "记录里那一格不是写库那一刻的 `buff`(时标另取时钟 / 漏写秒位 / 写错通道) ⇒ 两者对不上",
    "⑤": "非边界整分也落库(周期判定失效, 退化成每分钟一条) ⇒ 那个窗口里 :374 命中",
    "⑥": "记录时标不落在整数倍 `prd` 的分钟上(时标取的是写库时刻而不是边界分钟), 或读回不按序号定位"
         "(按位置返回 / 序号不随写库递增) ⇒ 相邻两条相差的不是 `prd`, 或两条的序号不接着上一号",
    "⑦": "出厂冻结对象表的第 1 行不是规范要的那批电量/电压/电流/功率对象, 或对象号查错了表"
         "(查出来的 OAD 对不上) ⇒ 逐项比当场不符",
    "⑧": "出厂存储信息的深度不是 35040, 或间隔不是 15(默认值被改过 / 深度按别的周期算)"
         " ⇒ 与规范『15min 间隔不少于 365 天』对不上",
    "⑨": "间隔的判定界写错(收下 0 或收下 61) ⇒ 写 0 / 写 61 回了 DAR=0, 或回读该通道的间隔跟着变了",
    "⑩": "固件自报的记录长与区首地址让 35040 条排不进分冻结区(按固件自己的折页式算出的末扇区 > "
         "画像登记的分冻结区末扇区 4032) ⇒ 算出来的末扇区超界",
    "⑪": "某一条 OAD 的折算(逐 OAD 的 dot / sign)固件没给, 或那一列压根没写进记录"
         "(槽里留着上一次的内容), 或那一列的对象位不是该 OAD 该有的符号性(组合无功 1/2 该是有符号"
         "0x14, 写成 0x15 就是不认那一路的符号) ⇒ 读回值与该 OAD 的 4 位小数折算值不相等, 或对象位"
         "与登记不符。本表就有一支: `OAD>>20` = 33/34 的两条(`02100401`/`02200401`)越出 "
         "`TAB_EnySign[13]`, `Convert_EnyData` 见 `sign>=2` 直接返回(kWhData.c:222), "
         "记录里那两列就是未初始化内容",
    "⑫": "间隔不可设: 固件把可设范围缩死(删空之后仍拒收 1 / 60), 或 Action 那条增删通路不接通"
         " ⇒ 删空通道 0 之后写间隔 1 / 写间隔 60 不被收下, 或收下了 AA80 回读仍是旧值",
    "⑬": "记录区装不下 35040 条(深度被改小 / 记录长变大), 或固件在满之前就环回覆盖 —— 最旧那条被"
         "提前挤掉 ⇒ 填满 35040 格之后, 第 35040 条(最旧)与最新一条的时标差不是 35039 × prd 分钟, "
         "或两者的序号差不是 35039, 或最新一条的时标不等于最后填的那一格",
}

# 分冻结记录表第 1 行(`FREZOBJ_SPEC[1]`)的前 12 个是电能量对象, 后 6 个是电压/电流/功率(不是电量列)。
# 12 个都走「单项」那一支(`g_ArrayNum[0] = 1`) ⇒ 记录里每列恰好 1 个元素(9B, 无 `01 xx` 头),
# 与 `IMMED_FREZ_ENE_OADS` 那种"整列 5 项 / 47B"不是一个形态, 读回时要走 `record_cols_chained`(见它那一句)。
MINFREZ_ENE_OADS = ("00100401", "00200401", "00300401", "00400401",
                    "00500401", "00600401", "00700401", "00800401",
                    "01100401", "01200401", "02100401", "02200401")

FREZ_BODY_OBJ_OFF = 6       # 写库那一刻 `buff` 里电量项的起点: 前 6 字节是冻结时标(秒分时日月年)


def minfrez_energy_match(ser, body, pos=1, oads=None, chip=None, wait=3.0, tag=""):
    """判据⑪: 分冻结记录第 `pos` 条的单项电量列 == 写库那一刻 `buff[6..]` 的同序槽 → `(ok, 说明)`。

    两侧各是什么、为什么要折算:
      · 写库那一刻: `buff[FREZ_BODY_OBJ_OFF + 5k]` 起 5 字节 = 第 k 个对象的**小端原始计数**
        (`Prep_ObjData` 的电量支从 `buff[6]` 起按 TAB_SelObj 顺序逐项写, 单项对象每项 5 字节,
        DLT698App.c:13440)。
      · 记录读回: `record_cols_chained` 切出的 9 字节元素(1B 对象位 + 8B 大端数值), 那个数值**该**等于
        上面 5 字节原始计数按该 OAD 的「4 位小数电能量」折算出来的值(`Convert_EnyData`,
        DLT698App.c:5814/5838 的 `j = 8` / `dot = j/2 = 4`)。
    每列判两样, 缺一不算判过: ① 线上那个 9 字节元素的值 == `kwh_to_698val(原始计数, OAD)`;
    ② 元素的对象位 == 该 OAD 该有的符号性(`kwh_conv_sign`: 有符号 0x14 / 无符号 0x15)。
    只判值不判对象位, 固件把组合无功那两列翻成无符号照样过 —— 那是尺子上少刻一道。
    OAD 没登记折算就**拒算**(记"没做成", 不拿默认值顶)。
    ⚠ 12 列**一列不落**地判, 不设"某几列不比"的口子: 一列不比, 那一路就不进分母, 而账本上
      它与"比过且相等"长得一样(CLAUDE.md 第 30 条)。`02100401`/`02200401` 恰是固件出问题的那两列
      (`OAD>>20` = 33/34 越出 `TAB_EnySign[13]`, `Convert_EnyData` 见 `sign>=2` 空转返回,
      槽里留着未初始化内容) —— 把它们摘掉, 等于把"这一列写坏了"从结果里删掉。
    返回 None 的三种情形(都记"没做成", 不是通过也不是失败): 写库那一刻 `buff` 短于要读的长度 /
    记录没读回或应答不是 `85 03` / 有列解不出元素或没登记折算。
    """
    oads = tuple(oads or MINFREZ_ENE_OADS)
    print("\n   数值判据(%s 记录第%d条的 %d 个单项电量列 == 写库那一刻 buff[6..]) [%s] ..."
          % (tag or "4-2", pos, len(oads), tag or "untagged"))
    need = FREZ_BODY_OBJ_OFF + KWH_WIRE_BYTES * len(oads)
    b = bytes(body or b"")
    if len(b) < need:
        print("   !! 写库那一刻的 buff 只有 %d 字节, 短于 %d ⇒ 没有对照物" % (len(b), need))
        return None, "写库那一刻 buff 只有 %d 字节(要 %d 才够 %d 项) ⇒ 没有对照物, 这一条没做成" \
                     % (len(b), need, len(oads))
    ud = read_record_ud(ser, P.MINFREZ_SUBCLASS, pos,
                        rcsd(REC_SEQ_OAD, REC_TIME_OAD, *oads), chip=chip, wait=wait)
    if not ud or ud[:2] != b"\x85\x03":
        print("   !! 记录读回无行(ud 空 / 应答不是 85 03) ⇒ 没有对照物")
        return None, "记录读回无行(ud 空或应答不是 85 03) ⇒ 没有对照物, 这一条没做成"
    cols = record_cols_chained(ud, len(oads))
    if not cols:
        print("   !! 读回的数据段不是「序号列+时间列+每列 9B」的形态(%d 列要不到) ⇒ 形态不认识" % len(oads))
        return None, "读回的数据段不是「序号列+时间列+每列 9B」的形态 ⇒ 这一条没做成(不猜形态)"
    bad, skipped, objsig, lines = [], [], [], []
    for k, oad in enumerate(oads):
        raw = int.from_bytes(b[FREZ_BODY_OBJ_OFF + KWH_WIRE_BYTES * k:
                               FREZ_BODY_OBJ_OFF + KWH_WIRE_BYTES * (k + 1)], "little")
        exp = kwh_to_698val(raw, oad)
        el = cols[k]
        got = int.from_bytes(el[1:9], "big") if len(el) == 9 else None
        sign = kwh_conv_sign(oad)
        want_obj = None if sign is None else (0x14 if sign else 0x15)
        obj = el[0] if len(el) == 9 else None
        lines.append("%s 对象位0x%02X(应0x%02X) 存%s→应%s/记录%s"
                     % (oad, obj if obj is not None else 0, want_obj if want_obj is not None else 0,
                        raw, exp, got))
        if exp is None or got is None or want_obj is None:
            skipped.append(oad)
        elif obj != want_obj:
            objsig.append(oad)
        elif exp != got:
            bad.append(oad)
    print("   " + " | ".join(lines))
    if skipped:
        return None, ("比到的 %d/%d 列全一致; 但 %s 那几列没比成(没登记去路折算 / 列解不出单个元素)"
                      " ⇒ 这一条没做成" % (len(oads) - len(skipped) - len(bad) - len(objsig),
                                          len(oads), " ".join(skipped)))
    if objsig:
        return False, ("%d/%d 列的对象位不是该 OAD 的符号性(该 %s): %s"
                       % (len(objsig), len(oads),
                          "有符号 0x14" if any(kwh_conv_sign(o) for o in objsig)
                          else "无符号 0x15", " ".join(objsig)))
    if bad:
        return False, ("%d/%d 列与写库那一刻不一致: %s"
                       % (len(bad), len(oads), " ".join(bad)))
    return True, "%d/%d 列的对象位与折算值都等于写库那一刻 buff[6..] 的同序槽" % (len(oads), len(oads))


def st_field(text, field):
    """gdb 打印的**结构体**文本里按**字段名**取一个整型量 → int 或 None。

    ⚠ 按名字取, 不按位置取(拿 `st_int` 抠"第一个数字"就是按位置取): 结构体成员次序一改,
      位置取法会**静默**换一个量的值 —— 读回来的仍然是个数, 只是不是要的那个(CLAUDE.md 第 22 条)。
    """
    m = re.search(r"\b%s\s*=\s*(0x[0-9a-fA-F]+|\d+)" % re.escape(field), str(text or ""))
    return st_int(m.group(1)) if m else None


def frez_abs_min(dt):
    """datetime → 绝对分钟数(基准 2000-01-01 00:00; 与固件 `Point_Mins`/`Point_Days` 同一算法)。

    分钟冻结与小时冻结共用这一把尺子: 整点边界就是**绝对分钟数整除 60** 的那一分钟
    (`Point_Mins = Point_Days*1440 + 时*60 + 分`, 而 `Point_Hours = Point_Days*24 + 时` —— 两者
    在整点上是同一个时刻的两种数法)。
    """
    return int((dt.replace(second=0, microsecond=0) - _MINFREZ_BASE).total_seconds() // 60)


def frez_boundary(base_min, prd, kmin=3):
    """≥ `base_min + kmin` 的**下一个边界分钟数**(绝对分钟数整除 `prd`)。纯算术, 不碰串口。

    `prd` 是**以分钟计**的周期: 分钟冻结传通道自己的 `u16Period`(分钟); 小时冻结传
    `u16Period * HOURFREZ_UNIT_MIN`(固件比的是"绝对小时数整除 prd", 而整点那一分钟的绝对分钟数
    正好是 60 的整数倍 ⇒ 两边是同一个边界)。

    `kmin = 3` 是留给"校时帧 + 计量芯 SPI 跟随"的时间: 拨到边界前 1 分钟那一刻, 距当下至少
    2 分钟 ⇒ `MINFREZ_LEAD` 必然满足, 不必再加分支判它。
    """
    k = kmin
    while (base_min + k) % prd:
        k += 1
    return base_min + k


def frez_target(now_dt, boundary_min, sec=5):
    """边界分钟数 → 校时目标串 = **[边界 − 1 分钟] 那一分、秒位 `sec`**。

    这样表钟自然步进在 `60 - sec` 秒后落到边界那一分钟(校时那趟自己不落库, 见本节头部)。
    分钟冻结与小时冻结共用它 —— 整点边界也是"某一分钟", 拨到它前一分钟同样到点。

    `sec` 取大 = 离边界更近(4-2 要 ≤30s ⇒ 传 35)。⚠ 不能大到让目标时刻落到**当下之前**:
    固件有"时间只能前进"守卫(TaskTime.c:127-128), 往回拨会被拒 —— 排边界时刻那一头
    (`frez_boundary` 的 `kmin`)已经留够余量, 所以只要 `sec < 60 - kmin*60` 就安全。
    """
    base_min = frez_abs_min(now_dt)
    return (now_dt.replace(second=sec, microsecond=0)
            + datetime.timedelta(minutes=(boundary_min - 1) - base_min)
            ).strftime("%Y-%m-%d %H:%M:%S")


def frez_step_target(now_dt, prd, lead=59):
    """`(边界时刻, 校时目标串)` —— 拨到**边界之后** `lead` 秒(默认 59), 边界取 `now_dt` 之后的下一个。

    与 `frez_target` 的方向相反: 那个拨到边界**前**一分钟等自然步进走完那一分钟, 这个拨到边界**后**
    只剩 1 秒 —— 固件那条记录本来就写在"边界之后的那次自然分钟步进"上(时标回填成边界整点),
    所以落点贴着下一次步进才最短: 落边界后 59 秒只等 1 秒, 落边界后 5 秒要等 55 秒(实测)。
    返回边界时刻是给调用方拿它当**对照物**核读回来的时标。
    """
    b = frez_boundary(frez_abs_min(now_dt), prd, kmin=1)
    bdt = _MINFREZ_BASE + datetime.timedelta(minutes=b)
    return bdt, (bdt + datetime.timedelta(seconds=lead)).strftime("%Y-%m-%d %H:%M:%S")


def frez_expect_ts(b):
    """写库那一刻的 `buff[0..5]` → 698 记录时标串; 取不全 → None。

    两种冻结的 buff 布局**同一副**: `buff[0]` 是上一行写死的秒 0, `buff[1]` 是分(小时冻结在
    `:434` 也写死 0), 再往后是 `Locate_Mins`/`Locate_Hours` 填的 [时,日,月,年偏移]
    (DateTime.c:346 / :398, 年偏移基准 2000)。所以同一个解串函数对两者都成立。
    """
    if len(b) < 6:
        return None
    return "%04d-%02d-%02d %02d:%02d:00" % (2000 + b[5], b[4], b[3], b[2], b[1])


def frez_ts_gap(r1, r2):
    """两条记录读数的**时标相差几分钟** → int 或 None(任一条读不到/无时标 ⇒ None)。"""
    a = (r1 or {}).get("ts")
    b = (r2 or {}).get("ts")
    if not a or not b:
        return None
    try:
        d1 = datetime.datetime.strptime(a, "%Y-%m-%d %H:%M:%S")
        d2 = datetime.datetime.strptime(b, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return int((d1 - d2).total_seconds() // 60)


def frez_crossed(t0, t1):
    """窗口两端两次表钟读数 → 中间跨过整分了吗(True/False); 任一次读不出来 → None。

    ⑤ 的否定期望要靠它**非空转**: 窗口里没跨过整分时"断点不命中"是必然的, 什么也没看 ——
    那种"达成"是假的(与 4-7 两个窗口的 ⚠ 同一个病)。读不出来记 None(没做成), 不记达成。
    """
    try:
        a = datetime.datetime.strptime(t0 or "", "%Y-%m-%d %H:%M:%S")
        b = datetime.datetime.strptime(t1 or "", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return frez_abs_min(b) > frez_abs_min(a)


def frez_store_decode(body, idxs):
    """`s_stFrzStorageInfo` 的**原始字节** → {表项序号: {prd,size,depth,addr}}; 纯解码, 不碰串口。

    表项序号 = `ID_FREZ` 枚举值(分钟通道 t 的是 `MINFREZ_CHANNEL0 + t`, 小时冻结的是
    `HOURFREZ_INDEX`)。每个表项 12B, 前 2B 小端 = `u16Period`。⚠ 偏移按 `FREZ_STORE_ENTRY_LEN` 算,
    不写死 12 —— 表项布局的单一事实源是 `TS_FrzStorageInfo`(FrezData.h)。

    `body` 读不全的表项**不占位**(该序号直接不出现在结果里)—— 那是"这一格没读全",
    不是"这变量读不到", 上层据此排不出边界时刻、记"没做成", 别把它说成固件不好测。
    """
    out = {}
    for idx in idxs:
        e = (body or b"")[idx * FREZ_STORE_ENTRY_LEN: (idx + 1) * FREZ_STORE_ENTRY_LEN]
        if len(e) < FREZ_STORE_ENTRY_LEN:
            continue
        out[idx] = {"prd": e[0] | (e[1] << 8), "size": e[2] | (e[3] << 8),
                    "depth": e[4] | (e[5] << 8), "addr": e[6] | (e[7] << 8)}
    return out


def frez_store_tail(block, idxs, got_len):
    """首段读到 `got_len` 字节之后, 点名的表项还**差哪一段** → `(名字, 绝对地址, 长度)`; 够了 → None。

    ⚠ 点名的表项若落在 AA80 单次上限之外, 首段只截到它的一部分: 日冻结(表项 10)的偏移是 120..132,
      截读前 128B 只给它前 8B ⇒ 表项整个缺位, 上层拿 `prd=None` 排不出边界日。
      这一段补读少了不会报错, 只会让整项退成"没做成"。

    纯解析, 不碰串口 —— 读那一下由调用方自己发(`W.aa80_ram_snapshots(ser, [tail], …)`);
    拼与解码在 `frez_store_decode`。地址解析不出 → None。
    """
    first = watch.named_blocks(block, clamp=None)
    if not first:
        return None
    name, addr = first[0][0], first[0][1]
    need = (max(int(i) for i in idxs) + 1) * FREZ_STORE_ENTRY_LEN
    if got_len >= need:
        return None
    return ("%s_%d" % (name, got_len), addr + got_len, min(AA80_MAX_LEN, need - got_len))




def minute_frez_criteria():
    """4-2「分钟冻结」预设条目(源 = `_whitebox_ledger/ledger.md` 4-2 那一节的【逐条】)。

    ⚠ 本函数与那一节的文字**逐字相等**, 改一处就得两处一起改。
    """
    return {
        "①": "停在 TaskFreeze.c:374 那一刻 normal == TRUE(自然分钟步进驱动, 不是校时那趟)",
        "②": "那一趟写的分钟是通道周期的整数倍: buff[1](分钟, 二进制) % stInfo.u16Period == 0",
        "③": "那一趟写的是 0 号分钟通道且只写 1 条: typ == 0 且 frezNum == 1",
        "④": "698 GetRequestRecord 子类 0x02 最新一条的冻结时标 == 那一刻 buff[1..5] 解出的整分(秒位 0)",
        "⑤": "紧随写库那一趟的 95 秒窗口里 TaskFreeze.c:374 不命中(非边界整分不落库), "
             "且该窗口里表钟确实跨过一个整分",
        "⑥": "698 读回相邻两条记录的时标相差 stInfo.u16Period 分钟, 且两条的序号接着上一号",
        "⑦": "TAB_FrezObj 第 1 行翻出的 OAD == 规范要的 18 项(12 个电能量 + A 相电压/A 相电流/"
             "零线电流 + 有功功率/无功功率 + 功率因数)",
        "⑧": "0 号分钟通道的存储深度 == 35040、间隔 == 15(出厂值, 与规范『15min 间隔下不少于 365 天』一致)",
        "⑨": "间隔的判定界卡在 1~60: 在一条已配的分钟通道上写间隔 0 与写间隔 61 都被拒"
             "(DAR=3 拒绝操作), 且写完之后 AA80 回读该通道的间隔仍是原值",
        "⑩": "条数上限: 按固件自报的 (记录长, 深度, 区首地址) 与分冻结区末扇区离线算, 记录区"
              "装得下 35040 条(折页式 = (深度-1)//(页大小//记录长) + 2; 页大小 4096 与末扇区 4032 由画像登记)",
        "⑪": "698 读回最新一条记录里的 12 个单项电能量列逐列看两样: 值 == 写库那一刻 `buff[6..]` 的同序槽"
              "(各按该 OAD 的 4 位小数电能量折算), 且该列的对象位 == 该 OAD 该有的符号性(有符号 0x14 /"
              "无符号 0x15); 12 列一列不落",
        "⑫": "间隔可设: 把通道 0 的 18 个关联对象逐个删空(该通道的间隔随之归零)之后, 把间隔设成 1 与"
              "设成 60 都被收下(DAR=0, 且 AA80 回读到的该通道间隔就是新值)",
    }


def minfrez_fill_criteria():
    """4-2「铺满 365 天」那一条 —— **只有 `--fill-year` 模式才进分母**。

    ⚠ 与 `_whitebox_ledger/ledger.md` 4-2 那一节的【逐条】⑬ 逐字相等, 改一处就得两处一起改。
    ⚠ 它跟 ⑧⑩ 不是一回事: ⑧ 是固件自报的深度值、⑩ 是照那个深度离线算排不排得下, 两条都不真铺;
      这一条是**真写 35040 条**, 写完再看最旧那条还在不在。默认跑次不做它(它要连跑十几小时),
      所以默认跑次分母是 12、它不在里面 —— 报分母时必须说清是哪一种跑次。
    """
    return {
        "⑬": "铺满 365 天: 一格一轮把 0 号分钟通道填到 35040 条(表钟逐格前推, 每格停在边界后 59 秒, "
              "由紧随的那次自然分钟步进落库), 填满后最旧那条与最新一条的时标相差 35039 × prd 分钟、"
              "序号相差 35039, 且最新一条的时标就是最后填的那一格",
    }




# ============================ 小时冻结(4-3) ============================
# 本表事实(离线读固件源码得到, 判据②/③/⑥/⑦ 靠它):
#   `TAB_FrezAdd[EM_Hour] == 0`(UserCfg.c:196, EM_Hour=2) ⇒ 一趟最多补 1 条(`:409` 的 `frezAdd = 1`),
#     且 `normal != TRUE` 那趟(校时消息)在 `:399` 直接 return —— **校时不落库**;
#   边界 = 绝对小时数是 `prd` 整数倍的那个整点。绝对小时数基准 2000-01-01 00:00, 与固件
#     `Point_Hours`(DateTime.c:162) 同一算法(`Point_Days` 已离线逐日比过: 2026-09-21 → 9760 天)。
# ⚠ **写点 `:436` 处读不到 `normal`/`prd`/`hour1`/`hour2`** —— DWARF 位置表的空洞(`normal` 的可读区
#   止于 `0x32588`, 写点在 `0x325ce`; `prd` 的可读区止于 `0x3256a`)。所以本项用**两个断点**:
#     断[A] `:425 Prep_ObjData(...)` —— 走到这一行就说明"判定已过、这一趟真要写了", 且 `normal`/
#       `frezNum`/`hours`/`stInfo` 在那里都有位置表区间(0x32578 落在 0x32518-0x32588 内);
#     断[B] `:436 Write_FrezData(...)` —— `buff`/`frezNum`/`stInfo` 在那里可读(0x325ce 落在
#       0x32590-0x325da 内)。判据①(驱动源)只能在 断[A] 读, 判据③(buff 时标)只能在 断[B] 读。
# ⚠ 两个断点必须**在放行之前都挂好**: 断[A] 停完一放行, 核几十微秒就到 断[B], 那时再挂已经晚了 ——
#   它会掠过一次写库, 而现象是"断[B] 从没命中过"(与断点没挂上一模一样)。
HOURFREZ_SUBCLASS = P.HOURFREZ_SUBCLASS
HOURFREZ_INDEX = P.HOURFREZ_INDEX
HOURFREZ_UNIT_MIN = P.HOURFREZ_UNIT_MIN
HOURFREZ_GATE_VARS = P.HOURFREZ_GATE_VARS
HOURFREZ_WRITE_VARS = P.HOURFREZ_WRITE_VARS
HOURFREZ_WAIT = P.HOURFREZ_WAIT
HOURFREZ_NEG_WIN = P.HOURFREZ_NEG_WIN
HOURFREZ_MONTHEND_TOD = P.HOURFREZ_MONTHEND_TOD

HOURFREZ_FALSIFY = {
    "①": "校时那趟也走到 :425(`normal` 没被用来早退) ⇒ 停到的那一趟可能是校时而不是自然步进",
    "②": "一趟补写多于 1 条(周期判定或 frezAdd 失效), 或写的是别的冻结子类 ⇒ `frezNum != 1`",
    "③": "写库不看整点(把当前时刻原样写进 buff) ⇒ 秒/分不为 0、时不是 prd 的整数倍, 或日月年与拨钟算出的那个整点对不上",
    "④": "记录里那一格不是写库那一刻的 `buff`(时标另取时钟 / 漏写秒分位 / 写错子类) ⇒ 两者对不上",
    "⑤": "非整点那些分钟也落库(整点判定失效, 退化成每分钟一条) ⇒ 那个窗口里 :436 命中",
    "⑥": "记录时标不落在整数倍 prd 的小时上(时标取的是写库时刻而不是边界整点) ⇒ 相邻两条相差的不是 prd 小时; "
         "或序号不接着上一号(同一条被写两次 / 两条之间漏了一条)",
    "⑦": "跨月那一刻不算整点(月进位后 Point_Hours/Locate_Hours 对不上) ⇒ :436 不命中, 或时标不是次月 1 日 00:00:00",
    "⑧": "出厂冻结对象表的第 2 行不是本表登记的那两个总电能对象, 或对象号查错了表 ⇒ 逐项比当场不符",
    "⑨": "固件把小时冻结的深度配成别的值(出厂配置被改 / 深度按别的周期算) ⇒ 读回的 depth != 254, "
         "与规范『应可存储 254 个数据』对不上",
    "⑩": "记录区写满 254 格之后停止写、或原地覆盖不推进(环回记账失效) ⇒ 最新一条的序号不再前进、"
         "最旧那一格还是原来那条, 或第 255 格读得到",
}


def _frez_month_end(dt):
    """dt 所在月的月末那一天的日期(纯算术)。"""
    first_next = (dt.date().replace(day=1) + datetime.timedelta(days=32)).replace(day=1)
    return first_next - datetime.timedelta(days=1)


def hourfrez_month_target(now_dt):
    """⑦ 的校时目标串 = **未来最近的那个月末**的 `HOURFREZ_MONTHEND_TOD`(月末这天 23:59:05)。

    为什么必须是"未来": ⑦ 要的是表钟**自然跨过**月界, 所以目标时刻不能已经过去。本月的月末若已经
    过了(今天就是月末且已过那一刻), 就顺延到下个月的月末 —— 一律**向前**拨, 不往回跨月。
    """
    t = datetime.datetime.combine(_frez_month_end(now_dt),
                                  datetime.datetime.strptime(HOURFREZ_MONTHEND_TOD, "%H:%M:%S").time())
    if t <= now_dt:
        t = datetime.datetime.combine(_frez_month_end(now_dt + datetime.timedelta(days=32)),
                                      t.time())
    return t.strftime("%Y-%m-%d %H:%M:%S")


def hour_frez_criteria():
    """4-3「小时冻结」预设条目(源 = `_whitebox_ledger/ledger.md` 4-3 那一节的【逐条】)。

    ⚠ 本函数与那一节的文字**逐字相等**, 改一处就得两处一起改。
    """
    return {
        "①": "停在 TaskFreeze.c:425 那一刻 normal == TRUE(自然分钟步进驱动, 不是校时那趟)",
        "②": "那一趟只写 1 条(frezNum == 1)且写的是小时冻结(ID_HourFrez)",
        "③": "写库那一刻 buff[0](秒)== 0 且 buff[1](分)== 0 且 buff[2](时)是 stInfo.u16Period "
             "的整数倍, 且 buff[3..5] 解出的日月年就是拨钟算出的那个整点",
        "④": "698 GetRequestRecord 子类 0x03 最新一条的冻结时标 == 那一刻 buff[1..5] 解出的整点",
        "⑤": "紧随写库那一趟的 95 秒窗口里 TaskFreeze.c:436 不命中(非整点那些分钟不落库), "
             "且该窗口里表钟确实跨过一个整分",
        "⑥": "698 读回相邻两条记录的时标相差 stInfo.u16Period 小时且序号接着上一号",
        "⑦": "拨到月末最后一个整点前, 表钟自然跨过月界后 :436 仍命中, "
             "且那一条的时标 == 次月 1 日 00:00:00",
        "⑧": "TAB_FrezObj 第 2 行翻出的 OAD == 规范要的那 2 项(正向有功总电能、反向有功总电能)",
        "⑨": "小时冻结的存储深度 == 254",
        "⑩": "记录区装满 254 格后环回顶掉最早那条(不是停止写): 拨钟逐条补满 254 条之后最新一条的"
             "序号恰好前进 254, 最旧那一条的序号是补之前最新那条的下一个, 且第 255 格读不到",
    }


# ============================ 日冻结(4-4) ============================
# 本表事实(离线读固件源码得到, 判据②/③/⑦/⑧ 靠它):
#   `TAB_FrezAdd[EM_Day] == 7`(UserCfg.c:196, EM_Day=3) ⇒ 校时那趟最多补写 7 条(`:472` 的 frezAdd
#     与 `:481-485` 的 clamp), 超过时 `over = TRUE`(`:485`)并先 `Clr_FrezData`(`:497`);
#   **日边界 = 绝对天数是 prd 整数倍的那一天的 00:00**(`:474 day1 % prd == 0`), 绝对天数基准
#     2000-01-01(`Point_Days`, 与 `frez_abs_day` 同一算法: 2026-09-21 → 9760);
#   自然跨日那趟 `frezNum == 1`(:469/:470 的 day1 = day2+1, 区间里最多一个 prd 的整数倍);
#   校时跨多日那趟补 `#{d : day2 < d <= day1, d % prd == 0}` 条(`:473-480`), 其中
#     day1 = 拨钟目标那一天, day2 = `g_HisTime` —— `:140 Copy_Data(g_HisTime, g_CurTime, 6)`
#     每趟收尾把历史时间置为当前时间;
#   写库那一刻 `buff[0..2] = 0`(`:506-508` 写死秒/分/时), `buff[3..5] = [日, 月, 年偏移]`
#     (`:509 Locate_Days`, 末尾 pTime[0]=day / pTime[1]=month / pTime[2]=year, DateTime.c)。
# ⚠ **写点 `:510` 处读不到 `normal`/`over`/`prd`** —— DWARF 位置表的空洞(`normal` 可读区止于
#   `0x326b8`, `over` 止于 `0x326c6`, 写点在 `0x32730`; `prd` 从 `0x3268a` 起就是空洞)。所以本项用
#   **两个断点**: 断[A] `:493 Prep_ObjData(...)`(它在 `:487 if (frezNum != 0)` 之内 ⇒ 走到它就说明
#   这一趟真要写)在那里读 `normal`/`frezNum`/`over`/`days`; 断[B] `:510 Write_FrezData(...)` 读
#   `buff`/`frezNum`/`i`。`prd` 两处都读不到 ⇒ 一律走 `stInfo.u16Period`。
# ⚠ 两个断点必须**在放行之前都挂好**(见 `_frez_arm`)。⑦ 的校时那趟是"发帧即到"(698 校时 →
#   `Set_MeterTime` → `Post_Message(ID_TaskFreeze, MSG_ChgTime)`, DLT698App.c:10209), 所以那一处
#   必须**先挂再拨钟**。⚠ 反过来, ①~⑥ 那两趟是**自然步进**驱动的: 校时那趟 `day1 == day2`
#   (`:473` 的 while 不跑)⇒ `frezNum == 0` ⇒ `:487` 早退, 所以 断[A] 只会在真跨日那趟停住。
# ⚠ **⑦ 之前必须先把 `g_HisTime` 钉在白天**: `:140` 每趟收尾把 `g_HisTime` 置为当前时间, 若紧接着
#   在 00:00:0x 那一刻去拨月末, day2 可能是当天 00:00:00 也可能是上一分钟的 23:59:59(差一天 ⇒
#   补冻条数差 1 ⇒ 判据⑦ 冤红)。所以先做一次**同一天内**的校时(`DAYFREZ_MIDDAY_TOD` 那一步)把
#   它钉死, 再拿那次拨钟的读数去排月末。
DAYFREZ_SUBCLASS = P.DAYFREZ_SUBCLASS
DAYFREZ_INDEX = P.DAYFREZ_INDEX
DAYFREZ_ADD = P.DAYFREZ_ADD
DAYFREZ_GATE_VARS = P.DAYFREZ_GATE_VARS
DAYFREZ_WRITE_VARS = P.DAYFREZ_WRITE_VARS
DAYFREZ_WAIT = P.DAYFREZ_WAIT
DAYFREZ_NEG_WIN = P.DAYFREZ_NEG_WIN
DAYFREZ_MIDDAY_TOD = P.DAYFREZ_MIDDAY_TOD
DAYFREZ_MONTHEND_TOD = P.DAYFREZ_MONTHEND_TOD
# ⑧ 挂断点前先等校时那趟把补冻写完(它连着写 7 条, 挂早了会停在中间那一条上)。55s 后才自然跨月,
# 所以这 15s 只用掉不到三分之一。**本项自己的流程常数**, 不进画像。
DAYFREZ_CATCHUP_SETTLE = 15.0

DAYFREZ_FALSIFY = {
    "①": "校时那趟也走到 :493(`:487` 的早退判错 / `normal` 没被用上) ⇒ 停到的那一趟是校时不是自然跨日",
    "②": "跨日那趟补写多于 1 条(`:473` 的 `day1 > day2` 或周期判错) ⇒ `frezNum != 1`",
    "③": "写库不看 0 点(把当前时刻原样写进 buff), 或日子不是 prd 的整数倍 ⇒ 秒/分/时不全 0 或 buff[3] % prd != 0",
    "④": "记录里那一格不是写库那一刻的 `buff`(时标另取时钟 / 日与月年写反 / 写错子类) ⇒ 两者对不上",
    "⑤": "非边界日那些分钟也落库(日界判定失效, 退化成每分钟一条) ⇒ 那个窗口里 :510 命中",
    "⑥": "记录时标不落在整数倍 prd 的天上(时标取的是写库时刻而不是边界日) ⇒ 相邻两条相差的不是 prd 天",
    "⑦": "拨钟跨多日之后那一趟不补历史(漏 `:473` 的 while / clamp 取错 / 补的是别的子类) ⇒ 补冻条数"
         "不是 min(7, 区间内 prd 的整数倍日数), 或 days[] 那几条不是等差的",
    "⑧": "跨月那一刻不算日界(月进位后 Point_Days/Locate_Days 对不上) ⇒ :510 不命中, 或时标不是次月 1 日 00:00:00",
    "⑩": "日冻结那一趟压根不动 g_MaxDemand(`Check_DayFrez` 全文没有清它的调用) ⇒ 注进去的非零值"
         "跨完日界原样留着",
    "⑪": "出厂冻结对象表的第 3 行不是出厂表登记的那 12 项电量/金额对象, 或对象号查错了表"
         " ⇒ 逐项比当场不符",
    "⑫": "出厂存储信息的深度不是 365、或补冻上限不是 7 ⇒ 与规范『应可存储 365 天』『最多补最近 7 个』对不上",
    "⑬": "上电不再补历史(掉电重启后 `Chk_FrezStamp` 没从记录重建 g_HisTime, 或上电那趟不认"
         "『当前 > 历史』) ⇒ 上电后跨过第一个分钟步进, 记录条数一条都不增(或增的不是 7 条)",
}


def frez_abs_day(dt):
    """datetime → 绝对天数(基准 2000-01-01; 与固件 `Point_Days` 同一算法)。"""
    return frez_abs_min(dt) // 1440


def frez_day_date(day):
    """绝对天数 → 那一天的 `date`(纯算术, 不碰串口)。"""
    return (_MINFREZ_BASE + datetime.timedelta(days=day)).date()


def frez_day_boundary(now_dt, prd, kmin=3):
    """≥ 现在之后 `kmin` 分钟的**下一个日边界**(绝对天数整除 `prd`)。纯算术, 不碰串口。

    边界是**那一天的 00:00**(固件 `:474` 比的是绝对天数整除 prd), 所以校时目标取它前一日 23:59:05
    (`frez_day_target`) —— 表钟自然步进 55s 后落到边界那一天。距现在不足 `kmin` 就顺延一个周期。

    ⚠ 起点取 `abs_day(now) + 1` 而不是"≥ 现在"的那个网格日: 这样校时目标落在**同一天内**
    (`day1 == day2`), 校时那趟不补冻(固件 `:473` 的 while 不跑), 于是 ①②③ 停到的一定是自然跨日那趟。
    """
    d = frez_abs_day(now_dt) + 1
    while d % prd:
        d += 1
    while (d * 1440 - 1) - frez_abs_min(now_dt) < kmin:
        d += prd
    return d


def frez_day_target(now_dt, boundary_day):
    """日边界(绝对天数)→ 校时目标串 = **前一日 23:59:05**。

    ⚠ 传给 `frez_target` 的是**边界分钟本身**(`boundary_day * 1440` = 那天 00:00), 它自己会减 1
      分钟 —— 写成 `boundary_day * 1440 - 1` 会减两次, 目标落到 23:58:05(那一分钟不是日界前一分钟)。
    """
    return frez_target(now_dt, boundary_day * 1440)


def frez_midday_target(now_dt, tod=DAYFREZ_MIDDAY_TOD):
    """⑦ 之前"把 `g_HisTime` 钉在白天"那一步的目标串 = 今天(今天已过就明天)的 `tod`。

    一律**向前**拨(固件 `:127-128` 有时间前进守卫, 往回拨会被挡)。返回目标串。
    """
    t = datetime.datetime.strptime(tod, "%H:%M:%S").time()
    tgt = datetime.datetime.combine(now_dt.date(), t)
    if tgt <= now_dt:
        tgt = datetime.datetime.combine(now_dt.date() + datetime.timedelta(days=1), t)
    return tgt.strftime("%Y-%m-%d %H:%M:%S")


def frez_catchup_plan(now_dt, prd, add, kmin=3, months=24):
    """⑦⑧ 的拨钟计划: 找**未来最近的月末** M, 使 `M+1` 落在 prd 的网格上(⑧ 那条自然跨月写得出来),
    并优先挑"该补的条数 > `add`"的那一个(走到 `:481-485` 的 `over = TRUE` 支)。

    返回 `dict(tgt, d1, trip_day, d2, count, want, want_over)` 或 None(24 个月内找不到合适的月末)。
    `d1` = 月末那一天, `trip_day = d1 + 1` = 拨钟之后**那一趟停下来写库的时刻**(次月 1 日 00:00) ——
    固件 `:469` 的 `day1` 取的是**那一刻**的绝对天数, 所以补冻的集合是
    `count = #{d : d2 < d <= trip_day, d % prd == 0}`(与 `:473-480` 同一个集合), `want = min(add, count)`。
    ⚠ 拿 `d1`(月末那天)当 `day1` 去算 `days[]` 会整整差一天 —— 实测 2026-09-22: 那一趟 `days[0] == 9252`
      (次月 1 日), 而月末是 9251。
    """
    cur = now_dt.date().replace(day=1)
    d2 = frez_abs_day(now_dt)
    tgt_tod = datetime.datetime.strptime(DAYFREZ_MONTHEND_TOD, "%H:%M:%S").time()
    fallback = None
    for _ in range(months + 1):
        nxt = (cur + datetime.timedelta(days=32)).replace(day=1)
        month_end = nxt - datetime.timedelta(days=1)      # 这个月的月末
        cur = nxt
        d1 = (month_end - _MINFREZ_BASE.date()).days
        trip_day = d1 + 1
        if trip_day % prd:                                # 自然跨月那条要落在网格上
            continue
        if (d1 * 1440 - 1) - frez_abs_min(now_dt) < kmin:
            continue
        cnt = trip_day // prd - d2 // prd
        plan = {"tgt": datetime.datetime.combine(month_end, tgt_tod).strftime("%Y-%m-%d %H:%M:%S"),
                "d1": d1, "trip_day": trip_day, "d2": d2, "count": cnt,
                "want": min(add, cnt), "want_over": cnt > add}
        if cnt > add:
            return plan                                   # 优先能走 over 支的那个月末
        if fallback is None:
            fallback = plan
    return fallback


def day_frez_criteria():
    """4-4「日冻结」预设条目(源 = `_whitebox_ledger/ledger.md` 4-4 那一节的【逐条】)。

    ⚠ 本函数与那一节的文字**逐字相等**, 改一处就得两处一起改。
    """
    return {
        "①": "停在 TaskFreeze.c:493 那一刻 normal == TRUE(该趟由自然分钟步进驱动)",
        "②": "跨日那一趟只写 1 条(frezNum == 1)且写的是日冻结(ID_DayFrez)",
        "③": "写库那一刻 buff[0..2](秒/分/时)全为 0 且 buff[3](日)是 stInfo.u16Period 的整数倍, "
             "且 buff[3..5](日月年) == 跨日那一天的日月年 —— 时标落在 0 点",
        "④": "698 GetRequestRecord 子类 0x04 最新一条的冻结时标 == 那一刻 buff[3..5] 解出的 0 点整",
        "⑤": "紧随写库那一趟的 95 秒窗口里 TaskFreeze.c:510 不命中(非边界日的分钟不落库), "
             "且该窗口里表钟确实跨过一个整分",
        "⑥": "698 读回相邻两条日冻结的时标相差 stInfo.u16Period 天",
        "⑦": "表钟被向前拨跨多日之后紧接着的那一趟补冻条数 == min(TAB_FrezAdd[EM_Day]=7, "
             "该趟区间内 prd 的整数倍日数), 且 days[] 那几条两两相差 prd",
        "⑧": "自然跨过月界后最新一条日冻结的时标 == 次月 1 日 00:00:00",
        "⑨": {"text": "记录里的冻结电能 == 前一日的累计电能(与 698 当前电能读数一致)",
              "unprovable": "本台 0 负载(实测电能 0.0000 kWh) ⇒ 前一日累计与当前累计都是 0, "
                            "任何把 0 写进去的固件都满足, 本条答不出 falsify; "
                            "要证须带非零负载跑满一日, 或把电能对象在 RAM 里注入成非零再跨日"},
        "⑩": "跨过日界那一趟把当日正反向有功最大需量归零(在 断[A] 处把 g_MaxDemand[0]/[1] 注成非零, "
             "跨完日界读回应为 0) —— 0 负载下需量恒 0 且固件只清零不写入, 帧通道分不开清没清, "
             "故这一条由注入造出非零值再跨日",
        "⑪": "TAB_FrezObj 第 3 行翻出的 OAD == 出厂表登记的那 12 项(8 个电能量 + 有功功率/无功功率"
             "集合 + 剩余金额 202C0200 + 透支金额 202D0200)",
        "⑫": "日冻结的存储深度 == 365, 且一次补冻上限 == 7",
        "⑬": "断电跨过补冻上限 + 1 天再上电, 上电那一路补出来的条数一样是 7(与校时那一路同一个上限)",
    }


# ============================ 4-1 瞬时冻结: 对象表读解 + 判据 ============================
# 判据照 `简洁正确的测试思路.md` 的「4-1」四步写死: ①时标 ②对象表 ③读回 ④条数封顶。
# 两张表都是 FLASH 常量(与本该不该停核无关), 用探针按符号地址读; 停核只用来读"触发那一刻"的量。
FREZOBJ_SYM = "TAB_FrezObj"     # 冻结对象表: 12 行 × 32 个 INT16U(前 30 槽对象号, 末 2 槽周期/条数)
SELOBJ_SYM = "TAB_SelObj"       # 对象号 → {OAD, len, local, type*} 的查询表
FREZOBJ_ROWS = 12
FREZOBJ_SLOTS = 32
FREZOBJ_ROW_ITEMS = 30          # 每行前 30 槽才是对象号
FREZOBJ_BYTES = FREZOBJ_ROWS * FREZOBJ_SLOTS * 2
SELOBJ_NUM = 825
SELOBJ_STRIDE = 12              # OAD 4 + len 2 + local 1 + 填充 1 + type 指针 4
SELOBJ_BYTES = SELOBJ_NUM * SELOBJ_STRIDE
FREZOBJ_EMPTY = 0xFFFF          # 空槽
FREZOBJ_OBJ_MASK = 0x1FFF       # TP_OBJ0.bit.obj:13(对象号 = TAB_SelObj 的下标)
FREZOBJ_TYP_SHIFT = 13          # TP_OBJ0.bit.typ:3(记录类型)
FREZOBJ_SET_OAD = "50020300"    # 698 Set 记录对象表·分钟对象(属性 03 索引 00); 别的冻结类别是 50[子类]0300
FREZOBJ_SET_MAX_PRD = 60        # 固件收下的间隔上限(DLT698App.c:12728 的 `prd > 60` 支)
FREZOBJ_SET_MIN_PRD = 1         # 固件收下的间隔下限(同处 `prd == 0` 支; 0 不是"最小值"而是"无效")
# 698 Action 那两条(冻结类 OI 通配 0x50FF, 第 2 字节 = 冻结类型 2 = 分钟冻结, 第 3 字节 = 方法号):
#   `Action_Freeze`(DLT698App.c:11540) 的 way=4 添加一个关联对象(参数 = 上面那 11 字节的 structure),
#   way=5 删除一个关联对象(参数 = 一个 OAD), way=8 清空关联对象表(无参数)。
# ⚠ 与 Set 那条路(`FREZOBJ_SET_OAD`)的分工不是"哪条更方便":
#   Set 走 `Set_MinFrezObj(..., act=1)` = **批量设置** —— 这一帧的对象表把现有的整张替换掉,
#     别的通道的对象一并消失(DLT698App.c:11201 / :12672);
#   Action 的 way=4/5 走 `act=3/4` = **单项增删** —— 固件先把现有的读回来再改一条(:12680)。
#   要动一条**已配**通道的间隔只能在单项删减这条路上走: 一个 mod 的对象删到一个不剩时, 固件把该通道
#   的存储信息整段清零(`:12764-12779`), 间隔随之归零, 于是"已配通道不许改 prd"那道坎(`:12729`)不再挡路。
#   ⚠ way=8 清空会把 8 条通道一起推平, 而那些通道的对象表读不回来(存在 EEPROM 的 `ID_FrezObj1`,
#     AA80 三区够不到) ⇒ 本库不许用它 —— 删一条通道就逐个删那一通道的对象。
FREZOBJ_ACT_ADD = "50020400"    # 添加一个关联对象
FREZOBJ_ACT_DEL = "50020500"    # 删除一个关联对象
D698_LONG_UNSIGNED = 0x12       # 698 数据域类型码 long-unsigned(DLT698App.c:142)
D698_OAD = 0x51                 # 698 数据域类型码 OAD(DLT698App.c:158)

# 规范点名的每行出厂配置(源: `简洁正确的测试思路.md` 的 4-1② / 4-2② / 4-3② / 4-4① / 4-6① / 4-7⑧)。
# 每项 = (对象号, 该对象号在 TAB_SelObj 里查出来的 OAD)。
# ⚠ 4-2 那一行 md 写「17 个」而本机 .out 里是 **18 个**: 多出的 750 属 `UserCfg.c:530` 的
#   `en_NEUTRALLINE_SAMPLING`(中性线采样)分支, 本机编了那一支 —— 判据按实物, md 已同步订正。
# 判据比的是**这份表** ⇒ 固件改了出厂配置、或 OAD 查错了表, 这一条当场不满足。
FREZOBJ_SPEC = {
    0: ((70, "00100400"), (126, "00200400"), (182, "00300400"), (238, "00400400"),
        (294, "00500400"), (350, "00600400"), (406, "00700400"), (462, "00800400"),
        (756, "20040200"), (759, "20050200")),
    1: ((71, "00100401"), (127, "00200401"), (183, "00300401"), (239, "00400401"),
        (295, "00500401"), (351, "00600401"), (407, "00700401"), (463, "00800401"),
        (519, "01100401"), (575, "01200401"), (631, "02100401"), (687, "02200401"),
        (746, "20000200"), (748, "20010200"), (750, "20010400"), (756, "20040200"),
        (759, "20050200"), (774, "200A0200")),
    2: ((71, "00100401"), (127, "00200401")),
    3: ((70, "00100400"), (126, "00200400"), (182, "00300400"), (238, "00400400"),
        (294, "00500400"), (350, "00600400"), (406, "00700400"), (462, "00800400"),
        (756, "20040200"), (759, "20050200"), (799, "202C0200"), (802, "202D0200")),
    4: ((14, "00000400"), (70, "00100400"), (126, "00200400"), (182, "00300400"),
        (238, "00400400"), (294, "00500400"), (350, "00600400"), (406, "00700400"),
        (462, "00800400"), (519, "01100401"), (575, "01200401"), (631, "02100401"),
        (687, "02200401"), (804, "20310200"), (822, "2E600200"), (823, "2E610200")),
    10: ((805, "20320200"),),
}


def freobj_read(pb, out=None):
    """探针读两张表 → `(冻结对象表字节, TAB_SelObj 字节)`; 任一张读不到记 None(**不抛**)。

    两张表都是 FLASH 常量, 读它们**不需要停核** —— 所以它走探针直读(通路C), 与断点会话互不打扰。
    地址经 `elfsym.any_addr` 按符号名取(不是死地址): 固件换版重链接后地址会变。
    """
    out_path = out or P.OUT_PATH
    blocks = []
    for sym, size in ((FREZOBJ_SYM, FREZOBJ_BYTES), (SELOBJ_SYM, SELOBJ_BYTES)):
        addr = elfsym.any_addr(sym, out=out_path)
        print("   %-14s @%s" % (sym, "0x%08X" % addr if addr else "**符号查不到**"))
        blocks.append((sym, addr, size))
    got = {}
    for sym, addr, size in blocks:
        if addr is None:
            got[sym] = None
            continue
        try:
            got[sym] = pb.read_abs(addr, size)
        except Exception as exc:            # 读不到 = None(与 AA80 侧同语义: 由上层判读)
            print("   !! %s 读失败: %s" % (sym, exc))
            got[sym] = None
    return got[FREZOBJ_SYM], got[SELOBJ_SYM]


def freobj_row(fb, so, row):
    """解冻结对象表第 `row` 行 → `[(对象号, typ, OAD, len), ...]`(空槽跳过)。

    两张表任一为 None / 长度不对 → 返回 `[]`(调用方据此记"没做成", 不许读成"这一行是空的")。
    """
    if not fb or not so or len(fb) < FREZOBJ_BYTES or len(so) < SELOBJ_BYTES:
        return []
    base = row * FREZOBJ_SLOTS * 2
    out = []
    for j in range(FREZOBJ_ROW_ITEMS):
        v = fb[base + j * 2] | (fb[base + j * 2 + 1] << 8)
        if v == FREZOBJ_EMPTY:
            continue
        obj = v & FREZOBJ_OBJ_MASK
        if obj >= SELOBJ_NUM:
            out.append((obj, v >> FREZOBJ_TYP_SHIFT, None, None))
            continue
        k = obj * SELOBJ_STRIDE
        oad = so[k:k + 4]
        ln = so[k + 4] | (so[k + 5] << 8)
        out.append((obj, v >> FREZOBJ_TYP_SHIFT, oad[::-1].hex().upper(), ln))
    return out


def freobj_txt(rows):
    """一行对象表读解结果的文本(对象号 → OAD), 给证据行用。"""
    return " ".join("%d→%s" % (o, oad or "**OAD越界**") for o, _t, oad, _l in rows)


def freobj_check(rows, row, tag=""):
    """判据: 第 `row` 行读解出来的 (对象号, OAD) 序列 == `FREZOBJ_SPEC[row]`。

    返回 `(ok, detail)`; `ok=None` = 表没读到(这次没做成, 不是"配置不符")。
    """
    want = FREZOBJ_SPEC.get(row)
    print("\n   对象表第 %d 行(%s): %s" % (row, tag or "对规范", freobj_txt(rows) or "**读不到**"))
    if not rows or want is None:
        return None, "冻结对象表读不到(探针没读成/符号查不到) ⇒ 这一次没做成"
    got = tuple((o, oad) for o, _t, oad, _l in rows)
    bad = [i for i, (w, g) in enumerate(zip(want, got)) if w != g]
    if len(got) != len(want):
        return False, ("第 %d 行 %d 项, 规范 %d 项; 差异项 %s"
                       % (row, len(got), len(want), bad or "长度就不对"))
    print("   规范: %s" % " ".join("%d→%s" % w for w in want))
    return (not bad), ("逐项一致(%d 项)" % len(want) if not bad
                       else "第 %s 项不符: 实物 %s / 规范 %s"
                            % ([i + 1 for i in bad], [got[i] for i in bad], [want[i] for i in bad]))


# 第 1 行(m分钟冻结)那 18 个关联对象的 OAD —— 删空一条通道 / 按出厂重建都要逐条指名它们。
# ⚠ 它不是 `FREZOBJ_SPEC[1]` 的副本, 而是从它推出来的: 对象表改了这里跟着改。
MINFREZ_ROW_OADS = tuple(oad for _obj, oad in FREZOBJ_SPEC[1])

FREZOBJ_ROW_TAG = {             # 每行对象表属于哪个子项(取证行里点名)
    0: "4-1 瞬时冻结", 1: "4-2 分钟冻结", 2: "4-3 小时冻结",
    3: "4-4 日冻结", 4: "4-6 结算日冻结", 10: "4-7 阶梯结算冻结",
}


def frez_store_read(ser, block, idxs, clamp=None, wait=3.0, tag=""):
    """AA80 读冻结存储信息区 → `{表项序号: {prd, size, depth, addr}}`; 读不到 → None。

    两段读: 首段截 `clamp` 字节(AA80 单次负载 ≤128B), 点名的表项若落在首段之外, 再按
    `frez_store_tail` 算出的那一段补读。⚠ 少补这一段不会报错, 只会让整项退成"没做成"。

    深度与间隔的单一来源就是它 —— 固件 `FrezGetStorageInfo`(FrezData.c:754) 从同一份
    `s_stFrzStorageInfo` 取数, 所以这条读的是固件自己据以判边界的那个值, 不是另抄的常量。
    """
    idxs = list(idxs)
    snap = watch.aa80_ram_snapshots(ser, watch.named_blocks(block, clamp=clamp), tag=tag or block)
    body = snap.get(block)
    if not body:
        print("   !! %s 读不到(无应答) ⇒ 存储信息这一次拿不到" % block)
        return None
    tail = frez_store_tail(block, idxs, len(body))
    if tail:
        body += (watch.aa80_ram_snapshots(ser, [tail], tag=(tag or block) + "_尾").get(tail[0])
                 or b"")[:tail[2]]
    return frez_store_decode(body, idxs) or None


def frez_store_depth_evidence(store, idx, want_prd=None, want_depth=None, tag=""):
    """存储信息第 `idx` 项的 `(prd, depth)` 对期望值 → `(ok, detail)`; 读不到 → `ok=None`。

    ⚠ `want_*` 给 None 表示这条不判它(只把读到的值写进证据), 不是"期望是 None"。
    """
    e = (store or {}).get(idx)
    if not e:
        return None, "存储信息第 %d 项(%s)没读到 ⇒ 这一条没做成" % (idx, tag or "?")
    got_prd, got_dep = e.get("prd"), e.get("depth")
    bad = []
    if want_prd is not None and got_prd != want_prd:
        bad.append("间隔 %s(期望 %s)" % (got_prd, want_prd))
    if want_depth is not None and got_dep != want_depth:
        bad.append("深度 %s(期望 %s)" % (got_dep, want_depth))
    detail = ("%s 第 %d 项: 间隔=%s 深度=%s 记录长=%s 区首地址=%s"
              % (tag or "存储信息", idx, got_prd, got_dep, e.get("size"), e.get("addr")))
    return (not bad), (detail if not bad else detail + " —— 不符: " + "; ".join(bad))


def frez_store_capacity(store, idx, want_depth, page_size, end_addr, tag=""):
    """存储信息第 `idx` 项按固件自己的折页式排 `want_depth` 条, 装得进记录区吗 → `(ok, detail)`。

    折页式逐字抄固件 `FrezUpdateStorageInfo` 的分钟那一支(FrezData.c:878):
        `末扇区 = 区首 + (深度-1)/(页大小/记录长) + 2`
    (末尾 +2 是固件留的余量页与循环擦除页); 判据 = `末扇区 <= 记录区末扇区` —— 固件在同一处
    用的就是这道闸(`u32Addr_end > FH_FrezEnd` 即 FALSE, FrezData.c:884)。区首与记录长取
    **固件自报**的值, 页大小与末扇区由画像登记; 三者任一读不到 → `ok=None`(没做成)。

    ⚠ 这一条证的是「固件自报的这份布局排得下 `want_depth` 条」, **不是**「真铺满了 `want_depth` 条」
      —— 铺满要表自然走过 365 天, 本台做不到。
    """
    e = (store or {}).get(idx)
    if not e:
        return None, "存储信息第 %d 项(%s)没读到 ⇒ 这一条没做成" % (idx, tag or "?")
    size, depth, addr = e.get("size"), e.get("depth"), e.get("addr")
    if not size or not page_size or not end_addr:
        return None, ("记录长 %s / 页大小 %s / 末扇区 %s 有一样没读到 ⇒ 这一条没做成"
                      % (size, page_size, end_addr))
    per = page_size // size
    if not per:
        return None, ("记录长 %s 比一页(%sB)还大 ⇒ 固件自己的折页式都除不下去, 这一条没做成"
                      % (size, page_size))
    pages = (want_depth - 1) // per + 2
    if addr is None:
        return None, "存储信息区首地址没读到 ⇒ 排不出末扇区, 这一条没做成"
    end = addr + pages
    detail = ("%s 第 %d 项: 记录长=%s 区首=%s 深度=%s(要 %d 条) ⇒ 每页 %d 条、%d 页、末扇区 %d"
              "(区末 %s)" % (tag or "存储信息", idx, size, addr, depth, want_depth, per, pages,
                             end, end_addr))
    return (end <= end_addr), (detail if end <= end_addr else detail + " —— 超界 %d 个扇区" % (end - end_addr))


def frezobj_oad_wire(oad, mod=0):
    """关联对象 OAD → 线上 4 字节, 并按固件规则把通道号嵌进**第 3 字节的高 3 位**。

    ⚠ 那三位不是另给的字段 —— 固件是从 OAD 自己身上取通道号再从 OAD 里清掉它的
      (`mod = pObj[1] >> 5`, `OAD &= ~0x0000E000`; 添加支 DLT698App.c:12692/12693, 删除支 :12700/12701)，
      所以通道号是 OAD 的一部分 —— 传 OAD 与传 `mod` 说的必须是同一件事。
    """
    v = (int(oad, 16) & ~0x0000E000) | ((int(mod) & 0x07) << 13)
    return bytes(((v >> 24) & 0xFF, (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF))


def frezobj_add_elem(oad, prd, dep, mod=0):
    """「添加一个关联对象」的那 11 字节元素(structure 头 + 逐个带类型标签的成员):
    `02 03` / 冻结周期(`12` + 2B) / 关联对象 OAD(`51` + 4B) / 存储深度(`12` + 2B)。

    Set 那条路把它包成 `T_ArrayFrezOADs` 的元素(前面再加 `01 01`), Action 的 way=4 那条路直接用它。
    ⚠ 成员在线上是**大端**(`Spread_NormalData` 用 `RevCopy_Data` 反转, DLT698App.c:15877), 与
      `Set_MinFrezObj` 从内部小端缓冲按 `pObj[j*8+0] | <<8` 取回的是同一个数 —— 两端一致。
    """
    return (b"\x02\x03" + bytes((D698_LONG_UNSIGNED, (prd >> 8) & 0xFF, prd & 0xFF))
            + bytes((D698_OAD,)) + frezobj_oad_wire(oad, mod)
            + bytes((D698_LONG_UNSIGNED, (dep >> 8) & 0xFF, dep & 0xFF)))


def build_frezobj_set_apdu(oad, prd, dep, mod):
    """组一条 698 Set「记录对象表」的 APDU: 往 `mod` 号通道上加一个对象(prd 间隔 / dep 深度)。

    数据域 = `T_ArrayFrezOADs`(DLT698App.c:330) 的形状: 数组(`01 <项数>`) + 每项一个 3 成员结构。
    ⚠ 这条路落地的是固件的 `Set_MinFrezObj(..., act=1)` = **批量设置** —— 整张分钟冻结对象表被这一帧
      替换掉, 别的通道的对象一并消失(DLT698App.c:11201 与 :12672-12676 的先初始化)。
      要改一条已配通道只能走 Action 那条**单项增删**的路, 见 `frezobj_del_one` / `frezobj_add_one`。
    """
    return build_set_apdu(0x07, FREZOBJ_SET_OAD, b"\x01\x01" + frezobj_add_elem(oad, prd, dep, mod))


def set_minfrez_obj(ser, oad, prd, dep, mod=0, chip=None, wait=3.0, tag=""):
    """698 Set `50020300` 往 `mod` 号分钟通道加一个对象 → `(verdict, note, dar)`。本函数自打印。

    判过: 应答 SetResponse(86) DAR=0。**固件收下了不等于配置变了** —— 读改由调用方回读
    `s_stFrzStorageInfo` 证实(`frez_store_read`)。
    ⚠ 固件的三道坎(DLT698App.c:12728-12733), 撞上任一道都回 DAR=3 拒绝操作:
      间隔 0 或 >60 / 深度 0 / **该通道已有非 0 间隔且与本次给的 prd 不同**(已设过的通道不许改)。
      所以要把出厂 15 改成别的, 得先把那个通道的对象删空(act=4)让它归零, 再加。
    """
    apdu = build_frezobj_set_apdu(oad, prd, dep, mod)
    frame = frame_698(apdu, addr=chip_addr(chip))
    head = "Set 记录对象表 %s: 通道%d +OAD %s 间隔%d 深度%d%s" % (
        FREZOBJ_SET_OAD, mod, oad, prd, dep, ("  " + tag) if tag else "")
    rx = send_frame(ser, frame, wait=wait, tag="set_frezobj_" + str(mod), peer=_chip_name(chip),
                    what=head)
    d = decode_set_ack(rx)
    if d is None:
        opout(head, frame, "无 SetResponse(86) 应答", ok=False)
        return "FAIL", "无 SetResponse(86) 应答", None
    if d["service"] == "ErrorResponse":
        note = "ErrorResponse DAR=%d(%s)" % (d["dar"], DAR.get(d["dar"], "?"))
        opout(head, frame, note, ok=False)
        return "FAIL", note, d["dar"]
    ok = d["dar"] == 0
    note = "SetResponse DAR=%s(%s)" % (d["dar"], DAR.get(d["dar"], "?"))
    opout(head, frame, note, ok=ok)
    return ("PASS" if ok else "FAIL"), note, d["dar"]


def _frezobj_act_result(rx, head, frame):
    """Action 应答 → `(verdict, note, dar)`, 与 `set_minfrez_obj` 同一套判读(无应答/形态不对记 FAIL)。"""
    d = decode_action_ack(rx)
    if d is None:
        opout(head, frame, "无 ActionResponse 应答", ok=False)
        return "FAIL", "无 ActionResponse 应答", None
    if d["service"] == "ErrorResponse":
        note = "ErrorResponse DAR=%d(%s)" % (d["dar"], DAR.get(d["dar"], "?"))
        opout(head, frame, note, ok=False)
        return "FAIL", note, d["dar"]
    ok = d["dar"] == 0
    note = "ActionResponse DAR=%s(%s)" % (d["dar"], DAR.get(d["dar"], "?"))
    opout(head, frame, note, ok=ok)
    return ("PASS" if ok else "FAIL"), note, d["dar"]


def frezobj_del_one(ser, oad, mod=0, chip=None, wait=3.0, tag=""):
    """698 Action `50020500` 删掉 `mod` 号分钟通道上的关联对象 `oad` → `(verdict, note, dar)`。本函数自打印。

    判过: ActionResponse DAR=0。⚠ **收下了不等于删掉了一个** —— 通道上还有别的对象时, 这一条收下了
    也只说明那一只没了, 该通道仍在。要证"删干净了"得看该通道的存储信息: 一个 mod 的对象删到一个
    不剩时固件把该通道那一整段清零(DLT698App.c:12764-12779), 间隔随之归零 —— 那次回读由调用方做
    (`minfrez_clear_channel`)。
    ⚠ **对象本来就不在该通道上时, 固件回 `DAR=3(拒绝操作)`**(:12738-12762 的查找循环走完没 break
      ⇒ `j >= num` 拒绝), 实测坐实(2026-09-24 16:12 那一跑: 通道 0 只剩 `00100401` 时删
      `00200401` 得 DAR=3)。所以调用方不许拿"每条都 DAR=0"当删干净的判据。
    """
    param = bytes((D698_OAD,)) + frezobj_oad_wire(oad, mod)
    apdu = build_action_apdu(0x03, FREZOBJ_ACT_DEL, param)
    frame = frame_698(apdu, addr=chip_addr(chip))
    head = ("Action 删分钟冻结关联对象 %s: 通道%d −OAD %s%s"
            % (FREZOBJ_ACT_DEL, mod, oad, ("  " + tag) if tag else ""))
    rx = send_frame(ser, frame, wait=wait, tag="act_frezobj_del_%d" % int(mod), peer=_chip_name(chip),
                    what=head)
    return _frezobj_act_result(rx, head, frame)


def frezobj_add_one(ser, oad, prd, dep, mod=0, chip=None, wait=3.0, tag=""):
    """698 Action `50020400` 往 `mod` 号分钟通道加一个关联对象(间隔 `prd` / 深度 `dep`)。
    → `(verdict, note, dar)`; 本函数自打印。判过: ActionResponse DAR=0。

    ⚠ 与 Set 那条路(`set_minfrez_obj`)的差别是**只动这一条**: 固件先 `Read_FrezObj` 把现有的读回来
      再改一条(DLT698App.c:12680), 别的通道的对象不受影响 —— 所以它才是"改一条已配通道"能用的那条。
    ⚠ 固件那三道坎(`:12728-12730`)照旧挡路: 间隔 0 或 >60 / 深度 0 / 该通道已有非 0 的间隔或深度且
      与本次给的不同。第三道正是"已配通道不许改" —— 要改须先把这个通道删空(`frezobj_del_one`)。
    """
    apdu = build_action_apdu(0x03, FREZOBJ_ACT_ADD, frezobj_add_elem(oad, prd, dep, mod))
    frame = frame_698(apdu, addr=chip_addr(chip))
    head = ("Action 加分钟冻结关联对象 %s: 通道%d +OAD %s 间隔%d 深度%d%s"
            % (FREZOBJ_ACT_ADD, mod, oad, prd, dep, ("  " + tag) if tag else ""))
    rx = send_frame(ser, frame, wait=wait, tag="act_frezobj_add_%d" % int(mod), peer=_chip_name(chip),
                    what=head)
    return _frezobj_act_result(rx, head, frame)


def _minfrez_store_one(ser, mod, tag=""):
    """AA80 读 `mod` 号分钟通道(表项序号 = `P.MINFREZ_CHANNEL0 + mod`)的存储信息 → dict 或 None。"""
    st = frez_store_read(ser, P.FREZ_STORE_BLOCK, [P.MINFREZ_CHANNEL0 + int(mod)],
                         clamp=P.FREZ_STORE_CLAMP, tag=tag)
    return (st or {}).get(P.MINFREZ_CHANNEL0 + int(mod))


def minfrez_clear_channel(ser, oads, mod=0, chip=None, wait=3.0, tag=""):
    """把 `mod` 号分钟通道上的 `oads` 逐个删掉, 再 AA80 回读该通道的间隔 → `(ok, detail)`。本函数自打印。

    判过 = 回读该通道的存储信息, 间隔归零 —— 即固件"一个 mod 的对象删到一个不剩就把该通道的
    存储信息整段清零"那一支(DLT698App.c:12764-12779)真的走了。
    ⚠ **判据落在回读上, 不落在"每一条都回 DAR=0"上** —— 删一个**不在**该通道上的对象, 固件回
      `DAR=3(拒绝操作)`, 那是"这个对象已经没有了", 不是删失败(DLT698App.c:12738-12762 的查找
      循环没 break 时 `j >= num` 走的是 `else` 拒绝支)。所以 `oads` 只要**覆盖**通道上现有的对象
      就够, 多给几个不存在的也无妨: 一旦回读见间隔归零就收工。
    ⚠ 通道号 `mod` 与存储信息的**表项序号**不是一回事: 回读取 `P.MINFREZ_CHANNEL0 + mod`。
    ⚠ 这一趟每成功一条都会连带清掉分钟冻结的记录区(`Clear_FrezData`, :12805), 且是 EEPROM 写。
    """
    oads = tuple(oads)
    print("\n   删空 %d 号分钟通道的关联对象(至多 %d 个, 见间隔归零即收工) [%s] ..."
          % (mod, len(oads), tag or "untagged"))
    for i, oad in enumerate(oads):
        v, n, _d = frezobj_del_one(ser, oad, mod=mod, chip=chip, wait=wait, tag=tag)
        if v == "PASS":
            continue
        e = _minfrez_store_one(ser, mod, tag=(tag or "") + "_删%d后" % (i + 1))
        if e is not None and e.get("prd") == 0:
            return True, ("删到第 %d 个(%s)时被拒(%s), 但回读该通道 间隔=0 深度=%s ⇒ 已删空"
                          % (i + 1, oad, n, e.get("depth")))
        return False, ("删 %s 没被收下(%s), 且回读该通道的间隔是 %s(应归零) ⇒ 没删干净"
                       % (oad, n, None if e is None else e.get("prd")))
    e = _minfrez_store_one(ser, mod, tag=(tag or "") + "_删后")
    if not e:
        return None, "删完 %d 个对象后回读该通道的存储信息没读到 ⇒ 这一条没做成" % len(oads)
    if e.get("prd") != 0:
        return False, ("删完 %d 个对象后该通道的间隔仍是 %s(应归零) ⇒ 间隔没被放开"
                       % (len(oads), e.get("prd")))
    return True, "删空 %d 个关联对象全被收下, 回读该通道 间隔=0 深度=%s" % (len(oads), e.get("depth"))


def minfrez_set_period(ser, oad, prd, dep, mod=0, chip=None, wait=3.0, tag=""):
    """698 Action 往 `mod` 号分钟通道加一个对象(间隔 `prd` / 深度 `dep`) 并回读该通道的间隔
    → `(ok, detail)`。本函数自打印。

    判过 = **两半一起**: Action 收下(DAR=0) **且** AA80 回读到的间隔就是 `prd` —— 只判 DAR 的话,
    一个收下却不落库的固件也满足。
    前提是该通道已被删空(间隔 0): 否则固件那道"已配通道已有非 0 间隔且与本次给的不同就 RefuseOp"
    的坎(`:12729`)会先挡下来, 那是 ⑨ 已判过的另一本账, 不是这一条要答的。
    """
    v, n, _d = frezobj_add_one(ser, oad, prd, dep, mod=mod, chip=chip, wait=wait, tag=tag)
    if v != "PASS":
        return False, "写间隔 %d 没被收下(%s)" % (prd, n)
    e = _minfrez_store_one(ser, mod, tag=(tag or "") + "_设%d后" % prd)
    if not e:
        return None, "写间隔 %d 被收下了, 但回读该通道的存储信息没读到 ⇒ 这一条没做成" % prd
    if e.get("prd") != prd:
        return False, "写间隔 %d 被收下, 但回读到的间隔是 %s ⇒ 收下了没落库" % (prd, e.get("prd"))
    return True, "写间隔 %d 被收下且回读命中(深度=%s 记录长=%s)" % (prd, e.get("depth"), e.get("size"))


def minfrez_restore_channel(ser, prd, dep, mod=0, oads=None, chip=None, wait=3.0, tag=""):
    """把 `mod` 号分钟通道按出厂那 18 个关联对象重建(间隔 `prd` / 深度 `dep`), 再回读证实
    → `(ok, detail)`。本函数自打印。

    先删空再逐条加回来: 已配通道那一组坎(`:12728-12730`)会挡住第一条, 所以删空是这一步的一部分。
    判过 = 18 条全被收下 **且** 回读到的 (间隔, 深度) 就是 (出厂的 `prd`, `dep`)。
    ⚠ 这是 `_test_4_2` 的 ⑫ 动过通道配置之后的还原动作, 不是通用配置工具 —— 对象表来自
      `MINFREZ_ROW_OADS`(由 `FREZOBJ_SPEC[1]` 推出), 改了那张表这里跟着改。
    """
    oads = tuple(oads or MINFREZ_ROW_OADS)
    v, w = minfrez_clear_channel(ser, oads, mod=mod, chip=chip, wait=wait, tag=(tag or "") + "_删空")
    if v is not True:
        return None, "重建前没能把该通道删空(%s) ⇒ 没重建, 这一条没做成" % w
    for oad in oads:
        v2, n2, _d = frezobj_add_one(ser, oad, prd, dep, mod=mod, chip=chip, wait=wait, tag=tag)
        if v2 != "PASS":
            return False, "按出厂重建时加 %s 没被收下(%s) ⇒ 该通道没还原" % (oad, n2)
    e = _minfrez_store_one(ser, mod, tag=(tag or "") + "_重建后")
    if not e:
        return None, "重建的 %d 条都被收下了, 但回读该通道的存储信息没读到 ⇒ 这一条没做成" % len(oads)
    if (e.get("prd"), e.get("depth")) != (prd, dep):
        return False, ("重建的 %d 条都被收下了, 但回读是 间隔=%s 深度=%s(要 %s / %s) ⇒ 没还原"
                       % (len(oads), e.get("prd"), e.get("depth"), prd, dep))
    return True, "按出厂重建 %d 条, 回读 间隔=%s 深度=%s 记录长=%s" \
                 % (len(oads), e.get("prd"), e.get("depth"), e.get("size"))


IMMED_FREZ_FALSIFY = {
    "①": "触发那一刻的表钟与落库记录里的时标不是同一次取数(或写库前又被改写) ⇒ 两者对不上",
    "②": "出厂冻结对象表的第 0 行不是规范要的那批电量/功率对象, 或对象号查错了表(查出来的 OAD 对不上)",
    "③": "瞬时冻结写的是空快照/旧值, 或被其它路的冻结覆盖 ⇒ 记录里的电量整列与当前电能量对象对不上",
    "③b": "一次动作写了不止一条(序号跳号)、或一条没写(序号不动)、或账记错了 ⇒ 序号不是恰好多 1",
    "③c": "写库那一刻缓冲里的电量与落库后读回的那一列不是同一份(写入丢字节/改字节/换算与源码不符) ⇒ 两边对不上",
    "④": "瞬时冻结没有环回记账(只写不顶) ⇒ 连发 4 次后条数不是 3, 或最早那条还在",
}


def immed_frez_criteria():
    """4-1「瞬时冻结」的预设条目 —— 一条对一步; 第 3 步拆成三条(记录读回 / 恰好多一条 / 与第 1 步同一批字节)。"""
    return {
        "①": "落库记录里的时标 == 触发那一刻读到的表钟(分/时/日/月/年, 秒写死 0)",
        "②": "TAB_FrezObj 第 0 行翻出的 OAD == 规范要的 8 个电能量 + 有功/无功功率(共 10 项)",
        "③": "698 读回瞬时冻结记录应答 85 03, 记录里的电量整列与当前电能对象整列逐字节一致",
        "③b": "触发一次之后恰好多出一条: 最新一条的记录序号 == 基线序号 + 1",
        "③c": "记录里那条的电量 == 停住那一刻 `Save_FrezData` 的 `buff[6:]` 里的电量项",
        "④": "连发 4 次后条数封顶 3, 最早那条(第 1 次那条)被顶掉",
    }


def immed_frez_count(ser, subclass, upto=6, chip=None, wait=3.0, quiet=True):
    """数记录条数: 从 pos=1 逐条读到第一条读不到为止 → `(条数, 逐条说明)`。

    条数封顶那一条判据(4-1④)要的是"第 4 条读不出来" —— 这是**黑盒唯一**能数条数的办法(固件不报条数)。
    `answered=False`(对端一个字都没回)不算"读完了", 整次返回 `None`: 断链不许数成"只有几条"。
    """
    n = 0
    detail = []
    for pos in range(1, upto + 1):
        r = read_freeze_row(ser, subclass, pos, chip=chip, wait=wait, empty_ok=True)
        if not r.get("answered"):
            return None, detail + ["pos%d 无应答 ⇒ 条数数不出来" % pos]
        if r.get("empty") or not r.get("seq"):
            detail.append("pos%d 无行" % pos)
            return n, detail
        n += 1
        detail.append("pos%d 序号=%s 时标=%s" % (pos, r.get("seq"), r.get("ts")))
    detail.append("读到 pos%d 还有行(上限 %d 不够数)" % (upto, upto))
    return None, detail


IMMED_FREZ_SUB = 0x00               # 瞬时冻结记录子类(记录 OAD = 50 00 02 00)
# 第 0 行前 8 个对象是 4 位小数的**电能整列**(每个 = 总 + 费率 1..4, 规范 47B);
# 后两个(20040200 有功功率 / 20050200 无功功率)不是电能整列, 不参与"整列 == 当前读数"的对拍。
IMMED_FREZ_ENE_OADS = ("00100400", "00200400", "00300400", "00400400",
                       "00500400", "00600400", "00700400", "00800400")

def immed_frez_snapshot(ser, tag="", oads=IMMED_FREZ_ENE_OADS, pos=1, chip=None, wait=3.0):
    """判据③: 瞬时冻结记录第 `pos` 条里那几个电能整列 == 698 当前同名 OAD 的整列 → `(ok, 说明)`。

    ok: True=列列逐字节一致 / False=有列不一致 / None=没做成(记录无行 / GET 读不出元素)。本函数自打印.
    形态: 两侧各截前 47B 再比 —— 该列元素本体的前 47B 就是「总 + 费率 1..4」(与全库其余整列对拍同口径)。

    ⚠ 本台 0 负载: 两侧多半都是 0 ⇒ 这一条分开的是「写进去了」与「写进去的不是当前读数」,
      分不开「0 是真的」与「空快照恰好也是 0」(同 4-4⑨ 那条的台面限制, 证据里照实写)。
    """
    print("\n   数值判据(瞬时冻结 pos%d 记录电量整列 == 当前电能量对象) [%s] ..."
          % (pos, tag or "untagged"))
    rec = read_record_ud(ser, IMMED_FREZ_SUB, pos, rcsd(_REC_SEQ_OAD, _REC_TIME_OAD, *oads),
                         chip=chip, wait=wait)
    if not rec:
        print("   !! 记录第 %d 条一个字都没回来 ⇒ 对拍做不成" % pos)
        return None, "记录第 %d 条一个字都没回来 ⇒ 这一次没做成" % pos
    if rec[:2] != b"\x85\x03":
        print("   !! 记录第 %d 条的应答是 %s, 不是 85 03(记录应答) ⇒ 这一次没读成记录行"
              % (pos, rec[:2].hex(" ").upper()))
        return False, "记录第 %d 条的应答是 %s, 不是 85 03" % (pos, rec[:2].hex(" ").upper())
    cols = record_energy_cols(rec, len(oads))
    if not cols:
        print("   !! 记录第 %d 条读回无行(ud 空 / 无 1C / 列界的 01 05 不够 %d 个) ⇒ 对拍做不成"
              % (pos, len(oads)))
        return None, "记录第 %d 条读回无行 ⇒ 这一次没做成" % pos
    bad = []
    for oad, got in zip(oads, cols):
        cur = energy_ele_of(read_oad_ud(ser, oad, chip=chip, wait=wait))
        if cur is None:
            print("   !! OAD %s 的当前读数读不出元素 ⇒ 对拍做不成" % oad)
            return None, "OAD %s 的当前读数读不出元素 ⇒ 这一次没做成" % oad
        print("   %s %s" % (oad, got.hex(" ").upper()))
        if cur != got:
            bad.append(oad)
    if bad:
        return False, "%d/%d 列与当前读数不一致: %s" % (len(bad), len(oads), ", ".join(bad))
    return True, "%d 列逐字节一致(各截前 47B; 本台 0 负载 ⇒ 两侧多为 0)" % len(oads)


# ============================ AA80 冻结打点(4-1 瞬时冻结的**佐证观测**) ============================
# 它证的是**另一件事**: 「AA80 内存 diff」与「698 读回」两条**互相独立**的证据同向 ⇒ "冻结落了库"
# 这件事不依赖 IAR 断点。4-1 的判据真源仍在 ledger.md(IAR 侧看 `Save_FrezData` 的局部量那一半)。
# ⚠ AA80 快照全程不停核: 没有"停住 → 8s 看门狗复位"那个坑。
PING_BLOCK_NAMES = P.PING_BLOCK_NAMES
PING_SNAP_CLAMP = P.PING_SNAP_CLAMP
PING_SUBCLASS = P.PING_SUBCLASS
PING_FRAME = P.PING_FRAME
PING_SETTLE = P.PING_SETTLE


_PING_FALSIFY = {
    "①": "冻结只落 EEPROM/外部 Flash 而不动 RAM 那一份 ⇒ 冻结存储信息区前后一个字节都不变"
         "(若本台如此, 这条佐证观测作废, 只能回到 IAR 断点看 `Save_FrezData`)",
    "②": "广播瞬时冻结被静默丢弃(安全判定没过 / 广播地址不匹配 / 该子类没有存储位) ⇒ 记录序号与时标都不动",
}


def aa80_freeze_ping_criteria():
    """4-1「瞬时冻结」的**佐证观测**预设条目(这两条就写在下面)。

    ⚠ 它**不认领 4-1 的判据条目** —— 4-1 的判据真源在 ledger.md(要 IAR/断点看 `Save_FrezData`)。
      这里是**另一条独立通路**给出的同向证据; 两者同向才谈得上"落库证据成立、不必依赖 IAR"。
    """
    return {
        "①": "AA80 前/后快照 diff: 冻结存储信息区(`g_FrezAdr`/`g_FrezNum`/`g_FrezLen`/"
             "`s_stFrzStorageInfo`)有字节变化",
        "②": "698 GetRequestRecord 子类 0x00 读回: 最新一条的序号或时标相比触发前前移",
    }


# ---- 4-1 的五步: 一步一个积木, 脚本正文一行一步(顺序即步骤, 见 project/tests/_test_4_1_freeze_ping.py) ----
# 读动词(首参 ser)返回数据, 判据动词返回证据记录 —— 与 1-2 段同一形状。
# ⚠ 两条读数里**任一条没读到**(对端一个字节都没回)都记 `ok=None`(没做成), 不许读成
#   "没变化/没推进" —— 那会把一次断链写成固件结论(与 4-7 那条订正同一个口径)。
# ⚠ `falsify=` 在**调用点**逐条写出来(不在助手内部查表): 藏进助手就看不见了,
#   那会让"答不出 falsify 的证据"看起来像写过 (4-7 的 add 同此)。


def aa80_freeze_ping_intro():
    """第一步 · 开场: 本段在证什么 / 中间那一次是什么动作 —— 只打印, 不判。"""
    print("\n===== AA80 冻结打点(4-1 佐证): 前快照 → 广播瞬时冻结 → 后快照 + 698 读回 =====")


def _ping_blocks(blocks=None):
    """本段的块表: 不给就用 `PING_BLOCK_NAMES`(名字 → 地址由画像解析; 脚本不摸硬地址)。"""
    return list(blocks or watch.named_blocks(*PING_BLOCK_NAMES, clamp=PING_SNAP_CLAMP))


def aa80_freeze_ping_pre(ser, blocks=None, *, subclass=PING_SUBCLASS, wait=4.0):
    """第二步 · 前快照 + 读回: AA80 直读冻结存储信息区, 再 698 读回最新一条。返回 `(pre, r_pre)`。"""
    blocks = _ping_blocks(blocks)
    print("\n[1] 前快照 + 读回 ...")
    pre = watch.aa80_ram_snapshots(ser, blocks, tag="pre")
    r_pre = read_freeze_row(ser, subclass, 1, wait=wait)
    return pre, r_pre


def aa80_freeze_ping_broadcast(ser, *, frame=PING_FRAME, wait=4.0, settle=PING_SETTLE):
    """第三步 · 触发(**写动作**): 698 广播瞬时冻结 ⇒ 表里新增 1 条记录。"""
    print("\n[2] 698 广播瞬时冻结(%s, **写动作**: 新增 1 条)" % frame)
    res = send(frame, ser_shared=ser, wait=wait)
    print("   verdict=%s reason=%s" % (res.get("verdict"), res.get("reason", "")))
    if settle:
        time.sleep(settle)
    return res


def aa80_freeze_ping_post(ser, blocks=None):
    """第四步 · 后快照: AA80 直读同一批块(与第二步那份逐块比)。"""
    print("\n[3] 后快照 + diff ...")
    return watch.aa80_ram_snapshots(ser, _ping_blocks(blocks), tag="post")


def aa80_freeze_ping_readback(ser, *, subclass=PING_SUBCLASS, wait=4.0):
    """第五步 · 再读回: 698 GetRequestRecord 读最新一条(与触发前那条对拍)。"""
    print("\n[4] GetRequestRecord 子类 0x%02X 读回 ..." % subclass)
    return read_freeze_row(ser, subclass, 1, wait=wait)


def aa80_freeze_ping_diff(pre, post, blocks=None):
    """第六步 · ① 判定: 冻结存储信息区前后有无字节变化。返回 `(recs, chg)`。

    `chg` 三态: `True` 有变化 / `False` 无变化 / `None` **读不到**。**前后两块都读到**才算做成
    —— 有块无应答时记未证, 不许读成"没变化"(那是把一次断链写成固件结论)。
    """
    blocks = _ping_blocks(blocks)
    recs, why, add = _rec_bag()
    changed = watch.aa80_snap_diff(pre, post, blocks)
    both_read = all(pre.get(n) is not None for n, _a, _l in blocks) \
        and all(post.get(n) is not None for n, _a, _l in blocks)
    chg = (True if changed else False) if both_read else None
    detail = ("有变化" if changed else "无变化(前后逐块比过)") if both_read \
        else "前后快照有块读不到(无应答) ⇒ 这一次没读到, 不是『没变化』"
    print("   AA80 前后逐块比: %s" % detail)
    add("① AA80 冻结存储信息区前后有字节变化", chg, detail,
        crit="①", falsify=_PING_FALSIFY["①"])
    return _rec_close(recs, why), chg


def aa80_freeze_ping_advanced(r_pre, r_post, chg=None):
    """第七步 · ② 判定: 读回的最新一条序号/时标相比触发前推进。

    三态助手 `rec_advanced`: 序号不同 / 由无记录变有 / 比不出来(记未证)。
    `chg` 只用来把 ① 那一格的读数并进收尾那行, 不参与本条的判定。
    """
    recs, why, add = _rec_bag()
    adv = rec_advanced(r_pre, r_post)
    print("      AA80 diff=%s; 协议 seq 前=%s 后=%s"
          % (chg if chg is not None else "读不到",
             (r_pre or {}).get("seq"), (r_post or {}).get("seq")))
    add("② 698 瞬时冻结读回最新一条推进", adv,
        "前 %s → 后 %s" % (rec_row_txt(r_pre), rec_row_txt(r_post)),
        crit="②", falsify=_PING_FALSIFY["②"])
    return _rec_close(recs, why)



# ============================ AA80 ↔ SWD 两通路逐字节对照(**表的属性**, 不是通路的属性) ============================
# 它证的是「`swdbg` 与串口白盒返回值可无缝互替」这句话 —— 只能靠两条通路在**同一时刻**读**同一批变量**、
# 逐字节比才谈得上证。2026-09-10 自 `swdbg/selftest.py` 迁出, 理由是它要**同时**握着两条通路与那块表
# (要 COM3 + 画像的 WATCH_VARS/STABLE_VARS); 留在 swdbg 里, 本包就永远离不开 project。
#
# 判据(表在跑, 这点很关键): 每个变量按 **SWD → AA80 → SWD** 读三次 ——
#   SWD 两次相同 ⇒ 该变量此刻**静止** ⇒ AA80 必须与它逐字节相等, 不等即 FAIL(真不等价);
#   SWD 两次不同 ⇒ 此刻**在动**(`g_HisTime` 这种秒表) ⇒ 三读无法定案, 记"漂移";
#                  但 stable 变量(结算绝不能碰的稳定态)出现漂移本身就是异常 ⇒ FAIL。
_XS_FALSIFY = {
    "①": "两条通路读到的东西真不一样(AA80 的区内偏移换算错了, 或 SWD 按绝对地址读的其实是另一处)"
         " ⇒ 静止变量上两边字节不等",
    "②": "`STABLE_VARS` 里登记为稳定的变量其实在动 ⇒ 结算/计量过程读到的那个'稳定态'是假的",
}


def aa80_vs_swd_criteria():
    """两通路对照的预设条目(源 = `scripts/_check_aa80_vs_swd.py` docstring 的「判据」那节)。

    ⚠ 它**不认领任何子项** —— 这是通路等价性的一条自证, 不是某个子项的判据。
    """
    return {
        "①": "静止变量上 AA80 与 SWD 逐字节相等(SWD 前后两读相同的那一档)",
        "②": "`STABLE_VARS` 里的变量在三次读期间不漂移",
    }


def aa80_vs_swd_compare(ser, names, pb, *, stable=(), resolve=None, read_aa80=None):
    """两通路逐字节对照(库内单点, 脚本不留)。返回 `(recs, details, scope)`。

    两条通路**都由调用方注入**, 与本模块的层纪律一致(本文件不许 import swdbg):
      · `pb`       —— J-Link 会话对象, 只要能 `read_abs(addr, size) -> bytes`(`swdbg.probe.Probe`);
      · `resolve`  —— 变量名 → `(绝对地址, 长度)`(脚本传 `common.varresolve.resolve`);
      · `read_aa80`—— 变量名 → `bytes|None` 的串口读(脚本传 `cmd_bank.watch_vars` 那一支)。
    `stable` = 那一批"绝不能碰的稳定态"变量名(出现漂移即异常, 不是正常抖动)。
    ⚠ 没给 `read_aa80` 就自己按串口读(库内已有的 `watch_vars`)。
    """
    read_aa80 = read_aa80 or (lambda nm: watch.watch_vars(ser, [nm]).get(nm))
    stable = set(stable or ())
    recs, details = [], []
    n_static = n_drift = n_bad = 0
    print("\n===== AA80(串口) vs SWD(J-Link) 逐字节对照: %d 个变量 =====" % len(names))
    for name in names:
        az = resolve(name) if resolve else None
        if not az:
            recs.append(rec("① %s 可定址" % name, False, "画像未登记该变量", crit=None))
            details.append("%s: 不可定址" % name)
            n_bad += 1
            continue
        addr, size = az
        a1 = pb.read_abs(addr, size)            # SWD 第 1 读
        aa = read_aa80(name)                    # 串口读(夹在两次 SWD 之间)
        a2 = pb.read_abs(addr, size)            # SWD 第 2 读
        if aa is None:
            recs.append(rec("① %s AA80 有应答" % name, None, "串口读失败(无应答)", crit=None))
            details.append("%s: AA80 无应答" % name)
            n_bad += 1
            continue
        if a1 != a2:
            ok = name not in stable
            n_drift += 1
            recs.append(rec("① %s 在动" % name, True if ok else False,
                            "SWD %s → %s, AA80 %s —— %s"
                            % (a1.hex(" "), a2.hex(" "), aa.hex(" "),
                               "非 stable, 属正常抖动" if ok else "**stable 变量不该动**"),
                            crit="②" if not ok else None,
                            falsify=_XS_FALSIFY["②"] if not ok else None))
            details.append("%s: 漂移" % name)
            continue
        same = (aa == a1)
        n_static += 1
        recs.append(rec("① %s AA80 == SWD" % name, same,
                        a1.hex(" ").upper() if same
                        else "SWD=%s AA80=%s" % (a1.hex(" ").upper(), aa.hex(" ").upper()),
                        crit="①", falsify=_XS_FALSIFY["①"]))
        details.append("%s: %s" % (name, "一致" if same else "不一致"))
        if not same:
            n_bad += 1
        else:
            recs.append(rec("② %s 静止期间不漂移" % name, True, "SWD 两次读数相同",
                            crit="②", falsify=_XS_FALSIFY["②"]))
    print("   -- 静止可比 %d / 漂移 %d / 不一致或无应答 %d --" % (n_static, n_drift, n_bad))
    return recs, details, "AA80 ↔ SWD 两通路对照"


# ============================ 白盒观察簇已搬到 meterlib/watch.py ============================
# 判据: "只往表发只读帧、拿回内存字节"这一簇不再与语义动词同住 —— 它是一条**独立的观察通路**
#   (AA80 直读管理芯 RAM, CPU 全程不停), 与 SWD 直读、gdb 断点并列。一条通道一个文件。
#
# ⚠ 老写法里说"它不依赖本文件其余部分, 所以合在这里不成环" —— 那句是**错的**: `read_mem_aa80`
#   要 `send_frame`, `WatchBank._read` 要 `tx_recv`, 三处判定行要判定行的出口。同住一个文件时
#   看不出这一点, 一旦分家立刻成环。所以那两样**零协议**的薄壳已下沉到 common/ ——
#   `common.portsel.send_frame`(现成串口才发, 否则抛)与 `common.loglabel.opout`(判定行的出口)。
#   于是本文件对那一簇是**使用者**不是中转: 上面三十来处调用点写 `W.xxx(...)`, 脚本直接
#   `from meterlib import watch as W` —— `CB.watch_vars` / `CB.WatchBank` 这些名字**不再存在**。

def verbs(keyword=None):
    """【活查询】列当下语义动词积木(拼脚本用, 不写死清单): 返回首参为 `ser` 的函数
    (即"发操作给表、可在脚本 main 里一行调用一个"的积木) → [(name, sig, doc首行)].
    积木增删/改名/加参都不用改这里 —— 它现查函数表, 随库长大自动变多.
    查两处: 本模块(语义动作)与 `meterlib.watch`(AA80 只读观察)—— 两处都发帧给表, 都算积木.
    keyword: 在 名字+文档 里做子串过滤(可给 域/功能中文词), None=全列."""
    import inspect
    rows = {}
    for me in (sys.modules[__name__], watch):
        for n, f in inspect.getmembers(me, inspect.isfunction):
            if n.startswith("_"):
                continue
            try:
                params = list(inspect.signature(f).parameters.values())
            except (ValueError, TypeError):
                continue
            if not params or params[0].name != "ser":   # 语义动词 = 首参 ser(发给表的操作)
                continue
            doc = (f.__doc__ or "").strip().splitlines()[0] if (f.__doc__ or "").strip() else ""
            row = (n, str(inspect.signature(f)), doc)
            if keyword is None or keyword.lower() in n.lower() or keyword in doc:
                rows.setdefault(n, row)      # 按名去重: 两个模块共用的名字(如 send_frame)只列一次
    return sorted(rows.values(), key=lambda r: r[0])


def cmd_verbs(keyword=None, as_json=False):
    """列语义动词积木(活查询) → 0. keyword 子串过滤."""
    rows = verbs(keyword)
    if as_json:
        _pjson([{"name": n, "sig": s, "doc": d} for n, s, d in rows])
        return 0
    if not rows:
        print("(无动词匹配%s) 用 verbs 无参列全部" % ((" " + keyword) if keyword else ""))
        return 0
    print("语义动词积木(首参 ser, 一行一个在脚本 main 拼; 活查询, 随库长大自动增): %d 个%s"
          % (len(rows), " 含[%s]" % keyword if keyword else ""))
    for n, s, d in rows:
        print("  %-32s %s\n        %s" % (n, s, d))
    return 0


# ============================ 控制台 UTF-8(仅命令行; import 时不碰 stdout) ============================
# 控制台 UTF-8 单点(原先本文件内联一份, 2026-09-10 收敛到 common/console.py;
# 别名保原调用点 `_ensure_utf8_stdout()` 一字不改)
from common.console import ensure_utf8_stdout


# ============================ 命令行 ============================
def _strip_flags(argv):
    fl = {"json": False, "dry": False, "actions": False, "raw": False,
          "repeat": 1, "wait": 3.0, "param": None, "chip": None}
    out = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--json":
            fl["json"] = True
        elif a == "--dry":
            fl["dry"] = True
        elif a == "--actions":
            fl["actions"] = True
        elif a == "--raw":
            fl["raw"] = True
        elif a == "--repeat":
            i += 1
            if i < len(argv):
                try:
                    fl["repeat"] = max(1, int(argv[i]))
                except ValueError:
                    pass
        elif a.startswith("--repeat="):
            try:
                fl["repeat"] = max(1, int(a.split("=", 1)[1]))
            except ValueError:
                pass
        elif a == "--wait":
            i += 1
            if i < len(argv):
                try:
                    fl["wait"] = float(argv[i])
                except ValueError:
                    pass
        elif a.startswith("--wait="):
            try:
                fl["wait"] = float(a.split("=", 1)[1])
            except ValueError:
                pass
        elif a == "--param":
            i += 1
            if i < len(argv):
                fl["param"] = argv[i]
        elif a.startswith("--param="):
            fl["param"] = a.split("=", 1)[1]
        elif a == "--chip":
            i += 1
            if i < len(argv):
                fl["chip"] = argv[i]
        elif a.startswith("--chip="):
            fl["chip"] = a.split("=", 1)[1]
        else:
            out.append(a)
        i += 1
    return out, fl


# ============================ 1-2『电能数据 · 与计量芯保持一致』(2026-09-11) ============================
# 规格(ledger.md 1-2)触发链: 计量芯帧 → Communicate.c:1039 Save_Caculator_Data
#   → kWhData.c:368 Save_CurEnergyData(g_CurkWh 落值 + Fetch_CRC) / :444 Update_Rate_Energy(费率分摊)
#   → 698 抄电能 → kWhData.c:127 Read_CurkWh(:187 Get_CurkWh 取数) 回读。
# 判过原文: 『698 读回值 = 计量芯来值 = 费率分摊和; 多帧稳定一致』。
#
# ---- 布局事实(编译产物 + 源码双证) ----
#   · `g_CurkWh[1*10*(1+C_RateNum)][8+2]` (kWhData.c:16), `.out` 符号 `size=1300`
#     ⇒ 10*(1+C_RateNum)*10 = 1300 ⇒ **1+C_RateNum = 13**(与 UserCfg.h:287 `#ifndef VER_20Edit`
#     的二分一致: VER_20Edit 已定义 ⇒ C_RateNum=12)。行序 = 正有功/反有功/四象限无功/正反基波/正反谐波。
#   · 每项 10B = 8B 电能(小端) + 2B CRC; CRC = Common.c:475 `Fetch_CRC(p,8)` = (Σ&0xFF)^0x55, (异或)^0xAA。
#   · 计量帧内电能字段自偏移 110 起、每电类步长 9(kWhData.c:374/:381/…); X_Buff = g_SPIMBuff + STR1_Index。
#   · 698 侧: OAD `00<eny<<4> 04 00` 的 eny 经 `TAB_EnyChg`(kWhData.c:21)映射到 g_CurkWh 行
#     (`Read_CurkWh` 内 `eny = TAB_EnyChg[eny]`); 故 `00100400`(eny=1)↔g_CurkWh 行 0(正有功)。
#
# ---- 台面事实(2026-09-11 只读探针实测, 每条当场坐实; 不是从源码推的) ----
#   · 空载: g_Curr/g_PowP = 0; 10 个「总」项里只有**反谐波总 = 150**, 其余 = 0。
#   · 10 个「总」项的 2B CRC **全部配对** ⇒ Save_CurEnergyData 每帧都跑到, 搬运链活着。
#   · 费率项(rateNo 1..12)的 CRC **几乎全坏** ⇒ 从未被合法写过 ⇒ 它们是 `__no_init`(:16)的随机 RAM。
#
# ⇒ 判据④『费率分摊和 == 总』在这台表上答 **False**, 根因是**代码级不对称**(静态可核, 与负载无关):
#     · `Get_CurkWh`(kWhData.c:79)     读费率项前先 `Check_CRC`, 坏则回落 EEPROM, 再不行给 0;
#     · `Update_Rate_Energy`(kWhData.c:456) 读费率项是**裸 `Copy_Data`** —— 把 `__no_init` 的随机值
#       直接 `+= Δ`, 再写回并 `Fetch_CRC` ⇒ **给垃圾盖上"合法"章**; 此后 `Get_CurkWh` 照单全收,
#       698 主站会读到天文数字的费率电量。触发条件 = 某电类总项第一次由 0 变非零且当时费率号 != 0。

CURKWH_NENY = P.CURKWH_NENY
CURKWH_SLOT = P.CURKWH_SLOT
CURKWH_STRIDE = P.CURKWH_STRIDE
ENE_FRAME_OFF = P.ENE_FRAME_OFF
ENE_FRAME_STEP = P.ENE_FRAME_STEP
ENE_FRAME_LEAD = P.ENE_FRAME_LEAD
CURKWH_ENY_NAMES = P.CURKWH_ENY_NAMES
CURKWH_ENY_698 = P.CURKWH_ENY_698
CURKWH_BP_ANCHOR = P.CURKWH_BP_ANCHOR
CURKWH_VAR = P.CURKWH_VAR
CURKWH_OAD_ROW = P.CURKWH_OAD_ROW



def kwh_crc2(b8):
    """`Fetch_CRC(pData, 8)`(Common.c:475)的 Python 等价 → 2B. 8 个零字节 ⇒ 55 AA."""
    sm = 0
    xr = 0
    for x in bytes(b8):
        sm = (sm + x) & 0xFF
        xr ^= x
    return bytes((sm ^ 0x55, xr ^ 0xAA))


def kwh_slot_ok(slot):
    """10 字节的项(8B 电能小端 + 2B CRC)的 CRC 是否配对 → True/False/None(形态不规)。"""
    if slot is None or len(slot) != CURKWH_SLOT:
        return None
    return bytes(slot[8:10]) == kwh_crc2(slot[0:8])


def kwh_slot_val(slot):
    """10 字节的项的电能值 = 小端 8B 无符号整数, **原始计数**(不是 kWh)。

    标度是 1e-6 kWh(微千瓦时): 固件 `Convert_EnyData` 按该 OAD 的 `dot`(报文保留几位小数)除
    `10^(6-dot)` —— dot=4 ⇒ 除 100, 正好把 6 位小数砍成报文要的 4 位。实测对拍坐实:
    格子 459550482753 与 698 报文 4595504827 是同一个电量(459550.482753 kWh), 只差这 100 倍。
    2026-09-18 更正: 旧 docstring 写的是"标度 4 位小数 ⇒ 值/10000", **是错的** —— 那样 45955048
    kWh 与 698 报文对不上。参见 `KWH_DOT_DIV` 上方那段推导。"""
    return int.from_bytes(bytes(slot[0:8]), "little")


def kwh_slot_units(slot):
    """该格原始计数折成 kWh(浮点, 仅供人读; 判据一律比整数, 不比浮点)。"""
    return kwh_slot_val(slot) / 1000000.0


# ---- 来路/去路的**字节序与定标**(2026-09-11 双证: 源码 + 同项读数对拍, 别照抄推断) ----
#   · **来路(计量芯帧, g_SPIMBuff)**: 一类电量 = `1B 类型码(0x14 long64 / 0x15 long64-un) + 8B **大端**`,
#     值**原封不动**等于 g_CurkWh 存的 u64(反谐波 150 == 150, 逐字节只差字节序)。698 线上本就是
#     MSB-first, 故这不是"帧"的怪癖, 是 698 编码本相。
#   · **去路(698 读回)**: `Read_CurkWh` 只填**低 5 字节**(`pBuff[0..4]` 拼 u64), 再
#     `Convert_EnyData(p, num=8, dot=len/2=4, HEX, sign)` —— `case 4: u64 /= 100`, 最后 `Copy_Data(p,&u64,8)`
#     仍按本机小端写出、由 698 层按大端上线。⇒ **去路是有损的**: 截到低 5 字节(>1.0995e12 溢出)且 //100。
#     实测对拍: rate0 `150` → 线上 `1`;  rate2 `0xE5729B808C=985470304396` → 线上 `9854703043` ✓ 两例全中。
#     (`TP_SoftVer == TP_Release` 时还会再 `/= C_IMP`; 本台实测**没有**这一步 —— 150→1 只除了 100。)
KWH_VAL_MASK5 = P.KWH_VAL_MASK5
KWH_WIRE_BYTES = P.KWH_WIRE_BYTES
KWH_DOT_DIV = P.KWH_DOT_DIV
KWH_OAD_698CONV = P.KWH_OAD_698CONV



def kwh_bswap64(x):
    """u64 大端↔小端互换(来路帧是大端, g_CurkWh 是本机小端)。None 透传。"""
    if x is None:
        return None
    return int.from_bytes((x & ((1 << 64) - 1)).to_bytes(8, "little"), "big")


def kwh_conv_sign(oad):
    """该 OAD 该带符号还是不带(`KWH_OAD_698CONV` 那一条的 sign 字段); 没登记 ⇒ None。

    记录行里那一列的对象位**该**是哪个由它定: 有符号 `0x14`(D_Long64) / 无符号 `0x15`(D_Long64Un),
    两个都 8 字节(`DLT698App.c:143-144`)。判据只收"两者之一"就放行的话, 固件把某列的符号性翻个个儿
    时值照样读得回, 这一路不进分母(CLAUDE.md 第 30 条)。
    """
    p = KWH_OAD_698CONV.get(oad)
    if p is None:
        return None
    _num, _dot, _code, sign = p
    return sign


def kwh_to_698val(v, oad):
    """g_CurkWh 存的 u64 经 `oad` 这条去路**该**给出的值; `oad` 没登记 ⇒ None(**拒算**)。

    2026-09-18 改: 旧写法 `(v & MASK5) // 100` 把"40 位 + 除 100 + HEX + 无符号"当成所有电类所有
    OAD 的同一把尺子。实际这四样**逐调用点定**(DLT698App.c 里有 `dot=len/2`, 也有 `dot=6` —— 后者
    落 switch 的 `default`, 一次都不除)。故改成按 OAD 查表, **查不到就拒算**, 不许拿 `//100` 顶上去:
    宁可让判据记"未证", 也不许把一个凭默认值算出来的数当成证据。
    换算顺序与固件一致(`Convert_EnyData`, kWhData.c:227-246): 先从 5 字节拼出 u64, 再按 `dot` 除,
    最后按 `num` 字节写出(高 3 字节恒 0)。None 透传。"""
    if v is None:
        return None
    p = KWH_OAD_698CONV.get(oad)
    if p is None:
        return None
    _num, dot, code, sign = p
    if code != "HEX" or sign > 1:
        # 本表只登记 HEX; 符号那两档都实现(固件 `Convert_EnyData` 的 `sign>=2` 是**空转**返回,
        # 故 sign 只有 0/1 两档可算 —— 登记成 0/1 之外的, 是表登记错了, 拒算)
        return None
    u = v & KWH_VAL_MASK5
    div = KWH_DOT_DIV.get(dot, 1)
    if sign == 1 and (u & (1 << (8 * KWH_WIRE_BYTES - 1))):   # 40 位的最高位是符号位
        s = u - (1 << (8 * KWH_WIRE_BYTES))                   # s64 = u64 - 0x10000000000 (kWhData.c:250)
        return -((-s) // div)                                 # C 的 `/` 向零截断, Python 的 `//` 向下取整
    return u // div


def kwh_rows(blob, neny=CURKWH_NENY, stride=CURKWH_STRIDE):
    """g_CurkWh 原始字节 → {(eny, rate): 10 字节的项}; 越出 blob 的项不出现(不补零, 免造假证据)。"""
    out = {}
    for eny in range(neny):
        for rate in range(stride):
            i = (eny * stride + rate) * CURKWH_SLOT
            if i + CURKWH_SLOT <= len(blob):
                out[(eny, rate)] = bytes(blob[i:i + CURKWH_SLOT])
    return out


def kwh_store_plan(chunk=120):
    """`g_CurkWh` 整块怎么读(**纯规划, 不碰串口**): `(绝对地址, 总长, 每片长)` 或 None(画像里没这个变量)。

    ⚠ AA80 单次负载 ≤128B, 1300B 要分 11 次(约 5~6s) ⇒ 读回的是**逐次拼接的非原子快照**;
    费率项/总项在空载台面上不变, 故可用, 但**断点观测那一份才是原子口径**, 判①以断点观测为准。
    读由脚本自己做(`watch.read_mem_aa80` 逐片), 解码走 `kwh_rows`。"""
    a = watch._pvar_addr(CURKWH_VAR)
    if not a:
        return None
    return (a[0], a[1], chunk)


def energy_col_parse(raw):
    """698 电能整列元素的**布局无关**解析: `01 <count> <每项 1B lead + 8B 大端值>`
    → {"count": n, "slots": [(lead, 值), …], "tail": b"…"} 或 None(不是 `01 xx` 数组形态)。
    ⚠ 不假定项数 —— 项数由线上字节给; 实测本台 5 项 = `1+Get_RatePara(3)`(**运行期**费率数 4,
      见 DLT698App.c:6957), 与 g_CurkWh 的**编译期**步长 13 是两个数, 别混(DLT698App.c:6954 的
      `#ifndef VER_20Edit` 分支才用编译期值, 本版走的是 `#else`)。
    ⚠ **值按大端解**(2026-09-11 实测对拍坐实): 698 线上整数 MSB-first, 项里的载荷也是;
      早先按小端解, 空载全 0 时看不出来, 一遇非零就错。"""
    b = bytes(raw or b"")
    if len(b) < 2 or b[0] != 0x01:
        return None
    cnt = b[1]
    need = 2 + cnt * 9
    if len(b) < need:
        return None
    slots = []
    for i in range(cnt):
        o = 2 + i * 9
        slots.append((b[o], int.from_bytes(b[o + 1:o + 9], "big")))
    return {"count": cnt, "slots": slots, "tail": b[need:]}


def energy_ele_raw(ud):
    """普通 GET 电能对象应答 ud 里的值元素原始字节 = 自数组头 `01 <count>` 起(含)到 ud 末尾。

    定位用**自尾向头扫 + 长度自洽**, 不用固定偏移: 应答是 `85 01 <PIID> <OAD4> <01 标志?> 01 <count> …`,
    标志位到底有没有、有几个字节, 不该由本函数猜(猜错就是静默取到错字节)。自洽判据 = 数组声明
    的 `2+count*9` 字节要能落到 ud 里, 且尾部余量 ≤4B(行尾 `00 00` 外壳那点噪音)。自尾向头扫 ⇒
    `01 01 05 …` 这种"标志位也是 01"的形态不会被当成 `01 01`(那一条长度对不上)。找不到返回 None。"""
    b = bytes(ud or b"")
    for i in range(len(b) - 2, -1, -1):
        if b[i] != 0x01:
            continue
        cnt = b[i + 1]
        if cnt > 32:
            continue
        need = 2 + cnt * 9
        if need <= len(b) - i <= need + 4:
            return b[i:]
    return None


def kwh_wb_vars(rcs=None):
    """断点观测停 `CURKWH_BP_ANCHOR`(:448)时要读的 gdb 表达式(逐项标量, 不整条读数组——
    gdb 对 char 数组按字符串打印, 又难解又不稳; 与 3-2 的 `swTime[0..4]` 同一律)。
      · `'Run_TaskVessel'::STR1_Index` —— 静态局部量, 帧在 g_SPIMBuff 中的起点(:987/:1022)。
      · 帧侧: `g_SPIMBuff[STR1_Index + ENE_FRAME_OFF + ENE_FRAME_STEP*电类号 + ENE_FRAME_LEAD]`
        —— 帧头(STR1_Index)后 ENE_FRAME_OFF = 110 字节起是第一类, 每类占 ENE_FRAME_STEP = 9 字节
        (1 字节类型码 + 8 字节数值), 十类起点 110/119/128/…/191 逐个差 9(kWhData.c:374/:381/…);
        ENE_FRAME_LEAD = 1 是跳过 698 的类型码字节。数值 8B。
      · 存侧: `g_CurkWh[CURKWH_STRIDE*电类号][0]` 值 + `g_CurkWh[CURKWH_STRIDE*电类号][8]` 的 2B CRC
        —— CURKWH_STRIDE = 1+C_RateNum = 13, 是每类电量占的**项数**(1 个「总」+ 12 个费率项),
        所以第 n 类的「总」在第 13*n 项。注意这两处**不是同一个单位**: 帧侧数的是字节, 存侧数的是项。
    ⚠ 表达式里**只有 STR1_Index 是变量**, 其余全是常量 —— 这是故意的: 项地址换算所需的四个常量(帧内起点 ENE_FRAME_OFF=110 / 帧内类步长 ENE_FRAME_STEP=9
      / 存侧项步长 CURKWH_STRIDE=13 / 电类数 CURKWH_NENY=10)集中在库里一处
      集中在库里一处, 脚本只给"要哪几个电类"。"""
    rcs = range(CURKWH_NENY) if rcs is None else rcs
    out = [WB_STR1, WB_FRAME0]
    out += ["*(unsigned long long*)&g_SPIMBuff['Run_TaskVessel'::STR1_Index+%d+%d*%d+%d]"
            % (ENE_FRAME_OFF, ENE_FRAME_STEP, k, ENE_FRAME_LEAD) for k in rcs]
    out += ["*(unsigned long long*)&g_CurkWh[%d*%d][0]" % (CURKWH_STRIDE, k) for k in rcs]
    out += ["*(unsigned short*)&g_CurkWh[%d*%d][8]" % (CURKWH_STRIDE, k) for k in rcs]
    return tuple(out)


# 断点观测表达式表里**头两条的位置是承重的**(`kwh_wb_exprs` 与取值的下标都按它俩排), 故立成常量。
WB_STR1 = P.WB_STR1
WB_FRAME0 = P.WB_FRAME0
WB_FRAME0_OK = P.WB_FRAME0_OK



def kwh_num(text):
    """gdb `-data-evaluate-expression` 的文本 → int; 读不到("optimized out"/None/非数字) → None。

    ⚠ **必须吃掉 gdb 的注释尾巴**(2026-09-11 实踩): 它打印的是 `3 '\\003'`、`104 'h'`、`150`,
      枚举/Bool 则是 `TRUE (170)` —— 直接 `int(t)` 在**第一种形态上必炸**(本项首跑就因此把
      "命中且读到了值"误判成"见证字节读不到", 判①记了未证)。故取第一个空白分隔的 token;
      它若不成整数(如 `TRUE`), 再退到括号里那个(`(170)`)。"""
    if text is None:
        return None
    t = str(text).strip()
    if not t:
        return None
    cands = [t.split()[0]]
    if "(" in t:
        cands.append(t.split("(")[-1].rstrip(")"))
    for c in cands:
        try:
            return int(c, 0)
        except ValueError:
            continue
    return None


def energy_mirror_criteria():
    """1-2 的**预设判据条目**(测试前定死; 源 = ledger.md 1-2「观察与判据」的『判过』那一句)。
    原文『698 读回值 = 计量芯来值 = 费率分摊和; 多帧稳定一致』拆成 11 条钥匙。③④ 各拆 a/b
    —— 不拆就会拿固件**明确判定不可用**的字节去换算/求和, 再把「前提不成立」记成
    「等式不成立」(假 FAIL), 或者反过来把「固件按设计回落」记成「取项错」。
    ⑥/⑦ 是后来加的**对照两半**: ④ 量"这一趟开跑时"的那份快照, ⑥ 量"清完零又正常跑"的,
    ⑦ 量"同一张表断电 ≥5s 再上电后"的; 三处同一把尺子, 差别只在做什么动作。"""
    return {
        "①": "计量芯来值 → g_CurkWh 逐字节无损(10 电类, 帧内每类 9 字节 与 g_CurkWh 每 13 项取一个的『总』项 同值)",
        "②": "写值同时写 CRC: 10 个「总」项的 2B CRC 全部与 Fetch_CRC(值) 配对",
        # ⚠ 这四条**逐字抄 md**: 改 md 就得同步改这儿。
        "③a": "698 读回值 == g_CurkWh 存值经该 OAD 去路换算(只比 CRC 配对的项)",
        "③b": "CRC 不配对的项被拦下(回读既不等于该项字节的换算值, 各校不过的项回读值又彼此相同)",
        "④a": "费率分摊的前提: 总项与**运行期费率数**个费率项(rate 1..费率数)的 2B CRC **全部配对**(有一个项配不上, 那一侧就没有可信值; 编译期容量 12, rate 5..12 是没人写的死区, 不计入)",
        "④b": "费率分摊和 == 总(总项非零的电类, 其运行期费率数个费率项 Σ 等于总项; **仅当 ④a 成立时这一条才有分辨力**)",
        "⑤": "多帧读回稳定一致(同一 OAD 连读 n 轮逐字节相同)",
        # ⑥/⑦ 是**同一件事的对照两半**(2026-09-23 加): 一次跑里前一半量"清零后正常跑", 后一半量
        # "同一张表断电 ≥5s 再上电后"。两半用同一把尺子(④ 那份算式), 差别只在掉电这一个动作上。
        # ⚠ 逐字抄 md: 改 md 就得同步改这儿。
        "⑥a": "645 电表清零后正常跑, 10 个电类的费率项(rate 1..运行期费率数)的 2B CRC 全部配对(干净态下分摊确实把每一项写了进去)",
        "⑥b": "645 电表清零后正常跑, 费率分摊和 == 总(与 ④b 同一把尺子, 量的是清零后的那一份快照)",
        "⑦a": "同一跑次断电 ≥5s 再上电后, 费率项 2B CRC 仍全部配对(上电时费率项被复位或从 EEPROM 恢复, 不是随机 RAM)",
        "⑦b": "同一跑次断电 ≥5s 再上电后, 费率分摊和 == 总 仍成立",
    }


# ---- 1-2 的六步: 一步一个积木, 脚本正文一行一步(顺序即步骤, 见 project/tests/_test_1_2_energy_mirror.py) ----
# 每一步的判据口径、台面预期与"为什么这么切"写在上面 1-2 段头; 下面每个函数只说自己那一步做什么。
# 读动词(首参 ser)返回数据, 判据动词返回证据记录 —— 与 4-6 段同一形状(见 4-6 段的薄清单样板)。


def _rec_bag():
    """各步共用的记账袋: `recs`(证据记录) / `why`(半途中止的原因) / `add`(记一条证据)。

    `add` 的形参与 `common.judge.rec` 一致(含 `trig`: 这一条是不是**注入**造出来的状态)。"""
    recs, why = [], []

    def add(name, ok, detail, crit=None, obs=judge.SERIAL, falsify=None, trig=None):
        recs.append(rec(name, ok, detail, crit=crit, obs=obs, falsify=falsify, trig=trig))

    return recs, why, add


def _rec_close(recs, why):
    """一步的收尾: 半途中止的原因打屏(原先由 `ctx.hold` 代打的那几行), 再把证据记录交给脚本。"""
    for w in why:
        print("      · %s" % w)
    return recs


def unproven_records(entries, why, falsify=None):
    """半途中止时把**没做成**的条目一次记全 —— 每条 `ok=None`(没做成绝不当 FAIL)。

    与 `_rec_bag` / `_rec_close` 同住 —— 它不认表也不认子项, 哪个子项的脚本要半途中止都能用
    (10-1 保电 / 12-1 保电解除 / 12-2 液晶拉闸)。

    `entries` 由脚本给(纯数据): 每条 `(认领号, 判据文字, 观测种类)` —— 与正常路径上那几条
    **同名同号同观测**; 中止那一趟少记一条、或把某条记到别的观测名下, 这一趟的分母就跟着变样。
    `falsify` 给一个字符串(所有条目共用)或一张 `{认领号: 字符串}` 表。
    """
    recs, w2, add = _rec_bag()
    for crit, text, obs in entries:
        add("%s %s" % (crit, text), None, why, crit=crit, obs=obs,
            falsify=(falsify.get(crit) if isinstance(falsify, dict) else falsify)
            or "不做这一次时无从判 —— 本记录不作为固件证据")
    w2.append(why)
    return _rec_close(recs, w2)


def energy_mirror_intro():
    """第一步 · 开场: 本项在比什么 / g_CurkWh 长什么样 / 两边单位与进位制的差别 —— 只打印, 不判。"""
    print("\n== 1-2 电能数据 · 与计量芯保持一致 ==")

    def say_block():
        """开场把"在比什么 / 表长什么样 / 单位与进位制"讲清楚 —— 这份 log 要能直接拿去讲。"""
        print("""
【本项在比什么】
  计量芯把电能数据用串口帧送给管理芯, 管理芯原样存进 RAM 里的 g_CurkWh 这张表; 电表再用
  698 规约把表里的数报给主站。本项做三件事, 都是只读:
      ① 计量芯送来的数与存进去的数 是不是同一个(要停核才看得到送来的那一侧)
      ② 表里存的那一格 是不是"完整写过的"(看给它配的 2 字节校验码对不对)
      ③ 698 报出去的数 与 表里存的数 对不对得上(外加: 连读几轮稳不稳)

【g_CurkWh 这张表长什么样】
  10 个电类 x 13 列, 每格 10 字节:
      前 8 字节 —— 电量计数值, **小端**(低位字节在前)
      后 2 字节 —— 校验码; 算不对说明这一格没被完整写过, 固件自己就不认它
  列的含义: 第 0 列 = 总电量; 第 1..12 列 = 费率 1..12 各自分摊到的电量。
  本台表只配了 4 个费率(实测 698 整列只报 1+4 = 5 项) ⇒ 第 5..12 列没有程序会去写,
  是死区, 不参与比对。

【单位与进位制(两边不一样, 别直接摆在一起比)】
  · 表里存的: **原始计数**, 小端; 1 个单位 = 0.000001 kWh。整数, 十进制打印。
  · 698 报的: 8 字节 **大端**, 前面还有 1 字节类型标记; 1 个单位 = 0.0001 kWh。整数, 十进制打印。
  · 两者的换算由固件 Convert_EnyData(kWhData.c:215)按该 OAD 的 dot 值做 ——
      dot 就是报文里保留几位小数: dot=0 除 1000000 / 1 除 100000 / 2 除 10000 /
      3 除 1000 / 4 除 100(:230-238)。"除 100"配"保留 4 位小数", 正好把表里的 6 位小数
      砍成报文要的 4 位 ⇒ 表里 1 个单位是 698 报文中 100 个单位。
  · 本项读的 10 个 OAD 全是 dot=4 ⇒ 一律除 100。另: 固件算完只取低 5 字节(40 位),
      高 3 字节是残留、不参与 —— 所以表里那 8 字节常看着像天文数字, 那是高位垃圾, 不是电量。
  · 本台编译开关 TP_SoftVer = TP_Debug(UserCfg.h:13), 故 kWhData.c:246 那段
      `#if (TP_SoftVer == TP_Release) u64 /= C_IMP` **没有编进去**, 换算里没有脉冲常数这一项。
  · 比对一律在**整数**上做, 不折浮点; 下面为了人读才额外标 kWh 近似值。""")

    say_block()



def energy_mirror_multiframe(ser, oads=("00000400", "00100400"), n_rounds=3, wait=3.0):
    """第二步 · ⑤ 读回: 每个 OAD 连读 `n_rounds` 轮整列(698 普通 GET) → `{oad: [每轮整列字节|None]}`。

    只读不比 —— 判定在 `energy_mirror_stable`。"""
    # ---------- ⑤ 多帧稳定(串口观测) ----------
    cols = {}
    for oad in oads:
        cols[oad] = []
        for _r in range(n_rounds):
            ud = read_oad_ud(ser, oad, wait=wait)
            raw = energy_ele_raw(ud) if ud else None
            cols[oad].append(bytes(raw) if raw else None)
    return cols


def energy_mirror_stable(cols, oads=("00000400", "00100400"), n_rounds=3):
    """第三步 · ⑤ 判定: 同一 OAD 各轮整列逐字节相同 → 每个 OAD 一条证据。"""
    recs, why, add = _rec_bag()
    for oad in oads:
        rounds = cols[oad]
        miss = sum(1 for x in rounds if x is None)
        if miss:
            add("⑤ %s 多帧读回稳定(n=%d)" % (oad, n_rounds), None,
                "%d/%d 轮没读到整列元素" % (miss, n_rounds),
                crit="⑤", falsify="帧间内容抖动 ⇒ 各轮字节不等")
            continue
        same = all(x == rounds[0] for x in rounds)
        p = energy_col_parse(rounds[0])
        e = CURKWH_OAD_ROW.get(oad)
        print("   698 报回 %s(%s) 的整列 —— 共 %s 项, 每项 9 字节 = 1 字节类型标记 + 8 字节大端整数:"
              % (oad, CURKWH_ENY_NAMES[e] if e is not None else "组合有功(表里无对应行)",
                 p["count"] if p else "?"))
        if p:
            for r, (lead, v) in enumerate(p["slots"]):
                print("      第 %d 项 %-9s 类型 0x%02X | %s  ->  整数 %-12d  (报文单位 = 0.0001 kWh, 即 %.4f kWh)"
                      % (r + 1, "(总电量)" if r == 0 else "(费率 %d)" % r, lead,
                         v.to_bytes(8, "big").hex(" ").upper(), v, v / 10000.0))
            if p["tail"]:
                print("      尾部 %s(数组声明之外的余量)" % p["tail"].hex(" ").upper())
        else:
            print("      (不是 `01 <项数>` 数组形态, 原文 %s)" % rounds[0].hex(" ").upper())
        add("⑤ %s 多帧读回稳定(n=%d)" % (oad, n_rounds), same,
            "%d 轮字节%s相同, 首轮 %s" % (n_rounds, "" if same else "不", rounds[0].hex(" ").upper()),
            crit="⑤", falsify="帧间内容抖动 ⇒ 各轮字节不等")

    return _rec_close(recs, why)


def energy_mirror_store_decode(blob):
    """第四步 · 存储侧底账的**纯解码**(不碰串口): 脚本读回的 `g_CurkWh` 原始字节 → `{(电类, 费率): 10B 格}`。

    读不成(`blob is None`)返回 `None` —— ②③④ 三步据此各自记「未做成」, 不拿空数据凑结论。"""
    slots = kwh_rows(blob) if blob else None
    if slots is None:
        print("   [白盒] AA80 直读 g_CurkWh 失败 ⇒ ②③④ 无存储侧证据")
        return None
    tot = dict((e, slots.get((e, 0))) for e in range(CURKWH_NENY))
    print("   [白盒] 直接从管理芯 RAM 读 g_CurkWh 的 10 个「总」格(第 0 列) —— 逐次读回拼接, 不是原子快照:")
    print("          列格式: 原始计数(小端 8B) 校验码(2B) 检查 | 折合 kWh")
    for e in range(CURKWH_NENY):
        s = tot[e]
        ok = kwh_slot_ok(s)
        print("            %-5s 计数 %-22s 校验 %-5s %-4s | %.6f kWh"
              % (CURKWH_ENY_NAMES[e], kwh_slot_val(s) if s else "(缺)",
                 s[8:10].hex(" ").upper() if s else "--",
                 {True: "对", False: "不对", None: "?"}[ok],
                 kwh_slot_units(s) if s else 0.0))

    return slots


def energy_mirror_crc(slots):
    """第五步 · ② 判定: 10 个「总」格的 2B 校验码与值是否配对(校验码只在写值之后写) → 一条证据。"""
    recs, why, add = _rec_bag()
    if slots is None:
        add("② 10 个总项 CRC 与值自洽", None,
            "AA80 直读整块 g_CurkWh 失败 ⇒ 无存储侧证据(不拿空数据凑结论)",
            crit="②", falsify="写值不写 CRC ⇒ 该项缓存恒无效、Get_CurkWh 回退 EEPROM")
        return _rec_close(recs, why)
    tot = dict((e, slots.get((e, 0))) for e in range(CURKWH_NENY))
    # ---------- ② 总项 CRC 自洽 ----------
    oks = [kwh_slot_ok(tot[e]) for e in range(CURKWH_NENY)]
    bad = [CURKWH_ENY_NAMES[e] for e in range(CURKWH_NENY) if oks[e] is not True]
    add("② 10 个总项 CRC 与值自洽", all(x is True for x in oks),
        "全部配对" if not bad else "不配对/形态不规: %s" % ",".join(bad),
        crit="②", falsify="写值不写 CRC ⇒ 该项缓存恒无效、Get_CurkWh 回退 EEPROM")

    return _rec_close(recs, why)


# 费率项范围定不下来时那句原因 —— ④/⑥/⑦ 三处共用一句, 不各写一份。
KWH_ALLOC_RANGE_WHY = "本次线上整列项数读不到(或各 OAD 报的费率数不一致) ⇒ 费率项范围定不下来"


def kwh_alloc_scan(slots, cols, oads):
    """费率分摊扫描 —— 在**一份存值快照**上算一遍「前提」与「等式」, 算式只这一处。

    ④(本次存值)、⑥(清零后正常跑)、⑦(断电重启后)量的是同一件事, 只是取的快照不同, 故共用这一份。
    费率项范围为什么是"本次线上整列项数减一", 见 `energy_mirror_alloc` 里那段注释。

    返回**具名字段**字典(调用方按名字取, 不按下标): `live_n`(运行期费率数; -1 = 各 OAD 报的不一致,
    None = 读不到) · `lines`(逐电类明细, 给人读) · `ok_crc`(前提: 总项与 rate 1..live_n 的 2B CRC 全配对)
    · `ok_sum`(等式: Σ费率 == 总) · `nchk`(总非零、因而参与判定的电类数)
    · `n_prem`(前提成立、因而等式有分辨力的电类数)。
    """
    tot = dict((e, slots.get((e, 0))) for e in range(CURKWH_NENY))
    live_n = None
    for oad in oads:
        _p = energy_col_parse(cols[oad][0]) if cols[oad][0] else None
        if not _p or _p["count"] < 2:
            continue
        _c = _p["count"] - 1
        live_n = _c if live_n in (None, _c) else -1      # -1 = 各 OAD 报的费率数不一致
    dead_zone = [r for r in range(max(live_n, 1) + 1, CURKWH_STRIDE)] if (live_n or 0) > 0 else []

    lines = []
    ok_crc = True
    ok_sum = True
    nchk = 0
    n_prem = 0                                  # 前提成立、因而等式有分辨力的电类数
    if live_n is not None and live_n > 0:
        for e in range(CURKWH_NENY):
            s = tot[e]
            if s is None or kwh_slot_val(s) == 0:
                continue                # 空载电类: 总分摊恒等式无分辨力, 不计入(避免 0==0 假通过)
            nchk += 1
            rates = [slots.get((e, r)) for r in range(1, live_n + 1)]
            bad_rate = [r for r in range(1, live_n + 1) if kwh_slot_ok(rates[r - 1]) is not True]
            tot_bad = kwh_slot_ok(s) is not True
            premise = (not tot_bad) and (not bad_rate)
            ok_crc = ok_crc and premise
            raw = [kwh_slot_val(x) for x in rates if x is not None]
            if premise:
                n_prem += 1
                ok_sum = ok_sum and (sum(raw) == kwh_slot_val(s))
                lines.append("%s: 总 %d  费率 1..%d 各列之和 %d  (原始计数, 1 单位 = 0.000001 kWh)"
                             % (CURKWH_ENY_NAMES[e], kwh_slot_val(s), live_n, sum(raw)))
            else:
                lines.append("%s: 总 %d; 费率 1..%d 各列计数 %s; 校验码不对的列 %s%s  (总列是 0.000001 kWh 为单位)"
                             % (CURKWH_ENY_NAMES[e], kwh_slot_val(s), live_n, raw, bad_rate or "无",
                                "; 总列校验码也不对" if tot_bad else ""))
        if dead_zone:
            lines.append("第 %s 列是死区(本台只有 %d 个费率, 第 %d 列往后固件按设计不写, 不计入): %s"
                         % ("/".join(str(x) for x in dead_zone), live_n, live_n + 1,
                            [kwh_slot_val(slots[(e, r)]) if (e, r) in slots else None
                             for e in range(CURKWH_NENY) for r in dead_zone][:8] + ["…"]))
    return {"live_n": live_n, "lines": lines, "ok_crc": ok_crc, "ok_sum": ok_sum,
            "nchk": nchk, "n_prem": n_prem}


def energy_mirror_alloc(slots, cols, oads=("00000400", "00100400")):
    """第六步 · ④a/④b 判定: 费率分摊 —— 前提(总项与 rate 1..运行期费率数 的 CRC 全配对)与等式(Σ费率 == 总)。

    范围取自本次线上整列项数减一(第 5..12 列是固件按设计不写的死区); 推导见下面 ④ 那一段。

    前提不成立时 ④b 记未证 —— 不拿无效字节凑一个和出来。"""
    recs, why, add = _rec_bag()
    if slots is None:
        add("④a 费率项 CRC 全部配对(费率分摊的前提)", None,
            "AA80 直读整块 g_CurkWh 失败 ⇒ 无存储侧证据", crit="④a",
            falsify="费率项从未被合法写过 ⇒ 该项缓存恒无效、Get_CurkWh 回退 EEPROM")
        add("④b 费率分摊和 == 总", None,
            "④a 未证 ⇒ 等式也无分辨力", crit="④b",
            falsify="费率项读污染/漏加 ⇒ 和不等于总")
        return _rec_close(recs, why)
    # ---------- ④a 前提 / ④b 等式: 费率分摊 ----------
    # **2026-09-18 拆的**, 原先是挤在一条里: `good = (not bad_rate) and (ssum == total_size)`。
    # 那样写有两处毛病, 都是**测试侧**的:
    #   (a) `bad_rate` 非空时后半截被短路掉, 却仍然拿**已知无效**的字节去求和, 再拿这个和去和一个
    #       有效值比 —— 实测两边差 17 个数量级。那个 Σ **不构成"总"的任何一侧**, 这种比较不成立。
    #   (b) 「前提不成立」与「等式不成立」被记成同一个『不满足』, 读的人分不出该去查哪一处。
    # ⇒ 前提(④a)与等式(④b)分开两条; ④a 不成立时 ④b 记 None(未证), **不拿无效数据凑一个数出来**。
    #   逐项原始值照旧打印 —— 那是人判断所需的材料, 只是不再拿它做算术。
    # **范围 = 运行期费率数, 不是编译期 C_RateNum** —— 2026-09-18 逐条对源码定的:
    #   · `g_CurkWh` 的**行步长**是编译期 `1+C_RateNum = 13`(kWhData.c:16);
    #   · 但 `Update_Rate_Energy`(:448) 只写 `Get_RateNo()` 当前费率**那一列**, 而 `Get_RateNo`
    #     的取值经 `Calculate_RateNo`(TaskRate.c:276) 被 `g_RatePara[nRateNum]` 夹在 1..费率数 内;
    #   · 698 整列读也只给 `1+Get_RatePara(3)` 个项(DLT698App.c:6957 / :7007, `VER_20Edit` 分支)
    #     —— 本台实测 5 项 ⇒ 运行期费率数 = 4。
    #   ⇒ **rate 5..12 是这台表上谁都不写的死区**, 项里是 `__no_init` 残留。把死区也算进"费率项",
    #     等于要求固件去写它按设计不写的项 —— 那是拿测试量错固件。故范围取自本次线上整列读回的
    #     项数减一; 各 OAD 报的必须一致; 读不到就记**未证**, **不许拿 12 顶上去**。
    #     死区的逐项原值照旧打印(那是材料), 只是不进等式。
    _scan = kwh_alloc_scan(slots, cols, oads)
    live_n, lines = _scan["live_n"], _scan["lines"]
    ok4a, ok4b = _scan["ok_crc"], _scan["ok_sum"]
    nchk, n_prem = _scan["nchk"], _scan["n_prem"]

    if live_n is None or live_n < 0:
        print("   ④ 明细: %s" % KWH_ALLOC_RANGE_WHY)
        add("④a 费率项 CRC 全部配对(费率分摊的前提)", None,
            KWH_ALLOC_RANGE_WHY + " ⇒ **不拿 12 顶上去**", crit="④a",
            falsify="费率项从未被合法写过 ⇒ 该项缓存恒无效、Get_CurkWh 回退 EEPROM")
        add("④b 费率分摊和 == 总", None,
            "④a 未证 ⇒ 等式也无分辨力", crit="④b",
            falsify="费率项读污染/漏加 ⇒ 和不等于总")
    elif nchk == 0:
        add("④a 费率项 CRC 全部配对(费率分摊的前提)", None,
            "10 个总项全为 0 ⇒ 本台面这条无分辨力(0==0 不算证据); 运行期费率数 = %d" % live_n,
            crit="④a", falsify="费率项从未被合法写过 ⇒ 该项缓存恒无效、Get_CurkWh 回退 EEPROM")
        add("④b 费率分摊和 == 总", None,
            "本台面 ④a 无分辨力 ⇒ 等式也无分辨力", crit="④b",
            falsify="费率项读污染/漏加 ⇒ 和不等于总")
    else:
        print("   ④ 明细 —— 费率分摊: 每个电类的「总」列 是否等于 费率 1..%d 各列之和" % live_n)
        print("      两侧都是原始计数(1 单位 = 0.000001 kWh), 同一把尺子; 各列要先能过校验码才拿来相加")
        for _l in lines:
            print("        " + _l)
        add("④a 费率项 CRC 全部配对(费率分摊的前提)", ok4a,
            ("总项与 rate 1..%d(= 运行期费率数)的 2B CRC 全部配对" % live_n)
            + ("" if ok4a else " —— 明细: " + " | ".join(lines)),
            crit="④a", falsify="费率项从未被合法写过 ⇒ 该项缓存恒无效、Get_CurkWh 回退 EEPROM")
        if n_prem == 0:
            add("④b 费率分摊和 == 总", None,
                "④a 不满足 ⇒ rate 1..%d 里没有可信值, 求和式两侧不可比(不拿无效字节凑一个和出来)"
                % live_n, crit="④b", falsify="费率项读污染/漏加 ⇒ 和不等于总")
        else:
            add("④b 费率分摊和 == 总", ok4b,
                "前提成立的电类 %d 个: %s" % (n_prem, " | ".join(lines)),
                crit="④b", falsify="费率项读污染/漏加 ⇒ 和不等于总")

    return _rec_close(recs, why)


def energy_mirror_compare(slots, cols, oads=("00000400", "00100400")):
    """第七步 · ③a/③b 判定: 698 整列逐项 vs 存值 —— CRC 配对的项比数值, 不配对的项看有没有被拦下。"""
    recs, why, add = _rec_bag()
    if slots is None:
        add("③a 698 读回值 == g_CurkWh 存值经该 OAD 去路换算(只比 CRC 配对的项)", None,
            "AA80 直读整块 g_CurkWh 失败 ⇒ 无存储侧证据", crit="③a",
            falsify="回读路径取错项/错电类/定标不符 ⇒ 与换算后的存值不等")
        add("③b CRC 不配对的项被拦下(回读既不等于该项字节的换算值, 各校不过的项回读值又彼此相同)", None,
            "AA80 直读整块 g_CurkWh 失败 ⇒ 无存储侧证据", crit="③b",
            falsify="校不过的项字节被原样放行 ⇒ 回读等于该项字节的换算值")
        return _rec_close(recs, why)
    # ---------- ③a/③b 698 读回 vs 存储 ----------
    # 拆 a/b 的理由与 ④ 同源(2026-09-18 改): 项有两种命, 不该用同一把尺子量 ——
    #   · CRC **配对** ⇒ 固件 `Get_CurkWh` 认它 ⇒ 该比的是"回读 == 该 OAD 去路换算(存值)"。
    #   · CRC **不配对** ⇒ 固件在 `Check_CRC` 那一步就不认它, 走 EEPROM/0 回落。这时拿项里那串字节
    #     去换算再跟回读比, 等于**把一个固件明确判定不可用的数当成期望值** —— 固件回落得越对, 反而
    #     越判"不满足"。旧写法就是这么报的假 FAIL(2026-09-18 那次:n 不等全落在 CRC 校不过的项上)。
    #     校不过的项该比的是**固件有没有把它拦下**: 回读值既不能等于该项字节的换算值(没放行), 且各校不过的项
    #     回读值应彼此相同(回落口径给的是一个值, 不是逐项各异的)。两条合起来才有分辨力。
    # 组合列(eny=0)在 g_CurkWh 里没有对应行(固件按组合方式现算), 故只对 g_CurkWh 有行的电类逐项比。
    n_ok3a, n_bad3a, det3a = 0, 0, []
    n_ok3b, n_bad3b, det3b = 0, 0, []
    bad3b_vals = []                              # CRC 校不过的项各自的 698 回读值
    n_nocv = 0                                   # 落在换算登记表外的 OAD ⇒ 拒算, 记未证
    print("   ③ 逐项对拍 —— 表里存的数 与 698 报出去的数")
    print("      存侧的数先按该 OAD 的 dot 换算(本项 10 个 OAD 全是 dot=4 ⇒ 除 100), 再与 698 报回的整数比。")
    print("      比之前先看存侧的校验码: 对不上就说明这一格没被完整写过, 固件自己不认它 —— 那种格")
    print("      不参与比, 只看它有没有被固件拦下来(回读既不该等于它的换算值, 各坏格回读也不该各报各的)。")
    n = 0                                        # 对拍序号, 跨 OAD 连续编, 方便按号点数
    for oad in oads:
        e = CURKWH_OAD_ROW.get(oad)              # 显式表, 不用 byte1>>4 推(谐波族会撞车, 见常量说明)
        if e is None:
            print("      %s: 这是组合电量, 表里没有对应行(固件按组合方式现算), 不参与对拍" % oad)
            continue                             # 组合类等无 g_CurkWh 行的 OAD: 无可对拍对象
        p = energy_col_parse(cols[oad][0]) if cols[oad][0] else None
        if not p:
            print("      %s: 这次没读到整列, 不参与对拍" % oad)
            continue
        for r, (_lead, val) in enumerate(p["slots"]):
            s = slots.get((e, r))
            if s is None:
                continue
            exp = kwh_to_698val(kwh_slot_val(s), oad)
            if exp is None:
                n_nocv += 1
                print("      %s 第 %d 项: 该 OAD 没登记换算参数 ⇒ 拒算, 不猜" % (oad, r + 1))
                continue
            n += 1
            raw = kwh_slot_val(s)
            ok = kwh_slot_ok(s)
            head = "%s · 第 %d 项(%s)" % (CURKWH_ENY_NAMES[e], r + 1,
                                        "总电量" if r == 0 else "费率 %d" % r)
            if ok is True:
                if val == exp:
                    n_ok3a += 1
                else:
                    n_bad3a += 1
                    if len(det3a) < 6:
                        det3a.append("%s 第 %d 项: 698 报 %d, 存侧换算后应是 %d(存侧计数 %d)"
                                     % (CURKWH_ENY_NAMES[e], r + 1, val, exp, raw))
                print("      [%d] %s" % (n, head))
                print("          存侧: 表里第 %d 行第 %d 列, 10 字节 %s | %s"
                      % (e, r, s[0:8].hex(" ").upper(), s[8:10].hex(" ").upper()))
                print("                按小端读成整数     = %d   (原始计数; 高 3 字节固件不参与, 看着大也别当电量)"
                      % raw)
                print("                固件只取低 5 字节   = %d   = %.6f kWh   (原始计数 1 单位 = 0.000001 kWh)"
                      % (raw & KWH_VAL_MASK5, (raw & KWH_VAL_MASK5) / 1000000.0))
                print("                该 OAD dot=4, 除 100 = %d   <- 这才是 698 该报的数" % exp)
                print("                校验码: 手算 %s, 格子里 %s  ⇒ 对得上, 这一格是完整写过的"
                      % (kwh_crc2(s[0:8]).hex(" ").upper(), s[8:10].hex(" ").upper()))
                print("          读侧: 698 第 %d 项 9 字节 %s | %s   (1 字节类型标记 + 8 字节大端)"
                      % (r + 1, "%02X" % _lead, val.to_bytes(8, "big").hex(" ").upper()))
                print("                按大端读成整数     = %d   (报文的数, 1 单位 = 0.0001 kWh = %.4f kWh)"
                      % (val, val / 10000.0))
                print("          比:   %d  %s  %d   ⇒ %s"
                      % (exp, "==" if val == exp else "!=", val, "相等" if val == exp else "不相等"))
            else:
                bad3b_vals.append(val)
                if val != exp:
                    n_ok3b += 1
                else:
                    n_bad3b += 1
                    if len(det3b) < 6:
                        det3b.append("%s 第 %d 项: 698 报 %d, 存侧计数 %d(校验码坏, 却原样放出去了)"
                                     % (CURKWH_ENY_NAMES[e], r + 1, val, raw))
                print("      [%d] %s" % (n, head))
                print("          存侧: 10 字节 %s | %s —— 校验码手算应是 %s, 对不上"
                      % (s[0:8].hex(" ").upper(), s[8:10].hex(" ").upper(),
                         kwh_crc2(s[0:8]).hex(" ").upper()))
                print("                ⇒ 这一格固件不认(回落成 0 或 EEPROM 里的值), 不拿它的计数 %d 当期望值"
                      % raw)
                print("          读侧: 698 第 %d 项 = %d" % (r + 1, val))
                print("          看有没有被拦下: 回读 %d  %s  该格计数换算后本应是 %d  ⇒ %s"
                      % (val, "!=" if val != exp else "==", exp,
                         "拦下了(没把它放出去)" if val != exp else "没拦住(原样放出去了)"))
    if n_ok3a + n_bad3a == 0:
        add("③a 698 读回值 == g_CurkWh 存值经该 OAD 去路换算(只比 CRC 配对的项)", None,
            "本次没有 CRC 配对的项可对拍(或 %d 个项落在换算登记表外) ⇒ 无分辨力" % n_nocv,
            crit="③a", falsify="回读路径取错项/错电类/定标不符 ⇒ 与换算后的存值不等")
    else:
        add("③a 698 读回值 == g_CurkWh 存值经该 OAD 去路换算(只比 CRC 配对的项)",
            n_bad3a == 0,
            "对拍 %d 项, 等 %d 不等 %d%s" % (
                n_ok3a + n_bad3a, n_ok3a, n_bad3a,
                "" if n_bad3a == 0 else "; 不等: " + "; ".join(det3a)),
            crit="③a", falsify="回读路径取错项/错电类/定标不符 ⇒ 与换算后的存值不等")
    _fb_same = len(set(bad3b_vals)) <= 1         # 回落口径给一个值 ⇒ 各校不过的项回读应彼此相同
    if not bad3b_vals:
        add("③b CRC 不配对的项被拦下(回读既不等于该项字节的换算值, 各校不过的项回读值又彼此相同)", None,
            "本次没有 CRC 不配对的项 ⇒ 这一条没有分辨力", crit="③b",
            falsify="校不过的项字节被原样放行 ⇒ 回读等于该项字节的换算值")
    else:
        add("③b CRC 不配对的项被拦下(回读既不等于该项字节的换算值, 各校不过的项回读值又彼此相同)",
            n_bad3b == 0 and _fb_same,
            "CRC 校不过的项 %d 个, 拦下 %d 个, 回读值集合 %s%s" % (
                len(bad3b_vals), n_ok3b, sorted(set(bad3b_vals)),
                "" if n_bad3b == 0 else "; 放行: " + "; ".join(det3b)),
            crit="③b", falsify="校不过的项字节被原样放行 ⇒ 回读等于该项字节的换算值")

    return _rec_close(recs, why)


def energy_mirror_fore(ser, trig=None, bp=None, wb_vars=None):
    """第八步 · ① 判定(**只有断点观测给得出**): 停 `kWhData.c:448`(`Update_Rate_Energy` 入口)一次,
    同时读**来值**(缓冲里那帧每类 8 字节)与**存值**(g_CurkWh 每 13 项取一的「总」), 逐电类比。

    AA80 读到的只是**存值**, 拿它当"来值"是假证据 —— 没会话时这条记 `ok=None`(没做成)。"""
    recs, why, add = _rec_bag()
    # ---------- ① 来值 → 存值(断点观测) ----------
    if bp is None:
        add("① 计量芯来值 → g_CurkWh 逐字节无损", None,
            "本次无断点会话 ⇒ 拿不到**来值**(AA80 读到的只是存值; 停核那次才有帧侧)",
            crit="①", obs=judge.DEBUG, falsify="换算/截断/错位 ⇒ 帧内字节与存储字节不等")
        return _rec_close(recs, why)

    exprs = tuple(wb_vars) if wb_vars else kwh_wb_vars()
    # 触发放后台线程(要等串口/等命中), 主线程等断点 —— 见 swdbg.breakpoint.with_trigger 的死锁说明。
    # 这一次**没有可发的触发帧**(断[B] 随计量帧到点自然命中) ⇒ `fn` 只是占位, 命中靠等。
    r = trig(bp, lambda: None, timeout=90.0, _vars=exprs)
    hit, vals = _wbline("断[B] kWhData.c:448 Update_Rate_Energy 入口", r)
    if hit is None or not vals:
        why.append("断[B] 未命中或读不到量 ⇒ 判①无证据%s" % (
            "(触发线程仍在跑)" if r.get("trigger_alive") else ""))
        add("① 计量芯来值 → g_CurkWh 逐字节无损", None, why[-1],
            crit="①", obs=judge.DEBUG, falsify="换算/截断/错位 ⇒ 帧内字节与存储字节不等")
        return _rec_close(recs, why)
    str1 = kwh_num(vals.get(WB_STR1))
    f0 = kwh_num(vals.get(WB_FRAME0))
    # 帧侧 8B 是 698 线上的**大端**; gdb 按本机小端把它读成 u64 ⇒ 这里换回来再比,
    # 于是"来值"与"存值"是**同一个整数**(不是同一个字节序)。反谐波 150 就是靠这一手对上的。
    frame = {}
    for k in range(CURKWH_NENY):
        frame[k] = kwh_bswap64(kwh_num(vals.get(exprs[2 + k])))
    stored = {}
    for k in range(CURKWH_NENY):
        stored[k] = kwh_num(vals.get(exprs[2 + CURKWH_NENY + k]))
    if str1 is None or f0 != WB_FRAME0_OK:
        why.append("断[B] 命中但见证字节不对(g_SPIMBuff[STR1_Index]=%s, 期望 0x%02X; STR1_Index=%s) "
                   "⇒ 缓冲里没有有效 698 帧, 拿到的『来值』不可信" % (f0, WB_FRAME0_OK, str1))
        add("① 计量芯来值 → g_CurkWh 逐字节无损", None, why[-1],
            crit="①", obs=judge.DEBUG, falsify="换算/截断/错位 ⇒ 帧内字节与存储字节不等")
        return _rec_close(recs, why)
    print("   断[B] 停在 %s (STR1_Index=%d, 帧首字节=0x%02X, 断点号=%s)" % (
        hit.where(), str1, f0, hit.bkptno))
    print("      这一停同时看到两侧: 计量芯刚送来的帧里那 8 字节(大端) 与 表里存的那 8 字节(小端)。")
    print("      下面是同 10 个电类挨个比 —— 字节序已各自换回同一整数, 比的是数, 不是字节排列。")
    for k in range(CURKWH_NENY):
        mark = "同" if frame[k] == stored[k] else "不同  <== 看这里"
        print("        %-5s  帧内(帧头后 %d 字节起, 第 %d 类) = %-22d  "
              "表里(g_CurkWh 第 %d 组) = %-22d  %s"
              % (CURKWH_ENY_NAMES[k], ENE_FRAME_OFF, k, frame[k], k, stored[k], mark))
    diff = [k for k in range(CURKWH_NENY) if frame[k] != stored[k]]
    # 分辨力: 非零的项才是真证据(0==0 任何偏移都成立, 不算) —— 本台面上常只有 1 个非零电类,
    # 如实写出来, 免得把"10 处全同"读成 10 份独立证据。
    nz = [CURKWH_ENY_NAMES[k] for k in range(CURKWH_NENY) if frame[k]]
    add("① 计量芯来值 → g_CurkWh 逐字节无损", not diff,
        "10 电类帧内(每类 9 字节: 帧头后 %d 字节起算, 再 +%d 跳过类型码; 大端→本机, "
        "STR1_Index=%d)与存侧 g_CurkWh 每 %d 项取一的『总』同值%s; 其中非零(有分辨力)的电类: %s" % (
            ENE_FRAME_OFF, ENE_FRAME_LEAD, str1, CURKWH_STRIDE,
            "全同" if not diff else "不同: " + str(
                [(CURKWH_ENY_NAMES[k], frame[k], stored[k]) for k in diff][:6]),
            ",".join(nz) if nz else "无(全 0 ⇒ 本条本次只有弱分辨力)"),
        crit="①", obs=judge.DEBUG, falsify="换算/截断/错位 ⇒ 帧内字节与存储字节不等")
    return _rec_close(recs, why)


def _kwh_diff(x, y):
    """两张 g_CurkWh 快照逐格比 → `(变了的格数, 变了的总列行名, 举例文字)`。

    `x`/`y` 是 `{(电类, 列): 10B}`; 缺格按 `None` 比。"""
    def cell(s):
        """一格打成"值字节 | 校验字节"的样子(给人读); 缺格写"(缺)"。"""
        return "%s | %s" % (s[0:8].hex(" ").upper(), s[8:10].hex(" ").upper()) if s else "(缺)"

    chg, tot_chg, ex = 0, [], []
    for e in range(CURKWH_NENY):
        for r in range(CURKWH_STRIDE):
            a, b = x.get((e, r)), y.get((e, r))
            if a == b:
                continue
            chg += 1
            if r == 0:
                tot_chg.append(CURKWH_ENY_NAMES[e])
            if len(ex) < 4:
                ex.append("%s 第 %d 列 %s → %s" % (CURKWH_ENY_NAMES[e], r, cell(a), cell(b)))
    return chg, tot_chg, ex


def energy_mirror_cleared_evidence(pre, imm, a_slots, cols, oads=("00000400", "00100400")):
    """第九步 · ⑥a/⑥b 判定: **645 电表清零后正常跑**(A 半) —— 分摊在干净态上成不成立。

    与 ④ 同一把尺子(同一份 `kwh_alloc_scan`), 换的只是快照: ④ 量"现在", 这里量"清完零、又跑过几轮之后"。
    两半(A/B)成对 —— **A 满足而 B(`energy_mirror_restart_evidence`)不满足 = 掉电这一个动作就是因**。

    `pre` / `imm` 是**旁证**(`crit=None`, 只进日志): 清前那一份、以及清完**当场**那一份。后者不作判据
    —— 它落在"清零刚写完"与"计量帧第一次覆盖"之间, 是个赛跑窗口, 拿它判会时红时绿。
    """
    recs, why, add = _rec_bag()
    _FALS = "分摊路径在干净态就不成立(费率项没被写进去 / 总项没被计量芯覆盖)"
    _NO = "AA80 直读整块 g_CurkWh 失败 ⇒ 无存储侧证据(不拿空数据凑结论)"
    if a_slots is None or pre is None or imm is None:
        add("⑥a 清零后正常跑: 费率项 CRC 全部配对", None, _NO, crit="⑥a", falsify=_FALS)
        add("⑥b 清零后正常跑: 费率分摊和 == 总", None, "⑥a 未证 ⇒ 等式也无分辨力", crit="⑥b", falsify=_FALS)
        return _rec_close(recs, why)

    n1, _t1, ex1 = _kwh_diff(pre, imm)
    n2, _t2, ex2 = _kwh_diff(imm, a_slots)
    _nz = sum(1 for e in range(CURKWH_NENY) for r in range(CURKWH_STRIDE)
              if kwh_slot_val(imm.get((e, r))) not in (None, 0))
    add("旁证 · 清零前后 g_CurkWh 逐格对照(不作判据)", None,
        "清前 → 清完当场: 130 格里 %d 格变了%s; 清完当场还有值的格 = %d 个; "
        "清完当场 → 跑过几轮: %d 格变了(总列变了的电类: %s)%s"
        % (n1, "" if not ex1 else "(如 " + "; ".join(ex1) + ")", _nz, n2,
           ",".join(_t2) if _t2 else "无", "" if not ex2 else "(如 " + "; ".join(ex2) + ")"),
        crit=None)

    _s = kwh_alloc_scan(a_slots, cols, oads)
    live_n = _s["live_n"]
    print("   ⑥ 明细 —— 清零后正常跑(A 半): 与 ④ 同一把尺子(费率项可不可信 / Σ费率 是否等于 总)")
    for _l in _s["lines"]:
        print("        " + _l)
    if live_n is None or live_n < 0:
        add("⑥a 清零后正常跑: 费率项 CRC 全部配对", None,
            KWH_ALLOC_RANGE_WHY + " ⇒ **不拿 12 顶上去**", crit="⑥a", falsify=_FALS)
        add("⑥b 清零后正常跑: 费率分摊和 == 总", None, "⑥a 未证 ⇒ 等式也无分辨力",
            crit="⑥b", falsify=_FALS)
    elif _s["nchk"] == 0:
        add("⑥a 清零后正常跑: 费率项 CRC 全部配对", None,
            "清零后 10 个总项又全为 0 ⇒ 本台面这条无分辨力(0==0 不算证据)", crit="⑥a", falsify=_FALS)
        add("⑥b 清零后正常跑: 费率分摊和 == 总", None, "本台面 ⑥a 无分辨力 ⇒ 等式也无分辨力",
            crit="⑥b", falsify=_FALS)
    else:
        add("⑥a 清零后正常跑: 费率项 CRC 全部配对", _s["ok_crc"],
            "总项与 rate 1..%d(= 运行期费率数)的 2B CRC %s" % (
                live_n, "全部配对" if _s["ok_crc"] else "有配对不上的 —— 明细: " + " | ".join(_s["lines"])),
            crit="⑥a", falsify=_FALS)
        if _s["n_prem"] == 0:
            add("⑥b 清零后正常跑: 费率分摊和 == 总", None,
                "⑥a 不满足 ⇒ rate 1..%d 里没有可信值, 求和式两侧不可比" % live_n, crit="⑥b", falsify=_FALS)
        else:
            add("⑥b 清零后正常跑: 费率分摊和 == 总", _s["ok_sum"],
                "前提成立的电类 %d 个: %s" % (_s["n_prem"], " | ".join(_s["lines"])),
                crit="⑥b", falsify=_FALS)

    return _rec_close(recs, why)


def energy_mirror_restart_evidence(a_slots, b_slots, cols, oads=("00000400", "00100400")):
    """第十步 · ⑦a/⑦b 判定: **同一跑次断电 ≥5s 再上电后**(B 半) —— 同一把尺子, 换 B 态快照。

    额外把 A/B 两态**逐格对照**打进证据(旁证): 变了的那些格就是"上电时既没被复位、也没从 EEPROM
    恢复"的格。`b_slots is None` = 没等到表回来(或没读成) ⇒ 两条记未证, 不冒充"重启过"。
    """
    recs, why, add = _rec_bag()
    _FALS = "上电时费率项既不被复位也不从 EEPROM 恢复 ⇒ 拿到 __no_init 的随机值, 分摊再把它 += Δ 后盖章"
    if b_slots is None or cols is None:
        _w = "断电后 B 态没有存储侧证据(没等到『表消失又回来』, 或那一读失败)"
        add("⑦a 断电重启后: 费率项 CRC 全部配对", None, _w, crit="⑦a", falsify=_FALS)
        add("⑦b 断电重启后: 费率分摊和 == 总", None, "⑦a 未证 ⇒ 等式也无分辨力", crit="⑦b", falsify=_FALS)
        return _rec_close(recs, why)

    if a_slots is None:
        add("旁证 · 断电前后 g_CurkWh 逐格对照(不作判据)", None,
            "A 半没读成 ⇒ 无对照基线", crit=None)
    else:
        n, t, ex = _kwh_diff(a_slots, b_slots)
        add("旁证 · 断电前后 g_CurkWh 逐格对照(不作判据)", None,
            "A → B: 130 格里 %d 格变了(其中总列变了的电类: %s)%s"
            % (n, ",".join(t) if t else "无", "(如 " + "; ".join(ex) + ")" if ex else ""),
            crit=None)

    _s = kwh_alloc_scan(b_slots, cols, oads)
    live_n = _s["live_n"]
    print("   ⑦ 明细 —— 断电重启后(B 半): 与 ④/⑥ 同一把尺子")
    for _l in _s["lines"]:
        print("        " + _l)
    if live_n is None or live_n < 0:
        add("⑦a 断电重启后: 费率项 CRC 全部配对", None,
            KWH_ALLOC_RANGE_WHY + " ⇒ **不拿 12 顶上去**", crit="⑦a", falsify=_FALS)
        add("⑦b 断电重启后: 费率分摊和 == 总", None, "⑦a 未证 ⇒ 等式也无分辨力",
            crit="⑦b", falsify=_FALS)
    elif _s["nchk"] == 0:
        add("⑦a 断电重启后: 费率项 CRC 全部配对", None,
            "重启后 10 个总项全为 0 ⇒ 本台面这条无分辨力(0==0 不算证据)", crit="⑦a", falsify=_FALS)
        add("⑦b 断电重启后: 费率分摊和 == 总", None, "本台面 ⑦a 无分辨力 ⇒ 等式也无分辨力",
            crit="⑦b", falsify=_FALS)
    else:
        add("⑦a 断电重启后: 费率项 CRC 全部配对", _s["ok_crc"],
            "总项与 rate 1..%d(= 运行期费率数)的 2B CRC %s" % (
                live_n, "全部配对" if _s["ok_crc"] else "有配对不上的 —— 明细: " + " | ".join(_s["lines"])),
            crit="⑦a", falsify=_FALS)
        if _s["n_prem"] == 0:
            add("⑦b 断电重启后: 费率分摊和 == 总", None,
                "⑦a 不满足 ⇒ rate 1..%d 里没有可信值, 求和式两侧不可比" % live_n, crit="⑦b", falsify=_FALS)
        else:
            add("⑦b 断电重启后: 费率分摊和 == 总", _s["ok_sum"],
                "前提成立的电类 %d 个: %s" % (_s["n_prem"], " | ".join(_s["lines"])),
                crit="⑦b", falsify=_FALS)

    return _rec_close(recs, why)



# ============================================================================
# 16-2 软件要求 · 软件比对
# ============================================================================
# 规格(源 project/knowledge/_whitebox_ledger/ledger.md 16-2):
#   开发状态 = 已实现(读版本/程序集成标识 + 升级内真比对); 电子签名未实现
#   可达性   = 可测(读+比对); 数字签名核对 = 未实现, 不测
#   断点     = 断[A] DLT698App.c:8916 / 断[B] DLT645App.c:6188 / 断[C] DLT698App.c:17586
#   判过: 读值 = 本机 K_SoftVersion 且三段校验和逐字可算; 升级校验拒掉 CRC/版本不符的包
#   判不过/待核: 签名核对不做(代码未实现, 与开发表一致)
#
# ---- 读侧有三条路; 各自的"放进去的字节是正序还是反序"是**读源码读出来的**, 不是猜的 ----
#   · 698 内部软件版本 OAD `0xFF3005`: `Get_NormalData`(`Application\DLT698App.c:6882`)里
#     `switch (pOAD[0]<<16 | pOAD[1]<<8 | pOAD[2])`(:6930)命中 `case 0xFF3005`(:8915-8917),
#     执行 `RevCopy_Data(&buff[0], TAB_SoftVer, sizeof(TAB_SoftVer))`(:8916)。
#     `RevCopy_Data`(`Platform\Common.c:306-313`)是**反序拷贝** ⇒ 那一刻 `buff` 里是发布标识的**反序**。
#     ⚠ 但**线上那 32B 是正序** —— 后半程还有一次翻转: 四个 OAD 的共享尾部 `:8924` 调
#       `Spread_StructArray` → 八位位组串走 `Spread_OctString`(`:15310`)的 `GET698` 支,
#       它在 `:15435` 又做一次 `RevCopy_Data(pStru->OutBuf, pStru->InBuf, len)`
#       (`Spread_NormalData` `:15977` 对数值型同样反向 —— 这是 698 的编码方向)。
#       两次相抵 ⇒ **线上 = 正序, `buff` = 反序**。整条链是「源码读出来 + 实测对过」的:
#       实跑里 `buff`(`断[A]` @`:8921`)与线上逐字节互为反转, 两种载荷(32 字符版本串 / 8 字符数字串)都对上。
#       ⚠ 判据**必须把这两半分开写**: 串口那半比正序, 断点那半比反序 —— 只照 `RevCopy_Data` 一处推,
#         会把「固件对、判据错」误报成固件 FAIL, 并把 ②c 那条独立的分区自洽关系一起带歪(见 ②a/②c)。
#   · 645 读版本 DI `04CC0000`(线上序 `00 00 CC 04`): `CMD_ReadData`(:913)在 `LEN == 0x04` 时按
#     **末个 DI 字节**分派, `DI3 == 0x04` → `CMD_ReadData04`(:5319); 其内
#     `switch (DI0 | DI1<<8 | DI2<<16)` = `0xCC0000` 命中 :6187-6191:
#       `Copy_Data(&pFrame[DAT0], &TAB_SoftVer[0], sizeof(TAB_SoftVer));`
#       `Copy_Data(&pFrame[DAT0+sizeof(TAB_SoftVer)], (INT8U*)&TAB_MeterSty, sizeof(TAB_MeterSty));`
#     `Copy_Data` 是**顺序拷贝** ⇒ 数据域**前 32B = 发布标识的正序**, 其后紧跟表型结构。
#   · 645 厂内标定读版本 DI `E1000000`(线上序 `00 00 00 E1`): 同一处分派, `DI3 == 0xE1` →
#     `ST_FactoryReadCMD`(:826); 其选择子 `DI0|DI1<<8|DI2<<16|DI3<<24` = `Calibrate_SoftVersion`
#     (`Platform\Metering.h:323` `#define Calibrate_SoftVersion 0xE1000000`)→ :840-844:
#     `memcpy(&pu8Frame[DAT0], TAB_SoftVer, sizeof(TAB_SoftVer));`
#     `Reverse_Data(&pu8Frame[DAT0], sizeof(TAB_SoftVer));`  ⇒ 数据域 = 发布标识的**反序**。
#   ⚠ **这两条 645 路是两段不同的代码、字节序相反** —— 规格栏把 `0xCC0000` 与 `:840` 写在同一行
#     (程序解释那句「645 读版本→…case 0xCC0000(厂内标定 Calibrate_SoftVersion …:840)」),
#     照它读会以为只有一条路。两条都测 ⇒ "两处拷贝方向相反"这件事本身也落了证据。
#
# ---- 三段程序集成标识: 口径(照源码写在这儿, 不照标准猜) ----
#   `Application\DLT698App.c:8880-8913` 一个 switch 管四个 OAD:
#     `0xFF3002` 整片 FLASH / `0xFF3003` 出厂区 [`FLASH_BASE`, `FLASH_APP_BASE`) /
#     `0xFF3004` 应用区 [`FLASH_APP_BASE`, `FM_FLASH_SIZE`) (三者都 `goto Caculatate_Firmware_ID`) /
#     `0xFF3005` 版本字符串(见上)。三个区走同一段:
#       `u32 = 0; while (u32Size--) { u32 += *(INT32U*)(u32Adr); u32Adr += 4; }`
#     = **逐 32 位小端字相加, 只留低 32 位**(溢出截尾, 不另处理)。
#   `:8901 LHEX_nBCD(buff+9, u32, 4)`(`Platform\Common.c:100-114`): 每轮 `j = dword%100`,
#     再拆成高低 nibble 存进 `pDest[i]` —— **低位那一对先写**; `:8902-8913` 那八行再把每个字节
#     按 nibble 互换取出一位数字 ⇒ `buff[0..7]` = `("%08d" % (u32 % 10**8))` 的**倒序**
#     (最低位在最前)。再经共享尾部那次 `Spread_OctString` 翻转(见上一条 ⚠)⇒
#     **线上那 8 个字符 = `"%08d" % (u32 % 10**8)`, 最高位在最前** —— 两个形态各归一半判据,
#     函数就是 `sv_digits`(线上)与 `sv_digits_buff`(buff), 后者是前者的倒序。
#   ⚠ **本库只实现"给定字节按这个口径重算"**(`sv_digits` + `common.elfsym.wordsum`);
#     口径本身住这段注释里 —— 换一版固件改了累加方式, 那两个纯函数**不会自己知道**。
#
# ---- 应用区那一段为什么"与填充假设无关"(这条决定判据②a 敢不敢报) ----
#   `common.elfsym.image(0x4000, 0x80000)` 的覆盖计数 == 区间长(实测 507904/507904, APP 的
#   PT_LOAD 段首尾相接铺满)⇒ 一个填充字节都用不到 ⇒ 重算出来的值与本台"未编程区读回 0x00 还是
#   0xFF"无关。整片(`0xFF3002`)与出厂区(`0xFF3003`)则**含 boot 区** [0, 0x4000), 而 boot 是另一个
#   IAR 工程(`EZ315-FM33A0610EV-Boot`), 它的 `.out` **不在本卡带的固件声明里**
#   (`project/ez315_fm33a0610.meta.json` 的 `firmware` 块只声明并指纹 APP 一个镜像)
#   ⇒ 那两段算不出可信的离线对照值, 见判据②b 的 unprovable 声明。
#
# ---- 判据条目 ----
#   ①a/①b/①c = 三条读路各自"读回的就是本机发布标识"(序各自不同);
#   ②a = 应用区累加和逐字可算; ②c = 三段分区自洽;
#   ②b/③/④ = 本台**证不了**的三条(逐条写清为什么、要证还得做什么, 见 softver_criteria)。
# ============================================================================
SV_ID = P.SV_ID
SV_ID_B = P.SV_ID_B
SV_ID_REV = P.SV_ID_REV
SV_VER_BYTES = P.SV_VER_BYTES
SV_OAD_VER = P.SV_OAD_VER
SV_OAD_TOTAL = P.SV_OAD_TOTAL
SV_OAD_FACT = P.SV_OAD_FACT
SV_OAD_APP = P.SV_OAD_APP
SV_OAD_IDS = P.SV_OAD_IDS
SV_DI_VER = P.SV_DI_VER
SV_DI_FAC = P.SV_DI_FAC
SV_IDS = P.SV_IDS
SV_MOD = P.SV_MOD
SV_APP_LO = P.SV_APP_LO              # FLASH_APP_BASE / FM_FLASH_SIZE
SV_APP_HI = P.SV_APP_HI
SV_FORMS = P.SV_FORMS



def sv_digits(v):
    """整片累加和(32 位无符号) → **线上**那 8 个字符(最高位在最前)。

    口径 = `DLT698App.c:8901`(`LHEX_nBCD(buff+9, u32, 4)`) + :8902-8913(逐个 nibble 拆字符)
    + `Platform\\Common.c:100-114`(每轮先写低位那一对)得 `buff` 里的倒序形态, 再由共享尾部
    `Spread_OctString`(`DLT698App.c:15435`)翻正 —— 两次相抵 ⇒ 线上就是 `"%08d" % (u32 % 10**8)`。
    逐条依据见本节头注。⚠ `buff` 里的是它的倒序, 用 `sv_digits_buff`。
    """
    return "%0*d" % (SV_IDS, int(v) % SV_MOD)


def sv_digits_buff(v):
    """整片累加和 → **`buff` 里**那 8 个字符(最低位在最前) = 线上形态的倒序。

    给断点观测用: 停在 `断[A]`(`:8921`)那一刻, 共享尾部的 `Spread_OctString` **还没跑**,
    所以 `buff` 里是这一形态。两个形态各占一半判据, 别混。
    """
    return sv_digits(v)[::-1]


def sv_digits_val(text):
    """**线上**那 8 个字符 → 整数值; 形态不对(长度不符 / 有非数字字符) → None(不猜)。"""
    t = str(text if text is not None else "")
    if len(t) != SV_IDS or any(c not in "0123456789" for c in t):
        return None
    return int(t)


def sv_ascii(data, n):
    """APDU 数据域 → `(正文 n 个字符, 用的是哪种封装)`; 两种都对不上 → `(None, None)`。

    为什么留两种封装: 「数据域 = 1 个长度字节 + n 字符」还是「= 纯 n 字符」, 本站是**第一次观测它**。
    在判据里先假定一种、再拿"对不上"去说固件不对, 那是拿**我的假定**当判据 —— 假定错的时候,
    现象与"固件根本没把版本放进去"长得一模一样(都是对不上)。故两种都认, 并把**实际命中的那一种**
    写进证据; 只有这样, "对不上"才真指向固件。

    ⚠ 两种封装取的是**不同偏移**, 且长度都卡死 ⇒ 判据的证伪力一点没丢: 一个把版本**正序**放进去的
      固件, 在两种封装下都对不上反序串。

    ⚠ **2026-09-17 实跑补第三种**: 本台 698 读回 `FF300500` 的数据域是 `0A 20 <32 字符>`、
    `FF3002/03/04` 是 `0A 08 <8 字符>` —— 前面**多一个 `0A` 类型字节**(698 的八位位组串标),
    两种假定都没认。原先"两种封装"是纯离线推的, 而**第三种只有真跑才看得见**。
    这不是把判据放宽: 长度仍卡死 n, `0A` 与长度两个字节都要对上才认 ⇒ 证伪力不变;
    多认一种只是让"对不上"重新指向固件, 而不是指向**我这边的假定**。
    """
    d = bytes(data if data is not None else b"")
    if len(d) == n + 2 and d[0] == 0x0A and d[1] == n:
        return d[2:2 + n].decode("ascii", "replace"), SV_FORMS[2]
    if len(d) == n + 1 and d[0] == n:
        return d[1:1 + n].decode("ascii", "replace"), SV_FORMS[0]
    if len(d) == n:
        return d.decode("ascii", "replace"), SV_FORMS[1]
    return None, None


def sv_text(ud, n=SV_VER_BYTES):
    """698 读应答 ud → `(正文, 封装)`(数据域取法见 `oad_ud_data`)。"""
    return sv_ascii(oad_ud_data(ud), n)


def sv_digits_of_head(data, n=SV_IDS):
    """一段**更长的**数据的开头 → `(n 个字符, 封装)`; 两种封装都试, 都不成 → `(None, None)`。

    给断点观测用: 停在写入口那一刻读回来的 `buff` 是整个数组, 那 n 个字符只占开头, 到底占 n 还是
    n+1 个字节同样按 `sv_ascii` 的两种封装认。
    """
    d = bytes(data if data is not None else b"")
    for k in (n + 1, n):
        t, f = sv_ascii(d[:k], n)
        if t is not None:
            return t, f
    return None, None


def sv_expected_appsum(out_path=None):
    """离线把**应用区** `[0x4000, 0x80000)` 从 `.out` 铺出来、按固件口径逐字累加。

    → `(值, 被段盖住的字节数, 区间长)`; `.out` 读不到 / 区间非法 → `None`(调用方据此记「没做成」,
    **不在这儿抛** —— 抛出去会把整段打断, 而这只该让 ②a 那一条降级)。

    覆盖计数 == 区间长 时**一个填充字节都用不到** ⇒ 这个值与本台"未编程区读回什么"无关。
    """
    try:
        img, cov = elfsym.image(SV_APP_LO, SV_APP_HI, out=out_path)
    except Exception as exc:
        print("   !! 离线重算应用区累加和没做成: %s" % exc)
        return None
    return elfsym.wordsum(img), cov, SV_APP_HI - SV_APP_LO


# ---- 三条读路的动作(既作黑盒读数, 也作 `fire_hit` 的触发动作; 各自打印它自己的观察行) ----
def sv_read_698_ver(ser, wait=3.0):
    """帧: 698 读 `0xFF3005`(内部软件版本) → 正文 32 字符或 None。"""
    ud = read_oad_ud(ser, SV_OAD_VER, wait=wait)
    txt, form = sv_text(ud)
    print("   [串口观测] 698 读 %s(内部软件版本): %s"
          % (SV_OAD_VER, ("「%s」(封装=%s)" % (txt, form)) if txt is not None
             else "没读回/形态对不上两种封装(数据域 %dB)" % len(oad_ud_data(ud))))
    return txt


def sv_read_698_ids(ser, oad, wait=3.0):
    """帧: 698 读某一个程序集成标识 OAD(8 位数字串) → 那 8 个字符或 None。"""
    nm = dict(SV_OAD_IDS).get(oad, oad)
    ud = read_oad_ud(ser, oad, wait=wait)
    txt, form = sv_text(ud, SV_IDS)
    print("   [串口观测] 698 读 %s(程序集成标识·%s): %s"
          % (oad, nm, ("「%s」(封装=%s)" % (txt, form)) if txt is not None
             else "没读回/形态对不上两种封装(数据域 %dB)" % len(oad_ud_data(ud))))
    return txt


def sv_read_645_ver(ser, wait=3.0):
    """帧: 645 读版本 DI `04CC0000`(顺序拷贝那一支) → 数据域; 前 32B 应是**正序**发布标识。"""
    val = read_param_di(ser, SV_DI_VER, wait=wait,
                        head="读版本 DI %s" % SV_DI_VER)
    print("   [串口观测] 645 读 DI %s: %s"
          % (SV_DI_VER, ("数据域 %dB, 前 %dB=「%s」"
                         % (len(val), SV_VER_BYTES,
                            bytes(val[:SV_VER_BYTES]).decode("ascii", "replace"))) if val
             else "没读回"))
    return val


def sv_read_645_fac(ser, wait=3.0):
    """帧: 645 厂内标定读版本 DI `E1000000`(拷贝后**反序**那一支) → 数据域; 应是反序发布标识。"""
    val = read_param_di(ser, SV_DI_FAC, wait=wait,
                        head="厂内标定读版本 DI %s" % SV_DI_FAC)
    print("   [串口观测] 645 厂内标定读 DI %s: %s"
          % (SV_DI_FAC, ("数据域 %dB=「%s」"
                         % (len(val), bytes(val[:SV_VER_BYTES]).decode("ascii", "replace"))) if val
             else "没读回"))
    return val


def softver_criteria():
    """16-2 的**预设条目**(源 = ledger.md 16-2 的「观察与判据」I 列 + 操作步骤 H 列)。测试**前**定死。

    拆分说明(每条都答得出 falsify, 见 `softver_roundtrip` 内各 `add` 的 falsify):
      · ① 拆成 ①a/①b/①c —— 三条读路是**三段互不相干的代码**, 且两条 645 的字节序相反。
        合成一条的话, 一个"698 那条反序对了、两条 645 路整段不通"的固件照样记「满足」。
      · ② 拆成 ②a/②b/②c —— ②a(应用区)与 ②c(三段分区自洽)本台证得了, ②b(整片/出厂区)证不了。
        合成一条会让「证不了」吞掉「证得了」。
      · ③(升级校验拒包)/④(签名核对)= **声明不可证**, 理由逐条写在下面 —— 声明出来,
        「忘了测」与「知道证不了」才分得开(`common/judge.py` 的立身之本之一)。
    """
    return {
        "①a": "698 读 0xFF3005(内部软件版本)读回的正文 = 本机发布标识的**正序**"
               "(`RevCopy_Data` 先把 32B 倒着拷进 buff —— DLT698App.c:8916, `pDest[i] = pSour[len-1-i]`, "
               "Platform/Common.c:306-313; 共享尾部的 `Spread_OctString` 出线上时又倒一次 —— "
               "DLT698App.c:15435, 两次相抵); 断点侧停在写入口之后时 buff 里仍是那 32B 的**反序**",
        "①b": "645 读版本 DI 04CC0000 的数据域**前 32B** = 本机发布标识的**正序**"
               "(`Copy_Data`, DLT645App.c:6187-6191), 其后紧跟 sizeof(TAB_MeterSty) 字节表型结构",
        "①c": "645 厂内标定读版本 DI E1000000 的数据域 = 发布标识的**反序**"
               "(`memcpy` + `Reverse_Data`, DLT645App.c:840-844; 选择子 0xE1000000, Metering.h:323)",
        "②a": "应用区程序集成标识 0xFF3004 的读回值 == **我们自己**按固件口径(逐 32 位小端字相加、"
               "截 32 位、取低 8 位十进制)从 `.out` 的 [0x4000, 0x80000) 重算出的那 8 个字符 —— "
               "该区间被 PT_LOAD 段 100% 盖住 ⇒ 与『未编程区读回什么』无关"
               "(口径逐个有出处: 累加 DLT698App.c:8895-8900 `u32 += *(INT32U*)` 天然 LE 且自然回绕; "
               "`LHEX_nBCD` Platform/Common.c:100-114 每轮先写**最低那一对**十进制位、低半字节是个位; "
               "拆字符 DLT698App.c:8902-8913 偶 i 取低半字节 ⇒ buff 里**最低位排在最前**, "
               "而出线上时 `Spread_OctString` DLT698App.c:15435 把它翻正 ⇒ **线上最高位排在最前**); "
               "断点侧停在写入口之后读 buff 时, 比的是它的**倒序**",

        "②b": {"text": "整片(0xFF3002)/出厂区(0xFF3003)的读回值 == 离线重算值",
               "unprovable": "这两段含 boot 区 [0, 0x4000), 而 boot 是另一个 IAR 工程"
                             "(EZ315-FM33A0610EV-Boot), 它的 .out 不在本卡带的固件声明里"
                             "(project/ez315_fm33a0610.meta.json 的 firmware 块只声明并指纹 APP 一个镜像)"
                             "⇒ 那两段没有可信的离线对照值。要证须二选一: ①把 boot 镜像也写进卡带声明并指纹"
                             "(改 project/, 且要先有那份 .out 的权威出处); ②用 AA80 直读把 [0, 0x4000) "
                             "读回来自己铺(boot 区 16KB ≈ 128 帧) —— 那证的是『表内两份读数自洽』, "
                             "不是『与镜像一致』, 两条不等价"},
        "②c": "三段分区自洽: 0xFF3002 == (0xFF3003 + 0xFF3004) mod 10^8(只用读回值, 不依赖离线镜像) —— "
               "⚠ 不成立时**不判失败**: 某一支的原始和若发生 2^32 回绕, 取低 8 位后这个关系照样不成立, "
               "而本台分辨不了回绕 ⇒ 记「未证」",
        "③": {"text": "升级校验拒掉 CRC 不符 / 版本不符的包(Check_Upgrade_Firmware 返回"
                      " Integrity_Check_Error / Software_Version_Mismatch)",
              "unprovable": "唯一的触发点是 TaskComm.c:1436 —— 整包 648 下载**跑完之后**。本库有意不做 "
                            "648 下载(没有组包能力), 而『篡改升级包』更在库的能力之外 ⇒ 帧通道到不了那个"
                            "函数。要证须真走一次整包下载, 并分别造 CRC 不符/版本不符两种包 —— "
                            "那是台面与厂商工具的事, 不是本仓能造的"},
        "④": {"text": "电子签名核对(不符则拒)",
              "unprovable": "源码那一支是空的(`DLT698App.c:17603` 只有注释、无代码), 与 ledger.md 16-2 的"
                            "『可达性』栏原文一致(『数字签名核对=未实现, 不测』)⇒ 没有可证的对象。"
                            "**这不是『本台证不了』, 是固件里没有这一段** —— 哪天厂商补上实现, "
                            "本条要改回可证并重建"},
    }


def softver_roundtrip(ser, wb=None, wb_waived=False, wait=3.0, out_path=None,
                      bp_698=None, vars_698=(), bp_645=None, vars_645=(),
                      bp_fac=None, vars_fac=()):
    """**16-2 软件比对**的判过积木: 三条读路 + 三段集成标识 + 离线重算的对照。

    两种观测都在本函数里配好(与其余子项同构):
      · 串口观测 —— 读数本身(『读值 = 本机发布标识』只有这条路量得出来);
      · 断点观测 —— 停在固件**把字节放进应答之前**那一刻读 `buff` / `pFrame` / `pu8Frame`,
        证明"这一支真被执行"且"放进去的就是那个值"(指令路径 + 内容, 见 CLAUDE.md ⑥ 的 6.2/6.3)。

    `wb` = `{"fire": partial(GD.fire_hit, g)}`; 没会话时传 None —— 白盒那几条记「没做成」,
    串口那几条**照跑**(降级只降白盒, 见 CLAUDE.md 的"两种观测")。

    ⚠ **为什么黑盒重新发一次、而不是从 `fire_hit` 的返回里取**: `fire_hit` 交回来的记录里
      **没有触发动作的返回值** —— `record()` 按"命中没有"定 `ok` 就把 `result` 丢了(见它自己的 ⚠),
      而本子项的判据恰恰是**值**。让动作把值塞进一个共享 dict 也能办到, 但那等于把库的返回结构
      当管道用; 读一次便宜, 且黑盒那一次本来也不依赖断点。两次都是只读 GET, 不改表状态。

    ⚠ **断点侧读的是哪一半, 逐断点写清**(过"这一条证据声称了什么"这一关):
      · 断[A](`:8921`)—— `buff` 是 `INT8U[]`, 读回**内容** ⇒ ①a/②a 的白盒那一半比的是字节。
      · 断[B](`:6190`)/断[C](`:843`)—— `pFrame`/`pu8Frame` 在 `$r4` 里是**指针**, 本库的读名口
        只收**普通变量名**(见 `scripts/_check_anchors.py`), 读不了 `pFrame[0]` 那种表达式
        ⇒ 这两条白盒只给**指令路径**(停在这一支的写入口之后), **不声称读到内容**;
        内容那一半由同一条判据的串口观测(和 ①a 的白盒)给。这与 5-5 `pOper` 的先例同规矩。

    返回 `(recs, why, scope)`。
    """
    recs, why = [], []
    fire = (wb or {}).get("fire")

    def add(name, ok, detail, crit=None, obs=judge.SERIAL, falsify=None):
        recs.append(rec(name, ok, detail, crit=crit, obs=obs, falsify=falsify))

    def bp_txt(bp):
        return _bptxt(bp) if bp else "(未给断点)"

    def hit_vars(r):
        """`fire_hit` 的记录 → `(命中没有, 停住时读到的量)`。记录为 None = 没会话/断点没下上。"""
        return (r or {}).get("ok") is True, dict((r or {}).get("vars") or {})

    print("\n== 16-2 软件要求 · 软件比对 ==")
    print("   本机发布标识(K_SoftVersion, %dB): %s" % (SV_VER_BYTES, SV_ID))
    print("   期望形态: 正序「%s」/ 反序「%s」" % (SV_ID, SV_ID_REV.decode("ascii")))

    # ---- 进厂内: **读**这条路的前置(5-3 实踩过"读记录也受安全判定管"; 645 厂内标定那条更是明文) ----
    enter_factory(ser)
    if wb_waived:
        why.append("用户指定只做串口观测 ⇒ 白盒那几条记『没做成』(不是固件不对)")

    # ---- 离线那一半: 应用区累加和按固件口径重算一遍(给 ②a 当对照值) ----
    exp = sv_expected_appsum(out_path)
    exp_val = exp_digits = None
    if exp is None:
        why.append("离线按 .out 重算应用区累加和没做成 ⇒ 判据②a 没有对照值")
    else:
        exp_val, cov, span = exp
        exp_digits = sv_digits(exp_val)
        print("   [离线] 应用区 [%#x, %#x) 逐 32 位小端字相加 = %#010x → 固件口径应报「%s」"
              " (覆盖 %d/%d 字节%s)"
              % (SV_APP_LO, SV_APP_HI, exp_val, exp_digits, cov, span,
                 ", 一个填充字节都用不到 ⇒ 与未编程区读回什么无关" if cov == span
                 else " !! 没铺满 —— 填充假设会改这个值"))

    # ================= ①a 698 读 0xFF3005(线上正序 / buff 反序那一支) =================
    txt = sv_read_698_ver(ser, wait=wait)
    add("①a 698 读 %s = 发布标识(线上正序)" % SV_OAD_VER,
        None if txt is None else (txt == SV_ID),
        ("读回「%s」⇔ 期望正序「%s」" % (txt, SV_ID)) if txt is not None
        else "%s 没读回, 或数据域长度对不上两种封装 ⇒ 这一路本次无证据" % SV_OAD_VER,
        crit="①a", obs=judge.SERIAL,
        falsify="那一支漏掉 `RevCopy_Data` 或 `Spread_OctString` 少翻一次(线上成反序)/读的是别的对象/"
                "长度不是 32 ⇒ 读回不是正序发布标识")

    if fire is None:
        add("①a 断[A] %s 停住时 buff 已是反序串" % bp_txt(bp_698), None,
            "未做: 本次无调试会话(降级只降白盒, 串口那一条照跑)",
            crit="①a", obs=judge.DEBUG,
            falsify="buff 里是正序串/别的 32 字节 ⇒ RevCopy_Data 没反序, 或读的不是 TAB_SoftVer")
    else:
        r = fire(bp_698, lambda: sv_read_698_ver(ser, wait=wait),
                 label="断[A] 698 读 %s 停 %s 读 buff" % (SV_OAD_VER, bp_txt(bp_698)),
                 vars=tuple(vars_698), timeout=wait + 20.0, crit="①a",
                 falsify="固件读版本不走 :8921 那一支(共享尾部)⇒ 断点不停")
        if r is not None:
            recs.append(r)
        hit, vv = hit_vars(r)
        b = bytes(gdb_bytes(vv.get("buff")))
        add("①a 断[A] %s 停住时 buff 已是反序串" % bp_txt(bp_698),
            (b[:SV_VER_BYTES] == SV_ID_REV) if hit else None,
            ("停住读到 buff[0:%d]=%s (`sch`=%s —— 那是停在哪一号对象的痕迹, 见脚本 VARS_698 注)"
             % (SV_VER_BYTES, b[:SV_VER_BYTES].hex(" ").upper(), vv.get("sch", "(读不到)"))) if hit
            else "断[A] 没命中/没下上 ⇒ 白盒这一半本次没做成(≠固件不对)",
            crit="①a", obs=judge.DEBUG,
            falsify="buff 里是正序串/别的 32 字节 ⇒ RevCopy_Data 没反序, 或读的不是 TAB_SoftVer")

    # ================= ①b 645 读版本(0xCC0000, 正序那一支) =================
    val = sv_read_645_ver(ser, wait=wait)
    got_b = bytes(val[:SV_VER_BYTES]) if val else None
    add("①b 645 读 %s 前 %dB = 发布标识(正序)" % (SV_DI_VER, SV_VER_BYTES),
        None if got_b is None else (got_b == SV_ID_B),
        ("数据域 %dB: 前 %dB=「%s」⇔ 期望正序「%s」; 其后 %dB = 表型结构"
         % (len(val), SV_VER_BYTES, got_b.decode("ascii", "replace"), SV_ID,
            len(val) - SV_VER_BYTES)) if got_b is not None
        else "645 读版本没应答/数据域不足 %dB ⇒ 这一路本次无证据" % SV_VER_BYTES,
        crit="①b", obs=judge.SERIAL,
        falsify="那一支若改成 RevCopy_Data(反序)/读的是别的 DI ⇒ 前 32B 不是正序发布标识")

    if fire is None:
        add("①b 断[B] %s 645 读版本那一支真被执行" % bp_txt(bp_645), None,
            "未做: 本次无调试会话", crit="①b", obs=judge.DEBUG,
            falsify="读版本走别的分支或提前返回 ⇒ 不停在那一行")
    else:
        r = fire(bp_645, lambda: sv_read_645_ver(ser, wait=wait),
                 label="断[B] 645 读 %s 停 %s" % (SV_DI_VER, bp_txt(bp_645)),
                 vars=tuple(vars_645), timeout=wait + 20.0, crit="①b",
                 falsify="645 读版本不经过 :6190(两处 Copy_Data 之后)⇒ 断点不停")
        if r is not None:
            recs.append(r)
        hit, vv = hit_vars(r)
        add("①b 断[B] %s 645 读版本那一支真被执行(白盒只给指令路径, 见 roundtrip 头注)" % bp_txt(bp_645),
            True if hit else None,
            ("停住读到 pFrame=%s" % vv.get("pFrame", "(读不到)")) if hit
            else "断[B] 没命中/没下上 ⇒ 本次没做成",
            crit="①b", obs=judge.DEBUG,
            falsify="读版本走别的分支(如 CMD_ReadData02)/提前返回 ⇒ 不停在 :6190")

    # ================= ①c 645 厂内标定读版本(0xE1000000, 反序那一支) =================
    fac = sv_read_645_fac(ser, wait=wait)
    got_c = bytes(fac[:SV_VER_BYTES]) if fac else None
    add("①c 645 厂内标定读 %s = 发布标识(反序)" % SV_DI_FAC,
        None if got_c is None else (got_c == SV_ID_REV),
        ("数据域 %dB=「%s」⇔ 期望反序「%s」"
         % (len(fac), got_c.decode("ascii", "replace"), SV_ID_REV.decode("ascii"))) if got_c is not None
        else "645 厂内标定读版本没应答/数据域不足 %dB ⇒ 这一路本次无证据" % SV_VER_BYTES,
        crit="①c", obs=judge.SERIAL,
        falsify="那一支若漏掉 Reverse_Data(不反序)⇒ 读回是正序发布标识")

    if fire is None:
        add("①c 断[C] %s 645 厂内标定读版本那一支真被执行" % bp_txt(bp_fac), None,
            "未做: 本次无调试会话", crit="①c", obs=judge.DEBUG,
            falsify="厂内标定读版本走别的分支或提前返回 ⇒ 不停在那一行")
    else:
        r = fire(bp_fac, lambda: sv_read_645_fac(ser, wait=wait),
                 label="断[C] 645 厂内标定读 %s 停 %s" % (SV_DI_FAC, bp_txt(bp_fac)),
                 vars=tuple(vars_fac), timeout=wait + 20.0, crit="①c",
                 falsify="厂内标定读版本不经过 :843(memcpy + Reverse_Data 之后)⇒ 断点不停")
        if r is not None:
            recs.append(r)
        hit, vv = hit_vars(r)
        add("①c 断[C] %s 645 厂内标定读版本那一支真被执行(白盒只给指令路径)" % bp_txt(bp_fac),
            True if hit else None,
            ("停住读到 pu8Frame=%s" % vv.get("pu8Frame", "(读不到)")) if hit
            else "断[C] 没命中/没下上 ⇒ 本次没做成",
            crit="①c", obs=judge.DEBUG,
            falsify="厂内标定读版本走别的分支/提前返回 ⇒ 不停在 :843")

    # ================= ②a / ②c 三段程序集成标识 =================
    ids = {}
    for oad, _nm in SV_OAD_IDS:
        ids[oad] = sv_read_698_ids(ser, oad, wait=wait)

    got_app = ids.get(SV_OAD_APP)
    add("②a 应用区 %s 的读回值 == 离线重算(线上 = 最高位在前)" % SV_OAD_APP,
        None if (got_app is None or exp_digits is None) else (got_app == exp_digits),
        ("读回「%s」⇔ 离线重算 %#010x → 固件口径「%s」(线上 = 低 8 位十进制本身)" % (got_app, exp_val, exp_digits))
        if (got_app is not None and exp_digits is not None)
        else ("应用区没读回" if got_app is None else "离线重算没做成") + " ⇒ 本次没做成",
        crit="②a", obs=judge.SERIAL,
        falsify="固件改用别的累加口径(逐字节/大端字)/区间端点错/不截 32 位/少翻一次 ⇒ 对不上重算值")

    if fire is None:
        add("②a 断[A] %s 停住时 buff 开头就是那 %d 个字符" % (bp_txt(bp_698), SV_IDS), None,
            "未做: 本次无调试会话", crit="②a", obs=judge.DEBUG,
            falsify="buff 里的字符与离线重算不符 ⇒ 固件算的不是这个口径")
    else:
        r = fire(bp_698, lambda: sv_read_698_ids(ser, SV_OAD_APP, wait=wait),
                 label="断[A] 698 读 %s 停 %s 读 buff" % (SV_OAD_APP, bp_txt(bp_698)),
                 vars=tuple(vars_698), timeout=wait + 20.0, crit="②a",
                 falsify="应用区那一支不经过 :8921(共享尾部)⇒ 断点不停")
        if r is not None:
            recs.append(r)
        hit, vv = hit_vars(r)
        b = bytes(gdb_bytes(vv.get("buff")))
        wb_txt, wb_form = sv_digits_of_head(b)
        wb_exp = None if exp_val is None else sv_digits_buff(exp_val)
        add("②a 断[A] %s 停住时 buff 开头就是那 %d 个字符(最低位在前)" % (bp_txt(bp_698), SV_IDS),
            (wb_txt == wb_exp) if (hit and wb_txt is not None and wb_exp is not None) else None,
            ("停住读到 buff 开头「%s」(封装=%s, `sch`=%s)⇔ 离线重算的 buff 形态「%s」"
             % (wb_txt, wb_form, vv.get("sch", "(读不到)"), wb_exp))
            if (hit and wb_txt is not None and wb_exp is not None)
            else ("停住读到 buff 前 8B=%s, 但形态对不上两种封装" % b[:SV_IDS].hex(" ").upper()) if hit
            else "断[A] 没命中/没下上 ⇒ 白盒这一半本次没做成",
            crit="②a", obs=judge.DEBUG,
            falsify="buff 里的字符与离线重算的倒序形态不符 ⇒ 固件算的不是这个口径")

    v2 = sv_digits_val(ids.get(SV_OAD_TOTAL))
    v3 = sv_digits_val(ids.get(SV_OAD_FACT))
    v4 = sv_digits_val(got_app)
    nm_c = "②c 三段分区自洽: %s == (%s + %s) mod 10^8" % (SV_OAD_TOTAL, SV_OAD_FACT, SV_OAD_APP)
    if None in (v2, v3, v4):
        add(nm_c, None, "三条标识没读全(有没读回或形态对不上的)⇒ 本次没做成",
            crit="②c", obs=judge.SERIAL,
            falsify="三段不是同一分区/同一口径 ⇒ 关系不成立")
    else:
        rel = (v2 == (v3 + v4) % SV_MOD)
        add(nm_c, True if rel else None,
            ("%d == (%d + %d) mod 10^8 = %d ✓" % (v2, v3, v4, (v3 + v4) % SV_MOD)) if rel
            else ("%d != (%d + %d) mod 10^8 = %d —— **不判失败**: 某一支原始和若发生 2^32 回绕, "
                  "取低 8 位后关系照样不成立, 本台分辨不了 ⇒ 记未证" % (v2, v3, v4, (v3 + v4) % SV_MOD)),
            crit="②c", obs=judge.SERIAL,
            falsify="三段不是同一分区/同一口径(如 02 用了别的区间)⇒ 关系不成立")

    if not [r for r in recs if r.get("crit")]:
        why.append("一条判据证据都没产生")
    return recs, why, "ok"


# ============================================================================
# 1-3 电能数据 · 支持 2 / 4 位小数与尾数
# ============================================================================
# 规格(ledger.md 1-3)判过三条: ①改 DotE→液晶 2/4 位随之切换; ②借位联动正确;
# ③(有源)698 读数按对象位数量纲正确。两种观测的分工是**死的**, 不是可选的:
#   · ①② —— **只有断点观测给得出**: 位数是 `Disp_Energy` 函数内的局部量 `dot`(寄存器驻留),
#     液晶本身没有回读入口; 而且固件**外部改参口全注释**(698 写 0x40070200=DLT698App.c:10338
#     注释、645 写 0x00030301/0x00030401=DLT645App.c:1823/1832 注释) ⇒ 只能注入。
#   · ③ —— **只有串口观测给得出**: 量纲折算在固件里, 外部能看到的就是上线的那几个整数。
DISP_OI_HARM = P.DISP_OI_HARM
DISP_DIGIT_ATTRS = P.DISP_DIGIT_ATTRS
DISP_TYPE_LEN = P.DISP_TYPE_LEN
DISP_TAIL = P.DISP_TAIL
DISP_IDX0 = P.DISP_IDX0
DISP_NIDX = P.DISP_NIDX
DISP_DOT_IDX = P.DISP_DOT_IDX
DISP_BORROW_IDX = P.DISP_BORROW_IDX
DISP_DOT_NAME = P.DISP_DOT_NAME
DISP_BORROW_NAME = P.DISP_BORROW_NAME
DISP_VAR = P.DISP_VAR
DISP_INJ_VARS = P.DISP_INJ_VARS
DISP_LMT_DOT_CFG = P.DISP_LMT_DOT_CFG
DISP_DOT_SWITCH_MAX = P.DISP_DOT_SWITCH_MAX
DISP_BPT_SWITCH = P.DISP_BPT_SWITCH
DISP_BPT_END = P.DISP_BPT_END
DISP_BPT_SETDP = P.DISP_BPT_SETDP
DISP_BIG_U64 = P.DISP_BIG_U64



def disp_oad(oi, attr, idx):
    """位数对象的 OAD 文本: `<OI2><属性2><索引1>`。属性 = 位数(02/04/06尾数/08), 索引+1 = 项号。"""
    return "%s%s%02X" % (oi, attr, idx & 0xFF)


def disp_scalar_ud(ud, oad):
    """普通 GET 标量对象应答 → `(类型字节, 值)` 或 None(**形态/长度对不上就不认, 不猜**)。

    实测形态(探针真表打出, 5 项 × 4 属性 20 次全同构):
        `85 01 <PIID> <OAD4> 01 <type> <值 nB> <2B 外壳>`   (n 由 `DISP_TYPE_LEN[type]` 定)
    长度是**等式**不是下限: `len == 9 + n + 2` —— 多一字节少一字节都判不认。这样"外壳到底几个
    字节"这种会在别处静默取错字节的问题, 在这里变成一声明确的 None。"""
    b = bytes(ud or b"")
    if len(b) < 11 or b[0] != 0x85 or b[1] != 0x01:
        return None
    if b[3:7].hex().upper() != str(oad).upper() or b[7] != 0x01:
        return None
    n = DISP_TYPE_LEN.get(b[8])
    if n is None or len(b) != 9 + n + DISP_TAIL:
        return None
    return (b[8], int.from_bytes(b[9:9 + n], "big"))


def disp_read_digits(ser, oi=DISP_OI_HARM, idxs=None, wait=2.0, verbose=True):
    """逐 (位数对象, 索引) 发标量 GET → `{索引: {属性: (类型, 值)}}`。读不到/形态不规的落 `None`。

    打印原始 hex —— 形态一旦与 `disp_scalar_ud` 的等式不符, 这一行就是唯一的线索(下次照它改表)。"""
    idxs = list(range(DISP_IDX0, DISP_IDX0 + DISP_NIDX)) if idxs is None else list(idxs)
    out = {}
    for idx in idxs:
        row = {}
        for attr, _name in DISP_DIGIT_ATTRS:
            oad = disp_oad(oi, attr, idx)
            ud = read_oad_ud(ser, oad, wait=wait)
            row[attr] = disp_scalar_ud(ud, oad)
            if verbose:
                b = bytes(ud or b"")
                print("      %-10s %s" % (
                    oad, ("%d" % row[attr][1]) if row[attr] else "读不到(ud=%s)" % (
                        b.hex(" ").upper() if b else "空")))
        out[idx] = row
    return out


def disp_digit_relation(v6, v4, v2, vm):
    """判据③ 的**关系式**(不是"能不能读到"): 4位==6位//100, 尾数==6位%100, 2位==6位//10000。
    → `(ok, 失败项说明)`。四条任一读不到由调用方先拦。"""
    bad = []
    if v4 != v6 // 100:
        bad.append("4位=%d≠6位//100=%d" % (v4, v6 // 100))
    if vm != v6 % 100:
        bad.append("尾数=%d≠6位%%100=%d" % (vm, v6 % 100))
    if v2 != v6 // 10000:
        bad.append("2位=%d≠6位//10000=%d" % (v2, v6 // 10000))
    return (not bad), ("; ".join(bad) if bad else "4位×100+尾数=%d×100+%d=%d==6位"
                       % (v4, vm, v4 * 100 + vm))


def disp_digit_criteria():
    """1-3 的**预设判据条目**(测试前定死; 源 = ledger.md 1-3「观察与判据」的『判过』那一句)。
    原文『①改 DotE→液晶 2/4 位随之切换; ②借位联动正确; ③(有源)698 读数按对象位数量纲正确』,
    其中 ② 拆成下限/写入两支(源码里是两处独立判定, 一条判据一个 falsify 对不上两处)。"""
    return {
        "①": "改 g_DispPara[DotE] ⇒ 液晶电能小数位数随之切换(:3188 dot = DotE - 进位)",
        "②a": "借位下限: 进位 dot < g_DispPara[Borrow] ⇒ 显示位数被 Borrow 抬起(:3174)",
        "②b": "借位写入: 进位 dot > g_DispPara[Borrow] ⇒ :3171 自动把 Borrow 改成 dot",
        "③": "698 读数按对象位数量纲: 4位==6位//100, 尾数==6位%100, 2位==6位//10000",
    }


def _disp_shot(inj, label, assigns, watch=DISP_BPT_END, watch_vars=("dot",),
               then_assigns=None, at=DISP_BPT_SWITCH, at_vars=(), timeout=30.0):
    """1-3 断点观测的一次(库内私有): 停 `at` → 写 `assigns` → 等 `watch` → 读 `watch_vars`。

    收在一个地方的**唯一**理由是两个断点 + 两个收尾钩子(`at_vars`/`then_assigns`)在 1-3 里是常量:
    ① 的注入点在 `switch(g_DispPara[DotE])` **载入之前**(`:3137`), 读点在同一次执行的末句(`:3199`)——
    这样读到的 `dot` 一定是"这一刀刚切出来的", 不必赌注入值能活到下一轮(活不到: 注入会破坏
    `g_DispPara` 的 CRC ⇒ `Run_TaskVessel:750 Renew_DispPara` 立刻按 CRC 从 EEPROM 整块恢复,
    2026-09-11 实踩)。"""
    if inj is None:
        return None, "%s: 本次无断点会话(J-Link 没接 / 用户指定只做串口观测)" % label
    out = inj(at=at, assigns=list(assigns), watch=watch, watch_vars=tuple(watch_vars),
              at_vars=tuple(at_vars), then_assigns=then_assigns, timeout=timeout)
    if out.get("unavailable"):
        return out, "%s: 断点没下上 —— %s" % (label, out.get("note"))
    if out.get("at_hit") is None:
        return out, "%s: 没停到注入点(%s) ⇒ 这一次一个字都没改" % (label, _bptxt(at))
    if out.get("hit") is None:
        return out, "%s: 注入了但判据断点(%s)没命中" % (label, _bptxt(watch))
    return out, None


def disp_gdispara_decode(raw):
    """`g_DispPara` 的**原始字节** → `{"dot": …, "borrow": …, "raw": hex}` 或 None(短于 12B 不认)。

    读由脚本自己做(裸 `watch.watch_vars(ser, [CB.DISP_VAR])` —— 本项唯一一次 AA80 直读), 这里只解码。
    用途只有一个: **收尾净零核对** —— ②b 会让固件自己把 `Borrow` 写进 EEPROM(`Set_DispPara:1850
    Write_ParaData`), 而本台没有反向复位口, 全靠那一次的 `then_assigns` 把它改回 0 之后由固件
    亲手写回。读回来对一眼, 免得"证完联动、把表改脏"。"""
    raw = bytes(raw or b"")
    if len(raw) < 12:
        return None
    return {"dot": raw[DISP_DOT_IDX], "borrow": raw[DISP_BORROW_IDX],
            "raw": raw.hex(" ").upper()}


# ---- 1-3 的四步: 一步一个积木, 脚本正文一行一步(顺序即步骤, 见 project/tests/_test_1_3_display_digits.py) ----
# ③ 读数侧量纲走串口; ①②a②b 是 `Disp_Energy` 的函数内局部量(液晶无回读入口、外部改参口全注释)
# ⇒ **只有断点/注入观测给得出**, 没会话时各自记 `ok=None`(没做成), 不拿另一种观测的读数去顶。
# 每一步的判据口径写在下面 1-3 段头; 每个函数只说自己那一步做什么。


def disp_digit_intro():
    """第一步 · 开场: 本项在比什么(只打印, 不判)。"""
    print("\n== 1-3 电能数据 · 支持 2/4 位小数与尾数 ==")

def disp_digit_readings(ser, oi=DISP_OI_HARM, idxs=None, wait=2.0):
    """第二步 · ③ 读: 位数对象族(`0220{02,04,06,08}{01..05}` 标量 GET) → 逐项字典。只读不比。"""
    print("   [串口观测] 位数对象标量 GET(%s 族, 索引 01..05 = 第 0..4 项):" % oi)
    return disp_read_digits(ser, oi=oi, idxs=idxs, wait=wait)


def disp_digit_scale(got):
    """第三步 · ③ 判定: 4位==6位//100, 尾数==6位%100, 2位==6位//10000(全 0 项不计, 0==0 不算证据)。"""
    recs, why, add = _rec_bag()
    if not got:
        why.append("位数对象一次都没读回 ⇒ 判③无证据")
        add("③ 698 读数按对象位数量纲", None, "半途中止: " + why[-1],
            crit="③", falsify="量纲折算错(如 4位用 /1000) ⇒ 三个对象不再差 100 的幂")
        return _rec_close(recs, why)
    nz = 0
    ok3 = True
    det3 = []
    for idx in sorted(got):
        row = got[idx]
        cells = {}
        miss = [attr for attr, _n in DISP_DIGIT_ATTRS if not row.get(attr)]
        if miss:
            det3.append("第%d项(索引%02X) 读不到: %s" % (idx - DISP_IDX0, idx, ",".join(miss)))
            ok3 = False
            continue
        for attr, _n in DISP_DIGIT_ATTRS:
            cells[attr] = row[attr][1]
        v6, v4, vm, v2 = cells["08"], cells["04"], cells["06"], cells["02"]
        if v6 == 0:
            # 空载项: 关系式 0==0 恒成立、**任何**折算错都照样绿 ⇒ 不计入(避免假通过)。
            continue
        nz += 1
        good, note = disp_digit_relation(v6, v4, v2, vm)
        ok3 = ok3 and good
        det3.append("第%d项: 6位=%d 4位=%d 尾数=%d 2位=%d → %s" % (
            idx - DISP_IDX0, v6, v4, vm, v2, note if not good else "关系成立"))
    if nz == 0:
        add("③ 698 读数按对象位数量纲", None,
            "5 个项的 6 位读数全为 0 ⇒ 本台面这条无分辨力(0==0 恒成立, 不算证据); 明细: %s"
            % " | ".join(det3),
            crit="③", obs=judge.SERIAL, falsify="量纲折算错 ⇒ 三个对象不再差 100 的幂")
    else:
        print("   ③ 明细: %s" % " | ".join(det3))
        add("③ 698 读数按对象位数量纲", ok3,
            "%d 个非零项对拍: %s" % (nz, " | ".join(det3)),
            crit="③", obs=judge.SERIAL, falsify="量纲折算错(如 4位用 /1000) ⇒ 三个对象不再差 100 的幂")

    return _rec_close(recs, why)


def disp_digit_dote(inj, dot_points=(0, 2, 4), bp_sw=DISP_BPT_SWITCH, bp_end=DISP_BPT_END):
    """第四步 · ① 判定: 注入 `g_DispPara[DotE]=k` → 停 `:3137`(切换载入点) → 等 `:3199` 读 `dot`, 逐点比 `dot == k`。"""
    recs, why, add = _rec_bag()
    if inj is None:
        add("① 改 DotE ⇒ 液晶位数随之切换", None,
            "本次无断点会话 ⇒ 位数/借位都是 `Disp_Energy` 的函数内局部量, "
            "液晶无回读入口、外部改参口全注释, 串口观测给不出",
            crit="①", obs=judge.DEBUG, falsify="位数写死/switch 恒取 case 2 ⇒ 注入后 dot 不变")
        return _rec_close(recs, why)
    # ---------- ① 改 DotE → 液晶位数随之切换(断点观测) ----------
    # 三点: 0 / 2 是本版**合法配置范围**(TAB_DispParaLmt[DotE]={0,2})内的两个端点, 4 越界但
    # `switch` 有 case 4 —— 拿它把"显示逻辑本身支持 4 位"与"配置表不放行 4 位"分开写清。
    print("   [断点观测] 注入 g_DispPara[DotE]=%s, 停 :3137 → 等 :3199 读 dot:"
          % "/".join(str(k) for k in dot_points))
    ok1 = True
    det1 = []
    for k in dot_points:
        out, err = _disp_shot(inj, "①DotE=%d" % k, [(DISP_DOT_NAME, k)],
                              at=bp_sw, watch=bp_end,
                              watch_vars=("dot", "num", "u64", DISP_DOT_NAME))
        if err:
            why.append(err)
            add("① 改 DotE ⇒ 液晶位数随之切换", None, err, crit="①", obs=judge.DEBUG,
                falsify="位数写死/switch 恒取 case 2 ⇒ 注入后 dot 不变",
                trig=judge.TRIG_INJECT)
            ok1 = None
            break
        dot = kwh_num(out["vars"].get("dot"))
        back = kwh_num(out["vars"].get(DISP_DOT_NAME))
        u64 = kwh_num(out["vars"].get("u64"))
        print("      DotE=%-2d → 读回 g_DispPara[DotE]=%-4s dot=%-4s u64=%-14s (停 @%s)" % (
            k, back, dot, u64, out["hit"].where()))
        good = (dot == k)
        ok1 = ok1 and good
        det1.append("DotE=%d→dot=%s%s" % (k, dot, "" if good else "(不符)"))
    if ok1 is not None:
        add("① 改 DotE ⇒ 液晶位数随之切换", ok1,
            "%s; ⚠ 本版 TAB_DispParaLmt[DotE]={0,2} ⇒ 4 只能是注入态、外部存不住(见段头)"
            % "; ".join(det1),
            crit="①", obs=judge.DEBUG,
            falsify="位数写死/switch 恒取 case 2 ⇒ 注入后 dot 不变", trig=judge.TRIG_INJECT)

    return _rec_close(recs, why)


def disp_digit_borrow_floor(inj, bp_sw=DISP_BPT_SWITCH, bp_end=DISP_BPT_END):
    """第五步 · ②a 判定: 注入 `Borrow=2`(进位为 0) → 看 `:3174` 是否把 dot 抬到 「DotE-Borrow」。"""
    recs, why, add = _rec_bag()
    if inj is None:
        add("②a 借位下限(dot < Borrow ⇒ dot=Borrow)", None,
            "本次无断点会话 ⇒ 位数/借位都是 `Disp_Energy` 的函数内局部量, "
            "液晶无回读入口、外部改参口全注释, 串口观测给不出",
            crit="②a", obs=judge.DEBUG, falsify="漏掉 :3174 的下限夹取 ⇒ dot 仍为进位值")
        return _rec_close(recs, why)
    # ---------- ②a 借位下限(断点观测) ----------
    print("   [断点观测] 注入 g_DispPara[Borrow]=2(进位为 0), 看 :3174 是否把 dot 抬到 2:")
    out, err = _disp_shot(inj, "②a", [(DISP_BORROW_NAME, 2)],
                          at=bp_sw, watch=bp_end,
                          watch_vars=("dot", DISP_BORROW_NAME, DISP_DOT_NAME),
                          at_vars=(DISP_DOT_NAME, DISP_BORROW_NAME))
    if err:
        why.append(err)
        add("②a 借位下限(dot < Borrow ⇒ dot=Borrow)", None, err, crit="②a", obs=judge.DEBUG,
            falsify="漏掉 :3174 的下限夹取 ⇒ dot 仍为进位值", trig=judge.TRIG_INJECT)
    else:
        dot = kwh_num(out["vars"].get("dot"))
        bor = kwh_num(out["vars"].get(DISP_BORROW_NAME))
        dot_e = kwh_num(out["vars"].get(DISP_DOT_NAME))
        print("      注入前(停 :3137) DotE=%s Borrow=%s | 注入后 :3199 dot=%s Borrow=%s" % (
            out["at_vals"].get(DISP_DOT_NAME), out["at_vals"].get(DISP_BORROW_NAME), dot, bor))
        # dot@:3199 = DotE - max(进位, Borrow); 本台进位恒 0 ⇒ 期望 = DotE - Borrow。
        exp = None if (dot_e is None or bor is None) else dot_e - bor
        good = (dot is not None and exp is not None and dot == exp and bor == 2)
        add("②a 借位下限(dot < Borrow ⇒ dot=Borrow)", good,
            "Borrow=2 注入后 :3199 dot=%s(期望 DotE(%s)-Borrow(%s)=%s) —— dot 被 Borrow 抬起时"
            "`dot = DotE - Borrow` 会变小 = 液晶少显示小数位; 本台进位恒 0(见段头)" % (
                dot, dot_e, bor, exp),
            crit="②a", obs=judge.DEBUG, trig=judge.TRIG_INJECT,
            falsify="漏掉 :3174 的下限夹取 ⇒ dot 仍为进位值(0), 与 DotE-Borrow 不符")

    return _rec_close(recs, why)


def disp_digit_borrow_write(inj, big_u64=DISP_BIG_U64, bp_sw=DISP_BPT_SWITCH, bp_setdp=DISP_BPT_SETDP):
    """第六步 · ②b 判定: 注入进位值 → 看 `:3171` 是否把 Borrow 由 0 写成 dot(同一次已改回 0, 表侧净零)。"""
    recs, why, add = _rec_bag()
    if inj is None:
        add("②b 借位写入(:3171 把 Borrow 改成 dot)", None,
            "本次无断点会话 ⇒ 位数/借位都是 `Disp_Energy` 的函数内局部量, "
            "液晶无回读入口、外部改参口全注释, 串口观测给不出",
            crit="②b", obs=judge.DEBUG, falsify="缺 :3169-3172 的回写 ⇒ Borrow 恒为原值")
        return _rec_close(recs, why)
    # ---------- ②b 借位写入(断点观测) ----------
    # ⚠ 这一次会让**固件自己**把 Borrow 写进 EEPROM(`Set_DispPara:1850 Write_ParaData`), 本台没有
    #   反向复位口 ⇒ 用 `then_assigns` 在 `:1849`(Fetch_CRC 正要跑)把 Borrow 改回 0, 于是固件算出
    #   来的 CRC 与写进 EEPROM 的都是 {DotE:2, Borrow:0} = **原值** —— 证联动与"表侧净零"同一次完成。
    print("   [断点观测] 注入 u64=%d 造进位, 看 :3171 是否把 Borrow 由 0 写成 dot:" % big_u64)
    out, err = _disp_shot(inj, "②b", [("u64", big_u64)], at=bp_sw, watch=bp_setdp,
                          watch_vars=(DISP_BORROW_NAME,), at_vars=(DISP_BORROW_NAME,),
                          then_assigns=[(DISP_BORROW_NAME, 0)])
    if err:
        why.append(err)
        add("②b 借位写入(:3171 把 Borrow 改成 dot)", None, err, crit="②b", obs=judge.DEBUG,
            falsify="缺 :3169-3172 的回写 ⇒ Borrow 恒为原值", trig=judge.TRIG_INJECT)
    else:
        bor_in = kwh_num(out["at_vals"].get(DISP_BORROW_NAME))
        bor = kwh_num(out["vars"].get(DISP_BORROW_NAME))
        print("      停 :3137 时 Borrow=%s → 停 Set_DispPara:1849 时 Borrow=%s(这就是 :3171 写下的); "
              "随即按 then_assigns 改回 0, 让固件自己的 Fetch_CRC/Write_ParaData 落回原值" % (
                  bor_in, bor, ))
        good = (bor_in == 0 and bor is not None and bor > 0)
        add("②b 借位写入(:3171 把 Borrow 改成 dot)", good,
            "Borrow 由 %s 变 %s(@Set_DispPara:1849, 即 :3171 调进来的那一刻; 同一次已把它改回 0 再"
            "让固件写 EEPROM —— 联动被证、表侧净零)" % (bor_in, bor),
            crit="②b", obs=judge.DEBUG, trig=judge.TRIG_INJECT,
            falsify="缺 :3169-3172 的回写, 或进位门槛(1e12)判错 ⇒ Borrow 保持 %s 不变" % bor_in)

    return _rec_close(recs, why)


def disp_digit_netzero(gd):
    """第七步 · 收尾核对(**不是预设判据条目, crit=None 只进日志**): 脚本 AA80 直读并解码后的
    `g_DispPara` → 看 ②b 那一次是否把表写回了原值。`gd is None` = 那一次读没读成。"""
    recs, why, add = _rec_bag()
    # ---------- 收尾净零核对(crit=None: 本项不是预设判据条目, 只进日志) ----------
    if gd is None:
        why.append("收尾 AA80 直读 g_DispPara 失败 ⇒ ②b 的 EEPROM 净零未经核对")
    else:
        print("   [收尾] AA80 g_DispPara = %s" % gd["raw"])
        add("收尾: g_DispPara 已回到 {DotE:2, Borrow:0}", None,
            "AA80 直读 DotE=%d Borrow=%d —— %s" % (
                gd["dot"], gd["borrow"],
                "与出厂默认一致" if (gd["dot"], gd["borrow"]) == (2, 0)
                else "**不是默认值, 需处置**(②b 的 then_assigns 没兜住)"))
    return _rec_close(recs, why)



# ==================== 7-4『通信 · 路由扩展模组、计量芯 —— SPI 链路冒烟』(2026-09-21 建) ====================
# 规格(源 project/knowledge/_whitebox_ledger/ledger.md 7-4):
#   判过原文『请求帧类型/周期恒稳定、落位值随帧更新 ⇒ 管理芯只轮询读取、不自计算』。
#   本行只有计量芯 SPI 通道可冒烟; 扩展模组交互与加解密路由 = 开发标注未实现 ⇒ 不给断点、不测。
#
# ---- 链路事实(源码 `Platform/Communicate.c:986-1055` + 编译产物双证) ----
#   · `Run_TaskVessel` 是一台三格状态机 `switch(g_SPIStep)`:
#       格 0(:994) 组一张固定读请求 → `SpiWriteDMA`(:1006) → 自增
#       格 1(:1010) 清 512B 接收缓冲 → `SpiReadDMA`(:1015) → 自增
#       格 2(:1019) 找起始符 0x68 → 查结束符 0x16 / 查 FCS → `Save_Caculator_Data`(:1039) 落位 → 归零回格 0
#     一整个周期只有**一个** `SpiWriteDMA` 调用点(:1006), 故"每停一次 :1006"就是"每转一圈"。
#   · 请求内容 = 常量表 `TAB_GetBoxData`(Communicate.c:917-921, 离线 .out 现读 `sizeof` = 29)
#     整表拷贝(:999), 之后只有两处逐轮变化: `[9..14]` 表地址(:1001 `Get_MeterAddr`)与
#     `[16..17]` 帧头校验(:1003-1004)。两处都只随**表地址**走, 不随表内数据走
#     ⇒ 同一块表上每轮的 29 字节应当**一字不差**。
#   · 落位动作 `Save_Caculator_Data(:959-975)` 自己不换算, 转发给四个 `Save_*_Data`; 底层是
#     `Spread_NormalData`(DLT698App.c:15873) 的 `RevCopy_Data(pStru->OutBuf, pStru->InBuf, len)`
#     —— **逐字节倒序直拷**(Common.c:306), 没有乘除加减。`SET698` 分支先把 `InBuf` 跳过 1 字节
#     类型码(:15959)再拷 `len` 字节 ⇒ `g_Volt[0]`(INT32U, TaskMetering.c:263) 的内存四字节
#     = 帧内 `+59..+62` 的倒序, 按小端读出正好是该四字节的大端值。`g_Volt[0]` 就是 698 的
#     A 相电压 `0x20000201`(读出链 TaskMetering.c:1187 `value = g_Volt[id-ID_Ua]/100`)。
#   · 帧布局(偏移都相对 `STR1_Index` = 起始符 0x68 在 `g_SPIMBuff` 里的位置; 落位表见
#     TaskMetering.c:5011-5032 / TaskTime.c:1075 / kWhData.c:368 起):
#         +5..+10   表地址 6B             +24..+29  日期时间 6B(1B 类型码 + 5B 值)
#         +32..+37  计量芯系统状态字 6B   +58..+62  电压 5B(1B 类型码 + 4B 大端值)
#         +63..+67  电流 5B               +110..    电能每类 9B
#         +331      结束符 0x16
#   · `STR1_Index` 不是全局量, 是 `Run_TaskVessel` 的静态局部量(Communicate.c:987), 只能按
#     `'Run_TaskVessel'::STR1_Index` 读; 它在 `:1042` 每轮归零 ⇒ 只在"当轮停在 :1039"时有意义。
#
# ---- 为什么这么判(每条钥匙对应『判过』的哪半句) ----
#   · ① / ② 是『请求帧类型/周期恒稳定』那一半: ① 三拍逐字节全等 ⇒ 请求里没有一个字节随表内数据走;
#     ② 每次请求之后、下次请求之前都等到一次落位 ⇒ 不是"光发不收", 轮询是**闭合**的。
#   · ③ / ④ 是『落位值随帧更新』那一半: ③ 帧内日期时间域逐拍递增 ⇒ 落进来的是**新帧**, 不是把
#     同一帧复读; ④ 上一拍帧里的电压值 == 这一拍 `g_Volt[0]` ⇒ 落位是**直拷**, 管理芯没算过。
#   · ⑤ 是黑盒那一侧: SPI 链路送来的量在 698 上真的出得来 —— 链路断了这一条先塌。
#
# ⚠ ③ 用日期时间域而不是电压做"随帧更新": 空载台面上电压恒定, 相邻两拍读到的电压**本来就该相等**,
#   拿它判"更新"会把固件的正确行为判成不满足。时间域是这块表上唯一**必然**逐秒前进的域。
# ⚠ ④ 比的是"上一拍停住时读到的帧内字节"与"这一拍停住时读到的 `g_Volt[0]`" —— 停住那一刻
#   `Save_Caculator_Data` 还没跑, `g_Volt` 里装的正是**上一帧**落位的结果, 两边刚好隔一帧。
#   两拍相邻(同一条 SPI 循环里前后两圈), 空载电压在两圈之间不变, 故这一比是稳的。
# ⚠ ② 判的是"每次请求之后都等得到一次落位", **不是"每拍都无重发"** —— 重发的两个触发支路
#   (`:1035` 尾字节非 `0x16`、`:1037` FCS 不过)是固件对不合格帧的**设计支路**, 由链路帧合格率
#   决定, 不由轮询纪律决定; 拿它判固件等于拿链路的帧合格率判固件(见 12301 那段 ⚠)。
#   故本项**显式交替**等(请求 → 落位 → 请求 …), 判"等落位那一次等到了没有"。
# ⚠ `Session.other_hits` 数出来的 `:1006` / `:1039` 整场次数是**下界** —— 放行到下一次
#   `wait_only` 之间那一小段没人轮询队列, 落在那一窗里的命中会被丢掉。所以那两个数只配
#   当台面事实写进 detail(记每拍重发与否也是这个用途), **不许拿它们定红绿**。
# ⚠ 没会话时 ①②③④ 一律记 `ok=None`(没做成) —— 不拿另一种观测的读数去顶; ⑤ 照做。

SPI_REQ_LEN = 29                       # sizeof(TAB_GetBoxData), 离线 .out 现读
SPI_REQ_PRE = 0x5A                     # 请求帧前导 4 字节
SPI_FRAME_HEAD = 0x68                  # 起始符
SPI_FRAME_TAIL = 0x16                  # 结束符
SPI_TAIL_OFF = 331                     # 结束符相对起始符的偏移
SPI_DT_OFF = 24                        # 日期时间域(1B 类型码 + 7B 值); 长度 --8 见 TaskTime.c:1075
SPI_DT_LEN = 8
SPI_DT_TYPE = 0x1C                     # D_DateTimeS(28) = octet-string(SIZE(7)), DLT698App.c:151
SPI_VOLT_VAL_OFF = 59                  # 电压值 4 字节(= 电压域偏移 58 跳掉类型码)
SPI_VOLT_LEN = 4
SPI_VOLT_OAD = "20000201"              # A 相电压(白盒链 g_Volt[0])

# ---- ⑤ 那一问的**应答**形态(与上面那几行是两条链: 上面是 SPI 帧里, 这里是 698 线上) ----
# 来历见 `spi_volt_from_ud` 的逐字节说明; 常量的**名字即含义**, 不另设表。
SPI_GET_SERVICE = 0x85                 # Get-Response 服务字节
SPI_GET_NORMAL = 0x01                  # GetResponseNormal 类型字节
SPI_GET_DATA = 0x01                    # OAD 之后的结果标签: 01=带数据(00=出错, 其后才是 DAR)
SPI_VOLT_TYPE = 0x12                   # D_LongUnsigned(18): 无符号 16 位
SPI_VOLT_ANS_LEN = 2                   # 上面那类型的值字节数(大端)
SPI_VOLT_APDU_MIN = 9 + SPI_VOLT_ANS_LEN   # 判形态时至少要看到的长度(尾随那两个 00 不参与判)


def spi_req_vars():
    """`:1006` 停住时要读的表达式表: 状态机格号 + 要发出去的 29 字节。"""
    return tuple(["g_SPIStep"] + spi_req_keys())


def spi_req_keys():
    """要发出去的那 29 个字节的表达式键 —— 与 `spi_req_vars()` **按同一段代码生成**, 不许另写一份。

    脚本要自己解这 29 字节(它发的帧、它读的镜像是同一批), 故这串键是**公开接口**;
    `vars_bytes(vals, keys)` 直接吃它。"""
    return ["((unsigned char *)g_SPIMBuff)[%d]" % i for i in range(SPI_REQ_LEN)]


def spi_land_keys():
    """`:1039` 要读的四串键 —— **具名字段**, 不许按位置解包(加一列就惊动所有解包处)。

    · `index` 起始符偏移(`Run_TaskVessel` 的静态局部量);
    · `dt`    帧内时间域 8 字节(+`SPI_DT_OFF`);
    · `volt`  帧内电压值 4 字节(+`SPI_VOLT_VAL_OFF`);
    · `gvolt` 已落位的 `g_Volt[0]` 四字节。
    """
    i = "'Run_TaskVessel'::STR1_Index"
    dt = ["((unsigned char *)g_SPIMBuff)[%s+%d]" % (i, SPI_DT_OFF + k) for k in range(SPI_DT_LEN)]
    volt = ["((unsigned char *)g_SPIMBuff)[%s+%d]" % (i, SPI_VOLT_VAL_OFF + k)
            for k in range(SPI_VOLT_LEN)]
    gv = ["((unsigned char *)&g_Volt[0])[%d]" % k for k in range(SPI_VOLT_LEN)]
    return {"index": i, "dt": dt, "volt": volt, "gvolt": gv}


def spi_land_vars():
    """`:1039` 停住时要读的表达式表: 起始符偏移 + 帧内时间域 + 帧内电压 + 已落位的 g_Volt[0]。"""
    k = spi_land_keys()
    return tuple([k["index"]] + k["dt"] + k["volt"] + k["gvolt"])


def spi_volt_vars():
    """落位**之后**那一停(`TaskMetering.c:5035`)要读的表达式表: 帧内电压 + 刚落位的 g_Volt[0]。

    ⚠ 为什么 ④ 必须换到这个停点: 比的是"来值 == 存值", 而这**两样要在同一帧、同一刻**才可比。
      `:1039` 那个停点在 `Save_Caculator_Data` **调用之前** —— 那一刻 `g_Volt` 装的是**上一帧**
      落位的结果, 于是"第 1 拍的帧"只能跟"第 2 拍的 g_Volt"跨拍配。而两拍之间夹着几次落位
      **不由我们决定**(实测 `log/7_4_spi_link_20260921_104429.log`: 两次观测到的 `:1039` 之间还
      夹了一次没被等待的 `:1039`), 跨拍配就配错了帧 —— 出来的差(实测 35~102 mV, 符号还不一致)
      会被读成"固件算过", 而固件只是在抽样另一个时刻。
      `:5035`(`TaskMetering.c` 电压段的 `Spread_StructArray` 之后、下一个域的 `info.Type` 那一行)
      落在**同一次落位之内**, 两个量天然同帧, 不需要任何跨拍假设。"""
    k = spi_land_keys()
    return tuple(k["volt"] + k["gvolt"])


def vars_bytes(vals, keys):
    """从 `{表达式: 文本}` 里按 keys 取一串字节; **任一读不到 → None**(不当 0 顶)。"""
    out = []
    for k in keys:
        n = kwh_num(vals.get(k))
        if n is None:
            return None
        out.append(n & 0xFF)
    return bytes(out)


def spi_volt_from_ud(ud):
    """698 读 A 相电压(`0x20000201`) 应答的 APDU → 原始整数(0.1V) | None。

    应答逐字节形态(固件 `DLT698App.c:4582 CMD_GetRequestNormal` 写 + `:15873 Spread_NormalData` 展开):
        `85 01 <PIID> <OAD4> <结果标签> <类型码> <值 N 字节大端> [00 00]`
      · `85`(+`01` GetResponseNormal) 服务字节, `<PIID>` 回显(`:4355-4356`);
      · OAD 之后那一字节是**结果标签**: `01` = 带数据, `00` = 出错且其后 1 字节是 DAR
        (`:4601-4602` 与 `:4675-4677` 两支都这么写) —— 它**不是**类型码, 别顺着读;
      · A 相电压的类型码 = `0x12`(`D_LongUnsigned = 18`, `:142`), 值 **2 字节大端**
        (`:15902` len=2, `:15977 RevCopy_Data` 反序拷出);
      · 末尾那 2 字节是 Follow_Report + Follow_TimeTag(都是 `00`), 本函数不看它们。
    换算: `TAB_NormalObj` 该条 `scale = -1`(`:1031`) ⇒ 原始值 × 0.1 = 伏(1239 → 123.9 V)。
    不是这个形态一律 None —— 不猜、不截, 免得把"帧没收到"糊成"值是 0"。"""
    b = bytes(ud or b"")
    if len(b) < SPI_VOLT_APDU_MIN or b[0] != SPI_GET_SERVICE or b[1] != SPI_GET_NORMAL \
            or b[7] != SPI_GET_DATA or b[8] != SPI_VOLT_TYPE:
        return None
    return int.from_bytes(b[9:9 + SPI_VOLT_ANS_LEN], "big")


def spi_dt_key(b8):
    """帧内日期时间域 8 字节(1B 类型码 + 7B 值) → `(年, 月, 日, 时, 分, 秒)`; 形态不对 → None。

    线上那 7 个值字节依次是 `年Hi 年Lo 月 日 时 分 秒` —— 源码 `DLT698App.c:15994-16178`
    (`Spread_DateTime` 的 SET698 支): `year = InBuf[1]<<8 | InBuf[2]`, 循环里
    `OutBuf[i] = InBuf[num+1-i]`(num=6) 把 `InBuf[7]/[6]/[5]/[4]/[3]` 依次摆成
    `秒/分/时/日/月` —— 所以**线序是年在前**, 落位到 `objtime` 才是 `秒分时日月年`。
    ⚠ 别照 `objtime` 的次序去读线上: 那会把 `07 EA 09 15 0A` 读成 `(月,日,时,分,秒)`
      = `(10, 21, 9, 234, 7)`(分 = 234) —— 一个当场就该看出形态不对的元组。
    ⚠ 本函数**返回 None 而不是硬折**: 类型码不是 `0x1C`(D_DateTimeS)或读不全时, 折出来的是
      别的域, 拿去比只会得到一个"看着像时间"的元组, 而 FAIL 会记到固件头上。
    ⚠ 编码是**二进制**不是 BCD(`0x15` 当日 = 21, BCD 该是 `0x21`); 按整数折即可 ——
      年用线上那 16 位本身(2026 就是 `0x07EA`), 别再减 2000。"""
    b = bytes(b8 or b"")
    if len(b) < SPI_DT_LEN or b[0] != SPI_DT_TYPE:
        return None
    return (b[1] << 8 | b[2], b[3], b[4], b[5], b[6], b[7])


SPI_FALSIFY = {
    "①": "请求随表内数据变 ⇒ 各拍字节不等",
    "②": "只发不收 ⇒ 每次等 :1039 都超时(落位口一次都不命中)",
    "③": "同一帧被反复落位 ⇒ 两拍时间域相等",
    "④": "落位时做过换算/取反 ⇒ 存值与来值对不上",
    "⑤": "链路断了 ⇒ 读不到或恒为 0",
}


def spi_link_criteria():
    """7-4 的**预设判据条目**(测试前定死; 源 = ledger.md 7-4「观察与判据」的『判过』那一句)。

    ⚠ 这五条**逐字抄 ledger.md 7-4 的 J 列**: 改那边就得同步改这儿。"""
    return {
        "①": "请求恒为固定读: 停 Communicate.c:1006 连取三拍, 发出去的 29 字节(g_SPIMBuff[0..28], = sizeof(TAB_GetBoxData))逐字节全等, 且[0..3]=5A5A5A5A、[4]=0x68、[28]=0x16",
        "②": "轮询闭合: 每一拍 :1006 请求之后都等到一次 :1039 落位(发出请求要有帧回来并落位), 整场落位数 > 0",
        "③": "落位值随帧更新: 停 Communicate.c:1039 连取两拍, 帧内日期时间域(+24 起 8 字节 = 1B 类型码 0x1C + 7B 值, 线序 年 月 日 时 分 秒)第二拍 > 第一拍",
        "④": "落位是直拷不自算: 停 TaskMetering.c:5035(电压段落位之后)逐拍比, 帧内 +59 起 4 字节按大端折出的值 == 同一次落位刚落下的 g_Volt[0] 4 字节按小端折出的值",
        "⑤": "SPI 送来的量出得来: 698 读 0x20000201(A相电压)读得到且非零",
    }


def spi_link_roundtrip(ser, sess=None, bp_req=None, bp_land=None, bp_volt=None, n_rounds=3,
                       wait=3.0, timeout=60.0):
    """7-4 判过积木: 『请求帧类型/周期恒稳定、落位值随帧更新』。

    为什么这样切(每一步对应规格 ①~⑤ 的哪一条):
      · ⑤ —— 串口观测: 698 读 A 相电压。**先做** —— 之后停核会让串口帧变慢甚至丢,
        反过来的顺序会把这一条做成假 FAIL。
      · ① / ② —— 断点观测, 停 `Communicate.c:1006`(`SpiWriteDMA` 调用点, 每转一圈一次):
        ① 三拍 29 字节逐字节全等 + 形态校验; ② 每拍之后**显式**等一次 `:1039`, 判"等到了"
        —— 发出去的请求要有帧回来并落位。⚠ ② **不判"无重发"**: `:1035`(尾字节非 `0x16`)与
        `:1037`(CRC 不过)两支是固件对不合格帧的**设计支路**(重发、不落位), 让它参与判定
        等于拿链路的帧合格率判固件; 重发次数照记进 detail, 作台面事实。
      · ③ —— 断点观测, 停 `Communicate.c:1039`(`Save_Caculator_Data` 调用点):
        帧内日期时间域(8 字节, `spi_dt_key` 解码)逐拍递增。
      · ④ —— 断点观测, 停 `TaskMetering.c:5035`(电压段落位**之后**): 同一帧的 +59 起 4 字节
        == 刚落位的 `g_Volt[0]` 四字节(直拷、管理芯不自算)。**不跨拍配** —— 为什么见
        `spi_volt_vars()` 的 ⚠。
    返回 `(recs, why, scope)`; **scope 半途中止时是 `None`**(结构信号, 别让脚本嗅文案)。
    需先 enter_factory(698 读在厂内态下才稳)。

    `sess` 传 `ctx.g`(没会话就是 `None`, 那时 ①②③④ 记"没做成"); `bp_req`/`bp_land`/`bp_volt`
    传脚本里那三个模块级字面量 (文件, 行号)。三个断点占三个 FPB 槽(共 4 个)。
    """
    if n_rounds < 2:
        raise ValueError("n_rounds 至少要 2 —— ①要比多拍、②要比相邻两拍、③④要两拍")
    recs = []
    why = []

    def add(name, ok, detail, crit=None, obs=judge.SERIAL, falsify=None):
        recs.append(rec(name, ok, detail, crit=crit, obs=obs, falsify=falsify))

    print("\n== 7-4 计量芯 SPI 链路冒烟 (Communicate.c:1006 发请求 / :1039 落位) ==")

    # ---------- ⑤ SPI 送来的量在 698 上出得出(串口观测) ----------
    ud = read_oad_ud(ser, SPI_VOLT_OAD, wait=wait)
    uv = spi_volt_from_ud(ud)
    print("   698 GET %s(A相电压) → %s" % (
        SPI_VOLT_OAD,
        ("整数 %d (报文单位 0.1V ⇒ %.1f V)" % (uv, uv / 10.0)) if uv is not None
        else "没读到单标量应答, 原文 %s" % (bytes(ud or b"").hex(" ").upper() or "(静默)")))
    add("⑤ 698 读 %s(A相电压)读得到且非零" % SPI_VOLT_OAD,
        None if uv is None else (uv != 0),
        ("读到 %d(%.1f V)" % (uv, uv / 10.0)) if uv is not None
        else "没读到单标量应答(原文 %s)" % (bytes(ud or b"").hex(" ").upper() or "(静默)"),
        crit="⑤", falsify="链路断了 ⇒ 读不到或恒为 0")

    # ---------- ①②③④ 断点观测 ----------
    if sess is None:
        why.append("本次没有断点会话(没接 J-Link, 或用户指定只做黑盒) ⇒ 判①②③④ 没做成")
        print("   [白盒] %s" % why[-1])
        # ⚠ `falsify` 这几条**照样得写**: 缺了它, 判据会打出"认领它的证据答不出『什么固件会让它
        #   FAIL』"—— 那是对**没做成**的制度性抱怨, 与本次没接 J-Link 无关, 读的人会当成固件嫌疑。
        for _c, _t, _f in (("①", "请求恒为固定读", "请求随表内数据变 ⇒ 各拍字节不等"),
                           ("②", "轮询闭合", "只发不收 ⇒ 每次等 :1039 都超时(落位口一次都不命中)"),
                           ("③", "落位值随帧更新", "同一帧被反复落位 ⇒ 两拍时间域相等"),
                           ("④", "落位是直拷不自算", "落位时做过换算/取反 ⇒ 存值与来值对不上")):
            add("断[%s] %s" % (_c, _t), None, "半途中止: " + why[-1],
                crit=_c, obs=judge.DEBUG, falsify=_f)
        return recs, why, "ok"

    REQ_VARS, LAND_VARS, VOLT_VARS = spi_req_vars(), spi_land_vars(), spi_volt_vars()
    _rk = spi_req_keys()
    _lkk = spi_land_keys()
    _i, _dk, _vk, _gk = _lkk["index"], _lkk["dt"], _lkk["volt"], _lkk["gvolt"]
    no_req = sess.break_at_anchor(bp_req)
    no_land = sess.break_at_anchor(bp_land)
    no_volt = sess.break_at_anchor(bp_volt)
    print("   [白盒] 断[A] %s → bp %s (SPI 固定读请求口) / 断[B] %s → bp %s (数据落位口) / "
          "断[C] %s → bp %s (电压段落位之后)"
          % (_bptxt(bp_req), no_req, _bptxt(bp_land), no_land,
             _bptxt(bp_volt), no_volt))
    print("   [白盒] 三个口都是**自然停**、没有触发动作: 断[A] 每转一圈一次, 断[B] 每收住一帧一次, "
          "断[C] 紧跟断[B] 同一次落位之内。")
    reqs, lands, volts, closed = [], [], [], []
    n_hits0 = len(sess.other_hits)     # 本段开跑前的掠过数 —— 结束时按批号数出整场发:落
    try:
        for k in range(n_rounds):
            last_req = (k == n_rounds - 1)
            r = sess.wait_only(no_req, timeout=timeout, vars=REQ_VARS, drop=last_req)
            reqs.append(r)
            hit = r.get("hit")
            if hit is None:
                print("   [白盒] 断[A] 第 %d 拍没等到(%s 在 %.0fs 内没命中)"
                      % (k + 1, _bptxt(bp_req), timeout))
            else:
                b = vars_bytes(r.get("vars") or {}, _rk)
                print("   [白盒] 断[A] 第 %d 拍 停在 %s | g_SPIStep=%s | 要发出去的 29 字节 = %s"
                      % (k + 1, hit.where(), (r.get("vars") or {}).get("g_SPIStep"),
                         b.hex(" ").upper() if b else "(读不全)"))
            if last_req:
                break
            n0 = len(sess.other_hits)
            # 末一次落位口顺手撤掉(drop=True = 趁停住撤, 见 breakpoint.clear_breaks 的 ⚠)
            l = sess.wait_only(no_land, timeout=timeout, vars=LAND_VARS,
                               drop=(k == n_rounds - 2))
            lands.append(l)
            skipped = [h.bkptno for h in sess.other_hits[n0:]]
            closed.append(str(no_req) not in skipped)
            lh = l.get("hit")
            if lh is None:
                print("   [白盒] 断[B] 第 %d 拍没等到(%s 在 %.0fs 内没命中)"
                      % (k + 1, _bptxt(bp_land), timeout))
            else:
                v = l.get("vars") or {}
                print("   [白盒] 断[B] 第 %d 拍 停在 %s | STR1_Index=%s | 帧内时间域 %s | "
                      "帧内电压 %s | g_Volt[0] %s"
                      % (k + 1, lh.where(), v.get(_i),
                         (vars_bytes(v, _dk) or b"").hex(" ").upper() or "(读不全)",
                         (vars_bytes(v, _vk) or b"").hex(" ").upper() or "(读不全)",
                         (vars_bytes(v, _gk) or b"").hex(" ").upper() or "(读不全)"))
                print("      ↑ 停住那一刻 `Save_Caculator_Data` 还没跑, 故 `g_Volt` 装的是**上一帧**的结果")
            # 断[C]: 同一次落位之内、电压段**已经**落位之后 —— ④ 的来值与存值在这一刻同帧可比。
            vt = sess.wait_only(no_volt, timeout=timeout, vars=VOLT_VARS,
                                drop=(k == n_rounds - 2))
            volts.append(vt)
            vh = vt.get("hit")
            if vh is None:
                print("   [白盒] 断[C] 第 %d 拍没等到(%s 在 %.0fs 内没命中)"
                      % (k + 1, _bptxt(bp_volt), timeout))
            else:
                vv = vt.get("vars") or {}
                print("   [白盒] 断[C] 第 %d 拍 停在 %s | 帧内电压 %s | 刚落位的 g_Volt[0] %s"
                      % (k + 1, vh.where(),
                         (vars_bytes(vv, _vk) or b"").hex(" ").upper() or "(读不全)",
                         (vars_bytes(vv, _gk) or b"").hex(" ").upper() or "(读不全)"))
        # ---- ① 请求恒为固定读 ----
        miss = sum(1 for r in reqs if r.get("hit") is None)
        if miss:
            add("断[A] ① 请求恒为固定读", None,
                "%d/%d 拍没等到 %s 命中 ⇒ 本次没做成, 不据此判固件"
                % (miss, n_rounds, _bptxt(bp_req)),
                crit="①", obs=judge.DEBUG, falsify="请求随表内数据变 ⇒ 各拍字节不等")
        else:
            bs = [vars_bytes(r.get("vars") or {}, _rk) for r in reqs]
            if any(x is None for x in bs):
                add("断[A] ① 请求恒为固定读", None,
                    "有拍数的 29 字节没读全(断点停在 g_SPIMBuff 的空洞区间?) ⇒ 本次没做成",
                    crit="①", obs=judge.DEBUG, falsify="请求随表内数据变 ⇒ 各拍字节不等")
            else:
                same = all(x == bs[0] for x in bs)
                pre_ok = bs[0][0:4] == bytes([SPI_REQ_PRE] * 4)
                head_ok = bs[0][4] == SPI_FRAME_HEAD
                tail_ok = bs[0][-1] == SPI_FRAME_TAIL
                add("断[A] ① 请求恒为固定读", same and pre_ok and head_ok and tail_ok,
                    "%d 拍各 %d 字节逐字节%s相同; 首拍 %s; 前导 5A5A5A5A=%s [4]=0x68=%s [28]=0x16=%s"
                    % (n_rounds, SPI_REQ_LEN, "" if same else "**不**", bs[0].hex(" ").upper(),
                       "对" if pre_ok else "**不对**", "对" if head_ok else "**不对**",
                       "对" if tail_ok else "**不对**"),
                    crit="①", obs=judge.DEBUG,
                    falsify="请求随表内数据变 ⇒ 各拍字节不等")
        # ---- ② 轮询闭合 ----
        # 判的是"发出去要有帧回来并落位"(`got`)。**不判"每拍都无重发"** —— 那条把
        # `Communicate.c:1035`(尾字节非 0x16)与 `:1037`(CRC 不过)两支的"帧不合格就 g_SPIStep
        # 归零、重发、不落位"当成故障, 而它是源码里写明的**设计支路**, 链路上有一次抖动就会走到。
        # 重发照记不误(`closed` + 发:落计数), 记作**台面事实**摆在 detail 里, 不让它定固件的红绿。
        # ⚠ "只发不收"的固件仍然红: 每一次等 `:1039` 都会超时 ⇒ `got` 全 False。
        # ⚠ 这两个数是**下界**: `other_hits` 只收"等在别的断点上时掠过"的那些, 没人轮询队列的
        #   那一小窗里落下的命中收不到(见 12020 那条 ⚠)。所以它们只配当台面事实报, 不许当比值用。
        # ⚠ `bkptno` 从 MI 来的是**字符串**, 比它一律 `str(...)` 对 `str(...)`(同 12248 行)。
        n_send_hit = (sum(1 for h in sess.other_hits[n_hits0:] if str(h.bkptno) == str(no_req))
                      + sum(1 for r in reqs if r.get("hit") is not None))
        n_land_hit = (sum(1 for h in sess.other_hits[n_hits0:] if str(h.bkptno) == str(no_land))
                      + sum(1 for l in lands if l.get("hit") is not None))
        if not lands:
            add("断[A] ② 轮询闭合", None,
                "一拍都没等到落位口, 配不出间隔 ⇒ 本次没做成",
                crit="②", obs=judge.DEBUG,
                falsify="只发不收 ⇒ 每次等 :1039 都超时(落位口一次都不命中)")
        else:
            got = [(l.get("hit") is not None) for l in lands]
            add("断[A] ② 轮询闭合", all(got),
                "%d 个间隔: 每次请求之后等到落位口 = %s; "
                "整场 :1006 至少 %d 次 / :1039 至少 %d 次(下界, 见 sess.other_hits); "
                "每拍是否无重发 = %s(重发是固件对不合格帧的设计支路, 只记台面事实, 不参与判定)"
                % (len(got), got, n_send_hit, n_land_hit, closed),
                crit="②", obs=judge.DEBUG,
                falsify="只发不收 ⇒ 每次等 :1039 都超时(落位口一次都不命中)")

        # ---- ③ 落位值随帧更新 ----
        seen = [l for l in lands if l.get("hit") is not None]
        if len(seen) < 2:
            add("断[B] ③ 落位值随帧更新", None,
                "只等到 %d 拍落位口(要 2 拍) ⇒ 本次没做成, 不据此判固件" % len(seen),
                crit="③", obs=judge.DEBUG, falsify="同一帧被反复落位 ⇒ 两拍时间域相等")
        else:
            dts = [vars_bytes(l.get("vars") or {}, _dk) for l in seen[:2]]
            ks = [None if x is None else spi_dt_key(x) for x in dts]
            if any(x is None for x in dts) or any(k is None for k in ks):
                add("断[B] ③ 落位值随帧更新", None,
                    "帧内时间域这一次没读全、或不是 D_DateTimeS(%#04x + 7B 值) 形态(%s / %s) "
                    "⇒ 本次没做成, 不据此判固件"
                    % (SPI_DT_TYPE,
                       "没读全" if any(x is None for x in dts) else dts[0].hex(" ").upper(),
                       "没读全" if any(x is None for x in dts) else dts[1].hex(" ").upper()),
                    crit="③", obs=judge.DEBUG, falsify="同一帧被反复落位 ⇒ 两拍时间域相等")
            else:
                k1, k2 = ks
                add("断[B] ③ 落位值随帧更新", k2 > k1,
                    "第 1 拍帧内时间域 %s → (年,月,日,时,分,秒)=%s; 第 2 拍 %s → %s; 第二拍%s第一拍"
                    % (dts[0].hex(" ").upper(), k1, dts[1].hex(" ").upper(), k2,
                       "晚于" if k2 > k1 else "**不晚于**"),
                    crit="③", obs=judge.DEBUG,
                    falsify="同一帧被反复落位 ⇒ 两拍时间域相等")

        # ---- ④ 落位是直拷不自算(在**同一次落位之内**比 —— 不跨拍配) ----
        # 停点是电压段落位之后的 `TaskMetering.c:5035`: 帧内那 4 字节与刚落位的 `g_Volt[0]`
        # 是同一帧、同一刻, 不需要任何"这两拍是相邻轮"的假设(旧写法就栽在那个假设上, 见
        # `spi_volt_vars` 的 ⚠)。逐拍都比, 全对上才算。
        vs = [v for v in volts if v.get("hit") is not None]
        pairs = []
        for v in vs:
            vv = v.get("vars") or {}
            f, g = vars_bytes(vv, _vk), vars_bytes(vv, _gk)
            if f is not None and g is not None:
                pairs.append((f, g))
        if not pairs:
            add("断[C] ④ 落位是直拷不自算", None,
                "等到 %d 拍落位口, 但一拍也没等到电压段落位之后的那个停点(或读数没读全) "
                "⇒ 本次没做成, 不据此判固件" % len(vs),
                crit="④", obs=judge.DEBUG, falsify="落位时做过换算/取反 ⇒ 存值与来值对不上")
        else:
            # 帧内 4 字节大端 = 来值; `g_Volt[0]` 内存小端 = 存值(`RevCopy_Data` 一次倒序直拷)。
            cmp_ = [(int.from_bytes(f, "big"), int.from_bytes(g, "little")) for f, g in pairs]
            bad = [(f, g, a, b) for (f, g), (a, b) in zip(pairs, cmp_) if a != b]
            f0, g0 = pairs[0]
            a0, b0 = cmp_[0]
            add("断[C] ④ 落位是直拷不自算", not bad,
                "%d 拍各比一次(同一次落位之内): 第 1 拍帧内 +%d 起 4 字节 %s(大端 = %d) ⇔ "
                "刚落位的 g_Volt[0] 四字节 %s(小端 = %d); %s"
                % (len(pairs), SPI_VOLT_VAL_OFF, f0.hex(" ").upper(), a0,
                   g0.hex(" ").upper(), b0,
                   "每一拍都逐字节对上(直拷)" if not bad
                   else "**%d/%d 拍对不上**(如帧内 %s = %d ⇔ g_Volt %s = %d) —— 落位这一侧改过值, "
                        "不是直拷" % (len(bad), len(pairs), bad[0][0].hex(" ").upper(), bad[0][2],
                                      bad[0][1].hex(" ").upper(), bad[0][3])),
                crit="④", obs=judge.DEBUG,
                falsify="落位时做过换算/取反 ⇒ 存值与来值对不上")
        return recs, why, "ok"
    finally:
        # 撤断点只能在停住态(见 `breakpoint.clear_breaks` 的 ⚠: 核一跑起来 MI 命令就石沉大海)。
        # 正常路径上两处 `drop=True`/`_drop_bp` 已经撤干净, 这里是**兜底**: 某一拍没等到时断点
        # 还挂在槽里, 留着它自己会把核撂停, 其后每条串口帧整帧无应答 —— 表象与"表死机"一模一样。
        try:
            if sess.breakpoints():
                sess.ensure_stopped()
                sess.clear_breaks()
        finally:
            sess.ensure_running()


# ============ 7-1 管理芯帧进出与派发(485 口/蓝牙口各注入一次 698 帧 + 停解析口取证) ============
DISP_OAD = "40000200"      # 读表钟。选它不选别的: 读钟**不受 698 安全判定(Chk_SafeMode)管** ——
#                            读记录/读冻结在厂外态会一律 DAR=20 打回(见 `p698.handshake_clock` 的理由那一段)
DISP_PORT_485 = 1          # PT_485_M(枚举见 Config/MengXi/UserCfg.h:378)。本机的 CP210x 是 USB-485 桥
DISP_PORT_BLE = 4          # PT_BLE_M —— 蓝牙模组那条口(TaskBluet.c 全程用它调 Analyse_698Prot)
DISP_PROTO_698 = 1         # g_ProtSt 取值: 0=645帧, 1=698帧(Communicate.c:115 的声明注释)
DISP_PORT_NAME = {DISP_PORT_485: "PT_485_M", DISP_PORT_BLE: "PT_BLE_M"}


def _disp_port_name(port):
    """口号 → 人认得出的名字。**认不出就照原样报号**, 不猜(PORT_ENUM 里还有别的口)。"""
    return "%s=%s" % (DISP_PORT_NAME[port], port) if port in DISP_PORT_NAME else str(port)


# ⑤ 判过用的那条等式: g_ComLen == u16Len + 3。那个 3 是**计入 g_ComLen 的中断前导数**
# (= `C_PreNum`-1); 线上另有第 4 个前导 0xFE 由 `OpenTx_UART4`(CpuCfg.c:1047)直接写、不进 g_ComLen。
# 依据(源码逐字节): 发送空中断 `IRQHandler_IR`(Communicate.c:725-738)每中断写 1 字节 ——
# `g_PreNum < C_PreNum` 写 0xFE、否则写 `g_BLEMBuff[g_ComAdr++ % 256]`(:538 自增在前, :736 才取),
# 而 `:438` 已把 `g_PreNum` 清 0、`:439` 把 `g_ComAdr` 清 0 ⇒ 前 3 个中断写 FE, 第 4 个起从
# 缓冲区下标 0 逐字节写。所以线上 = 4 个 FE + 缓冲区那一整包(本帧 59 字节), 共 u16Len+4 字节。
DISP_TX_PRE = 3

DISP_BUF_DUMP = 64         # 停住时从 g_BLEMBuff 起逐字节读几个(本帧那条应答 59 字节, 留出余量)

# 模组外壳(19 字节头)里的地址域与保留域 —— 头布局见 `Application\TaskBluet.c` 的 `Blue_frameheadType`:
# `sta0(1) | u16len(2) | type(1) | comm(4) | addr(6, 偏移 8..13) | reserve(4, 偏移 14..17) | sta1(1)`。
# ⑧ 比的就是偏移 8..13 那 6 个字节; 地址对照试验(不认领判据那一条)改这 6 个字节之外,
# 还要用保留域第 1 个字节(`g_BLEMBuff[14]`)把校验和补平 —— 校验和是整头加数据的 8 位和
# (`Blue_645_checksum`), 地址一动校验和就跟着变, 模组据此丢帧。
DISP_ADDR_OFF = 8
DISP_ADDR_BYTES = 6
DISP_BUF_ADDR = tuple("g_BLEMBuff[%d]" % i
                      for i in range(DISP_ADDR_OFF, DISP_ADDR_OFF + DISP_ADDR_BYTES))
DISP_BUF_RESERVE = "g_BLEMBuff[14]"


def disp_inject_allow():
    """7-1 的注入白名单 —— **必须在会话构造时点名**(白名单精确匹配符号名, 事后补不了)。

    收两组: 地址对照试验要改的"回程目的地地址"那 6 个字节加补校验和用的保留域 1 个字节,
    以及各口共用的"推多长"`g_ComLen[PT_BLE_M]`。别的量一律不许从这个脚本写。
    """
    return DISP_BUF_ADDR + (DISP_BUF_RESERVE, "g_ComLen[%d]" % DISP_PORT_BLE)


# 7-1 九条判据各自的「什么样的固件会让它 FAIL」——**单点**, 脚本按 `crit` 取。
# ⚠ 缺了它, 判据会打出"认领它的证据答不出『什么固件会让它 FAIL』" —— 那是对没做成的制度性
#   抱怨, 读的人会当成固件嫌疑。改这里就得同步改三个 roundtrip 里的同名文字。
# ⚠ ①~④ 在 485 段与蓝牙去程段**各判一次**(判据名靠 `tag` 分得开), 两段取的是同一条 ——
#   这不是重复: 两段走的是两条口, 各有各的"没进解析链"。
DISP_FALSIFY = {
    "①": "帧没进 698 解析链 ⇒ 断点不命中; 被 645 分支截走也走不到 :283",
    "②": "端口索引串了 ⇒ port 不是发帧那条口",
    "③": "缓冲错位/截断 ⇒ 字节与发出帧不等",
    "④": "被 645 分支截走 ⇒ g_ProtSt[port] 读回 0",
    "⑤": "发送状态机按别的字节数推 ⇒ g_ComLen[port] 与 u16Len+%d 不等" % DISP_TX_PRE,
    "⑥": "发送中断没把这一包送完 ⇒ 放行后回读到 g_ComLen[%d] > 0" % DISP_PORT_BLE,
    "⑦": "固件把这一包组错了(长度域/校验和/对象号) ⇒ 校验不通过, 或回的不是被请求的 %s"
          % DISP_OAD,
    "⑧": "固件把回程地址写成常量 ⇒ 与模组用的那个地址不等",
    "⑨": "固件把回程地址写成常量 ⇒ 模组丢帧 ⇒ 主机侧一个字节也收不到",
}


def frame_dispatch_criteria():
    """7-1 的**预设判据条目**(测试前定死; 源 = ledger.md 7-1「观察与判据」的 J 列逐条)。

    ⚠ 这几条**逐字抄 ledger.md 7-1 的 J 列**: 改那边就得同步改这儿。"""
    return {
        "①": "帧到即停: 从 485 口或蓝牙口发一帧 698 读表钟(单播本表地址), DLT698Link.c:283 "
              "Analyse_698Prot 首条可执行语句 3 s 内命中",
        "②": "端口归属: ① 停住时读当前帧入参 port, == 发帧走的那条口(485 口跑=1/PT_485_M; "
              "蓝牙口跑=4/PT_BLE_M)",
        "③": "取数正确: ① 停住时读入参 pFrame 起 len(frame) 个字节, 逐字节等于脚本发出的那一帧",
        "④": "派发认对协议: 放行让这帧跑完, 再发同一条帧仍停 断[B], 读 g_ProtSt[port] "
              "== 1(1=698帧, 0=645帧)",
        "⑤": "回程推得不多不少: 蓝牙口那条应答在发送状态机里读 u16Len / g_ComLen[port] / "
              "g_ComAdr[port], 判过 = g_ComAdr[port]==0(从缓冲区偏移 0 起送)且 "
              "g_ComLen[port]==u16Len+3(线上 = 4 个前导 0xFE + 缓冲区那一整包: 1 个前导由 "
              "OpenTx_UART4 直接写、不进 g_ComLen, 3 个由发送空中断写、计入 g_ComLen)",
        "⑥": "管理芯把这一包推完了: 停 断[C] 那一停放行、主机侧等满 wait 秒之后回读 g_ComLen[port], "
              "判过 = 0；:426 刚把它置成 u16Len+3, 而递减它的地方只有发送空中断里的 "
              "INT_TxByte(Communicate.c:540), 回读 0 ⇒ 那一整包已逐字节写进 UART4 发送寄存器; "
              "停在 >0 ⇒ 这一包没被送完",
        "⑦": "回程那一包是合法 698 应答: 停 断[C] 那一停读到的 g_BLEMBuff 里那一整包, 按 698 规则"
              "逐字节校验通过(长度域 L / HCS / FCS / 帧尾 16), 且它的 APDU 是对被请求对象 "
              "40000200 的 GetResponse(85) —— ⑤ 只管推的字节数与起点, 判不出这一包内容对不对",
        "⑧": "回程目的地地址对: 停 断[B] 读模组发给管理芯那一帧的地址域(g_BLEMBuff[8..13]), 停 断[C] "
              "读回程那一帧同一个域, 判过 = 两者逐字节相等(应答要按模组用的那个地址抄回去, "
              "模组只认目的地是它的帧)",
        "⑨": "主机侧收到那条应答: 断[C] 那一停放行之后, 主机侧(蓝牙这条链路的收方向)收到 ≥1 字节 "
              "—— ⑧ 判的是回程地址写对没有, 这一条判的是它真走到了主机; 地址写错时模组丢掉它, "
              "主机侧一个字节也收不到",
    }


def send_clock_698(ser, frame, oad, wait=2.0):
    """7-1 的触发动作: 把这帧发出去并收应答(由 `with_trigger` 放在**后台线程**里跑)。

    单拎成具名函数是为了让 `with_trigger` 的日志能打出触发干了什么(它取 `fn.__name__`)。
    ⚠ 功能名里**不写协议词** —— "[698]" 那一格是帧自己的属性(`loglabel.Frame`), 再写一遍就是两条
      通道说同一件事(`_check_loghead` 判据③)。
    """
    return send_frame(ser, frame, wait=wait, tag="disp_698",
                      what="读表钟 OAD %s(7-1 帧进出/派发 注入帧)" % oad)


def frame_dispatch_roundtrip(ser, sess=None, bp_parse=None, oad=DISP_OAD, port=DISP_PORT_485,
                             tag="", relay=True, hit_wait=3.0, join_extra=8.0):
    """7-1 判过积木: 『管理芯侧帧进出 + 消息派发』(发一帧 698, 停解析口取证)。

    为什么这样切(每一步对应规格 ①~④ 的哪一条):
      · ① —— 停 `Application\\DLT698Link.c:283`(`Analyse_698Prot` **首条可执行语句**):
        帧到即停。**不是**签名行(打不上), **也不是** `:307` —— 那句 `CMD_Relay_Caculator_And_Master`
        只在 `g_AddrLog == 0x01` 保护下跑, 普通抄读帧根本不走它(照抄那个锚点会做出一个永不命中的断点)。
      · ② —— 停住时读当前帧的入参 `port`: 这帧归哪条口。
      · ③ —— 停住时读入参 `pFrame` 起 `len(frame)` 个字节: 解析口拿到的就是这一帧。
      · ④ —— 放行后**重发一帧**, 在第二次停住时读 `g_ProtSt[port]`: 停住这一刻本帧还没判完, 那个值
        是**上一帧**跑完时固件写下的 ⇒ 它答的正是"第 1 帧被当成什么派发的"。**先在第 1 帧停住时读一次
        基线** —— 否则"它本来就是 1"会被当成判过(这个洞 `spi_link_roundtrip` 的 ④ 也踩过)。

    ⚠ `relay=False` 时**不做 ④**, 只出 ①②③。④ 读的 `g_ProtSt[port]==1` 是 **485 口那条路**的派发
      标记 —— 它由 `Communicate.c:357` 写。**蓝牙口不走那一句**: 蓝牙帧由 `Blue_645_analysedata`
      剥壳(`:329`), 那条路在 `:338` 把 `g_ProtSt[port]` 写成 **0**。拿 ④ 去判蓝牙口, 判出来的是
      "这条通路不写那个标记", 不是"固件派发错了"。蓝牙口"有没有被当 698 认"由 `ble_reply_roundtrip`
      的 ⑤ 答(认不出 698 就走不到发送状态机)。

    `ser` 是**发帧走的那条通路**: 串口对象或 `meterlib.ble.BleLink` 都行(`BleLink` 与
    `portsel.RawCom` 同形, `send_frame` 一行都不用改) —— `port` 说的是它在管理芯里落哪条口,
    两者必须对上, 否则 ② 判的就是错的。
    `tag` 非空时接在每条判据名后面(同一子项要跑两遍时用它分开, 如 `"(蓝牙支)"`)。
    返回 `(recs, why, scope)`; **scope 半途中止时是 `None`**(结构信号, 别让脚本嗅文案)。

    `sess` 传 `ctx.g`(没会话就是 `None`, 那时 ①②③④ 记"没做成"); `bp_parse` 传脚本里那个模块级
    字面量 **(文件, 行号)** —— 走 `with_trigger` 的元组形态(它当场挂、命中时趁停住自撤), 不收
    预先挂好的 bpno: `:283` 是"每来一帧就停"的高频口, 跨次复用的 bpno 会把中间没人管的帧全撞哑。
    """
    if not _bpok(bp_parse):
        raise ValueError("bp_parse 要断点写法 (文件, 行号) / ('call'|'prev', 函数, 被调, n) / ('func', 函数名), 收到 %r" % (bp_parse,))
    recs, why = [], []
    frame = frame_698(build_read_apdu(0x03, oad))

    def add(name, ok, detail, crit=None, obs=judge.DEBUG, falsify=None):
        recs.append(rec(name, ok, detail, crit=crit, obs=obs, falsify=falsify))

    def nm(code, what):
        """判据名: 同一子项跑两遍(485 口 / 蓝牙口)时靠 `tag` 分得开。"""
        return "断[B] %s %s%s" % (code, what, tag)

    def _abort(reason):
        why.append(reason)
        print("   [白盒] %s" % reason)
        # ⚠ `falsify` 四条**照样得写**: 缺了它, 判据会打出"认领它的证据答不出『什么固件会让它 FAIL』"
        #   —— 那是对**没做成**的制度性抱怨, 读的人会当成固件嫌疑(`spi_link_roundtrip` 同律)。
        for _c, _t, _f in (("①", "帧到即停", "帧没进 698 解析链 ⇒ 断点不命中"),
                           ("②", "端口归属", "端口索引串了 ⇒ port 不是发帧那条口"),
                           ("③", "取数正确", "收帧缓冲错位/截断 ⇒ 字节与发出帧不等"),
                           ("④", "派发认对协议", "被 645 分支截走 ⇒ g_ProtSt[port] 读回 0")):
            add(nm(_c, _t), None, "半途中止: " + reason, crit=_c, falsify=_f)
        return recs, why, "ok"

    print("\n== 7-1 管理芯帧进出与派发 (注入 698 读表钟 / 停 DLT698Link.c:283%s) ==" % tag)
    print("   发帧走 %s 口, 注入帧 %d 字节: %s"
          % (_disp_port_name(port), len(frame), bytes(frame).hex(" ").upper()))

    # 停住时要读的量。**逐字节列 key 而不是读整数组** —— 与 `spi_link_roundtrip` 同一律:
    # `kwh_num` 吃的就是 `-data-evaluate-expression` 的单值文本, 整数组的文本形态是另一回事。
    pkeys = tuple("pFrame[%d]" % i for i in range(len(frame)))
    keys1 = ("port", "len", "g_ComAdr[port]", "g_ProtSt[port]") + pkeys
    port_exp = port                # 停住时读回的那个 `port` 与它比(别拿它当容器名, 见下)

    if sess is None:
        return _abort("本次没有断点会话(没接 J-Link) ⇒ 判①②③④ 没做成")

    try:
        # ---- 第 1 帧: ① 帧到即停 + ② 端口归属 + ③ 取数正确 ----
        r1 = sess.with_trigger(bp_parse, send_clock_698, ser, frame, oad, timeout=hit_wait,
                               _join=join_extra, _vars=keys1,
                               _pair_name="7-1 第 1 帧 698 读表钟")
        h1, v1 = r1.get("hit"), r1.get("vars") or {}
        if r1.get("error") is not None:
            print("   !! 触发那一侧抛了异常(帧可能没发出去): %s" % r1.get("error"))
        if h1 is None:
            return _abort("第 1 帧发出后 %.1fs 内没等到 %s 命中" % (hit_wait, _bptxt(bp_parse)))
        print("   [白盒] 断[B] 第 1 帧 停在 %s | port=%s | len=%s | g_ComAdr[port]=%s | "
              "g_ProtSt[port] 基线=%s"
              % (h1.where(), v1.get("port"), v1.get("len"),
                 v1.get("g_ComAdr[port]"), v1.get("g_ProtSt[port]")))
        got = vars_bytes(v1, pkeys)
        print("   [白盒] 解析口拿到的 %d 字节 = %s"
              % (len(frame), got.hex(" ").upper() if got else "(读不全)"))
        got_port = kwh_num(v1.get("port"))
        base = kwh_num(v1.get("g_ProtSt[port]"))

        # ---- ① 帧到即停 ----
        add(nm("①", "帧到即停"), True,
            "%s 在 %.1fs 内命中(停在 %s); 注入帧 %d 字节 %s"
            % (_bptxt(bp_parse), hit_wait, h1.where(), len(frame),
               bytes(frame).hex(" ").upper()),
            crit="①", falsify="帧没进 698 解析链 ⇒ 断点不命中; 被 645 分支截走也走不到 :283")

        # ---- ② 端口归属 ----
        if got_port is None:
            add(nm("②", "端口归属"), None, "入参 port 这一次没读到 ⇒ 本次没做成",
                crit="②", falsify="端口索引串了 ⇒ port 不是发帧那条口")
        else:
            ok2 = (got_port == port_exp)
            add(nm("②", "端口归属"), ok2,
                "port=%d; 发帧走的填的是 %s ⇒ %s"
                % (got_port, _disp_port_name(port_exp),
                   "对上" if ok2 else "**对不上** —— 这帧不是从那条口进来的"),
                crit="②", falsify="端口索引串了 ⇒ port 不是发帧那条口")

        # ---- ③ 取数正确 ----
        if got_port != port_exp:
            add(nm("③", "取数正确"), None, "② 没判过(这帧不归发帧那条口) ⇒ 本次不据此判固件",
                crit="③", falsify="缓冲错位/截断 ⇒ 字节与发出帧不等")
        elif got is None:
            add(nm("③", "取数正确"), None,
                "入参 pFrame 这一次没读全(断点停在 pFrame 的空洞区间?) ⇒ 本次没做成",
                crit="③", falsify="缓冲错位/截断 ⇒ 字节与发出帧不等")
        else:
            same = (got == bytes(frame))
            add(nm("③", "取数正确"), same,
                "解析口拿到的 %d 字节 %s; 发出的是 %s; 逐字节%s; g_ComAdr[port]=%s / len=%s"
                % (len(got), got.hex(" ").upper(), bytes(frame).hex(" ").upper(),
                   "全等" if same else "**不等**", v1.get("g_ComAdr[port]"), v1.get("len")),
                crit="③", falsify="缓冲错位/截断 ⇒ 字节与发出帧不等")

        # ---- ④ 派发认对协议(第 2 帧; 读的是第 1 帧跑完时写下的值) ----
        # ⚠ 蓝牙口不做这一条(见函数头): 那条路把 g_ProtSt 写成 0, 是写法不是派发结果。
        if not relay:
            return recs, why, "ok"
        r2 = sess.with_trigger(bp_parse, send_clock_698, ser, frame, oad, timeout=hit_wait,
                               _join=join_extra, _vars=("g_ProtSt[port]", "port"),
                               _pair_name="7-1 第 2 帧 698 读表钟%s" % tag)
        h2, v2 = r2.get("hit"), r2.get("vars") or {}
        if h2 is None:
            add(nm("④", "派发认对协议"), None,
                "第 2 帧发出后 %.1fs 内没等到 %s 命中 ⇒ 本次没做成(第 1 帧的派发结果无从读起)"
                % (hit_wait, _bptxt(bp_parse)),
                crit="④", falsify="被 645 分支截走 ⇒ g_ProtSt[port] 读回 0")
        else:
            proto = kwh_num(v2.get("g_ProtSt[port]"))
            add(nm("④", "派发认对协议"), proto == DISP_PROTO_698,
                "第 2 次停住时 g_ProtSt[%s]=%s(装的是第 1 帧跑完时固件写下的派发结果; "
                "第 1 次停住时基线=%s); 1=698帧 0=645帧 ⇒ %s"
                % (v2.get("port"), v2.get("g_ProtSt[port]"), base,
                   "固件把它当 698 派发了" if proto == DISP_PROTO_698
                   else "**读回 %s** —— 没被认作 698" % (proto,)),
                crit="④", falsify="被 645 分支截走 ⇒ g_ProtSt[port] 读回 0")
        return recs, why, "ok"
    finally:
        # 撤断点只能在停住态(见 `breakpoint.clear_breaks` 的 ⚠: 核一跑起来 MI 命令就石沉大海)。
        # `with_trigger` 收 (文件,行号) 时命中路径会趁停住自撤、没命中时自己补撤; 这里兜底
        # "已命中但后面某步抛了"那种残局 —— 留着断点它自己会把核撂停, 其后每条串口帧整帧无应答。
        try:
            if sess.breakpoints():
                sess.ensure_stopped()
                sess.clear_breaks()
        finally:
            sess.ensure_running()


def disp_reply_frame_ok(buf, ul, oad):
    """停住时读到的那一整包 → (ok, 说明): 里面那条 698 应答帧是不是合法、回的对不对。

    为什么要它: ⑤ 判的是"推的字节数与起点", 判不出这一包**内容** —— 固件把长度域或校验和算错、
    回了别的对象, ⑤⑥ 照样全绿。这一条把"推出去的是不是一条合法应答"钉住。

    怎么挑那条 698 帧: 缓冲区前面是模组外壳(`68 | u16len | … | 68`), 698 帧在后头。逐个 `68`
    按帧自己的长度域 L 切出 `L+2` 字节再校验(`validate_698` 要求整段长度恰好 == L+2, 而缓冲区里
    这一包**后面还有残留字节** —— 实测读 64 字节时最后 5 个是上一包的旧值, 直接拿整段去校会
    一律判"长度不符"), 取第一个通过的 —— **不依赖外壳字段的语义**(那是模组的事, 不是本子项要判的)。
    """
    pkt = bytes(buf[:ul]) if ul else bytes(buf)
    for j in range(len(pkt)):
        if pkt[j] != 0x68 or j + 4 > len(pkt):
            continue
        n = (pkt[j + 1] | (pkt[j + 2] << 8)) + 2          # 698 的 L 是不含首尾各 1 字节的长度
        if n < 17 or j + n > len(pkt):
            continue
        ok, msg = validate_698(pkt[j:j + n])
        if not ok:
            continue
        apdu = split_apdu(pkt[j:j + n])
        if len(apdu) < 7 or apdu[0] != 0x85:
            return False, ("缓冲区偏移 %d 起那一段 698 帧格式合法(%s), 但它不是 GetResponse: "
                           "APDU 头 = %s" % (j, msg, apdu[:8].hex(" ").upper() if apdu else "(切不出来)"))
        got = apdu[3:7].hex().upper()
        if got != oad.upper():
            return False, ("缓冲区偏移 %d 起那一段 698 帧格式合法, 但回的对象是 %s, "
                           "不是被请求的 %s" % (j, got, oad.upper()))
        return True, ("缓冲区偏移 %d 起那一段 = 合法 698 GetResponse(长度域/HCS/FCS/帧尾 全过), "
                      "回的对象 %s 与请求一致" % (j, got))
    return False, ("那一包里找不到一条能过 698 校验的帧(起 %d 字节: %s) ⇒ 组出来的不是合法 698 帧"
                   % (min(len(pkt), 32), pkt[:32].hex(" ").upper()))


def ble_peer_addr(sess, ser, frame, oad, bp_parse, tries, wait, hit_wait, join_extra):
    """停 断[B] 读**模组发给管理芯**那一帧的地址域(外壳偏移 8..13); 没取到返回 `None`。

    ⑧ 的对照物在模组那一侧 —— 回程那一帧里的地址对不对, 只有跟"模组用的是哪个"比才答得出。
    多来几次是为了把链路连起来(`BleLink` 空闲会掉线), 也是为了让停在别的口上的那几次重来。
    """
    bkeys = tuple("g_BLEMBuff[%d]" % i for i in range(DISP_BUF_DUMP))
    for k in range(max(1, int(tries))):
        r = sess.with_trigger(bp_parse, send_clock_698, ser, frame, oad, timeout=hit_wait,
                              _join=join_extra, _vars=("port",) + bkeys, wait=wait,
                              _pair_name="7-1 蓝牙去程 读模组发来的地址域")
        v, h = r.get("vars") or {}, r.get("hit")
        if h is None:
            print("   取地址第 %d 次: %.1fs 内没等到 %s 命中"
                  % (k + 1, hit_wait, _bptxt(bp_parse)))
            continue
        if kwh_num(v.get("port")) != DISP_PORT_BLE:
            print("   取地址第 %d 次: 停在 %s, 不是 %s"
                  % (k + 1, h.where(), _disp_port_name(DISP_PORT_BLE)))
            continue
        b0 = vars_bytes(v, bkeys)
        if b0 is None or len(b0) < DISP_ADDR_OFF + DISP_ADDR_BYTES:
            print("   取地址第 %d 次: g_BLEMBuff 没读出来" % (k + 1))
            continue
        addr = bytes(b0[DISP_ADDR_OFF:DISP_ADDR_OFF + DISP_ADDR_BYTES])
        print("   [白盒] 模组发给管理芯那一帧的地址域 = %s(停在 %s)"
              % (addr.hex(" ").upper(), h.where()))
        return addr
    print("   [白盒] 没取到模组用的那个地址 ⇒ ⑧ 本次没做成")
    return None


def ble_reply_roundtrip(ser, sess=None, bp_send=None, bp_parse=None, oad=DISP_OAD, tries=4,
                        wait=10.0, hit_wait=3.0, join_extra=12.0):
    """7-1 蓝牙那一半的**回程**: 管理芯要把应答推给模组, 推的字节对不对 / 主机收不收得到。

    为什么与 `frame_dispatch_roundtrip` 分开: 去程与回程在固件里是两处代码 ——
    去程由 `Blue_645_analysedata` 剥掉模组外壳后送进 698 解析器(`TaskBluet.c:300`),
    回程由 `Communicate.c` 的发送状态机把组好的应答推给模组。**去程通不等于回程通**,
    判的也不是同一条, 所以不塞进同一个函数。

    · ⑤ —— 停 `Application\\Communicate.c:442`(`ComFun[port].OpenTx(C_PreCode);` = 开始推给模组),
      停住时读 `u16Len`(固件刚组好的那一整包多少字节) / `g_ComLen[port]`(中断里要推多少字节) /
      `g_ComAdr[port]`(从缓冲区第几个字节起取; `STR1`=0 ⇒ 从头送)。
      判过 = 从偏移 0 起送 且 `g_ComLen[port] == u16Len + 3`(那个 3 = 计入 g_ComLen 的中断前导数,
      线上另有第 4 个前导由 `OpenTx_UART4` 直接写、不进 g_ComLen —— 依据见 `DISP_TX_PRE` 那段)。
      ⚠ 这两个数只答得出"推的字节数对不对" —— 字节由中断那一段按 `g_ComLen` 计数从缓冲区逐个取
        (不是一次 memcpy), 所以它们答不出"这一包推给模组之后会怎么样"。那一包的目的地写成了谁,
        由 ⑧ 判; 主机侧到底收不收得到, 由 ⑨ 判。
      ⚠ 锚点为什么是 `:442` 而**不是** `:426`(`g_ComLen[port] = u16Len+3;`): 断点停在那一行**还没执行**
        它, 读到的 `g_ComLen[port]` 是上一次发送留下的值(实测读到 0)。要读"推多长"就得停在赋值**之后**。
      ⚠ `:442` 是**所有口**共用的那一句(不像 `:426` 只在蓝牙支里), 所以命中之后要按 `port` 认一认;
        不是蓝牙口就再发一帧重等, 最多 `tries` 次。
      ⚠ 能停在这里本身也是一条事实: 这条帧被判过"需要应答"才会进发送状态机 —— 那正是蓝牙口
        "有没有被当 698 认"的回答(它不走 `g_ProtSt[port]=1` 那一句, 见 `frame_dispatch_roundtrip` 的 `relay=`)。
    · ⑥ —— 把应答**放行之后**再回读一次 `g_ComLen[port]`: 判过 = 0(那一整包已逐字节写进 UART4
      发送寄存器)。`:426` 刚把它置成 `u16Len+3`, 而递减它的地方**只有**发送空中断里的
      `INT_TxByte`(`Communicate.c:540`) ⇒ 回读到 0 = 中断链把 `u16Len` 字节全推完了(管理芯这一半
      没有毛病); 停在 > 0 = 这一包没被送完。
    · ⑦ —— ⑤ 那一次读到的 `g_BLEMBuff` 里那一整包, 按 698 规则逐字节校验(`p698.validate_698`:
      长度域 L / HCS / FCS / 帧尾 16), 再切出 APDU 认服务与对象: 判过 = 校验通过且是**对被请求
      OAD(`40000200`)的 GetResponse(85)**。它补的是 ⑤ 判不出的事 —— 字节数对不代表内容对:
      固件把帧组错(校验和、长度域、回错对象), ⑤⑥ 照样全绿。挑帧只看 `68` 起那一段能不能过校验,
      **不解释模组外壳**(外壳字段是模组的事)。
    · ⑧ —— 停 断[B] 读**模组发给管理芯**那一帧的地址域(`g_BLEMBuff[8..13]`), 与停 断[C] 读到的
      **回程**那一帧同一个域逐字节比: 判过 = 相等。回程那一帧的外壳地址是固件写死的
      (`Application\\TaskBluet.c:303-304` 把 `MacAddr` 填成 `01 00 00 00 00 00`), 不是模组用的那个
      地址 —— 模组据此认不出这条应答是给它的。⑤⑥⑦ 全绿也照样发生: 那三条判的都是管理芯自己这一侧
      (推的字节数 / 推完没有 / 这一包内容), 判不出它的目的地写成了谁。
    · ⑨ —— 断[C] 那一停放行之后, 主机侧收到 ≥1 字节。它是 ⑧ 那条地址在链路那一段的后果:
      地址写错 ⇒ 模组丢帧 ⇒ 主机侧一个字节也收不到。反证(把地址改对主机就收得到)由
      `ble_reply_addr_control` 的受控注入给出 —— 那一条动的是 RAM 不是固件, 所以只记日志、
      **不认领判据**。
      ⚠ 读法: `read_vars` 要求**停住态**(核跑着时 gdb 拒答), 所以 ⑥ 自己叫停一次、读完立刻放行;
        停的是"这一包早就推完"之后的稳态 —— `wait` 秒远大于 63 字节在 115200 上的发送时间。
      ⚠ `g_ComAdr[port]` 一并记进明细当佐证, **但它不作判据**: 收帧(模组发给管理芯)也推着它走,
        它离开 0 不等于"从缓冲区中间起送"(那一件事由 ⑤ 在发送前那一刻判)。

    返回 `(recs, why, scope)`; **scope 半途中止时是 `None`**(结构信号, 别让脚本嗅文案)。

    `bp_send` 与 `bp_parse` 同一形状(模块级字面量 `(文件, 行号)`) —— 走 `with_trigger` 的
    元组形态当场挂、命中时趁停住自撤。
    """
    if not _bpok(bp_send):
        raise ValueError("bp_send 要断点写法 (文件, 行号) / ('call'|'prev', 函数, 被调, n) / ('func', 函数名), 收到 %r" % (bp_send,))
    if bp_parse is not None and not _bpok(bp_parse):
        raise ValueError("bp_parse 要断点写法 (文件, 行号) / ('call'|'prev', 函数, 被调, n) / ('func', 函数名), 收到 %r" % (bp_parse,))
    recs, why = [], []
    frame = frame_698(build_read_apdu(0x03, oad))

    def add(name, ok, detail, crit=None, obs=judge.DEBUG, falsify=None):
        recs.append(rec(name, ok, detail, crit=crit, obs=obs, falsify=falsify))

    def _abort(reason):
        why.append(reason)
        print("   [白盒] %s" % reason)
        for _c, _t, _f, _o, _w in (
                ("⑤", "回程推得不多不少",
                 "发送状态机按别的长度推 ⇒ g_ComLen[port] 与 u16Len+%d 不等" % DISP_TX_PRE,
                 judge.DEBUG, "断[B]"),
                ("⑥", "管理芯把这一包推完了",
                 "发送中断没把这一包送完 ⇒ 放行后回读到 g_ComLen[%d] > 0" % DISP_PORT_BLE,
                 judge.DEBUG, "断[C]"),
                ("⑦", "回程那一包是合法 698 应答",
                 "固件把这一包组错了(长度域/校验和/对象号) ⇒ 校验不通过, "
                 "或回的不是被请求的 %s" % oad, judge.DEBUG, "断[C]"),
                ("⑧", "回程目的地地址对",
                 "固件把回程地址写成常量 ⇒ 与模组用的那个地址不等", judge.DEBUG, "断[C]"),
                ("⑨", "主机侧收到那条应答",
                 "固件把回程地址写成常量 ⇒ 模组丢帧 ⇒ 主机侧一个字节也收不到",
                 judge.SERIAL, "串口")):
            add("%s %s %s" % (_w, _c, _t), None, "半途中止: " + reason, crit=_c, obs=_o,
                falsify=_f)
        return recs, why, "ok"

    print("\n== 7-1 蓝牙回程 (停 Communicate.c:442 看推给模组的长度 + 主机侧轮询) ==")
    print("   注入帧 %d 字节: %s" % (len(frame), bytes(frame).hex(" ").upper()))

    # 停住时要读的量。缓冲区那一段**必须走 `_vars` 逐字节读**(读发生在核停着的那一刻):
    # 2026-09-21 实踩 —— 原先在 `with_trigger` 返回后用 `sess._read_mem` 读, 那时核已经被放行了,
    # gdb 回 `Cannot execute this command while the target is running`, 缓冲区一个字节都没读到。
    bkeys = tuple("g_BLEMBuff[%d]" % i for i in range(DISP_BUF_DUMP))
    keys = ("port", "u16Len", "g_ComLen[port]", "g_ComAdr[port]") + bkeys
    if sess is None:
        return _abort("本次没有断点会话(没接 J-Link) ⇒ ⑤⑥⑦⑧⑨ 没做成")

    try:
        # ---- 先取"模组用的是哪个地址"(⑧ 的对照物; 见 `ble_peer_addr`) ----
        peer_addr = (ble_peer_addr(sess, ser, frame, oad, bp_parse, tries, wait, hit_wait,
                                    join_extra) if bp_parse is not None else None)

        hit_v, rx, tries_done = None, b"", 0
        for k in range(max(1, int(tries))):
            tries_done = k + 1
            r = sess.with_trigger(bp_send, send_clock_698, ser, frame, oad, timeout=hit_wait,
                                  _join=join_extra, _vars=keys, wait=wait,
                                  _pair_name="7-1 蓝牙回程 应答推给模组")
            if r.get("error") is not None:
                print("   !! 触发那一侧抛了异常(帧可能没发出去): %s" % r.get("error"))
            rx += r.get("result") or b""
            h, v = r.get("hit"), r.get("vars") or {}
            if h is None:
                print("   第 %d 次: %.1fs 内没等到 %s 命中"
                      % (k + 1, hit_wait, _bptxt(bp_send)))
                continue
            p = kwh_num(v.get("port"))
            print("   第 %d 次: 停在 %s | port=%s | u16Len=%s | g_ComLen[port]=%s | g_ComAdr[port]=%s"
                  % (k + 1, h.where(), v.get("port"), v.get("u16Len"),
                     v.get("g_ComLen[port]"), v.get("g_ComAdr[port]")))
            if p == DISP_PORT_BLE:
                # ⚠ 停在蓝牙口还不算数 —— `:442` 是**所有口共用**的那一句, 蓝牙口上还会推别的帧
                #   (实测撞上过一次 `u16Len=22` 的连接类帧, 缓冲区里根本没有那条 698 应答)。
                #   判据读的是"刚组好的那条应答", 所以优先取缓冲区里确实躺着它的那一停;
                #   一次都没撞上时**仍用最后一次** —— 否则"固件把这一包组坏了"会被记成"没做成",
                #   ⑦ 就永远判不出 FAIL(把 FAIL 降级成"没做成"是最坏的一种改法)。
                _got, _ul = vars_bytes(v, bkeys), kwh_num(v.get("u16Len"))
                if hit_v is None:
                    hit_v = v
                if _got is not None and disp_reply_frame_ok(_got, _ul, oad)[0]:
                    hit_v = v
                    break
                print("   第 %d 次: 停在蓝牙口, 但缓冲区里不是刚组好的那条 698 应答 ⇒ 再发一帧重等"
                      % (k + 1))
                continue
            print("   第 %d 次: 停的不是 %s, 再发一帧重等"
                  % (k + 1, _disp_port_name(DISP_PORT_BLE)))
        # ---- ⑤ 回程推得不多不少(断点那一半) ----
        if hit_v is None:
            add("断[B] ⑤ 回程推得不多不少", None,
                "%d 次触发里没有一次停在 %s 的蓝牙口上 ⇒ 本次没做成(没等到的原因不止一种: "
                "这帧没让管理芯产生应答 / 应答没走到发送状态机 / 停到的都是别的口)"
                % (tries_done, _bptxt(bp_send)),
                crit="⑤", falsify="发送状态机按别的长度推 ⇒ g_ComLen[port] 与 u16Len 不等")
            add("断[C] ⑦ 回程那一包是合法 698 应答", None,
                "⑤ 那一次没停在蓝牙口 ⇒ 没有可校验的那一包, 本次没做成",
                crit="⑦", falsify="固件把这一包组错了(长度域/校验和/对象号) ⇒ 校验不通过, "
                                  "或回的不是被请求的 %s" % oad)
            add("断[C] ⑧ 回程目的地地址对", None,
                "⑤ 那一次没停在蓝牙口 ⇒ 没有可读的那一停, 本次没做成", crit="⑧",
                falsify="固件把回程地址写成常量 ⇒ 与模组用的那个地址不等")
        else:
            ul, ln, ad = (kwh_num(hit_v.get("u16Len")), kwh_num(hit_v.get("g_ComLen[port]")),
                          kwh_num(hit_v.get("g_ComAdr[port]")))
            # 缓冲区这一段是**佐证**: 读到了就照实记, 读不到不影响判据本身。
            got_buf = vars_bytes(hit_v, bkeys)
            tail = ("; g_BLEMBuff 起 %d 字节 = %s" % (len(got_buf), got_buf.hex(" ").upper())
                    if got_buf else "; g_BLEMBuff 这一次没读出来")
            want = (ul + DISP_TX_PRE) if ul is not None else None
            ok5 = (ad == 0 and want is not None and ln == want)
            add("断[B] ⑤ 回程推得不多不少", ok5,
                "停 %s 蓝牙口那一停: u16Len=%s(固件组好的那一整包多长) / g_ComLen[port]=%s"
                "(中断里要推的字节数) / g_ComAdr[port]=%s(从缓冲区第几个字节起取, STR1=0 即从头)%s ⇒ %s"
                % (_bptxt(bp_send), hit_v.get("u16Len"), hit_v.get("g_ComLen[port]"),
                   hit_v.get("g_ComAdr[port]"), tail,
                   "从缓冲区偏移 0 起送、字节数 = 整包 + %d 个中断前导"
                   "(线上 = 4 个前导 0xFE + 那一整包, 共 %s 字节)" % (DISP_TX_PRE, want) if ok5
                   else "**字节数与设计不符**: 实际 %s, 应为 整包 + %d = %s"
                        % (ln, DISP_TX_PRE, want)),
                crit="⑤",
                falsify="发送状态机按别的字节数推 ⇒ g_ComLen[port] 与 u16Len+%d 不等" % DISP_TX_PRE)

            # ---- ⑦ 这一包的内容对不对(⑤ 只管长度与起点; 见 `disp_reply_frame_ok`) ----
            if not got_buf:
                ok7, why7 = None, "g_BLEMBuff 这一次没读出来 ⇒ 没有可校验的那一包, 本次没做成"
            else:
                ok7, why7 = disp_reply_frame_ok(got_buf, ul, oad)
            add("断[C] ⑦ 回程那一包是合法 698 应答", ok7, why7, crit="⑦",
                falsify="固件把这一包组错了(长度域/校验和/对象号) ⇒ 校验不通过, 或回的不是被请求的 %s"
                        % oad)

            # ---- ⑧ 回程那一帧的目的地地址(外壳偏移 8..13)对不对 ----
            rep_addr = (bytes(got_buf[DISP_ADDR_OFF:DISP_ADDR_OFF + DISP_ADDR_BYTES])
                        if got_buf is not None
                        and len(got_buf) >= DISP_ADDR_OFF + DISP_ADDR_BYTES else None)
            _f8 = "固件把回程地址写成常量 ⇒ 与模组用的那个地址不等"
            if peer_addr is None:
                add("断[C] ⑧ 回程目的地地址对", None,
                    "没取到模组用的那个地址(去程那一停没成) ⇒ 没有对照物, 本次没做成",
                    crit="⑧", falsify=_f8)
            elif rep_addr is None:
                add("断[C] ⑧ 回程目的地地址对", None,
                    "回程那一帧的外壳地址域(g_BLEMBuff 偏移 %d 起)这一次没读出来 ⇒ 本次没做成"
                    % DISP_ADDR_OFF, crit="⑧", falsify=_f8)
            else:
                same8 = (rep_addr == peer_addr)
                add("断[C] ⑧ 回程目的地地址对", same8,
                    "模组发给管理芯那一帧的地址域 = %s; 回程那一帧(g_BLEMBuff 偏移 %d 起)= %s ⇒ %s"
                    % (peer_addr.hex(" ").upper(), DISP_ADDR_OFF, rep_addr.hex(" ").upper(),
                       "一致, 模组认得出这条应答是给它的" if same8 else
                       "**不一致** —— 该把模组用的那个地址抄回去, 实际写的是别的"
                       "(`Application\\TaskBluet.c:303-304` 的透传回程把地址填成了常量)"),
                    crit="⑧", falsify=_f8)

        # ---- ⑨ 主机侧收到那条应答(串口观测: 看的是对外行为) ----
        print("   [台面] 主机侧 %d 次触发各等 %s 秒共收到 %d 字节"
              % (tries_done, wait, len(rx)))
        add("串口 ⑨ 主机侧收到那条应答", len(rx) > 0,
            "%d 次触发各等 %s 秒, 主机侧共收到 %d 字节: %s ⇒ %s"
            % (tries_done, wait, len(rx), rx.hex(" ").upper() if rx else "(一个字节都没有)",
               "应答走到了主机" if rx else
               "**一个字节都没有** —— 回程那一帧的目的地不是模组用的那个地址时, 模组丢掉它"
               "(地址对照试验见 `ble_reply_addr_control`: 把地址改成模组用的那个, 主机侧就收得到)"),
            crit="⑨", obs=judge.SERIAL,
            falsify="固件把回程地址写成常量 ⇒ 模组丢帧 ⇒ 主机侧一个字节也收不到")

        # ---- ⑥ 管理芯把这一包推完了没有(发送之后回读; 见本函数 docstring 那一条) ----
        ok6, why6 = None, ""
        cl_expr = "g_ComLen[%d]" % DISP_PORT_BLE
        ad_expr = "g_ComAdr[%d]" % DISP_PORT_BLE
        if hit_v is None:
            why6 = "⑤ 那一次没停在蓝牙口 ⇒ 没有可绑定的那一次发送, 本次没做成"
        else:
            try:
                sess.ensure_stopped()                 # 核跑着时读不了表达式(见 docstring 的 ⚠)
                try:
                    post = sess.read_vars((cl_expr, ad_expr))
                finally:
                    sess.ensure_running()
                cl, ad = kwh_num(post.get(cl_expr)), kwh_num(post.get(ad_expr))
                ok6 = (cl == 0)
                why6 = ("发送放行、主机侧等满 %s 秒之后回读: %s=%s(推完了应是 0) / %s=%s(佐证, 不作判据)"
                        % (wait, cl_expr, cl, ad_expr, ad))
                why6 += (" ⇒ 管理芯把这一包逐字节推给了模组" if ok6 else
                         " ⇒ **这一包没被送完**(发送中断链在 :442 之后停住了)")
            except Exception as e:                    # noqa: BLE001 —— 读不成就是"没做成", 不判 FAIL
                ok6 = None
                why6 = "回读没做成(停/读那一步抛了): %s" % str(e).strip().splitlines()[0]
        add("断[C] ⑥ 管理芯把这一包推完了", ok6, why6, crit="⑥",
            falsify="发送中断没把这一包送完 ⇒ 放行后回读到 %s > 0" % cl_expr)
        return recs, why, "ok"
    finally:
        # 同 `frame_dispatch_roundtrip`: 撤断点只能在停住态, 兜底"命中了但后面某步抛了"的残局。
        try:
            if sess.breakpoints():
                sess.ensure_stopped()
                sess.clear_breaks()
        finally:
            sess.ensure_running()


def ble_reply_addr_control(ser, sess=None, bp_parse=None, bp_send=None, oad=DISP_OAD,
                           tries=3, wait=8.0, hit_wait=3.0, join_extra=12.0):
    """7-1 的**受控对照**: 『主机收不到, 是不是因为回程那一帧的目的地地址写错了』。

    **它不认领判据**(动的是 RAM 不是固件, 答不出"什么固件会让它 FAIL"): 它答的是 ⑨ 那条 FAIL
    该记在谁头上 ——
      · 把地址改成模组用的那个之后主机收到字节 ⇒ 模组与空口这一段是好的, 病就在固件写死的地址;
      · 改了还是收不到 ⇒ 病不止地址那一条。
    三次停:① 停 断[B] 取模组用的地址(`ble_peer_addr`); ② 停 断[C] 读回程原文(这一停同时是
    "不改时主机收多少"的对照); ③ 停 断[C] 把地址写成①取到的那个, 再用保留域第 1 个字节
    (`g_BLEMBuff[14]`)把校验和补平 —— 校验和是整头加数据的 8 位和(`Blue_645_checksum`),
    地址一动它就变, 模组据此丢帧; 放行后看主机收不收得到。

    ⚠ ② 与 ③ 必须停在**同一条**回程帧上(补偿量是照 ② 读到的那几个字节算的): `:442` 所有口
      共用, 蓝牙口上还会推别的帧, 撞错帧就会把补偿量算错、模组丢帧、主机收 0 字节 —— 一次
      假阴性。所以每一停都先认帧(`u16Len` 截断后内嵌一条合法 698 应答), 不认就重发一帧重等,
      重试 `tries` 次都认不出就记"这一条没做成"。

    做法上走 `sess.with_inject`(它管"停 → 写 → 放行"的时机; 注入只在停住态成立, 不能塞进
    `with_trigger`)。⚠ 注入的是 `g_BLEMBuff` 那 7 个字节, 白名单必须在会话构造时点名
      (`disp_inject_allow()`), 收尾由 breakpoint 自动写回原值。
    返回 `(recs, why, scope)`; **scope 半途中止时是 `None`**(结构信号, 别让脚本嗅文案)。
    """
    for _n, _bp in (("bp_parse", bp_parse), ("bp_send", bp_send)):
        if not _bpok(_bp):
            raise ValueError("%s 不是认可的断点字面量(四种写法见 `_bpok`), 收到 %r" % (_n, _bp))
    recs, why = [], []
    frame = frame_698(build_read_apdu(0x03, oad))
    bkeys = tuple("g_BLEMBuff[%d]" % i for i in range(DISP_BUF_DUMP))
    keys = ("port", "u16Len", "g_ComLen[port]") + bkeys

    def add(name, ok, detail, crit=None, obs=judge.DEBUG, falsify=None):
        recs.append(rec(name, ok, detail, crit=crit, obs=obs, falsify=falsify))

    def _abort(reason):
        why.append(reason)
        print("   [白盒] %s" % reason)
        add("串口 回程地址对照试验(台面, 不认领判据)", None, "半途中止: " + reason)
        return recs, why, "ok"

    def _one(assigns):
        """停 bp_send 一次: 可选注入, 放行后把主机侧收到的字节交回来。"""
        res = sess.with_inject(bp_send, assigns, at_vars=keys,
                               trigger=send_clock_698, trigger_args=(ser, frame, oad, wait),
                               timeout=hit_wait, join=join_extra)
        if res.get("trigger_error") is not None:
            print("   !! 触发那一侧抛了异常(帧可能没发出去): %s" % res.get("trigger_error"))
        av = res.get("at_vals") or {}
        return av, vars_bytes(av, bkeys), res.get("result") or b""

    print("\n== 7-1 蓝牙回程地址对照试验 (停 Communicate.c:442 改回程地址再放行 + 主机侧轮询) ==")
    if sess is None:
        return _abort("本次没有断点会话(没接 J-Link) ⇒ 这一条没做成")

    try:
        peer = ble_peer_addr(sess, ser, frame, oad, bp_parse, tries, wait, hit_wait, join_extra)
        if peer is None:
            return _abort("① 没取到模组用的那个地址 ⇒ 没有可对的东西, 这一条没做成")

        # ⚠ ② 与 ③ 必须是**同一条**回程帧: `:442` 是所有口共用的一句, 蓝牙口上还会推别的帧
        #   (实测 ② 撞上过一条 `u16Len=22` 的帧, 于是补偿量是照那条帧算的 —— 写进真应答里
        #   校验和就错了, 模组丢帧, 主机收到 0 字节, 整场对照试验变成一次假阴性)。
        #   所以停下来之后先认帧(`u16Len` 截断 + 内嵌 698 校验), 不认就再发一帧重等。
        got_plain = got_fixed = None
        for k in range(max(1, int(tries))):
            av0, buf0, rx0 = _one([])
            cur = None
            if buf0 is None or not disp_reply_frame_ok(buf0, kwh_num(av0.get("u16Len")), oad)[0]:
                print("   第 %d 次: ② 那一停不是刚组好的那条 698 应答(port=%s, u16Len=%s) ⇒ 重来"
                      % (k + 1, av0.get("port"), av0.get("u16Len")))
                continue
            cur, res0 = (buf0[DISP_ADDR_OFF:DISP_ADDR_OFF + DISP_ADDR_BYTES], buf0[14])
            delta = sum(peer) - sum(cur)
            assigns = ([(DISP_BUF_ADDR[i], peer[i]) for i in range(DISP_ADDR_BYTES)]
                       + [(DISP_BUF_RESERVE, (res0 - delta) & 0xFF)])
            print("   ① 模组用的地址 = %s; ② 回程原文的地址 = %s(保留域 %02X) ⇒ 主机收到 %d 字节"
                  % (peer.hex(" ").upper(), cur.hex(" ").upper(), res0, len(rx0)))
            print("   ③ 把地址改成 %s(用保留域补 %d 保持校验和不变)再放行"
                  % (peer.hex(" ").upper(), (res0 - delta) & 0xFF))

            av1, buf1, rx1 = _one(assigns)
            if buf1 is None or not disp_reply_frame_ok(buf1, kwh_num(av1.get("u16Len")), oad)[0]:
                print("   第 %d 次: ③ 注入那一停不是那条 698 应答(port=%s, u16Len=%s) ⇒ 重来"
                      % (k + 1, av1.get("port"), av1.get("u16Len")))
                continue
            got_plain, got_fixed = rx0, rx1
            break
        if got_fixed is None:
            return _abort("②③ 几次都没停在「刚组好的那条 698 应答」上 ⇒ 这一条没做成")
        print("   ③ 停在 %s | 主机收到 %d 字节 %s"
              % (_bptxt(bp_send), len(got_fixed), got_fixed.hex(" ").upper() if got_fixed else ""))
        add("串口 回程地址对照试验(台面, 不认领判据)", None,
            "同一台表、同一条链路, 只改回程那一帧的目的地地址: 原文 %s ⇒ 主机收到 %d 字节; "
            "改成模组用的那个 %s ⇒ 主机收到 %d 字节 —— %s"
            % (cur.hex(" ").upper(), len(got_plain), peer.hex(" ").upper(), len(got_fixed),
               "模组与空口这一段是好的, 主机收到与否只取决于那个地址写成了谁 "
               "(⇒ ⑨ 那条 FAIL 记在固件写死的地址上)" if got_fixed else
               "**改了地址主机还是收不到** ⇒ 病不止地址那一条(模组侧还有别的原因)"),
            crit=None, obs=judge.SERIAL)
        return recs, why, "ok"
    finally:
        # 同 `ble_reply_roundtrip`: 撤断点只能在停住态。
        try:
            if sess.breakpoints():
                sess.ensure_stopped()
                sess.clear_breaks()
        finally:
            sess.ensure_running()


# ============ 8-7 通信唤醒点亮背光(蓝牙口发 698 读表钟 + 停通信唤醒投递点) ============
# 只做**蓝牙口**那一条路: 投唤醒消息那一句写在 `if (port == PT_BLE_M)` 里(DLT698Link.c:321-324),
# 485 口走到那里不投 —— 拿 485 口跑, 判出来的是"这条通路不投唤醒消息", 不是"背光会不会亮"。
LAMP_TICK = "u32LampTicker"       # TaskDisplay.c:73 static __no_init INT32U —— 背光精确计时
DISP_STAT = "g_DispStatus"        # TaskDisplay.c:50 static __no_init ST_DISP —— 显示状态
DISP_SLCT = "g_DispPara[SlctT]"   # TaskDisplay.c:60 的 static 数组; 下标用 TaskDisplay.h 的枚举名,
#                                   不写字面量(枚举序不是身份, 见 CLAUDE.md 第 22 条)
C_SYS_TICK = 64                   # Config\MengXi\UserCfg.h:125 —— 背光计时中断 64 Hz
DISP_ST_STOP = "ST_StopDisp"      # 息屏态(TaskDisplay.c:1151 SwTo_StopMode 写的那个值)
LAMP_BASE_KEYS = (LAMP_TICK, DISP_STAT, DISP_SLCT)   # 发帧前那一次基线回读的三个量


def lamp_wake_criteria():
    """8-7 的**预设判据条目**(测试前定死; 源 = ledger.md 8-7「观察与判据」的 J 列逐条)。

    ⚠ 这五条**逐字抄 ledger.md 8-7 的 J 列**: 改那边就得同步改这儿。

    为什么不判"背光那个 GPIO": 背光由 `TaskSystem.c:413` 每轮按 `Get_LampTimer()` 开/关,
    而 `Get_LampTimer()` 为真有两路 —— `u32LampTicker != 0` **或** `g_DispStatus == ST_FullDisp`。
    上电那一段本来就是全屏、灯本来就在亮, 所以"灯亮着"答不出"是不是这一帧点亮的";
    `u32LampTicker` 只由一个赋值点、一个递减点管, 它变了才归得到这一帧头上。
    """
    return {
        "①": "通信唤醒的投递点被执行: 蓝牙口发一帧 698 读表钟(单播本表地址), 3 s 内停在 "
              "Application\\DLT698Link.c:324(Analyse_698Prot 里蓝牙口那一支的 "
              "Post_Message(ID_TaskDisplay, MSG_DispWake)), 且停住时读回入参 port == 4(PT_BLE_M)",
        "②": "背光计时器被置上: 发帧前基线 u32LampTicker == 0, ① 那一停放行后等 1.5 s 叫停回读 "
              "u32LampTicker != 0",
        "③": "亮显时长等于该表参数: ② 那次回读 u32LampTicker == g_DispPara[SlctT] × C_SysTick"
              "(C_SysTick=64,Config\\MengXi\\UserCfg.h:125;SlctT 默认 10 ⇒ 640)",
        "④": "到时熄灭: 从 ① 那一停放行起等满\"③ 判出的时长 + 1 s\"叫停回读 u32LampTicker == 0, "
              "且 ② 那次读到过非 0",
        "⑤": "液晶从息屏态被唤醒: 发帧前基线 g_DispStatus == ST_StopDisp, 发帧后回读 != ST_StopDisp",
    }


def _send_clock_698_lamp(ser, frame, oad, wait=2.0):
    """8-7 的触发动作: 把这帧发出去并收应答(由 `with_trigger` 放在**后台线程**里跑)。

    ⚠ 功能名里**不写协议词** —— "[698]" 那一格是帧自己的属性(`loglabel.Frame`), 再写一遍
      就是两条通道说同一件事(`_check_loghead` 判据③)。
    """
    return send_frame(ser, frame, wait=wait, tag="lamp_698",
                      what="读表钟 OAD %s(8-7 通信唤醒触发帧)" % oad)


def _lamp_stop_read(sess, keys):
    """叫停 → 读一组表达式 → 放行。读不成给 `None`(**不抛** —— "没读到"与"读到 0"必须分得开)。

    为什么非得叫停: `read_vars` 走 `-data-evaluate-expression`, 核跑着时读不了(DWARF 表达式求值
    要逐条读目标内存)。读完立刻放行 —— 停着超过 ~8 s 会被 IWDT 复位。
    """
    try:
        sess.ensure_stopped()
        try:
            return sess.read_vars(tuple(keys))
        finally:
            sess.ensure_running()
    except Exception:                       # noqa: BLE001 —— 读不成记"没做成", 不许当 FAIL
        return None


# ---- 8-7 的九步: 一步一个积木, 脚本正文一行一步(顺序即步骤, 见 project/tests/_test_8_7_lamp_wake.py) ----
# 与 4-6 同一形状: 判据文字 / crit / falsify 由脚本给(那是本子项的判过汇总, 库不替它起名);
# 本段每个动词只做一件事 —— 一次读 / 一次发(连它那一次命中) / 一次比, 各自打印读过什么、比了什么。
# 本子项**全部证据都是断点观测**(obs=DEBUG): 帧虽是发出去的, 但"这帧走没走到该唤醒的那一句 /
# 背光计时器有没有被置上 / 亮了多久"串口自己答不出。

LIGHT_WAIT = 1.5        # ① 命中放行后先等它一会儿再回读计时器(那一下是 TurnOnLampLed 之后的几拍)
OFF_EXTRA = 1.0         # ④ 在"判出的时长"之外再多等 1 s, 免得卡在边界上
HIT_WAIT = 3.0          # ① 触发帧发出后等命中的上限(秒)
JOIN_EXTRA = 8.0        # 触发那个后台线程的收尾余量(秒)


def lamp_intro(have_sess):
    """第一步 · 开场: 发哪条口、触发帧长什么样、本次有没有断点会话 —— 只打印, 不判。"""
    frame = frame_698(build_read_apdu(0x03, DISP_OAD))
    print("\n== 8-7 通信唤醒点亮背光 (蓝牙口发 698 读表钟 / 停 DLT698Link.c:324 / 回读 u32LampTicker) ==")
    print("   发帧走 %s 口, 触发帧 %d 字节: %s"
          % (_disp_port_name(DISP_PORT_BLE), len(frame), bytes(frame).hex(" ").upper()))
    if not have_sess:
        print("   !! 本次没有断点会话(没接 J-Link) ⇒ 判①②③④⑤ 没做成; 本次范围 = 无白盒通路")


def lamp_read_base(sess):
    """第二步 · 基线: 发帧前叫停回读 `u32LampTicker` / `g_DispStatus` / `g_DispPara[SlctT]`。

    返回 `{"tick": int|None, "stat": str|None, "slct": int|None}`; 那一次**读失败**(抛了)时返回
    `None` —— "没读到"与"读到 0"必须分得开。
    """
    base = _lamp_stop_read(sess, LAMP_BASE_KEYS)
    if base is None:
        print("   [白盒] 基线那一次回读没做成(叫停/读那一步抛了)")
        return None
    print("   [白盒] 基线: %s=%s | %s=%s | %s=%s"
          % (LAMP_TICK, base.get(LAMP_TICK), DISP_STAT, base.get(DISP_STAT),
             DISP_SLCT, base.get(DISP_SLCT)))
    return {"tick": kwh_num(base.get(LAMP_TICK)),
            "stat": (base.get(DISP_STAT) or "").strip() or None,
            "slct": kwh_num(base.get(DISP_SLCT))}


def lamp_trigger_post(sess, link, bp_post, *, oad=DISP_OAD, port=DISP_PORT_BLE,
                      timeout=HIT_WAIT, join=JOIN_EXTRA):
    """第三步 · ① 的触发: 蓝牙口发一帧 698 读表钟, 并发中停在投递点上(`with_trigger` 当场挂当场撤)。

    返回 `with_trigger` 的那份记录(`hit` / `vars` / `error` 三样都在里面) —— 判成没判成由脚本
    下一步的 `lamp_judge_post` 说, 本步只管"发出去 + 有没有停住"。
    """
    frame = frame_698(build_read_apdu(0x03, oad))
    r1 = sess.with_trigger(bp_post, _send_clock_698_lamp, link, frame, oad, timeout=timeout,
                           _join=join, _vars=("port",),
                           _pair_name="8-7 通信唤醒触发帧 698 读表钟")
    if r1.get("error") is not None:
        print("   !! 触发那一侧抛了异常(帧可能没发出去): %s" % r1.get("error"))
    return r1


def lamp_judge_post(r1, bp_post, port=DISP_PORT_BLE, timeout=HIT_WAIT):
    """判① · 那个投递点被执行了没有 + 停住时入参 `port` 是不是发帧那条口。"""
    h1, v1 = (r1 or {}).get("hit"), (r1 or {}).get("vars") or {}
    got_port = kwh_num(v1.get("port"))
    _f1 = ("蓝牙帧没走到 698 分析口 ⇒ 断点不命中; 端口索引串了 ⇒ port 不是 %d" % port)
    if got_port is None:
        return None, "停住了但入参 port 没读到 ⇒ 本次没做成"
    print("   [白盒] 断[A] 停在 %s | port=%s" % (h1.where(), v1.get("port")))
    return got_port == port, \
        "%s 在 %.1fs 内命中(停在 %s); 停住时 port=%d(发帧走的填的是 %s) ⇒ %s" % (
            _bptxt(bp_post), timeout, h1.where(), got_port, _disp_port_name(port),
            "这条通信帧走到了该投唤醒消息的那一句" if got_port == port else
            "**这条帧不是从蓝牙口进来的** —— 投递点判的是蓝牙那一支")


def lamp_read_after_wake(sess):
    """第四步 · ① 放行后等 `LIGHT_WAIT` 秒, 叫停回读 `u32LampTicker` 与 `g_DispStatus`。

    返回 `{"tick": int|None, "stat": str|None}`; 那一次读失败返回 `None`。
    """
    time.sleep(LIGHT_WAIT)
    mid = _lamp_stop_read(sess, (LAMP_TICK, DISP_STAT))
    if mid is None:
        print("   [白盒] 放行后 %.1f s 那一次回读没做成" % LIGHT_WAIT)
        return None
    print("   [白盒] 放行后 %.1f s: %s=%s | %s=%s"
          % (LIGHT_WAIT, LAMP_TICK, mid.get(LAMP_TICK), DISP_STAT, mid.get(DISP_STAT)))
    return {"tick": kwh_num(mid.get(LAMP_TICK)),
            "stat": ((mid.get(DISP_STAT) or "").strip() or None)}


def lamp_judge_ticker(tick0, tick1, light_wait=LIGHT_WAIT):
    """判② · 背光计时器被这一帧置上了没有(发帧前基线必须是 0, 否则分不出是谁置的)。"""
    _f2 = "通信帧那条路没人调 TurnOnLampLed(TaskDisplay.c:3936) ⇒ 计时器一直是 0"
    if tick1 is None:
        return None, "放行后 %.1f s 回读 %s 没读到 ⇒ 本次没做成" % (light_wait, LAMP_TICK)
    if tick0 != 0:
        return None, ("发帧前基线 %s=%s 就不是 0(上一次亮显还没走完) ⇒ 这一帧有没有置上它分不出来, "
                      "本次没做成" % (LAMP_TICK, tick0))
    return tick1 != 0, "发帧前 %s=0; 放行后 %.1f s 回读 =%s ⇒ %s" % (
        LAMP_TICK, light_wait, tick1,
        "被这一帧置上了(背光该亮)" if tick1 != 0 else "**仍是 0** —— 通信帧跑完没人点亮背光")


def lamp_judge_duration(tick1, slct):
    """判③ · 置上的时长等于该表参数(计时器 == `g_DispPara[SlctT]` × C_SysTick)没有。"""
    _f3 = "置上的是别的秒数 ⇒ 计时器不等于 g_DispPara[SlctT]×C_SysTick"
    if tick1 is None:
        return None, "② 那次回读没读到 ⇒ 本次没做成"
    if slct is None:
        return None, "基线里 %s 没读到 ⇒ 比不上" % DISP_SLCT
    want = slct * C_SYS_TICK
    return tick1 == want, "%s=%s; %s=%s × C_SysTick(%d) = %s ⇒ %s" % (
        LAMP_TICK, tick1, DISP_SLCT, slct, C_SYS_TICK, want,
        "对上了" if tick1 == want else "**对不上** —— 置上的不是这个时长(该参数默认 10 ⇒ 640)")


def lamp_judge_disp(st0, st1):
    """判⑤ · 液晶从息屏态被唤醒没有。

    ⚠ 息屏那个前置**帧路径造不出**(驱得动 `Run_StopDisp` 的只有 RTC 中断与按键) ⇒ 基线不是息屏态
      时记"没做成", 不记 FAIL —— 记成 FAIL 就是拿台面属性判固件(CLAUDE.md 第 28 条)。
    """
    _f5 = "唤醒只投了消息没换显示状态 ⇒ 回读仍是 ST_StopDisp"
    if st0 is None or st1 is None:
        return None, "%s 两次有一边没读到(基线=%s / 发帧后=%s) ⇒ 本次没做成" % (DISP_STAT, st0, st1)
    if st0 != DISP_ST_STOP:
        return None, "前置不满足: 发帧前 %s=%s, 不是 %s ⇒ 息屏这个前置帧路径造不出" \
                     "(驱得动 Run_StopDisp 的只有 RTC 中断与按键), 本次没做成" % (
                         DISP_STAT, st0, DISP_ST_STOP)
    return st1 != DISP_ST_STOP, "发帧前 %s=%s(息屏); 发帧后 =%s ⇒ %s" % (
        DISP_STAT, st0, st1,
        "离开了息屏态" if st1 != DISP_ST_STOP else "**仍在息屏态** —— 通信帧没把液晶唤醒")


def lamp_read_tick(sess):
    """第五步 · 等到时候叫停回读一次 `u32LampTicker` → int 或 None(那一次读没做成)。"""
    end = _lamp_stop_read(sess, (LAMP_TICK,))
    if end is None:
        print("   [白盒] 到时那一次回读没做成")
        return None
    print("   [白盒] 到时回读: %s=%s" % (LAMP_TICK, end.get(LAMP_TICK)))
    return kwh_num(end.get(LAMP_TICK))


def lamp_judge_off(tick1, tick2, slct, waited):
    """判④ · 到时熄灭没有 —— 先看②那次读到过非 0 没有(压根没置上就无从谈起)。

    `tick2` 是等满 `waited` 秒之后那一次回读(`None` = 那一次没做成); `slct` 与 `waited` 只进
    判词 —— 判的仍然只有一件事: 到时该是 0。
    """
    _f4 = "INT_LampLedTicker(TaskDisplay.c:3921) 没在递减 ⇒ 到时回读仍非 0"
    if tick1 in (None, 0):
        return None, "② 那次没读到非 0(压根没被置上, %s s 的时长 = %s) ⇒ 熄灭无从谈起, 本次没做成" \
                     % (slct, "读不到" if slct is None else slct)
    if tick2 is None:
        return None, "等满 %.1f s 后回读 %s 没读到 ⇒ 本次没做成" % (waited, LAMP_TICK)
    return tick2 == 0, "放行后共等 %.1f s 回读 %s=%s ⇒ %s" % (
        waited, LAMP_TICK, tick2,
        "计时走完, 背光该灭" if tick2 == 0 else "**还非 0** —— 到时没灭(或计时没在递减)")


def lamp_unproven(entries, falsify, why):
    """半途中止时把**没做成**的条目一次记全 —— 每条 `ok=None`(没做成绝不当 FAIL)。

    `entries`(每条 `(认领号, 判据文字)`)与 `falsify`(认领号 → falsify)都由脚本给(纯数据),
    与正常路径上那五条**同名同号** —— 中止那一趟少记一条, 等于把那条判据从分母里拿掉。
    """
    recs, w2, add = _rec_bag()
    for crit, text in entries:
        add("%s %s" % (crit, text), None, why, crit=crit, obs=judge.DEBUG,
            falsify=falsify.get(crit, "不做这一次时无从判 —— 本记录不作为固件证据"))
    w2.append("   [白盒] %s" % why)
    return _rec_close(recs, w2)


def lamp_clear_breaks(sess):
    """兜底撤断点 —— 命中那一路 `with_trigger` 已自撤, 这里防"已命中但后面某步抛了"的残局。

    ⚠ 撤断点只能在停住态(见 `breakpoint.clear_breaks` 的 ⚠): 留着断点它自己会把核撂停, 于是其后
      每条串口帧整帧无应答 —— 与"表死机"一模一样。没会话时是空动作。
    """
    if sess is None:
        return
    try:
        if sess.breakpoints():
            sess.ensure_stopped()
            sess.clear_breaks()
    finally:
        sess.ensure_running()


# ============ 7-3 RS485 帧进出与派发(485 口注入 645/698/乱帧) ============
# 7-1 只发过 698 那一支(它的 ④ 是"放行后重发一帧, 读 g_ProtSt 看被当成什么派的"), 645 支与乱帧
# 这一套白盒从来没碰过 —— 7-3 判的就是这两半。`DISP_PORT_485` / `DISP_PROTO_698` / `DISP_OAD`
# 是同一件事的两处用法, 沿用 7-1 那份, 不另起一份。
DISP_PROTO_645 = 0         # g_ProtSt 取值: 0=645帧, 1=698帧(Communicate.c:115 的声明注释)

# 六条判据各自的『什么样的固件会让它 FAIL』—— 键与 `rs485_dispatch_criteria` 一一对应。
# 成表是为了让脚本递证据时按 `crit` 取, 不必把同一句在每条分支上重抄一遍。
RS485_FALSIFY = {
    "①": "这帧没被 645 支认下(地址/格式/CS 被判错) ⇒ 断点不命中",
    "②": "收帧缓冲错位/截断/粘帧 ⇒ 字节与发出帧不等",
    "③": "被 698 解析抢走 ⇒ g_ProtSt[port] 读回 1",
    "④": "端口索引串了或缓冲错位 ⇒ port/pFrame 对不上",
    "⑤": "CS 校验失守 ⇒ 乱帧被 645 支认下, :338 命中",
    "⑥": "乱帧被误派发 ⇒ g_ProtSt[port] 被改写成 0",
}


def rs485_dispatch_criteria():
    """7-3 的**预设判据条目**(测试前定死; 源 = ledger.md 7-3「观察与判据」的 J 列逐条)。

    ⚠ 这六条**逐字抄 ledger.md 7-3 的 J 列**: 改那边就得同步改这儿。"""
    return {
        "①": "645 帧认下: 从 485 口发一帧 645 读日期时间(单播本表地址), Communicate.c:338 "
              "3 s 内命中(那一句只有 645 支认下这帧才走得到)",
        "②": "645 帧取数正确: 在 645 解析入口(Communicate.c:332, Analyse_645Prot 调用行)停住时读 "
              "g_RS485Buf 起 len(frame) 个字节, 逐字节等于脚本发出的那一帧; "
              "且 g_ComAdr[port] 恰等于帧长(4 个前导 FE 不进缓冲)",
        "③": "派发认对协议(645 支): 紧接着发一帧 698 读表钟, 停 DLT698Link.c:283 读 g_ProtSt[port] "
              "== 0(装的是上一帧 645 跑完时固件写下的值)",
        "④": "698 帧原样进出与端口归属: ③ 同一次停住读入参 port == 1(PT_485_M)与 pFrame 起 "
              "len(frame) 个字节, 逐字节等于这一帧",
        "⑤": "乱帧被 645 支挡住: 从 485 口发一帧 CS 改坏的 645 帧, Communicate.c:338 3 s 内不命中",
        "⑥": "乱帧不改派发状态: ⑤ 之后发一帧 698 停 DLT698Link.c:283, 读 g_ProtSt[port] 仍 == 1"
              "(若乱帧被误派发成 645 会读回 0)",
    }


def send_rs485_645(ser, frame, wait=2.0):
    """7-3 的触发动作之一: 把这帧 645 发出去并收应答(由 `with_trigger` 放在**后台线程**里跑)。

    单拎成具名函数是为了让 `with_trigger` 的日志能打出触发干了什么(它取 `fn.__name__`)。
    ⚠ 功能名里**不写协议词** —— "[645]" 那一格是帧自己的属性(`loglabel.Frame`), 再写一遍就是
      两条通道说同一件事(`_check_loghead` 判据③)。
    """
    return send_frame(ser, frame, wait=wait, tag="rs485_645",
                      what="读日期时间 DI 0400010C(7-3 645 支注入帧)")


def send_rs485_698(ser, frame, oad, wait=2.0):
    """7-3 的触发动作之一: 把这帧 698 发出去并收应答(同上, 具名只为日志)。"""
    return send_frame(ser, frame, wait=wait, tag="rs485_698",
                      what="读表钟 OAD %s(7-3 698 支注入帧)" % oad)


def send_rs485_junk(ser, frame, wait=2.0):
    """7-3 的触发动作之一: 发那一帧 CS 被改坏的 645 帧(同上, 具名只为日志)。

    `wait` 也是**留给固件重同步的时间**: 这帧会被判 `ST_ProtErr`, `Communicate.c:371` 那一段
    要腾出手来找下一个 0x68 重新对齐, 期间通道不在"收帧等待"态。发完就撤, 下一帧会被那截残帧
    粘住 —— 这里多等一会儿, 让那套重同步跑完。
    """
    return send_frame(ser, frame, wait=wait, tag="rs485_junk",
                      what="CS 故意改坏的 645 帧(7-3 乱帧容错注入帧)")


def corrupt_cs(frame):
    """把一帧 645 的**校验和字节**改坏, 返回可发的那一帧 —— 645 的帧格式不合法, 但帧头/长度/尾 16H 都在。

    CS 的位置: 尾 `16H` 前面那一个字节。改它比改数据域更能把固件逼到 `ST_ProtErr` 那条路 ——
    数据域动一个字节仍是合法帧(会被当成另一条报文), 而 CS 对不上是**格式**不合法。
    作为 645 的帧结构知识住这儿; 要的是"一帧乱帧", 不是"某一处调用点的临时写法"。
    """
    body = bytearray(bytes(frame).lstrip(b"\xfe"))   # 收帧层从第一个 0x68 起才收, 前导 FE 不算帧体
    body[-2] ^= 0xFF
    return loglabel.Frame(bytes(body), loglabel.PROTO_645)


def rs485_dispatch_roundtrip(ser, sess=None, bp_698=None, bp_645=None, bp_645_rx=None,
                             oad=DISP_OAD, hit_wait=3.0, join_extra=8.0, junk_wait=2.0):
    """7-3 判过积木: 『485 口帧进出、协议派发与乱帧容错』。

    四帧的顺序就是判据本身(每一步读的是**上一帧**留在固件里的状态):
      · 第 1 帧 645 读日期时间(发两次, 见下) → 停 `Platform\\Communicate.c:338`
        (`g_ProtSt[port] = 0`): ① 这一句在 `if (sta645 == ST_NeedAck)` 里, 走得到 = 645 支认下了它;
        ② 另发一次, 停在 `Communicate.c:332`(`Analyse_645Prot` 的调用行)读 `g_RS485Buf`
        起 len(frame) 个字节与自己发出的帧对照。
      ⚠ ② 的读点**不能**放在 ① 那个 `:338` 停点上 —— 实测(7-3 首跑): `Analyse_645Prot` 把应答
        **原地写回同一个缓冲**(`pBuff = g_RS485Buf`, Communicate.c:318; `:622` 的发送口也从它取),
        走到 `:338` 时缓冲里装的已经是**应答**: 那一跑读回的是
        `… 68 91 0B 3F 34 33 37 39 76 …`(应答的 DI 与数据域, 减 0x33 解出 08:43:06 与同刻 698
        表钟的 08:43:07 对得上), 与发出的请求永远不等。那是读点错, 不是固件错 —— 拿它判固件,
        任何正确固件都不满足。`:332` 是调用行, 停在它上面时请求还没被覆盖, 判据原意(收帧缓冲
        错位/截断/粘帧)才成立。所以这一帧**发两次**, 两次都是只读抄读帧。
      · 第 2 帧 698 读表钟 → 停 `Application\\DLT698Link.c:283`(`Analyse_698Prot` 首条可执行语句):
        ③ 停住这一刻本帧还没判完, `g_ProtSt[port]` 装的还是第 1 帧跑完时写下的值 ⇒ 它答的正是
          "第 1 帧被当成什么派发的", 期望 0(645); ④ 同一次停住读入参 `port` 与 `pFrame`。
      · 第 3 帧 CS 改坏的 645 帧: ⑤ 期望 `:338` **不**命中(挡住); ⑥ 它跑完后 `g_ProtSt[port]`
        仍是第 2 帧留下的 1(没被误派发)—— 用第 4 帧的停住把它读出来。
      · 第 4 帧 698 读表钟 → 停 `:283`, 读 `g_ProtSt[port]` == 1。

    ⚠ 四个锚点都是**照固件源码核过的**, 不许照着台账旧文抄:
      · `:338` 而不是 `:334`(那句 `if (sta645 == ST_NeedAck)`)或 `DLT645Link.c:221`
        (`Analyse_645Prot` 首条可执行语句)—— 后两处**每一帧都过**(645 是先试的那一支, 698 帧
        也从 `:332` 走一遍), 停在那儿分不出"这帧是 645 还是 698"。
      · `:332` 而不是 `:334` —— 优化后 `:334` 解析成 `bl Analyse_645Prot` **之后**那一条
        (0x1f4e8), 停在它上面时应答已经写回缓冲, 与 `:338` 是同一个下场。
      · `:283` 而不是台账原写的 `DLT698Link.c:307` —— `:307` 那句 `CMD_Relay_Caculator_And_Master`
        只在 `g_AddrLog == 0x01` 保护下跑, 普通抄读帧根本不走它(永不命中的断点)。
      · 645 支那一半**没有**第二个"帧到即停"口可用: 一个被 645 支拒掉的帧(`ST_ProtErr`)会继续
        往下走到 `:352` 的 698 解析 —— 所以"乱帧进了 698 解析链"本身不说明任何事, 别拿 `:283`
        的命中与否去判乱帧(⑤ 判的是 `:338`, 见上)。
    返回 `(recs, why, scope)`; **scope 半途中止时是 `None`**(结构信号, 别让脚本嗅文案)。

    `bp_698` / `bp_645` 都传脚本里那两个模块级字面量(**四种写法之一**, 见 `_bpok`)—— 两处都是"每来一帧就停"
    的高频口, 走 `with_trigger` 的元组形态(它当场挂、命中时趁停住自撤), 不收预先挂好的 bpno。
    """
    for _n, _bp in (("bp_698", bp_698), ("bp_645", bp_645), ("bp_645_rx", bp_645_rx)):
        if not _bpok(_bp):
            raise ValueError("%s 不是认可的断点字面量(四种写法见 `_bpok`), 收到 %r" % (_n, _bp))
    recs, why = [], []
    f645 = read_time645_dt()
    raw645 = bytes(f645).lstrip(b"\xfe")     # 收帧层在第一个 0x68 才开始往缓冲里存, 前导 FE 被丢
    f698 = frame_698(build_read_apdu(0x03, oad))
    fjunk = corrupt_cs(f645)

    def add(name, ok, detail, crit=None, obs=judge.DEBUG, falsify=None):
        recs.append(rec(name, ok, detail, crit=crit, obs=obs, falsify=falsify))

    def _abort(reason, skip=()):
        """半途中止: 还没记过的条目一律记 `ok=None`(没做成)。

        `skip` = 已经出过读数的条目(如 ② 在 `:332` 那一窗已记) —— 半途中止不许覆盖一条已经
        拿到的读数: 那是拿"没做成"盖住"做成了", 与拿"没做成"盖住"失败"一样是伪造账。
        """
        why.append(reason)
        print("   [白盒] %s" % reason)
        # ⚠ `falsify` 六条**照样得写**: 缺了它, 判据会打出"认领它的证据答不出『什么固件会让它 FAIL』"
        #   —— 那是对**没做成**的制度性抱怨, 读的人会当成固件嫌疑(`frame_dispatch_roundtrip` 同律)。
        for _c, _t, _f in (("①", "645 帧认下", "这帧没被 645 支认下(地址/格式/CS 被判错) ⇒ 断点不命中"),
                           ("②", "645 帧取数正确", "收帧缓冲错位/截断/粘帧 ⇒ 字节与发出帧不等"),
                           ("③", "派发认对协议(645 支)", "被 698 解析抢走 ⇒ g_ProtSt[port] 读回 1"),
                           ("④", "698 帧原样进出与端口归属", "端口索引串了或缓冲错位 ⇒ port/pFrame 对不上"),
                           ("⑤", "乱帧被 645 支挡住", "CS 校验失守 ⇒ 乱帧被 645 支认下, :338 命中"),
                           ("⑥", "乱帧不改派发状态", "乱帧被误派发 ⇒ g_ProtSt[port] 被改写成 0")):
            if _c in skip:
                continue
            add("断[B/C] %s %s" % (_c, _t), None, "半途中止: " + reason, crit=_c, falsify=_f)
        return recs, why, "ok"

    print("\n== 7-3 RS485 帧进出与派发 (485 口注入 645/698/乱帧) ==")
    print("   第 1 帧 645 %d 字节(含 4 个前导 FE): %s" % (len(f645), bytes(f645).hex(" ").upper()))
    print("   第 2/4 帧 698 %d 字节: %s" % (len(f698), bytes(f698).hex(" ").upper()))
    print("   第 3 帧乱帧(CS 改坏) %d 字节: %s" % (len(fjunk), bytes(fjunk).hex(" ").upper()))

    # 停住时要读的量。**逐字节列 key 而不是读整数组** —— 与 `frame_dispatch_roundtrip` 同一律:
    # `kwh_num` 吃的就是 `-data-evaluate-expression` 的单值文本, 整数组的文本形态是另一回事。
    keys645 = tuple("g_RS485Buf[%d]" % i for i in range(len(raw645)))
    keys698 = tuple("pFrame[%d]" % i for i in range(len(f698)))

    if sess is None:
        return _abort("本次没有断点会话(没接探针) ⇒ 判①②③④⑤⑥ 没做成")

    try:
        # ---- 第 1 帧 645(第一次发): ② 收帧缓冲取数正确 ----
        # ⚠ 这一窗的停点是**解析入口**(`Analyse_645Prot` 的调用行), 不是 ① 那个 `:338` ——
        #   `:338` 上缓冲里已经是被原地写回的应答(理由见本函数 docstring 的 ⚠)。
        r0 = sess.with_trigger(bp_645_rx, send_rs485_645, ser, f645, timeout=hit_wait,
                               _join=join_extra,
                               _vars=("port", "g_ComAdr[port]") + keys645,
                               _pair_name="7-3 第 1 帧 645 收帧缓冲(解析入口)")
        h0, v0 = r0.get("hit"), r0.get("vars") or {}
        if r0.get("error") is not None:
            print("   !! 触发那一侧抛了异常(帧可能没发出去): %s" % r0.get("error"))
        if h0 is None:
            add("断[C] ② 645 帧取数正确", None,
                "645 帧发出后 %.1fs 内没等到 %s 命中 ⇒ 本次没做成(收帧缓冲无从读起)"
                % (hit_wait, _bptxt(bp_645_rx)),
                crit="②", falsify="收帧缓冲错位/截断/粘帧 ⇒ 字节与发出帧不等")
        else:
            print("   [白盒] 断[D] 收帧停在 %s | port=%s | g_ComAdr[port]=%s"
                  % (h0.where(), v0.get("port"), v0.get("g_ComAdr[port]")))
            got645 = vars_bytes(v0, keys645)
            print("   [白盒] 645 收帧缓冲起 %d 字节 = %s"
                  % (len(raw645), got645.hex(" ").upper() if got645 else "(读不全)"))
            if kwh_num(v0.get("port")) != DISP_PORT_485:
                add("断[C] ② 645 帧取数正确", None,
                    "停住的那一帧不归 485 口(port=%s) ⇒ 本次不据此判固件" % (v0.get("port"),),
                    crit="②", falsify="收帧缓冲错位/截断/粘帧 ⇒ 字节与发出帧不等")
            elif got645 is None:
                add("断[C] ② 645 帧取数正确", None,
                    "g_RS485Buf 这一次没读全 ⇒ 本次没做成",
                    crit="②", falsify="收帧缓冲错位/截断/粘帧 ⇒ 字节与发出帧不等")
            else:
                same2 = (got645 == raw645)
                add("断[C] ② 645 帧取数正确", same2,
                    "收帧缓冲 %d 字节 %s; 发出的是(去 FE) %s; 逐字节%s; g_ComAdr[port]=%s(帧长 %d); "
                    "读点 %s(解析入口, 应答还没写回缓冲)"
                    % (len(got645), got645.hex(" ").upper(), raw645.hex(" ").upper(),
                       "全等" if same2 else "**不等**", v0.get("g_ComAdr[port]"), len(raw645),
                       h0.where()),
                    crit="②", falsify="收帧缓冲错位/截断/粘帧 ⇒ 字节与发出帧不等")

        # ---- 第 1 帧 645(再发一次): ① 认下 ----
        r1 = sess.with_trigger(bp_645, send_rs485_645, ser, f645, timeout=hit_wait,
                               _join=join_extra, _vars=("port",),
                               _pair_name="7-3 第 1 帧 645 读日期时间")
        h1, v1 = r1.get("hit"), r1.get("vars") or {}
        if r1.get("error") is not None:
            print("   !! 触发那一侧抛了异常(帧可能没发出去): %s" % r1.get("error"))
        if h1 is None:
            return _abort("645 帧发出后 %.1fs 内没等到 %s 命中"
                          % (hit_wait, _bptxt(bp_645)), skip=("②",))
        print("   [白盒] 断[C] 第 1 帧 停在 %s | port=%s" % (h1.where(), v1.get("port")))

        # ---- ① 645 帧认下 ----
        add("断[C] ① 645 帧认下", True,
            "%s 在 %.1fs 内命中(停在 %s); 注入帧 %d 字节(去 FE 后 %d 字节)"
            % (_bptxt(bp_645), hit_wait, h1.where(), len(f645), len(raw645)),
            crit="①", falsify="这帧没被 645 支认下(地址/格式/CS 被判错) ⇒ 断点不命中")

        # ---- 第 2 帧 698: ③ 派发认对协议(读的是第 1 帧跑完时写下的值) + ④ 原样进出 ----
        r2 = sess.with_trigger(bp_698, send_rs485_698, ser, f698, oad, timeout=hit_wait,
                               _join=join_extra,
                               _vars=("port", "len", "g_ProtSt[port]") + keys698,
                               _pair_name="7-3 第 2 帧 698 读表钟")
        h2, v2 = r2.get("hit"), r2.get("vars") or {}
        if r2.get("error") is not None:
            print("   !! 触发那一侧抛了异常(帧可能没发出去): %s" % r2.get("error"))
        if h2 is None:
            print("   [白盒] 第 2 帧发出后 %.1fs 内没等到 %s 命中"
                  % (hit_wait, _bptxt(bp_698)))
            for _c, _t, _f in (("③", "派发认对协议(645 支)",
                                "被 698 解析抢走 ⇒ g_ProtSt[port] 读回 1"),
                               ("④", "698 帧原样进出与端口归属",
                                "端口索引串了或缓冲错位 ⇒ port/pFrame 对不上")):
                add("断[B] %s %s" % (_c, _t), None,
                    "第 2 帧 698 发出后没等到 %s 命中 ⇒ 本次没做成(第 1 帧的派发结果无从读起)"
                    % (_bptxt(bp_698)), crit=_c, falsify=_f)
        else:
            print("   [白盒] 断[B] 第 2 帧 停在 %s | port=%s | len=%s | g_ProtSt[port]=%s"
                  % (h2.where(), v2.get("port"), v2.get("len"), v2.get("g_ProtSt[port]")))
            proto2 = kwh_num(v2.get("g_ProtSt[port]"))
            add("断[B] ③ 派发认对协议(645 支)", proto2 == DISP_PROTO_645,
                "第 2 次停住时 g_ProtSt[%s]=%s(装的是第 1 帧 645 跑完时固件写下的派发结果); "
                "0=645帧 1=698帧 ⇒ %s"
                % (v2.get("port"), v2.get("g_ProtSt[port]"),
                   "固件把它当 645 派发了" if proto2 == DISP_PROTO_645
                   else "**读回 %s** —— 没被认作 645" % (proto2,)),
                crit="③", falsify="被 698 解析抢走 ⇒ g_ProtSt[port] 读回 1")

            got698 = vars_bytes(v2, keys698)
            len2 = kwh_num(v2.get("len"))
            if got698 is None:
                add("断[B] ④ 698 帧原样进出与端口归属", None,
                    "入参 pFrame 这一次没读全 ⇒ 本次没做成",
                    crit="④", falsify="端口索引串了或缓冲错位 ⇒ port/pFrame 对不上")
            else:
                ok4 = (kwh_num(v2.get("port")) == DISP_PORT_485
                       and got698 == bytes(f698) and len2 == len(f698))
                add("断[B] ④ 698 帧原样进出与端口归属", ok4,
                    "port=%s(期望 %d) | len=%s(期望 %d) | 解析口拿到的 %d 字节 %s, 逐字节%s"
                    % (v2.get("port"), DISP_PORT_485, v2.get("len"), len(f698), len(got698),
                       got698.hex(" ").upper(),
                       "全等" if got698 == bytes(f698) else "**不等**"),
                    crit="④", falsify="端口索引串了或缓冲错位 ⇒ port/pFrame 对不上")

        # ---- 第 3 帧 乱帧: ⑤ :338 不命中 ----
        r3 = sess.with_trigger(bp_645, send_rs485_junk, ser, fjunk, junk_wait, timeout=hit_wait,
                               _join=join_extra, _vars=("port",),
                               _pair_name="7-3 第 3 帧 乱帧(CS 改坏)")
        h3 = r3.get("hit")
        _wbline("7-3 第 3 帧 乱帧 → %s" % (_bptxt(bp_645)), r3, hit_expected=False)
        if r3.get("error") is not None and h3 is None:
            # 触发线程自己抛了(如串口写失败) —— 那这帧**根本没发出去**, 不命中就不是固件挡住的
            add("断[C] ⑤ 乱帧被 645 支挡住", None,
                "乱帧那一侧触发报错(%s), 这帧可能没发出去 ⇒ 本次没做成" % (r3.get("error"),),
                crit="⑤", falsify="CS 校验失守 ⇒ 乱帧被 645 支认下, :338 命中")
        else:
            add("断[C] ⑤ 乱帧被 645 支挡住", h3 is None,
                "%s 在 %.1fs 内%s; 乱帧 = 第 1 帧的同一条 645 帧, 只把 CS(倒数第二字节)改成 %s"
                % (_bptxt(bp_645), hit_wait, "未命中" if h3 is None else "**命中了**",
                   "%02X" % bytes(fjunk)[-2]),
                crit="⑤", falsify="CS 校验失守 ⇒ 乱帧被 645 支认下, :338 命中")

        # ---- 第 4 帧 698: ⑥ 乱帧没改派发状态(读的是第 2 帧留下的值) ----
        r4 = sess.with_trigger(bp_698, send_rs485_698, ser, f698, oad, timeout=hit_wait,
                               _join=join_extra, _vars=("port", "g_ProtSt[port]"),
                               _pair_name="7-3 第 4 帧 698 读表钟(乱帧之后的见证帧)")
        h4, v4 = r4.get("hit"), r4.get("vars") or {}
        if h4 is None:
            add("断[B] ⑥ 乱帧不改派发状态", None,
                "第 4 帧 698 发出后 %.1fs 内没等到 %s 命中 ⇒ 本次没做成(乱帧是否改写了派发状态"
                "无从读起; 固件在乱帧之后要重同步一次, 也可能就是那一下没接上)"
                % (hit_wait, _bptxt(bp_698)),
                crit="⑥", falsify="乱帧被误派发 ⇒ g_ProtSt[port] 被改写成 0")
        else:
            proto4 = kwh_num(v4.get("g_ProtSt[port]"))
            add("断[B] ⑥ 乱帧不改派发状态", proto4 == DISP_PROTO_698,
                "第 4 次停住时 g_ProtSt[%s]=%s(装的是第 2 帧 698 跑完时写下的 1; 乱帧若被误派发"
                "成 645 就会在这儿读回 0) ⇒ %s"
                % (v4.get("port"), v4.get("g_ProtSt[port]"),
                   "乱帧没改它" if proto4 == DISP_PROTO_698
                   else "**乱帧把它改成了 %s**" % (proto4,)),
                crit="⑥", falsify="乱帧被误派发 ⇒ g_ProtSt[port] 被改写成 0")
        return recs, why, "ok"
    finally:
        # 撤断点只能在停住态(见 `breakpoint.clear_breaks` 的 ⚠: 核一跑起来 MI 命令就石沉大海)。
        # `with_trigger` 收 (文件,行号) 时命中路径会趁停住自撤、没命中时自己补撤; 这里兜底
        # "已命中但后面某步抛了"那种残局 —— 留着断点它自己会把核撂停, 其后每条串口帧整帧无应答。
        try:
            if sess.breakpoints():
                sess.ensure_stopped()
                sess.clear_breaks()
        finally:
            sess.ensure_running()


# ============ 7-2 载波·管理芯中继透传(485 口注入转发给计量芯的帧) ============
# 7-2 的正身是"载波主站抄读计量芯", 本仓没有载波主站/载波模块(与 7-1 缺蓝牙管道同一类) ——
# 能做的是**管理芯那一半**: 主站发来的帧怎么被中继给计量芯、计量芯的应答怎么被转回去。
# 中继这条路**与口无关**: 判"这帧是给计量芯的"只看帧里地址特征(AF)的 bit4-5, 所以从 485 口
# 注入同一族的帧, 走的是同一段代码(E 列已写"白盒限管理芯帧处理")。载波链路电气那一半不假装测过。
RELAY_PORT_METER = 0           # PT_UARTM = 计量芯口(UserCfg.h:378 的 #define, 不是 enum —— gdb 里查不到这个名字)
RELAY_CMD_645_REPLY = 0x91     # 计量芯应答读数据。0x94/0xE1/0xE4 三档与它共用同一段体(DLT645Link.c:480-483 是 case 标号行, :484 才是体首条语句)
RELAY_AF_LOGIC = 0x10          # AF 的 bit4-5。置上它 ⇒ `g_AddrLog = (pFrame[AF]>>4)&0x03` == 1(DLT698Link.c:100)

# 7-2 七条判据各自的「什么样的固件会让它 FAIL」——**单点**, 脚本按 `crit` 取。
# ⚠ 缺了它, 判据会打出"认领它的证据答不出『什么固件会让它 FAIL』" —— 那是对没做成的制度性
#   抱怨, 读的人会当成固件嫌疑。改这里就得同步改 `plc_relay_roundtrip` 里的同名文字。
PLC_RELAY_FALSIFY = {
    "①": "逻辑地址位判错 ⇒ 这帧被当普通帧自己应答, 断点不命中",
    "②": "落点条件不成立或转发没被调用 ⇒ 断点不命中",
    "③": "搬运长度算错/缓冲没清 ⇒ 字节与发出帧不等",
    "④": "地址判定或功能码分派错了 ⇒ 断点不命中",
    "⑤": "漏了 DLT645Link.c:490 那次 +0x33(或重复加) ⇒ 数据域与收到的线上字节不等",
    "⑥": "回程被丢或走错支(回载波口去了) ⇒ 断点不命中",
    "⑦": "搬的是半条帧或连缓冲残渣一起推 ⇒ Len 与帧声明的长度不等",
}


def plc_relay_criteria():
    """7-2 的**预设判据条目**(测试前定死; 源 = ledger.md 7-2「观察与判据」的 J 列逐条)。

    ⚠ 这七条**逐字抄 ledger.md 7-2 的 J 列**: 改那边就得同步改这儿。"""
    return {
        "①": "698 中继入口命中并归 485 口: 从 485 口发一帧 698(地址特征的逻辑地址位置 1, 即转发给计量芯的帧), "
              "DLT698Link.c:307 3 s 内命中, 停住读入参 port == 1",
        "②": "698 转发动作发生: 同一帧再发一次停 Communicate.c:1129(Start_Relay_Frame 体首条语句), "
              "命中即说明落点条件成立(计量芯口空闲), 入参 port == 0(PT_UARTM)且 Len == 帧长",
        "③": "698 转发帧文正确: ② 停住时读 g_UARTMBuf 起 Len 字节, 逐字节等于发出的那一帧"
              "(管理芯原样透传, 不动一个字节)",
        "④": "645 透传入口命中并归 485 口: 从 485 口发一帧 645 应答形态(CMD 0x91, 地址=本表地址), "
              "DLT645Link.c:484 3 s 内命中, 停住读入参 port == 1",
        "⑤": "645 透传帧文正确: 同一帧再发一次停 Communicate.c:1129, 读 g_UARTMBuf 起 Len 字节, "
              "逐字节等于收到的那条 645 帧去前导 FE 后的原样(解析时数据域减过 0x33, 转发前 "
              "DLT645Link.c:490 又加回 0x33, 两次相抵 ⇒ 与收到的线上字节同一个形态)",
        "⑥": "回程回到请求来向那口: 再发一帧中继 698 等计量芯的应答回来, Communicate.c:1108 "
              "6 s 内命中(那一句 Start_Relay_Frame(PT_485_M, Len) 只在回 485 那一支里; "
              "回载波口的是 :1120, 另一行)",
        "⑦": "回程推的字节数对得上那条应答帧: ⑥ 同一次停住读 Len 与 g_RS485Buf 起 Len 字节, "
              "按协议从帧里算出来的长度(698 取长度域两个字节加 2; 645 取长度字节加 12)恰等于 Len",
    }


def relay_698_frame(oad):
    """转发给计量芯的 698 帧: 地址特征里逻辑地址位=01。

    `g_AddrLog == 0x01` 时固件在 :305-309 **任何地址检查之前**就把它转走并返回 —— 所以这帧的
    APDU 管理芯一个字都不看, 原样进计量芯口。用读表钟的 APDU 只是随手挑一个不含写的请求。
    """
    return frame_698(build_read_apdu(0x03, oad),
                     addr=bytes([P.MANAGE_AF | RELAY_AF_LOGIC]) + P.SERVER_TAIL)


def relay_645_frame(cmd=RELAY_CMD_645_REPLY):
    """转发给计量芯的 645 帧: 应答形态(CMD 0x91)、地址写本表地址。

    地址必须是本表地址(ADR_ABS)才进得了 DLT645Link.c:484 那个 if —— 通配地址要走厂内态那条
    或分支(`stAddr==ADR_6AA && Is_EnablePrg()`), 那会多一个不必要的前提。
    """
    return frame_645(cmd, bytes.fromhex("0C 01 00 04"), addr=TABLE_ADDR)


def reply_frame_len(buf):
    """计量芯回来的那条应答,**按协议**应当是多少字节; 认不出帧头返回 None。

    用在回程那一停: `Len` 是固件实际推回 485 的字节数, 这个数是那条帧**自己声明**的长度。
    两者不等 = 推给 485 的少了或多了几个字节(多推的可能是缓冲区残渣)。
    两条帧头长得不一样, 分开认: 645 在 `[7]` 还有个 0x68(68 + 6 地址 + 68), 698 只有开头一个。
    """
    b = bytes(buf or b"")
    if len(b) < 12 or b[0] != 0x68:
        return None
    if b[7] == 0x68:                     # 645: 长度字节在 [9], 全帧 = L+12
        return b[9] + 12
    return (b[1] | (b[2] << 8)) + 2      # 698: 长度域两字节小端, 全帧 = 长度域+2


def send_relay_698(ser, frame, oad, wait=2.0):
    """7-2 的触发动作之一: 把这帧中继 698 发出去并收应答(由 `with_trigger` 放在**后台线程**里跑)。

    单拎成具名函数是为了让 `with_trigger` 的日志能打出触发干了什么(它取 `fn.__name__`)。
    ⚠ 功能名里**不写协议词** —— "[698]" 那一格是帧自己的属性(`loglabel.Frame`), 再写一遍就是
      两条通道说同一件事(`_check_loghead` 判据③)。
    """
    return send_frame(ser, frame, wait=wait, tag="relay_698",
                      what="读表钟 OAD %s(7-2 中继 698 注入帧)" % oad)


def send_relay_645(ser, frame, wait=2.0):
    """7-2 的触发动作之一: 发这帧中继 645(同上, 具名只为日志)。"""
    return send_frame(ser, frame, wait=wait, tag="relay_645",
                      what="读日期时间 DI 0400010C(7-2 中继 645 注入帧)")


def plc_relay_roundtrip(ser, sess=None, bp_698=None, bp_645=None, bp_relay=None, bp_back=None,
                        oad=DISP_OAD, hit_wait=3.0, back_wait=6.0, join_extra=8.0):
    """7-2 判过积木: 『管理芯中继/透传——主站的帧转给计量芯, 计量芯的应答转回去』。

    五帧的顺序就是判据本身:
      · 第 1 帧 698(逻辑地址位置 1) → 停 `Application\\DLT698Link.c:307`: ① 命中 = 这帧被认成
        "转发给计量芯的帧", 在解析层就被转走(不走管理芯自己的应答逻辑); 同一次停住读入参 `port`。
      · 第 2 帧同一条 698 → 停 `Platform\\Communicate.c:1129`(`Start_Relay_Frame` 体首条语句):
        ② 命中 = 转发真的发生了(落点条件"计量芯口空闲"成立, 否则 :1070 那个 if 不成立、根本调不到
        这里), 读入参 `port`/`Len`; ③ 同一次停住读 `g_UARTMBuf` 起 `Len` 字节 —— 帧此时**已经拷进
        计量芯口缓冲**(拷贝就在它上一行的调用方里), 判"转过去的就是这一帧"。
      · 第 3 帧 645(CMD 0x91, 地址=本表地址) → 停 `Application\\DLT645Link.c:484`: ④ 命中 = 这帧
        进了 0x91/0x94/0xE1/0xE4 四档共用的那段体(它是体首条语句, 四个 case 标号行在它上面三行,
        打不上断点); 同一次停住读入参 `port`。
      · 第 4 帧同一条 645 → 停 `:1129`: ⑤ 读 `g_UARTMBuf` 起 `Len` 字节, 判它逐字节等于**收到的那条
        645 帧去前导 FE 后的原样**。为什么是原样而不是"数据域再加一次 0x33": 解析时数据域已经
        减过 0x33(`pFrame` 里是明文), `DLT645Link.c:490` 转发前又加回去 —— 一减一加相抵, 出去的
        与进来的同一个形态。⚠ 拿"线上数据域再 +0x33"当期望是**错的口径**, 那等于要求固件多加一次,
        会把合法的原样透传判成 FAIL(第一轮实跑就踩了这个坑)。
      · 第 5 帧中继 698(与前四帧同一条) → 停 `Platform\\Communicate.c:1108`: 那一句是
        `Start_Relay_Frame(PT_485_M, Len);`, 在 `else if (port == PT_UARTM)` 那一支里、`:1098`
        的 `port_num == PT_485_M && g_ComSta[PT_485_M] == ST_RxWait` 保护下。**命中它就是⑥** ——
        计量芯的应答走到了"回 485"这一支(回载波口的那一句是 :1120, 另一行, 停不到这儿);
        ⑦ 同一次停住读入参 `Len` 与 `g_RS485Buf` 起 `Len` 字节: 缓冲在 `:1100` 刚被
        `Copy_Data` 灌过, 所以头一个字节就该是那条应答帧的头; 拿帧自己声明的长度与 `Len` 比。
    返回 `(recs, why, scope)`; **scope 半途中止时是 `None`**(结构信号, 别让脚本嗅文案)。

    **判据能不能判死固件** —— 本积木里"断点没命中"一律判不通过, 不记"没做成":
      · 先把"没命中"里**台面那一半**摘出去 —— 开跑前发一帧**管理芯自己应答**的 698 读表钟,
        收不到合格应答就 `_abort`(表没跑 / 485 不通 / 管理芯 698 栈不工作 —— 那时一条都判不了,
        整项落未定论)。这一帧不认领任何条目, 它是把后面所有"没命中"从"不知道"变成
        "固件没走那条路"的那把尺子。
      · 前置过了之后的四种"没做成"只剩两种: `sess is None`(没接探针)与触发那一侧自己抛异常
        (帧没发出去)。这两种照样记 `None`; **其余一切没命中记 `False`**。
      · ⑥⑦ 例外但要写明前提: 回程要计量芯真的答了。只有前面已经证到"管理芯把帧转给了计量芯"
        (② 或 ⑤ 命中), 才允许把 ⑥ 的没命中判成 `False`(转出去了却没转回来 = 整表这一路不通);
        前面就断了的时候 ⑥ 记 `None`, 因为那种情形下回程根本没被走到。
    ⚠ 四个锚点都是**照固件源码核过的**, 不许照着台账旧文抄:
      · `:1129` 而不是台账原写的 `Communicate.c:1070` —— :1070 是 `if ((port == PT_485_M)&&
        (g_ComSta[PT_UARTM] == ST_RxWait))` 这行**判定**, 停在那儿读到的是条件里的变量; :1129 是
        转发动作函数体的首条语句, 停在那儿帧**已经在目标口缓冲里**, 能直接判帧文。而且 :1070 的
        条件成不成立由 :1129 命中**蕴含**(不成立就调不到 Start_Relay_Frame), 所以不必两处都停。
      · `DLT698Link.c:307` 只对**中继帧**成立 —— 它的 `g_AddrLog == 0x01` 是普通抄读帧走不到的那一支
        (7-1/7-3 的普通帧停在 :283)。这里是同一行在另一种帧下的用法, 不是重复。
      · `DLT645Link.c:484` 而不是 :480-483 —— 那四行是 `case 0x91/0x94/0xE1/0xE4` 的**标号行**
        (打不上断点), 而 case 之间是 fall-through, 四档共用 :484 起的同一段体。
      · 回程停 `:1108` 而不是 `:1098`(那个 `if` 判定行)或 `:1100`(`Copy_Data` 那一句) ——
        `:1108` 之后缓冲已经灌好, 而端口是**写死在参数里的字面量**, 于是"命中"本身就答了
        "回到的是哪一个口"; `:1098` 停住时还没搬, `:1100` 停住时帧还在源缓冲里没过去。

    `bp_*` 四个都传脚本里那四个模块级字面量(**四种写法之一**, 见 `_bpok`)—— 都是"每来一帧就停"的高频口, 走
    `with_trigger` 的元组形态(它当场挂、命中时趁停住自撤), 不收预先挂好的 bpno。
    """
    for _n, _bp in (("bp_698", bp_698), ("bp_645", bp_645),
                    ("bp_relay", bp_relay), ("bp_back", bp_back)):
        if not _bpok(_bp):
            raise ValueError("%s 不是认可的断点字面量(四种写法见 `_bpok`), 收到 %r" % (_n, _bp))
    recs, why = [], []
    f698 = relay_698_frame(oad)
    f645 = relay_645_frame()
    raw645 = bytes(f645).lstrip(b"\xfe")     # 收帧层丢掉 4 个前导 FE(与 7-3 同一件事)
    # ⑤ 的期望 = **收到的线上字节原样**(去 FE 那一份), 不是"数据域再加一次 0x33"。
    # 依据是固件那两步**互相抵消**: 解析口减一次(`:488-491` 之前已经还原进 `pFrame`), 转发前
    # `DLT645Link.c:490` 又加一次 —— 一减一加回到原样。实测停在 `:484` 时 pFrame 的数据域是
    # `0C 01 00 04`(减过 0x33 的), `:490` 加回去就是收到的 `3F 34 33 37`。
    # ⚠ 拿"线上数据域再 +0x33"当期望是**错的口径**: 那等于要求固件多加一次, 会把合法的原样透传
    #   判成 FAIL(第一轮实跑就踩了)。
    exp645 = raw645

    def add(name, ok, detail, crit=None, obs=judge.DEBUG, falsify=None):
        recs.append(rec(name, ok, detail, crit=crit, obs=obs, falsify=falsify))

    def _abort(reason):
        why.append(reason)
        print("   [白盒] %s" % reason)
        # ⚠ `falsify` 七条**照样得写**: 缺了它, 判据会打出"认领它的证据答不出『什么固件会让它 FAIL』"
        #   —— 那是对**没做成**的制度性抱怨, 读的人会当成固件嫌疑(`rs485_dispatch_roundtrip` 同律)。
        # ⚠ 每条名里的 `断[X]` **照着 F 列那四个字母写**(A=DLT698Link.c:307 中继入口,
        #   B=DLT645Link.c:484 透传入口, C=Communicate.c:1129 转发动作, D=Communicate.c:1108 回程)
        #   —— 判据名与 F 列说的是同一件事, 别在这儿另编一套字母。
        for _c, _l, _t, _f in (("①", "A", "698 中继入口命中并归 485 口",
                                "逻辑地址位判错 ⇒ 这帧被当普通帧自己应答, 断点不命中"),
                               ("②", "C", "698 转发动作发生",
                                "落点条件不成立或转发没被调用 ⇒ 断点不命中"),
                               ("③", "C", "698 转发帧文正确",
                                "搬运长度算错/缓冲没清 ⇒ 字节与发出帧不等"),
                               ("④", "B", "645 透传入口命中并归 485 口",
                                "地址判定或功能码分派错了 ⇒ 断点不命中"),
                               ("⑤", "C", "645 透传帧文正确",
                                "漏了 DLT645Link.c:490 那次 +0x33(或重复加) ⇒ 数据域与收到的线上字节不等"),
                               ("⑥", "D", "回程回到请求来向那口",
                                "回程被丢或走错支(回载波口去了) ⇒ 断点不命中"),
                               ("⑦", "D", "回程推的字节数对得上那条应答帧",
                                "搬的是半条帧或连缓冲残渣一起推 ⇒ Len 与帧声明的长度不等")):
            add("断[%s] %s %s" % (_l, _c, _t), None,
                "半途中止: " + reason, crit=_c, falsify=_f)
        return recs, why, "ok"

    print("\n== 7-2 管理芯中继透传 (485 口注入转发给计量芯的帧) ==")
    print("   第 1/2/5 帧中继 698 %d 字节: %s" % (len(f698), bytes(f698).hex(" ").upper()))
    print("   第 3/4 帧中继 645 %d 字节(含 4 个前导 FE): %s"
          % (len(f645), bytes(f645).hex(" ").upper()))
    print("   645 转给计量芯前应是(去 FE, 与收到的线上字节原样) %d 字节: %s"
          % (len(exp645), exp645.hex(" ").upper()))

    # 停住时要读的量。**逐字节列 key 而不是读整数组** —— 与 `frame_dispatch_roundtrip` 同一律:
    # `kwh_num` 吃的就是 `-data-evaluate-expression` 的单值文本, 整数组的文本形态是另一回事。
    # `ukeys` 按**两条帧里更长的那个**取: 698 那条 25 字节、645 那条去 FE 后 16 字节, 一趟读够。
    pkeys698 = tuple("pFrame[%d]" % i for i in range(len(f698)))
    pkeys645 = tuple("pFrame[%d]" % i for i in range(len(raw645)))
    ukeys = tuple("g_UARTMBuf[%d]" % i for i in range(max(len(f698), len(raw645))))
    # 回程那一停读的是**回 485 的目标缓冲**(`g_RS485Buf`), 不是计量芯口那条 —— 地址 `:1100`
    # 就把它从 `g_UARTMBuf` 灌过去了 (`DISP_BUF_DUMP` 与 7-1 读模组缓冲同一个数)。
    bkeys = tuple("g_RS485Buf[%d]" % i for i in range(DISP_BUF_DUMP))

    if sess is None:
        return _abort("本次没有断点会话(没接探针) ⇒ 判①②③④⑤⑥⑦ 没做成")

    # ---- 前置: 链路自证(不认领任何条目, 只为把"没命中"里的台面那一半摘出去) ----
    # 一帧**管理芯自己应答**的 698 读表钟(地址特征 = 管理芯本表, 逻辑地址位 0 ⇒ 不走中继那一支)。
    # 它答得上, 就同时证死三件事: 表在跑、485 通、管理芯 698 栈工作 —— 此后各条中继帧的断点
    # "没命中"就只剩下"固件没走那条路"一种解释, 于是那记 `False`(见本函数 docstring)。
    _ctl = frame_698(build_read_apdu(0x03, oad))
    print("\n   前置 链路自证帧(管理芯自答, 不判条目) %d 字节: %s"
          % (len(_ctl), _ctl.hex(" ").upper()))
    _rx = bytes(send_frame(ser, _ctl, wait=hit_wait, tag="relay_preflight",
                           what="前置链路自证: 管理芯自答的 698 读表钟") or b"")
    # ⚠ 判"答了没有"**先剥前导 0xFE**: 线上那条应答是 `FE FE FE FE 68 …`(4 个前导里 1 个由
    #   `OpenTx_UART4` 直接写、3 个由发送空中断写, 见本文件 `DISP_TX_PRE` 那段)。拿 `startswith(b"\x68")`
    #   判, 答得再好也判成"没成" —— 实测踩过: 表回了 39 字节的合格应答, 整项却被压成未定论。
    if not _rx.lstrip(b"\xfe").startswith(b"\x68"):
        return _abort("前置链路自证没成: 管理芯对自己应答的 698 读表钟帧回的是 %s ⇒ 表没在跑 / "
                      "485 不通 / 管理芯 698 栈不工作 —— 这一轮的中继判据一条都判不了"
                      % (_rx.hex(" ").upper()[:24] or "(一个字都没回)"))

    try:
        # ---- 第 1 帧 698: ① 中继入口 ----
        r1 = sess.with_trigger(bp_698, send_relay_698, ser, f698, oad, timeout=hit_wait,
                               _join=join_extra, _vars=("port", "g_AddrLog") + pkeys698,
                               _pair_name="7-2 第 1 帧 中继 698")
        h1, v1 = r1.get("hit"), r1.get("vars") or {}
        if h1 is None and r1.get("error") is None:
            # 前置已过 ⇒ 不是台面问题 ⇒ 这帧没走中继那一支, 判不通过。
            add("断[A] ① 698 中继入口命中并归 485 口", False,
                "中继 698 帧(地址特征 %02X)发出后 %.1fs 内 %s 没命中 ⇒ 这帧**没被认成"
                "转发给计量芯的帧**(逻辑地址位没取到, 或在更早的地方就被丢掉/自己应答了)"
                % (P.MANAGE_AF | RELAY_AF_LOGIC, hit_wait, _bptxt(bp_698)),
                crit="①", falsify="逻辑地址位判错 ⇒ 这帧被当普通帧自己应答, 断点不命中")
        elif h1 is None:
            add("断[A] ① 698 中继入口命中并归 485 口", None,
                "触发那一侧抛了异常, 帧可能没发出去: %s ⇒ 本次没做成" % r1.get("error"),
                crit="①", falsify="逻辑地址位判错 ⇒ 这帧被当普通帧自己应答, 断点不命中")
        else:
            print("   [白盒] 断[A] 第 1 帧 停在 %s | port=%s | g_AddrLog=%s"
                  % (h1.where(), v1.get("port"), v1.get("g_AddrLog")))
            got1 = vars_bytes(v1, pkeys698)
            print("   [白盒] 解析层拿到的 %d 字节 = %s"
                  % (len(f698), got1.hex(" ").upper() if got1 else "(读不全)"))
            port1 = kwh_num(v1.get("port"))
            add("断[A] ① 698 中继入口命中并归 485 口", port1 == DISP_PORT_485,
                "%s 在 %.1fs 内命中(停在 %s); 入参 port=%s(期望 %d=PT_485_M); "
                "g_AddrLog=%s(置位就该是 1); 这帧的地址特征 = %02X"
                % (_bptxt(bp_698), hit_wait, h1.where(), v1.get("port"), DISP_PORT_485,
                   v1.get("g_AddrLog"), P.MANAGE_AF | RELAY_AF_LOGIC),
                crit="①", falsify="逻辑地址位判错 ⇒ 这帧被当普通帧自己应答, 断点不命中")

        # ---- 第 2 帧 698: ② 转发动作 + ③ 转发帧文 ----
        r2 = sess.with_trigger(bp_relay, send_relay_698, ser, f698, oad, timeout=hit_wait,
                               _join=join_extra, _vars=("port", "Len") + ukeys,
                               _pair_name="7-2 第 2 帧 中继 698(转发落点)")
        h2, v2 = r2.get("hit"), r2.get("vars") or {}
        if h2 is None:
            print("   [白盒] 第 2 帧发出后 %.1fs 内没等到 %s 命中" % (hit_wait, _bptxt(bp_relay)))
            _o2 = None if r2.get("error") else False
            _d2 = ("触发那一侧抛了异常, 帧可能没发出去: %s ⇒ 本次没做成" % r2.get("error")
                   if _o2 is None else
                   "第 2 帧(同一条中继 698)发出后 %.1fs 内 %s 没命中 ⇒ 转发动作**没有被调到**"
                   " —— 要么 ① 那一支根本没往下走, 要么 %s 的落点条件不成立(计量芯口不在空闲态)"
                   % (hit_wait, _bptxt(bp_relay), _bptxt(bp_698)))
            for _c, _t, _f in (("②", "698 转发动作发生",
                                "落点条件不成立或转发没被调用 ⇒ 断点不命中"),
                               ("③", "698 转发帧文正确",
                                "搬运长度算错/缓冲没清 ⇒ 字节与发出帧不等")):
                add("断[C] %s %s" % (_c, _t), _o2, _d2, crit=_c, falsify=_f)
        else:
            print("   [白盒] 断[C] 第 2 帧 停在 %s | port=%s | Len=%s"
                  % (h2.where(), v2.get("port"), v2.get("Len")))
            port2, len2 = kwh_num(v2.get("port")), kwh_num(v2.get("Len"))
            add("断[C] ② 698 转发动作发生", port2 == RELAY_PORT_METER and len2 == len(f698),
                "转发动作被调到(落在 %s), 入参 port=%s(期望 %d=PT_UARTM 计量芯口) / Len=%s(期望 %d)"
                % (h2.where(), v2.get("port"), RELAY_PORT_METER, v2.get("Len"), len(f698)),
                crit="②", falsify="落点条件不成立或转发没被调用 ⇒ 断点不命中")

            gotu = vars_bytes(v2, ukeys[:len(f698)]) if len2 == len(f698) else None
            if gotu is None:
                add("断[C] ③ 698 转发帧文正确", None,
                    "计量芯口缓冲这一次没读全(或 Len 不是 %d, 读的区间没对齐) ⇒ 本次没做成" % len(f698),
                    crit="③", falsify="搬运长度算错/缓冲没清 ⇒ 字节与发出帧不等")
            else:
                same3 = (gotu == bytes(f698))
                add("断[C] ③ 698 转发帧文正确", same3,
                    "计量芯口 buff 起 %d 字节 %s; 发出的是 %s; 逐字节%s"
                    % (len(gotu), gotu.hex(" ").upper(), bytes(f698).hex(" ").upper(),
                       "全等" if same3 else "**不等**"),
                    crit="③", falsify="搬运长度算错/缓冲没清 ⇒ 字节与发出帧不等")

        # ---- 第 3 帧 645: ④ 透传入口 ----
        r3 = sess.with_trigger(bp_645, send_relay_645, ser, f645, timeout=hit_wait,
                               _join=join_extra, _vars=("port", "stAddr") + pkeys645,
                               _pair_name="7-2 第 3 帧 中继 645")
        h3, v3 = r3.get("hit"), r3.get("vars") or {}
        if h3 is None and r3.get("error") is None:
            add("断[B] ④ 645 透传入口命中并归 485 口", False,
                "中继 645 帧(CMD 0x%02X, 地址=本表地址)发出后 %.1fs 内 %s 没命中 ⇒ 这帧**没进"
                " 0x91/0x94/0xE1/0xE4 四档共用那段体**(功能码没分派到它, 或地址没判成 ADR_ABS)"
                % (RELAY_CMD_645_REPLY, hit_wait, _bptxt(bp_645)),
                crit="④", falsify="地址判定或功能码分派错了 ⇒ 断点不命中")
        elif h3 is None:
            add("断[B] ④ 645 透传入口命中并归 485 口", None,
                "触发那一侧抛了异常, 帧可能没发出去: %s ⇒ 本次没做成" % r3.get("error"),
                crit="④", falsify="地址判定或功能码分派错了 ⇒ 断点不命中")
        else:
            got3 = vars_bytes(v3, pkeys645)
            print("   [白盒] 断[B] 第 3 帧 停在 %s | port=%s | stAddr=%s"
                  % (h3.where(), v3.get("port"), v3.get("stAddr")))
            print("   [白盒] 解析口拿到的 %d 字节 = %s"
                  % (len(raw645), got3.hex(" ").upper() if got3 else "(读不全)"))
            port3 = kwh_num(v3.get("port"))
            add("断[B] ④ 645 透传入口命中并归 485 口", port3 == DISP_PORT_485,
                "%s 在 %.1fs 内命中(停在 %s); 入参 port=%s(期望 %d=PT_485_M); "
                "地址判定 stAddr=%s(ADR_ABS 才进这个 if); 解析口拿到 %s"
                % (_bptxt(bp_645), hit_wait, h3.where(), v3.get("port"), DISP_PORT_485,
                   v3.get("stAddr"), got3.hex(" ").upper() if got3 else "(读不全)"),
                crit="④", falsify="地址判定或功能码分派错了 ⇒ 断点不命中")

        # ---- 第 4 帧 645: ⑤ 透传帧文正确 ----
        r4 = sess.with_trigger(bp_relay, send_relay_645, ser, f645, timeout=hit_wait,
                               _join=join_extra, _vars=("port", "Len") + ukeys,
                               _pair_name="7-2 第 4 帧 中继 645(转发落点)")
        h4, v4 = r4.get("hit"), r4.get("vars") or {}
        if h4 is None and r4.get("error") is None:
            add("断[C] ⑤ 645 透传帧文正确", False,
                "第 4 帧(同一条中继 645)发出后 %.1fs 内 %s 没命中 ⇒ 转发动作**没有被调到**"
                " —— 要么 ④ 那一支根本没往下走, 要么 %s 的落点条件不成立(计量芯口不在空闲态)"
                % (hit_wait, _bptxt(bp_relay), _bptxt(bp_645)),
                crit="⑤", falsify="漏了 DLT645Link.c:490 那次 +0x33(或重复加) ⇒ 数据域与收到的线上字节不等")
        elif h4 is None:
            add("断[C] ⑤ 645 透传帧文正确", None,
                "触发那一侧抛了异常, 帧可能没发出去: %s ⇒ 本次没做成" % r4.get("error"),
                crit="⑤", falsify="漏了 DLT645Link.c:490 那次 +0x33(或重复加) ⇒ 数据域与收到的线上字节不等")
        else:
            len4 = kwh_num(v4.get("Len"))
            got4 = vars_bytes(v4, ukeys[:len(exp645)]) if len4 == len(exp645) else None
            if got4 is None:
                add("断[C] ⑤ 645 透传帧文正确", None,
                    "计量芯口缓冲这一次没读全(或 Len=%s 不是 %d[=645 帧去 FE 后的长度], 读的区间"
                    "没对齐) ⇒ 本次没做成" % (v4.get("Len"), len(exp645)),
                    crit="⑤", falsify="漏了 DLT645Link.c:490 那次 +0x33(或重复加) ⇒ 数据域与收到的线上字节不等")
            else:
                same5 = (got4 == exp645)
                add("断[C] ⑤ 645 透传帧文正确", same5,
                    "停在 %s, Len=%s; 计量芯口 buff 起 %d 字节 %s; 期望(收到的线上字节原样) %s; 逐字节%s"
                    % (h4.where(), v4.get("Len"), len(got4), got4.hex(" ").upper(),
                       exp645.hex(" ").upper(), "全等" if same5 else "**不等**"),
                    crit="⑤", falsify="漏了 DLT645Link.c:490 那次 +0x33(或重复加) ⇒ 数据域与收到的线上字节不等")

        # ---- 第 5 帧 中继 698: ⑥⑦ 回程回到请求来向那口 ----
        # ⚠ 这一帧与前四帧是**同一条**: 中继 698 本来就是"主站要发给计量芯"的样子, 计量芯认它就答,
        #   答回来的东西在 `:1096 else if (port == PT_UARTM)` 那一支里被搬回 485。停的那一句
        #   `Start_Relay_Frame(PT_485_M, Len);` 端口是**写死在参数里的字面量**, 所以"命中"本身
        #   就答了"回到的是 485 那一口"(回载波口的是 :1120, 另一行, 停不到这儿)。
        r5 = sess.with_trigger(bp_back, send_relay_698, ser, f698, oad, timeout=back_wait,
                               _join=join_extra, _vars=("port", "Len") + bkeys,
                               _pair_name="7-2 第 5 帧 中继 698(等回程)")
        h5, v5 = r5.get("hit"), r5.get("vars") or {}
        back5 = b""
        try:
            back5 = bytes(r5.get("result") or b"")
        except Exception:
            back5 = b""
        if h5 is None or r5.get("error"):
            # ⑥ 判不判得死, 取决于**回程有没有被走到**: 前面 ② 或 ⑤ 命中 = 管理芯确实把帧转给了
            # 计量芯, 那之后应答没被转回 485 就是整表这一路不通 ⇒ 判不通过。前面就断了(一个转发
            # 落点都没命中)时, 回程根本没被走到, 这一条无从谈起 ⇒ 记"没做成"(本轮的账已由 ②⑤ 认领)。
            _fwd = (h2 is not None) or (h4 is not None)
            _why5 = ("%s 在 %.1fs 内没命中" % (_bptxt(bp_back), back_wait)
                     if h5 is None else "命中后读变量出错: %s" % r5.get("error"))
            _tail5 = ("; 485 口这一次收到 %d 字节: %s" % (len(back5), back5.hex(" ").upper())
                      if back5 else "; 485 口这一次没收到任何字节")
            if r5.get("error"):
                _o6 = None
                _d6 = "触发那一侧抛了异常, 帧可能没发出去: %s ⇒ 本次没做成" % r5.get("error")
            elif _fwd:
                _o6 = False
                _d6 = ("%s ⇒ 管理芯已经把帧转给了计量芯(②/⑤ 命中过), 却没有帧走到“回 485”"
                       "那一支%s —— **回程段(计量芯→管理芯→485)没转回来**" % (_why5, _tail5))
            else:
                _o6 = None
                _d6 = ("%s ⇒ 本次一个转发落点都没命中, 回程根本无从走到 ⇒ 本次没做成"
                       "(这一轮的账由 ②⑤ 认领)%s" % (_why5, _tail5))
            add("断[D] ⑥ 回程回到请求来向那口", _o6, _d6,
                crit="⑥", falsify="回程被丢或走错支(回载波口去了) ⇒ 断点不命中")
            add("断[D] ⑦ 回程推的字节数对得上那条应答帧", None,
                "⑥ 没命中 ⇒ 没有 Len 可比 ⇒ 本次没做成",
                crit="⑦", falsify="搬的是半条帧或连缓冲残渣一起推 ⇒ Len 与帧声明的长度不等")
        else:
            print("   [白盒] 断[D] 第 5 帧 停在 %s | port=%s | Len=%s"
                  % (h5.where(), v5.get("port"), v5.get("Len")))
            add("断[D] ⑥ 回程回到请求来向那口", True,
                "%s 在 %.1fs 内命中(停在 %s): 这一句是 Start_Relay_Frame(PT_485_M, Len), "
                "端口是字面量 ⇒ 这条应答回到的是 485 那一口(请求的来向); "
                "485 口总共收到 %d 字节: %s"
                % (_bptxt(bp_back), back_wait, h5.where(), len(back5),
                   back5.hex(" ").upper() if back5 else "(串口这一侧没收到, 但断点先命中了)"),
                crit="⑥", falsify="回程被丢或走错支(回载波口去了) ⇒ 断点不命中")

            len5 = kwh_num(v5.get("Len"))
            got5 = vars_bytes(v5, bkeys[:len5]) if (len5 and len5 <= len(bkeys)) else None
            if got5 is None:
                add("断[D] ⑦ 回程推的字节数对得上那条应答帧", None,
                    "Len=%s 读不出或大于一次能读的 %d 字节 ⇒ 本次没做成"
                    % (v5.get("Len"), len(bkeys)),
                    crit="⑦", falsify="搬的是半条帧或连缓冲残渣一起推 ⇒ Len 与帧声明的长度不等")
            else:
                self5 = reply_frame_len(got5)
                if self5 is None:
                    add("断[D] ⑦ 回程推的字节数对得上那条应答帧", None,
                        "推回 485 的头 %d 字节 %s 认不出帧头(645 要 [7]==0x68, 698 要 [0]==0x68)"
                        " ⇒ 没做成(推的不是可解析的帧)" % (len(got5), got5.hex(" ").upper()),
                        crit="⑦", falsify="搬的是半条帧或连缓冲残渣一起推 ⇒ Len 与帧声明的长度不等")
                else:
                    same7 = (self5 == len5)
                    add("断[D] ⑦ 回程推的字节数对得上那条应答帧", same7,
                        "停在 %s, Len=%s(固件实际推回 485 的字节数); 目标缓冲起 %d 字节 %s; "
                        "这条帧自己声明 %d 字节 ⇒ %s"
                        % (h5.where(), v5.get("Len"), len(got5), got5.hex(" ").upper(), self5,
                           "对得上" if same7 else "**对不上**(推少了=半条帧, 推多了=连缓冲残渣一起推)"),
                        crit="⑦", falsify="搬的是半条帧或连缓冲残渣一起推 ⇒ Len 与帧声明的长度不等")
        return recs, why, "ok"
    finally:
        # 撤断点只能在停住态(见 `breakpoint.clear_breaks` 的 ⚠: 核一跑起来 MI 命令就石沉大海)。
        # `with_trigger` 收 (文件,行号) 时命中路径会趁停住自撤、没命中时自己补撤; 这里兜底
        # "已命中但后面某步抛了"那种残局 —— 留着断点它自己会把核撂停, 其后每条串口帧整帧无应答。
        try:
            if sess.breakpoints():
                sess.ensure_stopped()
                sess.clear_breaks()
        finally:
            sess.ensure_running()


def _cli(argv):
    ensure_utf8_stdout()
    args, fl = _strip_flags(argv)
    if not args:
        print(__doc__)
        cmd_cases(False)
        return 0
    cmd = args[0]
    try:
        if cmd == "list":
            return cmd_list(args[1] if len(args) > 1 else None, fl["json"])
        if cmd == "search":
            return cmd_search(args[1] if len(args) > 1 else "", fl["json"])
        if cmd == "show":
            if len(args) < 2:
                print("show 需帧 id/别名"); return 2
            return cmd_show(args[1], fl["json"])
        if cmd == "dry":
            if len(args) < 2:
                print("dry 需帧 id/别名"); return 2
            return cmd_dry(args[1], fl["json"], param=fl["param"], chip=fl["chip"])
        if cmd == "send":
            if len(args) < 2:
                print("send 需帧 id/别名"); return 2
            res = send(args[1], wait=fl["wait"], _json=fl["json"], param=fl["param"],
                       chip=fl["chip"])
            if fl["json"]:
                _pjson(res)
            return verdict_exit(res.get("verdict"))   # 守住契约: FAIL 必须非 0
        if cmd == "smoke":
            return smoke(wait=fl["wait"], actions=fl["actions"], raw=fl["raw"],
                         repeat=fl["repeat"], _json=fl["json"])
        if cmd == "cases":
            return cmd_cases(fl["json"])
        if cmd == "verbs":
            return cmd_verbs(args[1] if len(args) > 1 else None, fl["json"])
        if cmd == "plan":
            return plan(args[1] if len(args) > 1 else None)
        if cmd == "runcase":
            if len(args) < 2:
                print("runcase 需用例 id; 可用: cases"); return 2
            return runcase(args[1], wait=fl["wait"], dry=fl["dry"], _json=fl["json"])
        print("未知命令 %r" % cmd)
        print(__doc__)
        return 2
    except KeyError as e:
        print("错误:", e)
        return 2


def main():
    try:
        return _cli(sys.argv[1:])
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2


if __name__ == "__main__":
    sys.exit(main())
