const fs = require('fs');
const childProcess = require('child_process');
const {
  EVENTS_FILE,
  METADATA_FILE,
  REPORT_JSON,
  REPORT_MD,
  getBugHunterConfig,
  safeConfigForReport,
} = require('./env');
const { ensureReportDir, redact } = require('./reporting');

const SEVERITY_ORDER = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
  info: 4,
};

function readJson(file, fallback) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (_err) {
    return fallback;
  }
}

function readEvents() {
  if (!fs.existsSync(EVENTS_FILE)) return [];
  return fs.readFileSync(EVENTS_FILE, 'utf8')
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => {
      try {
        return JSON.parse(line);
      } catch (_err) {
        return null;
      }
    })
    .filter(Boolean);
}

function gitSha() {
  if (process.env.GITHUB_SHA) return process.env.GITHUB_SHA.slice(0, 12);
  try {
    return childProcess.execSync('git rev-parse --short=12 HEAD', { encoding: 'utf8' }).trim();
  } catch (_err) {
    return 'unknown';
  }
}

function countBy(items, key) {
  return items.reduce((acc, item) => {
    const value = item[key] || 'unknown';
    acc[value] = (acc[value] || 0) + 1;
    return acc;
  }, {});
}

function statusIcon(status) {
  if (status === 'passed') return 'PASS';
  if (status === 'skipped') return 'SKIP';
  return 'FAIL';
}

function sortedIssues(events) {
  return events
    .filter((event) => event.type === 'issue')
    .sort((a, b) => {
      const sev = (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9);
      if (sev !== 0) return sev;
      return String(a.area || '').localeCompare(String(b.area || ''));
    });
}

function mdEscape(value) {
  return String(value || '').replace(/\|/g, '\\|').replace(/\r?\n/g, '<br>');
}

function generateBugHunterReport({ tests = [], result = {} } = {}) {
  ensureReportDir();
  const config = getBugHunterConfig();
  const metadata = readJson(METADATA_FILE, {
    startedAt: new Date().toISOString(),
    config: safeConfigForReport(config),
  });
  const events = readEvents();
  const issues = sortedIssues(events);
  const blockedMutations = events.filter((event) => event.type === 'blocked-mutation');
  const infoEvents = events.filter((event) => event.type === 'info');
  const issueCounts = countBy(issues, 'severity');
  const testCounts = countBy(tests, 'status');

  const lines = [];
  lines.push('# Bug Hunter Mercado Livre');
  lines.push('');
  lines.push(`Gerado em: ${new Date().toISOString()}`);
  lines.push(`Commit: ${gitSha()}`);
  lines.push(`Status Playwright: ${result.status || 'unknown'}`);
  lines.push('');
  lines.push('## Politica de seguranca');
  lines.push('');
  lines.push(`- Dry-run: ${config.dryRun ? 'ativo' : 'inativo'}`);
  lines.push(`- Sandbox ML: ${config.sandbox ? 'ativo' : 'inativo'}`);
  lines.push(`- Mutacoes reais liberadas: ${config.allowMutations ? 'sim' : 'nao'}`);
  lines.push(`- Anuncio de teste configurado: ${config.testListingId ? 'sim' : 'nao'}`);
  lines.push('- Credenciais: lidas somente por variaveis/Secrets; valores nao sao gravados no relatorio.');
  lines.push('');
  lines.push('## Resumo');
  lines.push('');
  lines.push(`- Testes: ${tests.length} total, ${testCounts.passed || 0} passaram, ${testCounts.failed || 0} falharam, ${testCounts.skipped || 0} ignorados.`);
  lines.push(`- Bugs: ${issues.length} total, ${issueCounts.critical || 0} criticos, ${issueCounts.high || 0} altos, ${issueCounts.medium || 0} medios, ${issueCounts.low || 0} baixos.`);
  lines.push(`- Mutacoes bloqueadas em modo seguro: ${blockedMutations.length}.`);
  lines.push('');

  if (tests.length) {
    lines.push('## Testes');
    lines.push('');
    lines.push('| Status | Teste | Duracao | Erro |');
    lines.push('| --- | --- | ---: | --- |');
    for (const test of tests) {
      lines.push(`| ${statusIcon(test.status)} | ${mdEscape(test.title)} | ${Math.round((test.durationMs || 0) / 1000)}s | ${mdEscape(redact(test.error || ''))} |`);
    }
    lines.push('');
  }

  lines.push('## Bugs encontrados');
  lines.push('');
  if (!issues.length) {
    lines.push('Nenhum bug registrado pelos monitores do Bug Hunter.');
  } else {
    issues.forEach((issue, index) => {
      lines.push(`### ${index + 1}. [${String(issue.severity || 'medium').toUpperCase()}] ${issue.title}`);
      lines.push('');
      lines.push(`- Area: ${issue.area || 'unknown'}`);
      if (issue.details) lines.push(`- Detalhes: ${issue.details}`);
      if (issue.evidence) lines.push(`- Evidencia: ${issue.evidence}`);
      if (issue.test && issue.test.length) lines.push(`- Teste: ${issue.test.join(' > ')}`);
      lines.push('');
    });
  }
  lines.push('');

  lines.push('## Mutacoes bloqueadas');
  lines.push('');
  if (!blockedMutations.length) {
    lines.push('Nenhuma chamada mutante de Mercado Livre foi tentada durante esta execucao.');
  } else {
    lines.push('| Metodo | URL | Motivo |');
    lines.push('| --- | --- | --- |');
    for (const mutation of blockedMutations) {
      lines.push(`| ${mdEscape(mutation.method)} | ${mdEscape(mutation.url)} | ${mdEscape(mutation.reason)} |`);
    }
  }
  lines.push('');

  if (infoEvents.length) {
    lines.push('## Observacoes');
    lines.push('');
    for (const info of infoEvents) {
      lines.push(`- ${info.title}${info.details ? `: ${info.details}` : ''}`);
    }
    lines.push('');
  }

  lines.push('## Proxima acao');
  lines.push('');
  lines.push('Este relatorio e apenas diagnostico. Nao foram aplicadas correcoes automaticamente. Autorize explicitamente quais bugs devem ser corrigidos antes de qualquer alteracao no sistema.');
  lines.push('');

  fs.writeFileSync(REPORT_MD, `${lines.join('\n')}\n`, 'utf8');
  fs.writeFileSync(REPORT_JSON, JSON.stringify({
    generatedAt: new Date().toISOString(),
    metadata,
    result,
    tests,
    events,
    summary: {
      issueCounts,
      testCounts,
      blockedMutations: blockedMutations.length,
    },
  }, null, 2), 'utf8');

  return { reportMd: REPORT_MD, reportJson: REPORT_JSON };
}

if (require.main === module) {
  const output = generateBugHunterReport();
  console.log(`Bug Hunter report: ${output.reportMd}`);
}

module.exports = { generateBugHunterReport };
