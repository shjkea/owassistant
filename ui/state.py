# -*- coding: utf-8 -*-
"""前端共享状态。

只保存 UI 需要的数据，并把计算全部委托给 drive_core，不重写任何算法。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Signal

from drive_core import DriveRules, load_rules, replay_history


class AppState(QObject):
    """规则 + 历史记录的单一数据源。"""

    rulesChanged = Signal()
    historyChanged = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        try:
            self._rules: DriveRules = load_rules()
        except Exception:
            from drive_core import default_rules
            self._rules = default_rules()
        self._records: List[Dict[str, Any]] = []
        self._start_score: int = 0
        self._history: Dict[str, Any] = self._replay()

    # ---------------- 规则 ----------------
    @property
    def rules(self) -> DriveRules:
        return self._rules

    def set_rules(self, rules: DriveRules) -> None:
        self._rules = rules
        self._history = self._replay()
        self.rulesChanged.emit()
        self.historyChanged.emit()

    # ---------------- 历史 ----------------
    @property
    def records(self) -> List[Dict[str, Any]]:
        return self._records

    @property
    def history(self) -> Dict[str, Any]:
        return self._history

    @property
    def start_score(self) -> int:
        return self._start_score

    def set_start_score(self, score: int) -> None:
        self._start_score = max(0, int(score))
        self._refresh()

    def set_records(self, records: List[Dict[str, Any]]) -> None:
        self._records = [dict(r) for r in records]
        self._refresh()

    def append_record(self, result: str, grouped: bool = False, mode: str = "normal") -> None:
        self._records.append({"result": result, "grouped": bool(grouped), "mode": mode})
        self._refresh()

    def remove_last(self) -> bool:
        if not self._records:
            return False
        self._records.pop()
        self._refresh()
        return True

    def remove_indices(self, indices: List[int]) -> int:
        keep = set(int(i) for i in indices)
        if not keep:
            return 0
        before = len(self._records)
        self._records = [r for i, r in enumerate(self._records) if i not in keep]
        self._refresh()
        return before - len(self._records)

    def update_indices(self, indices: List[int], **changes: Any) -> int:
        touched = 0
        for i in indices:
            if 0 <= i < len(self._records):
                self._records[i].update(changes)
                touched += 1
        if touched:
            self._refresh()
        return touched

    def toggle_results(self, indices: List[int]) -> int:
        touched = 0
        for i in indices:
            if 0 <= i < len(self._records):
                cur = self._records[i].get("result", "W")
                self._records[i]["result"] = "L" if cur == "W" else "W"
                touched += 1
        if touched:
            self._refresh()
        return touched

    def clear_records(self) -> None:
        self._records = []
        self._refresh()

    # ---------------- 内部 ----------------
    def _replay(self) -> Dict[str, Any]:
        return replay_history(self._records, start_score=self._start_score, rules=self._rules)

    def _refresh(self) -> None:
        self._history = self._replay()
        self.historyChanged.emit()

    @property
    def current_score(self) -> int:
        return int(self._history.get("current_score", self._start_score))
