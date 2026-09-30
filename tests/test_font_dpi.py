# -*- coding: utf-8 -*-
"""全局字体 DPI 策略回归测试（ui/font_dpi.py）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtGui import QFont, QFontMetrics  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QTableView  # noqa: E402

from ui.font_dpi import (  # noqa: E402
    apply_font_policy,
    hinting_for_dpr,
    install_app_font,
    make_app_font,
)
from ui.theme import BASE_FONT_PX, build_qss  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([sys.argv[0]])


@pytest.mark.parametrize("dpr, expected", [
    (1.0, QFont.PreferDefaultHinting),
    (1.25, QFont.PreferNoHinting),
    (1.5, QFont.PreferNoHinting),
    (1.75, QFont.PreferNoHinting),
    (2.0, QFont.PreferDefaultHinting),
    (3.0, QFont.PreferDefaultHinting),
])
def test_hinting_by_dpr(dpr, expected):
    assert hinting_for_dpr(dpr) == expected


def test_app_font_matches_qss_base_size(app):
    font = make_app_font("Microsoft YaHei UI", 1.5)
    assert font.pixelSize() == BASE_FONT_PX
    assert f"font-size: {BASE_FONT_PX}px;" in build_qss()


def test_hinting_does_not_change_metrics(app):
    """hinting 只影响栅格化，不得改变行高 / 字宽，否则会引起布局位移或裁切。"""
    sample = "未定级宗师翡翠 K:12 D:3 A:8"
    for px in (10, 11, 12, 13, 15, 19, 26, 30):
        metrics = []
        for hint in (QFont.PreferDefaultHinting, QFont.PreferNoHinting):
            f = QFont("Microsoft YaHei UI")
            f.setPixelSize(px)
            f.setHintingPreference(hint)
            m = QFontMetrics(f)
            metrics.append((m.height(), m.ascent(), m.descent(), m.horizontalAdvance(sample)))
        assert metrics[0] == metrics[1], px


def test_policy_reaches_polished_widgets_and_item_views(app):
    install_app_font(app, make_app_font("Microsoft YaHei UI", 1.0), build_qss())
    label = QLabel("国服战绩")
    label.setObjectName("PlayerName")  # QSS 覆盖字号的控件
    table = QTableView()
    label.show()
    table.show()
    app.processEvents()
    assert label.font().hintingPreference() == QFont.PreferDefaultHinting
    assert table.font().hintingPreference() == QFont.PreferDefaultHinting

    for dpr, want in ((1.5, QFont.PreferNoHinting), (2.0, QFont.PreferDefaultHinting),
                      (1.25, QFont.PreferNoHinting)):
        assert apply_font_policy(app, dpr) is True
        app.processEvents()
        assert QFont().hintingPreference() == want  # 自绘控件 _font() 的基准
        for w in (label, table, table.horizontalHeader()):
            assert w.font().hintingPreference() == want, (dpr, type(w).__name__)
        assert label.font().pixelSize() == 21  # QSS 字号保持不变
        assert table.font().pixelSize() == BASE_FONT_PX

    # 策略未变化时不重复 repolish
    assert apply_font_policy(app, 1.75) is False
    label.close()
    table.close()
