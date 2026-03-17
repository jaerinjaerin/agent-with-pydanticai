# 엘루오씨앤씨 홈페이지 Playwright 크롤러

## Context

엘루오씨앤씨 홈페이지(eluocnc.com) 데이터를 크롤링하여 챗봇 지식 베이스에 넣는다.
- 기존 스크래퍼는 sitemap + AJAX 기반이지만 sitemap에 누락된 페이지가 있음
- Playwright BFS 크롤링으로 네비게이션을 따라가며 모든 페이지를 자동 수집
- VectorRAG 전환은 이미 완료 (코드 변경 적용됨), 데이터만 넣으면 됨

---

## Step 1: `src/scraper/eluocnc_scraper.py` 리라이트

기존 requests+BeautifulSoup → Playwright BFS 크롤러로 교체.

### 3단계 크롤링

**Phase 1 — BFS 링크 탐색 + 콘텐츠 추출**
- `https://www.eluocnc.com/ko/main.asp`에서 시작
- `collections.deque`로 BFS, `visited: set`으로 중복 방지
- 각 페이지에서: 콘텐츠 추출 → `<a href>` 링크 수집 → 큐에 추가
- `/ko/` 내부 링크만 따라감, 외부/정적 리소스 제외
- 최대 200 URL 캡 (무한 루프 방지)

**Phase 2 — AJAX 페이지네이션 (works + ideas)**
- `page.request.post()`로 AJAX 엔드포인트 호출
- `ajax.works_list.asp`: 프로젝트 상세 URL 수집
- `ajax.idea_list.asp`: 블로그 글 URL 수집 (`frmView('NNN')` 패턴)
- BFS에서 이미 방문한 URL은 건너뜀

**Phase 3 — PDF 다운로드 + 텍스트 추출**
- `PDF_URLS` 상수 + BFS 중 발견된 PDF URL
- `file_extractor.extract_text()`로 텍스트 추출 (기존 로직 재사용)

### 주요 함수

```
normalize_url(url) → str            # fragment 제거, 정규화
is_valid_internal_url(url) → bool   # /ko/ 내부 링크 필터
extract_page_content_pw(page) → (title, content)  # Playwright에서 콘텐츠 추출
collect_links_from_page(page) → list[str]          # 페이지 내 링크 수집
crawl_ajax_works(page) → list[str]  # works AJAX 페이지네이션
crawl_ajax_ideas(page) → list[str]  # ideas AJAX 페이지네이션
scrape_page_pw(page, url) → dict|None  # 단일 페이지 크롤링
crawl_bfs(page) → (results, pdf_urls) # BFS 메인 루프
scrape_all() → list[dict]           # 전체 오케스트레이션
```

### 유지하는 것
- `detect_category()`, `scrape_pdf()`, `save_results()`, `main()`
- `PDF_URLS`, `CATEGORY_MAP`, `OUTPUT_PATH` 상수
- 출력 포맷: `{url, title, content, category, source, scraped_at}`

### 제거하는 것
- `fetch_sitemap_urls()`, `collect_all_urls()`, `scrape_page()`
- `SITEMAP_URL`, `HEADERS` (requests용, PDF에서만 유지)
- `fetch_ajax_works_urls()`, `fetch_ajax_idea_urls()` → Playwright 버전으로 교체

### 콘텐츠 추출 전략
- `page.goto(url, wait_until="networkidle", timeout=30000)`
- timeout시 `domcontentloaded` + 2초 대기 폴백
- `page.content()` → BeautifulSoup로 파싱 (기존 셀렉터 재사용)
- Playwright가 인코딩(EUC-KR 등)을 자동 처리

### 에러 처리
- 페이지 실패 → 로그 후 스킵 (기존 패턴)
- AJAX 실패 → 루프 중단
- 브라우저 크래시 → `try/finally`로 수집된 결과 저장

## Step 2: 크롤러 실행

```bash
python src/scraper/eluocnc_scraper.py
```
- `data/eluocnc.json` 생성, 수집된 페이지 수 확인

## Step 3: 인덱스 빌드

```bash
python src/graph/build_index.py
```
- 청킹 적용된 벡터 업로드, 청크 수 확인

## Step 4: 검색 테스트

```bash
python tests/compare_search.py
```
- "엘루오씨앤씨 사업 분야", "회사 연혁" 등 질문에 결과 반환 확인

---

## 수정 파일

| 파일 | 변경 |
|------|------|
| `src/scraper/eluocnc_scraper.py` | Playwright BFS 크롤러로 전면 리라이트 |

## 참조 파일 (변경 없음)

| 파일 | 용도 |
|------|------|
| `src/scraper/board_scraper.py` | Playwright 패턴 참조 |
| `src/scraper/file_extractor.py` | PDF 텍스트 추출 재사용 |
| `src/graph/build_index.py` | 다운스트림 — 청킹+임베딩 (이미 수정됨) |
| `src/agent/graph_database.py` | 다운스트림 — VectorRAG 검색 (이미 수정됨) |

## 검증

1. `data/eluocnc.json` 존재 + 페이지 수 확인 (기존 대비 증가 예상)
2. `python src/graph/build_index.py` — 청크 수 출력
3. `python tests/compare_search.py` — 엘루오씨앤씨 관련 질문 결과 확인
4. Streamlit UI 테스트 (`/test-ui`)
