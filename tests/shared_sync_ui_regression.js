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
assert(admin.includes("executarSincronizacaoMinhasMaquinas('push')") && admin.includes("executarSincronizacaoMinhasMaquinas('pull')"), 'ações manuais da sincronização entre máquinas ausentes');
assert(!admin.includes('id="firebaseProvisioningPanel"'), 'credencial Firebase não pode ser provisionada pela interface');
assert(!admin.includes('id="firebaseProvisioningFile"'), 'seletor local da chave Firebase não pode reaparecer');
assert(!admin.includes('/api/admin/firebase-provisioning/'), 'interface não pode chamar endpoints locais de provisionamento Firebase');
assert(!admin.includes('normalizarStatusProvisionamentoFirebase'), 'fluxo legado de provisionamento Firebase não pode reaparecer');
assert(!admin.includes('FileReader'), 'frontend não pode ler o conteúdo de credenciais locais');
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
assert(authLoader.includes("VERSION = '20260828-cadastro-fotos-lojas-v1'"), 'cache-buster do auth loader nao foi atualizado');
assert.strictEqual(authLoader, read('auth.js'), 'auth.js divergiu do espelho oficial');

console.log('shared_sync_ui_regression: ok');
