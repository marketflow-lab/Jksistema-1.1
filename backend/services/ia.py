"""Small IA utility helpers shared by legacy IA endpoints."""

from __future__ import annotations

import base64
import datetime as dt
import logging
import re
import unicodedata
from typing import Optional

import fitz
import requests


logger = logging.getLogger("jk_sistema")


def _normalizar_texto(texto: str):
    if not texto:
        return ""
    texto = str(texto).upper()
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join([c for c in nfkd if not unicodedata.combining(c)])


def _extrair_texto_openai_response(payload: dict) -> str:
    texto = payload.get("output_text")
    if isinstance(texto, str) and texto.strip():
        return texto.strip()

    partes = []
    for item in payload.get("output") or []:
        for content in item.get("content") or []:
            if isinstance(content, dict):
                valor = content.get("text")
                if isinstance(valor, str) and valor.strip():
                    partes.append(valor.strip())
    return "\n".join(partes).strip()


def _extrair_b64_openai_image_response(payload: dict) -> str:
    try:
        data = payload.get("data")
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and isinstance(item.get("b64_json"), str) and item.get("b64_json").strip():
                    return item.get("b64_json").strip()
                if isinstance(item, dict) and isinstance(item.get("url"), str) and item.get("url").strip():
                    try:
                        resp_img = requests.get(item.get("url"), timeout=60)
                        if resp_img.ok and resp_img.content:
                            return base64.b64encode(resp_img.content).decode("ascii")
                    except Exception:
                        pass
    except Exception:
        pass
    return ""


def _ia_parse_data_iso_flex(texto: str) -> Optional[str]:
    valor = str(texto or "").strip()
    if not valor:
        return None
    valor = valor.replace(".", "/").replace("-", "/")
    partes = valor.split("/")
    if len(partes) != 3:
        return None
    try:
        if len(partes[0]) == 4:
            ano = int(partes[0])
            mes = int(partes[1])
            dia = int(partes[2])
        else:
            dia = int(partes[0])
            mes = int(partes[1])
            ano = int(partes[2])
        return dt.date(ano, mes, dia).isoformat()
    except Exception:
        return None


def _ia_normalizar_data_iso_chat(valor: str) -> str:
    texto = str(valor or "").strip()
    if not texto:
        return ""
    try:
        if re.match(r"^\d{4}-\d{2}-\d{2}$", texto):
            dt.date.fromisoformat(texto)
            return texto
        if re.match(r"^\d{4}-\d{2}-\d{2}[ T].*$", texto):
            base = texto[:10]
            dt.date.fromisoformat(base)
            return base
    except Exception:
        pass
    try:
        return _ia_parse_data_iso_flex(texto) or ""
    except Exception:
        return ""


def _ia_normalizar_periodo_chat(data_inicio: str, data_fim: str) -> tuple[str, str]:
    ini = _ia_normalizar_data_iso_chat(data_inicio)
    fim = _ia_normalizar_data_iso_chat(data_fim)
    if ini and not fim:
        fim = ini
    if fim and not ini:
        ini = fim
    if ini and fim and ini > fim:
        ini, fim = fim, ini
    return ini, fim


def _ia_ultimo_dia_mes(ano: int, mes: int) -> dt.date:
    prox = dt.date(ano + 1, 1, 1) if mes == 12 else dt.date(ano, mes + 1, 1)
    return prox - dt.timedelta(days=1)


def _ia_subtrair_meses(data_ref: dt.date, meses: int) -> dt.date:
    ano = data_ref.year
    mes = data_ref.month - int(meses or 0)
    while mes <= 0:
        mes += 12
        ano -= 1
    return dt.date(ano, mes, 1)


