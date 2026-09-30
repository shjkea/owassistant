# -*- coding: utf-8 -*-
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import RLock
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .auth import DashenQRAuth
from .client import DashenClient
from .credential_store import CredentialStore
from .drive_bridge import matches_to_drive_records
from .errors import MissingCredentialError, UpstreamError
from .game_catalog import resolve_hero
from .models import DashenCredential, QRStart, QRState, ResolvedPlayer
from .parsers import (
    ensure_success,
    extract_competitive_roles,
    extract_detail_players,
    extract_hero_stats,
    extract_leaderboard_entries,
    extract_match_detail_root,
    extract_match_entries,
    extract_stadium_stats,
    merge_unique_matches,
    normalize_match,
    normalize_role_type,
    payload_data,
    resolve_player_payload,
)

# 仅作为自动识别失败时的兜底值；默认会从大神当前赛季 payload 自动识别。
DEFAULT_DASHEN_CURRENT_SEASON = 24

DEFAULT_FIGHT_MODES = ("QuickFight", "LeisureFight", "SportFight")

_MODE_ALIASES = {
    "sport": "sport",
    "competitive": "sport",
    "ranked": "sport",
    "竞技": "sport",
    "leisure": "leisure",
    "quick": "leisure",
    "quickplay": "leisure",
    "快速": "leisure",
}


