/**
 * Eluo NaverWorks Extension — Background Service Worker
 *
 * content_script.js로부터 EXPENSE_DATA 메시지를 수신하여
 * chrome.storage.session에 저장하고, NaverWorks 탭을 열어 데이터를 전달한다.
 */
var PREFIX = "[Eluo BG]";

chrome.runtime.onMessage.addListener(function (message, sender, sendResponse) {
  if (message.type !== "EXPENSE_DATA") return false;

  console.log(PREFIX, "Received expense data from content script:", message.payload);

  // 세션 스토리지에 저장 (popup에서 읽기 + naverworks_filler에서 폴백)
  chrome.storage.session.set(
    {
      expenseData: message.payload,
      timestamp: Date.now(),
    },
    function () {
      if (chrome.runtime.lastError) {
        console.error(PREFIX, "Storage save failed:", chrome.runtime.lastError.message);
        sendResponse({ success: false, error: "storage_failed" });
        return;
      }
      console.log(PREFIX, "Data saved to session storage");

      // NaverWorks 탭 찾기 또는 새로 열기
      openNaverWorksTab(message.payload);
      sendResponse({ success: true });
    }
  );

  // 비동기 sendResponse를 위해 true 반환
  return true;
});

/**
 * NaverWorks 탭을 찾거나 새로 열고, 데이터를 전달한다.
 */
function openNaverWorksTab(payload) {
  chrome.tabs.query({ url: "https://*.worksmobile.com/*" }, function (tabs) {
    if (tabs && tabs.length > 0) {
      // 기존 탭 활성화
      var tab = tabs[0];
      chrome.tabs.update(tab.id, { active: true }, function () {
        sendToTab(tab.id, payload);
      });
    } else {
      // 새 탭 열기
      chrome.tabs.create(
        { url: "https://home.worksmobile.com" },
        function (tab) {
          // 탭 로드 완료 대기
          chrome.tabs.onUpdated.addListener(function listener(tabId, info) {
            if (tabId === tab.id && info.status === "complete") {
              chrome.tabs.onUpdated.removeListener(listener);
              // content script 로드 대기
              setTimeout(function () {
                sendToTab(tab.id, payload);
              }, 1000);
            }
          });
        }
      );
    }
  });
}

/**
 * 특정 탭에 FILL_FORM 메시지를 전송한다.
 */
function sendToTab(tabId, payload) {
  chrome.tabs.sendMessage(
    tabId,
    { type: "FILL_FORM", payload: payload },
    function (response) {
      if (chrome.runtime.lastError) {
        console.warn(
          PREFIX,
          "Failed to send to tab (script may not be loaded yet):",
          chrome.runtime.lastError.message
        );
        return;
      }
      console.log(PREFIX, "FILL_FORM sent, response:", response);
    }
  );
}
