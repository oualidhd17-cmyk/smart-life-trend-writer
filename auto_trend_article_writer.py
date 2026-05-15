from __future__ import annotations

import csv
import html
import json
import mimetypes
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

import feedparser
import requests
from bs4 import BeautifulSoup
from slugify import slugify

# =========================
# CONFIG
# =========================

SITE_NAME = "Smart Life Tools"

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "output"
HTML_DIR = OUT_DIR / "html"
DATA_DIR = OUT_DIR / "data"
IMAGES_DIR = OUT_DIR / "images"
CSV_FILE = OUT_DIR / "blogger_ready_posts.csv"

COUNTRIES = ["US", "GB", "CA", "AU"]

MAX_TRENDS_PER_COUNTRY = 20
MAX_ARTICLES_TO_GENERATE = 5
MAX_SOURCES_PER_TOPIC = 8

# False = safer images from Wikimedia fallback only
USE_RSS_IMAGES = False

# False avoids GDELT 429 errors
USE_GDELT = False

GOOGLE_TRENDS_RSS = "https://trends.google.com/trending/rss?geo={country}"

NICHE_KEYWORDS = [
    "ai",
    "artificial intelligence",
    "chatgpt",
    "gemini",
    "claude",
    "perplexity",
    "google",
    "microsoft",
    "apple",
    "openai",
    "meta",
    "canva",
    "notion",
    "tool",
    "tools",
    "app",
    "apps",
    "software",
    "productivity",
    "student",
    "students",
    "study",
    "college",
    "school",
    "remote work",
    "job",
    "jobs",
    "resume",
    "career",
    "freelance",
    "travel",
    "flight",
    "flights",
    "hotel",
    "trip",
    "itinerary",
    "fitness",
    "workout",
    "walking",
    "habit",
    "routine",
    "budget",
    "money",
    "saving",
    "expense",
    "business",
    "online business",
    "chrome extension",
    "browser",
    "workspace",
    "automation",
    "agent",
    "agents",
    "ai agent",
    "ai agents",
    "ai agent builder",
    "model context protocol",
    "mcp",
    "llama",
    "cursor",
    "codex",
    "zapier",
    "composio",
]

BLOCKED_KEYWORDS = [
    # politics / crime / disasters
    "war",
    "murder",
    "shooting",
    "killed",
    "death",
    "politics",
    "election",
    "celebrity",
    "divorce",
    "scandal",
    "crime",
    "lawsuit",
    "earthquake",
    # sports
    "dodgers",
    "yankees",
    "mets",
    "lakers",
    "nba",
    "nfl",
    "mlb",
    "nhl",
    "ufc",
    "soccer",
    "football",
    "baseball",
    "basketball",
    "hockey",
    "games",
    "game",
    "score",
    "match",
    "playoffs",
    "player",
    "coach",
    "pitcher",
    "striker",
    "goalkeeper",
    "league",
    "stadium",
    # entertainment / celebrities
    "actor",
    "actress",
    "movie",
    "film",
    "netflix",
    "disney",
    "trailer",
    "singer",
    "rapper",
    "song",
    "album",
    "concert",
    "tv show",
    # names / bad topics
    "edwin diaz",
    "jake gyllenhaal",
    "fernando valenzuela",
    "steve carell",
    "eiza gonzález",
    "john tortorella",
    "jack eichel",
    # unrelated science / animals
    "dinosaur",
    "species",
    "fossil",
    "thailand",
    "archaeology",
    "animal",
    "zoo",
    # weak news / company-news topics
    "reportedly",
    "legal action",
    "lawsuit",
    "cuts",
    "layoffs",
    "revenue",
    "stock",
    "shares",
    "chief",
    "ceo",
    "interview",
    "courts",
    "record quarterly",
]

RSS_SOURCES = [
    # AI / Tech / tools
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml",
    "https://www.zdnet.com/topic/artificial-intelligence/rss.xml",
    "https://www.makeuseof.com/feed/",
    "https://www.howtogeek.com/feed/",
    # Productivity / apps
    "https://zapier.com/blog/feeds/latest/",
    "https://todoist.com/inspiration/feed",
    # Travel
    "https://www.travelandleisure.com/rss",
    "https://www.cntraveler.com/feed/rss",
    # General tech
    "https://www.wired.com/feed/rss",
]


# =========================
# BASIC HELPERS
# =========================


def ensure_dirs() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    HTML_DIR.mkdir(exist_ok=True)
    DATA_DIR.mkdir(exist_ok=True)
    IMAGES_DIR.mkdir(exist_ok=True)


def clean_text(value: str | None) -> str:
    value = str(value or "")

    if value.startswith("http://") or value.startswith("https://"):
        return value.strip()

    value = BeautifulSoup(value, "html.parser").get_text(" ")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def esc(value: str | None) -> str:
    return html.escape(str(value or ""), quote=True)


def contains_any(text: str, keywords: list[str]) -> bool:
    text = text.lower()

    for keyword in keywords:
        keyword = keyword.lower().strip()

        if not keyword:
            continue

        if len(keyword) <= 3:
            pattern = r"\b" + re.escape(keyword) + r"\b"
            if re.search(pattern, text):
                return True
        else:
            if keyword in text:
                return True

    return False


