"""Write Favoritos static history snapshots to the configured Google Sheet."""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any

from fastapi import HTTPException

from backend.services import admin_usuarios_common
from backend.services import favoritos as favoritos_service


PARES_PLANILHA_HISTORICO_FAVORITOS = (
    ("C", "D", "E", "F"),
    ("G", "H", "I", "J"),
    ("K", "L", "M", "N"),
    ("O", "P", "Q", "R"),
    ("S", "T", "U", "V"),
    ("W", "X", "Y", "Z"),
)


def _texto(valor: Any) -> str:
    return re.sub(r"\s+", " ", str(valor or "").strip())


def _normalizar_sku(valor: Any) -> str:
    return re.sub(r"\s+", "", str(valor or "").strip().upper())


def _sku_igual(a: Any, b: Any) -> bool:
    sku_a = _normalizar_sku(a)
    sku_b = _normalizar_sku(b)
    if not sku_a or not sku_b:
        return False
    if sku_a == sku_b:
        return True
    if sku_a.isdigit() and sku_b.isdigit():
        return int(sku_a) == int(sku_b)
    return False


def _loja_bloqueada(valor: Any) -> bool:
    texto = _texto(valor).lower()
    chave = favoritos_service._chave_loja_favoritos(valor)
    return not texto or texto in {"todas as lojas", "todas"} or chave in {"__todas", "todas as lojas", "todas_as_lojas", "todas"}


def _spreadsheet_id(url: Any) -> str:
    texto = str(url or "").strip()
    match = re.search(r"/spreadsheets/d/([^/?#]+)", texto)
    return match.group(1) if match else ""


def _candidatos_credentials_google_sheets() -> list[str]:
    candidatos = [
        os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", ""),
        str(getattr(admin_usuarios_common, "CREDENTIALS_FILE", "") or ""),
    ]
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        candidatos.append(os.path.join(appdata, "JK Sistema Cliente", "local_app", "info", "credentials.json"))
    candidatos.append(os.path.join(os.getcwd(), "info", "credentials.json"))

    vistos: set[str] = set()
    saida: list[str] = []
    for caminho in candidatos:
        valor = os.path.abspath(str(caminho or "").strip()) if str(caminho or "").strip() else ""
        if not valor or valor in vistos:
            continue
        vistos.add(valor)
        saida.append(valor)
    return saida


def _autenticar_google_sheets_favoritos():
    try:
        cliente = admin_usuarios_common.autenticar_google_sheets()
    except Exception:
        cliente = None
    if cliente:
        return cliente

    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except Exception:
        return None

    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    ultimo_erro = ""
    for caminho in _candidatos_credentials_google_sheets():
        if not os.path.exists(caminho):
            continue
        try:
            creds = Credentials.from_service_account_file(caminho, scopes=scopes)
            return gspread.authorize(creds)
        except Exception as exc:
            ultimo_erro = str(exc)
            continue
    if ultimo_erro:
        raise HTTPException(status_code=500, detail=f"Falha ao autenticar Google Sheets: {ultimo_erro}")
    return None


def _numero(valor: Any) -> float | None:
    if valor is None or valor == "":
        return None
    if isinstance(valor, (int, float)):
        numero = float(valor)
        return numero if numero == numero and numero not in (float("inf"), float("-inf")) else None
    texto = str(valor).strip()
    if not texto:
        return None
    match = re.search(r"-?\d[\d.,]*", texto.replace("\xa0", " "))
    if not match:
        return None
    normalizado = match.group(0)
    if "." in normalizado and "," in normalizado:
        normalizado = normalizado.replace(".", "").replace(",", ".") if normalizado.rfind(",") > normalizado.rfind(".") else normalizado.replace(",", "")
    elif "," in normalizado:
        normalizado = normalizado.replace(".", "").replace(",", ".")
    try:
        numero = float(normalizado)
    except ValueError:
        return None
    return numero if numero == numero and numero not in (float("inf"), float("-inf")) else None


