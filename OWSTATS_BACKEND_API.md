# OW 国服战绩后端接口（backend-only）

`owstats/` 是网易大神国服战绩直连 Python 后端。本轮只维护后端，不继续设计或修改 UI。

特性：不启动 WebView、Node.js 或 localhost 服务；扫码授权仍由 MiniRacer 执行官方 JS/WASM 签名；同步网络调用由上层自行放工作线程；所有重要接口同时给出稳定字段和 `raw` 原始大神 JSON。

```python
from owstats import OWStatsService
stats = OWStatsService()
```

默认不再写死当前赛季。首次涉及赛季的玩家查询会从大神当前赛季数据自动识别并缓存；例如进入 S25 后，`season_count=3` 会自动返回 S25/S24/S23。`DEFAULT_DASHEN_CURRENT_SEASON=24` 仅在上游无法识别时作为兜底。可用 `stats.season_status()` 查看 `current_season/source`。如确实需要固定赛季，仍可显式传 `OWStatsService(current_season=24)`。

## 1. 授权与 BattleTag

```python
stats.auth_status()
start = stats.auth_start()
state = stats.auth_poll(start.session_id)
stats.auth_complete(start.session_id, role_id)

player = stats.resolve_player("玩家#12345")
# player.full_id / bnet_id / customer_token / icon_url
```

MiniRacer 仅在扫码签名实际发生时延迟加载。

## 2. 玩家资料

```python
card = stats.player_card("玩家#12345")
# 或 stats.player_card(customer_token="...")
```

稳定字段：

```text
battle_tag
bnet_id
icon_url
title
level
game_time
resolved
raw             # queryCard 完整响应
```

## 3. 标准赛季资料（竞技 / 快速）

```python
result = stats.player_seasons("玩家#12345", season_count=3)
# 任意范围：start_season=20, end_season=24
```

每赛季保留：

```text
competitive.roles
  role_type
  match_sum
  wins / losses
  win_rate
  current        # lastRankInfo
  peak           # maxRankInfo 等峰值字段
  raw
competitive.hero_stats
competitive.raw  # sport queryCountInfo 原始响应
quick.hero_stats
quick.raw        # leisure queryCountInfo 原始响应
raw_modes
```

## 4. 英雄统计

```python
heroes = stats.hero_stats("玩家#12345", season_count=3)
```

以下大神原始列表逐项完整保留：

```text
presetsHeroUseSummaryList
v6HeroUseSummaryList
openHeroUseSummaryList
presetsHeroList
openHeroList
```

同时提供归一化 `heroes`：`hero_guid / hero_name / role_type / icon_url / play_time / match_sum / wins / win_rate / kills / assists / deaths / hero_damage / healing / resist_damage / raw`。

## 5. 体育场 / 角斗赛季数据

```python
stadium = stats.stadium_seasons("玩家#12345", season_count=3)
```

来源为大神 `fight/queryCount`，主要暴露：

```text
stadium.roles                  # roleTypeCountData，含 current / peak
stadium.hero_use_summary_list  # heroUseSummaryList 原样
stadium.heroes                 # 归一化英雄摘要
stadium.raw                    # 完整原始响应
```

## 6. 三赛季完整逐场战绩

```python
history = stats.three_season_matches("玩家#12345")
# 等价：stats.match_history("玩家#12345", season_count=3)
```

**不再有 30/100 场结果截断。** 默认逐赛季抓：

```text
标准：sport + leisure
角斗：QuickFight + LeisureFight + SportFight
```

每个标准模式先收 `queryCountInfo` 自带记录，再从 `queryMatchList page=1` 连续翻页；角斗从 `fight/queryMatchList page=1` 连续翻页。停止条件仅为：空页、重复页、没有新 `matchId`、请求错误，或触发异常保护阈值。

`max_pages_per_mode=250` 是防止上游异常无限循环的保险阀，不是正常场次数上限。触发时：

```python
history["complete"] == False
history["warnings"]
```

