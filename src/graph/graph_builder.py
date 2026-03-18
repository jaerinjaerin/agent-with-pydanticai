"""
지식그래프 구축 모듈.

Claude API로 문서에서 엔티티/관계를 추출하고,
NetworkX 그래프를 구축한다.
"""

import json
from pathlib import Path

import networkx as nx
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel

import os


# ── Pydantic 스키마 ──

class Entity(BaseModel):
    name: str
    entity_type: str  # PRODUCT, FEATURE, PROCEDURE, POLICY, COMPANY, PROJECT
    description: str


class Relationship(BaseModel):
    source: str
    target: str
    relation: str  # HAS_FEATURE, SOLVES, BELONGS_TO, REQUIRES
    description: str


class DocumentGraphExtraction(BaseModel):
    entities: list[Entity]
    relationships: list[Relationship]


# ── 엔티티/관계 추출 에이전트 ──

def _embed_texts_via_pinecone(texts: list[str]) -> list[list[float]]:
    """Pinecone Inference API로 텍스트 임베딩."""
    from pinecone import Pinecone
    pc = Pinecone(api_key=os.environ.get("PINECONE_API_KEY", ""))
    response = pc.inference.embed(
        model="multilingual-e5-large",
        inputs=texts,
        parameters={"input_type": "passage"},
    )
    return [e.values for e in response.data]


extraction_agent = Agent(
    model=AnthropicModel("claude-sonnet-4-20250514"),
    output_type=DocumentGraphExtraction,
    system_prompt=(
        "당신은 한국어 문서에서 엔티티와 관계를 추출하는 전문가입니다.\n\n"
        "## 엔티티 유형\n"
        "- PRODUCT: 제품/서비스 (예: LINE WORKS, 드라이브)\n"
        "- FEATURE: 기능 (예: 파일 공유, 메일 전송)\n"
        "- PROCEDURE: 절차/방법 (예: 비밀번호 변경, 연차 신청)\n"
        "- POLICY: 정책/규정 (예: 근태관리 규정, 보안 정책)\n"
        "- COMPANY: 회사/조직 (예: 엘루오씨앤씨, 네이버)\n"
        "- PROJECT: 프로젝트/사례 (예: 이마트 프로젝트, 네이버 캠페인)\n\n"
        "## 관계 유형\n"
        "- HAS_FEATURE: 기능을 가짐\n"
        "- SOLVES: 문제를 해결\n"
        "- BELONGS_TO: 소속\n"
        "- REQUIRES: 필요조건\n"
        "- RELATED_TO: 관련\n\n"
        "## 규칙\n"
        "- 엔티티 이름에서 한국어 조사(을, 를, 이, 가, 의, 에서, 으로 등)를 제거하세요.\n"
        "- 중복 엔티티를 최소화하세요.\n"
        "- 관계의 source와 target은 반드시 추출한 엔티티 이름과 일치해야 합니다.\n"
        "- 문서 내용이 짧아도 최소 1개 이상의 엔티티를 추출하세요.\n"
    ),
)


async def extract_entities_from_doc(title: str, content: str) -> DocumentGraphExtraction:
    """단일 문서에서 엔티티와 관계를 추출한다."""
    text = f"제목: {title}\n내용: {content[:2000]}"
    result = await extraction_agent.run(text)
    return result.output


def resolve_duplicate_entities(
    all_entities: list[dict],
    similarity_threshold: float = 0.85,
) -> dict[str, str]:
    """동일 엔티티를 병합한다. 임베딩 유사도 기반.

    Returns:
        name → canonical_name 매핑
    """
    if not all_entities:
        return {}

    names = list({e["name"] for e in all_entities})
    if len(names) <= 1:
        return {n: n for n in names}

    vectors = _embed_texts_via_pinecone(names)

    from sklearn.metrics.pairwise import cosine_similarity as cos_sim
    import numpy as np

    sim_matrix = cos_sim(np.array(vectors))

    # Union-Find로 병합
    parent = {n: n for n in names}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            if sim_matrix[i][j] >= similarity_threshold:
                union(names[i], names[j])

    # 각 그룹에서 가장 빈번한 이름을 대표로 선택
    from collections import Counter
    name_counts = Counter(e["name"] for e in all_entities)

    groups: dict[str, list[str]] = {}
    for n in names:
        root = find(n)
        groups.setdefault(root, []).append(n)

    canonical_map = {}
    for group_members in groups.values():
        representative = max(group_members, key=lambda n: name_counts.get(n, 0))
        for member in group_members:
            canonical_map[member] = representative

    return canonical_map


