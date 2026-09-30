# -*- coding: utf-8 -*-
"""
_restore_all.py —— 总复位: 跑完测试后一键把试验台收拾回正常态
位置: 帧收发基础/scripts/ (顶层运行脚本; 库在 src/meterlib/ 与 src/swdbg/)
前身: _restore_clock.py(只做拨钟)。2026-09-10 并入"退出调试模式"等步骤, 升级为总复位入口。

为什么需要它 —— 跑完一轮白盒测试, 台上会残留两类"非正常态", 以前靠人记着逐个收拾:
  ① 调试残留: 被 gdb/Cortex-Debug 撂在 halt 的 Cortex-M0, 加上没清的 FPB 硬件断点槽
     (M0 只有 4 个槽, 占满后续断点就下不去)。**halt 住时串口全无应答**, 现象与"表死机/
     串口坏"一模一样, 极易误判(2026-09-10 已踩过一次)。
  ② 表态残留: 跳钟试验把表钟拨到结算日 0 点(假钟); 拉闸用例把继电器留在分闸。

步骤(顺序有硬约束, 见下)
------------------------
  [0] --dry 预演: 只打印, 不开串口、不连 J-Link
  [1] 退出调试模式        —— swdbg.restore.release_debug(): 放行核心 + 清 FPB 断点槽
  [2] 打开串口 + 进厂内   —— 645.factory(后续动作帧的前置安全判定)
  [3] 拨表钟回真实时间    —— 698 Set OAD 40000200 → **计量芯**(管理芯同对象 Set 被禁 DAR=FF)
  [4] 继电器恢复合闸      —— 先过 relay_precheck(75%Un 电压判定), 不满足记 TBD
  [5] 收尾复核            —— 双芯读钟 + 结算日读back
  [5b] 串口冒烟           —— smoke 电池 → HEALTHY
  [5c] 退出厂内           —— 645 0x1F/0F AA 00 (**必须排最后**, 见下)

⚠ **[1] 必须排在所有串口步骤之前**, 且失败即中止: 核心被 halt 时串口一条都发不出去,
  若不管它继续往下跑, 后面每一步都会以"串口坏了"的假象失败, 掩盖真正的病因。

⚠ **厂内态有串口退出路径**(2026-09-10 实测**推翻**旧写法)。旧文档(本文件头/CLAUDE.md/总纲 §10.2)
  写"无退出路径, 只能断电/复位" —— 不成立: 645 `0x1F / LEN=3 / DI=[0F AA 00]`(帧体 `42 DD 33`)
  走 `FDW_FacMode`(DLT645App.c:3566) 的 `DI1==0xaa` 那支, 直接 `Set_PrgTimer(0)`, **且该支无判定**。
  实测: 发前 g_PrgTimer[0]=255 → 发后 0, 应答尾 `9F 00 D5 16`。库动词 = `CB.exit_factory`,
  本脚本第 [5c] 步用它。
⚠ [5c] 必须排在**所有会改状态的步骤之后**, 两头都咬人:
  · 排太前 → 出了厂内, 698 动作/0x14 写全被打回(ER_PSWD / DAR_MatchAuth), 拨钟与合闸都做不成;
  · 排在 [5b] 冒烟**之前**也不行 —— 冒烟电池里含 `645.factory`, 会把厂内态**又打开**
    (2026-09-10 实测: [5a] 退了 PASS 255→0, 冒烟一跑白退, 而步骤汇总**全是 PASS**, 看不出来)。

有意不做的(经评审确认, 勿"顺手加上")
----------------------------------
* **不碰 clear_meter / clear_event**(cmd_bank 的清零动词): 它们真清空 电量/需量/冻结 与
  事件库, 高风险且未实测。总复位的语义是"收拾回正常态", 不是清库。
* **不动结算日**(只读复核, 不写): 改结算日的脚本自己负责写回原值 —— `billday_rw_roundtrip` 是
  写→验→写回; `_test_4_6_aa80.py` 在 `finally` 里还有一道强制兜底。本脚本在第 [5] 步**读出来给人看**,
  好让"自恢复"这条假设一旦不成立就**当场可见**, 而不是静默留在 alt 号上。
* **不抹测试痕迹**: 测试落下的冻结/事件记录不可撤销, 也不该被本脚本动。

运行(在 帧收发基础/ 下)
----------------------
    python scripts/_restore_all.py                       # 全量总复位
    python scripts/_restore_all.py --dry                 # 离线预演, 不开串口不连 J-Link
    python scripts/_restore_all.py --to 'YYYY-mm-dd HH:MM:SS'   # 指定目标时间(默认 主机now+3s)
    python scripts/_restore_all.py --no-debug            # 本机没接 J-Link 时跳过退调试模式
    python scripts/_restore_all.py --no-clock --no-relay # 只收拾调试残留

退出码: 0=全部恢复(含 SKIP/TBD, 无 FAIL) / 1=有步骤 FAIL 需人工核 / 2=前置失败中止
"""
import datetime
import sys
import time


