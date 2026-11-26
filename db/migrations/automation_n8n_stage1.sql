BEGIN;

CREATE TABLE automation_workflows (
    id VARCHAR(36) PRIMARY KEY,
    n8n_workflow_id VARCHAR(100) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    webhook_path VARCHAR(512) UNIQUE,
    input_schema JSON NOT NULL DEFAULT '{}'::json,
    output_schema JSON NOT NULL DEFAULT '{}'::json,
    access_mode VARCHAR(20) NOT NULL DEFAULT 'selected',
    is_published BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    timeout_seconds INTEGER NOT NULL DEFAULT 120,
    created_by VARCHAR(36)
        REFERENCES users_jwt(id) ON DELETE SET NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_automation_workflows_published_active
    ON automation_workflows (is_published, is_active);


CREATE TABLE automation_workflow_access (
    id VARCHAR(36) PRIMARY KEY,
    workflow_id VARCHAR(36) NOT NULL
        REFERENCES automation_workflows(id) ON DELETE CASCADE,
    user_id VARCHAR(36) NOT NULL
        REFERENCES users_jwt(id) ON DELETE CASCADE,
    is_allowed BOOLEAN NOT NULL DEFAULT TRUE,
    granted_by VARCHAR(36)
        REFERENCES users_jwt(id) ON DELETE SET NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_automation_access_workflow_user
        UNIQUE (workflow_id, user_id)
);

CREATE INDEX ix_automation_access_user_allowed
    ON automation_workflow_access (user_id, is_allowed);


CREATE TABLE automation_executions (
    id VARCHAR(36) PRIMARY KEY,
    workflow_id VARCHAR(36)
        REFERENCES automation_workflows(id) ON DELETE SET NULL,
    workflow_name VARCHAR(255) NOT NULL,
    user_id VARCHAR(36) NOT NULL
        REFERENCES users_jwt(id) ON DELETE CASCADE,
    correlation_id VARCHAR(36) NOT NULL UNIQUE,
    n8n_execution_id VARCHAR(100),
    status VARCHAR(20) NOT NULL DEFAULT 'queued',
    request_data JSON NOT NULL DEFAULT '{}'::json,
    result_data JSON,
    error_message TEXT,
    started_at TIMESTAMP WITHOUT TIME ZONE,
    finished_at TIMESTAMP WITHOUT TIME ZONE,
    duration_ms INTEGER,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_automation_executions_user_created
    ON automation_executions (user_id, created_at);

CREATE INDEX ix_automation_executions_workflow_created
    ON automation_executions (workflow_id, created_at);

CREATE INDEX ix_automation_executions_status
    ON automation_executions (status, created_at);

COMMIT;
