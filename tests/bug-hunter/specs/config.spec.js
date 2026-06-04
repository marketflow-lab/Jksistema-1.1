const { test, expect } = require('@playwright/test');
const { getBugHunterConfig, safeConfigForReport } = require('../lib/env');
const { recordInfo, recordIssue } = require('../lib/reporting');

test('configuracao segura do Bug Hunter', async ({}, testInfo) => {
  const config = getBugHunterConfig();
  recordInfo(testInfo, {
    area: 'config',
    title: 'Configuracao carregada',
    details: JSON.stringify(safeConfigForReport(config)),
  });

  if (config.missingSecrets.length) {
    recordIssue(testInfo, {
      severity: 'critical',
      area: 'config',
      title: 'Credenciais de teste ausentes',
      details: `Configure GitHub Secrets/env: ${config.missingSecrets.join(', ')}`,
    });
  }

  if (!config.dryRun && !config.sandbox && !config.testListingId) {
    recordIssue(testInfo, {
      severity: 'critical',
      area: 'safety',
      title: 'Modo sem dry-run exige sandbox ou anuncio de teste',
      details: 'Use BUG_HUNTER_DRY_RUN=true, MELI_SANDBOX=true ou BUG_HUNTER_TEST_LISTING_ID.',
    });
  }

  if (!config.dryRun && !config.allowMutations) {
    recordIssue(testInfo, {
      severity: 'high',
      area: 'safety',
      title: 'Mutacoes reais nao liberadas',
      details: 'BUG_HUNTER_ALLOW_MUTATIONS precisa ser true para qualquer mutacao fora de dry-run.',
    });
  }

  if (!config.allowMissingSecrets) {
    expect(config.missingSecrets, 'Configure credenciais via GitHub Secrets ou env local.').toEqual([]);
  }
  expect(
    config.dryRun || config.sandbox || Boolean(config.testListingId),
    'Nunca rode mutacoes fora de dry-run sem sandbox ou anuncio de teste.',
  ).toBeTruthy();
});
