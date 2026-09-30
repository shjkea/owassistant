# -*- coding: utf-8 -*-
"""可复用的深色 UI 组件。纯表现层。"""
from __future__ import annotations

from typing import List, Optional, Sequence

from PySide6.QtCore import QMargins, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .theme import C


class FlowFrame(QFrame):
    """使用 FlowLayout 的容器。

    Qt 的 heightForWidth 在嵌套布局里传播不可靠，所以这里不依赖它：
    每次宽度变化后直接把自身高度固定为折行所需高度，父布局拿到的就是确定值。
    """

    def __init__(
        self,
        margins: tuple[int, int, int, int] = (12, 9, 12, 9),
        h_spacing: int = 8,
        v_spacing: int = 8,
        object_name: str = "SubCard",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self._syncing = False
        self.flow = FlowLayout(self, margins=margins, h_spacing=h_spacing, v_spacing=v_spacing)
        self.flow.on_invalidate = self._sync_height
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    def addWidget(self, widget: QWidget) -> None:  # noqa: N802
        self.flow.addWidget(widget)
        self._sync_height()

    def sizeHint(self) -> QSize:  # noqa: N802
        width = self.width() if self.width() > 0 else self.flow.sizeHint().width()
        return QSize(self.flow.sizeHint().width(), self.flow.heightForWidth(width))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        mn = self.flow.minimumSize()
        width = self.width() if self.width() > 0 else mn.width()
        return QSize(mn.width(), self.flow.heightForWidth(width))

    def _sync_height(self) -> None:
        if self._syncing:
            return
        width = self.width()
        if width <= 0:
            return
        needed = self.flow.heightForWidth(width)
        if needed == self.height():
            return
        self._syncing = True
        try:
            self.setFixedHeight(needed)
            # 高度变了必须让父布局重新排列，否则下方控件位置不更新会被覆盖。
            parent_layout = self.parentWidget().layout() if self.parentWidget() else None
            if parent_layout is not None:
                parent_layout.invalidate()
                parent_layout.activate()
        finally:
            self._syncing = False

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_height()


class FlowLayout(QLayout):
    """自动换行布局：窗口变窄时控件折到下一行，而不是被压扁。"""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        margins: tuple[int, int, int, int] = (0, 0, 0, 0),
        h_spacing: int = 8,
        v_spacing: int = 8,
    ) -> None:
        super().__init__(parent)
        self._items: List[QLayoutItem] = []
        self._h = h_spacing
        self._v = v_spacing
        self.on_invalidate = None  # 由 FlowFrame 注入，用于同步容器高度
        self.setContentsMargins(QMargins(*margins))

    def invalidate(self) -> None:
        super().invalidate()
        if callable(self.on_invalidate):
            self.on_invalidate()

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> Optional[QLayoutItem]:  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> Optional[QLayoutItem]:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientations:  # noqa: N802
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._layout(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        """一行排开时的理想尺寸。"""
        m = self.contentsMargins()
        width = 0
        height = 0
        for i, item in enumerate(self._items):
            hint = item.sizeHint()
            width += hint.width() + (self._h if i else 0)
            height = max(height, hint.height())
        return QSize(
            width + m.left() + m.right(),
            height + m.top() + m.bottom(),
        )

    def minimumSize(self) -> QSize:  # noqa: N802
        """最窄情况：每个控件独占一行。"""
        m = self.contentsMargins()
        width = 0
        height = 0
        for i, item in enumerate(self._items):
            mn = item.minimumSize()
            width = max(width, mn.width())
            height += mn.height() + (self._v if i else 0)
        return QSize(
            width + m.left() + m.right(),
            height + m.top() + m.bottom(),
        )

    def _layout(self, rect: QRect, apply: bool) -> int:
        m = self.contentsMargins()
        eff = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = eff.x()
        y = eff.y()
        line_height = 0

        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + self._h
            if next_x - self._h > eff.right() and line_height > 0:
                x = eff.x()
                y = y + line_height + self._v
                next_x = x + hint.width() + self._h
                line_height = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())

        return y + line_height - rect.y() + m.bottom()


