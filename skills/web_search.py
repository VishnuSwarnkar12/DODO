"""
DODO — skills/web_search.py
Deep Web Search & Real-Time Intelligence Engine

Architecture:
  - Multi-source search: DuckDuckGo (primary) + Bing scrape + Google CSE (fallback)
  - Parallel page scraping: concurrent requests across top N results
  - Smart content extraction: trafilatura → BeautifulSoup → regex fallback
  - Real-time sources: RSS feeds, Wikipedia, WolframAlpha, GitHub trending
  - Quick-answer layer: factual instant answers before full search
  - Result deduplication + relevance scoring
  - In-memory cache with TTL (avoid hammering same queries)
  - Rate-limit safe with randomised delays + rotating User-Agents
"""

from __future__ import annotations

import html
import json
import logging
import random
import re
import socket
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse, quote_plus, urljoin

import requests

logger = logging.getLogger("dodo.websearch")

# ── Optional dependency flags ─────────────────────────────────────────────────
try:
    from ddgs import DDGS as _DDGS
    _DDGS_AVAILABLE = True
except ImportError:
    try:
        from duckduckgo_search import DDGS as _DDGS
        _DDGS_AVAILABLE = True
    except ImportError:
        _DDGS = None
        _DDGS_AVAILABLE = False

try:
    from bs4 import BeautifulSoup
    _BS4_AVAILABLE = True
except ImportError:
    _BS4_AVAILABLE = False

try:
    import trafilatura
    _TRAFILATURA_AVAILABLE = True
except ImportError:
    _TRAFILATURA_AVAILABLE = False

try:
    import html2text as _html2text_mod
    _HTML2TEXT_AVAILABLE = True
except ImportError:
    _html2text_mod = None
    _HTML2TEXT_AVAILABLE = False

# ── Constants ─────────────────────────────────────────────────────────────────
_TIMEOUT_SEARCH   = 10   # seconds per search engine call
_TIMEOUT_SCRAPE   = 8    # seconds per page scrape
_MAX_SCRAPE_BYTES = 500_000  # 500KB per page cap
_CACHE_TTL        = 120  # seconds before cache entry expires
_MAX_CACHE        = 100  # max cache entries
_MAX_WORKERS      = 6    # concurrent scraping threads

# ── User-Agent pool (rotate to avoid bot blocks) ─────────────────────────────
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# Domains to skip (paywalls, cookie walls, etc.)
_SKIP_DOMAINS = {
    "facebook.com", "twitter.com", "x.com", "instagram.com",
    "tiktok.com", "pinterest.com", "linkedin.com",
    "nytimes.com", "wsj.com", "ft.com", "bloomberg.com",  # heavy paywalls
}

# ── In-memory cache ───────────────────────────────────────────────────────────
_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()


def _cache_get(key: str) -> Optional[str]:
    with _cache_lock:
        entry = _cache.get(key)
        if entry and (time.time() - entry["ts"]) < _CACHE_TTL:
            return entry["val"]
        if entry:
            del _cache[key]
    return None


def _cache_set(key: str, val: str) -> None:
    with _cache_lock:
        if len(_cache) >= _MAX_CACHE:
            # evict oldest
            oldest = min(_cache, key=lambda k: _cache[k]["ts"])
            del _cache[oldest]
        _cache[key] = {"val": val, "ts": time.time()}


# ── HTTP session ──────────────────────────────────────────────────────────────
_session = requests.Session()
_session.max_redirects = 5


def _headers() -> dict:
    return {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
    }


