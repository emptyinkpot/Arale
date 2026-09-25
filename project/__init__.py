# -*- coding: utf-8 -*-
"""
project/ —— 电表"每表环境包"(换工程 = 换 CURRENT 指向)

一块表 = 一份可 import 的环境包(卡带), 装它 = 有调这张表的机器条件。内容都是数据 + 装包即验,
【没有给人读的知识】(知识单列在外部文档, 如 project/knowledge/对表操作总纲.md / CLAUDE.md / 经验总结)。通用机制
(698/645 收发/解析/AA80/双芯切换框架)是共享引擎, 在 meterlib, 不在这里。

每表环境 = 一组建前缀 <画像名> 的数据 + 一个自检:
  <画像名>.py          ① 数据画像(纯常量): 身份/双芯AF/表号/RAM地址图/OAD/.out 路径
  <画像名>.frames.json ② 该表"已验证帧"(叠加层, cmd_bank 合并进目录)
  <画像名>.meta.json   ③ 环境清单: 固件锁(build/验期) + 双芯可达能力 + 串口(机器数据)
  env_check.py         ④ 装包即验: env_check(online=?) 断言环境条件在不在
换测另一块表: 在 project/ 加一份同款三件套(前缀换成该表名), 把本文件的 CURRENT 指过去即可;
核心(meterlib)与脚本原样复用, 换的只有这份环境包。env_check 永远验 CURRENT。

加一块新表的四步
----------------
1. 写 `<表名>.py` 画像(纯常量): 身份 / 双芯 AF / 表号 / RAM 地址图 / `.out` 路径,
   外加**布局声明** —— `TESTS_DIR` / `KNOWLEDGE_DIR` / `MASTER_DOC` / `CASES_FILE` /
   `REPORTS_DIR`。通用层不硬编码这些路径, 只经 `profile.resolve(...)` 解析。
2. 写 `<表名>.meta.json` 环境清单: 固件锁 / 双芯可达 / 串口 + `firmware` 块(含 `out_sha256`)。
   可选 `<表名>.frames.json` 登记该表已验证帧。
3. `python scripts/_probe_all.py --from-project`(或给目录)探这块表 → `探测报告/<表名>_探测.md`。
4. 把本文件的 `CURRENT` 指过去, 跑 `python -m project.env_check` 验到 `READY`。

**这里还是"组合根"**(2026-09-10 阶段二)
--------------------------------------
共享引擎(meterlib)原先各自写着 `from project import CURRENT as P` —— 方向反了: 换表要改
project/, 却连 meterlib 也得跟着 import 一个可能不存在的 project。现在 meterlib 只认识
中立的 `common.profile`, **由本文件(卡带自己)把画像装上去** —— 即"装卡带"这个动作的正式落点,
与上面第 5 行那句"装它 = 有调这张表的机器条件"是同一件事, 只是从隐喻落成了代码。

    project ──→ common.profile ←── meterlib      (两边都指向中立层, 彼此不相识)

依赖方向因此是 project → common(向下), **不是** meterlib → project(横向)。改 import 前先看
common/__init__.py 头部那张依赖图。下面那句 configure 必须在 import CURRENT 之后。

路径引导 —— 已删(2026-09-20)
---------------------------
上面那句 `from common import profile` 要求 `帧收发基础/src/` 已在 sys.path 上。2026-09-11 起
本文件(与 `machine/__init__.py` 同款)在自己头上写了一段"向上找含 src/ 的仓根、塞进 sys.path"的
引导, 好让"卡带自己保证能被 import 起来"。

2026-09-20 那段删了 —— **这件事现在归 `pip install -e .`**: 仓根 `pyproject.toml` 把六个顶层包
(含 `common` 与 `project`)声明明白, 装一次之后从哪个目录 import 都通, 不必每个模块自己猜仓根在
第几层。原先那份样板满仓八十来份, 分布漂移, 而**分布一漂就静默失效**(找不到 src/ 时那段 while
会一路走到盘根, 然后把无关目录塞进 sys.path —— 不报错, 只是 import 到了别的东西)。
"""


from . import ez315_fm33a0610 as CURRENT   # 当前活动工程画像(唯一换工程点)

from common import profile     # 中立层; 本包是它唯一的装配方
profile.configure(CURRENT)
