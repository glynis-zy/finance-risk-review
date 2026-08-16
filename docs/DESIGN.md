# 设计原则与设计模式

> 本文件回答面试最可能被拷问的三个问题：设计原则是什么、用了哪些设计模式（在哪）、系统怎么扩展。
> 每个落点都是真实代码，可直接 `goto`。

---

## 1. 设计原则

### 1.1 单一职责（SRP）
- **路由层**（`routers/*.py`）只做三件事：收 HTTP 参数、调 service、回响应。
- **服务层**（`services/*.py`）一个 service 一个领域，互不掺和：
  `auth_service`（认证）/ `document_service`（单据+状态机）/ `workflow_service`（审批）/ `rule_engine`（判定）/ `analysis_service`（调度）/ `report_service`（报告）/ `audit_service`（审计）/ `attachment_service`（文件）/ `dialogue_service`（对话）/ `parse_pipeline`（解析）/ `sysparam_service`（系统参数）。

### 1.2 开闭原则（OCP）——三处"加配置不改代码"
| 扩展点 | 机制 | 新增成本 |
|---|---|---|
| 单据类型 | `document_schemas/` 元数据（字段定义+校验） | 加一份配置，平台零改动 |
| 风险规则 | `rule_engine.REGISTRY` 注册表 | 注册一个 `check_xxx(ctx)` 纯函数 |
| 外部厂商 | `llm_client`/`ocr_client` 适配层 + `.env` | 只改环境变量 |

### 1.3 依赖倒置（DIP）
业务代码依赖 `llm_client.extract_contract_fields()` / `ocr_client.ocr_invoice()` 这样的**抽象接口**，不依赖具体厂商 SDK。换 DeepSeek→通义 / 百度云→阿里云，只动适配层实现与配置。

### 1.4 接口隔离 / DTO 边界
Pydantic `schemas/*.py` 是内外边界：LLM 输出必须过 `ContractFields`/`SlotUpdate` 校验（**禁止自由文本字段**）；HTTP 入参出参走 DTO，ORM 对象不直接暴露。

### 1.5 关注点分离（四层流水线）
`OCR 看懂 → LLM 理解 → 规则引擎判定 → LLM 润色`。每层只干一件事，风险结论永远由规则引擎产出（可复现、可审计）。

### 1.6 最小必要依赖
不引 Celery/Redis/WebSocket/Vue/PDF库。异步用进程内 asyncio 队列 + 前端轮询；导出用 HTML。**中间件越少越可解释**。

**异步边界（要主动说清楚）**：进程内 asyncio 队列**仅限单进程 Demo**——多 worker 时各进程有独立队列、重启丢未完成任务。分析任务**状态落库**（`analysis_tasks`），但队列本身不提供持久化。生产环境需 `uvicorn --workers 1`，可平滑替换为 Celery/RQ/消息队列。面试话术："为降低 Demo 复杂度采用进程内异步队列；状态落库、队列不持久化，生产可替换"，比自称"生产级异步任务系统"更专业。

### 1.7 纵深防御（三层权限）
RBAC（能不能做）→ 数据权限（能看哪些行）→ 状态权限（什么状态能做什么）。任何一层不过即拒绝。

### 1.8 可测试性
规则引擎是**纯函数**（只读输入返回输出，无副作用），配合 `pytest` 可单测；依赖全部走 `Depends` 注入（`get_db`/`get_current_user`），冒烟测试用 `TestClient` 全链路断言。

---

## 2. 用到的设计模式（克制地贴标签）

> 面试建议：模式名点到为止，多讲"职责是什么、为什么这样拆"，比贴一堆标签更不容易被追问打穿。

### 2.1 明确使用的模式

