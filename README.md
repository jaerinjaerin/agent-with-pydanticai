# 엘루오 도우미 — Hybrid RAG 챗봇

사내 규정·업무가이드, 회사 소개·프로젝트 등 궁금한 점을 질문할 수 있는 AI 챗봇입니다.

PydanticAI 에이전트 + 벡터 검색(Pinecone Integrated Index) + 키워드 폴백을 결합한 **Hybrid RAG** 아키텍처로 구현되었습니다. 영수증 촬영 → 비용처리 자동화 기능도 포함합니다.

---

## 시스템 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           DATA SOURCES                                 │
│                                                                        │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────┐  │
│  │  사내 게시판      │  │  eluocnc.com     │  │  어드민 파일 업로드   │  │
│  │  (LINE WORKS     │  │  웹사이트         │  │  (PDF/DOCX/XLSX/     │  │
│  │   board)         │  │  (sitemap + AJAX) │  │   PPTX/HWP/MD/TXT)  │  │
│  └────────┬─────────┘  └────────┬─────────┘  └──────────┬───────────┘  │
│           │                     │                        │              │
│  ┌────────▼─────────┐  ┌───────▼──────────┐  ┌─────────▼───────────┐  │
│  │ board_scraper.py │  │eluocnc_scraper.py│  │  file_extractor.py  │  │
│  │ (Playwright +    │  │ (Playwright BFS + │  │  (pdfplumber,       │  │
│  │  file_extractor) │  │  BeautifulSoup)   │  │   python-docx, etc) │  │
│  └────────┬─────────┘  └───────┬──────────┘  └─────────┬───────────┘  │
└───────────┼─────────────────────┼────────────────────────┼──────────────┘
            │                     │                        │
            ▼                     ▼                        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     DATA STORAGE (Supabase + JSON)                      │