返回中 `season_summaries[*].modes[*]` 会给出 `count / pages / complete / request_season / stop_reason`，便于调用方判断是否真的抓完整。

只要标准快速/竞技、不取角斗：

```python
stats.match_history("玩家#12345", season_count=3, include_fight=False)
```

只取竞技：

```python
stats.match_history("玩家#12345", season_count=3, modes=("sport",), include_fight=False)
```

逐场稳定字段包括：

```text
match_id
result / result_code
competitive
game_mode / instance_type
map_guid / map_name
hero_guid / hero_name / hero_icon
role_type
team_score / opponent_score
kills / assists / deaths
hero_damage / healing / resist_damage
final_hits / damage_taken / healing_taken
game_time_sec / start_time / begin_ts
season / source_mode
raw
```

旧 `recent_matches(limit=...)` 仅为旧 UI 兼容的轻量接口。需要完整历史必须用 `match_history()` / `three_season_matches()`。

## 7. 单局详情

```python
row = history["matches"][0]
detail = stats.match_detail(
    row["match_id"],
    customer_token=history["resolved"]["customer_token"],
    source_match=row["raw"],
)
```

返回：

```text
match_kind            # normal / fight
detail                # queryMatchInfo 原始响应
detail_root           # 提取后的比赛根节点
scoreboard_players    # 双方记分板稳定字段
normalized            # 旧调用方兼容视图
```

记分板玩家稳定字段包括 BattleTag/bnetId/customerToken、英雄/职责、K/A/D、伤害、治疗、减伤、终结、承伤、受疗、目标时间、段位、组排 friendBnetIds，以及 `raw`。

## 8. 单局“全员详细”英雄数据

```python
full = stats.match_all_players_detail(
    row["match_id"],
    customer_token=history["resolved"]["customer_token"],
    source_match=row["raw"],
)
```

普通对局会：

1. 先取双方记分板；
2. 获取每名公开玩家的 `customerToken`（缺失时尝试 BattleTag 解析）；
3. 对每名玩家用 `customerToken + matchId` 再查一次 `queryMatchInfo`；
4. 返回该玩家自己的 `heroList` 与每英雄 `statMap`；
5. 单个玩家失败只记录该玩家 `error`，不会丢整局。

关键字段：

```text
all_players_supported
all_players_complete
player_count
available_player_count
players[]
  name / bnet_id / customer_token / team_type
  icon_url / title / rank_info
  scoreboard
  hero_list             # 上游 heroList 原样
  heroes[]              # hero_guid / stat_map / raw
  success / error
  detail_raw / card_raw
```

角斗模式目前按上游能力明确返回 `all_players_supported=False`，只保留其完整详情/记分板，不伪造普通对局式的全员 `heroList`。

## 9. 历史段位

```python
ranks = stats.rank_history("玩家#12345", start_season=15, end_season=24)
```

不限最近两赛季。每赛季返回：

```text
competitive.roles   # sport: current + peak + 场次 + 胜率
competitive.raw
has_stadium
stadium              # fight/queryCount 的职责与英雄摘要
```

若只关心标准竞技：

```python
stats.rank_history("玩家#12345", start_season=15, end_season=24, include_stadium=False)
```

后端不会用当前段位伪造峰值；只有上游存在 `maxRankInfo` 等峰值字段时才返回实际峰值。

## 10. 同玩记录与同玩单局

```python
same = stats.sameplay_records("玩家A#1111", "玩家B#2222", season_count=3)
```

分别完整抓取双方历史，以 `matchId` 求交集。返回双方各自的同一局记录、赛季、是否竞技、统计摘要以及双方抓取是否完整。

直接取同玩列表中的某局全员详细：

```python
same_detail = stats.sameplay_detail(
    "玩家A#1111",
    "玩家B#2222",
    index=0,                   # 或 match_id="..."
)
```

## 11. 地区职责榜

```python
board = stats.province_rank("浙江", "输出")
```

