const LINKEDIN_BASE_URL = 'https://www.linkedin.com';

const normalizeComparableText = (value) =>
  String(value || '')
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/&/g, ' and ')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();

const stripDecorators = (value) =>
  normalizeComparableText(value)
    .replace(/\b(remote|hybrid|on site|onsite)\b/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();

const tokenize = (value) => stripDecorators(value).split(' ').filter(Boolean);

const isLooseTextMatch = (a, b, minOverlap = 0.6) => {
  const left = stripDecorators(a);
  const right = stripDecorators(b);

  if (!left || !right) return false;
  if (left === right) return true;
  if (left.includes(right) || right.includes(left)) return true;

  const leftTokens = [...new Set(tokenize(left))];
  const rightTokens = [...new Set(tokenize(right))];
  if (!leftTokens.length || !rightTokens.length) return false;

  const rightSet = new Set(rightTokens);
  const overlap = leftTokens.filter((token) => rightSet.has(token)).length;
  const score = overlap / Math.min(leftTokens.length, rightTokens.length);
  return score >= minOverlap;
};

const extractJobIdFromUrl = (value) => {
  const text = String(value || '').trim();
  if (!text) return null;

  const pathMatch = text.match(/\/jobs\/view\/(\d+)/i);
  if (pathMatch?.[1]) return pathMatch[1];

  const queryMatch = text.match(/[?&#](?:currentJobId|jobId)=(\d+)/i);
  if (queryMatch?.[1]) return queryMatch[1];

  const rawIdMatch = text.match(/^\d+$/);
  if (rawIdMatch?.[0]) return rawIdMatch[0];

  return null;
};

const buildLinkedInJobUrl = (jobId, fallbackUrl = '') =>
  jobId ? `${LINKEDIN_BASE_URL}/jobs/view/${jobId}/` : String(fallbackUrl || '');

const compareJobIdentity = (expected = {}, actual = {}) => {
  const expectedId = extractJobIdFromUrl(expected.url) || extractJobIdFromUrl(expected.jobId);
  const actualId = extractJobIdFromUrl(actual.url) || extractJobIdFromUrl(actual.jobId);

  const titleMatch =
    expected.title && actual.title
      ? isLooseTextMatch(expected.title, actual.title, 0.6)
      : null;

  const companyMatch =
    expected.company && actual.company
      ? isLooseTextMatch(expected.company, actual.company, 0.75)
      : null;

  let matches = false;

  if (expectedId && actualId) {
    matches = expectedId === actualId;
  } else {
    const comparedText = [titleMatch, companyMatch].filter((value) => value !== null);
    matches =
      comparedText.length > 0 &&
      comparedText.every(Boolean) &&
      comparedText.some((value) => value === true);
  }

  return {
    matches,
    expectedId,
    actualId,
    titleMatch,
    companyMatch,
  };
};

module.exports = {
  buildLinkedInJobUrl,
  compareJobIdentity,
  extractJobIdFromUrl,
  isLooseTextMatch,
  normalizeComparableText,
};
