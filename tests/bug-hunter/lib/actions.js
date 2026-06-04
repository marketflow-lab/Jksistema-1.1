const { expect } = require('@playwright/test');
const { recordInfo, recordIssue } = require('./reporting');

async function login(page, config, testInfo) {
  await page.goto(config.loginUrl(), { waitUntil: 'domcontentloaded' });
  if (/dashboard\.html/i.test(page.url())) return;

  await expect(page.locator(config.selectors.username)).toBeVisible();
  await page.locator(config.selectors.username).fill(config.username);
  await page.locator(config.selectors.password).fill(config.password);
  await page.locator(config.selectors.loginButton).click();

  const skipIntro = page.getByRole('button', { name: /pular|skip/i });
  await skipIntro.click({ timeout: 5_000 }).catch(() => {});

  try {
    await page.waitForURL(/dashboard\.html/i, { timeout: config.loginTimeoutMs });
  } catch (error) {
    const errorText = await page.locator('#error-msg').textContent({ timeout: 1_000 }).catch(() => '');
    recordIssue(testInfo, {
      severity: 'high',
      area: 'login',
      title: 'Login de teste nao concluiu',
      details: errorText || error.message,
      evidence: page.url(),
    });
    throw error;
  }
}

async function openAdsPage(page, config) {
  await page.goto(config.adsUrl(), { waitUntil: 'domcontentloaded' });
  await expect(page.locator(config.selectors.storeSelect)).toBeVisible();
}

async function selectStore(page, config, testInfo) {
  const select = page.locator(config.selectors.storeSelect);
  await expect(select).toBeVisible();
  await page.waitForFunction((selector) => {
    const el = document.querySelector(selector);
    return el && el.options && el.options.length > 0;
  }, config.selectors.storeSelect, { timeout: config.pageTimeoutMs }).catch(() => {});

  const options = await select.locator('option').evaluateAll((items) => items.map((item) => ({
    value: item.value,
    label: item.label || item.textContent || '',
  })).filter((item) => item.value || item.label));

  if (!options.length) {
    recordIssue(testInfo, {
      severity: 'high',
      area: 'anuncios',
      title: 'Nenhuma loja Mercado Livre disponivel',
      details: 'O seletor de lojas nao recebeu opcoes.',
    });
    return '';
  }

  const wanted = config.storeName.toLowerCase();
  const selected = wanted
    ? options.find((item) => item.value.toLowerCase() === wanted || item.label.toLowerCase() === wanted)
    : options[0];

  if (!selected) {
    recordIssue(testInfo, {
      severity: 'medium',
      area: 'anuncios',
      title: 'Loja configurada nao encontrada',
      details: `BUG_HUNTER_STORE="${config.storeName}" nao bate com nenhuma opcao.`,
      evidence: options.map((item) => item.label || item.value).join(', '),
    });
    await select.selectOption(options[0].value);
    return options[0].value;
  }

  await select.selectOption(selected.value);
  return selected.value;
}

async function waitForAdsResult(page, config) {
  await Promise.race([
    page.waitForResponse((response) => response.url().includes('/api/mercadolivre/anuncios'), { timeout: config.pageTimeoutMs }).catch(() => null),
    page.waitForTimeout(3_000),
  ]);
  await page.waitForLoadState('networkidle', { timeout: 10_000 }).catch(() => {});
}

async function searchAds(page, config, testInfo) {
  const searchTerm = config.testSku || config.testListingId;
  if (!searchTerm) {
    recordInfo(testInfo, {
      area: 'anuncios',
      title: 'Busca por SKU ignorada',
      details: 'Configure BUG_HUNTER_TEST_SKU ou BUG_HUNTER_TEST_LISTING_ID para exercitar a busca direcionada.',
    });
    await waitForAdsResult(page, config);
    return;
  }

  await page.locator(config.selectors.skuFilter).fill(searchTerm);
  await page.locator(config.selectors.searchButton).click();
  await waitForAdsResult(page, config);
}

