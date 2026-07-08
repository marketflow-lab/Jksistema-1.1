/**
 * Worker da IA Sidebar.
 * Faz prefetch dos chunks e fetch JSON simples fora do thread visual.
 */
(function () {
  'use strict';

  function responder(id, payload) {
    self.postMessage({ id, ok: true, ...(payload || {}) });
  }

  function responderErro(id, error) {
    self.postMessage({
      id,
      ok: false,
      error: error && error.message ? error.message : String(error || 'Erro desconhecido'),
    });
  }

  async function prefetchChunks(files) {
    const lista = Array.isArray(files) ? files : [];
    return Promise.all(lista.map(async (url) => {
      const response = await fetch(String(url), { cache: 'no-store' });
      if (!response.ok) {
        throw new Error('Falha ao carregar ' + url + ': HTTP ' + response.status);
      }
      return response.text();
    }));
  }

  function headersParaObjeto(headers) {
    const out = {};
    try {
      headers.forEach((value, key) => { out[key] = value; });
    } catch (_) {}
    return out;
  }

  async function jsonFetch(url, options) {
    const response = await fetch(String(url), options || {});
    const text = await response.text();
    let json = null;
    let hasJson = false;
    if (text) {
      try {
        json = JSON.parse(text);
        hasJson = true;
      } catch (_) {}
    }
    return {
      responseOk: response.ok,
      status: response.status,
      statusText: response.statusText,
      url: response.url,
      headers: headersParaObjeto(response.headers),
      text,
      json,
      hasJson,
    };
  }

  self.addEventListener('message', async (event) => {
    const message = event.data || {};
    const id = message.id;
    try {
      if (message.type === 'prefetchChunks') {
        responder(id, { parts: await prefetchChunks(message.files) });
        return;
      }
      if (message.type === 'jsonFetch') {
        responder(id, await jsonFetch(message.url, message.options));
        return;
      }
      throw new Error('Tipo de mensagem desconhecido: ' + message.type);
    } catch (error) {
      responderErro(id, error);
    }
  });
})();