│                                                                        │
│  Supabase documents 테이블 (url PK)                                     │
│  + JSON 파일: board_documents.json, eluocnc.json, admin_documents.json  │
│                                                                        │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     INDEX BUILD (build_index.py)                        │
│                                                                        │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │           Pinecone Integrated Index (서버사이드 임베딩)           │   │
│  │                  multilingual-e5-large (768차원)                 │   │
│  └────────────────────┬──────────────────────┬─────────────────────┘   │
│                       │                      │                         │
│            ┌──────────▼──────────┐  ┌────────▼────────────────────┐   │
│            │  텍스트 청크 벡터    │  │  이미지 설명 벡터             │   │
│            │  (1000자/200 오버랩) │  │  (Claude Vision → 설명 생성  │   │
│            │  + 리랭킹            │  │   → 벡터화)                  │   │
│            │  (bge-reranker-v2)  │  │                              │   │
│            └──────────┬──────────┘  └────────┬────────────────────┘   │
│                       │                      │                         │
│            Pinecone Serverless (AWS us-east-1, cosine)                 │
└─────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    HYBRID RAG ENGINE (런타임)                            │
│                                                                        │
│                    GraphRAGDatabase (graph_database.py)                 │
│                                                                        │
│  ┌────────────────────┐              ┌────────────────────────────┐   │
│  │  벡터 검색          │              │  키워드 폴백                 │   │
│  │  (Pinecone)        │              │  (TF-IDF 스타일)            │   │
│  │                    │              │                             │   │
│  │  쿼리 → 서버사이드  │              │  벡터 결과 < 3건일 때       │   │
│  │  임베딩 → cosine   │              │  제목/본문 매칭 점수 합산   │   │
│  │  → 리랭킹 → top-K  │              │                             │   │
│  └─────────┬──────────┘              └──────────────┬─────────────┘   │
│            │                                        │                  │
│            └─────────────┬──────────────────────────┘                  │
│                          ▼                                             │
│              URL 중복 제거 + LRU 캐시 (50건, 5분 TTL)                   │
│                          │                                             │
│                          ▼                                             │
│                   병합된 검색 결과                                       │
└──────────────────────────┬──────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      AI AGENT (faq_agent.py)                           │
│                                                                        │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                 PydanticAI Agent                                 │   │
│  │                 Model: Claude Sonnet 4 / Haiku 4.5 (선택 가능)   │   │
│  │                                                                  │   │
│  │  Tools:                                                          │   │
│  │  ┌──────────────┐ ┌──────────────┐ ┌────────────────────────┐  │   │
│  │  │ search_faq   │ │ list_titles  │ │ get_item_detail        │  │   │
│  │  │ (벡터 검색)   │ │ (목록 조회)   │ │ (상세 조회)             │  │   │
│  │  └──────────────┘ └──────────────┘ └────────────────────────┘  │   │
│  │  ┌──────────────┐ ┌──────────────────────────────────────────┐ │   │
│  │  │get_data_stats│ │ process_expense (영수증 → 비용처리)       │ │   │
│  │  │ (통계 조회)   │ │ (조건부: 영수증 업로드 시에만 활성화)      │ │   │
│  │  └──────────────┘ └──────────────────────────────────────────┘ │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└──────────────────────────┬──────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      STREAMLIT UI (app.py + chat_page.py)              │
│                                                                        │
│  ┌────────────────────────────────────────────────────────┐            │
│  │                    💬 엘루오 도우미                      │            │
│  │                                                        │            │
│  │  ┌──────────────────────────────────────────────────┐  │            │
│  │  │  채팅 인터페이스                                   │  │            │
│  │  │  • 스트리밍 응답 (run_stream_events)              │  │            │
│  │  │  • 캐릭터 버퍼 리빌 (30ms 간격, 1-6자/틱)         │  │            │
│  │  │  • 대화 히스토리 지속 (Supabase)                   │  │            │
│  │  │  • OpenGraph 카드 렌더링 (og_cards.py)            │  │            │
│  │  │  • [IMAGE: ...] 인라인 이미지 렌더링               │  │            │
│  │  │  • 관련 주제 추천 (클릭 가능)                       │  │            │
│  │  │  • BadRequest 폴백 (히스토리 없이 재시도)          │  │            │
│  │  └──────────────────────────────────────────────────┘  │            │
│  │                                                        │            │
│  │  ┌──────────────────────────────────────────────────┐  │            │
│  │  │  📷 비용처리 기능                                  │  │            │
│  │  │  • 영수증 이미지 업로드                             │  │            │
│  │  │  • Claude Haiku Vision → 구조화된 비용 데이터       │  │            │
│  │  │  • NaverWorks 비용처리 버튼 → 크롬 확장프로그램     │  │            │
│  │  └──────────────────────────────────────────────────┘  │            │
│  │                                                        │            │
│  │  비동기 런타임 (async_runtime.py)                       │            │
│  │  • 백그라운드 asyncio 루프 (Streamlit 호환)             │            │
│  │  • sniffio 패치 (uvloop 환경 대응)                     │            │
│  └────────────────────────────────────────────────────────┘            │
│                                                                        │
│  ┌────────────────────────────────────────────────────────┐            │
│  │  📄 문서 관리 (pages/admin.py)                         │            │
│  │  • 파일 업로드 → 텍스트/표/이미지 추출                  │            │
│  │  • 이미지 → Supabase Storage 업로드 → Claude Vision 설명│            │
│  │  • 텍스트+이미지 벡터화 → Pinecone upsert              │            │
│  │  • 문서 삭제 (벡터 + Storage 이미지 정리)               │            │
│  └────────────────────────────────────────────────────────┘            │
└─────────────────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────────┐
│               NAVERWORKS EXTENSION (Chrome 확장프로그램)                 │
│                                                                        │
│  eluo-naverworks-extension/                                            │
│  • content_script.js — 챗봇 UI에서 [EXPENSE_RESULT: {...}] 감지        │
│  • naverworks_filler.js — NaverWorks 비용처리 폼 자동 입력              │
│  • popup.html/js — 확장프로그램 설정 팝업                               │
│  • background.js — 크롬 확장프로그램 이벤트 핸들러                       │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 디렉토리 구조

