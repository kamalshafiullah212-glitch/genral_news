#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sequential Pashto news bot — Technology, Economy, Rates, Jobs & Social Media.

The bot publishes exactly one post per run in a fixed rotation:

    economy -> date -> rates -> afghan_tech -> jobs -> global_tech -> social

* economy: only USD High-impact events from the Forex Factory calendar.
* date: the daily date in Asia/Kabul, Gregorian + Solar Hijri.
* rates: USD/EUR/GBP/PKR/IRR against AFN from ExchangeRate-API with timestamp.
* afghan_tech / jobs / global_tech / social: category-specific RSS filters.
* Optional Grok writing (GROK_API_KEY) for natural Pashto summaries; a safe
  fallback is used when the key is missing or the API fails.

Notes:
  * Secrets come only from environment variables (GitHub Secrets in CI).
  * Dry-run: set DRY_RUN=1 to print the post instead of sending it.
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
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

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
    "max_posts_per_run": 1,
    "max_age_hours": env_int("MAX_AGE_HOURS", 36),
    "min_score": env_int("MIN_SCORE", 50),
    "state_file": os.environ.get("STATE_FILE", "state/posted.json").strip(),
    "fetch_timeout": env_int("FETCH_TIMEOUT", 20),
    "request_delay": 0.35,
    "grok_api_key": os.environ.get("GROK_API_KEY", "").strip(),
    "grok_model": os.environ.get("GROK_MODEL", "grok-4.1-fast").strip(),
}

log = logging.getLogger("newsbot")

HTTP_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (compatible; GeneralNewsBot/2.0; "
                   "+https://github.com/kamalshafiullah212-glitch/genral_news)"),
    "Accept": "application/rss+xml, application/atom+xml, application/json, text/xml, */*",
}

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
# Sequential publication order. Every successful run publishes one category only.
ROTATION = ["economy", "date", "rates", "afghan_tech", "jobs", "global_tech", "social"]
CATEGORY_META = {
    "economy": ("💵", "اقتصاد", ["#Economy", "#USD_High_Impact"]),
    "date": ("📅", "ورځنی تاریخ", ["#Date"]),
    "rates": ("💱", "د افغانۍ نرخونه", ["#AFN", "#ExchangeRates"]),
    "afghan_tech": ("🇦🇫", "د افغانستان ټیکنالوجي", ["#Technology", "#Afghanistan"]),
    "jobs": ("💼", "د افغانستان دندې", ["#Jobs", "#Afghanistan"]),
    "global_tech": ("🌍", "نړیوال ټیکنالوجي", ["#Technology"]),
    "social": ("📱", "ټولنیزې رسانې", ["#SocialMedia"]),
}
FOREX_FACTORY_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
EXCHANGE_RATE_URL = "https://open.er-api.com/v6/latest/USD"
FOREX_FACTORY_LINK = "https://www.forexfactory.com/calendar"
EXCHANGE_RATE_LINK = "https://www.exchangerate-api.com"
AFGHAN_TZ = ZoneInfo("Asia/Kabul")

CATEGORY_SOURCES = {
    "afghan_tech": [
        ("Google News — Afghanistan Tech", 2,
         "https://news.google.com/rss/search?q=Afghanistan+(internet+OR+telecom+OR+software+OR+startup+OR+technology)&hl=en-US&gl=US&ceid=US:en"),
    ],
    "jobs": [
        ("ReliefWeb Jobs — Afghanistan", 3,
         "https://reliefweb.int/jobs/rss.xml?advanced-search=%28C13%29"),
        ("Google News — Afghanistan Jobs", 2,
         "https://news.google.com/rss/search?q=(job+OR+vacancy+OR+%22job+opening%22)+Afghanistan+apply&hl=en-US&gl=US&ceid=US:en"),
    ],
    "global_tech": [
        ("The Verge", 2, "https://www.theverge.com/rss/index.xml"),
        ("TechCrunch", 2, "https://techcrunch.com/feed/"),
        ("Ars Technica", 2, "https://feeds.arstechnica.com/arstechnica/index"),
        ("NVIDIA Blog", 3, "https://blogs.nvidia.com/feed/"),
        ("Google News — Global Technology", 2,
         "https://news.google.com/rss/search?q=(AI+OR+software+OR+cybersecurity+OR+robotics+OR+smartphone+OR+internet)&hl=en-US&gl=US&ceid=US:en"),
    ],
    "social": [
        ("Google News — Social Platforms", 2,
         "https://news.google.com/rss/search?q=(Facebook+OR+Instagram+OR+TikTok+OR+YouTube+OR+Telegram+OR+WhatsApp+OR+Snapchat+OR+LinkedIn+OR+X)+(%22privacy%22+OR+security+OR+update+OR+feature+OR+policy)&hl=en-US&gl=US&ceid=US:en"),
    ],
}

