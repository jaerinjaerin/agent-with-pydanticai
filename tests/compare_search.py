"""
VectorRAG 검색 품질 테스트.

청킹 적용 후 벡터 검색 결과를 확인한다.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.graph_database import GraphRAGDatabase

# ── 테스트 질문 세트 ──
TEST_QUERIES = [
    # 직접적 키워드 매칭
    "비밀번호를 잊어버렸어요",
    "드라이브 용량이 부족합니다",
    "메일 전달 설정 방법",
    # 기능 관련 질문
    "관리자가 설정할 수 있는 보안 기능들",
    "캘린더와 연동되는 기능은?",
    "조직도와 관련된 설정",
    # 게시판/사내 규정 관련
    "출장 규정",
    "휴가 신청 방법",
    # 엘루오씨앤씨 관련
    "엘루오씨앤씨 사업 분야",
    "회사 연혁",
]


def print_results(label: str, results: list[dict], max_show: int = 5):
    """검색 결과를 포맷팅하여 출력."""
    print(f"\n  [{label}] ({len(results)}건)")
    for i, r in enumerate(results[:max_show]):
        title = r.get("title", "(제목없음)")[:60]
        score = r.get("score", 0)
        source = r.get("source", "?")
        print(f"    {i+1}. [{source}] {title}  (score={score:.4f})")


def run_search_test(db: GraphRAGDatabase, queries: list[str], top_k: int = 5):
    """벡터 검색 결과를 확인."""
    print("=" * 80)
    print("VectorRAG 검색 품질 테스트")
    print("=" * 80)

    source_hit_counts = {"faq": 0, "board": 0, "eluocnc": 0}
    total_results = 0

    for query in queries:
        print(f"\n{'─' * 70}")
        print(f"질문: {query}")

        db._search_cache.clear()
        t0 = time.time()
        results = db.search(query, top_k=top_k)
        elapsed = time.time() - t0

        print_results(f"벡터 검색 ({elapsed:.3f}s)", results, max_show=top_k)

        for r in results:
            src = r.get("source", "")
            if src in source_hit_counts:
                source_hit_counts[src] += 1
            total_results += 1

    # ── 종합 요약 ──
    print(f"\n{'=' * 80}")
    print("종합 요약")
    print(f"{'=' * 80}")
    n = len(queries)
    print(f"  테스트 질문 수: {n}")
    print(f"  총 반환 결과: {total_results}건")
    print(f"  소스별 결과:")
    for src, count in source_hit_counts.items():
        print(f"    - {src}: {count}건")

    if source_hit_counts["faq"] == 0:
        print("\n  ⚠️ FAQ 결과가 하나도 없습니다! FAQ 데이터가 인덱싱되었는지 확인하세요.")
    else:
        print(f"\n  ✅ FAQ 결과 {source_hit_counts['faq']}건 확인")


if __name__ == "__main__":
    print("데이터베이스 로드 중...")
    db = GraphRAGDatabase().load()
    print(f"로드 완료: {len(db.items)}개 문서\n")

    run_search_test(db, TEST_QUERIES)
