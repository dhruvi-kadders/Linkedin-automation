// backend/src/bot/worker.js
// Runs inside a worker_thread - one per LinkedIn account.
// Flow: login -> for each search config -> click cards -> apply from right pane -> repeat

const { workerData, parentPort } = require('worker_threads');
const { createSession } = require('./linkedin-auth');
const { searchJobs } = require('./linkedin-search');
const { applyToJob } = require('./easy-apply');
const { accounts, searchConfigs, qaTemplates, applications } = require('../db');
const { sleep, randomInt } = require('../utils/humanize');

const { accountId } = workerData;

const emit = (type, payload) => parentPort.postMessage({ type, accountId, payload });

const logger = {
  info: (msg, meta) => {
    console.log(`[INFO][${accountId}] ${msg}`);
    emit('log', { level: 'info', message: msg, meta });
  },
  warn: (msg, meta) => {
    console.warn(`[WARN][${accountId}] ${msg}`);
    emit('log', { level: 'warn', message: msg, meta });
  },
  error: (msg, meta) => {
    console.error(`[ERR][${accountId}] ${msg}`);
    emit('log', { level: 'error', message: msg, meta });
  },
  debug: (msg, meta) => {
    emit('log', { level: 'debug', message: msg, meta });
  },
};

(async () => {
  emit('status', { status: 'starting' });

  try {
    const accRes = await accounts.getById(accountId);
    const account = accRes.rows[0];
    if (!account) throw new Error(`Account ${accountId} not found in DB`);

    await accounts.updateStatus(accountId, 'running');
    emit('status', { status: 'running' });

    const tplRes = await qaTemplates.getForAccount(accountId);
    const templates = tplRes.rows;
    logger.info(`Loaded ${templates.length} Q&A templates`);

    const { browser, page, loggedIn } = await createSession(account, logger);

    if (!loggedIn) {
      emit('status', { status: 'error', message: 'Login failed' });
      await accounts.updateStatus(accountId, 'error');
      await browser.close();
      return;
    }

    const cfgRes = await searchConfigs.getByAccount(accountId);
    const configs = cfgRes.rows;

    if (!configs.length) {
      logger.warn('No active search configs - add one in the dashboard');
      await browser.close();
      await accounts.updateStatus(accountId, 'idle');
      emit('status', { status: 'idle', message: 'No search configs' });
      return;
    }

    const stats = { applied: 0, failed: 0, skipped: 0, pending_questions: 0, manual_review: 0 };

    const retryQueue = await applications.getRetryQueue(accountId, null);
    const seenRetryJobs = new Set();
    for (const row of retryQueue.rows) {
      const retryKey = row.job_url || `${row.job_title}|${row.company_name}`;
      if (seenRetryJobs.has(retryKey)) {
        logger.info(`Skipping duplicate retry job: "${row.job_title}" @ "${row.company_name}"`);
        continue;
      }
      seenRetryJobs.add(retryKey);

      const retryJob = {
        application_id: row.id,
        config_id: row.search_config_id,
        url: row.job_url,
        title: row.job_title,
        company: row.company_name,
        location: row.location,
        job_role: row.job_role || row.job_title,
      };

      emit('progress', {
        phase: 'retrying',
        config: retryJob.job_role,
        job: retryJob.title,
        company: retryJob.company,
      });

      logger.info(`Retrying pending-question application: "${retryJob.title}"`);
      const result = await applyToJob(page, retryJob, account, templates, logger);

      if (result === 'applied') stats.applied++;
      else if (result === 'failed') stats.failed++;
      else if (result === 'skipped') stats.skipped++;
      else if (result === 'pending_questions') stats.pending_questions++;

      emit('application', { job: retryJob, result, stats });

      const retryPause = randomInt(8000, 18000);
      logger.info(`Waiting ${Math.round(retryPause / 1000)}s before the next retry...`);
      await sleep(retryPause);
    }

    for (const config of configs) {
      logger.info(`=== Config: "${config.job_title}" in "${config.location || 'anywhere'}" ===`);
      emit('progress', { phase: 'searching', config: config.job_title });

      await searchJobs(page, config, accountId, logger, async (job) => {
        emit('progress', {
          phase: 'applying',
          config: config.job_title,
          job: job.title,
          company: job.company,
        });

        logger.info(`Applying immediately from right pane: "${job.title}"`);
        const result = await applyToJob(page, job, account, templates, logger);

        if (result === 'applied') stats.applied++;
        else if (result === 'failed') stats.failed++;
        else if (result === 'skipped') stats.skipped++;
        else if (result === 'pending_questions') stats.pending_questions++;

        emit('application', { job, result, stats });

        const pause = randomInt(12000, 30000);
        logger.info(`Waiting ${Math.round(pause / 1000)}s before the next job...`);
        await sleep(pause);

        return result;
      });

      if (configs.indexOf(config) < configs.length - 1) {
        logger.info('Pausing between search configs...');
        await sleep(randomInt(5000, 15000));
      }
    }

    await browser.close();
    await accounts.updateStatus(accountId, 'idle');
    emit('status', { status: 'completed', stats });
    logger.info(`Worker completed. Stats: ${JSON.stringify(stats)}`);
  } catch (err) {
    logger.error(`Worker crashed: ${err.message}`, { stack: err.stack });
    await accounts.updateStatus(accountId, 'error').catch(() => {});
    emit('status', { status: 'error', message: err.message });
  }
})();
