# 开发记录（DEVLOG）

> 规则：每次改代码/文档都追加一条，最新在上。格式：`YYYY-MM-DD HH:MM - 改动摘要 + 涉及文件`。
> 用于：自我回溯 + 面试讲"我每一步怎么做的"。

---

## 2026-08-16

### 08:40 - 立项与决策收敛（访谈阶段）
- 范围：仅 2.7（财务单据智能风险审核系统）；全做 5 类单据 + 元数据驱动。
- 技术栈：FastAPI + SQLAlchemy + MySQL(Docker) + Vue→改为**原生 HTML/CSS/JS**。
- API：DeepSeek(LLM) + 百度云 OCR；LLM 只做理解与润色，风险判定归规则引擎。
- 决策编号 D1~D8 全部锁定（见 architecture.md）。

### 09:10 - 架构文档 & 函数级地图
- 新增 `docs/architecture.md`：四层流水线、表映射、状态机、风险公式、三层权限、10 条规则注册表、不做清单。
- 新增 `docs/function-map.md`：路由→函数→职责→表 全量对照 + 前端页面→API。

### 09:20 - 项目骨架 & 数据模型
- 目录结构：`backend(FastAPI) / frontend / docs / demo / data`。
- `backend/app/core/*`：config（厂商可换）、security（JWT+撤销）、perms（L1 RBAC）、scopes（L2 数据权限）、deps（依赖注入）。
- `backend/app/models/*`：27 张表 SQLAlchemy 模型。
- `backend/app/main.py`：入口，挂载 11 个路由，启动建表。
- `backend/scripts/init_db.py`、`.env.example`、`requirements.txt`。
- **修复**：SQLAlchemy 混入列复用问题（`created_at = TimestampMixin.created_at` → 改继承 `TimestampMixin`）。

### 09:40 - 认证模块（真实可用）
- `routers/auth.py` + `services/auth_service.py`：login（bcrypt 校验 + JWT）、me（含角色/权限码）、logout（令牌撤销）。
- 首次导入验证通过：16 routes / 10 rules。

### 10:00 - 单据模块（系统心脏）
- `document_schemas/__init__.py`：5 类单据类型字段元数据 + 校验。
- `services/document_service.py`：CRUD/复制/提交/撤回/作废/明细/状态机守卫（L3）/版本快照/提交时建审批实例+分析任务。
- `services/workflow_service.py`：流程匹配、实例/节点/任务创建、approve/return/reject、`pending_review→reviewing` 转换。
- `services/analysis_service.py`：任务创建、金额核对（单据/明细/发票/合同/付款对照）。
- `routers/documents.py`（全）、`routers/approvals.py`（全）。

### 10:20 - 解析流水线
- `schemas/llm.py`：ContractFields / SlotUpdate（Pydantic 强校验，LLM 禁止自由文本）。
- `services/llm_client.py`：DeepSeek 适配层（合同字段提取、对话 NLU、报告润色；失败降级）。
- `services/ocr_client.py`：百度云 OCR 适配层（发票专用 + 通用；当时为"预制优先"实现，后重构为三模式，见 14:00 修正）。
- `services/parse_pipeline.py`：文档类型识别、文本 PDF 直取（pypdf）、发票/合同/通用三类解析落表。
- `requirements.txt` 追加 pypdf。

### 10:40 - 规则引擎
- `services/rule_engine.py`：10 条规则注册表 + `build_context`（汇总数据/供应商/历史/配置/标准）+ `compute_overall_level`（D2 公式）+ risk_rules 配置加载。
- 修掉 `check_invoice_amount` 一处 `if False else` 残留。

### 10:50 - 附件服务
- `services/attachment_service.py`：上传（类型/大小/路径校验 + sha256）、下载（L2 鉴权）、删除（L3）。
- `document_service` 新增公开守卫 `ensure_editable`（供附件/明细复用）。
- 提交流程改为 commit 后 `analysis_service.enqueue(task.id)` 入队跑分析。

### 11:10 - 报告服务 & 分析流水线（异步）
- `services/report_service.py`：Markdown 报告生成（6 段结构，对应 2.7.13）、LLM 润色（失败用模板）、HTML 导出、人工复核。
- `services/analysis_service.py`：后台线程事件循环 + `run_pipeline`（querying→loading→parsing→analyzing→succeeded），失败回滚置 failed；`get_findings/get_report`（带 L2）。
- `routers/attachments.py`（上传/下载/删除/触发解析）、`routers/analysis.py`（状态轮询/风险项/报告）、`documents.py` 增 `POST /documents/{id}/analysis`。
- `document_service` 新增 `ensure_viewable`。

