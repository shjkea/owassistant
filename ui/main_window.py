# -*- coding: utf-8 -*-
"""主窗口：左侧导航 + 国服战绩、竞逐预测、竞逐历史、规则设置四个功能页。

应用整体名称是「守望先锋 国服助手」；「竞逐」只是其中一个功能模块，
所以窗口标题 / 品牌区都不再使用「竞逐计算器」作为程序名。
"""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from owstats import OWStatsService

from .pages.bnet_accounts import BattleNetPage, create_backend
from .pages.history import HistoryPage
from .pages.owstats import OWStatsPage
from .pages.prediction import PredictionPage
from .pages.settings import SettingsPage
from .font_dpi import FontDpiWatcher
from .state import AppState
from .win_titlebar import apply_dark_titlebar

APP_NAME = "守望先锋 国服助手"
APP_SUBTITLE = "OW ASSISTANT"
# 应用版本号唯一来源；侧栏品牌区等 UI 统一从这里读取。
APP_VERSION = "0.9.0"

# 导航顺序固定：国服战绩 → 账号切换 → 竞逐预测 → 竞逐历史 → 规则设置。
# 「竞逐历史」记录的是竞逐预测 / 竞逐计算相关历史，不是 OW 对局历史。
NAV_ITEMS = (
    ("国服战绩", "大神授权、BattleTag 与最近对局"),
    ("账号切换", "Battle.net 账号保存与切换"),
    ("竞逐预测", "推算节点进度与完成概率"),
    ("竞逐历史", "批量录入并逐局回放竞逐记录"),
    ("规则设置", "节点、加减分规则与大神授权"),
)

PAGE_DRIVE_HISTORY = 3


