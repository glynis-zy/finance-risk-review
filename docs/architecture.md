# 财务单据智能风险审核系统 — 架构设计

> 设计依据：`zy-项目实战.md` 第 2.7 节（2.7.1 ~ 2.7.15）。
> 本文档是系统实现的唯一权威蓝图，也是面试准备的主材料。

---

## 1. 系统定位

面向财务场景的智能风险审核系统：财务单据（对公付款、预付、批量付款、费用报销、差旅报销）创建后，系统对单据字段、明细和附件（发票/合同/行程单/付款依据）进行解析，通过**规则引擎**做确定性风险判断，输出风险项、证据和处理建议，并支撑审批流转与人工复核。

**核心设计原则（面试第一句话）**：

> 本系统把能力分层：**OCR 负责"看懂文档"，LLM 负责"理解非结构化文本"，规则引擎负责"确定性判断"，LLM 只做最后的"自然语言解释"。** 风险结论永远由规则引擎决定，LLM 不做最终判定。

这保证了风险结论可复现、可追溯、可审计——这在财务场景是不可妥协的。

---

## 2. 技术栈（已锁定）

| 层 | 选型 | 说明 |
|---|---|---|
| 后端 | Python **FastAPI** + SQLAlchemy ORM + Pydantic | 路由按模块一文件；Pydantic 模型≈表结构 |
| 数据库 | **MySQL 8**（Docker 运行） | SQLAlchemy 抽象，可切库 |
| 前端 | **原生 HTML/CSS/JS 单页应用**（FastAPI 托管静态） | 无构建步骤、无框架依赖；hash 路由 + schema 驱动动态表单 |
| 异步任务 | **进程内 asyncio 队列** + 前端轮询 | **仅限单进程 Demo**（`uvicorn --workers 1`）；任务状态落库，但队列不持久化，重启丢未完成任务；生产可平滑替换 Celery/RQ/消息队列 |
| LLM | DeepSeek（`deepseek-chat`），**适配层封装，厂商可换** | 用途：合同字段提取 + 对话意图解析 + 风险说明润色 |
| OCR | 百度云 OCR：**增值税发票识别**（专用）+ **通用文字识别** | 发票走专用模型，非固定版式文档走通用 |
| PDF 处理 | pypdf（文本直取）+ **PyMuPDF（扫描 PDF 渲染每页→逐页 OCR，带页码定位）** | 文本型与扫描型 PDF 都能解析 |
| 实时消息 | **前端轮询**（2~3s）任务状态接口 | 规格 2.7.12 的事件类型由轮询接口返回，不建 WebSocket |

**刻意不做**：Celery/Redis、本地模型部署、PDF 生成库。理由见 §15。

---

## 3. 核心流水线（四层分工）

```
用户上传(发票/合同/行程单/付款依据)
   │
   ▼
① 文档类型识别 + OCR 适配层
   ├─ PDF 先 pypdf 抽文本：有有效文本→直取；否则扫描PDF用 PyMuPDF 渲染每页→逐页OCR
   ├─ 增值税发票  → 专用发票OCR(百度云)      → InvoiceFields(Pydantic 校验)
   ├─ 合同        → 通用OCR全文 → LLM结构化提取 → ContractFields(Pydantic 校验)
   └─ 行程单/付款依据 → 通用OCR全文(可LLM提取) → 字段进 JSON
   │   ▲ 三种解析模式: real(真实API,失败即败) / auto(真实→失败回退预制) / preset(仅预制)
   ▼
② 结构化数据汇总
   单据字段 + 明细 + InvoiceFields + ContractFields + 供应商资料 + 历史单据
   ▼
③ 规则引擎（纯确定性，10 条规则）
   ──► 风险项 risk_findings: risk_code + 风险等级 + 实际值/参考值/阈值 + 数据来源 + 证据位置
   ▼
④ LLM 润色
   ──► 风险说明 + 处理建议（自然语言，结论已由规则引擎决定，LLM 只换表达）
```

映射到规格表：