def build_networkx_graph(
    documents: list[dict],
    extractions: list[DocumentGraphExtraction],
    canonical_map: dict[str, str],
) -> nx.Graph:
    """엔티티/관계 추출 결과로 NetworkX 그래프를 구축한다."""
    G = nx.Graph()

    # 문서 노드 추가
    for i, doc in enumerate(documents):
        doc_id = f"doc_{i}"
        G.add_node(doc_id, node_type="DOCUMENT", title=doc.get("title", ""),
                    url=doc.get("url", ""), source=doc.get("source", ""))

    # 엔티티 노드 + 관계 엣지 추가
    for i, extraction in enumerate(extractions):
        doc_id = f"doc_{i}"

        for entity in extraction.entities:
            canonical_name = canonical_map.get(entity.name, entity.name)
            if G.has_node(canonical_name):
                # 기존 노드에 문서 ID 추가
                existing_docs = G.nodes[canonical_name].get("document_ids", [])
                if doc_id not in existing_docs:
                    existing_docs.append(doc_id)
                    G.nodes[canonical_name]["document_ids"] = existing_docs
            else:
                G.add_node(
                    canonical_name,
                    node_type="ENTITY",
                    entity_type=entity.entity_type,
                    description=entity.description,
                    document_ids=[doc_id],
                )

            # 문서 → 엔티티 MENTIONS 엣지
            G.add_edge(doc_id, canonical_name, relation="MENTIONS")

        for rel in extraction.relationships:
            src = canonical_map.get(rel.source, rel.source)
            tgt = canonical_map.get(rel.target, rel.target)
            if G.has_node(src) and G.has_node(tgt):
                G.add_edge(src, tgt, relation=rel.relation, description=rel.description)

    return G


async def add_doc_to_graph(
    graph: nx.Graph, doc: dict
) -> nx.Graph:
    """단일 문서를 기존 그래프에 추가한다.

    엔티티/관계를 추출하고, DOCUMENT 노드 + ENTITY 노드/엣지를 추가한다.
    기존 엔티티와 이름이 동일하면 병합한다.
    """
    title = doc.get("title", "")
    content = doc.get("content", "")
    url = doc.get("url", "")

    # 엔티티/관계 추출
    extraction = await extract_entities_from_doc(title, content)

    # DOCUMENT 노드 ID: URL 기반
    doc_node_id = f"doc_url_{url}"
    graph.add_node(
        doc_node_id,
        node_type="DOCUMENT",
        title=title,
        url=url,
        source=doc.get("source", "admin"),
    )

    # 엔티티 노드 + 관계 엣지 추가
    for entity in extraction.entities:
        name = entity.name
        if graph.has_node(name):
            # 기존 엔티티에 문서 ID 추가
            existing_docs = graph.nodes[name].get("document_ids", [])
            if doc_node_id not in existing_docs:
                existing_docs.append(doc_node_id)
                graph.nodes[name]["document_ids"] = existing_docs
        else:
            graph.add_node(
                name,
                node_type="ENTITY",
                entity_type=entity.entity_type,
                description=entity.description,
                document_ids=[doc_node_id],
            )
        graph.add_edge(doc_node_id, name, relation="MENTIONS")

    for rel in extraction.relationships:
        if graph.has_node(rel.source) and graph.has_node(rel.target):
            graph.add_edge(
                rel.source, rel.target,
                relation=rel.relation, description=rel.description,
            )

    return graph


def remove_doc_from_graph(graph: nx.Graph, doc_url: str) -> nx.Graph:
    """URL에 해당하는 DOCUMENT 노드를 그래프에서 제거한다.

    연결된 엔티티 중 다른 문서와 연결이 없는 고아 엔티티도 함께 제거한다.
    """
    doc_node_id = f"doc_url_{doc_url}"
    if not graph.has_node(doc_node_id):
        return graph

    # 연결된 엔티티 노드 목록
    neighbors = list(graph.neighbors(doc_node_id))

    # 문서 노드 제거
    graph.remove_node(doc_node_id)

    # 고아 엔티티 정리: 다른 DOCUMENT 노드와 연결이 없는 엔티티 제거
    for neighbor in neighbors:
        if not graph.has_node(neighbor):
            continue
        node_data = graph.nodes.get(neighbor, {})
        if node_data.get("node_type") != "ENTITY":
            continue
        # 이 엔티티에 연결된 DOCUMENT 노드가 남아있는지 확인
        has_doc_connection = any(
            graph.nodes.get(n, {}).get("node_type") == "DOCUMENT"
            for n in graph.neighbors(neighbor)
        )
        if not has_doc_connection:
            graph.remove_node(neighbor)

    return graph


def save_graph(G: nx.Graph, path: Path) -> None:
    """그래프를 JSON 파일로 직렬화한다."""
    data = nx.node_link_data(G)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_graph(path: Path) -> nx.Graph:
    """JSON 파일에서 그래프를 로드한다."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return nx.node_link_graph(data)
