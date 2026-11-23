# AI 晨报 · NewsAssistant

按身份、兴趣和关键词订阅，每日由 LangGraph Agent 自主使用工具收集 AI 新闻，
生成可追溯简报，保存到历史并通过 QQ 邮箱 SMTP 推送。

## 当前实现与验证边界

已实现 FastAPI 认证和订阅、LangGraph 工具循环、五个文件/受限命令工具、RSS 新闻工具、
简报与来源持久化、独立调度 worker、事务 outbox、QQ SMTP 适配及 Vue 阅读页面。
已提供 v1 → v2 SQL 迁移、依赖锁文件、Docker Compose 和离线测试。

2026-11-23 已在隔离数据库中使用真实模型、RSS 原文和 QQ SMTP 完成一次三条新闻简报投递，
用户确认实际收件、内容和链接正常；[真实联调记录](docs/development/NA-LIVE-20261123-001.md)
记录调用次数、SMTP 状态和检查边界。**尚未公开部署，也未通过长期每日调度验收**。
离线模型、RSS 和 SMTP 替身仅存在于测试注入路径；
生产缺少配置时明确报错，不生成假新闻或假发送成功。
新闻检索当前覆盖配置的 RSS 源，不是全网搜索。默认包括 Hugging Face、arXiv、
OpenAI 与 TechCrunch AI；来源可能返回空内容、近期无更新或暂时不可用。
本次前两个源在 72 小时窗口内没有条目，补充后两个源后才生成有新闻的简报。

## Docker Compose 启动

宿主机只需 Docker Desktop（Linux 容器）或 Docker Engine + Compose。
在仓库根目录执行：

