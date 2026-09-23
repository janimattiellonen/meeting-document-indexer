-- migrate:up

-- Voikko base forms and compound parts (search/lemmas.py), filled by the indexer and `mi relemmatize`.
-- Searched together with search_tsv: a word matches through its base form or through Postgres' stemming.
-- Existing rows start empty; `mi status` reports them until `mi relemmatize` has been run.
ALTER TABLE topics ADD COLUMN lemma_tsv tsvector NOT NULL DEFAULT ''::tsvector;
ALTER TABLE chunks ADD COLUMN lemma_tsv tsvector NOT NULL DEFAULT ''::tsvector;
-- True once lemma_tsv has been computed, even when the text has no words (lemma_tsv stays empty then).
ALTER TABLE topics ADD COLUMN lemmatized boolean NOT NULL DEFAULT false;
ALTER TABLE chunks ADD COLUMN lemmatized boolean NOT NULL DEFAULT false;

CREATE INDEX topics_match_idx ON topics USING gin ((search_tsv || lemma_tsv));
CREATE INDEX chunks_match_idx ON chunks USING gin ((search_tsv || lemma_tsv));

-- migrate:down

DROP INDEX chunks_match_idx;
DROP INDEX topics_match_idx;
ALTER TABLE chunks DROP COLUMN lemmatized, DROP COLUMN lemma_tsv;
ALTER TABLE topics DROP COLUMN lemmatized, DROP COLUMN lemma_tsv;
