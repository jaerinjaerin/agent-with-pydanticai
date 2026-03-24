# CLAUDE.md

이 파일은 Claude Code (claude.ai/code)가 이 저장소에서 작업할 때 참고하는 안내서입니다.

## 프로젝트 개요

하나의 저장소에 세 개의 컴포넌트가 있습니다:

1. **PydanticAI 튜토리얼** (`src/introduction.py`) — 구조화된 응답, 의존성 주입, 도구 통합, 메시지 히스토리 등 PydanticAI 에이전트 패턴을 순차적으로 보여주는 예제 모음. 대부분의 예제는 주석 처리되어 있으며, 주석을 해제하면 실행 가능.
2. **엘루오씨앤씨 RAG 챗봇** — 웹사이트·게시판·어드민 문서를 Pinecone Integrated Index로 벡터 검색한 뒤, PydanticAI + Claude 에이전트가 답변. Streamlit 채팅 UI. 대화 기록은 Supabase에 저장. 영수증 비용처리 기능 포함.
3. **NaverWorks 비용처리 확장** (`eluo-naverworks-extension/`) — Chrome 확장프로그램. 챗봇에서 영수증 분석 결과를 받아 NaverWorks 비용처리 폼을 자동 입력.

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

# LINE WORKS 게시판 크롤링 (data/board_documents.json에 저장)
python src/scraper/board_scraper.py

# JSON → Supabase 데이터 마이그레이션 (최초 1회)
python scripts/migrate_to_supabase.py

# Pinecone 인덱스 빌드 (최초 1회, 데이터 변경 시 재실행)
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
- `ANTHROPIC_API_KEY` — Claude API 키 (에이전트 + Vision + 비용처리)
- `PINECONE_API_KEY` — Pinecone 벡터 DB
- `GEMINI_API_KEY` — Gemini API 키 (임베딩, Supabase 벡터 검색용)
- `NEXT_PUBLIC_SUPABASE_URL` — Supabase 프로젝트 URL
- `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_DEFAULT_KEY` — Supabase Publishable 키

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

**UI 레이어:**
- **멀티페이지 라우터** (`src/app.py`): `st.navigation(position="hidden")`으로 chat과 admin 페이지를 라우팅. 사이드바 FOUC 방지.
- **채팅 UI** (`src/chat_page.py`): 스트리밍 채팅 인터페이스. 캐릭터 버퍼 리빌 (30ms 간격, 1-6자/틱). 도구 호출 로딩 표시. `[IMAGE: ...]` 인라인 이미지 렌더링. `[관련 주제: ...]` 파싱 → 클릭 가능한 추천 주제. `[EXPENSE_RESULT: {...}]` 파싱 → NaverWorks 비용처리 버튼. 모델 선택 (Claude Sonnet 4.6 / Haiku 4.5). BadRequestError 폴백.
- **어드민 페이지** (`src/pages/admin.py`): 문서 등록/수정/삭제. 파일 업로드 → 텍스트+이미지 추출 → Supabase 저장 → Pinecone 벡터화. 진행 상태 콜백.
- **비동기 런타임** (`src/ui/async_runtime.py`): sniffio 패치 + 백그라운드 asyncio 루프. uvloop 환경(Streamlit Cloud) 호환.
- **OG 카드** (`src/ui/og_cards.py`): OpenGraph 메타데이터로 링크 카드 렌더링.
- **정적 파일** (`src/ui/static/`): style.css(채팅 테마), admin_style.css, input_layout.js(커스텀 입력바), scroll_lock.js, SVG 아바타.

