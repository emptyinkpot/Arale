# -*- coding: utf-8 -*-
"""
common/jsonc.py —— 读带注释的 JSON(**JSONC**, 就是 VS Code 的 `.vscode/launch.json` 那种)。

为什么值得单开一个中立层模块
----------------------------
`.vscode/launch.json` 里写着 gdb 路径 / J-Link SN / 器件型号 —— 它与装机卡带是**同一批事实**的
两份落笔, 所以两个包都要读它来核对: `machine/env_check.py` 核机器字段(serverpath/gdbPath/SN…),
`project/env_check.py` 核 `executable` 是不是就是画像的 `.out`。**两边必须按同一套规则解析**,
否则"核过了"这件事本身就不可信 —— 故实现只有这一份。

⚠ 不能图省事 `re.sub(r"//.*", "", text)`: 值里带 `//` 的字符串(路径/URL)会被从中间截断,
  把一条**合法**的配置切成语法坏的 JSON, 报错还指向别处。这里按"在不在字符串里"逐字符走。
⚠ 只处理 `//` 行注释与 `/* */` 块注释, **不处理尾逗号** —— 本仓的 launch.json 没有尾逗号,
  真遇到了让 json 库照常报错(那才是诚实的行为: 别把"藏起来的坏语法"猜成好语法)。

`canon()` 也住这儿: 读出来是为了**跟卡带的声明比**, 而"两个路径算不算同一个"必须有**唯一**的
判据 —— 两个 env_check 各写一份规则的话, 它们会在边界情形上给出不同答案, 而"核过了"这件事
本身就不可信了。
"""
import json
import os

__all__ = ["strip_comments", "loads", "load", "canon"]


def canon(v):
    """字段值归一: **大小写 / 斜杠方向 / 尾分隔符都不算差异**, 供"声明的值 == JSONC 里的值"比较。

    `os.path.normcase` 在 Windows 上顺带转小写 —— 于是 `"SWD"` 与 `"swd"`、`E:/a/b` 与
    `e:\\a\\b\\` 都能对上, 不必给每个字段配一套开关。非路径值(SN / 器件名)一并走这条,
    归一结果一样是确定且可比的。
    """
    return os.path.normcase(os.path.normpath(str(v))).strip()


def strip_comments(text):
    """把 `//` 与 `/* */` 注释换成等长空格(**保留换行与列号**, 这样 json 报错的行列还指得准)。"""
    out = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:      # 转义: 后一个字符原样吃进来(可能正是个引号)
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):            # 保住换行, 否则行列全错位
                out.append("\n" if text[k] == "\n" else " ")
            i = j
            continue
        out.append(c)
        i += 1
    return "".join(out)


def loads(text):
    """JSONC 文本 → 对象。"""
    return json.loads(strip_comments(text))


def load(path, encoding="utf-8"):
    """JSONC 文件 → 对象。"""
    with open(path, encoding=encoding) as f:
        return loads(f.read())


if __name__ == "__main__":
    import sys
    _bad = []
    # 注释剥掉、字符串里的 // 留住、换行列号不变 —— 三条都得成立, 否则"核过了"不可信。
    _t = '{\n  // 行注释\n  "a": 1, /* 块\n  注释 */\n  "u": "http://x/y",\n  "e": "a\\"//b"\n}'
    _d = loads(_t)
    _bad.append(("值里的 // 不被当注释", _d["u"] == "http://x/y" and _d["e"] == 'a"//b'))
    _bad.append(("注释真的没了", _d["a"] == 1))
    _bad.append(("行数不变(报错行列才指得准)", _t.count("\n") == strip_comments(_t).count("\n")))
    _bad.append(("不带注释的 JSON 原样通过", loads('{"a": [1, 2]}') == {"a": [1, 2]}))
    _bad.append(("canon: 斜杠方向/大小写不算差异",
                 canon("E:/a/B") == canon(r"e:\a\b")))
    _bad.append(("canon: 尾分隔符不算差异(Windows 上)",
                 canon("E:/a/b") == canon("E:/a/b/")))
    _bad.append(("canon: 真的不同还是不同", canon("E:/a/b") != canon("E:/a/c")))
    for _lbl, _ok in _bad:
        print("%-40s %s" % (_lbl, "OK" if _ok else "FAIL"))
    sys.exit(0 if all(o for _, o in _bad) else 1)
