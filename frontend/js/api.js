/* API 层：统一 fetch 封装 + JWT 令牌管理 + 各模块方法。
   一个模块对应后端一个 router（function-map.md §3）。 */
const API = {
  token: localStorage.getItem('frr_token') || null,
  user: JSON.parse(localStorage.getItem('frr_user') || 'null'),

  _headers(extra) {
    const h = { 'Content-Type': 'application/json', ...(extra || {}) };
    if (this.token) h['Authorization'] = 'Bearer ' + this.token;
    return h;
  },

  async request(method, path, body) {
    const opts = { method, headers: this._headers() };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const res = await fetch('/api/v1' + path, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const msg = (data && data.detail) || `请求失败 (${res.status})`;
      throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
    }
    return data;
  },

  async upload(path, file) {
    const fd = new FormData();
    fd.append('file', file);
    const res = await fetch('/api/v1' + path, { method: 'POST', headers: { 'Authorization': 'Bearer ' + this.token }, body: fd });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error((data && data.detail) || `上传失败 (${res.status})`);
    return data;
  },

  setAuth(token, user) {
    this.token = token; this.user = user;
    localStorage.setItem('frr_token', token);
    localStorage.setItem('frr_user', JSON.stringify(user));
  },
  clearAuth() {
    this.token = null; this.user = null;
    localStorage.removeItem('frr_token'); localStorage.removeItem('frr_user');
  },

  // ---- 认证 ----
  async login(username, password) {
    const d = await this.request('POST', '/auth/login', { username, password });
    this.setAuth(d.access_token, d.user);
    return d.user;
  },
  async me() { return await this.request('GET', '/auth/me'); },
  async logout() { try { await this.request('POST', '/auth/logout'); } catch (e) {} this.clearAuth(); },

  // ---- 单据 ----
  docTypes: () => API.request('GET', '/documents/types'),
  listDocs: (q) => API.request('GET', '/documents?' + new URLSearchParams(q || {}).toString()),
  getDoc: (id) => API.request('GET', `/documents/${id}`),
  createDoc: (body) => API.request('POST', '/documents', body),
  updateDoc: (id, body) => API.request('PATCH', `/documents/${id}`, body),
  copyDoc: (id) => API.request('POST', `/documents/${id}/copy`),
  submitDoc: (id) => API.request('POST', `/documents/${id}/submit`),
  withdrawDoc: (id) => API.request('POST', `/documents/${id}/withdraw`),
  voidDoc: (id) => API.request('POST', `/documents/${id}/void`),
  addLineItem: (id, body) => API.request('POST', `/documents/${id}/line-items`, body),
  updateLineItem: (id, lid, body) => API.request('PATCH', `/documents/${id}/line-items/${lid}`, body),
  delLineItem: (id, lid) => API.request('DELETE', `/documents/${id}/line-items/${lid}`),
  amountCompare: (id) => API.request('GET', `/documents/${id}/amount-comparison`),
  createAnalysis: (id) => API.request('POST', `/documents/${id}/analysis`),
  docAnalysisLatest: (id) => API.request('GET', `/documents/${id}/analysis/latest`),

  // P0-3：带 Authorization 下载（<a href> 直链不会带 JWT Header）
  async download(path, filename) {
    const res = await fetch('/api/v1' + path, { headers: { 'Authorization': 'Bearer ' + this.token } });
    if (!res.ok) throw new Error('下载失败');
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename || 'download';
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  },

  // ---- 附件 ----
  uploadAtt: (id, file) => API.upload(`/documents/${id}/attachments`, file),
  delAtt: (id, aid) => API.request('DELETE', `/documents/${id}/attachments/${aid}`),
  parseAtt: (id, aid) => API.request('POST', `/documents/${id}/attachments/${aid}/parse`),
  attUrl: (id, aid) => `/api/v1/documents/${id}/attachments/${aid}`,

  // ---- 对话 ----
  createSession: () => API.request('POST', '/review-sessions'),
  sendMessage: (sid, content) => API.request('POST', `/review-sessions/${sid}/messages`, { content }),
  getMessages: (sid) => API.request('GET', `/review-sessions/${sid}/messages`),

  // ---- 分析 ----
  taskStatus: (tid) => API.request('GET', `/analysis-tasks/${tid}`),
  findings: (tid) => API.request('GET', `/analysis-tasks/${tid}/findings`),
  report: (tid) => API.request('GET', `/analysis-tasks/${tid}/report`),
  updateFindingStatus: (fid, review_status) => API.request('PATCH', `/risk-findings/${fid}/review-status`, { review_status }),

  // ---- 审批 ----
  myTasks: () => API.request('GET', '/approval-tasks'),
  approveTask: (id, comment) => API.request('POST', `/approval-tasks/${id}/approve`, { review_comment: comment || '' }),
  returnTask: (id, comment) => API.request('POST', `/approval-tasks/${id}/return`, { review_comment: comment || '' }),
  rejectTask: (id, comment) => API.request('POST', `/approval-tasks/${id}/reject`, { review_comment: comment || '' }),

  // ---- 配置 ----
  listRules: () => API.request('GET', '/rules'),
  updateRule: (id, body) => API.request('PATCH', `/rules/${id}`, body),
  listWorkflows: () => API.request('GET', '/approval-workflows'),
  createWorkflow: (body) => API.request('POST', '/approval-workflows', body),
  updateWorkflow: (id, body) => API.request('PATCH', `/approval-workflows/${id}`, body),

  // ---- 供应商 / 报告 / 审计 ----
  supplierRisks: (code) => API.request('GET', `/suppliers/${encodeURIComponent(code)}/risks`),
  supplierLookup: (name) => API.request('GET', `/suppliers/lookup?name=${encodeURIComponent(name)}`),
  listReports: () => API.request('GET', '/review-reports'),
  manualReview: (rid, body) => API.request('POST', `/review-reports/${rid}/manual-reviews`, body),
  exportUrl: (rid) => `/api/v1/review-reports/${rid}/export`,
  auditLogs: () => API.request('GET', '/audit-logs'),

  // ---- 管理端 ----
  adminUsers: () => API.request('GET', '/admin/users'),
  createUser: (body) => API.request('POST', '/admin/users', body),
  updateUser: (id, body) => API.request('PATCH', `/admin/users/${id}`, body),
  adminRoles: () => API.request('GET', '/admin/roles'),
  adminPermissions: () => API.request('GET', '/admin/permissions'),
  updateRolePerms: (id, codes) => API.request('PATCH', `/admin/roles/${id}/permissions`, { permission_codes: codes }),
  sysParams: () => API.request('GET', '/admin/sys-params'),
  updateSysParam: (key, value) => API.request('PATCH', `/admin/sys-params/${key}`, { param_value: value }),
};
