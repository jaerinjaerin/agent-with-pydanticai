# CLAUDE.md

이 파일은 Claude Code (claude.ai/code)가 이 저장소에서 작업할 때 참고하는 안내서입니다.

## 프로젝트 개요

하나의 저장소에 두 개의 프로젝트가 있습니다:

1. **PydanticAI 튜토리얼** (`src/introduction.py`) — 구조화된 응답, 의존성 주입, 도구 통합, 메시지 히스토리 등 PydanticAI 에이전트 패턴을 순차적으로 보여주는 예제 모음. 대부분의 예제는 주석 처리되어 있으며, 주석을 해제하면 실행 가능. Dave Ebbelaar (Datalumina) 작성.
2. **엘루오씨앤씨 RAG 챗봇** — `eluocnc.com` 웹사이트를 크롤링하고, Hybrid RAG (VectorRAG + GraphRAG)로 검색한 뒤, PydanticAI + Claude 에이전트가 질문에 답변하며, Streamlit 채팅 UI로 제공.
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

`.env.example`을 `.env`로 복사하고, `ANTHROPIC_API_KEY`와 `PINECONE_API_KEY`를 설정해야 합니다. 문서 이미지 업로드 기능을 사용하려면 AWS S3 환경변수(`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_S3_BUCKET_NAME`, `AWS_S3_REGION`)도 설정합니다. S3 미설정 시 텍스트만 인제스트됩니다.

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

- **엘루오씨앤씨 크롤러** (`src/scraper/eluocnc_scraper.py`): BFS 링크 탐색 + AJAX 엔드포인트(`ajax.works_list.asp`, `ajax.idea_list.asp`)로 프로젝트/블로그 URL 수집 → 페이지 스크래핑 + PDF 텍스트 추출 → `data/eluocnc.json` 저장.
- **파일 추출기** (`src/scraper/file_extractor.py`): PDF/DOCX/XLSX/PPTX/MD/TXT/HWP 파일에서 텍스트, 표(마크다운 테이블), 임베디드 이미지를 추출. `extract_text()`(텍스트 전용, 하위호환)와 `extract_content()`(`ExtractionResult` 반환 — 텍스트+이미지) 두 가지 인터페이스 제공.
- **S3 이미지 저장** (`src/storage/s3_storage.py`): AWS S3에 이미지 업로드/삭제. `boto3` 사용. 환경변수 `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_S3_BUCKET_NAME`, `AWS_S3_REGION` 필요. `AWS_S3_PUBLIC_URL`로 CloudFront 커스텀 도메인 지정 가능. 미설정 시 이미지 처리를 건너뛰고 텍스트만 인제스트.
- **이미지 설명 생성** (`src/graph/image_describer.py`): Claude Vision API로 이미지 분석 → 한국어 텍스트 설명 생성. `describe_image()`(파일 경로), `describe_image_bytes()`(바이트 데이터) 두 가지 입력 방식 지원. 설명은 `data/image_descriptions.json`에 캐시.
- **임베딩 모듈** (`src/graph/embedding_index.py`): Pinecone Integrated Index (`multilingual-e5-large`)로 서버사이드 임베딩 생성/검색. 텍스트만 전송하면 Pinecone이 임베딩 처리.
- **그래프 빌더** (`src/graph/graph_builder.py`): Claude API로 문서에서 엔티티/관계 추출. Pydantic `DocumentGraphExtraction` 스키마로 구조화. 임베딩 유사도 기반 엔티티 중복 해결. NetworkX 그래프 구축.
- **빌드 스크립트** (`src/graph/build_index.py`): JSON 데이터 → 임베딩 + Pinecone 업로드 + 엔티티/관계 추출 + 지식그래프 구축. 최초 1회 실행.
- **인제스트 파이프라인** (`src/graph/ingest.py`): 단건 문서 인제스트. `ingest_document()`(텍스트 전용), `ingest_document_with_media()`(텍스트+이미지 — S3 업로드 → Vision 설명 생성 → 벡터화 통합 파이프라인). `ingest_images()`로 이미지 설명을 별도 벡터로 Pinecone에 저장 (metadata.image_path에 S3 URL).
- **GraphRAG 데이터베이스** (`src/agent/graph_database.py`): `GraphRAGDatabase` dataclass. Hybrid 검색 = 벡터 검색(Pinecone) + 그래프 검색(NetworkX 1-2홉 탐색). Reciprocal Rank Fusion으로 결과 합산. 이미지 타입 벡터 검색 시 S3 URL 또는 로컬 경로 자동 수집.
- **에이전트** (`src/agent/faq_agent.py`): `GraphRAGDatabase`를 deps로 사용. `search_faq`(하이브리드 검색), `explore_topic`(그래프 탐색), `list_titles`, `get_item_detail`, `get_data_stats` 도구 제공. `AnthropicModel("claude-sonnet-4-20250514")`와 한국어 시스템 프롬프트 사용.
- **UI** (`src/app.py`): Streamlit 채팅 인터페이스. `_render_message_with_images()`가 `[IMAGE: ...]` 패턴을 파싱하여 S3 URL(http/https) 또는 로컬 경로 이미지를 인라인 렌더링. BadRequestError 발생 시 히스토리 없이 재시도하는 폴백 처리 포함.
- **어드민 페이지** (`src/pages/admin.py`): 문서 등록/수정/삭제. 파일 업로드 시 `extract_content()`로 텍스트+이미지 추출 → S3 업로드 → Claude Vision 설명 생성 → 벡터화 6단계 파이프라인. 문서 삭제 시 S3 이미지도 함께 정리. JSON에 `attachments` 메타데이터 저장.

데이터 흐름: (eluocnc.com → 웹사이트 크롤러 → `eluocnc.json`) + (어드민 등록 → `admin_documents.json`) → `build_index.py` → (Pinecone 벡터 인덱스 + `knowledge_graph.json`) → `GraphRAGDatabase.load()` → Hybrid 검색 → Claude 답변 생성. 어드민 파일 업로드 시 이미지는 AWS S3에 저장되고, 이미지 설명은 별도 벡터로 Pinecone에 인덱싱.

## 알려진 이슈

- PydanticAI는 초기 베타 단계이며, API가 변경될 수 있음
- 도구 사용 시 `message_history`가 400 BadRequestError를 유발할 수 있음 (tool response 메시지 누락) — Streamlit UI에서 폴백 재시도로 처리
- PydanticAI에서 temperature 등 모델 파라미터 조정 불가
- Claude API 비용: 엔티티 추출 시 문서당 1회 API 호출. 문서 수에 비례
- Pinecone Integrated Index (`eluocnc-faq-v2`)는 `multilingual-e5-large` 모델로 서버사이드 임베딩 사용. 로컬 임베딩 모델 불필요
- Pinecone 무료 티어: Starter 플랜에서 1개 인덱스, 100K 벡터까지 무료
- Pinecone 검색은 인터넷 연결 필요. 연결 실패 시 벡터 검색 비활성화 (그래프 검색만 동작)
- AWS S3 미설정 시 파일 업로드의 이미지 추출/저장이 건너뛰어짐 (텍스트/표만 인제스트)
- 이미지 설명 생성: Claude Haiku Vision API 호출. 이미지 수에 비례한 API 비용 발생
- 파일 추출 시 2KB 미만 이미지는 아이콘/스페이서로 간주하여 스킵
