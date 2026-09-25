"""
swdbg/breakpoint.py —— 断点这件事的全部: 写法 / 源码侧事实 / 停核解地址

同一份事实原先分住两个文件, 改一处要记得改另一处 —— 现在合成一份, 三段依次是:

    一、写法的唯一定义        KINDS / normalize / text
    二、断点那一刻的源码侧事实  facts / render
    三、解地址与停核          Session 及其上的动词(fire_hit / wait_hit / inject_* …)

一二两段**只吃 `.c` 文本**: 不读 `.out`、不连探针、不认表 —— `scripts/_check_anchors.py`
靠的正是这一点(它要不拖探针就核任何一块表的断点)。第三段才连表, 下面从「为什么要有它」起
讲的都是这一段。

为什么要有它
------------
probe.py 那条路(J-Link AHB-AP 背景读)**只读全局**, 而且**刻意不停核** —— 这是它存在的价值,
不该动。但规格里有一整类判据是"**执行到某个文件:行才成立**"的指令路径硬证, 以及"**停在函数体
里才看得见**的局部量":

    · 5-2 / 9-2 / 9-3 的触发源是「C-SPY 注入门槛」—— 桌测无标准源, 瞬时量恒 0, 不注入就没有触发;
    · 1-3 要改 g_DispPara 看显示位数;
    · 4-6 要证「改结算日确经 Chg_BillDayM 这个入口」而非从记录反推。

这类事过去只能在 IAR 里手点。本模块把它们变成**可脚本化**的: `arm-none-eabi-gdb` 读的是**同一份
.out / 同一份 DWARF**, IAR 断点能看到的局部量, 这里一样看得到(反之亦然 —— DWARF 里没有位置的量,
换工具也读不到)。

与 probe.py 的关系
------------------
     probe.py    只读全局, **不停核**, 零依赖, 任何时候都能用
     breakpoint.py   停核 + 下断点 + 读局部量 + 受控注入   ← 本文件

**两者不是替代关系**。能不停核解决的判据, 一律走 probe(见 CLAUDE.md「停核 = 表不应答串口」)。

两端(2026-09-20 起)
-------------------
底下是 J-Link 还是 CMSIS-DAP 类探针, 对本模块**同样透明**: `swdbg/probesel.py` 认定该用哪一支
(真开一次会话读 CPUID `0xE000ED00` 证明它后面就是本表那颗核), 本模块拿它给的那一支去起 server。
两端**只有一个字不同** —— server 的命令行(`_server_cmd`):

    J-Link 端    JLinkGDBServerCL  -select usb=<SN> -device … -if … -speed … -port …
    CMSIS-DAP 端 python -m pyocd gdbserver -u <UID> -t cortex_m -f <Hz> -p <端口> -O connect_mode=attach

收尾、复核、断点、注入、喂狗**全部共用一份** —— 那两个 server 在 MI 这一层是同一个协议,
照抄两份必然漂移。⚠ DAP 端的收尾**不发 stdin**(pyOCD 的 gdbserver 没有命令行控制台),
只等它自退; 详见 `_shutdown_server`。

⚠⚠ 停核的代价(用之前必须知道的四件事)
--------------------------------------
1. **停住时表完全不应答串口** —— 645/698 一条都收不到回。所以**串口与 gdb 必须在同一个进程里交错**:
   先 `go()` 放行核心 → 再发串口帧 → 表命中断点停住 → `wait_hit()` 读局部量 → `resume()` 放行 →
   串口那边才收得到应答。**分两个进程靠掐时间必然翻车**, 这就是本模块必须提供非阻塞 `go()` 的原因。
2. **看门狗照跑** —— 停住超过约 8s, 核心被 IWDT 复位, 断点全丢、整轮测试报废。故本模块强制要求
   传入 `watchdog=(addr, magic)`: 装上 gdb 侧 `hook-stop` **每次停下立刻喂狗**(万一 python 正忙着
   读串口, 喂狗也不会漏), python 收到 `*stopped` 时再补喂一次并记账。**不给 watchdog 就不许下断点**。
3. **Cortex-M0 只有 4 个硬件断点槽** —— 一次会话最多 4 个, 超了 gdb 会报错(本模块会把错误抛出来,
   不静默降级成软件断点 —— 软件断点要改 Flash, 对这块表是另一回事, 不许)。
4. **绝不平白无故 halt** —— `close()` 的收尾是「detach → gdb 退出 → server 干净退出 → 复核 DHCSR」,
   复核发现核心还被撂着就自动叫 `swdbg.restore.release_debug()` 兜底。**绝不 terminate 一个还活着的
   GDB Server**(强杀正是把核心撂在 halt 的元凶, 见 restore.py 文件头)。

⚠⚠ mi-async —— "核心跑着的时候, MI 还收不收命令?"(2026-09-10 实测定案, **改这一块前先读**)
------------------------------------------------------------------------------------------
默认(gdb 的 all-stop 同步模式)下, **gdb 发出 continue 之后就阻塞在 remote 等 stop reply, 期间一条
MI 命令都不服务**: 实测 `-break-delete`、纯查询 `-data-list-register-names`、乃至唯一能把核叫停的
`-exec-interrupt`, **全都石沉大海**(各等 5-10s 一条记录都没有), 而同一个断点在核**停住**时删是秒回 `^done`。

开 `set mi-async on` 之后这一切消失(2026-09-10 实测: 跑动中发纯查询秒回 `^done` 带全寄存器表,
`-exec-interrupt` 秒回 `^done` 并把核停在 `Comm_Service() at Communicate.c:273`)。这正是 VS Code /
cortex-debug 这类 IDE 前端赖以工作的设置 —— 没有它, IDE 在程序跑起来之后就再也发不出任何命令了。

**两个必须记住的点**:
* **只能在 gdb 启动命令行上设**(`-ex "set mi-async on"`)。连上 remote 之后再发 `-gdb-set mi-async on`
  会被拒: `^error,msg="Cannot change this setting while the inferior is running."` —— gdb 一连上远端
  目标就把 inferior 视作 running, 而那正是"还没跑、想先把开关设好"的当口。见 `_start_gdb`。
* **别把"跑动中 MI 无应答"当成 gdb/MI 的固有缺陷**。2026-09-10 我为这个(其实是配置缺失的)根因
  打了一圈守卫: `clear_breaks()` 跑动时直接拒绝、`drop=`/`_drop=` 趁停住撤断点、`close()` 只在停住时
  detach、`_outstanding` 水位记账。**这些现在是"保守"而不是"必需"** —— 留着无害(它们让"撤断点必须
  趁停住"这件事在任何配置下都成立), 但若哪天要精简, 先确认 mi-async 仍开着再动。

写入能力(有意收窄, 不是随手开的)
-------------------------------
本模块必然要**写内存** —— 喂狗本身就是写(`IWDT->SERV = 0x12345A5A`)。既然这条口子天然破了,
就把它划成**三个函数、都不是任意地址**:

    feed_watchdog()         地址来自会话构造时的 watchdog 参数, **函数体内不可参数化**, 传不进别的地址
    inject(expr, value)     expr 必须在会话构造时的 `inject_allow` 白名单里(**默认空 = 注入整体关闭**),
                            不收裸地址; 每次写都记 (expr, 旧值, 新值), 并返回可恢复句柄
    with_inject(at,…)       **停着触发**的交错原语(见它自己的 docstring): 停下来 → 注入 → 放行 → 等判据断点。
                            注入观测递证据走 `inject_hit(…)`, 于是"这一条是靠注入造出来的状态"会以
                            `[断点/注入]` 显形在判据汇总里(common/judge 的 `TRIG_INJECT`)。

**判定是"必须点名", 不是"不许写"**(2026-09-11 校正 —— 早先本文件与 CLAUDE.md 把它写成"只读/不开这个
口子", 那是把**这一层的收窄设计**说成了**用户定的规矩**, 两者不是一回事):
  · **变量名**: 必须写进 `inject_allow` 才写得动 —— 白名单在**会话构造时**给, 于是"要注入什么"在
    调用方源码里一眼看得见, 不会手滑;
  · **外设寄存器符号**(`IWDT->SERV` 这类)走**同一条**点名的路 —— 它们也是 DWARF/头文件里的符号,
    写法一样; 本层不另开捷径, 也不因为"那是寄存器"就拒;
  · **裸地址写没有接口, 且有意保持没有** —— `0x…` 与 `*expr` 一律拒。理由不是洁癖: 地址写没有类型
    (改 1 字节还是 4 字节靠猜)、没有符号可核(读代码的人不知道那是什么)、也答不出"这一次受不受控"。
    要点名到符号, 就得先知道那个符号是什么 —— 这一道正是我们要的。

本层**不做**的(仍是有意留白, 见 CLAUDE.md): 复位目标、烧录 Flash、改 PC/寄存器组。要那些走别的工具。

"""
from __future__ import print_function

import ast
import collections
import functools                  # `_pair_window` 要 wraps(保 __name__/__doc__, 免得调试时认不出函数)
import inspect
import io
import json                       # 自检读回事件流(jsonl)用
import os
import queue
import re
import subprocess
import sys
import tempfile                   # 自检给事件流开临时文件用(不往 log/ 里丢东西)
import threading
import time
import traceback

from common import events      # 机器可读的事件流(第二路输出, 2026-09-17)。
from common import faultlog    # 故障台账: 会话开不了 / 起不来时必须留下一笔(2026-09-20)
#   方向 **swdbg → common** 是允许的(`judge` 也是这么来的); **绝不能** import meterlib ——
#   本包号称"零 meterlib 依赖", 而 `events` 住中立层正是为了两边都能用(见其模块头)。
#   本文件里的落点全在**已有的**卡口上(见 `_trace_sent`/`_trace_recv`/`_trace_srv` 与 `_ev_halt`):
#   按本仓 §2.7 的判据, "哪些动作有记录"必须靠**卡口少**来保证, 不靠"记得在每个函数里补一句"。
from common import judge       # 记录形状/观测常量的唯一定义处(中立层, 方向 swdbg → common)
from common import loglabel    # 字形(含调试行 `[调试] [来源] …`)的唯一定义处, 同上一行的方向
# ⚠ 2026-09-18 修: 下面几处降级行首前缀**原先写的是 `_JR.MARK_DEGRADED`, 而 `judge` 里根本没有这个名字**
#   —— `MARK_DEGRADED`/`MARK_NOTE` 的定义在 `common/runlog`(随 `loglabel` 一起, 见那两行的注释)。
#   犯错的代价: `Session._warn` 的**每一句**降级/提醒都会在打印那一行 `AttributeError` 抛出去,
#   而那正是本函数承诺"**不打断**整个取证"的地方 —— 于是"报告一个问题"这件事本身变成新问题:
#   现场是 2026-09-18 探针停在 `:3652` 后读寄存器, `_warn` 一抛, 整趟深入直接从 `step_one_pc` 里崩掉,
#   前面积累的四趟结论也一并丢掉。字形**只在 runlog 定义一次**, 故这里也去那儿取。
from common import runlog      # 降级/提醒行首字形(`MARK_DEGRADED`/`MARK_NOTE`)的唯一定义处
# 断点源码侧事实那一段的依赖: C 文本原语(抠注释/认读写)与 C 源码结构索引(函数范围/行归属)。
# 两者都**不认表**、不碰 .out, 故一个住中立层、一个住本包 —— 见 `csrc.py` 头。
from common import ctext
from swdbg import csrc
from swdbg import gdbinit      # 整片 .out 地图 + 唯一的反汇编出口(见该文件头)。方向: 本模块 → 它
# `GdbError` / `addr_tok` 的家在下层(`gdbinit`) —— 两层都要用, 而它是下层。这里只取进来用,
# 名字与 `__all__` 一字未变(`swdbg.breakpoint.GdbError` / `swdbg.selfcheck` 照旧能取到)。
from swdbg.gdbinit import GdbError, addr_tok
from swdbg import jlink        # J-Link 那一端的枚举/解析。本模块不再直接信卡带那个 SN 死值
from swdbg import probe_cmsis  # 只为 CMSIS-DAP 那一端的 backend 名
from swdbg import probe_jlink  # 只为 J-Link 那一端的 backend 名(`probesel.BACKENDS` 里的那一个)
from swdbg import probesel     # 「该用哪支探针」的唯一定义处(两端通吃, 含 --doctor)


# ---- 机器条件(= **这台机器**的事实)----
# **单一事实源在 `machine/` 装机卡带**(2026-09-11 收敛)。原先本文件 / probe.py / restore.py 各抄一份
# "gdb 在哪 / SN 多少", `.vscode/launch.json` 里还有三份 —— 换一台电脑要满仓找。现在这里只**取**,
# 不写值; 换机器改 `machine/*.py`(见 machine/env_check.py 那道"装包即验")。
# 依赖方向: swdbg → common.machspec, **不是** swdbg → machine(卡带的名字只是槽里的字符串默认值)。
#
# ⚠ 模块级读一次, 供下面 `Session.__init__` 的默认参数(def 时求值)用。**没装卡带时它们是 None**
#    —— 这不构成降级: `import swdbg.breakpoint` 照旧能 import(离线自检要的), 真去连表时
#    `_machine_ready()` 会当场拦下并点名缺了哪几项。
from common import machspec
from common.probe_guard import acquire

GDB_SERVER = machspec.get("GDB_SERVER")
# gdb —— 必须是**带 Python scripting 的多架构 gdb**(2026-09-11 换):
# ARM 官方那份 manifest 明写 `--with-python=no`, `python print(1)` 直接答 "Python scripting is not
# supported in this copy of GDB" —— 于是 `_install_hook_stop`(喂狗钩子) 装不上, 只能退到"python 侧
# 每次停住补喂", 按 judge 口径属**降级**。MSYS2 的 gdb-multiarch 是 `--enable-targets=all`
# `--with-python` 编的(见 `--configuration`), 反汇编/`info scope` 与旧 ARM gdb **逐字节一致**
# (2026-09-11 实测对照), 是等价替换而非另起炉灶。
# ⚠ 取的是 `gdb-multiarch.exe`(31MB, 带全目标)**不是** 同目录的 `gdb.exe`(10.5MB, 只有 x86 宿主目标,
#   载 ARM 的 .out 后架构停在 `i386`、`set architecture arm` 报 Undefined —— 两者同名同目录, 极易拿错)。
#   这两条能力(gdb_substr)由 `machine/env_check.py` 第 4 节**实测**守着, 不靠这句注释。
GDB = machspec.get("GDB")
# ⚠ `SN` 是卡带**声明**的那一支(None = 自动), **不是**最终要连的那一支 —— 枚举解析在
#    `swdbg/jlink.py:resolve_sn`(换支 J-Link 时"连不上"与"表挂了"长得一样, 所以不许静默猜)。
#    保留这个名字只为对外兼容(`__all__` 里有它); 真连表时走下面的解析。
SN = machspec.get("JLINK_SN")  # J-Link 声明的序列号(None = 自动, 恰好一支才认)
DEVICE = machspec.get("DEVICE")   # 泛型即可(FM33A0610 = Cortex-M0 r0p0 / armv6s-m)
IFACE = machspec.get("IFACE")
SPEED = machspec.get("SPEED")  # kHz —— 必须显式(见 probe.py 铁律 1: 不给速度会往 RAM 下自测代码)
PORT = machspec.get("GDB_PORT")   # GDB Server 监听口; 与 VS Code cortex-debug 抢口时换
# ---- CMSIS-DAP 端(2026-09-20 起第二支探针)----
# 目标名走 pyOCD 的 `-t`, **不借用上面的 `DEVICE`**: `DEVICE` 那个 "Cortex-M0" 是 **J-Link 的泛型
# 器件名**, 而 pyOCD 按目标名去加载目标类, 要的是 `cortex_m`(通用 Cortex-M)。
# ⚠ 别把 `DEVICE` 直接喂给 pyOCD —— 管理芯不是 M0, 是 **SecurCore SC000**(CPUID 0x410CC300),
#   见 `swdbg/probe_cmsis.py` 文件头; pyOCD 的 `cortex_m0` 目标是给真 M0 的。
DAP_TARGET = machspec.get("DAP_TARGET")

# ⚠ 连表前核机器条件走 `common.machspec.ready(...)`(机制在中立层, 一处实现) —— 见 Session.__init__。
#   不在这儿再写一个: "核哪几项"各引擎不同(probe 压根不用 gdb), 清单在调用处, 机制只该有一份。

def _pair_window(name_of):
    """把一整段「触发窗口」套进配对号里 —— **一条实现, 几个窗口共用**(见 `common/events.pairing`)。

    配对号要回答的是"**停住那一刻读到的值, 对应的是哪一帧**": 一次窗口里, 后台线程在发串口帧、
    主线程在等断点停, 这两半必须盖上同一个号才认得出来是一件事。号由 `common/events` 提供环境值
    (不是传参: 串口那半在 meterlib 里, 而 `swdbg` **不许 import meterlib** —— 边界铁律)。

    为什么是装饰器, 不是在函数体里写 `with`: 这几个函数的体都长(几十到上百行)、出口还不止一个
    (`raise` 与多个 `return`)。手工缩进既改不干净, 又一定会漏掉某个出口 —— 而**漏掉的那个出口
    正是"这一段的事没盖成章"**, 且漏了不报错(读日志的人只会以为那几帧没有归属)。装饰器把
    "进/出"钉在**函数边界**上, 漏不掉, 将来新加出口也自动被罩住。

    `name_of(*a, **k)` 只算**名字**(给读日志的人看); 机器只认那个号。名字算不出来时不许抛 ——
    这个包装站在**串口那条通路上**, 抛出去会打断那一帧(见 events 模块头 ①): 故一律兜底。
    """
    def deco(fn):
        @functools.wraps(fn)
        def wrap(*a, **k):
            try:
                _n = name_of(*a, **k)
            except Exception:
                _n = "%s(名字算不出)" % getattr(fn, "__name__", "?")
            with events.pairing(_n):
                return fn(*a, **k)
        return wrap
    return deco

def gdb_version(exe=None):
    """`gdb --version` 的第一行(如 `GNU gdb (GNU Arm Embedded Toolchain 13.3.rel1...) 14.2.90`)。

    为什么值得单独取出来: **gdb 换版 ⇒ MI 记录的形状可能变**, 而本模块 2026-09-11 撞的三个 bug
    **全部**出在"我以为 gdb 会输出什么"上。取不到就回 `"?"` —— 那是一个诚实的"不知道",
    而不是一个假的版本号; 排障时据它先确认"这一跑用的是哪版 gdb"。

    ⚠ 2026-09-18: 本函数**当前在仓内没有调用方**(它的上一个用户是已删除的录播带指纹)。
    它是真工具、不属假件, 故留着 —— 换 gdb 后按 `scripts/_install_gdb.py` 的验收三连复核时用得上。

    `exe=None` ⇒ 用当前卡带的 GDB(见 `_machine_ready`: 真取不到它是要报错的那种事)。
    """
    exe = exe or GDB
    try:
        r = subprocess.run([exe, "--version"], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=20)
        first = (r.stdout or b"").decode("utf-8", "replace").splitlines()
        return first[0].strip() if first else "?"
    except Exception:
        return "?"
# ============================================================================
# 一、写法的唯一定义
# ============================================================================
KINDS = ("call", "prev", "func")  # 带判别字的那种(行写法没有判别字, 它就是一对 (文件, 行号))
                                  # `call`/`prev` 是同一处调用的两侧: 前者取 `bl` 之后那条(注入/喂狗),
                                  # 后者取 `bl` 之前那条(要读这次调用的实参)。见 `gdbinit.map_of`。
                                  # 行写法归一之后也有判别字(`"line"`), 而它**不进 KINDS** ——
                                  # KINDS 管的是"脚本里能这么写", 脚本写的是一对 (文件, 行号)。


def normalize(x):
    """任意写法 → 规范元组; 认不出抛 `ValueError`(**不返回 None**)。

    归一成元组的规范形, 是为了让下游比集合、进日志时**只有一种长相**:
    同时给出 `("line", 文件, 行)` 这一种 —— 行写法原先是一对 `(文件, 行号)`, 与两种带判别字的
    写法混在一起时, "第 2 个元素是函数名还是文件" 得靠 `len()` 猜; 归一之后一律按 `x[0]` 判。

    ⚠ 判据是**第一个元素是不是判别字**, 不是长度 —— `("func", 名)` 与 `(文件, 行号)` 都是两元。
    ⚠ **幂等**: 归一过的再喂一次, 还是它自己(`("line", 文件, 行)` 也收)。
    """
    if isinstance(x, (tuple, list)) and len(x) == 3 and x[0] == "line":
        return ("line", str(x[1]), int(x[2]))
    if isinstance(x, (tuple, list)) and len(x) >= 2 and x[0] in KINDS:
        if x[0] in ("call", "prev") and len(x) == 4:
            return (x[0], str(x[1]), str(x[2]), int(x[3]))
        if x[0] == "func" and len(x) == 2:
            return ("func", str(x[1]))
    elif isinstance(x, (tuple, list)) and len(x) == 2:
        return ("line", str(x[0]), int(x[1]))
    raise ValueError("认不出的断点写法: %r\n"
                     '  只收 ("文件", 行号) / ("call", 函数, 被调, 第几处) / '
                     '("prev", 函数, 被调, 第几处) / ("func", 函数名)'
                     % (x,))


def text(spec):
    """规范写法 → 人读的那串 —— 与脚本里写的那一行**长得一样**, 好对着核。"""
    s = normalize(spec)
    if s[0] == "line":
        return "%s:%d" % (s[1], s[2])
    if s[0] in ("call", "prev"):
        return '("%s", %s, %s, %d)' % (s[0], s[1], s[2], s[3])
    return '("func", %s)' % (s[1],)


# ============================================================================
# 二、断点那一刻的源码侧事实
# ============================================================================
def facts(path, anchors):
    """断点清单 → 事实表(不判红绿)。

    anchors: [(标签, 断点那一行, (变量名, ...)), ...] —— 都针对同一个 `.c`(路径由 path 给)。
    返回 [{tag, line, func, func_range, problem, vars:[{name, first_seen, assigned_at,
            last_before, is_param, passed_at, passed_before, is_local, decl_line, decl_init}]}]

    "赋值" = `ctext.kind` 判为 `write` 的那次出现(它挡住 `==`、认 `+=`/`++`),
    且**不是声明行**(`ctext.decl_line`); 声明**带初值**的(`INT32U x = 0;`)算一次赋值 ——
    那确实把一个值放进了变量。

    `is_local` = 名字在**本函数体内**有没有声明。`False` ⇒ 它是文件级量(全局/静态量)或
    别的函数的局部量, 值在别处算出来 —— 这时"本函数里断点前没赋值"对它**不是一个结论**,
    见 `csrc.decl_info` 的说明。`decl_init` 是声明初值的**形状**(None / "常量" / "表达式"),
    不含源码正文。
    """
    code, _raw = csrc.load(path)
    rows = []
    for tag, line, names in anchors:
        enc = csrc.enclosing(code, line)
        row = {"tag": tag, "path": path, "line": line, "problem": None,
               "func": enc[0] if enc else None,
               "func_range": (enc[1], enc[2]) if enc else None, "vars": []}
        if enc is None:
            row["problem"] = "这一行不在任何函数体内"
            rows.append(row); continue
        lo, hi = enc[1], enc[2]
        ps = csrc.params(code, lo)
        for nm in names:
            base = csrc.name_base(nm)            # 见 name_base 的 ⚠: 带下标/成员的名字要先归一化
            outp = csrc.passed_out_re(base)
            first = None
            writes, addr = [], []
            for i in range(lo - 1, hi):
                occ = csrc.occurrences(code, i + 1, base)
                if not occ:
                    continue
                if first is None:
                    first = i + 1
                if any(ctext.kind(code[i], s, e) == "write" for s, e in occ):
                    writes.append(i + 1)
                # 定义行本身长得像调用(`Send(… mode …)`), 那是形参声明, 不是传出去
                if i + 1 != lo and outp.search(code[i]):
                    addr.append(i + 1)
            before = [h for h in writes if h < line]
            decl_line, decl_init = csrc.decl_info(code, lo, hi, base)
            row["vars"].append({
                "name": nm, "first_seen": first, "assigned_at": writes,
                "last_before": (max(before) if before else None),
                "is_param": base in ps,
                "passed_at": addr,
                "passed_before": [a for a in addr if a < line],
                "is_local": decl_line is not None,
                "decl_line": decl_line, "decl_init": decl_init,
            })
        rows.append(row)
    return rows


def render(rows):
    """事实表 → 人读文本。**每行都是事实, 没有一个字是判语。**"""
    L = []
    for r in rows:
        rng = "" if not r["func_range"] else "(%d..%d)" % r["func_range"]
        L.append("%s  断点 %s:%d  %s%s"
                 % (r["tag"], r["path"].replace("\\", "/").rsplit("/", 1)[-1],
                    r["line"], r["func"] or "?", rng))
        if r["problem"]:
            L.append("    !! %s" % r["problem"])
            continue
        for v in r["vars"]:
            if not v["is_local"]:
                desc = ("首次出现 @%s —— **本函数体内没有它的声明**: 它是文件级量"
                        "(全局/静态量)或别的函数的局部量, 值在别处算出来, "
                        "本函数里那几次出现说明不了它有没有值"
                        % v["first_seen"])
            elif v["last_before"] is None:
                desc = ("首次出现 @%s, 全函数赋值行 %s —— **断点那一行之前没有赋值**"
                        % (v["first_seen"], v["assigned_at"] or "无"))
            else:
                desc = ("首次出现 @%s, 断点那一行之前最后一次赋值 @%d"
                        % (v["first_seen"], v["last_before"]))
                later = [x for x in v["assigned_at"] if x > r["line"]]
                if later:
                    desc += ", 之后还有 %s" % later
            if v["decl_init"]:
                desc += "  [本函数声明 @%s, 初值是**%s**]" % (v["decl_line"], v["decl_init"])
            if v["is_param"]:
                desc += "  [形参: 值由调用方给]"
            if v["passed_before"]:
                desc += ("  [断点前已传出 @%s —— 赋值可能在**被调函数**里, 本扫描器看不到]"
                         % ",".join(str(x) for x in v["passed_before"]))
            elif v["passed_at"]:
                desc += "  [传出 @%s, 全在断点那一行之后]" % ",".join(str(x) for x in v["passed_at"])
            L.append("   %-12s %s" % (v["name"], desc))
    return "\n".join(L)


# ============================================================================
# 解析层的判据本体 —— 用**合成样例**, 绝不内置厂商源码(外泄硬约束)
# 它验的是"这套解析还认不认得那几种写法", 与某块表跑到什么结论无关, 所以**不进任何子项账本**;
# 本模块也不提供"跑一跑看看"的入口 —— 断点可不可信只能由实测的现场说话。
# ============================================================================
_SAMPLE = """\
/* 合成样例: 形状照搬"声明在函数头、赋值在分支里"的 C89 风格 */
static void Calc(BOOL normal)
{
\tINT32U used;          /* 声明无初值 */
\tBOOL  flag = FALSE;   /* 声明带初值 = 已赋值 */
\tINT8U i;

\tif ((g_Tab[6] == 0) && (normal != TRUE)) return;   /* ← 断点1: 两个量都还没意义 */

\tfor (i = 0; i < 3; i++) {
\t\tused += i;          /* 赋值(+ =)在循环里 */
\t}
\tif (normal) flag = TRUE;    /* 断点2 之前 */
\t/* used = 999;   ← 注释里的赋值不算 */
\tprintf("used = 0\\n");        /* 字面量里的也不算 */
\tWrite(&used);               /* ← 断点2: used 已赋值, flag 已赋值 */
}

static void Send(INT8U *out, BOOL mode, INT16U n)
{
\tINT8U buf[4];
\tFill(&buf, mode);           /* buf 由被调函数经指针写出 —— 本行不是赋值 */
\tif (mode) { n = 1; }        /* 长得像调用, 但不是: 控制流关键字, mode 没传出去 */
\tPut(&buf);                  /* ← 断点3: buf 断点前无直接赋值, 但已传出 */
}
"""

# 第三个盲区(文件级量 / "声明即赋真值")的合成样例。**与 _SAMPLE 分开一份**: _SAMPLE 的行号被
# 下面几条断言按数字引用(如 `assigned_at == [11]`), 往里插行就会连带改一批**验别的事**的断言。
_SAMPLE_SCOPE = """\
static INT16U g_Limit = 300;    /* 文件级量: 本函数体内没有它的声明 */

static void Calc2(BOOL normal)
{
\tINT16U lim = g_Limit + 1;   /* 声明带**表达式**初值 —— 那就是真值 */
\tBOOL  flag = 0;             /* 声明带**常量**初值 —— 那是占位 */
\tINT8U way;                  /* 声明无初值 */

\tif (g_Limit == 0) return;   /* ← 断点A: 三个量都还没被赋 */

\tif (normal) { flag = 1; way = 2; }   /* 真值到这里才赋 */
\tPut(&way);                  /* ← 断点B */
}
"""


def checks():
    """→ [(断言说明, 是否成立, 细节), ...]。**判据本体只此一份** —— 静态核对器
    `scripts/_check_anchors.py` 从这里取, 不在别处另写一套(两套就会漂移, 然后只绿一边)。

    验的是**解析**: 函数范围(按花括号, 不是缩进) / 注释与字面量不算赋值 /
    断点前无赋值要露出来 / 形参与传出不能和"没赋值"混为一谈 /
    文件级量与"声明即赋真值"不能和"断点前无赋值"混为一谈。
    只用**合成样例**, 不碰厂商源码 —— 所以在任何机器上都能跑。
    """
    checks = []

    def chk(label, cond, extra=""):
        checks.append((label, bool(cond), extra))

    d = tempfile.mkdtemp(prefix="breakpoint_checks_")
    p = d.replace("\\", "/") + "/sample.c"
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(_SAMPLE)
    try:
        code, raw = csrc.load(p)
        fs = csrc.functions(code)
        chk("识别出两个函数定义", [f[0] for f in fs] == ["Calc", "Send"], str(fs))
        # 函数尾 = **本函数**闭合的那个 }: 终止行必须是 `}`, 且紧跟其后的非空行是下一个函数定义。
        # 这条不靠本模块的配平算法自证 —— 对"取全文最后一个 }"那种实现它会**报 FAIL**。
        calc = [f for f in fs if f[0] == "Calc"][0]
        nxt = next(i + 1 for i in range(calc[2], len(code)) if code[i].strip())
        chk("函数尾按花括号配平(不是缩进/不是全文最后一个})",
            code[calc[2] - 1].strip() == "}" and nxt == fs[1][1] and len(fs) == 2,
            "Calc 收于@%d/%r, 其后第一个非空行@%d(=下一个定义@%d)"
            % (calc[2], code[calc[2] - 1].strip(), nxt, fs[1][1]))

        a1 = next(i + 1 for i, ln in enumerate(code) if "g_Tab[6] == 0" in ln)
        a2 = next(i + 1 for i, ln in enumerate(code) if "Write(&used)" in ln)
        r = facts(p, [("断点1", a1, ("used", "flag")), ("断点2", a2, ("used", "flag"))])
        v1 = {v["name"]: v for v in r[0]["vars"]}
        v2 = {v["name"]: v for v in r[1]["vars"]}
        chk("断点1: used 断点前无赋值(声明无初值)", v1["used"]["last_before"] is None, str(v1["used"]))
        chk("断点1: flag 断点前有赋值(声明带初值)", v1["flag"]["last_before"] is not None, str(v1["flag"]))
        chk("断点2: used 断点前已赋值", v2["used"]["last_before"] is not None, str(v2["used"]))
        cmt = next(i + 1 for i, ln in enumerate(raw) if "999" in ln)
        lit = next(i + 1 for i, ln in enumerate(raw) if "printf" in ln)
        chk("注释里的 `used = 999` 不算赋值(且该行在 code 中已置空)",
            cmt not in v2["used"]["assigned_at"] and "999" not in code[cmt - 1],
            "注释@%d 赋值行%s" % (cmt, v2["used"]["assigned_at"]))
        chk("字面量 `\"used = 0\"` 不算赋值", lit not in v2["used"]["assigned_at"])
        chk("`used += i` 算赋值(复合赋值不放过)",
            v2["used"]["assigned_at"] == [11], str(v2["used"]["assigned_at"]))
        chk("`==` 不算赋值(比较是读)",
            a1 not in v2["used"]["assigned_at"] and a1 not in v2["flag"]["assigned_at"])
        chk("raw 与 code 行号对齐(剥离不挪行)",
            len(raw) == len(code) and raw[cmt - 1].strip().startswith("/*"),
            "%d vs %d" % (len(raw), len(code)))
        chk("断点那一行落在函数外时明确报出(不静默给空事实)",
            "不在任何函数体" in (facts(p, [("X", 1, ("used",))])[0]["problem"] or ""))
        chk("render 不带判语(没有 PASS/FAIL/通过)",
            not any(w in render(r) for w in ("PASS", "FAIL", "通过")))

        # ---- 形参与传出: 两种"断点前没赋值"不是同一回事, 不能混为一谈 ----
        a3 = next(i + 1 for i, ln in enumerate(code) if "Put(&buf)" in ln)
        r3 = facts(p, [("断点3", a3, ("buf", "mode", "n"))])
        v3 = {v["name"]: v for v in r3[0]["vars"]}
        chk("形参被标出(值由调用方给, 不是'没赋值')",
            v3["mode"]["is_param"] and v3["n"]["is_param"] and not v3["buf"]["is_param"],
            str({k: v["is_param"] for k, v in v3.items()}))
        chk("render 把形参说明白(不是只报'无赋值')", "[形参: 值由调用方给]" in render(r3))
        chk("局部量传出要标出(赋值可能在被调函数里)",
            v3["buf"]["passed_before"] and "[断点前已传出" in render(r3), str(v3["buf"]))
        chk("断点后传出不算'断点前传出'",
            not v2["used"]["passed_before"] and v2["used"]["passed_at"],
            "passed_at=%s before=%s" % (v2["used"]["passed_at"], v2["used"]["passed_before"]))
        chk("render 对只传出、且全在断点那一行之后的量说清楚",
            "[传出" in render(r) and "[断点前已传出" not in render(r))
        # 控制流关键字长得像调用(`if (mode)`), 不能当成"传给被调函数";
        # 定义行 `Send(… mode …)` 是形参声明, 也不是"传出去"
        cif = next(i + 1 for i, ln in enumerate(code) if "if (mode)" in ln)
        cfill = next(i + 1 for i, ln in enumerate(code) if "Fill(&buf" in ln)
        chk("`if (mode)` 与定义行都不算传出, 只有真调用在列",
            v3["mode"]["passed_at"] == [cfill] and cif not in v3["mode"]["passed_at"],
            "if@%d Fill@%d 得%s" % (cif, cfill, v3["mode"]["passed_at"]))

        # ---- 带下标/成员的名字(2026-09-10 3-2 断点观测实踩出来的两个假警报) ----
        # 脚本里写的是 gdb 表达式(`swTime[0]`、`TAB_Switch[id].idFrez`), 而源码扫描比的是标识符。
        # 不归一化 → 一次都匹配不上 → 报告"断点前无赋值(读到的是未初始化值)", 而值其实是好的。
        chk("带下标/成员的名字归一化后再找出现",
            csrc.name_base("swTime[0]") == "swTime"
            and csrc.name_base("TAB_Switch[id].idFrez") == "TAB_Switch"
            and csrc.name_base("used") == "used",
            "%s | %s" % (csrc.name_base("swTime[0]"), csrc.name_base("TAB_Switch[id].idFrez")))
        r4 = facts(p, [("断点4", a3, ("buf[0]",))])
        v4 = {v["name"]: v for v in r4[0]["vars"]}
        chk("元素名 `buf[0]` 也能抠出『断点前已传出』(归一化到 buf)",
            v4["buf[0]"]["passed_before"] and v4["buf[0]"]["first_seen"] == v3["buf"]["first_seen"],
            str(v4["buf[0]"]))
        # `kind` 的下标那一跳: `arr[i] = v` 是写, `arr[i] == v` 仍是读。
        # 漏了这一跳, 只靠元素写入的数组**写入足迹全空** → "这变量从没被写过"这种结论凭空出现。
        chk("`arr[i] = v` / `arr[i][j] += v` / `arr[i]++` 算写",
            ctext.kind("    buf[0] = g_A;", 4, 7) == "write"
            and ctext.kind("\tbuf[i][j] += 1;", 1, 4) == "write"
            and ctext.kind("\tbuf[0]++;", 1, 4) == "write")
        chk("`arr[i] == v` 仍是读(别被 `=` 骗了)",
            ctext.kind("    if (buf[0] == g_A) {", 8, 11) == "read")
        chk("下标括号不配对(跨行/宏)时不敢猜, 原样返回",
            ctext.skip_subscripts("[i") == "[i" and ctext.skip_subscripts("[i][j] = x") == "= x")

        # ---- 文件级量 / "声明即赋真值"(2026-09-17 加: 实测 14 处告警里 13 处是这两类) ----
        p2 = d.replace("\\", "/") + "/sample2.c"
        with io.open(p2, "w", encoding="utf-8") as f:
            f.write(_SAMPLE_SCOPE)
        c2, _r2 = csrc.load(p2)
        aA = next(i + 1 for i, ln in enumerate(c2) if "g_Limit == 0" in ln)
        r5 = facts(p2, [("断点A", aA, ("g_Limit", "lim", "flag", "way"))])
        v5 = {v["name"]: v for v in r5[0]["vars"]}
        chk("文件级量被标出(本函数体内没有它的声明, 不是'断点前无赋值')",
            v5["g_Limit"]["is_local"] is False and v5["g_Limit"]["last_before"] is None,
            str(v5["g_Limit"]))
        chk("局部量被标出(本函数体内有声明)",
            v5["lim"]["is_local"] and v5["flag"]["is_local"] and v5["way"]["is_local"],
            str({k: v["is_local"] for k, v in v5.items()}))
        chk("声明即赋**表达式**初值 ⇒ 那不是'断点前无赋值'(读到的是算出来的真值)",
            v5["lim"]["decl_init"] == "表达式"
            and v5["lim"]["last_before"] == v5["lim"]["decl_line"],
            str(v5["lim"]))
        chk("声明带**常量**初值 ⇒ 那确实是占位, 要露出来",
            v5["flag"]["decl_init"] == "常量"
            and v5["flag"]["last_before"] == v5["flag"]["decl_line"],
            str(v5["flag"]))
        chk("声明无初值 ⇒ 初值形状为 None, 断点前仍旧无赋值",
            v5["way"]["decl_init"] is None and v5["way"]["last_before"] is None,
            str(v5["way"]))
        chk("render 把文件级量与'断点前无赋值'分开说",
            "本函数体内没有它的声明" in render(r5) and "初值是**表达式**" in render(r5))
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)
    return checks

