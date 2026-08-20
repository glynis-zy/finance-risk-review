# 前后端联调测试报告

- **日期**：2026-08-17 17:00–17:30
- **环境**：本机 MySQL 8.0.26（`finance_risk` 库，29 张表）+ FastAPI 0.141.1 + 原生前端单页
- **配置**：`.env` 已配置真实 DeepSeek LLM key + 百度云 OCR key（`ocr.mode=auto`：真实优先，失败回退预制）
- **服务**：`uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1`（单进程，进程内异步队列）
- **前置**：`scripts/seed.py` 灌入演示数据（5 类单据全部提交并触发分析）

## 一、测试矩阵与结果

测试脚本：`backend/scripts/integration_test.py`（真实 HTTP 调用，模拟前端 API 层全部请求）

**最终结果：45 PASS / 0 FAIL**（另有 pytest 52/52 无回归）

| 组 | 覆盖点 | 结果 |
|---|---|---|
| A 认证 | 登录成功 / 错误密码 401 / me 返回角色权限码 | ✅ 3/3 |
| B 单据生命周期 | 5 类单据 schema、L2 数据权限（张三仅见 3 单）、新建对公付款单、明细增改删、附件上传（magic bytes 校验）、金额核对、提交→pending_review→自动分析、分析 succeeded、风险项 6 条、报告（含 AI 风险说明小节）、HTML 导出、复制单据 | ✅ 14/14 |
| C 审批流 | 审批人登录、待办列表、L2 可见任务单据、审批通过带审批意见（P0-1 负载均衡：新单任务分配给 liuxi） | ✅ 4/4 |
| D 智能对话 | 创建会话、NLU 抽槽查单（"对公付款单 CP-20260816-001"→ 定位单据 + 发起分析） | ✅ 2/2 |
| E 配置与复核 | 规则列表 10 条、非法 config 400 / 合法 200、流程列表 6 条、供应商黑名单命中（远东贸易 blacklisted）、报告列表、人工复核 ManualReview（不改单据状态） | ✅ 7/7 |
| F 管理端 | 用户/角色/权限/系统参数、参数更新（attachment.max_size_mb 20→还原）、审计日志含变更记录 | ✅ 7/7 |
| G 安全越权 | 申请人访问管理端 403、查看他人单据 403、审批他人任务 403、无令牌 401、注销后旧令牌 401 | ✅ 6/6 |

**关键验证点**：
- 真实 LLM 链路：报告含「AI 风险说明」小节（DeepSeek 润色成功，非 fallback 模板）
- 真实 OCR 链路：占位 PNG 真实 OCR 失败 → auto 模式自动回退预制解析，链路确定性保持
- 规则引擎真实触发：新单 CP（付款条件/供应商维度）整体 high；seed 档位 CP=low / AP=high / BP=high / EX=medium / TR=medium

## 二、联调发现并修复的问题（4 个）

### 1.【环境】`.env` 编码损坏，DATABASE_URL / OCR_API_KEY 被吞进注释行
- **现象**：应用连默认 root 账号失败（Access denied）；OCR key 未加载
- **根因**：`.env` 文件被多次编辑后，注释与 key=value 粘连成一行（`# 注释…DATABASE_URL=…`），整行按注释解析
- **修复**：重建 `.env`，key=value 独立成行，UTF-8 无 BOM；全部密钥值保留
- **验证**：MySQL 8.0.26 连接成功，LLM/OCR key 加载正常

### 2.【P0 代码】`routers/suppliers.py` 缺少 `HTTPException` 导入
- **现象**：`GET /suppliers/lookup`（查不到供应商）→ 500，日志 `NameError: name 'HTTPException' is not defined`
- **根因**：`lookup_supplier` 404 分支使用了未导入的 HTTPException（latent bug，仅未命中时触发）
- **修复**：`from fastapi import APIRouter, Depends, HTTPException`
- **验证**：lookup 远东贸易 → S002，risks → blacklist_status=blacklisted

### 3.【P0 代码】`DocumentOut.type_fields` 校验崩溃（费用报销单列表 500）
- **现象**：任何包含费用报销单（EX）的列表请求 → 500（`type_fields_json: Input should be a valid dictionary`）
- **根因**：expense 无专属字段，seed 未写 `type_fields_json` 存为 NULL；`DocumentOut` 用 `validation_alias` 读 ORM 列，NULL 不满足 dict 校验
- **修复**：`field_validator("type_fields", mode="before")` 将 None → `{}`（容忍历史/空数据）；seed 中 EX 单显式写 `type_fields_json={}`
- **验证**：lisi 登录查单 2 条正常；DTO 单测 52/52 通过

### 4.【P1 脚本】`seed.py` wipe 的 FK 依赖顺序错误，对话链路后 seed 重建必挂
- **现象**：对话触发分析（任务带 `session_id`）后，再次 seed → `DELETE FROM review_sessions` / `analysis_tasks` / `users` 外键约束失败
- **根因**：三层 FK 链未按依赖倒序清理：
  - `analysis_tasks.session_id → review_sessions.id`（AnalysisTask 必须早于 ReviewSession）
  - `review_reports.task_id → analysis_tasks.id`（ReviewReport 早于 AnalysisTask）
  - `sys_params.updated_by → users.id`（SysParam 早于 User）
- **修复**：wipe 顺序调整为 `SessionMessage → ManualReview → ReviewReport → RiskFinding → AnalysisTask → ReviewSession → … → SysParam → User`
- **验证**：seed 全链路重建成功（5 单提交 + 5 任务 succeeded）

## 三、遗留风险 / 待办

| 项 | 状态 | 说明 |
|---|---|---|
| 真实发票/合同样例图 | 未完成 | 占位 PNG 会让真实 OCR 失败（auto 回退预制）；换真实样例后 `real` 模式可全真链路 |
| `ocr.mode=real` 全真验证 | 未完成 | 需真实单据图片 + 百度云 key 有效（当前 key 已配置，未验证真实识别结果质量） |
| 多进程/生产化 | 刻意不做 | 进程内异步队列仅单进程 Demo，重启丢未完成任务（文档已声明） |
| `datetime.utcnow()` 弃用告警 | 观察项 | 28 个 DeprecationWarning（非阻塞） |

## 四、复现方式

```bash
cd backend
# 1. 重建演示数据（当前已修复 FK 顺序问题，可反复重建）
.venv\Scripts\python.exe scripts/seed.py
# 2. 启动服务（联调已按此配置验证）
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
# 3. 联调测试（新开终端；注意：会消耗审批待办，重跑前重新 seed）
.venv\Scripts\python.exe scripts/integration_test.py
```
