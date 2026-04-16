// frontend/js/app.js
// Dashboard application logic.

const api = new LinkedInBotAPI('http://localhost:3000/api');

let state = {
  accounts: [],
  stats: [],
  qaTemplates: [],
  pendingApplications: [],
  pendingQuestions: [],
  runningIds: new Set(),
};

let toastTimer = null;
let sseConnected = false;

const sseStatus = document.getElementById('sseStatus');
const sseDot = sseStatus.querySelector('.status-dot');

function setSSEStatus(connected) {
  sseConnected = connected;
  sseDot.className = connected ? 'status-dot online' : 'status-dot offline';
  sseStatus.lastChild.textContent = connected ? ' Connected' : ' Offline';
}

function toast(message, type = 'info') {
  const el = document.getElementById('toast');
  el.textContent = message;
  el.className = `toast ${type}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add('hidden'), 3500);
}

function esc(value) {
  if (value == null) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function timeAgo(dateStr) {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function accountLabel(accountId) {
  const acc = state.accounts.find((item) => item.id === accountId);
  return acc?.label || acc?.email || accountId || 'Unknown';
}

function currentTab() {
  return document.querySelector('.nav-item.active')?.dataset.tab || 'dashboard';
}

async function loadAccounts() {
  state.accounts = await api.getAccounts();
  state.stats = await api.getAccountStats();
  const running = await api.getRunning();
  state.runningIds = new Set(running.running || []);

  renderStatsBar();
  renderAccountGrid();
  renderAccountsTable();
  populateAccountDropdowns();
}

async function refreshStats() {
  state.stats = await api.getAccountStats().catch(() => state.stats);
  renderStatsBar();
  renderAccountGrid();
  renderAccountsTable();
}

async function loadQATemplates() {
  const [templates, pendingApplications, pendingQuestions] = await Promise.all([
    api.getQATemplates(),
    api.getPendingApplications(),
    api.getPendingQuestions(),
  ]);

  state.qaTemplates = templates;
  state.pendingApplications = pendingApplications;
  state.pendingQuestions = pendingQuestions;
  renderPendingApplicationsTable();
  renderPendingQuestionsTable();
  renderQATable();
}

async function loadManualReview() {
  const accountId = document.getElementById('manualAccountFilter').value;
  const tbody = document.getElementById('manualTableBody');
  tbody.innerHTML = '<tr><td colspan="6" class="empty-state">Loading...</td></tr>';

  try {
    const accountIds = accountId ? [accountId] : state.accounts.map((item) => item.id);
    const rows = [];

    for (const id of accountIds) {
      const res = await api.getManualReview(id);
      res.forEach((row) => rows.push({ ...row, _accountLabel: accountLabel(id) }));
    }

    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty-state">No jobs for manual review</td></tr>';
      return;
    }

    tbody.innerHTML = rows.map((row) => `
      <tr>
        <td>${esc(row.job_title || '-')}</td>
        <td>${esc(row.company_name || '-')}</td>
        <td>${esc(row.location || '-')}</td>
        <td>${esc(row._accountLabel)}</td>
        <td>${timeAgo(row.created_at)}</td>
        <td><a href="${esc(row.job_url)}" target="_blank" rel="noopener">Open</a></td>
      </tr>
    `).join('');
  } catch (err) {
    toast(`Failed to load manual review: ${err.message}`, 'error');
  }
}

async function loadLogs() {
  const accountId = document.getElementById('logAccountFilter').value || undefined;
  const terminal = document.getElementById('logTerminal');
  terminal.innerHTML = '<div class="log-entry info">Loading...</div>';

  try {
    const logs = await api.getLogs(accountId, 300);
    terminal.innerHTML = logs.reverse().map((log) => `
      <div class="log-entry ${log.level}">
        <span class="ts">${new Date(log.created_at).toLocaleTimeString()}</span>
        <span class="acc">[${esc(log.label || log.email || '?')}]</span>
        <span class="msg">${esc(log.message)}</span>
      </div>
    `).join('') || '<div class="log-entry info">No logs yet</div>';

    terminal.scrollTop = terminal.scrollHeight;
  } catch (err) {
    toast(`Failed to load logs: ${err.message}`, 'error');
  }
}

function renderStatsBar() {
  const running = state.runningIds.size;
  const applied = state.stats.reduce((sum, row) => sum + (parseInt(row.total_applied, 10) || 0), 0);
  const manual = state.stats.reduce((sum, row) => sum + (parseInt(row.manual_review_count, 10) || 0), 0);
  const failed = state.stats.reduce((sum, row) => sum + (parseInt(row.total_failed, 10) || 0), 0);

  document.getElementById('statRunning').textContent = running;
  document.getElementById('statApplied').textContent = applied;
  document.getElementById('statManual').textContent = manual;
  document.getElementById('statFailed').textContent = failed;
}

function renderAccountGrid() {
  const grid = document.getElementById('accountsGrid');
  if (!state.accounts.length) {
    grid.innerHTML = '<div class="empty-state">No accounts. Add one in the Accounts tab.</div>';
    return;
  }

  grid.innerHTML = state.accounts.map((acc) => {
    const statsRow = state.stats.find((row) => row.account_id === acc.id) || {};
    const isRunning = state.runningIds.has(acc.id);
    const status = isRunning ? 'running' : (acc.status || 'idle');

    return `
      <div class="account-card ${isRunning ? 'running' : ''}" id="card-${acc.id}">
        <div class="account-card-header">
          <div>
            <div class="account-card-name">${esc(acc.label || 'Account')}</div>
            <div class="account-card-email">${esc(acc.email)}</div>
          </div>
          <span class="badge badge-${status}">${status}</span>
        </div>
        <div class="progress-bar-wrap" id="prog-wrap-${acc.id}" style="display:none">
          <div class="progress-bar" id="prog-${acc.id}" style="width:0%"></div>
        </div>
        <div class="account-card-stats">
          <div><strong>${statsRow.total_applied || 0}</strong> applied</div>
          <div><strong>${statsRow.manual_review_count || 0}</strong> manual</div>
          <div><strong>${statsRow.pending_questions_count || 0}</strong> pending</div>
          <div><strong>${statsRow.total_failed || 0}</strong> failed</div>
        </div>
        <div class="account-card-actions">
          ${isRunning
            ? `<button class="btn btn-danger btn-sm" onclick="stopAccount('${acc.id}')">Stop</button>`
            : `<button class="btn btn-primary btn-sm" onclick="startAccount('${acc.id}')">Start</button>`}
          <button class="btn btn-secondary btn-sm" onclick="viewApplications('${acc.id}')">View Jobs</button>
        </div>
      </div>
    `;
  }).join('');
}

function renderAccountsTable() {
  const tbody = document.getElementById('accountsTableBody');
  if (!state.accounts.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty-state">No accounts yet</td></tr>';
    return;
  }

  tbody.innerHTML = state.accounts.map((acc) => {
    const statsRow = state.stats.find((row) => row.account_id === acc.id) || {};
    const status = state.runningIds.has(acc.id) ? 'running' : (acc.status || 'idle');
    const resumeText = acc.resume_path
      ? '<span style="color:var(--green)">Uploaded</span>'
      : '<span style="color:var(--text-muted)">None</span>';

    return `
      <tr>
        <td>${esc(acc.label || '-')}</td>
        <td style="font-family:var(--font-mono);font-size:12px">${esc(acc.email)}</td>
        <td>${resumeText}</td>
        <td><span class="badge badge-${status}">${status}</span></td>
        <td style="font-family:var(--font-mono)">${statsRow.total_applied || 0}</td>
        <td>
          <div style="display:flex;gap:6px">
            <label class="btn btn-secondary btn-sm" style="cursor:pointer">
              Resume
              <input type="file" accept=".pdf,.doc,.docx" style="display:none" onchange="uploadResume('${acc.id}', this)">
            </label>
            <button class="btn btn-danger btn-sm" onclick="deleteAccount('${acc.id}')">Delete</button>
          </div>
        </td>
      </tr>
    `;
  }).join('');
}

async function renderConfigList() {
  const list = document.getElementById('configList');
  list.innerHTML = '<div class="empty-state">Loading...</div>';

  const allConfigs = [];
  for (const acc of state.accounts) {
    const configs = await api.getSearchConfigs(acc.id).catch(() => []);
    configs.forEach((config) => allConfigs.push({ ...config, _accountLabel: accountLabel(acc.id) }));
  }

  if (!allConfigs.length) {
    list.innerHTML = '<div class="empty-state">No search configs yet. Add one above.</div>';
    return;
  }

  list.innerHTML = allConfigs.map((config) => `
    <div class="config-item">
      <div class="config-item-info">
        <div class="config-item-title">${esc(config.job_title)}</div>
        <div class="config-item-meta">
          ${config.location ? `Location: ${esc(config.location)} | ` : ''}
          Account: ${esc(config._accountLabel)} | Max: ${config.max_applications} | ${esc((config.date_posted || '').replace('_', ' '))}
        </div>
        <div class="config-item-tags">
          ${config.easy_apply_only ? '<span class="tag">Easy Apply</span>' : ''}
          ${config.remote_only ? '<span class="tag">Remote</span>' : ''}
          ${(config.job_type || []).map((type) => `<span class="tag">${esc(type)}</span>`).join('')}
          ${(config.experience_level || []).map((level) => `<span class="tag">${esc(level)}</span>`).join('')}
        </div>
      </div>
      <button class="btn btn-danger btn-sm" onclick="deleteConfig('${config.id}')">Remove</button>
    </div>
  `).join('');
}

function renderQATable() {
  const tbody = document.getElementById('qaTableBody');
  if (!state.qaTemplates.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty-state">No templates</td></tr>';
    return;
  }

  tbody.innerHTML = state.qaTemplates.map((template) => `
    <tr>
      <td><code style="font-family:var(--font-mono);color:var(--accent)">${esc(template.question_pattern)}</code></td>
      <td>${esc(template.answer)}</td>
      <td><span class="tag">${esc(template.field_type)}</span></td>
      <td>${template.job_title_scope ? esc(template.job_title_scope) : '<span style="color:var(--text-muted)">Any</span>'}</td>
      <td>${template.account_id ? esc(accountLabel(template.account_id)) : '<span style="color:var(--text-muted)">Global</span>'}</td>
      <td style="font-family:var(--font-mono)">${template.priority}</td>
      <td><button class="btn btn-danger btn-sm" onclick="deleteQA('${template.id}')">Delete</button></td>
    </tr>
  `).join('');
}

function renderPendingApplicationsTable() {
  const tbody = document.getElementById('pendingApplicationsBody');
  if (!tbody) return;

  if (!state.pendingApplications.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty-state">No pending applications right now.</td></tr>';
    return;
  }

  tbody.innerHTML = state.pendingApplications.map((application) => {
    const canContinue = application.status === 'ready_to_retry' || parseInt(application.missing_required_count, 10) === 0;
    const statusLabel = canContinue ? 'ready' : 'awaiting answers';

    return `
      <tr>
        <td>
          <div style="font-weight:600">${esc(application.job_title || '-')}</div>
          <div style="color:var(--text-secondary);font-size:12px">${esc(application.company_name || '-')}</div>
        </td>
        <td>${esc(application.search_job_title || application.job_title || '-')}</td>
        <td><span class="tag">${esc(statusLabel)}</span></td>
        <td>${esc(application.missing_required_count || 0)}</td>
        <td>${esc(application.answered_count || 0)}</td>
        <td>${esc(accountLabel(application.account_id))}</td>
        <td>
          <div style="display:flex;gap:6px;flex-wrap:wrap">
            <button class="btn btn-primary btn-sm" onclick="continuePendingApplication('${application.id}')" ${canContinue ? '' : 'disabled'}>Continue</button>
            <a class="btn btn-secondary btn-sm" href="${esc(application.job_url)}" target="_blank" rel="noopener">Open Job</a>
          </div>
        </td>
      </tr>
    `;
  }).join('');
}

function pendingAnswerControl(question) {
  const id = `pending-answer-${question.id}`;
  const options = Array.isArray(question.options) ? question.options : [];

  if (options.length && (question.field_type === 'select' || question.field_type === 'radio')) {
    return `
      <select id="${id}" class="pending-answer-control">
        <option value="">Select answer...</option>
        ${options.map((option) => `<option value="${esc(option)}">${esc(option)}</option>`).join('')}
      </select>
    `;
  }

  return `<input type="text" id="${id}" class="pending-answer-control" placeholder="Type answer..." />`;
}

function renderPendingQuestionsTable() {
  const tbody = document.getElementById('pendingQuestionsBody');
  if (!state.pendingQuestions.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty-state">No pending questions right now.</td></tr>';
    return;
  }

  tbody.innerHTML = state.pendingQuestions.map((question) => {
    const options = Array.isArray(question.options) ? question.options : [];
    const optionsText = options.length ? options.map((option) => `<span class="tag">${esc(option)}</span>`).join(' ') : '<span style="color:var(--text-muted)">Free text</span>';

    return `
      <tr>
        <td>${esc(question.job_title_scope || question.search_job_title || '-')}</td>
        <td>
          <div style="font-weight:600">${esc(question.job_title || '-')}</div>
          <div style="color:var(--text-secondary);font-size:12px">${esc(question.company_name || '-')}</div>
        </td>
        <td>${esc(question.question_text)}</td>
        <td><span class="tag">${esc(question.field_type)}</span></td>
        <td>${optionsText}</td>
        <td>${esc(accountLabel(question.account_id))}</td>
        <td>${pendingAnswerControl(question)}</td>
        <td>
          <div style="display:flex;gap:6px;flex-wrap:wrap">
            <button class="btn btn-primary btn-sm" onclick="answerPendingQuestion('${question.id}')">Save</button>
            <a class="btn btn-secondary btn-sm" href="${esc(question.job_url)}" target="_blank" rel="noopener">Open Job</a>
          </div>
        </td>
      </tr>
    `;
  }).join('');
}

function addActivity(accountId, payload) {
  const feed = document.getElementById('activityFeed');
  if (feed.querySelector('.empty-state')) feed.innerHTML = '';

  const job = payload.job || {};
  const result = payload.result || 'info';
  const item = document.createElement('div');
  item.className = 'activity-item';
  item.innerHTML = `
    <div class="activity-dot ${esc(result)}"></div>
    <div style="flex:1">
      <span style="font-weight:600">${esc(job.title || 'Application update')}</span>
      ${job.company ? `<span style="color:var(--text-secondary)"> at ${esc(job.company)}</span>` : ''}
      <span style="color:var(--text-muted);margin-left:6px;font-size:11px">[${esc(accountLabel(accountId))}]</span>
    </div>
    <div class="activity-time">${new Date().toLocaleTimeString()}</div>
  `;
  feed.prepend(item);

  while (feed.children.length > 50) feed.removeChild(feed.lastChild);
}

function appendLog(accountId, payload) {
  const terminal = document.getElementById('logTerminal');
  const entry = document.createElement('div');
  entry.className = `log-entry ${payload.level}`;
  entry.innerHTML = `
    <span class="ts">${new Date().toLocaleTimeString()}</span>
    <span class="acc">[${esc(accountLabel(accountId))}]</span>
    <span class="msg">${esc(payload.message)}</span>
  `;
  terminal.appendChild(entry);
  while (terminal.children.length > 300) terminal.removeChild(terminal.firstChild);
  terminal.scrollTop = terminal.scrollHeight;
}

function updateAccountProgress(accountId, payload) {
  const wrap = document.getElementById(`prog-wrap-${accountId}`);
  const bar = document.getElementById(`prog-${accountId}`);
  if (!wrap || !bar) return;

  if (payload.total > 0) {
    wrap.style.display = 'block';
    const pct = Math.round(((payload.current || 0) / payload.total) * 100);
    bar.style.width = `${pct}%`;
  }
}

function populateAccountDropdowns() {
  const selectors = ['#configAccount', '#qaAccount', '#manualAccountFilter', '#logAccountFilter'];
  selectors.forEach((selector) => {
    const el = document.querySelector(selector);
    if (!el) return;
    const first = el.options[0];
    el.innerHTML = '';
    el.appendChild(first);
    state.accounts.forEach((acc) => {
      const option = document.createElement('option');
      option.value = acc.id;
      option.textContent = accountLabel(acc.id);
      el.appendChild(option);
    });
  });
}

document.querySelectorAll('.nav-item').forEach((item) => {
  item.addEventListener('click', async (event) => {
    event.preventDefault();
    const tab = item.dataset.tab;

    document.querySelectorAll('.nav-item').forEach((node) => node.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach((panel) => panel.classList.remove('active'));

    item.classList.add('active');
    document.getElementById(`tab-${tab}`).classList.add('active');

    if (tab === 'qa') await loadQATemplates();
    if (tab === 'manual') await loadManualReview();
    if (tab === 'logs') await loadLogs();
    if (tab === 'search') await renderConfigList();
  });
});

function connectSSE() {
  api.connectSSE(async (msg) => {
    if (!sseConnected) setSSEStatus(true);

    const { type, accountId, payload } = msg;

    if (type === 'status') {
      const account = state.accounts.find((item) => item.id === accountId);
      if (account) account.status = payload.status;
      if (payload.status === 'running') state.runningIds.add(accountId);
      else state.runningIds.delete(accountId);

      renderAccountGrid();
      renderStatsBar();

      if (payload.status === 'completed') toast(`Account completed: ${JSON.stringify(payload.stats)}`, 'success');
      if (payload.status === 'error') toast(`Error: ${payload.message}`, 'error');
      return;
    }

    if (type === 'application') {
      addActivity(accountId, payload);
      await refreshStats();
      if (currentTab() === 'qa' && payload.result === 'pending_questions') await loadQATemplates();
      return;
    }

    if (type === 'progress') {
      updateAccountProgress(accountId, payload);
      return;
    }

    if (type === 'log') {
      appendLog(accountId, payload);
    }
  });

  setTimeout(() => {
    if (!sseConnected) setSSEStatus(false);
  }, 3000);
}

window.startAccount = async (accountId) => {
  try {
    await api.startBot(accountId);
    state.runningIds.add(accountId);
    renderAccountGrid();
    toast('Bot started', 'info');
  } catch (err) {
    toast(err.message, 'error');
  }
};

window.stopAccount = async (accountId) => {
  try {
    await api.stopBot(accountId);
    state.runningIds.delete(accountId);
    renderAccountGrid();
    toast('Bot stopped', 'info');
  } catch (err) {
    toast(err.message, 'error');
  }
};

document.getElementById('startAllBtn').addEventListener('click', async () => {
  const ids = state.accounts.map((acc) => acc.id);
  if (!ids.length) return toast('No accounts configured', 'error');

  try {
    const res = await api.startBots(ids);
    toast(`Started ${res.results.filter((row) => row.started).length} bots`, 'success');
    await loadAccounts();
  } catch (err) {
    toast(err.message, 'error');
  }
});

document.getElementById('stopAllBtn').addEventListener('click', async () => {
  try {
    await api.stopAllBots();
    state.runningIds.clear();
    renderAccountGrid();
    toast('All bots stopped', 'info');
  } catch (err) {
    toast(err.message, 'error');
  }
});

document.getElementById('addAccountBtn').addEventListener('click', () => {
  document.getElementById('accountForm').classList.toggle('hidden');
});

document.getElementById('cancelAccountBtn').addEventListener('click', () => {
  document.getElementById('accountForm').classList.add('hidden');
});

document.getElementById('saveAccountBtn').addEventListener('click', async () => {
  const label = document.getElementById('accLabel').value.trim();
  const email = document.getElementById('accEmail').value.trim();
  const password = document.getElementById('accPassword').value;
  const resumeFile = document.getElementById('accResume').files[0];

  if (!email || !password) return toast('Email and password required', 'error');

  try {
    const account = await api.createAccount({ label, email, password });
    if (resumeFile) await api.uploadResume(account.id, resumeFile);
    document.getElementById('accountForm').classList.add('hidden');
    toast('Account created', 'success');
    await loadAccounts();
  } catch (err) {
    toast(err.message, 'error');
  }
});

window.deleteAccount = async (accountId) => {
  if (!confirm('Delete this account and all its data?')) return;

  try {
    await api.deleteAccount(accountId);
    await loadAccounts();
    toast('Account deleted', 'info');
  } catch (err) {
    toast(err.message, 'error');
  }
};

window.uploadResume = async (accountId, input) => {
  const file = input.files[0];
  if (!file) return;

  try {
    await api.uploadResume(accountId, file);
    await loadAccounts();
    toast('Resume uploaded', 'success');
  } catch (err) {
    toast(err.message, 'error');
  }
};

window.viewApplications = async (accountId) => {
  document.querySelector('[data-tab="manual"]').click();
  document.getElementById('manualAccountFilter').value = accountId;
  await loadManualReview();
};

document.getElementById('saveConfigBtn').addEventListener('click', async () => {
  const accountId = document.getElementById('configAccount').value;
  const job_title = document.getElementById('configTitle').value.trim();
  const location = document.getElementById('configLocation').value.trim();

  if (!accountId) return toast('Select an account', 'error');
  if (!job_title) return toast('Job title is required', 'error');

  const experience_level = [...document.querySelectorAll('#expChips input:checked')].map((input) => input.value);
  const job_type = [...document.querySelectorAll('#typeChips input:checked')].map((input) => input.value);

  try {
    await api.createSearchConfig(accountId, {
      job_title,
      location,
      remote_only: document.getElementById('configRemote').checked,
      easy_apply_only: document.getElementById('configEasyApply').checked,
      max_applications: parseInt(document.getElementById('configMax').value, 10) || 50,
      date_posted: document.getElementById('configDate').value,
      experience_level,
      job_type,
    });

    document.getElementById('configTitle').value = '';
    document.getElementById('configLocation').value = '';
    await renderConfigList();
    toast('Search config added', 'success');
  } catch (err) {
    toast(err.message, 'error');
  }
});

window.deleteConfig = async (configId) => {
  try {
    await api.deleteSearchConfig(configId);
    await renderConfigList();
    toast('Config removed', 'info');
  } catch (err) {
    toast(err.message, 'error');
  }
};

document.getElementById('saveQABtn').addEventListener('click', async () => {
  const question_pattern = document.getElementById('qaPattern').value.trim();
  const answer = document.getElementById('qaAnswer').value.trim();
  const field_type = document.getElementById('qaType').value;
  const account_id = document.getElementById('qaAccount').value || null;
  const job_title_scope = document.getElementById('qaRoleScope').value.trim() || null;
  const priority = parseInt(document.getElementById('qaPriority').value, 10) || 5;

  if (!question_pattern || !answer) return toast('Pattern and answer required', 'error');

  try {
    await api.createQATemplate({
      question_pattern,
      answer,
      field_type,
      account_id,
      job_title_scope,
      priority,
    });

    document.getElementById('qaPattern').value = '';
    document.getElementById('qaAnswer').value = '';
    document.getElementById('qaRoleScope').value = '';
    await loadQATemplates();
    toast('Template added', 'success');
  } catch (err) {
    toast(err.message, 'error');
  }
});

window.deleteQA = async (templateId) => {
  try {
    await api.deleteQATemplate(templateId);
    await loadQATemplates();
    toast('Template deleted', 'info');
  } catch (err) {
    toast(err.message, 'error');
  }
};

window.answerPendingQuestion = async (questionId) => {
  const input = document.getElementById(`pending-answer-${questionId}`);
  const answer = input?.value?.trim();
  if (!answer) return toast('Please enter an answer first', 'error');

  try {
    const result = await api.answerPendingQuestion(questionId, { answer });
    const readyCount = (result.applicationStatuses || []).filter((row) => row.status === 'ready_to_retry').length;
    await loadQATemplates();
    await refreshStats();
    toast(`Answer saved. ${readyCount} application(s) ready to continue.`, 'success');
  } catch (err) {
    toast(err.message, 'error');
  }
};

window.continuePendingApplication = async (applicationId) => {
  try {
    const result = await api.retryApplication(applicationId);
    await loadQATemplates();
    await refreshStats();

    if (result.workerStarted) {
      state.runningIds.add(result.accountId);
      renderAccountGrid();
      renderStatsBar();
      toast('Pending application queued and worker started.', 'success');
      return;
    }

    toast('Pending application queued. The running worker will pick it up.', 'info');
  } catch (err) {
    toast(err.message, 'error');
  }
};

document.getElementById('clearLogsBtn').addEventListener('click', () => {
  document.getElementById('logTerminal').innerHTML = '';
});

document.getElementById('manualAccountFilter').addEventListener('change', loadManualReview);
document.getElementById('logAccountFilter').addEventListener('change', loadLogs);

(async () => {
  connectSSE();
  await loadAccounts();
  setInterval(refreshStats, 30000);
})();
