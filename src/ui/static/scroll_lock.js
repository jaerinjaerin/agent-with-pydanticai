// scroll_lock.js — Streamlit 내장 auto-scroll 차단
(function() {
    var P = window.parent;
    var doc = P.document;

    if (P.__scrollLockApplied) return;
    P.__scrollLockApplied = true;

    // ── 1. Streamlit 내장 auto-scroll CSS 차단 ──
    var styleEl = doc.createElement('style');
    styleEl.textContent = [
        '[data-testid="stAppScrollToBottomContainer"] {',
        '  overflow-anchor: none !important;',
        '}',
        '[data-testid="ScrollToBottomContainer"] {',
        '  display: none !important;',
        '}'
    ].join('\n');
    doc.head.appendChild(styleEl);

    // ── 2. MutationObserver로 ScrollToBottom 요소 즉시 제거 ──
    var observer = new MutationObserver(function(mutations) {
        for (var i = 0; i < mutations.length; i++) {
            var added = mutations[i].addedNodes;
            for (var j = 0; j < added.length; j++) {
                var node = added[j];
                if (node.nodeType === 1) {
                    if (node.getAttribute && node.getAttribute('data-testid') === 'ScrollToBottomContainer') {
                        node.remove();
                    }
                    // 자식 노드에서도 검색
                    var children = node.querySelectorAll && node.querySelectorAll('[data-testid="ScrollToBottomContainer"]');
                    if (children) {
                        for (var k = 0; k < children.length; k++) {
                            children[k].remove();
                        }
                    }
                }
            }
        }
    });
    observer.observe(doc.body, { childList: true, subtree: true });

    // 초기 제거
    doc.querySelectorAll('[data-testid="ScrollToBottomContainer"]')
        .forEach(function(el) { el.remove(); });

})();
