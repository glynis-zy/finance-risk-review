# -*- coding: utf-8 -*-
"""演示数据生成（规格 2.7.15 验收链路）。

用法（在 backend/ 目录下）：
    python scripts/seed.py
会清空并重建全表，灌入：角色/权限/用户、审批流程、10 条规则、参考数据、
5 类示例单据 + 附件(占位PNG) + 预制解析结果，并提交部分单据触发分析。
"""
import hashlib
import json
import struct
import sys
import time
import uuid
import zlib
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.session import SessionLocal, init_db  # noqa: E402
from app.models.analysis import (  # noqa: E402
    AnalysisTask, ManualReview, ReviewReport, RiskFinding,
)
from app.models.attachment import (  # noqa: E402
    AttachmentParseResult, DocumentAttachment, InvoiceRecord,
)
from app.models.audit import AuditLog  # noqa: E402
from app.models.document import (  # noqa: E402
    DocumentLineItem, DocumentStatusLog, DocumentVersion, FinancialDocument,
)
from app.models.reference import (  # noqa: E402
    ExpenseStandard, MarketPriceReference, RiskRule, SupplierProfile, SysParam,
)
from app.models.session import ReviewSession, SessionMessage  # noqa: E402
from app.models.user import Permission, Role, RolePermission, User, UserRole  # noqa: E402
from app.models.workflow import (  # noqa: E402
    ApprovalInstance, ApprovalTask, ApprovalWorkflow, ApprovalWorkflowNode,
)
from app.services import document_service, analysis_service  # noqa: E402

PRESET_DIR = Path(settings.preset_parse_dir)
UPLOAD_DIR = Path(settings.file_storage_path)


def make_png(seed: int, size: int = 6) -> bytes:
    """用 zlib 手写一个合法 RGBA PNG（无 PIL 依赖）。

    每个附件额外携带一个 tEXt 块（内含 seed），保证不同 seed 的字节必然不同，
    从而 file_hash 各异，避免"重复文件"规则误报。
    """
    w = h = size

    def chunk(typ: bytes, data: bytes) -> bytes:
        body = typ + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xffffffff)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)  # 8bit RGBA
    rows = bytearray()
    for y in range(h):
        rows.append(0)  # filter: none
        for x in range(w):
            rows += bytes((
                (seed * 31 + x * 71 + y * 97) % 256,
                (seed * 17 + x * 53 + y * 83) % 256,
                (seed * 7 + x * 37 + y * 61) % 256,
                255,
            ))
    text = chunk(b"tEXt", b"Comment\x00seed=" + str(seed).encode())
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(rows))) + text + chunk(b"IEND", b""))

ALL_PERMISSIONS = [
    ("document:view", "查看单据", "document", "view"),
    ("document:create", "创建单据", "document", "create"),
    ("document:edit", "编辑单据", "document", "edit"),
    ("document:submit", "提交单据", "document", "submit"),
    ("document:withdraw", "撤回单据", "document", "withdraw"),
    ("document:void", "作废单据", "document", "void"),
    ("approval:view", "查看审批任务", "approval", "view"),
    ("approval:process", "处理审批任务", "approval", "process"),
    ("analysis:view", "查看分析结果", "analysis", "view"),
    ("analysis:create", "发起分析", "analysis", "create"),
    ("analysis:review", "复核风险项", "analysis", "review"),
    ("session:chat", "智能审核对话", "session", "chat"),
    ("rule:view", "查看规则", "rule", "view"),
    ("rule:manage", "维护规则", "rule", "manage"),
    ("workflow:view", "查看流程", "workflow", "view"),
    ("workflow:manage", "维护流程", "workflow", "manage"),
    ("supplier:view", "查看供应商", "supplier", "view"),
    ("audit:view", "查看审计日志", "audit", "view"),
    ("user:manage", "管理用户", "user", "manage"),
    ("role:manage", "管理角色权限", "role", "manage"),
    ("system:manage", "管理系统参数", "system", "manage"),
]

