# 财务单据智能风险审核系统 — 函数级地图

> 作用：把每个接口、每个函数、每张表的职责固定下来，供逐函数背诵与面试应答。
> 对照阅读：`architecture.md`（原理）+ 本文（落点）。

---

## 1. 后端路由 → 处理函数总表

> 路由层（`routers/*.py`）只做三件事：收 HTTP 参数 → 调 service → 回 Response。业务逻辑一律在 `services/*.py`。

### 1.1 auth（认证）
| 接口 | 处理函数 | 调用 service | 职责 | 涉及表 |
|---|---|---|---|---|
| POST `/api/v1/auth/login` | `login()` | `auth_service.authenticate()` | 校验密码（hash）→ 签发 JWT → 记录令牌 | users |
| GET `/api/v1/auth/me` | `me()` | `auth_service.get_current_user()` | 当前用户 + 角色 + 权限集合 | users/user_roles/roles/role_permissions/permissions |
| POST `/api/v1/auth/logout` | `logout()` | `auth_service.revoke_token()` | 令牌加入撤销清单（有效期/撤销机制，对应 2.7.14） | audit_logs |

### 1.2 documents（单据）
| 接口 | 处理函数 | 调用 service | 职责 | 涉及表 |
|---|---|---|---|---|
| POST `/api/v1/documents` | `create_document()` | `document_service.create()` | 按类型 schema 校验字段 → 建单据（draft） | financial_documents |
| GET `/api/v1/documents` | `list_documents()` | `document_service.query()` | **数据权限过滤（L2）** + 按类型/申请人/部门/状态/日期筛 | financial_documents |
| GET `/api/v1/documents/{id}` | `get_document()` | `document_service.get_detail()` | 单据+明细+附件+版本+审批进度 | 多表联查 |
| PATCH `/api/v1/documents/{id}` | `update_document()` | `document_service.update()` | **仅 draft/returned 可改（L3）**；类型字段走 schema 校验 | financial_documents |
| POST `/api/v1/documents/{id}/copy` | `copy_document()` | `document_service.copy()` | 复制为**新 draft**（新编号） | financial_documents |
| POST `/api/v1/documents/{id}/submit` | `submit_document()` | `document_service.submit()` | 字段完整性校验 → **快照+新版本** → 建审批实例 → 建分析任务 | document_versions/approval_instances/analysis_tasks/status_logs |
| POST `/api/v1/documents/{id}/withdraw` | `withdraw_document()` | `document_service.withdraw()` | **仅 pending_review（L3）**；实例→cancelled，pending 任务→cancelled | document_status_logs/approval_instances/approval_tasks |
| POST `/api/v1/documents/{id}/void` | `void_document()` | `document_service.void()` | 仅 draft/pending_review 可作废；终态 | document_status_logs |
| POST `/api/v1/documents/{id}/line-items` | `add_line_item()` | `document_service.add_line_item()` | 按 item_type 校验 → 重算合计 | document_line_items |
| PATCH `/api/v1/documents/{id}/line-items/{lid}` | `update_line_item()` | `document_service.update_line_item()` | 同上 | document_line_items |
| DELETE `/api/v1/documents/{id}/line-items/{lid}` | `delete_line_item()` | `document_service.delete_line_item()` | 同上 | document_line_items |
| GET `/api/v1/documents/{id}/amount-comparison` | `amount_comparison()` | `analysis_service.compare_amounts()` | 单据/明细合计/发票合计/合同金额/付款金额对照，输出差异 | 多表聚合 |

### 1.3 attachments（附件）
| 接口 | 处理函数 | 调用 service | 职责 | 涉及表 |
|---|---|---|---|---|
| POST `/api/v1/documents/{id}/attachments` | `upload_attachment()` | `attachment_service.save()` | 类型/大小/路径校验 → 存文件 → 算 hash | document_attachments |
| GET `/api/v1/documents/{id}/attachments/{aid}` | `download_attachment()` | `attachment_service.stream()` | **鉴权后**流式返回文件 | document_attachments |
| DELETE `/api/v1/documents/{id}/attachments/{aid}` | `delete_attachment()` | `attachment_service.delete()` | 仅 draft/returned 可删 | document_attachments |
| POST `/api/v1/documents/{id}/attachments/{aid}/parse` | `create_parse_task()` | `parse_pipeline.enqueue()` | 入解析队列，状态→pending | analysis_tasks/attachment_parse_results |

