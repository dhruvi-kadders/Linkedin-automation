// backend/src/db/index.js
// Central PostgreSQL connection pool + all query helpers.

const { Pool } = require('pg');
const path = require('path');
require('dotenv').config({ path: path.join(__dirname, '../../config/.env') });

const pool = new Pool({
  host: process.env.DB_HOST || 'localhost',
  port: parseInt(process.env.DB_PORT, 10) || 5432,
  database: process.env.DB_NAME || 'linkedin_bot',
  user: process.env.DB_USER || 'postgres',
  password: process.env.DB_PASSWORD || 'postgres',
  max: 20,
  idleTimeoutMillis: 30000,
  connectionTimeoutMillis: 2000,
});

pool.on('error', (err) => {
  console.error('Unexpected DB pool error', err);
});

const initPromise = (async () => {
  await pool.query(`CREATE EXTENSION IF NOT EXISTS "uuid-ossp"`);

  await pool.query(`
    ALTER TABLE qa_templates
    ADD COLUMN IF NOT EXISTS job_title_scope VARCHAR(255)
  `);

  await pool.query(`
    CREATE TABLE IF NOT EXISTS application_questions (
      id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
      application_id UUID REFERENCES job_applications(id) ON DELETE CASCADE,
      account_id UUID REFERENCES accounts(id) ON DELETE CASCADE,
      search_config_id UUID REFERENCES search_configs(id) ON DELETE SET NULL,
      question_text TEXT NOT NULL,
      field_type VARCHAR(30) DEFAULT 'text',
      options JSONB,
      answer TEXT,
      is_required BOOLEAN DEFAULT FALSE,
      is_answered BOOLEAN DEFAULT FALSE,
      step_index INT DEFAULT 1,
      job_title_scope VARCHAR(255),
      job_title VARCHAR(255),
      company_name VARCHAR(255),
      created_at TIMESTAMPTZ DEFAULT NOW(),
      updated_at TIMESTAMPTZ DEFAULT NOW()
    )
  `);

  await pool.query(`
    CREATE UNIQUE INDEX IF NOT EXISTS idx_application_questions_unique
    ON application_questions(application_id, question_text, step_index)
  `);

  await pool.query(`
    CREATE INDEX IF NOT EXISTS idx_application_questions_account
    ON application_questions(account_id, created_at DESC)
  `);

  await pool.query(`
    CREATE INDEX IF NOT EXISTS idx_application_questions_application
    ON application_questions(application_id)
  `);

  await pool.query(`
    CREATE OR REPLACE VIEW application_stats AS
    SELECT
      a.id AS account_id,
      a.label,
      a.email,
      a.status AS account_status,
      COUNT(ja.id) FILTER (WHERE ja.status = 'applied') AS total_applied,
      COUNT(ja.id) FILTER (WHERE ja.status = 'pending') AS total_pending,
      COUNT(ja.id) FILTER (WHERE ja.status = 'failed') AS total_failed,
      COUNT(ja.id) FILTER (WHERE ja.is_easy_apply = FALSE AND ja.status != 'applied') AS manual_review_count,
      MAX(ja.applied_at) AS last_applied_at,
      COUNT(ja.id) FILTER (WHERE ja.status = 'pending_questions') AS pending_questions_count
    FROM accounts a
    LEFT JOIN job_applications ja ON ja.account_id = a.id
    GROUP BY a.id, a.label, a.email, a.status
  `);
})();

const query = async (text, params) => {
  await initPromise;
  return pool.query(text, params);
};

const accounts = {
  getAll: () => query(`SELECT * FROM accounts ORDER BY created_at`),

  getById: (id) => query(`SELECT * FROM accounts WHERE id = $1`, [id]),

  create: ({ label, email, password, resume_path }) =>
    query(
      `INSERT INTO accounts (label, email, password, resume_path)
       VALUES ($1, $2, $3, $4)
       RETURNING *`,
      [label, email, password, resume_path]
    ),

  update: (id, fields) => {
    const keys = Object.keys(fields);
    const values = Object.values(fields);
    const set = keys.map((k, i) => `${k} = $${i + 2}`).join(', ');
    return query(
      `UPDATE accounts
       SET ${set}, updated_at = NOW()
       WHERE id = $1
       RETURNING *`,
      [id, ...values]
    );
  },

  updateStatus: (id, status) =>
    query(
      `UPDATE accounts
       SET status = $2, updated_at = NOW()
       WHERE id = $1`,
      [id, status]
    ),

  saveSession: (id, sessionData) =>
    query(
      `UPDATE accounts
       SET session_data = $2, updated_at = NOW()
       WHERE id = $1`,
      [id, JSON.stringify(sessionData)]
    ),

  getSession: async (id) => {
    const res = await query(`SELECT session_data FROM accounts WHERE id = $1`, [id]);
    return res.rows[0]?.session_data || null;
  },

  delete: (id) => query(`DELETE FROM accounts WHERE id = $1`, [id]),

  getStats: () => query(`SELECT * FROM application_stats ORDER BY label`),
};