| 流水线产物 | 落表 |
|---|---|
| 发票结构化字段 | `invoice_records` |
| OCR 全文 + 提取字段 + 证据位置 + 置信度 | `attachment_parse_results`（`fields_json`/`evidence_positions_json`/`confidence`） |
| 合同提取字段 | `attachment_parse_results.fields_json`（结构由 `ContractFields` Pydantic 定义） |
| 规则引擎输出 | `risk_findings`（`actual_value_json`/`reference_value_json`/`threshold_json`/`evidence_json`） |
| LLM 润色产物 | `review_reports.report_markdown` + 风险项 `suggestion_text` |

---

## 4. 总体架构

```
┌─────────────────────────────────────────────────────────────┐
│  原生 HTML/CSS/JS 单页前端（hash 路由 + 视图函数）            │
│  js/api.js 一个模块对应一个后端 router · 轮询任务状态        │
└───────────────┬───────────────────────┬──────────────────────┘
                │ REST (JWT Bearer)     │ 轮询: /analysis-tasks/{id}
                ▼                       ▼
┌─────────────────────────────────────────────────────────────┐
│  FastAPI 后端                                                  │
│                                                               │
│  routers/（薄层，只管 HTTP 与权限）                             │
│    auth · documents · attachments · sessions(对话) ·          │
│    analysis · approvals · workflows · rules · suppliers ·     │
│    reports · audit                                             │
│                                                               │
│  services/（UseCase 编排）                                       │
│    auth/document/workflow/analysis/attachment/dialogue/         │
│    parse_pipeline/report/supplier/sysparam/audit                │
│  repositories/（数据访问聚合）                                    │
│    Document / Attachment / Workflow / Analysis / User           │
│  domain/（业务规则）                                              │
│    document_state（状态机） · access_policy（L2+L3） ·           │
│    risk_engine（10 条规则 + build_context + 整体等级）            │
│  clients/（外部服务隔离）                                         │
│    ocr(base+baidu) · llm(base+deepseek)                          │
│                                                               │
│  core/        config · security · perms(RBAC) · scopes · deps  │
│  db/          session · base · (表结构见 §6)                    │
│                                                               │
│  异步分析流水线：asyncio 队列（单进程 Demo）→ 解析 → 分析 → 报告   │
└───────────────┬───────────────────────────────────────────────┘
                ▼
        MySQL 8（25+4 张表，见 §6）
```

---

## 5. 模块清单

### 5.1 后端模块（对应 2.7.9）

**分层结构**（Router 薄 / Service 编排 / Repository 管 SQL / Domain 管业务 / Client 隔离外部）：

```
routers/      HTTP + Depends（薄控制器）
services/     UseCase 编排（查单→权限→状态→完整性→新版本→审批→分析→审计→commit）
repositories/ 数据访问聚合（Document/Attachment/Workflow/Analysis/User，不一张表一个 repo）
domain/       状态机 document_state / 访问策略 access_policy / 风险引擎 risk_engine
clients/      OCR（base+baidu）/ LLM（base+deepseek），上层只依赖统一接口
models/       SQLAlchemy ORM
core/         config/security/perms/scopes/deps
```


| 模块 | 职责 | 核心服务 |
|---|---|---|
| 认证与权限 | 登录、当前用户、RBAC、数据权限、状态权限 | `auth_service`/`perms` |
| 会话（对话） | 审核会话、消息、槽位状态、多轮上下文 | `dialogue_service` |
| 单据 | 创建/编辑/复制/查询/提交/撤回/作废/版本/状态流转 | `document_service` |
| 明细 | 费用明细、付款明细、金额计算、合计校验 | `document_service` |
| 附件 | 上传/下载/删除/格式校验/访问控制/解析任务 | `attachment_service` |
| 审批流程 | 流程定义、条件匹配、实例、节点流转、任务 | `workflow_service` |
| 文档解析 | 类型识别、OCR/LLM、证据定位、三模式编排 | `parse_pipeline` + `clients/ocr·llm` |
| 规则引擎 | 10 条规则、阈值配置、金额容差/费用标准/市场价/异常 | `domain/risk_engine` |
| 智能分析 | 聚合数据、跑规则、生成 findings 与报告 | `analysis_service` |
| 供应商 | 档案、风险标签、黑名单、历史交易 | `supplier_service` |
| 数据访问 | 单据/附件/流程/分析/用户聚合查询 | `repositories/*` |
| 状态机 | 动作守卫 + 状态流转 + 状态日志 | `domain/document_state` |
| 访问策略 | L2 可见 / 归属 / L3 状态权限收口 | `domain/access_policy` |
| 报告 | 风险结果保存、面板数据、导出 | `report_service` |
| 审核 | 人工复核意见、风险项处理状态、最终审批结果 | 并入 `approval/analysis` |
| 日志 | 接口/附件/规则变更/分析任务/审批审计 | `audit_service` |
| 系统管理 | 用户/角色/权限 CRUD、系统参数、模型/OCR 模式配置（2.7.3） | `routers/admin.py` + `sysparam_service` |

