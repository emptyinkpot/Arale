# -*- coding: utf-8 -*-
"""
project/ez315_fm33a0610.py —— EZ315 / FM33A0610 单相智能电表【工程画像】(纯数据, 只读)

本模块只收"这块表"的固有事实(对象号/地址/双芯/表号/RAM 基址), 供通用核心 meterlib 与
测试脚本经 `from project import CURRENT as P; P.XXX` 读取 —— 单一事实源, 各脚本不再硬抄。
通用协议能力(698/645 组帧、收发、解析、CRC、AA80 传输)是跨工程复用的, 放 meterlib,
不在这里、也不在此复制一份(见帧收发基础/CLAUDE.md「硬性规矩」)。
换测另一块工程 = 在 project/ 加一份同款画像, 把 project/__init__.py 的 CURRENT 指过去;
核心与脚本原样复用, 只有本文件的对象/地址数据变。

本文件保持【纯数据常量】, 不放函数/逻辑; 备注用注释/字符串, 供人读。
"""

PROJECT = "EZ315 单相智能电表"
MCU     = "FM33A0610 管理芯"        # 白盒测试对象; RAM 基址跟随这颗

# ============================ ① 双芯 698 服务器: AF + 表号(6B) + CA ============================
# 本表双芯各占一个 698 服务器地址, 差在 AF 字节; 表号=111111111111(6B); CA=0xA1(逻辑地址/会话).
CHIP_AF      = {"管理芯": 0x05, "计量芯": 0x15}   # 本表: 管理芯 AF=0x05 / 计量芯 AF=0x15
MANAGE_AF    = 0x05
METER_AF     = 0x15
TABLE_ADDR   = b"\x11" * 6                        # 645/698 表号 111111111111
SERVER_TAIL  = b"\x11" * 6 + b"\xA1"              # 698 服务器地址 = AF(1) + 表号(6) + CA(1)
# 645 广播(通配/厂内)地址是协议标准值, 归通用层, 不进画像。

# ============================ ② 芯片 RAM 基址(AA80 区内偏移换算必需) ============================
RAM_BASE = 0x20000000            # FM33A0610 SRAM 基址

# ============================ ②b 看门狗喂狗点(停核类调试的机器条件) ============================
# 断点/单步把核心**停住**时 IWDT 照跑, 约 8s 不复位表就重启, 断点全丢、整轮测试报废 ——
# 所以停核类工具(breakpoint)每次停下必须立刻写这个寄存器。地址是**这块芯片**的固有事实, 故收进画像;
# 工具本身不认表, 由脚本/Session 显式喂进去。
#   推导: PERIPH_BASE 0x40000000 + IWDT_BASE 偏移 0x11400 = 0x40011400, SERV 在结构体偏移 0x00。
#   来源: 固件 Libraries/CMSIS/.../FM33A0XXEV.h:823/848/217; 固件自己的喂狗点在 Platform/CpuCfg.c:2471。
#   ⚠ .out 的 DWARF 里**没有** IWDT 这个符号(`No symbol "IWDT"`), 所以只能从固件头取常量, 不能按名解析。
IWDT_SERV = (0x40011400, 0x12345A5A)   # (SERV 寄存器绝对地址, 喂入魔数)

# ============================ ③ RAM 变量地图(白盒 Watch 等价的基础; 固定收此) ============================
# name: (绝对地址, size, 角色, 中文备注)。地址/size 用 .out 符号核对(2026-09-09)。
# 角色: watch = 观测变量(Watch 面板等价) / stable = 结算等操作"绝不能碰"的稳定状态全局 /
#       clock = **时钟派生量**(随日期/时段/结算自动重算, 拨钟试验里必然变, 故不属 stable)。
RAM_VARS = {
    # —— watch: 操作状态(编程态/密钥态) ——
    # 这两个是"当前表处在什么准入状态"的真值源, 2026-09-10 收进画像(原先 0x2000908C 硬编码在
    # scripts/_restore_all.py 里一份):
    "g_PrgTimer": (0x2000908C, 4, "watch", "厂内/编程态计时器(Is_EnablePrg() 的真值源: TaskRecord.c:687 "
                                           "`g_PrgTimer[0]!=0`; 645.factory 置非0, 645 0x1F/0F AA 00 清 0)"),
    "g_KeyStat":  (0x200090A4, 4, "watch", "密钥状态字(0x12345678=KEY_Testing, KEY_Formal=正式密钥); "
                                           "DLT645App.c:3153 远程清零长帧路拿它当判定, 本台=KEY_Testing"),
    # —— watch: 表钟(低频锁存副本)+ 分钟冻结 FIFO 台账 ——
    "g_HisTime": (0x20009000, 8, "watch", "表钟·低频锁存副本(冻结取数时点)"),
    "g_CurTime": (0x200034D0, 8, "watch", "表钟·低频锁存副本(冻结取数时点)"),
    "g_FrezAdr": (0x200090B0, 2, "watch", "分钟冻结 FIFO 台账·地址"),
    "g_FrezNum": (0x200090B2, 2, "watch", "分钟冻结 FIFO 台账·数量(域内记账, 非总量)"),
    "g_FrezLen": (0x200090B4, 2, "watch", "分钟冻结 FIFO 台账·长"),
    # 冻结存储信息区(240B 结构体)。**不进 WATCH_VARS**: 240B 超 AA80 单次负载上限 128B,
    # 要按块读时用 named_blocks("s_stFrzStorageInfo", clamp=128) 截读前 128B。
    # (原先该地址硬编码在 4-1 的测试脚本里, 2026-09-10 收进画像当单一事实源。)
    "s_stFrzStorageInfo": (0x200089E4, 240, "watch", "瞬时冻结存储信息区(整块 240B; AA80 读须 clamp=128)"),
    # —— watch: 最大需量(4-4 的日冻结要看的第二个量: 跨 0 点那一趟之后归没归零) ——
    "g_MaxDemand": (0x20007AA8, 24, "watch", "各费率最大需量 6 项 × 4B(TaskMetering.c:290; "
                                            "清零点只在 :523 上电 / :3182 / :3210 事件清零, 冻结那一路没有)"),
    # —— clock: 费率号(时钟派生, **不是** stable —— 见 NOTES 第 4 条) ——
    "g_RateNo":   (0x20009088, 3, "clock", "当前费率号(固件 INT8U[1+2]: [0]=费率号, [1][2]=Fetch_CRC; "
                                          "每轮 MSG_MinStep 里 Calculate_RateNo() 重算 → 日期一变就变)"),
    # —— 3-1 费率参数与取表中间量(2026-09-10 收进画像; nm 核对: g_ListNo/0x35A4, g_SoltNo/0x35A5,
    #    g_RatePara/0x8FB0 长12 = 8字段+4B Fetch_CRC) ——
    # 这三个正是规格 3-1「观察与判据」点名要 Watch 的量, 收进画像后与 3-2 同一条 AA80 通路读, 免 IAR。
    "g_ListNo":   (0x200035A4, 1, "clock", "Calculate_RateNo 取到的**日时段表号**(TaskRate.c:258; "
                                          "0=未取到。三条取表路径——节假日/周休日/时区——的落点)"),
    "g_SoltNo":   (0x200035A5, 1, "clock", "Calculate_RateNo 取到的**日时段号**(TaskRate.c:264/270; "
                                          "命中第几个时段, 用于判『无错位』)"),
    "g_RatePara": (0x20008FB0, 12, "watch", "费率参数镜像(8字段+4B Fetch_CRC; 偏移 0年时区数 1日时段表数 "
                                           "2日时段数 3费率数 4公共假日数 5阶梯数 6周休日特征字 7周休日时段表号) —— "
                                           "规格点名的 g_RatePara[nRateNum] 就是偏移 3"),
    # —— stable: 时段/时区套号 ——
    "g_ZoneSwNo": (0x200090D7, 1, "stable", "时区套号"),
    "g_SlotSwNo": (0x200090D8, 1, "stable", "时段套号"),
    # —— stable: 继电器 ——
    "g_RelayCmd": (0x20009090, 4, "stable", "继电器命令"),
    "g_RelaySta": (0x200090DC, 1, "stable", "继电器状态"),
    "g_RelayFlg": (0x200090DB, 1, "stable", "继电器标志"),
    "g_RelayBlk": (0x200035AE, 1, "stable", "继电器动作闭锁标志(0xAA=闭锁: 未接PLC且未达75%Un, 见 relay_precheck)"),
    "g_CompFlg":  (0x20009020, 5, "stable", "比较标志串[CMP_060Un,065,075,120,ProtI]; "
                                            "[2]低4位=F → 电压≥75%Un(继电器动作许可)"),
    # 费控状态字。角色取 watch 而不是 stable: 12-3 要**注入**它造"透支"态, 登记成 stable
    # 会让稳定态快照把它当成"该纹丝不动的量"。
    "g_CashStatus": (0x20009074, 3, "watch", "费控状态字([0]=状态: ST_OvrCash2=4=低于透支门限 / "
                                             "ST_OvrCash1=3=清零后; [1][2]=Fetch_CRC, TaskLclFee.c:121); "
                                             "12-3 阴性对照靠它证明本次**真的**不透支"),
    # —— stable: 控制裁决 / 显示 ——
    "g_CtrlStat": (0x20003534, 4, "stable", "控制裁决状态字"),
    "g_DispPara": (0x20008F80, 16, "stable", "显示参数"),
    # 显示状态机(ST_DISP: 0全显/1轮显/2按显/3定显/4选显/5卡显/6金额/7停显/8测试)。
    # 判 `Disp_Others` 那道闸 `Is_StopDispStatus()`(:3961)用的就是它 —— 读它才知道那一帧画没画。
    "g_DispStatus": (0x200090CC, 1, "watch", "显示状态机(ST_DISP; TaskDisplay.c:3961 停显区间判据)"),
    # 液晶**影子缓冲**(ST75263S.c:9 `INT8U lcd_buffer[LCD_BUFFER_SIZE]`, 2080B=208×10页)。
    # 写像素的只有 `ST75263S_DrawPixel`(读改写 lcd_buffer 并置 lcd_need_refresh), 刷屏每秒钟整块重画。
    # **不进 WATCH_VARS**: 2080B 超 AA80 单次负载上限 128B, 要读只能按块截读(同 s_stFrzStorageInfo 的先例)。
    "lcd_buffer": (0x200090E0, 2080, "watch", "液晶影子缓冲(整块 2080B; AA80 读须按块 clamp)"),
    # —— watch: 13-1 主动上报(2026-09-22 收进画像; 地址/size 逐条 `info address` 核过 .out) ——
    # 全是 TaskReport.c 的文件级 `static __no_init`(都在 RAM, .out 里按名解析得到), 走 AA80/SWD 直读即可,
    # 不必为它们开断点 —— 断点只留给"组帧那一刻缓冲里是什么"(那在 Auto_Report 的栈上, 见 13-1 规格)。
    "g_ReportEn": (0x20008FC8, 9, "watch", "上报使能(总开关/事件/通道; TaskReport.c:85 `[LEN_ReportEn+2]` 尾 2B 是 CRC)。"
                                           "13-1 的前置: [1]!=1 或 [3+PT_PLC_M]!=1 就不上送"),
    "g_AutoRptNum": (0x2000909C, 4, "watch", "轮询上报计数(TaskReport.c:99, 尾 2B CRC): [0]=事件[n] / [1]=掉电[n]"),
    "g_FollowSta": (0x20008AD4, 28, "watch", "跟随上报状态字(TaskReport.c:91, 按通道 [PT_Num+1]=28B) —— "
                                            "规格点名的『跟随上报状态字』就是它"),
    "g_ReportIdx": (0x20008524, 504, "watch", "上报事件索引表(TaskReport.c:88 `[PT_Num][NUM_RptObj]`, 504B; "
                                              "AA80 读须按块 clamp, 见 s_stFrzStorageInfo 的先例) —— "
                                              "规格点名的『新增上报事件列表』的落点"),
    "g_AutoRptGap": (0x200090DE, 1, "watch", "轮询上报间隔剩余(初值 C_AutoRptGap=10s, TaskReport.c:76)"),
    "g_CheckAutoRptSta": (0x200090DF, 1, "watch", "轮询上报进行中标志(TaskReport.c:101)"),
}
WATCH_VARS = ["g_HisTime", "g_CurTime", "g_FrezAdr", "g_FrezNum", "g_FrezLen"]
STABLE_VARS = ["g_ZoneSwNo", "g_SlotSwNo", "g_RelayCmd",
               "g_RelaySta", "g_RelayFlg", "g_RelayBlk", "g_CompFlg",
               "g_CtrlStat", "g_DispPara"]
# 时钟派生量: 观测(看它动)+ 佐证, 但**不作"结算没野写"的检查** —— 拨钟试验里它本来就该变。
CLOCK_VARS = ["g_RateNo"]

# ============================ ④ 工程固有资源路径 + 备注(描述性数据) ============================
OUT_PATH = (r"E:\My Work\MengXi\EZ315-FM33A0610EV-APP"
            r"\Build\EZ315-FM33A0610EV-APP\EZ315-FM33A0610EV-APP.out")
# 本表"已验证帧"注册文件(叠加层, 同包内, 与画像同前缀): cmd_bank 把它当该表环境帧合并进目录,
# 纯静态已验证帧贴这。路径相对本画像所在包目录解析(引擎按 dirname(P.__file__)+本文件名算), 包整体可搬。
FRAMES_FILE = "ez315_fm33a0610.frames.json"
# 本表"环境清单"(固件锁/双芯可达/串口): env_check 读取作装包即验断言; 机器数据, 非给人读的知识。
META_FILE = "ez315_fm33a0610.meta.json"

