# 竞逐计算器前端接口说明

前端请尽量只修改 UI 文件，不要复制或重写 `drive_core.py` 中的计分与 DP 算法。

## 文件职责

- `drive_core.py`：纯算法、历史回放、DP、配置读写；无 GUI 依赖。
- `config.json`：默认规则，可由“规则设置”页修改或导入/导出。
- `app_stub.py`：最小调用示例，可删除后换成 PySide6 前端。

## 最常用的四个入口

```python
from drive_core import load_rules, predict, build_history, replay_history

rules = load_rules()
```

### 1. 预测

```python
result = predict(
    current_score=1920,
    win_rate=0.55,          # 注意是 0~1，不是 55
    grouped=False,
    mode="normal",         # normal / duel
    probability_games=[10, 15, 20, 25, 30, 35],
    rules=rules,
)
```

关键字段：

```text
result["next_checkpoint"]
result["targets"]["next"]["expected_games"]
result["targets"]["final"]["expected_games"]
result["targets"]["final"]["quantile_games"]
result["targets"]["final"]["probability_within"]
```

`quantile_games` 的 key 是字符串概率，例如 `"0.5000"`、`"0.9000"`。

### 2. 一键构造历史

```python
records = build_history(
    sequence="+-++-++++-++++-++",
    group_ranges="3-9",
    duel_ranges="",
)
```

局号格式支持：

```text
3-9
3-9,12,15-17
```

### 3. 回放历史

```python
history = replay_history(records, start_score=0, rules=rules)
```

主要字段：

```text
history["current_score"]
history["summary"]["games"]
history["summary"]["wins"]
history["summary"]["losses"]
history["summary"]["win_rate_percent"]
history["summary"]["group_games"]
history["rows"]
```

每个 `rows` 元素包含：

```text
game
result / result_text
grouped / queue_text
mode / mode_name
score_before
delta
score_after
checkpoint_floor_after
```

### 4. 前端逐局编辑

前端内部可以直接维护这种列表：

```python
records = [
    {"result": "W", "grouped": False, "mode": "normal"},
    {"result": "L", "grouped": True,  "mode": "normal"},
]
```

修改完后直接：

```python
history = replay_history(records, start_score=0, rules=rules)
```

不需要前端自己计算任何 delta。

## 配置

`config.json` 当前默认普通规则：

```text
节点：500, 1000, 1600, 2200, 3000, 4000
胜：+210
组排胜额外：+50
败场 nominal penalty：0, 21, 21, 42, 42, 63
```

规则设置页如需保存：

```python
from drive_core import rules_from_dict, save_rules
new_rules = rules_from_dict(ui_config_dict)
save_rules(new_rules)
```

如需导出配置，调用 `rules_to_dict(rules)` 后写 JSON 即可。

## 固定联调测试

```text
战绩：+-++-++++-++++-++
3-9：组排
其余：单排
全部：普通
起始分：0
期望最终分：2925
```

可以运行：

```bash
python drive_core.py
```

正常应输出 `"ok": true` 和 `"sample_final_score": 2925`。

---

## OW 国服战绩扩展

新增的战绩/大神授权后端位于 `owstats/`，完整接口见 `OWSTATS_BACKEND_API.md`。

该后端不启动 localhost 服务。推荐前端只调用 `OWStatsService` / `DashenQRAuth`，不要把网易大神协议请求直接写进 UI 文件。