### 5.2 前端页面（对应 2.7.4 / 2.7.8）

登录、单据管理、单据编辑（**schema 驱动动态表单**）、审核工作台、智能审核对话、单据详情、附件解析、风险分析、金额核对面板、供应商风险、审批流程配置、规则配置、审核记录。

---

## 6. 数据模型设计

### 6.1 规格已给出的表（25 张，2.7.10）

users / roles / permissions / user_roles / role_permissions
review_sessions / session_messages
financial_documents / document_versions / document_line_items
document_attachments / attachment_parse_results / invoice_records
approval_workflows / approval_workflow_nodes / approval_instances / approval_tasks / document_status_logs
analysis_tasks / risk_findings / review_reports
market_price_references / supplier_profiles / manual_reviews / audit_logs

（以上 25 张为规格原列出的表，逐条核对无误）

### 6.2 规格缺口修复（新增 4 张表）

| 新增 | 用途 |
|---|---|
| **`risk_rules`** | 规则配置表（2.7.10 缺规则表）。字段：`id, rule_code, rule_name, applies_to_json, enabled, config_json, updated_by, updated_at`。`config_json` 承载各规则阈值（金额容差%、市场价偏离阈值、异常次数、置信度阈值等）。对应 2.7.11 的 `/rules` CRUD 与"规则配置页"。 |
| **`expense_standards`** | 费用标准表（`expense_policy_compliance` 规则的数据源）。字段：`id, expense_category, department, position_level, region, standard_amount, currency, effective_date`。 |
| **`sys_params`** | 系统参数表（2.7.3：管理员维护系统参数）。风险升级阈值、OCR 模式开关等运行时可调，业务经 `sysparam_service` 读取，不硬编码。管理端 `/admin/sys-params` 维护。 |
| **`revoked_tokens`** | JWT 撤销表（2.7.14 撤销机制）。字段：`id, jti(唯一), expires_at, revoked_at`。登出/泄露时写入，**重启不丢**；内存 set 仅作快速路径缓存。 |

> **字段扩展（非表）**：`financial_documents.type_fields_json` —— 类型专属字段（合同编号、付款比例、出差地点、补贴金额…）落点。**元数据驱动**：每类单据一份字段定义（`document_schemas/<type>.py`），动态表单据此渲染、校验据此执行。新增单据类型=新增一份定义文件，平台零改动。
>
> **字段扩展（非表）**：`users.position_level`（职级，费用标准规则维度）、`document_line_items.specification`（规格，市场价规则维度）。

**表数量合计：规格 25 张 + 新增 4 张 = 29 张表**（`type_fields_json` 等为列，不占表数）。

### 6.3 单据模型设计（通用 + 元数据）

- **通用字段**：`financial_documents`（规格已列全）。
- **类型差异字段**：存 `type_fields_json`，结构由 `document_schemas/*.py` 声明（字段名、类型、必填、校验规则、展示顺序）。
- **明细**：`document_line_items.item_type` 区分 `expense`（费用明细）/ `payment`（付款明细）/ 差旅分类明细；同一张表承载 5 类单据。
- **版本**：提交/退回重提交时写 `document_versions.snapshot_json`，`current_version` 递增。

---

## 7. 单据状态机（D1，已确认）

