'use strict';

const assert = require('assert');
const vm = require('vm');
const { read } = require('./helpers/cadastro_frontend_sources');

function input(value = '') {
  return { value };
}

function createRows(initial) {
  const rows = { children: [] };
  function addRow(id = '', freight = '') {
    const idInput = input(id);
    const freightInput = input(freight);
    const row = {
      querySelector(selector) {
        if (selector === '.mlb-id') return idInput;
        if (selector === '.mlb-frete') return freightInput;
        return null;
      },
      remove() {
        const index = rows.children.indexOf(row);
        if (index >= 0) rows.children.splice(index, 1);
      },
    };
    rows.children.push(row);
    return row;
  }
  rows.querySelectorAll = selector => selector === '.mlb-row' ? [...rows.children] : [];
  initial.forEach(item => addRow(item.id, item.freight));
  return { rows, addRow };
}

(async () => {
  const requests = [];
  const responsePayload = {
    success: true,
    read_only: true,
    coverage_complete: true,
    store_id: 'store-a',
    sku: 'SKU 1/2',
    campos: {
      mlb_ids: 'MLB100|MLB200',
      titulo_ml: '<img src=x onerror=alert(1)>',
      categoria: 'Autopeças',
      descricao: '<script>window.evil=true</script>',
      qtd_anuncios_mlb: '2',
    },
    foto: {
      url: 'https://http2.mlstatic.com/photo.jpg',
      data_url: 'data:image/jpeg;base64,/9j/',
      filename: 'MLB100.jpg',
    },
    avisos: ['Marca divergente; revise.'],
  };
  const context = {
    URLSearchParams,
    document: {
      createElement(tagName) { return { tagName, src: '', alt: '', className: '', textContent: '' }; },
    },
    fetch: async (url, options) => {
      requests.push({ url, options });
      return { ok: true, status: 200, json: async () => responsePayload };
    },
  };
  context.window = context;
  vm.createContext(context);
  const source = read('static/cadastro/form/01-mercado-livre.js');
  vm.runInContext(source, context, { filename: 'static/cadastro/form/01-mercado-livre.js' });
  const tools = context.JKCadastroMercadoLivre;
  const storeTools = {
    apiLoja(storeId, suffix) {
      return `/api/cadastro/lojas/${encodeURIComponent(storeId)}/${suffix}`;
    },
  };

  const payload = await tools.consultar({
    storeTools,
    storeId: 'store-a',
    sku: 'SKU 1/2',
    mlbPrincipal: 'MLB200',
    authHeaders: () => ({ Authorization: 'Bearer local' }),
  });
  assert.strictEqual(payload, responsePayload);
  assert.strictEqual(requests.length, 1);
  assert.strictEqual(requests[0].options.method, 'GET');
  assert.strictEqual(requests[0].options.body, undefined, 'consulta não pode enviar mutação');
  const url = new URL(requests[0].url, 'https://jk.local');
  assert.strictEqual(url.pathname, '/api/cadastro/lojas/store-a/mercado-livre/produto');
  assert.strictEqual(url.searchParams.get('sku'), 'SKU 1/2');
  assert.strictEqual(url.searchParams.get('mlb_principal'), 'MLB200');
  assert.strictEqual(requests[0].options.headers.Authorization, 'Bearer local');

  const { rows, addRow } = createRows([
    { id: 'MLB200', freight: '22,00' },
    { id: 'MLB999', freight: '99,00' },
  ]);
  const add = { click: () => addRow() };
  const fields = {
    titulo_ml: input(),
    categoria: input(),
    descricao: input(),
    qtd_anuncios_mlb: input(),
  };
  const container = {
    querySelector(selector) {
      if (selector === '.mlb-rows') return rows;
      if (selector === '.mlb-add') return add;
      const match = /^\[name="([^"]+)"\]$/.exec(selector);
      return match ? fields[match[1]] || null : null;
    },
  };
  const applied = Array.from(tools.aplicarCampos(container, responsePayload.campos));
  assert.deepStrictEqual(applied, ['mlb_ids', 'qtd_anuncios_mlb', 'titulo_ml', 'categoria', 'descricao']);
  assert.strictEqual(rows.children.length, 2);
  assert.strictEqual(rows.children[0].querySelector('.mlb-id').value, 'MLB100');
  assert.strictEqual(rows.children[0].querySelector('.mlb-frete').value, '');
  assert.strictEqual(rows.children[1].querySelector('.mlb-id').value, 'MLB200');
  assert.strictEqual(rows.children[1].querySelector('.mlb-frete').value, '22,00', 'frete do MLB existente deve ser preservado');
  assert.strictEqual(fields.titulo_ml.value, '<img src=x onerror=alert(1)>');
  assert.strictEqual(fields.descricao.value, '<script>window.evil=true</script>');
  assert.strictEqual(context.evil, undefined, 'texto da API não pode executar HTML ou script');
  assert.doesNotMatch(source, /innerHTML/, 'módulo ML deve aplicar texto sem HTML dinâmico');

  assert.deepStrictEqual(
    JSON.parse(JSON.stringify(tools.foto(responsePayload))),
    {
      dataUrl: 'data:image/jpeg;base64,/9j/',
      filename: 'MLB100.jpg',
      url: 'https://http2.mlstatic.com/photo.jpg',
    },
  );
  assert.strictEqual(tools.foto({ foto: { data_url: 'data:text/html;base64,PHNjcmlwdD4=', url: 'javascript:alert(1)' } }).dataUrl, '');
  assert.strictEqual(tools.foto({ foto: { data_url: 'data:image/png;base64,x" onerror="window.evil=true"' } }).dataUrl, '');
  const preview = { children: [], replaceChildren(...children) { this.children = children; } };
  tools.renderizarFotoPendente(preview, responsePayload.foto.data_url);
  assert.strictEqual(preview.children[0].src, responsePayload.foto.data_url);
  assert.strictEqual(preview.children[1].textContent, 'Imagem pendente. Será salva somente ao confirmar.');
  assert.match(tools.resumo(responsePayload, applied), /Revise e confirme no botão de salvar/);

  context.fetch = async () => ({
    ok: true,
    status: 200,
    json: async () => ({ ...responsePayload, store_id: 'store-b' }),
  });
  await assert.rejects(
    tools.consultar({ storeTools, storeId: 'store-a', sku: 'SKU 1/2', authHeaders: () => ({}) }),
    /não corresponde à loja e ao SKU/,
  );

  const rowsBeforePartial = rows.children.map(row => [row.querySelector('.mlb-id').value, row.querySelector('.mlb-frete').value]);
  context.fetch = async () => ({
    ok: true,
    status: 200,
    json: async () => ({ ...responsePayload, coverage_complete: false, campos: { ...responsePayload.campos, mlb_ids: 'MLB100' } }),
  });
  await assert.rejects(
    tools.consultar({ storeTools, storeId: 'store-a', sku: 'SKU 1/2', authHeaders: () => ({}) }),
    /consulta do Mercado Livre ficou incompleta/,
  );
  assert.deepStrictEqual(
    rows.children.map(row => [row.querySelector('.mlb-id').value, row.querySelector('.mlb-frete').value]),
    rowsBeforePartial,
    'resposta parcial não pode remover MLB ou frete existente',
  );

  const includeHtml = read('static/cadastro_incluir.html');
  const editHtml = read('static/cadastro_editar_item.html');
  const includeSource = read('static/cadastro/incluir-item.js');
  const editSource = read('static/cadastro/editar-item.js');
  [includeHtml, editHtml].forEach(html => {
    assert.match(html, /id="btnBuscarMercadoLivre"[^>]*type="button"/);
    assert.match(html, /form\/00-core\.js[\s\S]*form\/01-mercado-livre\.js/);
  });
  assert.match(includeSource, /btnBuscarMercadoLivre\.addEventListener\('click', buscarDadosMercadoLivre\)/);
  assert.match(editSource, /btnBuscarMercadoLivre\.addEventListener\('click', buscarDadosMercadoLivre\)/);
  assert.match(includeSource, /consultaMercadoLivreSeq[\s\S]*requestSeq !== consultaMercadoLivreSeq/);
  assert.match(editSource, /consultaMercadoLivreSeq[\s\S]*requestSeq !== consultaMercadoLivreSeq/);
  assert.match(editSource, /renderizarFotoPendente\(elements\.fotoPreview, state\.fotoDataUrl\)/);
  assert.match(editSource, /Selecione uma loja específica para editar\.[\s\S]*habilitarEdicao\(state\.carregadoDaRede\)/);
  assert.match(includeSource, /__foto_data_url/);
  assert.match(editSource, /__foto_data_url/);
  console.log('cadastro mercado livre frontend: OK');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
