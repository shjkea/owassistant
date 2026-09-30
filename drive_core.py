# -*- coding: utf-8 -*-
"""守望先锋竞逐计算器 - 纯计算核心。

这个文件故意不包含任何 GUI 代码。前端（PySide6 / CustomTkinter / WebView）
只需要调用本文件公开函数，不应复制或重写计分 / DP 逻辑。

主要入口：
    load_rules()
    save_rules()
    predict()
    build_history()
    replay_history()
    apply_match()

所有面向前端的高层接口都返回普通 dict/list，便于直接绑定到 UI。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
import copy
import json
import math
import os
import re
import sys


# ----------------------------- 数据模型 -----------------------------

@dataclass(frozen=True)
class ModeRule:
    name: str
    win_points: int
    group_bonus: int
    loss_penalties: Tuple[int, ...]


@dataclass(frozen=True)
class DriveRules:
    checkpoints: Tuple[int, ...]
    modes: Dict[str, ModeRule]
    dp_max_games: int = 5000
    dp_epsilon: float = 1e-13


@dataclass(frozen=True)
class MatchRecord:
    result: str              # "W" / "L"
    grouped: bool = False    # False=单排, True=组排
    mode: str = "normal"    # "normal" / "duel"


BUILTIN_CONFIG: Dict[str, Any] = {
    "version": 1,
    "checkpoints": [500, 1000, 1600, 2200, 3000, 4000],
    "modes": {
        "normal": {
            "name": "普通竞技",
            "win_points": 210,
            "group_bonus": 50,
            "loss_penalties": [0, 21, 21, 42, 42, 63],
        },
        "duel": {
            "name": "决斗 / 角斗",
            "win_points": 265,
            "group_bonus": 50,
            "loss_penalties": [0, 27, 27, 53, 53, 80],
        },
    },
    "dp": {"max_games": 5000, "epsilon": 1e-13},
}


def _bundled_config_path() -> Path:
    """项目/打包资源中的默认规则配置。"""
    return Path(__file__).with_name("config.json")


def _default_config_path() -> Path:
    """默认可写规则路径。

    源码运行时继续使用项目根目录的 config.json，保持现有开发行为；
    PyInstaller 冻结后改写到用户 LocalAppData，避免 onefile 临时目录或
    Program Files 导致规则无法持久化。
    """
    if getattr(sys, "frozen", False):
        if os.name == "nt":
            root = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
            if root:
                return Path(root) / "OWDriveSplit" / "config.json"
        xdg = os.getenv("XDG_CONFIG_HOME")
        if xdg:
            return Path(xdg) / "ow_drive_split" / "config.json"
        return Path.home() / ".config" / "ow_drive_split" / "config.json"
    return _bundled_config_path()


def _validate_nonnegative_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} 必须是整数")
    try:
        iv = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是整数") from exc
    if iv < 0:
        raise ValueError(f"{name} 不能小于 0")
    return iv


def rules_from_dict(data: Dict[str, Any]) -> DriveRules:
    """把 config.json 风格的 dict 转成已校验的规则对象。"""
    cps_raw = data.get("checkpoints")
    if not isinstance(cps_raw, list) or not cps_raw:
        raise ValueError("checkpoints 必须是非空数组")
    cps = tuple(_validate_nonnegative_int(x, "节点") for x in cps_raw)
    if cps[0] <= 0 or tuple(sorted(set(cps))) != cps:
        raise ValueError("节点必须是严格递增的正整数")

    modes_raw = data.get("modes")
    if not isinstance(modes_raw, dict) or not modes_raw:
        raise ValueError("modes 必须是非空对象")

    modes: Dict[str, ModeRule] = {}
    for key, raw in modes_raw.items():
        if not isinstance(raw, dict):
            raise ValueError(f"模式 {key} 配置格式错误")
        name = str(raw.get("name", key))
        win = _validate_nonnegative_int(raw.get("win_points"), f"{name} win_points")
        if win <= 0:
            raise ValueError(f"{name} win_points 必须大于 0")
        group = _validate_nonnegative_int(raw.get("group_bonus", 0), f"{name} group_bonus")
        loss_raw = raw.get("loss_penalties")
        if not isinstance(loss_raw, list):
            raise ValueError(f"{name} loss_penalties 必须是数组")
        loss = tuple(_validate_nonnegative_int(x, f"{name} loss_penalties") for x in loss_raw)
        if len(loss) != len(cps):
            raise ValueError(f"{name} 败场扣分数量必须等于节点数量 {len(cps)}")
        modes[str(key)] = ModeRule(name, win, group, loss)

    if "normal" not in modes:
        raise ValueError("至少必须存在 normal 模式")

    dp = data.get("dp") or {}
    max_games = int(dp.get("max_games", 5000))
    eps = float(dp.get("epsilon", 1e-13))
    if max_games <= 0:
        raise ValueError("dp.max_games 必须大于 0")
    if not 0 < eps < 1:
        raise ValueError("dp.epsilon 必须在 0 和 1 之间")

    return DriveRules(cps, modes, max_games, eps)


def rules_to_dict(rules: DriveRules) -> Dict[str, Any]:
    return {
        "version": 1,
        "checkpoints": list(rules.checkpoints),
        "modes": {
            key: {
                "name": rule.name,
                "win_points": rule.win_points,
                "group_bonus": rule.group_bonus,
                "loss_penalties": list(rule.loss_penalties),
            }
            for key, rule in rules.modes.items()
        },
        "dp": {
            "max_games": rules.dp_max_games,
            "epsilon": rules.dp_epsilon,
        },
    }


def load_rules(path: Optional[str | Path] = None) -> DriveRules:
    """读取规则。默认读取 drive_core.py 同目录下的 config.json。

    如果默认 config.json 不存在，会使用内置同值配置，避免程序直接崩溃。
    如果用户显式传入 path 而文件不存在，则抛 FileNotFoundError。
    """
    p = Path(path) if path is not None else _default_config_path()
    if p.exists():
        with p.open("r", encoding="utf-8") as f:
            return rules_from_dict(json.load(f))
    if path is not None:
        raise FileNotFoundError(p)

    # 冻结版首次启动时用户目录还没有 config.json；优先读取随程序打包的
    # config.json 作为初始规则，确保发布包与源码当前配置完全一致。
    if getattr(sys, "frozen", False):
        bundled = _bundled_config_path()
        if bundled.exists():
            with bundled.open("r", encoding="utf-8") as f:
                return rules_from_dict(json.load(f))

    return rules_from_dict(copy.deepcopy(BUILTIN_CONFIG))


def save_rules(rules: DriveRules, path: Optional[str | Path] = None) -> Path:
    p = Path(path) if path is not None else _default_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(rules_to_dict(rules), f, ensure_ascii=False, indent=2)
        f.write("\n")
    return p


def default_rules() -> DriveRules:
    return rules_from_dict(copy.deepcopy(BUILTIN_CONFIG))


# ----------------------------- 基础计分 -----------------------------

def _normalize_result(result: str) -> str:
    x = str(result).strip()
    mapping = {
        "+": "W", "W": "W", "w": "W", "胜": "W", "赢": "W", "1": "W",
        "-": "L", "L": "L", "l": "L", "负": "L", "输": "L", "0": "L",
    }
    if x not in mapping:
        raise ValueError(f"未知胜负值：{result!r}")
    return mapping[x]


def _normalize_mode(mode: str, rules: DriveRules) -> str:
    x = str(mode).strip().lower()
    aliases = {
        "普通": "normal", "普通竞技": "normal", "normal": "normal",
        "决斗": "duel", "角斗": "duel", "决斗/角斗": "duel", "duel": "duel", "stadium": "duel",
    }
    key = aliases.get(x, x)
    if key not in rules.modes:
        raise ValueError(f"未知模式：{mode!r}；可用模式：{', '.join(rules.modes)}")
    return key


def checkpoint_floor(score: int, checkpoints: Sequence[int]) -> int:
    floor = 0
    for cp in checkpoints:
        if score >= cp:
            floor = cp
        else:
            break
    return floor


def next_checkpoint(score: int, checkpoints: Sequence[int]) -> Optional[int]:
    return next((cp for cp in checkpoints if cp > score), None)


def band_index(score: int, checkpoints: Sequence[int]) -> int:
    """返回 loss_penalties 的区间索引。

    以默认节点为例：
      [0,500) -> 0
      [500,1000) -> 1
      [1000,1600) -> 2
      ...
    """
    idx = 0
    for cp in checkpoints:
        if score >= cp:
            idx += 1
        else:
            break
    return min(idx, len(checkpoints) - 1)


def apply_match(
    score: int,
    result: str,
    grouped: bool = False,
    mode: str = "normal",
    rules: Optional[DriveRules] = None,
) -> Tuple[int, int]:
    """应用一局比赛，返回 (赛后分数, 本局实际变化)。"""
    rules = rules or load_rules()
    result = _normalize_result(result)
    mode = _normalize_mode(mode, rules)
    final_cp = rules.checkpoints[-1]
    score = max(0, min(int(score), final_cp))
    if score >= final_cp:
        return score, 0

    rule = rules.modes[mode]
    if result == "W":
        gain = rule.win_points + (rule.group_bonus if grouped else 0)
        new_score = min(final_cp, score + gain)
        return new_score, new_score - score

    idx = band_index(score, rules.checkpoints)
    penalty = rule.loss_penalties[idx]
    floor = checkpoint_floor(score, rules.checkpoints)
    new_score = max(floor, score - penalty)
    return new_score, new_score - score


def score_rule_at(
    score: int,
    grouped: bool = False,
    mode: str = "normal",
    rules: Optional[DriveRules] = None,
) -> Dict[str, Any]:
    """给 UI 展示“当前分数这一局赢/输会怎样”。"""
    rules = rules or load_rules()
    mode = _normalize_mode(mode, rules)
    rule = rules.modes[mode]
    idx = band_index(score, rules.checkpoints)
    floor = checkpoint_floor(score, rules.checkpoints)
    win_after, win_delta = apply_match(score, "W", grouped, mode, rules)
    loss_after, loss_delta = apply_match(score, "L", grouped, mode, rules)
    return {
        "score": int(score),
        "mode": mode,
        "mode_name": rule.name,
        "grouped": bool(grouped),
        "band_index": idx,
        "checkpoint_floor": floor,
        "next_checkpoint": next_checkpoint(score, rules.checkpoints),
        "win_nominal": rule.win_points + (rule.group_bonus if grouped else 0),
        "loss_nominal": -rule.loss_penalties[idx],
        "win_delta_actual": win_delta,
        "loss_delta_actual": loss_delta,
        "win_score": win_after,
        "loss_score": loss_after,
    }


# ----------------------------- 历史战绩 -----------------------------

def parse_result_sequence(text: str) -> List[str]:
    """接受 +-、WL、胜负，忽略常见分隔符。"""
    mapping = {
        "+": "W", "w": "W", "W": "W", "胜": "W", "赢": "W", "1": "W",
        "-": "L", "l": "L", "L": "L", "负": "L", "输": "L", "0": "L",
    }
    ignored = set(" \t\r\n,，;；/|_")
    result: List[str] = []
    bad: List[str] = []
    for ch in str(text):
        if ch in mapping:
            result.append(mapping[ch])
        elif ch in ignored:
            continue
        else:
            bad.append(ch)
    if bad:
        raise ValueError("战绩串包含无法识别的字符：" + " ".join(sorted(set(bad))))
    if not result:
        raise ValueError("战绩串为空。示例：+-++-++++-++++-++")
    return result


def parse_index_ranges(text: str, total: int, name: str = "局号") -> set[int]:
    """解析 1-based 局号，如 3-9,12,15-17。"""
    text = str(text or "").strip()
    if not text:
        return set()
    text = (
        text.replace("，", ",")
        .replace("－", "-")
        .replace("—", "-")
        .replace("~", "-")
        .replace("～", "-")
    )
    out: set[int] = set()
    for part in [x.strip() for x in text.split(",") if x.strip()]:
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > b:
                a, b = b, a
            if a < 1 or b > total:
                raise ValueError(f"{name} {part} 超出 1~{total} 局范围")
            out.update(range(a, b + 1))
        elif part.isdigit():
            n = int(part)
            if not 1 <= n <= total:
                raise ValueError(f"{name} {n} 超出 1~{total} 局范围")
            out.add(n)
        else:
            raise ValueError(f"无法解析{name}：{part}；格式示例 3-9,12,15-17")
    return out


def compress_ranges(indices: Iterable[int]) -> str:
    vals = sorted(set(int(x) for x in indices))
    if not vals:
        return ""
    groups: List[str] = []
    start = prev = vals[0]
    for n in vals[1:]:
        if n == prev + 1:
            prev = n
        else:
            groups.append(str(start) if start == prev else f"{start}-{prev}")
            start = prev = n
    groups.append(str(start) if start == prev else f"{start}-{prev}")
    return ",".join(groups)


def build_history(
    sequence: str,
    group_ranges: str = "",
    duel_ranges: str = "",
) -> List[MatchRecord]:
    """从紧凑输入构造历史记录。

    示例：
        build_history("+-++-++++-++++-++", group_ranges="3-9")
    """
    seq = parse_result_sequence(sequence)
    groups = parse_index_ranges(group_ranges, len(seq), "组排局号")
    duels = parse_index_ranges(duel_ranges, len(seq), "决斗局号")
    return [
        MatchRecord(result=r, grouped=(i in groups), mode=("duel" if i in duels else "normal"))
        for i, r in enumerate(seq, start=1)
    ]


def _coerce_record(record: MatchRecord | Dict[str, Any] | Sequence[Any], rules: DriveRules) -> MatchRecord:
    if isinstance(record, MatchRecord):
        return MatchRecord(_normalize_result(record.result), bool(record.grouped), _normalize_mode(record.mode, rules))
    if isinstance(record, dict):
        return MatchRecord(
            _normalize_result(record.get("result", "")),
            bool(record.get("grouped", False)),
            _normalize_mode(record.get("mode", "normal"), rules),
        )
    if isinstance(record, (tuple, list)) and len(record) >= 3:
        return MatchRecord(_normalize_result(record[0]), bool(record[1]), _normalize_mode(record[2], rules))
    raise ValueError(f"无法解析历史记录：{record!r}")


def records_to_dicts(records: Iterable[MatchRecord]) -> List[Dict[str, Any]]:
    return [
        {
            "result": r.result,
            "grouped": r.grouped,
            "queue": "group" if r.grouped else "solo",
            "mode": r.mode,
        }
        for r in records
    ]


def replay_history(
    records: Iterable[MatchRecord | Dict[str, Any] | Sequence[Any]],
    start_score: int = 0,
    rules: Optional[DriveRules] = None,
) -> Dict[str, Any]:
    """逐局回放历史记录，返回 UI 可直接使用的结构化结果。"""
    rules = rules or load_rules()
    final_cp = rules.checkpoints[-1]
    score = max(0, min(int(start_score), final_cp))
    start_score = score

    rows: List[Dict[str, Any]] = []
    wins = losses = solo = grouped_n = 0
    group_indices: List[int] = []
    duel_indices: List[int] = []
    sequence_chars: List[str] = []

    parsed = [_coerce_record(r, rules) for r in records]
    for game, rec in enumerate(parsed, start=1):
        before = score
        score, delta = apply_match(before, rec.result, rec.grouped, rec.mode, rules)
        won = rec.result == "W"
        wins += int(won)
        losses += int(not won)
        solo += int(not rec.grouped)
        grouped_n += int(rec.grouped)
        if rec.grouped:
            group_indices.append(game)
        if rec.mode == "duel":
            duel_indices.append(game)
        sequence_chars.append("+" if won else "-")

        mode_rule = rules.modes[rec.mode]
        rows.append({
            "game": game,
            "result": rec.result,
            "result_text": "胜" if won else "负",
            "grouped": rec.grouped,
            "queue": "group" if rec.grouped else "solo",
            "queue_text": "组排" if rec.grouped else "单排",
            "mode": rec.mode,
            "mode_name": mode_rule.name,
            "score_before": before,
            "delta": delta,
            "score_after": score,
            "checkpoint_floor_after": checkpoint_floor(score, rules.checkpoints),
        })

    total = len(parsed)
    return {
        "start_score": start_score,
        "current_score": score,
        "final_checkpoint": final_cp,
        "completed": score >= final_cp,
        "summary": {
            "games": total,
            "wins": wins,
            "losses": losses,
            "win_rate": (wins / total if total else 0.0),
            "win_rate_percent": (wins / total * 100.0 if total else 0.0),
            "solo_games": solo,
            "group_games": grouped_n,
            "group_ranges": compress_ranges(group_indices),
            "duel_ranges": compress_ranges(duel_indices),
            "sequence": "".join(sequence_chars),
        },
        "rows": rows,
    }


# ----------------------------- DP 预测 -----------------------------

def dp_hitting_distribution(
    start_score: int,
    target: int,
    win_rate: float,
    grouped: bool = False,
    mode: str = "normal",
    rules: Optional[DriveRules] = None,
    max_games: Optional[int] = None,
    epsilon: Optional[float] = None,
) -> Tuple[float, List[float]]:
    """计算首次达到 target 的期望局数与累计完成概率 CDF。

    cdf[n] = n 局内至少一次达到 target 的概率。
    """
    rules = rules or load_rules()
    mode = _normalize_mode(mode, rules)
    start_score = int(start_score)
    target = int(target)
    p = float(win_rate)
    if not 0.0 <= p <= 1.0:
        raise ValueError("胜率必须在 0~1 之间，例如 55% 请传 0.55")
    if target <= start_score:
        return 0.0, [1.0]
    if target > rules.checkpoints[-1]:
        raise ValueError(f"目标分不能高于最终节点 {rules.checkpoints[-1]}")
    if p == 0.0:
        n = max_games or rules.dp_max_games
        return math.inf, [0.0] * (n + 1)

    max_games = int(max_games or rules.dp_max_games)
    eps = float(epsilon if epsilon is not None else rules.dp_epsilon)
    q = 1.0 - p

    states: Dict[int, float] = {max(0, start_score): 1.0}
    cdf: List[float] = [0.0]
    absorbed = 0.0
    expected = 0.0

    for _game in range(1, max_games + 1):
        # E[T] = sum_{n>=0} P(T > n)
        expected += sum(states.values())
        nxt: Dict[int, float] = {}
        newly_absorbed = 0.0

        for score, prob in states.items():
            ws, _ = apply_match(score, "W", grouped, mode, rules)
            if ws >= target:
                newly_absorbed += prob * p
            else:
                nxt[ws] = nxt.get(ws, 0.0) + prob * p

            if q > 0.0:
                ls, _ = apply_match(score, "L", grouped, mode, rules)
                if ls >= target:
                    newly_absorbed += prob * q
                else:
                    nxt[ls] = nxt.get(ls, 0.0) + prob * q

        absorbed = min(1.0, absorbed + newly_absorbed)
        cdf.append(absorbed)

        # 只剪掉极小数值噪声，保持概率质量基本完整。
        states = {s: pr for s, pr in nxt.items() if pr > eps * 1e-4}
        if sum(states.values()) < eps:
            break

    if states and sum(states.values()) >= eps:
        expected = math.nan
    return expected, cdf


def probability_within(cdf: Sequence[float], games: int) -> float:
    games = int(games)
    if games < 0:
        return 0.0
    if not cdf:
        return 0.0
    return float(cdf[games] if games < len(cdf) else cdf[-1])


def quantile_game(cdf: Sequence[float], threshold: float) -> Optional[int]:
    threshold = float(threshold)
    if not 0 <= threshold <= 1:
        raise ValueError("threshold 必须在 0~1")
    for i, prob in enumerate(cdf):
        if prob + 1e-15 >= threshold:
            return i
    return None


def predict_target(
    start_score: int,
    target: int,
    win_rate: float,
    grouped: bool = False,
    mode: str = "normal",
    probability_games: Sequence[int] = (10, 15, 20, 25, 30, 35, 40),
    quantiles: Sequence[float] = (0.50, 0.80, 0.90, 0.95),
    rules: Optional[DriveRules] = None,
) -> Dict[str, Any]:
    rules = rules or load_rules()
    mode = _normalize_mode(mode, rules)
    expected, cdf = dp_hitting_distribution(start_score, target, win_rate, grouped, mode, rules)

    q_values = {
        f"{q:.4f}": quantile_game(cdf, q)
        for q in quantiles
    }
    probs = {
        str(int(n)): probability_within(cdf, int(n))
        for n in sorted(set(int(x) for x in probability_games if int(x) >= 0))
    }
    return {
        "start_score": int(start_score),
        "target": int(target),
        "win_rate": float(win_rate),
        "grouped": bool(grouped),
        "mode": mode,
        "expected_games": expected,
        "quantile_games": q_values,
        "probability_within": probs,
        "cdf": cdf,
    }


def predict(
    current_score: int,
    win_rate: float,
    grouped: bool = False,
    mode: str = "normal",
    probability_games: Sequence[int] = (10, 15, 20, 25, 30, 35, 40),
    rules: Optional[DriveRules] = None,
) -> Dict[str, Any]:
    """前端主入口：计算下一节点 + 最终节点预测。"""
    rules = rules or load_rules()
    mode = _normalize_mode(mode, rules)
    final_cp = rules.checkpoints[-1]
    score = max(0, min(int(current_score), final_cp))
    wr = float(win_rate)
    if not 0 <= wr <= 1:
        raise ValueError("win_rate 必须在 0~1，例如 55% 请传 0.55")

    nxt = next_checkpoint(score, rules.checkpoints)
    base = {
        "current_score": score,
        "win_rate": wr,
        "win_rate_percent": wr * 100.0,
        "grouped": bool(grouped),
        "mode": mode,
        "mode_name": rules.modes[mode].name,
        "final_checkpoint": final_cp,
        "next_checkpoint": nxt,
        "completed": nxt is None,
        "current_rule": score_rule_at(score, grouped, mode, rules),
        "targets": {},
    }
    if nxt is None:
        return base

    base["targets"]["next"] = predict_target(
        score, nxt, wr, grouped, mode, probability_games, rules=rules
    )
    if nxt != final_cp:
        base["targets"]["final"] = predict_target(
            score, final_cp, wr, grouped, mode, probability_games, rules=rules
        )
    else:
        base["targets"]["final"] = base["targets"]["next"]
    return base


# ----------------------------- 自检 -----------------------------

def self_test() -> Dict[str, Any]:
    """供开发 / 前端联调使用。失败会抛 AssertionError。"""
    rules = default_rules()

    # 1) 用户提供的固定验证样例
    records = build_history("+-++-++++-++++-++", group_ranges="3-9")
    replay = replay_history(records, start_score=0, rules=rules)
    assert replay["current_score"] == 2925, replay["current_score"]
    assert replay["summary"]["games"] == 17
    assert replay["summary"]["group_games"] == 7

    # 2) 默认普通败场区间
    checks = {
        100: 0,
        500: 0,     # 节点保底导致实际 delta 为 0
        600: -21,
        1200: -21,
        1700: -42,
        2500: -42,
        3100: -63,
    }
    for score, expected_delta in checks.items():
        _, delta = apply_match(score, "L", False, "normal", rules)
        assert delta == expected_delta, (score, delta, expected_delta)

    # 3) DP CDF 单调
    pred = predict(1920, 0.55, False, "normal", (10, 20, 30), rules)
    cdf = pred["targets"]["final"]["cdf"]
    assert all(a <= b + 1e-15 for a, b in zip(cdf, cdf[1:]))

    return {
        "ok": True,
        "sample_final_score": replay["current_score"],
        "sample_games": replay["summary"]["games"],
        "sample_group_games": replay["summary"]["group_games"],
    }


if __name__ == "__main__":
    print(json.dumps(self_test(), ensure_ascii=False, indent=2))
