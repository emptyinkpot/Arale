# -*- coding: utf-8 -*-
"""
project/tests/_suite.py —— 把在册的子项脚本装订成一册: 一处看全貌、一处挨个跑。

**它是什么**: 一份**硬编码的目设定**(71 个待测项, 一项一行) + 一个轮询器(挨个起子进程)
+ 一个报表(每项最新的那份日志读成事实)。三样都在本文件里, 没有第二个入口。

**它不是什么**(这条比上面那句重要):
  · 它**不是**判据的真源。判据本体的实现在 `meterlib.cmd_bank` 的库动词里,
    子项脚本只调用它们。本文件只**读**、只**排队**。
  · 它**不自己跑测试**。每个子项都**起子进程**跑它自己的 `_test_<编号>_*.py` —— 见下面 ⚠。
  · 它**不碰串口、不碰 J-Link**。本文件里没有一行开串口或连调试器的代码。

⚠ **为什么必须起子进程, 不许 import**(2026-09-17 定)
  每个 `_test_*.py` 的收尾都走 `common.trial.run_subitem`, 它包掉了「开串口 / 开会话 /
  收尾三连 / 退出码」; 而且脚本是**在 `main()` 里** `raise SystemExit(...)` 结束的。
  import 进来就叫不动它 —— 要么把外壳拆成可重入的(那是改 21 个脚本的契约),
  要么在本文件里重写一遍开串口/收尾(那是把外壳抄第二份, 迟早分叉)。
  子进程还与"一次跑一份日志、一个退出码"这个既有形状一致: 报表要的就是**每项一份日志**。

⚠ **绝不给子进程设超时**(2026-09-17 定, 与 CLAUDE.md「调试链」关键纪律 5b 同一条)
  脚本里握着 J-Link 的是**子进程**, 而超时到点就是要把它杀掉。**任何握着 J-Link 的进程都不许
  强杀** —— 强杀正是把探针从 USB 上弄掉、把核心撂在 halt 的那个动作。所以本文件里
  **一行 `timeout=` 都没有**, 也不套 shell 的 `timeout`。要中断就按 Ctrl-C, 那是**人**的动作。
  同理这里只打印、不 `TaskStop`/不 SIGTERM。

⚠ **破坏性清零不跟着 `--all` 跑**(用户 2026-09-17 原话: 「先不跑i5-4 / 5-5 是不逆的清零」)
  `--all` 只轮**没有台面前置、也非破坏性**的那些; 破坏性的两项**必须点名**(在命令行里写出编号)
  才会跑, 且在跑之前把"这是不可逆清零"打出来。见 `_wants()`。

用法(在仓根跑; 也可直接 `python project/tests/_suite.py`)
------
    python project/tests/_suite.py                  # 报表: 71 项各一行(只读, 什么都不跑)
    python project/tests/_suite.py report           # 同上
    python project/tests/_suite.py list             # 只列注册表(编号/子项/脚本/可跑性)
    python project/tests/_suite.py run 1-2 5-2      # 跑点名的项(破坏性的点名才跑)
    python project/tests/_suite.py run --all        # 轮询全部"可跑"项
    python project/tests/_suite.py run --all --bench   # 连"需台面"的也轮(台面不满足的会自己 TBD)
    python project/tests/_suite.py run --all --from 5-2 --to 9-3   # 只轮那一段

退出码: 0=跑过的项都退 0 / 1=有项退非 0 / 2=参数错或注册表自检不过(那时**一项都不跑**)。

**报表里那列「真跑」怎么来的**: 一律经 `common.runlog.scan`, 取它的三态 `real_run`
(有凭据说真 / 有凭据说假 / 一条凭据都没读到=判不出来)。**干跑与真跑在对端不答时都是 `RX(0)`**,
从别处分不开 ⇒ 这个量只有一个出处, 本文件不自己判(见 `common/runlog.real_run_of`)。
"""
import os
import subprocess
import sys

# ---- 仓根(向上找含 src/ 的那一级), **只用来定位文件** ----
_p = os.path.dirname(os.path.abspath(__file__))
while not os.path.isdir(os.path.join(_p, "src")) and os.path.dirname(_p) != _p:
    _p = os.path.dirname(_p)

from common.cli import guard_argv                   # noqa: E402
from common.console import ensure_utf8_stdout       # noqa: E402
from common import runlog                           # noqa: E402

ROOT = _p                                            # 仓根(= 含 src/ 的那一级)
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
LOGDIR = os.path.join(ROOT, "log")

# =====================================================================================
# 一、目设定 —— 71 个待测项, 一项一行
# =====================================================================================
# 这一节**是硬编码的**, 有意如此: 跑哪些、跳过哪些、为什么跳过, 应当一眼看得见、一次改一处,
# 而不是运行期从别处推出来(推出来的东西看不出"当初为什么这么定")。
#
# 每行五样: 编号 / 脚本(None=还没建) / 子项 / 状态 / 说明
#   · **子项** 是这一项的规格名 —— 它就住在这儿, 别处不再有第二份。
#   · **状态** 只有五种, 见下面五个常量。它答的是"这项**今天**能不能让册子自己跑起来"。
#   · 台面/不跑 的必须写一句**说明** —— 说明是给人看的: 哪一项为什么没进轮询, 从这一列读得出。

AUTO = "可跑"        # 有脚本, 且没有台面前置 ⇒ `--all` 会轮它
BENCH = "台面"       # 有脚本, 但要台面满足某个条件(电压/负载/上级设备) ⇒ 只有 `--bench` 才轮
DESTRUCT = "破坏性"  # 有脚本, 且**不可逆**(清零) ⇒ 只有**点名**才跑
TODO = "待建"        # 还没有脚本(可建但未建) ⇒ 轮不到
NEVER = "不跑"       # 永不轮: 不可达(死代码)/未实现(开发标未实现)/未编译 ⇒ 轮不到

