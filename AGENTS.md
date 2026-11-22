# 项目开发约定
适用于整个仓库。修改前先读 README 和相关设计文档。

- 应用镜像从官方基础镜像独立构建，禁止继承其他项目应用镜像；数据库等基础设施优先用本机已有镜像。
- 运行基线：Docker Compose；现有 api/worker/frontend/migrate；check/db-init 使用 tools profile。
- 镜像构建排除 .env、数据库与 .venv；SQLite 使用命名卷，不挂载 Docker socket。
- 业务基线：Python + FastAPI + LangGraph；前端 Vue 3/TypeScript；数据库 SQLite MVP。
- 保持 LLM 动态选工具，不把搜索、读原文的顺序写死。
- 图节点、工具、提示词分别维护；模型调用必须支持 tool calling。
- API 负责认证和校验；Service 负责用例与事务；Repository 不自行 commit。
- 模型/网页/文件内容均不能越过工具权限边界；用户身份从可信运行上下文注入。
- QQ SMTP 授权码只能从环境变量读取；不得进入模型上下文、日志、数据库或提交。
- 邮件发送放在 Agent 外，由 outbox 投递记录驱动；结果不确定时禁止自动重发。
- 任意文件操作限定在本次 run 的工作目录；bash 不允许直接执行模型生成的任意 shell 字符串。
- 所有时间以 UTC 存储，用户时区参与业务日期与调度计算。
- 外部 I/O 设置超时，重试有上限；预算由程序强制执行。
- 文档、SQL、配置、实现必须同步；未实现功能不得描述成可用。
- Python 使用类型注解，Ruff 行宽 100；TS 开启 strict，不滥用 any。
- 检查入口：docker compose config --quiet、docker compose --profile tools build check、docker compose run --rm check。
- SQL 改动需验证外键、唯一约束和回滚；初始化通过 docker compose run --rm db-init。
- 后端 Ruff/mypy/pytest，前端 ESLint/Prettier/vue-tsc/Vite；保持 uv.lock 与 package-lock.json 一致。
- 离线模型/新闻/SMTP 替身只用于测试，不能在默认生产路径回退或宣称真实端到端通过。
- 提交只包含本任务文件，不覆盖用户已有变更；推送前检查 diff 和敏感信息。
