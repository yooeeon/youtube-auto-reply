"""자막 수집: 한국어 수동 자막 → 한국어 자동 자막 → yt-dlp 자막 → 없음(제목·설명으로 대체).

결과는 타임스탬프를 유지한 줄 목록. 분석 단계가 '근거 장면(시각+대사)'을 뽑는 데 쓴다.
"""
from __future__ import annotations

import asyncio
import re
import urllib.request
from dataclasses import dataclass

MAX_CHARS = 14_000  # 분석 프롬프트에 넣을 자막 최대 길이


@dataclass
class Transcript:
    source: str  # manual_ko | auto_ko | yt_dlp | none
    lines: list[tuple[float, str]]

    @property
    def available(self) -> bool:
        return bool(self.lines)

    def full_text(self) -> str:
        return " ".join(t for _, t in self.lines)

    def to_prompt(self, max_chars: int = MAX_CHARS) -> str:
        rendered = [f"[{fmt_ts(s)}] {t}" for s, t in self.lines]
        text = "\n".join(rendered)
        if len(text) <= max_chars:
            return text
        # 길면 고르게 표본 추출 (타임스탬프 유지)
        step = len(text) / max_chars
        keep = rendered[:: max(1, round(step))]
        return "\n".join(keep)[:max_chars]


def fmt_ts(sec: float) -> str:
    sec = int(sec)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&gt;", ">").replace("&lt;", "<").replace("&amp;", "&")
    text = re.sub(r"^>>\s*", "", text.strip())
    text = re.sub(r"\[(음악|박수|웃음)\]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _dedupe(lines: list[tuple[float, str]]) -> list[tuple[float, str]]:
    out: list[tuple[float, str]] = []
    for s, t in lines:
        if t and (not out or out[-1][1] != t):
            out.append((s, t))
    return out


def _fetch_with_ytt(video_id: str) -> Transcript | None:
    from youtube_transcript_api import YouTubeTranscriptApi

    try:
        tl = YouTubeTranscriptApi().list(video_id)
    except Exception:
        return None
    for finder, source in (
        (tl.find_manually_created_transcript, "manual_ko"),
        (tl.find_generated_transcript, "auto_ko"),
    ):
        try:
            fetched = finder(["ko"]).fetch()
            lines = _dedupe([(sn.start, _clean(sn.text)) for sn in fetched.snippets])
            if lines:
                return Transcript(source, lines)
        except Exception:
            continue
    return None


def _parse_vtt(vtt: str) -> list[tuple[float, str]]:
    lines: list[tuple[float, str]] = []
    cur: float | None = None
    for raw in vtt.splitlines():
        m = re.match(r"(\d+):(\d\d):(\d\d)\.\d+ -->", raw) or re.match(r"(\d\d):(\d\d)\.\d+ -->", raw)
        if m:
            g = [int(x) for x in m.groups()]
            cur = g[0] * 3600 + g[1] * 60 + g[2] if len(g) == 3 else g[0] * 60 + g[1]
            continue
        if cur is None or not raw.strip() or "-->" in raw:
            continue
        lines.append((cur, _clean(raw)))
    return _dedupe(lines)


def _fetch_with_ytdlp(video_id: str) -> Transcript | None:
    import yt_dlp

    try:
        with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True, "no_warnings": True}) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    except Exception:
        return None
    for key in ("subtitles", "automatic_captions"):
        tracks = (info.get(key) or {})
        for lang in ("ko", "ko-orig", "ko-KR"):
            for fmt in tracks.get(lang, []):
                if fmt.get("ext") == "vtt":
                    try:
                        vtt = urllib.request.urlopen(fmt["url"], timeout=20).read().decode("utf-8", "ignore")
                    except Exception:
                        continue
                    lines = _parse_vtt(vtt)
                    if lines:
                        return Transcript("yt_dlp", lines)
    return None


async def fetch_transcript(video_id: str) -> Transcript:
    for fn in (_fetch_with_ytt, _fetch_with_ytdlp):
        t = await asyncio.to_thread(fn, video_id)
        if t:
            return t
    return Transcript("none", [])