ROLE_PERMS = {
    "applicant": ["document:view", "document:create", "document:edit", "document:submit",
                  "document:withdraw", "document:void", "session:chat",
                  "analysis:view", "analysis:create"],
    "approver": ["document:view", "approval:view", "approval:process",
                 "analysis:view", "analysis:create", "supplier:view"],
    "finance": ["document:view", "analysis:view", "analysis:create", "analysis:review",
                "rule:view", "rule:manage", "supplier:view", "audit:view"],
    "admin": ALL_PERMISSIONS,
}

USERS = [
    ("zhangsan", "张三", "applicant"),
    ("lisi", "李四", "applicant"),
    ("wangwu", "王五", "approver"),
    ("zhaoliu", "赵六", "finance"),
    ("admin", "管理员", "admin"),
]

RULE_CONFIGS = {
    "invoice_amount_consistency": {"tolerance_pct": 0.5},
    "line_items_total": {"tolerance_pct": 0.5},
    "contract_payment_consistency": {"tolerance_pct": 0.5, "ratio_gap": 10},
    "batch_payment_consistency": {"tolerance_pct": 0.5},
    "expense_policy_compliance": {"exceed_pct": 20},
    "price_reasonableness": {"deviation_pct": 20},
    "spend_anomaly": {"history_spike_ratio": 3.0},
    "supplier_risk": {},
    "attachment_completeness": {"confidence_threshold": 0.8},
    "duplicate_invoice": {},
}


def wipe(db) -> None:
    """按依赖倒序清空全部表。"""
    tables = [SessionMessage, ReviewSession, ManualReview, ReviewReport, RiskFinding,
              AnalysisTask, ApprovalTask, ApprovalInstance, ApprovalWorkflowNode,
              ApprovalWorkflow, InvoiceRecord, AttachmentParseResult, DocumentAttachment,
              DocumentStatusLog, DocumentVersion, DocumentLineItem, FinancialDocument,
              ExpenseStandard, MarketPriceReference, SupplierProfile, RiskRule,
              AuditLog, RolePermission, UserRole, Permission, Role, User]
    for t in tables:
        db.execute(delete(t))
    db.commit()


def seed_identity(db) -> dict[str, User]:
    for code, name, rtype, action in ALL_PERMISSIONS:
        db.add(Permission(permission_code=code, permission_name=name,
                          resource_type=rtype, action_type=action))
    roles = {}
    for code, name in [("applicant", "单据申请人"), ("approver", "审批人员"),
                       ("finance", "财务人员"), ("admin", "系统管理员")]:
        roles[code] = Role(role_code=code, role_name=name)
        db.add(roles[code])
    db.flush()
    for role_code, codes in ROLE_PERMS.items():
        role = roles[role_code]
        for (code, *_rest) in ALL_PERMISSIONS:
            perm = db.scalar(select(Permission).where(Permission.permission_code == code))
            if role_code == "admin" or code in codes:
                db.add(RolePermission(role_id=role.id, permission_id=perm.id))
    users = {}
    for username, display, role_code in USERS:
        u = User(username=username, display_name=display,
                 password_hash=hash_password("123456"), status="active")
        db.add(u)
        db.flush()
        db.add(UserRole(user_id=u.id, role_id=roles[role_code].id))
        users[username] = u
    return users


def seed_workflows(db) -> None:
    wf_defs = [
        ("对公付款审批", "company_payment", {"amount_min": 0},
         [("部门主管审批", "approver", 1), ("财务复核", "finance", 2)]),
        ("预付审批", "advance_payment", {"amount_min": 0},
         [("部门主管审批", "approver", 1), ("财务复核", "finance", 2)]),
        ("批量付款复核", "batch_payment", {"amount_min": 0},
         [("财务复核", "finance", 1)]),
        ("费用报销审批", "expense", {"amount_min": 0},
         [("部门主管审批", "approver", 1)]),
        ("差旅报销审批", "travel", {"amount_min": 0},
         [("部门主管审批", "approver", 1)]),
    ]
    for name, dtype, cond, nodes in wf_defs:
        wf = ApprovalWorkflow(workflow_name=name, document_type=dtype,
                              match_conditions_json=cond, status="active")
        db.add(wf)
        db.flush()
        for nname, role, order in nodes:
            db.add(ApprovalWorkflowNode(workflow_id=wf.id, node_name=nname,
                                        approver_role=role, node_order=order))


