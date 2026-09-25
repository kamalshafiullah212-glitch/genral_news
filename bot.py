#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
General News Bot — Technology & Economy (جنرل نیوز بوټ — ټکنالوژۍ او اقتصاد)

One bot, all subjects except AI news:
    Technology / Mobile / Social / Big Tech / Security / Internet / Gaming /
    Tech Jobs / Afghanistan Tech  +  Economy / Forex / Markets / Crypto

Pipeline:
    RSS/News Sources -> Collection -> AI-News Filter -> Duplicate Removal
    -> Category Detection -> Priority -> Pashto Summary -> Telegram

Notes:
  * AI news (ChatGPT, Gemini, Claude, OpenAI, ...) is NEVER posted here —
    a separate AI News Bot handles that topic.
  * Secrets (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID) come only from
    environment variables (GitHub Secrets in CI). Nothing is hard-coded.
  * Dry-run: set DRY_RUN=1 to print posts instead of sending them.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import feedparser
import requests

# ---------------------------------------------------------------------------
# Configuration (environment only — no secrets in code)
# ---------------------------------------------------------------------------


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


CONFIG = {
    "token": os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
    "chat_id": os.environ.get("TELEGRAM_CHAT_ID", "").strip(),
    "dry_run": os.environ.get("DRY_RUN", "0").strip() in ("1", "true", "yes"),
    "max_posts_per_run": env_int("MAX_POSTS_PER_RUN", 3),
    "max_age_hours": env_int("MAX_AGE_HOURS", 36),
    "min_score": env_int("MIN_SCORE", 50),
    "state_file": os.environ.get("STATE_FILE", "state/posted.json").strip(),
    "fetch_timeout": env_int("FETCH_TIMEOUT", 20),
    "request_delay": 0.5,  # politeness delay between feed fetches
}

log = logging.getLogger("newsbot")

