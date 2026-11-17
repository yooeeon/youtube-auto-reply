"""config.yaml + .env 로드."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SCREENSHOT_DIR = DATA_DIR / "screenshots"
DB_PATH = DATA_DIR / "app.db"


@dataclass(frozen=True)
class Profile:
    name: str
    dir: Path

    @property
    def exists(self) -> bool:
        return self.dir.exists()


@dataclass(frozen=True)
class CommentCfg:
    min_chars: int = 40
    max_chars: int = 120
    brand: str = "운명원"


@dataclass(frozen=True)
class LimitsCfg:
    daily_max_comments: int = 10
    min_interval_sec: int = 60


@dataclass(frozen=True)
class TypingCfg:
    min_delay_ms: int = 70
    max_delay_ms: int = 190


@dataclass(frozen=True)
class BrowserCfg:
    window_size: tuple[int, int] = (1100, 1000)
    window_position: tuple[int, int] = (820, 0)
    keep_open_sec: float = 5
    layout: str = "right"  # right: 화면 오른쪽에 자동 배치 / fixed: window_size·position 그대로


@dataclass(frozen=True)
class Config:
    profiles: list[Profile]
    comment: CommentCfg
    limits: LimitsCfg
    typing: TypingCfg
    browser: BrowserCfg
    raw: dict = field(repr=False, default_factory=dict)
    youtube_api_key: str = ""
    anthropic_api_key: str = ""

    def profile(self, name: str) -> Profile:
        for p in self.profiles:
            if p.name == name:
                return p
        raise KeyError(f"설정에 없는 프로필이에요: {name}")


_cache: dict = {"key": None, "cfg": None}


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def load_config(path: Path | None = None) -> Config:
    """config.yaml·.env가 바뀌면 다시 읽는다 (서버 재시작 없이 상한 등 변경 반영)."""
    path = path or ROOT / "config.yaml"
    key = (str(path), _mtime(path), _mtime(ROOT / ".env"))
    if _cache["key"] == key:
        return _cache["cfg"]
    cfg = _load(path)
    _cache.update(key=key, cfg=cfg)
    return cfg


def _load(path: Path) -> Config:
    load_dotenv(ROOT / ".env", override=True)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    profiles = [
        Profile(name=p["name"], dir=(ROOT / p["dir"]).resolve())
        for p in raw.get("profiles", [])
    ]
    if not profiles:
        raise ValueError("config.yaml에 profiles가 비어 있어요.")
    b = raw.get("browser", {})
    return Config(
        profiles=profiles,
        comment=CommentCfg(**raw.get("comment", {})),
        limits=LimitsCfg(**raw.get("limits", {})),
        typing=TypingCfg(**raw.get("typing", {})),
        browser=BrowserCfg(
            window_size=tuple(b.get("window_size", (1100, 1000))),
            window_position=tuple(b.get("window_position", (820, 0))),
            keep_open_sec=b.get("keep_open_sec", 5),
            layout=b.get("layout", "right"),
        ),
        raw=raw,
        youtube_api_key=os.getenv("YOUTUBE_API_KEY", ""),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
    )
