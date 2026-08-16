/* 前端主逻辑：hash 路由 + 视图渲染（原生 JS，无框架）。 */
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

/* ---------- 工具：动态表单（元数据驱动） ---------- */
function typeFieldsHTML(fields, values) {
  return fields.map(f => {
    const v = values[f.key] ?? '';
    let inp;
    if (f.type === 'date') inp = `<input type="date" data-k="${f.key}" value="${esc(v)}">`;
    else if (f.type === 'number' || f.type === 'percent') inp = `<input type="number" step="0.01" data-k="${f.key}" value="${esc(v)}">`;
    else inp = `<input data-k="${f.key}" value="${esc(v)}">`;
    return `<div class="field"><label>${esc(f.label)}${f.required ? ' *' : ''}</label>${inp}</div>`;
  }).join('');
}
function collectTypeFields(container) {
  const out = {};
  container.querySelectorAll('[data-k]').forEach(i => { out[i.dataset.k] = i.value; });
  return out;
}

/* ---------- 路由 ---------- */
const routes = { dashboard: dashboardView, documents: documentsView, 'document-new': docEditView,
  'document-edit': docEditView, document: docDetailView, chat: chatView, approvals: approvalsView,
  rules: rulesView, workflows: workflowsView, records: recordsView, supplier: supplierView,
  admin: adminView };

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [name, param] = raw.split('/');
  return { name: name || 'dashboard', param };
}
function nav() { location.hash = '#/dashboard'; }

async function route() {
  const app = document.getElementById('app');
  if (!API.token) { app.innerHTML = loginHTML(); bindLogin(); return; }
  const { name, param } = parseHash();
  const view = routes[name] || dashboardView;
  app.innerHTML = layoutHTML(name);
  document.querySelectorAll('.sidebar nav a').forEach(a =>
    a.classList.toggle('active', a.dataset.route === name));
  const viewEl = document.getElementById('view');
  try { await view(viewEl, param); } catch (e) { viewEl.innerHTML = `<div class="msg error">${esc(e.message)}</div>`; }
}
window.addEventListener('hashchange', route);

/* ---------- 登录 ---------- */
function loginHTML() {
  return `<div class="login-wrap"><div class="login-card">
    <h1>财务单据智能风险审核系统</h1>
    <div class="sub">OCR 看懂 · LLM 理解 · 规则引擎判定 · LLM 润色</div>
    <div class="field"><label>用户名</label><input id="lg-u" value="zhangsan"></div>
    <div class="field"><label>密码</label><input id="lg-p" type="password" value="123456"></div>
    <div id="lg-err"></div>
    <button class="btn block" onclick="doLogin()">登 录</button>
    <div class="sub mt">演示账号：zhangsan/lisi(申请人) wangwu(审批) zhaoliu(财务) admin(管理员) / 123456</div>
  </div></div>`;
}
async function doLogin() {
  try {
    await API.login(document.getElementById('lg-u').value, document.getElementById('lg-p').value);
    nav();
  } catch (e) { document.getElementById('lg-err').innerHTML = `<div class="msg error">${esc(e.message)}</div>`; }
}
window.doLogin = doLogin;
async function doLogout() { await API.logout(); nav(); }
window.doLogout = doLogout;
async function openSupplier(name) {
  try { const r = await API.supplierLookup(name); location.hash = '#/supplier/' + r.supplier_code; }
  catch (e) { alert('未找到供应商档案：' + name); }
}
window.openSupplier = openSupplier;

/* ---------- 布局 ---------- */
function layoutHTML(active) {
  const u = API.user || {};
  const perms = u.permission_codes || [];
  const isAdmin = perms.some(p => ['user:manage', 'role:manage', 'system:manage'].includes(p));
  const menu = [
    ['dashboard', '审核工作台'], ['documents', '单据管理'], ['document-new', '新建单据'],
    ['chat', '智能审核对话'], ['approvals', '审批待办'], ['rules', '规则配置'],
    ['workflows', '流程配置'], ['records', '审核记录'],
  ];
  if (isAdmin) menu.push(['admin', '系统管理']);
  const links = menu.map(([r, t]) =>
    `<a data-route="${r}" href="#/${r}">${t}</a>`).join('');
  return `<div class="layout">
    <aside class="sidebar">
      <div class="brand">风险审核系统<small>Financial Risk Review</small></div>
      <nav>${links}</nav>
      <div class="foot">${esc(u.display_name || '')} · ${(u.role_codes || []).join('/')}</div>
    </aside>
    <div class="main">
      <header class="topbar">
        <h2>${esc(active === 'document' ? '单据详情' : menu.find(m => m[0] === active)?.[1] || '')}</h2>
        <div class="user"><span>${esc(u.display_name || u.username || '')}</span>
          <button class="btn ghost sm" onclick="doLogout()">退出</button></div>
      </header>
      <div class="content" id="view"></div>
    </div>
  </div>`;
}

