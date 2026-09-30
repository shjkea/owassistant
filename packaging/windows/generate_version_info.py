from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "ui" / "main_window.py"
OUT_DIR = Path(__file__).resolve().parent / "_generated"
OUT = OUT_DIR / "version_info.txt"

match = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', SOURCE.read_text(encoding="utf-8"), re.M)
if not match:
    raise SystemExit("无法从 ui/main_window.py 读取 APP_VERSION")
version = match.group(1).strip()
parts = version.split(".")
if not 1 <= len(parts) <= 4 or any(not p.isdigit() for p in parts):
    raise SystemExit(f"APP_VERSION 必须是 1~4 段纯数字版本号，当前为 {version!r}")
nums = [int(p) for p in parts]
nums += [0] * (4 - len(nums))
ver_tuple = tuple(nums[:4])

OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT.write_text(
f'''# UTF-8\nVSVersionInfo(\n  ffi=FixedFileInfo(\n    filevers={ver_tuple},\n    prodvers={ver_tuple},\n    mask=0x3f,\n    flags=0x0,\n    OS=0x40004,\n    fileType=0x1,\n    subtype=0x0,\n    date=(0, 0)\n  ),\n  kids=[\n    StringFileInfo([\n      StringTable(\n        '080404B0',\n        [\n          StringStruct('FileDescription', '守望先锋 国服助手'),\n          StringStruct('FileVersion', '{version}'),\n          StringStruct('InternalName', 'OWAssistant'),\n          StringStruct('OriginalFilename', 'OWAssistant.exe'),\n          StringStruct('ProductName', '守望先锋 国服助手'),\n          StringStruct('ProductVersion', '{version}')\n        ]\n      )\n    ]),\n    VarFileInfo([VarStruct('Translation', [2052, 1200])])\n  ]\n)\n''',
encoding="utf-8",
newline="\n",
)
print(version)