def _refresh(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


class Card(QFrame):
    """带标题的圆角卡片容器，内容放进 self.body。"""

    def __init__(
        self,
        title: str = "",
        subtitle: str = "",
        parent: Optional[QWidget] = None,
        object_name: str = "Card",
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 16)
        outer.setSpacing(12)

        if title:
            head = QVBoxLayout()
            head.setSpacing(2)
            lbl = QLabel(title)
            lbl.setProperty("role", "section")
            head.addWidget(lbl)
            if subtitle:
                sub = QLabel(subtitle)
                sub.setProperty("role", "hint")
                sub.setWordWrap(True)
                head.addWidget(sub)
            outer.addLayout(head)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(10)
        outer.addLayout(self.body)


class SegmentedControl(QFrame):
    """分段选择（用于 单排/组排、普通/决斗）。"""

    changed = Signal(int)

    def __init__(self, options: Sequence[str], current: int = 0, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Segment")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(3)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: List[QToolButton] = []
        for i, text in enumerate(options):
            btn = QToolButton(self)
            btn.setObjectName("SegmentButton")
            btn.setText(text)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.setMinimumHeight(30)
            self._group.addButton(btn, i)
            lay.addWidget(btn)
            self._buttons.append(btn)

        if self._buttons:
            self._buttons[max(0, min(current, len(self._buttons) - 1))].setChecked(True)
        self._group.idClicked.connect(self.changed.emit)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setFixedHeight(38)

    def current_index(self) -> int:
        return self._group.checkedId()

    def set_current_index(self, index: int) -> None:
        if 0 <= index < len(self._buttons):
            self._buttons[index].setChecked(True)


class StatTile(QFrame):
    """指标块：小标题 + 大数字 + 可选单位/副文本。"""

    def __init__(
        self,
        caption: str,
        value: str = "-",
        unit: str = "",
        accent: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("StatCardAccent" if accent else "StatCard")
        self._accent = accent
        lay = QVBoxLayout(self)
        lay.setContentsMargins(13, 10, 13, 11)
        lay.setSpacing(3)

        self._caption = QLabel(caption)
        self._caption.setObjectName("StatCaption")
        lay.addWidget(self._caption)

        row = QHBoxLayout()
        row.setSpacing(5)
        row.setContentsMargins(0, 0, 0, 0)
        self._value = QLabel(value)
        self._value.setObjectName("StatValueAccent" if accent else "StatValue")
        self._value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(self._value)
        self._unit = QLabel(unit)
        self._unit.setObjectName("StatUnit")
        self._unit.setAlignment(Qt.AlignLeft | Qt.AlignBottom)
        row.addWidget(self._unit)
        row.addStretch(1)
        lay.addLayout(row)

        self._foot = QLabel("")
        self._foot.setObjectName("StatCaption")
        self._foot.setVisible(False)
        lay.addWidget(self._foot)

        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setMinimumWidth(120)

    def set_value(self, text: str, color: Optional[str] = None) -> None:
        self._value.setText(text)
        if color:
            self._value.setStyleSheet(f"color: {color};")
        else:
            self._value.setStyleSheet("")

    def set_unit(self, text: str) -> None:
        self._unit.setText(text)

    def set_caption(self, text: str) -> None:
        self._caption.setText(text)

    def set_footer(self, text: str) -> None:
        self._foot.setText(text)
        self._foot.setVisible(bool(text))


class Banner(QFrame):
    """轻量提示条，用来替代弹窗。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Banner")
        self.setProperty("level", "info")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(8)
        self._text = QLabel("")
        self._text.setObjectName("BannerText")
        self._text.setWordWrap(True)
        lay.addWidget(self._text, 1)
        self.setVisible(False)

    def show_message(self, text: str, level: str = "info") -> None:
        self._text.setText(text)
        self.setProperty("level", level)
        _refresh(self)
        self.setVisible(True)

    def clear(self) -> None:
        self._text.clear()
        self.setVisible(False)


class StatusLine(QLabel):
    """单行状态文本，成功/失败用颜色区分。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusBar")
        self.setWordWrap(True)
        self.setProperty("level", "info")

    def show_message(self, text: str, level: str = "info") -> None:
        self.setProperty("level", level)
        self.setText(text)
        _refresh(self)


def make_button(
    text: str,
    variant: str = "default",
    tooltip: str = "",
    parent: Optional[QWidget] = None,
) -> QPushButton:
    btn = QPushButton(text, parent)
    btn.setProperty("variant", variant)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setMinimumHeight(34)
    btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
    if tooltip:
        btn.setToolTip(tooltip)
    return btn


def field_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("role", "fieldLabel")
    return lbl


def hline() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    line.setFixedHeight(1)
    line.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return line


def result_color(is_win: bool) -> str:
    return C.WIN if is_win else C.LOSE
