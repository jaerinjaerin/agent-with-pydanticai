"""
LINE WORKS FAQ 에이전트.

VectorRAG 검색 툴을 갖춘 PydanticAI 에이전트로,
크롤링된 FAQ 데이터를 기반으로 사용자 질문에 답변한다.
"""

import os

from dotenv import load_dotenv
from pydantic_ai import Agent, RunContext, Tool
from pydantic_ai.models.anthropic import AnthropicModel

load_dotenv()

# Streamlit Cloud secrets → 환경변수 주입
try:
    import streamlit as st
    for key in ("ANTHROPIC_API_KEY", "PINECONE_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        if key in st.secrets and key not in os.environ:
            os.environ[key] = st.secrets[key]
except Exception:
    pass

from agent.graph_database import GraphRAGDatabase


def search_faq(ctx: RunContext[GraphRAGDatabase], query: str, source: str = "") -> str:
    """사용자 질문을 기반으로 벡터 검색을 수행합니다.

    Args:
        query: 검색 쿼리.
        source: 소스 필터. "eluocnc"(회사 홈페이지), "admin"(어드민), "FAQ"(사내게시판), ""(전체 검색).
    """
    results = ctx.deps.search(query, source=source)
    if not results:
        return "관련 FAQ를 찾을 수 없습니다."

    source_labels = {"eluocnc": "회사 홈페이지", "admin": "사내문서", "FAQ": "사내게시판"}
    output_parts = []
    for i, r in enumerate(results, 1):
        src_label = source_labels.get(r.get("source", "eluocnc"), r.get("source", ""))
        part = (
            f"[{i}] [{src_label}] {r['title']}\n"
            f"내용: {r['content']}\n"
            f"URL: {r['url']}\n"
            f"유사도: {r.get('rerank_score', r['score']):.3f}"
        )
        images = r.get("images", [])
        if images:
            part += f"\n첨부 이미지: {', '.join(images[:5])}"
        output_parts.append(part)
    return "\n\n".join(output_parts)


def list_titles(ctx: RunContext[GraphRAGDatabase], source: str = "", keyword: str = "") -> str:
    """등록된 항목들의 제목 목록을 반환합니다.

    Args:
        source: 필터할 출처. "eluocnc", "admin", 또는 ""(전체).
        keyword: 제목에 포함된 키워드로 필터 (선택사항).
    """
    items = ctx.deps.items
    if source:
        items = [item for item in items if item.get("source") == source]
    if keyword:
        keyword_lower = keyword.lower()
        items = [item for item in items if keyword_lower in item.get("title", "").lower()
                 or keyword_lower in item.get("content", "")[:200].lower()]

    if not items:
        source_label = {"eluocnc": "회사 홈페이지", "admin": "사내문서", "FAQ": "사내게시판"}.get(source, source)
        return f"{source_label} 데이터가 없습니다." if source else "등록된 데이터가 없습니다."

    source_labels = {"eluocnc": "회사 홈페이지", "admin": "사내문서", "FAQ": "사내게시판"}
    lines = []
    for i, item in enumerate(items, 1):
        src = source_labels.get(item.get("source", ""), "")
        title = item.get("title", "(제목 없음)")
        preview = item.get("content", "")[:80].replace("\n", " ").strip()
        line = f"{i}. [{src}] {title}"
        if preview:
            line += f" — {preview}..."
        lines.append(line)
    return "\n".join(lines)


def get_item_detail(ctx: RunContext[GraphRAGDatabase], title: str) -> str:
    """제목으로 특정 항목의 전체 내용을 반환합니다.

    Args:
        title: 조회할 항목의 제목 (부분 일치 검색).
    """
    title_lower = title.lower()
    for item in ctx.deps.items:
        if title_lower in item.get("title", "").lower():
            source_labels = {"eluocnc": "회사 홈페이지", "admin": "사내문서", "FAQ": "사내게시판"}
            source = source_labels.get(item.get("source", ""), item.get("source", ""))
            parts = [
                f"[{source}] {item.get('title', '')}",
                f"URL: {item.get('url', '없음')}",
                f"\n{item.get('content', '내용 없음')}",
            ]
            attachments = item.get("attachments", [])
            if attachments:
                att_names = [a.get("filename", a.get("name", "")) for a in attachments]
                parts.append(f"\n첨부파일: {', '.join(att_names)}")
            return "\n".join(parts)
    return f"'{title}'과(와) 일치하는 항목을 찾을 수 없습니다."


def get_data_stats(ctx: RunContext[GraphRAGDatabase]) -> str:
    """데이터 소스별 항목 수 등 통계 정보를 반환합니다."""
    items = ctx.deps.items
    total = len(items)
    eluocnc_count = sum(1 for item in items if item.get("source") == "eluocnc")
    admin_count = sum(1 for item in items if item.get("source") == "admin")
    faq_count = sum(1 for item in items if item.get("source") == "FAQ")

    return (
        f"전체 항목 수: {total}\n"
        f"- 회사 홈페이지: {eluocnc_count}건\n"
        f"- 사내문서: {admin_count}건\n"
        f"- 사내게시판: {faq_count}건"
    )


model = AnthropicModel("claude-sonnet-4-20250514")

faq_agent = Agent(
    model=model,
    deps_type=GraphRAGDatabase,
    system_prompt=(
        "당신의 이름은 '엘루오'입니다. 자기소개 시 '안녕하세요, 엘루오입니다.'처럼 간단하게만 하세요.\n"
        "엘루오씨앤씨, AI 도우미, 사내 도우미 같은 수식어는 붙이지 마세요.\n"
        "회사 홈페이지 데이터(회사 소개, 프로젝트, 블로그)를 기반으로 답변합니다.\n\n"
        "## 도구 사용 전략 (속도 최적화)\n"
        "**대부분의 질문은 search_faq 한 번이면 충분합니다.** 도구 호출을 최소화하세요.\n\n"
        "0. **인사/잡담** (예: '안녕하세요', '감사합니다', '뭐 할 수 있어?')\n"
        "   → 도구 호출 없이 바로 응답. 검색하지 마세요.\n"
        "   → 간단한 자기소개 + 도와드릴 수 있는 내용 안내 + '무엇을 도와드릴까요?' 로 마무리.\n\n"
        "1. **짧은 키워드/후속 질문** (예: '디지털 마케팅', '파라다이스 블로그', '메타버스')\n"
        "   → search_faq로 검색. get_item_detail은 정확한 제목을 알 때만 사용.\n\n"
        "2. **구체적 질문** (예: '회사 소개', '프로젝트 알려줘')\n"
        "   → search_faq 1회 호출로 답변. 추가 도구 호출 불필요.\n\n"
        "3. **탐색적 질문** (예: '마케팅 관련 프로젝트')\n"
        "   → search_faq 1회로 먼저 시도. 결과가 부족할 때만 search_faq를 다른 키워드로 추가 호출.\n\n"
        "4. **넓은 탐색 질문** (예: '프로젝트 알려줘', '회사 소개해줘')\n"
        "   → list_titles(source='eluocnc')로 목록 조회 → 대표 항목 get_item_detail\n\n"
        "5. **목록/통계 질문** → list_titles 또는 get_data_stats\n\n"
        "6. **특정 항목 상세 질문** → get_item_detail로 바로 상세 조회\n\n"
        "## 데이터 출처 구분 및 소스 필터\n"
        "- source='eluocnc': 회사 홈페이지 (회사 소개, 프로젝트, 블로그)\n"
        "- source='admin': 사내문서 (사내 규정, 업무가이드 등)\n"
        "- source='FAQ': 사내게시판 (공지사항, 사내업무가이드, 규정 및 서식, 경조사, 자판기, 출퇴근, 인사발령)\n"
        "- 사내 규정/업무가이드/비용정산/공지사항/자판기/출퇴근/경조사/인사/보안 등 사내생활 질문 → source='FAQ'\n"
        "- 회사 소개/프로젝트/블로그 질문 → source='eluocnc'\n"
        "- 모호한 질문 → source 미지정 (전체 검색)\n\n"
        "## 검색 결과 관련성 판단\n"
        "- 유사도 0.5 미만인 결과는 관련성이 낮을 수 있으니 신중히 판단하세요.\n"
        "- 검색 결과가 질문과 직접 관련 없으면 무시하세요.\n"
        "- 모든 결과가 무관하면 '관련 정보를 찾지 못했습니다'라고 솔직히 답변하세요.\n\n"
        "## 답변 규칙\n"
        "- 답변은 항상 한국어로, 친절하고 다정한 톤으로 작성하세요. (~해요, ~드릴게요 체)\n"
        "- 검색 결과를 그대로 나열하지 말고, 사용자의 질문 의도에 맞게 정리하여 답변하세요.\n"
        "- 답변 끝에 출처(FAQ/게시판/회사 홈페이지)와 관련 URL을 함께 제공하세요.\n"
        "- 검색 결과가 없거나 관련이 없는 경우, 솔직하게 모른다고 답변하세요.\n"
        "- **답변 마지막에 반드시 `[관련 주제: 주제1, 주제2, 주제3]` 형식으로 관련 주제 2~3개를 제안하세요.**\n"
        "  답변 본문에서 사용자가 다음에 탐색할 만한 구체적 키워드를 선택하세요.\n"
        "  답변에서 직접 언급하거나 추천한 키워드와 일치해야 합니다.\n\n"
        "## 이미지 제공 규칙\n"
        "- 검색 결과에 '첨부 이미지' 경로가 포함되어 있으면, 답변 본문 중 관련 위치에 `[IMAGE: 경로]` 형식으로 삽입하세요.\n"
        "- 예: `[IMAGE: data/board_images/비용정산신청_p1.png]`\n"
        "- 이미지가 여러 장이면 답변 흐름에 맞게 적절한 위치에 각각 삽입하세요.\n"
        "- 이미지 경로는 검색 결과에서 제공된 것만 사용하고, 임의로 만들지 마세요."
    ),
    tools=[
        Tool(search_faq, takes_ctx=True),
        Tool(list_titles, takes_ctx=True),
        Tool(get_item_detail, takes_ctx=True),
        Tool(get_data_stats, takes_ctx=True),
    ],
)


def get_graph_db() -> GraphRAGDatabase:
    """GraphRAG 데이터베이스를 로드하여 반환한다."""
    return GraphRAGDatabase().load()


def ask(question: str, message_history=None) -> str:
    """질문에 대한 답변을 반환한다."""
    print(f"[Model] {model.model_name}")
    db = get_graph_db()
    result = faq_agent.run_sync(
        user_prompt=question,
        deps=db,
        message_history=message_history,
    )
    return result.output


if __name__ == "__main__":
    answer = ask("비밀번호를 잊어버렸어요")
    print(answer)
