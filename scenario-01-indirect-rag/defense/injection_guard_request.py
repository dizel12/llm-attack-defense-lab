"""
title: Injection Guard (Request)
author: dizel12
version: 0.2.0
required_open_webui_version: 0.5.0
description: RAG 컨텍스트가 주입된 뒤(모델 호출 직전) 메시지를 줄 단위로 검사해 프롬프트 인젝션 의심 줄을 삭제한다.
"""

# ---------------------------------------------------------------------------
# 왜 inlet 이 아니라 request 인가 (OWUI v0.11.x 기준)
#   middleware.py 실행 순서:
#     2635  filter_type='inlet'      <- RAG 주입 전. 유저 질문만 보임 → 못 지움
#     3077  apply_source_context...  <- 여기서 문서(숨긴 지시문 포함)가 user 메시지에 주입
#     3104  filter_type='request'    <- RAG 주입 후. 이 훅에서 지워야 실제로 막힘
#     3984  filter_type='outlet'     <- 모델 응답 후
#   훅 메서드 이름은 filter_type 과 문자 그대로 매칭됨 (filter.py: getattr(module, filter_type))
#
# 설치: OWUI > Admin Panel > Functions > [+] > 붙여넣기 > Save > Enable
#       기존 "Injection Guard (Inlet)" 함수는 삭제하거나 Disable.
#       self.toggle = True 라 채팅 입력창에서 켜고/끌 수 있음(실험 A/B).
# ---------------------------------------------------------------------------

from typing import Optional, Callable, Any
from pydantic import BaseModel, Field


def _filter_text(text: str, patterns: list[str]) -> tuple[str, int, list[str]]:
    kept: list[str] = []
    dropped: list[str] = []
    for line in text.split("\n"):
        low = line.lower()
        if any(p and p in low for p in patterns):
            dropped.append(line)
        else:
            kept.append(line)
    return "\n".join(kept), len(dropped), dropped


class Filter:
    class Valves(BaseModel):
        priority: int = Field(default=0, description="필터 실행 우선순위 (낮을수록 먼저)")
        patterns: str = Field(
            default="반드시,지침,system note,system:,무시하고,무시하라,ignore previous,ignore all previous,you must,always end,end your reply,exact line,다음 지시,아래 지시,마지막 줄에,맨 마지막,김제윤",
            description="이 문자열이 포함된 줄을 삭제한다 (쉼표 구분, 소문자 비교)",
        )
        scan_roles: str = Field(
            default="system,user,tool",
            description="검사할 메시지 role (쉼표 구분). assistant 는 기본 제외",
        )
        also_run_on_inlet: bool = Field(
            default=True,
            description="RAG 없이 채팅에 직접 붙인 인젝션도 잡도록 inlet 단계에서도 한 번 검사",
        )
        debug: bool = Field(default=True, description="서버 로그에 삭제 내역 출력")

    def __init__(self):
        self.valves = self.Valves()
        self.toggle = True

    def _patterns(self) -> list[str]:
        return [p.strip().lower() for p in self.valves.patterns.split(",") if p.strip()]

    def _roles(self) -> set[str]:
        return {r.strip().lower() for r in self.valves.scan_roles.split(",") if r.strip()}

    def _scrub(self, body: dict, stage: str) -> tuple[dict, int]:
        patterns = self._patterns()
        roles = self._roles()
        total = 0
        dumped: list[str] = []

        for msg in body.get("messages", []):
            if msg.get("role", "").lower() not in roles:
                continue
            content = msg.get("content")

            if isinstance(content, str):
                new, n, dropped = _filter_text(content, patterns)
                if n:
                    msg["content"] = new
                    total += n
                    dumped += dropped
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        new, n, dropped = _filter_text(part.get("text", ""), patterns)
                        if n:
                            part["text"] = new
                            total += n
                            dumped += dropped

        if self.valves.debug:
            preview = " | ".join(
                f'{m.get("role")}:{(m.get("content") if isinstance(m.get("content"), str) else "[parts]")[:80]!r}'
                for m in body.get("messages", [])
            )
            print(f"[injection_guard.{stage}] msgs -> {preview}")
            if total:
                print(f"[injection_guard.{stage}] 인젝션 의심 {total}줄 삭제:")
                for d in dumped:
                    print(f"    - {d!r}")
        return body, total

    async def _emit(self, emitter, total: int, stage: str):
        if emitter and total:
            await emitter(
                {
                    "type": "status",
                    "data": {
                        "description": f"🛡️ 인젝션 의심 {total}줄 제거 ({stage})",
                        "done": True,
                    },
                }
            )

    # RAG 주입 전 — 채팅에 직접 심은 인젝션만 잡힌다 (옵션)
    async def inlet(
        self,
        body: dict,
        __event_emitter__: Optional[Callable[[Any], Any]] = None,
        __user__: Optional[dict] = None,
    ) -> dict:
        if not self.valves.also_run_on_inlet:
            return body
        body, total = self._scrub(body, "inlet")
        await self._emit(__event_emitter__, total, "inlet")
        return body

    # RAG 주입 후, 모델 호출 직전 — 여기가 실제 방어 지점
    async def request(
        self,
        body: dict,
        __event_emitter__: Optional[Callable[[Any], Any]] = None,
        __user__: Optional[dict] = None,
    ) -> dict:
        body, total = self._scrub(body, "request")
        await self._emit(__event_emitter__, total, "request")
        return body
