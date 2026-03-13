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
    for key in ("ANTHROPIC_API_KEY", "PINECONE_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
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
        concepts = r.get("related_entities", [])
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
        "## 도구 사용 전략 (속도 최적화)\n"
        "**대부분의 질문은 search_faq 한 번이면 충분합니다.** 도구 호출을 최소화하세요.\n\n"
        "1. **구체적 질문** (예: '비밀번호 변경 방법', '연차 신청은?')\n"
        "   → search_faq 1회 호출로 답변. 추가 도구 호출 불필요.\n\n"
        "2. **탐색적 질문** (예: 'LINE WORKS 메일 관련 기능')\n"
        "   → search_faq 1회로 먼저 시도. 결과가 부족할 때만 explore_topic 추가.\n"
        "   → explore_topic은 '~에 대해 알려줘', '~와 관련된 기능은?' 같은 명시적 탐색 질문에만 사용.\n\n"
        "3. **넓은 탐색 질문** (예: '프로젝트 알려줘', '회사 소개해줘')\n"
        "   → list_titles(source='eluocnc')로 목록 조회 → 대표 항목 get_item_detail\n\n"
        "4. **목록/통계 질문** → list_titles 또는 get_data_stats\n\n"
        "5. **특정 항목 상세 질문** → get_item_detail로 바로 상세 조회\n\n"
        "## 데이터 출처 구분\n"
        "- source='faq': LINE WORKS 도움말 FAQ\n"
        "- source='board': 사내 게시판 (규정, 업무가이드)\n"
        "- source='eluocnc': 회사 홈페이지 (회사 소개, 프로젝트, 블로그)\n\n"
        "## 답변 규칙\n"
        "- 답변은 항상 한국어로, 자연스럽고 친절한 톤으로 작성하세요.\n"
        "- 검색 결과를 그대로 나열하지 말고, 사용자의 질문 의도에 맞게 정리하여 답변하세요.\n"
        "- 답변 끝에 출처(FAQ/게시판/회사 홈페이지)와 관련 URL을 함께 제공하세요.\n"
        "- 검색 결과가 없거나 관련이 없는 경우, 솔직하게 모른다고 답변하세요.\n"
        "- **답변 마지막에 반드시 `[관련 주제: 주제1, 주제2, 주제3]` 형식으로 관련 주제 2~3개를 제안하세요.**\n"
        "  검색 결과의 관련 개념이나 연관 키워드에서 선택하세요."
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
