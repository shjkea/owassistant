# -*- coding: utf-8 -*-
from __future__ import annotations

from owstats import CredentialStore, OWStatsService
from owstats.drive_bridge import matches_to_drive_records


def main() -> None:
    service = OWStatsService(credential_store=CredentialStore(".owstats_test_credential.json"))
    assert service.auth_status()["configured"] is False
    records = matches_to_drive_records([
        {"matchRet": 1, "gameMode": "SportPreset", "beginTs": 2},
        {"matchRet": 2, "gameMode": "SportPreset", "beginTs": 1},
    ])
    assert [r["result"] for r in records] == ["L", "W"]
    print({"ok": True, "backend": "owstats", "localhost_service": False})


if __name__ == "__main__":
    main()
