PY := .venv/bin/python
PROFILE ?= 유튜브1

.PHONY: setup run login check post-test dry-run draft

setup:
	python3 -m venv .venv
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -r requirements.txt
	@test -f .env || cp .env.example .env
	@echo "✓ 설치 완료. 다음: make login PROFILE=유튜브1"

# 대시보드 실행 → 브라우저가 열려요 (http://127.0.0.1:8000)
run:
	$(PY) -m app

# 최초 1회: 자동화 없이 일반 크롬으로 열어 직접 구글 로그인 + 유튜브 채널 확인
login:
	$(PY) -m scripts.login_profile "$(PROFILE)"

# 로그인 상태 점검 (자동화 크롬으로 유튜브 접속 → 아바타 확인)
check:
	$(PY) -m app.poster.check "$(PROFILE)"

# M1 시험: 실제 게시 (규칙 적용, 기록 남음)
#   make post-test URL=https://youtu.be/xxxx TEXT="댓글 내용"
post-test:
	$(PY) -m app.poster.cli --url "$(URL)" --text "$(TEXT)" --profile "$(PROFILE)"

# M1 시험: 입력까지만 하고 게시하지 않음 (기록 안 남음)
dry-run:
	$(PY) -m app.poster.cli --url "$(URL)" --text "$(TEXT)" --profile "$(PROFILE)" --dry-run

# M3 시험: 영상 분석 → 댓글 후보 → 규칙 검사 → 심사 (게시 안 함)
#   make draft URL=https://youtu.be/xxxx
draft:
	$(PY) -m app.commenter.cli "$(URL)"
