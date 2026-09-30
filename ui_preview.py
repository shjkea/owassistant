# -*- coding: utf-8 -*-
"""OW 国服助手：真实 UI / 真实业务逻辑 / 离线原始响应预览。

用途
----
在网易大神接口维护或不可用时，继续调当前项目 UI。

这不是第二套 UI，也不是 FakeService 直接返回前端 view model：

    MainWindow（真实）
      -> OWStatsPage（真实）
      -> OWStatsService（真实）
      -> parsers / owstats_data（真实）
      -> ReplayDashenClient（仅这里替代真实 HTTP）
      -> 固定的“网易原始 JSON”

因此战绩卡是否显示一个/多个英雄、开放职责 flex、地图 GUID、本人与组排等，
全部仍由当前项目原有逻辑决定。

运行：
    python ui_preview.py
    python ui_preview.py --detail     # 查询完成后自动打开第一场详情

可选后端自检（不需要 PySide6）：
    python ui_preview.py --backend-self-test

注意：
- 不访问网易网络；
- QSettings、大神凭证、LOCALAPPDATA 缓存均重定向到临时目录；
- 不修改 main.py / ui/*.py / owstats/*.py；
- 对局详情的原始响应中已预留双方 Ban 数据，但本脚本不会新增 Ban UI。
"""
from __future__ import annotations

import copy
import importlib
import inspect
import json
import os
import pkgutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from owstats.credential_store import CredentialStore
from owstats.models import DashenCredential
from owstats.service import OWStatsService

ROOT = Path(__file__).resolve().parent
CURRENT_SEASON = 24

# GitHub / UI 展示专用：所有账号与玩家名均为《守望先锋》主播模式匿名 ID，
# 不包含真实 BattleTag、真实 bnetId 或真实 customerToken。
SELF_TAG = "疑似开呱#51842"
SELF_TOKEN = "preview-ct-self"
SELF_BNET = "900001"
PREVIEW_AVATAR_PATH = ROOT / "preview_account_avatar.png"

# 英杰 = Champion。预览中的所有可见段位都固定为英杰，便于 GitHub 截图。
def _rank(name: str = "Champion", sub: Optional[int] = 1, score: int = 4700) -> Dict[str, Any]:
    row: Dict[str, Any] = {"rankName": "Champion", "rankScore": int(score)}
    if sub is not None:
        row["rankSubTier"] = int(sub)
    return row


# 详情页使用项目现有 game_catalog 中的真实英雄 GUID；只在预览层组织离线数据。
HERO: Dict[str, tuple[str, str, str]] = {
    "reinhardt": ("207165582859042823", "Reinhardt", "tank"),
    "winston": ("207165582859042825", "Winston", "tank"),
    "zarya": ("207165582859042920", "Zarya", "tank"),
    "dva": ("207165582859042938", "D.Va", "tank"),
    "roadhog": ("207165582859042880", "Roadhog", "tank"),
    "ramattra": ("207165582859043469", "Ramattra", "tank"),
    "mauga": ("207165582859043594", "Mauga", "tank"),
    "sigma": ("207165582859043387", "Sigma", "tank"),
    "dmon": ("207165582859044162", "D.Mon", "tank"),
    "tracer": ("207165582859042819", "Tracer", "dps"),
    "genji": ("207165582859042857", "Genji", "dps"),
    "soldier": ("207165582859042926", "Soldier: 76", "dps"),
    "sojourn": ("207165582859043308", "Sojourn", "dps"),
    "ashe": ("207165582859043328", "Ashe", "dps"),
    "echo": ("207165582859043334", "Echo", "dps"),
    "freja": ("207165582859043626", "Freja", "dps"),
    "emre": ("207165582859044056", "Emre", "dps"),
    "anran": ("207165582859044061", "Anran", "dps"),
    "mercy": ("207165582859042820", "Mercy", "healer"),
    "lucio": ("207165582859042937", "Lucio", "healer"),
    "ana": ("207165582859043131", "Ana", "healer"),
    "moira": ("207165582859043234", "Moira", "healer"),
    "baptiste": ("207165582859043361", "Baptiste", "healer"),
    "kiriko": ("207165582859043377", "Kiriko", "healer"),
    "illari": ("207165582859043612", "Illari", "healer"),
    "juno": ("207165582859043685", "Juno", "healer"),
    "wuyang": ("207165582859043779", "Wuyang", "healer"),
    "jetpack": ("207165582859044118", "Jetpack Cat", "healer"),
}
HERO_KEY_BY_GUID = {guid: key for key, (guid, _name, _role) in HERO.items()}
ROLE_GUID = {
    "tank": "972777519512029264",
    "dps": "972777519512029262",
    "healer": "972777519512029265",
}
ROLE_HERO_POOLS = {
    "tank": ["winston", "dva", "dmon", "sigma", "ramattra", "zarya", "reinhardt", "mauga", "roadhog"],
    "dps": ["sojourn", "ashe", "soldier", "freja", "echo", "tracer", "genji", "emre", "anran"],
    "healer": ["kiriko", "ana", "baptiste", "moira", "jetpack", "juno", "illari", "lucio", "mercy", "wuyang"],
}

# 这些中文昵称均来自游戏主播模式的内置匿名 ID。# 后的数字是展示用 BattleTag 后缀，
# 刻意避开用户和已知队友的真实后缀，防止 GitHub 截图泄露真实账号。
_STREAMER_TAGS = [
    "疑似开呱#51842", "求复活#51726", "蓝星猿住民#51387", "鱼塘冲浪#51624",
    "嘤嘤救护车#51453", "蒜蓉面包#51291", "推进机器人#51908", "新号直冲英杰1#51675",
    "敌魔附体#51342", "上最佳如喝水#51783", "月球猫猫#51264", "光子建筑圣体#51817",
    "燃火老枪#51406", "废土渣客#51691", "修喵米茨#51358", "羊驼丘纽#51964",
    "超力奥可洗OwO#51237", "进宫优仕#51754", "龟龟托马斯#51583", "锻灵爱好者#51893",
    "废哉害哉#51472", "微藓行动队#51608", "永远的尖峰小队#51319", "母狼万岁#51705",
    "冷硬大件垃圾回收#51861", "求你输到底#51248", "欢梦到此一游#51927", "安息吧维埃利#51438",
    "24K纯女王#51653", "细致女神#51804",
]

# 只使用已在真实国服资料卡中出现过的游戏称号，不自造称号。
_PLAYER_TITLES = [
    "宇宙毁灭者",
    "街头跑手",
    "无拘游民",
    "宗师重装",
    "宗师支援",
    "宗师输出",
    "猫神",
    "狂徒",
    "精英首脑",
    "雨中轻舞",
    "可怖巨物",
    "五百弱",
    "背叛之徒",
]

ANON_PLAYERS: List[Dict[str, Any]] = []
for idx, tag in enumerate(_STREAMER_TAGS):
    ANON_PLAYERS.append({
        "name": tag,
        "bnetId": str(900001 + idx),
        "customerToken": SELF_TOKEN if idx == 0 else f"preview-ct-{idx + 1:03d}",
        "title": _PLAYER_TITLES[idx % len(_PLAYER_TITLES)],
    })
# 本人使用固定稳定 ID，避免索引变化影响 is_self 识别。
ANON_PLAYERS[0]["bnetId"] = SELF_BNET
PLAYER_BY_TOKEN = {row["customerToken"]: row for row in ANON_PLAYERS}
PLAYER_BY_BNET = {row["bnetId"]: row for row in ANON_PLAYERS}


def _hero_meta(hero_key: str) -> tuple[str, str, str]:
    return HERO.get(hero_key, HERO["winston"])


def _hero_from_guid(guid: Any, fallback: str = "winston") -> str:
    return HERO_KEY_BY_GUID.get(str(guid or ""), fallback)


def _detail_seed(source: Dict[str, Any]) -> int:
    text = str(source.get("matchId") or "") + str(source.get("mapGuid") or "")
    return sum((idx + 1) * ord(ch) for idx, ch in enumerate(text)) % 1000003


