(function bootstrapJKMedias(global) {
    'use strict';

    const app = global.JKMedias || {};
    const modules = app.modules || Object.create(null);
    const state = app.state || Object.create(null);
    const dependencyGraph = app.dependencyGraph || Object.create(null);

    function exposeLegacy(api) {
        Object.entries(api || {}).forEach(([name, value]) => {
            if (typeof value === 'function') global[name] = value;
        });
    }

    function exposeState(names, readonlyNames) {
        const readonly = new Set(readonlyNames || []);
        names.forEach((name) => {
            Object.defineProperty(global, name, {
                configurable: true,
                enumerable: false,
                get: () => state[name],
                set: readonly.has(name) ? undefined : (value) => { state[name] = value; },
            });
        });
    }

    function defineModule(name, dependencies, factory) {
        if (modules[name]) throw new Error('Modulo JKMedias duplicado: ' + name);
        dependencyGraph[name] = Object.freeze([...(dependencies || [])]);
        const api = factory(Object.freeze({ app, modules, state, global })) || {};
        modules[name] = Object.freeze(api);
        exposeLegacy(api);
        return modules[name];
    }

    function assertReady() {
        const missing = [];
        Object.entries(dependencyGraph).forEach(([name, dependencies]) => {
            dependencies.forEach((dependency) => {
                if (!modules[dependency]) missing.push(name + ' -> ' + dependency);
            });
        });
        if (missing.length) throw new Error('Dependencias JKMedias ausentes: ' + missing.join(', '));
        return true;
    }

    Object.assign(app, { modules, state, dependencyGraph, exposeLegacy, exposeState, defineModule, assertReady });
    global.JKMedias = app;

    function definirStatusInicial(msg) {
        const el = document.getElementById('status');
        if (el) el.textContent = msg;
    }

    function parseJsonLocalStorage(chave, fallback) {
        try {
            const valor = localStorage.getItem(chave);
            return valor ? JSON.parse(valor) : fallback;
        } catch (e) {
            console.warn('Valor inválido no localStorage:', chave, e);
            return fallback;
        }
    }

    function numero(v) {
        return Number(v || 0).toLocaleString('pt-BR', { minimumFractionDigits: 0, maximumFractionDigits: 2 });
    }

    function calcularCoberturaMeses(posicaoEstoque, mediaMensal) {
        const media = Number(mediaMensal || 0);
        if (!Number.isFinite(media) || media <= 0) return null;

        const posicao = Number(posicaoEstoque || 0);
        if (!Number.isFinite(posicao) || posicao <= 0) return 0;
        return posicao / media;
    }

    function formatarCoberturaMeses(posicaoEstoque, mediaMensal) {
        const meses = calcularCoberturaMeses(posicaoEstoque, mediaMensal);
        if (meses === null) return '<span class="cobertura-sem-venda">Sem venda</span>';
        return '<strong>' + numero(meses) + '</strong>';
    }

    function formatarMoedaUSD(valor) {
        return '$ ' + Number(valor || 0).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    function parseNumeroFlex(valor, fallback = 0) {
        let txt = String(valor || '').trim();
        if (!txt) return fallback;

        txt = txt
            .replace(/US\$/gi, '')
            .replace(/\$/g, '')
            .replace(/\s+/g, '')
            .replace(/[^0-9,.-]/g, '');

        const hasDot = txt.includes('.');
        const hasComma = txt.includes(',');

        if (hasDot && hasComma) {
            const lastDot = txt.lastIndexOf('.');
            const lastComma = txt.lastIndexOf(',');
            if (lastComma > lastDot) {
                // Formato pt-BR: 1.234,56
                txt = txt.replace(/\./g, '').replace(',', '.');
            } else {
                // Formato en-US: 1,234.56
                txt = txt.replace(/,/g, '');
            }
        } else if (hasComma) {
            // Formato com vírgula decimal
            txt = txt.replace(',', '.');
        }

        const n = Number(txt);
        return Number.isFinite(n) ? n : fallback;
    }

    function toNumeroDecimal(valor) {
        return parseNumeroFlex(valor, 0);
    }

    function formatarNumeroListaPedido(valor, casas = 6) {
        const n = Number(valor || 0);
        if (!Number.isFinite(n) || n <= 0) return '';
        const fixo = Number(n.toFixed(casas));
        return fixo.toLocaleString('pt-BR', { maximumFractionDigits: casas });
    }

    function toNumeroPrompt(valor) {
        return parseNumeroFlex(valor, NaN);
    }

    function normalizarSkuComparacaoLocal(valor) {
        let txt = String(valor || '')
            .trim()
            .toUpperCase()
            .replace(/\s+/g, '')
            .replace(/[^A-Z0-9]/g, '');
        if (/^\d+$/.test(txt)) {
            txt = String(parseInt(txt, 10));
        }
        return txt;
    }

    function chaveOrdenacaoSkuLista(valor) {
        const sku = String(valor || '').trim().toUpperCase();
        const m = sku.match(/^(\d+)(?:[-./](\d+))?$/);
        if (m) {
            return { tipo: 0, n1: parseInt(m[1], 10), n2: parseInt(m[2] || '0', 10), txt: sku };
        }
        return { tipo: 1, n1: Number.MAX_SAFE_INTEGER, n2: Number.MAX_SAFE_INTEGER, txt: sku };
    }

    function ordenarItensListaPedidoPorSku(itens) {
        const lista = Array.isArray(itens) ? [...itens] : [];
        lista.sort((a, b) => {
            const ka = chaveOrdenacaoSkuLista(a && a.SKU);
            const kb = chaveOrdenacaoSkuLista(b && b.SKU);
            if (ka.tipo !== kb.tipo) return ka.tipo - kb.tipo;
            if (ka.n1 !== kb.n1) return ka.n1 - kb.n1;
            if (ka.n2 !== kb.n2) return ka.n2 - kb.n2;
            return ka.txt.localeCompare(kb.txt, 'pt-BR', { numeric: true, sensitivity: 'base' });
        });
        return lista;
    }

    function formatarMesNomeCurto(mesNumero) {
        const nomes = {
            '01': 'Jan', '02': 'Fev', '03': 'Mar', '04': 'Abr',
            '05': 'Mai', '06': 'Jun', '07': 'Jul', '08': 'Ago',
            '09': 'Set', '10': 'Out', '11': 'Nov', '12': 'Dez'
        };
        return nomes[String(mesNumero || '').padStart(2, '0')] || String(mesNumero || '');
    }

    function quebrarMesAno(chaveMes) {
        const texto = String(chaveMes || '').trim();
        const m = texto.match(/^(\d{4})-(\d{2})$/);
        if (!m) {
            return { anoCurto: '', mesNome: texto };
        }
        const anoCurto = m[1].slice(2);
        const mesNome = formatarMesNomeCurto(m[2]);
        return { anoCurto, mesNome };
    }

    function escaparHtml(valor) {
        return String(valor || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function ehUrlAbsoluta(valor) {
        return /^https?:\/\//i.test(String(valor || '').trim());
    }

    function obterUrlFoto(valor) {
        const foto = String(valor || '').trim();
        if (!foto) return '';
        const helper = global.JKAuthenticatedMedia;
        if (helper && typeof helper.normalizarUrlFotoCadastro === 'function') {
            return helper.normalizarUrlFotoCadastro(foto);
        }
        return /^(?:https?:)?\/\//i.test(foto) ? foto : '';
    }

    function ehUrlFotoCadastroProtegida(url) {
        const helper = global.JKAuthenticatedMedia;
        if (helper && typeof helper.ehUrlProtegidaCadastro === 'function') {
            return helper.ehUrlProtegidaCadastro(url);
        }
        return /^(\/api\/cadastro\/foto-arquivo\/|\/api\/cadastro\/foto\/)/i.test(String(url || '').trim());
    }

    function atributoSrcFotoCadastro(url) {
        const foto = escaparHtml(url);
        return ehUrlFotoCadastroProtegida(url)
            ? 'data-jk-auth-src="' + foto + '"'
            : 'src="' + foto + '"';
    }

    function obterCorLoja(valor, indice) {
        const paleta = [
            { accent: '79, 172, 254', soft: '21, 54, 92' },
            { accent: '46, 204, 113', soft: '18, 74, 48' },
            { accent: '155, 89, 182', soft: '64, 33, 84' },
            { accent: '241, 196, 15', soft: '92, 72, 14' },
            { accent: '231, 76, 60', soft: '88, 30, 24' },
            { accent: '26, 188, 156', soft: '16, 78, 71' },
            { accent: '230, 126, 34', soft: '96, 50, 18' },
            { accent: '236, 72, 153', soft: '96, 28, 65' }
        ];
        if (valor === '__todas') return paleta[0];
        return paleta[indice % paleta.length];
    }

    function normalizarSku(valor) {
        return String(valor || '').trim().toUpperCase();
    }

    function normalizarQuantidadeCompraSugerida(valor) {
        const texto = String(valor ?? '').trim();
        if (!/^\d+$/.test(texto)) return null;
        const quantidade = Number(texto);
        return Number.isSafeInteger(quantidade) && quantidade >= 0 ? quantidade : null;
    }

    function formatarDataHoraLocal(isoTexto) {
        const d = new Date(isoTexto || '');
        if (isNaN(d.getTime())) return String(isoTexto || '');
        return d.toLocaleString('pt-BR');
    }

    function normalizarStatusListaPedido(status) {
        let texto = String(status || '').trim();
        if (texto === 'Pedido feito') texto = 'Pedido Aprovado';
        return STATUS_LISTA_OPCOES.includes(texto) ? texto : 'Lista gerada';
    }

    function listaPedidoEstaEmImportacoes(status) {
        const texto = String(status || '')
            .normalize('NFD')
            .replace(/[\u0300-\u036f]/g, '')
            .toLowerCase()
            .trim();
        return texto === 'analisando orcamento';
    }

    function classeStatusListaPedido(status) {
        if (status === 'Em Orçamento') return 'status-em-orcamento';
        if (status === 'Analisando orçamento') return 'status-analisando-orcamento';
        if (['Pedido Aprovado', 'Pedido feito', 'Pedido emitido', 'Em produção', 'Em trânsito', 'Em desembaraço', 'Recebido'].includes(status)) return 'status-pedido-aprovado';
        if (status === 'Pedido cancelado') return 'status-pedido-cancelado';
        return 'status-lista-gerada';
    }

    modules.core = Object.freeze({
        definirStatusInicial,
        parseJsonLocalStorage,
        numero,
        calcularCoberturaMeses,
        formatarCoberturaMeses,
        formatarMoedaUSD,
        parseNumeroFlex,
        toNumeroDecimal,
        formatarNumeroListaPedido,
        toNumeroPrompt,
        normalizarSkuComparacaoLocal,
        chaveOrdenacaoSkuLista,
        ordenarItensListaPedidoPorSku,
        formatarMesNomeCurto,
        quebrarMesAno,
        escaparHtml,
        ehUrlAbsoluta,
        obterUrlFoto,
        ehUrlFotoCadastroProtegida,
        atributoSrcFotoCadastro,
        obterCorLoja,
        normalizarSku,
        normalizarQuantidadeCompraSugerida,
        formatarDataHoraLocal,
        normalizarStatusListaPedido,
        listaPedidoEstaEmImportacoes,
        classeStatusListaPedido
    });
    dependencyGraph.core = Object.freeze([]);
    exposeLegacy(modules.core);
})(typeof globalThis !== 'undefined' ? globalThis : this);
