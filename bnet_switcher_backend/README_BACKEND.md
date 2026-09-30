# Battle.net 切号后端（Python / Windows）

这是一个**前端无关**的 Battle.net 账号切换后端，适合直接放进现有 PySide6 项目，再由独立前端页面调用。

核心思路参考了 MIT 许可的 `kabxx/BnetSwitchLite`，但这里重新实现为纯 Python 后端，只保留 Windows 端需要的行为：

- 从 `%LOCALAPPDATA%\Battle.net\CachedData.db` 读取账号、BattleTag、environment、account_id 和当前账号。
- 首次保存账号时，正常走 Battle.net / 网易官方网页登录与手机验证码。
- `UnifiedAuth` **只读**并做哈希，用于确认本次登录产生了新的认证状态；不会导出、写入或打印 token 内容。
- 每个账号只保存 `%APPDATA%\Battle.net` **根目录普通文件**，不递归复制 BrowserCaches 等目录。
- 快照包含 SHA-256 校验。
- 切换时先保存当前配置为事务恢复点，恢复目标快照，启动 Battle.net，再通过 `CachedData.db` 二次确认 account_id；失败自动回滚。
- 检测到 `Overwatch.exe` 正在运行时拒绝切号。
- 不读取、不保存 Battle.net/网易明文密码。

## 文件

```text
bnet_backend/
  __init__.py
  core.py
  errors.py
  models.py

tests/
  test_backend.py
```

仅使用 Python 标准库，无额外 pip 依赖。

## 给 PySide6 前端的最小 API

```python
from bnet_backend import BattleNetBackend, AccountKey

backend = BattleNetBackend(
    data_dir=r"C:\Users\xxx\AppData\Local\OWStats\bnet_switcher"
)

# 账号列表
accounts = backend.list_accounts()
for a in accounts:
    print(a.battle_tag, a.region, a.is_current, a.is_saved)

# 若自动找不到 Battle.net.exe，只需要用户选择一次
backend.set_client_path(r"C:\Program Files (x86)\Battle.net\Battle.net.exe")
```

### 首次保存 / 重新登录

用户点击“登录并保存”：

```python
key = AccountKey(
    environment="cn.actual.battlenet.com.cn",
    account_id="7:42",
)

session = backend.begin_login(key, progress=on_progress)
```

此时官方 Battle.net 会启动。用户按官方流程完成网易网页登录/手机验证码。

前端可以每 500~1000 ms 调一次：

```python
probe = backend.probe_login(session.id)

if probe.status == "ready":
    account = backend.complete_login_and_save(session.id, progress=on_progress)
elif probe.status == "wrong_account":
    # 提示用户登录错号
    ...
```

也可在工作线程里直接使用阻塞版：

```python
account = backend.wait_and_complete_login(
    session.id,
    progress=on_progress,
    timeout=300,
)
```

取消：

```python
backend.cancel_login(session.id, progress=on_progress)
```

### 已保存账号切换

```python
account = backend.switch_account(
    key,
    progress=on_progress,
)
```

`switch_account()` 是阻塞调用，**必须放到 QThread / worker 线程**，不要直接在 Qt GUI 主线程运行。

### 删除本工具保存的账号快照

```python
backend.remove_saved_account(key)
```

不会删除 Battle.net 账号，也不会删除 Battle.net 自己的账号数据库。

## progress 回调

```python
def on_progress(event):
    print(event.kind, event.phase, event.detail, event.progress)
```

前端可以直接把它转成 Qt Signal：

```python
class Worker(QObject):
    progress = Signal(dict)

    def _report(self, event):
        self.progress.emit(event.to_dict())
```

常见 phase 包括：

- `validating`
- `preparing`
- `stoppingClient`
- `clearingPointer`
- `awaitingUser`
- `capturing`
- `restoring`
- `launchingClient`
- `verifying`
- `rollingBack`
- `completed`

前端不应依赖 detail 文案判断状态，只依赖 `phase`。

## UI 建议的数据字段

`AccountInfo`：

```python
account.key.environment
account.key.account_id
account.battle_tag
account.internal_name
account.region       # CN / KR / EU / US / None
account.is_current
account.is_saved
account.last_saved_at
```

建议 UI 主标题优先显示 `battle_tag`，不要显示 UnifiedAuth 或其他本地认证数据。

## 重要边界

1. `data_dir/accounts/` 中的账号快照具有登录状态复用能力，**不要上传、同步到网盘或提交 Git**。
2. 不要让前端展示/导出 UnifiedAuth 内容；本后端本身只保存哈希基线。
3. Battle.net 正在更新/安装时不建议切换。
4. 当前实现不会强制 `taskkill /F`。若 Battle.net 无法安全退出，会停止操作而不是强杀。
5. `Overwatch.exe` 正在运行时会拒绝登录保存、回滚和账号切换。
6. Battle.net 大版本更新后，旧快照失效时重新执行“登录并保存”即可。

## 测试

项目根目录执行：

```powershell
python -m unittest discover -s tests -v
```

Linux/macOS 上的单元测试只覆盖 SQLite / 配置 / 快照纯逻辑；真正 Battle.net 进程、Windows 注册表和客户端切换必须在 Windows 实机验证。
