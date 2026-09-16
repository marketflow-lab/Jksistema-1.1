'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const context = {
  TABLE_COLUMNS_MODERN: [], TABLE_COLUMN_ALIASES: {},
  currentData: [], apiAnalisesPorCampanha: [],
  document: { getElementById: () => ({ value: 'Loja Teste' }) },
  findApiPromoBCampaignById: () => ({}),
  getPromoClientId: () => 'cliente-teste',
  escapeHtml: value => String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'),
};
vm.createContext(context);
for (const file of ['participacao.js', 'tabela-preferencias.js']) {
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'static/frontend_promo', file), 'utf8'), context);
}

function setup(rows, campaign = 'P-TESTE') {
  context.currentData = rows;
  context.apiAnalisesPorCampanha = [{ promo_b_id: campaign, promo_b_type: 'SMART', data: rows, origem_loja: 'Loja Teste', origem_client_id: 'cliente-teste' }];
  return context.montarPayloadParticipacoesPromocoes(0);
}
const rows = Array.from({ length: 236 }, (_, i) => ({
  MLB: `MLB${100000 + i}`, 'Ação': 'Participar', action_financeiro_exato: i < 11,
  action_promotion_id: 'P-TESTE', action_offer_id: `CANDIDATE-MLB${100000 + i}-TESTE`,
  action_deal_price: 25, action_discount_percentage: 5,
}));
let payload = setup(rows);
assert.strictEqual(payload.promocoes[0].items.length, 236, 'toda escolha humana deve compor a operação');
assert.strictEqual(context.obterLinhasSelecionadasPromocoes(rows).length, 236);
assert.strictEqual(new Set(payload.promocoes[0].items.map(item => item.client_ref)).size, 236);
assert.strictEqual(context.montarPayloadParticipacoesPromocoes(null, { automatica: true }).promocoes[0].items.length, 11, 'automação mantém regra financeira');
rows[0]['Ação'] = 'Não participar';
assert.strictEqual(context.montarPayloadParticipacoesPromocoes(0).promocoes[0].items.length, 235);

payload = setup([{
  'Ação': 'Participar', SKU: 'SEM-MLB', action_financeiro_exato: false,
  action_promotion_id: 'P-OUTRA', action_offer_id: '', action_deal_price: null,
  deal_price: 99, offer_id: 'OFFER-OUTRA', 'ML % Campanha': '10%',
}], '');
let item = payload.promocoes[0].items[0];
assert.strictEqual(item.item_id, '', 'ausência de MLB não remove a seleção');
assert.strictEqual(payload.promocoes[0].promotion_id, '', 'ausência de campanha terá impedimento explícito');
assert.strictEqual(item.action_promotion_id, 'P-OUTRA', 'backend recebe identidade para validar divergência');
assert.strictEqual(item.deal_price, null, 'não usar preço de exibição quando falta preço da ação');
assert.strictEqual(item.offer_id, '');
assert.strictEqual(item.discount_percentage, null);

payload = setup([{ MLB: 'MLB111', 'Ação': 'Participar', deal_price: 29, offer_id: 'CANDIDATE-MLB111-LEGACY' }]);
assert.strictEqual(payload.promocoes[0].items[0].deal_price, 29, 'payload legado mantém aliases');
context.apiAnalisesPorCampanha[0].origem_loja = 'Outra Loja';
assert.throws(() => context.montarPayloadParticipacoesPromocoes(0), /não pertence/);
context.apiAnalisesPorCampanha[0].origem_loja = 'Loja Teste';
context.apiAnalisesPorCampanha[0].origem_client_id = 'outro-cliente';
assert.throws(() => context.montarPayloadParticipacoesPromocoes(0), /não pertence/);

const items = Array.from({ length: 351 }, (_, i) => ({ item_id: `MLB${i + 100}`, client_ref: `0:${i}` }));
payload = { loja: 'Loja Teste', promocoes: [{ promotion_id: 'P-TESTE', items }] };
const details = items.map(i => ({ ...i, promotion_id: 'P-TESTE', outcome: 'applied', success: true }));
let result = { detalhes: details };
let lines = context.montarLinhasResultadoParticipacao(payload, result);
assert.strictEqual(lines[0].confirmados.length, 351);
assert.strictEqual(context.resumirResultadoParticipacao(payload, result).success, true);
const page = context.renderListaResultadoParticipacao('Confirmados', lines[0].confirmados, 7, '0-confirmados');
assert(page.includes('MLB450'));
assert(!page.includes('MLB449'));
assert(page.includes('Página 8 de 8'));
assert(page.includes('data-result-page="6"'));
assert(context.renderListaResultadoParticipacao('<teste>', [{itemId: '<img>', nota: '<script>'}]).includes('&lt;script>'));