```
draft ──提交──▶ pending_review ──首个审批任务被处理──▶ reviewing ──全部节点通过──▶ approved
pending_review/reviewing ──退回──▶ returned ──修改后重提交──▶ pending_review
                                          （新版本 + 新审批实例 + 新分析任务）
pending_review ──撤回──▶ withdrawn （审批实例 cancelled，待处理任务全部 cancelled）
draft / pending_review ──作废──▶ voided
任意审批任务驳回 ──▶ rejected
```

补充规则：
- **撤回**：仅允许当前节点任务尚未被处理时；`withdrawn` / `voided` / `rejected` 为终态。
- **作废（P0-2 终态守卫）**：作废审批中单据会**取消 running 审批实例 + pending 任务 + 未开始分析任务**；`approve/return/reject` 除校验任务 `pending` 外，还必须校验实例 `running` 且单据状态 ∈ `pending_review/reviewing`——终态单据不会被旧审批任务再次迁移。
- `reviewing` 是"实例运行中"期间的单据状态，避免与 `pending_review`（已提交待处理）混淆。

审批实例状态：`pending → running → approved/returned/rejected/cancelled`
审批任务状态：`pending → approved/returned/rejected/cancelled`
附件存储状态：`uploading → stored/failed`；解析状态：`pending → parsing → succeeded/failed/manual_review`

---

## 8. 整体风险等级公式（D2，已确认）

```
等级分值: low=1 / medium=2 / high=3
整体 = max(单项等级)
若存在任意 high 项        → 整体 = high
否则若 max=medium 且 medium≥3 → 整体 = high
否则若 max=low 且 low≥5      → 整体 = medium
否则                        → 整体 = max(单项等级)
```

- 确定性、可复现——同一份数据每次跑出相同结论（规则无随机、无 LLM 调用，可读 DB/配置）。
- 升级阈值（3 个 medium、5 个 low）存入 `risk_rules` 配置，可调。
- 面试口径："整体等级=最高单项等级+数量升级，全部由规则引擎输出，不引入模型主观判断。"

---

## 9. 三层权限模型（D6，已确认）

| 层 | 内容 | 实现 |
|---|---|---|
| **L1 RBAC 功能权限** | 角色→操作。申请人可创建/编辑/提交/撤回本人单据；审批人可处理任务/提交审批结果；财务可查看全部分析、维护规则与供应商；管理员可维护用户/角色/流程/系统参数。 | `permissions` 表 + 权限装饰器（`@require_perm("document:submit")`） |
| **L2 数据权限（行级）** | 申请人=本人单据；审批人=分配给他的任务及其单据与分析结果；财务=全部分析结果；管理员=全部。 | 查询层强制过滤（service 层统一注入 `data_scope`，非前端拼 SQL） |
| **L3 单据状态权限** | 操作合法性随状态变化：仅 `draft/returned` 可编辑；仅 `draft/returned` 可提交（returned 为退回后重提交，生成新版本+新审批实例+新分析任务）；仅 `pending_review` 可撤回；审批操作仅任务处于 `pending`。 | `document_service` 状态机内部校验 + `workflow_service` 任务状态校验 |

**关键点**：三层叠加，缺一不可。例如"申请人"即便角色有删除权限，也不能删除非本人单据（L2），且只能编辑 `draft/returned` 状态的单据（L3）。

---

## 10. 规则引擎（D3，10 条规则全实现）

规则引擎 = **注册表 + 配置**。每条规则一个**确定性函数**（无随机、无 LLM 调用，可查 DB/配置），签名统一：

```python
def check_rule(ctx: RuleContext) -> list[Finding]
# ctx: 单据字段 + 明细 + 发票 + 合同 + 供应商 + 历史 + rules 配置
# Finding: risk_code, level, actual/reference/threshold, evidence, suggestion
```