def _ia_periodo_do_mes(mes_ano: str) -> tuple[str, str]:
    txt = str(mes_ano or "").strip()
    if not re.match(r"^\d{4}-\d{2}$", txt):
        return "", ""
    ano = int(txt[:4])
    mes = int(txt[5:7])
    if mes < 1 or mes > 12:
        return "", ""
    ini = dt.date(ano, mes, 1)
    fim = _ia_ultimo_dia_mes(ano, mes)
    return ini.isoformat(), fim.isoformat()

def _ia_chat_pede_consulta_vendas(mensagem: str) -> bool:
    texto = _normalizar_texto(mensagem or "")
    if not texto:
        return False
    gatilhos = (
        "VENDA", "VENDEU", "VENDEMOS", "VENDERAM", "VENDIDO", "VENDIDA", "VENDIDOS", "VENDIDAS",
        "FATURAMENTO", "FATUROU", "RECEITA", "UNIDADES", "QTD", "QUANTIDADE", "TOP", "LIDER"
    )
    return any(gatilho in texto for gatilho in gatilhos)


def _ia_chat_pede_consulta_produto(mensagem: str) -> bool:
    texto = _normalizar_texto(mensagem or "")
    if not texto:
        return False
    gatilhos = (
        "SKU", "PRODUTO", "ESTOQUE", "SALDO", "CADASTRO", "MARCA", "CATEGORIA", "PRECO", "PREÃƒâ€¡O", "CUSTO"
    )
    return any(gatilho in texto for gatilho in gatilhos)


def _ia_chat_pede_consulta_estoque(mensagem: str) -> bool:
    texto = _normalizar_texto(mensagem or "")
    return any(gatilho in texto for gatilho in ("ESTOQUE", "SALDO", "DISPONIVEL", "DISPONÃƒÂVEL", "FULL"))


def _ia_chat_pede_consulta_margem(mensagem: str) -> bool:
    texto = _normalizar_texto(mensagem or "")
    return any(gatilho in texto for gatilho in ("MARGEM", "LUCRO", "MARGEM DE CONTRIBUICAO", "MARGEM DE CONTRIBUIÃƒâ€¡ÃƒÆ’O"))


def _ia_chat_pede_consulta_devolucoes(mensagem: str) -> bool:
    texto = _normalizar_texto(mensagem or "")
    return any(gatilho in texto for gatilho in ("DEVOLUCAO", "DEVOLUÃƒâ€¡Ãƒâ€¢ES", "DEVOLUCOES", "DEVOLVIDO", "DEVOLVERAM"))


def _ia_chat_pede_status_integracoes(mensagem: str) -> bool:
    texto = _normalizar_texto(mensagem or "")
    if not texto:
        return False
    return (
        any(g in texto for g in ("INTEGRACAO", "INTEGRACOES", "API", "APIS", "CONECTAD", "TOKEN", "OAUTH"))
        and any(g in texto for g in ("MERCADO LIVRE", "MERCADOLIVRE", "ML", "BLING"))
    )


def _ia_chat_pede_consulta_mercado_livre(mensagem: str) -> bool:
    texto = _normalizar_texto(mensagem or "")
    if not texto:
        return False
    if re.search(r"\bMLB[\s_-]*\d{5,}\b", str(mensagem or ""), flags=re.IGNORECASE):
        return True
    gatilhos_ml = ("MERCADO LIVRE", "MERCADOLIVRE", "ANUNCIO", "ANUNCIOS", "MLB", "SELLER SKU", "SELLER_SKU")
    gatilhos_consulta = ("CONSULTE", "CONSULTAR", "BUSQUE", "BUSCAR", "LISTE", "LISTAR", "MOSTRE", "VERIFIQUE", "PRECO", "ESTOQUE", "STATUS", "DESCRICAO", "SKU")
    return any(g in texto for g in gatilhos_ml) and any(g in texto for g in gatilhos_consulta)