/* ---------- 视图：工作台 ---------- */
async function dashboardView(el) {
  const [docs, tasks, reports] = await Promise.all([
    API.listDocs({ size: 100 }), API.myTasks().catch(() => []), API.listReports().catch(() => []),
  ]);
  const byStatus = (st) => (docs.items || []).filter(d => d.document_status === st).length;
  const highs = reports.filter(r => r.overall_risk_level === 'high').length;
  el.innerHTML = `
    <div class="grid">
      <div class="stat"><div class="num">${docs.total || 0}</div><div class="lbl">我的单据</div></div>
      <div class="stat"><div class="num">${(tasks || []).length}</div><div class="lbl">我的审批待办</div></div>
      <div class="stat"><div class="num">${highs}</div><div class="lbl">高风险报告</div></div>
      <div class="stat"><div class="num">${byStatus('pending_review') + byStatus('reviewing')}</div><div class="lbl">审批中单据</div></div>
    </div>
    <div class="card mt"><h3>最近单据</h3>
      <table><thead><tr><th>编号</th><th>类型</th><th>金额</th><th>状态</th><th>操作</th></tr></thead>
      <tbody>${(docs.items || []).slice(0, 8).map(d => `
        <tr><td class="mono">${esc(d.document_no)}</td>
        <td>${TYPE_LABELS[d.document_type] || d.document_type}</td>
        <td>¥${fmt(d.total_amount)}</td><td>${stBadge(d.document_status)}</td>
        <td><a class="btn ghost sm" href="#/document/${d.id}">详情</a></td></tr>`).join('') || '<tr><td colspan=5>暂无</td></tr>'}
      </tbody></table>
    </div>`;
}

/* ---------- 视图：单据列表 ---------- */
async function documentsView(el) {
  const types = await API.docTypes();
  el.innerHTML = `
    <div class="toolbar">
      <input id="f-no" placeholder="单据编号">
      <select id="f-type"><option value="">全部类型</option>${types.map(t => `<option value="${t.document_type}">${esc(t.label)}</option>`).join('')}</select>
      <select id="f-status"><option value="">全部状态</option>${Object.entries(STATUS_LABELS).map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}</select>
      <button class="btn" onclick="loadDocs()">查询</button>
      <a class="btn ghost" href="#/document-new">+ 新建单据</a>
    </div>
    <div class="card"><table id="doc-table"></table></div>`;
  window.loadDocs = async () => {
    const q = { size: 100 };
    const no = document.getElementById('f-no').value;
    const ty = document.getElementById('f-type').value;
    const st = document.getElementById('f-status').value;
    if (no) q.document_no = no; if (ty) q.document_type = ty; if (st) q.document_status = st;
    const data = await API.listDocs(q);
    document.getElementById('doc-table').innerHTML = `
      <thead><tr><th>编号</th><th>类型</th><th>申请人</th><th>部门</th><th>金额</th><th>状态</th><th>操作</th></tr></thead>
      <tbody>${(data.items || []).map(d => `
        <tr><td class="mono">${esc(d.document_no)}</td>
        <td>${TYPE_LABELS[d.document_type] || d.document_type}</td>
        <td>${esc(d.applicant_department)}</td><td>${esc(d.budget_department)}</td>
        <td>¥${fmt(d.total_amount)}</td><td>${stBadge(d.document_status)}</td>
        <td><a class="btn ghost sm" href="#/document/${d.id}">详情</a>
        ${['draft', 'returned'].includes(d.document_status) ? `<a class="btn ghost sm" href="#/document-edit/${d.id}">编辑</a>` : ''}
        ${['draft', 'returned'].includes(d.document_status) ? `<button class="btn ok sm" onclick="act('${d.id}','submit')">提交</button>` : ''}
        ${d.document_status === 'pending_review' ? `<button class="btn warn sm" onclick="act('${d.id}','withdraw')">撤回</button>` : ''}
        ${['draft', 'pending_review'].includes(d.document_status) ? `<button class="btn danger sm" onclick="act('${d.id}','void')">作废</button>` : ''}
        </td></tr>`).join('') || '<tr><td colspan=7>暂无</td></tr>'}
      </tbody>`;
  };
  window.act = async (id, a) => {
    const fn = { submit: API.submitDoc, withdraw: API.withdrawDoc, void: API.voidDoc }[a];
    try { await fn(id); alert(a === 'submit' ? '已提交审批' : a === 'withdraw' ? '已撤回' : '已作废'); loadDocs(); }
    catch (e) { alert(e.message); }
  };
  loadDocs();
}

