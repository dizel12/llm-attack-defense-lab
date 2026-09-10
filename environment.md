# 실험 환경

| 구성요소 | 버전/설정 |
|---|---|
| OS | Windows 11, Docker Desktop |
| Open WebUI | `ghcr.io/open-webui/open-webui:v0.11.3` (컨테이너명 `open-webui`, 포트 3000→8080) |
| LLM 런타임 | Ollama, `http://localhost:11434` |
| 대상 모델 | `qwen3:14b` (14.8B), 보조 `gemma3:12b`, `qwen2.5:7b-instruct` |
| 임베딩 | `bge-m3`, `nomic-embed-text` |
| 웹검색 | SearXNG (`searxng/searxng:latest`, 포트 8888) |
| RAG 컨텍스트 주입 | `RAG_SYSTEM_CONTEXT` 미설정 → 검색 컨텍스트는 마지막 user 메시지에 prepend |

## 필터 훅 실행 순서 (middleware.py, v0.11.3)

```
2635  filter_type='inlet'        RAG 주입 전
3077  apply_source_context...    검색 문서가 메시지에 주입
3104  filter_type='request'      RAG 주입 후, 모델 호출 직전  ← 인젝션 방어 지점
3408  filter_type='request'      네이티브 툴콜 루프 재요청 시 재실행
3984  filter_type='outlet'       모델 응답 후
```

메서드 이름 == `filter_type` (`filter.py`: `getattr(module, filter_type)`).
`toggle = True` 필터는 채팅에서 토글 ON 한 경우에만 실행.

## 재현 시 주의

- 모든 테스트는 temperature 0, qwen3 는 `think: false`.
- 페이로드는 무해 카나리아(`김제윤`) 또는 localhost 비콘만 사용.
