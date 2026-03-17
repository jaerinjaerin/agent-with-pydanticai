# 엘루오 도우미 — Hybrid RAG 챗봇

사내 규정·업무가이드, 회사 소개·프로젝트 등 궁금한 점을 질문할 수 있는 AI 챗봇입니다.

PydanticAI 에이전트 + 벡터 검색(Pinecone) + 지식그래프(NetworkX)를 결합한 **Hybrid RAG** 아키텍처로 구현되었습니다.

---

## 시스템 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           DATA SOURCES                                 │
│                                                                        │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────┐  │
│  │  LINE WORKS FAQ  │  │  사내 게시판      │  │  eluocnc.com 웹사이트 │  │
│  │  (sitemap.xml)   │  │  (board.works     │  │  (sitemap + AJAX)    │  │
│  │                  │  │   mobile.com)     │  │                      │  │
│  └────────┬─────────┘  └────────┬─────────┘  └──────────┬───────────┘  │
│           │                     │                        │              │
│  ┌────────▼─────────┐  ┌───────▼──────────┐  ┌─────────▼───────────┐  │
│  │  faq_scraper.py  │  │ board_scraper.py │  │ eluocnc_scraper.py  │  │
│  │  (BeautifulSoup) │  │ (Playwright +    │  │ (requests +         │  │
│  │                  │  │  file_extractor)  │  │  BeautifulSoup)     │  │
│  └────────┬─────────┘  └───────┬──────────┘  └─────────┬───────────┘  │
└───────────┼─────────────────────┼────────────────────────┼──────────────┘
            │                     │                        │
            ▼                     ▼                        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          DATA STORAGE (JSON)                            │
│                                                                        │
│   faq_lineworks.json      board_lineworks.json        eluocnc.json     │
│                                                                        │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     INDEX BUILD (build_index.py)                        │
│                                                                        │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                    임베딩 모델 (Sentence-Transformers)            │   │
│  │             paraphrase-multilingual-MiniLM-L12-v2               │   │
│  └────────────────────┬──────────────────────┬─────────────────────┘   │
│                       │                      │                         │
│            ┌──────────▼──────────┐  ┌────────▼────────────────────┐   │
│            │  Pinecone 벡터 DB   │  │  지식그래프 구축              │   │
│            │                     │  │  (graph_builder.py)          │   │
│            │  • 문장 청킹        │  │                              │   │
│            │    (1000자/200 오버랩)│  │  • Claude Haiku로            │   │
│            │  • 코사인 유사도    │  │    엔티티/관계 추출           │   │
│            │  • Serverless       │  │  • 임베딩 기반 중복 병합      │   │
│            │    (AWS us-east-1)  │  │  • NetworkX 그래프 저장       │   │
│            │                     │  │                              │   │
│            └──────────┬──────────┘  └────────┬────────────────────┘   │
│                       │                      │                         │
│                       ▼                      ▼                         │
│            eluo-faq 인덱스          knowledge_graph.json               │
│            (384차원 벡터)            entity_embeddings.npz              │
└─────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    HYBRID RAG ENGINE (런타임)                            │
│                                                                        │
│                    GraphRAGDatabase (graph_database.py)                 │
│                                                                        │
│  ┌────────────────────┐              ┌────────────────────────────┐   │
│  │  벡터 검색          │              │  그래프 검색                 │   │
│  │  (Pinecone)        │              │  (NetworkX)                 │   │
│  │                    │              │                             │   │
│  │  쿼리 임베딩 →     │              │  쿼리 → 유사 엔티티 매칭 →  │   │
│  │  cosine similarity │              │  1-2홉 그래프 탐색 →        │   │
│  │  → top-K 문서      │              │  연결 문서 발견             │   │
│  └─────────┬──────────┘              └──────────────┬─────────────┘   │
│            │                                        │                  │
│            └─────────────┬──────────────────────────┘                  │
│                          ▼                                             │
│              Reciprocal Rank Fusion (RRF)                              │
│              score = Σ 1/(60 + rank_i)                                 │
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
│  │                 Model: Claude Sonnet 4                           │   │
│  │                                                                  │   │
│  │  Tools:                                                          │   │
│  │  ┌──────────────┐ ┌──────────────┐ ┌────────────────────────┐  │   │
│  │  │ search_faq   │ │ list_titles  │ │ get_item_detail        │  │   │
│  │  │ (의미 검색)   │ │ (목록 조회)   │ │ (상세 조회)             │  │   │
│  │  └──────────────┘ └──────────────┘ └────────────────────────┘  │   │
│  │  ┌──────────────┐ ┌──────────────┐                             │   │
│  │  │get_data_stats│ │explore_topic │                             │   │
│  │  │ (통계 조회)   │ │ (그래프 탐색) │                             │   │
│  │  └──────────────┘ └──────────────┘                             │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└──────────────────────────┬──────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      STREAMLIT UI (app.py)                              │
│                                                                        │
│  ┌────────────────────────────────────────────────────────┐            │
│  │                    💬 엘루오 도우미                      │            │
│  │                                                        │            │
│  │  ┌──────────────────────────────────────────────────┐  │            │
│  │  │  채팅 인터페이스                                   │  │            │
│  │  │  • 스트리밍 응답 (run_stream_events)              │  │            │
│  │  │  • 대화 히스토리 유지                              │  │            │
│  │  │  • OpenGraph 카드 렌더링 (og_cards.py)            │  │            │
│  │  │  • BadRequest 폴백 (히스토리 없이 재시도)          │  │            │
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
│  │  • 이미지 → AWS S3 업로드 → Claude Vision 설명           │            │
│  │  • 텍스트+이미지 벡터화 → Pinecone upsert              │            │
│  │  • 문서 삭제 (벡터 + S3 이미지 정리)                    │            │
│  └────────────────────────────────────────────────────────┘            │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 디렉토리 구조