/* ---------- 视图：新建/编辑单据（动态表单） ---------- */
async function docEditView(el, id) {
  const types = await API.docTypes();
  let doc = null, lineItems = [], attachments = [];
  if (id && id !== 'new') {
    const detail = await API.getDoc(id);
    doc = detail.document; lineItems = detail.line_items || []; attachments = detail.attachments || [];
  }
  const type = doc ? doc.document_type : types[0].document_type;
  const schema = types.find(t => t.document_type === type);
  const tf = doc ? (doc.type_fields || {}) : {};

  el.innerHTML = `
    <div class="card"><h3>${doc ? '编辑单据 ' + esc(doc.document_no) : '新建单据'}</h3>
      <div class="grid">
        <div class="field"><label>单据类型</label>
          <select id="e-type">${types.map(t => `<option value="${t.document_type}" ${t.document_type === type ? 'selected' : ''}>${esc(t.label)}</option>`).join('')}</select></div>
        <div class="field"><label>申请部门</label><input id="e-dept" value="${esc(doc?.applicant_department || '')}"></div>
        <div class="field"><label>预算部门</label><input id="e-budget" value="${esc(doc?.budget_department || '')}"></div>
        <div class="field"><label>收款单位</label><input id="e-payee" value="${esc(doc?.payee_name || '')}"></div>
        <div class="field"><label>收款账号</label><input id="e-acct" value="${esc(doc?.payee_account || '')}"></div>
        <div class="field"><label>费用类别</label><input id="e-cat" value="${esc(doc?.expense_category || '')}"></div>
        <div class="field"><label>总金额</label><input id="e-amt" type="number" step="0.01" value="${doc ? doc.total_amount : ''}"></div>
        <div class="field"><label>申请日期</label><input id="e-date" type="date" value="${doc ? doc.apply_date : new Date().toISOString().slice(0, 10)}"></div>
      </div>
      <div class="field"><label>事由</label><input id="e-reason" value="${esc(doc?.reason_text || '')}"></div>
      <h3 class="mt">类型专属字段</h3>
      <div class="grid" id="type-fields">${typeFieldsHTML(schema.fields, tf)}</div>
      <h3 class="mt">明细</h3>
      <table id="li-table">
        <thead><tr><th>类型</th><th>名称</th><th>规格</th><th>日期</th><th>地点</th><th>单价</th><th>金额</th><th>备注</th><th></th></tr></thead>
        <tbody></tbody>
      </table>
      <div class="toolbar mt"><button class="btn ghost sm" onclick="addLiRow()">+ 添加明细</button></div>
      <div id="li-hidden" class="hidden"></div>
      <h3 class="mt">附件</h3>
      <div id="att-list"></div>
      <div class="toolbar mt">
        <input type="file" id="att-file">
        <button class="btn sm" onclick="uploadAtt()">上传附件</button>
      </div>
      <div class="toolbar mt"><button class="btn" onclick="saveDoc(${doc ? doc.id : 'null'})">保存</button>
        ${doc && ['draft', 'returned'].includes(doc.document_status) ? '<button class="btn ok" onclick="saveDoc(' + doc.id + ', true)">保存并提交</button>' : ''}
      </div>
    </div>`;

  window.renderLi = () => {
    const tb = document.querySelector('#li-table tbody');
    tb.innerHTML = lineItems.map((li, i) => `
      <tr data-i="${i}">
        <td><select class="li-type"><option ${li.item_type === 'payment' ? 'selected' : ''}>payment</option><option ${li.item_type !== 'payment' ? 'selected' : ''}>expense</option></select></td>
        <td><input class="li-name" value="${esc(li.item_name)}"></td>
        <td><input class="li-spec" value="${esc(li.specification || '')}" placeholder="规格"></td>
        <td><input class="li-date" type="date" value="${esc(li.expense_date || '')}"></td>
        <td><input class="li-loc" value="${esc(li.expense_location || '')}"></td>
        <td><input class="li-price" value="${esc(li.unit_price || '')}"></td>
        <td><input class="li-amt" value="${esc(li.amount)}"></td>
        <td><input class="li-rem" value="${esc(li.remark || '')}"></td>
        <td><button class="btn danger sm" onclick="delLiRow(${i})">删</button></td>
      </tr>`).join('');
  };
  window.addLiRow = () => { lineItems.push({ item_type: 'expense', item_name: '', amount: 0 }); renderLi(); };
  window.delLiRow = (i) => { lineItems.splice(i, 1); renderLi(); };

  window.renderAtt = () => {
    document.getElementById('att-list').innerHTML = `
      <table><thead><tr><th>文件名</th><th>类型</th><th>解析状态</th><th>操作</th></tr></thead>
      <tbody>${attachments.map(a => `
        <tr><td>${esc(a.file_name)}</td><td>${a.file_type}</td>
        <td>${esc(a.parse_status)}</td>
        <td><a class="btn ghost sm" href="${API.attUrl(doc ? doc.id : id, a.id)}" target="_blank">下载</a>
            <button class="btn ghost sm" onclick="parseAtt(${a.id})">解析</button>
            <button class="btn danger sm" onclick="delAtt(${a.id})">删除</button></td></tr>`).join('')}
      </tbody></table>`;
  };
  window.parseAtt = async (aid) => {
    try { await API.parseAtt(doc.id, aid); renderAtt(); } catch (e) { alert(e.message); }
  };
  window.delAtt = async (aid) => {
    try { await API.delAtt(doc.id, aid); attachments = attachments.filter(a => a.id !== aid); renderAtt(); }
    catch (e) { alert(e.message); }
  };
  window.uploadAtt = async () => {
    const f = document.getElementById('att-file').files[0];
    if (!f) return;
    try { await API.uploadAtt(doc.id, f); renderAtt(); } catch (e) { alert(e.message); }
  };

  window.saveDoc = async (did, submit) => {
    const tf = collectTypeFields(document.getElementById('type-fields'));
    const body = {
      document_type: document.getElementById('e-type').value,
      applicant_department: document.getElementById('e-dept').value,
      budget_department: document.getElementById('e-budget').value,
      payee_name: document.getElementById('e-payee').value,
      payee_account: document.getElementById('e-acct').value,
      expense_category: document.getElementById('e-cat').value,
      total_amount: document.getElementById('e-amt').value,
      apply_date: document.getElementById('e-date').value,
      reason_text: document.getElementById('e-reason').value,
      type_fields: tf,
    };
    try {
      let saved = did ? await API.updateDoc(did, body) : await API.createDoc(body);
      const sid = saved.id;
      // 保存明细
      const rows = [...document.querySelectorAll('#li-table tbody tr')];
      for (const row of rows) {
        const it = {
          item_type: row.querySelector('.li-type').value,
          item_name: row.querySelector('.li-name').value,
          specification: row.querySelector('.li-spec').value || null,
          expense_date: row.querySelector('.li-date').value || null,
          expense_location: row.querySelector('.li-loc').value || null,
          unit_price: row.querySelector('.li-price').value || null,
          amount: row.querySelector('.li-amt').value,
          remark: row.querySelector('.li-rem').value || null,
        };
        if (it.item_name || it.amount) await API.addLineItem(sid, it);
      }
      // 上传附件
      const f = document.getElementById('att-file').files[0];
      if (f) await API.uploadAtt(sid, f);
      if (submit) await API.submitDoc(sid);
      location.hash = '#/document/' + sid;
    } catch (e) { alert(e.message); }
  };

  document.getElementById('e-type').addEventListener('change', () => {
    const t = document.getElementById('e-type').value;
    const s = types.find(x => x.document_type === t);
    document.getElementById('type-fields').innerHTML = typeFieldsHTML(s.fields, {});
  });
  renderLi();
  renderAtt();
}

