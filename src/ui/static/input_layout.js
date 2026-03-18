/**
 * 모델 셀렉트박스를 chat input 컨테이너 안으로 DOM 이동시킨다.
 * Streamlit은 selectbox와 chat_input을 별도 DOM 영역에 렌더링하므로,
 * JS로 selectbox를 chat input 내부로 이동하여 통합 레이아웃을 구현.
 *
 * DOM 구조:
 *   stChatInput > div(outer) > div(inner) > [textarea wrapper, button wrapper]
 *   selectbox는 inner div의 자식으로 button wrapper 앞에 삽입.
 *
 * components.html() iframe 안에서 실행 → parent document 조작.
 */
(function () {
  var doc = window.parent.document;
  var moving = false;

  function integrate() {
    if (moving) return;

    var chatInput = doc.querySelector('[data-testid="stChatInput"]');
    var selectbox = doc.querySelector('[data-testid="stSelectbox"]');
    if (!chatInput || !selectbox) return;

    // 이미 이동됐으면 스킵
    if (chatInput.contains(selectbox)) return;

    moving = true;
    try {
      // 전송 버튼으로부터 삽입 위치 결정
      var btn = chatInput.querySelector(
        '[data-testid="stChatInputSubmitButton"]'
      );
      if (!btn) return;

      // btn → btnWrapper(div) → innerContainer(div)
      var btnWrapper = btn.parentElement;
      var innerContainer = btnWrapper ? btnWrapper.parentElement : null;
      if (!innerContainer) return;

      // 빈 원래 부모 숨기기
      var originalParent = selectbox.parentElement;

      // selectbox를 버튼 래퍼 앞에 삽입 (같은 부모의 자식으로)
      innerContainer.insertBefore(selectbox, btnWrapper);

      // 원래 위치의 빈 컨테이너 숨기기
      if (
        originalParent &&
        originalParent.getAttribute("data-testid") === "stElementContainer"
      ) {
        originalParent.style.display = "none";
      }
    } finally {
      moving = false;
    }
  }

  // 초기 실행 + Streamlit 리렌더 감시
  setTimeout(integrate, 200);

  var timer = null;
  var observer = new MutationObserver(function () {
    clearTimeout(timer);
    timer = setTimeout(integrate, 100);
  });
  observer.observe(doc.body, { childList: true, subtree: true });
})();