def _ia_chat_pede_consulta_bling(mensagem: str) -> bool:
    texto = _normalizar_texto(mensagem or "")
    if not texto:
        return False
    gatilhos_bling = ("BLING", "ID BLING", "PRODUTO BLING", "NCM", "CEST")
    gatilhos_consulta = ("CONSULTE", "CONSULTAR", "BUSQUE", "BUSCAR", "LISTE", "LISTAR", "MOSTRE", "VERIFIQUE", "PRECO", "ESTOQUE", "SALDO", "SKU", "CADASTRO")
    return any(g in texto for g in gatilhos_bling) and any(g in texto for g in gatilhos_consulta)


def _ia_chat_pede_vendas_por_loja_virtual(mensagem: str) -> bool:
    texto = _normalizar_texto(mensagem or "")
    if not texto:
        return False
    gatilhos = (
        "LOJA VIRTUAL",
        "LOJAS VIRTUAIS",
        "UNIDADE DE NEGOCIO",
        "UNIDADE NEGOCIO",
        "CANAL",
        "CANAIS",
        "MARKETPLACE",
    )
    if any(gatilho in texto for gatilho in gatilhos):
        return True
    return "POR LOJA" in texto


def _ia_chat_pede_recorte_mensal(mensagem: str) -> bool:
    texto = _normalizar_texto(mensagem or "")
    if not texto:
        return False
    if any(gatilho in texto for gatilho in ("MENSAL", "POR MES", "MES A MES")):
        return True
    return bool(re.search(
        r"\b(MES|JANEIRO|JAN|FEVEREIRO|FEV|MARCO|MAR|ABRIL|ABR|MAIO|MAI|JUNHO|JUN|JULHO|JUL|AGOSTO|AGO|SETEMBRO|SET|OUTUBRO|OUT|NOVEMBRO|NOV|DEZEMBRO|DEZ)\b",
        texto,
    ))


def _ia_chat_extrair_limite_top(mensagem: str, padrao: int = 5, maximo: int = 50) -> int:
    texto = _normalizar_texto(mensagem or "")
    limite = int(padrao or 5)
    for padrao_regex in (
        r"\bTOP\s*(\d{1,3})\b",
        r"\b(\d{1,3})\s*(?:SKUS?|SKU|PRODUTOS?|ITENS?)\b",
    ):
        match = re.search(padrao_regex, texto)
        if match:
            try:
                limite = int(match.group(1))
                break
            except Exception:
                pass
    return max(1, min(limite, int(maximo or 50)))


def _ia_chat_pede_comparativo_periodo(mensagem: str) -> bool:
    texto = _normalizar_texto(mensagem or "")
    return any(gatilho in texto for gatilho in ("COMPARE", "COMPARA", "COMPARATIVO", "VERSUS", " VS ", "DIFERENCA", "CRESCIMENTO", "QUEDA"))


def _ia_chat_pede_ticket_medio(mensagem: str) -> bool:
    texto = _normalizar_texto(mensagem or "")
    return "TICKET" in texto


def _ia_chat_pede_taxa_devolucao(mensagem: str) -> bool:
    texto = _normalizar_texto(mensagem or "")
    return ("TAXA" in texto and "DEVOL" in texto) or ("PERCENTUAL" in texto and "DEVOL" in texto)


def _ia_chat_pede_serie_temporal(mensagem: str) -> bool:
    texto = _normalizar_texto(mensagem or "")
    if any(gatilho in texto for gatilho in ("TENDENCIA", "SERIE", "POR DIA", "DIARIO", "DIARIA", "GRAFICO")):
        return True
    return re.search(r"\bEVOLUCAO\b", texto) is not None


def _ia_chat_pede_anomalia(mensagem: str) -> bool:
    texto = _normalizar_texto(mensagem or "")
    return any(gatilho in texto for gatilho in ("ANOMALIA", "PICO", "FORA DO PADRAO", "ALERTA", "QUEDA BRUSCA", "OSCILACAO"))


