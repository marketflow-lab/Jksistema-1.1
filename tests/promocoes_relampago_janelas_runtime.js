'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

class Element {
  constructor() {
    this.children = [];
    this.style = {};
    this.dataset = {};
    this.listeners = {};
    this.classList = { add() {}, remove() {} };
    this.value = '';
    this._text = '';
    this.attributes = {};
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set innerHTML(value) { this._text = String(value); this.children = []; }
  get innerHTML() { return this._text; }
  appendChild(child) { child.parentElement = this; this.children.push(child); return child; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  change(value) { this.value = value; this.listeners.change?.call(this); }
  remove() {
    if (!this.parentElement) return;
    this.parentElement.children = this.parentElement.children.filter(child => child !== this);
  }
}

const ids = Object.fromEntries([
  'tabelaAnalise', 'totalAnuncios', 'totalParticipar', 'totalNaoParticipar',
  'apiLojaSelect', 'apiPromoBSelect', 'apiAnalysisTabs', 'apiPromoBFilesWrap',
  'paginationControls', 'pageSizeSelect', 'prevPageBtn', 'nextPageBtn', 'pageInfo',
].map(id => [id, new Element()]));
ids.apiLojaSelect.value = 'Loja Teste';
const thead = new Element();
const tbody = new Element();
const storage = new Map();
const context = {
  console, Intl, Date,
  document: {
    body: new Element(),
    getElementById: id => ids[id] || null,
    querySelector: selector => selector.endsWith('thead tr') ? thead : selector.endsWith('tbody') ? tbody : null,
    createElement: () => new Element(),
    addEventListener() {},
    removeEventListener() {},
  },
  window: { addEventListener() {} },
  localStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value) },
  requestAnimationFrame() {},
  setTimeout() {},
  fetch: () => { throw new Error('Nenhuma chamada real é permitida neste teste.'); },
};
vm.createContext(context);
for (const file of ['runtime.js', 'tabela-preferencias.js', 'arquivos-render.js', 'api-mercadolivre.js', 'participacao.js']) {
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'static', 'frontend_promo', file), 'utf8'), context, { filename: file });
}
for (const name of ['applyColumnWidthPrefsToDom', 'bindPromoMinimizeOnDblClick', 'renderApiAnalysisTabs']) context[name] = () => {};
context.getPromoClientId = () => 'cliente-teste';
context.getVisibleColumns = () => [
  { key: 'Campanha ML', label: 'Campanha ML' },
  { key: 'Preço Final ML', label: 'Preço Final Promoção 2' },
  { key: 'Margem ML', label: 'Margem ML' },
  { key: 'Ação', label: 'Ação', editable: true },
];

const lightning = { id: 'LGH-TESTE', name: 'LGH-TESTE', type: 'LIGHTNING', status: 'started' };
const smart = { id: 'P-SMART', name: 'Campanha Smart', type: 'SMART' };
assert.strictEqual(context.getPromoCampaignDisplayName(lightning), 'Ofertas relâmpago');
assert(context.formatPromoOption(lightning).includes('(LGH-TESTE)'));
assert.strictEqual(context.getPromoCampaignDisplayName(smart), 'Campanha Smart');
context.campaigns = [lightning, smart];
vm.runInContext('apiPromoBCampaigns = campaigns', context);
context.renderApiPromoBButtons([lightning, smart]);
assert(ids.apiPromoBSelect.innerHTML.includes('Ofertas relâmpago'));
assert(ids.apiPromoBSelect.innerHTML.includes('LGH-TESTE'));
assert(ids.apiPromoBSelect.innerHTML.includes('Campanha Smart'));

