// frontend/js/app.js
// Dashboard application logic.

const api = new LinkedInBotAPI('http://localhost:3000/api');

let state = {
  accounts: [],
  stats: [],
  qaTemplates: [],
  qaProfileTemplateMap: {},
  pendingApplications: [],
  pendingQuestions: [],
  runningIds: new Set(),
};

const QA_PROFILE_FIELDS = [
  {
    key: 'phone_number',
    label: 'Phone Number',
    question_pattern: 'phone',
    match_patterns: ['phone', 'phone number', 'mobile', 'mobile number', 'telephone'],
    field_type: 'text',
    input_type: 'text',
    default_answer: '',
    placeholder: '+91 98765 43210',
    priority: 12,
    required: true,
    help_text: 'Used for the phone or mobile number question during Easy Apply.',
  },
  {
    key: 'years_of_experience',
    label: 'Years of Experience',
    question_pattern: 'years of experience',
    match_patterns: ['years of experience', 'total experience', 'overall experience'],
    field_type: 'number',
    input_type: 'number',
    default_answer: '3',
    placeholder: '3',
    priority: 10,
    required: true,
  },
  {
    key: 'linkedin_url',
    label: 'LinkedIn URL',
    question_pattern: 'linkedin',
    match_patterns: ['linkedin', 'linkedin url', 'linkedin profile', 'linkedin profile url', 'profile url'],
    field_type: 'text',
    input_type: 'text',
    default_answer: '',
    placeholder: 'https://www.linkedin.com/in/your-profile',
    priority: 10,
    required: true,
  },
  {
    key: 'current_location',
    label: 'Current Location',
    question_pattern: 'current location',
    match_patterns: ['current location', 'current city', 'where are you located', 'currently located'],
    field_type: 'text',
    input_type: 'text',
    default_answer: '',
    placeholder: 'San Francisco, CA',
    priority: 10,
    required: true,
  },
  {
    key: 'legally_authorized',
    label: 'Legally Authorized to Work',
    question_pattern: 'legally authorized',
    field_type: 'select',
    input_type: 'text',
    default_answer: 'Yes',
    placeholder: 'Yes',
    priority: 10,
    required: true,
    options: ['Yes', 'No'],
  },
  {
    key: 'require_sponsorship',
    label: 'Require Sponsorship',
    question_pattern: 'require sponsorship',
    field_type: 'select',
    input_type: 'text',
    default_answer: 'No',
    placeholder: 'No',
    priority: 10,
    required: true,
    options: ['No', 'Yes'],
  },
  {
    key: 'work_remotely',
    label: 'Open to Remote Work',
    question_pattern: 'work remotely',
    field_type: 'select',
    input_type: 'text',
    default_answer: 'Yes',
    placeholder: 'Yes',
    priority: 5,
    required: false,
    options: ['Yes', 'No'],
  },
  {
    key: 'relocate',
    label: 'Willing to Relocate',
    question_pattern: 'relocate',
    field_type: 'select',
    input_type: 'text',
    default_answer: 'Yes',
    placeholder: 'Yes',
    priority: 5,
    required: false,
    options: ['Yes', 'No'],
  },
  {
    key: 'notice_period',
    label: 'Notice Period',
    question_pattern: 'notice period',
    field_type: 'text',
    input_type: 'text',
    default_answer: '30 days',
    placeholder: '30 days',
    priority: 5,
    required: false,
  },
  {
    key: 'expected_salary',
    label: 'Expected Salary',
    question_pattern: 'expected salary',
    field_type: 'number',
    input_type: 'number',
    default_answer: '80000',
    placeholder: '80000',
    priority: 5,
    required: false,
  },
  {
    key: 'current_salary',
    label: 'Current Salary',
    question_pattern: 'current salary',
    field_type: 'number',
    input_type: 'number',
    default_answer: '70000',
    placeholder: '70000',
    priority: 5,
    required: false,
  },
  {
    key: 'gender',
    label: 'Gender',
    question_pattern: 'gender',
    field_type: 'select',
    input_type: 'text',
    default_answer: 'Prefer not to say',
    placeholder: 'Prefer not to say',
    priority: 3,
    required: false,
  },
  {
    key: 'veteran',
    label: 'Veteran Status',
    question_pattern: 'veteran',
    field_type: 'select',
    input_type: 'text',
    default_answer: 'I am not a veteran',
    placeholder: 'I am not a veteran',
    priority: 3,
    required: false,
  },
  {
    key: 'disability',
    label: 'Disability Status',
    question_pattern: 'disability',
    field_type: 'select',
    input_type: 'text',
    default_answer: "I don't wish to answer",
    placeholder: "I don't wish to answer",
    priority: 3,
    required: false,
  },
  {
    key: 'race',
    label: 'Race / Ethnicity',
    question_pattern: 'race',
    field_type: 'select',
    input_type: 'text',
    default_answer: 'Prefer not to say',
    placeholder: 'Prefer not to say',
    priority: 3,
    required: false,
  },
];

