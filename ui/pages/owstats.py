# -*- coding: utf-8 -*-
"""OW 国服战绩页。

布局结构（固定顶部 + 独立滚动列表）：

    ┌ 固定顶部（不滚动，必须完整显示） ─────────────┐
    │ 查询 / 刷新 / 授权                            │
    │ 玩家资料                                      │
    │ 竞技段位 · 当前赛季 + 四张紧凑段位卡            │
    │ 近期状态（最近 20 场 + 胜率 + 走势）           │
    │ 比赛筛选 + [导入竞逐计算器][清除区间]          │
    └───────────────────────────────────────────────┘
    ┌ 比赛历史列表（**只有这里纵向滚动**） ─────────┐
    │ MatchCard …                                   │
    └───────────────────────────────────────────────┘

整页 QScrollArea 已被移除：页面右侧不再出现整页纵向滚动条，顶部信息也不会跟着
比赛列表一起滚走。滚动只发生在 ``MatchList`` 内部的滚动容器里。

点击任意 MatchCard 进入同尺寸单局详情页（ui_reference/match_detail_reference.png）。

赛季号全部来自后端：``season_status()`` 与各查询结果的 ``current_season``。
本文件不含任何赛季号硬编码，赛季推进后界面自动跟随。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import QSettings, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from owstats import OWStatsService

from .. import owstats_data as D
from ..assets import AvatarCache, library as asset_library
from ..async_worker import submit
from ..dashen_auth import DashenAuthDialog
from ..detail_cache import MatchDetailCache
from ..ow_ai_review import AIReviewPage
from ..ow_detail import MatchDetailPage
from ..ow_drive_import import DriveImportDialog, DriveRangeBar, DriveSelection
from ..ow_match_list import MatchList
from ..ow_widgets import (
    ChipBar,
    PlayerHeader,
    RecentFormPanel,
    RoleRankCard,
    SeasonSelector,
    SectionHeader,
)
from ..state import AppState
from ..widgets import Banner, make_button

SEASON_FILTER_ALL = "all"
PRESET_ROLE_ALL = "all"


class _TransientBanner(Banner):
    """仅在加载或操作提示期间进入布局，清空后不保留控件高度。"""

    def __init__(self, layout) -> None:
        super().__init__(layout.parentWidget())
        self._host_layout = layout
        self._host_index = layout.count()

    def show_message(self, text: str, level: str = "info") -> None:
        if self._host_layout.indexOf(self) < 0:
            self._host_layout.insertWidget(self._host_index, self)
        super().show_message(text, level)

    def clear(self) -> None:
        super().clear()
        self._host_layout.removeWidget(self)


class OWStatsPage(QWidget):
    requestHistoryPage = Signal()
    authChanged = Signal()
    # 当前赛季最近最多 20 场（竞技 + 快速合并）的胜率百分比。
    # 没有比赛数据时不会发出，避免竞逐计算器被填成 0%。
    # 附带查询账号标识，便于下游区分「换了玩家」。
    recentWinRateReady = Signal(float, str)

    def __init__(self, state: AppState, service: OWStatsService, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Root")
        self._state = state
        self._service = service
        self._library = asset_library()
        self._avatars = AvatarCache(service.load_image, self)

        # 后端数据
        self._customer_token = ""
        self._player: Dict[str, Any] = {}
        self._current_season: Optional[int] = None
        self._seasons: List[int] = []
        self._season_roles: Dict[int, List[Dict[str, Any]]] = {}
        self._matches: List[Dict[str, Any]] = []
        self._match_index: Dict[str, Dict[str, Any]] = {}

        # UI 状态
        self._selected_season: Optional[int] = None
        self._mode_filter = "all"
        self._preset_role_filter = PRESET_ROLE_ALL
        self._season_filter: Any = SEASON_FILTER_ALL
        self._recent_form: Dict[str, Any] = {}
        self._history_cursor = None
        self._history_has_more = False
        self._query_busy = False
        self._detail_busy = False
        self._drive = DriveSelection()
        self._auth_dialog: Optional[DashenAuthDialog] = None
        self._list_scroll = 0
        self._detail_cache = MatchDetailCache()
        self._settings = QSettings("ow_drive_split", "drive_calculator")
        self._auto_loaded_bound_account = False
        # 当前打开的对局：(match_view, match_all_players_detail 结果)。AI 锐评直接复用。
        self._current_view: Dict[str, Any] = {}
        self._current_detail: Optional[Dict[str, Any]] = None

        self._build_ui()
        self.refresh_auth_status()
        self._refresh_season_ui()
        QTimer.singleShot(0, self._load_bound_account_if_available)

    # ------------------------------------------------------------------ 构建
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.stack = QStackedWidget(self)
        self.stack.addWidget(self._build_browser())
        self.detail_page = MatchDetailPage(self._library, self._avatars, self)
        self.detail_page.backRequested.connect(self._close_detail)
        self.detail_page.aiReviewRequested.connect(self.open_ai_review)
        self.stack.addWidget(self.detail_page)
        # AI 锐评页：属于某一局详情，与详情页同尺寸切换。
        self.ai_review_page = AIReviewPage(self._library, self)
        self.ai_review_page.backRequested.connect(lambda: self.stack.setCurrentIndex(1))
        self.stack.addWidget(self.ai_review_page)
        root.addWidget(self.stack, 1)

    def _build_browser(self) -> QWidget:
        page = QWidget()
        page.setObjectName("Root")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---------------- 固定顶部区（整体不滚动） ----------------
        # 顶部所有内容都用普通布局，绝不再包 QScrollArea：
        # 桌面常见尺寸下必须一次性完整显示，也不会跟比赛列表一起滚动。
        top = QWidget(page)
        top.setObjectName("Root")
        top.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        top_lay = QVBoxLayout(top)
        top_lay.setContentsMargins(18, 12, 18, 4)
        top_lay.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText("BattleTag，例如 玩家#12345")
        self.query_edit.returnPressed.connect(self.search)
        head.addWidget(self.query_edit, 1)
        self.search_btn = make_button("查询战绩", "primary")
        self.search_btn.clicked.connect(self.search)
        head.addWidget(self.search_btn, 0)
        self.refresh_btn = make_button("刷新", "ghost")
        self.refresh_btn.clicked.connect(self.search)
        head.addWidget(self.refresh_btn, 0)
        self.auth_status = QLabel("")
        self.auth_status.setProperty("role", "hint")
        head.addWidget(self.auth_status, 0)
        self.auth_btn = make_button("大神授权", "ghost")
        self.auth_btn.clicked.connect(self.open_auth_dialog)
        head.addWidget(self.auth_btn, 0)
        top_lay.addLayout(head)

        # Banner 只在有真正需要用户关注的信息时出现（默认隐藏，不占高度）。
        self.banner = _TransientBanner(top_lay)

        self.player_header = PlayerHeader(self._library, self._avatars, top)
        top_lay.addWidget(self.player_header)

        # 竞技段位：标题合并为「竞技段位 · 当前赛季」，四张卡自己区分职责。
        season_row = QHBoxLayout()
        season_row.setSpacing(10)
        self.season_header = SectionHeader("竞技段位")
        season_row.addWidget(self.season_header, 1)
        self.season_selector = SeasonSelector(top)
        self.season_selector.seasonChanged.connect(self._on_season_changed)
        season_row.addWidget(self.season_selector, 0)
        top_lay.addLayout(season_row)

        # 重装 / 输出 / 支援 / 开放职责四张等宽紧凑卡，正常桌面宽度下同一行。
        rank_row = QHBoxLayout()
        rank_row.setSpacing(8)
        self.role_cards: List[RoleRankCard] = []
        for _ in range(3):
            card = RoleRankCard(self._library, parent=top)
            self.role_cards.append(card)
            rank_row.addWidget(card, 1)
        self.open_card = RoleRankCard(self._library, parent=top)
        rank_row.addWidget(self.open_card, 1)
        self._rank_row = rank_row
        top_lay.addLayout(rank_row)

        # 近期状态：当前赛季最近最多 20 场（预设竞技 + 开放竞技 + 快速合并）
        self.recent_header = SectionHeader("近期状态")
        top_lay.addWidget(self.recent_header)
        self.recent_form = RecentFormPanel(top)
        top_lay.addWidget(self.recent_form)

        # 比赛筛选 + 竞逐动作按钮（不再有竞逐赛季 / 起点 / 终点状态条）
        filter_row = QHBoxLayout()
        filter_row.setSpacing(12)
        self.match_header = SectionHeader("近期战绩")
        filter_row.addWidget(self.match_header, 0)
        self.mode_chips = ChipBar(top)
        self.mode_chips.set_options(list(D.MATCH_FILTERS), "all")
        self.mode_chips.selected.connect(self._on_mode_filter)
        filter_row.addWidget(self.mode_chips, 0)
        # 预设职责二级筛选：只在选中「预设职责」时出现。
        self.preset_role_chips = ChipBar(top)
        self.preset_role_chips.set_options(list(D.PRESET_ROLE_FILTERS), PRESET_ROLE_ALL)
        self.preset_role_chips.selected.connect(self._on_preset_role_filter)
        self.preset_role_chips.setVisible(False)
        filter_row.addWidget(self.preset_role_chips, 0)
        filter_row.addStretch(1)
        self.season_chips = ChipBar(top)
        self.season_chips.selected.connect(self._on_season_filter)
        filter_row.addWidget(self.season_chips, 0)
        self.drive_bar = DriveRangeBar(top)
        self.drive_bar.importRequested.connect(self._import_drive_range)
        self.drive_bar.clearRequested.connect(self._clear_drive_range)
        filter_row.addWidget(self.drive_bar, 0)
        top_lay.addLayout(filter_row)
        outer.addWidget(top, 0)

        # ---------------- 比赛历史列表（唯一的纵向滚动区域） ----------------
        list_host = QWidget(page)
        list_host.setObjectName("Root")
        list_lay = QVBoxLayout(list_host)
        list_lay.setContentsMargins(18, 2, 18, 8)
        list_lay.setSpacing(0)

        self.match_list = MatchList(self._library, list_host)
        self.match_list.set_mark_resolver(self._drive_mark_for)
        self.match_list.set_context_menu_enabled(True)
        self.match_list.matchOpened.connect(self.open_detail)
        self.match_list.driveStartRequested.connect(self._set_drive_start)
        self.match_list.driveEndRequested.connect(self._set_drive_end)
        self.match_list.driveClearRequested.connect(self._clear_drive_range)
        self.match_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        list_lay.addWidget(self.match_list, 1)
        self.load_more_btn = QPushButton("加载更多（20 场）", list_host)
        self.load_more_btn.clicked.connect(self.load_more_matches)
        self.load_more_btn.setVisible(False)
        self.load_more_btn.setToolTip("继续加载较早记录；赛季和模式筛选作用于已加载的记录。")
        list_lay.addWidget(self.load_more_btn)

        outer.addWidget(list_host, 1)
        self._list_host = list_host
        # 卡片右侧信息容器要和「导入竞逐计算器」按钮左边缘对齐；
        # 按钮几何在首次布局后才确定，所以延后一帧再同步一次。
        QTimer.singleShot(0, self._sync_mode_icon_column)
        return page

    # ------------------------------------------------------------- 对齐
    def _sync_mode_icon_column(self) -> None:
        """让比赛卡右侧「模式图标列」左边缘与「导入竞逐计算器」按钮左边缘对齐。

        不写死绝对 x 坐标：按钮和列表都在同一页面坐标系里，这里只用控件当前几何
        算出「卡片右缘 → 按钮左缘」的距离，然后把它作为卡片右侧信息容器宽度。
        """
        btn = getattr(self, "drive_bar", None)
        host = getattr(self, "_list_host", None)
        if btn is None or host is None or not hasattr(self, "match_list"):
            return
        button = btn.import_btn
        if button.width() <= 1:
            return
        page = host.parentWidget()
        if page is None:
            return
        btn_left = button.mapTo(page, button.rect().topLeft()).x()
        # 卡片右缘 = 列表视口右缘（列表内部还有一点右侧留白给滚动条）。
        viewport = self.match_list.scroll.viewport()
        card_right = viewport.mapTo(page, viewport.rect().topRight()).x()
        card_right -= self.match_list._holder_layout.contentsMargins().right()
        width = card_right - btn_left
        if width > 0:
            self.match_list.set_right_block_width(int(width))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        QTimer.singleShot(0, self._sync_mode_icon_column)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, self._sync_mode_icon_column)

    # ------------------------------------------------------------------ 授权
    def refresh_auth_status(self) -> None:
        status = self._service.auth_status()
        if status.get("configured"):
            self.auth_status.setText(f"大神已授权 · {status.get('role_id_masked') or ''}")
            self.auth_btn.setText("重新授权")
            # 也覆盖“在设置页完成授权后再进入战绩页”的路径。
            if hasattr(self, "_auto_loaded_bound_account"):
                QTimer.singleShot(0, self._load_bound_account_if_available)
        else:
            self.auth_status.setText("大神未授权")
            self.auth_btn.setText("大神授权")

    def open_auth_dialog(self) -> None:
        dialog = DashenAuthDialog(self._service, self)
        self._auth_dialog = dialog
        dialog.authorized.connect(self._auth_completed)
        dialog.finished.connect(lambda _=0: setattr(self, "_auth_dialog", None))
        dialog.open()

    def _auth_completed(self, status: Any) -> None:
        self.refresh_auth_status()
        self.authChanged.emit()
        account_name = str(dict(status or {}).get("account_name") or "").strip()
        if account_name:
            self.query_edit.setText(account_name)
            self._auto_loaded_bound_account = True
            self.banner.show_message(f"网易大神授权成功，正在加载关联账号 {account_name}…", "ok")
            QTimer.singleShot(0, self.search)
        else:
            self.banner.show_message("网易大神授权成功。", "ok")

    def _bound_account_name(self) -> str:
        """仅在 QSettings 记录与当前授权 role 一致时返回绑定游戏账号名。"""
        status = self._service.auth_status()
        if not status.get("configured"):
            return ""
        masked = str(status.get("role_id_masked") or "")
        saved_masked = str(self._settings.value("dashen/role_id_masked", "") or "")
        if not masked or saved_masked != masked:
            return ""
        return str(self._settings.value("dashen/account_name", "") or "").strip()

    def _load_bound_account_if_available(self) -> None:
        """战绩页默认显示扫码时选择的关联 OW 游戏账号；手动搜索仍可覆盖。"""
        if self._auto_loaded_bound_account or self._query_busy or self._customer_token:
            return
        account_name = self._bound_account_name()
        if not account_name:
            return
        self._auto_loaded_bound_account = True
        self.query_edit.setText(account_name)
        self.search()

    # ------------------------------------------------------------------ 赛季
    def _backend_current_season(self) -> Optional[int]:
        """当前赛季只从后端取；后端未识别时返回 None，由 UI 显示占位。"""
        try:
            status = self._service.season_status()
        except Exception:
            return self._current_season
        season = status.get("current_season")
        try:
            value = int(season)
        except (TypeError, ValueError):
            return self._current_season
        return value if value > 0 else self._current_season

    def _refresh_season_ui(self) -> None:
        if self._current_season is None:
            self._current_season = self._backend_current_season()
        self._seasons = D.season_labels(self._current_season, 3)
        if self._seasons:
            if self._selected_season not in self._seasons:
                self._selected_season = self._seasons[0]
            self.season_selector.set_seasons(self._seasons, self._selected_season)
            options = [(SEASON_FILTER_ALL, "全部赛季")] + [
                (str(season), D.season_text(season)) for season in self._seasons
            ]
            current = str(self._season_filter)
            self.season_chips.set_options(options, current if current in {v for v, _ in options} else SEASON_FILTER_ALL)
        else:
            self.season_selector.set_seasons([])
            self.season_chips.set_options([(SEASON_FILTER_ALL, "全部赛季")], SEASON_FILTER_ALL)
        self._update_headers()

    def _update_headers(self) -> None:
        season_text = D.season_text(self._selected_season) if self._selected_season else ""
        suffix = f" · {season_text}" if season_text else ""
        # 四张卡本身已区分重装 / 输出 / 支援 / 开放职责，标题不再重复职责名。
        self.season_header.set_text(f"竞技段位{suffix}")
        current = D.season_text(self._current_season) if self._current_season else ""
        self.recent_header.set_text(
            f"近期状态 · {current} 竞技 + 快速" if current else "近期状态 · 竞技 + 快速"
        )
        if self._history_has_more:
            self.recent_header.set_text(f"近期状态 · {current} 竞技 + 快速（已加载记录）")
        total = len(self._filtered_matches())
        self.match_header.set_text(f"已加载战绩（{total}）" if total else "已加载战绩（0）")

    def _on_season_changed(self, season: int) -> None:
        self._selected_season = int(season)
        # 切换赛季必须清除另一个赛季的竞逐边界。
        self._drive.set_season(self._selected_season)
        self._season_filter = str(season)
        self.season_chips.set_current(str(season))
        self._render_roles()
        self._render_matches(keep_scroll=False)
        self._update_drive_bar()
        self._update_headers()

    def _on_season_filter(self, value: str) -> None:
        self._season_filter = value
        self._render_matches(keep_scroll=False)
        self._update_headers()

    def _on_mode_filter(self, value: str) -> None:
        self._mode_filter = value
        # 二级职责筛选只属于「预设职责」；切走时复位，避免残留状态影响其它分类。
        is_preset = value == "sport_preset"
        self.preset_role_chips.setVisible(is_preset)
        if not is_preset and self._preset_role_filter != PRESET_ROLE_ALL:
            self._preset_role_filter = PRESET_ROLE_ALL
            self.preset_role_chips.set_current(PRESET_ROLE_ALL)
        self._render_matches(keep_scroll=False)
        self._update_headers()

    def _on_preset_role_filter(self, value: str) -> None:
        self._preset_role_filter = value or PRESET_ROLE_ALL
        self._render_matches(keep_scroll=False)
        self._update_headers()

    # ------------------------------------------------------------------ 查询
    def _set_query_busy(self, busy: bool) -> None:
        self._query_busy = busy
        self.load_more_btn.setEnabled(not busy)
        self.search_btn.setEnabled(not busy)
        self.refresh_btn.setEnabled(not busy)
        self.query_edit.setEnabled(not busy)
        self.search_btn.setText("查询中…" if busy else "查询战绩")

    def search(self) -> None:
        if self._query_busy or self._detail_busy:
            return
        query = self.query_edit.text().replace("＃", "#").strip()
        if not query:
            self.banner.show_message("请输入 BattleTag。", "warn")
            return
        if not self._service.auth_status().get("configured"):
            self.banner.show_message("请先完成网易大神扫码授权。", "warn")
            self.open_auth_dialog()
            return
        self.banner.show_message("正在读取玩家资料、三赛季段位与最近 20 场概览…", "info")
        self._set_query_busy(True)
        service = self._service

        def task() -> Dict[str, Any]:
            player = service.resolve_player(query)
            token = player.customer_token
            card = service.player_card(customer_token=token)
            seasons = service.player_seasons(customer_token=token, season_count=3)
            history = service.match_overview_page(customer_token=token, limit=20)
            return {
                "player": player.to_dict(),
                "card": card,
                "seasons": seasons,
                "history": history,
                "season_status": service.season_status(),
            }

        submit(self, task, on_result=self._query_done, on_error=self._query_error,
               on_finished=lambda: self._set_query_busy(False))

    def _query_done(self, result: Any) -> None:
        data = dict(result or {})
        player = dict(data.get("player") or {})
        card = dict(data.get("card") or {})
        seasons = dict(data.get("seasons") or {})
        history = dict(data.get("history") or {})
        status = dict(data.get("season_status") or {})
        self._history_cursor = history.get("cursor")
        self._history_has_more = bool(history.get("has_more"))
        self.load_more_btn.setVisible(self._history_has_more)

        self._customer_token = str(player.get("customer_token") or "")
        self._player = D.player_header_view(card)
        if not self._player.get("battle_tag"):
            self._player["battle_tag"] = str(player.get("full_id") or "玩家")

        # 当前赛季：优先用查询结果里后端给出的赛季号。
        for candidate in (history.get("current_season"), seasons.get("current_season"),
                          status.get("current_season")):
            try:
                value = int(candidate)
            except (TypeError, ValueError):
                continue
            if value > 0:
                self._current_season = value
                break

        self._season_roles = {}
        for row in seasons.get("seasons") or []:
            if not isinstance(row, dict):
                continue
            season = row.get("season")
            competitive = row.get("competitive") if isinstance(row.get("competitive"), dict) else {}
            try:
                key = int(season)
            except (TypeError, ValueError):
                continue
            self._season_roles[key] = [r for r in competitive.get("roles") or [] if isinstance(r, dict)]

        views = [D.match_view(m) for m in history.get("matches") or [] if isinstance(m, dict)]
        D.apply_rank_from_roles(views, self._season_roles)
        views.sort(key=lambda v: int(v.get("begin_ts") or 0), reverse=True)
        self._matches = views
        self._match_index = {str(v.get("match_id") or ""): v for v in views if v.get("match_id")}

        self._selected_season = None
        self._season_filter = SEASON_FILTER_ALL
        self._drive = DriveSelection()
        self._refresh_season_ui()
        self.player_header.set_player(self._player)
        # 本人识别仅使用完整 BattleTag 进行确定性匹配，稳定账号 ID 不参与判定。
        self.detail_page.set_self_identity(
            identity=D.query_player_identity(player, card),
            customer_token=self._customer_token,
            bnet_id=str(self._player.get("bnet_id") or player.get("bnet_id") or ""),
            battle_tag=str(self._player.get("battle_tag") or ""),
        )
        self._render_roles()
        self._render_recent_form()
        self._render_matches(keep_scroll=False)
        self._update_drive_bar()
        self._update_headers()

        # 分页完整性仅保留在返回数据中，不生成任何常驻提示控件。
        self.banner.clear()

    def load_more_matches(self) -> None:
        if self._query_busy or self._detail_busy or not self._history_has_more or not self._customer_token:
            return
        self._set_query_busy(True)
        token, cursor = self._customer_token, self._history_cursor
        self.banner.show_message("正在加载后续 20 场概览…", "info")

        def task():
            return self._service.match_overview_page(customer_token=token, cursor=cursor, limit=20)

        def ready(history):
            self._history_cursor = history.get("cursor")
            self._history_has_more = bool(history.get("has_more"))
            self.load_more_btn.setVisible(self._history_has_more)
            views = [D.match_view(m) for m in history.get("matches") or []]
            D.apply_rank_from_roles(views, self._season_roles)
            for view in views:
                mid = str(view.get("match_id") or "")
                if mid and mid not in self._match_index:
                    self._matches.append(view)
                    self._match_index[mid] = view
            self._matches.sort(key=lambda v: int(v.get("begin_ts") or 0), reverse=True)
            self._render_recent_form()
            self._render_matches(keep_scroll=True)
            self._update_headers()
            self.banner.clear()

        submit(self, task, on_result=ready, on_error=self._query_error,
               on_finished=lambda: self._set_query_busy(False))

    def _query_error(self, message: str) -> None:
        self.banner.show_message(message, "error")

    # ------------------------------------------------------------------ 渲染
    def _render_roles(self) -> None:
        season = self._selected_season
        roles = self._season_roles.get(int(season)) if season else []
        presets, open_card = D.split_role_cards(roles or [])
        for widget, data in zip(self.role_cards, presets):
            widget.set_card(data)
        if open_card:
            self.open_card.set_card(open_card)
            self.open_card.setVisible(True)
        else:
            # 没有开放职责数据时只隐藏这张卡，其余三张仍等宽排在同一行，
            # 不留空标题也不留空行高度。
            self.open_card.setVisible(False)

    def _filtered_matches(self) -> List[Dict[str, Any]]:
        season: Optional[int] = None
        if self._season_filter != SEASON_FILTER_ALL:
            try:
                season = int(self._season_filter)
            except (TypeError, ValueError):
                season = None
        return D.filter_matches(
            self._matches,
            filter_key=self._mode_filter,
            season=season,
            preset_role=self._preset_role_filter,
        )

    def _render_matches(self, *, keep_scroll: bool) -> None:
        self.match_list.set_matches(self._filtered_matches(), keep_scroll=keep_scroll)

    # ------------------------------------------------------------------ 近期状态
    def recent_form_summary(self) -> Dict[str, Any]:
        """当前赛季最近最多 20 场（预设竞技 + 开放竞技 + 快速）的统计。

        只看当前赛季，绝不跨赛季补齐；平局计入胜率分母；
        没有任何符合条件的比赛时 ``win_rate`` 为 None。
        """
        return D.recent_form_summary(self._matches, self._current_season)

    def _render_recent_form(self) -> None:
        summary = self.recent_form_summary()
        self._recent_form = summary
        self.recent_form.set_summary(summary)
        rate = summary.get("win_rate")
        # 只有确实存在最近比赛时才对外广播默认胜率；没有数据时不发 0%。
        if rate is not None:
            self.recentWinRateReady.emit(float(rate), self._customer_token)

    # ------------------------------------------------------------------ 竞逐区间
    def _drive_mark_for(self, match_id: str) -> str:
        return self._drive.mark_for(self._matches, match_id)

    def _drive_season_of(self, match_id: str) -> Optional[int]:
        view = self._match_index.get(str(match_id))
        if not view:
            return None
        season = view.get("season")
        try:
            return int(season)
        except (TypeError, ValueError):
            return None

    def _set_drive_start(self, match_id: str) -> None:
        view = self._match_index.get(str(match_id))
        if not view or not D.is_preset_competitive(view):
            self.banner.show_message("只能把标准竞技比赛设为竞逐起点。", "warn")
            return
        season = self._drive_season_of(match_id)
        if season is None:
            self.banner.show_message("该比赛缺少赛季信息，无法作为竞逐起点。", "warn")
            return
        previous = self._drive.season
        self._drive.set_start(season, match_id)
        if previous is not None and previous != season:
            self.banner.show_message(
                f"竞逐只能属于一个赛季，已切换到 {D.season_text(season)} 并清除原区间。", "warn"
            )
        self._after_drive_change()

    def _set_drive_end(self, match_id: str) -> None:
        view = self._match_index.get(str(match_id))
        if not view or not D.is_preset_competitive(view):
            self.banner.show_message("只能把标准竞技比赛设为竞逐终点。", "warn")
            return
        season = self._drive_season_of(match_id)
        if season is None:
            self.banner.show_message("该比赛缺少赛季信息，无法作为竞逐终点。", "warn")
            return
        if self._drive.season is not None and self._drive.start_id and season != self._drive.season:
            self.banner.show_message(
                f"起点属于 {D.season_text(self._drive.season)}，终点必须选同一赛季的比赛。", "error"
            )
            return
        self._drive.set_end(season, match_id)
        self._after_drive_change()

    def _clear_drive_range(self) -> None:
        self._drive.clear()
        self._after_drive_change()

    def _after_drive_change(self) -> None:
        self._update_drive_bar()
        self.match_list.refresh_marks()

    def _update_drive_bar(self) -> None:
        summary = self._drive.summary(self._matches)
        start = self._match_index.get(self._drive.start_id) if self._drive.start_id else None
        end = self._match_index.get(self._drive.end_id) if self._drive.end_id else None
        self.drive_bar.update_state(self._drive.season, start, end, summary)

    def _import_drive_range(self) -> None:
        summary = self._drive.summary(self._matches)
        if not summary or not summary.get("ok"):
            self.banner.show_message("请先在同一赛季内选择竞逐起点和终点。", "warn")
            return
        matches = list(summary.get("matches") or [])
        if not matches:
            self.banner.show_message("所选区间内没有可导入的标准竞技比赛。", "warn")
            return
        incoming = D.drive_records_from_matches(matches)
        merged, duplicated, added = D.dedupe_drive_records(self._state.records, incoming)
        if added == 0:
            self.banner.show_message(
                f"所选区间的 {duplicated} 场比赛全部已导入过，未新增记录。", "warn"
            )
            return
        dialog = DriveImportDialog(summary, duplicated, added, self)
        if not dialog.exec():
            return
        self._state.set_records(merged)
        self.banner.show_message(
            f"已导入 {D.season_text(summary.get('season'))} 竞逐区间："
            f"新增 {added} 场" + (f"，跳过重复 {duplicated} 场" if duplicated else "")
            + "。组排信息大神不提供，已按单排导入，可在历史战绩页批量调整。",
            "ok",
        )
        self.requestHistoryPage.emit()

    # ------------------------------------------------------------------ 详情
    def open_detail(self, match_id: str) -> None:
        if self._detail_busy or self._query_busy:
            return
        view = self._match_index.get(str(match_id))
        if not view:
            return
        if not self._customer_token:
            self.banner.show_message("缺少查询标识，请重新查询玩家。", "warn")
            return
        self._list_scroll = self.match_list.scroll_value()
        token = self._customer_token
        cached = self._detail_cache.get(token, str(match_id))
        if cached is not None:
            self._current_view = dict(view)
            self._current_detail = dict(cached)
            self.detail_page.set_detail(view, cached)
            self.stack.setCurrentIndex(1)
            return

        self._current_view = dict(view)
        self._current_detail = None

        self.detail_page.show_loading(view)
        self.stack.setCurrentIndex(1)
        self._detail_busy = True
        service = self._service
        source = view.get("raw") if isinstance(view.get("raw"), dict) else {}

        def task() -> Dict[str, Any]:
            # 第一次进入该局时一次性获取双方记分板 + 全员英雄明细；
            # 完成后整体写入缓存，后续进入不再逐项重新请求。
            return service.match_all_players_detail(
                str(match_id), customer_token=token, source_match=dict(source or {})
            )

        def detail_ready(data: Any, v=view, mid=str(match_id), ct=token) -> None:
            detail = dict(data or {})
            self._detail_cache.put(ct, mid, detail)
            if str(self._current_view.get("match_id") or "") == mid:
                self._current_detail = detail
            self.detail_page.set_detail(v, detail)

        submit(
            self,
            task,
            on_result=detail_ready,
            on_error=self.detail_page.show_error,
            on_finished=lambda: setattr(self, "_detail_busy", False),
        )

    def _self_identity_for_ai(self) -> Dict[str, str]:
        """当前查询账号身份，交给 ai_review 后端判定 is_self（前端不自行判断）。

        本项目的本人识别规则是「只按完整 BattleTag」（稳定账号 ID 不参与判定，
        见 _query_done 与详情页高亮）。ai_review 后端在收到 customer_token / bnet_id
        时会优先按这些 ID 匹配，可能把持有同 token 的其他玩家也标成本人，
        所以这里只传 battle_tag，保证 AI 锐评的「本人」与详情页高亮一致。
        """
        page = self.detail_page
        identity = dict(page._self_identity or {})
        battle_tag = str(identity.get("battle_tag") or page._self_battle_tag or "").strip()
        return {"battle_tag": battle_tag}

    def open_ai_review(self) -> None:
        """进入当前对局的 AI 锐评：直接使用已获取的详情，不重新请求。"""
        if self._current_detail is None:
            self.detail_page.show_error("本局详情尚未加载完成，暂时无法进行 AI 锐评。")
            return
        self.ai_review_page.open_match(
            self._current_detail,
            match_meta=self._current_view,
            self_identity=self._self_identity_for_ai(),
        )
        self.stack.setCurrentIndex(2)

    def _close_detail(self) -> None:
        self.stack.setCurrentIndex(0)
        # 返回时保留赛季、筛选和滚动位置，不重新从顶部加载。
        self.match_list.restore_scroll(self._list_scroll)

    # ------------------------------------------------------------------ 缓存
    def drop_runtime_caches(self, kind: str = "all") -> None:
        """设置页清理磁盘缓存后同步丢弃对应内存缓存。

        当前已打开页面的数据继续可用，不会因为磁盘文件消失而报错；
        下一次重新打开详情 / 需要头像时会自动重新获取并重新生成缓存。
        """
        target = str(kind or "all")
        if target in ("all", "detail"):
            self._detail_cache.clear_memory()
        if target in ("all", "avatar"):
            self._avatars.clear_memory()


__all__ = ["OWStatsPage"]