const invalidReason = 'Limites de estoque desta oferta não confirmados. Execute uma nova análise.';
const days = [9, 10, 12];
const rows = days.map((day, index) => ({
  MLB: 'MLB123', SKU: '47', 'Campanha ML': 'LGH-TESTE',
  'Preço Final ML': 'R$ 66,32', 'Margem ML': '17,48%', 'Ação': 'Participar',
  action_promotion_id: 'LGH-TESTE', action_deal_price: 66.32,
  action_financeiro_exato: day !== 10, action_financeiro_estimado: day === 10,
  action_tecnico_apto: true,
  lightning_offer_key: 'LGH-TESTE:MLB123:' + day,
  lightning_start_date: '2026-10-' + String(day).padStart(2, '0') + 'T15:00:00Z',
  lightning_finish_date: '2026-10-' + String(day + 1).padStart(2, '0') + 'T03:00:00Z',
  lightning_stock_min: 5, lightning_stock_max: 20, lightning_stock: 5 + index,
  lightning_action_blocked: false, lightning_action_reason: '',
}));

function installAnalysis(data, type = 'LIGHTNING', id = 'LGH-TESTE') {
  data.forEach((row, index) => { row.analysis_client_ref = `canonical:${id}:${index}`; });
  context.rows = data;
  context.analysisType = type;
  context.analysisId = id;
  vm.runInContext("apiAnalisesPorCampanha = [{ promo_b_id: analysisId, promo_b_type: analysisType, data: rows, origem_loja: 'Loja Teste', origem_client_id: 'cliente-teste', participacao_confirmada: true }]; currentData = rows", context);
  vm.runInContext("apiAnalisesPorCampanha[0].job_id = 'job-completed'; apiAnalisesPorCampanha[0].participacao_contexto = capturarParametrosParticipacaoPromocoes()", context);
  context.renderTable(data);
}

function assertOfferPayload(items, expectedRows) {
  assert.strictEqual(items.length, expectedRows.length);
  expectedRows.forEach((row, index) => {
    assert.strictEqual(items[index].item_id, row.MLB);
    assert.strictEqual(items[index].lightning_offer_key, row.lightning_offer_key);
    assert.strictEqual(items[index].lightning_start_date, row.lightning_start_date);
    assert.strictEqual(items[index].lightning_finish_date, row.lightning_finish_date);
    assert.strictEqual(items[index].lightning_stock, row.lightning_stock);
    assert.strictEqual(items[index].lightning_action_blocked, false);
    assert.strictEqual(items[index].lightning_schedule_pending, row.lightning_schedule_pending === true);
  });
}

const pricesBefore = rows.map(row => row['Preço Final ML']);
installAnalysis(rows);
assert.strictEqual(tbody.children.length, 3, 'cada data recebida deve gerar sua própria linha');
assert.strictEqual(ids.totalAnuncios.textContent, '3');
assert.strictEqual(ids.totalParticipar.textContent, '3');
assert.strictEqual(new Set(tbody.children.map(row => row.attributes['data-lightning-offer-key'])).size, 3);
for (const [index, day] of days.entries()) {
  const campaignCell = tbody.children[index].children[0];
  assert.strictEqual(campaignCell.children.length, 2, 'várias datas permitem editar o estoque de cada oferta');
  assert(campaignCell.textContent.includes(day + '/out/2026 às 12:00'), 'a data deve usar o fuso de Brasília');
  assert.strictEqual(campaignCell.children[1].children[1].value, String(5 + index));
  assert.strictEqual(context.obterAvisoParticipacaoPromocoes(rows[index]), '');
}
let payload = context.montarPayloadParticipacoesPromocoes(0);
assertOfferPayload(payload.promocoes[0].items, rows);
assertOfferPayload(context.montarPayloadParticipacoesPromocoes(null, { automatica: true }).promocoes[0].items, rows);
assert.strictEqual(payload.promocoes[0].items[0].deal_price, 66.32);
assert.strictEqual(rows[1].action_financeiro_exato, false, 'a oferta aprovada com tarifa estimada também entra na fila automática');