# ---------------------------------------------------------------------------
# News sources (verified live; tier: 3 = official newsroom, 2 = tech press,
# 1 = narrow aggregator query)
# ---------------------------------------------------------------------------
SOURCES: List[Dict[str, str]] = [
    {"name": "Apple Newsroom", "url": "https://www.apple.com/newsroom/rss-feed.rss", "tier": "3"},
    {"name": "Google Blog", "url": "https://blog.google/rss/", "tier": "3"},
    {"name": "Microsoft News", "url": "https://news.microsoft.com/feed/", "tier": "3"},
    {"name": "NVIDIA Blog", "url": "https://blogs.nvidia.com/feed/", "tier": "3"},
    {"name": "Cloudflare Blog", "url": "https://blog.cloudflare.com/rss/", "tier": "3"},
    {"name": "GSMA Newsroom", "url": "https://www.gsma.com/newsroom/feed/", "tier": "3"},
    {"name": "The Verge", "url": "https://www.theverge.com/rss/index.xml", "tier": "2"},
    {"name": "TechCrunch", "url": "https://techcrunch.com/feed/", "tier": "2"},
    {"name": "Ars Technica", "url": "https://feeds.arstechnica.com/arstechnica/index", "tier": "2"},
    {"name": "Engadget", "url": "https://www.engadget.com/rss.xml", "tier": "2"},
    {"name": "Wired", "url": "https://www.wired.com/feed/rss", "tier": "2"},
    {"name": "Android Authority", "url": "https://www.androidauthority.com/feed/", "tier": "2"},
    {"name": "GSMArena", "url": "https://www.gsmarena.com/rss-news-reviews.php3", "tier": "2"},
    {"name": "9to5Mac", "url": "https://9to5mac.com/feed/", "tier": "2"},
    {"name": "XDA", "url": "https://www.xda-developers.com/feed/", "tier": "2"},
    {"name": "PC Gamer", "url": "https://www.pcgamer.com/rss/", "tier": "2"},
    {"name": "Eurogamer", "url": "https://www.eurogamer.net/feed", "tier": "2"},
    {"name": "Polygon", "url": "https://www.polygon.com/rss/index.xml", "tier": "2"},
    {"name": "BleepingComputer", "url": "https://www.bleepingcomputer.com/feed/", "tier": "2"},
    {"name": "Krebs on Security", "url": "https://krebsonsecurity.com/feed/", "tier": "2"},
    {"name": "The Hacker News", "url": "https://feeds.feedburner.com/TheHackersNews", "tier": "2"},
    {"name": "Google News (Afghanistan Tech)",
     "url": "https://news.google.com/rss/search?q=Afghanistan+(internet+OR+telecom+OR+digital)&hl=en-US&gl=US&ceid=US:en",
     "tier": "1"},
    {"name": "Google News (Tech Jobs)",
     "url": "https://news.google.com/rss/search?q=(remote+OR+freelance)+tech+jobs+programming&hl=en-US&gl=US&ceid=US:en",
     "tier": "1"},
    # economy / forex sources (verified live)
    {"name": "Investing.com", "url": "https://www.investing.com/rss/news.rss", "tier": "2"},
    {"name": "CNBC Markets", "url": "https://www.cnbc.com/id/20910258/device/rss/rss.html", "tier": "2"},
    {"name": "CNBC Top News", "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html", "tier": "2"},
    {"name": "MarketWatch", "url": "https://feeds.content.dowjones.io/public/rss/mw_topstories", "tier": "2"},
    {"name": "FXStreet", "url": "https://www.fxstreet.com/rss/news", "tier": "2"},
    {"name": "ForexLive", "url": "https://www.forexlive.com/feed/news", "tier": "2"},
    {"name": "CoinDesk", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/", "tier": "2"},
    {"name": "Cointelegraph", "url": "https://cointelegraph.com/rss", "tier": "2"},
    {"name": "Yahoo Finance", "url": "https://finance.yahoo.com/news/rssindex", "tier": "2"},
    {"name": "Google News (Afghanistan Economy)",
     "url": "https://news.google.com/rss/search?q=Afghanistan+(economy+OR+afghani+OR+currency+OR+bank)&hl=en-US&gl=US&ceid=US:en",
     "tier": "1"},
    {"name": "Google News (Forex)",
     "url": "https://news.google.com/rss/search?q=(forex+OR+%22exchange+rate%22+OR+%22central+bank%22)+dollar&hl=en-US&gl=US&ceid=US:en",
     "tier": "1"},
]


# ---------------------------------------------------------------------------
# AI news blocklist — if AI is the main topic, the item is skipped entirely
# (a separate AI News Bot handles that subject)
# ---------------------------------------------------------------------------
AI_STRONG = [
    "chatgpt", "openai", "anthropic", "claude", "gemini", "grok", "qwen",
    "llama", "generative ai", "genai", "ai model", "ai models", "ai tool",
    "ai tools", "ai chatbot", "ai chatbots", "ai assistant", "ai agent",
    "ai agents", "gpt-3", "gpt-4", "gpt-5", "gpt4", "gpt5",
    "large language model", "large language models", "llm", "llms",
    "artificial intelligence", "machine learning", "deep learning",
    "neural network", "neural networks", "midjourney", "stable diffusion",
    "dall-e", "sora ai", "copilot", "deepseek", "mistral ai", "hugging face",
    "deepmind", "text-to-image", "text-to-video", "ai-generated",
    "ai generated", "ai-powered", "ai powered", "agi",
    "ai system", "ai systems", "chatbot",
]
AI_STANDALONE = re.compile(r"\bAI\b")

# ---------------------------------------------------------------------------
# Categories (keywords matched on title + summary, lower-cased)
# ---------------------------------------------------------------------------
CATEGORIES: Dict[str, Dict] = {
    "cybersecurity": {
        "label_ps": "سایبر امنیت",
        "tags": ["#Technology", "#CyberSecurity"],
        "kw": ["breach", "hacked", "hacking", "hacker", "malware", "ransomware",
               "phishing", "vulnerability", "vulnerable", "zero-day", "zero day",
               "cve-", "data leak", "leaked data", "security flaw",
               "botnet", "ddos", "encryption", "privacy", "spyware", "stolen",
               "cyberattack", "cyber attack", "credentials", "patch now",
               "data breach", "password", "backdoor"],
    },
    "gaming": {
        "label_ps": "ګیمینګ",
        "tags": ["#Technology", "#Gaming"],
        "kw": ["playstation", "xbox", "nintendo", "steam", "gaming", " gamer",
               "esports", "ps5", "xbox series", "game ", " games", "gameplay",
               "console", "nintendo switch"],
    },
    "afghanistan": {
        "label_ps": "د افغانستان ټکنالوژۍ",
        "tags": ["#Technology", "#Afghanistan_Tech"],
        "kw": ["afghanistan", "afghan", "kabul", "kandahar", "herat", "roshan",
               "africell", "awcc", "salammobile", "afghan telecom", "nixa"],
    },
    "jobs": {
        "label_ps": "د ټکنالوژۍ دنه او فری لانسرینګ",
        "tags": ["#Technology", "#TechJobs"],
        "kw": ["remote job", "remote jobs", "freelance", "freelancing",
               "freelancer", "hiring", "job opening", "work from home",
               "job market", "developer job", "programming job", "job search",
               "upwork", "fiverr", "remote work"],
    },
    "social": {
        "label_ps": "ټولنیزې رسانې",
        "tags": ["#Technology", "#SocialMedia"],
        "kw": ["facebook", "instagram", "tiktok", "telegram", "youtube",
               "whatsapp", "snapchat", "linkedin", "twitter", "social media",
               "threads app", "reels", "shorts", "content creator"],
    },
    "mobile": {
        "label_ps": "موبایل او اپلیکیشنونه",
        "tags": ["#Technology", "#Mobile"],
        "kw": ["android", "iphone", "ios", "smartphone", "pixel", "galaxy",
               "oneplus", "xiaomi", "huawei", "oppo", "vivo", "tablet",
               "app store", "play store", "mobile app", "mobile phone",
               "foldable"],
    },
    "internet": {
        "label_ps": "انټرنیټ او ټیلیکام",
        "tags": ["#Technology", "#Internet"],
        "kw": ["5g", "4g", "wi-fi", "wifi", "internet", "broadband", "telecom",
               "fiber", "fibre", "dns", "outage", "starlink", "esim",
               "mobile network", "undersea cable", "satellite internet"],
    },
    "bigtech": {
        "label_ps": "لویې ټکنالوژیزې شرکتونه",
        "tags": ["#Technology", "#BigTech"],
        "kw": ["apple", "google", "microsoft", "meta ", "amazon", "nvidia",
               "samsung", "tesla", "sony", "intel", "qualcomm", "oracle",
               "ibm", "dell", "lenovo", "bytedance"],
    },
    "technology": {
        "label_ps": "ټکنالوژۍ",
        "tags": ["#Technology"],
        "kw": ["processor", "cpu", "gpu", "semiconductor", "chip ", " chips",
               "laptop", "operating system", "windows", "linux", "macos",
               "hardware", "firmware", "ssd", "browser", "data center",
               "quantum", "robotics", "startup", "funding"],
    },
    # ---- economy / forex (same bot, separate subject) ----
    "forex": {
        "label_ps": "فورکس او اسعار",
        "tags": ["#Economy", "#Forex"],
        "kw": ["forex", "exchange rate", "currency", "dollar", "euro", "yen",
               "pound", "afghani", "usd", "eur", "central bank", "fed ",
               "interest rate", "rate cut", "monetary", "currency pair",
               "greenback", "devaluation", "currency war"],
    },
    "markets": {
        "label_ps": "بازارونه او سهمونه",
        "tags": ["#Economy", "#Markets"],
        "kw": ["stocks", "stock market", "shares", "nasdaq", "dow jones",
               "s&p 500", "wall street", "index", "bond", "yields",
               "ipo", "earnings", "rally", "bear market", "bull market",
               "trading", "investors", "market cap"],
    },
    "crypto": {
        "label_ps": "کریپټو",
        "tags": ["#Economy", "#Crypto"],
        "kw": ["bitcoin", "btc", "ethereum", "crypto", "cryptocurrency",
               "blockchain", "token", "stablecoin", "binance", "coinbase",
               "defi", "altcoin", "dogecoin", "solana"],
    },
    "economy": {
        "label_ps": "اقتصاد",
        "tags": ["#Economy"],
        "kw": ["economy", "economic", "gdp", "inflation", "recession",
               "tariff", "trade deal", "budget", "fiscal", "unemployment",
               "imf", "world bank", "poverty", "aid", "sanctions",
               "commodities", "oil price", "gold price", "gold ", "crude"],
    },
}
CATEGORY_ORDER = ["cybersecurity", "gaming", "afghanistan", "jobs", "social",
                  "mobile", "internet", "forex", "crypto", "markets",
                  "economy", "bigtech", "technology"]

# ---------------------------------------------------------------------------
# Pashto lexicon for rendering
# ---------------------------------------------------------------------------
COMPANIES_PS = {
    "apple": "اپل", "google": "ګوګل", "microsoft": "مایکروسافت", "meta": "میٹا",
    "facebook": "فیس‌بوک", "amazon": "أمازون", "nvidia": "اینویډیا",
    "samsung": "سامسونگ", "tesla": "تیسلا", "sony": "سونی", "huawei": "هووی",
    "xiaomi": "شیائومی", "intel": "اینټل", "amd": "ای‌ایم‌ډی",
    "qualcomm": "کوالکام", "oracle": "اوراکل", "ibm": "آی‌بی‌ام", "dell": "ډیل",
    "hp": "ایچ‌پی", "lenovo": "لینوو", "oneplus": "وان‌پلاس", "oppo": "اوپو",
    "vivo": "ویو", "motorola": "موټورولا", "nokia": "نوکیا", "asus": "ایزوس",
    "acer": "ایسر", "bytedance": "بایټ‌ډانس",
    "android": "اینډرائیڈ", "ios": "آی‌اواس", "windows": "وینڊوز",
    "macos": "میکوس", "linux": "لینکس", "chrome": "کروم",
    "firefox": "فایرفاکس", "safari": "سافاري", "ubuntu": "یوبانتو",
    "instagram": "اینستاګرام", "tiktok": "ټیک‌ټاک", "youtube": "یوټیوب",
    "telegram": "تیلیګرام", "whatsapp": "واټس‌ایپ", "snapchat": "سناپ‌چیټ",
    "linkedin": "لینکډین", "twitter": "ټویټر",
    "playstation": "پلے‌اسټیشن", "xbox": "ایکس‌باکس", "nintendo": "نینټینډو",
    "steam": "ستیم", "starlink": "ستارلینک",
    # economy / institutions (longer keys first so they match before "bank")
    "federal reserve": "فیډریل ریزرو", "european central bank": "یورپي مرکزي بینک",
    "world bank": "نړیوال بینک", "central bank": "مرکزي بینک",
    "international monetary fund": "نړیوال پیسو ټولنه",
    "imf": "آی‌ام‌ایف", "nasdaq": "ناسډاک", "dow jones": "ډاو جونز",
    "fed": "فیډ", "ecb": "یورپي مرکزي بینک",
    "bitcoin": "بیټ‌کوین", "ethereum": "ایتریم", "binance": "باینانس",
    "tether": "تیټر", "solana": "سولانا",
}

# action detection: (key, patterns, pashto template {S}=subject, {r}=remainder)
ACTIONS: List[Tuple[str, List[str], str]] = [
    ("release", [r"\breleases?\b", r"\breleased\b", r"\brolling out\b",
                 r"\brollout\b", r"\bdebut", r"\bavailable now\b",
                 r"\bnow available\b", r"\blaunche[sd]\b", r"\blaunches\b",
                 r"\bunveils?\b", r"\bunveiled\b", r"\bintroduces?\b",
                 r"\bintroduced\b", r"\bgoes on sale\b"],
     "{S} {r} د کارونکو لپاره شاته کړ"),
    ("announce", [r"\bannounces?\b", r"\bannounced\b", r"\breveals?\b",
                  r"\brevealed\b", r"\bconfirms?\b", r"\bconfirmed\b",
                  r"\bplans?\b", r"\bplanned\b"],
     "{S} {r} په اړه خبر ورکړ"),
    ("update", [r"\bupdates?\b", r"\bupdated\b", r"\bupgrades?\b",
                r"\bupgraded\b", r"\bpatches?\b", r"\bpatched\b",
                r"\badds?\b", r"\badded\b", r"\brefresh"],
     "{S} {r} تازه کړ"),
    ("fix", [r"\bfixes?\b", r"\bfixed\b", r"\bfixing\b", r"\bresolves?\b",
             r"\bsecurity fix", r"\bhotfix"],
     "{S} {r} درست کړ"),
    ("ban", [r"\bbans?\b", r"\bbanned\b", r"\bblocks?\b", r"\bblocked\b",
             r"\bremoves?\b", r"\bremoved\b", r"\bpulls?\b", r"\bpulled\b",
             r"\brestricts?\b", r"\brestricted\b", r"\bsuspends?\b",
             r"\bsuspended\b", r"\bdisables?\b", r"\bdisabled\b"],
     "{S} {r} ممنوع / بند کړ"),
    ("acquire", [r"\bacquires?\b", r"\bacquired\b", r"\bbuys?\b", r"\bbought\b",
                 r"\btakes over\b", r"\binvests?\b", r"\binvested\b"],
     "{S} {r} واخست"),
    ("breach", [r"\bbreach", r"\bhacked\b", r"\bhackers?\b", r"\bdata leak",
                r"\bleaked\b", r"\bstolen\b", r"\bcompromised\b",
                r"\bcyberattack", r"\bransomware", r"\bmalware\b"],
     "د {S} کې {r} خنډ/پاتې شو"),
    ("outage", [r"\boutage", r"\boffline\b", r"\bgoes down\b", r"\bwent down\b",
                r"\bdisruption", r"\bnot working\b", r"\bcrashes?\b",
                r"\bcrashed\b"],
     "{S} {r} ناکاره کېدل"),
    ("delay", [r"\bdelays?\b", r"\bdelayed\b", r"\bpostpones?\b",
               r"\bpostponed\b", r"\bpauses?\b", r"\bpaused\b"],
     "{S} {r} ځنډاوه کړ"),
    ("price", [r"\bprice", r"\bpricing\b", r"\bcost\b", r"\bcheaper\b",
               r"\bexpensive\b", r"\bfee\b", r"\bsubscription\b",
               r"\bdiscount\b", r"\brate cut", r"\brate hike",
               r"\binterest rate", r"\bcuts rates\b", r"\bhikes rates\b",
               r"\braises rates\b", r"\bmonetary policy\b"],
     "{S} د {r} قیمت/نرخ بدل کړ"),
    ("partner", [r"\bpartnership\b", r"\bpartner", r"\bteams? up\b",
                 r"\bcollaborat", r"\bintegrat"],
     "{S} {r} سره تفاهم کړ"),
    ("study", [r"\bstudy\b", r"\bsurvey\b", r"\bresearch\b", r"\breport(s|ed)?\b",
               r"\bwarns?\b", r"\bwarning\b", r"\badvises?\b"],
     "{S} د {r} خبر ورکړ"),
]
DEFAULT_ACTION = ("info", [], "{S} {r} تازه خبر ورکړ")

STOPWORDS = set("""
a an the and or but if then than that this these those of in on at to for from by
with as is are was were be been being it its his her their our your my me you we
they he she i not no nor so too very can will would should could may might must
do does did have has had having about into over under after before between out up
down off again further more most other some such only own same just also there
here when where why how all any both each few now new says said say per via its
he's she's it's don't
""".split())

CLICKBAIT_PATTERNS = [
    r"you won't believe", r"won't believe", r"shocking", r"click here",
    r"you need to know", r"mind-?blowing", r"going viral", r"here's why",
    r"here is why", r"secret(?:s)? revealed", r"doctors hate",
    r"life-changing", r"read at your own risk", r"you won't want to miss",
    r"you won.t believe", r"is this the end",
]
LISTICLE_PATTERN = re.compile(
    r"^\s*\d+\s+(best|worst|top|things|reasons|ways|games|apps|phones)", re.I)

# number/fact patterns — always kept verbatim (never translated)
FACT_PATTERNS = [
    re.compile(r"\$\s?\d[\d.,]*\s?(?:billion|million|trillion|bn|mn)?\b", re.I),
    re.compile(r"\d+(?:\.\d+)?%"),
    re.compile(r"\bv?\d+\.\d+(?:\.\d+)*\b"),
    re.compile(r"\d[\d,]*\s?(?:million|billion)\s+(?:users|customers|devices|accounts|records)", re.I),
    re.compile(r"\b\d+\s+basis points\b", re.I),
    re.compile(r"\b(?:€|£|¥)\s?\d[\d.,]*\s?(?:billion|million|trillion|bn|mn)?\b", re.I),
]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class NewsItem:
    title: str
    link: str
    summary: str
    source: str
    tier: int
    published: Optional[datetime]
    uid: str = ""
    category: str = "technology"
    score: int = 0
    priority: str = "🟢"
    facts: List[str] = field(default_factory=list)
    merged_sources: List[str] = field(default_factory=list)

    def text(self) -> str:
        return f"{self.title} {self.summary}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def strip_html(raw: str) -> str:
    raw = re.sub(r"<[^>]+>", " ", raw or "")
    raw = html_unescape(raw)
    return re.sub(r"\s+", " ", raw).strip()


def html_unescape(text: str) -> str:
    import html as _html
    return _html.unescape(text)


def normalize_title(title: str) -> str:
    t = re.sub(r"[^a-z0-9]+", " ", title.lower())
    return re.sub(r"\s+", " ", t).strip()


def item_uid(title: str, link: str) -> str:
    """Identity for duplicate protection: URL first, normalized title second."""
    key = (link.split("#")[0].strip().lower() or normalize_title(title))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def title_fingerprint(title: str) -> str:
    return hashlib.sha1(normalize_title(title).encode("utf-8")).hexdigest()[:16]


def similar(a: str, b: str) -> bool:
    """Cheap similarity for cross-source duplicate stories."""
    ta, tb = set(normalize_title(a).split()), set(normalize_title(b).split())
    if not ta or not tb:
        return False
    inter = len(ta & tb)
    return inter / min(len(ta), len(tb)) >= 0.7 or inter / max(len(ta), len(tb)) >= 0.55


def age_hours(dt: Optional[datetime]) -> float:
    if dt is None:
        return 999.0
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0


# ---------------------------------------------------------------------------
# Stage 1 — collection (per-source errors never crash the run)
# ---------------------------------------------------------------------------
def fetch_all(stats: Dict[str, int]) -> List[NewsItem]:
    items: List[NewsItem] = []
    headers = {"User-Agent": "GeneralTechNewsBot/1.0 (+RSS aggregator)"}
    for src in SOURCES:
        try:
            resp = requests.get(src["url"], headers=headers,
                                timeout=CONFIG["fetch_timeout"])
            if resp.status_code != 200:
                log.warning("source %s -> HTTP %s (skipped)",
                            src["name"], resp.status_code)
                stats["source_errors"] += 1
                continue
            feed = feedparser.parse(resp.content)
            if feed.bozo and not feed.entries:
                log.warning("source %s -> parse error (skipped)", src["name"])
                stats["source_errors"] += 1
                continue
            got = 0
            for entry in feed.entries:
                title = strip_html(entry.get("title", ""))
                link = (entry.get("link") or "").strip()
                if not title or not link:
                    continue
                published = None
                for key in ("published_parsed", "updated_parsed"):
                    tt = entry.get(key)
                    if tt:
                        published = datetime(*tt[:6], tzinfo=timezone.utc)
                        break
                summary = strip_html(entry.get("summary", ""))[:600]
                items.append(NewsItem(
                    title=title, link=link, summary=summary,
                    source=src["name"], tier=int(src["tier"]),
                    published=published,
                    uid=item_uid(title, link),
                ))
                got += 1
            log.info("source %-28s -> %d items", src["name"], got)
            stats["fetched"] += got
        except requests.RequestException as exc:
            log.warning("source %s -> network error: %s", src["name"], exc)
            stats["source_errors"] += 1
        except Exception as exc:  # noqa: BLE001 — keep the run alive
            log.warning("source %s -> unexpected error: %s", src["name"], exc)
            stats["source_errors"] += 1
        time.sleep(CONFIG["request_delay"])
    return items


# ---------------------------------------------------------------------------
# Stage 2 — AI news filter (hard requirement: AI news never passes)
# ---------------------------------------------------------------------------
def is_ai_news(title: str, summary: str) -> bool:
    combined = f"{title} {summary}".lower()
    for phrase in AI_STRONG:
        if phrase in combined:
            return True
    # standalone word "AI" in the title => AI is the main topic
    if AI_STANDALONE.search(title):
        return True
    # standalone "AI" repeated in body => main topic
    if len(AI_STANDALONE.findall(summary)) >= 2:
        return True
    return False


# ---------------------------------------------------------------------------
# Stage 3 — quality gate (clickbait / fake / too short / too old)
# ---------------------------------------------------------------------------
def quality_ok(item: NewsItem, stats: Dict[str, int]) -> bool:
    if len(item.title) < 25 or len(item.title) > 300:
        stats["filtered_short"] += 1
        return False
    if LISTICLE_PATTERN.search(item.title):
        stats["filtered_clickbait"] += 1
        return False
    low = item.title.lower()
    for pat in CLICKBAIT_PATTERNS:
        if re.search(pat, low):
            stats["filtered_clickbait"] += 1
            return False
    if not re.search(r"[a-zA-Z]", item.title):
        stats["filtered_short"] += 1
        return False
    if age_hours(item.published) > CONFIG["max_age_hours"]:
        stats["filtered_old"] += 1
        return False
    return True


# ---------------------------------------------------------------------------
# Stage 4 — duplicate protection (persisted state + in-run clustering)
# ---------------------------------------------------------------------------
class State:
    """Hashes of already-posted news, persisted between runs (JSON file)."""

    def __init__(self, path: str):
        self.path = path
        self.uids: Dict[str, str] = {}
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                self.uids = data
        except FileNotFoundError:
            self.uids = {}
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("state file unreadable (%s); starting fresh", exc)
            self.uids = {}

    def save(self) -> None:
        try:
            directory = os.path.dirname(self.path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            cutoff = time.time() - 30 * 86400  # prune entries >30 days
            pruned = {k: v for k, v in self.uids.items()
                      if isinstance(v, str) and _ts(v) >= cutoff}
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(pruned, fh, ensure_ascii=False, indent=0)
        except OSError as exc:
            log.warning("could not save state: %s", exc)

    def seen(self, uid: str) -> bool:
        return uid in self.uids

    def mark(self, uid: str) -> None:
        self.uids[uid] = datetime.now(timezone.utc).isoformat()


def _ts(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return 0.0

def dedup(items: List[NewsItem], state: State, stats: Dict[str, int]) -> List[NewsItem]:
    """Drop already-posted news; collapse same-story items into one post."""
    fresh: List[NewsItem] = []
    for it in items:
        if state.seen(it.uid) or title_fingerprint(it.title) in state.uids:
            stats["filtered_dup"] += 1
            continue
        fresh.append(it)

    fresh.sort(key=lambda x: (
        -x.tier,
        x.published or datetime.min.replace(tzinfo=timezone.utc),
    ))
    merged: List[NewsItem] = []
    used = set()
    for i, it in enumerate(fresh):
        if i in used:
            continue
        used.add(i)
        for j in range(i + 1, len(fresh)):
            if j in used:
                continue
            if similar(it.title, fresh[j].title):
                it.merged_sources.append(fresh[j].source)
                used.add(j)
                stats["merged"] += 1
        merged.append(it)
    return merged


# ---------------------------------------------------------------------------
# Stage 5 — category detection
# ---------------------------------------------------------------------------
def detect_category(item: NewsItem) -> str:
    text = " " + item.text().lower() + " "
    # special-coverage categories win outright when their keywords appear
    if any(kw in text for kw in CATEGORIES["afghanistan"]["kw"]):
        return "afghanistan"
    if any(kw in text for kw in CATEGORIES["cybersecurity"]["kw"]):
        return "cybersecurity"
    best, best_hits = "technology", 0
    for key in CATEGORY_ORDER:
        hits = sum(1 for kw in CATEGORIES[key]["kw"] if kw in text)
        if hits > best_hits:
            best, best_hits = key, hits
    return best


# ---------------------------------------------------------------------------
# Stage 6 — importance score + priority (never manufactures urgency)
# ---------------------------------------------------------------------------
BREAKING_WORDS = ["critical", "zero-day", "zero day", "actively exploited",
                  "outage", "recall", "breach", "hacked", "ransomware",
                  "shuts down", "shut down", "taken down", "disruption",
                  "massive hack", "exploited in the wild",
                  "market crash", "circuit breaker", "currency collapse",
                  "default on", "hyperinflation"]
IMPORTANT_WORDS = ["launch", "release", "unveil", "announce", "acquires",
                   "acquired", "update", "patch", "vulnerability", "available",
                   "partnership", "billion", "million", "expands", "debuts",
                   "rate hike", "rate cut", "all-time high", "record high",
                   "inflation", "gdp", "ipo", "earnings", "sanctions"]


def score_item(item: NewsItem) -> None:
    text = item.text().lower()
    score = 10
    score += {3: 25, 2: 18, 1: 8}.get(item.tier, 8)

    age = age_hours(item.published)
    if age <= 6:
        score += 20
    elif age <= 12:
        score += 14
    elif age <= 24:
        score += 8
    elif age <= 36:
        score += 3

    score += min(15, sum(4 for w in IMPORTANT_WORDS if w in text))
    if item.category == "afghanistan":
        score += 12   # audience relevance, not fake urgency
    elif item.category == "cybersecurity":
        score += 8
    elif item.category in ("forex", "markets", "crypto", "economy"):
        score += 5    # covered subject, never inflated to breaking
    elif item.category in ("mobile", "internet", "social"):
        score += 4

    item.score = min(100, score)

    breaking = any(w in text for w in BREAKING_WORDS) and age <= 24
    if breaking and item.score >= 70:
        item.priority = "🔴"
    elif item.score >= 55:
        item.priority = "🟠"
    else:
        item.priority = "🟢"

# ---------------------------------------------------------------------------
# Stage 7 — Pashto writing (template-based; facts never altered)
# ---------------------------------------------------------------------------
def _subject_ps(title: str) -> Optional[str]:
    low = " " + title.lower() + " "
    for en, ps in COMPANIES_PS.items():
        if re.search(rf"\b{re.escape(en)}\b", low):
            return f"{ps} ({en.title() if len(en) <= 4 else en})"
    return None


def _remainder(title: str) -> str:
    """Original title minus the subject name — kept verbatim as the fact core."""
    for en in COMPANIES_PS:
        title = re.sub(rf"\b{re.escape(en)}\b", "", title, flags=re.I)
    title = re.sub(r"\s+", " ", title).strip(" -–—:,.")
    return title


def _detect_action(text: str) -> Tuple[str, str]:
    low = text.lower()
    for key, patterns, template in ACTIONS:
        for pat in patterns:
            if re.search(pat, low):
                return key, template
    return DEFAULT_ACTION[0], DEFAULT_ACTION[2]


def _extract_facts(text: str) -> List[str]:
    facts: List[str] = []
    for pat in FACT_PATTERNS:
        for m in pat.findall(text):
            m = str(m).strip()
            if m and m.lower() not in {f.lower() for f in facts}:
                facts.append(m)
    return facts[:4]


def _relative_time(item: NewsItem) -> str:
    age = age_hours(item.published)
    if age >= 900:
        return "نږدې"
    if age < 1:
        return "لا نوې"
    if age < 24:
        return f"تېرې {int(age)} ساعته مخکې"
    return f"تېرې {int(age / 24)} ورځې مخکې"


def render_post(item: NewsItem) -> str:
    cat = CATEGORIES[item.category]
    subject = _subject_ps(item.title) or ""
    remainder = _remainder(item.title)
    _, template = _detect_action(item.text())

    if subject:
        event = template.format(S=subject, r=f"[{remainder}]")
    else:  # unknown subject: keep the truthful original wording
        event = f"[{item.title}]"

    facts = _extract_facts(item.text())
    item.facts = facts

    rel = _relative_time(item)
    lines: List[str] = []
    lines.append(f"{item.priority} 📰 {event[:300]}")
    lines.append("")
    lines.append("📌 لنډ معلومات:")
    lines.append("")
    lines.append(f"{event[:300]}.")
    lines.append(f"دا خبر {rel} د {item.source} سرچینې څخه راټول شوی او د {cat['label_ps']} برخې ته اړوند دی.")
    if item.merged_sources:
        all_src = ", ".join([item.source] + item.merged_sources)
        lines.append(f"یو خبر له {all_src} څخه راټول شوی دی — د تکراري پوسټونو پر ځای یو بشپړ خبر.")
    if facts:
        lines.append(f"د خبر مهمې شمېرې: {', '.join(facts)}.")
    lines.append("")
    lines.append("🔎 مهمې ټکې:")
    lines.append("")
    lines.append(f"• {event[:300]}")
    if facts:
        lines.append(f"• مهمې شمېرې / ارقام: {', '.join(facts)}")
    else:
        lines.append(f"• سرچینه: {item.source} ({rel} خپور شوی)")
    lines.append(f"• د خبر برخه: {cat['label_ps']} — معلومات د باوري سرچینو څخه دي")
    lines.append("")
    lines.append(f"🌐 سرچینه: {item.source} — {item.link}")
    tags = list(cat["tags"])
    # Afghan economy/forex news also gets the economy hashtags
    if item.category == "afghanistan":
        low = item.text().lower()
        econ_kw = (CATEGORIES["forex"]["kw"] + CATEGORIES["economy"]["kw"]
                   + CATEGORIES["markets"]["kw"])
        if any(kw in low for kw in econ_kw) and "#Economy" not in tags:
            tags.append("#Economy")
    lines.append(" ".join(tags))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Stage 8 — Telegram delivery (env-provided secrets, retries, no crash)
# ---------------------------------------------------------------------------
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def send_to_telegram(text: str, stats: Dict[str, int]) -> bool:
    if CONFIG["dry_run"]:
        print("=" * 60)
        print(text)
        stats["posted"] += 1
        return True

    url = TELEGRAM_API.format(token=CONFIG["token"])
    payload = {
        "chat_id": CONFIG["chat_id"],
        "text": text[:4096],
        "disable_web_page_preview": True,
    }
    for attempt in range(1, 4):
        try:
            resp = requests.post(url, json=payload, timeout=20)
            if resp.status_code == 200 and resp.json().get("ok"):
                stats["posted"] += 1
                return True
            # Telegram flood-control: wait and retry once more
            if resp.status_code == 429:
                wait = resp.json().get("parameters", {}).get("retry_after", 5)
                log.warning("rate limited; sleeping %ss", wait)
                time.sleep(wait)
                continue
            log.error("telegram error %s: %s", resp.status_code, resp.text[:200])
        except requests.RequestException as exc:
            log.error("telegram network error (attempt %d): %s", attempt, exc)
        time.sleep(3 * attempt)
    stats["send_errors"] += 1
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def setup_logging() -> None:
    # Windows consoles default to cp1252 — emoji/Pashto must never crash a run
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,
    )


def new_stats() -> Dict[str, int]:
    return {
        "fetched": 0, "source_errors": 0, "filtered_ai": 0,
        "filtered_dup": 0, "filtered_old": 0, "filtered_short": 0,
        "filtered_clickbait": 0, "merged": 0, "posted": 0,
        "send_errors": 0, "below_min_score": 0,
    }


def main() -> int:
    setup_logging()
    log.info("General News Bot (Technology + Economy) starting "
             "(AI news is excluded by design)")
    if not CONFIG["dry_run"]:
        if not CONFIG["token"] or not CONFIG["chat_id"]:
            log.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set. "
                      "Set them as environment variables (GitHub Secrets).")
            return 2

    stats = new_stats()
    state = State(CONFIG["state_file"])

    items = fetch_all(stats)
    log.info("collected %d items (%d source errors)",
             len(items), stats["source_errors"])

    # filters
    kept: List[NewsItem] = []
    for it in items:
        if is_ai_news(it.title, it.summary):
            stats["filtered_ai"] += 1
            continue
        if not quality_ok(it, stats):
            continue
        kept.append(it)
    log.info("after AI + quality filters: %d", len(kept))

    kept = dedup(kept, state, stats)
    log.info("after duplicate removal: %d (merged %d repeats)",
             len(kept), stats["merged"])

    for it in kept:
        it.category = detect_category(it)
        score_item(it)

    # rank by score, then freshness
    kept.sort(key=lambda x: (
        -x.score,
        x.published or datetime.min.replace(tzinfo=timezone.utc),
    ))

    selected: List[NewsItem] = []
    for it in kept:
        if it.score < CONFIG["min_score"]:
            stats["below_min_score"] += 1
            continue
        selected.append(it)
        if len(selected) >= CONFIG["max_posts_per_run"]:
            break

    log.info("selected %d posts (score>=%d, max=%d)",
             len(selected), CONFIG["min_score"], CONFIG["max_posts_per_run"])

    for it in selected:
        try:
            post = render_post(it)
        except Exception as exc:  # noqa: BLE001 — one bad item must not kill the run
            log.error("render failed for %r: %s", it.title, exc)
            continue
        ok = send_to_telegram(post, stats)
        if ok:
            state.mark(it.uid)             # duplicate protection for next run
            state.mark(title_fingerprint(it.title))
            state.save()
        time.sleep(2 if not CONFIG["dry_run"] else 0)

    log.info("=== summary: fetched=%d ai_filtered=%d dup=%d old=%d "
             "short=%d clickbait=%d merged=%d low_score=%d posted=%d "
             "send_errors=%d source_errors=%d ===",
             stats["fetched"], stats["filtered_ai"], stats["filtered_dup"],
             stats["filtered_old"], stats["filtered_short"],
             stats["filtered_clickbait"], stats["merged"],
             stats["below_min_score"], stats["posted"], stats["send_errors"],
             stats["source_errors"])
    return 0 if stats["send_errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())


