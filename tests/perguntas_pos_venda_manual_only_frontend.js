const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const read = (...parts) => fs.readFileSync(path.join(root, ...parts), 'utf8');

const lojas = read('static', 'perguntas_pos_venda', 'lojas-automacao.js');
const perguntas = read('static', 'perguntas_pos_venda', 'perguntas.js');
const posVenda = read('static', 'perguntas_pos_venda', 'pos-venda.js');
const monitor = read('static', 'ia-sidebar', '05-perguntas-monitor.part.js');
const approvals = read('static', 'ia-sidebar', '08-aprovacoes.part.js');
const codex = read('static', 'ia-sidebar', '03-init-shell-codex.part.js');
const loader = read('static', 'ia-sidebar.js');
const canonicalHtml = read('perguntas_pos_venda.html');
const mirrorHtml = read('static', 'perguntas_pos_venda.html');

assert.strictEqual(canonicalHtml, mirrorHtml, 'espelhos HTML de Perguntas e pos-venda devem ser identicos');
assert.doesNotMatch(canonicalHtml, /tab-pos-venda-notificacao/);
assert.doesNotMatch(canonicalHtml, /data-training-type="pos_venda"/);
assert.doesNotMatch(canonicalHtml, /Gerar resposta com IA/);
assert.match(posVenda, /id="pos-venda-resposta-texto"/);
assert.match(posVenda, /id="btn-pos-venda-enviar-resposta"/);
assert.match(posVenda, /\/api\/mercadolivre\/pos-venda\/conversas\/responder/);
assert.doesNotMatch(posVenda, /btn-pos-venda-gerar-ia/);
assert.doesNotMatch(posVenda, /conversas\/gerar-resposta/);
assert.doesNotMatch(posVenda, /preencherRespostaPosVenda/);

assert.doesNotMatch(lojas, /pos-venda\/automacao\/poll/);
assert.doesNotMatch(lojas, /buscarContadorPosVendaNaoLidas/);
assert.doesNotMatch(lojas, /summary_only:\s*'true'/);
assert.doesNotMatch(lojas, /data-config="habilitar_pos_venda_automatico"/);
assert.match(lojas, /habilitar_pos_venda_automatico:\s*false/g);
assert.match(lojas, /function notificarAprovacaoSidebar[\s\S]*?aprovacaoEhPosVenda\(aprovacao\)\) return;/);
assert.match(lojas, /const pendentes =[\s\S]*?\.filter\(\(item\) => !aprovacaoEhPosVenda\(item\)\)/);

assert.match(perguntas, /function sugestaoEhPosVenda[\s\S]*?payload\.ia_origem[\s\S]*?includes\('pos_venda'\)/);
assert.match(perguntas, /if \(sugestaoEhPosVenda\(payload\)\) return \{ ok: false, blocked: true/);
assert.match(perguntas, /if \(sugestaoEhPosVenda\(payload\)\) return;[\s\S]*?preencherRespostaPerguntaSugerida/);
assert.match(perguntas, /preencherRespostaPerguntaSugerida/);
assert.match(perguntas, /question-ai-answer-btn/);

assert.match(monitor, /_perguntasMostrarNotificacaoWindows[\s\S]*?_perguntasIsPosVenda\(approval\)\) return;/);
assert.match(monitor, /data\.pendentes[\s\S]*?filter\(approval => !_perguntasIsPosVenda\(approval\)\)/);
for (const name of ['_approvalSalvarNoHistorico', '_approvalMontarCard', '_approvalPersistirNoHistoricoBlackJhon', '_approvalRenderNoPainelBlackJhon', '_adicionarNotificacaoAprovacao']) {
  assert.match(approvals, new RegExp(`function ${name}[\\s\\S]*?_approvalEhPosVenda\\(payload\\)\\) return`), `${name} deve bloquear pos-venda`);
}
assert.match(approvals, /_approvalUsarRespostaNaTela[\s\S]*?blocked: true/);
assert.match(codex, /ehPosVenda\) return null/);
assert.match(codex, /ml\.pos_venda_responder'\) return null/);

const htmlFiles = [
  'configuracoes.html', 'favoritos.html', 'vendas.html', 'perguntas_pos_venda.html',
  path.join('static', 'configuracoes.html'), path.join('static', 'favoritos.html'),
  path.join('static', 'vendas.html'), path.join('static', 'perguntas_pos_venda.html'),
];
for (const file of htmlFiles) {
  assert.match(read(file), /\/ia-sidebar\.js\?v=20260722-black-jhon-unified-pos-venda-manual-v1/, `${file} precisa do cache-buster novo`);
}
assert.match(loader, /const VERSION = '20260722-black-jhon-unified-pos-venda-manual-v1'/);

const start = lojas.indexOf('function aprovacaoEhPosVenda');
const end = lojas.indexOf('async function carregarAprovacoesPendentes', start);
assert.ok(start >= 0 && end > start, 'funcao de notificacao precisa existir');
const sandbox = {
  state: { aprovacoesNotificadas: new Set() },
  window: {},
};
vm.createContext(sandbox);
vm.runInContext(`${lojas.slice(start, end)}; this.notificar = notificarAprovacaoSidebar;`, sandbox);
sandbox.notificar({ id: 'pv-1', tipo: 'pos_venda' });
assert.strictEqual(sandbox.state.aprovacoesNotificadas.size, 0, 'pos-venda nao pode entrar na fila de notificacao');
sandbox.notificar({ id: 'pv-legado', origem: 'geracao_pos_venda' });
assert.strictEqual(sandbox.state.aprovacoesNotificadas.size, 0, 'pos-venda legado sem tipo tambem deve ser bloqueado');
sandbox.notificar({ id: 'q-1', tipo: 'perguntas_anuncio' });
assert.strictEqual(sandbox.state.aprovacoesNotificadas.has('q-1'), true, 'Perguntas publicas continuam notificando');
assert.strictEqual(sandbox.window.__JK_PENDING_IA_APPROVALS__.length, 1);

console.log('OK: pos-venda manual, sem notificacao/sugestao IA; Perguntas publicas preservadas.');
