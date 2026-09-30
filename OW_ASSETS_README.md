# OW 静态素材一次性下载器

Windows 直接双击：

```text
00_下载OW素材.cmd
```

或在项目根目录执行：

```powershell
py scripts\download_ow_assets.py
```

默认行为：只补缺失资源，不重复下载已有文件。下载完成后 UI 直接读取 `assets/`，日常启动不再联网下载这些静态素材。

资源目录：

```text
assets/
├─ heroes/       英雄头像
├─ maps/         地图背景
├─ roles/        职责图标
├─ map_modes/    地图类型图标
├─ ranks/        竞技段位图标
├─ ui/           占位图
├─ _source/      部分原始下载内容
└─ manifest.json 素材索引、来源信息
```

## 图标策略

上一版的职责、地图类型和段位 fallback 是程序自绘近似图，和游戏内 UI 风格不一致；本版取消这种做法。

- `重装 / 输出 / 支援`：内置从游戏内同款参考图提取的透明 PNG，下载器不再覆盖。
- `攻防作战 / 占领要点 / 机动推进 / 运载目标 / 攻击护送`：内置游戏内同款图标。
- `青铜 / 白银 / 黄金 / 白金 / 钻石 / 大师 / 宗师 / 英杰`：内置游戏内同款段位徽章。
- `翡翠`：2026 年新增，下载器会单独获取当前 Blizzard 宣传图来源并提取徽章；失败时明确报缺失，不再自动生成一个“像翡翠”的假图标。
- `闪点作战 / 攻防阵线（Clash）` 等新增地图类型：优先从当前 OverFast `/gamemodes` 游戏资源补齐。若网络失败则保持缺失并让 `--verify` 报告，不使用自绘伪官方图标。
- `未定级`：不是竞技段位，只保留中性占位；UI 正常应以“未定级”文字为主。

## 英雄和地图

- 英雄头像：OverFast `/heroes` 返回的 Blizzard/游戏素材地址。
- 地图背景：OverFast `/maps` 返回的当前地图截图资源。
- 已有文件默认不重复下载。

## 检查

```powershell
py scripts\download_ow_assets.py --verify
```

`--verify` 会特别检查：

```text
三职责图标
青铜~英杰（含翡翠）
Assault / Control / Escort / Hybrid / Push / Flashpoint / Clash
```

缺失会直接列出，不再把生成的近似图当作“准备完成”。

其他命令：

```powershell
# 强制重新抓取可联网素材；不会覆盖内置的游戏内同款参考图标
py scripts\download_ow_assets.py --force

# 不联网，只检查/生成中性占位项
py scripts\download_ow_assets.py --no-network
```

## 大神 GUID / 中文名 / 图片统一目录

`assets/game_catalog.json` 是英雄、地图、地图类型的统一连接表。每条记录把上游
GUID、中文名、别名和本地图片路径放在同一处；后端解析和 UI 素材查找都通过
`owstats/game_catalog.py` 使用它，不再分别维护多份 GUID 表。

更新游戏内容后运行：

```bat
00_下载OW素材.cmd
```

（仓库中的文件名在某些压缩/传输环境会显示为转义形式，实际脚本内容不受影响。）
下载器会继续执行 `scripts/sync_game_catalog.py`：从 Overstats/Dashen 的公开
`query_tool.json` 同步新英雄/新地图的 GUID、中文名和图片 URL，并只下载缺失图片。
也可以单独运行：

```bash
python scripts/sync_game_catalog.py
python scripts/sync_game_catalog.py --verify
```

如果未来出现全新的地图类型，而上游只给出中文类型名、没有类型图标 URL，工具会
生成稳定的 `dashen-mode-xxxxxxxxxx` key，并打印它期望的
`assets/map_modes/<key>.png` 路径。把对应透明 PNG 放到该路径后再次同步即可自动连接，
无需再改 Python 映射代码。