### 1.4 review-sessions（多轮对话）
| 接口 | 处理函数 | 调用 service | 职责 | 涉及表 |
|---|---|---|---|---|
| POST `/api/v1/review-sessions` | `create_session()` | `dialogue_service.create_session()` | 建会话（空槽位） | review_sessions |
| POST `/api/v1/review-sessions/{sid}/messages` | `send_message()` | `dialogue_service.process_message()` | **LLM NLU 抽槽 → 状态机决策** → 返回澄清/分析任务信息 | session_messages/review_sessions |
| GET `/api/v1/review-sessions/{sid}/messages` | `list_messages()` | `dialogue_service.list_messages()` | 历史消息 | session_messages |

### 1.5 analysis（分析）
| 接口 | 处理函数 | 调用 service | 职责 | 涉及表 |
|---|---|---|---|---|
| POST `/api/v1/documents/{id}/analysis` | `create_analysis_task()` | `analysis_service.enqueue()` | 建分析任务（可来自对话或审批页"发起分析"） | analysis_tasks |
| GET `/api/v1/analysis-tasks/{tid}` | `get_task_status()` | `analysis_service.get_status()` | **轮询用**：状态 + 当前步骤 + 进度 | analysis_tasks |
| GET `/api/v1/analysis-tasks/{tid}/findings` | `get_findings()` | `analysis_service.get_findings()` | 风险项列表 | risk_findings |
| GET `/api/v1/analysis-tasks/{tid}/report` | `get_report()` | `analysis_service.get_report()` | 风险报告 + 面板数据 | review_reports/risk_findings |

### 1.6 approvals（审批）
| 接口 | 处理函数 | 调用 service | 职责 | 涉及表 |
|---|---|---|---|---|
| GET `/api/v1/approval-tasks` | `list_my_tasks()` | `workflow_service.list_my_tasks()` | **只返回分配给当前用户的任务（L2）** | approval_tasks/instances |
| POST `/api/v1/approval-tasks/{id}/approve` | `approve()` | `workflow_service.approve()` | **任务须 pending（L3）**；通过→下一节点或全部通过→单据 approved | approval_tasks/instances/document_status_logs |
| POST `/api/v1/approval-tasks/{id}/return` | `return_()` | `workflow_service.return_to_applicant()` | 退回→单据 returned，允许修改重提 | 同上 |
| POST `/api/v1/approval-tasks/{id}/reject` | `reject()` | `workflow_service.reject()` | 驳回→单据 rejected（终态） | 同上 |

### 1.7 workflows / rules / suppliers / reports / audit（配置与查询）
| 接口 | 处理函数 | 调用 service | 职责 | 涉及表 |
|---|---|---|---|---|
| GET/POST `/api/v1/approval-workflows`，PATCH `/{id}` | `list/create/update_workflow()` | `workflow_service.*` | 流程定义 CRUD（管理员） | approval_workflows/nodes |
| GET/POST `/api/v1/rules`，PATCH `/{id}` | `list/create/update_rule()` | `rule_engine.*_rule()` | 规则配置 CRUD（财务） | **risk_rules** |
| GET `/api/v1/suppliers/{code}/risks` | `get_supplier_risks()` | `supplier_service.get_risks()` | 供应商档案+标签+黑名单+异常 | supplier_profiles |
| PATCH `/api/v1/risk-findings/{fid}/review-status` | `update_finding_status()` | `analysis_service.update_finding_status()` | 人工确认/排除风险项 | risk_findings |
| POST `/api/v1/review-reports/{rid}/manual-reviews` | `submit_manual_review()` | `report_service.submit_manual_review()` | 复核意见+结论 | manual_reviews |
| GET `/api/v1/review-reports/{rid}/export` | `export_report()` | `report_service.export_html()` | markdown → **HTML** 导出 | review_reports |
| GET `/api/v1/audit-logs` | `list_audit_logs()` | `audit_service.list()` | 操作日志查询（审核记录页） | audit_logs |
| GET/POST `/api/v1/admin/users`，PATCH `/{id}` | `list/create/update_user()` | 用户 CRUD + 角色分配（`user:manage`） | users/user_roles |
| GET `/api/v1/admin/roles`，PATCH `/{id}/permissions` | `list_roles()`/`update_role_permissions()` | 角色权限维护（`role:manage`） | roles/role_permissions |
| GET `/api/v1/admin/permissions` | `list_permissions()` | 全部权限码 | permissions |
| GET `/api/v1/admin/sys-params`，PATCH `/{key}` | `list/update_sys_param()` | 系统参数（`system:manage`） | sys_params |
| GET `/api/v1/suppliers/lookup?name=` | `lookup_supplier()` | 按名称解析供应商编码（详情页入口） | supplier_profiles |

