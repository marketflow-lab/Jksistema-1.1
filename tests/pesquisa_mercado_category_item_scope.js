'use strict';

const assert = require('node:assert/strict');
const { scopeCategoryItems, validateCategoryItemScope } = require('../electron_app/main/modules/pesquisa-mercado-category-item-scope');

const cycling = 'https://lista.mercadolivre.com.br/esportes-fitness/ciclismo/';
const parts = cycling + 'pecas/';
const leaf = parts + 'coroas/';
const ancestry = Object.freeze([
    Object.freeze({ key: 'MLB1276', nome: 'Esportes e Fitness', url: 'https://lista.mercadolivre.com.br/_CatId_MLB1276' }),
    Object.freeze({ key: 'MLB1292', nome: 'Ciclismo', url: cycling }),
    Object.freeze({ key: 'MLB1934', nome: 'Peças', url: parts }),
    Object.freeze({ key: 'MLB122156', nome: 'Coroas', url: leaf })
]);
const options = { categoryKey: 'MLB122156', documentCategoryId: 'MLB1934', categoryBaseUrl: leaf };
const pair = (id, categoryId = options.categoryKey) => ({ id, categoryId });
const ad = id => ({ id, url: 'https://produto.mercadolivre.com.br/MLB-' + id.slice(3) + '-fixture-_JM',
    titulo: 'Fixture pública', _avantEvidence: { present: true, metrics: { vendas: 37 } } });
const page = (overrides = {}) => ({
    url: parts + '*_NoIndex_True', effectiveCategoryId: 'MLB1934', categoryDocumentIdentity: true,
    categoryIdentityConflict: false, categoryContext: { key: 'MLB1934', nome: 'Peças', baseUrl: parts },
    categoryItems: [pair('MLB1'), pair('MLB2', 'MLB1934'), pair('MLB3', 'MLB999')],
    anuncios: [ad('MLB1'), ad('MLB2'), ad('MLB3'), ad('MLB4')], empty: true,
    hasNext: true, nextUrl: parts + '*_Desde_49_NoIndex_True', ...overrides
});
const clone = value => JSON.parse(JSON.stringify(value));
const source = page();
const before = clone(source);
const scoped = scopeCategoryItems(source, options, ancestry);
assert.deepEqual(scoped.anuncios.map(item => item.id), ['MLB1'], 'an item without its own exact category pair is never admitted');
assert.equal(scoped.anuncios[0], source.anuncios[0], 'Avant evidence is preserved on the original proven item');
assert.deepEqual(scoped.categoryItems, [pair('MLB1')]);
assert.deepEqual(source, before, 'filtering does not mutate the source page');
assert.equal(scoped.effectiveCategoryId, 'MLB1934', 'the ancestor document identity is not relabelled as the requested leaf');
assert.equal(scoped.categoryDocumentIdentity, true);
assert.equal(scoped.empty, false, 'a filtered page never proves the leaf empty');
assert.equal(scoped.hasNext, true);
assert.equal(scoped.nextUrl, source.nextUrl);
assert.deepEqual(scoped.categoryItemScope, {
    version: 1, categoryKey: 'MLB122156', transportCategoryId: 'MLB1934', transportBaseUrl: parts, categoryBaseUrl: leaf
});
assert.equal(validateCategoryItemScope(scoped.categoryItemScope, ancestry), true);
assert.equal(JSON.stringify(scoped.categoryItemScope).includes('MLB1"'), false, 'the checkpoint descriptor stores no item identities');
const previousScope = clone(scoped.categoryItemScope);
const nextOptions = { ...options, firstPage: false, previousScope };
const second = scopeCategoryItems(page({ url: parts + '*_Desde_49_NoIndex_True',
    categoryItems: [pair('MLB9', 'MLB1934')], anuncios: [ad('MLB9')], nextUrl: parts + '*_Desde_97_NoIndex_True' }), nextOptions, ancestry);
assert(second, 'a later ancestor page can contain no exact leaf items');
assert.deepEqual(second.anuncios, []);
assert.equal(second.empty, false);
assert.equal(second.hasNext, true, 'zero matching items do not terminate ancestor pagination');
const third = scopeCategoryItems(page({ url: second.nextUrl, categoryItems: [pair('MLB10')], anuncios: [ad('MLB10')],
    hasNext: false, nextUrl: '' }), { ...nextOptions, previousScope: second.categoryItemScope }, ancestry);
assert.deepEqual(third.anuncios.map(item => item.id), ['MLB10'], 'matching leaf items remain discoverable after an unmatched page');
assert.equal(third.hasNext, false);
assert.equal(scopeCategoryItems(page({ categoryItems: [], anuncios: [] }), options, ancestry), null, 'the initial fallback needs a proven exact item');
assert.equal(scopeCategoryItems(page(), { ...options, previousScope }, ancestry), null, 'resume cannot silently use first-page defaults');
assert.equal(scopeCategoryItems(page(), { ...options, firstPage: false }, ancestry), null, 'resume needs a descriptor');
assert.equal(scopeCategoryItems(page(), { ...nextOptions, firstPage: 'false' }, ancestry), null);