const searchConfigs = {
  getByAccount: (accountId) =>
    query(`SELECT * FROM search_configs WHERE account_id = $1 AND active = TRUE`, [accountId]),

  create: (data) => {
    const {
      account_id,
      job_title,
      location,
      remote_only = false,
      easy_apply_only = true,
      max_applications = 50,
      date_posted = 'past_week',
      experience_level = [],
      job_type = [],
    } = data;

    return query(
      `INSERT INTO search_configs
         (account_id, job_title, location, remote_only, easy_apply_only,
          max_applications, date_posted, experience_level, job_type)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
       RETURNING *`,
      [
        account_id,
        job_title,
        location,
        remote_only,
        easy_apply_only,
        max_applications,
        date_posted,
        experience_level,
        job_type,
      ]
    );
  },

  update: (id, fields) => {
    const keys = Object.keys(fields);
    const values = Object.values(fields);
    const set = keys.map((k, i) => `${k} = $${i + 2}`).join(', ');
    return query(
      `UPDATE search_configs
       SET ${set}
       WHERE id = $1
       RETURNING *`,
      [id, ...values]
    );
  },

  delete: (id) => query(`DELETE FROM search_configs WHERE id = $1`, [id]),
};

const applications = {
  getById: (id) => query(`SELECT * FROM job_applications WHERE id = $1`, [id]),

  findByUrl: (accountId, jobUrl) =>
    query(
      `SELECT *
       FROM job_applications
       WHERE account_id = $1 AND job_url = $2
       ORDER BY created_at DESC
       LIMIT 1`,
      [accountId, jobUrl]
    ),

  create: async (data) => {
    const {
      account_id,
      search_config_id,
      job_url,
      job_title,
      company_name,
      location,
      is_easy_apply = false,
      status = 'pending',
    } = data;

    const existing = await query(
      `SELECT *
       FROM job_applications
       WHERE account_id = $1 AND job_url = $2
       ORDER BY created_at DESC
       LIMIT 1`,
      [account_id, job_url]
    );

    if (existing.rows[0]) return existing;

    return query(
      `INSERT INTO job_applications
         (account_id, search_config_id, job_url, job_title,
          company_name, location, is_easy_apply, status)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
       RETURNING *`,
      [
        account_id,
        search_config_id,
        job_url,
        job_title,
        company_name,
        location,
        is_easy_apply,
        status,
      ]
    );
  },

  updateStatus: (id, status, errorMessage = null) =>
    query(
      `UPDATE job_applications
       SET status = $2::varchar,
           error_message = $3,
           applied_at = CASE WHEN $2::text = 'applied' THEN NOW() ELSE applied_at END
       WHERE id = $1`,
      [id, status, errorMessage]
    ),

  getByAccount: (accountId, filters = {}) => {
    let q = `SELECT * FROM job_applications WHERE account_id = $1`;
    const params = [accountId];

    if (filters.status) {
      params.push(filters.status);
      q += ` AND status = $${params.length}`;
    }

    if (filters.is_easy_apply !== undefined) {
      params.push(filters.is_easy_apply);
      q += ` AND is_easy_apply = $${params.length}`;
    }

    q += ` ORDER BY created_at DESC LIMIT ${filters.limit || 100}`;
    return query(q, params);
  },

  getManualReview: (accountId) =>
    query(
      `SELECT *
       FROM job_applications
       WHERE account_id = $1 AND is_easy_apply = FALSE
       ORDER BY created_at DESC`,
      [accountId]
    ),

  getPendingApplications: (accountId = null) =>
    query(
      `SELECT
         ja.*,
         a.label AS account_label,
         a.email AS account_email,
         sc.job_title AS search_job_title,
         COUNT(aq.id) FILTER (
           WHERE aq.is_required = TRUE
             AND COALESCE(BTRIM(aq.answer), '') = ''
         ) AS missing_required_count,
         COUNT(aq.id) FILTER (
           WHERE COALESCE(BTRIM(aq.answer), '') != ''
         ) AS answered_count
       FROM job_applications ja
       LEFT JOIN application_questions aq ON aq.application_id = ja.id
       LEFT JOIN accounts a ON a.id = ja.account_id
       LEFT JOIN search_configs sc ON sc.id = ja.search_config_id
       WHERE ja.status IN ('pending_questions', 'ready_to_retry')
         AND ($1::uuid IS NULL OR ja.account_id = $1)
       GROUP BY ja.id, a.label, a.email, sc.job_title
       ORDER BY ja.created_at DESC`,
      [accountId]
    ),

  existsByUrl: async (accountId, jobUrl) => {
    const res = await query(
      `SELECT id
       FROM job_applications
       WHERE account_id = $1 AND job_url = $2
       LIMIT 1`,
      [accountId, jobUrl]
    );
    return res.rows.length > 0;
  },

  getRetryQueue: (accountId, searchConfigId = null) =>
    query(
      `SELECT
         ja.*,
         COALESCE(sc.job_title, ja.job_title) AS job_role
       FROM job_applications ja
       LEFT JOIN search_configs sc ON sc.id = ja.search_config_id
       WHERE ja.account_id = $1
         AND ($2::uuid IS NULL OR ja.search_config_id = $2)
         AND ja.status IN ('ready_to_retry', 'pending_questions')
         AND EXISTS (
           SELECT 1
           FROM application_questions aq
           WHERE aq.application_id = ja.id
         )
         AND NOT EXISTS (
           SELECT 1
           FROM application_questions aq
           WHERE aq.application_id = ja.id
             AND aq.is_required = TRUE
             AND COALESCE(BTRIM(aq.answer), '') = ''
         )
       ORDER BY ja.created_at ASC`,
      [accountId, searchConfigId]
    ),

  markReadyToRetryIfComplete: async (applicationId) => {
    const res = await query(
      `SELECT COUNT(*)::int AS missing_count
       FROM application_questions
       WHERE application_id = $1
         AND is_required = TRUE
         AND COALESCE(BTRIM(answer), '') = ''`,
      [applicationId]
    );

    const status = res.rows[0]?.missing_count > 0 ? 'pending_questions' : 'ready_to_retry';
    await query(
      `UPDATE job_applications
       SET status = $2::varchar
       WHERE id = $1`,
      [applicationId, status]
    );
    return status;
  },
};

