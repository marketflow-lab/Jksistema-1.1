    function _msgLabelUsuario(user) {
      return String((user && (user.name || user.username || user.email)) || 'Usuario').trim();
    }

    function _msgChaveUsuario(username, clientId) {
      const user = String(username || '').trim().toLowerCase();
      const client = String(clientId || _clientId() || 'default').trim() || 'default';
      return `${user}|${client}`;
    }

    function _msgChaveHistorico(user) {
      return _msgChaveUsuario(user && user.username, user && user.client_id);
    }

    function _msgLerUsuariosCache() {
      if (msgUsuariosCache.length && Date.now() - msgUsuariosCacheTs < MSG_USUARIOS_CACHE_TTL_MS) {
        return msgUsuariosCache;
      }
      try {
        const raw = JSON.parse(localStorage.getItem(MSG_USUARIOS_CACHE_KEY) || '{}') || {};
        const lista = Array.isArray(raw.users) ? raw.users : [];
        const ts = Number(raw.ts || 0);
        if (lista.length && Date.now() - ts < MSG_USUARIOS_CACHE_TTL_MS) {
          msgUsuariosCache = lista;
          msgUsuariosCacheTs = ts;
          return lista;
        }
      } catch (_) {}
      return [];
    }

    function _msgSalvarUsuariosCache(usuarios) {
      const lista = Array.isArray(usuarios) ? usuarios : [];
      if (!lista.length) return;
      msgUsuariosCache = lista;
      msgUsuariosCacheTs = Date.now();
      try {
        localStorage.setItem(MSG_USUARIOS_CACHE_KEY, JSON.stringify({ ts: msgUsuariosCacheTs, users: lista.slice(0, 80) }));
      } catch (_) {}
    }

    function _msgMesclarPresencaUsuarios(contatos, usuariosOnline) {
      const lista = Array.isArray(contatos) ? contatos : [];
      const presencas = Array.isArray(usuariosOnline) ? usuariosOnline : [];
      if (!lista.length || !presencas.length) return lista;
      const porChave = new Map();
      presencas.forEach(user => {
        if (!user || typeof user !== 'object') return;
        const chave = _msgChaveUsuario(user.username, user.client_id);
        if (chave) porChave.set(chave, user);
      });
      if (!porChave.size) return lista;
      return lista.map(user => {
        if (!user || typeof user !== 'object') return user;
        const presenca = porChave.get(_msgChaveUsuario(user.username, user.client_id));
        if (!presenca) return user;
        const onlineCount = Math.max(Number(user.online_count || 0), Number(presenca.online_count || 0));
        const machines = Array.isArray(presenca.machines) && presenca.machines.length
          ? presenca.machines
          : (Array.isArray(user.machines) ? user.machines : []);
        const allRecent = Array.isArray(presenca.all_recent_machines) && presenca.all_recent_machines.length
          ? presenca.all_recent_machines
          : (Array.isArray(user.all_recent_machines) ? user.all_recent_machines : []);
        return Object.assign({}, user, {
          online: !!(user.online || presenca.online || onlineCount > 0),
          online_count: onlineCount,
          last_seen_at: presenca.last_seen_at || user.last_seen_at || '',
          seconds_since_seen: presenca.seconds_since_seen !== undefined && presenca.seconds_since_seen !== null
            ? presenca.seconds_since_seen
            : user.seconds_since_seen,
          machines,
          all_recent_machines: allRecent,
        });
      });
    }

    function _msgListaTemPresencaOnline(lista) {
      return (Array.isArray(lista) ? lista : []).some(user => (
        user && (
          user.online === true ||
          Number(user.online_count || 0) > 0 ||
          (Array.isArray(user.machines) && user.machines.some(machine => machine && machine.online !== false))
        )
      ));
    }

    function _msgUsuarioEstaOnline(user, isSelf = false) {
      if (isSelf) return true;
      if (!user || typeof user !== 'object') return false;
      if (user.online === true) return true;
      if (Number(user.online_count || 0) > 0) return true;
      return (Array.isArray(user.machines) ? user.machines : [])
        .some(machine => machine && machine.online !== false);
    }

    function _msgFormatarHorarioVisto(raw) {
      const texto = String(raw || '').trim();
      if (!texto) return '';
      const data = new Date(texto.replace(' ', 'T'));
      if (Number.isNaN(data.getTime())) return texto;
      const agora = new Date();
      const mesmoDia = data.getFullYear() === agora.getFullYear()
        && data.getMonth() === agora.getMonth()
        && data.getDate() === agora.getDate();
      return mesmoDia
        ? data.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
        : data.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
    }

    function _msgStatusUsuarioTexto(user) {
      if (_msgUsuarioEstaOnline(user)) return 'Online';
      const direto = String(user && user.last_seen_at || '').trim();
      const maquina = Array.isArray(user && user.all_recent_machines) && user.all_recent_machines[0]
        ? String(user.all_recent_machines[0].last_seen_at || '').trim()
        : '';
      const visto = _msgFormatarHorarioVisto(direto || maquina);
      return visto ? `visto ${visto}` : 'Offline';
    }

    function _msgSubtituloUsuario(user, isSelf) {
      const status = _msgStatusUsuarioTexto(user);
      return isSelf ? 'Voc\u00ea \u2022 Online' : status;
    }

    function _msgInicialUsuario(user) {
      const label = _msgLabelUsuario(user) || String(user && user.username || 'U');
      const limpo = String(label || 'U').normalize('NFD').replace(/[\u0300-\u036f]/g, '').trim();
      return (limpo.charAt(0) || 'U').toUpperCase();
    }

    function _msgAvatarClasse(user) {
      const fonte = String(user && (user.username || user.name || user.email) || 'usuario').toLowerCase();
      let hash = 0;
      for (let i = 0; i < fonte.length; i += 1) hash = ((hash << 5) - hash) + fonte.charCodeAt(i);
      const idx = Math.abs(hash) % 8;
      return idx ? ` c${idx}` : '';
    }

    function _msgRenderVazio(el, texto) {
      if (!el) return;
      el.innerHTML = '';
      const vazio = document.createElement('div');
      vazio.className = 'jk-msg-empty';
      vazio.textContent = texto;
      el.appendChild(vazio);
    }

    function _msgMimeExt(mime) {
      const tipo = String(mime || '').toLowerCase();
      if (tipo.includes('ogg')) return 'ogg';
      if (tipo.includes('mpeg') || tipo.includes('mp3')) return 'mp3';
      if (tipo.includes('wav')) return 'wav';
      if (tipo.includes('webm')) return 'webm';
      return 'webm';
    }

    function _msgDataUrl(anexo) {
      if (!anexo || !anexo.data_base64) return '';
      return `data:${String(anexo.mime_type || 'application/octet-stream')};base64,${anexo.data_base64}`;
    }

    function _msgTotalAnexosBytes(lista = msgAnexos) {
      return (Array.isArray(lista) ? lista : []).reduce((acc, item) => acc + Number(item && item.size || 0), 0);
    }

    function _msgRenderAnexosComposer() {
      const el = document.getElementById('jk-msg-anexos');
      if (!el) return;
      el.innerHTML = '';
      msgAnexos.forEach((anexo, index) => {
        const chip = document.createElement('div');
        chip.className = 'jk-msg-anexo-chip';
        const nome = document.createElement('span');
        nome.textContent = String(anexo.name || 'arquivo');
        const remover = document.createElement('button');
        remover.type = 'button';
        remover.title = 'Remover anexo';
        remover.textContent = 'x';
        remover.addEventListener('click', () => {
          msgAnexos.splice(index, 1);
          _msgRenderAnexosComposer();
        });
        chip.appendChild(nome);
        chip.appendChild(remover);
        el.appendChild(chip);
      });
    }

    function _msgAdicionarAnexo(anexo) {
      if (!anexo || !anexo.data_base64) return false;
      if (msgAnexos.length >= MSG_ATTACHMENT_MAX_COUNT) {
        _msgSetStatus(`Limite de ${MSG_ATTACHMENT_MAX_COUNT} anexos por mensagem.`, true);
        return false;
      }
      const tamanho = Number(anexo.size || 0);
      if (tamanho > MSG_ATTACHMENT_MAX_BYTES) {
        _msgSetStatus('Anexo muito grande. Limite de 700 KB por arquivo.', true);
        return false;
      }
      if (_msgTotalAnexosBytes() + tamanho > MSG_ATTACHMENT_TOTAL_MAX_BYTES) {
        _msgSetStatus('Anexos muito grandes. Limite total de 900 KB por mensagem.', true);
        return false;
      }
      msgAnexos.push(anexo);
      _msgRenderAnexosComposer();
      return true;
    }

    function _msgBlobParaAnexo(blob, nome) {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
          const dataUrl = String(reader.result || '');
          const base64 = dataUrl.includes(',') ? dataUrl.split(',').pop() : '';
          resolve({
            name: nome || 'audio.webm',
            mime_type: blob.type || 'application/octet-stream',
            data_base64: base64 || '',
            size: Number(blob.size || 0),
          });
        };
        reader.onerror = reject;
        reader.readAsDataURL(blob);
      });
    }

    async function _msgArquivosSelecionados(files) {
      const lista = Array.from(files || []);
      for (const file of lista) {
        const anexo = await _msgBlobParaAnexo(file, file.name || 'arquivo');
        _msgAdicionarAnexo(anexo);
      }
    }

    function _msgNomeArquivoImagem(nome) {
      const limpo = String(nome || 'imagem').trim().replace(/[\\/:*?"<>|]+/g, '-');
      return limpo || 'imagem';
    }

    function _msgFecharImagemTelaCheia() {
      const modal = document.getElementById('jk-msg-image-modal');
      const img = document.getElementById('jk-msg-image-modal-img');
      if (modal) modal.hidden = true;
      if (img) {
        img.removeAttribute('src');
        img.alt = '';
      }
    }

    function _msgAbrirImagemTelaCheia(url, nome) {
      const modal = document.getElementById('jk-msg-image-modal');
      const img = document.getElementById('jk-msg-image-modal-img');
      const title = document.getElementById('jk-msg-image-modal-title');
      const copy = document.getElementById('jk-msg-image-modal-copy');
      const download = document.getElementById('jk-msg-image-modal-download');
      if (!modal || !img || !url) return;
      const nomeFinal = _msgNomeArquivoImagem(nome);
      img.src = url;
      img.alt = nomeFinal;
      modal.dataset.imageUrl = url;
      modal.dataset.imageName = nomeFinal;
      if (title) title.textContent = nomeFinal;
      if (copy) copy.disabled = false;
      if (download) {
        download.href = url;
        download.download = nomeFinal;
      }
      modal.hidden = false;
    }

    async function _msgCopiarImagemTelaCheia() {
      const modal = document.getElementById('jk-msg-image-modal');
      const copy = document.getElementById('jk-msg-image-modal-copy');
      const url = String(modal && modal.dataset.imageUrl || '').trim();
      if (!url) return;
      try {
        if (copy) copy.disabled = true;
        const blob = await fetch(url).then(resp => resp.blob());
        if (navigator.clipboard && window.ClipboardItem) {
          try {
            await navigator.clipboard.write([new ClipboardItem({ [blob.type || 'image/png']: blob })]);
            _msgSetStatus('Imagem copiada.');
            return;
          } catch (_) {
            // Alguns contextos do Chromium aceitam apenas o link no clipboard.
          }
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(url);
          _msgSetStatus('Link da imagem copiado.');
          return;
        }
        throw new Error('Area de transferencia indisponivel.');
      } catch (err) {
        _msgSetStatus(err && err.message ? err.message : 'Nao foi possivel copiar a imagem.', true);
      } finally {
        if (copy) copy.disabled = false;
      }
    }

    function _msgRenderAnexoHistorico(anexo) {
      const item = document.createElement('div');
      item.className = 'jk-msg-attachment';
      const url = _msgDataUrl(anexo);
      const nome = String(anexo && anexo.name || 'arquivo');
      const mime = String(anexo && anexo.mime_type || '').toLowerCase();
      if (url && mime.startsWith('image/')) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'jk-msg-image-thumb';
        btn.title = 'Abrir imagem em tela cheia';
        const img = document.createElement('img');
        img.src = url;
        img.alt = nome;
        btn.appendChild(img);
        btn.addEventListener('click', () => _msgAbrirImagemTelaCheia(url, nome));
        item.appendChild(btn);
        return item;
      }
      if (url && mime.startsWith('audio/')) {
        const audio = document.createElement('audio');
        audio.controls = true;
        audio.src = url;
        item.appendChild(audio);
        return item;
      }
      const link = document.createElement('a');
      link.href = url || '#';
      link.download = nome;
      link.textContent = nome;
      item.appendChild(link);
      return item;
    }

    function _msgRenderAnexosHistorico(bubble, anexos) {
      const lista = Array.isArray(anexos) ? anexos : [];
      if (!bubble || !lista.length) return;
      const wrap = document.createElement('div');
      wrap.className = 'jk-msg-attachments';
      lista.forEach(anexo => wrap.appendChild(_msgRenderAnexoHistorico(anexo)));
      bubble.appendChild(wrap);
    }

    function _msgToggleEmojiPanel() {
      document.getElementById('jk-msg-emoji-panel')?.classList.toggle('aberto');
    }

    function _msgInserirEmoji(emoji) {
      const input = document.getElementById('jk-msg-text');
      if (!input) return;
      const inicio = input.selectionStart || input.value.length;
      const fim = input.selectionEnd || input.value.length;
      input.value = input.value.slice(0, inicio) + emoji + input.value.slice(fim);
      const pos = inicio + emoji.length;
      input.focus();
      try { input.setSelectionRange(pos, pos); } catch (_) {}
    }

    function _msgMontarEmojiPanel() {
      const panel = document.getElementById('jk-msg-emoji-panel');
      if (!panel || panel.dataset.montado === '1') return;
      panel.dataset.montado = '1';
      ['🙂','😀','😂','😍','👍','🙏','👏','🔥','✅','⭐','⚠️','❤️','😎','🤝','📦','💬'].forEach(emoji => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'jk-msg-emoji-choice';
        btn.textContent = emoji;
        btn.addEventListener('click', () => _msgInserirEmoji(emoji));
        panel.appendChild(btn);
      });
    }

    function _msgSetDigitando(ativo, nome = '') {
      const el = document.getElementById('jk-msg-typing');
      if (!el) return;
      const mostrar = !!(ativo && msgChatAberto && msgUsuarioSelecionado);
      el.classList.toggle('ativo', mostrar);
      el.textContent = mostrar ? `${nome || _msgLabelUsuario(msgUsuarioSelecionado)} digitando...` : '';
    }

    async function _msgEnviarDigitando(typing) {
      if (!msgUsuarioSelecionado || !msgUsuarioSelecionado.username) return;
      const valor = !!typing;
      if (msgTypingEnviado === valor) return;
      msgTypingEnviado = valor;
      try {
        await window.__JK_IA_SIDEBAR_FETCH__('/api/user/chat/typing', {
          method: 'POST',
          headers: _authHeaders(),
          body: JSON.stringify({
            username: msgUsuarioSelecionado.username,
            client_id: msgUsuarioSelecionado.client_id || _clientId() || 'default',
            typing: valor,
          }),
        });
      } catch (_) {}
    }

    function _msgMarcarDigitandoLocal() {
      if (!msgChatAberto || !msgUsuarioSelecionado) return;
      void _msgEnviarDigitando(true);
      if (msgTypingStopTimer) clearTimeout(msgTypingStopTimer);
      msgTypingStopTimer = setTimeout(() => {
        msgTypingStopTimer = null;
        void _msgEnviarDigitando(false);
      }, 3200);
    }

    function _msgPararDigitandoLocal() {
      if (msgTypingStopTimer) {
        clearTimeout(msgTypingStopTimer);
        msgTypingStopTimer = null;
      }
      void _msgEnviarDigitando(false);
    }

    async function _msgBuscarDigitando() {
      if (!msgPanelAberto || !msgChatAberto || !msgUsuarioSelecionado || !msgUsuarioSelecionado.username) {
        _msgSetDigitando(false);
        return;
      }
      try {
        const params = new URLSearchParams({
          username: String(msgUsuarioSelecionado.username || ''),
          client_id: String(msgUsuarioSelecionado.client_id || _clientId() || 'default'),
        });
        const resp = await window.__JK_IA_SIDEBAR_FETCH__(`/api/user/chat/typing?${params.toString()}`, {
          method: 'GET',
          headers: _authHeaders(),
          cache: 'no-store',
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || data.success === false) throw new Error(data.detail || data.message || 'Erro ao buscar digitacao.');
        _msgSetDigitando(!!data.typing, data.name || _msgLabelUsuario(msgUsuarioSelecionado));
      } catch (_) {
        _msgSetDigitando(false);
      }
    }

    function _msgAtualizarDigitandoPoll() {
      if (msgTypingPollTimer) {
        clearInterval(msgTypingPollTimer);
        msgTypingPollTimer = null;
      }
      _msgSetDigitando(false);
      if (!msgPanelAberto || !msgChatAberto || !msgUsuarioSelecionado) return;
      void _msgBuscarDigitando();
      msgTypingPollTimer = setInterval(() => void _msgBuscarDigitando(), MSG_TYPING_POLL_MS);
    }

    async function _msgToggleGravacaoAudio() {
      const btn = document.getElementById('jk-msg-audio-btn');
      if (msgMediaRecorder && msgMediaRecorder.state === 'recording') {
        msgMediaRecorder.stop();
        if (btn) btn.classList.remove('ativo');
        return;
      }
      if (!navigator.mediaDevices || !window.MediaRecorder) {
        _msgSetStatus('Gravacao de audio indisponivel neste navegador.', true);
        return;
      }
      try {
        msgAudioChunks = [];
        msgAudioStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        msgMediaRecorder = new MediaRecorder(msgAudioStream);
        msgMediaRecorder.ondataavailable = (event) => {
          if (event.data && event.data.size) msgAudioChunks.push(event.data);
        };
        msgMediaRecorder.onstop = async () => {
          try {
            const mime = msgMediaRecorder.mimeType || 'audio/webm';
            const blob = new Blob(msgAudioChunks, { type: mime });
            const nome = `audio_${new Date().toISOString().replace(/[:.]/g, '-')}.${_msgMimeExt(mime)}`;
            const anexo = await _msgBlobParaAnexo(blob, nome);
            _msgAdicionarAnexo(anexo);
          } catch (err) {
            _msgSetStatus(err && err.message ? err.message : 'Nao foi possivel anexar o audio.', true);
          } finally {
            try { (msgAudioStream?.getTracks?.() || []).forEach(track => track.stop()); } catch (_) {}
            msgAudioStream = null;
            msgMediaRecorder = null;
            msgAudioChunks = [];
          }
        };
        msgMediaRecorder.start();
        if (btn) btn.classList.add('ativo');
        _msgSetStatus('Gravando audio. Clique no microfone novamente para finalizar.');
      } catch (err) {
        if (btn) btn.classList.remove('ativo');
        _msgSetStatus('Nao foi possivel acessar o microfone.', true);
      }
    }
