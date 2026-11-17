"""SSE용 간단한 이벤트 브로커. 늦게 구독해도 지금까지의 이벤트를 다시 받는다."""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict


class Broker:
    def __init__(self) -> None:
        self.history: dict[str, list[dict]] = defaultdict(list)
        self.subs: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self.closed: set[str] = set()

    def publish(self, key: str, event: dict, final: bool = False) -> None:
        # 타이핑 진행은 마지막 것만 기록해 재생 길이를 줄인다
        h = self.history[key]
        if h and event.get("stage") == "typing" and h[-1].get("stage") == "typing":
            h[-1] = event
        else:
            h.append(event)
        for q in self.subs[key]:
            q.put_nowait(event)
        if final:
            self.closed.add(key)
            for q in self.subs[key]:
                q.put_nowait(None)

    async def stream(self, key: str):
        q: asyncio.Queue = asyncio.Queue()
        for ev in self.history.get(key, []):
            yield sse(ev)
        if key in self.closed:
            return
        self.subs[key].append(q)
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                if ev is None:
                    return
                yield sse(ev)
        finally:
            self.subs[key].remove(q)


def sse(ev: dict) -> str:
    return f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"


broker = Broker()
