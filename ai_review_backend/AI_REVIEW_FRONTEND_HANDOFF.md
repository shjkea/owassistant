# 给 PySide6 前端的最短接入说明

## 重要：玩家人数必须动态渲染

不要写死 5v5、10 人、每队 5 张卡，也不要写死 `p01` ~ `p10`。

`players_for_ui(ctx)` 返回多少玩家就渲染多少玩家，并按 `team` 字段动态分组。当前项目既可能出现标准 5v5，也可能出现 6v6（每队 6 人、共 12 人）；以后其他模式也应自然兼容。

玩家临时 ID 是动态生成的 `p01`、`p02` …，上限取决于该局实际玩家数量。前端只把返回的 `player_id` 原样用于选择，不自行生成或推断。


前端不要实现任何 API / prompt / 数据压缩逻辑，只调用 `ai_review`。

```python
from ai_review import AIReviewService, ReviewRequest, CancellationToken
```

应用级创建一次：

```python
ai_review_service = AIReviewService(api_key=deepseek_api_key)
```

打开某局 AI 睿评页时，用已经获取好的详情：

```python
ctx = ai_review_service.prepare_match(
    detail,                 # 现有 match_all_players_detail() 结果
    match_meta=match_row,   # 当前那场 match_view / 战绩行
    self_identity={
        "battle_tag": current_battle_tag,
        "bnet_id": current_bnet_id,
        "customer_token": current_customer_token,
    },
)

players = ai_review_service.players_for_ui(ctx)
```

`players` 就是前端玩家选择卡所需数据；默认勾选 `is_self=True`。

开始睿评：

```python
req = ReviewRequest(style="coach", scope="self")
```

多选：

```python
req = ReviewRequest(
    style="roast",
    scope="selected",
    target_player_ids=("p01", "p07"),
)
```

全局：

```python
req = ReviewRequest(style="data", scope="global")
```

网络调用必须放 Worker/QThread：

```python
cancel = CancellationToken()
result = ai_review_service.stream_review(
    ctx,
    req,
    on_delta=lambda text: emit_delta(text),
    on_status=lambda status: emit_status(status),
    cancel_token=cancel,
)
```

不要显示 `reasoning_content`；后端已经过滤，只会输出最终正文。
