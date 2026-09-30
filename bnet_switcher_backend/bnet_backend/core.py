from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Iterable

from .errors import (
    AccountDatabaseError,
    BattleNetBackendError,
    ClientPathError,
    ClientStopTimeout,
    LoginError,
    SnapshotError,
    SwitchError,
    UnsupportedPlatformError,
)
from .models import AccountInfo, AccountKey, LoginProbe, LoginSession, ProgressEvent

ProgressCallback = Callable[[ProgressEvent], None]

CLIENT_PROCESS_NAMES = {
    "battle.net.exe",
    "battle.net launcher.exe",
    "battle.net helper.exe",
}
MAIN_PROCESS_NAMES = {"battle.net.exe", "battle.net launcher.exe"}
OVERWATCH_PROCESS_NAMES = {"overwatch.exe"}
UNIFIED_AUTH_PATH = r"Software\Blizzard Entertainment\Battle.net\UnifiedAuth"
REPARSE_POINT_ATTRIBUTE = 0x400

REGION_MAP = {
    "cn": "CN",
    "cn.actual.battlenet.com.cn": "CN",
    "kr": "KR",
    "kr.actual.battle.net": "KR",
    "eu": "EU",
    "eu.actual.battle.net": "EU",
    "us": "US",
    "us.actual.battle.net": "US",
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _emit(cb: ProgressCallback | None, kind: str, phase: str, detail: str, progress: int) -> None:
    if cb:
        cb(ProgressEvent(kind=kind, phase=phase, detail=detail, progress=progress))


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with open(temp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(temp, path)


def _atomic_write_json(path: Path, value: object) -> None:
    data = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    _atomic_write_bytes(path, data)


def _sha256(path: Path) -> tuple[int, str]:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        while True:
            block = fh.read(1024 * 1024)
            if not block:
                break
            size += len(block)
            h.update(block)
    return size, h.hexdigest()


def _is_reparse(path: Path) -> bool:
    st = os.lstat(path)
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & REPARSE_POINT_ATTRIBUTE) or path.is_symlink()


def _validate_regular_file(path: Path) -> None:
    if _is_reparse(path):
        raise SnapshotError(f"拒绝处理链接或重解析点：{path}")
    if not path.is_file():
        raise SnapshotError(f"不是普通文件：{path}")


def _regular_root_files(directory: Path) -> dict[str, Path]:
    if not directory.is_dir():
        raise SnapshotError(f"目录不存在：{directory}")
    if _is_reparse(directory):
        raise SnapshotError(f"拒绝处理链接或重解析目录：{directory}")
    files: dict[str, Path] = {}
    for entry in directory.iterdir():
        if _is_reparse(entry):
            raise SnapshotError(f"目录包含链接或重解析点：{entry}")
        if not entry.is_file():
            continue
        name = entry.name
        if name in {".", ".."} or "/" in name or "\\" in name:
            raise SnapshotError(f"非法文件名：{name}")
        files[name] = entry
    return files


def _capture_root_files(source: Path, output: Path) -> list[dict]:
    output.mkdir(parents=True, exist_ok=False)
    manifest: list[dict] = []
    for name, src in sorted(_regular_root_files(source).items()):
        dst = output / name
        shutil.copyfile(src, dst)
        size, digest = _sha256(dst)
        manifest.append({"name": name, "size": size, "sha256": digest})
    return manifest


def _validate_file_set(files_dir: Path, manifest: list[dict], require_config: bool = True) -> None:
    expected = {item["name"]: item for item in manifest}
    if len(expected) != len(manifest):
        raise SnapshotError("快照清单包含重复文件名")
    if require_config and "Battle.net.config" not in expected:
        raise SnapshotError("快照缺少 Battle.net.config")
    actual = _regular_root_files(files_dir)
    if set(actual) != set(expected):
        raise SnapshotError("快照文件集合与清单不一致")
    for name, path in actual.items():
        size, digest = _sha256(path)
        item = expected[name]
        if size != item["size"] or digest != item["sha256"]:
            raise SnapshotError(f"快照文件校验失败：{name}")


def _replace_file_atomic(source: Path, destination: Path) -> None:
    _validate_regular_file(source)
    temp = destination.with_name(f".bnet-switch-{uuid.uuid4().hex}.tmp")
    shutil.copyfile(source, temp)
    with open(temp, "r+b") as fh:
        os.fsync(fh.fileno())
    os.replace(temp, destination)


def _apply_file_set(files_dir: Path, manifest: list[dict], active_dir: Path) -> None:
    _validate_file_set(files_dir, manifest, require_config=True)
    active_dir.mkdir(parents=True, exist_ok=True)
    if _is_reparse(active_dir):
        raise SnapshotError(f"拒绝写入重解析目录：{active_dir}")
    expected_names = {item["name"] for item in manifest}
    for name, current in _regular_root_files(active_dir).items():
        if name not in expected_names:
            current.unlink()
    for item in manifest:
        _replace_file_atomic(files_dir / item["name"], active_dir / item["name"])
    current = _regular_root_files(active_dir)
    if set(current) != expected_names:
        raise SnapshotError("恢复后的文件集合与目标快照不一致")
    for item in manifest:
        size, digest = _sha256(current[item["name"]])
        if size != item["size"] or digest != item["sha256"]:
            raise SnapshotError(f"恢复后校验失败：{item['name']}")


def _coerce_id(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (bytes, bytearray)):
        try:
            return bytes(value).decode("utf-8")
        except UnicodeDecodeError:
            return None
    if isinstance(value, str):
        return value
    return None


def _account_low(account_id: str) -> str | None:
    pieces = account_id.split(":")
    return pieces[1] if len(pieces) == 2 and all(pieces) else None


def _copy_sqlite_to_memory(database_path: Path) -> sqlite3.Connection:
    if not database_path.is_file():
        raise AccountDatabaseError(f"未找到 Battle.net 账号数据库：{database_path}")
    try:
        src = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True, timeout=0.2)
        src.execute("PRAGMA busy_timeout=100")
        dst = sqlite3.connect(":memory:")
        src.backup(dst, pages=64, sleep=0.02)
        src.close()
        return dst
    except sqlite3.Error as exc:
        raise AccountDatabaseError(f"无法读取 Battle.net 账号数据库：{exc}") from exc


