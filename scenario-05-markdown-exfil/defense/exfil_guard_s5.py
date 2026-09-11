"""
title: Markdown Exfil Guard (S5)
author: dizel12
version: 0.1.0
required_open_webui_version: 0.5.0
description: 답변에 포함된 마크다운 이미지/HTML img/외부 링크를 검사해, 브라우저가 자동으로
             불러오는(=클릭 없이 GET 요청이 나가는) 이미지는 기본적으로 전부 제거하고,
             링크는 허용 도메인이 아니면 하이퍼링크만 제거(텍스트는 보존)한다.
"""

# ---------------------------------------------------------------------------
# 왜 outlet 인가 (LLM05 Improper Output Handling)
#   이 공격은 "모델이 뭘 하게 만드느냐"가 아니라 "모델이 만들어낸 출력을 OWUI가
#   어떻게 렌더링하느냐"의 문제다. 사용자가 클릭하지 않아도 마크다운 이미지
#   `![](url)` 나 raw `<img src=url>` 는 채팅 화면에 렌더링되는 순간 브라우저가
#   자동으로 그 URL 에 GET 요청을 보낸다 -> 쿼리스트링에 실어보낸 데이터가
#   조용히 외부(attacker.example)로 유출된다. 사용자 눈에는 깨진 이미지 아이콘
#   정도로만 보일 수 있다.
#   input(request)측에서 아무리 지시문을 걸러도 모델이 이미 유출할 값(카나리아,
#   시스템 프롬프트 비밀, 대화 요약 등)을 "정상적인 답변"으로 알고 있다면 막을
#   방법이 없다 -> 마지막 방어선은 "출력에 그런 URL이 나타나면 렌더링 자체를
#   막는다" = outlet.
#
# 기본 정책
#   - 이미지(`![alt](url)`, `<img src=url>`) : http/https 는 무조건 제거
#     (클릭 없이 자동 요청되므로 사용자 동의가 있을 수 없음)
#   - 링크(`[text](url)`) : allowed_domains 에 없으면 하이퍼링크만 제거하고
#     텍스트는 보존 (클릭이 필요하므로 이미지보다는 위험도가 낮지만 기본은 거부)
#   - allowed_domains 가 비어 있으면(기본값) 모든 외부 링크가 제거된다 —
#     RAG 출처 인용처럼 정당한 링크가 필요하면 관리자가 valve 에 도메인을 추가.
# ---------------------------------------------------------------------------

import re
from typing import Optional, Callable, Any
from pydantic import BaseModel, Field
from urllib.parse import urlparse

_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\((https?://[^\s)]+)\)")
_HTML_IMG = re.compile(r"<img\b[^>]*\bsrc\s*=\s*[\"']?(https?://[^\"'\s>]+)[\"']?[^>]*>", re.IGNORECASE)
_MD_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^\s)]+)\)")


class Filter:
    class Valves(BaseModel):
        priority: int = Field(default=100, description="실행 우선순위")
        allowed_domains: str = Field(
            default="",
            description="이미지/링크로 허용할 도메인 (쉼표 구분, 비우면 전부 차단). 예: mywiki.local,docs.company.com",
        )
        block_all_images: bool = Field(
            default=True, description="외부 URL 이미지는 도메인 무관 항상 차단 (자동 로드=사용자 동의 불가)"
        )
        debug: bool = Field(default=True, description="서버 로그 출력")

    def __init__(self):
        self.valves = self.Valves()

    def _allowed(self) -> set[str]:
        return {d.strip().lower() for d in self.valves.allowed_domains.split(",") if d.strip()}

    def _domain_ok(self, url: str) -> bool:
        try:
            host = urlparse(url).hostname or ""
        except Exception:
            return False
        return host.lower() in self._allowed()

    def _sanitize(self, text: str) -> tuple[str, list[str]]:
        notes: list[str] = []

        def repl_md_img(m: re.Match) -> str:
            notes.append(f"image-blocked({urlparse(m.group(2)).hostname})")
            return "[이미지 차단됨: 외부 URL 자동 로드 시도]"

        def repl_html_img(m: re.Match) -> str:
            notes.append(f"html-img-blocked({urlparse(m.group(1)).hostname})")
            return "[이미지 차단됨: 외부 URL 자동 로드 시도]"

        if self.valves.block_all_images:
            text = _MD_IMAGE.sub(repl_md_img, text)
            text = _HTML_IMG.sub(repl_html_img, text)

        def repl_link(m: re.Match) -> str:
            text_part, url = m.group(1), m.group(2)
            if self._domain_ok(url):
                return m.group(0)
            notes.append(f"link-stripped({urlparse(url).hostname})")
            return text_part  # 하이퍼링크만 제거, 텍스트는 보존

        text = _MD_LINK.sub(repl_link, text)
        return text, notes

    async def outlet(
        self,
        body: dict,
        __event_emitter__: Optional[Callable[[Any], Any]] = None,
        __user__: Optional[dict] = None,
    ) -> dict:
        messages = body.get("messages", [])
        last_idx = None
        for i, msg in enumerate(messages):
            if msg.get("role") == "assistant":
                last_idx = i
        if last_idx is None:
            return body

        target = messages[last_idx]
        content = target.get("content")
        all_notes: list[str] = []

        if isinstance(content, str):
            new, notes = self._sanitize(content)
            target["content"] = new
            all_notes += notes
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    new, notes = self._sanitize(part.get("text", ""))
                    part["text"] = new
                    all_notes += notes

        output = target.get("output")
        if isinstance(output, list):
            for item in output:
                if isinstance(item, dict) and item.get("type") == "message":
                    for c in item.get("content", []):
                        if isinstance(c, dict) and c.get("type") == "text":
                            new, notes = self._sanitize(c.get("text", ""))
                            c["text"] = new
                            all_notes += notes

        if self.valves.debug and all_notes:
            print(f"[exfil_guard_s5.outlet] {all_notes}")

        if all_notes and __event_emitter__:
            await __event_emitter__(
                {
                    "type": "status",
                    "data": {"description": f"🛡️ 외부 링크/이미지 차단 ({len(all_notes)}건)", "done": True},
                }
            )
        return body
