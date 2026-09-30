# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from unittest.mock import patch

from owstats.game_catalog import _DASHEN_HEROES, resolve_hero
from owstats.images import _target
from owstats.parsers import normalize_match


class HeroAssetRegressionTests(unittest.TestCase):
    def test_all_current_dashen_hero_guids_resolve(self):
        self.assertEqual(len(_DASHEN_HEROES), 53)
        for guid, (hero_key, role_type) in _DASHEN_HEROES.items():
            resolved = resolve_hero(guid)
            self.assertEqual(resolved["hero_key"], hero_key)
            self.assertEqual(resolved["role_type"], role_type)
            self.assertTrue(resolved["hero_name"])

    def test_numeric_hero_guid_populates_match_name_and_role(self):
        row = normalize_match({"heroGuid": "207165582859043377"})
        self.assertEqual(row["hero_key"], "kiriko")
        self.assertEqual(row["hero_name"], "雾子")
        self.assertEqual(row["role_type"], "healer")

    def test_dashen_img_cdn_is_allowed_for_player_avatar(self):
        with patch(
            "owstats.images.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
        ):
            _pinned, host = _target("https://img.166.net/gameyw-misc/avatar.png")
        self.assertEqual(host, "img.166.net")


if __name__ == "__main__":
    unittest.main()
