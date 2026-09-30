# -*- coding: utf-8 -*-
"""应用图标与「AI 锐评」文案回归测试（离屏）。

运行：
    python -m pytest tests/test_app_icon.py -q
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ui.assets import APP_ICON_PATH  # noqa: E402

SOURCE_ICON = ROOT / "ow_icon_from_display_transparent" / "ow_assistant_icon.ico"
UI_SOURCES = list((ROOT / "ui").rglob("*.py")) + [ROOT / "main.py", ROOT / "ui_preview.py"]

# 在全新进程中运行 build_app()，工作目录故意设为临时目录，验证不依赖 cwd。
_PROBE = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
os.environ["LOCALAPPDATA"] = os.getcwd()
os.environ["APPDATA"] = os.getcwd()
from main import build_app
from ui.main_window import MainWindow
app = build_app([sys.argv[0]])
assert not app.windowIcon().isNull(), "app icon null"
assert app.windowIcon().availableSizes(), "app icon has no sizes"
win = MainWindow()
assert not win.windowIcon().isNull(), "window icon null"
assert win.windowIcon().cacheKey() == app.windowIcon().cacheKey(), "window icon differs"
print("ICON_OK")
"""


def test_icon_in_formal_assets_dir():
    assert APP_ICON_PATH == ROOT / "assets" / "app" / "ow_assistant_icon.ico"
    assert APP_ICON_PATH.is_absolute() and APP_ICON_PATH.is_file()
    if SOURCE_ICON.is_file():
        assert APP_ICON_PATH.read_bytes() == SOURCE_ICON.read_bytes()


def test_no_code_depends_on_temp_icon_dir():
    for path in UI_SOURCES:
        assert "ow_icon_from_display_transparent" not in path.read_text(encoding="utf-8"), path


def test_build_app_sets_icon_independent_of_cwd(tmp_path):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE, str(ROOT)],
        cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=120,
    )
    assert "ICON_OK" in proc.stdout, proc.stdout + proc.stderr


def test_no_old_ai_review_wording_in_ui():
    for path in UI_SOURCES:
        assert "睿评" not in path.read_text(encoding="utf-8"), f"{path} 仍包含旧文案「睿评」"


def test_entry_card_wording():
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([sys.argv[0]])
    from ui.ow_ai_review import AIReviewEntryCard

    card = AIReviewEntryCard()
    assert card.button.text().startswith("进入 AI 锐评")
