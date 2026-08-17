# 财务单据智能风险审核系统

基于设计文档 `zy-项目实战.md` 第 2.7 节实现。系统把能力分四层：

> **OCR 负责"看懂文档"，LLM 负责"理解非结构化文本"，规则引擎负责"确定性判断"，LLM 只做最后的"自然语言解释"。风险结论永远由规则引擎决定。**

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | FastAPI + SQLAlchemy + Pydantic |
| 数据库 | MySQL 8（Docker 运行；`DATABASE_URL` 可切 SQLite） |
| 前端 | 原生 HTML/CSS/JS 单页（FastAPI 托管，无构建） |
| 异步 | 进程内 asyncio 队列 + 前端轮询（无 Celery/Redis）——**仅限单进程 Demo**，队列不持久化 |
| LLM | DeepSeek（适配层，厂商可换）——合同字段提取 / 对话 NLU / 报告润色 |
| OCR | 百度云（发票专用 + 通用）——三种模式：`real` / `auto`(真实→失败回退预制) / `preset`(仅预制) |
| PDF | pypdf（文本型直取）+ PyMuPDF（扫描型渲染每页→逐页 OCR，页码定位） |

## 目录结构

```
finance-risk-review/
├── docs/
│   ├── architecture.md    # 架构设计（面试主材料）
│   ├── function-map.md    # 函数级地图（背诵材料）
│   └── DEVLOG.md          # 开发记录
├── backend/
│   ├── app/
│   │   ├── routers/       # HTTP 控制器（薄）
│   │   ├── services/      # UseCase 编排
│   │   ├── repositories/  # 数据访问聚合（5 个）
│   │   ├── domain/        # 状态机 / 访问策略 / 风险引擎
│   │   ├── clients/       # OCR(baidu) / LLM(deepseek) 适配器
│   │   ├── core/          # config/security/perms/scopes/deps
│   │   ├── db/            # session/init
│   │   ├── models/        # SQLAlchemy ORM
│   │   ├── schemas/       # Pydantic DTO
│   │   └── document_schemas/  # 单据类型元数据
│   ├── demo/preset_parse/ # 附件预制解析结果（demo 链路确定性）
│   ├── data/uploads/      # 上传附件存储
│   ├── scripts/           # init_db / seed / smoke_test / verify
│   └── requirements.txt
├── frontend/              # 原生单页（index.html + css + js/ui.js/app.js）
└── demo/                  # （预留）
```

## 快速启动

```bash
# 1. 启动 MySQL（Docker）
docker run --name frr-mysql -e MYSQL_ROOT_PASSWORD=root \
  -e MYSQL_DATABASE=finance_risk -p 3306:3306 -d mysql:8

# 2. 后端依赖
cd backend
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt

# 3. 配置 .env（从 .env.example 复制；LLM/OCR 可选，不配则走演示 fallback）

# 4. 灌入演示数据（角色/权限/用户/流程/规则/5 类示例单据+附件+预制解析，并提交触发分析）
python scripts/seed.py

# 5. 启动（进程内异步队列仅单进程有效，务必 --workers 1）
uvicorn app.main:app --reload --workers 1 --port 8000
```

打开 http://127.0.0.1:8000 即可使用。

**演示账号**（密码均 `123456`）：`zhangsan`/`lisi`（单据申请人）、`wangwu`/`sunqi`（审批人员）、`zhaoliu`（财务人员）、`liuxi`（财务负责人 = **finance+approver 多角色**）、`admin`（系统管理员）。

> 职责：`finance` 做财务专业审核/风险复核/规则/供应商；`approver` 做正式审批（approve/return/reject）。正式审批节点一律 approver 角色；真实职级用 `position_level`，不拆成多个 RBAC 角色。

## 演示链路（规格 2.7.15 验收）

1. `zhangsan` 登录 → 单据管理 → 新建/编辑/提交对公付款单（schema 驱动动态表单 + 明细 + 附件上传）
2. 提交后自动：版本快照 → 审批实例 → 分析任务（后台解析附件→规则引擎→生成报告）
3. 单据详情"风险分析" tab 查看：整体风险等级、风险项、实际值/参考值/阈值、处理建议、报告全文与 HTML 导出
4. `wangwu` 登录 → 审批待办 → 查看单据风险后通过/退回/驳回
5. 财务/管理员可配置规则阈值、查看审核记录与审计日志
6. "智能审核对话"输入自然语言（如"对公付款单 CP-20260816-001"）→ LLM NLU 抽槽 → 发起分析

**预制示例的风险档位**：CP=低(建议通过)、EX/TR=中(补充材料)、AP/BP=高(人工复核)，覆盖 10 条规则的大部分分支。

## 测试

```powershell
# 先用 SQLite 跑一遍全链路（无需 MySQL）
$env:DATABASE_URL="sqlite:///./smoke.db"
python scripts/seed.py
python scripts/smoke_test.py     # 27 项端到端断言

# 单元测试（规则引擎等纯逻辑）
pip install -r requirements-dev.txt
python -m pytest tests -q          # 14 条用例
```

## 管理端

`admin` 登录后左侧出现"系统管理"：用户管理（建用户/分配角色/启停）、角色权限（勾选权限码）、系统参数（风险升级阈值、OCR 模式 `preset/auto/real` 等，改完下一次分析生效）。设计原则与设计模式见 `docs/DESIGN.md`。

## 接入真实 LLM/OCR（可选）

编辑 `.env`：

```ini
LLM_API_KEY=sk-xxx          # DeepSeek
OCR_API_KEY=xxx             # 百度云
OCR_SECRET_KEY=xxx
```

配置后：附件走真实 OCR（发票专用识别 / 通用识别），合同走"OCR 全文 → LLM 结构化提取（Pydantic 校验）"，报告由 LLM 润色。未配置 key 时 `auto` 模式自动回退 `demo/preset_parse/` 预制结果（`ocr.mode` 可在管理端切换 `preset`/`auto`/`real`），演示链路始终确定性可跑。