MAX_HW_BREAK = 4          # Cortex-M0 硬件断点项数
# 切块大小与建图参数(`swdbg/gdbinit.py`) —— 本模块不再有反汇编通路, 一律查那张地图。
# 断点单元(FPB)寄存器 —— 用来清**跨会话残留**的硬件断点槽, 见 `Session._clear_stale_fpb`。
# ⚠ 与 `swdbg/restore.py` 的 FP_CTRL/FP_COMP **重复书写**: 本模块不该 import 那个 CLI 救火脚本
#   (`restore` 是"人手动救场"的入口, 不是库依赖), 而这只是同一份硬件事实的两处落笔。
#   换芯片型号时两处都要动。
FP_CTRL_ADDR = 0xE0002000                        # bit0 ENABLE, bit1 KEY
FP_COMP_ADDRS = (0xE0002008, 0xE000200C, 0xE0002010, 0xE0002014)   # M0 只有 4 个断点槽
# ---- 数据观察点单元(DWT)寄存器 —— 跨会话残留的**第二处**, 见 `Session._clear_stale_dwt` ----
# 与 FPB 分开列: FPB 管**取指**地址, DWT 管**数据**地址(读写某个变量的时刻停住)。
# 2026-09-17 实踩: 只有 FPB 被清过, 一个留在 DWT_COMP0 上的变量观察点让整场串口全 RX(0)。
DWT_COMP_ADDRS = (0xE0001020, 0xE0001030, 0xE0001040, 0xE0001050)
DWT_FUNC_ADDRS = (0xE0001028, 0xE0001038, 0xE0001048, 0xE0001058)

SETTLE_AFTER_RUN = 0.4    # 收到 `^running` 之后还要静默几秒才算"表真在跑"(见 Session.go 的 ⚠):
                          # 不等这一下, **放行后的第一帧串口必丢**(2026-09-10 实测, 3-2 时区支首帧 RX(0))

__all__ = ["Session", "Hit", "GdbError", "session", "trigger", "open_or_none", "report",
           "parse_mi", "mi_fields", "mi_unescape", "GDB_SERVER", "GDB", "SN", "DEVICE",
           "IFACE", "SPEED", "PORT", "DAP_TARGET", "MAX_HW_BREAK", "addr_tok",
           "to_bpno", "break_at_or_none", "fire_hit", "wait_hit", "expect_no_hit",
           "ANCHOR_KINDS", "anchor_spec_txt",
           # 写法与源码侧事实(一二两段) —— 不连探针, 静态核对器共用这一份
           "KINDS", "normalize", "text", "facts", "render", "checks"]

# `addr_tok` / `GdbError` 实现在 `swdbg/gdbinit.py`(见本文件顶部的取用注释)。

# ============================================================================
# ① MI 记录解析(纯函数 —— 不碰表、不碰探针)
# ============================================================================
_ESC = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'",
        "a": "\a", "b": "\b", "f": "\f", "v": "\v", "0": "\0", "e": "\x1b"}

def mi_unescape(s):
    """MI 的带引号字符串里是 C 转义序列 → 还原成人读文本。

    ⚠ 两个坑, 都踩过:
    ① 不能图省事写 `s.replace('\\\\n','\\n')` —— gdb 打印中文/路径时用**八进制**转义
       (`\\346\\265\\213` 这种), 只处理字母转义会把它们原样留在结果里, 判读时看着像乱码。
    ② 还原**必须按字节攒、最后整体 UTF-8 解码**, 不能一个转义一个 `chr()`:
       `\\346\\265\\213` 是"测"的 UTF-8 三字节, 逐个 chr() 出来是三个拉丁字符 `æµ\\x8b`,
       永远拼不回"测"。而流本身已按 UTF-8 解码过, 所以字面非 ASCII 字符要先编码回字节。
    """
    buf, i = bytearray(), 0
    while i < len(s):
        c = s[i]
        if c != "\\" or i + 1 >= len(s):
            buf.extend(c.encode("utf-8"))
            i += 1
            continue
        n = s[i + 1]
        if n in _ESC:
            buf.extend(_ESC[n].encode("latin-1"))     # 字母转义都是 ASCII 控制符
            i += 2
            continue
        if n in "01234567":                           # 八进制转义, 最多 3 位
            j, digits = i + 1, ""
            while j < len(s) and len(digits) < 3 and s[j] in "01234567":
                digits += s[j]
                j += 1
            buf.append(int(digits, 8) & 0xFF)
            i = j
            continue
        buf.extend(n.encode("utf-8"))                 # 未知转义: 去掉反斜杠
        i += 2
    return buf.decode("utf-8", "replace")

def _mi_quoted(s, i):
    """s[i] == '"' 起, 解析带引号字符串 → (文本, 下一位置)。"""
    assert s[i] == '"'
    i += 1
    buf = []
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            buf.append(c)
            buf.append(s[i + 1])
            i += 2
            continue
        if c == '"':
            return mi_unescape("".join(buf)), i + 1
        buf.append(c)
        i += 1
    return mi_unescape("".join(buf)), i           # 未闭合: 容错当结束

def _mi_value(s, i):
    """从 s[i] 起解析一个 MI 值 → (value, 下一位置)。
    '"' → str; '{' → dict; '[' → list; 其余 → 裸 token(str)。"""
    while i < len(s) and s[i] == " ":
        i += 1
    if i >= len(s):
        return "", i
    c = s[i]
    if c == '"':
        return _mi_quoted(s, i)
    if c == "{":
        return mi_fields(s, i + 1, "}")
    if c == "[":
        vals, i = [], i + 1
        while i < len(s) and s[i] != "]":
            v, i = _mi_value(s, i)
            vals.append(v)
            if i < len(s) and s[i] == ",":
                i += 1
        return vals, (i + 1 if i < len(s) else i)
    j = i
    while j < len(s) and s[j] not in ",}]":
        j += 1
    return s[i:j], j

def mi_fields(s, i=0, close=""):
    """解析 `key=value,key=value,...` 序列 → dict。close 非空时以该字符收尾(末尾位置返回到
    收尾符之后)。值可以嵌套(`frame={file=..,line=..}`)。**只认顶层逗号** —— 引号/花括号里的
    逗号不切分, 否则 `reason="a,b"` 会被切坏。"""
    out = {}
    while i < len(s):
        while i < len(s) and s[i] in " ,":
            i += 1
        if i >= len(s) or (close and s[i] == close):
            i += 1 if (close and i < len(s)) else 0
            break
        j = s.find("=", i)
        if j < 0:
            break
        key = s[i:j].strip()
        val, i = _mi_value(s, j + 1)
        if key:
            out[key] = val
    return out, i

def parse_mi(line):
    """一行 MI 输出 → dict, 认不出则 None。

        '^done,value="3"'                     → {'_class':'^done','value':'3'}
        '*stopped,reason="breakpoint-hit",
             bkptno="2",frame={file="x.c",line=625}'
                                              → {'_class':'*stopped','reason':'breakpoint-hit',
                                                 'bkptno':'2','frame':{'file':'x.c','line':625(→'625')}}
        '~"text\\n"'                          → {'_class':'~','_stream':'text\\n'}
        '(gdb)'                               → None(提示符, 不是记录)

    sigil: `^`结果 / `*`异步执行 / `+`异步状态 / `=`通知 / `~`控制台 / `&`日志 / `@`目标输出。
    """
    if line is None:
        return None
    line = line.rstrip("\r\n")
    # 提示符判定要**去空白后**比 —— gdb 在控制台输出之后会吐 " (gdb) " 这种带空格的变体,
    # 精确匹配 "(gdb)"/"(gdb) " 会漏掉它, 于是提示符被当成一条 raw 记录混进流里。
    if not line or line.strip() in ("(gdb)",):
        return None
    sig = line[0]
    if sig not in "^*+=~&@":
        return {"_class": "raw", "text": line}
    body = line[1:]
    if sig in "~&@":                              # 流记录: 整段就是一个带引号字符串
        val, _ = _mi_value(body, 0)
        return {"_class": sig, "_stream": val}
    k = body.find(",")
    tok = body if k < 0 else body[:k]
    fields = {}
    if k >= 0:
        fields, _ = mi_fields(body, k + 1)
    fields["_class"] = sig + tok
    return fields

# ============================================================================
# ② 一次命中的快照
# ============================================================================
class Hit(object):
    """一次断点命中:`wait_hit()` 返回这个。

    字段: bkptno / reason / file / line / addr / func(上面全来自 gdb) + dog_fed(python 补喂的
    时刻, None = 没喂/没配看门狗)。局部量**不在这里预读** —— 每次 `read_vars` 都现查, 读数
    只在一瞬间有意义。"""

    def __init__(self, rec, dog_fed=None, raw=None):
        f = rec.get("frame") or {}
        self.bkptno = rec.get("bkptno")
        self.reason = rec.get("reason")
        self.file = f.get("file")
        self.line = f.get("line")
        self.addr = f.get("addr")
        self.func = f.get("func")
        self.dog_fed = dog_fed
        self.raw = raw or {}

    def where(self):
        return "%s:%s @0x%s" % (os.path.basename(str(self.file)), self.line,
                                str(self.addr).replace("0x", ""))

    def __repr__(self):
        return "<Hit %s %s bkpt=%s>" % (self.reason, self.where(), self.bkptno)

# ============================================================================
# ③ 会话
# ============================================================================
# 留痕打印的互斥锁(2026-09-15): 收侧在 reader **线程**里打、发侧在主线程里打, 不锁的话
# 一次 `print` 的两段(标签 + 正文)会被另一次 `print` 插进中间, 日志里出现拼行。
# 放在模块级而不是 `self` 上: 自检里的**假会话**没跑 `__init__`, 拿不到实例字段。
_TRACE_LOCK = threading.Lock()

# GDB Server 的**内部整读**行首(见 `Session._trace_srv`), **按端各一份**。
#
# J-Link 端: JLinkGDBServerCL 每停一次就把寄存器组/内存整段 dump 一遍, 实测占它全部输出的八成。
# ⚠ `"Read "` 带尾巴那个空格: 这样它**不**匹配 `"Reading …"`(两族要分开列, 别指望前缀包含)。
#
# CMSIS-DAP 端: pyOCD 的 gdbserver 话很少 —— 默认日志级别就是 WARNING(2026-09-20 实测:
#   `python -m pyocd list` 不带 `-v` 一行 INFO 都不吐), 所以这里**没有** J-Link 那种整读族。
#   剩下的是它自己的告警与日志行(带 `相对时间戳 级别 通道:` 前缀, 格式由 pyocd/__main__ 定)。
#   ⚠ 这一族**不是靠前缀包含就能认全的**, 故 DAP 端用正则。
_SRV_NOISE = {
    "jlink": ("Read register ", "Reading register ", "Reading common registers",
              "Read ", "Reading ", "Downloading "),
    "cmsis-dap": (),
}
# pyOCD 的日志行: `0001234 I pyocd.gdbserver: …`(相对毫秒 + 单字母级别 + 通道名)。
# 认的是**这一族**, 而不是逐条枚举消息 —— 枚举法漏掉新加的行, 而漏掉不报错。
_SRV_NOISE_RE = {"cmsis-dap": re.compile(r"^\s*\d+\s+[DIWEC]\s+\S+:\s")}

# 收尾时等 server 自己退多久(秒)。给长了只是多等几秒; 给短了就每场都退化回 `_kill_server` 强杀,
# 而强杀正是要防的那件事 —— 所以宁可长。两端共用这一个数(为什么 J-Link 端需要它, 见
# `_shutdown_server` 的 ⚠)。
_SRV_EXIT_WAIT_S = 15


def _gdb_env():
    """给 gdb 子进程的环境: 摘掉宿主 Python 的 `PYTHONHOME` / `PYTHONPATH`。

    本机 gdb 是 MSYS2 那份(**带 Python scripting** —— gdb 侧 `hook-stop` 喂狗钩子靠它),
    它内嵌的 Python 有自己一套标准库, 按 `gdb-multiarch.exe` 的位置去找。而 `PYTHONHOME`
    一进了环境, 内嵌 Python 就改按它找 ⇒ 起不来。2026-09-21 复现:

        $ PYTHONHOME=… gdb-multiarch.exe -batch -ex "python print('PYOK')"
        Python initialization failed: Failed to import encodings module
        …warning: Python failed to initialize with PYTHONHOME set…
        Python not initialized

    后果不是 gdb 不可用(断点照下), 而是**喂狗钩子静默装不上**: 那一轮日志里出现
    `Python not initialized`, `judge` 记成台面降级 ⇒ 整项「通过」被压成「未定论」。
    `subprocess.Popen` 不带 `env=` 就是原样继承父进程环境, 所以宿主解释器一旦设了这两个变量
    (venv / conda / 某些 IDE)就会踩上。`PYTHONPATH` 是同一类(往内嵌解释器的 `sys.path`
    里塞宿主的东西), 单独设它实测**不**触发上面那两句, 一并摘掉按的是同一类判据。

    ⚠ 不采用 gdb 自己那句 `Python failed to initialize with PYTHONHOME set` 暗示的写法 ——
    它默认让内嵌 Python 去认这个变量。`-ex "set python ignore-environment on"` 是同一件事的
    第二个机制, 只留这一个。
    """
    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    return env