def _ia_chat_pede_lucro_periodo(mensagem: str) -> bool:
    texto = _normalizar_texto(mensagem or "")
    return any(gatilho in texto for gatilho in ("LUCRO", "RENTABILIDADE", "MARGEM REAL", "MARGEM LIQUIDA", "MARGEM LÃƒÂQUIDA", "RESULTADO"))


def _ia_chat_pede_previsao_ruptura_estoque(mensagem: str) -> bool:
    texto = _normalizar_texto(mensagem or "")
    gatilhos = (
        "PREVEJA",
        "PREVER",
        "PREVISAO",
        "PREVISAO",
        "RUPTURA",
        "ACABAR",
        "ACABA",
        "ESGOTAR",
        "ESGOTA",
        "DIAS PARA ACABAR",
        "QUANDO ACABA",
        "QUANDO VAI ACABAR",
        "ESTOQUE VAI ACABAR",
    )
    return ("ESTOQUE" in texto) and any(g in texto for g in gatilhos)


def _ia_chat_pede_dias_sem_venda(mensagem: str) -> bool:
    texto = _normalizar_texto(mensagem or "")
    gatilhos = (
        "DIAS SEM VENDER",
        "NAO VENDE",
        "NÃƒO VENDE",
        "SEM VENDER",
        "TEMPO SEM VENDER",
        "ULTIMA VENDA",
        "ÃƒÅ¡LTIMA VENDA",
    )
    return any(g in texto for g in gatilhos)


def _ia_chat_pede_top_dias_sem_venda(mensagem: str) -> bool:
    texto = _normalizar_texto(mensagem or "")
    gatilhos = (
        "SKUS SEM VENDER",
        "TOP SEM VENDER",
        "MAIS TEMPO SEM VENDER",
        "PRODUTOS SEM VENDER",
        "LISTA SEM VENDER",
        "PARARAM DE VENDER",
        "QUE PARARAM",
    )
    return any(g in texto for g in gatilhos)


def _ia_chat_extrair_opcoes_top_dias_sem_venda(mensagem: str) -> dict:
    texto_norm = _normalizar_texto(mensagem or "")

    limite = 20
    padroes_limite = (
        r"\bTOP\s*(\d{1,3})\b",
        r"\b(\d{1,3})\s*(?:SKUS?|ITENS?|PRODUTOS?)\b",
    )
    for padrao in padroes_limite:
        m = re.search(padrao, texto_norm)
        if m:
            try:
                limite = max(1, min(int(m.group(1)), 200))
                break
            except Exception:
                pass

    apenas_com_estoque = any(
        chave in texto_norm
        for chave in (
            "COM ESTOQUE",
            "EM ESTOQUE",
            "ESTOQUE POSITIVO",
            "SO COM ESTOQUE",
            "SOMENTE COM ESTOQUE",
        )
    )
    apenas_ja_vendidos = any(
        chave in texto_norm
        for chave in (
            "JA VENDERAM",
            "JÃƒÂ VENDERAM",
            "QUE JA VENDERAM",
            "QUE JÃƒÂ VENDERAM",
            "PARARAM DE VENDER",
            "QUE PARARAM",
            "APENAS COM HISTORICO",
            "SOMENTE COM HISTORICO",
            "SEM NUNCA VENDEU",
            "EXCETO NUNCA",
        )
    )

    return {
        "limite": limite,
        "apenas_com_estoque": apenas_com_estoque,
        "apenas_ja_vendidos": apenas_ja_vendidos,
    }


def _ia_chat_pede_info_cadastro_produto(mensagem: str) -> bool:
    texto = _normalizar_texto(mensagem or "")
    gatilhos = ("CADASTRO", "DESCRICAO", "DESCRIÃƒâ€¡ÃƒÆ’O", "IMAGEM", "FOTO", "FICHA", "DADOS DO PRODUTO")
    return any(g in texto for g in gatilhos)


