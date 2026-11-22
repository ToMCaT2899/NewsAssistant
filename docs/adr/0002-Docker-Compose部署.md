# ADR 0002：使用 Docker Compose 统一运行方式
日期：2026-11-22
状态：采纳；当前落地检查与数据库初始化，业务服务在后续阶段实现。

## 背景
宿主机分别安装 Python、Node 和依赖容易产生环境差异。用户要求采用 Docker Compose。

## 决策
以 compose.yaml 为主要运行入口，Dockerfile 从官方 Python 基础镜像独立构建 base 阶段，不继承其他项目应用镜像。
check 是默认一次性任务；db-init 属于 tools profile，显式 run 执行。
代码复制到镜像；当前检查只用标准库，Ruff 使用本机已有环境；构建排除 .env、数据与宿主机 .venv。
应用镜像为 news-assistant:local，基础镜像为 python:3.12-slim-bookworm，优先使用本地缓存。
复用本机已有镜像仅适用于数据库、缓存等基础设施；本阶段 SQLite 不需要数据库容器。
应用 Compose 服务使用本地构建镜像；pull_policy: never 防止错误拉取同名应用镜像，
build.pull: false 不强制刷新基础镜像，但缺少官方基础镜像时构建仍会获取。
SQLite 使用命名卷，未来 api/worker 共享；工作区仅挂载给 worker。
非 root 运行，检查和初始化根文件系统只读，临时文件写入 tmpfs。

## 渐进落地
业务不存在时不创建虚假的 api、worker、frontend 服务或空占位启动命令。
补齐业务后再扩展同一 Compose 文件为 api + worker + frontend + migrate。
当前初始化器只接受新库；不把每次启动重复建库当作迁移方案。

## 代价与验证
需要 Docker Engine；官方应用基础镜像缺少时构建需要网络或事先 docker load 导入。
未来基础设施默认选本机已有 mysql:8.4、postgres:15-alpine、redis:7.4-alpine 等匹配版本，
只按实际需求启用一种数据库及所需服务，不因镜像已存在就启动额外组件。
不得把另一个项目的 backend/api 镜像当作 Python 基础镜像。
命名卷不自动读取以前宿主机 data 目录。
构建镜像、运行 check、运行 db-init、重复初始化拒绝覆盖、移除容器后数据仍在。
镜像标签需要定期更新；未来正式发布锁定 digest，并执行完整应用健康与故障恢复检查。
