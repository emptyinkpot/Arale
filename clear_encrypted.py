#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
清空被 %TSD-Header-###% 加密的文件内容，保留原文件名与扩展名不变。

处理规则（严格按需求）：
    .txt / .py / .md / .jsonl   -> 清空为 0 字节
    .docx / .xlsx / .pdf        -> 不处理（跳过并记录）
    其他扩展名 / 无扩展名       -> 跳过并记录

不做的事：不改名、不删除、不移动、不联网、不查进程、不查服务、不分析加密来源、
          不写测试文件。只做上面这些。

原地清空 = 不可恢复。密文一旦被截断就永久没了。

用法：
    python clear_encrypted.py                # 执行（只清空带加密头的文件）
    python clear_encrypted.py --dry-run      # 只预览，不落盘
    python clear_encrypted.py --force        # 不做加密头校验，命中扩展名就清空
    python clear_encrypted.py --root <目录>  # 指定扫描根目录
"""

import argparse
import os
import sys
from datetime import datetime

# ---- 按需求写死的规则，不要改 ----------------------------------------------

# 清空为 0 字节
CLEAR_EXTS = {".txt", ".py", ".md", ".jsonl"}

# 明确不处理的
IGNORE_EXTS = {".docx", ".xlsx", ".pdf"}

# 加密文件头
ENC_MAGIC = b"%TSD-Header-###%"

# ---------------------------------------------------------------------------

STATUS_SUCCESS = "成功"
STATUS_SKIP = "跳过"
STATUS_FAIL = "失败"


def human_size(n):
    return "0B" if n == 0 else "{}B".format(n)


def scan(root, dry_run=False, force=False):
    """遍历 root，返回 (记录列表, 统计)。每条记录是 (状态, 路径, 说明)。"""
    records = []
    stats = {STATUS_SUCCESS: 0, STATUS_SKIP: 0, STATUS_FAIL: 0}

    # 脚本自身和日志文件不参与处理（防止把自己清空）
    protected = {os.path.normcase(os.path.abspath(__file__))}

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()

        for name in filenames:
            path = os.path.join(dirpath, name)

            if os.path.normcase(os.path.abspath(path)) in protected:
                records.append((STATUS_SKIP, path, "脚本自身，不处理"))
                stats[STATUS_SKIP] += 1
                continue

            ext = os.path.splitext(name)[1].lower()

            # --- 不处理的格式 ---
            if ext in IGNORE_EXTS:
                records.append((STATUS_SKIP, path, "{} 不处理".format(ext)))
                stats[STATUS_SKIP] += 1
                continue

            # --- 只处理列出的这些扩展名，其他一律跳过 ---
            if ext not in CLEAR_EXTS:
                reason = "{} 不在处理范围".format(ext) if ext else "无扩展名，不在处理范围"
                records.append((STATUS_SKIP, path, reason))
                stats[STATUS_SKIP] += 1
                continue

            # --- 命中扩展名，开始检查 ---
            try:
                size = os.path.getsize(path)
                with open(path, "rb") as f:
                    head = f.read(len(ENC_MAGIC))
            except OSError as e:
                records.append((STATUS_SKIP, path, "无法读取：{}".format(e)))
                stats[STATUS_SKIP] += 1
                continue

            if size == 0:
                records.append((STATUS_SKIP, path, "已为空"))
                stats[STATUS_SKIP] += 1
                continue

            if not force and not head.startswith(ENC_MAGIC):
                records.append((STATUS_SKIP, path, "未加密，保持原样"))
                stats[STATUS_SKIP] += 1
                continue

            # --- 执行清空 ---
            if dry_run:
                records.append((STATUS_SUCCESS, path,
                                "[预览] {} -> 0B".format(human_size(size))))
                stats[STATUS_SUCCESS] += 1
                continue

            try:
                with open(path, "wb"):
                    pass  # 'wb' 直接截断为 0 字节，文件名/扩展名不变
                after = os.path.getsize(path)
                if after != 0:
                    records.append((STATUS_FAIL, path,
                                    "清空后大小仍为 {}".format(human_size(after))))
                    stats[STATUS_FAIL] += 1
                else:
                    records.append((STATUS_SUCCESS, path,
                                    "{} -> 0B".format(human_size(size))))
                    stats[STATUS_SUCCESS] += 1
            except OSError as e:
                records.append((STATUS_FAIL, path, "清空失败：{}".format(e)))
                stats[STATUS_FAIL] += 1
                continue

    return records, stats


def write_log(records, stats, root, log_path, dry_run):
    lines = []
    lines.append("=" * 100)
    lines.append("扫描根目录: {}".format(root))
    lines.append("时间: {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    lines.append("模式: {}".format("预览（未落盘）" if dry_run else "执行"))
    lines.append("规则: {} -> 清空为 0 字节 | {} -> 不处理 | 其他 -> 跳过".format(
        "/".join(sorted(CLEAR_EXTS)), "/".join(sorted(IGNORE_EXTS))))
    lines.append("=" * 100)
    lines.append("")

    for status, path, note in records:
        lines.append("[{}] {}  ({})".format(status, path, note))

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
        description="清空被加密的文件内容，保留文件名与扩展名。原地清空不可恢复。")
    parser.add_argument("--root", default=os.path.dirname(os.path.abspath(__file__)),
                        help="扫描根目录，默认脚本所在目录")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不落盘")
    parser.add_argument("--force", action="store_true",
                        help="不做加密头校验，命中扩展名就清空")
    parser.add_argument("--log", default=None, help="日志文件路径")
    args = parser.parse_args()

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print("根目录不存在: {}".format(root))
        return 1

    log_path = args.log or os.path.join(
        root, "clear_encrypted_log_{}.txt".format(datetime.now().strftime("%Y%m%d_%H%M%S")))

    records, stats = scan(root, dry_run=args.dry_run, force=args.force)
    text = write_log(records, stats, root, log_path, args.dry_run)

    print(text, end="")
    print("日志已写入: {}".format(log_path))

    return 0 if stats[STATUS_FAIL] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