# ---- 本表私有知识的位置(2026-09-14 收进卡带) ----
# 原先这几样散在**仓根**, 而通用层直接硬编码了它们的路径(watch_runner 的 cases71、
# discover 的 探测报告 与 总纲)—— "库一概不认表" 因此漏了个口子: 库不认表的**数据**,
# 却认了表的**文件布局**。现在改成: **路径由卡带声明, 通用层只解析**。
# 约定与 FRAMES_FILE/META_FILE 同款 —— 相对路径以**本画像所在包目录**(project/) 为基准, 包整体可搬。
TESTS_DIR     = "tests"                              # 本表的测试脚本(_test_*.py + 册子 _suite.py)
KNOWLEDGE_DIR = "knowledge"                          # 本表私有知识根(总纲/规格真源/探测报告/分析)
MASTER_DOC    = "knowledge/对表操作总纲.md"           # 唯一知识文档; discover 抽机读部分、回填写 §10.6
CASES_FILE    = "knowledge/cases71.json"             # AA80 用例数据层(watch_runner 执行器的输入)
REPORTS_DIR   = "knowledge/探测报告"                  # discover 的产物(该表各镜像的探测报告)
NOTES = [
    "结算日冻结记录写【外部存储】, 管理芯 RAM 无命名台账顶序号(经验总结 §15); 白盒证据看表钟变量.",
    "校时 Set 40000200 只认 →计量芯(AF=0x15); 管理芯同帧 Set 被拒 DAR=FF(负知识, 勿用).",
    "实时计量采样量(g_Volt/g_Curr/g_PowP/g_PowQ)随 AFE 每周期刷新, 非'稳定态', 故不进 STABLE_VARS.",
    "g_RateNo 是**时钟派生量**, 不进 STABLE_VARS(2026-09-10 4-6 跑出来的): 固件 Run_TaskRate.c 在每轮 "
    "MSG_MinStep 里 Calculate_RateNo() 重算费率号(且先 Check_Switch_BillDay), 一变就 Fetch_CRC+写库。"
    "4-6 的链A 故意把钟从 09-10 拨到 10-05(跨月+跨结算日) ⇒ 费率号 4→1、CRC 随之变, 是**表的预期行为**, "
    "不是结算路径野写。它进 stable 会让'稳定态快照'每跑必红, 真野写反而被淹没。改登记为 role=clock + CLOCK_VARS。",
]

# ============================================================================
# ③ 固件事实 —— 从本表固件反推出来的常量(值 + 源码出处)
#
# 这些原先长在 meterlib/cmd_bank.py 里 —— 那是"驱动"，换一块表就得跟着改。
# 它们是**这块表**的事实(事件编码/子类号/结构体步长/版本字符串)，故收进画像。
# cmd_bank 侧留一行 `X = P.X` 重导出，本模块只有数据、没有逻辑。
# ============================================================================

# ============================ 〔CURKWH〕电能数据 + 〔ENE〕计量帧 ============================
CURKWH_NENY = 10       # 电类数(kWhData.c:450 循环上界)
CURKWH_SLOT = 10       # 每项字节 = 8B 电能 + 2B CRC(kWhData.h:17 `LEN_CurkWh`)
CURKWH_STRIDE = 13     # 电类步长(行) = 1 + C_RateNum; 由 .out 符号 size=1300 定死
ENE_FRAME_OFF = 110    # 计量帧内首个电能字段字节偏移(kWhData.c:374)
ENE_FRAME_STEP = 9     # 帧内每电类字节数(:374→:381 差 9)
# ⚠ 每项头 **1B 是 698 类型/引导码**(实测全是 0x15, 与 698 整列每项的 lead 同一套编码),
#   8B 小端值紧随其后 ⇒ **值读 +1 处**。2026-09-11 首跑漏了这 1B, 10 个电类全读成 21(=0x15),
#   差点当成"帧侧偏移不对"去改判据; 实据 = 同一次的存侧 `g_CurkWh[13*9][0]=150` 与帧侧
#   领先字节 0x15 并置, 一眼看出读的是 lead 不是值。
ENE_FRAME_LEAD = 1
# g_CurkWh 行序 → 名字(与 :16 注释「正、反向有功，四象限无功，正、反向基波，正、反向谐波」一致)
CURKWH_ENY_NAMES = ("正有功", "反有功", "Q1无功", "Q2无功", "Q3无功",
                    "Q4无功", "正基波", "反基波", "正谐波", "反谐波")
# 各电类在 698 侧的 eny 号(= 经 TAB_EnyChg 反查): 正有功 1 / 反有功 2 … 反谐波 12
CURKWH_ENY_698 = (1, 2, 3, 4, 5, 6, 7, 8, 9, 12)
CURKWH_BP_ANCHOR = ("kWhData.c", 448)   # Update_Rate_Energy 入口 = Save_CurEnergyData 已写毕、X 帧仍在缓冲
CURKWH_VAR = "g_CurkWh"                 # 白盒直读的变量名(地址由画像/.out 解析, 脚本不摸硬地址)

# 698 电能 OAD(byte2=04 高精度族) → g_CurkWh 行号。**必须显式列, 不能用 `byte1>>4` 推**
# (2026-09-11 实踩): `001004`(正有功)与 `011004`(正基波)的 byte1>>4 **都等于 1**, 推出来的行号会撞车。
# 表来源 = DLT698App.c:6933-6950 的 case 组(`pOAD[1]>>4` 当 eny)∪ :6985-6999 的 `u8=9..12`
# (`TAB_EnyChg[u8]` 再转), 两条都经 `Read_CurkWh` 的 `eny = TAB_EnyChg[eny]` 落到同一个行序。
# 组合类(000004/003004/004004)在 g_CurkWh 里**没有对应行**(固件按组合方式现算) ⇒ 不入表。
CURKWH_OAD_ROW = {
    "00100400": 0,   # 正向有功
    "00200400": 1,   # 反向有功
    "00500400": 2,   # 第一象限无功
    "00600400": 3,   # 第二象限无功
    "00700400": 4,   # 第三象限无功
    "00800400": 5,   # 第四象限无功
    "01100400": 6,   # 正向基波有功
    "01200400": 7,   # 反向基波有功
    "02100400": 8,   # 正向谐波有功
    "02200400": 9,   # 反向谐波有功
}

# ============================ 〔WB〕帧观察 ============================
WB_STR1 = "'Run_TaskVessel'::STR1_Index"
# `STR1_Index` 是**别的函数**(Run_TaskVessel)的静态局部, 本断点所在的 Update_Rate_Energy 里
# 既没定义也没赋值 —— 故 `scripts/_check_anchors.py` 会如实报"断点那一行之前没有赋值"(不是缺陷, 是它
# 管不到的范围: 跨函数的静态局部)。它凭什么可信 = **下面这条见证字节**: 698 帧在缓冲里的首字节
# 就是 0x68(Communicate.c:1035-1037 用 `g_SPIMBuff[STR1_Index+331]==0x16` 收尾、对 +1 起 330B 校 CRC)
# ⇒ 若 `STR1_Index` 是垃圾, 这一条当场不为 0x68, 判①就记"没做成"而不是拿垃圾当"来值"。
WB_FRAME0 = "*(unsigned char*)&g_SPIMBuff['Run_TaskVessel'::STR1_Index]"
WB_FRAME0_OK = 0x68

# ============================ 〔SV〕软件版本 ============================
SV_ID = "EZ315-003-KP15-A287-V0014-260827"   # K_SoftVersion(Config\SoftVersion-…-APP.h:9), 恰好 32 字符
SV_ID_B = SV_ID.encode("ascii")               # = 固件里 `TAB_SoftVer[32]` 的字节(UserCfg.c:19 逐字赋的)
SV_ID_REV = SV_ID_B[::-1]                     # `RevCopy_Data` / `Reverse_Data` 之后(698 的 buff 里 / 645 厂内线)
SV_VER_BYTES = len(SV_ID_B)                   # = sizeof(TAB_SoftVer) = 32
SV_OAD_VER = "FF300500"                       # 698: 内部软件版本
SV_OAD_TOTAL = "FF300200"                     # 698: 程序集成标识 · 整片 FLASH
SV_OAD_FACT = "FF300300"                      # 698: 程序集成标识 · 出厂区
SV_OAD_APP = "FF300400"                       # 698: 程序集成标识 · 应用区
SV_OAD_IDS = ((SV_OAD_TOTAL, "整片"), (SV_OAD_FACT, "出厂区"), (SV_OAD_APP, "应用区"))
SV_DI_VER = "04CC0000"                        # 645: 读版本(线上序 00 00 CC 04 → CMD_ReadData04)
SV_DI_FAC = "E1000000"                        # 645: 厂内标定 · 读版本(线上序 00 00 00 E1 → 厂内支)
SV_IDS = 8                                    # `LHEX_nBCD(buff+9, u32, 4)` ⇒ 8 位十进制
SV_MOD = 10 ** SV_IDS
SV_APP_LO, SV_APP_HI = 0x4000, 0x80000        # FLASH_APP_BASE / FM_FLASH_SIZE
SV_FORMS = ("长度字节+正文", "纯正文", "八位位组串(0A+长度+正文)")   # 数据域封装(为什么都认见 sv_ascii)

# ============================ 〔DISP〕显示 ============================
DISP_OI_HARM = "0220"          # 反谐波有功: 本台空载下**唯一非零**的电类 ⇒ 唯一有分辨力的位数族
DISP_DIGIT_ATTRS = (("02", "2位"), ("04", "4位"), ("06", "尾数"), ("08", "6位"))
# 标量 GET 应答的类型字节 → 值字节数。**实测口径, 不是标准表照抄**(2026-09-11 真表打出
# 原始 hex 后定死): 05=4B / 0F=1B / 14=8B。
# ⚠ 表里没有的类型**一律不认**(`disp_scalar_ud` 返回 None)—— 猜长度 = 静默取到错字节,
#   而"取错字节"在这里恰好会**伪造**出判据③的关系式, 是最坏的一种错。
DISP_TYPE_LEN = {0x05: 4, 0x0F: 1, 0x14: 8}
DISP_TAIL = 2                  # 值之后固定 2B 外壳(split_apdu 已剥掉 FCS+16, 这 2B 是应答自带的)
DISP_IDX0 = 0x01               # 索引字节 01 ⇒ 第 0 项(DLT698App.c:6957 `i=(pOAD[3]==0)?0:pOAD[3]-1`)
DISP_NIDX = 5                  # 本台对外 5 项(= 1 + 运行期费率数 4)

DISP_DOT_IDX = 8               # g_DispPara[DotE]   (TaskDisplay.h:18-22)
DISP_BORROW_IDX = 11           # g_DispPara[Borrow]
DISP_DOT_NAME = "g_DispPara[%d]" % DISP_DOT_IDX
DISP_BORROW_NAME = "g_DispPara[%d]" % DISP_BORROW_IDX
DISP_VAR = "g_DispPara"        # 收尾复核要 AA80 直读的那块静态 RAM(.out size=16)
# `Session(inject_allow=…)` 的白名单是**符号名精确匹配** ⇒ 这三条就是本子项能写的**全部**。
# `u64` 是 `Disp_Energy` 的栈局部(8B, 我们本次会话已把 `inject()` 的 8B 标量截断修掉了);
# `g_DispPara[8]/[11]` 是全局量 —— 两者的收尾语义不同(栈槽不还原, 见 `inject` 的 ⚠)。
DISP_INJ_VARS = ("u64", DISP_DOT_NAME, DISP_BORROW_NAME)
# 显示逻辑的合法位数量纲(源码侧): `switch(g_DispPara[DotE])` 只认 case 0..4(:3139-3143)。
# ⚠ 但**配置侧的上限是 2**: `TAB_DispParaLmt[DotE] = {0,2}`(TaskDisplay.c:94, 本版未定义
#   VER_20Edit)⇒「4 位小数」在本版固件里**不是一个可保存的配置值**, `Renew_DispPara:1746`
#   会把它按范围表修回默认。这不是我们要证的判据, 是本次实测要**报出来**的台面事实。
DISP_LMT_DOT_CFG = (0, 2)
DISP_DOT_SWITCH_MAX = 4

# 断点(已离线核过 `info line`, 全部落在真可执行语句上):
DISP_BPT_SWITCH = ("TaskDisplay.c", 3137)    # switch(g_DispPara[DotE]) 的载入点 @Disp_Energy+2438
DISP_BPT_END = ("TaskDisplay.c", 3199)       # Decode_MainLine(buff, num, dot) @Disp_Energy+3286
DISP_BPT_SETDP = ("TaskDisplay.c", 1849)     # Set_DispPara 内的 Fetch_CRC @Set_DispPara+70
# `u64` 门槛是 1e12, 而本台自然值最大 = 5 字节帧域 2^40-1, 经 `/10000` 后 ≈1.1e8 —— **差四个数量级**
# ⇒ 自然进位在本台面上**不可能发生**(TP_SoftVer==TP_Debug ⇒ :3146 的 `/=C_IMP` 根本没编进去)。
# 要看 :3171 只能把 `u64` 注到大。注 1e17: DotE=2 时 /1e4 → 1e13 ⇒ 落 :3154/:3157 的 dot=2 档。
DISP_BIG_U64 = 10 ** 17

# ============================================================================
# ③ 固件事实 —— 从本表固件反推出来的常量(值 + 源码出处)
#
# 这些原先长在 meterlib/cmd_bank.py 里 —— 那是"驱动"，换一块表就得跟着改。
# 它们是**这块表**的事实(事件编码/子类号/结构体步长/版本字符串)，故收进画像。
# cmd_bank 侧留一行 `X = P.X` 重导出，本模块只有数据、没有逻辑。
# ============================================================================

