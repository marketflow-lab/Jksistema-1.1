'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const vm = require('vm');
const { fileURLToPath } = require('url');

const root = path.resolve(__dirname, '..');
const openingModule = require(path.join(root, 'electron_app', 'main', 'modules', 'etiquetas-pdf-opening.js'));

function extractFunction(source, name) {
  const marker = `async function ${name}(`;
  const start = source.indexOf(marker);
  assert(start >= 0, `funcao ${name} ausente`);
  const braceStart = source.indexOf('{', start);
  let depth = 0;
  for (let index = braceStart; index < source.length; index += 1) {
    if (source[index] === '{') depth += 1;
    if (source[index] === '}') depth -= 1;
    if (depth === 0) return source.slice(start, index + 1);
  }
  throw new Error(`funcao ${name} incompleta`);
}

async function testMainProcessChromeOpening() {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'jk-etiquetas-open-test-'));
  const pdf = Buffer.from('%PDF-1.4\nconteudo-de-teste');
  let openedUrl = '';

  try {
    const success = await openingModule.openPdfBytesInGoogleChrome(pdf, {
      tempRoot,
      filename: '..\\etiquetas avulsas.pdf',
      cleanupDelayMs: 60_000,
      tryOpenChrome: async (url) => {
        openedUrl = url;
        return { success: true, browser: 'chrome' };
      },
    });
    assert.deepStrictEqual(success, { success: true, browser: 'chrome' });
    assert.match(openedUrl, /^file:\/\//i, 'Chrome deve receber URL file:// do PDF temporario');
    const savedPath = fileURLToPath(openedUrl);
    assert.strictEqual(path.dirname(savedPath), tempRoot, 'PDF deve permanecer na pasta temporaria isolada');
    assert.strictEqual(fs.readFileSync(savedPath).toString(), pdf.toString(), 'bytes do PDF devem ser preservados');

    const unavailable = await openingModule.openPdfBytesInGoogleChrome(pdf, {
      tempRoot,
      filename: 'fallback.pdf',
      tryOpenChrome: async () => ({ success: false, reason: 'chrome-nao-encontrado' }),
    });
    assert.deepStrictEqual(unavailable, { success: false, reason: 'chrome-nao-encontrado' });
    assert.strictEqual(
      fs.readdirSync(tempRoot).filter(name => name.endsWith('-fallback.pdf')).length,
      0,
      'arquivo temporario deve ser removido quando o Chrome nao abre'
    );

    await assert.rejects(
      () => openingModule.openPdfBytesInGoogleChrome(Buffer.from('nao-pdf'), {
        tempRoot,
        tryOpenChrome: async () => ({ success: true }),
      }),
      /nao e um PDF valido/
    );
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
}

async function testRendererFallback() {
  const html = fs.readFileSync(path.join(root, 'frontend_etiquetas.html'), 'utf8');
  const abrirPdfSource = extractFunction(html, 'abrirPdfProtegido');
  const calls = { preview: 0, chrome: 0 };
  const context = {
    console,
    obterArquivoProtegidoBlob: async () => ({ arrayBuffer: async () => new ArrayBuffer(8) }),
    mostrarPreviewAvulsa: async () => { calls.preview += 1; },
    window: {
      electronAPI: {
        openEtiquetasPdfInChrome: async () => {
          calls.chrome += 1;
          return { success: true };
        },
      },
    },
  };
  vm.runInNewContext(`${abrirPdfSource}; globalThis.abrirPdfProtegido = abrirPdfProtegido;`, context);

  const opened = await context.abrirPdfProtegido('pdf-1', 3);
  assert.strictEqual(opened.mode, 'chrome');
  assert.strictEqual(calls.chrome, 1);
  assert.strictEqual(calls.preview, 0, 'previa nao deve aparecer quando o Chrome abre');

  context.window.electronAPI.openEtiquetasPdfInChrome = async () => ({
    success: false,
    reason: 'chrome-nao-encontrado',
  });
  const fallback = await context.abrirPdfProtegido('pdf-2', 4);
  assert.strictEqual(fallback.mode, 'preview');
  assert.strictEqual(calls.preview, 1, 'previa interna deve aparecer quando o Chrome esta ausente');

  assert.match(html, /PDF avulso aberto no Google Chrome\./);
  assert.match(html, /Google Chrome indisponível\. Pré-visualização interna exibida abaixo\./);
  assert.doesNotMatch(abrirPdfSource, /window\.open\(/, 'fluxo corrigido nao deve depender de popup blob');

  for (const preloadPath of ['preload.js', 'electron_tab_preload.js', path.join('electron_app', 'preload.js')]) {
    const preload = fs.readFileSync(path.join(root, preloadPath), 'utf8');
    assert.match(preload, /openEtiquetasPdfInChrome:[\s\S]*open-etiquetas-pdf-in-chrome/, `${preloadPath} deve expor a ponte do PDF`);
  }

  const ipc = fs.readFileSync(path.join(root, 'electron_app', 'main', 'modules', 'ipc.js'), 'utf8');
  assert.match(
    ipc,
    /ipcMain\.handle\('open-etiquetas-pdf-in-chrome'[\s\S]*assertTrustedEtiquetasPdfIpcSender\(event\)[\s\S]*openPdfBytesInGoogleChrome/,
    'IPC do PDF deve validar a pagina de Etiquetas antes de gravar ou abrir o arquivo'
  );
  const packageContract = JSON.parse(fs.readFileSync(path.join(root, 'electron_app', 'installer-required-resources.json'), 'utf8'));
  assert(
    packageContract.requiredPackagedFiles.includes('local_app/electron_app/main/modules/etiquetas-pdf-opening.js'),
    'pacote deve exigir o modulo que abre o PDF no Chrome'
  );
}

(async () => {
  await testMainProcessChromeOpening();
  await testRendererFallback();
  console.log('etiquetas_pdf_opening: OK');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
