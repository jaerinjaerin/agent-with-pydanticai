# Trouble Shooting

---

## 2026-03-16 | Streamlit st.status() 내부 메시지 누적 문제

### 문제

문서 등록/수정 시 `st.status()` 안에서 `st.write()`로 단계별 진행 상태를 출력하면, 텍스트가 계속 아래로 쌓여 "저장 중..."과 "저장 완료"가 동시에 표시됨.

```
1️⃣ 텍스트 준비 완료
2️⃣ JSON 저장 중...
✅ JSON 저장 완료          ← "중..."과 "완료"가 동시에 보임
3️⃣ 임베딩 & Pinecone 업로드 중...
4️⃣ 엔티티 추출 & 지식그래프 업데이트 중...
✅ 5개 청크 업로드 완료
```

### 원인

`st.write()`는 호출할 때마다 새로운 텍스트 요소를 추가한다. 한 번 렌더링된 텍스트를 덮어쓰는 기능이 없으므로, 이전 상태 메시지가 그대로 남는다.

### 해결

`st.empty()`로 플레이스홀더를 미리 만들고, 같은 슬롯에 `.write()`를 호출하면 기존 내용을 덮어쓴다.

```python
with st.status("문서 등록 중...", expanded=True) as status:
    step1 = st.empty()
    step2 = st.empty()

    step1.write("✅ 텍스트 준비 완료")

    step2.write("⏳ JSON 저장 중...")
    # ... 작업 수행 ...
    step2.write("✅ JSON 저장 완료")   # 같은 슬롯을 덮어씀
```

### 수정 파일

- `src/pages/admin.py` — 문서 등록(line ~460), 문서 수정(line ~191) 두 곳

---

## 2026-03-16 | 모바일에서 관련 주제 버튼이 잘려서 보이는 문제

### 문제

채팅 페이지의 "관련 주제" 버튼들이 모바일(375px 등 좁은 화면)에서 한 줄로 고정되어 화면 밖으로 넘침. 버튼이 잘리거나 스크롤이 필요한 상태.

### 원인

CSS에서 버튼 컨테이너에 `flex-wrap: nowrap !important`, 개별 버튼에 `white-space: nowrap !important`가 적용되어 있었음. 데스크톱에서는 충분한 너비가 있어 문제가 없지만, 모바일에서는 줄바꿈이 불가능.

```css
/* 데스크톱용 — 줄바꿈 금지 */
[data-testid="stVerticalBlock"]:has(.related-topics-row) [data-testid="stHorizontalBlock"] {
    flex-wrap: nowrap !important;
}
[data-testid="stVerticalBlock"]:has(.related-topics-row) button {
    white-space: nowrap !important;
}
```

### 해결

`@media (max-width: 768px)` 미디어 쿼리 안에서 `flex-wrap: wrap`과 `white-space: normal`로 오버라이드.

```css
@media (max-width: 768px) {
    [data-testid="stVerticalBlock"]:has(.related-topics-row) [data-testid="stHorizontalBlock"] {
        flex-wrap: wrap !important;
    }
    [data-testid="stVerticalBlock"]:has(.related-topics-row) button {
        white-space: normal !important;
    }
}
```

### 수정 파일

- `src/ui/static/style.css` — `@media (max-width: 768px)` 블록에 2개 룰 추가

---

## 2026-03-16 | Playwright 테스트에서 Streamlit 멀티페이지 URL 접근 실패

### 문제

Playwright로 어드민 페이지 테스트 시 한글 URL 인코딩(`/%EB%AC%B8%EC%84%9C_%EA%B4%80%EB%A6%AC`)으로 접근하면 "Page not found" 에러 발생.

### 원인

Streamlit 멀티페이지 앱의 URL 라우팅은 파일명 기반이다. `src/pages/admin.py` 파일의 경우 `/admin`으로 접근해야 하며, 한글 페이지 타이틀(`문서_관리`)을 URL로 사용하면 라우팅에 실패한다.

### 해결

파일명 기반 경로 `/admin`으로 직접 접근.

```python
# Before (실패)
page.goto("http://localhost:8501/%EB%AC%B8%EC%84%9C_%EA%B4%80%EB%A6%AC")

# After (성공)
page.goto("http://localhost:8501/admin")
```

### 참고

Streamlit 멀티페이지 앱의 URL은 `pages/` 디렉토리의 **파일명**(확장자 제외)으로 결정된다. `st.set_page_config(page_title=...)`의 타이틀과는 무관.

---

## 2026-03-16 | Playwright 버튼 셀렉터가 의도하지 않은 요소를 클릭

### 문제

`page.locator("button:has-text('등록')")` 셀렉터로 등록 버튼을 찾았지만, 실제로는 탭 버튼(`📤 새 문서 등록`)이 먼저 매칭되어 클릭됨. 폼 제출이 아닌 탭 전환이 발생.

### 원인

`:has-text()` 셀렉터는 부분 일치(substring match)로 동작한다. "등록"이라는 텍스트를 포함하는 모든 버튼이 매칭되며, 탭 버튼이 DOM 순서상 먼저 나타나므로 `.first`로 선택하면 탭 버튼이 클릭된다.

```
Button 0: '📤 새 문서 등록'  ← 탭 버튼 (먼저 매칭)
Button 1: '📥 등록'          ← 실제 제출 버튼
```

### 해결

이모지를 포함한 정확한 텍스트로 셀렉터를 구체화.

```python
# Before (탭 버튼이 먼저 매칭)
register_btn = page.locator("button:has-text('등록')")

# After (제출 버튼만 매칭)
register_btn = page.locator("button:has-text('📥 등록')")
```

### 참고

Playwright의 `:has-text()`는 부분 일치, `:text()`는 전체 일치. 버튼 텍스트가 겹칠 수 있는 경우 이모지나 고유 접두사를 포함하여 셀렉터를 작성하거나, `:text-is()`로 정확히 일치시킨다.
