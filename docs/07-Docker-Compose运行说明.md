# Docker Compose 运行说明

## 1. 当前可运行范围
仓库尚无 API、worker 或前端实现；当前 Compose 提供真实的检查与数据库初始化任务。
不会启动占位 Web 服务，也不把检查容器成功退出当作新闻系统已经上线。

安装 Docker Engine 与 Compose 插件；Windows/macOS 使用 Docker Desktop 并启用 Linux 容器。
检查和初始化无需在宿主机安装 Python、uv 或 Node。命令在仓库根目录执行。
应用镜像 news-assistant:local 从官方 python:3.12-slim-bookworm 独立构建。
应用基础镜像优先用本地缓存，缺少时构建需要获取官方镜像，或事先通过 docker load 导入。
不使用其他项目的 backend/api 镜像作为基础，也不复用其他项目代码、依赖环境或配置。
当前检查只用标准库，不在构建阶段额外安装 Python 包；Ruff 使用宿主机已有环境。
.env 的 PYTHON_BASE_IMAGE 可指定另一个兼容的官方 Python 基础镜像。

“复用本地镜像”指数据库、缓存等基础设施，不是应用镜像：
- 当前使用 SQLite 文件，无需数据库容器，也不新增 MySQL/PostgreSQL/Redis 依赖。
- 后续若需要关系数据库，可按选型使用已有 mysql:8.4 或 postgres:15-alpine。
- 后续若需要缓存或队列，可使用已有 redis:7.4-alpine。
- 对新增基础设施服务设置 pull_policy: never；使用独立命名卷、网络和账号，
  复用镜像不代表共享其他项目运行中的数据库、数据卷或凭证。

## 2. 构建与检查
```powershell
docker compose config --quiet
docker compose build
docker compose run --rm check
```

默认服务是一次性 check；也可以使用：
```powershell
docker compose up --build --abort-on-container-exit --exit-code-from check
```

check 执行文档链接、数据库约束、Python 语法和配置检查，均只依赖标准库。
Ruff 使用宿主机已有环境：uv run --locked --only-dev --no-sync ruff check scripts，
以及 uv run --locked --only-dev --no-sync ruff format --check scripts；不触发依赖同步。
检查的是构建时复制进镜像的代码，修改文件后必须重新 build；不会挂载整个宿主机目录。
本项目代码位于 /opt/news-assistant，数据路径为 /app/data，镜像中不包含其他项目应用。
容器以 UID/GID 10001 非 root 用户运行，根文件系统只读，临时文件使用 /tmp tmpfs。
检查容器不挂载数据卷，不加载 .env，不继承模型或 QQ 邮箱凭证。

## 3. 数据库初始化
```powershell
docker compose build db-init
docker compose run --rm db-init
```

db-init 使用 tools profile，显式 run 时自动启用；普通 up 不会重复初始化数据库。
SQLite 存在 Docker 命名卷 news-data 中，容器路径 /app/data/news_assistant.db。
Compose 项目名为 news-assistant，默认实际卷名为 news-assistant_news-data；
若通过 -p 修改项目名，会使用另一个独立卷。API 和 worker 实现后必须共享同一项目的数据卷。
卷从镜像中权限为 app:app 的 /app/data 目录初始化，服务不需要 root 运行。
初始化只允许新数据库；目标已存在时退出非零并明确拒绝覆盖。
已有宿主机 data/news_assistant.db 不会自动导入命名卷，切换前需显式备份与迁移。

## 4. 配置与敏感信息
使用默认官方基础镜像时，检查和初始化不要求创建 .env。需要修改基础镜像或业务配置时复制模板：
```powershell
Copy-Item .env.example .env
```

.env 已同时被 Git 与 Docker 构建上下文忽略，授权码不会随 COPY 进入镜像。
模板使用容器内绝对路径：DATABASE_URL=sqlite:////app/data/news_assistant.db、
AGENT_WORKSPACE_ROOT=/app/workspace。四个斜杠表示 SQLAlchemy 的绝对 SQLite 路径。
Compose 自动读取 .env 用于变量插值，不代表变量自动进入服务容器；
未来 API/worker 必须通过 environment 显式传入需要的字段，前端不得注入任何密钥。
不要把 docker compose config 的完整展开结果贴入公开日志，业务服务接入后可能包含凭证。

## 5. 后续业务部署约定（尚未实现）
- api：后端运行镜像；健康检查；仅必要 HTTP 端口；挂载 news-data。
- worker：与 api 同版本镜像，独立启动命令；挂载 news-data 与独立 news-workspace；
  注入模型、搜索和 QQ SMTP 凭证；MVP 保持一个 worker。
- frontend：Node 构建 Vue 静态产物，Nginx 等提供静态资源并将 /api 反向代理到 api；
  浏览器不使用容器内部服务名，不直接请求模型或 SMTP。
- migrate：一次性迁移任务，成功后再启动 api/worker；当前 db-init 仅用于全新数据库，
  不能在每次启动时重复执行，也不能代替后续迁移。
- 业务容器按需连接网络，使用 restart: unless-stopped、健康检查和停止宽限期；
  一次性任务 restart: "no"。depends_on 启动顺序不能代替运行期间的失败处理。
- SQLite 是共享文件，不需要单独“SQLite 数据库容器”；启用 WAL，限制单机本地卷。
- Agent 文件工具仅访问 worker 的 run 工作区，不挂载 Docker socket 或宿主机仓库。
- 不在现阶段 compose.yaml 填不存在的模块入口。业务实现时补齐镜像阶段、
  Compose 服务与端到端验证后，统一使用 docker compose up -d --build 启动完整应用。

## 6. 停止、数据与备份
```powershell
docker compose ps -a
docker compose logs check
docker compose down
```

down 默认保留命名卷；down -v 会删除数据库，不能作为普通停止命令。
备份前停止写入或使用 SQLite 在线备份 API，不能在 WAL 活跃写入时只复制主 .db 文件。
恢复时保持卷目录所有者 UID/GID 10001，并执行 integrity_check 与 foreign_key_check。
首次初始化失败应检查卷权限，不能用开放目录所有权限或 root 常驻绕过问题。

## 7. 官方参考
- [Compose profiles 与显式 run](https://docs.docker.com/compose/how-tos/profiles/)
- [容器环境变量](https://docs.docker.com/compose/how-tos/environment-variables/set-environment-variables/)
- [Compose 构建配置](https://docs.docker.com/reference/compose-file/build/)