from meterlib import cmd_bank            # 协议/动词层
from common.cli import guard_argv              # 入口参数守卫(机制, 不认表)
from common.portsel import open_com         # 串口
from swdbg import restore                # SWD 恢复(放行核心 + 清 FPB)
from swdbg import resolve as varresolve        # 变量名→地址(.out 符号表)
from project import CURRENT               # 画像(库不认表)
from common import machspec              # 装机卡带: 串口口名等"这台机器"的事实(与表画像正交)

# ---- 子项参数(纯数据) ----
# 端口是**机器**的事实(USB-485 桥插在哪), 真源在 machine/装机卡带 —— 别在这儿写第二份字面量。
# 取到 None 也不当场炸: 本脚本的 `--dry` 要能离线跑通; 真开串口时 `open_com()` 会点名缺 COM。
PORT = machspec.get("COM")
RELAY_RESTORE_OP = "合"                        # 正常态 = 合闸(负载通电)


# 控制台 UTF-8 单点: 实现收在 common/console.py(2026-09-10 收敛, 原先本文件内联一份)。
# 别名保原调用点 `_utf8_stdout()` 一字不改; 幂等 + 带非可重配流回退, 比原内联版强。
from common.console import ensure_utf8_stdout


def _prgtimer():
    """SWD 直读 g_PrgTimer 头 1 字节(Is_EnablePrg() 的真值源)。读不到回 None(不抛)。
    地址由 .out 符号表解析 —— 别在这儿写死地址。"""
    try:
        from swdbg import probe
        addr = varresolve.resolve("g_PrgTimer")[0]
        with probe.Probe() as p:
            b = p.read_abs(addr, 4)
        return None if b is None else b[0]
    except Exception as e:
        print("   (SWD 读 g_PrgTimer 跳过: %s)" % e)
        return None


def _probe_why(d=None):
    """[1] 连不上探针时, 当场问一句"它到底怎么了" → 一串人话(查不了就给一句说明, 不抛)。

    **只诊断, 不动手** —— 设备节点动不动由人来定(`python -m swdbg.jlink --recover`)。
    这一步也**绝不结束任何进程**: 强杀握着 J-Link 的进程正是把探针撂到要重新上电的元凶。

    ⚠ **按端诊断**(2026-09-20 双端起): 两端的设备树诊断各自成篇 —— 拿 J-Link 那一套去诊断一支
      DAPLink, 会得到一段**看着像结论的错话**(它会把"没枚举到 J-Link"当成病因报出来)。
      `d` 是 `restore.release_debug()` 的详情字典, 里面记着实际用的是哪一端。
    """
    backend = (d or {}).get("backend")
    if backend is None:
        return ["探针诊断: 还没选出探针(原因见上一行) —— 完整诊断: "
                "python -m swdbg.probesel --doctor"]
    if backend != "jlink":
        try:
            from swdbg import probe_cmsis
            line = probe_cmsis.diagnose_brief()
            return [line or ("探针诊断: CMSIS-DAP 枚举得到探针, USB 这头是活的 ⇒ 坏在**探针到目标"
                             "的那一段**(或选探针那一步)。"
                             "完整诊断: python -m swdbg.probesel --doctor")]
        except Exception as e:
            return ["探针诊断跑不了(%s); 手动: python -m swdbg.probesel --doctor" % e]
    try:
        from swdbg import jlink
        dg = jlink.diagnose()
        lines = ["结论: %s —— %s" % (dg["state"], dg["summary"])]
        lines += ["  · %s" % e for e in dg["evidence"]]
        lines += ["  → [%s] %s" % (a["kind"], a["why"]) for a in jlink.plan_recovery(dg)]
        lines.append("  完整诊断/恢复: python -m swdbg.jlink --doctor / --recover")
        return lines
    except Exception as e:
        return ["探针诊断跑不了(%s); 手动: python -m swdbg.jlink --doctor" % e]


