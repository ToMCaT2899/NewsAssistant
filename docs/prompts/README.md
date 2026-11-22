# 开发提示词归档

本目录保留用于开发每日 AI 新闻助手的提示词。它们是可审阅的项目资料，
不会因文件存在或被引用就自动执行。归档时用户要求只保存，之后已明确授权实施。
本次执行见[开发记录](../development/NA-RUN-20261122-001.md)；正文与 manifest 的归档状态保持为历史快照。

## 当前版本

- 提示词编号：NA-DEV-001，版本 v1.0.0，日期 2026-11-22。
- [完整开发提示词](daily-ai-news-agent.v1.md)
- [文字需求基线](../requirements/2026-11-22-需求基线.md)
- [内容指纹与来源清单](manifest.json)
- [版本记录](CHANGELOG.md)
- 归档时状态：archived_not_executed（历史快照；实际执行状态见开发记录）。
- 参考设计提交：e54c9d1a125a1af84af0651e399ebf1bffed85ad。

原始截图不提交 Git。需求摘要只记录内容与来源类别，不链接本机临时文件。
本目录的开发提示词与未来 backend/app/agent/prompts 中的运行时系统提示词不同；
开发提示词指导编码，运行时提示词指导新闻 Agent，不得把开发提示词当作运行时输入。

## 后续如何启动

在明确准备开发时，可以向编码助手发送：

> 请读取 docs/prompts/daily-ai-news-agent.v1.md，并按其中的阶段从当前仓库状态开始实现。
> 使用该版本提示词，记录需求映射、实际检查结果与执行起止提交。
> 不要把缺少真实凭证的离线验证描述成线上端到端通过。

也可只要求执行某个阶段；以用户当次明确给出的范围为准。
读取提示词、检查仓库和撰写计划不等于自动获得部署、群发邮件或改其他项目的授权。

## 版本与指纹

v1 归档后，改变需求、架构边界或验收语义时新增 v2 文件；
CHANGELOG 记录版本、日期、原因和影响。保留旧版，不用改写旧版伪造开发历史。
排版修正也应更新版本记录及 manifest 中的 SHA-256；Git 是变化审计的来源。
manifest 记录提示词和需求基线的字节内容 SHA-256，以及设计基线提交；
它用于内容一致性检查，不是数字签名，也不意味着实现已经通过验收。

可在仓库根目录使用 PowerShell 检查提示词指纹：

```powershell
$manifest = Get-Content docs/prompts/manifest.json -Raw | ConvertFrom-Json
foreach ($artifact in $manifest.artifacts) {
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $artifact.path).Hash.ToLowerInvariant()
    if ($actual -ne $artifact.sha256) { throw "Artifact hash mismatch: $($artifact.path)" }
}
```

## 后续执行记录约定

明确启动开发后，在 docs/development/ 下新建独立执行记录；当前不创建虚假的开发日志。
每次记录至少包含：
- 执行编号、日期、提示词 ID/版本/SHA-256、实际工作分支。
- 开始与结束提交，范围和对应 R/D/A 编号。
- 变更文件、实现进度、真实命令与退出结果。
- 离线替身、真实外部服务验证、尚缺凭证及未完成项。
- 用户新增决策和对应 ADR，不记录密钥、密码或授权码。

实施提交正文可使用 Prompt-ID: NA-DEV-001@v1.0.0、Requirements: R01/R03、
Acceptance: A03 等字段。实施结束后用下一次文档提交补记实际结束提交，
不在提交里预填自己的未来哈希，也不把本轮归档提交当作开发开始。
