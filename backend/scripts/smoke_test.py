# -*- coding: utf-8 -*-
"""端到端冒烟测试（TestClient，无需起服务）。

用法（backend/ 下，DATABASE_URL 指向已 seed 的库）：
    $env:DATABASE_URL="sqlite:///./smoke.db"; python scripts/smoke_test.py
覆盖：健康检查 / 前端页面 / 登录 / 单据列表与详情 / 金额核对 / 分析结果 /
审批待办与通过 / 报告导出 / 附件下载 / 供应商 / 规则 / 审计。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

OK = []
FAIL = []


def check(name: str, cond, extra: str = "") -> None:
    (OK if cond else FAIL).append(name)
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {extra}")


client = TestClient(app)

# 1. 健康检查
check("GET /health", client.get("/health").status_code == 200)

# 2. 前端页面与静态资源
r = client.get("/")
check("GET / 返回前端页面", r.status_code == 200 and "财务单据" in r.text)
check("GET /static/css/style.css", client.get("/static/css/style.css").status_code == 200)
check("GET /static/js/app.js", client.get("/static/js/app.js").status_code == 200)

# 3. 登录
login = client.post("/api/v1/auth/login", json={"username": "zhangsan", "password": "123456"})
check("申请人 zhangsan 登录", login.status_code == 200, f"-> {login.status_code}")
token = login.json()["access_token"]
h = {"Authorization": f"Bearer {token}"}
check("me 返回角色", client.get("/api/v1/auth/me", headers=h).json()["role_codes"] == ["applicant"])

# 4. 单据列表（数据权限）
docs = client.get("/api/v1/documents?size=100", headers=h).json()
check("zhangsan 单据列表", docs["total"] >= 1, f"total={docs['total']}")
own = {d["document_no"] for d in docs["items"]}
check("数据权限过滤（只含本人单据）", own <= {"CP-20260816-001", "BP-20260816-003", "TR-20260816-005"}, f"got={own}")

# 5. 单据详情
cp = next(d for d in docs["items"] if d["document_no"] == "CP-20260816-001")
detail = client.get(f"/api/v1/documents/{cp['id']}", headers=h).json()
check("单据详情含明细/附件", len(detail["attachments"]) == 2, f"attachments={len(detail['attachments'])}")

# 6. 金额核对
amt = client.get(f"/api/v1/documents/{cp['id']}/amount-comparison", headers=h).json()
check("金额核对：发票=单据", amt["document_total"] == amt["invoice_total"], f"{amt['invoice_total']}")

# 7. 分析任务 / 风险项 / 报告
t1 = client.get("/api/v1/analysis-tasks/1", headers=h).json()
check("分析任务1 成功", t1["task_status"] == "succeeded")
rpt = client.get("/api/v1/analysis-tasks/1/report", headers=h).json()
check("CP 报告整体风险=low", rpt["overall_risk_level"] == "low", rpt["overall_risk_level"])

# 8. 高风险单据的报告（单据申请人可看自己的报告；审批人按 resolver 负载均衡分派）
lisi = client.post("/api/v1/auth/login", json={"username": "lisi", "password": "123456"}).json()
hlisi = {"Authorization": f"Bearer {lisi['access_token']}"}
t2 = client.get("/api/v1/analysis-tasks/2/report", headers=hlisi).json()
check("AP 报告整体风险=high", t2["overall_risk_level"] == "high", t2["overall_risk_level"])
check("AP 含供应商黑名单风险项",
      any("黑名单" in f["risk_title"] for f in client.get("/api/v1/analysis-tasks/2/findings", headers=hlisi).json()))

# 9. 审批待办与通过
wangwu = client.post("/api/v1/auth/login", json={"username": "wangwu", "password": "123456"}).json()
hw = {"Authorization": f"Bearer {wangwu['access_token']}"}
tasks = client.get("/api/v1/approval-tasks", headers=hw).json()
check("审批人 wangwu 有待办", len(tasks) >= 1, f"count={len(tasks)}")
first = tasks[0]
approve = client.post(f"/api/v1/approval-tasks/{first['task_id']}/approve", headers=hw)
check("审批通过接口", approve.status_code == 200, approve.json())

# 10. 报告导出（AP 申请人 lisi）
exp = client.get(f"/api/v1/review-reports/{t2['report_id']}/export", headers=hlisi)
check("报告导出 HTML", exp.status_code == 200 and "<html" in exp.text.lower())

# 11. 附件下载
att = detail["attachments"][0]
dl = client.get(f"/api/v1/documents/{cp['id']}/attachments/{att['id']}", headers=h)
check("附件下载", dl.status_code == 200 and len(dl.content) > 0)

# 12. 供应商 / 规则 / 审计
zhaoliu = client.post("/api/v1/auth/login", json={"username": "zhaoliu", "password": "123456"}).json()
hf = {"Authorization": f"Bearer {zhaoliu['access_token']}"}
sup = client.get("/api/v1/suppliers/S002/risks", headers=hf)
check("供应商风险查询", sup.status_code == 200 and sup.json()["blacklist_status"] == "blacklisted")
check("规则列表", client.get("/api/v1/rules", headers=hf).status_code == 200)
check("审计日志", client.get("/api/v1/audit-logs", headers=hf).status_code == 200)

# 13. 越权校验：zhangsan 不能看 AP 单据
ap = client.get("/api/v1/documents/2", headers=h).status_code
check("越权访问被拒(403/404)", ap in (403, 404), f"-> {ap}")

# 14. 令牌撤销持久化（登出后该令牌失效，其他令牌不受影响）
u2 = client.post("/api/v1/auth/login", json={"username": "lisi", "password": "123456"}).json()
t2 = u2["access_token"]
client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {t2}"})
check("登出后旧令牌被拒(401)",
      client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {t2}"}).status_code == 401)
check("其他令牌不受影响", client.get("/api/v1/auth/me", headers=h).status_code == 200)

# 16. L2 数据权限越权（P0-1）：张三不能读/导出/看到李四的
t2st = client.get("/api/v1/analysis-tasks/2", headers=h).status_code
check("张三不能读李四的分析任务状态", t2st == 403, f"-> {t2st}")
rep2 = client.get("/api/v1/analysis-tasks/2/report", headers=hlisi).json()  # AP 申请人 lisi 可见
exp2 = client.get(f"/api/v1/review-reports/{rep2['report_id']}/export", headers=h).status_code
check("张三不能导出李四的报告", exp2 == 403, f"-> {exp2}")
zhang_list = client.get("/api/v1/review-reports", headers=h).json()
zhang_nos = [r["document_no"] for r in zhang_list]
check("张三报告列表不含李四单据", "AP-20260816-002" not in zhang_nos, str(zhang_nos))

# 审批人复核范围：对 AP 有任务者可复核，无任务者不可（resolver 负载均衡下动态找）
approvers = []
for uname in ["wangwu", "sunqi", "liuxi"]:
    tok = client.post("/api/v1/auth/login", json={"username": uname, "password": "123456"}).json()
    hh = {"Authorization": f"Bearer {tok['access_token']}"}
    docs = {t["document_id"] for t in client.get("/api/v1/approval-tasks", headers=hh).json()}
    approvers.append((uname, hh, docs))
has_ap = next((hh for _, hh, docs in approvers if 2 in docs), None)
no_ap = next((hh for _, hh, docs in approvers if 2 not in docs), None)
if has_ap and no_ap:
    mr_deny = client.post(f"/api/v1/review-reports/{rep2['report_id']}/manual-reviews",
                          headers=no_ap, json={"review_result": "confirmed", "review_comment": "x"}).status_code
    check("未分配任务的审批人不能复核报告", mr_deny == 403, f"-> {mr_deny}")
    mr_ok = client.post(f"/api/v1/review-reports/{rep2['report_id']}/manual-reviews",
                        headers=has_ap, json={"review_result": "needs_material", "review_comment": "需补充发票"}).status_code
    check("分配任务的审批人能复核报告", mr_ok == 200, f"-> {mr_ok}")
else:
    check("能找到有/无 AP 任务的审批人", False, "负载均衡下未找到对比用户")

# 15. 管理端（用户/角色/系统参数）
admin = client.post("/api/v1/auth/login", json={"username": "admin", "password": "123456"}).json()
ha = {"Authorization": f"Bearer {admin['access_token']}"}
check("管理端用户列表", client.get("/api/v1/admin/users", headers=ha).status_code == 200)
check("管理端角色列表", client.get("/api/v1/admin/roles", headers=ha).status_code == 200)
check("管理端系统参数", client.get("/api/v1/admin/sys-params", headers=ha).status_code == 200)
up = client.patch("/api/v1/admin/sys-params/risk.medium_bump_count", headers=ha, json={"param_value": "4"})
check("修改系统参数", up.status_code == 200 and up.json()["param_value"] == "4")
# 非管理员访问管理端应被拒
check("普通用户访问管理端被拒(403)", client.get("/api/v1/admin/users", headers=h).status_code == 403)
# 恢复参数
client.patch("/api/v1/admin/sys-params/risk.medium_bump_count", headers=ha, json={"param_value": "3"})

# 17. P0-1 明细 API 契约 / P0-2 type_fields / P1-7 workflow 校验 / P0-7 角色SQL / P0-8 审计
ndoc = client.post("/api/v1/documents", headers=h, json={
    "document_type": "company_payment", "applicant_department": "测试部", "budget_department": "测试部",
    "payee_name": "某公司", "payee_account": "A", "expense_category": "服务费",
    "total_amount": "1000", "currency": "CNY", "apply_date": "2026-08-16",
    "type_fields": {"supplier_name": "测试供应商", "contract_no": "C-X",
                    "payment_ratio": 50, "planned_payment_date": "2026-09-01"},
}).json()
ndoc_id = ndoc["id"]
for nm in ["住宿", "餐饮"]:
    client.post(f"/api/v1/documents/{ndoc_id}/line-items", headers=h,
                json={"item_type": "expense", "item_name": nm, "amount": "100"}).json()
items0 = client.get(f"/api/v1/documents/{ndoc_id}", headers=h).json()["line_items"]
check("新增明细为 2 条", len(items0) == 2)
client.patch(f"/api/v1/documents/{ndoc_id}/line-items/{items0[0]['id']}", headers=h, json={"amount": "200"})
client.delete(f"/api/v1/documents/{ndoc_id}/line-items/{items0[1]['id']}", headers=h)
items1 = client.get(f"/api/v1/documents/{ndoc_id}", headers=h).json()["line_items"]
check("删除后明细为 1 条且已更新", len(items1) == 1 and str(items1[0]["amount"]) == "200.00")
tf = client.get(f"/api/v1/documents/{ndoc_id}", headers=h).json()["document"]["type_fields"]
check("type_fields 双向正确", (tf or {}).get("supplier_name") == "测试供应商", str(tf))

wv = client.post("/api/v1/approval-workflows", headers=ha, json={
    "workflow_name": "空流程", "document_type": "expense",
    "match_conditions": {"amount_min": 0}, "nodes": []}).status_code
check("空节点流程被拒(400)", wv == 400, f"-> {wv}")
wv2 = client.post("/api/v1/approval-workflows", headers=ha, json={
    "workflow_name": "非法角色", "document_type": "expense",
    "match_conditions": {"amount_min": 0},
    "nodes": [{"node_name": "x", "approver_role": "bogus", "node_order": 1}]}).status_code
check("非法审批角色被拒(400)", wv2 == 400, f"-> {wv2}")

client.patch("/api/v1/admin/sys-params/attachment.max_size_mb", headers=ha, json={"param_value": "10"})
aud = client.get("/api/v1/audit-logs", headers=ha).json()
check("sys param 更新写审计日志", any(a["action_type"] == "sys_param:update" for a in aud))

# 18. 第四轮：finance/approver 职责 / parse 权限 / magic bytes / category / 规则 config 校验
zh = client.post("/api/v1/auth/login", json={"username": "zhangsan", "password": "123456"}).json()
hz = {"Authorization": f"Bearer {zh['access_token']}"}
zl = client.post("/api/v1/auth/login", json={"username": "zhaoliu", "password": "123456"}).json()
hzf = {"Authorization": f"Bearer {zl['access_token']}"}
lx = client.post("/api/v1/auth/login", json={"username": "liuxi", "password": "123456"}).json()
hlx = {"Authorization": f"Bearer {lx['access_token']}"}

# finance-only 不能正式审批（无 approval:process）
w_tasks = client.get("/api/v1/approval-tasks", headers=hw).json()
if w_tasks:
    deny = client.post(f"/api/v1/approval-tasks/{w_tasks[0]['task_id']}/approve", headers=hzf,
                       json={"review_comment": ""}).status_code
    check("finance-only 不能正式审批(403)", deny == 403, f"-> {deny}")
# finance+approver 用户具备审批能力（liuxi 权限含 approval:process）
lx_me = client.get("/api/v1/auth/me", headers=hlx).json()
check("finance+approver 具备审批权限", "approval:process" in lx_me["permission_codes"])

# parse 权限（P1-8：analysis:create + L2 可见）；lisi 看不到 CP 单据 → L2 拒绝
att0 = detail["attachments"][0]
p_deny = client.post(f"/api/v1/documents/{cp['id']}/attachments/{att0['id']}/parse", headers=hlisi).status_code
check("无数据权限不能触发解析(403)", p_deny == 403, f"-> {p_deny}")
p_ok = client.post(f"/api/v1/documents/{cp['id']}/attachments/{att0['id']}/parse", headers=hw).status_code
check("有 analysis:create + 可见可解析(200)", p_ok == 200, f"-> {p_ok}")

# magic bytes：PNG 内容伪装成 .pdf → 400
import io
png_magic = b"\x89PNG\r\n\x1a\n" + b"x" * 32
fd = {"file": ("fake.pdf", io.BytesIO(png_magic), "application/pdf")}
r = client.post(f"/api/v1/documents/{ndoc_id}/attachments", headers=h, files=fd)
check("伪装扩展名上传被拒(400)", r.status_code == 400, f"-> {r.status_code}")

# 用户指定 document_category 生效
r2 = client.post(
    f"/api/v1/documents/{ndoc_id}/attachments?document_category=contract",
    headers=h,
    files={"file": ("随便.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"y" * 32), "image/png")},
)
check("指定 document_category 生效", r2.status_code == 200 and r2.json().get("document_category") == "contract",
      r2.text[:80])

# 非法 risk rule config 保存失败
rules = client.get("/api/v1/rules", headers=hzf).json()
r_bad = client.patch(f"/api/v1/rules/{rules[0]['id']}", headers=hzf, json={
    "rule_code": rules[0]["rule_code"], "rule_name": rules[0]["rule_name"],
    "enabled": True, "config": {"tolerance_pct": "abc"},
}).status_code
check("非法规则 config 被拒(400)", r_bad == 400, f"-> {r_bad}")

# 19. 角色权限更新 SQL（select().delete() 修复）——放最后，避免 clobber 影响前面 applicant 操作
rp = client.patch("/api/v1/admin/roles/1/permissions", headers=ha,
                  json={"permission_codes": ["document:view"]}).status_code
check("角色权限更新 SQL 正常", rp == 200, f"-> {rp}")

print("\n==== 结果:", len(OK), "PASS /", len(FAIL), "FAIL ====")
sys.exit(1 if FAIL else 0)