def _lcg(seed: int):
    value = int(seed) & 0x7FFFFFFF
    while True:
        value = (1103515245 * value + 12345) & 0x7FFFFFFF
        yield value


def _rand_between(gen, lo: int, hi: int) -> int:
    value = next(gen)
    return int(lo) + (value % (int(hi) - int(lo) + 1))


def _pick_hero(role: str, gen, used: set[str]) -> str:
    pool = ROLE_HERO_POOLS.get(role, ROLE_HERO_POOLS["dps"])
    start = _rand_between(gen, 0, len(pool) - 1)
    for offset in range(len(pool)):
        key = pool[(start + offset) % len(pool)]
        if key not in used:
            used.add(key)
            return key
    key = pool[start]
    used.add(key)
    return key


def _synthetic_stats(role: str, *, winning_side: bool, gen) -> Dict[str, int]:
    if role == "tank":
        kills = _rand_between(gen, 21 if winning_side else 15, 39 if winning_side else 31)
        deaths = _rand_between(gen, 3 if winning_side else 7, 9 if winning_side else 14)
        assists = _rand_between(gen, 4, 14)
        damage = _rand_between(gen, 10500, 20500)
        cure = _rand_between(gen, 0, 3600)
        resist = _rand_between(gen, 12000, 32000)
    elif role == "healer":
        kills = _rand_between(gen, 7, 23)
        deaths = _rand_between(gen, 2 if winning_side else 6, 8 if winning_side else 12)
        assists = _rand_between(gen, 17, 38)
        damage = _rand_between(gen, 2800, 9200)
        cure = _rand_between(gen, 9800, 19000)
        resist = _rand_between(gen, 0, 900)
    else:
        kills = _rand_between(gen, 22 if winning_side else 16, 40 if winning_side else 34)
        deaths = _rand_between(gen, 3 if winning_side else 7, 10 if winning_side else 14)
        assists = _rand_between(gen, 1, 12)
        damage = _rand_between(gen, 10800, 20500)
        cure = _rand_between(gen, 0, 1400)
        resist = _rand_between(gen, 0, 800)
    return {
        "kill": kills,
        "assist": assists,
        "death": deaths,
        "heroDamage": damage,
        "cure": cure,
        "resistDamage": resist,
        "finalHit": max(1, kills // 3),
        "damageTaken": resist + _rand_between(gen, 3200, 7800),
        "healingTaken": cure + _rand_between(gen, 2200, 6200),
    }


def _player_row(
    identity: Dict[str, Any],
    hero_key: str,
    role: str,
    stats: Dict[str, Any],
    *,
    friends: Optional[List[str]] = None,
    rank_sub: int = 1,
    rank_score: int = 4700,
) -> Dict[str, Any]:
    guid, hero_name, _hero_role = _hero_meta(hero_key)
    return {
        "name": identity["name"],
        "bnetId": identity["bnetId"],
        "customerToken": identity["customerToken"],
        "heroGuid": guid,
        "heroName": hero_name,
        "roleType": role,
        "roleGuid": ROLE_GUID.get(role, ROLE_GUID["dps"]),
        "kill": int(stats.get("kill") or 0),
        "assist": int(stats.get("assist") or 0),
        "death": int(stats.get("death") or 0),
        "heroDamage": int(stats.get("heroDamage") or 0),
        "cure": int(stats.get("cure") or 0),
        "resistDamage": int(stats.get("resistDamage") or 0),
        "damageTaken": int(stats.get("damageTaken") or 0),
        "healingTaken": int(stats.get("healingTaken") or 0),
        "finalHit": int(stats.get("finalHit") or max(1, int(stats.get("kill") or 0) // 3)),
        "rankInfo": _rank(sub=rank_sub, score=rank_score),
        "friendBnetIds": list(friends or []),
    }


def _source_stats(source: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "kill": source.get("kill"), "assist": source.get("assist"), "death": source.get("death"),
        "heroDamage": source.get("heroDamage"), "cure": source.get("cure"),
        "resistDamage": source.get("resistDamage"), "finalHit": source.get("finalHit"),
        "damageTaken": source.get("damageTaken"), "healingTaken": source.get("healingTaken"),
    }


def _duo_stats(source: Dict[str, Any], gen) -> Optional[tuple[str, str, Dict[str, Any]]]:
    raw = source.get("duoStats")
    if not isinstance(raw, dict):
        return None
    role = str(raw.get("roleType") or "dps")
    hero_key = _hero_from_guid(raw.get("heroGuid"), ROLE_HERO_POOLS.get(role, ["sojourn"])[0])
    stats = {
        "kill": raw.get("kill"), "assist": raw.get("assist"), "death": raw.get("death"),
        "heroDamage": raw.get("heroDamage"), "cure": raw.get("cure"), "resistDamage": raw.get("resistDamage"),
        "finalHit": max(1, int(raw.get("kill") or 0) // 3),
        "damageTaken": int(raw.get("resistDamage") or 0) + _rand_between(gen, 3000, 6500),
        "healingTaken": int(raw.get("cure") or 0) + _rand_between(gen, 2200, 5200),
    }
    return hero_key, role, stats


def _build_team_rows(source: Dict[str, Any]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """为每一场生成独立的 5v5 / 开放 6v6 记分板。

    本人数据来自真实逐局摘要；此前文件中能对应到双排同 matchId 的场次，第二名玩家也沿用
    双排账号当局的真实逐局摘要。其余公开玩家没有被之前的文件保存，因此按职责和胜负单独生成，
    每场阵容、英雄和数值都不同，不再复用同一套模板。
    """
    seed = _detail_seed(source)
    gen = _lcg(seed)
    open_6v6 = str(source.get("gameMode") or "") == "SportOpen"
    target_counts = {"tank": 2 if open_6v6 else 1, "dps": 2, "healer": 2}
    friend_win = int(source.get("matchRet") or 0) == 1

    match_no = int(source.get("previewIndex") or 1)
    name_start = 2 + ((match_no * 5) % (len(ANON_PLAYERS) - 12))
    friend_identities = [ANON_PLAYERS[0], ANON_PLAYERS[1]]
    cursor = name_start
    while len(friend_identities) < sum(target_counts.values()):
        cand = ANON_PLAYERS[cursor % len(ANON_PLAYERS)]
        cursor += 1
        if cand not in friend_identities:
            friend_identities.append(cand)
    enemy_identities: List[Dict[str, Any]] = []
    while len(enemy_identities) < sum(target_counts.values()):
        cand = ANON_PLAYERS[cursor % len(ANON_PLAYERS)]
        cursor += 1
        if cand not in friend_identities and cand not in enemy_identities:
            enemy_identities.append(cand)

    used_friend: set[str] = set()
    used_enemy: set[str] = set()
    friendly: List[Dict[str, Any]] = []

    # 本人：真实逐局摘要。
    self_role = str(source.get("roleType") or "tank")
    self_key = _hero_from_guid(source.get("heroGuid"), ROLE_HERO_POOLS.get(self_role, ["winston"])[0])
    counts = dict(target_counts)
    counts[self_role] = max(0, counts.get(self_role, 0) - 1)
    used_friend.add(self_key)

    duo = _duo_stats(source, gen)
    self_friends: List[str] = []
    duo_friends: List[str] = []
    if duo is not None:
        self_friends = [ANON_PLAYERS[1]["bnetId"]]
        duo_friends = [ANON_PLAYERS[0]["bnetId"]]

    friendly.append(_player_row(
        ANON_PLAYERS[0], self_key, self_role, _source_stats(source),
        friends=self_friends, rank_sub=1 + ((match_no - 1) % 5), rank_score=4785 - match_no * 6,
    ))

    next_identity = 1
    if duo is not None:
        duo_key, duo_role, duo_stat = duo
        used_friend.add(duo_key)
        counts[duo_role] = max(0, counts.get(duo_role, 0) - 1)
        friendly.append(_player_row(
            ANON_PLAYERS[1], duo_key, duo_role, duo_stat,
            friends=duo_friends, rank_sub=1 + (match_no % 5), rank_score=4760 - match_no * 4,
        ))
        next_identity = 2

    # 如果没有双排摘要，第二个身份也只是普通匿名队友；从当前对局剩余职责开始填。
    fill_identities = friend_identities[next_identity:]
    fill_index = 0
    for role in ("tank", "dps", "healer"):
        for _ in range(counts.get(role, 0)):
            identity = fill_identities[fill_index]
            fill_index += 1
            key = _pick_hero(role, gen, used_friend)
            stats = _synthetic_stats(role, winning_side=friend_win, gen=gen)
            friendly.append(_player_row(
                identity, key, role, stats,
                rank_sub=1 + _rand_between(gen, 0, 4), rank_score=4580 + _rand_between(gen, 0, 220),
            ))

    enemy: List[Dict[str, Any]] = []
    enemy_index = 0
    for role in ("tank", "dps", "healer"):
        for _ in range(target_counts.get(role, 0)):
            identity = enemy_identities[enemy_index]
            enemy_index += 1
            key = _pick_hero(role, gen, used_enemy)
            stats = _synthetic_stats(role, winning_side=not friend_win, gen=gen)
            enemy.append(_player_row(
                identity, key, role, stats,
                rank_sub=1 + _rand_between(gen, 0, 4), rank_score=4580 + _rand_between(gen, 0, 240),
            ))

    return friendly, enemy


def _individual_hero_list(player: Dict[str, Any], source: Dict[str, Any]) -> List[Dict[str, Any]]:
    primary_key = _hero_from_guid(player.get("heroGuid"), "winston")
    keys = [primary_key]
    match_no = int(source.get("previewIndex") or 1)
    total = int(source.get("gameTimeSec") or 780)

    # 第一把只给“完全由预览生成”的匿名队友加三英雄展示。
    # ANON_PLAYERS[9] = 上最佳如喝水#51783；不是本人，也不是 duoStats 的真实摘要映射。
    if match_no == 1 and str(player.get("customerToken") or "") == str(ANON_PLAYERS[9]["customerToken"]):
        role = str(player.get("roleType") or _hero_meta(primary_key)[2])
        for candidate in ROLE_HERO_POOLS.get(role, []):
            if candidate not in keys:
                keys.append(candidate)
            if len(keys) == 3:
                break
        first = int(total * 0.62)
        second = int(total * 0.24)
        times = [first, second, max(1, total - first - second)]
    else:
        # 其余逻辑保持原样：少量生成玩家可能有一个次英雄。
        token_no = int(str(player.get("bnetId") or "0")[-2:] or 0)
        if (match_no + token_no) % 5 == 0:
            role = str(player.get("roleType") or _hero_meta(primary_key)[2])
            pool = ROLE_HERO_POOLS.get(role, [])
            for candidate in pool:
                if candidate != primary_key:
                    keys.append(candidate)
                    break
        if len(keys) == 1:
            times = [total]
        else:
            times = [max(120, int(total * 0.78)), max(60, int(total * 0.22))]

    heroes: List[Dict[str, Any]] = []
    for idx, key in enumerate(keys[:3]):
        guid, name, _role = _hero_meta(key)
        seconds = times[idx]
        heroes.append({
            "heroGuid": guid,
            "heroName": name,
            "playTime": seconds,
            "statMap": {"playTime": seconds},
        })
    return heroes


def _ban_hero(key: str) -> Dict[str, Any]:
    guid, name, _role = _hero_meta(key)
    return {"heroGuid": guid, "heroName": name}


def _bans_for_match(source: Dict[str, Any]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    options = [
        ("ana", "sigma", "kiriko", "echo"),
        ("winston", "freja", "baptiste", "dmon"),
        ("moira", "ashe", "ramattra", "juno"),
        ("dva", "sojourn", "jetpack", "sigma"),
        ("lucio", "anran", "winston", "kiriko"),
    ]
    idx = (int(source.get("previewIndex") or 1) - 1) % len(options)
    a, b, c, d = options[idx]
    return [_ban_hero(a), _ban_hero(b)], [_ban_hero(c), _ban_hero(d)]


class ReplayDashenClient:
    """只替换 DashenClient 的 HTTP 返回；方法名与真实 DashenClient 一致。"""

    def __init__(self, dataset: "PreviewDataset") -> None:
        self.dataset = dataset

    def __enter__(self) -> "ReplayDashenClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def close(self) -> None:
        return None

    def search_bnet_account(self, battletag: str) -> Dict[str, Any]:
        tag = str(battletag or SELF_TAG).replace("＃", "#").strip() or SELF_TAG
        return {
            "code": 0,
            "data": {
                "name": tag,
                "bnetId": SELF_BNET,
                "customerToken": SELF_TOKEN,
                "icon": "preview://player-avatar",
            },
        }

    def query_card(self, customer_token: str) -> Dict[str, Any]:
        row = PLAYER_BY_TOKEN.get(str(customer_token))
        if row is None:
            row = PLAYER_BY_TOKEN[SELF_TOKEN]
        return {
            "code": 0,
            "data": {
                "name": row["name"],
                "bnetId": row["bnetId"],
                "icon": "preview://player-avatar" if row["customerToken"] == SELF_TOKEN else "",
                "title": row["title"],
                "level": 312 if row["customerToken"] == SELF_TOKEN else 180,
                "gameTime": "1253.15" if row["customerToken"] == SELF_TOKEN else "760.0",
            },
        }

    def query_count_info(self, customer_token: str, game_mode: str, *, season: Optional[int] = None) -> Dict[str, Any]:
        season_no = CURRENT_SEASON if season is None else int(season)
        mode = str(game_mode or "sport").lower()
        rows = self.dataset.matches_for(season_no, mode)
        data: Dict[str, Any] = {
            "currentSeason": CURRENT_SEASON,
            "season": season_no,
            # 模拟 countInfo 自带近期/第一页；真实 service 仍会继续 queryMatchList 并按 matchId 去重。
            "matchList": copy.deepcopy(rows[:6]),
        }
        if mode == "sport":
            data["guideCountData"] = self.dataset.role_rows(season_no)
        else:
            # 给快速模式一个真实 parser 能识别的英雄汇总，不影响竞技段位卡。
            data["v6HeroUseSummaryList"] = [
                {"heroGuid": HERO["kiriko"][0], "matchSum": 8, "winSum": 5, "winRate": 62.5},
                {"heroGuid": HERO["sojourn"][0], "matchSum": 6, "winSum": 3, "winRate": 50.0},
            ]
        return {"code": 0, "data": data}

    def query_match_list(
        self,
        customer_token: str,
        game_mode: str,
        *,
        page: int = 1,
        season: Optional[int] = None,
    ) -> Dict[str, Any]:
        season_no = CURRENT_SEASON if season is None else int(season)
        mode = str(game_mode or "sport").lower()
        rows = self.dataset.matches_for(season_no, mode)
        page_size = 8
        start = (max(1, int(page)) - 1) * page_size
        end = start + page_size
        return {"code": 0, "data": {"matchList": copy.deepcopy(rows[start:end])}}

    def query_match_info(self, customer_token: str, match_id: str) -> Dict[str, Any]:
        return self.dataset.match_detail_raw(str(customer_token), str(match_id))

    # 当前战绩页默认 include_fight=True。预览明确返回“角斗无数据”，让真实 service 正常走空页结束。
    def fight_query_match_info(self, customer_token: str, match_id: str) -> Dict[str, Any]:
        return {"code": 0, "data": {}}

    def fight_query_count(self, customer_token: str, *, season: Optional[int] = None) -> Dict[str, Any]:
        return {"code": 0, "data": {"currentSeason": CURRENT_SEASON}}

    def fight_leisure_role_card(self, customer_token: str, *, season: Optional[int] = None) -> Dict[str, Any]:
        return {"code": 0, "data": {}}

    def fight_query_match_list(
        self,
        customer_token: str,
        game_mode: str = "SportFight",
        *,
        page: int = 1,
        season: Optional[int] = None,
    ) -> Dict[str, Any]:
        return {"code": 0, "data": {"matchList": []}}


SHOWCASE_MATCHES: List[Dict[str, Any]] = [{'matchRet': 1,
  'gameMode': 'SportPreset',
  'instanceType': 'IT_RANKED',
  'mapGuid': '576460752303426380',
  'heroGuid': '207165582859042825',
  'roleType': 'tank',
  'teamScore': 3,
  'opponentScore': 1,
  'kill': 35,
  'assist': 6,
  'death': 2,
  'heroDamage': 17190,
  'cure': 2942,
  'resistDamage': 23572,
  'matchId': 'a7c9e2f1-5b4d-4a6e-9c14-100000000001',
  'mapName': '中城',
  'heroName': 'Winston',
  'finalHit': 11,
  'damageTaken': 28572,
  'healingTaken': 6442,
  'gameTimeSec': 757,
  'beginTs': 1790344800000,
  'rankInfo': {'rankName': 'Champion', 'rankSubTier': 1, 'rankScore': 4780},
  'previewIndex': 1,
  'duoStats': {'heroGuid': '207165582859043377',
               'roleType': 'healer',
               'kill': 27,
               'assist': 29,
               'death': 3,
               'heroDamage': 9500,
               'cure': 11256,
               'resistDamage': 0,
               'heroName': 'Kiriko'}},
 {'matchRet': 1,
  'gameMode': 'SportPreset',
  'instanceType': 'IT_RANKED',
  'mapGuid': '576460752303425182',
  'heroGuid': '207165582859044162',
  'roleType': 'tank',
  'teamScore': 2,
  'opponentScore': 0,
  'kill': 37,
  'assist': 8,
  'death': 2,
  'heroDamage': 13970,
  'cure': 0,
  'resistDamage': 6902,
  'matchId': 'a7c9e2f1-5b4d-4a6e-9c25-100000000002',
  'mapName': '绿洲城',
  'heroName': 'D.Mon',
  'finalHit': 12,
  'damageTaken': 11902,
  'healingTaken': 3500,
  'gameTimeSec': 794,
  'beginTs': 1790344020000,
  'rankInfo': {'rankName': 'Champion', 'rankSubTier': 2, 'rankScore': 4773},
  'previewIndex': 2,
  'duoStats': {'heroGuid': '207165582859042926',
               'roleType': 'dps',
               'kill': 26,
               'assist': 2,
               'death': 4,
               'heroDamage': 10915,
               'cure': 1369,
               'resistDamage': 0,
               'heroName': 'Soldier: 76'}},
 {'matchRet': 1,
  'gameMode': 'SportPreset',
  'instanceType': 'IT_RANKED',
  'mapGuid': '576460752303423700',
  'heroGuid': '207165582859043387',
  'roleType': 'tank',
  'teamScore': 3,
  'opponentScore': 2,
  'kill': 41,
  'assist': 6,
  'death': 8,
  'heroDamage': 19945,
  'cure': 2334,
  'resistDamage': 28279,
  'matchId': 'a7c9e2f1-5b4d-4a6e-9c36-100000000003',
  'mapName': '国王大道',
  'heroName': 'Sigma',
  'finalHit': 13,
  'damageTaken': 33279,
  'healingTaken': 5834,
  'gameTimeSec': 831,
  'beginTs': 1790343240000,
  'rankInfo': {'rankName': 'Champion', 'rankSubTier': 3, 'rankScore': 4766},
  'previewIndex': 3,
  'duoStats': {'heroGuid': '207165582859043361',
               'roleType': 'healer',
               'kill': 33,
               'assist': 42,
               'death': 7,
               'heroDamage': 9409,
               'cure': 18479,
               'resistDamage': 118,
               'heroName': 'Baptiste'}},
 {'matchRet': -1,
  'gameMode': 'SportPreset',
  'instanceType': 'IT_RANKED',
  'mapGuid': '576460752303424195',
  'heroGuid': '207165582859044118',
  'roleType': 'healer',
  'teamScore': 1,
  'opponentScore': 3,
  'kill': 16,
  'assist': 13,
  'death': 9,
  'heroDamage': 5542,
  'cure': 12247,
  'resistDamage': 0,
  'matchId': 'a7c9e2f1-5b4d-4a6e-9c47-100000000004',
  'mapName': '多拉多',
  'heroName': 'Jetpack Cat',
  'finalHit': 5,
  'damageTaken': 3500,
  'healingTaken': 14747,
  'gameTimeSec': 868,
  'beginTs': 1790342460000,
  'rankInfo': {'rankName': 'Champion', 'rankSubTier': 4, 'rankScore': 4759},
  'previewIndex': 4},
 {'matchRet': 1,
  'gameMode': 'SportOpen',
  'instanceType': 'IT_RULESET',
  'mapGuid': '576460752303423700',
  'heroGuid': '207165582859043234',
  'roleType': 'healer',
  'teamScore': 2,
  'opponentScore': 1,
  'kill': 40,
  'assist': 38,
  'death': 1,
  'heroDamage': 10293,
  'cure': 13813,
  'resistDamage': 0,
  'matchId': 'a7c9e2f1-5b4d-4a6e-9c58-100000000005',
  'mapName': '国王大道',
  'heroName': 'Moira',
  'finalHit': 13,
  'damageTaken': 3500,
  'healingTaken': 16313,
  'gameTimeSec': 905,
  'beginTs': 1790341680000,
  'rankInfo': {'rankName': 'Champion', 'rankSubTier': 5, 'rankScore': 4752},
  'previewIndex': 5,
  'duoStats': {'heroGuid': '207165582859043626',
               'roleType': 'dps',
               'kill': 19,
               'assist': 3,
               'death': 2,
               'heroDamage': 10018,
               'cure': 0,
               'resistDamage': 0,
               'heroName': 'Freja'}},
 {'matchRet': 1,
  'gameMode': 'SportPreset',
  'instanceType': 'IT_RANKED',
  'mapGuid': '576460752303427091',
  'heroGuid': '207165582859042825',
  'roleType': 'tank',
  'teamScore': 3,
  'opponentScore': 2,
  'kill': 36,
  'assist': 6,
  'death': 12,
  'heroDamage': 16478,
  'cure': 2143,
  'resistDamage': 18953,
  'matchId': 'a7c9e2f1-5b4d-4a6e-9c69-100000000006',
  'mapName': '新渣客城',
  'heroName': 'Winston',
  'finalHit': 12,
  'damageTaken': 23953,
  'healingTaken': 5643,
  'gameTimeSec': 942,
  'beginTs': 1790340900000,
  'rankInfo': {'rankName': 'Champion', 'rankSubTier': 1, 'rankScore': 4745},
  'previewIndex': 6},
 {'matchRet': 1,
  'gameMode': 'SportPreset',
  'instanceType': 'IT_RANKED',
  'mapGuid': '576460752303427250',
  'heroGuid': '207165582859042938',
  'roleType': 'tank',
  'teamScore': 1,
  'opponentScore': 0,
  'kill': 26,
  'assist': 16,
  'death': 3,
  'heroDamage': 12749,
  'cure': 3075,
  'resistDamage': 9959,
  'matchId': 'a7c9e2f1-5b4d-4a6e-9c70-100000000007',
  'mapName': '鲁纳塞彼',
  'heroName': 'D.Va',
  'finalHit': 8,
  'damageTaken': 14959,
  'healingTaken': 6575,
  'gameTimeSec': 979,
  'beginTs': 1790340120000,
  'rankInfo': {'rankName': 'Champion', 'rankSubTier': 2, 'rankScore': 4738},
  'previewIndex': 7},
 {'matchRet': -1,
  'gameMode': 'SportPreset',
  'instanceType': 'IT_RANKED',
  'mapGuid': '576460752303427628',
  'heroGuid': '207165582859042938',
  'roleType': 'tank',
  'teamScore': 0,
  'opponentScore': 1,
  'kill': 6,
  'assist': 2,
  'death': 5,
  'heroDamage': 8337,
  'cure': 1238,
  'resistDamage': 11954,
  'matchId': 'a7c9e2f1-5b4d-4a6e-9c81-100000000008',
  'mapName': '霓虹枢纽',
  'heroName': 'D.Va',
  'finalHit': 2,
  'damageTaken': 16954,
  'healingTaken': 4738,
  'gameTimeSec': 756,
  'beginTs': 1790339340000,
  'rankInfo': {'rankName': 'Champion', 'rankSubTier': 3, 'rankScore': 4731},
  'previewIndex': 8},
 {'matchRet': -1,
  'gameMode': 'SportPreset',
  'instanceType': 'IT_RANKED',
  'mapGuid': '576460752303426878',
  'heroGuid': '207165582859042825',
  'roleType': 'tank',
  'teamScore': 2,
  'opponentScore': 3,
  'kill': 21,
  'assist': 9,
  'death': 13,
  'heroDamage': 18594,
  'cure': 3666,
  'resistDamage': 18662,
  'matchId': 'a7c9e2f1-5b4d-4a6e-9c92-100000000009',
  'mapName': '苏拉瓦萨',
  'heroName': 'Winston',
  'finalHit': 7,
  'damageTaken': 23662,
  'healingTaken': 7166,
  'gameTimeSec': 793,
  'beginTs': 1790338560000,
  'rankInfo': {'rankName': 'Champion', 'rankSubTier': 4, 'rankScore': 4724},
  'previewIndex': 9},
 {'matchRet': 1,
  'gameMode': 'SportPreset',
  'instanceType': 'IT_RANKED',
  'mapGuid': '576460752303424175',
  'heroGuid': '207165582859044118',
  'roleType': 'healer',
  'teamScore': 2,
  'opponentScore': 1,
  'kill': 18,
  'assist': 24,
  'death': 7,
  'heroDamage': 7263,
  'cure': 15499,
  'resistDamage': 0,
  'matchId': 'a7c9e2f1-5b4d-4a6e-9c03-100000000010',
  'mapName': '好莱坞',
  'heroName': 'Jetpack Cat',
  'finalHit': 6,
  'damageTaken': 3500,
  'healingTaken': 17999,
  'gameTimeSec': 830,
  'beginTs': 1790337780000,
  'rankInfo': {'rankName': 'Champion', 'rankSubTier': 5, 'rankScore': 4717},
  'previewIndex': 10},
 {'matchRet': 1,
  'gameMode': 'SportPreset',
  'instanceType': 'IT_RANKED',
  'mapGuid': '576460752303426899',
  'heroGuid': '207165582859042825',
  'roleType': 'tank',
  'teamScore': 1,
  'opponentScore': 0,
  'kill': 35,
  'assist': 11,
  'death': 6,
  'heroDamage': 13809,
  'cure': 5409,
  'resistDamage': 22637,
  'matchId': 'a7c9e2f1-5b4d-4a6e-9c14-100000000011',
  'mapName': '埃斯佩兰萨',
  'heroName': 'Winston',
  'finalHit': 11,
  'damageTaken': 27637,
  'healingTaken': 8909,
  'gameTimeSec': 867,
  'beginTs': 1790337000000,
  'rankInfo': {'rankName': 'Champion', 'rankSubTier': 1, 'rankScore': 4710},
  'previewIndex': 11},
 {'matchRet': -1,
  'gameMode': 'SportPreset',
  'instanceType': 'IT_RANKED',
  'mapGuid': '576460752303426116',
  'heroGuid': '207165582859043387',
  'roleType': 'tank',
  'teamScore': 0,
  'opponentScore': 3,
  'kill': 19,
  'assist': 2,
  'death': 8,
  'heroDamage': 13129,
  'cure': 181,
  'resistDamage': 22297,
  'matchId': 'a7c9e2f1-5b4d-4a6e-9c25-100000000012',
  'mapName': '哈瓦那',
  'heroName': 'Sigma',
  'finalHit': 6,
  'damageTaken': 27297,
  'healingTaken': 3681,
  'gameTimeSec': 904,
  'beginTs': 1790336220000,
  'rankInfo': {'rankName': 'Champion', 'rankSubTier': 2, 'rankScore': 4703},
  'previewIndex': 12},
 {'matchRet': 1,
  'gameMode': 'SportPreset',
  'instanceType': 'IT_RANKED',
  'mapGuid': '576460752303425165',
  'heroGuid': '207165582859042825',
  'roleType': 'tank',
  'teamScore': 2,
  'opponentScore': 1,
  'kill': 27,
  'assist': 3,
  'death': 5,
  'heroDamage': 11579,
  'cure': 3095,
  'resistDamage': 20837,
  'matchId': 'a7c9e2f1-5b4d-4a6e-9c36-100000000013',
  'mapName': '艾兴瓦尔德',
  'heroName': 'Winston',
  'finalHit': 9,
  'damageTaken': 25837,
  'healingTaken': 6595,
  'gameTimeSec': 941,
  'beginTs': 1790335440000,
  'rankInfo': {'rankName': 'Champion', 'rankSubTier': 3, 'rankScore': 4696},
  'previewIndex': 13},
 {'matchRet': -1,
  'gameMode': 'SportPreset',
  'instanceType': 'IT_RANKED',
  'mapGuid': '576460752303424195',
  'heroGuid': '207165582859042880',
  'roleType': 'tank',
  'teamScore': 2,
  'opponentScore': 3,
  'kill': 24,
  'assist': 11,
  'death': 6,
  'heroDamage': 11303,
  'cure': 9647,
  'resistDamage': 16344,
  'matchId': 'a7c9e2f1-5b4d-4a6e-9c47-100000000014',
  'mapName': '多拉多',
  'heroName': 'Roadhog',
  'finalHit': 8,
  'damageTaken': 21344,
  'healingTaken': 13147,
  'gameTimeSec': 978,
  'beginTs': 1790334660000,
  'rankInfo': {'rankName': 'Champion', 'rankSubTier': 4, 'rankScore': 4689},
  'previewIndex': 14},
 {'matchRet': 1,
  'gameMode': 'SportPreset',
  'instanceType': 'IT_RANKED',
  'mapGuid': '576460752303426116',
  'heroGuid': '207165582859043387',
  'roleType': 'tank',
  'teamScore': 3,
  'opponentScore': 0,
  'kill': 24,
  'assist': 4,
  'death': 3,
  'heroDamage': 11581,
  'cure': 0,
  'resistDamage': 11917,
  'matchId': 'a7c9e2f1-5b4d-4a6e-9c58-100000000015',
  'mapName': '哈瓦那',
  'heroName': 'Sigma',
  'finalHit': 8,
  'damageTaken': 16917,
  'healingTaken': 3500,
  'gameTimeSec': 755,
  'beginTs': 1790333880000,
  'rankInfo': {'rankName': 'Champion', 'rankSubTier': 5, 'rankScore': 4682},
  'previewIndex': 15},
 {'matchRet': 1,
  'gameMode': 'SportPreset',
  'instanceType': 'IT_RANKED',
  'mapGuid': '576460752303426878',
  'heroGuid': '207165582859044118',
  'roleType': 'healer',
  'teamScore': 3,
  'opponentScore': 1,
  'kill': 17,
  'assist': 25,
  'death': 2,
  'heroDamage': 2875,
  'cure': 9339,
  'resistDamage': 0,
  'matchId': 'a7c9e2f1-5b4d-4a6e-9c69-100000000016',
  'mapName': '苏拉瓦萨',
  'heroName': 'Jetpack Cat',
  'finalHit': 5,
  'damageTaken': 3500,
  'healingTaken': 11839,
  'gameTimeSec': 792,
  'beginTs': 1790333100000,
  'rankInfo': {'rankName': 'Champion', 'rankSubTier': 1, 'rankScore': 4675},
  'previewIndex': 16,
  'duoStats': {'heroGuid': '207165582859042882',
               'roleType': 'dps',
               'kill': 20,
               'assist': 4,
               'death': 4,
               'heroDamage': 8464,
               'cure': 141,
               'resistDamage': 85,
               'heroName': 'Cassidy'}},
 {'matchRet': 1,
  'gameMode': 'SportPreset',
  'instanceType': 'IT_RANKED',
  'mapGuid': '576460752303427628',
  'heroGuid': '207165582859043387',
  'roleType': 'tank',
  'teamScore': 3,
  'opponentScore': 2,
  'kill': 30,
  'assist': 5,
  'death': 7,
  'heroDamage': 24044,
  'cure': 550,
  'resistDamage': 33225,
  'matchId': 'a7c9e2f1-5b4d-4a6e-9c70-100000000017',
  'mapName': '霓虹枢纽',
  'heroName': 'Sigma',
  'finalHit': 10,
  'damageTaken': 38225,
  'healingTaken': 4050,
  'gameTimeSec': 829,
  'beginTs': 1790332320000,
  'rankInfo': {'rankName': 'Champion', 'rankSubTier': 2, 'rankScore': 4668},
  'previewIndex': 17,
  'duoStats': {'heroGuid': '207165582859043626',
               'roleType': 'dps',
               'kill': 25,
               'assist': 2,
               'death': 12,
               'heroDamage': 14580,
               'cure': 0,
               'resistDamage': 0,
               'heroName': 'Freja'}},
 {'matchRet': -1,
  'gameMode': 'SportPreset',
  'instanceType': 'IT_RANKED',
  'mapGuid': '576460752303425506',
  'heroGuid': '207165582859044162',
  'roleType': 'tank',
  'teamScore': 0,
  'opponentScore': 2,
  'kill': 11,
  'assist': 2,
  'death': 6,
  'heroDamage': 5395,
  'cure': 480,
  'resistDamage': 6537,
  'matchId': 'a7c9e2f1-5b4d-4a6e-9c81-100000000018',
  'mapName': '釜山',
  'heroName': 'D.Mon',
  'finalHit': 3,
  'damageTaken': 11537,
  'healingTaken': 3980,
  'gameTimeSec': 866,
  'beginTs': 1790331540000,
  'rankInfo': {'rankName': 'Champion', 'rankSubTier': 3, 'rankScore': 4661},
  'previewIndex': 18,
  'duoStats': {'heroGuid': '207165582859043779',
               'roleType': 'healer',
               'kill': 6,
               'assist': 10,
               'death': 6,
               'heroDamage': 4563,
               'cure': 6111,
               'resistDamage': 710,
               'heroName': 'Wuyang'}},
 {'matchRet': 1,
  'gameMode': 'SportPreset',
  'instanceType': 'IT_RANKED',
  'mapGuid': '576460752303425122',
  'heroGuid': '207165582859043308',
  'roleType': 'dps',
  'teamScore': 2,
  'opponentScore': 1,
  'kill': 20,
  'assist': 0,
  'death': 3,
  'heroDamage': 11006,
  'cure': 0,
  'resistDamage': 0,
  'matchId': 'a7c9e2f1-5b4d-4a6e-9c92-100000000019',
  'mapName': '漓江塔',
  'heroName': 'Sojourn',
  'finalHit': 6,
  'damageTaken': 3500,
  'healingTaken': 3500,
  'gameTimeSec': 903,
  'beginTs': 1790330760000,
  'rankInfo': {'rankName': 'Champion', 'rankSubTier': 4, 'rankScore': 4654},
  'previewIndex': 19},
 {'matchRet': 1,
  'gameMode': 'SportPreset',
  'instanceType': 'IT_RANKED',
  'mapGuid': '576460752303423876',
  'heroGuid': '207165582859044056',
  'roleType': 'dps',
  'teamScore': 4,
  'opponentScore': 3,
  'kill': 33,
  'assist': 0,
  'death': 9,
  'heroDamage': 12725,
  'cure': 717,
  'resistDamage': 16,
  'matchId': 'a7c9e2f1-5b4d-4a6e-9c03-100000000020',
  'mapName': '监测站：直布罗陀',
  'heroName': 'Emre',
  'finalHit': 11,
  'damageTaken': 3516,
  'healingTaken': 4217,
  'gameTimeSec': 940,
  'beginTs': 1790329980000,
  'rankInfo': {'rankName': 'Champion', 'rankSubTier': 5, 'rankScore': 4647},
  'previewIndex': 20}]


class PreviewDataset:
    def __init__(self) -> None:
        self._matches: Dict[tuple[int, str], List[Dict[str, Any]]] = {}
        self._match_index: Dict[str, Dict[str, Any]] = {}
        self._build_matches()

    @staticmethod
    def _season_copy(row: Dict[str, Any], season: int, idx: int) -> Dict[str, Any]:
        clone = copy.deepcopy(row)
        clone["matchId"] = f"SHOWCASE-S{season}-{idx:03d}"
        # 历史赛季仅用于页面切换测试；仍保持英杰段位与同样的数据风格。
        days = (CURRENT_SEASON - int(season)) * 45
        clone["beginTs"] = int(clone.get("beginTs") or 0) - days * 24 * 3600 * 1000
        clone["rankInfo"] = _rank(sub=1 + ((idx + season) % 5), score=4650 - idx * 5)
        return clone

    def _build_matches(self) -> None:
        current = [copy.deepcopy(row) for row in SHOWCASE_MATCHES]
        self._matches[(CURRENT_SEASON, "sport")] = current
        self._matches[(CURRENT_SEASON, "leisure")] = []

        for row in current:
            self._match_index[str(row["matchId"])] = copy.deepcopy(row)

        # S23 / S22 也保留足够数据让动态赛季页可点；不复制任何真实玩家身份。
        for season in (CURRENT_SEASON - 1, CURRENT_SEASON - 2):
            historical = [
                self._season_copy(row, season, i + 1)
                for i, row in enumerate(SHOWCASE_MATCHES[:12])
            ]
            self._matches[(season, "sport")] = historical
            self._matches[(season, "leisure")] = []
            for row in historical:
                self._match_index[str(row["matchId"])] = copy.deepcopy(row)

    def matches_for(self, season: int, mode: str) -> List[Dict[str, Any]]:
        return self._matches.get((int(season), str(mode).lower()), [])

    @staticmethod
    def role_rows(season: int) -> List[Dict[str, Any]]:
        # 所有可见职责段位统一为英杰；胜率做成 GitHub 展示友好的正常高分样例。
        return [
            {
                "roleType": "tank", "matchSum": 86, "winSum": 59, "loseSum": 27, "winRate": 68.6,
                "lastRankInfo": _rank(sub=2, score=4725), "maxRankInfo": _rank(sub=1, score=4860),
            },
            {
                "roleType": "dps", "matchSum": 34, "winSum": 21, "loseSum": 13, "winRate": 61.8,
                "lastRankInfo": _rank(sub=4, score=4635), "maxRankInfo": _rank(sub=3, score=4690),
            },
            {
                "roleType": "healer", "matchSum": 41, "winSum": 27, "loseSum": 14, "winRate": 65.9,
                "lastRankInfo": _rank(sub=3, score=4660), "maxRankInfo": _rank(sub=2, score=4730),
            },
            {
                "roleType": "open", "matchSum": 19, "winSum": 12, "loseSum": 7, "winRate": 63.2,
                "lastRankInfo": _rank(sub=5, score=4590), "maxRankInfo": _rank(sub=4, score=4640),
            },
        ]

    def match_detail_raw(self, customer_token: str, match_id: str) -> Dict[str, Any]:
        source = self._match_index.get(match_id) or next(iter(self._match_index.values()))
        friendly, enemy = _build_team_rows(source)
        all_rows = [*friendly, *enemy]
        focus = next(
            (row for row in all_rows if str(row.get("customerToken") or "") == str(customer_token or "")),
            friendly[0],
        )
        friendly_bans, enemy_bans = _bans_for_match(source)
        detail = {
            "matchId": match_id,
            "matchRet": source.get("matchRet", 1),
            "mapGuid": source.get("mapGuid"),
            "gameMode": source.get("gameMode"),
            "teamScore": source.get("teamScore", 3),
            "opponentScore": source.get("opponentScore", 1),
            "teammateList": copy.deepcopy(friendly),
            "enemyList": copy.deepcopy(enemy),
            "heroList": _individual_hero_list(focus, source),
            "teammateBanHeroList": copy.deepcopy(friendly_bans),
            "enemyBanHeroList": copy.deepcopy(enemy_bans),
            "banInfo": {
                "friendly": copy.deepcopy(friendly_bans),
                "enemy": copy.deepcopy(enemy_bans),
            },
            "banList": [
                *[{"teamType": "teammate", **copy.deepcopy(hero)} for hero in friendly_bans],
                *[{"teamType": "enemy", **copy.deepcopy(hero)} for hero in enemy_bans],
            ],
        }
        return {"code": 0, "data": detail}

_DATASET = PreviewDataset()


class PreviewService(OWStatsService):
    """真实 OWStatsService，只把 _new_client() 替换为离线 ReplayDashenClient。"""

    def __init__(self) -> None:
        self._preview_tmp = tempfile.TemporaryDirectory(prefix="ow-ui-preview-backend-")
        store = CredentialStore(Path(self._preview_tmp.name) / "dashen_credentials.json")
        # 写一份完全隔离的假凭证，只为了让真实 auth_status/credential API 保持正常语义。
        store.save(DashenCredential(role_id=123456789, token="preview-token", dts=2026, server=1))
        super().__init__(credential_store=store, current_season=None, proxy=None)

    def _new_client(self) -> ReplayDashenClient:
        return ReplayDashenClient(_DATASET)

    def match_all_players_detail(self, *args, **kwargs) -> Dict[str, Any]:
        # 最小兜底：详情页每个匿名玩家都保留游戏内真实存在的称号。
        # 正常路径 query_card() 已会返回 title；这里只防止个别详情链路丢字段。
        result = super().match_all_players_detail(*args, **kwargs)
        for player in result.get("players") or []:
            if str(player.get("title") or "").strip():
                continue
            identity = PLAYER_BY_TOKEN.get(str(player.get("customer_token") or ""))
            if identity is not None:
                player["title"] = str(identity.get("title") or "")
        return result

    @staticmethod
    def load_image(url: str) -> bytes:
        # GitHub 展示头像：优先使用随预览脚本一起提供的冰淇淋头像。
        if str(url or "").startswith("preview://"):
            candidates = (
                PREVIEW_AVATAR_PATH,
                ROOT / "assets" / "ui" / "portrait_placeholder.png",
            )
            for candidate in candidates:
                if candidate.exists():
                    return candidate.read_bytes()
            raise FileNotFoundError(
                "预览头像不存在：请把 preview_account_avatar.png 与 ui_preview.py 放在同一目录"
            )
        raise RuntimeError(f"UI 预览禁止网络图片请求：{url}")

    # 预览模式不启动真实扫码网络；误点授权时给清晰错误，而不是访问网易。
    def auth_start(self):
        raise RuntimeError("UI 预览模式不会访问网易大神扫码授权")



# ----------------------------- 账号切换离线展示 -----------------------------
PREVIEW_ACCOUNTS: List[Dict[str, Any]] = [
    {
        "id": "preview-account-1", "account_id": "preview-account-1",
        "name": "疑似开呱#51842", "display_name": "疑似开呱#51842",
        "battle_tag": "疑似开呱#51842", "battletag": "疑似开呱#51842",
        "region": "国服", "server": "CN", "is_active": True, "is_default": True,
    },
    {
        "id": "preview-account-2", "account_id": "preview-account-2",
        "name": "鱼塘冲浪#51624", "display_name": "鱼塘冲浪#51624",
        "battle_tag": "鱼塘冲浪#51624", "battletag": "鱼塘冲浪#51624",
        "region": "国服", "server": "CN", "is_active": False, "is_default": False,
    },
    {
        "id": "preview-account-3", "account_id": "preview-account-3",
        "name": "新号直冲英杰1#51675", "display_name": "新号直冲英杰1#51675",
        "battle_tag": "新号直冲英杰1#51675", "battletag": "新号直冲英杰1#51675",
        "region": "亚服", "server": "KR", "is_active": False, "is_default": False,
    },
]


def _preview_account_rows(avatar_path: Path) -> List[Dict[str, Any]]:
    rows = copy.deepcopy(PREVIEW_ACCOUNTS)
    for row in rows:
        # 同时提供 URI / 本地路径常见字段，兼容账号页的不同实现方式。
        row.update({
            "avatar": str(avatar_path),
            "avatar_path": str(avatar_path),
            "icon": str(avatar_path),
            "icon_url": "preview://account-avatar",
            "portrait": str(avatar_path),
            "status": "当前账号" if row.get("is_active") else "可切换",
        })
    return rows


def _install_account_switch_backend_preview(runtime_root: Path) -> List[Dict[str, Any]]:
    """尽量对当前项目的账号切换模块做离线注入。

    账号切换功能是在 ui_preview.py 之后加入项目的，因此这里不依赖某一个固定类名：
    只扫描名字中含 bnet/account/switch 的本地模块，并只替换“读取账号/执行切换”
    这一小组典型后端接口。若项目里没有该功能，本函数静默跳过。
    """
    avatar_path = runtime_root / "preview_account_avatar.png"
    if PREVIEW_AVATAR_PATH.exists():
        avatar_path.write_bytes(PREVIEW_AVATAR_PATH.read_bytes())
    accounts = _preview_account_rows(avatar_path)

    # 临时 QSettings 多写几组兼容键；全部位于本次预览临时目录，不碰真实配置。
    try:
        from PySide6.QtCore import QSettings
        settings = QSettings()
        payload = json.dumps(accounts, ensure_ascii=False)
        for key in (
            "bnet/accounts", "battle_net/accounts", "battleNet/accounts",
            "account_switch/accounts", "accounts/bnet",
        ):
            settings.setValue(key, payload)
        for key in (
            "bnet/active_account", "battle_net/active_account",
            "account_switch/active_account",
        ):
            settings.setValue(key, "preview-account-1")
        settings.setValue("dashen/account_name", SELF_TAG)
        settings.sync()
    except Exception:
        pass

    module_names: set[str] = set()
    # 根目录与 ui 包内，只挑明显和账号切换有关的模块名，避免导入整个项目。
    for search_path, prefix in (([str(ROOT)], ""),):
        try:
            for info in pkgutil.iter_modules(search_path):
                lower = info.name.lower()
                if any(token in lower for token in ("bnet", "account", "switch")):
                    module_names.add(prefix + info.name)
        except Exception:
            pass
    try:
        import ui
        for info in pkgutil.walk_packages(ui.__path__, ui.__name__ + "."):
            lower = info.name.lower()
            if any(token in lower for token in ("bnet", "account", "switch")):
                module_names.add(info.name)
    except Exception:
        pass

    list_method_names = {
        "list_accounts", "get_accounts", "discover_accounts", "read_accounts",
        "saved_accounts", "available_accounts",
    }
    current_method_names = {"current_account", "active_account", "get_current_account", "get_active_account"}
    switch_method_names = {"switch_account", "switch_to", "activate_account", "select_account", "launch_account"}

    def _list_accounts(*_args, **_kwargs):
        return copy.deepcopy(accounts)

    def _current_account(*_args, **_kwargs):
        return copy.deepcopy(accounts[0])

    def _switch_account(*args, **kwargs):
        target = kwargs.get("account_id") or kwargs.get("id")
        if target is None and args:
            target = args[-1]
        chosen = next(
            (row for row in accounts if str(row.get("id")) == str(target)
             or str(row.get("account_id")) == str(target)
             or str(row.get("name")) == str(target)),
            accounts[0],
        )
        return {"ok": True, "success": True, "account": copy.deepcopy(chosen), "preview": True}

    for module_name in sorted(module_names):
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue

        for name in list(vars(module)):
            obj = getattr(module, name, None)
            if inspect.isfunction(obj):
                lower = name.lower()
                if lower in list_method_names:
                    setattr(module, name, _list_accounts)
                elif lower in current_method_names:
                    setattr(module, name, _current_account)
                elif lower in switch_method_names:
                    setattr(module, name, _switch_account)
                continue

            if not inspect.isclass(obj):
                continue
            class_lower = name.lower()
            if not any(token in class_lower for token in ("bnet", "account", "switch", "store", "manager", "backend")):
                continue
            # 不替换 QWidget 页面的 load/refresh，只替换明确的后端方法名。
            for meth_name in list(vars(obj)):
                lower = meth_name.lower()
                try:
                    if lower in list_method_names:
                        setattr(obj, meth_name, _list_accounts)
                    elif lower in current_method_names:
                        setattr(obj, meth_name, _current_account)
                    elif lower in switch_method_names:
                        setattr(obj, meth_name, _switch_account)
                except Exception:
                    pass

    return accounts


def _apply_account_switch_page_preview(window: Any, accounts: List[Dict[str, Any]]) -> None:
    """MainWindow 创建后，再尝试把匿名账号直接交给账号页的公开/半公开填充接口。"""
    candidates: List[Any] = []
    for attr_name, value in vars(window).items():
        marker = (attr_name + " " + value.__class__.__name__).lower()
        if any(token in marker for token in ("bnet", "account", "switch")):
            candidates.append(value)

    for page in candidates:
        applied = False
        for method_name in ("set_accounts", "set_account_list", "show_accounts", "populate_accounts"):
            method = getattr(page, method_name, None)
            if callable(method):
                try:
                    method(copy.deepcopy(accounts))
                    applied = True
                    break
                except TypeError:
                    pass
                except Exception:
                    pass

        if not applied:
            for attr_name in ("_accounts", "accounts", "account_list"):
                if hasattr(page, attr_name):
                    try:
                        setattr(page, attr_name, copy.deepcopy(accounts))
                        applied = True
                    except Exception:
                        pass

        if applied:
            for refresh_name in ("refresh", "_refresh", "refresh_ui", "_render", "render"):
                refresh = getattr(page, refresh_name, None)
                if callable(refresh):
                    try:
                        refresh()
                        break
                    except TypeError:
                        pass
                    except Exception:
                        pass


# ----------------------------- 后端自检（不依赖 PySide6） -----------------------------
def _backend_self_test() -> int:
    service = PreviewService()
    player = service.resolve_player(SELF_TAG)
    assert player.customer_token == SELF_TOKEN

    card = service.player_card(customer_token=player.customer_token)
    assert card["battle_tag"] == SELF_TAG

    seasons = service.player_seasons(customer_token=player.customer_token, season_count=3)
    assert seasons["current_season"] == CURRENT_SEASON
    assert len(seasons["seasons"]) == 3

    history = service.match_history(customer_token=player.customer_token, season_count=3)
    assert history["matches"]
    # 关键：战绩 raw 不得出现 heroList，否则预览会无端变成多英雄战绩卡。
    assert all("heroList" not in (row.get("raw") or {}) for row in history["matches"])
    assert (history["matches"][0].get("raw") or {}).get("mapGuid") == "576460752303426380"

    first_five = history["matches"][:5]
    assert [row.get("result") for row in first_five] == ["win", "win", "win", "loss", "win"]
    assert (first_five[0].get("raw") or {}).get("mapGuid") == "576460752303426380"  # 中城
    assert (first_five[0].get("raw") or {}).get("heroGuid") == HERO["winston"][0]
    assert (first_five[1].get("raw") or {}).get("mapGuid") == "576460752303425182"  # 绿洲城
    assert (first_five[1].get("raw") or {}).get("heroGuid") == HERO["dmon"][0]
    assert (first_five[3].get("raw") or {}).get("roleType") == "healer"
    assert (first_five[4].get("raw") or {}).get("gameMode") == "SportOpen"

    detail_signatures = []
    for idx, match in enumerate(first_five):
        detail = service.match_all_players_detail(
            match["match_id"],
            customer_token=player.customer_token,
            source_match=match.get("raw") or {},
        )
        expected = 12 if idx == 4 else 10
        assert len(detail.get("scoreboard_players") or []) == expected
        assert len(detail.get("players") or []) == expected
        root = detail.get("detail_root") or {}
        assert len(root.get("teammateBanHeroList") or []) == 2
        assert len(root.get("enemyBanHeroList") or []) == 2
        scoreboard = detail.get("scoreboard_players") or []
        detail_signatures.append(tuple((p.get("name"), p.get("hero_guid"), p.get("kill"), p.get("death")) for p in scoreboard))
    assert len(set(detail_signatures)) == len(detail_signatures)
    assert all("#" in str(p.get("name") or "") for p in detail.get("scoreboard_players") or [])
    blob = json.dumps({"tags": _STREAMER_TAGS, "accounts": PREVIEW_ACCOUNTS}, ensure_ascii=False)
    assert "PRIVATE_BATTLETAG" not in blob and "PRIVATE_ACCOUNT_ID" not in blob

    print("ui_preview backend self-test: OK")
    print(f"history matches={len(history['matches'])}; first5 results=W/W/W/L/W; fifth=SportOpen 6v6")
    print("detail rosters are per-match; first four=10 players, fifth=12 players; bans=2v2")
    return 0


# ----------------------------- Qt 启动 -----------------------------
def _run_ui(auto_detail: bool = False) -> int:
    # 在导入任何 UI 模块之前隔离 QSettings / LOCALAPPDATA。
    runtime_tmp = tempfile.TemporaryDirectory(prefix="ow-ui-preview-runtime-")
    os.environ["LOCALAPPDATA"] = runtime_tmp.name
    os.environ["APPDATA"] = runtime_tmp.name
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")

    from PySide6.QtCore import QSettings, QTimer

    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, runtime_tmp.name)
    QSettings.setPath(QSettings.IniFormat, QSettings.SystemScope, runtime_tmp.name)

    preview_accounts = _install_account_switch_backend_preview(Path(runtime_tmp.name))

    # 先导入当前项目真实 UI，再只替换 MainWindow 创建 service 时使用的类。
    import ui.main_window as main_window_module
    import ui.assets as assets_module
    import ui.cache_manager as cache_manager_module

    main_window_module.OWStatsService = PreviewService

    # 玩家头像/运行缓存也隔离，防止预览误清真实 cache/。
    preview_cache = Path(runtime_tmp.name) / "cache"
    assets_module.AVATAR_CACHE_DIR = preview_cache / "avatars"
    cache_manager_module.AVATAR_CACHE_DIR = preview_cache / "avatars"
    cache_manager_module.PROJECT_CACHE_DIR = preview_cache

    from main import build_app

    app = build_app([sys.argv[0]])
    window = main_window_module.MainWindow()
    window.resize(1570, 990)
    window.show()
    window._select(0)
    _apply_account_switch_page_preview(window, preview_accounts)

    def start_query() -> None:
        page = window.owstats_page
        page.query_edit.setText(SELF_TAG)
        page.search()

    QTimer.singleShot(100, start_query)

    if auto_detail:
        attempts = {"n": 0}

        def open_when_ready() -> None:
            attempts["n"] += 1
            page = window.owstats_page
            if page._matches and not page._query_busy:
                # 打开当前显示列表的第一场，仍然调用现有 OWStatsPage.open_detail。
                page.open_detail(str(page._matches[0].get("match_id") or ""))
                return
            if attempts["n"] < 200:
                QTimer.singleShot(100, open_when_ready)

        QTimer.singleShot(250, open_when_ready)

    # 保证临时目录至少活到 Qt 事件循环退出。
    window._preview_runtime_tmp = runtime_tmp
    return app.exec()


def main() -> int:
    if "--backend-self-test" in sys.argv:
        return _backend_self_test()
    return _run_ui(auto_detail="--detail" in sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