def _read_account_catalog(database_path: Path) -> tuple[list[dict], AccountKey | None]:
    con = _copy_sqlite_to_memory(database_path)
    try:
        required = {
            "login_cache": {"name", "environment", "battle_tag", "account_id_hi", "account_id_lo"},
            "key_value_store": {"key", "value"},
        }
        for table, columns in required.items():
            rows = con.execute(f"PRAGMA table_info({table})").fetchall()
            actual = {str(row[1]) for row in rows}
            if not columns.issubset(actual):
                raise AccountDatabaseError(f"Battle.net 数据库表 {table} 缺少必要字段")

        accounts: list[dict] = []
        for name, env, tag, hi, lo in con.execute(
            "SELECT name, environment, battle_tag, account_id_hi, account_id_lo FROM login_cache"
        ):
            hi_s, lo_s = _coerce_id(hi), _coerce_id(lo)
            environment = (env or "").strip().lower() if isinstance(env, str) else ""
            if not hi_s or not lo_s or lo_s == "0" or not environment:
                continue
            accounts.append(
                {
                    "key": AccountKey(environment=environment, account_id=f"{hi_s}:{lo_s}"),
                    "internal_name": (name or "").strip() if isinstance(name, str) else "",
                    "battle_tag": (tag or "").strip() if isinstance(tag, str) else "",
                }
            )

        active_ids: set[str] = set()
        saw_without_id = False
        for (raw,) in con.execute(
            "SELECT value FROM key_value_store WHERE key = 'features_cached_data_points'"
        ):
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="strict")
            try:
                doc = json.loads(raw)
            except Exception as exc:
                raise AccountDatabaseError(f"当前账号字段不是有效 JSON：{exc}") from exc
            ident = _coerce_id(doc.get("account_id")) if isinstance(doc, dict) else None
            if ident is None:
                saw_without_id = True
            else:
                active_ids.add(ident)
        if len(active_ids) > 1 or (saw_without_id and active_ids):
            raise AccountDatabaseError("当前账号记录包含互相矛盾的值")
        current: AccountKey | None = None
        if active_ids:
            active = next(iter(active_ids))
            matches = [item["key"] for item in accounts if _account_low(item["key"].account_id) == active]
            if len(matches) == 1:
                current = matches[0]
            elif len(matches) > 1:
                raise AccountDatabaseError("当前账号 ID 同时属于多个 Battle.net 环境")
        return accounts, current
    finally:
        con.close()