class Session(object):
    """一次 gdb + GDB Server 会话(**server 是哪一个按探针端定**, 见 `_server_cmd`)。
    上下文管理器; 出 with 保证放行核心。

        with Session(out=P.OUT_PATH, watchdog=P.IWDT_SERV, inject_allow=()) as g:
            g.break_at("TaskFreeze.c", 625)
            g.go()                      # 非阻塞: 核心跑起来, 串口这才能通
            CB.write_billday(ser, alt)  # ← 串口触发(同进程!)
            hit = g.wait_hit(10)        # 命中即停; 停那刻 gdb 侧已喂过狗
            print(hit.where(), g.read_vars(["over", "usekWh", "normal"]))
            g.resume()

    构造参数:
        out           .out 路径(Debug 配置, 带全 DWARF)。**必填, 显式传** —— 本模块不认画像。
        watchdog      (绝对地址, 喂入值); 例如 (0x40011400, 0x12345A5A)。**不给就不许下断点**
                      (见文件头代价 2)。地址属"这块表"的机器条件, 由调用方从画像取。
        inject_allow  允许被 inject() 写的表达式名字白名单。**默认空 = 注入整体关闭**。
        port          GDB Server 端口(默认 2331; 与 VS Code 抢口时换)。
    """

    def __init__(self, out=None, watchdog=None, inject_allow=(), port=PORT,
                 server=GDB_SERVER, gdb=GDB, serial_no=jlink.FROM_CARD, device=DEVICE,
                 speed=SPEED, iface=IFACE, quiet=False, mi_async=True, dap_target=DAP_TARGET):
        if not out:
            raise GdbError("必须显式传 out=<.out 路径>(本模块不认画像, 见文件头)")
        if not os.path.isfile(out):
            raise GdbError("找不到 .out: %s" % out)
        # ---- 探针解析(两端通吃, 2026-09-20)----
        # 放在 .out 两道守卫**之后** —— `out` 都没给时就该在上一行报错, 不必去碰探针。
        # 三种入参, 与 `swdbg/probe.py` 的口径**一一对应**(本模块的 gdb 与那条直读通路必须认同一支):
        #   `serial_no` 显式给了序列号 ⇒ 钉死 J-Link 那一支(旧行为, 一字未改)
        #   `serial_no is None`       ⇒ 强制自动: 两端都找, 恰好一支才认
        #   其余(默认 `FROM_CARD`)    ⇒ 问卡带: 先看 `PROBE` 用哪一端, 再看该端的 `JLINK_SN`/`DAP_UID`
        # 生产路径只枚举并锁定唯一候选；正式 GDB server 负责唯一一次 attach。
        # CPUID 证明保留给 `--doctor` 或显式 `pick(verify_target=True)`，避免每场会话
        # 在 server 启动前再制造一次 attach/detach 状态转换。
        report = []
        if serial_no is not None and serial_no is not jlink.FROM_CARD:
            # 显式给序列号: 只可能是 J-Link 那一支(旧调用方就是这么用的)。仍走 resolve_sn 校验它
            # **确实插着**(给了一个不在的 SN 要当场抛, 不能带着它往前走)。
            try:
                backend, ident = probe_jlink.BACKEND, jlink.resolve_sn(serial_no)
            except jlink.JLinkError as exc:
                raise GdbError("解析 J-Link 探针失败: %s" % exc)
            report = ["钉死 J-Link 那一支 SN=%s(调用方显式给了序列号, 不自动挑)" % ident]
        else:
            try:
                backend, ident, report = probesel.pick(declared=None, speed=speed,
                                                  device=device, iface=iface,
                                                  verify_target=False)
            except probesel.ProbeSelectError as exc:
                raise GdbError("解析探针失败: %s" % exc)
        # 逐条记账留给 `open()` 里打(见那段 ⚠): `self.trace` 到本函数末尾才存在, 现在打会静默丢掉。
        self.probe_report = list(report)
        self.backend = backend
        self.probe_ident = ident
        self.serial_no = ident if backend == probe_jlink.BACKEND else None
        self.dap_target = dap_target
        # 机器条件齐不齐 —— 在连任何东西**之前**核。到这儿 ident 已是个具体值(上面解析过),
        # 所以 ready() 拦的是"卡带里其余几项没填"。
        # ⚠ **按端传对应的那一项**: 在 DAP 端传 `JLINK_SN=None` 会当场误报"卡带没填 J-Link 序列号"
        #   (J-Link 压根不在这台机器上), 反之亦然。这就是 ready() 的 `_bad` 判 None 的做法要防的。
        if backend == probe_jlink.BACKEND:
            machspec.ready(GDB=gdb, GDB_SERVER=server, JLINK_SN=ident,
                      DEVICE=device, IFACE=iface, SPEED=speed, GDB_PORT=port)
        else:
            machspec.ready(GDB=gdb, DAP_UID=ident, DAP_TARGET=dap_target, SPEED=speed, GDB_PORT=port)
        self.out = out
        self.watchdog = tuple(watchdog) if watchdog else None
        self.inject_allow = tuple(inject_allow or ())
        self.port = int(port)
        self.server_exe = server
        self.gdb_exe = gdb
        # `self.serial_no` / `self.backend` / `self.probe_ident` / `self.dap_target`
        # 都已在上面那段探针解析里定好(DAP 端 `self.serial_no` 是 None —— 那一端没有这个概念)。
        self.device = device
        self.speed = speed
        self.iface = iface
        self.quiet = quiet
        # mi-async: **目标跑着时还收不收 MI 命令**的开关(见 _start_gdb 里的 ⚠)。
        # 默认开 —— 这正是 VS Code/cortex-debug 这类 IDE 前端赖以工作的设置。
        self.mi_async = bool(mi_async)

        self._srv = None
        self._gdb = None
        self._probe_lease = None
        self._q = queue.Queue()
        self._err = []
        self._threads = []
        self._outstanding = 0         # 已发未应答的 MI 命令条数(_cmd 靠它认领自己的应答)
        self._pending = None          # go() 期间抢跑的 *stopped
        self._pushback = collections.deque()   # `_drain_async` 退回的非 *stopped 记录(**必须排在 _q 之前读**)
        self._stopped = False
        self._bps = {}                # bpno -> (file, line, addr)
        self._elfmap = None           # 整片 .out 地图, 由 `gdbinit.build(self)` 建(见该文件头)
        self._injected = []           # [(expr, addr, size, old_bytes)]
        self._stack_injected = set()  # 其中落在**栈区**的地址 —— 收尾**不还原**(见 inject 的 ⚠)
        self._dog_feeds = 0
        self._closed = False
        self._hook_installed = False  # gdb 侧喂狗钩子装上没(装不上只告警, python 侧兜底)
        # ---- 告警的两档(**不是**同一件事, 见 _warn) ----
        # degradations: **台面缺了本该有的能力** ⇒ 进账本, 把整项的「通过」压成「未定论」
        #              (`judge.Judge.feed_degradations`, 见 judge 模块头 四)。
        #              2026-09-11 前这两档混在一起、且**只打印** —— 100+ 份日志带着
        #              「喂狗钩子没装上」而账本与退出码里一个字都没有。
        # warnings:    台面发生了什么、且**做成了**(如残留 FPB 已清零) ⇒ 只印给人看, 不影响结论。
        self.degradations = []
        self.warnings = []
        self.other_hits = []          # 等目标断点时"掠过"的其它命中(见 wait_break)
        # ---- 强制留痕(2026-09-15) ----
        # 目的: 一次跑完之后 `log/<名>_<时间戳>.log` 里必须**看得见**这条调试通路做了什么。
        # 在此之前只有"开场横幅 / 下断点 / 收尾取证"三个孤立点有输出, 中间整段无痕 ——
        # 断点下了没下、go 发出去没有、watch 读了哪几个变量、读回来什么, 日志里一个字都没有。
        # 做法不是到处补 print(那样迟早有人新写一个函数忘了补), 而是**钉在两个卡口**上:
        #   发: `_send()`                       —— 全库唯一的 MI 命令出口
        #   收: `_spawn_reader` 里的 `_q.put()` —— 唯一的 gdb stdout 入口
        # 于是 `break_at`/`go`/`resume`/`ensure_running`/`read_vars`/`read_regs`/`step_one_pc`/
        # `inject*`/`wait_hit`/`clear_breaks`/`_feed_quiet` … 全部自动留痕, 无一例外。
        # 谁来守"没人绕过卡口": 自检里的 `_gate_chokepoints`(AST 判据, 谁直接戳 stdin/stdout 当场红)。
        # ⚠ 自检里的**假会话**(`Session.__new__(Session)` 造的)没跑过这里 ⇒ 所有读点一律
        #   `getattr(self, "trace", False)`; 假会话于是不打 —— 它们靠 `_gdb.stdin.getvalue()`
        #   断言"命令究竟发出去没有", 多几行字就把那些断言搅了。
        self.trace = True
        # 探针**怎么选出来的**逐条记账(枚举到几支 / 哪一支读 CPUID 证明了 / 最后选中谁)。
        # ⚠ 放在这一行**之后**: `self.trace` 刚存在, 在那之前 `_trace_emit` 会静默丢掉(它按
        #   `getattr(self, "trace", False)` 判)。这是本模块里 `[探针]` 那一格的**唯一**落笔处 ——
        #   与 `[SWD]`(连上之后读什么)分开: "探针没选出来"与"gdb 这边的毛病"要能一眼分开。
        for _rep in (getattr(self, "probe_report", None) or []):
            self._trace_emit(_rep, who=loglabel.DEBUG_PROBE)
        # 原先这里还有两个"被筛掉多少条"的计数(`_trace_skipped`/`_srv_skipped`)。2026-09-18 起
        # **不再筛任何一条**(见 `_trace_emit` 的 docstring), 计数恒为 0, 遂一并删掉。

    def _warn(self, msg, what=None):
        """告警: 非致命的问题要说出来, 但**不打断**整个取证(与 quiet 无关, 本来就该看到)。

        两档, **必须分清** —— 分不清的话要么把"做成了"记成"降级"(过计), 要么把"缺能力"放过去(漏计):

            `what="<短标签>"`  ⇒ **降级**: 台面缺了本该有的能力(钩子没装上 / 残留清不掉)。
                                 记进 `self.degradations`, 印 `[gdb] !! `。
            `what=None`(默认)  ⇒ **提醒**: 发生了什么、且**做成了**。记进 `self.warnings`,
                                 印 `[gdb] 注 `。

        ⚠ 降级**不只是打印**(2026-09-11 修, 本仓最大的一个洞): 脚本收尾要
        `J.feed_degradations(sess)` —— 否则"没喂到狗的那一轮"与"狗喂得好好的那一轮"退出码一样。
        单靠打印时, 100+ 份日志带着 `[gdb] !!` 而账本与退出码里一个字都没有。
        """
        # ⚠ 记账这一段**必须自己扛住**: 本函数的契约是"**不打断**整个取证"(见上一行),
        #   而它现在往两张表里记账 —— 表要是没有(自检里 `Session.__new__` 造的假会话没跑
        #   `__init__`), `AttributeError` 就会**从"非致命的告警"里抛出去把整轮打停**。
        #   2026-09-11 实踩: swdbg 自检就是这么崩的(横幅之前那一片 OK 全不算数)。
        #   宁可这条降级没记上, 也不能让"报告一个问题"这件事本身成为新问题。
        try:
            (self.degradations if what else self.warnings).append(
                judge.degradation(what, msg) if what else {"what": msg, "detail": msg})
        except AttributeError:
            pass
        # ⚠ 字形**不在这里拼** —— 走 `runlog` 那两个常量(它们由 `loglabel.debug_line` 出)。
        #   原先这里手写 `"[gdb] !! "`/`"[gdb] 注 "`, 是同一件事的第二个落笔处, 且来源写死成 gdb。
        print("   %s%s" % (runlog.MARK_DEGRADED if what else runlog.MARK_NOTE, msg),
              file=sys.stderr if self.quiet else sys.stdout)

    # ---------------------------- 强制留痕(2026-09-15) ----------------------------
    # 这三个函数是**唯一的留痕出口**。调用点只有两个: `_send`(发) 与 `_spawn_reader`(收)。
    # 别在别处调它 —— 那会把"哪些动作留痕"变成一份靠人记的清单, 而这份清单一定会漏。
    def _trace_emit(self, text, who="gdb"):
        """打一行留痕。发侧/收侧/server 侧共用**这一个出口**。

        ⚠ **2026-09-18 起不再筛任何一条**(用户定: 不损失信息)。原先 `=`/`~` 这两种 MI 记录与
          gdb server 的寄存器/内存整读会被**计数略过**, `.log` 里只留一行"略过 N 条" ——
          于是"日志里没有这一行"与"这件事没发生"分不开, 正是本仓反复治理的那类错; 而 `=`/`~`
          在真实会话里又极吵, 当时是拿"可读"换"完整"。

          用户定的口径是**不损失**: 那些行现在**原样进 `.log`**。原先那句"反正 `.jsonl` 里
          一条不少"不再是理由 —— 两处都全, 复盘时不必开两个文件对着看。
          ⚠ 代价说在前面: `.log` 会明显变长。3-1 那轮 `.log` 是 225KB, 而 `[gdb]`+`[srv]` 已占
            63.7% —— 那是**筛过一轮之后**的数, 现在这一轮筛子拆了。
        """
        if not getattr(self, "trace", False):
            return
        with _TRACE_LOCK:
            print("   %s" % loglabel.debug_line(who, text))

    def _trace_srv(self, text):
        """一条 JLinkGDBServerCL 的原始行 → 留痕。

        ⚠ **2026-09-18 起全部留痕**(原先筛掉 `_SRV_NOISE` 那一族, 见 `_trace_emit`)。
          被筛掉的是"每停一次就把 r0-r12/sp/lr/pc 逐个 dump 一遍"这类**server 自己的内部整读**
          (`Read register 'r8' …`) —— 实测 3-2 那轮它吐 685 行、其中约 550 行是这个。它们不是我们
          做的动作, 也确实会把要看的 MI 记录埋掉; 但**"埋掉"是读的人的事, 不是库的事** ——
          库只负责一条不丢地记下来, 该打什么记号留给读方判。

        `noise=` 那个记号**保留**: 它现在不再是"筛不筛"的开关, 只是给读方(和 `.jsonl` 的消费方)
          一个"这行是 server 内部噪声"的**标记** —— 判据留在数据里, 而不是替读方做掉。

        ⚠ 2026-09-20 起"噪声"的判据**按端取**(`_SRV_NOISE` / `_SRV_NOISE_RE`): 两支探针的
          server 不是同一个程序, 它自己的那些行自然长得不一样。假会话(`Session.__new__`)
          没有 `backend` ⇒ 按 J-Link 那一族判(那是本模块 2026-09-20 前唯一的一端)。
        """
        events.emit("srv", text=text, noise=self._is_srv_noise(text))
        self._trace_emit(text, who="srv")

    def _is_srv_noise(self, text):
        """这一行是不是 server **自己的**内部输出(判据按端取, 见 `_SRV_NOISE`)。"""
        be = getattr(self, "backend", None) or probe_jlink.BACKEND
        if text.startswith(_SRV_NOISE.get(be, ())):
            return True
        rx = _SRV_NOISE_RE.get(be)
        return bool(rx and rx.match(text))

    def _trace_sent(self, cmd):
        """一条 MI 命令**发出去**了。发侧**全打**, 不筛 —— 命令是有限的, 而且"发了什么"
        正是复查时最需要的(发的和读回来的对不上, 一眼就能看出来)。"""
        events.emit("gdb.cmd", cmd=cmd)          # ★ 事件流: 发侧**全打**(与下面那句同一个判据)
        self._trace_emit("→ %s" % cmd)

    def _trace_recv(self, text):
        """一条 gdb stdout 原始行 → 留痕。**发全打, 收也全打**(2026-09-18 起)。

        ⚠ 原先**收筛**: `=`(通知) 与 `~`(console 输出) 在真实会话里极吵 —— gdb 启动时会连着吐
          `=thread-group-added`、四条 `=cmd-param-changed`, 而 `~` 会带上它自己那一堆载符号的输出,
          把真正的结果记录(`^done`/`^error`/`*stopped`)埋进几十行噪音里。当时的口径是"可读优先,
          反正 `.jsonl` 里一条不少"。2026-09-18 用户定成**不损失信息** ⇒ 这一路也全打。
        ⚠ 唯一仍然不打的还是**空行与提示符** —— 它们**不是记录**(与 `_next` 的判据同一口径),
          所以"日志里没有这一行"仍然只表示"它不是一条记录", 不表示"它被筛掉了"。这个口径
          两条路(人读的 `.log` / 机器读的 `.jsonl`)必须一致。
        """
        s = text.strip()
        if not s or s == "(gdb)":
            return                      # 提示符与空行**不是记录**(与 `_next` 的判据同一口径)
        events.emit("gdb.reply", text=s)
        self._trace_emit("← %s" % s)

    # ---------------------------- 生命周期 ----------------------------
    def open(self):
        """开会话, **返回时核心必定在跑、断点单元必定是空的**。半途失败也要收干净(见下)。

        两道收尾, 各治一个"会话一开、串口全哑"的真病因(2026-09-11 一次查清, 下面是最终版):

        ① `_settle_after_connect()` → `ensure_running()` —— **JLinkGDBServerCL 一起来就把核心
           停住**, 不等 gdb 连上来。而"核被停住"那条 `*stopped` 是**异步通知**, 比 `^connected`
           晚到; 不看它一眼就判停, 会判成"已在跑"而**一个 continue 都不发**。停着的核 = 表完全
           不应答串口(645/698 一条都收不到回), 表象与"串口坏了/表死机"一模一样。
           ⚠ **CMSIS-DAP 端这一步按设计什么也不做**: pyOCD 那边写死了 `connect_mode=attach`
           (见 `_server_cmd`), 它**本来就不停核** ⇒ 没有那条 `*stopped` 要等, `ensure_running()`
           读到"在跑"就直接返回 —— 两端走**同一段代码**, 差别只在"要不要放行"。这条差别不是靠
           两套分支表达的, 而是靠"停着才放行"这个判据本身对两端都成立。
        ② `_clear_stale_fpb()` —— 更阴的一路: **上一场会话残留的硬件断点槽**。残留会跨会话活下来
           (`-target-detach` 没全清), 于是它不属于本会话、也撤不掉(`-break-delete` 只认自己的
           bp 号); 核一执行到那个地址就 SIGTRAP 停住。若那个地址是**高频行**(如 `TaskTime.c:1087`
           受 SPI 时间对象驱动、约 1 Hz), 核每次被放行**不到 1 秒就又停住** ⇒ 串口永久哑掉。
           本会话一个断点都还没下, 所以 FPB 里有内容 = 按定义是残留, 清掉不会误伤。
        ③ `_clear_stale_dwt()` —— 同一路的**第二套硬件**: "在某个变量被写时停住"用的是数据
           观察点单元(DWT), 不在 FPB 里, ②清不到。2026-09-17 实踩: 一个留在 `DWT_COMP0` 上的
           `g_PowP[0]` 观察点让 `open()` 之后**整场串口全 RX(0)**(`[srv] Target halted
           (Unknown data BP / WP, ...)`)—— 而那个地址每周期都被写, 于是与②同一种病。
           ⚠ **`python -m swdbg.restore` 也救不了它**: 那边"核心本就在跑 ⇒ 无需动任何东西"直接返回。

        早先这里只靠一条**调用方纪律**兜着("开调试会话排在所有串口动作之后, 第一件事就是下断点")
        —— 4-6/3-2 恰好是这个形状, 所以一直没露。但 2-1 是"**先读钟、后执行**", 中间一个 continue
        都没有 ⇒ 整轮串口全哑, 整项记"未定论"(2026-09-11 首跑就是这么废的)。
        纪律兜不住的东西就不该靠纪律: **"open 完核在跑、槽是空的"是本层的抽象不变式**。
        (⚠ 裸 `-exec-continue` 救不了②: 它会被 gdb 以 `Cannot execute this command while the
        selected thread is running.` 拒掉 —— 所以走 `ensure_running()` 的判停, 不自己发 continue。)
        """
        if self._gdb is not None:
            return self
        # GDB server 本身会持有探针；把 lease 放在 server 启动前，防止直读
        # 或另一场 GDB 会话同时进入同一支 J-Link/CMSIS-DAP。
        self._probe_lease = acquire("gdb-session")
        try:
            self._check_port_free()
            self._start_server()
            self._start_gdb()
            self._settle_after_connect()   # ⚠ 先等 server 那条 `*stopped` 落定, 再判停/放行
            self._clear_stale_fpb()        # ⚠ 见 docstring: 残留硬件断点会让核一跑到那地址就 SIGTRAP
            self._clear_stale_dwt()        # ⚠ 见 docstring: 残留数据观察点让 J-Link 一连上就把核停住
            self.ensure_running()          # ⚠ 见 docstring: 不放行的话, 调用方在打第一次之前发不出帧
        except Exception:
            # 半途失败最危险: server 已经起来(核心已停), 若就这么把异常抛上去, 对象没人再 close,
            # server 常驻、核心永久 halt —— 表看起来"串口坏了"。2026-09-10 真踩过这一下。
            self.close()
            raise
        return self

    def _release_probe_lease(self):
        lease, self._probe_lease = self._probe_lease, None
        if lease is not None:
            lease.release()

    def _check_port_free(self):
        """端口被占 = 多半有个残留的 GDB Server(或 VS Code 会话) —— 早报错, 别连上去搅和。"""
        import socket
        s = socket.socket()
        try:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", self.port)) == 0:
                raise GdbError(
                    "端口 %d 已被占用 —— 多半是残留的 JLinkGDBServerCL / VS Code 调试会话。\n"
                    "  先跑 `python -m swdbg.restore` 清残留, 或用 port=<其它口>。" % self.port)
        finally:
            s.close()

    def _server_cmd(self):
        """按端给出 GDB Server 的命令行 —— 两端**只差这一句**(其余收尾/复核完全共用)。

        J-Link 端: SEGGER 的 `JLinkGDBServerCL`, 器件名与速度都从卡带取。

        CMSIS-DAP 端: `python -m pyocd gdbserver`。每一处都有它的理由, 改之前先看:
          · **用 `sys.executable` 而不是裸 `pyocd`** —— 保证起的就是**本仓这个解释器**里的 pyOCD
            (裸命令会走 PATH, 挑到另一个环境时"装没装/装哪版"就说不清了)。
          · `-u <uid>` 钉死这一支, 与 J-Link 端的 `-select usb=` 同一个口径: 已经证明过是它,
            就不许 server 自己再挑一遍。
          · `-f <Hz>` **必须显式给**(铁律 1) —— 卡带 `SPEED` 是 kHz, pyOCD 要 Hz。
          · `-O connect_mode=halt` **必须写死, 不许改回 attach**。这条是拿一整天的失败换来的:
            pyOCD 的默认值本就是 `halt`(`core/options.py:55`), 而 **J-Link 那条命令行里根本没有
            connect 相关选项** —— 它走的就是"连上即停核"。当初一度把它写成 `attach`(理由: 别把表
            停住), 结果是**给 DAP 端单独造出一条 J-Link 端不存在的路**:
            · J-Link 端: 核在 gdb 连上时已被停住, 之后 gdb 要读数只需"继续/再停", 全程由调试器
              自己掌着核;
            · 被改成 attach 后: 核一直跑着, **每次读数都得让 pyOCD 去把一颗自己从没停过的、
              正在运行的核停下来** —— 而实测这一步在这支 DAPLink 上就是走不通:
              `Transfer error while checking target status ... (No ACK)`, 随后
              `interrupt 超 10s 都没停住`, 整场会话报废(2026-09-20 实表 13/32 个日志命中;
              此前 09-09~09-18 共 250 个日志(全是 J-Link 口径)一次都没有)。
            ⇒ 与 J-Link 对齐才是对的: **连上就停住, 由调试器掌着核**。
            `resume_on_disconnect` 写出来是因为它正是"关掉即放行"所依赖的那一项, 它一旦改能被看见。
          · `-O soft_bkpt_as_hard=True` **必须写, 不写则断点全部形同虚设**(2026-09-20 实表查出,
            这一条是拿一整轮回归的失败换来的, 病因埋在 pyOCD 与 gdb 对"Z0"的两种理解之间):
              gdb 给** flash 地址**下断点时发的是 **Z0**(0 号包 = 官方口径里的"软件断点"),
              由 GDB Server 自己决定在 flash 上怎么办 —— JLinkGDBServerCL 会译成 FPB 比较器
              (那正是"flash 断点"这个词的来历), 而 pyOCD 的 gdbserver 默认**照字面办**:
              `bkpt_type = HW if self.soft_bkpt_as_hard else SW`(`gdbserver.py:671`, 选项默认
              False, `core/options.py:204`), 于是它去**往 flash 里写 `BKPT` 指令** —— 写在只读区,
              什么也没发生, 可它照样回 `OK`。
              实测证据(2026-09-20, 台上只有 DAPLink):
                · 挂 `kWhData.c:448`(0x00023348) 与两条**核在几秒前刚跑过**的行
                  (`ST75263S.c:171` I2C_TxByte / `Communicate.c:283` Comm_Service),
                  15s 内**一条都不命中** —— 而核确实在跑(同一次会话里它正从 Comm_Service 走到
                  I2C_TxByte)。三条断点全哑 ⇒ 不再是"某一行没走到"能解释的。
                · 挂完之后读 FPB(`-data-read-memory-bytes 0xE0002000 24`):
                  `FP_CTRL=0x00000040`(ENABLE=0, 四位 NUM_CODE=4) 且四个 COMP 全 0 ——
                  **pyOCD 一个比较器都没动**。
                · 反证: 同一支探针、同一颗核, 绕过 gdb 直接 `set_breakpoint(..., HW)` 再
                  `bp_manager.flush()`, FPB 当场变成 `FP_CTRL=0x41` / `COMP0=0x40023349`
                  (FPBv1 编码: 匹配位+地址+使能位), 撤掉后回到 0。
                  ⇒ **硬件收得下, 缺的只是上面那一句配置。**
              ⚠ 这也解释了为什么 J-Link 端没露过这一手: 那条路上 gdb 发的同样是 Z0,
                只是 JLinkGDBServerCL 自己译成了 FPB。
          · `-W`(不要等探针): 探针不在时**当场报错**, 而不是挂在那儿等下一个人插上 ——
            否则这里会退化成"10s 没起来"这种看不出病因的报错。
          · `--no-config`: pyOCD 会去**当前工作目录**找 `pyocd.yaml`/`pyocd_user.py`。本仓没有,
            但这个"看 cwd 才知道行为"的性质必须掐掉 —— 同一行命令在不同目录下行为不同,
            正是本仓反复治理的那类静默差异。
        """
        if self.backend == probe_cmsis.BACKEND:
            return [sys.executable, "-m", "pyocd", "gdbserver",
                    "--no-config", "-W",
                    "-u", str(self.probe_ident),
                    "-t", self.dap_target,
                    "-f", str(int(self.speed) * 1000),      # 卡带是 kHz, pyOCD 要 Hz
                    "-p", str(self.port),
                    # ⚠ 必须 halt —— 与 J-Link 端"连上即停核"对齐。改成 attach 会让 pyOCD 去停一颗
                    #   它自己从没停过的运行核, 那一步在这支 DAPLink 上直接 No ACK。见本函数 docstring。
                    "-O", "connect_mode=halt",
                    "-O", "resume_on_disconnect=True",
                    # ⚠ 不写这一句, gdb 下的每一个断点都是空响的炮 —— 理由见本函数 docstring。
                    "-O", "soft_bkpt_as_hard=True"]
        return [self.server_exe,
                "-select", "usb=%d" % self.serial_no,
                "-device", self.device,
                "-if", self.iface,
                "-speed", str(self.speed),
                "-port", str(self.port),
                # ⚠ `-singlerun` 是**收尾能不能干净**的关键, 别省(2026-09-20 实测定案)。
                #   J-Link GDB Server 默认是"服务完一个客户端还接着等下一个", 于是 gdb 断开之后
                #   它**不自退**, 只能靠往 stdin 写命令请它退 —— 而实测那句命令**靠不住**:
                #   `exit\n` / `q\n` 它一个字不认(裸起 server 时 `Exit\n` 1.1s 退, 但真会话里
                #   gdb 断过之后写 `Exit\n` 等 15s 也不退)。退不掉就落到 `_kill_server` 强杀,
                #   而强杀 JLinkGDBServerCL 正是把核心撂在 halt 的元凶 —— 修前每场会话都这样收尾。
                #   加上这一句, server 在 gdb 断开时按自己的设计退出, 不用求任何人。
                "-singlerun"]

    def _start_server(self):
        cmd = self._server_cmd()
        try:
            self._srv = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        except FileNotFoundError:
            raise GdbError("起不了 GDB Server: %s\n  命令: %s"
                           % (cmd[0], " ".join(cmd)))
        # 排空 server 输出, 免得管道写满把它卡死(内容只留在 self._err 排障用)
        self._spawn_reader(self._srv, self._err, tag="server")
        if not self._wait_ready(10.0):
            if self._srv is not None:
                self._kill_server(self._srv)
                self._srv = None
            msg = ("GDB Server 10s 内没起来(端口 %d, %s 端)。常见原因: ①探针被别的进程占着\n"
                   "  ②USB 没插好 ③端口被占。先跑 `python -m swdbg.restore`。\n"
                   "  实际命令: %s\n  它自己吐的行: %s"
                   % (self.port, self.backend, " ".join(cmd),
                      " | ".join(self._err[-3:]) or "(一行都没有)"))
            # 这一笔是**根因**, 而且它带的证据是别处拿不到的: 起 server 的**实际命令行**与
            # server **自己吐的那几行**。2026-09-20 那次失败里, 那几行显示的是
            # `Connected to target` / `Waiting for GDB connection...` —— 服务器那头**是通的**,
            # 病灶在别处; 只看一句"10s 没起来"会把人支去查探针和 USB, 那两样当时都是好的。
            faultlog.record("GDB-SESSION", subsystem="gdb", text=msg,
                       tried=["起 GDB Server", "等它吐可服务的信号(超时 10s)"],
                       snapshot={"端口": self.port, "探针端": self.backend,
                                 "命令行": cmd, "server 最后三行": list(self._err[-3:])},
                       next_step="python -m swdbg.restore")
            raise GdbError(msg)

    def _wait_ready(self, timeout):
        """等 GDB Server 真起来 —— **两端一律认它自己吐的那一行, 绝不做 TCP 探活。**

        为什么连 J-Link 端也不能探活(2026-09-20 定案, 原先那一端是 `connect_ex` 探端口):
        J-Link 端现在带 `-singlerun` 起(理由见 `_server_cmd`), 而这个模式的口径是
        **"gdb 客户端一断开就退"** —— 探活那一下 `connect()`+`close()` 在它眼里就是一个
        连上又断开的客户端。实测(裸起带 `-singlerun` 的 server, 探活一次): server 当场退出,
        紧接着的下一次连接直接被拒。
        这不是新问题: DAP 端 2026-09-20 就踩过同一颗雷(拿一次实表回归的失败换来的)——
        pyOCD 的 gdbserver **把"第一个连上来又断开的客户端"当成收摊信号**, 探活那句
        `connect_ex` 恰恰成了那个"第一个客户端", 于是 10s 后 gdb 的 `-target-select` 收到
        `could not connect (error 138)`(连接被积极拒绝), 表面症状是"gdb 连不上 server",
        病因却是"探活把 server 探死了"—— 正是本仓反复治理的那类"现象与病因分家"的故障。
        实测那次的复现时序(原样留在 `log/1_2_energy_mirror_20260920_094736.log`):
          0001727 I GDB server listening on port 2331 (core 0)   ← 本函数要等的那一行
          0001956 I Client 1 connected on port 2331 from remote address localhost:49760   ← 探活
          0002066 I Client 1 disconnected from port 2331                   ← 探活收线
          0002143 I STDIO server stopped                                   ← 它决定收摊
        ⇒ 两端现在是同一条口径, `_wait_port()` 那个"只许 J-Link 端用"的特例连同函数一起没了。

        各端认哪一句:
          · J-Link 端 —— `Waiting for GDB connection`(它连上目标之后就在等 gdb 了, 这是
            "可以来连"的确证)。兜底再认 `Listening on TCP/IP port`: 版本改了句尾时不至于失联。
          · CMSIS-DAP 端 —— `listening on port`。认的是这半句而**不带端口号**: 端口是我们
            自己 `-p` 传进去的, 不存在认错口的问题; 不带版本相关的尾巴, pyOCD 升级改了句尾
            也不至于失联。(顺带: `STDIO server started on port 4444` 是另一句, 用的是
            `started`, 不会误命中。)
        """
        marks = (("waiting for gdb connection", "listening on tcp/ip port")
                 if self.backend != probe_cmsis.BACKEND else ("listening on port",))
        end = time.time() + timeout
        while time.time() < end:
            if any(m in ln.lower() for ln in self._err for m in marks):
                return True
            if self._srv is not None and self._srv.poll() is not None:
                return False          # 进程自己先没了 —— 不必再等满 timeout
            time.sleep(0.05)
        return False

    def _start_gdb(self):
        cmd = [self.gdb_exe, "--interpreter=mi2", "-q",
               "-ex", "set confirm off",
               "-ex", "set pagination off",
               "-ex", "set width 0",
               "-ex", "set height 0",
               # ⚠ 默认 `print elements 200` 会把长数组截到前 200 个元素: 4-1 要读的
               #   `INT8U buff[LEN_ImmedFrez]` 是 798B, 电量要到第 486B, 不关就只回前 200B
               #   (前 3 个电能量对象), 后面 5 个静默地读不到。关的只是**打印**上限, 不改读内存。
               "-ex", "set print elements 0"]
        # ⚠ mi-async **必须在这里(启动命令行)设**, 不能在 `-target-select` 之后用 `-gdb-set`:
        #   2026-09-10 实测, 连上 remote 之后发 `-gdb-set mi-async on` 被拒 ——
        #   `^error,msg="Cannot change this setting while the inferior is running."`
        #   (gdb 连上远端目标后就把 inferior 视作 running, 而此时正是"还没跑、想先把开关设好"的当口)。
        #   放 `-ex` 里则在任何目标被选之前生效, 没有这个冲突。原因与后果见下方 `-target-select` 后那段 ⚠。
        if self.mi_async:
            cmd += ["-ex", "set mi-async on"]
        try:
            self._gdb = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, bufsize=0, env=_gdb_env())
        except FileNotFoundError:
            raise GdbError("找不到 gdb: %s" % self.gdb_exe)
        self._spawn_reader(self._gdb, None, tag="gdb", into_queue=True)
        self._spawn_reader_stderr(self._gdb)
        r = self._cmd('-file-exec-and-symbols "%s"' % self.out.replace("\\", "/"))
        if not r or r.get("_class") != "^done":
            raise GdbError("gdb 载入 .out 失败: %s" % (r or "无应答"))
        r = self._cmd("-target-select remote 127.0.0.1:%d" % self.port, timeout=20.0)
        # ⚠ `-target-select` 的**成功**结果是 `^connected`(不是 `^done`) —— MI 规范就这么定的。
        # 2026-09-10 实测: 只认 ^done 会把一次成功连接误判成失败。
        if not r or r.get("_class") not in ("^connected", "^done"):
            raise GdbError("gdb 连 GDB Server 失败: %s" % (r or "无应答"))
        # ⚠ **必须先开 mi-async, 再下断点/放行** —— 这是本模块最要紧的一条设置。
        # 默认(all-stop 同步)下 gdb 发出 continue 后就**阻塞在 remote 等 stop reply**, 期间
        # **一条 MI 命令都不服务**: 实测 `-break-delete` / 纯查询 `-data-list-register-names` /
        # 连唯一能叫停核的 `-exec-interrupt` 全无应答(各等 5-10s)。2026-09-10 为这个根因打了一堆
        # 守卫(clear_breaks 跑动时拒绝 / drop= 趁停住撤断点 / close 只在停住时 detach)——
        # 开了 mi-async, "跑着时撤断点/叫停核"才成立, 那些守卫从"必需"降级成"保守", 尚保留。
        if self.watchdog:
            self._install_hook_stop()

    def _spawn_reader(self, proc, sink, tag, into_queue=False):
        """gdb 的 stdout 走 queue(MI 记录流); server 的 stdout 只排空, 免得管道堵死。"""
        def run():
            try:
                for raw in iter(proc.stdout.readline, b""):
                    text = raw.decode("utf-8", "replace")
                    if into_queue:
                        self._q.put(text)
                        # ★ 留痕的**收侧唯一卡口**(2026-09-15)。放在这里而不是 `_next()` 里:
                        #   `_next` 前面还有 `_pushback`(`_drain_async` 退回的记录会从那儿**重读**一遍),
                        #   放那儿同一条 gdb 输出会被打两遍。放 reader ⇒ 一条输出只留一次痕。
                        self._trace_recv(text)
                    else:
                        sink.append(text.rstrip("\r\n"))
                        # server 那一路(JLinkGDBServerCL 自己的话)也留痕: "连不上表"这类问题
                        #   的病因多半就在这几行里, 而原先它们只进 `self._err`, **从不打印**。
                        #   用 `[srv]` 前缀与 MI 流分开, 内部整读行按计数归并(见 `_trace_srv`)。
                        self._trace_srv(text.rstrip("\r\n"))
            except Exception:
                pass
        t = threading.Thread(target=run, name="breakpoint-%s" % tag)
        t.daemon = True
        t.start()
        self._threads.append(t)

    def _spawn_reader_stderr(self, proc):
        """gdb 的 stderr 单独排空(里面是 CP1252 编码告警之类) —— **绝不能并进 MI 流**,
        那会把记录切碎。"""
        def run():
            try:
                for raw in iter(proc.stderr.readline, b""):
                    self._err.append(raw.decode("utf-8", "replace").rstrip("\r\n"))
            except Exception:
                pass
        t = threading.Thread(target=run, name="breakpoint-gdb-err")
        t.daemon = True
        t.start()
        self._threads.append(t)

    def close(self):
        """收尾(幂等): 恢复注入 → detach → gdb 退出 → server 干净退出 → **复核核心真在跑**。

        复核不过就自动叫 restore.release_debug() 兜底。**全程不 terminate 活着的 server**
        (强杀正是把核心撂在 halt 的元凶)。"""
        if self._closed:
            return True
        # 不要在收尾开始时就标记 `_closed`。中途异常时必须允许下一次 close() 重试，
        # 否则 server/核心可能仍未释放却再也没有收尾机会。
        # 构造了 Session 但从未 open() 的对象没有任何资源可释放；尤其是 __del__
        # 路径不能因此再开一场 Probe 去“复核”，否则一次失败的构造会额外占探针。
        if self._gdb is None and self._srv is None:
            self._release_probe_lease()
            self._closed = True
            return True
        try:
            self._restore_injections_locked()
        except Exception:
            pass
        if self._gdb is not None:
            # 走 MI 干净收尾的前提是"跑动中 MI 也服务命令"。开了 mi-async 之后这成立
            # (2026-09-10 实测: 跑动中 `-data-list-register-names` / `-exec-interrupt` 都秒回 ^done),
            # 于是**不再按 `_stopped` 分叉** —— 一律 detach + gdb-exit。这条路顺带治了"残留 server":
            # gdb 干净退出 → server 察觉客户端断开自己收摊 → 端口 2331 不再被占(修前 gdb 被强杀,
            # server 变孤儿, 下一轮直接 "端口已被占用")。
            # mi_async=False 时退回老逻辑: 跑着就只拆会话, 硬等 20s 是白等。
            #
            # ⚠ 上面那句"一律 detach"是**错的**(2026-09-16 改正): `-target-detach` **跑动中发不出去**,
            #   gdb 一律回 `^error,msg="Cannot execute this command while the target is running."`。
            #   原先无条件发、异常照吞 ⇒ 每场会话收尾都往日志里留一行红字(当天 7 跑 7 中),
            #   看着像收尾失败而其实每一步都做成了 —— 正是本仓反复治理的那类"看着像故障的噪音"。
            #   真放行核心的是**后面 server 的干净退出**(`_verify_released()` 复核), detach 只在
            #   停着那条路上少让 server 自己收摊一次。所以按核的状态分岔, **不靠吞异常遮**。
            if self.mi_async or self._stopped:
                if self.backend == probe_cmsis.BACKEND and not self._stopped:
                    # ★ DAP 端专属: **跑着走人会让 pyOCD 的 gdbserver 卡死不退**(见 _shutdown_server
                    #   的 ⚠)。所以先把它叫停, 再 detach(停着才发得出去), 核心交给 pyOCD 的
                    #   `resume_on_disconnect` 放行 —— 那是它自己的设计语义, 不是我们的补丁。
                    #   J-Link 端**不需要这一手**: 那一端原先"等不到自己退"的真因不在核的状态,
                    #   而在退出命令写错(见 `_shutdown_server` 的 ⚠ 2026-09-20)。
                    try:
                        self.ensure_stopped(timeout=5.0)
                    except Exception:
                        pass
                if self._stopped:
                    try:
                        self._cmd("-target-detach", timeout=10.0)     # detach: 目标继续跑
                    except Exception:
                        pass
                try:
                    self._cmd("-gdb-exit", timeout=10.0)
                except Exception:
                    pass
            try:
                self._gdb.wait(timeout=8)
            except Exception:
                try:
                    self._gdb.kill()                          # gdb 本身可以强杀; 不能强杀的是 server
                except Exception:
                    pass
            self._gdb = None
        self._shutdown_server()
        if self._srv is None:
            self._release_probe_lease()
        self._verify_released()
        self._closed = True
        return True

    def _shutdown_server(self):
        """先请它自己退; 请不动再强杀 —— 强杀后必须靠 `_verify_released` 兜底。

        **"请它自己退"的做法两端不同**(2026-09-20):
          · J-Link 端: 给 stdin 发 `Exit`(**首字母必须大写**, 见下面那段 ⚠)。JLinkGDBServerCL
            有一个命令行控制台, 收得到这句话。
          · CMSIS-DAP 端: **不发 stdin, 只等**。pyOCD 的 `gdbserver` 没有命令行控制台, 往它
            stdin 写什么都石沉大海; 它退出的方式是 **gdb 客户端断开后自己收摊**。而到这一步
            `close()` 已经把 gdb 收干净了, 所以这里**等就是对的**, 不是无奈之举。
            ⚠ 2026-09-20 实测把"自己收摊"的前提量清楚了, **它是有条件的**(原先那句话没条件,
              于是比事实走得远):
                · gdb 连上就退(没下断点 / 没放核)          ⇒ pyOCD 0.7s 退出、退出码 0
                · 下了断点、**但核是停着的**时候 gdb 退       ⇒ pyOCD 0.7s 退出、退出码 0
                · 下了断点、**核是跑着的**时候 gdb 退         ⇒ **12s 也不退**(同形复现两次)
              最后那种正是本仓每场会话收尾时的形状, 所以 `close()` 里对 DAP 端补了一步
              "**先叫停, 再 detach**"(见那段 ★): 把收尾那一刻变成上表第二行, 落到这条
              "等它自退"的正路上来。这一步是治因, 不是把超时调大 —— 调大只是让它多僵 8 秒。
              真等不到仍落到 `_kill_server`(强杀的是我们自己起的那个子进程), 之后
              `_verify_released` 照旧复核; 实测强杀后核心仍在跑(DHCSR=0x01000001)
        ⚠ 落到强杀之前**必须先留一行痕**(铁律③): 强杀会让"这一次收尾不正常"这件事只发生在
          这一次, 没人知道。`_kill_server` 杀的是**我们自己起的那个子进程**(`proc.kill()`),
          不是按映像名去扫 —— 后者见 `restore.py` 的 `kill_stray`, 那一条对 DAP 端**永远不许用**
          (DAP 端 server 的映像名就是 `python.exe`, 按名扫会连本仓自己的解释器一起杀)。
        """
        if self._srv is None:
            return
        proc, self._srv = self._srv, None
        if self.backend != probe_cmsis.BACKEND:
            # ⚠ **大小写是要紧的: 只有首字母大写的 `Exit` 认**(2026-09-20 实测)。原先写的是小写
            #   `exit\n`, JLinkGDBServerCL **一个字都不认、也一声不吭** —— 于是每一场 J-Link 会话
            #   都等满超时再落到 `_kill_server` 强杀。这不是偶发: 日志里 "server 等 8s 没自己退
            #   (jlink 端) → 落到强杀" 是每跑必现的一行, 而强杀 JLinkGDBServerCL 正是 CLAUDE.md
            #   点名的元凶(核心被撂在 halt, 表串口全哑)。它也是"反复连不上"那条线的一环。
            #   实测(往 server 的 stdin 写字, 计时到它自己退出为止):
            #       没连过 gdb 的裸 server:  exit\n → 10s 不退    q\n → 10s 不退    Exit\n → 1.1s 退
            #       刚被 gdb 连过又断开的:  Exit\n → 5.9s 退(同一条件偶发更久, 有一轮 8s 也没退)
            #   退出码 1 是它的常规值(没有 gdb 客户端连过), 不当作失败。
            #   ⚠ 时序：它**收得慢**, 所以等待给到 15s(`_SRV_EXIT_WAIT_S`), 不是 8s ——
            #   给长了只是多等几秒, 给短了就每场都退化回强杀, 而强杀是要防的那件事。
            try:
                if proc.stdin:
                    proc.stdin.write(b"Exit\n")
                    proc.stdin.flush()
            except Exception:
                pass
        try:
            proc.wait(timeout=_SRV_EXIT_WAIT_S)
            return
        except Exception:
            pass
        # 超时后不能再 kill：即使这是本会话自己起的 J-Link Server，强杀也可能
        # 让核心停在 halt、让 DLL/USB 事务悬空。保留进程与 lease，明确报告资源
        # 未确认释放，等它自然退出；下一场会话会得到 ProbeBusyError，而不是抢进去。
        self._srv = proc
        self._trace_emit("server 等 %ds 没自己退(%s 端) → 不强杀，资源状态未确认"
                         % (_SRV_EXIT_WAIT_S, self.backend), who="srv")

    def _kill_server(self, proc):
        """兼容旧调用点的安全桩：不强杀任何调试 server。

        ⚠ 与 CLAUDE.md「绝不要 kill/SIGTERM 强杀 JLinkGDBServerCL」的关系: 那条禁的是
        **为了结束一次正常会话而强杀**(那样核心会被撂在 halt); 这里杀的是"已经没人管、
        僵在那儿占端口"的残留。杀完**必须**由 `_verify_released()` 把"核心是否真在跑"核一遍 ——
        所以本函数只负责杀, 善后不归它。

        ⚠ 2026-09-10 修的一颗雷: 原先签名是 `_kill_server(force=False)`, 里面 `if not force or
        self._srv is None: return`。而调用点 `_shutdown_server` 已经先把 `self._srv` 清成 None 了
        ⇒ **强杀这一路永远进不去**, 残留 server 一直占着 2331(实测现象: 探针跑完再跑, 直接报
        "端口 2331 已被占用")。改成把 proc 当参数传, 不再看 `self._srv`。
        """
        # 保留这个名字是为了兼容外部自检/旧调用方；真正的收尾由
        # ``_shutdown_server`` 等待自然退出并持有 probe lease。
        return False

    def _verify_released(self):
        """复核核心真在运行。**这一步不是洁癖**: 被撂在 halt 的表表现得像"串口坏了",
        真病因(核心停着)会被后面的每一步掩盖 —— 详见 CLAUDE.md 的调试链纪律 5。

        ⚠ 把**本会话已经证明过的**那一支显式传进去(`probe=(backend, ident)`, 见 `Probe.__init__`)。
          不传的话每场收尾都要重走一遍 `probesel.pick()` —— 而 pick 是**真开会话读 CPUID**,
          既多开一次会话, 又可能与刚放下的 server 抢同一支探针。
        """
        if os.environ.get("GDBCTL_SKIP_VERIFY"):
            return
        pin = getattr(self, "backend", None)
        pinned = (pin, self.probe_ident) if pin else None
        try:
            from swdbg import probe
            with probe.Probe(probe=pinned) as pb:
                if not pb.halted():
                    return
            from swdbg import restore
            ok, d = restore.release_debug(probe=pinned)
            if not ok:
                raise GdbError("收尾复核: 核心仍 HALT, 且自动恢复失败 —— %s\n"
                               "  请手动跑: python -m swdbg.restore" % d.get("reason"))
        except GdbError:
            raise
        except Exception:
            pass          # 没装 pylink / 没插探针: 复核做不了, 不因此判会话失败

    def __enter__(self):
        return self.open()

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    # ---------------------------- MI 收发 ----------------------------
    def _send(self, cmd):
        if self._gdb is None or self._gdb.stdin is None:
            raise GdbError("会话未打开, 先 with Session(...) as g: 或 g.open()")
        self._gdb.stdin.write((cmd + "\n").encode("utf-8"))
        self._gdb.stdin.flush()
        # ★ 留痕的**发侧唯一卡口**(2026-09-15)。`_send` 是全库唯一的 MI 命令出口
        #   (另一处直接写 stdin 的是 `_shutdown_server` 写 `exit` —— 那是请 server 自然退出,
        #   不是 MI 命令, 也不该出现在 MI 收发记录里)。
        #   ⚠ 2026-09-18: 守着这条的 AST 检查(`_gate_chokepoints`)随自检层一起删了 ——
        #      **绕过留痕现在不会有东西报红**, 新增碰 gdb stdin 的地方时自己盯住这一条。
        self._trace_sent(cmd)
        self._outstanding += 1            # gdb 按序应答; 记账见 _cmd

    def _next(self, timeout):
        """取下一条 MI **记录**(已解析)。**只有队列真等空了才返回 None**。

        ⚠ 提示符 `(gdb) ` 与空行**不是记录**(parse_mi → None), 必须跳过接着等 ——
        它们既不是结果也不是超时。2026-09-10 实测踩过: gdb 启动时会先吐 `=thread-group-added`
        + 四条 `=cmd-param-changed` + 一个提示符; 那会儿 `-file-exec-and-symbols` 才刚发出去,
        于是 `_cmd` 读到的第一条"None"其实是**启动提示符**, 却被当成"命令超时"抛出,
        整个会话半途而废(而 server 已把核心停住了 → 后面串口全军覆没)。
        """
        end = time.time() + timeout
        while True:
            # ⚠ `_pushback` 必须排在 `_q` **之前** —— 里面装的是 `_drain_async` 从 `_q` 里取出、
            #   但**先于**当前 `_q` 中一切记录到达的那些记录。反过来读就把 MI 的**应答顺序**打乱了。
            if self._pushback:
                rec = self._pushback.popleft()
                if (rec.get("_class") or "").startswith("^"):
                    self._outstanding = max(0, self._outstanding - 1)
                return rec
            left = end - time.time()
            if left <= 0:
                return None
            try:
                line = self._q.get(timeout=left)
            except queue.Empty:
                return None
            rec = parse_mi(line)
            if rec is not None:
                # 记账的**唯一落点**: `^` 记录 = 某条 MI 命令的应答, 读走一条就销一笔。
                # 放这里而不是 `_cmd` 里, 是因为读记录的路不止 `_cmd` —— `go()`/`wait_break()`
                # 直接用 `_next` 读 `^running`, 原先那些路径**只借不还**, 欠账越滚越大,
                # 最后某个无辜命令等不到自己的应答而超时(实测: `-break-delete` 报 MI 超时)。
                if (rec.get("_class") or "").startswith("^"):
                    self._outstanding = max(0, self._outstanding - 1)
                return rec

    def _cmd(self, cmd, timeout=15.0):
        """同步 MI 命令: 发出去, 等对应的 `^` 结果记录(途中的 `*`/`=`/`~` 顺手处理掉)。

        ⚠ `*stopped` 不能丢 —— 它可能在命令途中到达(例: resume 后紧接着的命令)。
        一律存进 `_pending`, 由 `wait_hit` 取。

        ⚠ **可重入**(2026-09-10 修): `*stopped` 到达时这里会补喂看门狗, 而那又是一次 `_cmd` ——
        于是 `_cmd` 套 `_cmd`。gdb 的 `^` 应答**按发出顺序**回, 不认人; 内层若只等"下一个 `^`"，
        就会把**外层**的应答吃掉, 外层等到超时(实测: `go()` 后 `-break-delete` 报 MI 超时)。
        故给未应答命令记账: 每条 `_cmd` 只认"记账降到**自己发之前**那个水位以下"的那条应答。"""
        self._send(cmd)
        my_level = self._outstanding          # 本命令发出后, 欠着的应答条数
        end = time.time() + timeout
        while True:
            left = end - time.time()
            if left <= 0:
                raise GdbError("MI 命令超时: %s" % cmd)
            rec = self._next(left)
            if rec is None:
                raise GdbError("MI 命令超时: %s" % cmd)
            cls = rec.get("_class")
            if cls == "*stopped":
                self._note_stopped(rec, "cmd")     # ★ 事件流: 命令途中到的停 —— 见 `_note_stopped`
                self._pending = self._pending or rec
                # 停住 = 固件不再喂狗, 从这一刻起才真要人喂(跑着的时候表自己喂, 不用管)。
                # 这里补喂, 与 gdb 侧钩子互为兜底: 钩子没装上/时序有缝, 都还有这一下。
                if self.watchdog:
                    try:
                        self._feed_quiet()
                    except Exception:
                        pass
                continue
            if cls and cls.startswith("^"):
                # 记账已在 `_next` 里销掉了(见那里的注释); 这里只判"够不够到我的水位"。
                if self._outstanding < my_level:
                    return rec                      # 这才是本命令的应答(^error 由调用方判)
                continue                            # 内层命令的应答, 不是给我的
            # 其它(`=`/`~`/`&`/`+`): 丢掉

    # ---------------------------- 看门狗 ----------------------------
    def _install_hook_stop(self):
        """gdb 侧 hook-stop: **每次停下立刻喂狗**, 不等 python。

        为什么不能只靠 python 喂: 收到 `*stopped` 后再发写命令, 中间隔着一次 MI 往返;
        而调用方在 `resume()` 与下一次 `wait_hit()` 之间还要读串口 —— 断点若恰好落在那段
        时间里, 喂狗就被推迟到下一次 wait_hit。gdb 侧 hook 没这个缝。

        ⚠ **别用 `define ... end`**(2026-09-10 连踩三次):
          ①拆成四条 `-interpreter-exec console` 分开发 → gdb 收下 `define hook-stop` 后就**等后续行**,
            一个结果都不回, 第一条就 MI 超时;
          ②塞进一条 MI 字符串用 `\\n` 分隔 → 换行没变成真行, `hook-stop` 被当命令执行 → `^error`;
          ③改成"写临时 .gdb 文件 + `source`" → **本机有 DLP 透明加密**(见下), python 写出的文件
            在盘上是密文(gdb 读到 `%TSD-Header-###%` 开头的乱码), 而 python 自己读得到明文 ——
            于是"文件内容明明对, gdb 就是报 `Undefined command: \"\"`"。**同机同内容不同进程读到的字节不同**,
            这种坑查起来极费时, 别再用"写文件给 gdb 读"这条路。
        正解: `define` 要的是**多行**, 而 MI 只方便送**单行** —— 那就换成 gdb 内嵌 Python 的**单行**事件钩子。
        gdb 侧钩子仍在(不与 python 抢时序), 但**装不上只告警不中止**: python 侧 `_cmd` 收到 `*stopped`
        也会立刻补喂一次, 有它兜底。"""
        addr, magic = self.watchdog
        code = ("import gdb; gdb.events.stop.connect(lambda e: "
                "gdb.execute('set {unsigned int}0x%08X = 0x%08X', to_string=True))" % (addr, magic))
        try:
            r = self._cmd('-interpreter-exec console "python %s"' % code.replace('"', '\\"'))
        except GdbError as exc:
            r = None
            self._warn("装 gdb 侧喂狗钩子超时(%s), 改由 python 侧在每次 *stopped 补喂" % exc,
                       what="gdb 侧喂狗钩子没装上(超时)")
        if not r or r.get("_class") != "^done":
            self._warn("gdb 侧喂狗钩子没装上(%s), 改由 python 侧在每次 *stopped 补喂"
                       % (r or "无应答"), what="gdb 侧喂狗钩子没装上")
        else:
            self._hook_installed = True

    def _feed_quiet(self):
        """喂一次狗, **不打印**。`_cmd` 内部用(那里不能有输出, 也不该递归太久)。"""
        addr, magic = self.watchdog
        self._cmd('-interpreter-exec console "set {unsigned int}0x%08X = 0x%08X"' % (addr, magic),
                  timeout=5.0)
        self._dog_feeds += 1

    def feed_watchdog(self):
        """python 侧补喂(在 `*stopped` 处理中自动调一次)。返回是否真的喂了。"""
        if not self.watchdog:
            return False
        self._feed_quiet()
        return True

    # ---------------------------- 断点 ----------------------------
    def break_at(self, srcfile, line):
        """按**源码行**下断点 → bp 号。**断言 gdb 真的落在了地址上**(不是 pending 悬空)。

        行 → 地址**由地图的行表算**(`gdbinit.line_addr`), 拿到地址后再 `-break-insert "*0x…"`,
        不走 gdb 的 `file:line` 那条路 —— 那条路碰到没编译出指令的行会**顺手滑到下一行**,
        断点就落在一条没打算测的语句上, 而日志里只有文件名与行号, 看不出来。所以本方法
        **要求会话上已建地图**(脚本开头 `gdbinit.build(ctx.g)`); 没建就抛, 不前退到 gdb 行表。

        ⚠ Cortex-M0 只有 4 个硬件槽, 第 5 个会报错 —— 本模块**把错误抛出来, 不静默降级成
        软件断点**(软件断点要写 Flash, 那是另一回事)。"""
        if not self.watchdog:
            raise GdbError("没配 watchdog 就不许下断点 —— 停住超过约 8s 表会被 IWDT 复位。\n"
                           "  Session(out=..., watchdog=(IWDT_SERV地址, 0x12345A5A))")
        if len(self._bps) >= MAX_HW_BREAK:
            raise GdbError("已有 %d 个硬件断点, Cortex-M0 只有 %d 个槽。先 clear_breaks()。"
                           % (len(self._bps), MAX_HW_BREAK))
        self.ensure_stopped()          # ⚠ 下断点只能趁停住 —— 上一次的收尾可能是 resume(见 ensure_stopped)
        base, ln = os.path.basename(str(srcfile)), int(line)
        spec = "%s:%d" % (base, ln)
        want = gdbinit.line_addr(self, base, ln)
        r = self._cmd('-break-insert "*0x%X"' % want)
        if not r or r.get("_class") != "^done":
            raise GdbError("下断点失败 %s: %s" % (spec, (r or {}).get("msg", "无应答")))
        bk = r.get("bkpt") or {}
        addr = bk.get("addr")
        if not addr or int(str(addr), 16) == 0:
            raise GdbError("断点 %s 没落到地址上(pending?) —— 文件名/行号对不上 DWARF?" % spec)
        if int(str(addr), 16) != want:
            raise GdbError("断点 %s 要下在 0x%X(行表给的), gdb 却落在 0x%s —— 行表与 gdb 对不上了, "
                           "别照它跑" % (spec, want, str(addr).replace("0x", "")))
        no = str(bk.get("number"))
        self._bps[no] = (base, ln, "0x%X" % want)
        # ★ 事件流: "某个断点落到了哪个地址"。放在 `quiet` 判据**之外** —— `quiet` 是"少打点给人看"
        #   的取舍(见模块头), 与"这件事有没有发生"无关; 事件流少了它, "断点到底下没下上"又回到
        #   只能靠人读日志猜。**`spec` 是 `basename:line`**, 落盘前再核一次地址。
        events.emit("bp.set", no=no, spec=spec, addr="%X" % want)
        if not self.quiet:
            print("   %s" % loglabel.debug_line(loglabel.DEBUG_GDB, "断点 %s → %s @0x%X" % (no, spec, want)))
        return no

    def break_at_func(self, name):
        """按**函数入口**下断点 → bp 号。判"断点单元还能不能比中"用这个, 不用源码行。

        ⚠ 为什么不能拿源码行当判据: **行号跑不跑得到, 看的是表此刻跑到哪**。
        2026-09-20 实测(同一颗表、同一版 .out、一次会话内): `TaskRate.c:85`(在 Run_TaskRate 体内)
        3.42s 一分不中, 而同一函数的**入口**每次都中。拿行号验 FPB ⇒ 表一换状态就假报"断点单元不通"。
        函数入口没有这个问题: 调用关系在, 入口就在。

        `name` 是**符号名**(如 `Run_TaskRate`), 不是文件名, 也不是地址 —— 别在这里手抄地址,
        地址要由 gdb 从 .out 推出来并写进事件流。
        """
        if not self.watchdog:
            raise GdbError("没配 watchdog 就不许下断点 —— 停住超过约 8s 表会被 IWDT 复位。\n"
                           "  Session(out=..., watchdog=(IWDT_SERV地址, 0x12345A5A))")
        if len(self._bps) >= MAX_HW_BREAK:
            raise GdbError("已有 %d 个硬件断点, Cortex-M0 只有 %d 个槽。先 clear_breaks()。"
                           % (len(self._bps), MAX_HW_BREAK))
        self.ensure_stopped()          # ⚠ 下断点只能趁停住(见 ensure_stopped)
        spec = str(name).strip()
        r = self._cmd('-break-insert "%s"' % spec)
        if not r or r.get("_class") != "^done":
            raise GdbError("下断点失败 %s: %s" % (spec, (r or {}).get("msg", "无应答")))
        bk = r.get("bkpt") or {}
        addr = bk.get("addr")
        if not addr or int(str(addr), 16) == 0:
            raise GdbError("断点 %s 没落到地址上(pending?) —— .out 里没有这个符号?" % spec)
        no = str(bk.get("number"))
        self._bps[no] = (spec, 0, str(addr))
        events.emit("bp.set", no=no, spec=spec, addr=str(addr).replace("0x", ""))
        if not self.quiet:
            print("   %s" % loglabel.debug_line(loglabel.DEBUG_GDB, "断点 %s → %s @0x%s" % (no, spec, str(addr).replace("0x", ""))))
        return no

    def clear_breaks(self, timeout=5.0):
        """删掉所有断点。**⚠ 只在核心停住时能删。**

        2026-09-10 实测(这条是本模块最反直觉的一条): **核心一旦跑起来, 这条 gdb 就一条 MI 命令
        都不回** —— 不是只不答 `-break-delete`, 连纯查询 `-data-list-register-names`、乃至唯一
        能把核叫停的 `-exec-interrupt` 都石沉大海(实测各等 10s 无任何记录; 4-6 全量跑时表现为
        `-break-delete` 报 MI 超时, 而同一个断点在核停住时删是秒回 `^done`)。
        所以"跑着的时候想把断点撤了"做不到 —— 撤断点必须**趁停住那一刻**。
        停住态撤断点的正路: `wait_only(..., drop=True)` / `with_trigger(..., drop=True)`,
        库会在读完后**放行之前**顺手删掉。"""
        if not self._stopped:
            raise GdbError(
                "核心在跑, 这时发 MI 命令(含 -break-delete)不会有任何应答, 别白等。\n"
                "  要撤断点请趁停住时: wait_only(..., drop=True) / with_trigger(..., drop=True)")
        self._cmd("-break-delete", timeout=timeout)
        self._bps.clear()

    def _drop_bp(self, bpno):
        """撤掉**一个**断点(核须停着)。腾硬件槽用 —— M0 只有 4 个, 用完即撤是常规动作。"""
        no = str(bpno)
        if no not in self._bps:
            return
        r = self._cmd("-break-delete %s" % no, timeout=5.0)
        if not r or r.get("_class") != "^done":
            raise GdbError("撤断点 %s 失败: %s" % (no, (r or {}).get("msg", "无应答")))
        self._bps.pop(no, None)

    def _disarm(self, bpno):
        """撤掉**这一次临时挂上**的断点(核未必停着 ⇒ 先叫停再撤, 撤完放行)。

        ⚠ 为什么非有不可(2026-09-10 实测, 2 分钟就坐实): **断点留在槽里、核心跑着, 它自己会命中。**
        实测 `TaskRate.c:85`(Run_TaskRate 在主循环 main.c:44 里每轮都调, 是**高频行**)挂上之后
        **只 `ensure_running()` 什么都不干**, 第 5 秒 `*stopped` 就到了 —— 因为没人轮询队列 ⇒
        没人放行 ⇒ **核心永远停在那儿**, 此后每一条 645/698 帧都收不到回, 现象与"表死机/串口坏了"
        一模一样(3-1 首跑就是这么整轮哑掉的: 断[A] 之后 8 个费率参数读全 RX(0), 基线读出全 None)。
        而"跑着的时候想把断点撤了"做不到(`clear_breaks` 的 ⚠: 核一跑起来 MI 命令石沉大海)——
        **唯一能撤的时机就是停住那一刻**。
        故本函数把"叫停 → 撤 → 放行"收成一处: 断点的存活期**不长过它自己那一次**。
        """
        if bpno is None or str(bpno) not in self._bps:
            return False
        self.ensure_stopped()          # 撤断点只能在停住态(见 clear_breaks 的 ⚠)
        self._drop_bp(bpno)
        self.ensure_running()          # 收尾态必须是"跑着" —— 否则后面串口全哑
        return True

    def breakpoints(self):
        return dict(self._bps)

    # ---------------------------- 跑 / 停 ----------------------------
    def go(self, settle=True):
        """**非阻塞** continue: 发出去, 等到 `^running` + 静默一小段(SETTLE_AFTER_RUN)才返回。

        返回 True = 已放行在跑; False = 还没等走就停了(断点就在眼前), 命中已存进 pending,
        接着调 `wait_hit()` 立刻拿到。

        `settle=False` = **跳过那 0.4s 静默**(那条静默只对"放行后马上发串口帧"有意义)。
        ⚠ 什么时候必须跳(2026-09-11 实踩): **要拿"放行→停住"的秒数当判据时**。那 0.4s 会原样
          加进每一次测量里 ⇒ 任何落在静默窗口里的停都被记成 ≥0.4s, "微秒级的那一次执行"与
          "~1s 后来的自然那次"再也分不开(2-1 的 ③ 段正是拿这个秒数判"这一停归谁", 于是 0.42/0.50
          这些读数全是静默本身, 阈值 0.5s 一条都没拦住)。只想"放行后还能发串口帧"的地方**照旧用
          缺省**(要 settle)。

        ⚠ **为什么 `^running` 之后还要等一下**(2026-09-10 实测, 这条差点被记成"串口偶发丢帧"):
          `^running` 只说明 **gdb 把 continue 发出去了**, 不说明目标**已经在执行** —— J-Link 真要
          把核放起来还有一小段窗口。在这个窗口里发串口帧, **表收不到, RX(0)**。
          实测(3-2 全套跑): `wait_only(断[A])` 放行后**紧接着**的 `read_clock` 必 RX(0),
          隔 0.4s 再来一次就 OK; 同一轮里第二次读(几秒后)也 OK ⇒ 只丢**放行后的第一帧**。
          证据落在 `log/3_2_zone_slot_switch_20260910_145214.log`: 时区支首帧 RX(0) 而时段支全通。
          ⚠ 这个坑对**两种观测的脚本是致命的**: 串口观测靠串口驱动, 而断点观测每命中一次就 resume 一次 ——
          "放行后马上发帧"正是最常见的写法(4-6 的 `wait_only(断[A])` → `write_billday` 就是)。
          而 4-6 恰好在中间又 `break_at` 了一次(那次 ensure_stopped 把核停住重来), 才没撞上。
          修在这一层而非各脚本里: "resume 之后表就应答"应当**由本函数保证**, 而不是让每个调用点
          各自记得 sleep —— 那就成了靠心照不宣的调用顺序。
        """
        self._pending = None
        self._stopped = False
        self._send("-exec-continue")
        end = time.time() + 5.0
        while True:
            left = end - time.time()
            if left <= 0:
                raise GdbError("continue 后 5s 没收到 ^running")
            rec = self._next(left)
            if rec is None:
                raise GdbError("continue 后 5s 没收到 ^running")
            cls = rec.get("_class")
            if cls == "*stopped":
                self._note_stopped(rec, "go")
                self._pending = rec
                return False
            if cls == "^running":
                if settle:
                    time.sleep(SETTLE_AFTER_RUN)   # 见上 ⚠: 不等这一下, 放行后的第一帧会丢
                return True
            if cls == "^error":
                # ⚠ **"它已经在跑了"不算失败**(2026-09-11 实踩)。gdb 分不清"我以为它停着"与
                #   "库里 `_stopped` 陈旧", 对着一个真在跑的核心发 `-exec-continue` 就回这一句 ——
                #   而 `go()` 要的**结果**恰恰就是"在跑", 已经成立。早先这里一律抛, 于是
                #   `ensure_running()` 明明已经把核放行了, 却在最后一步抛 `GdbError` 把这一次带崩
                #   (单步之后就是这么死的: 单步在飞, 核在跑,
                #   而库里还记着"停着")。真正的失败(如没有目标)照旧抛。
                if "while the selected thread is running" in str(rec.get("msg") or ""):
                    return True
                raise GdbError("continue 失败: %s" % rec.get("msg"))

    def _drain_async(self):
        """把**已经到达**的异步记录读完, 据此更新 `_stopped`。不阻塞。

        为什么需要它(mi-async 的必然后果): 开了 mi-async 之后, 断点在**没人轮询队列**的时候
        命中, gdb 照样会吐 `*stopped` —— 它静静躺在 `_q` 里, 谁都没读, 于是 `_stopped`
        **还停在 False**。这就是"陈旧状态": 实际停着, 库以为在跑。

        ⚠ **只吃 `*stopped`, 其余一条不动地退回** `_pushback`(按原序, `_next` 先读它)。早先那版
        把 `^` 记录也一并吃掉 —— 那是**某条未答命令的应答**, 吃掉它那条命令就永远等不到自己的
        应答(离线自检当场抓到: `ensure_running` 把队列里的 `^running` 吃了, 随后 `go()` 硬等 5s
        报"没收到 ^running")。
        """
        while True:
            try:
                line = self._q.get_nowait()
            except queue.Empty:
                return
            rec = parse_mi(line)
            if rec is None:
                continue
            if (rec.get("_class") or "") == "*stopped":
                self._note_stopped(rec, "drain_async")
                self._pending = self._pending or rec
            else:
                self._pushback.append(rec)      # 退回, 保持原序(见 _next 的 ⚠)

    def _read_u32(self, addr):
        """读一个 32 位寄存器(经 gdb)。读不到返回 None。"""
        r = self._cmd('-data-evaluate-expression "*(volatile unsigned int*)0x%08X"' % addr, timeout=6.0)
        if r and r.get("_class") == "^done":
            try:
                return int(str(r.get("value")).strip(), 0)
            except Exception:
                return None
        return None

    def _write_u32(self, addr, val):
        """写一个 32 位寄存器(经 gdb)。与喂狗走同一条路(`-interpreter-exec console "set …"`)。"""
        self._cmd('-interpreter-exec console "set {unsigned int}0x%08X = 0x%08X"' % (addr, val),
                  timeout=5.0)

    def _clear_stale_fpb(self):
        """下**第一个**断点之前, 把目标断点单元(FPB)里**残留的**槽清零。`open()` 的收尾第二步。

        ⚠ 2026-09-11 实探 —— 这就是"会话一开、
        串口全哑"的真凶, 前面两版修法(在 open 里 `ensure_running()`)都修错了地方:
          会话开着、本会话**一个断点都没下**(横幅 `断点=0`), 但核一 `continue` 就立刻
          `*stopped reason=signal-received (SIGTRAP)`, 停在 `TaskTime.c:1087` @0x28bb6。
          读寄存器当场坐实: `DHCSR=0x00030003 停着 | FP_CTRL=0x00000041 | FP_COMP0=0x80028BB5`
          (`0x28bb5` 就是那个断点地址, 其余三槽为 0)。
          而 `:1087`(Set_MeterTime)受 SPI 时间对象驱动、**约 1 Hz 执行一次** ⇒ 核每次被放行后
          **不到 1 秒就又撞上它停住**, 此后每一条 645/698 帧都 RX(0) —— 表象与"表死机/串口坏了"
          一模一样(2-1 首跑与第三跑都是整轮哑掉而记"未定论")。

        这正是 `_disarm` docstring 记过的那类事故("断点留在槽里、核心跑着, 它自己会命中"), 只是
        这一次残留**跨会话活了下来**(`-target-detach` 没全清), 于是它不属于任何一场会话 ——
        本会话既不认识它、也就撤不掉它(`-break-delete` 只认自己登记的 bp 号)。**撤不掉就清场**。

        所以不去查"上一场是谁留下的"(查不清也没用), 只钉一条不变式:
            **一场会话在下第一个断点之前, FPB 必须是空的。**
        新会话此刻一个断点都没有, FPB 里凡是有内容的, **按定义**都是残留, 清掉不可能误伤。
        (清法与 `restore.py` 一致: FP_CTRL 写 0x2 = KEY 开/ENABLE 关, 再把各 FP_COMP 写 0。)

        返回清掉的项数(0 = 本来就干净); 清不动只告警不中止 —— 别为一次兜底失败把整场会话废掉。
        """
        if self._gdb is None:
            return None
        try:
            if not self._stopped:
                self.ensure_stopped()          # 读写 FPB 都在停住态做
            stale = [(i, v) for i, a in enumerate(FP_COMP_ADDRS)
                     for v in (self._read_u32(a),) if v]
            if not stale:
                return 0
            self._write_u32(FP_CTRL_ADDR, 0x00000002)
            for a in FP_COMP_ADDRS:
                self._write_u32(a, 0x00000000)
            self._warn("目标断点单元里有 %d 个**残留**硬件断点槽(%s) —— 已清零。不清的话核一跑到"
                       "那个地址就被 SIGTRAP 停住, 之后串口全无应答(表象 = 表死机)。"
                       % (len(stale), ", ".join("COMP%d=0x%08X" % t for t in stale)))
            return len(stale)
        except Exception as exc:
            # **降级**(不是提醒): 残留没清掉 ⇒ 核一跑到那个地址就被停住 ⇒ 串口全无应答,
            # 表象与"表坏了"一模一样。这一轮取到的证据**不可信**, 不许报「通过」。
            self._warn("清残留 FPB 失败(%r) —— 若随后串口全无应答, 跑 `python -m swdbg.restore`" % exc,
                       what="残留 FPB 没清掉")
            return None

    def _clear_stale_dwt(self):
        """把数据观察点单元(DWT)里**残留的**比较器清零。`open()` 的收尾第三步。

        ⚠ 2026-09-17 实踩(5-2 重跑, 整场串口全 RX(0)): `_clear_stale_fpb` 只清 FPB,
        **清不到 DWT** —— 而"在某个变量被写时停住"这件事在 J-Link 上是**另一套硬件**。
        停核现场:
            `[srv] ...Target halted (Unknown data BP / WP, PC = 0x000159FA)`
            `*stopped reason=signal-received(SIGTRAP) func=RevCopy_Data`
            `Common.c:310`(那句每周期把 `g_PowP[0]` 从 SPI 缓冲拷回来)
        寄存器当场坐实: `DWT_COMP0=0x20007B20`(正是 `g_PowP[0]` 的地址)、
        `DWT_FUNCTION0=0x01000006`(COMP0 的使能位已关、但 **MATCHED 位还立着**)、
        `DHCSR=0x01000001`(**核心在跑**)、`FP_CTRL=0x41` 而四个 `FP_COMP` 全 0。
        ⇒ 残留来自更早那次"抓 `g_PowP[0]` 的写者"实验(`-break-watch`)。核心在跑、
        本会话一个断点都没有, 于是 `restore.py` 那句"核心本就在跑 ⇒ 无需动任何东西"
        **什么也没清**, 而 J-Link 一连上就认出这个"它不认识的"数据观察点, 立刻把核停住。
        (那个地址上的写是**每周期**都发生的 ⇒ 一放行就再停住, 与 FPB 残留同一种病。)

        不变式与 FPB 那条一样: **一场会话在下第一个断点之前, 调试单元必须是空的。**
        新会话此刻一个断点/观察点都没有, 那两处凡是有内容的, 按定义都是残留, 清掉不可能误伤。
        `FUNCTION` 一起写 0 而不只写 `COMP` —— 使能位与 MATCHED 位都在它里面。

        返回清掉的项数(0 = 本来就干净); 清不动只告警不中止(同 `_clear_stale_fpb` 的分寸)。
        """
        if self._gdb is None:
            return None
        try:
            if not self._stopped:
                self.ensure_stopped()          # 读写 DWT 都在停住态做
            # ⚠ 别把这段写成一条列表推导 —— 里面"地址"与"读回的值"是两组名字, 推导式里
            #   内外层刚好重名过一次, 于是**返回值**被写成了寄存器**地址**, 计数与清零都对、
            #   只有那句告警在拿地址冒充内容(2026-09-17 自检输出当场看出来的)。分开写。
            stale = []
            for i, (ca, fa) in enumerate(zip(DWT_COMP_ADDRS, DWT_FUNC_ADDRS)):
                cv, fv = self._read_u32(ca), self._read_u32(fa)
                if cv or fv:
                    stale.append((i, cv, fv))
            if not stale:
                return 0
            for c, f in zip(DWT_COMP_ADDRS, DWT_FUNC_ADDRS):
                self._write_u32(c, 0x00000000)
                self._write_u32(f, 0x00000000)
            self._warn("数据观察点单元(DWT)里有 %d 个**残留**比较器(%s) —— 已清零。不清的话 J-Link "
                       "一连上就认它是『它不认识的数据观察点』并把核停住, 之后串口全无应答"
                       "(表象 = 表死机)。"
                       % (len(stale), ", ".join("COMP%d=0x%08X/FUNC%d=0x%08X" % (i, cv, i, fv)
                                                for i, cv, fv in stale)))
            return len(stale)
        except Exception as exc:
            # **降级**(不是提醒): 残留没清掉 ⇒ 核一被放行就再次撞上它 ⇒ 串口全无应答,
            # 表象与"表坏了"一模一样。这一轮取到的证据**不可信**, 不许报「通过」。
            self._warn("清残留 DWT 失败(%r) —— 若随后串口全无应答, 跑 `python -m swdbg.restore`" % exc,
                       what="残留 DWT 没清掉")
            return None

    def _settle_after_connect(self, timeout=3.0):
        """连上 server 后**等它那条 `*stopped` 落定**再返回。`open()` 的收尾第一步。

        ⚠ 2026-09-11 实测到的时序(这就是首版修法静默失效的原因):
          `-target-select` 的应答是 `^connected`, 而"核心被 server 停住"这件事是以
          `*stopped` **异步通知**的形式**稍后**才到的 —— 两者不是一条记录。
          若在它到达之前就调 `ensure_running()`, 那边 `_drain_async()` **什么也吃不到**、
          `_stopped` 仍是 False ⇒ 判定"核在跑", **一个 continue 都不发**, 核心留在 halt 上。
          表象: `open()` 明明返回了会话(横幅照打、`open_or_none` 不报错), 但后面**每条串口帧
          RX(0)**。同一份代码, 隔着几秒从外面调 `ensure_running()` 就活 —— 差别只在这条记录到没到。

        ⚠ `ensure_running` 的 docstring 里原先把这类情形写成"实际到不了"的残留窗口, **那句是错的**:
        到得了, 且`open()` 正好就撞在窗口里(实测: 同一次会话 open 内调不灵、隔几秒外调灵)。
        所以不靠"算准了不会撞", 靠**等它一下**。

        返回 True = 等到了停产记录(核确实停着, 该放行); False = timeout 内没等到 ——
        那说明 gdb 本来就没把它当停着(核可能真在跑), 交给 `ensure_running()` 照常判。
        不长等: server 一起来就停核, 这条记录通常几十毫秒内到。
        """
        end = time.time() + timeout
        while not self._stopped and time.time() < end:
            self._drain_async()
            if self._stopped:
                break
            time.sleep(0.05)
        return self._stopped

    def ensure_running(self, timeout=5.0):
        """把核心弄到**确凿的运行态**再返回 —— `with_trigger`/`wait_only` 的入口前置。

        ⚠ 2026-09-10 实踩(断[C] 第二次炸掉的那颗): 原先 `with_trigger` 进来张口就是
        `self.go()`。第一次跑完后 `with_trigger` **以 `resume()` 收尾**(核心在跑), 于是第二次
        再发 `-exec-continue`, gdb 回 `^error,msg="Cannot execute this command while the
        selected thread is running."` ⇒ **一个会话里连做两次必崩**。这条与"陈旧的 `*stopped`"
        是同一个病根的两副面孔: **不能凭 `_stopped` 猜核心在不在跑**。

        所以这里不猜: 先 `_drain_async()` 把已到的 `*stopped` 吃进 `_stopped`; 若仍判在跑, 就
        认它真在跑(不发 continue); 若判停着, 放行; 放行**当口就停**(= 队列里还有陈旧停产记录,
        `go()` 返回 False)则再吃一轮 —— 直到真看见 `^running`。

        **陈旧 `*stopped` 为什么非吃掉不可**: `wait_break` 分不出"我这一次打中的"和"上一次
        遗留的", 前置没清干净就会把陈旧的停产记录报成本次命中 = **假通过**。

        残留窗口(**2026-09-11 改正: 这窗口真到得了, 早先这里写着"实际到不了"是错的**):
        若 `*stopped` 尚在管道里没进队列, 这里会误判"在跑"而**不发 continue**, 核心就留在 halt 上。
        `open()` 正好撞在窗口里(server 一起来就停核, 那条通知晚于 `^connected`)—— 首版修法
        (在 open 里直接调本函数)**静默失效**就是这么来的: 会话开得好好的, 后面串口全哑。
        所以 `open()` 必须先 `_settle_after_connect()` 等那条记录落定, 再进这里。
        凡是"刚连上/刚 resume"的当口, 都别指望 `_stopped` 已经跟上了。
        """
        end = time.time() + timeout
        while True:
            if time.time() >= end:
                raise GdbError("无法把核心稳定在运行态(%.0fs)" % timeout)
            self._drain_async()
            if not self._stopped:
                return True                 # 确凿在跑: 不要发 continue(gdb 会报错)
            self._pending = None            # 停着: 放行。丢弃的 pending 是陈旧停产记录, 不是命中
            if self.go():
                return True                 # 看见 ^running, 真在跑了
            # go() 返回 False = 放行当口就地又停 → 队列里还有陈旧记录, 再吃一轮

    def ensure_stopped(self, timeout=10.0):
        """把核心叫停并确认停住 —— `ensure_running` 的对称面。凡是"只在停住态成立"的操作
        (`-break-insert` / `-break-delete` / 读局部量 / 注入)之前都得先过这一道。

        ⚠ 2026-09-10 实踩(4-6 全量跑): `wait_only(断[A])` 是**以 `resume()` 收尾**的(它必须放行,
        否则后面串口全哑), 紧接着脚本 `g.break_at(断[C])` —— 对着一个正在跑的核心发
        `-break-insert`, gdb 回 `^error,msg="Cannot execute this command while the target is
        running."`, 整轮死在第二次之前。**"上一步的收尾态"与"下一步的入口态"必须各自显式声明**,
        不能靠调用顺序心照不宣。
        """
        if self._stopped:
            return True
        self._cmd("-exec-interrupt", timeout=timeout)   # 叫停; 期望 ^done
        # `*stopped` 多半在 `^done` **之后**才作为异步通知到达 → 再等它一下(_cmd 只等自己的应答)
        end = time.time() + timeout
        while not self._stopped:
            left = end - time.time()
            if left <= 0:
                raise GdbError("interrupt 后 %.0fs 仍没停下来" % timeout)
            rec = self._next(left)
            if rec is None:
                raise GdbError("interrupt 后 %.0fs 仍没停下来" % timeout)
            if (rec.get("_class") or "") == "*stopped":
                self._note_stopped(rec, "interrupt")
                self._pending = self._pending or rec
        return True

    def wait_hit(self, timeout=10.0):
        """等一次 `*stopped` → Hit; 超时(没命中) → None。

        超时**不抛异常**: 没命中是测试结论的一部分(比如"这次不该触发"), 由调用方判。"""
        if self._pending is not None:
            rec, self._pending = self._pending, None
            return self._mk_hit(rec)
        end = time.time() + timeout
        while True:
            left = end - time.time()
            if left <= 0:
                return None
            rec = self._next(left)
            if rec is None:
                return None
            cls = rec.get("_class")
            if cls == "*stopped":
                self._note_stopped(rec, "wait_hit")
                return self._mk_hit(rec)
            if cls == "^error":
                raise GdbError("等断点途中 MI 报错: %s" % rec.get("msg"))

    def _ev_halt(self, rec, via):
        """核心**停住**了 → 记一条 `halt` 事件。**2026-09-17 加; 也是本事件层唯一新造的一条。**

        为什么非新造不可: 172 份历史日志里"调试器**几点**停的 / 停在**哪**"一个字都查不到。
        根因不是"哪一处忘了 `print`", 是**没有任何一处知道"停住了"这件事值得记** ——
        `Hit` 只在"等断点"那条路上构造, 而最常见的停住根本不是等来的
        (残留观察点把核撂停 / `-exec-interrupt` / 单步)。于是它**从来没被记过**。

        钉在**两处**, 合起来覆盖每一条 `*stopped`:
          · `_cmd`   —— 命令执行途中到的(存进 `_pending`)。**这一处不能省**: 5-2 那次就是核被
                        残留数据观察点撂停、`*stopped` 进了 `_pending`, 而脚本之后再没等过断点 ⇒
                        **没人来认领它**。只在 `_mk_hit` 记的话, 恰恰是出事的那一次没有记录。
          · `_mk_hit` —— 等断点那条路(`wait_hit` 与 `wait_break` 的全部四条分支都走它,
                        见 `_mk_hit` 的调用点), 一并覆盖 `_pending` 的消费。

        两条路会不会把同一停记两遍? **不会**: 进两条路的 `*stopped` 是**同一个 dict**
        (`_pending` 存的就是那个 rec), 本函数在最前面盖一个 `_halt_seen` 记号, 于是只有
        "第一次看见"算数。⚠ 2026-09-18: 原先钉着这件事的那条断言随自检层一起删了 ——
        改这里时要自己确认"同一次 `*stopped` 只记一次 halt 事件"仍然成立。

        ⚠ **没有去用 `Hit.dog_fed` 那个墙钟**(`_mk_hit` 里算了却只在 `repr` 里露一脸的那个):
          它只到**秒**, 而且**只在喂了狗时**才有值。事件层的 `t`/`clock` 是**毫秒、且每条都有**
          (见 common/events 模块头 ③)。拿它只会更差、更全不了, 所以这里不碰它。
        """
        if not events.enabled() or rec.get("_halt_seen"):
            return
        rec["_halt_seen"] = True             # ⚠ 盖在 rec 上, 不是 self 上: `_pending` 存的正是它,
        f = rec.get("frame") or {}           #   盖在这儿才能让"两条路记同一次"认得出是同一停
        _file = f.get("file")
        events.emit("halt", via=via, reason=rec.get("reason"), bkptno=rec.get("bkptno"),
                 func=f.get("func"), addr=f.get("addr"), line=f.get("line"),
                 file=os.path.basename(str(_file)) if _file else None)

    def _note_stopped(self, rec, via):
        """`*stopped` 到手 → **`self._stopped` 只许从这里被置真**。

        为什么收成一个方法(而不是像原先那样在八处直接写 `self._stopped = True`):
        本仓的做法是"钉在卡口上", 可这一件事**没有物理卡口** —— 记录是在 `_q` 上, 而 `_q` 有
        **两个**消费者(`_next` 与 `_drain_async`), `*stopped` 落到八处不同的等待循环里
        (`_cmd` / `go` / `_drain_async` / `interrupt` / `wait_hit` / `wait_break` 两处 / `call_func`)。
        散着补 `halt` 事件就得靠"记得每一处都补", 而**漏一处不会报错** —— 恰恰最该看见的那几种
        停住(异常停、被观察点撂停)会静默没有记录, 也就是这一层当初要解决的那个问题原样再来一次。
        所以: 把"置真"收进这里, 谁来守"没人绕过它" —— 自检里的 `_gate_stopped_notes`(AST 判据,
        谁再直接写 `self._stopped = True` 当场红, 与 `_gate_chokepoints` 同一路数)。

        `via` = 哪条路看见它停的, 逐字进事件。**不是**装饰: 同一个 `*stopped` 只记**第一次**看见
        (`_ev_halt` 的 `_halt_seen` 记号), 于是 `via` 恰好回答了"我们当时在干什么"。
        """
        self._stopped = True
        self._ev_halt(rec, via=via)

    def _mk_hit(self, rec):
        dog = None
        try:
            if self.feed_watchdog():         # python 侧补喂(gdb 侧 hook-stop 已喂过一次)
                dog = time.strftime("%H:%M:%S")
        except Exception:
            dog = None
        return Hit(rec, dog_fed=dog, raw=rec)

    def wait_break(self, bpno, timeout=10.0, vars=(), drop=False):
        """等**指定的那个**断点命中 → (Hit, {var: 值}); 超时 → (None, {})。

        与 `wait_hit` 的分工: 一个会话里常同时挂着几个断点(4-6 就是 汇合点/判定/入口 三个), 而
        `wait_hit` 只会返回"下一个停在哪"。调用方真正关心的是"**我这一次打中了哪个**"。
        途中命中别的断点 → 记进 `self.other_hits`, **放行继续等**(不当作失败)。

        `vars` 非空时在停住那一刻把值读出来一起返回(现查现读 —— 放行之后读到的就不是那一刻的值了)。
        `drop=True` 时**趁停住把断点撤了再放行** —— 这是撤断点的唯一时机(见 `clear_breaks` 的 ⚠)。
        """
        want = str(bpno)
        end = time.time() + timeout
        while True:
            if self._pending is not None:
                rec, self._pending = self._pending, None
                self._note_stopped(rec, "wait_break")
                hit = self._mk_hit(rec)
            else:
                left = end - time.time()
                if left <= 0:
                    return None, {}
                rec = self._next(left)
                if rec is None:
                    return None, {}
                cls = rec.get("_class")
                if cls == "^error":
                    raise GdbError("等断点 %s 途中 MI 报错: %s" % (want, rec.get("msg")))
                if cls != "*stopped":
                    continue
                self._note_stopped(rec, "wait_break")
                hit = self._mk_hit(rec)
            if str(hit.bkptno) == want:
                vals = self.read_vars(vars) if vars else {}
                if drop:
                    self._drop_bp(hit.bkptno)      # 核正停着 —— 撤断点的唯一时机
                return hit, vals
            self.other_hits.append(hit)
            if not self.quiet:
                print("   %s" % loglabel.debug_line(loglabel.DEBUG_GDB, "(掠过非目标断点 %s %s)" % (hit.bkptno, hit.where())))
            self.go()                     # 不是等的那个: 放行继续等

    def wait_only(self, bpno, timeout=10.0, vars=(), drop=False):
        """**只等、不触发**: 等某个自然停到的断点(如"分钟步进汇合点")→ 与 `with_trigger` 同形状的 dict。

        值与 `with_trigger` 的差别只有"没有触发动作"这一条。**读完自动放行** —— 绝不把核心
        留在停住态出去(那会让后面每一步串口都像"坏了")。`drop=True` = 趁停住把这断点撤了再放行。

        ⚠ **队列里已经躺着"这一次要等的那个"停产记录时, 不许先 `ensure_running()`** —— 它把
          `_pending` 当陈旧记录丢掉再放行(`ensure_running` 里的 `self._pending = None`), 于是
          那一次命中被扔了, 现象是"这个断点从没命中过" —— 与断点压根没挂上一模一样。
          实踩(4-3): 断[A]`:425` 与断[B]`:436` 在同一趟里只差 94 ms(J-Link 单步过断[A] 之后核
          几十毫秒就到断[B]), 断[B] 的停产记录在断[A] **读量那 480 ms 里**就到了; 三次跑全记
          "没命中", 而日志里 `*stopped,bkptno=2,addr=0x000325ce` 白纸黑字到过三次。
          判据是**断点号相同**才算"就是它", 不同照旧走 `ensure_running`(那才是陈旧记录)。"""
        self._drain_async()            # 先把 `_q` 里已到的 `*stopped` 抬进 `_pending`(不阻塞, 不判红绿)
        pend = self._pending
        if pend is None or str(pend.get("bkptno")) != str(bpno):
            self.ensure_running()      # ⚠ 不是 self.go(): 上一次可能刚 resume 过(见 ensure_running)
        hit, vals = self.wait_break(bpno, timeout=timeout, vars=vars, drop=drop)
        if hit is not None:
            self.resume()
        return {"hit": hit, "vars": vals, "result": None, "error": None, "trigger_alive": False}

    def with_trigger(self, bpno, fn, *args, **kw):
        """**串口触发 + 断点取证的标准交错** —— 支路B 脚本的基本盘。

        为什么必须这么绕(否则会死锁):
            断点命中时核心停着, **串口应答要等我们放行之后才出得来**。若在主线程顺序写
                g.go(); CB.write_billday(ser, alt)      # ← 这一步阻塞等应答
            应答永远等不到(核心停着), 于是永远走不到 g.resume() —— 死锁。
        正解: 触发放**后台线程**(它爱阻塞就阻塞), 主线程专心等断点 → 读值 → 放行,
        应答那时自然出来, 后台线程自己收尾。

        参数:
            bpno   期望命中的断点号(`break_at` 的返回值); None = 只触发不等断点。
                   **也可以是 `(源文件, 行号)`** —— 本函数当场挂、用完必撤(hit 与 miss 两条路都撤),
                   于是"这一次的断点"不会活到下一次。**高频行(如 `TaskRate.c:85`, 主循环每轮都跑)必用这种**:
                   传一个跨次复用的 bpno, 中间那些没人管的串口帧会撞上它的自命中而全哑。
            fn     `CB.write_billday` 这类阻塞式串口动作(会打印它自己的 verdict)。
            timeout 等断点的秒数(**关键字传入**, 默认 30s —— 让串口往返 + 固件路径都从容);
                    它**不是**给 fn 的, 与 `_` 前缀那几个一起在这里摘掉。
            kw["_vars"]  停住时要读的量(名字序列); `_join` 等触发线程的额外秒数;
                         `_drop` 命中后趁停住撤掉该断点再放行(见 `clear_breaks` 的 ⚠)。

        返回 dict: hit(Hit|None) / vars / result(触发函数的返回值) / error(触发里抛的异常)
                   / pair(这一段的配对号, 见 `common/events.pairing`)。

        ⚠ 2026-09-10 实踩(这颗雷藏了很久): 上面文档里一直写着 `timeout`, 但代码里**从来没从
        `kw` 里取过它** —— 于是 `t.join(timeout=timeout + join_extra)` 当场 `NameError`。
        后果不是"超时时间不对", 而是**整套"串口触发 + 断点取证"一次都没能跑起来**: 断[C]
        从没命中过、链A 必崩, 而**当时那套离线断言全绿**(没有任何一条走过 with_trigger 这条路)。
        教训不因断言被删而失效: 文档里写了的参数, 就得有一条真的走一遍才算数。
        """
        timeout = float(kw.pop("timeout", 30.0))   # ⚠ 必须摘: 它不是给 fn 的(见上面那段)
        vars_ = kw.pop("_vars", ())
        join_extra = kw.pop("_join", 5.0)
        drop_ = kw.pop("_drop", False)
        # 配对窗口的名字。**控制参数那一路**(`_` 前缀), 与 `_vars`/`_join`/`_drop` 同律:
        # 不传就按断点位置自动起一个; `fire_hit` 这类**有 `label` 的**把 label 传下来 ——
        # 那句 label 是脚本写给"这一段在干什么"的, 比自动名有用得多(见 `fire_hit`)。
        pair_name = kw.pop("_pair_name", None)
        # `bpno` 也可以直接给 **`(源文件, 行号)`** —— 那时本函数自己临时挂、**用完必撤**(hit 路径趁
        # 停住撤, miss 路径由下面的 `_disarm` 补撤)。为什么要有这条: 传一个"早已挂好、且要跨好几次
        # 复用"的 bpno, 中间那些**没有断点在管**的串口帧就会撞上它的自命中而全哑(见 `_disarm` 的 ⚠,
        # 3-1 首跑就这么废的)。高频行(每轮主循环都跑的那种)尤其只能"一次一挂"。
        # 配对号的名字: 用**人认得出的那一件**(断点在哪 + 触发干的是什么)。机器只认那个号 ——
        # 名字里不含"第几次"(同一个脚本会把同一段跑好几遍), 那正是号要回答的。⚠ 必须在
        # `bpno` 被 `break_at()` 的返回值**顶掉之前**算: 之后就只剩一个断点号, 位置信息没了。
        _who = _anchor_txt(bpno) if isinstance(bpno, (tuple, list, dict)) else (
            "断点#%s" % bpno if bpno is not None else "无断点")
        _pair_name = pair_name or "触发 %s ← %s" % (_who, getattr(fn, "__name__", "?"))
        own_bp = None
        if isinstance(bpno, (tuple, list, dict)):
            try:
                bpno, own_bp = to_bpno(self, bpno)   # 自己叫停 → 插入
                drop_ = True                      # 命中路径: 趁停住撤(撤断点的唯一时机)
            except GdbError as exc:
                # 下不上 = 白盒这一半做不成 —— 但**这一次照样发**(与 fire_hit 同律: 降级只能降白盒)。
                print("   !! with_trigger: 断点 %s 没下上(%s) → 白盒这一半不做, 触发帧照发"
                      % (_anchor_txt(bpno), exc))
                bpno = None
        box = {}

        def run():
            try:
                box["result"] = fn(*args, **kw)
            except Exception as exc:          # 触发里的异常不能吞掉, 交回主线程
                box["error"] = exc

        # ⚠ 必须 `ensure_running()` 而不是 `go()` —— 上一次 `with_trigger` 是以 `resume()` 收尾的,
        #   核心那时就在跑; 再无条件发 `-exec-continue` gdb 直接 `^error`(2026-09-10 断[C] 第二次实测)。
        self.ensure_running()
        # ---- 配对窗口: 从"放行"到"后台线程收工" --------------------------------------------
        # 这一段的**边界就是那句话的边界** —— "后台线程发的那一帧" 与 "主线程在 `wait_break` 上
        # 等到的这一停"是同一件事的两半, 出了这个窗口它们就再也对不上了。
        # ⚠ 配对号是**全局**的(不是线程局部), 否则后台那半程(帧全在那儿)一条都盖不上章 ——
        #   见 `common/events.pairing` 的 ①。
        # ⚠ `t.join` 超时后线程可能**还活着**(`trigger_alive` 会如实报): 它之后发的事件就不带
        #   这个号了。**不为此延长窗口** —— 那种情况本来就要当"没做成"看, 不该假装还在配对里。
        with events.pairing(_pair_name) as _pw:
            t = threading.Thread(target=run, name="breakpoint-trigger")
            t.daemon = True
            t.start()
            hit, vals = (None, {})
            if bpno is not None:
                hit, vals = self.wait_break(bpno, timeout=timeout, vars=vars_, drop=drop_)
                if hit is not None:
                    self.resume()
            t.join(timeout=timeout + join_extra)
        if own_bp is not None and hit is None:
            self._disarm(bpno)          # ⚠ 没命中 ⇒ 它还在槽里, 不补撤下次就自己命中把核撂停
        # ⚠ **把号交回去**(2026-09-18): 窗口在 `return` 之前就关了, 而"停住时读到的值"要等到
        #   `fire_hit` 收工之后才由 `report` 记成 `bp.hit` —— 那一记**在窗口外**, 于是 `bp.hit`
        #   一条号都不带, 与它那张帧/那一停**对不上**(16-2 真跑量到: 4 个窗口里帧与停都带着号,
        #   只有值那一条是光头)。号只能由开窗口的这一层交出来, 别处没有。
        return {"hit": hit, "vars": vals,
                "result": box.get("result"), "error": box.get("error"),
                "trigger_alive": t.is_alive(), "pair": _pw.no}

    # ---------------------------- 注入点: 地址由 .out 推出, 不许手抄 ----------------------------
    # 这一段下面的四个方法**一个都不反汇编** —— 它们只查 `swdbg/gdbinit.py` 建的那张地图
    # (`gdbinit.build(self)` 在脚本开头调一次)。反汇编原语也在那个文件里, 本文件不再是它的家。
    def inject_anchor(self, func, callee):
        """从 `.out` 的反汇编里找 `func` 中**紧跟** `call <callee>` 之后的那条指令 → 注入点。

        为什么需要它(而不是把地址抄进脚本): 注入点常常正好落在**行号信息的空档**里。本函数的出处
        `TaskRate.c` 就是 —— `:180` 是一条 `Read_ParaData(ID_RatePara, &g_RatePara[0])` 调用, 而
        `:181-187` 那整条 `||` 守卫链**一个字节的代码都没分到**(编译器把它并进了 `:180` 的区间:
        `info line` 说 `:181` "contains no code")。于是"**EEPROM 刚读完、守卫还没判**"这个**唯一
        能注入的瞬间**没有行号可选, 只剩地址。

        为什么注入点必须在**调用之后**: 那个 `Read_ParaData` **每轮把整个数组从 EEPROM 重读一遍**
        —— 在它之前注入的值下一句就被覆盖, 守卫于是判的是 EEPROM 的值 ⇒ 判据断点永不命中, 表现为
        **静默假阴性**(看上去像"固件没走那条分支")。这是本层最像"没做成"的一种错, 所以断点不许手抄。

        地址**由 ELF 推出并打印**(`符号+偏移` + 那条指令原文), 与"脚本里写死一个 `0x…`"是两回事:
        前者可核(拿 `info line`/反汇编对得上)、固件换版跟着变, 后者是抄来的魔数。
        收 `(函数名, 被调名)` 两个**名字**, **不收地址**。
        """
        cands = gdbinit.calls_of(self, func)
        if not self.quiet:
            print("   %s" % loglabel.debug_line(loglabel.DEBUG_GDB, "%s 里有 %d 个可注入的调用点, 找 `call %s`" % (func, len(cands), callee)))
        for a in cands:
            if a["callee"] == callee:
                if not self.quiet:
                    print("   %s" % loglabel.debug_line(
                        loglabel.DEBUG_GDB, "注入点 %s @0x%08X  %s   (%s)"
                        % (a["loc"], a["addr"], a["insn"], a["why"])))
                return a
        raise GdbError("在 %s 里找不到紧跟 `call %s` 的指令\n"
                       "  函数名/被调名拼错? 还是被内联掉了(那就得换一个断点)?\n"
                       "  该函数里能找到的调用点是: %s"
                       % (func, callee,
                          ", ".join(sorted(set(c["callee"] for c in cands))) or "(一个都没有)"))

    def inject_anchors(self, func, callee):
        """`inject_anchor` 的**复数形**: `func` 里**每一个**紧跟 `call <callee>` 之后的指令。

        出处(3-1 风险②, 2026-09-14 建): 判据要的是**同一件事发生两次**, 而两次的可注入瞬间是
        **同一个被调函数在同一个函数里的两个调用点** —— `VerRd_EEprom` 里对 `Read_EEprom` 的两次调用
        (先读主份、不过再读备用份; `Platform/EEprom.c`), 只有把**两处**都打坏才逼得出那条
        `return OTHER`。而 `inject_anchor` 只回**第一个**, 于是第二处无断点可用 —— 剩下的路只有
        "手抄地址", 那正是本层立 `inject_anchor` 要禁的事(见它的 docstring)。

        排序 = 反汇编顺序 = **执行顺序**(主份在前、备用份在后)。调用方按位置取:
            a = sess.inject_anchors("VerRd_EEprom", "Read_EEprom")   # 两份里挑
            A, B = a[0], a[1]

        ⚠ **找不到任何一个 → 抛**(不返回空表): 与 `inject_anchor` 同一条分寸 —— 空表会被调用方
          当成"没断点可用"静默降级, 而真因多半是函数名/被调名拼错或被内联掉了。
        ⚠ 只查地图 ⇒ **全程不停核、不放行**, 没有收尾态要声明。
        """
        cands = gdbinit.calls_of(self, func)
        hits = [a for a in cands if a["callee"] == callee]
        if not hits:
            raise GdbError("在 %s 里找不到紧跟 `call %s` 的指令\n"
                           "  函数名/被调名拼错? 还是被内联掉了(那就得换一个断点)?\n"
                           "  该函数里能找到的调用点是: %s"
                           % (func, callee,
                              ", ".join(sorted(set(c["callee"] for c in cands))) or "(一个都没有)"))
        if not self.quiet:
            print("   %s" % loglabel.debug_line(loglabel.DEBUG_GDB, "%s 里有 %d 处 `call %s`:" % (func, len(hits), callee)))
            for i, a in enumerate(hits):
                print("   %s" % loglabel.debug_line(loglabel.DEBUG_GDB, "  [%d] %s @0x%08X  %s" % (i, a["loc"], a["addr"], a["insn"])))
        return hits

    def decision_anchor(self, func, callee):
        """**判定断点** —— `func` 里 `call <callee>` 之后**第一条条件分支**, 连同它两个落点一起返回。

        出处(2-1 判据③, 2026-09-11 实跑定案): 「算完 `Diff_Secs` 之后 `> 1` 才跟随」这条判据,
        原先的观测是"在判定那句停住注值 → 放行 → 在 1.5s 窗口里等跟随支命中与否"。**那把尺子是坏的**:
          · `stop_latency` 里混进了 `go()` 的 0.4s 静默 ⇒ 读数恒 ≥0.4s, 阈值 0.5s 拦不住任何东西;
          · 停核要 1~3s, 而管理芯钟是**软件走时** ⇒ 放行那一刻管理芯已落后计量芯 1~3s(> 阈值)
            ⇒ **下一次自然比较紧跟其后就跟随**, 窗口里那一停根本不是我们造的那次执行。
        本函数给的是**另一把尺子**: 把断点下在**判定指令本身**(那条 `cmp`/`bcc` 上), 于是一
          · 注完放行后, **当前这次调用必然继续走到它**(此前的分派已判过) ⇒ **放行后的第一停就是
            自己**, 没有窗口、没有抽签(2-1 实测 0.031s);
          · 停在它上面就能**就地读出判定**: 前一句 `cmp` 的寄存器值 + **单步一条**看 PC 落到哪个落点。

        返回值(与 `inject_anchor` 同形, 可直喂 `break_at_anchor`/`with_inject(watch=…)`, 另加三样):
            `prev`          判定前一条指令的原文(说得出"比的是什么")
            `target`        条件成立时跳去的地址 = **跟随支之外**那一支
            `fallthru`      **不跳**时继续执行的地址(= `target` 的对偶), 与 `fallthru_insn`
        判据于是可以写成一句**与机制无关**的话: **单步落点 == `fallthru`** ⇔ 那条被条件保护的
        代码真的被执行了(2-1: `fallthru` = `0x28BB6 add r0, sp, #4` = `Set_MeterTime(objtime)`
        的第一个字节; `target` = `0x28BBC`)。
        ⚠ **不收手抄地址**: 两个落点都是从 `.out` 反汇编里读出来的, 并打印进证据 —— 固件换版
          跟着变, 与"脚本里写死一个 `0x…`"是两回事。
        ⚠ 只读地图 ⇒ **不停核、不放行**。它要的是**整条指令流**(判定句的前一句与后一句),
          所以地图里存的是 `insns` 而不是只有调用点 —— 见 `gdbinit` 文件头。
        """
        start = gdbinit.func_entry(self, func)
        insns = gdbinit.insns_of(self, func)            # [(地址, 指令原文)], 整条
        seen_call = False
        for i, (addr, txt) in enumerate(insns):
            if not seen_call:
                m = re.match(r"^blx?\s", txt) and re.search(r"<([^>+]+)", txt)
                if m and m.group(1) == callee:
                    seen_call = True
                continue
            m = re.match(r"^(b\w*)(\.\w+)?\s+(0x[0-9a-fA-F]+)", txt)
            if not m or m.group(1) in ("b", "bl", "bx", "blx"):
                continue                    # 无条件分支/调用不是"判定"
            if i == 0 or i + 1 >= len(insns):
                continue
            a = {"loc": "%s+%d" % (func, addr - start),
                 "addr": addr,
                 "insn": txt,
                 "why": "`call %s` 之后第一条条件分支 = 判定指令(停在它上面就地读判定, 不等窗口)"
                        % callee,
                 "func": func, "callee": callee,
                 "prev": insns[i - 1][1],
                 "target": addr_tok(m.group(3)),
                 "fallthru": insns[i + 1][0],
                 "fallthru_insn": insns[i + 1][1]}
            if a["addr"] is None or a["target"] is None or a["fallthru"] is None:
                raise GdbError("判定断点的地址解析不了: %r" % (a,))
            if not self.quiet:
                print("   %s" % loglabel.debug_line(loglabel.DEBUG_GDB, "判定断点 %s @0x%08X  %s   (前一句 %r)"
                      % (a["loc"], a["addr"], a["insn"], a["prev"])))
                print("   %s" % loglabel.debug_line(loglabel.DEBUG_GDB, "  跳 = 0x%08X(不执行)   不跳 = 0x%08X %s"
                      % (a["target"], a["fallthru"], a["fallthru_insn"])))
            return a
        raise GdbError("在 %s 里找不到 `call %s` 之后的条件分支\n"
                       "  函数名/被调名拼错? 还是被内联掉了(那就得换一个断点)?" % (func, callee))

    def callees(self, func):
        """列出 `func` 里**每一个可注入的调用点** —— 返回值与 `inject_anchor` 同形, 故可直接喂
        `break_at_anchor`。

        为什么单提出来: `inject_anchor(func, callee)` 要调用方**先知道被调名**。对熟悉这份固件的人
        那不是事; 对一块**陌生的表**就变成了"我得先有人告诉我"。本函数把它变成"**列出来挑**" ——
        换表时先 `callees("某函数")` 看一眼有哪几个时机可注入, 再挑一个当断点。
        (2026-09-11 建。此前这一步只能靠人读反汇编, 而那正是"断点不许手抄"要防的事。)
        ⚠ 只读地图 ⇒ **不停核、不放行**, 没有收尾态要声明。
        """
        return gdbinit.calls_of(self, func)

    def func_addr(self, func):
        """取函数入口地址(**由 .out 的符号表给, 不收手抄的地址**)。"""
        return gdbinit.func_entry(self, func)

    def break_at_anchor(self, anchor):
        """按**锚点**下断点 —— 收 `inject_anchor()` 的产物(dict), 或**字面量**
        (`("call"|"prev", 函数, 被调, n)` / `("func", 函数名)` / `("文件", 行号)`,
        一律由 `gdbinit.map_of` 从地图上解出); 不收手抄的地址。"""
        if isinstance(anchor, (tuple, list)):
            try:
                spec = normalize(anchor)          # 认不出当场 ValueError(写法只由那一处定义)
            except ValueError as exc:
                raise GdbError("%s" % exc)
            anchor = gdbinit.map_of(self, spec)
            if not self.quiet:
                print("   %s" % loglabel.debug_line(
                    loglabel.DEBUG_GDB, "锚点 %s @0x%08X  %s   (%s)"
                    % (anchor["loc"], anchor["addr"], anchor["insn"], anchor["why"])))
            events.emit("bp.anchor", spec=anchor_spec_txt(spec), loc=anchor["loc"],
                        addr="%08X" % anchor["addr"], insn=anchor["insn"])
        if not isinstance(anchor, dict) or "loc" not in anchor or "addr" not in anchor:
            raise GdbError(
                "break_at_anchor 只收 inject_anchor() 的产物(带 loc/addr 的 dict), 收到 %r\n"
                "  别在这里手抄地址/行号: 地址要由 .out 推出来、并打印进证据(见 inject_anchor 的 docstring)"
                % (anchor,))
        if not self.watchdog:
            raise GdbError("没配 watchdog 就不许下断点 —— 停住超过约 8s 表会被 IWDT 复位。\n"
                           "  Session(out=..., watchdog=(IWDT_SERV地址, 0x12345A5A))")
        if len(self._bps) >= MAX_HW_BREAK:
            raise GdbError("已有 %d 个硬件断点, Cortex-M0 只有 %d 个槽。先 clear_breaks()。"
                           % (len(self._bps), MAX_HW_BREAK))
        self.ensure_stopped()              # 下断点只能趁停住(见 ensure_stopped 的 ⚠)
        r = self._cmd('-break-insert "*0x%X"' % anchor["addr"])
        if not r or r.get("_class") != "^done":
            raise GdbError("下断点失败 *0x%X(%s): %s"
                           % (anchor["addr"], anchor["loc"], (r or {}).get("msg", "无应答")))
        bk = r.get("bkpt") or {}
        if not bk.get("addr") or int(str(bk.get("addr")), 16) == 0:
            raise GdbError("断点 %s 没落到地址上(pending?)" % anchor["loc"])
        no = str(bk.get("number"))
        self._bps[no] = (anchor["loc"], 0, "0x%08X" % anchor["addr"])
        if not self.quiet:
            print("   %s" % loglabel.debug_line(loglabel.DEBUG_GDB, "断点 %s → %s @0x%08X" % (no, anchor["loc"], anchor["addr"])))
        return no

    # 配对窗口的名字 = 注入点(三个注入原语的断点都在第 2 个位置参数上, 见 `_pair_window`)。
    # 断点在名字里是**承重的**: 同一个脚本会把同一段跑好几遍, "这次注的是哪个断点"只有它答得了。
    @_pair_window(lambda self, at, *a, **k: "注入 %s" % _anchor_txt(at))
    def with_inject(self, at, assigns, watch=None, watch_vars=(), at_vars=(),
                    timeout=30.0, drop=None, trigger=None, trigger_args=(), join=5.0,
                    watch_regs=(), step=False, steps=0, then_assigns=None, gate=None):
        """**停着触发** —— 与 `with_trigger` 并列的第二种触发交错:
        在 `at` 停住 → 下 `watch` 断点 → 写 `assigns` → 撤 `at` → 放行 → 等 `watch` 命中。

        为什么不能塞进 `with_trigger`: 那边的 `fn` 是**阻塞式串口动作**(放后台线程, 靠核心**在跑**
        才收得到应答); 注入恰好相反 —— `inject()` 只在**停住态**成立。两者"触发时机"根本不同,
        硬合会当场 `_require_stopped` 抛错。

        为什么它够格当一条原语(而不是各脚本内联那五步): 中间全是**时机纪律**, 抄一遍错一遍 ——
          ① `at` 必须落在"要改的量已被填好、这一轮还没被重读覆盖"的位置。反例(本函数的出处,
             `TaskRate.c`): `:180` 每轮从 EEPROM 重读 `g_RatePara`, `:181-186` 是守卫, `:188` 是
             兜底。断点要核歪到 `:180` **之前**, 注进去的值下一句就被重读覆盖 ⇒ 判据断点永不命中 ⇒
             **静默假阴性**(看起来像"固件没走那条分支")。故返回里带 `at_hit`, 取证时地址对得上。
          ② `watch` 必须**趁停着**下 —— `resume()` 之后 `-break-insert` 会被 gdb 拒(见 `ensure_stopped`
             的 ⚠)。所以两个断点的插入次序是死的: 先 `at`、停住、再下 `watch`。
          ③ 撤 `at` 必须**趁停着、且在放行之前** —— 不然它自己会再命中一次把核撂停(见 `_disarm` 的 ⚠)。
          ④ 任何异常路径都不许把核心留在停住态(否则后面每条帧都像"串口坏了")。

        参数:
            at         **注入停靠点**, 三种写法:
                         · `(源文件, 行号)` —— 行号够用时用它;
                         · **`inject_anchor()` 的产物**(dict) —— 行号信息的空档就用它: 本函数的出处
                           `TaskRate.c` 那整条守卫链**一个字节代码都没分到行号**, 唯一可注入的瞬间
                           (读完 EEPROM、守卫未判)只有地址, 而地址由 ELF 推出、打印进证据, 见
                           `inject_anchor` 的 docstring(**拒收手抄的地址/裸 int**, 那正是要防的东西);
                         · 已挂好的 bpno(那时不替你撤)。
            assigns    `[(expr, value), …]` —— 走 `inject()` 同一条受控口径(白名单/不收裸地址/自动还原)。
            watch      `(源文件, 行号)`|`inject_anchor()` 产物|bpno: 注入后**应当命中**的判据断点
                       ("执行到 `:<行>` 才成立"的那一句); `None` = 只注入、不等。
            watch_vars 命中那一刻要读的量(现查现读; 放行后读到的已不是那一刻的值)。
            at_vars    **注入之前**在注入点那一停读的量 → 返回 `at_vals`。这是**对照**: 它证明
                       "我们停对了地方、被调函数的确把状态重读/重算过了"(例: 3-1 的 E1 —— 停在
                       `Read_ParaData` 之后读到的是 EEPROM 的合法值, 而不是上一轮我们注入的残留)。
                       没有它的话, "注入点是不是落在重读之前"只能靠推理, 而那个错法是**静默假阴性**。
            drop       `watch` 给的是**已有 bpno** 时, 命中后撤不撤(默认不撤 = 调用方要跨次复用);
                       给 `(文件,行号)`/断点时一律撤。
            trigger    **可选的动作**(`trigger_args=(ser, …)`) —— 与 `with_trigger` 的 `fn` 同理:
                       要注入的那个函数**可能不是热点**, 只在某个消息/事件驱动下才跑。那时"干等"
                       永远等不到停 — 本参数的出处就是 3-1 的 `Calculate_RateNo`: 它由
                       `Post_Message(ID_TaskRate, MSG_MinStep)` 驱动(写一次费率参量才跑一轮),
                       **不是每轮主循环都调**。没有触发时 `wait_break` 只会静静超时 ⇒ 记出来是
                       "没停到注入点", 与"断点错了/固件不对"长得一模一样(**静默假阴性**, 2026-09-11 实踩)。
                       ⚠ 触发**必须放后台线程**: 它是阻塞式串口动作, 而应答要等核心在跑才出得来 ——
                       主线程一边等它在断点上停住, 它一边把帧发出去(与 `with_trigger` 同构)。
                       放行与等待的次序与本函数**无关**: 触发线程只负责"让那段代码跑起来"。
            trigger_args 传给 `trigger` 的位置参数(通常是 `(ser, …)`)。
            join       `trigger` 线程的额外收尾秒数(触发里的串口往返可能比等断点慢)。
            watch_regs 命中 `watch` 那一刻要读的 **CPU 寄存器**名(如 `("r0","pc")`) → `out["regs"]`。
                       ⚠ 只有"停在判定指令上"才有意义: 那时"判出来是多少"只写在寄存器里, 源码里
                       没有变量名可读(`read_vars` 读的是 DWARF 表达式)。见 `read_regs` 的 ⚠。
            step       `True` = 命中后**再单步一条指令**, 落点 PC 进 `out["step_pc"]`。
            steps      `N` > 0 = 命中后**有界连续单步 N 条**, 落点逐个进 `out["step_trace"]`
                       (放行前读, 与 `step` 同一时机)。**只在"停到了却看不见下一行"时用** ——
                       它回答的是"PC 拐哪儿去了"。上限由调用方给死; 中途读不到 PC 就提前收工,
                       `step_trace` 末尾留一个 `None` 当记号(那是环境问题, 不是固件不对)。
                       ⚠ 本处**不挂任何观察点** —— 观察点走 MI `-break-watch -a`, 由 GDB Server 自己
                         配 DWT(硬件访问观察点, 命中即把核停住), 见 `swdbg.selfcheck` 第 3 关:
                         这里只做「步数由调用方给死」的有界单步。
                       这是"判定**实际**走向"的硬证, 与机制无关: 单步落点 == `decision_anchor()`
                       的 `fallthru` ⇔ 那条被条件保护的代码真被执行了。
                       ⚠ 判定断点往往压在**高频行**上(2-1 的判定指令每次 SPI 送达都走到, ~1Hz), 所以
                       本参数只与 `watch` 一起用, 而 `wait_break` 已经把断点**在命中那一刻撤了**
                       (`drop`)—— 留着它单步会撞上下一次自然命中, 把 `_stopped`/`_pending` 一起污染
                       (首版探针就是这么死的)。调用方若给的是**已有 bpno 且不撤**, 后果自负。
            then_assigns **判据断点命中那一刻再补一次受控写** —— 与 `assigns` 同一条 `inject()` 口径,
                       只是**落在后一个断点上**(即 `watch` 命中、核正停着时; 读进 `out["then"]`)。
                       用处是**收尾净零**: 有些固件副作用只在"到了判据断点"之后才发生, 而它没有反向
                       复位口 —— 1-3 的出处: `TaskDisplay.c:3171 Set_DispPara(dot, Borrow)` 会往
                       EEPROM 写 `Borrow`, 而本台 645/698 改参口**全注释**、固件内没有别的复位口,
                       于是"证明联动"和"把表改脏"绑在一起。在这里把 **`g_DispPara[Borrow]`(索引 11)**
                       注成 0 ⇒ 那次调用写下的就是原值 ⇒ 联动被证(分支确实以 `dot > Borrow` 命中)、
                       表侧净零。
                       ⚠ **别写成"把 `dot` 注成 0"** —— `dot` 是 `Disp_Energy` 的**函数内局部量**、
                       寄存器驻留, 在这一刻根本不属于本栈帧, 写它既无意义也不在(也不该在)白名单里;
                       真正要改的是**那个被写进 EEPROM 的全局量**。
                       ⚠ 只有**真停在 `watch` 上**才写(`hit is None` ⇒ 跳过): 没到那一刻, 写下去
                         就是无的放矢。⚠ 它写在 `resume()` **之前**(那一刻核停着, `inject()` 才成立)。
            gate       **汇合点断点** —— 在 `at` **之前**先停一下, 把"这一次调用"认下来。写法同上三种。
                       为什么非有不可(2026-09-14 建, 3-1 风险② 的出处): `at` 若落在一个**热路径**
                       函数里(每一个参数读都要过的 `VerRd_EEprom` 就是), 那么"挂上 `at` → 触发 → 等它
                       命中"是一个**抽签** —— 先撞上断点的多半是**别人的**一次调用, 于是被打坏的是
                       **无关参数**。而它的表象是"我们想造的那个状态没出现", 也就是本仓最忌讳的
                       **静默假阴性**(会让人去怀疑断点/固件, 而不是怀疑"停错了一次调用")。
                       给了 `gate` 就**没有窗口**: 先停在"只可能由我们那一帧造成"的地方(如写入口那
                       一行, 见 3-1 的 `DLT645App.c:1752`), 那一刻离真正要注的那次调用只剩几条指令
                       ⇒ 随后命中的 `at` **必然是它**。停完立刻撤断点(`drop=True`), 不留高频行残留。
                       ⚠ `gate` 下不上/没停到 ⇒ **一个字都不注入**, 返回 `gate_hit=None` +
                       `unavailable`, 由调用方记「未做成」(这与"注了没命中"不是一回事)。
        返回 dict: `gate_hit`(给了 `gate` 才有) / `at_hit` / `at_vals` / `hit` / `vars` / `injected`
                   / `unavailable` / `note`
                   / `result`(触发的返回值) / `trigger_error`(触发里抛的异常)。
            `at_hit=None` 或 `unavailable` ⇒ **一个字都没改**, 这一半没做成(调用方记「未证」)。
        ⚠ `inject()` 自己抛的错(白名单没点名 / gdb 写失败)**原样抛出** —— 那是**脚本配置错**,
          该当场炸, 不该伪装成"没做成"混进账本。下不上断点(槽满/文件行号对不上)则是环境问题,
          收进 `unavailable` 里返回。
        """
        # ⚠ 裸地址/裸 int 在**进 try 之前**就拒 —— 否则会被下面那个 `except GdbError` 收成
        # "注入点下不上"(一条 ok=None 的记录), 于是**脚本写错**伪装成**环境不行**, 谁也不去查。
        # 这类错必须当场炸: 它与 `inject()` 的白名单拒是同一类(配置错 ≠ 没做成)。
        if isinstance(at, int) and not isinstance(at, bool):
            raise GdbError("with_inject 的 at 不收裸地址/裸 int(%r) —— 要么给 (文件,行号), "
                           "要么给 inject_anchor() 的产物" % (at,))
        if isinstance(watch, int) and not isinstance(watch, bool):
            raise GdbError("with_inject 的 watch 不收裸地址/裸 int(%r)" % (watch,))
        if isinstance(gate, int) and not isinstance(gate, bool):
            raise GdbError("with_inject 的 gate 不收裸地址/裸 int(%r) —— 汇合点也要由 (文件,行号) "
                           "或 inject_anchor() 的产物给" % (gate,))
        # ⚠ 汇合点**没有触发就是错的**: 它的全部意义是"停在**我们那一帧**造成的那一步上"。没有触发帧,
        #   它就退化成"在一条高频行上等下一个自然命中" —— 正是 `gate` 要消灭的那种抽签。当场炸,
        #   别让它伪装成"汇合点停不到"(那是环境), 这是**配置错**(与本函数对白名单/裸地址的口径一致)。
        if gate is not None and trigger is None:
            raise GdbError("with_inject 给了 gate 却**没给 trigger** —— 汇合点是『停在我们那一帧造成的"
                           "那一步』, 没有触发帧就只能等自然命中(高频行上必是抽签)。要么给 trigger, "
                           "要么别给 gate。")
        out = {"gate_hit": None, "at_hit": None, "at_vals": {}, "hit": None, "vars": {},
               "injected": [], "injected_new": [],
               # `assigns` 原样带出来: `injected` 只记"写到了哪个变量", 而**写成了什么值**只有这里有
               # —— 记录里缺了它, 复核的人就看不出"注进去的到底是什么"(2-1 的 `g_MeterTime[0]+1`
               # 这种**表达式**尤其没法从别处反推)。
               "assigns": [[e, v] for e, v in (assigns or [])],
               "unavailable": False, "note": "", "result": None, "trigger_error": None,
               # 放行→停住之间的墙钟秒数(None = 没停住)。**"这一停归谁"的线索, 不是凭据**, 见下 ⚠。
               # 本层只**记账**, 不判 —— 归不归这一次是**调用方**的策略(cmd_bank 的 `_inject_shot`)。
               "stop_latency": None,
               # 命中那一刻的寄存器(`watch_regs=`)与单步落点(`step=`) —— 就地读判定用, 见参数说明
               "regs": {}, "step_pc": None, "step_trace": None,
               # 判据断点命中那一刻补的那一次写(`then_assigns=`) —— 逐条 `inject()` 记录 + 原样的
               # `(表达式, 值)`。与 `injected` 分开放: 那批是"在 stop 点改的", 这批是"在 watch 点改的",
               # 复核时"改在哪儿"是承重信息。
               "then": [], "then_injected": [], "then_injected_new": []}
        own_at = isinstance(at, (dict, tuple, list))
        own_w = isinstance(watch, (dict, tuple, list))
        # `gate` **一律是本函数自己挂的**(已有 bpno 对它没有意义: 要的是"等它命中一次然后撤掉"),
        # 故不走 `own_*` 那套 —— `bp_gate is not None` 就是"还有断点要撤"。
        bp_at, bp_gate = None, None
        box, th = {}, None

        def _start_trigger():
            """把触发动作放**后台线程**发出去(阻塞式串口动作; 应答要等核心在跑才出得来)。"""
            def run():
                try:
                    box["result"] = trigger(*trigger_args)
                except Exception as exc:      # 触发里的异常不吞, 交回主线程
                    box["error"] = exc
            t = threading.Thread(target=run, name="breakpoint-inject-trigger")
            t.daemon = True
            t.start()
            return t

        def _settle():
            """收线: 等触发线程收尾, 把结果/异常记进 out。

            ⚠ 触发里的异常**不向上抛**, 而是记进 `out["trigger_error"]` —— 与 `inject()` 的白名单拒
            不同: 那是**脚本配置错**(该当场炸), 而触发里最可能出的是**串口没应答/台面抽风**(环境)。
            两者混在一起的话, 一次串口抖动会把整条测试脚本崩掉; 记成"没做成 + 缘由"才是它该有的分量。
            调用方(`inject_hit`)据此记 `ok=None` 并把缘由写进 detail。"""
            if th is not None:
                th.join(timeout=timeout + join)
            out["result"] = box.get("result")
            if box.get("error") is not None:
                out["trigger_error"] = box["error"]
                out["note"] = (out.get("note") or "") + " | 触发里抛了: %s" % box["error"]

        def _desc(x):
            return _anchor_txt(x) if isinstance(x, (dict, tuple, list)) else x

        def _place(x):
            """下断点 —— 各种断点写法都经 `to_bpno`; **裸地址一律拒**。"""
            if isinstance(x, int):
                raise GdbError("with_inject 不收裸地址/裸 int(%r) —— 注入点要么给 (文件,行号) 或"
                               "字面量锚点, 要么给 inject_anchor() 的产物" % (x,))
            return to_bpno(self, x)[0]

        def _recover():
            """异常路径: 撤掉这一次自己的断点 + 把核弄回运行态(绝不留停住态出去)。"""
            try:
                if own_at and bp_at is not None:
                    self._disarm(bp_at)
            except Exception:
                pass
            try:
                if bp_gate is not None:       # 汇合点断点也是本次自己挂的, 同一条账
                    self._disarm(bp_gate)
            except Exception:
                pass
            try:
                self.ensure_running()
            except Exception:
                pass

        try:
            # ---- 汇合点(gate): 先把"这一次调用"认下来, 再谈注入(见参数说明) ----
            # 次序是死的: 挂汇合点 → 起触发(那一帧才是造成汇合点命中的原因) → 等汇合点 → 撤汇合点 →
            # 这时核**停着且在"我们那次调用"的几步之前**, 才轮到 `at` 的挂/放行/等待。
            if gate is not None:
                try:
                    bp_gate = _place(gate)
                except GdbError as exc:
                    print("   !! with_inject: 汇合点断点 %s 没下上(%s) → 本次不注入(一个字都没改)"
                          % (_desc(gate), exc))
                    out["unavailable"], out["note"] = True, "汇合点断点下不上: %s" % exc
                    return out
                # ⚠ **放行核心, 且必须排在起触发线程之前**(2026-09-14 实跑踩出来的, 见下)。
                #   `_place` → `break_at` **会 `ensure_stopped()`**(下断点只能在停住态, 那是它的
                #   docstring 明写的纪律)⇒ 到这里核是**停着**的。而触发帧是阻塞式串口动作, 它的应答
                #   要等**核心跑起来**才出得来 —— 停着发帧 = 那一帧永远没人理 (`RX(0)`), 于是汇合点
                #   必然等不到, 本次静默地什么都没干。**这正是 `with_trigger` docstring 讲的死锁**,
                #   只不过这里的"等应答"发生在后台线程里, 所以它不死锁、而是**悄悄失败**。
                #   另: `ensure_stopped()` 还会把"我方叫停"那条 `*stopped` 压进 `_pending`,
                #   `wait_break` 会把它**当成一次命中**报出来(`bkptno=None` ⇒ 打印成"掠过非目标
                #   断点", 而 PC 只是叫停那一刻碰巧所在的指令) —— 2026-09-14 那轮日志里的
                #   `掠过非目标断点 None ST75263S.c:501 @0x0003fe06` 就是它。本行同时消掉这两个。
                self.ensure_running()
                if trigger is not None:
                    th = _start_trigger()
                n_other0 = len(self.other_hits)
                g_hit, _ = self.wait_break(bp_gate, timeout=timeout, drop=True)
                out["gate_hit"] = g_hit
                if g_hit is None:
                    stray = len(self.other_hits) - n_other0
                    out["note"] = ("没停到汇合点断点 %s ⇒ 这一次调用没认下来, 本次不注入(一个字都没改)"
                                   "\n   (汇合点停不到, 多半是触发帧没发出去/没到那一步 —— 别把它当成"
                                   "『注入点没命中』)"
                                   "%s" % (_desc(gate), (
                                       "\n   (等它期间还掠过 %d 次**非目标**停靠(不是我们登记的断点"
                                       " ⇒ 多半是残留/叫停通知; 见 _clear_stale_fpb 的 ⚠)" % stray
                                       if stray else "")))
                    print("   !! with_inject: %s" % out["note"])
                    self._disarm(bp_gate)     # 没命中 ⇒ 断点还挂着, 收走(收尾会放行核心)
                    self.ensure_running()     # 触发线程还等着串口应答, 必须放行
                    _settle()                 # 也要把**触发那一帧的结果**记下来(见下 ⚠)
                    out["note"] += ("\n   (触发帧的结果: %s —— 收到应答=%s; 没应答就是"
                                    "帧根本没到那一步, 不是汇合点断点选错了)"
                                    % ("异常 %s" % out["trigger_error"]
                                       if out.get("trigger_error") else
                                       "正常返回", out.get("result") is not None))
                    return out
                if not self.quiet:
                    print("   %s" % loglabel.debug_line(loglabel.DEBUG_GDB, "汇合点 %s @%s —— 这一次调用认下来了, 接着挂注入点"
                          % (_desc(gate), g_hit.where())))
                # 汇合点命中的那一刻核停着 —— `at` 要趁这个时候挂(见 `ensure_stopped` 的 ⚠)。
            try:
                bp_at = _place(at) if own_at else at
            except GdbError as exc:
                # 白盒这半没做成 —— 但**一个字都没改**(没停住就没注入)。大声说, 别伪装成"没命中"。
                print("   !! with_inject: 注入停靠点 %s 没下上(%s) → 本次不注入" % (_desc(at), exc))
                out["unavailable"], out["note"] = True, "注入点下不上: %s" % exc
                if bp_gate is not None:
                    self._disarm(bp_gate)
                return out
            self.ensure_running()             # `_place` 内部会 ensure_stopped, 这里放行
            # 触发与等待**必须同时进行**(见 `trigger` 参数说明): 先起线程把驱动动作发出去, 主线程
            # 才等在断点上 —— 反过来的话(先发完再等)那条代码早跑完了, 断点永远等不到停。
            # ⚠ 汇合点那一支**已经起过触发线程了**(`th is not None`), 这里别重复发一帧。
            if trigger is not None and th is None:
                th = _start_trigger()
            at_hit, _ = self.wait_break(bp_at, timeout=timeout)
            out["at_hit"] = at_hit
            if at_hit is None:
                out["note"] = ("没停到注入点 %s ⇒ 本次不注入(一个字都没改)"
                               "%s" % (_desc(at),
                                       "(已给触发= %s)" % getattr(trigger, "__name__", trigger)
                                       if trigger is not None else
                                       "(**没给触发** —— 若那段代码不是热点, 干等必然等不到)"))
                print("   !! with_inject: %s" % out["note"])
                if own_at:
                    self._disarm(bp_at)       # 它的收尾会放行核心, 触发线程随后自己收线
                self.ensure_running()         # 没撤断点的那些也要放行: 否则触发线程永远等不到串口应答
                _settle()
                return out
            # ---- 停着: 次序见 docstring ②③, 不许调换 ----
            # `at_vars` 读在**注入之前** —— 它是"停对了地方、被调函数确实重读过"的对照(见参数说明)。
            out["at_vals"] = self.read_vars(at_vars) if at_vars else {}
            bp_w = None
            if watch is not None:
                try:
                    bp_w = _place(watch) if own_w else watch
                except GdbError as exc:
                    print("   !! with_inject: 判据断点 %s 没下上(%s) → 不注入(注了也没人在看)"
                          % (_desc(watch), exc))
                    out["unavailable"], out["note"] = True, "判据断点下不上: %s" % exc
                    if own_at:
                        self._drop_bp(bp_at)
                    self.resume()
                    _settle()
                    return out
            for expr, val in assigns:
                rec = self.inject(expr, val)
                out["injected"].append(rec)
                # 写完**读回来** —— 只记表达式文本的话, "注入生效"与"恰好写在原值上"在账本里
                # 长得一样(见 `_inj_line` 的 ⚠)。读不到记 None, **不抛**(见 `_read_back` 的 ⚠)。
                out["injected_new"].append(self._read_back(rec))
            if own_at:
                self._drop_bp(bp_at)          # 趁停住撤(放行之后撤不了)
            t_go = time.monotonic()           # ⚠ 放行**那一刻**的墙钟; 用来算 `stop_latency`
            # ⚠ `settle=False` **是必须的**(2026-09-11 实踩): 缺省的 0.4s 静默会被原样算进
            #   `stop_latency`, 于是"微秒级的那一次执行"和"~1s 后来的自然那次"读出来都是 0.4~0.5s
            #   —— 2-1 的 ③ 段正是拿这个秒数判"这一停归谁", 三次实跑里它一次都没判对过。
            #   这里放行之后只等断点、不发串口帧 ⇒ 那 0.4s 对本调用点毫无用处。
            self.resume(settle=False)
            if bp_w is None:
                _settle()
                return out
            hit, vals = self.wait_break(bp_w, timeout=timeout, vars=watch_vars,
                                        drop=(own_w if drop is None else drop))
            out["hit"], out["vars"] = hit, vals
            # `then_assigns` —— 判据断点上补的那一次受控写(见参数说明; 核此刻**停着**, `inject()` 才成立,
            # 故必须在下面 `resume()` 之前)。`hit is None` = 没到那一刻 ⇒ 跳过, 不无的放矢。
            if hit is not None and then_assigns:
                out["then"] = [[e, v] for e, v in then_assigns]
                for expr, val in then_assigns:
                    rec = self.inject(expr, val)
                    out["then_injected"].append(rec)
                    out["then_injected_new"].append(self._read_back(rec))
            # ⚠ **放行→停住的墙钟秒数** = "这一停归谁"的**线索**(下条 ⚠ 说了它为什么不够格当凭据)。
            #   注入的那一次执行在放行后**微秒级**就走完这几条指令(要么落到 `watch`、要么绕过去),
            #   所以远晚于这个尺度才到的命中不可能是它。**阈值由调用方定**(本层只把秒数交出去),
            #   因为"多少算同一次执行"是子项语义, 不是本层能定的。
            # ⚠ 但**这个秒数不是凭据**(2026-09-11 实跑定案): 2-1 曾拿它当"归谁"的判据, 三次实跑
            #   一次都没判对 —— 因为 `t_go` 取在 `resume()` 之前, 而 `resume` 缺省要 settle 0.4s,
            #   于是读数恒 ≥0.4s(实跑 0.421 / 0.422 / 0.500 全是静默本身)。settle 已在上面关掉,
            #   但**要判"这一停归谁"仍不该靠墙钟**: 停核一趟要 1~3s, 管理芯钟是**软件走时** ⇒
            #   放行那一刻管理芯已经落后计量芯 1~3s(> 阈值) ⇒ **下一次自然比较可能紧跟其后就跟随**,
            #   两个尺度会重叠。真要判归属, 该像 2-1 改用的那样: **停在判定指令上就地读判定**
            #   (当前这次调用放行后必然立刻走到那里 ⇒ 第一停就是自己, 没有窗口可言)。
            out["stop_latency"] = (time.monotonic() - t_go) if hit is not None else None
            # **就地读判定**: 停在这一停上面的量(寄存器)与它**实际走向**(单步落点)。两者都必须在
            # `resume()` 之前读 —— 放行之后 PC 与寄存器就不是这一停的了。见 `watch_regs`/`step`。
            if hit is not None and (watch_regs or step or steps):
                if watch_regs:
                    out["regs"] = self.read_regs(watch_regs)
                if step:
                    out["step_pc"] = self.step_one_pc()
                # `steps=N`(2026-09-18 加): **有界**连续单步, 落点逐个收进 `out["step_trace"]`,
                # 上限 N 条; 读不到 PC(环境问题)就**提前收工**, trace 里留一个 None 当断点记号。
                # 为什么要有它: "停在这一行, 下一行却从没被命中"这种局面, 只单步一条答不出来 ——
                # 得看清 PC 是**回到下一行**还是**拐进别处**(卡在循环里 / 撞进异常向量)。
                # 出处: 2026-09-18 探针要问"`:3652` 到了而 `:3653` 从没到过, 那 PC 去哪儿了"。
                # ⚠ 本处**不挂任何观察点** —— 观察点走 MI `-break-watch -a`, 由 GDB Server 自己配
                #   DWT(硬件访问观察点, 命中即把核停住), 见 `swdbg.selfcheck` 第 3 关:
                #   这里步数由调用方给死, 走完就停。
                if steps:
                    out["step_trace"] = []
                    for _ in range(int(steps)):
                        _pc = self.step_one_pc()
                        out["step_trace"].append(_pc)
                        if _pc is None:
                            break
            if hit is not None:
                self.resume()                 # 收尾态必须"跑着"(见 _disarm 的 ⚠)
            elif own_w:
                self._disarm(bp_w)
            _settle()
            return out
        except Exception:
            _recover()
            raise

    def resume(self, settle=True):
        """从命中处放行(等价 go, 只是语义上是"接着跑")。`settle=False` 见 `go` 的 ⚠。"""
        return self.go(settle=settle)

    @property
    def stopped(self):
        return self._stopped

    # ---------------------------- 读(停住态才准) ----------------------------
    def _require_stopped(self, what):
        if not self._stopped:
            raise GdbError("%s 要求核心**停住**(局部量只在停住那一刻有意义)。先 wait_hit()。" % what)

    def read_vars(self, names):
        """读一组表达式(局部量或全局) → {name: 文本|None}。

        None = 读不到。**读不到通常不是"被优化掉了", 而是断点停在了该变量的空洞区间** ——
        用 `arm-none-eabi-gdb -batch -ex 'file "<out>"' -ex "info scope <函数>"` 离线先查,
        挪断点即可(见 CLAUDE.md 调试链纪律 3)。"""
        self._require_stopped("read_vars")
        out = {}
        for name in names:
            r = self._cmd('-data-evaluate-expression "%s"' % str(name).replace('"', '\\"'))
            if r and r.get("_class") == "^done":
                v = str(r.get("value", ""))
                out[name] = None if "optimized out" in v or "No symbol" in v else v
            else:
                out[name] = None
        return out

    def read_regs(self, names):
        """读一组**CPU 寄存器** → {名: int|None}。仅停住态可用; 取不到给 None(不抛)。

        为什么单独要有它(`read_vars` 读的是 DWARF 表达式, 寄存器不在其中): **就地读判定**要用 ——
        停在判定指令(如 `cmp r0,#2` 后面那条 `bcc`)上时, "比出来是多少"只写在**寄存器**里, 源码里
        没有对应的变量名可读(2-1 判据③, 2026-09-11 实跑)。

        ⚠ 两个**实测**的坑(首版探针都踩过):
          · gdb 回的 `value` 是**十进制**(`$r0` → `'16777273'`), 不是 `0x…` ⇒ 解析必须 `int(v, 0)`,
            拿"必须带 0x"的 `addr_tok` 去抠会**静默给 None**(看着像"这个寄存器读不到")。
          · `$cpsr` 在本 gdb 里是 `void`(没有这个伪寄存器) —— 别指望靠读标志位判分支, 要判分支请
            **单步一条看落点**(`with_inject(step=True)` 会替你做)。
        """
        self._require_stopped("read_regs")
        out = {}
        for name in names:
            r = self._cmd('-data-evaluate-expression "$%s"' % str(name).strip().lstrip("$"))
            v = str((r or {}).get("value") or "").strip() if (r or {}).get("_class") == "^done" else ""
            if not v or v == "void":
                out[name] = None
                continue
            try:
                out[name] = int(v, 0)
            except ValueError:
                out[name] = addr_tok(v)
        return out

    def step_one_pc(self, timeout=3.0):
        """**单步一条指令**, 返回落点的 PC(取不到给 None); 调用前核须停着, 返回时仍停着。

        落点 = 判定的**实际走向**(2-1: 落到 `fallthru` ⇔ 被条件保护的那句真被执行了)。
        ⚠ **三步都必须照做, 少一步就出错**(2026-09-11 实踩, 首版探针在这里死了两次):
          ① **先清 `_pending`**: 队列里可能躺着**陈旧**停产记录(来自 `ensure_stopped`/interrupt),
             不清的话读到的是**上一条记录的 addr** —— 而单步其实还在飞, 于是紧接着 `$pc` 报
             `Selected thread is running.`、`ensure_running()` 被带崩(表象与"没接 J-Link"一样)。
          ② PC **直接取自停产记录的 `frame.addr`**, 别去读 `$pc` —— 记录刚到手时 gdb 侧可能还没把
             它翻成"选中线程已停", `$pc` 会回 `^error`。
          ③ 单步只走一条 ⇒ 它**必然**会在微秒级停下来; 等不到就是环境问题, 给 None 让调用方记"没做成"。
        """
        self._pending = None                     # 见 ⚠ ①
        st = self._cmd("-exec-step-instruction", timeout=10.0)
        if not st or st.get("_class") != "^running":
            if st and st.get("_class") == "^error" and not self.quiet:
                print("   %s单步被拒: %s" % (runlog.MARK_DEGRADED, st.get("msg")))
            return None
        end = time.time() + timeout
        while True:
            left = end - time.time()
            if left <= 0:
                return None
            rec = self._pending or self._next(left)
            self._pending = None
            if rec is None:
                return None
            if rec.get("_class") == "*stopped":
                self._note_stopped(rec, "step_one_pc")
                return addr_tok((rec.get("frame") or {}).get("addr"))

    def locals_all(self):
        """当前帧所有局部量 → [(name, value|None)]。读不全没关系, 那是 DWARF 位置表说了算。"""
        self._require_stopped("locals_all")
        r = self._cmd("-stack-list-variables --simple-values")
        out = []
        for item in (r or {}).get("variables") or []:
            if isinstance(item, dict):
                name = item.get("name")
                out.append((name, item.get("value")))
        return out

    def frame_info(self):
        """当前停在哪 → {'file','line','addr','func'}。"""
        r = self._cmd("-stack-info-frame")
        return (r or {}).get("frame") or {}

    # ---------------------------- 受控注入 ----------------------------
    def inject(self, expr, value):
        """**受控**写一个变量 → 恢复句柄。仅停住态可用; expr 必须在 `inject_allow` 白名单里。

        为什么要有它: 5-2/9-2/9-3 这类判据的**触发源**就是"改过门槛"(规格原文「C-SPY 注入门槛」),
        桌测无标准源、瞬时量恒 0, 不注入就永远没有触发。写的是**判据变量**, 不是任意内存。

        安全边界: ①expr 须在白名单(默认空 = 整体关闭) ②不收裸地址 —— `0x...` 与 `*expr` 一律拒
        ③记 (旧值 → 新值) ④`close()`/restore 时**自动写回原值**。

        ⚠ **寄存器驻留的局部量**(`&expr` 取不到 —— 优化把标量放进了 `$rN`, 如 1-3 的
          `TaskDisplay.c:3171` 处的 `dot` 在 `$r4`): **不是"注入不了", 是"没有地址可写回"**。
          这时退回 `set var EXPR = VALUE`(gdb 知道怎么改那个寄存器), 旧值改从
          `-data-evaluate-expression` 取文本。**不登记收尾还原** —— 与栈上局部量同一条理由
          (`_stack_injected` 的 ⚠): 它只活在这一帧, 函数一返回寄存器就不是它的了; 而且根本没有
          "老地址"可写回。**别把这条当成"台面证不了"** —— 它一度是本函数自己的收窄, 不是仪器限制。
        白名单收**符号名**(变量、`IWDT->SERV` 之类外设寄存器符号都算), 拒**裸地址** —— 见模块头
        「写入能力」那节: 判定是"必须点名", 不是"不许写"。
        与 `with_inject()` 的分工: 本函数只负责"写一个值"; 需要**趁停住改、然后看某条语句被执行**时,
        用 `with_inject`(它管时机与撤点)。"""
        self._require_stopped("inject")
        expr = str(expr).strip()
        if re.search(r"0[xX][0-9a-fA-F]+", expr) or expr.startswith("*"):
            raise GdbError("inject 不收地址表达式(%r) —— 只收变量名, 见本模块「写入能力」" % expr)
        if expr not in self.inject_allow:
            raise GdbError("inject(%r) 不在白名单。要用得在 Session(..., inject_allow=[...]) 里点名登记。\n"
                           "  当前白名单: %s" % (expr, list(self.inject_allow) or "(空 —— 注入整体关闭)"))
        # 先取地址与长度(经 gdb 本体解析, 结构体成员/数组元素都能对上)
        ra = self._cmd('-data-evaluate-expression "(unsigned int)&(%s)"' % expr)
        if not ra or ra.get("_class") != "^done":
            msg = str((ra or {}).get("msg") or "")
            # ---- 寄存器驻留的标量: `&expr` 天然取不到地址 ⇒ 走 `set var`(见 `_inject_reg`) ----
            if "register" in msg.lower():
                return self._inject_reg(expr, value, msg)
            raise GdbError("inject: 解析不了 %s 的地址: %s" % (expr, msg))
        addr = int(str(ra.get("value")).strip(), 0)
        rl = self._cmd('-data-evaluate-expression "sizeof(%s)"' % expr)
        size = int(str((rl or {}).get("value", "4")).strip(), 0) if rl else 4
        # ⚠ **8B 标量必须真按 8B 写**(2026-09-11 修, 1-3 实踩): 原先这里是
        #   `if size not in (1,2,4): size = 4` —— 于是 `INT64U u64`(TaskDisplay.c:2962) 被
        #   默默截成 4B 写下去。表象极难查: 注入"成功"、旧值/地址都记得对, 只有**值**是错的
        #   (2e13 低 32 位), 而调用方看到的是"注了却没触发" ⇒ 会去怀疑断点/固件, 而不是这一行。
        #   静默截断 = 本仓最忌讳的那类错。**定宽标量到 8B 都支持**; 再宽/非标量按 4B 并**喊出来**。
        if size not in (1, 2, 4, 8):
            print("   %s%s 的 sizeof=%d 不在 (1,2,4,8) —— 按 4B 写, 调用方自担"
                  % (runlog.MARK_DEGRADED, expr, size))
            size = 4
        old = self._read_mem(addr, size)
        cast = {1: "unsigned char", 2: "unsigned short", 4: "unsigned int",
                8: "unsigned long long"}[size]
        r = self._cmd('-interpreter-exec console "set {%s}0x%08X = %s"' % (cast, addr, value))
        if r and r.get("_class") == "^error":
            raise GdbError("inject 写 %s 失败: %s" % (expr, r.get("msg")))
        self._injected.append((expr, addr, size, old))
        # ⚠ **栈上局部量不登记还原**(2026-09-11 加, 2-1 判据③ 是第一个注局部的用例)。
        #   局部量的地址只在**那一帧活着时**才有意义; 函数一返回, 同一地址就是**别的函数的栈槽**。
        #   收尾(`close()` → `_restore_injections_locked`)按老地址写回 = 去踩别人正在用的栈帧 ——
        #   **静默改坏无关变量**, 正是本仓最忌讳的那类错(它不会报错, 只会让别处的判据莫名其妙地飘)。
        #   而**不还原**恰恰是安全的: 那个槽接下来会被正常的函数调用覆盖, 留一个字节在里面
        #   与固件自己留在里面没有区别。
        #   判据: 注入那一刻 `addr >= $sp` ⇒ 落在栈区(全局/静态量都在 RAM 低地址, 恒 < $sp)。
        #   取不到 `$sp` 就退回旧行为(登记还原)—— 宁可多一次写回, 也不默默丢掉全局量的安全网。
        sp = self.read_vars(("$sp",)).get("$sp")
        on_stack = False
        try:
            if sp is not None:
                on_stack = addr >= int(str(sp).strip(), 0)
        except Exception:
            on_stack = False
        if on_stack:
            self._stack_injected.add(addr)
            print("   %s" % loglabel.debug_line(loglabel.DEBUG_GDB, "↑ 这是**栈上局部量**(0x%08X ≥ $sp=%s) ⇒ **不登记收尾还原** —— "
                  "函数一返回那里就是别人的栈槽, 按老地址写回会踩坏无关变量" % (addr, sp)))
        print("   %s" % loglabel.debug_line(
            loglabel.DEBUG_GDB, "注入 %-30s @0x%08X [%dB]  %s → %s"
            % (expr, addr, size, old.hex(" ").upper() if old else "?", value)))
        # ★ 事件流: **改过哪块内存、从什么改成什么**。`old` 是写之前读回来的那几字节(读不到就是
        #   None) —— "注了什么"与"注之前是什么"必须成对记下, 否则复盘时说不清那个值是本来就那样
        #   还是被我们改的。`on_stack` 一并记: 它决定收尾**还不还**(见上面那段 ⚠), 是这条记录的
        #   一部分(只有看事件的人知道"这个量不会被还原", 才不会把残留当成固件行为)。
        events.emit("inject", expr=expr, where="mem", addr="0x%08X" % addr, size=size,
                 old=old.hex(" ").upper() if old else None, new=str(value), on_stack=bool(on_stack))
        return (expr, addr, size, old)

    def _inject_reg(self, expr, value, why):
        """写**寄存器驻留**的局部量 —— `inject()` 在 `&expr` 取不到地址时的退路(见其 docstring ⚠)。

        为什么不该一律拒: "取不到地址"只是**还原手段**没了, 不是**写入能力**没了。判据要的常常
        正是这种量(1-3 的借位收尾: `Set_DispPara(dot, Borrow)` 会把 `Borrow` 写进 EEPROM, 要把
        它写回原值只能改 `dot`, 而 `dot` 恰在 `$r4`)。拒掉它 = 把**库自己的收窄**说成"台面不可证"。
        返回形状与 `inject()` 一致, 只是 `addr=None`/`size=0` —— 调用方按 `_inj_line` 的 `@(寄存器)` 认。
        """
        ro = self._cmd('-data-evaluate-expression "%s"' % expr)
        old = str((ro or {}).get("value", "?")).strip().encode("utf-8")
        w = self._cmd('-interpreter-exec console "set var %s = %s"' % (expr, value))
        if w and w.get("_class") == "^error":
            raise GdbError("inject: `set var %s = %s` 失败: %s" % (expr, value, w.get("msg")))
        # ⚠ **不登记 `_injected`** = 收尾不写回。与 `_stack_injected` 同一条理由(见那里的 ⚠):
        #   它的存储只在**这一帧**活着时才是它的, 按"老地址"写回既不可能(没地址)也无意义。
        print("   %s" % loglabel.debug_line(
            loglabel.DEBUG_GDB, "注入 %-30s @(寄存器: %s)  %s → %s   [不登记收尾还原 —— 无地址可写回]"
            % (expr, why.split('"')[1] if '"' in why else "?", old.decode("utf-8", "replace"), value)))
        # ★ 事件流: 走 `set var` 的**寄存器驻留**那一支 —— `addr=None`(没有地址可写回, 收尾也不
        #   还原)。`where="reg"` 让读方一眼看出这次注入与上面那种"改内存"不是一回事。
        events.emit("inject", expr=expr, where="reg", addr=None, size=0,
                 old=old.decode("utf-8", "replace"), new=str(value), on_stack=None)
        return (expr, None, 0, old)

    def _read_mem(self, addr, size):
        r = self._cmd('-data-read-memory-bytes 0x%08X %d' % (addr, size))
        if not r or r.get("_class") != "^done":
            return None
        mem = (r.get("memory") or [{}])[0]
        try:
            return bytes(bytearray.fromhex(str(mem.get("contents", "")).replace(" ", "")))
        except Exception:
            return None

    def _read_back(self, rec):
        """注入写完之后**读回来**的那几字节 —— 纯记账, 读不到就记 `None`, **绝不抛**。

        ⚠ 2026-09-14 实踩(那一轮离线自检从这个函数**半途崩**): 写回读走的是 `_read_mem` ⇒ `_cmd`,
          而 `_cmd` 超时会**抛** `GdbError`; 当时那种会话没有 `-data-read-memory-bytes` 这一路,
          于是那一轮从注入那一行崩掉(崩之前的 OK 全不算数)。**病根比"夹具缺一笔"深**: 这条读数
          是 `_inj_line` 的**旁证**(为了让"注入生效"与"恰好写在原值上"在账本里长得不一样), 拿它去
          抛, 就是让一条**旁证**把一次**已经写成功**的注入判成异常 —— 顺手把证据弄丢, 正是本仓最忌的
          那类静默错误。真跑里同一件事也会发生(核心被前面的停核拖慢、gdb 忙)。
          故本函数只做一件事: **读不到 ⇒ `None`**。`_inj_line` 收到 `None` 会退回"只记表达式文本"
          (旧口径), 复核的人看得见"这次没读回来", 而不是整条消失。

        `rec[1] is None` = **寄存器驻留**(见 `_inject_reg`) ⇒ 本就没有地址可读回, 同记 `None`。
        """
        if rec[1] is None:
            return None
        try:
            return self._read_mem(rec[1], rec[2])
        except Exception:
            return None

    def _restore_injections_locked(self):
        """把注入过的变量写回原值(从后往前, 同址多次注入也能对)。

        ⚠ 2026-09-11 实踩(**3-1 首跑, 真表**): 本函数原先张口就发 `set {…}0xADDR = …`, 而它
        是 `close()` 的第一步 —— `close()` 又几乎总在**核心跑着**的时候被调(测试收尾的常态)。
        gdb 在 mi-async 下**拒绝在目标运行中写内存**:
        `^error,msg="Cannot execute this command while the selected thread is running."`
        ⇒ **注入值一个都没写回**, 只在日志里留一行孤零零的 `(失败!)`(连原因都没打)。
        注入那一刻之所以成功, 纯粹因为那一刻正好是停住态(`with_inject` 的本职)——
        **"只在停住态成立的操作"必须自己把核心叫停, 不能指望调用点的时机**
        (与 `ensure_stopped` 的 docstring 是同一条教训, 那边踩的是 `-break-insert`)。
        ⚠ 叫停之后**必须放行**: 把核心留在 halt 就是 CLAUDE.md 那条"调试完核心没放行 ⇒
        整表串口全无应答"。故整体 try/finally 收在 `ensure_running()`。
        """
        if not self._injected:
            return
        self._drain_async()                      # ⚠ 别凭陈旧的 `_stopped` 猜(见 _drain_async)
        was_running = not self._stopped
        if was_running:
            try:
                self.ensure_stopped()
            except Exception as exc:
                print("   %s" % loglabel.debug_line(loglabel.DEBUG_GDB, "还原注入前叫不停核心(%s) ⇒ 放弃还原, 注入值留在 RAM" % exc))
                return
        try:
            while self._injected:
                expr, addr, size, old = self._injected.pop()
                if not old:
                    continue
                if addr in self._stack_injected:
                    # 栈上局部量(见 `inject()` 里那段): **有意跳过**, 而且要**说出来** ——
                    # 静默跳过与静默写坏一样坏, 这里让人一眼看得见"谁没被还原、为什么"。
                    print("   %s" % loglabel.debug_line(loglabel.DEBUG_GDB, "跳过还原 %-26s @0x%08X —— 栈上局部量, 那一帧早没了"
                          "(写回会踩别的函数的栈槽)" % (expr, addr)))
                    continue
                cast = {1: "char", 2: "short", 4: "int"}[size]
                val = "0x" + old[::-1].hex().upper() if size > 1 else "0x%02X" % old[0]
                try:
                    r = self._cmd('-interpreter-exec console "set {unsigned %s}0x%08X = %s"'
                                  % (cast, addr, val))
                    bad = bool(r) and r.get("_class") == "^error"
                    print("   %s" % loglabel.debug_line(loglabel.DEBUG_GDB, "还原 %-30s @0x%08X ← %s%s"
                          % (expr, addr, val,
                             " (失败! %s)" % (r or {}).get("msg") if bad else "")))
                except Exception as exc:
                    print("   %s" % loglabel.debug_line(loglabel.DEBUG_GDB, "还原 %-30s @0x%08X ← %s (异常: %s)" % (expr, addr, val, exc)))
        finally:
            if was_running:
                try:
                    self.ensure_running()
                except Exception as exc:
                    print("   %s还原注入后没能放行核心(%s) —— 收尾请跑 python -m swdbg.restore"
                          % (runlog.MARK_DEGRADED, exc))

    def restore_injections(self):
        self._restore_injections_locked()

    # ---------------------------- 诊断 ----------------------------
    def dog_feeds(self):
        """python 侧喂狗次数(仅记账; gdb 侧 hook-stop 另有喂, 不在此计数)。"""
        return self._dog_feeds

    def describe(self):
        # ⚠ 横幅里带上**探针是哪一支**(2026-09-20): 换探针之后"这一场到底走的哪条路"必须一眼
        #   看得见 —— 两支探针的 server 是两个不同的程序, 只报 out/port 的话, 复盘时分不出
        #   这场会话底下是 J-Link 还是 CMSIS-DAP。
        return ("breakpoint: out=%s port=%d 探针=%s(%s) 断点=%d 看门狗=%s 注入白名单=%s"
                % (os.path.basename(self.out), self.port,
                   getattr(self, "backend", "?"), getattr(self, "probe_ident", "?"),
                   len(self._bps),
                   "0x%08X" % self.watchdog[0] if self.watchdog else "无",
                   list(self.inject_allow) or "关"))