---

## 2. 服务层函数地图

### 2.1 document_service（单据）
`create / update / copy / submit / withdraw / void / query / get_detail / add_line_item / update_line_item / delete_line_item / _apply_type_schema(document, values) / _transition(doc, to_state) / _snapshot(doc) / _check_state_permission(doc, action)`

### 2.2 workflow_service（审批）
`match_workflow(document)`（按 document_type+金额区间+部门 匹配 `match_conditions_json`）
`create_instance(document)` → `next_node(instance)` → `create_task(node, approver_role)`
`approve(task)` → 有下节点则 next_node，否则实例 approved → 单据 approved
`return_to_applicant(task)` / `reject(task)` / `list_my_tasks(user)`

### 2.3 parse_pipeline（解析流水线）
`recognize_document_type(filename)` → `parse_attachment(att)` → `_ocr_invoice(path)` / `_ocr_generic(path)` / `_preset_lookup(att)` / `_extract_contract_fields(text)` / `_validate(fields, schema)` / `_evidence_positions(ocr_result)`

### 2.4 ocr_client（OCR 适配层）
`ocr_invoice(file)` → InvoiceFields
`ocr_generic(file)` → {full_text, positions, confidence}
`_preset_lookup(attachment)` → 命中 `demo/preset_parse/*.json` 则返回预置结果（fallback 第 2 级）
`_fail(att)` → 置 `parse_status=failed`（fallback 第 3 级）

### 2.5 llm_client（LLM 适配层）
`extract_contract_fields(full_text) -> ContractFields`（Pydantic 校验，失败重试 1 次）
`parse_dialogue_intent(text) -> SlotUpdate{type?, no?, intent?}`（Pydantic 校验）
`polish_risk_report(summary, findings) -> markdown`（只换表达，不改结论）

### 2.6 analysis_service（分析调度）
`enqueue(document_id)` → asyncio 队列 → `run_pipeline(task)`:
```
loading_document → loading_attachments → parsing_attachments
→ analyzing(rule_engine.run_all) → compute_overall_level(findings)   ← D2 公式
→ report_service.generate(markdown) → succeeded
```
`get_status/get_findings/get_report/compare_amounts/update_finding_status`

### 2.7 rule_engine（规则引擎）
`REGISTRY: dict[rule_code, fn]` + `run_all(ctx) -> list[Finding]` + `load_config(risk_rules)`。

10 个规则函数（同一签名 `check_rule(ctx)`）：
| rule_code | 函数名 | 判定要点 |
|---|---|---|
| invoice_amount_consistency | `check_invoice_amount()` | 单据申请金额 vs 发票含税合计，超容差→high/medium |
| line_items_total | `check_line_items_total()` | 明细合计 vs 总金额；漏项/重复项 |
| contract_payment_consistency | `check_contract_payment()` | 合同金额/付款条件/付款比例 vs 当前付款 |
| batch_payment_consistency | `check_batch_payment()` | 笔数、笔金额合计 vs 批次总额；**重复收款账号** |
| expense_policy_compliance | `check_expense_policy()` | 类别×部门×职级×地区 vs 费用标准，超支 |
| price_reasonableness | `check_price()` | 商品×规格×地区×时间 vs 市场价区间，偏离% |
| spend_anomaly | `check_spend_anomaly()` | 短期高频/同日重复/节假日/异地/历史突增 |
| supplier_risk | `check_supplier()` | 黑名单/资质异常/账号变更/集中付款 |
| attachment_completeness | `check_attachment()` | 必需附件缺失 + **OCR 置信度 < 阈值** |
| duplicate_invoice | `check_duplicate_invoice()` | 发票四要素 + 文件 hash 全库查重 |