def seed_sys_params(db) -> None:
    from app.services.sysparam_service import DEFAULTS
    for key, (val, desc) in DEFAULTS.items():
        db.add(SysParam(param_key=key, param_value=val, description=desc))


def seed_rules_and_reference(db) -> None:
    for code, cfg in RULE_CONFIGS.items():
        db.add(RiskRule(rule_code=code, rule_name=code, enabled=True,
                        config_json=cfg, applies_to_json=None))
    db.add(ExpenseStandard(expense_category="餐饮", standard_amount=Decimal("500"),
                           currency="CNY", effective_date=date(2026, 1, 1)))
    db.add(ExpenseStandard(expense_category="住宿", standard_amount=Decimal("600"),
                           currency="CNY", effective_date=date(2026, 1, 1)))
    db.add(ExpenseStandard(expense_category="交通", standard_amount=Decimal("800"),
                           currency="CNY", effective_date=date(2026, 1, 1)))
    db.add(MarketPriceReference(item_name="商务酒店", price_min=Decimal("300"),
                                price_max=Decimal("800"), currency="CNY",
                                source_name="携程均价", effective_date=date(2026, 8, 1)))
    db.add(MarketPriceReference(item_name="机票", specification="北京-上海",
                                price_min=Decimal("800"), price_max=Decimal("1800"),
                                currency="CNY", source_name="航司均价", effective_date=date(2026, 8, 1)))
    suppliers = [
        ("S001", "恒通科技有限公司", "normal", "normal", {"tags": []},
         {"accounts": [{"account": "6222021000000001", "bank": "工行"}]}),
        ("S002", "远东贸易有限公司", "normal", "blacklisted", {"tags": ["黑名单"]},
         {"accounts": [{"account": "6222031000000002", "bank": "建行"}]}),
        ("S003", "明辉设备有限公司", "abnormal", "normal",
         {"tags": ["历史履约异常"]}, {"accounts": [{"account": "6222041000000003", "bank": "招行"}]}),
    ]
    for code, name, credit, black, tags, accounts in suppliers:
        db.add(SupplierProfile(supplier_code=code, supplier_name=name, credit_status=credit,
                               blacklist_status=black, risk_tags_json=tags, bank_accounts_json=accounts))


def _attach(db, doc, file_name: str, preset: dict) -> None:
    """创建占位附件文件 + 附件行 + 预制解析 JSON。"""
    rel = Path(str(doc.id)) / f"{uuid.uuid4().hex}.png"
    abs_p = UPLOAD_DIR / rel
    abs_p.parent.mkdir(parents=True, exist_ok=True)
    png_bytes = make_png(int(uuid.uuid4().hex[:8], 16))
    abs_p.write_bytes(png_bytes)
    att = DocumentAttachment(
        document_id=doc.id, document_version=doc.current_version,
        file_name=file_name, file_type="png", file_size=len(png_bytes),
        file_path=str(rel).replace("\\", "/"),
        file_hash=hashlib.sha256(png_bytes).hexdigest(),
        storage_status="stored", parse_status="pending",
    )
    db.add(att)
    db.flush()
    stem = Path(file_name).stem
    (PRESET_DIR / f"{stem}.json").write_text(
        json.dumps(preset, ensure_ascii=False, indent=2), encoding="utf-8")


