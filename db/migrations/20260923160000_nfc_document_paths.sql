-- migrate:up

-- Paths are stored NFC-normalized (see extract.normalize_path); earlier rows kept the form found on disk.
UPDATE documents SET rel_path = normalize(rel_path, NFC) WHERE rel_path IS NOT NFC NORMALIZED;

-- migrate:down

-- Nothing to undo: the original (NFD) form is not recorded, and NFC paths still find the same files.
