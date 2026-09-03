"""Extracted legacy AI implementation with static dependencies."""

from __future__ import annotations

from .runtime import (
    QuestionCategory,
    _favoritos_normalizar_sem_acentos,
    _normalizar_texto,
    _perguntas_ia_fluxo_pos_venda,
    _perguntas_ia_intencao_agent,
    _perguntas_ia_resposta_final_loja,
    _runtime_post_sale_requires_approval,
    _runtime_public_requires_approval,
    os,
    profile_language_issues,
    re,
    resolve_runtime_adapter,
    unicodedata,
)
from .inputs import (
    _perguntas_ia_categoria_classificada,
)
from .queries import (
    _ia_agent_perguntas_codigo_norm_web,
    _perguntas_ia_v2_perfil_compatibilidade,
)

ML_PERGUNTAS_IA_TERMOS_VEICULO = (
    "corolla", "passat", "peugeot", "fusion", "tiguan", "jetta", "mercedes",
    "c180", "c200", "c250", "c300", "c350", "slk", "hilux", "polo", "fiesta",
    "palio", "tucson", "pajero", "bmw", "320i", "golf", "fox", "gol", "voyage",
    "saveiro", "onix", "civic", "fit", "city", "focus", "ranger", "ecosport",
    "cruze", "s10", "spin", "astra", "vectra", "clio", "sandero", "logan",
    "duster", "compass", "renegade", "toro", "strada", "uno", "mobi", "argo",
    "hb20", "creta", "ix35", "azera", "santa fe", "cerato", "sportage",
    "audi", "volkswagen", "volvo", "toyota", "honda", "hyundai", "kia",
    "chevrolet", "gm", "ford", "fiat", "renault", "citroen", "nissan",
    "mitsubishi", "jeep",
)

ML_PERGUNTAS_IA_PREFIXOS_CODIGO_IGNORADOS = {
    "A", "AS", "O", "OS", "UM", "UMA", "UNS", "UMAS",
    "DE", "DA", "DO", "DAS", "DOS", "NO", "NA", "NOS", "NAS",
    "EM", "PARA", "PRA", "COM", "SEM", "MEU", "MINHA", "ANO", "ANOS",
}

_PERGUNTAS_IA_SAFE_INSUFFICIENT_VIOLATION = "evidence_insufficient_safe_draft_required"
_PERGUNTAS_IA_SELLER_STYLE_PREFIX = "seller_style_"
_PERGUNTAS_IA_SAUDACOES_CURTAS = {
    "ola", "oi", "bom dia", "boa tarde", "boa noite", "tudo bem", "agradecemos o contato",
}

def _ia_agent_perguntas_termos_contexto(texto: str, termos: tuple[str, ...] = ML_PERGUNTAS_IA_TERMOS_VEICULO) -> set[str]:
    texto_norm = _favoritos_normalizar_sem_acentos(texto or "")
    encontrados: set[str] = set()
    if not texto_norm:
        return encontrados
    for termo in termos:
        termo_norm = _favoritos_normalizar_sem_acentos(termo)
        if not termo_norm:
            continue
        padrao = r"(?<![a-z0-9])" + re.escape(termo_norm) + r"(?![a-z0-9])"
        if re.search(padrao, texto_norm):
            encontrados.add(termo_norm)
    return encontrados

def _ia_agent_perguntas_texto_fonte(agent_input: dict) -> str:
    question = agent_input.get("question") if isinstance(agent_input.get("question"), dict) else {}
    item = agent_input.get("item") if isinstance(agent_input.get("item"), dict) else {}
    context = agent_input.get("context") if isinstance(agent_input.get("context"), dict) else {}
    partes = [
        str(question.get("text") or ""),
        str(item.get("title") or ""),
        str(item.get("description") or ""),
        str(context.get("titulo") or ""),
        str(context.get("descricao") or ""),
    ]
    historico = question.get("history") if isinstance(question.get("history"), list) else []
    for evento in historico[-10:]:
        if not isinstance(evento, dict):
            continue
        role = str(evento.get("role") or evento.get("from_role") or "").strip().lower()
        if role in {"seller", "loja", "store"}:
            continue
        partes.append(str(evento.get("text") or ""))
    return "\n".join(partes)

