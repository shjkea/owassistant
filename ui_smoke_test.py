# -*- coding: utf-8 -*-
"""前端冒烟测试：离屏创建窗口，走一遍关键交互，校验不改动计算结果。

运行：
    python ui_smoke_test.py
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402

from drive_core import default_rules, records_to_dicts, build_history, replay_history  # noqa: E402
from main import build_app  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


def pump(ms: int = 400) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _snapshot_user_settings():
    """备份真实 QSettings，测试结束后逐键还原（含 closeEvent 写入的 geometry）。"""
    from PySide6.QtCore import QSettings

    settings = QSettings("ow_drive_split", "drive_calculator")
    snapshot = {key: settings.value(key) for key in settings.allKeys()}

    def restore() -> None:
        for key in settings.allKeys():
            if key not in snapshot:
                settings.remove(key)
        for key, value in snapshot.items():
            settings.setValue(key, value)
        settings.sync()

    return snapshot, restore


def main() -> int:
    settings_snapshot, restore_settings = _snapshot_user_settings()
    app = build_app([sys.argv[0]])
    win = MainWindow()
    win.resize(1280, 800)
    win.show()
    pump(200)

    hist = win.history_page
    state = win.state

    # 1) 一键导入官方联调样例，期望最终 2925
    hist.seq_edit.setPlainText("+-++-++++-++++-++")
    hist.group_edit.setText("3-9")
    hist.duel_edit.setText("")
    hist._do_import()
    assert state.current_score == 2925, state.current_score
    assert hist.tile_score._value.text() == "2925"
    assert hist.tile_games._value.text() == "17"
    assert hist.tile_group._value.text() == "7"
    assert hist._model.rowCount() == 17
    print("[ok] 一键导入 -> 2925 分，17 局，组排 7 局")

    # 2) 与核心直算结果逐行比对
    expect = replay_history(
        build_history("+-++-++++-++++-++", "3-9", ""),
        start_score=0,
        rules=state.rules,
    )
    for i, row in enumerate(expect["rows"]):
        assert hist._model.data(hist._model.index(i, 5)) == str(row["score_after"])
        assert hist._model.data(hist._model.index(i, 4)) == f"{row['delta']:+d}"
    print("[ok] 表格每行 delta / 赛后总分与核心回放一致")

    # 3) 快速追加 / 删除最后一局
    before = state.current_score
    hist._append("W")
    assert len(state.records) == 18
    hist._remove_last()
    assert len(state.records) == 17 and state.current_score == before
    print("[ok] 添加胜 / 删除最后一局")

    # 4) 批量修改选中局
    hist.table.selectAll()
    hist._update_selected(grouped=True)
    assert state.history["summary"]["group_games"] == 17
    hist._update_selected(mode="duel")
    assert state.history["summary"]["duel_ranges"] == "1-17"
    hist._toggle_selected()
    assert state.history["summary"]["sequence"] == "-+--+----+----+--"
    print("[ok] 批量改组排 / 决斗 / 切换胜负")

    # 5) 删除选中
    hist.table.clearSelection()
    hist.table.selectRow(0)
    hist._delete_selected()
    assert len(state.records) == 16
    print("[ok] 删除选中局")

    # 6) 回填 + 清空
    hist._pull_from_records()
    assert hist.seq_edit.toPlainText() == state.history["summary"]["sequence"]
    hist._clear_all()
    assert state.records == [] and state.current_score == 0
    print("[ok] 回填输入框 / 清空全部")

    # 7) 非法输入只出提示条，不抛异常
    hist.seq_edit.setPlainText("+-x+")
    hist._do_import()
    assert hist.banner.property("level") == "error", hist.banner.property("level")
    assert state.records == []
    hist.seq_edit.setPlainText("+-++-++++-++++-++")
    hist.group_edit.setText("3-99")
    hist._do_import()
    assert state.records == []
    hist.group_edit.setText("3-9")
    hist.duel_edit.setText("")
    hist._do_import()
    assert state.current_score == 2925, state.current_score
    print("[ok] 非法战绩串 / 越界局号只提示不崩溃")

    # 8) 预测页
    pred = win.prediction_page
    pred.score_spin.setValue(3187)
    pred.win_spin.setValue(55)
    pred.queue_seg.set_current_index(0)
    pred.mode_seg.set_current_index(0)
    pred.n_spin.setValue(10)
    pred._start_compute()
    for _ in range(40):
        pump(120)
        if pred._last_result is not None:
            break
    assert pred._last_result is not None, "预测未返回"
    from drive_core import predict

    ref = predict(3187, 0.55, False, "normal", [10], rules=state.rules)
    assert pred.tile_next._value.text() == str(ref["next_checkpoint"])
    assert pred.tile_gap._value.text() == str(ref["next_checkpoint"] - 3187)
    exp_final = ref["targets"]["final"]["expected_games"]
    assert pred.tile_exp_final._value.text() == f"{exp_final:.1f}"
    q = ref["targets"]["final"]["quantile_games"]
    for key in ("0.5000", "0.8000", "0.9000", "0.9500"):
        assert pred.quantile_tiles[key]._value.text() == str(q[key])
    p10 = ref["targets"]["final"]["probability_within"]["10"]
    assert pred.tile_n_final._value.text() == f"{p10 * 100:.1f}"
    print(f"[ok] 预测页数值与核心一致（期望 {exp_final:.1f} 局，10 局内 {p10*100:.1f}%）")

    # 9) 预测页同步历史分数与胜率
    pred._pull_history_score()
    assert pred.score_spin.value() == 2925
    pred._pull_history_winrate()
    assert pred.win_spin.value() == round(state.history["summary"]["win_rate_percent"])
    print("[ok] 预测页同步历史分数 / 胜率")

    # 10) 规则设置页：修改 -> 生效 -> 恢复默认
    st = win.settings_page
    st.cp_edit.setText("500, 1000, 1600, 2200, 3000, 4000")
    st._sync_penalty_labels()
    st._editors["normal"].win_spin.setValue(300)
    cfg = st._collect()
    assert cfg["modes"]["normal"]["win_points"] == 300
    st._apply(cfg, persist=False)
    assert state.rules.modes["normal"].win_points == 300
    st._restore_default()
    assert state.rules.modes["normal"].win_points == default_rules().modes["normal"].win_points
    assert state.current_score == 2925
    print("[ok] 规则设置修改生效 / 恢复默认后历史分数仍为 2925")

    # 11) 非法节点只提示
    st.cp_edit.setText("500, 400")
    st._save()
    assert st.banner.property("level") == "error"
    assert state.rules.checkpoints[-1] == 4000
    st._reload_current()
    print("[ok] 非法节点只提示不崩溃")

    # 12) 缩放不崩
    for w, h in ((1040, 680), (1600, 900), (1100, 700)):
        win.resize(w, h)
        pump(80)
    print("[ok] 窗口缩放正常")

    # 13) 四个页面切换 + 导航顺序 / 名称 / 应用名称
    from ui.main_window import APP_NAME, NAV_ITEMS

    assert [text for text, _tip in NAV_ITEMS] == ["国服战绩", "账号切换", "竞逐预测", "竞逐历史", "规则设置"], NAV_ITEMS
    assert [b.text().strip() for b in win._nav_buttons] == [
        "国服战绩", "账号切换", "竞逐预测", "竞逐历史", "规则设置",
    ], [b.text() for b in win._nav_buttons]
    expected_pages = [win.owstats_page, win.bnet_page, win.prediction_page, win.history_page, win.settings_page]
    for i in range(len(expected_pages)):
        win._select(i)
        pump(60)
        assert win.stack.currentIndex() == i
        assert win.stack.currentWidget() is expected_pages[i], i
    assert hasattr(win, "owstats_page")
    assert win.owstats_page._service is win.stats_service
    assert win.settings_page._stats_service is win.stats_service
    print("[ok] 导航顺序 国服战绩 / 账号切换 / 竞逐预测 / 竞逐历史 / 规则设置，与页面一一对应")

    # 13b) 应用名称：不再叫「竞逐计算器」
    assert APP_NAME == "守望先锋 国服助手", APP_NAME
    assert win.windowTitle() == APP_NAME, win.windowTitle()
    assert app.applicationName() == APP_NAME, app.applicationName()
    from PySide6.QtWidgets import QFrame as _QFrame, QLabel as _QLabel

    brand = win.findChild(_QLabel, "BrandTitle")
    assert brand is not None, "侧栏缺少品牌标题"
    assert "竞逐计算器" not in brand.text(), brand.text()
    assert "国服助手" in brand.text().replace("\n", ""), brand.text()
    # 「导入竞逐计算器」按钮名称保留：它表达的是导入竞逐计算功能，不是程序名。
    assert win.owstats_page.drive_bar.import_btn.text() == "导入竞逐计算器"
    print(f"[ok] 应用名称统一为「{APP_NAME}」，导入竞逐计算器按钮名称保留")

    # 13c) 左侧导航缩窄，且未改成 frameless window
    from PySide6.QtCore import Qt as _Qt

    sidebar = win.findChild(_QFrame, "Sidebar")
    assert sidebar is not None, "侧栏未找到"
    assert 130 <= sidebar.width() <= 152, sidebar.width()
    assert not win.windowFlags() & _Qt.FramelessWindowHint, "不得为标题栏改成 frameless"
    from ui import win_titlebar

    assert "FramelessWindowHint" not in open(
        win_titlebar.__file__, encoding="utf-8"
    ).read(), "深色标题栏实现不得引入 frameless"
    print(f"[ok] 左侧导航宽度 {sidebar.width()}px；窗口仍是原生有边框窗口（非 frameless）")

    # 确认 config.json 未被冒烟测试改写
    from drive_core import load_rules

    disk = load_rules()
    assert disk.modes["normal"].win_points == 210, "config.json 被意外修改"
    print("[ok] config.json 未被测试改动")

    # 14) 真实 QSettings 未被污染
    from PySide6.QtCore import QSettings as _QSettings

    win.close()  # closeEvent 会写 geometry，必须在还原之前触发
    restore_settings()
    live = _QSettings("ow_drive_split", "drive_calculator")
    assert sorted(live.allKeys()) == sorted(settings_snapshot.keys())
    for key, value in settings_snapshot.items():
        assert live.value(key) == value, f"真实设置 {key} 被测试改动"
    print(f"[ok] 真实 QSettings 未被污染（{len(settings_snapshot)} 个键全部还原）")

    print("\n全部冒烟测试通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