# ============================ 〔LP〕掉电 ============================
LP_EV_CODE = 0x11                 # 事件编码: 掉电(DLT698App.c:2981 事件记录表项 0x30110B64)
LP_EV_IDX = 52                    # TaskMetering.h EV_LostPower = 52(0x34, 已 `whatis` 核对)
LP_DLY = (4, 1)                   # TAB_LostPDly[] (UserCfg.c:318) = 进入 4s / 退出 1s
LP_VOLT_HIGH = 140000             # 注入用的判据反例: 140.000V > C_60Un(132.0V); 单位 mV
LP_BP_JUDGE = ("TaskMetering.c", 2415)      # 判据行 @0x24ecc Chk_LostPower+4 —— **注入停靠点**
LP_BP_WR_START = ("TaskMetering.c", 3991)   # 「记录开始」写库位置 @0x26778 Recd_LostPower+212
LP_BP_WR_END = ("TaskMetering.c", 4006)     # 「记录结束」写库位置 @0x267ce Recd_LostPower+298
LP_BP_RPTSTA = ("TaskReport.c", 2229)       # `g_CheckAutoRptSta = bFlag` @0x1b7fe(bFlag 在 $r0)
# ⚠ `LP_BP_RPTSTA` 是**唯一**的: 全仓 grep `Set_CheckAutoRptStaFlag` 只有 TaskMetering.c:3994
#   一处调用(「记录开始」支的收尾) ⇒ "停到 :2229"就等价于"刚落的正是『发生』那一笔", 没有别的调用者冒充。
# ⚠ 上面四条断点的**离线核对**(2026-09-11, `gdb-multiarch -ex "info line/scope"` + `scripts/_check_anchors.py`):
#   :2415 → 0x24ecc Chk_LostPower+4(真可执行, 不是 :2413 那种赋值空档); `g_Volt[0]` 是**全局**,
#           断点前无赋值是正常的(它由别的任务写), 在 :2415 停住读的正是**注入前**的当拍值。
#   :3991 → 0x26778 Recd_LostPower+212; 该断点**不读任何变量**(只证"执行到这条写库语句"),
#           故 `watch_vars=()` —— 别往那儿塞 `secs`(它在 :3991 还没被 :4005 算出来)。
#   :4006 → 0x267ce Recd_LostPower+298; `secs` 在断点前最后一次赋值 = :4005 ✓ 读得到。
#   TaskReport.c:2229 → 0x1b7fe; `bFlag` 是**形参**($r0), 由 :3994 的调用方给 ✓。
#   ⚠ 这四条断点住在**库里**(`LP_BP_*`), 而 `scripts/_check_anchors.py` 只扫脚本里的 `BP_*` 常量
#     ⇒ **自动那条路扫不到它们**(`_check_anchors.py` 对 5-3 报出来的 n_anchors 不含这四条)。
#     这不是"没核过" —— 核法就是本段记录的 `--anchors` 那条路, 结果如上; 换断点时**手工重核一次**。
LP_VARS_JUDGE = ("g_Volt[0]", "g_EventSta[EV_LostPower]", "g_EventTmr[EV_LostPower]")
# ⚠ 上面三个量在 :2415 那一停全部可读(全局)。对照用途: `at_vars` 给的是**注入前**的读数 ⇒
#   "124000 → 140000 这一次改写"才说得清(2-1 的教训: 注在原值上与真改了字节, 账本里长得一样)。
LP_TRUE = 170                      # BOOL 枚举 {FALSE=85, TRUE=170}(DWARF 里打印成枚举名, 注入要写数值)
# 两条一起写: 抬压让该拍判据翻假(g_Volt[0] 那一半), 同时把去抖终态写成 TRUE 让守卫看到"sta 为真"
#   —— 5-3 判据⑥ 要的这个组合(上一行已完整却仍报『要发生』)本台自然造不出, 只能这么造。
LP_INJECT_ASSIGNS = (("g_Volt[0]", LP_VOLT_HIGH), ("g_EventSta[EV_LostPower]", LP_TRUE))
LP_RCSD3 = ("20220200", "201E0200", "20200200")   # 序号 + 发生时间 + 结束时间(DLT698App.c:2447-2449)
# 掉电事件对象登记的**七列**逐条抄自 DLT698App.c:2979-2988 的 TAB_LostPower。
# ⚠ 这里**没有**『次数』与『累计时长』两列 —— 5-3 判据⑦ 量的就是这件事; 别照规格正文往这儿补。
LP_COLS7 = ("20220200", "201E0200", "20200200", "20240200", "33000200", "00102201", "00202201")
SVD_ISR_ADDR = 0x4001280C          # SVD_BASE(0x40012800) + ISR(0x0C): bit8 为 1 表示有电
LP_TRUE_TOKENS = ("TRUE", "170", "1")             # BOOL 打印形态: 枚举名 / 十进制 / 十六进制
LP_CRIT_HEAD = {"①": "发生", "②": "恢复"}

# ============================ 〔EVT〕事件计时 + 〔OVL〕过载 + 〔RVP〕功率反向 + 〔掩码〕事件掩码 ============================
TAB_MASK = (0x0200, 0x0400, 0x0800, 0x0E00)   # TaskMetering.c:1023 TAB_Mask[] = {A,B,C,总}
EVT_C_EVEFLT = 3                   # TaskMetering.c:72 C_EveFlt: 移位寄存器位数(掩码 0x07)
EVT_C_EVDLY = 4                    # TaskMetering.c:73 C_EveDly: 去抖秒数下钳位
EVT_TMR_JUMP = 56                  # 把去抖计时写高到"够"的值(见段头: 只缩短等待, 不动 delay 的计算)
# 逐拍重注(`breakpoint.inject_hold`)的窗口: 去抖要 `clamp(去抖参数−C_EveFlt, ≥C_EveDly)` 拍 —— 5-2 实测
# 57 拍(`OverLoadDelay=60`), 探针跑 58 拍才翻 TRUE。`EVT_HOLD_TICKS` 留余量(到点就停, 不是固定耗时);
# `EVT_HOLD_BUDGET` 是墙钟兜底, 必须**大于** `ticks × 单拍耗时`, 否则窗口没走完就断 ⇒ 只能记"没做成"、
# 拿不到"真证伪"那一档。⚠ 单拍耗时**不是**固件的 1 秒心跳 —— 探针实测 58 拍 703s ≈ **12 s/拍**,
# 大头是每次停靠上的 gdb MI 往返(读 6 个全局量 + 写 2 处), 不是等固件。故 80 拍 × 12s ≈ 960s,
# 兜底取 1500s(25 分钟): 证伪那一趟要把窗口跑满, 本来就是最贵的一次。
# `EVT_HOLD_WATCH` = 等判据断点的单拍秒数: 判据断点与注入点在**同一次执行里**
# (`:1882` 在前, `:2021` 在后)⇒ 到位是**微秒级**; 而等太久会把**下一拍**的停靠当成"掠过的非目标断点"
# (`wait_break` 会把每一个非目标停靠放行掉)—— 那就白丢一拍(连丢 3 拍去抖就归零)。故取 0.5s:
# 远大于微秒级, 又短于 1 Hz 的拍间隔。
EVT_HOLD_TICKS = 80
EVT_HOLD_BUDGET = 1500.0
EVT_HOLD_WATCH = 0.5
# `EVT_LAND_TICKS` = 「只需落地一拍」那四段(补完 / 恢复 / 二次发生 / 末段)的逐拍重注拍数。
# 这四段注的是 `g_EventTmr[idx] = 56`, 而 `:2004 if (g_EventSta[idx] == state[i]) g_EventTmr[idx] = 0;`
# 在同一趟里**紧跟其后**(注入点 `:1882` 在前, 那句在 `:2004`): 先写、后清。**只要这一拍两值相等,
# 注入就不留任何痕迹**, 且**下一拍还会一样** —— 相等是稳态, 不是"恰好"。故重注**救不了**,
# 必须让注入把 `g_EventSta[idx]` 一并钉到 `state[i]` 的**反面**(见 `OVL_INJ_*_FAST` 的段头)。
# 拍数取 3 而不是 80: 状态钉对之后, 计时一累到 delay 就在**同一次执行里**走到写库口, 1 拍即落地;
# 3 拍是余量。80 拍只在**证伪**那一趟才该付(见 `EVT_HOLD_TICKS` 的段头)。
EVT_LAND_TICKS = 3
EVT_RCSD3 = ("20220200", "201E0200", "20200200")   # 序号 + 发生时间 + 结束时间(与 5-3 同一组列 OAD)
# ⚠ **四个断点与四组 watch 变量不在这里** —— 它们由脚本以字面量元组持有并递进来(见
#   `_meas_event_roundtrip` 的 `bp_*`/`*_vars` 参数)。库里再留一份的下场: `scripts/_check_anchors.py`
#   只扫脚本, 判定照样全绿, 而"实际停在哪一行"没人核过(5-3 的 `LP_BP_*` 就是这么留下的盲区)。

# ---- 记录区容量: 落库那条路通不通, 先看这个数 ----
# `TAB_Recd[ID].num`(RecdData.c:18 `TAB_Recd[ID_RecdNum]`)是**该事件留几条记录**。
# 它为 **0** 时这个记录区是被**屏蔽**的 —— 不是"暂时没数据", 而是**存不进去也读不出来**:
#   · `Read_RecdData` 在 RecdData.c:448-455 第一段就 `return FALSE`, **且一个字节都不写 `pBuff`**
#     (真正的"空区"走的是 :510-513 `Set_Data(pBuff,0x00,len)` 那条 —— 那条会清零)。
#   · `Write_RecdData` 在 :575 `Get_RecdIdxNum` 失败后 :590-593 直接 `return FALSE`。
# ⚠ 这条事实**连带一处未定义行为**: `Recd_OverLoad`(TaskMetering.c:3610)不看
#   `Read_RecdData` 的返回值, 紧接着 :3611-3627 的守卫就拿 `buff[EM_Day]`/`buff[86+EM_Day]` 判
#   "有头无尾" —— 读的是**未初始化的栈**(`buff[LEN_OverLoad]` = 95 字节局部数组)。
#   于是"守卫放不放行"取决于栈上残留, 而 `sta == FALSE` 那一半又把"放行"变成"必须放行"。
# 实测读法(2026-09-18, **离线**: `arm-none-eabi-gdb -batch <out> -ex "p TAB_Recd[ID_RevPowerT]"`,
# 不需要接表、不需要停核): `.out` 建于 2026-09-08, 晚于 RecdData.h/.c(08-28/08-24) ⇒ 同源。
#   `TAB_Recd[ID_OverLoadA] = {num = 10, len = 95, dAddr = 10, iAddr = 18666}`
#   `TAB_Recd[ID_RevPowerT] = {num =  0, len = 95, dAddr = 16, iAddr = 18693}`
EVT_REC_NUM_OVERLOAD = 10          # RecdData.h:153  NUM_OverLoad = 10u
EVT_REC_NUM_REVPOWER = 0           # RecdData.h:154  NUM_RevPower = 0u  ← 本台功率反向**落不了库**
                                   # ⚠ 同一条 `NUM_RevPower` 还被 DLT645App.c:8537 与
                                   #   DLT698App.c:2963/2965 拿去登记两族的记录 OI(0x1B00 / 0x30070B0A)
                                   #   ⇒ 帧侧**照样报得出这个口**, 只是容量是 0。9-2 的读回因此
                                   #   永远是一段"空区"应答, 而不是报错。

# ---- 9 个测量类事件的公共循环基址(`Chk_OverLoad` 那一趟) ----
# `TaskMetering.c:2002` 是 `for (i=0; i<9; i++)`, 一趟把 `EV_OverLoadA .. EV_OverLoadA+8` 九个
# 测量类事件算完, `:2021` 调的是 `Recd_OverLoad(EV_OverLoadA+i)`。⇒ **停在调用点时 `i` 是循环
# 计数器(0..8), 不是事件号** —— 要判"这一次命中的是不是本子项那一号", 必须 `EV_OverLoadA + i == idx`。
# 2026-09-18 实测栽过: 判据拿 `i` **直接比** `OVL_EV_IDX`(21) / `RVP_EV_IDX`(24), 于是 5-2 / 9-2 / 9-3
# 三个子项各记一条**归错人的 FAIL**(日志里读回的 `i=0`(过载)、`i=3`(反向 24−21) 全是对的)。
EVT_MEAS_LOOP_BASE = 21            # = EV_OverLoadA(TaskMetering.h enum)

# ---- 过载(5-2 / 9-3): 索引 21 = EV_OverLoadA, 相位判定 = TAB_Mask[0] ----
OVL_EV_CODE = 0x08                 # 事件编码: 过载(记录 OAD 30080B0A)
OVL_EV_IDX = EVT_MEAS_LOOP_BASE    # = 21(== TAB_RecdId 里 ID_OverLoadA 的下标)
OVL_MASK_A = 0x0200                # TAB_Mask[0] —— A 相"已启动"那一位
OVL_RCSD3 = EVT_RCSD3
OVL_INJ_ON = (("g_PowP[0]", "g_EventSet.OverLoadPlower + 1"),
              ("g_SFlag", "g_SFlag & ~0x%04X" % OVL_MASK_A))
