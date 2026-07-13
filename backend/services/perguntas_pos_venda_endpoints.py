"""Endpoint implementations for Perguntas e Pos-venda.

The heavy helper graph lives in dedicated service modules. During startup,
``backend_api`` injects the assembled runtime globals here so the router can bind
stable endpoint callables without registering monolith-local functions.
"""

from __future__ import annotations

import inspect
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse

from backend.services.vendas_sync_progress import _corrigir_texto_mojibake


PERGUNTAS_POS_VENDA_ENDPOINTS: tuple[str, ...] = ('ml_perguntas_listar_lojas', 'ml_perguntas_salvar_config_loja', 'ml_perguntas_salvar_config_lojas_lote', 'ml_perguntas_automacao_poll', 'ml_perguntas_aprovacoes_listar', 'ml_perguntas_aprovacoes_aprovar', 'ml_perguntas_aprovacoes_rejeitar', 'ml_questions_v2_webhook', 'ml_questions_v2_process', 'ml_questions_v2_pending_review', 'ml_questions_v2_review_approve', 'ml_questions_v2_review_reject', 'ml_questions_v2_audit_question', 'ml_questions_v2_metrics', 'ml_perguntas_gerar_resposta_manual', 'ml_perguntas_responder_manual', 'ml_ia_treinamento_obter', 'ml_ia_treinamento_salvar', 'ml_ia_treinamento_listar_skus', 'ml_ia_treinamento_simular', 'ml_pos_venda_listar_conversas', 'ml_pos_venda_listar_mediacoes', 'ml_pos_venda_detalhe_conversa', 'ml_pos_venda_obter_anexo', 'ml_pos_venda_gerar_resposta_conversa', 'ml_pos_venda_responder_conversa', 'ml_pos_venda_automacao_poll', 'ml_listar_perguntas')

_TENANT_DEPENDENCY = None
_PROTECTED_GLOBALS = {
    "_TENANT_DEPENDENCY",
    "_PROTECTED_GLOBALS",
    "PERGUNTAS_POS_VENDA_ENDPOINTS",
    "configure_perguntas_pos_venda_endpoints_runtime",
    "get_tenant_id",
}


async def get_tenant_id(request: Request, authorization: Optional[str] = Header(default=None)):
    if _TENANT_DEPENDENCY is None:
        raise RuntimeError("perguntas_pos_venda_endpoints runtime was not configured")
    result = _TENANT_DEPENDENCY(request, authorization)
    if inspect.isawaitable(result):
        return await result
    return result


def _copy_runtime_globals(legacy_module) -> None:
    endpoint_names = set(PERGUNTAS_POS_VENDA_ENDPOINTS)
    for name in dir(legacy_module):
        if name.startswith("__") or name in endpoint_names or name in _PROTECTED_GLOBALS:
            continue
        globals()[name] = getattr(legacy_module, name)


def configure_perguntas_pos_venda_endpoints_runtime(legacy_module) -> None:
    global _TENANT_DEPENDENCY
    _TENANT_DEPENDENCY = getattr(legacy_module, "get_tenant_id")
    _copy_runtime_globals(legacy_module)


def ml_perguntas_listar_lojas(client_id: str = Depends(get_tenant_id)):
    lojas = []
    configs_lojas = _perguntas_loja_configs_carregar(client_id)
    lojas_index = set()
    for loja in carregar_lojas(client_id) or []:
        if not isinstance(loja, dict):
            continue
        nome = _corrigir_texto_mojibake(str(loja.get("nome") or "").strip())
        if not nome:
            continue
        lojas_index.add(_integracoes_nome_normalizado(nome))
        integracoes = loja.get("integracoes") or {}
        cfg = integracoes.get("mercadolivre") if isinstance(integracoes, dict) else {}
        ml_status = _ml_oauth_status(cfg)
        conectado = bool(ml_status.get("conectado"))
        lojas.append({
            "nome": nome,
            "mercadolivre_conectado": conectado,
            "mercadolivre_status": ml_status.get("status") or ("conectado" if conectado else "pendente"),
            "mercadolivre_motivo": ml_status.get("motivo") or "",
            "mercadolivre_oauth_faltando": ml_status.get("faltando") or [],
            "seller_id": str((cfg or {}).get("user_id") or "").strip(),
            "config_perguntas": _perguntas_loja_config_normalizar(_perguntas_loja_config_obter(configs_lojas, nome)),
            "precisa_reintegrar": False,
        })

    for nome_config, config in (configs_lojas or {}).items():
        nome = _corrigir_texto_mojibake(str(nome_config or "").strip())
        nome_norm = _integracoes_nome_normalizado(nome)
        if not nome or not nome_norm or nome_norm in lojas_index:
            continue
        lojas_index.add(nome_norm)
        lojas.append({
            "nome": nome,
            "mercadolivre_conectado": False,
            "mercadolivre_status": "reautenticar",
            "mercadolivre_motivo": "Loja tinha configuracao em Perguntas e pos-venda, mas nao esta mais autenticada em Integracoes.",
            "mercadolivre_oauth_faltando": ["access_token", "refresh_token", "app_id", "client_secret"],
            "seller_id": "",
            "config_perguntas": _perguntas_loja_config_normalizar(config),
            "precisa_reintegrar": True,
        })

    lojas.sort(key=lambda item: (
        0 if item.get("mercadolivre_conectado") else 1,
        _integracoes_nome_normalizado(item.get("nome")),
    ))
    return {"success": True, "lojas": lojas}


def ml_perguntas_salvar_config_loja(req: PerguntasLojaConfigRequest, client_id: str = Depends(get_tenant_id)):
    config = _perguntas_loja_config_salvar(
        client_id,
        req.loja,
        req.responder_automaticamente,
        req.solicitar_aprovacao,
        req.notificar_whatsapp_aprovacoes,
        req.habilitar_pos_venda_automatico,
        req.intervalo_minutos,
    )
    return {"success": True, "loja": req.loja, "config_perguntas": config}


def ml_perguntas_salvar_config_lojas_lote(req: PerguntasLojasConfigLoteRequest, client_id: str = Depends(get_tenant_id)):
    configs_lojas = _perguntas_loja_configs_carregar(client_id)
    intervalo_cfg = _perguntas_loja_config_normalizar({"intervalo_minutos": req.intervalo_minutos})
    intervalo_minutos = intervalo_cfg["intervalo_minutos"]
    atualizadas = []
    ignoradas = []
    for loja_cfg in carregar_lojas(client_id) or []:
        if not isinstance(loja_cfg, dict):
            continue
        nome = _corrigir_texto_mojibake(str(loja_cfg.get("nome") or "").strip())
        if not nome:
            continue
        integracoes = loja_cfg.get("integracoes") or {}
        ml_cfg = integracoes.get("mercadolivre") if isinstance(integracoes, dict) else {}
        conectado = bool(_ml_oauth_status(ml_cfg).get("conectado"))
        if req.somente_conectadas and not conectado:
            ignoradas.append({"loja": nome, "motivo": "mercado_livre_desconectado"})
            continue
        config_atual = _perguntas_loja_config_normalizar(_perguntas_loja_config_obter(configs_lojas, nome))
        config = _perguntas_loja_config_salvar(
            client_id,
            nome,
            config_atual.get("responder_automaticamente") is True,
            config_atual.get("solicitar_aprovacao") is True,
            config_atual.get("notificar_whatsapp_aprovacoes") is True,
            config_atual.get("habilitar_pos_venda_automatico") is True,
            intervalo_minutos,
        )
        atualizadas.append({"loja": nome, "config_perguntas": config})
    if not atualizadas:
        raise HTTPException(status_code=400, detail="Nenhuma conta Mercado Livre conectada para atualizar.")
    return {
        "success": True,
        "intervalo_minutos": intervalo_minutos,
        "atualizadas": atualizadas,
        "ignoradas": ignoradas,
        "total": len(atualizadas),
    }


