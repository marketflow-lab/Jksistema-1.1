        function favoritosPlanilhasDefinirStatus(texto, erro = false) {
            if (!favoritosPlanilhasStatusEl) return;
            favoritosPlanilhasStatusEl.textContent = texto || '';
            favoritosPlanilhasStatusEl.classList.toggle('hidden', !texto);
            favoritosPlanilhasStatusEl.classList.toggle('error', !!erro);
        }

        function favoritosPlanilhasUrlValida(url) {
            const valor = String(url || '').trim();
            if (!valor) return true;
            try {
                const parsed = new URL(valor);
                return /^https?:$/i.test(parsed.protocol)
                    && parsed.hostname.toLowerCase() === 'docs.google.com'
                    && parsed.pathname.toLowerCase().startsWith('/spreadsheets/');
            } catch (_err) {
                return false;
            }
        }

        function favoritosPlanilhasNormalizarUrl(url) {
            return String(url || '').replace(/\s+/g, '').trim();
        }

        function favoritosPlanilhasLojasDisponiveis() {
            const mapa = new Map();
            const adicionar = (valor) => {
                const nome = String((valor && valor.nome) || valor || '').trim();
                const chave = favoritosChavePlanilhaLoja(nome);
                if (!nome || !chave || favoritosEhTodasLojas(nome)) return;
                if (!mapa.has(chave)) mapa.set(chave, nome);
            };
            (Array.isArray(mlSkuLojasDisponiveis) ? mlSkuLojasDisponiveis : []).forEach(adicionar);
            skuLojasComDados().forEach(loja => adicionar(loja && loja.nome));
            adicionar(mlSkuLojaSelecionada);
            adicionar(skuLojaSelecionada);
            return Array.from(mapa.entries())
                .map(([chave, nome]) => ({ chave, nome }))
                .sort((a, b) => a.nome.localeCompare(b.nome, 'pt-BR', { numeric: true, sensitivity: 'base' }));
        }

        function favoritosPlanilhasUrlSalva(loja) {
            const chave = favoritosChavePlanilhaLoja(loja);
            const item = chave ? favoritosPlanilhasLojas[chave] : null;
            return String(item && item.url || '').trim();
        }

        function favoritosPlanilhasAtualizarLinkAbrir(linkEl, url) {
            if (!linkEl) return;
            const valor = favoritosPlanilhasNormalizarUrl(url);
            linkEl.href = valor || '#';
            linkEl.classList.toggle('is-disabled', !valor);
            linkEl.setAttribute('aria-disabled', valor ? 'false' : 'true');
        }

        function favoritosPlanilhasAtualizarTopo() {
            const lojaAtual = favoritosLojaSelecionadaParaApi(mlSkuLojaSelecionada || skuLojaSelecionada || '');
            const url = favoritosPlanilhasUrlSalva(lojaAtual);
            if (favoritosPlanilhaLojaAtualNomeEl) {
                favoritosPlanilhaLojaAtualNomeEl.textContent = lojaAtual || 'Escolha uma loja';
            }
            if (favoritosPlanilhaLojaAtualUrlEl) {
                favoritosPlanilhaLojaAtualUrlEl.value = url;
                favoritosPlanilhaLojaAtualUrlEl.disabled = !lojaAtual;
            }
            if (favoritosPlanilhaLojaAtualSalvarEl) {
                favoritosPlanilhaLojaAtualSalvarEl.disabled = !lojaAtual;
            }
            favoritosPlanilhasAtualizarLinkAbrir(favoritosPlanilhaLojaAtualAbrirEl, url);
        }

        function favoritosPlanilhasRenderizarLista() {
            if (!favoritosPlanilhasListEl) return;
            const lojas = favoritosPlanilhasLojasDisponiveis();
            favoritosPlanilhasListEl.innerHTML = '';
            if (!lojas.length) {
                favoritosPlanilhasListEl.innerHTML = '<div class="muted">Nenhuma loja disponivel.</div>';
                return;
            }
            lojas.forEach(loja => {
                const url = favoritosPlanilhasUrlSalva(loja.nome);
                const row = document.createElement('div');
                row.className = 'favoritos-sheets-row';

                const info = document.createElement('div');
                const nome = document.createElement('div');
                nome.className = 'favoritos-sheets-row-name';
                nome.textContent = loja.nome;
                const meta = document.createElement('div');
                meta.className = 'favoritos-sheets-row-meta';
                meta.textContent = url ? 'Link cadastrado' : 'Sem link';
                info.appendChild(nome);
                info.appendChild(meta);

                const input = document.createElement('input');
                input.type = 'url';
                input.placeholder = 'https://docs.google.com/spreadsheets/d/...';
                input.value = url;
                input.autocomplete = 'off';

                const salvar = document.createElement('button');
                salvar.type = 'button';
                salvar.className = 'btn-back';
                salvar.textContent = 'Salvar';
                salvar.addEventListener('click', () => favoritosPlanilhasSalvarLoja(loja.nome, input.value, salvar));
                input.addEventListener('keydown', (event) => {
                    if (event.key === 'Enter') favoritosPlanilhasSalvarLoja(loja.nome, input.value, salvar);
                });

                const abrir = document.createElement('a');
                abrir.className = 'favoritos-sheets-open';
                abrir.target = '_blank';
                abrir.rel = 'noopener';
                abrir.textContent = 'Abrir';
                favoritosPlanilhasAtualizarLinkAbrir(abrir, url);

                row.appendChild(info);
                row.appendChild(input);
                row.appendChild(salvar);
                row.appendChild(abrir);
                favoritosPlanilhasListEl.appendChild(row);
            });
        }

        function favoritosPlanilhasRenderizar() {
            favoritosPlanilhasAtualizarTopo();
            favoritosPlanilhasRenderizarLista();
        }

        async function favoritosPlanilhasCarregarServidor(forcar = false) {
            if (favoritosPlanilhasCarregando) return;
            if (favoritosPlanilhasServidorCarregado && !forcar) {
                favoritosPlanilhasRenderizar();
                return;
            }
            favoritosPlanilhasCarregando = true;
            favoritosPlanilhasDefinirStatus('Carregando planilhas...');
            try {
                const response = await fetch('/api/favoritos/planilhas-lojas', {
                    headers: obterAuthHeaders(),
                    cache: 'no-store'
                });
                if (!response.ok) {
                    let detalhe = `HTTP ${response.status}`;
                    try {
                        const dataErro = await response.json();
                        detalhe = dataErro.detail || detalhe;
                    } catch (_err) {}
                    throw new Error(detalhe);
                }
                const data = await response.json();
                favoritosPlanilhasLojas = data && typeof data.planilhas === 'object' && data.planilhas ? data.planilhas : {};
                favoritosPlanilhasServidorCarregado = true;
                if (favoritosPlanilhasUpdatedEl) {
                    favoritosPlanilhasUpdatedEl.textContent = data.updated_at ? `Atualizado em ${data.updated_at}` : '';
                }
                favoritosPlanilhasDefinirStatus('');
                favoritosPlanilhasRenderizar();
            } catch (err) {
                favoritosPlanilhasDefinirStatus(`Erro ao carregar planilhas: ${err && err.message ? err.message : err}`, true);
                favoritosPlanilhasRenderizar();
            } finally {
                favoritosPlanilhasCarregando = false;
            }
        }

        async function favoritosPlanilhasSalvarLoja(loja, url, botao = null) {
            const nome = String(loja || '').trim();
            const valor = favoritosPlanilhasNormalizarUrl(url);
            if (!nome || favoritosEhTodasLojas(nome)) return;
            if (!favoritosPlanilhasUrlValida(valor)) {
                favoritosPlanilhasDefinirStatus('Informe um link valido do Google Sheets.', true);
                return;
            }
            const textoOriginal = botao ? botao.textContent : '';
            if (botao) {
                botao.disabled = true;
                botao.textContent = 'Salvando...';
            }
            favoritosPlanilhasDefinirStatus(`Salvando link da loja ${nome}...`);
            try {
                const response = await fetch('/api/favoritos/planilhas-lojas', {
                    method: 'PUT',
                    headers: headersJsonAutenticado(),
                    body: JSON.stringify({ planilhas: [{ loja: nome, url: valor }] })
                });
                if (!response.ok) {
                    let detalhe = `HTTP ${response.status}`;
                    try {
                        const dataErro = await response.json();
                        detalhe = dataErro.detail || detalhe;
                    } catch (_err) {}
                    throw new Error(detalhe);
                }
                const data = await response.json();
                favoritosPlanilhasLojas = data && typeof data.planilhas === 'object' && data.planilhas ? data.planilhas : {};
                favoritosPlanilhasServidorCarregado = true;
                if (favoritosPlanilhasUpdatedEl) {
                    favoritosPlanilhasUpdatedEl.textContent = data.updated_at ? `Atualizado em ${data.updated_at}` : '';
                }
                favoritosPlanilhasDefinirStatus(valor ? `Link salvo para ${nome}.` : `Link removido de ${nome}.`);
                favoritosPlanilhasRenderizar();
            } catch (err) {
                favoritosPlanilhasDefinirStatus(`Erro ao salvar planilha: ${err && err.message ? err.message : err}`, true);
            } finally {
                if (botao) {
                    botao.disabled = false;
                    botao.textContent = textoOriginal || 'Salvar';
                }
            }
        }

        function prepararAbaPlanilhasFavoritos() {
            favoritosPlanilhasRenderizar();
            favoritosPlanilhasCarregarServidor();
        }

        function favoritosUsarIaRankingAtivo() {
            return !!(mlSkuUsarIaFavoritosEl && mlSkuUsarIaFavoritosEl.checked);
        }

        function carregarPreferenciaUsarIaFavoritos() {
            if (!mlSkuUsarIaFavoritosEl) return;
            try {
                mlSkuUsarIaFavoritosEl.checked = localStorage.getItem(ML_FAVORITOS_USAR_IA_KEY) === '1';
            } catch (_err) {
                mlSkuUsarIaFavoritosEl.checked = false;
            }
        }

        function salvarPreferenciaUsarIaFavoritos() {
            if (!mlSkuUsarIaFavoritosEl) return;
            try {
                localStorage.setItem(ML_FAVORITOS_USAR_IA_KEY, mlSkuUsarIaFavoritosEl.checked ? '1' : '0');
            } catch (_err) {}
        }

        function registrarLojasCompartilhadasFavoritosSku(sku, lojas) {
            const chave = skuChaveSku(sku);
            if (!chave) return [];
            const lista = (Array.isArray(lojas) ? lojas : [])
                .map(loja => String(loja || '').trim())
                .filter(Boolean);
            const unicas = Array.from(new Map(lista.map(loja => [skuNormalizarLoja(loja), loja])).values());
            if (unicas.length) {
                mlFavoritosLojasCompartilhadasPorSku.set(chave, unicas);
            }
            return unicas;
        }

        function obterLojasCompartilhadasFavoritosSku(sku) {
            const chave = skuChaveSku(sku);
            if (!chave) return [];
            return Array.isArray(mlFavoritosLojasCompartilhadasPorSku.get(chave))
                ? mlFavoritosLojasCompartilhadasPorSku.get(chave)
                : [];
        }

        function entradaHistoricoPertenceLojasCompartilhadasSku(entrada, sku, lojaAtualNorm) {
            const chave = skuChaveSku(sku);
            if (!chave || !lojaAtualNorm) return false;
            const lojasSku = obterLojasCompartilhadasFavoritosSku(chave)
                .map(skuNormalizarLoja)
                .filter(Boolean);
            if (lojasSku.length < 2 || !lojasSku.includes(lojaAtualNorm)) return false;
            const lojasEntrada = new Set();
            const lojaEntrada = skuNormalizarLoja(entrada && entrada.loja || '');
            if (lojaEntrada) lojasEntrada.add(lojaEntrada);
            (entrada && Array.isArray(entrada.grupos) ? entrada.grupos : [])
                .filter(grupo => skuChaveSku(grupo && grupo.sku) === chave)
                .forEach(grupo => {
                    const lojaGrupo = skuNormalizarLoja(grupo && grupo.loja || '');
                    if (lojaGrupo) lojasEntrada.add(lojaGrupo);
                });
            return Array.from(lojasEntrada).some(loja => lojasSku.includes(loja));
        }
