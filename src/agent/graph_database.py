"""
GraphRAG 데이터베이스.

Hybrid RAG (VectorRAG + GraphRAG) 검색을 제공하는 데이터베이스 클래스.
기존 FAQDatabase를 대체한다.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import networkx as nx
from sentence_transformers import SentenceTransformer

from graph.embedding_index import (
    get_embed_model,
    embed_query,
    init_pinecone,
    search_pinecone,
)
from graph.graph_builder import load_graph


DATA_DIR = Path(__file__).resolve().parents[2] / "data"
FAQ_DATA_PATH = DATA_DIR / "faq_lineworks.json"
BOARD_DATA_PATH = DATA_DIR / "board_lineworks.json"
ELUOCNC_DATA_PATH = DATA_DIR / "eluocnc.json"
GRAPH_PATH = DATA_DIR / "knowledge_graph.json"


@dataclass
class GraphRAGDatabase:
    """Hybrid RAG (벡터 + 그래프) 검색을 제공하는 데이터베이스."""

    items: list[dict] = field(default_factory=list)
    graph: nx.Graph = field(default_factory=nx.Graph)
    pinecone_index: Any = None
    embed_model: SentenceTransformer | None = None

    def load(self, paths: list[Path] | None = None) -> "GraphRAGDatabase":
        """JSON 데이터 + 지식그래프 + Pinecone 연결 + 임베딩 모델 로드."""
        if paths is None:
            paths = [FAQ_DATA_PATH, BOARD_DATA_PATH, ELUOCNC_DATA_PATH]

        # JSON 데이터 로드
        self.items = []
        for path in paths:
            if not path.exists():
                print(f"[warn] 데이터 파일 없음 (건너뜀): {path}")
                continue
            with open(path, encoding="utf-8") as f:
                items = json.load(f)
            if "eluocnc" in path.name:
                default_source = "eluocnc"
            elif "board" in path.name:
                default_source = "board"
            else:
                default_source = "faq"
            for item in items:
                item.setdefault("source", default_source)
            self.items.extend(items)

        # 중복 제거
        seen_urls: set[str] = set()
        unique = []
        for item in self.items:
            url = item.get("url", "")
            base_url = url.split("?")[0] if url else ""
            content = item.get("content", "").strip()
            if len(content) < 50:
                continue
            if base_url and base_url in seen_urls:
                continue
            if base_url:
                seen_urls.add(base_url)
            unique.append(item)
        self.items = unique

        if not self.items:
            raise ValueError("데이터가 비어있습니다. FAQ 또는 게시판 데이터를 먼저 수집해주세요.")

        # 지식그래프 로드
        if GRAPH_PATH.exists():
            self.graph = load_graph(GRAPH_PATH)
            entity_count = sum(
                1 for _, d in self.graph.nodes(data=True)
                if d.get("node_type") == "ENTITY"
            )
            print(f"[info] 지식그래프 로드: {entity_count} 엔티티, {self.graph.number_of_edges()} 엣지")
        else:
            print("[warn] 지식그래프 없음. 'python src/graph/build_index.py' 실행 필요")

        # 임베딩 모델 로드
        self.embed_model = get_embed_model()

        # Pinecone 연결
        try:
            self.pinecone_index = init_pinecone()
            print("[info] Pinecone 연결 완료")
        except Exception as e:
            print(f"[warn] Pinecone 연결 실패: {e}. 벡터 검색 비활성화.")

        return self

    def vector_search(self, query: str, top_k: int = 5) -> list[dict]:
        """벡터 유사도 검색 (Pinecone)."""
        if not self.pinecone_index or not self.embed_model:
            return []

        query_vec = embed_query(self.embed_model, query)
        results = search_pinecone(self.pinecone_index, query_vec, top_k=top_k)

        output = []
        for r in results:
            doc_idx = int(r["id"].replace("doc_", "")) if r["id"].startswith("doc_") else -1
            item = self.items[doc_idx] if 0 <= doc_idx < len(self.items) else {}

            # 첨부파일 이미지 수집
            images = []
            for att in item.get("attachments", []):
                images.extend(att.get("images", []))

            output.append({
                "title": r["metadata"].get("title", item.get("title", "")),
                "content": item.get("content", r["metadata"].get("content_preview", ""))[:800],
                "url": r["metadata"].get("url", item.get("url", "")),
                "source": r["metadata"].get("source", item.get("source", "faq")),
                "score": r["score"],
                "images": images,
                "search_type": "vector",
            })
        return output

    def graph_search(self, query: str, top_k: int = 5) -> list[dict]:
        """그래프 기반 검색. 쿼리 → 엔티티 매칭 → 1-2홉 탐색."""
        if not self.graph or self.graph.number_of_nodes() == 0 or not self.embed_model:
            return []

        # 엔티티 노드만 추출
        entity_nodes = [
            (n, d) for n, d in self.graph.nodes(data=True)
            if d.get("node_type") == "ENTITY"
        ]
        if not entity_nodes:
            return []

        # 쿼리와 엔티티 이름 유사도 비교
        entity_names = [n for n, _ in entity_nodes]
        query_vec = embed_query(self.embed_model, query)

        from graph.embedding_index import embed_documents
        entity_vecs = embed_documents(self.embed_model, entity_names)

        from sklearn.metrics.pairwise import cosine_similarity
        import numpy as np
        sims = cosine_similarity([query_vec], np.array(entity_vecs)).flatten()

        # 상위 매칭 엔티티에서 1-2홉 탐색
        top_entity_indices = sims.argsort()[::-1][:3]

        found_doc_ids: dict[str, float] = {}  # doc_id → score
        related_concepts: dict[str, list[str]] = {}  # doc_id → concepts

        for idx in top_entity_indices:
            entity_name = entity_names[idx]
            entity_sim = float(sims[idx])
            if entity_sim < 0.3:
                continue

            # 1홉: 직접 연결된 문서
            for neighbor in self.graph.neighbors(entity_name):
                node_data = self.graph.nodes[neighbor]
                if node_data.get("node_type") == "DOCUMENT":
                    old_score = found_doc_ids.get(neighbor, 0)
                    found_doc_ids[neighbor] = max(old_score, entity_sim)
                    related_concepts.setdefault(neighbor, []).append(entity_name)

            # 2홉: 엔티티 → 엔티티 → 문서
            for neighbor in self.graph.neighbors(entity_name):
                node_data = self.graph.nodes[neighbor]
                if node_data.get("node_type") == "ENTITY":
                    for nn in self.graph.neighbors(neighbor):
                        nn_data = self.graph.nodes[nn]
                        if nn_data.get("node_type") == "DOCUMENT":
                            old_score = found_doc_ids.get(nn, 0)
                            found_doc_ids[nn] = max(old_score, entity_sim * 0.7)
                            edge_data = self.graph.edges.get((entity_name, neighbor), {})
                            concept = f"{entity_name} → {edge_data.get('relation', 'RELATED')} → {neighbor}"
                            related_concepts.setdefault(nn, []).append(concept)

        # doc_id를 원본 데이터와 매칭
        output = []
        sorted_docs = sorted(found_doc_ids.items(), key=lambda x: x[1], reverse=True)[:top_k]
        for doc_id, score in sorted_docs:
            doc_idx = int(doc_id.replace("doc_", "")) if doc_id.startswith("doc_") else -1
            if 0 <= doc_idx < len(self.items):
                item = self.items[doc_idx]
                images = []
                for att in item.get("attachments", []):
                    images.extend(att.get("images", []))

                concepts = related_concepts.get(doc_id, [])
                output.append({
                    "title": item.get("title", ""),
                    "content": item.get("content", "")[:800],
                    "url": item.get("url", ""),
                    "source": item.get("source", "faq"),
                    "score": score,
                    "images": images,
                    "search_type": "graph",
                    "related_concepts": concepts[:5],
                })
        return output

    def hybrid_search(self, query: str, top_k: int = 5) -> list[dict]:
        """Hybrid 검색: 벡터 + 그래프 결과를 RRF로 합산."""
        vector_results = self.vector_search(query, top_k=top_k)
        graph_results = self.graph_search(query, top_k=top_k)

        # Reciprocal Rank Fusion
        k = 60  # RRF 상수
        rrf_scores: dict[str, float] = {}
        result_map: dict[str, dict] = {}
        concepts_map: dict[str, list[str]] = {}

        for rank, r in enumerate(vector_results):
            key = r.get("url") or r.get("title", "")
            rrf_scores[key] = rrf_scores.get(key, 0) + 1.0 / (k + rank + 1)
            result_map[key] = r

        for rank, r in enumerate(graph_results):
            key = r.get("url") or r.get("title", "")
            rrf_scores[key] = rrf_scores.get(key, 0) + 1.0 / (k + rank + 1)
            if key not in result_map:
                result_map[key] = r
            concepts_map[key] = r.get("related_concepts", [])

        # RRF 스코어로 정렬
        sorted_keys = sorted(rrf_scores.keys(), key=lambda k: rrf_scores[k], reverse=True)

        output = []
        for key in sorted_keys[:top_k]:
            item = result_map[key]
            item["score"] = rrf_scores[key]
            item["search_type"] = "hybrid"
            item["related_concepts"] = concepts_map.get(key, [])
            output.append(item)

        return output

    def explore_topic(self, topic: str) -> list[dict]:
        """그래프에서 특정 토픽의 연결된 엔티티와 문서를 탐색한다."""
        if not self.graph or self.graph.number_of_nodes() == 0 or not self.embed_model:
            return []

        entity_nodes = [
            (n, d) for n, d in self.graph.nodes(data=True)
            if d.get("node_type") == "ENTITY"
        ]
        if not entity_nodes:
            return []

        entity_names = [n for n, _ in entity_nodes]
        query_vec = embed_query(self.embed_model, topic)

        from graph.embedding_index import embed_documents
        entity_vecs = embed_documents(self.embed_model, entity_names)

        from sklearn.metrics.pairwise import cosine_similarity
        import numpy as np
        sims = cosine_similarity([query_vec], np.array(entity_vecs)).flatten()

        # 상위 매칭 엔티티
        top_indices = sims.argsort()[::-1][:5]
        results = []
        for idx in top_indices:
            if sims[idx] < 0.3:
                continue
            name = entity_names[idx]
            data = self.graph.nodes[name]

            neighbors = []
            for nb in self.graph.neighbors(name):
                nb_data = self.graph.nodes[nb]
                edge_data = self.graph.edges.get((name, nb), {})
                neighbors.append({
                    "name": nb,
                    "type": nb_data.get("node_type", ""),
                    "relation": edge_data.get("relation", ""),
                })

            results.append({
                "entity": name,
                "type": data.get("entity_type", ""),
                "description": data.get("description", ""),
                "similarity": float(sims[idx]),
                "neighbors": neighbors,
            })
        return results
