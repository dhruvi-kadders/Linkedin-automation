// backend/src/api/routes.js
// All REST API endpoints + SSE stream for real-time updates

const express = require('express');
const multer  = require('multer');
const path    = require('path');
const fs      = require('fs');
const router  = express.Router();

const db      = require('../db');
const manager = require('../bot/worker-manager');

// ── File upload (resumes) ─────────────────────────────────────────────────────
const UPLOAD_DIR = path.join(__dirname, '../../uploads/resumes');
fs.mkdirSync(UPLOAD_DIR, { recursive: true });

const storage = multer.diskStorage({
  destination: (_req, _file, cb) => cb(null, UPLOAD_DIR),
  filename: (_req, file, cb) => {
    const ext  = path.extname(file.originalname);
    const name = `${Date.now()}-${Math.round(Math.random() * 1e9)}${ext}`;
    cb(null, name);
  },
});
const upload = multer({
  storage,
  limits: { fileSize: 5 * 1024 * 1024 }, // 5MB
  fileFilter: (_req, file, cb) => {
    const allowed = ['.pdf', '.doc', '.docx'];
    if (allowed.includes(path.extname(file.originalname).toLowerCase())) cb(null, true);
    else cb(new Error('Only PDF/DOC/DOCX files allowed'));
  },
});

// ── SSE clients registry ──────────────────────────────────────────────────────
const sseClients = new Set();

// Relay all worker events to SSE clients
manager.on('worker:message', (msg) => {
  const data = JSON.stringify(msg);
  sseClients.forEach((res) => {
    try { res.write(`data: ${data}\n\n`); } catch { sseClients.delete(res); }
  });
});

// ── SSE endpoint ──────────────────────────────────────────────────────────────
router.get('/events', (req, res) => {
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.flushHeaders();

  // Keep alive ping every 25s
  const ping = setInterval(() => {
    try { res.write(': ping\n\n'); } catch { clearInterval(ping); }
  }, 25000);

  sseClients.add(res);

  req.on('close', () => {
    clearInterval(ping);
    sseClients.delete(res);
  });
});

// ── ACCOUNTS ──────────────────────────────────────────────────────────────────
router.get('/accounts', async (_req, res) => {
  const result = await db.accounts.getAll();
  // Don't send passwords to frontend
  const safe = result.rows.map(({ password, session_data, ...rest }) => rest);
  res.json(safe);
});

router.get('/accounts/stats', async (_req, res) => {
  const result = await db.accounts.getStats();
  res.json(result.rows);
});

router.post('/accounts', async (req, res) => {
  const { label, email, password } = req.body;
  if (!email || !password) return res.status(400).json({ error: 'email and password required' });
  const result = await db.accounts.create({ label, email, password, resume_path: null });
  const { password: _p, session_data: _s, ...safe } = result.rows[0];
  res.status(201).json(safe);
});

router.put('/accounts/:id', async (req, res) => {
  const { label, email, password } = req.body;
  const fields = {};
  if (label)    fields.label    = label;
  if (email)    fields.email    = email;
  if (password) fields.password = password;
  const result = await db.accounts.update(req.params.id, fields);
  res.json(result.rows[0]);
});

router.delete('/accounts/:id', async (req, res) => {
  await db.accounts.delete(req.params.id);
  res.json({ deleted: true });
});

// Resume upload
router.post('/accounts/:id/resume', upload.single('resume'), async (req, res) => {
  if (!req.file) return res.status(400).json({ error: 'No file uploaded' });
  const resumePath = req.file.path;
  await db.accounts.update(req.params.id, { resume_path: resumePath });
  res.json({ resume_path: resumePath, filename: req.file.originalname });
});

// ── BOT CONTROL ───────────────────────────────────────────────────────────────
router.post('/bot/start', async (req, res) => {
  const { accountIds } = req.body; // array of UUIDs
  if (!accountIds?.length) return res.status(400).json({ error: 'accountIds required' });

  const results = manager.startMany(accountIds);
  res.json({ results, running: manager.getRunning() });
});

router.post('/bot/start/:accountId', async (req, res) => {
  try {
    manager.start(req.params.accountId);
    res.json({ started: true, accountId: req.params.accountId });
  } catch (err) {
    res.status(400).json({ error: err.message });
  }
});

router.post('/bot/stop/:accountId', async (req, res) => {
  const stopped = await manager.stop(req.params.accountId);
  if (stopped) await db.accounts.updateStatus(req.params.accountId, 'idle');
  res.json({ stopped });
});

router.post('/bot/stop-all', async (_req, res) => {
  await manager.stopAll();
  res.json({ stopped: true });
});

router.get('/bot/running', (_req, res) => {
  res.json({ running: manager.getRunning() });
});

// ── SEARCH CONFIGS ────────────────────────────────────────────────────────────
router.get('/accounts/:accountId/search-configs', async (req, res) => {
  const result = await db.searchConfigs.getByAccount(req.params.accountId);
  res.json(result.rows);
});

router.post('/accounts/:accountId/search-configs', async (req, res) => {
  const data = { ...req.body, account_id: req.params.accountId };
  const result = await db.searchConfigs.create(data);
  res.status(201).json(result.rows[0]);
});

