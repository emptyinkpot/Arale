# -*- coding: utf-8 -*-
"""Cross-process ownership for the single physical SWD probe.

The J-Link DLL and pyOCD both talk to the same USB device.  A Python
``threading.Lock`` cannot protect that device from another process, and a
second enumerator can leave the vendor DLL in an undefined state.  This
module therefore uses a small OS file lock.  The lock is released by the OS
when the owner process exits, including a crash; there is no stale PID file to
clean up and no process is ever killed to acquire it.
"""
from __future__ import print_function

import io
import os
import socket
import tempfile
import threading
import time


class ProbeBusyError(RuntimeError):
    """The physical probe is already owned by another process."""


_PATH = os.path.join(tempfile.gettempdir(), "mengxi-swd-probe.lock")
# 占用者信息写在**锁旁边的一个普通文件**里, 不写在锁文件里: Windows 的字节范围锁会拦住
# 别人读那一段(实测: 占用者持锁期间, 另一个进程读锁文件得到 PermissionError),
# 于是"被谁占着"这个信息恰恰在最需要它的时候读不出来。这个旁文件不上锁, 谁都能读。
_OWNER = _PATH + ".owner"
_LOCAL = threading.RLock()
_HELD = 0
_HANDLE = None


def lock_path():
    """Return the diagnostic path used for the OS-backed lock."""
    return _PATH


def holder():
    """锁文件里写的占用者(`pid=… host=… owner=… time=…`) → 一行文本; 读不到给空串。

    只用于**报信**: 锁由操作系统在占用者退出(哪怕崩溃)时释放, 这里没有任何"照它去清"的意思 ——
    清进程正是把探针从 USB 上打下去的动作。
    """
    for _ in range(3):
        try:
            with io.open(_OWNER, "r", encoding="utf-8", errors="replace") as f:
                return " ".join(f.read().split())
        except Exception:
            # 读它的时候占用者**正好在改写这一行**(写完就关)→ 让一步再来;
            # 三次都读不到就照"读不到"报, 不编。
            time.sleep(0.05)
    return ""


def _write_owner(text):
    """把占用者信息写到锁旁边那个**不上锁**的文件里(见 `_OWNER` 上面那段)。"""
    try:
        with io.open(_OWNER, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass                   # 写不进去只是"报不出占用者", 不该拦住拿锁


def _clear_owner():
    try:
        os.remove(_OWNER)
    except Exception:
        pass


def _try_lock(handle):
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(handle):
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass


class ProbeLease(object):
    """Re-entrant in-process handle around a cross-process file lock."""

    def __init__(self, owner, timeout=0.0):
        self.owner = str(owner or "unknown")
        self.timeout = float(timeout)
        self.acquired = False

    def acquire(self):
        global _HELD, _HANDLE
        with _LOCAL:
            if _HELD:
                _HELD += 1
                self.acquired = True
                return self
            deadline = time.monotonic() + max(0.0, self.timeout)
            while True:
                handle = io.open(_PATH, "a+", encoding="utf-8")
                if _try_lock(handle):
                    who = ("pid=%s host=%s owner=%s time=%.6f\n" %
                           (os.getpid(), socket.gethostname(), self.owner, time.time()))
                    handle.seek(0)
                    handle.truncate()
                    handle.write(who)
                    handle.flush()
                    _write_owner(who)
                    _HANDLE = handle
                    _HELD = 1
                    self.acquired = True
                    return self
                handle.close()
                if time.monotonic() >= deadline:
                    who = holder()
                    raise ProbeBusyError(
                        "SWD/J-Link 探针已被另一进程占用(锁: %s)%s；"
                        "不能并发枚举、连接或强杀占用者 —— 等它自己退, 或去问那个 pid 是谁"
                        % (_PATH, ("。占用者: " + who) if who else ""))
                time.sleep(min(0.05, max(0.001, deadline - time.monotonic())))

    def release(self):
        global _HELD, _HANDLE
        with _LOCAL:
            if not self.acquired:
                return
            self.acquired = False
            _HELD -= 1
            if _HELD:
                return
            handle, _HANDLE = _HANDLE, None
            if handle is not None:
                _unlock(handle)
                handle.close()
            _clear_owner()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False


def acquire(owner, timeout=0.0):
    return ProbeLease(owner, timeout=timeout).acquire()


__all__ = ["ProbeBusyError", "ProbeLease", "acquire", "lock_path"]
