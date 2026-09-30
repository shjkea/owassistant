# -*- coding: utf-8 -*-
"""守望先锋 国服助手 - PySide6 桌面前端入口。

运行：
    python main.py

计分 / 历史回放 / DP 概率全部来自 drive_core.py，本前端不重写任何算法。
"""
from __future__ import annotations

import os
import sys

# Qt6 默认已按显示器 DPI 缩放、默认 PassThrough（150% 保持 1.5，不圆整成 2）。
# 这里显式写出只为防止外部环境改成 Round；字体 hinting 策略见 ui/font_dpi.py。
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")

from PySide6.QtGui import QPalette, QColor, QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.assets import APP_ICON_PATH  # noqa: E402
from ui.font_dpi import install_app_font, make_app_font  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402
from ui.theme import C, build_qss  # noqa: E402

# Windows 任务栏按 AppUserModelID 分组；不设置时用 python.exe 运行会显示 Python 图标。
APP_USER_MODEL_ID = "ow_drive_split.ow_assistant"


def _set_windows_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass


def _pick_cjk_font() -> str:
    """选一个 Windows 上稳定的中文字体。"""
    from PySide6.QtGui import QFontDatabase

    families = set(QFontDatabase.families())
    for name in ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "SimHei"):
        if name in families:
            return name
    return ""


def build_app(argv: list[str] | None = None) -> QApplication:
    _set_windows_app_id()
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("守望先锋 国服助手")
    app.setApplicationDisplayName("守望先锋 国服助手")
    app.setOrganizationName("ow_drive_split")
    app.setStyle("Fusion")
    # 应用图标：路径由 ui.assets 基于 __file__ 解析，不依赖当前工作目录。
    if APP_ICON_PATH.is_file():
        app.setWindowIcon(QIcon(str(APP_ICON_PATH)))

    # Fusion 基础调色板设成深色，保证原生绘制部分（菜单、对话框）也不发灰。
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(C.BG))
    pal.setColor(QPalette.WindowText, QColor(C.TEXT))
    pal.setColor(QPalette.Base, QColor(C.SURFACE_2))
    pal.setColor(QPalette.AlternateBase, QColor(C.SURFACE))
    pal.setColor(QPalette.Text, QColor(C.TEXT))
    pal.setColor(QPalette.Button, QColor(C.SURFACE_2))
    pal.setColor(QPalette.ButtonText, QColor(C.TEXT))
    pal.setColor(QPalette.ToolTipBase, QColor(C.SURFACE_2))
    pal.setColor(QPalette.ToolTipText, QColor(C.TEXT))
    pal.setColor(QPalette.Highlight, QColor(C.ACCENT))
    pal.setColor(QPalette.HighlightedText, QColor("#1a1204"))
    pal.setColor(QPalette.PlaceholderText, QColor(C.TEXT_DIM))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(C.TEXT_DIM))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(C.TEXT_DIM))
    app.setPalette(pal)

    # 基础字体与 QSS 同源（BASE_FONT_PX 逻辑 px）；hinting 先按主屏 DPR 决定，
    # 窗口显示 / 跨屏后由 MainWindow 上的 FontDpiWatcher 按实际所在屏幕修正。
    screen = app.primaryScreen()
    font = make_app_font(_pick_cjk_font(), screen.devicePixelRatio() if screen else 1.0)
    install_app_font(app, font, build_qss())
    return app


def main() -> int:
    app = build_app()
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
