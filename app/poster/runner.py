"""게시 상태 머신 (docs/AUTOMATION.md §2).

프로필 여는 중 → 영상 여는 중 → 댓글 창 여는 중 → 입력창 여는 중 → 입력 중(n/N) → 게시 중 → 확인 중 → 게시 완료
제출 버튼을 누른 뒤의 실패는 재시도하지 않고 'unverified'로 남긴다 (중복 게시 방지, D-13).
"""
from __future__ import annotations

import asyncio
import inspect
import random
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, urlparse

from playwright.async_api import Locator, Page, async_playwright
from playwright.async_api import TimeoutError as PWTimeout

from ..config import ROOT, SCREENSHOT_DIR, Config, Profile
from . import selectors as S
from .browser import ProfileInUse, open_profile

STAGE_LABELS = {
    "queued": "대기 중",
    "opening_profile": "프로필 여는 중",
    "opening_video": "영상 여는 중",
    "opening_comments": "댓글 창 여는 중",
    "focusing_input": "댓글 입력창 여는 중",
    "typing": "입력 중",
    "submitting": "게시 중",
    "verifying": "내 댓글 확인 중",
    "posted": "게시 완료",
    "dry_run": "입력 완료 (게시하지 않음)",
    "failed": "실패",
    "unverified": "확인 실패",
}

ERROR_MESSAGES = {
    "PROFILE_MISSING": "프로필 폴더가 없어요. 먼저 로그인으로 프로필을 만들어 주세요.",
    "PROFILE_IN_USE": "이 프로필의 크롬 창이 이미 열려 있어요. 그 창을 닫고 다시 시도해 주세요.",
    "NOT_LOGGED_IN": "이 프로필은 구글 로그인이 안 돼 있어요. 로그인 후 다시 시도해 주세요.",
    "NO_CHANNEL": "이 계정엔 유튜브 채널이 없어 댓글을 달 수 없어요. 채널을 먼저 만들어 주세요.",
    "COMMENTS_DISABLED": "이 영상은 댓글이 꺼져 있어요.",
    "INPUT_NOT_FOUND": "댓글 입력창을 찾지 못했어요. 캡처를 확인해 주세요.",
    "TEXT_MISMATCH": "입력된 내용이 초안과 달라 게시를 멈췄어요.",
    "SUBMIT_FAILED": "게시 버튼을 누르지 못했어요.",
    "NOT_VISIBLE": "게시는 했지만 내 댓글이 보이지 않아요 (스팸 보류 가능성). 직접 확인해 주세요.",
    "BROWSER_CLOSED": "작업 중에 크롬 창이 닫혔어요.",
    "TIMEOUT": "화면이 제때 뜨지 않아 시간이 초과됐어요.",
    "UNKNOWN": "알 수 없는 오류가 났어요. 캡처와 로그를 확인해 주세요.",
}

Emit = Callable[[dict], Any]


class PostError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass
class PostResult:
    status: str  # posted | failed | unverified | dry_run
    stage: str
    error_code: str | None = None
    error_message: str | None = None
    screenshot_path: str | None = None
    comment_url: str | None = None


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


