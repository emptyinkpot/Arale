# -*- coding: utf-8 -*-
"""把 MSYS2 的 gdb-multiarch(**带 Python scripting**) 按依赖闭包抓到本地(一次性脚本)。

**为什么要它**: ARM 官方那份 `arm-none-eabi-gdb` 的 manifest 明写 `--with-python=no`
(`python print(1)` 直接答 "Python scripting is not supported in this copy of GDB"), 于是
`swdbg.breakpoint._install_hook_stop`(gdb 侧喂狗钩子)**装不上**, 只能退到"python 侧每次停住补喂"
—— 按 `common/judge.py` 的口径那是**降级**(台面缺了本该有的能力), 会把整项压成"未定论"。
2026-09-11 换成这份, 钩子装上、停止事件里直写 `IWDT->SERV` 已实测触发。

**为什么不用 pacman / 不装整个 MSYS2**: 我们只要 `gdb-multiarch.exe` 和它那一圈 DLL —— MSYS2 的
包就是普通 tarball, 把闭包解成一棵 `mingw64/` 树即可自洽(gdb 按 `../share/gdb` 找数据目录)。

**装完必须过验收**:
    <DEST>\\mingw64\\bin\\gdb-multiarch.exe --configuration   # 要看到 --enable-targets=all 与 --with-python
    <DEST>\\mingw64\\bin\\gdb-multiarch.exe --batch -ex "python print('OK')"
⚠ 取 `gdb-multiarch.exe`(31MB) **不是**同目录的 `gdb.exe`(10.5MB, 只有 x86 宿主目标 —— 载 ARM 的
  `.out` 后架构停在 `i386`, `set architecture arm` 报 Undefined)。两者同名同目录, 极易拿错。
⚠ 换 gdb ⇒ 调试链要**重新验收一遍**(本脚本的验收三连 + 真接 J-Link 跑一次 4-6)。
  2026-09-18 起,**换 gdb 只能靠真跑验收** —— 原来"重录一盘带子让自检对得上"那条路,
  已连同整套机制从本仓删除(见 `CLAUDE.md`「只跑实物」)。
"""
import io
import os
import re
import subprocess
import sys
import tarfile
import urllib.request

import zstandard

MIRROR = "https://mirrors.tuna.tsinghua.edu.cn/msys2/mingw/mingw64"
DB = MIRROR + "/mingw64.db.tar.zst"
ROOT = "mingw-w64-x86_64-gdb-multiarch"



def _dest_from_cartridge():
    """装到哪 = **装机卡带声明的 gdb 在哪**的那个根(从 `GDB` 路径反推)。

    为什么反推而不是在卡带里再声明一个 `GDB_ROOT`: 装的位置与找的位置**必须是同一件事** ——
    分成两个字段就总有一天对不上, 而那种对不上的现象是"装完了 breakpoint 还是找不到 gdb",
    查起来要绕一圈。反推让它结构上不可能分岔。
    """
    from common import machspec
    g = machspec.require("GDB")[0]                    # 缺了当场抛: 装到哪无从得知, 别猜
    # …\gdb-multiarch\mingw64\bin\gdb-multiarch.exe → …\gdb-multiarch
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(g))))


DEST = _dest_from_cartridge()


def fetch(url, timeout=300):
    return urllib.request.urlopen(url, timeout=timeout).read()


def load_db():
    raw = fetch(DB, timeout=120)
    data = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(raw)).read()
    tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:")
    pkgs = {}
    for m in tf.getmembers():
        if not m.name.endswith("/desc"):
            continue
        txt = tf.extractfile(m).read().decode("utf-8", "replace")
        cur, d = None, {}
        for line in txt.splitlines():
            if re.match(r"^%[A-Z]+%$", line):
                cur = line.strip("%")
                d.setdefault(cur, [])
            elif cur and line.strip():
                d[cur].append(line.strip())
        name = (d.get("NAME") or [None])[0]
        if name:
            d["_dir"] = m.name.split("/")[0]
            pkgs[name] = d
    return pkgs


def resolve(pkgs, roots):
    """依赖闭包(按名字; `PROVIDES` 也当名字索引一份)。"""
    provides = {}
    for n, d in pkgs.items():
        provides[n] = n
        for p in d.get("PROVIDES", []):
            provides.setdefault(p.split("=")[0], n)
    need, seen = list(roots), set()
    while need:
        n = need.pop()
        n = provides.get(n, n)
        if n in seen or n not in pkgs:
            if n not in pkgs and n not in seen:
                print("   (跳过未知依赖 %s)" % n)
            seen.add(n)
            continue
        seen.add(n)
        for dep in pkgs[n].get("DEPENDS", []):
            need.append(dep.split("=")[0].split(">")[0].split("<")[0])
    return sorted(seen)


def main():
    print("[1/3] 读 MSYS2 包索引 ...")
    pkgs = load_db()
    print("      索引 %d 个包" % len(pkgs))
    if ROOT not in pkgs:
        print("!! 索引里没有 %s" % ROOT)
        return 1
    print("[2/3] 解析依赖闭包 ...")
    names = resolve(pkgs, [ROOT])
    for n in names:
        print("      - %-42s %s" % (n, pkgs[n]["VERSION"][0]))
    print("[3/3] 下载并解开到 %s ..." % DEST)
    os.makedirs(DEST, exist_ok=True)
    total = 0
    for n in names:
        d = pkgs[n]
        fn = d["FILENAME"][0]
        url = "%s/%s" % (MIRROR, fn)
        blob = fetch(url)
        total += len(blob)
        print("      %-52s %8.1f MB" % (fn, len(blob) / 1e6))
        plain = zstandard.ZstdDecompressor().stream_reader(
            io.BytesIO(blob)).read()          # tarfile 不认 zst, 先解出来
        with tarfile.open(fileobj=io.BytesIO(plain), mode="r:") as tf:
            for m in tf.getmembers():
                if m.name.startswith("mingw64/"):
                    tf.extract(m, DEST, filter="data")
    print("      合计 %.1f MB" % (total / 1e6))
    # ⚠ 验收的是 **gdb-multiarch.exe**, 不是同目录的 gdb.exe(只有 x86 宿主目标, 载不了 ARM 的 .out)。
    gdb = os.path.join(DEST, "mingw64", "bin", "gdb-multiarch.exe")
    ok = os.path.exists(gdb)
    print("      gdb: %s  %s" % (gdb, "存在" if ok else "**缺失**"))
    if ok:
        conf = subprocess.run([gdb, "--configuration"], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=60).stdout.decode("utf-8", "replace")
        for flag in ("--enable-targets=all", "--with-python"):
            print("      验收 %-22s %s" % (flag, "OK" if flag in conf else "**缺**"))
        py = subprocess.run([gdb, "--batch", "-ex", "python print('PYTHON-OK')"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=60).stdout.decode("utf-8", "replace")
        print("      验收 python 可跑            %s" % ("OK" if "PYTHON-OK" in py else "**不行**"))
        print("      ⇒ 接着真接 J-Link 跑一次 4-6, 验收换 gdb 之后这条链没坏")
    return 0


if __name__ == "__main__":
    # 入口参数守卫: 本脚本**一个开关都没有**。不守的话 `--dry` 这类拼错的开关会被静默忽略,
    # 人以为在预演, 它已经在下 65MB 的包了(同族事故见 CLAUDE.md「基础设施检查 ①」)。
    if len(sys.argv) > 1:
        print("本脚本不接受任何参数(收到: %s)" % " ".join(sys.argv[1:]))
        raise SystemExit(2)
    raise SystemExit(main())
