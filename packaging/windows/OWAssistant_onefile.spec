# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path.cwd().resolve()
AI_ROOT = ROOT / "ai_review_backend"
BNET_ROOT = ROOT / "bnet_switcher_backend"
for extra in (AI_ROOT, BNET_ROOT):
    value = str(extra)
    if value not in sys.path:
        sys.path.insert(0, value)


def tree_datas(source: Path, target: str):
    out = []
    for path in source.rglob("*"):
        if path.is_file():
            rel_parent = path.parent.relative_to(source)
            dest = Path(target) / rel_parent
            out.append((str(path), str(dest).replace("\\", "/")))
    return out


datas = tree_datas(ROOT / "assets", "assets")
datas.append((str(ROOT / "config.json"), "."))

# mini-racer 带本地二进制运行时；显式收集，避免只靠自动分析漏文件。
mr_datas, mr_binaries, mr_hidden = collect_all("py_mini_racer")
datas += mr_datas
binaries = mr_binaries
hiddenimports = mr_hidden

# 这两个后端由前端桥接层动态 import，PyInstaller 静态分析看不到。
hiddenimports += collect_submodules("ai_review")
hiddenimports += collect_submodules("bnet_backend")
hiddenimports = sorted(set(hiddenimports))

version_file = ROOT / "packaging" / "windows" / "_generated" / "version_info.txt"
if not version_file.is_file():
    raise SystemExit("缺少 version_info.txt；请先运行 packaging/windows/generate_version_info.py")

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT), str(AI_ROOT), str(BNET_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="OWAssistant",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "app" / "ow_assistant_icon.ico"),
    version=str(version_file),
)
