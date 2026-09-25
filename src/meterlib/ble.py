# -*- coding: utf-8 -*-
"""
meterlib/ble.py —— 蓝牙透传通道: 找模组 / 连上 / 发帧 / 收帧

**一条通道一个文件。** 本文件里只有"怎么把字节送进表、怎么把字节取回来"。

装的是什么
----------
  · 模组事实: `MODULE_ADDR` / `MODULE_NAME` / `SVC` / `CHR_WRITE` / `CHR_NOTIFY`
  · `BleLink` —— 与 `common/portsel.RawCom` **同形**的传输对象
    (`reset_input_buffer` / `write` / `flush` / `read` / `close`)
  · `scan()` 找广播 / `open_link()` 连上并返回 `BleLink`

为什么 `BleLink` 要长成 `RawCom` 那个形状
----------------------------------------
`common/portsel.tx_recv(ser, frame, …)` 只用到那五个方法。形状对齐 ⇒ 上层
(`cmd_bank.send_frame`、各 `_test_*.py`)一行都不用改就能跑在蓝牙上。不对齐就得在每一处
分叉"串口这样、蓝牙那样" —— 那是两条收帧逻辑, 迟早对不上。

⚠ 与串口不同的两条(实测, 不是设计选择)
  · 模组**空闲会掉线**: 连着不发, 60 s 内被断开(`Not connected`)。所以 `write` 之前先
    `_ensure_connected()` 现连。
  · `read` 无阻塞 —— 同 `RawCom`: 有数据立即给, 没数据给空串(`tx_recv` 本来就是轮询)。

不在这里的东西(有意, 不是漏)
--------------------------
  · 698 / 645 的组帧解码          -> `meterlib/p698.py` / `meterlib/p645.py`
  · 串口对象 / 选口 / 探活        -> `common/portsel.py`
  · 读写怎么判过 / 记录怎么落库    -> `meterlib/cmd_bank.py`

本通路的边界(2026-09-21 台面实测, 别照着想象写)
---------------------------------------------
  · 主机 -> 表: **通**。往 `CHR_WRITE` 写一条**裸 698 帧**(不加壳、不加 FE 前缀) ——
    模组自己补 `FE FE FE FE` 与 `Blue_645` 外壳再交给管理芯。停在 `DLT698Link.c:283`
    读到的入参 `pFrame` 逐字节等于发出的那一帧, `port = 4`(`PT_BLE_M`)。
    ⚠ **不要自己组蓝牙壳**: 组了就是"壳里套壳" —— 管理芯从壳里掏出来交给 698 解析器的
      是你的壳, 解析必失败, 且没有任何应答(实测 47 字节壳帧就是这个下场: 解析口收到
      `len=47` 的壳帧)。
  · 表 -> 主机: **未打通**。管理芯把应答帧完整组好了(`TaskBluet.c:306` 命中 `Datalen=38`;
    `g_BLEMBuff` 里是带校验和与结束符的完整外套帧), 但 `CHR_NOTIFY` 一条通知都没有。
    所以本模块现在**只承诺发得出去**, 不承诺收得回来 —— 收的那一半在打通之前,
    任何"读回来了什么"的结论都不成立。

命令(在 帧收发基础/ 下)
----------------------
    python -m meterlib.ble scan            找广播, 认模组
    python -m meterlib.ble send <十六进制>  连上、发这一帧、看收回了什么
"""
import asyncio
import threading

MODULE_ADDR = "C0:00:00:00:00:01"          # 模组的静态地址(台面实测)
MODULE_NAME = "BT2603_Meter"               # 设备名(0x2A00); 厂商(0x2A29) = DynCloud
# ⚠ 服务基址是 Nordic UART Service 的**仿制品**: 正版末段是 `…e50e24dc` `ca9e`,
#   这颗是 `…e50e24dc4179`。抄错是这类透传模组的通病 —— 照抄台面上读到的, 别改成"正版"。
SVC = "6e400001-b5a3-f393-e0a9-e50e24dc4179"
CHR_WRITE = "6e400002-b5a3-f393-e0a9-e50e24dc4179"    # 主机写这里 → 模组的串口出
CHR_NOTIFY = "6e400003-b5a3-f393-e0a9-e50e24dc4179"   # 模组串口收的 → 主机在这里等通知


def _bleak():
    """延迟取 bleak, 且取不到时**说清怎么装** —— 没装是"这台没这个能力", 不是"蓝牙坏了"。

    延迟的理由: 本仓大部分脚本跑在串口上, 不该因为这台机器没装蓝牙库就连 import 都进不来
    (与 `p698` 的"没有画像也 import 得进来"同一条边界)。
    """
    try:
        from bleak import BleakClient, BleakScanner
    except ImportError:
        raise RuntimeError(
            "没装 bleak —— 蓝牙通路要它。装法:\n"
            "    python -m pip install bleak\n"
            "pypi 直连若被 TLS 拦(企业网常见 `SSL: UNEXPECTED_EOF_WHILE_READING`), 换镜像:\n"
            "    python -m pip install bleak -i https://pypi.tuna.tsinghua.edu.cn/simple")
    return BleakClient, BleakScanner