router.put('/search-configs/:id', async (req, res) => {
  const result = await db.searchConfigs.update(req.params.id, req.body);
  res.json(result.rows[0]);
});

router.delete('/search-configs/:id', async (req, res) => {
  await db.searchConfigs.delete(req.params.id);
  res.json({ deleted: true });
});

// ── APPLICATIONS ──────────────────────────────────────────────────────────────
router.get('/accounts/:accountId/applications', async (req, res) => {
  const { status, is_easy_apply, limit } = req.query;
  const filters = {};
  if (status) filters.status = status;
  if (is_easy_apply !== undefined) filters.is_easy_apply = is_easy_apply === 'true';
  if (limit) filters.limit = parseInt(limit);
  const result = await db.applications.getByAccount(req.params.accountId, filters);
  res.json(result.rows);
});

router.get('/accounts/:accountId/applications/manual-review', async (req, res) => {
  const result = await db.applications.getManualReview(req.params.accountId);
  res.json(result.rows);
});

router.get('/applications/:applicationId/questions', async (req, res) => {
  const includeAnswered = req.query.includeAnswered !== 'false';
  const result = await db.applicationQuestions.getByApplication(req.params.applicationId, includeAnswered);
  res.json(result.rows);
});

router.get('/pending-questions', async (req, res) => {
  const accountId = req.query.accountId || null;
  const result = await db.applicationQuestions.getPending(accountId);
  res.json(result.rows);
});

router.get('/pending-applications', async (req, res) => {
  const accountId = req.query.accountId || null;
  const result = await db.applications.getPendingApplications(accountId);
  res.json(result.rows);
});

router.post('/pending-questions/:id/answer', async (req, res) => {
  const answer = String(req.body.answer || '').trim();
  const priority = parseInt(req.body.priority, 10) || 10;

  if (!answer) {
    return res.status(400).json({ error: 'answer required' });
  }

  const questionRes = await db.applicationQuestions.getById(req.params.id);
  const question = questionRes.rows[0];
  if (!question) {
    return res.status(404).json({ error: 'Pending question not found' });
  }

  const jobTitleScope = question.job_title_scope || question.search_job_title || null;
  const impactedApplicationIds = await db.applicationQuestions.answerMatchingScope({
    account_id: question.account_id,
    question_text: question.question_text,
    job_title_scope: jobTitleScope,
    answer,
  });

  const templateRes = await db.qaTemplates.upsertScoped({
    account_id: question.account_id,
    question_pattern: question.question_text,
    answer,
    field_type: question.field_type || 'text',
    priority,
    job_title_scope: jobTitleScope,
  });

  const applicationStatuses = [];
  for (const applicationId of impactedApplicationIds) {
    const status = await db.applications.markReadyToRetryIfComplete(applicationId);
    applicationStatuses.push({ applicationId, status });
  }

  res.json({
    saved: true,
    template: templateRes.rows[0],
    applicationStatuses,
  });
});

// ── Q&A TEMPLATES ─────────────────────────────────────────────────────────────
router.post('/applications/:applicationId/retry', async (req, res) => {
  const appRes = await db.applications.getById(req.params.applicationId);
  const application = appRes.rows[0];

  if (!application) {
    return res.status(404).json({ error: 'Application not found' });
  }

  const status = await db.applications.markReadyToRetryIfComplete(application.id);
  if (status !== 'ready_to_retry') {
    return res.status(400).json({
      error: 'This application still has unanswered required questions',
      status,
    });
  }

  let workerStarted = false;
  let workerAlreadyRunning = false;

  if (manager.isRunning(application.account_id)) {
    workerAlreadyRunning = true;
  } else {
    try {
      manager.start(application.account_id);
    } catch (err) {
      return res.status(400).json({ error: err.message });
    }
    workerStarted = true;
  }

  res.json({
    queued: true,
    status,
    accountId: application.account_id,
    workerStarted,
    workerAlreadyRunning,
  });
});

router.get('/qa-templates', async (req, res) => {
  const { accountId } = req.query;
  const result = accountId
    ? await db.qaTemplates.getForAccount(accountId)
    : await db.query('SELECT * FROM qa_templates ORDER BY priority DESC');
  res.json(result.rows);
});

router.post('/qa-templates', async (req, res) => {
  const result = await db.qaTemplates.create(req.body);
  res.status(201).json(result.rows[0]);
});

router.put('/qa-templates/:id', async (req, res) => {
  const result = await db.qaTemplates.update(req.params.id, req.body);
  res.json(result.rows[0]);
});

router.delete('/qa-templates/:id', async (req, res) => {
  await db.qaTemplates.delete(req.params.id);
  res.json({ deleted: true });
});

// ── LOGS ──────────────────────────────────────────────────────────────────────
router.get('/logs', async (req, res) => {
  const { accountId, limit } = req.query;
  const result = accountId
    ? await db.logs.getByAccount(accountId, parseInt(limit) || 200)
    : await db.logs.getRecent(parseInt(limit) || 500);
  res.json(result.rows);
});

module.exports = router;