def ml_perguntas_automacao_poll(
    loja: Optional[str] = None,
    max_per_store: int = 3,
    client_id: str = Depends(get_tenant_id),
):
    state = _perguntas_ia_state_carregar(client_id)
    aprovacoes = _perguntas_ia_aprovacoes_carregar(client_id)
    configs_lojas = _perguntas_loja_configs_carregar(client_id)
    loja_filtro = str(loja or "").strip()
    max_per_store = max(1, min(int(max_per_store or 3), 5))
    limite_busca = min(50, max(20, max_per_store))
    request_fn = _ml_api_request
    novas_pendentes = []
    enviadas = []
    erros = []
    mudou_aprovacoes = False
    mudou_state = False

    for loja_cfg in carregar_lojas(client_id) or []:
        if not isinstance(loja_cfg, dict):
            continue
        nome_loja = str(loja_cfg.get("nome") or "").strip()
        if not nome_loja or (loja_filtro and nome_loja != loja_filtro):
            continue
        config = _perguntas_loja_config_normalizar(configs_lojas.get(nome_loja))
        if not config.get("responder_automaticamente"):
            continue

        integracoes = loja_cfg.get("integracoes") or {}
        ml_cfg = integracoes.get("mercadolivre") if isinstance(integracoes, dict) else {}
        if not _ml_oauth_status(ml_cfg).get("conectado"):
            continue

        try:
            cfg = _obter_cfg_ml(client_id, nome_loja)
            seller_id = str(cfg.get("user_id") or "").strip()
            if not seller_id:
                continue
            resp, cfg = request_fn(
                client_id,
                nome_loja,
                cfg,
                "GET",
                "https://api.mercadolibre.com/questions/search",
                params={
                    "seller_id": seller_id,
                    "api_version": 4,
                    "status": "UNANSWERED",
                    "sort_fields": "date_created",
                    "sort_types": "DESC",
                    "limit": limite_busca,
                    "offset": 0,
                },
                timeout=20,
            )
            if resp.status_code != 200:
                erros.append({"loja": nome_loja, "erro": _ml_parse_error_detail(resp, "Erro ao buscar perguntas para automacao")})
                continue
            payload = resp.json() or {}
            perguntas = payload.get("questions") or payload.get("results") or []
            perguntas = [q for q in perguntas if isinstance(q, dict)]
            item_ids = list(dict.fromkeys([str(q.get("item_id") or "").strip() for q in perguntas if str(q.get("item_id") or "").strip()]))
            itens, cfg = _ml_buscar_itens_batch(client_id, nome_loja, cfg, item_ids)
            itens = _ml_perguntas_completar_skus_itens(client_id, nome_loja, cfg, itens)
            item_por_id = {str(item.get("id") or "").strip(): item for item in itens if isinstance(item, dict)}
            user_ids = []
            user_ids_vistos = set()
            for pergunta in perguntas:
                comprador = pergunta.get("from") if isinstance(pergunta.get("from"), dict) else {}
                user_id = str(comprador.get("id") or "").strip()
                if user_id and user_id not in user_ids_vistos:
                    user_ids_vistos.add(user_id)
                    user_ids.append(user_id)
                if len(user_ids) >= 500:
                    break
            usuario_por_id, cfg = _ml_perguntas_buscar_usuarios(client_id, nome_loja, cfg, user_ids)
            perguntas_norm = [_ml_perguntas_normalizar(pergunta, item_por_id, usuario_por_id) for pergunta in perguntas]
            perguntas_norm, cfg = _ml_perguntas_anexar_historico_comprador(client_id, nome_loja, cfg, seller_id, perguntas_norm)

            processadas_loja = 0
            for pergunta in perguntas_norm:
                question_id = str(pergunta.get("id") or "").strip()
                if not question_id:
                    continue
                if str(pergunta.get("status") or "").upper() != "UNANSWERED":
                    continue
                if pergunta.get("hold") or pergunta.get("deleted_from_listing") or pergunta.get("suspected_spam"):
                    continue
                if _perguntas_ia_ja_processada(state, nome_loja, question_id):
                    continue
                if _perguntas_ia_aprovacao_pendente(aprovacoes, nome_loja, question_id):
                    continue
                if processadas_loja >= max_per_store:
                    break

                item = item_por_id.get(str(pergunta.get("item_id") or "").strip()) or {}
                try:
                    resposta, cfg, contexto = _perguntas_ia_gerar_resposta(client_id, nome_loja, cfg, pergunta, item)
                except PerguntasIARespostaIndisponivel as exc:
                    logger.warning(
                        "[ML PERGUNTAS IA] Resposta bloqueada para a loja %s, pergunta %s: %s",
                        nome_loja,
                        question_id,
                        exc,
                    )
                    erros.append({"loja": nome_loja, "question_id": question_id, "erro": str(exc)})
                    continue
                if not resposta:
                    continue

                if config.get("solicitar_aprovacao") or contexto.get("ia_requer_revisao_humana") or _perguntas_ia_v2_exigir_aprovacao():
                    intencao_ctx = contexto.get("intencao_atendimento") if isinstance(contexto.get("intencao_atendimento"), dict) else {}
                    approval = {
                        "id": _perguntas_ia_aprovacao_id(nome_loja, question_id),
                        "status": "pending",
                        "loja": nome_loja,
                        "question_id": question_id,
                        "item_id": contexto.get("item_id") or "",
                        "sku": contexto.get("sku") or "",
                        "titulo": contexto.get("titulo") or "",
                        "permalink": contexto.get("permalink") or "",
                        "descricao_anuncio": str(contexto.get("descricao") or "")[:2500],
                        "pergunta": contexto.get("pergunta") or "",
                        "mensagens": _perguntas_ia_mensagens_aprovacao(pergunta, nome_loja),
                        "resposta_sugerida": resposta,
                        "model": contexto.get("model") or "",
                        "ia_origem": "mercado_livre_perguntas",
                        "ia_finalidade": intencao_ctx.get("fluxo") or "perguntas_anuncio",
                        "ia_intencao": intencao_ctx,
                        "ia_modo": contexto.get("modo_ia") or _ia_modo_perguntas_configurado(),
                        "aprovacao_obrigatoria_ia": _perguntas_ia_v2_exigir_aprovacao(),
                        "ia_decision": contexto.get("ia_decision") or "",
                        "ia_categoria": contexto.get("ia_categoria") or "",
                        "ia_validacao_ok": contexto.get("ia_validacao_ok"),
                        "ia_validacao_issues": contexto.get("ia_validacao_issues") or [],
                        "ia_requer_revisao_humana": bool(contexto.get("ia_requer_revisao_humana")),
                        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
                    }
                    aprovacoes.append(approval)
                    novas_pendentes.append(approval)
                    processadas_loja += 1
                    mudou_aprovacoes = True
                else:
                    resposta_ml, cfg = _perguntas_ia_enviar_resposta_ml(client_id, nome_loja, cfg, question_id, resposta)
                    _perguntas_ia_marcar_processada(state, nome_loja, question_id, "sent_auto")
                    processadas_loja += 1
                    mudou_state = True
                    enviadas.append({
                        "loja": nome_loja,
                        "question_id": question_id,
                        "item_id": contexto.get("item_id") or "",
                        "sku": contexto.get("sku") or "",
                        "titulo": contexto.get("titulo") or "",
                        "resposta": resposta,
                        "mercadolivre": resposta_ml,
                    })
        except HTTPException as exc:
            erros.append({"loja": nome_loja, "erro": exc.detail})
        except Exception as exc:
            logger.exception("[ML PERGUNTAS IA] Falha na automacao da loja %s: %s", nome_loja, exc)
            erros.append({"loja": nome_loja, "erro": str(exc)})

    if mudou_aprovacoes:
        _perguntas_ia_aprovacoes_salvar(client_id, aprovacoes)
    if mudou_state:
        _perguntas_ia_state_salvar(client_id, state)
    pendentes = [a for a in aprovacoes if isinstance(a, dict) and str(a.get("status") or "pending") == "pending"]
    return jsonable_encoder({
        "success": True,
        "novas_pendentes": novas_pendentes,
        "pendentes": pendentes,
        "enviadas": enviadas,
        "erros": erros,
    })


def ml_perguntas_aprovacoes_listar(client_id: str = Depends(get_tenant_id)):
    aprovacoes = _perguntas_ia_aprovacoes_carregar(client_id)
    mudou = False
    mudou_state = False
    state = None
    pendentes = []
    for approval in aprovacoes:
        if not isinstance(approval, dict) or str(approval.get("status") or "pending") != "pending":
            continue
        tipo = str(approval.get("tipo") or approval.get("approval_type") or "").strip().lower()
        loja = str(approval.get("loja") or "").strip()
        question_id = str(approval.get("question_id") or "").strip()
        origem_padrao = "mercado_livre_pos_venda" if tipo == "pos_venda" else "mercado_livre_perguntas"
        finalidade_padrao = "pos_venda" if tipo == "pos_venda" else "perguntas_anuncio"
        if not approval.get("ia_origem"):
            approval["ia_origem"] = origem_padrao
            mudou = True
        if not approval.get("ia_finalidade"):
            approval["ia_finalidade"] = finalidade_padrao
            mudou = True
        if not approval.get("ia_modo"):
            approval["ia_modo"] = _ia_modo_pos_venda_configurado() if tipo == "pos_venda" else _ia_modo_perguntas_configurado()
            mudou = True
        if tipo == "pos_venda":
            pack_id = str(approval.get("pack_id") or "").strip()
            order_id = str(approval.get("order_id") or "").strip()
            try:
                if loja and pack_id:
                    cfg = _obter_cfg_ml(client_id, loja)
                    conversa, cfg = _ml_pos_venda_montar_conversa_normalizada(client_id, loja, cfg, pack_id, order_id)
                    if _ml_pos_venda_conversa_respondida_pela_loja(conversa):
                        if _perguntas_ia_resolver_aprovacao(approval, "answered_elsewhere", "pos_venda_respondido_por_outro_fluxo"):
                            state = state or _perguntas_ia_state_carregar(client_id)
                            _perguntas_ia_marcar_processada(state, loja, question_id, "answered_elsewhere")
                            mudou = True
                            mudou_state = True
                        continue
                    conversa_aprovacao = {
                        "pack_id": conversa.get("pack_id") or pack_id,
                        "order_id": conversa.get("order_id") or order_id,
                        "buyer_id": conversa.get("buyer_id") or approval.get("buyer_id") or "",
                        "buyer_nickname": conversa.get("buyer_nickname") or "",
                        "items": conversa.get("items") or [],
                        "messages": conversa.get("messages") or [],
                        "last_message_text": conversa.get("last_message_text") or approval.get("pergunta") or "",
                        "seller_max_message_length": conversa.get("seller_max_message_length") or approval.get("max_chars") or ML_POS_VENDA_DEFAULT_MAX_CHARS,
                    }
                    approval["conversa"] = conversa_aprovacao
                    approval["mensagens"] = conversa_aprovacao["messages"]
                    approval["buyer_id"] = approval.get("buyer_id") or conversa_aprovacao["buyer_id"]
                    approval["pergunta"] = approval.get("pergunta") or conversa_aprovacao["last_message_text"]
                    mudou = True
            except Exception as exc:
                logger.warning("[PERGUNTAS IA] Nao foi possivel validar aprovacao pos-venda %s: %s", approval.get("id"), exc)
        else:
            try:
                if loja and question_id:
                    cfg = _obter_cfg_ml(client_id, loja)
                    respondida, pergunta_ml, cfg = _perguntas_ia_pergunta_respondida_ml(client_id, loja, cfg, question_id)
                    if respondida:
                        resposta_ml = ""
                        answer = pergunta_ml.get("answer") if isinstance(pergunta_ml.get("answer"), dict) else {}
                        if isinstance(answer, dict):
                            resposta_ml = str(answer.get("text") or "").strip()
                        if _perguntas_ia_resolver_aprovacao(approval, "answered_elsewhere", "pergunta_respondida_por_outro_fluxo", resposta_ml):
                            state = state or _perguntas_ia_state_carregar(client_id)
                            _perguntas_ia_marcar_processada(state, loja, question_id, "answered_elsewhere")
                            mudou = True
                            mudou_state = True
                        continue
            except Exception as exc:
                logger.warning("[PERGUNTAS IA] Nao foi possivel validar aprovacao de pergunta %s: %s", approval.get("id"), exc)
        pendentes.append(approval)
    if mudou:
        _perguntas_ia_aprovacoes_salvar(client_id, aprovacoes)
    if mudou_state and isinstance(state, dict):
        _perguntas_ia_state_salvar(client_id, state)
    return {"success": True, "pendentes": pendentes}


