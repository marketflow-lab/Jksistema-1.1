const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');

function read(relativePath) {
    return fs.readFileSync(path.join(root, relativePath), 'utf8');
}

function compileInlineScripts(relativePath) {
    const html = read(relativePath);
    const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
        .map(match => match[1])
        .filter(source => source.trim());
    scripts.forEach((source, index) => {
        assert.doesNotThrow(
            () => new Function(source),
            `${relativePath}: script inline ${index + 1} possui erro de sintaxe`,
        );
    });
    return html;
}

const admin = compileInlineScripts('static/admin_usuarios.html');
const integrations = compileInlineScripts('static/integracoes.html');
const settings = compileInlineScripts('static/configuracoes.html');
const sharedSyncBoot = read('static/auth/shared-sync-boot.js');
assert.doesNotThrow(() => new Function(sharedSyncBoot), 'shared-sync-boot.js possui erro de sintaxe');

assert(admin.includes('machineSyncProgressOverlay'), 'balão local da sincronização ausente');
assert(admin.includes('atualizarBalaoSincronizacaoMaquinas'), 'controle do balão da sincronização ausente');
assert(admin.includes("await recarregarLojasAposSincronizacao(['lojas_integracoes']);"), 'recarga de lojas após importação ausente');
assert(admin.includes('machineSyncActionStatus'), 'resultado da sincronização precisa permanecer visível no próprio painel');
assert(admin.includes('machineSyncKeyringStatus'), 'estado da criptografia segura precisa permanecer visível no painel');
assert(admin.includes('Criptografia segura pronta nesta máquina.'), 'estado seguro pronto não é explicado ao usuário');
assert(admin.includes('Na máquina de origem, clique em Enviar agora novamente.'), 'pareamento da máquina receptora não orienta um novo envio na origem');
assert(admin.includes('O cofre seguro do Windows está indisponível nesta máquina.'), 'falha do cofre seguro não é mostrada claramente');
assert(admin.includes('Sincronização parcial.'), 'falhas parciais precisam ser informadas sem ocultar os itens concluídos');
assert(admin.includes('Configurar Firebase nesta máquina'), 'painel local de provisionamento Firebase ausente');
assert(admin.includes('id="firebaseProvisioningFile"') && admin.includes('type="file"') && admin.includes('accept=".json,application/json"'), 'seletor local de JSON Firebase ausente');
assert(admin.includes('id="machineSyncPushButton"') && admin.includes('id="machineSyncPullButton"'), 'ações manuais da sincronização perderam o bloqueio nomeado');
const firebaseProvisioningStart = admin.indexOf('function normalizarStatusProvisionamentoFirebase(');
const firebaseProvisioningEnd = admin.indexOf('function renderSincronizacaoMinhasMaquinas(', firebaseProvisioningStart);
assert(firebaseProvisioningStart >= 0 && firebaseProvisioningEnd > firebaseProvisioningStart, 'fluxo frontend de provisionamento Firebase ausente');
const firebaseProvisioningSource = admin.slice(firebaseProvisioningStart, firebaseProvisioningEnd);
const firebaseProvisioningPanelStart = admin.indexOf('id="firebaseProvisioningPanel"');
const firebaseProvisioningPanelEnd = admin.indexOf('<label class="check-item">', firebaseProvisioningPanelStart);
const firebaseProvisioningCopy = admin.slice(firebaseProvisioningPanelStart, firebaseProvisioningPanelEnd) + firebaseProvisioningSource;
assert(firebaseProvisioningCopy.includes('local padronizado') && firebaseProvisioningCopy.includes('salva somente nesta máquina'), 'UI precisa descrever com precisão o armazenamento local padronizado');
assert(!/armazenamento protegido|credencial protegida|protegendo a credencial|validada e protegida/i.test(firebaseProvisioningCopy), 'UI não pode prometer cofre ou ACL inexistente para a credencial Firebase');
assert(firebaseProvisioningSource.includes("fetch('/api/admin/firebase-provisioning/status'"), 'status Firebase não usa o endpoint administrativo local');
const firebaseStatusLoaderStart = firebaseProvisioningSource.indexOf('async function carregarStatusProvisionamentoFirebase(');
const firebaseStatusLoaderEnd = firebaseProvisioningSource.indexOf('async function enviarArquivoProvisionamentoFirebase(', firebaseStatusLoaderStart);
const firebaseStatusLoaderSource = firebaseProvisioningSource.slice(firebaseStatusLoaderStart, firebaseStatusLoaderEnd);
assert(firebaseStatusLoaderSource.includes('if (!usuarioAdmin)'), 'todo administrador precisa consultar o status sanitizado, inclusive admin_usuarios sem full');
assert(!firebaseStatusLoaderSource.includes('if (!usuarioPodeGerenciarUsuarios)'), 'consulta do status não pode ficar restrita à permissão full');
assert(firebaseProvisioningSource.includes("'/api/admin/firebase-provisioning/import?replace=' + (substituir ? 'true' : 'false')"), 'importação Firebase não materializa replace explicitamente na query');
assert(firebaseProvisioningSource.includes("formData.append('file', arquivo, 'credencial-firebase.json')"), 'arquivo Firebase não é enviado com nome neutro no único campo multipart permitido');
assert(firebaseProvisioningSource.includes("fetch('/api/admin/firebase-provisioning/migrate-legacy'"), 'migração da credencial Firebase legada ausente');
assert(firebaseProvisioningSource.includes("confirm('Já existe uma credencial Firebase nesta máquina."), 'substituição da credencial Firebase perdeu a confirmação explícita');
assert(firebaseProvisioningSource.includes('panel.hidden = !usuarioPodeGerenciarUsuarios || (pronto && !firebaseProvisioningStatus.can_migrate);'), 'provisionamento Firebase pode aparecer para usuário sem permissão full ou esconder migração disponível');
assert(firebaseProvisioningSource.includes('if (!usuarioPodeGerenciarUsuarios || firebaseProvisioningBusy) return;'), 'importação e migração precisam continuar exclusivas de full');
assert(firebaseProvisioningSource.includes('importArea.hidden = firebaseProvisioningStatus.loaded && firebaseProvisioningStatus.ready;'), 'importação de arquivo precisa desaparecer quando o backend confirma ready=true');
assert(firebaseProvisioningSource.includes('migrateButton.hidden = !firebaseProvisioningStatus.can_migrate;'), 'ação de migração precisa seguir exclusivamente can_migrate');
assert(firebaseProvisioningSource.includes("button.disabled = !pronto;"), 'ações Enviar/Importar não são desabilitadas enquanto o Firebase não está pronto');
assert(admin.includes('data-firebase-sync-action') && firebaseProvisioningSource.includes("document.querySelectorAll('[data-firebase-sync-action]')"), 'ações manuais entre usuários não seguem o bloqueio de prontidão Firebase');
assert(firebaseProvisioningSource.includes("if ((pronto || firebaseProvisioningStatus.ready) && input) input.value = '';"), 'arquivo selecionado precisa sair da memória da tela quando o Firebase fica pronto');
assert(!firebaseProvisioningSource.includes('FileReader'), 'frontend não pode ler o conteúdo da credencial Firebase');
assert(!firebaseProvisioningSource.includes('arquivo.name') && !firebaseProvisioningSource.includes('file.name'), 'frontend não pode mostrar ou transmitir o nome original da credencial Firebase');
assert(!firebaseProvisioningSource.includes('data.detail') && !firebaseProvisioningSource.includes('data.message'), 'frontend de provisionamento não pode mostrar respostas brutas do backend');
assert(firebaseProvisioningSource.includes('firebase_permissions_insufficient:'), 'UI deve explicar permissões Firebase insuficientes sem mascará-las como falha de autorização do administrador');
assert(firebaseProvisioningSource.includes('firebase_permissions_test_failed:'), 'UI deve explicar falha na conferência das permissões Firebase sem expor detalhes brutos');
const firebaseReadinessMatch = admin.match(/function resolverProntidaoSincronizacaoFirebase\([^)]*\)\s*\{[\s\S]*?\n\s*\}/);
assert(firebaseReadinessMatch, 'resolver puro de prontidão Firebase ausente');
const resolverProntidaoSincronizacaoFirebase = new Function(`return (${firebaseReadinessMatch[0]});`)();
assert.strictEqual(resolverProntidaoSincronizacaoFirebase('local', { loaded: true, ready: true }, true), false, 'backend local não pode liberar Enviar/Importar');
assert.strictEqual(resolverProntidaoSincronizacaoFirebase('firebase', { loaded: true, ready: false }, true), false, 'status carregado sem ready não pode liberar Enviar/Importar');
assert.strictEqual(resolverProntidaoSincronizacaoFirebase('firebase', { loaded: true, ready: true, restart_required: true }, true), false, 'reinício pendente não pode liberar Enviar/Importar');
assert.strictEqual(resolverProntidaoSincronizacaoFirebase('firebase', { loaded: true, ready: true, restart_required: false }, true), true, 'ready=true precisa liberar Enviar/Importar');
assert.strictEqual(resolverProntidaoSincronizacaoFirebase('firebase', { loaded: false, unavailable: false, ready: false }, true), false, 'consulta ainda pendente não pode liberar Enviar/Importar antes de ready=true');
assert.strictEqual(resolverProntidaoSincronizacaoFirebase('firebase', { loaded: false, unavailable: true, ready: false }, true), true, '404 do endpoint novo durante atualização parcial não pode bloquear backend Firebase já ativo');
const firebaseReadinessWrapperStart = admin.indexOf('function sincronizacaoFirebasePronta()');
const firebaseReadinessWrapperEnd = admin.indexOf('function atualizarInterfaceProvisionamentoFirebase()', firebaseReadinessWrapperStart);
const firebaseReadinessWrapper = admin.slice(firebaseReadinessWrapperStart, firebaseReadinessWrapperEnd);
assert(firebaseReadinessWrapper.includes('usuarioAdmin'), 'readiness de administrador precisa abranger full e admin_usuarios');
assert(!firebaseReadinessWrapper.includes('usuarioPodeGerenciarUsuarios'), 'readiness não pode depender exclusivamente da permissão full');
const adminStatusLoads = admin.match(/if \(usuarioAdmin\) \{\s*await carregarStatusProvisionamentoFirebase\(\{ silencioso: true \}\);/g) || [];
assert(adminStatusLoads.length >= 2, 'status sanitizado precisa ser consultado no sucesso e na falha do status de sincronização para todo administrador');
const manualMachineSyncStart = admin.indexOf('async function executarSincronizacaoMinhasMaquinas(');
const manualMachineSyncEnd = admin.indexOf('async function carregarUsuariosParaCompartilhar(', manualMachineSyncStart);
const manualMachineSyncSource = admin.slice(manualMachineSyncStart, manualMachineSyncEnd);
assert(manualMachineSyncSource.includes('if (!sincronizacaoFirebasePronta())'), 'chamada programática ainda pode sincronizar sem Firebase pronto');
const manualUserSyncStart = admin.indexOf('async function executarVinculoCompartilhamentoUsuario(');
const manualUserSyncEnd = admin.indexOf('function enviarVinculoCompartilhamentoUsuario(', manualUserSyncStart);
assert(admin.slice(manualUserSyncStart, manualUserSyncEnd).includes('if (!sincronizacaoFirebasePronta())'), 'vínculo entre usuários ainda pode sincronizar sem Firebase pronto');
assert(admin.includes('Feche completamente o JK Sistema, abra novamente e clique em Atualizar status.'), 'sucesso do provisionamento não orienta reinício e nova conferência');
assert(integrations.includes('sharedSyncScreenOverlay'), 'balão central da Central de Integrações ausente');
assert(integrations.includes('window.jkIntegracoesSetSyncProgress'), 'ponte de status do iframe ausente');
assert(integrations.includes('window.jkIntegracoesReloadStores'), 'ponte de recarga das lojas ausente');
assert(integrations.includes('throw error;'), 'falha ao recarregar lojas precisa chegar ao fluxo de importaÃ§Ã£o');
assert(integrations.includes('void loadStores().catch(() => {});'), 'carga inicial precisa tratar a rejeiÃ§Ã£o localmente');
assert(settings.includes("defaultSeverity: 'blocker'"), 'bloqueios do Context Hub perderam sua severidade');
assert(settings.includes('const blockers = findings.filter(finding => finding.blocking);'), 'avisos e bloqueios do Context Hub continuam misturados');
assert.strictEqual(admin, read('admin_usuarios.html'), 'admin_usuarios.html divergiu do espelho oficial');
assert.strictEqual(integrations, read('integracoes.html'), 'integracoes.html divergiu do espelho oficial');
assert.strictEqual(settings, read('configuracoes.html'), 'configuracoes.html divergiu do espelho oficial');

assert(admin.includes('Atualizar status'), 'botao precisa deixar claro que apenas atualiza o status');
assert(admin.includes('Receber automaticamente nesta máquina'), 'opt-in de recebimento automatico ausente');
assert(admin.includes('auto_pull: enabled'), 'configuracao precisa acompanhar o opt-in de recebimento');
assert(admin.includes('auto_push: false'), 'envio automatico nao pode ser habilitado pela tela');
assert(admin.includes('item.pending_receive === true'), 'status de recebimento pendente nao e exibido');
assert(admin.includes('item.last_received_at'), 'ultimo recebimento nao e exibido');
assert(admin.includes('MACHINE_SYNC_STATUS_INTERVAL_MS = 60 * 1000'), 'polling de status perdeu o intervalo minimo');
assert(admin.includes("window.addEventListener('focus', atualizarStatusSincronizacaoMaquinasAoRetomar)"), 'status nao atualiza ao retomar a janela');
assert(admin.includes("window.addEventListener('jk:machine-sync-updated'"), 'status nao reage ao auto-pull');
assert(admin.includes('Status anterior descartado.'), 'falha de atualizacao ainda pode manter metadados obsoletos');
assert(integrations.includes("loadStores({ cache: 'no-store' })"), 'recarga de lojas apos pull precisa ignorar cache');
assert(integrations.includes("window.addEventListener('jk:machine-sync-updated'"), 'Central de Integracoes nao reage aos dados recebidos');

const userShareAutoStart = sharedSyncBoot.indexOf('(function initSharedSyncAutoPull');
const userShareAutoReturn = sharedSyncBoot.indexOf('    return;', userShareAutoStart);
assert(userShareAutoStart >= 0 && userShareAutoReturn > userShareAutoStart, 'compartilhamento automatico entre usuarios perdeu o bloqueio manual');
assert(userShareAutoReturn < sharedSyncBoot.indexOf('/api/shared-sync/auto-pull', userShareAutoStart), 'auto-pull entre usuarios foi reativado indevidamente');
const machineAutoStart = sharedSyncBoot.indexOf('(function initMachineSharedSyncAuto');
const machineAutoSource = sharedSyncBoot.slice(machineAutoStart);
assert(machineAutoStart >= 0, 'inicializador de auto-pull entre maquinas ausente');
assert(machineAutoSource.includes('/api/shared-sync/machine-sync/auto'), 'auto-pull entre maquinas nao chama o endpoint');
assert(machineAutoSource.includes('MACHINE_SHARED_SYNC_AUTO_START_DELAY_MS = 4000'), 'chamada inicial curta do auto-pull ausente');
assert(machineAutoSource.includes('MACHINE_SHARED_SYNC_AUTO_INTERVAL_MS = 2 * 60 * 1000'), 'intervalo seguro do auto-pull ausente');
assert(machineAutoSource.includes('setInterval(') && machineAutoSource.includes('setTimeout('), 'scheduler do auto-pull esta incompleto');
assert(machineAutoSource.includes('liderMachineSync()'), 'auto-pull perdeu a guarda de lideranca');
assert(machineAutoSource.includes('const machineSyncLeader = telaSeguraParaSincronizar()'), 'pagina sem auto-pull pode bloquear a lideranca da tela segura');
assert(machineAutoSource.includes("document.visibilityState === 'hidden'"), 'auto-pull perdeu a guarda de visibilidade');
assert(machineAutoSource.includes("'jk:machine-sync-updated'"), 'auto-pull nao notifica a tela apos recebimento');
assert(!machineAutoSource.includes('/api/shared-sync/machine-sync/push'), 'boot nao pode habilitar auto-push');
const authLoader = read('static/auth.js');
assert(authLoader.includes("VERSION = '20260907-central-migration-v1'"), 'cache-buster do auth loader nao foi atualizado');
assert.strictEqual(authLoader, read('auth.js'), 'auth.js divergiu do espelho oficial');

console.log('shared_sync_ui_regression: ok');