**에이전트 레이어:**
- **FAQ 에이전트** (`src/agent/faq_agent.py`): PydanticAI Agent. `AgentDeps` = `graph_db` + `receipt_data`. 5개 도구: `search_faq`(벡터 검색, 소스 필터), `list_titles`(문서 목록), `get_item_detail`(상세 조회), `get_data_stats`(통계), `process_expense`(조건부 — 영수증 업로드 시에만 활성화). 시스템 프롬프트에서 Closed/Open RAG 전략, 키워드 확장 예시, 소스별 안내, 출력 형식(`[관련 주제: ...]`, `[IMAGE: ...]`) 정의. `validate_response()`로 관련 주제 태그 보장.
- **비용처리** (`src/agent/expense_processor.py`): Claude Haiku 4.5 Vision으로 영수증 이미지 분석 → `ExpenseResult` (amount, date, place, item, expenseCategory, project). `analyze_receipt()` → JSON 구조화.
- **RAG 데이터베이스** (`src/agent/graph_database.py`): `GraphRAGDatabase` dataclass. `load()`: Supabase → JSON 폴백으로 문서 로드 + Pinecone 초기화. `vector_search()`: Pinecone 검색 → URL 중복 제거 → 리랭킹(bge-reranker-v2-m3) → 최소 점수 필터(0.25). `keyword_search()`: TF-IDF 스타일 제목/본문 매칭. `search()`: 벡터 → 결과 부족 시 키워드 폴백 → LRU 캐시(50건, 5분 TTL).

**벡터 검색 인프라:**
- **Pinecone Integrated Index** (`src/graph/embedding_index.py`): multilingual-e5-large (768차원) 서버사이드 임베딩. `init_pinecone()`, `upsert_records()`, `search_records()`, `chunk_text(1000자/200오버랩)`, `make_doc_id(MD5)`, `rerank_results()`. 주 검색 백엔드.
- **Supabase pgvector** (`src/graph/supabase_vector.py`): Gemini 임베딩 → `hybrid_search` RPC (벡터 + FTS RRF). Pinecone과 동일 인터페이스. 대체 검색 백엔드.
- **Gemini 임베딩** (`src/graph/embeddings.py`): `google-genai` SDK로 `text-embedding-004` (768차원) 임베딩 생성. `RETRIEVAL_DOCUMENT`(인덱싱), `RETRIEVAL_QUERY`(검색) task_type 구분.

**인제스트 파이프라인:**
- **빌드 스크립트** (`src/graph/build_index.py`): Supabase/JSON에서 문서 로드 → 청킹 → Pinecone 업로드. 이미지 설명 벡터도 포함.
- **단건 인제스트** (`src/graph/ingest.py`): `ingest_document()`(텍스트), `ingest_document_with_media()`(텍스트+이미지 — Supabase Storage 업로드 → Vision 설명 → 벡터화). 어드민 페이지에서 사용.
- **이미지 설명 생성** (`src/graph/image_describer.py`): Claude Haiku Vision API로 이미지 분석 → 한국어 텍스트 설명. `data/image_descriptions.json` 캐시. 2KB 미만 이미지 스킵.
- **그래프 빌더** (`src/graph/graph_builder.py`): Claude API로 문서에서 엔티티/관계 추출. Gemini 임베딩 유사도 기반 엔티티 중복 해결. NetworkX 그래프 구축.

**크롤러:**
- **웹사이트 크롤러** (`src/scraper/eluocnc_scraper.py`): Playwright BFS 탐색 + AJAX 엔드포인트 URL 수집 → BeautifulSoup 콘텐츠 추출 + PDF 텍스트 → `data/eluocnc.json`.
- **게시판 크롤러** (`src/scraper/board_scraper.py`): LINE WORKS 게시판. Playwright 로그인 자동화(2FA 수동). 인라인 이미지 + 첨부파일 추출 → `data/board_documents.json`.
- **파일 추출기** (`src/scraper/file_extractor.py`): PDF/DOCX/XLSX/PPTX/HWP/MD/TXT에서 텍스트, 표(마크다운), 임베디드 이미지 추출. `ExtractionResult` = text + `ExtractedImage[]`.
- **URL 추출기** (`src/scraper/url_scraper.py`): 범용 URL → {title, content}. 인코딩 자동 감지. Notion URL 특수 처리. requests → Playwright 폴백.

