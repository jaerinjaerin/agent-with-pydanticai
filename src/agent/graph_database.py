"""
VectorRAG 데이터베이스.

벡터 검색(Pinecone) 기반 검색을 제공한다.
기존 FAQDatabase와 동일한 search() 인터페이스를 유지하여 호환성 보장.
"""

import json
import os
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

# 프로젝트 루트를 sys.path에 추가 (graph 모듈 임포트용)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graph.embedding_index import (
    init_pinecone,
    search_records,
    rerank_results,
    PINECONE_INDEX_NAME,
)

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
ELUOCNC_DATA_PATH = DATA_DIR / "eluocnc.json"
ADMIN_DATA_PATH = DATA_DIR / "admin_documents.json"
BOARD_DATA_PATH = DATA_DIR / "board_documents.json"


@dataclass
class GraphRAGDatabase:
    """벡터 검색 기반 RAG 데이터베이스."""

    items: list[dict] = field(default_factory=list)
    pinecone_client: Any = None  # Pinecone 클라이언트 (리랭킹용)
    pinecone_index: Any = None
    _search_cache: OrderedDict = field(default_factory=OrderedDict, repr=False)
    _cache_max: int = field(default=50, repr=False)
    _cache_ttl: float = field(default=300.0, repr=False)  # 5분

    def load(self, paths: list[Path] | None = None) -> "GraphRAGDatabase":
        """JSON 데이터, Pinecone 인덱스를 로드한다."""

        # ── 1. JSON 데이터 로드 ──
        if paths is None:
            paths = [ELUOCNC_DATA_PATH, ADMIN_DATA_PATH, BOARD_DATA_PATH]

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
                    default_source = "eluocnc"
                for item in items:
                    item.setdefault("source", default_source)
                self.items.extend(items)

        # 중복/빈 항목 필터링
        seen_urls: set[str] = set()
        unique_items = []
        for item in self.items:
            url = item.get("url", "")
            content = item.get("content", "").strip()
            if len(content) < 50:
                continue
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            unique_items.append(item)
        self.items = unique_items

        if not self.items:
            print("[warn] 로드된 데이터가 비어있습니다.")

        # ── 2. Pinecone 인덱스 연결 (실패 시 None) ──
        api_key = os.environ.get("PINECONE_API_KEY", "")
        if api_key:
            try:
                from pinecone import Pinecone
                pc = Pinecone(api_key=api_key)
                self.pinecone_client = pc
                self.pinecone_index = pc.Index(PINECONE_INDEX_NAME)
                print(f"[VectorRAG] Pinecone 인덱스 '{PINECONE_INDEX_NAME}' 연결 완료")
            except Exception as e:
                print(f"[warn] Pinecone 연결 실패: {e}")
                self.pinecone_client = None
                self.pinecone_index = None
        else:
            print("[warn] PINECONE_API_KEY 미설정")
            self.pinecone_client = None
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
        """아이템에서 첨부파일/인라인 이미지 경로를 수집한다."""
        if not item:
            return []
        images = []
        for att in item.get("attachments", []):
            images.extend(att.get("images", []))
        # board_documents의 inline_images 지원
        for img in item.get("inline_images", []):
            if img not in images:
                images.append(img)
        return images

    # ── 벡터 검색 ──

    def vector_search(
        self,
        query: str,
        top_k: int = 5,
        min_score: float = 0.2,
        source: str = "",
    ) -> list[dict]:
        """Pinecone 벡터 유사도 검색. 같은 URL의 청크는 최고 점수만 유지.

        Args:
            min_score: 최소 유사도 임계값 (이하 결과 제거).
            source: 소스 필터 ("eluocnc", "admin", 또는 "" 전체).
        """
        if self.pinecone_index is None:
            return []

        # 소스 필터 구성
        pc_filter = {"source": {"$eq": source}} if source else None

        # 청크 중복 제거를 위해 더 많이 요청
        raw_results = search_records(
            self.pinecone_index, query, top_k=top_k * 8, filter=pc_filter,
        )

        # 같은 URL은 최고 점수 청크만 유지
        seen_urls: dict[str, int] = {}  # url → results index
        results = []
        for r in raw_results:
            meta = r.get("metadata", {})
            url = meta.get("url", r.get("url", ""))
            base_url = url.split("?")[0] if url else ""

            if base_url and base_url in seen_urls:
                continue
            if base_url:
                seen_urls[base_url] = len(results)

            item = self._find_item_by_url(url)
            images = self._collect_images(item)
            # 이미지 타입 벡터에서 직접 경로 추가
            if meta.get("type") == "image" and meta.get("image_path"):
                img_path = meta["image_path"]
                if img_path not in images:
                    images.insert(0, img_path)
            # content_preview 키 우선 사용
            content = meta.get("content_preview", meta.get("content", meta.get("text", r.get("content", ""))))[:1500]
            results.append({
                "title": meta.get("title", r.get("title", "")),
                "content": content,
                "url": url,
                "source": meta.get("source", r.get("source", "")),
                "score": float(r.get("score", 0)),
                "images": images,
            })

        # ── 리랭킹 (Pinecone Inference API) ──
        if self.pinecone_client and results:
            try:
                results = rerank_results(
                    self.pinecone_client, query, results, top_n=top_k * 2,
                )
            except Exception as e:
                print(f"[warn] 리랭킹 실패, cosine 순서 유지: {e}")

        # ── 스코어 필터링 ──
        # 리랭크 스코어(0~1, 보통 0.001~0.1)와 벡터 스코어(0~1, 보통 0.7~0.8)는 스케일이 다름
        if results and "rerank_score" in results[0]:
            rerank_threshold = min(min_score * 0.002, 0.0005)
            results = [r for r in results if r.get("rerank_score", 0) >= rerank_threshold]
        else:
            results = [r for r in results if r.get("score", 0) >= min_score]

        return results[:top_k]

    # ── 키워드 기반 폴백 검색 ──

    def keyword_search(self, query: str, top_k: int = 3, source: str = "") -> list[dict]:
        """키워드 기반 제목/내용 매칭 검색 (벡터 검색 보완용)."""
        keywords = [w for w in query.split() if len(w) >= 2]
        if not keywords:
            return []

        scored_items: list[tuple[float, dict]] = []
        for item in self.items:
            if source and item.get("source") != source:
                continue
            title = item.get("title", "").lower()
            content = item.get("content", "")[:2000].lower()

            score = 0.0
            for kw in keywords:
                kw_lower = kw.lower()
                if kw_lower in title:
                    score += 0.3
                if kw_lower in content:
                    score += 0.15
                    # 내용에서 키워드가 자주 등장하면 가산점
                    count = content.count(kw_lower)
                    if count >= 3:
                        score += 0.1

            if score > 0:
                scored_items.append((min(score, 0.9), item))

        scored_items.sort(key=lambda x: x[0], reverse=True)
        results = []
        for score, item in scored_items[:top_k]:
            images = self._collect_images(item)
            results.append({
                "title": item.get("title", ""),
                "content": item.get("content", "")[:1500],
                "url": item.get("url", ""),
                "source": item.get("source", ""),
                "score": score,
                "images": images,
            })
        return results

    # ── 검색 인터페이스 ──

    def search(self, query: str, top_k: int = 5, min_score: float = 0.25, source: str = "") -> list[dict]:
        """벡터 검색을 수행한다. 결과 부족 시 키워드 폴백. 캐시 지원."""
        cache_key = (query, top_k, min_score, source)
        if cache_key in self._search_cache:
            cached_time, cached_result = self._search_cache[cache_key]
            if time.monotonic() - cached_time < self._cache_ttl:
                self._search_cache.move_to_end(cache_key)
                return cached_result
            else:
                del self._search_cache[cache_key]

        results = self.vector_search(query, top_k=top_k, min_score=min_score, source=source)

        # 벡터 검색 결과가 부족하면 키워드 폴백으로 보완
        if len(results) < 3:
            kw_results = self.keyword_search(query, top_k=top_k - len(results), source=source)
            existing_urls = {r.get("url", "") for r in results}
            for kr in kw_results:
                if kr["url"] not in existing_urls:
                    results.append(kr)
                    existing_urls.add(kr["url"])
            # source 필터 사용 시, 결과가 여전히 부족하면 전체 소스에서 키워드 폴백
            if source and len(results) < 3:
                kw_all = self.keyword_search(query, top_k=2, source="")
                for kr in kw_all:
                    if kr["url"] not in existing_urls:
                        results.append(kr)
                        existing_urls.add(kr["url"])

        results = results[:top_k]

        # 캐시 저장
        self._search_cache[cache_key] = (time.monotonic(), results)
        if len(self._search_cache) > self._cache_max:
            self._search_cache.popitem(last=False)

        return results