AFGHAN_WORDS = ("afghanistan", "afghan", "kabul", "herat", "kandahar", "mazar", "balkh", "roshan")
JOB_WORDS = ("job", "jobs", "vacancy", "vacancies", "employment", "hiring", "recruitment", "tender", "career", "careers", "internship", "position", "manager", "officer", "coordinator", "director", "specialist", "consultant", "advisor", "assistant", "technician", "supervisor", "trainer", "engineer", "developer", "analyst", "nurse", "doctor", "midwife", "intern")
SOCIAL_WORDS = ("facebook", "instagram", "tiktok", "youtube", "telegram", "whatsapp", "snapchat", "linkedin", "twitter", "social media", "social platform", "threads", "reels", "shorts")
TECH_WORDS = ("technology", "tech", "internet", "telecom", "software", "app", "apps", "cybersecurity", "cyber security", "vulnerability", "malware", "ransomware", "smartphone", "iphone", "android", "computer", "laptop", "robot", "robotics", "chip", "semiconductor", "quantum", " ai ", "artificial intelligence", "machine learning", "openai", "chatgpt", "gemini", "claude", "llama", "grok", "microsoft", "apple", "google", "nvidia", "intel", "samsung", "developer", "data center", "broadband", "digital")
UPDATE_WORDS = ("launch", "release", "update", "feature", "policy", "security", "privacy", "outage", "breach", "ban", "block", "remove", "launches", "rollout", "partnership", "acquire", "lawsuit", "regulation", "reform", "new", "release")

# Sequential publisher uses CATEGORY_SOURCES above; the legacy SOURCES list is
# kept below for compatibility with the original collector.

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


# ---------------------------------------------------------------------------
# Sequential rotation pipeline (one post per run, fixed category order)
# ---------------------------------------------------------------------------
BROKEN_ENDINGS = {
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into",
    "is", "are", "was", "were", "be", "been", "being", "has", "have", "had",
    "he", "her", "his", "how", "if", "its", "may", "might", "of", "on", "or",
    "our", "over", "so", "than", "that", "the", "their", "then", "these",
    "they", "this", "those", "to", "under", "up", "via", "will", "with",
    "would", "you", "your", "vs", "new",
}
WEEKDAYS_PS = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"]
JALALI_MONTHS_PS = [
    "حمل", "ثور", "جوزا", "سرطان", "اسد", "سنبله",
    "میزان", "عقرب", "قوس", "جدي", "دلو", "حوت",
]


