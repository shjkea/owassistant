# -*- coding: utf-8 -*-
"""国服战绩页专用可视组件。

视觉基准是 ui_reference/ 下两张参考图：深灰黑底、细分割线、小圆角、
地图图像作为卡片主体、数据字体紧凑、次级字段降亮度。

复杂卡片全部用自定义 QWidget + paintEvent 绘制，而不是堆 QSS。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QFontMetricsF,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from owstats.game_catalog import resolve_hero

from . import owstats_data as D
from .assets import AssetLibrary, AvatarCache, normalize_dpr
from .theme import C, ROW_CHIP_PX, ROW_LINE1_PX, ROW_LINE2_PX

# 参考图里的状态色：克制的绿 / 红，不满屏彩色。
WIN_COLOR = QColor("#3ddc97")
LOSE_COLOR = QColor("#ff6b6b")
DRAW_COLOR = QColor("#9aa4b4")
PARTY_COLOR = QColor("#9d7bff")
SURFACE = QColor(C.SURFACE)
SURFACE_2 = QColor(C.SURFACE_2)
BORDER = QColor(C.BORDER)
TEXT = QColor(C.TEXT)
TEXT_SUB = QColor(C.TEXT_SUB)
TEXT_DIM = QColor(C.TEXT_DIM)
ACCENT = QColor(C.ACCENT)


def result_color(result: str) -> QColor:
    if result == "win":
        return WIN_COLOR
    if result == "loss":
        return LOSE_COLOR
    return DRAW_COLOR


def result_text(result: str) -> str:
    return {"win": "胜", "loss": "负", "draw": "平"}.get(result, "—")


def _font(size: int, *, bold: bool = False) -> QFont:
    font = QFont()
    font.setPixelSize(size)
    font.setBold(bold)
    return font


def _elide(painter: QPainter, text: str, width: int) -> str:
    metrics = QFontMetrics(painter.font())
    return metrics.elidedText(text, Qt.ElideRight, max(0, width))


# ---------------- HiDPI 位图辅助 ----------------
# AssetLibrary 返回的 pixmap 已按「逻辑尺寸 × dpr」生成并 setDevicePixelRatio(dpr)，
# 所以 pix.width()/height() 是 backing store 物理像素，不能再直接参与逻辑布局；
# 定位 / 居中一律用 deviceIndependentSize() 取回的逻辑尺寸。
def _widget_dpr(widget: QWidget) -> float:
    """控件当前所在屏幕的 devicePixelRatio（跨屏移动后自动变化）。"""
    return normalize_dpr(widget.devicePixelRatioF())


def _lw(pix: QPixmap) -> int:
    """pixmap 逻辑宽度（整数，与旧的 1:1 pixmap 宽度一致）。"""
    return int(round(pix.deviceIndependentSize().width()))


def _lh(pix: QPixmap) -> int:
    """pixmap 逻辑高度。"""
    return int(round(pix.deviceIndependentSize().height()))


# 中文字形上下最占地方的样本：段位名 + 常见头衔用字。
# 微软雅黑这类 CJK 字体的汉字 boundingRect 会超出 QFontMetrics.height()
# （height() 只按 ascent+descent 算，而汉字实际墨迹更高），所以量行高时
# 必须把汉字的 boundingRect 一起纳入，否则中文会被上下各切掉 1px。
CJK_PROBE = "未定级宗师翡翠钻石白金黄金白银青铜大师英杰颜"

# 文字行的上下安全余量（单侧）。给足中文墨迹高度之外的呼吸空间，
# 不用负 margin / 缩小字号这类歪招。
TEXT_SAFE_PAD = 2


def text_line_height(font: QFont, *, safe: int = TEXT_SAFE_PAD, probe: str = CJK_PROBE) -> int:
    """按 QFontMetrics 算出「中文也不会被裁切」的一行文字高度。

    取 ``height()`` / ``ascent()+descent()`` / 中文 ``boundingRect()`` 三者最大值，
    再上下各留 ``safe`` px。任何文字行高都必须走这里，不允许写死数字。
    """
    metrics = QFontMetrics(font)
    need = max(
        metrics.height(),
        metrics.ascent() + metrics.descent(),
        metrics.boundingRect(probe).height(),
    )
    return int(need) + int(safe) * 2


def safe_text_rect(rect: Any, font: QFont) -> QRectF:
    """把自绘控件里的文字矩形扩成「中文安全高度」，**中心线保持不变**。

    旧实现把文字画在固定 18～20px 高的 QRect 里，drawText 会按矩形裁切；
    125% / 150% DPI 或中文字体 fallback 时墨迹高度超出矩形就会被切掉上下 1px。
    这里只把高度撑到 ``text_line_height(font)``（不会变矮），x / 宽度 / 垂直中心
    都与原矩形一致，所以 AlignVCenter 的基线位置和原来完全相同，布局不变。
    """
    r = QRectF(rect)
    height = max(r.height(), float(text_line_height(font)))
    return QRectF(r.x(), r.center().y() - height / 2.0, r.width(), height)


class ElidedLabel(QLabel):
    """空间不足时在右侧显示「…」的 QLabel。

    - ``setText`` 保存完整文本，显示文本按 ``fontMetrics().elidedText(ElideRight)``
      根据当前可用宽度计算，resize / 字体或样式变化时重新计算；
    - ``sizeHint`` 仍按完整文本给出，布局空间充足时完整显示；
    - ``minimumSizeHint`` 只要求一个「…」的宽度，空间紧张时由它先让出宽度，
      而不是被控件边界硬切掉后半截；
    - 被省略时把完整文本放进 tooltip。

    样式（objectName / QSS / 固定行高 / 对齐方式）完全沿用普通 QLabel。
    """

    def __init__(self, text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self._elided = False
        self.setSizePolicy(QSizePolicy.Preferred, self.sizePolicy().verticalPolicy())
        self.setText(text)

    def full_text(self) -> str:
        return self._full_text

    def is_elided(self) -> bool:
        return self._elided

    def setText(self, text: str) -> None:  # noqa: N802
        self._full_text = str(text or "")
        self._apply_elide()
        self.updateGeometry()

    def _full_width(self) -> int:
        """完整文本所需宽度：用 QFontMetricsF 的小数 advance 向上取整，
        与 elidedText 的内部判定同源，避免「宽度 == sizeHint 仍被省略」。"""
        if not self._full_text:
            return 0
        return int(math.ceil(QFontMetricsF(self.font()).horizontalAdvance(self._full_text))) + 1

    def _apply_elide(self) -> None:
        width = self.contentsRect().width()
        full = self._full_text
        shown = full
        if full and width > 0 and self._full_width() > width:
            shown = self.fontMetrics().elidedText(full, Qt.ElideRight, width)
        elided = shown != full
        if elided != self._elided:
            self._elided = elided
            self.setToolTip(full if elided else "")
        if shown != super().text():
            super().setText(shown)

    def sizeHint(self) -> QSize:  # noqa: N802
        base = super().sizeHint()
        margins = self.contentsMargins()
        width = self._full_width() + margins.left() + margins.right()
        return QSize(max(width, 1), base.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        base = super().minimumSizeHint()
        margins = self.contentsMargins()
        width = self.fontMetrics().horizontalAdvance("…") + margins.left() + margins.right()
        return QSize(width if self._full_text else 0, base.height())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_elide()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange):
            # QSS 字号在 polish 时才生效：字体变化后完整宽度随之变化，需要重新通知布局。
            self.updateGeometry()
            self._apply_elide()


class IconTextBadge(QWidget):
    """小图标 + 文本的通用徽标。职责 / 段位 / 地图类型统一使用。

    高度规则（中文不被裁切的关键）：

    - 文字所需高度一律由 ``text_line_height()`` 按 ``QFontMetrics`` 推出，
      含中文墨迹高度与上下安全余量，绝不写死像素；
    - 控件高度取 ``max(图标高, 文字行高)``，所以图标只可能把行**撑高**，
      永远不会把文字行**压矮**；
    - 外部（如详情玩家行）可以用 ``set_line_height()`` 把高度钉到与同一行
      其它 QLabel 完全一致，这样 drawText(AlignVCenter) 与 QLabel 的
      垂直居中公式同源，基线自然对齐。
    """

    # 文字左右留白（px）。默认 0 保持既有徽标观感；RankBadge 覆盖。
    TEXT_PAD = 0
    # True：minimumSizeHint 等于完整内容宽度，布局不能把文字压成省略号。
    KEEP_FULL_TEXT = False

    def __init__(
        self,
        library: AssetLibrary,
        kind: str,
        *,
        icon_size: int = 15,
        font_size: int = 12,
        color: Optional[QColor] = None,
        bold: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._library = library
        self._kind = kind
        self._icon_size = int(icon_size)
        self._font_size = int(font_size)
        self._color = color or TEXT
        self._bold = bool(bold)
        self._icon_key = ""
        self._text = ""
        self._pixmap = QPixmap()
        self._pix_dpr = 0.0
        # 外部钉死的行高（None = 自适应）。
        self._forced_height: Optional[int] = None
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._apply_height()
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    # ---------------- 高度 ----------------
    def text_font(self) -> QFont:
        return _font(self._font_size, bold=self._bold)

    def natural_height(self) -> int:
        """自适应高度：中文安全的文字行高与图标高度取大者。"""
        return max(self._icon_size, text_line_height(self.text_font()))

    def _apply_height(self) -> None:
        self.setFixedHeight(int(self._forced_height or self.natural_height()))

    def set_line_height(self, height: int) -> None:
        """把徽标高度钉到与同一文字行其它控件一致（用于严格基线对齐）。"""
        value = max(1, int(height))
        if value == self._forced_height:
            return
        self._forced_height = value
        self._apply_height()
        self.updateGeometry()
        self.update()

    def set_data(self, icon_key: Any, text: str, color: Optional[QColor] = None) -> None:
        self._icon_key = str(icon_key or "")
        self._text = str(text or "")
        if color is not None:
            self._color = color
        self._pixmap = self._resolve_icon()
        self.updateGeometry()
        self.update()

    def set_icon_size(self, size: int) -> None:
        self._icon_size = int(size)
        self._apply_height()
        self._pixmap = self._resolve_icon()
        self.updateGeometry()
        self.update()

    def _resolve_icon(self) -> QPixmap:
        dpr = self._pix_dpr = _widget_dpr(self)
        if not self._icon_key:
            return QPixmap()
        if self._kind == "role":
            return self._library.role_icon(self._icon_key, self._icon_size, dpr=dpr)
        if self._kind == "rank":
            return self._library.rank_icon(self._icon_key, self._icon_size, dpr=dpr)
        if self._kind == "map_mode":
            return self._library.map_mode_icon(self._icon_key, self._icon_size, dpr=dpr)
        return QPixmap()

    def _sync_dpr(self) -> None:
        """控件所在屏幕的 DPR 变化后（首次显示 / 跨屏移动），按新 DPR 重新取图。"""
        if self._icon_key and _widget_dpr(self) != self._pix_dpr:
            self._pixmap = self._resolve_icon()

    def _text_width(self) -> int:
        """文字真实所需宽度：advance 与墨迹 boundingRect 取大者，再加左右留白。"""
        if not self._text:
            return 0
        metrics = QFontMetrics(self.text_font())
        width = max(metrics.horizontalAdvance(self._text), metrics.boundingRect(self._text).width())
        return int(width) + self.TEXT_PAD * 2

    def sizeHint(self) -> QSize:  # noqa: N802
        width = self._text_width()
        if not self._pixmap.isNull():
            width += self._icon_size + 5
        return QSize(max(width, 1), self.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        if self.KEEP_FULL_TEXT:
            return self.sizeHint()
        return QSize(1, self.height())

    def paintEvent(self, event) -> None:  # noqa: N802
        self._sync_dpr()
        if not self._text and self._pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        x = 0
        if not self._pixmap.isNull():
            # 图标严格按控件高度居中：素材自带的透明边距只影响图标自身观感，
            # 不参与文字 baseline 计算，也不会让整行上下漂移。
            y = (self.height() - _lh(self._pixmap)) // 2
            painter.drawPixmap(x, y, self._pixmap)
            x += self._icon_size + 5
        x += self.TEXT_PAD
        painter.setFont(self.text_font())
        painter.setPen(self._color)
        # 文字铺满控件全高 + AlignVCenter：与同高度 QLabel 的居中公式一致，
        # 因此同一行的 QLabel 与徽标文字基线相同。
        width = max(0, self.width() - x - self.TEXT_PAD)
        painter.drawText(
            QRect(x, 0, width, self.height()),
            Qt.AlignLeft | Qt.AlignVCenter,
            self._text if self.KEEP_FULL_TEXT else _elide(painter, self._text, width),
        )
        painter.end()


class RoleBadge(IconTextBadge):
    def __init__(self, library: AssetLibrary, **kwargs: Any) -> None:
        super().__init__(library, "role", **kwargs)

    def set_role(self, role_type: Any, role_name: str) -> None:
        self.set_data(role_type or role_name, role_name)


class RankBadge(IconTextBadge):
    """段位徽标。段位是比头衔更重要的信息：宽度按真实字体 metrics 算出，
    ``minimumSizeHint == sizeHint`` 且水平 Fixed，布局空间紧张时由旁边的
    头衔（ElidedLabel）先省略，段位中文（翡翠 / 白金 / 宗师 …）永远完整。"""

    TEXT_PAD = 2
    KEEP_FULL_TEXT = True

    def __init__(self, library: AssetLibrary, **kwargs: Any) -> None:
        super().__init__(library, "rank", **kwargs)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def set_rank(self, rank: Dict[str, Any]) -> None:
        info = dict(rank or {})
        display = str(info.get("display") or D.UNRANKED_TEXT)
        key = info.get("tier") if info.get("is_ranked") else "unranked"
        color = TEXT if info.get("is_ranked") else TEXT_DIM
        self.set_data(key or "unranked", display, color)


class MapModeBadge(IconTextBadge):
    """地图类型：视觉权重低于地图名，所以默认小号 + 次级色。"""

    def __init__(self, library: AssetLibrary, **kwargs: Any) -> None:
        kwargs.setdefault("icon_size", 13)
        kwargs.setdefault("font_size", 11)
        kwargs.setdefault("color", TEXT_SUB)
        super().__init__(library, "map_mode", **kwargs)

    def set_mode(self, instance_type: Any, text: str) -> None:
        self.set_data(instance_type or text, text)


def _draw_portrait(
    painter: QPainter,
    pixmap: QPixmap,
    box: QRect,
    radius: float,
    *,
    border: Optional[QColor] = None,
) -> None:
    """圆角裁切绘制一张英雄头像。素材缺失时只画中性底色，不自绘假图。"""
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(box), radius, radius)
    painter.save()
    painter.setClipPath(clip)
    if pixmap.isNull():
        painter.fillRect(box, SURFACE_2)
    else:
        painter.drawPixmap(
            box.x() + (box.width() - _lw(pixmap)) // 2,
            box.y() + (box.height() - _lh(pixmap)) // 2,
            pixmap,
        )
    painter.restore()
    painter.setPen(QPen(border or BORDER, 1.2))
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(clip)


class HeroPortrait(QWidget):
    """英雄头像列。

    一局只用了一个英雄时只画一张大头像；用了多个英雄时按
    ``[主英雄大头像] [次英雄小头像] …`` 横向排列。主英雄由数据层排序决定
    （有可靠使用时间时用得最多的在前，否则保持后端顺序）。

    ``stacked=True``（单局详情 PlayerRow 使用）时改为紧凑组合，最多 3 个英雄：

        ┌─────────┬────┐
        │         │ ② │
        │   ①     ├────┤
        │         │ ③ │
        └─────────┴────┘

    右侧只有一列小头像，高度不超过主头像，所以不会撑高玩家行；
    只有 2 个英雄时第二英雄在右侧垂直居中，不画空槽。

    所有 QPixmap 都由 AssetLibrary 按 (path, w, h) 缓存，多英雄不会反复读盘。
    """

    MAX_SECONDARY = 3
    # stacked 组合：右侧一列上下两个小头像。
    STACKED_MAX_SECONDARY = 2

    def __init__(
        self,
        library: AssetLibrary,
        size: int = 56,
        *,
        radius: int = 10,
        secondary_size: Optional[int] = None,
        secondary_gap: int = 3,
        stacked: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._library = library
        self._size = int(size)
        self._radius = int(radius)
        self._stacked = bool(stacked)
        self._max_secondary = self.STACKED_MAX_SECONDARY if self._stacked else self.MAX_SECONDARY
        self._gap = int(secondary_gap)
        default_small = max(12, round(self._size * 0.46))
        small = int(secondary_size if secondary_size else default_small)
        if self._stacked:
            # 两个小头像 + 中间间距必须装进主头像高度内。
            small = min(small, (self._size - self._gap) // 2)
        self._secondary_size = small
        self._pixmap = QPixmap()
        self._secondaries: List[QPixmap] = []
        # 取图参数（英雄候选键）：DPR 变化时按同一参数重新取图。
        self._main_args: Tuple[Any, ...] = ()
        self._second_args: List[Tuple[Any, ...]] = []
        self._loaded = False
        self._pix_dpr = 0.0
        self._border: Optional[QColor] = None
        # 预留的次英雄位数。默认 0：单英雄时不留空洞，紧贴玩家信息列（记分板观感）。
        # 一支队伍里存在多英雄行时，由 TeamPanel 统一调高，保证同队各行左边缘对齐。
        self._reserved_slots = 0
        self.setFixedSize(self._reserved_width(), self._size)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def _reserved_width(self) -> int:
        slots = max(self._reserved_slots, len(self._secondaries))
        if self._stacked:
            # 次英雄上下叠放，只占一列。
            slots = 1 if slots > 0 else 0
        return self._size + slots * (self._secondary_size + self._gap)

    def is_stacked(self) -> bool:
        return self._stacked

    def secondary_boxes(self) -> List[QRect]:
        """次英雄小头像的绘制区域（与 paintEvent 同一套公式，供测试校验布局）。"""
        count = len(self._secondaries)
        boxes: List[QRect] = []
        if not count:
            return boxes
        ss = self._secondary_size
        x = self._size + self._gap
        if self._stacked:
            if count == 1:
                tops = [(self._size - ss) // 2]
            else:
                top0 = (self._size - (ss * 2 + self._gap)) // 2
                tops = [top0, top0 + ss + self._gap]
            return [QRect(x, top, ss, ss) for top in tops[:count]]
        top = (self._size - ss) // 2
        for _ in range(count):
            boxes.append(QRect(x, top, ss, ss))
            x += ss + self._gap
        return boxes

    def set_reserved_slots(self, slots: int) -> None:
        """统一预留次英雄位，用于让同一队伍各行的玩家信息列左对齐。"""
        value = max(0, min(int(slots), self._max_secondary))
        if value == self._reserved_slots:
            return
        self._reserved_slots = value
        self.setFixedSize(self._reserved_width(), self._size)
        self.updateGeometry()
        self.update()

    def hero_count(self) -> int:
        return (1 if not self._pixmap.isNull() else 0) + len(self._secondaries)

    def _load_pixmaps(self) -> None:
        dpr = self._pix_dpr = _widget_dpr(self)
        self._pixmap = self._library.hero_icon(self._size, *self._main_args, dpr=dpr)
        self._secondaries = [
            self._library.hero_icon(self._secondary_size, *args, dpr=dpr) for args in self._second_args
        ]
        self._loaded = True

    def set_hero(self, *candidates: Any, border: Optional[QColor] = None) -> None:
        """单英雄入口，保持既有调用方式。"""
        self._main_args = tuple(candidates)
        self._second_args = []
        self._load_pixmaps()
        self._border = border
        self.setFixedSize(self._reserved_width(), self._size)
        self.update()

    def set_heroes(self, entries: Sequence[Dict[str, Any]], *, border: Optional[QColor] = None) -> None:
        """多英雄入口。entries 由 owstats_data.hero_entries 产出，已排好序。"""
        rows = [dict(e) for e in entries or []]
        if not rows:
            self._main_args = ()
            self._second_args = []
        else:
            main = rows[0]
            self._main_args = (main.get("hero_name"), main.get("hero_name_raw"), main.get("hero_guid"))
            self._second_args = [
                (row.get("hero_name"), row.get("hero_name_raw"), row.get("hero_guid"))
                for row in rows[1:1 + self._max_secondary]
            ]
        self._load_pixmaps()
        self._border = border
        names = [str(r.get("hero_name") or r.get("hero_name_raw") or "").strip() for r in rows]
        names = [n for n in names if n]
        self.setToolTip(" · ".join(names) if len(names) > 1 else "")
        self.setFixedSize(self._reserved_width(), self._size)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        if self._loaded and _widget_dpr(self) != self._pix_dpr:
            self._load_pixmaps()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        main_box = QRect(0, 0, self._size, self._size)
        _draw_portrait(painter, self._pixmap, main_box.adjusted(0, 0, -1, -1), self._radius, border=self._border)
        small_radius = max(3.0, self._radius * 0.6)
        for pix, box in zip(self._secondaries, self.secondary_boxes()):
            _draw_portrait(painter, pix, box.adjusted(0, 0, -1, -1), small_radius, border=self._border)
        painter.end()


class RoleIcon(QWidget):
    """OW 记分板式职责栏：窄、固定宽度、上下顶满整行。

    参考 ui_reference/scoreboard_role_reference.jpg：职责所在区域是一条贴满行高的
    窄栏，而不是漂在行中间的悬浮小 badge，所以这里：

    - 宽度固定 ``COLUMN_WIDTH``（约 30px），高度随玩家行拉满（上下 margin 为 0）；
    - 自带独立背景层级（比行背景略深）+ 右侧一条细分隔线，和玩家标签 / badge 区分；
    - 职责图标在栏内水平 + 垂直居中；
    - 图标素材仍严格走 AssetLibrary（assets/roles/*.png），flex.png 只代表开放职责，
      职责未知时画中性短横，绝不退回 flex。
    """

    COLUMN_WIDTH = 32

    def __init__(
        self,
        library: AssetLibrary,
        size: int = 18,
        *,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._library = library
        self._size = int(size)
        self._pixmap = QPixmap()
        self._role_args: Optional[Tuple[Any, Any]] = None
        self._pix_dpr = 0.0
        self._role_name = ""
        self._known = False
        self.setFixedWidth(self.COLUMN_WIDTH)
        # 高度交给布局拉满：职责栏必须从玩家行顶部贴到底部。
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def _load_pixmap(self) -> None:
        dpr = self._pix_dpr = _widget_dpr(self)
        if self._role_args is None:
            self._pixmap = QPixmap()
            return
        role, name = self._role_args
        self._pixmap = self._library.role_icon(role, self._size, name, dpr=dpr)

    def set_role(self, role_type: Any, role_name: Any = "") -> None:
        # 职责未知时不取图标：flex.png 只代表开放职责，绝不作为未知 fallback。
        key = self._library.role_key(role_type, role_name)
        self._known = bool(key)
        self._role_args = (role_type or role_name, role_name) if self._known else None
        self._load_pixmap()
        name = str(role_name or "").strip()
        self._role_name = name
        self.setToolTip(name if self._known else "职责未知")
        self.update()

    def role_name(self) -> str:
        """当前职责中文名。图标本身不带文字，名称走 tooltip。"""
        return self._role_name

    def paintEvent(self, event) -> None:  # noqa: N802
        if self._role_args is not None and _widget_dpr(self) != self._pix_dpr:
            self._load_pixmap()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        # 整栏铺满行高的独立背景层级（略深），不做圆角、不留上下空白。
        painter.fillRect(self.rect(), QColor(8, 10, 14, 132))
        painter.fillRect(QRect(self.width() - 1, 0, 1, self.height()), QColor(255, 255, 255, 20))
        if self._pixmap.isNull():
            # 未知职责：画一个中性短横，而不是显示 flex.png。
            painter.setPen(QPen(TEXT_DIM, 1.4))
            mid = self.height() / 2.0
            painter.drawLine(QPoint(int(self.width() * 0.3), int(mid)),
                             QPoint(int(self.width() * 0.7), int(mid)))
            painter.end()
            return
        painter.drawPixmap(
            (self.width() - _lw(self._pixmap)) // 2,
            (self.height() - _lh(self._pixmap)) // 2,
            self._pixmap,
        )
        painter.end()


class PlayerAvatar(QWidget):
    """玩家网络头像：圆形，缓存命中前显示占位图，加载完成后自动重绘。"""

    def __init__(
        self,
        cache: AvatarCache,
        library: AssetLibrary,
        size: int = 40,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._cache = cache
        self._library = library
        self._size = int(size)
        self._url = ""
        self.setFixedSize(self._size, self._size)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        cache.loaded.connect(self._on_loaded)

    def set_url(self, url: str) -> None:
        self._url = str(url or "").strip()
        self.update()

    def _on_loaded(self, url: str) -> None:
        if url == self._url:
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        # 头像按控件当前屏幕 DPR 取图；缓存 key 含 DPR，跨屏后自动换成对应分辨率。
        dpr = _widget_dpr(self)
        pix = self._cache.peek(self._url, self._size, dpr=dpr) if self._url else None
        path = QPainterPath()
        path.addEllipse(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1))
        if pix is not None and not pix.isNull():
            painter.setClipPath(path)
            painter.drawPixmap(0, 0, pix)
            painter.setClipping(False)
        else:
            painter.setBrush(SURFACE_2)
            painter.setPen(Qt.NoPen)
            painter.drawPath(path)
            placeholder = self._library.placeholder_portrait(self._size, dpr=dpr)
            if not placeholder.isNull():
                painter.setClipPath(path)
                painter.drawPixmap(0, 0, placeholder)
                painter.setClipping(False)
        painter.setPen(QPen(BORDER, 1.2))
        painter.drawPath(path)
        painter.end()


class RecentFormStrip(QWidget):
    """最近 N 场走势：一排紧凑的小圆角方块，纯 paintEvent 绘制。

    每个小格代表一场比赛，颜色区分 胜 / 负 / 平。
    显示顺序为「最新 → 较早」，最左边一格是最新一场。

    刻意不实现任何交互：没有 Tooltip、没有 Hover、不可点击，
    也绝不为小格发起任何网络请求（颜色只依赖已有的 result 字段）。
    """

    CELL = 11
    GAP = 3
    RADIUS = 2.5

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._results: List[str] = []
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setFixedHeight(self.CELL)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        # 明确关掉一切交互：不接收鼠标事件、不显示 tooltip。
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setMouseTracking(False)
        self.setFocusPolicy(Qt.NoFocus)

    def set_results(self, results: Sequence[str]) -> None:
        """results 必须已经是「最新 → 较早」显示顺序。"""
        self._results = [str(r or "unknown") for r in results or []]
        self.updateGeometry()
        self.update()

    def results(self) -> List[str]:
        return list(self._results)

    def sizeHint(self) -> QSize:  # noqa: N802
        count = max(len(self._results), 1)
        width = count * self.CELL + max(0, count - 1) * self.GAP
        return QSize(width, self.CELL)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    @staticmethod
    def _cell_colors(result: str) -> Tuple[QColor, QColor]:
        """(填充色, 描边色)。克制的深色底配色，不用高饱和大色块。"""
        if result == "win":
            fill = QColor(WIN_COLOR)
            fill.setAlpha(210)
            edge = QColor(WIN_COLOR)
            edge.setAlpha(120)
            return fill, edge
        if result == "loss":
            fill = QColor(LOSE_COLOR)
            fill.setAlpha(190)
            edge = QColor(LOSE_COLOR)
            edge.setAlpha(110)
            return fill, edge
        # 平局：中性灰，明显低于胜负的视觉权重。
        fill = QColor(DRAW_COLOR)
        fill.setAlpha(120)
        edge = QColor(DRAW_COLOR)
        edge.setAlpha(90)
        return fill, edge

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._results:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        x = 0.5
        top = (self.height() - self.CELL) / 2.0
        for result in self._results:
            fill, edge = self._cell_colors(result)
            box = QRectF(x, top, self.CELL - 1, self.CELL - 1)
            path = QPainterPath()
            path.addRoundedRect(box, self.RADIUS, self.RADIUS)
            painter.fillPath(path, fill)
            painter.setPen(QPen(edge, 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
            x += self.CELL + self.GAP
        painter.end()


class RecentFormPanel(QFrame):
    """战绩主页的「近期状态」紧凑区域。

    展示当前赛季最近最多 N 场（预设竞技 + 开放竞技 + 快速合并）的胜率、
    战绩文本与小型走势条。没有比赛数据时显示占位文案，绝不显示 0%。
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("RecentFormPanel")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 9)
        lay.setSpacing(5)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        self.caption = QLabel("近期状态")
        self.caption.setObjectName("RecentFormCaption")
        head.addWidget(self.caption, 0)
        self.record = QLabel("")
        self.record.setObjectName("RecentFormRecord")
        head.addWidget(self.record, 0)
        head.addStretch(1)
        self.rate = QLabel("")
        self.rate.setObjectName("RecentFormRate")
        self.rate.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        head.addWidget(self.rate, 0)
        lay.addLayout(head)

        self.strip = RecentFormStrip(self)
        lay.addWidget(self.strip, 0, Qt.AlignLeft)

        self.empty = QLabel("暂无本赛季比赛数据")
        self.empty.setObjectName("RecentFormEmpty")
        lay.addWidget(self.empty)

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.set_summary({})

    def set_summary(self, summary: Dict[str, Any]) -> None:
        info = dict(summary or {})
        total = int(info.get("total") or 0)
        if total <= 0:
            # 没有任何竞技 / 快速比赛：显示占位文案，不计算也不展示 0%。
            self.caption.setText("近期状态")
            self.record.setText("")
            self.record.setVisible(False)
            self.rate.setText("")
            self.rate.setVisible(False)
            self.strip.set_results([])
            self.strip.setVisible(False)
            self.empty.setVisible(True)
            return
        self.caption.setText(f"最近{total}场")
        self.record.setText(D.recent_form_record_text(info))
        self.record.setVisible(True)
        self.rate.setText(D.recent_form_rate_text(info))
        self.rate.setVisible(True)
        # 仅反转展示副本，数据层统计与时间顺序保持不变。
        self.strip.set_results(list(reversed(info.get("results") or [])))
        self.strip.setVisible(True)
        self.empty.setVisible(False)


class SectionHeader(QWidget):
    """参考图里的小节标题：橙色文字 + 下方短橙线。"""

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._text = text
        self.setFixedHeight(30)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_text(self, text: str) -> None:
        self._text = str(text or "")
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setFont(_font(13, bold=True))
        painter.setPen(ACCENT)
        painter.drawText(QRect(0, 0, self.width(), 20), Qt.AlignLeft | Qt.AlignVCenter, self._text)
        metrics = QFontMetrics(painter.font())
        width = min(metrics.horizontalAdvance(self._text), 72)
        painter.setPen(QPen(ACCENT, 2))
        painter.drawLine(0, 25, max(28, width), 25)
        painter.end()


class SeasonSelector(QWidget):
    """动态赛季切换。赛季号完全来自后端，界面不保存任何赛季常量。"""

    seasonChanged = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._seasons: List[int] = []
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: List[QToolButton] = []
        self._layout = lay
        self._group.idClicked.connect(self._on_clicked)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    def set_seasons(self, seasons: Sequence[int], current: Optional[int] = None) -> None:
        wanted = [int(s) for s in seasons]
        if wanted == self._seasons:
            if current is not None:
                self.set_current(current)
            return
        for btn in self._buttons:
            self._group.removeButton(btn)
            self._layout.removeWidget(btn)
            btn.deleteLater()
        self._buttons = []
        self._seasons = wanted
        for index, season in enumerate(wanted):
            btn = QToolButton(self)
            btn.setObjectName("SeasonChip")
            btn.setText(D.season_text(season))
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.NoFocus)
            self._group.addButton(btn, index)
            self._layout.addWidget(btn)
            self._buttons.append(btn)
        if self._buttons:
            target = current if current in wanted else wanted[0]
            self.set_current(target)

    def set_current(self, season: int) -> None:
        if season in self._seasons:
            self._buttons[self._seasons.index(int(season))].setChecked(True)

    def current(self) -> Optional[int]:
        index = self._group.checkedId()
        if 0 <= index < len(self._seasons):
            return self._seasons[index]
        return None

    def _on_clicked(self, index: int) -> None:
        if 0 <= index < len(self._seasons):
            self.seasonChanged.emit(self._seasons[index])


class ChipBar(QWidget):
    """一排轻量筛选 chip（参考图“全部 / 预设 5v5 / 开放 6v6”一行）。"""

    selected = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._values: List[str] = []
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: List[QToolButton] = []
        self._group.idClicked.connect(self._on_clicked)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    def set_options(self, options: Sequence[Tuple[str, str]], current: str = "") -> None:
        for btn in self._buttons:
            self._group.removeButton(btn)
            self._layout.removeWidget(btn)
            btn.deleteLater()
        self._buttons = []
        self._values = [str(value) for value, _label in options]
        for index, (_value, label) in enumerate(options):
            btn = QToolButton(self)
            btn.setObjectName("FilterChip")
            btn.setText(str(label))
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.NoFocus)
            self._group.addButton(btn, index)
            self._layout.addWidget(btn)
            self._buttons.append(btn)
        if self._buttons:
            target = current if current in self._values else self._values[0]
            self.set_current(target)

    def set_current(self, value: str) -> None:
        if value in self._values:
            self._buttons[self._values.index(value)].setChecked(True)

    def current(self) -> str:
        index = self._group.checkedId()
        return self._values[index] if 0 <= index < len(self._values) else ""

    def _on_clicked(self, index: int) -> None:
        if 0 <= index < len(self._values):
            self.selected.emit(self._values[index])


class StatChip(QWidget):
    """参考图顶部“21 / 收到赞赏”那种小数字块。"""

    def __init__(self, caption: str, value: str = "—", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._caption = caption
        self._value = value
        self._accent = ACCENT
        self.setMinimumSize(96, 62)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(62)

    def set_value(self, value: str, color: Optional[QColor] = None) -> None:
        self._value = str(value)
        if color is not None:
            self._accent = color
        self.update()

    def set_caption(self, caption: str) -> None:
        self._caption = str(caption)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        path = QPainterPath()
        path.addRoundedRect(rect, 6, 6)
        painter.fillPath(path, SURFACE_2)
        painter.setPen(QPen(BORDER, 1))
        painter.drawPath(path)
        painter.setFont(_font(19, bold=True))
        painter.setPen(self._accent)
        painter.drawText(QRect(0, 8, self.width(), 26), Qt.AlignCenter, self._value)
        painter.setFont(_font(11))
        painter.setPen(TEXT_SUB)
        painter.drawText(QRect(0, 36, self.width(), 18), Qt.AlignCenter, self._caption)
        painter.end()


class RoleRankCard(QWidget):
    """竞技段位卡（重装 / 输出 / 支援 / 开放职责共用同一种紧凑结构）。

    四张卡在正常桌面宽度下同一行等宽排列，所以卡片必须足够矮、信息密度足够高：

        [职责图标] 重装            [段位图标] 翡翠 2
        64场 · 58%                        最佳 翡翠 2

    职责图标、段位徽章、段位文字、场次、胜率全部保留；历史最佳段位在有空间时
    显示在右下，并始终写进 tooltip，避免为它单独加一行高度。
    """

    CARD_HEIGHT = 56
    # 两行文字的垂直中心线（沿用原 QRect(6,20) / QRect(29,18) 的中心）。
    LINE1_CENTER = 16
    LINE2_CENTER = 38

    @classmethod
    def required_height(cls) -> int:
        """两行按字体 metrics 的安全高度排布后，卡片至少需要的高度。
        正常 DPI / 字体下 <= CARD_HEIGHT，卡片尺寸不变；只有字体异常高时才撑高。"""
        line2_h = text_line_height(_font(11))
        return int(cls.LINE2_CENTER + (line2_h - line2_h // 2) + 2)

    def __init__(
        self,
        library: AssetLibrary,
        *,
        wide: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._library = library
        # wide 参数保留兼容既有调用；四卡同行后不再需要「横跨整行」的变体。
        self._wide = bool(wide)
        self._data: Dict[str, Any] = {}
        self._role_icon = QPixmap()
        self._rank_icon = QPixmap()
        self._peak_icon = QPixmap()
        self._pix_dpr = 0.0
        self.setMinimumWidth(150)
        self.setFixedHeight(max(self.CARD_HEIGHT, self.required_height()))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def _load_icons(self) -> None:
        dpr = self._pix_dpr = _widget_dpr(self)
        self._role_icon = self._library.role_icon(self._data.get("role_type"), 15, dpr=dpr)
        current = dict(self._data.get("current") or {})
        self._rank_icon = self._library.rank_icon(
            current.get("tier") if current.get("is_ranked") else "unranked", 15, dpr=dpr
        )
        peak = dict(self._data.get("peak") or {})
        self._peak_icon = (
            self._library.rank_icon(peak.get("tier"), 12, dpr=dpr) if peak.get("is_ranked") else QPixmap()
        )

    def set_card(self, data: Dict[str, Any]) -> None:
        self._data = dict(data or {})
        self._load_icons()
        current = dict(self._data.get("current") or {})
        peak = dict(self._data.get("peak") or {})
        tips: List[str] = [str(self._data.get("role_name") or "")]
        matches = int(self._data.get("matches") or 0)
        if matches > 0:
            tips.append(
                f"{matches} 场 · {int(self._data.get('wins') or 0)} 胜 / "
                f"{int(self._data.get('losses') or 0)} 负"
            )
        if current.get("display"):
            tips.append(f"当前 {current.get('display')}")
        if peak.get("is_ranked") and peak.get("display"):
            tips.append(f"最佳 {peak.get('display')}")
        self.setToolTip("\n".join(t for t in tips if t))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        if self._data and _widget_dpr(self) != self._pix_dpr:
            self._load_icons()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        path = QPainterPath()
        path.addRoundedRect(rect, 6, 6)
        painter.fillPath(path, SURFACE_2)
        painter.setPen(QPen(BORDER, 1))
        painter.drawPath(path)

        data = self._data
        current = dict(data.get("current") or {})
        peak = dict(data.get("peak") or {})
        left = 10
        right = self.width() - 10

        # 第 1 行：职责图标 + 职责名（左）　段位图标 + 段位（右）
        # 两行的「视觉中心线」保持原布局（第 1 行中心 y=16，第 2 行中心 y=38），
        # 绘制高度改为 text_line_height()：中文墨迹 + 安全留白，不再是写死的 20 / 18px。
        line1_font = _font(12, bold=True)
        line1_h = text_line_height(line1_font)
        top = QRect(left, self.LINE1_CENTER - line1_h // 2, max(0, right - left), line1_h)
        x = top.left()
        if not self._role_icon.isNull():
            painter.drawPixmap(x, top.top() + (top.height() - _lh(self._role_icon)) // 2,
                               self._role_icon)
            x += _lw(self._role_icon) + 5
        painter.setFont(_font(12, bold=True))
        painter.setPen(TEXT)
        name = str(data.get("role_name") or "—")
        nm = QFontMetrics(painter.font())
        name_w = min(nm.horizontalAdvance(name), max(0, top.right() - x))
        painter.drawText(QRect(x, top.top(), name_w, top.height()),
                         Qt.AlignLeft | Qt.AlignVCenter,
                         nm.elidedText(name, Qt.ElideRight, name_w))

        display = str(current.get("display") or D.UNRANKED_TEXT)
        painter.setFont(_font(12, bold=True))
        rm = QFontMetrics(painter.font())
        text_w = rm.horizontalAdvance(display)
        rank_right = top.right()
        painter.setPen(TEXT if current.get("is_ranked") else TEXT_DIM)
        painter.drawText(QRect(rank_right - text_w, top.top(), text_w, top.height()),
                         Qt.AlignRight | Qt.AlignVCenter, display)
        if not self._rank_icon.isNull():
            painter.drawPixmap(
                rank_right - text_w - _lw(self._rank_icon) - 4,
                top.top() + (top.height() - _lh(self._rank_icon)) // 2,
                self._rank_icon,
            )

        # 第 2 行：场次 · 胜率（左）　最佳段位（右，空间不足时只留 tooltip）
        line2_h = text_line_height(_font(11))
        bottom = QRect(left, self.LINE2_CENTER - line2_h // 2, max(0, right - left), line2_h)
        matches = int(data.get("matches") or 0)
        win_rate = data.get("win_rate")
        if matches > 0:
            stat = f"{matches}场" + (f" · {float(win_rate):.0f}%" if win_rate is not None else "")
        else:
            stat = "暂无场次"
        painter.setFont(_font(11))
        painter.setPen(TEXT_SUB if matches > 0 else TEXT_DIM)
        sm = QFontMetrics(painter.font())
        stat_w = sm.horizontalAdvance(stat)
        painter.drawText(QRect(bottom.left(), bottom.top(), stat_w, bottom.height()),
                         Qt.AlignLeft | Qt.AlignVCenter, stat)

        if peak.get("is_ranked") and peak.get("display"):
            peak_text = f"最佳 {peak.get('display')}"
            painter.setFont(_font(10))
            pm = QFontMetrics(painter.font())
            peak_w = pm.horizontalAdvance(peak_text) + (
                _lw(self._peak_icon) + 4 if not self._peak_icon.isNull() else 0
            )
            if bottom.left() + stat_w + 10 + peak_w <= bottom.right():
                painter.setPen(TEXT_DIM)
                painter.drawText(
                    safe_text_rect(QRect(bottom.right() - pm.horizontalAdvance(peak_text), bottom.top(),
                                         pm.horizontalAdvance(peak_text), bottom.height()),
                                   painter.font()),
                    Qt.AlignRight | Qt.AlignVCenter, peak_text,
                )
                if not self._peak_icon.isNull():
                    painter.drawPixmap(
                        bottom.right() - peak_w,
                        bottom.top() + (bottom.height() - _lh(self._peak_icon)) // 2,
                        self._peak_icon,
                    )
        painter.end()


class PlayerHeader(QFrame):
    """玩家资料区：头像 + BattleTag + 头衔 / 等级 / 时长，Battle.net ID 作次级信息。"""

    def __init__(
        self,
        library: AssetLibrary,
        avatars: AvatarCache,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PlayerHeader")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 16, 12)
        lay.setSpacing(14)

        self.avatar = PlayerAvatar(avatars, library, 62, self)
        lay.addWidget(self.avatar, 0, Qt.AlignVCenter)

        info = QVBoxLayout()
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(3)
        self.name = QLabel("未查询")
        self.name.setObjectName("PlayerName")
        self.name.setTextInteractionFlags(Qt.TextSelectableByMouse)
        info.addWidget(self.name)
        self.title = QLabel("")
        self.title.setObjectName("PlayerTitle")
        info.addWidget(self.title)
        self.meta = QLabel("")
        self.meta.setObjectName("PlayerMeta")
        info.addWidget(self.meta)
        lay.addLayout(info, 1)

        side = QVBoxLayout()
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(3)
        side.addStretch(1)
        self.bnet = QLabel("")
        self.bnet.setObjectName("PlayerBnet")
        self.bnet.setAlignment(Qt.AlignRight | Qt.AlignBottom)
        self.bnet.setTextInteractionFlags(Qt.TextSelectableByMouse)
        side.addWidget(self.bnet)
        lay.addLayout(side, 0)

    def set_player(self, data: Dict[str, Any]) -> None:
        info = dict(data or {})
        self.name.setText(str(info.get("battle_tag") or "玩家"))
        title = str(info.get("title") or "").strip()
        self.title.setText(title)
        self.title.setVisible(bool(title))
        meta: List[str] = []
        level = int(info.get("level") or 0)
        if level > 0:
            meta.append(f"等级 {level}")
        duration = D.format_game_time(info.get("game_time"))
        if duration:
            meta.append(f"总时长 {duration}")
        self.meta.setText(" · ".join(meta))
        self.meta.setVisible(bool(meta))
        bnet = str(info.get("bnet_id") or "").strip()
        self.bnet.setText(f"Battle.net ID {bnet}" if bnet else "")
        self.bnet.setVisible(bool(bnet))
        self.avatar.set_url(str(info.get("icon_url") or ""))

    def clear(self) -> None:
        self.set_player({"battle_tag": "未查询"})


class MatchCard(QWidget):
    """单局卡片。地图背景 + 深色遮罩 + 左侧结果色条，整卡自绘。

    结构：
      左侧英雄头像 / 中部地图名+胜负标签+模式文字+段位+KDA
      / 右侧固定宽度信息容器：[地图模式图标] 比分 → 时间 → Match ID

    右侧信息容器宽度由 ``set_right_block_width()`` 统一设置（MatchList 负责让它
    与「导入竞逐计算器」按钮左边缘对齐），所以所有卡片的模式图标一定在同一列，
    不会随左侧文字长度漂移。这里不写死任何绝对 x 坐标。
    """

    clicked = Signal(str)

    CARD_HEIGHT = 96

    PORTRAIT_SIZE = 62
    SECONDARY_SIZE = 26
    SECONDARY_GAP = 3
    MAX_SECONDARY = 3

    # 右侧信息容器默认宽度（模式图标列左边缘 → 卡片右边缘）。
    RIGHT_BLOCK_WIDTH = 150
    RIGHT_BLOCK_MIN = 120
    RIGHT_BLOCK_MAX = 340
    # 模式图标只做快速扫视，视觉权重必须低于比分。
    MODE_ICON_SIZE = 26

    def __init__(
        self,
        library: AssetLibrary,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._library = library
        self._view: Dict[str, Any] = {}
        self._portrait = QPixmap()
        self._secondary_portraits: List[QPixmap] = []
        self._rank_icon = QPixmap()
        self._role_icon = QPixmap()
        self._role_display_name = ""
        self._mode_icon = QPixmap()
        self._mode_icon_column = QPixmap()
        self._heroes: List[Dict[str, Any]] = []
        self._mode_key: Any = None
        self._role_key: Any = None
        self._pix_dpr = 0.0
        self._background = QPixmap()
        self._bg_size = QSize()
        self._hover = False
        self._drive_mark = ""  # "start" / "end" / "range" / ""
        self._right_block_width = self.RIGHT_BLOCK_WIDTH
        self.setFixedHeight(self.CARD_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)

    # ---------------- 右侧信息列 ----------------
    def set_right_block_width(self, width: int) -> None:
        """设置右侧信息容器宽度，用于与上方按钮对齐成一列。"""
        value = max(self.RIGHT_BLOCK_MIN, min(int(width), self.RIGHT_BLOCK_MAX))
        if value != self._right_block_width:
            self._right_block_width = value
            self.update()

    def right_block_width(self) -> int:
        return self._right_block_width

    def mode_icon_left(self) -> int:
        """模式图标列左边缘（卡片内坐标）。验收 / 对齐校验用。"""
        return max(0, self.width() - self._right_block_width)

    # ---------------- 数据 ----------------
    def set_match(self, view: Dict[str, Any], drive_mark: str = "") -> None:
        self._view = dict(view or {})
        self._drive_mark = str(drive_mark or "")
        # 一局多英雄：views["heroes"] 已由数据层排序，主英雄在首位。
        heroes = [dict(h) for h in self._view.get("heroes") or [] if isinstance(h, dict)]
        if not heroes:
            heroes = [{
                "hero_name": self._view.get("hero_name"),
                "hero_name_raw": self._view.get("hero_name_raw"),
                "hero_guid": self._view.get("hero_guid"),
            }]
        self._heroes = heroes
        # 职责图标 / 文案：开放职责队列统一用 flex（开放职责），不按本局英雄职责
        # 显示 tank/damage/support。判定只读数据层已解析好的 queue_kind。
        role_key, role_name = D.display_role(
            self._view.get("role_type"), self._view.get("role_name"), self._view.get("queue_kind")
        )
        self._role_display_name = role_name
        self._role_key = role_key
        # 地图类型只能来自后端 map_mode / map_mode_zh；instance_type(IT_RANKED) 不是地图类型。
        # 绝不根据地图名反推模式，也不新增第二套模式解析。
        self._mode_key = self._view.get("map_mode") or self._view.get("map_mode_zh")
        self._load_pixmaps()
        hero_names = [
            str(row.get("hero_name") or row.get("hero_name_raw") or "").strip()
            for row in heroes
        ]
        hero_names = [name for name in hero_names if name]
        tip = [f"{self._view.get('map_name') or ''}　{self._view.get('game_mode_zh') or ''}"]
        if len(hero_names) > 1:
            tip.append("使用英雄：" + " · ".join(hero_names))
        tip.append(f"Match ID: {self._view.get('match_id') or ''}")
        tip.append("点击查看单局详情")
        self.setToolTip("\n".join(tip))
        self.update()

    def _load_pixmaps(self) -> None:
        """按控件当前屏幕 DPR 取全部位图（set_match 时 / DPR 变化后调用）。"""
        dpr = self._pix_dpr = _widget_dpr(self)
        heroes = self._heroes or [{}]
        main = heroes[0]
        self._portrait = self._library.hero_icon(
            self.PORTRAIT_SIZE, main.get("hero_name"), main.get("hero_name_raw"), main.get("hero_guid"),
            dpr=dpr,
        )
        self._secondary_portraits = [
            self._library.hero_icon(
                self.SECONDARY_SIZE, row.get("hero_name"), row.get("hero_name_raw"), row.get("hero_guid"),
                dpr=dpr,
            )
            for row in heroes[1:1 + self.MAX_SECONDARY]
        ]
        rank = str(self._view.get("rank_display") or "")
        self._rank_icon = self._library.rank_icon(rank or "unranked", 14, dpr=dpr) if rank else QPixmap()
        role_name = self._role_display_name
        self._role_icon = self._library.role_icon(self._role_key or role_name, 14, role_name, dpr=dpr)
        self._mode_icon = self._library.map_mode_icon(self._mode_key, 13, dpr=dpr)
        # 右侧模式图标列用的大一号透明图标（白 / 灰白，不随胜负变色）。
        self._mode_icon_column = self._library.map_mode_icon(self._mode_key, self.MODE_ICON_SIZE, dpr=dpr)
        self._background = QPixmap()
        self._bg_size = QSize()

    def set_drive_mark(self, mark: str) -> None:
        if mark != self._drive_mark:
            self._drive_mark = str(mark or "")
            self.update()

    def match_id(self) -> str:
        return str(self._view.get("match_id") or "")

    # ---------------- 交互 ----------------
    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.match_id())

    # ---------------- 绘制 ----------------
    def _ensure_background(self) -> None:
        """地图背景按控件尺寸缓存，只在尺寸变化时重新取一次缩放结果。
        DPR 变化（跨屏）时全部位图按新 DPR 重取。"""
        if _widget_dpr(self) != self._pix_dpr:
            self._load_pixmaps()
        size = self.size()
        if self._background.isNull() or self._bg_size != size:
            self._background = self._library.map_background(
                size.width(), size.height(),
                self._view.get("map_name"), self._view.get("map_name_raw"), self._view.get("map_guid"),
                dpr=self._pix_dpr,
            )
            self._bg_size = QSize(size)

    def _portrait_width(self) -> int:
        """英雄头像区总宽度。多英雄时把次英雄一起算进去，中部文字随之右移。"""
        width = self.PORTRAIT_SIZE
        if self._secondary_portraits:
            width += len(self._secondary_portraits) * (self.SECONDARY_SIZE + self.SECONDARY_GAP)
        return width

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._view:
            return
        self._ensure_background()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        rect = QRectF(0, 0, self.width(), self.height())
        path = QPainterPath()
        path.addRoundedRect(rect, 6, 6)
        painter.setClipPath(path)

        result = str(self._view.get("result") or "")
        color = result_color(result)
        painter.fillRect(self.rect(), SURFACE)
        if not self._background.isNull():
            x = (self.width() - _lw(self._background)) // 2
            y = (self.height() - _lh(self._background)) // 2
            painter.drawPixmap(x, y, self._background)

        # 深色半透明遮罩：左侧更暗保证文字可读，右侧保留地图画面。
        overlay = QLinearGradient(0, 0, self.width(), 0)
        overlay.setColorAt(0.0, QColor(10, 12, 16, 242))
        overlay.setColorAt(0.42, QColor(10, 12, 16, 214))
        overlay.setColorAt(1.0, QColor(10, 12, 16, 150))
        painter.fillRect(self.rect(), overlay)
        tint = QColor(color)
        tint.setAlpha(26 if result in ("win", "loss") else 12)
        painter.fillRect(self.rect(), tint)
        if self._hover:
            painter.fillRect(self.rect(), QColor(255, 255, 255, 12))

        # 左侧结果色条
        painter.fillRect(QRect(0, 0, 4, self.height()), color)
        painter.setClipping(False)

        self._paint_portrait(painter)
        self._paint_center(painter)
        self._paint_right(painter)
        self._paint_border(painter, color)
        painter.end()

    def _paint_portrait(self, painter: QPainter) -> None:
        """主英雄大头像 + 次英雄小头像（只有该局确实用了多个英雄时才出现）。"""
        border = QColor(255, 255, 255, 40)
        size = self.PORTRAIT_SIZE
        box = QRect(14, (self.height() - size) // 2, size, size)
        _draw_portrait(painter, self._portrait, box, 8, border=border)
        x = box.right() + 1 + self.SECONDARY_GAP
        small = self.SECONDARY_SIZE
        # 次英雄纵向靠主头像下缘，视觉上明确是“附属英雄”。
        top = box.bottom() + 1 - small
        for pix in self._secondary_portraits:
            _draw_portrait(painter, pix, QRect(x, top, small, small), 5, border=border)
            x += small + self.SECONDARY_GAP

    def _paint_center(self, painter: QPainter) -> None:
        view = self._view
        left = 14 + self._portrait_width() + 14
        # 中部文字的右边界就是右侧信息容器的左边缘，两者不会互相覆盖。
        right_limit = self.mode_icon_left() - 10
        available = max(120, right_limit - left)

        # 第 1 行：地图中文名 + 胜/负标签
        painter.setFont(_font(15, bold=True))
        map_name = str(view.get("map_name") or "未知地图")
        metrics = QFontMetrics(painter.font())
        painter.setPen(TEXT)
        name_w = min(metrics.horizontalAdvance(map_name), available - 60)
        # 文字矩形统一经 safe_text_rect：中心线不变，高度按 QFontMetrics 撑到中文安全行高。
        painter.drawText(safe_text_rect(QRect(left, 10, name_w, 20), painter.font()),
                         Qt.AlignLeft | Qt.AlignVCenter,
                         metrics.elidedText(map_name, Qt.ElideRight, name_w))
        x = left + name_w + 8
        result = str(view.get("result") or "")
        color = result_color(result)
        label = result_text(result)
        painter.setFont(_font(11, bold=True))
        lm = QFontMetrics(painter.font())
        chip_w = lm.horizontalAdvance(label) + 14
        chip = QRectF(x, 11, chip_w, 18)
        painter.setPen(Qt.NoPen)
        fill = QColor(color)
        fill.setAlpha(36)
        painter.setBrush(fill)
        painter.drawRoundedRect(chip, 3, 3)
        painter.setPen(QPen(color, 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(chip, 3, 3)
        painter.setPen(color)
        # chip 背景尺寸保持不变；只有文字绘制矩形按字体安全高度扩展，避免中文被 chip 高度裁切。
        painter.drawText(safe_text_rect(chip, painter.font()), Qt.AlignCenter, label)
        x += chip_w + 8

        if self._drive_mark:
            mark_text = {"start": "竞逐起点", "end": "竞逐终点", "range": "竞逐区间"}.get(self._drive_mark, "")
            if mark_text:
                painter.setFont(_font(10, bold=True))
                mm = QFontMetrics(painter.font())
                mw = mm.horizontalAdvance(mark_text) + 12
                mark_rect = QRectF(x, 12, mw, 16)
                painter.setPen(Qt.NoPen)
                mark_fill = QColor(ACCENT)
                mark_fill.setAlpha(46)
                painter.setBrush(mark_fill)
                painter.drawRoundedRect(mark_rect, 3, 3)
                painter.setPen(ACCENT)
                painter.drawText(safe_text_rect(mark_rect, painter.font()), Qt.AlignCenter, mark_text)

        # 第 2 行：模式标签 + 地图类型图标/名称 + 职责图标/名称
        y = 34
        x = left
        mode_text = str(view.get("game_mode_zh") or "")
        if mode_text:
            painter.setFont(_font(11, bold=True))
            mm = QFontMetrics(painter.font())
            mw = mm.horizontalAdvance(mode_text) + 16
            chip = QRectF(x, y, mw, 19)
            painter.setPen(Qt.NoPen)
            accent_fill = QColor(ACCENT)
            accent_fill.setAlpha(34)
            painter.setBrush(accent_fill)
            painter.drawRoundedRect(chip, 3, 3)
            painter.setPen(ACCENT)
            painter.drawText(safe_text_rect(chip, painter.font()), Qt.AlignCenter, mode_text)
            x += mw + 10

        # [地图类型图标] 地图类型：只用后端 map_mode_zh，未知时显示中性文案，
        # 绝不把 instance_type（IT_RANKED 等）当作地图类型展示。
        if not self._mode_icon.isNull():
            painter.drawPixmap(x, y + 3, self._mode_icon)
            x += _lw(self._mode_icon) + 4
        mode_zh = str(view.get("map_mode_zh") or "").strip() or "未知类型"
        painter.setFont(_font(11))
        painter.setPen(TEXT_SUB)
        mm = QFontMetrics(painter.font())
        w = mm.horizontalAdvance(mode_zh)
        painter.drawText(safe_text_rect(QRect(x, y, w, 19), painter.font()),
                         Qt.AlignLeft | Qt.AlignVCenter, mode_zh)
        x += w + 12

        # [职责图标] 职责名称：开放职责队列固定 flex.png + 「开放职责」，
        # 预设职责按 tank / damage / support 显示，职责未知时不画图标。
        if not self._role_icon.isNull():
            painter.drawPixmap(x, y + 3, self._role_icon)
            x += _lw(self._role_icon) + 4
        role_name = str(self._role_display_name or "")
        if role_name and role_name != "—":
            painter.setFont(_font(11, bold=True))
            painter.setPen(TEXT)
            mm = QFontMetrics(painter.font())
            w = mm.horizontalAdvance(role_name)
            painter.drawText(safe_text_rect(QRect(x, y, w, 19), painter.font()),
                             Qt.AlignLeft | Qt.AlignVCenter, role_name)
            x += w + 12

        # 第 3 行：段位 + K/D/A + KDA + 伤害/治疗/承伤
        y = 58
        x = left
        rank = str(view.get("rank_display") or "")
        if rank:
            if not self._rank_icon.isNull():
                painter.drawPixmap(x, y + 3, self._rank_icon)
                x += _lw(self._rank_icon) + 4
            painter.setFont(_font(11))
            painter.setPen(TEXT_SUB)
            mm = QFontMetrics(painter.font())
            w = mm.horizontalAdvance(rank)
            painter.drawText(safe_text_rect(QRect(x, y, w, 18), painter.font()),
                             Qt.AlignLeft | Qt.AlignVCenter, rank)
            x += w + 10

        kda_text = f"K:{view.get('kills', 0)} D:{view.get('deaths', 0)} A:{view.get('assists', 0)}"
        painter.setFont(_font(12, bold=True))
        painter.setPen(TEXT)
        mm = QFontMetrics(painter.font())
        w = mm.horizontalAdvance(kda_text)
        painter.drawText(safe_text_rect(QRect(x, y, w, 18), painter.font()),
                         Qt.AlignLeft | Qt.AlignVCenter, kda_text)
        x += w + 8

        kda = float(view.get("kda") or 0.0)
        kda_label = f"KDA {kda:.1f}"
        painter.setPen(result_color(str(view.get("result") or "")))
        mm = QFontMetrics(painter.font())
        w = mm.horizontalAdvance(kda_label)
        painter.drawText(safe_text_rect(QRect(x, y, w, 18), painter.font()),
                         Qt.AlignLeft | Qt.AlignVCenter, kda_label)
        x += w + 12

        extras: List[str] = []
        if int(view.get("hero_damage") or 0) > 0:
            extras.append(f"{D.format_compact(view.get('hero_damage'))} 伤害")
        if int(view.get("healing") or 0) > 0:
            extras.append(f"{D.format_compact(view.get('healing'))} 治疗")
        if int(view.get("resist_damage") or 0) > 0:
            extras.append(f"{D.format_compact(view.get('resist_damage'))} 抵挡")
        if extras and x < right_limit - 40:
            painter.setFont(_font(11))
            painter.setPen(TEXT_DIM)
            text = " / ".join(extras)
            painter.drawText(safe_text_rect(QRect(x, y, max(0, right_limit - x), 18), painter.font()),
                             Qt.AlignLeft | Qt.AlignVCenter,
                             _elide(painter, text, right_limit - x))

    def _paint_right(self, painter: QPainter) -> None:
        """右侧固定信息容器：[模式图标] 比分 / 时间 / Match ID。

        容器左边缘 = ``mode_icon_left()``，所有卡片一致，因此模式图标形成一条
        稳定的纵向列，不会随左侧文字长度漂移。
        """
        view = self._view
        right = self.width() - 14
        block_left = self.mode_icon_left()
        result = str(view.get("result") or "")
        color = result_color(result)

        team = int(view.get("team_score") or 0)
        opponent = int(view.get("opponent_score") or 0)
        score = f"{team} : {opponent}"
        painter.setFont(_font(22, bold=True))
        metrics = QFontMetrics(painter.font())
        w = metrics.horizontalAdvance(score)
        painter.setPen(color if result in ("win", "loss") else TEXT_SUB)
        painter.drawText(safe_text_rect(QRect(right - w, 16, w, 28), painter.font()),
                         Qt.AlignRight | Qt.AlignVCenter, score)

        # 地图模式图标：右侧模式列内按整张卡片水平、垂直居中。
        # 保持素材原透明白色，不叠加胜负色、不加方框 / 背景 / 文字标签。
        if not self._mode_icon_column.isNull():
            icon = self._mode_icon_column
            painter.drawPixmap(
                block_left + (self.MODE_ICON_SIZE - _lw(icon)) // 2,
                (self.height() - _lh(icon)) // 2,
                icon,
            )

        painter.setFont(_font(11))
        painter.setPen(TEXT_DIM)
        lines: List[str] = [D.format_ts(view.get("begin_ts"))]
        duration = D.format_duration(view.get("game_time_sec"))
        if duration:
            lines.append(duration)
        text = " · ".join(lines)
        mm = QFontMetrics(painter.font())
        # 模式图标独占整高的列，时间与 ID 不得横穿该列。
        text_width = max(0, right - block_left - self.MODE_ICON_SIZE - 8)
        text = mm.elidedText(text, Qt.ElideRight, text_width)
        tw = mm.horizontalAdvance(text)
        painter.drawText(safe_text_rect(QRect(right - tw, 48, tw, 16), painter.font()),
                         Qt.AlignRight | Qt.AlignVCenter, text)

        match_id = str(view.get("match_id") or "")
        if match_id:
            painter.setFont(_font(10))
            painter.setPen(QColor(120, 128, 142))
            short = f"ID: {match_id[:18]}…" if len(match_id) > 19 else f"ID: {match_id}"
            mm = QFontMetrics(painter.font())
            short = mm.elidedText(short, Qt.ElideRight, text_width)
            iw = mm.horizontalAdvance(short)
            painter.drawText(safe_text_rect(QRect(right - iw, 66, iw, 14), painter.font()),
                             Qt.AlignRight | Qt.AlignVCenter, short)

    def _paint_border(self, painter: QPainter, color: QColor) -> None:
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        path = QPainterPath()
        path.addRoundedRect(rect, 6, 6)
        if self._drive_mark:
            pen_color = QColor(ACCENT)
            pen_color.setAlpha(190 if self._drive_mark in ("start", "end") else 110)
            painter.setPen(QPen(pen_color, 1.4))
        else:
            border = QColor(color)
            border.setAlpha(58 if self._hover else 34)
            painter.setPen(QPen(border, 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)


class PartyIndicator(QWidget):
    """组排标记：一条粗竖线，同组相邻行连续贯穿。

    位置固定在玩家行最左侧，即职责栏的**左边**。

    只画竖线：不画顶部横线、不画底部横线、不画括号拐角，也不显示
    Group 1 / Group 2 之类文字。组内相邻行首尾相接（配合 TeamPanel 行间距 0）
    形成一整段连续粗线；不同分组之间在组首上方 / 组尾下方各留一点空白自然断开。

    作为行边缘 overlay，不加入任何 layout；非组排时隐藏。
    """

    # overlay 宽度就是线宽，不占用布局空间。
    LINE_WIDTH = 5
    WIDTH = 5

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._active = False
        self._first = False
        self._last = False
        self.setFixedWidth(self.WIDTH)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def set_span(self, active: bool, first: bool, last: bool) -> None:
        self._active, self._first, self._last = bool(active), bool(first), bool(last)
        self.setVisible(self._active)
        self.raise_()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._active:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        # 组首上方 / 组尾下方各留一小段空白，相邻分组之间出现明显断口；
        # 组内相邻行首尾相接，视觉上是一条连续粗竖线。
        top = 5 if self._first else 0
        bottom = self.height() - (5 if self._last else 0)
        painter.setPen(Qt.NoPen)
        painter.setBrush(PARTY_COLOR)
        painter.drawRect(QRect(0, int(top), self.LINE_WIDTH, max(0, int(bottom - top))))
        painter.end()


class PlayerRow(QWidget):
    """单局详情里的一名玩家。

    左侧结构严格对齐游戏内记分板（ui_reference/scoreboard_role_reference.jpg）：

        |组排粗线|职责栏|英雄头像|玩家信息 ……………… 局内统计
        PartyIndicator · RoleIcon · HeroPortrait · PlayerIdentity · Stats

    - 组排是行左边缘的 5px overlay，同组相邻行连续贯穿，不占布局列。
    - 职责栏是上下顶满整行的窄固定列（RoleIcon），不是悬浮 badge，
      所以 addWidget 时不能加 AlignVCenter，必须让它拉满行高。

    玩家行不显示玩家账号小圆头像：既不占位、也不发起头像下载请求。
    （战绩主页玩家资料区的主头像不受影响。）

    文字区是**一个 QGridLayout 的严格两行两列**，不再让各 QLabel 在自己的
    VBox / HBox 里各自垂直居中（那是之前行与行之间文字忽高忽低的根因）：

        row 0 │ 玩家名 + 赞赏徽标            │ K:D:A
        row 1 │ 段位图标 + 段位文字 + 头衔    │ 伤害 / 治疗 / 抵挡

    同一 grid row 里的所有控件高度都等于该行的 ``line_heights()``，
    并统一 ``AlignVCenter``，所以「文字框高度相同 + 居中公式相同 + 字号相同」
    三者一致，视觉基线必然对齐；十个玩家行用的是同一套常量，坐标完全相同。
    """

    # 基准行高。真实行高由 __init__ 按 QFontMetrics 校验后写回（只会变大不会变小），
    # 保证「中文墨迹高度 + 两行 + 上下留白」永远装得下，同时所有行共享同一个值。
    ROW_HEIGHT = 52
    HERO_SIZE = 34
    ROLE_SIZE = 18
    # 段位图标视觉尺寸：< 第二行文字行高，所以它只能被行高容纳，不会反过来压文字。
    RANK_ICON_SIZE = 14
    # 两行文字之间的间距 / 文字块上下的安全留白。
    TEXT_ROW_GAP = 2
    TEXT_BLOCK_PAD = 4
    # 右侧数据列最小宽度：K:D:A 与伤害/治疗/抵挡共用这一列，
    # 列右边缘固定，字符串长短只影响列内左侧空白，右边缘不漂移。
    STATS_MIN_WIDTH = 124

    @classmethod
    def line_heights(cls) -> Tuple[int, int]:
        """(第一行高度, 第二行高度)。全部由 QFontMetrics 推导，绝不写死。"""
        return (
            text_line_height(_font(ROW_LINE1_PX, bold=True)),
            text_line_height(_font(ROW_LINE2_PX)),
        )

    @classmethod
    def _resolve_row_height(cls) -> int:
        line1, line2 = cls.line_heights()
        text_block = line1 + cls.TEXT_ROW_GAP + line2 + cls.TEXT_BLOCK_PAD * 2
        return max(cls.ROW_HEIGHT, text_block, cls.HERO_SIZE + cls.TEXT_BLOCK_PAD * 2)

    def __init__(
        self,
        library: AssetLibrary,
        avatars: Optional[AvatarCache] = None,
        *,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._library = library
        self._data: Dict[str, Any] = {}
        self._is_self = False
        # 本局竞技队列类型，由详情页传入；只影响职责图标，不参与任何数据抓取。
        self._queue_kind = ""
        line1, line2 = self.line_heights()
        self._line1_height = line1
        self._line2_height = line2
        # 行高按当前字体实测值抬升一次，之后所有 PlayerRow 共用同一常量 → 十行等高。
        PlayerRow.ROW_HEIGHT = self._resolve_row_height()
        self.setFixedHeight(PlayerRow.ROW_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # 左侧 margin 为 0：组排线与职责栏都贴着行边缘，不预留额外空间。
        # 上下 margin 同样为 0，让组排粗线和职责栏都铺满整行高度；
        # 配合 TeamPanel 行间距 0，同组相邻两行的线首尾相接成一整段。
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 10, 0)
        lay.setSpacing(0)

        # 1) 组排线是 overlay；职责栏始终从 x=0 开始。
        self.party = PartyIndicator(self)
        self.party.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.party.hide()

        # 2) 全高职责栏：不加 AlignVCenter，交给布局拉满行高。
        self.role = RoleIcon(library, self.ROLE_SIZE, parent=self)
        lay.addWidget(self.role, 0)

        # 3) 英雄头像列（支持一局多个英雄，最多 3 个）
        #    stacked：左侧主英雄大头像，右侧一列上下两个小头像（16 + 2 + 16 = HERO_SIZE），
        #    整体高度恒为 HERO_SIZE，所以 1 / 2 / 3 个英雄都不会改变行高。
        self.hero = HeroPortrait(
            library, self.HERO_SIZE, radius=7,
            secondary_size=16, secondary_gap=2, stacked=True, parent=self,
        )
        lay.addSpacing(6)
        lay.addWidget(self.hero, 0, Qt.AlignVCenter)
        lay.addSpacing(8)

        # 4) 文字区：严格两行两列的 QGridLayout。
        #    用 addLayout 而不是套一层 QWidget，保持这些 QLabel 仍是玩家行的直接子控件。
        text = QGridLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setHorizontalSpacing(12)
        text.setVerticalSpacing(self.TEXT_ROW_GAP)
        # 两行都按 QFontMetrics 得出的行高钉死，不给布局“按内容压缩”的机会。
        text.setRowMinimumHeight(0, line1)
        text.setRowMinimumHeight(1, line2)
        text.setColumnStretch(0, 1)
        text.setColumnStretch(1, 0)
        text.setColumnMinimumWidth(1, self.STATS_MIN_WIDTH)

        # row 0 左：玩家名 + 赞赏徽标（同高、同垂直居中）
        name_cell = QHBoxLayout()
        name_cell.setContentsMargins(0, 0, 0, 0)
        name_cell.setSpacing(6)
        # 玩家名 / 头衔用 ElidedLabel：空间不足时右侧显示「…」，而不是被控件边界硬切。
        # 省略优先级：段位（RankBadge 最小宽度 = 完整宽度）> 战斗数据（右列最小宽度）
        # > 玩家名 > 头衔；名与头衔各在一行，谁的行空间不够谁先省略。
        self.name = ElidedLabel("")
        self.name.setObjectName("RowName")
        self.name.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.name.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.name.setFixedHeight(line1)
        name_cell.addWidget(self.name, 0, Qt.AlignVCenter)
        self.endorse = QLabel("")
        self.endorse.setObjectName("RowEndorse")
        self.endorse.setAlignment(Qt.AlignCenter)
        # 小徽标高度取「自身字体安全高度」与第一行行高的较小值：只会更矮，不会撑高第一行。
        self.endorse.setFixedHeight(min(line1, text_line_height(_font(ROW_CHIP_PX)) + 2))
        name_cell.addWidget(self.endorse, 0, Qt.AlignVCenter)
        name_cell.addStretch(1)
        text.addLayout(name_cell, 0, 0)

        # row 0 右：K:D:A（与玩家名同字号等级、同行高、同垂直居中 → 同一基线）
        self.kda = QLabel("")
        self.kda.setObjectName("RowKda")
        self.kda.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.kda.setFixedHeight(line1)
        text.addWidget(self.kda, 0, 1, Qt.AlignRight | Qt.AlignVCenter)

        # row 1 左：段位图标 + 段位文字 + 头衔
        sub_cell = QHBoxLayout()
        sub_cell.setContentsMargins(0, 0, 0, 0)
        sub_cell.setSpacing(8)
        self.rank_badge = RankBadge(
            library, icon_size=self.RANK_ICON_SIZE, font_size=ROW_LINE2_PX, color=TEXT_SUB
        )
        # 段位徽标高度 = 第二行行高：图标在其中 AlignVCenter，文字用同一个居中公式，
        # 因此段位中文与右侧战斗数据基线一致，且图标透明边距不会让整行漂移。
        self.rank_badge.set_line_height(line2)
        sub_cell.addWidget(self.rank_badge, 0, Qt.AlignVCenter)
        self.title = ElidedLabel("")
        self.title.setObjectName("RowTitle")
        self.title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.title.setFixedHeight(line2)
        sub_cell.addWidget(self.title, 0, Qt.AlignVCenter)
        sub_cell.addStretch(1)
        text.addLayout(sub_cell, 1, 0)

        # row 1 右：伤害 / 治疗 / 抵挡
        self.extra = QLabel("")
        self.extra.setObjectName("RowExtra")
        self.extra.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.extra.setFixedHeight(line2)
        text.addWidget(self.extra, 1, 1, Qt.AlignRight | Qt.AlignVCenter)

        # 文字块整体在行内垂直居中：上下等量 stretch，不用负 margin。
        center = QVBoxLayout()
        center.setContentsMargins(0, self.TEXT_BLOCK_PAD, 0, self.TEXT_BLOCK_PAD)
        center.setSpacing(0)
        center.addStretch(1)
        center.addLayout(text)
        center.addStretch(1)
        lay.addLayout(center, 1)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.party.setGeometry(0, 0, self.party.LINE_WIDTH, self.height())
        self.party.raise_()

    def is_self(self) -> bool:
        """本行是否为当前查询账号本人。"""
        return self._is_self

    def set_player(
        self,
        data: Dict[str, Any],
        span: Tuple[bool, bool, bool],
        queue_kind: str = "",
    ) -> None:
        """``queue_kind`` 为本局竞技队列类型（``preset`` / ``open`` / ``""``），
        由数据层 ``match_view()['queue_kind']`` 提供，仅用于职责图标展示。"""
        self._data = dict(data or {})
        info = self._data
        self._queue_kind = str(queue_kind or "")
        self._is_self = bool(info.get("is_self"))
        # 多英雄由后端 hero_list/heroes 提供，数据层已排序；这里不再发请求。
        heroes = [dict(h) for h in info.get("heroes") or [] if isinstance(h, dict)]
        if heroes:
            self.hero.set_heroes(heroes)
        else:
            self.hero.set_hero(info.get("hero_name"), info.get("hero_guid"))
        # 开放职责对局：职责栏统一 flex.png（「开放职责」），不按玩家英雄职责显示。
        role_key, role_name = D.display_role(
            info.get("role_type"), info.get("role_name"), self._queue_kind
        )
        self.role.set_role(role_key, role_name)
        self.name.setText(str(info.get("name") or "未知玩家"))
        endorsement = info.get("endorsement")
        if endorsement:
            self.endorse.setText(f"赞赏 {int(endorsement)} 级")
            self.endorse.setVisible(True)
        else:
            self.endorse.setVisible(False)
        self.rank_badge.set_rank(info.get("rank") or {})
        title = str(info.get("title") or "").strip()
        self.title.setText(title)
        self.title.setVisible(bool(title))
        self.kda.setText(
            f"K:{info.get('kills', 0)} D:{info.get('deaths', 0)} A:{info.get('assists', 0)}"
        )
        extras: List[str] = []
        if int(info.get("hero_damage") or 0) > 0:
            extras.append(f"{D.format_compact(info.get('hero_damage'))} 伤害")
        if int(info.get("healing") or 0) > 0:
            extras.append(f"{D.format_compact(info.get('healing'))} 治疗")
        if int(info.get("resist_damage") or 0) > 0:
            extras.append(f"{D.format_compact(info.get('resist_damage'))} 抵挡")
        if not extras:
            if int(info.get("damage_taken") or 0) > 0:
                extras.append(f"{D.format_compact(info.get('damage_taken'))} 承伤")
            if int(info.get("healing_taken") or 0) > 0:
                extras.append(f"{D.format_compact(info.get('healing_taken'))} 受疗")
        self.extra.setText(" / ".join(extras))
        self.extra.setVisible(bool(extras))
        detail: List[str] = []
        for label, key in (("英雄伤害", "hero_damage"), ("治疗量", "healing"), ("抵挡", "resist_damage"),
                           ("承伤", "damage_taken"), ("受疗", "healing_taken"), ("终结", "final_hits")):
            value = int(info.get(key) or 0)
            if value:
                detail.append(f"{label} {value:,}")
        self.setToolTip("\n".join(detail))
        self.party.set_span(*span)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        if self._is_self:
            # 本人行（= 当前正在查询战绩的游戏账号，按稳定 ID 判定）：
            #   1) 整行背景层级明显高于其他行（略亮 + 很轻的主题橙）；
            #   2) 上下各一条 1px 橙色细强调边把整行框出来；
            #   3) 右侧一条 2px 橙色强调边（左侧留给组排粗竖线，避免互相覆盖）。
            # 不加“本人”大标签、不用高饱和大面积橙、不闪烁、不做动画。
            painter.fillRect(self.rect(), QColor(255, 255, 255, 22))
            accent = QColor(ACCENT)
            accent.setAlpha(46)
            painter.fillRect(self.rect(), accent)
            edge = QColor(ACCENT)
            edge.setAlpha(190)
            painter.fillRect(QRect(self.width() - 2, 0, 2, self.height()), edge)
            hair = QColor(ACCENT)
            hair.setAlpha(120)
            painter.setPen(QPen(hair, 1))
            painter.drawLine(0, 0, self.width(), 0)
            painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
            painter.end()
            return
        painter.fillRect(self.rect(), QColor(255, 255, 255, 6))
        # 分割线贴着职责栏深色区域的右缘起笔，不从栏内穿过。
        start_x = self.role.x() + self.role.width() - 1
        painter.setPen(QPen(QColor(255, 255, 255, 14), 1))
        painter.drawLine(int(start_x), self.height() - 1, self.width(), self.height() - 1)
        painter.end()


class BanHeroStrip(QWidget):
    """一排禁用英雄：圆形头像 + 彩色圆形描边，横向排列。

    头像统一走 ``AssetLibrary.hero_icon``（heroGuid → 本地素材的现有解析链），
    这里不做任何新的英雄映射。
    """

    SIZE = 36
    GAP = 8
    RING = 2.0

    def __init__(
        self,
        library: AssetLibrary,
        color: QColor,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._library = library
        self._color = QColor(color)
        self._pixmaps: List[QPixmap] = []
        self._rows: List[Dict[str, Any]] = []
        self._pix_dpr = 0.0
        self._names: List[str] = []
        self.setFixedHeight(self.SIZE)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def _load_pixmaps(self) -> None:
        dpr = self._pix_dpr = _widget_dpr(self)
        self._pixmaps = [
            self._library.hero_icon(
                self.SIZE, row.get("hero_name"), row.get("hero_name_raw"), row.get("hero_guid"), dpr=dpr
            )
            for row in self._rows
        ]

    def set_heroes(self, entries: Sequence[Dict[str, Any]]) -> None:
        rows = [dict(e) for e in entries or [] if isinstance(e, dict)]
        self._rows = rows
        self._load_pixmaps()
        names: List[str] = []
        for row in rows:
            meta = resolve_hero(row.get("hero_guid"), row.get("hero_name_raw") or row.get("hero_name"))
            name = str(meta.get("hero_name") or row.get("hero_name") or "").strip()
            if name:
                names.append(name)
        self._names = names
        self.setToolTip(" · ".join(names))
        self.updateGeometry()
        self.update()

    def hero_count(self) -> int:
        return len(self._pixmaps)

    def hero_names(self) -> List[str]:
        return list(self._names)

    def sizeHint(self) -> QSize:  # noqa: N802
        count = max(len(self._pixmaps), 1)
        return QSize(count * self.SIZE + (count - 1) * self.GAP, self.SIZE)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._pixmaps:
            return
        if _widget_dpr(self) != self._pix_dpr:
            self._load_pixmaps()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        inset = self.RING / 2.0
        x = 0
        for pix in self._pixmaps:
            circle = QRectF(x + inset, inset, self.SIZE - self.RING, self.SIZE - self.RING)
            clip = QPainterPath()
            clip.addEllipse(circle)
            painter.save()
            painter.setClipPath(clip)
            if pix.isNull():
                painter.fillRect(QRect(x, 0, self.SIZE, self.SIZE), SURFACE_2)
            else:
                painter.drawPixmap(
                    x + (self.SIZE - _lw(pix)) // 2,
                    (self.SIZE - _lh(pix)) // 2,
                    pix,
                )
            painter.restore()
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(self._color, self.RING))
            painter.drawEllipse(circle)
            x += self.SIZE + self.GAP
        painter.end()


class BanPanel(QFrame):
    """单方禁用卡片（我方 / 敌方）。卡片背景 / 边框 / 圆角沿用 TeamPanel 的 QSS。"""

    EMPTY_TEXT = "无禁用"

    def __init__(
        self,
        library: AssetLibrary,
        title: str,
        color: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("BanPanel")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)

        self.head = QLabel(title)
        self.head.setObjectName("TeamTitle")
        self.head.setStyleSheet(f"color: {color}; font-size: 13px; font-weight: 600;")
        lay.addWidget(self.head)

        self.strip = BanHeroStrip(library, QColor(color), self)
        lay.addWidget(self.strip, 0, Qt.AlignLeft)

        self.empty = QLabel(self.EMPTY_TEXT)
        self.empty.setObjectName("BanEmpty")
        self.empty.setVisible(False)
        lay.addWidget(self.empty)
        lay.addStretch(1)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def set_heroes(self, entries: Optional[Sequence[Dict[str, Any]]]) -> None:
        rows = list(entries or [])
        self.strip.set_heroes(rows)
        self.strip.setVisible(bool(rows))
        self.empty.setVisible(not rows)


class MapBanner(QWidget):
    """详情页顶部地图 Banner：真实地图图 + 深色渐变遮罩 + 关键信息。"""

    def __init__(self, library: AssetLibrary, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._library = library
        self._view: Dict[str, Any] = {}
        self._background = QPixmap()
        self._bg_size = QSize()
        self._mode_icon = QPixmap()
        self._pix_dpr = 0.0
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(200)

    def _load_mode_icon(self) -> None:
        self._pix_dpr = _widget_dpr(self)
        self._mode_icon = self._library.map_mode_icon(
            self._view.get("map_mode") or self._view.get("map_mode_zh"), 16, dpr=self._pix_dpr
        )

    def set_match(self, view: Dict[str, Any]) -> None:
        self._view = dict(view or {})
        self._background = QPixmap()
        self._bg_size = QSize()
        self._load_mode_icon()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        rect = QRectF(0, 0, self.width(), self.height())
        path = QPainterPath()
        path.addRoundedRect(rect, 8, 8)
        painter.setClipPath(path)
        painter.fillRect(self.rect(), SURFACE)

        if not self._view:
            painter.end()
            return
        if _widget_dpr(self) != self._pix_dpr:
            # 跨屏到不同 DPR：模式图标与背景都按新 DPR 重取。
            self._load_mode_icon()
            self._background = QPixmap()
        if self._background.isNull() or self._bg_size != self.size():
            # 统一走 AssetLibrary.map_background（缺图返回统一占位图）。
            self._background = self._library.map_background(
                self.width(), self.height(),
                self._view.get("map_name"), self._view.get("map_name_raw"), self._view.get("map_guid"),
                dpr=self._pix_dpr,
            )
            self._bg_size = QSize(self.size())
        if not self._background.isNull():
            painter.drawPixmap(
                (self.width() - _lw(self._background)) // 2,
                (self.height() - _lh(self._background)) // 2,
                self._background,
            )
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.0, QColor(8, 10, 14, 90))
        gradient.setColorAt(0.55, QColor(8, 10, 14, 170))
        gradient.setColorAt(1.0, QColor(8, 10, 14, 238))
        painter.fillRect(self.rect(), gradient)

        view = self._view
        result = str(view.get("result") or "")
        color = result_color(result)

        painter.setFont(_font(26, bold=True))
        painter.setPen(TEXT)
        painter.drawText(QRect(22, self.height() - 76, self.width() - 200, 34),
                         Qt.AlignLeft | Qt.AlignVCenter, str(view.get("map_name") or "未知地图"))

        y = self.height() - 40
        x = 22
        if not self._mode_icon.isNull():
            painter.drawPixmap(x, y + 2, self._mode_icon)
            x += _lw(self._mode_icon) + 5
        painter.setFont(_font(12))
        painter.setPen(TEXT_SUB)
        parts: List[str] = []
        mode_zh = str(view.get("map_mode_zh") or "")
        if mode_zh:
            parts.append(mode_zh)
        game_mode = str(view.get("game_mode_zh") or "")
        if game_mode:
            parts.append(game_mode)
        duration = D.format_duration(view.get("game_time_sec"))
        if duration:
            parts.append(f"时长 {duration}")
        parts.append(D.format_ts(view.get("begin_ts")))
        text = "  /  ".join(parts)
        painter.drawText(QRect(x, y, self.width() - x - 170, 20), Qt.AlignLeft | Qt.AlignVCenter,
                         _elide(painter, text, self.width() - x - 170))

        label = {"win": "VICTORY", "loss": "DEFEAT", "draw": "DRAW"}.get(result, "")
        if label:
            painter.setFont(_font(13, bold=True))
            painter.setPen(color)
            painter.drawText(QRect(self.width() - 220, self.height() - 82, 200, 20),
                             Qt.AlignRight | Qt.AlignVCenter, label)
        painter.setFont(_font(30, bold=True))
        painter.setPen(TEXT)
        score = f"{int(view.get('team_score') or 0)} : {int(view.get('opponent_score') or 0)}"
        painter.drawText(QRect(self.width() - 220, self.height() - 60, 200, 40),
                         Qt.AlignRight | Qt.AlignVCenter, score)
        painter.setClipping(False)
        painter.setPen(QPen(BORDER, 1))
        painter.drawPath(path)
        painter.end()


__all__ = [
    "BanHeroStrip",
    "BanPanel",
    "ChipBar",
    "ElidedLabel",
    "HeroPortrait",
    "IconTextBadge",
    "MapBanner",
    "MapModeBadge",
    "MatchCard",
    "PartyIndicator",
    "PlayerAvatar",
    "PlayerHeader",
    "PlayerRow",
    "RankBadge",
    "RecentFormPanel",
    "RecentFormStrip",
    "RoleBadge",
    "RoleIcon",
    "RoleRankCard",
    "SeasonSelector",
    "SectionHeader",
    "StatChip",
    "result_color",
    "result_text",
]