```
├── src/
│   ├── app.py                      # Streamlit 멀티페이지 라우터 (chat + admin)
│   ├── chat_page.py                # 채팅 UI (스트리밍, 이미지, 비용처리)
│   ├── agent/
│   │   ├── faq_agent.py            # PydanticAI 에이전트 + 5개 도구 정의
│   │   ├── graph_database.py       # Pinecone 벡터 검색 + 키워드 폴백 엔진
│   │   └── expense_processor.py    # 영수증 → 구조화된 비용 데이터 (Claude Vision)
│   ├── graph/
│   │   ├── build_index.py          # Pinecone 배치 인덱스 빌드 스크립트
│   │   ├── embedding_index.py      # Pinecone Integrated Index 래퍼 (multilingual-e5-large)
│   │   ├── embeddings.py           # Gemini text-embedding-004 래퍼 (768차원)
│   │   ├── supabase_vector.py      # Supabase pgvector 검색 (대체 백엔드)
│   │   ├── graph_builder.py        # Claude 기반 엔티티 추출 + NetworkX 그래프
│   │   ├── image_describer.py      # Claude Vision 이미지 설명 생성 + 캐시
│   │   ├── ingest.py               # 단건 문서 인제스트 (텍스트+이미지 미디어 파이프라인)
│   │   └── data_store.py           # JSON 파일 기반 문서 저장 (레거시 폴백)
│   ├── scraper/
│   │   ├── eluocnc_scraper.py      # 회사 웹사이트 크롤러 (Playwright BFS)
│   │   ├── board_scraper.py        # LINE WORKS 게시판 크롤러 (Playwright + 2FA)
│   │   ├── file_extractor.py       # 멀티포맷 파일 추출 (PDF/DOCX/XLSX/PPTX/HWP/MD/TXT)
│   │   └── url_scraper.py          # 범용 URL → {title, content} 추출기
│   ├── storage/
│   │   ├── supabase_client.py      # 공유 싱글턴 Supabase 클라이언트
│   │   ├── supabase_chat.py        # 대화 지속성 (conversations + messages)
│   │   ├── supabase_conversations.py # 대화 관리 (세션 토큰 기반)
│   │   ├── supabase_docs.py        # 문서 CRUD (documents 테이블)
│   │   ├── supabase_documents.py   # 문서 CRUD (간소화 버전)
│   │   └── supabase_storage.py     # Supabase Storage 이미지 업로드/삭제
│   ├── pages/
│   │   └── admin.py                # 문서 관리 어드민 (파일 업로드 → 미디어 파이프라인)
│   └── ui/
│       ├── async_runtime.py        # Streamlit 비동기 런타임 호환 레이어
│       ├── og_cards.py             # OpenGraph 카드 렌더링
│       └── static/                 # CSS, JS, SVG 정적 파일
│           ├── style.css           # 채팅 UI 테마
│           ├── admin_style.css     # 어드민 페이지 스타일
│           ├── input_layout.js     # 커스텀 입력바 (영수증 업로드, 중지, 새채팅)
│           └── scroll_lock.js      # 자동 스크롤
├── eluo-naverworks-extension/      # Chrome 확장프로그램 (비용처리 → NaverWorks)
│   ├── manifest.json
│   ├── content_script.js
│   ├── naverworks_filler.js
│   ├── background.js
│   └── popup.html / popup.js
├── data/
│   ├── board_documents.json        # 게시판 크롤링 데이터
│   ├── eluocnc.json                # 웹사이트 크롤링 데이터
│   └── admin_documents.json        # 어드민 등록 문서 데이터
├── scripts/
│   └── migrate_to_supabase.py      # JSON → Supabase 문서 마이그레이션
├── supabase/
│   └── migrations/
│       └── 001_init.sql            # DB 스키마 (documents, document_chunks, conversations, messages)
├── docs/                           # 설계 문서
├── tests/
│   └── compare_search.py           # 검색 비교 테스트
├── requirements.txt
└── .env                            # API 키
```

---

## 데이터 파이프라인

```
┌──────────────────┐     ┌──────────────┐     ┌──────────────────┐
│ 크롤링            │ ──▶ │ JSON 저장     │ ──▶ │ Supabase 마이그  │
│ (board, eluocnc) │     │ data/*.json  │     │ migrate_to_      │
│                  │     │              │     │ supabase.py      │
└──────────────────┘     └──────────────┘     └────────┬─────────┘
                                                       │
┌──────────────────┐                                   │
│ 어드민 파일 업로드 │ ─────────────────────────────────▶│
│ (pages/admin.py) │                                   │
└──────────────────┘                                   ▼
                                              ┌────────────────┐
                                              │ Supabase       │
                                              │ documents 테이블│
                                              └───────┬────────┘
                                                      │
                                         build_index.py / ingest.py
                                                      │
                                    ┌─────────────────┼──────────────────┐
                                    ▼                 ▼                  ▼
                             ┌───────────┐   ┌──────────────┐   ┌───────────┐
                             │ Pinecone  │   │ Supabase     │   │ 이미지 설명│
                             │ Integrated│   │ Storage      │   │ (Claude   │
                             │ Index     │   │ (doc-images) │   │  Vision)  │
                             └───────────┘   └──────────────┘   └───────────┘
```