def session(out=None, watchdog=None, inject_allow=(), **kw):
    """`Session(...)` 的函数式糖(与 `probe.Probe()` / `snapshot` 的用法并列)。"""
    return Session(out=out, watchdog=watchdog, inject_allow=inject_allow, **kw)

def open_or_none(out=None, watchdog=None, inject_allow=(), source=loglabel.DEBUG_GDB, **kw):
    """尽力开一个调试会话: 成了 → Session; 开不了(没接 J-Link / 口被占) → None, **并大声说明原因**。

    为什么要它: 同一份操作清单应当能在"接着 J-Link / 没接 J-Link"两种台面上都跑(见 `trigger`)。
    但降级**必须说出声** —— 否则断点取证判 TBD 时, 人分不清是"这台没接 J-Link"还是"接了、但这次没命中",
    这两种 TBD 的含义完全不同。

    `source=` 是调试行**来源格**里的那个词(词表见 `loglabel.DEBUG_SOURCES`), 不是自由标签 ——
    本函数打出的每一行都带它。2026-09-18 并字形时由 `tag=` 改名而来: `tag=` 在本仓别处意思是
    "帧 id / 自由串"(见 `loglabel` 开头那段), 一个参数扛两种意思正是这次要拆掉的东西;
    改名之前三个探针脚本传的是 `tag="probe"` —— 那是**第四个来源**, 不在词表里, 也没人登记过。
    """
    # ⚠ 先验来源, 且在 `try` **外面**: 传错了是**代码写错**, 不该被下面那个 `except` 吞成
    #   "打不开调试会话 ⇒ 判 TBD" —— 那会把人往"这台没接 J-Link"上引, 查半天查不到。
    loglabel.debug_line(source, "")          # 只起校验作用, 结果丢掉
    try:
        s = Session(out=out, watchdog=watchdog, inject_allow=inject_allow, **kw)
        s.open()
        # ⚠ 字形**不在这里拼**: 原先此处手写 `"   [%s] %s" % (tag, …)`, 与 `_trace_emit`/`_warn`/
        #   `probe._trace` 并列, 是同一个维度上第 N 条各拼各的通路 —— 横幅记的是"这一场调试会话
        #   是什么样", 和 gdb 说的每一句话同属一路, 就该走同一个落笔处。
        print("   %s" % loglabel.debug_line(source, s.describe()))
        return s
    except Exception as exc:
        # ★ 这两句是**降级**, 得让 `runlog` 数得到 —— `scan()` 认的是前缀逐字等于 `MARK_DEGRADED`,
        #   所以 `!! ` 连在来源格之后(当 source=gdb 时与 MARK_DEGRADED 逐字节相同)。
        #   原先手写 `"   [%s] !! …" % tag` ⇒ 默认 "gdb" 时碰巧对得上, 传别的 tag 就**静默漏账**。
        # 这一笔是**结论**: 本轮的断点观测没了。它与上面 `_start_server` 那笔根因各说一半
        # (那笔说"server 为什么起不来", 这笔说"所以这一轮白盒没了"), 复盘时先看这笔。
        faultlog.record("GDB-SESSION", subsystem="gdb", exc=exc,
                   tried=["open_or_none 里开一次完整会话"],
                   snapshot={"source": source},
                   next_step="python -m swdbg.restore, 再跑一次")
        print("   %s" % loglabel.debug_line(source, "!! 打不开调试会话 —— 本轮只跑串口观测, 断点取证判 TBD"))
        print("   %s" % loglabel.debug_line(source, "!! 原因: %s" % (
            str(exc).replace("\n", "\n   " + loglabel.debug_line(source, "   ")))))
        return None

