const { test } = require('@playwright/test');
const {
  assertListingRendered,
  firstListingId,
  login,
  openAdsPage,
  safePriceEdit,
  safeStockEdit,
  safeTitleEdit,
  searchAds,
  selectStore,
} = require('../lib/actions');
const { getBugHunterConfig } = require('../lib/env');
const { attachBugHunterMonitor } = require('../lib/monitor');
const { installSafetyGuards } = require('../lib/safety');

const fileConfig = getBugHunterConfig();
test.skip(
  fileConfig.missingSecrets.length > 0,
  `Configure Secrets/env: ${fileConfig.missingSecrets.join(', ')}`,
);

test('login, busca de anuncios e edicoes seguras em dry-run', async ({ page }, testInfo) => {
  const config = getBugHunterConfig();

  attachBugHunterMonitor(page, testInfo);
  await installSafetyGuards(page, config, testInfo);

  await login(page, config, testInfo);
  await openAdsPage(page, config);
  await selectStore(page, config, testInfo);
  await searchAds(page, config, testInfo);
  await assertListingRendered(page, config, testInfo);

  const itemId = await firstListingId(page, config, testInfo);
  await safePriceEdit(page, config, testInfo, itemId);
  await safeStockEdit(page, config, testInfo, itemId);
  await safeTitleEdit(page, config, testInfo, itemId);
});
