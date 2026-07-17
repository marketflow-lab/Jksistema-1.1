"""Converte os dossies brutos de SKU em arquivos finais limpos em portugues.

Regras centrais:
- o nome sempre fica em portugues do Brasil;
- medidas de embalagem, placeholders e conversoes quebradas sao descartados;
- compatibilidade veicular e agrupada por marca/modelo com anos visiveis;
- fontes, URLs, anuncios brutos e dados de cadastro nao aparecem no arquivo final.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 2
# O SKU permanece no cadastro comercial, mas não deve possuir dossiê publicado.
EXCLUDED_DOSSIER_SKUS = {"395"}

LOGISTICS_TERMS = (
    "embalagem", "vendor", "pacote", "shipping", "envio", "caixa de papelao",
    "caixa de papelão", "plastic bag", "cardboard box", "package size", "package weight",
)
MARKETING_TERMS = (
    "pronta entrega", "envio imediato", "garantia de fabricacao", "garantia de fabricação",
    "antes de confirmar a compra", "qualquer duvida", "qualquer dúvida", "prezado cliente",
    "nossa taxa de respostas", "valor do anuncio", "valor do anúncio", "foto original",
    "produto anunciado pela", "mercado livre", "devolucao sem custos", "devolução sem custos",
    "nao abra uma reclamacao", "não abra uma reclamação", "entre em contato conosco",
    "campo de mensagens", "todo suporte necessario", "todo suporte necessário",
    "antes de comprar", "escolha o modelo desejado", "disponivel no momento",
    "disponível no momento", "contra defeitos de fabricacao", "contra defeitos de fabricação",
    "troque as lampadas aos pares", "troque as lâmpadas aos pares",
)
MEASURE_LABELS = (
    "comprimento", "largura", "altura", "profundidade", "diametro", "diâmetro", "espessura", "dimens",
    "tamanho", "medida", "rosca", "encaixe", "bocal", "haste", "cabeca", "cabeça",
    "furo", "eixo", "entrada", "saida", "saída", "distancia", "distância", "bcd", "dn",
    "peso liquido", "peso líquido", "deslocamento", "offset", "corrente", "cabo",
)
MEASURE_UNIT_RE = re.compile(
    r"(?i)(?:\d+(?:[.,]\d+)?\s*(?:mm(?:2|²)?|cm(?:2|²)?|m|kg|g|pol(?:egadas?)?|\")\b|"
    r"M\s*\d+(?:[xX]\d+(?:[.,]\d+)?)?\b|DN\s*\d+\b|"
    r"\d+\s*/\s*\d+\s*(?:\"|pol(?:egadas?)?)(?:\s*BSP)?\b|"
    r"BCD\s*\d+(?:\s*/\s*\d+)?\s*(?:mm)?\b)"
)
DIMENSION_RE = re.compile(
    r"(?i)\b\d+(?:[.,]\d+)?\s*[x×]\s*\d+(?:[.,]\d+)?"
    r"(?:\s*[x×]\s*\d+(?:[.,]\d+)?)?\s*(?:mm|cm|m|pol(?:egadas?)?|\")\b"
)
YEAR_RE = re.compile(r"(?<!\d)(19[5-9]\d|20[0-3]\d)(?!\d)")
YEAR_RANGE_RE = re.compile(
    r"(?<!\d)(19[5-9]\d|20[0-3]\d)\s*(?:a|ate|até|[-–—/])\s*"
    r"(19[5-9]\d|20[0-3]\d)(?!\d)",
    re.IGNORECASE,
)

VEHICLE_BRANDS = (
    ("Mercedes-Benz", ("mercedes-benz", "mercedes benz", "mercedes")),
    ("Land Rover", ("land rover",)),
    ("Alfa Romeo", ("alfa romeo",)),
    ("Volkswagen", ("volkswagen", "vw", "volks")),
    ("Chevrolet", ("chevrolet", "gm")),
    ("Citroën", ("citroen", "citroën")),
    ("Mitsubishi", ("mitsubishi",)),
    ("Hyundai", ("hyundai",)),
    ("Peugeot", ("peugeot",)),
    ("Renault", ("renault",)),
    ("Nissan", ("nissan",)),
    ("Toyota", ("toyota",)),
    ("Honda", ("honda",)),
    ("Yamaha", ("yamaha",)),
    ("Kawasaki", ("kawasaki",)),
    ("Suzuki", ("suzuki",)),
    ("Ducati", ("ducati",)),
    ("Triumph", ("triumph",)),
    ("Harley-Davidson", ("harley-davidson", "harley davidson", "harley")),
    ("Ford", ("ford",)),
    ("Fiat", ("fiat",)),
    ("Jeep", ("jeep",)),
    ("Audi", ("audi",)),
    ("BMW", ("bmw",)),
    ("Volvo", ("volvo",)),
    ("Kia", ("kia",)),
    ("Chery", ("chery",)),
    ("Subaru", ("subaru",)),
    ("Mazda", ("mazda",)),
    ("Porsche", ("porsche",)),
    ("Jaguar", ("jaguar",)),
    ("Dodge", ("dodge",)),
    ("Ram", ("ram",)),
    ("Iveco", ("iveco",)),
    ("Scania", ("scania",)),
    ("Agrale", ("agrale",)),
)

FALSE_AUTOMOTIVE_SKUS = {"126", "141", "445", "446", "78", "79"}
TRUE_AUTOMOTIVE_SKUS = {
    "139", "147-K4", "149-K4", "154", "189-K", "200", "248", "280-K", "280",
    "297-K", "309.1", "314-K", "319", "320", "326", "33", "331-K", "331",
    "332-K", "332", "373", "416", "430", "431", "437", "46", "53", "99-1",
}
IDENTITY_CONFLICT_SKUS = {"151", "153", "261", "305-10-K", "449-K"}
VEHICLE_EXCLUSIONS = {
    "506": {("peugeot", "2008")},
    "341": {("bmw", "x1 118 320i 120i 116i")},
}

# Linhas contaminadas por texto editorial ou por compatibilidade de outro
# anúncio. A filtragem ocorre antes da normalização do nome do modelo.
RAW_VEHICLE_EXCLUSIONS = {
    "239-K": {("bmw", "serie 3 ano")},
    "507": {("chevrolet", "cruze 1.4 ano")},
}

OEM_EXCLUSIONS = {
    "001": {"3042678", "4628629660"},
    "214": {"93110-2D000", "2573685687"},
    "221": {"7895141638686N"},
    "225": {"06H906517B", "0280142459", "1024709650", "47372790658"},
    "232-1": {"5762572-09081"},
    "232-5": {"5762572-09081"},
    "275-19": {"5762572-09081"},
    "372": {
        "5790853526611", "55116901AA", "52028974AA", "52079880AA",
        "52079799AA", "4677493AA", "K55116901AA", "K52028974AA",
        "K52079880AA", "5058482AD", "52079778AA", "K04677493AA",
        "K52079778AA", "5058394AG", "K05058394AG", "5058446AH",
        "K05058446AH", "5058482AH",
    },
}

# Revisão autoritativa dos casos em que IDs de anúncio, EANs ou códigos de
# outro SKU haviam sido agregados como OEM.
OEM_FINAL_OVERRIDES = {
    "001": ["37760-MT2-003"],
    "4-2": ["6C1R7217KA", "1417447"],
    "005": ["5WS40039", "55PP02-03"],
    "35": ["9677899580"],
    "47": ["52933-C1100"],
    "53": ["16576ET00A"],
    "66": ["LR036127"],
    "70": ["40260-1S700"],
    "78": [],
    "79": [],
    "99": [],
    "99-1": [],
    "116": ["55579102"],
    "120": ["15825-P2M-005", "36172-P08-012"],
    "137": ["1 110 010 028"],
    "151": ["12 12 0 037 580", "12 12 0 036 759", "12 12 0 037 051"],
    "151-K6": ["12120037582", "ZR5TPP33S", "0242145518", "ZR5TPP332", "ZR5TPP33", "0242145515"],
    "155": ["A2059053414", "A2059052809", "A2054400073"],
    "160": [],
    "160-K": [],
    "167-1": [],
    "190": [],
    "192": [],
    "195-K": [],
    "196": ["44300-28A00", "44300-28A01", "44300-28A02", "43-67401"],
    "214": [
        "93110-3S000", "93110-0U000", "DNI2617", "55.110", "24027",
        "2104027", "EKS-HY-002", "V52-80-0009", "65SKV050", "09161286",
        "LCG1272", "IM41995",
    ],
    "221": ["63217217314"],
    "222-1": [],
    "222-2": [],
    "222-K": [],
    "225": [
        "06E906517A", "06E906517", "0280142431", "EVP0024", "CP569",
        "911-800", "V10-77-0032", "9412", "2250188", "8029412",
        "170864", "6NW 358 629-321", "300296900",
    ],
    "227-1": ["54132-S84-A81ZA"],
    "227-2": ["54132-SDA-A81"],
    "231": ["35355-T7A-J01", "35355-T7A-J02"],
    "231-1": ["35355-T7A-J01", "35355-T7A-J02"],
    "237": ["A2229003300", "1307329315"],
    "240": ["A2189009303", "A1668203589"],
    "232-1": ["51417279316", "51417279315"],
    "232-2": ["51427281465"],
    "232-5": ["51417279316", "51417279315"],
    "254-1": ["LR057235", "LR044427", "LR026192"],
    "272-K4": [],
    "272-K8": [],
    "250-1": ["98690-3V000", "98600-3V000"],
    "261": ["5WK97008Z", "5WK97008", "8200280060", "8200300002", "16580-00Q0A", "22680-JD50A", "4416861", "93856812"],
    "275-19": [],
    "275-5": [],
    "278": ["A2700900382"],
    "280": ["55563374", "55579838"],
    "280-K": ["55563374"],
    "286": ["20443641", "20748450"],
    "289": ["MD343605", "E5T08471"],
    "290": ["9657608580", "9657573680", "6500Y1"],
    "294": ["V861706980", "037979"],
    "298-K": ["98630-2E100", "98630-2E500"],
    "307-1": [],
    "307-2": ["9804315380"],
    "307-K": [],
    "316": ["497610324R"],
    "313": ["8015A066"],
    "318": ["81230-1S100"],
    "319": ["G1D500201"],
    "309.1": ["9109-946", "7135-818", "28233374", "6510740084"],
    "312": [],
    "321-2": [],
    "324": ["1K0819422B"],
    "326": ["1KD819203", "1K0819203"],
    "327": [],
    "344": ["1C3Z-3B396-CB"],
    "344-K": ["1C3Z-3B396-CB"],
    "353": [],
    "341": ["16127327120", "16127451424", "16127233840"],
    "343": ["MR342870"],
    "345": [],
    "346": [],
    "347": ["68191356AA"],
    "354": ["MB571603", "MR571603"],
    "355": ["6395451013"],
    "360": ["91214-P2F-A01", "91214-P7A-004", "91214-PAA-A01", "91214-PAA-A02", "91214-PC6-003", "91214-PC6-013", "91214-PC7-000"],
    "362": [],
    "363": [],
    "365": [],
    "367-K": [],
    "371": ["53366451", "55116901AA"],
    "372": ["53366451"],
    "369": ["68211200AC", "68211200AA", "68211200AB"],
    "376": ["8M0077471", "300-879984T01", "339-879984T00"],
    "377-2": [],
    "377-4": [],
    "377-4-K": [],
    "382": ["397839", "391638", "395091", "397274"],
    "384": ["14463-5X04B"],
    "385-2": [],
    "385-3": [],
    "385-4": [],
    "394-1": [],
    "394-2": [],
    "396": [],
    "397": [],
    "408": [],
    "410": ["17225-R1A-A01"],
    "411": [],
    "415": [],
    "417-2": ["19045-P32-P01", "19045-PAA-A01", "19045-RAA-003"],
    "425-1": ["AB39-6K683-DD"],
    "432": [],
    "402": ["6554.WA"],
    "435-1": ["87624-2S200"],
    "435-2": ["87614-2S200"],
    "448": ["AB3Z-21519A70-BC"],
    "451-1": ["FG9Z-3078-C", "GS7Z-3078-B"],
    "451-2": ["FG9Z-3079-C", "GS7Z-3079-B"],
    "451-K": ["FG9Z-3078-C", "GS7Z-3078-B", "FG9Z-3079-C", "GS7Z-3079-B"],
    "457-1": [],
    "457-2": [],
    "458-2": ["71121-T9A-T00"],
    "462": ["04692269AI", "4692269AI"],
    "514": ["81570-0K100"],
    "520-1": ["A2059056811", "2059056811"],
    "520-2": ["A2059056811", "2059056811"],
    "520-3": ["A2059056811", "2059056811"],
    "506": [],
    "512-K": [],
    "513-K": [],
    "515": [],
    "516-K": [],
    "SHOPEE-377-2": [],
    "SHOPEE-377-4": [],
}

# A lista final funciona como base curada, mas uma pesquisa marcada como
# autoritativa pode substituí-la. Assim, uma nova confirmação técnica não fica
# bloqueada por um override antigo.
OEM_FINAL_SUPERSEDES_RESEARCH: set[str] = set()

FINAL_TECHNICAL_OVERRIDES = {
    "27-1": ["Material: plástico técnico reforçado com fibra de carbono.", "Cor: preta."],
    "27-2": ["Material: plástico técnico reforçado com fibra de carbono.", "Cor: branca."],
    "61": ["Cor: preta."],
    "100": ["Conector de áudio: P2 estéreo de 3,5 mm.", "Alimentação elétrica própria: dispensada."],
    "81": ["Formato: D4R com base P32d-6.", "Potência nominal: 35 W.", "Tensão nominal da lâmpada após o reator: 42 V.", "Temperatura de cor: 6000 K."],
    "81-K": ["Formato: D4R com base P32d-6.", "Potência nominal por lâmpada: 35 W.", "Tensão nominal da lâmpada após o reator: 42 V.", "Temperatura de cor: 6000 K.", "Quantidade do conjunto: 2 unidades."],
    "82": ["Formato: D4R com base P32d-6.", "Potência nominal: 35 W.", "Tensão nominal da lâmpada após o reator: 42 V.", "Temperatura de cor: 4300 K."],
    "82-K": ["Formato: D4R com base P32d-6.", "Potência nominal por lâmpada: 35 W.", "Tensão nominal da lâmpada após o reator: 42 V.", "Temperatura de cor: 4300 K.", "Quantidade do conjunto: 2 unidades."],
    "151": ["Tecnologia: dupla platina.", "Referência Bosch: ZR5TPP33.", "Código Bosch: 0 242 145 515.", "Quantidade: 1 unidade."],
    "153": ["Interface ELM327/OBD2 com conexão USB e circuito FTDI.", "Comutação manual entre as redes HS-CAN e MS-CAN.", "HS-CAN: pinos 6 e 14.", "MS-CAN: pinos 3 e 11.", "Alimentação: 12 V.", "Corrente: 45 mA.", "Taxa de comunicação: 38.400 baud."],
    "173": ["Materiais da carcaça: metal e plástico.", "Montagem: conjunto do eletroventilador."],
    "195": ["Formato: D4S com base P32d-5.", "Potência nominal: 35 W.", "Tensão nominal da lâmpada após o reator: 42 V.", "Temperatura de cor: 6000 K.", "Quantidade: 1 unidade."],
    "195-K": ["Potência: 35 W.", "Temperatura de cor: 6000 K.", "Quantidade do conjunto: 2 unidades."],
    "208": [],
    "214": [
        "Tipo: comutador elétrico da ignição e partida.",
        "Tensão nominal: 12 V.",
        "Quantidade de terminais: 6.",
        "Acionamento: mecânico pelo cilindro da chave.",
    ],
    "221": ["Tipo: placa do módulo de LED da lanterna.", "Referência da placa: B003809.2.", "Referência alternativa da placa: B0038902."],
    "225": [
        "Tipo: válvula eletromagnética de purga do cânister do sistema EVAP.",
        "Material do corpo: plástico.",
        "Quantidade de terminais elétricos: 2.",
        "Quantidade de conexões para mangueiras: 2.",
        "Formato do conector elétrico: retangular.",
    ],
    "232-1": ["Material: plástico ABS.", "Cor: preta.", "Fixação: encaixe.", "Quantidade do conjunto: 2 unidades.", "Posições: dianteira direita e dianteira esquerda."],
    "232-5": ["Material: plástico ABS.", "Cor: bege.", "Fixação: encaixe.", "Quantidade do conjunto: 2 unidades.", "Posições: dianteira direita e dianteira esquerda."],
    "236": ["Tensão nominal: 12 V.", "Quantidade de terminais: 6.", "Distribuição dos conectores: 2 + 4 pinos.", "Estágios de acionamento: 2 velocidades."],
    "252-K": [],
    "282": ["Tipo: atuador a vácuo do engate dianteiro da tração 4x4.", "Posição de montagem: dianteira."],
    "289": ["Quantidade de pinos: 7."],
    "303": ["Tipo: atuador com diafragma acionado por vácuo.", "Material da carcaça: plástico."],
    "272-K4": ["Tipo: filtros-tela de óleo para o conjunto de eixos balanceadores 06H.", "Quantidade do conjunto: 4 unidades."],
    "272-K8": ["Tipo: filtros-tela de óleo para o conjunto de eixos balanceadores 06H.", "Quantidade do conjunto: 8 unidades."],
    "305-2-K": ["Material: liga de alumínio e lente divergente.", "Tensão de funcionamento: 12 V CC.", "Potência: 9 W.", "Cor externa: preta.", "Quantidade do conjunto: 2 unidades."],
    "306-10-K": ["Material: liga de alumínio e lente divergente.", "Tensão de funcionamento: 12 V CC.", "Cor externa: prata.", "Quantidade do conjunto: 2 unidades."],
    "306-2-K": ["Material: liga de alumínio e lente divergente.", "Tensão de funcionamento: 12 V CC.", "Cor externa: preta.", "Quantidade do conjunto: 2 unidades."],
    "312": ["Material: ABS flexível.", "Cor: preta.", "Seletor compatível: câmbio automático AL4 de 4 marchas."],
    "349": ["Acabamento: cromado."],
    "353": ["Tipo: filtro do sistema de acoplamento Haldex."],
    "367": ["Materiais: alumínio, ferro e polipropileno.", "Tipo: roda livre manual.", "Posição: cubo dianteiro."],
    "367-K": ["Materiais: alumínio, ferro e polipropileno.", "Tipo: rodas livres manuais.", "Posição: cubos dianteiros.", "Quantidade do conjunto: 2 unidades."],
    "368": [],
    "371": ["Tipo: conector superior com bocal e tampa.", "Materiais do conjunto: plástico e metal.", "Posição: superior.", "Conteúdo: conector/flange e tampa."],
    "372": [
        "Tipo: conector com gargalo de enchimento da mangueira superior do radiador.",
        "Material: plástico preto.",
        "Posição: mangueira superior do sistema de arrefecimento.",
        "Configuração: duas conexões principais estriadas para mangueira.",
        "Possui gargalo lateral para tampa pressurizada, bico fino de retorno ao reservatório de expansão e pino moldado de posicionamento.",
        "Conteúdo identificado: conector/gargalo sem tampa e sem abraçadeiras.",
        "Código de referência do gargalo: 53366451.",
        "Referência comercial equivalente em plástico: CL9952.",
        "Alternativa de reparo em alumínio: DM-0192; conferir o desenho e os diâmetros antes da substituição.",
    ],
    "377-4-K": ["Material: aço inoxidável.", "Quantidade de LEDs por unidade: 27.", "Quantidade do conjunto: 2 unidades."],
    "415": ["Tipo: digitalizador de vidro sensível ao toque.", "Referência gravada: A211216702.", "Quantidade de vias do cabo flexível: 50."],
    "417-2": ["Material: metal.", "Pressão nominal: 1,1 bar."],
    "449-K": ["Material: plástico ABS.", "Conteúdo do conjunto: grade superior e grade inferior dianteiras."],
    "454": ["Quantidade de pinos: 8."],
    "462": ["Tipo: sensor inteligente de bateria (IBS).", "Montagem: terminal negativo da bateria."],
    "467": ["Tipo: membrana flexível da válvula PCV."],
    "481": ["Tensão: 12 V.", "Rotação: 20 RPM.", "Torque: 22 Nm."],
    "521": ["Material: plástico.", "Posição: superior.", "Conteúdo do conjunto: mangueira e abraçadeiras."],
    "SHOPEE-124": ["Tipo: repelente ultrassônico portátil.", "Recursos: lanterna LED, apontador a laser e modo de treinamento.", "Alimentação: bateria de 9 V."],
}

FORCE_EMPTY_VEHICLES = {"462"}
FORCE_IDENTITY_PENDING: set[str] = set()
FORCE_EMPTY_DOSSIER_SKUS: set[str] = set()
# Mantém o nome em português para identificação do cadastro, mas não
# publica características, códigos ou aplicações enquanto foto, cadastro e
# anúncio apontarem para peças incompatíveis.
FORCE_EMPTY_DETAIL_SKUS: set[str] = set()

TRUSTED_MEASURE_SKUS = {
    "001", "12", "126", "129", "129-3", "141", "153", "160", "160-K", "167",
    "167-1K", "167-2", "167-2K", "167-K", "194-K", "196", "219-K6", "227-1",
    "227-2", "23", "232-2", "232-6", "241", "242", "245", "251", "256", "268",
    "27-1", "27-2", "27-K", "305-10-K", "305-2-K", "306-10-K", "306-2-K", "312",
    "313", "315-K", "337", "357", "361", "373", "377-2", "377-4", "377-4-K",
    "380", "384", "385-2", "385-3", "385-4", "391", "393-2", "394-1", "394-2",
    "412-1", "412-2", "412-3", "412-4", "412-5", "412-55", "412-6", "412-7",
    "412-8", "412-9", "413", "415", "422", "422-K", "425", "425-1", "425-2",
    "432", "433-1", "433-2", "436-1", "436-2", "436-3", "436-4", "437",
    "444-K2-1", "444-K2-2", "445", "446", "453", "459", "460", "461", "462",
    "280-K", "473", "481", "52-1", "60", "60-K4", "78", "79", "D104-32", "D104-32-O",
    "D104-46", "D104-48", "D104-50", "D104-52", "D9496-32", "D9496-38",
    "D96A-32-O", "D96A-34-O", "D96A-36-O", "D96A-38", "D96A-38-O", "DGC-03",
    "DGC-BB", "DSHI-32", "SHOPEE-377-2", "SHOPEE-377-4", "SHOPEE-459", "tik06",
}

MEASURE_OVERRIDES = {
    "001": ["Rosca: M16 x 1,5", "Sextavado: 22 mm"],
    "12": ["Comprimento: 112 mm", "Diâmetro aproximado: 45 mm", "Conexões de entrada e saída: 10 mm"],
    "129": ["Dimensões: 185 × 85 × 33 mm"],
    "129-3": ["Dimensões: 185 × 85 × 33 mm"],
    "141": ["Dimensões da estrutura: 15 × 2,5 × 1,5 cm", "Comprimento do cabo: 13 cm"],
    "153": ["Dimensões: 4,8 × 2,5 × 3,4 cm", "Peso líquido: 25 g"],
    "160": ["Bocal de encaixe: 6,2 cm", "Comprimento da ponteira: 11,5 cm"],
    "160-K": ["Bocal de encaixe: 6,2 cm", "Comprimento de cada ponteira: 11,5 cm"],
    "167": ["Comprimento: 3,75 cm", "Diâmetro do encaixe macho: 6 mm", "Diâmetro externo do encaixe fêmea: 9 mm"],
    "167-K": ["Comprimento de cada extensor: 3,75 cm", "Diâmetro do encaixe macho: 6 mm", "Diâmetro externo do encaixe fêmea: 9 mm"],
    "214": ["Comprimento aproximado: 6,5 cm", "Largura aproximada: 5,0 cm", "Altura aproximada: 4,0 cm"],
    "23": ["Dimensões: 7 × 3 cm", "Comprimento do cabo: 136 cm"],
    "241": ["Telas compatíveis: 4,0 a 6,8 polegadas"],
    "268": ["Dimensões totais: 14 × 8 × 5 cm", "Peso líquido: 117 g"],
    "280-K": ["Diâmetro interno: 35 mm", "Diâmetro externo: 48,25 mm", "Espessura: 7 mm"],
    "312": ["Dimensões: 210 × 47,5 mm", "Furo: 19 × 43 mm"],
    "337": ["Diâmetro da cabeça: 29,7 mm", "Comprimento: 43,61 mm", "Haste: 19,86 mm"],
    "361": ["Tamanho da tela: 3,5 polegadas"],
    "412-1": ["Diâmetro de trabalho: 360 mm", "Tubo: 26 mm", "Encaixe: 9 estrias", "Peso líquido: 3,9 kg"],
    "412-2": ["Diâmetro de trabalho: 360 mm", "Tubo: 26 mm", "Encaixe quadrado: 5 mm", "Peso líquido: 3,9 kg"],
    "412-3": ["Diâmetro de trabalho: 360 mm", "Tubo: 28 mm", "Encaixe: 9 estrias", "Peso líquido: 3,9 kg"],
    "412-4": ["Diâmetro de trabalho: 360 mm", "Tubo: 28 mm", "Encaixe quadrado: 5,0 mm", "Peso líquido: 3,9 kg"],
    "412-5": ["Diâmetro de trabalho: 360 mm", "Tubo: 26 mm", "Encaixe quadrado: 5,5 mm", "Peso líquido: 3,9 kg"],
    "412-55": ["Diâmetro de trabalho: 360 mm", "Tubo: 26 mm", "Encaixe quadrado: 5,5 mm", "Peso líquido: 3,9 kg"],
    "412-6": ["Diâmetro de trabalho: 360 mm", "Tubo: 26 mm", "Encaixe: 7 estrias", "Peso líquido: 3,9 kg"],
    "412-7": ["Diâmetro de trabalho: 360 mm", "Tubo: 26 mm", "Encaixe quadrado: 6,0 mm", "Peso líquido: 3,9 kg"],
    "412-8": ["Diâmetro de trabalho: 360 mm", "Tubo: 28 mm", "Encaixe quadrado: 5,5 mm", "Peso líquido: 3,9 kg"],
    "412-9": ["Diâmetro de trabalho: 360 mm", "Tubo: 28 mm", "Encaixe quadrado: 6,0 mm", "Peso líquido: 3,9 kg"],
    "415": ["Dimensões: 204 × 119 mm"],
    "425": ["Diâmetros internos: 55 mm e 67 mm", "Comprimento: 57 cm"],
    "432": ["Diâmetro externo: 200 mm", "Furo central: 25 mm"],
    "433-1": ["Dimensões de cada placa: 290 × 16 × 2,5 cm"],
    "433-2": ["Dimensões de cada placa: 290 × 16 × 2,5 cm"],
    "436-1": ["Encaixe: 9 estrias"],
    "436-2": ["Encaixe: 7 estrias"],
    "436-3": ["Encaixe quadrado: 6,0 mm"],
    "436-4": ["Encaixe quadrado: 5,5 mm"],
    "437": ["Comprimento: 9 cm", "Diâmetro interno: 3 cm"],
    "444-K2-1": ["Furo de fixação: 25,4 mm (1 polegada)"],
    "444-K2-2": ["Furo de fixação: 25,4 mm (1 polegada)"],
    "453": ["Furo central: 25,4 mm (1 polegada)"],
    "459": ["Roscas de entrada e saída: 1/4\" BSP"],
    "SHOPEE-459": ["Roscas de entrada e saída: 1/4\" BSP"],
    "460": ["Dimensões: 172 × 50 × 50 mm", "Saída: 0,75 polegada"],
    "461": ["Bitola dos cabos: 16 a 50 mm²"],
    "305-10-K": ["Diâmetro de montagem: 18 mm"],
    "305-2-K": ["Diâmetro de montagem: 18 mm"],
    "306-10-K": ["Diâmetro de montagem: 23 mm"],
    "306-2-K": ["Diâmetro de montagem: 23 mm"],
    "473": ["Dimensões: 1,20 × 1,80 m", "Espessura: 1 mm"],
    "52-1": ["Altura: 96,6 mm", "Comprimento: 135,4 mm", "Distância entre furos de montagem: 81,62 mm", "Furo central: 12,2 mm"],
    "78": ["Dimensões: 80 × 77 × 49 mm"],
    "79": ["Dimensões: 80 × 77 × 49 mm"],
    "D104-32": ["BCD: 104 mm"],
    "D104-32-O": ["BCD: 104 mm"],
    "D104-46": ["BCD: 104 mm"],
    "D104-48": ["BCD: 104 mm"],
    "D104-50": ["BCD: 104 mm"],
    "D104-52": ["BCD: 104 mm"],
    "D9496-32": ["BCD: 94/96 mm"],
    "D9496-38": ["BCD: 94/96 mm"],
    "D96A-32-O": ["BCD: 96 mm"],
    "D96A-34-O": ["BCD: 96 mm"],
    "D96A-36-O": ["BCD: 96 mm"],
    "D96A-38": ["BCD: 96 mm"],
    "D96A-38-O": ["BCD: 96 mm"],
    "DSHI-32": ["Deslocamento: 3 mm"],
    "tik06": ["Comprimento da corrente: 10 m", "Diâmetro da corrente: 6 mm"],
    "241-1": ["Encaixe do suporte: 12 mm"],
    "242": [],
    "232-2": ["Dimensões aproximadas do conjunto: 380 × 90 × 80 mm"],
    "232-6": ["Dimensões aproximadas do conjunto: 380 × 90 × 80 mm"],
    "315-K": [],
    "377-2": ["Comprimento do fio: 152 cm"],
    "377-4": ["Comprimento do fio: 152 cm"],
    "385-2": ["Comprimento do fio: 152 cm"],
    "385-3": ["Comprimento do fio: 152 cm"],
    "385-4": ["Comprimento do fio: 152 cm"],
    "394-1": ["Diâmetro nominal do acoplamento: 50 mm"],
    "394-2": ["Diâmetro nominal do acoplamento: 45 mm"],
    "SHOPEE-377-2": ["Comprimento do fio: 152 cm"],
    "SHOPEE-377-4": ["Comprimento do fio: 152 cm"],
    "481": ["Altura: 22 cm", "Comprimento: 15 cm", "Largura: 16 cm"],
    "251": ["Rosca macho: 1/2\""],
    "342": ["Diâmetro do mostrador: 52 mm"],
    "358": ["Diâmetro da ponta: 1,0 mm"],
    "391": ["Diâmetro nominal: 200 mm (8 polegadas)"],
    "403": ["Dimensões: 15 × 8,5 × 6,5 cm"],
    "407": ["Dimensões: 64 × 64 × 28 mm", "Peso líquido: 72 g"],
    "411": ["Diâmetro do venturi: 19 mm"],
    "438": ["Dimensões: 9 × 18 × 18 cm", "Peso líquido: 193 g"],
    "445": ["Dimensões externas: 11 × 9 × 4 cm", "Dimensões internas: 9 × 7 × 2 cm", "Espessura da porta: 3 cm", "Espessura da parede: 2,5 cm", "Espessura dos pinos: 1 cm", "Capacidade interna: 0,189 L", "Peso líquido: 400 g"],
    "D9496-32-O": ["BCD: 94/96 mm"],
    "D9496-34": ["BCD: 94/96 mm"],
    "D9496-34-O": ["BCD: 94/96 mm"],
    "D9496-36": ["BCD: 94/96 mm"],
    "D9496-36-O": ["BCD: 94/96 mm"],
    "D9496-38-O": ["BCD: 94/96 mm"],
}

# Medidas rechecadas que devem prevalecer inclusive sobre relatórios de
# pesquisa anteriores que tenham registrado somente parte das dimensões.
FINAL_MEASURE_OVERRIDES = {
    "312": ["Comprimento: 210 mm", "Largura: 47,5 mm", "Abertura: 19 × 43 mm"],
}

# Códigos confirmados pelo cadastro atual ou Mercado Livre e confrontados com
# catálogo técnico público. Códigos encontrados somente no Bling não entram.
OEM_OVERRIDES = {
    "001": ["37760-MT2-003"],
    "002": ["8M51-F405A02-AA", "8M51F405A02AA"],
    "134": ["A6510700132", "6510700132"],
    "147-K4": ["12620540"],
    "154": ["MR577031", "100798-5960"],
    "158": ["0281002908", "314004A010", "0281002568"],
    "199": ["22270-15010"],
    "20": ["3M519A299AA"],
    "208": ["06H103269H"],
    "230-K": ["61677211209", "61677211210"],
    "236": ["9673999980", "9673999880", "1267J6", "9662872380"],
    "243": ["56031003AB", "PS284"],
    "244": ["LR053666"],
    "246": ["8699465"],
    "248": ["2501074P01"],
    "264": ["575353K000"],
    "272-K8": ["06H103081E", "06H103144J"],
    "274": ["971431M000"],
    "281": ["55568437"],
    "282": ["4151009000", "4151036200"],
    "287": ["MD343605", "E5T08471"],
    "299-1": ["1Z0941431", "5ND941431B"],
    "321-K": ["5GG8059039B9", "5GG8059049B9"],
    "368": ["22270-22060"],
    "372": ["53366451"],
    "399": ["20S-12461-01-00"],
    "403": ["20455317", "20452017", "20568857", "20752918", "20953592", "21277587", "21354601", "21543897"],
    "454": ["35355-TBA-A01"],
    "519": ["4602544AG"],
    "520-1": ["A2059056811", "2059056811"],
    "81-K": ["90981-20029"],
}

APPLICATION_TYPE_OVERRIDES = {
    "001": "motocicleta",
    "008": "automóvel",
    "12": "motocicleta",
    "134": "automóvel",
    "170-K4": "automóvel",
    "210": "universal veicular",
    "214": "automóvel",
    "225": "automóvel",
    "228": "motocicleta",
    "236": "automóvel",
    "241-1": "motocicleta",
    "257": "automóvel",
    "264": "automóvel",
    "281": "automóvel",
    "282": "automóvel",
    "352": "automóvel",
    "367-K": "automóvel",
    "372": "automóvel",
    "376": "náutica",
    "377-2": "náutica",
    "377-4": "náutica",
    "377-4-K": "náutica",
    "379-1": "automóvel",
    "379-2": "automóvel",
    "379-3": "automóvel",
    "379-5": "automóvel",
    "382": "náutica",
    "384": "automóvel",
    "385-2": "náutica",
    "385-3": "náutica",
    "385-4": "náutica",
    "398": "automóvel",
    "404": "automóvel",
    "406": "motocicleta",
    "411": "motocicleta",
    "437": "motocicleta",
    "44-1": "automóvel",
    "456": "automóvel",
    "52": "motocicleta",
    "52-1": "motocicleta",
    "70": "automóvel",
    "81": "automóvel",
    "82-K": "automóvel",
    "99": "ferramenta automotiva",
    "99-1": "ferramenta automotiva",
    "232-1": "automóvel",
    "232-5": "automóvel",
    "305-2-K": "universal veicular",
    "306-2-K": "universal veicular",
    "306-10-K": "universal veicular",
    "396": "universal veicular",
    "SHOPEE-107": "automóvel",
    "SHOPEE-124": "não veicular",
    "SHOPEE-411": "motocicleta",
}

COMPATIBILITY_OVERRIDES = {
    "001": [
        {"marca": "Honda", "modelo": "CB 500", "anos": ["1997 a 2004"]},
        {"marca": "Honda", "modelo": "CB 600F Hornet", "anos": ["2004 a 2009"]},
        {"marca": "Honda", "modelo": "GL1500 Gold Wing", "anos": ["1988 a 1990", "1993 a 1994", "1996 a 2000"]},
        {"marca": "Honda", "modelo": "VT 600 C Shadow", "anos": ["1997 a 2006"]},
        {"marca": "Suzuki", "modelo": "GSX-R 1100 W", "anos": ["1994 a 1998"]},
    ],
    "100": [{"marca": "Fiat", "modelo": "Punto, Palio, Linea e Stilo", "anos": []}],
    "261": [{"marca": "Renault", "modelo": "Master 2.5 16V diesel", "anos": ["2009 a 2015"]}],
    "239-1": [{"marca": "BMW", "modelo": "Série 3", "anos": ["2012 a 2015"]}],
    "239-2": [{"marca": "BMW", "modelo": "Série 3", "anos": ["2012 a 2015"]}],
    "411": [
        {"marca": "Honda", "modelo": "Biz 100", "anos": ["1998 a 2005"]},
        {"marca": "Sundown", "modelo": "Web 100", "anos": ["2005"]},
    ],
    "SHOPEE-411": [
        {"marca": "Honda", "modelo": "Biz 100", "anos": ["1998 a 2005"]},
        {"marca": "Sundown", "modelo": "Web 100", "anos": ["2005"]},
    ],
}

MERCEDES_W205_WINDOW_SWITCH_VEHICLES = [
    {"marca": "Mercedes-Benz", "modelo": "C180 W205", "anos": ["2014 a 2021"]},
    {"marca": "Mercedes-Benz", "modelo": "C200 W205", "anos": ["2014 a 2018"]},
    {"marca": "Mercedes-Benz", "modelo": "C250 W205", "anos": ["2014 a 2018"]},
    {"marca": "Mercedes-Benz", "modelo": "C300 W205", "anos": ["2015 a 2018"]},
    {"marca": "Mercedes-Benz", "modelo": "C450 W205", "anos": ["2016"]},
    {"marca": "Mercedes-Benz", "modelo": "C63 AMG W205", "anos": ["2015 a 2020"]},
]

FINAL_VEHICLE_OVERRIDES = {
    "4-2": [
        {"marca": "Ford", "modelo": "Transit com câmbio manual MT82 de 6 marchas", "anos": ["2008 a 2014"]},
    ],
    "66": [
        {"marca": "Land Rover", "modelo": "Freelander 2 L359", "anos": ["2007 a 2010"], "restrições": ["confirmar o OEM LR036127"]},
        {"marca": "Land Rover", "modelo": "Range Rover Evoque L538", "anos": ["2011 a 2018"], "restrições": ["confirmar o OEM LR036127"]},
    ],
    "59": [
        {"marca": "Honda", "modelo": "Civic", "anos": ["1996 a 2000"]},
    ],
    "120": [
        {"marca": "Honda", "modelo": "Civic EX 1.6 D16Y8", "anos": ["1996 a 2000"]},
        {"marca": "Honda", "modelo": "Civic EX 1.7", "anos": ["2001 a 2005"]},
    ],
    "137": [
        {"marca": "Volkswagen", "modelo": "Delivery 5.150, 9.160 e 10.160", "anos": ["2012 a 2022"]},
    ],
    "151-K6": [
        {"marca": "BMW", "modelo": "Série 1 E82/E88 com motor N55", "anos": ["2009 a 2013"]},
        {"marca": "BMW", "modelo": "Série 1 F20/F21 com motor N55", "anos": ["2012 a 2016"]},
        {"marca": "BMW", "modelo": "Série 2 F22/F23 com motor N55", "anos": ["2014 a 2016"]},
        {"marca": "BMW", "modelo": "Série 3 E90/E91/E92/E93 com motor N55", "anos": ["2010 a 2013"]},
        {"marca": "BMW", "modelo": "Série 3 F30/F31/F34 com motor N55", "anos": ["2012 a 2016"]},
        {"marca": "BMW", "modelo": "Série 4 F32/F33/F36 com motor N55", "anos": ["2014 a 2016"]},
        {"marca": "BMW", "modelo": "Série 5 F07/F10/F11 com motor N55", "anos": ["2010 a 2017"]},
        {"marca": "BMW", "modelo": "Série 6 F06/F12/F13 com motor N55", "anos": ["2012 a 2018"]},
        {"marca": "BMW", "modelo": "Série 7 F01/F02 com motor N55", "anos": ["2011 a 2015"]},
        {"marca": "BMW", "modelo": "X1 E84 com motor N55", "anos": ["2013 a 2015"]},
        {"marca": "BMW", "modelo": "X3 F25 com motor N55", "anos": ["2011 a 2017"]},
        {"marca": "BMW", "modelo": "X4 F26 com motor N55", "anos": ["2015 a 2016"]},
        {"marca": "BMW", "modelo": "X5 E70/F15 com motor N55", "anos": ["2011 a 2018"]},
        {"marca": "BMW", "modelo": "X6 E71/F16 com motor N55", "anos": ["2011 a 2019"]},
    ],
    "155": [
        {"marca": "Mercedes-Benz", "modelo": "Classe C W205 C180, C200, C250 e C300", "anos": ["2014 a 2017"], "restrições": ["confirmar o OEM ou o VIN"]},
    ],
    "196": [
        {"marca": "Suzuki", "modelo": "GSX-R750 G/H", "anos": ["1986 a 1987"]},
        {"marca": "Suzuki", "modelo": "GSX-R750RG", "anos": ["1986"]},
        {"marca": "Suzuki", "modelo": "GSX-R1100 G/H/J", "anos": ["1986 a 1988"]},
        {"marca": "Suzuki", "modelo": "GSX750F", "anos": ["1998 a 2009"]},
    ],
    "237": [
        {"marca": "Mercedes-Benz", "modelo": "C250, C350 e C63 AMG com farol D3S/D3R", "anos": ["2013 a 2014"]},
        {"marca": "Mercedes-Benz", "modelo": "GL350, GL450, GL500, GL550 e GL63 AMG com farol D3S/D3R", "anos": ["2013 a 2014"]},
        {"marca": "Mercedes-Benz", "modelo": "GLK300 e GLK350 com farol D3S/D3R", "anos": ["2012 a 2014"]},
        {"marca": "Mercedes-Benz", "modelo": "ML350, ML500, ML550 e ML63 AMG com farol D3S/D3R", "anos": ["2012 a 2014"]},
        {"marca": "Mercedes-Benz", "modelo": "SL500, SL550, SL63 AMG e SL65 AMG com farol D3S/D3R", "anos": ["2013 a 2014"]},
    ],
    "240": [
        {"marca": "Mercedes-Benz", "modelo": "Classe A W176", "anos": ["2012 a 2018"], "restrições": ["somente versões com a mesma referência do módulo"]},
        {"marca": "Mercedes-Benz", "modelo": "CLA C117", "anos": ["2013 a 2018"], "restrições": ["somente versões com a mesma referência do módulo"]},
        {"marca": "Mercedes-Benz", "modelo": "GLA X156", "anos": ["2013 a 2018"], "restrições": ["somente versões com a mesma referência do módulo"]},
    ],
    "214": [
        {"marca": "Hyundai", "modelo": "HB20 1.0 e 1.6 com chave mecânica", "anos": ["2012 em diante"], "restrições": ["conector de 6 pinos; confirmar 93110-3S000 ou 93110-0U000"]},
        {"marca": "Hyundai", "modelo": "HB20S com chave mecânica", "anos": ["2013 em diante"], "restrições": ["conector de 6 pinos; confirmar o código da peça instalada"]},
        {"marca": "Hyundai", "modelo": "HB20X com chave mecânica", "anos": ["2013 em diante"], "restrições": ["conector de 6 pinos; confirmar o código da peça instalada"]},
        {"marca": "Hyundai", "modelo": "Elantra", "anos": ["2010 a 2016"], "restrições": ["confirmar o código da peça instalada"]},
        {"marca": "Hyundai", "modelo": "Elantra Coupé", "anos": ["2013 a 2014"], "restrições": ["confirmar o código da peça instalada"]},
        {"marca": "Hyundai", "modelo": "Genesis Coupé", "anos": ["2011 a 2016"], "restrições": ["confirmar o código da peça instalada"]},
        {"marca": "Hyundai", "modelo": "i20", "anos": ["2008 em diante"], "restrições": ["confirmar o código da peça instalada"]},
        {"marca": "Hyundai", "modelo": "i30", "anos": ["2012 a 2015"], "restrições": ["confirmar o código da peça instalada"]},
        {"marca": "Hyundai", "modelo": "ix35", "anos": ["2009 a 2013"], "restrições": ["confirmar o código da peça instalada"]},
        {"marca": "Hyundai", "modelo": "Sonata", "anos": ["2010 a 2014"], "restrições": ["confirmar o código da peça instalada"]},
        {"marca": "Kia", "modelo": "Optima", "anos": ["2011 a 2015"], "restrições": ["confirmar o código da peça instalada"]},
        {"marca": "Kia", "modelo": "Rio", "anos": ["2011 a 2017"], "restrições": ["confirmar o código da peça instalada"]},
        {"marca": "Kia", "modelo": "Rio5", "anos": ["2011 a 2017"], "restrições": ["confirmar o código da peça instalada"]},
        {"marca": "Kia", "modelo": "Soul", "anos": ["2013 a 2019"], "restrições": ["confirmar o código da peça instalada"]},
        {"marca": "Kia", "modelo": "Sportage", "anos": ["2010 a 2016"], "restrições": ["confirmar o código da peça instalada"]},
    ],
    "225": [
        {"marca": "Volkswagen", "modelo": "Bora", "anos": ["2005 a 2010"], "restrições": ["confirmar 06E906517A ou 0280142431"]},
        {"marca": "Volkswagen", "modelo": "Jetta 2.5", "anos": ["2005 a 2014"], "restrições": ["não serve no Jetta 2.0 TSI; confirmar o código"]},
        {"marca": "Volkswagen", "modelo": "Passat 2.0", "anos": ["2006 a 2008"], "restrições": ["confirmar o código"]},
        {"marca": "Volkswagen", "modelo": "Beetle 2.5", "anos": ["2006 a 2010", "2012 a 2014"], "restrições": ["confirmar o código"]},
        {"marca": "Volkswagen", "modelo": "Golf 2.5", "anos": ["2010 a 2014"], "restrições": ["confirmar o código"]},
        {"marca": "Volkswagen", "modelo": "Golf R 2.0", "anos": ["2012 a 2013"], "restrições": ["confirmar o código"]},
        {"marca": "Volkswagen", "modelo": "GTI 2.0", "anos": ["2006 a 2008"], "restrições": ["confirmar o código"]},
        {"marca": "Volkswagen", "modelo": "Eos 2.0", "anos": ["2007 a 2009"], "restrições": ["confirmar o código"]},
        {"marca": "Volkswagen", "modelo": "Touareg 4.2", "anos": ["2007 a 2010"], "restrições": ["confirmar o código"]},
        {"marca": "Audi", "modelo": "A3 2.0", "anos": ["2006 a 2008"], "restrições": ["confirmar o código"]},
        {"marca": "Audi", "modelo": "A4 2.0 e 3.2", "anos": ["2005 a 2009"], "restrições": ["confirmar o código"]},
        {"marca": "Audi", "modelo": "A6 3.2", "anos": ["2005 a 2010"], "restrições": ["confirmar o código"]},
        {"marca": "Audi", "modelo": "A6 3.0", "anos": ["2009 a 2011"], "restrições": ["confirmar o código"]},
        {"marca": "Audi", "modelo": "A6 4.2", "anos": ["2005 a 2011"], "restrições": ["confirmar o código"]},
        {"marca": "Audi", "modelo": "A8 4.2", "anos": ["2007 a 2012"], "restrições": ["confirmar o código"]},
        {"marca": "Audi", "modelo": "Q7 4.2", "anos": ["2007 a 2010"], "restrições": ["confirmar o código"]},
        {"marca": "Audi", "modelo": "R8 4.2", "anos": ["2008 a 2012", "2014 a 2015"], "restrições": ["confirmar o código"]},
        {"marca": "Audi", "modelo": "TT 2.0", "anos": ["2008 a 2013"], "restrições": ["confirmar o código"]},
        {"marca": "Audi", "modelo": "TTS 2.0", "anos": ["2009 a 2015"], "restrições": ["confirmar o código"]},
        {"marca": "Audi", "modelo": "S4 3.0", "anos": ["2010 a 2011"], "restrições": ["confirmar o código"]},
        {"marca": "Audi", "modelo": "S5 3.0 ou 4.2", "anos": ["2008 a 2012"], "restrições": ["confirmar motor e código conforme o ano"]},
        {"marca": "Audi", "modelo": "S6 5.2", "anos": ["2007 a 2011"], "restrições": ["confirmar o código"]},
        {"marca": "Audi", "modelo": "S8 5.2", "anos": ["2007 a 2009"], "restrições": ["confirmar o código"]},
        {"marca": "Audi", "modelo": "RS4 4.2", "anos": ["2007 a 2008"], "restrições": ["confirmar o código"]},
        {"marca": "Audi", "modelo": "RS5 4.2", "anos": ["2013 a 2015"], "restrições": ["confirmar o código"]},
    ],
    "232-1": [
        {"marca": "BMW", "modelo": "316", "anos": ["2013 a 2018"]},
        {"marca": "BMW", "modelo": "320", "anos": ["2013 a 2018"]},
        {"marca": "BMW", "modelo": "328", "anos": ["2013 a 2018"]},
        {"marca": "BMW", "modelo": "335", "anos": ["2013 a 2018"]},
        {"marca": "BMW", "modelo": "420", "anos": ["2014 a 2017"]},
        {"marca": "BMW", "modelo": "428", "anos": ["2014 a 2017"]},
        {"marca": "BMW", "modelo": "430", "anos": ["2014 a 2017"]},
        {"marca": "BMW", "modelo": "435", "anos": ["2014 a 2017"]},
        {"marca": "BMW", "modelo": "440", "anos": ["2014 a 2017"]},
    ],
    "232-5": [
        {"marca": "BMW", "modelo": "316", "anos": ["2013 a 2018"]},
        {"marca": "BMW", "modelo": "320", "anos": ["2013 a 2018"]},
        {"marca": "BMW", "modelo": "328", "anos": ["2013 a 2018"]},
        {"marca": "BMW", "modelo": "335", "anos": ["2013 a 2018"]},
        {"marca": "BMW", "modelo": "420", "anos": ["2014 a 2017"]},
        {"marca": "BMW", "modelo": "428", "anos": ["2014 a 2017"]},
        {"marca": "BMW", "modelo": "430", "anos": ["2014 a 2017"]},
        {"marca": "BMW", "modelo": "435", "anos": ["2014 a 2017"]},
        {"marca": "BMW", "modelo": "440", "anos": ["2014 a 2017"]},
    ],
    "232-2": [
        {"marca": "BMW", "modelo": "Série 3 F30", "anos": ["2011 a 2015"]},
        {"marca": "BMW", "modelo": "Série 3 F30 reestilizada", "anos": ["2014 a 2018"]},
        {"marca": "BMW", "modelo": "Série 3 F31", "anos": ["2011 a 2015"]},
        {"marca": "BMW", "modelo": "Série 3 F31 reestilizada", "anos": ["2014 a 2019"]},
    ],
    "232-6": [
        {"marca": "BMW", "modelo": "Série 3 F30", "anos": ["2011 a 2015"]},
        {"marca": "BMW", "modelo": "Série 3 F30 reestilizada", "anos": ["2014 a 2018"]},
        {"marca": "BMW", "modelo": "Série 3 F31", "anos": ["2011 a 2015"]},
        {"marca": "BMW", "modelo": "Série 3 F31 reestilizada", "anos": ["2014 a 2019"]},
    ],
    "239-K": [
        {"marca": "BMW", "modelo": "Série 3 F30", "anos": ["2012 a 2015"]},
    ],
    "173": [
        {"marca": "Audi", "modelo": "A3", "anos": ["2006 a 2013"]},
        {"marca": "Audi", "modelo": "TT", "anos": ["2007 a 2014"]},
        {"marca": "Volkswagen", "modelo": "Beetle/Fusca", "anos": ["2012 a 2014"]},
        {"marca": "Volkswagen", "modelo": "Eos", "anos": ["2007 a 2014"]},
        {"marca": "Volkswagen", "modelo": "Golf", "anos": ["2010 a 2014"]},
        {"marca": "Volkswagen", "modelo": "Golf GTI", "anos": ["2006 a 2014"]},
        {"marca": "Volkswagen", "modelo": "Jetta", "anos": ["2006 a 2010"]},
        {"marca": "Volkswagen", "modelo": "Passat", "anos": ["2006 a 2010"]},
        {"marca": "Volkswagen", "modelo": "Passat CC/CC", "anos": ["2009 a 2014"]},
        {"marca": "Volkswagen", "modelo": "Tiguan", "anos": ["2009 a 2014"]},
    ],
    "254-1": [
        {
            "marca": "Land Rover",
            "modelo": "Range Rover Evoque 2.0 gasolina",
            "anos": ["2012 a 2018"],
            "restrições": ["confirmar LR057235, LR044427 ou LR026192 na peça instalada"],
        }
    ],
    "319": [
        {"marca": "Chevrolet", "modelo": "Meriva Easytronic", "anos": ["2008 a 2012"], "restrições": ["somente sistema Easytronic com referência G1D500201"]},
    ],
    "268": [
        {"marca": "Mercedes-Benz", "modelo": "Classe C W202", "anos": ["1994 a 2000"]},
        {"marca": "Mercedes-Benz", "modelo": "CLK W208", "anos": ["1997 a 2003"]},
        {"marca": "Mercedes-Benz", "modelo": "Classe E W124", "anos": ["1994 a 1995"]},
        {"marca": "Mercedes-Benz", "modelo": "Classe E W210", "anos": ["1995 a 2003"]},
        {"marca": "Mercedes-Benz", "modelo": "Classe E W211", "anos": ["2002 a 2006"]},
        {"marca": "Mercedes-Benz", "modelo": "Classe S W220", "anos": ["1998 a 2005"]},
    ],
    "275-5": [
        {"marca": "BMW", "modelo": "528i F10", "anos": ["2012 a 2016"]},
        {"marca": "BMW", "modelo": "535i F10", "anos": ["2010 a 2016"]},
        {"marca": "BMW", "modelo": "535i GT F07", "anos": ["2010 a 2012"]},
        {"marca": "BMW", "modelo": "550i F10", "anos": ["2011 a 2012"]},
        {"marca": "BMW", "modelo": "550i GT F07", "anos": ["2010 a 2012"]},
    ],
    "275-19": [
        {"marca": "BMW", "modelo": "528i F10", "anos": ["2012 a 2016"]},
        {"marca": "BMW", "modelo": "535i F10", "anos": ["2010 a 2016"]},
        {"marca": "BMW", "modelo": "535i GT F07", "anos": ["2010 a 2012"]},
        {"marca": "BMW", "modelo": "550i F10", "anos": ["2011 a 2012"]},
        {"marca": "BMW", "modelo": "550i GT F07", "anos": ["2010 a 2012"]},
    ],
    "280": [
        {"marca": "Chevrolet", "modelo": "Cruze 1.8 16V", "anos": ["2012 a 2016"]},
    ],
    "280-K": [
        {"marca": "Chevrolet", "modelo": "Aveo e Aveo5", "anos": ["2009 a 2011"]},
        {"marca": "Chevrolet", "modelo": "Cruze L e LS", "anos": ["2011 a 2015"]},
        {"marca": "Chevrolet", "modelo": "Cruze Limited", "anos": ["2016"]},
        {"marca": "Chevrolet", "modelo": "Sonic", "anos": ["2012 a 2018"]},
    ],
    "287": [
        {"marca": "Mitsubishi", "modelo": "Pajero TR4 2.0 16V", "anos": ["2000 a 2008"]},
    ],
    "289": [
        {"marca": "Mitsubishi", "modelo": "Pajero TR4 2.0 16V", "anos": ["2000 a 2008"]},
    ],
    "290": [
        {"marca": "Citroën", "modelo": "Berlingo", "anos": ["2008 a 2021"]},
        {"marca": "Citroën", "modelo": "C3", "anos": ["2001 a 2010"]},
        {"marca": "Citroën", "modelo": "C5", "anos": ["2001 a 2004"]},
        {"marca": "Citroën", "modelo": "Xsara", "anos": ["1998 a 2000"]},
        {"marca": "Citroën", "modelo": "Xsara Picasso", "anos": ["2000 a 2012"]},
        {"marca": "Peugeot", "modelo": "1007", "anos": ["2005 a 2009"]},
        {"marca": "Peugeot", "modelo": "206", "anos": ["1998 a 2009"]},
        {"marca": "Peugeot", "modelo": "206 Cabriolet", "anos": ["2000 a 2007"]},
        {"marca": "Peugeot", "modelo": "207 1.4/1.6", "anos": ["2008 a 2015"]},
        {"marca": "Peugeot", "modelo": "307", "anos": ["2001 a 2011"]},
        {"marca": "Peugeot", "modelo": "307 Cabriolet", "anos": ["2003 a 2009"]},
        {"marca": "Peugeot", "modelo": "406", "anos": ["1999 a 2004"]},
        {"marca": "Peugeot", "modelo": "406 Coupé", "anos": ["1997 a 2005"]},
        {"marca": "Peugeot", "modelo": "807", "anos": ["2002 a 2010"]},
    ],
    "294": [
        {"marca": "Citroën", "modelo": "C4 I 1.6 THP", "anos": ["2008 a 2011"]},
        {"marca": "Citroën", "modelo": "C4 II 1.6 THP", "anos": ["2009 a 2016"]},
        {"marca": "Citroën", "modelo": "C4 Picasso I 1.6 16V THP", "anos": ["2008 a 2013"]},
        {"marca": "Citroën", "modelo": "DS3 1.6 THP/Racing", "anos": ["2010 a 2015"]},
        {"marca": "Peugeot", "modelo": "207 1.6 THP", "anos": ["2006 a 2013"]},
        {"marca": "Peugeot", "modelo": "208 1.6 THP/GTi", "anos": ["2012 a 2019"]},
        {"marca": "Peugeot", "modelo": "3008 I 1.6 THP", "anos": ["2009 a 2016"]},
        {"marca": "Peugeot", "modelo": "308 I/CC/SW 1.6 THP", "anos": ["2007 a 2014"]},
        {"marca": "Peugeot", "modelo": "308 II/SW II 1.6 THP", "anos": ["2013 a 2021"]},
        {"marca": "Peugeot", "modelo": "5008 I 1.6 THP", "anos": ["2009 a 2017"]},
        {"marca": "Peugeot", "modelo": "508/SW 1.6 THP", "anos": ["2010 a 2018"]},
        {"marca": "Peugeot", "modelo": "RCZ 1.6 THP", "anos": ["2010 a 2015"]},
    ],
    "307-2": [
        {"marca": "Citroën", "modelo": "C4 Lounge 1.6 THP", "anos": ["2012 a 2021"]},
        {"marca": "Citroën", "modelo": "DS3, DS4 e DS5 1.6 THP", "anos": ["2012 a 2017"]},
        {"marca": "Peugeot", "modelo": "208 1.6 THP", "anos": ["2015 a 2020"]},
        {"marca": "Peugeot", "modelo": "308 e 408 1.6 THP", "anos": ["2012 a 2021"]},
        {"marca": "Peugeot", "modelo": "3008, 508 e RCZ 1.6 THP", "anos": ["2011 a 2018"]},
        {"marca": "Mini", "modelo": "Cooper, One e Cooper S 1.6", "anos": ["2008 a 2013"]},
    ],
    "309.1": [
        {"marca": "Hyundai", "modelo": "HR 2.5 16V Diesel Euro 5", "anos": ["2012 a 2017"]},
        {"marca": "Kia", "modelo": "Bongo K2500 2.5 16V Diesel Euro 5", "anos": ["2013 a 2022"]},
        {"marca": "Mercedes-Benz", "modelo": "Sprinter 311 CDI 2.2 16V Euro 5", "anos": ["2012 a 2017"]},
        {"marca": "Mercedes-Benz", "modelo": "Sprinter 415 CDI 2.2 16V Euro 5", "anos": ["2012 a 2017"]},
        {"marca": "Mercedes-Benz", "modelo": "Sprinter 515 CDI 2.2 16V Euro 5", "anos": ["2012 a 2017"]},
    ],
    "312": [
        {
            "marca": "Peugeot",
            "modelo": "307 com câmbio automático AL4/4AT de 4 marchas",
            "anos": ["2004 a 2012"],
            "restrições": ["comparar a peça antiga com as dimensões de 210 × 47,5 mm e abertura de 19 × 43 mm"],
        },
        {
            "marca": "Citroën",
            "modelo": "C4 Hatch com câmbio automático AL4/4AT de 4 marchas",
            "anos": ["2009 a 2012"],
            "restrições": ["comparar a peça antiga com as dimensões de 210 × 47,5 mm e abertura de 19 × 43 mm"],
        },
        {
            "marca": "Citroën",
            "modelo": "C4 Pallas com câmbio automático AL4/4AT de 4 marchas",
            "anos": ["2008 a 2013"],
            "restrições": ["comparar a peça antiga com as dimensões de 210 × 47,5 mm e abertura de 19 × 43 mm"],
        },
    ],
    "336": [
        {"marca": "BMW", "modelo": "Série 1 F20/F21", "anos": ["2012 a 2019"]},
        {"marca": "BMW", "modelo": "Série 2 F22/F23", "anos": ["2014 a 2021"]},
        {"marca": "BMW", "modelo": "Série 3 F30/F31", "anos": ["2012 a 2019"]},
        {"marca": "BMW", "modelo": "Série 4 F32/F33/F36", "anos": ["2013 a 2019"]},
        {"marca": "BMW", "modelo": "Série 5 F10/F11", "anos": ["2009 a 2017"]},
        {"marca": "BMW", "modelo": "Série 6 F06/F12/F13", "anos": ["2011 a 2018"]},
        {"marca": "BMW", "modelo": "Série 7 F01/F02", "anos": ["2009 a 2015"]},
        {"marca": "BMW", "modelo": "X1 F48", "anos": ["2015 a 2022"]},
        {"marca": "BMW", "modelo": "X3 F25", "anos": ["2011 a 2016"]},
        {"marca": "BMW", "modelo": "X4 F26", "anos": ["2015 a 2016"]},
        {"marca": "BMW", "modelo": "X5 F15", "anos": ["2014 a 2016"]},
        {"marca": "BMW", "modelo": "X6 F16", "anos": ["2015 a 2016"]},
    ],
    "362": [
        {"marca": "Ford", "modelo": "Focus 2.0 16V Duratec a gasolina", "anos": ["2008 a 2009"]},
    ],
    "365": [
        {"marca": "Ford", "modelo": "Focus 2.0 16V Duratec a gasolina", "anos": ["2008 a 2009"]},
    ],
    "368": [
        {"marca": "Pontiac", "modelo": "Vibe 1.8", "anos": ["2003 a 2006"]},
        {"marca": "Toyota", "modelo": "Corolla 1.8", "anos": ["2003 a 2008"]},
        {"marca": "Toyota", "modelo": "Matrix 1.8", "anos": ["2003 a 2006"]},
    ],
    "371": [
        {"marca": "Dodge", "modelo": "Journey", "anos": ["2009 a 2018"]},
        {"marca": "Fiat", "modelo": "Freemont", "anos": ["2012 a 2016"]},
    ],
    "369": [
        {"marca": "Dodge", "modelo": "Ram 1500 3.0 V6 Diesel", "anos": ["2014 a 2019"]},
        {"marca": "Jeep", "modelo": "Grand Cherokee 3.0 V6 Diesel", "anos": ["2014 a 2019"]},
    ],
    "372": [
        {"marca": "Fiat", "modelo": "Freemont 2.4 16V", "anos": ["2011 a 2017"], "restrições": ["confirmar os códigos 53366451 ou CL9952, o desenho do gargalo e os diâmetros das conexões da peça instalada"]},
        {"marca": "Dodge", "modelo": "Journey 2.7 V6", "anos": ["2009 a 2012"], "restrições": ["confirmar os códigos 53366451 ou CL9952, o desenho do gargalo e os diâmetros das conexões da peça instalada"]},
        {"marca": "Dodge", "modelo": "Journey 3.6 V6", "anos": ["2011 a 2018"], "restrições": ["confirmar a referência CL9952, o desenho do gargalo e os diâmetros das conexões da peça instalada"]},
        {"marca": "Dodge", "modelo": "Durango 3.6 V6", "anos": ["2011 a 2016"], "restrições": ["confirmar a referência CL9952, o desenho do gargalo e os diâmetros das conexões da peça instalada"]},
        {"marca": "Chrysler", "modelo": "300C 3.6 V6", "anos": ["2011 a 2016"], "restrições": ["confirmar a referência CL9952, o desenho do gargalo e os diâmetros das conexões da peça instalada"]},
        {"marca": "Jeep", "modelo": "Grand Cherokee 3.6 V6", "anos": ["2010 a 2017"], "restrições": ["confirmar a referência CL9952, o desenho do gargalo e os diâmetros das conexões da peça instalada"]},
    ],
    "417-2": [
        {"marca": "Honda", "modelo": "Accord", "anos": ["1994 a 2016"], "restrições": ["tampa metálica de 1,1 bar; conferir o código da peça instalada"]},
        {"marca": "Honda", "modelo": "City", "anos": ["2009 a 2016"], "restrições": ["tampa metálica de 1,1 bar; conferir o código da peça instalada"]},
        {"marca": "Honda", "modelo": "Civic", "anos": ["1992 a 2016"], "restrições": ["tampa metálica de 1,1 bar; conferir o código da peça instalada"]},
        {"marca": "Honda", "modelo": "CR-V", "anos": ["2002 a 2016"], "restrições": ["tampa metálica de 1,1 bar; conferir o código da peça instalada"]},
        {"marca": "Honda", "modelo": "Fit", "anos": ["2003 a 2016"], "restrições": ["tampa metálica de 1,1 bar; conferir o código da peça instalada"]},
    ],
    "435-1": [
        {"marca": "Hyundai", "modelo": "ix35", "anos": ["2010 a 2015"]},
        {"marca": "Hyundai", "modelo": "Tucson", "anos": ["2009 a 2015"]},
    ],
    "435-2": [
        {"marca": "Hyundai", "modelo": "ix35", "anos": ["2010 a 2015"]},
        {"marca": "Hyundai", "modelo": "Tucson", "anos": ["2009 a 2015"]},
    ],
    "435-K": [
        {"marca": "Hyundai", "modelo": "ix35", "anos": ["2010 a 2015"]},
        {"marca": "Hyundai", "modelo": "Tucson", "anos": ["2009 a 2015"]},
    ],
    "451-1": [
        {"marca": "Ford", "modelo": "Fusion", "anos": ["2013 a 2017"]},
    ],
    "451-2": [
        {"marca": "Ford", "modelo": "Fusion", "anos": ["2013 a 2017"]},
    ],
    "451-K": [
        {"marca": "Ford", "modelo": "Fusion", "anos": ["2013 a 2017"]},
    ],
    "457-1": [
        {"marca": "Mercedes-Benz", "modelo": "C180 W205", "anos": ["2014 a 2021"]},
        {"marca": "Mercedes-Benz", "modelo": "C200 W205", "anos": ["2014 a 2021"]},
        {"marca": "Mercedes-Benz", "modelo": "C250 W205", "anos": ["2014 a 2021"]},
        {"marca": "Mercedes-Benz", "modelo": "C300 W205", "anos": ["2014 a 2021"]},
        {"marca": "Mercedes-Benz", "modelo": "C400 W205", "anos": ["2014 a 2021"]},
        {"marca": "Mercedes-Benz", "modelo": "C450 W205", "anos": ["2014 a 2021"]},
        {"marca": "Mercedes-Benz", "modelo": "C43 AMG W205", "anos": ["2014 a 2021"]},
    ],
    "457-2": [
        {"marca": "Mercedes-Benz", "modelo": "C180 W205", "anos": ["2014 a 2021"]},
        {"marca": "Mercedes-Benz", "modelo": "C200 W205", "anos": ["2014 a 2021"]},
        {"marca": "Mercedes-Benz", "modelo": "C250 W205", "anos": ["2014 a 2021"]},
        {"marca": "Mercedes-Benz", "modelo": "C300 W205", "anos": ["2014 a 2021"]},
        {"marca": "Mercedes-Benz", "modelo": "C400 W205", "anos": ["2014 a 2021"]},
        {"marca": "Mercedes-Benz", "modelo": "C450 W205", "anos": ["2014 a 2021"]},
        {"marca": "Mercedes-Benz", "modelo": "C43 AMG W205", "anos": ["2014 a 2021"]},
    ],
    "471": [
        {"marca": "Honda", "modelo": "CG 160 Fan", "anos": ["2015 a 2022"]},
        {"marca": "Honda", "modelo": "CG 160 Titan", "anos": ["2015 a 2022"]},
    ],
    "520-1": MERCEDES_W205_WINDOW_SWITCH_VEHICLES,
    "520-2": MERCEDES_W205_WINDOW_SWITCH_VEHICLES,
    "520-3": MERCEDES_W205_WINDOW_SWITCH_VEHICLES,
    "521": [
        {
            "marca": "Audi",
            "modelo": "Q5 2.0",
            "anos": ["2018 a 2020"],
            "restrições": ["confirmar o OEM 80A121081S"],
        }
    ],
}

FINAL_PRODUCT_NAME_OVERRIDES = {
    "001": "Interruptor térmico da ventoinha do radiador Honda, OEM 37760-MT2-003",
    "4-2": "Manopla preta da alavanca do câmbio manual de 6 marchas Ford Transit",
    "47": "Sensor de pressão dos pneus TPMS de 433 MHz para Hyundai Creta, Tucson e Sonata",
    "59": "Sensor de posição da borboleta TPS para Honda Civic 1996 a 2000",
    "70": "Roda livre automática do cubo dianteiro Nissan Frontier e X-Terra, OEM 40260-1S700",
    "66": "Tampa e flange com filtro interno do módulo da bomba de combustível Land Rover",
    "99": "Ferramenta de ativação e reaprendizado de sensores TPMS EL-50449",
    "99-1": "Ferramenta de ativação e reaprendizado de sensores TPMS EL-50448",
    "120": "Conjunto de juntas com telas-filtro da válvula solenoide VTEC Honda Civic",
    "129": "Detector eletrônico de parede para tubos, madeira, fios energizados e metais",
    "129-3": "Detector eletrônico de parede para tubos, madeira, fios energizados e metais, com pilhas",
    "137": "Válvula mecânica limitadora de pressão do sistema common rail Bosch",
    "151-K6": "Jogo com 6 velas de ignição Bosch de dupla platina ZR5TPP33 para BMW",
    "155": "Conversor de tensão da bateria auxiliar Mercedes-Benz Classe C W205",
    "167": "Extensor angular de 90° para válvula de pneu de carro, motocicleta e bicicleta",
    "167-K": "Par de extensores angulares de 90° para válvula de pneu de carro, motocicleta e bicicleta",
    "196": "Torneira de combustível para motocicletas Suzuki",
    "237": "Reator eletrônico do farol de xenônio D3S/D3R Mercedes-Benz",
    "240": "Módulo eletrônico de alimentação e controle do farol Mercedes-Benz",
    "268": "Estrela cromada do capô Mercedes-Benz",
    "275-5": "Acabamento bege do puxador interno da porta dianteira esquerda BMW Série 5 F10/F11",
    "280": "Retentor dianteiro do comando de válvulas Chevrolet Cruze 1.8",
    "280-K": "Conjunto com dois retentores dianteiros do comando de válvulas Chevrolet",
    "287": "Medidor de massa de ar MAF para Mitsubishi Pajero TR4 2.0 16V",
    "290": "Módulo BSM B5 de fusíveis e relés para Peugeot e Citroën",
    "294": "Tubo de retorno de óleo da turbina para motores 1.6 THP Peugeot e Citroën",
    "305-2-K": "Par de luzes auxiliares de LED tipo parafuso de 18 mm, acabamento preto",
    "306-2-K": "Par de luzes auxiliares de LED tipo parafuso de 23 mm, acabamento preto",
    "306-10-K": "Par de luzes auxiliares de LED tipo parafuso de 23 mm, acabamento prata",
    "307-2": "Chicote adaptador da carcaça da válvula termostática para motor 1.6 THP",
    "309.1": "Válvula reguladora de pressão IMV da bomba de alta pressão",
    "214": "Comutador elétrico de ignição e partida Hyundai e Kia, referência 93110-3S000",
    "225": "Válvula eletromagnética de purga do cânister Volkswagen e Audi, referências 06E906517A e 0280142431",
    "231": "Botão do freio de estacionamento eletrônico Honda HR-V",
    "232-1": "Par de puxadores internos dianteiros pretos das portas para BMW Série 3 e Série 4",
    "232-2": "Par de puxadores internos traseiros pretos das portas para BMW Série 3 F30/F31",
    "232-5": "Par de puxadores internos dianteiros bege das portas para BMW Série 3 e Série 4",
    "232-6": "Par de puxadores internos traseiros bege das portas para BMW Série 3 F30/F31",
    "239-K": "Par de refletores do para-choque traseiro BMW Série 3 F30",
    "275-19": "Puxador interno da porta do motorista, cor ostra, para BMW Série 5 F10/F07",
    "326": "Difusor de ar traseiro do console central Volkswagen Jetta Variant",
    "367": "Roda livre manual do cubo dianteiro Ford Ranger 4x4",
    "367-K": "Par de rodas livres manuais dos cubos dianteiros Ford Ranger 4x4",
    "425-1": "Mangueira direita do intercooler Ford Ranger 3.2, OEM AB39-6K683-DD",
    "445": "Mini cofre de aço para chaves com senha ajustável",
    "520-1": "Botão do comando do vidro elétrico do motorista Mercedes-Benz Classe C W205, acabamento prata e preto",
    "520-2": "Botão do comando do vidro elétrico do motorista Mercedes-Benz Classe C W205, acabamento prata e marrom",
    "520-3": "Botão do comando do vidro elétrico do motorista Mercedes-Benz Classe C W205, acabamento prata e bege",
    "336": "Capa vermelha de reposição do botão de partida e desligamento do motor BMW série F",
    "345": "Testador de faísca para sistemas de ignição de carros, motocicletas e barcos",
    "362": "Conjunto de flange e tampa do módulo da bomba de combustível Ford Focus",
    "365": "Flange superior do módulo da bomba de combustível Ford Focus",
    "369": "Tubo de desvio do líquido de arrefecimento da bomba d'água",
    "371": "Conector superior da mangueira do radiador com tampa para Fiat Freemont e Dodge Journey",
    "372": "Conector com gargalo de enchimento da mangueira superior do radiador, sem tampa",
    "382": "Bomba mecânica de combustível para motor de popa Johnson e Evinrude",
    "394-1": "Presilha metálica de retenção da mangueira do intercooler de 50 mm",
    "394-2": "Presilha metálica de retenção da mangueira do intercooler de 45 mm",
    "396": "Conjunto com 100 presilhas automotivas variadas GN1667",
    "397": "Conjunto com 100 presilhas automotivas variadas em estojo GN1667",
    "407": "Sensor Wi-Fi de temperatura e umidade compatível com Alexa e Google Home",
    "417-2": "Tampa do radiador Honda de 1,1 bar",
    "408": "Conjunto com 415 presilhas automotivas, estojo e ferramenta extratora GN1683",
    "432": "Escova de aço profissional para roçadeira com encaixe universal",
    "435-1": "Repetidor de seta com LED do retrovisor direito Hyundai ix35/Tucson",
    "435-2": "Repetidor de seta com LED do retrovisor esquerdo Hyundai ix35/Tucson",
    "438": "Protetor facial transparente antiembaçante para uso com roçadeira",
    "444-K2-1": "Conjunto de disco enxada e escova de aço para roçadeira, 2 peças",
    "444-K2-2": "Conjunto de escova de aço e lâmina tipo canivete para roçadeira, 2 peças",
    "444-K3": "Conjunto de disco enxada, escova de aço e lâmina tipo canivete para roçadeira, 3 peças",
    "453": "Lâmina articulada tipo canivete de 6 pontas para roçadeira",
    "451-1": "Bandeja inferior dianteira direita da suspensão Ford Fusion",
    "451-2": "Bandeja inferior dianteira esquerda da suspensão Ford Fusion",
    "451-K": "Par de bandejas inferiores dianteiras da suspensão Ford Fusion",
    "457-1": "Grade dianteira do para-choque Mercedes-Benz Classe C W205, acabamento preto brilhante",
    "457-2": "Grade dianteira do para-choque Mercedes-Benz Classe C W205, acabamento cromado",
    "471": "Painel de instrumentos digital para Honda CG 160 Titan e Fan 2015 a 2022",
    "462": "Sensor inteligente de bateria IBS Mopar 04692269AI",
    "521": "Mangueira de retorno do reservatório de expansão do líquido de arrefecimento Audi Q5 2.0, OEM 80A121081S",
}

FINAL_WHAT_IT_IS_OVERRIDES = {
    "66": "É um conjunto de flange e elemento filtrante instalado dentro do tanque de combustível.",
    "99": "É uma ferramenta portátil que ativa sensores de pressão dos pneus durante o procedimento de reaprendizado.",
    "99-1": "É uma ferramenta portátil que ativa sensores de pressão dos pneus durante o procedimento de reaprendizado da linha GM.",
    "120": "É um conjunto de duas juntas de vedação com telas-filtro para a válvula solenoide VTEC.",
    "129": "É um detector eletrônico portátil usado para localizar materiais e fiação ocultos em paredes.",
    "129-3": "É um detector eletrônico portátil usado para localizar materiais e fiação ocultos em paredes.",
    "137": "É uma válvula de segurança roscada na extremidade do tubo distribuidor do sistema common rail.",
    "155": "É um módulo eletrônico de armazenamento e conversão de energia que exerce a função de apoio da bateria auxiliar.",
    "167": "É um adaptador metálico angular que prolonga o acesso à válvula de enchimento do pneu.",
    "167-K": "É um conjunto de dois adaptadores metálicos angulares que prolongam o acesso às válvulas de enchimento dos pneus.",
    "196": "É uma torneira mecânica que abre ou fecha a passagem de combustível entre o tanque e o motor da motocicleta.",
    "214": "É um comutador elétrico acionado pelo cilindro da chave para selecionar acessórios, ignição e partida.",
    "225": "É uma válvula solenoide do sistema EVAP que controla a passagem dos vapores de combustível armazenados no cânister.",
    "47": "É um sensor eletrônico instalado na roda para medir a pressão do pneu e transmitir a leitura ao veículo.",
    "59": "É um sensor eletrônico acoplado ao corpo de borboleta para informar sua posição à unidade de controle do motor.",
    "70": "É uma roda livre automática que acopla ou desacopla o cubo da roda dianteira ao semieixo do sistema 4x4.",
    "231": "É um botão de reposição para o comando do freio de estacionamento eletrônico.",
    "232-1": "É um conjunto com os puxadores internos dianteiros direito e esquerdo das portas, na cor preta.",
    "232-2": "É um conjunto com os puxadores internos traseiros direito e esquerdo das portas, na cor preta.",
    "232-5": "É um conjunto com os puxadores internos dianteiros direito e esquerdo das portas, na cor bege.",
    "232-6": "É um conjunto com os puxadores internos traseiros direito e esquerdo das portas, na cor bege.",
    "239-K": "É um par de refletores passivos para montagem nos lados direito e esquerdo do para-choque traseiro.",
    "275-19": "É um puxador interno de reposição para a porta do motorista, com acabamento na cor ostra.",
    "326": "É uma saída de ar com aletas direcionais para a parte traseira do console central.",
    "367": "É uma roda livre manual que acopla ou desacopla o cubo dianteiro ao semieixo do sistema 4x4.",
    "367-K": "É um par de rodas livres manuais para os cubos dianteiros do sistema 4x4.",
    "425-1": "É uma mangueira pressurizada do sistema de admissão, instalada entre o intercooler e os demais componentes do circuito de ar.",
    "445": "É uma caixa metálica compacta com fechamento por combinação para guardar chaves e pequenos objetos.",
    "520-1": "É um botão de reposição do comando do vidro elétrico localizado na porta do motorista.",
    "520-2": "É um botão de reposição do comando do vidro elétrico localizado na porta do motorista.",
    "520-3": "É um botão de reposição do comando do vidro elétrico localizado na porta do motorista.",
    "237": "É uma unidade eletrônica de alimentação e controle da lâmpada de xenônio do farol.",
    "240": "É uma unidade eletrônica instalada no conjunto óptico para controlar a alimentação do farol.",
    "268": "É um emblema tridimensional cromado para montagem no ponto original do capô.",
    "280": "É um retentor radial instalado ao redor do eixo do comando de válvulas.",
    "280-K": "É um par de retentores radiais destinado à vedação dos eixos dos comandos de válvulas.",
    "290": "É um módulo elétrico que reúne fusíveis, relés e conexões para distribuir e proteger a alimentação do veículo.",
    "294": "É um tubo rígido do circuito de lubrificação responsável pelo retorno de óleo da turbina.",
    "307-2": "É um chicote elétrico que interliga o conector do veículo aos componentes elétricos da carcaça termostática.",
    "305-2-K": "É um par de luzes auxiliares compactas de LED com corpo roscado para montagem em furo de 18 mm.",
    "306-2-K": "É um par de luzes auxiliares compactas de LED com corpo roscado para montagem em furo de 23 mm.",
    "306-10-K": "É um par de luzes auxiliares compactas de LED com corpo roscado para montagem em furo de 23 mm.",
    "309.1": "É uma válvula eletromagnética dosadora instalada na bomba de alta pressão do sistema de combustível.",
    "336": "É uma capa plástica de reposição para a superfície do botão de partida e desligamento do motor.",
    "345": "É uma ferramenta de diagnóstico que permite observar e ajustar a frequência da centelha do sistema de ignição.",
    "362": "É um conjunto instalado na parte superior do módulo da bomba de combustível dentro do tanque.",
    "365": "É uma tampa e flange plástica instalada na parte superior do módulo da bomba dentro do tanque.",
    "369": "É um tubo rígido que forma uma passagem de desvio no circuito de arrefecimento do motor.",
    "371": "É um conjunto do sistema de arrefecimento formado pelo conector superior da mangueira, pelo bocal de enchimento e pela tampa de pressão.",
    "372": "É um conector plástico instalado em linha na mangueira superior do radiador, com gargalo lateral para a tampa pressurizada e bico de retorno ao reservatório de expansão.",
    "382": "É uma bomba de diafragma acionada pelos pulsos de pressão e vácuo produzidos pelo motor de popa.",
    "394-1": "É uma trava de arame metálico para o acoplamento rápido da mangueira do intercooler.",
    "394-2": "É uma trava de arame metálico para o acoplamento rápido da mangueira do intercooler.",
    "396": "É um sortimento de fixadores plásticos de pressão para acabamento e carroceria.",
    "397": "É um sortimento de fixadores plásticos de pressão acondicionado em estojo organizador.",
    "408": "É um sortimento de fixadores plásticos de pressão acompanhado de ferramenta extratora e estojo organizador.",
    "407": "É um sensor eletrônico conectado à rede Wi-Fi para monitorar temperatura e umidade do ambiente.",
    "432": "É uma escova rotativa de fios de aço projetada para ser acoplada a uma roçadeira.",
    "438": "É um equipamento de proteção facial com viseira transparente e carneira ajustável.",
    "444-K2-1": "É um conjunto de dois implementos de corte e limpeza para roçadeira: disco enxada e escova de aço.",
    "444-K2-2": "É um conjunto de dois implementos para roçadeira: escova de aço e lâmina articulada tipo canivete.",
    "444-K3": "É um conjunto de três implementos para roçadeira: disco enxada, escova de aço e lâmina articulada tipo canivete.",
    "453": "É uma lâmina rotativa articulada de seis pontas para montagem em roçadeira.",
    "451-1": "É uma bandeja inferior da suspensão dianteira destinada ao lado direito do veículo.",
    "451-2": "É uma bandeja inferior da suspensão dianteira destinada ao lado esquerdo do veículo.",
    "451-K": "É um conjunto com as bandejas inferiores direita e esquerda da suspensão dianteira.",
    "471": "É um painel eletrônico que reúne o mostrador e os indicadores do quadro de instrumentos da motocicleta.",
}

# Revisões finais que prevalecem sobre textos coletados do cadastro, dos anúncios
# e dos relatórios de pesquisa quando esses textos descrevem outro tipo de peça.
FINAL_CONTENT_OVERRIDES = {
    "47": {
        "para_que_serve": ["Monitora a pressão do pneu e envia a leitura ao sistema TPMS do veículo."],
        "características_técnicas": ["Tipo: sensor direto de pressão dos pneus.", "Frequência de comunicação: 433 MHz."],
        "modo_de_funcionamento": ["O transdutor mede a pressão dentro do pneu e transmite os dados por radiofrequência ao receptor do veículo."],
        "instalação": ["Instale na roda com o pneu desmontado, faça o balanceamento e execute o procedimento de programação ou reaprendizado do TPMS."],
    },
    "59": {
        "para_que_serve": ["Informa à unidade de controle do motor o ângulo de abertura da borboleta de aceleração."],
        "características_técnicas": ["Tipo: sensor de posição da borboleta TPS."],
        "modo_de_funcionamento": ["A rotação do eixo da borboleta altera o sinal elétrico enviado à unidade de controle."],
        "instalação": ["Monte no eixo do corpo de borboleta na mesma posição da peça original, conecte o chicote e realize a verificação ou calibração prevista para o veículo."],
    },
    "70": {
        "para_que_serve": ["Acopla a roda dianteira ao semieixo quando a tração 4x4 é utilizada e permite o desacoplamento no modo 4x2."],
        "características_técnicas": ["Tipo: roda livre automática.", "Posição: cubo da roda dianteira.", "Acabamento da face: preto.", "Material da face: alumínio."],
        "modo_de_funcionamento": ["O mecanismo interno trava ou libera automaticamente o cubo conforme o acionamento e o sentido de esforço do sistema de tração."],
        "instalação": ["Instale no cubo dianteiro com estrias, vedações e fixadores alinhados. Confira o engate e o desengate do sistema 4x4 antes do uso."],
    },
    "66": {
        "para_que_serve": ["Filtra o combustível e fecha o módulo da bomba, mantendo suas conexões e a vedação do tanque."],
        "características_técnicas": ["Flange superior moldada em plástico.", "Corpo filtrante cilíndrico integrado.", "Conexões para o circuito de combustível."],
        "modo_de_funcionamento": ["O combustível atravessa o elemento filtrante antes de seguir pelo circuito, enquanto a flange posiciona o conjunto e mantém a vedação do tanque."],
        "instalação": ["Despressurize o sistema, desligue a bateria e remova o módulo em ambiente ventilado. Instale com a vedação adequada e verifique pressão e vazamentos."],
    },
    "99": {
        "para_que_serve": ["Permite registrar a posição dos sensores TPMS no veículo e ajuda a identificar sensores que deixaram de responder."],
        "características_técnicas": ["Modelo: EL-50449.", "Alimentação: bateria de 9 V.", "Operação sem conexão física com o veículo."],
        "modo_de_funcionamento": ["Emite um sinal de ativação próximo ao sensor, que transmite sua identificação ao veículo para registro da posição da roda."],
        "instalação": ["Coloque uma bateria de 9 V, ative o modo de aprendizagem TPMS do veículo e aproxime a ferramenta da lateral do pneu, junto à válvula, seguindo a sequência indicada."],
        "equipamentos": ["Ford Fusion — 2008 a 2015.", "Ford F-150, F-250, F-350, F-450 e F-550 — 2008 a 2015.", "Ford Mustang — 2007 a 2015.", "Ford Edge — 2007 a 2014.", "Ford Expedition — 2008 a 2015.", "Ford Explorer — 2006 a 2016.", "Ford Taurus, Fiesta e Focus — 2008 a 2015.", "Ford Ranger — 2006 a 2011.", "Lincoln Navigator — 2008 a 2015.", "Lincoln Mark LT — 2007 a 2008.", "Mercury Mariner, Grand Marquis e Milan — 2006 a 2011."],
    },
    "99-1": {
        "para_que_serve": ["Permite associar cada sensor TPMS à posição da roda e ajuda a localizar sensores que deixaram de responder."],
        "características_técnicas": ["Modelo: EL-50448.", "Alimentação: bateria de 9 V."],
        "modo_de_funcionamento": ["Envia um sinal de ativação próximo à roda, fazendo o sensor TPMS transmitir sua identificação ao receptor do veículo."],
        "instalação": ["Coloque uma bateria de 9 V, ative o modo de reaprendizado do veículo e acione os sensores junto às válvulas, na sequência indicada."],
        "equipamentos": ["Veículos da linha GM equipados com sistema TPMS compatível com o procedimento EL-50448."],
    },
    "120": {
        "para_que_serve": ["Veda as galerias de óleo e filtra partículas antes da passagem do óleo pelo circuito de acionamento VTEC."],
        "características_técnicas": ["Quantidade: 2 juntas.", "Material do corpo: elastômero.", "Telas-filtro metálicas integradas."],
        "modo_de_funcionamento": ["As juntas são comprimidas entre o solenoide e o motor para separar as galerias de óleo, enquanto as telas retêm partículas do circuito."],
        "instalação": ["Com o motor frio, remova o solenoide, limpe as superfícies e galerias e instale cada junta na orientação correta. Aperte conforme o manual e verifique vazamentos de óleo."],
    },
    "137": {
        "para_que_serve": ["Protege o sistema de alta pressão, permitindo o alívio do combustível quando a pressão ultrapassa o limite da válvula."],
        "características_técnicas": ["Construção: corpo metálico roscado.", "Sextavado para montagem.", "Mecanismo interno acionado por mola."],
        "modo_de_funcionamento": ["Permanece fechada na faixa normal de trabalho e abre mecanicamente quando a pressão supera sua calibração."],
        "instalação": ["Despressurize completamente o sistema diesel, limpe a região e evite contaminação. Instale com o torque e a vedação especificados e realize o teste de vazamento e diagnóstico."],
    },
    "155": {
        "para_que_serve": ["Estabiliza a alimentação elétrica durante variações de carga e durante o funcionamento do sistema de partida e parada automática."],
        "características_técnicas": ["Módulo eletrônico de estado sólido.", "Armazenamento capacitivo de energia.", "Carcaça plástica preta.", "Dissipador e suporte de alumínio.", "Conector multipinos."],
        "modo_de_funcionamento": ["Armazena energia em capacitores e a libera de forma controlada para reduzir quedas de tensão no sistema elétrico."],
        "instalação": ["Instale no local do módulo original, com a ignição desligada e seguindo o procedimento de desconexão da bateria. Fixe o módulo e conecte o plugue original."],
    },
    "167": {
        "para_que_serve": ["Facilita o acesso ao bico do pneu quando a válvula está em posição estreita ou difícil de alcançar."],
        "características_técnicas": ["Material: metal.", "Acabamento: prateado.", "Ângulo: 90°."],
        "modo_de_funcionamento": ["O adaptador prolonga e muda a direção do ponto de enchimento, mantendo a passagem de ar e a vedação da válvula."],
        "instalação": ["Rosqueie o extensor na válvula do pneu e verifique a vedação antes de calibrar."],
    },
    "167-K": {
        "para_que_serve": ["Facilita o acesso aos bicos dos pneus quando as válvulas estão em posição estreita ou difícil de alcançar."],
        "características_técnicas": ["Material: metal.", "Acabamento: prateado.", "Ângulo: 90°.", "Quantidade do conjunto: 2 unidades."],
        "modo_de_funcionamento": ["Cada adaptador prolonga e muda a direção do ponto de enchimento, mantendo a passagem de ar e a vedação da válvula."],
        "instalação": ["Rosqueie cada extensor na válvula do pneu e verifique a vedação antes de calibrar."],
    },
    "196": {
        "para_que_serve": ["Controla manualmente a passagem de combustível do tanque para o sistema de alimentação da motocicleta."],
        "características_técnicas": ["Materiais: liga de zinco e plástico.", "Tipo: torneira mecânica de combustível."],
        "modo_de_funcionamento": ["A alavanca movimenta o obturador interno para abrir ou fechar a passagem do combustível."],
        "instalação": ["Feche a alimentação, substitua a torneira com a vedação adequada, conecte a mangueira e verifique vazamentos antes de operar a motocicleta."],
    },
    "214": {
        "para_que_serve": ["Distribui a alimentação elétrica entre as posições de acessórios, ignição e partida quando a chave é girada."],
        "modo_de_funcionamento": ["O movimento do cilindro da chave gira o mecanismo interno, que fecha os contatos correspondentes a cada posição."],
        "instalação": ["Desligue a bateria, remova os acabamentos da coluna de direção e substitua o comutador no alojamento original. Conecte o plugue de 6 pinos e teste todas as posições antes de remontar."],
    },
    "225": {
        "para_que_serve": ["Controla a purga dos vapores de combustível do cânister para o coletor de admissão, evitando sua liberação direta na atmosfera."],
        "modo_de_funcionamento": ["A unidade de controle do motor energiza a bobina da válvula e abre a passagem em pulsos para dosar os vapores enviados à admissão."],
        "instalação": ["Com a ignição desligada, desconecte o plugue e as duas mangueiras, respeite o sentido de fluxo e instale a válvula na mesma posição. Verifique vedação e códigos de falha do sistema EVAP."],
    },
    "231": {
        "para_que_serve": ["Permite acionar ou liberar o freio de estacionamento eletrônico pelo console do veículo."],
        "características_técnicas": ["Tipo: botão do freio de estacionamento eletrônico.", "Conexão elétrica: 1 terminal."],
        "modo_de_funcionamento": ["Ao ser pressionado, o botão envia o comando elétrico ao módulo do freio de estacionamento."],
        "instalação": ["Remova o acabamento do console, substitua o botão no alojamento original, conecte o terminal e teste o acionamento e a liberação."],
    },
    "232-1": {
        "para_que_serve": ["Substitui os puxadores internos dianteiros e oferece apoio para puxar e fechar as portas."],
        "características_técnicas": ["Material: plástico ABS.", "Cor: preta.", "Fixação: encaixe.", "Quantidade do conjunto: 2 unidades.", "Posições: dianteira direita e dianteira esquerda."],
        "modo_de_funcionamento": ["Cada puxador forma uma superfície rígida de apoio para a mão durante o fechamento da porta."],
        "instalação": ["Remova o acabamento antigo e encaixe cada puxador no lado correspondente, conferindo o alinhamento e o travamento das presilhas."],
    },
    "232-5": {
        "para_que_serve": ["Substitui os puxadores internos dianteiros e oferece apoio para puxar e fechar as portas."],
        "características_técnicas": ["Material: plástico ABS.", "Cor: bege.", "Fixação: encaixe.", "Quantidade do conjunto: 2 unidades.", "Posições: dianteira direita e dianteira esquerda."],
        "modo_de_funcionamento": ["Cada puxador forma uma superfície rígida de apoio para a mão durante o fechamento da porta."],
        "instalação": ["Remova o acabamento antigo e encaixe cada puxador no lado correspondente, conferindo o alinhamento e o travamento das presilhas."],
    },
    "232-2": {
        "para_que_serve": ["Substitui os puxadores internos traseiros e oferece apoio para puxar e fechar as portas."],
        "características_técnicas": ["Material: plástico ABS.", "Cor: preta.", "Fixação: encaixe.", "Quantidade do conjunto: 2 unidades.", "Posições: traseira direita e traseira esquerda."],
        "modo_de_funcionamento": ["Cada puxador forma uma superfície rígida de apoio para a mão durante o fechamento da porta."],
        "instalação": ["Remova o acabamento antigo e encaixe cada puxador no lado correspondente, conferindo o alinhamento e o travamento das presilhas."],
    },
    "232-6": {
        "para_que_serve": ["Substitui os puxadores internos traseiros e oferece apoio para puxar e fechar as portas."],
        "características_técnicas": ["Material: plástico ABS.", "Cor: bege.", "Fixação: encaixe.", "Quantidade do conjunto: 2 unidades.", "Posições: traseira direita e traseira esquerda."],
        "modo_de_funcionamento": ["Cada puxador forma uma superfície rígida de apoio para a mão durante o fechamento da porta."],
        "instalação": ["Remova o acabamento antigo e encaixe cada puxador no lado correspondente, conferindo o alinhamento e o travamento das presilhas."],
    },
    "239-K": {
        "para_que_serve": ["Sinaliza a posição da traseira do veículo ao refletir a luz que incide sobre o para-choque."],
        "características_técnicas": ["Tipo: refletores passivos.", "Posição: para-choque traseiro.", "Quantidade do conjunto: 2 unidades.", "Lados: direito e esquerdo."],
        "modo_de_funcionamento": ["A superfície prismática devolve a luz incidente sem alimentação elétrica."],
        "instalação": ["Remova os refletores usados e fixe cada peça no lado correspondente do para-choque, conferindo encaixes e alinhamento."],
    },
    "237": {
        "para_que_serve": ["Gera a tensão necessária para acender a lâmpada de xenônio e regula sua alimentação durante o funcionamento."],
        "características_técnicas": ["Compatível com lâmpadas D3S e D3R.", "A versão Q02, Q03 ou Q04 deve corresponder ao módulo original."],
        "modo_de_funcionamento": ["Converte a tensão do veículo em um pulso de alta tensão para a partida da lâmpada e depois mantém a corrente de operação controlada."],
        "instalação": ["Desligue a alimentação do veículo, retire o módulo do farol e instale outro com referência, sufixo e conectores equivalentes. Verifique infiltração ou curto no farol antes da troca."],
    },
    "240": {
        "para_que_serve": ["Regula a tensão e a corrente fornecidas aos elementos de iluminação do farol."],
        "características_técnicas": ["Tipo: módulo para conjunto óptico original Mercedes-Benz.", "Conectores, referência e versão devem coincidir com a peça retirada."],
        "modo_de_funcionamento": ["Recebe a alimentação do veículo, converte e regula a energia elétrica antes de fornecê-la ao conjunto de iluminação."],
        "instalação": ["Desligue a alimentação do veículo, substitua o módulo no alojamento original e verifique umidade ou curto no farol. Confirme a necessidade de parametrização após a montagem."],
    },
    "280": {
        "para_que_serve": ["Mantém o óleo dentro do motor e impede a entrada de contaminantes pelo ponto de saída do eixo."],
        "características_técnicas": ["Quantidade: 1 unidade.", "Corpo em elastômero.", "Reforço metálico.", "Mola circular no lábio de vedação."],
        "modo_de_funcionamento": ["O lábio mantém contato contínuo com o eixo giratório, enquanto a parte externa permanece prensada no alojamento."],
        "instalação": ["Remova a peça usada sem riscar o eixo ou o alojamento, lubrifique o lábio com óleo limpo e prense o retentor alinhado, na profundidade e orientação previstas."],
    },
    "280-K": {
        "para_que_serve": ["Evita vazamentos de óleo nos pontos de saída dos comandos e reduz a entrada de contaminantes."],
        "características_técnicas": ["Quantidade do conjunto: 2 unidades.", "Corpo em elastômero.", "Reforço metálico.", "Mola circular no lábio de vedação."],
        "modo_de_funcionamento": ["Os lábios permanecem pressionados contra os eixos giratórios, enquanto a parte externa fica fixa nos alojamentos."],
        "instalação": ["Remova os retentores sem danificar os eixos ou alojamentos, lubrifique os lábios e prense as peças alinhadas, respeitando orientação e profundidade de montagem."],
    },
    "294": {
        "para_que_serve": ["Conduz o óleo de volta da turbina ao motor após a lubrificação do eixo do turbocompressor."],
        "características_técnicas": ["Tipo: tubo de retorno de óleo da turbina.", "Aplicação: motores 1.6 THP."],
        "modo_de_funcionamento": ["O óleo escoa por gravidade através do tubo de retorno, saindo da carcaça da turbina e voltando ao motor."],
        "instalação": ["Com o motor frio, remova o tubo usado, limpe as superfícies, instale vedações novas e fixe o tubo sem torções. Verifique vazamentos após o funcionamento."],
    },
    "307-2": {
        "para_que_serve": ["Adapta e distribui os sinais e a alimentação elétrica entre o chicote do veículo e a carcaça termostática."],
        "características_técnicas": ["Formato: Y.", "Conexões: 1 entrada e 2 saídas.", "Cabos isolados e conectores com travas.", "Compatível com carcaças 1336.Z6, 11537534521 e 9808647080."],
        "modo_de_funcionamento": ["Conduz os sinais e a alimentação elétrica entre os conectores da carcaça termostática."],
        "instalação": ["Com o motor frio, desligue a bateria, conecte cada terminal ao encaixe correspondente e fixe o chicote longe de partes quentes ou móveis."],
    },
    "309.1": {
        "para_que_serve": ["Controla a quantidade de combustível admitida pela bomba de alta pressão para regular a pressão do sistema common rail."],
        "características_técnicas": ["Tipo: válvula dosadora IMV.", "Construção: corpo metálico.", "Acionamento: eletromagnético."],
        "modo_de_funcionamento": ["A unidade de controle varia a corrente da bobina e desloca o elemento dosador, alterando o volume de combustível enviado à bomba."],
        "instalação": ["Despressurize o sistema, limpe cuidadosamente a região, substitua a vedação e instale a válvula na bomba com o torque especificado. Faça o diagnóstico e verifique vazamentos."],
    },
    "362": {
        "para_que_serve": ["Fecha o módulo, mantém sua vedação e sustenta as conexões de combustível e a passagem elétrica."],
        "características_técnicas": ["Flange moldada em plástico.", "Conexões para mangueiras e encaixe elétrico.", "Conjunto com componentes de vedação e retenção."],
        "modo_de_funcionamento": ["A flange mantém o tanque vedado e direciona as conexões de combustível e elétricas."],
        "instalação": ["Despressurize o sistema, desligue a bateria e trabalhe em local ventilado, sem fontes de ignição. Substitua a vedação, alinhe a flange e verifique vazamentos após a montagem."],
    },
    "365": {
        "para_que_serve": ["Fecha o módulo, sustenta as conexões das mangueiras e permite a ligação elétrica."],
        "características_técnicas": ["Material: plástico.", "Cor: branca.", "Duas conexões para mangueira.", "Alojamento para conector elétrico."],
        "modo_de_funcionamento": ["Atua passivamente na vedação e na passagem das conexões do módulo da bomba."],
        "instalação": ["Despressurize o sistema, desligue a bateria, substitua a vedação e monte a flange na posição original. Realize o teste de vazamento."],
    },
    "369": {
        "para_que_serve": ["Conduz o líquido de arrefecimento entre os componentes do motor e mantém a vedação do circuito."],
        "características_técnicas": ["Material: plástico preto.", "Extremidades flangeadas.", "Dois furos de fixação.", "Anéis de vedação."],
        "modo_de_funcionamento": ["Permite a passagem do líquido pelo trajeto de desvio previsto no sistema de arrefecimento."],
        "instalação": ["Com o motor frio e o sistema despressurizado, drene o volume necessário, limpe os alojamentos, instale os anéis de vedação e aperte os parafusos conforme o manual. Reabasteça, faça a sangria e verifique vazamentos."],
    },
    "382": {
        "para_que_serve": ["Puxa combustível do tanque e o envia ao carburador do motor de popa."],
        "características_técnicas": ["Tipo: bomba mecânica de diafragma.", "Corpo metálico.", "Três conexões para mangueira.", "Equivalência: Sierra 18-7350."],
        "modo_de_funcionamento": ["Os pulsos do cárter movimentam o diafragma, criando ciclos alternados de sucção e envio do combustível pelas válvulas internas."],
        "instalação": ["Feche a alimentação de combustível, instale a bomba com junta nova e ligue cada mangueira ao ponto correto. Verifique vazamentos antes de colocar o motor em operação."],
        "equipamentos": ["Motores de popa Johnson e Evinrude de 6, 8, 9,9 e 15 hp — 1974 a 1992."],
    },
    "394-1": {
        "para_que_serve": ["Mantém o conector da mangueira preso ao alojamento sob a pressão do sistema de admissão."],
        "características_técnicas": ["Material: arame de aço-mola.", "Tipo: presilha de acoplamento rápido.", "Tamanho: modelo maior."],
        "modo_de_funcionamento": ["As extremidades encaixam nas ranhuras do conector e impedem sua retirada; o anel do acoplamento realiza a vedação."],
        "instalação": ["Com o sistema frio e sem pressão, encaixe completamente a mangueira e insira a presilha nas ranhuras. Verifique o anel de vedação e puxe levemente o conector para confirmar o travamento."],
    },
    "394-2": {
        "para_que_serve": ["Mantém o conector da mangueira preso ao alojamento sob a pressão do sistema de admissão."],
        "características_técnicas": ["Material: arame de aço-mola.", "Tipo: presilha de acoplamento rápido.", "Tamanho: modelo menor."],
        "modo_de_funcionamento": ["As extremidades encaixam nas ranhuras do conector e impedem sua retirada; o anel do acoplamento realiza a vedação."],
        "instalação": ["Com o sistema frio e sem pressão, encaixe completamente a mangueira e insira a presilha nas ranhuras. Verifique o anel de vedação e puxe levemente o conector para confirmar o travamento."],
    },
    "396": {
        "para_que_serve": ["Fixa para-barros, protetores, para-choques, revestimentos e painéis em furos compatíveis."],
        "características_técnicas": ["Quantidade do conjunto: 100 presilhas.", "Material: plástico.", "Formatos e encaixes variados.", "Referência do conjunto: GN1667."],
        "modo_de_funcionamento": ["O pino central expande as hastes da presilha ou a haste farpada trava no furo, prendendo as peças."],
        "instalação": ["Selecione a presilha pelo diâmetro do furo, pelo comprimento da haste e pela espessura dos painéis. Alinhe e pressione sem forçar."],
    },
    "397": {
        "para_que_serve": ["Fixa para-barros, protetores, para-choques, revestimentos e painéis em furos compatíveis."],
        "características_técnicas": ["Quantidade do conjunto: 100 presilhas.", "Material: plástico.", "Formatos variados.", "Estojo com divisórias.", "Referência: GN1667."],
        "modo_de_funcionamento": ["O pino central expande as hastes da presilha ou a haste farpada trava no furo, prendendo as peças."],
        "instalação": ["Selecione a presilha pelo diâmetro do furo, pelo comprimento da haste e pela espessura dos painéis. Alinhe e pressione sem forçar."],
    },
    "408": {
        "para_que_serve": ["Fixa e auxilia na remoção de para-barros, protetores, para-choques, revestimentos e painéis."],
        "características_técnicas": ["Quantidade do conjunto: 415 presilhas.", "Presilhas plásticas variadas.", "Ferramenta extratora metálica com cabo.", "Estojo com divisórias.", "Referência: GN1683."],
        "modo_de_funcionamento": ["As presilhas expandem ou travam no furo de montagem, enquanto a ferramenta extratora faz alavanca sob a cabeça para soltá-las."],
        "instalação": ["Escolha a presilha conforme o furo e a espessura dos painéis, alinhe e pressione até travar. Para remover, apoie a ferramenta sob a cabeça e aplique força gradual."],
    },
    "129": {
        "para_que_serve": ["Auxilia a localizar tubos, peças de madeira, fios energizados e metais antes de furar ou cortar uma parede."],
        "modo_de_funcionamento": ["Ao ser deslocado sobre a superfície, o sensor analisa alterações no material e indica a detecção por sinais luminosos e sonoros."],
        "instalação": ["Selecione o modo de detecção, calibre o aparelho encostado na superfície e mova-o lentamente sobre a área a ser verificada."],
    },
    "129-3": {
        "para_que_serve": ["Auxilia a localizar tubos, peças de madeira, fios energizados e metais antes de furar ou cortar uma parede."],
        "modo_de_funcionamento": ["Ao ser deslocado sobre a superfície, o sensor analisa alterações no material e indica a detecção por sinais luminosos e sonoros."],
        "instalação": ["Coloque as pilhas, selecione o modo de detecção, calibre o aparelho encostado na superfície e mova-o lentamente sobre a área a ser verificada."],
    },
    "139": {
        "para_que_serve": ["Mede a pressão absoluta e a temperatura do ar no coletor para que a unidade de controle calcule a carga do motor."],
        "modo_de_funcionamento": ["Os elementos sensores convertem a pressão e a temperatura do ar em sinais elétricos enviados à unidade de controle."],
    },
    "154": {
        "para_que_serve": ["Mede a pressão do ar no coletor de admissão e na linha de sobrealimentação para o gerenciamento do motor."],
        "modo_de_funcionamento": ["O elemento sensor converte a pressão do ar em um sinal elétrico enviado à unidade de controle."],
    },
    "287": {
        "para_que_serve": ["Mede a massa de ar admitida pelo motor para que a unidade de controle ajuste a injeção de combustível."],
        "modo_de_funcionamento": ["O elemento sensor detecta a quantidade de ar que atravessa o duto e envia um sinal proporcional à unidade de controle."],
    },
    "289": {
        "para_que_serve": ["Mede a massa de ar admitida pelo motor para que a unidade de controle ajuste a injeção de combustível."],
        "modo_de_funcionamento": ["O elemento sensor detecta a quantidade de ar que atravessa o duto e envia um sinal proporcional à unidade de controle."],
    },
    "336": {
        "para_que_serve": ["Substitui a capa desgastada do botão e restaura seu acabamento e a leitura das inscrições iluminadas."],
        "características_técnicas": ["Material: plástico.", "Cor: vermelha.", "Acabamento: brilhante.", "Inscrições translúcidas para a iluminação original."],
        "modo_de_funcionamento": ["A capa transmite ao mecanismo original a pressão exercida pelo usuário e permite a passagem da iluminação pelas inscrições."],
        "instalação": ["Remova somente a capa antiga com uma ferramenta plástica fina e encaixe a nova na mesma orientação, preservando o interruptor original."],
    },
    "345": {
        "para_que_serve": ["Permite verificar visualmente a presença, a regularidade e a frequência da centelha produzida pelo sistema de ignição."],
        "características_técnicas": ["Faixa de simulação de rotação: 1.000 a 5.000 RPM.", "Frequência de teste ajustável."],
        "modo_de_funcionamento": ["O equipamento aciona a vela conectada em pulsos ajustáveis, tornando a centelha visível para avaliação."],
        "instalação": ["Conecte a vela e o cabo de ignição aos terminais indicados, mantenha o conjunto longe de combustível e partes móveis e ajuste a frequência do teste."],
    },
    "371": {
        "para_que_serve": ["Conecta a mangueira superior ao circuito de arrefecimento, oferece o ponto de enchimento e mantém a vedação e a pressão por meio da tampa."],
        "características_técnicas": ["Tipo: conector superior com bocal e tampa.", "Materiais do conjunto: plástico e metal.", "Posição: superior.", "Conteúdo: conector/flange e tampa."],
        "modo_de_funcionamento": ["O líquido de arrefecimento circula pelo conector, enquanto a tampa veda o bocal e controla a pressão do circuito."],
        "instalação": ["Com o sistema frio e sem pressão, drene o líquido abaixo da conexão, substitua o conjunto, fixe as mangueiras e abraçadeiras e depois reabasteça e faça a sangria."],
    },
    "372": {
        "para_que_serve": [
            "Interliga os trechos da mangueira superior, conduz o líquido de arrefecimento e oferece o ponto de enchimento e fechamento pressurizado do circuito.",
            "Conecta a mangueira fina de retorno e recuperação ao reservatório de expansão.",
        ],
        "modo_de_funcionamento": ["O líquido de arrefecimento circula entre as duas conexões principais. A tampa correta, instalada no gargalo lateral, veda e controla a pressão do circuito; o bico menor conduz o excesso e o retorno do líquido pelo reservatório de expansão."],
        "instalação": ["Execute o serviço somente com o motor frio e o sistema sem pressão. Drene o líquido abaixo da conexão, registre a orientação da peça antiga, retire a tampa e as três mangueiras e instale o novo gargalo no mesmo sentido, com abraçadeiras adequadas. Use a tampa especificada para o veículo, reabasteça com o fluido correto, faça a sangria e verifique vazamentos."],
    },
    "407": {
        "para_que_serve": ["Monitora a temperatura e a umidade do ambiente e disponibiliza as leituras no aplicativo e em automações compatíveis."],
        "características_técnicas": ["Conectividade: Wi-Fi.", "Grandezas monitoradas: temperatura e umidade.", "Integrações compatíveis: Alexa e Google Home."],
        "modo_de_funcionamento": ["Os elementos sensores realizam as leituras e o módulo Wi-Fi transmite os dados para o aplicativo e os serviços configurados."],
        "instalação": ["Configure o sensor no aplicativo e na rede Wi-Fi e posicione-o no ambiente a ser monitorado, longe de fontes diretas de calor ou umidade."],
    },
    "432": {
        "para_que_serve": ["Auxilia na remoção de vegetação rasteira, resíduos e incrustações em superfícies resistentes."],
        "características_técnicas": ["Material dos fios: aço.", "Tipo: escova circular para roçadeira."],
        "modo_de_funcionamento": ["A rotação do eixo movimenta os fios de aço, que escovam a superfície por contato."],
        "instalação": ["Com a roçadeira desligada, fixe a escova pelo furo central de 25 mm, aperte o conjunto e confirme o sentido de rotação e a proteção do equipamento."],
    },
    "438": {
        "para_que_serve": ["Protege o rosto e os olhos contra partículas projetadas durante o uso de roçadeiras e outras ferramentas."],
        "características_técnicas": ["Viseira: transparente.", "Estrutura: preta e ajustável.", "Recurso: tratamento antiembaçante."],
        "modo_de_funcionamento": ["A viseira forma uma barreira física transparente entre o rosto e as partículas projetadas."],
        "instalação": ["Ajuste a carneira à cabeça e confirme a cobertura do rosto e o travamento da viseira antes de iniciar o trabalho."],
    },
    "444-K2-1": {
        "para_que_serve": ["Permite alternar entre revolvimento superficial do solo e limpeza por escovação com a mesma roçadeira."],
        "características_técnicas": ["Conteúdo: 1 disco enxada e 1 escova de aço.", "Quantidade do conjunto: 2 peças."],
        "modo_de_funcionamento": ["O eixo da roçadeira transfere a rotação ao implemento instalado para revolver o solo ou escovar a superfície."],
        "instalação": ["Com a roçadeira desligada, instale um implemento por vez pelo furo de 25,4 mm, aperte a fixação e use a proteção adequada."],
    },
    "444-K2-2": {
        "para_que_serve": ["Permite alternar entre limpeza por escovação e corte de vegetação com a mesma roçadeira."],
        "características_técnicas": ["Conteúdo: 1 escova de aço carbono e 1 lâmina articulada de 6 pontas.", "Quantidade do conjunto: 2 peças."],
        "modo_de_funcionamento": ["O eixo da roçadeira transfere a rotação ao implemento instalado; a escova atua por contato e as pontas articuladas realizam o corte."],
        "instalação": ["Com a roçadeira desligada, instale um implemento por vez pelo furo de 25,4 mm, aperte a fixação e use a proteção adequada."],
    },
    "444-K3": {
        "para_que_serve": ["Reúne implementos para revolvimento superficial do solo, limpeza por escovação e corte de vegetação."],
        "características_técnicas": ["Conteúdo: 1 disco enxada, 1 escova de aço e 1 lâmina articulada de 6 pontas.", "Quantidade do conjunto: 3 peças."],
        "modo_de_funcionamento": ["O eixo da roçadeira transfere a rotação ao implemento selecionado para executar a operação correspondente."],
        "instalação": ["Com a roçadeira desligada, instale um implemento por vez, aperte a fixação e use a proteção adequada."],
    },
    "453": {
        "para_que_serve": ["Corta capim e vegetação por meio de seis pontas articuladas acionadas pela rotação da roçadeira."],
        "características_técnicas": ["Tipo: lâmina articulada tipo canivete.", "Quantidade de pontas: 6."],
        "modo_de_funcionamento": ["A força centrífuga abre as pontas articuladas durante a rotação, permitindo o corte da vegetação."],
        "instalação": ["Com a roçadeira desligada, fixe a lâmina pelo furo central de 25,4 mm, confira o aperto e utilize a proteção do equipamento."],
    },
    "275-19": {
        "para_que_serve": ["Oferece apoio para puxar e fechar a porta do motorista e substitui o acabamento interno deteriorado."],
        "características_técnicas": ["Cor: ostra.", "Posição: porta do motorista."],
        "modo_de_funcionamento": ["O puxador forma uma superfície rígida de apoio para a mão durante o fechamento da porta."],
        "instalação": ["Remova o acabamento antigo e fixe o puxador na porta do motorista, conferindo o alinhamento e o travamento dos encaixes."],
    },
    "326": {
        "para_que_serve": ["Direciona o fluxo de ar do sistema de ventilação para os ocupantes do banco traseiro."],
        "características_técnicas": ["Tipo: difusor com aletas direcionais.", "Posição: parte traseira do console central."],
        "modo_de_funcionamento": ["As aletas permitem orientar e regular manualmente a passagem do ar pelo difusor."],
        "instalação": ["Remova o acabamento traseiro do console, substitua o difusor no alojamento original e confira a movimentação das aletas."],
    },
    "367": {
        "para_que_serve": ["Acopla ou desacopla manualmente a roda dianteira do semieixo no sistema de tração 4x4."],
        "características_técnicas": ["Materiais: alumínio, ferro e polipropileno.", "Tipo: roda livre manual.", "Posição: cubo dianteiro."],
        "modo_de_funcionamento": ["O seletor manual movimenta o mecanismo interno entre as posições livre e travada."],
        "instalação": ["Instale no cubo dianteiro com estrias, vedações e fixadores alinhados e teste as posições livre e travada."],
    },
    "367-K": {
        "para_que_serve": ["Acopla ou desacopla manualmente as duas rodas dianteiras dos semieixos no sistema de tração 4x4."],
        "características_técnicas": ["Materiais: alumínio, ferro e polipropileno.", "Tipo: rodas livres manuais.", "Posição: cubos dianteiros.", "Quantidade do conjunto: 2 unidades."],
        "modo_de_funcionamento": ["Cada seletor manual movimenta o mecanismo interno entre as posições livre e travada."],
        "instalação": ["Instale uma unidade em cada cubo dianteiro com estrias, vedações e fixadores alinhados e teste as posições livre e travada."],
    },
    "425-1": {
        "para_que_serve": ["Conduz o ar pressurizado no circuito do intercooler e mantém a vedação da admissão."],
        "características_técnicas": ["Tipo: mangueira pressurizada do intercooler.", "Posição: lado direito."],
        "modo_de_funcionamento": ["A mangueira direciona o ar comprimido entre os componentes da admissão sem permitir perda de pressão."],
        "instalação": ["Instale sem torções, fixe as abraçadeiras e verifique encaixes, vazamentos de ar e contato com partes quentes ou móveis."],
    },
    "445": {
        "para_que_serve": ["Armazena chaves e pequenos objetos e restringe o acesso por meio de uma combinação ajustável."],
        "características_técnicas": ["Material: aço.", "Formato: caixa.", "Mecanismo de abertura: senha ajustável de 1 a 4 dígitos.", "Resistência à água: sim.", "Acessórios: parafusos e manual."],
        "modo_de_funcionamento": ["O mecanismo libera a tampa quando a combinação correta é alinhada e volta a travar após o fechamento e a alteração dos discos."],
        "instalação": ["Fixe o cofre em uma superfície firme com os parafusos, cadastre a combinação e teste a abertura e o fechamento antes de guardar os objetos."],
    },
    "451-1": {
        "para_que_serve": ["Liga o conjunto da roda ao agregado da suspensão e ajuda a manter a geometria da roda dianteira direita."],
        "características_técnicas": ["Material da bandeja: alumínio.", "Material da bucha: borracha.", "Posição: dianteira inferior direita."],
        "modo_de_funcionamento": ["A bandeja articula pelas buchas e pelo pivô, guiando o movimento vertical da roda e controlando seu posicionamento lateral e longitudinal."],
        "instalação": ["Apoie o veículo e o conjunto da suspensão, substitua a bandeja no lado direito com o torque especificado e realize o alinhamento da direção."],
    },
    "451-2": {
        "para_que_serve": ["Liga o conjunto da roda ao agregado da suspensão e ajuda a manter a geometria da roda dianteira esquerda."],
        "características_técnicas": ["Material da bandeja: alumínio.", "Material da bucha: borracha.", "Posição: dianteira inferior esquerda."],
        "modo_de_funcionamento": ["A bandeja articula pelas buchas e pelo pivô, guiando o movimento vertical da roda e controlando seu posicionamento lateral e longitudinal."],
        "instalação": ["Apoie o veículo e o conjunto da suspensão, substitua a bandeja no lado esquerdo com o torque especificado e realize o alinhamento da direção."],
    },
    "451-K": {
        "para_que_serve": ["Liga os conjuntos das rodas ao agregado da suspensão e ajuda a manter a geometria das duas rodas dianteiras."],
        "características_técnicas": ["Material das bandejas: alumínio.", "Material das buchas: borracha.", "Posições: dianteira inferior direita e esquerda.", "Quantidade do conjunto: 2 unidades."],
        "modo_de_funcionamento": ["As bandejas articulam pelas buchas e pelos pivôs, guiando o movimento vertical das rodas e controlando seu posicionamento lateral e longitudinal."],
        "instalação": ["Apoie o veículo e a suspensão, substitua as bandejas nos lados correspondentes com o torque especificado e realize o alinhamento da direção."],
    },
    "520-1": {
        "para_que_serve": ["Permite acionar o vidro elétrico pela porta do motorista."],
        "características_técnicas": ["Posição: porta do motorista.", "Conector: 3 pinos.", "Acabamento: prata e preto."],
        "modo_de_funcionamento": ["O acionamento do botão comuta os contatos internos e envia o comando ao sistema do vidro elétrico."],
        "instalação": ["Remova o acabamento da porta, substitua o comando no alojamento original, conecte o plugue de 3 pinos e teste as funções do vidro."],
    },
    "520-2": {
        "para_que_serve": ["Permite acionar o vidro elétrico pela porta do motorista."],
        "características_técnicas": ["Posição: porta do motorista.", "Conector: 3 pinos.", "Acabamento: prata e marrom."],
        "modo_de_funcionamento": ["O acionamento do botão comuta os contatos internos e envia o comando ao sistema do vidro elétrico."],
        "instalação": ["Remova o acabamento da porta, substitua o comando no alojamento original, conecte o plugue de 3 pinos e teste as funções do vidro."],
    },
    "520-3": {
        "para_que_serve": ["Permite acionar o vidro elétrico pela porta do motorista."],
        "características_técnicas": ["Posição: porta do motorista.", "Conector: 3 pinos.", "Acabamento: prata e bege."],
        "modo_de_funcionamento": ["O acionamento do botão comuta os contatos internos e envia o comando ao sistema do vidro elétrico."],
        "instalação": ["Remova o acabamento da porta, substitua o comando no alojamento original, conecte o plugue de 3 pinos e teste as funções do vidro."],
    },
    "471": {
        "para_que_serve": ["Exibe velocidade, nível de combustível e os demais avisos e indicadores da motocicleta."],
        "modo_de_funcionamento": ["O circuito recebe os sinais dos sensores e comandos da motocicleta e os converte em indicações no mostrador."],
        "instalação": ["Desligue a bateria, fixe o painel no suporte original, conecte o chicote e teste o mostrador e todas as luzes indicadoras."],
    },
}

CONTENT_OVERRIDES = {
    "001": {
        "para_que_serve": ["Aciona a ventoinha do radiador quando o líquido de arrefecimento atinge a temperatura de atuação."],
        "características_técnicas": ["Temperatura de acionamento: 100 °C.", "Rosca: M16 x 1,5.", "Sextavado: 22 mm.", "Quantidade de terminais: 2.", "Construção metálica."],
        "modo_de_funcionamento": ["Ao atingir 100 °C, o interruptor térmico fecha o circuito elétrico que comanda a ventoinha."],
        "instalação": ["Rosqueie a peça no ponto original do sistema de arrefecimento, com o motor frio, após confirmar o OEM 37760-MT2-003, a rosca M16 x 1,5 e o conector de 2 pinos."],
    },
    "521": {
        "para_que_serve": ["Conduz o líquido de arrefecimento entre o reservatório e o circuito de arrefecimento do veículo."],
        "características_técnicas": ["Material: plástico.", "Posição informada: superior.", "Inclui abraçadeiras."],
        "modo_de_funcionamento": ["A mangueira mantém o fluxo do líquido de arrefecimento e a vedação entre os pontos conectados."],
        "instalação": ["Substitua a mangueira somente após confirmar o OEM 80A121081S e os encaixes. O sistema deve estar frio e sem pressão; recomenda-se instalação por profissional qualificado."],
    },
    "412-55": {
        "para_que_serve": ["Realiza capina e preparo do solo em pequenas e médias hortas e plantações quando acoplada à roçadeira compatível."],
        "características_técnicas": ["Diâmetro de trabalho: 360 mm.", "Tubo: 26 mm.", "Encaixe quadrado: 5,5 mm.", "Relação de redução: 16:19.", "Peso líquido: 3,9 kg."],
        "modo_de_funcionamento": ["A transmissão da roçadeira movimenta as lâminas da enxada por meio do conjunto redutor; a rotação de corte informada é anti-horária vista pelo operador."],
        "instalação": ["Confirme tubo de 26 mm e eixo quadrado de 5,5 mm antes de acoplar a ferramenta à roçadeira. Faça a fixação com o equipamento desligado e siga o aperto do conjunto original."],
        "equipamentos": ["Roçadeiras com tubo de 26 mm e eixo quadrado de 5,5 mm.", "Aplicações anunciadas: Stihl FS80, FS85, FS120 e FS200, mediante conferência do encaixe."],
    },
}

CONTENT_OVERRIDES.update({
    "129": {
        "características_técnicas": [
            "Detecção de cabos energizados acima de 110 V.", "Frequência: 50 a 60 Hz.",
            "Profundidade máxima de detecção informada: 110 mm.", "Alerta luminoso e sonoro.",
        ],
    },
    "129-3": {
        "características_técnicas": [
            "Detecção de cabos energizados acima de 110 V.", "Frequência: 50 a 60 Hz.",
            "Profundidade máxima de detecção informada: 110 mm.", "Alerta luminoso e sonoro.",
        ],
    },
    "153": {
        "características_técnicas": [
            "Interface ELM327/OBD2 por USB.", "Comutação HS-CAN e MS-CAN.",
            "Pinos HS-CAN: 6 e 14.", "Pinos MS-CAN: 3 e 11.",
            "Compatível com os programas FORScan, ELMConfig e FoCCCus.",
        ],
    },
    "167": {"instalação": ["Rosqueie o extensor na válvula do pneu e verifique a vedação antes de calibrar."]},
    "167-K": {"instalação": ["Rosqueie cada extensor na válvula do pneu e verifique a vedação antes de calibrar."]},
    "189-1": {"instalação": ["Encaixe nos pontos originais da caçamba, sem necessidade de perfuração, conferindo o lado direito."]},
    "189-2": {"instalação": ["Encaixe nos pontos originais da caçamba, sem necessidade de perfuração, conferindo o lado esquerdo."]},
    "342": {
        "características_técnicas": ["Alimentação: 12 V.", "Ligação elétrica por 3 fios.", "Mostrador digital."],
        "instalação": ["Fixe o mostrador, conecte os três fios conforme o diagrama e ajuste o sensor após abastecer o tanque."],
    },
    "377-2": {"características_técnicas": ["Grau de proteção: IP68.", "Alimentação: 10 a 30 V CC.", "Potência: 5,4 W.", "Quantidade de LEDs: 27.", "Ângulo do feixe: 120°.", "Comprimento do fio: 152 cm."]},
    "377-4": {"características_técnicas": ["Grau de proteção: IP68.", "Alimentação: 10 a 30 V CC.", "Potência: 5,4 W.", "Quantidade de LEDs: 27.", "Ângulo do feixe: 120°.", "Comprimento do fio: 152 cm."]},
    "SHOPEE-377-2": {"características_técnicas": ["Grau de proteção: IP68.", "Alimentação: 10 a 30 V CC.", "Potência: 5,4 W.", "Quantidade de LEDs: 27.", "Ângulo do feixe: 120°.", "Comprimento do fio: 152 cm."]},
    "SHOPEE-377-4": {"características_técnicas": ["Grau de proteção: IP68.", "Alimentação: 10 a 30 V CC.", "Potência: 5,4 W.", "Quantidade de LEDs: 27.", "Ângulo do feixe: 120°.", "Comprimento do fio: 152 cm."]},
    "381": {"características_técnicas": ["Grau de proteção: IP66.", "Corrente em 125 V CA: 16 A.", "Corrente em 250 V CA: 10 A.", "Corrente em 12 V CC: 20 A.", "Corrente em 24 V CC: 10 A.", "Temperatura de operação: -25 °C a 80 °C."]},
    "385-2": {"características_técnicas": ["Grau de proteção: IP68.", "Alimentação: 10 a 36 V CC.", "Potência: 9 W.", "Fluxo luminoso: 1500 lm.", "Material: aço inoxidável 316.", "Quantidade de LEDs: 42."]},
    "385-3": {"características_técnicas": ["Grau de proteção: IP68.", "Alimentação: 10 a 36 V CC.", "Potência: 9 W.", "Fluxo luminoso: 1500 lm.", "Material: aço inoxidável 316.", "Quantidade de LEDs: 42."]},
    "385-4": {"características_técnicas": ["Grau de proteção: IP68.", "Alimentação: 10 a 36 V CC.", "Potência: 9 W.", "Fluxo luminoso: 1500 lm.", "Material: aço inoxidável 316.", "Quantidade de LEDs: 42."]},
    "426-1": {"instalação": ["Conecte ao chicote original do retrovisor; instalação plug and play, sem codificação, após confirmar o lado direito."]},
    "426-2": {"instalação": ["Conecte ao chicote original do retrovisor; instalação plug and play, sem codificação, após confirmar o lado esquerdo."]},
    "459": {"características_técnicas": ["Roscas de entrada e saída: 1/4 polegada.", "Faixa de pressão: 0 a 10 bar.", "Vazão informada a 7 bar: 2300 L/min.", "Capacidade do copo: 0,15 L.", "Material do corpo: alumínio.", "Posição de montagem: vertical."]},
    "SHOPEE-459": {"características_técnicas": ["Roscas de entrada e saída: 1/4 polegada.", "Faixa de pressão: 0 a 10 bar.", "Vazão informada a 7 bar: 2300 L/min.", "Capacidade do copo: 0,15 L.", "Material do corpo: alumínio.", "Posição de montagem: vertical."]},
})

PRODUCT_CLASSES = (
    ("vela", ("vela de ignicao", "vela ignicao", "velas de ignicao", "velas ignicao")),
    ("sensor", ("sensor", "medidor de fluxo", "fluxo de ar")),
    ("bomba", ("bomba",)),
    ("grade", ("grade",)),
    ("farol", ("farol", "lanterna", "pisca")),
    ("manopla", ("manopla", "bola do cambio", "alavanca de cambio")),
    ("tampa", ("tampa", "portinhola")),
    ("mangueira", ("mangueira", "tubo de agua", "tubo do radiador")),
    ("trava", ("trava", "fechadura", "atuador")),
    ("emblema", ("emblema", "logotipo")),
    ("coroa_bicicleta", ("coroa unica", "coroa para bicicleta", "guia de corrente")),
    ("motoredutor", ("motoredutor",)),
    ("scanner", ("scanner", "detector", "calibrador", "sincronizador")),
    ("conector", ("conector", "terminal")),
)

FOREIGN_REPLACEMENTS = (
    (r"\btouch\s+screen\b", "tela sensível ao toque"),
    (r"\bfire\s+blanket\b", "manta antichamas"),
    (r"\bbreak\s+light\b", "terceira luz de freio"),
    (r"\bsnow\s+foam\b", "pulverizador de espuma"),
    (r"\bstart[\s/-]+stop\b", "partida e desligamento"),
    (r"\bnarrow\s+wide\b", "dentes largos e estreitos"),
    (r"\bengine\s+coolant\s+overflow\s+hose\b", "mangueira do reservatório de arrefecimento"),
    (r"\bcooling\s+water\s+tube\b", "tubo de água do arrefecimento"),
    (r"\bwireless\b", "sem fio"),
    (r"\bdisplay\b", "tela"),
    (r"\bshifter\b", "alavanca de câmbio"),
    (r"\biridium\b", "irídio"),
    (r"\biiridium\b", "irídio"),
    (r"\bplatinum\b|\bplatinium\b|\bplatinun\b", "platina"),
    (r"\baluminium\b", "alumínio"),
    (r"\bpencil\b", "caneta"),
    (r"\bstylus\b", "capacitiva"),
    (r"\btype\b", "tipo"),
    (r"\brail\b", "flauta de combustível"),
    (r"\bcanister\b", "reservatório de carvão ativado"),
    (r"\bred\b", "vermelho"),
    (r"\bblue\b", "azul"),
    (r"\bblack\b", "preto"),
    (r"\bleft\b", "esquerdo"),
    (r"\bright\b", "direito"),
    (r"\bfront\b", "dianteiro"),
    (r"\brear\b", "traseiro"),
    (r"\bclean\b", ""),
    (r"\bbike\b", "bicicleta"),
)
BANNED_NAME_WORDS = {
    "engine", "cooling", "water", "tube", "wireless", "display", "touch", "screen",
    "shifter", "iridium", "platinum", "platinium", "platinun", "aluminium", "pencil",
    "stylus", "fire", "blanket", "break", "light", "snow", "foam", "left", "right",
    "front", "rear", "black", "blue", "red", "clean", "type", "bike",
}

STOPWORDS = {
    "para", "com", "sem", "do", "da", "de", "dos", "das", "uma", "um", "kit", "conjunto",
    "produto", "peca", "peça", "novo", "nova", "original", "unidade", "unidades", "compativel",
    "compatível", "carro", "veiculo", "veículo", "modelo", "lado", "direito", "esquerdo",
}


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip().casefold()


def _mojibake_score(value: str) -> int:
    return sum(value.count(marker) for marker in ("Ã", "Â", "â", "ð", "�"))


def _repair_text(value: Any) -> str:
    text = str(value or "").replace("\x00", "").strip()
    for _ in range(2):
        if not text or not any(marker in text for marker in ("Ã", "Â", "â", "ð")):
            break
        try:
            candidate = text.encode("latin1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
        if _mojibake_score(candidate) >= _mojibake_score(text):
            break
        text = candidate
    text = text.replace("\uFFFD", "")
    return re.sub(r"\s+", " ", text).strip(" \t\r\n-–—|;")


def _is_missing_value(value: Any) -> bool:
    folded = re.sub(r"[_\s]+", " ", _fold(_repair_text(value))).strip(" .,:;-")
    if not folded:
        return True
    if re.search(
        r"\b(?:nao\s+(?:informad[oa]s?|localizad[oa]s?|encontrad[oa]s?|se\s+aplica)|"
        r"sem\s+informa(?:cao|coes))\b",
        folded,
    ):
        return True
    return bool(
        re.search(r"\bmedidas?.*\bembalagem\b.*\b(?:desconsiderad|descartad|removid)[oa]s?\b", folded)
    )


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read_catalog(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    seen: set[str] = set()
    output = []
    for row in rows:
        sku = _repair_text(row.get("sku"))
        key = _fold(sku)
        if not sku or key in seen:
            continue
        seen.add(key)
        row["sku"] = sku
        output.append(row)
    return sorted(output, key=lambda row: _fold(row.get("sku")))


def _safe_filename(sku: str) -> str:
    return re.sub(r"[<>:\"/\\|?*]", "_", sku).strip(". ") + ".json"


def _translate_common_terms(value: Any) -> str:
    text = _repair_text(value)
    for pattern, replacement in FOREIGN_REPLACEMENTS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = re.sub(r"\bsem fio\s+sem fio\b", "sem fio", text, flags=re.IGNORECASE)
    text = re.sub(r"\btela\s+(?:LCD\s+)?tela\b", "tela LCD", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip(" ,;:-")


def _sanitize_output_text(value: Any) -> str:
    """Remove linguagem de pesquisa, de venda e frases de ausência do dado final."""
    text = _repair_text(value)
    if not text:
        return ""
    folded = _fold(text)
    if any(term in folded for term in (
        "para escolher qual modelo voce deve comprar",
        "conforme imagens apresentadas no anuncio",
        "aplicacao isolada documentada no cadastro",
        "produto novo similar",
        "alta qualidade",
        "solucao ideal para quem busca",
        "perfeito para projetos",
        "super potencia",
        "grupo trendminas",
        "cor: como a imagem",
    )):
        return ""

    substitutions = (
        (r"(?i)\bo cadastro(?:\s+local)?\s+descreve\s+o\s+material\s+como\s+", "Material: "),
        (r"(?i)\bo cadastro(?:\s+local)?\s+(?:anuncia|indica)\s+conjunto\s+com\s+", "Inclui "),
        (r"(?i)\bconte[uú]do\s+declarado\s+no\s+cadastro\s*:\s*", "Conteúdo do conjunto: "),
        (r"(?i)\ban[uú]ncio\s+exato\s+cita\s+", ""),
        (r"(?i)\baplica[cç][aã]o\s+presente\s+em\s+an[uú]ncio\s+do\s+kit\s+de\s+reparo\s*;?\s*", ""),
        (r"(?i)\b(?:informad[oa]|declarad[oa]|indicad[oa])\s+(?:no|na|pelo|pela)\s+cadastro\b", ""),
        (r"(?i)\b(?:vers[aã]o|reposi[cç][aã]o)\s+pesquisad[oa]\b", lambda m: m.group(0).split()[0]),
        (r"(?i)\baplica[cç][oõ]es\s+[^.;]+?\s+pesquisadas\b", lambda m: re.sub(r"\s+pesquisadas\b", "", m.group(0), flags=re.IGNORECASE)),
        (r"(?i)\b(?:do|da|no|na|pelo|pela|conforme\s+o)\s+cadastro\b", ""),
        (r"(?i)\b(?:anunciad[oa]s?|cadastrad[oa]s?|pesquisad[oa]s?)\b", ""),
        (r"(?i)\s*(?:[.;]\s*)?n[aã]o\s+h[aá]\s+instru[cç][oõ]es\s+inclu[ií]das\s+neste\s+kit\.?\s*$", ""),
        (r"(?i)[;,]\s*(?:h[aá]\s+an[uú]ncios?|an[uú]ncios?\s+brasileiros?|algumas\s+fontes?\s+brasileiras?)\b.*$", ""),
        (r"(?i)\s+(?:nos?|pelos?|em)\s+an[uú]ncios?(?:\s+brasileiros?)?\s*$", ""),
        (r"(?i)\s+conforme\s+(?:o\s+)?an[uú]ncio\s*$", ""),
    )
    for pattern, replacement in substitutions:
        text = re.sub(pattern, replacement, text)
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    text = re.sub(r"([:;,])\s*([:;,])", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip(" ,;:-")
    # Se uma formulação editorial não foi neutralizada com segurança,
    # é preferível deixar o item vazio a exibi-la como dado do produto.
    if re.search(r"(?i)\b(?:cadastro|an[uú]ncios?|pesquisad[oa]s?)\b", text):
        return ""
    return text


def _clean_product_name(sku: str, row: dict[str, Any], overrides: dict[str, Any]) -> str:
    override = overrides.get(sku) or overrides.get(_fold(sku))
    if isinstance(override, dict):
        override = override.get("nome_produto") or override.get("nome")
    candidates = [
        FINAL_PRODUCT_NAME_OVERRIDES.get(sku),
        override,
        row.get("produto_bling"),
        row.get("nome"),
        row.get("produto"),
    ]
    value = next((_repair_text(item) for item in candidates if _repair_text(item)), f"Produto SKU {sku}")
    value = _translate_common_terms(value)
    value = re.sub(r"(?i)^kit\b", "Conjunto", value)
    value = re.sub(r"(?i)\bsuper\s+pot[eê]ncia\b", "", value)
    value = re.sub(r"(?i)\bcor\s*:\s*", "cor ", value)
    value = re.sub(r"(?i)\blado\s*:\s*", "lado ", value)
    value = re.sub(r"(?i)\bdi[aâ]metro\s*:\s*", "diâmetro ", value)
    value = re.sub(r"(?i)\bvoltagem\s*:\s*", "tensão ", value)
    value = re.split(
        r"(?i),?\s*(?:material\s*:|caixa de papel[aã]o\b|saco pl[aá]stico\b|plastic bag\b|cardboard box\b)",
        value,
        maxsplit=1,
    )[0]
    value = re.sub(r"\b(?:c/|com)\s+caixa\b.*$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s*[,;:]\s*$", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:220]


def _untranslated_name_words(name: str) -> list[str]:
    words = set(re.findall(r"[a-z]+", _fold(name)))
    return sorted(words & BANNED_NAME_WORDS)


def _sentinel_measure(text: str) -> bool:
    folded = _fold(text)
    if re.search(r"(?<!\d)(?:0|0[.,]0+)\s*(?:mm|cm|m|kg|g)\b", folded):
        return True
    if re.search(r"(?<!\d)1(?:[.,]0+)?\s*mm\b", folded) and not any(
        term in folded for term in ("ponta", "espessura", "diametro do fio", "diâmetro do fio")
    ):
        return True
    if re.search(r"(?<!\d)(?:0[.,]1\s*cm|1[.,]11\s*(?:mm|cm)|28[.,]21(?:9)?\s*m)\b", folded):
        return True
    if re.search(r"\b(1{3,}|0{3,})+(?:[.,]\d+)?\s*(?:mm|cm|m)\b", folded):
        return True
    for number in re.findall(r"\d+(?:[.,]\d+)?", folded):
        try:
            if float(number.replace(",", ".")) > 10000:
                return True
        except ValueError:
            pass
    return False


def _measure_fragments(text: str) -> list[str]:
    cleaned = _repair_text(text)
    if not cleaned:
        return []
    pieces = re.split(r"(?<=[.;])\s+|\s*[|•]\s*|\s+-\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ0-9])", cleaned)
    output = []
    for piece in pieces:
        piece = _repair_text(piece).strip(" .")
        if not piece:
            continue
        if len(piece) > 80:
            matches = []
            labeled = re.compile(
                r"(?i)\b(comprimento|largura|altura|di[aâ]metro(?:\s+(?:interno|externo|da cabe[cç]a))?|"
                r"espessura|dimens[oõ]es?|tamanho|rosca(?:s)?|encaixe|bocal|haste|furo(?:\s+central)?|"
                r"eixo|entrada|sa[ií]da|dist[aâ]ncia|BCD|DN|peso\s+l[ií]quido|bitola|deslocamento)"
                r"\s*(?:do|da|de|dos|das)?\s*[a-zçãõáéíóúâêô/-]{0,28}\s*[:=-]?\s*"
                r"((?:\d+(?:[.,]\d+)?\s*(?:[x×]\s*\d+(?:[.,]\d+)?\s*){1,2}"
                r"(?:mm|cm|m|pol(?:egadas?)?|\"))|(?:\d+(?:[.,]\d+)?\s*(?:mm(?:2|²)?|cm|m|kg|g|pol(?:egadas?)?|\"))|"
                r"(?:M\s*\d+(?:[xX]\d+(?:[.,]\d+)?)?)|(?:\d+\s*/\s*\d+\s*\"(?:\s*BSP)?))"
            )
            for match in labeled.finditer(piece):
                label = _repair_text(match.group(1)).capitalize()
                value = _repair_text(match.group(2))
                matches.append(f"{label}: {value}")
            for match in DIMENSION_RE.finditer(piece):
                value = _repair_text(match.group(0))
                if not any(value in candidate for candidate in matches):
                    matches.append(f"Dimensões: {value}")
            output.extend(matches)
        else:
            output.append(piece)
    return output


def _clean_measures(raw_items: Iterable[Any], description: str = "") -> list[str]:
    candidates: list[str] = []
    for item in raw_items or []:
        text = item.get("texto") if isinstance(item, dict) else item
        candidates.extend(_measure_fragments(str(text or "")))
    candidates.extend(_measure_fragments(description))

    output: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        text = _sanitize_output_text(_translate_common_terms(candidate))
        folded = _fold(text)
        if not text or any(term in folded for term in LOGISTICS_TERMS):
            continue
        if any(term in folded for term in ("pressao", "pressão", "vazao", "vazão", "capacidade", "alcance", "elevacao", "elevação")):
            continue
        has_label = any(label in folded for label in MEASURE_LABELS)
        if not (has_label and MEASURE_UNIT_RE.search(text) or DIMENSION_RE.search(text)):
            continue
        if _sentinel_measure(text):
            continue
        if len(text) > 120:
            continue
        text = re.sub(r"(?i)individual size", "Dimensões do produto", text)
        text = re.sub(r"(?i)^medidas?\s*:\s*", "", text)
        text = text.strip(" .,:;-")
        key = _fold(text)
        if key and key not in seen:
            seen.add(key)
            output.append(text)
        if len(output) >= 12:
            break
    return output


def _claim_fragments(value: Any) -> list[str]:
    """Segmenta blocos longos antes dos filtros de qualidade."""
    text = _translate_common_terms(value)
    if not text:
        return []
    text = re.sub(
        r"(?i)\s+(?=(?:material|cor|tens[aã]o|voltagem|pot[eê]ncia|corrente|frequ[eê]ncia|"
        r"press[aã]o|vaz[aã]o|capacidade|temperatura|quantidade|n[uú]mero de|tipo de|"
        r"dimens[oõ]es?|comprimento|largura|altura|di[aâ]metro|espessura|peso|rosca|"
        r"encaixe|instala[cç][aã]o|montagem|modo de uso|funcionamento)\s*[:=-])",
        "\n",
        text,
    )
    pieces = re.split(r"\r?\n+|[•●▪◦]+|\s+[|]+\s+|;\s+|(?<=[.!?])\s+", text)
    output = []
    for piece in pieces:
        piece = re.sub(r"\s+", " ", piece).strip(" \t\r\n-–—|;,.:")
        if not piece:
            continue
        if len(piece) <= 280:
            output.append(piece)
            continue
        subpieces = re.split(
            r"(?i)\s+(?=(?:fabricado|produzido|possui|inclui|acompanha|f[aá]cil instala[cç][aã]o|"
            r"instale|conecte|encaixe|rosqueie|fixe|serve para|utilizado para|ideal para)\b)",
            piece,
        )
        output.extend(part.strip(" .,:;-") for part in subpieces if 3 <= len(part.strip()) <= 280)
    return output


def _clean_text_items(raw_items: Iterable[Any], *, limit: int, product_name: str = "") -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    name_folded = _fold(product_name)
    for item in raw_items or []:
        original = item.get("texto") if isinstance(item, dict) else item
        for text in _claim_fragments(original):
            text = _sanitize_output_text(text)
            folded = _fold(text)
            if not text or _is_missing_value(text):
                continue
            if any(term in folded for term in LOGISTICS_TERMS + MARKETING_TERMS):
                continue
            if folded == name_folded or folded.startswith("titulos de anuncios"):
                continue
            if "http://" in folded or "https://" in folded or "www." in folded:
                continue
            if re.search(r"(?i)^caracter[ií]sticas do produto\s*:\s*(?:sem validade|n/?a|nao se aplica)", text):
                continue
            if re.search(r"(?i):\s*(?:-1|0|1{4,}|estados unidos)\s*\.?$", text):
                continue
            if re.search(r"(?i)^modelo alfanum[eé]rico:.*(?:estados unidos|\b1{4,}\b)", text):
                continue
            if re.search(r"(?i)^(?:caracter[ií]sticas do produto|[ée] marca (?:destacada|tom))\s*:", text):
                continue
            if re.search(r"(?i)\b(?:vehicle|radiator|temperature|remote|box|with|water|switch|assy)\b", text):
                continue
            if re.search(r"(?i)\b(?:caracter[ií]sticas|especifica[cç][oõ]es)\s+(?:do|da)\.?$", text):
                continue
            if re.fullmatch(
                r"(?i)(?:funcionamento|modo de funcionamento|instala[cç][aã]o|montagem|"
                r"finalidade|aplica[cç][aã]o|caracter[ií]sticas|especifica[cç][oõ]es)\.?",
                text.strip(),
            ):
                continue
            if any(word in set(re.findall(r"[a-z]+", folded)) for word in BANNED_NAME_WORDS):
                continue
            key = folded.rstrip(".")
            if key and key not in seen:
                seen.add(key)
                output.append(text.rstrip(" .") + ".")
            if len(output) >= limit:
                return output
    return output


def _trusted_raw_items(
    items: Iterable[Any],
    raw: dict[str, Any],
    product_name: str,
    sku: str,
    *,
    allow_description: bool = True,
    allow_ads: bool = True,
) -> list[Any]:
    allowed = {"cadastro:nome", "cadastro:produto", "cadastro:produto_bling"}
    if allow_description:
        allowed.add("cadastro:descricao")
    if allow_ads:
        for ad in ((raw.get("anuncios") or {}).get("ativos") or []):
            title = _repair_text((ad or {}).get("titulo"))
            item_id = _repair_text((ad or {}).get("item_id"))
            if item_id and _ad_matches_product(product_name, title, sku):
                allowed.add(f"anuncio:{item_id}")
    output = []
    for item in items or []:
        if not isinstance(item, dict):
            output.append(item)
            continue
        sources = {_repair_text(source) for source in item.get("fontes") or []}
        if not sources or sources & allowed:
            output.append(item)
    return output


def _technical_items(items: Iterable[Any], *, product_name: str) -> list[str]:
    cleaned = _clean_text_items(items, limit=20, product_name=product_name)
    output = []
    allowed_labels = (
        "material", "cor", "tensao", "voltagem", "alimentacao", "potencia", "corrente",
        "frequencia", "pressao", "vazao", "capacidade", "temperatura",
        "quantidade de terminais", "quantidade de conectores", "quantidade de pinos",
        "numero de terminais", "tipo de", "posicao", "lado", "tecnologia",
        "grau de protecao", "acabamento", "rotacao", "torque", "relacao de reducao",
        "numero de dentes", "resolucao", "rosca", "encaixe", "diametro", "comprimento",
        "largura", "altura", "espessura", "peso liquido", "bcd", "faixa de",
        "leds", "numero de leds",
    )
    forbidden_labels = (
        "marca", "marca compativel", "modelo", "modelo compativel", "linha compativel",
        "titulo de catalogo", "codigo universal", "mpn", "origem", "condicao",
        "tipo de embalagem", "caracteristicas do produto", "fonte do produto",
        "inclui ferragens de montagem", "quantidade de furos de montagem", "tipo de montagem",
        "cor principal",
    )
    for text in cleaned:
        folded = _fold(text)
        label = _fold(text.split(":", 1)[0]) if ":" in text else ""
        value = _repair_text(text.split(":", 1)[1]) if ":" in text else text
        if label and any(label == item or label.startswith(item + " ") for item in forbidden_labels):
            continue
        if len(text) > 220 or text.count(":") > 1:
            continue
        if any(term in folded for term in (
            "numero da peca", "número da peça", "interchange", "outros numeros",
            "outros números", "verificar disponibilidade", "aplicacoes:", "aplicações:",
            "medida da embalagem", "medidas da embalagem", "aproximado", "aproximada",
            "aprox.",
        )):
            continue
        if re.search(r"(?<!\d)1{3,}(?!\d)|2821[.,]94|281[.,]94|1938\s*mm|1112\s*cm", value):
            continue
        has_allowed_label = bool(label and any(label == item or label.startswith(item + " ") for item in allowed_labels))
        has_specific_number = bool(re.search(
            r"\b\d+(?:[.,]\d+)?\s*(?:v|w|a|mah|rpm|bar|psi|kpa|mpa|hz|khz|mhz|"
            r"graus|°c|mm|cm|kg|g|terminais?|pinos?|leds?|velocidades?|l/min|l/h)\b",
            folded,
        ))
        if has_allowed_label or (not label and has_specific_number):
            output.append(text)
        if len(output) >= 12:
            break
    conflicts: dict[str, set[str]] = defaultdict(set)
    for text in output:
        if ":" not in text:
            continue
        label, value = (_fold(part) for part in text.split(":", 1))
        if label.startswith("cor"):
            conflicts["cor"].add(value)
        elif any(term in label for term in ("terminais", "conectores", "pinos")):
            conflicts["conexões"].add(value)
        elif label == "lado":
            conflicts["lado"].add(value)
    bad_groups = {key for key, values in conflicts.items() if len(values) > 1}
    if not bad_groups:
        return output
    filtered = []
    for text in output:
        label = _fold(text.split(":", 1)[0]) if ":" in text else ""
        group = ""
        if label.startswith("cor"):
            group = "cor"
        elif any(term in label for term in ("terminais", "conectores", "pinos")):
            group = "conexões"
        elif label == "lado":
            group = "lado"
        if group not in bad_groups:
            filtered.append(text)
    return filtered


def _resolve_technical_conflicts(items: Iterable[Any]) -> list[str]:
    """Remove especificações contraditórias sem escolher arbitrariamente uma fonte."""
    cleaned = _dedupe_text(items, limit=20)
    accepted: list[tuple[str, str, str]] = []
    label_values: dict[str, set[str]] = defaultdict(set)
    label_units: dict[str, set[str]] = defaultdict(set)
    forbidden_fragments = (
        "numero da peca", "interchange", "outros numeros", "verificar disponibilidade",
        "aplicacoes:", "medida da embalagem", "medidas da embalagem", "aproximado",
        "aproximada", "aprox.", "gptrend", "aplicacao", "garantia", "reclamacao",
        "entre em contato", "campo de mensagens", "codigo do",
    )
    for text in cleaned:
        folded = _fold(text)
        if len(text) > 220 or text.count(":") > 1:
            continue
        if any(fragment in folded for fragment in forbidden_fragments):
            continue
        if ":" not in text:
            accepted.append((text, "", ""))
            continue
        raw_label, raw_value = text.split(":", 1)
        label = _fold(raw_label).strip()
        if label in {"voltagem", "alimentacao", "alimentação"}:
            label = "tensao"
        value = _fold(raw_value)
        if re.search(r"\b(?:de|do|da|dos|das)\.?$", value):
            continue
        if label.startswith("cor") and (
            len(raw_value) > 60
            or re.search(r"\b(?:diametro|material|aplicacao|marca|codigo)\b", value)
        ):
            continue
        normalized_value = re.sub(r"\s+", "", value).replace(",", ".").rstrip(".")
        unit = ""
        if re.search(r"\bpsi\b", value):
            unit = "psi"
        elif re.search(r"\bbar\b", value):
            unit = "bar"
        label_values[label].add(normalized_value)
        if unit:
            label_units[label].add(unit)
        accepted.append((text, label, normalized_value))

    conflicting = set()
    for label, values in label_values.items():
        if len(values) <= 1:
            continue
        if label.startswith("pressao") and label_units[label] == {"bar", "psi"}:
            continue
        conflicting.add(label)

    output = []
    seen_specs = set()
    for text, label, normalized_value in accepted:
        if label in conflicting:
            continue
        key = (label, normalized_value) if label else ("", _fold(text))
        if key in seen_specs:
            continue
        seen_specs.add(key)
        output.append(text)
    return output[:12]


def _functional_items(items: Iterable[Any], *, product_name: str) -> list[str]:
    cleaned = _clean_text_items(items, limit=12, product_name=product_name)
    terms = (
        "funciona", "funcionamento", "aciona", "detecta", "transmite", "converte", "regula",
        "controla", "movimenta", "permite", "interrompe", "abre", "fecha", "mede", "monitora",
    )
    return [text for text in cleaned if any(term in _fold(text) for term in terms)][:6]


def _installation_items(items: Iterable[Any], *, product_name: str) -> list[str]:
    cleaned = _clean_text_items(items, limit=12, product_name=product_name)
    terms = (
        "instale", "instalar", "montar", "encaixe a", "encaixar",
        "conecte", "conectar", "fixe", "fixar", "substitua", "substituir", "rosqueie",
        "rosquear", "remova", "remover", "plug and play",
    )
    output = []
    for text in cleaned:
        folded = _fold(text)
        if ":" in text:
            body = text.split(":", 1)[1]
            if not any(term in _fold(body) for term in terms):
                continue
        if any(term in folded for term in terms):
            output.append(text)
    return output[:7]


def _purpose_from_description(description: str) -> list[str]:
    parts = re.split(r"(?<=[.!?;])\s+|\n+", _repair_text(description))
    selected = []
    for part in parts:
        folded = _fold(part)
        if len(part) > 260 or any(term in folded for term in MARKETING_TERMS + LOGISTICS_TERMS):
            continue
        if any(term in folded for term in ("serve para", "utilizado para", "responsavel por", "responsável por", "permite ", "funcao", "função", "ideal para")):
            selected.append(part)
        if len(selected) >= 3:
            break
    return _clean_text_items(selected, limit=3)


def _knowledge_content(product_name: str) -> dict[str, list[str]]:
    """Descreve somente a função física geral da classe explícita no nome."""
    folded = _fold(product_name)
    rules = (
        (r"repelente.*(?:cachorro|cao|caes)|espanta cachorro|anti latido",
         "Emite sinal sonoro ou ultrassônico para auxiliar no afastamento ou condicionamento de cães.",
         "O circuito eletrônico produz pulsos acústicos ao ser acionado.",
         "Não exige instalação fixa; coloque pilha ou carregue o aparelho e direcione-o conforme o modo de uso."),
        (r"scanner automotivo|forscan|testador.*faisca",
         "Auxilia no diagnóstico ou teste do sistema automotivo correspondente.",
         "O equipamento lê sinais, comunica-se com módulos ou evidencia o circuito em teste.",
         "Conecte somente ao ponto indicado para o teste e siga o procedimento do veículo."),
        (r"(?:modulo|resistencia).*(?:eletroventoinha|eletroventilador|ventoinha)",
         "Controla a velocidade ou o acionamento elétrico da ventoinha.",
         "O circuito regula a corrente ou comuta o motor conforme o comando recebido.",
         "Fixe no local original e confira motor, chicote e terminais."),
        (r"tampa.*(?:reservatorio|radiador)",
         "Fecha e veda o reservatório do sistema de arrefecimento.",
         "A tampa mantém a vedação e, quando calibrada, controla a pressão do circuito.",
         "Instale somente com o sistema frio e confira pressão, rosca ou encaixe."),
        (r"diafragma.*(?:valvula.*)?pcv|membrana.*(?:valvula.*)?pcv",
         "Regula a ventilação dos gases do cárter no conjunto da válvula PCV.",
         "O diafragma se desloca com a diferença de pressão e modula a passagem dos vapores para a admissão.",
         "Abra o alojamento da PCV, substitua o diafragma na posição correta e confira a vedação antes de fechar."),
        (r"atuador.*vacuo|acoplador.*vacuo",
         "Acopla ou desacopla o eixo dianteiro do sistema de tração 4x4 por comando a vácuo.",
         "A depressão na linha movimenta o diafragma e a haste que comanda o mecanismo de engate.",
         "Fixe no ponto original, conecte a mangueira de vácuo e confira a vedação, o curso da haste e a referência da peça."),
        (r"cilindro.*(?:atuador.*)?hidraulico.*embreagem|atuador.*hidraulico.*embreagem",
         "Converte a pressão hidráulica em movimento para acionar a embreagem.",
         "O fluido pressurizado desloca o pistão do cilindro e movimenta o mecanismo de desacoplamento.",
         "Instale na posição original, conecte a linha hidráulica e realize a sangria do circuito."),
        (r"motor.*trava|fechadura|trava.*porta|tranca.*porta",
         "Trava e destrava mecanicamente a porta, tampa ou compartimento correspondente.",
         "O mecanismo interno movimenta a lingueta ou as hastes de travamento; nas versões elétricas, o motor recebe o comando do veículo.",
         "Fixe no ponto original, alinhe lingueta, cabos ou hastes e conecte o chicote quando existente."),
        (r"refletor|olho de gato",
         "Sinaliza a posição do veículo ao refletir a luz que incide sobre a peça.",
         "A superfície retrorrefletiva devolve a luz incidente sem alimentação elétrica.",
         "Instale no alojamento e lado corretos, preservando o encaixe, a orientação e a fixação original."),
        (r"^(?!.*(?:fita.*led|barra flexivel.*led|led.*parafuso|luz.*parafuso|"
         r"olho de aguia|luz.*subaquatica|lampada.*subaquatica)).*"
         r"(?:lampada|pisca|seta|lanterna|luz.*freio|\bled\b)",
         "Fornece iluminação ou sinalização luminosa no ponto de aplicação.",
         "A energia elétrica é convertida em luz pelo elemento luminoso.",
         "Instale no alojamento original, conferindo encaixe, alimentação, polaridade quando aplicável e vedação."),
        (r"caneta capacitiva",
         "Permite escrever, desenhar ou comandar uma tela sensível ao toque com maior precisão.",
         "A ponta capacitiva interage com o campo elétrico da tela e reproduz o toque do dedo.",
         "Não exige instalação; carregue ou conecte a caneta quando necessário e use-a em tela compatível."),
        (r"fita.*led|barra flexivel.*led",
         "Fornece iluminação ou sinalização auxiliar por meio de uma faixa flexível de LEDs.",
         "Os LEDs convertem a energia elétrica em luz e os circuitos separados acionam cada função luminosa.",
         "Limpe a superfície, fixe a fita sem dobras acentuadas e ligue os fios à alimentação e aos comandos correspondentes, respeitando a polaridade."),
        (r"led.*parafuso|luz.*parafuso|olho de aguia",
         "Fornece iluminação ou sinalização auxiliar em sistema veicular.",
         "O LED converte a energia elétrica em luz através da lente frontal.",
         "Fixe a peça em um furo com o diâmetro correspondente e conecte a alimentação respeitando a tensão, a polaridade e a vedação."),
        (r"luz.*subaquatica|lampada.*subaquatica",
         "Ilumina a área submersa ou externa de embarcações e outras aplicações compatíveis.",
         "O conjunto selado converte a energia elétrica em luz por meio dos LEDs.",
         "Fixe em superfície adequada, vede os pontos de passagem e conecte a alimentação na tensão correta, respeitando a polaridade."),
        (r"engrenagem.*(?:reparo|atuador)",
         "Substitui a engrenagem interna desgastada do atuador sem trocar o conjunto completo.",
         "Os dentes da engrenagem transmitem o movimento do motor ao mecanismo da caixa de transferência.",
         "Desmonte o atuador, substitua a engrenagem na mesma posição, lubrifique conforme o procedimento e feche o conjunto com a vedação preservada."),
        (r"vibrador",
         "Produz vibrações para estimulação pessoal.",
         "Um pequeno motor elétrico com massa excêntrica gera as vibrações selecionadas.",
         "Não exige instalação; carregue o aparelho e siga as orientações de higienização e uso."),
        (r"esquadro digital.*laser",
         "Mede e projeta ângulos e linhas de referência em trabalhos de alinhamento.",
         "Sensores internos calculam a inclinação e o laser projeta a referência visual.",
         "Não exige instalação fixa; calibre, apoie na superfície e selecione o modo de medição."),
        (r"piloto automatico|controle de velocidade",
         "Permite comandar o sistema de controle automático de velocidade do veículo.",
         "A alavanca envia ao módulo os comandos de ativar, ajustar, retomar e cancelar a velocidade.",
         "Instale no ponto original da coluna e faça a habilitação eletrônica quando exigida pelo veículo."),
        (r"suporte.*celular",
         "Mantém o telefone visível e acessível durante o uso do veículo ou motocicleta.",
         "O mecanismo de fixação segura o aparelho e, quando presente, a bobina Qi realiza o carregamento.",
         "Fixe no suporte compatível, ajuste o ângulo e conecte a alimentação quando houver carregamento."),
        (r"esteira.*cambio|esteira.*câmbio|trilho.*alavanca.*cambio",
         "Guia e cobre o deslocamento da alavanca do câmbio automático.",
         "A peça flexível acompanha o movimento da alavanca dentro do seletor.",
         "Remova o acabamento do console, alinhe a peça no seletor e reinstale travas e molduras."),
        (r"cebolinha.*direcao|cebolinha.*direção",
         "Monitora a pressão do sistema de direção hidráulica para o gerenciamento do motor.",
         "O interruptor altera o estado elétrico quando a pressão hidráulica atinge sua faixa de atuação.",
         "Instale na conexão original com vedação adequada e confira o conector elétrico."),
        (r"caixa bsm|placa.*fusiveis|placa.*fusíveis|modulo.*fusiveis|módulo.*fusíveis",
         "Distribui a alimentação e protege circuitos elétricos por fusíveis e relés.",
         "As trilhas, fusíveis e relés encaminham a corrente e interrompem circuitos em caso de sobrecarga.",
         "Desconecte a bateria, transfira fusíveis e conectores conforme a posição original e confira a referência."),
        (r"esguicho.*agua|brucutu|esguicho.*parabrisa|esguicho.*para brisa",
         "Direciona o líquido de lavagem para o para-brisa ou farol.",
         "A pressão da bomba força o líquido pelo pequeno orifício e forma o jato.",
         "Encaixe no furo original, conecte a mangueira e ajuste o direcionamento do jato."),
        (r"tapete.*agua.*bebe|tapete.*água.*bebê",
         "Oferece uma superfície sensorial com água para atividades de bebês.",
         "O compartimento com água movimenta as formas internas quando pressionado.",
         "Não exige instalação; encha os compartimentos indicados, vede e use sobre superfície plana."),
        (r"broca.*solo|broca.*terra",
         "Perfura ou revolve solo em atividades de jardim e plantio.",
         "A hélice corta e transporta a terra para fora do furo durante a rotação.",
         "Fixe no mandril ou equipamento compatível e confira aperto, diâmetro e sentido de rotação."),
        (r"ventosa manual",
         "Permite segurar, levantar ou posicionar superfícies lisas dentro da capacidade informada.",
         "A bomba manual remove o ar sob a ventosa e cria pressão negativa para aderência.",
         "Limpe a superfície, pressione a ventosa e bombeie até obter vácuo antes de movimentar a carga."),
        (r"protetor.*farol",
         "Protege a lente do farol contra impactos leves e detritos.",
         "A placa forma uma barreira física à frente do farol sem substituir a lente original.",
         "Fixe nos suportes compatíveis mantendo folga da lente e sem obstruir o feixe."),
        (r"guia.*farol|guia.*suporte.*parachoque|guia.*suporte.*para-choque",
         "Alinha e sustenta o componente da carroceria no ponto de montagem.",
         "O perfil da guia recebe as travas e mantém o componente na posição correta.",
         "Fixe no lado correto e encaixe o componente sem forçar presilhas ou pontos de montagem."),
        (r"console.*teto",
         "Aloja comandos e porta-objetos no acabamento interno do teto.",
         "A estrutura mantém os acessórios fixados e permite seu acionamento ou abertura.",
         "Conecte o chicote quando existente e fixe nos encaixes originais do forro."),
        (r"reservatorio.*direcao|reservatório.*direção",
         "Armazena e alimenta o fluido do sistema de direção hidráulica.",
         "O reservatório mantém a coluna de fluido disponível para a circulação da bomba.",
         "Instale com mangueiras e abraçadeiras corretas, complete o fluido e elimine o ar do sistema."),
        (r"dobradica.*capo|dobradiça.*capô",
         "Permite a articulação e sustenta o capô durante a abertura e o fechamento.",
         "O eixo da dobradiça guia o movimento entre o capô e a carroceria.",
         "Apoie o capô, fixe cada lado na posição original e faça o alinhamento antes do aperto final."),
        (r"acabamento.*parachoque|acabamento.*para-choque|frizo|suporte.*placa",
         "Completa o acabamento ou sustenta o componente externo no ponto de aplicação.",
         "A peça mantém alinhamento e cobertura no conjunto da carroceria.",
         "Posicione no local original e fixe pelos encaixes, parafusos ou adesivos compatíveis."),
        (r"rodas livres",
         "Acopla ou desacopla o cubo da roda ao sistema de tração.",
         "O mecanismo interno transmite o torque quando engatado e libera o cubo quando desengatado.",
         "Instale no cubo correto, confira estrias, vedação, fixadores e posição de engate."),
        (r"sensor inteligente.*bateria|sensor.*corrente.*bateria|modulo.*bateria",
         "Monitora corrente, tensão e estado de carga para o gerenciamento elétrico da bateria.",
         "O sensor mede as grandezas no terminal negativo e envia os dados ao módulo de gerenciamento de energia.",
         "Desconecte a bateria conforme o procedimento do veículo, instale no terminal negativo e conecte o chicote correto."),
        (r"sensor.*pressao.*(?:combustivel|flauta)|sensor.*flauta.*combustivel",
         "Mede a pressão do combustível e envia o sinal ao gerenciamento do motor.",
         "O elemento sensor converte a pressão do combustível em sinal elétrico.",
         "Instale no ponto original com o circuito despressurizado, conferindo código, rosca e conector."),
        (r"sensor map|sensor maf|medidor.*(?:fluxo|massa).*ar|fluxo de ar|sensor.*pressao.*(?:coletor|turbina)",
         "Mede a pressão ou o fluxo de ar da admissão para o cálculo da carga do motor.",
         "O sensor converte a condição do ar admitido em sinal elétrico para a unidade de controle.",
         "Substitua no alojamento original, conferindo referência, vedação e conector."),
        (r"sensor.*nivel|boia.*combustivel|marcador.*nivel.*combustivel",
         "Mede o nível de combustível do tanque para indicação no painel.",
         "O movimento da boia altera o sinal elétrico enviado ao marcador.",
         "Instale no tanque ou conjunto da bomba, mantendo a boia livre e conferindo a vedação."),
        (r"sensor.*(?:tpms|pressao.*pneu)|calibrador.*tpms|sincronizador.*tpms",
         "Monitora ou realiza o reconhecimento dos sensores de pressão dos pneus.",
         "O sistema usa radiofrequência entre o sensor da roda e o módulo do veículo.",
         "Instale ou faça o reaprendizado conforme a referência e o procedimento do veículo."),
        (r"sensor.*detona",
         "Detecta vibrações características de detonação no motor.",
         "O elemento piezoelétrico converte a vibração do bloco em sinal elétrico.",
         "Fixe no ponto original com o torque especificado e conecte o chicote."),
        (r"sensor.*(?:temperatura|termico)|cebolao|interruptor.*ventoinha",
         "Monitora a temperatura e informa ou comanda o circuito correspondente.",
         "A variação de temperatura altera o sinal ou o estado elétrico do sensor.",
         "Instale no ponto original, conferindo rosca, vedação, conector e especificação de atuação."),
        (r"sensor.*(?:velocidade|rotacao|posicao|tps|direcao)|sensor",
         "Detecta a variável associada ao componente e fornece um sinal ao sistema de controle.",
         "O elemento sensor converte uma condição física em sinal elétrico.",
         "Instale no ponto original após conferir referência, encaixe e conector."),
        (r"bomba.*combustivel|refil.*bomba.*combustivel",
         "Envia combustível do tanque ao sistema de alimentação do motor.",
         "Um motor elétrico aciona o elemento de bombeamento para gerar fluxo e pressão.",
         "Instale com o circuito despressurizado, conferindo polaridade, filtro, mangueiras e vedação."),
        (r"bomba.*direcao",
         "Pressuriza o fluido do sistema de assistência hidráulica da direção.",
         "A bomba mantém a circulação e a pressão do fluido hidráulico.",
         "Instale com conexões e fluido corretos e elimine o ar do sistema após a montagem."),
        (r"bomba.*vacuo",
         "Gera vácuo para os sistemas auxiliares do motor.",
         "O mecanismo interno aspira o ar e mantém a depressão necessária no circuito.",
         "Substitua no ponto original com juntas e conexões corretas e verifique vazamentos."),
        (r"bomba.*agua|bomba submersa|bomba.*transferencia",
         "Transfere o líquido compatível entre os pontos do sistema.",
         "O motor elétrico gira o rotor da bomba e produz o fluxo do líquido.",
         "Conecte mangueiras e alimentação na tensão correta, respeitando o sentido do fluxo."),
        (r"valvula.*pressao|valvula reguladora",
         "Controla ou limita a pressão do fluido no circuito correspondente.",
         "A válvula abre, fecha ou modula a passagem conforme a pressão e o comando do sistema.",
         "Instale no sentido e posição originais, conferindo rosca, vedação e faixa de pressão."),
        (r"valvula.*termost|carcaca.*termost",
         "Regula a circulação do líquido de arrefecimento conforme a temperatura do motor.",
         "O elemento termostático abre progressivamente a passagem ao atingir a temperatura de trabalho.",
         "Monte com o motor frio, junta nova e orientação correta; complete e sangre o sistema."),
        (r"^valvula\b|torneira.*combustivel",
         "Controla a passagem do fluido ou gás no circuito em que é aplicada.",
         "O mecanismo interno abre, fecha ou modula o fluxo.",
         "Instale respeitando sentido, posição, vedação e conexões do conjunto original."),
        (r"vela.*ignicao",
         "Produz a centelha que inicia a combustão da mistura no cilindro.",
         "A alta tensão salta entre os eletrodos e gera a centelha na câmara de combustão.",
         "Instale com o motor frio, conferindo referência, folga e torque especificado."),
        (r"bobina.*ignicao|estator",
         "Gera ou transforma a energia elétrica necessária ao sistema de ignição e carga.",
         "A indução eletromagnética produz a tensão ou corrente utilizada pelo sistema.",
         "Instale no suporte original e confira chicote, isolamento e aterramento."),
        (r"motor.*partida|motor.*arranque",
         "Gira o motor durante a partida até que a combustão se mantenha.",
         "O motor elétrico aciona o pinhão que engrena no conjunto de partida.",
         "Desconecte a bateria e confira fixação, engrenamento e terminais elétricos."),
        (r"mangueira.*(?:agua|radiador|arrefecimento)|tubo.*(?:agua|radiador)|flange.*agua",
         "Conduz o líquido de arrefecimento entre os componentes do sistema.",
         "A peça mantém o fluxo e a vedação do líquido no circuito.",
         "Instale com o sistema frio, usando vedações e abraçadeiras corretas."),
        (r"mangueira.*(?:ar|tbi|intercooler|turbina)|coletor.*admissao",
         "Conduz e veda o ar no sistema de admissão do motor.",
         "O componente direciona o fluxo de ar entre os elementos da admissão.",
         "Instale sem torções e aperte as abraçadeiras para evitar entrada falsa de ar."),
        (r"mangueira.*retorno|tubo.*oleo|mangueira|tubo|flange",
         "Conduz ou conecta o fluido ou ar entre os pontos do sistema.",
         "O componente mantém o caminho de fluxo e a vedação entre as conexões.",
         "Instale após conferir formato, diâmetros, encaixes, vedações e posição."),
        (r"radiador",
         "Dissipa o calor do líquido de arrefecimento para controlar a temperatura de funcionamento.",
         "O líquido circula pelos canais e transfere calor para o ar que passa pelas aletas.",
         "Instale nos suportes originais, conecte as mangueiras e sangre o sistema."),
        (r"filtro eliminador.*compressor|regulador.*compressor",
         "Separa condensado e partículas do ar comprimido e ajusta a pressão de saída.",
         "O ar passa pelo separador e pelo regulador antes de seguir para a linha.",
         "Instale verticalmente no sentido do fluxo, vede as roscas e ajuste a pressão."),
        (r"filtro",
         "Retém partículas ou contaminantes antes que o fluido ou o ar prossiga pelo sistema.",
         "O meio filtrante permite a passagem do fluxo e captura as impurezas.",
         "Instale no alojamento e sentido de fluxo corretos, substituindo as vedações quando aplicável."),
        (r"amortecedor.*tampa|mola.*gas",
         "Auxilia a abertura e controla o movimento da tampa ou caçamba.",
         "O cilindro pressurizado oferece sustentação e amortecimento.",
         "Apoie a tampa e encaixe as extremidades nos pontos originais, conferindo lado e orientação."),
        (r"tampa.*combustivel|portinhola.*tanque",
         "Fecha e protege o acesso ao abastecimento de combustível.",
         "O fechamento mecânico mantém o acesso coberto e auxilia a vedação conforme o modelo.",
         "Monte nas dobradiças ou no bocal original e confira alinhamento, trava e vedação."),
        (r"\b(?:tampas?|capas?|carcacas?|molduras?|frisos?|filetes?|apliques?|spoilers?|grades?|emblemas?|logos?)\b",
         "Protege, cobre ou dá acabamento ao ponto de aplicação.",
         "A peça atua de forma passiva, mantendo proteção, acabamento ou passagem de ar.",
         "Posicione no local original e fixe com presilhas, parafusos, encaixes ou adesivo compatíveis."),
        (r"manopla|bola.*cambio|alavanca.*freio|botao.*alavanca",
         "Permite o acionamento manual da alavanca ou comando correspondente.",
         "O movimento aplicado pelo usuário é transmitido ao mecanismo.",
         "Remova o acabamento antigo e instale respeitando encaixe, rosca, trava e orientação."),
        (r"ponteiras?.*escapamento",
         "Dá acabamento à saída do escapamento e direciona a descarga dos gases para fora do veículo.",
         "A ponteira forma a extremidade externa por onde os gases já conduzidos pelo escapamento são liberados.",
         "Encaixe ou fixe na saída do escapamento após conferir diâmetro, alinhamento e folga da carroceria."),
        (r"painel.*(?:chaves|usb).*voltimetro|painel eletrico",
         "Centraliza o comando e a alimentação de acessórios elétricos.",
         "As chaves comutam circuitos e o voltímetro mede a alimentação.",
         "Dimensione cabos, fusíveis e aterramento e faça a ligação conforme o diagrama."),
        (r"botao|comando.*vidro|comutador|interruptor|chave interruptora",
         "Permite ao usuário comandar o circuito correspondente.",
         "Os contatos internos enviam o comando elétrico ao módulo ou atuador.",
         "Instale no alojamento original, conecte o chicote e teste todas as posições."),
        (r"modulo.*bateria|sensor.*corrente.*bateria",
         "Monitora ou gerencia a alimentação elétrica e o estado da bateria.",
         "O módulo mede grandezas elétricas e comunica os dados ao gerenciamento de energia.",
         "Desconecte a bateria conforme o procedimento e instale no terminal e conector corretos."),
        (r"reator.*xenon",
         "Alimenta e estabiliza a lâmpada de descarga do farol.",
         "O reator gera a tensão de partida e controla a potência da lâmpada.",
         "Fixe no ponto original e conecte lâmpada e chicote com a alimentação desligada."),
        (r"^atuador\b",
         "Aciona mecanicamente a trava, a marcha lenta ou o mecanismo correspondente.",
         "O comando elétrico movimenta o motor, solenoide ou mecanismo interno.",
         "Instale no suporte original, alinhe hastes ou engrenagens e conecte o chicote."),
        (r"engrenagem|roda fonica|roda livre",
         "Transmite, acopla ou referencia o movimento de rotação no conjunto mecânico.",
         "Os dentes ou o mecanismo de acoplamento transferem o movimento.",
         "Instale com alinhamento e ajuste corretos, conferindo referência e sentido de montagem."),
        (r"retentor|anel.*vedacao|junta|presilha|abracadeira",
         "Veda ou mantém unidos os componentes, evitando vazamentos ou deslocamentos.",
         "A compressão ou o travamento mantém a vedação e a fixação.",
         "Limpe as superfícies e instale sem torcer a peça, respeitando diâmetro e aperto."),
        (r"coroa.*bicicleta|guia.*corrente.*bicicleta",
         "Transmite a força da pedalada para a corrente ou mantém a corrente guiada.",
         "Os dentes engrenam na corrente enquanto o guia limita seu deslocamento lateral.",
         "Instale respeitando BCD, linha da corrente, sentido e torque dos parafusos."),
        (r"cinto.*anti alarme|adaptador.*cinto",
         "Encaixa no fecho do cinto em aplicações compatíveis.",
         "O formato do adaptador aciona mecanicamente o contato do fecho.",
         "Insira somente em fecho compatível; o acessório não substitui o uso do cinto."),
        (r"cabo auxiliar.*p2",
         "Leva o áudio de uma fonte externa à entrada auxiliar do rádio compatível.",
         "O sinal analógico percorre o cabo entre o conector P2 e a entrada auxiliar.",
         "Conecte ao ponto AUX do rádio após confirmar o conector e a ativação da função."),
        (r"transmissor.*bluetooth.*fm",
         "Reproduz áudio de um dispositivo Bluetooth pelo rádio FM.",
         "O aparelho recebe o áudio por Bluetooth e o retransmite em uma frequência FM.",
         "Ligue à tomada veicular, pareie e ajuste a mesma frequência no transmissor e no rádio."),
        (r"receptor.*(?:carregamento|inducao)",
         "Adiciona recepção de energia sem fio Qi a um aparelho compatível.",
         "A bobina receptora converte o campo magnético do carregador em energia elétrica.",
         "Conecte ao aparelho e alinhe a bobina à base Qi sem objetos metálicos entre eles."),
        (r"adaptador.*usb|multiportas.*usb",
         "Expande uma porta USB-C para as interfaces presentes no adaptador.",
         "O circuito distribui e converte os sinais da porta USB-C.",
         "Conecte à porta USB-C compatível e ligue cada periférico à porta correspondente."),
        (r"tela.*lcd|tela sensivel|painel.*instrumentos",
         "Exibe informações do equipamento ou permite interação com a interface eletrônica.",
         "A tela converte os sinais do circuito em imagem e pode detectar o toque.",
         "Substitua com o equipamento desligado, conectando cabos flexíveis sem danificá-los."),
        (r"scanner.*parede|detector.*parede",
         "Localiza materiais ou instalações ocultas antes da perfuração.",
         "Os sensores indicam alterações correspondentes ao material detectado.",
         "Não exige instalação fixa; calibre e deslize o aparelho sobre a superfície."),
        (r"scanner automotivo|forscan|testador.*faisca",
         "Auxilia no diagnóstico ou teste do sistema automotivo correspondente.",
         "O equipamento lê sinais, comunica-se com módulos ou evidencia o circuito em teste.",
         "Conecte somente ao ponto indicado para o teste e siga o procedimento do veículo."),
        (r"enxada rotativa|disco.*enxada|escova.*rocadeira|lamina.*rocadeira|adaptador.*cardan.*rocadeira",
         "Executa capina, preparo do solo, corte ou adaptação da ferramenta na roçadeira.",
         "A transmissão da roçadeira transfere a rotação ao implemento.",
         "Com o equipamento desligado, confira tubo, eixo, estrias ou encaixe e aperte as fixações."),
        (r"bico.*aspersor|nebulizador.*irrigacao",
         "Distribui água em forma de jato ou névoa no sistema de irrigação.",
         "A pressão da água forma o padrão de pulverização no orifício.",
         "Rosqueie na conexão compatível, vede a rosca e ajuste o jato."),
        (r"pulverizador.*espuma",
         "Aplica água e detergente em forma de espuma durante a lavagem.",
         "O fluxo de água mistura o produto do reservatório antes da saída.",
         "Conecte ao equipamento compatível, abasteça o reservatório e ajuste a mistura."),
        (r"motoredutor",
         "Fornece movimento rotativo com velocidade reduzida e maior torque no eixo.",
         "O motor aciona engrenagens que reduzem a rotação de saída.",
         "Fixe pelos pontos previstos, alinhe o eixo e use a tensão e polaridade indicadas."),
        (r"talha manual",
         "Eleva ou posiciona cargas dentro da capacidade nominal.",
         "A corrente manual movimenta engrenagens e a corrente de carga.",
         "Suspenda em ponto dimensionado, inspecione ganchos e não exceda a carga nominal."),
        (r"controle.*(?:ponte rolante|guincho|talha)",
         "Envia comandos remotos ao equipamento de elevação compatível.",
         "O transmissor envia os comandos ao receptor que aciona as saídas elétricas.",
         "A ligação deve ser feita por profissional conforme tensão, diagrama e emergência."),
        (r"conector.*bateria|terminal.*bateria",
         "Facilita a conexão e a desconexão elétrica da bateria.",
         "O contato metálico conduz a corrente quando o conector está acoplado.",
         "Prense cabos da bitola correta, respeite a polaridade e proteja contra curto-circuito."),
        (r"painel ripado",
         "Reveste paredes e cria acabamento decorativo ripado.",
         "As placas formam uma superfície modular após alinhamento e fixação.",
         "Nivele a base e fixe com adesivo ou parafusos adequados ao substrato."),
        (r"cadeira.*escritorio",
         "Oferece assento e apoio para uso em escritório.",
         "Base, pistão e mecanismos permitem movimentação e regulagem.",
         "Monte base, rodízios, pistão, assento, encosto e braços na sequência correta."),
        (r"manta antichamas",
         "Auxilia no abafamento de pequenos focos de incêndio.",
         "A manta cobre o foco e reduz seu contato com o oxigênio.",
         "Mantenha acessível e cubra completamente o foco seguindo as orientações de segurança."),
        (r"mascara facial",
         "Protege o rosto e os olhos contra partículas projetadas.",
         "A viseira forma uma barreira física transparente.",
         "Ajuste a carneira e confirme a cobertura antes do uso."),
        (r"caixa segredo|mini cofre",
         "Armazena chaves ou pequenos objetos com acesso por senha.",
         "O mecanismo de combinação libera a tampa após o código correto.",
         "Fixe em base resistente, defina uma combinação e teste antes do uso."),
        (r"selim.*bicicleta",
         "Apoia o ciclista durante a condução da bicicleta.",
         "A estrutura distribui o peso sobre o trilho e o canote.",
         "Fixe os trilhos ao canote e ajuste altura, recuo e inclinação."),
        (r"base.*antena",
         "Sustenta e conecta a antena no ponto original do veículo.",
         "A base mantém a fixação mecânica e a continuidade do sinal.",
         "Instale no furo original, vede contra água e conecte o cabo."),
        (r"difusor.*ar",
         "Direciona o fluxo de ar das saídas de ventilação.",
         "As aletas móveis alteram a direção e a abertura do fluxo.",
         "Encaixe no alojamento preservando travas e dutos internos."),
        (r"puxador|macaneta",
         "Permite puxar, abrir ou acionar a porta ou tampa correspondente.",
         "O movimento é transferido ao mecanismo de abertura.",
         "Conecte hastes ou cabos e fixe a peça na posição e lado corretos."),
        (r"bandeja dianteira",
         "Liga o conjunto da roda ao chassi e orienta o movimento da suspensão.",
         "Buchas e articulações permitem o curso mantendo a geometria da roda.",
         "Instale com apoio seguro, aperte os fixadores corretamente e faça alinhamento."),
        (r"carburador",
         "Dosifica a mistura de ar e combustível fornecida ao motor.",
         "O fluxo pelo venturi aspira combustível pelos circuitos de dosagem.",
         "Instale com junta íntegra, conecte cabos e mangueiras e faça os ajustes necessários."),
        (r"reservatorio.*carvao|sistema evap",
         "Armazena vapores de combustível para posterior queima pelo motor.",
         "O carvão ativado retém os vapores até a purga para a admissão.",
         "Conecte cada mangueira no ponto correto e verifique trincas e vedação."),
        (r"rolamento|coxim.*mancal",
         "Apoia o eixo e permite sua rotação com menor atrito.",
         "Os elementos rolantes giram entre as pistas mantendo o alinhamento.",
         "Prense e alinhe sem transmitir esforço pelas pistas inadequadas."),
    )
    for pattern, purpose, functioning, installation in rules:
        if re.search(pattern, folded):
            return {
                "para_que_serve": [purpose],
                "modo_de_funcionamento": [functioning],
                "instalação": [installation],
            }
    return {"para_que_serve": [], "modo_de_funcionamento": [], "instalação": []}


def _what_it_is(product_name: str, application_type: str) -> str:
    """Fornece uma definição substantiva da classe do produto, sem repetir o título."""
    folded = _fold(product_name)
    rules = (
        (r"repelente.*(?:cachorro|cao|caes)", "É um aparelho eletrônico portátil que emite ultrassom para afastamento ou condicionamento de cães."),
        (r"forscan|scanner automotivo|interface.*diagnostico", "É uma interface eletrônica de diagnóstico que liga o conector OBD2 do veículo a um computador."),
        (r"interruptor termico|cebolao", "É um interruptor acionado pela temperatura do líquido de arrefecimento para comandar a ventoinha."),
        (r"(?:modulo|resistencia).*(?:eletroventoinha|eletroventilador|ventoinha)", "É um componente elétrico de controle da corrente e da velocidade do eletroventilador."),
        (r"tampa.*(?:reservatorio|radiador)", "É uma tampa de vedação, normalmente calibrada para manter ou aliviar a pressão do sistema de arrefecimento."),
        (r"diafragma.*pcv|membrana.*pcv", "É a membrana flexível do conjunto de ventilação positiva do cárter, a válvula PCV."),
        (r"sensor inteligente.*bateria|sensor.*corrente.*bateria", "É um sensor inteligente de bateria instalado no terminal negativo para o gerenciamento elétrico do veículo."),
        (r"medidor.*massa.*ar|sensor maf|fluxo de ar", "É um sensor eletrônico instalado na admissão para medir a massa ou o fluxo de ar do motor."),
        (r"sensor|boia.*combustivel|marcador.*nivel", "É um componente eletroeletrônico que detecta uma grandeza física e envia um sinal ao sistema de controle."),
        (r"atuador.*vacuo", "É um atuador mecânico com diafragma, movimentado pela depressão da linha de vácuo."),
        (r"cilindro.*hidraulico.*embreagem", "É um cilindro hidráulico do sistema de acionamento da embreagem."),
        (r"engrenagem.*(?:reparo|atuador)", "É uma engrenagem interna de reposição para o mecanismo do atuador da caixa de transferência."),
        (r"atuador|motor.*trava|fechadura|tranca", "É um mecanismo de acionamento que movimenta a peça ou trava correspondente."),
        (r"motoredutor", "É um conjunto formado por motor elétrico e caixa de redução de velocidade."),
        (r"motor.*partida|motor.*arranque", "É um motor elétrico de alta corrente que gira o motor do veículo durante a partida."),
        (r"bomba", "É um componente de bombeamento destinado a movimentar o fluido indicado no circuito."),
        (r"mangueira|tubo|flange|conexao.*agua", "É um componente de condução ou união usado para manter o fluxo e a vedação do circuito."),
        (r"radiador", "É um trocador de calor do sistema de arrefecimento, formado por canais de fluido e aletas."),
        (r"filtro", "É um elemento filtrante que retém partículas ou contaminantes do fluido ou do ar."),
        (r"valvula|torneira.*combustivel", "É uma válvula que abre, fecha ou regula a passagem no circuito correspondente."),
        (r"vela.*ignicao", "É uma vela de ignição que produz a centelha dentro da câmara de combustão."),
        (r"bobina.*ignicao|estator|reator.*xenon", "É um componente elétrico de transformação ou controle de energia do sistema indicado."),
        (r"lampada|\bled\b|pisca|seta|lanterna|luz.*freio", "É um componente de iluminação ou sinalização que utiliza uma fonte luminosa elétrica."),
        (r"refletor|olho de gato", "É uma peça retrorrefletiva passiva, sem circuito de iluminação próprio."),
        (r"tela|digitalizador|painel.*instrumentos", "É um componente de interface visual ou sensível ao toque para equipamento eletrônico."),
        (r"botao|interruptor|comutador|chave", "É um comando eletromecânico que abre, fecha ou seleciona funções de um circuito."),
        (r"conector|terminal|chicote|cabo", "É um componente de interligação elétrica ou de sinal entre equipamentos."),
        (r"engrenagem|coroa.*bicicleta|roda livre", "É um componente mecânico dentado ou de acoplamento para transmitir movimento e torque."),
        (r"rolamento|coxim.*mancal", "É um componente de apoio de eixo que reduz o atrito e mantém o alinhamento da rotação."),
        (r"retentor|anel.*vedacao|junta|diafragma|membrana|presilha|abracadeira", "É um componente de vedação ou fixação usado entre partes de um conjunto."),
        (r"\b(?:tampas?|capas?|carcacas?|molduras?|frisos?|apliques?|grades?|emblemas?|logos?)\b", "É uma peça de fechamento, proteção ou acabamento moldada para o ponto de aplicação."),
        (r"ponteiras?.*escapamento", "É uma peça metálica de acabamento instalada na extremidade externa do escapamento."),
        (r"manopla|bola.*cambio|puxador|macaneta", "É uma peça de acionamento manual que transmite o movimento do usuário ao mecanismo."),
        (r"carburador", "É um dispositivo mecânico de alimentação que dosa ar e combustível para o motor."),
        (r"suporte|guia|dobradica|bandeja", "É uma peça estrutural de fixação, alinhamento ou articulação do conjunto indicado."),
        (r"caneta capacitiva", "É um instrumento apontador com ponta capacitiva para telas sensíveis ao toque."),
        (r"talha|enxada rotativa|broca.*solo|pulverizador|aspersor|nebulizador", "É uma ferramenta ou implemento mecânico destinado à operação indicada."),
        (r"cadeira", "É um móvel de assento composto por base, assento, encosto e mecanismos de apoio ou regulagem."),
        (r"selim.*bicicleta", "É o componente de apoio do ciclista fixado ao canote da bicicleta."),
    )
    for pattern, description in rules:
        if re.search(pattern, folded):
            return description
    fallbacks = {
        "automóvel": "É uma peça de reposição automotiva destinada ao sistema e aos veículos indicados na compatibilidade.",
        "motocicleta": "É uma peça ou acessório de reposição para motocicleta.",
        "ferramenta automotiva": "É uma ferramenta destinada a diagnóstico, teste ou manutenção automotiva.",
        "náutica": "É um componente ou acessório destinado a embarcações e equipamentos náuticos.",
        "universal veicular": "É um acessório de aplicação veicular universal, condicionado às especificações de montagem.",
    }
    return fallbacks.get(application_type, "É um componente, acessório ou equipamento de uso não veicular.")


def _technical_from_name(product_name: str) -> list[str]:
    """Extrai somente especificações declaradas literalmente no nome."""
    text = _repair_text(product_name)
    folded = _fold(text)
    output: list[str] = []

    material_terms = (
        ("aço inox", "Aço inoxidável"), ("aco inox", "Aço inoxidável"),
        ("alumínio", "Alumínio"), ("aluminio", "Alumínio"),
        ("silicone", "Silicone"), ("borracha", "Borracha"), ("plástico", "Plástico"),
        ("plastico", "Plástico"), ("fibra de vidro", "Fibra de vidro"),
        ("madeira ecológica", "Madeira ecológica WPC"), ("madeira ecologica", "Madeira ecológica WPC"),
    )
    for term, normalized in material_terms:
        if term in folded:
            output.append(f"Material: {normalized}.")
            break

    color_match = re.search(r"(?i)\bcor\s*:\s*([^,;|]+)", text)
    if color_match:
        color = _repair_text(color_match.group(1))[:45]
        if color:
            output.append(f"Cor: {color}.")

    side_match = re.search(r"(?i)\blado\s*:\s*(direit[oa]|esquerd[oa])", text)
    if side_match:
        output.append(f"Lado: {side_match.group(1).capitalize()}.")

    specs = (
        (r"(?i)(?<!\d)(\d+(?:[.,]\d+)?)\s*v(?:\s*cc|\s*dc)?\b", "Tensão", "V"),
        (r"(?i)(?<!\d)(\d+(?:[.,]\d+)?)\s*w\b", "Potência", "W"),
        (r"(?i)(?<!\d)(\d+(?:[.,]\d+)?)\s*rpm\b", "Rotação", "RPM"),
        (r"(?i)(?<!\d)(\d+(?:[.,]\d+)?)\s*mhz\b", "Frequência", "MHz"),
        (r"(?i)(?<!\d)(\d+(?:[.,]\d+)?)\s*k\b", "Temperatura de cor", "K"),
        (r"(?i)(?<!\d)(\d+)\s*leds?\b", "Quantidade de LEDs", ""),
        (r"(?i)(?<!\d)(\d+)\s*marchas?\b", "Quantidade de marchas", ""),
        (r"(?i)(?<!\d)(\d+)\s*pinos?\b", "Quantidade de pinos", ""),
    )
    for pattern, label, unit in specs:
        match = re.search(pattern, text)
        if match:
            if label == "Tensão" and re.search(
                r"(?i)\b(?:motor\s+)?\d[.,]\d\s*(?:8|16|24)v\b",
                text,
            ):
                continue
            value = match.group(1).replace(".", ",")
            output.append(f"{label}: {value}{(' ' + unit) if unit else ''}.")

    ip_match = re.search(r"(?i)\bIP\s*(\d{2})\b", text)
    if ip_match:
        output.append(f"Grau de proteção: IP{ip_match.group(1)}.")
    if "iridio" in folded or "irídio" in folded:
        output.append("Material do eletrodo: irídio.")
    elif "platina" in folded:
        output.append("Material do eletrodo: platina.")

    quantity_match = re.search(r"(?i)\b(?:jogo|conjunto)\s+(?:com\s+)?(\d+)\b|\b(\d+)\s*(?:un|unidades|p[cç]s)\b", text)
    if quantity_match:
        value = next(group for group in quantity_match.groups() if group)
        output.append(f"Quantidade do conjunto: {value} unidades.")
    elif re.search(r"(?i)^par\b|\(\s*par\s*\)", text):
        output.append("Quantidade do conjunto: 2 unidades.")

    return _dedupe_text(output, limit=12)


def _dedupe_text(items: Iterable[Any], *, limit: int = 20) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for item in items or []:
        text = _sanitize_output_text(item)
        if not text or _is_missing_value(text):
            continue
        text = text.rstrip(" .") + "."
        key = _fold(text).rstrip(".")
        if key and key not in seen:
            seen.add(key)
            output.append(text)
        if len(output) >= limit:
            break
    return output


def _tokens(value: Any) -> set[str]:
    return {
        token for token in re.findall(r"[a-z0-9]{3,}", _fold(value))
        if token not in {_fold(word) for word in STOPWORDS} and not YEAR_RE.fullmatch(token)
    }


def _product_class(value: Any) -> str:
    folded = _fold(value)
    for class_name, terms in PRODUCT_CLASSES:
        if any(term in folded for term in terms):
            return class_name
    return ""


def _ad_matches_product(product_name: str, ad_title: str, sku: str) -> bool:
    if sku in IDENTITY_CONFLICT_SKUS:
        return False
    product_class = _product_class(product_name)
    ad_class = _product_class(ad_title)
    if product_class and ad_class and product_class != ad_class:
        return False
    product_tokens = _tokens(product_name)
    ad_tokens = _tokens(ad_title)
    shared = product_tokens & ad_tokens
    shared_codes = {
        token for token in shared
        if len(token) >= 6 and any(char.isdigit() for char in token) and any(char.isalpha() for char in token)
    }
    if shared_codes:
        return True
    meaningful = {token for token in shared if token not in {"sensor", "motor", "carro", "veiculo", "veículo"}}
    return bool(meaningful) or bool(product_class and product_class == ad_class and len(shared) >= 2)


def _universal_product_hint(product_name: str) -> bool:
    folded = _fold(product_name)
    return any(term in folded for term in (
        "universal", "presilhas grampos", "grampos fixadores", "terminal bateria c/ engate",
        "terminal bateria com engate", "extensor bico pneu", "adaptador cinto", "anti alarme",
        "olho de aguia carro moto", "olho de águia carro moto",
    ))


def _product_model_hints(product_name: str) -> set[str]:
    hints: set[str] = set()
    parsed = _parse_vehicle_text(product_name)
    if parsed:
        hints.update(_tokens(parsed.get("modelo")))
    for token in re.findall(r"\b[A-Za-z]*\d+[A-Za-z0-9-]*\b", _fold(product_name)):
        compact = re.sub(r"[^a-z0-9]", "", token)
        if 2 <= len(compact) <= 6 and not YEAR_RE.fullmatch(compact) and not re.fullmatch(r"\d+[.,]\d+", compact):
            hints.add(compact)
    return {hint for hint in hints if hint not in {"16v", "24v", "12v", "10mm", "18mm", "4x4", "2x4"}}


def _find_brand(value: str) -> tuple[str, int, int] | None:
    folded = _fold(value)
    for canonical, aliases in VEHICLE_BRANDS:
        for alias in aliases:
            match = re.search(rf"(?<![a-z0-9]){re.escape(_fold(alias))}(?![a-z0-9])", folded)
            if match:
                return canonical, match.start(), match.end()
    return None


def _clean_vehicle_model(value: Any) -> str:
    model = _repair_text(value)
    for pattern, replacement in (
        (r"\bconvertible\b", "conversível"),
        (r"\bcoupe\b", "cupê"),
        (r"\bwagon\b", "perua"),
        (r"\b([2345])-doors?\b", r"\1 portas"),
        (r"\b([2345])-portas?\b", r"\1 portas"),
    ):
        model = re.sub(pattern, replacement, model, flags=re.IGNORECASE)
    model = re.sub(r"(?i)^BR\s+", "", model)
    model = re.sub(r"\.{2,}", " ", model)
    model = re.sub(r"\s+", " ", model).strip(" .,:;/-")
    model = re.sub(r"(?i)^(?:para|compat[ií]vel\s+com|com)\s+", "", model)
    model = re.sub(
        r"(?i)(?:\s*[,;/–—-]\s*)?(?:para|anos?[-\s]?modelo|ano)\s*$",
        "",
        model,
    ).strip(" .,:;/–—-")
    model = re.sub(r"(?i)\s+(?:de\s+)?\d{2,4}\s+a\s*$", "", model)
    model = re.sub(r"(?i)\s+a\s+partir\s+de\s*$", "", model)
    model = re.sub(r"\s*[\(\[\{]+\s*$", "", model)
    model = re.sub(r"^[*#]+\s*", "", model)
    model = re.split(
        r"(?i)\b(?:aplica[cç][aã]o|compat[ií]vel|produto|descri[cç][aã]o|instala[cç][aã]o|"
        r"especifica[cç][aã]o|funcionamento|garantia|n[uú]mero da pe[cç]a|material|tamanho|"
        r"press[aã]o|valor referente|fabricante|selecione|aten[cç][aã]o|utiliza[cç][aã]o|"
        r"v[aá]rios modelos|adaptador|sensor|cabo|suporte|farol|grade|tampa|bomba|mangueira|"
        r"refletor|pisca|kit|conjunto|motor|promo[cç][aã]o|cor|pe[cç]as)\b",
        model,
        maxsplit=1,
    )[0].strip(" .,:;/-")
    model = re.sub(r"(?i)\s+\b(?:com|para|após|de)\b\s*$", "", model).strip(" .,:;/–—-")
    model = re.sub(r"\s*[\(\[\{]+\s*$", "", model).strip(" .,:;/–—-")
    if re.search(
        r"(?i)\b(?:radiator|switch|coolant|overflow|hose|genuine|catalogo oficial|catálogo oficial|identifica|"
        r"como mangueira|replacement part|usa - engine)\b",
        model,
    ):
        return ""
    first_token = (model.split() or [""])[0]
    compact_first = re.sub(r"[^A-Za-z0-9]", "", first_token)
    if (
        len(compact_first) >= 7
        and any(char.isalpha() for char in compact_first)
        and any(char.isdigit() for char in compact_first)
        and not re.search(
            r"\b(?:A\d|Q\d|X\d|CBR?|VT\d|[RFG]\d{3,4}GS)",
            model,
            flags=re.IGNORECASE,
        )
    ):
        return ""
    if re.search(
        r"(?i)^(?:pe[cç]a primeira linha|eles somente|preto e vermelho|logo\s*:|clean\b|"
        r"tfsi\b|tsi\b|!+$|de alum[ií]nio|cor\s*:|\d+mm\b)",
        model,
    ):
        return ""
    if _fold(model) in {"peugeot e", "audi tfsi", "volkswagen tsi", "bmw preto e vermelho"}:
        return ""
    tokens = model.split()
    numeric_tokens = sum(bool(re.fullmatch(r"[\d.,/-]+", token)) for token in tokens)
    if len(tokens) >= 3 and numeric_tokens / len(tokens) >= 0.8:
        return ""
    known_models = {
        "cb500": "CB 500",
        "cb600fhornet": "CB 600F Hornet",
        "vt600shadow": "VT600 Shadow",
        "vt750shadow": "VT750 Shadow",
    }
    compact = re.sub(r"[^a-z0-9]", "", _fold(model))
    if compact in known_models:
        model = known_models[compact]
    return model[:90]


def _parse_catalog_vehicle_name(name: str) -> dict[str, Any]:
    text = _repair_text(name)
    brand_match = _find_brand(text)
    if not brand_match:
        return {}
    brand, _brand_start, brand_end = brand_match
    folded = _fold(text)
    year_matches = list(YEAR_RE.finditer(folded[brand_end:]))
    year_match = None
    for index, candidate in enumerate(year_matches):
        year = int(candidate.group(1))
        prefix = folded[brand_end : brand_end + candidate.start()].strip()
        if brand == "Peugeot" and year == 2008 and not prefix and len(year_matches) > index + 1:
            continue
        year_match = candidate
        break
    if not year_match:
        return {}
    year = int(year_match.group(1))
    year_start = brand_end + year_match.start()
    year_end = brand_end + year_match.end()
    model = text[brand_end:year_start].strip(" -–—/,;")
    if brand == "Peugeot" and not model and year == 2008:
        return {}
    model = _clean_vehicle_model(model)
    if not model:
        return {}
    tokens = model.split()
    engine_index = next((index for index, token in enumerate(tokens) if re.fullmatch(r"\d+(?:[.,]\d+)?L", token, flags=re.IGNORECASE)), None)
    if engine_index is not None and engine_index > 0:
        tokens = tokens[:engine_index]
    if len(tokens) > 5:
        tokens = tokens[:5]
    model = " ".join(tokens)
    version = text[year_end:].strip(" -–—/,;")
    version = re.sub(r"\s+BR$", "", version, flags=re.IGNORECASE).strip()
    return {"marca": brand, "modelo": model, "ano": year, "versao": version}


def _compress_years(years: Iterable[int]) -> list[str]:
    ordered = sorted({int(year) for year in years if 1950 <= int(year) <= 2039})
    if not ordered:
        return []
    groups: list[tuple[int, int]] = []
    start = previous = ordered[0]
    for year in ordered[1:]:
        if year == previous + 1:
            previous = year
            continue
        groups.append((start, previous))
        start = previous = year
    groups.append((start, previous))
    return [str(start) if start == end else f"{start} a {end}" for start, end in groups]


def _years_from_text(value: str, *, brand: str = "", model: str = "") -> list[str]:
    text = _repair_text(value)
    folded = _fold(text)
    ranges = []
    covered: list[tuple[int, int]] = []
    for match in YEAR_RANGE_RE.finditer(folded):
        start, end = int(match.group(1)), int(match.group(2))
        if start <= end <= 2039:
            ranges.append(f"{start} a {end}")
            covered.append(match.span())
    singles = []
    for match in YEAR_RE.finditer(folded):
        year = int(match.group(1))
        if any(start <= match.start() < end for start, end in covered):
            continue
        if _fold(brand) == "peugeot" and year == 2008 and _fold(model) in {"", "2008"}:
            continue
        singles.append(year)
    for value in _compress_years(singles):
        if value not in ranges:
            ranges.append(value)
    return ranges


def _parse_vehicle_text(value: str) -> dict[str, Any]:
    text = _repair_text(value)
    brand_match = _find_brand(text)
    if not brand_match:
        folded = _fold(text)
        motorcycle_models = (
            ("Honda", r"\b(cb\s*500|cb\s*600f|hornet|shadow|cbr\s*600|cbr\s*900|cbr\s*1000rr|cbr\s*1100xx)\b"),
        )
        for brand, pattern in motorcycle_models:
            match = re.search(pattern, folded, flags=re.IGNORECASE)
            if match:
                model = text[match.start() : match.end()].strip()
                years = _years_from_text(text, brand=brand, model=model)
                return {"marca": brand, "modelo": model, "anos": years}
        return {}
    brand, _start, end = brand_match
    folded = _fold(text)
    year_match = next(iter(YEAR_RE.finditer(folded[end:])), None)
    if year_match:
        year_start = end + year_match.start()
        model = text[end:year_start].strip(" -–—/:,;")
    else:
        model = text[end:].strip(" -–—/:,;")
    model = re.split(r"(?i)\b(?:codigo|código|oem|material|marca)\b", model, maxsplit=1)[0]
    model = _clean_vehicle_model(model)
    if brand == "Peugeot" and not model and year_match and year_match.group(1) == "2008":
        model = "2008"
        remaining = folded[end + year_match.end() :]
        next_year = YEAR_RE.search(remaining)
        years = [next_year.group(1)] if next_year else []
    else:
        years = _years_from_text(text, brand=brand, model=model)
    if not model or len(model) > 90:
        return {}
    tokens = model.split()
    engine_index = next((index for index, token in enumerate(tokens) if re.fullmatch(r"\d+(?:[.,]\d+)?L", token, flags=re.IGNORECASE)), None)
    if engine_index is not None and engine_index > 0:
        tokens = tokens[:engine_index]
    if len(tokens) > 5:
        model = " ".join(tokens[:5])
    else:
        model = " ".join(tokens)
    if not years and any(
        term in _fold(model) for term in ("celular", "gps", "radio", "rádio", "aux", "menu", "peça", "qualidade")
    ):
        return {}
    return {"marca": brand, "modelo": model, "anos": years}


def _vehicle_rows_from_text(value: str, *, require_year: bool = False) -> list[dict[str, Any]]:
    text = _repair_text(value)
    folded = _fold(text)
    occurrences: list[tuple[int, int, str]] = []
    for canonical, aliases in VEHICLE_BRANDS:
        for alias in aliases:
            for match in re.finditer(rf"(?<![a-z0-9]){re.escape(_fold(alias))}(?![a-z0-9])", folded):
                occurrences.append((match.start(), match.end(), canonical))
    occurrences.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    deduped = []
    seen_positions = set()
    for occurrence in occurrences:
        if occurrence[0] in seen_positions:
            continue
        seen_positions.add(occurrence[0])
        deduped.append(occurrence)
    if not deduped:
        parsed = _parse_vehicle_text(text)
        if parsed and (not require_year or bool(parsed.get("anos"))):
            return [parsed]
        return []
    output = []
    for index, (start, _end, _canonical) in enumerate(deduped):
        finish = deduped[index + 1][0] if index + 1 < len(deduped) else len(text)
        segment = text[start : min(finish, start + 240)]
        parsed = _parse_vehicle_text(segment)
        if parsed and (not require_year or bool(parsed.get("anos"))):
            output.append(parsed)
    return output


def _normalize_year_labels(values: Iterable[Any]) -> list[str]:
    """Remove duplicatas sem ampliar uma faixa de aplicação por inferência."""
    singles: set[int] = set()
    intervals: list[tuple[int, int]] = []
    other_values: list[str] = []
    for raw_value in values or []:
        value = _repair_text(raw_value)
        if not value or _is_missing_value(value):
            continue
        single = re.fullmatch(r"(19[5-9]\d|20[0-3]\d)", value)
        interval = re.fullmatch(
            r"(19[5-9]\d|20[0-3]\d)\s*(?:a|até|ate|[-–—/])\s*(19[5-9]\d|20[0-3]\d)",
            value,
            flags=re.IGNORECASE,
        )
        if single:
            singles.add(int(single.group(1)))
        elif interval and int(interval.group(1)) <= int(interval.group(2)):
            pair = (int(interval.group(1)), int(interval.group(2)))
            if pair not in intervals:
                intervals.append(pair)
        elif value not in other_values:
            other_values.append(value)
    singles = {
        year for year in singles
        if not any(start <= year <= end for start, end in intervals)
    }
    interval_labels = [str(start) if start == end else f"{start} a {end}" for start, end in intervals]
    return [*_compress_years(singles), *interval_labels, *other_values]


def _merge_vehicle_rows(
    rows: Iterable[dict[str, Any]],
    *,
    limit: int = 180,
    sku: str = "",
    clean_models: bool = True,
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        brand = _repair_text(row.get("marca"))
        raw_model = _repair_text(row.get("modelo"))
        if (
            _fold(brand), _fold(raw_model)
        ) in (RAW_VEHICLE_EXCLUSIONS.get(sku) or set()):
            continue
        model = _clean_vehicle_model(raw_model) if clean_models else raw_model
        if not brand or not model:
            continue
        model_key = re.sub(r"[^a-z0-9]", "", _fold(model))
        key = (_fold(brand), model_key)
        group = groups.setdefault(
            key,
            {"marca": brand, "modelo": model, "anos": [], "restrições": []},
        )
        for year in row.get("anos") or []:
            year = _repair_text(year)
            if year and not _is_missing_value(year) and year not in group["anos"]:
                group["anos"].append(year)
        restrictions = row.get("restrições") or row.get("restricoes") or []
        if isinstance(restrictions, str):
            restrictions = [restrictions]
        for restriction in restrictions:
            restriction = _sanitize_output_text(restriction)
            if restriction and not _is_missing_value(restriction) and restriction not in group["restrições"]:
                group["restrições"].append(restriction)
    output = []
    for group in groups.values():
        group["anos"] = _normalize_year_labels(group["anos"])
        if not group["restrições"]:
            group.pop("restrições")
        output.append(group)
    precise_by_brand: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in output:
        if item["anos"]:
            precise_by_brand[_fold(item["marca"])].append(item)
    pruned = []
    for item in output:
        if not item["anos"]:
            precise = precise_by_brand.get(_fold(item["marca"])) or []
            item_tokens = _tokens(item["modelo"])
            if len(precise) >= 2 or any(item_tokens & _tokens(other["modelo"]) for other in precise):
                continue
        pruned.append(item)
    pruned.sort(key=lambda item: (_fold(item["marca"]), _fold(item["modelo"])))
    return pruned[:limit]


def _compatibility_from_cache(
    sku: str,
    product_name: str,
    cache_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool, int]:
    accepted = [row for row in cache_rows if _ad_matches_product(product_name, row.get("titulo", ""), sku)]
    total_products = sum(len(row.get("produtos") or []) for row in accepted)
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    model_hints = _product_model_hints(product_name)
    for row in accepted:
        for product in row.get("produtos") or []:
            parsed = _parse_catalog_vehicle_name(product.get("nome_catalogo", ""))
            if not parsed:
                continue
            if model_hints:
                parsed_tokens = _tokens(parsed["modelo"]) | {
                    re.sub(r"[^a-z0-9]", "", token)
                    for token in re.findall(r"[a-z0-9-]+", _fold(parsed["modelo"]))
                }
                if not (model_hints & parsed_tokens):
                    continue
            key = (_fold(parsed["marca"]), _fold(parsed["modelo"]))
            group = groups.setdefault(key, {"marca": parsed["marca"], "modelo": parsed["modelo"], "years": set()})
            group["years"].add(parsed["ano"])
    if len(groups) > 180:
        return [], True, total_products
    if total_products > 1500:
        return ([], True, total_products) if _universal_product_hint(product_name) else ([], False, total_products)
    vehicles = [
        {"marca": group["marca"], "modelo": group["modelo"], "anos": _compress_years(group["years"])}
        for group in groups.values()
    ]
    return _merge_vehicle_rows(vehicles, sku=sku), False, total_products


def _compatibility_from_texts(
    sku: str,
    product_name: str,
    row: dict[str, str],
    raw: dict[str, Any],
) -> list[dict[str, Any]]:
    if sku in COMPATIBILITY_OVERRIDES:
        return _merge_vehicle_rows(COMPATIBILITY_OVERRIDES[sku], sku=sku)
    if sku in {"151", "153", "305-10-K", "449-K"}:
        return []
    texts: list[tuple[str, bool]] = [(product_name, False)]
    raw_items = (((raw.get("produto") or {}).get("veiculos_compativeis") or {}).get("itens") or [])
    if sku != "261":
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            sources = [_repair_text(source) for source in item.get("fontes") or []]
            text = _repair_text(item.get("texto"))
            if any(source.startswith("cadastro:") or source.startswith("anuncio:") for source in sources):
                texts.append((text, len(text) > 170))
        for ad in ((raw.get("anuncios") or {}).get("ativos") or []):
            title = _repair_text((ad or {}).get("titulo"))
            if _ad_matches_product(product_name, title, sku):
                texts.append((title, False))
        description = _repair_text(row.get("descricao"))
        if description:
            texts.append((description, True))
    parsed = []
    for text, require_year in texts:
        parsed.extend(_vehicle_rows_from_text(text, require_year=require_year))
    return _merge_vehicle_rows(parsed, sku=sku)


def _application_type(sku: str, name: str, row: dict[str, str], vehicles: list[dict[str, Any]], universal: bool) -> str:
    if sku in APPLICATION_TYPE_OVERRIDES:
        return APPLICATION_TYPE_OVERRIDES[sku]
    folded = _fold(f"{name} {row.get('categoria', '')}")
    if sku in FALSE_AUTOMOTIVE_SKUS:
        return "não veicular"
    if any(term in folded for term in ("bicicleta", "mtb", "motoredutor", "cadeira", "talha", "rocadeira", "roçadeira", "irrigacao", "irrigação", "compressor de ar")):
        return "não veicular"
    if any(term in folded for term in ("scanner automot", "calibrador", "sincronizador", "detector tpms", "forscan")):
        return "ferramenta automotiva"
    if any(term in folded for term in ("volvo penta", "motor de popa", "nautico", "náutico")):
        return "náutica"
    if universal or _universal_product_hint(name) or any(term in folded for term in ("universal veicular", "carro moto", "carro e moto", "moto carro")):
        return "universal veicular"
    vehicle_models = _fold(" ".join(str(item.get("modelo") or "") for item in vehicles))
    if any(term in folded or term in vehicle_models for term in ("moto ", "motocicleta", "hornet", "cb 500", "cb500", "cbr", "r1200gs", "cg 160", "vt600", "vt750")):
        return "motocicleta"
    if sku in TRUE_AUTOMOTIVE_SKUS or vehicles or _find_brand(name):
        return "automóvel"
    if any(term in folded for term in ("automot", "veiculo", "veículo", "carro", "cambio", "câmbio", "radiador", "combustivel", "combustível", "parachoque", "para-choque", "retrovisor", "vela de ignicao", "vela de ignição", "grade superior")):
        return "automóvel"
    return "não veicular"


def _valid_oem_code(value: Any) -> bool:
    code = _repair_text(value).upper()
    compact = re.sub(r"[^A-Z0-9]", "", code)
    if not code or _is_missing_value(code):
        return False
    if re.fullmatch(r"([A-Z0-9])\1{5,}", compact):
        return False
    # EAN/GTIN e variações com um sufixo de controle não são OEM.
    if re.fullmatch(r"\d{13,}[A-Z]?", compact):
        return False
    if any(char.isalpha() for char in compact) and any(char.isdigit() for char in compact) and len(compact) < 6:
        return False
    return True


def _clean_oem(raw: dict[str, Any]) -> list[str]:
    output = []
    for item in ((((raw.get("produto") or {}).get("oem") or {}).get("codigos")) or []):
        sources = [
            _fold(source)
            for source in ((item.get("fontes") or []) if isinstance(item, dict) else [])
            if source
        ]
        if sources and all(
            "bling" in source
            or source.startswith("cadastro_legado:")
            or source.startswith("fornecedor_planilha:")
            for source in sources
        ):
            continue
        code = _repair_text(item.get("codigo") if isinstance(item, dict) else item).upper()
        if not _valid_oem_code(code):
            continue
        if code not in output:
            output.append(code)
    return output[:20]


def _section(items: list[str], _missing: str = "") -> dict[str, Any]:
    """Mantem o schema e deixa campos sem evidencia realmente vazios."""
    items = [item for item in items if not _is_missing_value(item)]
    return {
        "status": "documentado" if items else "",
        "itens": items,
        "observação": "",
    }


def _contains_source_fields(payload: Any) -> bool:
    forbidden = {"fonte", "fontes", "fontes_consultadas", "url", "urls", "link", "links", "referencia", "referências", "referencias"}
    if isinstance(payload, dict):
        for key, value in payload.items():
            if _fold(key) in {_fold(item) for item in forbidden}:
                return True
            if _contains_source_fields(value):
                return True
    elif isinstance(payload, list):
        return any(_contains_source_fields(value) for value in payload)
    return False


def _build_clean_dossier(
    row: dict[str, str],
    raw: dict[str, Any],
    overrides: dict[str, Any],
    cache_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    sku = _repair_text(row.get("sku"))
    name = _clean_product_name(sku, row, overrides)
    product = raw.get("produto") or {}
    description = _repair_text(row.get("descricao"))

    allow_description = sku not in {"151", "153", "305-10-K", "449-K"}
    purpose_raw = _trusted_raw_items(
        ((product.get("para_que_serve") or {}).get("itens") or []),
        raw, name, sku, allow_description=allow_description,
    )
    technical_raw = _trusted_raw_items(
        ((product.get("caracteristicas_tecnicas") or {}).get("itens") or []),
        raw, name, sku, allow_description=allow_description,
    )
    functioning_raw = _trusted_raw_items(
        ((product.get("modo_de_funcionamento") or {}).get("itens") or []),
        raw, name, sku, allow_description=allow_description,
    )
    installation_raw = _trusted_raw_items(
        ((product.get("instalacao") or {}).get("itens") or []),
        raw, name, sku, allow_description=allow_description,
    )
    measure_raw = _trusted_raw_items(
        [
            *(((product.get("medidas") or {}).get("itens") or [])),
            *(((product.get("caracteristicas_tecnicas") or {}).get("itens") or [])),
        ],
        raw, name, sku, allow_description=allow_description, allow_ads=False,
    )

    purpose = _clean_text_items(purpose_raw, limit=5, product_name=name)
    if not purpose and allow_description:
        purpose = _purpose_from_description(description)
    technical = _dedupe_text(
        [*_technical_items(technical_raw, product_name=name), *_technical_from_name(name)],
        limit=12,
    )
    functioning = _functional_items(functioning_raw, product_name=name)
    installation = _installation_items(installation_raw, product_name=name)
    measures = []
    if sku in MEASURE_OVERRIDES:
        measures = list(MEASURE_OVERRIDES[sku])
    elif sku in TRUSTED_MEASURE_SKUS:
        measures = list(_clean_measures(measure_raw, f"{name}. {description}"))
    oem = []
    for code in [*_clean_oem(raw), *(OEM_OVERRIDES.get(sku) or [])]:
        code = _repair_text(code).upper()
        if code and code not in oem:
            oem.append(code)

    cache_vehicles, universal, compatibility_count = _compatibility_from_cache(sku, name, cache_rows)
    text_vehicles = _compatibility_from_texts(sku, name, row, raw)
    vehicles = cache_vehicles if cache_vehicles else text_vehicles
    application_type = _application_type(sku, name, row, vehicles, universal)
    if application_type in {"não veicular", "ferramenta automotiva", "náutica"}:
        vehicles = []
    if application_type == "universal veicular":
        vehicles = []

    equipment_items = _clean_text_items(
        ((product.get("equipamentos_ou_aplicacoes_compativeis") or {}).get("itens") or []),
        limit=10,
        product_name=name,
    )
    equipment_items_are_curated = False
    content_override = CONTENT_OVERRIDES.get(sku) or {}
    knowledge = _knowledge_content(name)
    purpose = list(content_override.get("para_que_serve") or knowledge["para_que_serve"] or purpose)
    technical = _dedupe_text(content_override.get("características_técnicas") or technical, limit=12)
    functioning = list(content_override.get("modo_de_funcionamento") or knowledge["modo_de_funcionamento"] or functioning)
    installation = list(content_override.get("instalação") or knowledge["instalação"] or installation)
    if "equipamentos" in content_override:
        equipment_items = list(content_override.get("equipamentos") or [])
        equipment_items_are_curated = True

    sku_override = overrides.get(sku) or overrides.get(_fold(sku)) or {}
    if isinstance(sku_override, dict):
        purpose = list(sku_override.get("para_que_serve") or purpose)
        technical = _dedupe_text(sku_override.get("características_técnicas") or technical, limit=12)
        if sku_override.get("_medidas_pesquisa_autoritativas"):
            measures = list(sku_override.get("medidas_do_produto") or [])
        else:
            measures = list(sku_override.get("medidas_do_produto") or measures)
        functioning = list(sku_override.get("modo_de_funcionamento") or functioning)
        installation = list(sku_override.get("instalação") or installation)
        if "equipamentos_ou_aplicações_compatíveis" in sku_override:
            equipment_items = list(sku_override.get("equipamentos_ou_aplicações_compatíveis") or [])
            equipment_items_are_curated = True
        override_oem = sku_override.get("oem") or []
        if override_oem or sku_override.get("_oem_pesquisa_autoritativo"):
            oem = []
        for code in override_oem:
            code = _repair_text(code).upper()
            if code and code not in oem:
                oem.append(code)
        if "veículos_compatíveis" in sku_override:
            vehicles = _merge_vehicle_rows(
                sku_override.get("veículos_compatíveis") or [],
                sku=sku,
            )
        override_type = _repair_text(sku_override.get("tipo"))
        if override_type:
            application_type = override_type
    if sku in FINAL_VEHICLE_OVERRIDES:
        vehicles = _merge_vehicle_rows(FINAL_VEHICLE_OVERRIDES[sku], sku=sku)
    final_content = FINAL_CONTENT_OVERRIDES.get(sku) or {}
    if "para_que_serve" in final_content:
        purpose = list(final_content.get("para_que_serve") or [])
    if "características_técnicas" in final_content:
        technical = list(final_content.get("características_técnicas") or [])
    if "modo_de_funcionamento" in final_content:
        functioning = list(final_content.get("modo_de_funcionamento") or [])
    if "instalação" in final_content:
        installation = list(final_content.get("instalação") or [])
    if "equipamentos" in final_content:
        equipment_items = list(final_content.get("equipamentos") or [])
        equipment_items_are_curated = True
    purpose = _dedupe_text(purpose, limit=8)
    if sku in FINAL_TECHNICAL_OVERRIDES:
        technical = list(FINAL_TECHNICAL_OVERRIDES[sku])
    technical = _resolve_technical_conflicts(technical)
    if sku in FINAL_MEASURE_OVERRIDES:
        measures = list(FINAL_MEASURE_OVERRIDES[sku])
    measures = [
        cleaned.rstrip(" .")
        for item in measures
        if (cleaned := _sanitize_output_text(item)) and not _is_missing_value(cleaned)
    ]
    functioning = _dedupe_text(functioning, limit=8)
    installation = _dedupe_text(installation, limit=8)
    equipment_items = _dedupe_text(equipment_items, limit=30)
    if not equipment_items_are_curated:
        equipment_items = []
    excluded_vehicles = VEHICLE_EXCLUSIONS.get(sku) or set()
    if excluded_vehicles:
        vehicles = [
            vehicle for vehicle in vehicles
            if (_fold(vehicle.get("marca")), _fold(vehicle.get("modelo"))) not in excluded_vehicles
        ]
    if application_type in {"não veicular", "ferramenta automotiva", "náutica", "universal veicular"}:
        vehicles = []
    if sku in FORCE_EMPTY_VEHICLES:
        vehicles = []
    oem = [
        code for code in oem
        if _valid_oem_code(code) and code not in (OEM_EXCLUSIONS.get(sku) or set())
    ]
    if sku in OEM_FINAL_OVERRIDES and (
        sku in OEM_FINAL_SUPERSEDES_RESEARCH
        or not (isinstance(sku_override, dict) and sku_override.get("_oem_pesquisa_autoritativo"))
    ):
        oem = list(OEM_FINAL_OVERRIDES[sku])

    if sku in FORCE_EMPTY_DETAIL_SKUS:
        purpose = []
        technical = []
        measures = []
        functioning = []
        installation = []
        oem = []
        vehicles = []
        equipment_items = []

    # Quando cadastro, anúncios e pesquisa descrevem produtos diferentes para o
    # mesmo SKU, não aproveitamos nenhum dado por aproximação. O arquivo conserva
    # apenas a identificação do SKU e os campos de conteúdo ficam vazios.
    if sku in FORCE_EMPTY_DOSSIER_SKUS:
        name = ""
        purpose = []
        technical = []
        measures = []
        functioning = []
        installation = []
        oem = []
        vehicles = []
        equipment_items = []
        application_type = ""

    pending = []
    untranslated = _untranslated_name_words(name)
    if untranslated:
        pending.append("nome_contém_termo_estrangeiro_não_traduzido")
    if sku in FORCE_IDENTITY_PENDING or (
        sku in IDENTITY_CONFLICT_SKUS
        and not (isinstance(sku_override, dict) and sku_override.get("_identidade_confirmada"))
    ):
        pending.append("conflito_de_identidade_ou_aplicação")

    vehicle_status = ""
    vehicle_observation = ""
    if application_type in {"automóvel", "motocicleta", "universal veicular"}:
        vehicle_status = "documentado" if vehicles else ""

    dossier = {
        "schema_version": SCHEMA_VERSION,
        "sku": sku,
        "nome_produto": name,
        "o_que_é": (
            ""
            if sku in FORCE_EMPTY_DOSSIER_SKUS or sku in FORCE_EMPTY_DETAIL_SKUS
            else FINAL_WHAT_IT_IS_OVERRIDES.get(sku) or _what_it_is(name, application_type)
        ),
        "para_que_serve": _section(purpose),
        "características_técnicas": _section(technical),
        "medidas_do_produto": {
            "status": "documentado" if measures else "",
            "itens": measures,
            "observação": "",
        },
        "modo_de_funcionamento": _section(functioning),
        "instalação": _section(installation),
        "oem": {
            "status": "documentado" if oem else "",
            "códigos": oem,
            "observação": "",
        },
        "aplicação": {
            "tipo": application_type,
            "veículos_compatíveis": {
                "status": vehicle_status,
                "itens": vehicles,
                "observação": vehicle_observation,
            },
            "equipamentos_ou_aplicações_compatíveis": _section(equipment_items),
        },
        "revisão": {
            "status": "revisado",
            "nome_em_português_do_brasil": bool(name) and not untranslated,
            "medidas_de_embalagem_removidas": True,
            "anos_exibidos_em_cada_modelo": (
                all(bool(item.get("anos")) for item in vehicles)
                if vehicles
                else application_type not in {"automóvel", "motocicleta"}
            ),
            "pendências": pending,
        },
        "atualizado_em": datetime.now(timezone.utc).isoformat(),
    }
    if _contains_source_fields(dossier):
        raise ValueError(f"SKU {sku}: campo de fonte ou URL encontrado no arquivo final")
    return dossier


def _format_research_value(value: Any) -> str:
    if isinstance(value, float):
        text = f"{value:.6f}".rstrip("0").rstrip(".")
    else:
        text = _repair_text(value)
    return text.replace(".", ",") if re.fullmatch(r"-?\d+(?:\.\d+)?", text) else text


def _research_result_to_override(result: dict[str, Any]) -> dict[str, Any]:
    override: dict[str, Any] = {}
    sku = _repair_text(result.get("sku"))
    name = _repair_text(result.get("nome_produto_pt_br") or result.get("nome_produto"))
    if name:
        override["nome_produto"] = name
    if _fold(result.get("status_identidade")).startswith("confirmada"):
        override["_identidade_confirmada"] = True

    raw_type = _fold(result.get("tipo_correto") or result.get("tipo"))
    type_map = {
        "automovel": "automóvel",
        "automotivo": "automóvel",
        "acessorio automotivo": "automóvel",
        "caminhao": "automóvel",
        "utilitario leve": "automóvel",
        "utilitario 4x4": "automóvel",
        "veiculo pesado": "automóvel",
        "motocicleta": "motocicleta",
        "acessorio para motocicleta": "motocicleta",
        "nautica": "náutica",
        "equipamento nautico": "náutica",
        "ferramenta automotiva": "ferramenta automotiva",
        "universal veicular": "universal veicular",
        "acessorio automotivo universal": "universal veicular",
        "acessorio veicular universal": "universal veicular",
        "acessorio veicular e nautico": "universal veicular",
        "automotivo_universal_por_especificacao": "universal veicular",
        "nao veicular": "não veicular",
        "componente de vedacao de uso geral": "não veicular",
        "produto para animais": "não veicular",
    }
    application_type = type_map.get(raw_type)
    if application_type:
        override["tipo"] = application_type

    has_oem_research = "oem_referencias_confirmadas" in result or "oem" in result
    oem = []
    for item in result.get("oem_referencias_confirmadas") or result.get("oem") or []:
        raw_code = _repair_text(item.get("codigo") if isinstance(item, dict) else item).upper()
        for code in re.split(r"\s*(?:/|\||;|,)\s*", raw_code):
            if not _valid_oem_code(code):
                continue
            if code not in oem:
                oem.append(code)
    if has_oem_research:
        override["oem"] = oem
        override["_oem_pesquisa_autoritativo"] = True

    vehicles = []
    for item in result.get("veiculos_compativeis") or result.get("veículos_compatíveis") or []:
        if not isinstance(item, dict):
            continue
        brand = _repair_text(item.get("marca"))
        if application_type in {"náutica", "não veicular", "ferramenta automotiva"}:
            model = _repair_text(item.get("modelo"))
        else:
            model = _clean_vehicle_model(item.get("modelo"))
        years = [_repair_text(value) for value in item.get("anos") or [] if not _is_missing_value(value)]
        restrictions = _repair_text(item.get("restricoes") or item.get("restrições"))
        start = item.get("ano_inicio")
        end = item.get("ano_fim")
        if not years and str(start).isdigit():
            years = [str(start) if not str(end).isdigit() or int(start) == int(end) else f"{int(start)} a {int(end)}"]
        if brand and model:
            vehicle = {"marca": brand, "modelo": model, "anos": years}
            if restrictions:
                vehicle["restrições"] = [restrictions]
            vehicles.append(vehicle)

    equipment = result.get("equipamentos_ou_aplicações_compatíveis") or []
    equipment = [_repair_text(value) for value in equipment if not _is_missing_value(value)]
    if application_type in {"náutica", "não veicular", "ferramenta automotiva"}:
        for vehicle in _merge_vehicle_rows(vehicles, sku=sku, clean_models=False):
            application = f"{vehicle['marca']} {vehicle['modelo']}"
            if vehicle.get("anos"):
                application += f" — {', '.join(vehicle['anos'])}"
            if vehicle.get("restrições"):
                application += f" — {'; '.join(vehicle['restrições'])}"
            equipment.append(application)
        vehicles = []
    if vehicles or "veiculos_compativeis" in result or "veículos_compatíveis" in result:
        override["veículos_compatíveis"] = _merge_vehicle_rows(vehicles, sku=sku)

    technical = [_repair_text(item) for item in result.get("caracteristicas_confirmadas") or []]
    measures = []
    physical_terms = (
        "comprimento", "largura", "altura", "profundidade", "diametro", "diâmetro", "dimens", "espessura",
        "peso", "rosca", "encaixe", "bocal", "furo", "bcd", "haste", "sextavado",
        "distancia", "distância", "tamanho", "folga", "deslocamento", "bitola",
    )
    has_measure_research = "medidas_tecnicas" in result or "medidas" in result
    for item in result.get("medidas_tecnicas") or result.get("medidas") or []:
        if isinstance(item, dict):
            description = _repair_text(item.get("descricao") or item.get("nome"))
            value = _format_research_value(item.get("valor"))
            unit = _repair_text(item.get("unidade"))
            claim = f"{description.capitalize()}: {value}{(' ' + unit) if unit else ''}".strip()
        else:
            claim = _repair_text(item)
            description = claim.split(":", 1)[0]
        if not claim:
            continue
        if any(term in _fold(description) for term in physical_terms):
            measures.append(claim.rstrip(" ."))
        else:
            technical.append(claim)
    if has_measure_research:
        override["medidas_do_produto"] = measures
        override["_medidas_pesquisa_autoritativas"] = True
    if technical:
        override["características_técnicas"] = _dedupe_text(technical, limit=20)

    for field in ("para_que_serve", "modo_de_funcionamento", "instalação"):
        values = result.get(field) or []
        if isinstance(values, str):
            values = [values]
        values = [_repair_text(value) for value in values if not _is_missing_value(value)]
        if values:
            override[field] = values
    if equipment:
        override["equipamentos_ou_aplicações_compatíveis"] = _dedupe_text(equipment, limit=30)
    return override


def _merge_research_reports(overrides: dict[str, Any], paths: Iterable[Path]) -> dict[str, Any]:
    merged = dict(overrides or {})
    for path in paths or []:
        payload = _read_json(path, {})
        for result in payload.get("resultados") or []:
            if not isinstance(result, dict):
                continue
            sku = _repair_text(result.get("sku"))
            if not sku:
                continue
            current = dict(merged.get(sku) or {}) if isinstance(merged.get(sku), dict) else {}
            research = _research_result_to_override(result)
            for key, value in research.items():
                if key == "oem" and research.get("_oem_pesquisa_autoritativo"):
                    current[key] = list(value or [])
                elif key == "medidas_do_produto" and research.get("_medidas_pesquisa_autoritativas"):
                    current[key] = list(value or [])
                elif key in {"oem", "características_técnicas", "medidas_do_produto"}:
                    current[key] = list(dict.fromkeys([*(current.get(key) or []), *(value or [])]))
                else:
                    current[key] = value
            merged[sku] = current
    return merged


def _merge_content_completions(overrides: dict[str, Any], paths: Iterable[Path]) -> dict[str, Any]:
    merged = dict(overrides or {})
    fields = ("para_que_serve", "modo_de_funcionamento", "instalação")
    for path in paths or []:
        payload = _read_json(path, {})
        if not isinstance(payload, dict):
            continue
        for sku, content in payload.items():
            if not isinstance(content, dict):
                continue
            sku = _repair_text(sku)
            if not sku:
                continue
            current = dict(merged.get(sku) or {}) if isinstance(merged.get(sku), dict) else {}
            for field in fields:
                values = content.get(field) or []
                if isinstance(values, str):
                    values = [values]
                values = [_repair_text(value) for value in values if not _is_missing_value(value)]
                if values:
                    current[field] = _dedupe_text(values, limit=8)
            merged[sku] = current
    return merged


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Revisa os dossies de SKU e gera a versao limpa em PT-BR.")
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--compatibility-cache", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overrides", type=Path)
    parser.add_argument("--research-report", type=Path, action="append", default=[])
    parser.add_argument("--content-completion", type=Path, action="append", default=[])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv if argv is not None else sys.argv[1:]))
    repo_root = Path(__file__).resolve().parents[1]
    appdata = os.environ.get("APPDATA", "")
    default_source = Path(appdata) / "JK Sistema Cliente" / "local_app" / "info" / "000002"
    source_dir = (args.source_dir or default_source).resolve()
    raw_dir = args.raw_dir.resolve()
    cache_path = args.compatibility_cache.resolve()
    output_dir = args.output_dir.resolve()
    overrides_path = (args.overrides or repo_root / "scripts" / "sku_review_overrides_ptbr.json").resolve()

    rows = [
        row for row in _read_catalog(source_dir / "cadastro_produtos.csv")
        if _repair_text(row.get("sku")) not in EXCLUDED_DOSSIER_SKUS
    ]
    overrides = _merge_content_completions(
        _merge_research_reports(
            _read_json(overrides_path, {}),
            [path.resolve() for path in args.research_report],
        ),
        [path.resolve() for path in args.content_completion],
    )
    cache = _read_json(cache_path, {})
    cache_by_sku: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in (cache.get("anuncios") or {}).values():
        if isinstance(item, dict) and item.get("sku"):
            cache_by_sku[_repair_text(item.get("sku"))].append(item)

    output_dir.mkdir(parents=True, exist_ok=True)
    for excluded_sku in EXCLUDED_DOSSIER_SKUS:
        excluded_path = output_dir / _safe_filename(excluded_sku)
        if excluded_path.exists():
            excluded_path.unlink()
    stats = {
        "total": 0,
        "nomes_em_português": 0,
        "com_medidas_do_produto": 0,
        "com_veículos_e_anos": 0,
        "sem_campos_de_fonte": 0,
        "com_pendências": 0,
    }
    pending_skus = []
    for index, row in enumerate(rows, start=1):
        sku = _repair_text(row.get("sku"))
        raw = _read_json(raw_dir / _safe_filename(sku), {})
        dossier = _build_clean_dossier(row, raw, overrides, cache_by_sku.get(sku, []))
        _write_json_atomic(output_dir / _safe_filename(sku), dossier)
        stats["total"] += 1
        stats["nomes_em_português"] += dossier["revisão"]["nome_em_português_do_brasil"]
        stats["com_medidas_do_produto"] += dossier["medidas_do_produto"]["status"] == "documentado"
        vehicle_items = dossier["aplicação"]["veículos_compatíveis"]["itens"]
        stats["com_veículos_e_anos"] += bool(vehicle_items) and all(item.get("anos") for item in vehicle_items)
        stats["sem_campos_de_fonte"] += not _contains_source_fields(dossier)
        if dossier["revisão"]["pendências"]:
            stats["com_pendências"] += 1
            pending_skus.append(sku)
        if index % 50 == 0 or index == len(rows):
            print(f"[REVISÃO] processados: {index}/{len(rows)}", flush=True)

    index_payload = {
        "schema_version": SCHEMA_VERSION,
        "total_skus": len(rows),
        "arquivos_revisados": stats["total"],
        "critérios": {
            "nomes": "Português do Brasil; nomes técnicos, marcas e siglas preservados.",
            "medidas": "Somente medidas técnicas confirmadas do produto.",
            "compatibilidade": "Modelo e anos exibidos separadamente; conflitos não são combinados.",
            "códigos": "Nos SKUs 214, 225 e 372, referências oriundas somente do Bling foram ignoradas; os demais códigos publicados passaram pela revisão de cada dossiê.",
            "conteúdo_excluído": "Endereços da internet, textos promocionais e campos internos.",
        },
        "cobertura": stats,
        "skus_com_pendências": pending_skus,
        "skus_excluídos": sorted(EXCLUDED_DOSSIER_SKUS),
        "atualizado_em": datetime.now(timezone.utc).isoformat(),
    }
    if _contains_source_fields(index_payload):
        raise ValueError("Indice final contem campo proibido")
    _write_json_atomic(output_dir / "_INDICE.json", index_payload)
    print(f"[REVISÃO] concluído: {stats['total']} SKUs em {output_dir}", flush=True)
    print(json.dumps(stats, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
