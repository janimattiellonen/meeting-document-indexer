-- migrate:up

-- Paths are stored NFC-normalized (see extract.normalize_path); earlier rows kept the form found on disk.

-- If the new code already ran against this database, the same path can exist in both forms, and the
-- UPDATE below would violate UNIQUE (rel_path). Keep one row per path: the indexed one, else the newest.
DELETE FROM documents d
USING (
    SELECT id, row_number() OVER (
               PARTITION BY normalize(rel_path, NFC)
               ORDER BY status = 'indexed' DESC, updated_at DESC, id DESC
           ) AS rank
    FROM documents
) ranked
WHERE d.id = ranked.id AND ranked.rank > 1;

UPDATE documents SET rel_path = normalize(rel_path, NFC) WHERE rel_path IS NOT NFC NORMALIZED;

-- migrate:down

-- Nothing to undo: the original (NFD) form is not recorded, and NFC paths still find the same files.
