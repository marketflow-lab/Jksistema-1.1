(function (global) {
    'use strict';

    if (global.JKCadastroFotos) return;

    function obterNomeArquivoFoto(valor) {
        const partes = String(valor || '').replace(/\\/g, '/').split('/').filter(Boolean);
        const nome = partes.length ? partes[partes.length - 1] : '';
        return nome && nome !== '.' && nome !== '..' ? nome : '';
    }

    function limparReferenciaFoto(valor) {
        let texto = String(valor || '').trim();
        for (let tentativa = 0; tentativa < 3; tentativa += 1) {
            texto = texto.replace(/^['"`‘’“”]+|['"`‘’“”]+$/g, '').trim().replace(/\\/g, '/');
            let decodificado = texto;
            try {
                decodificado = decodeURIComponent(texto);
            } catch (_error) {
                break;
            }
            if (decodificado === texto) break;
            texto = decodificado;
        }
        return texto.replace(/^['"`‘’“”]+|['"`‘’“”]+$/g, '').trim().replace(/\\/g, '/');
    }

    function fotoReferenciaRotaCadastro(valor) {
        const caminho = String(valor || '').split('?', 1)[0].split('#', 1)[0].replace(/^\/+/, '');
        const partes = [];
        caminho.split('/').forEach(parte => {
            if (!parte || parte === '.') return;
            if (parte === '..') {
                partes.pop();
                return;
            }
            partes.push(parte);
        });
        const normalizado = partes.join('/').toLowerCase();
        return normalizado.startsWith('api/cadastro/foto/')
            || normalizado.startsWith('api/cadastro/foto-arquivo/');
    }

    function fotoReferenciaExterna(valor) {
        const texto = limparReferenciaFoto(valor);
        if (!texto) return false;
        // Mantem o mesmo limite do backend: URLs protocol-relative e esquemas
        // que nao representam o filesystem local nao viram rotas do Cadastro.
        if (/^\/\//.test(texto)) return true;
        if (/^[a-z]:\//i.test(texto) || /^file:/i.test(texto)) return false;
        const esquema = /^([a-z][a-z0-9+.-]*):/i.exec(texto);
        if (!esquema) return false;
        if (!/^https?$/i.test(esquema[1])) return true;
        if (/^https?:\/\//i.test(texto)) {
            const caminhoComHost = texto.replace(/^https?:\/\/[^/]*\/?/i, '/');
            return !fotoReferenciaRotaCadastro(caminhoComHost);
        }
        return !fotoReferenciaRotaCadastro(texto.slice(esquema[0].length));
    }

    function urlFoto(clientId, storeId, valor) {
        const texto = String(valor || '').trim();
        if (!texto || fotoReferenciaExterna(texto)) return texto;
        const caminho = limparReferenciaFoto(texto).split('?', 1)[0].split('#', 1)[0].replace(/^\/+/, '');
        const nome = obterNomeArquivoFoto(caminho);
        if (!clientId || !nome) return '';
        const caminhoEscopado = caminho.replace(/^cadastro_fotos\//i, '');
        const declarada = /^lojas\/([^/]+)\/([^/]+)$/i.exec(caminhoEscopado);
        if (declarada) {
            const storeExata = String(storeId || '').trim();
            const segmento = declarada[1];
            if (!storeExata) return '';
            if (/^sid-[a-f0-9]{64}$/.test(segmento)) {
                return `/api/cadastro/foto/${encodeURIComponent(clientId)}/lojas/${segmento}/${encodeURIComponent(nome)}?store_id=${encodeURIComponent(storeExata)}`;
            }
            // Compatibilidade com registros anteriores ao segmento opaco. O
            // servidor ainda recusa este formato quando dois IDs diferem so
            // por caixa, evitando a colisao de diretorios do Windows.
            if (!/^[A-Za-z0-9_-]{1,128}$/.test(segmento) || segmento !== storeExata) return '';
            return `/api/cadastro/foto/${encodeURIComponent(clientId)}/lojas/${encodeURIComponent(segmento)}/${encodeURIComponent(nome)}`;
        }
        if (/^lojas\//i.test(caminhoEscopado) || /(?:^|\/)cadastro_fotos\/lojas\//i.test(caminho)) return '';
        return `/api/cadastro/foto/${encodeURIComponent(clientId)}/${encodeURIComponent(nome)}`;
    }

    function fotoPodeReceberAuth(url) {
        const texto = String(url || '').trim();
        if (!texto || /^data:/i.test(texto) || /^blob:/i.test(texto)) return false;
        try {
            if (!global.location || !global.location.origin || typeof global.URL !== 'function') return false;
            const resolvida = new global.URL(texto, global.location.origin);
            return resolvida.origin === global.location.origin
                && /^\/api\/cadastro\/(?:foto-arquivo\/|foto\/)/i.test(resolvida.pathname);
        } catch (_error) {
            return false;
        }
    }

    async function carregarFotoAutenticada(url, authHeaders, options) {
        const texto = String(url || '').trim();
        if (!texto) throw new Error('Foto não informada.');
        if (/^data:/i.test(texto)) return { url: texto, revogavel: false };
        if (/^blob:/i.test(texto)) return { url: texto, revogavel: false };
        if (!fotoPodeReceberAuth(texto)) {
            // Imagens externas continuam diretas, sem CORS extra nem credencial do JK Sistema.
            return { url: texto, revogavel: false };
        }
        const headers = typeof authHeaders === 'function' ? authHeaders() : null;
        if (!headers || !Object.keys(headers).length) throw new Error('Autenticação indisponível para carregar a foto.');
        const config = options && typeof options === 'object' ? options : {};
        const response = await global.fetch(texto, { headers, ...(config.signal ? { signal: config.signal } : {}) });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const blob = await response.blob();
        if (!global.URL || typeof global.URL.createObjectURL !== 'function') {
            throw new Error('Prévia de foto indisponível neste navegador.');
        }
        return { url: global.URL.createObjectURL(blob), revogavel: true };
    }
    function revogarFotoCarregada(foto) {
        if (!foto || !foto.revogavel || !foto.url) return;
        if (!global.URL || typeof global.URL.revokeObjectURL !== 'function') return;
        global.URL.revokeObjectURL(foto.url);
    }

    global.JKCadastroFotos = Object.freeze({
        carregarFotoAutenticada,
        fotoPodeReceberAuth,
        revogarFotoCarregada,
        urlFoto,
    });
})(window);
