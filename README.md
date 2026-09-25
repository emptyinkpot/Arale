






每次都会进行一次初始化，通过解析elf文件来获取映射和变量，然后针对函数或者变量来打断点，watch使用的是串口来读的，而watchpoint有这个功能但是没有正式启用，

## 工程原理

本工程本质上就是直接通过串口的swd与内核通讯来调试，主要测试对象为芯片本体，覆盖可以通过上位机调试测试的所有项目

### swd自动调试原理 

`.out` 文件里带着符号地址和源码行号，本机的 Python 库就靠它来定址。
读的时候分两种情况：**不用停核的**，走 `swdbg/probe`，由它自动认出该用哪一支探针，再用 SWD 发起 AHB-AP 背景访问（J-Link 那一端走 pylink-square，CMSIS-DAP 那一端走 pyOCD）；**要停核或者要读局部变量的**，走 gdb-multiarch，经 gdbserver 从同一条 SWD 下发断点和读写命令（同样按端：J-Link 端起 `JLinkGDBServerCL`，CMSIS-DAP 端起 `pyocd gdbserver`）。不管走哪条路，结果最后都收敛到同一套 Python API：定址看 `common.varresolve`，判据看 `common.judge`，白盒读看 `meterlib.cmd_bank`。再往上，由 `project/tests/_test_*.py` 把发帧、观测、判据串成一次子项运行，最后给出**通过、失败、未定论**三种结论。

Cortex-M 内核里有一个叫 **FPB（Flash Patch and Breakpoint）** 的硬件单元，专门负责指令断点，监视点靠的是 **DWT（Data Watchpoint and Trace）** 单元。GDB 自己不碰硬件，它把「在地址 X 下断点」这类高层命令交给 gdbserver，再由探针通过 SWD 去配置 FPB/DWT —— 管这事的软件（gdbserver）与硬件（探针）**两端各有一套，但对上层的接口是同一套**。
```
GDB（PC 上的软件，发“在地址 X 下断点”这类高层命令）
  ↓ GDB Remote Serial Protocol
gdbserver（PC 上的软件，把命令翻译成探针能懂的格式）
      J-Link 那一端: JLinkGDBServerCL        CMSIS-DAP 那一端: pyocd gdbserver
  ↓ USB
探针固件（硬件设备，通过 SWD 读写芯片寄存器）
      J-Link 探针                            DAPLink 等 CMSIS-DAP 探针
  ↓ SWD
芯片内部的调试组件（SW-DP / AHB-AP）
  ↓
FPB（断点硬件） / DWT（监视点硬件）
```
因此可以实现软件swd调试

### 编排层的边界与顺序

编排层在 `common.orchestrator.Plan`，不是 `swdbg`。`swdbg` 只负责探针、SWD、GDB、FPB 和 DWT；`meterlib` 只负责串口协议；测试步骤通过 `common.trial.Ctx` 使用这两类能力。`Plan` 只声明顺序，并复用 `common.trial.run_subitem` 的既有资源生命周期：

```text
打开串口 -> 按需打开一次 GDB/SWD 会话 -> 执行步骤 -> 关闭调试会话
-> 执行收尾步骤 -> 关闭串口 -> 输出 Judge/日志/退出码
```

新测试可以这样写，旧的 `trial.run_subitem(...)` 写法继续有效：

```python
from common.orchestrator import Plan

PLAN = (Plan("示例", cmd_bank.example_criteria, name="example", gdb=breakpoint)
        .step("串口与白盒观测", run_step)
        .cleanup("恢复台面", cleanup_step))

if __name__ == "__main__":
    raise SystemExit(PLAN.run())
```

`Plan` 不持有串口或探针对象，也不自行枚举、连接、断开设备；步骤回调才调用所属层的 API。这保证了串口和 SWD 的依赖方向不会倒置，也保证旧脚本的收尾顺序不变。

### 645/698自动调试原理 

依靠脚本直接发送串口帧。






依靠外部插件
openocd pylink



启动流程：检测串口com匹配→自动匹配串口和com使得api运行正常，
检测是jlink还是daplink→自动匹配后端使得api正常运行（不管是jlink还是daplink用的都是同一套api，这样测试脚本也只需要写一套）



vcc
gnd

clk
dio


---

### 文件树

本文件里有**两份**关于代码的清单：文件树和各包的表。它们答的不是同一个问题，所以两份都留：

| | 树 | 表 |
|---|---|---|
| 答什么 | **它在哪** —— 哪个文件、哪一层、叫什么 | **它干什么、用错会怎样** |
| 形态 | `名` + 一句短注（够认出是谁就行） | `名` + 完整作用（允许跨行、允许举例） |
| 什么时候看 | 手上有个名字，要找它的家 | 要动手用一个东西，先看它什么意思 |

**唯一的硬约束：表里出现的每一个名字，树里必须有，方向是单向。**

即表 ⊆ 树。树可以比表多，表绝不能比树多。理由是顺着「树是地址图」来的：
一个名字得先有话可说、有家可回，才谈得上讲解；反过来，树上有、表上不讲（收尾函数、常量、纯数据模块），是刻意省略，不是漏。

这条一定，判据就顺手了 —— 两份对不上时**只朝一个方向查**：表里有、树里没有，是错；树里有、表里不讲，是对。

两份各自收哪些名字：

| 名字的种类 | 树 | 表 |
|---|---|---|
| 函数 / 类 / 类的方法 | **全收** | 收「有可讲作用」的 |
| 模块级常量（`MAX_EVENTS` / `MARK_*` / `SRAM_BASE` 这类） | **全收**（收在文件末尾一行 `常量 N: …`） | 只收「用错会静默出错」的 |
| `main()` | 收（「`main` 在哪」也是地址问题） | **不收** —— 没有「用错会怎样」，硬写是注水 |
| `_` 开头的私有名 | **不收** —— 它不是给别人调的，地址不稳 | 表里可**引用**（讲「为什么只能有这一个出口」时要点到名），但**不单独立行** |
| 纯数据模块（`profile.py` 这类，本体就是那份数据） | 收一行说明 | 不收 |
| 字面量的短注 vs 表里的作用 | 短注**可以更简**，但**不许和表里的说法相反** | 完整说法 |

改的时候按这五条走：

1. **改了源码的公开名 → 先改树。** 树是地址图，名字先要有家。
2. **新 API 带「用错会静默出错」的语义 → 表里加一行**；不带就不加（纯查询、纯取值不加）。
3. **只改表里某条的措辞 → 树不动**（树的短注没变，就不用动）。
4. **树里的短注和表里的作用不许相反** —— 树可以更简，不可以另说一套。
5. **删名 → 两处一起删。** 树删了表没删 = 表指向一个不存在的地址。

```
帧收发基础/
├── README.md                        本文件：入口地图
├── CLAUDE.md                        AI 操作手册
├── 调试器注入.md                   通用能力参考：注入原语 / 注入点推导
│
├── .gitignore                       忽略规则：__pycache__ / log/ / _dbg/ / TestResult*.rtf / _gittest* / .venv/
├── pyproject.toml                   **依赖与打包的唯一一份声明**：装哪几个包、装到哪、六个顶层包怎么映射
│
├── src/                            **全部库代码**
│   ├── common/                     中立层：零协议、零画像的共用积木，它谁都不用
│   │   ├── __init__.py             层依赖图
│   │   ├── cardslot.py             **卡带槽**：『当前活动的那份配方』的唯一机制
│   │   │                           另含 `resolve_in`
│   │   ├── profile.py              当前**表**画像的中立取用入口
│   │   ├── machspec.py             当前**装机**卡带的中立取用入口
│   │   ├── cli.py                  脚本入口参数守卫 `guard_argv`
│   │   ├── console.py              控制台 UTF-8 单点
│   │   ├── runlog.py               测试运行日志 tee
│   │   ├── events.py               **事件流**：`log/<名>.jsonl`，记『发生了什么』
│   │   ├── judge.py                **判据账本地基**：预设条目 × 证据 → 三态结论 + 退出码
│   │   ├── jsonc.py                读带注释的 JSON
│   │   ├── elfsym.py               把 .out 当符号表用
│   │   ├── varresolve.py           变量名 → 地址的**唯一**解析实现
│   │   ├── hashfile.py             文件指纹 sha256 的**唯一**实现
│   │   ├── loglabel.py             日志那一行的**字形**只在这里定义一次：协议词表 + 帧行 / 判定行
│   │   ├── ctext.py                C 源码的文本原语：抠注释与字符串 / 认函数定义行 / 判读写
│   │   ├── portsel.py              **串口传输层**：选口 + 收发 + 探活装配点
│   │   ├── snapdiff.py             两份字节快照逐块求差
│   │   ├── winpnp.py               Windows 设备树：查设备节点在不在位 / 坏没坏 / 何时掉的，以及提权重启它
│   │   ├── probe_guard.py          **探针跨进程占用闸**：OS 文件锁独占那支物理探针
│   │   ├── trial.py                **子项运行外壳**：argv / 日志 / Judge / 串口 / 会话 / 收尾三连 / 退出码
│   │   ├── orchestrator.py         **编排层门面**：声明步骤与收尾；不直接 import 串口、SWD 或 GDB 驱动
│   │   ├── bench.py                **台面体检**：离线断言 + 串口 + 探针
│   │   └── faultlog.py             **故障台账**：一个故障一个稳定的码，原话逐字不截断，跨跑次可查
│   │
│   ├── meterlib/                   **协议引擎**
│   │   ├── __init__.py             包出口
│   │   ├── p645.py                 **DL/T645-07 协议**：组帧 / 校验 / 解码 / 标准 DI 与指令码
│   │   │                           + **AA80 直读入口**
│   │   ├── p698.py                 **DL/T698.45 协议**：组帧 / 校验 / 解码 / **对象模型** + **开表口**
│   │   ├── ble.py                  **蓝牙透传通道**。模组 `C0:00:00:00:00:01` = `BT2603_Meter`
│   │   │                           主机→表**已通**
│   │   │                           表→主机**未打通**
│   │   ├── cmd_bank.py             **语义层**：帧目录 SPECS + 用例 + 语义动词 + verdict + CLI
│   │   └── watch.py                **AA80 只读观察簇**：按名直读管理芯 RAM / 快照 + diff / 任意区直读
│   │
│   ├── swdbg/                      **SWD 直读**：第二条观察通路，与串口 AA80 平行
│   │   ├── __init__.py             包出口
│   │   ├── probesel.py             该用哪支探针的解析器
│   │   ├── probe.py                管理芯 SWD 直读**门面**
│   │   ├── probe_jlink.py          上面那扇门的一支：J-Link 那一端
│   │   ├── probe_cmsis.py          上面那扇门的另一支：CMSIS-DAP 那一端
│   │   ├── breakpoint.py           断点这件事的全部：写法 / 源码侧事实 / 停核读局部量 / 注入
│   │   ├── gdbinit.py              **整片 .out 地图**：函数范围 / 指令流 / 调用点；一场会话建一次
│   │   ├── csrc.py                 C 源码结构索引：函数范围 / 某一行归哪个函数 / 名字在哪几行出现
│   │   ├── jlink.py                J-Link 那一端的枚举 / 体检与恢复
│   │   ├── restore.py              事故恢复：把被调试器停住的芯片重新放开运行
│   │   └── selfcheck.py            链路自检：一次连上，分三关验 探针 / 断点 / 观察点
│   │
│   └── discover/                   **通用探测器**：喂陌生表的 .out → 一份《探测报告》
│       ├── __init__.py                 包出口
│       ├── elf.py                  .out 里的一切：符号 + DWARF 类型
│       ├── source.py               符号 ←→ 源码足迹：谁在哪个 `文件:行号` 读写它
│       ├── doc.py                  从《对表操作总纲》抽机读部分：DI / OAD / 厂内前置 / 帧清单
│       ├── evidence.py             活体验证：借 swdbg 采样，判符号是『活』还是『静止』
│       ├── dossier.py              四路探测并成一份给人读的报告
│       └── scan.py                 找目标：一个目录 → 认出有哪几块表、各喂哪四样输入
│
├── project/                        **表卡带** = 配合这块表的**一切**
│   ├── __init__.py                 `profile.configure` = 『装卡带』的正式落点
│   ├── ez315_fm33a0610.py *        本表**工程画像**
│   │                               + **卡带布局声明**
│   ├── ez315_fm33a0610.meta.json * 本表**环境清单**：固件锁 / 双芯可达 / 串口 + firmware 指纹块
│   ├── ez315_fm33a0610.frames.json * 本表**已验证帧**叠加层
│   ├── firmware.py                 把卡带的固件声明读成探测要的输入
│   ├── env_check.py                装包即验
│   │
│   ├── tests/ *                    本表的测试脚本
│   │   ├── _suite.py                       **册子**：用例设定 + 逐个子项起子进程 + 最新日志报表；也在这儿核盘上的脚本有没有人认领、脚本给的 `wb` 键够不够库用
│   │   ├── _test_1_2_energy_mirror.py     1-2 电能数据·与计量芯保持一致
│   │   ├── _test_1_3_display_digits.py    1-3 电能数据·2/4 位小数、尾数
│   │   ├── _test_2_1_deviation_1s.py      2-1 同步时钟·偏差 ≤1s
│   │   ├── _test_3_1_rate_num.py          3-1 费率和时段·最多 12 费率
│   │   ├── _test_3_2_zone_slot_switch.py  3-2 当前、备用两套费率时段
│   │   ├── _test_4_1_freeze_ping.py       4-1 瞬时冻结
│   │   ├── _test_4_2_minute_frez.py       4-2 分钟冻结
│   │   ├── _test_4_3_hour_frez.py         4-3 小时冻结
│   │   ├── _test_4_4_day_frez.py          4-4 日冻结
│   │   ├── _test_4_6_aa80.py              4-6 结算日冻结·全量白盒
│   │   ├── _test_4_7_billfrez_y.py        4-7 阶梯结算冻结
│   │   ├── _test_5_2_overload.py          5-2 事件记录·过载
│   │   ├── _test_5_3_lostpower.py         5-3 事件记录·掉电
│   │   ├── _test_5_4_clear_meter.py       5-4 电表清零
│   │   ├── _test_5_5_clear_event.py       5-5 事件清零
│   │   ├── _test_5_6_program.py           5-6 编程事件记录
│   │   ├── _test_5_8_clock_error.py       5-8 时钟故障
│   │   ├── _test_5_9_relay_off.py         5-9 拉闸事件记录
│   │   ├── _test_5_10_relay_on.py         5-10 合闸事件记录
│   │   ├── _test_5_11_relay_fail.py       5-11 负荷开关误动作
│   │   ├── _test_7_1_frame_dispatch.py     7-1 通信·蓝牙·管理芯帧进出与派发
│   │   ├── _test_7_2_plc_relay.py         7-2 通信·载波
│   │   ├── _test_7_3_rs485_dispatch.py    7-3 通信·485
│   │   ├── _test_7_4_spi_link.py          7-4 计量芯 SPI 链路冒烟
│   │   ├── _test_8_7_lamp_wake.py         8-7 显示功能·通信唤醒点亮背光
│   │   ├── _test_9_2_rev_power.py         9-2 测量及监测·功率反向
│   │   ├── _test_9_3_overload.py          9-3 测量及监测·过载
│   │   ├── _test_10_1_keep_remote.py      10-1 费控·远程
│   │   ├── _test_11_1_year_step.py        11-1 年阶梯·未超档结转 / 超档清零
│   │   ├── _test_11_2_month_step.py       11-2 月阶梯·走月支 / 与年阶梯不混
│   │   ├── _test_12_1_keep_release.py     12-1 保电 / 解除
│   │   ├── _test_12_2_lcd_relay.py        12-2 液晶是否显示拉闸
│   │   ├── _test_12_3_auto_off.py         12-3 保电功能·解除后本地费控决定是否执行拉闸
│   │   ├── _test_13_1_auto_rpt.py         13-1 主动上报
│   │   ├── _test_16_1_prog_auth.py        16-1 软件要求·参数设置权限 / 编程记录 / 软件标识
│   │   └── _test_16_2_soft_compare.py     16-2 软件要求·软件比对
│   │
│   └── knowledge/ *                **本表私有知识卡带**
│       ├── 对表操作总纲.md            唯一知识文档：双芯寻址原文 + §10 对表补充
│       ├── cases71.json               AA80 用例的数据层
│       ├── 管理芯功能设计规范.md       **标准条文原文**：71 条各自「该做什么」，QND 10902 39/36/40 的条文照录，不作出处标注、不作实现状态与测试状态
│       ├── _whitebox_ledger/          71 子项规格
│       │   ├── ledger.md              **真源**：最前面两张 md 表 —— `## 表头规格`与 `## 通用规矩`，全部制表规矩就在这两张表里、一律写全文不编号；再每子项一节—— **改内容改这里**；这张表本身从哪来、怎么写一行，见同目录的 `怎么建.md`。⚠ 内容仍是**手改的**（改内容改它，不是改 `rows.py`），但它的一致性有检查器管：`mdsync.py check` / `items.py check` / `check.py`
│       │   ├── 简洁正确的测试思路.md    每子项的操作步骤：**脚本按它对齐**
│       │   ├── 结果.md                 每子项跑完的实测结果：步骤、原始数据、逐条判据的结论
│       │   ├── 4-1到4-4修改计划暂存.md   4-1 到 4-4 四份脚本按用户给的操作步骤改写、断点锚点统一 —— 挂起中的预期 diff
│       │   ├── 怎么建.md               **这张表本身**从哪来、怎么写一行：四个来源 / 七步 / 人要自己盯的地方
│       │   ├── items.py               末列「测试条目」这个规划位 ↔ 脚本 `*_criteria()` 对账；gen / sync / check / todo
│       │   ├── mdsync.py              ledger.md → rows.py 的流水线第一步；dump / build / check
│       │   ├── gen.py                 rows.py → 交付 xlsx（在 layout.xlsx 的版式种子上原位重写）
│       │   ├── show.py                不开 Excel 读 rows.py
│       │   ├── rows.py                **产物**，由 ledger.md 生成 —— 别直接改
│       │   ├── layout.xlsx            交付表的版式种子（行高 / 列宽 / 冻结 / 合并从它来）
│       │   ├── check.py               三方一致性：rows.py ↔ `_test_*.py` ↔ 对表操作总纲 §10.6
│       │   └── _verify.py             gen 产物 vs 现表逐 cell 对拍
│       └── 分析/                       固件缺陷分析
│           ├── 3-1_费率号计算与12费率上限_分析_2026-09-11.md
│           ├── 3-2_两套费率时段切换_Check_Switch吞错与AND绑死分析_2026-09-11.md
│           ├── 校时拨钟全死_事件容量0与AND绑死分析_2026-09-09.md
│           ├── 白盒4-5_约定冻结缺失分析_2026-09-09.md
│           ├── 白盒71项_自动分类方案_2026-09-09.md
│           └── 非3-2_顺路发现的问题台账_2026-09-11.md
│
├── machine/                        **装机卡带**
│   ├── __init__.py                 `machspec.configure` = 第二个组合根
│   ├── win11_c07751.py             本机事实的**唯一真源**
│   │                               ⚠ 依的 Python 包**不在这儿** —— 那是"软件依赖什么", 归仓根 pyproject.toml
│   ├── win11_c07751.meta.json      只声明**验收期望**，不重述路径
│   └── env_check.py                装包即验九节；全绿打印 `MACHINE CHECK: READY`
│
├── scripts/                             **入口脚本**
│   ├── _init_meter.py              探一块陌生表 → 一份《探测报告》
│   ├── _probe_all.py               批量探测：喂一个目录 / 一批 .out → 一镜像一份报告 + 索引
│   ├── _restore_all.py             总复位：跑完测试后一键把试验台收拾回正常态
│   ├── _check_bench.py                  台面体检：一条命令问清串口和 SWD 两条链路现在还通不通
│   ├── _check_aa80_vs_swd.py       两条观察通路等价对照：同一刻 AA80 vs SWD 逐字节比
│   ├── _install_gdb.py             按 MSYS2 依赖闭包把带 Python 的 gdb-multiarch 抓到本地
│   ├── _check_anchors.py           把测试脚本的断点拿去对源码，看要读的变量赋过值没有
│   ├── _check_fmt.py               % 格式化串里不合法的转换符
│   ├── _check_loghead.py           调用点不许在功能名里写日志头
│   ├── _backfill.py                **回填**：一次跑的结论补进总纲 §10.6 + 串跑三道派生检查
│   ├── watch_runner.py                  AA80 用例执行器
│   └── _check_readme.py            **本 README 的同步检查**：文件树 ↔ 真实仓库
│
├── 资料/                           过程笔记
│   ├── 1107计划.md                 按驱动模型重排整仓的提案 —— 内容已并入 README，本文件只剩指路牌
│   ├── log计划.md                  日志先补事件再谈格式：runlog 与 events 的分工
│   ├── 固件状况汇报.md             给审计会话的汇报格式规格
│   ├── 固件状况汇报_20260923.md    按上面那份规格产出的本轮汇报（71 子项两张表，只读审计）
│   ├── 管理芯固件源码/             管理芯固件源码侧的资料
│   │   └── 管理芯固件源码.md          IAR 虚拟文件夹文件树 + 已测出的固件问题
│   ├── 提示词脚本改造.md           把其余脚本改成 4-6 形状的提示词
│   ├── 原理.md                     SWD 背景访问的原理草稿
│   ├── 未命名.md                   「一个协议一个模块」的考证
│   ├── 未命名1.md                  过程笔记
│   ├── 锚点通用化.md               把 inject_anchor 那套从注入点专用放宽成锚点通用的方案
│   ├── 锚点落到4-6.md              锚点三种形态与 4-6 三个断点改成锚点后的实解
│   └── 图/                             体系图
│   │   ├── 一次子项跑的时序.drawio            一次子项从起跑到出结论的时序
│   │   ├── 一次子项跑的时序.png
│   │   ├── 体系全貌.drawio                    两条链路 × 四条通路 × 两个触发通道
│   │   ├── 体系全貌.png
│   │   ├── 体系全貌_v2.drawio                 同一张图的改版
│   │   ├── 体系全貌_v2.png
│   │   ├── 体系全貌_v2_full.png               上面那版的全尺寸导出
│   │   ├── 判据三态与退出码.drawio            PASS / FAIL / TBD 与退出码的对应
│   │   ├── 判据三态与退出码.png
│   │   ├── 观测对象与通路.drawio              每条通路能看什么、要不要停核
│   │   └── 观测对象与通路.png
│
├── templates/
│   └── test_script_template.py     新测试脚本的空骨架
└── .vscode/
    ├── launch.json                 调试配置；与装机卡带逐字段一致，由 machine / env_check 核
    └── settings.json               工作区设置：把 .git 从资源管理器默认排除项里放开
