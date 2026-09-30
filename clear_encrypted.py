#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用文件清空工具。

扫描指定目录, 把**文件开头匹配指定加密标记**的文件原地清空为 0 字节。

特点:
- 不限制文件扩展名 —— .txt/.py/.md/.jsonl/.docx/.xlsx/.pdf/.zip/.bin/.out 一视同仁
- 保留原文件名、扩展名、路径与文件本身, 只截断内容
- 默认先读文件头, 只有匹配加密标记才处理
- --force 才是不检查标记、对扫描范围内所有非空普通文件执行清空
- 支持 --dry-run 预览、--magic 自定义标记
- 跳过符号链接(文件与目录), 跳过脚本自身与日志文件
- 生成处理日志

注意:
**本程序无法仅凭文件内容可靠判断"文件是否加密"** —— 必须给一个识别条件,
最简单的就是文件头。默认用 `%TSD-Header-###%`, 可用 --magic 换成别的。

原地清空 = 不可恢复。密文一旦被截断就永久没了。

用法:
    python clear_encrypted.py                       # 扫描脚本所在目录
    python clear_encrypted.py --dry-run             # 只预览, 不落盘
    python clear_encrypted.py --root "E:\\Downloads"
    python clear_encrypted.py --magic "MY-ENCRYPTED-FILE-V1"
    python clear_encrypted.py --force               # 不检查标记, 清空所有非空普通文件(谨慎)
    python clear_encrypted.py --log "clear.log"
