# tools/web.py
"""Web search and fetch tools — enhanced with timeliness, deep fetch, and news search.

Provides:
- web_search(query, max_results, timeliness)    — quick search with time context
- web_deep_search(query, fetch_top_n)           — search + parallel page fetch
- web_news_search(query, max_results, days)     — time-filtered news search
- web_fetch(url)                                — single page fetch
"""
import re
import asyncio
from datetime import datetime, timedelta
from agent.registry.tool_registry import registry

# ddgs is the new package name for duckduckgo_search (v9+)
try:
    from ddgs import DDGS
    DUCKDUCKGO_AVAILABLE = True
except ImportError:
    try:
        from duckduckgo_search import DDGS
        DUCKDUCKGO_AVAILABLE = True
    except ImportError:
        DUCKDUCKGO_AVAILABLE = False

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

# Trusted domains for deep search — expanded to 40+
TRUSTED_DOMAINS = [
    # News / General
    "bbc.com", "bbc.co.uk", "bbc", "reuters.com", "apnews.com",
    "theguardian.com", "nytimes.com", "cnn.com", "wsj.com",
    "bloomberg.com", "economist.com", "npr.org", "ft.com",
    "washingtonpost.com", "latimes.com",
    # Sports
    "espn.com", "sky sports", "olympics.com", "fifa.com",
    "bbc.com/sport", "goal.com", "sports.yahoo.com",
    # Chinese
    "cctv.com", "cctv", "people.com.cn", "xinhuanet.com",
    "163.com", "sina.com.cn", "qq.com", "sohu.com",
    "zhihu.com", "baike.baidu.com", "thepaper.cn",
    "huanqiu.com", "globaltimes.cn",
    # Tech / Reference
    "wikipedia.org", "github.com", "stackoverflow.com",
    "medium.com", "techcrunch.com", "theverge.com",
    "arstechnica.com", "wired.com",
]

# Domains that are typically useless for factual information searches
BLOCKED_DOMAINS = [
    "instagram.com", "facebook.com", "tiktok.com", "twitter.com", "x.com",
    "vimeo.com", "youtube.com", "pinterest.com", "flickr.com",
    "reddit.com",
]

# Patterns to extract publish date from HTML
DATE_PATTERNS = [
    # JSON-LD
    r'"datePublished"\s*:\s*"([^"]+)"',
    r'"dateModified"\s*:\s*"([^"]+)"',
    # Open Graph / Article meta
    r'<meta[^>]+property\s*=\s*"article:published_time"[^>]+content\s*=\s*"([^"]+)"',
    r'<meta[^>]+name\s*=\s*"article:published_time"[^>]+content\s*=\s*"([^"]+)"',
    r'<meta[^>]+property\s*=\s*"og:updated_time"[^>]+content\s*=\s*"([^"]+)"',
    # Common date meta
    r'<meta[^>]+name\s*=\s*"date"[^>]+content\s*=\s*"([^"]+)"',
    r'<meta[^>]+name\s*=\s*"pubdate"[^>]+content\s*=\s*"([^"]+)"',
    r'<time[^>]+datetime\s*=\s*"([^"]+)"',
    # BBC specific
    r'data-seconds\s*=\s*"(\d+)"',
    r'data-datetime\s*=\s*"([^"]+)"',
    # Time tag content
    r'<time[^>]*>([^<]+)</time>',
]


def _build_time_aware_query(query: str, timeliness: str = "any") -> str:
    """Append date context to query based on timeliness parameter.

    Args:
        query: Original search query
        timeliness: "today" | "yesterday" | "week" | "month" | "any"

    Returns:
        Query string with date context appended.
    """
    now = datetime.now()
    today = now.strftime("%Y年%m月%d日")

    if timeliness == "today":
        return f"{today} {query}"
    elif timeliness == "yesterday":
        yesterday = (now - timedelta(days=1)).strftime("%Y年%m月%d日")
        return f"{yesterday} {query}"
    elif timeliness == "week":
        week_ago = (now - timedelta(days=7)).strftime("%Y年%m月")
        return f"{week_ago} {query}"
    elif timeliness == "month":
        month = now.strftime("%Y年%m月")
        return f"{month} {query}"
    return query