def _ia_agent_perguntas_codigos_modelo(texto: str) -> set[str]:
    texto_norm = _favoritos_normalizar_sem_acentos(texto or "").upper()
    codigos: set[str] = set()
    for match in re.finditer(r"\b[A-Z]{1,6}[\s\-]?\d{2,5}[A-Z]?\b|\b\d{3,4}[A-Z]{1,3}\b", texto_norm):
        bruto = (match.group(0) or "").strip()
        partes = re.match(r"^([A-Z]{1,6})[\s\-]+(\d{2,5})([A-Z]?)$", bruto)
        prefixo = partes.group(1) if partes else ""
        numero = partes.group(2) if partes else ""
        sufixo = partes.group(3) if partes else ""
        if prefixo in ML_PERGUNTAS_IA_PREFIXOS_CODIGO_IGNORADOS:
            if numero and numero.isdigit() and 1900 <= int(numero) <= 2099:
                continue
            codigo = f"{numero}{sufixo}".strip()
        else:
            codigo = re.sub(r"[\s\-]+", "", bruto).strip()
            if prefixo and numero and (sufixo or len(numero) == 3 or int(numero) > 2099):
                codigos.add(f"{numero}{sufixo}".strip())
        if not codigo:
            continue
        if codigo.startswith("MLB") or codigo in {"2022", "2023", "2024", "2025", "2026"}:
            continue
        codigos.add(codigo)
    return codigos

def _ia_agent_perguntas_codigos_modelo_tem_match(codigos_pergunta: set[str], codigos_resposta: set[str]) -> bool:
    for perguntado in codigos_pergunta or set():
        for respondido in codigos_resposta or set():
            if perguntado == respondido:
                return True
            menor, maior = sorted((perguntado, respondido), key=len)
            if len(menor) >= 3 and maior.endswith(menor):
                return True
    return False

def _ia_agent_perguntas_conectores(texto: str) -> set[str]:
    texto_norm = _favoritos_normalizar_sem_acentos(texto or "")
    compacto = re.sub(r"[^a-z0-9]+", "", texto_norm)
    encontrados: set[str] = set()
    if (
        "tipo c" in texto_norm
        or "type c" in texto_norm
        or "usb c" in texto_norm
        or "usb-c" in texto_norm
        or "usbc" in compacto
    ):
        encontrados.add("usb-c/tipo c")
    if "lightning" in texto_norm or "iphone" in texto_norm:
        encontrados.add("lightning/iphone")
    if "micro usb" in texto_norm or "micro-usb" in texto_norm or "microusb" in compacto:
        encontrados.add("micro usb")
    if "v8" in texto_norm and any(sinal in texto_norm for sinal in ("conector", "cabo", "entrada", "usb")):
        encontrados.add("micro usb")
    return encontrados

def _ia_agent_perguntas_resposta_pede_chassi(texto: str) -> bool:
    texto_norm = _favoritos_normalizar_sem_acentos(texto or "")
    if "chassi" not in texto_norm and "vin" not in texto_norm:
        return False
    termos_pedido = (
        "informe", "envie", "mande", "passe", "forneca", "digite", "encaminhe",
        "pode informar", "poderia informar", "favor informar", "preciso",
        "precisamos", "necessario", "necessaria",
    )
    padrao_pedido = r"(?:{})".format("|".join(re.escape(termo) for termo in termos_pedido))
    padrao_chassi = r"(?:chassi|vin)"
    return bool(
        re.search(padrao_pedido + r".{0,90}\b" + padrao_chassi + r"\b", texto_norm)
        or re.search(r"\b" + padrao_chassi + r"\b.{0,90}" + padrao_pedido, texto_norm)
    )