**Supabase 스토리지 레이어:**
- **클라이언트** (`src/storage/supabase_client.py`): 공유 싱글턴 Supabase 클라이언트. `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` 또는 `NEXT_PUBLIC_*` 환경변수 사용.
- **채팅 저장** (`src/storage/supabase_chat.py`): `conversations` + `messages` 테이블. 세션 토큰 기반 대화 생성/복원. `pydantic_message` JSONB 필드로 PydanticAI `ModelMessage` 직렬화/역직렬화. 영수증 이미지 Storage 업로드.
- **대화 관리** (`src/storage/supabase_conversations.py`): 대화 CRUD + 모델 선택 업데이트.
- **문서 CRUD** (`src/storage/supabase_docs.py`): `documents` 테이블. `load_items()`, `load_items_page()` (서버사이드 필터/검색/페이지네이션), `add_item()`, `update_item()`, `delete_item()`. JSON ↔ DB 스키마 변환.
- **문서 CRUD (간소화)** (`src/storage/supabase_documents.py`): `list_documents()`, `upsert_document()`, `delete_document()`, `get_stats()`.
- **이미지 스토리지** (`src/storage/supabase_storage.py`): Supabase Storage `doc-images` 버킷에 이미지 업로드/삭제. 공개 URL 자동 생성.

**데이터 저장 (레거시):**
- **JSON 저장** (`src/graph/data_store.py`): FileLock 기반 파일 읽기/쓰기. Supabase 미설정 시 폴백.

**DB 스키마** (`supabase/migrations/001_init.sql`):
- `documents`: url(PK), title, content, source, category, metadata(JSONB)
- `document_chunks`: id, document_id(FK), chunk_text, embedding(vector 768), tsv(tsvector), chunk_type, image_path
- `conversations`: session_token(UNIQUE), model_choice
- `messages`: conversation_id(FK), role, content, related_topics, pydantic_message(JSONB), metadata(JSONB)
- `hybrid_search` RPC: 벡터 + FTS Reciprocal Rank Fusion

**NaverWorks 확장프로그램** (`eluo-naverworks-extension/`):
- Chrome Manifest V3 확장프로그램
- `content_script.js`: 챗봇 UI에서 `[EXPENSE_RESULT: {...}]` 감지 → NaverWorks 페이지 전달
- `naverworks_filler.js`: NaverWorks 비용처리 폼 필드 자동 입력
- `popup.html/js`: 확장프로그램 설정 팝업
- `background.js`: 이벤트 핸들러

데이터 흐름: (크롤러 → JSON → `migrate_to_supabase.py`) + (어드민 파일 업로드) → Supabase `documents` 테이블 → `build_index.py` → Pinecone Integrated Index (텍스트 청크 + 이미지 설명) → `GraphRAGDatabase.load()` → Pinecone 벡터 검색 + 키워드 폴백 → Claude 답변 생성. 이미지는 Supabase Storage에 저장. 대화는 Supabase conversations/messages에 지속.

## 알려진 이슈

- PydanticAI는 초기 베타 단계이며, API가 변경될 수 있음
- 도구 사용 시 `message_history`가 400 BadRequestError를 유발할 수 있음 — chat_page.py에서 폴백 재시도로 처리
- PydanticAI에서 temperature 등 모델 파라미터 조정 불가
- Claude API 비용: 에이전트(Sonnet), 이미지 설명/비용처리/엔티티 추출(Haiku). 문서·이미지 수에 비례
- Supabase 무료 티어: 500MB DB + 1GB Storage
- Gemini text-embedding-004: 무료 티어 제공. 768차원
- Supabase 연결 실패 시 JSON 파일 폴백 + 키워드 검색만 동작
- Supabase Storage 미설정 시 파일 업로드의 이미지 추출/저장이 건너뛰어짐 (텍스트/표만 인제스트)
- 이미지 설명 생성: Claude Haiku Vision API 호출. 이미지당 API 비용 발생. 2KB 미만 이미지 스킵
- .env.example에 `GEMINI_API_KE` 오타 있음 (→ `GEMINI_API_KEY`)
