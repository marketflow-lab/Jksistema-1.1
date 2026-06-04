const fs = require('fs');
const path = require('path');
const { EVENTS_FILE, REPORT_DIR } = require('./env');

const SECRET_PATTERNS = [
  /Bearer\s+[A-Za-z0-9._~+/=-]+/gi,
  /(access_token|refresh_token|password|senha|client_secret)["']?\s*[:=]\s*["']?[^"',}\s]+/gi,
];

function ensureReportDir() {
  fs.mkdirSync(REPORT_DIR, { recursive: true });
}

function redact(value) {
  let text = typeof value === 'string' ? value : JSON.stringify(value);
  if (!text) return '';
  for (const pattern of SECRET_PATTERNS) {
    text = text.replace(pattern, (match) => {
      const keyMatch = match.match(/^(access_token|refresh_token|password|senha|client_secret)/i);
      return keyMatch ? `${keyMatch[1]}=<redacted>` : '<redacted>';
    });
  }
  return text;
}

function appendEvent(type, payload = {}) {
  ensureReportDir();
  const event = {
    type,
    timestamp: new Date().toISOString(),
    ...payload,
  };
  fs.appendFileSync(EVENTS_FILE, `${JSON.stringify(event)}\n`, 'utf8');
}

function titlePath(testInfo) {
  if (!testInfo) return [];
  if (typeof testInfo.titlePath === 'function') return testInfo.titlePath();
  if (Array.isArray(testInfo.titlePath)) return testInfo.titlePath;
  return testInfo.title ? [testInfo.title] : [];
}

function recordIssue(testInfo, issue) {
  appendEvent('issue', {
    severity: issue.severity || 'medium',
    area: issue.area || 'unknown',
    title: issue.title || 'Untitled issue',
    details: redact(issue.details || ''),
    evidence: redact(issue.evidence || ''),
    test: titlePath(testInfo),
  });
}

function recordInfo(testInfo, info) {
  appendEvent('info', {
    area: info.area || 'general',
    title: info.title || 'Info',
    details: redact(info.details || ''),
    test: titlePath(testInfo),
  });
}

function recordBlockedMutation(testInfo, mutation) {
  appendEvent('blocked-mutation', {
    method: mutation.method,
    url: redact(mutation.url),
    reason: mutation.reason,
    dryRun: mutation.dryRun,
    body: redact(mutation.body || ''),
    test: titlePath(testInfo),
  });
}

function resetReportDir() {
  const resolved = path.resolve(REPORT_DIR);
  if (!resolved.toLowerCase().includes('bug-hunter-report')) {
    throw new Error(`Refusing to reset unexpected report directory: ${resolved}`);
  }
  fs.rmSync(resolved, { recursive: true, force: true });
  fs.mkdirSync(resolved, { recursive: true });
}

module.exports = {
  appendEvent,
  ensureReportDir,
  recordBlockedMutation,
  recordInfo,
  recordIssue,
  redact,
  resetReportDir,
};