OVL_INJ_OFF = (("g_PowP[0]", "0"),)
# 「跳时」两段 = 判据条件 + `g_EventSta` 的**反面** + Tmr 写高。
# 为什么非得钉 `g_EventSta`: `Chk_OverLoad` 每趟把这九个索引算一遍, 而
#   `:2004 if (g_EventSta[idx] == state[i]) g_EventTmr[idx] = 0;`
# 把"状态没变"当成"去抖从头来"。跳时注入写高 Tmr, 而 Tmr 正是在这一句上被清掉的 ——
# 只要 `g_EventSta[idx] == state[i]`, 写多高都是白写, 每一拍都一样, 重注也救不了。
# 两个值**在真实发生顺序里本来就是一反一正**: 事件没开始时 `g_EventSta=FALSE`/`state=TRUE`
# (发生), 事件进行中 `g_EventSta=TRUE`/`state=FALSE`(恢复)。台面上因为「发生」那一笔落不进去
# (`g_EventSta` 停在 FALSE), 恢复段就走不到写库口 —— 表象是"固件不落库", 真相是
# **前一段留下的状态没跟上**。故这里把它一并注进去, 让每段自带它所假设的前置状态,
# 不靠"上一段跑成了"。
OVL_INJ_ON_FAST = OVL_INJ_ON + (("g_EventSta[%d]" % OVL_EV_IDX, "FALSE"),
                                ("g_EventTmr[%d]" % OVL_EV_IDX, str(EVT_TMR_JUMP)),)
OVL_INJ_OFF_FAST = OVL_INJ_OFF + (("g_EventSta[%d]" % OVL_EV_IDX, "TRUE"),
                                  ("g_EventTmr[%d]" % OVL_EV_IDX, str(EVT_TMR_JUMP)),)
# ⚠ **必须写 `TRUE`(或 `0xAA`), 不能写 `1`** —— 2026-09-18 实测栽过, 且栽了整整一天没人看出来。
#   本固件的 `BOOL` 是**三值枚举**(`Config/TypeDef.h:24-29`):
#       FALSE = (INT8U)0x55,  TRUE = (INT8U)0xAA,  OTHER = (INT8U)0x66
#   —— 不是 C 的 0/1。写 `1` 得到的是 `0x01`, **既不是 TRUE 也不是 FALSE**, 而 `Recd_OverLoad`
#   的守卫(`:3611-3627`)两条路都是拿 `!=(不等于)` 判的:
#       :3616 `if (TRUE  != sta) return;`      :3623 `if (FALSE != sta) return;`
#   ⇒ `sta=0x01` 时**两条路都 return**, 这个写库动作根本走不进去。
#   离线旁证(`disassemble Recd_OverLoad`, 不接表不停核): 编译器把 `TRUE`/`FALSE` 编成了
#   `cmp r6, #170 @ 0xaa` / `cmp r6, #85 @ 0x55` —— 真值就是 0xAA/0x55, 没有第二种读法。
#   后果: 「基线最新一条有头无尾」那一趟补完(`cmd_bank.py:6723-6742`)**从来没有做成过** ——
#   它既不报错也不落库, 只表现为"等 :3653 超时"; 于是 2026-09-17 那条有头无尾行一直挂到
#   今天(串口逐次读回都是「结束=(未结束)」), 而其后每一段的「发生」都因守卫(`sta=FALSE` +
#   有头无尾 ⇒ :3618 return)被挡下 —— 表象是"固件不落库", 真相是**注入值给错了**。
#   `TRUE` 这个名字 gdb 认得(枚举常量在调试信息里, 离线实测 `print (int)TRUE` → 170)。
OVL_INJ_HEAL = (("g_EventSta[%d]" % OVL_EV_IDX, "TRUE"),
                ("g_EventTmr[%d]" % OVL_EV_IDX, str(EVT_TMR_JUMP)))   # 基线有头无尾时的收尾

# ---- 功率反向(9-2): 索引 24 = EV_RevPowerT(总) ----
# ⚠ **为什么是 24(总)而不是 25(A 相)**: `DLT698App.c` 的 `TAB_RecordObj[]` 里 OI `0x30070B0A`
#   有两条 —— `EV_RevPowerA` 在 `#ifndef VER_20Edit` 里、`EV_RevPowerT` 在 `#else` 里, **只编进去一条**。
#   本工程 `Config/MengXi/UserCfg.h:15` 定义了 `VER_20Edit`(且全仓只有这一份 `UserCfg.h`,
#   `Config\MengXi` 在 .ewp 的头文件搜索路径里)⇒ 编进去的是 `EV_RevPowerT`, 索引 24。
#   判据旁证: `TAB_RecdId[24].dly = 54` == `offsetof(TP_Eve, RevPowerDelay)`(gdb `ptype /o` 实读),
#   与 `:2010` 那句 `*((INT8U*)&g_EventSet+TAB_RecdId[...].dly)` 对得上。
RVP_EV_CODE = 0x07                 # 事件编码: 功率反向(记录 OAD 30070B0A)
RVP_EV_IDX = 24                    # TaskMetering.h EV_RevPowerT = 24
RVP_MASK_T = TAB_MASK[3]           # 0x0E00 —— 总"已启动"那三位(A|B|C)
RVP_QUAD_T = 0x01 << 3             # g_PowQuad bit3 = 总有功功率反向(TaskMetering.c:262 位序注释)
RVP_POW_T = 3                      # g_PowP[3] = 总有功功率(INT32U g_PowP[4])
RVP_RCSD3 = EVT_RCSD3
RVP_INJ_ON = (("g_PowP[%d]" % RVP_POW_T, "g_EventSet.RevPowerPlower + 1"),
              ("g_SFlag", "g_SFlag & ~0x%04X" % RVP_MASK_T),
              ("g_PowQuad", "g_PowQuad | 0x%02X" % RVP_QUAD_T))
# ④ 的判定: 过门槛 + 相启动判定都满足, **只**把反向标志位清掉 ⇒ 该支一行都不该落。
RVP_INJ_NOQUAD = (("g_PowP[%d]" % RVP_POW_T, "g_EventSet.RevPowerPlower + 1"),
                  ("g_SFlag", "g_SFlag & ~0x%04X" % RVP_MASK_T),
                  ("g_PowQuad", "g_PowQuad & ~0x%02X" % RVP_QUAD_T))
RVP_INJ_OFF = (("g_PowP[%d]" % RVP_POW_T, "0"),)
RVP_INJ_ON_FAST = RVP_INJ_ON + (("g_EventTmr[%d]" % RVP_EV_IDX, str(EVT_TMR_JUMP)),)
RVP_INJ_ON_NOQUAD_FAST = RVP_INJ_NOQUAD + (("g_EventTmr[%d]" % RVP_EV_IDX, str(EVT_TMR_JUMP)),)
RVP_INJ_OFF_FAST = RVP_INJ_OFF + (("g_EventTmr[%d]" % RVP_EV_IDX, str(EVT_TMR_JUMP)),)
# ⚠ 同 `OVL_INJ_HEAL`: 必须 `TRUE`(=0xAA), **不能写 1** —— `BOOL` 是三值枚举, 理由见上面那一段。
RVP_INJ_HEAL = (("g_EventSta[%d]" % RVP_EV_IDX, "TRUE"),
                ("g_EventTmr[%d]" % RVP_EV_IDX, str(EVT_TMR_JUMP)))

# ============================ 〔OVL〕过载 + 〔RVP〕功率反向 ============================
OVL_SPEC = {
    "code": OVL_EV_CODE,
    "name": "过载",
    "idx": OVL_EV_IDX,
    "idx_txt": "A 相过载(21)",
    # `:2021` 停在调用点时读到的 `i` 是循环计数器 ⇒ 判据比的是 `loop_base + i`, 不是 `i` 本身
    "loop_base": EVT_MEAS_LOOP_BASE,
    "dly_field": "g_EventSet.OverLoadDelay",
    # `:1883 if (limit != 0)` 那一判定用的参数 —— 读回 0 说明该支整段不启用(见驱动的 `_branch_off`)
    "limit_field": "g_EventSet.OverLoadPlower",
    "inj_on": OVL_INJ_ON, "inj_off": OVL_INJ_OFF,
    "inj_on_fast": OVL_INJ_ON_FAST, "inj_off_fast": OVL_INJ_OFF_FAST,
    "inj_heal": OVL_INJ_HEAL,
    "on_txt": "g_PowP[0]=OverLoadPlower+1 且清掉 g_SFlag 的 A 相位",
    "off_txt": "g_PowP[0]=0",
    "snapshot": True,
    "rec_quota": EVT_REC_NUM_OVERLOAD,
    "rec_quota_txt": "NUM_OverLoad(RecdData.h:153)",
    "neg_legs": (
        {"crit": "⑥", "txt": "稳态(PowP 恒 0、相启动判定常闭)", "inj": None, "win": "sample_gap",
         "falsify": "稳态下 `g_EventSta[idx] == state[i]` 本应恒成立(:2004 每拍把去抖计时清零、"
                    "永不累到 delay), 故调用点整个窗口一次都不该被走到 —— 走到就说明 `state[i]` "
                    "被 :1993-1996 那段移位寄存器锁在 TRUE 上(`:1985` 只要 `state[i]==TRUE` 就置位, "
                    "`:1993` 只要低三位满就置 `state[i]=TRUE`, 两者自锁, 一旦满就再不衰减)"},
    ),
}

RVP_SPEC = {
    "code": RVP_EV_CODE,
    "name": "功率反向",
    "idx": RVP_EV_IDX,
    "idx_txt": "总有功功率反向(24)",
    "loop_base": EVT_MEAS_LOOP_BASE,     # 同上 —— 24 在这一趟里对应 `i=3`
    "dly_field": "g_EventSet.RevPowerDelay",
    "limit_field": "g_EventSet.RevPowerPlower",
    "inj_on": RVP_INJ_ON, "inj_off": RVP_INJ_OFF,
    "inj_on_fast": RVP_INJ_ON_FAST, "inj_off_fast": RVP_INJ_OFF_FAST,
    "inj_heal": RVP_INJ_HEAL,
    "on_txt": "g_PowP[3]=RevPowerPlower+1 且清掉 g_SFlag 的总相位判定、置 g_PowQuad 的 bit3",
    "off_txt": "g_PowP[3]=0",
    # 9-2 的 ④ 就是**它独有**的那条: 判定是 `(g_PowP[3] > limit) && (g_PowQuad & 0x08)`,
    # 前半满足、后半不满足 ⇒ 一行都不该落。与 ①(两半都满足 ⇒ 落)合起来才钉得住那个 `&&`。
    "snapshot": False,
    # ⚠ **本台这一格是 0** ⇒ `Recd_OverLoad` 存不进也读不出(见 `EVT_REC_NUM_REVPOWER` 那段)。
    #   判据 ①②③ 与 ④ 里"落不落库"那一半因此**本台证不了** —— 不是固件判据算错, 是这个口没容量。
    "rec_quota": EVT_REC_NUM_REVPOWER,
    "rec_quota_txt": "NUM_RevPower(RecdData.h:154)",
    "neg_legs": (
        {"crit": "⑥", "txt": "稳态(PowP 恒 0、两个判定都闭)", "inj": None, "win": "sample_gap",
         "falsify": "判据不成立也走到调用点 ⇒ 去抖/移位那段逻辑不对(如 :2004 不复位 Tmr) "
                    "⇒ 每个 delay 秒会乱落一条"},
        {"crit": "④", "txt": "过门槛**但** g_PowQuad 的 bit3 为 0(反向标志位不成立)",
         "inj": RVP_INJ_ON_NOQUAD_FAST, "win": "jump_timeout",
         "falsify": "固件把 `&& (g_PowQuad & (0x01<<3))` 那一半漏掉(或写成 `||`)"
                    "⇒ 只凭功率过门槛就落库"},
    ),
}

# ============================ 〔RFL〕负荷开关 ============================
RFL_EV_CODE = 0x2B                  # 事件编码: 负荷开关误动作(p698.py; 记录 OAD 302B0B0A)
RFL_RCSD3 = LP_RCSD3                # 列选与 5-3 同源(序号 + 发生时刻 + 结束时刻), 见 `_overload_checks` 那份理由
RFL_CMP_075UN = 2                   # TaskMetering.h:140-149 CMP_075Un = 2
RFL_CMP_120UN = 3                   # 同上 CMP_120Un = 3(写库判定要它 == 0x00)
RFL_CMP_NUM = 4                     # Get_CompFlag(idx, num) 的那个 num —— :282/:498 用的是 4
RFL_CMP_MASK = 0xFF >> (8 - RFL_CMP_NUM)      # = 0x0F; 低 4 位全 1 才返 0xFF、全 0 才返 0x00
RFL_CMD_ON = 8                      # TaskRelay.h ST_RelayOn = 8(≥ ST_RelayOn 里唯一不动继电器的值)
RFL_FLG_MASK = 0x0F                 # :501-514 比的是 `g_RelayFlg & 0x0F` 这 4 位
RFL_FLG_ON = RFL_FLG_MASK           # 实测=合闸(:502/:511 那两条要它全 1)
RFL_FLG_OFF = 0x00                  # 实测=分闸(:503/:510 那两条要它全 0)
# ⚠ **四个断点与四组 watch 变量不在这里** —— 它们由脚本以字面量元组持有并递进来(见
#   `relayfail_roundtrip` 的 `bp_*`/`*_vars` 参数)。库里再留一份的下场: `scripts/_check_anchors.py`
#   只扫脚本, 判定照样全绿, 而"实际停在哪一行"没人核过(5-3 的 `LP_BP_*` 就是这么留下的盲区)。

# ============================================================================
# ③ 固件事实 —— 从本表固件反推出来的常量(值 + 源码出处)
#
# 这些原先长在 meterlib/cmd_bank.py 里 —— 那是"驱动"，换一块表就得跟着改。
# 它们是**这块表**的事实(事件编码/子类号/结构体步长/版本字符串)，故收进画像。
# cmd_bank 侧留一行 `X = P.X` 重导出，本模块只有数据、没有逻辑。
# ============================================================================

