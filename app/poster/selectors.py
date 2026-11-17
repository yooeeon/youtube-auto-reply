"""유튜브 셀렉터 — 이 파일에만 둔다. (한국어 UI, URL에 hl=ko 고정)

각 요소는 쉼표로 묶은 CSS 대체 목록: 하나라도 맞으면 사용된다.
유튜브 DOM이 바뀌면 여기만 고친다. 실패 캡처(data/screenshots)로 원인을 확인.
"""

WATCH_URL = "https://www.youtube.com/watch?v={video_id}&hl=ko&gl=KR"
COMMENT_URL = "https://www.youtube.com/watch?v={video_id}&lc={comment_id}"

WATCH_ROOT = "ytd-watch-flexy"

# 로그인 여부 (상단 바)
AVATAR = "ytd-masthead #avatar-btn, ytd-masthead button#avatar-btn, ytd-topbar-menu-button-renderer #avatar-btn"
SIGN_IN = (
    "ytd-masthead a[href*='ServiceLogin'], ytd-masthead a[href*='accounts.google.com'], "
    "ytd-masthead ytd-button-renderer a[aria-label='로그인']"
)

# 댓글 영역
COMMENTS_SECTION = "ytd-comments#comments"
COMMENTS_DISABLED = "ytd-comments ytd-message-renderer, ytd-item-section-renderer#sections ytd-message-renderer"
COMMENTS_DISABLED_TEXT = r"댓글.*(사용 중지|사용이 중지|사용할 수 없|꺼)"

# 입력
PLACEHOLDER = "ytd-comments ytd-comment-simplebox-renderer #simplebox-placeholder, ytd-comments #placeholder-area"
INPUT = "ytd-comments ytd-commentbox #contenteditable-root, ytd-comments #contenteditable-root[contenteditable='true']"
SUBMIT = "ytd-comments ytd-commentbox #submit-button button, ytd-comments ytd-commentbox #submit-button"

# 채널 없음 / 로그인 유도 팝업
CHANNEL_DIALOG = "ytd-channel-creation-dialog-renderer, tp-yt-paper-dialog:has-text('채널 만들기'), tp-yt-paper-dialog:has-text('채널을 만들')"
SIGNIN_DIALOG = (
    "ytd-modal-with-title-and-button-renderer:has-text('로그인'), tp-yt-paper-dialog:has-text('로그인'), "
    "tp-yt-iron-dropdown:has-text('대화에 참여하고 싶으신가요'), ytd-popup-container :text('계속하려면 로그인하세요')"
)

# 확인
THREAD = "ytd-comment-thread-renderer"
THREAD_TEXT = "#content-text"
COMMENT_LINK = "a[href*='lc=']"
