"""예약된 attempt를 실행하고 결과를 DB에 기록. (M4에서 FastAPI가 그대로 재사용)"""
from __future__ import annotations

import asyncio
import inspect
import re
from typing import Any, Callable

from .. import store
from ..config import Config
from .runner import Poster, PostResult

# 동시에 브라우저 1개만 (R5)
POST_LOCK = asyncio.Lock()

VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/|live/)([A-Za-z0-9_-]{11})")


def parse_video_id(url_or_id: str) -> str:
    s = url_or_id.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s
    m = VIDEO_ID_RE.search(s)
    if not m:
        raise ValueError(f"유튜브 영상 주소를 알아볼 수 없어요: {s}")
    return m.group(1)


async def execute_attempt(cfg: Config, attempt_id: int, emit: Callable[[dict], Any] | None = None) -> PostResult:
    att = store.get_attempt(attempt_id)
    if not att:
        raise ValueError(f"attempt {attempt_id} 없음")
    profile = cfg.profile(att["profile"])

    last_stage = {"v": None}

    async def on_event(ev: dict) -> None:
        # 단계가 바뀔 때만 DB 갱신 (타이핑 글자마다 쓰지 않음)
        if ev["stage"] != last_stage["v"]:
            last_stage["v"] = ev["stage"]
            if ev["stage"] not in ("failed", "unverified"):
                store.update_attempt(attempt_id, stage=ev["stage"])
        if emit:
            r = emit(ev)
            if inspect.isawaitable(r):
                await r

    async with POST_LOCK:
        poster = Poster(cfg, profile, att["video_id"], att["comment_text"], attempt_id=attempt_id, emit=on_event)
        result = await poster.run()

    store.update_attempt(
        attempt_id,
        status=result.status,
        stage=result.stage,
        error_code=result.error_code,
        error_message=result.error_message,
        screenshot_path=result.screenshot_path,
        comment_url=result.comment_url,
        finished_at=store.now_kst().isoformat(),
    )
    return result