def score_topic(title: str, summary: str = "") -> int:
    text = f"{title} {summary}".lower()

    if contains_any(text, BLOCKED_KEYWORDS):
        return -100

    score = 0

    for keyword in NICHE_KEYWORDS:
        if contains_any(text, [keyword]):
            score += 10

    if contains_any(
        text,
        [
            "ai",
            "chatgpt",
            "gemini",
            "openai",
            "artificial intelligence",
            "claude",
            "perplexity",
            "llama",
            "mcp",
            "model context protocol",
            "ai agent",
            "ai agents",
        ],
    ):
        score += 30

    if contains_any(
        text,
        [
            "tool",
            "tools",
            "app",
            "apps",
            "software",
            "workspace",
            "automation",
            "builder",
        ],
    ):
        score += 20

    if contains_any(
        text,
        [
            "travel",
            "flight",
            "student",
            "resume",
            "productivity",
            "fitness",
            "remote work",
        ],
    ):
        score += 15

    return score


def looks_like_person_name(title: str) -> bool:
    title_clean = title.strip()
    words = title_clean.split()
    lower = title_clean.lower()

    allowed_short_topics = {
        "chatgpt",
        "gemini",
        "openai",
        "google",
        "apple",
        "microsoft",
        "canva",
        "notion",
        "perplexity",
        "claude",
        "figma",
        "grammarly",
        "google ai",
        "apple intelligence",
        "ai tools",
        "travel apps",
        "productivity apps",
        "remote jobs",
        "meta ai",
        "llama",
        "mcp",
        "cursor",
        "codex",
        "zapier",
    }

    if lower in allowed_short_topics:
        return False

    if len(words) == 2:
        has_niche_word = contains_any(lower, NICHE_KEYWORDS)
        both_title_case = all(w[:1].isupper() for w in words if w)
        if both_title_case and not has_niche_word:
            return True

    if len(words) == 1 and lower not in allowed_short_topics:
        return True

    return False


def is_publishable_topic(title: str) -> bool:
    lower = title.lower()

    bad_patterns = [
        "reportedly",
        "legal action",
        "lawsuit",
        "cuts nearly",
        "cuts jobs",
        "record quarterly",
        "who decides",
        "has thoughts",
        "says that",
        "courts a new kind",
        "chief",
        "ceo",
        "stock",
        "shares",
        "revenue",
        "layoffs",
        "fired",
        "resigns",
    ]

    if any(p in lower for p in bad_patterns):
        return False

    good_patterns = [
        "best",
        "tools",
        "tool",
        "apps",
        "app",
        "how to",
        "guide",
        "vs",
        "what is",
        "chatgpt",
        "gemini",
        "notion",
        "zapier",
        "cursor",
        "codex",
        "mcp",
        "ai agent",
        "ai agents",
        "automation",
        "productivity",
        "software",
        "builder",
        "workflow",
        "workflows",
        "chrome extension",
        "browser",
    ]

    return any(p in lower for p in good_patterns)


# =========================
# IMAGE HELPERS
# =========================


def extract_image_url_from_entry(entry) -> str:
    media_content = entry.get("media_content", [])
    if media_content:
        for item in media_content:
            url = item.get("url")
            if url and url.startswith("http"):
                return url

    media_thumbnail = entry.get("media_thumbnail", [])
    if media_thumbnail:
        for item in media_thumbnail:
            url = item.get("url")
            if url and url.startswith("http"):
                return url

    for link in entry.get("links", []):
        href = link.get("href", "")
        link_type = link.get("type", "")
        rel = link.get("rel", "")

        if href.startswith("http") and (
            "image" in link_type.lower()
            or rel in {"enclosure", "image"}
            or href.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        ):
            return href

    summary = str(entry.get("summary", "") or "")
    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', summary, re.I)
    if match:
        url = match.group(1)
        if url.startswith("http"):
            return url

    content = entry.get("content", [])
    if content:
        for item in content:
            value = str(item.get("value", "") or "")
            match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', value, re.I)
            if match:
                url = match.group(1)
                if url.startswith("http"):
                    return url

    return ""


def make_image_query(title: str, category: str) -> str:
    lower = title.lower()

    if "notion" in lower:
        return "productivity workspace dashboard"

    if "mcp" in lower or "model context protocol" in lower:
        return "artificial intelligence network diagram"

    if "meta ai" in lower or "llama" in lower:
        return "artificial intelligence assistant technology"

    if "chatgpt" in lower or "gemini" in lower or "ai" in lower:
        return "artificial intelligence productivity"

    if "coding" in lower or "cursor" in lower or "codex" in lower:
        return "software development laptop code"

    if "zapier" in lower or "automation" in lower:
        return "workflow automation diagram"

    if category == "Travel":
        return "travel planning app"

    if category == "Students":
        return "student laptop study notes"

    if category == "Remote Work":
        return "remote work laptop workspace"

    if category == "Fitness":
        return "fitness app smartphone workout"

    return "productivity apps workspace"


