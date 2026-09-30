# Opus 5 前端交接说明

## 当前状态

OW 国服战绩 UI 已接入现有 PySide6 竞逐计算器：扫码授权、BattleTag、玩家资料/三职责段位、近期战绩、单局详情、竞技战绩导入和设置页授权状态均已实现。仍然是纯桌面应用，**不启动 localhost/HTTP 服务**。

## 前端唯一需要认识的两个对象

```python
from owstats import OWStatsService

stats = OWStatsService()
# 前端直接调用 stats.auth_start/auth_poll/auth_complete，
# 其中 auth_poll/auth_complete 会自动使用 MiniRacer SignerBridge。
```

详细调用契约见 `OWSTATS_BACKEND_API.md`。

## 第一版页面范围

1. 大神扫码授权
2. BattleTag 玩家查询
3. 最近战绩
4. 单局详情
5. 将竞技胜负导入现有竞逐计算器

不要在第一版自行增加同玩、排行榜、AI 锐评等模块。

## 线程要求

`OWStatsService` 和 `DashenQRAuth` 的网络方法是同步的。所有网络调用放 `QThread` / `QtConcurrent`，禁止阻塞 GUI 主线程。
二维码轮询建议按 `QRState.poll_interval_ms` 调度。

## 扫码授权状态

`auth.start()` 返回 PNG bytes，直接用 `QPixmap.loadFromData()`。

`auth.poll()` 可能返回：

- `waiting_scan`
- `waiting_confirm`
- `login_signature_required`
- `signing_required`
- `roles_ready`
- `expired`

签名需求通过 `state.sign_request` 暴露。将网页签名运行环境独立封装成 `SignerBridge`，不要把签名逻辑写进页面类。
完成 `auth.complete(...)` 后调用：

```python
stats.reload_credentials()
```

## 玩家与战绩

```python
player = stats.resolve_player("玩家#1234")
profile = stats.player_profile(customer_token=player.customer_token)
recent = stats.recent_matches(customer_token=player.customer_token, limit=30)
```

最近战绩展示优先使用 `recent["matches"]` 的稳定字段，不要让 UI 深度依赖大神原始 JSON。

单局：

```python
row = recent["matches"][0]
detail = stats.match_detail(
    row["match_id"],
    customer_token=recent["resolved"]["customer_token"],
    source_match=row.get("raw"),
)
```

## 导入竞逐计算器

```python
records = stats.import_drive_records(recent["matches"], normalized_input=True)
app_state.set_records(records)
```

后端只导入竞技胜负；平局跳过；组排字段无法可靠获得，默认 `grouped=False`，让 UI 继续支持用户手动批量标记组排。

## 不要做的事

- 不要重新实现大神 API；
- 不要把 token 打到日志或显示到 UI；
- 不要启动 FastAPI/Flask/localhost；
- 不要改 `drive_core.py` 的计算规则；
- 不要在 UI 主线程做网络请求；
- 不要把大神原始 JSON 当稳定 UI 接口，优先用 `owstats` 已归一化字段。
