import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

os.environ["BNET_BACKEND_ALLOW_NONWINDOWS_TESTS"] = "1"

from bnet_backend.core import (
    _apply_file_set,
    _capture_root_files,
    _clear_saved_account_names,
    _read_account_catalog,
    _saved_account_names_present,
    _validate_file_set,
)
from bnet_backend.models import AccountKey


class BackendCoreTests(unittest.TestCase):
    def test_reads_accounts_and_current_account(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "CachedData.db"
            con = sqlite3.connect(db)
            con.executescript(
                """
                CREATE TABLE login_cache(name TEXT, environment TEXT, battle_tag TEXT, account_id_hi INTEGER, account_id_lo INTEGER);
                CREATE TABLE key_value_store(key TEXT, value TEXT);
                INSERT INTO login_cache VALUES('one', 'cn', 'Test#1234', 7, 42);
                INSERT INTO key_value_store VALUES('features_cached_data_points', '{"account_id":42}');
                """
            )
            con.commit()
            con.close()
            accounts, current = _read_account_catalog(db)
            self.assertEqual(accounts[0]["battle_tag"], "Test#1234")
            self.assertEqual(current, AccountKey("cn", "7:42"))

    def test_saved_account_marker_clear_and_detect(self):
        with tempfile.TemporaryDirectory() as td:
            config = Path(td) / "Battle.net.config"
            config.write_text(json.dumps({"Client": {"SavedAccountNames": ["abc"]}}), encoding="utf-8")
            self.assertTrue(_saved_account_names_present(config))
            _clear_saved_account_names(config)
            self.assertFalse(_saved_account_names_present(config))

    def test_snapshot_roundtrip_root_files_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            active = root / "active"
            snap = root / "snap"
            active.mkdir()
            (active / "Battle.net.config").write_text("A", encoding="utf-8")
            (active / "other.config").write_text("B", encoding="utf-8")
            (active / "ignored-dir").mkdir()
            (active / "ignored-dir" / "x").write_text("ignored", encoding="utf-8")

            manifest = _capture_root_files(active, snap)
            _validate_file_set(snap, manifest)

            (active / "Battle.net.config").write_text("changed", encoding="utf-8")
            (active / "extra.config").write_text("extra", encoding="utf-8")
            _apply_file_set(snap, manifest, active)

            self.assertEqual((active / "Battle.net.config").read_text(encoding="utf-8"), "A")
            self.assertFalse((active / "extra.config").exists())
            self.assertTrue((active / "ignored-dir" / "x").exists())


if __name__ == "__main__":
    unittest.main()
