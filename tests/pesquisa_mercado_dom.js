'use strict';
const assert = require('assert');
const { chromium } = require('playwright');
const scripts = require('../static/pesquisa-mercado/page-scripts');
const { normalizeCategory } = require('../electron_app/main/modules/pesquisa-mercado-runner');
const { collectFirstPage } = require('../static/pesquisa-mercado/shared-collector');
const { createPageScriptRenderer } = require('./helpers/favoritos_browser_sources');
const path = require('path');
const originalAvant = createPageScriptRenderer(path.resolve(__dirname, '..'));
const seller = { id: '123456', nome: 'Loja Alvo', perfilUrl: 'https://www.mercadolivre.com.br/perfil/LOJA_ALVO' };

function avant(sales, name = 'Loja Alvo', metric = 'Vendas do produto', sellerLink = '') {
    const row = (label, value) => `<div class="avantpro-product-info-row"><span class="avantpro-product-info-row-label">${label}</span><span class="avantpro-product-info-row-value">${value}</span></div>`;
    return `<section class="avantpro-product-info">${row(metric, sales)}${row('Ritmo atual', '4,5')}${row('Nome do vendedor', name)}${row('Anuncio criado em', '01/01/2026')}${sellerLink}</section>`;
}
function card(index, extra = '') {
    const id = 10000000 + index;
    return `<li class="ui-search-layout__item"><article class="poly-card"><a class="poly-component__title" href="https://produto.mercadolivre.com.br/MLB-${id}-produto-_JM">Produto ${index}</a><span class="andes-money-amount__fraction">59</span>${extra}</article></li>`;
}
function fixture(cards) { return `<html><head><style>li{min-height:80px}button{width:160px;height:32px}</style></head><body><main><ol class="ui-search-layout">${cards}</ol></main></body></html>`; }
function liveAvantRow(label, value, trailing = '') {
    return `<div class="avantpro-product-info-row"><div class="avantpro-product-info-row-content"><span class="avantpro-product-info-row-label">${label}</span><div class="avantpro-product-info-row-value-line"><span class="avantpro-product-info-row-value" title="${value}">${value}</span>${trailing}</div></div></div>`;
}

// Public PDP template classes from Avant Pro 7.0.3 / 7.32.3; values and identity are synthetic.
function pdpStat(label, value) {
    return '<div class="avt-relative avt-flex avt-min-h-[72px] avt-flex-col avt-items-center avt-justify-center avt-rounded-lg avt-bg-white avt-p-3 avt-text-center avt-border avt-border-gray-200">'
        + '<div class="avt-absolute avt-right-1 avt-top-1"><span>Tooltip 999999</span></div>'
        + '<div class="avt-mb-1 avt-flex avt-items-center avt-gap-1 avt-text-sm avt-text-gray-500"><span>' + label + '</span></div>'
        + '<div class="avt-text-base avt-font-semibold avt-text-gray-700">' + value + '</div></div>';
}
function pdpPill(value) {
    return '<div class="avt-relative avt-flex avt-items-center avt-gap-1.5 avt-rounded-full avt-border avt-px-3 avt-py-1.5 avt-text-sm avt-font-semibold">'
        + '<span class="avt-min-w-0 avt-flex-1">' + value + '</span></div>';
}
function pdpRoot({ total = 100, rhythm = 10, monthly = '5/Mês', created = '12/03/2025', attrs = '', extra = '' } = {}) {
    return '<div id="productInfo" class="avantpro-in-product-content" ' + attrs + '>'
        + (total === null ? '' : pdpPill(total + ' Vendidos')) + pdpPill('888 Estoque') + pdpStat('Vendas estimadas', '155')
        + (rhythm === null ? '' : pdpStat('Ritmo atual (vendas/mês)', rhythm))
        + (monthly === null ? '' : pdpStat('Vendas mensais', monthly))
        + (created ? pdpStat('Anúncio criado em', created) : '') + extra + '</div>';
}