def fetch_wikimedia_image_url(query: str) -> str:
    api_url = "https://commons.wikimedia.org/w/api.php"

    try:
        params = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": query,
            "gsrnamespace": 6,
            "gsrlimit": 8,
            "prop": "imageinfo",
            "iiprop": "url|mime",
        }

        r = requests.get(
            api_url,
            params=params,
            timeout=25,
            headers={"User-Agent": "SmartLifeToolsBot/1.0"},
        )
        r.raise_for_status()
        data = r.json()

        pages = data.get("query", {}).get("pages", {})

        for page in pages.values():
            info = page.get("imageinfo", [])
            if not info:
                continue

            image_url = info[0].get("url", "")
            mime = info[0].get("mime", "")

            if image_url.startswith("http") and mime.startswith("image/"):
                if image_url.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                    return image_url

    except Exception as exc:
        print(f"Wikimedia image failed for {query}: {exc}")

    return ""


def download_image(image_url: str, slug: str) -> str:
    if not image_url:
        return ""

    try:
        r = requests.get(
            image_url,
            timeout=30,
            headers={"User-Agent": "SmartLifeToolsBot/1.0"},
        )
        r.raise_for_status()

        content_type = r.headers.get("Content-Type", "").split(";")[0].strip()
        ext = mimetypes.guess_extension(content_type) or ".jpg"

        if ext == ".jpe":
            ext = ".jpg"

        if ext not in [".jpg", ".jpeg", ".png", ".webp"]:
            ext = ".jpg"

        path = IMAGES_DIR / f"{slug}{ext}"
        path.write_bytes(r.content)
        return str(path)

    except Exception as exc:
        print(f"Image download failed: {exc}")
        return ""


def pick_article_image(
    trend: dict,
    sources: list[dict],
    title: str,
    category: str,
) -> tuple[str, str]:
    if USE_RSS_IMAGES and trend.get("image_url"):
        return trend["image_url"], "RSS trend image"

    if USE_RSS_IMAGES:
        for source in sources:
            if source.get("image_url"):
                return source["image_url"], source.get("domain", "RSS source image")

    query = make_image_query(title, category)
    wiki_url = fetch_wikimedia_image_url(query)

    if wiki_url:
        return wiki_url, "Wikimedia Commons"

    return "", ""


# =========================
# TREND SOURCES
# =========================


def fetch_google_trends() -> list[dict]:
    trends = []

    for country in COUNTRIES:
        url = GOOGLE_TRENDS_RSS.format(country=country)
        print(f"Fetching Google Trends: {country}")

        feed = feedparser.parse(url)

        for entry in feed.entries[:MAX_TRENDS_PER_COUNTRY]:
            title = clean_text(entry.get("title", ""))
            summary = clean_text(entry.get("summary", ""))
            link = clean_text(entry.get("link", ""))
            published = clean_text(entry.get("published", ""))

            if not title:
                continue

            if looks_like_person_name(title):
                continue

            trends.append(
                {
                    "source": "Google Trends",
                    "country": country,
                    "title": title,
                    "summary": summary,
                    "link": link,
                    "published": published,
                    "score": score_topic(title, summary),
                    "image_url": extract_image_url_from_entry(entry),
                }
            )

        time.sleep(1)

    deduped = {}
    for item in trends:
        key = item["title"].lower()
        if key not in deduped or item["score"] > deduped[key]["score"]:
            deduped[key] = item

    result = [
        x
        for x in deduped.values()
        if x["score"] >= 20 and not contains_any(x["title"], BLOCKED_KEYWORDS)
    ]

    result.sort(key=lambda x: x["score"], reverse=True)
    return result


def fetch_rss_trending_topics() -> list[dict]:
    topics = []

    for rss_url in RSS_SOURCES:
        print(f"Fetching niche RSS: {rss_url}")

        try:
            feed = feedparser.parse(rss_url)
        except Exception as exc:
            print(f"RSS failed: {rss_url} - {exc}")
            continue

        for entry in feed.entries[:20]:
            title = clean_text(entry.get("title", ""))
            summary = clean_text(entry.get("summary", ""))
            link = clean_text(entry.get("link", ""))
            published = clean_text(entry.get("published", ""))
            image_url = extract_image_url_from_entry(entry)

            if not title:
                continue

            text = f"{title} {summary}"

            if contains_any(text, BLOCKED_KEYWORDS):
                continue

            if looks_like_person_name(title):
                continue

            if not is_publishable_topic(title):
                continue

            score = score_topic(title, summary)

            if score < 20:
                continue

            domain = re.sub(r"^https?://(www\.)?", "", link).split("/")[0]

            topics.append(
                {
                    "source": "Niche RSS",
                    "country": "GLOBAL",
                    "title": title,
                    "summary": summary,
                    "link": link,
                    "domain": domain,
                    "published": published,
                    "score": score,
                    "image_url": image_url,
                }
            )

    deduped = {}

    for item in topics:
        key = item["title"].lower()
        if key not in deduped or item["score"] > deduped[key]["score"]:
            deduped[key] = item

    result = list(deduped.values())
    result.sort(key=lambda x: x["score"], reverse=True)

    return result


# =========================
# ARTICLE SOURCES
# =========================