```
├── src/
│   ├── app.py                      # Streamlit 메인 챗봇 UI
│   ├── agent/
│   │   ├── faq_agent.py            # PydanticAI 에이전트 + 도구 정의
│   │   └── graph_database.py       # GraphRAG 하이브리드 검색 엔진
│   ├── graph/
│   │   ├── build_index.py          # Pinecone + 지식그래프 빌드 스크립트
│   │   ├── embedding_index.py      # 임베딩 생성 + Pinecone 연동
│   │   ├── graph_builder.py        # Claude 기반 엔티티 추출 + NetworkX 그래프
│   │   ├── image_describer.py      # Claude Vision 이미지 설명 생성 + 캐시
│   │   └── ingest.py               # 단건 문서 인제스트 (텍스트+이미지 미디어 파이프라인)
│   ├── scraper/
│   │   ├── eluocnc_scraper.py      # 회사 웹사이트 크롤러
│   │   └── file_extractor.py       # 파일 추출 (텍스트/표/이미지, PDF/DOCX/XLSX/PPTX/MD/TXT/HWP)
│   ├── storage/
│   │   └── r2_storage.py           # AWS S3 이미지 업로드/삭제
│   ├── pages/
│   │   └── admin.py                # 문서 관리 어드민 (파일 업로드 → 미디어 파이프라인)
│   └── ui/
│       ├── async_runtime.py        # Streamlit 비동기 런타임 호환 레이어
│       ├── og_cards.py             # OpenGraph 카드 렌더링
│       └── static/                 # CSS, JS 정적 파일
├── data/
│   ├── board_lineworks.json        # 게시판 크롤링 데이터
│   ├── eluocnc.json                # 웹사이트 크롤링 데이터
│   ├── knowledge_graph.json        # 지식그래프 (NetworkX JSON)
│   ├── entity_embeddings.npz       # 사전 계산 엔티티 임베딩
│   └── extraction_cache/           # Claude 엔티티 추출 캐시
├── requirements.txt
└── .env                            # API 키 (ANTHROPIC, PINECONE, etc.)
```