def _ev_hit(tag, verdict, r):
    """把一次断点取证记成 `bp.hit` 事件(2026-09-17 加)。

    **为什么收成一个函数**: `report()` 有三个出口(FAIL/TBD/PASS), 三处各写一遍 payload 的话,
    迟早有一处漏掉某个字段 —— 而**漏字段不报错**, 只表现为"这条事件里没有那个量", 于是"读到的值
    是空"与"没去读"又分不开(本仓反复治理的那类)。组 payload 只在这里一处。

    ⚠ `where` 是 `Hit.where()`(文件:行 @地址) —— 与给人读的取证行**同一个函数算的**, 不另拼一份;
      这正是用户最初要的"调试器停在哪", 只是这次是可判的。
    `vars` 取不到就是 `None`(**不补 `{}`**): `{}` 会被读成"停了但一个量都没读到",
    而 `None` 才是"这一次没有值这一栏"(没命中/没会话)。

    ⚠ **配对号要显式带过去**(2026-09-18): 本函数在**窗口之外**跑(`fire_hit` 收工后才 `report`),
    那时环境里已经没有号了 ⇒ 不带的话 `bp.hit` 与它那张帧/那一停对不上, 而**读的人只会以为
    这一次本来就没配对**。故 `with_trigger` 把号放进返回记录里, 这里原样转交。
    只在**有号时**才传 `pair=`: 传 `pair=None` 会写出一个 `"pair":null` 的字段, 那是"有号、号是空",
    与"这一条在窗口外"是两回事(同 `vars` 那条的道理)。
    """
    hit = r.get("hit")
    _kw = {}
    if r.get("pair") is not None:
        _kw["pair"] = r["pair"]
    events.emit("bp.hit", tag=tag, verdict=verdict,
             bkptno=None if hit is None else hit.bkptno,
             reason=None if hit is None else hit.reason,
             where=None if hit is None else hit.where(),
             vars=r.get("vars") or None,
             error=None if r.get("error") is None else str(r["error"]), **_kw)

