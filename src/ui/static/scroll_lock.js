// scroll_lock.js — 기본 설정만 담당 (이벤트 리스너, ScrollToBottom 제거)
(function() {
    var P = window.parent;
    var doc = P.document;
    if (P.__scrollLockApplied) return;
    P.__scrollLockApplied = true;

    P.__userScrolled = false;
    P.__autoScrollId = null;

    // 사용자 능동 스크롤 감지 (위로 스크롤만)
    doc.addEventListener('wheel', function(e) {
        if (e.deltaY < 0) P.__userScrolled = true;
    }, { capture: true, passive: true });
    doc.addEventListener('touchmove', function() {
        P.__userScrolled = true;
    }, { capture: true, passive: true });

    // ScrollToBottom 버튼 제거 (폴링)
    P.setInterval(function() {
        doc.querySelectorAll('[data-testid="ScrollToBottomContainer"]')
            .forEach(function(el) { el.remove(); });
    }, 1000);
})();