def ml_perguntas_aprovacoes_aprovar(req: PerguntasAprovacaoRequest, client_id: str = Depends(get_tenant_id)):
    approval_id = str(req.approval_id or "").strip()
    aprovacoes = _perguntas_ia_aprovacoes_carregar(client_id)
    idx = next((i for i, item in enumerate(aprovacoes) if isinstance(item, dict) and str(item.get("id") or "") == approval_id), -1)
    if idx < 0:
        raise HTTPException(status_code=404, detail="AprovaÃ§Ã£o nÃ£o encontrada.")
    approval = aprovacoes[idx]
    if str(approval.get("status") or "pending") != "pending":
        return {"success": True, "approval": approval}

    loja = str(approval.get("loja") or "").strip()
    question_id = str(approval.get("question_id") or "").strip()
    resposta_editada = req.resposta if req.resposta is not None else req.texto
    resposta_base = resposta_editada if resposta_editada is not None else approval.get("resposta_sugerida")
    resposta = str(resposta_base or "").strip()
    cfg = _obter_cfg_ml(client_id, loja)
    tipo_aprovacao = str(approval.get("tipo") or approval.get("approval_type") or "").strip().lower()
    if tipo_aprovacao == "pos_venda":
        pack_id = str(approval.get("pack_id") or "").strip()
        buyer_id = str(approval.get("buyer_id") or "").strip()
        max_chars = int(approval.get("max_chars") or ML_POS_VENDA_DEFAULT_MAX_CHARS)
        resposta = _pos_venda_ia_limpar_resposta(resposta, max_chars)
        if not resposta:
            raise HTTPException(status_code=400, detail="Informe uma resposta antes de aprovar.")
        resposta_ml, cfg = _ml_pos_venda_enviar_resposta_ml(client_id, loja, cfg, pack_id, buyer_id, resposta, max_chars)
    else:
        resposta = _perguntas_ia_limpar_resposta(resposta)
        if not resposta:
            raise HTTPException(status_code=400, detail="Informe uma resposta antes de aprovar.")
        resposta_ml, cfg = _perguntas_ia_enviar_resposta_ml(client_id, loja, cfg, question_id, resposta)
    approval["status"] = "sent"
    approval["sent_at"] = dt.datetime.now().isoformat(timespec="seconds")
    approval["resposta_enviada"] = resposta
    approval["mercadolivre"] = resposta_ml
    aprovacoes[idx] = approval
    _perguntas_ia_aprovacoes_salvar(client_id, aprovacoes)
    state = _perguntas_ia_state_carregar(client_id)
    _perguntas_ia_marcar_processada(state, loja, question_id, "sent_approved")
    _perguntas_ia_state_salvar(client_id, state)
    try:
        if tipo_aprovacao == "pos_venda":
            conversa_aprovacao = approval.get("conversa") if isinstance(approval.get("conversa"), dict) else {}
            try:
                conversa_aprovacao, cfg = _ml_pos_venda_preparar_conversa_ia(client_id, loja, cfg, conversa_aprovacao)
            except Exception as exc:
                logger.warning("[ML POS VENDA IA] Falha ao carregar perguntas do anuncio para aprovacao: %s", exc)
            registrados = _ml_pos_venda_memoria_registrar_resposta_enviada(
                client_id,
                loja,
                conversa_aprovacao,
                resposta,
                origem="aprovacao_pos_venda",
                approval=approval,
            )
            if not registrados:
                _perguntas_ia_memoria_registrar_resposta_aprovada(
                    client_id,
                    loja,
                    resposta,
                    approval=approval,
                    origem="aprovacao_pos_venda",
                    question_id=question_id,
                )
        else:
            _perguntas_ia_memoria_registrar_resposta_aprovada(
                client_id,
                loja,
                resposta,
                approval=approval,
                origem="aprovacao",
                question_id=question_id,
            )
    except Exception as exc:
        logger.warning("[ML PERGUNTAS IA] Falha ao registrar resposta aprovada na memoria do SKU: %s", exc)
    return {"success": True, "approval": approval}


def ml_perguntas_aprovacoes_rejeitar(req: PerguntasAprovacaoRequest, client_id: str = Depends(get_tenant_id)):
    approval_id = str(req.approval_id or "").strip()
    aprovacoes = _perguntas_ia_aprovacoes_carregar(client_id)
    idx = next((i for i, item in enumerate(aprovacoes) if isinstance(item, dict) and str(item.get("id") or "") == approval_id), -1)
    if idx < 0:
        raise HTTPException(status_code=404, detail="AprovaÃ§Ã£o nÃ£o encontrada.")
    approval = aprovacoes[idx]
    approval["status"] = "rejected"
    approval["rejected_at"] = dt.datetime.now().isoformat(timespec="seconds")
    aprovacoes[idx] = approval
    _perguntas_ia_aprovacoes_salvar(client_id, aprovacoes)
    state = _perguntas_ia_state_carregar(client_id)
    _perguntas_ia_marcar_processada(state, str(approval.get("loja") or ""), str(approval.get("question_id") or ""), "rejected")
    _perguntas_ia_state_salvar(client_id, state)
    return {"success": True, "approval": approval}


async def ml_questions_v2_webhook(request: Request, client_id: str = Depends(get_tenant_id)):
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Payload JSON invalido.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Payload JSON precisa ser um objeto.")
    receiver = MercadoLivreWebhookReceiver()
    event = receiver.normalize_event(payload)
    event["id"] = str(uuid.uuid4())
    event["received_at"] = dt.datetime.now().isoformat(timespec="seconds")
    event["status"] = "received"
    caminho = _ml_questions_v2_webhook_events_path(client_id)
    events = _perguntas_ia_ler_json(caminho, [])
    if not isinstance(events, list):
        events = []
    events.append(event)
    _perguntas_ia_salvar_json(caminho, events[-1000:])
    return {"success": True, "event": event, "processed": False}


def ml_questions_v2_process(question_id: str, req: MLQuestionsV2ProcessRequest, client_id: str = Depends(get_tenant_id)):
    loja = str(req.loja or "").strip()
    if not loja:
        raise HTTPException(status_code=400, detail="Informe a loja.")
    pergunta = req.pergunta if isinstance(req.pergunta, dict) else {}
    pergunta = {**pergunta, "id": str(question_id or pergunta.get("id") or "").strip()}
    if req.resposta_atual:
        pergunta["_resposta_atual"] = str(req.resposta_atual or "")[:1200]
    if not pergunta.get("id"):
        raise HTTPException(status_code=400, detail="Informe a pergunta.")
    if not str(pergunta.get("text") or "").strip() and not pergunta.get("buyer_question_chat"):
        raise HTTPException(status_code=400, detail="Informe o texto da pergunta.")
    item = req.item if isinstance(req.item, dict) else {}
    cfg = _obter_cfg_ml(client_id, loja)
    try:
        resposta, cfg, contexto = _perguntas_ia_gerar_resposta(client_id, loja, cfg, pergunta, item)
    except PerguntasIARespostaIndisponivel as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "success": True,
        "question_id": pergunta.get("id"),
        "answer": resposta,
        "context": contexto,
        "publish_attempted": False,
    }


def ml_questions_v2_pending_review(client_id: str = Depends(get_tenant_id)):
    return ml_perguntas_aprovacoes_listar(client_id)


def ml_questions_v2_review_approve(
    approval_id: str,
    req: MLQuestionsV2ReviewActionRequest,
    client_id: str = Depends(get_tenant_id),
):
    return ml_perguntas_aprovacoes_aprovar(
        PerguntasAprovacaoRequest(approval_id=approval_id, resposta=req.resposta, texto=req.texto),
        client_id,
    )


def ml_questions_v2_review_reject(
    approval_id: str,
    req: MLQuestionsV2ReviewActionRequest,
    client_id: str = Depends(get_tenant_id),
):
    return ml_perguntas_aprovacoes_rejeitar(
        PerguntasAprovacaoRequest(approval_id=approval_id, resposta=req.resposta, texto=req.texto),
        client_id,
    )


def ml_questions_v2_audit_question(question_id: str, client_id: str = Depends(get_tenant_id)):
    qid = str(question_id or "").strip()
    aprovacoes = _perguntas_ia_aprovacoes_carregar(client_id)
    eventos = _perguntas_ia_ler_json(_ml_questions_v2_webhook_events_path(client_id), [])
    if not isinstance(eventos, list):
        eventos = []
    eventos_match = [
        event for event in eventos
        if isinstance(event, dict) and qid and qid in json.dumps(event, ensure_ascii=False, default=str)
    ]
    aprovacoes_match = [
        item for item in aprovacoes
        if isinstance(item, dict) and str(item.get("question_id") or "") == qid
    ]
    return {
        "success": True,
        "question_id": qid,
        "approvals": aprovacoes_match,
        "webhook_events": eventos_match[-50:],
    }


def ml_questions_v2_metrics(client_id: str = Depends(get_tenant_id)):
    aprovacoes = _perguntas_ia_aprovacoes_carregar(client_id)
    metrics = {
        "total_reviews": 0,
        "pending": 0,
        "sent": 0,
        "rejected": 0,
        "by_category": {},
        "by_decision": {},
    }
    for item in aprovacoes:
        if not isinstance(item, dict):
            continue
        metrics["total_reviews"] += 1
        status = str(item.get("status") or "pending").strip() or "pending"
        if status in metrics:
            metrics[status] += 1
        category = str(item.get("ia_categoria") or item.get("ia_finalidade") or "unknown").strip() or "unknown"
        decision = str(item.get("ia_decision") or "human_review").strip() or "human_review"
        metrics["by_category"][category] = metrics["by_category"].get(category, 0) + 1
        metrics["by_decision"][decision] = metrics["by_decision"].get(decision, 0) + 1
    return {"success": True, "metrics": metrics}