/* ---------- 视图：单据详情（基本信息/金额/风险/附件/审批） ---------- */
async function docDetailView(el, id) {
  const detail = await API.getDoc(id);
  const d = detail.document;
  const tabs = ['基本信息', '金额核对', '风险分析', '附件解析', '审批进度'];
  el.innerHTML = `
    <div class="card"><div class="tabs">${tabs.map((t, i) => `<button class="${i === 0 ? 'active' : ''}" data-tab="${i}">${t}</button>`).join('')}</div>
      <div id="tab-body"></div></div>`;
  const tabsEl = el.querySelector('.tabs');
  const body = el.querySelector('#tab-body');
  const show = async (i) => {
    [...tabsEl.children].forEach((b, j) => b.classList.toggle('active', j === i));
    if (i === 0) body.innerHTML = docInfoHTML(detail);
    if (i === 1) body.innerHTML = '<div class="msg info">加载金额核对…</div>', await amountTab(body, d.id);
    if (i === 2) body.innerHTML = '<div class="msg info">加载风险分析…</div>', await riskTab(body, d.id, id);
    if (i === 3) body.innerHTML = attTabHTML(detail), bindAttTab(body, detail, d.id);
    if (i === 4) body.innerHTML = approvalTabHTML(detail);
  };
  [...tabsEl.children].forEach((b, i) => b.onclick = () => show(i));
  show(0);
}

function docInfoHTML(detail) {
  const d = detail.document;
  const kv = (k, v) => `<dt>${k}</dt><dd>${v}</dd>`;
  let tf = '';
  for (const [k, v] of Object.entries(d.type_fields || {})) tf += kv(esc(k), esc(v));
  const supName = (d.type_fields || {}).supplier_name;
  const supLink = supName
    ? `<div class="toolbar mt"><button class="btn ghost sm" onclick="openSupplier('${esc(supName)}')">查看供应商风险</button></div>`
    : '';
  return `${supLink}<dl class="kv">
    ${kv('单据类型', TYPE_LABELS[d.document_type] || d.document_type)}
    ${kv('单据编号', `<span class="mono">${esc(d.document_no)}</span>`)}
    ${kv('状态', stBadge(d.document_status))}
    ${kv('申请部门', esc(d.applicant_department))}
    ${kv('预算部门', esc(d.budget_department))}
    ${kv('收款单位', esc(d.payee_name))}
    ${kv('总金额', `¥${fmt(d.total_amount)} ${esc(d.currency)}`)}
    ${kv('申请日期', esc(d.apply_date))}
    ${kv('版本', d.current_version)}
    ${tf}
  </dl><h3 class="mt">明细</h3><table>
    <thead><tr><th>类型</th><th>名称</th><th>规格</th><th>日期</th><th>金额</th></tr></thead>
    <tbody>${(detail.line_items || []).map(li => `<tr><td>${li.item_type}</td><td>${esc(li.item_name)}</td><td>${esc(li.specification || '-')}</td><td>${esc(li.expense_date || '-')}</td><td>¥${fmt(li.amount)}</td></tr>`).join('') || '<tr><td colspan=5>无</td></tr>'}
    </tbody></table>`;
}

async function amountTab(body, did) {
  const c = await API.amountCompare(did);
  body.innerHTML = `<table>
    <thead><tr><th>项目</th><th>金额</th></tr></thead>
    <tbody>
      <tr><td>单据总金额</td><td>¥${fmt(c.document_total)}</td></tr>
      <tr><td>明细合计</td><td>¥${fmt(c.line_items_total)}</td></tr>
      <tr><td>发票合计</td><td>¥${fmt(c.invoice_total)}</td></tr>
      <tr><td>合同金额</td><td>${c.contract_amount === null ? '-' : '¥' + fmt(c.contract_amount)}</td></tr>
      <tr><td>付款金额</td><td>¥${fmt(c.payment_amount)}</td></tr>
      <tr><td>单据-明细差异</td><td>¥${fmt(c.differences.document_minus_line_items)}</td></tr>
      <tr><td>单据-发票差异</td><td>¥${fmt(c.differences.document_minus_invoice)}</td></tr>
    </tbody></table>`;
}

