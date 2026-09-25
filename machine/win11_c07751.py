# -*- coding: utf-8 -*-
"""
machine/win11_c07751.py —— 本机(Windows 11 / 用户 C07751)的**装机画像**: 纯常量, 全是数据。

这里是"这台机器的条件"的**单一事实源**。原先它们散在五处(breakpoint.py / probe.py / restore.py /
scripts/_install_gdb.py / .vscode/launch.json×3), 改一个 SN 要满仓找 —— 本文件就是那份收敛。

**只放机器条件, 不放表条件**: 表号 / RAM 地址图 / .out 路径属 `project/`(那盘卡带), 别写这儿。
判据: "换一台电脑要改的值"归本文件; "换一块表要改的值"归 project/。

换机器(新工位/新 J-Link): 复制本文件改成 `<新机名>.py`, 把 `machine/__init__.py` 的 CURRENT 指过去。
有值没填的项写 None —— env_check 会点名(且 `machspec.require()` 会当场拦下"拿 None 去连探针")。

本机的外网: `github.com` 直连不通(代理 `127.0.0.1:7890` 配了但 ProxyEnable=0);
ARM 官方 CDN、清华/阿里 pypi、gitee、marketplace 均通 —— 绕开 GitHub 够用。装包前先认这一条。
"""

# ============================ 身份 ============================
MACHINE = "win11-c07751"
OS = "Windows 11 Pro 10.0.26200"

# ============================ 调试链工具(装一次, 全机可用) ============================
# gdb —— 必须是**带 Python scripting 的多架构 gdb**(2026-09-11 定案):
# ARM 官方那份 manifest 明写 `--with-python=no`, `python print(1)` 直接答 "Python scripting is not
# supported" —— 于是 breakpoint 的 gdb 侧喂狗钩子装不上, 只能退到 python 侧补喂, 按 judge 口径属**降级**。
# ⚠ 取 `gdb-multiarch.exe`(31MB, 全目标)**不是**同目录的 `gdb.exe`(10.5MB, 只有 x86 宿主目标 ——
#   载 ARM 的 .out 后架构停在 i386)。两者同名同目录, 极易拿错。
GDB = r"E:\programfile\gdb-multiarch\mingw64\bin\gdb-multiarch.exe"
GDB_SERVER = r"C:\Program Files\SEGGER\JLink_V818\JLinkGDBServerCL.exe"
JLINK = r"C:\Program Files\SEGGER\JLink_V818\JLink.exe"     # Commander(restore.py 用它发包)
ARM_TOOLCHAIN = r"E:\programfile\gcc-arm-none-eabi\bin"     # 交叉编译器(无宿主 make)

# ============================ J-Link 探针 ============================
# **判据, 不是死值**(2026-09-14 起): 真连表时由 `swdbg/jlink.py:resolve_sn` 枚举 J-Link 后按它认。
#   填一支 SN = 钉死这一支(台上不止一支探针时用这个, 或者你就是想确定连的是哪支);
#   填 None   = **自动** —— 恰好枚举到一支才认, 0 支 / 多支都**当场抛**并列出实际连着的。
# 为什么不许"取第一支": 换支 J-Link 后"连不上"与"表挂了/线断了"现象一模一样, 静默猜 = 把
# 一个确定错换成一个看着一样的错。改这里一处, breakpoint / probe / restore 全部跟着变。
JLINK_SN = 609788888      # J-Link PLUS; None = 自动(恰好一支才认)
DEVICE = "Cortex-M0"      # **J-Link 的泛型器件名**, 值不许改: 管理芯实为 SecurCore SC000
                          #   (CPUID 0x410CC300), 但 SWD 与 ARMv6-M 指令集一致, 填泛型 M0 一直能用
                          #   (IAR 的 .jlink 也这么写)。见 swdbg/probe_cmsis.py 文件头。
IFACE = "SWD"
SPEED = 1000              # kHz —— **必须显式**(两端都是)。不给速度 pylink 会退化成 speed='auto',
                          #   那一步往目标 RAM 下自测代码(实测报 "Verification of test code
                          #   downloaded into RAM failed"); pyOCD 则会自己挑一个。见 probe.py 铁律 1。
                          #
                          # ⚠ 2026-09-20 **试过降到 100, 已还原, 别再试**: 换到 DAPLink 后 gdb 会话
                          #   会丢链路(`SWD/JTAG communication failure (No ACK)`), 且同会话内再不恢复。
                          #   为分开"时钟余量不足"与别的病因, 把这一行改成 100 实测跑了一遍
                          #   `_test_9_2_rev_power.py`: **No ACK 仍 4 条、仍在约 4s 处、仍是同一形状**,
                          #   当场硬失败(interrupt 超 10s 停不住)。**时钟降 10 倍毫无改善 ⇒ 不是时序余量
                          #   那一类问题, 降速这条路已经实测排除**, 不必再试。