def fetch_gdelt_articles(query: str, max_records: int = 8) -> list[dict]:
    url = (
        "https://api.gdeltproject.org/api/v2/doc/doc"
        f"?query={quote_plus(query)}"
        "&mode=artlist"
        "&format=json"
        "&sort=hybridrel"
        f"&maxrecords={max_records}"
    )

    try:
        r = requests.get(
            url,
            timeout=30,
            headers={"User-Agent": "SmartLifeToolsBot/1.0"},
        )
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        print(f"GDELT failed for {query}: {exc}")
        return []

    articles = []

    for item in data.get("articles", []):
        title = clean_text(item.get("title", ""))
        url = clean_text(item.get("url", ""))
        domain = clean_text(item.get("domain", ""))
        seendate = clean_text(item.get("seendate", ""))
        language = clean_text(item.get("language", ""))

        if not title or not url:
            continue

        if contains_any(title, BLOCKED_KEYWORDS):
            continue

        articles.append(
            {
                "source": "GDELT",
                "title": title,
                "url": url,
                "domain": domain,
                "published": seendate,
                "language": language,
                "summary": "",
                "image_url": "",
            }
        )

    return articles


def fetch_rss_articles(query: str, max_records: int = 8) -> list[dict]:
    query_words = [w.lower() for w in re.findall(r"[a-zA-Z0-9]+", query) if len(w) > 2]

    found = []

    for rss_url in RSS_SOURCES:
        try:
            feed = feedparser.parse(rss_url)
        except Exception:
            continue

        for entry in feed.entries[:30]:
            title = clean_text(entry.get("title", ""))
            summary = clean_text(entry.get("summary", ""))
            link = clean_text(entry.get("link", ""))
            published = clean_text(entry.get("published", ""))
            image_url = extract_image_url_from_entry(entry)

            domain = re.sub(r"^https?://(www\.)?", "", link).split("/")[0]

            text = f"{title} {summary}".lower()
            match_count = sum(1 for w in query_words if w in text)

            if match_count <= 0 and score_topic(title, summary) < 20:
                continue

            if contains_any(text, BLOCKED_KEYWORDS):
                continue

            found.append(
                {
                    "source": "RSS",
                    "title": title,
                    "summary": summary[:350],
                    "url": link,
                    "domain": domain,
                    "published": published,
                    "match_count": match_count,
                    "image_url": image_url,
                }
            )

    found.sort(
        key=lambda x: (
            x.get("match_count", 0),
            score_topic(x["title"], x["summary"]),
        ),
        reverse=True,
    )

    deduped = {}
    for item in found:
        key = item["url"] or item["title"].lower()
        deduped[key] = item

    return list(deduped.values())[:max_records]


# =========================
# ARTICLE METADATA
# =========================


def category_for_topic(topic: str) -> tuple[str, str]:
    t = topic.lower()

    if contains_any(t, ["flight", "travel", "hotel", "trip", "itinerary"]):
        return "Travel", "Travel, Apps, How To"

    if contains_any(t, ["student", "school", "college", "study"]):
        return "Students", "Students, Productivity, Apps"

    if contains_any(
        t, ["job", "resume", "career", "remote", "freelance", "remote work"]
    ):
        return "Remote Work", "Remote Work, Productivity, Apps"

    if contains_any(t, ["fitness", "workout", "walking", "habit", "routine"]):
        return "Fitness", "Fitness, Apps, Productivity"

    if contains_any(
        t,
        [
            "ai",
            "chatgpt",
            "gemini",
            "claude",
            "openai",
            "llama",
            "mcp",
            "model context protocol",
            "notion",
            "cursor",
            "codex",
            "zapier",
            "automation",
        ],
    ):
        return "AI Tools", "AI Tools, Productivity, Apps"

    return "Productivity", "Productivity, Apps, How To"


def make_article_title(topic: str, category: str) -> str:
    topic = clean_text(topic)
    lower = topic.lower()

    if "ai agent" in lower and "builder" in lower:
        return "Best AI Agent Builder Tools in 2026: Simple Guide for Beginners"

    if "ai agent builder" in lower:
        return "Best AI Agent Builder Tools in 2026: Simple Guide for Beginners"

    if "codex" in lower and "cursor" in lower:
        return "Codex vs Cursor: Which AI Coding Tool Should You Use in 2026?"

    if "ai coding" in lower or ("cursor" in lower and "codex" in lower):
        return (
            "Best AI Coding Tools in 2026: Cursor, Codex, and Developer AI Assistants"
        )

    if "social media management tools" in lower:
        return "Best Social Media Management Tools in 2026 for Creators and Small Businesses"

    if "automatically answer form responses" in lower and "chatgpt" in lower:
        return "How to Automatically Answer Form Responses with ChatGPT"

    if "composio" in lower and "zapier" in lower:
        return "Composio vs Zapier: Which Automation Tool Is Better in 2026?"

    if "zapier" in lower and "mcp" in lower:
        return "Zapier MCP Explained: How AI Agents Connect with Apps and Workflows"

    if "automation" in lower and "zapier" in lower:
        return "Zapier Automation Guide: How to Connect Apps and Save Time with AI"

    if "notion" in lower and "ai" in lower:
        return (
            "Notion AI Agents: How Notion Is Becoming a Smarter Productivity Workspace"
        )

    if "mcp" in lower or "model context protocol" in lower:
        return "What Is MCP? A Simple Guide to Model Context Protocol for AI Tools"

    if "meta ai" in lower or "llama" in lower or "muse spark" in lower:
        return "Meta AI, Muse Spark, and Llama: What They Mean for Everyday AI Tools"

    if "chatgpt" in lower:
        return f"{topic}: Practical ChatGPT Tips and AI Workflows for Everyday Use"

    if "gemini" in lower:
        return f"{topic}: Practical Gemini AI Tips for Productivity and Daily Work"

    if category == "AI Tools":
        return f"{topic}: A Simple Guide for Everyday AI Users"

    if category == "Travel":
        return f"{topic}: Smart Travel Tools, Apps, and Planning Tips"

    if category == "Students":
        return f"{topic}: Smart Study Tools and Productivity Tips for Students"

    if category == "Remote Work":
        return f"{topic}: Useful Tools for Remote Workers, Job Seekers, and Freelancers"

    if category == "Fitness":
        return f"{topic}: Smart Apps and Simple Tools for Better Daily Routines"

    return f"{topic}: Smart Tools, Apps, and Practical Tips"


