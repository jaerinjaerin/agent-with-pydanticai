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

  /**
   * "중지" 트리거 버튼을 찾아 숨긴다. (Streamlit widget이라 CSS만으로 숨기기 어려움)
   */
  function hideStopTrigger() {
    var buttons = doc.querySelectorAll('button[kind="secondary"]');
    for (var i = 0; i < buttons.length; i++) {
      if (buttons[i].textContent.trim() === '중지') {
        // stElementContainer까지 올라가서 숨기기
        var el = buttons[i];
        while (el && (!el.getAttribute || el.getAttribute('data-testid') !== 'stElementContainer')) {
          el = el.parentElement;
        }
        if (el) el.style.display = 'none';
        break;
      }
    }
  }

  /**
   * 숨겨진 "중지" Streamlit 버튼을 찾아 클릭한다.
   * 이것이 Streamlit의 스크립트를 중단/rerun 시킨다 (새로고침 없이).
   */
  function clickStopTrigger() {
    var buttons = doc.querySelectorAll('button[kind="secondary"]');
    for (var i = 0; i < buttons.length; i++) {
      if (buttons[i].textContent.trim() === '중지') {
        buttons[i].click();
        return true;
      }
    }
    return false;
  }

  /**
   * 스트리밍 중 submit 버튼을 정지 버튼으로 교체한다.
   * Python에서 #eluo-streaming hidden div가 렌더링되면 스트리밍 상태.
   */
  function handleStreamingState() {
    var isStreaming = !!doc.querySelector('#eluo-streaming');
    var chatInput = doc.querySelector('[data-testid="stChatInput"]');
    if (!chatInput) return;

    // 중지 트리거 버튼은 항상 숨기기
    hideStopTrigger();

    var submitBtn = chatInput.querySelector('[data-testid="stChatInputSubmitButton"]');
    var stopBtn = doc.getElementById('eluo-stop-btn');

    if (isStreaming) {
      // submit 버튼 숨기기
      if (submitBtn) submitBtn.style.display = 'none';

      // 정지 버튼이 없으면 생성
      if (!stopBtn) {
        var container = submitBtn ? submitBtn.parentElement : null;
        if (container) {
          stopBtn = doc.createElement('button');
          stopBtn.id = 'eluo-stop-btn';
          stopBtn.innerHTML = '⏹';
          stopBtn.title = '응답 중지';
          stopBtn.style.cssText = 'background:#dc3545;color:#fff;border:none;border-radius:50%;width:36px;height:36px;font-size:1.1rem;cursor:pointer;display:flex;align-items:center;justify-content:center;';
          stopBtn.onclick = function(e) {
            e.preventDefault();
            // 숨겨진 Streamlit 버튼을 클릭하여 스크립트 중단/rerun
            if (!clickStopTrigger()) {
              // fallback
              window.parent.location.reload();
            }
          };
          container.appendChild(stopBtn);
        }
      }

      // Enter 키 제출 차단
      var ta = chatInput.querySelector('textarea');
      if (ta && !ta._eluoBlocked) {
        ta._eluoBlocked = true;
        ta.addEventListener('keydown', function(e) {
          if (!doc.querySelector('#eluo-streaming')) return;
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            e.stopPropagation();
          }
        }, true);
      }
    } else {
      // 정지 버튼 제거
      if (stopBtn) stopBtn.remove();
      // submit 버튼 복원
      if (submitBtn) submitBtn.style.display = '';
    }
  }

  // 초기 실행 + Streamlit 리렌더 감시
  setTimeout(function() {
    integrate();
    handleStreamingState();
  }, 200);

  var timer = null;
  var observer = new MutationObserver(function () {
    clearTimeout(timer);
    timer = setTimeout(function() {
      integrate();
      handleStreamingState();
    }, 100);
  });
  observer.observe(doc.body, { childList: true, subtree: true });
})();
