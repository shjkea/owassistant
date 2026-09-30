# -*- coding: utf-8 -*-
"""深色主题：颜色常量 + 全局 QSS。

纯表现层，不含任何计分逻辑。
"""
from __future__ import annotations


class C:
    """配色表（深色数据面板风格）。"""

    BG = "#0f1116"          # 窗口底色
    SIDEBAR = "#14171f"     # 左侧导航
    SURFACE = "#181c25"     # 卡片
    SURFACE_2 = "#1f242f"   # 卡片内嵌 / 输入框
    SURFACE_3 = "#262c39"   # hover
    BORDER = "#2b3240"
    BORDER_STRONG = "#3a4353"

    TEXT = "#e6e9ef"
    TEXT_SUB = "#9aa4b4"
    TEXT_DIM = "#6b7484"

    ACCENT = "#f2a33c"      # 主强调（守望橙）
    ACCENT_HOVER = "#ffb554"
    ACCENT_PRESS = "#d98d2b"
    ACCENT_SOFT = "#3a2f1c"

    INFO = "#4ea8ff"
    WIN = "#3ddc97"
    LOSE = "#ff6b6b"
    GROUP = "#9d7bff"
    DUEL = "#ff9f43"


FONT_FAMILY = '"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif'
# 全局基础字号（逻辑 px）：QSS ``*`` 规则与 QApplication 默认字体共用这一个值。
# Qt 6 下 px 是逻辑像素，由高 DPI 缩放统一换算到物理像素；hinting 策略见 ui/font_dpi.py。
BASE_FONT_PX = 13
MONO_FAMILY = '"Cascadia Mono", "Consolas", "Microsoft YaHei UI", monospace'

# ---------------- 详情页玩家行字体层级 ----------------
# 单局详情玩家行是严格的两行结构，同一行里的所有文字必须同字号、同行高，
# 否则中文 / 英文 / 数字的基线会各走各的。这里是唯一来源：
# QSS 用它渲染，ow_widgets.PlayerRow 用同一组值做 QFontMetrics 行高计算。
ROW_LINE1_PX = 13   # 第一行：玩家名 / K:D:A
ROW_LINE2_PX = 11   # 第二行：段位文字 / 头衔 / 伤害·治疗·抵挡
ROW_CHIP_PX = 10    # 第一行右侧的赞赏等级小徽标（只跟随第一行行高，不撑高它）


