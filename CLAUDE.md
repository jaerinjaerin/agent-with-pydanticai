# CLAUDE.md

이 파일은 Claude Code (claude.ai/code)가 이 저장소에서 작업할 때 참고하는 안내서입니다.

## 프로젝트 개요

하나의 저장소에 두 개의 프로젝트가 있습니다:

1. **PydanticAI 튜토리얼** (`src/introduction.py`) — 구조화된 응답, 의존성 주입, 도구 통합, 메시지 히스토리 등 PydanticAI 에이전트 패턴을 순차적으로 보여주는 예제 모음. 대부분의 예제는 주석 처리되어 있으며, 주석을 해제하면 실행 가능. Dave Ebbelaar (Datalumina) 작성.
2. **엘루오 LINE WORKS FAQ 챗봇** — LINE WORKS 도움말 페이지를 크롤링하고, TF-IDF로 인덱싱한 뒤, PydanticAI + Gemini 에이전트가 질문에 답변하며, Streamlit 채팅 UI로 제공.
3. **LINE WORKS 게시판 크롤러** — `board.worksmobile.com` 사내 게시판을 Playwright 브라우저 자동화로 크롤링. 게시글 본문 + 첨부파일(PDF/DOCX/XLSX/HWP) 텍스트 추출. FAQ와 통합하여 챗봇에서 사내 규정/업무가이드 질문에도 답변 가능.
4. **엘루오씨앤씨 웹사이트 크롤러** — `eluocnc.com` 공식 웹사이트의 sitemap + AJAX 엔드포인트에서 URL 수집, 정적 페이지/프로젝트/블로그/PDF 콘텐츠 추출. FAQ 챗봇 파이프라인에 통합.

## 명령어

```bash
# 의존성 설치
pip install -r requirements.txt

# PydanticAI 튜토리얼 실행
python src/introduction.py

# FAQ 데이터 크롤링 (data/faq_lineworks.json에 저장)
python src/scraper/faq_scraper.py

# Playwright 브라우저 설치 (최초 1회)
playwright install chromium

# 게시판 크롤링 (로그인 자동화, data/board_lineworks.json에 저장)
python src/scraper/board_scraper.py

# 수동 다운로드 폴백 (data/board_raw/에 파일 넣고 실행)
python src/scraper/board_scraper.py --from-local data/board_raw/

# 엘루오씨앤씨 웹사이트 크롤링 (data/eluocnc.json에 저장)
python src/scraper/eluocnc_scraper.py

# 챗봇 UI 실행 (FAQ + 게시판 + 홈페이지 데이터 통합)
streamlit run src/app.py

# UI 없이 에이전트 빠른 테스트
python -c "import sys; sys.path.insert(0,'src'); from agent.faq_agent import ask; print(ask('비밀번호를 잊어버렸어요'))"
```

## 환경 설정

`.env.example`을 `.env`로 복사. `.env.example`에는 `OPENAI_API_KEY`가 있지만, 실제 코드는 `GoogleModel("gemini-2.5-flash")`를 사용하므로 `GEMINI_API_KEY`(또는 google-genai SDK가 요구하는 환경 변수)를 설정해야 합니다. 게시판 크롤러를 사용하려면 `LINEWORKS_ID`, `LINEWORKS_PW`도 설정해야 합니다.

## 아키텍처

### PydanticAI 튜토리얼 (`src/introduction.py`)

대부분 주석 처리된 순차적 예제가 담긴 단일 파일:
- `Agent`의 `output_type=ResponseModel`을 통한 구조화된 응답
- `deps_type=CustomerDetails`와 `RunContext[T]`를 통한 의존성 주입
- `@agent.system_prompt` 데코레이터로 의존성 기반 동적 시스템 프롬프트 생성
- `Tool()`로 래핑된 일반 함수가 첫 번째 인자로 `RunContext`를 받는 도구 패턴
- `message_history` 파라미터를 통한 대화 연속성

`nest_asyncio.apply()`를 사용하여 이미 실행 중인 이벤트 루프에서도 `run_sync()`가 동작. 헬퍼 `src/utils/markdown.py`의 `to_markdown()`이 Pydantic 모델을 마크다운으로 변환.

### FAQ 챗봇 + 게시판 통합

- **FAQ 크롤러** (`src/scraper/faq_scraper.py`): `faqs-sitemap.xml`에서 URL 수집 → BeautifulSoup + lxml로 제목/본문 추출 → `data/faq_lineworks.json`에 저장. 요청 간 1.5초 대기.
- **게시판 크롤러** (`src/scraper/board_scraper.py`): Playwright로 `board.worksmobile.com` 로그인 → 게시글 목록/상세 수집 → 첨부파일 다운로드 & 텍스트 추출 → `data/board_lineworks.json` 저장. `--from-local` 플래그로 수동 다운로드 폴백 가능.
- **첨부파일 추출기** (`src/scraper/file_extractor.py`): PDF(`pdfplumber`), DOCX(`python-docx`), XLSX(`openpyxl`), HWP(`python-hwp`/`olefile` 폴백)에서 텍스트 추출.
- **엘루오씨앤씨 크롤러** (`src/scraper/eluocnc_scraper.py`): `sitemap.xml`에서 `/ko/` URL 수집 + AJAX 엔드포인트(`ajax.works_list.asp`, `ajax.idea_list.asp`)로 프로젝트/블로그 URL 추가 수집 → 페이지 스크래핑 + PDF 텍스트 추출 → `data/eluocnc.json` 저장.
- **에이전트** (`src/agent/faq_agent.py`): `FAQDatabase` dataclass가 FAQ + 게시판 + 홈페이지 데이터를 병합하여 TF-IDF 인덱스(scikit-learn) 구축. `search_faq` 도구가 cosine similarity로 상위 3개 결과를 출처(FAQ/게시판/회사 홈페이지)와 함께 반환. `GoogleModel("gemini-2.5-flash")`와 한국어 시스템 프롬프트 사용.
- **UI** (`src/app.py`): Streamlit 채팅 인터페이스. 사이드바에 FAQ/게시판 데이터소스 현황 표시. `st.cache_resource`로 FAQDatabase 캐시. BadRequestError 발생 시 히스토리 없이 재시도하는 폴백 처리 포함.

데이터 흐름: (sitemap XML → FAQ 크롤러 → `faq_lineworks.json`) + (게시판 → Playwright 크롤러 → `board_lineworks.json`) + (eluocnc.com → 웹사이트 크롤러 → `eluocnc.json`) → `FAQDatabase.load()` → TF-IDF 인덱스 → 에이전트 도구 → LLM 응답.

## 알려진 이슈

- PydanticAI는 초기 베타 단계이며, API가 변경될 수 있음
- 도구 사용 시 `message_history`가 400 BadRequestError를 유발할 수 있음 (tool response 메시지 누락) — Streamlit UI에서 폴백 재시도로 처리
- PydanticAI에서 temperature 등 모델 파라미터 조정 불가
- 게시판 크롤러의 CSS 셀렉터는 LINE WORKS UI 업데이트 시 깨질 수 있으며, 최초 실행 시 조정 필요
- LINE WORKS 2FA/OTP 설정 시 최초 로그인에 수동 개입 필요 → `storage_state`로 이후 자동화
- `python-hwp`가 모든 HWP 버전을 지원하지 않을 수 있음 → 실패 시 `olefile` 폴백 또는 파일명만 기록