def _has(flag):
    return flag in sys.argv


def _target_time():
    """目标时间: --to 指定, 否则主机 now +3s(前置余量防落在过去秒)。"""
    if "--to" in sys.argv:
        return sys.argv[sys.argv.index("--to") + 1]
    return (datetime.datetime.now() + datetime.timedelta(seconds=3)).strftime("%Y-%m-%d %H:%M:%S")


def main():
    ensure_utf8_stdout()
    # 未识别开关=拦死。本脚本会真拨表钟, 而 _has() 只按【存在的开关】分支 —— 拼错一个字母
    # (如 --dryy) 它不报错, 直接退化成"全量真跑"。宁可入口拦死, 也不要以为在预演、实际动了表。
    # 守卫从 common.cli 取,**不是** CB.guard_argv: 它住 common, cmd_bank 只是重导出。
    guard_argv(sys.argv[1:],
               allow=("--dry", "--no-debug", "--no-clock", "--no-relay",
                      "--no-smoke", "--keep-servers", "--help", "-h"),
               known=("--to",))
    do_debug = not _has("--no-debug")
    do_clock = not _has("--no-clock")
    do_relay = not _has("--no-relay")
    do_smoke = not _has("--no-smoke")
    keep_servers = _has("--keep-servers")
    target = _target_time()

    print("== 总复位: 把试验台收拾回正常态 ==")
    print("   [1] 退出调试模式  %s" % ("开" if do_debug else "跳过(--no-debug)"))
    print("   [2] 进厂内        %s" % "开")
    print("   [3] 拨钟回真实时间 %s  目标=%s"
          % ("开" if do_clock else "跳过(--no-clock)", target))
    print("   [4] 继电器合闸    %s" % ("开" if do_relay else "跳过(--no-relay)"))
    print("   [5] 收尾复核      %s" % ("双芯读钟 + smoke" if do_smoke else "双芯读钟"))

    if _has("--dry"):
        print("\n--dry 预演: 以上步骤只打印, 不开串口、不连 J-Link。")
        return 0

    results = []

    def rec(step, status, note=""):
        results.append((step, status, note))
        print("   => %-14s [%s]%s" % (step, status, ("  " + note) if note else ""))

    # ---------- [1] 退出调试模式(在串口之前!) ----------
    if do_debug:
        print("\n[1] 退出调试模式(放行被停住的核 + 清 FPB 断点槽) ...")
        ok, d = restore.release_debug(clean_stray=not keep_servers)
        if d["stray"]:
            print("   已清理残留调试进程: %s" % ", ".join(d["stray"]))
        if not ok:
            if d["fatal"] == "unreachable":
                print("   !! 连不上探针 / 目标板(%s 端): %s" % (d.get("backend"), d["reason"]))
                print("      若本机没接调试器, 用 --no-debug 跳过这一步。")
            else:
                print("   !! %s" % d["reason"])
            # 「连不上探针」有两种起因, 处置完全相反: 探针自己不在 USB 上(要动设备节点), 还是
            # 探针在而通信层坏了(要禁用+启用)。别在这儿猜 —— 问一句就有答案(按端问)。
            print("   -- 探针诊断 --")
            for line in _probe_why(d):
                print("      %s" % line)
            rec("退调试模式", "ABORT", d["reason"])
            print("\n== 中止 ==")
            print("  核心可能仍被停住, 串口不会应答; 后续串口步骤已放弃执行。")
            return 2
        print("   DHCSR %s -> %s"
              % ("0x%08X" % d["dhcsr_before"] if d["dhcsr_before"] is not None else "(未读)",
                 "0x%08X" % d["dhcsr_after"] if d["dhcsr_after"] is not None else "(无需复核)"))
        rec("退调试模式", "PASS", d["reason"])
    else:
        print("\n[1] 退出调试模式 ... 跳过(--no-debug)")
        rec("退调试模式", "SKIP", "未验证核心是否在运行")

    # ---------- [2..5] 串口侧 ----------
    print("\n[2] 打开串口 %s 并进厂内(645.factory) ..." % PORT)
    try:
        ser = open_com(PORT)
    except Exception as exc:
        print("   !! 打不开 %s: %s" % (PORT, exc))
        rec("开串口", "ABORT", str(exc))
        print("\n== 中止 ==")
        return 2

    try:
        if cmd_bank.enter_factory(ser) != "PASS":
            print("   !! 进厂内未 PASS, 中止(后续动作帧缺前置安全判定, 发了也会被拒)")
            rec("进厂内", "ABORT", "645.factory 未 PASS")
            print("\n== 中止 ==")
            return 2
        rec("进厂内", "PASS")
        time.sleep(0.3)

        # ---------- [3] 拨表钟回真实时间 ----------
        clock_ok = True
        if do_clock:
            print("\n[3] 拨表钟回真实时间: 698 Set 40000200 -> 计量芯 写 %s ..." % target)
            v, note, dar = cmd_bank.set_meter_clock_set(ser, target, chip="计量芯")
            if v != "PASS":
                print("   !! 计量芯 Set 拒写(%s, DAR=%s), 中止" % (note, dar))
                rec("拨表钟", "ABORT", "计量芯拒写 dar=%s" % dar)
                print("\n== 中止 ==")
                return 2
            time.sleep(0.5)
            print("   回读双芯确认 ...")
            got_m = cmd_bank.read_clock(ser, chip="计量芯")
            got_n = cmd_bank.read_clock(ser, chip="管理芯")
            ok_m = bool(got_m and got_m[:16] == target[:16])
            ok_n = bool(got_n and got_n[:16] == target[:16])
            print("   主钟(计量芯)=%s  %s" % (got_m, "命中" if ok_m else "未命中!"))
            print("   管理芯跟随   =%s  %s" % (got_n, "命中" if ok_n else "未命中!"))
            clock_ok = ok_m and ok_n
            rec("拨表钟", "PASS" if clock_ok else "FAIL",
                "双芯命中 %s" % target if clock_ok else "回读未命中目标, 需人工核")
        else:
            print("\n[3] 拨表钟 ... 跳过(--no-clock)")
            rec("拨表钟", "SKIP")

        # ---------- [4] 继电器恢复合闸 ----------
        if do_relay:
            print("\n[4] 继电器恢复合闸(正常态 = 负载通电) ...")
            rp_ok, rp_note = cmd_bank.relay_precheck(ser)
            print("   relay_precheck: %s (%s)" % ("通过" if rp_ok else "不满足", rp_note))
            if not rp_ok:
                # 电压判定(75%Un)不满足 → 固件闭锁, 发了也不动。这不是故障, 是台面条件。
                rec("继电器合闸", "TBD", "电压判定未满足, 跳过: %s" % rp_note)
            else:
                v = cmd_bank.ctrl_relay(ser, RELAY_RESTORE_OP)
                rec("继电器合闸", "PASS" if v == "PASS" else "FAIL", "ctrl_relay %s" % v)
        else:
            print("\n[4] 继电器合闸 ... 跳过(--no-relay)")
            rec("继电器合闸", "SKIP")

        # ---------- [5] 收尾复核 ----------
        print("\n[5] 收尾复核: 双芯读钟 ...")
        fin_m = cmd_bank.read_clock(ser, chip="计量芯")
        fin_n = cmd_bank.read_clock(ser, chip="管理芯")
        print("   主钟(计量芯)=%s" % fin_m)
        print("   管理芯      =%s" % fin_n)
        rec("双芯读钟", "PASS" if (fin_m and fin_n) else "FAIL")
        # 结算日: **只读不写**(语义上不归总复位管, 见文件头"不动结算日"), 但必须**读出来给人看** ——
        # 它是"脚本跑完已自恢复"那条假设的兑现检查。4-6 这类改结算日的脚本若中途死/台面没接 J-Link,
        # 表会**静默留在 alt 号**上; 这里是唯一会发现它的地方(2026-09-10: 那条假设确实曾不成立 ——
        # breakpoint 的 `_drop` 参数泄漏把写回那次变成了静默 TypeError)。详见 _test_4_6_aa80.py 的 finally 兜底。
        bd = cmd_bank.read_billday(ser)
        print("   第1结算日  =%s" % ("每月%d号%d点" % (bd[1], bd[0]) if bd else "读不到"))
        rec("结算日复核", "PASS" if bd else "FAIL", "每月%s号" % (bd[1] if bd else "?"))


    finally:
        try:
            ser.close()
        except Exception:
            pass

    # ---------- [5b] 串口冒烟 ----------
    # ⚠ 必须放在 ser.close() **之后**: 冒烟要自己独占打开 COM3, 上面那条串口没关掉就调它,
    #   CreateFileW(COM3) 会 err=5(拒绝访问) → 假 UNHEALTHY。2026-09-10 首跑踩到。
    if do_smoke:
        print("\n[5b] 串口冒烟(证明链路真的活了) ...")
        rc = cmd_bank.smoke()
        rec("串口冒烟", "PASS" if rc == 0 else "FAIL",
            "SMOKE %s" % ("HEALTHY" if rc == 0 else "UNHEALTHY(rc=%s)" % rc))
    else:
        rec("串口冒烟", "SKIP")

    # ---------- [5c] 退出厂内 ----------
    # 为什么排在**最后**(而不是 [5] 之后): 冒烟电池里就含 `645.factory`, 它会把厂内态**又打开**。
    # 2026-09-10 实测踩到 —— [5a] 先退了 PASS(g_PrgTimer 255→0), [5b] 冒烟一跑又变回厂内,
    # 白忙一场且**看着是好的**(步骤全 PASS)。凡"收尾动作"都要排在**最后一个会改状态的步骤**之后。
    # 也因为它排在 ser.close() 之后, 这一步自己开串口(与冒烟同理, 不多占一条连接)。
    print("\n[5c] 退出厂内(645 0x1F LEN=3 DI0=0x0f DI1=0xaa → Set_PrgTimer(0)) ...")
    # 状态证据(有 J-Link 时): 只认应答 = 假通过风险(受理 ≠ 真执行)。地址取画像 g_PrgTimer,
    # 与断点观测/SWD 观测共用同一个"变量名→地址"真源。
    prg_before = _prgtimer() if do_debug else None
    if prg_before is not None:
        print("   发前 g_PrgTimer[0] = %s (Is_EnablePrg=%s)" % (
            prg_before, "真(在厂内)" if prg_before else "假(本就不在厂内)"))
    v, prg_after = "SKIP", None
    try:
        ser2 = open_com(PORT)
        try:
            v = cmd_bank.exit_factory(ser2)
        finally:
            ser2.close()
        prg_after = _prgtimer() if do_debug else None
        if prg_after is not None:
            print("   发后 g_PrgTimer[0] = %s → %s" % (
                prg_after, "已退厂内" if not prg_after else "**仍在厂内**"))
    except Exception as e:
        v = "FAIL"
        print("   退出厂内失败: %s" % e)
    if v != "PASS":
        rec("退出厂内", v if v == "FAIL" else "TBD", "CB.exit_factory 未受理(v=%s)" % v)
    elif prg_after:
        rec("退出厂内", "FAIL", "应答受理了但 g_PrgTimer[0]=%s 仍非 0" % prg_after)
    else:
        # ⚠ 拿不到状态证据时报"仅串口层", **不许**含糊地写成"已退厂内"(那是没证据的断言)。
        note = ("645 0x1F/0F AA 00 + SWD 复核 g_PrgTimer: %s→0" % prg_before
                if prg_after is not None else
                "645 0x1F/0F AA 00 受理应答(9F 00); **仅串口层** —— 未接 J-Link, 状态未复核")
        rec("退出厂内", "PASS", note)

    # ---------- 汇总 ----------
    n_fail = sum(1 for _, s, _ in results if s in ("FAIL", "ABORT"))
    n_tbd = sum(1 for _, s, _ in results if s == "TBD")
    print("\n== 总复位汇总 ==")
    for step, status, note in results:
        print("   %-14s %-6s %s" % (step, status, note))
    print("   -- FAIL %d / TBD %d / 共 %d 步 --" % (n_fail, n_tbd, len(results)))
    print()
    print("   厂内态: 见上面 [5c] 一行(2026-09-10 起本脚本**会**发 645 0x1F/0F AA 00 退出去;"
          "\n   该步 PASS 即 g_PrgTimer 已归 0。若跳过或 FAIL, 可单独发 `cmd_bank send 退厂内`。)")
    print("   提醒: 测试落下的冻结/事件记录不可撤销, 本脚本有意不动它们。")
    print("\n== %s ==" % ("总复位完成" if n_fail == 0 else "总复位完成但有失败, 需人工核"))
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