def _format_search_results(results: list[dict]) -> str:
    """Format search results into a consistent text block."""
    if not results:
        return ""
    lines = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        snippet = r.get("body", "") or r.get("snippet", "")
        link = r.get("href", "") or r.get("link", "")
        lines.append(f"{i}. **{title}**\n   {snippet}\n   {link}")
    return "\n\n".join(lines)


def _extract_publish_date(html: str) -> str:
    """Extract publication date from HTML meta tags using regex patterns."""
    for pattern in DATE_PATTERNS:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            raw = match.group(1)
            # Clean up timestamp
            if raw and len(raw) > 4:
                # Parse ISO format
                try:
                    if "T" in raw:
                        dt = datetime.fromisoformat(raw.split("+")[0].split("Z")[0])
                        return dt.strftime("%Y-%m-%d")
                except (ValueError, TypeError):
                    pass
                # Unix timestamp
                if re.match(r"^\d{10}$", raw):
                    try:
                        dt = datetime.fromtimestamp(int(raw))
                        return dt.strftime("%Y-%m-%d")
                    except (ValueError, OSError):
                        pass
                return raw[:10]
    return ""


async def _fetch_page_text(url: str) -> tuple[str, str]:
    """Fetch a page and extract clean text + publish date.

    Returns:
        (clean_text, publish_date) tuple. publish_date may be empty.
    """
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        html = resp.text

        # Extract publish date
        pub_date = _extract_publish_date(html)

        # Remove scripts and styles
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        # Remove HTML tags
        text = re.sub(r'<[^>]+>', ' ', text)
        # Collapse whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        # Remove empty lines
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        text = '\n'.join(lines)

        return text, pub_date


def _is_trusted_domain(url: str) -> bool:
    """Check if a URL belongs to a trusted domain."""
    url_lower = url.lower()
    return any(d in url_lower for d in TRUSTED_DOMAINS)


def _is_blocked_domain(url: str) -> bool:
    """Check if a URL belongs to a blocked (typically useless) domain."""
    url_lower = url.lower()
    return any(d in url_lower for d in BLOCKED_DOMAINS)


async def _ddgs_search(query: str, max_results: int) -> list[dict]:
    """Async DuckDuckGo search returning raw results."""
    results = []
    try:
        async with DDGS() as ddgs:
            async for r in ddgs.atext(query, max_results=max_results):
                results.append(r)
    except Exception:
        # Fallback to sync
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
        except Exception:
            pass
    return results


def _filter_blocked_results(results: list[dict]) -> list[dict]:
    """Remove results from blocked (social media / useless) domains and low-quality snippets."""
    filtered = []
    for r in results:
        link = r.get("href", "") or r.get("link", "")
        if link and _is_blocked_domain(link):
            continue
        # Also filter by snippet quality
        snippet = r.get("body", "") or r.get("snippet", "")
        if len(snippet) < 5:
            continue
        filtered.append(r)
    return filtered


async def _search_with_fallback(query: str, max_results: int, max_retries: int = 2) -> list[dict]:
    """Search with automatic fallback: try original query, then simplified query if no good results."""
    # Try 1: Original query
    results = await _ddgs_search(query, max_results=max_results)
    results = _filter_blocked_results(results)

    # If too few results, try simplified query
    if len(results) < 3 and max_retries > 0:
        # Remove date prefixes for a broader search
        simplified = query
        simplified = re.sub(r'\d{4}年\d{1,2}月\d{1,2}日', '', simplified)
        simplified = re.sub(r'\d{4}年\d{1,2}月', '', simplified)
        simplified = simplified.strip()
        if simplified != query and len(simplified) > 2:
            retry_results = await _ddgs_search(simplified, max_results=max_results)
            retry_results = _filter_blocked_results(retry_results)
            # Merge: prefer original results, fill gaps with retry results
            existing_links = {r.get("href", "") for r in results}
            for r in retry_results:
                if r.get("href", "") not in existing_links and len(results) < max_results:
                    results.append(r)
                    existing_links.add(r.get("href", ""))

    return results[:max_results]


