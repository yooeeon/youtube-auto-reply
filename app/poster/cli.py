"""M1 시험용 CLI: 대시보드 없이 게시 자동화만 단독 실행.

  python -m app.poster.cli --url https://youtu.be/XXXXXXXXXXX --text "댓글" [--profile 유튜브1] [--dry-run]

--dry-run: 입력까지만 하고 게시하지 않음. 규칙 예약·기록을 남기지 않는다.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from .. import rules, store
from ..commenter.validate import all_ok, validate
from ..config import load_config
from .runner import Poster
from .service import execute_attempt, parse_video_id


def print_event(ev: dict) -> None:
    stage = ev["stage"]
    if stage == "typing":
        typed, total = ev.get("typed", 0), ev.get("total", 0)
        if typed in (0, total) or typed % 10 == 0:
            print(f"\r  ✎ 입력 중 {typed}/{total}자", end="\n" if typed == total else "", flush=True)
        return
    line = f"  ● {ev['label']}"
    if ev.get("comment_url"):
        line += f"  → {ev['comment_url']}"
    if ev.get("error_message"):
        line += f"\n    이유: {ev['error_message']}"
    if ev.get("screenshot_path"):
        line += f"\n    캡처: {ev['screenshot_path']}"
    print(line, flush=True)


async def main() -> int:
    ap = argparse.ArgumentParser(description="유튜브 댓글 게시 자동화 시험")
    ap.add_argument("--url", required=True, help="유튜브 영상 주소 또는 ID")
    ap.add_argument("--text", required=True, help="댓글 내용")
    ap.add_argument("--profile", default=None, help="프로필 이름 (생략 시 자동 배정)")
    ap.add_argument("--dry-run", action="store_true", help="입력까지만, 게시 안 함")
    args = ap.parse_args()

    cfg = load_config()
    video_id = parse_video_id(args.url)
    text = args.text.strip()

    checks = validate(text, cfg.comment)
    print("규칙 검사:")
    for c in checks:
        print(f"  {'✓' if c.ok else '✗'} {c.label} {c.detail}")
    if not all_ok(checks) and not args.dry_run:
        print("규칙을 통과하지 못해 게시하지 않아요.")
        return 2

    if args.dry_run:
        profile = cfg.profile(args.profile or cfg.profiles[0].name)
        print(f"[dry-run] {profile.name} → {video_id}")
        result = await Poster(cfg, profile, video_id, text, emit=print_event, submit=False).run()
        return 0 if result.status == "dry_run" else 1

    store.connect()
    if n := store.recover_interrupted():
        print(f"(이전에 중단된 게시 {n}건을 '확인 실패'로 정리했어요)")
    try:
        res = rules.reserve(cfg, video_id=video_id, comment_text=text, profile=args.profile)
    except rules.RuleError as e:
        print(f"게시 불가 [{e.code}]: {e.message}")
        return 3
    print(f"[게시] #{res.attempt_id} {res.profile} → {video_id}")
    result = await execute_attempt(cfg, res.attempt_id, emit=print_event)
    status = rules.check_status(cfg)
    print(f"결과: {result.status} · 오늘 {status['today_used']}/{status['daily_max']}")
    return 0 if result.status == "posted" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