def make_meta_description(title: str) -> str:
    t = title.lower()

    if "notion" in t:
        return "Learn how Notion AI agents can improve productivity, task management, notes, and daily workflows."

    if "mcp" in t or "model context protocol" in t:
        return "Understand MCP in simple words and learn how it helps AI tools connect with apps, data, and workflows."

    if "meta ai" in t or "llama" in t:
        return "A simple guide to Meta AI, Muse Spark, and Llama for creators, productivity, and everyday AI use."

    if "chatgpt" in t:
        return "Discover practical ChatGPT tips, tools, and workflows to save time and improve daily productivity."

    if "gemini" in t:
        return "Learn how Gemini AI can help with productivity, research, writing, planning, and everyday tasks."

    if "cursor" in t or "codex" in t or "coding" in t:
        return "Compare AI coding tools and learn how they can help developers write, review, and improve code faster."

    if "zapier" in t or "automation" in t:
        return "Learn how automation tools can connect apps, save time, and improve everyday productivity workflows."

    if "social media" in t:
        return "Discover social media management tools that help creators and small businesses plan, schedule, and manage content."

    if "travel" in t or "flight" in t:
        return "Discover smart travel tools and apps to plan trips, compare options, and save time."

    return "Discover useful AI tools, apps, and practical tips to improve productivity, work, study, and daily life."


# =========================
# ARTICLE CONTENT HELPERS
# =========================


def extract_keywords(items: list[dict]) -> list[str]:
    text = " ".join(
        [x.get("title", "") + " " + x.get("summary", "") for x in items]
    ).lower()

    words = re.findall(r"[a-zA-Z][a-zA-Z0-9]{3,}", text)

    stop = {
        "this",
        "that",
        "with",
        "from",
        "have",
        "will",
        "your",
        "more",
        "about",
        "after",
        "before",
        "into",
        "over",
        "what",
        "when",
        "where",
        "which",
        "their",
        "there",
        "they",
        "them",
        "than",
        "news",
        "says",
        "said",
        "best",
        "how",
        "why",
        "using",
        "use",
        "guide",
        "just",
        "also",
        "like",
        "make",
        "help",
        "helps",
        "work",
        "works",
        "need",
    }

    words = [w for w in words if w not in stop]
    counts = Counter(words)
    return [w for w, _ in counts.most_common(12)]


def make_source_summary(sources: list[dict]) -> list[str]:
    bullets = []

    for s in sources[:MAX_SOURCES_PER_TOPIC]:
        title = clean_text(s.get("title", ""))
        domain = clean_text(s.get("domain", ""))
        published = clean_text(s.get("published", ""))
        summary = clean_text(s.get("summary", ""))

        if summary:
            bullet = f"{title} — {summary}"
        else:
            bullet = title

        if domain:
            bullet += f" ({domain})"

        if published:
            bullet += f" [{published}]"

        bullets.append(bullet)

    return bullets


