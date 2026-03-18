# CLAUDE.md

이 파일은 Claude Code (claude.ai/code)가 이 저장소에서 작업할 때 참고하는 안내서입니다.

## 프로젝트 개요

하나의 저장소에 두 개의 프로젝트가 있습니다:

1. **PydanticAI 튜토리얼** (`src/introduction.py`) — 구조화된 응답, 의존성 주입, 도구 통합, 메시지 히스토리 등 PydanticAI 에이전트 패턴을 순차적으로 보여주는 예제 모음. 대부분의 예제는 주석 처리되어 있으며, 주석을 해제하면 실행 가능. Dave Ebbelaar (Datalumina) 작성.
2. **엘루오씨앤씨 RAG 챗봇** — `eluocnc.com` 웹사이트를 크롤링하고, Hybrid RAG (pgvector + tsvector RRF)로 검색한 뒤, PydanticAI + Claude 에이전트가 질문에 답변하며, Streamlit 채팅 UI로 제공. 대화 기록은 Supabase에 저장되어 세션 간 지속됨.
3. **엘루오씨앤씨 웹사이트 크롤러** — `eluocnc.com` 공식 웹사이트의 BFS 탐색 + AJAX 엔드포인트에서 URL 수집, 정적 페이지/프로젝트/블로그/PDF 콘텐츠 추출. 챗봇 파이프라인에 통합.

## 명령어

```bash
# 의존성 설치
pip install -r requirements.txt

# PydanticAI 튜토리얼 실행
python src/introduction.py

# Playwright 브라우저 설치 (최초 1회)
playwright install chromium

# 엘루오씨앤씨 웹사이트 크롤링 (data/eluocnc.json에 저장)
python src/scraper/eluocnc_scraper.py

# JSON → Supabase 데이터 마이그레이션 (최초 1회)
python scripts/migrate_to_supabase.py

# Hybrid RAG 인덱스 빌드 (최초 1회, 데이터 변경 시 재실행)
python src/graph/build_index.py

# 챗봇 UI 실행
streamlit run src/app.py

# UI 없이 에이전트 빠른 테스트
python -c "import sys; sys.path.insert(0,'src'); from agent.faq_agent import ask; print(ask('비밀번호를 잊어버렸어요'))"
```

## 워크플로우 규칙

- **UI 관련 기능 구현 후 반드시 `/test-ui` 스킬로 Playwright 테스트를 수행할 것.** 페이지 로드, 채팅 입력/응답, 스크린샷 캡처를 통해 변경 사항이 정상 동작하는지 검증한다. 플랜 작성 시에도 검증 단계에 포함해야 한다.

## 환경 설정

`.env.example`을 `.env`로 복사하고, 다음 환경변수를 설정합니다:
- `ANTHROPIC_API_KEY` — Claude API 키
- `SUPABASE_URL` — Supabase 프로젝트 URL
- `SUPABASE_SERVICE_KEY` — Supabase Service Role 키 (Dashboard → Settings → API)
- `GOOGLE_API_KEY` — Gemini API 키 (임베딩 + 모델용)

## 아키텍처

### PydanticAI 튜토리얼 (`src/introduction.py`)

대부분 주석 처리된 순차적 예제가 담긴 단일 파일:
- `Agent`의 `output_type=ResponseModel`을 통한 구조화된 응답
- `deps_type=CustomerDetails`와 `RunContext[T]`를 통한 의존성 주입
- `@agent.system_prompt` 데코레이터로 의존성 기반 동적 시스템 프롬프트 생성
- `Tool()`로 래핑된 일반 함수가 첫 번째 인자로 `RunContext`를 받는 도구 패턴
- `message_history` 파라미터를 통한 대화 연속성

`nest_asyncio.apply()`를 사용하여 이미 실행 중인 이벤트 루프에서도 `run_sync()`가 동작. 헬퍼 `src/utils/markdown.py`의 `to_markdown()`이 Pydantic 모델을 마크다운으로 변환.

### RAG 챗봇

