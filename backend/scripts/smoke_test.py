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

# 8. 高风险单据的报告（审批人可看）
wangwu = client.post("/api/v1/auth/login", json={"username": "wangwu", "password": "123456"}).json()
hw = {"Authorization": f"Bearer {wangwu['access_token']}"}
t2 = client.get("/api/v1/analysis-tasks/2/report", headers=hw).json()
check("AP 报告整体风险=high", t2["overall_risk_level"] == "high", t2["overall_risk_level"])
check("AP 含供应商黑名单风险项",
      any("黑名单" in f["risk_title"] for f in client.get("/api/v1/analysis-tasks/2/findings", headers=hw).json()))

# 9. 审批待办与通过
tasks = client.get("/api/v1/approval-tasks", headers=hw).json()
check("审批人 wangwu 有待办", len(tasks) >= 1, f"count={len(tasks)}")
first = tasks[0]
approve = client.post(f"/api/v1/approval-tasks/{first['task_id']}/approve", headers=hw)
check("审批通过接口", approve.status_code == 200, approve.json())

# 10. 报告导出
exp = client.get(f"/api/v1/review-reports/{t2['report_id']}/export", headers=hw)
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

# 14. 管理端（用户/角色/系统参数）
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

print("\n==== 结果:", len(OK), "PASS /", len(FAIL), "FAIL ====")
sys.exit(1 if FAIL else 0)
