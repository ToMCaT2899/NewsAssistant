# ADR 0001：显式 ReAct 图与事务 outbox
日期：2026-11-22
状态：设计阶段采纳，待业务实现验证。

## 背景
题目要求工具顺序由 LLM 决定；系统还要定时、保存历史和通过 QQ 邮箱推送。
自由模型不能承担权限、数据库一致性或外部邮件的可靠性保证。

## 决策
使用 LangGraph 显式 graph：预算检查、agent、tools、validate。
LLM 动态返回 tool_calls，工具层执行真实能力；不解析模型隐藏推理。
定时器、鉴权、预算、校验、事务和发送为确定性代码。
正式简报和待投递 outbox 同事务；SMTP 在事务外执行。
将 SMTP accepted、failed、unknown 分开，未知结果停止自动重发。

## 备选
- 手写循环：依赖少，但后续状态、图与日志需要自行组织；仍是有效替代。
- LangChain create_agent：接入快，底层基于 LangGraph；本项目为便于学习与演示选择显式图。
- 固定搜索流水线：易实现，但不满足自主工具调用要求。
- 把发邮件暴露为自由工具：会增加重复发送与权限控制复杂度，MVP 不采用。

## 代价
需要实现自己的业务预算和输出校验，不能只依赖 recursion_limit。
outbox 和唯一键防并发重复，但普通 SMTP 无法提供严格 exactly-once。
单机 SQLite 适合 MVP，不承诺大规模并发；迁移 PostgreSQL 时重新验证锁与任务领取。
SQL 初始化与未来 ORM/迁移工具需保持一致，已发布 DDL 不原地修改。

## 验证
工具 observation 改变后模型能改变下一步；
预算批次不能超额；发布失败不残留待发送记录；
SMTP 接受后崩溃进入 unknown，重启不自动重发。
