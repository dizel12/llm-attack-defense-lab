"""
title: Memory Context Guard (S11)
author: dizel12
version: 0.1.0
required_open_webui_version: 0.5.0
description: system 메시지에 재주입되는 <memory_context> 블록만 골라 카나리아/의심
             패턴을 검사하고, 오염된 것으로 보이면 그 메모리 항목만 제거한다.
             (시나리오 2 v0.4 에서 처음 만든 <memory_context> 정화 로직을 독립 필터로 분리)
"""

# ---------------------------------------------------------------------------
# 왜 이 필터만으로는 "완전한" 해결이 아닌가 (소스로 확인한 사실)
#   OWUI utils/memory.py::_generate_memory_operations 는 `generate_chat_completion`
#   을 직접 호출한다 — 메인 채팅의 inlet/request/outlet Filter Functions 파이프라인을
#   전혀 거치지 않는다 (시나리오 12 의 task-model 우회와 동일한 근본 원인).
#   게다가 `review_memory_after_turn` 은 `ctx['assistant_message']` 를 읽는데, 이는
#   outlet 필터가 화면에 보여줄 답변을 정화하기 *이전* 시점의 원본이다
#   (middleware.py: outlet_filter_handler 가 먼저 실행되지만 ctx 자체는 갱신 안 함).
#   즉 "메모리에 무엇이 저장되는가"(write) 는 이 프로젝트의 Filter Functions 로
#   구조적으로 가로챌 수 없다.
#
#   그래서 이 필터가 하는 일은 "저장을 막는다"가 아니라
#   **"이미 오염된 메모리가 다음 대화들에 재주입될 때마다 매번 무력화한다"** (read-time)
#   — 시나리오 2 에서 검증된 것과 동일한 접근. 진짜 근본 방지는 애초에 오염된
#   답변이 나오지 않게 하는 것(시나리오 1/4 의 request 훅 입력 정화)이고, 이 필터는
#   그게 뚫렸을 때의 2차 방어선 + scan_memories.py(맨 아래 참고)가 3차 방어선.
# ---------------------------------------------------------------------------

import re
import unicodedata
from typing import Optional, Callable, Any
from pydantic import BaseModel, Field

_MEMCTX = re.compile(r"<memory_context>(.*?)</memory_context>", re.DOTALL | re.IGNORECASE)
_INVISIBLE = re.compile(
    "[​‌‍⁠﻿‪-‮⁦-⁩]"
    "|[\U000e0000-\U000e007f]"
)

_DEFAULT_SUSPICIOUS = (
    "반드시,항상,맨 마지막,마지막 줄,모든 답변,every answer,every reply,always end,"
    "always include,ignore previous,system:,system note"
)


def _normalize(text: str) -> str:
    text = _INVISIBLE.sub("", text)
    text = unicodedata.normalize("NFKC", text)
    return "".join(ch for ch in text if not unicodedata.category(ch).startswith("M"))


def _has_canary(text: str, canary: str) -> bool:
    norm = _normalize(text)
    loose = re.sub(r"\s+", "", norm)
    return canary in norm or canary in loose


class Filter:
    class Valves(BaseModel):
        priority: int = Field(default=0, description="실행 우선순위")
        canary: str = Field(default="김제윤", description="탐지용 카나리아")
        suspicious_patterns: str = Field(
            default=_DEFAULT_SUSPICIOUS,
            description="메모리 항목(한 줄)에 이 문구가 있으면 '행동 지시성 메모리'로 의심해 제거 (쉼표 구분)",
        )
        remove_whole_block_if_canary: bool = Field(
            default=True,
            description="카나리아가 있으면 memory_context 블록 전체를 제거(True) vs 해당 줄만 제거(False)",
        )
        debug: bool = Field(default=True, description="서버 로그 출력")

    def __init__(self):
        self.valves = self.Valves()

    def _suspicious(self) -> list[str]:
        return [p.strip().lower() for p in self.valves.suspicious_patterns.split(",") if p.strip()]

    def _clean_block(self, raw: str) -> tuple[str, list[str]]:
        """항목(줄) 단위로 제거한다 — 같은 블록의 다른(오염되지 않은) 메모리는 보존."""
        notes: list[str] = []
        kept = []
        any_item_removed = False

        for line in raw.split("\n"):
            low = _normalize(line).lower()
            is_item = line.strip().startswith("- ")  # memory_label 렌더링 형식 ("- path: content")

            if is_item and _has_canary(line, self.valves.canary):
                notes.append(f"canary-item-dropped: {line.strip()[:40]!r}")
                any_item_removed = True
                continue
            if is_item and any(p and p in low for p in self._suspicious()):
                notes.append(f"suspicious-item-dropped: {line.strip()[:40]!r}")
                any_item_removed = True
                continue
            kept.append(line)

        if not any_item_removed and _has_canary(raw, self.valves.canary):
            # 카나리아가 항목 형식이 아닌 곳(헤더 등)에 숨어있는 경우의 안전장치
            notes.append("memory-canary-found-non-item")
            if self.valves.remove_whole_block_if_canary:
                return "[정책 위반 메모리 제거됨]", notes

        return "\n".join(kept), notes

    def _scrub_system_message(self, content: str) -> tuple[str, list[str]]:
        all_notes: list[str] = []

        def repl(m: re.Match) -> str:
            cleaned, notes = self._clean_block(m.group(1))
            all_notes.extend(notes)
            return f"<memory_context>{cleaned}</memory_context>"

        return _MEMCTX.sub(repl, content), all_notes

    async def request(
        self,
        body: dict,
        __event_emitter__: Optional[Callable[[Any], Any]] = None,
        __user__: Optional[dict] = None,
    ) -> dict:
        all_notes: list[str] = []
        for msg in body.get("messages", []):
            if msg.get("role") != "system":
                continue
            content = msg.get("content")
            if isinstance(content, str) and "<memory_context>" in content:
                new, notes = self._scrub_system_message(content)
                msg["content"] = new
                all_notes += notes

        if self.valves.debug:
            print(f"[memory_guard_s11.request] notes={all_notes}")

        if all_notes and __event_emitter__:
            await __event_emitter__(
                {
                    "type": "status",
                    "data": {"description": f"🛡️ 오염 의심 메모리 정화 ({len(all_notes)}건)", "done": True},
                }
            )
        return body
