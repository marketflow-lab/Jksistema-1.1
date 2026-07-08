(function initGlobalIaRagAutoIndex() {
    if (window.__jkIaRagAutoIndexInit) return;
    window.__jkIaRagAutoIndexInit = true;

    const STORAGE_KEY = 'jk-ia-rag-autoindex-v1';
    const SUCESSO_INTERVALO_MS = 6 * 60 * 60 * 1000; // 6h
    const TENTATIVA_MIN_INTERVALO_MS = 5 * 60 * 1000; // 5min
    const INDICATOR_REFRESH_MS = 60 * 1000;
    let emExecucao = false;
    let indicadorTimer = null;
    let modalDiagnostico = null;

    function usuarioAdmin() {
        try {
            const permissoes = JSON.parse(localStorage.getItem('permissions') || '{}');
            return permissoes && (permissoes.full === true || permissoes.admin_usuarios === true);
        } catch (_e) {
            return false;
        }
    }

    function formatarTempoDecorrido(timestamp) {
        const t = Number(timestamp || 0);
        if (!t) return '';
        const deltaMs = Math.max(0, Date.now() - t);
        const totalMin = Math.floor(deltaMs / 60000);
        if (totalMin <= 0) return 'agora';
        if (totalMin < 60) return `ha ${totalMin} min`;
        const horas = Math.floor(totalMin / 60);
        const minutos = totalMin % 60;
        if (horas < 24) {
            return minutos > 0 ? `ha ${horas}h ${minutos}min` : `ha ${horas}h`;
        }
        const dias = Math.floor(horas / 24);
        return `ha ${dias}d`;
    }

    function garantirIndicador() {
        if (!usuarioAdmin()) return null;

        if (!document.getElementById('jk-ia-rag-indicador-style')) {
            const style = document.createElement('style');
            style.id = 'jk-ia-rag-indicador-style';
            style.textContent = `
                #jk-ia-rag-indicador {
                    position: fixed;
                    left: 14px;
                    bottom: 14px;
                    z-index: 99996;
                    max-width: min(360px, calc(100vw - 28px));
                    padding: 8px 10px;
                    border-radius: 10px;
                    border: 1px solid rgba(123, 207, 255, 0.36);
                    background: linear-gradient(180deg, rgba(14, 29, 52, 0.96), rgba(10, 21, 38, 0.98));
                    color: #d9ecff;
                    font: 600 12px "Segoe UI", Tahoma, sans-serif;
                    line-height: 1.35;
                    box-shadow: 0 10px 20px rgba(0, 0, 0, 0.35);
                    letter-spacing: 0.1px;
                    user-select: none;
                    cursor: pointer;
                }
                #jk-ia-rag-indicador:hover {
                    filter: brightness(1.05);
                }
                #jk-ia-rag-indicador.running {
                    border-color: rgba(140, 215, 255, 0.6);
                }
                #jk-ia-rag-indicador.ok {
                    border-color: rgba(108, 224, 154, 0.55);
                }
                #jk-ia-rag-indicador.warn {
                    border-color: rgba(255, 169, 109, 0.62);
                }
                #jk-ia-rag-modal {
                    position: fixed;
                    inset: 0;
                    z-index: 99997;
                    display: none;
                    align-items: center;
                    justify-content: center;
                    background: rgba(2, 6, 12, 0.66);
                    backdrop-filter: blur(3px);
                }
                #jk-ia-rag-modal.open {
                    display: flex;
                }
                #jk-ia-rag-modal .jk-ia-rag-card {
                    width: min(560px, calc(100vw - 28px));
                    max-height: min(78vh, 760px);
                    overflow: auto;
                    border-radius: 12px;
                    border: 1px solid rgba(123, 207, 255, 0.38);
                    background: linear-gradient(180deg, rgba(12, 24, 44, 0.98), rgba(8, 17, 33, 0.98));
                    color: #d9ecff;
                    box-shadow: 0 22px 44px rgba(0, 0, 0, 0.52);
                    padding: 14px;
                    font: 600 12px "Segoe UI", Tahoma, sans-serif;
                }
                #jk-ia-rag-modal .jk-ia-rag-head {
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                    gap: 8px;
                    margin-bottom: 12px;
                }
                #jk-ia-rag-modal .jk-ia-rag-title {
                    font-size: 14px;
                    font-weight: 800;
                    color: #cbe8ff;
                }
                #jk-ia-rag-modal .jk-ia-rag-close {
                    width: 28px;
                    height: 28px;
                    border: 1px solid rgba(123, 207, 255, 0.36);
                    border-radius: 8px;
                    background: rgba(14, 29, 52, 0.9);
                    color: #d9ecff;
                    cursor: pointer;
                    font-weight: 800;
                }
                #jk-ia-rag-modal .jk-ia-rag-grid {
                    display: grid;
                    grid-template-columns: 1fr 1fr;
                    gap: 8px;
                }
                #jk-ia-rag-modal .jk-ia-rag-item {
                    border: 1px solid rgba(123, 207, 255, 0.2);
                    border-radius: 8px;
                    padding: 8px;
                    background: rgba(255, 255, 255, 0.03);
                }
                #jk-ia-rag-modal .jk-ia-rag-item .k {
                    color: #9fd4ff;
                    font-weight: 700;
                    margin-bottom: 3px;
                }
                #jk-ia-rag-modal .jk-ia-rag-item .v {
                    color: #e4f2ff;
                    word-break: break-word;
                    white-space: pre-wrap;
                }
                #jk-ia-rag-modal .jk-ia-rag-foot {
                    margin-top: 10px;
                    color: #9ab8d8;
                    font-size: 11px;
                }
                @media (max-width: 760px) {
                    #jk-ia-rag-modal .jk-ia-rag-grid {
                        grid-template-columns: 1fr;
                    }
                }
            `;
            document.head.appendChild(style);
        }

        let el = document.getElementById('jk-ia-rag-indicador');
        if (!el) {
            el = document.createElement('div');
            el.id = 'jk-ia-rag-indicador';
            el.title = 'Clique para abrir diagnostico IA/RAG';
            el.addEventListener('click', () => {
                abrirDiagnosticoRag();
            });
            (document.body || document.documentElement).appendChild(el);
        }
        return el;
    }

    function boolTexto(v) {
        return v ? 'sim' : 'nao';
    }

    function textoCurto(v, limite = 280) {
        const s = String(v || '').trim();
        return s.length > limite ? `${s.slice(0, limite)}...` : s;
    }

    function garantirModalDiagnostico() {
        if (!usuarioAdmin()) return null;
        if (modalDiagnostico) return modalDiagnostico;

        const el = document.createElement('div');
        el.id = 'jk-ia-rag-modal';
        el.innerHTML = `
            <div class="jk-ia-rag-card" role="dialog" aria-modal="true" aria-label="Diagnostico IA RAG">
                <div class="jk-ia-rag-head">
                    <div class="jk-ia-rag-title">Diagnostico IA/RAG</div>
                    <button type="button" class="jk-ia-rag-close" aria-label="Fechar">x</button>
                </div>
                <div id="jk-ia-rag-modal-body">Carregando status...</div>
                <div class="jk-ia-rag-foot">Fonte: /api/ia/rag/status</div>
            </div>
        `;
        el.addEventListener('click', (event) => {
            if (event.target === el) {
                el.classList.remove('open');
            }
        });
        el.querySelector('.jk-ia-rag-close')?.addEventListener('click', () => {
            el.classList.remove('open');
        });
        window.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                el.classList.remove('open');
            }
        });
        (document.body || document.documentElement).appendChild(el);
        modalDiagnostico = el;
        return el;
    }

    async function abrirDiagnosticoRag() {
        const modal = garantirModalDiagnostico();
        if (!modal) return;

        modal.classList.add('open');
        const body = modal.querySelector('#jk-ia-rag-modal-body');
        if (body) body.textContent = 'Carregando status...';

        try {
            const resp = await fetch('/api/ia/rag/status', {
                headers: obterAuthHeaders(),
                cache: 'no-store'
            });
            let data = null;
            try {
                data = await resp.json();
            } catch (_e) {
                data = null;
            }

            if (!resp.ok) {
                const detalhe = textoCurto(data && data.detail ? data.detail : resp.statusText || 'falha ao consultar status');
                if (body) body.innerHTML = `<div class="jk-ia-rag-item"><div class="k">Erro</div><div class="v">${detalhe}</div></div>`;
                return;
            }

            if (!data || typeof data !== 'object') {
                if (body) body.innerHTML = `<div class="jk-ia-rag-item"><div class="k">Erro</div><div class="v">Resposta invalida do servidor.</div></div>`;
                return;
            }

            const itens = [
                ['Client ID', data.client_id || '-'],
                ['RAG habilitado', boolTexto(data.enabled)],
                ['Backend RAG', data.backend || '-'],
                ['Modo configurado', data.backend_configurado || '-'],
                ['Motor local', data.local_engine || '-'],
                ['Local ok', data.local_ok == null ? '-' : boolTexto(data.local_ok)],
                ['Docs locais', data.local_documentos != null ? String(data.local_documentos) : '-'],
                ['DB local MB', data.local_db_mb != null ? String(data.local_db_mb) : '-'],
                ['Postgres configurado', boolTexto(data.postgres_configurado)],
                ['Postgres ok', boolTexto(data.postgres_ok)],
                ['pgvector ok', boolTexto(data.pgvector_ok)],
                ['Ollama ok', boolTexto(data.ollama_ok)],
                ['Ollama base URL', data.ollama_base_url || '-'],
                ['Modelo embedding', data.ollama_embedding_model || '-'],
                ['Top K', data.top_k != null ? String(data.top_k) : '-'],
                ['psycopg instalado', boolTexto(data.psycopg_instalado)],
                ['Erro local', textoCurto(data.local_error || '-')],
                ['Erro Ollama', textoCurto(data.ollama_error || '-')],
                ['Erro Postgres', textoCurto(data.postgres_error || '-')]
            ];

            if (body) {
                body.innerHTML = `<div class="jk-ia-rag-grid">${itens.map(([k, v]) => `<div class="jk-ia-rag-item"><div class="k">${k}</div><div class="v">${String(v || '-')}</div></div>`).join('')}</div>`;
            }
        } catch (_e) {
            if (body) body.innerHTML = `<div class="jk-ia-rag-item"><div class="k">Erro</div><div class="v">Falha de conexao ao consultar /api/ia/rag/status.</div></div>`;
        }
    }

    function atualizarIndicador() {
        document.getElementById('jk-ia-rag-indicador')?.remove();
        return;

        const el = garantirIndicador();
        if (!el) return;

        const estado = lerEstado();
        const ultimoOk = Number(estado.last_ok_at || 0);
        const ultimaFalha = Number(estado.last_fail_at || 0);

        el.classList.remove('running', 'ok', 'warn');

        if (emExecucao) {
            el.classList.add('running');
            el.textContent = 'IA RAG: indexando em background...';
            return;
        }

        if (ultimoOk > 0) {
            el.classList.add('ok');
            el.textContent = `IA RAG: indexado ${formatarTempoDecorrido(ultimoOk)}.`;
            return;
        }

        if (ultimaFalha > 0) {
            el.classList.add('warn');
            el.textContent = `IA RAG: ultima tentativa falhou ${formatarTempoDecorrido(ultimaFalha)}.`;
            return;
        }

        el.classList.add('warn');
        el.textContent = 'IA RAG: aguardando primeira indexacao automatica.';
    }

    function lerEstado() {
        try {
            const raw = localStorage.getItem(STORAGE_KEY);
            return raw ? JSON.parse(raw) : {};
        } catch (_e) {
            return {};
        }
    }

    function salvarEstado(patch) {
        try {
            const atual = lerEstado();
            localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...atual, ...patch }));
        } catch (_e) {
            // silencioso
        }
    }

    function deveExecutar(clientId, agora) {
        const estado = lerEstado();
        if (!estado || estado.client_id !== clientId) return true;

        const ultimoSucesso = Number(estado.last_ok_at || 0);
        if (ultimoSucesso > 0 && (agora - ultimoSucesso) < SUCESSO_INTERVALO_MS) {
            return false;
        }

        const ultimaTentativa = Number(estado.last_try_at || 0);
        if (ultimaTentativa > 0 && (agora - ultimaTentativa) < TENTATIVA_MIN_INTERVALO_MS) {
            return false;
        }

        return true;
    }

    async function dispararAutoIndex() {
        if (!obterToken() && !obterClientId()) return;

        const clientId = obterClientId() || 'desconhecido';
        const agora = Date.now();
        if (!deveExecutar(clientId, agora)) return;

        emExecucao = true;
        salvarEstado({ client_id: clientId, last_try_at: agora });
        atualizarIndicador();

        try {
            const resp = await fetch('/api/ia/rag/reindexar', {
                method: 'POST',
                headers: {
                    ...obterAuthHeaders(),
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ force: false })
            });

            if (resp.ok) {
                salvarEstado({
                    client_id: clientId,
                    last_ok_at: Date.now(),
                    last_fail_at: 0,
                    last_fail_msg: ''
                });
            } else {
                let erroMsg = '';
                try {
                    const data = await resp.json();
                    erroMsg = String(data && data.detail ? data.detail : '').slice(0, 180);
                } catch (_e) {
                    erroMsg = resp.statusText || '';
                }
                salvarEstado({
                    client_id: clientId,
                    last_fail_at: Date.now(),
                    last_fail_msg: erroMsg
                });
            }
        } catch (_e) {
            salvarEstado({
                client_id: clientId,
                last_fail_at: Date.now(),
                last_fail_msg: 'falha de conexao'
            });
        } finally {
            emExecucao = false;
            atualizarIndicador();
        }
    }

    const iniciar = () => {
        // Aguarda a página estabilizar para não competir com chamadas críticas de carregamento.
        atualizarIndicador();
        if (!indicadorTimer) {
            indicadorTimer = setInterval(atualizarIndicador, INDICATOR_REFRESH_MS);
        }
        window.addEventListener('storage', (event) => {
            if (!event || event.key === STORAGE_KEY || event.key === 'permissions') {
                atualizarIndicador();
            }
        });
        setTimeout(dispararAutoIndex, 3500);
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', iniciar, { once: true });
    } else {
        iniciar();
    }
})();