for (const changes of [
    { categoryDocumentIdentity: false }, { categoryIdentityConflict: true }, { effectiveCategoryId: 'MLB1292' },
    { documentCategoryId: 'MLB1292' }, { categoryTrends: true }, { authRequired: true }, { avantAuthRequired: true },
    { categoryNotFound: true }, { sellerId: '42' }, { perfilUrl: 'https://www.mercadolivre.com.br/perfil/fixture' },
    { allAdsUrl: 'https://lista.mercadolivre.com.br/_CustId_42' }, { seller_identity_conflict: true },
    { categoryContext: { key: 'MLB1292', nome: 'Ciclismo', baseUrl: parts } },
    { categoryContext: { key: 'MLB1934', nome: 'Peças', baseUrl: cycling } }
]) assert.equal(scopeCategoryItems(page(changes), options, ancestry), null, 'unconfirmed or changed document context cannot widen the scope');
assert(scopeCategoryItems(page({ categoryContext: null }), options, ancestry), 'explicit applied document identity can stand without optional context metadata');
assert(scopeCategoryItems(page({ categoryContext: { key: 'MLB1934', nome: 'Peças para bicicleta', baseUrl: parts } }), options, ancestry),
    'a public label rename does not override a coupled exact category ID and observed base');

for (const url of [parts, parts.slice(0, -1), parts + '_NoIndex_True', parts + '*', parts + '*_NoIndex_True',
    parts + '_Desde_49_NoIndex_True', parts + '*_NoIndex_True_Desde_49', parts.slice(0, -1) + '_Desde_49_NoIndex_True']) {
    assert(scopeCategoryItems(page({ url }), options, ancestry), 'an observed category base and neutral pagination remain scoped: ' + url);
}
for (const url of [cycling, leaf, parts + 'coroas_NoIndex_True', parts + 'outro-termo/', parts + '$query',
    parts + '*_NoIndex_False', parts + '*_OrderId_PRICE', parts + '*_BRAND_1', parts + '_Desde_0',
    parts + '_Desde_49_Desde_97', parts + '_Desde_9999999999', parts + '*_NoIndex_True?category_id=MLB1934',
    parts + '?original_category_landing=true', parts + '?q=*', parts + '*_NoIndex_True#tracking',
    parts + '%2f*', parts + '%252f*', parts.replace('https:', 'http:'), parts.replace('https://', 'https://name:secret@'),
    parts.replace('lista.mercadolivre.com.br', 'lista.mercadolivre.com.br.example.invalid'),
    parts.replace('lista.mercadolivre.com.br', 'outro.mercadolivre.com.br'), 'https://lista.mercadolivre.com.br/_CustId_42']) {
    assert.equal(scopeCategoryItems(page({ url }), options, ancestry), null, 'another route, keyword, filter or authority is rejected');
}
for (const nextUrl of [leaf, cycling + '*_Desde_49_NoIndex_True', parts + '*_Desde_49_NoIndex_True?q=coroas']) {
    assert.equal(scopeCategoryItems(page({ nextUrl }), options, ancestry), null, 'a next link cannot leave the exact ancestor transport');
}

const duplicated = scopeCategoryItems(page({ categoryItems: [pair('MLB1'), pair('MLB1'), pair('MLB2'), pair('MLB2', 'MLB1934')],
    anuncios: [ad('MLB1'), ad('MLB1'), ad('MLB2')] }), options, ancestry);
assert.deepEqual(duplicated.anuncios.map(item => item.id), ['MLB1'], 'replicated equal metadata is safe; a conflicted item is excluded');
const conflicting = page({ categoryItems: [pair('MLB1'), pair('MLB1', 'MLB1934')] });
assert.equal(scopeCategoryItems(conflicting, options, ancestry), null, 'conflicting metadata cannot establish the first exact item');
assert.deepEqual(scopeCategoryItems(conflicting, nextOptions, ancestry).anuncios, [], 'a conflict on a later page excludes that item and keeps pagination');
assert.equal(scopeCategoryItems(page({ categoryItems: [pair('MLB1'), { id: 'MLB1' }] }), options, ancestry), null);
assert.equal(scopeCategoryItems(page({ anuncios: [{ ...ad('MLB1'), category_id: 'MLB1934' }] }), options, ancestry), null);
assert.equal(scopeCategoryItems(page({ anuncios: [{ ...ad('MLB1'), categoryId: 'MLB1934' }, ad('MLB1')] }), options, ancestry), null);
assert.equal(scopeCategoryItems(page({ categoryItems: [pair('MLB2')], anuncios: [ad('MLB1')] }), options, ancestry), null, 'item/category pairs cannot be joined by position');
for (const url of [
    'https://produto.mercadolivre.com.br/MLB-2-outra-categoria-_JM',
    'https://www.mercadolivre.com.br/fixture/p/MLB999?wid=MLB2',
    'https://www.mercadolivre.com.br/fixture/up/MLBU999?wid=MLB1&wid=MLB2',
    'https://www.mercadolivre.com.br/fixture/p/MLB999?wid=MLB1#tracking=fixture&wid=MLB2',
    'https://www.mercadolivre.com.br/fixture/p/MLB999?wid=MLB1#wid%3DMLB2',
    'https://produto.mercadolivre.com.br/MLB-1-fixture-_JM?item_id=MLB2',
    'https://www.mercadolivre.com.br/fixture/p/MLB999',
    'https://www.mercadolivre.com.br/fixture/up/MLBU999?wid=unknown',
    'https://example.invalid/MLB-1-fixture-_JM',
    'https://name:secret@produto.mercadolivre.com.br/MLB-1-fixture-_JM',
    'https://produto.mercadolivre.com.br/MLB-1-fixture-_JM?token=fixture'
]) assert.equal(scopeCategoryItems(page({ anuncios: [{ ...ad('MLB1'), url }] }), options, ancestry), null,
    'a category metadata ID cannot authorize visiting another item or authority');
