'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const read = (...parts) => fs.readFileSync(path.join(root, ...parts), 'utf8');
const core = require(path.join(root, 'static', 'pesquisa-mercado', 'core.js'));

assert.strictEqual(
    core.normalizarEntradaVendedor('123456789'),
    'https://lista.mercadolivre.com.br/_CustId_123456789'
);
assert.strictEqual(
    core.normalizarEntradaVendedor('@LOJA_TESTE'),
    'https://www.mercadolivre.com.br/perfil/LOJA_TESTE'
);
assert.throws(() => core.normalizarEntradaVendedor('https://example.com/perfil/loja'), /Mercado Livre Brasil/);
assert.strictEqual(core.extrairMlb('https://produto.mercadolivre.com.br/MLB-1234567890-item'), 'MLB1234567890');

assert.strictEqual(core.normalizarNumeroVendas('+10 mil vendidos'), 10000);
assert.strictEqual(core.normalizarNumeroVendas('1,2 mil vendas'), 1200);
assert.strictEqual(core.normalizarNumeroVendas('2.345 vendidos'), 2345);
assert.strictEqual(core.normalizarNumeroVendas('234 vendas'), 234);
assert.strictEqual(core.normalizarNumeroVendas('nao encontrada'), null);

const deduplicados = core.consolidarAnuncios([
    { id: 'MLB1234567890', url: 'https://produto.mercadolivre.com.br/MLB-1234567890-a', titulo: 'A', vendas: '10' },
    { id: 'MLB1234567890', url: 'https://produto.mercadolivre.com.br/MLB-1234567890-a', titulo: 'A completo', vendas: '20' },
    { id: 'MLB9999999999', url: 'https://produto.mercadolivre.com.br/MLB-9999999999-b', titulo: 'B', vendas: '5' }
]);
assert.strictEqual(deduplicados.length, 2);
assert.strictEqual(deduplicados[0].vendas, 20);
assert.strictEqual(deduplicados[0].titulo, 'A completo');

const alvo = { vendedor: 'Loja Alvo', perfilUrl: 'https://www.mercadolivre.com.br/perfil/LOJA_ALVO' };
const card = {
    id: 'MLB1234567890',
    url: 'https://produto.mercadolivre.com.br/MLB-1234567890-a',
    titulo: 'Produto alvo',
    vendas: 20,
    vendasFonte: 'card_perfil'
};
const divergente = core.aplicarDetalhe(card, {
    vendas: 999,
    vendasFonte: 'pagina_anuncio',
    vendedor: 'Outra Loja',
    perfilUrl: 'https://www.mercadolivre.com.br/perfil/OUTRA_LOJA'
}, alvo);
assert.strictEqual(divergente.vendas, 20, 'nao deve atribuir ao alvo vendas mostradas por outro vendedor');
assert.strictEqual(divergente.status, 'vendedor_divergente');

const confirmado = core.aplicarDetalhe(card, {
    vendas: 80,
    vendasFonte: 'pagina_anuncio',
    vendedor: 'Loja Alvo',
    perfilUrl: 'https://www.mercadolivre.com.br/perfil/LOJA_ALVO'
}, alvo);
assert.strictEqual(confirmado.vendas, 80);
assert.strictEqual(confirmado.status, 'analisado');

const ranking = core.filtrarMaisVendidos([
    { id: 'MLB1111111111', url: 'https://produto.mercadolivre.com.br/MLB-1111111111-a', titulo: 'A', vendas: 5 },
    { id: 'MLB2222222222', url: 'https://produto.mercadolivre.com.br/MLB-2222222222-b', titulo: 'B', vendas: 50 },
    { id: 'MLB3333333333', url: 'https://produto.mercadolivre.com.br/MLB-3333333333-c', titulo: 'C', vendas: null }
], { minimo: 5, limite: 1 });
assert.deepStrictEqual(ranking.map(item => item.id), ['MLB2222222222']);

const html = read('static', 'pesquisa_mercado.html');
const rootHtml = read('pesquisa_mercado.html');
const runtime = read('static', 'pesquisa-mercado', 'runtime.js');
const dashboard = read('static', 'dashboard.html');
const sidebar = read('static', 'ia-sidebar', '03-init-shell-codex.part.js');
const shell = read('electron_shell.html');
const installer = JSON.parse(read('electron_app', 'installer-required-resources.json'));

assert.strictEqual(rootHtml, html, 'HTML raiz e fonte canonica static devem ser identicos');
assert.match(html, /<h1>Pesquisa de Mercado<\/h1>/);
assert.match(html, /vendas acumuladas/i);
assert.match(html, /pm-btn-pausar/);
assert.match(html, /pm-btn-cancelar/);
assert.match(runtime, /MAX_PROFILE_PAGES/);
assert.match(runtime, /pagina\.nextUrl/);
assert.match(runtime, /await navegar\(anuncio\.url\)/);
assert.match(runtime, /jk-ml-browser-show/);
assert.match(runtime, /jk-ml-browser-execute/);
assert.match(runtime, /vendedor_divergente/);

assert.match(dashboard, /key: 'pesquisa_mercado', permissionKey: 'favoritos'/);
assert.match(sidebar, /key: 'pesquisa_mercado', permissionKey: 'favoritos'/);
assert.match(sidebar, /'pesquisa_mercado\.html': 'pesquisa_mercado'/);
assert.match(shell, /'pesquisa_mercado\.html': 'Pesquisa de Mercado'/);
assert.match(shell, /label: 'PM'.*pesquisa de mercado/);

for (const required of [
    'pesquisa_mercado.html',
    'static/pesquisa_mercado.html',
    'static/pesquisa-mercado/core.js',
    'static/pesquisa-mercado/runtime.js'
]) {
    assert.ok(installer.requiredSourceFiles.includes(required), `manifesto deve exigir ${required}`);
}
assert.ok(installer.mirrorPairs.some(pair => pair.source === 'pesquisa_mercado.html' && pair.mirror === 'static/pesquisa_mercado.html'));

console.log('Pesquisa de Mercado: contratos OK');