# ============================ 〔CLR〕事件清零 ============================
CLR_EV_DIS_ALL = 0xFFFFFFFF             # 帧内标识: 全清(简单路径唯一受理值)
CLR_EV_DIS_SAVED = 0x43000500           # 全清经 :3392 归一后**落库**的标识(698 事件起始化 OAD)
CLR_EV_DIS_PART = 0x033000FF            # 按标识部分清的样本(编程记录那一块) —— 本台应被 :3323 拒
CLR_EV_ER_PSWD = 0x02                   # TaskComm.h ST_COM: ER_OTHER=0 / ER_D0D1=1 / ER_PSWD=2
CLR_EV_ER_D0D1 = 0x01
CLR_EV_ST_PSWD = 1 << CLR_EV_ER_PSWD    # 645 异常应答状态字节 = 1<<comSta(DLT645Link.c:519) ⇒ 0x04
CLR_EV_ST_D0D1 = 1 << CLR_EV_ER_D0D1    # ⇒ 0x02
CLR_EV_CMD_OK = 0x9B                    # 0x1B|0x80 = 受理
CLR_EV_CMD_ERR = 0xDB                   # 0x1B|0xC0 = 异常应答(错误状态字节随其后)
CLR_EV_ID_PRG = 0x12                    # 『编程』记录(本台可确定性种一条: 645 0x14 写参量必落)
CLR_EV_ID_CLR = 0x15                    # 『事件清零』记录(永久; 全清不抹它)
CLR_EV_IDX_DIS = (18, 19, 20, 21)       # 帧内标识字节位(小端)
CLR_EV_BPT_STORE = ("DLT645App.c", 3399)   # 写库位置: 读 Recd_ClrEvent 实参(= 帧内标识归一后的值)
CLR_EV_BPT_GATE = ("DLT645App.c", 3317)    # 非编程态拒出口(ER_PSWD; ⚠ 与 LEN/DI0 两处共用 0x36482)
CLR_EV_VARS_DIS = tuple("pFrame[%d]" % i for i in CLR_EV_IDX_DIS)
# ⚠ **不要读 `dis`**(局部量): `info scope CMD_ClearEvent` 实测它在 :3399 那一段(0x365e2-0x365f2)
#   已出活跃区间(最后一段到 0x3657c), 读回来是栈垃圾 —— 与 4-6 的 `:625`/3-2 的 `:307` 同类假通过。
#   帧内标识从 `pFrame[18..21]` 现读现拼, 那条路实测 `pFrame` 在 $r4 且活跃区间覆盖 0x365ba-0x365f2。

# ============================ 〔CE〕时钟故障 ============================
CE_EVENT = 0x2E                          # 事件编码: 时钟故障(p698 EVENT_CODE_NAME/EVENT_REC_OAD 302E0B0A)
CE_RCSD_END = bytes.fromhex("02 00 20 22 02 00 00 20 20 02 00")   # 列: 记录序号 + **结束时间**(20200200)
CE_REC_CAP = 10                          # 时钟故障口留几条(`RecdData.h:170` `NUM_TimeError` 10u;
#   ⚠ 同名的 `:165` 那个 0u 在 `#ifndef VER_20Edit` 支里, 而 `Config/MengXi/UserCfg.h:15` 定义了
#   `VER_20Edit` ⇒ 编译的是 10u 那支。容量不封顶的那个"总次数"另有其地(索引区), 不是这个数。
CE_BPT_INJ = ("TaskTime.c", 275)         # 注入停靠点 @0x27f88 <Run_TaskTime+54>
# 判据断点 = **三个校正分支的汇合点**: `Recd_TimeError(str, sour)` 体首那句 `Read_RecdData`(`:1508`)
#   @0x1ef56 <Recd_TimeError+8> —— `str` 在 $r5 / `sour` 在 $r4, 可读区间正是 0x1ef56-0x1ef6a。
# ⚠ **别再断点 `TaskTime.c:1103`**(分支①"用备份时间校正")—— **那条在本台走不到**, 2026-09-16 实跑定死:
#   `:1099` 的分支闸要求 `Check_CRC(PTime=g_MeterTime_Backup, 6)` 为真, 而备份里那一对 CRC 字节
#   对它自己那 6 字节**恒不成立**: `:272` 原样抄的是 `g_MeterTime` 当时的两字节, 而别处用
#   `Set_MeterTime` 只写 6 字节、**不刷 CRC**(`TaskTime.c:351-360`) ⇒ 抄进备份的是"旧数据的 CRC"。
#   ⇒ `:1099` 判假 ⇒ 走 `else`(`:1108`)⇒ 实际落 `:1115`/`:1125`, 永远不是 `:1103`。
#   断点在汇合点上: 三条分支(以及清除口的 `:734`)全都从这一处看得出来, 且 `str` 一读就分清
#   是"判故障"(TRUE)还是"清故障"(FALSE) —— 比钉某一条分支稳, 也少占一个断点槽。
CE_BPT_HIT = ("TaskRecord.c", 1508)      # 判据断点   @0x1ef56 <Recd_TimeError+8>
CE_BPT_HIT_VARS = ("str", "sour")        # str=TRUE 判故障 / FALSE 清故障; sour = 事件源(本项恒 0)
CE_BPT_CLR = ("DLT645App.c", 734)        # 清除断点   @0x33c6a <CMD_CaliTime+966>
CE_INJ_VARS = ("g_MeterTime", "g_MeterTime_Backup")
CE_INJ_ASSIGN = [("g_MeterTime[5]", "g_MeterTime[5] - 1")]   # 年字节 −1(25→24)⇒ Comp_Data<0 ⇒ 倒退
CE_CALI_DELTA = 120                      # 广播校时的目标偏差(秒); 须落在 TAB_CaliTPara=[60,300] 内
CE_TOL = 15                              # 时标比对容差(秒): 记录读取/串口往返的抖动, 与 2-1 同量级
# 清除段的白盒观测: 停在 `Recd_TimeError(FALSE,0)` 那一句。读 `setTime`(= 解完 BCD 的 HEX 目标时间)
# 而不是 `pTime`(const 指针, 读出来只是个地址, 对不上账); `is698`/`mode` 是那一次调用的形态
# (广播 = is698 FALSE / mode 0), 停下来的如果不是这个形态, 说明停错了地方。
CE_VARS_CLR = ("setTime", "is698", "mode")
CE_BP_WAIT = 20.0                        # 等清除断点命中; 被判定拒时等满即返(那一次本就不该停)

# ============================ 〔AR〕主动上报(13-1) ============================
# 上送口 = 载波(PT_PLC_M), 台上没有集中器 ⇒ 只观测"表侧把事件排进队列并组出帧", 收帧那半支不可证。
AR_PLC_M = 3                             # PT_PLC_M(UserCfg.h:381); 上报对象表 g_ReportIdx / g_FollowSta 按口分组
AR_RPT_NUM = 3                           # C_AutoRptNum(TaskReport.c:78): 一次事件置几个待上报数
# 事件入队点 = 时钟故障。`TaskRecord.c:1553` 的 `Rpt_ReportSta(ID_TimeError, 1)` 把
#   `g_ReportIdx[3][0] = {bit.eve = TAB_ReportObj 里 ID_TimeError 那一行的下标, bit.rpt = 0}`。
# ⚠ 那一行的下标(实测 70)**别写进判据**: 它是 TAB_ReportObj 的排布, 换个版本就漂。
#   判据读的是**自描述**的一步 —— `TAB_ReportObj[g_ReportIdx[3][0].bit.eve].OAD == 0x302E0200`。
AR_OAD_TIMERROR = 0x302E0200             # ID_TimeError 的对象 OAD(TaskReport.c:200)
AR_INJ_TIME = ("TaskTime.c", 275)        # 时钟故障注入停靠点 @0x27f88 <Run_TaskTime+54>
AR_BPT_RPT = ("TaskReport.c", 1632)      # 断[A] 排完队、下帧前 @0x1ac44 <Chk_ReportSta+676>
AR_BPT_SEND = ("TaskReport.c", 2050)     # 断[B] Send_Report 入口   @0x1b554 <Auto_Report+1732>
# 停靠点与判据断点不是一处: ①的 rptnum/event 只在 :1632 那一行活跃, ②③⑤的 buff 只在 :2050 活跃。
AR_AT_VARS = ("rptnum", "event", "g_ReportIdx[3][0].bit.eve",
              "TAB_ReportObj[g_ReportIdx[3][0].bit.eve].OAD")
# ⚠ `TAB_RevByte` 与 `g_FollowSta[3]` 一起读: 判据③比的是"组帧块用的那张变换表"对不对,
#   把表本身当输入读回来, 固件改了表也照样判得对 —— 硬编码那张表就是把固件常量抄成第二份。
AR_VARS_SEND = ("buff", "len", "TAB_RevByte", "g_FollowSta[3]", "g_AutoRptNum", "g_AutoRptGap")
# 断[A] 停的是 `:1632`(下帧之前), 断[B] 停的是 `:2050`(Send_Report 之前) —— 两处的量不通用:
#   `rptnum`/`event` 只在断[A] 活跃, `buff` 只在断[B] 活跃。
AR_TIME_VARS = ("g_MeterTime", "g_MeterTime_Backup")   # 注入前那两个入参(对照用)
AR_SETTLE = 3.0                          # 造故障之后、下断[A] 之前等的秒数(让事件排进队列再动断点)
AR_POST_VARS = ("g_AutoRptNum", "g_AutoRptGap")   # 三帧跑完之后的 AA80 读回(判据④的后半段)
# ---- 刻意不注入 `g_FollowMod` --------------------------------------------------------------
# `Chk_ReportSta` 每趟先做四道 CRC 复核(`:1499` g_ReportEn / `:1508` g_ReportMod /
#   `:1517` g_FollowMod / `:1527` g_AutoRptNum), 复核不过就 `Read_ParaData` 从 EEPROM 重载
#   ⇒ **注入进去的值会被抹掉**。这四样一律不注入。
# `g_FollowMod[0]` 实测 0, 于是"跟随上报"(`:1599`-`:1607`)那一路永不成立 —— 判据①的
#   `rptnum[1] == 3` 只可能来自事件那一路, 无须再写台面参数把两者分开, 也就无须还原那一步。
# `g_AutoRptGap` **不在那四样之列**(全文件没有它的 `Check_CRC`), 所以注入它留得住。
AR_KEEP_ZERO = ("g_FollowMod", "g_ReportEn", "g_ReportMod", "g_AutoRptNum")   # 有 CRC 复核, 不许注入
# ---- 为什么要动 `g_AutoRptGap` --------------------------------------------------------------
# `:800` 初始化 `g_AutoRptGap = 3`, 空转三拍归 0 后**一直停在 0**; 而 `:865-866` 是
#   `Chk_ReportSta(); Auto_Report(C_AutoRptGap);` —— **同一拍里背靠背**, 队列一有事件,
#   帧在同一拍就被组出去。若等"排完队再下断点", 帧已经跑了。
# 所以拿 `g_AutoRptGap` 当闸: 造故障的那一次顺带把它按到 10(`:1793` 的上限也是 10),
#   之后每拍 `:1796` 减一, 给出约 9 拍的余量; 判据那一次再按回 0 并同拍放行。
# `g_FollowSta[3]` 每趟被 `:1564-1565` 清零(`&= TAB_RptMask; &= g_FollowMod[0]`, 后者为 0)
#   ⇒ **每一帧都要重新注入一次**, 只在第一次注入的话第二、三帧的判据③读回来是 0。
AR_FOLLOW_BIT = 0x00004000               # 注入用的跟随状态位(取 TAB_RptMask=0xC200 之外的一位, 避免与事件位混)
AR_ASSIGN_TIME = [("g_MeterTime[5]", "g_MeterTime[5] - 1"), ("g_AutoRptGap", "10")]
AR_ASSIGN_RPT = [("g_FollowSta[3]", "0x00004000"), ("g_AutoRptGap", "0")]
# ---- 组帧判据的字节 ------------------------------------------------------------------------
AR_APDU_EVENT = "51 30 2E 02 00"         # OAD 302E0200 的 698 写法: 属性 0x51 + 4B OAD 大端(TaskReport.c:1888)
AR_APDU_ARRAY = "01 33 20 02 00 01 01"   # 「新增上报事件列表」那一格的头(TaskReport.c:1856-1863); 紧随 1B = 元素计数
AR_APDU_FOLLOW = "20 15 02 00 01 04 20"  # 跟随上报对象描述(TaskReport.c:1919), 其后 4B = 位图
AR_FRAME_TAIL = 0x16                      # 698 结束符, 应落在 buff[len+1]
AR_FRAME_CMD = 0x83                       # buff[3] 链路层控制字 Send_Report 发出的那一类
AR_FRAME_AF = 0x05                        # buff[4] 地址域标志
AR_FRAME_CA = 0x00                        # buff[11] 客户机地址(组帧时写死 0)
AR_MAX_SEND = 3                           # 一次事件最多组 3 帧(= g_AutoRptNum[1] 的初值)
# 最后一帧之后 `:2064` 的 `g_AutoRptGap += 5` 会把 `:2060` 刚写回的 10 抬到 15 —— 判据④比的是这个。
AR_GAP_LAST = 15
AR_GAP_IDLE = 10                         # 中途那几帧之后的值(判据④只做参考, 不认领条目)
AR_SEND_WAIT = 25.0                      # 等一帧组完的秒数上限(帧间隔约 10s, 三帧合计约 25s)

