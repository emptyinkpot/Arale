# -*- coding: utf-8 -*-
"""
meterlib —— EZ315 帧收发基础库(纯源码包; __init__ = C 里的"汇总头").

用 C 的 .c/.h 心智看, 本包是这几个 .c:
    p698.py                         = DL/T698.45 协议控制器(组帧/校验/解码)
    p645.py                         = DL/T645-07 协议控制器(组帧/校验/解码)
    ble.py                          = 蓝牙透传**通道**(找模组/连上/发帧/收帧; 无协议)
    cmd_bank.py                     = 设备类驱动(帧目录 + 语义动词 + CLI), 调用上面三个
    watch.py                        = AA80 只读观察簇(按名直读管理芯 RAM / 快照 / diff)
    __init__.py                     = 汇总头, 让 from meterlib import cmd_bank 一步到位
⚠ 本包**没有**同夹的数据文件。本表实测验证帧由**卡带**声明 —— 画像里的 `FRAMES_FILE`
  (本仓 = `project/ez315_fm33a0610.frames.json`); 画像没声明时 `cmd_bank` 才回退找同夹的
  `user_frames.json`(那个名字是**兜底**, 本仓并不存在这个文件, 别去 src/meterlib/ 找它)。

引用方式(父目录 帧收发基础 在 sys.path 上即通 —— 顶层运行脚本自己目录就在 path 上,
CLI 用 python -m meterlib.cmd_bank, 自带 path 引导):
    from meterlib import cmd_bank as CB                 # 语义操作 + 帧目录 + CLI
    from meterlib import watch as W                     # AA80 只读观察簇(白盒通路, 与语义动作分家)
    from meterlib.p698 import frame_698, ...            # 698 组帧/校验/解码
    from meterlib.ble import open_link                  # 蓝牙透传通道(同 portsel.tx_recv 的用法)
    from common.portsel import open_com                 # 串口(选口 + 收发)
    CB.set_meter_clock_set(ser, "YYYY-MM-DD HH:MM:SS", chip="计量芯")

CLI(在 帧收发基础/ 下):
    python -m meterlib.cmd_bank list / send <id> / runcase 4-1 ...
"""
# 注意: 此处不要 `from . import cmd_bank` —— 那会把 cmd_bank 提前装入 sys.modules,
# 导致 `python -m meterlib.cmd_bank` 报 RuntimeWarning。子模块全部惰性加载:
#   `from meterlib import cmd_bank` 由 Python 自动按需导入。
#
# 白盒观察簇住 watch.py: 把 IAR「断点+Watch」搬成 485 上可自动观察 ——
#   watch_vars / aa80_ram_snapshots / named_blocks / read_mem_aa80 / WatchBank 快照·diff·
#   wait_change(AA80 直读 RAM 只读, 不停 CPU)。
#   ⚠ 它是**一条独立的观察通路**, 故独立成文件、不与语义动作同住: 谁用谁
#     `from meterlib import watch as W`, 没有中转。它只 import common.* 与 p645。
#
# 2026-09-18 本包瘦身(搬出去两个不认协议、也不是驱动的成员):
#   trial.py        -> common/trial.py      零 meterlib 依赖, 是运行外壳不是驱动(改 25 处 import)
#   watch_runner.py -> scripts/watch_runner.py  全仓零个模块 import 它, 是入口工具不是库
#   本包于是只剩: p645 / p698 (一个协议一个文件, 并列) + cmd_bank (调用它俩的设备类驱动)
#                 + watch (AA80 只读观察通路)。
__all__ = ["cmd_bank"]
