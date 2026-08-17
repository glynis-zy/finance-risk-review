# -*- coding: utf-8 -*-
"""报告服务：生成 Markdown 风险报告（LLM 润色）、HTML 导出、人工复核。

规格 2.7.13 报告结构：单据摘要→整体风险→金额核对→风险项列表→证据→供应商→处理建议→人工复核。
导出：markdown 源 → HTML（浏览器可打印 PDF），不引 PDF 库。
"""
from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.clients import llm as llm_client
from app.document_schemas import TYPE_LABELS
from app.models.analysis import ManualReview, ReviewReport
from app.models.user import User
from app.services import amount_service, audit_service


def _json_safe(value):
    """报告/面板 JSON 序列化：Decimal → str。"""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def _recommendation(overall: str, findings: list) -> str:
    highs = sum(1 for f in findings if f.risk_level == "high")
    if highs >= 2:
        return "建议驳回"
    if overall == "high":
        return "人工复核"
    if overall == "medium":
        return "补充材料"
    return "建议通过"


def generate(db: Session, task, doc, findings: list, overall: str) -> ReviewReport:
    """汇总风险项 → 生成 markdown（LLM 润色）→ 落 review_reports。"""
    comp = amount_service.calculate_amount_comparison(db, doc)  # P1-13：与金额核对面板同一实现
    n_high = sum(1 for f in findings if f.risk_level == "high")
    n_med = sum(1 for f in findings if f.risk_level == "medium")
    n_low = sum(1 for f in findings if f.risk_level == "low")
    recommendation = _recommendation(overall, findings)

    lines = []
    lines.append("# 财务单据风险审核报告")
    lines.append("")
    lines.append("## 一、单据摘要")
    lines.append(f"- 单据类型：{TYPE_LABELS.get(doc.document_type, doc.document_type)}")
    lines.append(f"- 单据编号：{doc.document_no}")
    lines.append(f"- 申请人部门：{doc.applicant_department}")
    lines.append(f"- 预算部门：{doc.budget_department}")
    lines.append(f"- 收款单位：{doc.payee_name}")
    lines.append(f"- 总金额：{doc.total_amount} {doc.currency}")
    lines.append(f"- 申请日期：{doc.apply_date}")
    lines.append("")
    lines.append("## 二、整体风险")
    lines.append(f"- 整体风险等级：**{overall}**")
    lines.append(f"- 风险项统计：高 {n_high} / 中 {n_med} / 低 {n_low}")
    lines.append(f"- 审核建议：{recommendation}")
    lines.append("")
    lines.append("## 三、金额核对")
    lines.append("| 项目 | 金额 |")
    lines.append("| --- | --- |")
    lines.append(f"| 单据总金额 | {doc.total_amount} |")
    lines.append(f"| 明细合计 | {comp.line_items_total} |")
    lines.append(f"| 发票合计 | {comp.invoice_total} |")
    lines.append(f"| 合同金额 | {comp.contract_amount if comp.contract_amount is not None else '-'} |")
    lines.append(f"| 付款金额 | {comp.payment_amount} |")
    lines.append("")
    lines.append("## 四、风险项列表")
    if not findings:
        lines.append("- 未发现风险项。")
    for i, f in enumerate(findings, 1):
        lines.append(f"### {i}. [{f.risk_level}] {f.risk_title}")
        lines.append(f"- 风险类型：{f.risk_type}")
        lines.append(f"- 描述：{f.description}")
        if f.actual:
            lines.append(f"- 实际值：{f.actual}")
        if f.reference:
            lines.append(f"- 参考值：{f.reference}")
        if f.threshold:
            lines.append(f"- 规则阈值：{f.threshold}")
        if f.suggestion:
            lines.append(f"- 处理建议：{f.suggestion}")
        lines.append("")
    lines.append("## 五、处理建议")
    lines.append(f"- **{recommendation}**：{('存在高风险项，需人工复核后再审批' if recommendation in ('人工复核', '建议驳回') else '材料齐全，风险可控')}。")
    lines.append("")
    lines.append("## 六、人工复核")
    lines.append("（审批人员填写复核意见）")

    markdown = "\n".join(lines)

    # P1-8：LLM 只润色、不污染确定结论——整体风险等级/统计/建议由规则引擎权威给出，
    # LLM 叙述作为独立的 "### AI 风险说明" 小节插入（即使 LLM 写错，固定结论仍是权威值）。
    try:
        narrative = llm_client.polish_risk_report(
            f"{TYPE_LABELS.get(doc.document_type)} {doc.document_no}，金额 {doc.total_amount}",
            [{"level": f.risk_level, "title": f.risk_title, "desc": f.description} for f in findings],
        )
        if narrative:
            marker = "## 三、金额核对"
            markdown = markdown.replace(
                marker, f"### AI 风险说明\n\n{narrative}\n\n{marker}", 1)
    except Exception:  # noqa: BLE001
        pass

    summary = {
        "high": n_high, "medium": n_med, "low": n_low,
        "count": len(findings),
        "by_type": {t: sum(1 for f in findings if f.risk_type == t) for t in
                    {f.risk_type for f in findings}},
    }
    report = ReviewReport(
        task_id=task.id,
        document_id=doc.id,
        overall_risk_level=overall,
        risk_summary_json=summary,
        amount_comparison_json=_json_safe(comp.model_dump()),
        recommendation=recommendation,
        report_markdown=markdown,
    )
    db.add(report)
    db.flush()
    return report


