"""
Ask Eluo — Streamlit 멀티페이지 엔트리포인트.

pages/ 디렉토리 대신 st.navigation(position="hidden")으로 라우팅하여
사이드바 FOUC(깜빡임)를 완전히 제거한다.
"""

import streamlit as st

st.set_page_config(page_title="Ask Eluo", page_icon="💬", layout="wide")

# ── 멀티페이지 라우팅 (사이드바 없이) ──
_chat_page = st.Page("chat_page.py", title="Ask Eluo", icon="💬", default=True)
_admin_page = st.Page("pages/admin.py", title="문서 관리", icon="📄")
_nav = st.navigation([_chat_page, _admin_page], position="hidden")
_nav.run()