def report(tag, r):
    """把 `trigger()`/`with_trigger()` 的返回打成**一行标准取证**, 返回 verdict 字符串。

    PASS = 命中期望断点(那一次确实打进了这个文件:行); TBD = 没命中/没有会话 —— **不是 FAIL**,
    "没停"既可能是"这一次本就不该触发", 也可能是"台面没接 J-Link", 得由调用方结合台面判
    (硬性规矩 5: 串口不足判定就是 TBD, 不许拔高成 PASS, 也不许冤枉成 FAIL)。"""
    hit, vals = r.get("hit"), r.get("vars") or {}
    if r.get("error") is not None:
        print("== 断点取证 %s: FAIL(触发动作自己报错: %s)" % (tag, r["error"]))
        _ev_hit(tag, "FAIL", r)
        return "FAIL"
    if hit is None:
        extra = "" if not r.get("trigger_alive") else "(触发线程仍在跑)"
        print("== 断点取证 %s: TBD(没命中该断点)%s" % (tag, extra))
        _ev_hit(tag, "TBD", r)
        return "TBD"
    body = "  ".join("%s=%s" % (k, v if v is not None else "(该行读不到)") for k, v in vals.items())
    print("== 断点取证 %s: PASS  停在 %s bkpt=%s%s"
          % (tag, hit.where(), hit.bkptno, ("  |  " + body) if body else ""))
    _ev_hit(tag, "PASS", r)
    return "PASS"

