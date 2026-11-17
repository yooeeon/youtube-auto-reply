"""AI 호출 계층 — JSON 스키마로 구조화 출력을 받는다.

backend (config.yaml llm.backend):
  - auto (기본): ANTHROPIC_API_KEY가 있으면 api, 없으면 cli
  - cli: 이 컴퓨터에 로그인된 Claude Code CLI(`claude -p`)를 호출 → Claude 구독 사용량으로 처리
  - api: Anthropic API (크레딧 결제)

CLI는 기본 Claude Code 지시문(수만 토큰) 대신 우리 지시문만 쓰고(--system-prompt),
도구·MCP·설정·세션 저장을 모두 꺼서 한 번 호출을 ~1.5k 토큰 수준으로 가볍게 만든다.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass

from .config import Config

API_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(Exception):
    pass


@dataclass
class LLMResult:
    data: dict
    backend: str
    model: str
    duration_ms: int


def resolve_backend(cfg: Config) -> str:
    want = (cfg.raw.get("llm", {}) or {}).get("backend", "auto")
    if want == "auto":
        if cfg.anthropic_api_key:
            return "api"
        if shutil.which("claude"):
            return "cli"
        raise LLMError("AI를 쓸 수 없어요. Claude Code에 로그인하거나 .env에 ANTHROPIC_API_KEY를 넣어 주세요.")
    if want == "cli" and not shutil.which("claude"):
        raise LLMError("Claude Code CLI(claude)를 찾을 수 없어요.")
    if want == "api" and not cfg.anthropic_api_key:
        raise LLMError(".env에 ANTHROPIC_API_KEY가 없어요.")
    return want


def model_name(cfg: Config) -> str:
    return (cfg.raw.get("llm", {}) or {}).get("model", "claude-opus-5-5")


async def complete_json(
    cfg: Config, *, system: str, user: str, schema: dict, effort: str = "medium", timeout: float = 240
) -> LLMResult:
    backend = resolve_backend(cfg)
    model = model_name(cfg)
    t0 = time.monotonic()
    if backend == "cli":
        data = await _cli(system, user, schema, model, effort, timeout)
    else:
        data = await _api(cfg, system, user, schema, model, effort, timeout)
    return LLMResult(data, backend, model, int((time.monotonic() - t0) * 1000))


async def _cli(system: str, user: str, schema: dict, model: str, effort: str, timeout: float) -> dict:
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)  # 구독 로그인으로 처리되게
    with tempfile.TemporaryDirectory(prefix="yt-llm-") as cwd:  # 프로젝트 CLAUDE.md 등을 읽지 않도록
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p",
            "--output-format", "json",
            "--json-schema", json.dumps(schema, ensure_ascii=False),
            "--system-prompt", system,
            "--tools", "",
            "--strict-mcp-config",
            "--setting-sources", "",
            "--disable-slash-commands",
            "--no-session-persistence",
            "--model", model,
            "--effort", effort,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(user.encode()), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise LLMError("AI 응답이 너무 오래 걸려 중단했어요.")
    try:
        d = json.loads(out.decode() or "{}")
    except json.JSONDecodeError:
        raise LLMError(f"Claude Code 응답을 읽지 못했어요: {(out or err).decode()[:300]}")
    if d.get("is_error") or d.get("structured_output") is None:
        raise LLMError(f"Claude Code 오류: {str(d.get('result') or err.decode())[:300]}")
    return d["structured_output"]


async def _api(cfg: Config, system: str, user: str, schema: dict, model: str, effort: str, timeout: float) -> dict:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=cfg.anthropic_api_key, timeout=timeout)
    try:
        resp = await client.beta.messages.create(
            model=model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            betas=[API_FALLBACK_BETA],
            fallbacks="default",  # 안전 분류기 오탐 시 서버가 다른 모델로 이어서 처리
        )
    except anthropic.RateLimitError as e:
        raise LLMError("AI 요청이 많아 잠시 막혔어요. 잠시 후 다시 시도해 주세요.") from e
    except anthropic.AuthenticationError as e:
        raise LLMError("ANTHROPIC_API_KEY가 올바르지 않아요.") from e
    except anthropic.APIStatusError as e:
        raise LLMError(f"AI 서버 오류({e.status_code}): {str(e.message)[:200]}") from e
    except anthropic.APIConnectionError as e:
        raise LLMError("AI 서버에 연결하지 못했어요. 인터넷 연결을 확인해 주세요.") from e
    if resp.stop_reason == "refusal":
        raise LLMError("AI가 이 요청을 거절했어요.")
    if resp.stop_reason == "max_tokens":
        raise LLMError("AI 응답이 길이 제한에 걸렸어요.")
    text = next((b.text for b in resp.content if b.type == "text"), None)
    if not text:
        raise LLMError("AI 응답이 비어 있어요.")
    return json.loads(text)
