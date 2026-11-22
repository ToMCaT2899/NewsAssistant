-- Initial SQLite schema. Run only through scripts/init_db.py or a transactional runner.
-- UTC timestamps use YYYY-MM-DDTHH:MM:SS.sssZ; identifiers are application-generated UUIDs.
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_hash TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE subscriptions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL UNIQUE REFERENCES users(id),
    persona TEXT NOT NULL CHECK (length(persona) BETWEEN 1 AND 200),
    topics_json TEXT NOT NULL DEFAULT '[]',
    include_keywords_json TEXT NOT NULL DEFAULT '[]',
    exclude_keywords_json TEXT NOT NULL DEFAULT '[]',
    recipient_email TEXT NOT NULL,
    recipient_verified_at TEXT,
    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    local_send_time TEXT NOT NULL DEFAULT '08:00',
    max_items INTEGER NOT NULL DEFAULT 5 CHECK (max_items BETWEEN 1 AND 10),
    lookback_hours INTEGER NOT NULL DEFAULT 24 CHECK (lookback_hours BETWEEN 1 AND 72),
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    next_run_at TEXT,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (enabled = 0 OR recipient_verified_at IS NOT NULL)
);
CREATE INDEX idx_subscriptions_due ON subscriptions(enabled, next_run_at);
CREATE TABLE runs (
    id TEXT PRIMARY KEY,
    subscription_id TEXT NOT NULL REFERENCES subscriptions(id),
    business_date TEXT NOT NULL,
    trigger_type TEXT NOT NULL CHECK (trigger_type IN ('scheduled', 'manual', 'retry')),
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'succeeded', 'incomplete', 'failed', 'cancelled')),
    snapshot_json TEXT NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    model_calls INTEGER NOT NULL DEFAULT 0 CHECK (model_calls >= 0),
    tool_calls INTEGER NOT NULL DEFAULT 0 CHECK (tool_calls >= 0),
    token_usage_json TEXT,
    draft_json TEXT,
    error_code TEXT,
    error_message TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT,
    deadline_at TEXT,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(subscription_id, idempotency_key),
    UNIQUE(id, subscription_id, business_date),
    CHECK (window_start < window_end)
);
CREATE UNIQUE INDEX idx_runs_one_active ON runs(subscription_id)
    WHERE status IN ('queued', 'running');
CREATE INDEX idx_runs_queue ON runs(status, created_at);
CREATE INDEX idx_runs_history ON runs(subscription_id, business_date);
CREATE TABLE articles (
    id TEXT PRIMARY KEY,
    canonical_url TEXT NOT NULL UNIQUE,
    original_url TEXT NOT NULL,
    title TEXT NOT NULL,
    source_name TEXT NOT NULL,
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    body_text TEXT,
    content_hash TEXT,
    evidence_level TEXT NOT NULL CHECK (evidence_level IN ('full_text', 'search_excerpt')),
    created_at TEXT NOT NULL
);
CREATE INDEX idx_articles_published ON articles(published_at);
CREATE TABLE tool_calls (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    model_tool_call_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    result_summary TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'rejected')),
    error_code TEXT,
    duration_ms INTEGER CHECK (duration_ms >= 0),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    UNIQUE(run_id, sequence)
);
CREATE TABLE briefs (
    id TEXT PRIMARY KEY,
    subscription_id TEXT NOT NULL REFERENCES subscriptions(id),
    business_date TEXT NOT NULL,
    run_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    overview TEXT NOT NULL,
    body_markdown TEXT NOT NULL,
    no_news INTEGER NOT NULL DEFAULT 0 CHECK (no_news IN (0, 1)),
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(subscription_id, business_date),
    FOREIGN KEY (run_id, subscription_id, business_date)
        REFERENCES runs(id, subscription_id, business_date),
    CHECK (window_start < window_end)
);
CREATE TABLE brief_items (
    id TEXT PRIMARY KEY,
    brief_id TEXT NOT NULL REFERENCES briefs(id),
    position INTEGER NOT NULL CHECK (position > 0),
    headline TEXT NOT NULL,
    summary TEXT NOT NULL,
    why_it_matters TEXT NOT NULL,
    event_key TEXT NOT NULL,
    UNIQUE(brief_id, position),
    UNIQUE(brief_id, event_key)
);
CREATE TABLE item_sources (
    item_id TEXT NOT NULL REFERENCES brief_items(id) ON DELETE CASCADE,
    article_id TEXT NOT NULL REFERENCES articles(id),
    evidence_level TEXT NOT NULL CHECK (evidence_level IN ('full_text', 'search_excerpt')),
    PRIMARY KEY(item_id, article_id)
);
CREATE INDEX idx_item_sources_article ON item_sources(article_id);
CREATE TABLE deliveries (
    id TEXT PRIMARY KEY,
    brief_id TEXT NOT NULL REFERENCES briefs(id),
    channel TEXT NOT NULL DEFAULT 'email' CHECK (channel = 'email'),
    recipient_email TEXT NOT NULL,
    message_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sending', 'accepted', 'retry_wait',
                         'failed', 'unknown', 'cancelled')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_attempt_at TEXT NOT NULL,
    lease_owner TEXT,
    lease_expires_at TEXT,
    accepted_at TEXT,
    last_error_code TEXT,
    last_error_message TEXT,
    retry_confirmed_by TEXT REFERENCES users(id),
    retry_confirmation_reason TEXT,
    retry_confirmed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(brief_id, channel)
);
CREATE INDEX idx_deliveries_due ON deliveries(status, next_attempt_at);
CREATE TABLE delivery_attempts (
    id TEXT PRIMARY KEY,
    delivery_id TEXT NOT NULL REFERENCES deliveries(id),
    attempt_no INTEGER NOT NULL CHECK (attempt_no > 0),
    status TEXT NOT NULL
        CHECK (status IN ('sending', 'accepted', 'retryable_failure', 'failed', 'unknown')),
    smtp_code INTEGER CHECK (smtp_code BETWEEN 100 AND 599),
    error_code TEXT,
    error_message TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    UNIQUE(delivery_id, attempt_no)
);
INSERT INTO schema_migrations(version, applied_at)
VALUES (1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
