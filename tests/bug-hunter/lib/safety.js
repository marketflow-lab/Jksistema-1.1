const { recordBlockedMutation, recordIssue } = require('./reporting');

const MUTATING_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

function isMercadoLivreMutation(requestUrl, method) {
  if (!MUTATING_METHODS.has(method)) return false;
  const url = new URL(requestUrl);
  const path = url.pathname;
  if (/api\.mercadolibre\.com$/i.test(url.hostname)) return true;
  if (path.startsWith('/api/mercadolivre/')) return true;
  if (path.startsWith('/api/integracoes/mercadolivre')) return true;
  return false;
}

function requestBody(request) {
  try {
    return request.postData() || '';
  } catch (_err) {
    return '';
  }
}

function bodyHasTestListing(body, testListingId) {
  if (!testListingId || !body) return false;
  return body.toUpperCase().includes(testListingId.toUpperCase());
}

function canPassMutation(request, config) {
  if (!config.allowMutations) return false;
  if (config.sandbox) return true;
  return bodyHasTestListing(requestBody(request), config.testListingId);
}

function dryRunPayload(request) {
  const path = new URL(request.url()).pathname;
  if (path.endsWith('/preco')) {
    return { success: true, dry_run: true, message: 'Dry-run: preco validado sem alterar anuncio.' };
  }
  if (path.endsWith('/estoque')) {
    return { success: true, dry_run: true, message: 'Dry-run: estoque validado sem alterar anuncio.' };
  }
  if (path.endsWith('/status')) {
    return { success: true, dry_run: true, message: 'Dry-run: status validado sem alterar anuncio.' };
  }
  return { success: true, dry_run: true, message: 'Dry-run: mutacao Mercado Livre bloqueada.' };
}

async function fulfillJson(route, status, payload) {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(payload),
  });
}

async function installSafetyGuards(page, config, testInfo) {
  await page.route('**/*', async (route) => {
    const request = route.request();
    const method = request.method().toUpperCase();
    const url = request.url();

    if (!isMercadoLivreMutation(url, method)) {
      await route.continue();
      return;
    }

    if (config.dryRun) {
      recordBlockedMutation(testInfo, {
        method,
        url,
        body: requestBody(request),
        reason: 'dry-run ativo',
        dryRun: true,
      });
      await fulfillJson(route, 200, dryRunPayload(request));
      return;
    }

    if (!canPassMutation(request, config)) {
      recordBlockedMutation(testInfo, {
        method,
        url,
        body: requestBody(request),
        reason: 'mutacao fora de sandbox/dry-run/anuncio de teste',
        dryRun: false,
      });
      recordIssue(testInfo, {
        severity: 'critical',
        area: 'safety',
        title: 'Mutacao real bloqueada pelo Bug Hunter',
        details: `${method} ${url}`,
        evidence: 'Defina BUG_HUNTER_DRY_RUN=true ou use MELI_SANDBOX=true/BUG_HUNTER_TEST_LISTING_ID com BUG_HUNTER_ALLOW_MUTATIONS=true.',
      });
      await fulfillJson(route, 409, {
        success: false,
        blocked_by_bug_hunter: true,
        message: 'Mutacao bloqueada: ambiente nao seguro para alterar anuncios.',
      });
      return;
    }

    await route.continue();
  });
}

module.exports = {
  installSafetyGuards,
  isMercadoLivreMutation,
};