class MainWindow(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Root")
        self.setWindowTitle(APP_NAME)
        # Windows 兜底：直接沿用 build_app() 设置的应用图标，不再维护第二套路径。
        app_icon = QApplication.windowIcon()
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)
        self.setMinimumSize(1040, 680)
        self.resize(1280, 800)
        # 跨 DPI 显示器移动 / 系统缩放变化时同步全局字体 hinting 策略。
        self._font_dpi_watcher = FontDpiWatcher(self)

        self.state = AppState(self)
        self.stats_service = OWStatsService()
        # Battle.net 切号后端：整个应用只持有这一个实例，页面切换不会重复创建。
        self.bnet_backend, bnet_error = create_backend()
        self._settings = QSettings("ow_drive_split", "drive_calculator")

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_sidebar(), 0)

        self.stack = QStackedWidget()
        self.stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.prediction_page = PredictionPage(self.state)
        self.history_page = HistoryPage(self.state)
        self.owstats_page = OWStatsPage(self.state, self.stats_service)
        self.settings_page = SettingsPage(self.state, self.stats_service)
        self.bnet_page = BattleNetPage(self.bnet_backend, bnet_error)
        # 页面加入顺序必须与 NAV_ITEMS 一致。
        self.stack.addWidget(self.owstats_page)
        self.stack.addWidget(self.bnet_page)
        self.stack.addWidget(self.prediction_page)
        self.stack.addWidget(self.history_page)
        self.stack.addWidget(self.settings_page)

        self.owstats_page.requestHistoryPage.connect(lambda: self._select(PAGE_DRIVE_HISTORY))
        self.owstats_page.authChanged.connect(self.settings_page.refresh_auth_status)
        self.settings_page.authChanged.connect(self.owstats_page.refresh_auth_status)
        # 当前赛季最近最多 20 场（竞技 + 快速合并）胜率 → 竞逐计算器默认胜率。
        # PredictionPage 自己判断是否已被用户手动修改，避免覆盖用户输入。
        self.owstats_page.recentWinRateReady.connect(self.prediction_page.apply_recent_win_rate)
        # 清理缓存后同步丢弃战绩页的内存缓存，保证下次重新获取。
        self.settings_page.cacheCleared.connect(self._on_cache_cleared)
        root.addWidget(self.stack, 1)

        self._nav_group.idClicked.connect(self._go)
        self._nav_buttons[0].setChecked(True)
        self._go(0)

        for i in range(len(NAV_ITEMS)):
            sc = QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self)
            sc.activated.connect(lambda idx=i: self._select(idx))

        self.state.historyChanged.connect(self._update_footer)
        self._update_footer()
        self._restore_geometry()
        # Windows 原生 DWM 深色标题栏。窗口仍是普通有边框窗口，
        # 系统按钮 / 拖动 / 最大化 / DPI 行为全部保持原生。
        apply_dark_titlebar(self)

    # ---------------- 侧边栏 ----------------
    def _build_sidebar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("Sidebar")
        # 120px 文字侧栏，保留完整品牌与四项导航。
        bar.setFixedWidth(120)
        lay = QVBoxLayout(bar)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        brand = QWidget()
        brand_lay = QVBoxLayout(brand)
        brand_lay.setContentsMargins(8, 16, 8, 14)
        brand_lay.setSpacing(2)
        # 中文主标题 + 版本号同一行；版本号底部对齐到标题第二行，视觉上弱于主标题。
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)
        title = QLabel("守望先锋\n国服助手")
        title.setObjectName("BrandTitle")
        title_row.addWidget(title, 0, Qt.AlignBottom)
        version = QLabel(f"v{APP_VERSION}")
        version.setObjectName("BrandVersion")
        title_row.addWidget(version, 0, Qt.AlignBottom)
        title_row.addStretch(1)
        brand_lay.addLayout(title_row)
        sub = QLabel(APP_SUBTITLE)
        sub.setObjectName("BrandSub")
        brand_lay.addWidget(sub)
        lay.addWidget(brand)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        # 导航项用 QPushButton：QToolButton 不响应 QSS 的 text-align，
        # 文字只会在按钮内居中，无法做出「整体偏左 + 左侧留白」的排版。
        self._nav_buttons: List[QPushButton] = []
        for i, (text, tip) in enumerate(NAV_ITEMS):
            btn = QPushButton()
            btn.setObjectName("NavButton")
            # 左内边距交给 QSS（padding-left: 18px），文本本身不再塞空格缩进。
            btn.setText(text)
            btn.setToolTip(f"{tip}（Ctrl+{i + 1}）")
            btn.setCheckable(True)
            btn.setAutoDefault(False)
            btn.setDefault(False)
            btn.setFlat(True)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self._nav_group.addButton(btn, i)
            self._nav_buttons.append(btn)
            lay.addWidget(btn)

        lay.addStretch(1)

        self.footer = QLabel("")
        self.footer.setObjectName("BrandSub")
        self.footer.setWordWrap(True)
        self.footer.setContentsMargins(12, 10, 10, 14)
        lay.addWidget(self.footer)
        return bar

    def _select(self, index: int) -> None:
        if 0 <= index < len(self._nav_buttons):
            self._nav_buttons[index].setChecked(True)
            self._go(index)

    def _go(self, index: int) -> None:
        self.stack.setCurrentIndex(index)

    def _on_cache_cleared(self, kind: str) -> None:
        """磁盘缓存被清理后同步内存缓存；已打开页面的数据不受影响。"""
        self.owstats_page.drop_runtime_caches(str(kind or "all"))

    def _update_footer(self) -> None:
        summary = self.state.history.get("summary", {})
        games = int(summary.get("games", 0))
        if games:
            self.footer.setText(
                f"当前 {self.state.current_score} 分\n{games} 场 · 胜率 "
                f"{summary.get('win_rate_percent', 0.0):.1f}%"
            )
        else:
            self.footer.setText(f"尚未录入战绩\nCtrl+1~{len(NAV_ITEMS)} 切换页面")

    # ---------------- 窗口几何 ----------------
    def _restore_geometry(self) -> None:
        geo = self._settings.value("geometry")
        if geo is not None:
            try:
                self.restoreGeometry(geo)
            except Exception:
                pass

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # 真正拿到原生句柄之后再补一次，保证首次显示时标题栏就是深色。
        apply_dark_titlebar(self)

    def closeEvent(self, event) -> None:  # noqa: N802
        # 首次登录流程未完成 / Battle.net 操作进行中时，由战网账号页决定能否退出。
        if not self.bnet_page.can_close(self.close):
            event.ignore()
            return
        self._settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(event)
