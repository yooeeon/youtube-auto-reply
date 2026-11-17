"""최초 1회 로그인: 자동화 없이 '일반 크롬'을 전용 프로필 폴더로 연다.

구글은 자동화된 브라우저의 로그인을 막을 수 있어서, 로그인만 사람이 직접 하고
이후 Playwright가 같은 폴더(--user-data-dir)를 재사용한다.

  python -m scripts.login_profile 유튜브1
"""
from __future__ import annotations

import subprocess
import sys

from app.config import load_config

CHROME_APP = "Google Chrome"
LOGIN_URL = "https://accounts.google.com/ServiceLogin?hl=ko&continue=https://www.youtube.com/?hl=ko"


def main() -> int:
    cfg = load_config()
    name = sys.argv[1] if len(sys.argv) > 1 else cfg.profiles[0].name
    profile = cfg.profile(name)
    profile.dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["open", "-na", CHROME_APP, "--args",
         f"--user-data-dir={profile.dir}", "--no-first-run", "--no-default-browser-check", LOGIN_URL],
        check=True,
    )
    print(f"""
[{profile.name}] 크롬 창이 열렸어요. ({profile.dir})
  1) 이 프로필에 쓸 구글 계정으로 로그인하세요.
  2) 유튜브에서 오른쪽 위 프로필 → '채널 만들기'가 보이면 채널을 만드세요. (채널이 있어야 댓글 가능)
  3) 끝나면 이 크롬을 ⌘Q로 종료하세요. 따로 실행된 크롬이라 평소 쓰는 크롬 창은 영향받지 않아요.
  4) 확인: make check PROFILE={profile.name}
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