ITEMS = [
    ("1-1", None,
     '四象限、基波、谐波电量：698 抄读值等于从表里读到的计量芯来值（管理芯只搬运不自算），无负载时电流与功率为 0',
     TODO, "谐波/四象限需标准源才能比数值; 链路一致性可先白盒验"),
    ("1-2", "_test_1_2_energy_mirror.py",
     '电能数据与计量芯保持一致：698 读回值 = 计量芯送来的值 = 各费率分摊之和，多帧稳定不变',
     AUTO, ""),
    ("1-3", "_test_1_3_display_digits.py",
     '电量小数位与尾数：改显示参数 DotE 后液晶在 2 位与 4 位之间切换，借位与尾数联动正确，698 按对象位数量纲读数正确',
     AUTO, ""),
    ("2-1", "_test_2_1_deviation_1s.py",
     '管理芯钟与计量芯钟的偏差：偏差大于 1s 时管理芯钟跟随被改、小于等于 1s 时不跟随（不误动），跟随后的时标正常',
     AUTO, ""),
    ("3-1", "_test_3_1_rate_num.py",
     '费率数与时段归属：各时段落到所设费率号且不错位，费率数上限 12 生效（超过 12 被拦后落默认）',
     AUTO, ""),
    ("3-2", "_test_3_2_zone_slot_switch.py",
     '当前、备用两套费率时段：到点自动切套、切后费率归属按新套，每次切套出一帧切换冻结；设定被清时不重复切、未到点不误切',
     AUTO, ""),
    ("4-1", "_test_4_1_freeze_ping.py",
     '瞬时冻结：每次触发落一条瞬时冻结记录，时标=触发时刻、电量与读回一致，多次累计正确',
     AUTO, ""),
    ("4-2", "_test_4_2_minute_frez.py",
     '分钟冻结：每个应记的冻结边界落一条（通道周期 prd=1 时是每个整分，本表实测通道周期大于 1 时按周期边界写），通道轮转到深度不越界，时标与电量连续',
     AUTO, "跑完表钟停在第二次拨钟的目标时刻 ⇒ 由册子跑 _restore_all.py 拨回真实时间"),
    ("4-3", "_test_4_3_hour_frez.py",
     '小时冻结：每个整点落一条，时标=该整点、中间分钟不重复、跨日跨月整点不丢',
     AUTO, "跑完表钟停在月末时刻 ⇒ 由册子跑 _restore_all.py 拨回真实时间"),
    ("4-4", "_test_4_4_day_frez.py",
     '日冻结：仅跨日当刻落一条，时标=0 点、电量=前一日累计且与读回一致，跨月正常',
     AUTO, "跑完表钟停在月末时刻 ⇒ 由册子跑 _restore_all.py 拨回真实时间"),
    ("4-5", None,
     '约定冻结：到约定时刻落一条约定冻结记录，时标与电量正确',
     NEVER, "未实现(无定时/约定冻结), 需开发定义需求后再测"),
    ("4-6", "_test_4_6_aa80.py",
     '结算日冻结：到结算日边界落一条结算冻结，电量=结算时刻快照、账期对得上，改结算日也触发一次',
     AUTO, ""),
    ("4-7", "_test_4_7_billfrez_y.py",
     '阶梯结算冻结：没有跨结算边界时不产生；本地表在年与阶梯结算边界正确生成一条并结转',
     AUTO, "跑完台面留在厂内态 ⇒ 由册子跑 _restore_all.py 收拾"),
    ("5-1", None,
     '过流事件：过流发生时落一条过流事件记录',
     NEVER, "不可达(死代码, 零调用已核)"),
    ("5-2", "_test_5_2_overload.py",
     '过载事件：发生与恢复各落一笔，去抖秒数、时标、电量快照正确',
     AUTO, "跑完台面留在厂内态 ⇒ 由册子跑 _restore_all.py 收拾"),
    ("5-3", "_test_5_3_lostpower.py",
     '掉电事件：去抖满后发生与恢复各落一笔，时标正确并置上报标志',
     AUTO, ""),
    ("5-4", "_test_5_4_clear_meter.py",
     '电表清零：电量与需量清零、冻结记录被清，永久记录与序号基准保留，存储区重建正常',
     DESTRUCT, "**不可逆清零**; 其中①记录本台不可达(三条准入路全够不着)"),
    ("5-5", "_test_5_5_clear_event.py",
     '事件清零：全清与帧标识一致，永久记录保留且新增一条，无权限被拒且原事件未动',
     DESTRUCT, "**不可逆清零**"),
    ("5-6", "_test_5_6_program.py",
     '编程事件：每次成功写参落一条，操作者、项目、时标正确，无权限写被拒且不记录',
     AUTO, ""),
    ("5-7", None,
     '计量芯片故障事件：计量芯故障时落一条记录',
     NEVER, "不可达(死代码): g_MeterSta 恒 TRUE"),
    ("5-8", "_test_5_8_clock_error.py",
     '时钟故障事件：超差、倒退、超前正确判故障并写库，恢复或校时后事件清除，时标正确',
     AUTO, ""),
    ("5-9", "_test_5_9_relay_off.py",
     '拉闸记录：每次成功拉闸落一条，含操作方式',
     BENCH, "继电器动作过 75%Un 判定, 本台常态 38.12V 或 0V"),
    ("5-10", "_test_5_10_relay_on.py",
     '合闸记录：每次成功合闸落一条，含操作方式',
     BENCH, "同 5-9: 需 ≥75%Un(165V)"),
    ("5-11", "_test_5_11_relay_fail.py",
     '负荷开关误动作事件：命令与实测不符记失败事件、一致记正常，操作方式与时标正确',
     AUTO, "跑完台面留在厂内态 ⇒ 由册子跑 _restore_all.py 收拾; "
     "缺的那半段: 「真电压下硬件反馈不跟随」—— 本台交流 0V、g_RelayFlg 恒分闸、75%Un 窗口常闭, "
     "『命令≠实测』是注入造出来的(6 条判据都不依赖它, 所以本项记可跑不记台面)"),
    ("5-12", None,
     '模块更换事件：模块更换时落一条记录',
     NEVER, "未实现(开发表标)"),
    ("5-13", None,
     '端子座过热报警：端子座温度越限后报警并落一条事件记录',
     NEVER, "未实现(且端子座编译关)"),
    ("5-14", None,
     '端子座温度剧变：端子座温度突变量超限后落一条事件记录',
     NEVER, "未实现(开发表标)"),
    ("5-15", None,
     '端子座温度不平衡报警：温度不平衡越限后报警并落一条事件记录',
     NEVER, "未实现(开发表标)"),
    ("5-16", None,
     '零线电流异常事件：零线电流异常时落一条记录',
     NEVER, "不可达(死代码, 零调用已核); 单相产品多半不适用"),
    ("5-17", None,
     '插卡事件：插卡时落一条事件记录',
     NEVER, "未实现(开发表标)"),
    ("6-1", None,
     '电表清零：电量与需量清零、冻结记录被清，永久记录与序号基准保留，存储区重建正常',
     DESTRUCT, "脚本待建; **清零本身不可逆**, 建好也只点名跑"),
    ("6-2", None,
     '事件清零：全清与帧标识一致，永久记录保留且新增一条，无权限被拒且原事件未动',
     DESTRUCT, "脚本待建; 同 6-1"),
    ("7-1", "_test_7_1_frame_dispatch.py",
     '蓝牙通道：从蓝牙口进来的帧被管理芯正确取数并派发到对应处理',
     AUTO, "判据只收管理芯固件答得出的那 7 条(485 口 + 蓝牙口各跑一遍去程, 蓝牙口另跑回程 ⑤⑥⑦); "
     "蓝牙链路「表→主机」那半段观测不到 —— 模组不往主机转发(实测: 4 个可通知特征全订上、"
     "发帧后只收到电池特征 1 字节), 与固件无关, 记在 7-1『结果』列的台面事实里"),
    ("7-2", "_test_7_2_plc_relay.py",
     '载波通道：转发帧进没进那条路、转过去的口对不对、转过去的字节与收到的帧逐字节一致（去前导 FE 后原样）',
     AUTO, "只做管理芯中继/透传那一半(485 口注入转发给计量芯的帧), 含计量芯应答转回 485 的回程段; "
     "载波链路电气与主站那半边未测; 并关联已知问题①(载波抄读计量芯升级事件报文异常)"),
    ("7-3", "_test_7_3_rs485_dispatch.py",
     'RS485 通道：发帧后管理芯正确取数并派发',
     AUTO, "只做管理芯帧处理那一半(485 口注入 645/698 + 一帧乱帧); 485 链路电气与主站那半边未测"),
    ("7-4", "_test_7_4_spi_link.py",
     '路由扩展模组与计量芯：计量芯 SPI 链路正常，管理芯只轮询读取、不自算',
     AUTO, ""),
    ("8-1", None,
     '按键背光：按键点亮、60 秒到时熄灭，背光状态位驱动正确',
     TODO, "目测项; 白盒限背光状态/计时位"),
    ("8-2", None,
     '液晶显示的数值、单位与符号：显示值等于存储回读值，单位与符号正确',
     TODO, "目测项; 数值源可断点核对"),
    ("8-3", None,
     '错误编码、插卡提示与插屏显示：对应状态下液晶给出该提示',
     NEVER, "未实现(开发表标)"),
    ("8-4", None,
     '掉电按键显示：掉电后 4H 内最多 5 次按键点亮显示、每次 30 秒、不支持翻屏(需澄清：每4H最多5次？)',
     NEVER, "未实现(开发表标); 且规格本身待澄清"),
    ("8-5", None,
     '轮显与键显方式：轮显顺序与间隔符合参数，按键转手显、超时回自动',
     TODO, "目测项; 白盒限轮显状态/参数位"),
    ("8-6", None,
     '上电显示：上电 3s 内液晶显、背光亮，默认持续 5s 且可设 5s～20s',
     NEVER, "未实现(开发表标)"),
    ("8-7", "_test_8_7_lamp_wake.py",
     '通信唤醒显示：收到通信帧后液晶显、背光亮，背光计时按要求置位、到时归 0',
     AUTO, "只做蓝牙口那一条路(投唤醒消息那一句写在 `if (port == PT_BLE_M)` 里, 485 口不投); "
     "液晶\"显出\"那一半要先有息屏态前置(只有 RTC 中断与按键驱得动 Run_StopDisp, 帧路径造不出), "
     "前置不满足时那一条记未证"),
    ("8-8", None,
     '液晶符号：通信符、公钥符、功率反向符、电池欠压符与各自状态源一致',
     TODO, "目测项; 白盒核状态源位"),
    ("8-9", None,
     '峰谷电价计费模式：费率时段位置固定显示 T8',
     NEVER, "未实现(开发表标)"),
    ("9-1", None,
     '过流事件：过流发生时落一条过流事件记录',
     NEVER, "不可达(与 5-1 同源: 过流检测无调用)"),
    ("9-2", "_test_9_2_rev_power.py",
     '功率反向事件：反向功率过门槛后正确判并落事件，恢复笔与时标正确',
     AUTO, ""),
    ("9-3", "_test_9_3_overload.py",
     '过载事件：发生与恢复各落一笔，去抖秒数、时标、电量快照正确',
     AUTO, ""),
    ("9-4", None,
     '端子座过热报警：端子座温度越限后报警并落一条事件记录',
     NEVER, "不可测(未编译): 端子座检测未开宏"),
    ("9-5", None,
     '端子座过热跳闸：端子座过热越限后跳闸并落一条事件记录',
     NEVER, "不可测(未编译)(同 9-4)"),
    ("9-6", None,
     '端子座温度剧变：端子座温度突变量超限后落一条事件记录',
     NEVER, "不可测(未编译)(同 9-4)"),
    ("9-7", None,
     '端子座温度不平衡：温度不平衡越限后报警并落一条事件记录',
     NEVER, "不可测(未编译)(同 9-4)"),
    ("9-8", None,
     '电压谐波总畸变：抄读的谐波总畸变值与实际谐波含量一致',
     NEVER, "不可测(未实现; 抄表变量谐波=占位恒0, 无数据源)"),
    ("9-9", None,
     '电流谐波总畸变：抄读的谐波总畸变值与实际谐波含量一致',
     NEVER, "不可测(未实现; 同 9-8)"),
    ("9-10", None,
     '零线电流异常事件：零线电流异常时落一条记录',
     NEVER, "不可达(与 5-16 同源: 检测无调用)"),
    ("9-11", None,
     '计量芯片故障事件：计量芯故障时落一条记录',
     NEVER, "不可达(同 5-7: g_MeterSta 恒 TRUE)"),
    ("10-1", "_test_10_1_keep_remote.py",
     '远程费控：优先级高者生效，保电期间拉闸被拦且回明确错误，状态与事件正确',
     AUTO, "跑完台面留在厂内态 ⇒ 由册子跑 _restore_all.py 收拾; "
     "缺的那半段: 判据⑦(保电/解除/拉闸各落一条事件)够不到 —— 遥控事件经 Recd_CtrlRelay 落库, "
     "而那在 TaskRelay.c:282-284 的 ≥75%Un 许可判定里面, 本台 38V ⇒ 事件按设计不产生。"
     "其余 6 条判据都在管理芯固件上做得出, 所以本项记可跑不记台面; "
     "698 那条入口(DLT698App.c 的 Action_Control)本库无组帧, 未证"),
    ("10-2", None,
     '本地费控：本地费控命令按规则裁决执行，状态与事件正确',
     NEVER, "开发标: 本地未实现(代码在 TP_Local 下可达=疑未部署)"),
    ("11-1", "_test_11_1_year_step.py",
     '年阶梯：年边界档位电量结转正确，结转时冻结一条，档状态与规范一致',
     AUTO, "跨年边界由注入造(本台年结算日期表参数是出厂无效值); 跑完台面留在厂内态 ⇒ 由册子跑 _restore_all.py"),
    ("11-2", "_test_11_2_month_step.py",
     '月阶梯：按月结转档位电量正确、不与年阶梯相混，结转时冻结一条',
     AUTO, "同 11-1; 跑完台面留在厂内态 ⇒ 由册子跑 _restore_all.py"),
    ("12-1", "_test_12_1_keep_release.py",
     '保电与解除：保电期间拉闸被拒且回明确错误，解除后命令可执行，保电位正确上报',
     AUTO, "跑完台面留在厂内态 ⇒ 由册子跑 _restore_all.py 收拾; "
     "缺的那半段: 判据⑧(保电/解除/拉闸各落一条事件)够不到 —— 遥控事件经 Recd_CtrlRelay 落库, "
     "而那在 TaskRelay.c:282-284 的 ≥75%Un 许可判定里面, 本台 38V ⇒ 事件按设计不产生。"
     "其余 7 条判据都在管理芯固件上做得出, 所以本项记可跑不记台面"),
    ("12-2", "_test_12_2_lcd_relay.py",
     '液晶拉闸符号：仅命令态为非合闸类时显示拉闸符号，与实际液晶目测一致',
     AUTO, "跑完台面留在厂内态 ⇒ 由册子跑 _restore_all.py 收拾; "
     "缺的那半段: 判据⑦(实际液晶屏上目测到“拉闸”)够不到 —— 四条通路读的都是内存里的影子缓冲 "
     "lcd_buffer, 那是驱动液晶的那份数据, 不是玻璃上的像素(驱动断线/背光坏/段码没接时它照样对)。"
     "其余 7 条判据都在管理芯固件上做得出, 所以本项记可跑不记台面"),
    ("12-3", "_test_12_3_auto_off.py",
     '解除后是否续拉：本地透支解除后自动本地拉闸，远程解除后不自动续拉、须主站再发拉闸命令',
     AUTO, "跑完台面留在厂内态 ⇒ 由册子跑 _restore_all.py 收拾; "
     "缺的那半段: 判据④(远程费控表须主站再发拉闸)够不到 —— `TAB_MeterSty.style` 是**编译期常量** "
     "(`UserCfg.c:26`, `Local_Meter` 在 `UserCfg.h:102` 已 define), 本台固件烘成本地表, "
     "没有任何帧或注入能把它改成远程表。其余 3 条判据都在管理芯固件上做得出, 所以本项记可跑不记台面"),
    ("13-1", "_test_13_1_auto_rpt.py",
     '主动上报的内容：事件发生→按周期上送→应答或超时后标志复位，帧内含该事件对象与上报状态字',
     AUTO, "跑完表钟被 −1 个月(注入那次不还原)、台面留厂内态 ⇒ 由册子跑 _restore_all.py 收拾; "
     "缺的那半段: 上送走载波(PT_PLC_M=3, TAB_PortOAD[PLC_M]=F2090201, DLT698App.c:3227), "
     "台上无集中器/载波主站收帧, 判据⑥『帧真上送到对端』已声明不可证 ⇒ 整项据此记未定论; "
     "其余 5 条都在管理芯固件上做得出(事件靠注入造, rptnum/buff 是局部量 ⇒ 只有断点观测给得出, "
     "本项没有黑盒替身), 所以本项记可跑不记台面"),
    ("13-2", None,
     '掉电主动上报：掉电后主动上发一次，随后每秒 1 次共 3～4 帧，帧内含事件与状态字，3s 窗口后停',
     TODO, "脚本待建; 多级判定控, 须逐项排除"),
    ("14-1", None,
     '报文加解密：不认证的写被拒、认证的写放行并落对应记录',
     TODO, "应用层冒烟可测; 路由模组加解密部分=开发标未实现, 不测"),
    ("14-2", None,
     '卡片读写：插卡后卡片内容被正确读出',
     NEVER, "未实现(开发表标『未完成』)"),
    ("15", None,
     '费控转换：插费控转换卡后在非预付费与预付费模式之间转换，远程设置不允许',
     NEVER, "未实现(开发表标『未完成』)"),
    ("16-1", "_test_16_1_prog_auth.py",
     '参数设置权限与编程记录：无权限写被拒且不记录，成功写落一条编程记录，软件标识读取并与本机发布标识一致',
     AUTO, "权限/记录半支与 5-6 同一代码路径、软件标识半支与 16-2 同一对象; **独有**的是①正面停在权限拒绝点"),
    ("16-2", "_test_16_2_soft_compare.py",
     '软件比对：读到的软件版本与程序集成标识三段校验和逐字相符，升级校验拒掉 CRC 或版本不符的包',
     AUTO, "数字签名核对=未实现, 不测"),
    ("16-3", None,
     '在线升级：升级后头与尾各完整落一条且结果为成功，除升级记录外其余数据全部一致',
     TODO, "脚本待建; 电子签名=未实现, 不测"),
]