def _parse_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        dt = None
        for candidate in (raw, raw.replace("Z", "+00:00")):
            try:
                dt = datetime.fromisoformat(candidate)
                break
            except ValueError:
                continue
        if dt is None:
            try:
                dt = parsedate_to_datetime(raw)
            except (TypeError, ValueError, IndexError):
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_published(entry: Any) -> Optional[datetime]:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        tt = entry.get(key)
        if tt:
            try:
                return datetime(*tt[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                pass
    for key in ("published", "updated", "created", "dc_date"):
        dt = _parse_dt(entry.get(key))
        if dt:
            return dt
    return None


def _has_any(text: str, words: tuple) -> bool:
    low = " " + (text or "").lower() + " "
    return any((" " + word.strip().lower() + " ") in low for word in words if word.strip())


def _clean_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _title_ok(title: str) -> bool:
    if not (15 <= len(title) <= 300):
        return False
    if not re.search(r"[A-Za-z]", title):
        return False
    if LISTICLE_PATTERN.search(title):
        return False
    low = title.lower()
    if any(re.search(pattern, low) for pattern in CLICKBAIT_PATTERNS):
        return False
    words = re.findall(r"[A-Za-z0-9']+", title)
    if len(words) < 4:
        return False
    if words[-1].lower() in BROKEN_ENDINGS:
        return False
    if title.rstrip().endswith((":", ",", ";", "-", "–", "—")):
        return False
    return True


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> Tuple[int, int, int]:
    """Convert a Gregorian date to the Solar Hijri (Jalali) calendar."""
    month_days = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy - 1600
    gm2 = gm - 1
    gd2 = gd - 1
    day_no = (365 * gy2) + ((gy2 + 3) // 4) - ((gy2 + 99) // 100) + ((gy2 + 399) // 400)
    day_no += month_days[gm2] + gd2
    if gm > 2 and ((gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0):
        day_no += 1
    j_day_no = day_no - 79
    j_np = j_day_no // 12053
    j_day_no %= 12053
    jy = 979 + 33 * j_np + 4 * (j_day_no // 1461)
    j_day_no %= 1461
    if j_day_no >= 366:
        jy += (j_day_no - 1) // 365
        j_day_no = (j_day_no - 1) % 365
    for month in range(11):
        month_len = 31 if month < 6 else 30
        if j_day_no < month_len:
            return jy, month + 1, j_day_no + 1
        j_day_no -= month_len
    return jy, 12, j_day_no + 1


class RotationState:
    """Persistent duplicate + rotation state (migrates the legacy flat file)."""

    def __init__(self, path: str):
        self.path = path
        self.rotation_index = 0
        self.uids: Dict[str, str] = {}
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            data = {}
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("rotation state unreadable (%s); starting fresh", exc)
            data = {}
        if not isinstance(data, dict):
            data = {}
        raw_uids = data.get("uids") if isinstance(data.get("uids"), dict) else data
        self.uids = {str(k): str(v) for k, v in raw_uids.items()
                     if isinstance(v, str)} if isinstance(raw_uids, dict) else {}
        try:
            self.rotation_index = int(data.get("rotation_index", 0))
        except (TypeError, ValueError):
            self.rotation_index = 0
        self.rotation_index %= len(ROTATION)

    def save(self) -> None:
        try:
            directory = os.path.dirname(self.path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            cutoff = time.time() - 45 * 86400
            pruned = {k: v for k, v in self.uids.items()
                      if isinstance(v, str) and _ts(v) >= cutoff}
            payload = {
                "version": 2,
                "rotation_index": self.rotation_index % len(ROTATION),
                "uids": pruned,
            }
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=0)
        except OSError as exc:
            log.warning("could not save rotation state: %s", exc)

    def seen(self, key: str) -> bool:
        return bool(key) and key in self.uids

    def mark(self, key: str) -> None:
        if key:
            self.uids[key] = datetime.now(timezone.utc).isoformat()

    def advance(self, selected_index: int) -> None:
        self.rotation_index = (selected_index + 1) % len(ROTATION)


def fetch_json(url: str, stats: Dict[str, int]) -> Any:
    for attempt in range(1, 4):
        try:
            resp = requests.get(url, headers=HTTP_HEADERS,
                                timeout=CONFIG["fetch_timeout"])
        except requests.RequestException as exc:
            log.warning("json source %s -> %s (attempt %d)", url, exc, attempt)
            if attempt < 3:
                time.sleep(3 * attempt)
                continue
            stats["source_errors"] += 1
            return None
        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError as exc:
                log.warning("json source %s -> invalid JSON: %s", url, exc)
                stats["source_errors"] += 1
                return None
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < 3:
            try:
                wait = int(resp.headers.get("Retry-After", "")) or 5 * attempt
            except ValueError:
                wait = 5 * attempt
            wait = max(1, min(wait, 30))
            log.warning("json source %s -> HTTP %s; retrying in %ss",
                        url, resp.status_code, wait)
            time.sleep(wait)
            continue
        log.warning("json source %s -> HTTP %s", url, resp.status_code)
        stats["source_errors"] += 1
        return None
    stats["source_errors"] += 1
    return None


def fetch_feed_entries(name: str, tier: int, url: str, stats: Dict[str, int]) -> List[NewsItem]:
    items: List[NewsItem] = []
    try:
        resp = requests.get(
            url,
            headers=HTTP_HEADERS,
            timeout=CONFIG["fetch_timeout"],
        )
        if resp.status_code != 200:
            log.warning("feed %s -> HTTP %s (skipped)", name, resp.status_code)
            stats["source_errors"] += 1
            return items
        feed = feedparser.parse(resp.content)
        if feed.bozo and not feed.entries:
            log.warning("feed %s -> parse error (skipped)", name)
            stats["source_errors"] += 1
            return items
        for entry in feed.entries:
            title = _clean_spaces(strip_html(entry.get("title", "")))
            link = (entry.get("link") or "").strip()
            if not title or not link:
                continue
            published = _parse_published(entry)
            summary = strip_html(entry.get("summary", "") or entry.get("description", ""))[:900]
            source = name
            source_info = entry.get("source")
            if isinstance(source_info, dict) and source_info.get("title"):
                source = _clean_spaces(str(source_info.get("title")))
            suffix = " - " + source
            if source and title.endswith(suffix):
                title = title[:-len(suffix)].strip()
            items.append(NewsItem(
                title=title, link=link, summary=summary, source=source,
                tier=tier, published=published, uid=item_uid(title, link),
            ))
        log.info("feed %-34s -> %d entries", name, len(items))
        stats["fetched"] += len(items)
    except requests.RequestException as exc:
        log.warning("feed %s -> network error: %s", name, exc)
        stats["source_errors"] += 1
    except Exception as exc:  # noqa: BLE001
        log.warning("feed %s -> unexpected error: %s", name, exc)
        stats["source_errors"] += 1
    return items


def _fmt_rate(value: float) -> str:
    if value >= 100:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    return f"{value:.8f}".rstrip("0").rstrip(".")


def _event_text(event: Dict[str, Any]) -> str:
    title = _clean_spaces(str(event.get("title", "")))
    parts = [f"د Forex Factory د کیلنڈر له مخې د امریکايي ډالرو (USD) لوړ اغېز پېښه: {title}."]
    actual = _clean_spaces(str(event.get("actual", "")))
    forecast = _clean_spaces(str(event.get("forecast", "")))
    previous = _clean_spaces(str(event.get("previous", "")))
    if actual:
        parts.append(f"ثبت شوې کچه (Actual): {actual}.")
    if forecast:
        parts.append(f"تمه شوې کچه (Forecast): {forecast}.")
    if previous:
        parts.append(f"پخوانۍ کچه (Previous): {previous}.")
    return " ".join(parts)


def _ff_calendar_link(dt: datetime) -> str:
    day = dt.astimezone(timezone.utc).strftime("%b%d.%Y").lower()
    return f"https://www.forexfactory.com/calendar?day={day}"


def economy_candidate(state: "RotationState", stats: Dict[str, int]) -> Optional[NewsItem]:
    data = fetch_json(FOREX_FACTORY_URL, stats)
    if not isinstance(data, list):
        return None
    now = datetime.now(timezone.utc)
    best: Optional[NewsItem] = None
    best_key: Optional[Tuple[float, float]] = None
    for event in data:
        if not isinstance(event, dict):
            continue
        if str(event.get("country", "")).strip().upper() != "USD":
            continue
        if str(event.get("impact", "")).strip().lower() != "high":
            continue
        title = _clean_spaces(str(event.get("title", "")))
        dt = _parse_dt(event.get("date"))
        if not title or not dt:
            continue
        delta_hours = (dt - now).total_seconds() / 3600.0
        # Recent releases and the next two weeks of scheduled high-impact events.
        if delta_hours > 24 * 14 or delta_hours < -24:
            continue
        uid = "ff|usd|high|" + normalize_title(title) + "|" + dt.isoformat()
        if state.seen(uid) or state.seen(title_fingerprint(title)):
            continue
        item = NewsItem(
            title=f"USD High Impact: {title}",
            link=_ff_calendar_link(dt),
            summary=_event_text(event),
            source="Forex Factory",
            tier=3,
            published=dt,
            uid=uid,
            category="economy",
        )
        item.facts = [value for value in (
            _clean_spaces(str(event.get("actual", ""))),
            _clean_spaces(str(event.get("forecast", ""))),
            _clean_spaces(str(event.get("previous", ""))),
        ) if value]
        # Prefer a released event slightly, then the nearest event in time.
        released = 0 if delta_hours <= 0 else 1
        proximity = abs(delta_hours)
        key = (released, proximity)
        if best_key is None or key < best_key:
            best_key = key
            best = item
    if best is None:
        log.info("economy: no fresh USD high-impact event")
    return best


def date_candidate(state: "RotationState") -> Optional[NewsItem]:
    now = datetime.now(AFGHAN_TZ)
    uid = "date|" + now.date().isoformat()
    if state.seen(uid):
        return None
    jy, jm, jd = gregorian_to_jalali(now.year, now.month, now.day)
    weekday = WEEKDAYS_PS[now.weekday()]
    month_ps = JALALI_MONTHS_PS[jm - 1]
    summary = "\n".join([
        f"میلادي نېټه: {now:%Y-%m-%d}",
        f"د اونۍ ورځ: {weekday}",
        f"هجري شمسي: {jy:04d}-{jm:02d}-{jd:02d} ({jd} {month_ps} {jy})",
    ])
    link = f"https://www.timeanddate.com/calendar/?year={now.year}&month={now.month}"
    return NewsItem(
        title=f"د نن ورځې نېټه — {now:%Y-%m-%d}",
        link=link,
        summary=summary,
        source="Asia/Kabul local date",
        tier=3,
        published=now.astimezone(timezone.utc),
        uid=uid,
        category="date",
    )


def rates_candidate(state: "RotationState", stats: Dict[str, int]) -> Optional[NewsItem]:
    today = datetime.now(AFGHAN_TZ).date().isoformat()
    uid = "rates|" + today
    if state.seen(uid):
        return None
    data = fetch_json(EXCHANGE_RATE_URL, stats)
    if not isinstance(data, dict) or data.get("result") != "success":
        return None
    rates = data.get("rates")
    if not isinstance(rates, dict):
        return None
    update_dt = _parse_dt(data.get("time_last_update_utc")) or _parse_dt(data.get("time_last_update_unix"))
    if update_dt is None or age_hours(update_dt) > 48:
        log.warning("rates: provider timestamp missing or stale")
        return None
    try:
        afn = float(rates["AFN"])
    except (KeyError, TypeError, ValueError):
        log.warning("rates: AFN rate missing from provider")
        return None
    lines: List[str] = []
    facts: List[str] = []
    for code in ("USD", "EUR", "GBP", "PKR", "IRR"):
        try:
            per_afn = afn / float(rates[code])
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
        rendered = f"1 {code} = {_fmt_rate(per_afn)} AFN"
        lines.append(rendered)
        facts.append(rendered)
    if len(lines) < 5:
        log.warning("rates: incomplete currency basket; skipping")
        return None
    lines.append(f"د نرخ وخت: {update_dt:%Y-%m-%d %H:%M} UTC")
    lines.append("دا نرخونه د ExchangeRate-API د حوالې معلوماتو له مخې دي.")
    item = NewsItem(
        title="د افغانۍ (AFN) په وړاندې د مهمو اسعارو نرخونه",
        link=EXCHANGE_RATE_LINK,
        summary="\n".join(lines),
        source="ExchangeRate-API",
        tier=3,
        published=update_dt,
        uid=uid,
        category="rates",
    )
    item.facts = facts
    return item


def _category_match(category: str, text: str) -> bool:
    if category == "afghan_tech":
        return _has_any(text, AFGHAN_WORDS) and _has_any(text, TECH_WORDS)
    if category == "jobs":
        return _has_any(text, AFGHAN_WORDS) and _has_any(text, JOB_WORDS)
    if category == "global_tech":
        return _has_any(text, TECH_WORDS)
    if category == "social":
        return _has_any(text, SOCIAL_WORDS) and _has_any(text, UPDATE_WORDS)
    return False


def _job_details(item: NewsItem) -> List[str]:
    if item.category != "jobs":
        return []
    text = item.summary
    found: List[str] = []
    patterns = (
        ("د ادارې/شرکت", r"(?:organization|employer|company)\s*[:\-]\s*([^\n]{2,80}?)(?=\s+(?:closing date|deadline|posted|location|country|about us|job details|contract|duration|reporting)\b|$)"),
        ("ځای", r"(?:location|duty station|city)\s*[:\-]\s*([A-Z][\w'’\-]*(?:,\s*[A-Z][\w'’\-]*)?)"),
        ("وروستۍ نېټه", r"(?:closing date|deadline|apply before|closing)\s*[:\-]\s*([0-9]{1,2}\s+[A-Za-z]{3,9}\s+[0-9]{4}|[0-9]{1,2}[/\-][0-9]{1,2}[/\-][0-9]{2,4})"),
    )
    for label, pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            value = _clean_spaces(match.group(1)).strip(" .,-")
            if value and len(value) >= 3:
                found.append(f"{label}: {value}")
    return found[:4]


def _news_score(item: NewsItem, category: str) -> int:
    text = item.text().lower()
    set_map = {
        "afghan_tech": TECH_WORDS,
        "jobs": JOB_WORDS,
        "global_tech": TECH_WORDS,
        "social": UPDATE_WORDS,
    }
    score = {3: 30, 2: 20, 1: 10}.get(item.tier, 10)
    age = age_hours(item.published)
    if age <= 6:
        score += 25
    elif age <= 12:
        score += 18
    elif age <= 24:
        score += 12
    elif age <= 72:
        score += 6
    hits = sum(1 for word in set_map.get(category, ()) if _has_any(text, (word,)))
    score += min(24, hits * 4)
    if category in ("afghan_tech", "jobs"):
        score += 10
    if category == "global_tech" and _has_any(text, ("vulnerability", "ransomware", "breach", "launch", "release", "billion", "million")):
        score += 8
    if category == "social" and _has_any(text, ("privacy", "security", "policy", "outage", "ban", "feature")):
        score += 8
    return score


def rss_candidate(category: str, state: "RotationState", stats: Dict[str, int]) -> Optional[NewsItem]:
    max_age = 14 * 24 if category == "jobs" else CONFIG["max_age_hours"]
    candidates: List[NewsItem] = []
    for name, tier, url in CATEGORY_SOURCES.get(category, []):
        for item in fetch_feed_entries(name, int(tier), url, stats):
            if not _title_ok(item.title):
                stats["filtered_short"] += 1
                continue
            age = age_hours(item.published)
            if age > max_age or age < -6:
                stats["filtered_old"] += 1
                continue
            if not _category_match(category, item.text()):
                continue
            if state.seen(item.uid) or state.seen(title_fingerprint(item.title)):
                stats["filtered_dup"] += 1
                continue
            item.category = category
            item.facts = _job_details(item)
            item.score = _news_score(item, category)
            candidates.append(item)
        time.sleep(CONFIG["request_delay"])
    if not candidates:
        log.info("%s: no suitable fresh item", category)
        return None
    candidates.sort(key=lambda x: (
        -x.score,
        -((x.published or datetime.min.replace(tzinfo=timezone.utc)).timestamp()),
    ))
    log.info("%s: %d candidates, best score=%d", category, len(candidates), candidates[0].score)
    return candidates[0]


XAI_CHAT_URL = "https://api.x.ai/v1/chat/completions"


def grok_compose(item: NewsItem) -> Optional[Tuple[str, str]]:
    """Optional Grok rewrite. Facts are passed in; missing key -> safe fallback."""
    api_key = CONFIG.get("grok_api_key", "")
    if not api_key:
        return None
    context = "\n".join([
        f"category: {item.category}",
        f"source: {item.source}",
        f"published_utc: {_post_date_line(item)}",
        f"title: {item.title}",
        f"summary: {item.summary[:1400]}",
        f"facts: {'; '.join(item.facts[:6])}",
        f"link: {item.link}",
    ])
    system_prompt = (
        "You write short Telegram news posts in natural Pashto. "
        "Use only the supplied title, summary, facts, source and date. "
        "Never invent facts, numbers, quotes, names or links. Keep every number exactly as supplied. "
        "Return strict JSON only, with keys headline and summary. "
        "headline: one clear Pashto sentence, maximum 120 characters. "
        "summary: one or two short Pashto sentences, maximum 420 characters."
    )
    payload = {
        "model": CONFIG.get("grok_model") or "grok-4.1-fast",
        "temperature": 0.2,
        "max_tokens": 320,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": context},
        ],
    }
    try:
        resp = requests.post(
            XAI_CHAT_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=40,
        )
        if resp.status_code != 200:
            log.warning("grok API -> HTTP %s; using fallback", resp.status_code)
            return None
        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
        log.warning("grok API error: %s; using fallback", exc)
        return None
    cleaned = re.sub(r"^```(?:json)?|```$", "", str(content).strip(), flags=re.M).strip()
    match = re.search(r"\{.*\}", cleaned, re.S)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    headline = _clean_spaces(str(parsed.get("headline", "")))
    summary = _clean_spaces(str(parsed.get("summary", "")))
    if 8 <= len(headline) <= 160 and 30 <= len(summary) <= 700:
        return headline, summary
    log.warning("grok output failed validation; using fallback")
    return None


def _post_date_line(item: NewsItem) -> str:
    if item.category == "date":
        return datetime.now(AFGHAN_TZ).strftime("%Y-%m-%d (%H:%M Afghanistan)")
    dt = item.published
    if dt is None:
        return "نېټه نه ده معلومه"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _fallback_body(item: NewsItem) -> str:
    if item.category in ("economy", "date", "rates"):
        return item.summary
    parts = [f"د {item.source} د راپور له مخې دا مهم پرمختګ خپور شو: {item.title}."]
    if item.category == "jobs":
        parts.append("د دندې بشپړ تفصیل او د غوښتنلیک لینک د سرچینې په لینک کې دی.")
        return " ".join(parts)
    facts = item.facts or _extract_facts(item.text())[:3]
    if facts:
        parts.append("د خبر مهم معلومات: " + "؛ ".join(facts) + ".")
    else:
        parts.append("د بشپړ متن لپاره د اصلي سرچینې لینک وګورئ.")
    return " ".join(parts)


def render_rotation_post(item: NewsItem) -> str:
    emoji, label, tags = CATEGORY_META[item.category]
    headline = item.title
    body = item.summary
    if item.category in ("economy", "afghan_tech", "jobs", "global_tech", "social"):
        composed = grok_compose(item)
        if composed:
            headline, body = composed
            log.info("grok: composed Pashto post for %s", item.category)
        else:
            body = _fallback_body(item)
    body = body[:1400]
    lines: List[str] = [
        f"{emoji} {headline}",
        "",
        body,
        "",
        f"📅 نېټه: {_post_date_line(item)}",
        f"🔗 سرچینه: {item.source}",
        item.link,
        " ".join(tags),
    ]
    if item.category == "jobs" and item.facts:
        lines.insert(3, "💼 " + " | ".join(item.facts[:4]))
    return "\n".join(lines)


def candidate_for(category: str, state: RotationState, stats: Dict[str, int]) -> Optional[NewsItem]:
    if category == "economy":
        return economy_candidate(state, stats)
    if category == "date":
        return date_candidate(state)
    if category == "rates":
        return rates_candidate(state, stats)
    return rss_candidate(category, state, stats)


def main_rotation() -> int:
    setup_logging()
    log.info("Sequential news bot starting: one post per run, order=%s", " -> ".join(ROTATION))
    if not CONFIG["dry_run"] and (not CONFIG["token"] or not CONFIG["chat_id"]):
        log.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set. "
                  "Set them as environment variables (GitHub Secrets).")
        return 2

    stats = new_stats()
    state = RotationState(CONFIG["state_file"])
    start = state.rotation_index % len(ROTATION)
    log.info("rotation start: %s (index %d)", ROTATION[start], start)

    selected: Optional[NewsItem] = None
    selected_index: Optional[int] = None
    for offset in range(len(ROTATION)):
        index = (start + offset) % len(ROTATION)
        category = ROTATION[index]
        try:
            candidate = candidate_for(category, state, stats)
        except Exception as exc:  # noqa: BLE001 — never kill the whole run
            log.error("category %s failed: %s", category, exc)
            stats["source_errors"] += 1
            candidate = None
        if candidate is None:
            log.info("category %s: skipped (no suitable item)", category)
            continue
        selected = candidate
        selected_index = index
        break

    if selected is None or selected_index is None:
        log.info("no category had a suitable item; nothing posted this run")
    else:
        try:
            post = render_rotation_post(selected)
        except Exception as exc:  # noqa: BLE001
            log.error("render failed for category %s: %s", selected.category, exc)
            post = ""
        if post:
            ok = send_to_telegram(post, stats)
            if ok:
                state.mark(selected.uid)
                state.mark(title_fingerprint(selected.title))
                state.advance(selected_index)
                if not CONFIG["dry_run"]:
                    state.save()
                log.info("posted category=%s; next=%s",
                         selected.category, ROTATION[state.rotation_index])
        else:
            log.error("empty post for category %s; rotation not advanced", selected.category)

    log.info("=== summary: fetched=%d dup=%d old=%d short=%d posted=%d "
             "send_errors=%d source_errors=%d next=%s ===",
             stats["fetched"], stats["filtered_dup"], stats["filtered_old"],
             stats["filtered_short"], stats["posted"], stats["send_errors"],
             stats["source_errors"],
             ROTATION[state.rotation_index % len(ROTATION)])
    return 0 if stats["send_errors"] == 0 else 1



if __name__ == "__main__":
    sys.exit(main_rotation())


