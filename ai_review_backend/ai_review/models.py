from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Dict, List, Optional, Tuple


class ReviewStyle(StrEnum):
    PRAISE = "praise"
    COACH = "coach"
    ROAST = "roast"
    DATA = "data"


class ReviewScope(StrEnum):
    SELF = "self"
    SELECTED = "selected"
    GLOBAL = "global"


@dataclass(slots=True)
class HeroSummary:
    hero_guid: str = ""
    hero_name: str = ""
    play_time: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PlayerSummary:
    player_id: str
    battle_tag: str
    team: str
    role_type: str
    role_name: str
    heroes: List[HeroSummary] = field(default_factory=list)
    rank: str = ""
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    hero_damage: int = 0
    healing: int = 0
    resist_damage: int = 0
    damage_taken: int = 0
    healing_taken: int = 0
    final_hits: int = 0
    party_id: Optional[str] = None
    is_self: bool = False

    @property
    def primary_hero(self) -> Optional[HeroSummary]:
        return self.heroes[0] if self.heroes else None

    def to_ui_dict(self) -> Dict[str, Any]:
        hero = self.primary_hero
        return {
            "player_id": self.player_id,
            "battle_tag": self.battle_tag,
            "team": self.team,
            "role_type": self.role_type,
            "role_name": self.role_name,
            "hero_guid": hero.hero_guid if hero else "",
            "hero_name": hero.hero_name if hero else "",
            "heroes": [h.to_dict() for h in self.heroes],
            "kills": self.kills,
            "deaths": self.deaths,
            "assists": self.assists,
            "hero_damage": self.hero_damage,
            "healing": self.healing,
            "resist_damage": self.resist_damage,
            "rank": self.rank,
            "party_id": self.party_id,
            "is_self": self.is_self,
        }

    def to_api_dict(self, *, is_target: bool) -> Dict[str, Any]:
        # 不向 API 发送 customer_token / bnet_id 等内部账号标识。
        return {
            "id": self.player_id,
            "battle_tag": self.battle_tag,
            "team": self.team,
            "role": self.role_name or self.role_type,
            "heroes": [h.to_dict() for h in self.heroes],
            "rank": self.rank,
            "stats": {
                "kills": self.kills,
                "deaths": self.deaths,
                "assists": self.assists,
                "hero_damage": self.hero_damage,
                "healing": self.healing,
                "resist_damage": self.resist_damage,
                "damage_taken": self.damage_taken,
                "healing_taken": self.healing_taken,
                "final_hits": self.final_hits,
            },
            "party_id": self.party_id,
            "is_self": self.is_self,
            "is_target": is_target,
        }


@dataclass(slots=True)
class MatchSummary:
    match_id: str = ""
    result: str = "unknown"
    map_name: str = ""
    map_mode: str = ""
    game_mode: str = ""
    team_score: Optional[int] = None
    opponent_score: Optional[int] = None
    duration_seconds: Optional[float] = None
    begin_ts: Optional[int] = None
    friendly_bans: List[Dict[str, str]] = field(default_factory=list)
    enemy_bans: List[Dict[str, str]] = field(default_factory=list)

    def to_api_dict(self) -> Dict[str, Any]:
        return {
            "match_id": self.match_id,
            "result": self.result,
            "map": self.map_name,
            "map_mode": self.map_mode,
            "game_mode": self.game_mode,
            "score": {
                "friendly": self.team_score,
                "enemy": self.opponent_score,
            },
            "duration_seconds": self.duration_seconds,
            "begin_ts": self.begin_ts,
            "bans": {
                "friendly": self.friendly_bans,
                "enemy": self.enemy_bans,
            },
        }


@dataclass(slots=True)
class MatchReviewContext:
    match: MatchSummary
    players: List[PlayerSummary]
    data_warnings: List[str] = field(default_factory=list)

    @property
    def self_player(self) -> Optional[PlayerSummary]:
        for player in self.players:
            if player.is_self:
                return player
        return None

    def player_by_id(self, player_id: str) -> Optional[PlayerSummary]:
        for player in self.players:
            if player.player_id == player_id:
                return player
        return None

    def ui_players(self) -> List[Dict[str, Any]]:
        return [player.to_ui_dict() for player in self.players]


@dataclass(slots=True)
class ReviewRequest:
    style: ReviewStyle | str = ReviewStyle.COACH
    scope: ReviewScope | str = ReviewScope.SELF
    target_player_ids: Tuple[str, ...] = ()
    force_refresh: bool = False

    def normalized_style(self) -> ReviewStyle:
        return self.style if isinstance(self.style, ReviewStyle) else ReviewStyle(str(self.style))

    def normalized_scope(self) -> ReviewScope:
        return self.scope if isinstance(self.scope, ReviewScope) else ReviewScope(str(self.scope))


@dataclass(slots=True)
class ReviewResult:
    text: str
    model: str
    style: str
    scope: str
    target_player_ids: List[str]
    usage: Dict[str, Any] = field(default_factory=dict)
    finish_reason: str = ""
    cached: bool = False
    cache_key: str = ""
