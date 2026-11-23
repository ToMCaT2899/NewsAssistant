# Docker Compose 运行说明

## 1. 实现状态与依赖

当前服务为 migrate、api、worker、frontend；check/db-init 属于 tools profile。
应用从官方 Python 3.12 与 Node 24 镜像独立构建。
前端运行层固定本机已有官方 Nginx 1.31.8 的 digest；没有使用其他项目应用镜像。
SQLite 使用文件卷，不需要 MySQL/PostgreSQL/Redis 容器。

基础设施镜像复用仅指镜像层，不共享别的项目容器、账号、端口或数据卷。
其他机器需要准备相同镜像，或显式配置其已导入镜像的标签/digest。
官方应用基础镜像缺少时构建会获取，build.pull=false 不代表禁止获取缺少的基础镜像。
服务 pull_policy=never 防止误拉取同名应用镜像，应用需要先 build 或 docker load。

2026-11-23 已完成隔离数据库中的真实模型、新闻原文与 QQ SMTP 单次联调，用户确认收件及内容。
结果见[真实联调记录](development/NA-LIVE-20261123-001.md)；
容器健康、离线测试或 API 排队成功仍不能替代真实服务验收，公开部署与跨日定时运行尚未验收。
实际命令结果见[开发执行记录](development/NA-RUN-20261122-001.md)。

## 2. 配置与启动

