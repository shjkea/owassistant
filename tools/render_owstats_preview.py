# -*- coding: utf-8 -*-
"""离屏渲染国服战绩页与单局详情页，导出 PNG 供与参考图比对。

用法：
    python tools/render_owstats_preview.py
产出：
    tools/preview_profile.png
    tools/preview_detail.png
"""
from __future__ import annotations

import os
import sys

# 默认用 Windows 原生平台：offscreen 平台没有任何系统字体，中文会渲染成方块，
# 无法用于视觉核对。需要无窗口环境时可显式设置 OW_PREVIEW_PLATFORM=offscreen。
os.environ.setdefault("QT_QPA_PLATFORM", os.environ.get("OW_PREVIEW_PLATFORM", "windows"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402

from main import build_app  # noqa: E402
from owstats_ui_smoke_test import FakeService  # noqa: E402

# owstats_ui_smoke_test 在导入时会 setdefault 到 offscreen；这里恢复本脚本的选择。
os.environ["QT_QPA_PLATFORM"] = os.environ.get("OW_PREVIEW_PLATFORM", "windows")

from ui.main_window import MainWindow  # noqa: E402


def pump(ms: int = 150) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def main() -> int:
    app = build_app([sys.argv[0]])
    win = MainWindow()
    page = win.owstats_page
    page._service = FakeService(current_season=24, matches_per_season=60)
    win.resize(1480, 940)
    win.show()
    win._select(0)  # 国服战绩已是第一项导航
    pump(250)

    page.query_edit.setText("Demo#1234")
    page.search()
    for _ in range(60):
        pump(100)
        if page._matches:
            break
    pump(400)
    win.grab().save("tools/preview_profile.png")

    target = next(v["match_id"] for v in page._matches if v["competitive"])
    page.open_detail(target)
    for _ in range(40):
        pump(100)
        if page.detail_page.friendly._rows and page.detail_page.friendly._rows[0].isVisible():
            break
    pump(400)
    win.grab().save("tools/preview_detail.png")
    print("saved tools/preview_profile.png tools/preview_detail.png")
    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
