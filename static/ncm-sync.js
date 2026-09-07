/**
 * Sistema global de sincronização de NCM em background
 * Sincroniza estado entre abas via StorageEvent e localStorage
 */

const NCM_SYNC = {
    // Configuração
    LOCAL_STORAGE_KEY: 'ncm_sync_job',
    POLL_INTERVAL: 3000, // 3 segundos
    
    // Estado
    jobId: null,
    storeId: null,
    progressTimer: null,
    statusCallback: null,
    onCompleteCallback: null,
    
    /**
     * Inicializa o sistema: setup de listeners e recuperação de job anterior
     */
    init(statusCallback, onCompleteCallback) {
        this.statusCallback = statusCallback;
        this.onCompleteCallback = onCompleteCallback;
        
        // Listener para mudanças em outras abas
        window.addEventListener('storage', (e) => {
            if (e.key === this.LOCAL_STORAGE_KEY) {
                const novoJob = this._parseStoredJob(e.newValue);
                if (!novoJob) {
                    this._discardJob(false);
                    return;
                }
                if (novoJob && novoJob.jobId && novoJob.clientId === this._getClientId()) {
                    this.jobId = novoJob.jobId;
                    this.storeId = novoJob.storeId || null;
                    console.log('[NCM-SYNC] Job detectado de outra aba:', this.jobId);
                    this.startMonitoring();
                } else {
                    this._discardJob(false);
                }
            }
        });
        
        // Recuperar job anterior se existir
        const stored = localStorage.getItem(this.LOCAL_STORAGE_KEY);
        if (stored) {
            const data = this._parseStoredJob(stored);
            if (data && data.jobId && data.status === 'running' && data.clientId === this._getClientId()) {
                this.jobId = data.jobId;
                this.storeId = data.storeId || null;
                console.log('[NCM-SYNC] Recuperando job anterior:', this.jobId);
                this.startMonitoring();
            } else if (!data || !data.clientId || data.clientId === this._getClientId()) {
                // Jobs legados, concluidos ou invalidos nao podem permanecer
                // visiveis depois que o backend que os mantinha em memoria reinicia.
                this._discardJob();
            }
        }
    },
    
    /**
     * Inicia uma nova sincronização NCM
     */
    async iniciarSincronizacao(storeId) {
        const storeIdSeguro = String(storeId || '').trim();
        if (!storeIdSeguro) throw new Error('Selecione uma loja especifica para sincronizar NCM.');
        if (this.jobId && this.isRunning()) {
            if (this.storeId && this.storeId !== storeIdSeguro) {
                throw new Error('Ja existe uma sincronizacao NCM em andamento para outra loja.');
            }
            console.log('[NCM-SYNC] Sincronização já em andamento:', this.jobId);
            return this.jobId;
        }
        
        try {
            const params = new URLSearchParams({ store_id: storeIdSeguro });
            const resp = await fetch(`/api/cadastro/sync-ncm/iniciar?${params.toString()}`, {
                method: 'POST',
                headers: this._getAuthHeaders()
            });
            
            if (!resp.ok) {
                const err = await resp.json();
                throw new Error(err.detail || `HTTP ${resp.status}`);
            }
            
            const data = await resp.json();
            this.jobId = data.job_id;
            this.storeId = storeIdSeguro;
            
            // Guardar no localStorage para outras abas
            localStorage.setItem(this.LOCAL_STORAGE_KEY, JSON.stringify({
                jobId: this.jobId,
                clientId: this._getClientId(),
                storeId: this.storeId,
                status: 'running',
                startTime: Date.now()
            }));
            
            console.log('[NCM-SYNC] Sincronização iniciada:', this.jobId);
            this.startMonitoring();
            return this.jobId;
        } catch (e) {
            console.error('[NCM-SYNC] Erro ao iniciar sincronização:', e);
            this._renderError(e.message);
            throw e;
        }
    },
    
    /**
     * Inicia polling de progresso
     */
    startMonitoring() {
        if (this.progressTimer) {
            clearInterval(this.progressTimer);
        }
        
        // Fazer requisição imediata
        this._fetchProgress();
        
        // Depois fazer polling
        this.progressTimer = setInterval(() => {
            this._fetchProgress();
        }, this.POLL_INTERVAL);
    },
    
    /**
     * Para o monitoramento
     */
    stopMonitoring() {
        if (this.progressTimer) {
            clearInterval(this.progressTimer);
            this.progressTimer = null;
        }
    },
    
    /**
     * Fetch do progresso do job
     */
    async _fetchProgress() {
        if (!this.jobId) return;
        
        try {
            const resp = await fetch(`/api/cadastro/sync-ncm/progresso/${this.jobId}`, {
                headers: this._getAuthHeaders()
            });
            
            if (!resp.ok) {
                if (resp.status === 403 || resp.status === 404 || resp.status === 410) {
                    console.warn('[NCM-SYNC] Job anterior nao esta mais disponivel:', resp.status);
                    this._discardJob();
                    return;
                }
                console.error('[NCM-SYNC] Erro ao buscar progresso:', resp.status);
                return;
            }
            
            const progress = await resp.json();
            
            // Atualizar localStorage para outras abas
            localStorage.setItem(this.LOCAL_STORAGE_KEY, JSON.stringify({
                jobId: this.jobId,
                clientId: this._getClientId(),
                storeId: this.storeId,
                status: progress.status,
                progress: progress,
                lastUpdate: Date.now()
            }));
            
            this._renderProgress(progress);
            
            // Se terminou, parar monitoramento
            if (progress.status === 'done' || progress.status === 'error') {
                this.stopMonitoring();
                setTimeout(() => {
                    this.jobId = null;
                    this.storeId = null;
                    localStorage.removeItem(this.LOCAL_STORAGE_KEY);
                    if (this.onCompleteCallback) {
                        this.onCompleteCallback(progress.status);
                    }
                }, 2000);
            }
        } catch (e) {
            console.error('[NCM-SYNC] Erro ao buscar progresso:', e);
        }
    },
    
    /**
     * Renderiza o progresso no elemento de status
     */
    _renderProgress(progress) {
        if (!this.statusCallback) return;
        
        const status = progress.status || 'running';
        const mensagem = progress.mensagem || 'Sincronizando NCM e classificação monofásica...';
        const processados = progress.processados || 0;
        const total = progress.total || 0;
        const encontrados = progress.encontrados || 0;
        
        let percent = 0;
        if (total > 0) {
            percent = Math.round((processados / total) * 100);
        }
        
        const html = `
            <span class="spinner"></span>
            <strong>${mensagem}</strong>
            <div style="margin-top:8px;background:#222;border-radius:6px;overflow:hidden;">
                <div style="height:8px;width:${percent}%;background:#4facfe;transition:width 0.3s ease;"></div>
            </div>
            <div style="margin-top:6px;font-size:0.85rem;color:#9bd1ff;">
                ${percent}% | ${processados}/${total} produtos | ${encontrados} NCMs encontrados
            </div>
        `;
        
        this.statusCallback(html, 'loading');
    },
    
    /**
     * Renderiza erro
     */
    _renderError(message) {
        if (!this.statusCallback) return;
        this.statusCallback(`❌ Erro: ${message}`, 'error');
    },

    _parseStoredJob(raw) {
        if (!raw) return null;
        try {
            const data = JSON.parse(raw);
            return data && typeof data === 'object' ? data : null;
        } catch (_error) {
            return null;
        }
    },

    _discardJob(removeStored = true) {
        this.stopMonitoring();
        this.jobId = null;
        this.storeId = null;
        if (removeStored) localStorage.removeItem(this.LOCAL_STORAGE_KEY);
        if (this.statusCallback) this.statusCallback('', '');
    },
    
    /**
     * Verifica se sincronização está em andamento
     */
    isRunning() {
        if (!this.jobId) return false;
        const stored = localStorage.getItem(this.LOCAL_STORAGE_KEY);
        if (!stored) return false;
        const data = this._parseStoredJob(stored);
        if (!data) return false;
        return data.status === 'running'
            && data.clientId === this._getClientId()
            && (!this.storeId || data.storeId === this.storeId);
    },

    _getClientId() {
        try {
            const user = JSON.parse(localStorage.getItem('user_data') || 'null');
            return String(user && user.client_id || '').trim();
        } catch (_error) {
            return '';
        }
    },
    
    /**
     * Obtém cabeçalhos de autenticação
     */
    _getAuthHeaders() {
        if (typeof obterAuthHeaders === 'function') {
            return obterAuthHeaders();
        }
        // Fallback
        const token = localStorage.getItem('access_token');
        if (token) {
            return { 'Authorization': `Bearer ${token}` };
        }
        return {};
    }
};

// Auto-exportar para uso global
if (typeof window !== 'undefined') {
    window.NCM_SYNC = NCM_SYNC;
}