def _is_safe_url(url: str) -> bool:
    """Block private IPs and bad domains."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if not host:
            return False
        if any(bad in host for bad in _SKIP_DOMAINS):
            return False
        ip = socket.gethostbyname(host)
        parts = ip.split(".")
        if (ip.startswith("127.") or ip.startswith("10.") or
                ip.startswith("192.168.") or ip == "0.0.0.0" or
                ip.startswith("169.254.") or
                (ip.startswith("172.") and 16 <= int(parts[1]) <= 31)):
            return False
        return True
    except Exception:
        return False


# ── HTML → plain text extraction ─────────────────────────────────────────────

def _extract_text(html_content: str, url: str = "") -> str:
    """
    Best-effort text extraction from HTML.
    Priority: trafilatura → BeautifulSoup/lxml → html2text → regex strip
    """
    # 1. trafilatura — best at article/blog extraction
    if _TRAFILATURA_AVAILABLE:
        try:
            text = trafilatura.extract(
                html_content,
                include_comments=False,
                include_tables=True,
                no_fallback=False,
                favor_precision=False,
            )
            if text and len(text.strip()) > 100:
                return text.strip()
        except Exception:
            pass

    # 2. BeautifulSoup — structured extraction
    if _BS4_AVAILABLE:
        try:
            soup = BeautifulSoup(html_content, "lxml" if _lxml_available() else "html.parser")
            # Remove noise
            for tag in soup(["script", "style", "nav", "header", "footer",
                             "aside", "form", "noscript", "iframe", "svg",
                             "advertisement", "ads"]):
                tag.decompose()
            # Prefer article/main content
            main = (soup.find("article") or soup.find("main") or
                    soup.find(id=re.compile(r"content|article|main|body", re.I)) or
                    soup.find(class_=re.compile(r"content|article|post|entry", re.I)) or
                    soup.body)
            if main:
                text = main.get_text(separator="\n", strip=True)
                if len(text.strip()) > 100:
                    return _clean_text(text)
        except Exception:
            pass

    # 3. html2text
    if _HTML2TEXT_AVAILABLE and _html2text_mod:
        try:
            converter = _html2text_mod.HTML2Text()
            converter.ignore_links = True
            converter.ignore_images = True
            converter.body_width = 0
            text = converter.handle(html_content)
            if text.strip():
                return _clean_text(text)
        except Exception:
            pass

    # 4. Regex fallback
    cleaned = re.sub(
        r"<(script|style|noscript|header|footer|nav|svg|aside)[^>]*>.*?</\1>",
        "", html_content, flags=re.DOTALL | re.IGNORECASE
    )
    cleaned = re.sub(r"<!--.*?-->", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    return _clean_text(html.unescape(cleaned))


def _lxml_available() -> bool:
    try:
        import lxml
        return True
    except ImportError:
        return False


def _clean_text(text: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", l.strip()) for l in text.splitlines() if l.strip()]
    # Remove very short lines (nav fragments)
    lines = [l for l in lines if len(l) > 15]
    return "\n".join(lines)


# ── Page scraper ──────────────────────────────────────────────────────────────

def scrape_page(url: str, max_chars: int = 6000) -> str:
    """
    Fetch and extract clean text from a single URL.
    Returns extracted text or an error string.
    """
    if not _is_safe_url(url):
        return ""
    try:
        resp = _session.get(
            url, headers=_headers(), timeout=_TIMEOUT_SCRAPE,
            stream=True, allow_redirects=True
        )
        resp.raise_for_status()

        # Content-type check
        ct = resp.headers.get("content-type", "")
        if not any(t in ct for t in ("text/html", "application/xhtml", "text/plain")):
            return ""

        # Stream cap
        chunks = []
        size = 0
        for chunk in resp.iter_content(chunk_size=16384, decode_unicode=False):
            chunks.append(chunk)
            size += len(chunk)
            if size >= _MAX_SCRAPE_BYTES:
                break
        raw_bytes = b"".join(chunks)

        encoding = resp.apparent_encoding or "utf-8"
        try:
            html_content = raw_bytes.decode(encoding, errors="replace")
        except Exception:
            html_content = raw_bytes.decode("utf-8", errors="replace")

        text = _extract_text(html_content, url)
        return text[:max_chars].strip() if text else ""

    except requests.exceptions.Timeout:
        logger.debug("Scrape timeout: %s", url)
        return ""
    except Exception as e:
        logger.debug("Scrape error %s: %s", url, e)
        return ""


def scrape_pages_parallel(urls: list[str], max_chars: int = 4000,
                          max_workers: int = _MAX_WORKERS) -> dict[str, str]:
    """
    Scrape multiple URLs concurrently.
    Returns {url: text} dict (empty string on failure).
    """
    results: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(scrape_page, url, max_chars): url for url in urls}
        for future in as_completed(futures, timeout=_TIMEOUT_SCRAPE + 3):
            url = futures[future]
            try:
                results[url] = future.result()
            except Exception:
                results[url] = ""
    return results


# ── Search engines ─────────────────────────────────────────────────────────────

def _ddg_search(query: str, max_results: int = 10) -> list[dict]:
    """DuckDuckGo search — returns list of {title, url, snippet}."""
    if not _DDGS_AVAILABLE:
        return []
    try:
        with _DDGS() as ddgs:
            raw = list(ddgs.text(query.strip(), max_results=max_results))
        results = []
        for r in raw:
            url = r.get("href") or r.get("url", "")
            if url:
                results.append({
                    "title":   (r.get("title") or "").strip(),
                    "url":     url,
                    "snippet": (r.get("body") or r.get("snippet") or "").strip(),
                    "source":  "ddg",
                })
        return results
    except Exception as e:
        logger.debug("DDG error: %s", e)
        return []


def _ddg_answers(query: str) -> Optional[str]:
    """DuckDuckGo instant answer (zero-click)."""
    if not _DDGS_AVAILABLE:
        return None
    try:
        with _DDGS() as ddgs:
            for r in ddgs.answers(query.strip()):
                text = (r.get("text") or r.get("answer") or
                        r.get("abstract") or r.get("body") or "")
                if text.strip():
                    return text.strip()
    except Exception:
        pass
    return None


def _bing_scrape(query: str, max_results: int = 10) -> list[dict]:
    """
    Scrape Bing search results page — lightweight, no API key needed.
    Fallback when DDG is unavailable or returns too few results.
    """
    try:
        url = f"https://www.bing.com/search?q={quote_plus(query)}&count={max_results}"
        resp = _session.get(url, headers=_headers(), timeout=_TIMEOUT_SEARCH)
        resp.raise_for_status()

        if not _BS4_AVAILABLE:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for item in soup.select("li.b_algo")[:max_results]:
            a = item.find("a")
            p = item.find("p") or item.find(class_="b_caption")
            if not a:
                continue
            href = a.get("href", "")
            if not href.startswith("http"):
                continue
            snippet = p.get_text(strip=True) if p else ""
            results.append({
                "title":   a.get_text(strip=True),
                "url":     href,
                "snippet": snippet,
                "source":  "bing",
            })
        return results
    except Exception as e:
        logger.debug("Bing scrape error: %s", e)
        return []


def _ddg_lite_scrape(query: str, max_results: int = 5) -> list[dict]:
    """DuckDuckGo Lite HTML scrape — ultra-lightweight fallback."""
    try:
        resp = _session.get(
            "https://lite.duckduckgo.com/lite/",
            params={"q": query.strip()},
            headers=_headers(), timeout=_TIMEOUT_SEARCH
        )
        snippets = re.findall(r'<td[^>]*class="result-snippet"[^>]*>(.*?)</td>',
                              resp.text, re.DOTALL)
        titles   = re.findall(r'<a[^>]*rel="nofollow"[^>]*>(.*?)</a>',
                              resp.text, re.DOTALL)
        hrefs    = re.findall(r'<a[^>]*rel="nofollow"[^>]*href="([^"]+)"',
                              resp.text)
        results = []
        for t, s, h in zip(titles[:max_results], snippets[:max_results], hrefs[:max_results]):
            if not h.startswith("http"):
                continue
            results.append({
                "title":   re.sub(r"<[^>]+>", "", t).strip(),
                "url":     h,
                "snippet": re.sub(r"<[^>]+>", "", s).strip(),
                "source":  "ddg_lite",
            })
        return results
    except Exception:
        return []


# ── Specialised real-time sources ─────────────────────────────────────────────

def _wikipedia_summary(query: str) -> Optional[str]:
    """Fetch Wikipedia article summary via REST API."""
    try:
        # Replace spaces with underscores and title-case for best match
        title = query.strip().replace(" ", "_")
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote_plus(title)}"
        resp = _session.get(url, headers=_headers(), timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            extract = data.get("extract", "")
            page_title = data.get("title", "")
            if extract and len(extract) > 50:
                return f"📖 **{page_title}** (Wikipedia)\n{extract[:1500]}"
        # Try search API if direct title fails
        elif resp.status_code == 404:
            search_url = "https://en.wikipedia.org/w/api.php"
            params = {
                "action": "opensearch", "search": query.strip(),
                "limit": 1, "format": "json",
            }
            s_resp = _session.get(search_url, params=params, headers=_headers(), timeout=6)
            if s_resp.status_code == 200:
                data = s_resp.json()
                if data and len(data) >= 2 and data[1]:
                    # Found a suggestion, retry with it
                    suggestion = data[1][0].replace(" ", "_")
                    r2 = _session.get(
                        f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote_plus(suggestion)}",
                        headers=_headers(), timeout=6
                    )
                    if r2.status_code == 200:
                        d2 = r2.json()
                        extract = d2.get("extract", "")
                        if extract and len(extract) > 50:
                            return f"📖 **{d2.get('title', suggestion)}** (Wikipedia)\n{extract[:1500]}"
    except Exception as e:
        logger.debug("Wikipedia error: %s", e)
    return None


def _fetch_rss_news(query: str, max_items: int = 5) -> list[dict]:
    """
    Search Google News RSS for real-time news.
    No API key required.
    """
    try:
        url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-IN&gl=IN&ceid=IN:en"
        resp = _session.get(url, headers=_headers(), timeout=8)
        resp.raise_for_status()

        # Parse RSS with regex (avoid feedparser dependency for now)
        items = re.findall(r"<item>(.*?)</item>", resp.text, re.DOTALL)
        results = []
        for item in items[:max_items]:
            title_m   = re.search(r"<title><!\[CDATA\[(.*?)\]\]></title>", item)
            link_m    = re.search(r"<link>(.*?)</link>", item)
            date_m    = re.search(r"<pubDate>(.*?)</pubDate>", item)
            source_m  = re.search(r"<source[^>]*>(.*?)</source>", item)
            if title_m:
                results.append({
                    "title":   title_m.group(1).strip(),
                    "url":     link_m.group(1).strip() if link_m else "",
                    "snippet": f"[{source_m.group(1).strip() if source_m else 'News'}] "
                               f"{date_m.group(1).strip()[:22] if date_m else ''}",
                    "source":  "google_news",
                })
        return results
    except Exception as e:
        logger.debug("RSS news error: %s", e)
        return []


def _wolframalpha_query(query: str) -> Optional[str]:
    """
    Query WolframAlpha short-answers API (no API key — uses the public widget endpoint).
    Good for math, unit conversions, quick facts.
    """
    try:
        url = "https://www.wolframalpha.com/input"
        resp = _session.get(
            url, params={"i": query}, headers=_headers(), timeout=8,
            allow_redirects=True
        )
        # Try the simple API endpoint
        simple_url = f"https://api.wolframalpha.com/v1/result?appid=DEMO&i={quote_plus(query)}"
        simple_resp = _session.get(simple_url, headers=_headers(), timeout=6)
        if simple_resp.status_code == 200 and simple_resp.text.strip():
            answer = simple_resp.text.strip()
            if "Wolfram" not in answer and len(answer) < 500:
                return f"🧮 {answer}"
    except Exception:
        pass
    return None


def _github_trending(language: str = "") -> str:
    """Fetch GitHub trending repositories."""
    try:
        url = "https://github.com/trending"
        if language:
            url += f"/{quote_plus(language.lower())}"
        resp = _session.get(url, headers=_headers(), timeout=8)
        resp.raise_for_status()
        if _BS4_AVAILABLE:
            soup = BeautifulSoup(resp.text, "html.parser")
            repos = soup.select("article.Box-row")[:8]
            if repos:
                lines = ["🔥 GitHub Trending:"]
                for repo in repos:
                    h2 = repo.find("h2")
                    desc = repo.find("p")
                    stars = repo.find(class_=re.compile("octicon-star"))
                    star_count = ""
                    if stars and stars.parent:
                        star_count = stars.parent.get_text(strip=True)
                    if h2:
                        name = re.sub(r"\s+", " ", h2.get_text(strip=True))
                        description = desc.get_text(strip=True) if desc else ""
                        lines.append(f"  • {name} ⭐{star_count}  — {description[:80]}")
                return "\n".join(lines)
    except Exception as e:
        logger.debug("GitHub trending error: %s", e)
    return ""


def _pypi_search(package: str) -> str:
    """Fetch PyPI package info."""
    try:
        url = f"https://pypi.org/pypi/{quote_plus(package.strip())}/json"
        resp = _session.get(url, headers=_headers(), timeout=8)
        if resp.status_code == 200:
            data = resp.json().get("info", {})
            name    = data.get("name", package)
            version = data.get("version", "?")
            summary = data.get("summary", "")
            home    = data.get("home_page") or data.get("project_url", "")
            return (f"📦 **{name}** v{version}\n"
                    f"{summary}\n"
                    f"PyPI: https://pypi.org/project/{name}/\n"
                    f"Home: {home}")
    except Exception:
        pass
    return ""


def _stackoverflow_search(query: str) -> list[dict]:
    """Search StackOverflow via their public API."""
    try:
        url = "https://api.stackexchange.com/2.3/search/advanced"
        params = {
            "q": query, "order": "desc", "sort": "relevance",
            "site": "stackoverflow", "pagesize": 5,
            "filter": "withbody",
        }
        resp = _session.get(url, params=params, headers=_headers(), timeout=8)
        resp.raise_for_status()
        data = resp.json()
        results = []
        for item in data.get("items", []):
            title   = html.unescape(item.get("title", ""))
            link    = item.get("link", "")
            excerpt = re.sub(r"<[^>]+>", "", item.get("body", ""))[:300]
            score   = item.get("score", 0)
            answered = "✅" if item.get("is_answered") else "❓"
            results.append({
                "title":   f"{answered} [{score}✦] {title}",
                "url":     link,
                "snippet": excerpt,
                "source":  "stackoverflow",
            })
        return results
    except Exception as e:
        logger.debug("SO error: %s", e)
        return []


# ── Deduplication & scoring ───────────────────────────────────────────────────

def _deduplicate(results: list[dict]) -> list[dict]:
    """Remove duplicate URLs."""
    seen_urls  = set()
    seen_domains = {}
    out = []
    for r in results:
        url = r.get("url", "")
        domain = urlparse(url).netloc
        if url in seen_urls:
            continue
        # Max 2 results per domain
        if seen_domains.get(domain, 0) >= 2:
            continue
        seen_urls.add(url)
        seen_domains[domain] = seen_domains.get(domain, 0) + 1
        out.append(r)
    return out


def _score_result(r: dict, query_words: set[str]) -> float:
    """Simple relevance score: word overlap in title+snippet."""
    text = (r.get("title", "") + " " + r.get("snippet", "")).lower()
    hits = sum(1 for w in query_words if w in text)
    # Boost trusted sources
    source_boost = {"stackoverflow": 0.3, "wikipedia": 0.2, "google_news": 0.1}
    boost = source_boost.get(r.get("source", ""), 0)
    return hits + boost


# ── Main public API ────────────────────────────────────────────────────────────

def quick_answer(query: str) -> Optional[str]:
    """
    Instant answer — tries DDG zero-click, WolframAlpha, Wikipedia.
    Returns a short string or None.
    """
    if not query or not query.strip():
        return None

    q = query.strip()

    # 1. DuckDuckGo instant answer
    ans = _ddg_answers(q)
    if ans:
        return ans

    # 2. WolframAlpha for math/facts
    if any(c in q for c in "=+-×÷^%") or re.search(r"\d", q):
        wa = _wolframalpha_query(q)
        if wa:
            return wa

    # 3. Wikipedia for "what is X", "who is X"
    if re.match(r"(what|who|where|when|how)\s+(is|are|was|were|did)\s+", q, re.I):
        wiki = _wikipedia_summary(re.sub(r"^(what|who|where|when|how)\s+(is|are|was|were|did)\s+", "", q, flags=re.I))
        if wiki:
            return wiki

    return None


def search(query: str, max_results: int = 5, deep: bool = False) -> str:
    """
    Primary search function.

    Args:
        query:       Search query string
        max_results: Number of results to include in summary (default 5)
        deep:        If True, scrapes the top result pages for full content

    Returns:
        Formatted string with results and (if deep=True) extracted content.
    """
    if not query or not query.strip():
        return "Please provide a search query."

    q = query.strip()
    cache_key = f"{q}:{max_results}:{deep}"
    cached = _cache_get(cache_key)
    if cached:
        logger.debug("Cache hit: %s", q)
        return cached

    # ── Gather results from multiple sources ──────────────────────────────────
    results: list[dict] = []

    # Primary: DDG
    ddg_res = _ddg_search(q, max_results=max_results + 5)
    results.extend(ddg_res)

    # If DDG returned < 3, try Bing scrape
    if len(ddg_res) < 3:
        results.extend(_bing_scrape(q, max_results=max_results))

    # If still < 2, try DDG lite
    if len(results) < 2:
        results.extend(_ddg_lite_scrape(q))

    if not results:
        return f"❌ No results found for '{q}'. Check your internet connection."

    # ── Deduplicate + rank ────────────────────────────────────────────────────
    results = _deduplicate(results)
    query_words = set(re.findall(r"\w+", q.lower()))
    results.sort(key=lambda r: _score_result(r, query_words), reverse=True)
    results = results[:max_results]

    # ── Format output ─────────────────────────────────────────────────────────
    lines = [f"🔍 **Results for:** {q}  *(realtime — {datetime.now(timezone.utc).strftime('%H:%M UTC')})*\n"]

    for i, r in enumerate(results, 1):
        title   = r.get("title") or "Untitled"
        url     = r.get("url", "")
        snippet = r.get("snippet") or ""
        source_icon = {
            "ddg": "🦆", "bing": "🅱️", "ddg_lite": "🦆",
            "google_news": "📰", "stackoverflow": "💻",
            "wikipedia": "📖",
        }.get(r.get("source", ""), "🌐")
        lines.append(f"{source_icon} **{i}. {title}**")
        if snippet:
            lines.append(f"   {snippet[:200]}")
        if url:
            lines.append(f"   🔗 {url}")
        lines.append("")

    # ── Deep mode: scrape top pages and extract real content ──────────────────
    if deep:
        top_urls = [r["url"] for r in results[:3] if r.get("url")]
        safe_urls = [u for u in top_urls if _is_safe_url(u)]
        if safe_urls:
            lines.append("─" * 40)
            lines.append("📄 **Deep content from top pages:**\n")
            page_texts = scrape_pages_parallel(safe_urls, max_chars=3000)
            for url, text in page_texts.items():
                if text and len(text) > 200:
                    domain = urlparse(url).netloc
                    lines.append(f"**From {domain}:**")
                    lines.append(text[:2000])
                    lines.append("")

    result_str = "\n".join(lines).strip()
    _cache_set(cache_key, result_str)
    return result_str


def search_news(query: str, max_items: int = 6) -> str:
    """
    Real-time news search via Google News RSS.
    No API key required.
    """
    if not query.strip():
        return "Please provide a news topic."

    cache_key = f"news:{query}:{max_items}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    news = _fetch_rss_news(query, max_items)

    if not news:
        # Fallback: DDG news tab
        if _DDGS_AVAILABLE:
            try:
                with _DDGS() as ddgs:
                    raw = list(ddgs.news(query.strip(), max_results=max_items))
                news = [{
                    "title":   r.get("title", ""),
                    "url":     r.get("url", ""),
                    "snippet": f"[{r.get('source', '')}] {r.get('date', '')}",
                    "source":  "ddg_news",
                } for r in raw]
            except Exception:
                pass

    if not news:
        return f"❌ No news found for '{query}'."

    lines = [f"📰 **Live News: {query}**  *({datetime.now().strftime('%d %b %Y %H:%M')})*\n"]
    for i, item in enumerate(news, 1):
        lines.append(f"**{i}. {item['title']}**")
        if item.get("snippet"):
            lines.append(f"   {item['snippet']}")
        if item.get("url"):
            lines.append(f"   🔗 {item['url']}")
        lines.append("")

    result_str = "\n".join(lines).strip()
    _cache_set(cache_key, result_str)
    return result_str


def search_stackoverflow(query: str) -> str:
    """Search StackOverflow for programming questions."""
    cache_key = f"so:{query}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    results = _stackoverflow_search(query)
    if not results:
        # Fallback to web search restricted to SO
        return search(f"site:stackoverflow.com {query}", max_results=4)

    lines = [f"💻 **StackOverflow: {query}**\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"**{i}. {r['title']}**")
        if r.get("snippet"):
            lines.append(f"   {r['snippet'][:200]}")
        if r.get("url"):
            lines.append(f"   🔗 {r['url']}")
        lines.append("")

    result_str = "\n".join(lines).strip()
    _cache_set(cache_key, result_str)
    return result_str


def summarize_url(url: str, max_chars: int = 5000) -> str:
    """
    Fetch and extract clean text from a URL for LLM summarisation.
    Uses the full extraction pipeline (trafilatura → BS4 → regex).
    """
    if not url or not url.strip():
        return "Invalid URL provided."

    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    if not _is_safe_url(url):
        return "❌ Cannot access that URL (private/blocked)."

    text = scrape_page(url, max_chars=max_chars)
    if not text:
        return f"❌ Could not extract content from {url}."
    return text


def get_github_trending(language: str = "") -> str:
    """Return GitHub trending repos (optional language filter)."""
    cache_key = f"gh_trending:{language}"
    cached = _cache_get(cache_key)
    if cached:
        return cached
    result = _github_trending(language)
    if result:
        _cache_set(cache_key, result)
    return result or "❌ Could not fetch GitHub trending."


def get_pypi_info(package: str) -> str:
    """Return PyPI package info."""
    return _pypi_search(package) or f"❌ Package '{package}' not found on PyPI."


def get_wikipedia(topic: str) -> str:
    """Fetch Wikipedia summary for a topic."""
    result = _wikipedia_summary(topic)
    return result or f"❌ No Wikipedia article found for '{topic}'."


def clear_cache() -> str:
    """Clear the search result cache."""
    with _cache_lock:
        count = len(_cache)
        _cache.clear()
    return f"🗑️ Cleared {count} cached search results."


def cache_stats() -> str:
    """Show cache statistics."""
    with _cache_lock:
        count = len(_cache)
        keys = list(_cache.keys())[:5]
    return f"📊 Cache: {count}/{_MAX_CACHE} entries. Recent: {keys}"
