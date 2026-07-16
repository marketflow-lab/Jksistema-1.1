"""Conservative PIS/Cofins monophase classification rules.

The classifier deliberately avoids broad four-digit assumptions.  Automotive
parts are evaluated against Annexes I and II of Law 10.485/2002, including the
description-dependent ``Ex`` entries and the exclusion for used products.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any


RULE_VERSION = "2026-07-15"
LAW_10485_URL = "https://www.planalto.gov.br/ccivil_03/leis/2002/l10485compilado.htm"
SPED_TABLE_URL = (
    "https://www.gov.br/sped/pt-br/assuntos/comunicados/efd-contribuicoes/"
    "efd-contribuicoes-atualizacao-da-tabela-4-3-10-codigos-150-151-152-e-153"
)


def normalizar_ncm(valor: Any) -> str:
    codigo = re.sub(r"\D", "", str(valor or ""))
    return codigo if len(codigo) == 8 else ""


def _texto_normalizado(valor: Any) -> str:
    texto = unicodedata.normalize("NFKD", str(valor or ""))
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", texto.lower()).strip()


def _contem(texto: str, *termos: str) -> bool:
    return any(termo in texto for termo in termos)


def _resultado(
    status: str,
    motivo: str,
    *,
    fundamento: str,
    fonte: str = LAW_10485_URL,
    confianca: str = "alta",
    regra: str = "",
) -> dict[str, Any]:
    rotulos = {
        "sim": "Monofásico",
        "nao": "Não Monofásico",
        "revisao": "Revisão necessária",
        "nao_verificado": "Não verificado",
    }
    return {
        "is_monofasico": True if status == "sim" else False if status == "nao" else None,
        "status": status,
        "rotulo": rotulos[status],
        "confianca": confianca,
        "fonte": fonte,
        "fundamento": fundamento,
        "motivo": motivo,
        "regra": regra,
        "versao_regra": RULE_VERSION,
    }


# Annex I entries that apply to the whole listed NCM/subheading.
_ANEXO_I_EXATOS = frozenset(
    {
        "40161010",
        "70071100",
        "70072100",
        "70091000",
        "83012000",
        "83023000",
        "84073390",
        "84073490",
        "84148021",
        "84148022",
        "84212300",
        "84213100",
        "84314100",
        "84314200",
        "84339090",
        "84832000",
        "85123000",
        "85129000",
        "85443000",
        "90292010",
        "90299010",
        "90303921",
        "90318040",
        "91040000",
        "94012000",
    }
)

_ANEXO_I_PREFIXOS = (
    "6813",
    "840820",
    "840991",
    "840999",
    "841330",
    "848310",
    "848330",
    "848340",
    "848350",
    "850520",
    "850710",
    "8511",
    "851220",
    "851240",
    "85272",
    "853910",
    "8706",
    "8707",
    "8708",
    "9032892",
)

# Entries whose legal scope depends on an Ex description or the product use.
_ANEXO_I_EX = frozenset({"40169990", "73201000", "84139100", "84139190", "84818099", "85365090"})
_ANEXO_II_EXATOS = frozenset(
    {
        "84089090",
        "84122110",
        "84122190",
        "84123110",
        "84136019",
        "84148019",
        "84149039",
        "84329000",
        "84811000",
        "84812090",
        "84818092",
        "85011019",
    }
)


_TERMOS_VEICULO = (
    "automot",
    "veiculo",
    "carro",
    "caminhao",
    "onibus",
    "pickup",
    "pick-up",
    "sprinter",
    "ranger",
    "hilux",
    "s10",
    "blazer",
    "focus",
    "fiesta",
    "ecosport",
    "civic",
    "corolla",
    "cruze",
    "tracker",
    "renegade",
    "compass",
    "peugeot",
    "citroen",
    "mercedes",
    "bmw",
    "audi",
    "volkswagen",
    "volvo",
    "ford",
    "chevrolet",
    "hyundai",
    "honda",
    "toyota",
    "mitsubishi",
    "nissan",
    "jeep",
    "dodge",
    "kia",
    "fiat",
)

_TERMOS_MAQUINA = (
    "trator",
    "agricol",
    "colheitadeira",
    "escavadeira",
    "retroescavadeira",
    "carregadeira",
    "motoniveladora",
    "bulldozer",
    "ceifadeira",
    "debulhadora",
)


def _avaliar_ex_anexo_i(ncm: str, texto: str) -> dict[str, Any]:
    fundamento = "Lei 10.485/2002, art. 3º e Anexo I (item Ex)"
    if ncm == "40169990":
        if "tapete" in texto and _contem(texto, "automot", "carro", "veiculo", "onibus", "caminhao"):
            return _resultado("sim", "Tapete automotivo compatível com os Ex 03/05.", fundamento=fundamento, regra="anexo_i_ex")
        if texto and "tapete" not in texto:
            return _resultado("nao", "A descrição não é de tapete automotivo dos Ex 03/05.", fundamento=fundamento, regra="fora_do_ex")
    elif ncm == "73201000":
        if _contem(texto, "mola de folhas", "feixe de mola") and _contem(texto, "onibus", "caminhao"):
            return _resultado("sim", "Descrição compatível com o Ex 01 para ônibus/caminhão.", fundamento=fundamento, regra="anexo_i_ex")
    elif ncm in {"84139100", "84139190"}:
        if _contem(texto, "bomba injetora", "injecao em linha") and _contem(texto, "onibus", "caminhao"):
            return _resultado("sim", "Descrição compatível com o Ex 01 de partes de bomba injetora.", fundamento=fundamento, regra="anexo_i_ex")
    elif ncm == "84818099":
        if "colheitadeira" in texto and _contem(texto, "valvula", "comando pneumatic"):
            return _resultado("sim", "Descrição compatível com o Ex 01 para colheitadeira.", fundamento=fundamento, regra="anexo_i_ex")
        if _contem(texto, "tucho", "valvula") and _contem(texto, "onibus", "caminhao"):
            return _resultado("sim", "Descrição compatível com o Ex 02 para motor de ônibus/caminhão.", fundamento=fundamento, regra="anexo_i_ex")
        if texto and not _contem(texto, "colheitadeira", "onibus", "caminhao", "tucho"):
            return _resultado("nao", "A descrição não atende aos Ex 01/02 deste NCM.", fundamento=fundamento, regra="fora_do_ex")
    elif ncm == "85365090":
        if "24v" in texto and _contem(texto, "onibus", "caminhao") and _contem(texto, "interruptor", "comutador", "chave"):
            return _resultado("sim", "Descrição compatível com o interruptor 24 V do Ex 01.", fundamento=fundamento, regra="anexo_i_ex")
        if texto and not ("24v" in texto and _contem(texto, "onibus", "caminhao")):
            return _resultado("nao", "A descrição não atende ao Ex 01 (interruptor 24 V para ônibus/caminhão).", fundamento=fundamento, regra="fora_do_ex")

    return _resultado(
        "revisao",
        "O NCM possui Ex tarifário; faltam características técnicas para confirmar o enquadramento.",
        fundamento=fundamento,
        confianca="pendente",
        regra="anexo_i_ex",
    )


def _avaliar_anexo_ii(ncm: str, texto: str) -> dict[str, Any]:
    fundamento = "Lei 10.485/2002, art. 3º e Anexo II"
    veiculo = _contem(texto, *_TERMOS_VEICULO)
    maquina = _contem(texto, *_TERMOS_MAQUINA)

    if ncm.startswith("4009"):
        if _contem(texto, "presilha", "trava mangueira", "grampo mangueira"):
            return _resultado("nao", "A descrição é de fixador da mangueira, não de tubo de borracha do item 1.", fundamento=fundamento, regra="fora_anexo_ii")
        if _contem(texto, "mangueira", "tubo") and (veiculo or maquina):
            return _resultado("sim", "Tubo/mangueira com aplicação veicular ou em máquina listada no Anexo II.", fundamento=fundamento, confianca="media", regra="anexo_ii_1")
    elif ncm.startswith("8431"):
        if maquina:
            return _resultado("sim", "Parte identificada para máquina da posição 84.29.", fundamento=fundamento, confianca="media", regra="anexo_ii_2")
        if texto and not maquina:
            return _resultado("nao", "A descrição não identifica parte de máquina da posição 84.29.", fundamento=fundamento, regra="fora_anexo_ii")
    elif ncm == "85011019":
        if _contem(texto, "vidro eletrico", "window lift", "elevador de vidro") and veiculo:
            return _resultado("sim", "Motor de corrente contínua identificado para acionamento de vidro veicular.", fundamento=fundamento, regra="anexo_ii_15")
        if _contem(texto, "wiper", "limpador"):
            return _resultado("nao", "Motor de limpador não atende ao item 15, restrito ao acionamento de vidros.", fundamento=fundamento, regra="fora_anexo_ii")
    elif ncm == "84818092":
        if "valvula" in texto and "solenoid" in texto and (veiculo or maquina):
            return _resultado("sim", "Válvula solenoide com aplicação prevista no item 13.", fundamento=fundamento, confianca="media", regra="anexo_ii_13")
        if texto and "valvula" not in texto:
            return _resultado("nao", "A descrição não identifica válvula solenoide.", fundamento=fundamento, regra="fora_anexo_ii")
    elif ncm == "84811000":
        if _contem(texto, "redutora de pressao", "reguladora de pressao") and (veiculo or maquina):
            return _resultado("sim", "Válvula redutora de pressão com aplicação prevista no item 11.", fundamento=fundamento, confianca="media", regra="anexo_ii_11")
    elif ncm == "84812090":
        if _contem(texto, "oleo-hidraulic", "hidraulic", "pneumatic") and maquina:
            return _resultado("sim", "Válvula para transmissão hidráulica/pneumática de máquina listada.", fundamento=fundamento, confianca="media", regra="anexo_ii_12")
        if veiculo and not maquina:
            return _resultado("nao", "O item 12 abrange máquinas específicas; a descrição indica aplicação veicular fora desse item.", fundamento=fundamento, regra="fora_anexo_ii")
    elif ncm == "84123110" and "hidraulic" in texto:
        return _resultado("nao", "O item 6 exige cilindro pneumático; a descrição informa cilindro hidráulico.", fundamento=fundamento, regra="fora_anexo_ii")

    return _resultado(
        "revisao",
        "O Anexo II exige comprovação da aplicação do produto em veículo ou máquina específica.",
        fundamento=fundamento,
        confianca="pendente",
        regra="anexo_ii_condicional",
    )


def avaliar_monofasico(ncm: Any, descricao: Any = "", *, posicao_cadeia: str = "revendedor") -> dict[str, Any]:
    """Classify a product conservatively for PIS/Cofins monophase treatment."""
    codigo = normalizar_ncm(ncm)
    texto = _texto_normalizado(descricao)
    if not codigo:
        return _resultado(
            "nao_verificado",
            "NCM ausente ou inválido; não é possível concluir com segurança.",
            fundamento="NCM vigente e descrição do produto são obrigatórios",
            fonte=SPED_TABLE_URL,
            confianca="pendente",
            regra="ncm_ausente",
        )

    # Product descriptions often contain phrases such as "pode ser usado" or
    # "nunca usado".  The legal exclusion applies to the item's condition, so
    # inspect only the catalog title (the first segment), not boilerplate.
    titulo = texto.split("|", 1)[0].strip()
    if re.search(r"\b(?:usado|usada|usados|usadas|seminovo|seminova|seminovos|seminovas)\b", titulo):
        return _resultado(
            "nao",
            "Produto identificado como usado; a Lei 10.485/2002 exclui produtos usados.",
            fundamento="Lei 10.485/2002, art. 6º",
            regra="produto_usado",
        )

    if codigo in _ANEXO_I_EX:
        return _avaliar_ex_anexo_i(codigo, texto)

    if codigo.startswith("4009") or codigo in _ANEXO_II_EXATOS or codigo.startswith("8483601"):
        return _avaliar_anexo_ii(codigo, texto)

    if codigo in _ANEXO_I_EXATOS or any(codigo.startswith(prefixo) for prefixo in _ANEXO_I_PREFIXOS):
        return _resultado(
            "sim",
            "NCM abrangido diretamente pelo Anexo I da Lei 10.485/2002.",
            fundamento="Lei 10.485/2002, art. 3º e Anexo I",
            regra="anexo_i_direto",
        )

    if codigo.startswith(("4011", "4013")):
        return _resultado(
            "sim",
            "Pneus novos ou câmaras de ar sujeitos ao regime monofásico.",
            fundamento="Lei 10.485/2002, art. 5º",
            regra="pneus_camaras",
        )

    # Strict non-automotive entries present in the current EFD table.
    if codigo in {"27101259", "27101921", "27111910", "27101911", "38260000", "38249929"}:
        return _resultado("sim", "Combustível relacionado na Tabela 4.3.10.", fundamento="Tabela 4.3.10 da EFD-Contribuições, grupo 100", fonte=SPED_TABLE_URL, regra="combustiveis")
    if codigo.startswith(("3003", "3004")) or codigo in {"30029020", "30029092", "30029099", "30051010", "30066000"}:
        return _resultado("sim", "Produto farmacêutico relacionado na Tabela 4.3.10.", fundamento="Lei 10.147/2000 e Tabela 4.3.10, grupo 200", fonte=SPED_TABLE_URL, regra="farmaceuticos")
    if codigo.startswith(("3303", "3304", "3305", "3306", "3307")) or codigo in {"34011190", "34012010", "96032100"}:
        return _resultado("sim", "Produto de perfumaria ou higiene relacionado na Tabela 4.3.10.", fundamento="Lei 10.147/2000 e Tabela 4.3.10, grupo 200", fonte=SPED_TABLE_URL, regra="perfumaria")

    return _resultado(
        "nao",
        "NCM não consta das hipóteses monofásicas aplicáveis mapeadas nas fontes oficiais.",
        fundamento="Tabela 4.3.10 da EFD-Contribuições e legislação nela referenciada",
        fonte=SPED_TABLE_URL,
        confianca="alta" if posicao_cadeia == "revendedor" else "media",
        regra="fora_das_hipoteses",
    )


__all__ = ["RULE_VERSION", "LAW_10485_URL", "SPED_TABLE_URL", "normalizar_ncm", "avaliar_monofasico"]
