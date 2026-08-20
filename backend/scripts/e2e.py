# -*- coding: utf-8 -*-
"""五类单据完整 E2E：create → 明细 → 附件 → 提交 → 解析 → 分析 → 报告 → 审批 → 导出。

前提：已跑过 seed（角色/流程/规则/用户）。按 DATABASE_URL 连接（SQLite 或 MySQL）。
确定性：解析走 preset 预制（脚本写入 preset_parse，不依赖 OCR key）；报告润色有 LLM key 则真调，无 key 自动降级。
"""
import io
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app

client = TestClient(app)
OK, FAIL = [], []


def check(name, cond, extra=""):
    (OK if cond else FAIL).append(name)
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {extra}")


def _login(u):
    return client.post("/api/v1/auth/login", json={"username": u, "password": "123456"}).json()


h_app = {"Authorization": f"Bearer {_login('zhangsan')['access_token']}"}
APPROVERS = ["wangwu", "sunqi", "liuxi"]
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PRESET_DIR = Path(settings.preset_parse_dir)


def _write_preset(file_name, category, fields):
    (PRESET_DIR / f"{Path(file_name).stem}.json").write_text(
        json.dumps({"category": category, "fields": fields, "confidence": 0.98},
                   ensure_ascii=False), encoding="utf-8")


def _upload(doc_id, file_name, category, fields):
    _write_preset(file_name, category, fields)
    r = client.post(f"/api/v1/documents/{doc_id}/attachments?document_category={category}",
                    headers=h_app, files={"file": (file_name, io.BytesIO(PNG), "image/png")})
    assert r.status_code == 200, r.text


def _submit_and_analyze(doc_id):
    r = client.post(f"/api/v1/documents/{doc_id}/submit", headers=h_app)
    assert r.status_code == 200, r.text
    task_id = client.get(f"/api/v1/documents/{doc_id}/analysis/latest", headers=h_app).json()["task_id"]
    deadline = time.time() + 60
    while time.time() < deadline:
        st = client.get(f"/api/v1/analysis-tasks/{task_id}", headers=h_app).json()
        if st["task_status"] in ("succeeded", "failed"):
            return st
        time.sleep(0.5)
    return {"task_status": "timeout"}


def _approve_through(doc_id):
    deadline = time.time() + 40
    while time.time() < deadline:
        d = client.get(f"/api/v1/documents/{doc_id}", headers=h_app).json()
        if d["document"]["document_status"] == "approved":
            return True
        advanced = False
        for uname in APPROVERS:
            hh = {"Authorization": f"Bearer {_login(uname)['access_token']}"}
            for t in client.get("/api/v1/approval-tasks", headers=hh).json():
                if t["document_id"] == doc_id:
                    client.post(f"/api/v1/approval-tasks/{t['task_id']}/approve", headers=hh,
                                json={"review_comment": "E2E 通过"})
                    advanced = True
                    break
            if advanced:
                break
        if not advanced:
            return False
    return False


DOC_TYPES = {
    "company_payment": {
        "payee": "恒通科技有限公司", "category": "服务费", "amount": "10000",
        "type_fields": {"supplier_name": "恒通科技有限公司", "contract_no": "E-C1",
                        "payment_ratio": 50, "payment_terms": "首付50%", "planned_payment_date": "2026-09-01"},
        "line_items": [("服务费", "10000")],
        "attachments": [
            ("发票_E2E.png", "invoice", {"invoice_no": "E-INV1", "amount_including_tax": 10000, "tax_amount": 1150.44}),
            ("合同_E2E.png", "contract", {"contract_no": "E-C1", "party_b": "恒通科技有限公司",
                                          "contract_amount": 20000, "payment_ratio": 50})],
    },
    "advance_payment": {
        "payee": "预付供应商", "category": "采购", "amount": "5000",
        "type_fields": {"supplier_name": "预付供应商", "contract_no": "E-C2",
                        "payment_ratio": 30, "payment_terms": "预付30%", "planned_payment_date": "2026-09-05"},
        "line_items": [],
        "attachments": [("合同_预付E2E.png", "contract",
                         {"contract_no": "E-C2", "party_b": "预付供应商", "contract_amount": 16000, "payment_ratio": 30})],
    },
    "batch_payment": {
        "payee": "多供应商", "category": "采购", "amount": "6000",
        "type_fields": {"payment_count": 2},
        "line_items": [("甲供应商", "3000"), ("乙供应商", "3000")],
        "attachments": [("回单_批量E2E.png", "payment_basis", {"batch_total": 6000, "count": 2})],
    },
    "expense": {
        "payee": "个人垫付", "category": "差旅", "amount": "1500",
        "type_fields": {},
        "line_items": [("商务酒店", "800"), ("市内交通", "700")],
        "attachments": [("发票_费用E2E.png", "invoice",
                         {"invoice_no": "E-INV2", "amount_including_tax": 1500, "tax_amount": 84.9})],
    },
    "travel": {
        "payee": "个人垫付", "category": "差旅", "amount": "3000",
        "type_fields": {"travel_destination": "上海", "travel_start": "2026-08-10",
                        "travel_end": "2026-08-12", "transport_fee": 1500,
                        "hotel_fee": 800, "meal_fee": 500, "allowance": 200},
        "line_items": [("机票", "1500"), ("商务酒店", "800"), ("餐饮", "500"), ("补贴", "200")],
        "attachments": [
            ("发票_机票E2E.png", "invoice", {"invoice_no": "E-INV3", "amount_including_tax": 3000, "tax_amount": 169.8}),
            ("行程_出差E2E.png", "itinerary", {})],
    },
}


def main():
    for dtype, cfg in DOC_TYPES.items():
        body = {
            "document_type": dtype, "applicant_department": "E2E部", "budget_department": "E2E部",
            "payee_name": cfg["payee"], "payee_account": "ACCT-E2E",
            "expense_category": cfg["category"], "total_amount": cfg["amount"],
            "currency": "CNY", "apply_date": "2026-08-17", "type_fields": cfg["type_fields"],
        }
        doc = client.post("/api/v1/documents", headers=h_app, json=body).json()
        doc_id = doc["id"]
        for (name, amt) in cfg["line_items"]:
            it = {"item_type": "payment" if dtype == "batch_payment" else "expense",
                  "item_name": name, "amount": amt, "remark": name if dtype == "batch_payment" else None}
            client.post(f"/api/v1/documents/{doc_id}/line-items", headers=h_app, json=it)
        for (fname, cat, fields) in cfg["attachments"]:
            _upload(doc_id, fname, cat, fields)

        st = _submit_and_analyze(doc_id)
        check(f"{dtype} 分析完成", st["task_status"] == "succeeded", st["task_status"])
        if st["task_status"] == "succeeded":
            latest = client.get(f"/api/v1/documents/{doc_id}/analysis/latest", headers=h_app).json()
            rep = latest["report"]
            check(f"{dtype} 报告生成", rep is not None)
            if rep:
                exp = client.get(f"/api/v1/review-reports/{rep['report_id']}/export", headers=h_app)
                check(f"{dtype} 报告导出HTML", exp.status_code == 200)
        check(f"{dtype} 审批通过", _approve_through(doc_id))

    print(f"\n==== E2E 结果: {len(OK)} PASS / {len(FAIL)} FAIL ====")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
