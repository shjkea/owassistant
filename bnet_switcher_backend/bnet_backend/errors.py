class BattleNetBackendError(RuntimeError):
    """Base error raised by the Battle.net switcher backend."""


class UnsupportedPlatformError(BattleNetBackendError):
    pass


class ClientPathError(BattleNetBackendError):
    pass


class ClientStopTimeout(BattleNetBackendError):
    pass


class AccountDatabaseError(BattleNetBackendError):
    pass


class SnapshotError(BattleNetBackendError):
    pass


class LoginError(BattleNetBackendError):
    pass


class SwitchError(BattleNetBackendError):
    pass