---

## 데이터 파이프라인

```
┌──────────┐     ┌──────────┐     ┌──────────┐
│ 크롤링    │ ──▶ │ JSON 저장 │ ──▶ │ 인덱스    │
│          │     │          │     │ 빌드     │
└──────────┘     └──────────┘     └──────────┘
                                       │
                          ┌────────────┼────────────┐
                          ▼            ▼            ▼
                   ┌───────────┐ ┌──────────┐ ┌──────────┐
                   │ Pinecone  │ │ 지식     │ │ 엔티티    │
                   │ 벡터 DB   │ │ 그래프   │ │ 임베딩    │
                   └───────────┘ └──────────┘ └──────────┘

┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────┐
│ 어드민 파일   │ ──▶ │ 텍스트/표/   │ ──▶ │ S3 이미지    │ ──▶ │ Pinecone │
│ 업로드       │     │ 이미지 추출   │     │ 업로드 +     │     │ 벡터화   │
│ (PDF/DOCX/..)│     │              │     │ Vision 설명  │     │          │
└──────────────┘     └──────────────┘     └──────────────┘     └──────────┘
```

### 1단계: 크롤링

| 크롤러 | 대상 | 방식 | 출력 |
|--------|------|------|------|
| `faq_scraper.py` | LINE WORKS FAQ | sitemap → BeautifulSoup | `faq_lineworks.json` |
| `board_scraper.py` | 사내 게시판 | Playwright 로그인 자동화 + 첨부파일 추출 | `board_lineworks.json` |
| `eluocnc_scraper.py` | 회사 웹사이트 | sitemap + AJAX → BeautifulSoup | `eluocnc.json` |

### 2단계: 인덱스 빌드

```bash
python src/graph/build_index.py              # 전체 빌드
python src/graph/build_index.py --pinecone   # Pinecone만
python src/graph/build_index.py --graph      # 지식그래프만
```

### 3단계: 챗봇 실행

```bash
streamlit run src/app.py
```

---

## 기술 스택

| 영역 | 기술 |
|------|------|
| **AI 프레임워크** | PydanticAI |
| **LLM** | Claude Sonnet 4 (에이전트), Claude Haiku 4.5 (엔티티 추출) |
| **임베딩** | Sentence-Transformers (`paraphrase-multilingual-MiniLM-L12-v2`, 384차원) |
| **벡터 DB** | Pinecone Serverless (cosine, AWS us-east-1) |
| **지식그래프** | NetworkX |
| **UI** | Streamlit |
| **크롤링** | BeautifulSoup, Playwright |
| **파일 추출** | pdfplumber, PyMuPDF, python-docx, openpyxl, python-pptx |
| **이미지 저장** | AWS S3 (boto3) |
| **이미지 분석** | Claude Haiku Vision API |

---

## 환경 설정

`.env.example`을 `.env`로 복사하고 필요한 키를 설정합니다:

```bash
ANTHROPIC_API_KEY=sk-...       # Claude API (에이전트 + 엔티티 추출 + 이미지 설명)
PINECONE_API_KEY=...           # Pinecone 벡터 DB

# AWS S3 — 문서 이미지 저장 (선택, 미설정 시 텍스트만 인제스트)
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_S3_REGION=ap-northeast-2
AWS_S3_BUCKET_NAME=eluo-docs
AWS_S3_PUBLIC_URL=                    # CloudFront URL (선택)
```

---

## 빠른 시작

```bash
# 1. 의존성 설치
pip install -r requirements.txt
playwright install chromium  # 게시판 크롤러 사용 시

# 2. 데이터 크롤링
python src/scraper/faq_scraper.py
python src/scraper/board_scraper.py
python src/scraper/eluocnc_scraper.py

# 3. 인덱스 빌드
python src/graph/build_index.py

# 4. 챗봇 실행
streamlit run src/app.py
```
