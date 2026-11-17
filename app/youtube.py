"""유튜브 데이터: 영상 정보 · 최신 영상 검색 · 뜨기 직전 영상 · 인기 댓글.

YOUTUBE_API_KEY가 있으면 YouTube Data API v3(정확·빠름), 없으면 yt-dlp로 대체한다 (D-02).
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone

from . import store
from .config import Config

ISO_DUR = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


class YouTubeError(Exception):
    pass


def _iso_duration(s: str | None) -> int:
    m = ISO_DUR.fullmatch(s or "")
    if not m:
        return 0
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


def _hours_since(published_at: str) -> float:
    dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    return max((datetime.now(timezone.utc) - dt).total_seconds() / 3600, 0.25)


def _api(cfg: Config):
    from googleapiclient.discovery import build

    return build("youtube", "v3", developerKey=cfg.youtube_api_key, cache_discovery=False)


# ── 영상 정보 ─────────────────────────────────────────────────────────
def _meta_from_api_item(it: dict) -> dict:
    sn, st, cd = it["snippet"], it.get("statistics", {}), it.get("contentDetails", {})
    thumbs = sn.get("thumbnails", {})
    thumb = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url")
    views = int(st.get("viewCount", 0))
    return {
        "video_id": it["id"],
        "title": sn.get("title"),
        "channel_title": sn.get("channelTitle"),
        "channel_id": sn.get("channelId"),
        "published_at": sn.get("publishedAt"),
        "thumbnail_url": thumb,
        "duration_sec": _iso_duration(cd.get("duration")),
        "view_count": views,
        # commentCount가 없으면 댓글이 꺼진 영상
        "comment_count": int(st["commentCount"]) if "commentCount" in st else None,
        "description": sn.get("description", ""),
        "live": sn.get("liveBroadcastContent", "none") != "none",
        "was_live": bool((it.get("liveStreamingDetails") or {}).get("actualStartTime")),
        "category_id": sn.get("categoryId"),
        "views_per_hour": round(views / _hours_since(sn["publishedAt"]), 1) if sn.get("publishedAt") else None,
    }


def _meta_from_ytdlp(info: dict) -> dict:
    ts = info.get("timestamp")
    published = (
        datetime.fromtimestamp(ts, timezone.utc).isoformat()
        if ts
        else (datetime.strptime(info["upload_date"], "%Y%m%d").replace(tzinfo=timezone.utc).isoformat()
              if info.get("upload_date") else None)
    )
    views = info.get("view_count") or 0
    return {
        "video_id": info["id"],
        "title": info.get("title"),
        "channel_title": info.get("channel") or info.get("uploader"),
        "channel_id": info.get("channel_id"),
        "published_at": published,
        "thumbnail_url": info.get("thumbnail") or f"https://i.ytimg.com/vi/{info['id']}/hqdefault.jpg",
        "duration_sec": info.get("duration") or 0,
        "view_count": views,
        "comment_count": info.get("comment_count"),
        "description": info.get("description", ""),
        "live": bool(info.get("is_live") or info.get("live_status") in ("is_upcoming", "is_live")),
        "views_per_hour": round(views / _hours_since(published), 1) if published else None,
    }


def _ytdlp_info(video_id: str, comments: int = 0) -> dict:
    import yt_dlp

    opts = {"quiet": True, "skip_download": True, "no_warnings": True}
    if comments:
        opts["getcomments"] = True
        opts["extractor_args"] = {"youtube": {"comment_sort": ["top"], "max_comments": [str(comments), "all", "0"]}}
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)


async def get_videos(cfg: Config, video_ids: list[str]) -> list[dict]:
    if not video_ids:
        return []
    if cfg.youtube_api_key:
        def run():
            items = []
            for i in range(0, len(video_ids), 50):
                r = _api(cfg).videos().list(
                    part="snippet,statistics,contentDetails,liveStreamingDetails", id=",".join(video_ids[i : i + 50])
                ).execute()
                items += r.get("items", [])
            return [_meta_from_api_item(it) for it in items]
        return await asyncio.to_thread(run)

    async def one(vid: str):
        try:
            return _meta_from_ytdlp(await asyncio.to_thread(_ytdlp_info, vid))
        except Exception:
            return None
    return [m for m in await asyncio.gather(*(one(v) for v in video_ids)) if m]


async def get_video(cfg: Config, video_id: str) -> dict:
    metas = await get_videos(cfg, [video_id])
    if not metas:
        raise YouTubeError("영상 정보를 가져오지 못했어요. 주소를 확인해 주세요.")
    return metas[0]


# ── 인기 댓글 ─────────────────────────────────────────────────────────
async def get_top_comments(cfg: Config, video_id: str, n: int = 20) -> list[dict]:
    if cfg.youtube_api_key:
        def run():
            try:
                r = _api(cfg).commentThreads().list(
                    part="snippet", videoId=video_id, order="relevance", maxResults=min(n, 100),
                    textFormat="plainText",
                ).execute()
            except Exception:
                return []
            out = []
            for it in r.get("items", []):
                c = it["snippet"]["topLevelComment"]["snippet"]
                out.append({"text": c.get("textDisplay", ""), "likes": c.get("likeCount", 0),
                            "author": c.get("authorDisplayName", "")})
            return out
        return await asyncio.to_thread(run)

    def run_ytdlp():
        try:
            info = _ytdlp_info(video_id, comments=n)
        except Exception:
            return []
        cs = [c for c in (info.get("comments") or []) if c.get("parent") in (None, "root")]
        return [{"text": c.get("text", ""), "likes": c.get("like_count") or 0, "author": c.get("author", "")}
                for c in cs[:n]]
    return await asyncio.to_thread(run_ytdlp)


# ── 목록 필터 ─────────────────────────────────────────────────────────
SHORTS_MAX_SEC = 180
LONG_MAX_SEC = 60 * 60  # 1시간 넘는 몰아보기·오디오 사연은 장면을 짚기 어려워 제외
# 음악·스포츠·게임·뉴스·과학기술·자동차 카테고리는 연애 고민 영상이 아님
EXCLUDED_CATEGORIES = {"10", "17", "20", "25", "28", "2"}


DEFAULT_EXCLUDE_WORDS = [
    # 음악
    "전곡듣기", "플레이리스트", "playlist", "Playlist", "[MV]", "OST", "발라드", "노래", "가사",
    # 주식·뉴스
    "상장", "공시", "주가", "속보",
    # 운세 경쟁 채널 (운명원 홍보가 경쟁사 광고처럼 보임)
    "타로", "사주", "운세", "점사", "신점", "보살",
]


def excluded_by_title(m: dict, words: list[str]) -> bool:
    text = f"{m.get('title') or ''} {m.get('channel_title') or ''}"
    return any(w in text for w in words)


def matches_keywords(m: dict, keywords: list[str]) -> bool:
    """채널명이 아니라 제목·설명에 키워드가 있어야 연애 영상으로 본다 (예: '썸타임즈' 야구 채널 제외)."""
    title, desc, ch = m.get("title") or "", m.get("description") or "", m.get("channel_title") or ""
    if any(k in title for k in keywords):
        return True
    # 설명에만 있으면, 채널 이름에 들어간 키워드(예: 썸타임즈의 '썸')는 근거로 치지 않음
    return any(k in desc and k not in ch for k in keywords)
def is_postable(m: dict) -> tuple[bool, str]:
    """쇼츠·라이브·댓글 꺼짐 제외 (D-07)."""
    if m.get("live") or m.get("was_live"):
        return False, "라이브 방송"
    if m.get("category_id") in EXCLUDED_CATEGORIES:
        return False, "연애와 무관한 카테고리"
    if (m.get("duration_sec") or 0) > LONG_MAX_SEC:
        return False, "1시간 넘는 영상"
    # 쇼츠는 최대 3분까지 가능 → 3분 이하는 제외 (쇼츠는 댓글 UI가 달라 게시 실패율이 높음)
    if (m.get("duration_sec") or 0) <= SHORTS_MAX_SEC:
        return False, "쇼츠"
    if m.get("comment_count") is None and m.get("_source") == "api":
        return False, "댓글 꺼짐"
    return True, ""


def _save(metas: list[dict], source: str) -> None:
    conn = store.connect()
    now = store.now_kst().isoformat()
    with store._lock:
        for m in metas:
            conn.execute(
                """INSERT INTO videos (video_id, title, channel_title, channel_id, published_at, thumbnail_url,
                     duration_sec, view_count, comment_count, description, source, views_per_hour, fetched_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(video_id) DO UPDATE SET title=excluded.title, view_count=excluded.view_count,
                     comment_count=excluded.comment_count, views_per_hour=excluded.views_per_hour,
                     fetched_at=excluded.fetched_at""",
                (m["video_id"], m["title"], m["channel_title"], m["channel_id"], m["published_at"],
                 m["thumbnail_url"], m["duration_sec"], m["view_count"], m["comment_count"],
                 m["description"], source, m.get("views_per_hour"), now),
            )


def _decorate(metas: list[dict], source: str) -> list[dict]:
    done = store.commented_video_ids()
    out = []
    for m in metas:
        m["_source"] = "api" if source != "ytdlp" else "ytdlp"
        ok, why = is_postable(m)
        if not ok:
            continue
        m["commented"] = m["video_id"] in done
        out.append(m)
    return out


# ── 검색 ─────────────────────────────────────────────────────────────
async def _search_ids_api(
    cfg: Config, query: str, published_after: datetime, limit: int = 50, durations: tuple[str, ...] = ("medium", "long")
) -> list[str]:
    """최신순 검색. 최신 결과는 대부분 쇼츠라서 4~20분(medium)·20분 이상(long) 영상만 따로 검색해 합친다.
    할당량: search.list 1회 = 100 units → 불러오기 1번에 200 units."""
    def run(duration: str) -> list[str]:
        r = _api(cfg).search().list(
            part="id", q=query, type="video", order="date", regionCode="KR", relevanceLanguage="ko",
            publishedAfter=published_after.strftime("%Y-%m-%dT%H:%M:%SZ"), maxResults=min(limit, 50),
            videoDuration=duration,
        ).execute()
        return [it["id"]["videoId"] for it in r.get("items", [])]

    results = await asyncio.gather(*(asyncio.to_thread(run, d) for d in durations))
    seen: list[str] = []
    for ids in results:
        seen += [i for i in ids if i not in seen]
    return seen


async def _search_ids_ytdlp(query: str, limit: int) -> list[str]:
    import yt_dlp

    from urllib.parse import quote

    # 업로드 날짜순 검색 결과 페이지
    url = f"https://www.youtube.com/results?search_query={quote(query)}&sp=CAI%3D"

    def run():
        opts = {"quiet": True, "extract_flat": True, "no_warnings": True, "playlistend": limit}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return [e["id"] for e in info.get("entries", []) if e.get("id") and "shorts" not in (e.get("url") or "")][:limit]
    return await asyncio.to_thread(run)


async def search_latest(cfg: Config) -> list[dict]:
    s = cfg.raw.get("search", {})
    keywords = s.get("keywords", ["연애"])
    days, limit = s.get("days", 7), s.get("max_results", 25)
    query = "|".join(keywords)  # YouTube 검색 OR 문법
    if cfg.youtube_api_key:
        ids = await _search_ids_api(cfg, query, datetime.now(timezone.utc) - timedelta(days=days))
        exclude = s.get("exclude_title_words", DEFAULT_EXCLUDE_WORDS)
        metas = [m for m in await get_videos(cfg, ids)
                 if matches_keywords(m, keywords) and not excluded_by_title(m, exclude)]
        src = "search"
    else:
        ids = await _search_ids_ytdlp(" ".join(keywords[:2]), min(limit, 12))
        metas = await get_videos(cfg, ids)
        src = "ytdlp"
    metas.sort(key=lambda m: m.get("published_at") or "", reverse=True)
    out = _decorate(metas, src)[:limit]
    _save(out, "search")
    return out


async def find_rising(cfg: Config) -> list[dict]:
    """'연애' 키워드, 최근 N시간, 조회수 1,000~5,000 & 시간당 150 이상 → 뜨기 직전 (초안까지만)."""
    r = cfg.raw.get("rising", {})
    kw, hours = r.get("keyword", "연애"), r.get("hours", 48)
    lo, hi, vph = r.get("min_views", 1000), r.get("max_views", 5000), r.get("min_views_per_hour", 150)
    if cfg.youtube_api_key:
        ids = await _search_ids_api(cfg, kw, datetime.now(timezone.utc) - timedelta(hours=hours))
        src = "rising"
    else:
        ids = await _search_ids_ytdlp(kw, 20)
        src = "ytdlp"
    exclude = cfg.raw.get("search", {}).get("exclude_title_words", DEFAULT_EXCLUDE_WORDS)
    metas = [m for m in await get_videos(cfg, ids) if not excluded_by_title(m, exclude)]
    hits = [
        m for m in metas
        if m.get("published_at") and _hours_since(m["published_at"]) <= hours
        and lo <= (m.get("view_count") or 0) <= hi and (m.get("views_per_hour") or 0) >= vph
    ]
    hits.sort(key=lambda m: m.get("views_per_hour") or 0, reverse=True)
    out = _decorate(hits, src)
    _save(out, "rising")
    return out


async def add_by_link(cfg: Config, video_id: str) -> dict:
    m = await get_video(cfg, video_id)
    m["_source"] = "api" if cfg.youtube_api_key else "ytdlp"
    ok, why = is_postable(m)
    m["postable"], m["not_postable_reason"] = ok, why
    m["commented"] = video_id in store.commented_video_ids()
    _save([m], "link")
    return m
