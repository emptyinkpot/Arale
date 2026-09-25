# -*- coding: utf-8 -*-
"""common/hashfile.py —— 文件内容指纹(sha256)。**全仓唯一的一份实现。**

2026-09-10 自 `discover/elf.py` 提到中立层 —— 这是 `common/` 的**第六次**同款搬迁
(`elfsym`/`console`/`cli`/`profile`/`runlog` 之后), 判据一字不变: **零协议 / 零画像成分**。
一个"把文件读成十六进制摘要"的函数, 不属于探测、不属于串口、不属于任何一块表。

为什么非提不可(不是洁癖)
------------------------
原先只有 `discover.elf.sha256` 会算这个指纹。`project/env_check.py` 要校验
"meta 里声明的 `out_sha256` 是否等于盘上 `.out` 的实算值" —— 而 `project/` **不许 import
`discover`**(见 `env_check.py` 的边界断言: 卡带一旦依赖探测器, "探测器对任何表都能用"
这条立身之本就没了)。

于是只剩三条路: ① 在 `env_check` 里内联一份 `hashlib`; ② 给边界断言开个口子;
③ 提到中立层。**选③**, 理由是前两条都会留下**第二份指纹实现** —— 而那恰恰是这道判定最不能有的:

    报告 §0 钉的指纹, 与"探测前用来核对的指纹", 必须是**同一串字节算出来的**。
    两份实现哪天在分块大小 / 打开模式 / 编码上分了歧, 判定就会在报告记着另一个值的
    时候**放行** —— 而这道判定存在的全部意义, 就是证明"我探的确实是声明的那份固件"。

`discover/elf.py` 的 `sha256` 因此改成**委托**(一行), 保持 `D_elf.sha256(out)` 的
调用点与 `discover.__init__` 的再导出一字不改。
"""

__all__ = ["sha256"]


def sha256(path):
    """文件内容的 sha256 十六进制摘要(小写)。1 MiB 分块读, 不整份载入内存。

    `.out` 动辄 2.5 MB(APP 那份 2,547,323 B), 整份 `read()` 也无妨, 但分块是免费的好习惯,
    且**写法必须固定** —— 分块大小决定不了摘要(sha256 是流式的), 但"二进制读"决定得了:
    换文本模式 + 换行转换, 摘要当场变。所以这里写死 `"rb"`。
    """
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