def record(tag, r, ok=None, crit=None, falsify=None, detail=None, trig=None):
    """`report()` 的**记录版** —— 打同一行取证, 但返回 `common.judge.rec` 形状的记录(交 `CB.Judge` 汇总)。

    断点观测递证据的**统一入口**: 白盒证据长什么样只有一处定义(common/judge.py), 判定也只有一处实现。
    本包 import `common` 是允许的(方向 `swdbg → common`), 不碰 meterlib。

    `ok` 缺省口径(想override就显式传 —— **负向判据**必须显式传, 见下):
      · 触发动作自己报错 → **False**(这一次根本没打成; 有意选"大声", 别让它悄悄出溜过去)
      · 没命中          → **None**(没做成 —— 与"固件不对"分开, 别冤枉它)
      · 命中            → **True**
    ⚠ **期望"不命中"的判据**(负向判据, 例: 未到点不该执行切换体)**不能吃这个缺省** ——
      对它来说"没命中"恰恰是达成。那种调用方自己算 `ok` 传进来(`ok=hit is None`),
      并且**要把触发出错/线程没落定算成 None**, 否则一个"永不命中的断点"会永远绿。
    """
    verdict = report(tag, r)
    hit = r.get("hit")
    if ok is None:
        ok = False if verdict == "FAIL" else (True if hit is not None else None)
    if detail is None:
        detail = ("停在 %s" % hit.where()) if hit is not None else verdict
    return judge.rec(tag, ok, detail, crit=crit, obs=judge.DEBUG, falsify=falsify, trig=trig)

def trigger(sess, bpno, fn, *args, **kw):
    """`with_trigger` 的**可缺省**版本: 没有会话(`sess is None`, 台面上没接 J-Link)就直呼 `fn`。

    为什么要有它: 一份操作清单应当**在"有/没有 J-Link"两种台面上都能跑**, 而不是让每个脚本
    自己写 `if g: ... else: ...` 两种观测(那样两种观测迟早分叉)。差别只有一条 —— 断点那一次的
    证据拿不到, 返回里 `hit=None`(调用方据此打 TBD), 串口观测一字不动。

    返回形状与 `Session.with_trigger` 相同(无会话时 hit=None / vars={})。
    """
    if sess is None:
        # 控制参数 **不是给 fn 的** —— 必须全摘掉。两条都要:
        # ① 具名的 `timeout`(它没有 `_` 前缀, 光靠下面那道前缀扫是扫不掉的);
        # ② 所有 `_` 前缀的(`_vars`/`_join`/`_drop`), 按前缀统一摘 ⇒ 以后再加 `_xxx` 不会漏。
        # ⚠ 2026-09-10 实踩: 原先只逐个 pop `_vars`/`_join`, 漏了 `_drop`, 于是台面没接 J-Link
        # (`--no-gdb`)时 `_drop=True` 漏进 `write_billday(ser, d0, _drop=True)` → TypeError 被
        # 下面的 except 吞进 `error` 字段 → **写回结算日那一次静默失败**, 表被留在 alt 号上;
        # 而 `scripts/_restore_all.py` 又写明"不动结算日(脚本会自恢复)", 于是没人修它。
        kw.pop("timeout", None)
        _pn = kw.pop("_pair_name", None)        # 配对名字(同样不是给 fn 的), 见下
        for _k in [k for k in kw if k.startswith("_")]:
            kw.pop(_k)
        # ⚠ **没会话这一路也要开配对窗口**: 台面上没接 J-Link 时白盒那半程不做, 但**帧照样发**
        #   (见本函数开头"两种台面都能跑"), 于是这一段的 `serial.tx/rx` 更需要一个配对号 ——
        #   否则它成了整份事件流里唯一没有归属的那几帧, 而"是哪次触发的"恰恰只能靠这个号答。
        with events.pairing(_pn or "触发(无会话) ← %s" % getattr(fn, "__name__", "?")) as _pw:
            try:
                return {"hit": None, "vars": {}, "result": fn(*args, **kw),
                        "error": None, "trigger_alive": False, "pair": _pw.no}
            except Exception as exc:
                return {"hit": None, "vars": {}, "result": None,
                        "error": exc, "trigger_alive": False, "pair": _pw.no}
    return sess.with_trigger(bpno, fn, *args, **kw)

def _hit_rec(label, res, crit, falsify):
    """把"一次"的结果包成证据记录, 并把**停住时读到的量原样附在记录上**(`rec["vars"]`)。

    为什么附在记录上、而不是另开一个返回值: 断点观测的两半(`wait_hit` 等一次 / `fire_hit` 触发一次)
    必须返回**同形**的东西, 脚本才能一行一次地写; 而"这一次读到了什么"本就是这条记录的一部分。
    子项判据(如 5-4 的 `clear_partition`)要拿它去比 —— 那一步不该由脚本反解 `trigger` 的内部
    返回结构(`r["vars"]["id"]`), 那是解析, 属库: 库哪天改了 key 名, 脚本里那行会**静默**取到 None,
    判据跟着静默降级成"未定论", 不报错、不喊。

    取不到 = `{}`(没命中/没会话), **不抛** —— 与两种观测同律: 缺证据记「没做成」, 不是异常。

    `rec["result"]` 是**触发函数自己的返回值**(2026-09-17 加), 取不到 = `None`。同一个理由:
    触发动作的**对外结果**(例: `ctrl_relay_reply` 回的 `(操作字, 应答命令, 应答数据域, 帧)`)是
    "这一次发生了什么"的另一半, 而 `fire_hit` 的调用方只能拿到记录 —— 不附上, 判据里那条
    "应答是受理还是被拒"就取不到, 只能**再发一次同样的帧**去要(16-2 当时就是这么绕的)。
    ⚠ 附的是**原样对象**, 不在这里解析; `trigger` 里抛过异常的这一次 `result` 就是 `None`
      (`error` 那一栏不进记录 —— `record` 已按 verdict 把它记成"没打成")。
    """
    r = record(label, res, crit=crit, falsify=falsify)
    r["vars"] = dict((res or {}).get("vars") or {})
    r["result"] = (res or {}).get("result")
    return r

def break_at_or_none(sess, srcfile, line):
    """下断点, **没会话就回 `None`** —— `sess.break_at` 的可缺省版。

    供 `fire_hit` 那种"`bp` 可以先算好、也可以直接给 `(文件,行号)`"的用法: 脚本写
        bpC = GD.break_at_or_none(g, *BP_C)     # 没接 J-Link 时就是 None
    再把 `bpC` 原样传给 `fire_hit` —— 它见到 None 会照发帧、不取证(与没会话同一条律),
    于是脚本里不必再写 `g.break_at(...) if g is not None else None` 那句守卫
    (那守卫散在 3-2/4-6/5-4 里共四处, 且**判空逻辑本来就该跟 break_at 住一起**)。
    """
    return None if sess is None else sess.break_at(srcfile, line)

def to_bpno(sess, at):
    """把脚本里给的**断点写法**统一解成一个 bp 号 → `(bpno, own)`。

    `own=True` = "这是本次自己挂的, 用完必撤"; 撤不掉的后果见 `Session._disarm` 的 ⚠。
    ⚠ 第二个元素是**开关**, 不是断点号 —— 补撤要写 `_disarm(bpno)`。写 `_disarm(own_bp)` 是把
      `True` 当号查(`_disarm` 查不到就静默返回 False, 一个字都不报), 于是"超时补撤"整条路失效,
      断点留在槽里自己命中把核撂停。实踩: 4-7 转换后用 `expect_no_hit` 的两个断点都不撤, 后面
      `inject_hit` 的 watch 断点与它撞在同一个地址上, 真命中被判成"掠过非目标断点" ⇒ 判据③未证。

    收这几种(`at` 是脚本里写的**字面量**, 不是解出来的地址):

        ("文件", 行号)                 行锚点 —— 该行的**起始地址**(由地图的行表查, 见 `break_at`)
        ("call", 函数, 被调, n)        ELF 锚点 —— 该函数里第 n 处 `call 被调` 之后那条指令
        ("prev", 函数, 被调, n)        ELF 锚点 —— 同一处调用**之前**那条指令(要读这次调用的实参)
        ("func", 函数名)               ELF 锚点 —— 函数入口
        {"loc":…, "addr":…}            已解出的锚点(`inject_anchor` / `gdbinit.map_of` 的产物)
        {"srcfile":…, "line":…}        行锚点的另一种写法
        "<bpno>"                       已挂好的断点号(`break_at_or_none` 给的) —— `own=False`

    ⚠ **三种字面量写法都归 `break_at_anchor`**(它 `normalize` 之后一律查地图), 不走 gdb 自己解 ——
      所以哪一个都要求会话上已建地图。

    为什么收敛到这一个口: 这件事原先在 `fire_hit` / `wait_hit` / `expect_no_hit` / `with_trigger` /
    `inject_hold` 里**各写了一遍**, 而其中 `wait_hit` 那一份一直是死的(见它 docstring 的 ⚠) ——
    同族五兄弟里漏一个, 漏掉的那条路不报错, 只静默地什么都不做。再加一种锚点写法就要再改五处,
    必然再漏一次; 所以只留这一处, 别处调它。
    """
    if isinstance(at, dict):
        if "loc" in at and "addr" in at:
            return sess.break_at_anchor(at), True
        f = at.get("srcfile") or at.get("file")
        if f is None:
            raise GdbError("认不出的断点写法: %r\n"
                           "  锚点要带 loc/addr, 行锚点要带 srcfile+line" % (at,))
        return sess.break_at(f, at.get("line")), True
    if isinstance(at, (tuple, list)):
        return sess.break_at_anchor(tuple(at)), True      # 归一 + 查地图, 四种写法同一处
    return at, False

def fire_hit(sess, bp, fn, *args, label="断点", timeout=30.0, vars=(), drop=False,
             crit=None, falsify=None, join=5.0, **kw):
    """`wait_hit` 的**对偶**: 有可发的触发帧那一半 —— 下断点 → 发帧 → 等命中 → 读量 → 包成证据记录。

    为什么要它: `wait_hit` 立起来之后, "断点观测递证据"只剩这一个半边有单点入口, 于是
    `record(标签, trigger(...))` 这一对被**逐处复制**(4-6 三处、5-4 一处)—— 与当初复制
    `record(标签, wait_only(...))` 是同一个毛病, 只是换了个半边。两半各一行一次, 形状才对称。

    ⚠ **没会话(`sess is None`)时这一次照样发出去**(走 `trigger(None, …)` 直呼 `fn`, 控制参数摘净),
    只是没有白盒记录, 返回 **None** —— 与 `wait_hit` 同律(调用方据此不做白盒那一半)。
    **"照发"是承重的**: 若这里也返回 None 就完事, 台面没接 J-Link 时**串口观测会整段消失**
    (5-4 那两次就是它唯一的清零触发源), 黑盒结论跟着没了 —— 降级只能降白盒, 不能降黑盒。

    `bp` 有两种写法:
      · `(源码文件, 行号)` —— 本函数自己下断点。**这种是"一次一挂", 命中/没命中都一定撤掉**
        (与 `drop=` 无关, 见下面那段 ⚠)。
      · 一个**已有的 bpno**(`break_at` 的返回, 多为 `break_at_or_none` 给的)—— **复用不重下**,
        撤不撤由 `drop=` 声明(默认 False = 留着给下一次)。
        4-6 的链B 就是两次打同一个断点: 重下一次会在同一个地址上再插一个硬件槽(Cortex-M0 只有 4 个),
        第二次 `_drop=True` 也只撤掉后插的那个, 前一个漏在槽里。

    ⚠ **`drop=` 只管"调用方自己的 bpno"** —— 传 `(文件,行号)` 时本次自己挂的那个, **两条路都撤**。
       2026-09-11 实踩(修前): 命中支靠 `with_trigger` 的 tuple 分支去撤, 而本函数在调 `trigger` 前
       就把 tuple 解成了 bpno ⇒ 那边走不到 tuple 分支 ⇒ `_drop` 停在默认 False ⇒ **命中后不撤**。
       于是断点留在 `TaskTime.c:1087` 这种约 1 Hz 的高频行上, **它自己命中把核撂停**, 后面串口全哑。
    `args`/`kw` 是给 `fn` 的(**别在这里具名收掉**): `trigger` 会把 `timeout` 与所有 `_` 前缀的
    摘干净再转交, 其余原样给 `fn` —— `fire_hit` 只加"下断点 + 包记录", 不改变这条转交规则。
    ⚠ 2026-09-10 实踩: 这一条起初漏了, 于是 4-6 断[B] 那一次写着的 `billday=d0`(`settle_across_master`
    的**真参数**)在 `fire_hit` 签名上撞成 `TypeError` —— 而它是**调用期**就抛, 比"静默漏参"好,
    但也只有真跑才看得见(离线 ast 解析全绿)。
    """
    if sess is None:
        trigger(None, None, fn, *args, timeout=timeout, _vars=tuple(vars), _drop=drop, _join=join,
                _pair_name=label, **kw)
        return None
    try:
        # ⚠ `break_at` / `break_at_anchor` **下不上就抛**, 从不返回 None(见它们的 ⚠)—— 所以只能 try
        bpno, own = to_bpno(sess, bp)    # `own` = 本次自己挂的, 用完必撤
    except GdbError as exc:
        # 下不上断点 = 白盒这一半做不成 —— 但**这一次照样发**(理由见上面"照发是承重的"那段):
        # 直呼 fn, 返回 None。⚠ 必须**大声**打印: 静默吞掉的话, "白盒观测没做成"会伪装成
        # "白盒观测做了、只是没命中"(两者在汇总里都长成 ok=None), 而前者要修的是断点/环境。
        print("   !! fire_hit: 断点 %s 没下上(%s) → 白盒这一次不做, 触发帧照发"
              % (_anchor_txt(bp), exc))
        trigger(None, None, fn, *args, timeout=timeout, _vars=tuple(vars), _drop=drop, _join=join,
                _pair_name=label, **kw)
        return None
    res = trigger(sess, bpno, fn, *args, timeout=timeout, _vars=tuple(vars),
                  _drop=(drop or own), _join=join, _pair_name=label, **kw)
    # ⚠ **本次自己挂的断点, 命中与没命中都得撤掉**。撤不掉的后果见 `Session._disarm` 的 ⚠:
    #   它留在槽里、核跑着, **它自己会命中** —— 撞上高频行(如 `TaskTime.c:1087` 受 SPI 时间对象驱动、
    #   约 1 Hz)就是核被撂停、后面每条 645/698 帧全 RX(0), 表象与"表死机"一模一样。
    #   **2026-09-11 实踩**: 这里原先只管**没命中**那一支(`isinstance(bp, tuple)` 只出现在下面那个
    #   if 里), 而命中支靠 `with_trigger` 的 tuple 分支去撤 —— 可 `fire_hit` 在调 `trigger` **之前**
    #   就把 tuple 解成了 bpno(见上面 `bpno = sess.break_at(*bp)`), 于是那边**永远走不到 tuple 分支**,
    #   `_drop` 停在默认 False ⇒ **命中后断点不撤**。2-1 首跑就栽在这儿: ②b 命中留下 bp1 在 `:1087`,
    #   ③ 中途核撞上它停住, 串口全哑, ③a 直接没法测(记"未证")。
    #   ⇒ 自己挂的就得自己撤, 且两条路都要 —— 判据是 **own**, 不是"有没有命中"。
    #   (给的是**已有 bpno** 时不撤: 那是调用方明确要跨次复用的, 撤不撤由它自己声明 `drop=`。)
    if res.get("hit") is None and own:
        sess._disarm(bpno)               # 没命中 ⇒ 还在槽里, 补撤(命中那支已由 _drop=True 撤掉)
    return _hit_rec(label, res, crit, falsify)

def wait_hit(sess, bpno, timeout, vars=(), label="断点", crit=None, falsify=None, drop=True):
    """等一个**自然到达**的断点(没有可发的触发帧)并包成证据记录 —— `trigger` 的对偶。

    `trigger` 管的是"有触发动作"那一半(`fn` 一次发出去, 断点该命中); 另一半断点是**没有帧可发**的
    —— 典型如"分钟步进汇合点"(`TaskFreeze.c:119`/`TaskRate.c:82`), 只能**静等**它自己到。
    这两半合起来才是"断点观测递证据"的全部, 可它的写法原先(`record(标签, sess.wait_only(...), …)`)
    被**复制在每个脚本**里 —— 于是"断点 → 判据记录"这件事在两个地方各写一遍, 迟早分叉。

    `label`/`crit`/`falsify` 由**调用方(子项脚本)**给: 它们描述的是"这是哪个断点、什么固件会让它
    不命中", 属**子项数据**(经验总结 §18 的判据), 本就该在脚本里 —— 本函数只管"**怎么等、怎么包**",
    不管"这是哪一条"。故它不替调用方决定判据文字, 只把这一次收干净:
    `drop=True` 趁停住撤断点(撤断点的唯一可靠时机, 见 `clear_breaks` 的⚠; 也免它插进后面的串口轮询再停一次)。

    `bpno` 两种写法与 `fire_hit`/`expect_no_hit` **同**: `(源文件, 行号)`(本函数自己挂、**用完必撤**)
    或已有 bpno(调用方挂的, 撤不撤由 `drop` 定)。

    ⚠ **2026-09-18 补上元组这一路 —— 它此前一直是死的**: 这一支原先把 `bpno` **原样**交给
      `wait_only` → `wait_break`, 而后者是 `want = str(bpno)` 再与 `str(hit.bkptno)` 比
      (本文件:1478/1499)。传一个元组进去, `want` 就成了 `"('TaskMetering.c', 1882)"`,
      **永远不会等于任何一个断点号** ⇒ 每一停都被当"掠过非目标断点"放行, 直到超时返回 `None`。
      现象是"这个断点从没命中过", 而真相是"它压根没被挂上" —— 两者在日志里长得一模一样。
      实踩点: `cmd_bank._meas_event_roundtrip` 的 pre-flight(`:6711`)正是拿 `wb["hit"]`
      (脚本给的是 `partial(GD.wait_hit, g)`)去等 `bp_judge` 这个**元组** ⇒ 9-2/9-3 两次跑的
      pre-flight 都报「没命中该断点」, 于是 `_branch_off` 恒 False、"本台证不了"那一层**从来不写**。
      `fire_hit:2714` 与 `expect_no_hit:2790` 两处**都**做了这个转换, 只有这里漏了 —— 同族三兄弟
      里漏一个, 漏掉的那条路不会报错, 只会静默地什么都不做。
    """
    if sess is None:
        return None
    own_bp = None
    if isinstance(bpno, (tuple, list, dict)):
        bp = bpno
        try:
            bpno, own_bp = to_bpno(sess, bp)
        except GdbError as exc:
            # 下不上 = 白盒这一半做不成。**不许记 FAIL** —— 那会把"断点错了/环境不对"冤枉成固件问题
            # (与 `expect_no_hit` 同一条)。
            print("   !! wait_hit: 断点 %s 没下上(%s) → 白盒这一次不做"
                  % (_anchor_txt(bp), exc))
            return record(label, {"hit": None, "vars": {}, "error": exc}, ok=None, crit=crit,
                          falsify=falsify, detail="断点没下上: %s" % exc)
    res = sess.wait_only(bpno, timeout, vars=vars, drop=(drop or own_bp is not None))
    # ⚠ 本次自己挂的、**又没命中** ⇒ 超时那一路不会撤(`wait_break` 的 `drop` 只在命中支用),
    #   它留在槽里、核跑着, 自己就会命中把核撂停(见 `_disarm` 的 ⚠)。补撤。
    if own_bp is not None and res.get("hit") is None:
        sess._disarm(bpno)
    return _hit_rec(label, res, crit, falsify)

def expect_no_hit(sess, bp, window, label="断点(否定期望)", crit=None, falsify=None,
                  trigger=None, trigger_args=(), vars=(), join=5.0):
    """**否定期望**的一次: `window` 秒内该断点**不该**命中 —— 命中判 FAIL, 超时判达成。

    为什么不并进 `wait_hit`: 两者的**超时含义相反**。`wait_hit` 超时是"没做成"(ok=None, 不冤枉固件),
    这里超时**正是要证的那件事**。合成一个入口, "没命中"在两种期望下会记成同一个东西 ——
    而它们一个该判 FAIL、一个该判达成。

    为什么非有不可(2-1 判据③): 「|差| ≤ 1s 时**不跟随**」断的是一条**没发生**的事
    (`TaskTime.c:1087 Set_MeterTime(objtime)` 没被执行)。这种判据**读值读不出来** ——
    停在 `:1085` 读 `objtime`/`g_MeterTime` 自算 |差| ≤ 1, 一个"无条件跟随"的坏固件读数一模一样,
    答不出 falsify ⇒ 不算证据。只有"那条指令路径没被走到"才是硬证, 也只有本原语给得出。

    `bp` 两种写法与 `fire_hit` 同: `(源文件, 行号)`(本函数自己挂、**用完必撤**)或已有 bpno。
    `trigger` 可选(要造状态就传, 与 `with_trigger` 同理走后台线程 —— 它是阻塞式串口动作)。
    `trigger_args` 是给 `trigger` 的**位置参数元组**(不是 `*args`: 这里还要留 `vars`/`join`)。
    没会话 → **触发照发、返回 None** —— 与 `fire_hit` 同律(**降级只能降白盒, 不能降黑盒**):
    `trigger` 是一条**该发的帧**, 它走了黑盒观测就跟着消失。(这条与 `inject_hit` 的"没会话就不做"
    有意不同: 注入造的状态没有可降级的黑盒替身, 断点只是"少了一条观察通路"。)
    """
    if sess is None:
        print("   !! expect_no_hit: 没有调试会话 → 白盒这一半不做(%s); 触发帧照发" % label)
        if trigger is not None:
            try:
                trigger(*trigger_args)
            except Exception as exc:
                print("   !! expect_no_hit: 触发动作抛错: %r" % exc)
        return None
    own_bp = None
    if isinstance(bp, (tuple, list, dict)):
        try:
            bpno, own_bp = to_bpno(sess, bp)
        except GdbError as exc:
            # 下不上 = 白盒这一半做不成。**不许记 FAIL** —— 那会把"断点错了/环境不对"冤枉成固件问题。
            print("   !! expect_no_hit: 断点 %s 没下上(%s) → 这一次不做" % (_anchor_txt(bp), exc))
            return record(label, {"hit": None, "vars": {}, "error": exc}, ok=None, crit=crit,
                          falsify=falsify, detail="断点没下上: %s" % exc)
    else:
        bpno = bp
    box, th = {}, None
    if trigger is not None:
        def run():
            try:
                box["result"] = trigger(*trigger_args)
            except Exception as exc:
                box["error"] = exc
        sess.ensure_running()
        th = threading.Thread(target=run, name="breakpoint-expect-no-hit")
        th.daemon = True
        th.start()
    res = sess.wait_only(bpno, window, vars=vars, drop=own_bp is not None)
    if th is not None:
        th.join(timeout=join)
    hit = res.get("hit")
    if own_bp is not None and hit is None:
        sess._disarm(bpno)          # 超时 ⇒ 它还在槽里, 不补撤下次就自己命中把核撂停
    ok = hit is None
    detail = ("%.1fs 窗口内未命中 —— 该指令路径未被走到" % window if ok
              else "**命中**了: %s" % (hit.where() if hit is not None else "?"))
    if box.get("error") is not None:
        detail += "  ⚠ 触发动作抛错: %r" % box["error"]
    r = record(label, res, ok=ok, crit=crit, falsify=falsify, detail=detail)
    r["vars"] = dict(res.get("vars") or {})
    return r

def _inj_line(expr, addr, size, old, new=None, new_bytes=None):
    """一条注入写成一行人话: `objtime[0] @0x20013FCC [1B] 0x1F → g_MeterTime[0] + 1`。

    ⚠ 为什么非要把**原值 + 写入的表达式**都留在记录里(2026-09-11 2-1 实跑): 那一次
      `objtime[0] = g_MeterTime[0] + 1` 落下去, 与**原值恰好相同** —— 因为计量芯本来就快 ~1s,
      注入正写在原值上。只记变量名的话, "注入生效了"与"注入是个空操作"在账本里**长得一模一样**;
      两条都记上, 复核的人一眼看得出这一次到底有没有改变状态。

    `new_bytes`(2026-09-14 加) = **写完之后读回来的那几字节**。上面那条担忧只靠"表达式文本"仍是
      **推断**(值对不对要人自己算), 而读回来是**事实**: 记为 `0x02 → 0xFD`, 并单列一句"改没改",
      于是"注了个空操作"当场可见, 不必回过头去解读表达式(3-1 风险② 的 `temp[0] ^ 0xFF` 就是
      这种"值只能算不能看"的表达式)。**取不到就记 `None`**(寄存器驻留, 或写回读没读上来 —— 见
      `_read_back` 的 ⚠) ⇒ 退回"只记表达式文本"的旧口径, 而不是整条消失。
    """
    where = "@(寄存器)" if addr is None else "@0x%08X [%dB]" % (addr, size)
    old_s = old.hex(" ").upper() if isinstance(old, (bytes, bytearray)) and old else "?"
    if new_bytes is None:
        return "%s %s %s → %s" % (expr, where, old_s, new if new is not None else "?")
    nb = new_bytes.hex(" ").upper() if new_bytes else "?"
    line = "%s %s %s → %s" % (expr, where, old_s, nb)
    if isinstance(old, (bytes, bytearray)) and old and new_bytes:
        line += "   (↔ 空操作 —— 写下去与读回来**一模一样**)" if bytes(old) == bytes(new_bytes) \
            else "   (= %s)" % (new if new is not None else "?")
    return line