const qaTemplates = {
  getForAccount: (accountId) =>
    query(
      `SELECT *
       FROM qa_templates
       WHERE (account_id = $1 OR account_id IS NULL)
         AND answer IS NOT NULL
         AND BTRIM(answer) != ''
       ORDER BY
         CASE WHEN job_title_scope IS NULL THEN 1 ELSE 0 END,
         account_id NULLS LAST,
         priority DESC`,
      [accountId]
    ),

  create: (data) => {
    const {
      account_id,
      question_pattern,
      answer,
      field_type = 'text',
      priority = 0,
      job_title_scope = null,
    } = data;

    return query(
      `INSERT INTO qa_templates
         (account_id, question_pattern, answer, field_type, priority, job_title_scope)
       VALUES ($1,$2,$3,$4,$5,$6)
       RETURNING *`,
      [account_id || null, question_pattern, answer, field_type, priority, job_title_scope]
    );
  },

  update: (id, fields) => {
    const keys = Object.keys(fields);
    const values = Object.values(fields);
    const set = keys.map((k, i) => `${k} = $${i + 2}`).join(', ');
    return query(
      `UPDATE qa_templates
       SET ${set}
       WHERE id = $1
       RETURNING *`,
      [id, ...values]
    );
  },

  upsertScoped: async (data) => {
    const {
      account_id,
      question_pattern,
      answer,
      field_type = 'text',
      priority = 10,
      job_title_scope = null,
    } = data;

    const existing = await query(
      `SELECT *
       FROM qa_templates
       WHERE account_id IS NOT DISTINCT FROM $1
         AND LOWER(question_pattern) = LOWER($2)
         AND COALESCE(LOWER(job_title_scope), '') = COALESCE(LOWER($3), '')
       ORDER BY priority DESC
       LIMIT 1`,
      [account_id || null, question_pattern, job_title_scope]
    );

    if (existing.rows[0]) {
      return query(
        `UPDATE qa_templates
         SET answer = $2,
             field_type = $3,
             priority = $4,
             job_title_scope = $5
         WHERE id = $1
         RETURNING *`,
        [existing.rows[0].id, answer, field_type, priority, job_title_scope]
      );
    }

    return qaTemplates.create({
      account_id,
      question_pattern,
      answer,
      field_type,
      priority,
      job_title_scope,
    });
  },

  delete: (id) => query(`DELETE FROM qa_templates WHERE id = $1`, [id]),
};