```

---

![[体系全貌_v2_full.png]]

---

### 例子

下面以 4-6「结算日冻结」为例，一次全流程。

**1. `trial.run_subitem(...)`（328 行）——总入口。** 脚本里写死的参数（分几段、判据、断点、超时）在这里交下去。它做三件事：把终端输出同时写进 `log/4_6_aa80_20260918_105517.log` 和同名 `.jsonl`，建 `Judge` 记录每条结果，把 `cmd_bank.bill_freeze_criteria` 里的四条判据读进来。不发表。

**2. 选串口（`portsel.open_com`）。** 机器卡带里没钉死 COM 号，按 `COM_MATCH`（vid 0x10C4 / pid 0xEA60 / desc CP210x）筛出候选，再逐个发一帧 698 读表钟（`p698.handshake_clock`）确认对端是这块表，只有一个口会应。日志：

```text
[串口] 判据 [vid=0x10C4 pid=0xEA60 desc=CP210x] 命中 1 个候选, 逐个证明『口后面是本表』…
  · COM3   应了, 表钟=2026-09-10 10:03:41
串口: COM3 (Serial) —— 真串口 | 桥 10C4:EA60 SN=0001 | 探活: 应了, 表钟=2026-09-10 10:03:41
```

**这一行是实测凭据本身**：桥的 VID:PID 与 USB 序列号、探活那一帧的结果，都是开串口那一刻
当场从设备上读的。没有它（口没开成 / 桥插着而表没应答）的那一轮，收尾时由外壳自动挪去
`log/未实测/`，回填也不收。

**3. `cmd_bank.enter_factory(ser)`（167 行）——串口。** 发 645 广播进厂内调试态；后面写结算日的 0x14 帧要求先处于这个状态。日志 `TX(19) 645.factory: FE FE FE FE 68 AA AA AA AA AA AA 68 1F 03 42 88 32 EA 16`，回 `9F 00 D5 16`。

**4. `cmd_bank.read_billday(ser)`（168 行）——串口。** 读表里当前的结算日。日志 `TX ... 68 11 04 34 3E 33 37 27 16` → `-> 时=0 日=5 → 每月5号0点`。这个 5 既是后面要改走再改回来的原值，也是自然跨结算日那一段的边界。

**5. `watch.named_blocks(*STABLE_NAMES)`（174 行）——只查名字，不发表。** 把变量名解析成内存地址和长度：先查表卡带里的 RAM_VARS，查不到再回退到 `.out` 的符号表。

**6. `watch.watch_vars(ser, CLOCK_NAMES, "时钟派生量基线")`（175 行）——串口 645。** 这一族读帧是 645 帧（CMD 0x11），数据标识用 `区号 AA 80 04`，负载是 4 字节区内偏移加 1 字节长度；按名字逐个读管理芯里的 RAM 变量。日志 `g_RateNo 04 51 AE`。

**7. `cmd_bank.read_freeze_row(ser, 0x05, 1)`（177 行）——串口 698。** 读结算冻结记录里最新的一条。日志 `-> 序号=85 冻结时标=2026-10-05 00:00:00`。85 是基线，后面每写一次只许加 1。

**8. `watch.watch_vars(ser, VARS, "改前基线")`（178 行）——串口 645。** 读 5 个变量：`g_HisTime 00 03 0A 0A 09 1A 6F BA`、`g_CurTime 00 03 0A 0A 09 1A 00 00`、`g_FrezAdr 62 00`、`g_FrezNum 0C 00`、`g_FrezLen 6F 02`。这几行不认领判据，只供人核对。

**9. `watch.aa80_ram_snapshots(ser, stable, "稳定态基线")`（179 行）——串口 645。** 把 9 个不该被结算碰到的全局变量各读一份存下来。

**10. `ctx.session()`（182 行）——SWD。** 到这一步才连调试器，因为一连上核心就停住。这一跑的台上是 J-Link，启动的是 `JLinkGDBServerCL -select usb=609788888 -device Cortex-M0 -if SWD -speed 1000 -port 2331` 和 `gdb-multiarch`（台子上换 DAPLink 时，这一句换成 `<当前解释器> -m pyocd gdbserver --no-config -W -u <UID> -t cortex_m -f 1000000 -p 2331 -O connect_mode=attach -O resume_on_disconnect=True -O soft_bkpt_as_hard=True`，其余步骤一字不变。**三个 `-O` 一个都不能少**：少 `attach` 会把表停住，少 `soft_bkpt_as_hard=True` 则每个断点都是空响的炮 —— 详见下面 `probe_*` 那一节。另外三项给的是**同一条命令里别的用处**，省了会各自换一种坏法：`-f` 是 SWCLK 频率，取**卡带 `SPEED`（kHz）× 1000** 得 Hz（卡带 `SPEED = 1000` ⇒ 这里写 `1000000`；卡带是 kHz 而 pyOCD 要 Hz，**不乘就会把时钟设成 1/1000**）；`-W` 不许 pyOCD 等探针，不然探针不在时这一句会挂在那儿等而不是当场报错；`--no-config` 掐掉「去当前工作目录找 `pyocd.yaml`」这条路，否则换台机器跑结果就跟着 cwd 变），载入 `.out` 的符号，装一个每次停下就复位看门狗（写 `0x40011400 = 0x12345A5A`）的钩子，清掉上一次没撤的断点，然后让核心继续跑。日志最后一行 `breakpoint: out=EZ315-FM33A0610EV-APP.out port=2331 断点=0 看门狗=0x40011400 注入白名单=关`。

**11. `ctx.bp(BP_A)` + `breakpoint.wait_hit(...)`（192、194 行）——SWD。** 在 `TaskFreeze.c:119`（`Run_TaskFreeze` 每分钟走一次的入口）下断点，然后让核心继续跑、等它自己命中——这一步没有任何帧可发。10:55:29.5 继续运行，10:55:36.76 断下（停了 7.26 s），复位看门狗后读 `msg=2 '\002'`（`MSG_MinStep`），撤掉断点继续跑。日志 `== 断点取证 断[A] 收口 Run_TaskFreeze:119: PASS`。这一条不认领判据，只是让人确认这条路径确实在走。

**12. `ctx.bp(BP_C)` + `breakpoint.fire_hit(g, bpC, cmd_bank.write_billday, ser, 6, ...)`（203、207-210 行）——SWD 断住，串口发帧。** 断点下在 `TaskFreeze.c:851`，`alt=6` 表示把结算日改成 6 号（固件只接受 1..28，而且值要和当前不同才走这条路径）。`fire_hit` 先让核心继续跑，再在后台线程里发 645 写结算日的 `0x14` 帧，主线程等断点命中：1.18 s 后停在 `Chg_BillDayM`，复位看门狗、读 `usekWh=0`、撤掉断点、继续跑，串口这时才收到 `68 94 00 CA 16`。日志 `== 断点取证 断[C] 链B 改到6 号: PASS`。断点选在 `:851` 而不是 `:842`，因为 `:851` 在「写存储失败就直接返回」那一段之后，命中就说明冻结记录真写进去了。

**13. `time.sleep(1.5)` → `cmd_bank.read_freeze_row(ser, 0x05, 1)` 和 `(…, 2)`（212-216 行）——串口 698。** 日志 `-> 序号=86 冻结时标=2026-09-10 10:04:00`，第二条是 `序号=85`（原来最新的一条）。

**14. `cmd_bank.bill_freeze_evidence(r1, base_seq=85, crit="④改日")`（217 行）——判据。** 序号正好等于 85+1 才算通过；只要求序号变大，漏写和重复写都能蒙过去。

**15. `cmd_bank.check_freeze_snapshot(ser, subclass=0x05)`（220 行）——串口 698。** 把冻结记录里的电量和当前电量分别读出来逐字节比对：记录侧用 GetRequestRecord 读 4 列（含组合、正向两个 47 字节整列），当前侧用普通 GET 读 `00000400`/`00100400`，另外检查 lead 位（组合=14、正向=15）。日志 `判定: PASS (字节一致=True, lead位=组合14/正向15; 载荷=全0 -> 0.0000kWh)`。

**16. `watch.watch_vars(ser, VARS, "链B 改到6 当刻")`（224 行）——串口 645。** 看变量里的取数时钟和刚读到的记录时标对不对得上。

**17. 第二轮写回 5 号（227-229 行）——SWD 断住，串口发帧。** 用 `drop=True` 复用上面那个断点，不重新下（管理芯的核是 SecurCore SC000，硬件断点比较器只有 4 个，重下要再占一个）。同样停在 `:851`，读 `usekWh=0`，放行后收到 `68 94 00 CA 16`。232-241 行把第 13 到 16 步再做一遍：序号 86→87，快照再比一次。

**18. `cmd_bank.read_billday(ser)`（245 行）——串口。** 读回 5 号才把「改过结算日」这个标记清掉；万一没读回来，交给最后的兜底步骤处理。

**19. `cmd_bank.read_clock(ser)` + `cmd_bank.next_billday_eve(ct, 5)`（252、253 行）——串口 698 + 本地计算。** 读表钟得 `2026-09-10 10:04:17`，算出下一个 5 号前一天的时刻 `2026-10-04 23:59:40`。

**20. `ctx.bp(BP_B)` + `breakpoint.fire_hit(g, bpB, cmd_bank.settle_across_master, ser, tgt, timeout=150, billday=5, ...)`（257-262 行）——SWD 断住，串口发帧。** 断点在 `TaskFreeze.c:823`（`Check_BillFrezM` 里写 `ID_MonthUsed` 的那一行，只有真的走了结算分支才会命中）。`settle_across_master` 做的事：进厂内 → 用 698 Set 把计量芯的表钟写到 `40000200`（`AF=0x15` 才回 `DAR=0`；主钟拨准后管理芯自己跟随）→ 回读两块芯片的时间，到分为止 → 每秒读一次表钟 → 10:56:18.09 表钟到 `2026-10-05 00:00:00` → 10:56:19.10 断点 B 命中（停了 24 s）→ 读 `usekWh=0` → 继续跑。

**21. `cmd_bank.read_freeze_row(ser, 0x05, 1)`（268 行）——串口 698。** `-> 序号=88 冻结时标=2026-10-05 00:00:00`；`cmd_bank.bill_freeze_evidence(rA, want_ts_day=..., crit=("①边界","③账期"))`（269 行）要求序号正好加 1、且时标日期等于结算日；`check_freeze_snapshot`（274 行）再比对一次；`watch_vars(VARS)`（278 行）看变量。

**22. `watch_vars(CLOCK_NAMES)` → `aa80_ram_snapshots` → `aa80_snap_diff`（279、280、285 行）——串口 645。** `g_RateNo` 从 `04 51 AE` 变成 `01 54 AB`（跨月导致的正常变化）；9 份快照逐份比较，日志 9 行 `diff: 无变化`。库的注释里写明这一条只作参考、不当判据：结算写的是存储不是 RAM，这些变量本来就不会跟着变。

**23. `g.close()`——SWD。** 释放调试器：断开 gdb 和 GDB Server，再读一次 `0xE000EDF0` 得 `01 00 00 01`，确认核心在运行。必须先把核心放开，后面的兜底步骤才发得出帧。

**24. 兜底 `cleanup_billday`（288-321 行）——串口。** 只有在「改过结算日又没读回 5 号」时才动手：进厂内、写回原来的日、再读一遍确认。兜底一旦跑起来就记一条 `ok=False`，这一轮判为失败——把表恢复回去是收拾现场，和功能对不对是两回事，不能相互顶替。

**25. 关串口 → `J.summary()` → 退出码（296-300 行）。** 日志末四行 `[满足] ①边界 / ②快照 / ③账期 / ④改日`，`观测: 串口 PASS(6/6) | 断点 PASS(4/4)`，`总: 通过`，`退出码: 0`，耗时 81.2 s。带 `--no-gdb` 跑时，断点那四步全都不做，结论是未定论、退出码 2，不是 0。

---



### breakpoint 的几种断点方法

断点一共有五种写法

  - （"文件", 行号）—— 行写法
  - （"line", 文件, 行号）—— 同上，带判别字的
  - （"call", 函数, 被调, n）—— 函数里第 n 处调用某个函数的之后那条指令
  - （"prev", 函数, 被调, n）—— 第 n 处调用之前那条指令
  - （"func", 函数名）—— 函数入口





  1. 行写法 ("TaskFreeze.c", 119)，规范形 ("line", "TaskFreeze.c", 119)。解它的是 gdb：Session.break_at(srcfile,
     line)（:1712）发 -break-insert "文件:行"，gdb 按行表把这一行解到它的首条指令。用在源码上没有"调用"这种结构可依的位
     置（要停在某个赋值语句上）。前提是那一行真编出了指令 —— 签名行 / { / 声明 / 注释 / 空行 / #define / case
     标号行都解不到，这正是『断点停不住』那类错在拦的事 —— 现在没有检查器盯它，改断点要人自己核。
  2. 调用锚点 ("call", "Run_TaskFreeze", "Get_MeterTime", 1)。落点是该函数里第 1 处 call Get_MeterTime
     的下一条指令（返回点）。运行侧由 gdbinit.map_of 从那张整片地图上查；源码侧的行号由 `.out` 的 DWARF
     行表反查（gdbinit.src_at），静态检查器走的是同一份换算。用在要表达"某次调用的落点"的位置 —— 写库、注入都属这类。
  3. 函数锚点 ("func", "Chg_BillDayM")。落点是函数入口，地址走
     Session.func_addr。只关心"进这个函数"、不关心进在哪一处时用它。



| 函数               | 位置                        | 干什么                    |
| ---------------- | ------------------------- | ---------------------- |
| `break_at`       | `src/swdbg/breakpoint.py` | 下硬件断点到 FPB             |
| `wait_hit`       | 同上                        | 等命中，命中即 halt           |
| `read_vars`      | `breakpoint.py:2885`      | 停住后读变量（含 DWARF 算局部量地址） |
| `read_regs`      | `breakpoint.py:2902`      | 停住后读寄存器                |
| `ensure_stopped` | 你提过，位置待确认                 | 直接叫停（不经断点）             |
| `inject_hit`     | 你提过（注入那 13 个值）            | 停核态受控写，写完放行            |




### breakpoint 与 watch 通过 ELF 进行寻址

**watch/断点都是“名字 → 地址”的解析，断点走代码地址，watch 走数据地址，两者都从 ELF 的符号表和 DWARF 里查。**


| 段 | 内容 | 给谁用 |
|---|---|---|
| `.symtab` | 函数名→地址、全局变量名→地址 | 符号级寻址（无调试信息也能用） |
| `.debug_info` (DWARF) | 函数、变量、类型、行号、栈偏移 | 行级断点、局部变量读取 |
| `.debug_line` | 行号 ↔ 地址映射表 | 行断点 |
| `.debug_loc` / `.debug_frame` | 变量在栈/寄存器里的位置 | 读局部变量 |

`symtab` 是简化版，DWARF 是详细版。断点和 watch 各自按需查。


**1. 函数断点 `break Run_TaskFreeze`**

```
查 .symtab 里 STT_FUNC 的 Run_TaskFreeze
  → 拿到 st_value = 入口地址（链接后已重定位）
  → 在该地址下断