def inject_decide(sess, at, assigns, decide, follow, watch_vars=(), at_vars=(),
                  label="注入判定", crit=None, falsify=None, timeout=10.0,
                  trigger=None, trigger_args=(), regs=("r0",)):
    """**停到判定指令上就地读判定** —— 注完放行, 停在条件分支那条指令上, 单步看落点归哪一支。

    `follow=True`  ⇒ 期望**落进 `decide["fallthru"]`**(被条件保护的那句被执行);
    `follow=False` ⇒ 期望落在 `decide["target"]`(跳走了, 那句**没被执行**)。
    `decide` = `Session.decision_anchor(func, callee)` 的产物(地址由 `.out` 推出, 不收手抄)。

    **为什么它比 `inject_miss`/`inject_hit` 强**(2026-09-11 实跑定案, 见 `decision_anchor`)：
    那两次的"这一停归谁"只能靠**放行后的秒数**猜 —— 而秒数既混着 `go()` 的静默、又被"停核 1~3s
    造成管理芯钟落后"污染, 两个尺度重叠, 猜不准。本函数换的尺子**与时间无关**: 注完放行后,
    当前这次调用**必然立刻走到判定指令**(放行前它已经越过了分派) ⇒ **放行后的第一停就是自己**;
    停在它上面单步一条, 落点就是这条判据的答案本身。实测放行→停 **0.031s**, 无窗口可言。
    ⚠ 因此本函数**没有 `window`/`attribute_within` 参数** —— 那两个参数存在的理由在这里不成立。

    记录里带: `regs`(判定那一刻的寄存器, 默认 r0 = `cmp` 比的那个)、`step_pc`(落点)、
              `follow`(单步落点是否等于 `fallthru`)、`at_vals`/`injects`(同 `inject_miss`)。
    没会话 → `None`; 没停到注入点/断点下不上/单步没走成 → `ok=None`(**没做成**, 绝不记 FAIL)。
    """
    if sess is None:
        print("   !! inject_decide: 没有调试会话 → 这一次不做(%s)" % label)
        return None
    res = sess.with_inject(at, assigns, watch=decide, watch_vars=watch_vars, at_vars=at_vars,
                           timeout=timeout, trigger=trigger, trigger_args=trigger_args,
                           watch_regs=regs, step=True)
    base = {"hit": None, "vars": {}, "error": None}
    def _mk(ok, detail):
        r = record(label, dict(base), ok=ok, crit=crit, falsify=falsify,
                   detail=detail, trig=judge.TRIG_INJECT)
        r["injects"] = _inj_lines(res)
        r["at_vals"] = res.get("at_vals") or {}
        r["vars"] = res.get("vars") or {}
        r["regs"] = res.get("regs") or {}
        r["step_pc"] = res.get("step_pc")
        r["follow"] = None
        r["decide"] = {k: decide.get(k) for k in
                       ("loc", "insn", "prev", "target", "fallthru", "fallthru_insn")}
        r["stop_latency"] = res.get("stop_latency")
        return r
    if res.get("unavailable") or res.get("at_hit") is None:
        return _mk(None, res.get("note") or "未停到注入点(一个字都没改)")
    pc = res.get("step_pc")
    regs_txt = ", ".join("%s=%s" % (k, ("0x%X" % v) if isinstance(v, int) else v)
                         for k, v in (res.get("regs") or {}).items())
    head = ("停在判定 `%s` @%s(前一句 %r)%s; 单步落点 %s"
            % (decide.get("loc"), ("0x%08X" % decide["addr"]) if decide.get("addr") else "?",
               decide.get("prev"), ("; " + regs_txt) if regs_txt else "",
               ("0x%08X" % pc) if pc is not None else "**没走成**"))
    if pc is None:
        return _mk(None, head + " ⇒ **单步没走成** ⇒ 本次证不了任何事(记『没做成』, 不是 FAIL)")
    got = (pc == decide.get("fallthru"))
    r = _mk(bool(got == bool(follow)),
            "%s ⇒ %s" % (head,
                         ("落到**不跳**那一支 = `%s` ⇒ 被条件保护的那句**被执行了**"
                          % decide.get("fallthru_insn")) if got else
                         ("跳到 0x%08X ⇒ 被条件保护的那句**没被执行**" % decide.get("target"))))
    r["follow"] = got
    return r

def inject_miss(sess, at, assigns, watch=None, watch_vars=(), at_vars=(),
                label="注入触发(否定期望)", crit=None, falsify=None,
                window=3.0, timeout=None, trigger=None, trigger_args=(), attribute_within=None):
    """**否定期望的注入动作** —— `inject_hit` 的对偶, 与 `wait_hit`/`expect_no_hit` 那对同构。

    `timeout` = **等注入点停住**的秒数(不给就取 `window`)。二者在本函数里是**两个尺度**:
    注入点可能挂在低频路径上(11-2 的 `TaskFreeze.c:887` 只随自然分钟步进每 ~60s 到一次), 而
    放行后的观察窗口要短(那一趟在微秒级里就走完)。2026-09-17 实跑踩过: 11-2 判据④没给 `timeout`,
    于是等了 `window`(=4s)就放弃 ⇒ 记成"没停到注入点"(看着像断点错了, 其实是窗口太短, **静默假阴性**)。

    停 `at` → 注入 `assigns` → 放行 → `window` 秒内 `watch` **不该**命中:
      · 注成了 + 窗口内没命中 → `ok=True`  —— 这就是"**造出这个状态, 那条路径仍然不被走到**"的硬证;
      · 注成了 + 命中了       → `ok=False`;
      · 没停到注入点/断点下不上 → `ok=None`(没做成, **不是** False) —— 与 `inject_hit` 同一分寸。
    `trig=TRIG_INJECT` 同样由本函数统一打上。

    **为什么非有不可**(2-1 判据③, 2026-09-11 实踩): 「|差| ≤ 1s ⇒ 不跟随」要证的是一条**没发生**的事。
    原先是靠"写计量芯钟造一个 ≤1s 的偏差, 再看 :1087 会不会中" —— 但那条写路径的**落地偏差不可控**:
    698 的往返只有 0.69s, 而计量芯的新时间要等 SPI 时间对象下一次送达(~1 Hz)管理芯才看得见, 这段
    1~3s 抖动与 1s 的判据**同量级**。两次实跑因此一次命中一次没命中(命中那次管理芯钟还后退了 2.56s,
    说明落地偏差远超意图)—— **测的是 SPI 时序抽签, 不是固件**。
    注入把这条通道绕开: 在 `:1085`(判据那句)停住, 直接把 `objtime` 写成 `g_MeterTime + 1s`
    —— 精确到字节, 不经过任何传输。
    ⚠ **注入前那一刻的状态必须留证**(`at_vars=`) —— 否则"注进去的和原来一样"(2-1 实跑:
      `objtime[0]` 原本就是 `g_MeterTime[0]+1`, 因为计量芯本来就快 ~1s)与"注进去真的改了字节"
      在账本里长得一模一样。`r["injects"]` 记的是**逐条写入的 (表达式, 地址, 原值 → 新值)**,
      `r["at_vals"]` 记的是注入前读到的对照量 —— 两者一起才说得清"这一次到底把状态造成了什么样"。

    ⚠ **没会话(`sess is None`)→ 返回 `None`**, 与 `inject_hit` 同律(注入没有可降级的黑盒替身)。
    ⚠ `window` 别开太长: 放行后**下一次 SPI 送达(~1s)会重新用真值覆盖 objtime** 再判一次 ——
      窗口拉长会捞到那次**自然**比较, 把"稳态本来就不该跟随"混进这一次。3s 够看完注入那次比较。

    ⚠ **`attribute_within`(秒) —— 光把窗口开小是不够的, 得判"这一停归谁"**(2026-09-11 实跑定案):
      2-1 把窗口从 1.5s 收到再小也堵不住, 因为**病根是停核本身**: 停核要 1~3s, 而管理芯钟是软件
      走时 ⇒ 停完管理芯已落后计量芯 1~3s,**超过判据阈值** ⇒ 放行后**下一次自然的 SPI 比较必然
      跟随**, 窗口里那一停**根本不是我注入的那次执行**(实跑 ③d 因此假红; 同一天加的"每次前对齐
      两芯"也救不了 —— 停核的账 1.4~2.7s, 比 1s 的阈值还大, 对齐只能让滞后从 0 起算, 起点清了、
      终点照样越线)。
      出路是把判据从"**窗口内有没有停**"换成"**这一停是不是我们造的那次执行**": 注入那次在放行后
      **微秒级**就走完(要么落到 `watch`、要么绕过去), 所以**远晚于**这个尺度才到的命中不可能是它。
      给了本参数 ⇒ 晚于它的命中**不计入本次**(当作"没走到"), 并在 detail 里如实写出来。
      **阈值由调用方定**: "多少算同一次执行"是子项语义, 不是本层能定的。
    """
    if sess is None:
        print("   !! inject_miss: 没有调试会话 → 注入观测这一次不做(%s)" % label)
        return None
    res = sess.with_inject(at, assigns, watch=watch, watch_vars=watch_vars,
                           at_vars=at_vars, timeout=(window if timeout is None else timeout),
                           trigger=trigger, trigger_args=trigger_args)
    if res.get("unavailable") or res.get("at_hit") is None:
        r = record(label, {"hit": None, "vars": {}, "error": None}, ok=None, crit=crit,
                   falsify=falsify, detail=res.get("note") or "未停到注入点",
                   trig=judge.TRIG_INJECT)
        r["injected"] = []
        r["injects"] = []
        r["at_vals"] = {}
        r["vars"] = {}
        return r
    hit = res.get("hit")
    _lat = res.get("stop_latency")
    # ⚠ **归不到这一次的命中不算命中**(`attribute_within` 给了才算, 见参数说明)。
    _late = (hit is not None) and (attribute_within is not None) \
        and (_lat is None or _lat > attribute_within)
    if _late:
        hit = None
    ok = hit is None
    if _late:
        detail = ("**命中**了 %s, 但那一停是在放行后 %s 才到的(阈值 %.2fs)⇒ **不是这一次的执行**"
                  "(注入那次在微秒级就走完了)⇒ 不计入本次"
                  % (res["hit"].where(), ("%.2fs" % _lat) if _lat is not None else "未知",
                     attribute_within))
    else:
        detail = ("%.1fs 窗口内未命中 —— 该指令路径未被走到(注入点已停住并改写 %s)"
                  % (window, ", ".join(e for e, _a, _s, _o in res.get("injected") or []))) if ok else \
                 ("**命中**了: %s%s" % (hit.where() if hit is not None else "?",
                                        "" if attribute_within is None else
                                        "(放行后 %.2fs, 在 %.2fs 阈值内 ⇒ 判为这一次)"
                                        % (_lat, attribute_within)))
    r = record(label, {"hit": hit, "vars": res.get("vars") or {}, "error": None},
               ok=ok, crit=crit, falsify=falsify, detail=detail, trig=judge.TRIG_INJECT)
    r["stop_latency"] = _lat
    r["vars"] = res.get("vars") or {}
    r["injected"] = [e for e, _a, _s, _o in res.get("injected") or []]
    # ⚠ **带着原值**的逐条写入账(`表达式 @地址 原值→新值`) —— `injected` 只有名字, 而"注进去的
    #   恰好与原来一样"(2-1 实跑真实发生过: 计量芯本来就快 ~1s, 注入 `g_MeterTime[0]+1` 正落在原值上)
    #   与"真的改了字节"光看名字分不出来。`at_vals`(注入前读的对照量)要一起看才说得清。
    r["injects"] = _inj_lines(res)
    r["at_vals"] = res.get("at_vals") or {}
    return r

def _inj_lines(res):
    """把 `with_inject` 的 `injected`(逐条 4 元组)与 `assigns`(表达式→新值)配成一行人话。

    `injected_new`(写完读回来的字节, 逐条与 `injected` 同序)有就用它 —— 见 `_inj_line` 的 ⚠。
    """
    vals = {str(e): v for e, v in (res.get("assigns") or [])}
    news = list(res.get("injected_new") or [])
    out = []
    for i, (e, a, s, o) in enumerate(res.get("injected") or []):
        out.append(_inj_line(e, a, s, o, vals.get(str(e)),
                             news[i] if i < len(news) else None))
    return out

def _then_lines(res):
    """同 `_inj_lines`, 但取 `then_assigns` 那一批(落在**判据断点**上的那次补写)。

    单独一个函数而不是复用 `_inj_lines`: 两批写**落在不同的停点**上, 而"改在哪儿"是承重信息 ——
    合成一列就分不出哪一次是在注入点、哪一次是在判据断点上改的。
    """
    return _inj_lines({"assigns": res.get("then") or [],
                       "injected": res.get("then_injected") or [],
                       "injected_new": res.get("then_injected_new") or []})

@_pair_window(lambda sess, at, *a, **k: "注入 %s ← %s" % (_anchor_txt(at), k.get("label", "注入触发")))
def inject_hit(sess, at, assigns, watch=None, watch_vars=(), at_vars=(), label="注入触发",
               crit=None, falsify=None, timeout=30.0, ok=None, trigger=None, trigger_args=(),
               attribute_within=None, gate=None, then_assigns=None,
               watch_regs=(), step=False, steps=0):
    """**注入观测递证据的统一入口** —— `with_inject` 的记录版(与 `fire_hit`/`wait_hit` 同族)。

    为什么把它单列出来: 「一行一次」这条纪律对注入观测同样适用, 但它的**结论口径**与帧触发那条不同,
    不能靠调用方各自拿捏:
      · 命中 `watch`        → `ok=True` —— 这就是"**执行到 `:<行>` 才成立**"的硬证;
      · 没命中 / 没停到注入点 / 断点下不上 → `ok=`**None**(没做成) —— 与"固件不对"分开 (judge 模块头);
      · `inject()` 自己抛错(白名单没点名) → **原样向上抛** —— 那是脚本配置错, 该当场炸, 不许
        伪装成"没做成"混进账本(`with_inject` 的 docstring 有同一条)。
    `trig=TRIG_INJECT` 由**本函数**统一打上 —— 于是汇总里那条 `[断点/注入]` 前缀不靠调用方记得写。

    ⚠ **没会话(`sess is None`)→ 返回 `None`**(调用方据此不做这一半) —— 与 `wait_hit` 同律。注意这与
      `fire_hit` 的"照发"**不一样, 是有意的**: `fire_hit` 的 `fn` 是一条**该发的帧**(黑盒观测不能跟着走丢),
      而注入**没有可降级的黑盒替身** —— 它本身就是白盒那一半, 没会话就只剩"不做", 不能假装发过什么。
    """
    if sess is None:
        print("   !! inject_hit: 没有调试会话 → 注入观测这一次不做(%s)" % label)
        return None
    res = sess.with_inject(at, assigns, watch=watch, watch_vars=watch_vars,
                           at_vars=at_vars, timeout=timeout,
                           trigger=trigger, trigger_args=trigger_args, gate=gate,
                           then_assigns=then_assigns,
                           watch_regs=watch_regs, step=step, steps=steps)
    # ⚠ 给了 `gate` 时, "汇合点没停到"与"注入点没停到"**同属**"这一次没做成"(都不是"固件不对")。
    #   少了这一条, 汇合点停不到会掉进下面的"命中支"里被当成真命中 —— 那是**把别人的一次执行
    #   当成了证据**(与 `attribute_within` 那条同一个毛病)。
    if res.get("unavailable") or res.get("at_hit") is None \
            or (gate is not None and res.get("gate_hit") is None):
        # 没停住 = 一个字都没改。照录一条 TBD 形状的(ok=None), 由调用方决定挂不挂判据。
        # `note` 里带着"给没给触发" —— 这是本函数最要紧的一条线索: 那段代码若不是热点, **没触发
        # 就必然等不到停**, 而它的表象与"断点错了/固件不对"一模一样(2026-09-11 实踩)。
        r = record(label, {"hit": None, "vars": {}, "error": None}, ok=None, crit=crit,
                   falsify=falsify, detail=res.get("note") or "未停到注入点",
                   trig=judge.TRIG_INJECT)
        r["injected"] = []
        r["injects"] = []
        r["gate_hit"] = res.get("gate_hit")
        # ⚠ **两次停靠也要跟着进记录**(2026-09-14 实跑踩到, 与上一段 `r["vars"]` 那条同类):
        #   `record()` 造的是**判据记录**, 它只拿走 `hit` 去定 ok —— `res["at_hit"]`/`res["hit"]`
        #   若不搬到记录上, 调用方的 why 行读 `r.get("at_hit")`/`r.get("hit")` 就恒得 `None`
        #   ⇒ 日志里印着"注入点=未命中 | 判据断点=None", 而那一次其实**两次都停到了**
        #   (injects/then_injects 非空就是铁证)。那是**把成功的那次写成没停到**, 复核的人会
        #   据此判"本半支没做成" —— 静默错误换了个方向而已。
        r["at_hit"] = res.get("at_hit")
        r["watch_hit"] = res.get("hit")
        r["then_injects"] = []
        r["at_vals"] = {}
        r["vars"] = {}          # 形状与命中支一致(见下 ⚠): 取不到就是空, 别留 KeyError 的坑
        r["trigger_result"] = res.get("result")
        r["trigger_error"] = res.get("trigger_error")
        r["regs"] = {}
        r["step_trace"] = None
        return r
    # ⚠ **归不到这一次的命中不算命中**(`attribute_within` 给了才算, 说明见 `inject_miss`):
    #   期望**命中**的那一次尤其致命 —— 记成"命中"就等于把**别人**的一次执行当成了证据。
    _lat = res.get("stop_latency")
    _late = (res.get("hit") is not None) and (attribute_within is not None) \
        and (_lat is None or _lat > attribute_within)
    if _late:
        r = record(label, {"hit": None, "vars": {}, "error": None}, ok=None, crit=crit,
                   falsify=falsify, trig=judge.TRIG_INJECT,
                   detail="**命中**了 %s, 但那一停是在放行后 %s 才到的(阈值 %.2fs)⇒ **不是这一次的"
                          "执行** ⇒ 本次证不了任何事(记『没做成』, 不是 FAIL)"
                          % (res["hit"].where(),
                             ("%.2fs" % _lat) if _lat is not None else "未知", attribute_within))
        r["stop_latency"] = _lat
        r["injected"] = [e for e, _a, _s, _o in res.get("injected") or []]
        r["injects"] = _inj_lines(res)
        r["gate_hit"] = res.get("gate_hit")
        # ⚠ **两次停靠也要跟着进记录**(2026-09-14 实跑踩到, 与上一段 `r["vars"]` 那条同类):
        #   `record()` 造的是**判据记录**, 它只拿走 `hit` 去定 ok —— `res["at_hit"]`/`res["hit"]`
        #   若不搬到记录上, 调用方的 why 行读 `r.get("at_hit")`/`r.get("hit")` 就恒得 `None`
        #   ⇒ 日志里印着"注入点=未命中 | 判据断点=None", 而那一次其实**两次都停到了**
        #   (injects/then_injects 非空就是铁证)。那是**把成功的那次写成没停到**, 复核的人会
        #   据此判"本半支没做成" —— 静默错误换了个方向而已。
        r["at_hit"] = res.get("at_hit")
        r["watch_hit"] = res.get("hit")
        r["then_injects"] = _then_lines(res)
        r["at_vals"] = res.get("at_vals") or {}
        r["vars"] = {}
        r["trigger_result"] = res.get("result")
        r["trigger_error"] = res.get("trigger_error")
        return r
    if ok is None:
        # 命中 ⇒ True(这就是"执行到 :<行> 才成立"的硬证); 没命中 ⇒ **None**(没做成), 不是 False
        # —— 与 `record()` 的缺省口径一致: "没停在那儿"分不清是"固件没走这条分支"还是"注入没生效",
        # 那要由调用方用**另一条观察通道**(读回/AA80)去分辨, 不许在这里替它定罪。
        ok = True if res.get("hit") is not None else None
    r = record(label, {"hit": res.get("hit"), "vars": res.get("vars") or {},
                       "error": None}, ok=ok, crit=crit, falsify=falsify, trig=judge.TRIG_INJECT)
    r["stop_latency"] = _lat
    # ⚠ `record()` 返回的是 **judge 记录**, 它只拿走 `hit` 去定 ok —— 上面那个 info 字典里
    #   `vars` 只喂给了 `report()` 的**打印**。不补这一行, 调用方 `r["vars"]` 恒 None:
    #   1-3 行的打印里明明列着 8 个值, 取出来却是 None(2026-09-11 3-1 实跑踩到, 判据 ④b 因此
    #   把"兜底执行过"记成了"未见兜底" —— **假 FAIL**, 冤枉固件)。与 `at_vals` 同一处理。
    r["vars"] = res.get("vars") or {}
    r["injected"] = [e for e, _a, _s, _o in res.get("injected") or []]
    r["injects"] = _inj_lines(res)
    # 汇合点那一停 + 判据断点上补的那一次写(`then_assigns`) —— 与 `injects` 分开放: "改在哪儿"是承重信息,
    # 复核时要看得出两次写分别落在哪个停点上(3-1 风险② 就是靠 A/B 两停各打坏一份才逼出 `return OTHER`)。
    r["gate_hit"] = res.get("gate_hit")
    # 两次停靠的实录(`at_hit`=注入点停到了没有 / `watch_hit`=判据断点停到了没有)。
    # why 行要照实报"三停到没到", 而 `record()` 只拿走 `hit` 去定 ok, 不转交这两个键
    # ⇒ 漏了它们, **命中的那次会被 why 行写成"没停到"**(2026-09-14 3-1 实跑踩过:
    # 注入与判据断点补注都有实据, 汇总却打 `注入点=未命中 | 判据断点=None`)。静默假阴性, 故在此补上。
    r["at_hit"] = res.get("at_hit")
    r["watch_hit"] = res.get("hit")
    r["then_injects"] = _then_lines(res)
    # `at_vals` = **注入之前**在注入点读到的量(对照, 见 `with_inject` 的 `at_vars`)。放在记录上,
    # 于是"我们停对了地方、被调函数确实重读过"这件事跟着证据一起进账本, 不靠事后回忆。
    r["at_vals"] = res.get("at_vals") or {}
    # 触发帧**自己的回执**(触发函数的返回值) —— 它证明"那一次调用真的发生且串口收到了应答"。
    # 为什么必须带出来(2026-09-14, 3-1 风险② 的出处): 那一次要证的判据之一是"**回帧是成功还是错误**"
    # (645 写入口兜底后仍回 `OK_FRAME` / 698 写入口回 `DAR_OtherErr`)。没有它, "帧被受理了"与
    # "帧其实失败了、根本没跑到那一步"在账本里长得一样 —— 本仓最忌的那类静默错误
    # (与 `_inj_line` 的 ⚠、`_read_back` 的 ⚠ 同一类, 这是第三处)。
    r["trigger_result"] = res.get("result")
    r["trigger_error"] = res.get("trigger_error")
    # 就地读判定那两个(`watch_regs` 的寄存器 / `steps` 的单步轨迹) —— 与上面 `vars`/`at_vals` 同一条规矩:
    # `record()` 只拿走 `hit`, 不转交这些键 ⇒ 不在这儿补上, 调用方 `r.get("step_trace")` 恒 None,
    # 而"没读到"与"没做这一步"长得一模一样(本仓最忌的那类静默错误)。
    r["regs"] = res.get("regs") or {}
    r["step_trace"] = res.get("step_trace")
    return r

ANCHOR_KINDS = KINDS            # 字面量锚点的判别字 —— 定义在本文件上部「断点写法」

def anchor_spec_txt(spec):
    """锚点规格的字面形式(日志里那串) —— 与脚本里写的那一行**长得一样**, 好对着核。"""
    return text(spec)

def _anchor_txt(at):
    """断点的打印形式 —— 四种都打成一行人读得懂的:
    `(文件, 行号)` → `文件:行号`; 字面量锚点 → 它自己的规格; dict(已解出的锚点) → 它的 `loc`。"""
    if isinstance(at, dict):
        return at.get("loc") or "%s:%s" % (at.get("srcfile") or at.get("file"), at.get("line"))
    if len(at) >= 2 and at[0] in ANCHOR_KINDS + ("line",):
        return text(at)
    return "%s:%s" % (at[0], at[1])

@_pair_window(lambda sess, at, *a, **k: "逐拍重注 %s ← %s" % (_anchor_txt(at), k.get("label", "逐拍重注")))
def inject_hold(sess, at, assigns, ticks, at_vars=(), watch=None, watch_vars=(),
                stop_when=None, tick_timeout=15.0, watch_timeout=3.0, budget=900.0,
                label="逐拍重注"):
    """**逐拍重注**: 在 `at` 处每一拍都重写一遍, 把注入造出的内部条件**按住**跨过整个时间窗。

    为什么非它不可(2026-09-17 实测定案, 5-2 判据① 正是卡在这上面):
      被注入的量若**每周期被固件自己的数据通路刷回原值**, 单次注入只在**那一拍**成立。
      5-2 的 `g_PowP[0]` 就是这样 —— 停核现场抓到的是
      `RevCopy_Data(pDest=g_PowP, pSour=g_SPIMBuff+77, 4)`(`Platform/Common.c:310`), 走的是
      **指针形参** ⇒ 按变量名 grep 全固件只搜到"两处初始化写 0", 于是"注进去的值 2 秒内变 0"
      一度被读成"有人在清它"。真来路是计量芯经 SPI 推来的 698 帧
      (`SpiReadDMA(g_SPIMBuff) → Save_variable_Data → Spread_StructArray(SET698) → RevCopy_Data`);
      本台面**无负载** ⇒ 计量芯恒推 0 ⇒ 注进去的值下一拍就没了。
      而 `Chk_OverLoad` 的去抖要 `C_EveFlt=3` 拍锁存 + `clamp(去抖参数−3, ≥4)` 拍计时
      ⇒ **单次注入永远攒不满**。判据随之以"没命中该断点"收场 —— 而那**看起来像"固件不落库"**,
      其实是**触发通道缺了若干拍**。
      实跑取证(探针 58 拍): `g_EventTmr[21]` 一路 20→56 每拍 +1, 第 58 拍 `g_EventSta[21]`
      由 FALSE 翻 TRUE、Tmr 归 0 —— 全固件只有 `:2024` 那一处写 `g_EventSta[21]`,
      即 `Recd_OverLoad(EV_OverLoadA)` 真的执行了。

    与 `inject_hit` 的分工: 那个是**一次**停靠(证的形状是"执行到 `:<行>` 才成立"), 本函数是
    **按住**(要跨过一段**时间窗**, 比如一整段去抖)。判据本身"固件该不该动"由 `stop_when` 回答,
    **不是**靠命中本身 —— 命中的是注入点, 那只能证明"我们停对了地方"。

    `watch`/`watch_vars` = **判据断点**(可选): 注完放行后在同一次执行里等它一次(注入点在它之前),
    到了就**停在那儿读那几样**(局部量只在这一停有意义)并收工 —— 5-2 判据①⑤ 要的 `i`/`delay`
    就是这样拿到的(`:1882` 注入 → `:2021` 读)。没给 `watch` 就用 `stop_when` 判"固件动没动"。
    ⚠ `watch_timeout` 是**单拍**等判据断点的秒数, 别按"等一个人走完流程"那种尺度取: 同一次执行里的
    断点到位是**微秒级**, 而 `wait_break` 会把**掠过的非目标停靠**(即下一拍的注入点)放行掉 ——
    等久了等于每隔一拍才注一次, 窗口跟着拉长。判据断点在别的函数里(要几百毫秒才到)时再放大。

    三态(与库里其余原语同律; 调用方据此挂 `crit`, 不许把 `None` 当 FAIL):
      · `stop_when` 在某一拍读到预期 ⇒ `ok=True` —— 条件被按住整个窗口, 固件**照做了**;
      · 跑满 `ticks` 拍都没读到      ⇒ `ok=False` —— 这是**真证伪**(窗口走完了, 固件仍没动);
      · 一次都没停到 `at` / 中途断了 ⇒ `ok=None` —— **本次没做成**, 与"固件不对"分得开。

    ⚠ 本函数**只做注入**, 不发任何帧 —— 与 `fire_hit` 不同, 它没有"没会话就照发"的替身:
    注入本身就是白盒那一半, 没会话就是"不做"。
    """
    if sess is None:
        print("   !! inject_hold: 没有调试会话 → 逐拍重注这一次不做(%s)" % label)
        return None
    if not isinstance(at, (dict, tuple, list)):
        raise GdbError("inject_hold: `at` 收 (源文件, 行号) / 字面量锚点 / {'srcfile':…, 'line':…}; "
                       "已挂好的 bpno 请直接用 `wait_break` —— 本函数要自己撤断点")
    out = {"at_hit": 0, "fired": False, "stopped": "", "vals_log": [], "injects": [],
           "assigns": [[e, v] for e, v in (assigns or [])], "ok": None, "detail": "",
           # 形状与 `inject_hit` 的记录对齐(`vars` = 在**判据断点**上读到的 / `at_vals` = 最后一拍
           # 注入**之前**在注入点读到的对照) —— 调用方的 why 行读同一批键, 两处不必各写一份。
           "vars": {}, "watch_hit": None, "at_vals": {}}
    bpno = to_bpno(sess, at)[0]
    bp_w = None
    if watch is not None:
        bp_w = to_bpno(sess, watch)[0]
    print("   逐拍重注 %s ×%d 拍%s" % (_anchor_txt(at), int(ticks),
                                   (" | 等判据断点 %s" % _anchor_txt(watch)) if bp_w is not None
                                   else ("(判到 %s 就停)" % str(stop_when)) if stop_when else ""))
    t0 = time.monotonic()
    try:
        for k in range(int(ticks)):
            if time.monotonic() - t0 > budget:
                out["stopped"] = "预算 %.0fs 用尽" % budget
                break
            hit, vals = sess.wait_break(bpno, timeout=tick_timeout, vars=at_vars, drop=False)
            if hit is None:
                out["stopped"] = "第 %d 拍没停到(超时 %.0fs)" % (k + 1, tick_timeout)
                break
            out["at_hit"] += 1
            out["at_vals"] = dict(vals or {})      # 最后一次注入**之前**的现场(对照)
            out["vals_log"].append(dict(vals or {}))
            for expr, val in assigns:
                rec = sess.inject(expr, val)
                out["injects"].append(rec if isinstance(rec, str) else repr(rec))
            if stop_when is not None and str((vals or {}).get(stop_when[0])) == stop_when[1]:
                out["fired"] = True
                out["stopped"] = "第 %d 拍读到 %s=%s" % (k + 1, stop_when[0], stop_when[1])
                break
            # ⚠ `settle=False`: 每一拍只等**下一拍的停靠**, 中间不发串口帧 —— 缺省的 0.4s 静默
            #   在这里是纯浪费, 而本函数要跑几十拍, 那一笔会乘几十倍(探针实测 58 拍 ~12 s/拍)。
            sess.resume(settle=False)
            if bp_w is not None:
                # 注入点与判据断点**在同一次执行里**(`:1882` 在前、`:2021` 在后) ⇒ 放行后那一停
                # 微秒级就到。来了就是"这一次执行走到了判据断点" —— 正是要看的那一刻。
                # ⚠ 没来只说明**这一拍**没走到(判据还没满), 不能当"固件不对" —— 故这里只记不判。
                hit_w, vals_w = sess.wait_break(bp_w, timeout=watch_timeout,
                                               vars=watch_vars, drop=False)
                if hit_w is not None:
                    out["fired"] = True
                    out["watch_hit"] = dict(vals_w or {})
                    out["vars"] = dict(vals_w or {})
                    out["stopped"] = "第 %d 拍走到判据断点 %s, 读到 %s" % (
                        k + 1, _anchor_txt(watch), out["vars"])
                    break
    finally:
        # 收尾态必须"跑着" —— 撂在 halt 会让表串口全哑, 现象与"表死机"一样。
        # ⚠ 两个断点**趁同一次停住一起撤**(等价于 `_disarm`, 只是不逐个绕"放行→再叫停"): 一个个走
        #   `_disarm` 会在中间放行一次, 而那时**另一个断点还挂着** —— 高频行上的断点会自己命中,
        #   把核心撂在停住态, 于是"收尾"反而制造了它本要避免的那个现象(见 `_disarm` 的 ⚠)。
        sess.ensure_stopped()
        for _b in (bpno, bp_w):
            if _b is not None:
                sess._drop_bp(_b)
        sess.ensure_running()
    n = out["at_hit"]
    if out["fired"]:
        out["ok"] = True
        out["detail"] = "逐拍重注 %d 拍: %s" % (n, out["stopped"])
    elif n == 0:
        out["ok"] = None
        out["detail"] = "一次都没停到 %s ⇒ 本次没做成 —— %s" % (_anchor_txt(at), out["stopped"])
    elif n < int(ticks):
        # 窗口**没走完** —— 不是"固件不对", 是"这一次没做成"。
        out["ok"] = None
        out["detail"] = ("只重注了 %d/%d 拍就断了 ⇒ 窗口没走完, 本次没做成 —— %s"
                         % (n, int(ticks), out["stopped"]))
    else:
        _what = ("走到判据断点 %s" % _anchor_txt(watch)) if bp_w is not None else \
                ("读不到 %s=%s" % (stop_when[0], stop_when[1]) if stop_when else "条件成立")
        out["ok"] = False
        out["detail"] = ("逐拍重注整整 %d 拍, 始终没能%s ⇒ 条件被按住整个窗口, 固件仍未动"
                         % (n, _what))
    print("   %s %s: %s" % ("[OK]" if out["ok"] else "[--]", label, out["detail"]))
    return out

# ============================================================================
# ④ 离线自检(不开串口、不连表、不需要 jlink)
# ============================================================================
# 什么样算"关于 gdb 会怎么回的事实"。**要精确**: 第一版写成"含 `value="` 就算", 当场报出 1200 条
# (全是 `n`/`t`/`\` 这种被 `parse_mi` 随手当记录收下的碎片), 那种判定没人会看第二眼。
# 现在只认三类**形状明确**的:
_REC_HEAD = re.compile(                       # ① 一整条 MI 记录(带 class 前缀, 后面要么 `,` 要么到头)
    r"^\s*[\^*=~&](done|error|running|connected|exit|stopped|stopping|"
    r"thread-[\w-]+|cmd-param[\w-]*|memory-changed|library-[\w-]+|breakpoint[\w-]*)(\s*,|$)")
_ADDR = re.compile(r"^0x[0-9a-fA-F]+\s*(<[^>]*>)?$")          # ② gdb 的地址串(可带 `<符号名>`)
_SCALAR = re.compile(r"^-?\d+ '[^']*'$")                      # ③ gdb 打印的标量(`4 '\004'`; char 数组的文本形态)

# ⚠ 本模块**没有 `main()` 也没有 `__main__` 入口**(2026-09-18): 它原本只有一个 CLI, 就是跑自检。
#   自检层已按用户指令删除(本仓禁止非实物测试)。本模块的入口是**被 `common.trial` import**,
#   经 `GD.*` 调用 —— 它不是给命令行跑的。