```powershell
Copy-Item .env.example .env
# 编辑 JWT_SECRET（至少32随机字符）、ADMIN_EMAIL、ADMIN_PASSWORD（至少12字符）
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

访问 [本地应用](http://127.0.0.1:8080)，端口通过 APP_PORT 修改。
只监听宿主机 127.0.0.1，未公开部署；正式上线应配置域名、TLS 和受控入口。
Compose 不向宿主机发布 API/worker 端口。
不要把含真实 .env 的完整 docker compose config 输出贴到公开日志；使用 --quiet 校验。

migrate 使用有版本的 SQL 升级到 v2，成功后 api/worker 启动。
Bootstrap 仅在配置完整且账号不存在时创建用户与默认订阅，不覆盖已有密码。
管理员密码只注入迁移服务，不传入前端或模型。
没有模型/邮箱凭证也可登录和编辑订阅；生成请求明确拒绝未配置模型，邮件状态明确失败或未配置。
健康检查区分进程 /health 与数据库版本 /ready，不把外部 API 连通性当作进程存活条件。

## 3. 外部服务

LLM_PROVIDER=chat_completions，填写实际 LLM_BASE_URL（包含 /v1 等前缀）、LLM_MODEL、LLM_API_KEY。
必须支持 tools/tool_calls；系统不会自动选择付费模型或创建资源。
修改配置后用 docker compose up -d 重新创建服务，以便读取新环境变量。

RSS_SOURCES_JSON 是来源 ID 到 HTTPS URL 的映射，最多 10 个；
search_news 在这些源中按关键词过滤，不是全网搜索。
来源读取使用 DNS/IP 约束和内容大小限制，局域网 RSS 不在本 MVP 允许范围内。

QQ 邮箱使用 smtp.qq.com:465、TLS 和 SMTP 授权码；不能填 QQ 登录密码。
配置 SMTP_USERNAME、SMTP_AUTH_CODE 后，管理员需确认收件地址：
- 首次 bootstrap 时填写 VERIFIED_RECIPIENT_EMAIL；或
- 对已有账号执行：

```powershell
docker compose exec api python -m backend.app.cli verify-recipient --user-email <账号邮箱> --recipient <已核实的收件邮箱>
```

确认地址后刷新订阅页面，再开启每日推送。
手动生成允许关闭每日推送，此时只保存网页简报，不生成邮件投递。
同日已存在正式简报会复用；之后开启推送不自动补发这份历史简报。

accepted 只是 SMTP 接受，不表示送达或阅读。unknown 不自动重发；
页面要求先核查并明确确认可能重复发送、填写原因。
自动重试含首次最多 MAIL_MAX_ATTEMPTS 次；达到上限后手动重试是新的明确发送请求，
保留历史 attempt_count，不抹掉原记录。

## 4. 数据与权限

news-data 映射 /app/data，SQLite URL 为 sqlite:////app/data/news_assistant.db。
news-workspace 仅给 worker 使用，文件工具根目录为 /app/workspace/run_id。
容器 UID/GID 10001；根文件系统只读；临时文件使用 /tmp。
生产不挂载仓库和 Docker socket，.env、数据库、本地环境、截图及测试产物不进入构建上下文。
api/worker 共享受信任后端配置，但模型上下文不包含环境变量和 SMTP/模型密钥。
工具子进程显式清理环境，不继承这些凭证。

停止并保留卷：

```powershell
docker compose down
```

down -v 会删除数据库，不是普通停止方式。已有宿主机 data/*.db 不自动导入卷。
备份前停止所有写入，或通过 SQLite 在线备份 API 创建一致副本；
WAL 活跃时不能只复制主 .db 文件。恢复后执行 integrity_check、foreign_key_check 和版本检查。
项目名称变更会使用另一组独立卷，不能误判为原数据丢失。

## 5. 检查与迁移

```powershell
docker compose --profile tools build check
docker compose run --rm check
docker compose run --rm --no-deps migrate
```

check 不加载真实密钥，断网执行文档/初始 DDL、Ruff、格式、mypy 和 pytest；
测试中的模型、新闻与 SMTP 都是显式替身。
镜像 check-dependencies 按 uv.lock 安装开发依赖，运行镜像只带运行时依赖。
前端使用 npm ci，镜像构建执行 lint、Prettier、vue-tsc 和 Vite 生产构建。

db-init 是保留的诊断工具，仅创建原始 v1 数据库，目标已存在则拒绝覆盖；
正式运行使用 migrate，不要每次启动重复执行 db-init。
v1 → v2 迁移保留已有用户，新增 run_articles，并记录 schema_migrations。
已发布 001_initial.sql 保持不变；未来迁移按编号追加。

## 6. 本地可重复冒烟

有 uv、Node、宿主机 Chrome 时：

```powershell
uv sync --locked
cd frontend
npm ci
cd ..
uv run python scripts/browser_smoke.py
```

此脚本使用随机密码、动态本地端口及隔离数据库，启动真实 API 和 Vite，
检查登录、订阅、排队与移动布局。worker 不启动，不访问真实模型/SMTP。
截图和日志仅在被忽略的 artifacts、frontend/test-results 中。

先构建应用镜像后：

```powershell
uv run python scripts/compose_smoke.py
```

此脚本创建随机 Compose 测试项目与动态本地端口，验证健康、登录、保存订阅、
缺少模型时的明确错误、容器重建、删除容器后数据仍保留。
结束时核对项目标签，只删除本次临时容器、网络和测试卷，不碰正常应用卷。
这些步骤不代表公开上线或真实模型/QQ 邮件验收。

## 7. 上线与运维

服务器可按锁文件构建应用，或导入经过验证的 news-assistant:local 和 news-assistant-web:local；
基础设施镜像也需提前准备。导入标签和 NGINX_BASE_IMAGE 的 digest/标签须一致。
镜像与数据库备份分别迁移；为服务器重新配置密钥，不把本机 .env 加入 Git 或镜像。
先备份和执行迁移，再升级服务；保持 API/worker 镜像版本一致。

日志：docker compose logs --tail 100 api worker。不要在日志中记录原始正文、token 或授权码。
默认生成预算 180 秒，worker 停止宽限期 6 分钟；提高预算或 SMTP 超时时相应调整宽限期。
MVP 单 worker、SQLite 本地卷；不能把增加副本等同于已经具备高并发部署能力。

## 8. 官方参考

- [Compose profiles](https://docs.docker.com/compose/how-tos/profiles/)
- [Compose environment](https://docs.docker.com/compose/how-tos/environment-variables/set-environment-variables/)
- [Compose build](https://docs.docker.com/reference/compose-file/build/)