### 2.8 dialogue_service（对话）
`create_session / process_message / list_messages / _nlu(session, text) / _next_question(session) / _start_analysis(session)`
状态机槽位：`document_type / document_no`（存 review_sessions）；输入优先级：LLM NLU 成功→用；失败→规则匹配纯槽位问答。

### 2.9 auth_service / perms（权限）
`authenticate / get_current_user / revoke_token`
`require_perm(code)`（L1 装饰器）→ `scope_query(model)`（L2 数据权限过滤）→ 状态机守卫（L3，在 document/workflow service 内）。

---

## 3. 前端页面 → 视图函数 → API

> 前端为**原生 HTML/CSS/JS 单页**（FastAPI 托管，无框架无构建）。`frontend/js/app.js` 内每个视图函数对应一个页面，`frontend/js/api.js` 一个模块对应一个后端 router；hash 路由 `#/dashboard`、`#/document/1` 等。

| 页面 | 路由 | 视图组件 | 核心子组件 | 调用 API |
|---|---|---|---|---|
| 登录页 | `/login` | LoginView | LoginForm | auth.login/me |
| 审核工作台 | `/` | DashboardView | StatCards/TaskList/RiskChart | analysis.summary, approval-tasks |
| 单据管理页 | `/documents` | DocumentListView | SearchBar/DocumentTable | documents.list/copy/withdraw/void |
| 单据编辑页 | `/documents/new`、`/documents/:id/edit` | DocumentEditView | **DynamicForm**（按 document_schemas 渲染）/LineItemsEditor/AttachmentUploader | documents.create/update, line-items, attachments |
| 智能审核对话页 | `/chat` | ChatView | MessageList/ChatInput/TaskProgress | sessions.*, analysis.task(轮询) |
| 单据详情页 | `/documents/:id` | DocumentDetailView | InfoTabs/ApprovalTimeline/VersionList | documents.get |
| 附件解析页 | `/documents/:id/attachments/:aid/parse` | AttachmentParseView | ImageViewer/ParseTextView/FieldTable/ConfidenceBadge | attachments.parse, attachments.get |
| 风险分析页 | `/documents/:id/analysis` | RiskAnalysisView | OverallRiskCard/RiskTable/EvidencePanel/SuggestionPanel | analysis.findings/report |
| 金额核对面板 | `/documents/:id/amount` | AmountPanelView | AmountCompareTable（差异高亮） | documents.amountComparison |
| 供应商风险页 | `/suppliers/:code` | SupplierRiskView | ProfileCard/HistoryTable/TagList | suppliers.risks |
| 审批流程配置页 | `/workflows` | WorkflowConfigView | FlowList/NodeEditor | workflows.* |
| 规则配置页 | `/rules` | RuleConfigView | RuleTable/ThresholdForm | rules.* |
| 审核记录页 | `/records` | RecordsView | ReportTable/ManualReviewLog/AuditTable | reports.*, audit-logs |

前端 api 层（`src/api/`）：auth / documents / lineItems / attachments / sessions / analysis / approvals / workflows / rules / suppliers / reports / audit，一个模块对应一个后端 router。

---

## 4. 状态机守卫函数（L3 权限与流转的核心）

| 动作 | 允许的当前状态 | 迁移后状态 |
|---|---|---|
| 编辑 update | draft, returned | 不变 |
| 提交 submit | draft | pending_review |
| 撤回 withdraw | pending_review | withdrawn |
| 作废 void | draft, pending_review | voided |
| 退回 return | pending_review, reviewing | returned |
| 驳回 reject | pending_review, reviewing | rejected |
| 重提交 resubmit | returned | pending_review |
| 审批通过 approve（末节点） | reviewing | approved |

守卫统一在 `_transition()` 内：**状态不合法 → 抛 409 + 可读错误**，前端据此禁用按钮。

---

## 5. 背诵顺序建议

1. 先背 §1 路由表（接口名 ↔ 职责），对着 Swagger 过一遍。
2. 再背 §2 服务函数清单（一个 service 一句话：它管什么状态）。
3. 然后背 §3 规则引擎 10 个规则函数（面试被问"举一条规则"用）。
4. 最后背 §4 状态机守卫（面试被问"撤回时会发生什么"用）。
