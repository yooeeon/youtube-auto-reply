# 게시 자동화 설계 (실제 게시 25점)

## 1. 크롬 프로필 방식

- 크롬은 **기본 사용자 데이터 폴더**의 원격 제어(자동화)를 막는다. 그래서 프로젝트 안에 **전용 프로필 폴더**를 만든다.
  `chrome-profiles/youtube1`, `youtube2`, `youtube3` — 각 폴더가 하나의 크롬 프로필(=구글 계정 1개)이다.
- 연결: `playwright.chromium.launch_persistent_context(user_data_dir, channel="chrome", headless=False, locale="ko-KR", viewport=None, args=[...])`
  - `channel="chrome"`: 설치된 실제 Google Chrome 사용(번들 Chromium보다 구글 로그인 호환성이 좋음).
  - `ignore_default_args=["--enable-automation"]`, `args=["--disable-blink-features=AutomationControlled", "--lang=ko-KR", "--start-maximized"]`.

### 최초 로그인 (1회)
구글은 자동화 브라우저의 로그인을 막는 경우가 있으므로, **로그인만큼은 자동화 없이** 한다.
```
make login PROFILE=youtube1
# = open -na "Google Chrome" --args --user-data-dir=$PWD/chrome-profiles/youtube1 https://accounts.google.com
```
사용자가 직접 로그인 → 유튜브 접속 → (채널이 없으면) 채널 만들기 → 창 닫기. 이후 Playwright가 같은 폴더를 재사용하므로 로그인 상태가 유지된다.

### 프로필 상태 점검
대시보드 시작 시(그리고 [다시 확인] 클릭 시) 각 프로필 폴더를 열지 않고 판단하기는 어려우므로:
- 폴더 존재 여부 + 마지막 점검 결과 캐시를 표시.
- [점검] 시 headless가 아닌 짧은 세션으로 `youtube.com` 접속 → 아바타 버튼(`#avatar-btn`) 유무로 로그인 판단, 채널 유무는 댓글 입력창 클릭 시 채널 생성 팝업 여부로 판단.

## 2. 게시 상태 머신

| 단계(stage) | 대시보드 문구 | 동작 | 타임아웃 |
|---|---|---|---|
| `opening_profile` | 프로필 여는 중 | persistent context 실행. 다른 프로세스가 폴더를 쓰고 있으면 실패(`PROFILE_IN_USE`) | 30s |
| `opening_video` | 영상 여는 중 | `https://www.youtube.com/watch?v={id}&hl=ko&gl=KR` 이동, 로그인 확인(`NOT_LOGGED_IN`) | 30s |
| `opening_comments` | 댓글 창 여는 중 | 영상 일시정지 → 댓글 섹션까지 스크롤(지연 로딩) → `ytd-comments` 로드 대기. 댓글 사용 중지 문구 감지(`COMMENTS_DISABLED`) | 30s |
| `focusing_input` | 댓글 입력창 여는 중 | 플레이스홀더 "댓글 추가..." 클릭 → `#contenteditable-root` 활성. 채널 생성 팝업 감지(`NO_CHANNEL`) | 15s |
| `typing` | 입력 중 (n/N자) | 한 글자씩 `keyboard.type(ch)` + 70~190ms 랜덤 지연, 문장부호 뒤 약간 더 쉼. 10자마다 진행률 SSE | 글자수×0.3s |
| `submitting` | 게시 중 | 입력값이 원문과 같은지 검증 후 "댓글" 제출 버튼 클릭(`#submit-button`) | 15s |
| `verifying` | 확인 중 | 댓글 목록 최상단의 내 댓글(작성자=내 채널, 본문 일치) 노출 대기 → 타임스탬프 링크에서 `lc=` 댓글 ID 추출 | 20s |
| `posted` | 게시 완료 · 댓글 보기 | 성공 캡처 저장, `comment_url` 기록. 창은 3초 유지 후 닫기(시연용 옵션: 열어 두기) | — |
| `failed` | 실패: {사유} | 캡처 저장 + 에러 코드/메시지 기록 | — |

- 실패 시 같은 단계 **1회 재시도**(네트워크·로딩 계열만). 제출 버튼 클릭 이후의 실패는 **재시도하지 않음**(중복 게시 위험) → `unverified`로 기록.
- 확인 2차 방법: 정렬을 "최신순"으로 바꾼 뒤 재확인, 또는 페이지 새로고침 후 재확인.

## 3. 셀렉터 전략 (한국어 UI)

`selectors.py` 한 곳에 모으고, 각 요소마다 **우선순위 목록**을 둔다. 하나라도 맞으면 사용.

| 요소 | 1순위 (접근성/한국어) | 2순위 (id/태그) |
|---|---|---|
| 댓글 섹션 | — | `ytd-comments#comments` |
| 입력 플레이스홀더 | `get_by_text("댓글 추가...")` | `#simplebox-placeholder` |
| 입력창 | `get_by_role("textbox")` within `ytd-commentbox` | `#contenteditable-root` |
| 제출 버튼 | `get_by_role("button", name="댓글")` within commentbox | `#submit-button button` |
| 내 댓글 | `ytd-comment-thread-renderer` 중 본문 일치 | `#content-text` 텍스트 비교 |
| 댓글 꺼짐 | `get_by_text("댓글이 사용 중지")` | `ytd-message-renderer` |

- URL에 `hl=ko`를 붙여 UI 언어를 고정한다(계정 언어 설정과 무관하게 한국어).
- 문자열 비교는 공백·줄바꿈 정규화 후 수행.

## 4. 실패 코드

| 코드 | 사용자 문구 |
|---|---|
| `PROFILE_MISSING` | 프로필 폴더가 없어요. [로그인 하기]로 먼저 만들어 주세요. |
| `PROFILE_IN_USE` | 이 프로필 크롬 창이 이미 열려 있어요. 닫고 다시 시도해 주세요. |
| `NOT_LOGGED_IN` | 이 프로필은 구글 로그인이 안 돼 있어요. |
| `NO_CHANNEL` | 이 계정엔 유튜브 채널이 없어 댓글을 달 수 없어요. |
| `COMMENTS_DISABLED` | 이 영상은 댓글이 꺼져 있어요. |
| `INPUT_NOT_FOUND` | 댓글 입력창을 찾지 못했어요. 캡처를 확인해 주세요. |
| `TEXT_MISMATCH` | 입력된 내용이 초안과 달라 게시를 멈췄어요. |
| `SUBMIT_FAILED` | 게시 버튼을 누르지 못했어요. |
| `NOT_VISIBLE` | 게시는 했지만 내 댓글이 보이지 않아요 (스팸 보류 가능성). → `unverified` |
| `TIMEOUT` | {단계}에서 시간이 초과됐어요. |

모든 실패는 `data/screenshots/{attempt_id}_{stage}.png` 전체 화면 캡처를 남긴다.

## 5. 안전장치
- 전역 락: 동시에 브라우저 1개만.
- 입력 직전 텍스트 재검사(규칙) + 입력 직후 DOM 값 비교.
- 시연 모드 옵션 `demo.keep_open: true` — 게시 후 창을 닫지 않아 평가자가 확인 장면을 볼 수 있게 함.
