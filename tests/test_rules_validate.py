"""운영 규칙·댓글 규칙 단위 테스트 (임시 DB 사용)."""
import sqlite3
from dataclasses import replace

import pytest

from app import rules, store
from app.commenter.validate import all_ok, validate
from app.config import CommentCfg, LimitsCfg, load_config

CFG = CommentCfg()


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "_conn", None)
    base = load_config()
    profiles = [replace(p, dir=tmp_path / f"p{i}") for i, p in enumerate(base.profiles)]
    for p in profiles:
        p.dir.mkdir()
    return replace(base, profiles=profiles, limits=LimitsCfg(daily_max_comments=3, min_interval_sec=0))


def ok_text(n=0):
    return f"바빠도 연락은 한다는 말에 뼈 맞음ㅠㅠ {n}일째 읽씹당하는 중인 1인… 운명원 연애사주 잘 본다던데 이 사람이랑 인연인지나 보고 싶다"


# ── 댓글 규칙 ──
def test_valid_comment_passes():
    assert all_ok(validate(ok_text(3), CFG))


@pytest.mark.parametrize("bad,key", [
    ("너무 짧아요 운명원 가볼까", "length"),
    (ok_text() + " https://bit.ly/abc", "no_link"),
    (ok_text().replace("뼈 맞음", "2:13 뼈 맞음"), "no_timestamp"),
    (ok_text() + " #연애", "no_hashtag"),
    (ok_text().replace("운명원", "그곳"), "brand"),
    (ok_text() + " 운명원", "brand"),
    ("운명원 " + ok_text().replace("운명원 ", ""), "brand"),
    (ok_text().replace("잘 본다던데", "가서 봤는데 소름"), "no_fake_review"),
    (ok_text().replace("잘 본다던데", "친구가 봤는데 맞았대"), "no_fake_review"),
])
def test_rule_violations(bad, key):
    failed = {c.key for c in validate(bad, CFG) if not c.ok}
    assert key in failed, failed


def test_example_from_brief_is_too_long_with_spaces():
    s = "근데 진짜 남자심리는 알다가도 모르겠다...ㅠㅠ 맨날 연애하면 차이기만 했던 1인. 결국 심리 알기는 개뿔 그냥 인연이면 만나는 것 같아요. 운명원 연애사주가 그렇게 잘 본다던데 나도 언제 짝 만나는지 한번 보고 싶다. 각자 정해진 운명이 있는 듯."
    assert "length" in {c.key for c in validate(s, CFG) if not c.ok}


def test_question_style_allowed():
    s = "고백은 확신을 줄 때 하는 거라는 말 너무 공감ㅠ 근데 그 확신이 언제 올지 모르겠다… 운명원 연애사주 본 분 계세요? 시기 잘 본다던데"
    assert all_ok(validate(s, CFG))


def test_similarity_check():
    checks = validate(ok_text(3), CFG, previous=[ok_text(4)])
    assert not all_ok(checks)


# ── 운영 규칙 ──
def test_one_comment_per_video(cfg):
    rules.reserve(cfg, video_id="AAAAAAAAAAA", comment_text="x")
    with pytest.raises(rules.RuleError) as e:
        rules.reserve(cfg, video_id="AAAAAAAAAAA", comment_text="y", profile=cfg.profiles[1].name)
    assert e.value.code == "ALREADY_COMMENTED"


def test_failed_attempt_frees_video_and_limit(cfg):
    r = rules.reserve(cfg, video_id="AAAAAAAAAAA", comment_text="x")
    store.update_attempt(r.attempt_id, status="failed")
    rules.reserve(cfg, video_id="AAAAAAAAAAA", comment_text="x")  # 재시도 가능


def test_unverified_blocks_video(cfg):
    r = rules.reserve(cfg, video_id="AAAAAAAAAAA", comment_text="x")
    store.update_attempt(r.attempt_id, status="unverified")
    with pytest.raises(rules.RuleError):
        rules.reserve(cfg, video_id="AAAAAAAAAAA", comment_text="x")


def test_daily_limit(cfg):
    for i in range(3):
        rules.reserve(cfg, video_id=f"VIDEO{i:06d}", comment_text="x")
    with pytest.raises(rules.RuleError) as e:
        rules.reserve(cfg, video_id="VIDEO999999", comment_text="x")
    assert e.value.code == "DAILY_LIMIT"
    assert rules.check_status(cfg)["blocked_reason"].startswith("오늘 상한")


def test_cooldown(cfg):
    c2 = replace(cfg, limits=LimitsCfg(daily_max_comments=10, min_interval_sec=60))
    rules.reserve(c2, video_id="AAAAAAAAAAA", comment_text="x")
    with pytest.raises(rules.RuleError) as e:
        rules.reserve(c2, video_id="BBBBBBBBBBB", comment_text="x")
    assert e.value.code == "COOLDOWN"


def test_round_robin_assignment(cfg):
    got = [rules.reserve(cfg, video_id=f"VIDEO{i:06d}", comment_text="x").profile for i in range(3)]
    assert got == [p.name for p in cfg.profiles]


def test_db_unique_index_is_last_line_of_defense(cfg):
    rules.reserve(cfg, video_id="AAAAAAAAAAA", comment_text="x")
    with pytest.raises(sqlite3.IntegrityError):
        store.connect().execute(
            "INSERT INTO attempts (video_id, profile, comment_text, status, started_at, kst_date) "
            "VALUES ('AAAAAAAAAAA','유튜브2','y','posted','2026-01-01T00:00:00+09:00','2026-01-01')")


def test_recover_interrupted(cfg):
    r = rules.reserve(cfg, video_id="AAAAAAAAAAA", comment_text="x")
    assert store.recover_interrupted() == 1
    assert store.get_attempt(r.attempt_id)["status"] == "unverified"
