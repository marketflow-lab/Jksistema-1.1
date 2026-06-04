const { recordIssue } = require('./reporting');

function isRelevantApiUrl(url) {
  return /\/api\//i.test(url) || /api\.mercadolibre\.com/i.test(url);
}

function attachBugHunterMonitor(page, testInfo) {
  page.on('console', (message) => {
    if (message.type() !== 'error') return;
    recordIssue(testInfo, {
      severity: 'medium',
      area: 'console',
      title: 'Erro no console do navegador',
      details: message.text(),
      evidence: message.location() ? JSON.stringify(message.location()) : '',
    });
  });

  page.on('pageerror', (error) => {
    recordIssue(testInfo, {
      severity: 'high',
      area: 'javascript',
      title: 'Excecao JavaScript nao tratada',
      details: error.message,
      evidence: error.stack || '',
    });
  });

  page.on('requestfailed', (request) => {
    const failure = request.failure();
    const url = request.url();
    if (!isRelevantApiUrl(url)) return;
    recordIssue(testInfo, {
      severity: 'medium',
      area: 'api',
      title: 'Falha de request',
      details: `${request.method()} ${url}`,
      evidence: failure ? failure.errorText : '',
    });
  });

  page.on('response', (response) => {
    const url = response.url();
    const status = response.status();
    if (!isRelevantApiUrl(url) || status < 400) return;
    recordIssue(testInfo, {
      severity: status >= 500 ? 'high' : 'medium',
      area: 'api',
      title: `Resposta HTTP ${status}`,
      details: `${response.request().method()} ${url}`,
      evidence: response.statusText(),
    });
  });
}

module.exports = { attachBugHunterMonitor };
