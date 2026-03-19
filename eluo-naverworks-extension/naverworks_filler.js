/**
 * Eluo NaverWorks Extension — NaverWorks Form Filler
 *
 * worksmobile.com 페이지에서 실행.
 * background.js로부터 FILL_FORM 메시지를 수신하여 비용처리 폼에 자동 입력한다.
 *
 * NOTE: NaverWorks DOM 셀렉터는 placeholder 값이며,
 * 실제 NaverWorks 비용처리 페이지 DOM 구조 확인 후 업데이트 필요.
 */
(function () {
  "use strict";

  var PREFIX = "[Eluo NaverWorks]";

  // ── 셀렉터 매핑 (NaverWorks DOM 확인 후 업데이트) ──
  var SELECTORS = {
    amount: 'input[name="amount"], input[placeholder*="금액"], input[data-field="amount"]',
    date: 'input[name="date"], input[placeholder*="날짜"], input[type="date"], input[data-field="date"]',
    place: 'input[name="place"], input[placeholder*="사용처"], input[data-field="place"], input[placeholder*="가맹점"]',
    item: 'input[name="item"], input[placeholder*="품목"], input[data-field="item"], textarea[placeholder*="내용"]',
    expenseCategory:
      'select[name="expenseCategory"], select[data-field="category"], select[placeholder*="비목"]',
    project: 'input[name="project"], input[placeholder*="프로젝트"], input[data-field="project"]',
    detail: 'input[name="detail"], input[placeholder*="상세"], input[data-field="detail"], textarea[placeholder*="상세"]',
    approver1: 'input[name="approver1"], input[placeholder*="승인자"], input[data-field="approver1"]',
    approver2: 'input[name="approver2"], input[placeholder*="합의자"], input[data-field="approver2"]',
  };

  console.log(PREFIX, "Form filler loaded on", window.location.href);

  /**
   * 입력 필드에 값을 설정하고 change 이벤트를 발생시킨다.
   */
  function setInputValue(element, value) {
    if (!element || !value) return false;

    // React/Vue 등 프레임워크 호환을 위해 nativeInputValueSetter 사용
    var nativeSetter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      "value"
    );
    if (nativeSetter && nativeSetter.set) {
      nativeSetter.set.call(element, value);
    } else {
      element.value = value;
    }

    // 이벤트 발생 (프레임워크가 변경을 감지하도록)
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }

  /**
   * select 요소에서 텍스트가 매칭되는 option을 선택한다.
   */
  function setSelectValue(element, text) {
    if (!element || !text) return false;

    var options = element.querySelectorAll("option");
    for (var i = 0; i < options.length; i++) {
      if (options[i].textContent.trim().includes(text)) {
        element.value = options[i].value;
        element.dispatchEvent(new Event("change", { bubbles: true }));
        return true;
      }
    }
    return false;
  }

  /**
   * 셀렉터 문자열(콤마 구분)에서 첫 번째 매칭 요소를 찾는다.
   */
  function findElement(selectorStr) {
    var selectors = selectorStr.split(",").map(function (s) {
      return s.trim();
    });
    for (var i = 0; i < selectors.length; i++) {
      try {
        var el = document.querySelector(selectors[i]);
        if (el) return el;
      } catch (e) {
        // 잘못된 셀렉터 무시
      }
    }
    return null;
  }

  /**
   * 폼 필드에 데이터를 입력한다.
   * payload 구조: { data: { date, amount, item, place, receiptImage }, profile: { expenseCategory, project, detail, approver1, approver2 } }
   */
  function fillForm(payload) {
    var data = payload.data || payload;  // 폴백: flat 구조도 지원
    var profile = payload.profile || {};
    console.log(PREFIX, "Filling form — data:", data, "profile:", profile);

    var results = {};

    // 금액
    var amountEl = findElement(SELECTORS.amount);
    results.amount = amountEl
      ? setInputValue(amountEl, data.amount)
      : false;

    // 날짜
    var dateEl = findElement(SELECTORS.date);
    results.date = dateEl ? setInputValue(dateEl, data.date) : false;

    // 사용처
    var placeEl = findElement(SELECTORS.place);
    results.place = placeEl
      ? setInputValue(placeEl, data.place)
      : false;

    // 품목
    var itemEl = findElement(SELECTORS.item);
    results.item = itemEl ? setInputValue(itemEl, data.item) : false;

    // 비목 — profile.expenseCategory 우선, data.expenseCategory 폴백
    var category = profile.expenseCategory || data.expenseCategory;
    if (category) {
      var categoryEl = findElement(SELECTORS.expenseCategory);
      if (categoryEl && categoryEl.tagName === "SELECT") {
        results.expenseCategory = setSelectValue(categoryEl, category);
      } else if (categoryEl) {
        results.expenseCategory = setInputValue(categoryEl, category);
      } else {
        results.expenseCategory = false;
      }
    }

    // 프로젝트 — profile.project 우선
    var project = profile.project || data.project;
    if (project) {
      var projectEl = findElement(SELECTORS.project);
      results.project = projectEl
        ? setInputValue(projectEl, project)
        : false;
    }

    // 상세 (profile)
    if (profile.detail) {
      var detailEl = findElement(SELECTORS.detail);
      results.detail = detailEl
        ? setInputValue(detailEl, profile.detail)
        : false;
    }

    // 승인자 1 (profile)
    if (profile.approver1) {
      var approver1El = findElement(SELECTORS.approver1);
      results.approver1 = approver1El
        ? setInputValue(approver1El, profile.approver1)
        : false;
    }

    // 승인자 2 (profile)
    if (profile.approver2) {
      var approver2El = findElement(SELECTORS.approver2);
      results.approver2 = approver2El
        ? setInputValue(approver2El, profile.approver2)
        : false;
    }

    console.log(PREFIX, "Fill results:", results);

    // 매칭되지 않은 필드가 있으면 경고
    var missed = Object.keys(results).filter(function (k) {
      return !results[k];
    });
    if (missed.length > 0) {
      console.warn(
        PREFIX,
        "Could not fill fields:",
        missed.join(", "),
        "— NaverWorks DOM 셀렉터 업데이트 필요"
      );
    }

    return results;
  }

  // 메시지 수신 리스너
  chrome.runtime.onMessage.addListener(function (message, sender, sendResponse) {
    if (message.type !== "FILL_FORM") return false;

    console.log(PREFIX, "Received FILL_FORM:", message.payload);

    var results = fillForm(message.payload);
    sendResponse({ success: true, results: results });
    return false;
  });

  // 페이지 로드 시 session storage에서 대기 중인 데이터 확인 (폴백)
  chrome.storage.session.get(["expenseData", "timestamp"], function (data) {
    if (!data.expenseData) return;

    // 30초 이내 데이터만 처리
    var age = Date.now() - (data.timestamp || 0);
    if (age > 30000) {
      console.log(PREFIX, "Stored data is stale, ignoring");
      return;
    }

    console.log(PREFIX, "Found pending expense data in storage");
    // 약간의 지연 후 입력 시도 (DOM 렌더링 대기)
    setTimeout(function () {
      fillForm(data.expenseData);
    }, 2000);
  });
})();
