
BEGIN;

-- =====================================================
-- 1. TELEGRAM CENTRAL LINKS
-- =====================================================

CREATE TABLE telegram_central_links (
    id VARCHAR(36) PRIMARY KEY,

    user_id VARCHAR(36) NOT NULL,

    telegram_user_id BIGINT NULL,
    telegram_username VARCHAR(64) NULL,

    conversation_id VARCHAR(36) NULL,

    link_code_hash VARCHAR(64) NULL,
    link_code_expires_at TIMESTAMP NULL,

    linked_at TIMESTAMP NULL,

    is_active BOOLEAN NOT NULL DEFAULT FALSE,

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_telegram_central_links_user_id
        UNIQUE (user_id),

    CONSTRAINT uq_telegram_central_links_telegram_user_id
        UNIQUE (telegram_user_id),

    CONSTRAINT uq_telegram_central_links_link_code_hash
        UNIQUE (link_code_hash),

    CONSTRAINT fk_telegram_central_links_user
        FOREIGN KEY (user_id)
        REFERENCES users_jwt(id)
        ON DELETE CASCADE,

    CONSTRAINT fk_telegram_central_links_conversation
        FOREIGN KEY (conversation_id)
        REFERENCES conversations_orm(id)
        ON DELETE SET NULL
);

CREATE INDEX ix_telegram_central_links_active
    ON telegram_central_links (is_active);


-- =====================================================
-- 2. TELEGRAM CENTRAL UPDATES
-- =====================================================

CREATE TABLE telegram_central_updates (
    id VARCHAR(36) PRIMARY KEY,

    update_id BIGINT NOT NULL,

    telegram_user_id BIGINT NULL,
    telegram_message_id BIGINT NULL,

    user_id VARCHAR(36) NULL,
    reservation_id VARCHAR(36) NULL,
    assistant_message_id VARCHAR(36) NULL,

    response_sent_at TIMESTAMP NULL,

    status VARCHAR(20) NOT NULL DEFAULT 'processing',
    error_code VARCHAR(100) NULL,

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP NULL,

    CONSTRAINT uq_telegram_central_updates_update_id
        UNIQUE (update_id),

    CONSTRAINT ck_telegram_central_updates_status
        CHECK (
            status IN (
                'processing',
                'reply_pending',
                'completed',
                'failed',
                'needs_review'
            )
        ),

    CONSTRAINT fk_telegram_central_updates_user
        FOREIGN KEY (user_id)
        REFERENCES users_jwt(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_telegram_central_updates_reservation
        FOREIGN KEY (reservation_id)
        REFERENCES token_reservations(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_telegram_central_updates_message
        FOREIGN KEY (assistant_message_id)
        REFERENCES messages_orm(id)
        ON DELETE SET NULL
);

CREATE INDEX ix_telegram_central_updates_status_created
    ON telegram_central_updates (status, created_at);

COMMIT;
