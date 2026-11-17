"""SQLite 저장소. 운영 규칙의 최후 방어선(유니크 인덱스)도 여기 있다."""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import DATA_DIR, DB_PATH

KST = ZoneInfo("Asia/Seoul")

# 상한·중복 계산에 포함되는 상태: 실제로 게시됐을 수 있는 것 전부 (D-08)
ACTIVE_STATUSES = ("posting", "posted", "unverified")

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS videos (
  video_id TEXT PRIMARY KEY, title TEXT, channel_title TEXT, channel_id TEXT,
  published_at TEXT, thumbnail_url TEXT, duration_sec INTEGER, view_count INTEGER,
  comment_count INTEGER, description TEXT, source TEXT, views_per_hour REAL, fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS analyses (
  video_id TEXT PRIMARY KEY, transcript_source TEXT, concern_one_liner TEXT,
  scenes_json TEXT, top_comments_json TEXT, audience_json TEXT, model TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS drafts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, video_id TEXT, batch_id TEXT, text TEXT,
  persona TEXT, strategy TEXT, anchor_scene_idx INTEGER, checks_json TEXT, judge_json TEXT,
  selected INTEGER DEFAULT 0, edited INTEGER DEFAULT 0, created_at TEXT
);
CREATE TABLE IF NOT EXISTS attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  video_id TEXT NOT NULL, video_title TEXT, profile TEXT NOT NULL, draft_id INTEGER,
  comment_text TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('posting','posted','failed','unverified')),
  stage TEXT, error_code TEXT, error_message TEXT, screenshot_path TEXT, comment_url TEXT,
  started_at TEXT NOT NULL, finished_at TEXT, kst_date TEXT NOT NULL
);
-- 영상당 댓글 1개 (계정 무관). UI·서비스 검사를 우회해도 DB가 막는다.
CREATE UNIQUE INDEX IF NOT EXISTS one_comment_per_video
  ON attempts(video_id) WHERE status IN {ACTIVE_STATUSES};
CREATE INDEX IF NOT EXISTS attempts_by_date ON attempts(kst_date, status);
"""

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def now_kst() -> datetime:
    return datetime.now(KST)


def connect() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.executescript(SCHEMA)
        return _conn


def update_attempt(attempt_id: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with _lock:
        connect().execute(f"UPDATE attempts SET {cols} WHERE id=?", (*fields.values(), attempt_id))


def get_attempt(attempt_id: int) -> dict | None:
    with _lock:
        row = connect().execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
    return dict(row) if row else None


def list_attempts(limit: int = 200) -> list[dict]:
    """기록: 시간순(최신 먼저)."""
    with _lock:
        rows = connect().execute(
            "SELECT a.id, a.video_id, COALESCE(a.video_title, v.title) AS video_title, a.profile, a.draft_id, "
            "a.comment_text, a.status, a.stage, a.error_code, a.error_message, a.screenshot_path, a.comment_url, "
            "a.started_at, a.finished_at, a.kst_date FROM attempts a "
            "LEFT JOIN videos v ON v.video_id = a.video_id ORDER BY a.started_at DESC, a.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def recover_interrupted() -> int:
    """프로그램이 게시 도중 종료돼 'posting'으로 남은 기록 → 'unverified' (게시됐을 수 있으므로 중복 방지 유지)."""
    with _lock:
        cur = connect().execute(
            "UPDATE attempts SET status='unverified', error_code='INTERRUPTED', "
            "error_message='게시 도중 프로그램이 종료됐어요. 영상에서 직접 확인해 주세요.', finished_at=? "
            "WHERE status='posting'",
            (now_kst().isoformat(),),
        )
    return cur.rowcount


def count_today(conn: sqlite3.Connection, kst_date: str, profile: str | None = None) -> int:
    q = f"SELECT COUNT(*) FROM attempts WHERE kst_date=? AND status IN {ACTIVE_STATUSES}"
    args: list = [kst_date]
    if profile:
        q += " AND profile=?"
        args.append(profile)
    return conn.execute(q, args).fetchone()[0]


def video_has_comment(conn: sqlite3.Connection, video_id: str) -> dict | None:
    row = conn.execute(
        f"SELECT * FROM attempts WHERE video_id=? AND status IN {ACTIVE_STATUSES}", (video_id,)
    ).fetchone()
    return dict(row) if row else None


def commented_video_ids() -> set[str]:
    with _lock:
        rows = connect().execute(
            f"SELECT video_id FROM attempts WHERE status IN {ACTIVE_STATUSES}"
        ).fetchall()
    return {r[0] for r in rows}
