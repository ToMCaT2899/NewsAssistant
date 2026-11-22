-- Per-run evidence scopes prevent cross-user or invented source references.
CREATE TABLE run_articles (
    run_id TEXT NOT NULL REFERENCES runs(id),
    article_id TEXT NOT NULL REFERENCES articles(id),
    evidence_level TEXT NOT NULL CHECK (evidence_level IN ('full_text', 'search_excerpt')),
    PRIMARY KEY (run_id, article_id)
);
CREATE INDEX idx_run_articles_article ON run_articles(article_id);
INSERT INTO schema_migrations(version, applied_at)
VALUES (2, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
