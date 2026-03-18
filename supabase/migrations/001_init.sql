-- Extensions
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 문서 테이블 (JSON 파일 대체)
CREATE TABLE documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  url TEXT UNIQUE NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  content TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'eluocnc',
  category TEXT DEFAULT '',
  metadata JSONB DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- 문서 청크 + 임베딩 (Pinecone 대체)
CREATE TABLE document_chunks (
  id TEXT PRIMARY KEY,
  document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  chunk_index INTEGER NOT NULL,
  total_chunks INTEGER NOT NULL,
  chunk_text TEXT NOT NULL,
  content_preview TEXT DEFAULT '',
  embedding vector(768) NOT NULL,
  chunk_type TEXT NOT NULL DEFAULT 'text',
  image_path TEXT,
  source TEXT DEFAULT '',
  tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', chunk_text)) STORED,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_chunks_embedding ON document_chunks
  USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX idx_chunks_tsv ON document_chunks USING gin(tsv);
CREATE INDEX idx_chunks_document_id ON document_chunks(document_id);
CREATE INDEX idx_chunks_source ON document_chunks(source);

-- 대화 테이블 (신규)
CREATE TABLE conversations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_token TEXT UNIQUE,
  model_choice TEXT DEFAULT '',
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE messages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
  content TEXT NOT NULL,
  timestamp TIMESTAMPTZ DEFAULT now(),
  related_topics TEXT[] DEFAULT '{}',
  pydantic_message JSONB,
  metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX idx_messages_conversation ON messages(conversation_id, timestamp);

-- 하이브리드 검색 RPC 함수
CREATE OR REPLACE FUNCTION hybrid_search(
  query_embedding vector(768),
  query_text text,
  match_count int DEFAULT 5,
  source_filter text DEFAULT NULL
)
RETURNS TABLE (
  id text, score float, chunk_text text, title text,
  url text, source text, content_preview text,
  chunk_index int, total_chunks int, chunk_type text, image_path text
)
LANGUAGE sql AS $$
  WITH vector_results AS (
    SELECT dc.id, 1 - (dc.embedding <=> query_embedding) AS vscore,
      ROW_NUMBER() OVER (ORDER BY dc.embedding <=> query_embedding) AS vrank,
      dc.chunk_text, d.title, d.url, d.source, dc.content_preview,
      dc.chunk_index, dc.total_chunks, dc.chunk_type, dc.image_path
    FROM document_chunks dc JOIN documents d ON dc.document_id = d.id
    WHERE (source_filter IS NULL OR dc.source = source_filter)
    ORDER BY dc.embedding <=> query_embedding
    LIMIT match_count * 4
  ),
  fts_results AS (
    SELECT dc.id, ts_rank(dc.tsv, plainto_tsquery('simple', query_text)) AS tscore,
      ROW_NUMBER() OVER (ORDER BY ts_rank(dc.tsv, plainto_tsquery('simple', query_text)) DESC) AS frank,
      dc.chunk_text, d.title, d.url, d.source, dc.content_preview,
      dc.chunk_index, dc.total_chunks, dc.chunk_type, dc.image_path
    FROM document_chunks dc JOIN documents d ON dc.document_id = d.id
    WHERE dc.tsv @@ plainto_tsquery('simple', query_text)
      AND (source_filter IS NULL OR dc.source = source_filter)
    LIMIT match_count * 4
  ),
  combined AS (
    SELECT COALESCE(v.id, f.id) AS id,
      COALESCE(1.0/(60+v.vrank), 0) + COALESCE(1.0/(60+f.frank), 0) AS score,
      COALESCE(v.chunk_text, f.chunk_text) AS chunk_text,
      COALESCE(v.title, f.title) AS title,
      COALESCE(v.url, f.url) AS url,
      COALESCE(v.source, f.source) AS source,
      COALESCE(v.content_preview, f.content_preview) AS content_preview,
      COALESCE(v.chunk_index, f.chunk_index) AS chunk_index,
      COALESCE(v.total_chunks, f.total_chunks) AS total_chunks,
      COALESCE(v.chunk_type, f.chunk_type) AS chunk_type,
      COALESCE(v.image_path, f.image_path) AS image_path
    FROM vector_results v FULL OUTER JOIN fts_results f ON v.id = f.id
  )
  SELECT * FROM combined ORDER BY score DESC LIMIT match_count;
$$;
