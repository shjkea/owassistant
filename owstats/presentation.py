# -*- coding: utf-8 -*-
"""把网易大神原始 JSON 转成前端可长期使用的稳定展示结构。

这里不发网络请求，也不包含 Qt。字段解析采取宽松兼容策略：优先使用已知字段，
字段变化时宁可显示“—”也不把原始 token / cookie 等内部值暴露到 UI。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional

from .game_catalog import resolve_map
from .localization import (
    localize_game_mode, localize_hero, localize_instance_type,
    localize_rank, localize_role,
)


def _dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _data(payload: Any) -> Dict[str, Any]:
    root = _dict(payload)
    value = root.get("data")
    return _dict(value) if isinstance(value, Mapping) else root


def _first(mapping: Mapping[str, Any], keys: Iterable[str], default: Any = "") -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", [], {}):
            return value
    return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_percent(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if 0.0 <= number <= 1.0:
        number *= 100.0
    if not 0.0 <= number <= 100.0:
        return None
    return number


def _role_key(item: Mapping[str, Any], index: int, total: int) -> Optional[str]:
    raw = str(_first(item, (
        "roleType", "role", "roleName", "position", "positionName", "typeName", "guideName",
    ), "")).strip().lower()
    aliases = {
        "tank": ("tank", "重装", "坦克", "t"),
        "damage": ("damage", "dps", "输出", "攻击", "c"),
        "support": ("support", "healer", "辅助", "治疗", "heal", "s"),
    }
    for key, words in aliases.items():
        if raw in words or any(word in raw for word in words if len(word) > 1):
            return key

    # 大神不同版本出现过数字职责；三项完整列表时，以其固定展示顺序兜底最稳妥。
    if total == 3 and 0 <= index < 3:
        return ("tank", "damage", "support")[index]
    return None



def _rank_info(item: Mapping[str, Any]) -> Mapping[str, Any]:
    # 网易大神当前 queryCountInfo 的真实结构把段位放在 lastRankInfo 中。
    # 同时保留若上游改名为 currentRankInfo/rankInfo 时的兼容。
    for key in ("lastRankInfo", "currentRankInfo", "rankInfo"):
        value = item.get(key)
        if isinstance(value, Mapping) and value:
            return value
    return item


def _rank_text(item: Mapping[str, Any]) -> str:
    info = _rank_info(item)
    tier_raw = str(_first(info, (
        "rank_name", "rankName", "tierName", "divisionName", "gradeName", "segmentName", "rank", "tier", "grade",
    ), "")).strip()
    # Do not infer a visible tier from rankScore/MMR/rankLevel alone.  Dashen can
    # return those internal values while the player is still unranked.
    if not tier_raw or tier_raw.lower() in {
        "0", "none", "null", "n/a", "na", "-", "—",
        "unranked", "placement", "placements", "placing", "未定级", "定级中",
    }:
        return "未定级"

    tier = localize_rank(tier_raw)
    sub = str(_first(info, (
        "rank_sub_tier", "rankSubTier", "rankLevel", "tierLevel", "division", "subRank", "star", "levelName",
    ), "")).strip()
    if sub and sub.lower() not in tier.lower():
        return f"{tier} {sub}".strip()
    return tier


def _rank_item(item: Mapping[str, Any]) -> Dict[str, Any]:
    games = _as_int(_first(item, (
        "matchSum", "matchCount", "gameCount", "games", "totalCount", "totalMatchCount", "count",
    ), 0))
    wins = _as_int(_first(item, ("winSum", "winCount", "wins", "win", "victoryCount"), 0))
    losses = _as_int(_first(item, ("loseSum", "loseCount", "lossCount", "losses", "lose"), 0))
    if games <= 0 and wins + losses > 0:
        games = wins + losses
    win_rate = _as_percent(_first(item, ("winRate", "winningRate", "winPercent", "victoryRate"), None))
    if win_rate is None and games > 0 and wins >= 0:
        win_rate = wins * 100.0 / games
    return {
        "rank": _rank_text(item),
        "games": games,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
    }


def _extract_ranks(sport_payload: Any) -> Dict[str, Dict[str, Any]]:
    out = {
        "tank": {"rank": "未定级", "games": 0, "wins": 0, "losses": 0, "win_rate": None},
        "damage": {"rank": "未定级", "games": 0, "wins": 0, "losses": 0, "win_rate": None},
        "support": {"rank": "未定级", "games": 0, "wins": 0, "losses": 0, "win_rate": None},
    }
    data = _data(sport_payload)
    values = data.get("guideCountData")
    if isinstance(values, Mapping):
        values = [v for v in values.values() if isinstance(v, Mapping)]
    if not isinstance(values, list):
        values = []
    for index, value in enumerate(values):
        if not isinstance(value, Mapping):
            continue
        key = _role_key(value, index, len(values))
        if key:
            out[key] = _rank_item(value)
    return out


def profile_view(payload: Dict[str, Any]) -> Dict[str, Any]:
    resolved = _dict(payload.get("resolved"))
    card = _data(payload.get("profile_card"))
    full_id = str(_first(card, ("name", "battleTag", "battleNetName"), resolved.get("full_id", ""))).strip()
    bnet_id = str(_first(card, ("bnetId", "battleNetId"), resolved.get("bnet_id", ""))).strip()
    icon_url = str(_first(card, ("icon", "avatar", "avatarUrl", "iconUrl"), resolved.get("icon_url", ""))).strip()
    return {
        "resolved": resolved,
        "name": full_id or str(resolved.get("query") or "玩家"),
        "bnet_id": bnet_id,
        "icon_url": icon_url,
        "title": str(_first(card, ("title", "playerTitle", "careerTitle"), "")).strip(),
        "level": _as_int(_first(card, ("level", "playerLevel"), 0)),
        "game_time": _first(card, ("gameTime", "playTime", "totalGameTime"), ""),
        "season": payload.get("logical_season"),
        "ranks": _extract_ranks(payload.get("sport")),
    }


def _looks_like_player(item: Mapping[str, Any]) -> bool:
    keys = {str(k).lower() for k in item.keys()}
    identity = any(k in keys for k in ("name", "playername", "battletag", "nickname", "bnetname", "roleid"))
    stats = any(k in keys for k in (
        "kill", "kills", "assist", "assists", "death", "deaths", "herodamage", "damage", "cure", "healing",
        "heroguid", "heroname",
    ))
    return identity and stats


def _find_player_rows(data: Any, depth: int = 0, team_hint: str = "") -> List[Dict[str, Any]]:
    if depth > 5:
        return []
    if isinstance(data, list):
        direct = [dict(x) for x in data if isinstance(x, Mapping) and _looks_like_player(x)]
        if direct:
            if team_hint:
                for item in direct:
                    item.setdefault("_team_hint", team_hint)
            return direct
        out: List[Dict[str, Any]] = []
        for item in data:
            out.extend(_find_player_rows(item, depth + 1, team_hint))
        return out
    if not isinstance(data, Mapping):
        return []

    out: List[Dict[str, Any]] = []
    local_team = str(_first(data, ("teamName", "team", "sideName", "side", "campName", "camp"), team_hint)).strip()
    preferred = (
        "playerList", "players", "memberList", "members", "matchPlayerList", "teamMemberList", "userList",
        "redTeam", "blueTeam", "teamList", "teams",
    )
    for key in preferred:
        if key in data:
            out.extend(_find_player_rows(data.get(key), depth + 1, local_team or key))
    if out:
        return out
    for key, value in data.items():
        if key in {"token", "customerToken", "cookie", "headers"}:
            continue
        out.extend(_find_player_rows(value, depth + 1, local_team))
    return out


def _player_view(item: Mapping[str, Any]) -> Dict[str, Any]:
    name = str(_first(item, ("name", "playerName", "battleTag", "bnetName", "nickname", "roleName"), "未知玩家")).strip()
    team = str(_first(item, ("teamName", "team", "sideName", "side", "campName", "camp", "_team_hint"), "")).strip()
    hero = localize_hero(_first(item, ("heroName", "hero", "heroGuid", "heroId"), "—"))
    rank_info = item.get("rankInfo") if isinstance(item.get("rankInfo"), Mapping) else {}
    role_raw = _first(item, ("roleName", "roleType", "role", "position", "roleGuid"), "") or _first(rank_info, ("roleType", "role", "position", "roleGuid"), "")
    role = localize_role(role_raw) or "—"
    return {
        "team": team or "—",
        "name": name or "未知玩家",
        "hero": hero or "—",
        "role": role or "—",
        "kills": _as_int(_first(item, ("kill", "kills", "eliminations"), 0)),
        "assists": _as_int(_first(item, ("assist", "assists"), 0)),
        "deaths": _as_int(_first(item, ("death", "deaths"), 0)),
        "damage": _as_int(_first(item, ("heroDamage", "damage", "damageDone"), 0)),
        "healing": _as_int(_first(item, ("cure", "healing", "healingDone"), 0)),
        "mitigation": _as_int(_first(item, ("resistDamage", "mitigation", "damageBlocked"), 0)),
    }


def detail_view(payload: Dict[str, Any]) -> Dict[str, Any]:
    source = _dict(payload.get("source_match"))
    detail_payload = payload.get("detail")
    root = _data(detail_payload)
    raw_mode = _first(root, ("gameModeName", "gameMode", "instanceType"), _first(source, ("gameMode", "instanceType"), "—"))
    mode = localize_game_mode(raw_mode)
    if mode == str(raw_mode or "").strip():
        mode = localize_instance_type(raw_mode)
    rows = _find_player_rows(root)
    players: List[Dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in rows:
        row = _player_view(raw)
        key = (row["team"], row["name"], row["hero"])
        if key in seen:
            continue
        seen.add(key)
        players.append(row)
    map_guid = _first(root, ("mapGuid",), _first(source, ("mapGuid",), ""))
    map_raw_name = _first(root, ("mapName", "map"), _first(source, ("mapName", "map"), ""))
    map_meta = resolve_map(map_guid, map_raw_name)
    return {
        "match_id": str(payload.get("match_id") or source.get("matchId") or ""),
        "map_name": map_meta["map_name"],
        "map_mode": map_meta["map_mode"],
        "map_mode_zh": map_meta["map_mode_zh"],
        "mode": mode or "—",
        "team_score": _as_int(_first(root, ("teamScore", "ourScore"), source.get("teamScore", 0))),
        "opponent_score": _as_int(_first(root, ("opponentScore", "enemyScore"), source.get("opponentScore", 0))),
        "players": players,
        "player_count": len(players),
    }