def _primeiro_numero(item: dict[str, Any], campos: tuple[str, ...]) -> float | None:
    for campo in campos:
        valor = _numero(item.get(campo))
        if valor is not None:
            return valor
    return None


def _primeiro_numero_objetos(objetos: tuple[Any, ...], campos: tuple[str, ...]) -> float | None:
    for objeto in objetos:
        item = objeto if isinstance(objeto, dict) else {}
        valor = _primeiro_numero(item, campos)
        if valor is not None:
            return valor
    return None


def _preco_final_anuncio(anuncio: Any) -> float | None:
    item = anuncio if isinstance(anuncio, dict) else {}
    promocional = _primeiro_numero(
        item,
        (
            "preco_promocional",
            "promotional_price",
            "promotion_price",
            "deal_price",
            "discounted_price",
        ),
    )
    if promocional is not None:
        return promocional
    sale_price = item.get("sale_price")
    if isinstance(sale_price, dict):
        valor_sale = _numero(sale_price.get("amount") if sale_price.get("amount") is not None else sale_price.get("price"))
        if valor_sale is not None:
            return valor_sale
    return _primeiro_numero(
        item,
        (
            "price",
            "preco",
            "valor",
            "standard_price",
            "base_price",
            "preco_original",
            "original_price",
        ),
    )


def _preco_final_nosso(vinculo: Any) -> float | None:
    item = vinculo if isinstance(vinculo, dict) else {}
    simulacao = item.get("simulacao") if isinstance(item.get("simulacao"), dict) else {}
    fallback_sem_campanha = bool(
        simulacao.get("fallback_sem_promocao")
        or simulacao.get("fallback_sem_promocao_aplicado")
        or simulacao.get("sem_campanha")
    )
    if not fallback_sem_campanha:
        promocional = _primeiro_numero(
            simulacao,
            (
                "preco_promocional_aplicado",
                "preco_final_promocional_aplicado",
                "preco_promocional_previsto",
                "preco_final_promocional_previsto",
            ),
        )
        if promocional is not None:
            return promocional
    aplicado = _primeiro_numero(
        simulacao,
        (
            "preco_aplicado",
            "preco_cheio_aplicado",
            "preco_previsto",
            "preco_cheio_previsto",
            "preco_final",
        ),
    )
    if aplicado is not None:
        return aplicado
    return _preco_final_anuncio(item.get("nosso"))


def _link_anuncio(anuncio: Any) -> str:
    item = anuncio if isinstance(anuncio, dict) else {}
    return _texto(item.get("url") or item.get("permalink") or item.get("link"))


def _mlb_anuncio(anuncio: Any) -> str:
    item = anuncio if isinstance(anuncio, dict) else {}
    return _texto(item.get("id") or item.get("mlb") or item.get("item_id"))


def _formatar_data_br(valor: Any) -> str:
    texto = str(valor or "").strip()
    if texto:
        for candidato in (texto, texto.replace("Z", "+00:00")):
            try:
                return datetime.fromisoformat(candidato).strftime("%d/%m/%Y")
            except ValueError:
                pass
        match = re.search(r"(\d{2}/\d{2}/\d{4})", texto)
        if match:
            return match.group(1)
    return datetime.now().strftime("%d/%m/%Y")


def _formatar_moeda_br(valor: Any) -> str:
    numero = _numero(valor)
    if numero is None:
        return ""
    texto = f"{numero:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


def _formatar_percentual_br(valor: Any) -> str:
    numero = _numero(valor)
    if numero is None:
        return ""
    return f"{numero:.2f}%".replace(".", ",")


def _plural(qtd: int, singular: str, plural: str | None = None) -> str:
    return singular if int(qtd) == 1 else (plural or f"{singular}s")


