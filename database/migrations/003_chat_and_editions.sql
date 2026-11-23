-- 保留每日正式版，另存用户主动生成的版本，不覆盖已发送内容。
CREATE TABLE briefs_v3 (
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
    edition INTEGER NOT NULL DEFAULT 0 CHECK (edition >= 0),
    UNIQUE(subscription_id, business_date, edition),
    FOREIGN KEY (run_id, subscription_id, business_date)
        REFERENCES runs(id, subscription_id, business_date),
    CHECK (window_start < window_end)
);
INSERT INTO briefs_v3
    SELECT id, subscription_id, business_date, run_id, title, overview, body_markdown,
           no_news, window_start, window_end, created_at, 0 FROM briefs;
DROP TABLE briefs;
ALTER TABLE briefs_v3 RENAME TO briefs;
ALTER TABLE deliveries ADD COLUMN manual_requested INTEGER NOT NULL DEFAULT 0
    CHECK (manual_requested IN (0, 1));
CREATE TABLE chat_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_chat_sessions_user ON chat_sessions(user_id, updated_at);
CREATE TABLE chat_turns (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES chat_sessions(id),
    idempotency_key TEXT NOT NULL,
    question TEXT NOT NULL CHECK (length(question) BETWEEN 1 AND 2000),
    action TEXT NOT NULL CHECK (action IN ('chat', 'generate', 'generate_send')),
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    answer TEXT,
    error_code TEXT,
    run_id TEXT REFERENCES runs(id),
    events_json TEXT NOT NULL DEFAULT '[]',
    lease_owner TEXT,
    lease_expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(session_id, idempotency_key)
);
CREATE UNIQUE INDEX idx_chat_one_active ON chat_turns(session_id)
    WHERE status IN ('queued', 'running');
INSERT INTO schema_migrations(version, applied_at)
    VALUES (3, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
