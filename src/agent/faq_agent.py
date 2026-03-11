"""
LINE WORKS FAQ 에이전트.

TF-IDF 기반 검색 툴을 갖춘 PydanticAI 에이전트로,
크롤링된 FAQ 데이터를 기반으로 사용자 질문에 답변한다.
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import asyncio
import nest_asyncio
from dotenv import load_dotenv
from pydantic_ai import Agent, RunContext, Tool
from pydantic_ai.models.google import GoogleModel
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

load_dotenv()

# Streamlit Cloud secrets → 환경변수 주입
try:
    import streamlit as st
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        if key in st.secrets and key not in os.environ:
            os.environ[key] = st.secrets[key]
except Exception:
    pass

# uvloop 환경(Streamlit 등)에서는 nest_asyncio가 호환되지 않으므로 건너뛴다.
try:
    import uvloop  # noqa: F401
    _has_uvloop = True
except ImportError:
    _has_uvloop = False

if not _has_uvloop:
    nest_asyncio.apply()

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
FAQ_DATA_PATH = DATA_DIR / "faq_lineworks.json"
BOARD_DATA_PATH = DATA_DIR / "board_lineworks.json"
ELUOCNC_DATA_PATH = DATA_DIR / "eluocnc.json"


@dataclass
class FAQDatabase:
    """TF-IDF 인덱스를 포함한 FAQ 데이터베이스."""

    items: list[dict] = field(default_factory=list)
    vectorizer: TfidfVectorizer = field(default_factory=TfidfVectorizer)
    tfidf_matrix: object = None  # sparse matrix

    def load(self, paths: list[Path] | None = None) -> "FAQDatabase":
        """하나 이상의 JSON 파일을 로드하고 TF-IDF 인덱스를 구축한다."""
        if paths is None:
            paths = [FAQ_DATA_PATH, BOARD_DATA_PATH, ELUOCNC_DATA_PATH]

        self.items = []
        for path in paths:
            if not path.exists():
                print(f"[warn] 데이터 파일 없음 (건너뜀): {path}")
                continue
            with open(path, encoding="utf-8") as f:
                items = json.load(f)
                # source 필드가 없으면 파일명 기반으로 추가
                if "eluocnc" in path.name:
                    default_source = "eluocnc"
                elif "board" in path.name:
                    default_source = "board"
                else:
                    default_source = "faq"
                for item in items:
                    item.setdefault("source", default_source)
                self.items.extend(items)

        if not self.items:
            raise ValueError("데이터가 비어있습니다. FAQ 또는 게시판 데이터를 먼저 수집해주세요.")

        # 제목 + 본문을 합쳐서 TF-IDF 벡터화
        documents = [
            f"{item.get('title', '')} {item.get('content', '')}" for item in self.items
        ]
        self.tfidf_matrix = self.vectorizer.fit_transform(documents)
        return self

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        """쿼리와 가장 유사한 상위 k개 FAQ를 반환한다."""
        if self.tfidf_matrix is None:
            return []

        query_vec = self.vectorizer.transform([query])
        similarities = cosine_similarity(query_vec, self.tfidf_matrix).flatten()

        top_indices = similarities.argsort()[::-1][:top_k]
        results = []
        for idx in top_indices:
            if similarities[idx] > 0:
                item = self.items[idx]
                # 첨부파일에서 이미지 경로 수집
                images = []
                for att in item.get("attachments", []):
                    images.extend(att.get("images", []))
                results.append(
                    {
                        "title": item.get("title", ""),
                        "content": item.get("content", "")[:500],
                        "url": item.get("url", ""),
                        "source": item.get("source", "faq"),
                        "score": float(similarities[idx]),
                        "images": images,
                    }
                )
        return results


def search_faq(ctx: RunContext[FAQDatabase], query: str) -> str:
    """사용자 질문을 기반으로 FAQ 데이터베이스를 검색합니다."""
    results = ctx.deps.search(query)
    if not results:
        return "관련 FAQ를 찾을 수 없습니다."

    source_labels = {"faq": "FAQ", "board": "게시판", "eluocnc": "회사 홈페이지"}
    output_parts = []
    for i, r in enumerate(results, 1):
        source = source_labels.get(r.get("source", "faq"), r.get("source", ""))
        output_parts.append(
            f"[{i}] [{source}] {r['title']}\n"
            f"내용: {r['content']}\n"
            f"URL: {r['url']}\n"
            f"유사도: {r['score']:.3f}"
        )
    return "\n\n".join(output_parts)


def list_titles(ctx: RunContext[FAQDatabase], source: str = "") -> str:
    """등록된 항목들의 제목 목록을 반환합니다.

    Args:
        source: 필터할 출처. "faq", "board", 또는 ""(전체).
    """
    items = ctx.deps.items
    if source:
        items = [item for item in items if item.get("source") == source]

    if not items:
        source_label = {"faq": "FAQ", "board": "게시판", "eluocnc": "회사 홈페이지"}.get(source, source)
        return f"{source_label} 데이터가 없습니다." if source else "등록된 데이터가 없습니다."

    source_labels = {"faq": "FAQ", "board": "게시판", "eluocnc": "회사 홈페이지"}
    lines = []
    for i, item in enumerate(items, 1):
        src = source_labels.get(item.get("source", ""), "")
        lines.append(f"{i}. [{src}] {item.get('title', '(제목 없음)')}")
    return "\n".join(lines)


def get_item_detail(ctx: RunContext[FAQDatabase], title: str) -> str:
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


def get_data_stats(ctx: RunContext[FAQDatabase]) -> str:
    """데이터 소스별 항목 수 등 통계 정보를 반환합니다."""
    items = ctx.deps.items
    total = len(items)
    faq_count = sum(1 for item in items if item.get("source") == "faq")
    board_count = sum(1 for item in items if item.get("source") == "board")
    eluocnc_count = sum(1 for item in items if item.get("source") == "eluocnc")
    return (
        f"전체 항목 수: {total}\n"
        f"- FAQ: {faq_count}건\n"
        f"- 게시판: {board_count}건\n"
        f"- 회사 홈페이지: {eluocnc_count}건"
    )


model = GoogleModel("gemini-2.0-flash")

faq_agent = Agent(
    model=model,
    deps_type=FAQDatabase,
    system_prompt=(
        "당신은 엘루오 회사의 LINE WORKS FAQ, 사내 규정/업무가이드, 회사 홈페이지 전문 도우미입니다. "
        "사용자의 질문에 대해 적절한 도구를 사용하여 답변해주세요.\n"
        "- 특정 주제 검색: search_faq 도구 사용\n"
        "- 항목 목록 조회: list_titles 도구 사용 (source='board', 'faq', 'eluocnc'로 필터 가능)\n"
        "- 특정 항목 상세 조회: get_item_detail 도구 사용\n"
        "- 데이터 통계: get_data_stats 도구 사용\n\n"
        "중요 규칙:\n"
        "- 검색 결과에 제목이 유사한 항목이 2개 이상 나오면, 바로 답변하지 말고 "
        "해당 항목들의 제목을 번호 목록으로 보여주며 어떤 항목에 대해 알고 싶은지 사용자에게 먼저 질문하세요. "
        "사용자가 선택한 후에 get_item_detail로 해당 항목의 상세 내용을 조회하여 답변하세요.\n"
        "- 답변 시 출처(FAQ, 게시판, 회사 홈페이지)와 관련 URL도 함께 제공해주세요.\n"
        "- 검색 결과가 없거나 관련이 없는 경우, 솔직하게 모른다고 답변하세요.\n"
        "- 항상 한국어로 답변하세요."
    ),
    tools=[
        Tool(search_faq, takes_ctx=True),
        Tool(list_titles, takes_ctx=True),
        Tool(get_item_detail, takes_ctx=True),
        Tool(get_data_stats, takes_ctx=True),
    ],
)


def get_faq_db() -> FAQDatabase:
    """FAQ 데이터베이스를 로드하여 반환한다."""
    return FAQDatabase().load()


def ask(question: str, message_history=None) -> str:
    """질문에 대한 답변을 반환한다."""
    db = get_faq_db()
    result = faq_agent.run_sync(
        user_prompt=question,
        deps=db,
        message_history=message_history,
    )
    return result.output


if __name__ == "__main__":
    answer = ask("비밀번호를 잊어버렸어요")
    print(answer)