# ============================ 〔CLOCK〕拨钟 + 〔INJECT〕注入 ============================
CLOCK_BIG = 90        # 制造「偏差 > 1s」的拨偏量(须跨分钟, 见上)
CLOCK_SMALL = 1       # 制造「偏差 ≤ 1s」的拨偏量 —— 就是规格阈值本身(>1 才跟随, 1 恰在边界内)
CLOCK_SETTLE = 8.0    # 拨后等 SPI 时间对象送达并跟随的秒数(探针实测 ≤3s 跟上, 留余量)
# ③ 的注入**每次执行前**把两芯拨齐(写计量芯 = 管理芯)后等的秒数。只要 SPI 时间对象(约 1 Hz)
# 送达一次, 让镜像 ≈ 管理芯即可 —— **不需要**等管理芯跟随(对齐压根不该动它), 故远短于 settle。
CLOCK_ALIGN_SETTLE = 2.0
# ---- 判据③ 的**注入通道**(2026-09-11 定; 触发原语见 `swdbg.inject_miss`) ----------------------
# 时刻串的布局**源码实证**(两处独立证据, 都不是推理):
#   · `Platform/DateTime.c:226` 的 `Diff_Secs` = `Point_Days(&t[3])*86400 + t[2]*3600 + t[1]*60 + t[0]`
#     ⇒ 下标 0/1/2 = 秒/分/时, 3 起是日期; `TaskTime.c:232-246` 的秒针自增(`[0]` 到 60 归零、
#     `[1]` 到 60 归零、`[2]` 到 24 归零、`[3]<=DayOfMonth([4],[5])`)把 3/4/5 = 日/月/年 也钉死了;
#   · 实测 objtime 六字节 `0x29 0x00 0x0B 0x0B 0x09 0x1A` == 2026-09-11 11:00:41, 逐字节对得上。
#   全程**二进制(HEX)**, 没有 BCD 的坑。
# ⇒ 造"差**恰好** 1 秒"只要动**第 0 字节**, 其余五字节照抄 `g_MeterTime`(管理芯本地钟, 与 objtime 同布局):
#     objtime[0] = g_MeterTime[0] + 1
#   `Diff_Secs` 是**裸算术**(不做进位归一), 所以秒 = 60 也无妨 ⇒ 无需 `== 59` 的特判;
#   `TaskTime.c:1085` 那句编译成 `cmp r0,#2 / bcc`(源码 `> 1`), 差 1 走 `bcc` 不跟随 —— 这正是要被证的。
INJECT_AT = ("TaskTime.c", 1085)          # 判据那句 `if (Diff_Secs(objtime, g_MeterTime) > 1)`
INJECT_WATCH = ("TaskTime.c", 1087)       # 跟随支 `Set_MeterTime(objtime)`(②b 仍在用: 帧那一次)
# ---- ③ 的**观测尺子**: 判 定 指 令 就 地 读 (2026-09-11 第三次实跑定案, 换掉了窗口法) -----------
# 旧尺子是"注完放行 → 1.5s 窗口里等 `:1087` 命中与否 + 拿放行后的秒数判『这一停归谁』"。**它是坏的**:
#   · 那个秒数里混着 `go()` 的 0.4s 静默(读数恒 ≥0.4s, 阈值 0.5s 一个都没拦住);
#   · 更根本的是**停核本身**: 停核要 1~3s, 而管理芯钟是**软件走时** ⇒ 放行那一刻管理芯已落后计量芯
#     1~3s(> 阈值)⇒ **下一次自然比较紧跟其后就跟随**, 窗口里那一停根本不是我们造的那次执行。
# 新尺子**与时间无关**: 断点下在**判定指令**(`cmp r0,#2` 后面那条 `bcc`)上 —— 注完放行后**当前这次
# 调用必然立刻走到它**(分派在 `:1085` 之前已判过), 于是**放行后的第一停就是自己**(实测 0.031s),
# 停在它上面单步一条, 落点就是答案: 落到 `fallthru`(=`Set_MeterTime` 的第一个字节)⇒ 跟随支被执行。
# `decide` 由 `.out` 反汇编推出(`swdbg.breakpoint.Session.decision_anchor`), **不收手抄地址**;
# 本层只给两个**名字**, 解析那一句在脚本里(会话在脚本手里, 库不 import swdbg): `CB.resolve_decide(g)`。
INJECT_DECIDE = ("Save_DateTime_Data", "Diff_Secs")   # (函数, 被调) → 之后第一条条件分支 = 判定指令

# ============================ 〔CLOCK〕拨钟 + 〔INJECT〕注入 ============================
INJECT_TIMEOUT = 3.0

# ③ 的四个边界点。判据 `Diff_Secs(...) > 1` 里秒是**整数**, 所以边界只有 {0, 1, 2} 三点
# ("差 >1 但 <2" 是空集, 不是"没测")。② 只证了 90s 必跟随 —— 单靠它阈值被夹在 1..90 的空档里;
# 四次合起来才把阈值**夹死在 (1, 2]**: 2 跟随 + 1 不跟随 ⇒ 阈值恰在两者之间。
#
# 分成两组**不是排版**: `shift=True` 的那一次会**真的把管理芯钟前推**(跟随了嘛), 而 ③a 判的是
# "本地钟**没**被拉走" —— 它必须待在 ③a 的测量窗口**之外**, 否则自己打自己。
INJECT_SHOTS_STEADY = (              # 期望**不**跟随(expect="miss" ⇒ follow=False): 判定落点是 `target`
    {"delta": 1,  "crit": "③b", "expect": "miss"},
    {"delta": 0,  "crit": "③d", "expect": "miss"},
    {"delta": -1, "crit": "③e", "expect": "miss"},
)
INJECT_SHOTS_SHIFT = (               # 期望**跟随**(expect="hit" ⇒ follow=True): 落点是 `fallthru`
    # ⚠ 它**不会自愈**, 别写"扰动自愈": 前推 2s 之后两芯自然差变成 1s(恰在阈值内), 谁都不再跟随,
    #   于是管理芯就停在"比计量芯快 1s"这个新稳态上, 一直到收尾拨回真实时间。
    {"delta": 2,  "crit": "③c", "expect": "hit"},
)
INJECT_SHOTS = INJECT_SHOTS_STEADY + INJECT_SHOTS_SHIFT

# ④ 两次的 `want` 秒位 —— **为的是把判据接受的两支都走到**, 不是随便挑的:
#   · 54 ⇒ 写钟后隔 `settle`(8s, 等管理芯跟随)才触发冻结, 落库时表钟已跨进下一分钟(drift = +1 分);
#   · 3  ⇒ 同一次落库仍在**当分钟**(drift = 0)。
# 判据 `记录那一分钟 − want 那一分钟 ∈ {0, +1 分}` 接受两支, 而首轮只走到过 "+1 分" 那一支 ——
# 那等于"放宽判据"只被**断言**、没被**验证**。两次各走一支, 放宽才有据。
CLOCK_STAMP_SECS = ((54, "④a"), (3, "④b"))
# ⚠ 早先这里有个 `CLOCK_OFF_TOL = 1`(③a 判「未被拉平」的秒容差)。2026-09-11 探针实测后**删掉**:
#   ③a 已改成量「管理芯本地钟有没有阶跃」—— 尺子是 `clock_hold_evidence` 的**不停核窗口**
#     (旧版在注入动作前后采样, 那要停核, 而停核期间管理芯钟不走 ⇒ 观察手段把被测的量动掉了);
#   留着它会变成一条**读着像现行规矩的死常量**, 下次有人照着它调参就白调。

# ============================ 〔HOLD〕计分窗口 ============================
HOLD_SAMPLES = 12          # 计分窗口采几个点
HOLD_GAP = 2.0             # 点间隔; 每点还要两次 read_clock 往返(实测 ~1.5s) ⇒ 一个窗口 ≈ 42s
HOLD_SETTLE = 8.0          # 摆完状态后等系统自稳的秒数(SPI 时间对象 ~1 Hz, 一次跟随就到位)
HOLD_THRESH = 0.5          # 阶跃阈值(前/后段 max 之差); 1s 的跟随阶跃远在阈值外
HOLD_CONFIRM = 3           # 计分前"先证明它稳了"的确认点数

# ============================================================================
# ③ 固件事实 —— 从本表固件反推出来的常量(值 + 源码出处)
#
# 这些原先长在 meterlib/cmd_bank.py 里 —— 那是"驱动"，换一块表就得跟着改。
# 它们是**这块表**的事实(事件编码/子类号/结构体步长/版本字符串)，故收进画像。
# cmd_bank 侧留一行 `X = P.X` 重导出，本模块只有数据、没有逻辑。
# ============================================================================

# ============================ 〔RELAY〕继电器 ============================
RELAY_CMD_R = {0x1A: "拉闸", 0x1B: "允许合闸", 0x1C: "直接合闸", 0x1D: "预跳闸1", 0x1E: "预跳闸2",
               0x3A: "保电", 0x3B: "保电解除", 0x2A: "报警", 0x2B: "报警解除"}   # TAB_RelayCmdR(DLT645App.c:3416)

# ============================ 〔零散〕 ============================
C_RATENUM_MAX = 12   # UserCfg.h:290(VER_20Edit 分支) —— 真上限, 不是 3-2 注里引的 4

# 错误标志位图 → 人话(DLT645Link.c:519 `0x01 << comSta`; 序号即 ST_COM 枚举, TaskComm.h:9-45)

# ============================ 〔零散〕 ============================
CMP_075UN_IDX = 2                      # TaskMetering.h:144 CMP_075Un 在 g_CompFlg[] 中的下标

# ============================ 〔零散〕 ============================
PROG_EVENT = "编程"                     # 编码 0x12, 记录 OAD 30120B0A(通用写参口 Recd_Program)
PROG_EVENT_SUB = "结算日编程"           # 编码 0x1A, 记录 OAD 301A0B0A(结算日专用口 Recd_PrgCntDay)
# ⚠ 断点 `BP_REC`/`VARS_REC` **住在脚本里**(`project/tests/_test_5_6_program.py` 的模块级字面量元组),
#   由脚本当参数递进来 —— 不在这里再写一份。理由是**检查**: `scripts/_check_anchors.py` 只抠脚本里的
#   `BP_<X>`/`VARS_<X>`, 断点写进库 = 自动那条路扫不到它(5-3 的 `LP_BP_*` 就是这么留下盲区的, 见其段末注)。

# ============================ 〔RELAY〕继电器 ============================
RELAY_EVENT = {"拉": "拉闸", "合": "合闸"}          # 事件编码 0x1F→301F0B0A / 0x20→30200B0A
RELAY_ACT_TXT = {"拉": "FALSE(ST_SwOff)", "合": "TRUE(ST_SwOn)"}
RELAY_CMD_DIR = {"拉": "低于 ST_RelayOn(8)", "合": "不低于 ST_RelayOn(8)"}
ST_RELAY_ON = 8                                     # TaskRelay.h 枚举序号; g_RelayCmd[0] 用它分向
RELAY_REC_CAP = 10                                  # 跳闸/合闸/开关误动作三个记录口各留几条
# (RecdData.h:142-145 `NUM_RelayFail` / `NUM_RelayOff` / `NUM_RelayOn` 三个宏都是 10u)
# ⇒ 规范「最近 10 次」那半的容量。5-9/5-10/5-11 三条子项共用这一份, 不在脚本里再各写一个字面量。
# ⚠ 断点 `BP_REC`/`VARS_REC` **住在脚本里**(`project/tests/_test_5_9_relay_off.py` 与
#   `_test_5_10_relay_on.py` 的模块级字面量元组), 由脚本当参数递进来 —— 不在这里再写一份。
#   理由同 5-6 段末那条: `scripts/_check_anchors.py` 只抠脚本里的 `BP_<X>`/`VARS_<X>`。

# ============================ 〔KP〕继电器命令 ============================
KP_CMD_OFF = 0x1A        # CMD_RelayOff   拉闸          TAB_RelayCmdR[0]
KP_CMD_INDIR = 0x1B      # CMD_IndirOn    允许合闸      TAB_RelayCmdR[1]
KP_CMD_ON = 0x1C         # CMD_DirectOn   直接合闸      TAB_RelayCmdR[2]
KP_CMD_OFF1 = 0x1D       # CMD_AdvOff1    预跳闸1       TAB_RelayCmdR[3]
KP_CMD_OFF2 = 0x1E       # CMD_AdvOff2    预跳闸2       TAB_RelayCmdR[4]
KP_CMD_KEEP = 0x3A       # CMD_InKeep     保电          TAB_RelayCmdR[5]
KP_CMD_KEEPOFF = 0x3B    # CMD_OutKeep    保电解除      TAB_RelayCmdR[6]
KP_REMOTE_NUM = 7        # CMD_RemoteNum(= CMD_OutKeep+1); 行 7/8 = 报警/报警解除, 不在裁决表里
KP_CMD_ROW = {KP_CMD_OFF: 0, KP_CMD_INDIR: 1, KP_CMD_ON: 2, KP_CMD_OFF1: 3,
              KP_CMD_OFF2: 4, KP_CMD_KEEP: 5, KP_CMD_KEEPOFF: 6}   # 操作字 → 裁决表的行号

# ⚠ 上面 `KP_CMD_*` 是**645 线上命令字**(0x1A/0x3A/0x3B…), 而 `Set_RelayCmdR` 形参 `cmd`
#   取的是 `TaskRelay.h:9-23` 的**枚举序数**(CMD_RelayOff=0 … CMD_OutKeep=6)。两者差得很远
#   (拉闸: 序数 0, 命令字 0x1A)。本表七条遥控命令上**命令字与枚举序数恰好同序**, 所以
#   `KP_CMD_ROW` 的值可以直接当那个序数用 —— 断点读回来的 `cmd` 一律拿 `KP_CMD_ROW[操作字]` 比,
#   拿 `KP_CMD_*` 比会当场错判。

