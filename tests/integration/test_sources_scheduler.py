import json
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from backend.app.agent.tools.news import NewsTools
from backend.app.domain.common import now, stamp
from backend.app.domain.schemas import BriefDraft
from backend.app.models.schema import Run, RunArticle, Subscription
from backend.app.service.runs import claim_run, create_run
from backend.app.worker.main import schedule_due
from tests.integration.test_application import FeedFixture, owner


def test_duplicate_schedule_and_claim_are_idempotent(database, settings):
    with database.session.begin() as session:
        sub = session.scalar(select(Subscription))
        sub.enabled = 1
        sub.local_send_time = "00:00"
        sub.next_run_at = stamp(now() - timedelta(hours=1))
    schedule_due(database)
    schedule_due(database)
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(Run)) == 1
    assert claim_run(database, "worker-a", settings) is not None
    assert claim_run(database, "worker-b", settings) is None


@pytest.mark.asyncio
async def test_per_run_evidence_and_exclusion_are_validated(database, settings):
    ticket = create_run(database, owner(database), "source-scope")
    with database.session() as session:
        run = session.get(Run, ticket["run_id"])
    news = NewsTools(database, run, settings, FeedFixture())
    with pytest.raises(ValueError):
        news.validate_sources(BriefDraft(title="none", overview="not researched", no_news=True))
    rows = await news.fetch_feed("fixture")
    assert len(rows) == 1
    article_id = rows[0]["article_id"]
    draft = BriefDraft.model_validate(
        {
            "title": "test",
            "overview": "test",
            "items": [
                {
                    "headline": "Open model",
                    "summary": "AI tooling",
                    "why_it_matters": "test",
                    "event_key": "test",
                    "sources": [{"article_id": article_id, "evidence_level": "search_excerpt"}],
                }
            ],
        }
    )
    news.validate_sources(draft)
    draft.items[0].sources[0].evidence_level = "full_text"
    with pytest.raises(ValueError):
        news.validate_sources(draft)
    with database.session() as session:
        assert session.get(RunArticle, (run.id, article_id)).evidence_level == "search_excerpt"
    snapshot = json.loads(run.snapshot_json)
    snapshot["exclude_keywords"] = ["Open"]
    run.snapshot_json = json.dumps(snapshot)
    draft.items[0].sources[0].evidence_level = "search_excerpt"
    with pytest.raises(ValueError):
        news.validate_sources(draft)
