-- migrate:up

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- A source file under DOCS_ROOT. rel_path is relative to DOCS_ROOT so it works on the host and in containers.
CREATE TABLE documents (
    id                bigserial PRIMARY KEY,
    rel_path          text NOT NULL UNIQUE,
    sha256            text NOT NULL,
    file_type         text NOT NULL,
    page_count        int,
    status            text NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'indexed', 'no_text', 'failed')),
    error             text,
    extractor_version text,
    llm_model         text,
    indexed_at        timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE document_pages (
    document_id bigint NOT NULL REFERENCES documents ON DELETE CASCADE,
    page_no     int NOT NULL,
    text        text NOT NULL,
    PRIMARY KEY (document_id, page_no)
);

CREATE TABLE meetings (
    id             bigserial PRIMARY KEY,
    document_id    bigint NOT NULL UNIQUE REFERENCES documents ON DELETE CASCADE,
    title          text NOT NULL,
    meeting_type   text NOT NULL
                   CHECK (meeting_type IN ('board', 'spring_general', 'autumn_general', 'extraordinary', 'other')),
    meeting_date   date,
    start_time     time,
    end_time       time,
    location       text,
    summary        text,
    raw_extraction jsonb NOT NULL,
    search_tsv     tsvector GENERATED ALWAYS AS (
                       to_tsvector('finnish'::regconfig, coalesce(title, '') || ' ' || coalesce(summary, ''))
                   ) STORED
);

CREATE INDEX meetings_date_idx ON meetings (meeting_date);
CREATE INDEX meetings_search_idx ON meetings USING gin (search_tsv);

CREATE TABLE people (
    id             bigserial PRIMARY KEY,
    canonical_name text NOT NULL UNIQUE
);

-- Every spelling of a name seen in the documents.
CREATE TABLE person_aliases (
    alias     text PRIMARY KEY,
    person_id bigint NOT NULL REFERENCES people ON DELETE CASCADE
);

CREATE INDEX person_aliases_trgm_idx ON person_aliases USING gin (alias gin_trgm_ops);

CREATE TABLE attendance (
    meeting_id      bigint NOT NULL REFERENCES meetings ON DELETE CASCADE,
    person_id       bigint NOT NULL REFERENCES people,
    status          text NOT NULL CHECK (status IN ('present', 'absent')),
    role            text,
    name_as_written text NOT NULL,
    PRIMARY KEY (meeting_id, person_id)
);

CREATE INDEX attendance_person_idx ON attendance (person_id);

-- An agenda item of a meeting.
CREATE TABLE topics (
    id          bigserial PRIMARY KEY,
    meeting_id  bigint NOT NULL REFERENCES meetings ON DELETE CASCADE,
    ordinal     int NOT NULL,
    item_number text,
    title       text NOT NULL,
    summary     text,
    decisions   text,
    page_no     int,
    search_tsv  tsvector GENERATED ALWAYS AS (
                    setweight(to_tsvector('finnish'::regconfig, title), 'A') ||
                    setweight(to_tsvector('finnish'::regconfig, coalesce(decisions, '')), 'B') ||
                    setweight(to_tsvector('finnish'::regconfig, coalesce(summary, '')), 'C')
                ) STORED,
    embedding   vector(1024),
    UNIQUE (meeting_id, ordinal)
);

CREATE INDEX topics_search_idx ON topics USING gin (search_tsv);
CREATE INDEX topics_title_trgm_idx ON topics USING gin (title gin_trgm_ops);
CREATE INDEX topics_embedding_idx ON topics USING hnsw (embedding vector_cosine_ops);

-- Raw document text in windows, so search doesn't rely only on the LLM's summaries.
CREATE TABLE chunks (
    id          bigserial PRIMARY KEY,
    document_id bigint NOT NULL REFERENCES documents ON DELETE CASCADE,
    ordinal     int NOT NULL,
    page_no     int,
    text        text NOT NULL,
    search_tsv  tsvector GENERATED ALWAYS AS (to_tsvector('finnish'::regconfig, text)) STORED,
    embedding   vector(1024),
    UNIQUE (document_id, ordinal)
);

CREATE INDEX chunks_search_idx ON chunks USING gin (search_tsv);
CREATE INDEX chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);

-- migrate:down

DROP TABLE chunks;
DROP TABLE topics;
DROP TABLE attendance;
DROP TABLE person_aliases;
DROP TABLE people;
DROP TABLE meetings;
DROP TABLE document_pages;
DROP TABLE documents;
