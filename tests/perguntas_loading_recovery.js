'use strict';
const assert = require('assert');
const { QuestionPager } = require('../static/perguntas_pos_venda/loading.js');

async function main() {
    let fail = true;
    const pager = new QuestionPager([{store_id: 's', nome: 'Synthetic'}], async (store, offset) => {
        if (offset === 29 && fail) { fail = false; throw new DOMException('Canceled', 'AbortError'); }
        const length = offset === 0 ? 29 : Math.min(20, 80 - offset);
        return { questions: Array.from({length}, (_, i) => ({id: offset+i, date_created: new Date(1000000-(offset+i)*1000).toISOString()})),
            total: 80, next_offset: offset + length < 80 ? offset + length : null };
    });
    const first = await pager.page(1);
    await assert.rejects(pager.page(2), {name: 'AbortError'});
    const all = [...first];
    for (let page = 2; page <= 4; page++) all.push(...await pager.page(page));
    assert.strictEqual(all.length, 80, 'cancelamento deve preservar as perguntas retiradas do buffer antes da falha');
    assert.strictEqual(new Set(all.map(q => q.id)).size, 80);
    console.log('perguntas_loading_recovery: pager cancellation passed');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