def render_article_html(
    title: str,
    meta: str,
    category: str,
    labels: str,
    trend: dict,
    sources: list[dict],
    image_url: str = "",
    image_credit: str = "",
) -> str:
    keywords = extract_keywords(sources)
    source_bullets = make_source_summary(sources)

    trend_title = trend["title"]
    country = trend.get("country", "")
    now_iso = datetime.now(timezone.utc).isoformat()
    now_date = datetime.now(timezone.utc).strftime("%B %d, %Y")

    subtitle = (
        f"A practical roundup of tools, apps, and useful sources connected "
        f"to the current trend: {trend_title}."
    )

    rows = []
    for s in sources[:6]:
        rows.append(
            {
                "source": s.get("domain") or s.get("source", "Source"),
                "best_for": "Background research",
                "key_point": s.get("title", "")[:90],
                "link": s.get("url", ""),
            }
        )

    html_parts = []

    html_parts.append(
        '<div style="max-width: 900px; margin: 0 auto; padding: 30px 15px; '
        'font-family: Arial, sans-serif; line-height: 1.75; color: #111827;">'
    )

    html_parts.append(f"""
  <p style="font-size: 15px; color: #4b5563; margin-bottom: 20px;">
    <strong>Meta Description:</strong> {esc(meta)}
  </p>

  <h1 style="font-size: 36px; font-weight: 800; line-height: 1.25; margin-bottom: 20px; color: #0b63ff;">
    {esc(title)}
  </h1>

  <p style="font-size: 21px; font-weight: 600; margin-bottom: 24px; color: #111827;">
    {esc(subtitle)}
  </p>
""")

    if image_url:
        html_parts.append(f"""
  <figure style="margin: 0 0 28px; padding: 0;">
    <img src="{esc(image_url)}" alt="{esc(title)}" style="width: 100%; max-height: 420px; object-fit: cover; border-radius: 18px; display: block; border: 1px solid #e5e7eb;" loading="lazy">
    <figcaption style="font-size: 13px; color: #6b7280; margin-top: 8px;">
      Image source: {esc(image_credit or "Public source")}
    </figcaption>
  </figure>
""")

    html_parts.append(f"""
  <p style="font-size: 18px; margin-bottom: 18px;">
    People are currently searching for <strong>{esc(trend_title)}</strong>, especially in {esc(country or "major English-speaking markets")}. For a site like Smart Life Tools, the best way to use this trend is not to publish random news, but to turn it into a practical guide that helps readers find useful tools, apps, and simple actions.
  </p>

  <p style="font-size: 18px; margin-bottom: 18px;">
    This article was prepared from multiple public sources, trend signals, and recent article listings. It focuses on practical takeaways rather than copying any single source.
  </p>

  <p style="font-size: 18px; margin-bottom: 28px;">
    Below, you will find the most useful angles, tool ideas, practical tips, and source links to explore the topic further.
  </p>

  <h2 style="font-size: 28px; font-weight: 800; margin: 34px 0 16px; color: #111827;">
    Why This Topic Is Trending
  </h2>

  <p style="font-size: 18px; margin-bottom: 18px;">
    The topic is connected to recent search activity and media coverage. When a topic appears across trend feeds and multiple article sources, it usually means that readers are actively looking for explanations, tools, comparisons, or simple next steps.
  </p>

  <p style="font-size: 18px; margin-bottom: 28px;">
    For Smart Life Tools readers, the important question is simple: what tools, apps, and practical habits can make this trend useful in everyday life?
  </p>
""")

    if keywords:
        html_parts.append("""
  <h2 style="font-size: 28px; font-weight: 800; margin: 34px 0 16px; color: #111827;">
    Key Related Terms
  </h2>

  <div style="display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 28px;">
""")

        for kw in keywords[:12]:
            html_parts.append(f"""
    <span style="font-size: 15px; padding: 8px 12px; background: #eff6ff; color: #0b63ff; border-radius: 999px; font-weight: 700;">
      {esc(kw)}
    </span>
""")

        html_parts.append("  </div>\n")

    html_parts.append("""
  <h2 style="font-size: 28px; font-weight: 800; margin: 34px 0 18px; color: #111827;">
    Practical Smart Life Tools Angle
  </h2>
""")

    practical_points = [
        "Look for tools that solve one clear problem instead of installing many apps at once.",
        "Check official pricing pages because free plans, limits, and features can change at any time.",
        "Prefer tools that save time, reduce friction, and fit naturally into your current routine.",
        "Use comparison tables and source links before making a decision.",
    ]

    for p in practical_points:
        html_parts.append(f"""
  <p style="font-size: 18px; margin-bottom: 18px;">
    {esc(p)}
  </p>
""")

    html_parts.append("""
  <h2 style="font-size: 28px; font-weight: 800; margin: 34px 0 18px; color: #111827;">
    Quick Comparison Table
  </h2>

  <div style="overflow-x: auto; margin-bottom: 30px;">
    <table style="width: 100%; border-collapse: collapse; font-size: 16px;">
      <thead>
        <tr style="background: #0b63ff; color: #ffffff;">
          <th style="padding: 12px; border: 1px solid #e5e7eb; text-align: left;">Source</th>
          <th style="padding: 12px; border: 1px solid #e5e7eb; text-align: left;">Best For</th>
          <th style="padding: 12px; border: 1px solid #e5e7eb; text-align: left;">Key Point</th>
        </tr>
      </thead>
      <tbody>
""")

    for row in rows:
        html_parts.append(f"""
        <tr>
          <td style="padding: 12px; border: 1px solid #e5e7eb;"><strong>{esc(row["source"])}</strong></td>
          <td style="padding: 12px; border: 1px solid #e5e7eb;">{esc(row["best_for"])}</td>
          <td style="padding: 12px; border: 1px solid #e5e7eb;">{esc(row["key_point"])}</td>
        </tr>
""")

    html_parts.append("""
      </tbody>
    </table>
  </div>

  <h2 style="font-size: 28px; font-weight: 800; margin: 34px 0 18px; color: #111827;">
    How to Use This Trend in a Practical Way
  </h2>
""")

    how_to_steps = [
        f"Start by understanding the main reason people are searching for {trend_title}.",
        "Open the official websites or trusted sources before trusting summaries or social media posts.",
        "Write down what you actually need: saving time, learning faster, planning a trip, improving productivity, or comparing tools.",
        "Choose one or two tools to test for a week before changing your full workflow.",
        "Keep a simple note of what worked, what did not work, and whether the tool is worth using again.",
    ]

    for idx, step in enumerate(how_to_steps, start=1):
        html_parts.append(f"""
  <h3 style="font-size: 23px; font-weight: 800; margin: 26px 0 10px; color: #0b63ff;">
    {idx}. {esc(step)}
  </h3>
""")

    html_parts.append("""
  <h2 style="font-size: 28px; font-weight: 800; margin: 34px 0 18px; color: #111827;">
    What the Sources Are Saying
  </h2>
""")

    for i, bullet in enumerate(source_bullets, start=1):
        html_parts.append(f"""
  <p style="font-size: 18px; margin-bottom: 16px;">
    <strong>{i}.</strong> {esc(bullet)}
  </p>
""")

    html_parts.append("""
  <h2 style="font-size: 28px; font-weight: 800; margin: 34px 0 18px; color: #111827;">
    FAQ
  </h2>

  <h3 style="font-size: 22px; font-weight: 800; margin: 24px 0 10px; color: #0b63ff;">
    Is this a news article?
  </h3>

  <p style="font-size: 18px; margin-bottom: 18px;">
    No. This is a practical Smart Life Tools guide based on trend signals and public source listings. It is designed to help readers understand useful tools and actions related to the topic.
  </p>

  <h3 style="font-size: 22px; font-weight: 800; margin: 24px 0 10px; color: #0b63ff;">
    Are the tools and prices always accurate?
  </h3>

  <p style="font-size: 18px; margin-bottom: 18px;">
    Tool features, free plans, and prices can change. Always check the official website before signing up, paying, or making an important decision.
  </p>

  <h3 style="font-size: 22px; font-weight: 800; margin: 24px 0 10px; color: #0b63ff;">
    Why are multiple sources used?
  </h3>

  <p style="font-size: 18px; margin-bottom: 18px;">
    Using multiple sources helps avoid copying a single article and gives readers a broader view of the topic.
  </p>

  <h3 style="font-size: 22px; font-weight: 800; margin: 24px 0 10px; color: #0b63ff;">
    Should I rely only on this article?
  </h3>

  <p style="font-size: 18px; margin-bottom: 28px;">
    No. Use this article as a starting point, then check official websites, trusted documentation, and updated sources.
  </p>
""")

    html_parts.append(f"""
  <h2 style="font-size: 28px; font-weight: 800; margin: 34px 0 18px; color: #111827;">
    Final Thoughts
  </h2>

  <p style="font-size: 18px; margin-bottom: 18px;">
    Trends move quickly, but useful tools and habits remain valuable for longer. The best approach is to use trending topics as signals, then turn them into practical guides that help readers save time, make better choices, and understand their options.
  </p>

  <p style="font-size: 18px; margin-bottom: 28px;">
    This article was generated on {esc(now_date)} from trend signals and multiple public article listings. Review the source links below for the latest details.
  </p>

  <p style="font-size: 15px; color: #4b5563; margin: 28px 0; padding: 14px 16px; background: #f9fafb; border-left: 4px solid #0b63ff;">
    <strong>Disclaimer:</strong> This article is for informational purposes only. It does not provide medical, financial, legal, or professional advice. Always verify information from official sources.
  </p>
""")

    html_parts.append("""
  <h2 style="font-size: 26px; font-weight: 800; margin: 34px 0 16px; color: #111827;">
    Related Smart Life Tools Guides
  </h2>

  <ul style="font-size: 17px; margin-bottom: 28px; padding-left: 22px;">
    <li style="margin-bottom: 10px;">
      <a href="/search/label/AI%20Tools" style="color: #0b63ff; font-weight: 700;">More AI Tools Guides</a>
    </li>
    <li style="margin-bottom: 10px;">
      <a href="/search/label/Productivity" style="color: #0b63ff; font-weight: 700;">More Productivity Apps and Tips</a>
    </li>
    <li style="margin-bottom: 10px;">
      <a href="/search/label/How%20To" style="color: #0b63ff; font-weight: 700;">More How-To Guides</a>
    </li>
  </ul>
""")

    html_parts.append("""
  <h2 style="font-size: 26px; font-weight: 800; margin: 34px 0 16px; color: #111827;">
    Sources & Further Reading
  </h2>

  <ul style="font-size: 17px; margin-bottom: 0; padding-left: 22px;">
""")

    if trend.get("link"):
        html_parts.append(f"""
    <li style="margin-bottom: 10px;">
      <a href="{esc(trend["link"])}" target="_blank" rel="nofollow noopener" style="color: #0b63ff; font-weight: 700;">Trend source: {esc(trend_title)}</a>
    </li>
""")

    for s in sources[:MAX_SOURCES_PER_TOPIC]:
        if not s.get("url"):
            continue

        label = s.get("title") or s.get("domain") or "Source"
        html_parts.append(f"""
    <li style="margin-bottom: 10px;">
      <a href="{esc(s["url"])}" target="_blank" rel="nofollow noopener" style="color: #0b63ff; font-weight: 700;">{esc(label)}</a>
    </li>
""")

    html_parts.append("""
  </ul>
</div>
""")

    schema = {
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        "headline": title,
        "description": meta,
        "author": {
            "@type": "Organization",
            "name": SITE_NAME,
        },
        "publisher": {
            "@type": "Organization",
            "name": SITE_NAME,
        },
        "datePublished": now_iso,
        "dateModified": now_iso,
        "keywords": labels,
        "articleSection": category,
    }

    if image_url:
        schema["image"] = [image_url]

    html_parts.append(
        '<script type="application/ld+json">'
        + json.dumps(schema, ensure_ascii=False)
        + "</script>"
    )

    return "\n".join(html_parts)