async function riskTab(body, did, docId) {
  const amt = await API.amountCompare(did);
  let taskId = null;
  try {
    const t = await API.createAnalysis(did);
    taskId = t.task_id;
  } catch (e) { taskId = null; }
  if (taskId === null) {
    // 若创建失败，尝试从已有报告（兜底：无报告入口则提示）
    body.innerHTML = `<div class="msg error">无法发起分析：${esc('请检查单据是否已提交')}</div>`;
    return;
  }
  body.innerHTML = `<div class="msg info">分析任务 ${taskId} 执行中…</div>`;
  const timer = setInterval(async () => {
    try {
      const st = await API.taskStatus(taskId);
      if (st.task_status === 'succeeded' || st.task_status === 'failed') {
        clearInterval(timer);
        const rep = await API.report(taskId);
        const findings = await API.findings(taskId);
        body.innerHTML = riskHTML(rep, findings);
        bindRiskActions(body, findings);
      } else {
        body.innerHTML = `<div class="msg info">分析中：${esc(st.current_step || st.task_status)}</div>`;
      }
    } catch (e) { clearInterval(timer); body.innerHTML = `<div class="msg error">${esc(e.message)}</div>`; }
  }, 2000);
}

function riskHTML(rep, findings) {
  return `<h3>整体风险：${badge(rep.overall_risk_level)}　建议：${esc(rep.recommendation)}</h3>
    <div class="msg info mt">${esc((rep.risk_summary || {}).count || 0)} 项风险（高 ${(rep.risk_summary || {}).high || 0} / 中 ${(rep.risk_summary || {}).medium || 0} / 低 ${(rep.risk_summary || {}).low || 0}）</div>
    <table><thead><tr><th>等级</th><th>风险项</th><th>描述</th><th>实际值</th><th>参考值</th><th>阈值</th><th>建议</th><th>复核</th></tr></thead>
    <tbody>${findings.map(f => `
      <tr><td>${badge(f.risk_level)}</td><td>${esc(f.risk_title)}</td><td>${esc(f.description)}</td>
      <td class="mono">${esc(JSON.stringify(f.actual || {}))}</td><td class="mono">${esc(JSON.stringify(f.reference || {}))}</td>
      <td class="mono">${esc(JSON.stringify(f.threshold || {}))}</td><td>${esc(f.suggestion || '')}</td>
      <td><select data-fid="${f.id}"><option ${f.review_status === 'pending' ? 'selected' : ''} value="pending">待复核</option>
      <option ${f.review_status === 'confirmed' ? 'selected' : ''} value="confirmed">确认</option>
      <option ${f.review_status === 'dismissed' ? 'selected' : ''} value="dismissed">排除</option></select></td></tr>`).join('') || '<tr><td colspan=8>无风险项</td></tr>'}
    </tbody></table>
    <div class="toolbar mt">
      <a class="btn ghost sm" href="${API.exportUrl(rep.report_id)}" target="_blank">导出报告(HTML)</a>
    </div>
    <div class="card mt"><h3>人工复核</h3>
      <div class="grid">
        <div class="field"><label>复核结论</label><select id="rev-result">
          <option value="approved">通过</option><option value="return">退回</option>
          <option value="reject">驳回</option><option value="manual">需人工进一步处理</option></select></div>
        <div class="field"><label>复核意见</label><input id="rev-comment" placeholder="填写复核意见"></div>
      </div>
      <button class="btn sm" onclick="submitReview(${rep.report_id})">提交复核</button></div>
    <details class="mt"><summary>查看报告全文</summary><pre class="mono" style="white-space:pre-wrap;background:#f8fafc;padding:12px;border-radius:6px">${esc(rep.report_markdown)}</pre></details>`;
}
function bindRiskActions(body, findings) {
  body.querySelectorAll('[data-fid]').forEach(sel => sel.onchange = async () => {
    try { await API.updateFindingStatus(sel.dataset.fid, sel.value); } catch (e) { alert(e.message); }
  });
  window.submitReview = async (rid) => {
    const review_result = document.getElementById('rev-result').value;
    const review_comment = document.getElementById('rev-comment').value;
    try { await API.manualReview(rid, { review_result, review_comment }); alert('复核已提交'); }
    catch (e) { alert(e.message); }
  };
}

function attTabHTML(detail) {
  return `<table><thead><tr><th>文件名</th><th>类型</th><th>大小</th><th>解析状态</th><th>操作</th></tr></thead>
    <tbody>${(detail.attachments || []).map(a => `
      <tr><td>${esc(a.file_name)}</td><td>${a.file_type}</td><td>${(a.file_size / 1024).toFixed(1)}KB</td>
      <td>${esc(a.parse_status)}</td>
      <td><a class="btn ghost sm" href="${API.attUrl(detail.document.id, a.id)}" target="_blank">下载/预览</a>
      <button class="btn ghost sm" onclick="tryParse(${a.id})">解析</button></td></tr>`).join('') || '<tr><td colspan=5>无附件</td></tr>'}
    </tbody></table>`;
}
function bindAttTab(body, detail, docId) {
  window.tryParse = async (aid) => {
    try { await API.parseAtt(docId, aid); body.innerHTML = attTabHTML(detail); bindAttTab(body, detail, docId); }
    catch (e) { alert(e.message); }
  };
}

