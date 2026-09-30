# Windows 发布封装

这套配置只负责发布，不改变 GitHub 展示素材。`screenshoot/`、测试、探针、文档、预览脚本都不会进入最终发行包。

## 两种发行形式

### 1. 安装版

发布文件：`release/OWAssistant_Setup_v<版本>.exe`

- 对外只有一个安装程序 EXE。
- 安装器内包含 PyInstaller onefile 版 `OWAssistant.exe`。
- 默认按当前用户安装到 `%LOCALAPPDATA%\Programs\OWAssistant`，不要求管理员权限。
- 创建开始菜单快捷方式，可选桌面快捷方式。
- 正常生成卸载入口。

### 2. 免安装版

发布文件：`release/OWAssistant_Portable_v<版本>.zip`

解压后目录结构大致为：

```text
OWAssistant_Portable_v0.9.0/
├─ OWAssistant.exe
└─ _internal/
```

用户只需要双击 `OWAssistant.exe`。`_internal` 是程序依赖，不需要单独安装 Python。

## 构建

要求：Windows 10/11 x64、64 位 Python 3.14、Inno Setup 7（只在生成安装版时需要）。

最简单：直接双击项目根目录的 `BUILD_RELEASE.cmd`。

或者 PowerShell 在项目根目录执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\packaging\windows\build_all.ps1
```

脚本会：

1. 创建 `.venv-build`；
2. 安装项目依赖、PyInstaller 与 pytest；
3. 分套运行测试；
4. 构建安装版所用的 onefile EXE；
5. 构建免安装 onedir 版并压缩 ZIP；
6. 调用 Inno Setup 生成 Setup EXE；
7. 输出 `release/SHA256SUMS.txt`。

如本机尚未安装 Inno Setup 7：

```powershell
winget install --id JRSoftware.InnoSetup.7 -e -s winget -i
```

只想先构建免安装版和单文件 EXE、暂不生成安装器：

```powershell
.\packaging\windows\build_all.ps1 -SkipInstaller
```

## 发布包明确不包含

- `tests/`、`owstats/tests/`、`ai_review_backend/tests/`、`bnet_switcher_backend/tests/`
- `tools/` 探针与预览工具
- `scripts/` 素材下载/同步脚本
- `screenshoot/` GitHub UI 说明截图
- `ui_preview.py` 与各类 smoke test
- Markdown 开发文档
- `__pycache__`、`.pyc`、pytest 缓存
- 本机 Token/API Key/Battle.net 运行数据

PyInstaller 只从 `main.py` 的实际运行依赖开始分析，并额外加入真正运行所需的 `assets/`、`config.json`、动态导入的 `ai_review` / `bnet_backend` 和 mini-racer 二进制运行时。

## EXE 下的可写数据

冻结版不会尝试向安装目录或 PyInstaller onefile 临时目录写用户数据：

- 规则配置：`%LOCALAPPDATA%\OWDriveSplit\config.json`
- 大神授权：`%LOCALAPPDATA%\OWDriveSplit\dashen_credentials.json`
- DeepSeek API Key：`%LOCALAPPDATA%\OWDriveSplit\deepseek_api_key.json`
- 玩家头像缓存：`%LOCALAPPDATA%\OWDriveSplit\cache\avatars`
- 对局详情缓存：`%LOCALAPPDATA%\OWDriveSplit\cache\match_details`

卸载程序默认不会删除这些用户数据。
