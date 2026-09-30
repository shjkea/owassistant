# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from .errors import PlayerNotFoundError, UpstreamError
from .models import ResolvedPlayer
from .game_catalog import resolve_hero, resolve_map
from .localization import (
    localize_game_mode, localize_instance_type,
    localize_rank, localize_role,
)


def ensure_success(payload: Dict[str, Any], *, operation: str) -> Dict[str, Any]:
    code = payload.get("code")
    # 网易战绩接口成功一般是 code=0；某些响应没有 code，保留兼容。
    if code not in (None, 0, "0"):
        msg = str(payload.get("msg") or payload.get("message") or payload.get("errmsg") or "").strip()
        raise UpstreamError(msg or f"{operation}失败", code=code, payload=payload)
    return payload


def payload_data(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    data = payload.get("data", payload)
    return dict(data) if isinstance(data, Mapping) else {}


def resolve_player_payload(query: str, payload: Dict[str, Any]) -> ResolvedPlayer:
    ensure_success(payload, operation="搜索 BattleTag")
    data = payload.get("data")
    data = data if isinstance(data, dict) else {}
    customer_token = str(data.get("customerToken") or "").strip()
    if not customer_token:
        raise PlayerNotFoundError(
            f"无法查询 {query!r}。请检查 BattleTag 大小写和 # 后数字；也可能是上游暂未收录。"
        )
    return ResolvedPlayer(
        query=str(query or "").replace("＃", "#").strip(),
        full_id=str(data.get("name") or query or "").strip(),
        bnet_id=str(data.get("bnetId") or "").strip(),
        customer_token=customer_token,
        icon_url=str(data.get("icon") or "").strip(),
        raw=payload,
    )


def extract_match_entries(payload: Any, *preferred_keys: str) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(x) for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    data = payload.get("data", payload)
    if isinstance(data, list):
        return [dict(x) for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    for key in preferred_keys or ("matchList", "recentMatchList"):
        value = data.get(key)
        if isinstance(value, list):
            return [dict(x) for x in value if isinstance(x, dict)]
    return []


def merge_unique_matches(*groups: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen: set[str] = set()
    fallback_seen: set[tuple[Any, ...]] = set()
    for group in groups:
        for raw in group or []:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            match_id = str(item.get("matchId") or "").strip()
            if match_id:
                if match_id in seen:
                    continue
                seen.add(match_id)
            else:
                # 极少数上游异常条目没有 matchId；用稳定字段避免同页重复。
                key = (
                    item.get("beginTs"), item.get("mapGuid"), item.get("heroGuid"),
                    item.get("gameMode"), item.get("matchRet"),
                )
                if key in fallback_seen:
                    continue
                fallback_seen.add(key)
            result.append(item)
    result.sort(key=lambda m: _safe_int(m.get("beginTs")), reverse=True)
    return result


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _first(mapping: Mapping[str, Any], keys: Sequence[str], default: Any = None) -> Any:
    for key in keys:
        if key in mapping and mapping.get(key) not in (None, ""):
            return mapping.get(key)
    return default


def _percent(value: Any) -> Optional[float]:
    number = _safe_float(value)
    if number is None:
        return None
    if 0 <= number <= 1:
        number *= 100.0
    return number


def is_competitive_match(match: Dict[str, Any]) -> bool:
    text = " ".join(
        str(match.get(k) or "")
        for k in ("gameMode", "instanceType", "mode", "game_mode")
    ).lower()
    return "sport" in text or "ranked" in text or "competitive" in text


def normalize_match(match: Dict[str, Any]) -> Dict[str, Any]:
    """给调用方一层稳定字段，同时完整保留 raw。"""
    ret = _safe_int(match.get("matchRet"), default=-999)
    result = "win" if ret == 1 else ("draw" if ret == 0 else ("loss" if ret in (2, -1) else "unknown"))
    map_meta = resolve_map(match.get("mapGuid"), match.get("mapName") or match.get("map"))
    hero_meta = resolve_hero(match.get("heroGuid"), match.get("heroName") or match.get("hero"))
    role_type = normalize_role_type(match.get("roleType")) or hero_meta["role_type"]
    return {
        "match_id": str(match.get("matchId") or ""),
        "result": result,
        "result_code": ret,
        "competitive": is_competitive_match(match),
        "game_mode": str(match.get("gameMode") or ""),
        "game_mode_zh": localize_game_mode(match.get("gameMode")),
        "instance_type": str(match.get("instanceType") or ""),
        "instance_type_zh": localize_instance_type(match.get("instanceType")),
        "map_guid": map_meta["map_guid"],
        "map_name_raw": str(match.get("mapName") or match.get("map") or ""),
        "map_name": map_meta["map_name"],
        "map_mode": map_meta["map_mode"],
        "map_mode_zh": map_meta["map_mode_zh"],
        "hero_guid": hero_meta["hero_guid"],
        "hero_key": hero_meta["hero_key"],
        "hero_name_raw": str(match.get("heroName") or match.get("hero") or ""),
        "hero_name": hero_meta["hero_name"],
        "hero_icon": str(match.get("heroIcon") or ""),
        "role_type": role_type,
        "role_name_zh": localize_role(role_type),
        "team_score": _safe_int(match.get("teamScore")),
        "opponent_score": _safe_int(match.get("opponentScore")),
        "kills": _safe_int(match.get("kill")),
        "assists": _safe_int(match.get("assist")),
        "deaths": _safe_int(match.get("death")),
        "hero_damage": _safe_int(match.get("heroDamage")),
        "healing": _safe_int(match.get("cure")),
        "resist_damage": _safe_int(match.get("resistDamage")),
        "final_hits": _safe_int(match.get("finalHit")),
        "damage_taken": _safe_int(match.get("damageTaken")),
        "healing_taken": _safe_int(match.get("healingTaken")),
        "game_time_sec": _safe_float(_first(match, ("gameTimeSec", "gameTime", "durationSec", "duration"), None)),
        "start_time": _safe_int(match.get("startTime")) or None,
        "begin_ts": _safe_int(match.get("beginTs")),
        "season": _safe_int(match.get("_dashenSeason")) or None,
        "source_mode": str(match.get("_dashenSourceMode") or ""),
        "raw": dict(match),
    }


# ---------- 单局详情 / 全员记分板 ----------
def _walk_dicts(value: Any, *, max_nodes: int = 5000) -> List[Dict[str, Any]]:
    """深度遍历 JSON 中的 dict；用于兼容大神详情偶尔多包一层 data/result。"""
    found: List[Dict[str, Any]] = []
    stack: List[Any] = [value]
    seen: set[int] = set()
    while stack and len(found) < max_nodes:
        current = stack.pop()
        current_id = id(current)
        if current_id in seen:
            continue
        seen.add(current_id)
        if isinstance(current, Mapping):
            item = dict(current)
            found.append(item)
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return found


def _detail_root_score(data: Mapping[str, Any]) -> int:
    score = 0
    if isinstance(data.get("teammateList"), list):
        score += 50 + len(data.get("teammateList") or [])
    if isinstance(data.get("enemyList"), list):
        score += 50 + len(data.get("enemyList") or [])
    if isinstance(data.get("roundCountList"), list):
        score += 60 + len(data.get("roundCountList") or [])
    if isinstance(data.get("totalCount"), Mapping):
        score += 20
    if isinstance(data.get("heroList"), list):
        score += 12 + len(data.get("heroList") or [])
    if data.get("matchRet") is not None:
        score += 8
    if data.get("mapGuid") is not None:
        score += 6
    if data.get("teamScore") is not None:
        score += 4
    return score


def _find_nested_value(value: Any, *keys: str) -> Any:
    wanted = {str(key) for key in keys}
    for candidate in _walk_dicts(value):
        for key in wanted:
            if key in candidate and candidate.get(key) not in (None, [], {}):
                return candidate.get(key)
    return None


def extract_match_detail_root(payload: Any) -> Dict[str, Any]:
    """提取 queryMatchInfo/fight queryMatchInfo 中真正的比赛详情根节点。

    做法与 Overstats 当前实现相同思路：在整个响应树里寻找 teammate/enemy/round/hero
    数据最完整的 dict，并把几个关键嵌套列表提升到统一根节点。原始响应仍由 service 保留。
    """
    if not isinstance(payload, Mapping):
        return {}
    roots: List[Dict[str, Any]] = []
    data = payload.get("data")
    if isinstance(data, Mapping):
        roots.append(dict(data))
    roots.append(dict(payload))
    best = roots[0] if roots else {}
    best_score = _detail_root_score(best)
    for root in roots:
        for candidate in _walk_dicts(root):
            score = _detail_root_score(candidate)
            if score > best_score:
                best = candidate
                best_score = score
    merged: Dict[str, Any] = {}
    for root in roots:
        merged.update(root)
    merged.update(best)
    for canonical, keys in (
        ("teammateList", ("teammateList", "teammate_list")),
        ("enemyList", ("enemyList", "enemy_list")),
        ("roundCountList", ("roundCountList", "round_count_list")),
        ("totalCount", ("totalCount", "total_count")),
        ("nameMap", ("nameMap", "name_map")),
        ("heroList", ("heroList", "hero_list")),
    ):
        value = _find_nested_value(merged, *keys)
        if isinstance(value, (list, Mapping)):
            merged[canonical] = dict(value) if isinstance(value, Mapping) else list(value)
    return merged


def normalize_detail_player(item: Mapping[str, Any], *, team_type: str = "") -> Dict[str, Any]:
    rank_info = item.get("rankInfo") if isinstance(item.get("rankInfo"), Mapping) else {}
    hero_list = item.get("heroList") if isinstance(item.get("heroList"), list) else []
    first_hero = next((row for row in hero_list if isinstance(row, Mapping)), {})
    hero_guid = _first(item, ("heroGuid", "heroId", "hero_guid"), "") or _first(first_hero, ("heroGuid", "heroId", "hero_guid"), "")
    hero_name_raw = _first(item, ("heroName", "hero", "hero_name"), "") or _first(first_hero, ("heroName", "hero", "hero_name", "name"), "")
    # Dashen detail rows frequently omit top-level roleType.  Overstats itself
    # falls back to rankInfo.roleType/role before consulting hero metadata.
    role_raw = (
        _first(item, ("roleType", "role", "position", "roleGuid"), "")
        or _first(rank_info, ("roleType", "role", "position", "roleGuid"), "")
        or _first(first_hero, ("roleType", "role", "position", "roleGuid"), "")
    )
    hero_meta = resolve_hero(hero_guid, hero_name_raw)
    role_type = normalize_role_type(role_raw) or hero_meta["role_type"]
    return {
        "team_type": str(team_type or ""),
        "name": str(_first(item, ("name", "playerName", "battleTag", "battletag", "bnetName", "nickname"), "")).strip(),
        "bnet_id": str(_first(item, ("bnetId", "battleNetId", "bnet_id"), "")).strip(),
        "customer_token": str(_first(item, ("customerToken", "customer_token"), "")).strip(),
        "hero_guid": hero_meta["hero_guid"],
        "hero_key": hero_meta["hero_key"],
        "hero_name_raw": str(hero_name_raw or "").strip(),
        "hero_name": hero_meta["hero_name"],
        "role_type": role_type,
        "role_name_zh": localize_role(role_type),
        "kills": _safe_int(_first(item, ("kill", "kills", "eliminations"), 0)),
        "assists": _safe_int(_first(item, ("assist", "assists"), 0)),
        "deaths": _safe_int(_first(item, ("death", "deaths"), 0)),
        "hero_damage": _safe_int(_first(item, ("heroDamage", "damage", "damageDone"), 0)),
        "healing": _safe_int(_first(item, ("cure", "healing", "healingDone"), 0)),
        "resist_damage": _safe_int(_first(item, ("resistDamage", "mitigation", "damageBlocked"), 0)),
        "final_hits": _safe_int(_first(item, ("finalHit", "finalHits"), 0)),
        "damage_taken": _safe_int(_first(item, ("damageTaken", "damage_taken"), 0)),
        "healing_taken": _safe_int(_first(item, ("healingTaken", "healing_taken"), 0)),
        "target_competing_time": _safe_float(_first(item, ("targetCompetingTime", "objectiveTime"), None)),
        "rank_info": normalize_rank_info(rank_info),
        "friend_bnet_ids": list(item.get("friendBnetIds") or []) if isinstance(item.get("friendBnetIds"), list) else [],
        "hero_list": [dict(row) for row in hero_list if isinstance(row, Mapping)],
        "raw": dict(item),
    }


def extract_detail_players(payload_or_root: Any) -> List[Dict[str, Any]]:
    root = extract_match_detail_root(payload_or_root)
    rows: List[Dict[str, Any]] = []
    for key, team_type in (("teammateList", "teammate"), ("enemyList", "enemy")):
        values = root.get(key)
        if isinstance(values, list):
            rows.extend(normalize_detail_player(row, team_type=team_type) for row in values if isinstance(row, Mapping))
    if rows:
        return rows
    # 角斗等结构可能按 roundCountList 存放队伍；保留每轮玩家并用 bnet/name 去重。
    seen: set[tuple[str, str, str]] = set()
    for round_row in root.get("roundCountList") or []:
        if not isinstance(round_row, Mapping):
            continue
        for key, team_type in (("teammateList", "teammate"), ("enemyList", "enemy")):
            for player in round_row.get(key) or []:
                if not isinstance(player, Mapping):
                    continue
                normalized = normalize_detail_player(player, team_type=team_type)
                dedup = (normalized.get("bnet_id") or "", normalized.get("name") or "", team_type)
                if dedup in seen:
                    continue
                seen.add(dedup)
                rows.append(normalized)
    return rows


# ---------- 竞技职责 / 段位 ----------
_ROLE_ALIASES = {
    "tank": "tank",
    "重装": "tank",
    "坦克": "tank",
    "972777519512029264": "tank",
    "dps": "dps",
    "damage": "dps",
    "输出": "dps",
    "972777519512029262": "dps",
    "healer": "healer",
    "support": "healer",
    "辅助": "healer",
    "支援": "healer",
    "972777519512029265": "healer",
    "open": "open",
    "开放": "open",
}


def normalize_role_type(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return _ROLE_ALIASES.get(raw, raw)


_UNRANKED_RANK_NAMES = {
    "", "0", "none", "null", "n/a", "na", "-", "—",
    "unranked", "placement", "placements", "placing",
    "未定级", "定级中",
}


def _has_explicit_visible_rank(rank_name: str) -> bool:
    """Only an explicit tier name proves that a player is ranked.

    Dashen can expose an internal rankScore/MMR for players who have not completed
    placements.  That number must never be converted back into Bronze/Silver/etc.
    """
    raw = str(rank_name or "").strip()
    if not raw:
        return False
    return raw.lower() not in _UNRANKED_RANK_NAMES


def normalize_rank_info(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, Mapping) or not value:
        return None
    rank_name_raw = str(_first(value, ("rank_name", "rankName", "tierName", "gradeName", "rank"), "")).strip()
    sub_tier_raw = _safe_int(_first(value, ("rank_sub_tier", "rankSubTier", "rankLevel", "subRank"), 0)) or None
    rank_score = _safe_int(_first(value, ("rankScore", "rank_score", "score", "mmr"), 0)) or None
    rank_level = _safe_int(_first(value, ("rank_level", "rankLevelCode", "tierLevel", "level"), 0)) or None

    is_ranked = _has_explicit_visible_rank(rank_name_raw)
    if is_ranked:
        rank_name_zh = localize_rank(rank_name_raw)
        sub_tier = sub_tier_raw
        display_rank = f"{rank_name_zh} {sub_tier}" if sub_tier else rank_name_zh
        rank_status = "ranked"
    else:
        # A numeric score without a visible tier is an internal/hidden MMR signal,
        # not proof of Bronze.  Keep the score for diagnostics but never expose it
        # as a visible rank.
        rank_name_zh = "未定级"
        sub_tier = None
        display_rank = "未定级"
        rank_status = "unranked_hidden_mmr" if rank_score is not None else "unranked"

    return {
        "rank_name_raw": rank_name_raw,
        "rank_name": rank_name_zh,
        "rank_name_zh": rank_name_zh,
        "rank_sub_tier": sub_tier,
        "rank_sub_tier_raw": sub_tier_raw,
        "rank_score": rank_score,
        "rank_level": rank_level,
        "is_ranked": is_ranked,
        "rank_status": rank_status,
        "display_rank": display_rank,
        "hidden_mmr": rank_score if not is_ranked else None,
        "rank_score_is_display_rank": bool(is_ranked),
        "raw": dict(value),
    }


def normalize_competitive_role(item: Mapping[str, Any]) -> Dict[str, Any]:
    current_raw: Any = None
    for key in ("lastRankInfo", "currentRankInfo", "rankInfo"):
        if isinstance(item.get(key), Mapping) and item.get(key):
            current_raw = item.get(key)
            break
    if current_raw is None:
        current_raw = item

    peak_raw: Any = None
    for key in (
        "maxRankInfo", "bestRankInfo", "highestRankInfo", "peakRankInfo",
        "seasonMaxRankInfo", "topRankInfo",
    ):
        if isinstance(item.get(key), Mapping) and item.get(key):
            peak_raw = item.get(key)
            break

    match_sum = _safe_int(_first(item, ("matchSum", "matchCount", "gameCount", "games", "totalCount"), 0))
    wins = _safe_int(_first(item, ("winSum", "winCount", "wins", "win"), 0))
    losses = _safe_int(_first(item, ("loseSum", "loseCount", "losses", "lose"), 0))
    win_rate = _percent(_first(item, ("winRate", "winningRate", "winPercent", "victoryRate"), None))
    if win_rate is None and match_sum > 0 and wins > 0:
        win_rate = wins * 100.0 / match_sum
    role_type = normalize_role_type(_first(item, ("roleType", "role", "position"), ""))
    return {
        "role_type": role_type,
        "role_name_zh": localize_role(role_type),
        "match_sum": match_sum,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "current": normalize_rank_info(current_raw),
        "peak": normalize_rank_info(peak_raw),
        "raw": dict(item),
    }


def _extract_role_rows(payload: Any, key: str) -> List[Dict[str, Any]]:
    data = payload_data(payload)
    rows = data.get(key)
    if isinstance(rows, Mapping):
        rows = list(rows.values())
    if not isinstance(rows, list):
        return []
    return [normalize_competitive_role(row) for row in rows if isinstance(row, Mapping)]


def extract_competitive_roles(payload: Any) -> List[Dict[str, Any]]:
    return _extract_role_rows(payload, "guideCountData")


def extract_stadium_roles(payload: Any) -> List[Dict[str, Any]]:
    """角斗/体育场赛季职责数据（roleTypeCountData）。"""
    return _extract_role_rows(payload, "roleTypeCountData")


def extract_stadium_stats(payload: Any) -> Dict[str, Any]:
    """保留 fight/queryCount 的职责、英雄使用摘要和原始字段。"""
    data = payload_data(payload)
    hero_rows = data.get("heroUseSummaryList")
    hero_rows = [dict(row) for row in hero_rows if isinstance(row, Mapping)] if isinstance(hero_rows, list) else []
    return {
        "roles": extract_stadium_roles(payload),
        "hero_use_summary_list": hero_rows,
        "heroes": [normalize_hero_stat(row, source="heroUseSummaryList") for row in hero_rows],
        "raw": payload,
    }


# ---------- 英雄使用数据 ----------
_HERO_LIST_KEYS = (
    "presetsHeroUseSummaryList",
    "v6HeroUseSummaryList",
    "openHeroUseSummaryList",
    "presetsHeroList",
    "openHeroList",
)


def normalize_hero_stat(item: Mapping[str, Any], *, source: str = "") -> Dict[str, Any]:
    hero_guid = _first(item, ("heroGuid", "hero_guid", "heroId", "heroID"), "")
    hero_name_raw = _first(item, ("heroName", "hero_name", "name"), "")
    hero_meta = resolve_hero(hero_guid, hero_name_raw)
    role_type = normalize_role_type(_first(item, ("roleType", "heroRole", "role"), "")) or hero_meta["role_type"]
    games = _safe_int(_first(item, ("matchSum", "matchCount", "gameCount", "games", "count"), 0))
    wins = _safe_int(_first(item, ("winSum", "winCount", "wins", "win"), 0))
    win_rate = _percent(_first(item, ("winRate", "winningRate", "winPercent", "victoryRate"), None))
    if win_rate is None and games > 0 and wins > 0:
        win_rate = wins * 100.0 / games
    return {
        "source": source,
        "hero_guid": hero_meta["hero_guid"],
        "hero_key": hero_meta["hero_key"],
        "hero_name_raw": str(hero_name_raw or "").strip(),
        "hero_name": hero_meta["hero_name"],
        "role_type": role_type,
        "role_name_zh": localize_role(role_type),
        "icon_url": str(_first(item, ("heroIcon", "icon", "iconUrl", "avatar"), "")).strip(),
        "play_time": _safe_float(_first(item, ("gameTime", "playTime", "useTime", "time", "duration"), None)),
        "match_sum": games,
        "wins": wins,
        "win_rate": win_rate,
        "kills": _safe_int(_first(item, ("kill", "kills", "eliminations"), 0)),
        "assists": _safe_int(_first(item, ("assist", "assists"), 0)),
        "deaths": _safe_int(_first(item, ("death", "deaths"), 0)),
        "hero_damage": _safe_int(_first(item, ("heroDamage", "damage", "damageDone"), 0)),
        "healing": _safe_int(_first(item, ("cure", "healing", "healingDone"), 0)),
        "resist_damage": _safe_int(_first(item, ("resistDamage", "mitigation", "damageBlocked"), 0)),
        "raw": dict(item),
    }


def extract_hero_stats(payload: Any) -> Dict[str, Any]:
    data = payload_data(payload)
    lists: Dict[str, List[Dict[str, Any]]] = {}
    combined: List[Dict[str, Any]] = []
    for key in _HERO_LIST_KEYS:
        value = data.get(key)
        rows = [dict(x) for x in value if isinstance(x, Mapping)] if isinstance(value, list) else []
        lists[key] = rows
        combined.extend(normalize_hero_stat(row, source=key) for row in rows)
    return {
        "lists": lists,
        "heroes": combined,
        "summary": {
            "presets": data.get("presetsSummaryData") if isinstance(data.get("presetsSummaryData"), Mapping) else {},
            "open": data.get("openSummaryData") if isinstance(data.get("openSummaryData"), Mapping) else {},
            "recent_match_count": data.get("recentMatchCount"),
            "game_action": data.get("gameAction") if isinstance(data.get("gameAction"), Mapping) else {},
        },
    }


# ---------- 榜单 ----------
def extract_leaderboard_entries(payload: Any) -> List[Dict[str, Any]]:
    """尽量宽松地抽取省榜/英雄榜条目，原始 payload 始终由 service 同时返回。"""
    data = payload_data(payload)
    candidates: List[Any] = []
    for key in (
        "rankList", "itemList", "list", "rows", "userProvinceRankList", "heroBillboardList",
        "billboardList", "dataList", "resultList",
    ):
        value = data.get(key)
        if isinstance(value, list):
            candidates.extend(value)
    if not candidates and isinstance(data.get("groups"), list):
        for group in data["groups"]:
            if isinstance(group, Mapping) and isinstance(group.get("entries"), list):
                candidates.extend(group.get("entries") or [])
    out: List[Dict[str, Any]] = []
    for row in candidates:
        if not isinstance(row, Mapping):
            continue
        rank_info_raw = row.get("rankInfo") if isinstance(row.get("rankInfo"), Mapping) else {}
        rank_score = _safe_int(_first(row, ("rankScore", "rank_score", "rankedLevel", "ranked_level"), 0))
        if not rank_score and rank_info_raw:
            rank_score = _safe_int(_first(rank_info_raw, ("rankScore", "rank_score", "score"), 0))
        out.append({
            "rank_num": _safe_int(_first(row, ("rankNum", "rank_num", "rank", "ranking"), 0)) or None,
            "user_name": str(_first(row, ("userName", "user_name", "name", "nickName", "nickname"), "")).strip(),
            "match_sum": _safe_int(_first(row, ("matchSum", "match_sum", "matchCount", "games"), 0)),
            "win_rate": _percent(_first(row, ("winRate", "win_rate", "winningRate"), None)),
            "wins": _safe_int(_first(row, ("wins", "winCount", "winSum"), 0)),
            "rank_score": rank_score or None,
            "rank_info": dict(rank_info_raw),
            "customer_token": str(_first(row, ("customerToken", "customer_token", "token"), "")).strip(),
            "raw": dict(row),
        })
    return out
