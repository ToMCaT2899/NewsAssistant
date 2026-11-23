"""聊天只读工具，用户身份由服务注入，模型不能选择用户或调用写操作。"""

from pydantic import Field

from backend.app.agent.tools.registry import Tool
from backend.app.domain.schemas import Contract
from backend.app.infrastructure.database import Database
from backend.app.service.accounts import read_subscription
from backend.app.service.briefs import brief_detail, list_briefs


class BriefInput(Contract):
    brief_id: str = Field(min_length=1, max_length=80)


class EmptyInput(Contract):
    pass


class ChatTools:
    def __init__(self, database: Database, user_id: str) -> None:
        self.database, self.user_id = database, user_id

    async def list_briefs(self) -> object:
        return list_briefs(self.database, self.user_id, 10, 0)

    async def read_brief(self, brief_id: str) -> object:
        detail = brief_detail(self.database, self.user_id, brief_id)
        detail.pop("delivery", None)
        return detail

    async def read_preferences(self) -> object:
        data = read_subscription(self.database, self.user_id)
        return {
            key: data[key]
            for key in (
                "persona",
                "topics",
                "include_keywords",
                "exclude_keywords",
                "max_items",
                "lookback_hours",
            )
        }

    def definitions(self) -> list[Tool]:
        return [
            Tool("list_briefs", "查看当前用户最近简报", EmptyInput, self.list_briefs),
            Tool("read_brief", "读取当前用户一份简报和来源链接", BriefInput, self.read_brief),
            Tool("read_preferences", "读取当前用户的新闻偏好", EmptyInput, self.read_preferences),
        ]