```

如果只有 DWARF 没 symtab，就查 `DW_TAG_subprogram` 的 `DW_AT_low_pc`。

**2. 行断点 `break TaskFreeze.c:823`**

```
查 .debug_line 的 line table
  → 找 (file=TaskFreeze.c, line=823) 对应的地址序列
  → 取第一条（或所有）下断
```

一行可能对应多条指令地址，GDB 默认取第一条，`break` 时也可以指定 `:823` 落到哪个地址。

**3. 你脚本现在的 `("TaskFreeze.c", 119)`**

就是第 2 种。改成 `"Run_TaskFreeze"` 就是第 1 种。两者最终都变成**一个代码地址**，下到 CPU 的断点比较器（硬件）或替换指令（软件）。


**1. 全局变量 watch**

```
查 .symtab / DWARF 的 DW_TAG_variable
  → 全局变量有固定地址（DW_AT_location = DW_OP_addr 0x2000xxxx）
  → 直接对该地址下 watchpoint / 或轮询读
```

你脚本里 `watch.named_blocks` / `CURRENT.RAM_VARS` 做的就是这件事，只是地址是**硬编码在工程画像里**，没走 ELF 解析。如果改成从 ELF 查，流程一样：

```
变量名 g_HisTime
  → 查 ELF 符号表拿地址
  → AA80 直读该地址
```

**2. 局部变量 watch**

局部变量没有固定地址，它在**栈帧或寄存器**里：

```
查 DWARF 的 DW_TAG_variable
  → DW_AT_location 给的是“位置表达式”，如 DW_OP_fbreg -12（帧基址-12）
  → 必须在函数停住时，结合当前 SP/FP 才能算出真实地址
  → 所以局部变量只能在断点停住后读，不能提前下 watchpoint
```

这就是你脚本里 `VARS_A/B/C`（`msg`、`usekWh`）的机制：断点停住 → GDB 按 DWARF 算栈偏移 → 读值。


**断点：**

```
源码位置("TaskFreeze.c", 823) 或 函数名"Check_BillFrezM"
   │  DWARF .debug_line / .symtab
   ▼
代码地址 0x0800ABCD
   │  写入 CPU 断点比较器
   ▼
执行到该地址 → 停核
```

**全局 watch：**

```
变量名 g_CurTime
   │  .symtab / DWARF DW_OP_addr
   ▼
数据地址 0x20001A40
   │  轮询读(你脚本) 或 硬件 watchpoint
   ▼
拿到当前值 / 访问时停核
```

**局部 watch：**

```
变量名 usekWh
   │  DWARF DW_AT_location = DW_OP_fbreg -12
   ▼
需要当前帧基址 FP
   │  断点停住 → GDB 读 FP
   ▼