| # | rule_code | 规则 | 数据源 | 主要配置 |
|---|---|---|---|---|
| 1 | `invoice_amount_consistency` | 单据金额 vs 发票含税合计 | invoice_records | 容差 % |
| 2 | `line_items_total` | 明细合计 vs 总金额，漏项/重复项 | document_line_items | 容差 % |
| 3 | `contract_payment_consistency` | 合同主体/金额/付款条件/比例 vs 付款 | ContractFields | 容差 % |
| 4 | `batch_payment_consistency` | 付款笔数、笔金额合计 vs 批次总额、**重复收款账号** | line_items(payment) | 容差 % |
| 5 | `expense_policy_compliance` | 费用类别×部门×职级×地区 vs 费用标准 | expense_standards | 超支阈值 |
| 6 | `price_reasonableness` | 商品/服务×规格×地区×时间 vs 市场价区间 | market_price_references | 偏离 % |
| 7 | `spend_anomaly` | 短期高频/同日重复/节假日/异地/历史突增 | 单据历史 | 次数/倍数阈值 |
| 8 | `supplier_risk` | 黑名单/资质异常/关联交易/账号变更/集中付款 | supplier_profiles | 标签表 |
| 9 | `attachment_completeness` | 必需附件缺失 + **OCR 置信度低于阈值**（清晰度代理指标） | attachment_parse_results.confidence | 置信度阈值 |
| 10 | `duplicate_invoice` | 按发票代码/号码/金额/开票日期/销售方 + 文件 hash 识别重复提交 | invoice_records | 全库匹配 |

**确定性保证**：规则只读数据与配置，无随机、无模型调用；每条 finding 必须携带 `actual_value / reference_value / threshold / evidence / data_source`（对应规格 2.7.7 最后一条）。

> 费用标准的四维数据来源：类别=明细名称→科目（关键词）；部门=单据 `budget_department`；**职级=申请人 `users.position_level`**；地区=明细 `expense_location`。匹配策略：维度指定但不匹配的标准排除，命中维度越多越优先，全空标准作宽松兜底。
>
> 市场价的四维数据来源：名称=明细 `item_name`；**规格=明细 `specification`（`document_line_items` 新增列）**；地区=明细 `expense_location`；时间=明细 `expense_date` 对比参考 `effective_date`（取生效日期不晚于消费日的参考）。匹配顺序：名称（双向包含）→ 规格精确 → 地区 → 时间。

---

## 11. 解析流水线细节（AUTO / 预制 / 失败）

解析由系统参数 `ocr.mode` 决定三种模式（`parse_pipeline.parse_attachment` 编排）：

- **preset**：直接命中 `demo/preset_parse/*.json` 预置结果落表，**不调用任何外部 API**（演示确定性）；
- **auto**（默认）：先真实调用云 OCR / LLM，**失败后回退**命中预制案例，再失败才解析失败；
- **real**：只走真实 OCR/LLM，失败即失败，不查预制。

命中预制案例即**跳过 OCR 与 LLM**（直接写 `AttachmentParseResult` / `InvoiceRecord`）。

**PDF 双路径**（`_content_sources`）：文本型 PDF 先用 pypdf 抽文本（≥20 有效字符视为可用）；扫描型（图片 PDF）用 **PyMuPDF 渲染每页为 PNG → 逐页 OCR**，聚合全文并给每个词条标 **页码**（原文定位到页），发票则逐页调专用识别、取首个识别出关键字段的结果。OCR/LLM 失败统一走 auto 回退或 failed。

**职责边界（附件解析 vs 风险分析）**：附件解析只更新 `document_attachments.parse_status` 五态（`pending→parsing→succeeded/failed/manual_review`），**不创建 analysis_tasks**；解析失败置 `failed`，可再次调用该接口重试；`manual_review` 保留给"解析成功但需人工确认"场景。`analysis_tasks` 仅表示某单据的一次**整单风险分析**，其 `parsing_attachments` 阶段会顺带解析未成功的附件（复用本流水线）。

**分析任务状态（对齐规格 2.7.12 枚举）**：`analysis_tasks.task_status` = `queued → querying_document → loading_attachments → parsing_attachments → analyzing → succeeded / failed / cancelled`（cancelled 预留，当前无取消入口）。

LLM 提取：
- 合同等非固定版式：OCR 全文 → LLM 输出 **严格 JSON**（`ContractFields` Pydantic schema）→ 校验，失败重试一次 → 仍失败则 `manual_review`。
- **禁止 LLM 自由文本输出字段**（已确认）。LLM 只输出结构化 JSON，Pydantic 强校验。
- 证据定位：OCR 返回文本坐标 → 存 `evidence_positions_json`，前端可高亮原文位置。

---

