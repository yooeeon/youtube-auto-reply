"""댓글 초안 파이프라인: 수집 → ① 분석 → ② 후보 생성 → ③ 규칙 검사(코드) → ④ AI 심사 → 선택.

규칙 검사 실패나 심사 점수 미달이면 사유를 넣어 다시 생성한다(최대 2회).
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Callable

from .. import store, youtube
from ..config import Config
from ..llm import complete_json
from ..transcript import fetch_transcript
from . import prompts as P
from .validate import all_ok, failures_text, to_dicts, validate

MAX_ROUNDS = 3  # 첫 생성 + 재생성 2회

Progress = Callable[[str], Any]


def _noop(_: str) -> None:
    pass


def previous_comments(limit: int = 30) -> list[str]:
    with store._lock:
        rows = store.connect().execute(
            "SELECT comment_text FROM attempts WHERE status IN ('posted','unverified') ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [r[0] for r in rows]


def _verified_hooks(hooks: list[str], corpus: str) -> list[str]:
    """AI가 지어낸 핵심어 제거: 자막·제목·설명에 글자 그대로 있는 것만."""
    out = []
    for h in hooks:
        h = h.strip()
        if 2 <= len(h) <= 12 and h in corpus and h not in out:
            out.append(h)
    return out


async def analyze(cfg: Config, video_id: str, progress: Progress = _noop, force: bool = False) -> dict:
    if not force:
        with store._lock:
            row = store.connect().execute("SELECT * FROM analyses WHERE video_id=?", (video_id,)).fetchone()
        if row:
            a = json.loads(row["scenes_json"])
            return a

    progress("영상 정보 불러오는 중")
    meta = await youtube.get_video(cfg, video_id)
    progress("자막 읽는 중")
    tr = await fetch_transcript(video_id)
    progress("인기 댓글 읽는 중")
    comments = await youtube.get_top_comments(cfg, video_id, 20)

    body = tr.to_prompt() if tr.available else "(자막 없음 — 제목과 설명으로 판단)"
    top = "\n".join(f"- (좋아요 {c['likes']}) {c['text'][:150]}" for c in comments) or "(댓글 없음)"
    user = (
        f"제목: {meta['title']}\n채널: {meta['channel_title']}\n설명:\n{(meta['description'] or '')[:1500]}\n\n"
        f"자막({tr.source}):\n{body}\n\n인기 댓글:\n{top}"
    )
    progress("연애 고민 정리 중")
    res = await complete_json(cfg, system=P.ANALYZE_SYSTEM, user=user, schema=P.ANALYZE_SCHEMA, effort="medium")

    corpus = " ".join([meta["title"] or "", meta["description"] or "", tr.full_text()])
    a = res.data
    a["hooks"] = _verified_hooks(a.get("hooks", []), corpus)
    for sc in a.get("scenes", []):  # 근거 대사가 실제 자막에 있는지 표시
        sc["verified"] = bool(sc.get("quote")) and sc["quote"].replace(" ", "")[:12] in corpus.replace(" ", "")
    a["video"] = {k: meta.get(k) for k in ("video_id", "title", "channel_title", "published_at",
                                            "thumbnail_url", "view_count", "duration_sec")}
    a["transcript_source"] = tr.source
    a["transcript_excerpt"] = tr.to_prompt(6000) if tr.available else ""
    a["top_comments"] = comments
    a["llm"] = {"backend": res.backend, "model": res.model, "ms": res.duration_ms}

    with store._lock:
        store.connect().execute(
            """INSERT OR REPLACE INTO analyses (video_id, transcript_source, concern_one_liner, scenes_json,
                 top_comments_json, audience_json, model, created_at) VALUES (?,?,?,?,?,?,?,?)""",
            (video_id, tr.source, a["concern_one_liner"], json.dumps(a, ensure_ascii=False),
             json.dumps(comments, ensure_ascii=False), json.dumps(a["audience"], ensure_ascii=False),
             res.model, store.now_kst().isoformat()),
        )
    return a


def _scene_terms(a: dict) -> list[str]:
    hooks = a.get("hooks", [])
    if hooks:
        return hooks
    # 핵심어가 검증되지 않았으면(자막 없음 등) 제목 속 어절로 대체
    title = a.get("video", {}).get("title") or ""
    return [w for w in title.replace("[", " ").replace("]", " ").split() if len(w) >= 2]


async def _generate(cfg: Config, a: dict, prev: list[str], feedback: str, n: int) -> list[dict]:
    scenes = "\n".join(f"{i}. [{s['ts']}] \"{s['quote']}\" — {s['why']}" for i, s in enumerate(a["scenes"]))
    tone_samples = "\n".join(f"- {c['text'][:80]}" for c in a.get("top_comments", [])[:6])
    user = (
        f"영상 제목: {a['video']['title']}\n"
        f"고민 한 줄: {a['concern_one_liner']}\n고민 유형: {a['problem_type']}\n판단 기준(criterion): {a['criterion']}\n"
        f"장면:\n{scenes}\n"
        f"hooks(이 중 하나를 댓글에 글자 그대로 넣기): {', '.join(a['hooks'])}\n"
        f"시청자: {a['audience']['who']} / 분위기: {a['audience']['mood']} / 말투: {a['audience']['tone']}\n"
        f"인기 댓글 말투 예:\n{tone_samples}\n\n"
        f"이전에 쓴 댓글(구조·표현 겹치지 말 것):\n" + ("\n".join(f"- {p}" for p in prev[:8]) or "(없음)") + "\n\n"
        f"후보 {n}개를 써라."
        + (f"\n\n직전 시도의 문제(반드시 고칠 것):\n{feedback}" if feedback else "")
    )
    res = await complete_json(cfg, system=P.GENERATE_SYSTEM, user=user, schema=P.GENERATE_SCHEMA, effort="medium")
    return res.data.get("candidates", [])[: n + 1]


async def _judge(cfg: Config, a: dict, cands: list[dict]) -> list[dict]:
    listing = "\n".join(f"{i}. {c['text']}" for i, c in enumerate(cands))
    user = (
        f"영상 제목: {a['video']['title']}\n고민: {a['concern_one_liner']}\n"
        f"장면: " + " / ".join(s["quote"] for s in a["scenes"])
        + (f"\n\n자막 일부(장면 사실 확인용):\n{a['transcript_excerpt']}" if a.get("transcript_excerpt") else "")
        + f"\n\n후보:\n{listing}"
    )
    res = await complete_json(cfg, system=P.JUDGE_SYSTEM, user=user, schema=P.JUDGE_SCHEMA, effort="low")
    by_idx = {s["idx"]: s for s in res.data.get("scores", [])}
    out = []
    for i, _ in enumerate(cands):
        s = by_idx.get(i, {"uniqueness": 1, "naturalness": 1, "persuasion": 1, "reason": "채점 누락"})
        s["total"] = round(sum(s[k] * w for k, w in P.WEIGHTS.items()), 2)
        out.append(s)
    return out


async def make_draft(cfg: Config, video_id: str, progress: Progress = _noop, regenerate: bool = False) -> dict:
    a = await analyze(cfg, video_id, progress)
    prev = previous_comments()
    # 같은 영상에 이미 달린 운명원 언급 댓글도 '겹치지 말 것'에 포함
    prev += [c["text"] for c in a.get("top_comments", []) if cfg.comment.brand in c.get("text", "")]
    n = int((cfg.raw.get("llm", {}) or {}).get("candidates", 3))
    batch = uuid.uuid4().hex[:8]
    feedback = ""
    if regenerate:  # 다시 만들기: 이전 초안과 다른 방향 강제
        with store._lock:
            olds = store.connect().execute(
                "SELECT text FROM drafts WHERE video_id=? ORDER BY id DESC LIMIT 6", (video_id,)
            ).fetchall()
        prev = [r[0] for r in olds] + prev
        feedback = "이전 초안들과 다른 장면·다른 문장 구조로 써라."

    pool: list[dict] = []
    for round_ in range(MAX_ROUNDS):
        progress("댓글 후보 쓰는 중" if round_ == 0 else f"다시 쓰는 중 ({round_}/{MAX_ROUNDS - 1})")
        cands = await _generate(cfg, a, prev, feedback, n)
        for c in cands:
            checks = validate(c["text"], cfg.comment, scene_terms=_scene_terms(a), previous=prev)
            c["checks"] = to_dicts(checks)
            c["ok"] = all_ok(checks)
            c["round"] = round_
        passed = [c for c in cands if c["ok"]]
        if passed:
            progress("후보 심사 중")
            for c, s in zip(passed, await _judge(cfg, a, passed)):
                c["judge"] = s
        pool += cands
        best = max((c for c in pool if c["ok"]), key=lambda c: c["judge"]["total"], default=None)
        if best and best["judge"]["total"] >= P.PASS_SCORE:
            break
        problems = [f"- \"{c['text']}\" → {failures_text(validate(c['text'], cfg.comment, scene_terms=_scene_terms(a), previous=prev))}"
                    for c in cands if not c["ok"]]
        problems += [f"- \"{c['text']}\" → 심사 {c['judge']['total']}점: {c['judge']['reason']}"
                     for c in passed]
        feedback = "\n".join(problems)

    ok_pool = sorted((c for c in pool if c["ok"]), key=lambda c: -c["judge"]["total"])
    selected = ok_pool[0] if ok_pool else None
    alternatives = ok_pool[1:3]
    needs_review = not selected or selected["judge"]["total"] < P.PASS_SCORE

    with store._lock:
        conn = store.connect()
        for c in pool:
            conn.execute(
                """INSERT INTO drafts (video_id, batch_id, text, persona, strategy, anchor_scene_idx, checks_json,
                     judge_json, selected, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (video_id, batch, c["text"], "조언자", c.get("criterion"), c.get("scene_idx"),
                 json.dumps(c["checks"], ensure_ascii=False), json.dumps(c.get("judge"), ensure_ascii=False),
                 1 if c is selected else 0, store.now_kst().isoformat()),
            )
            c["id"] = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    return {
        "video": a["video"],
        "concern_one_liner": a["concern_one_liner"],
        "problem_type": a["problem_type"],
        "criterion": a["criterion"],
        "scenes": a["scenes"],
        "hooks": a["hooks"],
        "transcript_source": a["transcript_source"],
        "selected": selected,
        "alternatives": alternatives,
        "rejected": [c for c in pool if not c["ok"]],
        "needs_review": needs_review,
        "batch_id": batch,
    }