真实地址 = FP - 12 → 读值
```


能，而且现在已经在用一半了：

- **断点**：`ctx.bp(("TaskFreeze.c", 119))` 最终由 GDB 查 ELF 解析成地址。改成 `ctx.bp("Run_TaskFreeze")` 就走符号表。
- **全局 watch**：现在走 `CURRENT.RAM_VARS` 硬编码地址，**没走 ELF**。要改成 ELF 寻址，就是拿 `g_HisTime` 这个名字去 `.symtab` 查地址，再 AA80 读。
- **局部量**：`VARS_A/B/C` 已经在用 DWARF 了——断点停住后 GDB 按 DWARF 算偏移读值。



| | 断点 | 全局 watch | 局部 watch |
|---|---|---|---|
| 查什么 | .debug_line / .symtab | .symtab / DWARF DW_OP_addr | DWARF DW_AT_location |
| 得到 | 代码地址 | 固定数据地址 | 栈偏移（需 FP） |
| 何时可定址 | 加载即可 | 加载即可 | 必须停在该函数帧内 |
| 你脚本现状 | GDB 解析 | 硬编码 CURRENT.RAM_VARS | GDB 按 DWARF 读 |


**断点 = 名字/行号 → 代码地址（查 `.debug_line`/`.symtab`）；watch = 变量名 → 数据地址（全局查 `.symtab`/`DW_OP_addr`，局部查 `DW_AT_location` 再结合当前栈帧）。** 你要的“从 ELF 软连接寻址”，对全局量和函数断点都成立，对局部量则必须等断点停住才能算出真实地址。



---
## 本调试程序的库 API 

```apitree
├── common/                              中立层 · 两条通路都用 —— 不认表、不认协议、不认探针
│   ├── cli.py                           
│   │   └── guard_argv()                 脚本入口守卫 —— 不认得的参数 → 拒绝，不静默继续
│   ├── console.py                       
│   │   └── ensure_utf8_stdout()         控制台编码单点 —— 中文不乱码的唯一出口
│   ├── runlog.py                        
│   │   ├── run()                        跑一条命令并把 stdout / stderr 记进 log/
│   │   ├── stamp()                      给一行打时间戳
│   │   ├── strip_ts()                   剥掉时间戳 —— 读日志一律经它，不许各自写正则
│   │   ├── real_run_of()                这一行是不是实测 —— 要看得到「口后面是本表」的凭据
│   │   ├── proof_level()                那份凭据的成色：强 / 弱 / 没有
│   │   ├── quarantine()                 没凭据的那一份挪出 log / 根
│   │   ├── scan()                       扫一份日志里的子项结果
│   │   ├── scan_dir()                   扫一整个 log / 目录
│   │   └── 常量 13: STAMP_CONSOLE TS_PREFIX TAG_RUN TAG_END MARK_ALERT MARK_DEGRADED MARK_NOTE MARK_LEDGER_DEGRADED
│   │           MARK_SATISFIED MARK_FAILED MARK_UNPROVEN MARK_TOTAL MARK_NO_TOTAL
│   ├── events.py                        事件流，只给机器读
│   │   ├── emit()                       发一个事件
│   │   ├── bind() / unbind()            挂 / 摘一个订阅者
│   │   ├── pairing() / current_pairing()  配对关系 / 当下的配对
│   │   ├── enabled() / depth()          开关 / 当前嵌套深度
│   │   ├── why_not_bound()              没绑上时，说清是为什么
│   │   └── 常量 2: MAX_EVENTS RESERVED
│   ├── loglabel.py                      帧行 / 判定行 / 调试行的字形只在这里定一次
│   │   ├── Frame                         一帧报文，自带它属于哪个协议
│   │   ├── proto_of()                    从帧上取协议 —— 全仓唯一一处读 proto 的地方
│   │   ├── frame_line() / result_line()  一帧的日志行 / 一次操作的判定行
│   │   ├── debug_line()                  调试侧留痕的一行 [调试] [来源] 内容
│   │   └── 常量 10: PROTO_645 PROTO_698 PROTO_AA80 PROTOS VERDICT
│   │           DEBUG_GDB DEBUG_SRV DEBUG_SWD DEBUG_PROBE DEBUG_SOURCES
│   ├── hashfile.py                      
│   │   └── sha256()                      文件摘要
│   ├── cardslot.py                      
│   │   ├── Slot                          一块卡带槽
│   │   │   ├── configure()               给这块槽指定取哪一盘
│   │   │   ├── current()                 当下取的是哪一盘
│   │   │   ├── resolve() / need()        按名解析 / 缺了就报
│   │   │   ├── require()                 缺了直接停 —— 不许带着半个配方往下走
│   │   │   ├── clear()
│   │   │   ├── set_default_source()
│   │   │   ├── bootstrap()
│   │   │   ├── Proxy()
│   │   │   ├── has()
│   │   │   └── get()
│   │   └── resolve_in()
│   ├── profile.py                       本体就是那份画像数据，无公开函数
│   ├── machspec.py                      
│   │   └── ready()                       这台机器齐了没
│   ├── elfsym.py                        
│   │   ├── load()                        读进一个 .out
│   │   ├── configure() / configured_out()  指定 / 取回当下这个 .out
│   │   ├── ram_objects()                 列出所有落在 RAM 的全局
│   │   ├── addr_of()                     全局名 → 绝对地址
│   │   ├── find()                        按名找符号
│   │   ├── segments()                    各段
│   │   ├── image()                       取一段的字节
│   │   ├── wordsum()                     按字求和
│   │   ├── main()                         
│   │   └── 常量 1: CORE_WATCH
│   ├── varresolve.py                    变量名 → 地址的唯一实现
│   │   ├── configure()
│   │   ├── wire_from_profile()           按当前画像装配
│   │   ├── resolve()
│   │   └── blocks()
│   ├── jsonc.py                         带注释的 json
│   │   ├── loads() / load()              去注释后解析
│   │   ├── strip_comments()              只去注释
│   │   └── canon()
│   ├── judge.py                         判定这次测试证明了什么
│   │   ├── Judge                         一个子项的账本
│   │   │   ├── note()
│   │   │   ├── add()
│   │   │   ├── extend()
│   │   │   ├── feed_degradations()
│   │   │   ├── status()
│   │   │   ├── status_without_degradations()
│   │   │   ├── exit_code()
│   │   │   └── summary()
│   │   ├── decide()                      三态判决：True / False / None
│   │   ├── rec()                         造一条记录
│   │   ├── render()                      账本 → 给人读的一段
│   │   ├── crit_states() / obs_states()  判据 / 观测各自的账面
│   │   ├── crit_text() / crit_unprovable()  判据原文 / 被判为"证不了"
│   │   ├── claims() / injected() / obs_tag()  这一轮主张了什么 / 注过什么 / 观测打什么旗
│   │   ├── is_evidence()                 这条到底算不算证据
│   │   ├── orphans()                     +判据却没人认领的
│   │   ├── degradation()                 降级声明
│   │   ├── tri_eq() / tri_all()          三态相等 / 三态与
│   │   └── 常量 11: SERIAL DEBUG TRIG_INJECT STATUS_PASS STATUS_FAIL STATUS_TBD STATUS_NO_TOTAL EXIT PHASE_OBSERVE
│   │           PHASE_ENFORCE PHASE_DEFAULT
│   ├── portsel.py                       零协议：选口 + 收发，本机 USB-485 桥在哪、怎么用
│   │   ├── list_ports()                  系统当前认得的所有串口
│   │   ├── candidates()                   哪些口像管理芯
│   │   ├── describe()                    为什么挑它、别的为什么没挑上
│   │   ├── pick()                         按判据挑；绝不取第一个，多个候选交给带握手的那一方
│   │   ├── RawCom                        pyserial 打不开 / SetCommState 卡死时的 ctypes 直连
│   │   │   ├── read()
│   │   │   ├── write()
│   │   │   ├── flush()
│   │   │   ├── reset_input_buffer()
│   │   │   └── close()
│   │   ├── tx_recv()                     发一帧收应答
│   │   ├── open_com()                    开管理芯串口，线路参数从装机卡带取
│   │   ├── set_probe() / probe_installed()  探活的装配点 / 装上了没 —— 由协议层装进来
│   │   └── PortselError                  枚举不可用 / 判据不命中 / 候选含糊
│   ├── winpnp.py                        Windows 设备树
│   │   ├── devices() / scan_devices()    枚举 USB 设备
│   │   ├── problems() / problems_of()    有问题的设备 / 某个节点的问题
│   │   ├── all_usb_nodes() / parse_nodes()
│   │   ├── dropped_then_returned()       掉过又回来的
│   │   ├── summarize() / describe_node()
│   │   ├── restart_device() / cycle_device()  重启 / 循环上下电一个设备；`cycle_device` 以该节点自己回到 present 且 problem=0 判成功，不看 pnputil 退出码
│   │   ├── is_elevated()                 当下有没有管理员权限
│   │   └── PnPError
│   ├── snapdiff.py                      
│   │   └── snap_diff()                   两份快照求差 —— 串口与调试共用，所以住这儿
│   ├── trial.py                         每个 _test_*.py 的 main 都长它
│       ├── run_subitem()                 跑完一个子项并返回退出码 —— 脚本只调这一个
│       ├── Ctx                           段拿到的东西：串口 + 账本 + 会话开关 + 状态袋
│       │   ├── session()                 按需开调试会话
│       │   ├── trig() / trigger()        递触发回调；没会话时 trig 给 None
│       │   ├── inject() / with_inject()  注入回调，与 trig 成对；没会话时也是 None
│       │   ├── bp() / anchor()           现在就挂上断点 / 只把断点原样传给库
│       │   ├── hold() / take()           收库动词的三件套 / 只要记录
│       │   └── g()
│       └── Stop                          段里抛它 = 这一轮到此为止；收尾照常跑
│   ├── orchestrator.py                  只声明步骤与收尾；不 import 串口 / SWD / GDB 驱动
│   │   ├── Plan                           step 排步骤 / cleanup 排收尾 / run 交回 trial.run_subitem 的退出码
│   │   └── PlanError
│   └── bench.py                         
│       ├── check_env() / check_serial() / check_probe() / check_swd()   四步各答一问
│       ├── check_bench()                 逐步跑，不过就收摊；"没跑"与"跑了没过"在返回值里分得开
│       └── Step / Result / Report        步骤、一步的结论、一次体检的结论
├── meterlib/                            串口通路 · 645 · 698 · AA80
│   ├── p645.py                          只有 645
│   │   ├── frame_645()                   645 组帧：FE×4 + 68 + 地址 + 68 + 控制码 + 长 + 数据 + CS + 16
│   │   ├── validate_645()                逐字节校验一帧 645
│   │   ├── decode_645_reply()            645 应答 →
│   │   ├── read_time645_dt() / read_time645_date() / read_time645_time()
│   │   │       645 三种读时
│   │   ├── read_elect645_total()         645 读组合有功总电能
│   │   ├── read_temp645_e0()             645 0xE0 厂商读温度
│   │   ├── abs_to_aa80()            绝对地址 → 区内偏移，按地址所在区间自动归区
│   │   ├── read_aa80_645()               AA80 直读：走 645 的 C=0x11 读，DI 尾三字节 AA 80 04
│   │   ├── main()                         
│   │   └── 常量 12: P TABLE_ADDR BROADCAST_ADDR SRAM_BASE PERIPH_BASE INFO_BASE SRAM_TOP AA80_MAX_LEN
│   │           BILLDAY_DI_HEX BILLDAY_READ_DI CLEAR_645_ALL CLEAR_645_EV
│   ├── p698.py                          只有 698
│   │   ├── frame_698()                   698 组帧
│   │   ├── build_read_apdu()             读对象属性：05 01 <PIID> <OAD4> 00
│   │   ├── build_getrecord_apdu() / build_getrecord_apdu_oad()  GetRequestRecord / 任意 OAD 通用版
│   │   ├── record_req_oad()              规范 OAD → 记录型对象请求 OAD
│   │   ├── build_action_apdu()           操作对象方法：07 01 <PIID> <OMD4> [参数] 00
│   │   ├── build_set_apdu() / build_settime_apdu()  698 设置请求通用 / 写表钟 40000200
│   │   ├── crc_x25()                     CRC-16 / X.25，poly 0x8408 init 0xFFFF
│   │   ├── validate_698()                逐字节校验一帧 698
│   │   ├── split_apdu()                  从应答帧切出 APDU
│   │   ├── decode_action_ack() / decode_set_ack()  698 操作应答 / 设置应答 → DAR
│   │   ├── decode_ts_698() / decode_clock()  698 时标 / 读表钟应答 → 时间串
│   │   ├── decode_freeze_row()           冻结记录行 →
│   │   ├── dar_from_ud() / decode_getrecord_dar()  判"记录读回被拒"，取 DAR
│   │   ├── ud_record_count()             成功应答里的记录条数
│   │   ├── handshake_clock()             开表口：发一帧 698 读表钟证明口后面是本表，经 portsel.set_probe 装进中立层
│   │   └── 常量 19: P ADDR_698 KNOWN_READ_FRAME FREEZE_SUB FREEZE_RCSD EVENT_RCSD DAR REC_SEQ_OAD REC_TIME_OAD
│   │           ENE_COMB_FULL_OAD ENE_FWD_FULL_OAD CLOCK_SET_OAD FREEZE_OMD FREEZE_SUBCLASS_NAME EVENT_CODE_NAME
│   │           EVENT_REC_OAD EVENT_REC_SEQ_OAD EVENT_REC_TIME_OAD EVENT_REC_DEF_OAD
│   ├── cmd_bank.py                     最厚的一个
│       ├── 〔判过口径〕4
│       │       machine_verdict verdict_exit smoke smoke_verdict
│       ├── 〔发帧底座〕10
│       │       send_frame send send_frame_id chip_addr chip_of retarget_frame reload_overlay by_id
│       │       specs_for resolve
│       ├── 〔用例编排〕9
│       │       plan runcase cmd_list cmd_search cmd_show cmd_dry cmd_cases verbs cmd_verbs
│       ├── 〔厂内模式〕2
│       │       enter_factory exit_factory
│       ├── 〔表钟〕17
│       │       read_clock set_meter_clock_set clock_dt clock_add wait_second_at_least clock_sync_criteria
│       │       clock_sync_evidence clock_hold_evidence clock_stamp_evidence clock_offset_samples wall_offset_samples
│       │       calitime_bc645_evidence read_clock_error_event clock_error_criteria clock_error_inject_evidence
│       │       clock_error_clear_path_evidence ce_inject_allow
│       ├── 〔参量读写 645〕6
│       │       read_param_di write_param_di read_write_equal read_oad_ud write_oad_ud oad_ud_data
│       ├── 〔结算日〕8
│       │       read_billday write_billday billday_rw_roundtrip bill_freeze_criteria bill_freeze_evidence
│       │       next_billday_eve billfrez_y_criteria bfy_inject_allow
│       ├── 〔时区 / 时段表〕20
│       │       read_zone_tab_item write_zone_tab_item read_slot_tab write_slot_tab read_tab_whole zone_tab_whole_parse
│       │       zone_tab_item_data zone_tab_item_parse slot_tab_data slot_tab_parse which_slot_at rate_of active_slot_no
│       │       read_switch_frez seg_midpoints plant_bak_tabs restore_bak_tabs set_zone_slot_switch
│       │       read_zone_slot_switch zone_slot_switch_criteria
│       ├── 〔费率〕18
│       │       rate_no_decode read_rate_para read_rate_param write_rate_param rate_val dec_rate_val rate_at ymd_of
│       │       rate_trace_decode force_rate_recalc rate_num_limit_evidence rate_attribution_evidence
│       │       date_type_evidence rate_fallback_evidence rate_silent_default_evidence rate_write_fallback_evidence
│       │       rate_para_criteria rate_para_roundtrip
│       ├── 〔假日〕4
│       │       holiday_di read_holiday write_holiday erase_holiday
│       ├── 〔记录〕8
│       │       read_freeze_row read_event_row read_record_ud read_event_ud event_row3 event_advanced errflag_of
│       │       errflag_name
│       ├── 〔事件区 · 过载〕5
│       │       overload_criteria overload_roundtrip read_overload_rows ovl_neg_claims ovl_inject_allow
│       ├── 〔事件区 · 反向〕4
│       │       revpower_criteria revpower_roundtrip rvp_inject_allow rvp_neg_claims
│       ├── 〔事件区 · 失压〕4
│       │       lostpower_criteria lostpower_roundtrip read_lostpower_rows lp_inject_allow
│       ├── 〔事件区 · 继电器故障〕7
│       │       relayfail_criteria relayfail_roundtrip read_relayfail_rows relayfail_list_state rfl_inject_allow
│       │       rfl_top rfl_stat_expect
│       ├── 〔事件区 · 编程〕2
│       │       program_criteria program_roundtrip
│       ├── 〔事件区 · 年 / 月结算〕23
│       │       year_step_criteria year_step_inject_allow year_step_dose_allow year_step_intro
│       │       year_step_read_row year_step_read_raw year_step_neg year_step_gate year_step_advance
│       │       year_step_carry year_step_judge_carry year_step_over year_step_judge_over
│       │       month_step_criteria month_step_inject_allow month_step_intro month_step_read_row
│       │       month_step_gate month_step_neg month_step_leg_month month_step_judge_freznum
│       │       month_step_leg_year month_step_advance
│       ├── 〔事件区 · 保电〕24
│       │       keep_criteria keep_intro keep_goto_factory keep_read_sta3
│       │       keep_judge_table keep_judge_reply keep_judge_state_in keep_judge_state_is kp_ctrlstat_word
│       │       keep_judge_hold keep_judge_ctrlstat keep_judge_release keep_judge_sta3 kp_step_decode kp_state_txt keep_end_notes
│       │       keep698_criteria keep698_intro keep698_send keep698_timetag keep698_step_decode keep698_judge_action keep698_judge_timetag
│       │       keep698_end_notes
│       ├── 〔液晶拉闸 · 12-2〕14
│       │       lcd_relay_criteria lcd_relay_intro lcd_disp_decode lcd_norm_needed lcd_ladder_decode
│       │       lcd_probe_draw lcd_probe_blink lcd_window_obs lcd_judge_ladder lcd_judge_lz_low
│       │       lcd_judge_lz_high lcd_judge_ctrl lcd_relay_end_notes lcd_window_blocks
│       ├── 〔本地续拉 · 12-3〕11
│       │       auto_off_criteria auto_off_intro auto_off_walk_plan auto_off_neg_decode
│       │       auto_off_inject_intro auto_off_inject_decode auto_off_judge_neg auto_off_judge_ovr
│       │       auto_off_judge_keep
│       │       auto_off_end_notes ao_inject_allow
│       ├── 〔记账〕1
│       │       unproven_records
│       ├── 〔事件区 · 公共〕2
│       │       event_area_count resolve_event_baseline
│       ├── 〔继电器〕9
│       │       ctrl_relay ctrl_relay_reply relay_precheck kp_expect kp_reply_kind rcsd relay_off_criteria
│       │       relay_on_criteria relay_roundtrip
│       ├── 〔清零〕18
│       │       clear_meter clear_event clear_partition clear_meter_criteria clear_event_criteria clear_library_evidence
│       │       clear_freeze_evidence clear_elec_evidence clear_rebuild_evidence clr_event_send clr_event_dis_read
│       │       clr_event_accept_evidence clr_event_id_evidence clr_event_block_evidence clr_event_keep_evidence
│       │       clr_event_reject_evidence clr_event_gate_evidence clr_event_partial_probe
│       ├── 〔电能镜像〕27
│       │       kwh_rows kwh_slot_ok kwh_slot_val kwh_slot_units kwh_store_plan kwh_to_698val kwh_bswap64 kwh_crc2 kwh_num
│       │       kwh_wb_vars
│       │       energy_col_parse energy_ele_raw energy_ele_of energy_slot_zero record_energy_cols norm_energy47
│       │       check_freeze_snapshot freeze_settle_kwh_raw energy_mirror_criteria
│       │       energy_mirror_intro energy_mirror_multiframe energy_mirror_stable energy_mirror_store_decode
│       │       energy_mirror_crc energy_mirror_alloc energy_mirror_compare energy_mirror_fore
│       ├── 〔版本 / 显示〕25
│       │       sv_read_698_ver sv_read_698_ids sv_read_645_ver sv_read_645_fac sv_digits sv_digits_val
│       │       sv_digits_of_head sv_ascii sv_text sv_expected_appsum softver_criteria softver_roundtrip disp_oad
│       │       disp_scalar_ud disp_read_digits disp_gdispara_decode disp_digit_relation disp_digit_criteria
│       │       disp_digit_intro disp_digit_readings disp_digit_scale disp_digit_dote
│       │       disp_digit_borrow_floor disp_digit_borrow_write disp_digit_netzero
│       ├── 〔注入白名单〕2
│       │       inject_assigns inject_allow_names
│       ├── 〔语义动作〕2
│       │       settle_across_master resolve_decide
│       ├── 〔AA80 互验〕10
│       │       aa80_freeze_ping_criteria aa80_freeze_ping_intro aa80_freeze_ping_pre
│       │       aa80_freeze_ping_broadcast aa80_freeze_ping_post aa80_freeze_ping_readback
│       │       aa80_freeze_ping_diff aa80_freeze_ping_advanced
│       │       aa80_vs_swd_criteria aa80_vs_swd_compare
│       ├── 〔计量芯 SPI / 7-4〕6
│       │       spi_req_vars spi_land_vars spi_volt_from_ud spi_dt_key spi_link_criteria spi_link_roundtrip
│       ├── 〔杂项〕1
│       │       bcd8
│       └── main()
│   └── watch.py                         AA80 只读观察簇 —— 不 import cmd_bank
│       ├── WatchBank                一组观察点 + 快照 / 前后对比
│       │   ├── add() / add_specs()  挂一个观察点 / 一次挂一批
│       │   ├── snapshot()           读全部观察点 → {名: bytes|None}
│       │   ├── diff()               前后快照 → 哪些点变了
│       │   ├── errors()             这一轮没读成的点
│       │   └── fmt_shot()           快照 → 给人读的一段
│       ├── WatchPoint               一个观察点 = 一个源码全局
│       │   └── describe()                    它是谁、定在哪
│       ├── wait_change()                轮询到观察点有变化为止
│       ├── watch_vars()                  读一组按名登记的 RAM 变量 → {名: bytes|None}
│       ├── named_blocks()               变量名 → [(名, 绝对地址, 长)]
│       ├── aa80_ram_snapshots()         读一组绝对地址块
│       ├── read_mem_aa80()              按区、区内偏移、长直读内存
│       └── hexd()                       十六进制对照打印
├── swdbg/                               调试通路 · SWD
│   ├── probesel.py                      该用哪支探针的解析器
│   │   ├── candidates()                   两端的候选各是谁 → 逐端结果，扫过的每一端都留一行
│   │   ├── make_driver() / verify()       按端造驱动 / 真开一次会话读 CPUID 才算证明
│   │   ├── pick()                         恰好一支；0 支 / 多支都抛，且附逐条原因
│   │   ├── doctor() / main()              打印完整挑选过程
│   │   ├── ProbeSelectError
│   │   └── 常量 3: BACKENDS PROBE_CPUID_ADDR PROBE_CPUID
│   ├── probe.py                         绝不停核
│   │   ├── Probe                         一次 SWD 会话；进程崩了也由 atexit 兜底放行核心
│   │   │   ├── open() / close()          连接并 attach / 干净断开；close 回 True / False，关不掉时留着句柄可重试
│   │   │   ├── read_abs()                读绝对地址 size 字节
│   │   │   ├── read_many()               [] → {名: bytes|None}，单块失败记 None
│   │   │   ├── read_stable()             连读到两次相同才认 —— 表在跑，多字节读会撕裂
│   │   │   ├── halted() / dhcsr()        核心停住了吗
│   │   │   └── describe()
│   │   ├── ProbeError
│   │   └── 常量 6: SN DEVICE IFACE SPEED DHCSR RAM_BASE
│   ├── probe_jlink.py                   只做三件事：open / close / read_abs
│   │   ├── JLinkDriver                    pylink-square 那一端；BACKEND = "jlink"
│   │   ├── list_probes() / diagnose_brief()
│   │   ├── JLinkProbeError
│   │   └── 常量 2: BACKEND FROM_CARD
│   ├── probe_cmsis.py                   同样只做那三件事
│   │   ├── CmsisDapDriver                 pyOCD 那一端；BACKEND = "cmsis-dap"
│   │   │   └── connect_mode=attach        写死在 options 里 —— 默认 halt 会把表停住
│   │   ├── list_probes() / diagnose_brief()
│   │   ├── CmsisDapError
│   │   └── 常量 3: BACKEND PROBE_CPUID_ADDR PROBE_CPUID
│   ├── breakpoint.py                        写法 / 源码侧事实 / 停核读局部量
│   │   ├── Session                       一次 gdb + gdbserver 会话
│   │   │   ├── open() / close()          开局保证核心在跑、断点槽是空的；收尾复核核心真在跑；收尾中途出错不标记已关，下次 close 还能重试
│   │   │   ├── break_at()                下断点 → bp 号，断言真落在地址上
│   │   │   ├── clear_breaks()            删掉所有断点；⚠ 只在核心停住时能删
│   │   │   ├── go() / resume()           非阻塞 continue
│   │   │   ├── ensure_running() / ensure_stopped()
│   │   │   ├── wait_hit()                等一次命中 → Hit；超时不抛异常
│   │   │   ├── wait_break() / wait_only()  等指定那个断点 / 只等不触发
│   │   │   ├── with_trigger()            串口触发 + 断点取证的标准交错 —— 不这么绕会死锁
│   │   │   ├── with_inject()             停着触发：在 at 停住 → 下 watch 断点 → 写值 → 放行
│   │   │   ├── read_vars() / read_regs() 读一组表达式 / CPU 寄存器
│   │   │   ├── locals_all() / frame_info()  当前帧全部局部量 / 当前停在哪
│   │   │   ├── step_one_pc()             单步一条指令，落点 = 判定的实际走向
│   │   │   ├── inject() / restore_injections()  受控写一个变量 / 恢复回去
│   │   │   ├── inject_anchor() / decision_anchor() / callees() / break_at_anchor()
│   │   │   ├── feed_watchdog() / dog_feeds()  补喂狗
│   │   │   ├── breakpoints()
│   │   │   ├── inject_anchors()
│   │   │   ├── func_addr()
│   │   │   ├── stopped()
│   │   │   └── describe()
│   │   ├── session() / open_or_none()    开一次会话 / 开不了给 None 并说明原因
│   │   ├── Hit                           一次命中
│   │   │   └── where()
│   │   ├── trigger() / fire_hit()        有触发帧 / 自然到达
│   │   ├── expect_no_hit()               否定期望：窗口内不该命中
│   │   ├── inject_hit() / inject_miss() / inject_decide() / inject_hold()
│   │   ├── report() / record()           打成一行标准取证 / 返回 judge.rec 形状的记录
│   │   ├── break_at_or_none()            没接 J-Link 时给 None 而不是抛 —— 脚本照跑，白盒那半如实记没做成
│   │   ├── parse_mi() / mi_fields() / mi_unescape() / addr_tok()  解析 gdb MI 输出
│   │   ├── gdb_version() / GdbError
│   │   └── 常量 13: GDB_SERVER GDB SN DEVICE IFACE SPEED PORT MAX_HW_BREAK FP_CTRL_ADDR FP_COMP_ADDRS
│   │           DWT_COMP_ADDRS DWT_FUNC_ADDRS SETTLE_AFTER_RUN
│   ├── jlink.py                         只管 J-Link，该用哪支交给 probesel
│   │   ├── list_probes()                 枚举当下在连的 J-Link
│   │   ├── resolve_sn()                  按判据拿"该用哪支"
│   │   ├── diagnose() / diagnose_brief() 查清探针到底怎么了 —— 返回 dict 不是布尔
│   │   ├── plan_recovery()               由诊断算"该动什么" —— 只算不做，所以不碰硬件就能核
│   │   ├── recover() / doctor()          按诊断弄回来 / 打印完整诊断
│   │   ├── JLinkError
│   │   ├── main()                         
│   │   └── 常量 3: PROBE_VID FROM_CARD STATES
│   ├── restore.py                       
│   │   ├── release_debug()               把被撂在停住态的管理芯放开，并清空 FPB 断点槽
│   │   ├── kill_stray()                  结束还可能挂在后台的调试进程；⚠ 名单里永远不许有 python.exe
│   │   ├── run_jlink() / parse_dhcsr()   跑一段 J-Link Commander 脚本 / 解析 DHCSR
│   │   ├── describe() / utf8_stdout()
│   │   ├── main()                         
│   │   └── 常量 13: JLINK DEVICE IFACE SPEED SN DHCSR FP_CTRL FP_COMP DWT_COMP DWT_FUNC UNIT_SLOTS UNIT_REPORT STRAY_PROCS
│   ├── selfcheck.py                     
│   │   ├── Report.step()                 三关的判决与耗时，逐关打一行
│   │   ├── main()                         三关顺序是死的：第一关不过就地收摊
│   │   └── 常量 4: BP_FUNC WATCH_CANDS WAIT_BP WAIT_WP
├── scripts/                             入口脚本 · 用例数据驱动执行器
│   ├── _check_bench.py                  台面体检入口：一条命令问清串口和 SWD 两条链路现在还通不通
│   │   └── main()                         解析开关→ 调库 → 定退出码
│   └── watch_runner.py                  cases71.json → 一键跑
│       ├── load() / case_of()            读数据层 / 取一条 case
│       ├── build_banks()                 case 里注册的观察区名 → []
│       ├── run_reader()                  记录注册表 → 读回一行
│       ├── dispatch_semantic()           语义动作：cmd_bank 现成函数名 + kwargs → 判过
│       ├── run()                         跑一条 case；dry=True 只打印计划
│       ├── anchors_pending()             还有哪些断点没定
│       ├── main()
│       └── 常量 1: CASES71
└── discover/                            探测 · 与上面三条线正交 —— 探一块陌生表的内部结构
    ├── elf.py                           
    │   ├── Symbol                        一个符号：名字 / 地址 / 大小 / 落在哪一段
    │   │   ├── size_verdict()
    │   │   ├── size_agrees()
    │   │   ├── addr_agrees()
    │   │   └── as_dict()
    │   ├── symbols() / dwarf() / sections() / has_debug()
    │   ├── cross_check()                 两处对符号的说法对得上吗
    │   ├── describe() / sha256()
    │   └── main()
    ├── source.py                        
    │   ├── ewp_root() / resolve_ewp_rel() / ewp_files()   工程文件在哪、有哪些
    │   ├── index()                       建源码索引
    │   ├── usage() / summary()           一个符号在源码里被怎么用 / 汇总
    │   ├── main()
    │   └── 常量 3: SOURCE_EXTS CAP_WRITE CAP_READ
    ├── anchors.py                       断点在该变量的赋值之前还是之后
    │   ├── load() / functions() / enclosing()
    │   └── facts() / checks() / render()
    ├── scan.py                          
    │   ├── Target                          一个探测目标：固件 + 源码 + 工程
    │   │   └── report_path()
    │   ├── TargetResult
    │   │   └── name()
    │   ├── ewp_exe_paths() / ewp_candidates() / ewp_out() / src_root_of()
    │   ├── scan() / check_plan() / default_doc()
    │   ├── describe_target() / render_index() / write_index()
    │   ├── last_skipped()
    │   └── 常量 5: LAST_SKIPPED EXE_EXTS DEFAULT_DEPTH SKIP_MARK PRUNE_DIRS
    ├── doc.py                           
    │   ├── find_table() / extract()
    │   ├── main()
    │   └── 常量 7: HDR_FRAMES HDR_OADS HDR_SERVICES DI_RE OBJ_RE OAD_RE PROSE_SECTIONS
    ├── evidence.py                      
    │   ├── sample_once() / classify() / summarize()
    │   ├── main()
    │   └── 常量 4: STATIC SLOW FAST FAILED
    ├── dossier.py                       
    │   ├── build() / render() / write_report()
    │   ├── meter_name() / is_ram()
    │   ├── main()
    │   └── 常量 4: DEFAULT_DIR GAP_MIN TOP_N ROLE_RULES
