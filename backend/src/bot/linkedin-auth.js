// backend/src/bot/linkedin-auth.js
// Handles LinkedIn login with session persistence to avoid repeated logins

const { chromium } = require('playwright');
const { humanType, humanClick, humanDelay, getBrowserConfig, sleep } = require('../utils/humanize');
const { accounts } = require('../db');

const LINKEDIN_LOGIN_URL = 'https://www.linkedin.com/login';
const LINKEDIN_FEED_URL  = 'https://www.linkedin.com/feed';

/**
 * Launch browser with optional saved session
 */
const launchBrowser = async (sessionData = null) => {
  const config = getBrowserConfig();
  const browser = await chromium.launch(config);
  
  const contextOptions = {
    viewport: config.viewport,
    userAgent: config.args.find(a => a.startsWith('--user-agent'))?.split('=').slice(1).join('='),
    locale: 'en-US',
    timezoneId: 'America/New_York',
    // Anti-detection: mask webdriver flag
    extraHTTPHeaders: { 'Accept-Language': 'en-US,en;q=0.9' },
  };

  if (sessionData) {
    contextOptions.storageState = sessionData;
  }

  const context = await browser.newContext(contextOptions);
  
  // Mask automation flags
  await context.addInitScript(() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    window.chrome = { runtime: {} };
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
  });

  return { browser, context };
};

/**
 * Check if already logged in
 */
const isLoggedIn = async (page) => {
  try {
    await page.goto(LINKEDIN_FEED_URL, { waitUntil: 'domcontentloaded', timeout: 15000 });
    await sleep(2000);
    const url = page.url();
    return url.includes('/feed') || url.includes('/mynetwork');
  } catch {
    return false;
  }
};

/**
 * Perform LinkedIn login
 */
const login = async (page, email, password, logger) => {
  logger.info(`Logging in as ${email}`);

  await page.goto(LINKEDIN_LOGIN_URL, { waitUntil: 'domcontentloaded', timeout: 20000 });
  await sleep(2000);

  // Already redirected to feed?
  if (page.url().includes('/feed')) {
    logger.info('Already logged in via session');
    return true;
  }

  try {
    await humanType(page, '#username', email, { clear: true, speed: 'normal' });
    await humanDelay('fast');
    await humanType(page, '#password', password, { clear: true, speed: 'normal' });
    await humanDelay('fast');
    await humanClick(page, '[data-litms-control-urn="login-submit"]');

    // Wait for redirect
    await page.waitForURL((url) =>
      !url.toString().includes('/login') && !url.toString().includes('/checkpoint'),
      { timeout: 30000 }
    );

    await sleep(3000);

    // Handle CAPTCHA or verification challenges
    const currentUrl = page.url();
    if (currentUrl.includes('/checkpoint') || currentUrl.includes('/challenge')) {
      logger.warn('Security checkpoint detected — manual intervention may be required');
      // Wait up to 60s for user to solve CAPTCHA manually
      await page.waitForURL((url) => !url.toString().includes('/checkpoint'), { timeout: 60000 });
    }

    if (page.url().includes('/feed') || page.url().includes('/mynetwork')) {
      logger.info('Login successful');
      return true;
    }

    logger.error('Login failed — unexpected URL: ' + page.url());
    return false;

  } catch (err) {
    logger.error('Login error', { error: err.message });
    return false;
  }
};

/**
 * Create authenticated browser session for an account
 * Reuses saved session if valid, otherwise performs fresh login
 */
const createSession = async (account, logger) => {
  const savedSession = await accounts.getSession(account.id);
  
  const { browser, context } = await launchBrowser(savedSession);
  const page = await context.newPage();

  let loggedIn = false;

  if (savedSession) {
    loggedIn = await isLoggedIn(page);
    if (loggedIn) {
      logger.info('Session restored from saved state');
    } else {
      logger.info('Saved session expired, performing fresh login');
    }
  }

  if (!loggedIn) {
    loggedIn = await login(page, account.email, account.password, logger);
    if (loggedIn) {
      // Save session for future use
      const state = await context.storageState();
      await accounts.saveSession(account.id, state);
      logger.info('Session saved to database');
    }
  }

  return { browser, context, page, loggedIn };
};

module.exports = { createSession, login, isLoggedIn, launchBrowser };
