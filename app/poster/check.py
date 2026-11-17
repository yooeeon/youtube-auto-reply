"""프로필 로그인 상태 점검: 자동화 크롬으로 유튜브 접속 → 아바타 유무.

  python -m app.poster.check 유튜브1
"""
from __future__ import annotations

import asyncio
import sys

from playwright.async_api import async_playwright

from ..config import Config, Profile, load_config
from . import selectors as S
from .browser import ProfileInUse, open_profile


async def check_profile(cfg: Config, profile: Profile) -> dict:
    if not profile.exists:
        return {"profile": profile.name, "status": "missing", "message": "프로필 폴더가 없어요. 로그인부터 해 주세요."}
    async with async_playwright() as pw:
        try:
            ctx = await open_profile(pw, profile, cfg.browser)
        except ProfileInUse:
            return {"profile": profile.name, "status": "in_use", "message": "이 프로필 크롬 창이 열려 있어요. 닫고 다시 점검해 주세요."}
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto("https://www.youtube.com/?hl=ko&gl=KR", wait_until="domcontentloaded", timeout=30_000)
            for _ in range(40):
                if await page.locator(S.AVATAR).first.is_visible():
                    return {"profile": profile.name, "status": "ok", "message": "로그인 돼 있어요."}
                if await page.locator(S.SIGN_IN).first.is_visible():
                    return {"profile": profile.name, "status": "logged_out", "message": "로그인이 안 돼 있어요."}
                await page.wait_for_timeout(500)
            return {"profile": profile.name, "status": "unknown", "message": "로그인 상태를 확인하지 못했어요."}
        finally:
            await ctx.close()


async def main() -> int:
    cfg = load_config()
    names = sys.argv[1:] or [p.name for p in cfg.profiles]
    ok = True
    for name in names:
        r = await check_profile(cfg, cfg.profile(name))
        print(f"{'✓' if r['status'] == 'ok' else '⚠'} {r['profile']}: {r['message']}")
        ok &= r["status"] == "ok"
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
