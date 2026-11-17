# 아키텍처

## 1. 개요

```
 브라우저(대시보드, localhost:8000)
   │  REST (JSON)            ▲ SSE (진행 상태 스트림)
   ▼                         │
 FastAPI 서버 ───────────────┴──────────────────────────────────────┐
   ├─ youtube/      영상 검색·통계·인기댓글 (YouTube Data API v3)     │
   ├─ transcript/   자막 수집 (youtube-transcript-api → yt-dlp)       │
   ├─ commenter/    분석 → 후보 생성 → 규칙 검사 → AI 심사 (Claude)    │
   ├─ poster/       Playwright 게시 자동화 (launch_persistent_context)│
   ├─ rules/        영상당 1개 · 하루 상한 · 간격 · 락                 │
   └─ store/        SQLite (videos, analyses, drafts, attempts)      │
                                                                     │
 chrome-profiles/youtube1..3  ← 프로필 폴더(--user-data-dir)          │
 data/app.db, data/screenshots/                                      │
```

**스택**: Python 3.12 · FastAPI · Uvicorn · Playwright(Python, `channel="chrome"`) · Claude Code CLI(구독) 또는 anthropic SDK (D-15) · google-api-python-client · youtube-transcript-api · yt-dlp · SQLite(sqlite3/SQLModel) · 프론트엔드는 빌드 없는 단일 HTML + vanilla JS + CSS.

## 2. 디렉터리 구조 (계획)

```
youtube-auto-reply/
├─ app/
│  ├─ __main__.py          # 서버 실행 + 브라우저 자동 오픈
│  ├─ main.py              # FastAPI 라우트
│  ├─ config.py            # config.yaml + .env 로드/검증
│  ├─ youtube_api.py       # search, videos, commentThreads
│  ├─ transcript.py        # 자막 수집/정리(타임스탬프 유지)
│  ├─ commenter/
│  │  ├─ analyze.py        # 1단계: 고민 한 줄 + 근거 장면
│  │  ├─ generate.py       # 2단계: 후보 N개
│  │  ├─ validate.py       # 규칙 검사(결정적 코드)
│  │  ├─ judge.py          # AI 심사 + 선택
│  │  └─ prompts.py
│  ├─ poster/
│  │  ├─ runner.py         # 게시 상태 머신
│  │  ├─ selectors.py      # 유튜브 셀렉터(한국어 라벨 우선)
│  │  └─ profiles.py       # 프로필 열기/로그인 점검/자동 배정
│  ├─ rules.py             # 운영 규칙 강제
│  ├─ store.py             # DB 스키마/쿼리
│  └─ events.py            # SSE 브로커
├─ web/ index.html, app.js, style.css
├─ scripts/login_profile.py  # 최초 로그인(자동화 없이 크롬 실행)
├─ config.yaml, .env.example, Makefile, requirements.txt
├─ data/ (gitignore)  chrome-profiles/ (gitignore)
└─ docs/
```

## 3. 데이터 모델 (SQLite)

```sql
videos(
  video_id TEXT PRIMARY KEY, title, channel_title, channel_id, published_at,
  thumbnail_url, duration_sec, view_count, comment_count, description,
  source TEXT,              -- 'search' | 'link' | 'rising'
  views_per_hour REAL, fetched_at
)

analyses(                    -- 1단계 결과 (영상당 최신 1개)
  video_id PK, transcript_source TEXT,   -- 'manual_ko'|'auto_ko'|'yt_dlp'|'none'
  concern_one_liner TEXT, scenes_json TEXT, top_comments_json TEXT,
  audience_json TEXT, model TEXT, created_at
)

drafts(                      -- 2단계 후보 + 선택본 (재생성마다 누적)
  id PK, video_id, batch_id, text, persona, strategy, anchor_scene_idx,
  checks_json TEXT,          -- 규칙 검사 결과
  judge_json TEXT,           -- 점수/사유
  selected INTEGER, edited INTEGER, created_at
)

attempts(                    -- 게시 시도 기록 (append-only, 상태만 갱신)
  id PK, video_id, profile, draft_id, comment_text,
  status TEXT,               -- 'posting' | 'posted' | 'failed' | 'unverified'
  stage TEXT,                -- 마지막 도달 단계
  error_code TEXT, error_message TEXT, screenshot_path TEXT,
  comment_url TEXT, started_at, finished_at, kst_date TEXT
)
CREATE UNIQUE INDEX one_comment_per_video
  ON attempts(video_id) WHERE status IN ('posting','posted','unverified');
```

