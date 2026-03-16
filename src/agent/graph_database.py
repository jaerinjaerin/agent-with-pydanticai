"""
GraphRAG 데이터베이스.

벡터 검색(Pinecone) + 지식그래프(NetworkX) 하이브리드 검색을 제공한다.
기존 FAQDatabase와 동일한 search() 인터페이스를 유지하여 호환성 보장.
"""

import json
import os
import sys
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import networkx as nx
from dotenv import load_dotenv

load_dotenv()

# 프로젝트 루트를 sys.path에 추가 (graph 모듈 임포트용)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graph.embedding_index import (
    get_embed_model,
    embed_query,
    init_pinecone,
    search_pinecone,
    PINECONE_INDEX_NAME,
)
from graph.graph_builder import load_graph

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
GRAPH_PATH = DATA_DIR / "knowledge_graph.json"
ENTITY_EMBEDDINGS_PATH = DATA_DIR / "entity_embeddings.npz"
BOARD_DATA_PATH = DATA_DIR / "board_lineworks.json"
ELUOCNC_DATA_PATH = DATA_DIR / "eluocnc.json"
ADMIN_DATA_PATH = DATA_DIR / "admin_documents.json"


@dataclass
class GraphRAGDatabase:
    """벡터 검색 + 지식그래프 하이브리드 RAG 데이터베이스."""

    items: list[dict] = field(default_factory=list)
    graph: nx.Graph = field(default_factory=nx.Graph)
    pinecone_index: Any = None
    embed_model: Any = None  # SentenceTransformer
    _entity_names: list[str] = field(default_factory=list)
    _entity_vectors: np.ndarray | None = field(default=None, repr=False)
    _search_cache: OrderedDict = field(default_factory=OrderedDict, repr=False)
    _cache_max: int = field(default=50, repr=False)
    _cache_ttl: float = field(default=300.0, repr=False)  # 5분

    def load(self, paths: list[Path] | None = None) -> "GraphRAGDatabase":
        """JSON 데이터, 지식그래프, 임베딩 모델, Pinecone 인덱스를 로드한다."""

        # ── 1. JSON 데이터 로드 (faq_lineworks.json 제외) ──
        if paths is None:
            paths = [BOARD_DATA_PATH, ELUOCNC_DATA_PATH, ADMIN_DATA_PATH]

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
                elif "admin" in path.name:
                    default_source = "admin"
                else:
                    default_source = "faq"
                for item in items:
                    item.setdefault("source", default_source)
                self.items.extend(items)

        # 중복/빈 항목 필터링
        seen_urls: set[str] = set()
        unique_items = []
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
            unique_items.append(item)
        self.items = unique_items

        if not self.items:
            print("[warn] 로드된 데이터가 비어있습니다.")

        # ── 2. 임베딩 모델 로드 ──
        print("[GraphRAG] 임베딩 모델 로드 중...")
        self.embed_model = get_embed_model()

        # ── 3. 지식그래프 로드 (실패 시 빈 그래프) ──
        try:
            self.graph = load_graph(GRAPH_PATH)
            entity_count = sum(
                1 for _, d in self.graph.nodes(data=True)
                if d.get("node_type") == "ENTITY"
            )
            print(f"[GraphRAG] 지식그래프 로드 완료: 노드 {self.graph.number_of_nodes()}개 "
                  f"(엔티티 {entity_count}개), 엣지 {self.graph.number_of_edges()}개")
        except FileNotFoundError:
            print(f"[warn] 그래프 파일 없음 (벡터 검색만 사용): {GRAPH_PATH}")
            self.graph = nx.Graph()
        except Exception as e:
            print(f"[warn] 그래프 로드 실패 (벡터 검색만 사용): {e}")
            self.graph = nx.Graph()

        # ── 3.5. 사전 계산된 엔티티 임베딩 로드 ──
        try:
            data = np.load(ENTITY_EMBEDDINGS_PATH, allow_pickle=True)
            self._entity_names = data["names"].tolist()
            vectors = data["vectors"].astype(np.float32)
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            norms[norms == 0] = 1
            self._entity_vectors = vectors / norms
            print(f"[GraphRAG] 엔티티 임베딩 로드 완료: {len(self._entity_names)}개")
        except FileNotFoundError:
            print(f"[warn] 엔티티 임베딩 파일 없음 (런타임 계산으로 폴백): {ENTITY_EMBEDDINGS_PATH}")
        except Exception as e:
            print(f"[warn] 엔티티 임베딩 로드 실패 (런타임 계산으로 폴백): {e}")

        # ── 4. Pinecone 인덱스 연결 (실패 시 None) ──
        api_key = os.environ.get("PINECONE_API_KEY", "")
        if api_key:
            try:
                self.pinecone_index = init_pinecone(api_key)
                print(f"[GraphRAG] Pinecone 인덱스 '{PINECONE_INDEX_NAME}' 연결 완료")
            except Exception as e:
                print(f"[warn] Pinecone 연결 실패 (그래프 검색만 사용): {e}")
                self.pinecone_index = None
        else:
            print("[warn] PINECONE_API_KEY 미설정 (그래프 검색만 사용)")
            self.pinecone_index = None

        return self

    # ── URL → 원본 아이템 매핑 (이미지 등 추가 정보 조회용) ──

    def _find_item_by_url(self, url: str) -> dict | None:
        """URL로 원본 아이템을 찾는다."""
        if not url:
            return None
        base_url = url.split("?")[0]
        for item in self.items:
            item_base = item.get("url", "").split("?")[0]
            if item_base == base_url:
                return item
        return None

    def _collect_images(self, item: dict | None) -> list[str]:
        """아이템에서 첨부파일 이미지 경로를 수집한다."""
        if not item:
            return []
        images = []
        for att in item.get("attachments", []):
            images.extend(att.get("images", []))
        return images

    # ── 벡터 검색 ──

    def vector_search(self, query: str, top_k: int = 5, query_vector: list[float] | None = None) -> list[dict]:
        """Pinecone 벡터 유사도 검색."""
        if self.pinecone_index is None or self.embed_model is None:
            return []

        if query_vector is None:
            query_vector = embed_query(self.embed_model, query)
        raw_results = search_pinecone(self.pinecone_index, query_vector, top_k=top_k)

        results = []
        for r in raw_results:
            meta = r.get("metadata", {})
            url = meta.get("url", r.get("url", ""))
            item = self._find_item_by_url(url)
            images = self._collect_images(item)
            # 이미지 타입 벡터에서 직접 경로 추가
            if meta.get("type") == "image" and meta.get("image_path"):
                img_path = meta["image_path"]
                if img_path not in images:
                    images.insert(0, img_path)
            results.append({
                "title": meta.get("title", r.get("title", "")),
                "content": meta.get("content", meta.get("text", r.get("content", "")))[:500],
                "url": url,
                "source": meta.get("source", r.get("source", "")),
                "score": float(r.get("score", 0)),
                "images": images,
            })
        return results

    # ── 그래프 검색 ──

    def graph_search(self, query: str, top_k: int = 5, query_vector: list[float] | None = None) -> list[dict]:
        """지식그래프 기반 검색: 쿼리와 유사한 엔티티 → 연결 문서 탐색."""
        if self.graph.number_of_nodes() == 0 or self.embed_model is None:
            return []

        # 쿼리 임베딩
        if query_vector is None:
            query_vector = embed_query(self.embed_model, query)
        query_vector = np.array(query_vector)
        query_norm = query_vector / (np.linalg.norm(query_vector) + 1e-10)

        # 사전 캐시된 엔티티 임베딩 사용 (없으면 런타임 계산으로 폴백)
        if self._entity_vectors is not None and self._entity_names:
            entity_names = self._entity_names
            similarities = self._entity_vectors @ query_norm
        else:
            entity_nodes = [
                (n, d) for n, d in self.graph.nodes(data=True)
                if d.get("node_type") == "ENTITY"
            ]
            if not entity_nodes:
                return []
            entity_names = [n for n, _ in entity_nodes]
            entity_texts = [
                f"{n} {d.get('description', '')}" for n, d in entity_nodes
            ]
            entity_vectors = np.array(
                self.embed_model.encode(entity_texts, batch_size=64)
            )
            entity_norms = entity_vectors / (
                np.linalg.norm(entity_vectors, axis=1, keepdims=True) + 1e-10
            )
            similarities = entity_norms @ query_norm

        # 상위 매칭 엔티티
        top_entity_count = min(5, len(entity_names))
        top_indices = np.argsort(similarities)[::-1][:top_entity_count]

        # 1-2홉 그래프 탐색 → 연결된 DOCUMENT 노드 찾기
        doc_scores: dict[str, dict] = {}  # doc_id → {score, entities}
        for idx in top_indices:
            entity_name = entity_names[idx]
            entity_sim = float(similarities[idx])
            if entity_sim <= 0:
                continue

            # 1홉: 엔티티에 직접 연결된 노드
            neighbors_1hop = set(self.graph.neighbors(entity_name))
            # 2홉: 1홉 노드의 이웃
            neighbors_2hop = set()
            for n1 in neighbors_1hop:
                neighbors_2hop.update(self.graph.neighbors(n1))

            all_neighbors = neighbors_1hop | neighbors_2hop
            for neighbor in all_neighbors:
                node_data = self.graph.nodes.get(neighbor, {})
                if node_data.get("node_type") != "DOCUMENT":
                    continue
                hop = 1 if neighbor in neighbors_1hop else 2
                hop_weight = 1.0 if hop == 1 else 0.5
                score = entity_sim * hop_weight

                if neighbor not in doc_scores:
                    doc_scores[neighbor] = {
                        "score": 0.0,
                        "entities": [],
                        "node_data": node_data,
                    }
                doc_scores[neighbor]["score"] += score
                doc_scores[neighbor]["entities"].append(entity_name)

        # 스코어 순 정렬, 상위 top_k 반환
        sorted_docs = sorted(doc_scores.items(), key=lambda x: x[1]["score"], reverse=True)

        results = []
        for doc_id, info in sorted_docs[:top_k]:
            nd = info["node_data"]
            url = nd.get("url", doc_id)
            item = self._find_item_by_url(url)
            content = item.get("content", "")[:500] if item else ""
            title = nd.get("title", item.get("title", "") if item else "")
            results.append({
                "title": title,
                "content": content,
                "url": url,
                "source": nd.get("source", ""),
                "score": float(info["score"]),
                "images": self._collect_images(item),
                "related_entities": list(set(info["entities"])),
            })
        return results

    # ── 하이브리드 검색 (RRF) ──

    def hybrid_search(self, query: str, top_k: int = 5) -> list[dict]:
        """벡터 검색 + 그래프 검색을 Reciprocal Rank Fusion으로 결합."""
        # 캐시 확인
        cache_key = (query, top_k)
        if cache_key in self._search_cache:
            cached_time, cached_result = self._search_cache[cache_key]
            if time.monotonic() - cached_time < self._cache_ttl:
                self._search_cache.move_to_end(cache_key)
                return cached_result
            else:
                del self._search_cache[cache_key]

        # 쿼리 임베딩을 한 번만 계산하여 양쪽 검색에 공유
        query_vector = embed_query(self.embed_model, query) if self.embed_model else None

        with ThreadPoolExecutor(max_workers=2) as executor:
            vec_future = executor.submit(self.vector_search, query, top_k, query_vector)
            graph_future = executor.submit(self.graph_search, query, top_k, query_vector)
            vector_results = vec_future.result()
            graph_results = graph_future.result()

        # 한쪽만 있으면 그대로 반환
        if not vector_results and not graph_results:
            return []
        if not vector_results:
            return graph_results[:top_k]
        if not graph_results:
            return vector_results[:top_k]

        # RRF 점수 계산: score = Σ 1/(k + rank_i), k=60
        K = 60
        rrf_scores: dict[str, float] = {}
        result_map: dict[str, dict] = {}  # URL → 결과 dict

        for rank, r in enumerate(vector_results):
            url = r.get("url", "")
            key = url.split("?")[0] if url else f"_vec_{rank}"
            rrf_scores[key] = rrf_scores.get(key, 0) + 1.0 / (K + rank + 1)
            if key not in result_map:
                result_map[key] = r

        for rank, r in enumerate(graph_results):
            url = r.get("url", "")
            key = url.split("?")[0] if url else f"_graph_{rank}"
            rrf_scores[key] = rrf_scores.get(key, 0) + 1.0 / (K + rank + 1)
            if key not in result_map:
                result_map[key] = r
            elif "related_entities" in r:
                # 그래프 결과에서 엔티티 정보 병합
                existing_entities = result_map[key].get("related_entities", [])
                result_map[key]["related_entities"] = list(
                    set(existing_entities + r["related_entities"])
                )

        # RRF 점수로 정렬
        sorted_keys = sorted(rrf_scores.keys(), key=lambda k: rrf_scores[k], reverse=True)

        results = []
        for key in sorted_keys[:top_k]:
            item = result_map[key]
            item["score"] = rrf_scores[key]
            results.append(item)

        # 캐시 저장
        self._search_cache[cache_key] = (time.monotonic(), results)
        if len(self._search_cache) > self._cache_max:
            self._search_cache.popitem(last=False)

        return results

    # ── 토픽 탐색 ──

    def explore_topic(self, topic: str, top_k: int = 5) -> list[dict]:
        """그래프에서 토픽과 유사한 엔티티를 찾고 연결된 이웃을 반환한다."""
        if self.graph.number_of_nodes() == 0 or self.embed_model is None:
            return []

        # 토픽 임베딩
        query_vector = np.array(embed_query(self.embed_model, topic))
        query_norm = query_vector / (np.linalg.norm(query_vector) + 1e-10)

        # 엔티티 임베딩과 유사도 계산
        if self._entity_vectors is not None and self._entity_names:
            entity_names = self._entity_names
            similarities = self._entity_vectors @ query_norm
        else:
            entity_nodes = [
                (n, d) for n, d in self.graph.nodes(data=True)
                if d.get("node_type") == "ENTITY"
            ]
            if not entity_nodes:
                return []
            entity_names = [n for n, _ in entity_nodes]
            entity_texts = [f"{n} {d.get('description', '')}" for n, d in entity_nodes]
            entity_vectors = np.array(self.embed_model.encode(entity_texts, batch_size=64))
            entity_norms = entity_vectors / (np.linalg.norm(entity_vectors, axis=1, keepdims=True) + 1e-10)
            similarities = entity_norms @ query_norm

        # 상위 매칭 엔티티
        top_count = min(top_k, len(entity_names))
        top_indices = np.argsort(similarities)[::-1][:top_count]

        results = []
        for idx in top_indices:
            name = entity_names[idx]
            sim = float(similarities[idx])
            if sim <= 0:
                continue

            node_data = self.graph.nodes.get(name, {})
            neighbors = []
            for neighbor in self.graph.neighbors(name):
                n_data = self.graph.nodes[neighbor]
                edge_data = self.graph.edges[name, neighbor]
                neighbors.append({
                    "name": neighbor,
                    "type": n_data.get("node_type", ""),
                    "relation": edge_data.get("relation", "RELATED_TO"),
                })

            results.append({
                "entity": name,
                "type": node_data.get("entity_type", ""),
                "description": node_data.get("description", ""),
                "similarity": sim,
                "neighbors": neighbors,
            })
        return results

    # ── 호환성 인터페이스 ──

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """hybrid_search의 별칭. 기존 FAQDatabase.search()와 호환."""
        return self.hybrid_search(query, top_k=top_k)
