    function _approvalCompactarPayload(valor, depth = 0) {
      if (valor == null) return valor;
      if (typeof valor === 'string') return valor.slice(0, 5000);
      if (typeof valor === 'number' || typeof valor === 'boolean') return valor;
      if (depth > 4) return '';
      if (Array.isArray(valor)) {
        return valor.slice(0, 40).map(item => _approvalCompactarPayload(item, depth + 1));
      }
      if (typeof valor === 'object') {
        const saida = {};
        Object.keys(valor).slice(0, 80).forEach((key) => {
          if (['mercadolivre', 'raw', 'debug'].includes(key)) return;
          saida[key] = _approvalCompactarPayload(valor[key], depth + 1);
        });
        return saida;
      }
      return '';
    }

    function _approvalSerializarMensagem(payload) {
      try {
        return APPROVAL_MSG_PREFIX + JSON.stringify(_approvalCompactarPayload(payload || {}));
      } catch (_) {
        return '';
      }
    }

    function _approvalParseMensagem(texto) {
      const raw = String(texto || '').trimStart();
      if (!raw.startsWith(APPROVAL_MSG_PREFIX)) return null;
      try {
        const payload = JSON.parse(raw.slice(APPROVAL_MSG_PREFIX.length));
        return payload && typeof payload === 'object' ? payload : null;
      } catch (_) {
        return null;
      }
    }

    function _approvalIndexNoHistorico(approvalId) {
      const id = String(approvalId || '').trim();
      if (!id) return -1;
      return mensagensAtuais.findIndex((msg) => {
        if (!msg) return false;
        const payload = _approvalParseMensagem(msg.text);
        return String((payload && payload.id) || '').trim() === id;
      });
    }

    function _approvalGarantirConversaHistorico() {
      if (!convAtualId) convAtualId = _novoId();
    }

    function _approvalSalvarNoHistorico(payload) {
      const id = String((payload && payload.id) || '').trim();
      const texto = _approvalSerializarMensagem(payload);
      if (!id || !texto) return;
      _approvalGarantirConversaHistorico();
      const idx = _approvalIndexNoHistorico(id);
      if (idx >= 0) {
        mensagensAtuais[idx] = { role: 'assistant', text: texto };
      } else {
        mensagensAtuais.push({ role: 'assistant', text: texto });
      }
      try {
        void salvarMensagensAtuais();
        if (convsVisible) renderConvsList();
      } catch (_) {}
    }

    function renderMsgs() {
      const msgsEl = document.getElementById('jk-ia-msgs');
      msgsEl.innerHTML = '';
      if (mensagensAtuais.length === 0) {
        addMsg('assistant', 'Olá! Posso analisar dados desta tela, responder dúvidas ou ajudar com próximos passos.', false);
        return;
      }
      mensagensAtuais.forEach(m => {
        const approvalPayload = _approvalParseMensagem(m && m.text);
        const div = document.createElement('div');
        div.className = 'jk-ia-msg ' + (approvalPayload ? 'assistant' : (m.role || 'assistant'));
        if (approvalPayload) {
          div.innerHTML = '';
          _approvalMontarCard(div, approvalPayload);
        } else {
          _definirTextoMsg(div, m && m.text);
        }
        msgsEl.appendChild(div);
      });
      msgsEl.scrollTop = msgsEl.scrollHeight;
    }

    function addMsg(role, texto, salvar = true) {
      const msgsEl = document.getElementById('jk-ia-msgs');
      const approvalPayload = _approvalParseMensagem(texto);
      const div = document.createElement('div');
      div.className = 'jk-ia-msg ' + (approvalPayload ? 'assistant' : role);
      if (approvalPayload) {
        _approvalMontarCard(div, approvalPayload);
      } else {
        _definirTextoMsg(div, texto);
      }
      msgsEl.appendChild(div);
      msgsEl.scrollTop = msgsEl.scrollHeight;
      if (salvar) {
        mensagensAtuais.push({
          role: approvalPayload ? 'assistant' : role,
          text: approvalPayload ? _approvalSerializarMensagem(approvalPayload) : texto,
        });
        try {
          void salvarMensagensAtuais();
          if (convsVisible) renderConvsList();
        } catch (_) {}
      }
      return div;
    }

    function _approvalTexto(payload) {
      const isPosVenda = String(payload.tipo || payload.approval_type || '').toLowerCase() === 'pos_venda';
      return [
        isPosVenda
          ? 'Aprovação necessária para responder conversa pós-venda do Mercado Livre.'
          : 'Aprovação necessária para responder pergunta do Mercado Livre.',
        `Loja: ${payload.loja || '-'}`,
        `${isPosVenda ? 'Venda/Produto' : 'Anúncio'}: ${payload.titulo || payload.item_id || payload.order_id || '-'}`,
        `SKU: ${payload.sku || '-'}`,
        '',
        `${isPosVenda ? 'Mensagem do comprador' : 'Pergunta'}: ${payload.pergunta || '-'}`,
        '',
        `Resposta sugerida: ${payload.resposta_sugerida || '-'}`
      ].join('\n');
    }

    function _approvalMensagens(payload) {
      const conversa = payload && typeof payload.conversa === 'object' ? payload.conversa : {};
      const listas = [
        conversa && conversa.messages,
        payload && payload.messages,
        payload && payload.mensagens,
      ];
      for (const lista of listas) {
        if (Array.isArray(lista) && lista.length) {
          return lista.filter(item => item && typeof item === 'object');
        }
      }
      const pergunta = String((payload && payload.pergunta) || '').trim();
      return pergunta ? [{ from_role: 'buyer', text: pergunta, attachments: [] }] : [];
    }

    function _approvalDescricaoAnuncio(payload) {
      const texto = String((payload && (payload.descricao_anuncio || payload.descricao || payload.item_description)) || '').trim();
      if (!texto) return '';
      return texto.length > 1000 ? `${texto.slice(0, 1000).trim()}...` : texto;
    }

    function _approvalAnexos(msg) {
      const listas = [msg && msg.attachments, msg && msg.anexos, msg && msg.images, msg && msg.pictures];
      const anexos = [];
      listas.forEach(lista => {
        if (!Array.isArray(lista)) return;
        lista.forEach(item => {
          if (!item) return;
          if (typeof item === 'string') {
            anexos.push({ url: item, name: 'anexo' });
          } else if (typeof item === 'object') {
            anexos.push(item);
          }
        });
      });
      return anexos;
    }

    function _approvalAnexoEhImagem(anexo) {
      const texto = `${anexo && anexo.mime_type || ''} ${anexo && anexo.type || ''} ${anexo && anexo.name || ''} ${anexo && anexo.url || ''}`;
      return (anexo && anexo.is_image === true) || /image|foto|picture|jpg|jpeg|png|webp|gif/i.test(texto);
    }

    function _approvalAppendAnexos(container, anexos) {
      if (!Array.isArray(anexos) || !anexos.length) return;
      const wrap = document.createElement('div');
      wrap.className = 'jk-ia-approval-attachments';
      anexos.forEach((anexo) => {
        const url = String((anexo && (anexo.url || anexo.href || anexo.src)) || '').trim();
        if (!url) return;
        const nome = String((anexo && (anexo.name || anexo.filename || anexo.id)) || 'anexo').trim();
        const link = document.createElement('a');
        link.href = url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        if (_approvalAnexoEhImagem(anexo)) {
          const img = document.createElement('img');
          img.className = 'jk-ia-approval-attachment-img';
          img.src = url;
          img.alt = nome || 'Imagem enviada pelo comprador';
          img.loading = 'lazy';
          img.referrerPolicy = 'no-referrer';
          link.title = nome || 'Abrir imagem';
          link.appendChild(img);
        } else {
          link.className = 'jk-ia-approval-file';
          link.textContent = nome || 'Abrir anexo';
        }
        wrap.appendChild(link);
      });
      if (wrap.childElementCount) container.appendChild(wrap);
    }

    function _approvalCriarConversa(payload) {
      const mensagens = _approvalMensagens(payload);
      const box = document.createElement('div');
      box.className = 'jk-ia-approval-conversation';
      const label = document.createElement('div');
      label.className = 'jk-ia-approval-label';
      label.textContent = 'Conversa completa';
      box.appendChild(label);
      if (!mensagens.length) {
        const vazio = document.createElement('div');
        vazio.textContent = 'Nenhuma mensagem encontrada nesta aprovacao.';
        box.appendChild(vazio);
        return box;
      }
      mensagens.forEach((msg) => {
        const role = String(msg.from_role || msg.role || '').toLowerCase() === 'seller' ? 'seller' : 'buyer';
        const item = document.createElement('div');
        item.className = `jk-ia-approval-message ${role}`;
        const head = document.createElement('div');
        head.className = 'jk-ia-approval-message-head';
        const data = String(msg.date || msg.created_at || msg.message_date || '').trim();
        head.textContent = `${role === 'seller' ? 'Vendedor' : 'Comprador'}${data ? ' - ' + data : ''}`;
        const texto = document.createElement('div');
        texto.textContent = String(msg.text || msg.plain || msg.message || '').trim() || '(mensagem sem texto)';
        item.appendChild(head);
        item.appendChild(texto);
        _approvalAppendAnexos(item, _approvalAnexos(msg));
        box.appendChild(item);
      });
      return box;
    }

    function _approvalStatusTexto(payload) {
      const status = String((payload && payload.status) || '').toLowerCase();
      if (status === 'approved' || status === 'sent') return '\u2713 Aprovado e enviado ao Mercado Livre';
      if (status === 'rejected') return 'Rejeitado. Nada foi enviado ao Mercado Livre';
      if (status === 'answered_elsewhere') return String(payload.status_message || 'Respondida fora da aprova\u00e7\u00e3o.');
      if (status === 'error') return String(payload.status_message || 'N\u00e3o consegui processar essa aprova\u00e7\u00e3o.');
      return String(payload.status_message || '');
    }

    function _approvalStatusClasse(payload) {
      const status = String((payload && payload.status) || '').toLowerCase();
      if (status === 'approved' || status === 'sent') return 'approved';
      if (status === 'rejected') return 'rejected';
      if (status === 'answered_elsewhere') return 'approved';
      if (status === 'error') return 'error';
      return '';
    }

    function _approvalTextoDaMensagem(msg) {
      return String((msg && (msg.text || msg.plain || msg.message)) || '').trim();
    }

    function _approvalOrigemTexto(payload) {
      const isPosVenda = String(payload && (payload.tipo || payload.approval_type) || '').toLowerCase() === 'pos_venda';
      const origem = String(payload && (payload.ia_origem || payload.ai_origin) || '').trim();
      if (origem === 'mercado_livre_pos_venda' || isPosVenda) return 'IA de pos-venda do Mercado Livre';
      if (origem === 'mercado_livre_perguntas') return 'IA de respostas do Mercado Livre';
      return 'IA de respostas do Mercado Livre';
    }

    function _approvalHistoricoChat(msg) {
      const payload = _approvalParseMensagem(msg && msg.text);
      if (!payload) {
        return { role: (msg && msg.role) || 'assistant', content: String((msg && msg.text) || '').slice(0, 1200) };
      }
      const origem = _approvalOrigemTexto(payload);
      const modelo = String(payload.model || '').trim();
      const texto = [
        `Card de aprovacao do Mercado Livre gerado pela ${origem}.`,
        modelo ? `Modelo usado na sugestao: ${modelo}.` : '',
        _approvalTexto(payload),
      ].filter(Boolean).join('\n');
      return { role: 'assistant', content: texto.slice(0, 1200) };
    }

    function _approvalPerguntaParaTreinamento(payload) {
      const perguntaDireta = String((payload && payload.pergunta) || '').trim();
      if (perguntaDireta) return perguntaDireta;
      const mensagens = _approvalMensagens(payload);
      for (let i = mensagens.length - 1; i >= 0; i -= 1) {
        const msg = mensagens[i] || {};
        const role = String(msg.from_role || msg.role || '').toLowerCase();
        if (role === 'seller') continue;
        const texto = _approvalTextoDaMensagem(msg);
        if (texto) return texto;
      }
      return '';
    }

    function _approvalNormalizarExemplosTreinamento(exemplos) {
      return (Array.isArray(exemplos) ? exemplos : [])
        .map((item) => ({
          pergunta: String((item && (item.pergunta || item.question)) || '').trim(),
          resposta: String((item && (item.resposta || item.answer)) || '').trim(),
          sku: String((item && item.sku) || '').trim(),
          observacao: String((item && (item.observacao || item.obs)) || '').trim(),
          updated_at: (item && (item.updated_at || item.created_at)) || null,
        }))
        .filter((item) => item.pergunta && item.resposta)
        .slice(0, 60);
    }

    async function _approvalSalvarNoContextoIA(payload, respostaAtual) {
      payload = payload || {};
      const pergunta = _approvalPerguntaParaTreinamento(payload);
      const resposta = String(respostaAtual || '').trim();
      if (!pergunta) throw new Error('Nao encontrei a pergunta do comprador para salvar.');
      if (!resposta) throw new Error('Informe uma resposta antes de salvar no contexto.');

      const tipo = String(payload.tipo || payload.approval_type || '').toLowerCase() === 'pos_venda'
        ? 'pos_venda'
        : 'perguntas_anuncio';
      const sku = String(payload.sku || '').trim();
      const getResp = await window.__JK_IA_SIDEBAR_FETCH__('/api/mercadolivre/ia-treinamento', {
        headers: _authHeaders(),
        cache: 'no-store',
      });
      const data = await getResp.json().catch(() => ({}));
      if (!getResp.ok) throw new Error(data.detail || 'Erro ao carregar o contexto da IA.');

      const exemplosAtuais = _approvalNormalizarExemplosTreinamento(((data.exemplos || {})[tipo]) || []);
      const novoExemplo = {
        pergunta,
        resposta,
        sku,
        observacao: 'Salvo a partir da aprovacao da IA',
        updated_at: new Date().toISOString(),
      };
      const perguntaNorm = pergunta.toLowerCase();
      const skuNorm = sku.toLowerCase();
      const exemplos = [
        novoExemplo,
        ...exemplosAtuais.filter((item) => (
          String(item.pergunta || '').toLowerCase() !== perguntaNorm
          || String(item.sku || '').toLowerCase() !== skuNorm
        )),
      ].slice(0, 60);

      const orientacoes = tipo === 'pos_venda'
        ? String(data.orientacoes_pos_venda || '')
        : String(data.orientacoes_perguntas || data.orientacoes || '');

      const postResp = await window.__JK_IA_SIDEBAR_FETCH__('/api/mercadolivre/ia-treinamento', {
        method: 'POST',
        headers: _authHeaders(),
        body: JSON.stringify({
          tipo,
          orientacoes,
          contexto_loja: String(data.contexto_loja || ''),
          compatibilidade_autopecas: String(data.compatibilidade_autopecas || ''),
          proibicoes: String(data.proibicoes || ''),
          exemplos,
        }),
      });
      const postData = await postResp.json().catch(() => ({}));
      if (!postResp.ok) throw new Error(postData.detail || 'Erro ao salvar no contexto da IA.');
      return postData;
    }

    function _approvalPayloadComRespostaAtual(payload, respostaAtual, extras = {}) {
      return {
        ...(payload || {}),
        resposta_sugerida: String(respostaAtual || (payload && payload.resposta_sugerida) || '').trim(),
        ...extras,
      };
    }

    function _approvalUsarRespostaNaTela(payload, respostaAtual, opcoes = {}) {
      const detail = _approvalPayloadComRespostaAtual(payload, respostaAtual, {
        force: opcoes.force === true,
        focus: opcoes.focus !== false,
        source: 'ia-sidebar-approval',
      });
      const api = window.JKPerguntasPosVenda;
      if (api && typeof api.preencherRespostaSugerida === 'function') {
        return api.preencherRespostaSugerida(detail, {
          force: detail.force,
          focus: detail.focus,
          allowFallback: false,
        });
      }
      const pagina = String((location.pathname.split('/').pop() || '').split('?')[0] || '').toLowerCase();
      if (pagina !== 'perguntas_pos_venda.html') {
        return { ok: false, message: 'Abra a tela de Perguntas e pos venda para usar a sugestao.' };
      }
      try {
        window.dispatchEvent(new CustomEvent('jk:perguntas-pos-venda:usar-resposta', { detail }));
        return { ok: true, message: 'Sugestao enviada para a tela.' };
      } catch (_) {
        return { ok: false, message: 'Abra a tela de Perguntas e pos venda para usar a sugestao.' };
      }
    }

    function _approvalAtualizarStatusUsoTela(statusEl, resultado) {
      if (!statusEl || !resultado) return;
      statusEl.textContent = resultado.message || (resultado.ok ? 'Resposta copiada para a tela.' : 'Nao foi possivel copiar para a tela.');
    }

    function _approvalMontarStatus(card, payload) {
      const texto = _approvalStatusTexto(payload);
      if (!texto) return;
      card.dataset.resolved = '1';
      const statusBtn = document.createElement('button');
      statusBtn.type = 'button';
      statusBtn.disabled = true;
      statusBtn.className = `jk-ia-approval-status-btn ${_approvalStatusClasse(payload)}`;
      statusBtn.textContent = texto;
      card.appendChild(statusBtn);
    }

    function _approvalMontarCard(container, payload) {
      payload = payload || {};
      const approvalId = String(payload.id || '').trim();
      if (!approvalId || !container) return null;
      window.__JK_IA_APPROVAL_NOTIFIED__ = window.__JK_IA_APPROVAL_NOTIFIED__ || {};
      window.__JK_IA_APPROVAL_NOTIFIED__[approvalId] = true;
      const isPosVenda = String(payload.tipo || payload.approval_type || '').toLowerCase() === 'pos_venda';
      const statusAtual = String(payload.status || 'pending').toLowerCase();
      const resolvida = ['approved', 'sent', 'rejected', 'answered_elsewhere'].includes(statusAtual);

      container.innerHTML = '';
      const card = document.createElement('div');
      card.className = 'jk-ia-approval-card';
      card.dataset.approvalId = approvalId;

      const title = document.createElement('div');
      title.className = 'jk-ia-approval-title';
      title.textContent = resolvida ? 'Aprova\u00e7\u00e3o registrada' : 'Aprova\u00e7\u00e3o necess\u00e1ria';
      card.appendChild(title);

      const meta = document.createElement('div');
      meta.className = 'jk-ia-approval-meta';
      meta.textContent = isPosVenda
        ? `Loja ${payload.loja || '-'} - Pack ${payload.pack_id || '-'} - SKU ${payload.sku || '-'}`
        : `Loja ${payload.loja || '-'} - SKU ${payload.sku || '-'} - ${payload.titulo || payload.item_id || '-'}`;
      card.appendChild(meta);

      const descricaoAnuncio = !isPosVenda ? _approvalDescricaoAnuncio(payload) : '';
      if (descricaoAnuncio) {
        const descBox = document.createElement('div');
        descBox.className = 'jk-ia-approval-question';
        const descLabel = document.createElement('div');
        descLabel.className = 'jk-ia-approval-label';
        descLabel.textContent = 'Descricao do anuncio usada pela IA';
        const descText = document.createElement('div');
        descText.textContent = descricaoAnuncio;
        descBox.appendChild(descLabel);
        descBox.appendChild(descText);
        card.appendChild(descBox);
      }

      card.appendChild(_approvalCriarConversa(payload));

      const answerBox = document.createElement('div');
      answerBox.className = 'jk-ia-approval-answer';
      const answerLabel = document.createElement('div');
      answerLabel.className = 'jk-ia-approval-label';
      answerLabel.textContent = `${_approvalOrigemTexto(payload)} (edite antes de enviar)`;
      answerBox.appendChild(answerLabel);
      const origemMeta = document.createElement('div');
      origemMeta.className = 'jk-ia-approval-meta';
      origemMeta.textContent = [
        'Origem: motor de respostas do Mercado Livre',
        payload.model ? `Modelo: ${payload.model}` : '',
        payload.ia_modo ? `Modo: ${payload.ia_modo}` : '',
      ].filter(Boolean).join(' - ');
      answerBox.appendChild(origemMeta);
      const answerText = document.createElement('textarea');
      answerText.className = 'jk-ia-approval-edit';
      answerText.value = payload.resposta_sugerida || '';
      answerText.placeholder = 'Edite a resposta antes de aprovar e enviar...';
      answerText.disabled = resolvida;
      answerBox.appendChild(answerText);
      card.appendChild(answerBox);

      const actions = document.createElement('div');
      actions.className = 'jk-ia-approval-actions';
      const saveContext = document.createElement('button');
      saveContext.className = 'jk-ia-approval-btn';
      saveContext.type = 'button';
      saveContext.textContent = 'Salvar no contexto da IA';
      const saveStatus = document.createElement('span');
      saveStatus.className = 'jk-ia-approval-context-status';
      actions.appendChild(saveContext);
      actions.appendChild(saveStatus);

      if (resolvida) {
        card.appendChild(actions);
        _approvalMontarStatus(card, payload);
        container.appendChild(card);
        saveContext.addEventListener('click', async () => {
          saveContext.disabled = true;
          saveStatus.textContent = 'Salvando...';
          try {
            await _approvalSalvarNoContextoIA(payload, answerText.value || payload.resposta_enviada || payload.resposta_sugerida || '');
            saveContext.textContent = 'Salvo no contexto';
            saveStatus.textContent = 'Esta pergunta e resposta entraram no treino da IA.';
          } catch (error) {
            saveContext.disabled = false;
            saveStatus.textContent = error && error.message ? error.message : 'Erro ao salvar no contexto.';
          }
        });
        return card;
      }

      const approve = document.createElement('button');
      approve.className = 'jk-ia-approval-btn primary';
      approve.type = 'button';
      approve.textContent = 'Aprovar e enviar';
      const useInScreen = document.createElement('button');
      useInScreen.className = 'jk-ia-approval-btn';
      useInScreen.type = 'button';
      useInScreen.textContent = 'Usar na caixa de resposta';
      const reject = document.createElement('button');
      reject.className = 'jk-ia-approval-btn danger';
      reject.type = 'button';
      reject.textContent = 'Rejeitar';
      actions.appendChild(useInScreen);
      actions.appendChild(approve);
      actions.appendChild(reject);
      card.appendChild(actions);
      container.appendChild(card);

      window.setTimeout(() => {
        const resultado = _approvalUsarRespostaNaTela(payload, answerText.value, { force: false, focus: false });
        if (resultado && resultado.ok) _approvalAtualizarStatusUsoTela(saveStatus, resultado);
      }, 0);

      saveContext.addEventListener('click', async () => {
        saveContext.disabled = true;
        saveStatus.textContent = 'Salvando...';
        try {
          await _approvalSalvarNoContextoIA(payload, answerText.value || '');
          saveContext.textContent = 'Salvo no contexto';
          saveStatus.textContent = 'Esta pergunta e resposta entraram no treino da IA.';
        } catch (error) {
          saveContext.disabled = false;
          saveStatus.textContent = error && error.message ? error.message : 'Erro ao salvar no contexto.';
        }
      });

      useInScreen.addEventListener('click', () => {
        const resultado = _approvalUsarRespostaNaTela(payload, answerText.value, { force: true, focus: true });
        _approvalAtualizarStatusUsoTela(saveStatus, resultado);
      });

      async function responderAcao(url, statusFinal, enviarResposta = false) {
        approve.disabled = true;
        reject.disabled = true;
        useInScreen.disabled = true;
        saveContext.disabled = true;
        try {
          const body = { approval_id: approvalId };
          if (enviarResposta) body.resposta = answerText.value || '';
          const response = await window.__JK_IA_SIDEBAR_FETCH__(url, {
            method: 'POST',
            headers: _authHeaders(),
            body: JSON.stringify(body),
          });
          const data = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(data.detail || 'Falha ao processar aprova\u00e7\u00e3o.');
          const atualizado = {
            ...payload,
            resposta_sugerida: answerText.value || payload.resposta_sugerida || '',
            status: statusFinal,
            status_message: statusFinal === 'approved'
              ? '\u2713 Aprovado e enviado ao Mercado Livre'
              : 'Rejeitado. Nada foi enviado ao Mercado Livre',
            resolved_at: new Date().toISOString(),
          };
          _approvalSalvarNoHistorico(atualizado);
          _approvalPersistirNoHistoricoBlackJhon(atualizado);
          _approvalMontarCard(container, atualizado);
        } catch (error) {
          approve.disabled = false;
          reject.disabled = false;
          useInScreen.disabled = false;
          saveContext.disabled = false;
          const erroTexto = `N\u00e3o consegui processar essa aprova\u00e7\u00e3o: ${error && error.message ? error.message : error}`;
          const erro = { ...payload, status: 'error', status_message: erroTexto };
          const antigo = card.querySelector('.jk-ia-approval-status-btn.error');
          if (antigo) antigo.remove();
          _approvalMontarStatus(card, erro);
        }
      }

      approve.addEventListener('click', () => responderAcao('/api/mercadolivre/perguntas/aprovacoes/aprovar', 'approved', true));
      reject.addEventListener('click', () => responderAcao('/api/mercadolivre/perguntas/aprovacoes/rejeitar', 'rejected'));
      return card;
    }

    function _approvalPersistirNoHistoricoBlackJhon(payload) {
      if (!_usuarioLocalEhFull()) return;
      const approvalId = String(payload && payload.id || '').trim();
      if (!approvalId) return;
      if (!codexMessagesAtuais.length) codexMessagesAtuais = _codexLerHistoricoLocal();
      const idx = codexMessagesAtuais.findIndex(item => item && item.kind === 'approval'
        && String(item.approval_payload && item.approval_payload.id || '').trim() === approvalId);
      const anterior = idx >= 0 ? codexMessagesAtuais[idx] : null;
      const textoCompacto = _approvalSerializarMensagem(payload);
      const payloadCompacto = _approvalParseMensagem(textoCompacto) || { id: approvalId };
      const item = {
        role: 'assistant',
        text: textoCompacto,
        classExtra: 'black-jhon-approval',
        kind: 'approval',
        approval_payload: payloadCompacto,
        task_id: '',
        report_formats: [],
        at: String(anterior && anterior.at || new Date().toISOString()),
      };
      if (idx >= 0) codexMessagesAtuais[idx] = item;
      else codexMessagesAtuais.push(item);
      codexMessagesAtuais = codexMessagesAtuais.slice(-CODEX_HISTORY_LIMIT);
      _codexSalvarHistoricoLocal();
    }

    function _approvalRenderNoPainelBlackJhon(payload, options = {}) {
      if (!_usuarioLocalEhFull() || blackJhonUsandoIaSecundaria) return false;
      const approvalId = String(payload && payload.id || '').trim();
      const lista = document.getElementById('jk-codex-messages');
      if (!approvalId || !lista) return false;
      let card = Array.from(lista.querySelectorAll('.jk-ia-approval-card'))
        .find(item => String(item && item.dataset && item.dataset.approvalId || '').trim() === approvalId);
      let container = card && card.closest ? card.closest('.jk-codex-msg') : null;
      if (!container) {
        container = document.createElement('div');
        container.className = 'jk-codex-msg assistant black-jhon-approval';
        lista.appendChild(container);
      }
      _approvalPersistirNoHistoricoBlackJhon(payload);
      _approvalMontarCard(container, payload);
      if (options.abrirPainel !== false) {
        toggleBlackJhonPanel(true);
      } else if (!codexPanelAberto) {
        codexAprovacaoNaoVista = true;
      }
      _sidebarAtualizarAlertas();
      lista.scrollTop = lista.scrollHeight + 9999;
      return true;
    }

    function _adicionarNotificacaoAprovacao(payload, options = {}) {
      if (!_usuarioLocalEhFull()) return;
      payload = payload || {};
      const approvalId = String(payload.id || '').trim();
      if (!approvalId) return;
      const abrirPainel = options.abrirPainel !== false;
      window.__JK_IA_APPROVAL_NOTIFIED__ = window.__JK_IA_APPROVAL_NOTIFIED__ || {};
      const idxExistente = _approvalIndexNoHistorico(approvalId);
      const jaNotificada = !!window.__JK_IA_APPROVAL_NOTIFIED__[approvalId];
      if (jaNotificada && idxExistente >= 0) {
        if (_approvalRenderNoPainelBlackJhon(payload, { abrirPainel })) return;
        if (abrirPainel) {
          mostrarChat();
          togglePanel(true);
          renderMsgs();
        } else {
          _iaAvisarMensagemRecebida();
        }
        return;
      }
      window.__JK_IA_APPROVAL_NOTIFIED__[approvalId] = true;

      if (idxExistente < 0) {
        _approvalSalvarNoHistorico(payload);
      }

      if (_approvalRenderNoPainelBlackJhon(payload, { abrirPainel })) {
        void _perguntasNotificarWindowsAprovacaoUmaVez(payload);
        return;
      }

      const deveRenderizarAgora = abrirPainel || panelAberto === true;
      if (deveRenderizarAgora) {
        mostrarChat();
        if (abrirPainel) togglePanel(true);
        renderMsgs();
        const msgsEl = document.getElementById('jk-ia-msgs');
        if (msgsEl) msgsEl.scrollTop = 99999;
      } else {
        _iaAvisarMensagemRecebida();
      }
      void _perguntasNotificarWindowsAprovacaoUmaVez(payload);
    }

    function _resolverNotificacaoAprovacao(approvalId, mensagem) {
      if (!_usuarioLocalEhFull()) return;
      const id = String(approvalId || '').trim();
      if (!id) return;
      if (Array.isArray(window.__JK_PENDING_IA_APPROVALS__)) {
        window.__JK_PENDING_IA_APPROVALS__ = window.__JK_PENDING_IA_APPROVALS__.filter((item) => String((item && item.id) || '').trim() !== id);
      }
      const idx = _approvalIndexNoHistorico(id);
      let payloadHistorico = idx >= 0 ? _approvalParseMensagem(mensagensAtuais[idx].text) : null;
      const statusAtual = String((payloadHistorico && payloadHistorico.status) || '').toLowerCase();
      if (['approved', 'sent', 'rejected'].includes(statusAtual)) return;
      if (payloadHistorico) {
        payloadHistorico = {
          ...payloadHistorico,
          status: 'answered_elsewhere',
          status_message: mensagem || 'Respondida fora da aprova\u00e7\u00e3o.',
          resolved_at: new Date().toISOString(),
        };
        _approvalSalvarNoHistorico(payloadHistorico);
        _approvalPersistirNoHistoricoBlackJhon(payloadHistorico);
      }
      document.querySelectorAll('.jk-ia-approval-card').forEach((card) => {
        if (String((card.dataset && card.dataset.approvalId) || '').trim() !== id) return;
        if (payloadHistorico) {
          _approvalMontarCard(card.closest('.jk-ia-msg') || card.parentElement, payloadHistorico);
          return;
        }
        card.dataset.resolved = '1';
        const actions = card.querySelector('.jk-ia-approval-actions');
        if (actions) actions.remove();
        const edit = card.querySelector('.jk-ia-approval-edit');
        if (edit) edit.disabled = true;
        const antigo = card.querySelector('.jk-ia-approval-status-btn');
        if (antigo) antigo.remove();
        _approvalMontarStatus(card, {
          status: 'answered_elsewhere',
          status_message: mensagem || 'Respondida fora da aprova\u00e7\u00e3o.',
        });
      });
    }

    window.JKIASidebarNotifyApproval = (payload, options) => _adicionarNotificacaoAprovacao(payload, options || {});
    window.JKIASidebarResolveApproval = _resolverNotificacaoAprovacao;
    window.addEventListener('jk-ia-approval', (event) => _adicionarNotificacaoAprovacao(event.detail || {}));
    window.addEventListener('jk-ia-approval-resolved', (event) => {
      const detail = event.detail || {};
      _resolverNotificacaoAprovacao(detail.id || detail.approval_id, detail.mensagem || detail.message);
    });
    setTimeout(() => {
      const pendentes = Array.isArray(window.__JK_PENDING_IA_APPROVALS__) ? window.__JK_PENDING_IA_APPROVALS__ : [];
      window.__JK_PENDING_IA_APPROVALS__ = [];
      pendentes.forEach((payload) => _adicionarNotificacaoAprovacao(payload, { abrirPainel: false }));
    }, 0);
