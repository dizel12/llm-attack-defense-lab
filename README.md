# LLM Attack & Defense Lab

자가호스팅 **Open WebUI + Ollama** 환경에서 대표적인 LLM 공격을 재현하고,
방어를 반복 적용하며 실제로 막히는지 검증한 기록.

> 연구·교육 목적. 모든 페이로드는 무해한 카나리아(`김제윤` 문자열, localhost 비콘)만 사용하며
> 제3자 시스템을 대상으로 하지 않는다. 자가호스팅 본인 인프라 대상 방어 연구.
> 환경은 [`environment.md`](environment.md), 결과 요약은 [`results-summary.md`](results-summary.md).

## 시나리오

| # | OWASP | 공격 | 상태 |
|---|-------|------|------|
| 01 | LLM01 | 간접 프롬프트 인젝션 (RAG PDF 숨김 텍스트) | ✅ 방어 완료 (`request` 훅 필터) |
| 02 | LLM01 | 직접 프롬프트 인젝션 / 탈옥 | ✅ 방어 시도 1~3 (11/11 차단, 회귀 0). 3층: 하드닝 프롬프트 + request(정규화·스포트라이팅·인코딩·히스토리/메모리 정제·hard-replace) + outlet(카나리아/흔적 차단) |
| 03 | LLM07 | 시스템 프롬프트 추출 | ✅ 방어 시도 1 (v0.2). BEFORE 1/12 → AFTER 0/12, 회귀 0. request 스포트라이팅+거부지시 + outlet(비밀 전체/조각, 라인·절 축자, 슁글 겹침) |
| 04 | LLM01 | 웹검색(SearXNG) 경유 간접 인젝션 | ✅ 방어 완료. BEFORE 돌파 → AFTER 3/3 PASS, 회귀 0. source_context 정규화+디코드+정화+스포트라이팅(`<<WEB_UNTRUSTED>>`) + outlet 백스톱 |
| 05 | LLM05 | 마크다운 이미지 데이터 유출 | ✅ 방어 완료. 로컬 비콘으로 실제 유출 증명(3/3) → outlet 이미지/링크 차단으로 3/3 PASS, 회귀 0 |
| 06 | LLM06 | 과도한 에이전시 / 툴 오남용 | ✅ 방어 완료. 숨긴 지시만으로 send_webhook 실제 호출 확인 → request 정화 + 실행 직전 게이트로 2/2 차단, 회귀 0 |
| 07 | LLM04 | 지식베이스 포이즈닝 | ⬜ |
| 08 | LLM08 | 임베딩 / 검색 조작 | ⬜ |
| 09 | LLM02 | 크로스 세션 정보 유출 | ⬜ |
| 10 | LLM10 | 무제한 소비 / Denial-of-Wallet | ⬜ |
| 11 | LLM01/04 | **메모리 = 간접 인젝션 영속화** (시나리오 2에서 발견) | ✅ 방어 완료. 오염→재주입 전체 체인 재현 → 재주입 차단 PASS, 정상 메모리 보존. at-rest 스캔 보완책 포함 |
| 12 | LLM01 | task 모델(제목/태그 생성) 경유 인젝션 (시나리오 2에서 발견) | 🔍 재현 완료, **Function 레벨에서 고칠 수 없음을 소스로 확인** (설정 레벨 완화책만 존재) |

## 구조

```
common/           카나리아 정의·탐지 (canary.py)
scenario-XX-*/
  attack.md       원리와 페이로드
  test.md         재현·판정 절차
  payloads/       실제 페이로드 (PDF, jsonl, prompts_for_ui.md)
  run_baseline.py 자동 테스트 러너 (BEFORE) — Ollama 직접, temp 0
  run_after.py    방어 적용 후 재측정 (AFTER)
  result-before.md / result-after.md
  defense/        OWUI Filter Function + 시스템 프롬프트 + attempt-N.md 기록
```

## 실행

```bash
# BEFORE 측정
cd scenario-02-direct-jailbreak && python run_baseline.py
# 방어 적용 후
python run_after.py
```

OWUI 적용: `defense/*.py` 를 Admin Panel > Functions 에 추가·Enable,
`defense/system_prompt_*.txt` 를 Workspace > Models 의 System Prompt 에.
대상 모델은 스트리밍 OFF 권장. 자세히는 각 `defense/attempt-*.md`.

## 각 시나리오 방법론

1. 공격 재현 → `result-before.md` 로 돌파율 측정
2. 방어 아이디어 → 검토 → 구현 (필터 / 설정 / 프롬프트)
3. 동일 테스트 재실행 → `result-after.md` → before/after 비교
4. 뚫리면 2로 복귀. 3회 이상 실패 시 접근 자체를 재검토
