# 每日 AI 新闻助手 NewsAssistant

根据用户身份、兴趣与关键词，每天自主检索和筛选 AI 新闻，生成带来源的个性化简报，通过 QQ 邮箱 SMTP 推送。

## 当前交付状态
本仓库当前提供**设计文档、项目目录骨架、工程配置和可执行的 SQLite 初始化脚本**。
FastAPI 接口、LangGraph Agent、前端、调度器、搜索接入和真实邮件发送尚未实现。
不得将设计中的接口、示例参数和计划中的能力视为已经可用。

## 设计导航
- [运行流程图](docs/01-运行流程图.md)
- [概要设计](docs/02-概要设计.md)
- [详细设计](docs/03-详细设计.md)
- [数据库设计](docs/04-数据库设计.md)
- [项目结构与代码规范](docs/05-项目结构与代码规范.md)
- [实施计划与验收](docs/06-实施计划与验收.md)
- [架构决策](docs/adr/0001-agent与邮件可靠性.md)
- [开发约定](AGENTS.md)

## 技术方向
Python 3.11+、FastAPI、LangGraph 显式 ReAct 图、SQLAlchemy、SQLite；前端 Vue 3 + TypeScript + Vite。
MVP 使用单个独立 worker 扫描数据库任务，不引入 Redis、Celery 或多 Agent。
模型与新闻搜索供应商通过适配器注入，选定前不写死供应商、价格或 SDK 版本。

## 现在可以执行
在仓库根目录运行，以下命令不需要第三方依赖：

```powershell
python scripts/init_db.py --database data/news_assistant.db
python scripts/check_design.py
git diff --check
```

初始化脚本创建数据库及建表版本记录；目标已存在时拒绝覆盖。数据文件已被 Git 忽略。
复制配置模板：`Copy-Item .env.example .env`；填入真实密钥前确认 .env 被忽略。

## 后续启动约定
业务实现阶段必须补齐后端依赖锁文件、前端 package.json 与锁文件，以及 API/worker/frontend 启动命令。
目前没有可启动的 Web 服务，不提供虚假的启动指令。运行时依赖在接入 SDK 并验证兼容性后锁定。
`pyproject.toml` 声明 Python 基线、Ruff 开发依赖与检查规则，`uv.lock` 锁定开发依赖。
安装开发检查工具：`uv sync --locked --only-dev`；检查：`uv run --locked --only-dev ruff check .`
和 `uv run --locked --only-dev ruff format --check .`。业务运行时依赖尚未接入。

## 需求基线
- 每日定时生成、手动触发、个性化订阅、历史简报、QQ 邮件推送。
- LLM 决定工具、参数、次数和正常终止；程序执行权限、超时、预算及业务校验。
- 实现至少五个工具；为稳妥覆盖题目，保留 list_dir/read_file/search_content/write_file/bash 五个基础工具，另提供新闻工具。
- Git 保留提交历史；密钥通过环境变量；提供依赖清单、建表/迁移脚本和真实安装说明。
- 本轮完成设计与基础结构，业务实现留到后续开发阶段。
