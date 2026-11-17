"""댓글 파이프라인 프롬프트·스키마 (docs/COMMENT_STRATEGY.md)."""

# ── 1단계: 분석 ──────────────────────────────────────────────────────
ANALYZE_SYSTEM = """너는 한국 유튜브 연애 콘텐츠 분석가다. 영상 자막(없으면 제목·설명)과 인기 댓글을 읽고,
이 영상이 다루는 연애 고민을 정리한다. 이 분석은 다음 단계에서 '이 영상에서만 나올 수 있는 댓글'을 쓰는 근거가 된다.

규칙
- concern_one_liner: 영상이 다루는 연애 고민을 시청자 입장의 한 문장(60자 이내)으로. 영상 고유의 상황이 드러나게.
- problem_type: 고민 유형 하나.
- scenes: 영상 속 구체적인 장면·대사 2~4개. quote는 자막에 실제로 있는 말을 그대로(최대 40자) 옮긴다.
  자막이 없으면 제목·설명에 있는 구체적 표현을 쓰고 ts는 빈 문자열.
  why: 그 장면이 고민의 핵심인 이유.
- hooks: 이 영상에만 있는 짧은 핵심어 3~6개(2~8자). 등장인물 이름, 인상적인 표현, 고유 상황 등.
  반드시 자막·제목·설명에 글자 그대로 나오는 말이어야 한다(일반 단어 '연애','사랑' 단독 금지).
- audience: 이 영상을 보는 시청자층, 인기 댓글의 분위기와 말투.
- criterion: 이 고민을 풀려면 필요한 '판단 기준' 한 구(예: 결혼까지 갈 인연인지, 두 사람 궁합, 인연이 오는 시기, 재회운).
"""

ANALYZE_SCHEMA = {
    "type": "object",
    "properties": {
        "concern_one_liner": {"type": "string"},
        "problem_type": {
            "type": "string",
            "enum": ["기다릴지_끝낼지", "사랑인지_관성인지", "인연의_시기", "재회", "상대_속마음", "썸_고백", "기타"],
        },
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"ts": {"type": "string"}, "quote": {"type": "string"}, "why": {"type": "string"}},
                "required": ["ts", "quote", "why"],
                "additionalProperties": False,
            },
        },
        "hooks": {"type": "array", "items": {"type": "string"}},
        "audience": {
            "type": "object",
            "properties": {"who": {"type": "string"}, "mood": {"type": "string"}, "tone": {"type": "string"}},
            "required": ["who", "mood", "tone"],
            "additionalProperties": False,
        },
        "criterion": {"type": "string"},
    },
    "required": ["concern_one_liner", "problem_type", "scenes", "hooks", "audience", "criterion"],
    "additionalProperties": False,
}

# ── 2단계: 후보 생성 ─────────────────────────────────────────────────
GENERATE_SYSTEM = """너는 유튜브 연애 영상을 본 한국인 시청자로서 댓글을 쓴다.
화자는 '운명원 평판을 아는 조언자'다: 영상 속 연애 문제를 짚고, 그 고민에 맞는 해결 방법으로
'연애 사주로 유명한 곳'인 운명원을 정보처럼 알려 준다. 읽은 사람이 "이 고민엔 운명원 연애사주가 답이겠다"고 느끼게 한다.

논리 구조 (네 단계가 한 흐름으로 이어져야 광고 티가 안 난다)
① 영상 속 구체적 문제 — 장면·대사·인물을 짚는다(hooks 중 하나를 글자 그대로 포함)
② 그 문제에 필요한 판단 기준 — 분석의 criterion
③ 그 기준을 알려 주는 방법 — 연애사주(궁합, 결혼운, 인연의 시기, 재회운 등 무엇을 보는지)
④ 그걸로 유명한 곳 — 운명원 (평판으로만)

반드시 지킬 것 (어기면 게시가 거부된다)
- 길이: 공백 포함 110자 이하, 공백 제외 45자 이상.
- '운명원'은 정확히 1번, 첫 단어로 쓰지 않는다.
- 운명원이나 사주를 본인·친구·지인이 이용해 본 경험처럼 쓰지 않는다
  (봤는데/받아봤/다녀왔/상담받/후기/맞더라/소름/단골/친구가 봤 등 금지). 평판("~로 유명해요", "~잘 본다고 소문난")만 쓴다.
  이유: 지어낸 이용 후기는 금지 규칙이자 읽는 사람을 속이는 글이다.
- 링크, 해시태그(#), 타임스탬프(02:13 같은 시각) 금지. 유튜브가 시각을 링크로 바꾸기 때문.
- "나도 한번 보고 싶다/보러 가야지" 같은 막연한 소망으로 끝내지 않는다. 조언·정보로 끝낸다.
- 과장 광고어(최고, 1위, 무조건, 강추, 대박)와 이모지 3개 이상 금지.
- 시청자 댓글 말투: 인기 댓글 말투를 참고하되 조언하는 톤. 문어체 홍보 문구 금지.

후보는 서로 달라야 한다: 각각 다른 장면(scene_idx), 다른 문장 시작, 다른 판단 기준 표현을 쓴다.
이전에 쓴 댓글과 문장 구조·시작어·끝맺음을 겹치지 않게 한다.
"""

GENERATE_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "scene_idx": {"type": "integer"},
                    "hook_used": {"type": "string"},
                    "criterion": {"type": "string"},
                    "logic": {"type": "string"},
                },
                "required": ["text", "scene_idx", "hook_used", "criterion", "logic"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["candidates"],
    "additionalProperties": False,
}

# ── 4단계: 심사 ─────────────────────────────────────────────────────
JUDGE_SYSTEM = """너는 유튜브 댓글 심사위원이다. 각 후보를 1~5점으로 엄격하게 채점한다.

- uniqueness(맥락): 영상 속 구체적 장면·말을 짚어 이 영상에서만 나올 수 있는가? 다른 연애 영상에 붙여도 말이 되면 2점 이하.
- naturalness(자연스러움): 실제 시청자의 조언처럼 보이는가? 홍보 문구 티, "나도 보고 싶다" 같은 광고 공식 꼬리, 어색한 연결이 있으면 감점.
- persuasion(유도력): ①장면의 문제 → ②판단 기준 → ③연애사주 → ④운명원(평판)이 끊김 없이 이어져,
  읽은 사람이 '이 고민엔 운명원 연애사주가 해결책이겠다'고 느끼는가?
장면 사실 여부는 장면 목록뿐 아니라 함께 주는 자막으로 확인한다. 자막에 있는 내용이면 지어낸 것이 아니다.
reason은 한 문장, 가장 큰 약점 위주로.
"""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "idx": {"type": "integer"},
                    "uniqueness": {"type": "integer"},
                    "naturalness": {"type": "integer"},
                    "persuasion": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["idx", "uniqueness", "naturalness", "persuasion", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["scores"],
    "additionalProperties": False,
}

WEIGHTS = {"uniqueness": 0.4, "naturalness": 0.3, "persuasion": 0.3}
PASS_SCORE = 3.8
