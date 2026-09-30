# -*- coding: utf-8 -*-
"""全局字体 DPI 策略（只管 hinting，不改字号 / 布局）。

背景
----
Qt 6 在 Windows 上按显示器 DPR 缩放整个 UI，QSS / QFont 里的 ``px`` 是逻辑像素，
逻辑 DPI 恒为 96，所以 ``13px`` 与 ``9.75pt`` 渲染结果完全一致 —— 字号单位本身
不是问题，项目继续统一使用逻辑 px。

问题出在 hinting：DirectWrite 下 ``PreferDefaultHinting`` 对 ≤16 逻辑 px 的字
实际落成 *vertical hinting*，即把每个字形的横向笔画 / 顶边各自吸附到物理像素网格。
DPR 为整数时，逻辑坐标 → 物理坐标仍是整数网格，吸附结果一致；DPR 为小数
（125% / 150% / 175%）时字号落在非整数 ppem（13px × 1.5 = 19.5ppem），
逐字吸附会把设计上齐平的汉字顶边分到相邻两行像素，表现为个别汉字顶部差 1px。

策略
----
- 整数 DPR（100% / 200%）：保持系统默认 hinting（笔画最锐利，与原来一致）；
- 小数 DPR：``PreferNoHinting``，按字形轮廓原样栅格化，顶边 / 基线全局一致。

hinting 不改变 QFontMetrics（行高、ascent、字宽逐项实测一致），因此不会引起
任何裁切或布局位移。策略由窗口当前所在屏幕的 DPR 决定，跨屏 / 系统缩放改变时
自动重新下发到所有控件。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QWidget

from .theme import BASE_FONT_PX

# setStyleSheet() 会把这些类的平台类字体重置回系统默认（9pt + 默认 hinting），
# 只设 app 字体覆盖不到它们（表格 / 表头 / 列表），必须按类名再下发一次。
_CLASS_FONT_OVERRIDES = ("QAbstractItemView",)


def is_integer_dpr(dpr: float) -> bool:
    return abs(float(dpr) - round(float(dpr))) < 1e-3


def hinting_for_dpr(dpr: float) -> QFont.HintingPreference:
    """整数 DPR 保持系统默认 hinting；小数 DPR 关闭 hinting。"""
    return QFont.PreferDefaultHinting if is_integer_dpr(dpr) else QFont.PreferNoHinting


def make_app_font(family: str, dpr: float) -> QFont:
    """应用基础字体：字号与 QSS ``*`` 规则同源（BASE_FONT_PX 逻辑 px）。"""
    font = QFont(family) if family else QFont()
    font.setPixelSize(BASE_FONT_PX)
    font.setHintingPreference(hinting_for_dpr(dpr))
    return font


def install_app_font(app: QApplication, font: QFont, qss: str) -> None:
    """下发应用字体并设置样式表。顺序固定：字体 → QSS → 类字体。

    - 先 setFont：随后的 setStyleSheet 触发全局 repolish，已 polish 控件的 QSS
      字体以**新的**应用字体为基准重新解析，hinting 随之更新；
    - setStyleSheet 会把 QAbstractItemView 等类字体重置回平台默认，所以类字体
      必须在它之后再补一次；
    - 自绘控件的 ``_font()`` 基于 ``QFont()``（= 应用字体），下一次 paintEvent 自动跟随。
    """
    app.setFont(font)
    app.setStyleSheet(qss)
    for class_name in _CLASS_FONT_OVERRIDES:
        app.setFont(font, class_name)


def apply_font_policy(app: QApplication, dpr: float) -> bool:
    """按 DPR 更新全局 hinting；只有策略真的变化时才动手，返回是否发生了变化。"""
    want = hinting_for_dpr(dpr)
    current = app.font()
    if current.hintingPreference() == want:
        return False
    font = QFont(current)
    font.setHintingPreference(want)
    install_app_font(app, font, app.styleSheet())
    return True


class FontDpiWatcher(QObject):
    """挂在顶层窗口上：首次显示、跨屏移动、所在屏幕缩放改变时同步字体策略。"""

    _EVENTS = (QEvent.Show, QEvent.DevicePixelRatioChange, QEvent.ScreenChangeInternal)

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self._window = window
        # 事件里不直接 repolish：合并到下一轮事件循环，避免在 DPR 变更过程中重入。
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self.sync)
        window.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self._window and event.type() in self._EVENTS:
            self._timer.start()
        return False

    def sync(self) -> Optional[bool]:
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return None
        return apply_font_policy(app, self._window.devicePixelRatioF())