def build_qss() -> str:
    """返回应用级 QSS。所有尺寸用 px，配合 Qt 高 DPI 自动缩放。"""
    return f"""
* {{
    font-family: {FONT_FAMILY};
    font-size: {BASE_FONT_PX}px;
    outline: none;
}}

QWidget#Root, QMainWindow {{
    background: {C.BG};
}}

QLabel {{
    color: {C.TEXT};
    background: transparent;
}}
QLabel[role="title"] {{
    font-size: 19px;
    font-weight: 600;
    color: {C.TEXT};
}}
QLabel[role="subtitle"] {{
    font-size: 12px;
    color: {C.TEXT_SUB};
}}
QLabel[role="section"] {{
    font-size: 13px;
    font-weight: 600;
    color: {C.TEXT};
}}
QLabel[role="hint"] {{
    font-size: 12px;
    color: {C.TEXT_DIM};
}}
QLabel[role="fieldLabel"] {{
    font-size: 12px;
    color: {C.TEXT_SUB};
}}

/* ---------------- 侧边导航 ---------------- */
QFrame#Sidebar {{
    background: {C.SIDEBAR};
    border-right: 1px solid {C.BORDER};
}}
QLabel#BrandTitle {{
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", sans-serif;
    font-size: 15px;
    font-weight: 700;
    color: {C.TEXT};
}}
QLabel#BrandVersion {{
    font-size: 11px;
    font-weight: 400;
    color: {C.TEXT_DIM};
    padding-bottom: 2px;
}}
QLabel#BrandSub {{
    font-size: 10px;
    color: {C.TEXT_DIM};
}}
/* 导航项文字整体偏左：左内边距 18px（含 3px 强调线合计约 21px 起笔），
   右侧留少量余量。必须用 QPushButton —— QToolButton 会忽略 text-align，
   把文字强行画在 content rect 中央。 */
QPushButton#NavButton {{
    background: transparent;
    border: none;
    border-left: 3px solid transparent;
    color: {C.TEXT_SUB};
    text-align: left;
    padding: 10px 6px 10px 18px;
    font-size: 13px;
    border-radius: 0px;
    min-height: 18px;
}}
QPushButton#NavButton:hover {{
    background: {C.SURFACE};
    color: {C.TEXT};
}}
QPushButton#NavButton:pressed {{
    background: {C.SURFACE};
}}
QPushButton#NavButton:checked {{
    background: {C.SURFACE_2};
    color: {C.ACCENT};
    border-left: 3px solid {C.ACCENT};
    font-weight: 600;
}}

/* ---------------- 卡片 ---------------- */
QFrame#Card {{
    background: {C.SURFACE};
    border: 1px solid {C.BORDER};
    border-radius: 10px;
}}
QFrame#SubCard {{
    background: {C.SURFACE_2};
    border: 1px solid {C.BORDER};
    border-radius: 8px;
}}
QFrame#StatCard {{
    background: {C.SURFACE_2};
    border: 1px solid {C.BORDER};
    border-radius: 8px;
}}
QFrame#StatCardAccent {{
    background: {C.SURFACE_2};
    border: 1px solid {C.ACCENT};
    border-radius: 8px;
}}
QFrame#Divider {{
    background: {C.BORDER};
    border: none;
}}
QFrame#Plain {{
    background: transparent;
    border: none;
}}

QLabel#StatValue {{
    font-size: 26px;
    font-weight: 700;
    color: {C.TEXT};
}}
QLabel#StatValueAccent {{
    font-size: 30px;
    font-weight: 700;
    color: {C.ACCENT};
}}
QLabel#StatCaption {{
    font-size: 11px;
    color: {C.TEXT_SUB};
}}
QLabel#StatUnit {{
    font-size: 12px;
    color: {C.TEXT_DIM};
}}

/* ---------------- 按钮 ---------------- */
QPushButton {{
    background: {C.SURFACE_2};
    color: {C.TEXT};
    border: 1px solid {C.BORDER_STRONG};
    border-radius: 6px;
    padding: 7px 14px;
    min-height: 18px;
}}
QPushButton:hover {{
    background: {C.SURFACE_3};
    border-color: {C.TEXT_DIM};
}}
QPushButton:pressed {{
    background: {C.SURFACE};
}}
QPushButton:disabled {{
    color: {C.TEXT_DIM};
    background: {C.SURFACE};
    border-color: {C.BORDER};
}}
QPushButton[variant="primary"] {{
    background: {C.ACCENT};
    color: #1a1204;
    border: 1px solid {C.ACCENT};
    font-weight: 600;
}}
QPushButton[variant="primary"]:hover {{
    background: {C.ACCENT_HOVER};
    border-color: {C.ACCENT_HOVER};
}}
QPushButton[variant="primary"]:pressed {{
    background: {C.ACCENT_PRESS};
}}
QPushButton[variant="win"] {{
    background: rgba(61, 220, 151, 0.14);
    color: {C.WIN};
    border: 1px solid rgba(61, 220, 151, 0.45);
    font-weight: 600;
}}
QPushButton[variant="win"]:hover {{
    background: rgba(61, 220, 151, 0.24);
}}
QPushButton[variant="lose"] {{
    background: rgba(255, 107, 107, 0.14);
    color: {C.LOSE};
    border: 1px solid rgba(255, 107, 107, 0.45);
    font-weight: 600;
}}
QPushButton[variant="lose"]:hover {{
    background: rgba(255, 107, 107, 0.24);
}}
QPushButton[variant="ghost"] {{
    background: transparent;
    border: 1px solid {C.BORDER_STRONG};
    color: {C.TEXT_SUB};
}}
QPushButton[variant="ghost"]:hover {{
    background: {C.SURFACE_2};
    color: {C.TEXT};
}}

/* 分段开关（单排/组排、普通/决斗） */
QFrame#Segment {{
    background: {C.SURFACE_2};
    border: 1px solid {C.BORDER};
    border-radius: 7px;
}}
QToolButton#SegmentButton {{
    background: transparent;
    border: none;
    border-radius: 5px;
    color: {C.TEXT_SUB};
    padding: 6px 16px;
    font-size: 13px;
}}
QToolButton#SegmentButton:hover {{
    color: {C.TEXT};
}}
QToolButton#SegmentButton:checked {{
    background: {C.ACCENT};
    color: #1a1204;
    font-weight: 600;
}}

/* ---------------- 输入控件 ---------------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {C.SURFACE_2};
    color: {C.TEXT};
    border: 1px solid {C.BORDER_STRONG};
    border-radius: 6px;
    padding: 6px 9px;
    selection-background-color: {C.ACCENT};
    selection-color: #1a1204;
    min-height: 20px;
    max-height: 34px;
}}
QPlainTextEdit, QTextEdit {{
    background: {C.SURFACE_2};
    color: {C.TEXT};
    border: 1px solid {C.BORDER_STRONG};
    border-radius: 6px;
    padding: 6px 9px;
    selection-background-color: {C.ACCENT};
    selection-color: #1a1204;
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {C.ACCENT};
}}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    color: {C.TEXT_DIM};
}}
QPlainTextEdit#SequenceEdit {{
    font-family: {MONO_FAMILY};
    font-size: 15px;
    letter-spacing: 2px;
    line-height: 20px;
}}
QLineEdit#RangeEdit {{
    font-family: {MONO_FAMILY};
}}

QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    background: {C.SURFACE_3};
    border: none;
    width: 17px;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    border-top-right-radius: 5px;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    border-bottom-right-radius: 5px;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {C.ACCENT};
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: none;
    width: 0px; height: 0px;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-bottom: 5px solid {C.TEXT_SUB};
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: none;
    width: 0px; height: 0px;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {C.TEXT_SUB};
}}

QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox::down-arrow {{
    image: none;
    width: 0px; height: 0px;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {C.TEXT_SUB};
    margin-right: 7px;
}}
QComboBox QAbstractItemView {{
    background: {C.SURFACE_2};
    border: 1px solid {C.BORDER_STRONG};
    color: {C.TEXT};
    selection-background-color: {C.ACCENT};
    selection-color: #1a1204;
    outline: none;
}}

QSlider::groove:horizontal {{
    height: 4px;
    background: {C.SURFACE_3};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {C.ACCENT};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {C.TEXT};
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{
    background: {C.ACCENT_HOVER};
}}

QCheckBox {{
    color: {C.TEXT};
    spacing: 7px;
}}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border-radius: 4px;
    border: 1px solid {C.BORDER_STRONG};
    background: {C.SURFACE_2};
}}
QCheckBox::indicator:checked {{
    background: {C.ACCENT};
    border-color: {C.ACCENT};
}}

/* ---------------- 表格 ---------------- */
QTableView {{
    background: {C.SURFACE};
    alternate-background-color: {C.SURFACE_2};
    color: {C.TEXT};
    border: 1px solid {C.BORDER};
    border-radius: 8px;
    gridline-color: transparent;
    selection-background-color: {C.ACCENT_SOFT};
    selection-color: {C.TEXT};
}}
QTableView::item {{
    padding: 2px 6px;
    border: none;
}}
QTableView::item:selected {{
    background: {C.ACCENT_SOFT};
    color: {C.TEXT};
}}
QHeaderView {{
    background: {C.SURFACE_2};
    border: none;
}}
QHeaderView::section {{
    background: {C.SURFACE_2};
    color: {C.TEXT_SUB};
    border: none;
    border-bottom: 1px solid {C.BORDER};
    padding: 7px 6px;
    font-size: 12px;
    font-weight: 600;
}}
QHeaderView::section:horizontal:hover {{
    color: {C.TEXT};
}}
QTableCornerButton::section {{
    background: {C.SURFACE_2};
    border: none;
}}

/* ---------------- 滚动条 ---------------- */
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollArea > QWidget > QWidget {{
    background: transparent;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {C.BORDER_STRONG};
    border-radius: 5px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
    background: {C.TEXT_DIM};
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {C.BORDER_STRONG};
    border-radius: 5px;
    min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {C.TEXT_DIM};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0px; height: 0px;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

/* ---------------- 其它 ---------------- */
QToolTip {{
    background: {C.SURFACE_2};
    color: {C.TEXT};
    border: 1px solid {C.BORDER_STRONG};
    padding: 5px 7px;
}}
QLabel#StatusBar {{
    color: {C.TEXT_SUB};
    font-size: 12px;
    background: transparent;
}}
QLabel#StatusBar[level="ok"] {{ color: {C.WIN}; }}
QLabel#StatusBar[level="error"] {{ color: {C.LOSE}; }}
QLabel#StatusBar[level="info"] {{ color: {C.INFO}; }}

QFrame#Banner {{
    background: {C.SURFACE_2};
    border: 1px solid {C.BORDER};
    border-left: 3px solid {C.INFO};
    border-radius: 6px;
}}
QFrame#Banner[level="ok"] {{ border-left-color: {C.WIN}; }}
QFrame#Banner[level="error"] {{ border-left-color: {C.LOSE}; }}
QFrame#Banner[level="warn"] {{ border-left-color: {C.ACCENT}; }}
QLabel#BannerText {{
    color: {C.TEXT};
    font-size: 12px;
}}

QProgressBar {{
    background: {C.SURFACE_2};
    border: 1px solid {C.BORDER};
    border-radius: 6px;
    text-align: center;
    color: {C.TEXT_SUB};
    font-size: 11px;
    min-height: 16px;
}}
QProgressBar::chunk {{
    background: {C.ACCENT};
    border-radius: 5px;
}}

QMessageBox {{
    background: {C.SURFACE};
}}
QMessageBox QLabel {{
    color: {C.TEXT};
}}
QDialog {{
    background: {C.SURFACE};
}}

/* ---------------- 国服战绩页 ---------------- */
QFrame#PlayerHeader {{
    background: {C.SURFACE};
    border: 1px solid {C.BORDER};
    border-radius: 8px;
}}
QLabel#PlayerName {{
    font-size: 21px;
    font-weight: 700;
    color: {C.TEXT};
}}
QLabel#PlayerTitle {{
    font-size: 12px;
    color: {C.ACCENT};
}}
QLabel#PlayerMeta {{
    font-size: 12px;
    color: {C.TEXT_SUB};
}}
QLabel#PlayerBnet {{
    font-size: 11px;
    color: {C.TEXT_DIM};
}}

/* 赛季切换 chip / 模式筛选 chip：参考图里的轻量下划线与描边风格 */
QToolButton#SeasonChip {{
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    color: {C.TEXT_SUB};
    padding: 4px 12px;
    font-size: 13px;
}}
QToolButton#SeasonChip:hover {{
    color: {C.TEXT};
}}
QToolButton#SeasonChip:checked {{
    color: {C.ACCENT};
    border-bottom: 2px solid {C.ACCENT};
    font-weight: 600;
}}
QToolButton#FilterChip {{
    background: transparent;
    border: 1px solid {C.BORDER};
    border-radius: 4px;
    color: {C.TEXT_SUB};
    padding: 4px 12px;
    font-size: 12px;
}}
QToolButton#FilterChip:hover {{
    border-color: {C.BORDER_STRONG};
    color: {C.TEXT};
}}
QToolButton#FilterChip:checked {{
    border-color: {C.ACCENT};
    color: {C.ACCENT};
    background: rgba(242, 163, 60, 0.10);
}}

/* 近期状态（当前赛季最近 N 场 竞技+快速） */
QFrame#RecentFormPanel {{
    background: {C.SURFACE};
    border: 1px solid {C.BORDER};
    border-radius: 6px;
}}
QLabel#RecentFormCaption {{
    font-size: 12px;
    font-weight: 600;
    color: {C.TEXT};
}}
QLabel#RecentFormRecord {{
    font-size: 12px;
    color: {C.TEXT_SUB};
}}
QLabel#RecentFormRate {{
    font-size: 15px;
    font-weight: 700;
    color: {C.ACCENT};
}}
QLabel#RecentFormAxis {{
    font-size: 10px;
    color: {C.TEXT_DIM};
}}
QLabel#RecentFormEmpty {{
    font-size: 12px;
    color: {C.TEXT_DIM};
}}

/* 缓存管理 */
QLabel#CacheKind {{
    font-size: 12px;
    color: {C.TEXT_SUB};
}}
QLabel#CacheSize {{
    font-size: 12px;
    color: {C.TEXT};
}}
QLabel#CacheTotalKind {{
    font-size: 13px;
    font-weight: 600;
    color: {C.TEXT};
}}
QLabel#CacheTotalSize {{
    font-size: 13px;
    font-weight: 700;
    color: {C.ACCENT};
}}
QLabel#CachePath {{
    font-size: 11px;
    color: {C.TEXT_DIM};
}}

/* 竞逐区间只剩两个动作按钮，没有常驻状态条，所以这里不再有任何背景 / 边框。 */
QWidget#DriveActions {{
    background: transparent;
    border: none;
}}

QLabel#EmptyHint {{
    color: {C.TEXT_DIM};
    font-size: 13px;
}}
QLabel#ListFooter {{
    color: {C.TEXT_DIM};
    font-size: 11px;
    padding: 4px 0px 8px 0px;
}}

QFrame#TeamPanel, QFrame#BanPanel {{
    background: {C.SURFACE};
    border: 1px solid {C.BORDER};
    border-radius: 8px;
}}
QLabel#BanEmpty {{
    font-size: 12px;
    color: {C.TEXT_DIM};
}}
/* 玩家行两行文字：字号只在这里和 theme.ROW_LINE*_PX 出现一次，
   行高 / 垂直对齐全部由 PlayerRow 按 QFontMetrics 统一计算，QSS 不设高度。 */
QLabel#RowName {{
    font-size: {ROW_LINE1_PX}px;
    font-weight: 600;
    color: {C.TEXT};
}}
QLabel#RowTitle {{
    font-size: {ROW_LINE2_PX}px;
    color: {C.TEXT_DIM};
}}
QLabel#RowEndorse {{
    font-size: {ROW_CHIP_PX}px;
    color: {C.ACCENT};
    border: 1px solid rgba(242, 163, 60, 0.45);
    border-radius: 3px;
    padding: 0px 4px;
}}
QLabel#RowKda {{
    font-size: {ROW_LINE1_PX}px;
    font-weight: 600;
    color: {C.TEXT};
}}
QLabel#RowExtra {{
    font-size: {ROW_LINE2_PX}px;
    color: {C.TEXT_DIM};
}}
QLabel#DetailTitle {{
    font-size: 18px;
    font-weight: 700;
    color: {C.TEXT};
}}
QLabel#DetailMatchId {{
    font-size: 11px;
    color: {C.TEXT_DIM};
}}
QLabel#DetailSummary {{
    font-size: 12px;
    color: {C.TEXT_SUB};
}}

/* ---------------- AI 锐评 ---------------- */
QFrame#AIPanel {{
    background: {C.SURFACE};
    border: 1px solid {C.BORDER};
    border-radius: 8px;
}}
QLabel#AIEntryTitle {{
    font-size: 14px;
    font-weight: 600;
    color: {C.ACCENT};
}}
QLabel#AIResult {{
    font-size: 15px;
    font-weight: 700;
}}
QLabel#AIScore {{
    font-size: 20px;
    font-weight: 700;
    color: {C.TEXT};
}}
QLabel#AITeamTitle {{
    font-size: 12px;
    font-weight: 600;
}}
QLabel#AIPickName {{
    font-size: 12px;
    font-weight: 600;
    color: {C.TEXT};
}}
QLabel#AIPickSelf {{
    font-size: 10px;
    color: {C.ACCENT};
    border: 1px solid rgba(242, 163, 60, 0.45);
    border-radius: 3px;
    padding: 0px 3px;
}}
QLabel#AIPickKda {{
    font-size: 12px;
    font-weight: 600;
    color: {C.TEXT};
}}
QLabel#AIPickStats {{
    font-size: 11px;
    color: {C.TEXT_DIM};
}}
QTextBrowser#AIReviewOutput {{
    background: {C.SURFACE_2};
    color: {C.TEXT};
    border: 1px solid {C.BORDER};
    border-radius: 6px;
    padding: 8px 10px;
    font-size: 13px;
}}
"""