def _ia_chat_pede_imagem_produto(mensagem: str) -> bool:
    texto = _normalizar_texto(mensagem or "")
    if not texto:
        return False
    gatilhos_imagem = ("IMAGEM", "FOTO", "IMG", "FIGURA", "MOSTRA", "MOSTRE", "MANDA", "ENVIA", "ENVIE")
    gatilhos_produto = ("SKU", "PRODUTO", "ITEM", "PECA", "PEÃƒÆ’Ã¢â‚¬Â¡A")
    return any(g in texto for g in gatilhos_imagem) and any(g in texto for g in gatilhos_produto)


def _ia_chat_pede_geracao_imagem(mensagem: str) -> bool:
    texto = _normalizar_texto(mensagem or "")
    if not texto:
        return False
    gatilhos_explicitos = (
        "GERE UMA IMAGEM", "GERAR UMA IMAGEM", "GERA UMA IMAGEM", "CRIE UMA IMAGEM", "CRIAR UMA IMAGEM",
        "FACA UMA IMAGEM", "FA?A UMA IMAGEM", "GERE UMA FOTO", "GERAR UMA FOTO", "CRIE UMA FOTO",
        "CRIAR UMA FOTO", "NOVA IMAGEM", "IMAGEM NOVA", "GERAR ARTE", "GERE ARTE", "CRIE ARTE",
        "CRIAR ARTE", "GERAR RENDER", "GERE RENDER", "CRIE RENDER", "CRIAR RENDER",
    )
    if any(g in texto for g in gatilhos_explicitos):
        return True
    return bool(re.search(r"\b(GERE|GERAR|GERA|CRIE|CRIAR|FACA|FA?A)\b.{0,60}\b(IMAGEM|FOTO|ARTE|RENDER)\b", texto))


def _ia_chat_pede_noticias(mensagem: str) -> bool:
    texto = _normalizar_texto(mensagem or "")
    if not texto:
        return False
    gatilhos = (
        "NOTICIA", "NOTICIAS", "NOTÃƒÂCIA", "NOTÃƒÂCIAS", "MANCHETES", "ULTIMAS NOTICIAS",
        "ÃƒÅ¡LTIMAS NOTÃƒÂCIAS", "DO DIA", "DE HOJE", "HOJE", "AGORA", "RECENTES", "ATUALIDADES",
    )
    return any(gatilho in texto for gatilho in gatilhos)


def _ia_chat_pede_analise_especialista_vendas(
    mensagem: str,
    page: Optional[str] = None,
    context: Optional[dict] = None,
) -> bool:
    texto = _normalizar_texto(mensagem or "")
    if not texto:
        return False

    gatilhos_analise = (
        "ANALISE",
        "ANALISAR",
        "QUAL ANALISE",
        "QUE ANALISE",
        "ME DE UMA ANALISE",
        "FAZ UMA ANALISE",
        "FACA UMA ANALISE",
        "ANALISE DAS VENDAS",
        "DIAGNOSTICO",
        "RAIO X",
        "RELATORIO",
        "PARECER",
        "ESTRATEGIA",
    )
    gatilhos_comerciais = (
        "VENDA",
        "VENDAS",
        "ESTOQUE",
        "FATURAMENTO",
        "DEVOLUCAO",
        "DEVOLUCOES",
        "SKU",
        "MARGEM",
        "GIRO",
        "CURVA ABC",
        "REPOSICAO",
        "LEAD TIME",
        "MERCADO LIVRE",
        "SHOPEE",
        "MARKETPLACE",
        "CATALOGO",
        "MIX",
    )

    page_norm = _normalizar_texto(page or "")
    contexto = context if isinstance(context, dict) else {}
    tem_contexto_comercial = (
        any(chave in page_norm for chave in ("VENDAS", "ESTOQUE", "DASHBOARD", "ASSISTENTE IA"))
        or any(str(contexto.get(k) or "").strip() for k in ("data_inicio", "data_fim", "loja", "periodo", "periodo_label"))
    )

    if any(g in texto for g in gatilhos_analise) and any(g in texto for g in gatilhos_comerciais):
        return True

    if any(g in texto for g in gatilhos_analise) and tem_contexto_comercial:
        return True

    # TambÃƒÂ©m ativa para pedidos explÃƒÂ­citos de especialista, mesmo sem a palavra "analise".
    if ("ESPECIALISTA" in texto or "CONSULTOR" in texto) and any(g in texto for g in gatilhos_comerciais):
        return True

    if ("ESPECIALISTA" in texto or "CONSULTOR" in texto) and tem_contexto_comercial:
        return True

    return False