def _titulo_curto_anuncio(anuncio: Any, limite: int = 70) -> str:
    item = anuncio if isinstance(anuncio, dict) else {}
    titulo = _texto(item.get("titulo") or item.get("title"))
    if not titulo:
        return ""
    return titulo if len(titulo) <= limite else f"{titulo[: limite - 3].rstrip()}..."


def _textos_relatorio_vinculo(item: dict[str, Any]) -> list[str]:
    textos = [
        _texto(item.get("status_texto")),
        _texto(item.get("status")),
    ]
    for chave in ("relatorio_final", "relatorio_inicial"):
        relatorio = item.get(chave)
        if not isinstance(relatorio, dict):
            continue
        for campo in ("detalhe", "resumo", "mensagem", "titulo", "status"):
            valor = _texto(relatorio.get(campo))
            if valor:
                textos.append(valor)
    return [texto for texto in textos if texto]


def _texto_busca_vinculo(item: dict[str, Any]) -> str:
    simulacao = item.get("simulacao") if isinstance(item.get("simulacao"), dict) else {}
    partes = _textos_relatorio_vinculo(item)
    partes.extend(f"{chave}={valor}" for chave, valor in simulacao.items() if valor not in (None, ""))
    return " ".join(partes).lower()


def _vinculo_fallback_sem_campanha(item: dict[str, Any]) -> bool:
    simulacao = item.get("simulacao") if isinstance(item.get("simulacao"), dict) else {}
    if (
        simulacao.get("fallback_sem_promocao")
        or simulacao.get("fallback_sem_promocao_aplicado")
        or simulacao.get("sem_campanha")
    ):
        return True
    texto = _texto_busca_vinculo(item)
    return any(
        termo in texto
        for termo in (
            "fallback",
            "sem campanha",
            "campanha nao aplicada",
            "campanha removida",
            "campanha recusada",
            "mercado livre nao aceitou",
        )
    )


def _vinculo_sucesso(item: dict[str, Any]) -> bool:
    status = _texto(item.get("status")).lower()
    if status in {"success", "sucesso", "ok", "feito", "alterado"}:
        return True
    texto = _texto_busca_vinculo(item)
    return any(termo in texto for termo in ("alteracao feita", "feito sem campanha", "favorito feito", "aplicado fallback"))


def _vinculo_travado_margem(item: dict[str, Any]) -> bool:
    simulacao = item.get("simulacao") if isinstance(item.get("simulacao"), dict) else {}
    if simulacao.get("limite_margem_aplicado") or simulacao.get("limiteMargemAplicado"):
        return True
    texto = _texto_busca_vinculo(item)
    return "margem 15" in texto or "margem de 15" in texto or "limite de margem" in texto or "travado" in texto


def _resumo_geral_planilha(historico: dict[str, Any]) -> str:
    vinculos = historico.get("vinculos") if isinstance(historico.get("vinculos"), list) else []
    total = len(vinculos)
    sucesso = 0
    fallback = 0
    falhas = 0
    travados = 0
    for vinculo in vinculos:
        item = vinculo if isinstance(vinculo, dict) else {}
        if _vinculo_sucesso(item):
            sucesso += 1
        else:
            falhas += 1
        if _vinculo_fallback_sem_campanha(item):
            fallback += 1
        if _vinculo_travado_margem(item):
            travados += 1

    if total <= 0:
        return _texto(historico.get("mensagem_final")) or "Nenhum anuncio relacionado foi enviado para a planilha."

    verbo_sucesso = "foi feito" if sucesso == 1 else "foram feitos"
    partes = [f"{verbo_sucesso} {sucesso} de {total} {_plural(total, 'favorito')} no Mercado Livre."]
    if fallback:
        partes.append(f"Em {fallback}, o Mercado Livre nao aceitou a campanha, entao o preco final foi aplicado direto no anuncio.")
    if travados:
        verbo = "ficou" if travados == 1 else "ficaram"
        partes.append(f"{travados} {_plural(travados, 'anuncio')} {verbo} travado na margem minima de 15%.")
    if falhas:
        verbo = "foi" if falhas == 1 else "foram"
        partes.append(f"{falhas} {_plural(falhas, 'anuncio')} nao {verbo} alterado.")
    return " ".join(partes)