async function run() {
    const browser = await chromium.launch({ headless: true });
    try {
        const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
        await page.route('**/*', route => route.fulfill({ contentType: 'text/html', body: '<html><body></body></html>' }));
        await page.goto('https://lista.mercadolivre.com.br/_CustId_123456');
        await page.goto('https://www.mercadolivre.com.br/item/up/MLBU123456?wid=MLB10000001');
        await page.setContent('<main><h1 class="ui-pdp-title">Titulo do produto</h1><section class="ui-pdp-seller"><a class="ui-pdp-seller__link-trigger" href="https://www.mercadolivre.com.br/loja/tivui">Ir para a loja oficial</a><span class="ui-pdp-seller__header__title">Tivui</span></section></main>');
        const namedOfficial = await page.evaluate(scripts.discoveryScript());
        assert.equal(namedOfficial.vendedor, 'Tivui', 'nome dedicado tem precedencia sobre chamada de navegacao');
        assert.equal(namedOfficial.seller_name_kind, 'store_brand');
        await page.setContent('<main><h1 class="ui-pdp-title">Titulo do produto</h1><section class="ui-pdp-seller"><a class="ui-pdp-seller__link-trigger" href="https://www.mercadolivre.com.br/loja/tivui">Ir para a loja oficial</a></section><section class="recommendations"><span class="ui-pdp-seller__header__title">Loja recomendada</span></section></main>');
        assert.equal((await page.evaluate(scripts.discoveryScript())).vendedor, '', 'botao, titulo do produto e recomendacao nao viram nome da loja');
        await page.goto('https://www.mercadolivre.com.br/loja/tivui');
        await page.setContent('<main><h1>Tivui</h1><a href="https://lista.mercadolivre.com.br/loja/tivui/">Todos os produtos</a></main>');
        assert.equal((await page.evaluate(scripts.discoveryScript())).vendedor, 'Tivui', 'titulo da pagina da loja confirma a marca');
        await page.goto('https://lista.mercadolivre.com.br/_CustId_123456');
        await page.setContent('<main><a class="ui-pdp-seller__link-trigger" href="https://lista.mercadolivre.com.br/_CustId_999">Loja estrangeira</a><span class="ui-pdp-seller__header__title">Loja estrangeira</span></main>');
        const wrongHeader = await page.evaluate(scripts.discoveryScript());
        assert.equal(wrongHeader.vendedor, '', 'URL do vendedor alvo nao comprova nome de um header com outro ID');
        assert.equal(wrongHeader.seller_identity_conflict, true);
        const foreignNameCard = card(1, avant(50, 'Outro vendedor', 'Vendas do produto', '<a href="https://lista.mercadolivre.com.br/_CustId_999">Outro vendedor</a>'))
            .replace('<article class="poly-card">', '<article class="poly-card" data-seller-id="123456">');
        await page.setContent(fixture(foreignNameCard));
        const foreignName = await page.evaluate(scripts.snapshotScript({ seller }));
        assert.equal(foreignName.anuncios[0].seller_confirmed, false, 'identidade Avant divergente invalida o nome mesmo com ID do card igual ao alvo');
        assert.equal(foreignName.anuncios[0].vendedor, '');
        assert.equal(foreignName.anuncios[0].rankable, false);
        await page.setContent(fixture(card(1, avant(50, 'Ir para a loja oficial'))));
        const genericAvantName = await page.evaluate(scripts.snapshotScript({ seller: { ...seller, nome: 'Nome pendente — vendedor 123456' } }));
        assert.equal(genericAvantName.anuncios[0].vendedor, '', 'chamada de navegacao do Avant nao vira nome nem conflita com nome pendente');
        await page.setContent(fixture(Array.from({ length: 121 }, (_, i) => card(i, avant(i + 1))).join('')));
        const all = await page.evaluate(scripts.snapshotScript({ seller }));
        assert.equal(all.readyState, 'complete', 'snapshot informa o estado de carregamento usado pelo coordenador');
        assert.equal(all.anuncios.length, 121, 'o parser real nao pode cortar a pagina em 100');
        assert.ok(all.anuncios.every(item => item.rankable && item.seller_id === '123456'), JSON.stringify(all.anuncios.slice(0, 2)));
        assert.equal(all.anuncios[120].vendas, 121);
        assert.equal(all.perfilUrl, 'https://lista.mercadolivre.com.br/_CustId_123456');
        assert.equal(all.allAdsUrl, page.url(), 'listagem pagina 1 do seller dispensa botao ver todos');

        await page.setContent(fixture(Array.from({ length: 8 }, (_, i) => card(i, avant(i + 1))).join('')));
        await page.addStyleTag({ content: 'li { min-height: 160px; }' });
        await page.evaluate(more => {
            const append = () => {
                if (window.scrollY < 100) return;
                document.querySelector('ol').insertAdjacentHTML('beforeend', more);
                window.removeEventListener('scroll', append);
            };
            window.addEventListener('scroll', append);
        }, Array.from({ length: 113 }, (_, i) => card(i + 8, avant(i + 9))).join(''));
        await page.bringToFront();
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        const lazyAdapter = { getURL: () => page.url(), executeJavaScript: code => page.evaluate(code), navigate: () => { throw new Error('nao navegar na coleta'); } };
        const lazy = await collectFirstPage({ browser: lazyAdapter, seller, settleMs: 100, pollMs: 30, timeoutMs: 25000 });
        assert.equal(lazy.anuncios.length, 121, 'lazy loading real precisa materializar todos os cards da primeira pagina');
        assert.equal(lazy.incomplete, false, JSON.stringify({ reason: lazy.reason, count: lazy.anuncios.length }));
        assert.ok(lazy.anuncios.every(item => Number.isFinite(item.media_vendas_mensal) && item.rank !== null));

        await page.setContent(fixture(card(1, avant(500, 'Outra Loja'))));
        const wrong = await page.evaluate(scripts.snapshotScript({ seller }));
        assert.equal(wrong.anuncios[0].rankable, false, 'nome Avant divergente nao pertence ao seller da pagina');

        const catalogue = `<li class="ui-search-layout__item"><article class="poly-card"><a class="poly-component__title" href="https://www.mercadolivre.com.br/produto/p/MLB9999999?wid=MLB10000001">Catalogo</a>${avant(900, 'Loja Alvo')}</article></li>`;
        await page.setContent(fixture(catalogue));
        const ambiguous = await page.evaluate(scripts.snapshotScript({ seller }));
        assert.equal(ambiguous.anuncios[0].catalogo, true);
        assert.equal(ambiguous.anuncios[0].rankable, false, 'vendas do produto de catalogo nao provam vendas do vendedor');
        assert.equal(ambiguous.anuncios[0].avant_seller_id, '');

        const proved = catalogue.replace(avant(900, 'Loja Alvo'), avant(90, 'Loja Alvo', 'Vendas do anuncio', '<a href="https://lista.mercadolivre.com.br/_CustId_123456">Vendedor</a>'));
        await page.setContent(fixture(proved));
        const specific = await page.evaluate(scripts.snapshotScript({ seller }));
        assert.equal(specific.anuncios[0].rankable, true, JSON.stringify(specific.anuncios[0]));
        assert.equal(specific.anuncios[0].avant_seller_id, '123456');
        assert.equal(specific.anuncios[0].vendas, 90);
        const hashedProduct = proved.replace('/produto/p/MLB9999999?wid=MLB10000001', '/produto/up/MLBU9999999#wid=MLB10000001');
        await page.setContent(fixture(hashedProduct));
        const hashedSnapshot = await page.evaluate(scripts.snapshotScript({ seller }));
        assert.equal(hashedSnapshot.anuncios[0].rankable, true, 'parser Avant preserva a associacao do anuncio #wid');
        assert.equal(hashedSnapshot.anuncios[0].vendas, 90);
        assert.ok(await page.locator('.poly-component__title').getAttribute('href').then(value => value.endsWith('#wid=MLB10000001')), 'normalizacao temporaria restaura links originais');

        await page.setContent(fixture(proved.replace('_CustId_123456', '_CustId_999999')));
        const foreignCatalog = await page.evaluate(scripts.snapshotScript({ seller }));
        assert.equal(foreignCatalog.anuncios[0].rankable, false, 'id do vendedor Avant divergente invalida catalogo');

        await page.goto('https://lista.mercadolivre.com.br/produtos');
        await page.setContent(fixture(card(1, avant(90))));
        const unknownSeller = await page.evaluate(scripts.snapshotScript({ seller }));
        assert.equal(unknownSeller.anuncios[0].rankable, false, 'nome coincidente sozinho nao comprova identidade');
        await page.setContent(fixture('<li class="ui-search-layout__item" data-item-id="MLBU1234567"><article class="poly-card"><a class="poly-component__title" href="https://www.mercadolivre.com.br/produto/up/MLBU1234567#polycard_client=search&wid=MLB10000007&tracking_id=123">Com anuncio</a></article></li><li class="ui-search-layout__item"><article class="poly-card"><a class="poly-component__title" href="https://www.mercadolivre.com.br/outro/up/MLBU1234568#tracking_id=123">Produto sem anuncio</a></article></li>'));
        const userProducts = await page.evaluate(scripts.discoveryScript());
        assert.equal(userProducts.anuncios[0].id, 'MLB10000007', 'wid do fragmento identifica o anuncio real');
        assert.equal(userProducts.anuncios[0].url, 'https://www.mercadolivre.com.br/produto/up/MLBU1234567?wid=MLB10000007');
        assert.equal(userProducts.anuncios[1].id, '', 'MLBU identifica produto, nao um anuncio MLB');
        assert.equal(userProducts.anuncios[1].key, 'https://www.mercadolivre.com.br/outro/up/MLBU1234568');
        await page.setContent(fixture('<li class="ui-search-layout__item"><a class="poly-component__title" href="https://www.mercadolivre.com.br/kit/up/MLBU5102939588#polycard_client%3Dsearch%26wid%3DMLB5181894003%26tracking_id%3Dabc">Fragmento codificado</a></li>'));
        const encodedFragment = await page.evaluate(scripts.discoveryScript());
        assert.equal(encodedFragment.anuncios[0].id, 'MLB5181894003');
        assert.equal(encodedFragment.anuncios[0].url, 'https://www.mercadolivre.com.br/kit/up/MLBU5102939588?wid=MLB5181894003');
        await page.setContent(fixture(card(1, '<s class="poly-price__old"><span class="andes-money-amount"><span class="andes-money-amount__fraction">99</span><span class="andes-money-amount__cents">99</span></span></s><div class="poly-price__current"><span class="andes-money-amount"><span class="andes-money-amount__fraction">55</span><span class="andes-money-amount__cents">44</span></span></div>')));
        assert.equal((await page.evaluate(scripts.discoveryScript())).anuncios[0].preco, 55.44, 'preco corrente com centavos tem precedencia sobre riscado e fraction avulso');
        // Public official-store structures; unrelated recommendation IDs are not seller proof.
        await page.goto('https://www.mercadolivre.com.br/kit/up/MLBU5102939588?wid=MLB5181894003');
        await page.setContent('<main><h1 class="ui-pdp-title">Kit Tivui</h1><div><span>Loja oficial</span><a href="https://www.mercadolivre.com.br/loja/tivui?tracking_id=abc">Tivui</a></div><section class="same-seller"><a href="https://lista.mercadolivre.com.br/_CustId_3383122021">Ver todos os anuncios</a></section><section class="eshops"><a href="https://lista.mercadolivre.com.br/_CustId_2696895394">Ver todos os produtos</a></section></main>');
        const officialProduct = await page.evaluate(scripts.discoveryScript());
        assert.equal(officialProduct.perfilUrl, 'https://www.mercadolivre.com.br/loja/tivui');
        assert.equal(officialProduct.sellerId, '', 'IDs em same-seller/eshops nao identificam o vendedor do anuncio');
        assert.equal(officialProduct.allAdsUrl, '');
        assert.equal(officialProduct.seller_name_kind, 'store_brand');
        await page.goto('https://www.mercadolivre.com.br/loja/tivui');
        await page.setContent('<main><h1>Tivui</h1><a class="ui-ms-profile__item ui-ms-profile__menu-element" href="https://lista.mercadolivre.com.br/loja/tivui/#tracking_id=abc">Todos os produtos</a></main>');
        const officialLanding = await page.evaluate(scripts.discoveryScript());
        assert.equal(officialLanding.allAdsUrl, 'https://lista.mercadolivre.com.br/loja/tivui/');
        await page.setContent('<main><h1>Tivui</h1></main>');
        assert.equal((await page.evaluate(scripts.discoveryScript())).allAdsUrl, '', 'landing www da loja nao significa todos os produtos');
        await page.goto('https://lista.mercadolivre.com.br/loja/tivui/');
        await page.setContent(fixture(card(4, avant(50, 'BAEHFCGBD5542', 'Vendas do produto', '<a href="https://www.mercadolivre.com.br/loja/tivui">Vendedor</a>'))));
        const officialSeller = { perfilUrl: 'https://www.mercadolivre.com.br/loja/tivui', nome: 'Tivui' };
        const officialListing = await page.evaluate(scripts.snapshotScript({ seller: officialSeller }));
        assert.equal(officialListing.allAdsUrl, page.url());
        assert.equal(officialListing.perfilUrl, officialSeller.perfilUrl);
        assert.equal(officialListing.anuncios[0].rankable, true, 'marca oficial difere do nickname; perfil canonico confirma o anuncio');
        await page.setContent(fixture('<li class="ui-search-layout__item"><article class="poly-card"><a class="poly-component__title" href="https://www.mercadolivre.com.br/kit/up/MLBU5102939588#polycard_client%3Dsearch%26wid%3DMLB5181894003">Kit Tivui</a>' + avant(50, 'BAEHFCGBD5542', 'Vendas do produto', '<a href="https://www.mercadolivre.com.br/loja/tivui">Vendedor</a>') + '</article></li>'));
        const officialCatalog = await page.evaluate(scripts.snapshotScript({ seller: officialSeller }));
        assert.equal(officialCatalog.anuncios[0].vendas, 50, 'wid codificado continua associado ao parser Avant');
        assert.equal(officialCatalog.anuncios[0].catalogo, false, 'User Products Page nao prova que o anuncio e de catalogo');
        assert.equal(officialCatalog.anuncios[0].rankable, true, 'UP do vendedor confirmado usa o total estruturado do produto');
        const currentPrice = (integer, cents, old) => `<s class="poly-price__old"><span class="andes-money-amount"><span class="andes-money-amount__fraction">${old}</span></span></s><div class="poly-price__current"><span class="andes-money-amount"><span class="andes-money-amount__fraction">${integer}</span><span class="andes-money-amount__cents">${cents}</span></span></div>`;
        const liveRows = liveAvantRow('Vendas do catálogo', '1.000') + liveAvantRow('Vendas estimadas', '4.372') + liveAvantRow('Ritmo atual (vendas/mês)', '1.566') + liveAvantRow('Anúncio criado em', '24/06/2026', '<span class="avantpro-creation-time-inline">Há 102 dias</span>');
        await page.setContent(fixture('<li class="ui-search-layout__item"><article class="poly-card"><a class="poly-component__title" href="https://www.mercadolivre.com.br/inversor/up/MLBU7032306368?wid=MLB7032306368">Inversor Carro 12v/24v Turbo Tomada De Lightning Tipo C 100w</a>' + currentPrice('67', '63', '119') + '<section class="avantpro-product-info">' + liveRows + '</section></article></li>'));
        const originalLive = await page.evaluate(originalAvant.render('extrair-anuncios-avant-pro-dom-webview-1', { p0: '1000' }));
        const normalizedLive = await page.evaluate(scripts.snapshotScript({ seller: officialSeller }));
        assert.equal(normalizedLive.anuncios[0].preco, 67.63);
        assert.equal(normalizedLive.anuncios[0].vendas, 1000, 'total explicito do catalogo normaliza separador de milhar');
        assert.equal(normalizedLive.anuncios[0].vendas_estimadas, 4372, 'estimativa e preservada separadamente do total observado');
        assert.equal(normalizedLive.anuncios[0].media_mensal, 1566, 'ritmo PT-BR com ponto representa milhar');
        assert.equal(normalizedLive.anuncios[0].data_criacao, '24/06/2026', 'idade inline nao pode substituir a data de criacao');
        assert.equal(normalizedLive.anuncios[0].catalogo, true, 'rotulo explicito de vendas de catalogo preserva protecao tambem na rota UP');
        assert.equal(normalizedLive.anuncios[0].rankable, false, 'total explicitamente agregado continua sem atribuicao ao vendedor');
        assert.equal(normalizedLive.anuncios[0].status, 'catalogo_sem_evidencia_vendedor');
        console.log('Reproducao parser Favoritos anterior, labels com ponto de milhar:', JSON.stringify({
            raw: originalLive.anuncios.map(item => ({ vendas: item.vendas, estimadas: item.vendas_estimadas, ritmo: item.media_mensal, criacao: item.data_criacao })),
            normalizado: normalizedLive.anuncios.map(item => ({ vendas: item.vendas, ritmo: item.media_mensal, rankable: item.rankable }))
        }));
        const liveProductRows = liveAvantRow('Vendas do produto', '50') + liveAvantRow('Ritmo atual (vendas/mês)', '99') + liveAvantRow('Nome do vendedor', 'BAEHFCGBD5542') + liveAvantRow('Anúncio criado em', '03/09/2026');
        await page.setContent(fixture('<li class="ui-search-layout__item"><article class="poly-card"><a class="poly-component__title" href="https://www.mercadolivre.com.br/kit/up/MLBU5102939588?wid=MLB5181894003">Kit Tivui</a>' + currentPrice('55', '44', '99') + '<section class="avantpro-product-info">' + liveProductRows + '</section></article></li>'));
        const publicProduct = await page.evaluate(scripts.snapshotScript({ seller: officialSeller }));
        assert.equal(publicProduct.anuncios[0].vendas, 50);
        assert.equal(publicProduct.anuncios[0].media_mensal, 99);
        assert.equal(publicProduct.anuncios[0].data_criacao, '03/09/2026');
        assert.equal(publicProduct.anuncios[0].vendedor, 'BAEHFCGBD5542');
        assert.equal(publicProduct.anuncios[0].preco, 55.44);
        assert.equal(publicProduct.anuncios[0].rankable, true, 'UP na listagem oficial confirmada pode rankear com total e media');
        await page.setContent(fixture(card(9, '<section class="avantpro-product-info">' + liveProductRows + '</section>')));
        const plainProduct = await page.evaluate(scripts.snapshotScript({ seller: officialSeller }));
        assert.equal(plainProduct.anuncios[0].rankable, true);
        assert.equal(plainProduct.anuncios[0].vendas, 50);
        assert.equal(plainProduct.anuncios[0].data_criacao, '03/09/2026');
        assert.equal(plainProduct.anuncios[0].media_mensal, 99);
        await page.setContent(fixture(card(9, '<section class="avantpro-product-info">' + liveAvantRow('Vendas estimadas', '4.372') + liveAvantRow('Ritmo atual (vendas/mês)', '1.566') + liveAvantRow('Anúncio criado em', '31/02/2026') + '</section>')));
        await page.evaluate(scripts.prepareScript()); // A new independent fixture starts a new collection.
        const estimatesOnly = await page.evaluate(scripts.snapshotScript({ seller: officialSeller }));
        assert.equal(estimatesOnly.anuncios[0].vendas, null, 'vendas estimadas nunca substituem total ausente');
        assert.equal(estimatesOnly.anuncios[0].media_mensal, 1566);
        assert.equal(estimatesOnly.anuncios[0].data_criacao, '', 'data calendario invalida e rejeitada');
        assert.equal(estimatesOnly.anuncios[0].rankable, false, 'media sem quantidade nao encerra a coleta de dados');
        assert.equal(estimatesOnly.anuncios[0].status, 'sem_quantidade_avant');
        await page.goto('https://lista.mercadolivre.com.br/_CustId_123456');

        await page.setContent(fixture(card(1, '<span>+10 mil vendidos</span>')));
        const publicOnly = await page.evaluate(scripts.snapshotScript({ seller }));
        assert.equal(publicOnly.anuncios[0].vendas, null, 'texto do ML nao vira dado Avant');
        assert.equal(publicOnly.anuncios[0].rankable, false);

        // Delayed data is populated by the actual Favoritos card-click script.
        await page.setContent(fixture(card(1, '<button class="avantpro-button" title="Informacoes Avantpro">Informacoes Avantpro</button>')));
        await page.evaluate(html => {
            document.querySelector('button').onclick = () => setTimeout(() => {
                document.querySelector('article').insertAdjacentHTML('beforeend', html);
            }, 120);
        }, avant(42));
        const adapter = { getURL: () => page.url(), executeJavaScript: code => page.evaluate(code), navigate: () => { throw new Error('no navigation'); } };
        const delayed = await collectFirstPage({ browser: adapter, seller, settleMs: 100, missingWaitMs: 1500, timeoutMs: 10000, pollMs: 100 });
        assert.equal(delayed.anuncios[0].vendas, 42, JSON.stringify(delayed));
        assert.equal(delayed.incomplete, false);

        await page.setContent(fixture(card(21, '<button class="avantpro-button" title="Informacoes Avantpro">Informacoes Avantpro</button>') + card(22, '<button class="avantpro-button" title="Informacoes Avantpro">Informacoes Avantpro</button>')));
        await page.evaluate(values => {
            const panel = document.createElement('aside');
            panel.id = 'avantpro-floating-panel';
            document.body.appendChild(panel);
            Array.from(document.querySelectorAll('button')).forEach((button, index) => {
                button.onclick = () => setTimeout(() => {
                    const itemUrl = button.closest('article').querySelector('a').href;
                    panel.innerHTML = '<a href="' + itemUrl + '">Produto selecionado</a><section class="avantpro-product-info">' + values[index] + '</section>';
                }, 50);
            });
        }, [
            liveAvantRow('Vendas do produto', '1.200') + liveAvantRow('Ritmo atual (vendas/mês)', '100') + liveAvantRow('Nome do vendedor', 'Loja Alvo'),
            liveAvantRow('Vendas do produto', '3.400') + liveAvantRow('Ritmo atual (vendas/mês)', '200') + liveAvantRow('Nome do vendedor', 'Loja Alvo')
        ]);
        const floating = await collectFirstPage({ browser: adapter, seller, settleMs: 100, missingWaitMs: 1500, timeoutMs: 15000, pollMs: 100 });
        assert.deepEqual(floating.anuncios.map(item => [item.id, item.vendas]).sort(), [['MLB10000021', 1200], ['MLB10000022', 3400]], 'painel flutuante fica associado somente ao card clicado e confirmado');
        assert.equal(floating.incomplete, false, JSON.stringify(floating));

        // Partial rows must request missing metrics without changing Favoritos' default policy.
        const partialRows = '<section class="avantpro-product-info">' + liveAvantRow('Vendas do produto', '100') + '</section>';
        const partialButton = '<button class="avantpro-button" title="Informacoes Avantpro">Informacoes Avantpro</button>';
        await page.setContent(fixture(card(31, partialRows + partialButton)));
        await page.evaluate(() => { window.pmFixtureClicks = 0; document.querySelector('button').onclick = () => { window.pmFixtureClicks += 1; }; });
        await page.evaluate(scripts.prepareScript());
        const legacyPartial = await page.evaluate(originalAvant.render('acionar-cards-avant-pro-fila-webview-1', { p0: '1', p1: '3', p2: '4500', p3: 'true', p4: 'true' }));
        assert.equal(legacyPartial.clicked, 0, 'Favoritos sem opt-in conserva criterio anterior');
        assert.equal(await page.evaluate(() => window.pmFixtureClicks), 0);
        await page.evaluate(() => {
            document.querySelector('button').onclick = () => {
                window.pmFixtureClicks += 1;
                setTimeout(() => document.querySelector('.avantpro-product-info').insertAdjacentHTML('beforeend',
                    '<div class="avantpro-product-info-row"><span class="avantpro-product-info-row-label">Ritmo atual</span><span class="avantpro-product-info-row-value">4,5</span></div>'), 30);
            };
        });
        const enrichedPartial = await collectFirstPage({ browser: adapter, seller, settleMs: 80, missingWaitMs: 1500, pollMs: 80, timeoutMs: 10000 });
        assert.equal(enrichedPartial.incomplete, false, JSON.stringify(enrichedPartial));
        assert.equal(enrichedPartial.anuncios[0].vendas, 100);
        assert.equal(enrichedPartial.anuncios[0].media_vendas_mensal, 4.5);
        assert.equal(await page.evaluate(() => window.pmFixtureClicks), 1, 'dados parciais deixam de impedir o clique de complemento');
        assert.equal(await page.evaluate(() => '__JK_PM_AVANT_PENDING_KEYS' in window), false, 'opt-in nao persiste depois do clique');

        await page.setContent(fixture(card(32, '<section class="avantpro-product-info">' + liveAvantRow('Ritmo atual', '8') + '</section>' + partialButton)));
        await page.evaluate(html => { document.querySelector('button').onclick = () => setTimeout(() => document.querySelector('.avantpro-product-info').insertAdjacentHTML('beforeend', html), 30); }, liveAvantRow('Vendas do produto', '0'));
        const monthlyPartial = await collectFirstPage({ browser: adapter, seller, settleMs: 80, missingWaitMs: 1500, pollMs: 80, timeoutMs: 10000 });
        assert.equal(monthlyPartial.incomplete, false, JSON.stringify(monthlyPartial));
        assert.equal(monthlyPartial.anuncios[0].vendas, 0, 'zero informado e quantidade valida');
        assert.equal(monthlyPartial.anuncios[0].media_vendas_mensal, 8);

        await page.setContent(fixture(card(33, '<section class="avantpro-product-info">' + liveAvantRow('Ritmo atual', '8') + '</section>')));
        const missingQuantity = await collectFirstPage({ browser: adapter, seller, settleMs: 40, missingWaitMs: 150, pollMs: 40, timeoutMs: 3000 });
        assert.equal(missingQuantity.incomplete, true);
        assert.equal(missingQuantity.anuncios[0].vendas, null, 'ritmo mensal nunca vira quantidade inventada');
        assert.equal(missingQuantity.anuncios[0].rank, null);

        // Failed queue execution must restore the caller's prior scope too.
        await page.evaluate(() => { window.__JK_PM_AVANT_PENDING_KEYS = { caller: true }; });
        const rejectingScripts = scripts.create({ renderAvantScript: () => '(async function(){throw new Error("fixture queue failure");})()' });
        await assert.rejects(page.evaluate(rejectingScripts.clickAvantScript({ pending: [{ id: 'MLB10000033' }] })), /fixture queue failure/);
        assert.deepEqual(await page.evaluate(() => window.__JK_PM_AVANT_PENDING_KEYS), { caller: true });
        await page.evaluate(() => { delete window.__JK_PM_AVANT_PENDING_KEYS; });

        // Catalog product IDs must not override wid or collapse two listing identities.
        const catalogCard = (id, total, monthly) => '<li class="ui-search-layout__item"><article class="poly-card">'
            + '<a class="poly-component__title" href="https://www.mercadolivre.com.br/produto/p/MLB9999999?wid=MLB' + id + '">Catalogo ' + id + '</a>'
            + '<section class="avantpro-product-info">' + liveAvantRow('Vendas do anuncio', total)
            + (monthly === null ? '' : liveAvantRow('Ritmo atual', monthly))
            + liveAvantRow('Nome do vendedor', 'Loja Alvo', '<a href="https://lista.mercadolivre.com.br/_CustId_123456">Vendedor</a>') + '</section>' + partialButton + '</article></li>';
        await page.setContent(fixture(catalogCard('10000041', '41', null) + catalogCard('10000042', '42', '7')));
        await page.evaluate(html => {
            window.pmClickedListings = [];
            document.querySelectorAll('button').forEach(button => { button.onclick = () => {
                const card = button.closest('article');
                window.pmClickedListings.push(card.querySelector('.poly-component__title').href);
                setTimeout(() => card.querySelector('.avantpro-product-info').insertAdjacentHTML('beforeend', html), 30);
            }; });
        }, liveAvantRow('Ritmo atual', '6'));
        const catalogEnriched = await collectFirstPage({ browser: adapter, seller, settleMs: 80, missingWaitMs: 1500, pollMs: 80, timeoutMs: 10000 });
        assert.equal(catalogEnriched.incomplete, false, JSON.stringify(catalogEnriched));
        assert.deepEqual(catalogEnriched.anuncios.map(item => item.id).sort(), ['MLB10000041', 'MLB10000042']);
        assert.deepEqual(await page.evaluate(() => window.pmClickedListings), ['https://www.mercadolivre.com.br/produto/p/MLB9999999?wid=MLB10000041']);

        // An explicit detail uses the selected PDP, never a recommendation card.
        await page.goto('https://produto.mercadolivre.com.br/MLB-10000034-produto-_JM');
        await page.setContent('<main><h1 class="ui-pdp-title">Produto de detalhe</h1><a class="ui-pdp-seller__link" href="https://lista.mercadolivre.com.br/_CustId_123456">Loja Alvo</a>'
            + '<section class="avantpro-product-info">' + liveAvantRow('Vendas do produto', '40') + '</section>'
            + '<button disabled class="avantpro-button" title="Informacoes Avantpro">Informacoes Avantpro</button>' + partialButton
            + '<section class="same-seller">' + card(35, avant(999)) + '</section></main>');
        await page.evaluate(html => { document.querySelector('button:not([disabled])').onclick = () => setTimeout(() => document.querySelector('.avantpro-product-info').insertAdjacentHTML('beforeend', html), 30); }, liveAvantRow('Ritmo atual', '6'));
        const detailCollector = require('../static/pesquisa-mercado/shared-collector');
        const detail = await detailCollector.collectDetail({ browser: adapter, seller, expectedItem: { id: 'MLB10000034' }, settleMs: 80, missingWaitMs: 1500, pollMs: 80, timeoutMs: 10000 });
        assert.equal(detail.incomplete, false, JSON.stringify(detail));
        assert.deepEqual(detail.anuncios.map(item => item.id), ['MLB10000034']);
        assert.equal(detail.anuncios[0].vendas, 40);
        assert.equal(detail.anuncios[0].media_vendas_mensal, 6);
        assert.equal(detail.primeira_pagina, false);
        await page.evaluate(html => {
            const aside = document.createElement('aside'); aside.className = 'avantpro-panel';
            aside.innerHTML = '<a href="https://produto.mercadolivre.com.br/MLB-10000999-outro-_JM">Outro produto</a><section class="avantpro-product-info">' + html + '</section>';
            document.body.appendChild(aside);
        }, liveAvantRow('Vendas do produto', '999') + liveAvantRow('Ritmo atual', '999') + liveAvantRow('Nome do vendedor', 'Outro vendedor', '<a href="https://lista.mercadolivre.com.br/_CustId_999">Vendedor</a>'));
        const boundedDetail = await page.evaluate(scripts.snapshotScript({ seller, detail: true }));
        assert.equal(boundedDetail.anuncios[0].vendas, 40, 'painel explicitamente de outro produto nao substitui dados do PDP selecionado');
        assert.equal(boundedDetail.anuncios[0].media_mensal, 6);
        assert.equal(boundedDetail.anuncios[0].seller_confirmed, true, 'proof do painel de outro produto nao conflita com o vendedor principal');
        await page.evaluate(() => { document.querySelector('aside').remove(); });
        await page.evaluate(scripts.prepareScript());
        await page.evaluate(html => { document.querySelector('.avantpro-product-info').innerHTML = html; }, liveAvantRow('Vendas do produto', '40'));
        const firstDetailSnapshot = await page.evaluate(scripts.snapshotScript({ seller, detail: true }));
        assert.equal(firstDetailSnapshot.anuncios[0].vendas, 40);
        assert.equal(firstDetailSnapshot.anuncios[0].media_mensal, null);
        await page.evaluate(html => { document.querySelector('.avantpro-product-info').innerHTML = html; }, liveAvantRow('Ritmo atual', '6'));
        const secondDetailSnapshot = await page.evaluate(scripts.snapshotScript({ seller, detail: true }));
        assert.equal(secondDetailSnapshot.anuncios[0].vendas, 40, 'total confirmado permanece quando o painel exibe apenas a media depois');
        assert.equal(secondDetailSnapshot.anuncios[0].media_mensal, 6);
        assert.equal(secondDetailSnapshot.anuncios[0].rankable, true);
        await page.evaluate(html => { document.querySelector('.avantpro-product-info').innerHTML = html; }, liveAvantRow('Ritmo atual', '999') + liveAvantRow('Nome do vendedor', 'Outro vendedor', '<a href="https://lista.mercadolivre.com.br/_CustId_999">Vendedor</a>'));
        const conflictingDetailSnapshot = await page.evaluate(scripts.snapshotScript({ seller, detail: true }));
        assert.equal(conflictingDetailSnapshot.anuncios[0].rankable, false, 'cache parcial nao permite metricas de vendedor divergente');
        assert.equal(conflictingDetailSnapshot.anuncios[0].vendas, null, 'mudanca de vendedor invalida quantidade anterior do painel');

        await page.goto('https://lista.mercadolivre.com.br/_CustId_123456');

        // Read the actual modern PDP shape, including old bundled cards, without page-wide text fallback.
        const modernItemId = 'MLB10000077';
        const modernUrl = 'https://www.mercadolivre.com.br/fixture/up/MLBU10000077?wid=' + modernItemId;
        await page.goto(modernUrl);
        const header = '<h1 class="ui-pdp-title">Produto fixture</h1><a class="ui-pdp-seller__link" href="https://lista.mercadolivre.com.br/_CustId_123456">Loja Alvo</a>';
        const readModern = async (body, sellerHeader = header) => {
            await page.setContent('<main>' + sellerHeader + body + '</main>');
            await page.evaluate(scripts.prepareScript());
            return (await page.evaluate(scripts.snapshotScript({ seller, detail: true }))).anuncios[0];
        };
        const modern = await readModern(pdpRoot());
        assert.equal(modern.vendas, 100, 'pill Vendidos informa quantidade; estimativa 155 e estoque 888 nao substituem total');
        assert.equal(modern.vendas_estimadas, 155);
        assert.equal(modern.media_mensal, 5, 'media mensal do Avant e separada do ritmo atual');
        assert.equal(modern.ritmo_atual, 10);
        assert.equal(modern.data_criacao, '12/03/2025');
        assert.equal(modern.rankable, true);
        assert.equal(modern.avant_seller_id, '', 'vendedor nao e fabricado a partir da URL da pagina');
        let modernClock = Date.parse('2026-10-14T12:00:00Z');
        const modernPartials = [];
        const modernDetail = await detailCollector.collectDetail({ browser: adapter, seller, expectedItem: { id: modernItemId }, now: () => modernClock,
            onPartial: partial => { modernPartials.push(partial); },
            wait: async ms => { modernClock += ms; await new Promise(resolve => setTimeout(resolve, ms)); },
            settleMs: 40, missingWaitMs: 300, pollMs: 40, timeoutMs: 3000 });
        assert.equal(modernDetail.incomplete, false, JSON.stringify(modernDetail));
        assert.equal(modernDetail.anuncios[0].vendas, 100);
        assert.equal(modernDetail.anuncios[0].media_mensal, 5);
        assert.equal(modernDetail.anuncios[0].ritmo_atual, 10);
        assert.equal(modernDetail.anuncios[0].media_vendas_mensal, require('../static/pesquisa-mercado/shared-ranking').calculateMetrics(modern, modernClock).media,
            'calculo total/data continua a regra do ranking quando ha data');
        assert.equal(modernDetail.anuncios[0].media_mensal_fonte, 'calculado_vendas_dias_avantpro');
        assert.ok(modernPartials.length >= 2, 'cartoes modernos sao observados antes da estabilidade final');
        assert.ok(modernPartials.every(partial => partial.reason === 'partial' && partial.primeira_pagina === false));
        assert.ok(modernPartials.every(partial => partial.anuncios.length === 1 && partial.anuncios[0].id === modernItemId));
        assert.ok(modernPartials.every(partial => partial.anuncios[0].vendas === 100 && partial.anuncios[0].vendas_estimadas === 155));
        assert.ok(modernPartials.every(partial => partial.anuncios[0].rankable === true && partial.anuncios[0].media_mensal === 5));

        const withoutDate = await readModern(pdpRoot({ created: '' }));
        assert.equal(withoutDate.media_mensal, 5);
        const directDetail = await detailCollector.collectDetail({ browser: adapter, seller, expectedItem: { id: modernItemId },
            settleMs: 40, missingWaitMs: 300, pollMs: 40, timeoutMs: 3000 });
        assert.equal(directDetail.anuncios[0].media_vendas_mensal, 5, 'sem data usa media mensal 5, sem trocar pelo ritmo 10');
        const bundledModern = await readModern(pdpRoot({ rhythm: null }));
        assert.equal(bundledModern.vendas, 100);
        assert.equal(bundledModern.media_mensal, 5, 'cards do bundled 7.0.3 mantem total e media');
        const modernZeros = await readModern(pdpRoot({ total: 0, rhythm: null, monthly: '0/Mês', created: '' }));
        assert.equal(modernZeros.vendas, 0);
        assert.equal(modernZeros.media_mensal, 0);
        assert.equal(modernZeros.rankable, true);
        const estimateOnlyModern = await readModern(pdpRoot({ total: null }));
        assert.equal(estimateOnlyModern.vendas, null);
        assert.equal(estimateOnlyModern.vendas_estimadas, 155);
        assert.equal(estimateOnlyModern.rankable, false, 'estimativa nao vira quantidade de vendas');
        const plainTailwind = await readModern(pdpRoot().replace('class="avantpro-in-product-content"', 'class="other-product-content"'));
        assert.equal(plainTailwind.vendas, null, 'classes tailwind sem raiz Avant comprovada sao ignoradas');
        assert.equal(plainTailwind.rankable, false);

        const foreignId = 'MLB99999999';
        for (const attrs of ['data-item-id="' + foreignId + '"', 'data-product-id="' + foreignId + '"', 'data-catalog-product-id="' + foreignId + '"',
            'data-item-id="' + modernItemId + '" data-mlb="' + foreignId + '"']) {
            const foreign = await readModern(pdpRoot({ total: 999, attrs }));
            assert.equal(foreign.vendas, null, 'identidade explicita divergente nao fornece quantidade para o PDP: ' + attrs);
            assert.equal(foreign.rankable, false);
        }
        const profileHeader = '<h1 class="ui-pdp-title">Produto fixture</h1><a class="ui-pdp-seller__link" href="' + seller.perfilUrl + '">Loja Alvo</a>';
        const foreignSellerRoot = await readModern(pdpRoot({ total: 999, attrs: 'data-seller-id="999999"' }), profileHeader);
        assert.equal(foreignSellerRoot.vendas, null, 'seller alvo conhecido rejeita id explicito divergente mesmo com cabecalho somente de perfil');
        assert.equal(foreignSellerRoot.rankable, false);
        const conflictingProfileRoot = await readModern(pdpRoot({ total: 999,
            attrs: 'data-seller-profile-url="https://www.mercadolivre.com.br/perfil/OUTRO"',
            extra: liveAvantRow('Nome do vendedor', 'Loja Alvo', '<a href="' + seller.perfilUrl + '">Vendedor</a>') }), profileHeader);
        assert.equal(conflictingProfileRoot.vendas, null, 'linha legada nao sobrescreve perfil explicito estrangeiro na raiz');
        assert.equal(conflictingProfileRoot.rankable, false);
        const declaredSellerRoot = await readModern(pdpRoot({ attrs: 'data-seller-id="123456"' }), profileHeader);
        assert.equal(declaredSellerRoot.vendas, 100);
        assert.equal(declaredSellerRoot.avant_seller_id, '123456', 'id explicito verdadeiro e propagado, sem fabricar prova de vendedor');
        assert.equal(declaredSellerRoot.rankable, true);
        await page.setContent('<main>' + header + pdpRoot({ total: 999,
            attrs: 'data-seller-id="123456" data-seller-profile-url="https://www.mercadolivre.com.br/perfil/ROOT_NAME?seller_id=123456"',
            extra: liveAvantRow('Nome do vendedor', 'Loja Alvo', '<a href="https://www.mercadolivre.com.br/perfil/ROW_NAME?seller_id=123456">Vendedor</a>') }) + '</main>');
        await page.evaluate(scripts.prepareScript());
        const profilesWithSameId = (await page.evaluate(scripts.snapshotScript({ seller: { id: '123456', nome: 'Loja Alvo', perfilUrl: 'https://lista.mercadolivre.com.br/_CustId_123456' }, detail: true }))).anuncios[0];
        assert.equal(profilesWithSameId.vendas, null, 'mesmo seller_id nas queries nao resolve perfis nomeados incompatíveis');
        assert.equal(profilesWithSameId.rankable, false);
        const ignoredRecommendation = await readModern(pdpRoot({ extra: '<section class="recommendations">' + pdpRoot({ total: 999 })
            + '<a href="https://lista.mercadolivre.com.br/_CustId_999">Outro vendedor</a></section>' }));
        assert.equal(ignoredRecommendation.vendas, 100);
        assert.equal(ignoredRecommendation.seller_confirmed, true, 'links de recomendacao nao se tornam prova de vendedor');
        const foreignPanelModern = await readModern(pdpRoot() + '<aside class="avantpro-panel"><a href="https://produto.mercadolivre.com.br/MLB-99999999-outro-_JM">Outro item</a>' + pdpRoot({ total: 999 }) + '</aside>');
        assert.equal(foreignPanelModern.vendas, 100);
        assert.equal(foreignPanelModern.rankable, true);
        const foreignLinkedRoot = await readModern(pdpRoot({ total: 999, extra: '<a href="https://www.mercadolivre.com.br/outro/p/MLB99999999">Outro catalogo</a>' }));
        assert.equal(foreignLinkedRoot.vendas, null, 'link de outro catalogo sem wid tambem invalida a raiz');
        assert.equal(foreignLinkedRoot.rankable, false);

        // Ambiguity invalidates both current extraction and previously complete cache.
        await readModern(pdpRoot());
        await page.evaluate(html => document.querySelector('main').insertAdjacentHTML('beforeend', html), pdpRoot({ total: 999, rhythm: 999, monthly: '999/Mês' })
            + '<section>' + liveAvantRow('Nome do vendedor', 'Loja Alvo', '<a href="https://lista.mercadolivre.com.br/_CustId_123456">Vendedor</a>') + '</section>');
        const duplicatedRoot = (await page.evaluate(scripts.snapshotScript({ seller, detail: true }))).anuncios[0];
        assert.equal(duplicatedRoot.vendas, null, 'duas raizes validas nao escolhem primeira ou ultima quantidade');
        assert.equal(duplicatedRoot.rankable, false, 'linha legada de vendedor nao apaga a ambiguidade das raizes');
        await page.evaluate(() => document.querySelectorAll('#productInfo').forEach(node => node.remove()));
        const clearedRoots = (await page.evaluate(scripts.snapshotScript({ seller, detail: true }))).anuncios[0];
        assert.equal(clearedRoots.vendas, null, 'cache completo foi invalidado pelo conflito anterior');
        assert.equal(clearedRoots.rankable, false);
        await page.goto('https://www.mercadolivre.com.br/fixture/p/MLB9999999?wid=' + modernItemId);
        const matchingCatalogRoot = await readModern(pdpRoot({ attrs: 'data-product-id="MLB9999999" data-catalog-product-id="MLB9999999"' }));
        assert.equal(matchingCatalogRoot.vendas, 100, 'id de produto do caminho p e distinto do wid e aceito quando corresponde');
        assert.equal(matchingCatalogRoot.rankable, false, 'pill de catalogo sem prova especifica continua pendente');
        const catalogPartials = [];
        const catalogDetail = await detailCollector.collectDetail({ browser: adapter, seller, expectedItem: { id: modernItemId },
            onPartial: partial => { catalogPartials.push(partial); }, settleMs: 40, missingWaitMs: 100, pollMs: 40, timeoutMs: 3000 });
        assert.equal(catalogDetail.reason, 'avant_data_incomplete');
        assert.ok(catalogPartials.length >= 2);
        assert.ok(catalogPartials.every(partial => partial.anuncios[0].vendas === 100 && partial.anuncios[0].media_mensal === 5));
        assert.ok(catalogPartials.every(partial => partial.anuncios[0].status === 'catalogo_sem_evidencia_vendedor'
            && partial.anuncios[0].rankable === false && partial.anuncios[0].rank === null), 'observacao preserva valores sem atribuir vendas de catalogo ao vendedor');
        await page.goto(modernUrl);
        const explicitCatalogPill = await readModern(pdpRoot({ extra: pdpPill('Catálogo') }));
        assert.equal(explicitCatalogPill.catalogo, true);
        assert.equal(explicitCatalogPill.rankable, false, 'badge explicito de catalogo nao e dispensado pela rota UP');
        await page.goto('https://lista.mercadolivre.com.br/_CustId_123456');

        // Discovery is constrained to category and product elements, not arbitrary links.
        await page.goto('https://www.mercadolivre.com.br/categorias');
        // Public index shapes and links observed on 2026-10-05, without page header/session data.
        const realEstate = [
            ['apartamentos/', 'Apartamentos'], ['casas/', 'Casas'], ['chacaras/', 'Chácaras'], ['fazendas/', 'Fazendas'],
            ['flat-apart-hotel/', 'Flat - Apart Hotel'], ['galpoes/', 'Galpões'], ['lojas-comerciais/', 'Lojas Comerciais'],
            ['outros/', 'Outros Imóveis'], ['salas-comerciais/', 'Salas Comerciais'], ['sitios/', 'Sítios'], ['terrenos/', 'Terrenos']
        ];
        const services = [
            ['academia-esportes/', 'Academia e Esportes'], ['animais/', 'Animais'], ['beleza-estetica/', 'Beleza, Estética e Bem Estar'],
            ['educacao/', 'Educação'], ['festas-eventos/', 'Festas e Eventos'], ['gastronomia/', 'Gastronomia'],
            ['graficas-impresso/', 'Gráficas e Impressão'], ['lar/', 'Lar'], ['marketing-internet/', 'Marketing e Internet'],
            ['outros-profissionais/', 'Outros Profissionais'], ['outros/', 'Outros Serviços'], ['saude/', 'Saúde'],
            ['suporte-tecnico/', 'Suporte Técnico'], ['vestuario/', 'Vestuário'], ['veiculos-transportes/', 'Veículos e Transportes'],
            ['viagens-turismo/', 'Viagens e Turismo']
        ];
        const indexGroup = (name, href, host, children) => '<div class="categories__container"><h2 class="categories__title"><a class="categories__title-link" href="' + href + '">' + name + '</a></h2><ul class="categories__list">'
            + children.map(([path, label]) => '<li><a href="https://' + host + '/' + path + '">' + label + '</a></li>').join('') + '</ul></div>';
        const vehicles = [
            ['veiculos/caminhoes/', 'Caminhões'], ['veiculos/carros-antigos/', 'Carros Antigos'],
            ['veiculos/carros-caminhonetes/', 'Carros e Caminhonetes'], ['veiculos/consorcios/', 'Consórcios'],
            ['veiculos/motorhomes/', 'Motorhomes'], ['veiculos/motos/', 'Motos'], ['veiculos/nautica/', 'Náutica'],
            ['veiculos/outros/', 'Outros'], ['veiculos/veiculos-pesados/', 'Veículos Pesados'], ['veiculos/onibus/', 'Ônibus']
        ];
        await page.setContent('<main>' + indexGroup('Carros, Motos e Outros',
            'https://www.mercadolivre.com.br/c/carros-motos-e-outros', 'lista.mercadolivre.com.br', vehicles) + '</main>');
        const vehicleTree = await page.evaluate(scripts.discoveryScript());
        assert.equal(vehicleTree.categories.length, 1);
        assert.equal(vehicleTree.categoryTree[0].children.length, 10, 'índice mantém os dez filhos públicos de veículos');
        const vehicleUrls = vehicles.map(([relative]) => 'https://lista.mercadolivre.com.br/' + relative);
        assert.deepEqual(vehicleTree.categoryTree[0].children.map(item => item.url), vehicleUrls);
        assert.deepEqual(vehicleTree.categoryTree[0].children.map(normalizeCategory).map(item => item.url), vehicleUrls,
            'extrator → normalizador não pode retirar a barra que mantém o caminho /veiculos no Mercado Livre');
        assert.deepEqual(vehicleTree.categoryTree[0].children.map(item => item.nome), vehicles.map(item => item[1]));
        await page.setContent('<main>'
            + indexGroup('Imóveis', 'http://www.mercadolivre.com.br/imoveis/', 'imoveis.mercadolivre.com.br', realEstate)
            + indexGroup('Serviços', 'https://www.mercadolivre.com.br/c/servicos', 'servicos.mercadolivre.com.br', services)
            + '<div class="category-list"><a href="http://www.mercadolivre.com.br/c/servicos">Outro HTTP</a><a href="http://www.mercadolivre.com.br/imoveis/?tracking_id=x">HTTP diferente</a><a href="https://imoveis.mercadolivre.com.br/">Host sem categoria</a><a href="https://outro.mercadolivre.com.br/apartamentos/">Host nao reconhecido</a></div>'
            + '<li class="ui-search-layout__item"><a href="http://produto.mercadolivre.com.br/MLB-10000001-produto-_JM">Produto HTTP</a></li></main>');
        const specialHosts = await page.evaluate(scripts.discoveryScript());
        assert.deepEqual(specialHosts.categories.map(item => [item.nome, item.url]), [
            ['Imóveis', 'https://www.mercadolivre.com.br/c/imoveis'], ['Serviços', 'https://www.mercadolivre.com.br/c/servicos']
        ], 'apenas o href HTTP exato observado de Imoveis recebe alias HTTPS');
        assert.deepEqual(specialHosts.categoryTree.map(item => item.children.length), [11, 16], 'nenhum dos 27 filhos publicos de Imoveis e Servicos e omitido');
        assert.equal(new Set(specialHosts.categoryTree.flatMap(item => item.children.map(child => child.url))).size, 27);
        assert.deepEqual(specialHosts.categoryTree[0].children.map(item => item.nome), realEstate.map(item => item[1]));
        assert.deepEqual(specialHosts.categoryTree[1].children.map(item => item.nome), services.map(item => item[1]));
        assert.equal(specialHosts.anuncios.length, 0, 'excecao do indice nao permite HTTP na coleta de anuncios');
        assert.equal(specialHosts.readyState, 'complete');
        await page.evaluate(() => Object.defineProperty(document, 'readyState', { value: 'loading', configurable: true }));
        assert.equal((await page.evaluate(scripts.discoveryScript())).readyState, 'loading', 'discovery nao declara pronta uma pagina ainda em carregamento');
        await page.evaluate(() => { delete document.readyState; });
        await page.goto('https://imoveis.mercadolivre.com.br/apartamentos/');
        await page.setContent('<main><dl class="ui-search-filter-dl"><h3 class="ui-search-filter-dt-title">Categorias</h3><ul>'
            + '<li class="ui-search-filter-container"><a href="http://www.mercadolivre.com.br/imoveis/">Imóveis</a></li>'
            + '<li class="ui-search-filter-container"><a href="https://imoveis.mercadolivre.com.br/apartamentos/aluguel/">Aluguel</a></li></ul></dl></main>');
        assert.deepEqual((await page.evaluate(scripts.discoveryScript())).categories.map(item => item.url),
            ['https://imoveis.mercadolivre.com.br/apartamentos/aluguel/'], 'alias HTTP do indice nao reintroduz a raiz ancestral num filtro de apartamento');
        await page.goto('https://www.mercadolivre.com.br/categorias');
        await page.setContent('<main><div class="categories__container"><h2 class="categories__title" itemprop="name"><a class="categories__title-link" href="https://www.mercadolivre.com.br/c/acessorios-para-veiculos" itemprop="url">Acessorios</a></h2><ul class="categories__list" aria-label="Categoria"><li><a href="https://lista.mercadolivre.com.br/acessorios-veiculos/pecas">Pecas</a></li></ul></div><div class="categories__container"><h2 class="categories__title"><a class="categories__title-link" href="https://www.mercadolivre.com.br/c/agro">Agro</a></h2><ul class="categories__list"><li><a href="https://lista.mercadolivre.com.br/agro/ferramentas">Ferramentas</a></li></ul></div><nav><a href="https://lista.mercadolivre.com.br/promocoes">Promocoes</a></nav><a href="https://example.com/fake">Externo</a><a href="https://www.mercadolivre.com.br/perfil/LOJA_ALVO">Loja</a></main>');
        const discovery = await page.evaluate(scripts.discoveryScript());
        assert.deepEqual(discovery.categories.map(item => item.nome), ['Acessorios', 'Agro'], 'estrutura real do indice mostra somente raizes, sem descendentes ou navegacao avulsa');
        assert.equal(discovery.categories[0].url, 'https://www.mercadolivre.com.br/c/acessorios-para-veiculos');
        assert.equal(discovery.allAdsUrl, '');
        assert.deepEqual(discovery.categoryTree.map(item => [item.nome, item.children.map(child => child.nome)]), [['Acessorios', ['Pecas']], ['Agro', ['Ferramentas']]], 'indice canonico mantem filhos separados e associados a sua propria raiz');
        assert.equal(discovery.categoryTree[0].children[0].url, 'https://lista.mercadolivre.com.br/acessorios-veiculos/pecas');
        // The next three hrefs were followed from the public ML directory and
        // its Category filters on 2026-10-08, rather than generated from names.
        const nauticalUrl = 'https://lista.mercadolivre.com.br/acessorios-veiculos/acessorios-nauticos/';
        const navigationUrl = 'https://lista.mercadolivre.com.br/acessorios-veiculos/acessorios-nauticos/sistemas-navegacao/';
        const nauticalAnemometersUrl = 'https://lista.mercadolivre.com.br/acessorios-veiculos/acessorios-nauticos/sistemas-navegacao/anemometros/';
        // Synthetic homonym links are distractions outside the current category group.
        const agricultureAnemometersUrl = 'https://lista.mercadolivre.com.br/agro/ferramentas-trabalho/instrumentos-medicao/anemometros/';
        const toolsAnemometersUrl = 'https://lista.mercadolivre.com.br/ferramentas/medicoes-instrumentacao/medidores-ambientais/anemometros/';
        await page.setContent('<main>'
            + indexGroup('Acessórios para Veículos', 'https://www.mercadolivre.com.br/c/acessorios-para-veiculos',
                'lista.mercadolivre.com.br', [['acessorios-veiculos/acessorios-nauticos/', 'Acessórios Náuticos'], ['acessorios-veiculos/pecas-nauticas/', 'Peças Náuticas']])
            + indexGroup('Agro', 'https://www.mercadolivre.com.br/c/agro', 'lista.mercadolivre.com.br', [['agro/ferramentas-trabalho/', 'Ferramentas de Trabalho']])
            + '<section class="recommendations"><div class="categories__container"><h2 class="categories__title"><a class="categories__title-link" href="' + toolsAnemometersUrl + '">Anemômetros</a></h2></div></section>'
            + '</main>');
        const nauticalIndex = await page.evaluate(scripts.discoveryScript());
        assert.deepEqual(nauticalIndex.categoryTree.map(root => [root.nome, root.children.map(child => [child.nome, child.url])]), [
            ['Acessórios para Veículos', [['Acessórios Náuticos', nauticalUrl], ['Peças Náuticas', 'https://lista.mercadolivre.com.br/acessorios-veiculos/pecas-nauticas/']]],
            ['Agro', [['Ferramentas de Trabalho', 'https://lista.mercadolivre.com.br/agro/ferramentas-trabalho/']]],
        ], 'índice associa os filhos observados à sua própria raiz e ignora recomendações');
        assert.equal(normalizeCategory(nauticalIndex.categoryTree[0].children[0]).url, nauticalUrl, 'link náutico preserva barra final na passagem para o runner');
        const nauticalFilters = (title, breadcrumbs, children) => '<main><h1>' + title + '</h1>'
            + '<nav class="andes-breadcrumb">' + breadcrumbs.map(([name, href]) => '<a href="' + href + '">' + name + '</a>').join('') + '</nav>'
            + '<dl class="ui-search-filter-dl"><h3 class="ui-search-filter-dt-title">Categorias</h3><ul>'
            + '<li class="ui-search-filter-container ui-search-filter-hierarchy--parent"><a href="' + breadcrumbs[breadcrumbs.length - 1][1] + '">' + breadcrumbs[breadcrumbs.length - 1][0] + '</a></li>'
            + '<li class="ui-search-filter-container ui-search-filter-hierarchy--selected"><a href="' + page.url() + '">' + title + '</a></li>'
            + children.map(([name, href]) => '<li class="ui-search-filter-container"><a class="ui-search-link" href="' + href + '"><span class="ui-search-filter-name">' + name + '</span></a></li>').join('')
            + '<li class="ui-search-filter-container" data-filter-id="brand"><h3>Marca</h3><a href="' + toolsAnemometersUrl + '">Anemômetros</a></li></ul></dl>'
            + '<dl class="ui-search-filter-dl"><h3>Preço</h3><a href="' + navigationUrl + '_PriceRange_100-200">Anemômetros</a></dl>'
            + '<nav><a href="' + agricultureAnemometersUrl + '">Anemômetros</a></nav>'
            + '<section class="carousel"><dl class="ui-search-filter-dl"><h3>Categorias</h3><a href="' + toolsAnemometersUrl + '">Anemômetros</a></dl></section>'
            + '</main>';
        await page.goto(nauticalUrl);
        await page.setContent(nauticalFilters('Acessórios Náuticos', [['Acessórios para Veículos', 'https://www.mercadolivre.com.br/c/acessorios-para-veiculos']],
            [['Sistemas de Navegação', navigationUrl], ['Segurança e Salvamento', nauticalUrl + 'seguranca-salvamento/']]));
        const nauticalChildren = await page.evaluate(scripts.discoveryScript());
        assert.deepEqual(nauticalChildren.categories.map(child => [child.nome, child.url]),
            [['Sistemas de Navegação', navigationUrl], ['Segurança e Salvamento', nauticalUrl + 'seguranca-salvamento/']], 'grupo Categorias náutico fornece filhos sem aproveitar filtros, pais ou homônimos externos');
        await page.goto(navigationUrl);
        await page.setContent(nauticalFilters('Sistemas de Navegação', [['Acessórios Náuticos', nauticalUrl]],
            [['Anemômetros', nauticalAnemometersUrl], ['Barômetros', navigationUrl + 'barometros/']]));
        const navigationChildren = await page.evaluate(scripts.discoveryScript());
        assert.deepEqual(navigationChildren.categories.map(child => [child.nome, child.url]),
            [['Anemômetros', nauticalAnemometersUrl], ['Barômetros', navigationUrl + 'barometros/']], 'Anemômetros usa somente href observado no pai correto');
        await page.goto(nauticalAnemometersUrl);
        await page.setContent(nauticalFilters('Sistemas de Navegação Anemômetros', [['Acessórios Náuticos', nauticalUrl], ['Sistemas de Navegação', navigationUrl]], []).replace('</main>', '<ol class="ui-search-layout">' + card(36) + '</ol></main>'));
        const nauticalLeaf = await page.evaluate(scripts.discoveryScript());
        assert.equal(nauticalLeaf.hasChildren, false, 'folha náutica não reabre pais nem categorias homônimas de Agro ou Ferramentas');
        assert.equal(nauticalLeaf.anuncios.length, 1);
        assert.equal(nauticalLeaf.categoryNotFound, false);
        await page.goto('https://www.mercadolivre.com.br/c/acessorios-para-veiculos');
        await page.setContent('<main><section class="categories"><a class="subcategory__link" href="https://lista.mercadolivre.com.br/acessorios-veiculos/rodas_ITEM*CONDITION_2230284">Rodas e Calotas</a></section><section class="carousel"><a class="subcategory__link" href="https://lista.mercadolivre.com.br/acessorios-veiculos/motos">Motos</a></section></main>');
        const marketing = await page.evaluate(scripts.discoveryScript());
        assert.deepEqual(marketing.categories, [], 'landing /c com links promocionais nao define arvore canonica');
        assert.deepEqual(marketing.categoryTree, [], 'arvore canonica pertence somente ao indice');
        await page.goto('https://www.mercadolivre.com.br/categorias');
        await page.setContent('<main><ul class="category-list"><li><a href="https://lista.mercadolivre.com.br/acessorios-veiculos">Acessorios</a><ul><li><a href="https://lista.mercadolivre.com.br/acessorios-veiculos/pecas">Pecas</a></li></ul></li><li><a href="https://lista.mercadolivre.com.br/agro">Agro</a></li></ul><a href="https://lista.mercadolivre.com.br/ofertas">Link avulso</a></main>');
        assert.deepEqual((await page.evaluate(scripts.discoveryScript())).categories.map(item => item.nome), ['Acessorios', 'Agro'], 'indice antigo em lista mantem apenas primeiro nivel');
        await page.goto('https://lista.mercadolivre.com.br/pecas');
        await page.setContent('<nav><a href="https://www.mercadolivre.com.br/categorias">Todas as categorias</a></nav><main><div class="ui-search-filter-container"><h3>Categorias</h3><a href="https://lista.mercadolivre.com.br/pecas/motor">Motor</a></div><div class="ui-search-filter-container"><h3>Localizacao</h3><a href="https://lista.mercadolivre.com.br/pecas_sao-paulo">SP</a></div><div class="ui-search-filter-container"><h3>Preco</h3><a href="https://lista.mercadolivre.com.br/pecas_PriceRange_50-100">R$ 50</a></div><nav class="andes-breadcrumb"><a href="https://lista.mercadolivre.com.br">Pai</a></nav></main>');
        const categoryOnly = await page.evaluate(scripts.discoveryScript());
        assert.deepEqual(categoryOnly.categories.map(item => item.nome), ['Motor']);
        assert.equal(categoryOnly.categoriesIndexUrl, 'https://www.mercadolivre.com.br/categorias');
        await page.goto('https://lista.mercadolivre.com.br/pecas/motor');
        const selectedLeaf = '<main><nav class="andes-breadcrumb"><a href="https://www.mercadolivre.com.br/c/acessorios-para-veiculos">Acessorios</a><a href="https://lista.mercadolivre.com.br/pecas">Pecas</a></nav><div class="ui-search-filter-container"><dl class="ui-search-filter-dl"><h3 id="filter-heading-category" class="ui-search-filter-dt-title">Categorias</h3><a href="https://lista.mercadolivre.com.br/pecas">Pecas</a><a href="https://www.mercadolivre.com.br/c/acessorios-para-veiculos">Acessorios</a><a href="https://www.mercadolivre.com.br/categorias">Todas as categorias</a><a href="https://lista.mercadolivre.com.br/voltar-outro-caminho">Voltar</a><a href="https://lista.mercadolivre.com.br/motor-outro-caminho">Voltar para Motor</a><a aria-current="page" href="https://lista.mercadolivre.com.br/motor-alias">Motor</a><a href="https://lista.mercadolivre.com.br/pecas/motor_PriceRange_10-50">Oferta</a><div class="ui-search-filter-container"><h3>Localizacao</h3><a href="https://lista.mercadolivre.com.br/pecas/motor_sao-paulo">Sao Paulo</a></div>CHILDREN</dl><dl class="ui-search-filter-dl"><h3>Preco</h3><a href="https://lista.mercadolivre.com.br/pecas/motor_PriceRange_50-100">R$ 50</a></dl></div><nav><a href="https://lista.mercadolivre.com.br/pecas/freios">Outra categoria</a></nav><section class="categories carousel"><a href="https://lista.mercadolivre.com.br/tendencia">Tendencia</a></section></main>';
        await page.setContent(selectedLeaf.replace('CHILDREN', ''));
        const leaf = await page.evaluate(scripts.discoveryScript());
        assert.equal(leaf.hasChildren, false, 'folha nao reabre pais, breadcrumb, retornos, filtros, recos ou navegacao externa ao grupo');
        assert.deepEqual(leaf.categories, []);
        await page.setContent(selectedLeaf.replace('CHILDREN', '<a class="ui-search-filter-hierarchy-link" href="https://lista.mercadolivre.com.br/pecas/motor/bombas">Bombas</a><a href="https://lista.mercadolivre.com.br/pecas/motor/correias">Correias</a>'));
        assert.deepEqual((await page.evaluate(scripts.discoveryScript())).categories.map(item => item.nome), ['Bombas', 'Correias'], 'filhos do grupo Categoria continuam disponiveis apesar dos pais e filtros vizinhos');
        // Two deeper public levels captured on 2026-10-05; this is a sampled
        // regression fixture, not an exhaustive traversal of Mercado Livre.
        const partsUrl = 'https://lista.mercadolivre.com.br/acessorios-veiculos/pecas-carros-caminhonetes/';
        const partsSlugs = ['baterias', 'carroceria', 'climatizacao', 'conducao-assistida-avancada', 'eletroventiladores',
            'eletricos-hibridos-phev', 'escapamentos', 'fechaduras-chaves/fechaduras-chaves', 'filtros', 'freios', 'ignicao',
            'iluminacao', 'injecao', 'janelas-vedacoes', 'motor', 'outros', 'pecas-exterior/pecas-exterior', 'pecas-interior',
            'seguranca', 'sistema-eletrico/sistema-eletrico', 'suspensao-direcao/suspensao-direcao', 'transmissao/transmissao'];
        const motorSlugs = ['bielas', 'bombas', 'cabos-comando', 'carburadores-pecas', 'coletor-escapamento', 'coletores-admissao',
            'componentes-motor', 'correias', 'coxins-motor', 'distribuicao', 'filtros-oleo', 'intercoolers', 'juntas-selos-ferragens',
            'kits-motor', 'kits-reconstrucao', 'kits-servico', 'kits-suporte-motor', 'mangueira-intercooler', 'mangueira-linha-tubulacoes',
            'mangueiras-admissao', 'montagens-tirante-motor', 'motor-completo', 'motores-bloco-curto', 'motores-bloco-longo',
            'motores-tracao', 'outros', 'polia-virabrequim', 'protetores-laterais-motor', 'radiadores', 'refrigeracao', 'retentores',
            'rolamentos-motor', 'sensor-oleo', 'sensores', 'tampas-enchimento', 'turbos-supercargadores', 'vareta-nivel-oleo',
            'virabrequim', 'valvulas-egr', 'valvulas-solenoides'];
        const deepCategoryFilter = (parent, slugs) => '<main><nav class="andes-breadcrumb"><a href="' + partsUrl + '">Peças</a></nav>'
            + '<dl class="ui-search-filter-dl"><h3 class="ui-search-filter-dt-title">Categorias</h3><ul>'
            + slugs.map(slug => '<li class="ui-search-filter-container"><a class="ui-search-link" href="' + parent + slug
                + '/"><span class="ui-search-filter-name">' + slug.split('/').pop().replace(/-/g, ' ') + '</span></a></li>').join('')
            + '</ul></dl><dl class="ui-search-filter-dl"><h3 class="ui-search-filter-dt-title">Preço</h3><ul><li class="ui-search-filter-container">'
            + '<a class="ui-search-link" href="' + parent + '_PriceRange_100-200"><span class="ui-search-filter-name">100 a 200</span></a></li></ul></dl></main>';
        await page.goto(partsUrl);
        await page.setContent(deepCategoryFilter(partsUrl, partsSlugs));
        const deepParts = await page.evaluate(scripts.discoveryScript());
        assert.equal(deepParts.categories.length, 22, 'estrutura real de Pecas mantem 22 filhos dentro de li.ui-search-filter-container');
        assert.deepEqual(deepParts.categories.map(item => item.url), partsSlugs.map(slug => partsUrl + slug + '/'));
        const motorUrl = deepParts.categories.find(item => item.url === partsUrl + 'motor/').url;
        await page.goto(motorUrl);
        await page.setContent(deepCategoryFilter(motorUrl, motorSlugs));
        const deepMotor = await page.evaluate(scripts.discoveryScript());
        assert.equal(deepMotor.categories.length, 40, 'estrutura real de Motor mantem os 40 filhos aninhados');
        assert.deepEqual(deepMotor.categories.map(item => item.url), motorSlugs.map(slug => motorUrl + slug + '/'));
        assert.deepEqual(deepParts.categories.map(normalizeCategory).map(item => item.url), partsSlugs.map(slug => partsUrl + slug + '/'),
            'os 22 links profundos de Peças mantêm suas URLs depois da normalização');
        assert.deepEqual(deepMotor.categories.map(normalizeCategory).map(item => item.url), motorSlugs.map(slug => motorUrl + slug + '/'),
            'os 40 links profundos de Motor mantêm suas URLs depois da normalização');
        const queryUrl = motorUrl + 'bombas/?tracking_id=fixture&original_category_landing=true#menu';
        await page.setContent('<main><dl class="ui-search-filter-dl"><h3>Categorias</h3><a href="' + queryUrl + '">Bombas</a></dl></main>');
        const queryCategory = (await page.evaluate(scripts.discoveryScript())).categories[0];
        assert.equal(queryCategory.url, motorUrl + 'bombas/?original_category_landing=true',
            'discovery remove tracking e fragmento sem perder slash ou query de navegação');
        assert.equal(normalizeCategory(queryCategory).url, motorUrl + 'bombas/?original_category_landing=true',
            'normalização mantém a query funcional e a barra do filho profundo');
        assert.ok(deepParts.hasChildren && deepMotor.hasChildren);
        assert.ok(deepMotor.categories.every(item => item.url !== partsUrl), 'breadcrumb para Pecas nao retorna como filho de Motor');
        await page.setContent('<main><div class="ui-search-filter-container"><h3>Preco</h3><a href="https://lista.mercadolivre.com.br/pecas_PriceRange_50-100">R$ 50</a></div></main>');
        assert.equal((await page.evaluate(scripts.discoveryScript())).hasChildren, false, 'filtros nao criam arvore infinita');
        const landingUrl = 'https://lista.mercadolivre.com.br/acessorios-veiculos/ferramentas-veiculos/ferramentas-baterias/';
        const listingUrl = landingUrl + '_NoIndex_True?original_category_landing=true';
        await page.goto(landingUrl);
        await page.setContent('<main><dl class="ui-search-filter-dl"><h3 class="ui-search-filter-dt-title" id="filter-heading-category">Categorias</h3><a href="' + landingUrl + 'carregadores/">Carregadores</a><a href="' + listingUrl + '">Ver tudo</a></dl><div class="categories carousel"><a href="https://lista.mercadolivre.com.br/tendencia">Tendencia</a></div><div class="recommendations"><a href="' + landingUrl + '?reco=1">Ver tudo</a></div></main>');
        const landing = await page.evaluate(scripts.discoveryScript());
        assert.equal(landing.listingUrl, listingUrl, 'URL de listagem vem do link Ver tudo observado');
        assert.deepEqual(landing.categories.map(item => item.nome), ['Carregadores']);
        await page.setContent('<main><a href="' + listingUrl + '">Ver tudo</a><section class="carousel"><a href="' + landingUrl + 'outra">Tendencia</a></section></main>');
        const finalLanding = await page.evaluate(scripts.discoveryScript());
        assert.equal(finalLanding.listingUrl, listingUrl);
        assert.equal(finalLanding.hasChildren, false, 'Ver tudo nao cria uma categoria filha artificial');
        await page.setContent('<main><section class="recommendations"><a href="' + listingUrl + '">Ver tudo</a></section></main>');
        assert.equal((await page.evaluate(scripts.discoveryScript())).listingUrl, '', 'link de recomendacoes nao representa a listagem da categoria');
        await page.setContent('<main><ol class="andes-pagination"><li class="andes-pagination__button--next"><a class="andes-pagination__link" title="Seguinte" data-andes-pagination-control="next" href="">Seguinte</a></li></ol></main>');
        await page.evaluate(() => {
            window.nextClicks = 0;
            document.querySelector('a').onclick = event => { event.preventDefault(); window.nextClicks += 1; };
        });
        const jsNext = await page.evaluate(scripts.discoveryScript());
        assert.equal(jsNext.nextUrl, '');
        assert.equal(jsNext.hasNext, true, 'next JS pode estar habilitado sem href');
        assert.equal((await page.evaluate(scripts.nextCategoryPageScript(landingUrl))).clicked, true);
        assert.equal(await page.evaluate(() => window.nextClicks), 1);
        assert.equal((await page.evaluate(scripts.nextCategoryPageScript(landingUrl + 'outra/'))).clicked, false, 'categoria deve estar restaurada antes do clique');
        await page.evaluate(() => document.querySelector('li').classList.add('andes-pagination__button--disabled'));
        assert.equal((await page.evaluate(scripts.discoveryScript())).hasNext, false);
        assert.equal((await page.evaluate(scripts.nextCategoryPageScript(landingUrl))).clicked, false, 'controle next desabilitado nao recebe clique');
        await page.goto('https://lista.mercadolivre.com.br/pecas/filtros/');
        await page.setContent('<main>Nenhum produto</main><script type="application/json">{"category_id":"MLB200"}</script>');
        assert.equal((await page.evaluate(scripts.discoveryScript())).effectiveCategoryId,'','arbitrary JSON recommendations do not prove selected category');
        await page.setContent('<main>Nenhum produto</main><script id="__PRELOADED_STATE__" type="application/json">{"initialState":{"pageState":{"filters":[{"id":"category","values":[{"id":"MLB191834"}]}],"recommendations":{"category_id":"MLB200"},"available_filters":[{"id":"category","values":[{"id":"MLB200"}]}]}}}</script>');
        const applied=await page.evaluate(scripts.discoveryScript());
        assert.equal(applied.effectiveCategoryId,'MLB191834','exact applied category proves canonical redirect');
        assert.equal(applied.categoryIdentityConflict,false);
        await page.setContent('<main>Nenhum produto</main><script>window.__PRELOADED_STATE__ = ' + JSON.stringify({initialState:{pageState:{category_id:'MLB191834',name:'Brace } and quote " safely'}}}) + ';</script>');
        assert.equal((await page.evaluate(scripts.discoveryScript())).effectiveCategoryId,'MLB191834','JSON assignment is parsed without script evaluation');
        await page.goto('https://lista.mercadolivre.com.br/_CatId_MLB191834');
        await page.setContent('<main>Nenhum produto</main><script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"initialState":{"pageState":{"category_id":"MLB200"}}}}}</script>');
        const conflict=await page.evaluate(scripts.discoveryScript());
        assert.equal(conflict.effectiveCategoryId,'');
        assert.equal(conflict.categoryIdentityConflict,true,'contradictory document identity cannot fall back to URL ID');
        // Public rescue markup observed in the ML browser on 2026-10-08.
        const searchRescue = '<div class="ui-search-rescue ui-search-rescue--zrp"><img id="not_found" alt="">'
            + '<div class="ui-search-rescue__info"><h3 class="ui-search-rescue__title">Não encontramos resultados para a sua busca</h3>'
            + '<a href="https://www.mercadolivre.com.br/categories.html">Navegue pelas categorias</a></div></div>';
        await page.goto('https://lista.mercadolivre.com.br/_CatId_MLB455956');
        await page.setContent('<main>' + searchRescue + '</main>');
        const routeNotFound = await page.evaluate(scripts.discoveryScript());
        assert.equal(routeNotFound.categoryNotFound, true, 'ID no alias da URL não comprova uma categoria na página de busca não reconhecida');
        assert.equal(routeNotFound.empty, true, 'novo indicador mantém o empty legado compatível');
        await page.setContent('<main>Nenhum anúncio</main>');
        const plainEmpty = await page.evaluate(scripts.discoveryScript());
        assert.equal(plainEmpty.empty, true);
        assert.equal(plainEmpty.categoryNotFound, false, 'texto de categoria vazia sem rescue não significa rota não reconhecida');
        await page.setContent('<main><h1>Anemômetros</h1>' + searchRescue + '</main>');
        assert.equal((await page.evaluate(scripts.discoveryScript())).categoryNotFound, false, 'título preserva categoria legitimamente vazia');
        await page.setContent('<main><nav class="andes-breadcrumb"><a href="https://lista.mercadolivre.com.br/acessorios-veiculos/acessorios-nauticos/">Acessórios Náuticos</a></nav>' + searchRescue + '</main>');
        assert.equal((await page.evaluate(scripts.discoveryScript())).categoryNotFound, false, 'breadcrumb oficial preserva categoria legitimamente vazia');
        await page.setContent('<main>' + searchRescue + '</main><script id="__PRELOADED_STATE__" type="application/json">'
            + JSON.stringify({initialState:{pageState:{filters:[{id:'category',values:[{id:'MLB455956'}]}]}}}) + '</script>');
        assert.equal((await page.evaluate(scripts.discoveryScript())).categoryNotFound, false, 'identidade aplicada no documento preserva categoria legitimamente vazia');
        await page.setContent(fixture(card(35) + searchRescue));
        assert.equal((await page.evaluate(scripts.discoveryScript())).categoryNotFound, false, 'rescue antigo não invalida página com anúncios presentes');
        await page.setContent('<main><section class="recommendations">' + searchRescue + '</section></main>');
        assert.equal((await page.evaluate(scripts.discoveryScript())).categoryNotFound, false, 'rescue de recomendação não representa a página de categoria');
        await page.setContent('<main><div hidden>' + searchRescue + '</div></main>');
        assert.equal((await page.evaluate(scripts.discoveryScript())).categoryNotFound, false, 'rescue oculto não invalida a página em hidratação');
        await page.goto('https://lista.mercadolivre.com.br/_CustId_123456');
        await page.setContent('<main>' + searchRescue + '</main>');
        assert.equal((await page.evaluate(scripts.discoveryScript())).categoryNotFound, false, 'busca vazia do vendedor não é falha de rota de categoria');
        await page.setContent('<main><a rel="next" href="https://lista.mercadolivre.com.br/_CustId_123456_Desde_51">Seguinte</a></main>');
        assert.equal((await page.evaluate(scripts.nextCategoryPageScript(page.url()))).reason, 'not_category_page', 'script de categorias nunca avanca pagina do vendedor');
        await page.setContent('<form id="login_user_form"><input type="password"></form>');
        assert.equal((await page.evaluate(scripts.discoveryScript())).authRequired, true);
        console.log('Pesquisa de Mercado DOM: 121 cards, parser Avant real, clique atrasado, vendedor, catalogo, ausencia e login OK');
    } finally { await browser.close(); }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
