# 正式员工模型统一迁移报告

- 日期：2026-08-17
- 配置 revision：`24h-v3-gpt-5.6-sol-20260817T025041Z`
- 目标模型：`gpt-5.6-sol`
- 配置备份：`backups/config-migrations/24h-v3-gpt-5.6-sol-20260817T025041Z`（本地备份，不提交 Git）

## 迁移范围

以下正式员工从 Claude 系列模型统一迁移到 `gpt-5.6-sol`：

- 00003 COO：`claude-opus-5` → `gpt-5.6-sol`
- 00005 CSO：`claude-sonnet-5` → `gpt-5.6-sol`
- 00006 Senior Backend Engineer：`claude-opus-5` → `gpt-5.6-sol`
- 00007 Full-Stack Engineer：`claude-sonnet-5` → `gpt-5.6-sol`
- 00009 QA Lead：`claude-sonnet-5` → `gpt-5.6-sol`
- 00010 Tech Lead：`claude-fable-5` → `gpt-5.6-sol`

00001、00004、00008、00011、00012 原本已经使用 `gpt-5.6-sol`；00002 保持 `deepseek-v4-flash`。

## 同步修改

- 正式员工 `profile.yaml`；
- 11 份工作原则源文件及正式运行副本；
- 工作原则 revision manifest；
- 24 小时团队 README、团队配置、启动指南、验证清单、文档索引和快速参考；
- `check-system-ready.sh`、`verify-24h-mode.sh`、`check-p0-gate.py` 的目标模型基线；
- 实施计划和状态报告。

历史 iteration、历史对话、任务历史和既有验收报告保留原始模型名称，未做回写。

## 验证结果

- P0 Gate：`passed`；
- 正式员工配置 Gate：`passed`；
- 工作原则原子应用：`passed`；
- P0 内置定向回归：41 + 19 + 24 + 5 + 4 项测试通过；
- `iter_009` 只读保护哈希前后一致；
- 当前唯一未通过项：真实后端服务未运行，Service Gate 不能验证。

## iteration 处理

`iter_019` 使用旧 Claude 模型基线，禁止在同一 checkpoint thread 中混用新模型。下一次启动真实服务时应：

1. 设置 `OMC_RESTORE_PERSISTED_TASKS=false`；
2. 启动服务；
3. 通过正式 abort API 中止 `iter_019` 并保留原始证据；
4. 验证 `gpt-5.6-sol` Provider；
5. 创建全新 standard v2 iteration；
6. 重新完成四人任务、COO/EA 显式验收和 Closure Gate。
