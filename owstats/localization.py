# -*- coding: utf-8 -*-
"""Overwatch 国服常用名称本地化。

机器字段（game mode / role key 等）仍保留原值；本模块只负责生成稳定的 zh-CN
展示字段。未知的新值原样返回，避免错误翻译，同时 raw 始终由 parsers/service 保留。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict


def _key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    text = text.replace("’", "'").replace("–", "-").replace("—", "-")
    text = re.sub(r"[\s_\-]+", " ", text)
    return text.strip()


def _aliases(canonical: Dict[str, str], *extra: tuple[str, str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for name, zh in canonical.items():
        out[_key(name)] = zh
        out[_key(zh)] = zh
    for name, zh in extra:
        out[_key(name)] = zh
    return out


# 核心竞技排位。2025/2026 国服官方用语：青铜/白银/黄金/白金/翡翠/钻石/大师/宗师/英杰。
_RANKS = _aliases({
    "Bronze": "青铜",
    "Silver": "白银",
    "Gold": "黄金",
    "Platinum": "白金",
    "Emerald": "翡翠",
    "Diamond": "钻石",
    "Master": "大师",
    "Grandmaster": "宗师",
    "Champion": "英杰",
    "Unranked": "未定级",
    # 角斗领域赛阶
    "Rookie": "新秀",
    "Fighter": "斗士",
    "Contender": "斗士",
    "Elite": "精英",
    "Expert": "专家",
    "Pro": "专家",
    "All-Star": "全明星",
    "All Star": "全明星",
    "Legend": "传奇",
},
    ("GM", "宗师"), ("Grand Master", "宗师"), ("Top 500", "500强"),
)

_ROLES = _aliases({
    "tank": "重装",
    "damage": "输出",
    "dps": "输出",
    "support": "支援",
    "healer": "支援",
    "open": "开放职责",
},
    ("坦克", "重装"), ("辅助", "支援"), ("治疗", "支援"), ("开放", "开放职责"),
    # Dashen query_tool.json roleGuid.  Detail rows sometimes expose only the
    # numeric roleGuid instead of roleType, so treat these as first-class aliases.
    ("972777519512029264", "重装"),
    ("972777519512029262", "输出"),
    ("972777519512029265", "支援"),
)

# 大神接口中常见的业务模式值 + 游戏内模式英文名。
_GAME_MODES = _aliases({
    "sport": "竞技",
    "leisure": "快速",
    "SportPreset": "竞技 · 预设职责",
    "SportOpen": "竞技 · 开放职责",
    "QuickPreset": "快速 · 预设职责",
    "QuickOpen": "快速 · 开放职责",
    "SportFight": "角斗竞技",
    "QuickFight": "角斗快速",
    "LeisureFight": "角斗快速",
    "Competitive": "竞技",
    "Competitive Play": "竞技",
    "Quick Play": "快速",
    "Quickplay": "快速",
    "Stadium": "角斗领域",
})

_INSTANCE_TYPES = _aliases({
    "Control": "占领要点",
    "Escort": "运载目标",
    "Hybrid": "攻击/护送",
    "Push": "机动推进",
    "Flashpoint": "闪点作战",
    "Clash": "攻防阵线",
    "Assault": "攻防作战",
    "Deathmatch": "死斗",
    "Team Deathmatch": "团队死斗",
    "Capture the Flag": "勇夺锦旗",
    "Elimination": "决斗先锋",
    "Stadium": "角斗领域",
})

# 地图名称以国服官方译名为准。既覆盖当前核心地图，也覆盖近年仍可能出现在历史/街机数据中的地图。
_MAPS = _aliases({
    # 占领要点
    "Antarctic Peninsula": "南极半岛",
    "Busan": "釜山",
    "Ilios": "伊利奥斯",
    "Lijiang Tower": "漓江塔",
    "Nepal": "尼泊尔",
    "Oasis": "绿洲城",
    "Samoa": "萨摩亚",
    # 运载目标
    "Circuit Royal": "皇家赛道",
    "Dorado": "多拉多",
    "Havana": "哈瓦那",
    "Junkertown": "渣客镇",
    "Rialto": "里阿尔托",
    "Route 66": "66号公路",
    "Shambali Monastery": "香巴里寺院",
    "Watchpoint: Gibraltar": "监测站：直布罗陀",
    # 攻击/护送
    "Blizzard World": "暴雪世界",
    "Eichenwalde": "艾兴瓦尔德",
    "Hollywood": "好莱坞",
    "King's Row": "国王大道",
    "Midtown": "中城",
    "Numbani": "努巴尼",
    "Paraiso": "帕拉伊苏",
    "Paraíso": "帕拉伊苏",
    # 机动推进
    "Colosseo": "斗兽场",
    "Esperanca": "埃斯佩兰萨",
    "Esperança": "埃斯佩兰萨",
    "New Queen Street": "新皇后街",
    "Runasapi": "鲁纳塞彼",
    # 闪点作战
    "New Junk City": "新渣客城",
    "Suravasa": "苏拉瓦萨",
    "Aatlis": "阿特利斯",
    # 攻防阵线
    "Hanaoka": "花冈",
    "Throne of Anubis": "阿努比斯王座",
    # 角斗领域 / 2026 新内容
    "Lijiang Tower Night Market": "漓江塔夜市",
    "Night Market": "漓江塔夜市",
    "Neon Nexus": "霓虹枢纽",
    "Grand Mesa": "格兰德台地",
    "Watchpoint: Grand Mesa": "监测站：格兰德台地",
    # 历史/街机地图
    "Hanamura": "花村",
    "Horizon Lunar Colony": "地平线月球基地",
    "Paris": "巴黎",
    "Temple of Anubis": "阿努比斯神殿",
    "Volskaya Industries": "沃斯卡娅工业区",
    "Ayutthaya": "阿育陀耶",
    "Black Forest": "黑森林",
    "Castillo": "卡斯蒂略",
    "Chateau Guillard": "吉拉德堡",
    "Château Guillard": "吉拉德堡",
    "Ecopoint: Antarctica": "生态监测站：南极洲",
    "Kanezaka": "铁坂",
    "Malevento": "马莱温多",
    "Necropolis": "墓园",
    "Petra": "佩特拉",
})

# 英雄名称：覆盖当前阵容以及最近赛季新增英雄；中文输入本身也会被原样规范到国服名称。
_HEROES = _aliases({
    # 重装
    "D.Va": "D.Va",
    "Dva": "D.Va",
    "D.Mon": "D.Mon",
    "Dmon": "D.Mon",
    "Domina": "金驭",
    "Doomfist": "末日铁拳",
    "Hazard": "骇灾",
    "Junker Queen": "渣客女王",
    "Mauga": "毛加",
    "Orisa": "奥丽莎",
    "Ramattra": "拉玛刹",
    "Reinhardt": "莱因哈特",
    "Roadhog": "路霸",
    "Sigma": "西格玛",
    "Winston": "温斯顿",
    "Wrecking Ball": "破坏球",
    "Zarya": "查莉娅",
    # 输出
    "Anran": "安燃",
    "Ashe": "艾什",
    "Bastion": "堡垒",
    "Cassidy": "卡西迪",
    "Echo": "回声",
    "Emre": "埃姆雷",
    "Freja": "弗蕾娅",
    "Genji": "源氏",
    "Hanzo": "半藏",
    "Junkrat": "狂鼠",
    "Mei": "美",
    "Pharah": "法老之鹰",
    "Reaper": "死神",
    "Sierra": "西拉",
    "Shion": "死怨",
    "Syla": "西拉",
    "Sojourn": "索杰恩",
    "Soldier: 76": "士兵：76",
    "Soldier 76": "士兵：76",
    "Sombra": "黑影",
    "Symmetra": "秩序之光",
    "Torbjorn": "托比昂",
    "Torbjörn": "托比昂",
    "Tracer": "猎空",
    "Vendetta": "斩仇",
    "Venture": "探奇",
    "Widowmaker": "黑百合",
    # 支援
    "Ana": "安娜",
    "Baptiste": "巴蒂斯特",
    "Brigitte": "布丽吉塔",
    "Illari": "伊拉锐",
    "Jetpack Cat": "飞天猫",
    "Juno": "朱诺",
    "Kiriko": "雾子",
    "Lifeweaver": "生命之梭",
    "Lucio": "卢西奥",
    "Lúcio": "卢西奥",
    "Mercy": "天使",
    "Mizuki": "瑞稀",
    "Moira": "莫伊拉",
    "Wuyang": "无漾",
    "Zenyatta": "禅雅塔",
})


def _lookup(value: Any, table: Dict[str, str]) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return table.get(_key(raw), raw)


def localize_rank(value: Any) -> str:
    """段位名汉化，并保留诸如 Platinum IV / Grandmaster 2 的后缀。"""
    raw = str(value or "").strip()
    if not raw:
        return ""
    exact = _RANKS.get(_key(raw))
    if exact:
        return exact
    # 英文段位 + 分组数字/罗马数字
    m = re.match(r"^(.+?)[\s\-]+((?:[1-5])|(?:I{1,3}|IV|V))$", raw, flags=re.I)
    if m:
        base = _RANKS.get(_key(m.group(1)))
        if base:
            return f"{base} {m.group(2).upper()}"
    return raw


def localize_role(value: Any) -> str:
    return _lookup(value, _ROLES)


def localize_game_mode(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    exact = _GAME_MODES.get(_key(raw))
    if exact:
        return exact
    low = _key(raw)
    # 兼容大神偶尔拼接出的 mode 名称。
    if "fight" in low or "stadium" in low:
        if "sport" in low or "competitive" in low or "rank" in low:
            return "角斗竞技"
        return "角斗快速"
    if "sport" in low or "competitive" in low or "rank" in low:
        if "open" in low:
            return "竞技 · 开放职责"
        if "preset" in low or "role" in low:
            return "竞技 · 预设职责"
        return "竞技"
    if "quick" in low or "leisure" in low:
        if "open" in low:
            return "快速 · 开放职责"
        if "preset" in low or "role" in low:
            return "快速 · 预设职责"
        return "快速"
    return raw


def localize_instance_type(value: Any) -> str:
    return _lookup(value, _INSTANCE_TYPES)


def localize_map(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    # 去掉常见英文模式后缀后再尝试，例如 "King's Row - Hybrid"。
    exact = _MAPS.get(_key(raw))
    if exact:
        return exact
    for sep in (" - ", " | ", " / "):
        if sep in raw:
            head = raw.split(sep, 1)[0].strip()
            translated = _MAPS.get(_key(head))
            if translated:
                return translated
    return raw


def localize_hero(value: Any) -> str:
    return _lookup(value, _HEROES)


__all__ = [
    "localize_rank", "localize_role", "localize_game_mode", "localize_instance_type",
    "localize_map", "localize_hero",
]
