(function () {
  'use strict';

  const permissions = JSON.parse(localStorage.getItem('permissions') || '{}');
  if (permissions.full !== true) return;

  const byId = id => document.getElementById(id);
  const notesBox = byId('contextHubNotes');
  const backupsBox = byId('contextHubBackups');
  const statusBox = byId('contextHubCurationStatus');
  if (!notesBox || !backupsBox || !statusBox) return;

  function authHeaders() {
    if (typeof obterAuthHeaders === 'function') return obterAuthHeaders();
    const token = localStorage.getItem('access_token') || localStorage.getItem('token') || '';
    return token ? { Authorization: `Bearer ${token}` } : {};
  }

  async function request(pathname, options = {}) {
    const headers = { ...authHeaders(), ...(options.headers || {}) };
    if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
    const response = await fetch(pathname, { cache: 'no-store', ...options, headers });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.success === false) {
      throw new Error(data.detail || data.error || data.message || `Falha no Context Hub (HTTP ${response.status}).`);
    }
    return data;
  }

  function setStatus(message, kind = '') {
    statusBox.className = `status${kind ? ` ${kind}` : ''}`;
    statusBox.textContent = message || '';
  }

  function textLine(className, value) {
    const line = document.createElement('div');
    line.className = className;
    line.textContent = value;
    return line;
  }

  function actionButton(label, kind, action) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = kind || 'btn-refresh';
    button.textContent = label;
    button.addEventListener('click', async () => {
      if (button.disabled) return;
      button.disabled = true;
      try {
        await action();
      } finally {
        button.disabled = false;
      }
    });
    return button;
  }

  function noteId(note) {
    return String(note && (note.note_id || note.id) || '').trim();
  }

  function noteState(note) {
    return String(note && (note.status || note.state) || 'draft').trim().toLowerCase();
  }

  async function runNoteAction(note, action) {
    const id = noteId(note);
    if (!id) return;
    let body;
    if (action === 'reject') {
      const reason = window.prompt('Motivo da rejeição desta versão da nota:');
      if (!reason) return;
      body = JSON.stringify({ reason });
    }
    setStatus(`Executando ${action} na nota...`, 'loading');
    try {
      await request(`/api/admin/context-hub/curation/notes/${encodeURIComponent(id)}/${action}`, {
        method: 'POST',
        ...(body ? { body } : {}),
      });
      setStatus('Nota atualizada. A aprovação sempre corresponde ao hash exibido.', 'success');
      await loadNotes();
      byId('btnContextHubRefresh')?.click();
    } catch (error) {
      setStatus(error.message, 'error');
    }
  }

  function renderNotes(items) {
    notesBox.replaceChildren();
    const notes = Array.isArray(items) ? items : [];
    if (!notes.length) {
      notesBox.appendChild(textLine('hint', 'Nenhuma nota em 80_Curadoria.'));
      return;
    }
    notes.forEach(note => {
      const card = document.createElement('div');
      card.className = 'context-hub-generation';
      const state = noteState(note);
      const title = String(note.title || note.name || note.relative_path || 'Nota sem título');
      card.appendChild(textLine('', title));
      card.appendChild(textLine('hint', `Estado: ${state} · hash: ${String(note.content_sha256 || note.current_hash || note.hash || '—').slice(0, 16)}`));
      if (note.approval_valid === false && note.approved_hash) {
        card.appendChild(textLine('hint', 'A nota foi editada depois da aprovação; o hash precisa ser aprovado novamente.'));
      }
      const actions = document.createElement('div');
      actions.className = 'actions';
      actions.style.margin = '8px 0 0';
      actions.append(
        actionButton('Abrir no Obsidian', 'btn-refresh', async () => byId('btnContextHubOpen')?.click()),
        actionButton('Validar', 'btn-refresh', () => runNoteAction(note, 'validate')),
        actionButton('Marcar revisada', 'btn-refresh', () => runNoteAction(note, 'review')),
        actionButton('Aprovar hash', 'btn-save', () => runNoteAction(note, 'approve')),
        actionButton('Rejeitar', 'btn-danger', () => runNoteAction(note, 'reject')),
      );
      card.appendChild(actions);
      notesBox.appendChild(card);
    });
  }

  async function loadNotes() {
    notesBox.replaceChildren(textLine('hint', 'Carregando notas...'));
    try {
      const data = await request('/api/admin/context-hub/curation/notes');
      renderNotes(data.notes || data.items || []);
    } catch (error) {
      notesBox.replaceChildren(textLine('hint', error.message));
    }
  }

  async function createNote() {
    const titleInput = byId('contextHubNoteTitle');
    const bodyInput = byId('contextHubNoteBody');
    const categoryInput = byId('contextHubNoteCategory');
    const title = String(titleInput.value || '').trim();
    const body = String(bodyInput.value || '').trim();
    if (!title || !body) {
      setStatus('Informe título e conteúdo inicial.', 'error');
      return;
    }
    setStatus('Criando rascunho em 80_Curadoria...', 'loading');
    try {
      await request('/api/admin/context-hub/curation/notes', {
        method: 'POST',
        body: JSON.stringify({ title, body, category: categoryInput.value || 'Notas' }),
      });
      titleInput.value = '';
      bodyInput.value = '';
      setStatus('Rascunho criado. Continue a edição no Obsidian e valide o hash atual.', 'success');
      await loadNotes();
    } catch (error) {
      setStatus(error.message, 'error');
    }
  }

  async function publishCuration() {
    if (!window.confirm('Publicar manualmente todas as notas aprovadas no hash atual?')) return;
    setStatus('Criando e publicando uma geração revisada...', 'loading');
    try {
      await request('/api/admin/context-hub/curation/publish', {
        method: 'POST',
        body: JSON.stringify({ reason: 'manual_admin', force: false }),
      });
      setStatus('Curadoria publicada na nova geração ativa.', 'success');
      await loadNotes();
      byId('btnContextHubRefresh')?.click();
    } catch (error) {
      setStatus(error.message, 'error');
    }
  }

  function backupId(item) {
    return String(item && (item.backup_id || item.id || item.name) || '').trim();
  }

  function renderBackups(items) {
    backupsBox.replaceChildren();
    const backups = Array.isArray(items) ? items : [];
    if (!backups.length) {
      backupsBox.appendChild(textLine('hint', 'Nenhum backup cifrado de 80_Curadoria.'));
      return;
    }
    backups.forEach(item => {
      const id = backupId(item);
      const row = document.createElement('div');
      row.className = 'context-hub-generation';
      row.appendChild(textLine('', id || 'Backup sem identificador'));
      row.appendChild(textLine('hint', `${item.created_at || 'data não informada'} · ${Number(item.file_count || 0)} arquivo(s)`));
      row.appendChild(actionButton('Restaurar em staging', 'btn-warning', () => restoreBackup(id)));
      backupsBox.appendChild(row);
    });
  }

  async function loadBackups() {
    backupsBox.replaceChildren(textLine('hint', 'Carregando backups...'));
    try {
      const data = await request('/api/admin/context-hub/curation/backups');
      renderBackups(data.backups || data.items || []);
    } catch (error) {
      backupsBox.replaceChildren(textLine('hint', error.message));
    }
  }

  function takePassphrase() {
    const input = byId('contextHubBackupPassphrase');
    const passphrase = String(input.value || '');
    input.value = '';
    if (passphrase.length < 12) throw new Error('A senha do backup precisa ter pelo menos 12 caracteres.');
    return passphrase;
  }

  async function createBackup() {
    let passphrase;
    try { passphrase = takePassphrase(); } catch (error) { return setStatus(error.message, 'error'); }
    setStatus('Criando backup cifrado somente de 80_Curadoria...', 'loading');
    try {
      await request('/api/admin/context-hub/curation/backups', {
        method: 'POST',
        body: JSON.stringify({ passphrase }),
      });
      setStatus('Backup cifrado criado.', 'success');
      await loadBackups();
    } catch (error) {
      setStatus(error.message, 'error');
    } finally {
      passphrase = '';
    }
  }

  async function restoreBackup(id) {
    if (!id || !window.confirm(`Restaurar ${id} primeiro em staging e validar antes de promover?`)) return;
    let passphrase;
    try { passphrase = takePassphrase(); } catch (error) { return setStatus(error.message, 'error'); }
    setStatus('Restaurando e validando o backup em staging...', 'loading');
    try {
      await request(`/api/admin/context-hub/curation/backups/${encodeURIComponent(id)}/restore`, {
        method: 'POST',
        body: JSON.stringify({ passphrase }),
      });
      setStatus('Backup validado e restaurado. Revise novamente os hashes antes de publicar.', 'success');
      await Promise.all([loadNotes(), loadBackups()]);
      byId('btnContextHubRefresh')?.click();
    } catch (error) {
      setStatus(error.message, 'error');
    } finally {
      passphrase = '';
    }
  }

  byId('btnContextHubRefreshNotes')?.addEventListener('click', loadNotes);
  byId('btnContextHubCreateNote')?.addEventListener('click', createNote);
  byId('btnContextHubPublishCuration')?.addEventListener('click', publishCuration);
  byId('btnContextHubRefreshBackups')?.addEventListener('click', loadBackups);
  byId('btnContextHubCreateBackup')?.addEventListener('click', createBackup);
  byId('tabContextHubBtn')?.addEventListener('click', () => Promise.all([loadNotes(), loadBackups()]));
}());
