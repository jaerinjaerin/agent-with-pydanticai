---
name: test-ui
description: 기능 구현 후 Playwright로 로컬 웹 UI를 자동 테스트. Streamlit(8501) 또는 Chainlit(8000) 서버를 감지하고, 페이지 로드·채팅 입력·응답 확인·스크린샷 캡처를 수행. 기능 구현이 끝나면 자동으로 호출하여 UI 동작을 검증.
---

# UI 테스트 스킬 (Playwright)

기능 구현이 완료된 후 Playwright를 사용하여 로컬 웹 UI의 동작을 검증한다.

## 언제 사용하는가

- 기능 구현 플랜의 **마지막 검증 단계**로 반드시 포함
- UI 관련 코드(Streamlit, Chainlit, CSS, JS)를 수정한 후
- 사용자가 `/test-ui`로 직접 호출할 때

## 워크플로우

### 1. 서버 감지 및 실행

로컬에서 실행 중인 서버를 확인한다:

```bash
# 실행 중인 서버 확인
lsof -i :8501 2>/dev/null  # Streamlit
lsof -i :8000 2>/dev/null  # Chainlit
```

서버가 없으면 적절한 서버를 백그라운드로 시작한다:

- **Streamlit**: `cd src && streamlit run app.py --server.port 8501 --server.headless true`
- **Chainlit**: `cd src && chainlit run chainlit_app.py --port 8000`

서버 시작 후 3초 대기.

### 2. 테스트 스크립트 작성 및 실행

`/tmp/test_ui.py`에 Playwright 테스트 스크립트를 작성한다.

기본 테스트 항목:
1. **페이지 로드** — 타이틀/헤더가 정상 렌더링되는지 확인
2. **채팅 입력** — 텍스트 입력 후 전송
3. **응답 대기** — 봇 응답이 나타나는지 확인 (최대 60초)
4. **시각적 검증** — 스크린샷 캡처하여 사용자에게 제시

```python
# 테스트 스크립트 기본 구조
from playwright.sync_api import sync_playwright
import time

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 720})

    # 페이지 로드
    page.goto("http://localhost:{port}")
    page.wait_for_load_state("networkidle")

    # 스크린샷: 초기 화면
    page.screenshot(path="/tmp/ui_01_initial.png")

    # 채팅 테스트 (구현에 따라 셀렉터 조정)
    # Streamlit: textarea[data-testid="stChatInputTextArea"]
    # Chainlit: .cl-textarea, textarea
    input_sel = "textarea"
    page.fill(input_sel, "테스트 질문입니다")
    page.keyboard.press("Enter")

    # 응답 대기
    time.sleep(30)  # 또는 특정 셀렉터 wait

    # 스크린샷: 응답 후
    page.screenshot(path="/tmp/ui_02_response.png", full_page=True)

    browser.close()
```

### 3. 결과 보고

- `/tmp/ui_01_initial.png` — 초기 화면 스크린샷을 사용자에게 보여줌
- `/tmp/ui_02_response.png` — 응답 후 스크린샷을 사용자에게 보여줌
- 에러가 있으면 콘솔 로그와 함께 보고

## 주의사항

- Playwright가 설치되어 있어야 한다: `playwright install chromium`
- WSL 환경에서는 `headless=True` 필수
- Streamlit의 경우 첫 로드 시 "Please wait..." 화면이 나올 수 있으므로 `networkidle` 대기
- 테스트 후 서버를 종료할지 사용자에게 확인
- 스크린샷 파일은 `/tmp/`에 저장하여 프로젝트를 오염시키지 않음