### 11:30 - 对话/规则/流程/供应商/报告/审计路由
- `services/dialogue_service.py`：LLM NLU + 槽位状态机（缺类型问类型→缺编号问编号→查单→发起分析）。
- `services/supplier_service.py`：供应商档案/标签/黑名单/历史付款。
- `routers/sessions.py`（对话）、`routers/rules.py`（risk_rules CRUD）、`routers/workflows.py`（流程+节点 CRUD）、`routers/suppliers.py`、`routers/reports.py`（列表/人工复核/导出）、`routers/audit.py`、`routers/riskfindings.py`（复核状态）。
- `main.py` 挂载 12 个路由；导入验证通过（17 routes / 10 rules）。
- **注意**：Docker 守护进程未启动，验证暂用 SQLite 冒烟（`DATABASE_URL` 可切）。

### 11:40 - 解析流水线增强：全量预制通道
- `parse_pipeline` 增加"全量预制"优先级：命中 `demo/preset_parse/<附件名>.json`（含 category+fields）→ 直接落表，跳过 OCR 与 LLM。
- 目的：演示链路完全确定性、不依赖外部 API 稳定性（当时语义"预制优先"，后统一为三模式，见 14:00 修正）。

### 12:00 - 演示数据 seed.py + 首次全链路验证
- `scripts/seed.py`：清空重建 → 角色/权限/5 用户 → 5 类审批流程 → 10 条规则 → 参考数据（费用标准/市场价/3 供应商）→ 5 类示例单据+明细+附件+预制解析 → 全部提交并触发分析。
- 修复链：bcrypt 版本冲突（固定 4.0.1）→ `type_fields_json` 字段名 → 发票日期字符串转 date（SQLite 严格）→ 占位 PNG 字节碰撞导致 file_hash 相同（改手写 PNG + tEXt 块保证唯一）→ 规则误报修正（附件完整性 `fields_json is not None`、同日重复按"同名同日"判定、重复明细排除 payment）。
- 验证结果（5 档风险齐全）：CP=low/通过，AP=high/人工复核，BP=high/人工复核，EX=medium/补充材料，TR=medium/补充材料。

### 12:20 - 前端（原生 HTML/CSS/JS 单页，FastAPI 托管）
- `frontend/index.html` + `css/style.css`（简约商务：深蓝主色、卡片、表格、徽标）+ `js/api.js`（fetch 封装+JWT）+ `js/app.js`（hash 路由 + 视图）。
- 视图：登录 / 工作台 / 单据列表 / 新建编辑（schema 驱动动态表单）/ 单据详情（基本信息/金额核对/风险分析/附件解析/审批进度 5 个 tab）/ 智能对话 / 审批待办 / 规则配置 / 流程配置 / 审核记录 / 供应商。
- `main.py` 挂载 `/static` 静态目录 + `GET /` 返回 index.html（无构建步骤）。

### 12:40 - 端到端冒烟测试
- `scripts/smoke_test.py`（TestClient）：22 项全过。覆盖前端托管、登录 RBAC、数据权限过滤（张三只见本人 3 单）、金额核对、分析报告（CP=low/AP=high）、审批通过推进下一节点、HTML 导出、附件下载、供应商/规则/审计、越权拦截(403)。

### 12:50 - 收尾
- `main.py` 静态托管 `/static` + `GET /` 返回前端（无构建）。
- JWT 默认 secret 加长（消除 InsecureKeyLength 告警）。
- 删除调试脚本 `debug_hash.py`；新增 `README.md`（快速启动/演示链路/冒烟测试/接真 API）。
- `architecture.md` §2/§15、`function-map.md` §3 同步"原生前端"事实。
- 全量 `compileall` 通过；19 条路由 / 10 条规则 / 12 个路由模块。

### 状态
- 任务 #1~#8 全部完成。功能可跑、文档可背、演示链路确定性可复现（无 API key 也能全链路演示）。

