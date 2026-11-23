"""基于 RSS 的关键词检索与单次任务的来源追溯。"""

import calendar
import hashlib
import json
from datetime import UTC, datetime, timedelta

import feedparser
from bs4 import BeautifulSoup
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.app.agent.tools.registry import Tool
from backend.app.conf.settings import Settings
from backend.app.domain.common import AppError, now, parse_time, required, stamp
from backend.app.domain.schemas import BriefDraft, Contract
from backend.app.infrastructure.database import Database
from backend.app.infrastructure.web_reader import WebReader, canonical_url
from backend.app.models.schema import Article, Brief, Run, RunArticle


class FeedInput(Contract):
    source_id: str = Field(min_length=1, max_length=80)


class SearchInput(Contract):
    query: str = Field(min_length=1, max_length=200)
    start_time: str
    end_time: str
    limit: int = Field(10, ge=1, le=20)


class ArticleInput(Contract):
    article_id: str = Field(min_length=1, max_length=80)


class HistoryInput(Contract):
    days: int = Field(7, ge=1, le=30)


class NewsTools:
    def __init__(
        self,
        database: Database,
        run: Run,
        settings: Settings,
        reader: WebReader | None = None,
    ) -> None:
        self.database, self.run = database, run
        self.reader = reader or WebReader()
        self.sources: dict[str, str] = json.loads(settings.rss_sources_json)
        if not 1 <= len(self.sources) <= 10:
            raise AppError("SOURCES_NOT_CONFIGURED", "请配置 1～10 个新闻来源")
        self.cache: dict[str, list[dict]] = {}
        self.successful_reads = 0

    def available(self) -> dict:
        return {
            "sources": self.sources,
            "window_start": self.run.window_start,
            "window_end": self.run.window_end,
        }

    async def fetch_feed(self, source_id: str) -> list[dict]:
        if source_id not in self.sources:
            raise AppError("UNKNOWN_SOURCE", "来源未配置")
        if source_id in self.cache:
            return self.cache[source_id]
        data = await self.reader.get(self.sources[source_id])
        parsed = feedparser.parse(data)
        if not parsed.get("version"):
            raise AppError("INVALID_FEED", "来源不是可识别的 RSS/Atom")
        articles = []
        for entry in parsed.entries[:200]:
            date = entry.get("published_parsed") or entry.get("updated_parsed")
            if not date or not entry.get("link"):
                continue
            published = datetime.fromtimestamp(calendar.timegm(date), UTC)
            if not parse_time(self.run.window_start) <= published < parse_time(self.run.window_end):
                continue
            try:
                url = canonical_url(entry.link)
            except AppError:
                continue
            article_id = hashlib.sha256(url.encode()).hexdigest()[:40]
            excerpt = BeautifulSoup(entry.get("summary", ""), "html.parser").get_text(
                " ", strip=True
            )
            with self.database.session.begin() as session:
                session.execute(
                    sqlite_insert(Article)
                    .values(
                        id=article_id,
                        canonical_url=url,
                        original_url=entry.link,
                        title=str(entry.get("title", "未命名"))[:500],
                        source_name=source_id,
                        published_at=stamp(published),
                        fetched_at=stamp(),
                        body_text=excerpt[:30000],
                        evidence_level="search_excerpt",
                        created_at=stamp(),
                    )
                    .on_conflict_do_nothing(index_elements=["canonical_url"])
                )
                session.execute(
                    sqlite_insert(RunArticle)
                    .values(
                        run_id=self.run.id,
                        article_id=article_id,
                        evidence_level="search_excerpt",
                    )
                    .on_conflict_do_nothing()
                )
            articles.append(
                {
                    "article_id": article_id,
                    "title": str(entry.get("title", ""))[:500],
                    "url": url,
                    "published_at": stamp(published),
                    "source": source_id,
                    "excerpt": excerpt[:1500],
                    "evidence_level": "search_excerpt",
                }
            )
            if len(articles) >= 50:
                break
        self.cache[source_id] = articles
        self.successful_reads += 1
        return articles

    async def search_news(
        self,
        query: str,
        start_time: str,
        end_time: str,
        limit: int = 10,
    ) -> object:
        start, end = parse_time(start_time), parse_time(end_time)
        if not parse_time(self.run.window_start) <= start < end <= parse_time(self.run.window_end):
            raise AppError("INVALID_WINDOW", "搜索窗口超出本次任务范围")
        rows: list[dict] = []
        failures = 0
        for source_id in self.sources:
            try:
                rows.extend(await self.fetch_feed(source_id))
            except (AppError, OSError, TimeoutError):
                failures += 1
        if failures == len(self.sources):
            raise AppError("NEWS_UNAVAILABLE", "所有新闻源均不可用")
        words = query.casefold().split()
        filtered = [
            row
            for row in rows
            if start <= parse_time(row["published_at"]) < end
            and any(word in (row["title"] + " " + row["excerpt"]).casefold() for word in words)
        ]
        return {
            "items": filtered[:limit],
            "failed_sources": failures,
            "scope": "configured_rss_feeds",
        }

    async def fetch_article(self, article_id: str) -> object:
        with self.database.session() as session:
            scope = session.get(RunArticle, (self.run.id, article_id))
            article = session.get(Article, article_id)
            if scope is None or article is None:
                raise AppError("SOURCE_DENIED", "来源未在本次运行中登记")
            url = article.canonical_url
        html = await self.reader.get(url)
        soup = BeautifulSoup(html, "html.parser")
        for element in soup(["script", "style", "nav", "footer", "header", "iframe", "form"]):
            element.decompose()
        node = soup.find("article") or soup.find("main") or soup
        body = node.get_text(" ", strip=True)[:30000]
        if not body:
            raise AppError("ARTICLE_EMPTY", "正文为空，仍只能使用摘要证据")
        with self.database.session.begin() as session:
            article = session.get(Article, article_id)
            scope = session.get(RunArticle, (self.run.id, article_id))
            article, scope = required(article), required(scope)
            article.body_text, article.evidence_level = body, "full_text"
            article.content_hash = hashlib.sha256(body.encode()).hexdigest()
            article.fetched_at = stamp()
            scope.evidence_level = "full_text"
        return {"article_id": article_id, "url": url, "body": body, "evidence_level": "full_text"}

    async def get_recent_briefs(self, days: int = 7) -> object:
        with self.database.session() as session:
            briefs = session.scalars(
                select(Brief)
                .where(
                    Brief.subscription_id == self.run.subscription_id,
                    Brief.created_at >= stamp(now() - timedelta(days=days)),
                )
                .order_by(Brief.created_at.desc())
                .limit(30)
            )
            return [
                {"date": item.business_date, "title": item.title, "overview": item.overview}
                for item in briefs
            ]

    def validate_sources(self, draft: BriefDraft) -> None:
        if not self.successful_reads:
            raise ValueError("必须先成功查询至少一个真实新闻源")
        if len(draft.items) > json.loads(self.run.snapshot_json)["max_items"]:
            raise ValueError("条数超出订阅设置")
        excluded = json.loads(self.run.snapshot_json)["exclude_keywords"]
        for item in draft.items:
            text = (item.headline + item.summary).casefold()
            if any(word.casefold() in text for word in excluded):
                raise ValueError("内容命中排除词")
        with self.database.session() as session:
            for item in draft.items:
                for source in item.sources:
                    scope = session.get(RunArticle, (self.run.id, source.article_id))
                    if scope is None:
                        raise ValueError("引用不属于本次运行")
                    if source.evidence_level == "full_text" and scope.evidence_level != "full_text":
                        raise ValueError("尚未取得原文，不得标为全文证据")

    def definitions(self) -> list[Tool]:
        return [
            Tool(
                "search_news",
                "在配置的RSS源按关键词搜索；非全网搜索",
                SearchInput,
                self.search_news,
            ),
            Tool("fetch_feed", "读取一个已配置RSS源，获取本次窗口新闻", FeedInput, self.fetch_feed),
            Tool("fetch_article", "读取本次已登记新闻的原文", ArticleInput, self.fetch_article),
            Tool(
                "get_recent_briefs",
                "读取当前订阅最近简报避免重复",
                HistoryInput,
                self.get_recent_briefs,
            ),
        ]
