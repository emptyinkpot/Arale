# -*- coding: utf-8 -*-
"""
common/winpnp.py —— Windows 设备树: 查某个 VID 的节点在不在位、坏没坏、什么时候掉的;
                    以及把设备节点重新枚举 / 重启 / 禁用启用(后两样要管理员)。

为什么要有它
------------
2026-09-17 J-Link 探针从 USB 上掉了下来, 现场只剩下一堆互相矛盾的猜测 ——「线没插好」
「驱动坏了」「表挂了」; 能定案的证据其实一直躺在 Windows 设备树里, 但那几条 PowerShell
是**临时敲的**: 敲完就没了, 下次还得从头推一遍, 而且很可能又推错。

本模块把那套查询与判据固定下来, 让「探针掉线」这件事变成一条命令的结论。

为什么住 common/
零协议、零画像 —— 它只认识 USB 的设备节点与 Windows 的 PnP 属性, 不认识 645/698,
也不认识"哪块表/哪台机器"。按本仓层判据(见 common/__init__.py 那张图)它属中立层。
**住这儿的直接好处**: 装机卡带 `machine/env_check.py` 要用它(探针枚举不到时说出为什么),
而那一包**不许 import swdbg**(它自己的第 9 节铁律)。

判别力来自三样事实(缺一样就定不了案)
------------------------------------
"枚举不到探针"是一个**结论**, 不是**证据**。要看的是:
  ① 现在在位的设备里, 还有没有这个 VID 的节点;
  ② 历史节点(含已移除的)最后一次**到达**/**移除**是什么时候;
  ③ 有没有一个**读不出描述符**的节点(Problem != 0, 典型 Code 43)紧跟在那次移除之后
     —— 那是「同一个东西掉了又回、停在需要重新上电的状态」的指纹。
     ⚠ 这一条专门用来防一句很顺口的错话: 「一定是你没插好」。实测那次用户线一直插着。

⚠ 本模块**不结束任何进程**。它只查设备节点、只动设备节点 —— 强杀握着 J-Link 的进程
  (JLink.exe / JLinkGDBServerCL.exe / 经 pylink 打开 DLL 的 Python 进程)**正是**把探针
  撂到需要重新上电的元凶(2026-09-17 实踩, 见 CLAUDE.md「调试链」关键纪律)。
"""
from __future__ import print_function

import datetime
import json
import os
import subprocess
import tempfile

__all__ = ["PnPError", "devices", "problems", "summarize", "dropped_then_returned",
           "describe_node", "is_elevated", "scan_devices", "restart_device",
           "cycle_device", "parse_nodes"]

# PowerShell 一律这么起: 不读用户 profile(免得被别名/函数干扰)、不交互、临时绕过执行策略。
_PS = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"]


class PnPError(RuntimeError):
    """设备树查不了 / 动不了时抛这个, 带人话解释。"""


# ---------------------------------------------------------------------------
# 一、查询
# ---------------------------------------------------------------------------
def _check_platform():
    if os.name != "nt":
        raise PnPError("设备树是 Windows 的东西; 本机是 %s, 用不了这一套。" % os.name)