GDB_PORT = 2331           # GDB Server 监听口; 与 VS Code cortex-debug 抢口时换

# ============================ 探针: 用哪一端(2026-09-20 双端) ============================
# 台上有两种探针可用: J-Link PLUS(pylink)与 CMSIS-DAP 类如 DAPLink(pyOCD)。对上层完全透明 ——
# `swdbg/probesel.py` 判"该用哪一支"(判据→候选→**逐个真连读 CPUID 证明**→恰好一支才算数)。
#   PROBE = None         = 两端都找(推荐: "只要把探针插上就行"的落点)
#   PROBE = "jlink"      = 只用 J-Link 那一端
#   PROBE = "cmsis-dap"  = 只用 CMSIS-DAP 那一端
# ⚠ 写别的值**当场抛** —— 拼错一个字母就退化成"自动找", 那正是要消灭的静默猜测。
PROBE = None

DAP_UID = None            # CMSIS-DAP 那一支的 UID; None = 自动(该端恰好一支才认)。
                          #   与上面的 `JLINK_SN` 同一条分寸: 台上不止一支时钉死它。
DAP_TARGET = "cortex_m"   # pyOCD 的目标名。⚠ **不是** `DEVICE`(那个是 J-Link 的泛型 M0 名),
                          #   也**不是** `cortex_m0` —— 管理芯不是 M0, 见 DEVICE 那行的注释。
                          #   通用目标即可: pyOCD 按 CPUID 的架构字段定架构, partno 只影响显示名。
DAP_VIDPID = (0x0D28, 0x0204)   # 体检时用来报"这一族的探针在不在 USB 上"(DAPLink 的 VID:PID)

# ============================ 串口(USB-485 桥接 CP210x) ============================
# DL/T645 + DL/T698.45 同口。发数据走这里。
# **`COM` 是"钉死"的逃生判定, 不是默认**(2026-09-14 起): 平常走下面的判据自动识别。
#   填 None   = **自动识别**(推荐, 也是"只要把线插上就行"的落点):
#               `common/portsel` 按 `COM_MATCH` 筛候选 → 用**装配进来的探活动作**(`meterlib/p698.py`
#               在 import 时 `set_probe(handshake_clock)` 装的"发一帧 698 读表钟")逐个证明
#               "口后面是**本表**" → 唯一命中才用。0 候选 / 多候选 / 全握手失败 → **当场抛**。
#   填 "COM3" = 钉死(跳过筛选; 仍握手, 除非 open_com(..., probe=False))。应急/调试/台上有
#               两块同型号桥时用。
# ⚠ 判据**不是**"系统里有这个口"就算数 —— 那只证明桥插着, 不证明桥后面是我们那块表。
COM = None
COM_MATCH = {              # 自动识别的判据(填了的字段必须全中; None = 不筛那一项)
    "vid":    0x10C4,      # Silicon Labs
    "pid":    0xEA60,      # CP210x
    "serial": None,        # None = 认型号不认这一根线; 填 "0001" = 连这根桥都钉死
    "desc":   "CP210x",    # 描述含此子串(双保险 —— 有的驱动 desc 里不写型号)
}
BAUD = 9600                # ⚠ 下面三项原先只在卡带里声明、**没被吃过**(open_com 把 8E1 写死在函数体里);
PARITY = "E"               #   2026-09-14 起 open_com 从卡带取 —— 改这里才真的改得动串口参数。
BYTESIZE = 8
STOPBITS = 1

# ============================ 工作区 ============================
# 两块芯片的 IAR 工程源码放哪(表自己的 .out/源码根声明在 project/*.meta.json 的 firmware 块)。
# 这里只声明"工作区在哪"这一件事, 具体某块表的路径不重复 —— 免得同一事实两处落笔。
MENGXI_ROOT = r"E:\My Work\MengXi"
MANAGE_CHIP_DIR = "EZ315-FM33A0610EV-APP"     # 管理芯(IAR 工程; 白盒测试的符号来源)
METER_CHIP_DIR = "EZ315-8611"                 # 计量芯(IAR 工程)

# ============================ 能力开关 ============================
# 本机接没接 J-Link。置 False = 显式声明"这台机器没有 J-Link", 于是需要探针的观测该**明着降级**
# (而不是撞 FileNotFoundError 后被人当成"固件有问题")。env_check 会据此改判。
# ⚠ 它与 `PROBE` 不是一回事: 这里说的是"**这种**探针在这台机器上有没有", `PROBE` 说的是
#   "这一次**该用**哪一端"。本机两种都有过(2026-09-20 DAPLink 上台), 故两者都留。
HAS_JLINK = True

# Python 包的清单**不在这儿**: 那是"这个**软件**依赖什么"(换谁开发、换哪台机器都一样), 归仓根
# `pyproject.toml`。本文件只管"换**这台**电脑要改的值"。2026-09-20 从本文件挪走 —— 原先挂着它,
# 是把这两件事按错了轴。别再加回来。
