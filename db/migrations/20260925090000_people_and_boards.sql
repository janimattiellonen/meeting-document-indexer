-- migrate:up

-- The meeting type, number and term year are read from the document's header in code (normalize.py);
-- the model's meeting_type was often wrong. classified is false for meetings stored before this, until
-- `mi refresh` recomputes them.
ALTER TABLE meetings
    ADD COLUMN meeting_number text,
    ADD COLUMN term_year      int,
    ADD COLUMN classified     boolean NOT NULL DEFAULT false;

CREATE INDEX meetings_type_term_year_idx ON meetings (meeting_type, term_year);

-- A spelling-insensitive form of the alias (people.name_key): "Esimerkki, Antti" and "Antti Esimerkki" share
-- one. NULL for aliases stored before this, until `mi refresh`.
ALTER TABLE person_aliases ADD COLUMN name_key text;

CREATE INDEX person_aliases_name_key_idx ON person_aliases (name_key);

-- Set by `mi people rename`: the name is kept instead of being recomputed from the most common spelling.
ALTER TABLE people ADD COLUMN name_fixed boolean NOT NULL DEFAULT false;

-- migrate:down

ALTER TABLE people DROP COLUMN name_fixed;
DROP INDEX person_aliases_name_key_idx;
ALTER TABLE person_aliases DROP COLUMN name_key;
DROP INDEX meetings_type_term_year_idx;
ALTER TABLE meetings DROP COLUMN classified, DROP COLUMN term_year, DROP COLUMN meeting_number;