def _ps(script, timeout=30.0):
    """跑一段 PowerShell(不提权), 返回 stdout 文本。非零退出也返回文本 —— 由调用方判。"""
    _check_platform()
    r = subprocess.run(_PS + ["-Command", script], capture_output=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return (r.stdout or "") + (r.stderr or "")


_QUERY = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'SilentlyContinue'
$vid = '@VID@'
$out = @()
foreach ($d in Get-PnpDevice) {
  if ($d.InstanceId -notlike ('USB\*VID_' + $vid + '*')) { continue }
  $as = ''; $rs = ''
  foreach ($p in (Get-PnpDeviceProperty -InstanceId $d.InstanceId -KeyName 'DEVPKEY_Device_LastArrivalDate','DEVPKEY_Device_LastRemovalDate')) {
    if ($p.Data) {
      if ($p.KeyName -like '*LastArrivalDate') { $as = $p.Data.ToString('o') }
      if ($p.KeyName -like '*LastRemovalDate') { $rs = $p.Data.ToString('o') }
    }
  }
  $out += [pscustomobject]@{
    instance_id  = [string]$d.InstanceId
    present      = [bool]$d.Present
    status       = [string]$d.Status
    problem      = [int]$d.Problem
    problem_desc = [string]$d.ProblemDescription
    klass        = [string]$d.Class
    friendly     = [string]$d.FriendlyName
    arrival      = $as
    removal      = $rs
  }
}
ConvertTo-Json -InputObject @($out) -Depth 5 -Compress
"""


def parse_nodes(text):
    """PowerShell 那段 JSON → 节点列表。**纯函数**(只吃文本, 不碰真机器)。

    三样要挡住: 空输出、`null`、单节点(ConvertTo-Json 对单元素会**不套数组** —— 这条
    不挡就会在下游 `for n in nodes` 上把字典当序列拆)。
    """
    text = (text or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except ValueError:
        # 真机器上偶发: 前面混进了别的输出。取最后一个 JSON 值再试一次。
        i = min([j for j in (text.find("["), text.find("{")) if j >= 0] or [-1])
        if i < 0:
            return []
        try:
            data = json.loads(text[i:])
        except ValueError:
            return []
    if data is None:
        return []
    if isinstance(data, dict):
        data = [data]
    out = []
    for d in data:
        if not isinstance(d, dict):
            continue
        out.append({
            "instance_id": d.get("instance_id") or "",
            "present": bool(d.get("present")),
            "status": d.get("status") or "",
            "problem": int(d.get("problem") or 0),
            "problem_desc": d.get("problem_desc") or "",
            "klass": d.get("klass") or "",
            "friendly": d.get("friendly") or "",
            "arrival": d.get("arrival") or "",
            "removal": d.get("removal") or "",
        })
    return out


def devices(vid, timeout=30.0):
    """查这个 USB 厂商号(4 位十六进制, 如 `"1366"` = SEGGER)的全部节点, **含已移除的历史节点**。

    返回 `[node, …]`; 一个都没有 = `[]`。「这台机器上从没见过这种设备」与「枚举失败」是
    两件事 —— 后者抛 `PnPError`。

    ⚠ **非管理员会话常常读不到 Windows 的「幽灵节点」(已移除的历史设备)** —— 2026-09-17
    实测: 同一台机器、同一支探针, 非提权时只回在位的那一个, 提权后才看得见整条历史。
    所以「没看到历史节点」**不等于**「这台机器从没接过这种设备」; 判读时别把两者混为一谈。
    """
    vid = str(vid).strip().upper().replace("VID_", "")
    if len(vid) != 4 or any(c not in "0123456789ABCDEF" for c in vid):
        raise PnPError("VID 要 4 位十六进制(如 '1366'), 收到 %r" % vid)
    return parse_nodes(_ps(_QUERY.replace("@VID@", vid), timeout=timeout))


def problems(timeout=30.0):
    """**当前在位**且 Windows 报了问题(Problem != 0)的 USB 节点。

    Code 43 那类"设备描述符请求失败"的节点在这里现形 —— 它常常就是掉下来的那个东西
    回来时报的名。
    """
    return problems_of(all_usb_nodes(timeout=timeout))


_ALL_USB = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'SilentlyContinue'
$out = @()
foreach ($d in Get-PnpDevice -PresentOnly) {
  if ($d.InstanceId -notlike 'USB\*') { continue }
  $out += [pscustomobject]@{
    instance_id = [string]$d.InstanceId
    present     = $true
    status      = [string]$d.Status
    problem     = [int]$d.Problem
    problem_desc= [string]$d.ProblemDescription
    klass       = [string]$d.Class
    friendly    = [string]$d.FriendlyName
    arrival     = ''
    removal     = ''
  }
}
ConvertTo-Json -InputObject @($out) -Depth 5 -Compress
"""


def all_usb_nodes(timeout=30.0):
    """**当前在位**的全部 USB 节点(不筛 VID)。"""
    return parse_nodes(_ps(_ALL_USB, timeout=timeout))


def problems_of(nodes):
    return [n for n in nodes if n["present"] and n["problem"] != 0]


# ---------------------------------------------------------------------------
# 二、判读
# ---------------------------------------------------------------------------
def _ts(s):
    """ISO 时间串 → datetime; 解析不了给 None。

    ⚠ Windows 的属性是 **7 位小数秒**(`…T09:11:51.0000000+08:00`), 而 `fromisoformat`
    在 3.11 之前只吃 3 位或 6 位 —— 直接喂会抛。这里先把它截到 6 位。
    """
    if not s:
        return None
    t = s.strip()
    if "." in t:
        head, rest = t.split(".", 1)
        digits = ""
        for ch in rest:
            if ch.isdigit():
                digits += ch
            else:
                break
        t = head + "." + (digits + "000000")[:6] + rest[len(digits):]
    try:
        return datetime.datetime.fromisoformat(t)
    except ValueError:
        return None


def dropped_then_returned(nodes, window=300.0):
    """指纹判据: 有没有"某节点移除之后 `window` 秒内上来一个 Problem != 0 的节点"。

    命中 ⇒ 返回 `{"removal": 串, "arrival": 串, "node": 那个坏节点}`; 没有 ⇒ None。
    这条判据的用处只有一个: 把「一定是你没插好」这句顺口的错话挡在判定外 —— 实测那次
    探针是自己掉的, 线一直插着。
    """
    worst = None
    for n in nodes:
        t = _ts(n["removal"])
        if t is None:
            continue
        if worst is None or t > worst[0]:
            worst = (t, n)
    if worst is None:
        return None
    for n in nodes:
        if n is worst[1] or not n["present"] or n["problem"] == 0:
            continue
        t = _ts(n["arrival"])
        if t is None:
            continue
        dt = (t - worst[0]).total_seconds()
        if 0 <= dt <= window:
            return {"removal": worst[1]["removal"], "arrival": n["arrival"],
                    "node": n, "seconds": dt}
    return None


def summarize(nodes):
    """节点列表 → 一份给人看/给判据吃的小结。纯函数。"""
    present = [n for n in nodes if n["present"]]
    absent = [n for n in nodes if not n["present"]]
    bad = [n for n in present if n["problem"] != 0]
    last_rm, last_ar = None, None
    for n in nodes:
        for key, cur in (("removal", last_rm), ("arrival", last_ar)):
            t = _ts(n[key])
            if t is None:
                continue
            if cur is None or t > cur:
                if key == "removal":
                    last_rm = t
                else:
                    last_ar = t
    return {"total": len(nodes), "present": present, "absent": absent,
            "bad_present": bad,
            "last_arrival": last_ar, "last_removal": last_rm,
            "returned_bad": dropped_then_returned(nodes)}


def describe_node(n):
    """一个节点 → 一行。"""
    if n["present"]:
        line = "在位"
        if n["problem"]:
            line += "·问题%d(%s)" % (n["problem"], n["problem_desc"] or n["status"] or "?")
    else:
        line = "不在位"
    bits = [line, n["instance_id"] or "?"]
    if n["friendly"]:
        bits.append(n["friendly"])
    if n["arrival"]:
        bits.append("到 %s" % n["arrival"])
    if n["removal"]:
        bits.append("移除 %s" % n["removal"])
    return " | ".join(bits)


# ---------------------------------------------------------------------------
# 三、动作(要管理员)
# ---------------------------------------------------------------------------
def is_elevated():
    """当前进程是不是管理员。拿不到答案时按 False 算(保守: 会去走提权那条路)。"""
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _elevated(body, timeout=180.0):
    """把 body 写成一枚 .ps1 提权跑。返回 `(rc, 文本)`。

    `@@OUT@@` 会被替换成一个临时文件路径 —— 提权起的那个进程与父进程**不共享 stdout**,
    所以它的输出得先落到文件里再读回来。

    已经管理员就直接跑(不再弹一次 UAC); 否则用 `Start-Process -Verb RunAs` 请一次 UAC。
    **用户取消 UAC 是正常结果**, 不是崩溃 —— 那样 rc 非 0、文本里写明。
    """
    _check_platform()
    d = tempfile.mkdtemp(prefix="winpnp_")
    inner = os.path.join(d, "do.ps1")
    outer = os.path.join(d, "run.ps1")
    out = os.path.join(d, "out.txt")
    with open(inner, "w", encoding="utf-8", newline="\n") as f:
        f.write("[Console]::OutputEncoding = [Text.Encoding]::UTF8\n")
        f.write(body.replace("@@OUT@@", out))
        if not body.endswith("\n"):
            f.write("\n")
    try:
        if is_elevated():
            r = subprocess.run(_PS + ["-File", inner], capture_output=True,
                               encoding="utf-8", errors="replace", timeout=timeout)
            rc, text = r.returncode, (r.stdout or "") + (r.stderr or "")
        else:
            with open(outer, "w", encoding="utf-8", newline="\n") as f:
                f.write("$p = Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait "
                        "-PassThru -ArgumentList '-NoProfile','-NonInteractive',"
                        "'-ExecutionPolicy','Bypass','-File','%s'\n" % inner)
                f.write("exit $p.ExitCode\n")
            r = subprocess.run(_PS + ["-File", outer], capture_output=True,
                               encoding="utf-8", errors="replace", timeout=timeout)
            rc = r.returncode
            text = (r.stdout or "") + (r.stderr or "")
            if "canceled by the user" in text or "取消" in text:
                text = "UAC 被取消(没有管理员权限做不了这一步)\n" + text
    except subprocess.TimeoutExpired:
        return 1, "超时(%ds) —— 提权那一步没回来" % int(timeout)
    finally:
        try:
            if os.path.isfile(out):
                with open(out, encoding="utf-8", errors="replace") as f:
                    filetext = f.read()
                if filetext.strip():
                    text = filetext + ("\n" + text if text.strip() else "")
        except Exception:
            pass
    return rc, (text or "").strip()


def _pnputil(args, timeout=180.0):
    body = ("$log = & pnputil.exe %s 2>&1 | Out-String\n"
            "$rc = $LASTEXITCODE\n"
            '"RC=$rc`n$log" | Out-File -FilePath \'@@OUT@@\' -Encoding utf8\n'
            "exit $rc\n") % args
    return _elevated(body, timeout=timeout)


def scan_devices(timeout=120.0):
    """让 Windows 重新扫一遍设备(等价于"重新枚举")。返回 `(ok, 文本)`。"""
    rc, text = _pnputil("/scan-devices", timeout=timeout)
    return rc == 0, text


def restart_device(instance_id, timeout=180.0):
    """重启一个设备节点(不拔插即重新初始化那一支)。返回 `(ok, 文本)`。"""
    rc, text = _pnputil("/restart-device '%s'" % instance_id, timeout=timeout)
    return rc == 0, text


def cycle_device(instance_id, timeout=240.0):
    """**禁用 + 启用**一个设备节点。

    这一样专治"枚举层活着、通信层坏了"(SEGGER 自己的 `ShowEmuList` 列得出探针, 而
    `connect` 报 `Out of sync, resynchronizing... FAILED: Cannot connect to J-Link`)。
    2026-09-17 实测: `/restart-device` 报成功、节点 Status 回到 OK, 而通信层照旧不通;
    只有 disable + enable 才修好。

    ⚠ **判成功看节点回来没有, 不看 pnputil 的退出码**。2026-09-21 实踩: 两步都报成功, 而节点
      停在 Code 22("This device is disabled") —— 探针从此枚举不到, 本函数却回的是"成功"。
      也就是说这一步能把探针**从可用推成不可用**, 还报喜。判据换成"节点自己活过来没有"之后,
      这种半途失败一定报失败。
    """
    rc1, t1 = _pnputil("/disable-device '%s'" % instance_id, timeout=timeout // 2)
    rc2, t2 = _pnputil("/enable-device '%s'" % instance_id, timeout=timeout // 2)
    vid = (instance_id.upper().split("VID_", 1)[1][:4]
           if "VID_" in instance_id.upper() else "")
    target = None
    if vid:
        try:
            wanted = instance_id.strip().upper()
            target = next((n for n in devices(vid)
                           if (n.get("instance_id") or "").strip().upper() == wanted), None)
        except Exception:
            target = None
    # 只能由**被操作的那一个 instance_id** 判定成功；同 VID 的另一台设备正常
    # 不能抵销本设备仍为 Code 22/不在位。
    alive = bool(target and target.get("present") and target.get("problem") == 0)
    ok = bool(alive)
    return bool(rc1 == 0 and rc2 == 0 and ok), ("[禁用] rc=%s\n%s\n[启用] rc=%s\n%s\n[复核] 节点%s%s"
                % (rc1, t1.strip(), rc2, t2.strip(),
                   "已回来(present 且 problem=0)" if alive
                   else "**没回来** —— 这一步没成, 探针现在多半是「被禁用/看不见」",
                   "" if is_elevated() else "; 本进程非管理员, 这类动作要一次 UAC"))


# ---------------------------------------------------------------------------
# 四、判据的**分辨力**从哪来(2026-09-18 注)
# ---------------------------------------------------------------------------
# 这里原先有一份 `FIXTURES` —— 逐字录自 2026-09-17 那次探针掉线当场的 `Get-PnpDevice`
# 输出, 当离线自检的夹具用。**已随自检层一起删除**(本仓禁止非实物测试)。
#
# ⚠ 但那件事的结论留下来, 因为它决定了 `dropped_then_returned()` 为什么要看两件事:
#   当场的关键**不在**"探针不见了", 而在**第二项** —— 用户的线一直插着, 而设备树说的是
#   "探针自己从总线上移除, 12 秒后一个读不出描述符的东西在同一根集线器上出现"。
#   只判"在不在位"会漏掉这一幕; 而**喂假数据一律判对, 等于没测**。
#   要复核这条判据, 就照着现场真跑一次: 拔探针, 隔十几秒插回, 再看 `summarize()`。