def _resumo_relatorio_vinculo(indice: int, vinculo: Any) -> str:
    item = vinculo if isinstance(vinculo, dict) else {}
    nosso = item.get("nosso") if isinstance(item.get("nosso"), dict) else {}
    base = item.get("base") if isinstance(item.get("base"), dict) else {}
    ordem = int(item.get("ordem") or indice)
    titulo_nosso = _titulo_curto_anuncio(nosso)
    titulo_base = _titulo_curto_anuncio(base)
    nosso_preco = _preco_final_nosso(item)
    base_preco = _preco_final_anuncio(base)
    sucesso = _vinculo_sucesso(item)
    fallback = _vinculo_fallback_sem_campanha(item)
    travado = _vinculo_travado_margem(item)
    concorrendo = (
        nosso_preco is not None
        and base_preco is not None
        and float(nosso_preco) <= float(base_preco) + 0.01
    )

    partes = [f"{ordem}o anuncio:"]
    if titulo_nosso:
        partes.append(f"{titulo_nosso}.")

    if sucesso:
        if fallback:
            partes.append("Favorito feito. O Mercado Livre nao aceitou a campanha, entao o preco final foi aplicado direto no anuncio.")
        elif concorrendo:
            partes.append("Favorito feito, produto colocado em promocao e concorrendo.")
        else:
            partes.append("Favorito feito e produto colocado em promocao.")
    else:
        partes.append("Favorito nao concluido. O anuncio precisa ser revisado manualmente.")

    if concorrendo:
        if not (sucesso and not fallback):
            partes.append("Estamos concorrendo pelo preco.")
    elif nosso_preco is not None and base_preco is not None:
        partes.append("Nao estamos concorrendo pelo preco.")

    if travado:
        partes.append("Produto travado na margem minima de 15%.")

    preco_nosso_txt = _formatar_moeda_br(nosso_preco)
    preco_base_txt = _formatar_moeda_br(base_preco)
    detalhes = []
    if preco_nosso_txt:
        detalhes.append(f"nosso preco final {preco_nosso_txt}")
    if preco_base_txt:
        detalhes.append(f"preco do concorrente {preco_base_txt}")
    if titulo_base:
        detalhes.append(f"concorrente usado: {titulo_base}")
    if detalhes:
        partes.append("Detalhes: " + "; ".join(detalhes) + ".")

    return " ".join(partes)


def _comentario_historico_planilha(historico: dict[str, Any]) -> str:
    vinculos = historico.get("vinculos") if isinstance(historico.get("vinculos"), list) else []
    linhas = [_resumo_geral_planilha(historico)]
    linhas.extend(
        resumo
        for resumo in (
            _resumo_relatorio_vinculo(indice + 1, vinculo)
            for indice, vinculo in enumerate(vinculos)
        )
        if resumo
    )
    return "\n".join(linhas)


def _custo_base_vinculo(item: dict[str, Any]) -> float | None:
    simulacao = item.get("simulacao") if isinstance(item.get("simulacao"), dict) else {}
    nosso = item.get("nosso") if isinstance(item.get("nosso"), dict) else {}
    return _primeiro_numero_objetos(
        (simulacao, nosso),
        (
            "custo",
            "custo_base",
            "custo_unitario",
            "custo_produto",
            "preco_custo",
            "valor_custo",
            "product_cost",
            "cost",
        ),
    )


def _margem_final_vinculo(item: dict[str, Any]) -> float | None:
    simulacao = item.get("simulacao") if isinstance(item.get("simulacao"), dict) else {}
    return _primeiro_numero(
        simulacao,
        (
            "margem_aplicada",
            "margem_estimada_contingencia",
            "margem_prevista",
            "margem",
        ),
    )