```powershell
Copy-Item .env.example .env
# 编辑 .env，填写 JWT_SECRET、ADMIN_EMAIL、ADMIN_PASSWORD。
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

浏览器访问 [本地 AI 晨报](http://127.0.0.1:8080)。端口占用时修改 APP_PORT。
JWT_SECRET 至少 32 个随机字符；ADMIN_PASSWORD 至少 12 字符，二者不能使用示例常量。
migrate 会升级数据库并按环境配置首次创建账号；已有账号不会被启动过程覆盖密码。
缺少账号配置时仍可迁移和检查健康状态，但不能登录；补齐后重新运行 migrate。

可在构建后用容器生成随机 JWT_SECRET，复制到本地 .env：

```powershell
docker compose run --rm --no-deps migrate python -c "import secrets; print(secrets.token_urlsafe(48))"
```

首次初始化后的 .env 包含私密信息，仅保留本机；Git 与镜像构建均忽略它。

## 模型、新闻与 QQ 邮箱

- 模型：填写 LLM_BASE_URL（含 /v1 等实际 API 前缀）、LLM_MODEL、LLM_API_KEY。
  提供商必须支持 chat completions 的 tools/tool_calls 协议。未提供凭证时生成请求返回 503。
- 新闻：RSS_SOURCES_JSON 配置最多 10 个来源 ID/URL，模型可自主选源、换关键词和读原文。
  使用真实来源，不调用模型知识充当今日新闻；仅摘要证据会明确标注。
- QQ 邮件：在 QQ 邮箱开启 SMTP，填写 SMTP_USERNAME、SMTP_AUTH_CODE；
  使用 smtp.qq.com:465 和 TLS，授权码不是 QQ 登录密码。
- 收件邮箱：管理员确认自己控制或获授权的地址后设置 VERIFIED_RECIPIENT_EMAIL，
  或在已有账号上执行以下命令：

```powershell
docker compose exec api python -m backend.app.cli verify-recipient --user-email <账号邮箱> --recipient <已核实的收件邮箱>
```

在页面保存兴趣、关键词与身份；开启每日推送前需要已验证收件邮箱。
“立即整理”也能在关闭每日推送时生成网页简报，这种情况下不创建邮件投递。
每天的发送时间是开始整理的时间，完成后再发送，不保证精确到达分钟。

## 服务与数据

- frontend：Vue 构建产物与 Nginx，唯一对宿主机开放的 HTTP 入口。
- api：鉴权、订阅、任务、历史与工具记录；不启动调度器。
- worker：每日扫描、生成、投递、过期恢复；MVP 保持一个实例。
- migrate：一次性版本迁移，成功后 api/worker 启动。
- check、db-init：tools profile 下的显式维护任务，不混入日常服务启动。

应用镜像从官方 Python/Node 基础独立构建；Nginx 复用已存在的官方镜像并固定 digest。
基础设施镜像复用不代表共享其他项目数据库、数据卷或账号。
当前 SQLite 无需独立数据库容器，api/worker 共享 news-data，文件工具只访问 worker 的
news-workspace/run_id。旧宿主机 data/*.db 不会自动导入命名卷。

```powershell
docker compose logs --tail 100 api worker
docker compose down
```

down 保留命名卷；不要把 down -v 当作常规停止命令。
SMTP accepted 仅代表邮件服务器接受。提交后超时或崩溃的 unknown 状态不自动重发；
先核查邮箱，再在页面明确确认可能重复发送。数据库备份必须考虑 SQLite WAL 一致性。

## 检查与开发

```powershell
docker compose --profile tools build check
docker compose run --rm check
```

check 容器断网执行文档/DDL、Ruff、mypy 和 pytest，不请求真实模型或发送邮件。
前端镜像构建时执行 npm ci、lint、格式、类型与生产构建检查。

宿主机维护检查（需 uv、Node）：

```powershell
uv sync --locked
uv run ruff check backend scripts tests
uv run mypy backend
uv run python -m pytest --basetemp=artifacts/pytest-local
cd frontend
npm ci
npm run lint
npm run format:check
npm run build
```

首次宿主机 pytest 前创建 artifacts 父目录；它被 Git 忽略。
可用宿主机已有 Chrome 执行 `uv run python scripts/browser_smoke.py`：
自动生成隔离测试账号和数据库，验证真实本地 API 与页面；不启动 worker，不证明真实 LLM/QQ 联调。
完整 Compose 冒烟入口与实际结果见[运行说明](docs/07-Docker-Compose运行说明.md)。

真实服务验收会消耗模型额度；加 `--send-mail` 会向配置的 SMTP 发件邮箱自发自收一封。
不启动调度，不使用日常数据库；`--require-news` 防止将空简报记为内容验收通过。
默认 migrate 断网，因此用临时覆盖文件开启验收容器网络：

```powershell
New-Item -ItemType Directory -Force artifacts | Out-Null
@"
services:
  migrate:
    network_mode: bridge
"@ | Set-Content -Encoding utf8 artifacts/live-compose.yaml
docker compose build migrate
docker compose -f compose.yaml -f artifacts/live-compose.yaml run --rm --no-deps -T migrate python -m scripts.live_acceptance --require-news --send-mail
```

SMTP `250` 仅代表服务器接收；实际入箱和内容仍须收件人核查。
结果不确定时先查邮箱，不要反复执行此命令；每次执行是独立验收，会产生新的邮件。

## 设计与开发溯源

- [运行流程](docs/01-运行流程图.md)
- [概要设计](docs/02-概要设计.md)
- [详细设计](docs/03-详细设计.md)
- [数据库设计](docs/04-数据库设计.md)
- [项目结构与代码规范](docs/05-项目结构与代码规范.md)
- [实施与验收](docs/06-实施计划与验收.md)
- [Compose 运行说明](docs/07-Docker-Compose运行说明.md)
- [实现决策 ADR](docs/adr/0003-MVP实现决策.md)
- [本次执行记录与需求映射](docs/development/NA-RUN-20261122-001.md)
- [归档开发提示词](docs/prompts/README.md)
- [文字需求基线](docs/requirements/2026-11-22-需求基线.md)
- [开发约定](AGENTS.md)

使用 NA-DEV-001@v1.0.0 实现，起始提交 fe7f0ad。提示词正文、原始需求摘要和指纹保留不变；
执行记录独立维护。用户截图不上传 Git。