| 模式 | 代码位置 | 一句话 |
|---|---|---|
| **策略模式** | `rule_engine.REGISTRY` + `run_all()` | 风险规则是策略注册表，新增规则=注册一个函数，启停/阈值由 `risk_rules` 配置驱动，调用方零改动 |
| **适配器模式** | `services/llm_client.py`、`services/ocr_client.py` | 外部厂商封装成统一接口，换厂商只改适配器和 `.env` |
| **单例（懒加载）** | `llm_client._get_client()`、`analysis_service._get_loop()` | 客户端/后台事件循环全局一份，首次调用才初始化 |
| **责任链** | `perms`(L1) → `scopes`(L2) → 状态守卫(L3) | 权限三级校验依次执行，任一关不过即 4xx |
| **装饰器/DI** | `require_perm()` 依赖工厂 + FastAPI `Depends` | 权限作为依赖注入到路由签名，接口声明即权限声明 |
| **工厂/注册表** | `parse_pipeline.recognize_document_type` + `build_context` | 按文档类别路由解析路径；聚合构造规则上下文 |

### 2.2 有"模式味道"，但不强贴 GoF 标签

| 结构 | 实际是什么 |
|---|---|
| `run_pipeline()` 固定骨架 + 可替换阶段 | 有"模板方法"味道，但严格 GoF 模板方法要求子类覆写钩子；这里只是函数编排，不贴标签 |
| 前端轮询 `/analysis-tasks/{id}` | 拉取式状态检查，不是观察者模式 |
| `routers/*` → `services/*` | Controller/Service 分层：router 是控制器（收参/权限/回响应），service 是业务 |
| services 封装 SQLAlchemy 查询 | Repository 风格：业务不拼 SQL，查询收敛在 service |

> 诚实的边界：没有用"状态模式"把状态机拆成状态类（守卫表 + 显式 `_transition` 规模足够且更好讲）；没有用事件总线/消息队列（异步边界见 §1.6，Demo 选进程内队列）。被问到就答"按规模选了更简单可解释的方案"。

---

## 3. 管理端与系统参数（规格 2.7.3）

**角色**：`admin` 角色具备 `user:manage`/`role:manage`/`system:manage`，前端菜单按权限动态显示"系统管理"页。

**`sys_params` 系统参数表**（运行时修改，业务代码通过 `sysparam_service` 读取，不硬编码）：

| key | 作用 | 默认 |
|---|---|---|
| `risk.medium_bump_count` | 整体风险升级：medium≥N 升 high | 3 |
| `risk.low_bump_count` | 整体风险升级：low≥N 升 medium | 5 |
| `attachment.max_size_mb` | 附件大小上限 | 10 |
| `attachment.confidence_threshold` | OCR 置信度阈值 | 0.8 |
| `ocr.mode` | real=真实OCR/LLM失败即失败；auto=真实→失败回退预制；preset=仅预制不调外部API | auto |

**管理端 API**：`/admin/users`、`/admin/roles/{id}/permissions`、`/admin/permissions`、`/admin/sys-params`。
非管理员访问一律 403（冒烟测试已断言）。

---

## 4. 面试问答预演

**Q: 为什么规则引擎做成注册表而不是 if-else？**
A: ①新增规则只注册一个函数，不碰调用方（开闭）；②每条规则可独立启停/配阈值（策略）；③纯函数可单测（pytest 14 条用例）。规模再大可以拆成独立 worker。

**Q: LLM 为什么不做风险判定？**
A: 财务结论必须可复现、可审计、同一输入同输出。LLM 判定无法保证确定性；所以 LLM 只做合同字段提取（结构化 JSON + Pydantic 校验）和报告润色，判定永远在规则引擎。

**Q: 换 LLM/OCR 厂商要改什么？**
A: 只改 `.env`（`LLM_BASE_URL/API_KEY`、`OCR_API_KEY/SECRET`）。适配层封装了 OpenAI 兼容接口和百度 REST，业务不感知厂商。

**Q: 系统参数为什么放表里不放配置文件？**
A: 管理员要运行时调整（比如把"3 个 medium 升级 high"改成 4），改表立即生效且带审计；配置文件的修改要重启且无审计。

**Q: 三层权限怎么实现的？**
A: L1 `require_perm`（角色→权限码）→ L2 `scopes.visible_document_ids`（行级，查询层强制过滤，非前端拼 SQL）→ L3 状态守卫表（动作×允许状态）。三层叠加才是完整授权。
