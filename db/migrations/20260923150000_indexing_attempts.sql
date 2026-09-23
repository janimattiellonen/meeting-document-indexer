-- migrate:up

-- timed_out: the document hit the per-document time limit. pending: registered, not yet processed.
ALTER TABLE documents DROP CONSTRAINT documents_status_check;
ALTER TABLE documents ADD CONSTRAINT documents_status_check
    CHECK (status IN ('pending', 'indexed', 'no_text', 'failed', 'timed_out'));

-- A pending document hasn't been read yet, so it has no hash.
ALTER TABLE documents ALTER COLUMN sha256 DROP NOT NULL;

ALTER TABLE documents
    ADD COLUMN attempts         int NOT NULL DEFAULT 0,
    ADD COLUMN last_attempt_at  timestamptz,
    ADD COLUMN duration_seconds real;

-- migrate:down

ALTER TABLE documents DROP COLUMN duration_seconds, DROP COLUMN last_attempt_at, DROP COLUMN attempts;
UPDATE documents SET sha256 = '' WHERE sha256 IS NULL;
ALTER TABLE documents ALTER COLUMN sha256 SET NOT NULL;
UPDATE documents SET status = 'failed' WHERE status = 'timed_out';
ALTER TABLE documents DROP CONSTRAINT documents_status_check;
ALTER TABLE documents ADD CONSTRAINT documents_status_check
    CHECK (status IN ('pending', 'indexed', 'no_text', 'failed'));
