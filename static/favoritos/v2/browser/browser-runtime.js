        const ML_FAVORITOS_MONITORAMENTO_PAGINA_AUTOMATICO = false;


        const ML_FAVORITOS_AVANT_CACHE_KEY = 'jk_favoritos_avant_cache_v1';
        const ML_FAVORITOS_AVANT_CACHE_TTL_MS = 6 * 60 * 60 * 1000;
        const ML_FAVORITOS_AVANT_CACHE_MAX = 1000;
        const ML_FAVORITOS_AVANT_RELOAD_APOS_LOGIN_KEYS = [
            'jk_favoritos_avant_login_retomar_sem_reload',
            'jk_favoritos_avant_pos_login_at'
        ];
        const ML_FAVORITOS_AVANT_LOGIN_RECENTE_MS = 90000;
        const ML_FAVORITOS_AVANT_LOGIN_CONFIRMADO_KEY = 'jk_favoritos_avant_login_confirmado_at';
        let mlFavoritosAvantSnapshotPromise = null;
        let mlFavoritosAvantSnapshotAt = 0;


window.FavoritosV2 = window.FavoritosV2 || {};
window.FavoritosV2.browser = window.FavoritosV2.browser || {};
window.FavoritosV2.browser.runtime = Object.freeze({
  monitoramentoAutomatico: () => ML_FAVORITOS_MONITORAMENTO_PAGINA_AUTOMATICO,
  cacheKey: ML_FAVORITOS_AVANT_CACHE_KEY,
  cacheTtlMs: ML_FAVORITOS_AVANT_CACHE_TTL_MS,
  cacheMax: ML_FAVORITOS_AVANT_CACHE_MAX,
  loginRecenteMs: ML_FAVORITOS_AVANT_LOGIN_RECENTE_MS,
  getSnapshotPromise: () => mlFavoritosAvantSnapshotPromise,
  getSnapshotAt: () => mlFavoritosAvantSnapshotAt
});