# **不在册里的脚本** —— 有意列出来, 免得"册里没有"与"忘了它"分不开。
# 只收 `tests/` 下的 `_test_*.py`; "不认领编号"的对照检查器(如 AA80↔SWD 通路对照)归 `scripts/`。
OFF_REGISTRY = []

# 跑完会把**台面留在厂内态**的项 —— 跑完它们必须让 `scripts/_restore_all.py` 收拾一遍。
# 判据来自那 5 个脚本自己的 docstring(它们都写着"台面留在厂内态, 收尾由 _restore_all.py 收拾")。
LEAVES_BENCH = {"4-7", "5-2", "5-11", "11-1", "11-2", "12-1", "12-2", "12-3", "13-1"}

RESET_ALL = os.path.join(ROOT, "scripts", "_restore_all.py")


# =====================================================================================
# 二、对账与查表
# =====================================================================================
def _registry_dict():
    """注册表 → {编号: dict(脚本/子项/状态/说明)}, 并当场判"编号有没有写重"。"""
    d = {}
    for no, script, sub, kind, note in ITEMS:
        if no in d:
            raise ValueError("注册表里编号 %r 写了两次 —— 一项一行, 重复的那行先删掉" % no)
        d[no] = {"script": script, "sub": sub, "kind": kind, "note": note}
    return d


