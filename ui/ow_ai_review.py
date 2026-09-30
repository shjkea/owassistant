# -*- coding: utf-8 -*-
"""单局 AI 锐评前端。

只负责展示、玩家选择、风格选择与流式输出；数据整理 / Prompt / API 全部由
``ai_review``（ai_review_backend/）完成：

- 玩家列表完全来自 ``AIReviewService.players_for_ui(ctx)``：返回多少人就渲染多少张卡，
  按 ``team`` 字段动态分组，``player_id`` 原样使用；不假定 5v5 / 10 人 / 固定列数；
- 本人只看后端给出的 ``is_self``；
- 网络调用放在 ``ReviewStreamWorker``（线程池）里，停止只走 CancellationToken。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Set

from PySide6.QtCore import QCoreApplication, QEvent, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import ai_review_bridge as AIB
from . import owstats_data as D
from .assets import AssetLibrary
from .ow_widgets import ElidedLabel, HeroPortrait
from .theme import C
from .widgets import Banner, SegmentedControl, StatusLine, make_button

# 锐评风格：值与后端 ReviewStyle 一致；默认 coach。
STYLE_OPTIONS = (
    ("praise", "夸夸型", "重点寻找本局值得肯定的表现。"),
    ("coach", "专业教练", "从数据、职责贡献和同位置对比进行复盘。"),
    ("roast", "喷子型", "基于真实数据进行毒舌点评，只针对游戏表现。"),
    ("data", "数据党", "重点分析数据结构、差距和异常点。"),
)
DEFAULT_STYLE = "coach"

STATUS_TEXT = {
    "requesting": "正在连接 DeepSeek…",
    "thinking": "正在分析本局数据…",
    "answering": "正在生成锐评…",
    "cache_hit": "已读取缓存",
    "done": "完成",
}

# 队伍分组：按 players_for_ui 返回的 team 字段；已知值给中文标题与配色，
# 其它值（以后可能出现的模式）原样作为标题，不丢弃任何玩家。
TEAM_TITLES = {"friendly": ("我方", C.WIN), "enemy": ("敌方", C.LOSE)}
TEAM_ORDER = ("friendly", "enemy")

RESULT_TEXT = {"win": ("胜利", C.WIN), "loss": ("失败", C.LOSE), "draw": ("平局", C.TEXT_SUB)}

ACCENT = QColor(C.ACCENT)


# --------------------------------------------------------------------------- 工具
def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def player_stat_line(player: Mapping[str, Any]) -> str:
    """职责相关的主要数据，最多两项；0 / 缺失不显示。"""
    role = str(player.get("role_type") or "").lower()
    items = {
        "dmg": (_int(player.get("hero_damage")), "伤害"),
        "heal": (_int(player.get("healing")), "治疗"),
        "block": (_int(player.get("resist_damage")), "抵挡"),
    }
    if role == "tank":
        order = ("dmg", "block", "heal")
    elif role in ("healer", "support"):
        order = ("dmg", "heal", "block")
    else:
        order = ("dmg", "heal", "block")
    parts = [f"{D.format_compact(items[k][0])}{items[k][1]}" for k in order if items[k][0] > 0]
    return " · ".join(parts[:2])


def player_kda_line(player: Mapping[str, Any]) -> str:
    k, d, a = (_int(player.get(key)) for key in ("kills", "deaths", "assists"))
    if k == d == a == 0 and not player_stat_line(player):
        return ""
    return f"{k} / {d} / {a}"


def group_players(players: Sequence[Mapping[str, Any]]) -> List[tuple]:
    """按 team 字段分组，保留后端顺序。返回 [(team, [player, ...]), ...]。"""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for p in players:
        team = str(p.get("team") or "").strip() or "friendly"
        groups.setdefault(team, []).append(dict(p))
    ordered = [t for t in TEAM_ORDER if t in groups] + [t for t in groups if t not in TEAM_ORDER]
    return [(t, groups[t]) for t in ordered]


# --------------------------------------------------------------------------- 入口卡
class AIReviewEntryCard(QFrame):
    """对局详情页最下方的全宽 AI 锐评入口。"""

    openRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("AIPanel")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(12)
        text = QVBoxLayout()
        text.setSpacing(3)
        title = QLabel("✦ AI 锐评")
        title.setObjectName("AIEntryTitle")
        text.addWidget(title)
        self.sub = QLabel("让 DeepSeek 分析本局玩家表现")
        self.sub.setProperty("role", "hint")
        self.sub.setWordWrap(True)
        text.addWidget(self.sub)
        lay.addLayout(text, 1)
        self.button = make_button("进入 AI 锐评 ›", "ghost")
        self.button.clicked.connect(self.openRequested.emit)
        lay.addWidget(self.button, 0, Qt.AlignRight | Qt.AlignBottom)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        if AIB.AR is None:
            self.set_available(False, AIB.IMPORT_ERROR)

    def set_available(self, ok: bool, reason: str = "") -> None:
        self.button.setEnabled(bool(ok))
        self.sub.setText("让 DeepSeek 分析本局玩家表现" if ok else (reason or "AI 锐评当前不可用"))


# --------------------------------------------------------------------------- 玩家选择卡
class PlayerPickCard(QWidget):
    """紧凑的「分析对象」选择卡：职责 / 主英雄（+最多 2 个次英雄）/ BattleTag / KDA / 主要数据。"""

    toggled = Signal(str)

    HERO_SIZE = 36
    SECONDARY_SIZE = 16
    MAX_EXTRA_HEROES = 2
    ROLE_SIZE = 16
    CHECK_SIZE = 14

    def __init__(self, library: AssetLibrary, player: Mapping[str, Any], queue_kind: str = "",
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("AIPickCard")
        self._library = library
        self._player = dict(player)
        self.player_id = str(self._player.get("player_id") or "")
        self.team = str(self._player.get("team") or "")
        self.is_self = bool(self._player.get("is_self"))
        self._selected = False
        self._included = False  # 全局分析时的「整体包含」状态（非勾选）
        self._hover = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_Hover, True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 9, 10, 9)
        lay.setSpacing(3)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        self.role = QLabel(self)
        self.role.setFixedSize(self.ROLE_SIZE, self.ROLE_SIZE)
        role_key, role_name = D.display_role(
            self._player.get("role_type"), self._player.get("role_name"), queue_kind
        )
        self._role_args = (role_key or role_name, role_name)
        self.role.setToolTip(role_name or "职责未知")
        top.addWidget(self.role, 0, Qt.AlignVCenter)
        self.hero = HeroPortrait(library, self.HERO_SIZE, radius=7,
                                 secondary_size=self.SECONDARY_SIZE, parent=self)
        heroes = [dict(h) for h in self._player.get("heroes") or [] if isinstance(h, Mapping)]
        if heroes:
            self.hero.set_heroes(heroes[: 1 + self.MAX_EXTRA_HEROES])
        else:
            self.hero.set_hero(self._player.get("hero_name"), self._player.get("hero_guid"))
        top.addWidget(self.hero, 0, Qt.AlignVCenter)
        top.addStretch(1)
        lay.addLayout(top)

        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 2, 0, 0)
        name_row.setSpacing(4)
        self.name = ElidedLabel(str(self._player.get("battle_tag") or "未知玩家"), self)
        self.name.setObjectName("AIPickName")
        name_row.addWidget(self.name, 1)
        if self.is_self:
            tag = QLabel("本人", self)
            tag.setObjectName("AIPickSelf")
            name_row.addWidget(tag, 0)
        lay.addLayout(name_row)

        kda = player_kda_line(self._player)
        self.kda = QLabel(kda or "暂无数据", self)
        self.kda.setObjectName("AIPickKda" if kda else "AIPickStats")
        lay.addWidget(self.kda)
        self.stats = ElidedLabel(player_stat_line(self._player), self)
        self.stats.setObjectName("AIPickStats")
        self.stats.setVisible(bool(self.stats.full_text()))
        lay.addWidget(self.stats)
        lay.addStretch(1)

        self._load_role_icon()
        self._apply_tooltip(heroes)
        self.setAccessibleName(f"分析对象 {self._player.get('battle_tag') or ''}")

    # ---------------- 数据 ----------------
    def _load_role_icon(self) -> None:
        dpr = self.devicePixelRatioF() if self.isVisible() else 1.0
        pix = self._library.role_icon(self._role_args[0], self.ROLE_SIZE, self._role_args[1], dpr=dpr)
        self.role.setPixmap(pix if not pix.isNull() else QPixmap())

    def _apply_tooltip(self, heroes: Sequence[Mapping[str, Any]]) -> None:
        lines = [str(self._player.get("battle_tag") or "")]
        names = [str(h.get("hero_name") or "").strip() for h in heroes]
        names = [n for n in names if n] or [str(self._player.get("hero_name") or "").strip()]
        names = [n for n in names if n]
        if names:
            lines.append("英雄：" + " · ".join(names))
        for label, key in (("伤害", "hero_damage"), ("治疗", "healing"), ("抵挡", "resist_damage")):
            value = _int(self._player.get(key))
            if value:
                lines.append(f"{label} {value:,}")
        self.setToolTip("\n".join(line for line in lines if line))

    def player(self) -> Dict[str, Any]:
        return dict(self._player)

    # ---------------- 状态 ----------------
    def is_selected(self) -> bool:
        return self._selected

    def set_selected(self, selected: bool) -> None:
        if bool(selected) != self._selected:
            self._selected = bool(selected)
            self.update()

    def set_included(self, included: bool) -> None:
        if bool(included) != self._included:
            self._included = bool(included)
            self.update()

    # ---------------- 交互 ----------------
    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.toggled.emit(self.player_id)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self.toggled.emit(self.player_id)
            event.accept()
            return
        super().keyPressEvent(event)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def focusInEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().focusOutEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._load_role_icon()

    # ---------------- 绘制 ----------------
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        path = QPainterPath()
        path.addRoundedRect(rect, 7, 7)
        if self._selected:
            fill = QColor(C.SURFACE_3)
            border = QPen(ACCENT, 1.5)
        elif self._included:
            fill = QColor(C.SURFACE_2)
            border = QPen(QColor(242, 163, 60, 110), 1.0)
        else:
            fill = QColor(C.SURFACE_3 if self._hover else C.SURFACE_2)
            border = QPen(QColor(C.BORDER_STRONG if (self._hover or self.hasFocus()) else C.BORDER), 1.0)
        painter.fillPath(path, fill)
        if self._selected:
            painter.fillPath(path, QColor(242, 163, 60, 16))
        painter.setPen(border)
        painter.drawPath(path)
        if self.hasFocus() and not self._selected:
            painter.setPen(QPen(QColor(C.TEXT_DIM), 1.0, Qt.DotLine))
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 6, 6)
        if self._selected:
            s = self.CHECK_SIZE
            box = QRectF(self.width() - s - 8, 8, s, s)
            painter.setPen(Qt.NoPen)
            painter.setBrush(ACCENT)
            painter.drawEllipse(box)
            pen = QPen(QColor("#1a1204"), 1.8)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            x, y = box.x(), box.y()
            painter.drawPolyline([
                QPointF(x + s * 0.28, y + s * 0.52),
                QPointF(x + s * 0.44, y + s * 0.68),
                QPointF(x + s * 0.74, y + s * 0.34),
            ])
        painter.end()


# --------------------------------------------------------------------------- 动态卡片网格
class PlayerCardGrid(QWidget):
    """按可用宽度自动决定列数的卡片网格。

    列数 = 当前宽度能放下的最小卡宽数量（上限为卡片总数），卡片等宽铺满；
    不写死列数：5 张卡就一行 5 张，6 张卡就一行 6 张，宽度不足时自动换行。
    高度按行数确定后固定，父布局拿到的是确定值（与 FlowFrame 同样的策略）。
    """

    MIN_CARD_W = 128
    MAX_CARD_W = 220
    H_GAP = 8
    V_GAP = 8

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._cards: List[PlayerPickCard] = []
        self._syncing = False
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    def set_cards(self, cards: Sequence[PlayerPickCard]) -> None:
        for card in self._cards:
            card.setParent(None)
            card.deleteLater()
        self._cards = list(cards)
        for card in self._cards:
            card.setParent(self)
            card.show()
        self._relayout()
        self.updateGeometry()

    def cards(self) -> List[PlayerPickCard]:
        return list(self._cards)

    def _card_height(self) -> int:
        return max((card.sizeHint().height() for card in self._cards), default=0)

    def columns_for(self, width: int) -> int:
        count = len(self._cards)
        if not count:
            return 0
        fit = (max(int(width), 0) + self.H_GAP) // (self.MIN_CARD_W + self.H_GAP)
        return max(1, min(count, fit))

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        count = len(self._cards)
        if not count:
            return 0
        cols = self.columns_for(width)
        rows = -(-count // cols)
        return rows * self._card_height() + (rows - 1) * self.V_GAP

    def sizeHint(self) -> QSize:  # noqa: N802
        count = len(self._cards)
        width = count * self.MIN_CARD_W + max(0, count - 1) * self.H_GAP
        return QSize(width, self.heightForWidth(self.width() if self.width() > 0 else width))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        width = self.MIN_CARD_W if self._cards else 0
        return QSize(width, self.heightForWidth(self.width() if self.width() > 0 else width))

    def _relayout(self) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            width = self.width()
            if not self._cards:
                self.setFixedHeight(0)
                return
            if width <= 0:
                width = self.sizeHint().width()
            cols = self.columns_for(width)
            card_w = min(self.MAX_CARD_W, (width - (cols - 1) * self.H_GAP) // cols)
            card_h = self._card_height()
            for index, card in enumerate(self._cards):
                row, col = divmod(index, cols)
                card.setGeometry(QRect(col * (card_w + self.H_GAP), row * (card_h + self.V_GAP), card_w, card_h))
            needed = self.heightForWidth(width)
            if self.minimumHeight() != needed or self.maximumHeight() != needed:
                self.setFixedHeight(needed)
                parent = self.parentWidget()
                if parent is not None and parent.layout() is not None:
                    parent.layout().invalidate()
        finally:
            self._syncing = False

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._relayout()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._relayout()

    def event(self, event) -> bool:  # noqa: A003
        # 子卡片字号在 polish 后才生效，sizeHint 变化会向父控件投递 LayoutRequest。
        if event.type() == QEvent.LayoutRequest:
            self._relayout()
        return super().event(event)


# --------------------------------------------------------------------------- 锐评页
class AIReviewPage(QWidget):
    """某一局的 AI 锐评页（与对局详情同尺寸，放在同一个 QStackedWidget 里）。"""

    backRequested = Signal()

    def __init__(self, library: AssetLibrary, parent: Optional[QWidget] = None, *,
                 service_provider=None) -> None:
        super().__init__(parent)
        self.setObjectName("Root")
        self._library = library
        self._service_provider = service_provider or AIB.ai_service
        self._service: Any = None
        self._ctx: Any = None
        self._match_key = ""
        self._players: List[Dict[str, Any]] = []
        self._cards: Dict[str, PlayerPickCard] = {}
        self._grids: Dict[str, PlayerCardGrid] = {}
        self._team_widgets: List[QWidget] = []
        self._self_id = ""
        self._selected: Set[str] = set()
        self._global = False
        self._style = DEFAULT_STYLE
        self._queue_kind = ""

        self._run_seq = 0
        self._active_run = 0
        self._cancel_token: Any = None
        self._workers: Dict[int, AIB.ReviewStreamWorker] = {}
        self._text_parts: List[str] = []
        self._has_result = False
        self._cache_hit = False
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(60)
        self._flush_timer.timeout.connect(self._flush_output)

        self._build_ui()
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.cancel_running)

    # ------------------------------------------------------------------ 构建
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        root.addWidget(self.scroll, 1)

        page = QWidget()
        page.setObjectName("Root")
        self.scroll.setWidget(page)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(18, 14, 18, 16)
        lay.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(10)
        back = make_button("← 返回对局详情", "ghost")
        back.clicked.connect(self.backRequested.emit)
        head.addWidget(back, 0)
        self.title = QLabel("AI 锐评")
        self.title.setObjectName("DetailTitle")
        head.addWidget(self.title, 1)
        lay.addLayout(head)

        # 本局简要摘要（不复制完整详情页）。
        summary = QFrame(page)
        summary.setObjectName("AIPanel")
        s_lay = QHBoxLayout(summary)
        s_lay.setContentsMargins(14, 10, 14, 10)
        s_lay.setSpacing(12)
        self.result_label = QLabel("")
        self.result_label.setObjectName("AIResult")
        s_lay.addWidget(self.result_label, 0)
        s_text = QVBoxLayout()
        s_text.setSpacing(2)
        self.map_label = QLabel("")
        self.map_label.setProperty("role", "section")
        s_text.addWidget(self.map_label)
        self.meta_label = QLabel("")
        self.meta_label.setObjectName("DetailSummary")
        self.meta_label.setWordWrap(True)
        s_text.addWidget(self.meta_label)
        s_lay.addLayout(s_text, 1)
        self.score_label = QLabel("")
        self.score_label.setObjectName("AIScore")
        s_lay.addWidget(self.score_label, 0, Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(summary)

        # 后端 data_warnings：不抢眼的提示条。
        self.warning = Banner(page)
        lay.addWidget(self.warning)

        # ---------------- 分析对象 ----------------
        target = QFrame(page)
        target.setObjectName("AIPanel")
        t_lay = QVBoxLayout(target)
        t_lay.setContentsMargins(14, 12, 14, 14)
        t_lay.setSpacing(10)
        t_head = QHBoxLayout()
        t_head.setSpacing(8)
        cap = QLabel("分析对象")
        cap.setProperty("role", "section")
        t_head.addWidget(cap, 0)
        t_head.addStretch(1)
        self.count_label = QLabel("")
        self.count_label.setProperty("role", "hint")
        t_head.addWidget(self.count_label, 0)
        t_lay.addLayout(t_head)

        quick = QHBoxLayout()
        quick.setSpacing(6)
        self.quick_self = self._chip("只选本人", self.select_self)
        self.quick_friendly = self._chip("我方全选", lambda: self.select_team("friendly"))
        self.quick_enemy = self._chip("敌方全选", lambda: self.select_team("enemy"))
        self.quick_global = self._chip("全局分析", self.select_global)
        for btn in (self.quick_self, self.quick_friendly, self.quick_enemy, self.quick_global):
            quick.addWidget(btn, 0)
        quick.addStretch(1)
        t_lay.addLayout(quick)

        self.teams_host = QWidget(target)
        self.teams_layout = QVBoxLayout(self.teams_host)
        self.teams_layout.setContentsMargins(0, 0, 0, 0)
        self.teams_layout.setSpacing(8)
        t_lay.addWidget(self.teams_host)
        self.empty_players = QLabel("暂无可分析的玩家数据")
        self.empty_players.setObjectName("EmptyHint")
        self.empty_players.setAlignment(Qt.AlignCenter)
        self.empty_players.setMinimumHeight(48)
        self.empty_players.setVisible(False)
        t_lay.addWidget(self.empty_players)
        lay.addWidget(target)

        # ---------------- 锐评风格 ----------------
        style = QFrame(page)
        style.setObjectName("AIPanel")
        st_lay = QVBoxLayout(style)
        st_lay.setContentsMargins(14, 12, 14, 12)
        st_lay.setSpacing(8)
        st_cap = QLabel("锐评风格")
        st_cap.setProperty("role", "section")
        st_lay.addWidget(st_cap)
        values = [v for v, _l, _d in STYLE_OPTIONS]
        self.style_control = SegmentedControl([l for _v, l, _d in STYLE_OPTIONS], values.index(DEFAULT_STYLE), style)
        self.style_control.changed.connect(self._on_style_changed)
        st_lay.addWidget(self.style_control)
        self.style_hint = QLabel("")
        self.style_hint.setProperty("role", "hint")
        self.style_hint.setWordWrap(True)
        st_lay.addWidget(self.style_hint)
        lay.addWidget(style)

        # ---------------- 操作 ----------------
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.start_btn = make_button("✦ 开始 AI 锐评", "primary")
        self.start_btn.clicked.connect(self._on_start_clicked)
        actions.addWidget(self.start_btn, 0)
        self.regen_btn = make_button("重新生成", "ghost", "忽略本地缓存，重新请求 DeepSeek")
        self.regen_btn.clicked.connect(lambda: self.start_review(force_refresh=True))
        actions.addWidget(self.regen_btn, 0)
        self.status = StatusLine(page)
        actions.addWidget(self.status, 1)
        lay.addLayout(actions)

        self.message = Banner(page)
        lay.addWidget(self.message)

        # ---------------- AI 输出 ----------------
        out = QFrame(page)
        out.setObjectName("AIPanel")
        o_lay = QVBoxLayout(out)
        o_lay.setContentsMargins(14, 12, 14, 14)
        o_lay.setSpacing(8)
        o_cap = QLabel("AI 锐评")
        o_cap.setProperty("role", "section")
        o_lay.addWidget(o_cap)
        self.output = QTextBrowser(out)
        self.output.setObjectName("AIReviewOutput")
        self.output.setReadOnly(True)
        self.output.setOpenLinks(False)
        self.output.setOpenExternalLinks(False)
        self.output.setLineWrapMode(QTextBrowser.WidgetWidth)
        self.output.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.output.setMinimumHeight(300)
        self.output.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.output.setPlaceholderText("选择分析对象和风格后，点击「开始 AI 锐评」。")
        o_lay.addWidget(self.output, 1)
        lay.addWidget(out, 1)

        self._update_style_hint()
        self._refresh_selection_ui()

    def _chip(self, text: str, slot) -> QToolButton:
        btn = QToolButton(self)
        btn.setObjectName("FilterChip")
        btn.setText(text)
        btn.setCheckable(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda _=False: slot())
        return btn

    # ------------------------------------------------------------------ 数据
    def open_match(self, detail: Mapping[str, Any], *, match_meta: Optional[Mapping[str, Any]] = None,
                   self_identity: Optional[Mapping[str, Any]] = None) -> None:
        """进入某局锐评。detail 为已经获取好的 match_all_players_detail() 结果，不重新请求。"""
        data = dict(detail or {})
        meta = dict(match_meta or {})
        identity = dict(self_identity or {})
        match_id = str(data.get("match_id") or meta.get("match_id") or "")
        key = f"{match_id}|{identity.get('battle_tag') or ''}|{identity.get('customer_token') or ''}" if match_id else ""
        if key and key == self._match_key and self._ctx is not None:
            # 同一局返回再进入：保留选择、风格、输出与进行中的任务。
            return
        self.cancel_running()
        self._match_key = key
        self._ctx = None
        self._players = []
        self._has_result = False
        self._text_parts = []
        self._cache_hit = False
        self.output.clear()
        self.message.clear()
        self.status.show_message("", "info")
        self._service = self._service_provider()

        if self._service is None:
            self._render_players()
            self._render_summary(meta, None)
            self.warning.clear()
            self.message.show_message(AIB.IMPORT_ERROR or "AI 锐评后端不可用。", "error")
            self._refresh_selection_ui()
            return
        try:
            self._ctx = self._service.prepare_match(data, match_meta=meta, self_identity=identity)
            self._players = [dict(p) for p in self._service.players_for_ui(self._ctx)]
        except Exception as exc:  # noqa: BLE001
            self._ctx = None
            self._players = []
            self.message.show_message(AIB.friendly_error(exc), "error")

        self._queue_kind = str(meta.get("queue_kind") or "")
        self._render_summary(meta, self._ctx)
        warnings = [str(w) for w in (getattr(self._ctx, "data_warnings", None) or []) if str(w).strip()]
        if warnings:
            self.warning.show_message("\n".join(warnings), "warn")
        else:
            self.warning.clear()
        self._render_players()
        self.select_self()

    def _render_summary(self, meta: Mapping[str, Any], ctx: Any) -> None:
        match = getattr(ctx, "match", None)
        map_name = str(getattr(match, "map_name", "") or meta.get("map_name") or "").strip()
        self.title.setText(f"AI 锐评 · {map_name}" if map_name else "AI 锐评")
        self.map_label.setText(map_name or "未知地图")
        game_mode = str(getattr(match, "game_mode", "") or meta.get("game_mode_zh") or "").strip()
        map_mode = str(getattr(match, "map_mode", "") or meta.get("map_mode_zh") or "").strip()
        duration = D.format_duration(getattr(match, "duration_seconds", None) or meta.get("game_time_sec"))
        self.meta_label.setText(" · ".join(x for x in (game_mode, map_mode, duration) if x))
        ts = getattr(match, "team_score", None)
        os_ = getattr(match, "opponent_score", None)
        self.score_label.setText(f"{ts} : {os_}" if ts is not None and os_ is not None else "")
        result = str(getattr(match, "result", "") or meta.get("result") or "")
        text, color = RESULT_TEXT.get(result, ("", C.TEXT_SUB))
        self.result_label.setText(text)
        self.result_label.setStyleSheet(f"color: {color};")
        self.result_label.setVisible(bool(text))

    def _render_players(self) -> None:
        for widget in self._team_widgets:
            widget.setParent(None)
            widget.deleteLater()
        self._team_widgets = []
        self._cards = {}
        self._grids = {}
        self._self_id = ""
        queue_kind = getattr(self, "_queue_kind", "")
        for player in self._players:
            if player.get("is_self") and not self._self_id:
                self._self_id = str(player.get("player_id") or "")
        for team, members in group_players(self._players):
            title, color = TEAM_TITLES.get(team, (team, C.TEXT_SUB))
            label = QLabel(f"{title} · {len(members)} 人", self.teams_host)
            label.setObjectName("AITeamTitle")
            label.setStyleSheet(f"color: {color};")
            grid = PlayerCardGrid(self.teams_host)
            cards = []
            for player in members:
                card = PlayerPickCard(self._library, player, queue_kind)
                card.toggled.connect(self.toggle_player)
                self._cards[card.player_id] = card
                cards.append(card)
            grid.set_cards(cards)
            self._grids[team] = grid
            self.teams_layout.addWidget(label)
            self.teams_layout.addWidget(grid)
            self._team_widgets.extend([label, grid])
        self.empty_players.setVisible(not self._players)

    # ------------------------------------------------------------------ 选择
    def players(self) -> List[Dict[str, Any]]:
        return [dict(p) for p in self._players]

    def cards(self) -> Dict[str, PlayerPickCard]:
        return dict(self._cards)

    def team_grid(self, team: str) -> Optional[PlayerCardGrid]:
        return self._grids.get(team)

    def selected_ids(self) -> List[str]:
        """按后端玩家顺序返回已选 player_id。"""
        return [str(p.get("player_id")) for p in self._players if str(p.get("player_id")) in self._selected]

    def is_global(self) -> bool:
        return self._global

    def _team_ids(self, team: str) -> Set[str]:
        return {str(p.get("player_id")) for p in self._players if str(p.get("team") or "") == team}

    def select_self(self) -> None:
        self._global = False
        self._selected = {self._self_id} if self._self_id else set()
        self._refresh_selection_ui()

    def select_team(self, team: str) -> None:
        self._global = False
        self._selected = self._team_ids(team)
        self._refresh_selection_ui()

    def select_global(self) -> None:
        self._global = True
        self._refresh_selection_ui()

    def toggle_player(self, player_id: str) -> None:
        pid = str(player_id or "")
        if pid not in self._cards:
            return
        if self._global:
            # 从全局分析回到手动选择：保留之前的勾选，再切换当前这张卡。
            self._global = False
        if pid in self._selected:
            self._selected.discard(pid)
        else:
            self._selected.add(pid)
        self._refresh_selection_ui()

    def set_style(self, style: str) -> None:
        values = [v for v, _l, _d in STYLE_OPTIONS]
        if style in values:
            self.style_control.set_current_index(values.index(style))
            self._on_style_changed(values.index(style))

    def current_style(self) -> str:
        return self._style

    def _on_style_changed(self, index: int) -> None:
        if 0 <= index < len(STYLE_OPTIONS):
            self._style = STYLE_OPTIONS[index][0]
        self._update_style_hint()

    def _update_style_hint(self) -> None:
        desc = next((d for v, _l, d in STYLE_OPTIONS if v == self._style), "")
        self.style_hint.setText(desc)

    def build_request(self, *, force_refresh: bool = False) -> Any:
        """当前选择 → ReviewRequest；选择无效时返回 None。

        - 全局分析 → scope="global"
        - 恰好只选本人（后端 is_self）→ scope="self"
        - 其它 1 人或多人 → scope="selected"，target_player_ids 为后端原样 player_id
        """
        if AIB.AR is None or self._ctx is None:
            return None
        if self._global:
            return AIB.AR.ReviewRequest(style=self._style, scope="global", force_refresh=force_refresh)
        ids = self.selected_ids()
        if not ids:
            return None
        if self._self_id and ids == [self._self_id]:
            return AIB.AR.ReviewRequest(style=self._style, scope="self", force_refresh=force_refresh)
        return AIB.AR.ReviewRequest(
            style=self._style, scope="selected", target_player_ids=tuple(ids), force_refresh=force_refresh
        )

    def _refresh_selection_ui(self) -> None:
        for pid, card in self._cards.items():
            card.set_selected(not self._global and pid in self._selected)
            card.set_included(self._global)
        friendly = self._team_ids("friendly")
        enemy = self._team_ids("enemy")
        sel = self._selected
        has_players = bool(self._players)
        self.quick_self.setEnabled(bool(self._self_id))
        self.quick_friendly.setEnabled(bool(friendly))
        self.quick_enemy.setEnabled(bool(enemy))
        self.quick_global.setEnabled(has_players)
        self.quick_self.setChecked(not self._global and bool(self._self_id) and sel == {self._self_id})
        self.quick_friendly.setChecked(not self._global and bool(friendly) and sel == friendly)
        self.quick_enemy.setChecked(not self._global and bool(enemy) and sel == enemy)
        self.quick_global.setChecked(self._global)
        if not has_players:
            self.count_label.setText("")
        elif self._global:
            self.count_label.setText(f"全局分析 · 本局 {len(self._players)} 名玩家")
        elif sel:
            self.count_label.setText(f"已选 {len(sel)} / {len(self._players)} 人")
        else:
            self.count_label.setText("请至少选择一名玩家")
        self._update_buttons()

    # ------------------------------------------------------------------ 运行
    def is_running(self) -> bool:
        return self._active_run != 0

    def is_draining(self) -> bool:
        """已点击停止、但后台 Worker 还没退出（等待下一个流式分片检查取消标记）。"""
        return not self.is_running() and bool(self._workers)

    def _update_buttons(self) -> None:
        running = self.is_running()
        valid = self.build_request() is not None
        if running:
            self.start_btn.setText("停止生成")
            self.start_btn.setProperty("variant", "lose")
            self.start_btn.setEnabled(True)
        elif self.is_draining():
            self.start_btn.setText("正在停止…")
            self.start_btn.setProperty("variant", "primary")
            self.start_btn.setEnabled(False)
        else:
            self.start_btn.setText("✦ 开始 AI 锐评")
            self.start_btn.setProperty("variant", "primary")
            self.start_btn.setEnabled(valid)
        self.start_btn.style().unpolish(self.start_btn)
        self.start_btn.style().polish(self.start_btn)
        self.regen_btn.setEnabled(not running and not self.is_draining() and valid and self._has_result)

    def _on_start_clicked(self) -> None:
        if self.is_running():
            self.stop_review()
        else:
            self.start_review()

    def start_review(self, *, force_refresh: bool = False) -> bool:
        if self.is_running() or self.is_draining() or self._service is None:
            return False
        request = self.build_request(force_refresh=force_refresh)
        if request is None:
            return False
        try:
            self._service.validate_request(self._ctx, request)
        except Exception as exc:  # noqa: BLE001 - 选择无效等，不发请求
            self.message.show_message(AIB.friendly_error(exc), "error")
            return False
        if bool(getattr(self._service, "busy", False)):
            self.message.show_message("上一次 AI 锐评正在结束，请稍候再试。", "warn")
            return False

        self._run_seq += 1
        run_id = self._run_seq
        self._active_run = run_id
        self._cancel_token = AIB.AR.CancellationToken()
        self._text_parts = []
        self._cache_hit = False
        self.output.clear()
        self.message.clear()
        self.status.show_message(STATUS_TEXT["requesting"], "info")

        worker = AIB.ReviewStreamWorker(run_id, self._service, self._ctx, request, self._cancel_token)
        worker.signals.delta.connect(self._on_delta)
        worker.signals.status.connect(self._on_status)
        worker.signals.done.connect(self._on_done)
        worker.signals.failed.connect(self._on_failed)
        worker.signals.cancelled.connect(self._on_cancelled)
        worker.signals.finished.connect(self._on_worker_finished)
        self._workers[run_id] = worker
        self._update_buttons()
        worker.start()
        return True

    def stop_review(self) -> None:
        """停止生成：只通过 CancellationToken；已收到的文本保留。"""
        if not self.is_running():
            return
        if self._cancel_token is not None:
            self._cancel_token.cancel()
        self._finish_run()
        self._has_result = bool("".join(self._text_parts).strip()) or self._has_result
        self._flush_output()
        self.status.show_message("已停止生成", "info")
        self._update_buttons()

    def cancel_running(self) -> None:
        if self.is_running():
            self.stop_review()

    def _finish_run(self) -> None:
        # 之后该 run 的迟到信号全部忽略；Worker 自己在后台结束。
        self._active_run = 0
        self._cancel_token = None

    def _on_delta(self, run_id: int, text: str) -> None:
        if run_id != self._active_run:
            return
        self._text_parts.append(str(text))
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def _on_status(self, run_id: int, phase: str) -> None:
        if run_id != self._active_run:
            return
        if phase == "cache_hit":
            self._cache_hit = True
        if phase == "done":
            return  # 完成文案由 _on_done 统一给出
        self.status.show_message(STATUS_TEXT.get(phase, "正在处理…"), "info")

    def _on_done(self, run_id: int, result: Any) -> None:
        if run_id != self._active_run:
            return
        text = str(getattr(result, "text", "") or "")
        if text:
            self._text_parts = [text]
        cached = bool(getattr(result, "cached", False)) or self._cache_hit
        self._finish_run()
        self._has_result = True
        self._flush_output(force_bottom=False)
        note = "完成 · 已读取缓存" if cached else "完成"
        if str(getattr(result, "finish_reason", "") or "") == "length":
            note += "（输出达到长度上限）"
        self.status.show_message(note, "ok")
        self._update_buttons()

    def _on_failed(self, run_id: int, message: str) -> None:
        if run_id != self._active_run:
            return
        self._finish_run()
        self._has_result = bool("".join(self._text_parts).strip()) or self._has_result
        self._flush_output()
        self.status.show_message("锐评失败", "error")
        self.message.show_message(AIB.scrub(message), "error")
        self._update_buttons()

    def _on_cancelled(self, run_id: int) -> None:
        if run_id != self._active_run:
            return
        self._finish_run()
        self._flush_output()
        self.status.show_message("已停止生成", "info")
        self._update_buttons()

    def _on_worker_finished(self, run_id: int) -> None:
        self._workers.pop(run_id, None)
        if run_id == self._active_run:
            # 兜底：正常情况下 done/failed/cancelled 已经收尾。
            self._finish_run()
        self._update_buttons()

    # ------------------------------------------------------------------ 输出
    def output_text(self) -> str:
        return "".join(self._text_parts)

    def _flush_output(self, force_bottom: Optional[bool] = None) -> None:
        self._flush_timer.stop()
        text = self.output_text()
        bar = self.output.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        previous = bar.value()
        # 模型正文通常是 Markdown；QTextBrowser 只读渲染，不打开任何链接。
        self.output.setMarkdown(text)
        if force_bottom is False:
            bar.setValue(0 if not previous else min(previous, bar.maximum()))
        elif at_bottom or force_bottom:
            bar.setValue(bar.maximum())
        else:
            bar.setValue(min(previous, bar.maximum()))


__all__ = [
    "AIReviewEntryCard",
    "AIReviewPage",
    "PlayerCardGrid",
    "PlayerPickCard",
    "STYLE_OPTIONS",
    "group_players",
    "player_kda_line",
    "player_stat_line",
]
