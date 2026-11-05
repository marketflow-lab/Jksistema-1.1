"""Mercado Livre label PDF processing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from io import BytesIO
from unicodedata import combining, normalize

import fitz

from backend.services import etiquetas_marketplaces_context as marketplace_ctx
from backend.services.etiquetas_manifesto import (
    LinhaListaPDF,
    ProdutoListaPDF,
    SeloQuantidadeListaPDF,
    inserir_lista_ordenada,
)
from backend.services.etiquetas_marketplaces_common import (
    desenhar_rodape_sku,
    extrair_chave_acesso,
    gerar_imagem_barcode,
    ordenar_etiquetas,
)


_ID_VENDA_ML = r"\b(?:N[\u00ba\u00b0o]?\s*DA\s*VENDA|ID\s*DA\s*VENDA|VENDA|PEDIDO)\b"
_ID_PACK_ML = r"\b(?:PACK\s*ID|ID\s*DO\s*PACK|ID\s*DO\s*PACOTE|PACOTE)\b"
_ID_VALOR_ML = r"\s*[:#-]?\s*(?P<identificador>[A-Z0-9.\-]{6,})(?![\w.\-])"
_IDS_ML = re.compile(rf"(?:(?P<venda>{_ID_VENDA_ML})|(?P<pacote>{_ID_PACK_ML})){_ID_VALOR_ML}", re.IGNORECASE)
_CODIGO_ENVIO_ML = re.compile(
    r"(?:[A-F0-9]{8}(?:-[A-F0-9]{4}){3}-[A-F0-9]{12}|MEL(?=[A-Z0-9]*[0-9])[A-Z0-9]{6,})",
    re.IGNORECASE,
)
_RODAPE_ML = re.compile(r"^(?:P[ÁA]G(?:INA)?\b|RODAP[ÉE]\b|\d{1,3}\s*(?:/|DE)\s*\d{1,3}$)", re.IGNORECASE)
_SKU_EXPLICITO_ML = re.compile(
    r"\b(?:SELLER\s*SKU|SKU\s*DO\s*VENDEDOR|SKU)\b\s*[:#-]?\s*"
    r"([A-Z0-9_][A-Z0-9_.\-/]{0,59})(?![\w.\-/])",
    re.IGNORECASE,
)
_SKU_SEM_MARCADOR_ML = re.compile(
    r"^[ \t]*([A-Z0-9][A-Z0-9_.\-/]{0,59})[ \t]*(?:\n[ \t]*)?"
    r"(?=(?:QTD|QTY|QUANTIDADE|QUANT)\b\s*[:#-]?\s*\d)",
    re.IGNORECASE | re.MULTILINE,
)
_SKUS_INVALIDOS_ML = {"N/D", "SKU", "QTD", "QTY", "QUANT", "QUANTIDADE"}
_QUANTIDADE_TABELA_ML = re.compile(
    r"^[ \t]*(?:QTD|QTY|QUANTIDADE|QUANT)\b[ \t]*[:#]?[ \t]*(?:\n[ \t]*)?"
    r"([0-9]{1,3}(?:\.[0-9]{3}){1,2}|[0-9]{1,9})[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
_FRASE_LISTA_ML = "DESPACHEM AS SUAS VENDAS O QUANTO ANTES"


@dataclass(frozen=True)
class _EtiquetaMl:
    pagina: int
    ids: tuple[str, ...]
    dados_sku: tuple[str, str] | None
    chave: str | None
    danfe_embutida: bool

    @property
    def pedido(self) -> str | None:
        return self.ids[0] if self.ids else None


def _normalizar_id_ml(valor):
    """Normaliza IDs de venda/pack para facilitar o vínculo entre lista e etiqueta."""
    if not valor:
        return None
    return re.sub(r'[^A-Z0-9]', '', str(valor).upper())


def _extrair_ids_ml(texto):
    """Extrai possíveis IDs de venda/pack em layouts diversos (inclui Flex)."""
    if not texto:
        return []

    padroes = (_ID_VENDA_ML + _ID_VALOR_ML, _ID_PACK_ML + _ID_VALOR_ML)

    ids = []
    texto_up = texto.upper()
    for padrao in padroes:
        for m in re.finditer(padrao, texto_up, flags=re.IGNORECASE):
            valor = _normalizar_id_ml(m.group(1))
            if valor and valor not in ids:
                ids.append(valor)
    return ids


def _extrair_sku_do_segmento_ml(segmento):
    """Extrai SKU de trecho textual da lista do Mercado Livre com fallback para formatos flexíveis."""
    if not segmento:
        return None

    candidatos = re.findall(
        r'SKU\s*[:#-]?\s*([A-Z0-9_\-\./]{1,60})',
        segmento,
        flags=re.IGNORECASE
    )
    if candidatos:
        return candidatos[-1].strip()

    candidatos_genericos = re.findall(
        r'\b([A-Z0-9][A-Z0-9_\-\./]{2,40})\b',
        segmento,
        flags=re.IGNORECASE
    )
    palavras_bloqueadas = {
        "VENDA", "PACK", "ID", "QTD", "QUANT", "QUANTIDADE", "SKU", "ML", "MERCADOLIVRE"
    }
    for c in reversed(candidatos_genericos):
        if c.upper() not in palavras_bloqueadas:
            return c.strip()
    return None


def _extrair_qtd_ml(texto, default="1"):
    """Extrai quantidade em textos do Mercado Livre (QTD/QUANTIDADE/QTY)."""
    if not texto:
        return default
    m = re.search(r'(?:QTD|QTY|QUANTIDADE|QUANT)\s*[:#-]?\s*(\d{1,3})', texto, flags=re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r'\bX\s*(\d{1,3})\b', texto, flags=re.IGNORECASE)
    if m:
        return m.group(1)
    return default


def _extrair_dados_ml_da_pagina(texto_pagina):
    """Fallback para capturar SKU/QTD direto da etiqueta quando a lista falha."""
    if not texto_pagina:
        return None

    sku = None
    candidatos = re.findall(
        r'(?:SELLER\s*SKU|SKU\s*DO\s*VENDEDOR|SKU)\s*[:#-]?\s*([A-Z0-9_\-\./]{1,60})',
        texto_pagina,
        flags=re.IGNORECASE
    )
    if candidatos:
        sku = candidatos[-1].strip()

    if not sku:
        sku = _extrair_sku_do_segmento_ml(texto_pagina)

    if not sku:
        return None

    return (sku, _extrair_qtd_ml(texto_pagina))


def _skus_explicitos_ml(texto):
    skus = {sku.upper(): sku for sku in _SKU_EXPLICITO_ML.findall(texto) if sku.upper() not in _SKUS_INVALIDOS_ML}
    return tuple(skus.values())


def _dados_segmento_manifesto_ml(texto):
    skus = _skus_explicitos_ml(texto)
    if not skus:
        # Em alguns manifestos o código aparece sozinho, antes da quantidade.
        skus = tuple({
            sku.upper(): sku
            for sku in _SKU_SEM_MARCADOR_ML.findall(texto)
            if sku.upper() not in _SKUS_INVALIDOS_ML and any(c.isdigit() or c in "-_/" for c in sku)
        }.values())
    return (skus[0], _extrair_qtd_ml(texto)) if len(skus) == 1 else None


def _selos_quantidade_ml(pagina, faixa, bordas, quantidades, marcas, glifos):
    def caixa_do_caractere(caractere):
        texto = caractere["c"]
        if len(texto) == 1:
            chave = (ord(texto), caractere["origin"])
            if chave in glifos:
                return fitz.Rect(glifos[chave])
        return fitz.Rect(caractere["bbox"])

    flags = fitz.TEXTFLAGS_RAWDICT & ~fitz.TEXT_PRESERVE_IMAGES
    blocos = pagina.get_text("rawdict", clip=faixa, flags=flags)["blocks"]
    linhas = [
        (linha, [caractere for span in linha["spans"] for caractere in span["chars"]])
        for bloco in blocos if bloco["type"] == 0
        for linha in bloco["lines"]
    ]
    encontrados = []
    selos = []
    area = faixa & fitz.Rect(bordas[0], faixa.y0 + 0.5, bordas[1], faixa.y1 - 0.5)
    for linha, caracteres in linhas:
        texto = "".join(c["c"] for c in caracteres)
        quantidade = _QUANTIDADE_TABELA_ML.fullmatch(texto)
        if quantidade is None or linha["dir"] != (1.0, 0.0):
            continue
        valor = int(quantidade[1].replace(".", ""))
        encontrados.append(valor)
        if valor < 2:
            continue

        inicio, fim = quantidade.span(1)
        numero = fitz.Rect()
        for caractere in caracteres[inicio:fim]:
            numero |= caixa_do_caractere(caractere)
        linha_base = caracteres[inicio]["origin"][1]
        tamanho = min(7.4, numero.height)
        if tamanho < 6:
            continue
        texto_selo = f"{quantidade[1]} UNIDADES"
        largura = fitz.get_text_length(texto_selo, fontname="hebo", fontsize=tamanho) + 8
        caixa = fitz.Rect(
            numero.x0 - 0.4, min(numero.y0, linha_base - tamanho),
            numero.x0 - 0.4 + largura, max(numero.y1, linha_base + tamanho * 0.35),
        )
        if not area.contains(caixa) or not caixa.contains(numero):
            continue
        if any(
            caixa.intersects(caixa_do_caractere(c))
            for outra_linha, outros in linhas
            for indice, c in enumerate(outros)
            if not c["c"].isspace() and (outra_linha is not linha or not inicio <= indice < fim)
        ):
            continue
        # Permite o fundo da linha, mas preserva ícones, imagens e traços vizinhos.
        if any(caixa.intersects(marca) and not (tipo == "fill-path" and marca.contains(caixa))
               for tipo, marca in marcas):
            continue
        selos.append(SeloQuantidadeListaPDF(texto_selo, caixa, linha_base, tamanho))

    return tuple(selos) if encontrados == quantidades else ()


def _produto_tabela_ml(texto, sku, bordas, pagina, faixa, marcas, glifos):
    quantidades = [int(valor.replace(".", "")) for valor in _QUANTIDADE_TABELA_ML.findall(texto)]
    ocorrencias = sum(codigo.upper() == sku.upper() for codigo in _SKU_EXPLICITO_ML.findall(texto))
    # Para a lista, o total depende da quantidade impressa de cada ocorrência do SKU.
    total = sum(quantidades) if len(quantidades) == max(1, ocorrencias) and all(quantidades) else None
    selos = (
        _selos_quantidade_ml(pagina, faixa, bordas, quantidades, marcas, glifos)
        if total and max(quantidades) > 1 else ()
    )
    return ProdutoListaPDF(sku, total, bordas, selos)


def _eh_lista_ml(texto):
    texto = texto.upper()
    return (
        _FRASE_LISTA_ML in texto
        or any(titulo in texto for titulo in ("MANIFESTO", "RESUMO DO PEDIDO", "RELATÓRIO DE"))
        or (texto.count("PACK ID") >= 6 and "DESTINATÁRIO" not in texto)
    )


def _cabecalho_tabela_ml(pagina):
    identificacoes = []
    produtos = []
    for palavra in pagina.get_text("words"):
        titulo = "".join(letra for letra in normalize("NFD", palavra[4].casefold()) if not combining(letra))
        # Alguns PDFs do Mercado Livre extraem o título com um "i" duplicado.
        if titulo in {"identificacao", "identifiicacao"}:
            identificacoes.append(fitz.Rect(palavra[:4]))
        elif titulo == "produtos":
            produtos.append(fitz.Rect(palavra[:4]))

    for esquerda in sorted(identificacoes, key=lambda caixa: caixa.y0):
        for direita in produtos:
            sobreposicao = min(esquerda.y1, direita.y1) - max(esquerda.y0, direita.y0)
            if esquerda.x1 < direita.x0 and sobreposicao >= min(esquerda.height, direita.height) / 3:
                return esquerda, direita
    return None


def _divisorias_tabela_ml(pagina, esquerda, direita):
    fim_cabecalho = max(esquerda.y1, direita.y1)
    largura_minima = pagina.rect.width * 0.5
    segmentos = []

    def acrescentar(y, x0, x1):
        if y >= fim_cabecalho and x1 - x0 > 2:
            segmentos.append((y, x0, x1))

    for desenho in pagina.get_drawings():
        for item in desenho["items"]:
            if item[0] == "l":
                a, b = item[1:]
                if abs(a.y - b.y) < 0.5:
                    acrescentar((a.y + b.y) / 2, min(a.x, b.x), max(a.x, b.x))
            elif item[0] == "re":
                rect = item[1]
                if rect.height < 2:
                    acrescentar((rect.y0 + rect.y1) / 2, rect.x0, rect.x1)
                elif rect.y0 <= min(esquerda.y0, direita.y0) and rect.y1 <= fim_cabecalho + 14:
                    acrescentar(rect.y1, rect.x0, rect.x1)

    faixas = []
    for y, x0, x1 in sorted(segmentos):
        if not faixas or y - faixas[-1][0] >= 0.75:
            faixas.append((y, []))
        faixas[-1][1].append((x0, x1))

    limites = [fim_cabecalho]
    bordas = None
    for y, trechos in faixas:
        # A borda horizontal da tabela pode ser desenhada por célula.
        unidos = []
        for x0, x1 in sorted(trechos):
            if unidos and x0 <= unidos[-1][1] + 3:
                unidos[-1] = (unidos[-1][0], max(unidos[-1][1], x1))
            else:
                unidos.append((x0, x1))
        faixa = next((
            (x0, x1) for x0, x1 in unidos
            if x1 - x0 >= largura_minima and x0 <= esquerda.x1 and x1 >= direita.x0
        ), None)
        if y - limites[-1] >= 0.75 and faixa:
            limites.append(y)
            if bordas is None:
                bordas = faixa
    return limites, bordas


def _ids_linha_tabela_ml(identificacao):
    vendas, pacotes = set(), set()
    for match in _IDS_ML.finditer(identificacao):
        identificador = _normalizar_id_ml(match.group("identificador"))
        (vendas if match.group("venda") else pacotes).add(identificador)
    codigos = [linha.strip() for linha in identificacao.splitlines() if linha.strip()]
    envios = [codigo for codigo in codigos if _CODIGO_ENVIO_ML.fullmatch(codigo)]
    if len(vendas) > 1 or len(pacotes) > 1 or len(envios) > 1:
        return None

    ids = _extrair_ids_ml(identificacao)
    if ids and codigos and _CODIGO_ENVIO_ML.fullmatch(codigos[0]):
        ids.append(_normalizar_id_ml(codigos[0]))
    return tuple(dict.fromkeys(ids))


def _base_ultima_linha_ml(pagina, topo):
    faixa = fitz.Rect(pagina.rect.x0, topo, pagina.rect.x1, pagina.rect.y1)
    blocos = pagina.get_text("dict", clip=faixa, flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)["blocks"]
    linhas = [
        (fitz.Rect(linha["bbox"]), "".join(span["text"] for span in linha["spans"]).strip())
        for bloco in blocos if bloco["type"] == 0
        for linha in bloco["lines"]
    ]
    inicio_rodape = min(
        (caixa.y0 for caixa, texto in linhas if caixa.y0 >= pagina.rect.height * 0.75 and _RODAPE_ML.match(texto)),
        default=pagina.rect.y1,
    )
    caixas = [caixa for caixa, texto in linhas if texto and caixa.y0 < inicio_rodape]
    for tipo, coordenadas in pagina.get_bboxlog():
        caixa = fitz.Rect(coordenadas)
        if tipo in {"fill-path", "stroke-path", "fill-image"} and topo <= caixa.y0 < inicio_rodape:
            caixas.append(caixa)

    if not caixas or any(caixa.y1 > inicio_rodape for caixa in caixas):
        return None
    return min(inicio_rodape, max(caixa.y1 for caixa in caixas) + 4)


def _linhas_tabela_ml(pagina, cabecalho):
    esquerda, direita = cabecalho
    limites, bordas = _divisorias_tabela_ml(pagina, esquerda, direita)
    if bordas is None:
        margem = min(max(0, esquerda.x0 - 5), pagina.rect.width * 0.1)
        bordas = margem, pagina.rect.width - margem
    # A segunda coluna pode começar alguns pontos antes do seu título.
    divisao = direita.x0 - 4
    col_esquerda = fitz.Rect(pagina.rect.x0, limites[0], divisao, pagina.rect.y1)
    texto_identificacao = pagina.get_text(clip=col_esquerda)
    ids_tabela = set(_extrair_ids_ml(texto_identificacao))
    if not ids_tabela:
        return ()

    final_sem_divisoria = pagina.get_text(clip=fitz.Rect(col_esquerda.x0, limites[-1], divisao, pagina.rect.y1))
    if _extrair_ids_ml(final_sem_divisoria):
        base = _base_ultima_linha_ml(pagina, limites[-1])
        if base is None:
            return ()
        limites.append(base)

    linhas = []
    ids_recortados = set()
    marcas = [
        (tipo, fitz.Rect(caixa))
        for tipo, caixa in pagina.get_bboxlog()
        if tipo not in {"fill-text", "stroke-text", "ignore-text"}
    ]
    # As caixas de get_text são mais altas que os glifos e se sobrepõem em linhas próximas.
    glifos = {
        (codigo, origem): caixa
        for trecho in pagina.get_texttrace()
        for codigo, _, origem, caixa in trecho["chars"]
    }
    for topo, base in zip(limites, limites[1:]):
        identificacao = pagina.get_text(clip=fitz.Rect(col_esquerda.x0, topo, divisao, base))
        ids = _ids_linha_tabela_ml(identificacao)
        if ids is None:
            return ()
        if not ids:
            continue
        if linhas and topo != linhas[-1][0].base:
            return ()
        faixa_produtos = fitz.Rect(divisao, topo, pagina.rect.x1, base)
        produtos = pagina.get_text(clip=faixa_produtos)
        dados = _dados_segmento_manifesto_ml(produtos)
        produto = (
            _produto_tabela_ml(produtos, dados[0], bordas, pagina, faixa_produtos, marcas, glifos)
            if dados else None
        )
        linhas.append((LinhaListaPDF(ids, topo, base, produto), dados))
        ids_recortados.update(_extrair_ids_ml(identificacao))

    # Um quadro sem divisórias suficientes fica no PDF com sua página inteira.
    return tuple(linhas) if ids_recortados == ids_tabela else ()


def _mapear_manifestos_ml(textos, linhas_tabela=()):
    dados_por_id = {}
    conflitos = set()

    def registrar(identificador, dados):
        if identificador in dados_por_id and dados_por_id[identificador] != dados:
            conflitos.add(identificador)
        dados_por_id[identificador] = dados

    for linha, dados in linhas_tabela:
        if dados:
            for identificador in linha.pedidos:
                registrar(identificador, dados)

    for texto in textos:
        ids = list(_IDS_ML.finditer(texto))
        if not ids:
            continue

        segmentos = [texto[:ids[0].start()]]
        for indice, match in enumerate(ids):
            fim = ids[indice + 1].start() if indice + 1 < len(ids) else len(texto)
            segmentos.append(texto[match.end():fim])

        # A lista pode trazer o SKU antes ou depois do ID. Se os dois
        # extremos têm dados, não há direção inequívoca nessa página.
        if _dados_segmento_manifesto_ml(segmentos[0]):
            if _dados_segmento_manifesto_ml(segmentos[-1]):
                continue
            ids.reverse()
            segmentos.reverse()

        for indice, (match, segmento) in enumerate(zip(ids, segmentos[1:])):
            dados = _dados_segmento_manifesto_ml(segmento)
            if not dados:
                continue

            aliases = [match.group("identificador")]
            if indice > 0 and re.fullmatch(r"[ \t]*(?:\n[ \t]*)?", segmentos[indice]):
                anterior = ids[indice - 1]
                if bool(anterior.group("venda")) != bool(match.group("venda")):
                    aliases.append(anterior.group("identificador"))

            for alias in aliases:
                identificador = _normalizar_id_ml(alias)
                registrar(identificador, dados)

    return {identificador: dados for identificador, dados in dados_por_id.items() if identificador not in conflitos}


def _resolver_pedido_ml(texto, mapa_ids_dados):
    ids = _extrair_ids_ml(texto)
    if not ids and mapa_ids_dados:
        # Alguns layouts imprimem o código sem a legenda da venda. Compare
        # tokens completos para evitar vincular um ID que é prefixo de outro.
        tokens = re.findall(r"\b[A-Z0-9][A-Z0-9.\-]{5,}\b", texto.upper())
        encontrados = {_normalizar_id_ml(token) for token in tokens}.intersection(mapa_ids_dados)
        if len(encontrados) == 1:
            ids = list(encontrados)

    dados_manifesto = {mapa_ids_dados[identificador] for identificador in ids if identificador in mapa_ids_dados}
    dados_sku = next(iter(dados_manifesto)) if len(dados_manifesto) == 1 else None
    skus_etiqueta = _skus_explicitos_ml(texto)
    if len(skus_etiqueta) > 1:
        return ids, None
    if skus_etiqueta:
        sku_etiqueta = skus_etiqueta[0]
        qtd_etiqueta = _extrair_qtd_ml(texto, default=None)
        if qtd_etiqueta is None and dados_sku and dados_sku[0].upper() == sku_etiqueta.upper():
            qtd_etiqueta = dados_sku[1]
        dados_sku = (sku_etiqueta, qtd_etiqueta or "1")

    return ids, dados_sku


def processar_mercado_livre(uploaded_file, *, layout=None, component_collector=None, limite_etiquetas=None):
    progress_bar = marketplace_ctx.st.progress(0, text="Processando arquivo do Mercado Livre...")
    
    file_bytes = uploaded_file.read()
    doc_origem = fitz.open(stream=file_bytes, filetype="pdf")
    doc_etiquetas = fitz.open()
    doc_lista = fitz.open()
    progress_bar.progress(20, text="Mapeando SKUs...")
    textos_paginas = [pagina.get_text() for pagina in doc_origem]
    paginas_lista = {}
    linhas_por_pagina = {}
    paginas_tabela = set()
    dados_tabela = []
    for indice, texto in enumerate(textos_paginas):
        cabecalho = _cabecalho_tabela_ml(doc_origem[indice]) if "produtos" in texto.casefold() else None
        if not _eh_lista_ml(texto) and cabecalho is None:
            continue

        linhas = ()
        if cabecalho:
            paginas_tabela.add(indice)
            linhas = _linhas_tabela_ml(doc_origem[indice], cabecalho)
        if linhas:
            linhas_por_pagina[indice] = tuple(linha for linha, _ in linhas)
            dados_tabela.extend(linhas)
            paginas_lista[indice] = tuple(dict.fromkeys(
                identificador for linha, _ in linhas for identificador in linha.pedidos
            ))
        else:
            ids = _extrair_ids_ml(texto)
            paginas_lista[indice] = tuple(ids) if len(ids) == 1 else ()

    mapa_ids_dados = _mapear_manifestos_ml(
        (textos_paginas[i] for i in paginas_lista if i not in paginas_tabela),
        dados_tabela,
    )

    progress_bar.progress(50, text="Separando e formatando etiquetas...")
    etiquetas = []
    i = 0
    while i < len(textos_paginas):
        texto_pagina = textos_paginas[i]
        texto_atual = texto_pagina.upper()
        if i in paginas_lista:
            i += 1
            continue

        tem_financeiro = any(t in texto_atual for t in ["VALOR TOTAL DA NOTA", "BASE DE CÁLCULO", "VALOR DO ICMS"])
        eh_danfe = "DANFE" in texto_atual or "DOCUMENTO AUXILIAR" in texto_atual or tem_financeiro
        if eh_danfe or ("DECLARAÇÃO DE CONTEÚDO" in texto_atual and "DESTINATÁRIO" in texto_atual and "VALOR TOTAL" in texto_atual):
            i += 1
            continue

        ids_pagina, dados_sku = _resolver_pedido_ml(texto_pagina, mapa_ids_dados)
        chave_embutida = extrair_chave_acesso(texto_pagina) if "CHAVE DE ACESSO" in texto_atual else None
        chave_declaracao = None
        salto = 1
        if i + 1 < len(textos_paginas) and i + 1 not in paginas_lista:
            texto_prox = textos_paginas[i + 1].upper()
            prox_tem_chave = any(t in texto_prox for t in ("CHAVE DE ACESSO", "DANFE", "DECLARAÇÃO"))
            prox_eh_etiqueta = any(t in texto_prox for t in ("DESTINATÁRIO", "DESTINATARIO", "RECEBEDOR"))
            if prox_tem_chave and not prox_eh_etiqueta:
                salto = 2
                ids_declaracao = _extrair_ids_ml(texto_prox)
                if not ids_pagina or not ids_declaracao or set(ids_pagina).intersection(ids_declaracao):
                    chave_declaracao = extrair_chave_acesso(texto_prox)

        etiquetas.append(_EtiquetaMl(
            i, tuple(ids_pagina), dados_sku,
            chave_embutida or chave_declaracao, bool(chave_embutida),
        ))
        i += salto

    etiquetas_ordenadas = ordenar_etiquetas(
        etiquetas,
        sku=lambda etiqueta: etiqueta.dados_sku[0] if etiqueta.dados_sku else None,
        quantidade=lambda etiqueta: etiqueta.dados_sku[1] if etiqueta.dados_sku else None,
        pedido=lambda etiqueta: etiqueta.pedido,
    )
    if paginas_lista:
        posicoes = {}
        for posicao, etiqueta in enumerate(etiquetas_ordenadas):
            for identificador in etiqueta.ids:
                posicoes.setdefault(identificador, posicao)
        inserir_lista_ordenada(
            doc_lista, doc_origem, paginas_lista, posicoes,
            linhas_por_pagina=linhas_por_pagina,
        )

    count_etiquetas = 0
    for etiqueta in etiquetas_ordenadas:
        if limite_etiquetas and count_etiquetas >= limite_etiquetas:
            break

        i, id_correspondente = etiqueta.pagina, etiqueta.pedido
        dados_sku, chave_numeros = etiqueta.dados_sku, etiqueta.chave
        sku_text = f"SKU {dados_sku[0]}  x  {dados_sku[1]}" if dados_sku else "SKU N/D"
        quantidade_sku = dados_sku[1] if dados_sku else None
        count_etiquetas += 1
        nova_pagina = doc_etiquetas.new_page(width=283, height=425)
        if layout is not None:
            from backend.services.etiquetas_layout import renderizar_etiqueta_layout
            renderizar_etiqueta_layout(
                nova_pagina, doc_origem, i, layout=layout, store='Mercado Livre',
                sku_text=sku_text, quantidade_sku=quantidade_sku,
                code=chave_numeros or id_correspondente,
                code_text=chave_numeros or (f"VENDA {id_correspondente}" if id_correspondente else None),
                counter=count_etiquetas, embedded_danfe=etiqueta.danfe_embutida,
                collector=component_collector,
            )
            continue

        # Mantem a escala original; a DANFE usa a margem lateral existente.
        if quantidade_sku is not None and int(quantidade_sku) > 1:
            nova_pagina.insert_font(fontname="hebo")
        nova_pagina.show_pdf_page(fitz.Rect(0, 0, 283, 390), doc_origem, i)
        cor_sku = (0, 0, 0) if dados_sku else (1, 0, 0)
        desenhar_rodape_sku(nova_pagina, sku_text, cor=cor_sku, quantidade=quantidade_sku)

        codigo_barra_base = chave_numeros or id_correspondente
        titulo_codigo = "CHAVE DE ACESSO / VENDA"
        if codigo_barra_base:
            texto_codigo = chave_numeros if chave_numeros else f"VENDA {id_correspondente}"
            tamanho_codigo = 5.5
            cor_codigo = (0, 0, 0)
        else:
            texto_codigo = "CODIGO NAO ENCONTRADO"
            tamanho_codigo = 5
            cor_codigo = (0.5, 0.5, 0.5)

        comprimento_titulo = fitz.get_text_length(titulo_codigo, fontsize=5)
        comprimento_codigo = fitz.get_text_length(texto_codigo, fontsize=tamanho_codigo)
        fim_titulo = (390 - comprimento_titulo - 8 - comprimento_codigo) / 2 + comprimento_titulo
        nova_pagina.insert_text(
            fitz.Point(262.5, fim_titulo), titulo_codigo,
            fontsize=5, color=(0, 0, 0), rotate=90,
        )
        nova_pagina.insert_text(
            fitz.Point(262.5, fim_titulo + 8 + comprimento_codigo), texto_codigo,
            fontsize=tamanho_codigo, color=cor_codigo, rotate=90,
        )
        if codigo_barra_base:
            img_bytes = gerar_imagem_barcode(codigo_barra_base)
            if img_bytes:
                # Mantem amplo o eixo dos modulos; apenas encurta a altura das barras.
                nova_pagina.insert_image(
                    fitz.Rect(264.2, 20, 283, 385), stream=img_bytes,
                    rotate=90, keep_proportion=False,
                )

        num_pag_text = f"{count_etiquetas}"
        tamanho_contador = min(12, 21 / fitz.get_text_length(num_pag_text, fontsize=1))
        nova_pagina.insert_text(
            fitz.Point(260, 13), num_pag_text, fontsize=tamanho_contador, color=(0,0,0),
        )
        
    progress_bar.progress(100, text="Finalizado!")
    progress_bar.empty()

    bytes_etiquetas = None; bytes_lista = None
    if count_etiquetas > 0:
        buffer_et = BytesIO(); doc_etiquetas.save(buffer_et); bytes_etiquetas = buffer_et.getvalue()
    if len(doc_lista) > 0:
        buffer_li = BytesIO(); doc_lista.save(buffer_li); bytes_lista = buffer_li.getvalue()

    doc_etiquetas.close(); doc_lista.close(); doc_origem.close()
    return bytes_etiquetas, count_etiquetas, bytes_lista
