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
   * + 버튼(영수증 업로드)을 chat input 안에 생성하고,
   * 파일 선택 후 파일명 badge를 표시한다.
   */
  function handleReceiptButton() {
    var chatInput = doc.querySelector('[data-testid="stChatInput"]');
    if (!chatInput) return;

    var btn = chatInput.querySelector('[data-testid="stChatInputSubmitButton"]');
    if (!btn) return;
    var btnWrapper = btn.parentElement;
    var innerContainer = btnWrapper ? btnWrapper.parentElement : null;
    if (!innerContainer) return;

    // + 버튼 생성 (1회)
    if (!innerContainer.querySelector('.receipt-upload-btn')) {
      var uploadBtn = doc.createElement('button');
      uploadBtn.className = 'receipt-upload-btn';
      uploadBtn.type = 'button';
      uploadBtn.textContent = '+';
      uploadBtn.title = '영수증 첨부';
      uploadBtn.addEventListener('click', function(e) {
        e.preventDefault();
        var fileInput = doc.querySelector(
          '[data-testid="stFileUploader"] input[type="file"]'
        );
        if (fileInput) fileInput.click();
      });
      innerContainer.insertBefore(uploadBtn, innerContainer.firstChild);
    }

    // 미리보기 썸네일 + 파일명 badge 감시
    var uploadBtnEl = innerContainer.querySelector('.receipt-upload-btn');
    var previewData = doc.getElementById('receipt-preview-data');
    var preview = innerContainer.querySelector('.receipt-preview');
    var badge = innerContainer.querySelector('.receipt-file-badge');

    if (previewData && previewData.dataset.src) {
      // 미리보기 컨테이너가 없으면 생성
      if (!preview) {
        preview = doc.createElement('div');
        preview.className = 'receipt-preview';

        var thumb = doc.createElement('img');
        thumb.className = 'receipt-preview-img';
        preview.appendChild(thumb);

        var nameSpan = doc.createElement('span');
        nameSpan.className = 'receipt-preview-name';
        preview.appendChild(nameSpan);

        var removeBtn = doc.createElement('button');
        removeBtn.className = 'receipt-preview-remove';
        removeBtn.type = 'button';
        removeBtn.textContent = '\u00d7';
        removeBtn.title = '\uc601\uc218\uc99d \uc81c\uac70';
        removeBtn.addEventListener('click', function(e) {
          e.preventDefault();
          // Streamlit file_uploader의 삭제 버튼 클릭
          var delBtn = doc.querySelector('[data-testid="stFileUploader"] button[aria-label]');
          if (delBtn) delBtn.click();
          // 미리보기 즉시 제거
          var p = innerContainer.querySelector('.receipt-preview');
          if (p) p.remove();
          var b = innerContainer.querySelector('.receipt-file-badge');
          if (b) b.remove();
        });
        preview.appendChild(removeBtn);

        innerContainer.insertBefore(preview, uploadBtnEl ? uploadBtnEl.nextSibling : innerContainer.firstChild);
      }

      // 이미지/이름 업데이트
      var img = preview.querySelector('.receipt-preview-img');
      if (img && img.src !== previewData.dataset.src) {
        img.src = previewData.dataset.src;
      }
      var name = preview.querySelector('.receipt-preview-name');
      if (name) name.textContent = previewData.dataset.name || '';

      // badge 제거 (미리보기가 대체)
      if (badge) badge.remove();
    } else {
      // 파일 없음 — 미리보기 제거
      if (preview) preview.remove();

      // 파일명 badge 폴백 (Streamlit 자체 파일명 표시)
      var uploaderEl = doc.querySelector('[data-testid="stFileUploader"]');
      if (uploaderEl) {
        var fileName = uploaderEl.querySelector('[data-testid="stFileUploaderFileName"]');
        if (fileName && fileName.textContent.trim()) {
          if (!badge) {
            badge = doc.createElement('span');
            badge.className = 'receipt-file-badge';
            innerContainer.insertBefore(badge, uploadBtnEl ? uploadBtnEl.nextSibling : innerContainer.firstChild);
          }
          badge.textContent = '\ud83d\udcce ' + fileName.textContent.trim();
        } else if (badge) {
          badge.remove();
        }
      }
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

  /**
   * #action-trigger div가 렌더링되면 #action-payload에 data-trigger 속성을 설정한다.
   * content_script.js(Chrome Extension)가 MutationObserver로 이 속성 변경을 감지한다.
   */
  function handleExpenseTrigger() {
    var trigger = doc.getElementById('action-trigger');
    var payload = doc.getElementById('action-payload');
    if (!trigger || !payload) return;
    // 이미 트리거된 상태면 스킵
    if (payload.getAttribute('data-trigger') && trigger.dataset.handled) return;
    trigger.dataset.handled = 'true';
    payload.setAttribute('data-trigger', Date.now().toString());
  }

  /**
   * Chrome Extension 설치 여부를 감지한다.
   * content_script.js가 <meta name="eluo-extension"> 태그를 삽입한다.
   */
  function checkExtensionInstalled() {
    var meta = doc.querySelector('meta[name="eluo-extension"]');
    var badge = doc.getElementById('eluo-ext-badge');

    if (meta) {
      // 설치됨 — 배지 표시
      if (!badge) {
        badge = doc.createElement('div');
        badge.id = 'eluo-ext-badge';
        badge.style.cssText = 'position:fixed;bottom:8px;right:8px;background:#28a745;color:#fff;font-size:11px;padding:2px 8px;border-radius:10px;z-index:9999;opacity:0.7;';
        badge.textContent = '🔌 Extension 연결됨';
        doc.body.appendChild(badge);
      }
    } else {
      if (badge) badge.remove();
    }
  }

  /**
   * 로딩 오버레이를 페이드아웃 후 제거한다.
   * selectbox가 chatInput 안에 이동되었을 때 "준비 완료"로 판단.
   */
  var overlayRemoved = false;
  function dismissLoadingOverlay() {
    if (overlayRemoved) return;
    var chatInput = doc.querySelector('[data-testid="stChatInput"]');
    var selectbox = chatInput && chatInput.querySelector('[data-testid="stSelectbox"]');
    if (!selectbox) return; // 아직 통합 안 됨

    overlayRemoved = true;
    var overlay = doc.getElementById('eluo-loading-overlay');
    if (overlay) {
      overlay.classList.add('fade-out');
      setTimeout(function() { overlay.remove(); }, 400);
    }
  }

  // 초기 실행 + Streamlit 리렌더 감시
  setTimeout(function() {
    integrate();
    handleReceiptButton();
    handleStreamingState();
    handleExpenseTrigger();
    checkExtensionInstalled();
    dismissLoadingOverlay();
  }, 200);

  var timer = null;
  var observer = new MutationObserver(function () {
    clearTimeout(timer);
    timer = setTimeout(function() {
      integrate();
      handleReceiptButton();
      handleStreamingState();
      handleExpenseTrigger();
      checkExtensionInstalled();
      dismissLoadingOverlay();
    }, 100);
  });
  observer.observe(doc.body, { childList: true, subtree: true });
})();
