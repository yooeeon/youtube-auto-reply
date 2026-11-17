"""운영 규칙 — 코드에서 강제.

게시 슬롯 예약은 하나의 IMMEDIATE 트랜잭션 안에서:
  영상당 1개 → 하루 상한 → 최소 간격 → 프로필 배정 → attempts INSERT(status='posting')
마지막 방어선은 store의 부분 유니크 인덱스(one_comment_per_video).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from . import store
from .config import Config


class RuleError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


@dataclass
class Reservation:
    attempt_id: int
    profile: str


def _seconds_since_last_post(conn: sqlite3.Connection, now: datetime) -> float | None:
    row = conn.execute(
        "SELECT MAX(started_at) FROM attempts WHERE status IN ('posting','posted','unverified')"
    ).fetchone()
    if not row or not row[0]:
        return None
    return (now - datetime.fromisoformat(row[0])).total_seconds()


def pick_profile(conn: sqlite3.Connection, cfg: Config, kst_date: str) -> str:
    """자동 배정: 폴더가 있는 프로필 중 오늘 게시가 가장 적은 것, 동률이면 설정 순서."""
    ready = [p for p in cfg.profiles if p.exists]
    if not ready:
        raise RuleError(
            "NO_PROFILE", "사용할 수 있는 프로필이 없어요. 먼저 [로그인 하기]로 프로필을 만들어 주세요.", 400
        )
    return min(ready, key=lambda p: (store.count_today(conn, kst_date, p.name), cfg.profiles.index(p))).name


def check_status(cfg: Config, video_id: str | None = None) -> dict:
    """UI에서 버튼 활성/비활성 이유를 보여 주기 위한 읽기 전용 점검."""
    conn = store.connect()
    now = store.now_kst()
    kst_date = now.date().isoformat()
    used = store.count_today(conn, kst_date)
    since = _seconds_since_last_post(conn, now)
    cooldown = 0 if since is None else max(0, int(cfg.limits.min_interval_sec - since))
    blocked = None
    if video_id and (prev := store.video_has_comment(conn, video_id)):
        blocked = f"이미 댓글을 단 영상이에요 ({prev['profile']}, {prev['started_at'][:16].replace('T', ' ')})."
    elif used >= cfg.limits.daily_max_comments:
        blocked = f"오늘 상한 {cfg.limits.daily_max_comments}개를 모두 사용했어요. 내일 0시에 초기화돼요."
    elif cooldown:
        blocked = f"연속 게시를 막기 위해 {cooldown}초 후에 달 수 있어요."
    return {
        "today_used": used,
        "daily_max": cfg.limits.daily_max_comments,
        "cooldown_sec": cooldown,
        "blocked_reason": blocked,
    }


def reserve(
    cfg: Config,
    *,
    video_id: str,
    comment_text: str,
    profile: str | None = None,
    video_title: str | None = None,
    draft_id: int | None = None,
) -> Reservation:
    conn = store.connect()
    now = store.now_kst()
    kst_date = now.date().isoformat()
    with store._lock:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if prev := store.video_has_comment(conn, video_id):
                raise RuleError(
                    "ALREADY_COMMENTED",
                    f"이미 댓글을 단 영상이에요 ({prev['profile']}). 영상당 댓글은 1개만 달 수 있어요.",
                )
            used = store.count_today(conn, kst_date)
            if used >= cfg.limits.daily_max_comments:
                raise RuleError(
                    "DAILY_LIMIT",
                    f"오늘 상한 {cfg.limits.daily_max_comments}개를 모두 사용했어요. 내일 0시에 초기화돼요.",
                    429,
                )
            since = _seconds_since_last_post(conn, now)
            if since is not None and since < cfg.limits.min_interval_sec:
                wait = int(cfg.limits.min_interval_sec - since) + 1
                raise RuleError("COOLDOWN", f"연속 게시를 막기 위해 {wait}초 후에 달 수 있어요.", 429)

            if profile:
                p = cfg.profile(profile)
                if not p.exists:
                    raise RuleError("PROFILE_MISSING", f"{profile} 프로필 폴더가 없어요. 먼저 로그인해 주세요.", 400)
            else:
                profile = pick_profile(conn, cfg, kst_date)

            cur = conn.execute(
                """INSERT INTO attempts (video_id, video_title, profile, draft_id, comment_text,
                       status, stage, started_at, kst_date)
                   VALUES (?,?,?,?,?, 'posting', 'queued', ?, ?)""",
                (video_id, video_title, profile, draft_id, comment_text, now.isoformat(), kst_date),
            )
            conn.execute("COMMIT")
            return Reservation(attempt_id=cur.lastrowid, profile=profile)
        except sqlite3.IntegrityError:
            conn.execute("ROLLBACK")
            raise RuleError("ALREADY_COMMENTED", "이미 댓글을 단 영상이에요. 영상당 댓글은 1개만 달 수 있어요.")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
