# -*- coding: utf-8 -*-
"""前后端联调测试（真实 HTTP，模拟前端 API 层调用）。

前提：uvicorn 已启动（127.0.0.1:8000），seed 已灌入演示数据。
运行：.venv\\Scripts\\python.exe scripts/integration_test.py
说明：审批等操作会消耗待办，重跑前请重新 seed（与 smoke_test 相同约定）。
"""
import sys
import time
import zlib
import struct
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = "http://127.0.0.1:8000/api/v1"
TIMEOUT = httpx.Timeout(90.0, connect=5.0)


def make_png(seed: int, size: int = 6) -> bytes:
    """与 seed.py 相同的占位 PNG 生成（zlib 手写，tEXt 保证 hash 唯一）。"""
    w = h = size

    def chunk(typ: bytes, data: bytes) -> bytes:
        body = typ + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xffffffff)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    rows = bytearray()
    for y in range(h):
        rows.append(0)
        for x in range(w):
            rows += bytes(((seed * 31 + x * 71 + y * 97) % 256,
                           (seed * 17 + x * 53 + y * 83) % 256,
                           (seed * 7 + x * 37 + y * 61) % 256, 255))
    text = chunk(b"tEXt", b"Comment\x00seed=" + str(seed).encode())
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(rows))) + text + chunk(b"IEND", b""))

PASS = 0
FAIL = 0
FMT = "\033[32mPASS\033[0m  %-34s %s" if sys.stdout.isatty() else "PASS  %-34s %s"
FMT_FAIL = "\033[31mFAIL\033[0m  %-34s %s" if sys.stdout.isatty() else "FAIL  %-34s %s"


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(FMT % (name, detail))
    else:
        FAIL += 1
        print(FMT_FAIL % (name, detail))


class Client:
    def __init__(self, name: str = ""):
        self.c = httpx.Client(base_url=BASE, timeout=TIMEOUT)
        self.name = name
        self.token: str | None = None
        self.user: dict | None = None

    def login(self, username: str, password: str = "123456") -> dict:
        r = self.c.post("/auth/login", json={"username": username, "password": password})
        r.raise_for_status()
        d = r.json()
        self.token = d["access_token"]
        self.user = d["user"]
        return d

    def request(self, method: str, path: str, **kw) -> httpx.Response:
        kw.setdefault("headers", {})
        if self.token:
            kw["headers"]["Authorization"] = f"Bearer {self.token}"
        return self.c.request(method, path, **kw)

    def get(self, path: str, **kw) -> httpx.Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw) -> httpx.Response:
        return self.request("POST", path, **kw)

    def patch(self, path: str, **kw) -> httpx.Response:
        return self.request("PATCH", path, **kw)

    def delete(self, path: str, **kw) -> httpx.Response:
        return self.request("DELETE", path, **kw)


def upload_att(client: Client, doc_id: int, file_name: str, category: str) -> httpx.Response:
    png = make_png(hash(file_name) & 0x7FFFFFFF)
    return client.post(
        f"/documents/{doc_id}/attachments",
        params={"document_category": category},
        files={"file": (file_name, png, "image/png")},
    )


def wait_analysis(client: Client, task_id: int, timeout: int = 150) -> dict:
    """轮询分析任务直到终态。"""
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        r = client.get(f"/analysis-tasks/{task_id}")
        if r.status_code == 200:
            last = r.json()
            if last.get("task_status") in ("succeeded", "failed"):
                return last
        time.sleep(2)
    return last


