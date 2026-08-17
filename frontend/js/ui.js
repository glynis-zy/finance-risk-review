/* 前端共享工具：常量 / 格式化 / 徽标 / 下载 / 供应商跳转。 */
const TYPE_LABELS = {
  company_payment: '对公付款单', advance_payment: '预付款单', batch_payment: '批量付款单',
  expense: '费用报销单', travel: '差旅报销单',
};
const STATUS_LABELS = {
  draft: '草稿', pending_review: '待审批', reviewing: '审批中', returned: '已退回',
  approved: '已通过', rejected: '已驳回', withdrawn: '已撤回', voided: '已作废',
};

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (n) => Number(n ?? 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 });
const badge = (lvl) => `<span class="badge ${lvl}">${lvl}</span>`;
const stBadge = (st) => `<span class="badge ${st === 'approved' ? 'low' : st === 'rejected' || st === 'voided' ? 'high' : st === 'draft' ? 'neutral' : 'pending'}">${STATUS_LABELS[st] || st}</span>`;

function showError(msg) { alert(msg); }

async function openSupplier(name) {
  try { const r = await API.supplierLookup(name); location.hash = '#/supplier/' + r.supplier_code; }
  catch (e) { alert('未找到供应商档案：' + name); }
}
window.openSupplier = openSupplier;

// P0-3：附件/报告下载必须带 Authorization（<a href> 直链不带 JWT Header）
async function dlAtt(docId, attId, name) {
  try { await API.download(`/documents/${docId}/attachments/${attId}`, name || 'attachment'); }
  catch (e) { alert(e.message); }
}
window.dlAtt = dlAtt;
async function dlExport(reportId) {
  try { await API.download(`/review-reports/${reportId}/export`, `report-${reportId}.html`); }
  catch (e) { alert(e.message); }
}
window.dlExport = dlExport;
