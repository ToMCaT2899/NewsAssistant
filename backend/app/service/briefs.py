"""Ownership-safe history and source-linked presentation."""

from sqlalchemy import select

from backend.app.infrastructure.database import Database
from backend.app.models.schema import Article, Brief, BriefItem, Delivery, ItemSource, Subscription
from backend.app.repository.queries import owned_brief


def brief_detail(database: Database, user_id: str, brief_id: str) -> dict:
    with database.session() as session:
        brief = owned_brief(session, user_id, brief_id)
        data = {
            key: getattr(brief, key)
            for key in (
                "id",
                "title",
                "overview",
                "business_date",
                "no_news",
                "created_at",
                "window_start",
                "window_end",
            )
        }
        data["items"] = []
        for item in session.scalars(
            select(BriefItem).where(BriefItem.brief_id == brief.id).order_by(BriefItem.position)
        ):
            sources = session.execute(
                select(Article, ItemSource.evidence_level)
                .join(ItemSource, ItemSource.article_id == Article.id)
                .where(ItemSource.item_id == item.id)
            ).all()
            data["items"].append(
                {
                    "headline": item.headline,
                    "summary": item.summary,
                    "why_it_matters": item.why_it_matters,
                    "sources": [
                        {
                            "url": article.canonical_url,
                            "title": article.title,
                            "source_name": article.source_name,
                            "published_at": article.published_at,
                            "evidence_level": level,
                        }
                        for article, level in sources
                    ],
                }
            )
        delivery = session.scalar(select(Delivery).where(Delivery.brief_id == brief.id))
        data["delivery"] = (
            {
                "id": delivery.id,
                "status": delivery.status,
                "attempt_count": delivery.attempt_count,
                "last_error_code": delivery.last_error_code,
            }
            if delivery
            else None
        )
        return data


def list_briefs(database: Database, user_id: str, limit: int, offset: int) -> list[dict]:
    with database.session() as session:
        rows = session.scalars(
            select(Brief)
            .join(Subscription, Brief.subscription_id == Subscription.id)
            .where(Subscription.user_id == user_id)
            .order_by(Brief.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return [
            {
                "id": row.id,
                "title": row.title,
                "business_date": row.business_date,
                "overview": row.overview,
                "no_news": bool(row.no_news),
            }
            for row in rows
        ]
