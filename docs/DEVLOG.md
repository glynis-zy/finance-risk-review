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
- `services/ocr_client.py`：百度云 OCR 适配层（发票专用 + 通用；AUTO→预制案例→失败 三级）。
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
- 目的：演示链路完全确定性、不依赖外部 API 稳定性（AUTO→预制→失败 三级在文档级生效）。

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

### 下一步（由你决定）
- [ ] 启动 Docker Desktop → 跑 MySQL 生产路径
- [ ] 配置 DeepSeek + 百度云 key → 演示真 OCR/LLM 路径
- [ ] 替换占位 PNG 为真实发票/合同样例图
- [ ] 按 function-map.md §5 背诵顺序过一遍
