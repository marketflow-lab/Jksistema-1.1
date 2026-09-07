/**
 * Status global NCM - Injeta balão de status em todas as páginas
 * Executa automaticamente quando a página carrega
 */

(function() {
    'use strict';
    
    // Aguardar que auth.js e ncm-sync.js estejam carregados
    function waitForDependencies(callback) {
        let checks = 0;
        const maxChecks = 50; // 5 segundos com interval de 100ms
        
        const interval = setInterval(() => {
            checks++;
            if (typeof obterAuthHeaders === 'function' && typeof NCM_SYNC === 'object') {
                clearInterval(interval);
                callback();
            } else if (checks >= maxChecks) {
                clearInterval(interval);
                console.warn('[GLOBAL-STATUS] Timeout aguardando dependências');
            }
        }, 100);
    }
    
    function init() {
        // 1. Verificar se statusNcmGlobal já existe
        let statusNcmEl = document.getElementById('statusNcmGlobal');
        
        if (!statusNcmEl) {
            // Criar elemento status NCM global
            statusNcmEl = document.createElement('div');
            statusNcmEl.id = 'statusNcmGlobal';
            statusNcmEl.className = 'status-bar';
            statusNcmEl.style.cssText = `
                display: none;
                margin: 10px 0;
                padding: 10px 12px;
                border-radius: 8px;
                background: #161925;
                border: 1px solid #222;
                min-height: 20px;
            `;
            
            // Tentar inserir no topo do container principal ou no body
            const container = document.querySelector('.container') || 
                            document.querySelector('main') || 
                            document.body;
            
            if (container && container.firstChild) {
                container.insertBefore(statusNcmEl, container.firstChild);
            } else {
                container.appendChild(statusNcmEl);
            }
        }
        
        // 2. Adicionar estilos CSS se não existirem
        if (!document.getElementById('global-status-styles')) {
            const style = document.createElement('style');
            style.id = 'global-status-styles';
            style.textContent = `
                .status-bar.loading { 
                    color: #9bd1ff; 
                    border-color: #4facfe; 
                    background: #0a1428 !important; 
                }
                .status-bar.error { 
                    color: #ff6b6b; 
                    border-color: #c62828; 
                    background: #28010d !important; 
                }
                .status-bar.success { 
                    color: #66bb6a; 
                    border-color: #1b5e20; 
                    background: #0f2e0d !important; 
                }
                .spinner { 
                    display: inline-block; 
                    width: 14px; 
                    height: 14px; 
                    border: 2px solid #4facfe; 
                    border-top-color: transparent; 
                    border-radius: 50%; 
                    animation: spin 0.8s linear infinite; 
                    margin-right: 8px; 
                    vertical-align: middle; 
                }
                @keyframes spin { 
                    to { transform: rotate(360deg); } 
                }
            `;
            document.head.appendChild(style);
        }
        
        // 3. Função de callback para atualizar status
        function setStatusNcmGlobal(html, cls) {
            if (!html) {
                statusNcmEl.style.display = 'none';
                return;
            }
            
            statusNcmEl.innerHTML = html;
            statusNcmEl.className = 'status-bar ' + (cls || '');
            statusNcmEl.style.display = 'block';
        }
        
        // 4. Função de callback para quando sincronização termina
        function onNcmSyncComplete(status) {
            if (status === 'done') {
                setStatusNcmGlobal('✅ Sincronização NCM concluída!', 'success');
                setTimeout(() => {
                    setStatusNcmGlobal('', '');
                    // Se estiver em cadastro, recarregar produtos
                    if (typeof carregarProdutos === 'function') {
                        carregarProdutos();
                    }
                }, 2000);
            } else if (status === 'error') {
                setStatusNcmGlobal('❌ Sincronização NCM finalizou com erro.', 'error');
            }
        }
        
        // 5. Inicializar NCM_SYNC se não estiver já inicializado
        if (NCM_SYNC && typeof NCM_SYNC.init === 'function') {
            // Verificar se já foi inicializado
            if (!NCM_SYNC.statusCallback) {
                NCM_SYNC.init(setStatusNcmGlobal, onNcmSyncComplete);
                console.log('[GLOBAL-STATUS] NCM_SYNC inicializado');
                
                // Se teve sincronização anterior, recuperar e mostrar
                const stored = localStorage.getItem('ncm_sync_job');
                if (stored) {
                    const data = NCM_SYNC._parseStoredJob(stored);
                    if (data && data.jobId === NCM_SYNC.jobId && NCM_SYNC.isRunning() && data.progress) {
                        // Renderizar progresso imediatamente
                        const p = data.progress;
                        const total = p.total || 0;
                        const processados = p.processados || 0;
                        const encontrados = p.encontrados || 0;
                        let percent = 0;
                        if (total > 0) {
                            percent = Math.round((processados / total) * 100);
                        }
                        
                        const html = `
                            <span class="spinner"></span>
                            <strong>${p.mensagem || 'Sincronizando NCM...'}</strong>
                            <div style="margin-top:8px;background:#222;border-radius:6px;overflow:hidden;">
                                <div style="height:8px;width:${percent}%;background:#4facfe;transition:width 0.3s ease;"></div>
                            </div>
                            <div style="margin-top:6px;font-size:0.85rem;color:#9bd1ff;">
                                ${percent}% | ${processados}/${total} produtos | ${encontrados} NCMs encontrados
                            </div>
                        `;
                        
                        setStatusNcmGlobal(html, 'loading');
                        console.log('[GLOBAL-STATUS] Recuperada sincronização em andamento');
                    }
                }
            }
        }
        
        console.log('[GLOBAL-STATUS] Sistema inicializado com sucesso');
    }
    
    // Iniciar quando o DOM estiver pronto
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            waitForDependencies(init);
        });
    } else {
        waitForDependencies(init);
    }
})();
