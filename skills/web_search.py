"""
DODO — skills/web_search.py
Web search and webpage content extraction skill for DODO AI assistant.
Provides instant factual answers, web search results, and webpage text extraction.
"""

from __future__ import annotations

import html
import re
from typing import Optional

import requests

try:
    from ddgs import DDGS
    _DDGS_AVAILABLE = True
except ImportError:
    try:
        from duckduckgo_search import DDGS
        _DDGS_AVAILABLE = True
    except ImportError:
        DDGS = None  # type: ignore[assignment,misc]
        _DDGS_AVAILABLE = False

try:
    import html2text
    _HTML2TEXT_AVAILABLE = True
except ImportError:
    html2text = None  # type: ignore[assignment]
    _HTML2TEXT_AVAILABLE = False


_NOT_INSTALLED_MSG = (
    "DuckDuckGo search library is not installed. "
    "Please install it using 'pip install duckduckgo_search'."
)


def quick_answer(query: str) -> Optional[str]:
    """
    Retrieve a short factual instant answer using DuckDuckGo.

    Args:
        query: The search or question query string.

    Returns:
        A concise factual answer string if available, otherwise None.
        If the library is not installed, returns a helpful message.
    """
    if not _DDGS_AVAILABLE:
        return _NOT_INSTALLED_MSG

    if not query or not query.strip():
        return None

    try:
        with DDGS() as ddgs:
            results = ddgs.answers(query.strip())
            if results:
                for r in results:
                    text = (
                        r.get("text")
                        or r.get("answer")
                        or r.get("abstract")
                        or r.get("body")
                    )
                    if text and text.strip():
                        return text.strip()
        return None
    except Exception:
        return None


def search(query: str, max_results: int = 3) -> str:
    """
    Perform a web search using DuckDuckGo and return formatted top results.

    Args:
        query: The search term or question.
        max_results: Maximum number of search results to return (default: 3).

    Returns:
        A formatted string with numbered results (title + snippet), or an error message.
    """
    if not _DDGS_AVAILABLE:
        return _NOT_INSTALLED_MSG

    if not query or not query.strip():
        return "Please provide a valid search query."

    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query.strip(), max_results=max_results))

        if not results:
            return f"No results found for '{query.strip()}'."

        formatted_items = []
        for i, item in enumerate(results, start=1):
            title = (item.get("title") or "Untitled").strip()
            snippet = (
                item.get("body")
                or item.get("snippet")
                or item.get("text")
                or ""
            ).strip()
            formatted_items.append(f"{i}. {title}\n   {snippet}")

        return "\n".join(formatted_items)

    except Exception as e:
        pass

    # Fallback: use DuckDuckGo lite HTML
    try:
        resp = requests.get(
            "https://lite.duckduckgo.com/lite/",
            params={"q": query.strip()},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8
        )
        # Extract snippets from the HTML
        snippets = re.findall(r'<td[^>]*class="result-snippet"[^>]*>(.*?)</td>', resp.text, re.DOTALL)
        titles = re.findall(r'<a[^>]*rel="nofollow"[^>]*>(.*?)</a>', resp.text, re.DOTALL)
        if snippets:
            items = []
            for i, (t, s) in enumerate(zip(titles[:max_results], snippets[:max_results]), 1):
                t_clean = re.sub(r'<[^>]+>', '', t).strip()
                s_clean = re.sub(r'<[^>]+>', '', s).strip()
                items.append(f"{i}. {t_clean}\n   {s_clean}")
            return "\n".join(items)
    except Exception:
        pass

    return f"No results found for '{query.strip()}'."


def summarize_url(url: str) -> str:
    """
    Fetch a webpage's text content using requests and convert/strip HTML to clean text.
    Returns the first 2000 characters of clean text for LLM summarization.

    Args:
        url: The web URL to fetch and extract text from.

    Returns:
        The first 2000 characters of extracted clean text, or an error message.
    """
    if not url or not url.strip():
        return "Invalid URL provided."

    target_url = url.strip()
    if not target_url.startswith(("http://", "https://")):
        target_url = f"https://{target_url}"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    try:
        response = requests.get(target_url, headers=headers, timeout=10)
        response.raise_for_status()

        # Handle content decoding
        response.encoding = response.apparent_encoding or response.encoding

        html_content = response.text

        if _HTML2TEXT_AVAILABLE and html2text is not None:
            converter = html2text.HTML2Text()
            converter.ignore_links = True
            converter.ignore_images = True
            converter.ignore_emphasis = True
            converter.body_width = 0
            raw_text = converter.handle(html_content)
        else:
            # Fallback regex-based HTML tag stripper
            # Remove scripts, styles, metadata, and structural navigation blocks
            cleaned = re.sub(
                r"<(script|style|noscript|header|footer|nav|svg|aside)[^>]*>.*?</\1>",
                "",
                html_content,
                flags=re.DOTALL | re.IGNORECASE,
            )
            # Remove comments
            cleaned = re.sub(r"<!--.*?-->", "", cleaned, flags=re.DOTALL)
            # Remove remaining tags
            cleaned = re.sub(r"<[^>]+>", " ", cleaned)
            # Unescape HTML entities
            raw_text = html.unescape(cleaned)

        # Normalize whitespace while preserving paragraphs
        lines = [line.strip() for line in raw_text.splitlines()]
        paragraphs = [re.sub(r"[ \t]+", " ", line) for line in lines if line]
        clean_text = "\n".join(paragraphs).strip()

        if not clean_text:
            return "No readable content could be extracted from the webpage."

        # Return first 5000 characters (2000 was too short for blog posts/news)
        return clean_text[:5000].strip()

    except requests.exceptions.Timeout:
        return f"Request timed out while trying to reach {target_url}."
    except requests.exceptions.ConnectionError:
        return f"Failed to connect to {target_url}."
    except requests.exceptions.HTTPError as e:
        return f"HTTP error {e.response.status_code if e.response is not None else ''} fetching {target_url}."
    except requests.exceptions.RequestException as e:
        return f"Network error fetching webpage: {str(e)}"
    except Exception as e:
        return f"Error extracting content from webpage: {str(e)}"
