"""FastAPI 서버 — 세 칸 대시보드의 API (docs/ARCHITECTURE.md §4)."""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import rules, store, youtube
from .commenter.pipeline import make_draft, previous_comments
from .commenter.validate import all_ok, char_counts, failures_text, to_dicts, validate
from .config import DATA_DIR, ROOT, SCREENSHOT_DIR, load_config
from .events import broker, sse
from .llm import LLMError, resolve_backend
from .poster.check import check_profile
from .poster.runner import STAGE_LABELS
from .poster.service import POST_LOCK, execute_attempt, parse_video_id

app = FastAPI(title="운명원 댓글 에이전트")
WEB = ROOT / "web"

# 프로필 로그인 점검 결과 (자동 점검은 크롬 창을 띄우므로 버튼을 눌렀을 때·게시 결과로만 갱신)
# 서버를 다시 켜도 유지되도록 data/profile_status.json 에 저장
PROFILE_STATUS_FILE = DATA_DIR / "profile_status.json"
try:
    profile_status: dict[str, dict] = json.loads(PROFILE_STATUS_FILE.read_text(encoding="utf-8"))
except Exception:
    profile_status = {}


def set_profile_status(name: str, status: str, message: str) -> None:
    profile_status[name] = {"status": status, "message": message, "checked_at": store.now_kst().isoformat()}
    try:
        PROFILE_STATUS_FILE.write_text(json.dumps(profile_status, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
# 대시보드에 보이는 목록 (탭별)
video_lists: dict[str, list[dict]] = {"latest": [], "rising": []}
running_tasks: set[asyncio.Task] = set()
# 영상별 마지막 초안 (다시 만들기를 누르기 전까지 재사용)
draft_cache: dict[str, dict] = {}


@app.on_event("startup")
async def startup() -> None:
    store.connect()
    store.recover_interrupted()
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


def cfg():
    return load_config()


def err(status: int, message: str, code: str = "ERROR"):
    return JSONResponse({"error": message, "code": code}, status_code=status)


# ── 상태 ────────────────────────────────────────────────────────────
@app.get("/api/status")
async def status(video_id: str | None = None):
    c = cfg()
    try:
        backend = resolve_backend(c)
    except LLMError as e:
        backend = None
        backend_err = str(e)
    else:
        backend_err = None
    profiles = []
    for p in c.profiles:
        st = profile_status.get(p.name)
        if not p.exists:
            st = {"status": "missing", "message": "로그인 전 (프로필 없음)"}
        elif not st:
            with store._lock:
                ok_before = store.connect().execute(
                    "SELECT 1 FROM attempts WHERE profile=? AND status='posted' LIMIT 1", (p.name,)).fetchone()
            st = ({"status": "ok", "message": "로그인 돼 있어요. (이전 게시 성공)"} if ok_before
                  else {"status": "unchecked", "message": "점검 전 — [점검]을 눌러 로그인 상태를 확인하세요"})
        profiles.append({"name": p.name, **st,
                         "today": store.count_today(store.connect(), store.now_kst().date().isoformat(), p.name)})
    return {
        **rules.check_status(c, video_id),
        "profiles": profiles,
        "youtube_api": bool(c.youtube_api_key),
        "llm_backend": backend,
        "llm_error": backend_err,
        "posting": POST_LOCK.locked(),
        "comment_cfg": {"min": c.comment.min_chars, "max": c.comment.max_chars, "brand": c.comment.brand},
    }


# ── ① 영상 불러오기 ───────────────────────────────────────────────────
def _public(m: dict, tab: str) -> dict:
    keep = ("video_id", "title", "channel_title", "published_at", "thumbnail_url", "duration_sec",
            "view_count", "comment_count", "views_per_hour", "commented")
    out = {k: m.get(k) for k in keep}
    out["tab"] = tab
    return out


def _refresh_commented(lst: list[dict]) -> list[dict]:
    done = store.commented_video_ids()
    for m in lst:
        m["commented"] = m["video_id"] in done
    return lst


@app.get("/api/videos")
async def list_videos(tab: str = "latest"):
    return {"videos": _refresh_commented(video_lists.get(tab, []))}


@app.post("/api/videos/load")
async def load_videos():
    try:
        metas = await youtube.search_latest(cfg())
    except Exception as e:  # noqa: BLE001
        return err(502, f"영상을 불러오지 못했어요: {str(e)[:200]}")
    # 링크로 직접 추가한 영상은 위에 유지
    linked = [m for m in video_lists["latest"] if m.get("_linked")]
    ids = {m["video_id"] for m in linked}
    video_lists["latest"] = linked + [_public(m, "latest") for m in metas if m["video_id"] not in ids]
    return {"videos": video_lists["latest"]}


@app.post("/api/videos/rising")
async def load_rising():
    try:
        metas = await youtube.find_rising(cfg())
    except Exception as e:  # noqa: BLE001
        return err(502, f"뜨기 직전 영상을 찾지 못했어요: {str(e)[:200]}")
    video_lists["rising"] = [_public(m, "rising") for m in metas]
    return {"videos": video_lists["rising"]}


class LinkIn(BaseModel):
    url: str


@app.post("/api/videos/link")
async def add_link(body: LinkIn):
    try:
        vid = parse_video_id(body.url)
    except ValueError as e:
        return err(400, str(e))
    try:
        m = await youtube.add_by_link(cfg(), vid)
    except Exception as e:  # noqa: BLE001
        return err(404, f"영상 정보를 가져오지 못했어요: {str(e)[:150]}")
    pub = _public(m, "latest")
    pub["_linked"] = True
    pub["warning"] = None if m["postable"] else f"{m['not_postable_reason']} — 게시가 실패할 수 있어요."
    video_lists["latest"] = [pub] + [x for x in video_lists["latest"] if x["video_id"] != vid]
    return {"video": pub, "videos": video_lists["latest"]}


# ── ② 초안 ───────────────────────────────────────────────────────────
@app.get("/api/videos/{video_id}/draft")
async def draft_stream(video_id: str, regenerate: int = 0):
    """SSE: 진행 단계 → 최종 결과(type=result) 또는 오류(type=error)."""
    q: asyncio.Queue = asyncio.Queue()

    async def run():
        try:
            if not regenerate and video_id in draft_cache:  # 같은 영상을 다시 고르면 직전 초안을 그대로
                q.put_nowait({"type": "result", "draft": draft_cache[video_id]})
                return
            d = await make_draft(cfg(), video_id, lambda m: q.put_nowait({"type": "progress", "message": m}),
                                 regenerate=bool(regenerate))
            draft_cache[video_id] = d
            q.put_nowait({"type": "result", "draft": d})
        except LLMError as e:
            q.put_nowait({"type": "error", "message": str(e)})
        except youtube.YouTubeError as e:
            q.put_nowait({"type": "error", "message": str(e)})
        except Exception as e:  # noqa: BLE001
            q.put_nowait({"type": "error", "message": f"초안을 만들지 못했어요: {str(e)[:200]}"})
        finally:
            q.put_nowait(None)

    task = asyncio.create_task(run())
    running_tasks.add(task)
    task.add_done_callback(running_tasks.discard)

    async def gen():
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            if ev is None:
                return
            yield sse(ev)

    return StreamingResponse(gen(), media_type="text/event-stream")


def _scene_terms(video_id: str) -> list[str] | None:
    with store._lock:
        row = store.connect().execute("SELECT scenes_json FROM analyses WHERE video_id=?", (video_id,)).fetchone()
    if not row:
        return None
    from .commenter.pipeline import _scene_terms as terms
    return terms(json.loads(row[0]))


def _check(video_id: str, text: str) -> list:
    c = cfg()
    prev = previous_comments()
    return validate(text, c.comment, scene_terms=_scene_terms(video_id), previous=prev)


class ValidateIn(BaseModel):
    video_id: str
    text: str


@app.post("/api/drafts/validate")
async def validate_draft(body: ValidateIn):
    checks = _check(body.video_id, body.text)
    with_sp, no_sp = char_counts(body.text.strip())
    return {"ok": all_ok(checks), "checks": to_dicts(checks), "chars": {"with_spaces": with_sp, "no_spaces": no_sp}}


# ── ③ 댓글 달기 ───────────────────────────────────────────────────────
class PostIn(BaseModel):
    video_id: str
    text: str
    profile: str | None = None  # None = 자동 배정
    draft_id: int | None = None
    video_title: str | None = None
    tab: str = "latest"


@app.post("/api/post")
async def post(body: PostIn):
    c = cfg()
    if body.tab == "rising":
        return err(400, "뜨기 직전 영상은 초안까지만 만들어요. 게시하려면 링크로 추가해 주세요.", "RISING_DRAFT_ONLY")
    if POST_LOCK.locked():
        return err(429, "다른 댓글을 게시하는 중이에요. 끝나면 다시 눌러 주세요.", "BUSY")
    checks = _check(body.video_id, body.text)
    if not all_ok(checks):
        return err(400, f"댓글 규칙을 통과하지 못했어요: {failures_text(checks)}", "INVALID_TEXT")
    try:
        res = rules.reserve(c, video_id=body.video_id, comment_text=body.text.strip(), profile=body.profile,
                            video_title=body.video_title, draft_id=body.draft_id)
    except rules.RuleError as e:
        return err(e.http_status, e.message, e.code)

    key = f"post:{res.attempt_id}"

    def emit(ev: dict) -> None:
        final = ev["stage"] in ("posted", "failed", "unverified", "dry_run")
        if ev.get("error_code") in ("NOT_LOGGED_IN", "NO_CHANNEL"):
            set_profile_status(res.profile, "logged_out" if ev["error_code"] == "NOT_LOGGED_IN" else "no_channel",
                               ev.get("error_message", ""))
        elif ev["stage"] == "posted":
            set_profile_status(res.profile, "ok", "로그인 돼 있어요. (최근 게시 성공)")
        broker.publish(key, ev, final=final)

    broker.publish(key, {"attempt_id": res.attempt_id, "stage": "queued", "label": STAGE_LABELS["queued"],
                         "profile": res.profile})
    task = asyncio.create_task(execute_attempt(c, res.attempt_id, emit=emit))
    running_tasks.add(task)
    task.add_done_callback(running_tasks.discard)
    return {"attempt_id": res.attempt_id, "profile": res.profile}


@app.get("/api/post/{attempt_id}/events")
async def post_events(attempt_id: int):
    key = f"post:{attempt_id}"
    if key not in broker.history:
        att = store.get_attempt(attempt_id)
        if not att:
            raise HTTPException(404)
        # 서버 재시작 후 등: DB 상태로 한 번만 응답
        ev = {"attempt_id": attempt_id, "stage": att["status"], "label": STAGE_LABELS.get(att["status"], att["status"]),
              "comment_url": att["comment_url"], "error_message": att["error_message"],
              "screenshot_path": att["screenshot_path"]}
        return StreamingResponse(iter([sse(ev)]), media_type="text/event-stream")
    return StreamingResponse(broker.stream(key), media_type="text/event-stream")


# ── 기록 ────────────────────────────────────────────────────────────
@app.get("/api/attempts")
async def attempts():
    return {"attempts": store.list_attempts()}


class ResolveIn(BaseModel):
    status: str  # 'posted' | 'failed'


@app.post("/api/attempts/{attempt_id}/resolve")
async def resolve(attempt_id: int, body: ResolveIn):
    """'확인 실패(unverified)'를 운영자가 직접 확인 후 정리."""
    att = store.get_attempt(attempt_id)
    if not att or att["status"] != "unverified":
        return err(400, "확인 실패 상태인 기록만 정리할 수 있어요.")
    if body.status not in ("posted", "failed"):
        return err(400, "posted 또는 failed만 가능해요.")
    store.update_attempt(attempt_id, status=body.status,
                         error_message=(att["error_message"] or "") + " (운영자가 직접 확인)")
    return {"ok": True}


# ── 프로필 ──────────────────────────────────────────────────────────
@app.post("/api/profiles/{name}/login")
async def profile_login(name: str):
    try:
        p = cfg().profile(name)
    except KeyError as e:
        return err(404, str(e))
    subprocess.Popen([str(ROOT / ".venv/bin/python"), "-m", "scripts.login_profile", name], cwd=ROOT)
    set_profile_status(name, "unchecked", "로그인 창을 열었어요. 로그인 후 ⌘Q로 닫고 [점검]을 눌러 주세요.")
    return {"ok": True, "dir": str(p.dir)}


@app.post("/api/profiles/{name}/check")
async def profile_check(name: str):
    if POST_LOCK.locked():
        return err(429, "게시 중에는 점검할 수 없어요.")
    c = cfg()
    try:
        p = c.profile(name)
    except KeyError as e:
        return err(404, str(e))
    async with POST_LOCK:
        r = await check_profile(c, p)
    set_profile_status(name, r["status"], r["message"])
    return r


# ── 정적 파일 ───────────────────────────────────────────────────────
@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=WEB), name="static")
DATA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")
