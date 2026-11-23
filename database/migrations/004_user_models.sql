-- 用户模型密钥仅保存加密后的密文，修订号用于防止旧表单覆盖。
CREATE TABLE user_model_configs (
    user_id TEXT PRIMARY KEY REFERENCES users(id),
    base_url TEXT NOT NULL DEFAULT '',
    model_name TEXT NOT NULL DEFAULT '',
    encrypted_api_key TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    tested_at TEXT,
    updated_at TEXT NOT NULL
);
INSERT INTO schema_migrations(version, applied_at)
    VALUES (4, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