# ST_Relay* 枚举序号(TaskRelay.h:30-53); `KP_ST_TXT` 只给打印用, 判据比的是数字
KP_ST_ERROR0, KP_ST_ERROR1 = 0, 16
KP_ST_RLYOFFR, KP_ST_ALLOWON, KP_ST_RELAYON, KP_ST_WAITOFFR = 1, 6, 8, 10
KP_ST_ALLOWONKP, KP_ST_RELAYONKP = 7, 9
KP_ST_RLYOFFL = 4             # ST_RlyOffL(本地拉闸) —— 12-3「本地续拉」的落点(TaskRelay.c:1016/:1020)
KP_KP_STATES = (KP_ST_ALLOWONKP, KP_ST_RELAYONKP)      # **保电位的两个现态**(保电拦截只在它们上发生)
KP_ST_TXT = {0: "ST_Error0", 1: "ST_RlyOffR", 2: "ST_AdvOff1R", 3: "ST_AdvOff2R", 4: "ST_RlyOffL",
             5: "ST_AdvOffL", 6: "ST_AllowOn", 7: "ST_AllowOnKp", 8: "ST_RelayOn", 9: "ST_RelayOnKp",
             10: "ST_WaitOffR", 11: "ST_RlyOffGdR", 12: "ST_AdvOff1GdR", 13: "ST_AdvOff2GdR",
             14: "ST_RlyOffGdL", 15: "ST_AdvOffGdL", 16: "ST_Error1"}

# TAB_RelaySta[10][15](TaskRelay.c:51-66)前七行 = 七条遥控命令的转态表; 列 = 现态 - 1
KP_STA_TAB = (
    (1, 1, 1, 1, 1, 1, 0, 10, 0, 10, 10, 10, 10, 10, 10),      # 拉闸
    (6, 6, 6, 6, 6, 6, 7, 8, 9, 8, 8, 8, 8, 14, 8),            # 允许合闸
    (8, 8, 8, 8, 8, 8, 9, 8, 9, 8, 8, 8, 8, 14, 8),            # 直接合闸
    (0, 2, 2, 0, 0, 0, 0, 12, 0, 0, 0, 12, 12, 0, 0),          # 预跳闸1
    (0, 3, 3, 0, 0, 0, 0, 13, 0, 0, 0, 13, 13, 0, 0),          # 预跳闸2
    (7, 7, 7, 7, 7, 7, 7, 9, 9, 7, 7, 7, 7, 7, 7),             # 保电
    (1, 2, 3, 4, 5, 6, 6, 8, 8, 10, 11, 12, 13, 14, 15),       # 保电解除
)
KP_ERR_BYTE_PSWD = 0x04        # 线上错误位 = 1<<ER_PSWD(=2), DLT645Link.c:519(数据域已去 0x33)
KP_CTRLSTAT_RLYKEEP = 0x0020   # g_CtrlStat[1] bit5 = 1<<(ER_RlyOffKeep-16), TaskComm.c:1256
KP_STA3_DI = "04000503"        # 645 运行状态字3(CMD_ReadData04 的 case 0x000503)
KP_STA3_KP_BIT = 0x10          # 状态字3 的**第二字节** bit4 置位 = 保电位(TaskComm.c:1006)

# ============================ 〔AO〕解除保电后的本地续拉(12-3) ============================
# 判据源: TaskRelay.c:1009-1022「本地自动续拉判定区」——
#   `(TAB_MeterSty.style == TP_Local) && (cmd == CMD_OutKeep) && (Get_CashStatus() == ST_OvrCash2)`
#   三条件齐 ⇒ :1020 `newSta = ST_RlyOffL;`, 覆盖掉裁决表算出来的 ST_RelayOn(8)。
AO_CASH_OVR = 4                # ST_OvrCash2 = 低于透支金额门限(TaskLclFee.h); **帧通道造不出**:
                               # 充值/退费/开户都过 Read_Esam MAC 校验, 清零只落 ST_OvrCash1 ⇒ 只能注入
AO_INJ_ASSIGN = [("g_CashStatus[0]", 4)]   # 注入表: 把费控状态字改成"低于透支门限"
# ⚠ 断点 `BP_*`/`VARS_*` **住在脚本里**(同 5-9/5-10/12-2 的先例), 不在这里再写一份 ——
#   `scripts/_check_anchors.py` 只抠脚本里的字面量 `BP_<X>`/`VARS_<X>`。
AO_AT_VARS = ("g_RelayCmd",)   # 注入停点(:1009)那刻可读的现场量 —— 现态, 三条件之一的前置
AO_WATCH_VARS = ("newSta",)    # 裁决汇合点(:1090)那刻的新态 —— ①的硬证

# ============================ 〔BFY〕阶梯结算 ============================
BFY_SUBCLASS = 0x11                      # 阶梯结算冻结子类号(源 DLT698App.c:3042 = ID_BillFrezY)
BFY_STYLE_LOCAL = 1                      # TP_Local(UserCfg.h:345); 风格判定放行的那个值
# 注入表(**一次停点写两样**, 见上面那段): 年字节 + 一份有效的年结算日期表。
# 年字节的值写成**表达式**(gdb 现算) —— 不抄死年份, 跨年后本行不用改; 表字节是常量。
BFY_TABLE_TRIPLE = (0, 5, 9)             # 年结算日期表第 0 组 = (时, 日, 月); 日 ≤ TAB_DayOfMonth[月]
BFY_PARAM_BYTES = tuple(BFY_TABLE_TRIPLE) + (99,) * 9   # 12B = 4 组三元组; 后 3 组留 99=无效
BFY_INJ_ASSIGN = [("g_HisTime[5]", "g_CurTime[5] - 1")] + [
    ("buff[%d]" % _i, "%d" % _v) for _i, _v in enumerate(BFY_PARAM_BYTES)]
# 4-7 两支各自注进 `buff[0..11]` 的结算日期表(后 3 组留 99=无效):
#   年支那趟的年份差由**拨钟自然跨年**造 ⇒ 只写这张表, 第 0 组 (时 0, 日 1, 月 1) 带月 = 年结算形态;
#   月支那趟的月份差由注入 `g_HisTime` 造(见 `cmd_bank.bfy_month_assign`)⇒ 表第 0 组留
#   (时 0, 日 1, 月 99) 落月结算形态。
BFY_YEAR_BYTES = (0, 1, 1) + (99,) * 9
BFY_MONTH_BYTES = (0, 1, 99) + (99,) * 9
BFY_YEAR_ASSIGN = [("buff[%d]" % _i, "%d" % _v) for _i, _v in enumerate(BFY_YEAR_BYTES)]
BFY_MONTH_BUFF_ASSIGN = [("buff[%d]" % _i, "%d" % _v) for _i, _v in enumerate(BFY_MONTH_BYTES)]
BFY_GATE_VARS = ("TAB_MeterSty.style",)  # 风格判定那刻的现场值(判据①的硬证)
# 注入停点(:887)那刻可读的量 —— 由 `info scope Check_BillFrezY` 的位置表逐区间核过(2026-09-16):
#   `g_CurTime`/`g_HisTime`(全局) 与 `buff`(0x32e9c-0x32fc0 内, $sp+8) 都有位置;
#   ⚠ `dateNum` 那一处**还没有**位置(区间从 0x32eb4 起、在 `$r7` 里) ⇒ 读它会得到一句"读不到"。
#   ⚠ **`flag` 已去掉**(2026-09-17): 它在 $sp-124 确有位置, 但 :887 那一停**还没被赋过值**
#     —— 声明 `BOOL flag = OTHER;` 在 :871, 真值要到 :895/:903 才写 ⇒ 读回来恒是占位值,
#     当"注入前对照量"不带信息。**位置可读 ≠ 值算数**: 前者 `info scope` 答得了, 后者要读源码,
#     即 `swdbg.breakpoint` 与由此长出的断点体检(`scripts/_check_anchors.py`)。
BFY_AT_VARS = ("g_CurTime", "g_HisTime", "buff")

# ============================ 〔BFY〕阶梯结算 ============================
BFY_WRITE_VARS = ("frezNum", "buff")
BFY_NEG_WIN = 70.0                       # ② 月支否定期望窗口: 一个自然分钟步进必然落在里面
BFY_SAME_WIN = 4.0                       # ② 年支否定期望窗口: 紧接 :875 放行的那一趟
BFY_WAIT = 100.0                         # 等自然分钟步进到达 :875(分钟步进 ≤60s 一次)
BFY_INJ_WAIT = 100.0                     # 注入放行后等 :1075 命中(同样只等下一趟分钟步进)

# ============================ 〔BFY〕阶梯结算 ============================
BFY_FREZ_ADD = 4                         # 本台 `TAB_FrezAdd[7]`(非 0 ⇒ :878 的判定放行, 且 :993 的 frezAdd)
# 条目 ↔ 证据的极性:falsify = "什么样的固件会让这条判 FAIL"(答不出就不算证据, judge 的规矩)。
# 两份分开写 —— 4-7 证的是"生成与结转", 11-1 证的是"那一列怎么处理", 同一句 falsify 换个子项就不成立。

# ============================ 〔ST〕结算 ============================
ST_OAD_SETTLE_KWH = "20320200"
# 读记录的三列列选(序号 + 冻结时标 + 那一列)。⚠ **别拿它去喂 `decode_freeze_row`** —— 那个解码器
# 靠 `rfind(b"\x06\x00\x00\x00")` 定位记录头, 三列布局下会取到**最后一列自己**(实据见探针), 于是
# 解出错误的序号。取尾部那一列走下面的 `freeze_settle_kwh_raw`(按时标切, 不靠记录头)。

# ============================ 〔YST〕年阶梯 ============================
YST_YEAR_BACK_CARRY = 1                  # 未超档那趟: 年份 −1 ⇒ frezNum=1 ≤ 4 ⇒ over=FALSE
YST_YEAR_BACK_OVER = 5                   # 超档那趟:   年份 −5 ⇒ frezNum=5 > 4 ⇒ over=TRUE
# 注进 `buff[6..9]`(记录尾部那一列)的"源"。用两个不常见的字节当**特征**(A5/5A) ——
# 判据是"记录尾部字节里还找不找得到它们", 于是**不必知道那一列的编码**:
# 结转 ⇒ 原样留在记录里(找得到); 清零 ⇒ `Set_Data(...,0,4)` 抹平(找不到)。
YST_DOSE_BYTES = (0xA5, 0x5A, 0x00, 0x00)
YST_DOSE_ASSIGN = [("buff[%d]" % (6 + _i), "0x%02X" % _b) for _i, _b in enumerate(YST_DOSE_BYTES)]

# ============================ 〔MST〕月阶梯 ============================
MST_PARAM_BYTES = (0, 5, 0) + (99,) * 9
# 月份回退写成 **gdb 现算的 C 条件表达式**(1 月要回绕到 12 月并退一年) ⇒ 跨年跑这条不用改。
MST_MONTH_BACK_EXPR = "g_CurTime[4] > 1 ? g_CurTime[4] - 1 : 12"
MST_YEAR_BACK_EXPR = "g_CurTime[4] > 1 ? g_CurTime[5] : g_CurTime[5] - 1"
MST_INJ_ASSIGN = ([("g_HisTime[4]", MST_MONTH_BACK_EXPR), ("g_HisTime[5]", MST_YEAR_BACK_EXPR)]
                  + [("buff[%d]" % _i, "%d" % _v) for _i, _v in enumerate(MST_PARAM_BYTES)])

# ============================ 〔PING〕瞬时冻结 ============================
PING_BLOCK_NAMES = ("s_stFrzStorageInfo", "g_FrezAdr", "g_FrezNum", "g_FrezLen")
PING_SNAP_CLAMP = 128     # `s_stFrzStorageInfo` 画像登记 240B > AA80 单次负载上限 128B ⇒ 截读前 128B
                          # (不 clamp 会读成「无应答」, 而那看起来像"这个变量读不了")
PING_SUBCLASS = 0x00      # 瞬时冻结记录子类(记录 OAD = 50 00 02 00)
PING_FRAME = "698.action.freeze.immed"   # 698 广播瞬时冻结 —— **写动作**: 表里新增 1 条记录
PING_SETTLE = 4.0         # 广播冻结要写 EEPROM, 那期间表会短暂不应答, 等它回落再读

# ============================ 〔FREZSTORE〕冻结存储信息区(各冻结类别共用) ============================
# 单一事实源: 固件 `FrezGetStorageInfo`(FrezData.c:754) 也读这一份 —— 所以下面这些量是固件
# 自己据以判边界/查深度的值, 不是另抄的常量。表项序号 = `ID_FREZ` 枚举值(FrezData.h)。
FREZ_STORE_BLOCK = "s_stFrzStorageInfo"
FREZ_STORE_CLAMP = 128    # AA80 单次负载 ≤128B ⇒ 首段截 128B; 表项落在其后的由 `frez_store_tail` 补读
FREZ_STORE_ENTRY_LEN = 12  # TS_FrzStorageInfo = u16Period/u16Size/u16Depth/u16Addr/u16Crc/u08Res[2]

# 记录区(FLASH 扇区)的分区常量 —— 固件 `FrezUpdateStorageInfo` 的折页式(FrezData.c:878)与它那道闸
# (`:884` 的 `u32Addr_end > FH_FrezEnd` 即 FALSE)用的就是这两个值; 判据⑩(装得下 35040 条)按它们算。
FREZ_PAGE_SIZE = 4096     # FH_PageSize(FLASH.h; CHIP_Flash==FH128Mbit 那一支 ⇒ 一扇区 4096B)
FREZ_END_ADDR = 4032      # FH_FrezEnd = FH_Capacity − 64 = 4096 − 64; 分冻结等记录区的末**扇区**