"""

import argparse
import os
import sys
from datetime import datetime

# 默认加密文件头, 可用 --magic 覆盖
DEFAULT_MAGIC = b"%TSD-Header-###%"

STATUS_SUCCESS = "成功"
STATUS_SKIP = "跳过"
STATUS_FAIL = "失败"


def human_size(size):
    """字节数 → 可读形式。"""
    if size == 0:
        return "0B"
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return "{}B".format(int(value))
            return "{:.2f}{}".format(value, unit)
        value /= 1024
    return "{}B".format(size)


def normalize_magic(value):
    """命令行传入的标记 → bytes(默认 UTF-8)。"""
    if isinstance(value, bytes):
        return value
    return value.encode("utf-8")


def is_same_path(path1, path2):
    """两个路径是否指向同一处(归一大小写 / 相对 / 符号链接之后比较)。"""
    return (os.path.normcase(os.path.abspath(os.path.realpath(path1)))
            == os.path.normcase(os.path.abspath(os.path.realpath(path2))))


def scan(root, magic, dry_run=False, force=False, protected_paths=None):
    """扫描 root → (records, stats)。records 每条是 (状态, 路径, 说明)。"""
    records = []
    stats = {STATUS_SUCCESS: 0, STATUS_SKIP: 0, STATUS_FAIL: 0}
    protected_paths = protected_paths or set()
    magic_size = len(magic)

    def note(status, path, why):
        records.append((status, path, why))
        stats[status] += 1

    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        dirnames.sort()          # 排序只为日志稳定
        filenames.sort()

        # 不进入符号链接目录
        real_dirs = []
        for dirname in dirnames:
            dpath = os.path.join(dirpath, dirname)
            if os.path.islink(dpath):
                note(STATUS_SKIP, dpath, "符号链接目录，不处理")
                continue
            real_dirs.append(dirname)
        dirnames[:] = real_dirs

        for name in filenames:
            path = os.path.join(dirpath, name)

            # 1. 保护文件(脚本自身 / 日志)
            if any(is_same_path(path, p) for p in protected_paths):
                note(STATUS_SKIP, path, "保护文件，不处理")
                continue

            # 2. 符号链接不处理(免得误伤链接目标)
            if os.path.islink(path):
                note(STATUS_SKIP, path, "符号链接，不处理")
                continue

            # 3. 文件信息
            try:
                size = os.path.getsize(path)
            except OSError as exc:
                note(STATUS_SKIP, path, "无法读取文件大小：{}".format(exc))
                continue

            if size == 0:
                note(STATUS_SKIP, path, "文件已经为空")
                continue

            # 4. force: 不查标记
            if force:
                matched, reason = True, "强制模式"
            else:
                if size < magic_size:
                    note(STATUS_SKIP, path, "文件小于加密标记长度")
                    continue
                # 5. 读文件头
                try:
                    with open(path, "rb") as f:
                        head = f.read(magic_size)
                except OSError as exc:
                    note(STATUS_SKIP, path, "无法读取文件：{}".format(exc))
                    continue
                matched = head == magic
                reason = "匹配加密标记" if matched else "未匹配加密标记"

            # 6. 不匹配 → 原样保留
            if not matched:
                note(STATUS_SKIP, path, "未匹配加密标记，保持原样")
                continue

            # 7. dry-run: 只记不改
            if dry_run:
                note(STATUS_SUCCESS, path,
                     "[预览] {} -> 0B ({})".format(human_size(size), reason))
                continue

            # 8. 原地截断('wb' 直接截为 0 字节, 文件名/扩展名不变)
            try:
                with open(path, "wb"):
                    pass
                after = os.path.getsize(path)
                if after != 0:
                    note(STATUS_FAIL, path,
                         "清空后大小仍为 {}".format(human_size(after)))
                else:
                    note(STATUS_SUCCESS, path,
                         "{} -> 0B ({})".format(human_size(size), reason))
            except OSError as exc:
                note(STATUS_FAIL, path, "清空失败：{}".format(exc))

    return records, stats


def write_log(records, stats, root, log_path, dry_run, magic, force):
    lines = []
    lines.append("=" * 100)
    lines.append("通用文件清空工具")
    lines.append("=" * 100)
    lines.append("扫描根目录: {}".format(root))
    lines.append("时间: {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    lines.append("模式: {}".format("预览（未落盘）" if dry_run else "执行"))
    lines.append("匹配方式: {}".format(
        "强制模式，不检查文件头" if force else "文件头 == {}".format(repr(magic))))
    lines.append("处理规则: 匹配条件的文件原地清空为 0 字节; 未匹配文件保持原样; 符号链接跳过")
    lines.append("=" * 100)
    lines.append("")

    for status, path, note_ in records:
        lines.append("[{}] {}  ({})".format(status, path, note_))

    lines.append("")
    lines.append("-" * 100)
    lines.append("合计: 成功 {} | 跳过 {} | 失败 {} | 总计 {}".format(
        stats[STATUS_SUCCESS], stats[STATUS_SKIP], stats[STATUS_FAIL], len(records)))
    lines.append("-" * 100)

    text = "\n".join(lines) + "\n"
    with open(log_path, "w", encoding="utf-8", errors="replace") as f:
        f.write(text)
    return text


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        description="通用文件清空工具：匹配指定文件头的文件原地清空为 0 字节。")
    parser.add_argument("--root", default=os.path.dirname(os.path.abspath(__file__)),
                        help="扫描根目录，默认脚本所在目录")
    parser.add_argument("--magic", default=DEFAULT_MAGIC.decode("utf-8"),
                        help="加密文件头标记，默认：{}".format(DEFAULT_MAGIC.decode("utf-8")))
    parser.add_argument("--dry-run", action="store_true", help="只预览，不实际修改文件")
    parser.add_argument("--force", action="store_true",
                        help="不检查加密标记，对扫描范围内的普通文件全部执行清空。")
    parser.add_argument("--log", default=None, help="日志文件路径，默认写入扫描根目录")
    args = parser.parse_args()

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print("根目录不存在: {}".format(root))
        return 1

    try:
        magic = normalize_magic(args.magic)
    except Exception as exc:
        print("加密标记无法转换为字节：{}".format(exc))
        return 1

    if not magic and not args.force:
        print("错误：未指定加密标记。请使用 --magic，或者使用 --force。")
        return 1

    log_path = args.log or os.path.join(
        root, "clear_encrypted_log_{}.txt".format(datetime.now().strftime("%Y%m%d_%H%M%S")))
    log_path = os.path.abspath(log_path)

    # 保护: 脚本自身 + 日志文件 —— 防止 --force 时把工具自己或日志截断
    protected_paths = {
        os.path.abspath(os.path.realpath(__file__)),
        os.path.abspath(os.path.realpath(log_path)),
    }

    records, stats = scan(root=root, magic=magic, dry_run=args.dry_run,
                          force=args.force, protected_paths=protected_paths)
    text = write_log(records, stats, root, log_path, args.dry_run, magic, args.force)

    print(text, end="")
    print("日志已写入: {}".format(log_path))

    return 0 if stats[STATUS_FAIL] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