职责支持 `tank / dps / healer / open` 及常用中文别名。返回归一化 `entries` + `raw`。

## 12. 英雄地区榜

```python
board = stats.hero_leaderboard("浙江", hero_guid="...", mode="preset")
```

`mode` 支持 `preset/open`；后端实际发送大神所需的 `SportPreset/SportOpen`。当前上游 `data.itemList` 会归一化为 `entries`，同时保留 `raw`。

## 13. 玩家自己的大神英雄榜信息

```python
board = stats.user_billboard("玩家#12345")
```

对应 `billboard/customGetUserHeroBillboard`，返回 `entries` + 完整 `raw`。

## 14. 竞逐计算器桥接（兼容旧项目）

```python
records = stats.import_drive_records(history["matches"], normalized_input=True)
```

只转换现有计分器能处理的竞技胜/负；平局不猜规则；不推断是否组排。

## 15. 测试

```bash
python -m unittest discover -s owstats/tests -v
python owstats_smoke_test.py
python drive_core.py
python -m compileall -q .
```

离线测试覆盖完整分页、角斗分页、玩家资料、三赛季段位和峰值、五类英雄数据、体育场赛季数据、普通/角斗单局详情、全员 heroList/statMap、同玩交集、省榜/英雄榜/玩家榜及竞逐桥接。

## 12. 国服中文本地化

后端新增统一 `owstats.localization`，地图、英雄、核心竞技段位、角斗领域赛阶、职责、大神业务模式和游戏模式统一使用国服中文展示名；未知的新上游值不会硬翻译，会原样保留以避免误译。

逐场战绩新增/调整字段：

```text
map_name          # 国服中文展示名，例如 斗兽场
map_name_raw      # 大神原始值，例如 Colosseo
hero_name         # 国服中文展示名，例如 雾子
hero_name_raw     # 大神原始值，例如 Kiriko
game_mode         # 上游机器值，继续用于逻辑判断
game_mode_zh      # 竞技 / 快速 / 角斗竞技 / 角斗快速…
instance_type     # 上游机器值
instance_type_zh  # 占领要点 / 运载目标 / 攻击/护送 / 机动推进 / 闪点作战 / 攻防阵线…
role_type         # tank / dps / healer / open 等稳定机器值
role_name_zh      # 重装 / 输出 / 支援 / 开放职责
```

段位：

```text
rank_name         # 中文展示名，例如 白金 / 宗师 / 英杰
rank_name_zh      # 与 rank_name 相同，便于前端显式取中文
rank_name_raw     # 大神原始值，例如 Platinum / Grandmaster / Champion
rank_sub_tier
rank_score                 # 原始分数/MMR，仅作数据保留，不能单独证明已定级
is_ranked                  # 只有上游显式返回可见段位名时才为 true
rank_status                # ranked / unranked / unranked_hidden_mmr
display_rank               # 可直接展示；未定级时固定为“未定级”
hidden_mmr                 # 未定级但上游仍返回分数时保留在这里
rank_score_is_display_rank # 未定级时为 false，禁止前端拿 rank_score 反推青铜等段位
```

**重要：段位不能由 `rankScore/MMR` 反推。** 大神可能在玩家仍处于定级/未定级状态时返回内部匹配分。后端只认 `rank_name/rankName` 等显式可见段位字段；只有分数而没有显式段位名时，一律返回 `display_rank=未定级`。

当前覆盖核心竞技段位（青铜、白银、黄金、白金、翡翠、钻石、大师、宗师、英杰）及角斗领域常见赛阶，并覆盖当前核心地图、近期新增地图和常见历史/街机地图。英雄名称同样统一国服译名。调用方不需要自行维护翻译表。

也可直接调用：

```python
from owstats import localize_map, localize_rank, localize_hero, localize_game_mode

localize_map("Colosseo")      # 斗兽场
localize_rank("Champion")     # 英杰
localize_hero("Kiriko")       # 雾子
localize_game_mode("SportFight")  # 角斗竞技
```