def _custo_ideal_concorrencia_vinculo(item: dict[str, Any]) -> float | None:
    if not _vinculo_travado_margem(item):
        return None
    simulacao = item.get("simulacao") if isinstance(item.get("simulacao"), dict) else {}
    return _primeiro_numero(
        simulacao,
        (
            "custo_ideal_abaixo_base",
            "custoIdealAbaixoBase",
            "preco_custo_ideal",
            "preco_custo_para_competir",
            "preco_custo_necessario",
            "novo_preco_custo",
            "custo_para_concorrer",
            "custo_maximo_para_concorrer",
            "custo_maximo_concorrencia",
            "custo_ideal",
        ),
    )


def _metricas_margem_planilha(historico: dict[str, Any]) -> tuple[Any, Any, Any, list[str]]:
    vinculos = historico.get("vinculos") if isinstance(historico.get("vinculos"), list) else []
    custos_ideais: list[float] = []
    margens: list[float] = []
    custos_base: list[float] = []
    tem_trava_margem = False
    avisos: list[str] = []

    for vinculo in vinculos:
        item = vinculo if isinstance(vinculo, dict) else {}
        if _vinculo_travado_margem(item):
            tem_trava_margem = True
        custo_ideal = _custo_ideal_concorrencia_vinculo(item)
        if custo_ideal is not None:
            custos_ideais.append(custo_ideal)
        margem = _margem_final_vinculo(item)
        if margem is not None:
            margens.append(margem)
        custo_base = _custo_base_vinculo(item)
        if custo_base is not None:
            custos_base.append(custo_base)

    custo_ideal_coluna = round(min(custos_ideais), 2) if tem_trava_margem and custos_ideais else ""
    menor_margem_coluna = _formatar_percentual_br(min(margens)) if margens else ""
    custo_base_coluna = round(custos_base[0], 2) if custos_base else ""
    if tem_trava_margem and not custos_ideais:
        avisos.append("Houve anuncio travado em 15%, mas nao encontrei o custo necessario para concorrer; a coluna AC foi limpa.")
    if len({round(custo, 2) for custo in custos_base}) > 1:
        avisos.append("Os anuncios tinham custos base diferentes; a coluna AF recebeu o primeiro custo encontrado.")
    return custo_ideal_coluna, menor_margem_coluna, custo_base_coluna, avisos


def _montar_valores_linha(historico: dict[str, Any]) -> tuple[list[Any], list[str], int]:
    vinculos = historico.get("vinculos") if isinstance(historico.get("vinculos"), list) else []
    valores_c_z: list[Any] = [""] * 24
    avisos: list[str] = []

    for indice, vinculo in enumerate(vinculos[: len(PARES_PLANILHA_HISTORICO_FAVORITOS)]):
        item = vinculo if isinstance(vinculo, dict) else {}
        nosso = item.get("nosso") if isinstance(item.get("nosso"), dict) else {}
        base = item.get("base") if isinstance(item.get("base"), dict) else {}
        nosso_link = _link_anuncio(nosso)
        base_link = _link_anuncio(base)
        nosso_preco = _preco_final_nosso(item)
        base_preco = _preco_final_anuncio(base)
        offset = indice * 4
        valores_c_z[offset] = nosso_link
        valores_c_z[offset + 1] = nosso_preco if nosso_preco is not None else ""
        valores_c_z[offset + 2] = base_link
        valores_c_z[offset + 3] = base_preco if base_preco is not None else ""
        if not nosso_link:
            avisos.append(f"{indice + 1}o vinculo sem link do nosso anuncio.")
        if nosso_preco is None:
            avisos.append(f"{indice + 1}o vinculo sem preco final do nosso anuncio.")
        if not base_link:
            avisos.append(f"{indice + 1}o vinculo sem link do concorrente.")
        if base_preco is None:
            avisos.append(f"{indice + 1}o vinculo sem preco final do concorrente.")

    if len(vinculos) > len(PARES_PLANILHA_HISTORICO_FAVORITOS):
        avisos.append(f"{len(vinculos) - len(PARES_PLANILHA_HISTORICO_FAVORITOS)} vinculo(s) excedente(s) foram enviados apenas para o comentario.")

    return valores_c_z, avisos, min(len(vinculos), len(PARES_PLANILHA_HISTORICO_FAVORITOS))