def _roundtrip_needs():
    """{库动词名: 它(含委托的驱动)会从 `wb` 里读的键} —— 从 `cmd_bank` 源码现抠。

    「读哪些键」不许在这里手列第二份: 库加一个 `wb.get("hold")`, 手列的清单不会跟着动,
    而本检查存在的全部意义就是**发现脚本少给了库要用的东西**。
    委托只跟一层: 那几个 `*_roundtrip` 要么自己读(`wb.get`), 要么 `return _meas_event_roundtrip(...)`
    (5-2 过载 / 9-2 反向 / 9-3 复验走的就是这条) —— 一层不够时这里会报不出来, 那就把遍历加深。
    """
    import ast as _ast
    import re
    p = os.path.join(ROOT, "src", "meterlib", "cmd_bank.py")
    txt = open(p, encoding="utf-8").read()
    lines = txt.split("\n")
    body, deleg = {}, {}
    for n in _ast.walk(_ast.parse(txt)):
        if not isinstance(n, _ast.FunctionDef):
            continue
        src = "\n".join(lines[n.lineno - 1:n.end_lineno])
        body[n.name] = set(re.findall(r"wb(?:\.get\(\"|\[\")([a-z_]+)", src))
        deleg[n.name] = sorted(set(re.findall(r"\b(_[a-z_]*roundtrip)\(", src)))
    out = {}
    for name in list(body):
        k = set(body[name])
        for d in deleg.get(name, ()):
            k |= body.get(d, set())
        out[name] = k
    return out


