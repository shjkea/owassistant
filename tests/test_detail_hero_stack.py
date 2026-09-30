# -*- coding: utf-8 -*-
"""单局详情 PlayerRow 多英雄头像组合（1 大 + 右侧上下 2 小）离屏测试。

运行：
    python -m pytest tests/test_detail_hero_stack.py -q
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.assets import library  # noqa: E402
from ui.ow_widgets import PlayerRow  # noqa: E402

HEROES = [
    ("Winston", "207165582859042825"),
    ("Kiriko", "207165582859043377"),
    ("Ashe", "207165582859043328"),
    ("Ana", "207165582859043131"),
    ("D.Va", "207165582859042938"),
]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([sys.argv[0]])


def _player(n: int, *, is_self: bool = False) -> dict:
    return {
        "name": "测试玩家#1001",
        "is_self": is_self,
        "role_type": "tank",
        "role_name": "重装",
        "kills": 12, "deaths": 3, "assists": 7,
        "hero_damage": 8200, "healing": 0, "resist_damage": 4100,
        "rank": {}, "title": "头衔",
        "heroes": [
            {"hero_name": name, "hero_name_raw": name, "hero_guid": guid}
            for name, guid in HEROES[:n]
        ],
    }


def _row(app, n: int, *, reserved: int = 1, is_self: bool = False, span=(True, False, False)) -> PlayerRow:
    row = PlayerRow(library(), parent=None)
    row.hero.set_reserved_slots(reserved)
    row.set_player(_player(n, is_self=is_self), span, "")
    row.resize(640, row.height())
    row.show()
    app.processEvents()
    return row


def test_single_hero_only_main(app):
    row = _row(app, 1, reserved=0)
    hero = row.hero
    assert hero.is_stacked()
    assert hero.hero_count() == 1
    assert hero.secondary_boxes() == []
    # 单英雄且无预留：不留空槽，宽度就是主头像。
    assert hero.width() == PlayerRow.HERO_SIZE


def test_two_heroes_one_big_one_small_centered(app):
    row = _row(app, 2)
    hero = row.hero
    assert hero.hero_count() == 2
    boxes = hero.secondary_boxes()
    assert len(boxes) == 1
    box = boxes[0]
    size = PlayerRow.HERO_SIZE
    assert box.width() < size and box.height() < size
    # 在主头像右侧，并在垂直方向居中。
    assert box.left() >= size
    assert abs((box.top() + box.height() / 2) - size / 2) <= 1


def test_three_heroes_right_column_stacked(app):
    row = _row(app, 3)
    hero = row.hero
    assert hero.hero_count() == 3
    top, bottom = hero.secondary_boxes()
    size = PlayerRow.HERO_SIZE
    # 同一列、上下排列、互不重叠、都在主头像高度范围内。
    assert top.left() == bottom.left() >= size
    assert top.bottom() < bottom.top()
    assert top.top() >= 0 and bottom.bottom() < size
    # 只占一列宽度。
    assert hero.width() == size + hero._gap + hero._secondary_size
    assert hero.height() == size


@pytest.mark.parametrize("n", [4, 5])
def test_four_or_more_only_first_three(app, n):
    row = _row(app, n)
    assert row.hero.hero_count() == 3
    assert len(row.hero.secondary_boxes()) == 2


def test_row_height_and_layout_unchanged(app):
    rows = {n: _row(app, n) for n in (1, 2, 3, 4)}
    base = rows[1]
    for n, row in rows.items():
        assert row.height() == PlayerRow.ROW_HEIGHT, n
        assert row.hero.height() == PlayerRow.HERO_SIZE, n
        assert row.hero.geometry() == base.hero.geometry(), n
        for attr in ("role", "name", "kda", "extra", "rank_badge"):
            assert getattr(row, attr).geometry() == getattr(base, attr).geometry(), (n, attr)
        assert row.kda.text() == "K:12 D:3 A:7"
        assert row.party.geometry() == base.party.geometry(), n


def test_self_highlight_and_party_unaffected(app):
    row = _row(app, 3, is_self=True, span=(True, True, False))
    assert row.is_self()
    assert row.height() == PlayerRow.ROW_HEIGHT
    assert row.party.geometry().height() == row.height()
    assert row.party.geometry().left() == 0
    # 能正常绘制（本人高亮 paintEvent 不因多英雄出错）。
    assert not row.grab().isNull()