### 13:10 - 管理端 + 设计深化（用户反馈"太简单"）
- **SysParam 系统参数表**（`risk.medium_bump_count`/`low_bump_count`/`attachment.max_size_mb`/`confidence_threshold`/`ocr.mode`），运行时修改、带审计，`compute_overall_level` 与 `ocr.mode` 已真正接入。
- **`routers/admin.py`**：用户 CRUD+角色分配、角色权限维护、权限列表、系统参数；权限码 `user:manage`/`role:manage`/`system:manage`。
- **前端**：新增"系统管理"页（用户/角色权限/系统参数三 tab，菜单按权限显示）；流程配置改为可新建/编辑；风险 tab 加人工复核表单；对公/预付单详情加"查看供应商风险"入口（新增 `GET /suppliers/lookup?name=`）。
- **`tests/`** pytest 14 条：`compute_overall_level` 公式 + 规则引擎代表性分支（发票/供应商黑名单/批量重复账号/费用超标/附件缺失）+ sysparam 默认值。
- **`docs/DESIGN.md`**：设计原则表（SRP/OCP/DIP/DI/DTO/四层/纵深/可测）+ 设计模式→代码位置→面试话术 + 管理端说明 + 面试问答预演。
- 冒烟测试扩到 **27 项**（含管理端 5 项）全过；pytest 14/14 通过。

### 14:00 - 解析三模式重构 + 文档一致性（用户纠正）
- `ocr.mode` 明确为**三模式**：`preset`（仅预制，不调外部 API）／ `auto`（真实→失败回退预制）／ `real`（真实，失败即败）。默认 `auto`。
- `ocr_client` 改为**纯真实 API**（移除内部 `_preset_for`）；预制命中逻辑全部上移到 `parse_pipeline`，按模式编排（`parse_attachment` 统一入口）。
- **文档一致性**：
  - 前端技术栈统一为原生 HTML/CSS/JS：`architecture.md` 架构图改掉 Vue3/Element Plus；`function-map.md` 的 `src/api/` 残留改为 `frontend/js/api.js`；删除 `frontend/src/*` 空目录（api/views/components/router/stores）。
  - 解析语义统一为三模式：`architecture.md` §3/§11/§16、`README.md`、`DESIGN.md`、`function-map.md` §2.3/§2.4。
- 回归：27/27 冒烟 + 14/14 单测通过（`auto` 模式无 key 时真实失败→自动回退预制，链路依旧确定）。

### 14:20 - 修复"退回后重提交"状态机漏洞（用户抓出）
- 漏洞：`GUARD["submit"]={draft}` 把 returned 挡在门外，与架构"returned→修改→重提交→pending_review（新版本+新实例+新分析任务）"矛盾；函数地图还虚构了不存在的 `resubmit()`。
- 修复：`submit` 同时承担首次提交与退回后重提交，`GUARD["submit"]={draft, returned}`；returned 提交时 remark 记"退回后重新提交"，照走 `_snapshot`(新版本) + `start_approval`(新实例) + `create_task`(新分析)。
- 前端：单据列表"提交"按钮改为 `draft`/`returned` 都显示。
- 文档：architecture §9 L3 改"仅 draft/returned 可提交"；function-map §1.2/§4 合并为"提交/重提交 submit"，删除 resubmit 行。
- 新增 `tests/test_state_machine.py`：2 条（重提交=新版本+新实例+新任务；draft 首次提交仍正常）。pytest 16/16、冒烟 27/27。

### 14:30 - 修正表数量（用户抓出）
- 2.7.10 规格原列 **25 张**表（此前文档误写"21 张/实际 26 张"）；新增 **3 张**（risk_rules / expense_standards / sys_params）；`type_fields_json` 是 `financial_documents` 的 **JSON 列，不是表**。
- 合计：**25 + 3 = 28 张表**。已用 `Base.metadata.tables` 实测核对：正好 28 张。
- architecture.md §6.1/§6.2 重写：规格表标注 25 张；新增区明确"3 张表 + 1 个字段扩展"；末尾加合计说明。
- DEVLOG 早期"27 张 ORM 模型"为当时正确记录（sys_params 后加），按历史保留。