def _dicts_in(node):
    """剥掉 `if … else None` / 括号, 把这个表达式里**写死的字典字面量**找出来。"""
    import ast as _ast
    if isinstance(node, _ast.Dict):
        return [node]
    if isinstance(node, _ast.IfExp):
        return _dicts_in(node.body) + _dicts_in(node.orelse)
    return []


def _wb_gate(mine):
    """脚本交给库的 `wb` 字典, 键必须够库用 —— 少一个键, 库**静默**走兜底支。

    为什么非有这一道(2026-09-18 加, 起因是实测): `_test_9_2_rev_power.py` 与
    `_test_9_3_overload.py` 的 `wb` 少了 `"hold"`(逐拍重注), 只有 5-2 那份给全了。
    少这一个键的后果**不是崩**, 而是库打印一行警告后回退到单次注入 —— 而单次注入在本台面
    攒不满去抖, 于是 ① 记了一条 **FAIL**。那条 FAIL 看着与 5-2 那条一模一样, 却**不是固件的账**:
    账本里从此躺着一条归错人的结论, 而两个屏幕都不红。
    ⇒ 由「册子 ↔ 脚本」这条线来查: 键从库里现抠(`_roundtrip_needs`), 脚本里那个 `wb = {...}`
    字面量的键与它比。**多给不算错**(库不读而已), 少给就是这里要报的。
    """
    try:
        need = _roundtrip_needs()
    except Exception as e:
        return ["读不出库动词要的 `wb` 键(%s: %s) —— 这一条对不了, 先修上面那个错"
                % (type(e).__name__, e)]
    import ast as _ast
    bad = []
    for no, r in sorted(mine.items()):
        script = r["script"]
        if not script:
            continue
        try:
            tree = _ast.parse(open(script_path(script), encoding="utf-8").read())
        except (OSError, SyntaxError) as e:
            bad.append("%s 的脚本 %s 读不动(%s: %s)" % (no, script, type(e).__name__, e))
            continue
        called = sorted({c.func.attr for c in _ast.walk(tree)
                         if isinstance(c, _ast.Call) and isinstance(c.func, _ast.Attribute)
                         and c.func.attr.endswith("_roundtrip")})
        if not called:
            continue                       # 这套脚本不靠库动词递证据: 不归本检查管
        gave = set()
        for n in _ast.walk(tree):
            if isinstance(n, _ast.Assign) and any(
                    isinstance(t, _ast.Name) and t.id == "wb" for t in n.targets):
                # ⚠ 这里**不能只看 `n.value` 是不是 Dict**: 在册那几种写法都是
                #   `wb = {...} if g is not None else None`(没接 J-Link 就整体给 None),
                #   字面量裹在一层 `IfExp` 里。首版没剥这层 ⇒ 所有脚本都被判成"一个键都没给"
                #   (2026-09-18 实测: 一次报出 7 条, 连 5-2 那份给全了的也报)。
                for d in _dicts_in(n.value):
                    gave |= {k.value for k in d.keys if isinstance(k, _ast.Constant)}
        want = set()
        for fn in called:
            if fn not in need:
                bad.append("%s 的脚本 %s 调了 `CB.%s()`, 而 cmd_bank 里没这个函数(或名字改了)"
                           % (no, script, fn))
                continue
            want |= need[fn]
        miss = sorted(want - gave)
        if miss:
            bad.append("%s 的脚本 %s 给的 `wb` 少了 %s —— 库会**静默**走兜底支(不是崩), "
                       "那一条结论会看着像固件的账(见 `_wb_gate` 注)"
                       % (no, script, "、".join("`%s`" % m for m in miss)))
    return bad


