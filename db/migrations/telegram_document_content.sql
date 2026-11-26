BEGIN;

ALTER TABLE telegram_central_updates
    ADD COLUMN IF NOT EXISTS document_content TEXT NULL;

COMMIT;
