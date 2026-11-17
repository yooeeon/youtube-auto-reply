# CLAUDE.md

평가 100점을 목표로 하는 과제 프로젝트. 모든 작업은 `docs/EVALUATION_MAP.md`의 평가 항목에 기여해야 한다.

## 반드시 지킬 것
- 문서가 기준: 요구사항 `docs/PRD.md`, 구조 `docs/ARCHITECTURE.md`, 댓글 규칙 `docs/COMMENT_STRATEGY.md`, 게시 자동화 `docs/AUTOMATION.md`. 결정을 바꾸면 `docs/DECISIONS.md`를 갱신.
- 댓글 규칙(길이, 링크·해시태그·타임스탬프 금지, '운명원' 1회, 지어낸 후기 금지)은 `app/commenter/validate.py`의 결정적 코드로 검사한다. 프롬프트만 믿지 않는다.
- 댓글 페르소나는 '운명원 평판을 아는 조언자'(D-14): 장면 문제 → 판단 기준 → 연애사주 → 운명원(평판만). 이용 경험·팬 행세는 금지.
- 운영 규칙(영상당 1개, 하루 상한, 간격)은 DB 제약과 트랜잭션으로 강제한다. UI 비활성화는 보조 수단.
- 제출 버튼 클릭 이후 실패는 재시도하지 않는다 (중복 게시 방지).
- AI 호출은 `app/llm.py`의 `complete_json`만 쓴다 (구독 CLI 우선, API 키 있으면 API — D-15).
- 크롬 기본 프로필 폴더를 절대 자동화 대상으로 쓰지 않는다. `chrome-profiles/` 전용 폴더만.
- 유튜브 UI는 한국어(`hl=ko`) 기준. 셀렉터는 `app/poster/selectors.py`에만 둔다.
- 비밀값은 `.env`. `chrome-profiles/`, `data/`, `.env`는 커밋 금지.
- UI 문구는 한국어 존댓말, 실패/비활성 사유는 사람이 읽는 문장으로.

## 스택
Python 3.12 · FastAPI · Playwright(Python, channel="chrome") · anthropic SDK (`claude-opus-5-5`) · YouTube Data API v3 · youtube-transcript-api / yt-dlp · SQLite · 빌드 없는 HTML/JS
