const assert = require('assert');
const path = require('path');

const pool = require(path.join(__dirname, '..', 'static', 'favoritos', 'v2', 'browser', 'worker-pool.js'));

const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

async function testarLimiteEReposicao() {
    let ativos = 0;
    let maxAtivos = 0;
    const iniciados = [];
    const concluidos = [];
    const resultado = await pool.runQueue({
        items: Array.from({ length: 10 }, (_item, index) => ({ sku: `SKU-${index + 1}` })),
        concurrency: 4,
        workerIds: ['w1', 'w2', 'w3', 'w4'],
        processItem: async (item, meta) => {
            assert.ok(Number(meta.taskStartedAt) > 0, 'cada tarefa deve receber o proprio inicio para telemetria');
            ativos += 1;
            maxAtivos = Math.max(maxAtivos, ativos);
            iniciados.push({ sku: item.sku, workerId: meta.workerId, at: Date.now() });
            await wait(meta.index < 4 ? 80 + (meta.index * 30) : 5);
            ativos -= 1;
            concluidos.push(item.sku);
            return { sku: item.sku, workerId: meta.workerId };
        }
    });

    assert.strictEqual(maxAtivos, 4, '10 SKUs devem usar no maximo quatro trabalhadores');
    assert.strictEqual(iniciados.slice(0, 4).length, 4, 'os quatro primeiros SKUs devem iniciar imediatamente');
    assert.strictEqual(resultado.state.completed, 10, 'todos os 10 SKUs devem sair da fila');
    assert.deepStrictEqual(
        resultado.results.map(item => item.value.sku),
        Array.from({ length: 10 }, (_item, index) => `SKU-${index + 1}`),
        'resultados devem permanecer na ordem original apesar da conclusao fora de ordem'
    );
    assert.notDeepStrictEqual(concluidos, resultado.results.map(item => item.value.sku), 'o teste precisa exercitar conclusao fora de ordem');
    assert.ok(resultado.results.every(item => Number(item.taskElapsedMs) >= 0), 'resultado deve guardar a duracao individual');
}

async function testarQuantidadeExataDeWorkers() {
    for (let total = 1; total <= 4; total += 1) {
        const usados = new Set();
        const resultado = await pool.runQueue({
            items: Array.from({ length: total }, (_item, index) => index),
            concurrency: 4,
            processItem: async (_item, meta) => {
                usados.add(meta.workerId);
                await wait(2);
                return meta.workerId;
            }
        });
        assert.strictEqual(resultado.workerIds.length, total, `${total} SKU(s) devem criar exatamente ${total} trabalhador(es)`);
        assert.strictEqual(usados.size, total, `${total} SKU(s) devem ocupar exatamente ${total} trabalhador(es)`);
    }
}

async function testarRetryNoMesmoWorkerEContinuidade() {
    const chamadas = [];
    const resultado = await pool.runQueue({
        items: [{ sku: 'A' }, { sku: 'B' }, { sku: 'C' }],
        concurrency: 2,
        retryCount: 1,
        retryDelayMs: 1,
        processItem: async (item, meta) => {
            chamadas.push({ sku: item.sku, workerId: meta.workerId, attempt: meta.attempt });
            if (item.sku === 'A' && meta.attempt === 1) throw new Error('falha temporaria');
            if (item.sku === 'B') throw new Error('falha definitiva');
            return item.sku;
        }
    });
    const tentativasA = chamadas.filter(item => item.sku === 'A');
    const tentativasB = chamadas.filter(item => item.sku === 'B');
    assert.deepStrictEqual(tentativasA.map(item => item.attempt), [1, 2], 'falha comum deve repetir uma unica vez');
    assert.strictEqual(tentativasA[0].workerId, tentativasA[1].workerId, 'retry deve permanecer no mesmo trabalhador');
    assert.deepStrictEqual(tentativasB.map(item => item.attempt), [1, 2], 'segunda falha deve encerrar o SKU sem terceira tentativa');
    assert.strictEqual(resultado.results[1].success, false, 'SKU com duas falhas deve ser registrado como falha');
    assert.strictEqual(resultado.results[2].success, true, 'fila deve continuar depois da segunda falha comum');
}

async function testarErroFatalInterrompeFila() {
    const iniciados = [];
    let fatalMeta = null;
    await assert.rejects(() => pool.runQueue({
        items: Array.from({ length: 10 }, (_item, index) => ({ sku: `F-${index + 1}` })),
        concurrency: 4,
        retryCount: 1,
        processItem: async (item, meta) => {
            iniciados.push(item.sku);
            if (meta.index === 0) {
                const err = new Error('login necessario');
                err.loginMercadoLivreNecessario = true;
                throw err;
            }
            await wait(15);
            return item.sku;
        },
        isFatal: err => !!err.loginMercadoLivreNecessario,
        onFatal: async (_err, meta) => { fatalMeta = meta; }
    }), err => err && err.favoritosPoolFatal === true);
    assert.ok(fatalMeta && fatalMeta.workerId === 'w1', 'erro global deve identificar o trabalhador problematico');
    assert.ok(iniciados.length <= 4, 'erro global nao deve liberar novos SKUs depois do primeiro lote ativo');
}

async function main() {
    await testarQuantidadeExataDeWorkers();
    await testarLimiteEReposicao();
    await testarRetryNoMesmoWorkerEContinuidade();
    await testarErroFatalInterrompeFila();
    console.log('Favoritos workers pool checks passed');
}

main().catch(err => {
    console.error(err);
    process.exitCode = 1;
});