tbody.children[0].children[3].children[0].change('Não participar');
tbody.children[2].children[3].children[0].change('Não participar');
payload = context.montarPayloadParticipacoesPromocoes(0);
assertOfferPayload(payload.promocoes[0].items, [rows[1]]);
assert.strictEqual(payload.promocoes[0].items[0].client_ref, rows[1].analysis_client_ref);
assert.strictEqual(rows[2]['Ação'], 'Não participar');
assert.deepStrictEqual(rows.map(row => row['Preço Final ML']), pricesBefore, 'a apresentação não recalcula os preços canônicos');
context.renderTable(rows);
assert.strictEqual(rows[1]['Ação'], 'Participar', 'renderizar novamente preserva a escolha por data');

const result = context.montarLinhasResultadoParticipacao(payload, { detalhes: [{
  promotion_id: 'LGH-TESTE', client_ref: payload.promocoes[0].items[0].client_ref,
  outcome: 'applied', success: true,
}] });
assert.strictEqual(result[0].confirmados.length, 1);
assert(result[0].confirmados[0].nota.includes('10/out/2026 às 12:00'));
assert(!result[0].confirmados[0].nota.includes('9/out/2026 às 12:00'));

const editable = [{ ...rows[0], lightning_offer_key: 'EDITAVEL', lightning_stock: 5, 'Ação': 'Participar' }];
installAnalysis(editable);
let stockInput = tbody.children[0].children[0].children[1].children[1];
assert.strictEqual(stockInput.value, '5', 'o mínimo confirmado pela análise está disponível para envio');
assert.strictEqual(stockInput.min, '5');
assert.strictEqual(stockInput.max, '20');
assert.strictEqual(context.obterAvisoParticipacaoPromocoes(editable[0]), '');
stockInput.change('3');
assert(context.obterAvisoParticipacaoPromocoes(editable[0]).includes('mínimo é 5'));
assert.strictEqual(context.montarPayloadParticipacoesPromocoes(0).promocoes.length, 0);
assert.strictEqual(context.montarPayloadParticipacoesPromocoes(null, { automatica: true }).promocoes.length, 0);
stockInput.change('15');
assert.strictEqual(context.obterAvisoParticipacaoPromocoes(editable[0]), '');
assert.strictEqual(context.montarPayloadParticipacoesPromocoes(0).promocoes[0].items[0].lightning_stock, 15);
assert.strictEqual(context.montarPayloadParticipacoesPromocoes(null, { automatica: true }).promocoes[0].items[0].lightning_stock, 15);
assert.strictEqual(vm.runInContext('apiAnalisesPorCampanha[0].participacao_confirmada', context), false);
assert.strictEqual(vm.runInContext('apiAnalisesPorCampanha[0].participacao_selecao_revisao', context), 2);
context.renderTable(editable);
assert.strictEqual(tbody.children[0].children[0].children[1].children[1].value, '15');
stockInput = tbody.children[0].children[0].children[1].children[1];
stockInput.change('21');
assert(context.obterAvisoParticipacaoPromocoes(editable[0]).includes('máximo é 20'));
assert.strictEqual(context.montarPayloadParticipacoesPromocoes(0).promocoes.length, 0);
for (const invalidStock of ['1.5', '-1', '0', '']) {
  stockInput.change(invalidStock);
  assert.strictEqual(context.montarPayloadParticipacoesPromocoes(0).promocoes.length, 0);
  assert.strictEqual(context.montarPayloadParticipacoesPromocoes(null, { automatica: true }).promocoes.length, 0);
}

const invalid = [{ ...rows[0], 'Ação': 'Participar', lightning_action_blocked: true,
  lightning_action_reason: invalidReason, lightning_stock_min: null, lightning_stock_max: null }];
installAnalysis(invalid);
assert.strictEqual(context.obterAvisoParticipacaoPromocoes(invalid[0]), invalidReason);
assert.strictEqual(context.montarPayloadParticipacoesPromocoes(0).promocoes.length, 0);
assert.strictEqual(context.montarPayloadParticipacoesPromocoes(null, { automatica: true }).promocoes.length, 0);
assert(!context.obterAvisoParticipacaoPromocoes({ lightning_action_blocked: true }).includes('painel do Mercado Livre'));

