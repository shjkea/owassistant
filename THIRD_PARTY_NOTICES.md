# Third-Party Notices / 第三方说明

本文件用于说明 `owassistant` 仓库中第三方项目、素材、服务与数据的来源及授权边界。

## 1. 本项目 MIT License 的适用范围

根目录 [`LICENSE`](LICENSE) 中的 MIT License 适用于仓库作者有权以 MIT 方式授权的原创源码与原创文档。

以下内容**不因本仓库使用 MIT License 而自动获得 MIT 授权**：

- 第三方项目的源码或其衍生内容；
- 《守望先锋》相关图片、英雄头像、地图背景、段位 / 职责 / 模式图标等游戏素材；
- 第三方字体、图片、图标及其他媒体文件；
- 来自网易大神、Battle.net 或其他上游服务的配置、接口数据、缓存数据及衍生数据；
- 商标、产品名称和服务名称；
- 其他不属于仓库作者原创、或仓库作者无权重新授权的内容。

这些内容应继续遵守其原始来源的许可证、服务条款及权利人要求。

## 2. BnetSwitchLite

账号切换模块的设计参考了：

- Project: **BnetSwitchLite**
- Repository: https://github.com/kabxx/BnetSwitchLite
- License: **MIT License**

本项目主要参考其 Windows 端关于账号发现、登录状态验证、配置快照、事务恢复和切换后校验等行为设计，并为当前 PySide6 应用重新实现了 Python 后端。

本项目不会读取或保存 Battle.net / 网易明文密码。账号切换所保存的本地登录状态可能具有登录复用能力，应视为敏感数据，不应公开上传或提交到 Git。

## 3. Overstats

网易大神 / 国服战绩相关实现参考了：

- Project: **Overstats**
- Repository: https://github.com/AddOneSecondL/Overstats

参考范围包括网易大神相关请求链路、部分数据结构解析方式，以及公开的游戏目录数据。

本项目的 `scripts/sync_game_catalog.py` 会使用：

```text
https://raw.githubusercontent.com/AddOneSecondL/Overstats/master/res/query_tool.json
```

作为英雄 / 地图 GUID、中文名称和相关目录同步的数据来源之一。

Overstats 仓库本身也包含第三方素材与上游衍生数据。对这些内容进行使用、缓存、再分发或打包时，应单独检查其原始授权边界；本项目的 MIT License 不对这些内容重新授权。

## 4. Blizzard / Overwatch / Battle.net

《守望先锋》（Overwatch）、Battle.net 及相关游戏内容、名称、Logo、图片、图标、角色、地图和其他素材归其各自权利人所有。

本项目为非官方第三方项目，与 Blizzard Entertainment 或其关联方无隶属、授权、赞助或背书关系。

## 5. 网易 / 网易大神

网易、网易大神及相关产品名称、服务、接口与上游数据归其各自权利人所有。

本项目为第三方客户端工具，与网易或网易大神无隶属、授权、赞助或背书关系。上游接口发生变化时，本项目相关功能可能失效。

## 6. DeepSeek

实验性的 AI 锐评功能可调用 DeepSeek API。DeepSeek、相关模型与 API 服务受其自己的服务条款、价格政策和权利声明约束。

用户应使用自己的 API Key，并自行承担 API 使用产生的费用。API Key 不应提交到 GitHub 或包含在公开日志 / 截图中。

## 7. 再分发建议

如果你准备 fork、重新打包、公开分发或商业使用本项目，请自行确认：

1. 第三方图片、字体和游戏素材是否允许再分发；
2. 上游配置和接口衍生数据是否允许镜像、缓存或公开提供；
3. 上游项目许可证要求是否已经满足；
4. 发布包中是否意外包含 Token、API Key、Cookie、Battle.net 登录状态或其他敏感本地数据。

如对某项第三方内容没有明确授权，请不要因为本仓库使用 MIT License 就默认该内容可以按 MIT License 继续分发。