class Poster:
    def __init__(
        self,
        cfg: Config,
        profile: Profile,
        video_id: str,
        text: str,
        *,
        attempt_id: int | None = None,
        emit: Emit | None = None,
        submit: bool = True,
    ):
        self.cfg = cfg
        self.profile = profile
        self.video_id = video_id
        self.text = text.strip()
        self.attempt_id = attempt_id
        self._emit_cb = emit
        self.submit = submit
        self.stage = "queued"
        self.submitted = False

    # ── 이벤트 ─────────────────────────────────────────────
    async def _emit(self, **payload) -> None:
        if not self._emit_cb:
            return
        event = {"attempt_id": self.attempt_id, "stage": self.stage,
                 "label": STAGE_LABELS.get(self.stage, self.stage), **payload}
        r = self._emit_cb(event)
        if inspect.isawaitable(r):
            await r

    async def _set_stage(self, stage: str, **payload) -> None:
        self.stage = stage
        await self._emit(**payload)

    async def _shot(self, page: Page | None, tag: str) -> str | None:
        if page is None or page.is_closed():
            return None
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{self.attempt_id or 'dry'}_{tag}_{datetime.now():%Y%m%d-%H%M%S}.png"
        path = SCREENSHOT_DIR / name
        try:
            await page.screenshot(path=str(path))
            return str(path.relative_to(ROOT))
        except Exception:
            return None

    # ── 실행 ───────────────────────────────────────────────
    async def run(self) -> PostResult:
        if not self.profile.exists:
            return await self._fail(None, "PROFILE_MISSING")
        async with async_playwright() as pw:
            ctx = page = None
            try:
                await self._set_stage("opening_profile")
                ctx = await open_profile(pw, self.profile, self.cfg.browser)
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                page.set_default_timeout(15_000)

                await self._open_video(page)
                await self._open_comments(page)
                box = await self._focus_input(page)
                await self._type(page, box)

                if not self.submit:
                    shot = await self._shot(page, "dry_run")
                    await self._set_stage("dry_run", screenshot_path=shot)
                    await asyncio.sleep(self.cfg.browser.keep_open_sec)
                    return PostResult("dry_run", "dry_run", screenshot_path=shot)

                await self._submit(page)
                url = await self._verify(page)
                shot = await self._shot(page, "posted")
                await self._set_stage("posted", comment_url=url, screenshot_path=shot)
                await asyncio.sleep(self.cfg.browser.keep_open_sec)
                return PostResult("posted", "posted", screenshot_path=shot, comment_url=url)

            except ProfileInUse:
                return await self._fail(None, "PROFILE_IN_USE")
            except PostError as e:
                return await self._fail(page, e.code, e.detail)
            except PWTimeout as e:
                return await self._fail(page, "TIMEOUT", f"{STAGE_LABELS.get(self.stage)} 단계: {str(e).splitlines()[0][:200]}")
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                code = "BROWSER_CLOSED" if "has been closed" in msg else "UNKNOWN"
                return await self._fail(page, code, msg.splitlines()[0][:300] if msg else type(e).__name__)
            finally:
                if ctx is not None:
                    try:
                        await ctx.close()
                    except Exception:
                        pass

    async def _fail(self, page: Page | None, code: str, detail: str = "") -> PostResult:
        failed_at = self.stage
        shot = await self._shot(page, f"fail_{failed_at}")
        status = "unverified" if self.submitted else "failed"
        message = ERROR_MESSAGES.get(code, code) + (f" ({detail})" if detail else "")
        self.stage = status
        await self._emit(error_code=code, error_message=message, failed_stage=failed_at, screenshot_path=shot)
        if page is not None and not page.is_closed():
            await asyncio.sleep(min(self.cfg.browser.keep_open_sec, 3))
        return PostResult(status, failed_at, code, message, shot)

    # ── 단계 ───────────────────────────────────────────────
    async def _open_video(self, page: Page) -> None:
        await self._set_stage("opening_video")
        await page.goto(S.WATCH_URL.format(video_id=self.video_id), wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_selector(S.WATCH_ROOT, state="attached", timeout=30_000)
        for _ in range(40):  # 상단 바에 아바타(로그인) 또는 로그인 버튼이 뜰 때까지
            if await page.locator(S.AVATAR).first.is_visible():
                break
            if await page.locator(S.SIGN_IN).first.is_visible():
                raise PostError("NOT_LOGGED_IN")
            await page.wait_for_timeout(500)
        else:
            raise PostError("NOT_LOGGED_IN", "로그인 상태를 확인하지 못했어요")
        await self._pause_video(page)

    async def _pause_video(self, page: Page) -> None:
        await page.evaluate("() => { const v = document.querySelector('video'); if (v) { v.muted = true; v.pause(); } }")

    async def _open_comments(self, page: Page) -> None:
        await self._set_stage("opening_comments")
        await page.locator(S.COMMENTS_SECTION).wait_for(state="attached", timeout=20_000)
        await page.evaluate("() => document.querySelector('ytd-comments#comments')?.scrollIntoView({block: 'start'})")
        disabled_re = re.compile(S.COMMENTS_DISABLED_TEXT)
        for i in range(40):
            if await page.locator(S.PLACEHOLDER).first.is_visible():
                await page.locator(S.PLACEHOLDER).first.scroll_into_view_if_needed()
                await self._pause_video(page)
                return
            disabled = page.locator(S.COMMENTS_DISABLED).filter(has_text=disabled_re)
            if await disabled.count() and await disabled.first.is_visible():
                raise PostError("COMMENTS_DISABLED")
            # 댓글은 지연 로딩: 조금씩 내려 준다
            await page.mouse.wheel(0, 400 if i % 2 == 0 else -150)
            await page.wait_for_timeout(600)
        raise PostError("INPUT_NOT_FOUND", "댓글 영역이 열리지 않았어요")

    async def _focus_input(self, page: Page) -> Locator:
        await self._set_stage("focusing_input")
        await page.locator(S.PLACEHOLDER).first.click()
        for _ in range(30):
            box = page.locator(S.INPUT).first
            if await box.is_visible():
                return box
            if await page.locator(S.CHANNEL_DIALOG).first.is_visible():
                raise PostError("NO_CHANNEL")
            if await page.locator(S.SIGNIN_DIALOG).first.is_visible():
                raise PostError("NOT_LOGGED_IN")
            await page.wait_for_timeout(500)
        raise PostError("INPUT_NOT_FOUND")

    async def _type(self, page: Page, box: Locator) -> None:
        total = len(self.text)
        await self._set_stage("typing", typed=0, total=total)
        await box.click()
        await page.keyboard.press("ControlOrMeta+A")
        await page.keyboard.press("Backspace")
        lo, hi = self.cfg.typing.min_delay_ms, self.cfg.typing.max_delay_ms
        for i, ch in enumerate(self.text, 1):
            await page.keyboard.type(ch)
            delay = random.uniform(lo, hi)
            if ch in ".!?…~":
                delay += random.uniform(150, 350)  # 문장 끝에서 사람처럼 잠깐 쉼
            await asyncio.sleep(delay / 1000)
            await self._emit(typed=i, total=total)
        typed = norm(await box.inner_text())
        if typed != norm(self.text):
            raise PostError("TEXT_MISMATCH", f"입력됨: {typed[:40]}…")

    async def _submit(self, page: Page) -> None:
        await self._set_stage("submitting")
        btn = page.locator(S.SUBMIT).first
        try:
            await btn.wait_for(state="visible", timeout=10_000)
            for _ in range(40):
                if await btn.is_enabled():
                    break
                await page.wait_for_timeout(250)
            else:
                raise PostError("SUBMIT_FAILED", "게시 버튼이 활성화되지 않았어요")
        except PWTimeout as e:
            raise PostError("SUBMIT_FAILED", "게시 버튼을 찾지 못했어요") from e
        self.submitted = True  # 이 시점 이후 실패는 unverified (재시도 금지)
        await btn.click()

    async def _verify(self, page: Page) -> str:
        await self._set_stage("verifying")
        snippet = norm(self.text)[:30]
        for round_ in range(2):
            thread = page.locator(S.THREAD).filter(
                has=page.locator(S.THREAD_TEXT, has_text=snippet)
            ).first
            try:
                await thread.wait_for(state="visible", timeout=20_000)
            except PWTimeout:
                if round_ == 0:  # 새로고침 후 한 번 더 확인 (게시는 다시 하지 않음)
                    await page.reload(wait_until="domcontentloaded")
                    await page.wait_for_selector(S.WATCH_ROOT, state="attached", timeout=30_000)
                    await self._open_comments(page)
                    await self._set_stage("verifying")
                    continue
                raise PostError("NOT_VISIBLE")
            await thread.scroll_into_view_if_needed()
            # 시연 화면에서 확인된 내 댓글을 눈에 띄게 표시
            await thread.evaluate(
                "el => { el.style.outline = '3px solid #22c55e'; el.style.outlineOffset = '6px'; el.style.borderRadius = '8px'; }"
            )
            comment_id = None
            try:
                href = await thread.locator(S.COMMENT_LINK).first.get_attribute("href", timeout=5_000)
                comment_id = parse_qs(urlparse(href or "").query).get("lc", [None])[0]
            except PWTimeout:
                pass
            if comment_id:
                return S.COMMENT_URL.format(video_id=self.video_id, comment_id=comment_id)
            return S.WATCH_URL.format(video_id=self.video_id)
        raise PostError("NOT_VISIBLE")