def _auth_fingerprint() -> dict[str, str]:
    if os.name != "nt":
        raise UnsupportedPlatformError("UnifiedAuth 读取仅支持 Windows")
    import winreg

    values: dict[str, str] = {}
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNIFIED_AUTH_PATH, 0, winreg.KEY_READ)
    except FileNotFoundError:
        return values
    try:
        index = 0
        while True:
            try:
                name, value, value_type = winreg.EnumValue(key, index)
            except OSError:
                break
            index += 1
            if isinstance(value, bytes):
                payload = value
            elif isinstance(value, str):
                payload = value.encode("utf-16le")
            elif isinstance(value, int):
                payload = value.to_bytes(8, "little", signed=False)
            elif isinstance(value, list):
                payload = "\0".join(str(x) for x in value).encode("utf-16le")
            else:
                payload = repr(value).encode("utf-8")
            h = hashlib.sha256()
            h.update(int(value_type).to_bytes(4, "little", signed=False))
            h.update(payload)
            values[name.upper()] = h.hexdigest()
    finally:
        winreg.CloseKey(key)
    return values


def _has_fresh_auth(baseline: dict[str, str], current: dict[str, str]) -> bool:
    return any(baseline.get(name) != digest for name, digest in current.items())


def _find_saved_account_nodes(value: object, nodes: list[object]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "SavedAccountNames":
                nodes.append(child)
            else:
                _find_saved_account_nodes(child, nodes)
    elif isinstance(value, list):
        for child in value:
            _find_saved_account_nodes(child, nodes)


def _saved_account_names_present(config_path: Path) -> bool:
    try:
        value = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise LoginError(f"无法读取 Battle.net.config：{exc}") from exc
    nodes: list[object] = []
    _find_saved_account_nodes(value, nodes)
    if len(nodes) != 1:
        raise LoginError(f"Battle.net.config 应恰好包含一个 SavedAccountNames，实际为 {len(nodes)}")
    node = nodes[0]
    if isinstance(node, str):
        return bool(node.strip())
    if isinstance(node, list):
        if not all(isinstance(v, str) for v in node):
            raise LoginError("SavedAccountNames 数组包含非字符串值")
        return any(v.strip() for v in node)
    raise LoginError("SavedAccountNames 字段类型未知")


def _clear_saved_account_names(config_path: Path) -> None:
    try:
        value = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise LoginError(f"无法读取 Battle.net.config：{exc}") from exc
    count = 0

    def walk(node: object) -> None:
        nonlocal count
        if isinstance(node, dict):
            for key, child in node.items():
                if key == "SavedAccountNames":
                    count += 1
                    if isinstance(child, str):
                        node[key] = ""
                    elif isinstance(child, list):
                        node[key] = []
                    else:
                        raise LoginError("SavedAccountNames 字段类型未知")
                else:
                    walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    if count != 1:
        raise LoginError(f"Battle.net.config 应恰好包含一个 SavedAccountNames，实际为 {count}")
    _atomic_write_json(config_path, value)


def _directory_fingerprint(directory: Path) -> list[tuple[str, int, int]]:
    result: list[tuple[str, int, int]] = []
    for name, path in sorted(_regular_root_files(directory).items()):
        st = path.stat()
        result.append((name, st.st_size, st.st_mtime_ns))
    return result


def _wait_directory_stable(directory: Path, timeout: float = 6.0, interval: float = 0.25) -> None:
    deadline = time.monotonic() + timeout
    previous = _directory_fingerprint(directory)
    stable_samples = 0
    while time.monotonic() < deadline:
        time.sleep(interval)
        current = _directory_fingerprint(directory)
        if current == previous:
            stable_samples += 1
            if stable_samples >= 2:
                return
        else:
            stable_samples = 0
            previous = current
    raise SnapshotError("Battle.net 配置仍在变化，请稍后重试")


def _tasklist() -> list[tuple[str, int]]:
    if os.name != "nt":
        return []
    proc = subprocess.run(
        ["tasklist", "/FO", "CSV", "/NH"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        raise BattleNetBackendError(f"无法读取 Windows 进程列表：{proc.stderr.strip()}")
    rows: list[tuple[str, int]] = []
    for row in csv.reader(proc.stdout.splitlines()):
        if len(row) < 2:
            continue
        try:
            rows.append((row[0].strip(), int(row[1].replace(",", ""))))
        except ValueError:
            continue
    return rows


def _named_processes(names: set[str]) -> list[tuple[str, int]]:
    lower = {n.lower() for n in names}
    return [(name, pid) for name, pid in _tasklist() if name.lower() in lower]


def _is_overwatch_running() -> bool:
    return bool(_named_processes(OVERWATCH_PROCESS_NAMES))


def _client_processes() -> list[tuple[str, int]]:
    return _named_processes(CLIENT_PROCESS_NAMES)


def _is_main_client_running() -> bool:
    return any(name.lower() in MAIN_PROCESS_NAMES for name, _ in _client_processes())


def _send_shutdown_messages(pids: set[int]) -> None:
    if os.name != "nt" or not pids:
        return
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    EnumWindows = user32.EnumWindows
    GetWindowThreadProcessId = user32.GetWindowThreadProcessId
    SendMessageTimeoutW = user32.SendMessageTimeoutW

    WM_QUERYENDSESSION = 0x0011
    WM_ENDSESSION = 0x0016
    SMTO_ABORTIFHUNG = 0x0002
    CALLBACK = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    windows: list[int] = []

    @CALLBACK
    def callback(hwnd, _lparam):
        pid = wintypes.DWORD()
        GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            windows.append(hwnd)
        return True

    EnumWindows(callback, 0)
    for hwnd in windows:
        ignored = wintypes.DWORD_PTR() if hasattr(wintypes, "DWORD_PTR") else ctypes.c_size_t()
        SendMessageTimeoutW(hwnd, WM_QUERYENDSESSION, 0, 0, SMTO_ABORTIFHUNG, 3000, ctypes.byref(ignored))
        SendMessageTimeoutW(hwnd, WM_ENDSESSION, 1, 0, SMTO_ABORTIFHUNG, 3000, ctypes.byref(ignored))


def _graceful_stop(timeout: float = 20.0) -> None:
    processes = _client_processes()
    if not processes:
        return
    pids = {pid for name, pid in processes if name.lower() in MAIN_PROCESS_NAMES}
    _send_shutdown_messages(pids)
    deadline = time.monotonic() + timeout
    empty_samples = 0
    while time.monotonic() < deadline:
        if not _client_processes():
            empty_samples += 1
            if empty_samples >= 2:
                return
        else:
            empty_samples = 0
        time.sleep(0.25)
    raise ClientStopTimeout("Battle.net 未能在限定时间内完全退出；未强制结束进程")


def _ensure_client_stopped() -> None:
    if _client_processes():
        raise ClientStopTimeout("Battle.net 仍有进程运行，已取消配置写入")
    time.sleep(0.2)
    if _client_processes():
        raise ClientStopTimeout("Battle.net 已自动重新启动，已取消配置写入")


def _region_for_environment(environment: str) -> str | None:
    return REGION_MAP.get(environment.strip().lower())


def _sanitize_client_path(path: Path) -> Path:
    try:
        path = path.expanduser().resolve(strict=True)
    except OSError as exc:
        raise ClientPathError(f"Battle.net.exe 路径无效：{exc}") from exc
    if not path.is_file() or path.name.lower() != "battle.net.exe":
        raise ClientPathError("请选择 Battle.net.exe 主程序")
    if _is_reparse(path):
        raise ClientPathError("拒绝使用链接或重解析点形式的 Battle.net.exe")
    return path


def _extract_config_paths(value: object) -> Iterable[Path]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "Path" and isinstance(child, str) and child.strip():
                yield Path(child)
            yield from _extract_config_paths(child)
    elif isinstance(value, list):
        for child in value:
            yield from _extract_config_paths(child)


class BattleNetBackend:
    """Windows-only Battle.net account switcher backend.

    The class is UI-agnostic. Blocking operations should be run in a QThread/
    worker thread by a PySide6 frontend.
    """

    def __init__(self, data_dir: str | os.PathLike | None = None, client_path: str | os.PathLike | None = None):
        if os.name != "nt" and not os.environ.get("BNET_BACKEND_ALLOW_NONWINDOWS_TESTS"):
            raise UnsupportedPlatformError("该后端只支持 Windows 10/11")

        local = Path(os.environ.get("LOCALAPPDATA", Path.home()))
        roaming = Path(os.environ.get("APPDATA", Path.home()))
        self.local_bnet = local / "Battle.net"
        self.cached_data_db = self.local_bnet / "CachedData.db"
        self.roaming_dir = roaming / "Battle.net"
        self.roaming_config = self.roaming_dir / "Battle.net.config"
        self.data_dir = Path(data_dir) if data_dir else local / "OWStats" / "bnet_switcher"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.accounts_dir = self.data_dir / "accounts"
        self.sessions_dir = self.data_dir / "sessions"
        self.transaction_dir = self.data_dir / "transaction"
        self.accounts_dir.mkdir(exist_ok=True)
        self.sessions_dir.mkdir(exist_ok=True)
        self.settings_path = self.data_dir / "settings.json"

        self._client_path: Path | None = None
        if client_path is not None:
            self.set_client_path(client_path)
        else:
            settings = self._load_json(self.settings_path, {})
            stored = settings.get("client_path") if isinstance(settings, dict) else None
            if isinstance(stored, str):
                try:
                    self._client_path = _sanitize_client_path(Path(stored))
                except ClientPathError:
                    self._client_path = None
            if self._client_path is None:
                self._client_path = self.detect_client_path()

    # ---------- public state ----------

    @property
    def client_path(self) -> Path | None:
        return self._client_path

    def set_client_path(self, path: str | os.PathLike) -> Path:
        validated = _sanitize_client_path(Path(path))
        self._client_path = validated
        _atomic_write_json(self.settings_path, {"client_path": str(validated)})
        return validated

    def detect_client_path(self) -> Path | None:
        candidates: list[Path] = []
        if self.roaming_config.is_file():
            try:
                config = json.loads(self.roaming_config.read_text(encoding="utf-8"))
                for candidate in _extract_config_paths(config):
                    candidates.append(candidate)
            except Exception:
                pass
        for env_name in ("ProgramFiles(x86)", "ProgramFiles"):
            root = os.environ.get(env_name)
            if root:
                candidates.append(Path(root) / "Battle.net")
        candidates.extend([Path(r"C:\Battle.net"), Path(r"D:\Battle.net")])
        for candidate in candidates:
            exe = candidate if candidate.name.lower() == "battle.net.exe" else candidate / "Battle.net.exe"
            try:
                return _sanitize_client_path(exe)
            except ClientPathError:
                continue
        return None

    def list_accounts(self) -> list[AccountInfo]:
        discovered, current = _read_account_catalog(self.cached_data_db)
        found: dict[str, AccountInfo] = {}
        for item in discovered:
            key: AccountKey = item["key"]
            profile = self._load_profile(key)
            found[key.stable_id] = AccountInfo(
                key=key,
                battle_tag=item["battle_tag"],
                internal_name=item["internal_name"],
                region=_region_for_environment(key.environment),
                is_current=key == current,
                is_saved=profile is not None,
                last_saved_at=profile.get("last_saved_at") if profile else None,
            )
        for profile in self._all_profiles():
            key = AccountKey(**profile["key"])
            if key.stable_id in found:
                continue
            found[key.stable_id] = AccountInfo(
                key=key,
                battle_tag=profile.get("battle_tag", ""),
                internal_name=profile.get("internal_name", ""),
                region=_region_for_environment(key.environment),
                is_current=key == current,
                is_saved=True,
                last_saved_at=profile.get("last_saved_at"),
            )
        return sorted(found.values(), key=lambda a: (not a.is_current, a.region or "ZZ", a.battle_tag, a.key.stable_id))

    def get_current_account(self) -> AccountKey | None:
        return _read_account_catalog(self.cached_data_db)[1]

    def open_client(self, region: str | None = None) -> None:
        if _is_main_client_running():
            return
        self._launch(region)

    def remove_saved_account(self, key: AccountKey) -> None:
        directory = self._account_dir(key)
        if directory.exists():
            if _is_reparse(directory):
                raise SnapshotError("拒绝删除链接或重解析账号目录")
            shutil.rmtree(directory)

    # ---------- login/save flow ----------

    def begin_login(self, key: AccountKey, progress: ProgressCallback | None = None) -> LoginSession:
        self._guard_windows()
        self._ensure_no_overwatch()
        self.recover_pending(progress)
        if any(self.sessions_dir.iterdir()):
            raise LoginError("已有未完成的登录流程，请先完成或取消")
        client = self._require_client()
        _emit(progress, "login", "preparing", "正在保存当前 Battle.net 配置", 8)
        was_running = _is_main_client_running()
        if was_running:
            _emit(progress, "login", "stoppingClient", "正在安全关闭 Battle.net", 16)
            _graceful_stop()
        if not self.roaming_config.is_file():
            if was_running:
                self._launch(None)
            raise LoginError("未找到 Battle.net.config，请先正常登录一次 Battle.net")
        _wait_directory_stable(self.roaming_dir)
        _ensure_client_stopped()

        baseline = _auth_fingerprint()
        previous = None
        try:
            previous = self.get_current_account()
        except AccountDatabaseError:
            previous = None

        session_id = uuid.uuid4().hex
        session_dir = self.sessions_dir / session_id
        session_dir.mkdir()
        try:
            before_dir = session_dir / "before"
            manifest = _capture_root_files(self.roaming_dir, before_dir)
            _atomic_write_json(session_dir / "before_manifest.json", manifest)
            session_data = {
                "id": session_id,
                "target": asdict(key),
                "created_at": _now_ms(),
                "previous": asdict(previous) if previous else None,
                "previous_running": was_running,
                "auth_baseline": baseline,
                "client_path": str(client),
            }
            _atomic_write_json(session_dir / "session.json", session_data)
            _emit(progress, "login", "clearingPointer", "正在准备官方 Battle.net 登录页", 24)
            _clear_saved_account_names(self.roaming_config)
            self._launch(_region_for_environment(key.environment))
        except Exception:
            self._restore_session_before(session_dir)
            shutil.rmtree(session_dir, ignore_errors=True)
            if was_running:
                self._launch(None)
            raise
        _emit(progress, "login", "awaitingUser", "请在官方 Battle.net/网易页面完成登录", 32)
        return LoginSession(id=session_id, target=key, created_at=session_data["created_at"])

    def probe_login(self, session_id: str) -> LoginProbe:
        session = self._load_session(session_id)
        target = AccountKey(**session["target"])
        if not self.roaming_config.is_file() or not _saved_account_names_present(self.roaming_config):
            return LoginProbe("pending", None, "等待 Battle.net 写入登录完成标记")
        current_auth = _auth_fingerprint()
        if not _has_fresh_auth(session.get("auth_baseline", {}), current_auth):
            return LoginProbe("pending", None, "等待本次登录产生新的 UnifiedAuth 状态")
        try:
            current = self.get_current_account()
        except AccountDatabaseError:
            return LoginProbe("pending", None, "等待 Battle.net 更新当前账号数据库")
        if current is None:
            return LoginProbe("pending", None, "等待 Battle.net 确认当前账号")
        if current != target:
            return LoginProbe("wrong_account", current, "当前登录账号与所选账号不一致")
        return LoginProbe("ready", current, "登录已确认，可以保存")

    def complete_login_and_save(self, session_id: str, progress: ProgressCallback | None = None) -> AccountInfo:
        self._guard_windows()
        self._ensure_no_overwatch()
        session = self._load_session(session_id)
        target = AccountKey(**session["target"])
        probe = self.probe_login(session_id)
        if probe.status == "pending":
            raise LoginError(probe.message)
        if probe.status == "wrong_account":
            raise LoginError(f"登录结果与目标账号不一致：{probe.current.stable_id if probe.current else 'unknown'}")

        _emit(progress, "login", "stoppingClient", "正在关闭 Battle.net 并固化登录状态", 48)
        if _client_processes():
            _graceful_stop()
        _ensure_client_stopped()
        _wait_directory_stable(self.roaming_dir)

        # Re-validate after the client is fully stopped.
        if not _saved_account_names_present(self.roaming_config):
            raise LoginError("Battle.net.config 未保留登录完成标记")
        if not _has_fresh_auth(session.get("auth_baseline", {}), _auth_fingerprint()):
            raise LoginError("未检测到本次登录产生的新认证状态")
        discovered, current = _read_account_catalog(self.cached_data_db)
        if current != target:
            raise LoginError("Battle.net 最终确认的账号与目标账号不一致")
        account = next((x for x in discovered if x["key"] == target), None)
        if account is None:
            raise LoginError("账号数据库中未找到目标账号")

        _emit(progress, "login", "capturing", "正在保存账号快照", 68)
        self._save_account_snapshot(target, account["battle_tag"], account["internal_name"])
        session_dir = self.sessions_dir / session_id
        shutil.rmtree(session_dir, ignore_errors=True)
        _emit(progress, "login", "launchingClient", "账号已保存，正在重新启动 Battle.net", 88)
        self._launch(None)
        _emit(progress, "login", "completed", "登录状态已保存", 100)
        return next(a for a in self.list_accounts() if a.key == target)

    def wait_and_complete_login(
        self,
        session_id: str,
        progress: ProgressCallback | None = None,
        timeout: float = 300.0,
        poll_interval: float = 0.5,
    ) -> AccountInfo:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            probe = self.probe_login(session_id)
            if probe.status == "ready":
                return self.complete_login_and_save(session_id, progress)
            if probe.status == "wrong_account":
                raise LoginError(probe.message)
            if not _is_main_client_running():
                raise LoginError("Battle.net 已退出，登录未完成")
            time.sleep(poll_interval)
        raise LoginError("等待登录完成超时")

    def cancel_login(self, session_id: str, progress: ProgressCallback | None = None) -> None:
        self._guard_windows()
        session = self._load_session(session_id)
        self._ensure_no_overwatch()
        _emit(progress, "login", "rollingBack", "正在恢复登录前配置", 60)
        if _client_processes():
            _graceful_stop()
        _ensure_client_stopped()
        self._restore_session_before(self.sessions_dir / session_id)
        shutil.rmtree(self.sessions_dir / session_id, ignore_errors=True)
        if session.get("previous_running"):
            self._launch(None)
        _emit(progress, "login", "cancelled", "已恢复登录前配置", 100)

    # ---------- switch flow ----------

    def switch_account(
        self,
        key: AccountKey,
        progress: ProgressCallback | None = None,
        verify_timeout: float = 45.0,
    ) -> AccountInfo:
        self._guard_windows()
        self._ensure_no_overwatch()
        self.recover_pending(progress)
        if any(self.sessions_dir.iterdir()):
            raise SwitchError("存在未完成登录流程，不能切换账号")
        snapshot = self._validated_snapshot(key)
        self._require_client()
        _emit(progress, "switch", "validating", "正在校验目标账号快照", 5)
        was_running = _is_main_client_running()
        if was_running:
            _emit(progress, "switch", "stoppingClient", "正在安全关闭 Battle.net", 18)
            _graceful_stop()
        _wait_directory_stable(self.roaming_dir)
        _ensure_client_stopped()

        self.transaction_dir.mkdir(exist_ok=False)
        try:
            before_dir = self.transaction_dir / "before"
            before_manifest = _capture_root_files(self.roaming_dir, before_dir)
            _atomic_write_json(self.transaction_dir / "before_manifest.json", before_manifest)
            current = None
            try:
                current = self.get_current_account()
            except AccountDatabaseError:
                pass
            _atomic_write_json(
                self.transaction_dir / "transaction.json",
                {
                    "target": asdict(key),
                    "previous": asdict(current) if current else None,
                    "previous_running": was_running,
                    "created_at": _now_ms(),
                },
            )
            _emit(progress, "switch", "restoring", "正在事务化恢复目标账号配置", 58)
            _apply_file_set(snapshot["files_dir"], snapshot["manifest"], self.roaming_dir)
            _emit(progress, "switch", "launchingClient", "正在启动 Battle.net", 76)
            self._launch(None)
            _emit(progress, "switch", "verifying", "正在等待 Battle.net 确认目标账号", 88)
            self._wait_for_account(key, verify_timeout)
        except Exception as exc:
            rollback_error = None
            try:
                if _client_processes():
                    _graceful_stop()
                _ensure_client_stopped()
                self._restore_transaction_before()
                if was_running:
                    self._launch(None)
            except Exception as rb:
                rollback_error = rb
            if rollback_error:
                raise SwitchError(f"切换失败：{exc}；自动恢复也失败：{rollback_error}") from exc
            raise SwitchError(f"切换失败：{exc}；已恢复切换前配置") from exc
        else:
            shutil.rmtree(self.transaction_dir, ignore_errors=True)
            _emit(progress, "switch", "completed", "Battle.net 已确认目标账号", 100)
            return next(a for a in self.list_accounts() if a.key == key)

    def recover_pending(self, progress: ProgressCallback | None = None) -> bool:
        if not self.transaction_dir.exists():
            return False
        self._guard_windows()
        self._ensure_no_overwatch()
        _emit(progress, "recovery", "stoppingClient", "检测到未完成切换，正在恢复原配置", 20)
        if _client_processes():
            _graceful_stop()
        _ensure_client_stopped()
        tx = self._load_json(self.transaction_dir / "transaction.json", {})
        self._restore_transaction_before()
        shutil.rmtree(self.transaction_dir, ignore_errors=True)
        if tx.get("previous_running"):
            self._launch(None)
        _emit(progress, "recovery", "completed", "上次未完成切换已恢复", 100)
        return True

    # ---------- internal storage ----------

    def _account_dir(self, key: AccountKey) -> Path:
        digest = hashlib.sha256(key.stable_id.encode("utf-8")).hexdigest()[:24]
        return self.accounts_dir / digest

    def _save_account_snapshot(self, key: AccountKey, battle_tag: str, internal_name: str) -> None:
        directory = self._account_dir(key)
        staging = self.accounts_dir / f".staging-{uuid.uuid4().hex}"
        staging.mkdir()
        try:
            files_dir = staging / "files"
            manifest = _capture_root_files(self.roaming_dir, files_dir)
            if not any(item["name"] == "Battle.net.config" for item in manifest):
                raise SnapshotError("账号快照缺少 Battle.net.config")
            _validate_file_set(files_dir, manifest, require_config=True)
            profile = {
                "version": 1,
                "key": asdict(key),
                "battle_tag": battle_tag,
                "internal_name": internal_name,
                "last_saved_at": _now_ms(),
                "manifest": manifest,
            }
            _atomic_write_json(staging / "profile.json", profile)
            old = self.accounts_dir / f".old-{uuid.uuid4().hex}"
            if directory.exists():
                os.replace(directory, old)
            os.replace(staging, directory)
            if old.exists():
                shutil.rmtree(old, ignore_errors=True)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def _validated_snapshot(self, key: AccountKey) -> dict:
        directory = self._account_dir(key)
        profile = self._load_json(directory / "profile.json", None)
        if not isinstance(profile, dict) or profile.get("key") != asdict(key):
            raise SnapshotError("目标账号没有有效快照")
        manifest = profile.get("manifest")
        if not isinstance(manifest, list):
            raise SnapshotError("目标账号快照清单无效")
        files_dir = directory / "files"
        _validate_file_set(files_dir, manifest, require_config=True)
        return {"profile": profile, "manifest": manifest, "files_dir": files_dir}

    def _load_profile(self, key: AccountKey) -> dict | None:
        profile = self._load_json(self._account_dir(key) / "profile.json", None)
        return profile if isinstance(profile, dict) and profile.get("key") == asdict(key) else None

    def _all_profiles(self) -> list[dict]:
        result = []
        for directory in self.accounts_dir.iterdir():
            if not directory.is_dir() or directory.name.startswith("."):
                continue
            profile = self._load_json(directory / "profile.json", None)
            if isinstance(profile, dict) and isinstance(profile.get("key"), dict):
                result.append(profile)
        return result

    def _load_session(self, session_id: str) -> dict:
        if not session_id or any(c not in "0123456789abcdef" for c in session_id.lower()):
            raise LoginError("登录会话 ID 无效")
        session = self._load_json(self.sessions_dir / session_id / "session.json", None)
        if not isinstance(session, dict) or session.get("id") != session_id:
            raise LoginError("登录会话不存在或已结束")
        return session

    def _restore_session_before(self, session_dir: Path) -> None:
        manifest = self._load_json(session_dir / "before_manifest.json", None)
        if not isinstance(manifest, list):
            raise SnapshotError("登录恢复清单不存在")
        _apply_file_set(session_dir / "before", manifest, self.roaming_dir)

    def _restore_transaction_before(self) -> None:
        manifest = self._load_json(self.transaction_dir / "before_manifest.json", None)
        if not isinstance(manifest, list):
            raise SnapshotError("切换恢复清单不存在")
        _apply_file_set(self.transaction_dir / "before", manifest, self.roaming_dir)

    # ---------- internal process helpers ----------

    def _launch(self, region: str | None) -> None:
        client = self._require_client()
        args = [str(client)]
        if region:
            args.append(f"--setregion={region}")
        try:
            subprocess.Popen(args, cwd=str(client.parent), close_fds=True)
        except OSError as exc:
            raise ClientPathError(f"无法启动 Battle.net：{exc}") from exc

    def _wait_for_account(self, expected: AccountKey, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        matches = 0
        last_error: str | None = None
        while time.monotonic() < deadline:
            if not _is_main_client_running():
                raise SwitchError("Battle.net 在确认账号前已退出")
            try:
                current = self.get_current_account()
                if current == expected:
                    matches += 1
                    if matches >= 2:
                        return
                else:
                    matches = 0
                last_error = None
            except AccountDatabaseError as exc:
                matches = 0
                last_error = str(exc)
            time.sleep(0.5)
        detail = f"；最后读取错误：{last_error}" if last_error else ""
        raise SwitchError(f"等待 Battle.net 确认目标账号超时{detail}")

    def _require_client(self) -> Path:
        if self._client_path is None:
            detected = self.detect_client_path()
            if detected:
                self._client_path = detected
        if self._client_path is None:
            raise ClientPathError("未找到 Battle.net.exe，请由前端让用户选择一次客户端路径")
        return self._client_path

    @staticmethod
    def _load_json(path: Path, default: object) -> object:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    @staticmethod
    def _guard_windows() -> None:
        if os.name != "nt":
            raise UnsupportedPlatformError("该操作仅支持 Windows")

    @staticmethod
    def _ensure_no_overwatch() -> None:
        if _is_overwatch_running():
            raise BattleNetBackendError("检测到 Overwatch.exe 正在运行。为避免切号期间修改 Battle.net 状态，已取消操作。")