function approvalTabHTML(detail) {
  const ap = detail.approval || {};
  return `<p>审批实例状态：<span class="badge ${ap.instance_status === 'running' ? 'pending' : 'neutral'}">${esc(ap.instance_status || '无')}</span></p>
    <table><thead><tr><th>任务</th><th>状态</th><th>审批意见</th></tr></thead>
    <tbody>${(ap.tasks || []).map(t => `<tr><td>节点 #${t.node_id}</td><td>${esc(t.task_status)}</td><td>${esc(t.review_comment || '')}</td></tr>`).join('') || '<tr><td colspan=3>无审批任务</td></tr>'}
    </tbody></table>`;
}

/* ---------- 视图：智能审核对话 ---------- */
async function chatView(el) {
  const s = await API.createSession();
  el.innerHTML = `
    <div class="chat-box" id="chat-box"></div>
    <div class="chat-input"><input id="chat-in" placeholder="输入单据类型和编号，例如：对公付款单 CP-20260816-001"><button class="btn" onclick="chatSend()">发送</button></div>`;
  const box = el.querySelector('#chat-box');
  const render = async () => {
    const msgs = await API.getMessages(s.session_id);
    box.innerHTML = msgs.map(m =>
      `<div class="chat-item ${m.role}">${esc(m.content)}</div>`).join('');
    box.scrollTop = box.scrollHeight;
  };
  window.chatSend = async () => {
    const inp = el.querySelector('#chat-in');
    const text = inp.value.trim();
    if (!text) return;
    inp.value = '';
    await API.sendMessage(s.session_id, text);
    render();
  };
  el.querySelector('#chat-in').addEventListener('keydown', e => { if (e.key === 'Enter') chatSend(); });
  render();
}

/* ---------- 视图：审批待办 ---------- */
async function approvalsView(el) {
  const tasks = await API.myTasks();
  el.innerHTML = `<div class="card"><h3>我的审批待办</h3>
    <table><thead><tr><th>节点</th><th>单据</th><th>类型</th><th>金额</th><th>部门</th><th>操作</th></tr></thead>
    <tbody>${tasks.map(t => `
      <tr><td>${esc(t.node_name)}</td><td class="mono"><a href="#/document/${t.document_id}">${esc(t.document_no)}</a></td>
      <td>${TYPE_LABELS[t.document_type] || t.document_type}</td><td>¥${fmt(t.total_amount)}</td>
      <td>${esc(t.applicant_department)}</td>
      <td>
        <a class="btn ghost sm" href="#/document/${t.document_id}">详情</a>
        <button class="btn ok sm" onclick="actTask(${t.task_id},'approve')">通过</button>
        <button class="btn warn sm" onclick="actTask(${t.task_id},'return')">退回</button>
        <button class="btn danger sm" onclick="actTask(${t.task_id},'reject')">驳回</button>
      </td></tr>`).join('') || '<tr><td colspan=6>暂无待办</td></tr>'}
    </tbody></table></div>`;
  window.actTask = async (id, a) => {
    const fn = { approve: API.approveTask, return: API.returnTask, reject: API.rejectTask }[a];
    const r = confirm(a === 'approve' ? '确认通过该审批？' : a === 'return' ? '确认退回修改？' : '确认驳回？');
    if (!r) return;
    try { await fn(id); alert(a === 'approve' ? '已通过' : a === 'return' ? '已退回' : '已驳回'); approvalsView(el); }
    catch (e) { alert(e.message); }
  };
}

/* ---------- 视图：规则配置 ---------- */
async function rulesView(el) {
  const rules = await API.listRules();
  el.innerHTML = `<div class="card"><h3>风险规则配置（risk_rules）</h3>
    <table><thead><tr><th>规则</th><th>状态</th><th>阈值配置(JSON)</th><th></th></tr></thead>
    <tbody>${rules.map(r => `
      <tr><td><b>${esc(r.rule_code)}</b><br>${esc(r.rule_name)}</td>
      <td><input type="checkbox" data-rid="${r.id}" ${r.enabled ? 'checked' : ''}></td>
      <td><textarea data-cfg="${r.id}" style="min-height:48px">${esc(JSON.stringify(r.config))}</textarea></td>
      <td><button class="btn sm" onclick="saveRule(${r.id})">保存</button></td></tr>`).join('')}
    </tbody></table></div>`;
  window.saveRule = async (rid) => {
    const rule = rules.find(x => x.id === rid);
    const enabled = document.querySelector(`[data-rid="${rid}"]`).checked;
    const cfg = JSON.parse(document.querySelector(`[data-cfg="${rid}"]`).value || '{}');
    try { await API.updateRule(rid, { rule_code: rule.rule_code, rule_name: rule.rule_name, applies_to: rule.applies_to, enabled, config: cfg }); alert('已保存'); }
    catch (e) { alert(e.message); }
  };
}

/* ---------- 视图：流程配置（可新建/编辑） ---------- */
async function workflowsView(el) {
  const wfs = await API.listWorkflows();
  const types = await API.docTypes();
  el.innerHTML = `<div class="card"><h3>审批流程配置</h3>
    <button class="btn sm" onclick="wfForm()">+ 新建流程</button>
    <div id="wf-form"></div>
    <div class="mt">${wfs.map(w => `
      <div class="card">
        <div style="display:flex;justify-content:space-between">
          <div><b>${esc(w.workflow_name)}</b> · ${TYPE_LABELS[w.document_type] || w.document_type} <span class="badge neutral">${esc(w.status)}</span></div>
          <div><button class="btn ghost sm" onclick="wfForm(${w.id})">编辑</button></div>
        </div>
        <div class="mt">匹配条件：金额 ≥ ${w.match_conditions?.amount_min ?? 0}</div>
        <div class="mt">节点：${w.nodes.map(n => `${esc(n.node_name)}(${esc(n.approver_role)})`).join(' → ') || '无节点'}</div>
      </div>`).join('') || '<div class="msg info mt">暂无流程</div>'}
    </div></div>`;
  window.wfForm = async (id) => {
    const target = id ? wfs.find(x => x.id === id) : null;
    const box = document.getElementById('wf-form');
    const nodes = (target?.nodes || []).map(n => `${n.node_name}|${n.approver_role}`).join('\n');
    box.innerHTML = `
      <div class="grid">
        <div class="field"><label>流程名称</label><input id="wf-name" value="${esc(target?.workflow_name || '')}"></div>
        <div class="field"><label>单据类型</label><select id="wf-type">${types.map(t => `<option value="${t.document_type}" ${target?.document_type === t.document_type ? 'selected' : ''}>${esc(t.label)}</option>`).join('')}</select></div>
        <div class="field"><label>最小金额</label><input id="wf-min" value="${target?.match_conditions?.amount_min ?? 0}"></div>
      </div>
      <div class="field"><label>审批节点（每行：节点名|角色，角色 ∈ approver/finance/admin）</label>
        <textarea id="wf-nodes" style="min-height:70px">${esc(nodes)}</textarea></div>
      <button class="btn sm" onclick="wfSave(${id || 'null'})">保存</button>`;
  };
  window.wfSave = async (id) => {
    const nodes = document.getElementById('wf-nodes').value.split('\n').filter(Boolean).map((line, i) => {
      const [name, role] = line.split('|');
      return { node_name: (name || '').trim(), approver_role: (role || 'approver').trim(), node_order: i + 1 };
    });
    const body = {
      workflow_name: document.getElementById('wf-name').value,
      document_type: document.getElementById('wf-type').value,
      match_conditions: { amount_min: Number(document.getElementById('wf-min').value) || 0 },
      nodes,
    };
    try {
      if (id) await API.updateWorkflow(id, body); else await API.createWorkflow(body);
      workflowsView(el);
    } catch (e) { alert(e.message); }
  };
}

/* ---------- 视图：审核记录 ---------- */
async function recordsView(el) {
  const [reports, audits] = await Promise.all([API.listReports(), API.auditLogs().catch(() => [])]);
  el.innerHTML = `
    <div class="card"><h3>历史风险报告</h3>
      <table><thead><tr><th>单据</th><th>整体风险</th><th>建议</th><th>生成时间</th><th>操作</th></tr></thead>
      <tbody>${reports.map(r => `
        <tr><td class="mono">${esc(r.document_no || '')}</td><td>${badge(r.overall_risk_level)}</td>
        <td>${esc(r.recommendation)}</td><td>${esc((r.created_at || '').slice(0, 19).replace('T', ' '))}</td>
        <td><a class="btn ghost sm" href="${API.exportUrl(r.report_id)}" target="_blank">导出</a></td></tr>`).join('') || '<tr><td colspan=5>暂无</td></tr>'}
      </tbody></table>
    </div>
    <div class="card"><h3>操作审计日志</h3>
      <table><thead><tr><th>时间</th><th>操作人</th><th>动作</th><th>资源</th><th>详情</th></tr></thead>
      <tbody>${audits.slice(0, 30).map(a => `
        <tr><td>${esc((a.created_at || '').slice(0, 19).replace('T', ' '))}</td><td>${a.user_id ?? '-'}</td>
        <td>${esc(a.action_type)}</td><td>${esc(a.resource_type)}/${esc(a.resource_id || '')}</td>
        <td class="mono">${esc(JSON.stringify(a.detail || {}))}</td></tr>`).join('')}
      </tbody></table>
    </div>`;
}

/* ---------- 视图：供应商风险 ---------- */
async function supplierView(el, code) {
  const s = await API.supplierRisks(code);
  el.innerHTML = `<div class="card"><h3>供应商：${esc(s.supplier_name)}</h3>
    <dl class="kv">
      <dt>供应商编码</dt><dd class="mono">${esc(s.supplier_code)}</dd>
      <dt>资质状态</dt><dd>${esc(s.credit_status)}</dd>
      <dt>黑名单</dt><dd>${s.blacklist_status === 'blacklisted' ? badge('high') + ' 黑名单' : '正常'}</dd>
      <dt>风险标签</dt><dd>${(s.risk_tags || []).map(t => `<span class="badge high">${esc(t)}</span>`).join(' ') || '无'}</dd>
      <dt>累计付款</dt><dd>¥${fmt(s.total_paid)}（${s.payment_count} 笔）</dd>
    </dl>
    <h3 class="mt">历史付款</h3><table>
      <thead><tr><th>单据</th><th>金额</th><th>日期</th><th>状态</th></tr></thead>
      <tbody>${s.history.map(h => `<tr><td class="mono">${esc(h.document_no)}</td><td>¥${fmt(h.amount)}</td><td>${esc(h.apply_date)}</td><td>${esc(h.status)}</td></tr>`).join('') || '<tr><td colspan=4>无</td></tr>'}
      </tbody></table></div>`;
}

/* ---------- 视图：系统管理（用户/角色权限/系统参数） ---------- */
async function adminView(el) {
  const tabs = ['用户管理', '角色权限', '系统参数'];
  el.innerHTML = `<div class="card"><div class="tabs">${tabs.map((t, i) => `<button class="${i === 0 ? 'active' : ''}" data-tab="${i}">${t}</button>`).join('')}</div>
    <div id="adm-body"></div></div>`;
  const head = el.querySelector('.tabs');
  const body = el.querySelector('#adm-body');
  const show = async (i) => {
    [...head.children].forEach((b, j) => b.classList.toggle('active', j === i));
    if (i === 0) await admUsers(body);
    if (i === 1) await admRoles(body);
    if (i === 2) await admParams(body);
  };
  [...head.children].forEach((b, i) => b.onclick = () => show(i));
  show(0);
}

async function admUsers(body) {
  const users = await API.adminUsers();
  body.innerHTML = `
    <div class="toolbar"><button class="btn sm" onclick="admUserForm()">+ 新建用户</button></div>
    <div id="adm-user-form"></div>
    <table><thead><tr><th>用户名</th><th>姓名</th><th>角色</th><th>状态</th><th>权限数</th><th>操作</th></tr></thead>
    <tbody>${users.map(u => `<tr>
      <td>${esc(u.username)}</td><td>${esc(u.display_name)}</td>
      <td>${(u.role_codes || []).map(r => `<span class="badge neutral">${esc(r)}</span>`).join(' ') || '-'}</td>
      <td>${esc(u.status)}</td><td>${u.permission_codes.length}</td>
      <td><button class="btn ghost sm" onclick="admUserForm(${u.id})">编辑</button>
          <button class="btn ghost sm" onclick="admUserToggle(${u.id},'${u.status}')">${u.status === 'active' ? '停用' : '启用'}</button></td></tr>`).join('')}
    </tbody></table>`;
  window.admUserToggle = async (id, st) => {
    try { await API.updateUser(id, { status: st === 'active' ? 'disabled' : 'active' }); admUsers(body); }
    catch (e) { alert(e.message); }
  };
  window.admUserForm = async (id) => {
    const target = id ? users.find(u => u.id === id) : null;
    const roles = await API.adminRoles();
    const box = document.getElementById('adm-user-form');
    box.innerHTML = `
      <div class="grid">
        <div class="field"><label>用户名</label><input id="au-name" value="${esc(target?.username || '')}" ${target ? 'disabled' : ''}></div>
        <div class="field"><label>姓名</label><input id="au-display" value="${esc(target?.display_name || '')}"></div>
        <div class="field"><label>密码</label><input id="au-pass" type="password" placeholder="${target ? '留空不修改' : '默认 123456'}"></div>
      </div>
      <div class="field"><label>角色</label>
        <div style="display:flex;gap:14px;flex-wrap:wrap">${roles.map(r => `
          <label style="font-weight:400"><input type="checkbox" class="au-role" value="${r.role_code}" ${target?.role_codes.includes(r.role_code) ? 'checked' : ''}> ${esc(r.role_name)} (${esc(r.role_code)})</label>`).join('')}
        </div></div>
      <button class="btn sm" onclick="admUserSave(${id || 'null'})">保存</button>`;
  };
  window.admUserSave = async (id) => {
    const roles = [...document.querySelectorAll('.au-role:checked')].map(i => i.value);
    const body2 = {
      username: document.getElementById('au-name').value,
      display_name: document.getElementById('au-display').value,
      password: document.getElementById('au-pass').value || '123456',
      role_codes: roles,
    };
    try {
      if (id) await API.updateUser(id, { display_name: body2.display_name, role_codes: body2.role_codes, password: body2.password !== '123456' ? body2.password : undefined });
      else await API.createUser(body2);
      admUsers(body);
    } catch (e) { alert(e.message); }
  };
}

async function admRoles(body) {
  const [roles, perms] = await Promise.all([API.adminRoles(), API.adminPermissions()]);
  body.innerHTML = `<table><thead><tr><th>角色</th><th>权限</th></tr></thead>
    <tbody>${roles.map(r => `<tr>
      <td><b>${esc(r.role_name)}</b><br><span class="badge neutral">${esc(r.role_code)}</span></td>
      <td>
        <div style="max-height:200px;overflow-y:auto">
        ${perms.map(p => `<label style="display:inline-block;min-width:220px;font-weight:400;margin:2px 8px 2px 0">
          <input type="checkbox" class="rp-${r.id}" value="${p.permission_code}" ${r.permission_codes.includes(p.permission_code) ? 'checked' : ''}> ${esc(p.permission_code)}
        </label>`).join('')}
        </div>
        <button class="btn sm mt" onclick="admRoleSave(${r.id})">保存该角色权限</button>
      </td></tr>`).join('')}
    </tbody></table>`;
  window.admRoleSave = async (id) => {
    const codes = [...document.querySelectorAll(`.rp-${id}:checked`)].map(i => i.value);
    try { await API.updateRolePerms(id, codes); alert('角色权限已保存'); } catch (e) { alert(e.message); }
  };
}

async function admParams(body) {
  const params = await API.sysParams();
  body.innerHTML = `<table><thead><tr><th>参数</th><th>值</th><th>说明</th><th></th></tr></thead>
    <tbody>${params.map(p => `<tr>
      <td class="mono">${esc(p.param_key)}</td>
      <td><input data-pk="${esc(p.param_key)}" value="${esc(p.param_value)}" style="min-width:120px"></td>
      <td>${esc(p.description || '')}</td>
      <td><button class="btn sm" onclick="admParamSave('${esc(p.param_key)}')">保存</button></td></tr>`).join('')}
    </tbody></table>`;
  window.admParamSave = async (key) => {
    const val = document.querySelector(`[data-pk="${key}"]`).value;
    try { await API.updateSysParam(key, val); alert('已保存，生效于下一次分析'); } catch (e) { alert(e.message); }
  };
}

/* 启动 */
route();
