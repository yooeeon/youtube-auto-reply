"""크롬 프로필 열기 — Playwright launch_persistent_context + 전용 --user-data-dir.

주의:
- 크롬 기본 프로필은 자동 제어가 막혀 있으므로 프로젝트 내부 전용 폴더만 쓴다.
- Playwright는 macOS에서 기본으로 --use-mock-keychain 을 붙인다. 그러면 일반 크롬으로
  로그인해 둔 쿠키(키체인으로 암호화)를 못 읽어 로그아웃 상태가 된다 → 반드시 제외.
- --enable-automation 을 빼고 AutomationControlled 를 꺼서 구글/유튜브의 자동화 경고를 줄인다.
"""
from __future__ import annotations

import subprocess
from functools import lru_cache

from playwright.async_api import BrowserContext, Playwright

from ..config import BrowserCfg, Profile


class ProfileInUse(Exception):
    pass


def is_profile_locked(profile: Profile) -> bool:
    """크롬이 이 폴더를 쓰고 있으면 SingletonLock 심볼릭 링크가 있다."""
    return (profile.dir / "SingletonLock").is_symlink() or (profile.dir / "SingletonLock").exists()


@lru_cache
def screen_size() -> tuple[int, int] | None:
    """macOS 메인 화면 크기(포인트). 실패하면 None."""
    try:
        out = subprocess.run(
            ["osascript", "-e", 'tell application "Finder" to get bounds of window of desktop'],
            capture_output=True, text=True, timeout=3,
        ).stdout
        x0, y0, x1, y1 = (int(v) for v in out.strip().split(","))
        return x1 - x0, y1 - y0
    except Exception:
        return None


def window_geometry(bcfg: BrowserCfg) -> tuple[int, int, int, int]:
    """시연용 배치: 대시보드는 화면 왼쪽, 자동화 크롬은 오른쪽 약 42%.
    config의 browser.layout이 'right'(기본)면 화면 크기로 자동 계산, 'fixed'면 window_size/position 사용."""
    if bcfg.layout == "right" and (sz := screen_size()):
        sw, sh = sz
        w = max(640, min(1100, int(sw * 0.42)))
        return w, sh, sw - w, 0
    w, h = bcfg.window_size
    x, y = bcfg.window_position
    return w, h, x, y


async def open_profile(pw: Playwright, profile: Profile, bcfg: BrowserCfg) -> BrowserContext:
    if is_profile_locked(profile):
        raise ProfileInUse(profile.name)
    w, h, x, y = window_geometry(bcfg)
    try:
        return await pw.chromium.launch_persistent_context(
            user_data_dir=str(profile.dir),
            channel="chrome",
            headless=False,
            locale="ko-KR",
            no_viewport=True,
            ignore_default_args=["--enable-automation", "--use-mock-keychain"],
            args=[
                "--disable-blink-features=AutomationControlled",
                "--lang=ko-KR",
                "--no-first-run",
                "--no-default-browser-check",
                f"--window-size={w},{h}",
                f"--window-position={x},{y}",
            ],
        )
    except Exception as e:  # 이미 열려 있는 프로필
        msg = str(e)
        if "ProcessSingleton" in msg or "already in use" in msg or "SingletonLock" in msg:
            raise ProfileInUse(profile.name) from e
        raise
