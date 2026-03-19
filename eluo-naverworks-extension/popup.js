/**
 * Eluo NaverWorks Extension — Popup Script
 *
 * Extension 연결 상태와 마지막 전달 데이터를 표시한다.
 */
(function () {
  "use strict";

  var statusEl = document.getElementById("status");
  var statusText = document.getElementById("status-text");
  var dataContent = document.getElementById("data-content");
  var dataTimestamp = document.getElementById("data-timestamp");

  // Ask Eluo 탭(localhost:8501) 연결 확인
  chrome.tabs.query({ url: "http://localhost:8501/*" }, function (tabs) {
    if (tabs && tabs.length > 0) {
      statusEl.className = "status connected";
      statusText.textContent = "Ask Eluo 연결됨";
    } else {
      statusEl.className = "status disconnected";
      statusText.textContent = "Ask Eluo 연결 대기 중";
    }
  });

  // 마지막 전달 데이터 표시
  chrome.storage.session.get(["expenseData", "timestamp"], function (data) {
    if (!data.expenseData) return;

    var d = data.expenseData;
    var fields = [
      { label: "금액", value: d.amount ? d.amount + "원" : "-" },
      { label: "날짜", value: d.date || "-" },
      { label: "사용처", value: d.place || "-" },
      { label: "품목", value: d.item || "-" },
      { label: "비목", value: d.expenseCategory || "-" },
    ];

    var html = "";
    for (var i = 0; i < fields.length; i++) {
      html +=
        '<div class="data-row">' +
        '<span class="label">' + fields[i].label + "</span>" +
        '<span class="value">' + fields[i].value + "</span>" +
        "</div>";
    }
    dataContent.innerHTML = html;

    if (data.timestamp) {
      var date = new Date(data.timestamp);
      var h = date.getHours();
      var period = h < 12 ? "오전" : "오후";
      var dh = h <= 12 ? h : h - 12;
      if (dh === 0) dh = 12;
      var m = ("0" + date.getMinutes()).slice(-2);
      dataTimestamp.textContent = period + " " + dh + ":" + m;
    }
  });
})();