def ml_perguntas_gerar_resposta_manual(req: PerguntasGerarRespostaRequest, client_id: str = Depends(get_tenant_id)):
    loja = str(req.loja or "").strip()
    pergunta = req.pergunta if isinstance(req.pergunta, dict) else {}
    resposta_atual = str(req.resposta_atual or "").strip()
    if resposta_atual:
        pergunta = {**pergunta, "_resposta_atual": resposta_atual[:1200]}
    orientacao_usuario = str(req.orientacao_usuario or "").strip()
    if orientacao_usuario:
        pergunta = {**pergunta, "_orientacao_usuario": orientacao_usuario[:1200]}
    if not loja:
        raise HTTPException(status_code=400, detail="Informe a loja.")
    if not str(pergunta.get("id") or "").strip():
        raise HTTPException(status_code=400, detail="Informe a pergunta.")

    cfg = _obter_cfg_ml(client_id, loja)
    item_id = str(pergunta.get("item_id") or "").strip()
    item = {}
    if item_id:
        try:
            resp_item, cfg = _ml_api_request(
                client_id,
                loja,
                cfg,
                "GET",
                f"https://api.mercadolibre.com/items/{item_id}",
                timeout=12,
            )
            if resp_item.status_code == 200:
                item = resp_item.json() or {}
        except Exception as exc:
            logger.warning("[ML PERGUNTAS] Falha ao buscar item %s para resposta manual: %s", item_id, exc)
    if not item:
        item = _ml_api_item_com_oauth_tenant(client_id, item_id) or _ml_api_item(item_id) or {}
    if not isinstance(item, dict):
        item = {}
    if item and not _ml_extrair_sku(item):
        item = _ml_perguntas_completar_skus_itens(client_id, loja, cfg, [item])[0]
    if not item:
        item = {
            "id": item_id,
            "title": pergunta.get("item_title") or "",
            "permalink": pergunta.get("item_permalink") or "",
            "thumbnail": pergunta.get("item_thumbnail") or "",
            "attributes": [
                {"id": "SELLER_SKU", "value_name": pergunta.get("item_sku") or ""}
            ] if pergunta.get("item_sku") else [],
        }

    try:
        resposta, cfg, contexto = _perguntas_ia_gerar_resposta(client_id, loja, cfg, pergunta, item)
    except PerguntasIARespostaIndisponivel as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "success": True,
        "loja": loja,
        "question_id": str(pergunta.get("id") or "").strip(),
        "resposta": resposta,
        "contexto": contexto,
    }


def ml_perguntas_responder_manual(req: PerguntasEnviarRespostaRequest, client_id: str = Depends(get_tenant_id)):
    loja = str(req.loja or "").strip()
    question_id = str(req.question_id or "").strip()
    resposta = str(req.resposta or "").strip()
    if not loja:
        raise HTTPException(status_code=400, detail="Informe a loja.")
    if not question_id:
        raise HTTPException(status_code=400, detail="Informe a pergunta.")
    if not resposta:
        raise HTTPException(status_code=400, detail="Informe a resposta.")

    cfg = _obter_cfg_ml(client_id, loja)
    resposta_ml, cfg = _perguntas_ia_enviar_resposta_ml(client_id, loja, cfg, question_id, resposta)
    resolvidas = _perguntas_ia_resolver_aprovacoes_pendentes(
        client_id,
        loja,
        question_id=question_id,
        status="sent_manual",
        motivo="pergunta_respondida_manualmente",
        resposta=resposta,
    )
    try:
        if resolvidas:
            for approval in resolvidas:
                registrado = _perguntas_ia_memoria_registrar_resposta_aprovada(
                    client_id,
                    loja,
                    resposta,
                    approval=approval,
                    origem="manual_com_aprovacao",
                    question_id=question_id,
                )
                if not registrado:
                    _perguntas_ia_memoria_registrar_resposta_aprovada(
                        client_id,
                        loja,
                        resposta,
                        pergunta=req.pergunta if isinstance(req.pergunta, dict) else {},
                        origem="manual_com_aprovacao",
                        sku=req.sku,
                        item_id=req.item_id,
                        question_id=question_id,
                    )
        else:
            _perguntas_ia_memoria_registrar_resposta_aprovada(
                client_id,
                loja,
                resposta,
                pergunta=req.pergunta if isinstance(req.pergunta, dict) else {},
                origem="manual",
                sku=req.sku,
                item_id=req.item_id,
                question_id=question_id,
            )
    except Exception as exc:
        logger.warning("[ML PERGUNTAS IA] Falha ao registrar resposta manual na memoria do SKU: %s", exc)
    state = _perguntas_ia_state_carregar(client_id)
    _perguntas_ia_marcar_processada(state, loja, question_id, "sent_manual")
    _perguntas_ia_state_salvar(client_id, state)
    return {
        "success": True,
        "loja": loja,
        "question_id": question_id,
        "resposta": resposta,
        "mercadolivre": resposta_ml,
    }


def ml_ia_treinamento_obter(loja: Optional[str] = None, client_id: str = Depends(get_tenant_id)):
    data = _ia_treinamento_ppv_resolver(client_id, loja)
    return {"success": True, **data}


def ml_ia_treinamento_salvar(req: IATreinamentoPerguntasPosVendaRequest, client_id: str = Depends(get_tenant_id)):
    data = _ia_treinamento_ppv_salvar(
        client_id,
        req.orientacoes,
        req.tipo,
        loja=req.loja,
        contexto_loja=req.contexto_loja,
        compatibilidade_autopecas=req.compatibilidade_autopecas,
        proibicoes=req.proibicoes,
        sku=req.sku,
        notas_sku=req.notas_sku,
        exemplos=req.exemplos,
    )
    return {"success": True, **data}


def ml_ia_treinamento_listar_skus(client_id: str = Depends(get_tenant_id)):
    return {"success": True, "produtos": _ia_treinamento_ppv_listar_skus(client_id)}


def ml_ia_treinamento_simular(req: IATreinamentoPerguntasPosVendaSimularRequest, client_id: str = Depends(get_tenant_id)):
    pergunta = str(req.pergunta or "").strip()
    if not pergunta:
        raise HTTPException(status_code=400, detail="Informe uma pergunta para simular.")

    tipo_treinamento = _ia_treinamento_ppv_tipo_normalizar(req.tipo)
    contexto_tipo = "pos-venda" if tipo_treinamento == "pos_venda" else "pergunta de anuncio"
    contexto_extra = str(req.contexto or "").strip()
    loja = str(req.loja or "").strip()
    mensagem = (
        f"Simule um rascunho via IA de {contexto_tipo} para enviar a um comprador do Mercado Livre. "
        f"Use as orientacoes salvas no treinamento de {_ia_treinamento_ppv_tipo_label(tipo_treinamento)}. "
        "A resposta deve ser cordial, objetiva e comercial, sem inventar dados tecnicos, prazo, estoque, garantia ou compatibilidade. "
        "Nunca se apresente como IA, assistente, Gemini, Vertex ou JK Sistema. "
        "Responda como a equipe da loja, sem mencionar sistema interno, app, prompt, JSON, modelo ou treinamento. "
        f"Finalize exatamente com: {_perguntas_ia_assinatura_loja(loja)} "
        f"Se faltar informacao essencial, peÃ§a a informacao de forma educada. "
        f"Mantenha a resposta com no maximo {ML_RESPOSTA_PERGUNTA_LIMITE_SEGURO} caracteres para evitar falha no Mercado Livre.\n\n"
        f"Pergunta do comprador:\n{pergunta}"
    )
    sku_selecionado = _normalizar_sku_mes(str(req.sku or "").strip())
    produto_sku = _ia_treinamento_ppv_produto_por_sku(client_id, sku_selecionado) if sku_selecionado else {}
    produto_prompt = _ia_treinamento_ppv_produto_prompt(produto_sku)
    if produto_prompt:
        mensagem += produto_prompt
    if contexto_extra:
        mensagem += f"\n\nContexto adicional informado pelo usuario:\n{contexto_extra}"

    payload = IAChatRequest(
        message=mensagem,
        page="Perguntas e pÃ³s venda",
        context={
            "modulo": "perguntas_pos_venda",
            "tipo": "treinamento_ia",
            "tipo_treinamento": tipo_treinamento,
            "loja": loja,
            "sku": sku_selecionado,
            "produto": produto_sku,
        },
        model=req.model,
    )
    model_req = _normalizar_ia_modelo_padrao(str(req.model or "").strip() or _ia_modelo_perguntas_configurado())
    payload.model = model_req

    if _modelo_eh_codex(model_req):
        resposta = _chamar_codex_chat(payload, client_id)
        model_usado = f"codex:{_codex_modelo_nome_curto(model_req)}"
    elif _modelo_eh_vertex_ai(model_req):
        resposta = _chamar_vertex_ai_chat(payload, client_id)
        model_usado = f"vertex:{_vertex_modelo_nome_curto(model_req) or _vertex_ai_modelo_padrao()}"
    elif _modelo_eh_gemini_api(model_req):
        resposta = _chamar_gemini_chat(payload, client_id)
        model_usado = f"gemini:{_gemini_nome_curto(model_req) or 'gemini-2.5-flash'}"
    elif model_req.startswith("deepseek-"):
        resposta = _chamar_deepseek_chat(payload, client_id)
        model_usado = model_req
    else:
        resposta = _chamar_openai_responses(payload, client_id)
        model_usado = model_req or (os.getenv("OPENAI_MODEL") or "gpt-5.4-nano").strip()

    resposta_final = _perguntas_ia_resposta_final_loja(resposta, loja)
    if not resposta_final:
        raise HTTPException(status_code=502, detail="IA nao gerou resposta para a simulacao.")
    return {"success": True, "model": model_usado, "resposta": resposta_final}


