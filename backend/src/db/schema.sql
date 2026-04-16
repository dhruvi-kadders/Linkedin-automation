-- LinkedIn Bot Database Schema
-- Run: psql -U postgres -d linkedin_bot -f schema.sql

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ─────────────────────────────────────────
-- ACCOUNTS
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS accounts (
  id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  label         VARCHAR(100),                        -- friendly name e.g. "Account 1"
  email         VARCHAR(255) NOT NULL UNIQUE,
  password      TEXT NOT NULL,                       -- store encrypted in production
  resume_path   TEXT,                                -- path to uploaded resume file
  status        VARCHAR(30) DEFAULT 'idle',          -- idle | running | paused | error
  session_data  JSONB,                               -- saved playwright storage state
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  updated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────
-- SEARCH CONFIGS (job role + location combos)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS search_configs (
  id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  account_id      UUID REFERENCES accounts(id) ON DELETE CASCADE,
  job_title       VARCHAR(255) NOT NULL,
  location        VARCHAR(255),
  remote_only     BOOLEAN DEFAULT FALSE,
  easy_apply_only BOOLEAN DEFAULT TRUE,
  max_applications INT DEFAULT 50,
  date_posted     VARCHAR(30) DEFAULT 'past_week',   -- any_time | past_month | past_week | past_24h
  experience_level VARCHAR(50)[],                    -- entry | associate | mid | senior | director
  job_type        VARCHAR(50)[],                     -- full_time | part_time | contract | internship
  active          BOOLEAN DEFAULT TRUE,
  created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────
-- JOB APPLICATIONS
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS job_applications (
  id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  account_id      UUID REFERENCES accounts(id) ON DELETE CASCADE,
  search_config_id UUID REFERENCES search_configs(id) ON DELETE SET NULL,
  job_url         TEXT NOT NULL,
  job_title       VARCHAR(255),
  company_name    VARCHAR(255),
  location        VARCHAR(255),
  is_easy_apply   BOOLEAN DEFAULT FALSE,
  status          VARCHAR(30) DEFAULT 'pending',     -- pending | applied | failed | skipped | manual_review
  error_message   TEXT,
  applied_at      TIMESTAMPTZ,
  created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast lookups
CREATE INDEX IF NOT EXISTS idx_applications_account ON job_applications(account_id);
CREATE INDEX IF NOT EXISTS idx_applications_status ON job_applications(status);
CREATE INDEX IF NOT EXISTS idx_applications_easy_apply ON job_applications(is_easy_apply);

-- ─────────────────────────────────────────
-- Q&A TEMPLATES (common application questions)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS qa_templates (
  id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  account_id  UUID REFERENCES accounts(id) ON DELETE CASCADE,  -- NULL = global template
  question_pattern  TEXT NOT NULL,    -- substring/regex to match against question text
  answer      TEXT NOT NULL,
  field_type  VARCHAR(30) DEFAULT 'text',   -- text | number | boolean | select | multiselect
  priority    INT DEFAULT 0,               -- higher = matched first
  job_title_scope VARCHAR(255),            -- optional role-specific scope, e.g. "Software Engineer"
  created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Default global Q&A templates (account_id = NULL)
INSERT INTO qa_templates (account_id, question_pattern, answer, field_type, priority) VALUES
  (NULL, 'years of experience', '3', 'number', 10),
  (NULL, 'legally authorized', 'Yes', 'select', 10),
  (NULL, 'require sponsorship', 'No', 'select', 10),
  (NULL, 'work remotely', 'Yes', 'select', 5),
  (NULL, 'notice period', '30 days', 'text', 5),
  (NULL, 'expected salary', '80000', 'number', 5),
  (NULL, 'current salary', '70000', 'number', 5),
  (NULL, 'relocate', 'Yes', 'select', 5),
  (NULL, 'gender', 'Prefer not to say', 'select', 3),
  (NULL, 'veteran', 'I am not a veteran', 'select', 3),
  (NULL, 'disability', 'I don''t wish to answer', 'select', 3),
  (NULL, 'race', 'Prefer not to say', 'select', 3)
ON CONFLICT DO NOTHING;

-- Track every question encountered during an application so unanswered
-- required fields can be surfaced and answered later.
CREATE TABLE IF NOT EXISTS application_questions (
  id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  application_id  UUID REFERENCES job_applications(id) ON DELETE CASCADE,
  account_id      UUID REFERENCES accounts(id) ON DELETE CASCADE,
  search_config_id UUID REFERENCES search_configs(id) ON DELETE SET NULL,
  question_text   TEXT NOT NULL,
  field_type      VARCHAR(30) DEFAULT 'text',
  options         JSONB,
  answer          TEXT,
  is_required     BOOLEAN DEFAULT FALSE,
  is_answered     BOOLEAN DEFAULT FALSE,
  step_index      INT DEFAULT 1,
  job_title_scope VARCHAR(255),
  job_title       VARCHAR(255),
  company_name    VARCHAR(255),
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_application_questions_unique
  ON application_questions(application_id, question_text, step_index);
CREATE INDEX IF NOT EXISTS idx_application_questions_account
  ON application_questions(account_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_application_questions_application
  ON application_questions(application_id);

-- ─────────────────────────────────────────
-- BOT LOGS
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bot_logs (
  id          BIGSERIAL PRIMARY KEY,
  account_id  UUID REFERENCES accounts(id) ON DELETE CASCADE,
  level       VARCHAR(10) DEFAULT 'info',   -- info | warn | error | debug
  message     TEXT NOT NULL,
  metadata    JSONB,
  created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_logs_account ON bot_logs(account_id);
CREATE INDEX IF NOT EXISTS idx_logs_created ON bot_logs(created_at DESC);

-- ─────────────────────────────────────────
-- STATS VIEW
-- ─────────────────────────────────────────
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
GROUP BY a.id, a.label, a.email, a.status;