def _ia_agent_perguntas_resposta_pede_foto(texto: str) -> bool:
    texto_norm = _favoritos_normalizar_sem_acentos(texto or "")
    objeto = r"(?:foto|fotos|imagem|imagens|anexo|anexos|arquivo|arquivos|documento|documentos|pdf|video|videos|gravacao|gravacoes)"
    pedido_antes = r"(?:informe|envie|mande|passe|forneca|anexe|encaminhe|compartilhe|adicione|faca\s+upload|pode\s+enviar|poderia\s+enviar|favor\s+enviar)"
    transferencia = r"(?:envie|mande|anexe|encaminhe|compartilhe|adicione|faca\s+upload)"
    for trecho in re.split(r"[.!?;\n]+", texto_norm):
        trecho = trecho.strip()
        if not trecho or not re.search(r"\b" + objeto + r"\b", trecho):
            continue
        negacao = re.search(
            r"\b(?:nao\s+(?:e\s+)?necessari[oa]|nao\s+precisa|nao\s+(?:envie|mande|anexe|encaminhe)|sem\s+necessidade\s+de)\b.{0,70}\b"
            + objeto + r"\b",
            trecho,
        )
        if negacao and len(re.findall(r"\b" + objeto + r"\b", trecho)) == 1:
            continue
        if re.search(r"\b(?:anexe|anexar|faca\s+upload|adicione\s+um\s+anexo)\b", trecho):
            return True
        pedido_do_objeto = re.search(r"\b" + pedido_antes + r"\b.{0,90}\b" + objeto + r"\b", trecho)
        if pedido_do_objeto:
            return True
        referencia_anuncio = re.search(r"\b" + objeto + r"\b\s+(?:que\s+consta[m]?\s+)?(?:do|no|das|nas)\s+anuncio", trecho)
        if referencia_anuncio and len(re.findall(r"\b" + objeto + r"\b", trecho)) == 1:
            continue
        if re.search(r"\b" + objeto + r"\b.{0,60}\b" + transferencia + r"\b", trecho):
            return True
        if re.search(r"\b(?:preciso|precisamos|necessario|necessaria)\b.{0,70}\b" + objeto + r"\b", trecho):
            return True
    return False

def _ia_agent_perguntas_recomenda_mecanico_generico(texto: str) -> bool:
    texto_norm = _favoritos_normalizar_sem_acentos(texto or "")
    if not any(termo in texto_norm for termo in ("mecanico", "oficina", "profissional de confianca")):
        return False
    return any(
        termo in texto_norm
        for termo in ("confirme", "confirmar", "consulte", "consultar", "verifique", "verificar", "recomendamos", "recomendo")
    )

def _ia_agent_perguntas_pede_conector(texto: str) -> bool:
    texto_norm = _favoritos_normalizar_sem_acentos(texto or "")
    if _ia_agent_perguntas_conectores(texto_norm):
        return True
    return any(
        termo in texto_norm
        for termo in (
            "conector", "entrada", "plug", "cabo", "carregador", "carregamento",
            "tipo de ponta", "ponta do cabo", "porta usb", "usb",
        )
    )


