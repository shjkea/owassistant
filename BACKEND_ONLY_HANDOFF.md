# Backend-only handoff

本轮按要求**没有继续设计或修改 UI**，只扩展 `owstats/` 后端。旧 UI 仍可继续调用兼容接口，但新能力不要求 UI 使用。

主要后端入口：

```python
player_card()
player_seasons()
hero_stats()
stadium_seasons()
match_history()
three_season_matches()
match_detail()
match_all_players_detail()
rank_history()
sameplay_records()
sameplay_detail()
province_rank()
hero_leaderboard()
user_billboard()
```

三赛季完整历史不再使用 30/100 场上限，标准快速/竞技以及 QuickFight/LeisureFight/SportFight 都按页抓到末页；每个分页结果都有 `complete` 与 `stop_reason`。

普通单局的“全员详细”会对记分板中每名公开玩家进一步获取自己的 `heroList/statMap`；角斗目前明确标记不支持这种补全，不伪造数据。

所有新接口优先提供稳定字段，同时保留上游 `raw`，供后续任意 UI 或分析脚本自行使用。

完整字段说明见 `OWSTATS_BACKEND_API.md`。

## 2026-09-28 本地化补全

仅修改 `owstats/` 后端，没有修改 `ui/`。新增 `owstats/localization.py`，统一处理国服地图、英雄、段位、职责、模式中文名。规范化结果同时保存中文展示字段和大神原始字段；未知新值原样回退，不静默误译。新增本地化单测后，后端测试总数为 22。


## 段位判定补充

不要使用 `rankScore/MMR` 反推出青铜/白银等可见段位。大神可能对未定级玩家仍返回内部匹配分。后端 `normalize_rank_info()` 现在只有在存在显式 `rank_name/rankName/...` 时才设置 `is_ranked=true`；否则 `display_rank` 固定为“未定级”，原始分数仅保留为 `hidden_mmr/rank_score`。