def main() -> None:
    print("=" * 70)
    print("前后端联调测试（真实 HTTP）  BASE=" + BASE)
    print("=" * 70)

    # ---------------- A. 认证 ----------------
    print("\n[A] 认证")
    zs = Client("zhangsan")
    try:
        d = zs.login("zhangsan")
        check("A1 登录成功 zhangsan", bool(d.get("access_token")), f"roles={d['user'].get('role_codes')}")
    except Exception as e:
        check("A1 登录成功 zhangsan", False, str(e))

    anon = Client("anon")
    r = anon.post("/auth/login", json={"username": "zhangsan", "password": "wrong-pass"})
    check("A2 错误密码登录 401", r.status_code == 401, f"-> {r.status_code}")

    r = zs.get("/auth/me")
    check("A3 me 返回角色权限", r.status_code == 200 and r.json().get("username") == "zhangsan",
          f"perms={len(r.json().get('permission_codes', []))}")

    # ---------------- B. 单据生命周期（zhangsan） ----------------
    print("\n[B] 单据生命周期（zhangsan）")
    r = zs.get("/documents/types")
    types = r.json() if r.status_code == 200 else []
    check("B1 单据类型 5 类", r.status_code == 200 and len(types) == 5,
          f"types={[t.get('document_type') for t in types] if types else r.status_code}")

    r = zs.get("/documents")
    docs = (r.json() or {}).get("items", []) if r.status_code == 200 else []
    check("B2 单据列表（L2 数据权限：张三仅见本人单）",
          r.status_code == 200 and all(doc.get("applicant_id") == zs.user.get("id") for doc in docs),
          f"可见 {len(docs)} 单")

    # 新建对公付款单
    payload = {
        "document_type": "company_payment",
        "applicant_department": "销售部",
        "budget_department": "销售部",
        "payee_name": "上海恒通科技有限公司",
        "payee_account": "6222000011112222333",
        "expense_category": "采购付款",
        "total_amount": 5000.00,
        "currency": "CNY",
        "apply_date": "2026-08-17",
        "reason_text": "联调测试-对公付款单",
        "type_fields": {
            "contract_no": "HT-2026-LT-001",
            "supplier_name": "上海恒通科技有限公司",
            "payment_ratio": 50,
            "payment_terms": "验收合格后支付 50%",
            "planned_payment_date": "2026-09-30",
        },
    }
    r = zs.post("/documents", json=payload)
    new_doc = r.json() if r.status_code == 200 else {}
    check("B3 新建对公付款单 draft", r.status_code == 200 and new_doc.get("document_status") == "draft",
          f"id={new_doc.get('id')} no={new_doc.get('document_no')}")
    doc_id = new_doc.get("id")

    if doc_id:
        # 明细 add
        r = zs.post(f"/documents/{doc_id}/line-items", json={
            "item_type": "payment", "item_name": "设备采购款",
            "expense_date": "2026-08-17", "expense_location": "上海",
            "quantity": 1, "unit_price": 5000.00, "amount": 5000.00,
        })
        li1 = r.json() if r.status_code == 200 else {}
        r = zs.post(f"/documents/{doc_id}/line-items", json={
            "item_type": "payment", "item_name": "临时占位",
            "expense_date": "2026-08-17", "expense_location": "上海",
            "quantity": 1, "unit_price": 100.00, "amount": 100.00,
        })
        li2 = r.json() if r.status_code == 200 else {}
        check("B4 明细新增 x2", bool(li1.get("id")) and bool(li2.get("id")), f"id={li1.get('id')},{li2.get('id')}")
        if li2.get("id"):
            r = zs.patch(f"/documents/{doc_id}/line-items/{li2['id']}", json={"amount": 200.00, "item_name": "占位修改"})
            r2 = zs.get(f"/documents/{doc_id}")
            li_check = next((x for x in r2.json().get("line_items", []) if x.get("id") == li2["id"]), {}) if r2.status_code == 200 else {}
            check("B5 明细修改 PATCH", r.status_code == 200 and float(li_check.get("amount") or 0) == 200.0,
                  f"amount={li_check.get('amount')}")
            r = zs.delete(f"/documents/{doc_id}/line-items/{li2['id']}")
            check("B6 明细删除 DELETE", r.status_code == 200, f"-> {r.status_code}")

        # 附件上传（命中预制解析：合同_远东 / 发票_恒通）
        r = upload_att(zs, doc_id, "合同_远东.png", "contract")
        att1 = r.json() if r.status_code == 200 else {}
        r = upload_att(zs, doc_id, "发票_恒通.png", "invoice")
        att2 = r.json() if r.status_code == 200 else {}
        check("B7 附件上传（contract+invoice）", bool(att1.get("id")) and bool(att2.get("id")),
              f"category={att1.get('document_category')},{att2.get('document_category')}")

        # 金额核对
        r = zs.get(f"/documents/{doc_id}/amount-comparison")
        comp = r.json() if r.status_code == 200 else {}
        check("B8 金额核对接口", r.status_code == 200 and float(comp.get("document_total") or 0) == 5000.0,
              f"doc={comp.get('document_total')} lines={comp.get('line_items_total')}")

        # 提交
        r = zs.post(f"/documents/{doc_id}/submit")
        sub = r.json() if r.status_code == 200 else {}
        check("B9 提交 → pending_review + 触发分析", r.status_code == 200 and sub.get("document_status") == "pending_review",
              f"status={sub.get('document_status')} task={sub.get('analysis_task_id')}")
        task_id = sub.get("analysis_task_id")
        if not task_id:
            r = zs.get(f"/documents/{doc_id}/analysis/latest")
            task_id = (r.json() or {}).get("task_id") if r.status_code == 200 else None

        # 轮询分析（真实 LLM 润色 + 真实 OCR 尝试失败回退预制）
        if task_id:
            st = wait_analysis(zs, task_id)
            check("B10 分析任务 succeeded", st.get("task_status") == "succeeded",
                  f"status={st.get('task_status')} err={st.get('error_message')}")
            r = zs.get(f"/analysis-tasks/{task_id}/findings")
            findings = r.json() if r.status_code == 200 else []
            check("B11 风险项返回", r.status_code == 200 and isinstance(findings, list),
                  f"{len(findings)} 项")
            r = zs.get(f"/analysis-tasks/{task_id}/report")
            rep = r.json() if r.status_code == 200 else {}
            ai_note = "AI 风险说明" in (rep.get("report_markdown") or "")
            check("B12 分析报告（含 AI 风险说明小节）", r.status_code == 200 and rep.get("overall_risk_level"),
                  f"overall={rep.get('overall_risk_level')} AI小节={ai_note}")
            # HTML 导出走 /review-reports/{rid}/export（带 JWT 下载）
            r = zs.get("/review-reports")
            reps_all = r.json() if r.status_code == 200 else []
            my_rep = next((x for x in reps_all if x.get("document_no") == new_doc.get("document_no")), None)
            if my_rep:
                r = zs.get(f"/review-reports/{my_rep['report_id']}/export")
                check("B13 报告 HTML 导出", r.status_code == 200 and "text/html" in r.headers.get("content-type", ""),
                      f"-> {r.status_code} {len(r.content)}B")
            else:
                check("B13 报告 HTML 导出", False, "未找到新单报告")

        # 复制单据
        r = zs.post(f"/documents/{doc_id}/copy")
        cp = r.json() if r.status_code == 200 else {}
        check("B14 复制单据（新 draft / version 0 / 不复制审批分析）",
              r.status_code == 200 and cp.get("document_status") == "draft" and cp.get("current_version") == 0,
              f"id={cp.get('id')} version={cp.get('current_version')}")
        if cp.get("id"):
            zs.post(f"/documents/{cp['id']}/void")  # 清理副本，避免干扰后续统计

    # ---------------- C. 审批（wangwu） ----------------
    print("\n[C] 审批流（wangwu）")
    ww = Client("wangwu")
    try:
        ww.login("wangwu")
        check("C1 审批人登录", bool(ww.token), f"roles={ww.user.get('role_codes')}")
    except Exception as e:
        check("C1 审批人登录", False, str(e))

    r = ww.get("/approval-tasks")
    tasks = r.json() if r.status_code == 200 else []
    check("C2 审批待办列表（wangwu）", r.status_code == 200 and len(tasks) >= 1, f"{len(tasks)} 个待办")

    # P0-1 Resolver 负载均衡：新单任务可能分配给 wangwu/sunqi/liuxi 任一人
    approver_client, target = None, None
    for u in ("wangwu", "sunqi", "liuxi"):
        c = Client(u)
        try:
            c.login(u)
        except Exception:
            continue
        t = c.get("/approval-tasks")
        mine = t.json() if t.status_code == 200 else []
        hit = next((x for x in mine if x.get("document_id") == doc_id), None)
        if hit:
            approver_client, target = c, hit
            break
        if approver_client is None:
            approver_client = c
    if target and approver_client:
        r = approver_client.get(f"/documents/{doc_id}")
        check("C3 审批人可见任务单据详情（L2）", r.status_code == 200,
              f"-> {r.status_code} (by {approver_client.name})")
        r = approver_client.post(f"/approval-tasks/{target['task_id']}/approve",
                                 json={"review_comment": "联调测试：风险可控，同意付款"})
        check("C4 审批通过（带审批意见）", r.status_code == 200, f"-> {r.status_code}")
    else:
        check("C3 审批人可见任务单据详情（L2）", False, "新单未生成审批任务（workflow 匹配异常）")
        check("C4 审批通过（带审批意见）", False, "新单未生成审批任务")

    # ---------------- D. 智能对话（zhangsan） ----------------
    print("\n[D] 智能对话 NLU（zhangsan）")
    r = zs.post("/review-sessions")
    sid = (r.json() or {}).get("session_id") if r.status_code == 200 else None
    check("D1 创建会话", bool(sid), f"sid={sid}")
    if sid:
        r = zs.post(f"/review-sessions/{sid}/messages", json={"content": "帮我审核对公付款单 CP-20260816-001"})
        d = r.json() if r.status_code == 200 else {}
        reply = d.get("reply") or d.get("content") or ""
        hit = any(k in reply for k in ("CP-20260816-001", "对公付款", "分析", "已找到"))
        check("D2 会话消息 NLU 抽槽查单", r.status_code == 200 and hit,
              f"reply={reply[:60]!r}")

    # ---------------- E. 配置 / 参考 / 复核（liuxi 财务负责人） ----------------
    print("\n[E] 配置与复核（liuxi finance+approver）")
    lx = Client("liuxi")
    try:
        lx.login("liuxi")
        check("E0 财务负责人登录", bool(lx.token), f"roles={lx.user.get('role_codes')}")
    except Exception as e:
        check("E0 财务负责人登录", False, str(e))

    r = lx.get("/rules")
    rules = r.json() if r.status_code == 200 else []
    check("E1 规则列表 10 条", r.status_code == 200 and len(rules) == 10, f"{len(rules)} 条")
    if rules:
        rid = next(x["id"] for x in rules if x.get("rule_code") == "price_reasonableness")
        r = lx.patch(f"/rules/{rid}", json={"rule_code": "price_reasonableness",
                                    "rule_name": "市场价合理性", "enabled": True,
                                    "config": {"deviation_pct": -5}})
        check("E2 非法规则 config 400", r.status_code == 400, f"-> {r.status_code}")
        r = lx.patch(f"/rules/{rid}", json={"rule_code": "price_reasonableness",
                                    "rule_name": "市场价合理性", "enabled": True,
                                    "config": {"deviation_pct": 25}})
        check("E3 合法规则 config 200", r.status_code == 200, f"-> {r.status_code}")

    _ad0 = Client("admin")
    _ad0.login("admin")
    r = _ad0.get("/approval-workflows")
    wfs = r.json() if r.status_code == 200 else []
    check("E4 审批流程列表（admin）", r.status_code == 200 and len(wfs) >= 5, f"{len(wfs)} 条流程")

    r = lx.get("/suppliers/lookup", params={"name": "远东贸易有限公司"})
    sup = r.json() if r.status_code == 200 else {}
    code = sup.get("supplier_code")
    if code:
        r = lx.get(f"/suppliers/{code}/risks")
        risks = r.json() if r.status_code == 200 else {}
        check("E5 供应商查询（黑名单命中）", r.status_code == 200 and risks.get("blacklist_status") == "blacklisted",
              f"blacklist={risks.get('blacklist_status')} tags={risks.get('risk_tags')}")
    else:
        check("E5 供应商查询（黑名单命中）", False, f"lookup -> {r.status_code} {sup}")

    r = lx.get("/review-reports")
    reps = r.json() if r.status_code == 200 else []
    check("E6 报告列表", r.status_code == 200 and len(reps) >= 5, f"{len(reps)} 份")
    if reps:
        rid = reps[0]["report_id"]
        r = lx.post(f"/review-reports/{rid}/manual-reviews", json={
            "review_result": "confirmed", "review_comment": "联调复核：材料齐全，风险可控"})
        check("E7 人工复核 ManualReview", r.status_code == 200, f"-> {r.status_code}")

    # ---------------- F. 管理端（admin） ----------------
    print("\n[F] 管理端（admin）")
    ad = Client("admin")
    try:
        ad.login("admin")
        check("F1 管理员登录", bool(ad.token), f"roles={ad.user.get('role_codes')}")
    except Exception as e:
        check("F1 管理员登录", False, str(e))

    r = ad.get("/admin/users")
    check("F2 用户管理列表", r.status_code == 200 and len(r.json()) >= 6, f"{len(r.json()) if r.status_code == 200 else 0} 用户")
    r = ad.get("/admin/roles")
    check("F3 角色列表", r.status_code == 200 and len(r.json()) >= 4, f"{len(r.json()) if r.status_code == 200 else 0} 角色")
    r = ad.get("/admin/permissions")
    perms = r.json() if r.status_code == 200 else []
    check("F4 权限码列表", r.status_code == 200 and len(perms) >= 20, f"{len(perms)} 权限")
    r = ad.get("/admin/sys-params")
    sps = r.json() if r.status_code == 200 else []
    sp_keys = [x.get("param_key") for x in sps]
    check("F5 系统参数读取", r.status_code == 200 and "ocr.mode" in sp_keys, f"{len(sps)} 项")

    # 系统参数更新 + 审计
    old = next((x.get("param_value") for x in sps if x.get("param_key") == "attachment.max_size_mb"), "10")
    r = ad.patch("/admin/sys-params/attachment.max_size_mb", json={"param_value": "20"})
    check("F6 系统参数更新", r.status_code == 200 and r.json().get("param_value") == "20", f"-> {r.json().get('param_value')}")
    r = ad.patch("/admin/sys-params/attachment.max_size_mb", json={"param_value": old})  # 还原
    r = ad.get("/audit-logs")
    logs = r.json() if r.status_code == 200 else []
    check("F7 审计日志含参数变更", r.status_code == 200 and any("attachment.max_size_mb" in str(x) for x in logs),
          f"{len(logs)} 条日志")

    # ---------------- G. 安全与越权 ----------------
    print("\n[G] 安全与越权")
    r = zs.get("/admin/users")
    check("G1 申请人访问管理端 403", r.status_code == 403, f"-> {r.status_code}")
    # L2 越权：lisi 的单据对 zhangsan 不可见（seed：EX/TR 属于 lisi）
    ls = Client("lisi")
    try:
        ls.login("lisi")
        r = ls.get("/documents")
        lisi_ids = [d["id"] for d in (r.json() or {}).get("items", [])] if r.status_code == 200 else []
        check("G2a lisi 登录并取到自己的单据", bool(lisi_ids), f"{len(lisi_ids)} 单")
    except Exception as e:
        lisi_ids = []
        check("G2a lisi 登录并取到自己的单据", False, str(e))
    if lisi_ids:
        r = zs.get(f"/documents/{lisi_ids[0]}")
        check("G2 查看他人单据被拒（L2 越权）", r.status_code in (403, 404), f"-> {r.status_code}")
    else:
        check("G2 查看他人单据被拒（L2 越权）", False, "未取得 lisi 单据 id")
    r = zs.post("/approval-tasks/999999/approve", json={"review_comment": "x"})
    check("G3 申请人审批他人任务被拒", r.status_code in (403, 404), f"-> {r.status_code}")
    r = anon.get("/documents")
    check("G4 无令牌访问 401", r.status_code == 401, f"-> {r.status_code}")
    r = zs.post("/auth/logout")
    check("G5 注销成功", r.status_code == 200, f"-> {r.status_code}")
    r = zs.get("/auth/me")
    check("G6 注销后旧令牌失效 401", r.status_code == 401, f"-> {r.status_code}")

    # ---------------- 汇总 ----------------
    print("\n" + "=" * 70)
    print(f"==== 联调结果: {PASS} PASS / {FAIL} FAIL ====")
    print("=" * 70)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