```

### 体系全貌与依赖方向

```apitree
① 入口、两个并列，一个认表一个不认
     project/tests/_test_*.py      22 个测试脚本 —— 认表，走库动词
     scripts/*.py                  12 个入口工具 —— 不认表，跑一次就完，不被 import
         scripts/watch_runner.py   按 cases 文件遍历，像按设备树节点遍历

        │
        │  认表那一侧只认库动词，不自己碰帧、不自己碰地址
        ↓
── 以下是通用层：不认表、不认机器 ──

② 设备类驱动、meterlib —— 认协议、认设备类，不认表
     meterlib/cmd_bank.py
                               = 帧资源库 + 语义动词 + 71 项用例
                               ⚠ 三职一体；「71 项用例」是 ① 的东西，不该在这儿
                               ⚠ 另有整批十六进制字面量，是 ⑥ 的东西
                               —— 它现在认表了，这是违规，不是这层的属性

        │
        ↓
③ 总线、class 的 ops
     {变量名: bytes}               ⚠ 没有代码位置
                                   只写在 swdbg/__init__.py 的注释里
                                   于是想接第三条通路（IIC / SPI）时无处可实现对

        │
        ├── controller A、串口
        │       meterlib/p698.py     DL/T698.45   分帧 / 校验 / 解码
        │       meterlib/p645.py     DL/T645-07   分帧 / 校验 / 解码
        │       ⚠ 两个协议互不相认
        │       —— 这两行是 framer（Linux 的 line discipline、pymodbus 的 framer）：
        │          一个协议一个文件、同一副形状，插在传输之上、驱动之下。
        │
        └── controller B、SWD
                swdbg/probesel.py    认探针：两端枚举 → 选中哪支（唯一候选交给正式会话连上时证明）
                swdbg/probe.py       门面：按地址读内存（绝不停核）—— 两端共用这一套 API
                swdbg/probe_jlink.py J-Link 那一端（pylink-square）
                swdbg/probe_cmsis.py CMSIS-DAP 那一端（pyOCD）—— connect_mode 必须显式 attach
                swdbg/breakpoint.py      断点级（要停核）
                swdbg/restore.py     停核之后必须还原 —— remove() 的那半个

        │
        │  两条都只认「一个口的字节进出」
        ↓
④ 传输、tty_driver
     common/portsel.py             选口 + tx_recv + open_com
     ⚙ 装配点、全仓唯一一处
          p698.handshake_clock  →  portsel.set_probe()
          收下之后，portsel 不再知道 698、不再知道帧

── 以上是一条链：运行期，每次发帧都走 ──

── 以下是一条链：构建期，跑一次，产物是数据 ──
⑤ probe()、造设备树的东西
     discover/*.py                 吃 .out / 源码 / 总纲 → 造出画像
                                   挂在入口工具上跑：scripts/ 的 _check_anchors /
                                   _init_meter / _probe_all；运行期那条链一个都不碰它
     ✅ 产物是数据不是代码，这条守住了
     ⚠ 硬绑 swdbg.probe 这一种 controller，没有对总线说话

        ↓
⑥ 设备树（卡带）、 数据，不是层
     project/ez315_fm33a0610.py    这块表：双芯 AF / 表号 / RAM 基址 / 变量地图 / 固件事实
     project/knowledge/            规格真源 / 对表总纲 / 探测报告
     machine/win11_c07751.py       这台机器：口名 / J-Link 序列号 / 路径
     ✅ 纯数据，文件头明写「不放函数 / 逻辑」
     ⚠ 画像太薄 —— ② 那批字面量本该在这儿

── 以下是一条带：谁都能调，不接在任何一条链上 ──
⑦ 中立层、相当于 kernel/lib/
     events   runlog   console   cli    judge    profile   varresolve
     snapdiff cardslot elfsym    jsonc  hashfile machspec  winpnp
     trial                          子项运行外壳：包着 ① 的 main 跑 —— argv / 日志 /
                                    账本 / 串口 / 会话 / 收尾三连 / 退出码
     ✅ 谁都指向它，它谁都不指
     ⚠ 一旦长出「核心」，它就开始变成子系统，中立性立刻没了
```

```apitree
        project(表卡带)   ──┐
                            ├──→  common(中立层)  ←──  meterlib(协议引擎)
        machine(装机卡带) ──┘         ↑              ←──  swdbg(SWD 直读)
                                      └────────────  ←──  scripts / discover
```



---

### 串口通路 `src/meterlib/` 的表

上面那棵树负责答「它在哪个文件」；这一节答「它干什么、为什么非得这么设计」。
名字全部从源码 AST 实取，不是我手打的。

#### `p645.py` / `p698.py` —— 两个协议各一个文件（第 0 层 · 原语）

**一个协议一个文件**：645 的一切住 [`p645.py`](src/meterlib/p645.py)，698 的一切住 [`p698.py`](src/meterlib/p698.py)。
两个文件里都**没有电表业务词**：帧怎么拼、CRC 怎么算、应答怎么切、标准号是哪些。

**串口那一半不在这儿** —— 开、发、收、选口搬去了 [`common/portsel.py`](src/common/portsel.py)。
这是本次拆分的设计意图：**串口既不属于 645 也不属于 698，它在它们下面**（零协议的中立层）。
同理，"**口后面是不是本表**"那半截也不在 `common`（那要发一帧去问，是协议动作），它住在 `p698.py`。

#### `p645.py` —— DL/T645-07

| 分组 | API | 作用 |
|---|---|---|
| 组帧 | `frame_645()` | 645 组帧（`FE×4` + `68` + 地址 + `68` + 控制码 + 长 + 数据 + CS + `16`；数据域线上编码 = 每字节值 `+0x33`） |
| 校验 | `validate_645()` | 逐字节校验一帧（双 `68` / 长度 `L` / CS 累加低字节 / 尾 `16`；容忍前置 `FE×4`） |
| 解码 | `decode_645_reply()` | 645 应答 →（控制码，数据段，错误码） |
| 读时 | `read_time645_dt()` | 645 读 日期 + 时间 |
| | `read_time645_date()` / `read_time645_time()` | 645 只读日期 / 只读时间 |
| 读量 | `read_elect645_total()` | 645 读组合有功总电能 |
| | `read_temp645_e0()` | 645 `0xE0` 厂商读温度（s16 小端） |
| AA80 | `abs_to_aa80()` | 绝对地址 →（区，区内偏移），按地址所在区间自动归区 |
| | `read_aa80_645()` | **AA80 直读这个寻址入口**：走 645 的 `C=0x11` 读、DI 尾三字节 `AA 80 04`，负载 = 4B 区内偏移（LE）+ 1B 长度 |

> AA80 为什么在 645 里、而不独立成第三个协议文件：看组帧就知道 —— 它走的就是 645 的 `C=0x11` 读，
> 自带的只有「绝对地址 →（区号，区内偏移）」这条折算。「一个协议一个文件」说的是**协议**，
> 不是**厂商留的远程调试通道**（`p645.py` 文件头写着这条边界）。

#### `p698.py` —— DL/T698.45

| 分组 | API | 作用 |
|---|---|---|
| 组帧 | `frame_698()` | 698 组帧（补 `68 L C` + 表地址 + HCS + FCS + `16`） |
| | `build_read_apdu()` | 读普通对象属性：`05 01 <PIID> <OAD4> 00` |
| | `build_getrecord_apdu()` | GetRequestRecord（冻结） |
| | `build_getrecord_apdu_oad()` | 同上的通用版，OAD 任意给 |
| | `record_req_oad()` | 规范 OAD → 记录型对象请求 OAD |
| | `build_action_apdu()` | 操作对象方法：`07 01 <PIID> <OMD4> [参数] 00` |
| | `build_set_apdu()` | 698 Set-Request-Normal 通用 |
| | `build_settime_apdu()` | 698 写表钟 `40000200`（OOPT 成功校钟的命令形） |
| 校验 | `crc_x25()` | CRC-16/X.25，`poly 0x8408 init 0xFFFF` |
| | `validate_698()` | 逐字节校验一帧 698（L / HCS / FCS / 尾 `16`；容忍前置 `FE×4`） |
| 解码 | `split_apdu()` | 从应答帧切出 APDU（去掉 `68 L C 8addr HCS` 与 `FCS+16`） |
| | `decode_action_ack()` | 698 操作应答 →（DAR，数据） |
| | `decode_set_ack()` | 698 设置应答 → DAR |
| | `decode_clock()` / `decode_ts_698()` | 698 读表钟应答 / 698 时标 → 时间串 |
| | `decode_freeze_row()` | 冻结记录行 →（记录序号，冻结时标） |
| | `dar_from_ud()` / `decode_getrecord_dar()` | 从用户数据里取 DAR / 判「记录读回被拒」，取 DAR 值 |
| | `ud_record_count()` | 成功应答里的记录条数 |
| 开表口 | `handshake_clock()` | **「口后面是不是本表」那半截**：按本表服务器地址单播读一帧表钟，读得回才算数；经 `portsel.set_probe()` 装进中立层（装配点，与 `common/varresolve` 的 configure 同一套做法） |

**对象模型**：记录 OAD / 冻结子类名 / 事件编码与事件记录 OAD / 标准对象号，
以模块级常量收在本文件里（`REC_SEQ_OAD`、`FREEZE_SUBCLASS_NAME`、`EVENT_REC_OAD`、`DAR` 表…共 19 个）。

> ⚠ 换通路（比如 IIC）时要换的是 `handshake_clock` 这一句，不是整个 `open_com` —— 所以它必须住在协议层，
> 不能住进 `common`（`p698.py` 文件头写着这条边界）。

#### `watch.py` —— AA80 只读观察簇（第 1 层）

按名读管理芯 RAM，CPU 全程不停、不掉 8s 看门狗。
它不与语义动作同住，一条通道一个文件。
脚本直接 `from meterlib import watch` 再 `watch.watch_vars(...)`。

它只 import `common.*` 与 `meterlib.p645`，不 import `cmd_bank`：反向 import 会成环。
为此两样零协议的薄壳已同时下沉到中立层 —— `common.portsel.send_frame`（现成串口才发，否则抛）与
`common.loglabel.opout`（判定行的出口）。`cmd_bank` 对它是使用者：内部调用点写 `watch.xxx(...)`。

另有两个名字是本文件从别处取来的，不是它自己定义的：`aa80_snap_diff` 取自 `common/snapdiff.py`，
`abs_to_aa80` 取自 `p645.py`。

| API | 作用 |
|---|---|
| `WatchBank` | 一组观察点 + 快照 / 前后对比 |
| ├ `add()` / `add_specs()` | 挂一个观察点 / 一次挂一批 |
| ├ `snapshot()` | 读全部观察点 → `{名: bytes\|None}` |
| ├ `diff()` | 前后快照 → 哪些点变了 |
| ├ `errors()` | 这一轮没读成的点 |
| └ `fmt_shot()` | 快照 → 给人读的一段 |
| `WatchPoint` | 一个观察点 = 一个源码全局的（区，偏移，长）；定址三选一 |
| └ `describe()` | 它是谁、定在哪 |
| `wait_change()` | 轮询到观察点有变化为止 |
| `watch_vars()` | 读一组按名登记的 RAM 变量 |
| `aa80_ram_snapshots()` | 读一组绝对地址块 |
| `named_blocks()` | 变量名 → `[(名, 绝对地址, 长)]` |
| `aa80_snap_diff()` | 两份快照逐块求差 |
| `read_mem_aa80()` | 按（区，区内偏移，长）直读内存 |
| `hexd()` | 十六进制对照打印 |

#### `cmd_bank.py` —— 帧资源库 + 语义动词 + 用例层

这一个文件干三件事，是整仓最厚的一个。它的定位写在文件头：**把所有要发的帧收敛成「数据」—— 帧是资源，不是代码**。

其中 `send_frame` / `_opout` 两个名字是从 `common` 取来的（`portsel.send_frame` / `loglabel.opout`），
不是本文件定义的。它们零协议，下沉到中立层之后 watch 通路才用得着而不成环。调用点仍写
`send_frame(...)` / `_opout(...)`，名字照旧。

**〔判过口径〕4** —— 全仓唯一一份

| API | 作用 |
|---|---|
| `machine_verdict()` | 一帧应答 → PASS / TBD / FAIL + 理由 |
| `verdict_exit()` | 判过词 → 进程退出码；send / runcase / smoke / 测试脚本共用 |
| `smoke()` | 在线冒烟：逐帧发，只证「链路 + 组帧 + 应答结构」健康 |
| `smoke_verdict()` | 冒烟结果 → 判过 |

**〔发帧底座〕10**

| API | 作用 |
|---|---|
| `send_frame()` | 底层一轮收发，发任意已组好的帧（698 / 645 均可）；定义在 `common/portsel.py` |
| `send()` | 发一帧并打帧行 + 自动判过 |
| `send_frame_id()` | 按 id 取预置帧发出去 |
| `chip_addr()` | 管理芯 / 计量芯各自的 698 地址 |
| `chip_of()` | 一帧是发给哪颗芯的 |
| `retarget_frame()` | 换芯：AF `05↔15` 并重算 HCS / FCS |
| `reload_overlay()` | 重载叠加层（换表 / 换机器后重新取配方） |
| `by_id()` | 按 id 取一条帧资源 |
| `specs_for()` | 取某一类的全部帧资源 |
| `resolve()` | 解析帧资源里的引用 |

**〔用例编排〕9**

| API | 作用 |
|---|---|
| `plan()` | 一个用例的帧步骤 → 计划（不跑） |
| `runcase()` | 按序跑一个用例的帧步骤（整个用例只开一次串口） |
| `cmd_list()` | 列出所有可发的命令 |
| `cmd_search()` | 按词搜命令 |
| `cmd_show()` | 看一条命令的原文 |
| `cmd_dry()` | 只打印计划（含每帧 hex） |
| `cmd_cases()` | 列可用用例 |
| `verbs()` | 列出当下有哪些语义动词（首参是 `ser` 的那些），**不写死清单** |
| `cmd_verbs()` | 同上，走命令形 |

**〔厂内模式〕2**

| API | 作用 |
|---|---|
| `enter_factory()` | 进 645 广播 `0x1F` 工厂模式 |
| `exit_factory()` | 出工厂模式 |

**〔表钟〕17**

| API | 作用 |
|---|---|
| `read_clock()` | 698 读表钟 |
| `set_meter_clock_set()` | 698 写表钟 |
| `clock_dt()` | 表钟当前值 → datetime |
| `clock_add()` | 在表钟上加一段（造「未来」用） |
| `wait_second_at_least()` | 等到至少跨过 N 秒（避开边界抖动） |
| `clock_sync_criteria()` | 校钟判据 —— 内容取自 `ledger.md` 校钟那一节的「观察与判据」+「操作步骤」 |
| `clock_sync_evidence()` | 校钟证据 |
| `clock_hold_evidence()` | 校钟期间「钟没被改回去」的证据 |
| `clock_stamp_evidence()` | 时标证据 |
| `clock_offset_samples()` | 表钟与墙钟的偏差点采样 |
| `wall_offset_samples()` | 墙钟侧偏差点采样 |
| `calitime_bc645_evidence()` | 广播校钟 645 证据 |
| `read_clock_error_event()` | 读「时钟错误」事件 |
| `clock_error_criteria()` | 时钟错误判据 |
| `clock_error_inject_evidence()` | 注入造时钟错误 → 证据 |
| `clock_error_clear_path_evidence()` | 时钟错误被清掉的路径证据 |
| `ce_inject_allow` | 时钟错误的注入白名单 |

**〔参量读写 645〕6**

| API | 作用 |
|---|---|
| `read_param_di()` | 645 `0x11` 读参量 |
| `write_param_di()` | 645 `0x14` 写参量 |
| `read_write_equal()` | 写进去再读回来是否相等 —— 「这个 OAD 到底能不能写」的标准手段 |
| `read_oad_ud()` | 698 读对象 → 用户数据 |
| `write_oad_ud()` | 698 写对象 |
| `oad_ud_data()` | 从读回的报文里取用户数据段 |

**〔结算日〕8**

| API | 作用 |
|---|---|
| `read_billday()` | 读结算日参数 |
| `write_billday()` | 写结算日 |
| `billday_rw_roundtrip()` | 结算日读写全流程 + 判据（写参量能力自证） |
| `bill_freeze_criteria()` | 结算冻结判据 |
| `bill_freeze_evidence()` | 结算冻结证据 |
| `next_billday_eve()` | 下一次结算日的前夜（造「跨结算」用） |
| `billfrez_y_criteria()` | 年结算冻结判据 |
| `bfy_inject_allow` | 年结算冻结的注入白名单 |

**〔时区 / 时段表〕21**

| API | 作用 |
|---|---|
| `read_zone_tab_item()` | 读时区表单项 |
| `write_zone_tab_item()` | 写时区表单项 |
| `read_slot_tab()` | 读时段表 |
| `write_slot_tab()` | 写时段表 |
| `read_tab_whole()` | 整表读回 |
| `zone_tab_whole_parse()` | 整张时区表 → 结构 |
| `zone_tab_item_data()` | 时区单项 → 字节 |
| `zone_tab_item_parse()` | 时区单项 → 结构 |
| `slot_tab_data()` | 时段项 → 字节 |
| `slot_tab_parse()` | 时段项 → 结构 |
| `which_slot_at()` | 某时刻落在哪个时段（纯函数） |
| `rate_of()` | 某时刻属于哪个费率（纯函数） |
| `active_slot_no()` | 当下生效的时段号 |
| `read_switch_frez()` | 读「时段切换冻结」 |
| `seg_midpoints()` | 各时段的中点（取点采样用） |
| `plant_bak_tabs()` | 写入备份表 |
| `restore_bak_tabs()` | 还原备份表 |
| `set_zone_slot_switch()` | 写时区时段切换开关 |
| `read_zone_slot_switch()` | 读该开关 |
| `zone_slot_switch_criteria()` | 该开关的判据 |

**〔费率〕18**

| API | 作用 |
|---|---|
| `rate_no_decode()` | 当前费率号的原始字节 → 值（读在脚本） |
| `read_rate_para()` | 读费率参数 |
| `read_rate_param()` | 读费率参数（另一种粒度） |
| `write_rate_param()` | 写费率参数 |
| `rate_val()` | 费率值编码 |
| `dec_rate_val()` | 费率值解码 |
| `rate_at()` | 某时刻的费率（纯函数） |
| `ymd_of()` | 时间 → 年月日（纯函数） |
| `rate_trace_decode()` | 那次 AA80 读回的原始字节 → 费率镜像（读由脚本做，追踪） |
| `force_rate_recalc()` | 逼表重算费率 |
| `rate_num_limit_evidence()` | 费率个数上限证据 |
| `rate_attribution_evidence()` | 费率归属证据（这个量算在哪个费率上） |
| `date_type_evidence()` | 日期类型证据 |
| `rate_fallback_evidence()` | 费率回退证据（AA80 镜像读由脚本给） |
| `rate_silent_default_evidence()` | 静默默认费率证据（EEPROM/镜像读由脚本给） |
| `rate_write_fallback_evidence()` | 写入回退证据（EEPROM 读由脚本给） |
| `rate_para_criteria()` | 费率参数判据 |
| `rate_para_roundtrip()` | 费率参数全流程 |

**〔假日〕4**

| API | 作用 |
|---|---|
| `holiday_di()` | 假日 DI（645 数据标识） |
| `read_holiday()` | 读公共假日 |
| `write_holiday()` | 写公共假日 |
| `erase_holiday()` | 清公共假日 |

**〔记录〕8**

| API | 作用 |
|---|---|
| `read_freeze_row()` | 698 读冻结记录一行 |
| `read_event_row()` | 698 读事件记录一行 |
| `read_record_ud()` | 读记录 → 用户数据 |
| `read_event_ud()` | 读事件 → 用户数据 |
| `event_row3()` | 事件记录第三行（固定取法） |
| `event_advanced()` | 事件是否已推进（新事件来了没） |
| `errflag_of()` | 错误标志取值 |
| `errflag_name()` | 错误标志取名字 |

**〔事件区〕31** —— 八个子区共用一套四件：判据 / 全流程 / 读回 / 注入白名单

| 子区 | API | 作用 |
|---|---|---|
| 过载 | `overload_criteria()` / `overload_roundtrip()` / `read_overload_rows()` / `ovl_inject_allow` | 判据 / 全流程 / 读回 / 注入白名单 |
| | `ovl_neg_claims()` | 否定期望：本不该有过载记录时要主张的那几条 |
| 反向 | `revpower_criteria()` / `revpower_roundtrip()` / `rvp_inject_allow` / `rvp_neg_claims` | 同上 |
| 失压 | `lostpower_criteria()` / `lostpower_roundtrip()` / `read_lostpower_rows()` / `lp_inject_allow` | 同上 |
| 继电器故障 | `relayfail_criteria()` / `relayfail_roundtrip()` / `read_relayfail_rows()` / `rfl_inject_allow` | 同上 |
| | `relayfail_list_state()` | 继电器故障列表当下的状态 |
| | `rfl_top()` / `rfl_stat_expect()` | 按序号归位后的当前最大序号 / 按「命令 + g_RelayFlg 低 4 位」现算该当的 `stat` |
| 编程 | `program_criteria()` / `program_roundtrip()` | 判据 / 全流程 |
| 年 / 月结算 | `year_step_criteria()` / `year_step_inject_allow` / `year_step_dose_allow` | 年步进：判据 / 注入白名单 / 「投什么量」的白名单 |
| | `year_step_intro()` | 第一步 · 开场说明(只打印) |
| | `year_step_read_row()` / `year_step_read_raw()` | 第二步/第五步 · 黑盒读记录最新一条 / 读那一列原始字节 |
| | `year_step_neg()` | 第三步 · ② 白盒半边: 普通趟里两个写点都不该命中 |
| | `year_step_gate()` | 第四步 · ① 判: 风格判定断点停到 + style==TP_Local |
| | `year_step_advance()` | 第五步 · ② 黑盒半边: 记录序号不推进 |
| | `year_step_carry()` / `year_step_judge_carry()` | 第六/七步 · ③ 结转那趟(注入 + 等 `:1008`) / 判那一列原样保留 |
| | `year_step_over()` / `year_step_judge_over()` | 第八/九步 · ④ 超档那趟(注入 + 等 `:1012`) / 判清零点命中且那列已清 |
| | `month_step_criteria()` / `month_step_inject_allow` | 月步进：判据 / 注入白名单 |
| | `month_step_intro()` | 第一步 · 开场说明(只打印) |
| | `month_step_read_row()` | 第二步/第五步 · 黑盒读最新一条(第二趟改 `tag`) |
| | `month_step_gate()` | 第三步 · ① 判: 风格判定断点停到 + style==TP_Local |
| | `month_step_neg()` | 第四步 · ② 白盒半边: 普通分钟步进里月支写点不该命中 |
| | `month_step_leg_month()` | 第五步 · ②③ 第一趟: 注入月形态 ⇒ 月支写点应命中 |
| | `month_step_judge_freznum()` | 第六步 · ③ 判: `frezNum` 按月差算 |
| | `month_step_leg_year()` | 第七步 · ④ 第二趟: 再造月形态 ⇒ 年支写点仍不该被走到 |
| | `month_step_advance()` | 第八步 · ⑤ 判: 黑盒读回记录推进 |
| 保电 | `keep_criteria()` | 判据 |
| | `keep_intro()` | 第一步 · 开场说明(只打印) |
| | `keep_goto_factory()` | 第二步 · 进厂内(0x3A/0x3B/0x1A 不在密码 bypass 里) |
| | `keep_read_sta3()` | 第三步 · 读一次运行状态字3(645 DI 04000503); 命令状态与控制状态字那两次由脚本自己 `watch.watch_vars` |
| | `kp_state_txt()` / `kp_ctrlstat_word()` / `kp_step_decode()` | 脚本**读完之后**: 读数写成文本 / 解 `g_CtrlStat[1]` / 装配本步的 `step`(纯解码, 一次串口都不碰) |
| | `keep_judge_table()` / `keep_judge_reply()` | 判: 裁决逐格对表 / 对外应答(该受理的受理、该拒的拒) |
| | `keep_judge_state_in()` / `keep_judge_state_is()` / `keep_judge_hold()` | 判: 命令状态落进那一格 / 等于表值 / 原地不动 |
| | `keep_judge_ctrlstat()` / `keep_judge_release()` | 判: 被拒的原因是 ER_RlyOffKeep / 放行跨到拉闸侧 |
| | `keep_judge_sta3()` | 判: 保电位在运行状态字3 上置位、解除后清零 |
| | `keep_end_notes()` | 收尾写明本次够不到的那两块 |
| 记账 | `unproven_records()` | 半途中止时把没做成的条目一次记全(`ok=None`, 绝不当 FAIL); 条目表由脚本给, 每条带观测种类 |
| 液晶拉闸(12-2) | `lcd_relay_criteria()` | 判据 |
| | `lcd_relay_intro()` | 开场: 本次走哪两种观测(只打印) |
| | `lcd_disp_decode()` | 脚本读回的 `g_DispStatus` → 在不在 `Disp_Others` 的运行窗口(1..6) |
| | `lcd_norm_needed()` | 脚本读回的起点 → 要不要先解除保电(起点在合闸保电(9) 时阶梯第 1 级走不下去) |
| | `lcd_ladder_decode()` | 脚本发过帧也轮询过之后: 这一步落进目标态那一片没有 |
| | `lcd_probe_draw()` / `lcd_probe_blink()` | 断[A] 一次(非合闸态读 `Curr_Value` / 合闸态等"不该被走到") / 断[B] 一次(合闸允许态读 `b_Blink`); 触发动作由脚本给 |
| | `lcd_window_blocks()` / `lcd_window_obs()` | 影子缓冲那两块窗口: 名字→地址的规划(供 `watch.aa80_ram_snapshots`) / 读回的两页拆成拉闸区 48B 与对照区 16B |
| | `lcd_judge_ladder()` / `lcd_judge_lz_low()` / `lcd_judge_lz_high()` / `lcd_judge_ctrl()` | 判: 五态全到位 / 非合闸态非全 0 且各态逐字节相同 / 合闸态全 0 / 对照区每个观察态都非全 0 |
| | `lcd_relay_end_notes()` | 收尾: 本次够不到的那两块 |
| 本地续拉(12-3) | `auto_off_criteria()` | 判据(④ 声明 `unprovable`: `TAB_MeterSty.style` 是编译期常量) |
| | `auto_off_intro()` | 开场: 本次走哪两种观测(只打印) |
| | `auto_off_walk_plan()` | 前置: 走到 `ST_RelayOnKp(9)` 要按序发的那两帧(0x3A → 7/9, 落 7 再发 0x1C); 读与轮询在脚本 |
| | `auto_off_neg_decode()` | ② 的装配: 脚本读回的费控状态字与现态 + 那一帧的应答 → 本步的观测(只回事实) |
| | `auto_off_inject_intro()` / `auto_off_inject_decode()` | ①③ 的注入: 注入前那一句(停哪儿写什么, 只打印) / 注入记录 + 脚本读回的现态 → `{"rec","hit","watch","injects","ns","state"}` |
| | `auto_off_judge_neg()` / `auto_off_judge_ovr()` / `auto_off_judge_keep()` | 判: 不透支走表值 / 透支时裁决落 `ST_RlyOffL(4)` / 换发 0x3A 状态不动 |
| | `auto_off_end_notes()` | 收尾: 本次够不到的那两块 |
| 保电(698 入口) | `keep698_criteria()` | 判据(与 `keep_criteria()` 同一份: 裁决层是同一条) |
| | `keep698_intro()` | 开场: 本段走哪条入口、本次有没有断点会话(只打印) |
| | `keep698_send()` / `keep698_timetag()` / `keep698_step_decode()` | 一帧一步的三块: 发 698 Action 并解应答 / 取时标(读表钟 + 一分钟窗口) / 把脚本读过的数装配成 `step` |
| | `keep698_judge_action()` / `keep698_judge_timetag()` | 判: 断点解出的行号与新态对表 / 时标闸拒帧的原因位 |
| | `keep698_end_notes()` | 收尾写明本次够不到的那两块 |
| 公共 | `event_area_count()` | 某事件区当下有几条 |
| | `resolve_event_baseline()` | 定「基线」（判「有没有新增」的起点） |

**〔继电器〕9**

| API | 作用 |
|---|---|
| `ctrl_relay()` | 645 `0x1C` 跳合闸 |
| `ctrl_relay_reply()` | 跳合闸应答 → 裁决 |
| `relay_precheck()` | 动手前的先决检查（不许在非法态下跳闸） |
| `kp_expect()` | 给定的控制码该期待什么（裁决表） |
| `kp_reply_kind()` | 应答属于哪一类 |
| `rcsd()` | 继电器当前状态描述 |
| `relay_off_criteria()` / `relay_on_criteria()` | 跳 / 合 的判据 |
| `relay_roundtrip()` | 继电器全流程 |

**〔清零〕18** —— 高风险，只点名跑

| API | 作用 |
|---|---|
| `clear_meter()` | 645 `0x1A` 电表清零 |
| `clear_event()` | 645 `0x1B` 事件清零 |
| `clear_partition()` | 分区清零 |
| `clear_meter_criteria()` / `clear_event_criteria()` | 两种清零的判据 |
| `clear_library_evidence()` | 档案区被清的证据 |
| `clear_freeze_evidence()` | 冻结区被清的证据 |
| `clear_elec_evidence()` | 电量区被清的证据 |
| `clear_rebuild_evidence()` | 清完之后重建的证据 |
| `clr_event_send()` | 发事件清零帧 |
| `clr_event_dis_read()` | 读「事件清零」的展示量 |
| `clr_event_accept_evidence()` | 被接受 |
| `clr_event_id_evidence()` | 清的是哪一类 |
| `clr_event_block_evidence()` | 该被挡住的被挡住了 |
| `clr_event_keep_evidence()` | 该保留的没被清掉 |
| `clr_event_reject_evidence()` | 该被拒的确实被拒 |
| `clr_event_gate_evidence()` | 检查（允不允许清）的证据 |
| `clr_event_partial_probe()` | 只清一半会怎样 |

**〔电能镜像〕19**

| API | 作用 |
|---|---|
| `kwh_rows()` | 电能镜像的行读法 |
| `kwh_slot_ok()` / `kwh_slot_val()` | 某个 10 字节的项 是否有效 / 取值 |
| `kwh_store_plan()` | `g_CurkWh` 整块怎么读: `(绝对地址, 总长, 每片长)`, 读由脚本逐片做 |
| `kwh_to_698val()` | 项值 → 698 表示 |
| `kwh_bswap64()` | 8 字节换序（镜像的字节序与 698 不同） |
| `kwh_crc2()` | 项的 CRC2 |
| `kwh_num()` | 项个数 |
| `kwh_wb_vars()` | `g_CurkWh` 那组变量 |
| `energy_col_parse()` | 电能列解析 |
| `energy_ele_raw()` | 电能原始值 |
| `energy_ele_of()` | 某费率 / 某象限的电能 |
| `energy_slot_zero()` | 判某个项是不是全零 |
| `record_energy_cols()` | 记录里哪几列是电能 |
| `norm_energy47()` | 归一化 4 / 7 位电能 |
| `check_freeze_snapshot()` | 冻结与快照对得上吗 |
| `freeze_settle_kwh_raw()` | 结算冻结的原始电能 |
| `energy_mirror_criteria()` | 判据(预设条目) |
| `energy_mirror_intro()` | 第一步 · 开场说明(只打印) |
| `energy_mirror_multiframe()` | 第二步 · ⑤ 读: 每个 OAD 连读 n 轮整列 |
| `energy_mirror_stable()` | 第三步 · ⑤ 判: 各轮逐字节相同 |
| `energy_mirror_store_decode()` | 第四步 · 存储侧底账的纯解码: 脚本读回的 `g_CurkWh` 原始字节 → `{(电类, 费率): 10B 格}` |
| `energy_mirror_crc()` | 第五步 · ② 判: 10 个总项 CRC 与值配对 |
| `energy_mirror_alloc()` | 第六步 · ④a/④b 判: 费率分摊前提与等式 |
| `energy_mirror_compare()` | 第七步 · ③a/③b 判: 698 读回 vs 存值 |
| `energy_mirror_fore()` | 第八步 · ① 判: 来值→存值(断点观测) |

**〔版本 / 显示〕19**

| API | 作用 |
|---|---|
| `sv_read_698_ver()` / `sv_read_698_ids()` | 698 读版本 / 读 IDs |
| `sv_read_645_ver()` / `sv_read_645_fac()` | 645 读版本 / 读厂商 |
| `sv_digits()` / `sv_digits_val()` / `sv_digits_of_head()` | 版本串里的数字段 取 / 取值 / 取头部那几位 |
| `sv_ascii()` / `sv_text()` | 版本串的 ASCII 段 / 文本段 |
| `sv_expected_appsum()` | 版本串该有的校验和 |
| `softver_criteria()` / `softver_roundtrip()` | 判据 / 全流程 |
| `disp_oad()` | 显示量的 OAD |
| `disp_scalar_ud()` | 显示量的标量用户数据 |
| `disp_read_digits()` | 读显示数字 |
| `disp_gdispara_decode()` | `g_DispPara` 的原始字节 → `{dot, borrow, raw}`; 读由脚本自己做 |
| `disp_digit_relation()` | 数字之间的关系 |
| `disp_digit_criteria()` | 判据(预设条目) |
| `disp_digit_intro()` | 第一步 · 开场说明(只打印) |
| `disp_digit_readings()` | 第二步 · ③ 读: 位数对象族标量 GET |
| `disp_digit_scale()` | 第三步 · ③ 判: 4/2 位与尾数的量纲关系 |
| `disp_digit_dote()` | 第四步 · ① 判: 改 DotE ⇒ 液晶位数随之切换 |
| `disp_digit_borrow_floor()` | 第五步 · ②a 判: 借位下限(dot < Borrow) |
| `disp_digit_borrow_write()` | 第六步 · ②b 判: 借位写入(:3171 改 Borrow) |
| `disp_digit_netzero()` | 第七步 · 收尾: `g_DispPara` 净零核对(只进日志) |

**〔AA80 互验〕4 · 〔注入白名单〕2 · 〔语义动作〕2 · 〔杂项〕1**

| API | 作用 |
|---|---|
| `aa80_freeze_ping_criteria()` | 4-1 佐证观测的判据(预设条目) |
| `aa80_freeze_ping_intro()` | 第一步 · 开场说明(只打印) |
| `aa80_freeze_ping_pre()` | 第二步 · 前快照 + 698 读回 |
| `aa80_freeze_ping_broadcast()` | 第三步 · 触发(**写动作**): 698 广播瞬时冻结 |
| `aa80_freeze_ping_post()` | 第四步 · 后快照(AA80) |
| `aa80_freeze_ping_readback()` | 第五步 · 再读回 698 GetRequestRecord |
| `aa80_freeze_ping_diff()` | 第六步 · ① 判: 前/后快照有无字节变化 |
| `aa80_freeze_ping_advanced()` | 第七步 · ② 判: 读回最新一条推进 |
| `aa80_vs_swd_criteria()` / `aa80_vs_swd_compare()` | 串口读回与 SWD 读回比 —— 判据 / 比对 |
| `inject_assigns()` | 一次注入里要写的那些赋值 |
| `inject_allow_names()` | 注入白名单的名字集合 |
| `settle_across_master()` | 跨主站的结算 |
| `resolve_decide()` | 把「该判什么」解析成判据 |
| `bcd8()` | 8 位 BCD 编解码 |

**〔计量芯 SPI / 7-4〕6** —— 7-4 那条链路的判过积木（请求帧恒稳定 / 落位随帧更新 / 落位是直拷）

| API | 作用 |
|---|---|
| `spi_req_vars()` | 停 `Communicate.c:1006`（`SpiWriteDMA` 调用点）时要读的表达式表：状态机格号 + 要发出去的 29 字节 |
| `spi_land_vars()` | 停 `:1039` 时要读的：起始符偏移 + 帧内时间域 + 帧内电压 + 已落位的 `g_Volt[0]` |
| `spi_volt_from_ud()` | 698 读 A 相电压（`0x20000201`）应答的 APDU → 原始整数（0.1 V）；不是那个形态一律 `None` |
| `spi_dt_key()` | 帧内日期时间域 6 字节 → 可比元组（同年之内比大小即先后） |
| `spi_link_criteria()` | 7-4 预设判据五条（逐字抄 `ledger.md` 7-4 那一节，改那边要同步改这儿） |
| `spi_link_roundtrip()` | 7-4 判过全流程 |

---

### 调试通路 `src/swdbg/` 的表

#### `probesel.py` + `probe_jlink.py` / `probe_cmsis.py` —— 「该用哪支探针」与两支

台上插的是 J-Link 还是 DAPLink，上层不该管：`probesel` 枚举两端、选出**恰好那一支**（只枚举不预证：证明留给正式会话连上那一刻；只有 `--doctor` 会预先逐个读 CPUID）；
`probe_jlink.py`（pylink-square）与 `probe_cmsis.py`（pyOCD）是同一扇门的**两支**，各只实现三件事
—— `open` / `close` / 读绝对地址，其余全部共用。换一支探针不需要改任何测试脚本。

| API | 作用 |
|---|---|
| `probesel.candidates()` | 两端的候选各是谁 —— 返回 `(候选, 过程记账)`，扫过的每一端都留一行 |
| `probesel.verify()` | 真开一次会话读 CPUID(`0xE000ED00` == `0x410CC300`) 才算证明；**只有 `--doctor` 走它** —— 生产连接不预证，唯一候选连上即证明 |
| `probesel.pick()` | **恰好一支**；0 支 / 多支都抛，且附逐条原因（不是一句「找不到」） |
| `probesel.doctor()` | 打印完整挑选过程（`python -m swdbg.probesel --doctor`，装了 `pip install -e .` 之后从任何目录都行） |
| `probe_jlink` / `probe_cmsis` | 各端的三件事：`open` / `close` / `read_abs`；`backend` / `BACKEND` 是端名 |

⚠ CMSIS-DAP 那一端**必须**用 `connect_mode=attach`：pyOCD 默认是 `halt`，不写就把表停住了
（实测反证：默认 ⇒ `DHCSR=0x01030003`，`S_HALT=1`；attach ⇒ `0x01000001`，`S_HALT=0`）。

⚠ CMSIS-DAP 那一端还**必须**用 `soft_bkpt_as_hard=True`，否则**每个断点都是空响的炮**。
gdb 给 flash 地址下断点发的是 Z0（官方口径里的「软件断点」），由 GDB Server 自己决定在 flash 上怎么办：
JLinkGDBServerCL 译成 FPB 比较器（这正是「flash 断点」的来历），pyOCD 却默认照字面办
（`gdbserver.py` 里 `bkpt_type = HW if soft_bkpt_as_hard else SW`，选项默认 `False`），于是去往只读的 flash
里写 `BKPT` —— 什么也没发生，**它却照样回 `OK`**。2026-09-20 实测：挂三个断点（其中两个下在核几秒前刚跑过的行上），
15 s 内一条都不命中；挂完之后读 FPB（`-data-read-memory-bytes 0xE0002000 24`）是 `FP_CTRL=0x00000040`
（ENABLE=0）且四个比较器全 0 —— pyOCD 一个都没动。
**反证**（这一步不能省，否则会被读成「这颗核本来就下不了断点」）：同一支探针、同一颗核，绕过 gdb 直接
`set_breakpoint(<addr>, Target.BreakpointType.HW)` 再 `bp_manager.flush()`，FPB 当场变成
`FP_CTRL=0x41` / `COMP0=0x40023349`（FPBv1 的编码：匹配位 + 地址 + 使能位），撤掉后回到 0。
**硬件收得下，缺的只是那一条配置。** 加上配置后同一批断点秒命中。
⚠ 这也解释了它为什么只在 DAP 这一端露头：**两端 gdb 发的都是 Z0**，差别只在 Server 怎么译。

⚠ 第三个坑在**收尾**，比前两个更晚才露头：pyOCD 的 gdbserver **没有命令行控制台**（往它 stdin 写
什么都石沉大海），它退出的方式是 **gdb 客户端断开后自己收摊** ——
而这一步**是有前提的**（2026-09-20 实测把前提量清楚了）：gdb 连上就退 ⇒ 0.7 s 退出、退出码 0；
下了断点但**核是停着的**时候 gdb 退 ⇒ 照样 0.7 s、退出码 0；下了断点而**核是跑着的**时候 gdb 退 ⇒
**12 s 也不退**。最后那种正是每场会话收尾时的形状，所以 `close()` 对 DAP 端补了一步
「**先叫停，再 detach**」，把收尾那一刻变成上面第二行 —— 核心仍然交给 pyOCD 自己的
`resume_on_disconnect` 放行，不是我们的补丁。真等不到才落到 `_kill_server`（强杀的是**我们自己起的那个子进程**），
且落之前必须先留一行痕。**绝不允许**为此把 `python.exe` 加进 `restore.STRAY_PROCS`：那走
`taskkill /F /IM <映像名>` 按名字扫，会把本仓自己的解释器一起杀掉。

⚠ J-Link 端那一句「请它自己退」也是 2026-09-20 才修对的：JLinkGDBServerCL 的命令行控制台**只认
首字母大写的 `Exit`**，小写 `exit` 它一个字不认、也不吭声（实测：`exit\n` 10 s 不退，`Exit\n`
1.1 s 干净退出）。原先是小写，于是**每一场 J-Link 会话都等满 8 s 再落到 `_kill_server` 强杀** ——
而强杀 JLinkGDBServerCL 正是把核心撂在 halt 的元凶。这条不是偶发：修前它每跑必现。

#### `probe.py` —— 调试的地基（第 0 层 · 原语）

文件头自己写着：这是 swdbg 的**最底层**，只负责「连上探针、按绝对地址读出字节、干净退出」，不含任何电表语义。**它的立身之本是不停核** —— 与 `breakpoint` 的分工就在这里。对外名字与方法签名与单端时**一字未改**，只是 `open` / `close` / `read_abs` 按端分派，其余方法两端共用一份。

| API | 作用 |
|---|---|
| `Probe` | 一次 SWD 会话（**自动认出该用哪一支探针**），上下文管理器；进程崩了也由 atexit 兜底放行核心 |
| ├ `open()` / `close()` | 连接并 attach（幂等）/ 干净断开，**绝不 kill 任何进程**；`close()` 回 `True`/`False`，关不掉时**不宣称核心已放行**、留着句柄可重试 |
| ├ `read_abs()` | 读绝对地址 size 字节 |
| ├ `read_many()` | `[(名, 绝对地址, 长)]` → `{名: bytes\|None}`，单块失败记 None 不中断 |
| ├ `read_stable()` | **连读到两次相同才认** —— 表在跑，多字节读不是原子的，计数字段会撕裂 |
| ├ `halted()` / `dhcsr()` | 核心是否停住（停住了表不会应答串口） |
| └ `describe()` | 这次会话连上了什么 |
| `ProbeError` | 这一层自己的错 |

#### `breakpoint.py` —— 断点这件事的全部

三段住一个文件：**写法**（哪种断点算数）、**源码侧事实**（断点那一行上变量赋过值没有）、
**停核解地址**（用 `arm-none-eabi-gdb` 取代 IAR 手点）。前两段只吃 `.c` 文本与 `.out`，不连探针 ——
`scripts/_check_anchors.py` 靠的正是这一点。第三段**比 `probe` 强在能停核读局部量，代价就是要停核。**

| 分组 | API | 作用 |
|---|---|---|
| 写法 | `KINDS` / `normalize()` / `text()` | 四种写法收哪种、怎么念出来 |
| 源码侧事实 | `facts()` / `render()` | 断点清单 → 事实表 / 打成给人读的一段 |
| | `checks()` | 这套解析自己的判据（用合成样例，不碰厂商源码） |
| 会话 | `Session` | 一次 gdb + gdbserver 会话（**按端起**：J-Link 端 `JLinkGDBServerCL`，CMSIS-DAP 端 `pyocd gdbserver`） |
| | ├ `open()` / `close()` | 开会话时保证**核心必定在跑、断点单元必定是空的**；收尾时复核核心真在跑，复核不过自动叫 `restore.release_debug()` 兜底；收尾中途出错不标记已关，下次 `close()` 还能重试 |
| | ├ `describe()` / `stopped()` | 这次会话长什么样 / 核停着没 |
| | ├ `feed_watchdog()` / `dog_feeds()` | python 侧补喂狗（停住 8s 会复位） |
| | `session()` / `open_or_none()` | 开一次会话 / 开不了返回 `None` 并说明原因 |
| 断点 | ├ `break_at()` | 下断点 → bp 号，**断言真落在地址上**（不是 pending 悬空）；管理芯是 SecurCore SC000，只有 4 个硬断点 |
| | ├ `clear_breaks()` | 删掉所有断点。**⚠ 只在核心停住时能删** —— 这条最反直觉 |
| | ├ `breakpoints()` | 现在挂着的断点 |
| | ├ `inject_anchor()` / `inject_anchors()` | 从 `.out` 地图上查注入点（不再反汇编） |
| | ├ `decision_anchor()` | 找判定断点 |
| | ├ `callees()` / `func_addr()` | 一个函数调了谁 / 它的地址 |
| | └ `break_at_anchor()` | 在断点上直接下断点 |
| 跑停 | ├ `go()` / `resume()` | 非阻塞 continue |
| | └ `ensure_running()` / `ensure_stopped()` | 把核弄到确凿的运行 / 停住态再返回 |
| 等 | ├ `wait_hit()` | 等一次命中 → `Hit`；**超时不抛异常**（没命中是测试结论的一部分） |
| | ├ `wait_break()` | 等**指定的那个**断点命中 → `(Hit, {变量: 值})` |
| | └ `wait_only()` | 只等、不触发（等一个自然停到的断点） |
| 交错 | ├ `with_trigger()` | **串口触发 + 断点取证的标准交错** —— 不这么绕会死锁（命中时核停着，串口发不出去） |
| | └ `with_inject()` | **停着触发**：在 `at` 停住 → 下 watch 断点 → 写值 → 放行 |
| 读 | ├ `read_vars()` / `read_regs()` | 读一组表达式 / CPU 寄存器 |
| | ├ `locals_all()` / `frame_info()` | 当前帧全部局部量 / 当前停在哪 |
| | └ `step_one_pc()` | **单步一条指令**，落点 = 判定的实际走向 |
| 注入 | ├ `inject()` | **受控**写一个变量 → 恢复句柄；表达式必须在 `inject_allow` 白名单里 |
| | └ `restore_injections()` | 恢复注入过的值 |
| 证据封装 | ├ `trigger()` | 有触发帧的那一路 |
| | ├ `fire_hit()` | 自然到达的那一路 |
| | ├ `expect_no_hit()` | **否定期望**（窗口内不该命中） |
| | ├ `inject_hit()` / `inject_miss()` | 注入后 命中 / 没命中 |
| | ├ `inject_decide()` | 注入后看判定走向 |
| | ├ `inject_hold()` | **逐拍重注**，把注入造出的条件按住跨过整个时间窗 |
| | └ `record()` / `report()` | 打成一行标准取证 / 返回 `judge.rec` 形状的记录 |
| 非异常入口 | `break_at_or_none()` | **没接探针时回 `None` 而不是抛异常** —— 脚本照跑，只是白盒那半如实记「没做成」 |
| 结构 | `Hit` | 一次命中 |
| | └ `where()` | 命中在哪 |
| MI 解析 | `parse_mi()` / `mi_fields()` / `mi_unescape()` / `addr_tok()` | 解析 gdb MI 输出 |
| 其他 | `gdb_version()` / `GdbError` | gdb 版本 / 这一层自己的错 |

#### `gdbinit.py` —— 整片 `.out` 地图

`breakpoint.py` 底下的**地址来源**：函数范围、每条 `bl`、每个符号 —— 全在一张图上。
脚本开头 `gdbinit.build(ctx.g)` 把整片代码段解一遍（要停核，所以必须配 watchdog），
之后 `Session` 那几个找锚点的方法只查这张图，**谁都不再独立反汇编**：
连 `(文件, 行号)` 这种行锚点也解自这张图的 DWARF 行表，所以**凡是脚本里有断点元组，就必须先 build**。

| API | 作用 |
|---|---|
| `build(g)` | 整片解一次 → 挂在 `g._elfmap`；同一会话重复调用直接返回，不重解 |
| `map_of(g, spec)` | 四种字面量锚点 → 地址：`(文件, 行号)` / `("call"|"prev", 函数, 被调, n)` / `("func", 函数名)` |
| `offline_map(out, funcs)` / `resolve_spec(map, spec)` / `src_at(map, addr)` | **不起会话的那一份**：直接读 `.out` 建同形的图、按锚点解地址、再由行表给出源码行 —— `scripts/_check_anchors.py` 走这条 |
| `calls_of(g, func)` | 一个函数里每一个紧跟 `call 被调` 之后的指令（`inject_anchor` 的来源） |
| `insns_of(g, func)` / `func_entry(g, func)` / `line_addr(g, 文件, 行)` | 函数体整条指令流 / 函数入口地址 / 该行的起始地址 |
| `disasm(g, start, end)` | 全场唯一的 `-data-disassemble` 出口 |

#### `csrc.py` —— C 源码结构索引

`breakpoint.py` 底下的那一层：文件里有哪些函数、某一行归哪个函数、某个名字在哪几行出现。
判读原语（抠注释、判读写）在 `common/ctext.py`，与 `discover/source.py` 共用一份。

| API | 作用 |
|---|---|
| `load()` | 读一个 `.c` → （只留代码的行，原文行） |
| `functions()` / `enclosing()` / `params()` | 函数范围 / 某一行归哪个函数 / 形参 |
| `occurrences()` / `name_base()` / `decl_info()` | 一行里某名字的各次出现 / 名字归一 / 声明与初值形状 |
| `passed_out_re()` | 「传出」判定（赋值发生在被调函数里） |

#### `jlink.py` / `restore.py` —— 两根横切

⚠ `jlink.py` 只管 **J-Link 那一端**（枚举 / 体检 / 恢复都在这一端）；「该用哪支探针」现在归 `probesel.py`。
`restore.py` 是**两端通吃**的：J-Link 端走 `JLink.exe` Commander 脚本，CMSIS-DAP 端走一次 `attach` 会话
（读 `DHCSR` 判停没停 → 清 FPB/DWT 残留 → `resume()` → 关会话 → 再开一次复核），**不用 reset** —— 复位会打乱表钟与表内状态。

| 文件 | API | 作用 |
|---|---|---|
| `jlink.py` | `list_probes()` | 枚举当下在连的 J-Link |
| | `resolve_sn()` | 按判据拿「该用哪支」的序列号（**判据代替死值**） |
| | `diagnose()` / `diagnose_brief()` | 查清「探针到底怎么了」—— 返回 dict 不是布尔（布尔答不了「然后呢」） |
| | `plan_recovery()` | 由诊断结果算「该动什么」——**只算不做**，所以不碰硬件也能核 |
| | `recover()` / `doctor()` | 按诊断把探针弄回来 / 打印完整诊断 |
| | `JLinkError` | 这一层自己的错 |
| `restore.py` | `release_debug()` | 把被撂在停住态的管理芯放开，并清空 FPB 断点槽（**库面入口**） |
| | `kill_stray()` | 结束还可能挂在后台的调试进程 |
| | `run_jlink()` | 跑一段 J-Link Commander 脚本 |
| | `parse_dhcsr()` | 解析 DHCSR |
| | `describe()` | 这台机器上探针的状况 |
| | `utf8_stdout()` | 控制台编码（独立入口要用，不能依赖 `common`） |

---

### 中立层 `src/common/` 的表

这一层不认表、不认协议、不认探针，所以最容易被人当成「随便用用就行」—— 但实际上**这里用错，出的错全是静默的**：
判据算错不报错、日志剥错时间戳不报错、变量名解析到错的地址也不报错。所以整层配表。

#### `cardslot.py` —— 卡带槽

| API | 作用 |
|---|---|
| `Slot` | 一块卡带槽（「这块表 / 这台机器」的配方从这里取） |
| ├ `configure()` | 给这块槽指定取哪一盘卡带 |
| ├ `clear()` | 清空这块槽 |
| ├ `set_default_source()` | 设默认取哪一盘 |
| ├ `bootstrap()` | 第一次取用时的初始化 |
| ├ `current()` | 当下取的是哪一盘 |
| ├ `has()` / `get()` | 有没有 / 取出来 |
| ├ `resolve()` | 按名解析一项 |
| ├ `need()` | 缺了就报（可捕获） |
| ├ `require()` | 缺了直接停 —— 不许带着半个配方往下走 |
| └ `Proxy` | 取用代理（真到用的时候才读盘） |
| `resolve_in()` | 在指定的那几盘里找 |

#### `profile.py` —— 当前活动画像

模块级数据（当前活动画像 = 那块表），**无公开函数**。

#### `cli.py` / `console.py` —— 两个入口单点

| 文件 | API | 作用 |
|---|---|---|
| `cli.py` | `guard_argv()` | 脚本入口守卫 —— 不认得的参数 → **拒绝**，不静默继续（`--dry` 被静默吞掉会照样写真表） |
| `console.py` | `ensure_utf8_stdout()` | 控制台编码单点 —— 中文不乱码的唯一出口 |

#### `runlog.py` —— 读日志的唯一入口

| API | 作用 |
|---|---|
| `run()` | 跑一条命令并把 stdout / stderr 记进 `log/` |
| `stamp()` | 给一行打时间戳 |
| `strip_ts()` | 剥掉时间戳 —— **读日志一律经它**，不许各自写正则 |
| `real_run_of()` | 这一行是不是实测 —— 要看得到「口后面是本表」的凭据（三态：真 / 假 / 判不出来） |
| `proof_level()` | 那份凭据的成色：强 / 弱（老日志，只有自述）/ 没有 |
| `quarantine()` | 没凭据的那一份挪出 `log/` 根（去 `log/未实测/`），并写明为什么 |
| `scan()` | 扫一份日志里的子项结果 |
| `scan_dir()` | 扫一整个 `log/` 目录 |

#### `loglabel.py` —— 日志那一行的字形，只定义一次

同一个东西在日志里长出两种长相，读的人就得先猜再读。所以把**帧行 / 判定行 / 调试行**三样字形
各收成一个函数，**协议、方向、来源都是「格」不是自由文本** —— 写词表外的值当场炸，不许悄悄出一行看着正常的日志。

| API | 作用 |
|---|---|
| `Frame` | 一帧报文，**自带它属于哪个协议** —— 它就是 `bytes`，只多一个 `proto`（代价：切片会退回普通 `bytes`） |
| `proto_of()` | 从**帧**上取协议；取不到给空串（那是「没标」，不是「标错」）。**全仓唯一一处读 `proto` 的地方** |
| `frame_line()` | 一帧的日志行（收 / 发各一行）：协议格 + 方向格 + 功能名 + 帧长 + 帧体 |
| `result_line()` | 一次操作的判定行；`ok` 三态 True / False / None → PASS / FAIL / TBD |
| `debug_line()` | 调试侧留痕的一行 `[调试] [来源] 内容`（来源只认 `gdb` / `srv` / `SWD` / `探针` 四条） |

> ⚠ **功能名是唯一的自由文本，且不许含方括号头** —— 方向 / 对象 / 协议三格都由这里出，调用点一个都不许写。
> 检查在 `scripts/_check_loghead.py`（2026-09-18 实表跑 3-1 抓到过 9 处把整串烤进功能名的）。


#### `ctext.py` —— C 源码的文本原语

「这一行字长什么样」的那几条：抠注释与字符串（**等长空格**替换，所以行号与列偏移不变）、
认函数定义行、判一次出现是读还是写。断点那条线（`swdbg/csrc.py`）与足迹那条线
（`discover/source.py`）用的是同一份 —— 两处各判一次必然漂移，而漂移的表现是「同一个变量，
足迹说被写、断点体检说没写」。

| API | 作用 |
|---|---|
| `strip_comments()` | 一行 → 只留代码（注释/字符串等长抹平），返回块注释状态接着传 |
| `func_of()` | 这一行是不是函数定义的头 |
| `kind()` / `skip_subscripts()` | 一次出现是写还是读 / 去掉开头的下标组 |
| `decl_line()` | 这一行的这次出现是不是声明 |

#### `hashfile.py`

| API | 作用 |
|---|---|
| `sha256()` | 文件摘要（验「这份产物没被换过」） |

#### `varresolve.py` —— 变量名 →（地址，长）的唯一实现

| API | 作用 |
|---|---|
| `configure()` | 指定这次用哪个 `.out` / 哪套符号 |
| `resolve()` | 一个变量名 → 地址 |
| `blocks()` | 一批变量名 → `[(名, 地址, 长)]` |

#### `jsonc.py` —— 带注释的 json

| API | 作用 |
|---|---|
| `loads()` / `load()` | 去注释后解析（字符串 / 文件） |
| `strip_comments()` | 只去注释，不解析 |
| `canon()` | 规范化 |

#### `judge.py` —— 「这次测试到底证明了什么」的唯一出口

| API | 作用 |
|---|---|
| `Judge` | 一个子项的账本 |
| ├ `note()` | 记一条备注 |
| ├ `add()` / `extend()` | 加一条判据记录 / 一次加一批 |
| ├ `feed_degradations()` | 把降级声明喂进去 |
| ├ `status()` | 当下的三态结论 |
| ├ `status_without_degradations()` | 不看降级的话会是什么结论（用来显形「是降级救了它」） |
| ├ `exit_code()` | 结论 → 进程退出码 |
| └ `summary()` | 账本 → 给人读的汇总 |
| `decide()` | 三态判决：True / False / None（未定论） |
| `rec()` | 造一条记录（串口与调试往这里递同一种） |
| `render()` | 账本 → 给人读的一段 |
| `crit_states()` | 各条判据的状态 |
| `obs_states()` | 各条观测的状态 |
| `crit_text()` | 判据原文 |
| `crit_unprovable()` | 被判为「证不了」的那些 |
| `claims()` | 这一轮主张了什么 |
| `injected()` | 这一轮注过什么 |
| `obs_tag()` | 给一条观测打旗（是断点来的还是串口来的） |
| `is_evidence()` | 这条到底算不算证据 |
| `orphans()` | 有判据却没人认领的 |
| `degradation()` | 降级声明 —— 哪一条从白盒退成了黑盒、为什么 |
| `tri_eq()` / `tri_all()` | 三态相等 / 三态与 |

#### `portsel.py` —— 本机 USB-485 桥在哪

| API | 作用 |
|---|---|
| `list_ports()` | 列出所有串口 |
| `candidates()` | 哪些口像管理芯 |
| `describe()` | 【给人读】为什么挑它、别的为什么没挑上 |
| `pick()` | 按判据挑（判据代替死值） |
| `PortselError` | 这一层自己的错 |

#### `winpnp.py` —— Windows 设备树

| API | 作用 |
|---|---|
| `devices()` / `scan_devices()` | 枚举 USB 设备 |
| `problems()` / `problems_of()` | 有问题的设备 / 某个节点的问题 |
| `all_usb_nodes()` / `parse_nodes()` | 所有 USB 节点 / 解析设备树节点 |
| `dropped_then_returned()` | 掉过又回来的（**掉线判据**） |
| `summarize()` / `describe_node()` | 汇总 / 单个节点展开 |
| `is_elevated()` | 当下有没有管理员权限 |
| `restart_device()` / `cycle_device()` | 重启 / 循环上下电一个设备 |
| `PnPError` | 这一层自己的错 |

#### `trial.py` —— 运行外壳（第 3 层）

每个 `_test_*.py` 的 `main` 都长同一段固定开销，而这段里有**两处写错不报错**（收尾次序、退出码），所以收成一个外壳。

| API | 作用 |
|---|---|
| `run_subitem()` | 跑完一个子项并返回退出码 —— 脚本只调这一个 |
| `Stop` | 段里抛它 = 这一轮到此为止；收尾照常跑 |
| `Ctx` | 段拿到的东西：串口 + 账本 + 会话开关 + 段与段之间的状态袋 |
| ├ `g` | 段与段之间的状态袋 |
| ├ `session()` | 按需开调试会话（**一律 attach**，禁用 launch —— launch 会复位表、RAM 态清零） |
| ├ `trig()` | 递触发回调给库动词；**没会话时给 `None`**（库自己降级成黑盒） |
| ├ `trigger()` | 同上是「必须有」的那个 —— 拿不到就直接失败，不给 `None` |
| ├ `inject()` | 注入回调；**没会话时也是 `None`**（注入没有可降级的黑盒通路） |
| ├ `with_inject()` | 与 `inject()` 成对的上下文管理器 |
| ├ `bp()` | 现在就把断点挂上 |
| ├ `anchor()` | 只把断点原样传给库，不下断点 |
| ├ `hold()` | 收库动词的三件套 `(recs, why, scope)` |
| └ `take()` | 只要记录 |

---

#### `bench.py` —— 台面体检

一条命令问清「串口和 SWD 两条链路现在还通不通」。入口在 `scripts/_check_bench.py`，**不是** `project/tests/` 里的测试子项 —— 不产生测试结论、不写 `log/`，随时可单独喊。

四步：离线断言 → 串口 → 探针 →（`--full` 时）SWD 三关。**任一步不过就地收摊**，后面的步不跑。

| API | 作用 |
|---|---|
| `check_env()` | 离线断言（画像字段 / 帧清单 / 固件声明与 `.out` 指纹）——**不碰硬件，但它不许省**：画像或 `.out` 被换过时后面几步照样会通，那时「通了」是假象 |
| `check_serial()` | 开 COM3 并证明口后面是本表；证明本身含读一次表钟，所以这里不再重复读 |
| `check_probe()` | 证明探针后面是本表那颗核（读 CPUID）——`probesel.pick()` 读不到就抛，不会带着没证明过的探针往下走 |
| `check_swd()` | 委托 `swdbg.selfcheck` 跑三关（仅 `--full`）——一处实现，不在这儿重写一遍 |
| `check_bench()` | 逐步跑 → `Report`。两半至少要开一半，都关掉直接 `ValueError`——那种静默的「全通过」比报错危险 |
| `Step` / `Result` / `Report` | 步骤、一步的结论、一次体检的结论，都是具名属性。`Report.rows` 只含真跑过的步，没跑几步看 `Report.skipped`——不往 `rows` 里塞一行假结果标「没跑」 |

---

### 探测包 `src/discover/` 的表

与上面三条线正交：从 `.out` + 源码 + 总纲，探出一块**陌生表**的内部结构，产物是一份给人读的 md。

#### `elf.py` —— 符号 + DWARF

| API | 作用 |
|---|---|
| `Symbol` | 一个符号 |
| ├ `size_verdict()` | 大小对不对 |
| ├ `size_agrees()` / `addr_agrees()` | 大小 / 地址 两个来源对得上吗 |
| └ `as_dict()` | 转字典 |
| `symbols()` | 列出符号 |
| `dwarf()` | 取 DWARF 调试信息 |
| `sections()` | 列出各段 |
| `has_debug()` | 有没有调试信息（没有就只能靠符号猜） |
| `cross_check()` | 两处对同一个符号的说法对得上吗 |
| `describe()` | 一个符号展开讲 |
| `sha256()` | 这个 `.out` 的摘要 |

#### `source.py` —— 符号 ↔ 源码足迹

| API | 作用 |
|---|---|
| `ewp_root()` / `resolve_ewp_rel()` | 工程根在哪 / 解析工程里的相对路径 |
| `ewp_files()` | 工程里有哪些文件 |
| `index()` | 建源码索引 |
| `usage()` | 一个符号在源码里被怎么用 |
| `summary()` | 汇总 |

#### `scan.py` —— 找目标

| API | 作用 |
|---|---|
| `Target`（`report_path`） | 一个探测目标 |
| `TargetResult`（`name`） | 一个目标的探测结果 |
| `ewp_exe_paths()` / `ewp_candidates()` | IAR 装在哪 / 有哪些候选 |
| `ewp_out()` | 从工程推 `.out` 在哪 |
| `src_root_of()` | 源码根在哪 |
| `scan()` | 扫一遍 |
| `check_plan()` | 检查探测计划 |
| `default_doc()` | 默认总纲在哪 |
| `describe_target()` | 一个目标展开讲 |
| `render_index()` / `write_index()` | 索引打成给人读的 / 写出去 |
| `last_skipped()` | 上一次跳过了哪些（不静默） |

#### `doc.py` —— 从总纲抽 DI / OAD

| API | 作用 |
|---|---|
| `find_table()` | 在总纲里找那张表 |
| `extract()` | 抽出来 |

#### `evidence.py` —— 借 swdbg 采样判活 / 静止

| API | 作用 |
|---|---|
| `sample_once()` | 采一次 |
| `classify()` | 判这个变量是活的还是静止的 |
| `summarize()` | 汇总 |

#### `dossier.py` —— 四路并成一份报告

| API | 作用 |
|---|---|
| `build()` | 四路并起来 |
| `render()` / `write_report()` | 打成给人读的 / 写出去 |
| `meter_name()` | 这块表叫什么 |
| `is_ram()` | 这个符号在不在 RAM |

> `common/__init__.py`、`discover/__init__.py`、`meterlib/__init__.py` 没有公开函数，不配表：
> 它们是空包（层依赖图 / 包出口），不导出可调的东西。

---

### 入口脚本 `scripts/` 的表

这一层只管跑，不被 import；从仓根按路径跑。与 `project/tests/` 的 `_test_*.py` 不同：那些进册子、出测试结论、写 `log/`。

| 脚本 | 怎么跑 | 干什么（括号里是退出码） |
|---|---|---|
| `_check_bench.py` | `python scripts/_check_bench.py [--full] [--serial-only\|--swd-only]` | 台面体检：离线断言 → 串口 → 探针 →（`--full` 时）SWD 三关。不过就停在那一步。（0=全过 / 2=有步不过） |
| `_restore_all.py` | `python scripts/_restore_all.py [--dry] [--to '…'] [--no-debug] [--no-clock] [--no-relay]` | 总复位。步骤次序有硬约束：`release_debug` 最先、`exit factory` 最后。（0=全恢复 / 1=有步骤 FAIL / 2=前置失败） |
| `_init_meter.py` | `python scripts/_init_meter.py --out <表.out> [--src 源码目录]` | 探一块陌生表 → 一份《探测报告》。单表入口。（0=已生成 / 2=前置失败） |
| `_probe_all.py` | `python scripts/_probe_all.py <目录\|.ewp\|.out> [...]` | 批量探测：一镜像一份报告 + 一份总索引。（0=全成 / 1=有目标失败 / 2=前置失败） |
| `_check_anchors.py` | `python scripts/_check_anchors.py [某个 _test_*.py] [--anchors 文件:行:变量] [--src-root …]` | 把测试脚本里的断点对源码，看要读的变量在那一行赋过值没有。（0=每条都有配对 / 1=有抠不出的 / 2=前置失败） |
| `_check_aa80_vs_swd.py` | `python scripts/_check_aa80_vs_swd.py [--vars a,b]` | 同一刻 AA80(串口) vs SWD 逐字节比，证两条观察通路可无缝互替。**要真接探针**，没有离线跑法。（0=等价 / 1=有不等价证据 / 2=未定论） |
| `_check_readme.py` | `python scripts/_check_readme.py [--json]` | 本 README 的文件树 ↔ 真实仓库。（0=一致 / 1=有漂移） |
| `_check_fmt.py` | `python scripts/_check_fmt.py [目录] [--selftest]` | 扫 `.py`，抓 `%` 格式化串里不合法的转换符。 |
| `_check_loghead.py` | `python scripts/_check_loghead.py [目录] [--selftest]` | 扫 `.py`，抓调用点自己往功能名里写日志头。 |
| `_backfill.py` | `python scripts/_backfill.py <编号> --log <日志> [--no-gates] [--print-only]` | 把一次跑的结论回填进总纲 §10.6（幂等），并串跑三道派生检查。（0=没出问题 / 1=有检查红了 / 2=参数或文件有问题） |
| `_install_gdb.py` | `python scripts/_install_gdb.py` | 按 MSYS2 依赖闭包把带 Python 的 `gdb-multiarch` 抓到本地。一次性。 |
| `watch_runner.py` | `python scripts/watch_runner.py list \| show <case-id> \| run <case-id> [--wait S] \| anchors` | AA80 用例执行器：`cases71.json` → 一键跑判过。 |

---
### 事件流与 jsonl 的契约

一次跑出**两个文件**，同一个主干名，只差后缀：

    log/<名>_<时间戳>.log     人读的：缩进树、`==` 取证行、汇总块
    log/<名>_<时间戳>.jsonl   机器读的：一行一条 JSON，记「发生了什么」

`runlog` 回答「这次跑**打印**了什么」，事件流回答「这次跑**发生**了什么」。两路**并列，不是替换** —— 把那些专门排过版的 print 塞进事件流，只会让人读的那一路变难读。

同名主干是刻意的：**「这次跑的两路输出」靠文件名就配得上**，不另写对照表（写对照表迟早不同步）。也顺手避开了「同名第二次跑把第一次覆盖掉」这个问题。

#### 保留键

| 键 | 谁写 | 含义 |
|---|---|---|
| `kind` | `emit()` | 事件名（下面那张表） |
| `t` | `emit()` | epoch 秒（float）—— 排序、求差用这个，**别解析字符串** |
| `clock` | 宿主注入的 `stamp` | `[HH:MM:SS.mmm]`，与 `.log` 行首前缀**逐字相同**；没注入 ⇒ **没这个键**，也不补默认格式 |
| `seq` | `emit()` | 这一份文件里的**到达序**（从 1 起） |

`RESERVED = ("kind", "t", "clock", "seq")` 是**给读方的契约**：调用方顶不掉它们。`kind` 由函数签名保证（它没有默认值，传 `kind=` 当场 `TypeError`），其余三个靠 `emit` 里的**字典次序**（先放调用方的字段、再放保留键）。

`seq` 为什么除了时刻还要：串口动作跑在**后台线程**，两个线程同时写，**毫秒会撞在一起** —— 那时 `t` 分不出先后，`seq` 分得出。

另外一条不在 `RESERVED` 里的：`pair`（配对号），由 `emit()` 兜底填当前值，调用方显式传了 `pair=` 就按它的算。见下。

#### kind 词表（14 种）

| kind | 什么时候发 | 数据从哪儿收编 | 这个 kind 自己的字段 |
|---|---|---|---|
| `serial.tx` | 发出一帧 | `portsel.tx_recv` | `n` `tag` `hex` |
| `serial.rx` | 收完一帧（对端不回 ⇒ 收 0） | 同上 | `n` `tag` `hex` `waited` |
| `gdb.cmd` | 一条 MI 命令发出去 | `_trace_sent` | `cmd` |
| `gdb.reply` | 一条 MI 应答回来 | `_trace_recv` | `text` |
| `srv` | gdbserver 自己吐的行（两端同一条：J-Link 端是 JLinkGDBServerCL，CMSIS-DAP 端是 pyocd gdbserver） | `_trace_srv` | `text` `noise` |
| `bp.set` | 下了一个断点 | `bp_set` | `no` `spec` `addr` |
| `bp.hit` | 断点命中并取了证 | `report()` / `record()` | `tag` `verdict` `bkptno` `reason` `where` `vars` `error` |
| `halt` | **核心停住** | **唯一新造的**，见下 | `via` `reason` `bkptno` `func` `addr` `line` `file` |
| `inject` | 受控注入一次 | `Session.inject` / `with_inject` | `expr` `where` `addr` `size` `old` `new` `on_stack` |
| `verdict` | 本次结论出来 | `common.judge` | `title` `status` `exit_code` `reason` `phase` `degradations` `degradations_counted` `counts` |
| `pair.begin` | 开一个配对窗口 | `events.pairing()` | `pair` `name` `parent` |
| `fault` | **一个故障判死时** | `faultlog.record` | `code` `subsystem` `where` `text` `exc_type` `raised_at` `tried` `snapshot` `next_step` |
| `bench` | 实跑的头尾各一条：**那一刻链路通不通** | `faultlog.bench` | 现场给（探针端+SN / 设备到达时间 / CPUID / VTref / 核 DHCSR / 串口…），**不设死 schema** |
| `diag` | 敲了一条临时诊断命令 | `faultlog.diag`、`python -m common.faultlog --run` | `argv` `launched` `rc` `out` |


---
