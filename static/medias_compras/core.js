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

    function obterPlanejamentoCompra(item) {
        if (!item || Number(item.planejamento_versao) !== 1) return null;
        const numeroValido = valor => valor !== null && valor !== undefined && valor !== '' && Number.isFinite(Number(valor)) ? Number(valor) : null;
        const tipos = { alta: 'Alta', queda: 'Queda', estavel: 'Estável', insuficiente: 'Dados insuficientes', indeterminada: 'Tendência indeterminada' };
        const tendencia = Object.hasOwn(tipos, item.tendencia_vendas) ? item.tendencia_vendas : 'indeterminada';
        const variacao = numeroValido(item.variacao_vendas_percentual);
        const percentual = variacao !== null && ['alta', 'queda'].includes(tendencia)
            ? ' ' + (variacao > 0 ? '+' : '') + numero(variacao) + '%' : '';
        const textoTendencia = tendencia === 'indeterminada' && variacao !== null && variacao !== 0
            ? (variacao > 0 ? 'Alta a confirmar' : 'Queda a confirmar') : tipos[tendencia] + percentual;
        const avisos = Array.isArray(item.avisos_planejamento) ? item.avisos_planejamento.map(String).filter(Boolean) : [];
        return {
            tendencia, textoTendencia,
            mediaPrevista: numeroValido(item.media_mensal_prevista),
            saldoAntes: numeroValido(item.estoque_previsto_antes_chegada),
            saldoDepois: numeroValido(item.estoque_previsto_apos_chegada),
            metaDepois: numeroValido(item.estoque_alvo_apos_chegada),
            faltaAntes: numeroValido(item.falta_antes_chegada_quantidade),
            reserva: numeroValido(item.reserva_antes_chegada_quantidade),
            riscoRuptura: item.risco_ruptura_antes_chegada === true,
            reservaAtendida: item.reserva_antes_chegada_atendida === true,
            revisar: item.planejamento_requer_revisao === true,
            motivo: String(item.tendencia_motivo || ''),
            chegada: String(item.data_chegada_prevista || '').replace(/^(\d{4})-(\d{2})-(\d{2})$/, '$3/$2/$1'),
            avisos,
        };
    }

    function textosPrevisaoMensal(item) {
        const plano = obterPlanejamentoCompra(item);
        if (!plano) return [numero(item && item.media_mensal)];
        return [numero(item.media_mensal), 'Prev. ' + (plano.mediaPrevista === null ? '—' : numero(plano.mediaPrevista))];
    }

    function renderizarPrevisaoMensal(item) {
        const plano = obterPlanejamentoCompra(item);
        if (!plano) return '';
        const media = plano.mediaPrevista === null ? '—' : numero(plano.mediaPrevista);
        return '<span class="previsao-detalhe" aria-label="Previsão mensal: ' + escaparHtml(media) + '">Prev. ' + escaparHtml(media) + '</span>';
    }

    function renderizarSetaTendenciaCompra(item) {
        const plano = obterPlanejamentoCompra(item);
        if (!plano || !['alta', 'queda'].includes(plano.tendencia)) return '';
        return '<span class="previsao-seta previsao-seta--' + plano.tendencia + '" aria-hidden="true">' + (plano.tendencia === 'alta' ? '↑' : '↓') + '</span>';
    }

    function renderizarTendenciaCompra(item) {
        const plano = obterPlanejamentoCompra(item);
        if (!plano) return '';
        return '<span class="previsao-tendencia previsao-tendencia--' + plano.tendencia + '" title="' + escaparHtml(plano.motivo) + '">' + escaparHtml(plano.textoTendencia) + '</span>';
    }

    function descricaoPlanejamentoCompra(item, manual) {
        const plano = obterPlanejamentoCompra(item);
        if (!plano) return 'Clique para editar a sugestão de compra';
        const partes = ['Entrega em 6 meses; reserva de 1 mês antes; meta de 7 meses no total após receber'];
        if (plano.chegada) partes.push('Chegada prevista: ' + plano.chegada);
        partes.push('Tendência: ' + plano.textoTendencia);
        if (plano.motivo) partes.push(plano.motivo);
        partes.push('A previsão usa meses completos e exclui o mês atual.');
        if (plano.saldoAntes !== null) partes.push('Estoque antes da chegada: ' + numero(plano.saldoAntes) + ' unidades previstas');
        if (plano.reserva !== null) partes.push('Reserva para 1 mês: ' + numero(plano.reserva) + ' unidades');
        if (!plano.reservaAtendida) partes.push('Reserva insuficiente: a sobra prevista antes da chegada é menor que um mês de vendas');
        if (plano.saldoDepois !== null) partes.push('Estoque previsto após receber a sugestão automática: ' + numero(plano.saldoDepois));
        if (plano.metaDepois !== null) partes.push('Meta de estoque para 7 meses após receber: ' + numero(plano.metaDepois));
        if (plano.riscoRuptura) partes.push('Falta antes da entrega: demanda sem cobertura de ' + numero(plano.faltaAntes || 0) + ' unidades');
        if (plano.revisar) partes.push('Revisar previsão: confirme os dados indicados nos avisos antes de comprar');
        partes.push(...plano.avisos);
        if (manual) partes.push('Ajuste manual: as projeções exibidas são do cálculo automático');
        return partes.join('\n');
    }

    function textosPlanejamentoCompra(item) {
        const plano = obterPlanejamentoCompra(item);
        if (!plano) return [numero(item && item.compra_sugerida)];
        return [numero(item.compra_sugerida), plano.textoTendencia];
    }

    function renderizarDetalhesPlanejamento(item, manual) {
        return '';
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

    function numeroCubagemListaPedido(valor) {
        const texto = String(valor ?? '').trim();
        if (!texto) return NaN;
        // Preserve scientific notation for small product volumes.
        if (/^[+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[eE][+-]?\d+)?$/.test(texto)) {
            return Number(texto.replace(',', '.'));
        }
        return toNumeroPrompt(texto);
    }

    function somarCubagensListaPedido(linhas) {
        let total = 0, pendentes = 0;
        for (const linha of linhas) {
            if (toNumeroDecimal(linha.quantidade) <= 0) continue;
            const volume = numeroCubagemListaPedido(linha.cubagem);
            // Cubagem is already the total of the row, not a unit volume.
            if (Number.isFinite(volume) && volume > 0 && Number.isFinite(total + volume)) {
                total += volume;
            } else {
                pendentes += 1;
            }
        }
        return { total, pendentes };
    }

    function formatarResumoCubagemListaPedido(total, pendentes) {
        const parcial = pendentes > 0;
        const valor = total.toLocaleString('pt-BR', { maximumFractionDigits: 12 });
        return {
            texto: valor + ' m³' + (parcial ? ' (parcial)' : ''),
            dados: { totalM3: String(total), partial: String(parcial), pendingCount: String(pendentes) },
            titulo: parcial
                ? 'Soma das cubagens preenchidas; ' + pendentes + ' SKU(s) com quantidade positiva e cubagem pendente.'
                : 'Soma das cubagens de todos os SKUs da lista.',
            rotulo: 'Cubagem total: ' + valor + ' metros cúbicos' +
                (parcial ? ', parcial; ' + pendentes + ' SKUs pendentes' : ', completa'),
        };
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
        obterPlanejamentoCompra,
        textosPrevisaoMensal,
        renderizarPrevisaoMensal,
        renderizarSetaTendenciaCompra,
        renderizarTendenciaCompra,
        descricaoPlanejamentoCompra,
        textosPlanejamentoCompra,
        renderizarDetalhesPlanejamento,
        formatarMoedaUSD,
        parseNumeroFlex,
        toNumeroDecimal,
        numeroCubagemListaPedido,
        somarCubagensListaPedido,
        formatarResumoCubagemListaPedido,
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
