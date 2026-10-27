"""Bounded repair of fresh, individually confirmed Renovacao discrepancies."""

from __future__ import annotations

import copy
import re
from typing import Callable, Optional

from fastapi import HTTPException


def _renovacao_corrigir_divergencias_promocao(
    client_id: str,
    loja: str,
    campanha_origem_id: str,
    campanha_destino_id: str,
    promotion_type_origem: str = "SELLER_CAMPAIGN",
    promotion_type_destino: str = "SELLER_CAMPAIGN",
    progress_callback: Optional[Callable[[dict], None]] = None,
    quantidade_origem_esperada: int | None = None,
) -> dict:
    # The worker imports this module only for the explicitly requested repair
    # mode. Keep the existing read-only route and full-copy guard independent.
    from backend.services import renovacao_promocoes as service

    loja = str(loja or "").strip()
    campanha_origem_id = str(campanha_origem_id or "").strip()
    campanha_destino_id = str(campanha_destino_id or "").strip()

    def progresso_fase(inicio, fim):
        def emitir(payload):
            data = dict(payload or {})
            percentual = max(0, min(100, float(data.get("percentual", data.get("progress", 0)) or 0)))
            data.update(percentual=round(inicio + (fim - inicio) * percentual / 100),
                        progress=round(inicio + (fim - inicio) * percentual / 100))
            if progress_callback:
                progress_callback(data)
        return emitir

    def auditar(inicio, fim):
        return service._renovacao_sincronizar_promocao_existente(
            client_id, loja, campanha_origem_id, campanha_destino_id,
            promotion_type_origem, promotion_type_destino,
            progress_callback=progresso_fase(inicio, fim),
            quantidade_origem_esperada=quantidade_origem_esperada, somente_conferir=True,
        )

    def fotografias(resultado):
        conferencia = resultado.get("conferencia") or {}
        rows = {str(row["item_id"]): dict(row) for row in conferencia.get("itens") or []
                if isinstance(row, dict) and row.get("item_id")}
        for row in conferencia.get("inconclusivos") or []:
            if isinstance(row, dict) and row.get("item_id"):
                rows.setdefault(str(row["item_id"]), {"item_id": str(row["item_id"]),
                    "confirmado": False, "erro": row.get("erro") or "Consulta inconclusiva"})
        return rows

    antes = auditar(3, 35)
    fotos_antes = fotografias(antes)
    candidatos = sorted(item for item, row in fotos_antes.items() if row.get("confirmado") is not True)
    tentativas = {item: [] for item in candidatos}
    tentados, regularizados, fora_origem = set(), set(), set()
    acoes = {}
    erros = {}
    passagens = 0
    consulta_final_inconclusiva = False
    depois = antes

    if candidatos:
        cfg = service.ctx._obter_cfg_ml(client_id, loja)
        if not isinstance(cfg, dict) or not str(cfg.get("user_id") or "").strip():
            raise HTTPException(502, "Conta da loja nao confirmada para corrigir as divergencias.")
        restantes = list(candidatos)
        for passagem in range(1, 3):
            if not restantes:
                break
            passagens = passagem
            preparados = {}
            for numero, item_id in enumerate(restantes, start=1):
                service._renovacao_emitir_progresso(
                    progress_callback, "Correcao", 35 + 20 * (passagem - 1) + 10 * numero / len(restantes),
                    f"Revalidando divergencias: {numero}/{len(restantes)} (tentativa {passagem}/2).",
                )
                if not re.fullmatch(r"ML[A-Z]\d+", item_id):
                    erros[item_id] = "Identificador do anuncio invalido; nenhuma alteracao aplicada"
                    continue
                try:
                    estado, cfg = service._renovacao_consultar_participacoes(
                        client_id, loja, cfg, item_id, [campanha_origem_id, campanha_destino_id])
                    origem = estado.get(campanha_origem_id) or {}
                    destino = estado.get(campanha_destino_id) or {}
                    if origem.get("situacao") == "ausente":
                        fora_origem.add(item_id)
                        erros[item_id] = "Anuncio sem participacao confirmada na origem; nenhuma alteracao aplicada"
                        continue
                    if origem.get("situacao") != "participante":
                        erros[item_id] = origem.get("erro") or "Participacao na origem inconclusiva; nenhuma alteracao aplicada"
                        continue
                    percentual, _, preco_origem = service._renovacao_desconto_item(item_id, origem.get("raw") or {}, {})
                    if percentual is None:
                        erros[item_id] = "Percentual atual da origem nao confirmado; nenhuma alteracao aplicada"
                        continue
                    if destino.get("situacao") not in {"participante", "ausente"}:
                        erros[item_id] = destino.get("erro") or "Participacao no destino inconclusiva; nenhuma alteracao aplicada"
                        continue
                    if destino.get("situacao") == "participante" and service._renovacao_participacao_mantem_desconto(
                        destino.get("raw") or {}, item_id, campanha_destino_id, percentual):
                        regularizados.add(item_id)
                        erros.pop(item_id, None)
                        continue
                    valores = service._renovacao_valores_participacao(destino.get("raw") or {}, item_id, campanha_destino_id)
                    preparados[item_id] = {"percentual": percentual, "preco_origem": preco_origem,
                        "base": valores[1] if valores else None,
                        "metodo": "PUT" if destino.get("situacao") == "participante" else "POST"}
                except HTTPException as exc:
                    erros[item_id] = str(exc.detail)
                except Exception:
                    erros[item_id] = "Falha na reconsulta deste anuncio; nenhuma alteracao aplicada"

            # Existing, valid participation supplies its own base. Only listings
            # still needing a base use the current item/standard-price adapter.
            precisam_base = [item for item, data in preparados.items() if data["base"] is None]
            detalhes_por_id = {}
            if precisam_base:
                try:
                    detalhes, cfg = service.ctx._ml_buscar_itens_batch(client_id, loja, cfg, precisam_base)
                    detalhes_por_id = {str(row.get("id")): row for row in detalhes
                                       if isinstance(row, dict) and row.get("id") in precisam_base}
                except Exception:
                    detalhes_por_id = {}
            for numero, (item_id, data) in enumerate(preparados.items(), start=1):
                service._renovacao_emitir_progresso(
                    progress_callback, "Correcao", 45 + 20 * (passagem - 1) + 10 * numero / max(1, len(preparados)),
                    f"Tentando corrigir {numero}/{len(preparados)} divergencias (tentativa {passagem}/2).",
                )
                tentativa_iniciada = False
                try:
                    base = data["base"]
                    if base is None:
                        base, cfg = service._renovacao_preco_base_atual(
                            client_id, loja, cfg, item_id, detalhes_por_id.get(item_id) or {})
                    if service._renovacao_preco_com_desconto(base, data["percentual"]) is None:
                        erros[item_id] = "Preco base atual nao confirmado; nenhuma alteracao aplicada"
                        continue
                    tentados.add(item_id)
                    acoes.setdefault(item_id, data["metodo"])
                    tentativa_iniciada = True
                    ok, erro, cfg = service._renovacao_adicionar_item_campanha(
                        client_id, loja, cfg, item_id, campanha_destino_id, data["percentual"], base,
                        data["preco_origem"], preferir_percentual=True, metodo=data["metodo"],
                        campanha_origem_id=campanha_origem_id,
                    )
                    tentativas[item_id].append({"passagem": passagem, "acao": data["metodo"],
                        "confirmado_apos_tentativa": bool(ok), "erro": erro or ""})
                    if ok:
                        regularizados.add(item_id)
                        erros.pop(item_id, None)
                    else:
                        erros[item_id] = erro or "O desconto ainda nao foi confirmado no destino"
                except HTTPException as exc:
                    erros[item_id] = str(exc.detail)
                    if tentativa_iniciada:
                        tentativas[item_id].append({"passagem": passagem, "acao": data["metodo"],
                            "confirmado_apos_tentativa": False, "erro": str(exc.detail)})
                except Exception:
                    erros[item_id] = "Falha ao consultar ou corrigir o desconto deste anuncio"
                    if tentativa_iniciada:
                        tentativas[item_id].append({"passagem": passagem, "acao": data["metodo"],
                            "confirmado_apos_tentativa": False, "erro": erros[item_id]})
            restantes = [item for item in candidatos if item not in regularizados and item not in fora_origem]

        if tentados:
            try:
                service.ctx._cache_invalidar_loja(client_id, loja)
            except Exception:
                pass
        try:
            # Confirmation is independent from successful write responses and
            # from the per-item reads performed inside the correction helper.
            depois = auditar(76, 98)
        except Exception:
            consulta_final_inconclusiva = True
            depois = copy.deepcopy(antes)
            depois["success"] = False
            conferencia = depois.setdefault("conferencia", {})
            conferencia.update(completa=False, confirmados=0, pendentes=conferencia.get("total_origem", 0))
            for row in conferencia.get("itens") or []:
                row.update(confirmado=False, status_origem="", status_destino="",
                           percentual_origem=None, percentual_destino=None,
                           preco_origem=None, preco_destino=None, base_destino=None,
                           erro="Conferencia final inconclusiva; nao foi possivel confirmar o resultado atual")
            falhas_por_id = {str(row["item_id"]): {"item_id": str(row["item_id"]),
                "erro": row.get("erro") or "Conferencia final inconclusiva"}
                for row in list(conferencia.get("itens") or []) + list(conferencia.get("inconclusivos") or [])
                if isinstance(row, dict) and row.get("item_id")}
            depois["falhas"] = [falhas_por_id[item] for item in sorted(falhas_por_id)[:50]]
            depois["falhas_total"] = len(falhas_por_id)
            depois["erro_conferencia_final"] = "A conferencia final falhou; confira novamente antes de concluir o resultado."

    fotos_depois = fotografias(depois)
    confirmados = {item for item in candidatos if fotos_depois.get(item, {}).get("confirmado") is True}
    fora_origem = {item for item in fora_origem if item not in fotos_depois}
    pendentes = set(candidatos) - confirmados - fora_origem
    corrigidos = confirmados & tentados
    incluidos = {item for item in corrigidos if acoes.get(item) == "POST"}
    linhas = []
    for item in candidatos:
        foto_depois = fotos_depois.get(item) or {"item_id": item, "confirmado": False,
            "erro": erros.get(item) or "Participacao na origem nao confirmada na conferencia final"}
        linhas.append({"item_id": item, "antes": fotos_antes[item], "depois": foto_depois,
            "acao": acoes.get(item) or ("fora_origem" if item in fora_origem else "conferir"),
            "tentativas": tentativas[item], "corrigido": item in corrigidos,
            "erro": "" if item in confirmados else foto_depois.get("erro") or erros.get(item) or "Consulta inconclusiva"})
    resultado = dict(depois)
    resultado.update(somente_conferir=False, corrigir_divergencias=True,
        incluidos=len(incluidos), atualizados=len(corrigidos - incluidos),
        mlbs_ja_presentes=antes.get("mlbs_ja_presentes", 0), faltantes=antes.get("faltantes", 0),
        success=bool(depois.get("success") and (depois.get("conferencia") or {}).get("completa")
                     and not pendentes and not consulta_final_inconclusiva),
        erros_operacao={item: erros.get(item) or fotos_depois.get(item, {}).get("erro") or "Consulta inconclusiva"
                        for item in sorted(pendentes)},
        correcao={"candidatos": len(candidatos), "ids_candidatos": candidatos,
            "tentados": len(tentados), "tentativas": sum(len(value) for value in tentativas.values()),
            "corrigidos": len(corrigidos), "incluidos": len(incluidos),
            "atualizados": len(corrigidos - incluidos), "ja_regularizados": len(confirmados - tentados),
            "fora_origem": len(fora_origem), "pendentes": len(pendentes), "passagens": passagens,
            "conferencia_final_inconclusiva": consulta_final_inconclusiva,
            "contagem_coerente_antes": (antes.get("conferencia") or {}).get("contagem_coerente"),
            "contagem_coerente_depois": (depois.get("conferencia") or {}).get("contagem_coerente"),
            "confirmados_antes": (antes.get("conferencia") or {}).get("confirmados", 0),
            "confirmados_depois": (depois.get("conferencia") or {}).get("confirmados", 0),
            "itens": linhas})
    return service._renovacao_sanitizar_relatorio(resultado)