const applicationQuestions = {
  upsertMany: async (applicationId, meta, questions) => {
    const rows = [];

    for (const question of questions) {
      if (!question.question_text) continue;

      const res = await query(
        `INSERT INTO application_questions
           (application_id, account_id, search_config_id, question_text, field_type,
            options, answer, is_required, is_answered, step_index,
            job_title_scope, job_title, company_name, updated_at)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,NOW())
         ON CONFLICT (application_id, question_text, step_index)
         DO UPDATE SET
           account_id = EXCLUDED.account_id,
           search_config_id = EXCLUDED.search_config_id,
           field_type = EXCLUDED.field_type,
           options = EXCLUDED.options,
           answer = EXCLUDED.answer,
           is_required = EXCLUDED.is_required,
           is_answered = EXCLUDED.is_answered,
           job_title_scope = EXCLUDED.job_title_scope,
           job_title = EXCLUDED.job_title,
           company_name = EXCLUDED.company_name,
           updated_at = NOW()
         RETURNING *`,
        [
          applicationId,
          meta.account_id,
          meta.search_config_id,
          String(question.question_text).trim(),
          question.field_type || 'text',
          question.options ? JSON.stringify(question.options) : null,
          question.answer || null,
          !!question.is_required,
          !!question.is_answered,
          question.step_index || meta.step_index || 1,
          meta.job_title_scope || null,
          meta.job_title || null,
          meta.company_name || null,
        ]
      );

      if (res.rows[0]) rows.push(res.rows[0]);
    }

    return { rows };
  },

  getById: (id) =>
    query(
      `SELECT
         aq.*,
         ja.job_url,
         ja.status AS application_status,
         sc.job_title AS search_job_title
       FROM application_questions aq
       JOIN job_applications ja ON ja.id = aq.application_id
       LEFT JOIN search_configs sc ON sc.id = aq.search_config_id
       WHERE aq.id = $1`,
      [id]
    ),

  getByApplication: (applicationId, includeAnswered = true) =>
    query(
      `SELECT *
       FROM application_questions
       WHERE application_id = $1
         AND ($2::boolean = TRUE OR COALESCE(BTRIM(answer), '') = '')
       ORDER BY step_index ASC, updated_at ASC, created_at ASC`,
      [applicationId, includeAnswered]
    ),

  getPending: (accountId = null) =>
    query(
      `SELECT
         aq.*,
         ja.job_url,
         ja.status AS application_status,
         sc.job_title AS search_job_title
       FROM application_questions aq
       JOIN job_applications ja ON ja.id = aq.application_id
       LEFT JOIN search_configs sc ON sc.id = aq.search_config_id
       WHERE aq.is_required = TRUE
         AND COALESCE(BTRIM(aq.answer), '') = ''
         AND ($1::uuid IS NULL OR aq.account_id = $1)
       ORDER BY aq.updated_at DESC, aq.step_index DESC, aq.created_at DESC`,
      [accountId]
    ),

  answer: (id, answer) =>
    query(
      `UPDATE application_questions
       SET answer = $2,
           is_answered = CASE WHEN COALESCE(BTRIM($2), '') != '' THEN TRUE ELSE FALSE END,
           updated_at = NOW()
       WHERE id = $1
       RETURNING *`,
      [id, answer]
    ),

  answerMatchingScope: async ({ account_id, question_text, job_title_scope, answer }) => {
    const impacted = await query(
      `SELECT DISTINCT application_id
       FROM application_questions
       WHERE account_id = $1
         AND LOWER(question_text) = LOWER($2)
         AND COALESCE(LOWER(job_title_scope), '') = COALESCE(LOWER($3), '')
         AND is_required = TRUE
         AND COALESCE(BTRIM(answer), '') = ''`,
      [account_id, question_text, job_title_scope]
    );

    await query(
      `UPDATE application_questions
       SET answer = $4,
           is_answered = CASE WHEN COALESCE(BTRIM($4), '') != '' THEN TRUE ELSE FALSE END,
           updated_at = NOW()
       WHERE account_id = $1
         AND LOWER(question_text) = LOWER($2)
         AND COALESCE(LOWER(job_title_scope), '') = COALESCE(LOWER($3), '')
         AND is_required = TRUE`,
      [account_id, question_text, job_title_scope, answer]
    );

    return impacted.rows.map((row) => row.application_id);
  },
};

const logs = {
  insert: (accountId, level, message, metadata = null) =>
    query(
      `INSERT INTO bot_logs (account_id, level, message, metadata)
       VALUES ($1,$2,$3,$4)`,
      [accountId, level, message, metadata ? JSON.stringify(metadata) : null]
    ),

  getByAccount: (accountId, limit = 200) =>
    query(
      `SELECT *
       FROM bot_logs
       WHERE account_id = $1
       ORDER BY created_at DESC
       LIMIT $2`,
      [accountId, limit]
    ),

  getRecent: (limit = 500) =>
    query(
      `SELECT bl.*, a.label, a.email
       FROM bot_logs bl
       LEFT JOIN accounts a ON a.id = bl.account_id
       ORDER BY bl.created_at DESC
       LIMIT $1`,
      [limit]
    ),
};

module.exports = {
  pool,
  query,
  accounts,
  searchConfigs,
  applications,
  qaTemplates,
  applicationQuestions,
  logs,
};
