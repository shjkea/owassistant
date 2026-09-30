# -*- coding: utf-8 -*-
"""Windows 原生深色标题栏。

只调用 Windows 自带的 DWM 接口给窗口打上「深色模式」属性，让系统自己把标题栏
画成深底浅字。**不改 frameless、不自绘拖动、不自绘最小化 / 最大化 / 关闭按钮**，
因此系统的窗口拖动、贴边、最大化、DPI 缩放行为完全保持原生。

非 Windows 平台 / 旧版本 Windows 上调用会安全地什么也不做。
"""
from __future__ import annotations

import ctypes
import sys
from typing import Optional

from PySide6.QtWidgets import QWidget

# DWMWINDOWATTRIBUTE：20 是 Win10 20H1+ 的正式值，19 是 Win10 1809 的预览值。
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1 = 19


def is_supported() -> bool:
    return sys.platform == "win32"


def apply_dark_titlebar(widget: QWidget) -> bool:
    """给窗口打开系统深色标题栏。返回是否成功应用。

    需要窗口已经有原生句柄（winId() 非 0），所以一般在 ``show()`` 之后调用，
    或者在 ``showEvent`` 里调用。重复调用是安全的。
    """
    if not is_supported() or widget is None:
        return False
    try:
        hwnd = int(widget.winId())
    except (RuntimeError, TypeError, ValueError):
        return False
    if not hwnd:
        return False
    try:
        dwm = ctypes.windll.dwmapi  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False
    value = ctypes.c_int(1)
    handle = ctypes.c_void_p(hwnd)
    for attribute in (
        DWMWA_USE_IMMERSIVE_DARK_MODE,
        DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1,
    ):
        try:
            result = dwm.DwmSetWindowAttribute(
                handle,
                ctypes.c_uint(attribute),
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
        except Exception:
            continue
        if result == 0:
            return True
    return False


def apply_dark_titlebar_later(widget: QWidget, delay_ms: int = 0) -> None:
    """延后到事件循环里应用，便于在 __init__ 阶段调用。"""
    from PySide6.QtCore import QTimer

    QTimer.singleShot(max(0, int(delay_ms)), lambda: apply_dark_titlebar(widget))


def titlebar_state(widget: QWidget) -> Optional[bool]:
    """诊断用：返回 None 表示平台不支持，否则返回是否已成功应用。"""
    if not is_supported():
        return None
    return apply_dark_titlebar(widget)


__all__ = [
    "DWMWA_USE_IMMERSIVE_DARK_MODE",
    "apply_dark_titlebar",
    "apply_dark_titlebar_later",
    "is_supported",
    "titlebar_state",
]