# ============================ 〔MINFREZ〕分钟冻结 ============================
MINFREZ_SUBCLASS = 0x02   # 分钟冻结记录子类(记录 OAD = 50 02 02 00)
MINFREZ_CHANNEL0 = 1      # ID_MinuteFrez0(FrezData.h 的 ID_FREZ 枚举); 通道 t 的表项序号 = 1+t
MINFREZ_CHANNELS = 8      # ID_MinuteFrez0..7
MINFREZ_WRITE_VARS = ("normal", "typ", "frezNum", "stInfo", "buff")
MINFREZ_WAIT = 190.0      # 等 :374 命中: 拨到边界前 1 分钟, 至多 ~3 分钟到点
MINFREZ_NEG_WIN = 95.0    # ⑤ 否定期望窗口: 比一个整分间隔长, 保证窗口里至少跨过一个整分
MINFREZ_LEAD = 40.0       # 拨钟那一刻距目标分钟至少留这么多秒(校时帧 + 计量芯 SPI 跟随要时间)

# ============================ 〔HOURFREZ〕小时冻结 ============================
HOURFREZ_SUBCLASS = 0x03  # 小时冻结记录子类(记录 OAD = 50 03 02 00)
HOURFREZ_INDEX = 9        # ID_HourFrez(FrezData.h 的 ID_FREZ 枚举) ⇒ s_stFrzStorageInfo[9]
HOURFREZ_DEPTH = 254      # NUM_HourFrez(UserCfg.h) —— 应可存储 254 个数据
HOURFREZ_UNIT_MIN = 60    # 一个小时 = 60 个绝对分钟; 整点边界 = 绝对分钟数整除 60*prd
HOURFREZ_GATE_VARS = ("normal", "frezNum", "hours", "stInfo")   # 断[A] 处可读(见 cmd_bank 段头)
HOURFREZ_WRITE_VARS = ("frezNum", "buff", "stInfo")             # 断[B] 处可读
HOURFREZ_WAIT = 190.0     # 等写库点命中: 拨到整点前 1 分钟, 至多 ~3 分钟到点
HOURFREZ_NEG_WIN = 95.0   # ⑤ 否定期望窗口: 比一个整分间隔长, 保证窗口里至少跨过一个整分
HOURFREZ_MONTHEND_TOD = "23:59:05"   # ⑦ 跨月跨日那条的拨钟时刻(月末这一天的这个点)

# ============================ 〔DAYFREZ〕日冻结 ============================
DAYFREZ_SUBCLASS = 0x04   # 日冻结记录子类(记录 OAD = 50 04 02 00)
DAYFREZ_INDEX = 10        # ID_DayFrez(FrezData.h 的 ID_FREZ 枚举) ⇒ s_stFrzStorageInfo[10]
DAYFREZ_DEPTH = 365       # NUM_DayFrez(UserCfg.h) —— 应可存储 365 天的数据量
DAYFREZ_ADD = 7           # TAB_FrezAdd[EM_Day](UserCfg.c:196, EM_Day=3) —— 校时那趟最多补写这么条
DAYFREZ_GATE_VARS = ("normal", "frezNum", "over", "days", "stInfo")  # 断[A] 处可读(见 cmd_bank 段头)
DAYFREZ_WRITE_VARS = ("frezNum", "i", "stInfo", "buff")              # 断[B] 处可读
DAYFREZ_WAIT = 190.0      # 等写库点命中: 拨到日界前 1 分钟, 至多 ~3 分钟到点
DAYFREZ_NEG_WIN = 95.0    # ⑤ 否定期望窗口: 比一个整分间隔长, 保证窗口里至少跨过一个整分
DAYFREZ_MIDDAY_TOD = "12:00:05"   # ⑦ 之前把 g_HisTime 钉在白天那一步的时刻(消掉 :140 的日界二义)
DAYFREZ_MONTHEND_TOD = "23:59:05"  # ⑦ 跨月那条的拨钟时刻(月末这一天的这个点)

# ============================ 〔BILLFREZ / BILLFREZY〕结算日冻结 / 阶梯结算冻结 ============================
BILLFREZ_SUBCLASS = 0x05   # 结算日冻结记录子类(记录 OAD = 50 05 02 00)
BILLFREZ_INDEX = 12        # ID_BillFrezM(FrezData.h 的 ID_FREZ 枚举) ⇒ s_stFrzStorageInfo[12]
BILLFREZ_DEPTH = 12        # NUM_BillFrezM(UserCfg.h) —— 存储上 12 个结算日
BILLFREZ_ADD = 12          # TAB_FrezAdd[6](UserCfg.c:196 = {0,0,0,7,0,0,12,4}) —— 上电最多补 12 条
BILLFREZY_SUBCLASS = 0x11  # 阶梯结算冻结记录子类(记录 OAD = 50 11 02 00)
BILLFREZY_INDEX = 19       # ID_BillFrezY(FrezData.h 的 ID_FREZ 枚举) ⇒ s_stFrzStorageInfo[19]
BILLFREZY_DEPTH = 6        # NUM_BillFrezY(UserCfg.h) —— 应可存储 6 次
BILLFREZY_ADD = 4          # TAB_FrezAdd[7](UserCfg.c:196) —— 上电最多补 4 条

# ============================ 〔零散〕 ============================
# ---- g_CurkWh 的项 → 698 去路的换算参数。**逐 OAD 登记**, 逐条抄自 DLT698App.c 的调用点。 ----
# 2026-09-18 改: 旧写法是一把尺子 `(v & MASK5) // 100` 量所有电类所有 OAD。实际 `num`/`dot`/
# `code`/`sign` **都是逐调用点定的**, 不是表级常量。逐条对过源码后:
#   · 1-2 读的 10 个 OAD 全是 `xx 04 00`(高精度族), 走 `:6930-6983` 与 `:7000-7033` 两个**同构**块:
#       `len = (pOAD[2]==0x02)? 4: 8`                                   (:6964 / :7014) ⇒ 8
#       `Convert_EnyData(&buff[addr], len, len/2, HEX, TAB_EnySign[..])` (:6979 / :7029)
#     ⇒ `num=8, dot=len/2=4, code=HEX, sign=TAB_EnySign[下标]`。
#     `TAB_EnySign[]`(DLT698App.c:3284) = `{1,0,0,1,1,0,0,0,0,0,0,0,0}`; 这 10 个 OAD 取到的下标
#     是 `pOAD[1]>>4 ∈ {0,1,2}` 或 `u8 ∈ {9,10,11,12}` ⇒ **全是 0(无符号)**。
#   · **不是全表统一** —— 别的调用点另有定标: `:7073 / :7122 / :7168 / :7214 / :13469 / :13889 /
#     :13920` 是 `dot=6`, `:5860` 是 `dot=(OAD&0x0F)*2`。而 `dot=6` 落到 switch 的 `default`
#     ⇒ **一次都不除**(kWhData.c:237)。凭"去路就是 //100"去算表外 OAD, 会静默算出一个错数。
#     ⇒ 表外 OAD 一律**拒算**(`kwh_to_698val` 返回 None), 不许套默认值。
#   · 出线宽度: `Read_CurkWh`(kWhData.c:183/188)只搬 5 字节, `Convert_EnyData`(:227)也只从
#     `pBuff[0..4]` 拼 —— **40 位**。故先截 40 位再除(顺序与固件一致: 先拼 u64, 再 /=)。
#   · ⚠ 2026-09-18 更正: 上一轮我在这里写过"注释说 4 位小数、代码除 100, 注释与代码不符" ——
#     **那句是错的**, 是我按常识读的, 没把原始计数的标度算进去。把标度补上就完全自洽:
#       表里存的原始计数是 **1e-6 kWh(微千瓦时, 6 位小数)**;
#       `dot` 是"报文里保留几位小数", 故除法是 `10^(6-dot)`:  dot=0→1e6 / 1→1e5 / 2→1e4 /
#       3→1e3 / **4→100** —— `case 4: /= 100` 正好把 6 位小数砍成报文要的 4 位。注释没错。
#     实证(09-18 实表): 格子 459550482753 与 698 报文 4595504827 是同一个电量
#       459550.482753 kWh(只取低 5 字节、再除 100、截尾); 总格 150 与报文 1 同理(0.00015 kWh)。
#     下表是 `:230-238` 那个 switch 的逐字转写; 未列出的档位(dot=5/6)走 `default` ⇒
#     不除, 故 `.get(dot, 1)` —— 那是固件自己的不连续处, 不是本表的取舍。
KWH_WIRE_BYTES = 5                                  # `Convert_EnyData` 只拼 pBuff[0..4]
KWH_VAL_MASK5 = (1 << (8 * KWH_WIRE_BYTES)) - 1     # = 0xFFFFFFFFFF
KWH_DOT_DIV = {0: 1000000, 1: 100000, 2: 10000, 3: 1000, 4: 100}
_KWH_CONV_HI = (8, 4, "HEX", 0)                     # `xx04 00` 族: num=8, dot=4, HEX, 无符号
_KWH_CONV_HI_S = (8, 4, "HEX", 1)                   # 同族里**有符号**的两条: 组合无功 1/2
KWH_OAD_698CONV = {                                 # 逐 OAD 显式列(同 CURKWH_OAD_ROW 的规矩)
    "00100400": _KWH_CONV_HI,   # 正向有功
    "00200400": _KWH_CONV_HI,   # 反向有功
    "00300400": _KWH_CONV_HI_S, # 组合无功 1 —— 记录读回那一列走 :5838 `sign = TAB_EnySign[OAD>>20]`
                                #   = TAB_EnySign[3] = 1(:3284 的 `{1,0,0,1,…}`) ⇒ 有符号
    "00400400": _KWH_CONV_HI_S, # 组合无功 2 —— 同上, TAB_EnySign[4] = 1
    "00500400": _KWH_CONV_HI,   # 第一象限无功
    "00600400": _KWH_CONV_HI,   # 第二象限无功
    "00700400": _KWH_CONV_HI,   # 第三象限无功
    "00800400": _KWH_CONV_HI,   # 第四象限无功
    "01100400": _KWH_CONV_HI,   # 正向基波有功
    "01200400": _KWH_CONV_HI,   # 反向基波有功
    "02100400": _KWH_CONV_HI,   # 正向谐波有功
    "02200400": _KWH_CONV_HI,   # 反向谐波有功
    # ---- 分冻结记录表第 1 行那 12 个**单项**电能量(`xx100401`)：只出现在记录里, 按记录那一支登记 ----
    # ⚠ 上面那一族按 **GET 读**那一支取 sign(`:6964`/`:7014` 用 `pOAD[1]>>4`), 这一族按**记录读回**
    #   那一支取 sign(`:5838` 用 `OAD>>20`)；两支下标算法不同, 同一个 OI 可以不同号 ⇒ 逐条登记, 不合并。
    #   两族的 dot 都是 4: 记录那支 `j = ((OAD>>8)&0x0F)==0x02 ? 4 : 8`(:5814), `dot = j/2`(:5838)。
    # ⚠ 这一族的 sign 取"**该 OAD 的物理量是什么符号**", 不抄固件 `TAB_EnySign` 的当下取值 —— 拿固件
    #   自己的尺子当期望, 固件把尺子拿错(见下)时读回值反而"对得上", 判据就永远绿。
    #   符号的依据不在固件源码里, 在线路上: 组合无功 1/2 那两列的对象位实测是 `0x14`(D_Long64 有符号),
    #   其余十条是 `0x15`(D_Long64Un 无符号), 类别见 `DLT698App.c:143-144` —— 与 `xx04 00` 那一族同号。
    #   ⚠ 早先一律取无符号是过头了: 那样固件把组合无功那两列的符号性翻掉也照样过(尺子上少刻一道)。
    # ⚠ `02100401` / `02200401` 与上面十条**一样登记**(不摘掉、不"不比")：`TAB_EnySign` 只有 13 项
    #   (实测 .out 符号为 `[13]`), 这两条的 `OAD>>20` = 33 / 34 越界, 固件读到邻居常量表 TAB_RevByte
    #   的头两字节(0x08 / 0x04, 都 ≥2)⇒ `Convert_EnyData` 见 `sign>=2` **空转返回**(kWhData.c:222),
    #   记录里那两列留着未初始化的槽内容。这正是判据⑪要判掉的那一支, 期望值照 4 位小数、各自该有的
    #   符号给(这两条本身是无符号, 见上)。
    "00100401": _KWH_CONV_HI,   # 正向有功(记录·单项)
    "00200401": _KWH_CONV_HI,   # 反向有功(记录·单项)
    "00300401": _KWH_CONV_HI_S, # 组合无功 1(记录·单项) —— 有符号: 线路对象位 0x14
    "00400401": _KWH_CONV_HI_S, # 组合无功 2(记录·单项) —— 同上, 线路对象位 0x14
    "00500401": _KWH_CONV_HI,   # 第一象限无功(记录·单项)
    "00600401": _KWH_CONV_HI,   # 第二象限无功(记录·单项)
    "00700401": _KWH_CONV_HI,   # 第三象限无功(记录·单项)
    "00800401": _KWH_CONV_HI,   # 第四象限无功(记录·单项)
    "01100401": _KWH_CONV_HI,   # 正向基波有功(记录·单项) —— `OAD>>20` = 17 也已越出 `TAB_EnySign[13]`
    "01200401": _KWH_CONV_HI,   # 反向基波有功(记录·单项) —— 同上, `OAD>>20` = 18
    "02100401": _KWH_CONV_HI,   # 正向谐波有功(记录·单项) —— `OAD>>20` = 33 越界, 见上
    "02200401": _KWH_CONV_HI,   # 反向谐波有功(记录·单项) —— `OAD>>20` = 34 越界, 见上
}

