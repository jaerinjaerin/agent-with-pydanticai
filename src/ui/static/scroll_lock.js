// scroll_lock.js — Streamlit 내장 auto-scroll 차단 + 스크롤 위치 기반 감지
(function() {
    var P = window.parent;
    var doc = P.document;
    if (P.__scrollLockApplied) return;
    P.__scrollLockApplied = true;

    P.__userScrolled = false;
    P.__autoScrollId = null;

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

    // ── 3. 스크롤 위치 기반 __userScrolled 감지 ──
    var BOTTOM_THRESHOLD = 100; // px

    function getScrollContainer() {
        return doc.querySelector('[data-testid="stAppScrollToBottomContainer"]');
    }

    function isNearBottom(container) {
        if (!container) return true;
        return container.scrollHeight - container.scrollTop - container.clientHeight < BOTTOM_THRESHOLD;
    }

    // scroll 이벤트로 바닥 근처인지 판단
    doc.addEventListener('scroll', function() {
        var c = getScrollContainer();
        if (!c) return;
        if (isNearBottom(c)) {
            P.__userScrolled = false;
        }
    }, { capture: true, passive: true });

    // wheel 위로 스크롤 시 즉시 userScrolled 설정
    doc.addEventListener('wheel', function(e) {
        if (e.deltaY < 0) {
            P.__userScrolled = true;
            // interval 즉시 중단
            if (P.__autoScrollId) {
                P.clearInterval(P.__autoScrollId);
                P.__autoScrollId = null;
            }
        }
    }, { capture: true, passive: true });

    // touch 스크롤
    doc.addEventListener('touchmove', function() {
        P.__userScrolled = true;
        if (P.__autoScrollId) {
            P.clearInterval(P.__autoScrollId);
            P.__autoScrollId = null;
        }
    }, { capture: true, passive: true });

    // 스크롤바 드래그 감지
    var _scrollOnMouseDown = null;
    doc.addEventListener('mousedown', function() {
        var c = getScrollContainer();
        if (c) _scrollOnMouseDown = c.scrollTop;
    }, { capture: true, passive: true });
    doc.addEventListener('mouseup', function() {
        var c = getScrollContainer();
        if (c && _scrollOnMouseDown !== null) {
            if (c.scrollTop < _scrollOnMouseDown - 5) {
                P.__userScrolled = true;
                if (P.__autoScrollId) {
                    P.clearInterval(P.__autoScrollId);
                    P.__autoScrollId = null;
                }
            }
            _scrollOnMouseDown = null;
        }
    }, { capture: true, passive: true });

    // 키보드 스크롤 감지
    doc.addEventListener('keydown', function(e) {
        if (e.key === 'PageUp' || e.key === 'ArrowUp' || e.key === 'Home') {
            P.__userScrolled = true;
            if (P.__autoScrollId) {
                P.clearInterval(P.__autoScrollId);
                P.__autoScrollId = null;
            }
        }
    }, { capture: true, passive: true });

    // ── 4. 스크롤 컨테이너에 직접 scroll 리스너 (바닥 복귀 감지) ──
    function attachScrollListener() {
        var c = getScrollContainer();
        if (!c || c.__scrollListenerAttached) return;
        c.__scrollListenerAttached = true;
        c.addEventListener('scroll', function() {
            if (isNearBottom(c)) {
                P.__userScrolled = false;
            }
        }, { passive: true });
    }

    // 컨테이너가 늦게 생성될 수 있으므로 주기적으로 attach 시도
    attachScrollListener();
    var attachRetry = P.setInterval(function() {
        attachScrollListener();
        var c = getScrollContainer();
        if (c && c.__scrollListenerAttached) P.clearInterval(attachRetry);
    }, 500);
})();
