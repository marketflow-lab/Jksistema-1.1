        function normalizarNomeVendedorParaBusca(value) {
            return normalizarNomeVendedor(value)
                .toLowerCase()
                .replace(/[^a-z0-9]+/g, ' ')
                .replace(/\s+/g, ' ')
                .trim();
        }

        function vendedorValido(valor) {
            const texto = normalizarNomeVendedor(valor);
            if (!texto) return false;
            if (texto.length < 2 || texto.length > 120) return false;
            if (!/[A-Za-z0-9]/.test(texto)) return false;
            if (/^\d+$/.test(texto)) return false;

            const textoBusca = normalizarNomeVendedorParaBusca(texto);
            if (!textoBusca) return false;
            if (textoBusca.length < 2) return false;
            if (/(^|\b)(anuncio criado|an ncio criado|criado em|catalogo criado|cat logo criado|vendas produto|total vendas|quantidade vendas)(\b|$)/i.test(textoBusca)) return false;
            if (/^(vendido|vendedor|anuncio|anunc[ií]o|produto|frete|envio|loja|oferta|ofertas|desconto|comprar|comprando|login|entrar|cadastro|email|senha|contato|perfil|busca|filtro|categoria|condi[cç][aã]o|aviso|informa[cç][aã]o|cria[cç][aã]o|valor|pre[cç]o)$/i.test(textoBusca)) return false;
            return true;
        }

        function scoreNomeVendedor(valor) {
            if (!vendedorValido(valor)) return -1;
            const normalizado = normalizarNomeVendedorParaBusca(valor);
            let score = normalizado.length;
            if (/\s/.test(normalizado)) score += 6;
            if (normalizado.length > 20) score += 5;
            return score;
        }

        function escolherNomeVendedor(candidatos) {
            const itens = Array.isArray(candidatos) ? candidatos : [];
            const opcoes = [];

            for (let i = 0; i < itens.length; i += 1) {
                const item = itens[i];
                const valor = typeof item === 'string' ? item : item && item.valor;
                const prioridade = Number(item && item.prioridade) || 0;
                const nome = normalizarNomeVendedor(valor);

                if (!nome || !vendedorValido(nome)) continue;
                opcoes.push({
                    nome,
                    prioridade,
                    score: scoreNomeVendedor(nome),
                    ordem: i
                });
            }

            if (!opcoes.length) return '';
            opcoes.sort((a, b) => b.prioridade - a.prioridade || b.score - a.score || a.ordem - b.ordem);
            return opcoes[0].nome;
        }

        function carregarVendedoresIgnoradosRanking() {
            try {
                const bruto = window.localStorage ? window.localStorage.getItem(ML_VENDEDORES_IGNORADOS_RANKING_KEY) : null;
                const lista = bruto ? JSON.parse(bruto) : [];
                return normalizarListaVendedoresIgnoradosRanking(lista);
            } catch (err) {
                console.warn('Não foi possível carregar vendedores ignorados do ranking:', err);
                return [];
            }
        }

        function obterVendedoresIgnoradosRanking() {
            if (!Array.isArray(mlVendedoresIgnoradosRanking)) {
                mlVendedoresIgnoradosRanking = carregarVendedoresIgnoradosRanking();
            }
            return mlVendedoresIgnoradosRanking;
        }

        function normalizarListaVendedoresIgnoradosRanking(lista) {
            const mapa = new Map();
            (Array.isArray(lista) ? lista : []).forEach(valor => {
                const nome = normalizarNomeVendedor(valor);
                const chave = normalizarNomeVendedorParaBusca(nome);
                if (nome && vendedorValido(nome) && chave && !mapa.has(chave)) {
                    mapa.set(chave, nome);
                }
            });
            return Array.from(mapa.values()).sort((a, b) => a.localeCompare(b, 'pt-BR'));
        }

        function mesclarListasVendedoresIgnoradosRanking(...listas) {
            return normalizarListaVendedoresIgnoradosRanking(
                listas.flatMap(lista => Array.isArray(lista) ? lista : [])
            );
        }

        function salvarVendedoresIgnoradosRankingLocal() {
            try {
                if (window.localStorage) {
                    window.localStorage.setItem(
                        ML_VENDEDORES_IGNORADOS_RANKING_KEY,
                        JSON.stringify(obterVendedoresIgnoradosRanking())
                    );
                }
            } catch (err) {
                console.warn('Não foi possível salvar vendedores ignorados do ranking:', err);
            }
        }

        function salvarVendedoresIgnoradosRanking() {
            mlVendedoresIgnoradosRanking = normalizarListaVendedoresIgnoradosRanking(obterVendedoresIgnoradosRanking());
            salvarVendedoresIgnoradosRankingLocal();
            agendarSalvarVendedoresIgnoradosRankingServidor();
        }

        function aplicarVendedoresIgnoradosRanking(lista) {
            mlVendedoresIgnoradosRanking = normalizarListaVendedoresIgnoradosRanking(lista);
            salvarVendedoresIgnoradosRankingLocal();
            renderizarVendedoresIgnoradosRanking();
            atualizarBotoesVendedoresRanking();
            atualizarRankingMediaVendas();
            renderizarFavoritosOutrosAnuncios(favMlSkuSelecionado);
            renderizarHistoricoFavoritos();
        }

        async function carregarVendedoresIgnoradosRankingServidor() {
            try {
                const response = await fetch('/api/favoritos/preferencias-vendedores-ignorados', {
                    headers: obterAuthHeaders()
                });
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                const data = await response.json();
                const listaServidor = normalizarListaVendedoresIgnoradosRanking(data.vendedores_ignorados || []);
                const listaLocal = normalizarListaVendedoresIgnoradosRanking(obterVendedoresIgnoradosRanking());
                const combinada = mesclarListasVendedoresIgnoradosRanking(listaServidor, listaLocal);
                mlVendedoresIgnoradosServidorCarregado = true;
                aplicarVendedoresIgnoradosRanking(combinada);
                if (JSON.stringify(combinada) !== JSON.stringify(listaServidor)) {
                    await salvarVendedoresIgnoradosRankingServidor();
                }
            } catch (err) {
                console.warn('Não foi possível sincronizar vendedores ignorados com o servidor:', err);
                mlVendedoresIgnoradosServidorCarregado = false;
                renderizarVendedoresIgnoradosRanking();
            }
        }

        function agendarSalvarVendedoresIgnoradosRankingServidor() {
            if (mlVendedoresIgnoradosSaveTimer) {
                clearTimeout(mlVendedoresIgnoradosSaveTimer);
            }
            mlVendedoresIgnoradosSaveTimer = setTimeout(() => {
                mlVendedoresIgnoradosSaveTimer = null;
                salvarVendedoresIgnoradosRankingServidor().catch(err => {
                    console.warn('Não foi possível salvar vendedores ignorados no servidor:', err);
                });
            }, mlVendedoresIgnoradosServidorCarregado ? 250 : 800);
        }

        async function salvarVendedoresIgnoradosRankingServidor() {
            const response = await fetch('/api/favoritos/preferencias-vendedores-ignorados', {
                method: 'PUT',
                headers: headersJsonAutenticado(),
                body: JSON.stringify({
                    vendedores_ignorados: obterVendedoresIgnoradosRanking()
                })
            });
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            const data = await response.json();
            mlVendedoresIgnoradosServidorCarregado = true;
            aplicarVendedoresIgnoradosRanking(data.vendedores_ignorados || obterVendedoresIgnoradosRanking());
            return data;
        }

        function vendedorIgnoradoNoRanking(vendedor) {
            const chave = normalizarNomeVendedorParaBusca(vendedor);
            if (!chave) return false;
            return obterVendedoresIgnoradosRanking()
                .some(nome => normalizarNomeVendedorParaBusca(nome) === chave);
        }

        function filtrarAnunciosIgnoradosRanking(anuncios, sku = '') {
            return (Array.isArray(anuncios) ? anuncios : [])
                .filter(anuncio => !vendedorIgnoradoNoRanking(anuncio && anuncio.vendedor))
                .filter(anuncio => !anuncioIgnoradoNoSku(sku, anuncio));
        }

        function chaveUnicaAnuncioIgnoradoSku(item) {
            const chaves = Array.isArray(item && item.chaves) ? item.chaves : [];
            return chaves.find(Boolean) || String(item && (item.id || item.url || item.titulo) || '').trim();
        }

        function normalizarAnuncioIgnoradoSkuItem(item, skuFallback = '') {
            if (!item || typeof item !== 'object') return null;
            const sku = String(item.sku || skuFallback || '').trim();
            const chaveSku = skuChaveSku(sku);
            if (!chaveSku) return null;
            const id = normalizarIdAnuncioFavoritosIa(item) || obterIdAnuncioFavoritos(item);
            const chaves = [];
            if (Array.isArray(item.chaves)) {
                item.chaves.forEach(chave => {
                    const texto = String(chave || '').trim();
                    if (texto && !chaves.includes(texto)) chaves.push(texto);
                });
            }
            chavesRemocaoAnuncioRankingFavoritos(item).forEach(chave => {
                if (chave && !chaves.includes(chave)) chaves.push(chave);
            });
            if (id && !chaves.some(chave => String(chave).toLowerCase() === `id:${id}`.toLowerCase())) {
                chaves.unshift(`id:${id}`);
            }
            if (!chaves.length) return null;
            const precos = obterPrecosAnuncioFavoritos(item);
            return {
                sku: chaveSku,
                id,
                url: item.url || item.permalink || item.link || '',
                titulo: String(item.titulo || item.title || '').trim(),
                vendedor: normalizarNomeVendedor(item.vendedor || item.seller || ''),
                imagem: obterImagemAnuncioFavoritos(item),
                preco: precos.preco,
                preco_promocional: precos.promocional,
                chaves,
                loja: String(item.loja || favMlLojaSelecionada || favoritosLojaSelecionadaParaApi() || '').trim(),
                ignorado_em: item.ignorado_em || item.data_iso || new Date().toISOString()
            };
        }

        function normalizarMapaAnunciosIgnoradosSku(valor) {
            const mapa = {};
            const origem = valor && typeof valor === 'object' && !Array.isArray(valor) ? valor : {};
            Object.entries(origem).forEach(([sku, itens]) => {
                const chaveSku = skuChaveSku(sku);
                if (!chaveSku || !Array.isArray(itens)) return;
                const vistos = new Set();
                itens.forEach(item => {
                    const normalizado = normalizarAnuncioIgnoradoSkuItem(item, chaveSku);
                    const chave = chaveUnicaAnuncioIgnoradoSku(normalizado);
                    if (!normalizado || !chave || vistos.has(chave)) return;
                    vistos.add(chave);
                    if (!mapa[chaveSku]) mapa[chaveSku] = [];
                    if (mapa[chaveSku].length < 500) mapa[chaveSku].push(normalizado);
                });
            });
            return mapa;
        }

        function mesclarMapasAnunciosIgnoradosSku(...mapas) {
            const combinado = {};
            mapas.forEach(mapa => {
                const normalizado = normalizarMapaAnunciosIgnoradosSku(mapa);
                Object.entries(normalizado).forEach(([sku, itens]) => {
                    if (!combinado[sku]) combinado[sku] = [];
                    const vistos = new Set(combinado[sku].map(chaveUnicaAnuncioIgnoradoSku));
                    itens.forEach(item => {
                        const chave = chaveUnicaAnuncioIgnoradoSku(item);
                        if (!chave || vistos.has(chave)) return;
                        vistos.add(chave);
                        combinado[sku].push(item);
                    });
                });
            });
            return normalizarMapaAnunciosIgnoradosSku(combinado);
        }

        function carregarAnunciosIgnoradosSkuLocal() {
            try {
                const bruto = window.localStorage ? window.localStorage.getItem(ML_ANUNCIOS_IGNORADOS_SKU_KEY) : null;
                return normalizarMapaAnunciosIgnoradosSku(bruto ? JSON.parse(bruto) : {});
            } catch (err) {
                console.warn('Nao foi possivel carregar anuncios ignorados por SKU:', err);
                return {};
            }
        }

        function obterAnunciosIgnoradosSku() {
            if (!mlAnunciosIgnoradosSku || typeof mlAnunciosIgnoradosSku !== 'object') {
                mlAnunciosIgnoradosSku = carregarAnunciosIgnoradosSkuLocal();
            }
            return mlAnunciosIgnoradosSku;
        }

        function salvarAnunciosIgnoradosSkuLocal() {
            try {
                if (window.localStorage) {
                    window.localStorage.setItem(ML_ANUNCIOS_IGNORADOS_SKU_KEY, JSON.stringify(obterAnunciosIgnoradosSku()));
                }
            } catch (err) {
                console.warn('Nao foi possivel salvar anuncios ignorados por SKU:', err);
            }
        }

        function agendarSalvarAnunciosIgnoradosSkuServidor() {
            if (mlAnunciosIgnoradosSaveTimer) clearTimeout(mlAnunciosIgnoradosSaveTimer);
            mlAnunciosIgnoradosSaveTimer = setTimeout(() => {
                mlAnunciosIgnoradosSaveTimer = null;
                salvarAnunciosIgnoradosSkuServidor().catch(err => {
                    console.warn('Nao foi possivel salvar anuncios ignorados por SKU no servidor:', err);
                });
            }, mlAnunciosIgnoradosServidorCarregado ? 250 : 800);
        }

        function salvarAnunciosIgnoradosSku() {
            mlAnunciosIgnoradosSku = normalizarMapaAnunciosIgnoradosSku(obterAnunciosIgnoradosSku());
            salvarAnunciosIgnoradosSkuLocal();
            agendarSalvarAnunciosIgnoradosSkuServidor();
        }

        function aplicarAnunciosIgnoradosSku(mapa) {
            mlAnunciosIgnoradosSku = normalizarMapaAnunciosIgnoradosSku(mapa);
            salvarAnunciosIgnoradosSkuLocal();
            renderizarAnunciosIgnoradosSku();
            renderizarFavoritosOutrosAnuncios(favMlSkuSelecionado);
            renderizarHistoricoFavoritos();
            if (Array.isArray(favMlAnunciosSkuAtual)) {
                renderizarFavoritosAnunciosMl(favMlAnunciosSkuAtual, favMlSkuSelecionado);
            }
        }

        async function carregarAnunciosIgnoradosSkuServidor() {
            try {
                const response = await fetch('/api/favoritos/preferencias-anuncios-ignorados', {
                    headers: obterAuthHeaders()
                });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                const data = await response.json();
                const servidor = normalizarMapaAnunciosIgnoradosSku(data.anuncios_ignorados || {});
                const local = normalizarMapaAnunciosIgnoradosSku(obterAnunciosIgnoradosSku());
                const combinado = mesclarMapasAnunciosIgnoradosSku(servidor, local);
                mlAnunciosIgnoradosServidorCarregado = true;
                aplicarAnunciosIgnoradosSku(combinado);
                if (JSON.stringify(combinado) !== JSON.stringify(servidor)) {
                    await salvarAnunciosIgnoradosSkuServidor();
                }
            } catch (err) {
                console.warn('Nao foi possivel sincronizar anuncios ignorados por SKU:', err);
                mlAnunciosIgnoradosServidorCarregado = false;
                renderizarAnunciosIgnoradosSku();
            }
        }

        async function salvarAnunciosIgnoradosSkuServidor() {
            const response = await fetch('/api/favoritos/preferencias-anuncios-ignorados', {
                method: 'PUT',
                headers: headersJsonAutenticado(),
                body: JSON.stringify({ anuncios_ignorados: obterAnunciosIgnoradosSku() })
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            mlAnunciosIgnoradosServidorCarregado = true;
            mlAnunciosIgnoradosSku = normalizarMapaAnunciosIgnoradosSku(data.anuncios_ignorados || obterAnunciosIgnoradosSku());
            salvarAnunciosIgnoradosSkuLocal();
            renderizarAnunciosIgnoradosSku();
            return data;
        }

        function anuncioIgnoradoNoSku(sku, anuncio) {
            const chaveSku = skuChaveSku(sku);
            if (!chaveSku || !anuncio) return false;
            const lista = obterAnunciosIgnoradosSku()[chaveSku] || [];
            if (!lista.length) return false;
            const chavesAnuncio = new Set(chavesRemocaoAnuncioRankingFavoritos(anuncio));
            return lista.some(item => (item.chaves || []).some(chave => chavesAnuncio.has(chave)));
        }

        function adicionarAnuncioIgnoradoSku(sku, anuncio) {
            const item = normalizarAnuncioIgnoradoSkuItem({ ...(anuncio || {}), sku }, sku);
            if (!item) return false;
            const mapa = obterAnunciosIgnoradosSku();
            const chaveSku = skuChaveSku(sku);
            if (!mapa[chaveSku]) mapa[chaveSku] = [];
            const chave = chaveUnicaAnuncioIgnoradoSku(item);
            if (!mapa[chaveSku].some(existente => chaveUnicaAnuncioIgnoradoSku(existente) === chave)) {
                mapa[chaveSku].unshift(item);
                mapa[chaveSku] = mapa[chaveSku].slice(0, 500);
                salvarAnunciosIgnoradosSku();
                renderizarAnunciosIgnoradosSku();
                return true;
            }
            return false;
        }

        function removerAnuncioIgnoradoSku(sku, item) {
            const chaveSku = skuChaveSku(sku);
            if (!chaveSku) return;
            const alvo = normalizarAnuncioIgnoradoSkuItem(item, chaveSku);
            const chaveAlvo = chaveUnicaAnuncioIgnoradoSku(alvo);
            const mapa = obterAnunciosIgnoradosSku();
            mapa[chaveSku] = (mapa[chaveSku] || []).filter(atual => chaveUnicaAnuncioIgnoradoSku(atual) !== chaveAlvo);
            if (!mapa[chaveSku].length) delete mapa[chaveSku];
            salvarAnunciosIgnoradosSku();
            aplicarAnunciosIgnoradosSku(mapa);
            if (favMlStatusEl && skuChaveSku(favMlSkuSelecionado) === chaveSku) {
                favMlStatusEl.textContent = `Anuncio restaurado para o ranking do SKU ${sku}.`;
            }
        }

        function obterUrlAnuncioIgnoradoSku(item) {
            const url = String(item && (item.url || item.permalink || item.link) || '').trim();
            if (/^https?:\/\//i.test(url)) return url;
            const id = String(item && item.id || '').trim().toUpperCase().replace(/[^A-Z0-9]/g, '');
            const match = id.match(/^MLB(\d+)$/);
            if (match) return `https://produto.mercadolivre.com.br/MLB-${match[1]}`;
            return '';
        }

        function alternarGrupoAnunciosIgnoradosSku(sku) {
            const chaveSku = skuChaveSku(sku) || String(sku || '').trim();
            if (!chaveSku) return;
            if (mlAnunciosIgnoradosSkuExpandidos.has(chaveSku)) {
                mlAnunciosIgnoradosSkuExpandidos.delete(chaveSku);
            } else {
                mlAnunciosIgnoradosSkuExpandidos.add(chaveSku);
            }
            renderizarAnunciosIgnoradosSku();
        }

        function voltarAnunciosIgnoradosSkuGeral() {
            if (!mlAnunciosIgnoradosSkuExpandidos.size) return;
            mlAnunciosIgnoradosSkuExpandidos.clear();
            renderizarAnunciosIgnoradosSku();
        }

        function atualizarBotaoVoltarAnunciosIgnoradosSku() {
            const voltarBtn = document.getElementById('ml-anuncios-ignorados-voltar-geral');
            if (!voltarBtn) return;
            if (!voltarBtn.dataset.boundVoltarGeral) {
                voltarBtn.dataset.boundVoltarGeral = '1';
                voltarBtn.addEventListener('click', voltarAnunciosIgnoradosSkuGeral);
            }
            voltarBtn.hidden = mlAnunciosIgnoradosSkuExpandidos.size === 0;
        }

        function renderizarAnunciosIgnoradosSku() {
            if (!mlAnunciosIgnoradosListEl || !mlAnunciosIgnoradosEmptyEl) return;
            const mapa = obterAnunciosIgnoradosSku();
            const entradas = Object.entries(mapa)
                .map(([sku, itens]) => [sku, Array.isArray(itens) ? itens : []])
                .filter(([, itens]) => itens.length)
                .sort((a, b) => a[0].localeCompare(b[0], 'pt-BR'));
            const total = entradas.reduce((acc, [, itens]) => acc + itens.length, 0);
            const chavesAtuais = new Set(entradas
                .map(([sku]) => skuChaveSku(sku) || String(sku || '').trim())
                .filter(Boolean));
            Array.from(mlAnunciosIgnoradosSkuExpandidos).forEach(chave => {
                if (!chavesAtuais.has(chave)) mlAnunciosIgnoradosSkuExpandidos.delete(chave);
            });
            atualizarBotaoVoltarAnunciosIgnoradosSku();
            if (mlAnunciosIgnoradosStatusEl) {
                mlAnunciosIgnoradosStatusEl.textContent = total ? `${total} anuncio(s) ignorado(s) em ${entradas.length} SKU(s).` : '';
            }
            mlAnunciosIgnoradosEmptyEl.classList.toggle('hidden', total > 0);
            mlAnunciosIgnoradosListEl.innerHTML = '';
            entradas.forEach(([sku, itens]) => {
                const chaveSku = skuChaveSku(sku) || String(sku || '').trim();
                const expandido = mlAnunciosIgnoradosSkuExpandidos.has(chaveSku);
                const grupo = document.createElement('section');
                grupo.className = `ml-ignored-sku-group${expandido ? ' is-expanded' : ''}`;
                const head = document.createElement('button');
                head.type = 'button';
                head.className = 'ml-ignored-sku-head';
                head.setAttribute('aria-expanded', expandido ? 'true' : 'false');
                head.addEventListener('click', () => alternarGrupoAnunciosIgnoradosSku(sku));
                const titulo = document.createElement('span');
                titulo.className = 'ml-ignored-sku-title';
                titulo.textContent = `SKU ${sku}`;
                const lateral = document.createElement('span');
                lateral.className = 'ml-ignored-sku-head-side';
                const meta = document.createElement('span');
                meta.className = 'ml-ignored-page-meta';
                meta.textContent = `${itens.length} anuncio(s) fora do ranking desse SKU`;
                const indicador = document.createElement('span');
                indicador.className = 'ml-ignored-sku-toggle';
                indicador.setAttribute('aria-hidden', 'true');
                indicador.textContent = expandido ? '-' : '+';
                head.appendChild(titulo);
                lateral.appendChild(meta);
                lateral.appendChild(indicador);
                head.appendChild(lateral);
                grupo.appendChild(head);

                if (expandido) {
                    const lista = document.createElement('div');
                    lista.className = 'ml-ignored-ad-list';
                    itens.forEach(item => {
                        const row = document.createElement('div');
                        row.className = 'ml-ignored-ad-item';
                        const urlAnuncio = obterUrlAnuncioIgnoradoSku(item);
                        const thumb = document.createElement(urlAnuncio ? 'button' : 'div');
                        thumb.className = `ml-ignored-ad-thumb${urlAnuncio ? ' ml-ignored-ad-thumb-button' : ''}`;
                        if (urlAnuncio) {
                            thumb.type = 'button';
                            thumb.title = 'Abrir anuncio no Google Chrome';
                            thumb.setAttribute('aria-label', `Abrir anuncio ${item.id || item.titulo || ''} no Google Chrome`.trim());
                            thumb.addEventListener('click', (event) => abrirAnuncioNoChromeExterno(urlAnuncio, event));
                        }
                        if (item.imagem) {
                            const img = document.createElement('img');
                            img.src = item.imagem;
                            img.alt = item.titulo || item.id || 'Anuncio ignorado';
                            thumb.appendChild(img);
                        } else {
                            thumb.textContent = item.id ? item.id.slice(-4) : '-';
                        }
                        const info = document.createElement('div');
                        const nome = document.createElement('div');
                        nome.className = 'ml-ignored-ad-title';
                        nome.textContent = item.titulo || item.id || 'Anuncio ignorado';
                        const detalhes = document.createElement('div');
                        detalhes.className = 'ml-ignored-ad-meta';
                        const partes = [];
                        if (item.id) partes.push(item.id);
                        if (item.vendedor) partes.push(item.vendedor);
                        if (item.ignorado_em) partes.push(`Ignorado em ${formatarDataHistoricoFavoritos(item.ignorado_em) || item.ignorado_em}`);
                        detalhes.textContent = partes.join(' | ');
                        info.appendChild(nome);
                        info.appendChild(detalhes);
                        const actions = document.createElement('div');
                        actions.className = 'ml-ignored-page-actions';
                        const restaurar = document.createElement('button');
                        restaurar.type = 'button';
                        restaurar.className = 'btn-back';
                        restaurar.textContent = 'Restaurar';
                        restaurar.addEventListener('click', () => removerAnuncioIgnoradoSku(sku, item));
                        actions.appendChild(restaurar);
                        row.appendChild(thumb);
                        row.appendChild(info);
                        row.appendChild(actions);
                        lista.appendChild(row);
                    });
                    grupo.appendChild(lista);
                }
                mlAnunciosIgnoradosListEl.appendChild(grupo);
            });
        }

        function renderizarVendedoresIgnoradosRanking() {
            const vendedores = obterVendedoresIgnoradosRanking();
            if (mlIgnoredSellersListEl && mlIgnoredSellersEmptyEl) {
                mlIgnoredSellersListEl.innerHTML = '';
                mlIgnoredSellersEmptyEl.classList.toggle('hidden', vendedores.length > 0);

                vendedores.forEach(vendedor => {
                    const chip = document.createElement('span');
                    chip.className = 'ml-ignored-chip';

                    const nome = document.createElement('span');
                    nome.textContent = vendedor;

                    const remover = document.createElement('button');
                    remover.type = 'button';
                    remover.title = `Remover ${vendedor} da lista de ignorados`;
                    remover.textContent = 'x';
                    remover.addEventListener('click', () => removerVendedorIgnoradoRanking(vendedor));

                    chip.appendChild(nome);
                    chip.appendChild(remover);
                    mlIgnoredSellersListEl.appendChild(chip);
                });
            }
            if (mlVendedoresIgnoradosStatusEl) {
                mlVendedoresIgnoradosStatusEl.textContent = vendedores.length
                    ? `${vendedores.length} vendedor(es) fora do ranking.`
                    : '';
            }
            if (mlVendedoresIgnoradosEmptyEl) {
                mlVendedoresIgnoradosEmptyEl.classList.toggle('hidden', vendedores.length > 0);
            }
            if (mlVendedoresIgnoradosListEl) {
                mlVendedoresIgnoradosListEl.innerHTML = '';
                vendedores.forEach(vendedor => {
                    const item = document.createElement('div');
                    item.className = 'ml-ignored-page-item';

                    const info = document.createElement('div');
                    const nome = document.createElement('div');
                    nome.className = 'ml-ignored-page-name';
                    nome.textContent = vendedor;
                    const meta = document.createElement('div');
                    meta.className = 'ml-ignored-page-meta';
                    meta.textContent = 'Este vendedor nao entra nos rankings e no historico filtrado.';
                    info.appendChild(nome);
                    info.appendChild(meta);

                    item.appendChild(info);
                    mlVendedoresIgnoradosListEl.appendChild(item);
                });
            }
        }

        function atualizarBotoesVendedoresRanking() {
            (mlAnunciosPrimeiraPaginaAtuais || []).forEach(anuncio => {
                const row = selecionarLinhaAnuncio(anuncio);
                const cell = row ? row.querySelector('.ml-vendedor') : null;
                if (cell) renderizarCelulaVendedor(cell, anuncio.vendedor || '');
            });
        }

        function adicionarVendedorIgnoradoRanking(vendedor) {
            const nome = normalizarNomeVendedor(vendedor);
            if (!vendedorValido(nome) || vendedorIgnoradoNoRanking(nome)) return;
            obterVendedoresIgnoradosRanking().push(nome);
            mlVendedoresIgnoradosRanking.sort((a, b) => a.localeCompare(b, 'pt-BR'));
            salvarVendedoresIgnoradosRanking();
            renderizarVendedoresIgnoradosRanking();
            atualizarBotoesVendedoresRanking();
            atualizarRankingMediaVendas();
            renderizarFavoritosOutrosAnuncios(favMlSkuSelecionado);
            renderizarHistoricoFavoritos();
            if (mlPrimeiraPaginaStatusEl) {
                mlPrimeiraPaginaStatusEl.textContent = `Vendedor "${nome}" removido do ranking e salvo na conta do usuário. Os anúncios dele continuam na tabela.`;
            }
            if (favMlStatusEl) {
                favMlStatusEl.textContent = `Vendedor "${nome}" ignorado e salvo na conta do usuario. Os anuncios dele nao entram mais no ranking.`;
            }
            if (mlHistoricoFavoritosStatusEl && document.getElementById('aba-historico')?.classList.contains('active')) {
                mlHistoricoFavoritosStatusEl.textContent = `Vendedor "${nome}" ignorado. O historico agora oculta os anuncios dele.`;
            }
        }

        function removerVendedorIgnoradoRanking(vendedor) {
            const chave = normalizarNomeVendedorParaBusca(vendedor);
            mlVendedoresIgnoradosRanking = obterVendedoresIgnoradosRanking()
                .filter(nome => normalizarNomeVendedorParaBusca(nome) !== chave);
            salvarVendedoresIgnoradosRanking();
            renderizarVendedoresIgnoradosRanking();
            atualizarBotoesVendedoresRanking();
            atualizarRankingMediaVendas();
            renderizarFavoritosOutrosAnuncios(favMlSkuSelecionado);
            renderizarHistoricoFavoritos();
        }

        function renderizarCelulaVendedor(cell, vendedor) {
            if (!cell) return;
            const nome = normalizarNomeVendedor(vendedor);
            cell.innerHTML = '';
            if (!nome) return;

            const wrap = document.createElement('div');
            wrap.className = 'ml-seller-cell';

            const nomeEl = document.createElement('span');
            nomeEl.className = 'ml-seller-name';
            nomeEl.textContent = nome;
            wrap.appendChild(nomeEl);

            if (vendedorValido(nome)) {
                const ignorado = vendedorIgnoradoNoRanking(nome);
                const botao = document.createElement('button');
                botao.type = 'button';
                botao.className = `ml-ignore-seller-btn${ignorado ? ' is-ignored' : ''}`;
                botao.textContent = ignorado ? 'Vendedor ignorado' : 'Ignorar esse vendedor';
                botao.title = ignorado
                    ? 'Este vendedor já está fora do ranking'
                    : `Ignorar ${nome} nos rankings`;
                botao.disabled = ignorado;
                if (!ignorado) {
                    botao.addEventListener('click', () => adicionarVendedorIgnoradoRanking(nome));
                }
                wrap.appendChild(botao);
            }

            cell.appendChild(wrap);
        }

        function criarBotaoIgnorarVendedorFavoritos(vendedor) {
            const nome = normalizarNomeVendedor(vendedor);
            if (!vendedorValido(nome)) return null;
            const ignorado = vendedorIgnoradoNoRanking(nome);
            const botao = document.createElement('button');
            botao.type = 'button';
            botao.className = `ml-ignore-seller-btn${ignorado ? ' is-ignored' : ''}`;
            botao.textContent = ignorado ? 'Vendedor ignorado' : 'Ignorar esse vendedor';
            botao.title = ignorado
                ? 'Este vendedor ja esta fora do ranking'
                : `Ignorar ${nome} nos rankings`;
            botao.disabled = ignorado;
            if (!ignorado) {
                botao.addEventListener('click', (event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    adicionarVendedorIgnoradoRanking(nome);
                });
            }
            return botao;
        }

        function parseNumeroVendas(valor) {
            if (valor === null || valor === undefined) return null;
            if (typeof valor === 'number') return Number.isFinite(valor) ? valor : null;
            const texto = String(valor).trim();
            if (!texto) return null;
            const match = texto.match(/([0-9][0-9\.,]*)\s*(k|mil)?\b/i);
            if (!match) return null;
            let numeroTexto = String(match[1]).replace(/\s+/g, '');
            const sufixo = String(match[2] || '').toLowerCase();

            if (numeroTexto.includes('.') && numeroTexto.includes(',')) {
                numeroTexto = numeroTexto.lastIndexOf('.') > numeroTexto.lastIndexOf(',')
                    ? numeroTexto.replace(/,/g, '')
                    : numeroTexto.replace(/\./g, '').replace(',', '.');
            } else if (numeroTexto.includes(',')) {
                numeroTexto = /^\d{1,3}(?:,\d{3})+$/.test(numeroTexto)
                    ? numeroTexto.replace(/,/g, '')
                    : numeroTexto.replace(',', '.');
            } else if (numeroTexto.includes('.')) {
                numeroTexto = /^\d{1,3}(?:\.\d{3})+$/.test(numeroTexto)
                    ? numeroTexto.replace(/\./g, '')
                    : numeroTexto;
            }
            const numero = Number(numeroTexto);
            if (!Number.isFinite(numero)) return null;
            const total = (sufixo === 'k' || sufixo === 'mil') ? numero * 1000 : numero;
            return Number.isFinite(total) ? Math.round(total) : null;
        }

        const hasNumeroVendas = (valor) => parseNumeroVendas(valor) !== null;
        const hasTexto = (valor) => typeof valor === 'string' ? valor.trim().length > 0 : valor !== null && valor !== undefined;
        const PESO_FONTE_VENDEDOR = {
            '': 0,
            avantpro_card: 1,
            api_search: 2,
            pagina_produto_fonte: 3,
            codigo_fonte: 4,
            avantpro_cache: 4,
            pagina_produto: 5,
            avantpro_dom: 5,
            avantpro_fast_dom: 5,
            avantpro_vendedor: 5,
            api: 6,
            api_item: 6,
            api_item_redirect: 6,
            mercado_livre_api: 8,
            mercado_livre_dom: 8,
            mercado_livre_dom_contexto_avant: 8,
            mercado_livre_dom_card_clicado: 8,
            mercadolivre_backend: 6,
            browser_item: 7
        };
        const PESO_FONTE_VENDAS = {
            '': 0,
            api: 2,
            api_search: 2,
            api_item_vendas: 3,
            api_item_redirect: 3,
            mercadolivre_backend: 3,
            html_text: 3,
            browser_item: 3,
            pagina_produto: 4,
            avantpro_card: 7,
            avantpro_produto: 9,
            avantpro_cache: 6,
            avantpro_dom: 7,
            avantpro_fast_dom: 8,
            avantpro_anuncio: 8
        };
        const normalizarFonte = (fonte) => String(fonte || '').trim().toLowerCase();
        const fontesNormalizadas = (fonte) => normalizarFonte(fonte)
            .split(/[+,|/]+/)
            .map(item => item.trim())
            .filter(Boolean);
        const pesoFonteVendedor = (fonte) => Math.max(0, ...fontesNormalizadas(fonte).map(item => PESO_FONTE_VENDEDOR[item] || 0));
        const pesoFonteVendas = (fonte) => Math.max(0, ...fontesNormalizadas(fonte).map(item => PESO_FONTE_VENDAS[item] || 0));
        const fonteVendasAvantPro = (fonte) => {
            const valor = normalizarFonte(fonte);
            return /avantpro_(?:anuncio|dom|fast_dom|cache|card|produto)/.test(valor);
        };
        const fonteVendasConfiavel = (fonte) => pesoFonteVendas(fonte) > 0;
        function parseVendasAvantPro(anuncio) {
            const fonte = anuncio && (anuncio.vendasFonte || anuncio.vendas_fonte || '');
            if (!fonteVendasConfiavel(fonte)) return null;
            return parseNumeroVendas(anuncio && anuncio.vendas);
        }
        function formatarVendasAvantPro(anuncio) {
            const vendas = parseVendasAvantPro(anuncio);
            return vendas !== null ? String(vendas) : '';
        }
        function normalizarImagemAnuncioFavoritos(valor) {
            const url = String(valor || '').trim();
            if (!url) return '';
            if (url.startsWith('//')) return `https:${url}`;
            if (/^https?:\/\//i.test(url)) return url;
            return '';
        }
        function obterImagemAnuncioFavoritos(anuncio) {
            if (!anuncio) return '';
            let imagem = anuncio.imagem
                || anuncio.thumbnail
                || anuncio.secure_thumbnail
                || anuncio.imagem_url
                || anuncio.foto
                || anuncio.picture
                || '';
            if (!imagem && Array.isArray(anuncio.pictures)) {
                const primeira = anuncio.pictures.find(Boolean) || {};
                imagem = primeira.secure_url || primeira.url || primeira.thumbnail || '';
            }
            return normalizarImagemAnuncioFavoritos(
                imagem
            );
        }
        function preencherImagemAnuncioFavoritos(alvo, fonte) {
            if (!alvo || obterImagemAnuncioFavoritos(alvo)) return false;
            const imagem = obterImagemAnuncioFavoritos(fonte);
            if (!imagem) return false;
            alvo.imagem = imagem;
            alvo.thumbnail = imagem;
            return true;
        }
        function criarCelulaFotoAnuncioFavoritos(anuncio) {
            const td = document.createElement('td');
            td.className = 'ml-favoritos-foto-cell';
            const imagem = obterImagemAnuncioFavoritos(anuncio);
            if (!imagem) {
                const placeholder = document.createElement('span');
                placeholder.className = 'ml-favoritos-foto-placeholder';
                placeholder.textContent = 'Sem foto';
                td.appendChild(placeholder);
                return td;
            }
            const img = document.createElement('img');
            img.className = 'ml-favoritos-foto';
            img.src = imagem;
            img.alt = anuncio && anuncio.id ? `Foto ${anuncio.id}` : 'Foto do anuncio';
            img.loading = 'lazy';
            img.addEventListener('load', agendarSincronizarLinhasFavoritos, { once: true });
            img.addEventListener('error', agendarSincronizarLinhasFavoritos, { once: true });
            if (anuncio && anuncio.url) {
                const wrap = document.createElement('span');
                wrap.className = 'ml-favoritos-foto-wrap';
                const openBtn = document.createElement('button');
                openBtn.type = 'button';
                openBtn.className = 'ml-favoritos-foto-open';
                openBtn.title = 'Abrir anuncio no Google Chrome';
                openBtn.addEventListener('click', (event) => abrirAnuncioNoChromeExterno(anuncio.url, event));
                openBtn.appendChild(img);
                const copyBtn = document.createElement('button');
                copyBtn.type = 'button';
                copyBtn.className = 'ml-favoritos-foto-copy';
                copyBtn.textContent = 'Copiar link';
                copyBtn.title = 'Copiar link do anuncio';
                copyBtn.addEventListener('click', (event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    copiarLinkAnuncio(anuncio.url, copyBtn);
                });
                wrap.appendChild(openBtn);
                wrap.appendChild(copyBtn);
                td.appendChild(wrap);
            } else {
                td.appendChild(img);
            }
            return td;
        }
        function parsePrecoAnuncioFavoritos(valor) {
            if (valor === null || valor === undefined || valor === '') return null;
            if (typeof valor === 'number') return Number.isFinite(valor) ? valor : null;
            const texto = String(valor).trim();
            if (!texto) return null;
            const match = texto.replace(/\s+/g, '').match(/-?\d[\d.,]*/);
            if (!match) return null;
            let normalizado = match[0];
            if (normalizado.includes('.') && normalizado.includes(',')) {
                normalizado = normalizado.lastIndexOf('.') > normalizado.lastIndexOf(',')
                    ? normalizado.replace(/,/g, '')
                    : normalizado.replace(/\./g, '').replace(',', '.');
            } else if (normalizado.includes(',')) {
                normalizado = normalizado.replace(/\./g, '').replace(',', '.');
            }
            const numero = Number(normalizado);
            return Number.isFinite(numero) ? numero : null;
        }
        function parsePercentualDescontoFavoritos(valor) {
            if (valor === null || valor === undefined || valor === '') return null;
            if (typeof valor === 'number') {
                if (!Number.isFinite(valor) || valor <= 0) return null;
                return valor > 0 && valor <= 1 ? valor * 100 : valor;
            }
            const texto = String(valor).trim();
            if (!texto) return null;
            const match = texto.replace(/\s+/g, '').match(/-?\d[\d.,]*/);
            if (!match) return null;
            let normalizado = match[0];
            if (normalizado.includes('.') && normalizado.includes(',')) {
                normalizado = normalizado.lastIndexOf('.') > normalizado.lastIndexOf(',')
                    ? normalizado.replace(/,/g, '')
                    : normalizado.replace(/\./g, '').replace(',', '.');
            } else if (normalizado.includes(',')) {
                normalizado = normalizado.replace(/\./g, '').replace(',', '.');
            }
            const numero = Number(normalizado);
            if (!Number.isFinite(numero) || numero <= 0) return null;
            return numero > 0 && numero <= 1 ? numero * 100 : numero;
        }
        function calcularDescontoPrecoFavoritos(precoOriginal, precoPromocional, anuncio) {
            if (precoOriginal !== null && precoPromocional !== null && precoOriginal > precoPromocional && precoOriginal > 0) {
                const desconto = ((precoOriginal - precoPromocional) / precoOriginal) * 100;
                return Number.isFinite(desconto) && desconto > 0 ? desconto : null;
            }
            return null;
        }
        function formatarDescontoPrecoFavoritos(valor) {
            const numero = Number(valor);
            if (!Number.isFinite(numero) || numero <= 0) return '';
            const arredondado = Math.round(numero * 10) / 10;
            const texto = Math.abs(arredondado - Math.round(arredondado)) < 0.05
                ? String(Math.round(arredondado))
                : arredondado.toFixed(1).replace('.', ',');
            return `-${texto}%`;
        }
        function anuncioEstaEmPromocaoFavoritos(anuncio) {
            if (!anuncio) return false;
            if (anuncio.has_promotion === true || anuncio.em_promocao === true) return true;
            if (anuncio.promotion_id || anuncio.promotion_type || anuncio.campaign_id) return true;
            if (Array.isArray(anuncio.deal_ids) && anuncio.deal_ids.length) return true;
            if (typeof anuncio.deal_ids === 'string' && anuncio.deal_ids.trim()) return true;
            return false;
        }
        function fontePrecoFavoritos(anuncio) {
            return normalizarFonte(anuncio && (
                anuncio.fonte_preco ||
                anuncio.preco_fonte ||
                anuncio.price_source ||
                anuncio.source ||
                anuncio.origem_dados ||
                ''
            ));
        }
        function prioridadeFontePrecoFavoritos(fonte) {
            const texto = normalizarFonte(fonte);
            if (!texto) return 0;
            if (texto.includes('api') || texto.includes('item') || texto.includes('mercadolivre_backend')) return 30;
            if (texto.includes('backend')) return 20;
            if (texto.includes('avantpro') || texto.includes('browser') || texto.includes('pagina')) return 10;
            return 5;
        }
        function fontePrecoConfiavelFavoritos(anuncio) {
            return prioridadeFontePrecoFavoritos(fontePrecoFavoritos(anuncio)) >= 20;
        }
        function obterPrecosAnuncioFavoritos(anuncio) {
            if (!anuncio) return { preco: null, promocional: null, desconto: null };
            const precoAtual = parsePrecoAnuncioFavoritos(anuncio.preco ?? anuncio.price ?? anuncio.valor ?? '');
            const salePriceObj = anuncio.sale_price && typeof anuncio.sale_price === 'object' ? anuncio.sale_price : null;
            const precoPromoDireto = parsePrecoAnuncioFavoritos(
                anuncio.preco_promocional ??
                anuncio.promotional_price ??
                anuncio.promotion_price ??
                anuncio.deal_price ??
                anuncio.discounted_price ??
                (salePriceObj && (salePriceObj.amount ?? salePriceObj.price)) ??
                anuncio.sale_price ??
                ''
            );
            let precoOriginal = parsePrecoAnuncioFavoritos(
                anuncio.preco_original ??
                anuncio.original_price ??
                anuncio.regular_amount ??
                anuncio.standard_price ??
                anuncio.base_price ??
                (salePriceObj && (salePriceObj.regular_amount ?? salePriceObj.original_amount)) ??
                ''
            );
            if (precoOriginal === null && precoPromoDireto !== null) {
                precoOriginal = parsePrecoAnuncioFavoritos(anuncio.standard_price ?? anuncio.base_price ?? '');
            }
            const descontoInformado = parsePercentualDescontoFavoritos(
                anuncio.discount_pct ??
                anuncio.discount_percent ??
                anuncio.discount_percentage ??
                anuncio.desconto ??
                anuncio.desconto_percentual ??
                ''
            );
            let preco = precoAtual;
            let promocional = precoPromoDireto;
            if (precoOriginal !== null && precoAtual !== null && precoOriginal > precoAtual) {
                preco = precoOriginal;
                promocional = precoAtual;
            } else if (precoOriginal !== null && precoPromoDireto !== null && precoOriginal > precoPromoDireto) {
                preco = precoOriginal;
                promocional = precoPromoDireto;
            } else if (precoPromoDireto !== null && precoAtual !== null && precoAtual > precoPromoDireto) {
                preco = precoAtual;
                promocional = precoPromoDireto;
            }
            if (promocional !== null && preco !== null && Math.abs(promocional - preco) < 0.005) {
                promocional = null;
            }
            if (promocional === null && precoAtual !== null && descontoInformado !== null && descontoInformado > 0 && descontoInformado < 100) {
                const originalCalculado = precoAtual / (1 - (descontoInformado / 100));
                if (Number.isFinite(originalCalculado) && originalCalculado > precoAtual) {
                    preco = originalCalculado;
                    promocional = precoAtual;
                }
            }
            const desconto = calcularDescontoPrecoFavoritos(preco, promocional, anuncio) || descontoInformado;
            return { preco, promocional, desconto };
        }
        function preencherPrecoAnuncioFavoritos(alvo, fonte) {
            if (!alvo || !fonte) return false;
            let alterou = false;
            const precosFonte = obterPrecosAnuncioFavoritos(fonte);
            const fonteNova = fontePrecoFavoritos(fonte);
            const prioridadeNova = prioridadeFontePrecoFavoritos(fonteNova);
            const prioridadeAtual = prioridadeFontePrecoFavoritos(fontePrecoFavoritos(alvo));
            const temPrecoFonte = precosFonte.preco !== null || precosFonte.promocional !== null;

            if (temPrecoFonte && (prioridadeNova > prioridadeAtual || (prioridadeNova >= 20 && !fontePrecoConfiavelFavoritos(alvo)))) {
                const precoBase = precosFonte.preco !== null ? precosFonte.preco : precosFonte.promocional;
                const precoAtual = precosFonte.promocional !== null ? precosFonte.promocional : precoBase;
                alvo.preco = precoBase;
                alvo.price = precoAtual;
                alvo.preco_original = precosFonte.promocional !== null ? precoBase : '';
                alvo.original_price = precosFonte.promocional !== null ? precoBase : '';
                alvo.standard_price = precoBase;
                alvo.preco_promocional = precosFonte.promocional !== null ? precosFonte.promocional : '';
                alvo.promotional_price = precosFonte.promocional !== null ? precosFonte.promocional : '';
                alvo.sale_price = '';
                alvo.discount_pct = precosFonte.desconto || '';
                alvo.fonte_preco = fonteNova || fonte.source || fonte.origem_dados || '';
                alterou = true;
                return alterou;
            }

            const campos = ['preco', 'price', 'preco_original', 'original_price', 'standard_price', 'preco_promocional', 'promotional_price', 'promotion_price', 'sale_price'];
            campos.forEach(campo => {
                if ((alvo[campo] === null || alvo[campo] === undefined || alvo[campo] === '') && fonte[campo] !== null && fonte[campo] !== undefined && fonte[campo] !== '') {
                    alvo[campo] = fonte[campo];
                    alterou = true;
                }
            });
            if (!alvo.fonte_preco && fonteNova) {
                alvo.fonte_preco = fonteNova;
                alterou = true;
            }
            return alterou;
        }
        function precisaComplementoPrecoFavoritos(anuncio) {
            const precos = obterPrecosAnuncioFavoritos(anuncio);
            if (precos.preco === null) return true;
            if (!fontePrecoConfiavelFavoritos(anuncio)) return true;
            return parsePrecoAnuncioFavoritos(anuncio && (anuncio.preco_original ?? anuncio.original_price ?? anuncio.standard_price ?? '')) === null
                && parsePrecoAnuncioFavoritos(anuncio && (anuncio.preco_promocional ?? anuncio.promotional_price ?? anuncio.promotion_price ?? anuncio.sale_price ?? '')) === null;
        }
        function criarInfoPrecoFavoritos(rotulo, valor) {
            if (valor === null || valor === undefined) return null;
            const titulo = String(rotulo || '').trim();
            if (!titulo) return null;
            const texto = String(valor).trim();
            if (!texto) return null;
            const info = document.createElement('span');
            info.className = 'ml-favoritos-preco-info';
            info.textContent = `${titulo}: ${texto}`;
            return info;
        }

        function criarCelulaPrecoAnuncioFavoritos(anuncio, infosPreco = []) {
            const td = document.createElement('td');
            td.className = 'ml-favoritos-preco-cell';
            const precos = obterPrecosAnuncioFavoritos(anuncio);
            const infosValidas = Array.isArray(infosPreco)
                ? infosPreco.map(item => criarInfoPrecoFavoritos(item && item.rotulo, item && item.valor)).filter(Boolean)
                : [];
            if (precos.preco === null && precos.promocional === null && !infosValidas.length) return td;
            const stack = document.createElement('div');
            stack.className = 'ml-favoritos-preco-stack';
            if (infosValidas.length) {
                const infoGroup = document.createElement('span');
                infoGroup.className = 'ml-favoritos-preco-info-group';
                infosValidas.forEach(info => infoGroup.appendChild(info));
                stack.appendChild(infoGroup);
            }
            if (precos.preco !== null || precos.promocional !== null) {
                const valueStack = document.createElement('span');
                valueStack.className = 'ml-favoritos-preco-value-stack';
                const base = document.createElement('span');
                base.className = 'ml-favoritos-preco-base' + (precos.promocional !== null ? ' is-original' : '');
                base.textContent = formatarPrecoFavoritosMl(precos.preco !== null ? precos.preco : precos.promocional);
                const descontoTexto = formatarDescontoPrecoFavoritos(precos.desconto);
                const promoTexto = anuncioEstaEmPromocaoFavoritos(anuncio) ? (descontoTexto || 'Promo') : '';
                valueStack.appendChild(base);
                if (promoTexto) {
                    const desconto = document.createElement('span');
                    desconto.className = 'ml-favoritos-preco-desconto';
                    desconto.textContent = promoTexto;
                    valueStack.appendChild(desconto);
                }
                if (precos.promocional !== null && precos.preco !== null) {
                    const promo = document.createElement('span');
                    promo.className = 'ml-favoritos-preco-promo';
                    promo.textContent = formatarPrecoFavoritosMl(precos.promocional);
                    valueStack.appendChild(promo);
                }
                const margemCell = criarCelulaMargemAnuncioFavoritos(anuncio);
                const margemBadge = margemCell.firstElementChild;
                if (margemBadge) {
                    const margemWrap = document.createElement('span');
                    margemWrap.className = 'ml-favoritos-preco-margem';
                    margemWrap.title = margemCell.title || '';
                    margemWrap.appendChild(margemBadge.cloneNode(true));
                    valueStack.appendChild(margemWrap);
                }
                stack.appendChild(valueStack);
            }
            td.appendChild(stack);
            return td;
        }

        function criarCelulaPrecoHistoricoFavoritos(anuncio) {
            const td = document.createElement('td');
            td.className = 'ml-favoritos-preco-cell';
            const precos = obterPrecosAnuncioFavoritos(anuncio);
            if (precos.preco === null && precos.promocional === null) return td;

            const stack = document.createElement('span');
            stack.className = 'ml-favoritos-preco-value-stack';
            const precoBase = precos.preco !== null ? precos.preco : precos.promocional;
            const temPromocional = precos.promocional !== null && precos.preco !== null;

            const base = document.createElement('span');
            base.className = 'ml-favoritos-preco-base' + (temPromocional ? ' is-original' : '');
            const baseLabel = document.createElement('span');
            baseLabel.className = 'ml-favoritos-preco-label';
            baseLabel.textContent = temPromocional ? 'Normal:' : 'Preco:';
            base.appendChild(baseLabel);
            base.append(` ${formatarPrecoFavoritosMl(precoBase)}`);
            stack.appendChild(base);

            const descontoTexto = formatarDescontoPrecoFavoritos(precos.desconto);
            if (descontoTexto) {
                const desconto = document.createElement('span');
                desconto.className = 'ml-favoritos-preco-desconto';
                desconto.textContent = descontoTexto;
                stack.appendChild(desconto);
            }

            if (temPromocional) {
                const promo = document.createElement('span');
                promo.className = 'ml-favoritos-preco-promo';
                const promoLabel = document.createElement('span');
                promoLabel.className = 'ml-favoritos-preco-label';
                promoLabel.textContent = 'Com desconto:';
                promo.appendChild(promoLabel);
                promo.append(` ${formatarPrecoFavoritosMl(precos.promocional)}`);
                stack.appendChild(promo);
            }

            const margemCell = criarCelulaMargemAnuncioFavoritos(anuncio);
            const margemBadge = margemCell.firstElementChild;
            if (margemBadge) {
                const margemWrap = document.createElement('span');
                margemWrap.className = 'ml-favoritos-preco-margem';
                margemWrap.title = margemCell.title || '';
                margemWrap.appendChild(margemBadge.cloneNode(true));
                stack.appendChild(margemWrap);
            }

            td.appendChild(stack);
            return td;
        }
        function parseMargemAnuncioFavoritos(valor) {
            if (valor === null || valor === undefined || valor === '') return null;
            if (typeof valor === 'number') return Number.isFinite(valor) ? valor : null;
            const texto = String(valor).replace('%', '').replace(/\s+/g, '').trim();
            if (!texto) return null;
            const normalizado = texto.includes(',')
                ? texto.replace(/\./g, '').replace(',', '.')
                : texto;
            const numero = Number(normalizado);
            return Number.isFinite(numero) ? numero : null;
        }
        function obterIdAnuncioFavoritos(anuncio) {
            if (!anuncio) return '';
            return String(
                anuncio.mlb ||
                anuncio.id ||
                anuncio.item_id ||
                anuncio.itemId ||
                extrairItemIdAnuncio(anuncio.url || anuncio.link || anuncio.permalink) ||
                ''
            ).trim().toUpperCase();
        }
        function encontrarAnuncioProprioRankingFavoritos(anuncio) {
            const itemId = obterIdAnuncioFavoritos(anuncio);
            if (!itemId || !Array.isArray(favMlAnunciosSkuAtual)) return null;
            return favMlAnunciosSkuAtual.find(item => obterIdAnuncioFavoritos(item) === itemId) || null;
        }
        function mesclarMargemAnuncioFavoritos(alvo, fonte) {
            if (!alvo || !fonte) return alvo;
            [
                'margem_percentual', 'margem', 'margem_text', 'margem_status',
                'valor_liquido', 'valor_liquido_text', 'custo', 'custo_text',
                'frete_ml', 'frete_ml_text', 'tarifa_ml', 'tarifa_ml_text',
                'imposto_percentual', 'imposto_valor', 'imposto_valor_text',
                'taxa_ml_percentual', 'preco_final_margem', 'sku_margem',
                'ad_cost_original', 'ad_cost_original_text',
                'promotion_fee_discount', 'promotion_fee_discount_text',
                'promotion_fee_discount_applied',
                'promotion_fee_charged', 'promotion_fee_charged_text',
                'promotion_fee_discount_source', 'promotion_name',
                'promotion_fee_base', 'promotion_fee_base_text',
                'promotion_fee_ml', 'promotion_fee_ml_text'
            ].forEach(campo => {
                if ((alvo[campo] === null || alvo[campo] === undefined || alvo[campo] === '') && fonte[campo] !== null && fonte[campo] !== undefined && fonte[campo] !== '') {
                    alvo[campo] = fonte[campo];
                }
            });
            return alvo;
        }
        function aplicarMargemRankingFavoritos(anuncio) {
            return mesclarMargemAnuncioFavoritos(anuncio, encontrarAnuncioProprioRankingFavoritos(anuncio));
        }
        function formatarMargemAnuncioFavoritos(valor) {
            const numero = parseMargemAnuncioFavoritos(valor);
            if (numero === null) return '';
            return `${numero.toFixed(2).replace('.', ',')}%`;
        }
        function parseTaxaSimuladorFavoritos(valor) {
            const numero = parseMargemAnuncioFavoritos(valor);
            if (numero === null) return null;
            return Math.abs(numero) > 1 ? numero / 100 : numero;
        }
        function obterPrecoVigenteAnuncioFavoritos(anuncio) {
            const precos = obterPrecosAnuncioFavoritos(anuncio);
            const direto = precos.promocional !== null && precos.promocional !== undefined
                ? precos.promocional
                : precos.preco;
            if (direto !== null && direto !== undefined) return direto;
            return parsePrecoAnuncioFavoritos(anuncio && (
                anuncio.price ??
                anuncio.preco ??
                anuncio.valor ??
                anuncio.sale_price ??
                anuncio.preco_promocional ??
                ''
            ));
        }
        function chavePrecoCentavosFavoritos(valor) {
            const numero = parsePrecoAnuncioFavoritos(valor);
            if (numero === null || !Number.isFinite(numero)) return '';
            return String(Math.round(numero * 100));
        }
        function obterPrecoFinalSimulacaoFavoritos(sim) {
            if (!sim || !sim.ok) return null;
            return parsePrecoAnuncioFavoritos(
                sim.precoPromocionalCalculado
                ?? sim.precoPromocional
                ?? sim.precoCompetitivo
                ?? sim.preco
            );
        }
        function obterRankingFavoritosParaSimulador(sku) {
            const resultadoRanking = obterGrupoRankingFavoritosSku(sku);
            const grupo = resultadoRanking && resultadoRanking.grupo;
            if (!grupo || grupo.erro || !Array.isArray(grupo.anuncios)) return [];
            return filtrarAnunciosIgnoradosRanking(grupo.anuncios, sku)
                .map(anuncio => aplicarMargemRankingFavoritos({ ...anuncio }));
        }
