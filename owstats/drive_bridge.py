# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict, Iterable, List

from .parsers import is_competitive_match


def matches_to_drive_records(
    matches: Iterable[Dict[str, Any]],
    *,
    grouped: bool = False,
    newest_first: bool = True,
) -> List[Dict[str, Any]]:
    """把大神近期竞技战绩转换成 drive_core.py 可直接使用的 records。

    注意：大神近期列表没有稳定、可靠的“本局是否组排”字段，所以这里不猜测。
    默认统一 grouped=False；前端可在导入后让用户批量修改组排区间。

    平局(matchRet == 0)跳过，因为现有竞逐计算器只有 W/L 规则。
    """
    source = [dict(m) for m in matches if isinstance(m, dict)]
    if newest_first:
        # 竞逐历史的自然顺序应为旧 -> 新。
        source.reverse()

    records: List[Dict[str, Any]] = []
    for match in source:
        if not is_competitive_match(match):
            continue
        try:
            result_code = int(match.get("matchRet"))
        except (TypeError, ValueError):
            continue
        if result_code == 0:
            continue
        records.append(
            {
                "result": "W" if result_code == 1 else "L",
                "grouped": bool(grouped),
                "mode": "normal",
            }
        )
    return records