let toastTimer = null;
let sseConnected = false;

const sseStatus = document.getElementById('sseStatus');
const sseDot = sseStatus.querySelector('.status-dot');
const qaProfileStatus = document.getElementById('qaProfileStatus');
const jobRecordModal = document.getElementById('jobRecordModal');
const jobRecordTitle = document.getElementById('jobRecordTitle');
const jobRecordSubtitle = document.getElementById('jobRecordSubtitle');
const jobRecordNotice = document.getElementById('jobRecordNotice');
const jobRecordMeta = document.getElementById('jobRecordMeta');
const jobRecordLinks = document.getElementById('jobRecordLinks');
const jobRecordQuestions = document.getElementById('jobRecordQuestions');
const closeJobRecordBtn = document.getElementById('closeJobRecordBtn');

let jobRecordRequestId = 0;

function qaProfileInputId(key) {
  return `qaProfile-${key}`;
}

function normalizePattern(value) {
  return String(value || '').trim().toLowerCase();
}

function getFieldPatterns(field) {
  const patterns = Array.isArray(field.match_patterns) && field.match_patterns.length
    ? field.match_patterns
    : [field.question_pattern];
  return patterns.map((pattern) => normalizePattern(pattern)).filter(Boolean);
}

function chunkItems(items, size) {
  const chunks = [];
  for (let index = 0; index < items.length; index += size) {
    chunks.push(items.slice(index, index + size));
  }
  return chunks;
}

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

function humanizeStatus(value) {
  return String(value || 'unknown').replace(/_/g, ' ');
}