def check_registry():
    """册子自检。返回 (ok, 差异说明行 list)。

    **为什么非对不可**: 本文件是**目设定**, 清单短了一项、脚本改了名字, 屏幕上一切正常 ——
    这正是本仓反复治理的那类**静默**错误。⚠ 对不上就**一项都不跑**(退出码 2) ——
    宁可当场停, 也不要在对不上的清单上跑真表。

    两条:
      ① 盘上的 `_test_*.py`: 要么被册子认领, 要么**有意**登记在 `OFF_REGISTRY` —— 不许有第三种。
      ② 脚本 ↔ 库动词: `wb` 的键得够库用(少一个键, 库静默走兜底支, 结论归错人) —— 见 `_wb_gate`。
    """
    bad = []
    mine = _registry_dict()

    # ① 盘上的 _test_*.py: 要么被认领, 要么**有意**登记在 OFF_REGISTRY —— 不许有第三种
    try:
        on_lit = sorted(f for f in os.listdir(TESTS_DIR)
                        if f.startswith("_test_") and f.endswith(".py"))
    except OSError:
        on_lit = []
    claimed = {r["script"] for r in mine.values() if r["script"]}
    off = {nm for nm, _why in OFF_REGISTRY}
    for fn in on_lit:
        if fn not in claimed and fn not in off:
            bad.append("%s 在盘上, 但册子里没人认领、也不在 OFF_REGISTRY —— "
                       "要么它认领一个编号, 要么明写'有意不认领'" % fn)

    # ② 脚本 ↔ 库动词: `wb` 的键得够库用(少一个键, 库静默走兜底支, 结论归错人)
    bad += _wb_gate(mine)
    return (not bad), bad


def script_path(script):
    return os.path.join(TESTS_DIR, script) if script else None


def log_stem(script):
    """脚本文件名 → 日志主干名。单点判据: `_test_5_2_overload.py` → `5_2_overload`。

    ⚠ 这条是**约定**, 不是猜: 21 个脚本 `run_subitem(name=…)` 给的名字与自己的文件名主干
      **逐个相同**(2026-09-17 核过全部 21 个)。所以这里从文件名推, 不另建一张对照表 ——
      对照表那种写法迟早会不同步, 而不同步的表现是"报表里这一项的日志永远是空的"。
    """
    if not script:
        return None
    s = script
    if s.startswith("_test_"):
        s = s[len("_test_"):]
    if s.endswith(".py"):
        s = s[:-3]
    return s


def latest_log(script):
    """该项**最新**的那份日志的完整路径, 没有就是 None(不猜、不造)。"""
    stem = log_stem(script)
    if not stem or not os.path.isdir(LOGDIR):
        return None
    hits = sorted(f for f in os.listdir(LOGDIR)
                  if f.startswith(stem + "_") and f.endswith(".log"))
    return os.path.join(LOGDIR, hits[-1]) if hits else None


def registry_rows():
    """报表用的行: 每项 → dict(编号/子项/脚本/状态/说明/日志/事实)。

    `事实` 是 `common.runlog.scan` 读出来的那份(没有日志就是 None)。**本文件不从日志里
    自己抠任何字段** —— 抠法只有 `common.runlog` 一份, 各抠各的迟早分叉。
    """
    out = []
    for no, script, sub, kind, note in ITEMS:
        p = latest_log(script)
        fact = None
        if p:
            try:
                fact = runlog.scan(p)
            except Exception as e:
                fact = {"_error": "%s: %s" % (type(e).__name__, e)}
        out.append({"no": no, "sub": sub, "script": script, "kind": kind,
                    "note": note, "log": p, "fact": fact})
    return out


