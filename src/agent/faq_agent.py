"""
LINE WORKS FAQ 에이전트.

VectorRAG 검색 툴을 갖춘 PydanticAI 에이전트로,
크롤링된 FAQ 데이터를 기반으로 사용자 질문에 답변한다.
"""

import os

from dotenv import load_dotenv
from pydantic_ai import Agent, ModelRetry, RunContext, Tool, ToolDefinition
from pydantic_ai.models.anthropic import AnthropicModel

load_dotenv()

# Streamlit Cloud secrets → 환경변수 주입
try:
    import streamlit as st
    for key in ("ANTHROPIC_API_KEY", "SUPABASE_URL", "SUPABASE_SERVICE_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        if key in st.secrets and key not in os.environ:
            os.environ[key] = st.secrets[key]
except Exception:
    pass

from dataclasses import dataclass

from agent.graph_database import GraphRAGDatabase


@dataclass
class AgentDeps:
    graph_db: GraphRAGDatabase
    receipt_data: dict | None = None  # {"bytes": bytes, "mime": str, "name": str}


def search_faq(ctx: RunContext[AgentDeps], query: str, source: str = "") -> str:
    """사용자 질문을 기반으로 벡터 검색을 수행합니다.

    Args:
        query: 검색 쿼리.
        source: 소스 필터. "eluocnc"(회사 홈페이지), "admin"(어드민), "FAQ"(사내게시판), ""(전체 검색).
    """
    results = ctx.deps.graph_db.search(query, source=source)
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


def list_titles(ctx: RunContext[AgentDeps], source: str = "", keyword: str = "") -> str:
    """등록된 항목들의 제목 목록을 반환합니다.

    Args:
        source: 필터할 출처. "eluocnc", "admin", 또는 ""(전체).
        keyword: 제목에 포함된 키워드로 필터 (선택사항).
    """
    items = ctx.deps.graph_db.items
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


def get_item_detail(ctx: RunContext[AgentDeps], title: str) -> str:
    """제목으로 특정 항목의 전체 내용을 반환합니다.

    Args:
        title: 조회할 항목의 제목 (부분 일치 검색).
    """
    title_lower = title.lower()
    for item in ctx.deps.graph_db.items:
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


def get_data_stats(ctx: RunContext[AgentDeps]) -> str:
    """데이터 소스별 항목 수 등 통계 정보를 반환합니다."""
    items = ctx.deps.graph_db.items
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


def process_expense(ctx: RunContext[AgentDeps]) -> str:
    """영수증 이미지를 분석하여 비용처리 정보를 추출합니다. 이 도구가 목록에 보이면 이미지가 이미 첨부된 상태이므로 바로 호출하세요."""
    receipt = ctx.deps.receipt_data
    if not receipt:
        return "영수증 이미지가 첨부되지 않았습니다. 이미지를 업로드한 후 다시 요청해주세요."
    import json

    from agent.expense_processor import analyze_receipt

    expense = analyze_receipt(receipt["bytes"], receipt["mime"])
    return f"[EXPENSE_RESULT: {json.dumps(expense.model_dump(), ensure_ascii=False)}]"


async def _prepare_expense(
    ctx: RunContext[AgentDeps], tool_def: ToolDefinition
) -> ToolDefinition | None:
    """영수증 데이터가 있을 때만 도구를 활성화한다."""
    if ctx.deps.receipt_data is None:
        return None
    return tool_def


model = AnthropicModel("claude-sonnet-4-20250514")

faq_agent = Agent(
    model=model,
    deps_type=AgentDeps,
    system_prompt=(
        "당신의 이름은 '엘루오'입니다. 자기소개 시 '안녕하세요, 엘루오입니다.'처럼 간단하게만 하세요.\n"
        "엘루오씨앤씨, AI 도우미, 사내 도우미 같은 수식어는 붙이지 마세요.\n"
        "회사 홈페이지 데이터(회사 소개, 프로젝트, 블로그)를 기반으로 답변합니다.\n\n"
        "## 질문 유형 판단 및 답변 전략\n\n"
        "모든 질문에 대해, 답변하기 전에 유형을 판단하세요:\n\n"
        "**[사실 조회] — Closed RAG**\n"
        "정의: 회사 데이터에 정답이 있는 사실 확인형 질문\n"
        "예시: '대표 이름?', '회사 주소?', '프로젝트 목록?', '자판기 비밀번호?', '비용정산 서식?'\n"
        "→ 반드시 검색 결과에 기반해서만 답변하세요. 검색 결과에 없으면 '관련 정보를 찾지 못했습니다'라고 답변.\n"
        "→ 추측이나 일반 지식을 섞지 마세요.\n\n"
        "**[방법/절차/전략] — Open RAG**\n"
        "정의: 회사 업무·서비스 분야와 관련된 방법론, 전략, 프로세스, 노하우, 업계 사례 질문\n"
        "예시: '마케팅 전략 수립 방법?', 'UX 리서치 프로세스?', '웹사이트 SEO 개선?', '브랜드 컨설팅은 어떻게?', '다른 회사는 성과측정을 어떻게 해?', '업계 트렌드가 뭐야?'\n"
        "주의: '다른 회사에서는~', '업계에서는~', '레퍼런스~', '벤치마크~' 같은 질문은 업무 참고용이므로 Open RAG로 답변하세요.\n"
        "→ 먼저 search_faq로 회사 문서를 검색하세요.\n"
        "→ 검색 결과를 기반으로 하되, 회사 서비스 분야와 관련된 전문 지식을 보충하여 풍부하게 답변하세요.\n"
        "→ 보충 내용은 '일반적으로~', '업계에서는~' 등으로 구분하여 출처를 명확히 하세요.\n\n"
        "**[혼합형]**\n"
        "사실 확인과 방법론이 섞인 질문 (예: '우리 회사 SEO 현황이랑 개선 방법?')\n"
        "→ 사실 부분은 검색 결과에서, 방법 부분은 전문 지식을 보충하여 답변.\n"
        "→ '회사 문서에 따르면...' / '추가로, 일반적으로...' 식으로 구분.\n\n"
        "**[기타 질문] — Search-First**\n"
        "위 유형에 해당하지 않는 모든 질문 (기술, 코딩, 일반 지식 등)\n"
        "→ **반드시 먼저 search_faq로 검색하세요.** 사내문서(admin)에 관련 자료가 등록되어 있을 수 있습니다.\n"
        "→ 검색 결과에 관련 문서가 있으면: 해당 문서를 기반으로 답변하세요.\n"
        "→ 검색 결과가 없거나 전혀 무관하면: '엘루오 사내 문서에는 관련 내용이 없지만, 일반적인 내용을 안내해 드릴게요.' 라고 먼저 밝힌 후, LLM 자체 지식으로 답변하세요.\n\n"
        "## Open RAG 범위 제한\n"
        "- Open RAG는 회사의 서비스 분야(컨설팅, UX, 디자인, 개발, 마케팅, 브랜딩 등)와 관련된 전문 지식을 보충합니다.\n"
        "- 사내 문서에 없는 주제라도 검색 후 일반 지식으로 답변할 수 있습니다. 단, 사내 문서 기반이 아님을 명확히 하세요.\n\n"
        "## 도구 사용 전략 (속도 최적화)\n"
        "**중요: 도구는 최소한으로 호출하세요. 1회 호출로 충분하면 추가 호출하지 마세요.**\n"
        "첫 search_faq 결과가 질문과 무관하거나 답변에 필요한 정보가 없으면:\n"
        "- source 필터를 사용했다면 → source 없이 전체 검색으로 재시도\n"
        "- source 없이 검색했다면 → 키워드를 바꿔서 재시도 (동의어, 관련 업무 용어 확장)\n"
        "- 최대 1회 추가 검색을 허용합니다.\n\n"
        "0. **인사/잡담** → 도구 호출 없이 간단히 인사 응답.\n"
        "   - 회사와 무관해 보이는 질문이라도, 반드시 search_faq로 먼저 검색한 뒤 판단하세요.\n\n"
        "1. **일반 질문** → search_faq 1회 호출로 답변.\n"
        "   - 검색 결과의 내용이 잘려 보이거나 상세 정보가 필요하면 get_item_detail로 전체 내용을 추가 조회하세요.\n\n"
        "2. **목록/통계 질문** (예: '뭐가 있어?', '몇 개야?') → list_titles 또는 get_data_stats 1회.\n\n"
        "3. **특정 항목 상세** (정확한 제목을 알 때) → get_item_detail 1회.\n\n"
        "4. **비용처리/영수증 분석** → 사용자가 영수증 분석, 비용처리, 경비정산, 영수증 처리 등을 요청하면:\n"
        "   - process_expense 도구가 도구 목록에 존재하면, 이미지가 이미 첨부된 상태입니다. **무조건 process_expense를 호출하세요. 절대 안내 메시지를 보내지 마세요.**\n"
        "   - process_expense 도구가 도구 목록에 존재하지 않으면, 이미지가 첨부되지 않은 것입니다. '영수증 이미지를 먼저 첨부해주세요'라고 안내.\n"
        "   - 도구가 [EXPENSE_RESULT: {...}] 형식으로 반환하면, JSON에서 정보를 추출하여 아래 형식으로 정리:\n"
        "     **📋 영수증 분석 결과**\n"
        "     - **금액:** {amount}원\n"
        "     - **날짜:** {date}\n"
        "     - **사용처:** {place}\n"
        "     - **품목:** {item}\n"
        "     - **비목:** {expenseCategory}\n"
        "   - 답변 마지막에 반드시 원본 [EXPENSE_RESULT: {...}] 태그를 그대로 포함하세요. UI가 이를 파싱하여 NaverWorks 버튼을 표시합니다.\n"
        "   - 이 도구 사용 시 [관련 주제: ...] 태그는 생략해도 됩니다.\n\n"
        "## 검색 키워드 전략\n"
        "**중요: 사용자의 질문을 그대로 쿼리로 넣지 마세요.** 핵심 키워드를 추출·확장하여 검색하세요.\n"
        "- 직무/채용/복지/입사/인재/취업 관련 → query='채용 직무 소개 복지 채용프로세스'\n"
        "- 회사 소개/연혁/비전 → query='회사소개 엘루오씨앤씨 비전'\n"
        "- 프로젝트/포트폴리오/실적 → query='프로젝트' 또는 구체적 프로젝트명/고객사명\n"
        "- 특정 서비스/사업 분야 → query='컨설팅 UX 디자인 개발' 등 구체적 키워드\n"
        "- **구체적 물품/항목명** → 상위 카테고리와 함께 검색하세요:\n"
        "  - 예: '건전지 어디서 가져와' → query='사무용품 물품 관리 건전지 위치'\n"
        "  - 예: '프린터 어떻게 써' → query='프린터 사무기기 사용법 이용 방법'\n"
        "  - 예: '택시비 정산' → query='경비정산 교통비 택시비 비용 처리'\n"
        "- 질문이 모호하면 핵심 명사 2~3개 + 동의어/관련어를 조합하여 검색하세요.\n"
        "- 예: '직무가 뭐가 있어?' → query='채용 직무 소개 컨설팅 기획 디자인 개발'\n"
        "- 예: '회사 복지' → query='채용 복지제도 유연근무 휴가'\n\n"
        "## 데이터 출처 구분 및 소스 필터\n"
        "- source='eluocnc': 회사 홈페이지 (회사 소개, 프로젝트, 블로그)\n"
        "- source='admin': 사내문서 (사내 규정, 업무가이드 등)\n"
        "- source='FAQ': 사내게시판 (공지사항, 사내업무가이드, 규정 및 서식, 경조사, 자판기, 출퇴근, 인사발령)\n"
        "- **기본적으로 source 필터 없이 전체 검색하세요.**\n"
        "- 질문이 명확히 특정 출처에만 해당할 때만 source를 지정하세요.\n"
        "- 예: '프로젝트 포트폴리오 보여줘' → source='eluocnc' (명확히 홈페이지)\n"
        "- 예: '자판기 비밀번호' → source='FAQ' (명확히 사내생활)\n"
        "- 예: '대표이름', '조직구조', '복지' → source 미지정 (여러 출처에 있을 수 있음)\n\n"
        "## 검색 결과 관련성 판단\n"
        "- 유사도 점수보다 **내용의 관련성**을 기준으로 판단하세요.\n"
        "- 검색 결과의 제목과 내용이 질문과 직접 관련 없으면 무시하세요.\n"
        "- 모든 결과가 무관하면 '관련 정보를 찾지 못했습니다'라고 솔직히 답변하세요.\n\n"
        "## 답변 규칙\n"
        "- 답변은 항상 한국어로, 친절하고 다정한 톤으로 작성하세요. (~해요, ~드릴게요 체)\n"
        "- 검색 결과를 그대로 나열하지 말고, 사용자의 질문 의도에 맞게 정리하여 답변하세요.\n"
        "- 답변 끝에 출처(FAQ/게시판/회사 홈페이지)와 관련 URL을 함께 제공하세요.\n"
        "- 검색 결과가 없거나 관련이 없는 경우, 솔직하게 모른다고 답변하세요.\n"
        "- Open RAG 답변 시, 회사 문서 내용과 보충 지식을 명확히 구분하세요:\n"
        "  - 회사 문서 기반: '회사 자료에 따르면...', '사내 문서에서는...'\n"
        "  - 보충 지식: '일반적으로...', '업계 모범 사례로는...', '참고로...'\n"
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
        Tool(process_expense, takes_ctx=True, prepare=_prepare_expense),
    ],
)


@faq_agent.system_prompt
async def _dynamic_receipt_prompt(ctx: RunContext[AgentDeps]) -> str:
    """영수증 데이터가 있으면 동적으로 시스템 프롬프트를 추가한다."""
    if ctx.deps.receipt_data is not None:
        return (
            "\n\n## ⚠️ 현재 상태: 영수증 이미지가 첨부되어 있습니다.\n"
            "사용자가 비용처리, 영수증 분석, 경비정산 등을 요청하면 "
            "**반드시 process_expense 도구를 즉시 호출하세요.** "
            "절대로 '이미지를 첨부해주세요'라고 안내하지 마세요. 이미 첨부되어 있습니다."
        )
    return ""


@faq_agent.output_validator
async def validate_response(ctx: RunContext[AgentDeps], output: str) -> str:
    """답변 형식을 검증한다: 일반 응답에는 [관련 주제: ...] 태그가 필요."""
    # 비용처리 응답은 관련 주제 불필요
    if "[EXPENSE_RESULT:" in output:
        return output
    # 짧은 응답(인사 등)은 태그 불필요
    if len(output) <= 50:
        return output
    # 일반 응답에 관련 주제 태그 확인
    if "[관련 주제:" not in output:
        raise ModelRetry("답변 마지막에 [관련 주제: 주제1, 주제2, 주제3] 형식을 포함해주세요.")
    return output


def get_graph_db() -> GraphRAGDatabase:
    """GraphRAG 데이터베이스를 로드하여 반환한다."""
    return GraphRAGDatabase().load()


def ask(question: str, message_history=None) -> str:
    """질문에 대한 답변을 반환한다."""
    print(f"[Model] {model.model_name}")
    db = get_graph_db()
    deps = AgentDeps(graph_db=db)
    result = faq_agent.run_sync(
        user_prompt=question,
        deps=deps,
        message_history=message_history,
    )
    return result.output


if __name__ == "__main__":
    answer = ask("비밀번호를 잊어버렸어요")
    print(answer)
