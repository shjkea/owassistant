# -*- coding: utf-8 -*-
"""国服战绩页的数据适配层（无 Qt 依赖，便于单独测试）。

原则：
- 赛季号全部来自后端（``season_status()`` / 各查询结果里的 ``current_season``），
  本模块不按日期推算，也不保存任何赛季号常量。
- 段位一律使用后端 ``display_rank / is_ranked / rank_status``，不用 rankScore 反推。
- 中文展示名一律使用后端已本地化字段，不在前端维护第二套翻译表。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

PRESET_ROLES: Tuple[str, ...] = ("tank", "dps", "healer")
ROLE_FALLBACK_NAMES = {"tank": "重装", "dps": "输出", "healer": "支援", "open": "开放职责"}
UNRANKED_TEXT = "未定级"

# 比赛筛选：竞技 UI 明确区分「预设职责」与「开放职责」。
# 注意：这只影响竞技 UI 的分类展示；「近期状态」的最近 N 场统计仍然把
# 预设竞技 + 开放竞技 + 快速合并在一起，两者不要混淆。
MATCH_FILTERS: Tuple[Tuple[str, str], ...] = (
    ("all", "全部"),
    ("sport", "全部竞技"),
    ("sport_preset", "预设职责"),
    ("sport_open", "开放职责"),
    ("leisure", "快速"),
    ("SportFight", "角斗竞技"),
    ("fight_quick", "角斗快速"),
)

# 预设职责竞技的二级筛选（全部 / 重装 / 输出 / 支援）。
PRESET_ROLE_FILTERS: Tuple[Tuple[str, str], ...] = (
    ("all", "全部"),
    ("tank", "重装"),
    ("dps", "输出"),
    ("healer", "支援"),
)

# 近期状态（最近 N 场）：只统计标准竞技（预设 + 开放）与快速比赛。
RECENT_FORM_LIMIT = 20
RECENT_FORM_FILTER_KEYS: Tuple[str, ...] = ("sport", "leisure")
# 明确排除：自定义 / 训练 / 街机 / 创意工坊 / 角斗等非竞技非快速模式。
# 这里只是在后端已解析好的模式字段上再兜一层，不重写第二套模式识别逻辑。
_RECENT_FORM_EXCLUDED: Tuple[str, ...] = (
    "custom", "自定义", "training", "practice", "tutorial", "训练", "练习",
    "arcade", "街机", "workshop", "创意工坊", "brawl", "lucioball",
    "deathmatch", "死斗", "elimination", "fight", "stadium", "角斗",
)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def role_display_name(role_type: Any, zh: Any = "") -> str:
    text = str(zh or "").strip()
    if text:
        return text
    return ROLE_FALLBACK_NAMES.get(str(role_type or "").strip().lower(), str(role_type or "") or "—")


# 开放职责队列里没有「玩家的预设职责」这一概念：上游仍可能按本局英雄给出
# tank / dps / healer，但界面上必须统一显示 flex（开放职责）。
OPEN_QUEUE_ROLE_TYPE = "open"
OPEN_QUEUE_ROLE_NAME = ROLE_FALLBACK_NAMES["open"]


def is_open_queue(queue_kind: Any) -> bool:
    """是否开放职责队列。只接受已解析好的 queue_kind，不做二次模式识别。"""
    return str(queue_kind or "").strip().lower() == "open"


def display_role(role_type: Any, role_name: Any = "", queue_kind: Any = "") -> Tuple[str, str]:
    """返回界面应展示的 (职责键, 职责名)。

    - 开放职责队列 → 固定 ``("open", "开放职责")``，由素材层映射到 flex.png；
    - 其它（预设职责 / 快速等）→ 原样使用后端 role_type / role_name；
    - 职责缺失时保持为空，由控件显示既有的未知状态，绝不退回 flex。
    """
    if is_open_queue(queue_kind):
        return OPEN_QUEUE_ROLE_TYPE, OPEN_QUEUE_ROLE_NAME
    return str(role_type or "").strip().lower(), str(role_name or "").strip()


# --------------------------------------------------------------------------- 英雄列表
# 一局可能使用多个英雄。后端已经提供 hero_list / heroes；这里只做归一化和排序，
# 不发任何新请求，也不修改后端抓取逻辑。
_HERO_GUID_KEYS = ("hero_guid", "heroGuid", "heroId", "heroID", "guid")
_HERO_NAME_KEYS = ("hero_name", "heroName", "name")
_HERO_NAME_RAW_KEYS = ("hero_name_raw", "heroName", "hero", "name")
# 使用时间：大神不同接口字段名不统一，statMap 里也可能出现。
_HERO_TIME_KEYS = (
    "play_time", "playTime", "gameTime", "game_time", "useTime", "heroTime",
    "duration", "time", "totalTime", "playTimeSec",
)


def _pick(row: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _hero_play_time(row: Mapping[str, Any]) -> Optional[float]:
    value = _pick(row, _HERO_TIME_KEYS)
    if value is None:
        stat_map = row.get("stat_map") if isinstance(row.get("stat_map"), Mapping) else row.get("statMap")
        if isinstance(stat_map, Mapping):
            value = _pick(stat_map, _HERO_TIME_KEYS)
    number = _float(value)
    return number if number is not None and number > 0 else None


def _hero_entry(row: Mapping[str, Any]) -> Dict[str, Any]:
    nested = row.get("raw") if isinstance(row.get("raw"), Mapping) else {}
    merged: Dict[str, Any] = dict(nested)
    merged.update({k: v for k, v in row.items() if k != "raw"})
    return {
        "hero_guid": str(_pick(merged, _HERO_GUID_KEYS) or "").strip(),
        "hero_name": str(_pick(merged, _HERO_NAME_KEYS) or "").strip(),
        "hero_name_raw": str(_pick(merged, _HERO_NAME_RAW_KEYS) or "").strip(),
        "play_time": _hero_play_time(merged),
    }


def hero_entries(*sources: Any) -> List[Dict[str, Any]]:
    """把后端多种英雄列表结构合并成有序的英雄条目。

    - 有可靠使用时间时按使用时间降序，用得最多的作为主英雄；
    - 没有可靠时间时保持后端原始顺序；
    - 按 guid/名称去重，避免同一英雄出现两次。
    """
    entries: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for source in sources:
        rows: List[Any]
        if isinstance(source, Mapping):
            rows = [source]
        elif isinstance(source, (list, tuple)):
            rows = list(source)
        else:
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            entry = _hero_entry(row)
            token = entry["hero_guid"] or entry["hero_name"] or entry["hero_name_raw"]
            if not token or token in seen:
                continue
            seen.add(token)
            entries.append(entry)
    if any(e["play_time"] for e in entries):
        # 只有确实拿到使用时间时才排序；无时间的条目沉到末尾但保持相对顺序。
        entries.sort(key=lambda e: -(e["play_time"] or 0.0))
    return entries


def hero_entries_from_match(row: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """逐场记录的英雄列表：优先 raw 里的 heroList，退回单英雄字段。"""
    raw = row.get("raw") if isinstance(row.get("raw"), Mapping) else {}
    entries = hero_entries(
        row.get("heroes"),
        row.get("hero_list"),
        raw.get("heroList"),
        raw.get("hero_list"),
    )
    if entries:
        return entries
    single = {
        "hero_guid": str(row.get("hero_guid") or "").strip(),
        "hero_name": str(row.get("hero_name") or "").strip(),
        "hero_name_raw": str(row.get("hero_name_raw") or "").strip(),
    }
    if single["hero_guid"] or single["hero_name"] or single["hero_name_raw"]:
        return [{**single, "play_time": None}]
    return []


# --------------------------------------------------------------------------- 英雄禁用
# 详情根节点（service 已返回的 detail_root）里现成的 Ban 字段，只解析不抓取：
# - 真实大神 queryMatchInfo：teamBanHeroGuids / enemyBanHeroGuids（heroGuid 字符串列表）；
# - 兼容 ui_preview 离线数据：teammateBanHeroList / enemyBanHeroList（{heroGuid, heroName}）。
# "team" 与 teammateList 同侧，即当前查询账号所在队伍 = 我方。
_FRIENDLY_BAN_KEYS = ("teamBanHeroGuids", "teammateBanHeroGuids", "teammateBanHeroList", "teamBanHeroList")
_ENEMY_BAN_KEYS = ("enemyBanHeroGuids", "enemyBanHeroList")


def _ban_list(root: Mapping[str, Any], keys: Sequence[str]) -> Optional[List[Dict[str, Any]]]:
    """返回禁用英雄条目；所有候选字段都不存在时返回 None（区别于「存在但为空」）。"""
    found = False
    entries: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for key in keys:
        if key not in root:
            continue
        found = True
        values = root.get(key)
        if not isinstance(values, (list, tuple)):
            continue
        for value in values:
            if isinstance(value, Mapping):
                entry = _hero_entry(value)
            else:
                guid = str(value if value is not None else "").strip()
                entry = {"hero_guid": guid, "hero_name": "", "hero_name_raw": "", "play_time": None}
            token = entry["hero_guid"] or entry["hero_name"] or entry["hero_name_raw"]
            if not token or token in seen:
                continue
            seen.add(token)
            entries.append(entry)
        if entries:
            break
    return entries if found else None


def detail_ban_heroes(
    detail_root: Any,
) -> Tuple[Optional[List[Dict[str, Any]]], Optional[List[Dict[str, Any]]]]:
    """(我方禁用, 敌方禁用)。某一方字段完全缺失时对应位置为 None。"""
    root = _dict(detail_root)
    return _ban_list(root, _FRIENDLY_BAN_KEYS), _ban_list(root, _ENEMY_BAN_KEYS)


# --------------------------------------------------------------------------- 赛季
def season_labels(current_season: Optional[int], count: int = 3) -> List[int]:
    """由后端当前赛季推出要展示的赛季列表（降序）。没有赛季信息时返回空列表。"""
    current = _int(current_season, 0)
    if current <= 0:
        return []
    count = max(1, int(count))
    return [s for s in range(current, current - count, -1) if s >= 1]


def season_text(season: Any) -> str:
    value = _int(season, 0)
    return f"S{value}" if value > 0 else "—"


# --------------------------------------------------------------------------- 段位
def rank_view(rank_info: Any) -> Dict[str, Any]:
    """把后端 rank_info 转成展示用结构；未定级绝不显示成青铜。"""
    info = _dict(rank_info)
    if not info:
        return {
            "display": UNRANKED_TEXT,
            "tier": "",
            "sub_tier": None,
            "is_ranked": False,
            "status": "unranked",
        }
    is_ranked = bool(info.get("is_ranked"))
    display = str(info.get("display_rank") or "").strip()
    if not display:
        display = str(info.get("rank_name_zh") or info.get("rank_name") or "").strip() if is_ranked else ""
    if not is_ranked or not display:
        display = UNRANKED_TEXT
    tier = str(info.get("rank_name_zh") or info.get("rank_name") or "").strip() if is_ranked else ""
    return {
        "display": display,
        "tier": tier,
        "sub_tier": info.get("rank_sub_tier"),
        "is_ranked": is_ranked,
        "status": str(info.get("rank_status") or ("ranked" if is_ranked else "unranked")),
    }


def role_card_view(role_row: Any) -> Dict[str, Any]:
    row = _dict(role_row)
    role_type = str(row.get("role_type") or "").strip().lower()
    win_rate = _float(row.get("win_rate"))
    return {
        "role_type": role_type,
        "role_name": role_display_name(role_type, row.get("role_name_zh")),
        "matches": _int(row.get("match_sum")),
        "wins": _int(row.get("wins")),
        "losses": _int(row.get("losses")),
        "win_rate": win_rate,
        "current": rank_view(row.get("current")),
        "peak": rank_view(row.get("peak")),
        "has_data": _int(row.get("match_sum")) > 0 or bool(_dict(row.get("current"))),
    }


def split_role_cards(roles: Sequence[Any]) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """返回 (三个预设职责卡, 开放职责卡)。缺失职责用空卡补齐，保证布局稳定。"""
    by_type: Dict[str, Dict[str, Any]] = {}
    extras: List[Dict[str, Any]] = []
    for row in roles or []:
        view = role_card_view(row)
        key = view["role_type"]
        if key in PRESET_ROLES or key == "open":
            by_type.setdefault(key, view)
        else:
            extras.append(view)
    presets: List[Dict[str, Any]] = []
    for key in PRESET_ROLES:
        presets.append(by_type.get(key) or {
            "role_type": key,
            "role_name": ROLE_FALLBACK_NAMES[key],
            "matches": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": None,
            "current": rank_view(None),
            "peak": rank_view(None),
            "has_data": False,
        })
    open_card = by_type.get("open")
    if open_card is None and extras:
        open_card = extras[0]
    return presets, open_card


# --------------------------------------------------------------------------- 玩家资料
def player_header_view(card: Any) -> Dict[str, Any]:
    data = _dict(card)
    return {
        "battle_tag": str(data.get("battle_tag") or "").strip(),
        "bnet_id": str(data.get("bnet_id") or "").strip(),
        "icon_url": str(data.get("icon_url") or "").strip(),
        "title": str(data.get("title") or "").strip(),
        "level": _int(data.get("level")),
        "game_time": data.get("game_time"),
    }


def format_game_time(value: Any) -> str:
    """大神 gameTime 目前是小时数字符串；无法识别时原样返回。"""
    if value in (None, ""):
        return ""
    number = _float(value)
    if number is None:
        return str(value)
    if number <= 0:
        return ""
    if number >= 1:
        return f"{number:,.1f} 小时"
    return f"{number * 60:.0f} 分钟"


# --------------------------------------------------------------------------- 比赛
def match_filter_key(match: Mapping[str, Any]) -> str:
    """把逐场记录映射到筛选键，依据后端 source_mode / game_mode 机器值。"""
    source = str(match.get("source_mode") or "").strip()
    mode = str(match.get("game_mode") or "").strip()
    token = f"{source} {mode}".lower()
    if "sportfight" in token:
        return "SportFight"
    if "quickfight" in token or "leisurefight" in token:
        return "fight_quick"
    if source == "sport" or ("sport" in token and "fight" not in token):
        return "sport"
    if source == "leisure" or "leisure" in token or "quick" in token:
        return "leisure"
    return "other"


def competitive_queue_kind(match: Mapping[str, Any]) -> str:
    """标准竞技的队列类型：``preset`` / ``open`` / ``""``（非标准竞技）。

    只读取后端已经解析好的字段：``game_mode`` / ``game_mode_zh``（SportPreset /
    SportOpen / 竞技 · 预设职责 / 竞技 · 开放职责）以及 ``role_type``。
    不做任何基于 UI 显示文字的模糊匹配，也不重写第二套模式识别逻辑。
    """
    if match_filter_key(match) != "sport":
        return ""
    token = f"{match.get('game_mode') or ''} {match.get('source_mode') or ''}".lower()
    zh = str(match.get("game_mode_zh") or "")
    if "open" in token or "开放职责" in zh:
        return "open"
    if "preset" in token or "预设职责" in zh:
        return "preset"
    # 上游没给出队列类型时用职责兜底：open 职责只可能来自开放职责队列。
    role = str(match.get("role_type") or "").strip().lower()
    if role == "open":
        return "open"
    if role in PRESET_ROLES:
        return "preset"
    return "preset"


def is_preset_competitive(match: Mapping[str, Any]) -> bool:
    """标准竞技（可用于竞逐导入）：竞技且非角斗。"""
    return bool(match.get("competitive")) and match_filter_key(match) == "sport"


def match_view(match: Mapping[str, Any]) -> Dict[str, Any]:
    row = dict(match)
    kills = _int(row.get("kills"))
    assists = _int(row.get("assists"))
    deaths = _int(row.get("deaths"))
    kda = (kills + assists) / deaths if deaths > 0 else float(kills + assists)
    heroes = hero_entries_from_match(row)
    view = {
        # 一局可能有多个英雄；主英雄在首位。
        "heroes": heroes,
        "match_id": str(row.get("match_id") or ""),
        "result": str(row.get("result") or "unknown"),
        "competitive": bool(row.get("competitive")),
        "filter_key": match_filter_key(row),
        "map_name": str(row.get("map_name") or row.get("map_name_raw") or "").strip(),
        "map_name_raw": str(row.get("map_name_raw") or "").strip(),
        "map_guid": str(row.get("map_guid") or "").strip(),
        "instance_type": str(row.get("instance_type") or "").strip(),
        "instance_type_zh": str(row.get("instance_type_zh") or "").strip(),
        "map_mode": str(row.get("map_mode") or "").strip(),
        # 地图类型中文名：后端 map_mode_zh 为准；绝不使用 instance_type(IT_RANKED)。
        "map_mode_zh": str(row.get("map_mode_zh") or "").strip(),
        "game_mode": str(row.get("game_mode") or "").strip(),
        "game_mode_zh": str(row.get("game_mode_zh") or "").strip(),
        "hero_name": str(row.get("hero_name") or row.get("hero_name_raw") or "").strip(),
        "hero_name_raw": str(row.get("hero_name_raw") or "").strip(),
        "hero_guid": str(row.get("hero_guid") or "").strip(),
        "role_type": str(row.get("role_type") or "").strip().lower(),
        "role_name": role_display_name(row.get("role_type"), row.get("role_name_zh")),
        "team_score": _int(row.get("team_score")),
        "opponent_score": _int(row.get("opponent_score")),
        "kills": kills,
        "assists": assists,
        "deaths": deaths,
        "kda": kda,
        "hero_damage": _int(row.get("hero_damage")),
        "healing": _int(row.get("healing")),
        "resist_damage": _int(row.get("resist_damage")),
        "damage_taken": _int(row.get("damage_taken")),
        "healing_taken": _int(row.get("healing_taken")),
        "final_hits": _int(row.get("final_hits")),
        "game_time_sec": _float(row.get("game_time_sec")),
        "begin_ts": _int(row.get("begin_ts")),
        "season": _int(row.get("season")) or None,
        "source_mode": str(row.get("source_mode") or ""),
        "rank_display": "",
        "raw": row.get("raw") if isinstance(row.get("raw"), Mapping) else {},
    }
    # 竞技队列类型（预设 / 开放）：竞技 UI 分类用，最近 N 场统计不区分。
    view["queue_kind"] = competitive_queue_kind(view)
    return view


def apply_rank_from_roles(views: Sequence[Dict[str, Any]], season_roles: Mapping[int, Sequence[Any]]) -> None:
    """给每场比赛补上该赛季对应职责的段位展示文本。

    大神逐场列表本身不带段位，所以取同赛季同职责的赛季段位；没有则显示未定级。
    """
    cache: Dict[Tuple[int, str], str] = {}
    for season, roles in (season_roles or {}).items():
        for row in roles or []:
            view = role_card_view(row)
            cache[(int(season), view["role_type"])] = view["current"]["display"]
    for view in views:
        season = _int(view.get("season"), 0)
        role = str(view.get("role_type") or "")
        view["rank_display"] = cache.get((season, role), "")


def filter_matches(
    views: Sequence[Dict[str, Any]],
    *,
    filter_key: str = "all",
    season: Optional[int] = None,
    preset_role: str = "all",
) -> List[Dict[str, Any]]:
    """按模式 + 赛季（+ 预设职责二级筛选）过滤比赛。

    ``filter_key`` 支持：
    - ``all`` / ``leisure`` / ``SportFight`` / ``fight_quick``：与原逻辑一致；
    - ``sport``：全部标准竞技（预设 + 开放）；
    - ``sport_preset`` / ``sport_open``：竞技里的预设职责 / 开放职责。
    """
    key = str(filter_key or "all")
    role = str(preset_role or "all").strip().lower()
    out: List[Dict[str, Any]] = []
    for view in views:
        base = str(view.get("filter_key") or "")
        if key in ("sport_preset", "sport_open"):
            if base != "sport":
                continue
            wanted = "preset" if key == "sport_preset" else "open"
            if str(view.get("queue_kind") or "") != wanted:
                continue
            if key == "sport_preset" and role != "all":
                if str(view.get("role_type") or "").strip().lower() != role:
                    continue
        elif key != "all" and base != key:
            continue
        if season is not None and _int(view.get("season"), 0) != int(season):
            continue
        out.append(view)
    return out


# --------------------------------------------------------------------------- 近期状态
def _excluded_from_recent_form(view: Mapping[str, Any]) -> bool:
    """在后端模式字段上再兜一层，排除自定义 / 训练 / 街机 / 角斗等模式。"""
    token = " ".join(
        str(view.get(key) or "")
        for key in ("game_mode", "source_mode", "instance_type", "game_mode_zh", "instance_type_zh")
    ).lower()
    return any(bad in token for bad in _RECENT_FORM_EXCLUDED)


def recent_form_candidates(
    views: Sequence[Mapping[str, Any]],
    season: Optional[int],
) -> List[Dict[str, Any]]:
    """当前赛季的「竞技 + 快速」候选集合，按真实比赛时间升序（旧 → 新）。

    - 竞技里的预设职责和开放职责全部合并进来；
    - 快速比赛正常进入；
    - 自定义 / 训练 / 街机 / 角斗等模式一律排除；
    - 绝不跨赛季：``season`` 为 None 时返回空列表。
    """
    if season is None:
        return []
    try:
        wanted = int(season)
    except (TypeError, ValueError):
        return []
    rows: List[Dict[str, Any]] = []
    for view in views or []:
        if _int(view.get("season"), 0) != wanted:
            continue
        if str(view.get("filter_key") or "") not in RECENT_FORM_FILTER_KEYS:
            continue
        if _excluded_from_recent_form(view):
            continue
        rows.append(dict(view))
    # 依据真实比赛时间排序；时间相同时用 match_id 稳定兜底。
    rows.sort(key=lambda v: (_int(v.get("begin_ts")), str(v.get("match_id") or "")))
    return rows


def recent_form_summary(
    views: Sequence[Mapping[str, Any]],
    season: Optional[int],
    limit: int = RECENT_FORM_LIMIT,
) -> Dict[str, Any]:
    """当前赛季最近最多 ``limit`` 场（竞技 + 快速）的胜率与走势。

    - 候选不足 ``limit`` 时使用实际场数，绝不跨赛季补齐；
    - 平局计入总场数（胜率分母）；
    - ``results`` 顺序为「较早 → 最近」，即最右边是最新一场；
    - 没有任何候选时 ``win_rate`` 为 None，调用方不得显示 0%。
    """
    limit = max(1, int(limit))
    candidates = recent_form_candidates(views, season)
    # 先按时间倒序取最新 limit 场，再翻回时间升序用于展示。
    window = candidates[-limit:]
    results = [str(v.get("result") or "unknown") for v in window]
    wins = sum(1 for r in results if r == "win")
    losses = sum(1 for r in results if r == "loss")
    total = len(results)
    draws = total - wins - losses
    return {
        "season": int(season) if season is not None else None,
        "limit": limit,
        "available": len(candidates),
        "total": total,
        "wins": wins,
        "losses": losses,
        "draws": draws,
        # 平局计入分母；没有比赛时为 None（不要写 0.0）。
        "win_rate": (wins * 100.0 / total) if total else None,
        "results": results,
        "matches": window,
    }


def recent_form_record_text(summary: Mapping[str, Any]) -> str:
    """``12胜 · 7负 · 1平``；没有平局时不显示 ``0平``。"""
    info = dict(summary or {})
    if not _int(info.get("total")):
        return ""
    parts = [f"{_int(info.get('wins'))}胜", f"{_int(info.get('losses'))}负"]
    draws = _int(info.get("draws"))
    if draws > 0:
        parts.append(f"{draws}平")
    return " · ".join(parts)


def recent_form_rate_text(summary: Mapping[str, Any]) -> str:
    rate = dict(summary or {}).get("win_rate")
    value = _float(rate)
    return f"{value:.1f}%" if value is not None else ""


def format_ts(begin_ts: Any, pattern: str = "%y/%m/%d %H:%M") -> str:
    ts = _int(begin_ts, 0)
    if ts <= 0:
        return "—"
    if ts > 10_000_000_000:
        ts //= 1000
    try:
        return datetime.fromtimestamp(ts).strftime(pattern)
    except (OSError, OverflowError, ValueError):
        return "—"


def format_duration(seconds: Any) -> str:
    value = _float(seconds)
    if value is None or value <= 0:
        return ""
    total = int(value)
    minutes, sec = divmod(total, 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"{hours}小时{minutes}分"
    return f"{minutes}分{sec:02d}秒"


def format_compact(value: Any) -> str:
    number = _int(value)
    if number >= 100_000:
        return f"{number / 1000:.0f}k"
    if number >= 1000:
        return f"{number / 1000:.1f}k"
    return str(number)


# --------------------------------------------------------------------------- 组排
@dataclass
class TeamLayout:
    """一支队伍排序后的玩家序列 + 组排分组信息。"""

    players: List[Dict[str, Any]] = field(default_factory=list)
    # 每个玩家的 party 序号（None 表示单排）
    party_ids: List[Optional[int]] = field(default_factory=list)

    def party_span(self, index: int) -> Tuple[bool, bool, bool]:
        """返回 (是否组排, 是否该组首位, 是否该组末位)。用于绘制连续竖线。"""
        if not 0 <= index < len(self.party_ids):
            return False, False, False
        pid = self.party_ids[index]
        if pid is None:
            return False, False, False
        first = index == 0 or self.party_ids[index - 1] != pid
        last = index == len(self.party_ids) - 1 or self.party_ids[index + 1] != pid
        return True, first, last


def _player_ids(player: Mapping[str, Any]) -> List[str]:
    out: List[str] = []
    for key in ("bnet_id", "customer_token", "name"):
        value = str(player.get(key) or "").strip()
        if value:
            out.append(value)
    return out


# 详情队内排序用的职责权重：重装 → 输出 → 支援 → 未知。
_ROLE_SORT_RANK = {"tank": 0, "dps": 1, "healer": 2}


def role_sort_rank(role_type: Any) -> int:
    return _ROLE_SORT_RANK.get(str(role_type or "").strip().lower(), 3)


def build_team_layout(players: Sequence[Mapping[str, Any]]) -> TeamLayout:
    """根据 friend_bnet_ids 计算组排，并决定队内最终展示顺序。

    组排识别（并查集）保持原有实现不变，只调整最终顺序，优先级为：
    1. 重装永远第一行（无论是否参与组排）；
    2. 组排成员尽量连续显示（允许输出 / 支援交错）；
    3. 其余情况保持 输出 → 支援 的自然顺序，同职责按原始稳定顺序。
    """
    rows = [dict(p) for p in players or []]
    count = len(rows)
    parent = list(range(count))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    id_to_index: Dict[str, int] = {}
    for index, row in enumerate(rows):
        for token in _player_ids(row):
            id_to_index.setdefault(token, index)

    for index, row in enumerate(rows):
        friends = row.get("friend_bnet_ids")
        if not isinstance(friends, (list, tuple)):
            continue
        for friend in friends:
            token = str(friend or "").strip()
            if not token:
                continue
            other = id_to_index.get(token)
            if other is not None and other != index:
                union(index, other)

    groups: Dict[int, List[int]] = {}
    for index in range(count):
        groups.setdefault(find(index), []).append(index)

    # party_of：只有成员 >= 2 才算组排（与原逻辑一致）。
    party_of: Dict[int, Optional[int]] = {}
    party_members: Dict[int, List[int]] = {}
    for root, members in groups.items():
        if len(members) >= 2:
            party_members[root] = sorted(members)
    for index in range(count):
        root = find(index)
        party_of[index] = root if root in party_members else None

    def role_key(index: int) -> Tuple[int, int]:
        return (role_sort_rank(rows[index].get("role_type")), index)

    order: List[int] = []
    emitted: set[int] = set()

    def emit_party(root: int, *, lead: Optional[int] = None) -> None:
        members = [m for m in party_members[root] if m not in emitted]
        if lead is not None and lead in members:
            members.remove(lead)
            members.insert(0, lead)
            rest = sorted(members[1:], key=role_key)
            members = [lead] + rest
        else:
            members = sorted(members, key=role_key)
        for member in members:
            order.append(member)
            emitted.add(member)

    def emit_one(index: int) -> None:
        root = party_of[index]
        if root is not None:
            emit_party(root, lead=index)
        else:
            order.append(index)
            emitted.add(index)

    # 1) 重装锁定第一行。重装本人参与组排时，同组成员紧随其后。
    tank = next((i for i in range(count) if role_sort_rank(rows[i].get("role_type")) == 0), None)
    if tank is not None:
        emit_one(tank)

    # 2) 其余玩家按 输出 → 支援 顺序推进；遇到组排成员就把整组一次性排完，保证连续。
    for index in sorted(range(count), key=role_key):
        if index in emitted:
            continue
        emit_one(index)

    layout = TeamLayout()
    party_no = 0
    seen_party: Dict[int, int] = {}
    for index in order:
        root = party_of[index]
        if root is None:
            layout.players.append(rows[index])
            layout.party_ids.append(None)
            continue
        if root not in seen_party:
            party_no += 1
            seen_party[root] = party_no
        layout.players.append(rows[index])
        layout.party_ids.append(seen_party[root])
    return layout


def _self_identity(player: Mapping[str, Any]) -> Dict[str, str]:
    """只归一化游戏账号身份字段，不使用大神登录身份。"""
    aliases = {
        "customer_token": ("customer_token", "customerToken"),
        "player_token": ("player_token", "playerToken"),
        "bnet_id": ("bnet_id", "bnetId", "battleNetId"),
        "account_id": ("account_id", "accountId"),
        "player_id": ("player_id", "playerId"),
        "battle_tag": ("battle_tag", "battleTag", "battletag", "full_id", "name", "query"),
    }
    result = {}
    for key, names in aliases.items():
        for name in names:
            value = str(player.get(name) or "").strip()
            if value and (key != "battle_tag" or ("#" in value and all(value.rsplit("#", 1)))):
                result[key] = value
                break
    return result


def query_player_identity(player: Mapping[str, Any], card: Mapping[str, Any]) -> Dict[str, str]:
    """查询解析结果为准，资料卡仅补缺；显示昵称不能覆盖完整 BattleTag。"""
    identity = _self_identity(card)
    identity.update(_self_identity(player))
    return identity


def is_self_player(
    player: Mapping[str, Any],
    *,
    self_token: str = "",
    self_bnet_id: str = "",
    self_battle_tag: str = "",
    self_identity: Mapping[str, Any] | None = None,
) -> bool:
    """只按当前查询账号与详情玩家的完整 BattleTag 判断本人。"""
    identity = _self_identity(player)
    wanted = _self_identity({"battle_tag": self_battle_tag})
    wanted.update(_self_identity(self_identity or {}))
    tag, wanted_tag = identity.get("battle_tag", ""), wanted.get("battle_tag", "")
    return bool(tag and wanted_tag and tag.casefold() == wanted_tag.casefold())


def detail_player_view(player: Mapping[str, Any], extra: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    """合并记分板玩家与 match_all_players_detail 补充的卡片信息。"""
    row = dict(player)
    more = _dict(extra)
    rank = rank_view(row.get("rank_info") or more.get("rank_info"))
    scoreboard_extra = _dict(more.get("scoreboard"))
    # 一局多英雄：后端 heroes / hero_list 已经抓好，这里只归一化+排序，不再发请求。
    heroes = hero_entries(
        more.get("heroes"),
        more.get("hero_list"),
        row.get("heroes"),
        row.get("hero_list"),
        scoreboard_extra.get("hero_list"),
    )
    first_hero = heroes[0] if heroes else {}
    hero_name = str(
        row.get("hero_name") or row.get("hero_name_raw")
        or scoreboard_extra.get("hero_name") or scoreboard_extra.get("hero_name_raw")
        or first_hero.get("hero_name") or first_hero.get("hero_name_raw") or ""
    ).strip()
    hero_guid = str(
        row.get("hero_guid") or scoreboard_extra.get("hero_guid") or first_hero.get("hero_guid") or ""
    ).strip()
    if not heroes and (hero_guid or hero_name):
        heroes = [{
            "hero_guid": hero_guid,
            "hero_name": hero_name,
            "hero_name_raw": str(row.get("hero_name_raw") or "").strip(),
            "play_time": None,
        }]
    role_type = str(row.get("role_type") or scoreboard_extra.get("role_type") or more.get("role_type") or "").strip().lower()
    role_zh = row.get("role_name_zh") or scoreboard_extra.get("role_name_zh") or more.get("role_name_zh")
    identity = query_player_identity(row, more)
    return {
        **identity,
        "name": str(row.get("name") or more.get("name") or "未知玩家").strip(),
        "bnet_id": identity.get("bnet_id", ""),
        "customer_token": identity.get("customer_token", ""),
        "team_type": str(row.get("team_type") or more.get("team_type") or "").strip(),
        "hero_name": hero_name,
        "hero_guid": hero_guid,
        "heroes": heroes,
        "role_type": role_type,
        "role_name": role_display_name(role_type, role_zh),
        "icon_url": str(more.get("icon_url") or "").strip(),
        "title": str(more.get("title") or "").strip(),
        "endorsement": _int(row.get("endorsement") or more.get("endorsement")) or None,
        "kills": _int(row.get("kills")),
        "assists": _int(row.get("assists")),
        "deaths": _int(row.get("deaths")),
        "hero_damage": _int(row.get("hero_damage")),
        "healing": _int(row.get("healing")),
        "resist_damage": _int(row.get("resist_damage")),
        "damage_taken": _int(row.get("damage_taken")),
        "healing_taken": _int(row.get("healing_taken")),
        "final_hits": _int(row.get("final_hits")),
        "rank": rank,
        "friend_bnet_ids": list(row.get("friend_bnet_ids") or []),
    }


def split_detail_teams(
    scoreboard: Sequence[Mapping[str, Any]],
    full_players: Sequence[Mapping[str, Any]] | None = None,
    *,
    self_token: str = "",
    self_bnet_id: str = "",
    self_battle_tag: str = "",
    self_identity: Mapping[str, Any] | None = None,
) -> Tuple[TeamLayout, TeamLayout]:
    """拆分我方 / 敌方，并各自计算组排与展示顺序。

    可选的 self_* 参数用于标记「当前查询账号本人」，优先使用稳定账号 ID。
    """
    extra_index: Dict[str, Dict[str, Any]] = {}
    for row in full_players or []:
        item = _dict(row)
        for key in ("customer_token", "bnet_id", "name"):
            token = str(item.get(key) or "").strip()
            if token:
                extra_index.setdefault(token, item)

    friendly: List[Dict[str, Any]] = []
    enemy: List[Dict[str, Any]] = []
    for row in scoreboard or []:
        item = _dict(row)
        extra: Dict[str, Any] = {}
        for key in ("customer_token", "bnet_id", "name"):
            token = str(item.get(key) or "").strip()
            if token and token in extra_index:
                extra = extra_index[token]
                break
        view = detail_player_view(item, extra)
        view["is_self"] = is_self_player(
            view,
            self_token=self_token,
            self_bnet_id=self_bnet_id,
            self_battle_tag=self_battle_tag,
            self_identity=self_identity,
        )
        if str(item.get("team_type") or "").lower().startswith("enemy"):
            enemy.append(view)
        else:
            friendly.append(view)
    return build_team_layout(friendly), build_team_layout(enemy)


# --------------------------------------------------------------------------- 竞逐区间
def drive_range_summary(
    views: Sequence[Dict[str, Any]],
    season: int,
    start_match_id: str,
    end_match_id: str,
) -> Dict[str, Any]:
    """按赛季 + 起止比赛计算竞逐导入区间。

    规则：
    - 起点、终点必须都在同一个赛季（调用方已按赛季过滤）；
    - 区间包含起点和终点本身；
    - 区间内的快速/角斗等非标准竞技比赛跳过，但不截断区间。
    """
    season = int(season)
    season_rows = [v for v in views if _int(v.get("season"), 0) == season]
    # 按时间升序，符合竞逐从旧到新的自然顺序。
    season_rows.sort(key=lambda v: (_int(v.get("begin_ts")), str(v.get("match_id"))))
    ids = [str(v.get("match_id") or "") for v in season_rows]
    if start_match_id not in ids or end_match_id not in ids:
        return {"ok": False, "reason": "起点或终点不在当前赛季比赛列表中", "matches": []}
    i, j = ids.index(start_match_id), ids.index(end_match_id)
    if i > j:
        i, j = j, i
    window = season_rows[i:j + 1]
    competitive = [v for v in window if is_preset_competitive(v)]
    wins = sum(1 for v in competitive if v.get("result") == "win")
    losses = sum(1 for v in competitive if v.get("result") == "loss")
    draws = len(competitive) - wins - losses
    return {
        "ok": True,
        "season": season,
        "start": window[0] if window else None,
        "end": window[-1] if window else None,
        "window_count": len(window),
        "skipped_count": len(window) - len(competitive),
        "matches": competitive,
        "wins": wins,
        "losses": losses,
        "draws": draws,
    }


def drive_records_from_matches(views: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """转成 drive_core 记录（旧 -> 新）。平局跳过，组排未知按单排。"""
    rows = sorted(views, key=lambda v: (_int(v.get("begin_ts")), str(v.get("match_id"))))
    records: List[Dict[str, Any]] = []
    for view in rows:
        result = str(view.get("result") or "")
        if result not in ("win", "loss"):
            continue
        records.append({
            "result": "W" if result == "win" else "L",
            "grouped": False,
            "mode": "normal",
            "match_id": str(view.get("match_id") or ""),
        })
    return records


def dedupe_drive_records(
    existing: Sequence[Mapping[str, Any]],
    incoming: Sequence[Mapping[str, Any]],
) -> Tuple[List[Dict[str, Any]], int, int]:
    """用 match_id 防止重复导入，返回 (合并结果, 已存在数, 新增数)。"""
    seen = {
        str(r.get("match_id") or "")
        for r in existing
        if isinstance(r, Mapping) and r.get("match_id")
    }
    merged = [dict(r) for r in existing]
    duplicated = 0
    added = 0
    for record in incoming:
        mid = str(record.get("match_id") or "")
        if mid and mid in seen:
            duplicated += 1
            continue
        if mid:
            seen.add(mid)
        merged.append(dict(record))
        added += 1
    return merged, duplicated, added


__all__ = [
    "MATCH_FILTERS",
    "PRESET_ROLES",
    "PRESET_ROLE_FILTERS",
    "RECENT_FORM_LIMIT",
    "TeamLayout",
    "apply_rank_from_roles",
    "build_team_layout",
    "competitive_queue_kind",
    "dedupe_drive_records",
    "detail_ban_heroes",
    "detail_player_view",
    "display_role",
    "drive_range_summary",
    "drive_records_from_matches",
    "filter_matches",
    "format_compact",
    "format_duration",
    "format_game_time",
    "format_ts",
    "hero_entries",
    "hero_entries_from_match",
    "is_open_queue",
    "is_preset_competitive",
    "is_self_player",
    "match_filter_key",
    "match_view",
    "recent_form_candidates",
    "recent_form_rate_text",
    "recent_form_record_text",
    "recent_form_summary",
    "role_sort_rank",
    "player_header_view",
    "rank_view",
    "role_card_view",
    "role_display_name",
    "season_labels",
    "season_text",
    "split_detail_teams",
    "split_role_cards",
]
