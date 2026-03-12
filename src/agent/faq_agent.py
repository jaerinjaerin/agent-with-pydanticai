"""
LINE WORKS FAQ 에이전트.

Hybrid RAG (VectorRAG + GraphRAG) 검색 툴을 갖춘 PydanticAI 에이전트로,
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
    for key in ("ANTHROPIC_API_KEY", "PINECONE_API_KEY"):
        if key in st.secrets and key not in os.environ:
            os.environ[key] = st.secrets[key]
except Exception:
    pass

from agent.graph_database import GraphRAGDatabase


def search_faq(ctx: RunContext[GraphRAGDatabase], query: str) -> str:
    """사용자 질문을 기반으로 하이브리드 검색(벡터 + 그래프)을 수행합니다."""
    results = ctx.deps.hybrid_search(query)
    if not results:
        return "관련 FAQ를 찾을 수 없습니다."

    source_labels = {"faq": "FAQ", "board": "게시판", "eluocnc": "회사 홈페이지"}
    output_parts = []
    for i, r in enumerate(results, 1):
        source = source_labels.get(r.get("source", "faq"), r.get("source", ""))
        part = (
            f"[{i}] [{source}] {r['title']}\n"
            f"내용: {r['content']}\n"
            f"URL: {r['url']}\n"
            f"유사도: {r['score']:.3f}"
        )
        concepts = r.get("related_concepts", [])
        if concepts:
            part += f"\n관련 개념: {', '.join(concepts[:3])}"
        output_parts.append(part)
    return "\n\n".join(output_parts)


def explore_topic(ctx: RunContext[GraphRAGDatabase], topic: str) -> str:
    """그래프에서 특정 토픽의 연결된 엔티티와 문서를 탐색합니다.

    Args:
        topic: 탐색할 토픽/주제 (예: "메일", "드라이브", "경조금").
    """
    results = ctx.deps.explore_topic(topic)
    if not results:
        return f"'{topic}' 관련 엔티티를 찾을 수 없습니다."

    output_parts = []
    for r in results:
        neighbors_str = ""
        entity_neighbors = [n for n in r["neighbors"] if n["type"] == "ENTITY"]
        doc_neighbors = [n for n in r["neighbors"] if n["type"] == "DOCUMENT"]

        if entity_neighbors:
            related = [f"  - {n['name']} ({n['relation']})" for n in entity_neighbors[:5]]
            neighbors_str += "\n관련 개념:\n" + "\n".join(related)
        if doc_neighbors:
            neighbors_str += f"\n관련 문서: {len(doc_neighbors)}건"

        output_parts.append(
            f"[{r['type']}] {r['entity']}\n"
            f"설명: {r['description']}\n"
            f"유사도: {r['similarity']:.3f}"
            f"{neighbors_str}"
        )
    return "\n\n".join(output_parts)


def list_titles(ctx: RunContext[GraphRAGDatabase], source: str = "", keyword: str = "") -> str:
    """등록된 항목들의 제목 목록을 반환합니다.

    Args:
        source: 필터할 출처. "faq", "board", "eluocnc", 또는 ""(전체).
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
        source_label = {"faq": "FAQ", "board": "게시판", "eluocnc": "회사 홈페이지"}.get(source, source)
        return f"{source_label} 데이터가 없습니다." if source else "등록된 데이터가 없습니다."

    source_labels = {"faq": "FAQ", "board": "게시판", "eluocnc": "회사 홈페이지"}
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
            source_labels = {"faq": "FAQ", "board": "게시판", "eluocnc": "회사 홈페이지"}
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
    faq_count = sum(1 for item in items if item.get("source") == "faq")
    board_count = sum(1 for item in items if item.get("source") == "board")
    eluocnc_count = sum(1 for item in items if item.get("source") == "eluocnc")

    graph = ctx.deps.graph
    entity_count = sum(1 for _, d in graph.nodes(data=True) if d.get("node_type") == "ENTITY")
    edge_count = graph.number_of_edges()

    return (
        f"전체 항목 수: {total}\n"
        f"- FAQ: {faq_count}건\n"
        f"- 게시판: {board_count}건\n"
        f"- 회사 홈페이지: {eluocnc_count}건\n"
        f"\nKnowledge Graph:\n"
        f"- 엔티티: {entity_count}개\n"
        f"- 관계: {edge_count}개"
    )


model = AnthropicModel("claude-sonnet-4-20250514")

faq_agent = Agent(
    model=model,
    deps_type=GraphRAGDatabase,
    system_prompt=(
        "당신은 엘루오씨앤씨(디지털 마케팅 에이전시)의 사내 도우미입니다.\n"
        "LINE WORKS FAQ, 사내 규정/업무가이드(게시판), 회사 홈페이지 데이터를 기반으로 답변합니다.\n\n"
        "## 질문 의도 파악 및 도구 사용 전략\n"
        "사용자의 질문 의도를 먼저 파악한 후, 적합한 도구를 선택하세요:\n\n"
        "1. **구체적 질문** (예: '비밀번호 변경 방법', '연차 신청은?')\n"
        "   → search_faq로 하이브리드 검색 (벡터 + 그래프)\n"
        "   → 결과가 명확하면 바로 답변, 유사 항목이 여러 개면 목록 제시 후 선택 요청\n\n"
        "2. **탐색적 질문** (예: 'LINE WORKS 메일 관련 기능', '드라이브 기능 알려줘')\n"
        "   → explore_topic으로 그래프 탐색하여 관련 엔티티 파악\n"
        "   → 필요시 search_faq로 구체적 문서 검색 추가\n\n"
        "3. **넓은 탐색 질문** (예: '프로젝트 알려줘', '회사 소개해줘')\n"
        "   → list_titles(source='eluocnc')로 관련 항목 목록 조회\n"
        "   → 그중 대표적인 항목 몇 개를 get_item_detail로 상세 조회\n"
        "   → 조회 결과를 종합하여 자연스럽게 요약 답변\n\n"
        "4. **목록/통계 질문** (예: '게시판 글 목록', '데이터 몇 건이야?')\n"
        "   → list_titles 또는 get_data_stats 사용\n\n"
        "5. **특정 항목 상세 질문** (예: '이마트 프로젝트 자세히', '경동나비엔 프로젝트 알려줘')\n"
        "   → get_item_detail로 바로 상세 조회\n\n"
        "## 데이터 출처 구분\n"
        "- source='faq': LINE WORKS 도움말 FAQ\n"
        "- source='board': 사내 게시판 (규정, 업무가이드)\n"
        "- source='eluocnc': 회사 홈페이지 (회사 소개, 프로젝트, 블로그)\n\n"
        "## 답변 규칙\n"
        "- 답변은 항상 한국어로, 자연스럽고 친절한 톤으로 작성하세요.\n"
        "- 검색 결과를 그대로 나열하지 말고, 사용자의 질문 의도에 맞게 정리하여 답변하세요.\n"
        "- 답변 끝에 출처(FAQ/게시판/회사 홈페이지)와 관련 URL을 함께 제공하세요.\n"
        "- 검색 결과가 없거나 관련이 없는 경우, 솔직하게 모른다고 답변하세요.\n"
        "- 도구를 한 번만 호출해서 부족하면, 추가 도구를 호출하여 충분한 정보를 확보한 뒤 답변하세요."
    ),
    tools=[
        Tool(search_faq, takes_ctx=True),
        Tool(explore_topic, takes_ctx=True),
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