function extractLinkedInJobId(value) {
  const text = String(value || '').trim();
  if (!text) return '';

  const pathMatch = text.match(/\/jobs\/view\/(\d+)/i);
  if (pathMatch?.[1]) return pathMatch[1];

  const queryMatch = text.match(/[?&#](?:currentJobId|jobId)=(\d+)/i);
  if (queryMatch?.[1]) return queryMatch[1];

  const rawIdMatch = text.match(/^\d+$/);
  return rawIdMatch?.[0] || '';
}

function openJobRecordModal() {
  jobRecordModal.classList.remove('hidden');
  jobRecordModal.setAttribute('aria-hidden', 'false');
}

function closeJobRecordModal() {
  jobRecordModal.classList.add('hidden');
  jobRecordModal.setAttribute('aria-hidden', 'true');
}

function setJobRecordLoading() {
  jobRecordTitle.textContent = 'Loading job record...';
  jobRecordSubtitle.textContent = 'Fetching the saved application details for this row.';
  jobRecordNotice.textContent = 'LinkedIn can redirect saved job URLs when a listing changes, expires, or is replaced. This view shows the exact record stored by the bot.';
  jobRecordMeta.innerHTML = '';
  jobRecordLinks.innerHTML = '<div class="job-record-empty">Loading saved URLs...</div>';
  jobRecordQuestions.innerHTML = '<div class="job-record-empty">Loading captured questions...</div>';
}

function renderJobRecordMeta(context) {
  const application = context.application || {};
  const cards = [
    ['Status', humanizeStatus(application.status)],
    ['Account', application.account_label || application.account_email || accountLabel(application.account_id)],
    ['Role Scope', application.search_job_title || application.job_title || '-'],
    ['Location', application.location || '-'],
    ['Saved Job ID', extractLinkedInJobId(application.job_url) || 'Unknown'],
    ['Saved', application.created_at ? new Date(application.created_at).toLocaleString() : '-'],
  ];

  if (application.error_message) {
    cards.push(['Last Note', application.error_message]);
  }

  jobRecordMeta.innerHTML = cards.map(([label, value]) => `
    <div class="job-record-card">
      <div class="job-record-card-label">${esc(label)}</div>
      <div class="job-record-card-value">${esc(value || '-')}</div>
    </div>
  `).join('');
}

function renderJobRecordLinks(context) {
  const application = context.application || {};
  const related = Array.isArray(context.relatedApplications) ? context.relatedApplications : [];
  const allLinks = [{ ...application, _isPrimary: true }, ...related];

  if (!allLinks.length) {
    jobRecordLinks.innerHTML = '<div class="job-record-empty">No saved URL was found for this application.</div>';
    return;
  }

  jobRecordLinks.innerHTML = allLinks.map((item) => {
    const jobId = extractLinkedInJobId(item.job_url);
    const label = item._isPrimary ? 'Selected record' : 'Related saved record';
    return `
      <div class="job-record-link ${item._isPrimary ? 'is-primary' : ''}">
        <div>
          <div class="job-record-link-title">${esc(label)}</div>
          <div class="job-record-link-meta">
            ${esc(item.job_title || '-')} at ${esc(item.company_name || '-')}<br>
            Job ID ${esc(jobId || 'unknown')} · ${esc(humanizeStatus(item.status))} · ${esc(item.created_at ? timeAgo(item.created_at) : '-')}
          </div>
        </div>
        ${item.job_url
          ? `<a class="btn btn-secondary btn-sm" href="${esc(item.job_url)}" target="_blank" rel="noopener">Open on LinkedIn</a>`
          : '<span class="job-record-link-meta">No URL saved</span>'}
      </div>
    `;
  }).join('');
}

function renderJobRecordQuestions(questions) {
  if (!questions.length) {
    jobRecordQuestions.innerHTML = '<div class="job-record-empty">No application questions were captured for this record.</div>';
    return;
  }

  jobRecordQuestions.innerHTML = questions.map((question) => {
    const answer = String(question.answer || '').trim();
    const status = answer ? 'Answered' : (question.is_required ? 'Required' : 'Optional');
    return `
      <div class="job-record-question">
        <div class="job-record-question-head">
          <div class="job-record-question-text">${esc(question.question_text || '-')}</div>
          <span class="tag">${esc(status)}</span>
        </div>
        <div class="job-record-question-answer">
          ${answer ? esc(answer) : '<span style="color:var(--text-muted)">No answer saved</span>'}
        </div>
      </div>
    `;
  }).join('');
}

async function viewApplicationRecord(applicationId) {
  if (!applicationId) {
    toast('No saved application record is available for this row.', 'error');
    return;
  }

  const requestId = ++jobRecordRequestId;
  openJobRecordModal();
  setJobRecordLoading();

  try {
    const [context, questions] = await Promise.all([
      api.getApplicationContext(applicationId),
      api.getApplicationQuestions(applicationId, true).catch(() => []),
    ]);

    if (requestId !== jobRecordRequestId) return;

    const application = context.application || {};
    jobRecordTitle.textContent = application.job_title || 'Saved Job Record';
    jobRecordSubtitle.textContent = application.company_name
      ? `${application.company_name}${application.search_job_title ? ` • ${application.search_job_title}` : ''}`
      : 'Saved application details';

    jobRecordNotice.textContent = application.error_message
      ? `${application.error_message} LinkedIn links below are preserved for reference and may still redirect externally.`
      : 'This is the exact saved record for the row you clicked. External LinkedIn links are preserved below, but LinkedIn may redirect them if the original posting changed.';

    renderJobRecordMeta(context);
    renderJobRecordLinks(context);
    renderJobRecordQuestions(Array.isArray(questions) ? questions : []);
  } catch (err) {
    if (requestId !== jobRecordRequestId) return;
    jobRecordTitle.textContent = 'Job record unavailable';
    jobRecordSubtitle.textContent = 'The saved application details could not be loaded.';
    jobRecordNotice.textContent = err.message || 'Failed to load this job record.';
    jobRecordMeta.innerHTML = '';
    jobRecordLinks.innerHTML = '<div class="job-record-empty">No saved links available.</div>';
    jobRecordQuestions.innerHTML = '<div class="job-record-empty">No captured questions available.</div>';
    toast(err.message, 'error');
  }
}

function currentTab() {
  return document.querySelector('.nav-item.active')?.dataset.tab || 'dashboard';
}

async function loadAccounts() {
  state.accounts = await api.getAccounts();
  state.stats = await api.getAccountStats().catch(() => state.stats);
  const running = await api.getRunning().catch(() => ({ running: [...state.runningIds] }));
  state.runningIds = new Set(running.running || []);

  renderStatsBar();
  renderAccountGrid();
  renderAccountsTable();
  populateAccountDropdowns();
}

function upsertAccount(account) {
  if (!account || !account.id) return;

  const idx = state.accounts.findIndex((item) => item.id === account.id);
  if (idx >= 0) state.accounts[idx] = { ...state.accounts[idx], ...account };
  else state.accounts.unshift(account);

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
  await loadQAProfile(document.getElementById('qaProfileAccount')?.value || '');
}

function renderQAProfileInput(field) {
  const control = Array.isArray(field.options) && field.options.length
    ? `
      <select id="${qaProfileInputId(field.key)}">
        ${field.options.map((option) => `<option value="${esc(option)}"${option === (field.default_answer || '') ? ' selected' : ''}>${esc(option)}</option>`).join('')}
      </select>
    `
    : `
      <input
        type="${esc(field.input_type || 'text')}"
        id="${qaProfileInputId(field.key)}"
        placeholder="${esc(field.placeholder || '')}"
        value="${esc(field.default_answer || '')}"
      />
    `;

  return `
    <div class="qa-profile-field">
      <label for="${qaProfileInputId(field.key)}">${esc(field.label)}</label>
      ${control}
      ${field.help_text ? `<small>${esc(field.help_text)}</small>` : ''}
    </div>
  `;
}

function renderQAProfileForm() {
  const container = document.getElementById('qaProfileFields');
  if (!container) return;

  container.innerHTML = chunkItems(QA_PROFILE_FIELDS, 3).map((group) => `
    <div class="form-row qa-profile-row">
      ${group.map((field) => renderQAProfileInput(field)).join('')}
    </div>
  `).join('');
}

function setQAProfileStatus(message) {
  if (qaProfileStatus) qaProfileStatus.textContent = message;
}

function setQAProfileFieldValue(field, value) {
  const input = document.getElementById(qaProfileInputId(field.key));
  if (input) input.value = value ?? '';
}

function buildQAProfileTemplateMap(templates, accountId) {
  const map = {};

  QA_PROFILE_FIELDS.forEach((field) => {
    const matches = (templates || []).filter(
      (template) =>
        getFieldPatterns(field).includes(normalizePattern(template.question_pattern))
        && !template.job_title_scope
    );
    const accountTemplate = matches.find((template) => template.account_id === accountId) || null;
    const fallbackTemplate = accountTemplate || matches.find((template) => !template.account_id) || null;

    map[field.key] = {
      accountTemplate,
      fallbackTemplate,
    };
  });

  return map;
}

async function loadQAProfile(accountId) {
  const selectedAccountId = accountId || '';

  if (!selectedAccountId) {
    state.qaProfileTemplateMap = {};
    QA_PROFILE_FIELDS.forEach((field) => {
      setQAProfileFieldValue(field, field.default_answer || '');
    });
    setQAProfileStatus('Select an account to load the saved answers for that user.');
    return;
  }

  try {
    const templates = await api.getQATemplates(selectedAccountId);
    state.qaProfileTemplateMap = buildQAProfileTemplateMap(templates, selectedAccountId);
    const savedCount = Object.values(state.qaProfileTemplateMap).filter((entry) => entry.accountTemplate).length;

    QA_PROFILE_FIELDS.forEach((field) => {
      const entry = state.qaProfileTemplateMap[field.key] || {};
      const template = entry.fallbackTemplate;
      const value = template?.answer ?? field.default_answer ?? '';

      setQAProfileFieldValue(field, value);
    });
    setQAProfileStatus(savedCount
      ? `Loaded ${savedCount} saved answer${savedCount === 1 ? '' : 's'} for this account. The rest are using the shared defaults.`
      : 'This account is using the shared defaults right now. Save once to create account-specific answers.');
  } catch (err) {
    state.qaProfileTemplateMap = {};
    QA_PROFILE_FIELDS.forEach((field) => {
      setQAProfileFieldValue(field, field.default_answer || '');
    });
    setQAProfileStatus('Could not load saved answers, so the built-in defaults are shown.');
    toast(`Failed to load QA profile: ${err.message}`, 'error');
  }
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
        <td><button class="btn btn-secondary btn-sm" onclick="viewApplicationRecord('${row.id}')">View Record</button></td>
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
          <div style="display:flex;gap:6px;flex-wrap:wrap">
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
  const selectedAccountId = document.getElementById('qaProfileAccount')?.value || '';
  let rows = selectedAccountId
    ? state.qaTemplates.filter((template) => !template.account_id || template.account_id === selectedAccountId)
    : state.qaTemplates;

  if (selectedAccountId) {
    const deduped = new Map();
    rows.forEach((template) => {
      const key = [
        normalizePattern(template.question_pattern),
        normalizePattern(template.job_title_scope),
        normalizePattern(template.field_type),
      ].join('|');
      const existing = deduped.get(key);

      if (!existing || (!existing.account_id && template.account_id === selectedAccountId)) {
        deduped.set(key, template);
      }
    });
    rows = [...deduped.values()];
  }

  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty-state">No templates</td></tr>';
    return;
  }

  tbody.innerHTML = rows.map((template) => `
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
            <button class="btn btn-secondary btn-sm" onclick="viewApplicationRecord('${application.id}')">View Record</button>
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
    tbody.innerHTML = '<tr><td colspan="7" class="empty-state">No pending questions right now.</td></tr>';
    return;
  }

  tbody.innerHTML = state.pendingQuestions.map((question) => {
    return `
      <tr>
        <td>${esc(question.job_title_scope || question.search_job_title || '-')}</td>
        <td>
          <div style="font-weight:600">${esc(question.job_title || '-')}</div>
          <div style="color:var(--text-secondary);font-size:12px">${esc(question.company_name || '-')}</div>
        </td>
        <td>${esc(question.question_text)}</td>
        <td><span class="tag">${esc(question.field_type)}</span></td>
        <td class="pending-question-account-cell">${esc(accountLabel(question.account_id))}</td>
        <td class="pending-question-answer-cell">${pendingAnswerControl(question)}</td>
        <td class="pending-question-action-cell">
          <div class="pending-question-actions">
            <button class="btn btn-primary btn-sm" onclick="answerPendingQuestion('${question.id}')">Save</button>
            <button class="btn btn-secondary btn-sm" onclick="viewApplicationRecord('${question.application_id}')">View Record</button>
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
  const selectors = ['#configAccount', '#qaAccount', '#qaProfileAccount', '#manualAccountFilter', '#logAccountFilter'];
  selectors.forEach((selector) => {
    const el = document.querySelector(selector);
    if (!el) return;
    const first = el.options[0] ? el.options[0].cloneNode(true) : null;
    const previousValue = el.value;
    el.innerHTML = '';
    if (first) el.appendChild(first);
    state.accounts.forEach((acc) => {
      const option = document.createElement('option');
      option.value = acc.id;
      option.textContent = accountLabel(acc.id);
      el.appendChild(option);
    });
    if (previousValue && state.accounts.some((acc) => acc.id === previousValue)) {
      el.value = previousValue;
    }
  });
}

renderQAProfileForm();

document.getElementById('qaProfileAccount').addEventListener('change', async (event) => {
  await loadQAProfile(event.target.value);
  renderQATable();
});

document.getElementById('saveQAProfileBtn').addEventListener('click', async () => {
  const accountId = document.getElementById('qaProfileAccount').value;
  if (!accountId) return toast('Select an account before saving the QA profile', 'error');

  const missing = QA_PROFILE_FIELDS.filter(
    (field) => field.required && !document.getElementById(qaProfileInputId(field.key))?.value?.trim()
  );
  if (missing.length) {
    return toast(`Please fill ${missing[0].label} before saving`, 'error');
  }

  try {
    const requests = QA_PROFILE_FIELDS.map((field) => {
      const answer = document.getElementById(qaProfileInputId(field.key)).value.trim();
      const existing = state.qaProfileTemplateMap[field.key]?.accountTemplate || null;
      const payload = {
        account_id: accountId,
        question_pattern: field.question_pattern,
        answer,
        field_type: field.field_type,
        priority: field.priority,
        job_title_scope: null,
      };

      return existing
        ? api.updateQATemplate(existing.id, payload)
        : api.createQATemplate(payload);
    });

    await Promise.all(requests);
    await loadQATemplates();
    await loadQAProfile(accountId);
    toast('QA profile saved for this account', 'success');
  } catch (err) {
    toast(err.message, 'error');
  }
});

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
    upsertAccount(account);

    if (resumeFile) {
      const resumeData = await api.uploadResume(account.id, resumeFile);
      upsertAccount({ ...account, resume_path: resumeData.resume_path });
    }

    document.getElementById('accountForm').classList.add('hidden');
    document.getElementById('accLabel').value = '';
    document.getElementById('accEmail').value = '';
    document.getElementById('accPassword').value = '';
    document.getElementById('accResume').value = '';
    toast('Account created.', 'success');
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
  } finally {
    input.value = '';
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
      toast('Selected pending application queued. The worker started and will retry this one first.', 'success');
      return;
    }

    if (result.workerAlreadyRunning) {
      toast('Application marked ready. The current worker keeps its existing queue, so this one will retry on the next run.', 'info');
      return;
    }

    toast('Application marked ready for retry.', 'info');
  } catch (err) {
    toast(err.message, 'error');
  }
};

window.viewApplicationRecord = viewApplicationRecord;

closeJobRecordBtn.addEventListener('click', closeJobRecordModal);

jobRecordModal.addEventListener('click', (event) => {
  if (event.target === jobRecordModal) {
    closeJobRecordModal();
  }
});

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !jobRecordModal.classList.contains('hidden')) {
    closeJobRecordModal();
  }
});

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
