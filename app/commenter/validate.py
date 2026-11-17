"""댓글 규칙 검사 — 결정적 코드. 프롬프트만 믿지 않는다 (docs/COMMENT_STRATEGY.md §2).

M1에서는 텍스트 자체 규칙만 쓰고, 장면 언급(scenes/hooks)·중복(previous) 검사는
M3 생성 파이프라인에서 인자를 넘겨 활성화한다.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from ..config import CommentCfg

URL_RE = re.compile(
    r"(https?://|www\.|\b[\w-]+\.(com|net|org|kr|co|io|me|ly|gl|be|link|xyz|shop)\b)", re.I
)
HASHTAG_RE = re.compile(r"#\S")
# 유튜브는 댓글 속 2:13 같은 시각을 클릭 가능한 링크로 바꾼다 → 금지 (D-11)
TIMESTAMP_RE = re.compile(r"\b\d{1,2}:\d{2}\b")
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
# 운명원 주변의 1인칭/지인 경험 표현 = 지어낸 이용 후기 (D-12)
FAKE_REVIEW_PATTERNS = [
    "봤는데", "봤어요", "봤었", "봤더니", "봤다", "받아봤", "받아 봤", "보고 왔", "보고왔",
    "다녀왔", "다녀 왔", "갔다 왔", "갔다왔", "상담받", "상담 받", "후기", "맞더라", "맞았",
    "맞췄", "소름", "진짜 맞", "용하더라", "추천받", "친구가", "지인이", "언니가", "동생이",
]
REVIEW_WINDOW = 25  # 브랜드 앞뒤 글자 수
HYPE_WORDS = ["최고", "1위", "무조건", "강추", "대박"]


@dataclass
class Check:
    key: str
    label: str
    ok: bool
    detail: str = ""


def char_counts(text: str) -> tuple[int, int]:
    """(공백 포함, 공백 제외)"""
    return len(text), len(re.sub(r"\s", "", text))


def _ngrams(s: str, n: int = 3) -> set[str]:
    s = re.sub(r"\s", "", s)
    return {s[i : i + n] for i in range(max(0, len(s) - n + 1))}


def similarity(a: str, b: str) -> float:
    x, y = _ngrams(a), _ngrams(b)
    return len(x & y) / len(x | y) if x and y else 0.0


def validate(
    text: str,
    cfg: CommentCfg,
    *,
    scene_terms: list[str] | None = None,
    previous: list[str] | None = None,
    max_similarity: float = 0.35,
) -> list[Check]:
    text = text.strip()
    with_sp, no_sp = char_counts(text)
    brand = cfg.brand
    checks: list[Check] = []

    # 길이: 공백 제외 ≥ min AND 공백 포함 ≤ max (D-03)
    checks.append(Check(
        "length", f"{cfg.min_chars}~{cfg.max_chars}자",
        no_sp >= cfg.min_chars and with_sp <= cfg.max_chars,
        f"공백 포함 {with_sp}자 · 공백 제외 {no_sp}자",
    ))
    m = URL_RE.search(text)
    checks.append(Check("no_link", "링크 없음", m is None, f"'{m.group(0)}' 발견" if m else ""))
    m = TIMESTAMP_RE.search(text)
    checks.append(Check("no_timestamp", "시간표시 없음", m is None,
                        f"'{m.group(0)}'는 유튜브에서 링크가 돼요" if m else ""))
    m = HASHTAG_RE.search(text)
    checks.append(Check("no_hashtag", "해시태그 없음", m is None, f"'{m.group(0)}' 발견" if m else ""))

    n_brand = text.count(brand)
    first_word = text.split()[0] if text.split() else ""
    brand_ok = n_brand == 1 and not first_word.startswith(brand)
    checks.append(Check(
        "brand", f"{brand} 1회", brand_ok,
        "" if brand_ok else (f"{n_brand}회 등장" if n_brand != 1 else "첫 단어로 쓰면 광고처럼 보여요"),
    ))

    hits: list[str] = []
    for mm in re.finditer(re.escape(brand), text):
        window = text[max(0, mm.start() - REVIEW_WINDOW) : mm.end() + REVIEW_WINDOW]
        hits += [p for p in FAKE_REVIEW_PATTERNS if p in window]
    checks.append(Check("no_fake_review", "이용 후기 아님", not hits,
                        f"경험담으로 읽힐 수 있는 표현: {', '.join(sorted(set(hits)))}" if hits else ""))

    emoji_n = len(EMOJI_RE.findall(text))
    hype = [w for w in HYPE_WORDS if w in text]
    checks.append(Check("tone", "광고 말투 아님", emoji_n <= 2 and not hype,
                        ", ".join(filter(None, [f"이모지 {emoji_n}개" if emoji_n > 2 else "",
                                                f"과장어: {', '.join(hype)}" if hype else ""]))))

    if scene_terms is not None:
        found = [t for t in scene_terms if t and t in text]
        checks.append(Check("scene", "영상 장면 언급", bool(found),
                            f"언급: {found[0]}" if found else "영상 속 장면·말이 들어가야 해요"))
    if previous:
        worst = max(previous, key=lambda p: similarity(text, p))
        sim = similarity(text, worst)
        checks.append(Check("unique", "이전 댓글과 다름", sim < max_similarity, f"최대 유사도 {sim:.2f}"))
    return checks


def all_ok(checks: list[Check]) -> bool:
    return all(c.ok for c in checks)


def to_dicts(checks: list[Check]) -> list[dict]:
    return [asdict(c) for c in checks]


def failures_text(checks: list[Check]) -> str:
    return " / ".join(f"{c.label}: {c.detail}" for c in checks if not c.ok)
