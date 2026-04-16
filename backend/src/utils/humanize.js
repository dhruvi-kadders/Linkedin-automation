// backend/src/utils/humanize.js
// Utilities to simulate human-like browser behavior with Playwright

const randomInt   = (min, max) => Math.floor(Math.random() * (max - min + 1)) + min;
const randomFloat = (min, max) => Math.random() * (max - min) + min;
const sleep       = (ms) => new Promise(r => setTimeout(r, ms));

// ── Delay presets ─────────────────────────────────────────────────────────────
const humanDelay = async (speed = 'normal') => {
  const ranges = { fast: [300, 800], normal: [800, 2000], slow: [2000, 4500] };
  const [min, max] = ranges[speed] || ranges.normal;
  await sleep(randomInt(min, max));
};

// ── Type text character by character ─────────────────────────────────────────
const humanType = async (page, selector, text, options = {}) => {
  const { clear = true, speed = 'normal' } = options;
  try {
    const el = await page.waitForSelector(selector, { state: 'visible', timeout: 8000 });
    if (clear) {
      await el.click({ clickCount: 3 });
      await sleep(randomInt(50, 150));
      await page.keyboard.press('Backspace');
    }
    for (const char of String(text)) {
      await el.type(char, { delay: randomInt(35, 120) });
      if (Math.random() < 0.04) await sleep(randomInt(200, 600)); // occasional pause
    }
    if (speed !== 'fast') await sleep(randomInt(150, 400));
  } catch (err) {
    // Fallback: use fill() which is less human-like but more reliable
    try {
      const el = await page.$(selector);
      if (el) { await el.fill(String(text)); }
    } catch { /* give up */ }
  }
};

// ── Click with slight offset ──────────────────────────────────────────────────
const humanClick = async (page, selector, options = {}) => {
  const { timeout = 12000 } = options;
  try {
    const el = await page.waitForSelector(selector, { state: 'visible', timeout });
    const box = await el.boundingBox();
    if (box) {
      const x = box.x + box.width  * randomFloat(0.3, 0.7);
      const y = box.y + box.height * randomFloat(0.3, 0.7);
      await page.mouse.move(x, y, { steps: randomInt(4, 12) });
      await sleep(randomInt(40, 180));
      await page.mouse.click(x, y);
    } else {
      await el.click();
    }
    await sleep(randomInt(80, 300));
  } catch (err) {
    throw new Error(`humanClick failed on "${selector}": ${err.message}`);
  }
};

// ── Scroll gradually ─────────────────────────────────────────────────────────
const humanScroll = async (page, direction = 'down', amount = 300) => {
  const steps    = randomInt(3, 8);
  const stepSize = amount / steps;
  for (let i = 0; i < steps; i++) {
    await page.mouse.wheel(0, direction === 'down' ? stepSize : -stepSize);
    await sleep(randomInt(40, 180));
  }
};

// ── Select by label or value ──────────────────────────────────────────────────
const humanSelect = async (page, selector, value) => {
  await page.waitForSelector(selector, { state: 'visible', timeout: 8000 });
  await sleep(randomInt(100, 300));
  await page.selectOption(selector, { label: value }).catch(() =>
    page.selectOption(selector, { value }).catch(() =>
      page.selectOption(selector, { index: 1 })
    )
  );
  await sleep(randomInt(150, 400));
};

// ── Navigate with pre-delay ───────────────────────────────────────────────────
const humanNavigate = async (page, url) => {
  await sleep(randomInt(400, 1200));
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await sleep(randomInt(900, 2000));
};

// ── Answer a field using Q&A templates ───────────────────────────────────────
// (Legacy — used by some older code paths; new code uses fillField in easy-apply.js)
const answerQuestion = async (page, fieldInfo, qaTemplates) => {
  const { label = '', type, selector } = fieldInfo;
  if (!selector) return false;
  const lower = label.toLowerCase();
  const tpl = qaTemplates.find(t => lower.includes(t.question_pattern.toLowerCase()));
  if (!tpl) return false;

  try {
    if (type === 'text' || type === 'textarea' || type === 'number' || type === 'email') {
      await humanType(page, selector, tpl.answer, { clear: true, speed: 'fast' });
    } else if (type === 'select') {
      await humanSelect(page, selector, tpl.answer);
    } else if (type === 'radio' || type === 'checkbox') {
      const opts = await page.$$(`${selector}`);
      for (const opt of opts) {
        const lbl = await opt.evaluate(el => {
          const l = el.closest('label') || document.querySelector(`label[for="${el.id}"]`);
          return l ? l.textContent.trim().toLowerCase() : '';
        });
        if (lbl.includes(tpl.answer.toLowerCase()) || tpl.answer.toLowerCase().includes(lbl)) {
          await opt.click();
          break;
        }
      }
    }
    return true;
  } catch { return false; }
};

// ── Browser launch config ─────────────────────────────────────────────────────
const getBrowserConfig = () => ({
  headless: process.env.HEADLESS === 'true',  // false by default for easier debugging
  args: [
    '--no-sandbox',
    '--disable-setuid-sandbox',
    '--disable-blink-features=AutomationControlled',
    '--disable-infobars',
    '--window-size=1440,900',
  ],
  viewport: { width: 1440, height: 900 },
});

module.exports = {
  sleep, randomInt, randomFloat,
  humanDelay, humanType, humanClick,
  humanScroll, humanSelect, humanNavigate,
  answerQuestion, getBrowserConfig,
};