async function firstListingId(page, config, testInfo) {
  if (config.testListingId) return config.testListingId;
  const firstRow = page.locator(config.selectors.listingRows).first();
  if (!await firstRow.count()) {
    recordIssue(testInfo, {
      severity: 'medium',
      area: 'anuncios',
      title: 'Nenhum anuncio visivel para teste seguro',
      details: 'A listagem nao retornou linhas para extrair um item_id.',
    });
    return '';
  }
  const id = await firstRow.locator('[data-col="id"]').textContent().catch(() => '');
  const normalized = String(id || '').trim().toUpperCase();
  if (normalized && config.dryRun) {
    recordInfo(testInfo, {
      area: 'safety',
      title: 'Usando primeiro anuncio apenas em dry-run',
      details: 'Nenhum BUG_HUNTER_TEST_LISTING_ID foi configurado; chamadas mutantes continuarao bloqueadas.',
    });
  }
  return normalized;
}

async function activateTab(page, tabId, testInfo) {
  const button = page.locator(`[data-tab="${tabId}"]`);
  if (await button.count()) {
    await button.first().click();
    return;
  }
  recordIssue(testInfo, {
    severity: 'medium',
    area: 'ui',
    title: `Aba ${tabId} existe mas nao tem botao de navegacao`,
    details: 'O Bug Hunter ativou a aba via DOM apenas para validar a guarda de dry-run.',
  });
  await page.evaluate((id) => {
    document.querySelectorAll('.tab.active').forEach((el) => el.classList.remove('active'));
    const target = document.getElementById(id);
    if (target) target.classList.add('active');
  }, tabId);
}

async function safePriceEdit(page, config, testInfo, itemId) {
  if (!itemId) return;
  await activateTab(page, 'tab-preco', testInfo);
  await page.locator(config.selectors.priceItem).fill(itemId);
  await page.locator(config.selectors.priceValue).fill(config.testPrice);
  await page.locator(config.selectors.priceButton).click();
  await expect(page.locator(config.selectors.priceMessage)).toContainText(/dry-run|preco|processado|bloquead/i);
}

async function safeStockEdit(page, config, testInfo, itemId) {
  if (!itemId) return;
  await activateTab(page, 'tab-estoque', testInfo);
  await page.locator(config.selectors.stockItem).fill(itemId);
  await page.locator(config.selectors.stockValue).fill(config.testStock);
  await page.locator(config.selectors.stockButton).click();
  await expect(page.locator(config.selectors.stockMessage)).toContainText(/dry-run|estoque|atualizado|bloquead/i);
}

async function safeTitleEdit(page, config, testInfo, itemId) {
  if (!itemId) return;
  const hasTitleControls = await page.locator(config.selectors.titleItem).count()
    && await page.locator(config.selectors.titleValue).count()
    && await page.locator(config.selectors.titleButton).count();

  if (!hasTitleControls) {
    recordIssue(testInfo, {
      severity: 'medium',
      area: 'anuncios',
      title: 'Fluxo de edicao de titulo nao encontrado',
      details: 'Nao foram encontrados controles padrao para editar titulo do anuncio.',
      evidence: `${config.selectors.titleItem}, ${config.selectors.titleValue}, ${config.selectors.titleButton}`,
    });
    return;
  }

  await activateTab(page, 'tab-titulo', testInfo);
  await page.locator(config.selectors.titleItem).fill(itemId);
  await page.locator(config.selectors.titleValue).fill(config.testTitle);
  await page.locator(config.selectors.titleButton).click();
  await expect(page.locator(config.selectors.titleMessage)).toContainText(/dry-run|titulo|processado|bloquead/i);
}

async function assertListingRendered(page, config, testInfo) {
  const rows = page.locator(config.selectors.listingRows);
  const count = await rows.count();
  if (count > 0) {
    recordInfo(testInfo, {
      area: 'anuncios',
      title: 'Listagem de anuncios renderizada',
      details: `${count} linha(s) visivel(is).`,
    });
    return;
  }

  const message = await page.locator('#anunciosMsg').textContent().catch(() => '');
  recordIssue(testInfo, {
    severity: 'medium',
    area: 'anuncios',
    title: 'Listagem de anuncios sem linhas',
    details: message || 'Nenhuma linha foi renderizada apos a busca/listagem.',
  });
}

module.exports = {
  assertListingRendered,
  firstListingId,
  login,
  openAdsPage,
  safePriceEdit,
  safeStockEdit,
  safeTitleEdit,
  searchAds,
  selectStore,
};