const normal = [{ MLB: 'MLB456', 'Campanha ML': 'Campanha Smart', 'Preço Final ML': 'R$ 88,00',
  'Ação': 'Participar', action_promotion_id: 'P-SMART', action_deal_price: 88,
  action_financeiro_exato: true, action_tecnico_apto: true }];
installAnalysis(normal, 'SMART', 'P-SMART');
assert.strictEqual(tbody.children[0].children[0].textContent, 'Campanha Smart');
payload = context.montarPayloadParticipacoesPromocoes(0);
assert.strictEqual(payload.promocoes[0].items[0].deal_price, 88);
assert(!Object.keys(payload.promocoes[0].items[0]).some(key => key.startsWith('lightning_')), 'SMART preserva seu payload');
normal[0].action_financeiro_exato = false;
assert.strictEqual(context.montarPayloadParticipacoesPromocoes(null, { automatica: true }).promocoes.length, 0,
  'a mudança na elegibilidade estimada fica restrita às ofertas relâmpago');
assert.strictEqual(context.obterAvisoParticipacaoPromocoes({ action_tecnico_apto: false }), '', 'avisos técnicos genéricos não mudam');
assert.strictEqual(context.formatarJanelaOfertaRelampago({}), 'Data e horário não informados pela API');
const naiveWindow = context.formatarJanelaOfertaRelampago({
  lightning_start_date: '2026-10-09T12:00:00', lightning_finish_date: '2026-10-10T00:00:00',
});
assert(naiveWindow.includes('fuso não informado'));
assert(!naiveWindow.includes('Brasília'));

const schedulePendingText = 'Data e horário serão informados pelo Mercado Livre após a inclusão.';
const schedulePending = {
  ...rows[0], MLB: 'MLB789', 'Ação': 'Participar', action_offer_id: 'CANDIDATE-MLB789-TESTE',
  action_financeiro_exato: false, action_financeiro_estimado: true, action_tecnico_apto: true,
  lightning_offer_key: 'LGH-TESTE:MLB789:CANDIDATE', lightning_start_date: '', lightning_finish_date: '',
  lightning_stock: 5, lightning_action_blocked: false, lightning_action_reason: '', lightning_schedule_pending: true,
};
installAnalysis([schedulePending]);
assert.strictEqual(context.formatarJanelaOfertaRelampago(schedulePending), schedulePendingText);
assert(tbody.children[0].children[0].textContent.includes(schedulePendingText));
assert.strictEqual(context.obterAvisoParticipacaoPromocoes(schedulePending), '');
const pendingPayload = context.montarPayloadParticipacoesPromocoes(0);
assertOfferPayload(pendingPayload.promocoes[0].items, [schedulePending]);
assertOfferPayload(context.montarPayloadParticipacoesPromocoes(null, { automatica: true }).promocoes[0].items, [schedulePending]);
assert.strictEqual(pendingPayload.promocoes[0].items[0].offer_id, schedulePending.action_offer_id);
assert.strictEqual(pendingPayload.promocoes[0].items[0].deal_price, 66.32);
const assignedResult = { detalhes: [{
  promotion_id: 'LGH-TESTE', client_ref: pendingPayload.promocoes[0].items[0].client_ref,
  outcome: 'applied', success: true, lightning_schedule_pending: false,
  lightning_start_date: '2026-10-12T15:00:00Z', lightning_finish_date: '2026-10-13T03:00:00Z',
}] };
const assignedSummary = context.montarLinhasResultadoParticipacao(pendingPayload, assignedResult);
assert(assignedSummary[0].confirmados[0].nota.includes('12/out/2026 às 12:00'));
assert(!assignedSummary[0].confirmados[0].nota.includes(schedulePendingText));
assert.strictEqual(schedulePending.lightning_start_date, '', 'a resposta não deve alterar a análise ou escolher datas');
const rejectedSummary = context.montarLinhasResultadoParticipacao(pendingPayload, {
  detalhes: assignedResult.detalhes.map(detail => ({ ...detail, outcome: 'rejected', success: false })),
});
assert(rejectedSummary[0].falhas[0].nota.includes(schedulePendingText), 'um resultado recusado não confirma calendário');
const contradictorySummary = context.montarLinhasResultadoParticipacao(pendingPayload, {
  detalhes: assignedResult.detalhes.map(detail => ({ ...detail, error: 'Resultado ainda não confirmado.' })),
});
assert(contradictorySummary[0].indeterminados[0].nota.includes(schedulePendingText), 'resultado com erro não confirma calendário');