def export_html(report: ReviewReport) -> str:
    """markdown → 简约商务 HTML（可打印成 PDF）。"""
    body = _md_to_html(report.report_markdown)
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>风险审核报告 - {report.id}</title>
<style>
 body{{font-family:"Microsoft YaHei",sans-serif;max-width:820px;margin:24px auto;padding:0 24px;color:#1f2937;}}
 h1{{border-bottom:2px solid #1e3a8a;padding-bottom:8px;font-size:24px;}}
 h2{{color:#1e3a8a;margin-top:28px;font-size:18px;}}
 h3{{font-size:15px;margin:16px 0 4px;}}
 table{{border-collapse:collapse;width:100%;margin:8px 0;}}
 th,td{{border:1px solid #d1d5db;padding:6px 10px;text-align:left;font-size:14px;}}
 th{{background:#f3f4f6;}}
 pre,code{{white-space:pre-wrap;}}
 .risk-high{{color:#b91c1c;font-weight:bold;}}
</style></head><body>{body}</body></html>"""


def _md_to_html(md: str) -> str:
    """极简 markdown 渲染（标题/列表/表格/强调），够演示用。"""
    import html as html_lib
    out = []
    for line in md.splitlines():
        line = line.rstrip()
        if not line:
            out.append("<p></p>")
        elif line.startswith("### "):
            out.append(f"<h3>{html_lib.escape(line[4:])}</h3>")
        elif line.startswith("## "):
            out.append(f"<h2>{html_lib.escape(line[3:])}</h2>")
        elif line.startswith("# "):
            out.append(f"<h1>{html_lib.escape(line[2:])}</h1>")
        elif line.startswith("- "):
            out.append(f"<p>• {html_lib.escape(line[2:])}</p>")
        elif line.startswith("| "):
            if "---" in line:
                continue
            cells = [html_lib.escape(c.strip()) for c in line.strip("|").split("|")]
            out.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
        else:
            out.append(f"<p>{html_lib.escape(line)}</p>")
    return "".join(out)


def submit_manual_review(db: Session, user: User, report_id: int,
                         review_result: str, comment: str) -> ManualReview:
    """人工复核（P1-7）：只记录复核结论（confirmed/needs_material/escalated），
    不改 document_status——正式单据状态只能由 ApprovalTask 流程决定。"""
    if review_result not in ("confirmed", "needs_material", "escalated"):
        raise HTTPException(400, "review_result 取值: confirmed/needs_material/escalated")
    report = db.get(ReviewReport, report_id)
    if report is None:
        raise HTTPException(404, "报告不存在")
    review = ManualReview(
        report_id=report_id,
        reviewer_id=user.id,
        review_result=review_result,
        review_comment=comment,
    )
    db.add(review)
    audit_service.log(db, user, "report:manual_review", "review_report", str(report_id))
    db.commit()
    db.refresh(review)
    return review