def _ia_chat_resumo_historico(history: Optional[list[dict]], limite: int = 10) -> list[dict]:
    resumo = []
    for item in (history or []):
        role = str((item or {}).get("role") or "user").strip().lower()
        if role not in ("user", "assistant"):
            role = "user"
        content = str((item or {}).get("content") or "").strip()
        if not content:
            continue
        resumo.append({
            "role": role,
            "content": content[:1200],
        })
    if limite > 0:
        return resumo[-limite:]
    return resumo


def _ia_chat_mensagem_contextual(payload: IAChatRequest) -> str:
    mensagem = str(payload.message or "").strip()
    resumo = _ia_chat_resumo_historico(payload.history, limite=6)
    if not resumo:
        return mensagem
    linhas = []
    for item in resumo:
        prefixo = "U" if item.get("role") == "user" else "A"
        linhas.append(f"{prefixo}: {str(item.get('content') or '').strip()}")
    bloco = "\n".join(linhas)
    if not bloco:
        return mensagem
    return f"{mensagem}\n\nContexto recente da conversa:\n{bloco}".strip()


def _ia_chat_normalizar_anexos(payload: IAChatRequest) -> list[dict]:
    anexos_norm = []
    anexos_raw = payload.attachments or []
    for item in anexos_raw[:4]:
        try:
            nome = str(getattr(item, "name", "") or "anexo").strip()[:120] or "anexo"
            mime = str(getattr(item, "mime_type", "") or "application/octet-stream").strip().lower()[:100]
            b64 = str(getattr(item, "data_base64", "") or "").strip()
            if "," in b64 and b64.lower().startswith("data:"):
                b64 = b64.split(",", 1)[1].strip()
            if not b64:
                continue
            conteudo = base64.b64decode(b64, validate=True)
            if not conteudo:
                continue
            if len(conteudo) > 5 * 1024 * 1024:
                logger.warning(f"[IA] Anexo ignorado por tamanho (>5MB): {nome}")
                continue
            anexos_norm.append({
                "name": nome,
                "mime_type": mime,
                "data_base64": b64,
                "bytes": conteudo,
            })
        except Exception:
            logger.warning("[IA] Falha ao normalizar anexo recebido.")
            continue
    return anexos_norm


def _ia_chat_extrair_texto_anexo(anexo: dict) -> str:
    mime = str(anexo.get("mime_type") or "").lower()
    nome = str(anexo.get("name") or "anexo")
    conteudo = anexo.get("bytes") or b""
    if not conteudo:
        return ""

    mimetypes_texto = {
        "application/json",
        "application/xml",
        "text/csv",
        "application/csv",
        "application/x-csv",
    }
    extensoes_texto = (".txt", ".csv", ".json", ".xml", ".md", ".log")
    is_texto = mime.startswith("text/") or mime in mimetypes_texto or nome.lower().endswith(extensoes_texto)

    if is_texto:
        for enc in ("utf-8", "latin-1"):
            try:
                texto = conteudo.decode(enc, errors="strict").strip()
                if texto:
                    return texto[:12000]
            except Exception:
                continue
        return conteudo.decode("utf-8", errors="ignore").strip()[:12000]

    if mime == "application/pdf" or nome.lower().endswith(".pdf"):
        try:
            pdf = fitz.open(stream=conteudo, filetype="pdf")
            partes = []
            max_paginas = min(8, len(pdf))
            for idx in range(max_paginas):
                pagina = pdf[idx]
                txt = (pagina.get_text("text") or "").strip()
                if txt:
                    partes.append(txt)
                if sum(len(p) for p in partes) > 12000:
                    break
            pdf.close()
            return "\n\n".join(partes)[:12000]
        except Exception:
            logger.warning(f"[IA] Nao foi possivel extrair texto de PDF: {nome}")
            return ""

    return ""

# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# IA Ã¢â‚¬â€ PersistÃƒÂªncia de Conversas
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def _ia_chat_tem_imagem(anexos: Optional[list[dict]] = None) -> bool:
    for anexo in (anexos or []):
        mime = str(anexo.get("mime_type") or "").lower()
        if mime.startswith("image/"):
            return True
    return False


def _ia_contexto_desativa_recursos_chat(contexto: Optional[dict]) -> bool:
    if not isinstance(contexto, dict):
        return False
    if bool(contexto.get("desativar_recursos_chat")):
        return True
    if bool(contexto.get("desativar_busca_web_chat")):
        return True
    tipo = str(contexto.get("tipo") or contexto.get("origem_ia") or "").strip().lower()
    return tipo in {"agente_cloud_perguntas_ml_sem_chat", "mercado_livre_perguntas_sem_chat"}


def _ia_chat_deve_anexar_vendas_db_contexto(mensagem: str) -> bool:
    texto = _normalizar_texto(mensagem or "")
    if not texto:
        return False
    termos = (
        "TOP", "RANKING", "MAIS VENDEU", "MAIS VENDIDO", "LIDER",
        "LISTE", "LISTAR", "TODOS OS SKU", "TODOS OS SKUS",
        "SKUS VENDIDOS", "PRODUTOS VENDIDOS", "CURVA ABC",
    )
    return any(termo in texto for termo in termos)


def _ia_chat_bloco_prompt_analise_especialista(
    mensagem: str,
    page: Optional[str] = None,
    context: Optional[dict] = None,
) -> str:
    if not _ia_chat_pede_analise_especialista_vendas(mensagem, page, context):
        return ""

    return (
        "\n\nModo de atuaÃƒÂ§ÃƒÂ£o para esta resposta: aja como especialista sÃƒÂªnior em operaÃƒÂ§ÃƒÂµes de e-commerce automotivo "
        "(foco em Mercado Livre, Shopee e logÃƒÂ­stica China-Brasil), com objetivo de otimizar capital de giro e margem. "
        "Estruture a analise de forma objetiva e executiva, priorizando: "
        "1) Giro de estoque e Curva ABC (itens A/B/C e impacto no caixa), "
        "2) ReposiÃƒÂ§ÃƒÂ£o e lead time de importaÃƒÂ§ÃƒÂ£o (risco de ruptura e ponto de pedido sugerido), "
        "3) Oportunidades de mix/cross-sell (produtos complementares e aÃƒÂ§ÃƒÂµes prÃƒÂ¡ticas). "
        "Sempre baseie a analise primeiro nos dados reais disponÃƒÂ­veis no backend/contexto; "
        "quando faltar dado essencial, explicite o que falta e proponha a prÃƒÂ³xima pergunta objetiva. "
        "Entregue a resposta neste formato fixo e nesta ordem: "
        "DiagnÃƒÂ³stico executivo; Curva ABC (A/B/C com leitura de giro); Risco de ruptura e reposiÃƒÂ§ÃƒÂ£o (incluindo lead time); "
        "Oportunidades de mix/cross-sell; Plano de aÃƒÂ§ÃƒÂ£o 7 dias; Plano de aÃƒÂ§ÃƒÂ£o 30 dias; Impacto esperado. "
        "Em cada seÃƒÂ§ÃƒÂ£o, inclua recomendaÃƒÂ§ÃƒÂµes priorizadas e objetivas com linguagem clara e acionÃƒÂ¡vel."
    )