def _w(s, width):
    """按**显示宽度**截断/补齐(中文两格)。报表列宽靠它对齐, 不然中文列会错位。"""
    def wid(t):
        return sum(2 if ord(c) > 0x2E80 else 1 for c in t)
    s = s or ""
    w = wid(s)
    if w > width:
        cut, acc = "", 0
        for c in s:
            cw = 2 if ord(c) > 0x2E80 else 1
            if acc + cw > width - 1:
                break
            cut, acc = cut + c, acc + cw
        return cut + "…" + " " * max(0, width - acc - 2)
    return s + " " * (width - w)


def _real_cell(fact):
    """报表里「真跑」那一格: 三态各给一个词, **判不出来不许说成"假"**。"""
    if fact is None:
        return "—"
    if "_error" in fact:
        return "读不了"
    r = fact.get("real_run")
    if r is True:
        return "真跑"
    if r is False:
        return "替身"
    return "无凭据"


# =====================================================================================
# 三、报表
# =====================================================================================
def report(compare=True):
    """71 项各一行。只读 —— 不开串口、不连调试器、不跑任何脚本。

    **自检不通过时退出码 1**(2026-09-18 改): 原先只是把差异打出来、照样 `return 0` ——
    那等于"看见了但不拦", 与本仓「靠人眼盯的事全换成跑一次就知道」的规矩相反, 也让
    README 的检查表没法把它当检查列。现在差异本身**就是** red; `run` 那条路更硬(exit 2,
    一项都不跑)。
    """
    diff = 0
    if compare:
        ok, bad = check_registry()
        if not ok:
            diff = len(bad)
            print("!! 册子自检不过 —— 报表先照本文件那份打, 但**这些差异是真的**:")
            for b in bad:
                print("   · " + b)
            print("   (`run` 也照旧拒跑 —— 它本来就拒)")
            print()
    rows = registry_rows()
    print("== 册子报表: 在册 %d 项 | 日志夹 %s ==" % (len(rows), LOGDIR))
    print()
    print("  " + _w("编号", 6) + _w("子项", 34) + _w("脚本", 30) + _w("最新日志", 22)
          + _w("判据", 18) + _w("真跑", 8) + "可跑性")
    print("  " + "-" * 120)
    for r in rows:
        f = r["fact"] or {}
        logname = os.path.basename(r["log"]) if r["log"] else "—(还没跑过)"
        crit = "—"
        if f and "_error" not in f:
            n_sat, n_fail, n_un = (f["counts"]["满足"], f["counts"]["失败"], f["counts"]["未证"])
            crit = "满足 %d / 失败 %d / 未证 %d" % (n_sat, n_fail, n_un)
        print("  " + _w(r["no"], 6) + _w(r["sub"], 34)
              + _w(r["script"] or "—", 30) + _w(logname, 22) + _w(crit, 18)
              + _w(_real_cell(r["fact"]), 8) + r["kind"])
    print()
    # 汇总: 按可跑性分组点名 —— 这几行才是"册子今天能替我干多少"的答案。
    from collections import Counter
    c = Counter(r["kind"] for r in rows)
    print("  合计: " + "  ".join("%s %d" % (k, c[k]) for k in (AUTO, BENCH, DESTRUCT, TODO, NEVER)))
    print("  「真跑」那列: 真跑=日志里有凭据说这一轮真开了硬件; 替身=自报不是实测;"
          " 无凭据=读不到凭据(**判不出来, 不等于干跑**)")
    if OFF_REGISTRY:
        print("  不在册(有意列出, 免得与'忘了它'分不开):")
        for nm, why in OFF_REGISTRY:
            print("     %-28s %s" % (nm, why))
    return 1 if diff else 0


def show_list():
    """只列注册表(不读日志)。"""
    rows = registry_rows()
    for r in rows:
        print("%-6s %-9s %-30s %s%s"
              % (r["no"], r["kind"], r["script"] or "—", r["sub"],
                 ("   [" + r["note"] + "]") if r["note"] and r["kind"] != AUTO else ""))
    return 0


# =====================================================================================
# 四、轮询
# =====================================================================================
def _run_script(script):
    """起子进程跑一个子项脚本, 返回它的退出码。**不设超时**(理由见模块头那段 ⚠)。

    stdout/stderr 直接继承: 脚本自己的 `run_subitem` 会 tee 一份到 `log/`(那是它的实况记录),
    这里再抓一遍毫无意义, 还会把屏幕上的实时输出憋住 —— 跑真表时人要看的就是那个实时输出。
    """
    return subprocess.call([sys.executable, script_path(script)], cwd=ROOT)


def _reset_bench(why):
    """让 `scripts/_restore_all.py` 收拾台面。**它自己会连 J-Link 与串口**, 故只在需要时调。"""
    print("   ── 收拾台面(%s): python scripts/_restore_all.py" % why)
    rc = subprocess.call([sys.executable, RESET_ALL], cwd=ROOT)
    print("   ── 收拾台面的退出码: %d" % rc)
    return rc


