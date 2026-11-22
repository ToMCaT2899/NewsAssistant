# 每日 AI 新闻助手 NewsAssistant

根据用户身份、兴趣与关键词，每天自主检索和筛选 AI 新闻，生成带来源的个性化简报，通过 QQ 邮箱 SMTP 推送。

## 当前交付状态

当前交付设计文档、项目目录骨架、Docker Compose 工程配置和可执行的 SQLite 初始化脚本。
FastAPI 接口、LangGraph Agent、前端、调度器、搜索接入和真实邮件发送尚未实现。
Compose 当前运行检查与数据库初始化；业务实现后统一扩展为 API、worker、前端部署。

## 使用 Docker Compose

安装并启动 Docker Desktop（Linux 容器）或 Docker Engine + Compose 插件，在仓库根目录执行。
应用从官方 python:3.12-slim-bookworm 独立构建，不使用其他项目的应用镜像。
数据库等基础设施优先复用本机已有镜像；当前 SQLite 为文件存储，无需数据库镜像。
宿主机不必安装 Python、uv 或 Node；Ruff 静态检查使用现有本地开发环境。

```powershell
docker compose config --quiet
docker compose build
docker compose run --rm check
```

检查服务运行数据库约束、文档链接、配置与 Python 语法检查。修改代码后重新 build。
Ruff 仍由宿主机现有环境执行。Python 基础镜像优先使用缓存，缺少时构建会获取官方镜像。
也可以用以下命令构建并运行默认检查服务，将检查退出码返回终端：

```powershell
docker compose up --build --abort-on-container-exit --exit-code-from check
```

首次初始化数据库，单独执行：

```powershell
docker compose build db-init
docker compose run --rm db-init
```

SQLite 保存在 Docker 命名卷 news-data，容器路径为 /app/data/news_assistant.db。
目标已存在时初始化会拒绝覆盖；容器删除后数据保留。以前宿主机 data 目录不会自动导入。
检查和初始化均不需要 .env 或真实密钥。业务配置阶段复制 `Copy-Item .env.example .env`。
.env 同时被 Git 与镜像构建上下文忽略。不要使用 down -v 作为普通停止命令，它会删除数据库卷。

完整说明见 [Docker Compose 运行说明](docs/07-Docker-Compose运行说明.md)。

## 设计导航

- [运行流程图](docs/01-运行流程图.md)
- [概要设计](docs/02-概要设计.md)
- [详细设计](docs/03-详细设计.md)
- [数据库设计](docs/04-数据库设计.md)
- [项目结构与代码规范](docs/05-项目结构与代码规范.md)
- [实施计划与验收](docs/06-实施计划与验收.md)
- [Docker Compose 运行说明](docs/07-Docker-Compose运行说明.md)
- [Agent 与邮件架构决策](docs/adr/0001-agent与邮件可靠性.md)
- [Docker Compose 部署决策](docs/adr/0002-Docker-Compose部署.md)
- [开发约定](AGENTS.md)

## 技术方向与后续部署

Python 3.11+，容器采用 Python 3.12；FastAPI、LangGraph 显式 ReAct 图、SQLAlchemy、SQLite；
前端 Vue 3 + TypeScript + Vite。MVP 使用一个独立 worker，不引入 Redis、Celery 或多 Agent。
模型与新闻搜索供应商通过适配器注入。pyproject.toml/uv.lock 当前只锁定开发检查依赖。
Dockerfile 从官方 Python 镜像独立构建 base 阶段，以非 root 用户运行。
本项目应用镜像名为 news-assistant:local；当前脚本只使用标准库，后续依赖单独声明和锁定。
“复用已有镜像”仅用于数据库、Redis 等基础设施镜像，不继承其他项目代码或配置。

后续补齐业务代码、运行时依赖、前端锁文件和健康检查后，再增加 api、worker、frontend、
migrate 服务，统一用 `docker compose up -d --build` 启动完整系统。
当前不提供不存在的业务服务启动命令，不能将检查通过视为 Agent 已运行。

## 需求基线

- 每日定时生成、手动触发、个性化订阅、历史简报、QQ 邮件推送。
- LLM 决定工具、参数、次数和正常终止；程序执行权限、超时、预算及业务校验。
- 至少五个工具；保留 list_dir/read_file/search_content/write_file/bash，再提供新闻工具。
- Git 保留提交历史；密钥使用环境变量；依赖锁定；提供建表/迁移脚本。
- 运行和验收以 Docker Compose 为主要入口；原生 Python 执行只作为维护调试手段。
