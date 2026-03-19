/**
 * Eluo NaverWorks Extension — Content Script
 *
 * localhost:8501 (Streamlit) 페이지에서 실행.
 * #action-payload div의 data-trigger 속성 변경을 감시하여
 * 영수증 데이터를 background.js로 전달한다.
 *
 * 통신 방식: DOM 속성 변경 → MutationObserver → chrome.runtime.sendMessage
 * (content script와 page script는 격리된 월드이므로 CustomEvent 불가)
 */
(function () {
  "use strict";

  var PREFIX = "[Eluo Extension]";

  // Extension 설치 표시 — Streamlit JS(input_layout.js)가 이 meta 태그로 감지
  var meta = document.createElement("meta");
  meta.name = "eluo-extension";
  meta.content = "installed";
  document.head.appendChild(meta);
  console.log(PREFIX, "Content script loaded on", window.location.href);

  var lastTrigger = null;

  /**
   * #action-payload에서 JSON 데이터를 읽어 background로 전송한다.
   */
  function handleTrigger(payload) {
    var triggerValue = payload.getAttribute("data-trigger");
    if (!triggerValue || triggerValue === lastTrigger) return;
    lastTrigger = triggerValue;

    // data-payload 속성에서 JSON 읽기 (textContent 대신 — Streamlit HTML 파싱 호환)
    var jsonText = payload.getAttribute("data-payload");
    if (!jsonText) {
      // 폴백: textContent에서 읽기
      jsonText = payload.textContent.trim();
    }
    if (!jsonText) {
      console.warn(PREFIX, "Payload div is empty (no data-payload attr, no textContent)");
      return;
    }

    var data;
    try {
      data = JSON.parse(jsonText);
    } catch (e) {
      console.error(PREFIX, "Failed to parse payload JSON:", e);
      return;
    }

    console.log(PREFIX, "Received expense data:", data);

    // background.js로 전송
    chrome.runtime.sendMessage(
      { type: "EXPENSE_DATA", payload: data },
      function (response) {
        if (chrome.runtime.lastError) {
          console.error(PREFIX, "Send failed:", chrome.runtime.lastError.message);
          showStatus(payload, "error", "전송 실패");
          return;
        }
        console.log(PREFIX, "Data sent to background:", response);
        showStatus(payload, "success", "NaverWorks로 전달됨");
      }
    );
  }

  /**
   * 페이로드 div 옆에 상태 표시를 추가한다.
   */
  function showStatus(payload, type, message) {
    // 기존 상태 제거
    var existing = document.getElementById("eluo-ext-status");
    if (existing) existing.remove();

    var status = document.createElement("div");
    status.id = "eluo-ext-status";
    status.style.cssText =
      "padding:6px 12px;margin:4px 0;border-radius:4px;font-size:13px;display:inline-block;";

    if (type === "success") {
      status.style.background = "#d4edda";
      status.style.color = "#155724";
      status.textContent = "\u2713 " + message;
    } else {
      status.style.background = "#f8d7da";
      status.style.color = "#721c24";
      status.textContent = "\u2717 " + message;
    }

    payload.parentElement.insertBefore(status, payload.nextSibling);

    // 5초 후 자동 제거
    setTimeout(function () {
      if (status.parentElement) status.remove();
    }, 5000);
  }

  // MutationObserver: #action-payload의 data-trigger 속성 변경 감시
  var observer = new MutationObserver(function (mutations) {
    for (var i = 0; i < mutations.length; i++) {
      var m = mutations[i];
      // 속성 변경 감지
      if (
        m.type === "attributes" &&
        m.attributeName === "data-trigger" &&
        m.target.id === "action-payload"
      ) {
        handleTrigger(m.target);
        return;
      }
      // 새 노드 추가 감지 (Streamlit rerun 시 DOM 재생성)
      if (m.type === "childList" && m.addedNodes.length) {
        var payload = document.getElementById("action-payload");
        if (payload && payload.getAttribute("data-trigger")) {
          handleTrigger(payload);
          return;
        }
      }
    }
  });

  observer.observe(document.body, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ["data-trigger"],
  });

  // 초기 체크 (페이지 로드 시 이미 트리거가 있을 수 있음)
  var existingPayload = document.getElementById("action-payload");
  if (existingPayload && existingPayload.getAttribute("data-trigger")) {
    handleTrigger(existingPayload);
  }
})();
