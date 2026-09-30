@echo off
chcp 65001 >nul
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
  set "PY=py"
) else (
  set "PY=python"
)

echo ========================================
echo   OW 静态素材一次性下载器
echo ========================================
echo.
echo 将准备：英雄头像、地图背景、游戏内同款职责/地图类型/段位图标。
echo 已内置的游戏内同款图标不会被覆盖；缺失的新素材才联网补齐。
echo.

%PY% scripts\download_ow_assets.py
set "RC=%errorlevel%"

echo.
if "%RC%"=="0" (
  echo 完成。素材已保存在 assets\ 目录。
) else (
  echo 执行失败，错误码：%RC%
  echo 请查看上方缺失项；联网补齐需要可访问 OverFast / Blizzard 素材源，并已安装 requirements.txt。
)
echo.
pause
exit /b %RC%