### 1단계: 크롤링

| 크롤러 | 대상 | 방식 | 출력 |
|--------|------|------|------|
| `board_scraper.py` | LINE WORKS 사내 게시판 | Playwright 로그인 자동화 + 첨부파일/이미지 추출 | `board_documents.json` |
| `eluocnc_scraper.py` | 회사 웹사이트 | Playwright BFS + AJAX → BeautifulSoup | `eluocnc.json` |

### 2단계: 마이그레이션 + 인덱스 빌드

```bash
# JSON → Supabase 문서 테이블 (최초 1회)
python scripts/migrate_to_supabase.py

# Pinecone 인덱스 빌드
python src/graph/build_index.py
```

### 3단계: 챗봇 실행

```bash
streamlit run src/app.py
```

---

## 비용처리 자동화

영수증 이미지를 업로드하면 자동으로 비용 데이터를 추출하고 NaverWorks에서 비용처리를 할 수 있습니다.

```
영수증 이미지 업로드
       ↓
Claude Haiku Vision (expense_processor.py)
       ↓
ExpenseResult {amount, date, place, item, expenseCategory}
       ↓
[EXPENSE_RESULT: {...}] → UI 렌더링
       ↓
"NaverWorks 비용처리" 버튼 클릭
       ↓
Chrome 확장프로그램 (eluo-naverworks-extension)
       ↓
NaverWorks 비용처리 폼 자동 입력
```

---

## 기술 스택

| 영역 | 기술 |
|------|------|
| **AI 프레임워크** | PydanticAI |
| **LLM** | Claude Sonnet 4 (에이전트), Claude Haiku 4.5 (비용처리, 이미지 설명, 엔티티 추출) |
| **벡터 DB** | Pinecone Integrated Index (multilingual-e5-large, 768차원, cosine) |
| **리랭킹** | Pinecone Inference API (bge-reranker-v2-m3) |
| **임베딩 (대체)** | Gemini text-embedding-004 (768차원, Supabase pgvector용) |
| **문서 DB** | Supabase PostgreSQL (documents, conversations, messages) |
| **벡터 검색 (대체)** | Supabase pgvector + tsvector Hybrid RRF |
| **이미지 저장** | Supabase Storage (doc-images 버킷) |
| **지식그래프** | NetworkX (엔티티/관계 추출) |
| **UI** | Streamlit |
| **크롤링** | Playwright, BeautifulSoup |
| **파일 추출** | pdfplumber, PyMuPDF, python-docx, openpyxl, python-pptx, pypyhwp |
| **비용처리 확장** | Chrome Extension (content_script + NaverWorks filler) |

---

## 환경 설정

`.env.example`을 `.env`로 복사하고 필요한 키를 설정합니다:

```bash
ANTHROPIC_API_KEY=sk-ant-...   # Claude API (에이전트 + 이미지 설명 + 비용처리)
PINECONE_API_KEY=pcsk_...      # Pinecone 벡터 DB
GEMINI_API_KEY=AI...           # Gemini 임베딩 (Supabase 벡터 검색용)

# Supabase — 문서 DB + 대화 저장 + 이미지 스토리지
NEXT_PUBLIC_SUPABASE_URL=https://xxx.supabase.co
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_DEFAULT_KEY=sb_publishable_...

# LINE WORKS 게시판 크롤러 (선택사항)
LINEWORKS_ID=
LINEWORKS_PW=
```

---

## 빠른 시작

```bash
# 1. 의존성 설치
pip install -r requirements.txt
playwright install chromium  # 게시판 크롤러 사용 시

# 2. 데이터 크롤링
python src/scraper/eluocnc_scraper.py
python src/scraper/board_scraper.py   # LINE WORKS 로그인 필요

# 3. Supabase 마이그레이션 (최초 1회)
python scripts/migrate_to_supabase.py

# 4. 인덱스 빌드
python src/graph/build_index.py

# 5. 챗봇 실행
streamlit run src/app.py
```
