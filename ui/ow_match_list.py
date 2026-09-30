# -*- coding: utf-8 -*-
"""比赛列表：懒加载 + 卡片回收。

三赛季可能有上千场，禁止启动时一次实例化全部 MatchCard。
这里首屏只建 ``FIRST_BATCH`` 张卡，滚动接近底部时再追加 ``NEXT_BATCH`` 张；
筛选或赛季切换时复用已创建的卡片控件，只重设数据，避免反复 new/delete。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QMenu,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .assets import AssetLibrary
from .ow_widgets import MatchCard


class MatchList(QWidget):
    """可滚动的 MatchCard 列表。"""

    FIRST_BATCH = 24
    NEXT_BATCH = 24
    # 距底部还有这么多像素就开始加载下一批，滚动体感上不会看到空白。
    PREFETCH_PX = 560

    matchOpened = Signal(str)
    driveStartRequested = Signal(str)
    driveEndRequested = Signal(str)
    driveClearRequested = Signal()

    def __init__(self, library: AssetLibrary, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._library = library
        self._views: List[Dict[str, Any]] = []
        self._cards: List[MatchCard] = []
        self._rendered = 0
        self._mark_resolver: Optional[Callable[[str], str]] = None
        self._context_enabled = False
        # 所有卡片右侧信息容器（模式图标列 → 比分 → 时间 → ID）共用同一个宽度，
        # 由页面按「导入竞逐计算器」按钮左边缘算出来，保证图标严格成一列。
        self._right_block_width = MatchCard.RIGHT_BLOCK_WIDTH

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        root.addWidget(self.scroll, 1)

        self._holder = QWidget()
        self._holder.setObjectName("Root")
        self._holder_layout = QVBoxLayout(self._holder)
        self._holder_layout.setContentsMargins(0, 0, 6, 8)
        self._holder_layout.setSpacing(6)
        self._holder_layout.addStretch(1)
        self.scroll.setWidget(self._holder)

        self.empty = QLabel("暂无比赛")
        self.empty.setObjectName("EmptyHint")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setMinimumHeight(140)
        self._holder_layout.insertWidget(0, self.empty)

        self.footer = QLabel("")
        self.footer.setObjectName("ListFooter")
        self.footer.setAlignment(Qt.AlignCenter)
        self.footer.setVisible(False)
        root.addWidget(self.footer, 0)

        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scrolled)
        self._pending = QTimer(self)
        self._pending.setSingleShot(True)
        self._pending.setInterval(0)
        self._pending.timeout.connect(self._maybe_load_more)

    # ---------------- 配置 ----------------
    def set_mark_resolver(self, resolver: Optional[Callable[[str], str]]) -> None:
        self._mark_resolver = resolver

    def set_right_block_width(self, width: int) -> None:
        """统一设置卡片右侧信息容器宽度（=模式图标列左边缘到卡片右缘的距离）。"""
        value = max(MatchCard.RIGHT_BLOCK_MIN, min(int(width), MatchCard.RIGHT_BLOCK_MAX))
        if value == self._right_block_width:
            return
        self._right_block_width = value
        for card in self._cards:
            card.set_right_block_width(value)

    def right_block_width(self) -> int:
        return self._right_block_width

    def set_context_menu_enabled(self, enabled: bool) -> None:
        self._context_enabled = bool(enabled)

    def scroll_value(self) -> int:
        return int(self.scroll.verticalScrollBar().value())

    def restore_scroll(self, value: int) -> None:
        bar = self.scroll.verticalScrollBar()
        QTimer.singleShot(0, lambda: bar.setValue(min(int(value), bar.maximum())))

    # ---------------- 数据 ----------------
    def set_matches(self, views: Sequence[Dict[str, Any]], *, keep_scroll: bool = False) -> None:
        previous = self.scroll_value() if keep_scroll else 0
        self._views = list(views)
        self._rendered = 0
        for card in self._cards:
            card.setVisible(False)
        self.empty.setVisible(not self._views)
        if self._views:
            self._render_batch(self.FIRST_BATCH)
        self._update_footer()
        if keep_scroll:
            self.restore_scroll(previous)
        else:
            self.scroll.verticalScrollBar().setValue(0)
        self._pending.start()

    def refresh_marks(self) -> None:
        if self._mark_resolver is None:
            return
        for index in range(self._rendered):
            card = self._cards[index]
            card.set_drive_mark(self._mark_resolver(card.match_id()))

    def rendered_count(self) -> int:
        return self._rendered

    def total_count(self) -> int:
        return len(self._views)

    # ---------------- 渲染 ----------------
    def _card_at(self, index: int) -> MatchCard:
        while index >= len(self._cards):
            card = MatchCard(self._library, self._holder)
            card.set_right_block_width(self._right_block_width)
            card.clicked.connect(self.matchOpened.emit)
            card.setContextMenuPolicy(Qt.CustomContextMenu)
            card.customContextMenuRequested.connect(
                lambda pos, c=card: self._show_menu(c, pos)
            )
            # 插在 stretch 之前，保证卡片自上而下排列。
            self._holder_layout.insertWidget(self._holder_layout.count() - 1, card)
            self._cards.append(card)
        return self._cards[index]

    def _render_batch(self, count: int) -> None:
        end = min(len(self._views), self._rendered + max(1, int(count)))
        for index in range(self._rendered, end):
            card = self._card_at(index)
            mark = self._mark_resolver(str(self._views[index].get("match_id") or "")) if self._mark_resolver else ""
            card.set_match(self._views[index], mark)
            card.setVisible(True)
        self._rendered = end
        self._update_footer()

    def _content_height(self) -> int:
        """按卡片固定高度估算内容高度。

        不能用 self._holder.height()：页面处于非当前 QStackedWidget 页时布局尚未生效，
        真实高度会长期偏小，导致判断失真。
        """
        spacing = max(0, self._holder_layout.spacing())
        return self._rendered * (MatchCard.CARD_HEIGHT + spacing)

    def _fill_needed(self) -> bool:
        """视口还没被填满时才继续渲染。

        这是唯一的“自动”增长路径，用内容高度和视口高度比较，天然有上界。
        滚动条 maximum 在布局未生效时是过期值，绝不能作为自动增长依据，
        否则首屏就会把上千场全部实例化。
        """
        if self._rendered >= len(self._views):
            return False
        viewport = self.scroll.viewport().height()
        return self._content_height() < max(viewport, 0) + self.PREFETCH_PX

    def _maybe_load_more(self) -> None:
        if not self._fill_needed():
            return
        self._render_batch(self.NEXT_BATCH)
        if self._fill_needed():
            self._pending.start()

    def _on_scrolled(self) -> None:
        """用户滚动接近底部时追加下一批。此时滚动条数值已经可信。"""
        if self._rendered >= len(self._views):
            return
        bar = self.scroll.verticalScrollBar()
        if bar.maximum() > 0 and bar.maximum() - bar.value() <= self.PREFETCH_PX:
            self._render_batch(self.NEXT_BATCH)
            self._pending.start()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        # 首次切换到本页 / 窗口放大后视口变高，需要补齐首屏。
        self._pending.start()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._pending.start()

    def _update_footer(self) -> None:
        total = len(self._views)
        if not total:
            self.footer.setVisible(False)
            return
        if self._rendered >= total:
            self.footer.setText(f"已显示全部 {total} 场")
        else:
            self.footer.setText(f"已显示 {self._rendered} / {total} 场 · 继续滚动加载更多")
        self.footer.setVisible(True)

    # ---------------- 右键：竞逐起止点 ----------------
    def _show_menu(self, card: MatchCard, pos) -> None:
        if not self._context_enabled:
            return
        match_id = card.match_id()
        if not match_id:
            return
        menu = QMenu(self)
        menu.addAction("设为竞逐起点", lambda: self.driveStartRequested.emit(match_id))
        menu.addAction("设为竞逐终点", lambda: self.driveEndRequested.emit(match_id))
        menu.addSeparator()
        menu.addAction("清除竞逐区间", self.driveClearRequested.emit)
        menu.exec(card.mapToGlobal(pos))


__all__ = ["MatchList"]
