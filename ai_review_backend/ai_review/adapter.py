from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .errors import InvalidMatchDataError
from .models import HeroSummary, MatchReviewContext, MatchSummary, PlayerSummary


ROLE_NAMES = {
    "tank": "重装",
    "dps": "输出",
    "damage": "输出",
    "healer": "支援",
    "support": "支援",
    "open": "开放职责",
}


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _pick(row: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _optional_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_team(value: Any) -> str:
    token = _text(value).lower()
    if any(word in token for word in ("enemy", "opponent", "red", "敌")):
        return "enemy"
    return "friendly"


def _normalize_role(value: Any) -> Tuple[str, str]:
    token = _text(value).lower()
    canonical = {
        "damage": "dps",
        "support": "healer",
    }.get(token, token)
    return canonical, ROLE_NAMES.get(canonical, _text(value))


def _normalize_rank(value: Any) -> str:
    row = _mapping(value)
    if not row:
        return ""
    display = _text(_pick(row, "display", "display_rank", "displayRank"))
    if display:
        return display
    ranked = row.get("is_ranked")
    if ranked is False:
        return "未定级"
    name = _text(_pick(row, "rank_name_zh", "rankNameZh", "rank_name", "rankName"))
    tier = _optional_int(_pick(row, "rank_sub_tier", "rankSubTier", "sub_tier", "subTier"))
    if name and tier:
        return f"{name} {tier}"
    return name


def _hero_from_row(row: Mapping[str, Any]) -> Optional[HeroSummary]:
    raw = _mapping(row.get("raw"))
    merged = dict(raw)
    merged.update({k: v for k, v in row.items() if k != "raw"})
    guid = _text(_pick(merged, "hero_guid", "heroGuid", "heroId", "heroID", "guid"))
    name = _text(_pick(merged, "hero_name", "heroName", "name"))
    time_value = _pick(
        merged,
        "play_time", "playTime", "gameTime", "game_time", "useTime", "heroTime",
        "duration", "time", "totalTime", "playTimeSec",
    )
    if time_value is None:
        stat_map = _mapping(_pick(merged, "stat_map", "statMap"))
        time_value = _pick(
            stat_map,
            "play_time", "playTime", "gameTime", "game_time", "useTime", "heroTime",
            "duration", "time", "totalTime", "playTimeSec",
        )
    play_time = _optional_float(time_value)
    if play_time is not None and play_time <= 0:
        play_time = None
    if not guid and not name:
        return None
    return HeroSummary(hero_guid=guid, hero_name=name, play_time=play_time)


def _heroes_from_sources(*sources: Any) -> List[HeroSummary]:
    out: List[HeroSummary] = []
    seen: set[str] = set()
    for source in sources:
        rows: Iterable[Any]
        if isinstance(source, Mapping):
            rows = [source]
        elif isinstance(source, (list, tuple)):
            rows = source
        else:
            continue
        for item in rows:
            if not isinstance(item, Mapping):
                continue
            hero = _hero_from_row(item)
            if not hero:
                continue
            key = hero.hero_guid or hero.hero_name.casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(hero)
    if any(hero.play_time for hero in out):
        out.sort(key=lambda hero: -(hero.play_time or 0.0))
    return out


def _identity_tokens(row: Mapping[str, Any]) -> Dict[str, str]:
    return {
        "customer_token": _text(_pick(row, "customer_token", "customerToken")),
        "bnet_id": _text(_pick(row, "bnet_id", "bnetId", "battleNetId")),
        "battle_tag": _text(_pick(row, "battle_tag", "battleTag", "battletag", "name")),
    }


def _self_identity_tokens(self_identity: Mapping[str, Any] | str | None) -> Dict[str, str]:
    if isinstance(self_identity, str):
        return {"customer_token": "", "bnet_id": "", "battle_tag": _text(self_identity)}
    if isinstance(self_identity, Mapping):
        return _identity_tokens(self_identity)
    return {"customer_token": "", "bnet_id": "", "battle_tag": ""}


# 与主项目 ui/owstats_data.py 的 _self_identity / is_self_player 保持同一套字段别名与规则。
_BATTLE_TAG_KEYS = ("battle_tag", "battleTag", "battletag", "full_id", "name", "query")


def _is_full_battle_tag(value: str) -> bool:
    """完整 BattleTag：包含 '#'，且 '#' 两侧都非空（例如 名字#12345）。"""
    if "#" not in value:
        return False
    name, _, tag = value.rpartition("#")
    return bool(name.strip()) and bool(tag.strip())


def _full_battle_tag(row: Mapping[str, Any]) -> str:
    """按别名顺序取第一个「完整 BattleTag」；只有昵称（无 #xxxx）时返回空。"""
    for key in _BATTLE_TAG_KEYS:
        value = _text(row.get(key))
        if value and _is_full_battle_tag(value):
            return value
    return ""


def _same_identity(row: Mapping[str, Any], wanted: Mapping[str, str]) -> bool:
    """本人判定：只比较完整 BattleTag（大小写不敏感）。

    与主项目 is_self_player() 一致：customer_token / bnet_id / player_id 等账号 ID
    一律不参与判定，也不做前缀匹配或忽略 '#' 后缀；任一侧缺完整 BattleTag 即非本人。
    """
    got = _full_battle_tag(row)
    want = _full_battle_tag(wanted)
    return bool(got and want and got.casefold() == want.casefold())


def _build_extra_index(rows: Sequence[Any]) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for value in rows:
        row = _mapping(value)
        if not row:
            continue
        ids = _identity_tokens(row)
        for token in (ids["customer_token"], ids["bnet_id"], ids["battle_tag"], _text(row.get("name"))):
            if token:
                index.setdefault(token.casefold(), row)
    return index


def _find_extra(row: Mapping[str, Any], index: Mapping[str, Dict[str, Any]]) -> Dict[str, Any]:
    ids = _identity_tokens(row)
    for token in (ids["customer_token"], ids["bnet_id"], ids["battle_tag"], _text(row.get("name"))):
        if token and token.casefold() in index:
            return dict(index[token.casefold()])
    return {}


def _merge_player(row: Mapping[str, Any], extra: Mapping[str, Any], player_id: str, wanted_self: Mapping[str, str]) -> Tuple[PlayerSummary, str, List[str]]:
    scoreboard_extra = _mapping(extra.get("scoreboard"))
    identity = _identity_tokens(extra)
    identity.update({k: v for k, v in _identity_tokens(row).items() if v})

    role_raw = _pick(row, "role_type", "roleType", "role")
    if role_raw is None:
        role_raw = _pick(scoreboard_extra, "role_type", "roleType", "role")
    if role_raw is None:
        role_raw = _pick(extra, "role_type", "roleType", "role")
    role_type, role_name = _normalize_role(role_raw)
    role_name = _text(_pick(row, "role_name", "role_name_zh", "roleNameZh")) or role_name

    heroes = _heroes_from_sources(
        extra.get("heroes"), extra.get("hero_list"), extra.get("heroList"),
        row.get("heroes"), row.get("hero_list"), row.get("heroList"),
        scoreboard_extra.get("heroes"), scoreboard_extra.get("hero_list"), scoreboard_extra.get("heroList"),
    )
    if not heroes:
        hero = _hero_from_row({
            "hero_guid": _pick(row, "hero_guid", "heroGuid") or _pick(scoreboard_extra, "hero_guid", "heroGuid"),
            "hero_name": _pick(row, "hero_name", "heroName") or _pick(scoreboard_extra, "hero_name", "heroName"),
        })
        if hero:
            heroes = [hero]

    team = _normalize_team(_pick(row, "team_type", "teamType", "team") or _pick(extra, "team_type", "teamType", "team"))
    battle_tag = identity["battle_tag"] or _text(_pick(row, "name")) or _text(_pick(extra, "name")) or "未知玩家"
    rank = _normalize_rank(_pick(row, "rank", "rank_info", "rankInfo") or _pick(extra, "rank", "rank_info", "rankInfo"))

    def stat(*keys: str) -> int:
        value = _pick(row, *keys)
        if value is None:
            value = _pick(scoreboard_extra, *keys)
        if value is None:
            value = _pick(extra, *keys)
        return _int(value)

    player = PlayerSummary(
        player_id=player_id,
        battle_tag=battle_tag,
        team=team,
        role_type=role_type,
        role_name=role_name,
        heroes=heroes,
        rank=rank,
        kills=stat("kills", "kill"),
        deaths=stat("deaths", "death"),
        assists=stat("assists", "assist"),
        hero_damage=stat("hero_damage", "heroDamage", "damage"),
        healing=stat("healing", "cure", "heal"),
        resist_damage=stat("resist_damage", "resistDamage", "mitigation"),
        damage_taken=stat("damage_taken", "damageTaken"),
        healing_taken=stat("healing_taken", "healingTaken"),
        final_hits=stat("final_hits", "finalHit"),
        is_self=_same_identity(row, wanted_self) or _same_identity(extra, wanted_self),
    )
    bnet_id = identity["bnet_id"]
    friends_raw = _pick(row, "friend_bnet_ids", "friendBnetIds")
    if friends_raw is None:
        friends_raw = _pick(scoreboard_extra, "friend_bnet_ids", "friendBnetIds")
    if friends_raw is None:
        friends_raw = _pick(extra, "friend_bnet_ids", "friendBnetIds")
    friends = [_text(v) for v in _list(friends_raw) if _text(v)]
    return player, bnet_id, friends


def _assign_party_ids(players: List[PlayerSummary], bnet_ids: List[str], friend_lists: List[List[str]]) -> None:
    n = len(players)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    by_bnet = {bid: i for i, bid in enumerate(bnet_ids) if bid}
    for i, friends in enumerate(friend_lists):
        for friend in friends:
            j = by_bnet.get(friend)
            if j is not None and players[i].team == players[j].team:
                union(i, j)

    groups: Dict[int, List[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    party_no = 0
    for indexes in groups.values():
        if len(indexes) < 2:
            continue
        party_no += 1
        party_id = f"party-{party_no}"
        for i in indexes:
            players[i].party_id = party_id


def _result_from(value: Any) -> str:
    token = _text(value).lower()
    if token in {"win", "victory", "胜利", "1"}:
        return "win"
    if token in {"loss", "lose", "defeat", "失败", "2"}:
        return "loss"
    if token in {"draw", "tie", "平局", "0"}:
        return "draw"
    return token or "unknown"


def _ban_list(value: Any) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for item in _list(value):
        row = _mapping(item)
        if not row:
            continue
        out.append({
            "hero_guid": _text(_pick(row, "hero_guid", "heroGuid", "guid")),
            "hero_name": _text(_pick(row, "hero_name", "heroName", "name")),
        })
    return out


def _build_match_summary(detail: Mapping[str, Any], match_meta: Mapping[str, Any] | None) -> MatchSummary:
    meta = _mapping(match_meta)
    root = _mapping(detail.get("detail_root"))
    raw = _mapping(meta.get("raw"))

    def m(*keys: str) -> Any:
        for source in (meta, raw, root, detail):
            value = _pick(source, *keys)
            if value is not None:
                return value
        return None

    bans = _mapping(detail.get("bans"))
    friendly_bans = _ban_list(bans.get("friendly"))
    enemy_bans = _ban_list(bans.get("enemy"))
    if not friendly_bans:
        friendly_bans = _ban_list(m("teammateBanHeroList", "teamBanHeroList", "friendlyBanHeroList"))
    if not enemy_bans:
        enemy_bans = _ban_list(m("enemyBanHeroList", "opponentBanHeroList"))

    result_value = m("result")
    if result_value is None:
        result_value = m("matchRet", "match_ret")

    return MatchSummary(
        match_id=_text(m("match_id", "matchId")),
        result=_result_from(result_value),
        map_name=_text(m("map_name", "mapName", "map_name_raw")),
        map_mode=_text(m("map_mode_zh", "mapModeZh", "map_mode", "mapMode")),
        game_mode=_text(m("game_mode_zh", "gameModeZh", "game_mode", "gameMode")),
        team_score=_optional_int(m("team_score", "teamScore")),
        opponent_score=_optional_int(m("opponent_score", "opponentScore")),
        duration_seconds=_optional_float(m("game_time_sec", "gameTimeSec", "duration_seconds", "duration")),
        begin_ts=_optional_int(m("begin_ts", "beginTs")),
        friendly_bans=friendly_bans,
        enemy_bans=enemy_bans,
    )


def build_review_context(
    detail: Mapping[str, Any],
    *,
    match_meta: Mapping[str, Any] | None = None,
    self_identity: Mapping[str, Any] | str | None = None,
) -> MatchReviewContext:
    """Adapt the project's existing match detail into a compact AI-review context.

    Expected production input is OWStatsService.match_all_players_detail().  The adapter is
    intentionally tolerant of snake_case/camelCase aliases because the project currently
    combines normalized scoreboard rows with per-player detail rows.
    """
    if not isinstance(detail, Mapping):
        raise InvalidMatchDataError("detail 必须是 match_all_players_detail() 返回的字典")

    scoreboard = _list(detail.get("scoreboard_players"))
    extras = _list(detail.get("players"))
    if not scoreboard:
        # 离线/后续调用方若已经传入统一玩家列表，也可以直接适配。
        scoreboard = extras
    if not scoreboard:
        raise InvalidMatchDataError("对局详情中没有可分析的玩家数据")

    extra_index = _build_extra_index(extras)
    wanted_self = _self_identity_tokens(self_identity)
    if not any(wanted_self.values()):
        fallback = detail.get("self_identity")
        wanted_self = _self_identity_tokens(fallback if isinstance(fallback, (str, Mapping)) else None)

    players: List[PlayerSummary] = []
    bnet_ids: List[str] = []
    friend_lists: List[List[str]] = []
    for i, raw_value in enumerate(scoreboard, start=1):
        row = _mapping(raw_value)
        if not row:
            continue
        extra = _find_extra(row, extra_index)
        player, bnet_id, friends = _merge_player(row, extra, f"p{i:02d}", wanted_self)
        players.append(player)
        bnet_ids.append(bnet_id)
        friend_lists.append(friends)

    if not players:
        raise InvalidMatchDataError("玩家数据存在但无法归一化")

    _assign_party_ids(players, bnet_ids, friend_lists)

    warnings: List[str] = []
    # 不对总人数做 5v5/6v6 假设。标准模式可能是 10 人或 12 人，
    # 以后也可能出现其他人数；只有上游明确告诉我们“应有多少人/是否完整”时才警告。
    expected_count = 0
    for key in ("player_count", "expected_player_count", "total_player_count"):
        try:
            value = int(detail.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            expected_count = value
            break
    completeness = detail.get("all_players_complete")
    if completeness is False:
        if expected_count > 0:
            warnings.append(f"当前仅获得 {len(players)}/{expected_count} 名玩家数据，整局比较可能不完整。")
        else:
            warnings.append(f"当前获得 {len(players)} 名玩家数据，但上游标记全员详情不完整。")
    elif expected_count > 0 and len(players) < expected_count:
        warnings.append(f"当前仅获得 {len(players)}/{expected_count} 名玩家数据，整局比较可能不完整。")
    if not any(p.is_self for p in players):
        warnings.append("未能从当前数据识别本人；默认“本人”分析需要前端传入当前查询账号身份。")
    if any(not p.heroes for p in players):
        warnings.append("部分玩家缺少英雄信息。")

    return MatchReviewContext(
        match=_build_match_summary(detail, match_meta),
        players=players,
        data_warnings=warnings,
    )
