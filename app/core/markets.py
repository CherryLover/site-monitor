# ============================================================================
#  markets —— 市场缺口：某个语言的搜索需求在增长，站点却没有这个语言的版本
#
#  单个长尾词的展示往往过不了逐词门槛，但同一语言的词合起来就是一个清楚的信号。
#  做法：按国家 → 语言汇总非品牌展示；已有的语言版本从 sitemap 路径推断（/zh/、/id/…）；
#  需求够大、排名靠后、又没有对应版本时，生成一条机会。
# ============================================================================
from __future__ import annotations

import re
from collections import defaultdict
from urllib.parse import urlparse

from app.db import DB

MIN_IMPR = 20
MIN_POSITION = 20.0

# Search Console 的国家代码（ISO 3166-1 alpha-3）→ 主要语言；没列出的国家不参与判断
COUNTRY_LANG = {
    "deu": "de", "aut": "de", "che": "de",
    "fra": "fr", "bel": "fr",
    "esp": "es", "mex": "es", "col": "es", "arg": "es", "per": "es", "chl": "es", "ven": "es", "ecu": "es",
    "bra": "pt", "prt": "pt",
    "rus": "ru", "blr": "ru", "kaz": "ru", "ukr": "uk",
    "ita": "it", "nld": "nl", "pol": "pl", "tur": "tr", "vnm": "vi", "tha": "th",
    "jpn": "ja", "kor": "ko", "idn": "id", "mys": "ms",
    "chn": "zh", "twn": "zh", "hkg": "zh",
    "sau": "ar", "are": "ar", "egy": "ar", "dza": "ar", "mar": "ar",
    "usa": "en", "gbr": "en", "can": "en", "aus": "en", "ind": "en", "nzl": "en", "irl": "en",
}  # fmt: skip
LANG_NAME = {
    "de": "德语", "fr": "法语", "es": "西班牙语", "pt": "葡萄牙语", "ru": "俄语", "uk": "乌克兰语",
    "it": "意大利语", "nl": "荷兰语", "pl": "波兰语", "tr": "土耳其语", "vi": "越南语", "th": "泰语",
    "ja": "日语", "ko": "韩语", "id": "印尼语", "ms": "马来语", "zh": "中文", "ar": "阿拉伯语", "en": "英语",
}  # fmt: skip
LOCALE_SEGMENT = re.compile(r"^([a-z]{2})(?:[-_][a-z]{2,4})?$", re.I)


def covered_languages(db: DB, site_id: int) -> set[str]:
    """站点已有的语言：根目录算英语，再加上 sitemap 里出现过的语言前缀目录。"""
    langs = {"en"}
    for r in db.all("SELECT url FROM inspections WHERE site_id = ?", site_id):
        first = urlparse(r["url"]).path.strip("/").split("/")[0]
        m = LOCALE_SEGMENT.match(first)
        if m and m.group(1).lower() in LANG_NAME:
            langs.add(m.group(1).lower())
    return langs


def language_demand(db: DB, site_id: int, lo: str, hi: str, is_brand) -> dict[str, dict]:
    rows = db.all(
        "SELECT query, country, SUM(clicks) c, SUM(impressions) i, SUM(position * impressions) pw "
        "FROM gsc_country WHERE site_id = ? AND date BETWEEN ? AND ? GROUP BY query, country",
        site_id,
        lo,
        hi,
    )
    out: dict[str, dict] = defaultdict(
        lambda: {
            "clicks": 0,
            "impressions": 0,
            "pos_w": 0.0,
            "countries": defaultdict(int),
            "queries": defaultdict(int),
        }
    )
    for r in rows:
        lang = COUNTRY_LANG.get(r["country"])
        if not lang or is_brand(r["query"]):
            continue
        m = out[lang]
        m["clicks"] += r["c"]
        m["impressions"] += r["i"]
        m["pos_w"] += r["pw"]
        m["countries"][r["country"].upper()] += r["i"]
        m["queries"][r["query"]] += r["i"]
    return out


def market_rule(db: DB, site: dict, lo: str, hi: str, is_brand) -> list[dict]:
    have = covered_languages(db, site["id"])
    items = []
    for lang, m in language_demand(db, site["id"], lo, hi, is_brand).items():
        if lang in have or m["impressions"] < MIN_IMPR:
            continue
        pos = round(m["pos_w"] / m["impressions"], 1)
        if pos <= MIN_POSITION:
            continue
        name = LANG_NAME[lang]
        countries = "、".join(c for c, _ in sorted(m["countries"].items(), key=lambda kv: -kv[1])[:3])
        top = "、".join(q for q, _ in sorted(m["queries"].items(), key=lambda kv: -kv[1])[:3])
        existing = "、".join(f"/{x}/" for x in sorted(have - {"en"})) or "现有页面"
        items.append(
            {
                "key": f"market:{lang}",
                "type": "market",
                "target": f"{name}市场没有对应版本",
                "evidence": f"{countries} 共 {m['impressions']} 展示 · 平均排名 {pos} · {m['clicks']} 点击 · 热门词：{top}",
                "action": f"参照 {existing} 新增 /{lang}/ 版本，先翻译热门词对应的落地页。",
                "impact": "高" if m["impressions"] >= 100 else "中",
            }
        )
    return items