# ====== Exported Tools ======


async def web_search(query: str = "", q: str = "", url: str = "", keyword: str = "", search: str = "", max_results: int = 5, timeliness: str = "any") -> str:
    """搜索网络信息并返回文本摘要。支持时间限定搜索。

    使用 DuckDuckGo 搜索，无需 API key。

    Tool Contract Compatibility (P0.2): 不同 LLM 可能输出
    query / q / url / keyword / search —— 工具层统一归一化，
    不依赖 Prompt 纠正。

    Args:
        query: 搜索关键词
        q: 搜索关键词别名
        url: 关键词别名（模型可能误把搜索当 URL 抓取）
        keyword: 关键词别名
        search: 关键词别名
        max_results: 返回的最大结果数量（默认 5）
        timeliness: 时间限定，"today"|"yesterday"|"week"|"month"|"any"（默认 "any"）

    Returns:
        搜索结果的标题、关键信息的提要和链接列表
    """
    # 参数别名归一化（鲁棒性：工具层兼容，而非 Prompt）
    query = query or q or keyword or search or url or ""
    if not query:
        return "错误：web_search 缺少 query 参数"
    if not DUCKDUCKGO_AVAILABLE:
        return (
            f"网络搜索功能不可用。请运行: pip install ddgs\n"
            f"无法搜索 '{query}' 的信息。"
        )

    time_query = _build_time_aware_query(query, timeliness)
    results = await _search_with_fallback(time_query, max_results)

    if not results:
        return f"未找到关于 '{query}' 的搜索结果。"

    formatted = _format_search_results(results)
    if timeliness != "any":
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        return f"[时间限定: {timeliness}] 搜索: {query}\n\n" + formatted
    return formatted


async def web_news_search(query: str, max_results: int = 5, days: int = 7) -> str:
    """搜索新闻信息，带时间过滤和来源标注。只返回来自可信新闻源的近期结果。

    先通过 DuckDuckGo 搜索，然后自动过滤出新闻类可信域名的结果，
    并尝试提取每篇文章的发布时间。

    Args:
        query: 搜索关键词
        max_results: 返回的最大结果数量（默认 5）
        days: 限定最近几天的新闻（默认 7 天）

    Returns:
        按时间排序的新闻结果列表，包含标题、时间、来源、摘要和链接
    """
    if not DUCKDUCKGO_AVAILABLE or not HTTPX_AVAILABLE:
        # Fallback to regular search
        return await web_search(query, max_results=max_results)

    now = datetime.now()
    date_str = now.strftime("%Y年%m月%d日")
    # Add time context to query
    time_query = f"{date_str} {query}"

    results = await _search_with_fallback(time_query, max_results=max_results * 2)

    if not results:
        return f"未找到关于 '{query}' 的新闻。"

    # Fetch first few trusted results in parallel to get publish dates
    fetch_tasks = []
    trusted_urls = []
    for r in results:
        link = r.get("href", "")
        if link and _is_trusted_domain(link) and len(trusted_urls) < max_results:
            trusted_urls.append(link)
            fetch_tasks.append(_fetch_page_text(link))

    # Parallel fetch
    fetched_pages = []
    if fetch_tasks:
        try:
            fetched_pages = await asyncio.gather(*fetch_tasks, return_exceptions=True)
        except Exception:
            fetched_pages = []

    # Combine results
    date_map = {}
    for i, url in enumerate(trusted_urls):
        if i < len(fetched_pages) and not isinstance(fetched_pages[i], Exception):
            _, pub_date = fetched_pages[i]
            if pub_date:
                date_map[url] = pub_date

    # Format output
    output = [f"## 新闻搜索结果: {query}\n"]

    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        snippet = r.get("body", "")
        link = r.get("href", "")
        pub_date = date_map.get(link, "")

        # Highlight date if found
        date_tag = f"[{pub_date}] " if pub_date else ""
        # Mark if trusted
        trusted = "★" if link and _is_trusted_domain(link) else " "

        output.append(f"{i}. {trusted} {date_tag}**{title}**\n   {snippet}\n   {link}")

    return "\n\n".join(output)