**인프라: Supabase 통합** (벡터DB + 스토리지 + 문서DB + 대화DB)
- **Supabase 클라이언트** (`src/storage/supabase_client.py`): 공유 싱글턴 Supabase 클라이언트. `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` 환경변수 사용.
- **Gemini 임베딩** (`src/graph/embeddings.py`): `google-genai` SDK로 `text-embedding-004` (768차원) 임베딩 생성. `RETRIEVAL_DOCUMENT`(인덱싱), `RETRIEVAL_QUERY`(검색) task_type 구분.
- **벡터 검색** (`src/graph/supabase_vector.py`): Supabase pgvector 기반. Gemini 임베딩 → `hybrid_search` RPC (벡터 + FTS Reciprocal Rank Fusion). `chunk_text()`, `make_doc_id()` 텍스트 유틸리티 포함.
- **이미지 스토리지** (`src/storage/supabase_storage.py`): Supabase Storage `doc-images` 버킷에 이미지 업로드/삭제. 공개 URL 자동 생성.
- **문서 CRUD** (`src/storage/supabase_documents.py`): `documents` 테이블 CRUD. `list_documents()`, `upsert_document()`, `delete_document()`, `get_stats()`.
- **대화 지속성** (`src/storage/supabase_conversations.py`): `conversations` + `messages` 테이블. 세션 토큰 기반 대화 생성/복원/삭제.

**파이프라인:**
- **엘루오씨앤씨 크롤러** (`src/scraper/eluocnc_scraper.py`): BFS 링크 탐색 + AJAX 엔드포인트로 URL 수집 → 페이지 스크래핑 + PDF 텍스트 추출 → `data/eluocnc.json` 저장.
- **파일 추출기** (`src/scraper/file_extractor.py`): PDF/DOCX/XLSX/PPTX/MD/TXT/HWP 파일에서 텍스트, 표(마크다운 테이블), 임베디드 이미지를 추출.
- **이미지 설명 생성** (`src/graph/image_describer.py`): Claude Vision API로 이미지 분석 → 한국어 텍스트 설명 생성.
- **그래프 빌더** (`src/graph/graph_builder.py`): Claude API로 문서에서 엔티티/관계 추출. Gemini 임베딩 유사도 기반 엔티티 중복 해결. NetworkX 그래프 구축.
- **빌드 스크립트** (`src/graph/build_index.py`): Supabase `documents` 테이블 → 청킹 → Gemini 임베딩 → pgvector 업로드.
- **인제스트 파이프라인** (`src/graph/ingest.py`): 단건 문서 인제스트. `ingest_document()`(텍스트), `ingest_document_with_media()`(텍스트+이미지 — Storage 업로드 → Vision 설명 → 벡터화).
- **VectorRAG 데이터베이스** (`src/agent/graph_database.py`): `GraphRAGDatabase` dataclass. Supabase에서 문서 로드 + 하이브리드 검색 (pgvector + tsvector RRF). 키워드 폴백 지원.
- **에이전트** (`src/agent/faq_agent.py`): `GraphRAGDatabase`를 deps로 사용. `search_faq`, `list_titles`, `get_item_detail`, `get_data_stats` 도구 제공.
- **UI** (`src/app.py`): Streamlit 채팅 인터페이스. 대화 지속성 (Supabase). `[IMAGE: ...]` 패턴으로 이미지 인라인 렌더링. BadRequestError 폴백.
- **어드민 페이지** (`src/pages/admin.py`): 문서 등록/수정/삭제. Supabase `documents` 테이블 CRUD + Storage 이미지 관리 + 벡터 인덱싱.

데이터 흐름: (eluocnc.com → 크롤러 → `eluocnc.json` → `migrate_to_supabase.py`) + (어드민 등록) → Supabase `documents` 테이블 → `build_index.py` → (pgvector `document_chunks` + 지식그래프) → `GraphRAGDatabase.load()` → Hybrid RRF 검색 → Claude 답변 생성. 이미지는 Supabase Storage에 저장되고, 이미지 설명은 별도 벡터로 인덱싱.

## 알려진 이슈

- PydanticAI는 초기 베타 단계이며, API가 변경될 수 있음
- 도구 사용 시 `message_history`가 400 BadRequestError를 유발할 수 있음 — Streamlit UI에서 폴백 재시도로 처리
- PydanticAI에서 temperature 등 모델 파라미터 조정 불가
- Claude API 비용: 엔티티 추출 시 문서당 1회 API 호출. 문서 수에 비례
- Supabase 무료 티어: 500MB DB + 1GB Storage
- Gemini text-embedding-004: 무료 티어 제공. 768차원.
- Supabase 검색은 인터넷 연결 필요. 연결 실패 시 벡터 검색 비활성화 (키워드 검색만 동작)
- Supabase Storage 미설정 시 파일 업로드의 이미지 추출/저장이 건너뛰어짐 (텍스트/표만 인제스트)
- 이미지 설명 생성: Claude Haiku Vision API 호출. 이미지 수에 비례한 API 비용 발생
- 파일 추출 시 2KB 미만 이미지는 아이콘/스페이서로 간주하여 스킵