def ml_pos_venda_listar_conversas(
    loja: str,
    dias: int = 365,
    offset: int = 0,
    limit: int = 20,
    max_orders: int = 10000,
    busca: Optional[str] = None,
    nao_lidas: bool = False,
    client_id: str = Depends(get_tenant_id),
):
    try:
        nome_loja = str(loja or "").strip()
        if not nome_loja:
            raise HTTPException(status_code=400, detail="Informe a loja para buscar o pÃƒÂ³s venda.")

        cfg = _obter_cfg_ml(client_id, nome_loja)
        seller_id = str(cfg.get("user_id") or "").strip()
        if not seller_id:
            raise HTTPException(status_code=400, detail="ID do usuÃƒÂ¡rio do Mercado Livre nÃ£o encontrado para esta loja.")

        dias = max(1, min(int(dias or 365), 365))
        offset_inicial = max(0, int(offset or 0))
        limit = max(1, min(int(limit or 20), 20))
        max_orders = max(limit, min(int(max_orders or 10000), 10000))
        busca_texto = str(busca or "").strip()
        busca_ativa = bool(_ml_pos_venda_normalizar_termo_busca(busca_texto))
        filtro_nao_lidas = bool(nao_lidas)
        agora = dt.datetime.now()
        data_inicio = agora - dt.timedelta(days=dias)
        orders_url = "https://api.mercadolibre.com/orders/search"
        offset = offset_inicial
        page_limit = limit
        total = 0
        orders_avaliadas = 0
        conversas_raw = []
        packs_avaliados = set()
        erros = []

        def consultar_mensagens(order: dict):
            order_id = str((order or {}).get("id") or "").strip()
            pack_id = str((order or {}).get("pack_id") or order_id).strip()
            if not pack_id:
                return None
            url = f"https://api.mercadolibre.com/messages/packs/{pack_id}/sellers/{seller_id}"
            try:
                resp_msg, _cfg_msg = _ml_api_request(
                    client_id,
                    nome_loja,
                    dict(cfg),
                    "GET",
                    url,
                    params={"tag": "post_sale", "mark_as_read": "false", "limit": 10, "offset": 0},
                    timeout=18,
                )
                if resp_msg.status_code in {403, 404}:
                    return None
                if resp_msg.status_code != 200:
                    erros.append({"order_id": order_id, "status": resp_msg.status_code})
                    return None
                mensagens_data = resp_msg.json() or {}
                mensagens = mensagens_data.get("messages") if isinstance(mensagens_data.get("messages"), list) else []
                if not mensagens:
                    paging = mensagens_data.get("paging") if isinstance(mensagens_data.get("paging"), dict) else {}
                    if int(paging.get("total") or 0) <= 0:
                        return None
                if filtro_nao_lidas and not _ml_pos_venda_conversa_nao_lida(mensagens_data, seller_id):
                    return None
                return {"order": order, "mensagens_data": mensagens_data}
            except Exception as exc:
                erros.append({"order_id": order_id, "erro": str(exc)})
                return None

        limite_coleta = max_orders if busca_ativa else limit

        while len(conversas_raw) < limite_coleta and orders_avaliadas < max_orders:
            pedidos_restantes = max_orders - orders_avaliadas
            params = {
                "seller": seller_id,
                "order.date_created.from": _ml_pos_venda_data_iso(data_inicio),
                "order.date_created.to": _ml_pos_venda_data_iso(agora),
                "sort": "date_desc",
                "offset": offset,
                "limit": min(page_limit, pedidos_restantes),
            }
            resp, cfg = _ml_api_request(client_id, nome_loja, cfg, "GET", orders_url, params=params, timeout=25)
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=_ml_parse_error_detail(resp, "Erro ao buscar vendas do Mercado Livre"))
            data = resp.json() or {}
            lote = data.get("results") or []
            if not isinstance(lote, list) or not lote:
                break
            orders = [order for order in lote if isinstance(order, dict)]
            orders_avaliadas += len(orders)
            paging = data.get("paging") or {}
            total = int(paging.get("total") or total or 0)
            offset += len(lote)

            orders_para_consultar = []
            for order in orders:
                order_id = str((order or {}).get("id") or "").strip()
                pack_id = str((order or {}).get("pack_id") or order_id).strip()
                if pack_id and pack_id in packs_avaliados:
                    continue
                if pack_id:
                    packs_avaliados.add(pack_id)
                orders_para_consultar.append(order)

            if not orders_para_consultar:
                if offset >= total:
                    break
                continue

            max_workers = min(8, max(1, len(orders_para_consultar)))
            if max_workers <= 1:
                for order in orders_para_consultar:
                    conversa = consultar_mensagens(order)
                    if conversa:
                        conversas_raw.append(conversa)
                        if len(conversas_raw) >= limite_coleta:
                            break
            else:
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futuros = [executor.submit(consultar_mensagens, order) for order in orders_para_consultar]
                    for futuro in as_completed(futuros):
                        if len(conversas_raw) >= limite_coleta:
                            continue
                        conversa = futuro.result()
                        if conversa:
                            conversas_raw.append(conversa)
            if offset >= total:
                break

        item_ids = []
        item_ids_vistos = set()
        for conversa in conversas_raw:
            order = conversa.get("order") if isinstance(conversa, dict) else {}
            for entry in (order or {}).get("order_items") or []:
                item = entry.get("item") if isinstance(entry, dict) and isinstance(entry.get("item"), dict) else {}
                item_id = str(item.get("id") or item.get("item_id") or entry.get("item_id") or "").strip() if isinstance(entry, dict) else ""
                if item_id and item_id not in item_ids_vistos:
                    item_ids_vistos.add(item_id)
                    item_ids.append(item_id)

        itens, cfg = _ml_buscar_itens_batch(client_id, nome_loja, cfg, item_ids)
        item_por_id = {str(item.get("id") or "").strip(): item for item in itens if isinstance(item, dict)}
        conversas = [
            _ml_pos_venda_normalizar_pedido(conversa.get("order") or {}, conversa.get("mensagens_data") or {}, seller_id, item_por_id)
            for conversa in conversas_raw
            if isinstance(conversa, dict)
        ]
        if busca_ativa:
            conversas = [
                conversa
                for conversa in conversas
                if _ml_pos_venda_conversa_corresponde_busca(conversa, busca_texto)
            ]
        if filtro_nao_lidas:
            conversas = [conversa for conversa in conversas if bool(conversa.get("nao_lida") or conversa.get("unread"))]

        conversas.sort(key=lambda item: item.get("last_message_date") or item.get("date_created") or "", reverse=True)
        conversas_total = len(conversas)
        if busca_ativa:
            conversas = conversas[:limit]
        next_offset = None if busca_ativa else (offset if offset < total else None)

        return jsonable_encoder({
            "success": True,
            "loja": nome_loja,
            "seller_id": seller_id,
            "dias": dias,
            "offset": offset_inicial,
            "limit": limit,
            "busca": busca_texto,
            "nao_lidas": filtro_nao_lidas,
            "next_offset": next_offset,
            "has_next": bool(next_offset is not None),
            "orders_total": total,
            "orders_avaliadas": orders_avaliadas,
            "conversas_total": conversas_total,
            "conversas_nao_lidas_total": sum(1 for conversa in conversas if conversa.get("nao_lida") or conversa.get("unread")),
            "interrompido": offset < total,
            "erros": erros[:10],
            "conversas": conversas,
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[ML POS VENDA] Falha inesperada ao listar conversas da loja {loja}: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao listar conversas do pÃƒÂ³s venda: {str(e)}")


def ml_pos_venda_listar_mediacoes(
    loja: str,
    dias: int = 365,
    limit: int = 20,
    max_claims: int = 300,
    busca: Optional[str] = None,
    client_id: str = Depends(get_tenant_id),
):
    try:
        nome_loja = str(loja or "").strip()
        if not nome_loja:
            raise HTTPException(status_code=400, detail="Informe a loja para buscar mediaÃ§Ãµes.")

        cfg = _obter_cfg_ml(client_id, nome_loja)
        seller_id = str(cfg.get("user_id") or "").strip()
        if not seller_id:
            raise HTTPException(status_code=400, detail="ID do usuÃ¡rio do Mercado Livre nÃ£o encontrado para esta loja.")

        dias = max(1, min(int(dias or 365), 365))
        limit = max(1, min(int(limit or 20), 50))
        max_claims = max(limit, min(int(max_claims or 300), 1000))
        busca_texto = str(busca or "").strip()
        busca_ativa = bool(_ml_pos_venda_normalizar_termo_busca(busca_texto))
        agora = dt.datetime.now()
        data_inicio = agora - dt.timedelta(days=dias)
        claims_raw = []
        claims_vistos = set()
        total = 0
        interrompido = False
        consultas_claims = [
            {
                "tipo": "mediacao",
                "label": "MediaÃ§Ã£o",
                "obrigatoria": True,
                "params": {"type": "mediations", "stage": "dispute", "status": "opened"},
            },
            {
                "tipo": "devolucao",
                "label": "DevoluÃ§Ã£o",
                "obrigatoria": False,
                "params": {"type": "return", "status": "opened"},
            },
        ]

        for consulta in consultas_claims:
            offset = 0
            total_consulta = 0
            coletados_consulta = 0
            while coletados_consulta < max_claims:
                params = {
                    **consulta["params"],
                    "sort": "last_updated:desc",
                    "offset": offset,
                    "limit": min(50, max_claims - coletados_consulta),
                    "range": f"last_updated:after:{_ml_pos_venda_data_iso(data_inicio)},before:{_ml_pos_venda_data_iso(agora)}",
                }
                resp, cfg = _ml_api_request(
                    client_id,
                    nome_loja,
                    cfg,
                    "GET",
                    "https://api.mercadolibre.com/post-purchase/v1/claims/search",
                    params=params,
                    timeout=25,
                )
                if resp.status_code != 200:
                    detalhe = _ml_parse_error_detail(resp, f"Erro ao buscar {consulta['label'].lower()} do Mercado Livre")
                    if consulta["obrigatoria"]:
                        raise HTTPException(status_code=resp.status_code, detail=detalhe)
                    logger.warning("[ML MEDIACAO] Falha ao buscar %s loja=%s: %s", consulta["label"].lower(), nome_loja, detalhe)
                    break
                data = resp.json() or {}
                lote = data.get("data") or data.get("results") or []
                if not isinstance(lote, list) or not lote:
                    break
                paging = data.get("paging") if isinstance(data.get("paging"), dict) else {}
                total_consulta = int(paging.get("total") or total_consulta or len(lote))
                total += total_consulta if offset == 0 else 0
                for claim in lote:
                    if not isinstance(claim, dict):
                        continue
                    claim_id = str(claim.get("id") or "").strip()
                    chave_claim = claim_id or f"{consulta['tipo']}:{_ml_mediacao_order_id(claim)}:{claim.get('reason_id') or ''}"
                    if chave_claim and chave_claim in claims_vistos:
                        continue
                    if chave_claim:
                        claims_vistos.add(chave_claim)
                    claim = dict(claim)
                    claim["_jk_claim_tipo"] = consulta["tipo"]
                    claim["_jk_claim_tipo_label"] = consulta["label"]
                    claims_raw.append(claim)
                offset += len(lote)
                coletados_consulta += len(lote)
                if offset >= total_consulta:
                    break
            if offset < total_consulta:
                interrompido = True

        reason_ids = [str(claim.get("reason_id") or "").strip() for claim in claims_raw if isinstance(claim, dict)]
        motivos_claims, cfg = _ml_mediacao_buscar_motivos_claims(client_id, nome_loja, cfg, reason_ids)

        order_ids = []
        vistos = set()
        for claim in claims_raw:
            order_id = _ml_mediacao_order_id(claim)
            if order_id and order_id not in vistos:
                vistos.add(order_id)
                order_ids.append(order_id)

        orders_por_id = {}
        for order_id in order_ids:
            try:
                order, cfg = _ml_pos_venda_buscar_pedido(client_id, nome_loja, cfg, order_id)
                if order:
                    orders_por_id[str(order.get("id") or order_id)] = order
            except Exception as exc:
                logger.warning("[ML MEDIACAO] Falha ao buscar pedido %s loja=%s: %s", order_id, nome_loja, exc)

        item_ids = []
        item_ids_vistos = set()
        for order in orders_por_id.values():
            for item_id in _ml_pos_venda_item_ids_pedido(order):
                if item_id and item_id not in item_ids_vistos:
                    item_ids_vistos.add(item_id)
                    item_ids.append(item_id)

        itens, cfg = _ml_buscar_itens_batch(client_id, nome_loja, cfg, item_ids)
        item_por_id = {str(item.get("id") or "").strip(): item for item in itens if isinstance(item, dict)}
        mediacoes = []
        for claim in claims_raw:
            order_id = _ml_mediacao_order_id(claim)
            reason_id = str(claim.get("reason_id") or "").strip()
            if reason_id and motivos_claims.get(reason_id):
                claim = dict(claim)
                claim["reason"] = motivos_claims[reason_id]
            venda = _ml_mediacao_normalizar_claim(claim, orders_por_id.get(order_id) or {"id": order_id}, seller_id, item_por_id)
            if busca_ativa and not _ml_pos_venda_pedido_corresponde_busca(venda, busca_texto):
                continue
            mediacoes.append(venda)

        mediacoes.sort(key=lambda item: item.get("claim_last_updated") or item.get("claim_date_created") or "", reverse=True)
        total_filtrado = len(mediacoes)
        total_mediacoes = sum(1 for item in mediacoes if str(item.get("claim_kind") or item.get("claim_type") or "").strip().lower() != "devolucao")
        total_devolucoes = sum(1 for item in mediacoes if str(item.get("claim_kind") or item.get("claim_type") or "").strip().lower() in {"devolucao", "return", "returns"})
        mediacoes = mediacoes[:limit]

        return jsonable_encoder({
            "success": True,
            "loja": nome_loja,
            "seller_id": seller_id,
            "dias": dias,
            "limit": limit,
            "busca": busca_texto,
            "orders_total": total,
            "orders_avaliadas": len(order_ids),
            "conversas_total": total_filtrado,
            "mediacoes_total": total_mediacoes,
            "devolucoes_total": total_devolucoes,
            "interrompido": interrompido,
            "conversas": mediacoes,
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[ML MEDIACAO] Falha inesperada ao listar mediaÃ§Ãµes da loja {loja}: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao listar mediaÃ§Ãµes: {str(e)}")


def ml_pos_venda_detalhe_conversa(
    loja: str,
    pack_id: str,
    order_id: Optional[str] = None,
    client_id: str = Depends(get_tenant_id),
):
    nome_loja = str(loja or "").strip()
    pack = str(pack_id or "").strip()
    if not nome_loja:
        raise HTTPException(status_code=400, detail="Informe a loja.")
    if not pack:
        raise HTTPException(status_code=400, detail="Informe a conversa do pos venda.")
    cfg = _obter_cfg_ml(client_id, nome_loja)
    conversa, cfg = _ml_pos_venda_montar_conversa_normalizada(
        client_id,
        nome_loja,
        cfg,
        pack,
        str(order_id or "").strip(),
    )
    return jsonable_encoder({
        "success": True,
        "loja": nome_loja,
        "conversa": conversa,
        "mensagens": conversa.get("messages") or [],
        "seller_max_message_length": conversa.get("seller_max_message_length") or ML_POS_VENDA_DEFAULT_MAX_CHARS,
    })


def ml_pos_venda_obter_anexo(
    attachment_id: str,
    loja: str,
    client_id: str = Depends(get_tenant_id),
):
    nome_loja = str(loja or "").strip()
    anexo_id = str(attachment_id or "").strip()
    if not nome_loja:
        raise HTTPException(status_code=400, detail="Informe a loja.")
    if not anexo_id:
        raise HTTPException(status_code=400, detail="Informe o anexo.")
    cfg = _obter_cfg_ml(client_id, nome_loja)
    resp, cfg = _ml_api_request(
        client_id,
        nome_loja,
        cfg,
        "GET",
        f"https://api.mercadolibre.com/messages/attachments/{quote_plus(anexo_id)}",
        params={"tag": "post_sale"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=_ml_parse_error_detail(resp, "Erro ao carregar anexo do pÃ³s venda"))
    media_type = str(resp.headers.get("content-type") or "application/octet-stream").split(";")[0].strip()
    headers = {"Cache-Control": "private, max-age=300"}
    return StreamingResponse(io.BytesIO(resp.content), media_type=media_type, headers=headers)


def ml_pos_venda_gerar_resposta_conversa(req: PosVendaGerarRespostaRequest, client_id: str = Depends(get_tenant_id)):
    nome_loja = str(req.loja or "").strip()
    pack = str(req.pack_id or "").strip()
    if not nome_loja:
        raise HTTPException(status_code=400, detail="Informe a loja.")
    if not pack:
        raise HTTPException(status_code=400, detail="Informe a conversa do pos venda.")

    cfg = _obter_cfg_ml(client_id, nome_loja)
    max_chars = int(req.max_chars or ML_POS_VENDA_DEFAULT_MAX_CHARS)
    conversa, cfg = _ml_pos_venda_montar_conversa_normalizada(
        client_id,
        nome_loja,
        cfg,
        pack,
        str(req.order_id or "").strip(),
    )
    if req.buyer_id and not conversa.get("buyer_id"):
        conversa["buyer_id"] = str(req.buyer_id or "").strip()
    if str(req.resposta_atual or "").strip():
        conversa["_resposta_atual"] = str(req.resposta_atual or "").strip()[:1200]
    if str(req.orientacao_usuario or "").strip():
        conversa["_orientacao_usuario"] = str(req.orientacao_usuario or "").strip()[:1200]
    conversa, cfg = _ml_pos_venda_preparar_conversa_ia(client_id, nome_loja, cfg, conversa)
    resultado_ia, cfg = _ml_pos_venda_executar_pipeline_ia(client_id, nome_loja, cfg, conversa, max_chars)
    return jsonable_encoder({
        "success": True,
        "loja": nome_loja,
        "pack_id": pack,
        "resposta": resultado_ia.get("resposta") or "",
        "model": resultado_ia.get("model") or "",
        "pode_enviar_automaticamente": bool(resultado_ia.get("pode_enviar_automaticamente")),
        "decisao": resultado_ia.get("decisao") or {},
        "validacao": resultado_ia.get("validacao") or {},
        "ia_pipeline": _ml_pos_venda_pipeline_resumo(resultado_ia.get("contexto_ia")),
        "audit_id": resultado_ia.get("audit_id") or "",
        "seller_max_message_length": conversa.get("seller_max_message_length") or ML_POS_VENDA_DEFAULT_MAX_CHARS,
    })


def ml_pos_venda_responder_conversa(req: PosVendaMensagemRequest, client_id: str = Depends(get_tenant_id)):
    nome_loja = str(req.loja or "").strip()
    pack = str(req.pack_id or "").strip()
    buyer = str(req.buyer_id or "").strip()
    if not nome_loja:
        raise HTTPException(status_code=400, detail="Informe a loja.")
    if not pack:
        raise HTTPException(status_code=400, detail="Informe a conversa do pos venda.")
    cfg = _obter_cfg_ml(client_id, nome_loja)
    max_chars = int(req.max_chars or ML_POS_VENDA_DEFAULT_MAX_CHARS)
    resposta = _pos_venda_ia_limpar_resposta(req.texto, max_chars)
    conversa_memoria = req.conversa if isinstance(req.conversa, dict) else {}
    if conversa_memoria:
        conversa_memoria.setdefault("pack_id", pack)
        conversa_memoria.setdefault("order_id", str(req.order_id or "").strip())
        if buyer:
            conversa_memoria.setdefault("buyer_id", buyer)
    if not buyer:
        try:
            conversa_memoria, cfg = _ml_pos_venda_montar_conversa_normalizada(
                client_id,
                nome_loja,
                cfg,
                pack,
                str(req.order_id or "").strip(),
            )
            buyer = str(conversa_memoria.get("buyer_id") or "").strip()
        except Exception as exc:
            logger.warning("[ML POS VENDA] Nao foi possivel resolver buyer_id antes do envio pack=%s loja=%s: %s", pack, nome_loja, exc)
    resposta_ml, cfg = _ml_pos_venda_enviar_resposta_ml(client_id, nome_loja, cfg, pack, buyer, resposta, max_chars)
    if not conversa_memoria:
        try:
            conversa_memoria, cfg = _ml_pos_venda_montar_conversa_normalizada(
                client_id,
                nome_loja,
                cfg,
                pack,
                str(req.order_id or "").strip(),
            )
            if buyer and isinstance(conversa_memoria, dict):
                conversa_memoria.setdefault("buyer_id", buyer)
        except Exception as exc:
            logger.warning("[ML POS VENDA] Nao foi possivel carregar conversa para memoria pack=%s loja=%s: %s", pack, nome_loja, exc)
    if conversa_memoria:
        try:
            conversa_memoria, cfg = _ml_pos_venda_preparar_conversa_ia(client_id, nome_loja, cfg, conversa_memoria)
        except Exception as exc:
            logger.warning("[ML POS VENDA IA] Falha ao carregar perguntas do anuncio para memoria pack=%s loja=%s: %s", pack, nome_loja, exc)
    resolvidas = _perguntas_ia_resolver_aprovacoes_pendentes(
        client_id,
        nome_loja,
        pack_id=pack,
        status="sent_manual_pos_venda",
        motivo="pos_venda_respondido_manualmente",
        resposta=resposta,
    )
    try:
        if resolvidas:
            for approval in resolvidas:
                registrados = _ml_pos_venda_memoria_registrar_resposta_enviada(
                    client_id,
                    nome_loja,
                    conversa_memoria,
                    resposta,
                    origem="manual_pos_venda_com_aprovacao",
                    approval=approval,
                )
                if not registrados:
                    _perguntas_ia_memoria_registrar_resposta_aprovada(
                        client_id,
                        nome_loja,
                        resposta,
                        approval=approval,
                        origem="manual_pos_venda_com_aprovacao",
                    )
        else:
            _ml_pos_venda_memoria_registrar_resposta_enviada(
                client_id,
                nome_loja,
                conversa_memoria,
                resposta,
                origem="manual_pos_venda",
            )
    except Exception as exc:
        logger.warning("[ML POS VENDA IA] Falha ao registrar resposta enviada na memoria do SKU: %s", exc)
    if resolvidas:
        state = _perguntas_ia_state_carregar(client_id)
        for approval in resolvidas:
            _perguntas_ia_marcar_processada(state, nome_loja, str(approval.get("question_id") or ""), "sent_manual_pos_venda")
        _perguntas_ia_state_salvar(client_id, state)
    return jsonable_encoder({
        "success": True,
        "loja": nome_loja,
        "pack_id": pack,
        "resposta": resposta,
        "mercadolivre": resposta_ml,
    })


def ml_pos_venda_automacao_poll(
    loja: Optional[str] = None,
    max_per_store: int = 2,
    client_id: str = Depends(get_tenant_id),
):
    state = _perguntas_ia_state_carregar(client_id)
    aprovacoes = _perguntas_ia_aprovacoes_carregar(client_id)
    configs_lojas = _perguntas_loja_configs_carregar(client_id)
    loja_filtro = str(loja or "").strip()
    max_per_store = max(1, min(int(max_per_store or 2), 5))
    novas_pendentes = []
    enviadas = []
    erros = []
    mudou_aprovacoes = False
    mudou_state = False

    for loja_cfg in carregar_lojas(client_id) or []:
        if not isinstance(loja_cfg, dict):
            continue
        nome_loja = str(loja_cfg.get("nome") or "").strip()
        if not nome_loja or (loja_filtro and nome_loja != loja_filtro):
            continue
        config = _perguntas_loja_config_normalizar(configs_lojas.get(nome_loja))
        if not config.get("responder_automaticamente") or not config.get("habilitar_pos_venda_automatico"):
            continue

        integracoes = loja_cfg.get("integracoes") or {}
        ml_cfg = integracoes.get("mercadolivre") if isinstance(integracoes, dict) else {}
        if not _ml_oauth_status(ml_cfg).get("conectado"):
            continue

        try:
            cfg = _obter_cfg_ml(client_id, nome_loja)
            seller_id = str(cfg.get("user_id") or "").strip()
            if not seller_id:
                continue
            data = ml_pos_venda_listar_conversas(
                loja=nome_loja,
                dias=30,
                offset=0,
                limit=20,
                max_orders=250,
                client_id=client_id,
            )
            conversas_base = data.get("conversas") if isinstance(data, dict) else []
            processadas_loja = 0
            for base in conversas_base or []:
                if processadas_loja >= max_per_store:
                    break
                if not isinstance(base, dict):
                    continue
                pack_id = str(base.get("pack_id") or "").strip()
                order_id = str(base.get("order_id") or "").strip()
                last_message_id = str(base.get("last_message_id") or "").strip()
                last_message_date = str(base.get("last_message_date") or "").strip()
                last_message_from = str(base.get("last_message_from") or "").strip()
                if not pack_id or last_message_from == seller_id:
                    continue
                if not str(base.get("last_message_text") or "").strip():
                    continue
                chave = f"pos_venda:{pack_id}:{last_message_id or last_message_date}"
                if _perguntas_ia_ja_processada(state, nome_loja, chave):
                    continue
                if _perguntas_ia_aprovacao_pendente(aprovacoes, nome_loja, chave):
                    continue

                conversa, cfg = _ml_pos_venda_montar_conversa_normalizada(client_id, nome_loja, cfg, pack_id, order_id)
                mensagens = conversa.get("messages") if isinstance(conversa.get("messages"), list) else []
                ultima = next((msg for msg in reversed(mensagens) if msg.get("from_role") != "seller"), None)
                if not ultima:
                    continue
                last_text = str(ultima.get("text") or conversa.get("last_message_text") or "").strip()
                if not last_text:
                    continue
                buyer_id = str(conversa.get("buyer_id") or "").strip()
                max_chars = int(conversa.get("seller_max_message_length") or ML_POS_VENDA_DEFAULT_MAX_CHARS)
                conversa, cfg = _ml_pos_venda_preparar_conversa_ia(client_id, nome_loja, cfg, conversa)
                resultado_ia, cfg = _ml_pos_venda_executar_pipeline_ia(client_id, nome_loja, cfg, conversa, max_chars)
                resposta = str(resultado_ia.get("resposta") or "").strip()
                model_usado = str(resultado_ia.get("model") or "").strip()
                contexto_ia = resultado_ia.get("contexto_ia") if isinstance(resultado_ia.get("contexto_ia"), dict) else {}
                if not resposta:
                    continue
                item = (conversa.get("items") or [{}])[0] if isinstance(conversa.get("items"), list) else {}

                if config.get("solicitar_aprovacao") or _pos_venda_ia_v2_exigir_aprovacao() or not resultado_ia.get("pode_enviar_automaticamente"):
                    conversa_aprovacao = {
                        "pack_id": conversa.get("pack_id") or pack_id,
                        "order_id": conversa.get("order_id") or order_id,
                        "buyer_id": conversa.get("buyer_id") or buyer_id,
                        "buyer_nickname": conversa.get("buyer_nickname") or "",
                        "items": conversa.get("items") or [],
                        "messages": conversa.get("messages") or [],
                        "last_message_text": conversa.get("last_message_text") or last_text,
                        "seller_max_message_length": max_chars,
                        "buyer_listing_question_history": conversa.get("buyer_listing_question_history") or [],
                        "buyer_listing_question_chat": conversa.get("buyer_listing_question_chat") or [],
                        "buyer_listing_question_history_count": conversa.get("buyer_listing_question_history_count") or 0,
                    }
                    pipeline_resumo = _ml_pos_venda_pipeline_resumo(contexto_ia)
                    approval = {
                        "id": _pos_venda_ia_aprovacao_id(nome_loja, pack_id, str(ultima.get("id") or last_message_id or last_message_date)),
                        "tipo": "pos_venda",
                        "approval_type": "pos_venda",
                        "status": "pending",
                        "loja": nome_loja,
                        "question_id": chave,
                        "pack_id": pack_id,
                        "order_id": order_id,
                        "buyer_id": buyer_id,
                        "item_id": item.get("id") or "",
                        "sku": item.get("sku") or "",
                        "titulo": item.get("title") or conversa.get("item_title") or "",
                        "permalink": item.get("permalink") or item.get("link") or item.get("url") or "",
                        "pergunta": last_text,
                        "conversa": conversa_aprovacao,
                        "mensagens": conversa_aprovacao["messages"],
                        "resposta_sugerida": resposta,
                        "max_chars": max_chars,
                        "model": model_usado,
                        "ia_origem": "mercado_livre_pos_venda",
                        "ia_finalidade": "pos_venda",
                        "ia_modo": _ia_modo_pos_venda_configurado(),
                        "aprovacao_obrigatoria_ia": _pos_venda_ia_v2_exigir_aprovacao(),
                        "ia_pipeline": pipeline_resumo,
                        "ia_decisao": resultado_ia.get("decisao") or {},
                        "ia_validacao": resultado_ia.get("validacao") or {},
                        "audit_id": resultado_ia.get("audit_id") or "",
                        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
                    }
                    aprovacoes.append(approval)
                    novas_pendentes.append(approval)
                    mudou_aprovacoes = True
                    processadas_loja += 1
                    try:
                        _ml_pos_venda_auditoria_registrar(client_id, {
                            "evento": "pos_venda_ia_enviada_para_humano",
                            "loja": nome_loja,
                            "pack_id": pack_id,
                            "order_id": order_id,
                            "buyer_id": buyer_id,
                            "approval_id": approval.get("id") or "",
                            "audit_pipeline_id": resultado_ia.get("audit_id") or "",
                            "decisao": resultado_ia.get("decisao") or {},
                            "validacao": resultado_ia.get("validacao") or {},
                        })
                    except Exception as exc:
                        logger.warning("[ML POS VENDA IA] Falha ao auditar envio para humano: %s", exc)
                else:
                    resposta_ml, cfg = _ml_pos_venda_enviar_resposta_ml(client_id, nome_loja, cfg, pack_id, buyer_id, resposta, max_chars)
                    try:
                        _ml_pos_venda_memoria_registrar_resposta_enviada(
                            client_id,
                            nome_loja,
                            conversa,
                            resposta,
                            origem="auto_pos_venda",
                        )
                    except Exception as exc:
                        logger.warning("[ML POS VENDA IA] Falha ao registrar resposta automatica na memoria do SKU: %s", exc)
                    _perguntas_ia_marcar_processada(state, nome_loja, chave, "sent_auto_pos_venda")
                    mudou_state = True
                    processadas_loja += 1
                    try:
                        _ml_pos_venda_auditoria_registrar(client_id, {
                            "evento": "pos_venda_ia_enviada_auto",
                            "loja": nome_loja,
                            "pack_id": pack_id,
                            "order_id": order_id,
                            "buyer_id": buyer_id,
                            "audit_pipeline_id": resultado_ia.get("audit_id") or "",
                            "decisao": resultado_ia.get("decisao") or {},
                            "validacao": resultado_ia.get("validacao") or {},
                            "mercadolivre": resposta_ml,
                        })
                    except Exception as exc:
                        logger.warning("[ML POS VENDA IA] Falha ao auditar envio automatico: %s", exc)
                    enviadas.append({
                        "tipo": "pos_venda",
                        "loja": nome_loja,
                        "pack_id": pack_id,
                        "order_id": order_id,
                        "buyer_id": buyer_id,
                        "sku": item.get("sku") or "",
                        "titulo": item.get("title") or conversa.get("item_title") or "",
                        "resposta": resposta,
                        "mercadolivre": resposta_ml,
                        "audit_id": resultado_ia.get("audit_id") or "",
                    })
        except HTTPException as exc:
            erros.append({"loja": nome_loja, "erro": exc.detail})
        except Exception as exc:
            logger.exception("[ML POS VENDA IA] Falha na automacao da loja %s: %s", nome_loja, exc)
            erros.append({"loja": nome_loja, "erro": str(exc)})

    if mudou_aprovacoes:
        _perguntas_ia_aprovacoes_salvar(client_id, aprovacoes)
    if mudou_state:
        _perguntas_ia_state_salvar(client_id, state)
    pendentes = [a for a in aprovacoes if isinstance(a, dict) and str(a.get("status") or "pending") == "pending"]
    return jsonable_encoder({
        "success": True,
        "novas_pendentes": novas_pendentes,
        "pendentes": pendentes,
        "enviadas": enviadas,
        "erros": erros,
    })


def ml_listar_perguntas(
    loja: str,
    status: Optional[str] = None,
    offset: int = 0,
    limit: int = 50,
    carregar_todas: bool = True,
    max_pages: int = 100,
    client_id: str = Depends(get_tenant_id),
):
    try:
        nome_loja = str(loja or "").strip()
        if not nome_loja:
            raise HTTPException(status_code=400, detail="Informe a loja para buscar as perguntas.")

        cfg = _obter_cfg_ml(client_id, nome_loja)
        seller_id = str(cfg.get("user_id") or "").strip()
        if not seller_id:
            raise HTTPException(status_code=400, detail="ID do usuÃƒÂ¡rio do Mercado Livre nÃ£o encontrado para esta loja.")

        limit = max(1, min(int(limit or 20), 100))
        offset = max(0, int(offset or 0))
        max_pages = max(1, min(int(max_pages or 100), 100))
        status_filtro = str(status or "").strip().upper()
        if status_filtro in {"TODAS", "TODOS", "ALL"}:
            status_filtro = ""

        url = "https://api.mercadolibre.com/questions/search"
        perguntas: list[dict] = []
        total = 0
        filtros = {}
        paginas_lidas = 0
        proximo_offset = offset
        interrompido = False
        scroll_id = ""
        scroll_ids_lidos = set()
        perguntas_vistas = set()

        usar_scan = carregar_todas
        scan_falhou_invalid_params = False

        if usar_scan:
            while True:
                if scroll_id:
                    if scroll_id in scroll_ids_lidos:
                        interrompido = True
                        break
                    scroll_ids_lidos.add(scroll_id)
                    params = {
                        "search_type": "scan",
                        "scroll_id": scroll_id,
                        "limit": limit,
                    }
                else:
                    params = {
                        "seller_id": seller_id,
                        "api_version": 4,
                        "search_type": "scan",
                        "limit": limit,
                    }

                resp, cfg = _ml_api_request(client_id, nome_loja, cfg, "GET", url, params=params, timeout=25)
                if resp.status_code != 200:
                    detail = _ml_parse_error_detail(resp, "Erro ao buscar perguntas do Mercado Livre")
                    if resp.status_code == 400 and "invalid client parameter" in str(detail).lower():
                        scan_falhou_invalid_params = True
                        perguntas = []
                        perguntas_vistas = set()
                        total = 0
                        filtros = {}
                        paginas_lidas = 0
                        proximo_offset = offset
                        interrompido = False
                        usar_scan = False
                        break
                    raise HTTPException(status_code=resp.status_code, detail=detail)

                data = resp.json() or {}
                lote = data.get("questions") or data.get("results") or []
                if not isinstance(lote, list):
                    lote = []
                for pergunta in lote:
                    if not isinstance(pergunta, dict):
                        continue
                    pergunta_id = str(pergunta.get("id") or "").strip()
                    chave_pergunta = pergunta_id or json.dumps(pergunta, sort_keys=True, default=str)
                    if chave_pergunta in perguntas_vistas:
                        continue
                    perguntas_vistas.add(chave_pergunta)
                    perguntas.append(pergunta)

                try:
                    total = max(total, int(data.get("total") or data.get("paging", {}).get("total") or len(perguntas) or 0))
                except Exception:
                    total = max(total, len(perguntas))
                filtros = data.get("filters") or filtros
                paginas_lidas += 1
                scroll_id = str(data.get("scroll_id") or "").strip()

                if not lote or not scroll_id:
                    break
                if paginas_lidas >= max_pages:
                    interrompido = True
                    break
            if status_filtro:
                perguntas = [
                    pergunta for pergunta in perguntas
                    if str((pergunta or {}).get("status") or "").strip().upper() == status_filtro
                ]
                total = len(perguntas)

        if carregar_todas and not usar_scan:
            while True:
                if proximo_offset > 1000:
                    interrompido = True
                    break
                limite_chamada = min(limit, 1000 - proximo_offset)
                if limite_chamada <= 0:
                    interrompido = True
                    break

                params = {
                    "seller_id": seller_id,
                    "api_version": 4,
                    "offset": proximo_offset,
                    "limit": limite_chamada,
                    "sort_fields": "date_created",
                    "sort_types": "DESC",
                }
                if status_filtro:
                    params["status"] = status_filtro

                resp, cfg = _ml_api_request(client_id, nome_loja, cfg, "GET", url, params=params, timeout=20)
                if resp.status_code != 200:
                    raise HTTPException(status_code=resp.status_code, detail=_ml_parse_error_detail(resp, "Erro ao buscar perguntas do Mercado Livre"))

                data = resp.json() or {}
                lote = data.get("questions") or data.get("results") or []
                if not isinstance(lote, list):
                    lote = []
                for pergunta in lote:
                    if not isinstance(pergunta, dict):
                        continue
                    pergunta_id = str(pergunta.get("id") or "").strip()
                    chave_pergunta = pergunta_id or json.dumps(pergunta, sort_keys=True, default=str)
                    if chave_pergunta in perguntas_vistas:
                        continue
                    perguntas_vistas.add(chave_pergunta)
                    perguntas.append(pergunta)

                total = int(data.get("total") or data.get("paging", {}).get("total") or len(perguntas) or 0)
                filtros = data.get("filters") or filtros
                paginas_lidas += 1
                proximo_offset += limite_chamada

                if not lote or proximo_offset >= total:
                    break
                if proximo_offset >= 1000:
                    interrompido = proximo_offset < total
                    break
                if paginas_lidas >= max_pages:
                    interrompido = True
                    break
            if scan_falhou_invalid_params:
                logger.warning("[ML PERGUNTAS] Scan indisponivel para loja=%s. Usando fallback por offset.", nome_loja)
        elif not carregar_todas:
            if offset > 1000:
                raise HTTPException(status_code=400, detail="O Mercado Livre permite offset no mÃƒÂ¡ximo atÃƒÂ© 1000. Use carregar_todas=true para buscar com scan.")
            proximo_offset = min(offset, 1000)
            params = {
                "seller_id": seller_id,
                "api_version": 4,
                "offset": proximo_offset,
                "limit": limit,
                "sort_fields": "date_created",
                "sort_types": "DESC",
            }
            if status_filtro:
                params["status"] = status_filtro

            resp, cfg = _ml_api_request(client_id, nome_loja, cfg, "GET", url, params=params, timeout=20)
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=_ml_parse_error_detail(resp, "Erro ao buscar perguntas do Mercado Livre"))

            data = resp.json() or {}
            lote = data.get("questions") or data.get("results") or []
            if not isinstance(lote, list):
                lote = []
            perguntas.extend([q for q in lote if isinstance(q, dict)])
            total = int(data.get("total") or data.get("paging", {}).get("total") or len(perguntas) or 0)
            filtros = data.get("filters") or {}
            paginas_lidas = 1
            proximo_offset += limit
            interrompido = proximo_offset < total and proximo_offset > 1000

        perguntas.sort(key=lambda pergunta: str((pergunta or {}).get("date_created") or (pergunta or {}).get("last_updated") or ""), reverse=True)

        item_ids = []
        vistos = set()
        for pergunta in perguntas:
            item_id = str(pergunta.get("item_id") or "").strip()
            if item_id and item_id not in vistos:
                vistos.add(item_id)
                item_ids.append(item_id)
            if len(item_ids) >= 500:
                break

        user_ids = []
        user_ids_vistos = set()
        for pergunta in perguntas:
            comprador = pergunta.get("from") if isinstance(pergunta.get("from"), dict) else {}
            user_id = str(comprador.get("id") or "").strip()
            if user_id and user_id not in user_ids_vistos:
                user_ids_vistos.add(user_id)
                user_ids.append(user_id)
            if len(user_ids) >= 500:
                break

        itens, cfg = _ml_buscar_itens_batch(client_id, nome_loja, cfg, item_ids)
        itens = _ml_perguntas_completar_skus_itens(client_id, nome_loja, cfg, itens)
        item_por_id = {str(item.get("id") or "").strip(): item for item in itens if isinstance(item, dict)}
        usuario_por_id, cfg = _ml_perguntas_buscar_usuarios(client_id, nome_loja, cfg, user_ids)
        perguntas_norm = [_ml_perguntas_normalizar(pergunta, item_por_id, usuario_por_id) for pergunta in perguntas]
        perguntas_norm, cfg = _ml_perguntas_anexar_historico_comprador(client_id, nome_loja, cfg, seller_id, perguntas_norm)
        tempo_resposta_ml, cfg = _ml_perguntas_tempo_resposta(client_id, nome_loja, cfg, seller_id)

        return jsonable_encoder({
            "success": True,
            "loja": nome_loja,
            "seller_id": seller_id,
            "total": total,
            "retornadas": len(perguntas_norm),
            "offset": offset,
            "limit": limit,
            "next_offset": None if carregar_todas else (proximo_offset if proximo_offset < total and not interrompido else None),
            "interrompido": interrompido,
            "modo_busca": "scan" if usar_scan else "offset",
            "status_resumo": _ml_perguntas_resumir_status(perguntas_norm),
            "tempo_resposta_ml": tempo_resposta_ml,
            "filters": filtros,
            "questions": perguntas_norm,
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[ML PERGUNTAS] Falha inesperada ao listar perguntas da loja {loja}: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao listar perguntas do Mercado Livre: {str(e)}")



__all__ = [
    "PERGUNTAS_POS_VENDA_ENDPOINTS",
    "configure_perguntas_pos_venda_endpoints_runtime",
    *PERGUNTAS_POS_VENDA_ENDPOINTS,
]
