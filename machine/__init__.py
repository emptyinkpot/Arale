# -*- coding: utf-8 -*-
r"""
machine/ —— **装机卡带**: "这台机器的事实"(换机器 = 换 CURRENT 指向; 与换表正交)。

本仓有两盘**正交**的卡带, 别混:

    project/   本表环境包 —— 这块**表**的事实(表号 / 双芯AF / RAM 地址图 / .out 路径)
    machine/   装机卡带   —— 这台**机器**的事实(gdb / J-Link / 串口 / 工具链路径)

拆开的理由(2026-09-11): 这两件事原先混在一起 —— `breakpoint.py`/`probe.py`/`restore.py`/
`_install_gdb.py` 各抄一份"gdb 在哪、SN 多少", `launch.json` 里还有三份, COM 口在
`ez_meter.open_com` 的默认值与 `project/*.meta.json` 里各一份。**换一台电脑要满仓找**。
现在: 值只在本包画像里写一次, 引擎经 `common.machspec` 取。

    装机卡带 = 一组建前缀 <画像名> 的数据 + 一个自检:
      <画像名>.py          ① 数据画像(纯常量): 工具路径 / 探针 SN / 串口 / 工作区 / 能力开关
      <画像名>.meta.json   ② 环境清单: **验收期望**(工具版本与能力、Python 包、镜像文件清单)
      env_check.py         ③ 装包即验: env_check(online=?) 断言本机条件在不在
    换一台电脑: 加一份同款两件套(前缀换成该机名), 把本文件的 CURRENT 指过去;
    引擎(swdbg/meterlib)与脚本原样复用。env_check 永远验 CURRENT。

    加一台新机器: 照 `win11_c07751.py` 写一份 `<机器名>.py`(gdb / J-Link 路径、器件、SWD 频率、
    端口、工作区根、依赖的 Python 包), 写 `<机器名>.meta.json` 声明**验收期望**;
    再把本文件的 CURRENT 指过去, 跑 `python -m machine.env_check` 验到 `READY`。

**这里还是"组合根"**(与 `project/__init__.py` 同构)
--------------------------------------------------
引擎(swdbg 等)原先各自写着 `GDB = r"E:\programfile\..."` —— 机器条件焊死在引擎里。现在
引擎只认识中立的 `common.machspec`, **由本文件(卡带自己)把画像装上去** —— 即"装卡带"这个
动作的正式落点。

    machine ──→ common.machspec ←── swdbg / meterlib / scripts

依赖方向因此是 machine → common(向下), **不是** swdbg → machine(横向)。改 import 前先看
`common/__init__.py` 头部那张依赖图。下面那句 configure 必须在 import CURRENT 之后。

⚠ 与 `project/` 的分寸: 本包**不许** import meterlib / swdbg / discover / project,
   也不许反向被它们 import(它们只经 `common.machspec` 取)。由 `env_check.py` 的 AST 扫描守着。

这台机器上有哪两条链路
--------------------
环境有**两条互不相干的物理链路**, 所有观测都架在它们上面。本包声明的就是这两条的事实。

| | 串口链路 | 调试链路 |
|---|---|---|
| 物理 | USB-485 桥(CP210x) — RS485 — 表 UART1 | 探针 — **SWD**(2 线 SWCLK/SWDIO) — 管理芯 **SecurCore SC000**(J-Link PLUS 或 CMSIS-DAP 类如 DAPLink, **两端通吃**) |
| 软件栈 | pyserial 与虚拟串口 COMx; 失败回退 **RawCom** | `gdb-multiarch` 与 **GDB RSP / TCP:2331** 与 gdbserver(J-Link 端 `JLinkGDBServerCL`, CMSIS-DAP 端 `pyocd gdbserver`) |
| 参数 | 9600 / 8 / **E** / 1 | J-Link 端器件填泛型 **Cortex-M0** / CMSIS-DAP 端 `DAP_TARGET=cortex_m`, SWD **1 MHz**(两端都要显式给速度) |
| 谁不认表 | 库不写死口名、不写死 SN | 同上(判据代替死值) |

体系全貌那张图(两条链路 × 四条通路 × 两个触发通道)在 `计划/图/体系全貌_v2.drawio`, README 里嵌了导出图。
"""

from . import win11_c07751 as CURRENT      # 当前活动装机画像(唯一换机器点)

from common import machspec   # 中立层; 本包是它唯一的装配方
machspec.configure(CURRENT)
