'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(
    path.join(root, 'static', 'favoritos', 'tabelas-layout', '04-promocoes-busca-ranking.js'),
    'utf8'
);

function extractFunction(name, context) {
    const marker = `function ${name}`;
    const start = source.indexOf(marker);
    assert.ok(start >= 0, `funcao ${name} ausente`);
    const functionStart = source.slice(Math.max(0, start - 6), start) === 'async ' ? start - 6 : start;
    const paramsOpen = source.indexOf('(', start);
    let paramsDepth = 0;
    let paramsClose = -1;
    for (let index = paramsOpen; index < source.length; index += 1) {
        const char = source[index];
        if (char === '(') paramsDepth += 1;
        if (char === ')' && --paramsDepth === 0) {
            paramsClose = index;
            break;
        }
    }
    assert.ok(paramsClose > paramsOpen, `parametros de ${name} invalidos`);
    const open = source.indexOf('{', paramsClose);
    let depth = 0;
    let quote = '';
    let escaped = false;
    let end = -1;
    for (let index = open; index < source.length; index += 1) {
        const char = source[index];
        if (quote) {
            if (escaped) escaped = false;
            else if (char === '\\') escaped = true;
            else if (char === quote) quote = '';
            continue;
        }
        if (char === '"' || char === "'" || char === '`') {
            quote = char;
            continue;
        }
        if (char === '{') depth += 1;
        if (char === '}' && --depth === 0) {
            end = index + 1;
            break;
        }
    }
    assert.ok(end > open, `fim de ${name} ausente`);
    return vm.runInNewContext(`(${source.slice(functionStart, end)})`, context);
}