### 14:40 - 附件解析与风险分析任务解耦（用户抓出）
- 问题：函数地图把 `POST /attachments/{aid}/parse` 写成 `parse_pipeline.enqueue()` → `analysis_tasks`，但 `analysis_tasks` 无 `attachment_id/task_type`，附件 A/B 各自重试无法定位；且 parse_status 只有 pending/succeeded/manual_review，缺 parsing/failed。
- 定案（用户推荐）：
  - **附件解析**：不建 analysis_tasks，只更新 `document_attachments.parse_status` 五态（pending→parsing→succeeded/failed/manual_review）；失败置 `failed`，重试=再次调用该接口。
  - **analysis_tasks**：只承载某单据的一次整单风险分析；其 `parsing_attachments` 阶段顺带解析未成功附件（复用 parse_pipeline）。
- 代码：`parse_attachment` 开头置 `parsing`；`_fail` 置 `failed`（manual_review 保留给"成功但需人工确认"）。
- 文档：function-map §1.3/§2.3/§2.6、architecture §11 统一为"职责边界"表述。
- 回归：pytest 16/16、冒烟 27/27。

### 14:50 - 补齐扫描 PDF OCR 链路（用户抓出，功能缺口）
- 缺口：原始只做"PDF→pypdf 文本提取"，扫描型（图片 PDF）无文字层拿不到内容，且把整份 PDF 字节直接塞给图片 OCR 接口，链路断裂；需求要求 PDF/PNG/JPG 都能 OCR + 原文定位。
- 修复（`parse_pipeline`）：
  - `_pdf_to_images(path)`：PyMuPDF 渲染每页为 PNG（`import pymupdf`，兼容旧 `fitz`）；
  - `_content_sources(path, is_pdf, bytes)` 分流：文本型 PDF 有效文本（≥20 字符）直取；否则渲染页图逐页 OCR；
  - `_parse_real_*` 逐页 OCR 聚合：合同/通用全文按页拼接 `[第N页]`，词条位置带 `page` 页码（原文定位到页）；发票逐页调专用识别、取首个识别出关键字段的结果。
- `ocr_client.ocr_invoice/ocr_generic` 移除无用 `file_name` 参数（纯真实 API，只收图片字节）。
- 新增 `tests/test_pdf_ocr.py`：4 条（文本 PDF 直取 / 扫描 PDF 无文字层 / 扫描 PDF 渲染出合法 PNG）。pytest 20/20、冒烟 27/27。
- 文档：architecture §3 图 + §2 技术栈表 + §11 新增"PDF 双路径"；function-map §2.3；README 技术栈表。

### 15:00 - 费用标准规则补齐职级维度（用户抓出）
- 缺口：规则要求"类别×部门×职级×地区"，但 `users` 无职级字段，`financial_documents` 也不带，规则引擎拿不到"职级"。
- 修复：按用户建议的简单方案，**`users` 新增 `position_level`**（staff/manager，seed 已给 5 个演示用户赋值）。
- `check_expense_policy` 升级为**四维最精确匹配**：`_match_standard`（部门=预算部门、职级=申请人、地区=明细地点；维度指定不匹配则排除该标准，命中维度越多越优先，全空标准宽松兜底），finding 的 reference 里带命中维度。
- seed 增加四维费用标准（销售部 staff/manager × 上海 × 住宿等）。
- 新增 2 条单测（staff 超 staff 标准命中且匹配到带职级标准；manager 未超 manager 标准不命中）。pytest 22/22、冒烟 27/27。
- 文档：architecture §10 补四维数据来源说明。

### 15:10 - 市场价规则补齐规格维度（用户抓出）
- 缺口：规则要求"名称×规格×地区×时间"，但 `document_line_items` 无 `specification`，规格匹配只是假装存在。
- 修复：`document_line_items` 新增 `specification` 列；schema/`_line_items_out`/前端明细编辑器（新增"规格"输入列与展示列）同步。
- `check_price` 升级为四维匹配：名称（双向包含）→ 规格（明细填了规格则优先精确匹配带相同规格的参考）→ 地区 → 时间（取 `effective_date` 不晚于消费日的参考，否则取最早）。finding 的 reference/evidence 带规格、地区、来源、生效日期。
- seed：明细补规格（商务酒店 豪华大床房/标准间、机票 经济舱），新增"标准间 [300,600]"规格参考；演示档位不变。
- 新增 2 条单测（标准间 500 命中规格档不误用豪华档不报险；豪华大床房 1500 超豪华档且 evidence 明确规格档）。pytest 24/24、冒烟 27/27。
- 文档：architecture §10 补市场价四维数据来源。

