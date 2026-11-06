"""Bilibili read-side MCP server — Pluto motif exploration.

Exposes the public web API as tools: search, video info, comments, and
subtitle download.  Optional SESSDATA cookie via env unlocks logged-in
reads (1080p subtitles, private-space visibility); all writes are
intentionally out of scope.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("tsagent-bilibili")

_API = "https://api.bilibili.com"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

_buvid3: str | None = None


def _fingerprint() -> str:
    """Fetch the buvid3 token bilibili's WAF expects on public reads."""
    global _buvid3
    if _buvid3 is None:
        req = urllib.request.Request(
            f"{_API}/x/frontend/finger/spi",
            headers={"User-Agent": _UA, "Referer": "https://www.bilibili.com"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            spi = json.loads(resp.read().decode("utf-8"))
        _buvid3 = str((spi.get("data") or {}).get("b_3", ""))
    return _buvid3


def _headers() -> dict[str, str]:
    headers = {
        "User-Agent": _UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://search.bilibili.com",
        "Origin": "https://search.bilibili.com",
    }
    cookies = [f"buvid3={_fingerprint()}"]
    sessdata = os.environ.get("BILI_SESSDATA", "").strip()
    if sessdata:
        cookies.append(f"SESSDATA={sessdata}")
    headers["Cookie"] = "; ".join(cookies)
    return headers


def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{_API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("code") not in (0, None):
        raise RuntimeError(
            f"bilibili api error {payload.get('code')}: {payload.get('message')}"
        )
    return payload.get("data") or {}


def _resolve_bvid(value: str) -> str:
    """Accept a bvid, av号, or a video URL and return the bvid."""
    value = value.strip()
    if "bilibili.com/video/" in value:
        value = value.split("/video/")[-1].split("/")[0].split("?")[0]
    if value.lower().startswith("av") and value[2:].isdigit():
        data = _get("/x/web-interface/view", {"aid": value[2:]})
        return str(data.get("bvid", ""))
    return value


@mcp.tool()
def bili_search(keyword: str, page: int = 1) -> str:
    """Search bilibili videos by keyword. Returns bvid/title/up/play/duration
    for the top results."""
    data = _get(
        "/x/web-interface/search/type",
        {"search_type": "video", "keyword": keyword, "page": page},
    )
    results = []
    for item in (data.get("result") or [])[:15]:
        results.append(
            {
                "bvid": item.get("bvid"),
                "title": str(item.get("title", "")).replace("<em class=\"keyword\">", "").replace("</em>", ""),
                "author": item.get("author"),
                "mid": item.get("mid"),
                "play": item.get("play"),
                "danmaku": item.get("danmaku"),
                "duration": item.get("duration"),
                "pubdate": item.get("pubdate"),
            }
        )
    return json.dumps(results, ensure_ascii=False, indent=1)


@mcp.tool()
def bili_video_info(bvid: str) -> str:
    """Get a video's metadata: title, owner, stats, description, cid (needed
    for subtitle/comments)."""
    data = _get("/x/web-interface/view", {"bvid": _resolve_bvid(bvid)})
    keep = {
        "bvid": data.get("bvid"),
        "aid": data.get("aid"),
        "cid": data.get("cid"),
        "title": data.get("title"),
        "desc": data.get("desc"),
        "owner": (data.get("owner") or {}).get("name"),
        "mid": (data.get("owner") or {}).get("mid"),
        "pubdate": data.get("pubdate"),
        "duration": data.get("duration"),
        "stat": data.get("stat"),
        "tname": data.get("tname"),
    }
    return json.dumps(keep, ensure_ascii=False, indent=1)


@mcp.tool()
def bili_comments(bvid: str, page: int = 1) -> str:
    """Fetch top-level comments (热评) for a video."""
    data = _get("/x/web-interface/view", {"bvid": _resolve_bvid(bvid)})
    aid = data.get("aid")
    replies = _get(
        "/x/v2/reply",
        {"type": 1, "oid": aid, "pn": page, "ps": 10, "sort": 1},
    )
    comments = []
    for r in (replies.get("replies") or [])[:10]:
        member = r.get("member") or {}
        comments.append(
            {
                "author": member.get("uname"),
                "like": r.get("like"),
                "text": ((r.get("content") or {}).get("message", ""))[:500],
            }
        )
    return json.dumps(comments, ensure_ascii=False, indent=1)


@mcp.tool()
def bili_subtitle(bvid: str) -> str:
    """Download the video's first available subtitle track and return the
    transcript text.  Falls back to reporting unavailable tracks honestly."""
    data = _get("/x/web-interface/view", {"bvid": _resolve_bvid(bvid)})
    cid = data.get("cid")
    player = _get("/x/player/v2", {"bvid": data.get("bvid"), "cid": cid})
    subs = ((player.get("subtitle") or {}).get("subtitles")) or []
    if not subs:
        return "该视频没有可用字幕轨（UP主未上传或未生成 AI 字幕）。"
    track = subs[0]
    url = track.get("subtitle_url", "")
    if url.startswith("//"):
        url = "https:" + url
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    lines = [item.get("content", "") for item in (body.get("body") or [])]
    text = "\n".join(line for line in lines if line.strip())
    return f"[{track.get('lan_doc') or track.get('lan')}] {text[:12000]}"


if __name__ == "__main__":
    mcp.run()