details[0] = { ...details[0], outcome: 'blocked', success: false, message: 'Sem preço' };
details[1] = { ...details[1], outcome: 'rejected', success: false, message: 'Recusa ML' };
details[2] = { ...details[2], outcome: 'already_participating', success: true, ignored: true };
details[3] = { ...details[3], outcome: 'unknown', success: false, message: 'Timeout' };
details.splice(4, 1);
lines = context.montarLinhasResultadoParticipacao(payload, result);
assert.strictEqual(lines[0].confirmados.length, 346);
assert.strictEqual(lines[0].impedidos.length, 1);
assert.strictEqual(lines[0].falhas.length, 1);
assert.strictEqual(lines[0].ignorados.length, 1);
assert.strictEqual(lines[0].indeterminados.length, 2, 'ausência de detalhe é indeterminação');
const summary = context.resumirResultadoParticipacao(payload, { ...result, success: true, total_falha: 0 });
assert.strictEqual(summary.total_itens, 351);
assert.strictEqual(summary.total_falha, 4);
assert.strictEqual(summary.success, false, 'resumo incompleto não confirma campanha');
assert.strictEqual(context.resumirResultadoParticipacao(payload, {}).total_falha, 351);
assert.strictEqual(context.resumirResultadoParticipacao({ promocoes: [] }, {}).success, false);

// Referências distinguem duas linhas sem MLB e não atravessam campanhas.
payload = setup([{ 'Ação': 'Participar', SKU: 'A' }, { 'Ação': 'Participar', SKU: 'B' }]);
result = { detalhes: [
  { promotion_id: 'P-TESTE', client_ref: '0:0', outcome: 'blocked', success: false, message: 'Sem MLB A' },
  { promotion_id: 'P-OUTRA', client_ref: '0:1', outcome: 'applied', success: true },
] };
lines = context.montarLinhasResultadoParticipacao(payload, result);
assert.strictEqual(lines[0].impedidos[0].nota, 'SKU A · Sem MLB A');
assert.strictEqual(lines[0].impedidos[0].itemId, 'Selecionado 1 sem MLB');
assert.strictEqual(lines[0].indeterminados.length, 1);
result.detalhes[1] = { promotion_id: 'P-TESTE', client_ref: '0:1', outcome: 'blocked', success: false, message: 'Sem MLB A' };
lines = context.montarLinhasResultadoParticipacao(payload, result);
const missingMlbHtml = context.renderListaResultadoParticipacao('Impedidos', lines[0].impedidos);
assert(missingMlbHtml.includes('SKU A'));
assert(missingMlbHtml.includes('SKU B'), 'o motivo não pode ocultar a identificação das linhas sem MLB');

async function checkConfirmation() {
  let sent;
  let displayed;
  let mode = 'success';
  const dataset = [{ MLB: 'MLB123', 'Ação': 'Participar', action_financeiro_exato: false }];
  setup(dataset);
  Object.assign(context, {
    pedirConfirmacaoParticipacaoCampanhaApi: async args => { assert.strictEqual(args.total, 1); return true; },
    atualizarStatusAutomacaoPromo() {}, iniciarStatusParticipacaoPromocoes() {},
    concluirStatusParticipacaoPromocoes() {}, falharStatusParticipacaoPromocoes() {},
    renderApiAnalysisTabs() {}, getAuthHeadersWithClient: () => ({}),
    buildApiErrorMessage: () => 'Falha',
    mostrarResumoParticipacaoPromocoes: (_payload, response) => { displayed = response; },
    fetch: async (_url, options) => {
      sent = JSON.parse(options.body);
      if (mode === 'timeout') throw new Error('Timeout');
      if (mode === 'selection-changed') context.apiAnalisesPorCampanha[0].participacao_selecao_revisao = 1;
      return { ok: true, json: async () => mode === 'empty' ? {} : { detalhes: [{
        ...sent.promocoes[0].items[0], promotion_id: 'P-TESTE', outcome: 'applied', success: true,
      }] } };
    },
  });
  await context.confirmarParticipacaoCampanhaApi(0);
  assert.strictEqual(sent.promocoes[0].items.length, 1);
  assert.strictEqual(context.apiAnalisesPorCampanha[0].participacao_confirmada, true);
  mode = 'selection-changed';
  await context.confirmarParticipacaoCampanhaApi(0);
  assert.strictEqual(context.apiAnalisesPorCampanha[0].participacao_confirmada, false, 'sucesso da seleção anterior não confirma seleção editada durante envio');
  mode = 'empty';
  await context.confirmarParticipacaoCampanhaApi(0);
  assert.strictEqual(context.apiAnalisesPorCampanha[0].participacao_confirmada, false);
  assert.strictEqual(displayed.total_falha, 1);
  mode = 'timeout';
  await context.confirmarParticipacaoCampanhaApi(0);
  assert.strictEqual(context.apiAnalisesPorCampanha[0].participacao_confirmada, false);
  assert.strictEqual(context.apiAnalisesPorCampanha[0].participacao_em_andamento, false);
  assert.strictEqual(displayed.total_falha, 1);
  let alerts = 0;
  context.alert = () => { alerts++; };
  context.pedirConfirmacaoParticipacaoCampanhaApi = async () => {
    context.apiAnalisesPorCampanha[0].participacao_selecao_revisao++;
    return true;
  };
  sent = null;
  await context.confirmarParticipacaoCampanhaApi(0);
  assert.strictEqual(sent, null, 'seleção alterada no modal exige nova confirmação');
  assert.strictEqual(alerts, 1);
}
checkConfirmation().then(() => console.log('promocoes participation runtime checks passed')).catch(error => {
  console.error(error);
  process.exitCode = 1;
});