def scan(timeout=8.0):
    """扫 BLE 广播 → [(地址, 名字, rssi)]。**不筛选** —— 认模组是人看着挑的事。

    留这个函数是因为"扫不到"有三种原因(模组不在电 / 不在范围 / 扫描器本身没收到事件),
    它们必须能被分开: 先把周围**所有**广播源列出来, "一个都没有"才算扫描器的问题。
    """
    _, BleakScanner = _bleak()
    seen = {}

    async def go():
        def cb(dev, adv):
            seen[dev.address] = (dev.address, adv.local_name or dev.name, adv.rssi)

        sc = BleakScanner(detection_callback=cb)
        await sc.start()
        await asyncio.sleep(timeout)
        await sc.stop()

    asyncio.run(go())
    return sorted(seen.values(), key=lambda r: -(r[2] or -999))


def open_link(addr=MODULE_ADDR, connect_timeout=20.0):
    """连上模组 → `BleLink`。形状对齐 `portsel.open_com`, 便于两处同写法。"""
    return BleLink(addr=addr, connect_timeout=connect_timeout)


class BleLink:
    """一条蓝牙透传链路。与 `common/portsel.RawCom` 同形(见模块头)。

    bleak 是 asyncio 的, 本仓是阻塞式的 —— 所以这里起一个**后台事件循环线程**, 把
    `write`/`read` 让给主线程按串口的节奏用。⚠ 线程只跑 I/O, 不碰任何 gdb/串口资源。
    """

    def __init__(self, addr=MODULE_ADDR, connect_timeout=20.0):
        BleakClient, BleakScanner = _bleak()
        self._BleakClient = BleakClient
        self._BleakScanner = BleakScanner
        self.addr = addr
        self.connect_timeout = connect_timeout
        self.port = addr            # 与 RawCom 同名同义: 日志里"这一轮开的是什么口"那一格
        self._client = None
        self._rx = bytearray()
        self._lock = threading.Lock()
        self._closed = False
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._ensure_connected()

    # ---- 事件循环桥 ----------------------------------------------------------
    def _submit(self, coro, timeout=None):
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(self.connect_timeout if timeout is None else timeout)

    def _on_notify(self, _ch, data):
        """通知回调, 跑在事件循环线程上 ⇒ 必须加锁(主线程同时在 `read`)。"""
        if data:
            with self._lock:
                self._rx += bytes(data)

    async def _connect(self):
        dev = await self._BleakScanner.find_device_by_address(self.addr,
                                                              timeout=self.connect_timeout)
        if dev is None:
            raise RuntimeError(
                "%s 在 %.0f s 内没广播 —— 模组不在电/不在范围(不是'连不上', 是'没看见')"
                % (self.addr, self.connect_timeout))
        c = self._BleakClient(dev, timeout=25.0)
        await c.connect()
        await c.start_notify(CHR_NOTIFY, self._on_notify)
        return c

    def _ensure_connected(self):
        """掉了就现连。模组空闲会主动断, 所以这件事**每次 `write` 之前都要做**。"""
        if self._closed:
            raise RuntimeError("BleLink 已关闭")
        if self._client is not None and self._client.is_connected:
            return
        if self._client is not None:
            try:
                self._submit(self._client.disconnect(), timeout=5.0)
            except Exception:
                pass
            self._client = None
        self._client = self._submit(self._connect())

    # ---- 与 RawCom 同形的五个方法 --------------------------------------------
    def write(self, b):
        self._ensure_connected()
        b = bytes(b)
        self._submit(self._client.write_gatt_char(CHR_WRITE, b, response=False))
        return len(b)

    def read(self, n=1):
        with self._lock:
            if not self._rx:
                return b""
            take = bytes(self._rx[:n])
            del self._rx[:n]
            return take

    def flush(self):
        pass                        # 无缓冲要冲: `write` 已经把整帧交给 bleak 了

    def reset_input_buffer(self):
        with self._lock:
            self._rx.clear()

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            if self._client is not None:
                self._submit(self._client.disconnect(), timeout=5.0)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5.0)
        self._loop.close()


# =====================================================================================
# 命令行 —— 两件事: 认模组 / 跑一次真实的往返
# =====================================================================================
def main(argv=None):
    import sys

    from common.portsel import tx_recv

    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("scan", "send"):
        print("用法:\n    python -m meterlib.ble scan [秒数]\n"
              "    python -m meterlib.ble send <十六进制帧> [等秒]")
        return 2

    if argv[0] == "scan":
        sec = float(argv[1]) if len(argv) > 1 else 8.0
        rows = scan(sec)
        print("扫到 %d 个广播源:" % len(rows))
        for addr, name, rssi in rows:
            mark = " ← 本表模组(%s)" % MODULE_NAME if addr == MODULE_ADDR else ""
            print("    %s  rssi=%s  name=%r%s" % (addr, rssi, name, mark))
        return 0 if any(r[0] == MODULE_ADDR for r in rows) else 1

    frame = bytes.fromhex(argv[1].replace(" ", ""))
    wait = float(argv[2]) if len(argv) > 2 else 3.0
    link = open_link()
    try:
        rx = tx_recv(link, frame, wait=wait, tag="ble", what="蓝牙往返")
    finally:
        link.close()
    print("收回 %d 字节: %s" % (len(rx), rx.hex(" ").upper() if rx else "(无)"))
    return 0 if rx else 1


if __name__ == "__main__":
    raise SystemExit(main())