for (const url of [
    ad('MLB1').url,
    'https://www.mercadolivre.com.br/fixture/p/MLB999?wid=MLB1',
    'https://www.mercadolivre.com.br/fixture/up/MLBU999?wid=MLB1',
    'https://www.mercadolivre.com.br/fixture/up/MLBU999#tracking=fixture&wid=MLB1',
    'https://www.mercadolivre.com.br/fixture/p/MLB999#wid%3DMLB1',
    'https://www.mercadolivre.com.br/fixture/p/MLB999?wid=MLB1&item_id=MLB1#tracking=fixture&wid=MLB1'
]) assert.deepEqual(scopeCategoryItems(page({ anuncios: [{ ...ad('MLB1'), url }] }), options, ancestry).anuncios.map(item => item.id), ['MLB1'],
    'a direct product or catalogue/up URL remains usable when every observed item identity agrees');

for (const field of ['version', 'categoryKey', 'transportCategoryId', 'transportBaseUrl', 'categoryBaseUrl']) {
    const changed = { ...previousScope, [field]: field === 'version' ? 2 : 'changed' };
    assert.equal(validateCategoryItemScope(changed, ancestry), false, 'every checkpoint identity field is revalidated');
    assert.equal(scopeCategoryItems(page(), { ...nextOptions, previousScope: changed }, ancestry), null);
    assert.equal(scopeCategoryItems(page({ categoryItemScope: changed }), nextOptions, ancestry), null);
}
assert.equal(validateCategoryItemScope({ ...previousScope, itemIds: ['MLB1'] }, ancestry), false, 'the persisted descriptor has a closed schema');
assert.equal(validateCategoryItemScope(previousScope, [...ancestry.slice(0, -1), { ...ancestry.at(-1), key: 'MLB999' }]), false);
assert.equal(validateCategoryItemScope(previousScope, [ancestry[0], ancestry[2], ancestry[1], ancestry[3]]), false, 'reordered observed ancestors do not validate a checkpoint');
assert.equal(validateCategoryItemScope(previousScope, [...ancestry, ancestry.at(-1)]), false, 'duplicate official IDs are ambiguous');
assert.equal(validateCategoryItemScope(previousScope, [...ancestry.slice(0, 2), { ...ancestry[2], url: 'https://lista.mercadolivre.com.br/_CatId_MLB1934' }, ancestry[3]]), false,
    'an alias is not an observed ancestor transport URL');
assert.equal(validateCategoryItemScope(previousScope, ancestry.map(node => node.key === 'MLB1934' ? { ...node, url: parts + '*_NoIndex_True' } : node)), true,
    'an observed wildcard route revalidates the same canonical base');
assert.equal(scopeCategoryItems(page({ effectiveCategoryId: 'MLB122156' }), { ...options, documentCategoryId: 'MLB122156' }, ancestry), null,
    'this fallback only operates through a strict official ancestor');
const higherTransport = { ...options, documentCategoryId: 'MLB1292' };
const higherPage = page({ url: cycling + '*_NoIndex_True', effectiveCategoryId: 'MLB1292', categoryContext: { key: 'MLB1292', nome: 'Ciclismo', baseUrl: cycling },
    nextUrl: cycling + '*_Desde_49_NoIndex_True' });
assert(scopeCategoryItems(higherPage, higherTransport, ancestry));
assert.equal(scopeCategoryItems(higherPage, { ...higherTransport, firstPage: false, previousScope }, ancestry), null,
    'resuming cannot switch to a broader ancestor even if its document and IDs are otherwise valid');
assert.equal(scopeCategoryItems(page(), { ...options, categoryBaseUrl: parts + 'outra-categoria/' }, ancestry), null);
console.log('Pesquisa de Mercado exact item scope: coupled categories, ancestor proof, pagination, conflicts and checkpoint resume OK');