const partialWindow = { ...schedulePending, lightning_start_date: rows[0].lightning_start_date,
  lightning_action_blocked: true, lightning_action_reason: 'Janela parcial da oferta não confirmada.' };
installAnalysis([partialWindow]);
assert(!context.formatarJanelaOfertaRelampago(partialWindow).includes(schedulePendingText));
assert.strictEqual(context.montarPayloadParticipacoesPromocoes(0).promocoes.length, 0);
assert.strictEqual(context.montarPayloadParticipacoesPromocoes(null, { automatica: true }).promocoes.length, 0);
installAnalysis([{ ...schedulePending, lightning_stock: null }]);
assert.strictEqual(context.montarPayloadParticipacoesPromocoes(0).promocoes.length, 0);
assert.strictEqual(context.montarPayloadParticipacoesPromocoes(null, { automatica: true }).promocoes.length, 0);
installAnalysis([{ ...schedulePending, lightning_action_blocked: true, lightning_action_reason: invalidReason }]);
assert.strictEqual(context.montarPayloadParticipacoesPromocoes(0).promocoes.length, 0, 'agenda pendente não contorna bloqueio técnico');
installAnalysis([{ ...schedulePending, 'Ação': 'Não participar' }]);
assert.strictEqual(context.montarPayloadParticipacoesPromocoes(0).promocoes.length, 0, 'agenda pendente não aprova a análise');
assert.strictEqual(context.montarPayloadParticipacoesPromocoes(null, { automatica: true }).promocoes.length, 0);
installAnalysis([schedulePending]);
vm.runInContext("apiAnalisesPorCampanha[0].origem_loja = 'Outra Loja'", context);
assert.throws(() => context.montarPayloadParticipacoesPromocoes(0), /não pertence/);
vm.runInContext("apiAnalisesPorCampanha[0].origem_loja = 'Loja Teste'; apiAnalisesPorCampanha[0].origem_client_id = 'outro-cliente'", context);
assert.throws(() => context.montarPayloadParticipacoesPromocoes(0), /não pertence/);
installAnalysis([schedulePending]);