## 12. 多轮对话模块（D8，已确认）

对话 = **LLM NLU → 本地规则槽位解析（fallback）→ 槽位状态机**（P1-1）：

1. 用户输入自由文本 → LLM 抽取 `{document_type?, document_no?, intent?}`（Pydantic 校验）；**LLM 不可用/解析失败 → `_local_parse` 本地规则**（五类中文名/简称 + `CP/AP/BP/EX/TR-YYYYMMDD-NNN` 编号正则）。
2. 槽位更新：用户显式纠正时覆盖旧槽位；已确认槽位不重复询问。
3. 槽位齐全 → 查单据时**同时校验 `document_type + document_no`**：两者不一致 → 澄清（A=采用单据实际类型，B=重新输入编号），**不偷偷忽略 document_type**；一致 → 校验数据权限 → 创建/推进分析任务。
4. 状态机：缺 `document_type` → 问类型；缺 `document_no` → 问编号；槽位存 `review_sessions`（对应 2.7.6）。

---

## 13. 报告与导出（D4，已确认）

- 报告源：`review_reports.report_markdown`（LLM 润色生成）。
- 面板数据：`risk_summary_json` / `amount_comparison_json` 供前端"风险分析页"和"金额核对面板"渲染。
- 导出：`GET /review-reports/{id}/export` 返回 **HTML**（内含 markdown 渲染），浏览器可打印成 PDF。**不引 PDF 生成库**。

报告结构（对应 2.7.13）：单据摘要 → 整体风险 → 金额核对 → 风险项列表 → 证据列表 → 供应商风险 → 处理建议（建议通过/补充材料/人工复核/建议驳回）→ 人工复核。

---

## 14. 演示数据（D5，已确认）

`demo/seed.py` 生成：
- 5 类单据各若干套（含合法/含风险两种，用于演示高/中/低风险）。
- 附件：发票/合同/行程单/付款依据（脚本生成或少量真实样例图）。
- **预制解析结果**：`demo/preset_parse/*.json` 与附件一一对应，供 preset 模式直接使用、auto 模式失败回退。
- **历史单据**：制造足够历史用于 `spend_anomaly`（历史突增）与供应商历史付款。
- 市场价参考、费用标准、供应商档案、用户/角色/流程初始数据。

---

## 15. 明确不做（D7，已确认）

| 不做 | 理由（面试备答） |
|---|---|
| 多币种换算 | 金额比较限定同币种；`currency` 字段保留，跨币种比较明确不做 |
| 附件清晰度独立模型 | 无可靠可测定义；用 **OCR confidence 作为质量代理指标**（规则 #9） |
| 批量付款高级规则 | 基础金额/笔数/重复收款账号检查全做（规则 #4），高级场景不做 |
| 全文检索 | 明细查询走结构化过滤，附件全文不建索引 |
| Celery/Redis/WebSocket | 为降低 Demo 复杂度用进程内 asyncio 队列 + 轮询（状态落库、队列不持久化、仅单进程）；生产可平滑替换 Celery/RQ/消息队列 |
| 前端框架/构建工具 | 原生 HTML/CSS/JS 单页 + FastAPI 托管静态，无 Vite/打包步骤 |

---

## 16. 面试要点（给自己背）

1. **一句话定位**：一个把"看懂/理解/判定/解释"四层拆开的财务单据风险审核系统，风险结论由确定性规则引擎产出。
2. **LLM 为什么不做风险判定**：财务结论必须可复现、可审计；LLM 判定无法保证同一输入同输出。
3. **为什么元数据驱动**：新增单据类型=新增一份字段定义文件，平台零改动；动态表单/校验/规则注册共用同一份元数据。
4. **状态机为什么这样设计**：`reviewing` 与 `pending_review` 语义分离避免时序歧义；撤回/作废/退回重提交都有明确转换与版本规则。
5. **三层权限为什么必要**：角色权限（能不能）+数据权限（哪些行）+状态权限（什么状态能做什么）三者叠加才是完整授权。
6. **解析三模式**：`ocr.mode` 分 `preset`/`auto`/`real`；演示用 auto，真实失败自动回退预制，链路永远可跑；生产可切 real。