### 15:20 - JWT 撤销机制持久化（用户抓出）
- 问题：撤销只是内存 `set`，服务重启即丢，与文档"访问令牌撤销机制"（2.7.14）不符；函数地图"涉及表"只写了 audit_logs，兜不住黑名单。
- 修复（选用户给的 revoked_tokens 表方案）：
  - 新增 **`revoked_tokens` 表**（`jti` 唯一、`expires_at`、`revoked_at`），登出/泄露时写入，重启不丢；内存 `_revoked_cache` set 仅作快速路径缓存。
  - `security`：`revoke_token(db, token)` 写表、`is_token_revoked(db, jti)` 缓存→DB、`purge_revoked(db)` 顺带清过期；`decode_access_token` 只验签名/有效期。
  - `deps.get_current_user` 校验撤销（401"令牌已撤销"）；`logout` 写表 + 审计。
- 表数：25 规格 + 4 新增（risk_rules/expense_standards/sys_params/**revoked_tokens**）= **29 张**，实测 `Base.metadata.tables`=29。
- 新增 `tests/test_security.py`（撤销写表可查/幂等/过期拒绝）；冒烟加"登出后旧令牌 401、其他令牌不受影响"（证明是逐令牌撤销）。pytest 27/27、冒烟 29/29。
- 文档：architecture §6.2 加 revoked_tokens、表数 29、字段扩展补 position_level/specification；function-map §1.1/§2.9 改持久化表述。

### 15:30 - 异步边界 / 状态名 / 模式标签收敛（用户三条意见）
1. **异步只限单进程 Demo**：进程内 asyncio 队列多 worker 各一份、重启丢未完成任务。明确 `uvicorn --workers 1`；文档/README/DESIGN 统一话术："为降低 Demo 复杂度采用进程内异步队列，任务状态落库但队列不持久化，生产可平滑替换 Celery/RQ/消息队列"。
2. **分析状态名对齐规格 2.7.12**：`analysis_tasks.task_status` 改为规格枚举 `queued → querying_document → loading_attachments → parsing_attachments → analyzing → succeeded/failed`（原实现 task_status="running"+current_step 分离；现用 `_stage()` 直接写 task_status，current_step 仅镜像）。seed 等待条件改为"非终态"；function-map/architecture 同步。修正 function-map 里残留的 `loading_document`。
3. **DESIGN.md 收敛模式标签**：保留 6 个明确模式（策略/适配器/单例/责任链/装饰器-DI/工厂）；run_pipeline、前端轮询、router→service、SQL 封装移入"有味道不强贴 GoF"清单；附面试建议"少贴标签、多讲职责"。
- 回归：pytest 27/27、冒烟 29/29、compile 通过。

### 16:00 - 第二轮审查修复：P0（权限 / 状态机 / 版本 / 规则异常）
**P0-1 分析/报告 L2 数据权限闭环**
- `analysis_service.get_status()` 增加 `_visible_task`（task 对应 document 必须可见）；
- `reports.py` 重写：list 按 `visible_document_ids` 过滤、export 校验可见、manual-reviews 限 admin + 自己审批任务范围（新 `scopes.approval_document_ids`）；
- `riskfindings.py` 校验 finding 所属 document 可见。
- 冒烟新增 5 项越权断言（张三读李四任务/导出李四报告/列表不见李四、未分配任务的 sunqi 复核被拒、wangwu 复核通过）。

**P0-2 voided 终态**
- `document_service.void()`：作废审批中单据 → 取消 running 实例 + pending 任务 + 未开始分析任务；
- `workflow_service` 新增 `_ensure_processable`（instance 必须 running + document 必须 pending_review/reviewing），approve/return/reject 统一执行；
- 回归测试：提交→作废→旧 task approve → 409，单据仍 voided。

**P0-3 版本语义**
- `current_version` 默认 0（=最近一次正式提交版本）；提交时 `new_version = current_version + 1`；
- `_snapshot(doc, user, version_no)` 不再自行递增；`document_version` 附件模型默认 0（暂存），提交时绑定到本次版本；
- 首次提交三者（snapshot/current_version/instance.document_version）=1，重提交=2，附件可追溯进入版本；
- 修复模型 `DocumentAttachment.document_version` 默认 1→0 的 off-by-one。

**P0-4 规则异常不静默跳过**
- `rule_engine.run_all` 删除 `except: continue`，异常带 rule_code 抛 RuntimeError；
- 流水线 catch → `analysis_task = failed`、`current_step` 保留 analyzing、`error_message` 指明规则、不生成报告；
- 测试：注入会抛异常的规则 → task failed、无 succeeded 报告。

### 16:30 - 第二轮审查修复：P1
**P1-1 对话 fallback + 冲突澄清**：本地规则槽位解析（五类中文名/简称 + CP/AP/BP/EX/TR-编号正则）；查询同时校验 type+no；冲突 → 澄清 A（采用单据实际类型）/B（重新输入）；无 key 纯槽位可跑通；已确认槽位不重复询问、显式纠正覆盖。测试 3 条。
**P1-2 提交前附件类别校验**：`DocumentAttachment.document_category`（上传时按文件名识别）；提交按 REQUIRED_ATTACHMENTS 类别校验（两张 invoice 不能冒充 contract+invoice）。测试 1 条。
**P1-3 审批意见 + 状态日志**：approve/return/reject 带 `review_comment` 落库；`_status_log` 记录 pending_review→reviewing / →approved/returned/rejected，operator_id 记真实审批人（`_mark_processing(db, doc, operator_id)`）。
**P1-4 附件解析幂等**：重解析前删旧 InvoiceRecord/ParseResult；同发票两次解析金额不翻倍。测试 1 条。
**P1-5 文本 PDF 发票正则失败→渲染 OCR**：`_parse_real_invoice` 文本 PDF 正则失败仍 `_pdf_to_images` 走专用发票 OCR。
**P1-6 输入边界**：`total_amount>0`、`amount>0`、`quantity>0`、`unit_price>=0`（Pydantic Field）；payment_ratio 0~100、差旅开始≤结束（schema 校验）。测试 4 条。
**P1-7 规则分支补齐**：contract 加"乙方 vs 收款单位"、"付款条件表述"；batch 加 payment_count 声明对比（schema 新增 payment_count 必填）；expense 标准按 effective_date 选消费当日有效；spend 加"同日多地""同日多单据疑似拆单"。
**P1-8 LLM 只润色**：报告固定结构化结论权威；LLM 叙述作为独立"### AI 风险说明"小节插入，不覆盖 overall/recommendation。

### 16:50 - 第二轮审查修复：P2
- 附件大小上限读 `sys_params.attachment.max_size_mb`（不再硬编码 10MB）；
- 置信度阈值唯一权威源 = `sys_params.attachment.confidence_threshold`（规则 build_context 注入）；
- `ContractFields.party_a/party_b` 改 Optional（与 Prompt"无法确定可为 null"对齐）；
- OCR/文本提取无可靠置信度时如实 None（不再伪造 0.95/0.98）；
- 百度 access token 失效（401/110/111）自动清空重取一次；
- 新增第二个审批人 sunqi（无分配任务，越权测试用）。

### 17:00 - MySQL 实跑 + git 提交
- 本机 MySQL80 服务已启动，但 root/atguigu 多次 Access denied（含用户给的多组密码）→ 用户指示"重建"：备份 Data→Data.bak，`mysqld --initialize-insecure` 重初始化，root 密码设为 Atguigu.123，创建 atguigu/Atguigu.123 + finance_risk 库。
- **MySQL 全链路验证**：`seed.py` 5 单全提交、5 分析任务 succeeded（档位与 SQLite 一致，P1-7 新规则抓到 CP 合同"首付"vs单据"首付款"的真实表述差异）；`smoke_test.py` **34/34 PASS**。
- 创建 `backend/.env`（atguigu/Atguigu.123，gitignored）；`.env.example` 同步；seed 等待上限 60→120s（MySQL 首跑较慢）。
- 文档同步：architecture §7 终态守卫/§12 对话 fallback+冲突/§13 AI 风险说明/§10 确定性表述；function-map §1.5/§1.7/§2.8；DESIGN §1.8 "确定性规则"（不再叫纯函数）。
- **git**：仓库已有历史（first commit + 一版修复），提交 `6ebc488`"第二轮修复 P0+P1+P2"。
- 回归：pytest 40/40、compile OK、smoke SQLite 34/34 与 MySQL 34/34。

### 最终状态
- P0/P1/P2 全部落地并验证（SQLite + MySQL 双跑）。
- 仍需人工确认：真实 LLM/OCR key（当前演示走 preset fallback）；MySQL Data.bak 原数据目录保留未删。

---

## 第四轮：审批体系 + 状态一致性 + 权限体验 + 公共业务收口

### P0
- **P0-1 审批人 Resolver**：`resolve_approver(db, document, node)`——候选=持有 node.approver_role AND approval:process 的 active 用户，**排除申请人**（禁止自审）；按待办数 ASC → user.id ASC 确定性选择；无合法审批人抛 409（不再产生 approver_id=None 的 pending 任务）。workflow 配置校验同步要求"角色下必须有 approval:process 用户"。
- **P0-2 finance/approver 职责统一**：正式审批节点一律 `approver` 角色；财务专业复核走 `analysis:review`/`ManualReview`。seed 流程去掉"财务复核→finance"节点；支持**多角色用户**（liuxi = finance+approver，财务负责人）。
- **P0-3 Workflow priority**：`approval_workflows.priority`；匹配 = 类型+金额+部门 → priority DESC → id ASC。seed 加"大额对公付款审批"（≥50000, priority 10）。
- **P0-4 状态机合法性**：`document_state.TRANSITIONS` 表，`transition()` 校验非法迁移（draft→approved 等）→ 409。
- **P0-5 分析取消**：`cancel_for_document`（void/withdraw 取消未完成分析）+ `_is_aborted` 合作式检查（入口 + 各阶段 + 报告前），取消后不生成报告。
- **P0-6 审核记录导出**：recordsView 改 `dlExport`（fetch+JWT）；删除 `exportUrl`/`attUrl`。

### P1
- **P1-7 ManualReview 语义**：枚举改 `confirmed/needs_material/escalated`，**不改变 document_status**（正式审批只走 ApprovalTask）；前端复核表单文案去"通过/驳回"措辞。
- **P1-8 parse 权限**：`POST .../parse` 改 `require_perm("analysis:create")` + L2 可见。
- **P1-9 附件**：magic bytes（PDF/PNG/JPEG）扩展名校验；`document_category` 可选（用户指定 > 文件名识别 > other）；类别枚举标准化 `invoice/contract/itinerary/payment_basis/other`（原 payment_doc 更名）；前端上传区加类别选择。
- **P1-10 规则 config 校验**：已知字段类型/范围错误保存时 400（tolerance_pct/ratio_gap/exceed_pct/deviation_pct ≥0，history_spike_ratio >0）。
- **P1-11 前端权限菜单**：按 permission_codes 显隐菜单；风险页 `analysis:review` 才显示确认/排除与复核表单。
- **P1-12 复制**：copy 复制 line_items（不复制附件/版本/审批/分析/报告），新单 draft、current_version=0。
- **P1-13 金额统一**：`services/amount_service.py::calculate_amount_comparison` 唯一实现；`compare_amounts` 与 `report_service` 复用。

### P2
- **Risk Engine 拆分**：`models.py`（Finding/LEVEL_SCORE）、`context.py`（RuleContext/build_context/load_configs/DEFAULT_CONFIGS）；engine.py 保留 run_all/compute/10 条规则；`rules/*.py` 子拆解记为规划项。
- **app.js views 子拆解**：记为规划项（本轮未做，避免扩大改动面）。

### 验证
- pytest **52/52**；smoke_test **48/48**（SQLite 与 MySQL 双跑）。
- 新增 `tests/test_round4.py`（11 条）：Resolver 自审/无权限/负载均衡/稳定、priority、状态机非法迁移、取消不生成报告、复制明细、金额统一、ManualReview 不改状态。
- 冒烟新增：finance-only 不能审批、finance+approver 有审批权、parse 权限、magic bytes、category 指定、非法 config。
- 说明：seed 中所有角色都有 `analysis:create`，parse 权限的拒绝面由 L2 覆盖（冒烟改用 L2 拒测）。

### 仍刻意不做（第四轮）
- `rules/*.py` 十条规则按文件拆分、前端 `views/*.js` 拆包、Repository 100% 覆盖（无意义大改）。
- 完整组织架构/部门树/代理审批/会签、finance_manager 等新 RBAC 角色（用 position_level 表达真实职级）。

### 收尾：MySQL 可复现 + 五类单据 E2E
- 新增 `docker-compose.yml`（MySQL 8 测试库，atguigu/Atguigu.123，可复现）。
- **判断：不写 Dockerfile**——交付为"代码+截图"，容器化非刚需；当前部署口径=本地 uvicorn + 可选 compose 起 MySQL（见上）。
- 新增 `scripts/e2e.py`：五类单据完整 E2E（create→明细→附件→提交→解析→分析→报告→导出→多节点审批），解析走预制、动态找审批人。MySQL 上 **20/20 PASS**。
- 清单状态：DATABASE_URL→MySQL 全链路 ✅（smoke 48/48 + e2e 20/20）；DeepSeek key 已配置、**百度 OCR key 待用户提供**（auto/real 真实 OCR 需 key）；preset 占位 PNG 待真实样例替换。

---

## 第三轮：缺陷修复 + 分层重构（收口）

### 17:30 - P0 缺陷
- **P0-1 明细重复 INSERT**（前端）：编辑页保存改为 diff 同步——已存在→PATCH、新增→POST、已删除→DELETE、未修改不重复插入；行带 `data-id` 追踪。后端契约冒烟验证（add2/patch/delete → 剩 1）。
- **P0-2 type_fields 双向映射**：`DocumentOut.type_fields` 用 `validation_alias=type_fields_json` + `serialization_alias=type_fields`；顺带修 JSON 列不能存 Decimal/date（number→float、date→ISO 字符串）。
- **P0-3 下载/导出带 JWT**：前端 `<a href>` 直链改 `API.download`（fetch + Authorization → Blob → objectURL）；`ui.js` 提供 dlAtt/dlExport。
- **P0-7 `select(...).delete()`**：admin `_set_roles`/`update_role_permissions` 改为标准 `delete(...)`（此前是运行时错误，latent）。

### 17:50 - P1 收口
- **P1-7 workflow 校验**：document_type 合法、≥1 节点、node_order 唯一、approver_role 合法、amount_min≤max、角色下有可用用户。
- **P1-8 防重复分析**：`create_or_get_task` 复用 queued/running 任务；新增 `GET /documents/{id}/analysis/latest`；前端风险 Tab 默认加载最新报告，仅显式"发起分析/重新分析"才新建。
- **P1-12 附件删除级联**：删附件先删 parse_results + invoice_records。
- **P1-13 状态参数**：前端 `document_status` → `status` 对齐后端。
- **P1-14 supplier**：`total_paid` 只统计 approved；lookup import。
- **P1-2 审批意见**：前端审批待办加意见输入，传 review_comment。

### 18:10 - 后端分层重构
- **clients/**：`clients/ocr/{base,baidu}.py`、`clients/llm/{base,deepseek}.py`；services 只依赖统一接口（原 services/ocr_client、llm_client 删除）。
- **domain/**：`document_state.py`（状态机：guard/transition 唯一权威）、`access_policy.py`（L2+L3 收口）、`risk_engine/`（原 rule_engine 迁入，公开 build_context/run_all/compute_overall_level）。
- **repositories/**：5 聚合（Document/Attachment/Workflow/Analysis/User），workflow/document/analysis 核心读路径已接入。
- **事务边界（P0-8）**：`sysparam_service.set_value` 不再 commit，admin 统一一个事务提交参数+审计；login 审计改走 `audit_service`。
- **前端收口**：共享工具抽到 `ui.js`；app.js 按 section 组织（views/* 子拆解记入后续）。

### 18:30 - 稳定性修复（跑通落地）
- **seed 同步跑分析**：改为 `asyncio.run(run_pipeline)` 逐任务同步执行（关闭后台入队），规避后台线程+SQLite 并发锁问题；服务器端仍走 enqueue 后台循环。
- **SQLite WAL**：db/session 加 `PRAGMA journal_mode=WAL` + busy_timeout，支持多线程读写。
- **冒烟幂等性**：冒烟含"审批通过"会消耗待办，要求每次配新 seed（已文档化）。

### 19:00 - 验证与提交
- pytest **41/41**；smoke_test **41/41**（SQLite 与 MySQL 双跑）。
- 新增断言：明细 add/patch/delete 契约、type_fields 双向、空节点/非法角色 workflow 拒、角色权限 SQL、sys param 审计、create_or_get_task 复用。
- 提交见 git log（本轮改动）。
- [ ] 启动 Docker Desktop → 跑 MySQL 生产路径
- [ ] 配置 DeepSeek + 百度云 key → 演示真 OCR/LLM 路径
- [ ] 替换占位 PNG 为真实发票/合同样例图
- [ ] 按 function-map.md §5 背诵顺序过一遍