async function checkParticipation() {
  const previewPromise = context.pedirConfirmacaoParticipacaoCampanhaApi({
    nome: 'Ofertas relâmpago', total: 1, loja: 'Loja Teste', promo: pendingPayload.promocoes[0],
    analise: { data: [schedulePending], promo_b_id: 'LGH-TESTE', promo_b_type: 'LIGHTNING' }, campanha: lightning,
  });
  const preview = context.document.body.children[context.document.body.children.length - 1];
  assert(preview.innerHTML.includes('Agenda das ofertas relâmpago'));
  assert(preview.innerHTML.includes('1 oferta(s) serão enviadas à API com a agenda ainda indisponível.'));
  assert(preview.innerHTML.includes(schedulePendingText));
  preview.listeners.click({ target: { closest: selector => selector === '[data-cancel-promo-participation]' } });
  assert.strictEqual(await previewPromise, false);
  const approved = rows.map(row => ({ ...row, 'Ação': 'Participar' }));
  installAnalysis(approved);
  const messages = [], requests = [], alerts = [];
  context.alert = message => alerts.push(message);
  context.getApiAutoPrefs = () => ({ enabled: true, approvalRequired: false });
  context.atualizarStatusAutomacaoPromo = message => messages.push(message);
  context.getAuthHeadersWithClient = headers => headers;
  context.pedirConfirmacaoParticipacaoCampanhaApi = async () => true;
  for (const name of ['iniciarStatusParticipacaoPromocoes', 'concluirStatusParticipacaoPromocoes',
    'falharStatusParticipacaoPromocoes', 'mostrarResumoParticipacaoPromocoes']) context[name] = () => {};
  context.fetch = async (url, options) => {
    assert.strictEqual(url, '/api/promo/aplicar-participacoes');
    assert.strictEqual(options.method, 'POST');
    const sent = JSON.parse(options.body);
    requests.push(sent);
    return { ok: true, json: async () => ({ detalhes: sent.promocoes.flatMap(promo => promo.items.map(item => ({
      promotion_id: promo.promotion_id, item_id: item.item_id, client_ref: item.client_ref,
      outcome: 'applied', success: true,
    }))) }) };
  };
  await context.confirmarParticipacaoCampanhaApi(0);
  assert.strictEqual(requests.length, 1, 'a confirmação manual envia as três ofertas aprovadas');
  assertOfferPayload(requests[0].promocoes[0].items, approved);
  const automaticResult = await context.executarParticipacaoAutomaticaPromocoes();
  assert.strictEqual(requests.length, 2, 'a automação envia as três ofertas aprovadas');
  assertOfferPayload(requests[1].promocoes[0].items, approved);
  assert.strictEqual(automaticResult.total_sucesso, 3);
  assert(messages.some(message => message.includes('Entrando em 3 anuncio(s)')));

  context.getApiAutoPrefs = () => ({ enabled: true, approvalRequired: true });
  assert.strictEqual(await context.executarParticipacaoAutomaticaPromocoes(), null);
  assert.strictEqual(requests.length, 2, 'a preferência Não aprovar automaticamente continua respeitada');
  assert(messages[messages.length - 1].includes('aprovacao automatica esta desativada'));
  context.getApiAutoPrefs = () => ({ enabled: true, approvalRequired: false });
  installAnalysis(approved.map(row => ({ ...row, 'Ação': 'Não participar' })));
  assert.strictEqual(await context.executarParticipacaoAutomaticaPromocoes(), null);
  await context.confirmarParticipacaoCampanhaApi(0);
  assert.strictEqual(requests.length, 2, 'linhas recusadas pela análise não são enviadas');
  installAnalysis(invalid);
  assert.strictEqual(await context.executarParticipacaoAutomaticaPromocoes(), null);
  assert(messages[messages.length - 1].includes('1 oferta(s) relâmpago sem envio automático'));
  assert(messages[messages.length - 1].includes(invalidReason));
  await context.confirmarParticipacaoCampanhaApi(0);
  assert.strictEqual(requests.length, 2, 'dados inválidos impedem chamadas manuais e automáticas');
  assert.strictEqual(alerts[alerts.length - 1], invalidReason);

  installAnalysis([schedulePending]);
  await context.confirmarParticipacaoCampanhaApi(0);
  assert.strictEqual(requests.length, 3, 'candidata sem agenda pode ser confirmada manualmente');
  assertOfferPayload(requests[2].promocoes[0].items, [schedulePending]);
  const pendingAutomaticResult = await context.executarParticipacaoAutomaticaPromocoes();
  assert.strictEqual(requests.length, 4, 'candidata aprovada sem agenda pode ser enviada automaticamente');
  assert.strictEqual(pendingAutomaticResult.total_sucesso, 1);
  assertOfferPayload(requests[3].promocoes[0].items, [schedulePending]);
  context.getApiAutoPrefs = () => ({ enabled: true, approvalRequired: true });
  assert.strictEqual(await context.executarParticipacaoAutomaticaPromocoes(), null);
  assert.strictEqual(requests.length, 4, 'agenda pendente preserva Não aprovar automaticamente');
  assert(messages[messages.length - 1].includes('aprovacao automatica esta desativada'));
}
checkParticipation().then(() => console.log('promocoes lightning multiple date participation runtime checks passed')).catch(error => {
  console.error(error);
  process.exitCode = 1;
});