def _localizar_linha_sku(worksheet: Any, sku: str) -> int:
    sku_alvo = _normalizar_sku(sku)
    if not sku_alvo:
        raise HTTPException(status_code=400, detail="Historico sem SKU para localizar na planilha.")
    try:
        valores = worksheet.col_values(1)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Nao consegui ler a coluna A da planilha: {exc}") from exc
    for indice, valor in enumerate(valores, start=1):
        if _sku_igual(valor, sku_alvo):
            return indice
    raise HTTPException(status_code=404, detail=f"SKU {sku} nao encontrado na coluna A da planilha.")


def favoritos_colar_historico_planilha(client_id: str, loja: Any, historico: Any) -> dict[str, Any]:
    nome_loja = _texto(loja)
    if _loja_bloqueada(nome_loja):
        raise HTTPException(status_code=400, detail="Selecione uma loja especifica. Todas as lojas nao pode colar na planilha.")
    item_historico = historico if isinstance(historico, dict) else {}
    sku = _texto(item_historico.get("sku"))
    if not sku:
        raise HTTPException(status_code=400, detail="Historico sem SKU.")

    payload_planilhas = favoritos_service._favoritos_carregar_planilhas_lojas(client_id)
    chave_loja = favoritos_service._chave_loja_favoritos(nome_loja)
    planilha = (payload_planilhas.get("planilhas") or {}).get(chave_loja) or {}
    url_planilha = planilha.get("url") or ""
    planilha_id = _spreadsheet_id(url_planilha)
    if not planilha_id:
        raise HTTPException(status_code=404, detail=f"Nenhuma planilha cadastrada para a loja {nome_loja}.")

    cliente_sheets = _autenticar_google_sheets_favoritos()
    if not cliente_sheets:
        raise HTTPException(status_code=500, detail="Google Sheets nao esta autenticado no sistema.")
    try:
        spreadsheet = cliente_sheets.open_by_key(planilha_id)
        worksheet = spreadsheet.get_worksheet(0)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Nao consegui abrir a planilha cadastrada: {exc}") from exc
    if worksheet is None:
        raise HTTPException(status_code=404, detail="A planilha cadastrada nao possui abas.")

    linha = _localizar_linha_sku(worksheet, sku)
    valores_c_z, avisos, pares_colados = _montar_valores_linha(item_historico)
    custo_competir, menor_margem, custo_base, avisos_metricas = _metricas_margem_planilha(item_historico)
    avisos.extend(avisos_metricas)
    data_br = _formatar_data_br(item_historico.get("data_iso"))
    comentario = _comentario_historico_planilha(item_historico)

    updates = [
        {"range": f"C{linha}:Z{linha}", "values": [valores_c_z]},
        {"range": f"AA{linha}:AB{linha}", "values": [[data_br, "BJ"]]},
        {"range": f"AC{linha}", "values": [[custo_competir]]},
        {"range": f"AE{linha}:AF{linha}", "values": [[menor_margem, custo_base]]},
        {"range": f"AI{linha}", "values": [[comentario]]},
    ]
    try:
        try:
            worksheet.batch_update(updates, value_input_option="RAW")
        except TypeError:
            worksheet.batch_update(updates)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Nao consegui escrever na planilha: {exc}") from exc

    return {
        "success": True,
        "loja": nome_loja,
        "sku": sku,
        "linha": linha,
        "pares_colados": pares_colados,
        "avisos": avisos,
    }


__all__ = ["favoritos_colar_historico_planilha"]
