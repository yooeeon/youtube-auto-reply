"""댓글 초안 생성 시험용 CLI (대시보드 없이 M2~M3 확인).

  python -m app.commenter.cli <영상주소> [--regenerate] [--reanalyze]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time

from ..config import load_config
from ..llm import LLMError, resolve_backend
from ..poster.service import parse_video_id
from .pipeline import analyze, make_draft


def show_cand(c: dict, mark: str) -> None:
    j = c.get("judge") or {}
    score = f"{j['total']}점 (맥락 {j['uniqueness']} · 자연 {j['naturalness']} · 유도 {j['persuasion']})" if j else "-"
    length = next((ch["detail"] for ch in c["checks"] if ch["key"] == "length"), "")
    print(f"{mark} {c['text']}\n    {length} | {score}")
    if j.get("reason"):
        print(f"    심사: {j['reason']}")
    bad = [f"{ch['label']}: {ch['detail']}" for ch in c["checks"] if not ch["ok"]]
    if bad:
        print(f"    ✗ {' / '.join(bad)}")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--regenerate", action="store_true")
    ap.add_argument("--reanalyze", action="store_true")
    args = ap.parse_args()
    cfg = load_config()
    vid = parse_video_id(args.url)
    print(f"AI: {resolve_backend(cfg)} · YouTube: {'API' if cfg.youtube_api_key else 'yt-dlp'}")
    t0 = time.monotonic()
    prog = lambda m: print(f"  … {m} ({time.monotonic() - t0:.0f}s)", flush=True)
    try:
        if args.reanalyze:
            await analyze(cfg, vid, prog, force=True)
        d = await make_draft(cfg, vid, prog, regenerate=args.regenerate)
    except LLMError as e:
        print(f"실패: {e}")
        return 1
    print(f"\n■ {d['video']['title']} — {d['video']['channel_title']}  (자막: {d['transcript_source']})")
    print(f"💬 고민 한 줄: {d['concern_one_liner']}")
    print(f"   유형: {d['problem_type']} · 판단 기준: {d['criterion']}")
    for s in d["scenes"]:
        print(f"📍 [{s['ts']}] \"{s['quote']}\" {'✓' if s.get('verified') else '(자막 대조 실패)'}")
    print(f"🔑 핵심어: {', '.join(d['hooks'])}\n")
    if d["selected"]:
        show_cand(d["selected"], "✅ 선택")
    for c in d["alternatives"]:
        show_cand(c, "  · 대안")
    for c in d["rejected"]:
        show_cand(c, "  ✗ 탈락")
    if d["needs_review"]:
        print("\n⚠ 검토 필요: 기준 점수(3.8) 미달 또는 통과 후보 없음")
    print(f"\n총 {time.monotonic() - t0:.0f}초")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
