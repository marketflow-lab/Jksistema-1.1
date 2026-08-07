(function (root, factory) {
    'use strict';
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.PesquisaMercadoCore = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    const ML_ITEM_RE = /\bMLB-?(\d{7,})\b/i;
    const ML_HOST_RE = /(^|\.)mercadolivre\.com\.br$/i;

    function texto(valor) {
        return String(valor == null ? '' : valor).replace(/\s+/g, ' ').trim();
    }

    function normalizarIdentidade(valor) {
        return texto(valor)
            .normalize('NFD')
            .replace(/[\u0300-\u036f]/g, '')
            .replace(/[^a-z0-9]+/gi, '')
            .toLowerCase();
    }

    function extrairMlb(valor) {
        const match = texto(valor).match(ML_ITEM_RE);
        return match ? `MLB${match[1]}` : '';
    }

    function normalizarUrlMercadoLivre(valor, base) {
        const raw = texto(valor);
        if (!raw) return '';
        try {
            const url = new URL(raw, base || 'https://www.mercadolivre.com.br/');
            if (!/^https?:$/i.test(url.protocol) || !ML_HOST_RE.test(url.hostname)) return '';
            url.hash = '';
            return url.href;
        } catch (_err) {
            return '';
        }
    }

    function normalizarEntradaVendedor(valor) {
        const raw = texto(valor);
        if (!raw) throw new Error('Informe o link do perfil, o ID ou o apelido do vendedor.');

        if (/^https?:\/\//i.test(raw)) {
            const url = normalizarUrlMercadoLivre(raw);
            if (!url) throw new Error('Use um link do Mercado Livre Brasil.');
            return url;
        }

        if (/^\d{5,}$/.test(raw)) {
            return `https://lista.mercadolivre.com.br/_CustId_${raw}`;
        }

        const apelido = raw.replace(/^@/, '').trim();
        if (!/^[\p{L}\p{N}._-]{2,80}$/u.test(apelido)) {
            throw new Error('Apelido de vendedor invalido. Cole o link completo do perfil.');
        }
        return `https://www.mercadolivre.com.br/perfil/${encodeURIComponent(apelido)}`;
    }

    function numeroPtBr(valor) {
        let raw = texto(valor).replace(/\s/g, '').replace(/\+/g, '');
        if (!raw) return null;
        raw = raw.replace(/[^\d.,-]/g, '');
        if (!raw || raw === '-') return null;

        const comma = raw.lastIndexOf(',');
        const dot = raw.lastIndexOf('.');
        if (comma >= 0 && dot >= 0) {
            if (comma > dot) raw = raw.replace(/\./g, '').replace(',', '.');
            else raw = raw.replace(/,/g, '');
        } else if (comma >= 0) {
            const decimals = raw.length - comma - 1;
            raw = decimals > 0 && decimals <= 2 ? raw.replace(',', '.') : raw.replace(/,/g, '');
        } else if (dot >= 0) {
            const partes = raw.split('.');
            if (partes.length > 2 || (partes.length === 2 && partes[1].length === 3)) raw = partes.join('');
        }

        const parsed = Number(raw);
        return Number.isFinite(parsed) ? parsed : null;
    }

    function normalizarNumeroVendas(valor) {
        if (valor == null || valor === '') return null;
        if (typeof valor === 'number') return Number.isFinite(valor) && valor >= 0 ? Math.round(valor) : null;

        const raw = texto(valor).toLowerCase();
        if (!raw || /nao\s+informad|n[aã]o\s+encontrad|indispon[ií]vel/.test(raw)) return null;
        const match = raw.match(/(?:\+\s*)?(\d[\d.,]*)(?:\s*)(mil|k|mi|milh(?:ao|oes|ão|ões))?/i);
        if (!match) return null;
        const base = numeroPtBr(match[1]);
        if (!Number.isFinite(base) || base < 0) return null;

        const sufixo = texto(match[2]).toLowerCase();
        const multiplicador = /^(mil|k)$/.test(sufixo)
            ? 1000
            : /^(mi|milh)/.test(sufixo) ? 1000000 : 1;
        return Math.max(0, Math.round(base * multiplicador));
    }

    function identidadePerfil(url) {
        const normalizada = normalizarUrlMercadoLivre(url);
        if (!normalizada) return '';
        try {
            const parsed = new URL(normalizada);
            const cust = parsed.pathname.match(/_CustId_(\d+)/i) || parsed.search.match(/[?&](?:seller_id|custid)=(\d+)/i);
            if (cust) return `id:${cust[1]}`;
            const perfil = parsed.pathname.match(/\/perfil\/([^/?#]+)/i);
            if (perfil) return `nick:${normalizarIdentidade(decodeURIComponent(perfil[1]))}`;
        } catch (_err) {}
        return '';
    }

    function vendedorConfere(alvo, detalhe) {
        const alvoUrl = identidadePerfil(alvo && (alvo.perfilUrl || alvo.url));
        const detalheUrl = identidadePerfil(detalhe && detalhe.perfilUrl);
        if (alvoUrl && detalheUrl && alvoUrl.split(':')[0] === detalheUrl.split(':')[0]) return alvoUrl === detalheUrl;

        const alvoNome = normalizarIdentidade(alvo && (alvo.vendedor || alvo.nome));
        const detalheNome = normalizarIdentidade(detalhe && detalhe.vendedor);
        if (alvoNome && detalheNome) return alvoNome === detalheNome;
        return null;
    }

    function normalizarAnuncio(item) {
        const origem = item && typeof item === 'object' ? item : {};
        const url = normalizarUrlMercadoLivre(origem.url || origem.permalink || origem.link);
        const id = extrairMlb(origem.id || origem.itemId || origem.mlb || url);
        const vendas = normalizarNumeroVendas(origem.vendas != null ? origem.vendas : origem.soldQuantity);
        return {
            id,
            url,
            titulo: texto(origem.titulo || origem.title),
            preco: texto(origem.preco || origem.price),
            imagem: texto(origem.imagem || origem.image),
            vendas,
            vendasFonte: texto(origem.vendasFonte || origem.salesSource || (vendas != null ? 'card_perfil' : '')),
            vendedor: texto(origem.vendedor || origem.sellerName),
            perfilUrl: normalizarUrlMercadoLivre(origem.perfilUrl || origem.sellerUrl),
            status: texto(origem.status),
            erro: texto(origem.erro || origem.error)
        };
    }

    function chaveAnuncio(item, indice) {
        if (item.id) return item.id;
        if (item.url) {
            try {
                const url = new URL(item.url);
                url.search = '';
                url.hash = '';
                return url.href.toLowerCase();
            } catch (_err) {}
        }
        return `sem-chave-${indice}`;
    }

    function consolidarAnuncios(itens) {
        const mapa = new Map();
        (Array.isArray(itens) ? itens : []).forEach((raw, indice) => {
            const item = normalizarAnuncio(raw);
            if (!item.url && !item.id) return;
            const chave = chaveAnuncio(item, indice);
            const atual = mapa.get(chave);
            if (!atual) {
                mapa.set(chave, item);
                return;
            }
            const vendas = item.vendas != null && (atual.vendas == null || item.vendas > atual.vendas) ? item.vendas : atual.vendas;
            mapa.set(chave, {
                ...atual,
                ...item,
                id: atual.id || item.id,
                url: atual.url || item.url,
                titulo: item.titulo || atual.titulo,
                preco: item.preco || atual.preco,
                imagem: item.imagem || atual.imagem,
                vendas,
                vendasFonte: vendas === item.vendas && item.vendasFonte ? item.vendasFonte : atual.vendasFonte,
                vendedor: item.vendedor || atual.vendedor,
                perfilUrl: item.perfilUrl || atual.perfilUrl,
                erro: item.erro || atual.erro
            });
        });
        return Array.from(mapa.values());
    }

    function aplicarDetalhe(anuncioRaw, detalheRaw, vendedorAlvo) {
        const anuncio = normalizarAnuncio(anuncioRaw);
        const detalhe = normalizarAnuncio(detalheRaw);
        const confere = vendedorConfere(vendedorAlvo || anuncio, detalhe);
        const divergente = confere === false;
        const detalheTemVendas = detalhe.vendas != null && !divergente;
        const vendas = detalheTemVendas ? detalhe.vendas : anuncio.vendas;
        return {
            ...anuncio,
            id: anuncio.id || detalhe.id,
            url: anuncio.url || detalhe.url,
            titulo: detalhe.titulo || anuncio.titulo,
            preco: detalhe.preco || anuncio.preco,
            imagem: detalhe.imagem || anuncio.imagem,
            vendas,
            vendasFonte: detalheTemVendas ? (detalhe.vendasFonte || 'pagina_anuncio') : anuncio.vendasFonte,
            vendedor: detalhe.vendedor || anuncio.vendedor,
            perfilUrl: detalhe.perfilUrl || anuncio.perfilUrl,
            status: divergente ? 'vendedor_divergente' : (vendas == null ? 'sem_vendas' : 'analisado'),
            erro: divergente
                ? 'A pagina abriu com outro vendedor; a venda exibida nela nao foi atribuida ao perfil pesquisado.'
                : (detalhe.erro || anuncio.erro)
        };
    }

    function filtrarMaisVendidos(itens, opcoes) {
        const cfg = opcoes && typeof opcoes === 'object' ? opcoes : {};
        const minimo = Math.max(0, normalizarNumeroVendas(cfg.minimo) || 0);
        const limiteRaw = Number(cfg.limite);
        const limite = Number.isFinite(limiteRaw) && limiteRaw > 0 ? Math.floor(limiteRaw) : 0;
        const busca = normalizarIdentidade(cfg.busca || '');

        const ordenados = consolidarAnuncios(itens)
            .filter(item => item.vendas != null && item.vendas >= minimo)
            .filter(item => !busca || normalizarIdentidade(`${item.id} ${item.titulo} ${item.vendedor}`).includes(busca))
            .sort((a, b) => {
                if (b.vendas !== a.vendas) return b.vendas - a.vendas;
                return a.titulo.localeCompare(b.titulo, 'pt-BR');
            });
        return limite ? ordenados.slice(0, limite) : ordenados;
    }

    return {
        aplicarDetalhe,
        consolidarAnuncios,
        extrairMlb,
        filtrarMaisVendidos,
        identidadePerfil,
        normalizarAnuncio,
        normalizarEntradaVendedor,
        normalizarIdentidade,
        normalizarNumeroVendas,
        normalizarUrlMercadoLivre,
        texto,
        vendedorConfere
    };
});
