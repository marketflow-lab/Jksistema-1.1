'use strict';

const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const scripts = require('../static/pesquisa-mercado/page-scripts');
const { sellerFromPage, sameSeller } = require('../electron_app/main/modules/pesquisa-mercado-runner');

const PDP = 'https://carro.mercadolivre.com.br/MLB-100000001-veiculo-de-teste-_JM';
const OWNER_ID = '700001';
const OTHER_ID = '700002';
const STOCK = `https://lista.mercadolivre.com.br/veiculos/_CustId_${OWNER_ID}`;
const OTHER_STOCK = `https://lista.mercadolivre.com.br/veiculos/_CustId_${OTHER_ID}`;
const BRAND = 'Garagem Exemplo';
const escape = value => String(value).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
const stockLink = (url = STOCK, name = BRAND) => `<a class="ui-vip-profile-info__info-link" href="${escape(url)}">${escape(name)}</a>`;
const profile = (links = stockLink()) => `<section class="ui-vip-seller-profile">${links}</section>`;
const main = body => `<main><h1 class="ui-pdp-title">Veículo de teste</h1>${body}</main>`;

async function run() {
    const browser = await chromium.launch({ headless: true });
    let cases = 0;
    try {
        const page = await browser.newPage();
        // Every request is fulfilled locally, including the synthetic vehicle URL.
        await page.route('**/*', route => route.fulfill({ contentType: 'text/html', body: '<main></main>' }));
        await page.goto(PDP);
        const read = async body => {
            await page.setContent(body);
            return page.evaluate(scripts.discoveryScript());
        };
        const assertOwner = (observed, description) => {
            assert.equal(observed.sellerId, OWNER_ID, description);
            assert.equal(observed.perfilUrl, STOCK, 'preserve the emitted /veiculos/_CustId_ route instead of manufacturing another profile');
            assert(['', STOCK].includes(observed.allAdsUrl), 'the PDP cannot replace the emitted stock route with a different listing');
            assert.equal(observed.vendedor, BRAND);
            assert.equal(observed.seller_identity_conflict, false);
            const seller = sellerFromPage(observed);
            assert(seller, 'the production runner accepts the public seller identity');
            assert.equal(seller.key, `id:${OWNER_ID}`);
            assert.equal(seller.id, OWNER_ID);
            assert.equal(seller.perfilUrl, STOCK);
            assert(sameSeller(seller, { seller_id: OWNER_ID, seller_perfil_url: STOCK }));
            assert.equal(sameSeller(seller, { seller_id: OTHER_ID, seller_perfil_url: OTHER_STOCK }), false);
        };
        const assertNoOwner = (observed, description) => {
            assert.equal(observed.sellerId, '', description);
            assert.equal(observed.perfilUrl, '', description);
            assert.equal(observed.allAdsUrl, '', description);
            assert.equal(sellerFromPage(observed), null, description);
        };
        const check = async (description, body, verify = assertNoOwner) => {
            verify(await read(body), description);
            cases += 1;
        };

        await check('the vehicle PDP exposes its own public seller and stock route', main(profile()), assertOwner);
        await check('identical links are not ambiguous', main(profile(stockLink() + stockLink())), assertOwner);
        await check('identical owner components are not ambiguous', main(profile() + profile()), assertOwner);
        await page.goto(STOCK);
        await check('the observed stock page keeps its own first-page route', '<main><h1>Estoque de teste</h1>'
            + `<span class="ui-ms-profile__name">${BRAND}</span></main>`, observed => {
            assertOwner(observed);
            assert.equal(observed.allAdsUrl, STOCK, 'the stock page is its own first seller listing');
        });
        await page.goto(PDP);

        const excluded = [
            ['recommendations', '<section class="recommendations">', '</section>'],
            ['carousel', '<section class="carousel">', '</section>'],
            ['same seller rail', '<section class="same-seller">', '</section>'],
            ['search item', '<li class="ui-search-layout__item">', '</li>'],
            ['polycard', '<article class="poly-card">', '</article>'],
            ['search result', '<article class="ui-search-result">', '</article>'],
            ['test-id result card', '<article data-testid="search-result">', '</article>']
        ];
        for (const [description, open, close] of excluded) {
            await check(`${description} cannot establish the PDP owner`, main(open + profile() + close));
            await check(`${description} cannot conflict with a genuine owner`, main(profile() + open + profile(stockLink(OTHER_STOCK, 'Outra Garagem')) + close), assertOwner);
            await check(`${description} conventional seller link cannot conflict with the vehicle owner`, main(profile() + open
                + `<section class="ui-pdp-seller"><a class="ui-pdp-seller__link" href="${OTHER_STOCK}">Outra Garagem</a></section>` + close), assertOwner);
            await check(`${description} conventional seller name cannot replace the vehicle owner name`, main(profile() + open
                + '<span class="ui-pdp-seller__header__title">Outra Garagem</span>' + close), assertOwner);
        }
        await check('an official-store card cannot replace the vehicle owner', main(profile()
            + '<article class="poly-card"><a href="https://www.mercadolivre.com.br/loja/outra-garagem">Loja oficial Outra Garagem</a></article>'), assertOwner);
        await check('another seller inventory card cannot replace the own inventory route', main(profile()
            + `<article class="poly-card"><a href="${OTHER_STOCK}">Ver todos os anúncios</a></article>`), assertOwner);
        await check('a component outside the product main is not the PDP owner', main('') + `<aside>${profile()}</aside>`);
        await check('a component without an own product title is not a PDP owner', `<main>${profile()}</main>`);
        await check('a title outside the product main does not authorize its component', `<h1 class="ui-pdp-title">Título externo</h1><main>${profile()}</main>`);
        await page.goto('https://lista.mercadolivre.com.br/veiculos/');
        await check('a listing URL cannot become a product owner through copied PDP markup', main(profile()));
        await page.goto(PDP);
        await check('a business name alone is not a seller identity', main(profile(`<span>${BRAND}</span>`)));
        await check('WhatsApp alone is not a seller profile', main(profile('<a class="ui-vip-profile-info__info-link" href="https://wa.me/0000000000">WhatsApp</a>')));
        await check('name and contact buttons do not manufacture a profile', main(profile(`<span>${BRAND}</span><a href="https://wa.me/0000000000">WhatsApp</a><a href="tel:+0000000000">Contato</a>`)));

        const invalidUrls = [
            ['HTTP', STOCK.replace('https:', 'http:')],
            ['foreign host', STOCK.replace('lista.mercadolivre.com.br', 'example.invalid')],
            ['lookalike host', STOCK.replace('lista.mercadolivre.com.br', 'lista.mercadolivre.com.br.example.invalid')],
            ['credentials', STOCK.replace('https://', 'https://fixture:fixture@')],
            ['nondefault port', STOCK.replace('.com.br/', '.com.br:8443/')],
            ['different seller query', STOCK + `?seller_id=${OTHER_ID}`],
            ['keyword query', STOCK + '?q=veiculo'],
            ['pagination query', STOCK + '?page=2'],
            ['pagination path', STOCK + '_Desde_49'],
            ['sensitive query', STOCK + '?access_token=fixture'],
            ['product URL', 'https://carro.mercadolivre.com.br/MLB-100000002-outro-veiculo-_JM'],
            ['catalog URL', 'https://www.mercadolivre.com.br/p/MLB100000002'],
            ['extended stock path', STOCK + '/outro-caminho']
        ];
        for (const [description, url] of invalidUrls) {
            await check(`${description} is not an observed public stock identity`, main(profile(stockLink(url))));
        }

        const assertConflict = observed => {
            assert.equal(observed.seller_identity_conflict, true, 'conflicting owner evidence is explicit');
            assert.equal(sellerFromPage(observed), null, 'the runner must reject conflicting owner identities');
        };
        await check('different IDs in the same owner component are blocked', main(profile(stockLink() + stockLink(OTHER_STOCK, 'Outra Garagem'))), assertConflict);
        await check('different owner components are blocked', main(profile() + profile(stockLink(OTHER_STOCK, 'Outra Garagem'))), assertConflict);
        await check('vehicle and conventional owner components cannot disagree', main(profile()
            + `<section class="ui-pdp-seller"><a class="ui-pdp-seller__link" href="${OTHER_STOCK}">Outra Garagem</a></section>`), assertConflict);

        console.log(`Pesquisa de Mercado vehicle seller: ${cases} offline DOM/runner identity cases passed.`);
    } finally {
        await browser.close();
    }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