def run(ids, use_all=False, bench=False, lo=None, hi=None, explicit=()):
    """轮询。`ids` 是位置参数里点名的编号; `explicit` 是**点名**集合(破坏性项靠它放行)。

    三条放行规则(改了会影响"表会不会被清"):
      ① 点名的编号一定跑(含破坏性 —— 点了名就是用户要跑);
      ② `--all` 只轮 `可跑`; 加 `--bench` 才把 `台面` 也轮上;
      ③ **破坏性永远不跟着 `--all` 走**, 只有点名才跑。
    """
    reg = _registry_dict()
    picked = []
    for no in ids:
        if no not in reg:
            print("!! 注册表里没有编号 %r" % no)
            return 2
        picked.append(no)
    if use_all:
        for no, r in sorted(reg.items(), key=lambda kv: kv[0]):
            if not r["script"]:
                continue
            if r["kind"] == AUTO or (r["kind"] == BENCH and bench):
                if no not in picked:
                    picked.append(no)
    if lo or hi:
        picked = [no for no in picked if (not lo or no >= lo) and (not hi or no <= hi)]
    if not picked:
        print("!! 一项都没选上。`run` 要点名编号, 或者给 --all(可再加 --bench)。")
        return 2

    # **跑之前先自检** —— 不过就一项都不跑。理由见 check_registry 的 docstring:
    # 在自检不过的清单上跑真表, 是"照着一份过期的清单动实表", 而这事不会有任何东西报错。
    ok, bad = check_registry()
    if not ok:
        print("!! 册子自检不过 —— **一项都不跑**:")
        for b in bad:
            print("   · " + b)
        return 2

    # 排好序、去重, 并逐项说清"这一项为什么被轮上/为什么被跳过"。
    picked = sorted(set(picked), key=lambda n: [int(x) for x in n.split("-")] if "-" in n else [int(n)])
    print("== 册子轮询: %d 项 ==" % len(picked))
    to_run, skipped = [], []
    for no in picked:
        script = reg[no]["script"]
        if not script:
            skipped.append((no, "没有脚本"))
            continue
        if not os.path.isfile(script_path(script)):
            skipped.append((no, "脚本文件不在盘上: %s" % script))
            continue
        to_run.append(no)
    for no, why in skipped:
        print("   [跳过] %-6s %s" % (no, why))
    if not to_run:
        print("!! 选上的项一个都不能跑。")
        return 2

    results = []
    try:
        for i, no in enumerate(to_run, 1):
            script, kind, sub = reg[no]["script"], reg[no]["kind"], reg[no]["sub"]
            if kind == DESTRUCT:
                # 走到这里说明它是**点名**上来的(③ 保证 --all 不会把它带进来) —— 但话要说在跑之前。
                print("\n" + "!" * 78)
                print("!! %s 是**不可逆清零**(%s)。这条命令点了它的名 ⇒ 现在就跑它。" % (no, sub))
                print("!! 跑完**清掉的东西回不来** —— 确认这是你要的。")
                print("!" * 78)
            print("\n" + "=" * 78)
            print("== [%d/%d] %s %s | %s | %s"
                  % (i, len(to_run), no, sub, kind, script))
            print("=" * 78)
            rc = _run_script(script)
            print("== [%d/%d] %s 退出码: %d ==" % (i, len(to_run), no, rc))
            lg = latest_log(script)
            print("== 这一项最新的日志: %s" % (os.path.basename(lg) if lg else "(没找到)"))
            results.append((no, rc))
            if no in LEAVES_BENCH:
                _reset_bench("%s 跑完把台面留在厂内态" % no)
    finally:
        # 收尾: 只要真跑过, 就让台面回到正常态 —— 哪怕中间抛了异常/Ctrl-C。
        # 理由见 `_restore_all.py` 的 6 步: 不收拾的话, 表钟停在改动过的时间、
        # 台面留在厂内态, 而**下一个跑的人看不出为什么**。
        if results:
            print()
            _reset_bench("本轮跑过 %d 项, 收尾" % len(results))

    print("\n== 册子轮询结果 ==")
    n_bad = 0
    for no, rc in results:
        # 退出码的说法与 `common.judge` 的契约一致(通过0/失败1/未定论2); 别的码是脚本自己炸了。
        word = {0: "通过", 1: "失败", 2: "未定论"}.get(rc, "异常退出(%d)" % rc)
        if rc != 0:
            n_bad += 1
        print("   %-6s 退出码 %d  %s" % (no, rc, word))
    print("   共 %d 项, 非 0 的 %d 项" % (len(results), n_bad))
    print("   逐项的判据与证据在各自那份日志里(本册子不代写)。")
    return 1 if n_bad else 0


# =====================================================================================
def main(argv=None):
    ensure_utf8_stdout()
    argv = list(sys.argv[1:] if argv is None else argv)
    # 开关照实登记(漏登记=正常用法被误拦, 多登记=那道判定对它失效; 宁可吵不可静默)。
    guard_argv(argv, allow=("--all", "--bench"), known=("--from", "--to"), positional=True)

    cmd = (argv[0] if argv and not argv[0].startswith("-") else "report")
    rest = argv[1:] if argv and not argv[0].startswith("-") else argv

    ids, opts = [], {}
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in ("--from", "--to"):
            if i + 1 >= len(rest):
                print("!! %s 后面要跟一个编号" % a)
                return 2
            opts[a.lstrip("-")] = rest[i + 1]
            i += 2
            continue
        if a.startswith("-"):
            opts[a.lstrip("-")] = True
            i += 1
            continue
        ids.append(a)
        i += 1

    if cmd == "list":
        return show_list()
    if cmd == "report":
        return report()
    if cmd == "run":
        return run(ids, use_all=bool(opts.get("all")), bench=bool(opts.get("bench")),
                   lo=opts.get("from"), hi=opts.get("to"), explicit=tuple(ids))
    print("!! 认不得的命令 %r —— 只有 report / list / run" % cmd)
    return 2


if __name__ == "__main__":
    # 本册子自己**也留一份运行日志**(与 21 个子项脚本同款), 于是"这一轮轮了哪些项、
    # 各自的退出码、什么时候跑的"在 `log/` 里查得到, 而不是只在屏幕上滚过去。
    # ⚠ 落 `log/工具/` —— `log/` 根下那个名字空间只装**表应答过的子项跑次**(见 `log/README.md`
    #   与 `common.portsel.live_proof`), 册子这份是工具自己的输出, 不住那儿。
    with runlog.run("suite", logdir=os.path.join(runlog.default_logdir(), "工具")) as _logpath:
        try:
            _rc = main()
        except SystemExit as e:                     # guard_argv 拦下来时走这条
            _rc = e.code if isinstance(e.code, int) else 2
        print("== 册子的运行日志: %s" % _logpath)
    raise SystemExit(_rc)