async function main() {
    const mensagens = [];
    const context = {
        URL,
        console,
        setTimeout,
        clearTimeout,
        mlWebviewEl: null,
        favoritosBrowserShellBridge: null,
        mostrarBalaoFavoritosStatus: mensagem => mensagens.push(String(mensagem || '')),
        abrirBalaoResultadosMl: () => {}
    };
    [
        'urlHttpValidaFavoritos',
        'urlPertenceAoMercadoLivreFavoritos',
        'urlEmFluxoAutenticacaoMercadoLivreFavoritos',
        'obterEstadoFrescoBrowserShellFavoritos',
        'obterEstadoAutenticacaoMercadoLivreFavoritos',
        'validarLoginMercadoLivreAntesDeContinuarFavoritos'
    ].forEach(name => {
        context[name] = extractFunction(name, context);
    });

    const executarCenario = async ({ shellState, domState = null, rejectDom = false, pendingDom = false, rejectShell = false }) => {
        let shellCalls = 0;
        let continuacoes = 0;
        const loginAntigo = 'https://www.mercadolivre.com.br/login';
        context.mlWebviewEl = {
            getURL: () => loginAntigo,
            executeJavaScript: async () => {
                if (rejectDom) throw new Error('leitura DOM transitoria');
                if (pendingDom) return new Promise(() => {});
                return domState;
            }
        };
        context.favoritosBrowserShellBridge = {
            verificarEstado: async (urlEsperada, timeoutMs) => {
                shellCalls += 1;
                assert.strictEqual(urlEsperada, loginAntigo, 'URL antiga serve apenas para correlacionar a consulta fresca');
                assert.strictEqual(timeoutMs, 1200, 'fallback deve ter timeout curto e limitado');
                if (rejectShell) throw new Error('IPC indisponivel');
                return shellState;
            }
        };
        const botao = { disabled: true };
        const result = await context.validarLoginMercadoLivreAntesDeContinuarFavoritos(async () => {
            continuacoes += 1;
            return true;
        }, botao);
        return { result, shellCalls, continuacoes, botao };
    };

    const recuperado = await executarCenario({
        rejectDom: true,
        shellState: {
            success: true,
            available: true,
            attached: true,
            url: 'https://lista.mercadolivre.com.br/pecas-honda',
            authFlow: false
        }
    });
    assert.strictEqual(recuperado.result, true, 'estado fresco e valido deve liberar a retomada');
    assert.strictEqual(recuperado.shellCalls, 1, 'estado fresco deve ser consultado uma unica vez');
    assert.strictEqual(recuperado.continuacoes, 1, 'coleta deve continuar exatamente uma vez');

    const inicioDomPendente = Date.now();
    const domPendente = await executarCenario({
        pendingDom: true,
        shellState: {
            success: true,
            available: true,
            attached: true,
            url: 'https://lista.mercadolivre.com.br/pecas-honda',
            authFlow: false
        }
    });
    assert.strictEqual(domPendente.result, true, 'promessa DOM pendente deve cair no estado fresco do shell');
    assert.strictEqual(domPendente.shellCalls, 1);
    assert.strictEqual(domPendente.continuacoes, 1);
    assert.ok(Date.now() - inicioDomPendente < 2000, 'DOM pendente nao pode atrasar o clique por varios segundos');

    const autenticando = await executarCenario({
        shellState: {
            success: true,
            available: true,
            attached: true,
            url: 'https://lista.mercadolivre.com.br/pecas-honda',
            authFlow: true
        }
    });
    assert.strictEqual(autenticando.result, false, 'authFlow fresco deve manter a retomada bloqueada');
    assert.strictEqual(autenticando.continuacoes, 0);

    const urlDeLogin = await executarCenario({
        shellState: {
            success: true,
            available: true,
            attached: true,
            url: 'https://www.mercadolivre.com.br/gz/account-verification',
            authFlow: false
        }
    });
    assert.strictEqual(urlDeLogin.result, false, 'URL de login deve bloquear mesmo sem authFlow sinalizado');
    assert.strictEqual(urlDeLogin.continuacoes, 0);

    const urlExterna = await executarCenario({
        shellState: {
            success: true,
            available: true,
            attached: true,
            url: 'https://example.com/pagina-carregada',
            authFlow: false
        }
    });
    assert.strictEqual(urlExterna.result, false, 'URL externa nao pode validar o BrowserView do Mercado Livre');
    assert.strictEqual(urlExterna.continuacoes, 0);

    const domExterno = await executarCenario({
        domState: {
            url: 'https://example.com/pagina-carregada',
            needsLogin: false
        },
        shellState: {
            success: false,
            available: true,
            attached: true,
            url: 'https://example.com/pagina-carregada',
            authFlow: false
        }
    });
    assert.strictEqual(domExterno.result, false, 'URL externa devolvida pelo DOM nao pode validar o estado ML');
    assert.strictEqual(domExterno.shellCalls, 1, 'DOM externo deve exigir confirmacao fresca do shell');
    assert.strictEqual(domExterno.continuacoes, 0);

    const estadosInvalidos = [
        { success: false, available: true, attached: true, url: 'https://lista.mercadolivre.com.br/pecas' },
        { success: true, available: false, attached: true, url: 'https://lista.mercadolivre.com.br/pecas' },
        { success: true, available: true, attached: false, url: 'https://lista.mercadolivre.com.br/pecas' },
        { success: true, available: true, attached: true, url: 'file:///pagina-local.html' }
    ];
    for (const shellState of estadosInvalidos) {
        const invalido = await executarCenario({ shellState });
        assert.strictEqual(invalido.result, false, 'estado shell incompleto ou invalido deve permanecer indeterminado');
        assert.strictEqual(invalido.continuacoes, 0);
        assert.strictEqual(invalido.botao.disabled, false, 'usuario deve poder tentar novamente depois do bloqueio seguro');
    }

    const falhaShell = await executarCenario({ rejectShell: true });
    assert.strictEqual(falhaShell.result, false, 'falha do fallback nao pode liberar a coleta');
    assert.strictEqual(falhaShell.continuacoes, 0);
    assert.ok(
        mensagens.some(mensagem => mensagem.includes('nao foi possivel confirmar seu estado')),
        'falha segura deve orientar nova tentativa'
    );

    console.log('Favoritos continuar login shell fallback checks passed');
}

main().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
