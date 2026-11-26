'use strict';

// Public listing paths observed on Mercado Livre. The taxonomy path selects a
// candidate; only a fresh listing document can confirm its ID and supply offers.
const listingPaths = new Map([
    ['MLB1384', '/bebes/'],
    ['MLB1384/MLB40563', '/bebes/cercadinho/'],
    ['MLB1196', '/livros-revistas-comics/'],
    ['MLB1196/MLB437616', '/livros-revistas-comics/livros-fisicos/'],
    ['MLB1574', '/casa-moveis-decoracao/'],
    ['MLB5672', '/acessorios-veiculos/'],
    ['MLB1368', '/arte-papelaria-armarinho/']
]);

function publishedCategoryListing(pathIds) {
    if (!Array.isArray(pathIds) || !pathIds.every(id => typeof id === 'string' && /^MLB\d+$/.test(id))) return null;
    const pathname = listingPaths.get(pathIds.join('/'));
    return pathname ? 'https://lista.mercadolivre.com.br' + pathname : null;
}

module.exports = { publishedCategoryListing };