class OWStatsService:
    """网易大神直连后端的高层同步入口。

    不创建 Web 服务、不监听本地端口。所有网络方法均为阻塞调用，GUI 调用方应自行放线程。
    后端接口优先返回“稳定归一化字段 + 完整 raw”，这样 UI 可以更换而不影响抓取层。
    """

    def __init__(
        self,
        *,
        credential_store: CredentialStore | None = None,
        current_season: int | None = None,
        proxy: str | None = None,
    ) -> None:
        self.store = credential_store or CredentialStore()
        self._season_explicit = current_season is not None
        self.current_season = int(current_season) if current_season is not None else DEFAULT_DASHEN_CURRENT_SEASON
        self._season_source = "explicit" if self._season_explicit else "fallback"
        self._season_detection_attempted = self._season_explicit
        self._season_lock = RLock()
        self.proxy = proxy
        self._credential: Optional[DashenCredential] = self.store.load()
        self._auth_lock = RLock()
        self._auth = DashenQRAuth(self.store)
        self._signer = None

    # ---------- 扫码授权 ----------
    def _get_signer(self):
        if self._signer is None:
            from .signer_bridge import SignerBridge
            self._signer = SignerBridge()
        return self._signer

    def auth_start(self) -> QRStart:
        with self._auth_lock:
            return self._auth.start()

    def auth_poll(self, session_id: str) -> QRState:
        with self._auth_lock:
            state = self._auth.poll(session_id)
            request = state.sign_request
            if request is not None:
                signature, version = self._get_signer().sign(request)
                if request.purpose == "web_login":
                    state = self._auth.poll(session_id, login_signature=signature, signer_version=version)
                elif request.purpose == "roles":
                    state = self._auth.poll(session_id, role_signature=signature)
            public_state = "authorizing" if state.sign_request else state.state
            return QRState(
                public_state,
                state.expires_in,
                state.poll_interval_ms,
                roles=[dict(role) for role in state.roles],
            )

    def auth_complete(self, session_id: str, role_id: str | int) -> Dict[str, Any]:
        with self._auth_lock:
            request = self._auth.complete_sign_request(session_id, role_id)
            signature, _ = self._get_signer().sign(request)
            self._auth.complete(session_id, role_id, signature)
            return self.reload_credentials()

    def auth_cancel(self, session_id: str) -> None:
        with self._auth_lock:
            self._auth.cancel(session_id)

    def close_auth(self) -> None:
        with self._auth_lock:
            try:
                self._auth.close()
            finally:
                if self._signer is not None:
                    self._signer.close()
                    self._signer = None

    # ---------- 兼容旧前端的 presentation helper ----------
    def profile_view(
        self,
        *,
        customer_token: str | None = None,
        battletag: str | None = None,
    ) -> Dict[str, Any]:
        from .presentation import profile_view
        return profile_view(self.player_profile(battletag, customer_token=customer_token))

    def detail_view(
        self,
        match_id: str,
        *,
        customer_token: str | None = None,
        source_match: Dict[str, Any] | None = None,
        battletag: str | None = None,
    ) -> Dict[str, Any]:
        from .presentation import detail_view
        return detail_view(
            self.match_detail(
                match_id,
                customer_token=customer_token,
                source_match=source_match,
                battletag=battletag,
            )
        )

    @staticmethod
    def load_image(url: str) -> bytes:
        from .images import load_image
        return load_image(url)

    # ---------- 凭证 ----------
    def auth_status(self) -> Dict[str, Any]:
        c = self._credential
        return {
            "configured": bool(c),
            "role_id": c.role_id if c else None,
            "role_id_masked": self._mask_role_id(c.role_id) if c else "",
            "credential_path": str(self.store.path),
        }

    def save_credentials(
        self,
        role_id: Any,
        token: Any,
        *,
        dts: int = 2026,
        server: int = 1,
    ) -> Dict[str, Any]:
        c = self.store.normalize(role_id, token, dts=dts, server=server)
        self.store.save(c)
        self._credential = c
        return self.auth_status()

    def clear_credentials(self) -> None:
        self.store.clear()
        self._credential = None

    def reload_credentials(self) -> Dict[str, Any]:
        self._credential = self.store.load()
        return self.auth_status()

    def credential(self) -> DashenCredential:
        if self._credential is None:
            raise MissingCredentialError("尚未完成网易大神授权")
        return self._credential

    @staticmethod
    def _mask_role_id(role_id: Any) -> str:
        text = str(role_id or "")
        if len(text) <= 4:
            return text
        return "*" * (len(text) - 4) + text[-4:]

    def _new_client(self) -> DashenClient:
        return DashenClient(self.credential(), proxy=self.proxy)

    # ---------- 玩家标识 / 基本资料 ----------
    def resolve_player(self, battletag: str) -> ResolvedPlayer:
        query = str(battletag or "").replace("＃", "#").strip()
        if not query:
            raise ValueError("BattleTag 不能为空")
        with self._new_client() as client:
            payload = client.search_bnet_account(query)
        return resolve_player_payload(query, payload)

    def player_card(
        self,
        battletag: str | None = None,
        *,
        customer_token: str | None = None,
    ) -> Dict[str, Any]:
        """BattleTag / bnetId / 头像 / 头衔 / 等级 / 总游戏时长。"""
        resolved = self._resolve_target(battletag, customer_token)
        with self._new_client() as client:
            raw = ensure_success(client.query_card(resolved.customer_token), operation="查询玩家资料")
        data = payload_data(raw)
        return {
            "resolved": resolved.to_dict(),
            "battle_tag": str(data.get("name") or resolved.full_id or resolved.query or ""),
            "bnet_id": str(data.get("bnetId") or resolved.bnet_id or ""),
            "icon_url": str(data.get("icon") or resolved.icon_url or ""),
            "title": str(data.get("title") or ""),
            "level": self._to_int(data.get("level")),
            "game_time": data.get("gameTime"),
            "raw": raw,
        }

    def player_profile(
        self,
        battletag: str | None = None,
        *,
        customer_token: str | None = None,
        season: int | None = None,
        include_previous_season: bool = True,
    ) -> Dict[str, Any]:
        """旧接口兼容：返回一份可用赛季的 card/sport/leisure 原始资料。"""
        resolved = self._resolve_target(battletag, customer_token)

        with self._new_client() as client:
            if season is None:
                self._ensure_current_season(client, resolved.customer_token)
            logical_seasons = [int(season)] if season is not None else [self.current_season]
            if season is None and include_previous_season and self.current_season > 1:
                logical_seasons.append(self.current_season - 1)
            card = ensure_success(client.query_card(resolved.customer_token), operation="查询玩家资料")
            selected_sport: Dict[str, Any] = {}
            selected_leisure: Dict[str, Any] = {}
            selected_logical: Optional[int] = None
            selected_request: Optional[int] = None

            for logical in logical_seasons:
                for request_season in self._season_request_values(logical):
                    sport = self._safe_count_info(client, resolved.customer_token, "sport", request_season)
                    leisure = self._safe_count_info(client, resolved.customer_token, "leisure", request_season)
                    selected_sport, selected_leisure = sport, leisure
                    selected_logical, selected_request = logical, request_season
                    if self._profile_payload_has_content(sport) or self._profile_payload_has_content(leisure):
                        break
                if self._profile_payload_has_content(selected_sport) or self._profile_payload_has_content(selected_leisure):
                    break

        return {
            "resolved": resolved.to_dict(),
            "profile_card": card,
            "sport": selected_sport,
            "leisure": selected_leisure,
            "logical_season": selected_logical,
            "request_season": selected_request,
            "current_season": self.current_season,
            "season_source": self._season_source,
        }

    def player_seasons(
        self,
        battletag: str | None = None,
        *,
        customer_token: str | None = None,
        season_count: int = 3,
        start_season: int | None = None,
        end_season: int | None = None,
        modes: Sequence[str] = ("sport", "leisure"),
    ) -> Dict[str, Any]:
        """返回最近 N 赛季（默认 3）完整 countInfo 资料。

        每赛季保留竞技/快速原始 JSON，并额外归一化职责段位和英雄统计。
        """
        resolved = self._resolve_target(battletag, customer_token)
        normalized_modes = self._normalize_modes(modes)
        warnings: List[str] = []
        rows: List[Dict[str, Any]] = []

        with self._new_client() as client:
            if start_season is None and end_season is None:
                self._ensure_current_season(client, resolved.customer_token)
            seasons = self._resolve_seasons(season_count, start_season, end_season)
            card = ensure_success(client.query_card(resolved.customer_token), operation="查询玩家资料")
            for season in seasons:
                mode_payloads: Dict[str, Dict[str, Any]] = {}
                request_seasons: Dict[str, Optional[int]] = {}
                for mode in normalized_modes:
                    payload, req = self._count_info_with_current_fallback(
                        client, resolved.customer_token, mode, season, warnings
                    )
                    mode_payloads[mode] = payload
                    request_seasons[mode] = req

                sport = mode_payloads.get("sport", {})
                leisure = mode_payloads.get("leisure", {})
                rows.append({
                    "season": season,
                    "request_seasons": request_seasons,
                    "has_competitive": self._profile_payload_has_content(sport),
                    "competitive": {
                        "roles": extract_competitive_roles(sport),
                        "hero_stats": extract_hero_stats(sport),
                        "raw": sport,
                    } if "sport" in mode_payloads else None,
                    "quick": {
                        "hero_stats": extract_hero_stats(leisure),
                        "raw": leisure,
                    } if "leisure" in mode_payloads else None,
                    "raw_modes": mode_payloads,
                })

        return {
            "resolved": resolved.to_dict(),
            "profile_card": card,
            "current_season": self.current_season,
            "season_source": self._season_source,
            "season_range": {"start": min(seasons), "end": max(seasons), "count": len(seasons)},
            "seasons": rows,
            "warnings": warnings,
        }

    def hero_stats(
        self,
        battletag: str | None = None,
        *,
        customer_token: str | None = None,
        season_count: int = 3,
        start_season: int | None = None,
        end_season: int | None = None,
        modes: Sequence[str] = ("sport", "leisure"),
    ) -> Dict[str, Any]:
        """按赛季返回大神所有英雄使用统计列表，不丢弃原始字段。"""
        data = self.player_seasons(
            battletag,
            customer_token=customer_token,
            season_count=season_count,
            start_season=start_season,
            end_season=end_season,
            modes=modes,
        )
        seasons: List[Dict[str, Any]] = []
        for item in data["seasons"]:
            seasons.append({
                "season": item["season"],
                "competitive": item["competitive"]["hero_stats"] if item.get("competitive") else None,
                "quick": item["quick"]["hero_stats"] if item.get("quick") else None,
            })
        return {
            "resolved": data["resolved"],
            "season_range": data["season_range"],
            "seasons": seasons,
            "warnings": data["warnings"],
        }

    # ---------- 角斗 / 体育场赛季资料 ----------
    def stadium_seasons(
        self,
        battletag: str | None = None,
        *,
        customer_token: str | None = None,
        season_count: int = 3,
        start_season: int | None = None,
        end_season: int | None = None,
    ) -> Dict[str, Any]:
        """返回 fight/queryCount 的赛季维度数据。

        当前上游主要包含 roleTypeCountData / heroUseSummaryList；两类数据均归一化，
        同时完整保留 raw。该接口独立于标准 sport/leisure，前端不用也不会影响其它调用。
        """
        resolved = self._resolve_target(battletag, customer_token)
        warnings: List[str] = []
        rows: List[Dict[str, Any]] = []
        with self._new_client() as client:
            if start_season is None and end_season is None:
                self._ensure_current_season(client, resolved.customer_token)
            seasons = self._resolve_seasons(season_count, start_season, end_season)
            for season in seasons:
                payload, request_season = self._fight_count_with_current_fallback(
                    client, resolved.customer_token, season, warnings
                )
                stats = extract_stadium_stats(payload)
                rows.append({
                    "season": season,
                    "request_season": request_season,
                    "has_stadium": self._fight_payload_has_content(payload),
                    "stadium": stats,
                })
        return {
            "resolved": resolved.to_dict(),
            "current_season": self.current_season,
            "season_source": self._season_source,
            "season_range": {"start": min(seasons), "end": max(seasons), "count": len(seasons)},
            "seasons": rows,
            "warnings": warnings,
        }

    # ---------- 三赛季完整逐场战绩 ----------
    def match_history(
        self,
        battletag: str | None = None,
        *,
        customer_token: str | None = None,
        season_count: int = 3,
        start_season: int | None = None,
        end_season: int | None = None,
        modes: Sequence[str] = ("sport", "leisure"),
        include_fight: bool = True,
        fight_modes: Sequence[str] = DEFAULT_FIGHT_MODES,
        include_raw: bool = True,
        max_pages_per_mode: int = 250,
    ) -> Dict[str, Any]:
        """抓取指定赛季范围内的完整逐场记录。

        与旧 recent_matches 不同：这里没有 30/100 场结果上限。标准快速/竞技及（默认开启）
        QuickFight/LeisureFight/SportFight 均逐页翻到空页、重复页或没有新 matchId 为止。
        max_pages_per_mode 仅是防上游异常无限循环的保险阀。
        """
        resolved = self._resolve_target(battletag, customer_token)
        normalized_modes = self._normalize_modes(modes)
        normalized_fight_modes = self._normalize_fight_modes(fight_modes) if include_fight else []
        max_pages_per_mode = max(1, int(max_pages_per_mode))
        warnings: List[str] = []
        all_raw: List[Dict[str, Any]] = []
        season_summaries: List[Dict[str, Any]] = []
        overall_complete = True

        with self._new_client() as client:
            if start_season is None and end_season is None:
                self._ensure_current_season(client, resolved.customer_token)
            seasons = self._resolve_seasons(season_count, start_season, end_season)
            for season in seasons:
                season_raw: List[Dict[str, Any]] = []
                mode_summaries: Dict[str, Dict[str, Any]] = {}
                for mode in normalized_modes:
                    mode_rows, summary = self._fetch_complete_mode_history(
                        client,
                        resolved.customer_token,
                        mode=mode,
                        logical_season=season,
                        max_pages=max_pages_per_mode,
                        warnings=warnings,
                    )
                    season_raw = merge_unique_matches(season_raw, mode_rows)
                    mode_summaries[mode] = summary
                    overall_complete = overall_complete and bool(summary.get("complete"))
                for fight_mode in normalized_fight_modes:
                    mode_rows, summary = self._fetch_complete_fight_mode_history(
                        client,
                        resolved.customer_token,
                        fight_mode=fight_mode,
                        logical_season=season,
                        max_pages=max_pages_per_mode,
                        warnings=warnings,
                    )
                    season_raw = merge_unique_matches(season_raw, mode_rows)
                    mode_summaries[fight_mode] = summary
                    overall_complete = overall_complete and bool(summary.get("complete"))
                all_raw = merge_unique_matches(all_raw, season_raw)
                season_summaries.append({
                    "season": season,
                    "count": len(season_raw),
                    "modes": mode_summaries,
                })

        normalized = [normalize_match(row) for row in all_raw]
        if not include_raw:
            for row in normalized:
                row.pop("raw", None)
        return {
            "resolved": resolved.to_dict(),
            "current_season": self.current_season,
            "season_source": self._season_source,
            "season_range": {"start": min(seasons), "end": max(seasons), "count": len(seasons)},
            "count": len(normalized),
            "complete": overall_complete,
            "season_summaries": season_summaries,
            "included_modes": {"normal": normalized_modes, "fight": normalized_fight_modes},
            "warnings": warnings,
            "matches": normalized,
            "raw_matches": all_raw if include_raw else None,
        }

    def three_season_matches(
        self,
        battletag: str | None = None,
        *,
        customer_token: str | None = None,
        modes: Sequence[str] = ("sport", "leisure"),
        include_fight: bool = True,
        fight_modes: Sequence[str] = DEFAULT_FIGHT_MODES,
        include_raw: bool = True,
        max_pages_per_mode: int = 250,
    ) -> Dict[str, Any]:
        """match_history(..., season_count=3) 的明确别名。"""
        return self.match_history(
            battletag,
            customer_token=customer_token,
            season_count=3,
            modes=modes,
            include_fight=include_fight,
            fight_modes=fight_modes,
            include_raw=include_raw,
            max_pages_per_mode=max_pages_per_mode,
        )

    def recent_matches(
        self,
        battletag: str | None = None,
        *,
        customer_token: str | None = None,
        limit: int = 20,
        include_previous_season: bool = True,
        include_raw: bool = True,
    ) -> Dict[str, Any]:
        """旧 UI 的轻量近期接口；完整历史请使用 match_history/three_season_matches。

        这里故意只取 countInfo + 下一页，避免旧 UI 为显示 30 场却把三赛季几百场全部抓完。
        """
        resolved = self._resolve_target(battletag, customer_token)
        limit = max(1, int(limit))
        warnings: List[str] = []
        all_raw: List[Dict[str, Any]] = []
        successful = 0
        with self._new_client() as client:
            self._ensure_current_season(client, resolved.customer_token)
            seasons = [self.current_season]
            if include_previous_season and self.current_season > 1:
                seasons.append(self.current_season - 1)
            for season in seasons:
                season_rows: List[Dict[str, Any]] = []
                for request_season in self._season_request_values(season):
                    extracted: List[Dict[str, Any]] = []
                    for mode in ("sport", "leisure"):
                        try:
                            payload = ensure_success(
                                client.query_count_info(resolved.customer_token, mode, season=request_season),
                                operation="查询近期战绩",
                            )
                            successful += 1
                            rows = extract_match_entries(payload, "matchList", "recentMatchList")
                            self._tag_matches(rows, season, mode)
                            extracted.extend(rows)
                        except Exception:
                            warnings.append(f"S{season} {mode} 近期统计请求失败")
                        try:
                            payload = ensure_success(
                                client.query_match_list(resolved.customer_token, mode, page=2, season=request_season),
                                operation="查询近期战绩",
                            )
                            successful += 1
                            rows = extract_match_entries(payload, "matchList", "recentMatchList")
                            self._tag_matches(rows, season, mode)
                            extracted.extend(rows)
                        except Exception:
                            warnings.append(f"S{season} {mode} 近期第 2 页请求失败")
                    season_rows = merge_unique_matches(season_rows, extracted)
                    if season_rows:
                        break
                all_raw = merge_unique_matches(all_raw, season_rows)
                if len(all_raw) >= limit:
                    break
        if not successful:
            raise UpstreamError("近期战绩查询全部失败，请稍后重试")
        raw = all_raw[:limit]
        normalized = [normalize_match(row) for row in raw]
        if not include_raw:
            for row in normalized:
                row.pop("raw", None)
        return {
            "resolved": resolved.to_dict(),
            "current_season": self.current_season,
            "season_source": self._season_source,
            "count": len(normalized),
            "warnings": list(dict.fromkeys(warnings)),
            "matches": normalized,
            "raw_matches": raw if include_raw else None,
        }

    # ---------- 单局完整详情 ----------
    def match_detail(
        self,
        match_id: str,
        *,
        battletag: str | None = None,
        customer_token: str | None = None,
        source_match: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        resolved = self._resolve_target(battletag, customer_token)
        match_id = str(match_id or "").strip()
        if not match_id:
            raise ValueError("match_id 不能为空")
        source = dict(source_match or {})
        is_fight = "fight" in str(source.get("gameMode") or "").lower()
        with self._new_client() as client:
            detail = ensure_success(
                client.fight_query_match_info(resolved.customer_token, match_id)
                if is_fight else client.query_match_info(resolved.customer_token, match_id),
                operation="查询角斗单局详情" if is_fight else "查询单局详情",
            )
        result = {
            "resolved": resolved.to_dict(),
            "match_id": match_id,
            "match_kind": "fight" if is_fight else "normal",
            "source_match": source,
            "detail": detail,
            "detail_root": extract_match_detail_root(detail),
            "scoreboard_players": extract_detail_players(detail),
        }
        # 提供全员稳定字段，同时 raw detail 原样保留。
        from .presentation import detail_view
        result["normalized"] = detail_view(result)
        return result

    def match_all_players_detail(
        self,
        match_id: str,
        *,
        battletag: str | None = None,
        customer_token: str | None = None,
        source_match: Dict[str, Any] | None = None,
        max_workers: int = 4,
        include_player_raw: bool = True,
    ) -> Dict[str, Any]:
        """抓取普通对局的“全员详细”：记分板 + 每名玩家自己的 heroList/statMap。

        网易 queryMatchInfo 是以“玩家 token + matchId”为视角返回详情。先用目标玩家取得双方名单，
        再为每名公开玩家解析 customerToken（名单没有时用 BattleTag 搜索），并分别查询同一 matchId。
        角斗详情结构不同，当前只返回角斗记分板，不伪造全员 heroList。
        """
        base = self.match_detail(
            match_id,
            battletag=battletag,
            customer_token=customer_token,
            source_match=source_match,
        )
        root = base.get("detail_root") if isinstance(base.get("detail_root"), dict) else {}
        scoreboard = list(base.get("scoreboard_players") or [])
        if base.get("match_kind") == "fight":
            return {
                **base,
                "all_players_supported": False,
                "all_players_complete": False,
                "player_count": len(scoreboard),
                "available_player_count": 0,
                "players": [],
                "warnings": ["角斗对局当前上游结构不提供 Overstats 同款全员 heroList 详情；已保留完整角斗 raw。"],
            }

        resolved = base.get("resolved") if isinstance(base.get("resolved"), dict) else {}
        focus_token = str(resolved.get("customer_token") or customer_token or "").strip()
        focus_name = str(resolved.get("full_id") or battletag or "").strip()
        focus_bnet_id = str(resolved.get("bnet_id") or "").strip()
        targets: List[Dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for row in scoreboard:
            if not isinstance(row, Mapping):
                continue
            item = dict(row)
            token = str(item.get("customer_token") or "").strip()
            name = str(item.get("name") or "").strip()
            bnet_id = str(item.get("bnet_id") or "").strip()
            if not token and focus_token:
                same_focus = bool(
                    (focus_bnet_id and bnet_id and focus_bnet_id == bnet_id)
                    or (focus_name and name and focus_name.lower() == name.lower())
                )
                if same_focus:
                    token = focus_token
                    item["customer_token"] = token
            key = (token, bnet_id, name.lower())
            if key in seen:
                continue
            seen.add(key)
            targets.append(item)

        # 极端情况下主详情有 heroList 但记分板列表缺失，也至少保留查询目标自己的详细。
        if not targets and (root.get("heroList") or focus_token):
            targets.append({
                "team_type": "teammate",
                "name": focus_name,
                "bnet_id": focus_bnet_id,
                "customer_token": focus_token,
                "rank_info": None,
                "raw": {},
            })

        workers = max(1, min(int(max_workers), 8))
        results_by_index: Dict[int, Dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    self._fetch_full_player_match_detail,
                    target,
                    str(match_id),
                    base_detail=base.get("detail") if str(target.get("customer_token") or "").strip() == focus_token else None,
                    base_root=root if str(target.get("customer_token") or "").strip() == focus_token else None,
                    include_raw=include_player_raw,
                ): index
                for index, target in enumerate(targets)
            }
            for future in as_completed(futures):
                index = futures[future]
                try:
                    results_by_index[index] = future.result()
                except Exception as exc:  # 单个玩家失败不能让整局数据丢失
                    target = targets[index]
                    results_by_index[index] = {
                        "name": str(target.get("name") or ""),
                        "team_type": str(target.get("team_type") or ""),
                        "bnet_id": str(target.get("bnet_id") or ""),
                        "customer_token": str(target.get("customer_token") or ""),
                        "success": False,
                        "hero_list": [],
                        "heroes": [],
                        "error": f"{type(exc).__name__}: {exc}",
                    }
        players = [results_by_index[i] for i in range(len(targets)) if i in results_by_index]
        available = sum(1 for row in players if row.get("success") and row.get("hero_list"))
        warnings = [
            f"{row.get('name') or '未知玩家'}：{row.get('error')}"
            for row in players
            if row.get("error")
        ]
        return {
            **base,
            "all_players_supported": True,
            "all_players_complete": bool(players) and available == len(players),
            "player_count": len(players),
            "available_player_count": available,
            "players": players,
            "warnings": warnings,
        }

    def _fetch_full_player_match_detail(
        self,
        target: Mapping[str, Any],
        match_id: str,
        *,
        base_detail: Any = None,
        base_root: Any = None,
        include_raw: bool = True,
    ) -> Dict[str, Any]:
        name = str(target.get("name") or "").strip()
        token = str(target.get("customer_token") or "").strip()
        resolved_bnet_id = str(target.get("bnet_id") or "").strip()
        resolve_error = ""
        if not token and name and "#" in name:
            try:
                with self._new_client() as client:
                    resolved = resolve_player_payload(name, client.search_bnet_account(name))
                token = resolved.customer_token
                resolved_bnet_id = resolved_bnet_id or resolved.bnet_id
            except Exception as exc:
                resolve_error = f"无法解析 customerToken: {type(exc).__name__}: {exc}"

        detail_payload: Dict[str, Any] = {}
        detail_root: Dict[str, Any] = {}
        card_payload: Dict[str, Any] = {}
        if isinstance(base_detail, Mapping) and isinstance(base_root, Mapping):
            detail_payload = dict(base_detail)
            detail_root = dict(base_root)
        elif token:
            with self._new_client() as client:
                detail_payload = ensure_success(
                    client.query_match_info(token, match_id),
                    operation=f"查询 {name or resolved_bnet_id or '玩家'} 单局英雄详情",
                )
            detail_root = extract_match_detail_root(detail_payload)

        if token:
            try:
                with self._new_client() as client:
                    card_payload = ensure_success(client.query_card(token), operation="查询玩家卡片")
            except Exception:
                card_payload = {}
        card = payload_data(card_payload)
        hero_list = [dict(row) for row in detail_root.get("heroList") or [] if isinstance(row, Mapping)]
        heroes = []
        for row in hero_list:
            hero_meta = resolve_hero(
                row.get("heroGuid") or row.get("heroId"),
                row.get("heroName") or row.get("hero") or row.get("name"),
            )
            heroes.append({
                "hero_guid": hero_meta["hero_guid"],
                "hero_key": hero_meta["hero_key"],
                "hero_name": hero_meta["hero_name"],
                "hero_name_raw": str(row.get("heroName") or row.get("hero") or row.get("name") or "").strip(),
                "role_type": hero_meta["role_type"],
                "stat_map": dict(row.get("statMap") or {}) if isinstance(row.get("statMap"), Mapping) else {},
                "raw": dict(row),
            })
        error = resolve_error
        if not token and not error:
            error = "没有 customerToken，无法查询该玩家的个人英雄明细"
        elif token and not hero_list:
            error = "该玩家本场未返回公开 heroList"
        inferred_role = str(target.get("role_type") or "").strip()
        if not inferred_role and heroes:
            inferred_role = str(heroes[0].get("role_type") or "").strip()
        result = {
            "name": str(card.get("name") or name or "").strip(),
            "team_type": str(target.get("team_type") or ""),
            "bnet_id": str(card.get("bnetId") or resolved_bnet_id or "").strip(),
            "customer_token": token,
            "icon_url": str(card.get("icon") or "").strip(),
            "role_type": inferred_role,
            "title": str(card.get("title") or "").strip(),
            "rank_info": target.get("rank_info"),
            "scoreboard": dict(target),
            "hero_list": hero_list,
            "heroes": heroes,
            "success": bool(hero_list),
            "error": error,
        }
        if include_raw:
            result["detail_raw"] = detail_payload
            result["card_raw"] = card_payload
        return result

    def sameplay_detail(
        self,
        player1_battletag: str | None = None,
        player2_battletag: str | None = None,
        *,
        player1_customer_token: str | None = None,
        player2_customer_token: str | None = None,
        match_id: str | None = None,
        index: int = 0,
        season_count: int = 3,
        start_season: int | None = None,
        end_season: int | None = None,
        modes: Sequence[str] = ("sport", "leisure"),
        include_fight: bool = True,
        fight_modes: Sequence[str] = DEFAULT_FIGHT_MODES,
        max_pages_per_mode: int = 250,
        max_workers: int = 4,
    ) -> Dict[str, Any]:
        """同玩列表中的某一局 + 该局全员详细。index 为 0 基索引。"""
        same = self.sameplay_records(
            player1_battletag,
            player2_battletag,
            player1_customer_token=player1_customer_token,
            player2_customer_token=player2_customer_token,
            season_count=season_count,
            start_season=start_season,
            end_season=end_season,
            modes=modes,
            include_fight=include_fight,
            fight_modes=fight_modes,
            max_pages_per_mode=max_pages_per_mode,
        )
        matches = list(same.get("matches") or [])
        selected: Dict[str, Any] | None = None
        wanted = str(match_id or "").strip()
        if wanted:
            selected = next((row for row in matches if str(row.get("match_id") or "") == wanted), None)
            if selected is None:
                raise ValueError(f"同玩记录中不存在 match_id={wanted}")
        else:
            idx = int(index)
            if idx < 0 or idx >= len(matches):
                raise IndexError(f"同玩记录 index 超出范围：{idx}；当前共 {len(matches)} 场")
            selected = matches[idx]
        p1_token = str((same.get("players") or {}).get("player1", {}).get("customer_token") or player1_customer_token or "")
        source = ((selected or {}).get("player1") or {}).get("raw") if isinstance((selected or {}).get("player1"), Mapping) else None
        full = self.match_all_players_detail(
            str((selected or {}).get("match_id") or ""),
            battletag=player1_battletag,
            customer_token=p1_token,
            source_match=dict(source or {}),
            max_workers=max_workers,
        )
        return {
            "players": same.get("players"),
            "sameplay_summary": same.get("summary"),
            "sameplay_match": selected,
            "detail": full,
            "warnings": list(dict.fromkeys([*(same.get("warnings") or []), *(full.get("warnings") or [])])),
        }

    # ---------- 历史段位 ----------
    def rank_history(
        self,
        battletag: str | None = None,
        *,
        customer_token: str | None = None,
        start_season: int | None = None,
        end_season: int | None = None,
        season_count: int = 3,
        include_stadium: bool = True,
    ) -> Dict[str, Any]:
        """按赛季返回标准竞技段位；默认同时带角斗/体育场赛季职责数据。"""
        resolved = self._resolve_target(battletag, customer_token)
        warnings: List[str] = []
        rows: List[Dict[str, Any]] = []
        with self._new_client() as client:
            if start_season is None and end_season is None:
                self._ensure_current_season(client, resolved.customer_token)
            seasons = self._resolve_seasons(season_count, start_season, end_season)
            for season in seasons:
                payload, request_season = self._count_info_with_current_fallback(
                    client, resolved.customer_token, "sport", season, warnings
                )
                roles = extract_competitive_roles(payload)
                fight_payload: Dict[str, Any] = {}
                fight_request_season: Optional[int] = None
                stadium: Optional[Dict[str, Any]] = None
                if include_stadium:
                    fight_payload, fight_request_season = self._fight_count_with_current_fallback(
                        client, resolved.customer_token, season, warnings
                    )
                    stadium = extract_stadium_stats(fight_payload)
                rows.append({
                    "season": season,
                    "request_season": request_season,
                    "has_competitive": bool(roles),
                    "competitive": {"roles": roles, "raw": payload},
                    "has_stadium": self._fight_payload_has_content(fight_payload) if include_stadium else False,
                    "stadium_request_season": fight_request_season,
                    "stadium": stadium,
                })
        return {
            "resolved": resolved.to_dict(),
            "current_season": self.current_season,
            "season_source": self._season_source,
            "season_range": {"start": min(seasons), "end": max(seasons), "count": len(seasons)},
            "seasons": rows,
            "warnings": warnings,
        }

    # ---------- 同玩 ----------
    def sameplay_records(
        self,
        player1_battletag: str | None = None,
        player2_battletag: str | None = None,
        *,
        player1_customer_token: str | None = None,
        player2_customer_token: str | None = None,
        season_count: int = 3,
        start_season: int | None = None,
        end_season: int | None = None,
        modes: Sequence[str] = ("sport", "leisure"),
        include_fight: bool = True,
        fight_modes: Sequence[str] = DEFAULT_FIGHT_MODES,
        max_pages_per_mode: int = 250,
    ) -> Dict[str, Any]:
        """通过双方完整历史的 matchId 交集得到同玩记录。"""
        p1 = self.match_history(
            player1_battletag,
            customer_token=player1_customer_token,
            season_count=season_count,
            start_season=start_season,
            end_season=end_season,
            modes=modes,
            include_fight=include_fight,
            fight_modes=fight_modes,
            include_raw=True,
            max_pages_per_mode=max_pages_per_mode,
        )
        p2 = self.match_history(
            player2_battletag,
            customer_token=player2_customer_token,
            season_count=season_count,
            start_season=start_season,
            end_season=end_season,
            modes=modes,
            include_fight=include_fight,
            fight_modes=fight_modes,
            include_raw=True,
            max_pages_per_mode=max_pages_per_mode,
        )
        p2_map = {
            str(row.get("matchId")): row
            for row in (p2.get("raw_matches") or [])
            if isinstance(row, Mapping) and row.get("matchId")
        }
        common: List[Dict[str, Any]] = []
        quick_count = 0
        competitive_count = 0
        for row1 in p1.get("raw_matches") or []:
            if not isinstance(row1, Mapping):
                continue
            match_id = str(row1.get("matchId") or "")
            row2 = p2_map.get(match_id)
            if not row2:
                continue
            n1 = normalize_match(dict(row1))
            n2 = normalize_match(dict(row2))
            competitive = bool(n1.get("competitive") or n2.get("competitive"))
            competitive_count += int(competitive)
            quick_count += int(not competitive)
            common.append({
                "match_id": match_id,
                "begin_ts": max(int(n1.get("begin_ts") or 0), int(n2.get("begin_ts") or 0)),
                "season": n1.get("season") or n2.get("season"),
                "competitive": competitive,
                "player1": n1,
                "player2": n2,
            })
        common.sort(key=lambda row: int(row.get("begin_ts") or 0), reverse=True)
        return {
            "players": {"player1": p1["resolved"], "player2": p2["resolved"]},
            "summary": {
                "total_common_count": len(common),
                "quick_count": quick_count,
                "competitive_count": competitive_count,
                "player1_scanned_count": p1["count"],
                "player2_scanned_count": p2["count"],
                "complete": bool(p1.get("complete") and p2.get("complete")),
            },
            "matches": common,
            "warnings": list(dict.fromkeys([*(p1.get("warnings") or []), *(p2.get("warnings") or [])])),
        }

    # ---------- 地区榜 / 英雄榜 ----------
    def province_rank(self, province: str, role: str) -> Dict[str, Any]:
        province = str(province or "").strip()
        role_type = normalize_role_type(role)
        if not province:
            raise ValueError("province 不能为空")
        if role_type not in {"tank", "dps", "healer", "open"}:
            raise ValueError("role 仅支持 tank/dps/healer/open（也支持中文别名）")
        with self._new_client() as client:
            raw = ensure_success(client.query_province_rank(province, role_type), operation="查询地区职责榜")
        return {
            "province": province,
            "role": role_type,
            "entries": extract_leaderboard_entries(raw),
            "raw": raw,
        }

    def hero_leaderboard(self, province: str, hero_guid: str, *, mode: str = "preset") -> Dict[str, Any]:
        """查询英雄地区榜。

        网易上游参数是 heroGuid，gameMode 使用 ``SportPreset`` / ``SportOpen``。
        对调用方保留更稳定的 ``preset`` / ``open``（并接受中文别名）。
        """
        province = str(province or "").strip()
        hero_guid = str(hero_guid or "").strip()
        mode_text = str(mode or "preset").strip().lower()
        if not province:
            raise ValueError("province 不能为空")
        if not hero_guid:
            raise ValueError("hero_guid 不能为空")
        aliases = {
            "preset": "preset",
            "sportpreset": "preset",
            "预设": "preset",
            "open": "open",
            "sportopen": "open",
            "开放": "open",
        }
        canonical_mode = aliases.get(mode_text)
        if canonical_mode is None:
            raise ValueError("mode 仅支持 preset/open（也支持 SportPreset/SportOpen、预设/开放）")
        source_mode = {"preset": "SportPreset", "open": "SportOpen"}[canonical_mode]
        with self._new_client() as client:
            raw = ensure_success(
                client.get_hero_billboard(province, source_mode, hero_guid),
                operation="查询英雄地区榜",
            )
        return {
            "province": province,
            "mode": canonical_mode,
            "source_mode": source_mode,
            "hero_guid": hero_guid,
            "entries": extract_leaderboard_entries(raw),
            "raw": raw,
        }

    def user_billboard(
        self,
        battletag: str | None = None,
        *,
        customer_token: str | None = None,
    ) -> Dict[str, Any]:
        """大神 customGetUserHeroBillboard 原始接口 + 尽力归一化条目。"""
        resolved = self._resolve_target(battletag, customer_token)
        with self._new_client() as client:
            raw = ensure_success(
                client.get_billboard_user(resolved.customer_token),
                operation="查询玩家英雄榜信息",
            )
        return {
            "resolved": resolved.to_dict(),
            "entries": extract_leaderboard_entries(raw),
            "raw": raw,
        }

    # ---------- 竞逐计算器桥接 ----------
    def import_drive_records(
        self,
        matches: Iterable[Dict[str, Any]],
        *,
        grouped: bool = False,
        normalized_input: bool = False,
    ) -> List[Dict[str, Any]]:
        if normalized_input:
            raw = [
                dict(m.get("raw") or {})
                for m in matches
                if isinstance(m, dict) and isinstance(m.get("raw"), dict)
            ]
        else:
            raw = [dict(m) for m in matches if isinstance(m, dict)]
        return matches_to_drive_records(raw, grouped=grouped, newest_first=True)

    # ---------- 内部：分页 / 赛季 ----------
    def _fetch_complete_fight_mode_history(
        self,
        client: DashenClient,
        token: str,
        *,
        fight_mode: str,
        logical_season: int,
        max_pages: int,
        warnings: List[str],
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        attempts = self._season_request_values(logical_season)
        best_rows: List[Dict[str, Any]] = []
        best_summary: Dict[str, Any] = {
            "count": 0,
            "pages": 0,
            "complete": True,
            "request_season": attempts[0] if attempts else logical_season,
            "stop_reason": "empty",
            "kind": "fight",
        }
        for request_season in attempts:
            rows, summary = self._paginate_fight_mode(
                client,
                token,
                fight_mode=fight_mode,
                logical_season=logical_season,
                request_season=request_season,
                max_pages=max_pages,
                warnings=warnings,
            )
            if len(rows) > len(best_rows):
                best_rows, best_summary = rows, summary
            if rows:
                return rows, summary
        return best_rows, best_summary

    def _paginate_fight_mode(
        self,
        client: DashenClient,
        token: str,
        *,
        fight_mode: str,
        logical_season: int,
        request_season: Optional[int],
        max_pages: int,
        warnings: List[str],
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        collected: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        pages_fetched = 0
        stop_reason = "empty"
        complete = True
        previous_signature: Optional[Tuple[str, ...]] = None
        for page in range(1, max_pages + 1):
            try:
                payload = ensure_success(
                    client.fight_query_match_list(
                        token, fight_mode, page=page, season=request_season
                    ),
                    operation=f"查询 S{logical_season} {fight_mode} 第 {page} 页",
                )
            except Exception as exc:
                warnings.append(f"S{logical_season} {fight_mode} 第 {page} 页失败：{type(exc).__name__}")
                complete = False
                stop_reason = "request_error"
                break
            pages_fetched = page
            rows = extract_match_entries(payload, "matchList", "recentMatchList")
            if not rows:
                stop_reason = "empty_page"
                break
            for row in rows:
                row["gameMode"] = fight_mode
            self._tag_matches(rows, logical_season, fight_mode)
            signature = tuple(str(row.get("matchId") or "") for row in rows)
            if signature and signature == previous_signature:
                stop_reason = "repeated_page"
                break
            previous_signature = signature
            new_rows: List[Dict[str, Any]] = []
            for row in rows:
                mid = str(row.get("matchId") or "").strip()
                if mid and mid in seen_ids:
                    continue
                if mid:
                    seen_ids.add(mid)
                new_rows.append(row)
            if not new_rows:
                stop_reason = "no_new_matches"
                break
            collected = merge_unique_matches(collected, new_rows)
        else:
            complete = False
            stop_reason = "max_pages_guard"
            warnings.append(
                f"S{logical_season} {fight_mode} 已达到 {max_pages} 页安全阈值；上游可能异常，结果标记为不完整"
            )
        return collected, {
            "count": len(collected),
            "pages": pages_fetched,
            "complete": complete,
            "request_season": request_season,
            "stop_reason": stop_reason,
            "kind": "fight",
        }

    def _fetch_complete_mode_history(
        self,
        client: DashenClient,
        token: str,
        *,
        mode: str,
        logical_season: int,
        max_pages: int,
        warnings: List[str],
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        attempts = self._season_request_values(logical_season)
        best_rows: List[Dict[str, Any]] = []
        best_summary: Dict[str, Any] = {
            "count": 0,
            "pages": 0,
            "complete": True,
            "request_season": attempts[0] if attempts else logical_season,
            "stop_reason": "empty",
        }
        for request_season in attempts:
            rows, summary = self._paginate_mode(
                client,
                token,
                mode=mode,
                logical_season=logical_season,
                request_season=request_season,
                max_pages=max_pages,
                warnings=warnings,
            )
            if len(rows) > len(best_rows):
                best_rows, best_summary = rows, summary
            if rows:
                return rows, summary
        return best_rows, best_summary

    def _paginate_mode(
        self,
        client: DashenClient,
        token: str,
        *,
        mode: str,
        logical_season: int,
        request_season: Optional[int],
        max_pages: int,
        warnings: List[str],
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        collected: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        pages_fetched = 0
        stop_reason = "empty"
        complete = True

        # queryCountInfo 通常自带第一页/近期列表，先收进去；随后仍从 queryMatchList page=1 翻页，靠 matchId 去重。
        try:
            count_payload = ensure_success(
                client.query_count_info(token, mode, season=request_season),
                operation=f"查询 S{logical_season} {mode} 统计",
            )
            bootstrap = extract_match_entries(count_payload, "matchList", "recentMatchList")
            self._tag_matches(bootstrap, logical_season, mode)
            collected = merge_unique_matches(collected, bootstrap)
            seen_ids.update(str(row.get("matchId")) for row in collected if row.get("matchId"))
        except Exception as exc:
            warnings.append(f"S{logical_season} {mode} countInfo 失败：{type(exc).__name__}")

        previous_signature: Optional[Tuple[str, ...]] = None
        for page in range(1, max_pages + 1):
            try:
                payload = ensure_success(
                    client.query_match_list(token, mode, page=page, season=request_season),
                    operation=f"查询 S{logical_season} {mode} 第 {page} 页",
                )
            except Exception as exc:
                warnings.append(f"S{logical_season} {mode} 第 {page} 页失败：{type(exc).__name__}")
                complete = False
                stop_reason = "request_error"
                break

            pages_fetched = page
            rows = extract_match_entries(payload, "matchList", "recentMatchList")
            if not rows:
                stop_reason = "empty_page"
                break
            self._tag_matches(rows, logical_season, mode)

            signature = tuple(str(row.get("matchId") or "") for row in rows)
            if signature and signature == previous_signature:
                stop_reason = "repeated_page"
                break
            previous_signature = signature

            new_rows: List[Dict[str, Any]] = []
            for row in rows:
                mid = str(row.get("matchId") or "").strip()
                if mid and mid in seen_ids:
                    continue
                if mid:
                    seen_ids.add(mid)
                new_rows.append(row)
            if not new_rows:
                # queryCountInfo 往往已经包含第 1 页；因此 page=1 全重复时继续探测 page=2。
                # 若 page>=2 仍没有任何新 matchId，则可安全视为上游到末页/重复末页。
                if page == 1 and collected:
                    continue
                stop_reason = "no_new_matches"
                break
            collected = merge_unique_matches(collected, new_rows)
        else:
            complete = False
            stop_reason = "max_pages_guard"
            warnings.append(
                f"S{logical_season} {mode} 已达到 {max_pages} 页安全阈值；上游可能异常，结果标记为不完整"
            )

        return collected, {
            "count": len(collected),
            "pages": pages_fetched,
            "complete": complete,
            "request_season": request_season,
            "stop_reason": stop_reason,
        }

    @staticmethod
    def _tag_matches(rows: Iterable[Dict[str, Any]], season: int, mode: str) -> None:
        for row in rows:
            row["_dashenSeason"] = int(season)
            row["_dashenSourceMode"] = str(mode)

    def _count_info_with_current_fallback(
        self,
        client: DashenClient,
        token: str,
        mode: str,
        logical_season: int,
        warnings: List[str],
    ) -> Tuple[Dict[str, Any], Optional[int]]:
        best: Dict[str, Any] = {}
        best_request: Optional[int] = logical_season
        for request_season in self._season_request_values(logical_season):
            try:
                payload = ensure_success(
                    client.query_count_info(token, mode, season=request_season),
                    operation=f"查询 S{logical_season} {mode} 资料",
                )
            except Exception as exc:
                warnings.append(f"S{logical_season} {mode} 资料失败：{type(exc).__name__}")
                continue
            best, best_request = payload, request_season
            if self._profile_payload_has_content(payload):
                break
        return best, best_request

    def _fight_count_with_current_fallback(
        self,
        client: DashenClient,
        token: str,
        logical_season: int,
        warnings: List[str],
    ) -> Tuple[Dict[str, Any], Optional[int]]:
        best: Dict[str, Any] = {}
        best_request: Optional[int] = logical_season
        for request_season in self._season_request_values(logical_season):
            try:
                payload = ensure_success(
                    client.fight_query_count(token, season=request_season),
                    operation=f"查询 S{logical_season} 角斗赛季资料",
                )
            except Exception as exc:
                warnings.append(f"S{logical_season} 角斗资料失败：{type(exc).__name__}")
                continue
            best, best_request = payload, request_season
            if self._fight_payload_has_content(payload):
                break
        return best, best_request

    @staticmethod
    def _fight_payload_has_content(payload: Any) -> bool:
        data = payload_data(payload)
        return bool(data.get("roleTypeCountData") or data.get("heroUseSummaryList"))

    @staticmethod
    def _safe_count_info(
        client: DashenClient,
        token: str,
        mode: str,
        request_season: Optional[int],
    ) -> Dict[str, Any]:
        try:
            return client.query_count_info(token, mode, season=request_season)
        except Exception:
            return {}

    def season_status(self) -> Dict[str, Any]:
        """返回当前后端采用的赛季号以及来源；默认首次玩家查询后会自动更新。"""
        return {
            "current_season": self.current_season,
            "source": self._season_source,
            "automatic": not self._season_explicit,
            "detection_attempted": self._season_detection_attempted,
        }

    def _ensure_current_season(self, client: DashenClient, token: str) -> int:
        """自动识别大神当前赛季；失败时保留 DEFAULT_DASHEN_CURRENT_SEASON 兜底。

        优先读取 payload 中显式 season/currentSeason 字段；若上游未暴露赛季号，
        再将“不传 season”的当前赛季 countInfo 与附近显式赛季做稳定内容指纹匹配。
        识别仅执行一次并缓存，不会每次请求重复探测。
        """
        if self._season_explicit or self._season_detection_attempted:
            return self.current_season
        with self._season_lock:
            if self._season_explicit or self._season_detection_attempted:
                return self.current_season
            self._season_detection_attempted = True
            try:
                payload = ensure_success(
                    client.query_count_info(token, "sport", season=None),
                    operation="自动识别当前赛季",
                )
            except Exception:
                return self.current_season

            detected = self._extract_current_season_from_payload(payload)
            if detected is not None:
                self.current_season = detected
                self._season_source = "upstream"
                return self.current_season

            fingerprint = self._season_fingerprint(payload)
            if fingerprint is None:
                return self.current_season

            base = self.current_season
            # 先检查下一赛季，再检查再下一赛季和当前/上一赛季；足够跨正常版本更新，
            # 又避免为了一个 UI 标签产生大量额外请求。
            candidates = [base + 1, base + 2, base, max(1, base - 1)]
            seen: set[int] = set()
            for candidate in candidates:
                if candidate in seen:
                    continue
                seen.add(candidate)
                try:
                    explicit = ensure_success(
                        client.query_count_info(token, "sport", season=candidate),
                        operation=f"探测 S{candidate}",
                    )
                except Exception:
                    continue
                if self._season_fingerprint(explicit) == fingerprint:
                    self.current_season = candidate
                    self._season_source = "matched_current_payload"
                    return self.current_season
            return self.current_season

    @classmethod
    def _extract_current_season_from_payload(cls, payload: Any) -> Optional[int]:
        preferred = {
            "currentseason", "currentseasonid", "currentseasonno",
            "currentseasonnum", "currentseasonnumber",
        }
        generic = {"season", "seasonid", "seasonno", "seasonnum", "seasonnumber", "seasonindex"}

        def parse(value: Any) -> Optional[int]:
            if isinstance(value, bool):
                return None
            if isinstance(value, (int, float)):
                number = int(value)
                return number if 1 <= number <= 99 else None
            text = str(value or "").strip().upper()
            if text.startswith("S"):
                text = text[1:]
            try:
                number = int(float(text))
            except (TypeError, ValueError):
                return None
            return number if 1 <= number <= 99 else None

        def walk(obj: Any, wanted: set[str], depth: int = 0) -> Optional[int]:
            if depth > 4:
                return None
            if isinstance(obj, Mapping):
                for key, value in obj.items():
                    normalized = str(key).replace("_", "").replace("-", "").lower()
                    if normalized in wanted:
                        found = parse(value)
                        if found is not None:
                            return found
                for value in obj.values():
                    found = walk(value, wanted, depth + 1)
                    if found is not None:
                        return found
            elif isinstance(obj, list):
                for value in obj[:20]:
                    found = walk(value, wanted, depth + 1)
                    if found is not None:
                        return found
            return None

        return walk(payload, preferred) or walk(payload_data(payload), generic)

    @staticmethod
    def _season_fingerprint(payload: Any) -> Optional[Tuple[Any, ...]]:
        try:
            matches = extract_match_entries(payload, "matchList", "recentMatchList")
            match_ids = tuple(str(row.get("matchId") or "") for row in matches[:12] if row.get("matchId"))
            roles = extract_competitive_roles(payload)
            role_sig = tuple(
                (
                    str(role.get("role_type") or ""),
                    int(role.get("match_sum") or 0),
                    str(role.get("win_rate") or ""),
                    str(((role.get("current") or {}).get("rank_name_raw") or (role.get("current") or {}).get("rank_name") or "")),
                    int(((role.get("current") or {}).get("rank_sub_tier") or 0)),
                )
                for role in roles
            )
            heroes = extract_hero_stats(payload).get("heroes") or []
            hero_sig = tuple(
                (str(hero.get("hero_guid") or ""), int(hero.get("match_sum") or 0))
                for hero in heroes[:12]
            )
            if not match_ids and not role_sig and not hero_sig:
                return None
            return (match_ids, role_sig, hero_sig)
        except Exception:
            return None

    def _season_request_values(self, logical_season: int) -> Sequence[Optional[int]]:
        # 当前赛季在大神不同版本中有“省略 season”和“显式 season”两种行为，两个都兼容。
        if int(logical_season) == self.current_season:
            return (None, int(logical_season))
        return (int(logical_season),)

    def _resolve_seasons(
        self,
        season_count: int,
        start_season: int | None,
        end_season: int | None,
    ) -> List[int]:
        if start_season is not None or end_season is not None:
            start = int(start_season if start_season is not None else end_season)
            end = int(end_season if end_season is not None else start_season)
            if start <= 0 or end <= 0:
                raise ValueError("赛季号必须大于 0")
            if start > end:
                start, end = end, start
            return list(range(end, start - 1, -1))
        count = max(1, int(season_count))
        start = max(1, self.current_season - count + 1)
        return list(range(self.current_season, start - 1, -1))

    @staticmethod
    def _normalize_fight_modes(modes: Sequence[str]) -> List[str]:
        aliases = {
            "quickfight": "QuickFight",
            "quick": "QuickFight",
            "角斗快速": "QuickFight",
            "leisurefight": "LeisureFight",
            "leisure": "LeisureFight",
            "休闲角斗": "LeisureFight",
            "sportfight": "SportFight",
            "sport": "SportFight",
            "竞技角斗": "SportFight",
        }
        out: List[str] = []
        for mode in modes:
            raw = str(mode or "").strip()
            normalized = aliases.get(raw.lower(), aliases.get(raw, raw))
            if normalized not in DEFAULT_FIGHT_MODES:
                raise ValueError(
                    f"不支持的角斗模式：{mode!r}；仅支持 QuickFight/LeisureFight/SportFight"
                )
            if normalized not in out:
                out.append(normalized)
        if not out:
            raise ValueError("fight_modes 不能为空")
        return out

    @staticmethod
    def _normalize_modes(modes: Sequence[str]) -> List[str]:
        out: List[str] = []
        for mode in modes:
            raw = str(mode or "").strip().lower()
            normalized = _MODE_ALIASES.get(raw, raw)
            if normalized not in {"sport", "leisure"}:
                raise ValueError(f"不支持的模式：{mode!r}；仅支持 sport/leisure（竞技/快速）")
            if normalized not in out:
                out.append(normalized)
        if not out:
            raise ValueError("modes 不能为空")
        return out

    @staticmethod
    def _profile_payload_has_content(payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        data = payload.get("data", payload)
        if not isinstance(data, dict):
            return False
        for key in (
            "guideCountData",
            "matchList",
            "recentMatchList",
            "presetsHeroUseSummaryList",
            "v6HeroUseSummaryList",
            "openHeroUseSummaryList",
            "presetsHeroList",
            "openHeroList",
            "userProvinceRankList",
        ):
            if isinstance(data.get(key), list) and data.get(key):
                return True
        for key in ("presetsSummaryData", "openSummaryData", "recentMatchCount", "gameAction"):
            value = data.get(key)
            if isinstance(value, dict) and any(v not in (None, 0, "", [], {}) for v in value.values()):
                return True
        return False

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    def _resolve_target(self, battletag: str | None, customer_token: str | None) -> ResolvedPlayer:
        token = str(customer_token or "").strip()
        if token:
            query = str(battletag or "customer_token").strip()
            return ResolvedPlayer(
                query=query,
                full_id=str(battletag or ""),
                bnet_id="",
                customer_token=token,
            )
        if not battletag:
            raise ValueError("battletag 或 customer_token 至少提供一个")
        return self.resolve_player(str(battletag))