- `unverified`(게시 버튼은 눌렸으나 노출 확인 실패)도 유니크에 포함 → **중복 게시 방지가 우선**. 운영자가 확인 후 수동으로 `failed` 처리할 수 있는 버튼 제공.
- 하루 상한 계산: `SELECT COUNT(*) FROM attempts WHERE kst_date=? AND status IN ('posting','posted','unverified')`.

## 4. API

| Method | Path | 설명 |
|---|---|---|
| GET | `/api/status` | 프로필 상태, 오늘 사용량, 쿨다운, 키 설정 여부 |
| POST | `/api/videos/load` | 최신 영상 불러오기 |
| POST | `/api/videos/rising` | 뜨기 직전 영상 찾기 |
| POST | `/api/videos/link` | `{url}` 링크 직접 추가 |
| GET | `/api/videos` | 현재 목록 (+ 댓글 완료 여부) |
| POST | `/api/videos/{id}/draft` | 분석+초안 생성 (캐시 있으면 반환, `?regenerate=1` 재생성) |
| POST | `/api/drafts/validate` | 수정한 초안 규칙 검사 |
| POST | `/api/post` | `{video_id, draft_text, profile|auto}` 게시 시작 → `attempt_id` |
| GET | `/api/post/{attempt_id}/events` | SSE 진행 상태 |
| GET | `/api/attempts` | 기록 (시간순) |
| POST | `/api/profiles/{name}/login` | 최초 로그인용 크롬 열기 |

## 5. 게시 처리 순서 (규칙 강제 지점)

```
POST /api/post
 1) 규칙 검사(텍스트) 재실행 → 실패면 400
 2) BEGIN IMMEDIATE 트랜잭션
    - 이 영상에 posting/posted/unverified 있나? → 409 "이미 댓글 단 영상"
    - 오늘 건수 ≥ 상한? → 429 "오늘 상한 도달"
    - 마지막 성공 후 min_interval 미만? → 429 "N초 후 가능"
    - 프로필 결정(자동 배정) → attempts INSERT status='posting'
    COMMIT   ← 유니크 인덱스가 최후 방어선
 3) 전역 게시 락 획득 → poster.runner 백그라운드 실행
 4) 단계마다 attempts.stage 갱신 + SSE 발행
 5) 종료: posted(comment_url, 캡처) | failed(error_code, 메시지, 캡처) | unverified
```

## 6. UI 설계 원칙 (사용성 10점)

1. **번호가 붙은 세 칸**: `① 영상 불러오기 → ② 영상 선택 → ③ 댓글 달기`. 현재 해야 할 칸만 강조, 나머지는 흐리게.
2. **빈 상태 안내**: 각 칸이 비어 있을 때 다음 행동을 한 문장으로 ("왼쪽에서 영상을 고르면 AI가 댓글 초안을 만들어요").
3. **비활성 버튼엔 반드시 이유**: 버튼 아래 회색 문장 (상한·중복·쿨다운·로그인 필요).
4. **진행 상태는 체크리스트형 스테퍼** + 입력 중엔 `54/87자` 진행률.
5. **실패는 사람 말로**: `댓글창을 찾지 못했어요 (댓글이 꺼진 영상일 수 있어요)` + 캡처 썸네일 클릭 확대.
6. **상단 상태 바**: 프로필별 로그인 ✓/⚠, 오늘 `3/10`, API 키 설정 여부. 첫 실행 시 설정 체크리스트.
7. 초안 검사 배지는 초록/빨강 칩으로 **왜 통과/실패인지** 노출 → 평가자가 규칙 준수를 눈으로 확인.

## 7. 외부 의존성 및 할당량

| 호출 | 비용 | 빈도 |
|---|---|---|
| `search.list` | 100 units | 불러오기 1회당 키워드 수만큼(기본 1~2회로 묶음) |
| `videos.list` (최대 50개) | 1 unit | 불러오기당 1 |
| `commentThreads.list` | 1 unit | 영상 선택당 1 |
| Claude 분석 | 1 call | 영상 선택당 1 (캐시) |
| Claude 생성+심사 | 1~2 call | 초안 생성/재생성당 |

기본 할당량 10,000 units/일 → 하루 수십 번 불러와도 충분.