# =========================
# MAIN
# =========================


def main() -> None:
    ensure_dirs()

    trends = fetch_google_trends()

    if len(trends) < MAX_ARTICLES_TO_GENERATE:
        print(
            "Google Trends did not return enough niche topics. Using niche RSS sources..."
        )
        trends = fetch_rss_trending_topics()

    if not trends:
        print("No relevant trends found from Google Trends or RSS sources.")
        return

    (DATA_DIR / "trends.json").write_text(
        json.dumps(trends, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\nFetched candidate trends:")
    for t in trends[:20]:
        print(f"- [{t.get('score')}] {t.get('source')} | {t.get('title')}")

    csv_rows = []
    generated_count = 0
    seen_titles = set()
    seen_slugs = set()

    for index, trend in enumerate(trends, start=1):
        if generated_count >= MAX_ARTICLES_TO_GENERATE:
            break

        topic = trend["title"]

        if not is_publishable_topic(topic):
            print(f"Skipping weak SEO topic: {topic}")
            continue

        category, labels = category_for_topic(topic)
        title = make_article_title(topic, category)
        meta = make_meta_description(title)

        title_key = " ".join(title.lower().strip().split())
        if title_key in seen_titles:
            print(f"Skipping duplicate article title: {title}")
            continue

        slug = slugify(title)[:90]
        if slug in seen_slugs:
            print(f"Skipping duplicate slug: {slug}")
            continue

        seen_titles.add(title_key)
        seen_slugs.add(slug)

        print(f"\n[{generated_count + 1}/{MAX_ARTICLES_TO_GENERATE}] Topic: {topic}")
        print(f"Article: {title}")

        gdelt_sources = []
        if USE_GDELT:
            gdelt_sources = fetch_gdelt_articles(
                topic,
                max_records=MAX_SOURCES_PER_TOPIC,
            )
            time.sleep(1)

        rss_sources = fetch_rss_articles(topic, max_records=MAX_SOURCES_PER_TOPIC)

        combined_sources = []
        seen_urls = set()

        for item in gdelt_sources + rss_sources:
            url = item.get("url", "")
            if not url or url in seen_urls:
                continue

            seen_urls.add(url)
            combined_sources.append(item)

        combined_sources = combined_sources[:MAX_SOURCES_PER_TOPIC]

        if len(combined_sources) < 2:
            print("Not enough sources. Skipping.")
            continue

        image_url, image_credit = pick_article_image(
            trend,
            combined_sources,
            title,
            category,
        )

        image_file = download_image(image_url, slug) if image_url else ""

        html_content = render_article_html(
            title,
            meta,
            category,
            labels,
            trend,
            combined_sources,
            image_url=image_url,
            image_credit=image_credit,
        )

        html_path = HTML_DIR / f"{slug}.html"
        json_path = DATA_DIR / f"{slug}.json"

        html_path.write_text(html_content, encoding="utf-8")

        json_path.write_text(
            json.dumps(
                {
                    "trend": trend,
                    "title": title,
                    "meta": meta,
                    "category": category,
                    "labels": labels,
                    "sources": combined_sources,
                    "image_url": image_url,
                    "image_credit": image_credit,
                    "image_file": image_file,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        csv_rows.append(
            {
                "title": title,
                "search_description": meta,
                "category": category,
                "labels": labels,
                "trend_title": topic,
                "trend_country": trend.get("country", ""),
                "source_count": len(combined_sources),
                "image_url": image_url,
                "image_credit": image_credit,
                "image_file": image_file,
                "html_file": str(html_path),
                "status": "Draft",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )

        generated_count += 1
        time.sleep(2)

    if not csv_rows:
        print("No publishable articles were generated after filtering.")
        return

    with CSV_FILE.open("w", encoding="utf-8-sig", newline="") as f:
        fieldnames = [
            "title",
            "search_description",
            "category",
            "labels",
            "trend_title",
            "trend_country",
            "source_count",
            "image_url",
            "image_credit",
            "image_file",
            "html_file",
            "status",
            "created_at",
        ]

        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(csv_rows)

    print("\nDone.")
    print(f"Generated articles: {len(csv_rows)}")
    print(f"HTML files: {HTML_DIR}")
    print(f"Images: {IMAGES_DIR}")
    print(f"CSV file: {CSV_FILE}")


if __name__ == "__main__":
    main()