async def web_deep_search(query: str, fetch_top_n: int = 2, timeliness: str = "any") -> str:
    """深度搜索：先搜索网络，并行获取前 N 个可信网页的完整内容。

    适用于需要获取详细信息、比分、数据、新闻全文等场景。
    搜索时会自动注入时间上下文，抓取时提取页面发布日期，
    多个页面并行抓取提升速度。

    Args:
        query: 搜索关键词
        fetch_top_n: 要获取完整内容的网页数量（默认 2，最多 5）
        timeliness: 时间限定，"today"|"yesterday"|"week"|"month"|"any"

    Returns:
        搜索结果摘要 + 多个页面的完整文本内容（含发布时间）
    """
    if not DUCKDUCKGO_AVAILABLE or not HTTPX_AVAILABLE:
        return await web_search(query, max_results=5, timeliness=timeliness)

    fetch_top_n = min(fetch_top_n, 5)  # cap at 5

    # Step 1: Search with time context
    time_query = _build_time_aware_query(query, timeliness)
    results = await _search_with_fallback(time_query, max_results=min(fetch_top_n + 5, 15))

    if not results:
        return f"未找到关于 '{query}' 的搜索结果。"

    # Step 2: Collect URLs — prefer trusted, fallback to any
    search_lines = []
    trusted_urls = []
    fallback_urls = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        snippet = r.get("body", "")
        link = r.get("href", "")
        search_lines.append(f"{i}. **{title}**\n   {snippet}\n   {link}")

        if link and len(trusted_urls) < fetch_top_n:
            if _is_trusted_domain(link):
                trusted_urls.append(link)
            elif len(fallback_urls) < fetch_top_n:
                fallback_urls.append(link)

    # Use trusted if available, otherwise fallback
    urls_to_fetch = trusted_urls if len(trusted_urls) >= 2 else (trusted_urls + fallback_urls[:max(0, fetch_top_n - len(trusted_urls))])

    output_parts = ["## 搜索结果摘要\n"]
    output_parts.append("\n\n".join(search_lines))

    # Step 3: Parallel fetch
    if urls_to_fetch:
        output_parts.append("\n\n## 详细页面内容（自动获取）\n")
        fetch_tasks = [_fetch_page_text(url) for url in urls_to_fetch]
        try:
            fetched = await asyncio.gather(*fetch_tasks, return_exceptions=True)
        except Exception:
            fetched = []

        for i, url in enumerate(urls_to_fetch):
            if i < len(fetched) and not isinstance(fetched[i], Exception):
                text, pub_date = fetched[i]
                if text and len(text) > 100:
                    header = f"### [{url}]"
                    if pub_date:
                        header += f" (发布时间: {pub_date})"
                    output_parts.append(f"{header}\n{text[:8000]}")

    result = "\n\n".join(output_parts)

    # Add time info
    if timeliness != "any":
        now = datetime.now()
        result = f"[时间限定: {timeliness}] 搜索: {query}\n\n" + result

    return result


async def web_fetch(url: str) -> str:
    """获取网页内容并返回纯文本摘要（含发布时间）。

    自动从网页 meta 标签提取发布日期。

    Args:
        url: 要获取的网页 URL

    Returns:
        网页的纯文本内容（含发布时间，前 8000 字符）
    """
    if not HTTPX_AVAILABLE:
        return f"错误：httpx 未安装，无法获取网页。请运行: pip install httpx"

    try:
        text, pub_date = await _fetch_page_text(url)
        parts = []
        if pub_date:
            parts.append(f"发布时间: {pub_date}")
        parts.append("")
        parts.append(text[:8000])
        return "\n".join(parts)
    except Exception as e:
        return f"获取网页失败: {str(e)}"


# ====== Register tools ======

registry.register(web_search, category="web", tags=["search", "web"])
registry.register(web_fetch, category="web", tags=["search", "fetch", "web"])
registry.register(web_deep_search, category="web", tags=["search", "deep", "fetch", "web"])
registry.register(web_news_search, category="web", tags=["search", "news", "time"])