def _ia_agent_perguntas_inclui_contato_ou_pagamento_externo(texto: str) -> bool:
    texto_sem_acentos = _favoritos_normalizar_sem_acentos(texto or "")
    if re.search(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", texto, flags=re.IGNORECASE):
        return True
    if re.search(r"\b(?:whatsapp|telegram)\b", texto_sem_acentos):
        return True
    if re.search(
        r"(?<!\d)(?:\+?55[\s.-]?)?(?:\(\d{2}\)|\d{2}[\s.-])"
        r"[\s.-]?(?:9\d{4}|\d{4})[\s.-]?\d{4}(?!\d)",
        texto,
    ):
        return True
    return bool(
        re.search(r"\b(?:pix|deposito|transferencia bancaria)\b", texto_sem_acentos)
        and re.search(
            r"\b(?:pague|pagamento|pagar|chave|envie|transferir|fora do mercado livre)\b",
            texto_sem_acentos,
        )
    )


def _ia_agent_perguntas_contexto_validacao(agent_input: dict, resposta: str) -> dict:
    texto = str(resposta or "").strip()
    question = agent_input.get("question") if isinstance(agent_input.get("question"), dict) else {}
    item = agent_input.get("item") if isinstance(agent_input.get("item"), dict) else {}
    historico = question.get("history") if isinstance(question.get("history"), list) else []
    textos_comprador = [str(question.get("text") or "")]
    for evento in historico[-10:]:
        if not isinstance(evento, dict):
            continue
        role = str(evento.get("role") or evento.get("from_role") or "").strip().lower()
        if role not in {"seller", "loja", "store"}:
            textos_comprador.append(str(evento.get("text") or ""))
    texto_comprador = " ".join(textos_comprador)
    return {
        "agent_input": agent_input,
        "texto": texto,
        "texto_norm": _normalizar_texto(texto),
        "texto_sem_acentos": _favoritos_normalizar_sem_acentos(texto),
        "question": question,
        "item": item,
        "textos_comprador": textos_comprador,
        "texto_comprador": texto_comprador,
        "pergunta_norm": _normalizar_texto(texto_comprador),
        "pergunta_sem_acentos": _favoritos_normalizar_sem_acentos(texto_comprador),
        "rascunho_atual_norm": _normalizar_texto(str(question.get("current_draft_to_avoid") or "")),
        "loja": str(agent_input.get("store") or agent_input.get("loja") or "").strip(),
        "intent": _perguntas_ia_intencao_agent(agent_input),
        "categoria": _perguntas_ia_categoria_classificada(agent_input),
    }


def _ia_agent_perguntas_violacoes_politica(contexto: dict) -> list[str]:
    texto = contexto["texto"]
    texto_norm = contexto["texto_norm"]
    texto_sem_acentos = contexto["texto_sem_acentos"]
    pergunta_sem_acentos = contexto["pergunta_sem_acentos"]
    question = contexto["question"]
    item = contexto["item"]
    intent = contexto["intent"]
    categoria = contexto["categoria"]
    violacoes: list[str] = []
    if _ia_agent_perguntas_inclui_contato_ou_pagamento_externo(texto):
        violacoes.append("incluiu contato ou pagamento externo ao Mercado Livre")
    if intent.get("fluxo") == "pos_venda":
        termos_compat = (
            "serve", "servi", "compativel", "compatibilidade", "aplicacao", "veiculo informado",
            "mecanico de confianca", "aguardamos sua compra",
        )
        if any(termo in texto_sem_acentos for termo in termos_compat):
            violacoes.append("tratou pos-venda como compatibilidade/venda")
        sinais_defeito = (
            "defeito", "problema", "apagando", "apaga", "nao funciona", "parou", "queimou",
            "mal funcionamento", "fica apagando", "trocar", "troca", "garantia",
        )
        respostas_esperadas = (
            "foto", "fotos", "compra", "pedido", "mensagem", "detalhe da compra",
            "verificar", "ajudar", "atendimento", "problema", "troca", "garantia",
        )
        if any(sinal in pergunta_sem_acentos for sinal in sinais_defeito) and not any(sinal in texto_sem_acentos for sinal in respostas_esperadas):
            violacoes.append("nao tratou o defeito/troca relatado pelo comprador")
    elif _ia_agent_perguntas_resposta_pede_foto(texto):
        violacoes.append("pediu anexo/arquivo em pergunta publica")
    if re.search(r"\bSKU\b", texto, flags=re.IGNORECASE):
        violacoes.append("mencionou SKU/codigo interno")
    seller_sku = str(item.get("seller_sku") or item.get("sku") or "").strip()
    if seller_sku and _ia_agent_perguntas_codigo_norm_web(seller_sku) in _ia_agent_perguntas_codigo_norm_web(texto):
        violacoes.append("mencionou o codigo interno do produto")
    item_id = str(item.get("id") or question.get("item_id") or "").strip()
    if item_id and item_id.upper() in texto.upper():
        violacoes.append("mencionou o ID/link do anuncio atual")
    permalink = str(item.get("permalink") or "").strip().lower()
    if permalink and permalink in texto.lower():
        violacoes.append("incluiu link do proprio anuncio")
    if "produto.mercadolivre.com" in texto.lower() or "mercadolivre.com.br" in texto.lower():
        violacoes.append("incluiu link de Mercado Livre sem necessidade")
    menciona_unidades = bool(re.search(r"\b\d+\s+unidades?\b", texto, flags=re.IGNORECASE))
    menciona_estoque = bool(
        re.search(
            r"\b(?:estoque|saldo|disponiveis?|temos|restam|pronta entrega)\b",
            texto_sem_acentos,
        )
    )
    if (
        (menciona_unidades and (menciona_estoque or not _ia_agent_perguntas_pede_quantidade_kit(contexto)))
        or "UNIDADES DISPONIVEIS" in texto_norm
    ):
        violacoes.append("mencionou quantidade em estoque")
    if "ESTOQUE NA LOJA" in texto_norm or "DISPONIVEIS EM ESTOQUE" in texto_norm:
        violacoes.append("mencionou estoque interno")
    pergunta_pede_preco = any(termo in contexto["pergunta_norm"] for termo in ("PRECO", "VALOR", "CUSTA", "QUANTO"))
    if not pergunta_pede_preco and (re.search(r"\bR\$\s*\d", texto) or "O VALOR E" in texto_norm or "O PRECO E" in texto_norm):
        violacoes.append("respondeu preco sem o comprador perguntar")
    texto_norm_sem_assinatura = re.sub(
        r"EQUIPE\s+.+?\s+AGRADECE\s+(?:(?:O\s+)?SEU\s+CONTATO\.?|PELO\s+CONTATO,\s*PRECISANDO\s+ESTAMOS\s+A\s+DISPOSICAO!)\s*$",
        "",
        texto_norm,
        flags=re.IGNORECASE,
    ).strip()
    if intent.get("fluxo") != "pos_venda":
        violacoes.extend(_perguntas_ia_seller_style_violations(texto))
    loja_norm = _normalizar_texto(contexto["loja"])
    if contexto["loja"] and loja_norm and loja_norm in texto_norm_sem_assinatura:
        violacoes.append("mencionou nome da loja")
    if "ANUNCIO ATIVO" in texto_norm or "ANUNCIO DESSE PRODUTO ESTA ATIVO" in texto_norm:
        violacoes.append("mencionou status do anuncio")
    if "CONFORME O ANUNCIO" in texto_norm or "CONFORME ANUNCIO" in texto_norm:
        violacoes.append("usou expressao proibida: conforme o anuncio")
    if "NAO CONSEGUIMOS CONFIRMAR A COMPATIBILIDADE" in texto_norm:
        violacoes.append("usou expressao proibida sobre nao confirmar compatibilidade")
    if _ia_agent_perguntas_resposta_pede_chassi(texto):
        violacoes.append("pediu chassi em pergunta de compatibilidade")
    if intent.get("fluxo") != "pos_venda" and categoria == QuestionCategory.COMPATIBILITY.value and _ia_agent_perguntas_recomenda_mecanico_generico(texto):
        violacoes.append("recomendou mecanico/oficina genericamente em pergunta publica")
    if intent.get("fluxo") != "pos_venda" and categoria == QuestionCategory.COMPATIBILITY.value:
        perfil = _perguntas_ia_v2_perfil_compatibilidade(contexto["agent_input"])
        language_issues = resolve_runtime_adapter("policies", "compatibility_language_issues", profile_language_issues)
        linguagem = language_issues(texto, perfil.get("target_type")) if perfil.get("target_type") else []
        if linguagem:
            violacoes.append("usou linguagem de outro perfil de compatibilidade: " + ", ".join(linguagem[:3]))
    if re.search(r"COMPAT\w*\s+COM\s+(?:O|A)?\s*(?:BOA|BOM|OLA|OI)", texto_norm):
        violacoes.append("copiou a pergunta inteira como veiculo")
    if "COMPAT" in texto_norm and "COM" in texto_norm and any(t in texto_norm for t in ("ESSA PECA", "ESSA PE??A", "ESSA PE", "MEU CARRO", "MINHA MOTO")):
        violacoes.append("copiou trecho da pergunta como veiculo")
    rascunho = contexto["rascunho_atual_norm"]
    if rascunho and len(rascunho) >= 40:
        texto_compacto = re.sub(r"\s+", " ", texto_norm).strip()
        rascunho_compacto = re.sub(r"\s+", " ", rascunho).strip()
        if texto_compacto == rascunho_compacto or texto_compacto in rascunho_compacto or rascunho_compacto in texto_compacto:
            violacoes.append("repetiu a resposta atual sem corrigir")
    return violacoes


def _ia_agent_perguntas_violacoes_aderencia(contexto: dict) -> list[str]:
    texto = contexto["texto"]
    texto_norm = contexto["texto_norm"]
    texto_sem_acentos = contexto["texto_sem_acentos"]
    categoria = contexto["categoria"]
    pergunta_compatibilidade = categoria == QuestionCategory.COMPATIBILITY.value
    resposta_compatibilidade = any(
        termo in texto_sem_acentos
        for termo in (
            "serve", "compat", "aplicacao", "pode ser compat", "provavelmente", "mecanico",
            "confirmar", "garantir", "da certo", "pode usar", "encaixa", "encaixe", "nao encaixa",
        )
    )
    violacoes: list[str] = []
    if resposta_compatibilidade and not pergunta_compatibilidade and categoria != QuestionCategory.OTHER_PRODUCT.value:
        violacoes.append("respondeu compatibilidade sem a pergunta pedir")
    if pergunta_compatibilidade and not resposta_compatibilidade:
        violacoes.append("nao respondeu a pergunta de compatibilidade")
    termos_resposta = _ia_agent_perguntas_termos_contexto(texto)
    if termos_resposta:
        termos_fonte = _ia_agent_perguntas_termos_contexto(_ia_agent_perguntas_texto_fonte(contexto["agent_input"]))
        termos_fora = sorted(termos_resposta - termos_fonte)
        if termos_fora:
            violacoes.append("mencionou veiculo/produto fora do contexto: " + ", ".join(termos_fora[:4]))
    codigos_pergunta = _ia_agent_perguntas_codigos_modelo(contexto["texto_comprador"])
    codigos_resposta = _ia_agent_perguntas_codigos_modelo(texto)
    if not pergunta_compatibilidade and codigos_pergunta and codigos_resposta and not _ia_agent_perguntas_codigos_modelo_tem_match(codigos_pergunta, codigos_resposta):
        violacoes.append("nao respondeu ao modelo/codigo perguntado: " + ", ".join(sorted(codigos_pergunta)[:4]))
    conectores_pergunta = _ia_agent_perguntas_conectores(contexto["texto_comprador"])
    conectores_resposta = _ia_agent_perguntas_conectores(texto)
    if conectores_pergunta and not (conectores_pergunta & conectores_resposta):
        violacoes.append("nao respondeu ao conector/variacao perguntado: " + ", ".join(sorted(conectores_pergunta)))
    elif conectores_pergunta and conectores_resposta and not (conectores_pergunta & conectores_resposta):
        violacoes.append("respondeu outro conector/variacao")
    elif _ia_agent_perguntas_pede_conector(contexto["texto_comprador"]) and not any(
        termo in texto_sem_acentos for termo in ("conector", "entrada", "plug", "cabo", "usb", "tipo c", "type c", "lightning", "iphone", "micro usb")
    ):
        violacoes.append("nao respondeu a pergunta sobre conector")
    pergunta_quantidade = _ia_agent_perguntas_pede_quantidade_kit(contexto)
    if pergunta_quantidade and not any(
        termo in texto_norm
        for termo in ("PAR", "UNIDADE", "DIREITO", "ESQUERDO", "LADO", "PECA", "PE??A", "DUAS", "2", "VARIACAO", "VARIA????O", "DESCRICAO", "DESCRI????O", "ANUNCIO", "AN??NCIO")
    ):
        violacoes.append("nao respondeu a duvida de quantidade/variacao")
    pergunta_caracteristica = any(
        termo in contexto["pergunta_norm"]
        for termo in ("PLASTICO", "PL??STICO", "ALUMINIO", "ALUM??NIO", "JUNTA", "PARAFUSO", "VEM COM", "ACOMPANHA", "INCLUSO", "INCLUI")
    )
    if pergunta_caracteristica and not any(
        termo in texto_norm
        for termo in ("PLASTICO", "PL??STICO", "ALUMINIO", "ALUM??NIO", "JUNTA", "PARAFUSO", "ACOMPANHA", "INCLUSO", "INCLUI", "VEM COM")
    ):
        violacoes.append("nao respondeu itens/material perguntados")
    return violacoes


def _ia_agent_perguntas_violacoes_resposta(agent_input: dict, resposta: str) -> list[str]:
    texto = str(resposta or "").strip()
    if not texto:
        return []
    contexto = _ia_agent_perguntas_contexto_validacao(agent_input, texto)
    violacoes = _ia_agent_perguntas_violacoes_politica(contexto)
    violacoes.extend(_ia_agent_perguntas_violacoes_aderencia(contexto))
    return list(dict.fromkeys(violacoes))

ML_PERGUNTAS_IA_V2_MODO = "novo_fluxo_perguntas_v2"

ML_POS_VENDA_IA_V2_MODO = "novo_fluxo_pos_venda_v2"

def _perguntas_ia_v2_exigir_aprovacao() -> bool:
    return resolve_runtime_adapter("policies", "public_requires_approval", _runtime_public_requires_approval)()

def _pos_venda_ia_v2_exigir_aprovacao() -> bool:
    return resolve_runtime_adapter("policies", "post_sale_requires_approval", _runtime_post_sale_requires_approval)()


def _ia_agent_perguntas_rascunho_insuficiente_seguro(
    resposta: Any,
    compatibility_analysis: Any,
) -> bool:
    """Validate the model-authored safe draft used after evidence retries."""

    text = str(resposta or "").strip()
    normalized = unicodedata.normalize("NFKD", text)
    normalized = "".join(char for char in normalized if not unicodedata.combining(char)).lower()
    normalized = re.sub(r"\s+", " ", normalized).strip()
    analysis = compatibility_analysis if isinstance(compatibility_analysis, dict) else {}
    if str(analysis.get("decision") or "").strip().lower() != "insufficient":
        return False
    if not text or len(text) > 2000 or text.count("?") > 2:
        return False
    padded = f" {normalized} "
    prohibited_requests = (
        "foto", "imagem", "anexo", "arquivo", "documento", "video", "chassi",
        " vin ", "mecanico", "oficina",
    )
    if any(marker in padded for marker in prohibited_requests):
        return False
    conditional = normalized.replace("se serve", "").replace("se e compativel", "")
    unsupported_assertions = (
        "sim, serve", "sim serve", "serve perfeitamente", "e compativel",
        "nao serve", "nao e compativel", "pode usar", "nao pode usar", "garantimos",
    )
    if any(marker in conditional for marker in unsupported_assertions):
        return False
    request_markers = (
        "informe", "informar", "confirme", "confirmar", "qual ", "quais ",
        "precisamos do", "precisamos da", "envie o codigo", "envie a medida",
    )
    if not any(marker in normalized for marker in request_markers):
        return True
    requested_fields = 0
    for match in re.finditer(
        r"(?:informe|confirme|qual|quais|precisamos d[oa]|envie)\s+([^?.!]+)",
        normalized,
    ):
        parts = [
            part.strip(" ,;:")
            for part in re.split(r"\s*,\s*|\s+e\s+", match.group(1))
            if part.strip(" ,;:")
        ]
        requested_fields += max(1, len(parts))
    return requested_fields <= 2

def _ia_agent_perguntas_exige_rascunho_insuficiente_seguro(
    agent_input: dict,
    compatibility_analysis: Any,
    *,
    post_sale: bool | None = None,
) -> bool:
    analysis = compatibility_analysis if isinstance(compatibility_analysis, dict) else {}
    is_post_sale = _perguntas_ia_fluxo_pos_venda(agent_input) if post_sale is None else bool(post_sale)
    return bool(
        not is_post_sale
        and _perguntas_ia_categoria_classificada(agent_input) == QuestionCategory.COMPATIBILITY.value
        and str(analysis.get("decision") or "").strip().lower() == "insufficient"
    )

def _perguntas_ia_seller_body(resposta: Any) -> str:
    return re.sub(
        r"(?is)\s*Equipe\s+.+?\s+agradece\s+(?:(?:o\s+)?seu\s+contato\.?|pelo\s+contato,\s*Precisando\s+estamos\s+[àa]\s+disposi[cç][ãa]o!)\s*$",
        "",
        str(resposta or ""),
    ).strip()

def _perguntas_ia_seller_sentences(resposta: Any) -> list[str]:
    protected = re.sub(r"(?<=\d)\.(?=\d)", "\x00", str(resposta or ""))
    return [part.replace("\x00", ".").strip() for part in re.split(r"[.!?]+", protected) if part.strip()]

def _perguntas_ia_seller_greeting_only(sentence: Any) -> bool:
    normalized = _favoritos_normalizar_sem_acentos(str(sentence or "")).strip(" ,;:-")
    if normalized in _PERGUNTAS_IA_SAUDACOES_CURTAS:
        return True
    for greeting in ("ola", "oi", "bom dia", "boa tarde", "boa noite"):
        if not normalized.startswith(greeting + " "):
            continue
        remainder = normalized[len(greeting):].strip(" ,;:-")
        decision_terms = (
            "sim", "nao", "serve", "compativel", "aplicacao", "inclui", "acompanha", "tem", "possui",
        )
        return bool(remainder and len(remainder.split()) <= 3 and not any(term in remainder for term in decision_terms))
    return False


def _ia_agent_perguntas_pede_quantidade_kit(contexto: dict) -> bool:
    pergunta = str(contexto.get("pergunta_sem_acentos") or contexto.get("pergunta_norm") or "").lower()
    return bool(
        re.search(r"\b(?:par|unidades?|quantidade)\b", pergunta)
        or re.search(r"\b(?:duas|2)\s+pecas?\b", pergunta)
        or re.search(r"\blado\s+(?:direito|esquerdo)\b", pergunta)
    )


def _perguntas_ia_seller_style_violations(resposta: Any) -> list[str]:
    body = _perguntas_ia_seller_body(resposta)
    normalized = _favoritos_normalizar_sem_acentos(body)
    violations: list[str] = []
    sentences = _perguntas_ia_seller_sentences(body)
    content_sentences = sentences[1:] if sentences and _perguntas_ia_seller_greeting_only(sentences[0]) else sentences
    if len(content_sentences) > 3:
        violations.append("seller_style_too_many_sentences")
    process_terms = (
        "evidencia tecnica",
        "evidencia insuficiente",
        "analise de compatibilidade",
        "validacao humana",
        "revisao humana",
        "interface alvo",
        "target type",
        "compatibility analysis",
        "decision insufficient",
        "schema de resposta",
    )
    if any(term in normalized for term in process_terms):
        violations.append("seller_style_internal_process_language")
    return violations

def _perguntas_ia_compactar_estilo_vendedor(resposta: Any, loja: str) -> str:
    body = _perguntas_ia_seller_body(resposta)
    sentences = _perguntas_ia_seller_sentences(body)
    greeting: list[str] = []
    if sentences and _perguntas_ia_seller_greeting_only(sentences[0]):
        greeting = sentences[:1]
        sentences = sentences[1:]
    process_terms = (
        "evidencia tecnica",
        "evidencia insuficiente",
        "analise de compatibilidade",
        "validacao humana",
        "revisao humana",
        "interface alvo",
        "target type",
        "compatibility analysis",
        "decision insufficient",
        "schema de resposta",
    )
    content_sentences = [
        sentence
        for sentence in sentences
        if not any(term in _favoritos_normalizar_sem_acentos(sentence) for term in process_terms)
    ][:3]
    safe_sentences = [*greeting, *content_sentences]
    if not safe_sentences:
        return ""
    compacted = ". ".join(safe_sentences).strip()
    if compacted and compacted[-1] not in ".!?":
        compacted += "."
    return resolve_runtime_adapter("state", "final_response", _perguntas_ia_resposta_final_loja)(compacted, loja)