def seed_documents(db, users: dict[str, User]) -> list[dict]:
    """造 5 类示例单据。返回 [(doc, applicant_user)] 用于后续提交。"""
    today = date.today()
    docs = []

    # 1. 对公付款单（低风险：付款≤合同金额、发票一致、供应商正常）
    d1 = FinancialDocument(
        document_type="company_payment", document_no="CP-20260816-001",
        applicant_id=users["zhangsan"].id, applicant_department="市场部", budget_department="市场部",
        payee_name="恒通科技有限公司", payee_account="6222021000000001",
        expense_category="服务费", total_amount=Decimal("50000"), currency="CNY",
        apply_date=today, reason_text="软件开发服务费首期付款",
        type_fields_json={"contract_no": "C2026-001", "supplier_name": "恒通科技有限公司",
                          "payment_ratio": 50, "payment_terms": "首付款50%，验收后付清",
                          "planned_payment_date": str(today + timedelta(days=7))},
    )
    db.add(d1); db.flush()
    _attach(db, d1, "发票_恒通.png", {
        "category": "invoice",
        "fields": {"invoice_code": "202608100123", "invoice_no": "88880001",
                   "seller_name": "恒通科技有限公司", "buyer_name": "某某公司",
                   "invoice_date": "2026-08-10", "amount_including_tax": 50000,
                   "tax_amount": 5747.13, "amount_excluding_tax": 44252.87},
        "confidence": 0.99,
    })
    _attach(db, d1, "合同_恒通.png", {
        "category": "contract",
        "fields": {"contract_no": "C2026-001", "party_a": "某某公司",
                   "party_b": "恒通科技有限公司", "contract_amount": 100000,
                   "payment_terms": "首付50%，验收后付清", "payment_ratio": 50,
                   "signed_date": "2026-08-01"},
        "confidence": 0.97,
    })
    docs.append((d1, users["zhangsan"]))

    # 2. 预付款单（高风险：供应商黑名单）
    d2 = FinancialDocument(
        document_type="advance_payment", document_no="AP-20260816-002",
        applicant_id=users["lisi"].id, applicant_department="采购部", budget_department="采购部",
        payee_name="远东贸易有限公司", payee_account="6222031000000002",
        expense_category="采购", total_amount=Decimal("80000"), currency="CNY",
        apply_date=today, reason_text="原材料预付款",
        type_fields_json={"contract_no": "C2026-002", "supplier_name": "远东贸易有限公司",
                          "payment_ratio": 80, "payment_terms": "预付80%，货到付清",
                          "planned_payment_date": str(today + timedelta(days=3))},
    )
    db.add(d2); db.flush()
    _attach(db, d2, "合同_远东.png", {
        "category": "contract",
        "fields": {"contract_no": "C2026-002", "party_a": "某某公司",
                   "party_b": "远东贸易有限公司", "contract_amount": 100000,
                   "payment_terms": "预付80%，货到付清", "payment_ratio": 80,
                   "signed_date": "2026-08-02"},
        "confidence": 0.96,
    })
    docs.append((d2, users["lisi"]))

    # 3. 批量付款单（高风险：重复收款账号）
    d3 = FinancialDocument(
        document_type="batch_payment", document_no="BP-20260816-003",
        applicant_id=users["zhangsan"].id, applicant_department="行政部", budget_department="行政部",
        payee_name="多供应商", payee_account="batch", expense_category="采购",
        total_amount=Decimal("30000"), currency="CNY", apply_date=today, reason_text="8月批量采购付款",
        type_fields_json={"batch_note": "多供应商批量付款"},
    )
    db.add(d3); db.flush()
    for i, (name, acct, amt) in enumerate([
        ("甲供应商", "6222050001", "10000"),
        ("乙供应商", "6222050002", "10000"),
        ("甲供应商", "6222050001", "10000"),  # 重复账号
    ]):
        db.add(DocumentLineItem(document_id=d3.id, item_type="payment", item_name=name,
                                amount=Decimal(amt), remark=acct))
    _attach(db, d3, "付款回单_批量.png", {
        "category": "payment_doc", "fields": {"batch_total": 30000, "count": 3},
        "confidence": 0.98,
    })
    docs.append((d3, users["zhangsan"]))

    # 4. 费用报销单（中风险：酒店价格超市场区间 + 住宿超标准，2 个 medium → 整体 medium）
    d4 = FinancialDocument(
        document_type="expense", document_no="EX-20260816-004",
        applicant_id=users["lisi"].id, applicant_department="销售部", budget_department="销售部",
        payee_name="个人垫付", payee_account="lisi-reimburse", expense_category="差旅",
        total_amount=Decimal("1800"), currency="CNY", apply_date=today, reason_text="客户拜访费用",
    )
    db.add(d4); db.flush()
    wk = today - timedelta(days=4)  # 工作日
    db.add(DocumentLineItem(document_id=d4.id, item_type="expense", item_name="商务酒店",
                            expense_date=wk, expense_location="上海",
                            unit_price=Decimal("1200"), amount=Decimal("1200")))
    db.add(DocumentLineItem(document_id=d4.id, item_type="expense", item_name="市内交通",
                            expense_date=wk, expense_location="上海",
                            unit_price=Decimal("600"), amount=Decimal("600")))
    _attach(db, d4, "发票_酒店.png", {
        "category": "invoice",
        "fields": {"invoice_code": "202608100456", "invoice_no": "88880002",
                   "seller_name": "上海某某酒店", "buyer_name": "某某公司",
                   "invoice_date": str(wk), "amount_including_tax": 1800,
                   "tax_amount": 101.89, "amount_excluding_tax": 1698.11},
        "confidence": 0.98,
    })
    docs.append((d4, users["lisi"]))

    # 5. 差旅报销单（低风险：明细=总额=发票，各项均在标准内）
    d5 = FinancialDocument(
        document_type="travel", document_no="TR-20260816-005",
        applicant_id=users["zhangsan"].id, applicant_department="技术部", budget_department="技术部",
        payee_name="个人垫付", payee_account="zhangsan-travel", expense_category="差旅",
        total_amount=Decimal("3250"), currency="CNY", apply_date=today, reason_text="北京-上海出差",
        type_fields_json={"travel_destination": "上海", "travel_start": str(today - timedelta(days=5)),
                          "travel_end": str(today - timedelta(days=3)),
                          "transport_fee": 2000, "hotel_fee": 550, "meal_fee": 400, "allowance": 300},
    )
    db.add(d5); db.flush()
    db.add(DocumentLineItem(document_id=d5.id, item_type="expense", item_name="机票",
                            expense_date=today - timedelta(days=5), unit_price=Decimal("2000"),
                            amount=Decimal("2000")))
    db.add(DocumentLineItem(document_id=d5.id, item_type="expense", item_name="商务酒店",
                            expense_date=today - timedelta(days=4), unit_price=Decimal("550"),
                            amount=Decimal("550")))
    db.add(DocumentLineItem(document_id=d5.id, item_type="expense", item_name="餐饮",
                            expense_date=today - timedelta(days=4), unit_price=Decimal("400"),
                            amount=Decimal("400")))
    db.add(DocumentLineItem(document_id=d5.id, item_type="expense", item_name="补贴",
                            expense_date=today - timedelta(days=3), unit_price=Decimal("300"),
                            amount=Decimal("300")))
    _attach(db, d5, "发票_机票.png", {
        "category": "invoice",
        "fields": {"invoice_code": "202608100789", "invoice_no": "88880003",
                   "seller_name": "某航空公司", "buyer_name": "某某公司",
                   "invoice_date": str(today - timedelta(days=5)), "amount_including_tax": 3250,
                   "tax_amount": 184, "amount_excluding_tax": 3066},
        "confidence": 0.98,
    })
    _attach(db, d5, "行程单_出差.png", {"category": "itinerary", "fields": {},
                                      "confidence": 0.95})
    docs.append((d5, users["zhangsan"]))

    db.commit()
    return docs


def submit_all(db, docs) -> None:
    for doc, user in docs:
        document_service.submit(db, user, doc.id)
        print(f"[seed] 已提交 {doc.document_no} -> {doc.document_status}")
    # 等待后台分析全部进入终态（succeeded/failed/cancelled），最多 60s
    deadline = time.time() + 60
    while time.time() < deadline:
        pending = db.scalars(select(AnalysisTask).where(
            AnalysisTask.task_status.in_(["queued", "running"]))).all()
        if not pending:
            break
        time.sleep(0.5)
    tasks = db.scalars(select(AnalysisTask)).all()
    for t in tasks:
        print(f"[seed] 分析任务 {t.id} 状态 {t.task_status}")


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        wipe(db)
        users = seed_identity(db)
        seed_workflows(db)
        seed_rules_and_reference(db)
        seed_sys_params(db)
        docs = seed_documents(db, users)
        db.commit()
        submit_all(db, docs)
        print("[seed] 演示数据生成完成。")
        print("       登录账号: zhangsan/lisi(申请人) wangwu(审批人) zhaoliu(财务) admin(管理员)，密码均为 123456")
    finally:
        db.close()


if __name__ == "__main__":
    main()
