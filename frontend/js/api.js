// frontend/js/api.js
// Reusable API client for the LinkedIn Bot backend
// Works with any frontend framework — just import this module

const API_BASE = 'http://localhost:3000/api';

class LinkedInBotAPI {
  constructor(baseUrl = API_BASE) {
    this.base = baseUrl;
    this._sseSource = null;
    this._listeners  = {};
  }

  // ── Core fetch helper ───────────────────────────────────────────────────────
  async _fetch(path, options = {}) {
    const res = await fetch(`${this.base}${path}`, {
      headers: { 'Content-Type': 'application/json', ...options.headers },
      ...options,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: res.statusText }));
      throw new Error(err.error || `HTTP ${res.status}`);
    }
    return res.json();
  }

  _get(path)        { return this._fetch(path); }
  _post(path, body) { return this._fetch(path, { method: 'POST', body: JSON.stringify(body) }); }
  _put(path, body)  { return this._fetch(path, { method: 'PUT',  body: JSON.stringify(body) }); }
  _del(path)        { return this._fetch(path, { method: 'DELETE' }); }

  // ── SSE Real-time stream ────────────────────────────────────────────────────
  connectSSE(onMessage) {
    if (this._sseSource) this._sseSource.close();
    this._sseSource = new EventSource(`${this.base}/events`);
    this._sseSource.onmessage = (e) => {
      try { onMessage(JSON.parse(e.data)); } catch { /* ignore ping */ }
    };
    this._sseSource.onerror = () => {
      // Auto-reconnect handled by browser
    };
    return () => this._sseSource?.close();
  }

  // ── Accounts ────────────────────────────────────────────────────────────────
  getAccounts()               { return this._get('/accounts'); }
  getAccountStats()           { return this._get('/accounts/stats'); }
  createAccount(data)         { return this._post('/accounts', data); }
  updateAccount(id, data)     { return this._put(`/accounts/${id}`, data); }
  deleteAccount(id)           { return this._del(`/accounts/${id}`); }

  uploadResume(accountId, file) {
    const form = new FormData();
    form.append('resume', file);
    return fetch(`${this.base}/accounts/${accountId}/resume`, {
      method: 'POST', body: form,
    }).then(r => r.json());
  }

  // ── Bot Control ─────────────────────────────────────────────────────────────
  startBot(accountId)         { return this._post(`/bot/start/${accountId}`); }
  startBots(accountIds)       { return this._post('/bot/start', { accountIds }); }
  stopBot(accountId)          { return this._post(`/bot/stop/${accountId}`); }
  stopAllBots()               { return this._post('/bot/stop-all'); }
  getRunning()                { return this._get('/bot/running'); }

  // ── Search Configs ──────────────────────────────────────────────────────────
  getSearchConfigs(accountId) { return this._get(`/accounts/${accountId}/search-configs`); }
  createSearchConfig(accountId, data) {
    return this._post(`/accounts/${accountId}/search-configs`, data);
  }
  updateSearchConfig(id, data) { return this._put(`/search-configs/${id}`, data); }
  deleteSearchConfig(id)       { return this._del(`/search-configs/${id}`); }

  // ── Applications ────────────────────────────────────────────────────────────
  getApplications(accountId, filters = {}) {
    const qs = new URLSearchParams(filters).toString();
    return this._get(`/accounts/${accountId}/applications${qs ? '?' + qs : ''}`);
  }
  getManualReview(accountId) {
    return this._get(`/accounts/${accountId}/applications/manual-review`);
  }
  getApplicationQuestions(applicationId, includeAnswered = true) {
    return this._get(`/applications/${applicationId}/questions?includeAnswered=${includeAnswered}`);
  }
  getPendingApplications(accountId) {
    return this._get(`/pending-applications${accountId ? '?accountId=' + accountId : ''}`);
  }
  getPendingQuestions(accountId) {
    return this._get(`/pending-questions${accountId ? '?accountId=' + accountId : ''}`);
  }
  answerPendingQuestion(questionId, data) {
    return this._post(`/pending-questions/${questionId}/answer`, data);
  }
  retryApplication(applicationId) {
    return this._post(`/applications/${applicationId}/retry`, {});
  }

  // ── Q&A Templates ───────────────────────────────────────────────────────────
  getQATemplates(accountId)    { return this._get(`/qa-templates${accountId ? '?accountId=' + accountId : ''}`); }
  createQATemplate(data)       { return this._post('/qa-templates', data); }
  updateQATemplate(id, data)   { return this._put(`/qa-templates/${id}`, data); }
  deleteQATemplate(id)         { return this._del(`/qa-templates/${id}`); }

  // ── Logs ────────────────────────────────────────────────────────────────────
  getLogs(accountId, limit = 200) {
    const qs = new URLSearchParams({ ...(accountId && { accountId }), limit }).toString();
    return this._get(`/logs?${qs}`);
  }
}

// Export for both ESM and CJS/browser global
if (typeof module !== 'undefined') module.exports = LinkedInBotAPI;
else window.LinkedInBotAPI = LinkedInBotAPI;
