'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const publicHtml = fs.readFileSync(path.join(root, 'perguntas_pos_venda.html'), 'utf8');
const staticHtml = fs.readFileSync(path.join(root, 'static', 'perguntas_pos_venda.html'), 'utf8');
const sourcePath = path.join(root, 'static', 'perguntas_pos_venda', 'solicitacoes.js');
const questionsSource = fs.readFileSync(path.join(root, 'static', 'perguntas_pos_venda', 'perguntas.js'), 'utf8');

assert.strictEqual(
  publicHtml,
  staticHtml,
  'os dois HTML servidos pelo app devem permanecer byte a byte iguais',
);
assert.match(staticHtml, /id="tab-solicitacao"[^>]*role="tab"[^>]*aria-controls="aba-solicitacao"[^>]*>Solicitação</);
assert.match(staticHtml, /id="aba-solicitacao"[^>]*role="tabpanel"[^>]*aria-labelledby="tab-solicitacao"/);
assert.match(staticHtml, /id="solicitacoes-status-filtro"/);
assert.match(staticHtml, /id="solicitacoes-list"[^>]*aria-live="polite"/);
assert.match(staticHtml, /id="solicitacoes-pagination"/);
assert.match(staticHtml, /perguntas_pos_venda\/solicitacoes\.js\?v=[^"']+/);
assert(fs.existsSync(sourcePath), 'o carregador da aba deve ficar em um modulo proprio');

const source = fs.readFileSync(sourcePath, 'utf8');
assert.match(source, /async function carregar\s*\(/);
assert.match(source, /window\.JKSolicitacoes\s*=\s*Object\.freeze\s*\(\s*\{[\s\S]*carregar/);
assert.match(source, /\/api\/mercadolivre\/assistant\/solicitacoes/);
assert.match(source, /store_id/);
assert.match(source, /status/);
assert.match(source, /limit/);
assert.match(source, /offset/);
assert.match(source, /solicitacao-card/);
assert.match(source, /conclusao/);
assert.match(source, /evidencias/);
assert.match(source, /avisos/);
assert.match(
  questionsSource,
  /\/api\/mercadolivre\/perguntas\/resposta\/gerar[\s\S]{0,900}store_id:\s*String\(pergunta\.store_id\s*\|\|\s*''\)/,
  'a criacao manual deve enviar o store_id canonico exibido na pergunta',
);

console.log('Perguntas solicitacoes frontend contract: OK');
