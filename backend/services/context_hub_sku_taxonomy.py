"""Taxonomia versionada de categorias de produto usada pelo Context Hub.

Os dossies ``info/<client_id>/SKU/*.json`` continuam sendo a fonte canonica do
produto. Este modulo apenas define a arvore editorial aprovada e regras
deterministicas para montar indices derivados no Obsidian.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


PRODUCT_CATEGORY_TAXONOMY_VERSION = "1.0.0"
PRODUCT_CATEGORY_TAXONOMY_SOURCE_REF = (
    "backend/services/context_hub_sku_taxonomy.py"
)


_ACCENTED_WORDS = {
    "acessorios": "acessórios",
    "aco": "aço",
    "admissao": "admissão",
    "alimentacao": "alimentação",
    "animais": "animais",
    "aplicacao": "aplicação",
    "arrefecimento": "arrefecimento",
    "audio": "áudio",
    "automatico": "automático",
    "automoveis": "automóveis",
    "bracos": "braços",
    "botoes": "botões",
    "cacamba": "caçamba",
    "cambio": "câmbio",
    "capo": "capô",
    "carcacas": "carcaças",
    "climatizacao": "climatização",
    "combustivel": "combustível",
    "decoracao": "decoração",
    "deteccao": "detecção",
    "diagnostico": "diagnóstico",
    "direcao": "direção",
    "distribuicao": "distribuição",
    "dobradicas": "dobradiças",
    "eletrica": "elétrica",
    "eletricas": "elétricas",
    "eletrico": "elétrico",
    "eletricos": "elétricos",
    "eletronica": "eletrônica",
    "eletronico": "eletrônico",
    "eletronicos": "eletrônicos",
    "elevacao": "elevação",
    "emissoes": "emissões",
    "estimacao": "estimação",
    "fusiveis": "fusíveis",
    "ignicao": "ignição",
    "iluminacao": "iluminação",
    "industria": "indústria",
    "injecao": "injeção",
    "instalacao": "instalação",
    "instrumentacao": "instrumentação",
    "intimos": "íntimos",
    "irrigacao": "irrigação",
    "laminas": "lâminas",
    "lampadas": "lâmpadas",
    "lubrificacao": "lubrificação",
    "macanetas": "maçanetas",
    "medicao": "medição",
    "modulos": "módulos",
    "moveis": "móveis",
    "movimentacao": "movimentação",
    "multimidia": "multimídia",
    "nautica": "náutica",
    "nebulizacao": "nebulização",
    "nivel": "nível",
    "niveis": "níveis",
    "oleo": "óleo",
    "operacao": "operação",
    "paineis": "painéis",
    "pneumatica": "pneumática",
    "pos": "pós",
    "pressao": "pressão",
    "protecao": "proteção",
    "pulverizacao": "pulverização",
    "reservatorios": "reservatórios",
    "resistencias": "resistências",
    "revisao": "revisão",
    "rocadeiras": "roçadeiras",
    "seguranca": "segurança",
    "sinalizacao": "sinalização",
    "sobrealimentacao": "sobrealimentação",
    "subaquatica": "subaquática",
    "suspensao": "suspensão",
    "tensao": "tensão",
    "termicas": "térmicas",
    "termicos": "térmicos",
    "termostaticas": "termostáticas",
    "tracao": "tração",
    "transferencia": "transferência",
    "transmissao": "transmissão",
    "utilitarios": "utilitários",
    "ultrassonicos": "ultrassônicos",
    "vacuo": "vácuo",
    "valvulas": "válvulas",
    "vedacoes": "vedações",
    "xenonio": "xenônio",
    "escritorio": "escritório",
}


def _accent_label(label: str) -> str:
    def replace(match: re.Match[str]) -> str:
        original = match.group(0)
        replacement = _ACCENTED_WORDS.get(original.casefold(), original)
        if original[:1].isupper():
            return replacement[:1].upper() + replacement[1:]
        return replacement

    return re.sub(r"[A-Za-z]+", replace, label)


def _path(*labels: str) -> tuple[str, ...]:
    return tuple(_accent_label(label) for label in labels)


VEHICLES = "Veiculares"
AUTOMOTIVE = "Automoveis, utilitarios e pesados"
MOTORCYCLES = "Motocicletas"
NAUTICAL = "Nautica"
UNIVERSAL_VEHICLE = "Universais veiculares"
AUTOMOTIVE_TOOLS = "Ferramentas automotivas"


CATEGORY_PATHS: dict[str, tuple[str, ...]] = {
    # Automoveis, utilitarios e pesados - motor.
    "auto_intake_manifolds": _path(VEHICLES, AUTOMOTIVE, "Motor e gerenciamento", "Admissao e sobrealimentacao", "Coletores de admissao"),
    "auto_air_hoses": _path(VEHICLES, AUTOMOTIVE, "Motor e gerenciamento", "Admissao e sobrealimentacao", "Mangueiras de ar e TBI"),
    "auto_intercooler_hoses": _path(VEHICLES, AUTOMOTIVE, "Motor e gerenciamento", "Admissao e sobrealimentacao", "Mangueiras de intercooler e turbo"),
    "auto_maf_map": _path(VEHICLES, AUTOMOTIVE, "Motor e gerenciamento", "Admissao e sobrealimentacao", "Medidores MAF e MAP"),
    "auto_throttle": _path(VEHICLES, AUTOMOTIVE, "Motor e gerenciamento", "Admissao e sobrealimentacao", "Sensores e atuadores da borboleta"),
    "auto_fuel_pumps": _path(VEHICLES, AUTOMOTIVE, "Motor e gerenciamento", "Combustivel e injecao", "Bombas e refis"),
    "auto_fuel_modules": _path(VEHICLES, AUTOMOTIVE, "Motor e gerenciamento", "Combustivel e injecao", "Modulos, flanges e tampas"),
    "auto_fuel_level": _path(VEHICLES, AUTOMOTIVE, "Motor e gerenciamento", "Combustivel e injecao", "Boias e sensores de nivel"),
    "auto_fuel_pressure_sensors": _path(VEHICLES, AUTOMOTIVE, "Motor e gerenciamento", "Combustivel e injecao", "Sensores de pressao"),
    "auto_fuel_valves": _path(VEHICLES, AUTOMOTIVE, "Motor e gerenciamento", "Combustivel e injecao", "Valvulas dosadoras e reguladoras"),
    "auto_fuel_filters": _path(VEHICLES, AUTOMOTIVE, "Motor e gerenciamento", "Combustivel e injecao", "Filtros e telas"),
    "auto_fuel_returns": _path(VEHICLES, AUTOMOTIVE, "Motor e gerenciamento", "Combustivel e injecao", "Mangueiras e retornos"),
    "auto_spark_plugs": _path(VEHICLES, AUTOMOTIVE, "Motor e gerenciamento", "Ignicao e partida", "Velas de ignicao"),
    "auto_ignition_coils": _path(VEHICLES, AUTOMOTIVE, "Motor e gerenciamento", "Ignicao e partida", "Bobinas"),
    "auto_ignition_switches": _path(VEHICLES, AUTOMOTIVE, "Motor e gerenciamento", "Ignicao e partida", "Comutadores de ignicao"),
    "auto_starter_motors": _path(VEHICLES, AUTOMOTIVE, "Motor e gerenciamento", "Ignicao e partida", "Motores de partida"),
    "auto_oil_filters": _path(VEHICLES, AUTOMOTIVE, "Motor e gerenciamento", "Lubrificacao e componentes internos", "Filtros e telas de oleo"),
    "auto_oil_tubes": _path(VEHICLES, AUTOMOTIVE, "Motor e gerenciamento", "Lubrificacao e componentes internos", "Tubos de oleo"),
    "auto_seals": _path(VEHICLES, AUTOMOTIVE, "Motor e gerenciamento", "Lubrificacao e componentes internos", "Retentores e vedacoes"),
    "auto_oil_sensors": _path(VEHICLES, AUTOMOTIVE, "Motor e gerenciamento", "Lubrificacao e componentes internos", "Sensores de oleo"),
    "auto_vvt": _path(VEHICLES, AUTOMOTIVE, "Motor e gerenciamento", "Lubrificacao e componentes internos", "Valvulas VVT, VTEC e comando"),
    "auto_evap": _path(VEHICLES, AUTOMOTIVE, "Motor e gerenciamento", "Emissoes e vacuo", "EVAP e canister"),
    "auto_pcv": _path(VEHICLES, AUTOMOTIVE, "Motor e gerenciamento", "Emissoes e vacuo", "PCV e diafragmas"),
    "auto_vacuum_pumps": _path(VEHICLES, AUTOMOTIVE, "Motor e gerenciamento", "Emissoes e vacuo", "Bombas de vacuo"),
    "auto_vacuum_valves": _path(VEHICLES, AUTOMOTIVE, "Motor e gerenciamento", "Emissoes e vacuo", "Valvulas e atuadores de vacuo"),
    # Arrefecimento e climatizacao.
    "auto_radiators": _path(VEHICLES, AUTOMOTIVE, "Arrefecimento e climatizacao", "Radiadores"),
    "auto_thermostats": _path(VEHICLES, AUTOMOTIVE, "Arrefecimento e climatizacao", "Valvulas termostaticas"),
    "auto_thermostat_housings": _path(VEHICLES, AUTOMOTIVE, "Arrefecimento e climatizacao", "Carcacas termostaticas"),
    "auto_cooling_hoses": _path(VEHICLES, AUTOMOTIVE, "Arrefecimento e climatizacao", "Mangueiras, tubos e flanges"),
    "auto_radiator_connectors": _path(VEHICLES, AUTOMOTIVE, "Arrefecimento e climatizacao", "Conectores do radiador"),
    "auto_cooling_reservoirs": _path(VEHICLES, AUTOMOTIVE, "Arrefecimento e climatizacao", "Reservatorios e tampas"),
    "auto_thermal_switches": _path(VEHICLES, AUTOMOTIVE, "Arrefecimento e climatizacao", "Interruptores termicos"),
    "auto_fan_controls": _path(VEHICLES, AUTOMOTIVE, "Arrefecimento e climatizacao", "Modulos e resistencias da ventoinha"),
    "auto_climate_sensors": _path(VEHICLES, AUTOMOTIVE, "Arrefecimento e climatizacao", "Sensores de climatizacao"),
    "auto_air_vents": _path(VEHICLES, AUTOMOTIVE, "Arrefecimento e climatizacao", "Difusores de ar"),
    "auto_cabin_filter_accessories": _path(VEHICLES, AUTOMOTIVE, "Arrefecimento e climatizacao", "Tampas e acessorios do filtro de cabine"),
    # Eletrica e eletronica.
    "auto_battery_sensors": _path(VEHICLES, AUTOMOTIVE, "Eletrica e eletronica", "Bateria e alimentacao", "Sensores de bateria"),
    "auto_battery_terminals": _path(VEHICLES, AUTOMOTIVE, "Eletrica e eletronica", "Bateria e alimentacao", "Terminais e conectores"),
    "auto_voltage_converters": _path(VEHICLES, AUTOMOTIVE, "Eletrica e eletronica", "Bateria e alimentacao", "Conversores de tensao"),
    "auto_fuse_boxes": _path(VEHICLES, AUTOMOTIVE, "Eletrica e eletronica", "Fusiveis e distribuicao", "Caixas de fusiveis"),
    "auto_bsm_modules": _path(VEHICLES, AUTOMOTIVE, "Eletrica e eletronica", "Fusiveis e distribuicao", "Modulos BSM"),
    "auto_power_modules": _path(VEHICLES, AUTOMOTIVE, "Eletrica e eletronica", "Fusiveis e distribuicao", "Placas e modulos de protecao"),
    "auto_window_controls": _path(VEHICLES, AUTOMOTIVE, "Eletrica e eletronica", "Comandos e interruptores", "Vidros eletricos"),
    "auto_light_switches": _path(VEHICLES, AUTOMOTIVE, "Eletrica e eletronica", "Comandos e interruptores", "Iluminacao"),
    "auto_steering_controls": _path(VEHICLES, AUTOMOTIVE, "Eletrica e eletronica", "Comandos e interruptores", "Volante"),
    "auto_start_buttons": _path(VEHICLES, AUTOMOTIVE, "Eletrica e eletronica", "Comandos e interruptores", "Partida"),
    "auto_parking_brake_switches": _path(VEHICLES, AUTOMOTIVE, "Eletrica e eletronica", "Comandos e interruptores", "Freio de estacionamento"),
    "auto_clusters": _path(VEHICLES, AUTOMOTIVE, "Eletrica e eletronica", "Instrumentacao e telas", "Paineis de instrumentos"),
    "auto_lcd_displays": _path(VEHICLES, AUTOMOTIVE, "Eletrica e eletronica", "Instrumentacao e telas", "Telas LCD"),
    "auto_multimedia_screens": _path(VEHICLES, AUTOMOTIVE, "Eletrica e eletronica", "Instrumentacao e telas", "Telas de centrais multimidia"),
    "auto_audio_cables": _path(VEHICLES, AUTOMOTIVE, "Eletrica e eletronica", "Audio, conectividade e conforto", "Cabos auxiliares"),
    "auto_bluetooth": _path(VEHICLES, AUTOMOTIVE, "Eletrica e eletronica", "Audio, conectividade e conforto", "Transmissores Bluetooth e FM"),
    "auto_cruise_control": _path(VEHICLES, AUTOMOTIVE, "Eletrica e eletronica", "Audio, conectividade e conforto", "Piloto automatico"),
    # Transmissao, chassis e freios.
    "auto_shift_controls": _path(VEHICLES, AUTOMOTIVE, "Transmissao, embreagem e tracao", "Alavancas, manoplas e botoes"),
    "auto_shift_tracks": _path(VEHICLES, AUTOMOTIVE, "Transmissao, embreagem e tracao", "Esteiras e guarda-pos do seletor"),
    "auto_transmission_sensors": _path(VEHICLES, AUTOMOTIVE, "Transmissao, embreagem e tracao", "Sensores de transmissao"),
    "auto_clutch_actuators": _path(VEHICLES, AUTOMOTIVE, "Transmissao, embreagem e tracao", "Atuadores de embreagem"),
    "auto_transfer_case": _path(VEHICLES, AUTOMOTIVE, "Transmissao, embreagem e tracao", "Caixa de transferencia"),
    "auto_4x4_actuators": _path(VEHICLES, AUTOMOTIVE, "Transmissao, embreagem e tracao", "Atuadores 4x4"),
    "auto_freewheel_hubs": _path(VEHICLES, AUTOMOTIVE, "Transmissao, embreagem e tracao", "Cubos e rodas livres"),
    "auto_driveshaft": _path(VEHICLES, AUTOMOTIVE, "Transmissao, embreagem e tracao", "Cardan, coxins e rolamentos"),
    "auto_control_arms": _path(VEHICLES, AUTOMOTIVE, "Suspensao, direcao, rodas e pneus", "Bandejas e bracos"),
    "auto_steering_pumps": _path(VEHICLES, AUTOMOTIVE, "Suspensao, direcao, rodas e pneus", "Bombas de direcao"),
    "auto_steering_reservoirs": _path(VEHICLES, AUTOMOTIVE, "Suspensao, direcao, rodas e pneus", "Reservatorios de direcao"),
    "auto_steering_sensors": _path(VEHICLES, AUTOMOTIVE, "Suspensao, direcao, rodas e pneus", "Sensores de pressao da direcao"),
    "auto_steering_couplings": _path(VEHICLES, AUTOMOTIVE, "Suspensao, direcao, rodas e pneus", "Acoplamentos e buchas"),
    "auto_wheel_trim": _path(VEHICLES, AUTOMOTIVE, "Suspensao, direcao, rodas e pneus", "Calotas e acabamentos de roda"),
    "auto_tpms": _path(VEHICLES, AUTOMOTIVE, "Suspensao, direcao, rodas e pneus", "Sensores TPMS"),
    "auto_parking_brake": _path(VEHICLES, AUTOMOTIVE, "Freios", "Freio de estacionamento"),
    "auto_brake_controls": _path(VEHICLES, AUTOMOTIVE, "Freios", "Botoes e alavancas"),
    "auto_brake_trim": _path(VEHICLES, AUTOMOTIVE, "Freios", "Capas e acabamentos"),
    "auto_brake_vacuum": _path(VEHICLES, AUTOMOTIVE, "Freios", "Servo-freio e vacuo"),
    # Carroceria, interior e iluminacao.
    "auto_grilles": _path(VEHICLES, AUTOMOTIVE, "Carroceria e acabamento externo", "Grades dianteiras"),
    "auto_bumpers": _path(VEHICLES, AUTOMOTIVE, "Carroceria e acabamento externo", "Para-choques e molduras"),
    "auto_guides_supports": _path(VEHICLES, AUTOMOTIVE, "Carroceria e acabamento externo", "Guias e suportes"),
    "auto_trim_appliques": _path(VEHICLES, AUTOMOTIVE, "Carroceria e acabamento externo", "Frisos e apliques"),
    "auto_emblems": _path(VEHICLES, AUTOMOTIVE, "Carroceria e acabamento externo", "Emblemas"),
    "auto_door_handles": _path(VEHICLES, AUTOMOTIVE, "Carroceria e acabamento externo", "Macanetas"),
    "auto_door_pulls": _path(VEHICLES, AUTOMOTIVE, "Carroceria e acabamento externo", "Portas e puxadores"),
    "auto_hood_hinges": _path(VEHICLES, AUTOMOTIVE, "Carroceria e acabamento externo", "Capo e dobradicas"),
    "auto_trunk": _path(VEHICLES, AUTOMOTIVE, "Carroceria e acabamento externo", "Porta-malas e fechaduras"),
    "auto_tailgate": _path(VEHICLES, AUTOMOTIVE, "Carroceria e acabamento externo", "Tampa de cacamba e amortecedores"),
    "auto_fuel_door": _path(VEHICLES, AUTOMOTIVE, "Carroceria e acabamento externo", "Portinhola de combustivel"),
    "auto_washers": _path(VEHICLES, AUTOMOTIVE, "Carroceria e acabamento externo", "Lavadores e esguichos"),
    "auto_exhaust_tips": _path(VEHICLES, AUTOMOTIVE, "Carroceria e acabamento externo", "Ponteiras de escapamento"),
    "auto_fasteners": _path(VEHICLES, AUTOMOTIVE, "Carroceria e acabamento externo", "Presilhas e fixadores"),
    "auto_consoles": _path(VEHICLES, AUTOMOTIVE, "Interior e acabamento", "Consoles"),
    "auto_interior_pulls": _path(VEHICLES, AUTOMOTIVE, "Interior e acabamento", "Puxadores internos"),
    "auto_door_trim": _path(VEHICLES, AUTOMOTIVE, "Interior e acabamento", "Acabamentos de porta"),
    "auto_shift_trim": _path(VEHICLES, AUTOMOTIVE, "Interior e acabamento", "Acabamentos do cambio"),
    "auto_dashboard_trim": _path(VEHICLES, AUTOMOTIVE, "Interior e acabamento", "Acabamentos do painel"),
    "auto_xenon_lamps": _path(VEHICLES, AUTOMOTIVE, "Iluminacao e sinalizacao", "Lampadas de xenonio"),
    "auto_ballasts": _path(VEHICLES, AUTOMOTIVE, "Iluminacao e sinalizacao", "Reatores e modulos"),
    "auto_turn_signals": _path(VEHICLES, AUTOMOTIVE, "Iluminacao e sinalizacao", "Piscas e repetidores"),
    "auto_reflectors": _path(VEHICLES, AUTOMOTIVE, "Iluminacao e sinalizacao", "Refletores"),
    "auto_brake_lights": _path(VEHICLES, AUTOMOTIVE, "Iluminacao e sinalizacao", "Luzes de freio"),
    "auto_aux_lights": _path(VEHICLES, AUTOMOTIVE, "Iluminacao e sinalizacao", "Luzes auxiliares e DRL"),
    "auto_headlight_guides": _path(VEHICLES, AUTOMOTIVE, "Iluminacao e sinalizacao", "Guias de farol"),
    "auto_headlight_protection": _path(VEHICLES, AUTOMOTIVE, "Iluminacao e sinalizacao", "Protetores e tampas"),
    # Motocicletas, nautica, universais e ferramentas automotivas.
    "moto_engine": _path(VEHICLES, MOTORCYCLES, "Motor, ignicao e partida"),
    "moto_fuel": _path(VEHICLES, MOTORCYCLES, "Combustivel e alimentacao"),
    "moto_cooling": _path(VEHICLES, MOTORCYCLES, "Arrefecimento"),
    "moto_electrical": _path(VEHICLES, MOTORCYCLES, "Eletrica e estatores"),
    "moto_instruments": _path(VEHICLES, MOTORCYCLES, "Instrumentacao e paineis"),
    "moto_lighting": _path(VEHICLES, MOTORCYCLES, "Iluminacao e sinalizacao"),
    "moto_tank": _path(VEHICLES, MOTORCYCLES, "Tanque, tampas e torneiras"),
    "moto_wheels": _path(VEHICLES, MOTORCYCLES, "Rodas, pneus e valvulas"),
    "moto_mounts": _path(VEHICLES, MOTORCYCLES, "Suportes e carregadores"),
    "moto_protection": _path(VEHICLES, MOTORCYCLES, "Protecao e acessorios"),
    "nautical_engine": _path(VEHICLES, NAUTICAL, "Motor e ignicao"),
    "nautical_fuel": _path(VEHICLES, NAUTICAL, "Combustivel"),
    "nautical_panels": _path(VEHICLES, NAUTICAL, "Paineis e comandos eletricos"),
    "nautical_lighting": _path(VEHICLES, NAUTICAL, "Iluminacao subaquatica"),
    "nautical_accessories": _path(VEHICLES, NAUTICAL, "Acessorios eletricos"),
    "universal_lighting": _path(VEHICLES, UNIVERSAL_VEHICLE, "Iluminacao universal"),
    "universal_instruments": _path(VEHICLES, UNIVERSAL_VEHICLE, "Instrumentacao universal"),
    "universal_battery": _path(VEHICLES, UNIVERSAL_VEHICLE, "Bateria e conectores"),
    "universal_connectivity": _path(VEHICLES, UNIVERSAL_VEHICLE, "Audio e conectividade"),
    "universal_wheels": _path(VEHICLES, UNIVERSAL_VEHICLE, "Rodas, pneus e valvulas"),
    "universal_safety": _path(VEHICLES, UNIVERSAL_VEHICLE, "Cinto e seguranca"),
    "universal_fasteners": _path(VEHICLES, UNIVERSAL_VEHICLE, "Presilhas e fixadores"),
    "tool_diagnostics": _path(VEHICLES, AUTOMOTIVE_TOOLS, "Diagnostico eletronico"),
    "tool_tpms": _path(VEHICLES, AUTOMOTIVE_TOOLS, "Ferramentas TPMS"),
    "tool_ignition": _path(VEHICLES, AUTOMOTIVE_TOOLS, "Testadores de ignicao"),
    "tool_installation": _path(VEHICLES, AUTOMOTIVE_TOOLS, "Ferramentas de instalacao"),
    # Bicicletas.
    "bike_round_chainrings": _path("Bicicletas", "Transmissao", "Coroas redondas"),
    "bike_oval_chainrings": _path("Bicicletas", "Transmissao", "Coroas ovais"),
    "bike_chain_guides": _path("Bicicletas", "Transmissao", "Guias de corrente"),
    "bike_saddles": _path("Bicicletas", "Selins"),
    "bike_valves": _path("Bicicletas", "Rodas, pneus e valvulas"),
    # Jardim, agricultura e rocadeiras.
    "garden_tillers": _path("Jardim, agricultura e rocadeiras", "Enxadas rotativas"),
    "garden_discs": _path("Jardim, agricultura e rocadeiras", "Discos de capina"),
    "garden_adapters": _path("Jardim, agricultura e rocadeiras", "Adaptadores e luvas"),
    "garden_brushes": _path("Jardim, agricultura e rocadeiras", "Escovas de aco"),
    "garden_blades": _path("Jardim, agricultura e rocadeiras", "Laminas"),
    "garden_augers": _path("Jardim, agricultura e rocadeiras", "Brocas de solo"),
    "garden_irrigation": _path("Jardim, agricultura e rocadeiras", "Irrigacao e nebulizacao"),
    "garden_protection": _path("Jardim, agricultura e rocadeiras", "Protecao para operacao"),
    # Ferramentas, oficina e industria.
    "industry_hoists": _path("Ferramentas, oficina e industria", "Elevacao e movimentacao", "Talhas"),
    "industry_remotes": _path("Ferramentas, oficina e industria", "Elevacao e movimentacao", "Controles remotos"),
    "industry_suction": _path("Ferramentas, oficina e industria", "Elevacao e movimentacao", "Ventosas"),
    "industry_air_filters": _path("Ferramentas, oficina e industria", "Pneumatica e ar comprimido", "Filtros reguladores"),
    "industry_pressure_valves": _path("Ferramentas, oficina e industria", "Pneumatica e ar comprimido", "Valvulas de pressao"),
    "industry_pumps": _path("Ferramentas, oficina e industria", "Bombas e transferencia de fluidos"),
    "industry_motors": _path("Ferramentas, oficina e industria", "Motores e motoredutores"),
    "industry_detectors": _path("Ferramentas, oficina e industria", "Medicao e deteccao", "Detectores de parede"),
    "industry_squares": _path("Ferramentas, oficina e industria", "Medicao e deteccao", "Esquadros e niveis"),
    "industry_cleaning": _path("Ferramentas, oficina e industria", "Limpeza e pulverizacao"),
    "industry_face_protection": _path("Ferramentas, oficina e industria", "Seguranca e EPI", "Protetores faciais"),
    "industry_fire_blankets": _path("Ferramentas, oficina e industria", "Seguranca e EPI", "Mantas antichamas"),
    "industry_safes": _path("Ferramentas, oficina e industria", "Armazenamento e seguranca", "Cofres e porta-chaves"),
    # Eletronicos, casa e demais dominios nao veiculares.
    "electronics_usb": _path("Eletronicos e tecnologia", "Computadores e conectividade", "Hubs e adaptadores USB"),
    "electronics_stylus": _path("Eletronicos e tecnologia", "Celulares e tablets", "Canetas capacitivas"),
    "electronics_wireless": _path("Eletronicos e tecnologia", "Celulares e tablets", "Carregamento sem fio"),
    "electronics_lcd": _path("Eletronicos e tecnologia", "Telas e displays", "Displays LCD"),
    "electronics_locator": _path("Eletronicos e tecnologia", "Telas e displays", "Telas para localizadores"),
    "electronics_smart_sensors": _path("Eletronicos e tecnologia", "Casa inteligente", "Sensores Wi-Fi"),
    "home_chairs": _path("Casa, moveis e decoracao", "Cadeiras de escritorio"),
    "home_panels": _path("Casa, moveis e decoracao", "Paineis ripados"),
    "home_security": _path("Casa, moveis e decoracao", "Seguranca residencial"),
    "pet_training": _path("Animais de estimacao", "Adestramento"),
    "pet_repellents": _path("Animais de estimacao", "Repelentes ultrassonicos"),
    "children_sensory": _path("Infantil", "Tapetes e brinquedos sensoriais"),
    "adult_wellness": _path("Bem-estar adulto", "Produtos intimos"),
}


REVIEW_PATH = _path("Pendentes de revisao")
PRODUCT_CATEGORY_PATHS = tuple(CATEGORY_PATHS.values())


@dataclass(frozen=True)
class TaxonomyRule:
    rule_id: str
    category_key: str
    application_types: tuple[str, ...]
    terms: tuple[str, ...]
    excluded_terms: tuple[str, ...] = ()


def _rule(
    rule_id: str,
    category_key: str,
    application_types: Sequence[str],
    *terms: str,
    excluded_terms: Sequence[str] = (),
) -> TaxonomyRule:
    return TaxonomyRule(
        rule_id=rule_id,
        category_key=category_key,
        application_types=tuple(application_types),
        terms=tuple(terms),
        excluded_terms=tuple(excluded_terms),
    )


AUTO = ("automovel",)
MOTO = ("motocicleta",)
BOAT = ("nautica",)
UNIVERSAL = ("universal veicular",)
AUTO_TOOL = ("ferramenta automotiva",)
NON_VEHICLE = ("nao veicular",)


# Regras ordenadas da identificacao mais especifica para a mais generica.
TAXONOMY_RULES: tuple[TaxonomyRule, ...] = (
    # Motocicletas, nautica, universais e ferramentas automotivas.
    _rule("moto-instruments", "moto_instruments", MOTO, "painel de instrumentos", "marcador", "medidor"),
    _rule("moto-lighting", "moto_lighting", MOTO, "barra flexivel de led", "pisca", "lanterna", "luz"),
    _rule("moto-cooling", "moto_cooling", MOTO, "radiador", "interruptor termico", "cebolinha", "ventoinha"),
    _rule("moto-tank", "moto_tank", MOTO, "tampa combustivel", "tampa de combustivel", "torneira combustivel", "torneira de combustivel", "torneira gasolina"),
    _rule("moto-fuel", "moto_fuel", MOTO, "bomba de combustivel", "bomba eletrica", "carburador"),
    _rule("moto-electrical", "moto_electrical", MOTO, "estator"),
    _rule("moto-mounts", "moto_mounts", MOTO, "suporte de celular", "carregamento sem fio"),
    _rule("moto-protection", "moto_protection", MOTO, "protetor", "capa"),
    _rule("moto-wheels", "moto_wheels", MOTO, "valvula", "bico pneu", "pneu"),
    _rule("moto-engine", "moto_engine", MOTO, "motor de partida", "vela", "bobina", "testador de faisca"),
    _rule("nautical-lighting", "nautical_lighting", (*BOAT, *NON_VEHICLE), "luz subaquatica", "luzes subaquaticas", "luz submarina"),
    _rule("nautical-panels", "nautical_panels", (*BOAT, *NON_VEHICLE), "painel eletrico", "painel de comando"),
    _rule("nautical-fuel", "nautical_fuel", BOAT, "bomba mecanica", "bomba de combustivel", "combustivel"),
    _rule("nautical-engine", "nautical_engine", BOAT, "vela", "bobina", "ignicao", "motor de popa"),
    _rule("universal-safety", "universal_safety", UNIVERSAL, "cinto", "anti alarme", "lingueta"),
    _rule("universal-wheels", "universal_wheels", UNIVERSAL, "valvula", "bico pneu", "extensor", "pneu"),
    _rule("universal-lighting", "universal_lighting", UNIVERSAL, "lampada", "luz", "led", "pisca", "fita"),
    _rule("universal-instruments", "universal_instruments", UNIVERSAL, "marcador", "medidor", "painel eletrico", "voltimetro"),
    _rule("universal-battery", "universal_battery", UNIVERSAL, "bateria", "terminal", "conector"),
    _rule("universal-connectivity", "universal_connectivity", UNIVERSAL, "bluetooth", "fm", "usb", "carregamento", "receptor"),
    _rule("universal-fasteners", "universal_fasteners", UNIVERSAL, "presilha", "fixador"),
    _rule("mixed-tire-valves", "universal_wheels", NON_VEHICLE, "bico pneu", "valvula extensor", "extensores angulares"),
    _rule("tool-tpms", "tool_tpms", AUTO_TOOL, "tpms", "reaprendizado"),
    _rule("tool-diagnostics", "tool_diagnostics", AUTO_TOOL, "diagnostico", "forscan", "interface"),
    _rule("tool-ignition", "tool_ignition", (*AUTO_TOOL, *AUTO), "testador de faisca"),
    # Nao veiculares.
    _rule("bike-oval-chainrings", "bike_oval_chainrings", NON_VEHICLE, "coroa unica oval", "coroa oval"),
    _rule("bike-round-chainrings", "bike_round_chainrings", NON_VEHICLE, "coroa unica", "coroa para bicicleta"),
    _rule("bike-chain-guides", "bike_chain_guides", NON_VEHICLE, "guia de corrente"),
    _rule("bike-saddles", "bike_saddles", NON_VEHICLE, "selim"),
    _rule("garden-tillers", "garden_tillers", NON_VEHICLE, "enxada rotativa", "enxada aradora"),
    _rule("garden-discs", "garden_discs", NON_VEHICLE, "disco enxada", "disco de capina"),
    _rule("garden-adapters", "garden_adapters", NON_VEHICLE, "adaptador luva", "luva cardan"),
    _rule("garden-brushes", "garden_brushes", NON_VEHICLE, "escova de aco"),
    _rule("garden-blades", "garden_blades", NON_VEHICLE, "lamina", "canivete"),
    _rule("garden-augers", "garden_augers", NON_VEHICLE, "broca", "solo terra"),
    _rule("garden-irrigation", "garden_irrigation", NON_VEHICLE, "aspersor", "nebulizador", "irrigacao"),
    _rule(
        "garden-protection",
        "garden_protection",
        NON_VEHICLE,
        "protetor facial transparente antiembacante",
        "protecao para operacao de rocadeira",
    ),
    _rule("industry-hoists", "industry_hoists", NON_VEHICLE, "talha manual", "talha de"),
    _rule("industry-remotes", "industry_remotes", NON_VEHICLE, "controle remoto", "ponte rolante", "guincho"),
    _rule("industry-suction", "industry_suction", NON_VEHICLE, "ventosa"),
    _rule("industry-air-filters", "industry_air_filters", NON_VEHICLE, "filtro eliminador", "compressor"),
    _rule("industry-pressure-valves", "industry_pressure_valves", NON_VEHICLE, "valvula reguladora de pressao"),
    _rule("industry-pumps", "industry_pumps", (*NON_VEHICLE, *AUTO), "bomba submersa", "minibomba", "transferencia oleo"),
    _rule("industry-motors", "industry_motors", NON_VEHICLE, "motoredutor"),
    _rule("industry-detectors", "industry_detectors", NON_VEHICLE, "detector eletronico", "detector de parede"),
    _rule("industry-squares", "industry_squares", NON_VEHICLE, "esquadro", "nivel laser"),
    _rule("industry-cleaning", "industry_cleaning", NON_VEHICLE, "pulverizador de espuma", "lavagem"),
    _rule("industry-face-protection", "industry_face_protection", NON_VEHICLE, "protetor facial"),
    _rule("industry-fire-blankets", "industry_fire_blankets", NON_VEHICLE, "manta antichamas"),
    _rule("industry-safes", "industry_safes", NON_VEHICLE, "cofre"),
    _rule("electronics-usb", "electronics_usb", NON_VEHICLE, "adaptador multiportas", "hub usb", "usb-c"),
    _rule("electronics-stylus", "electronics_stylus", NON_VEHICLE, "caneta capacitiva"),
    _rule("electronics-wireless", "electronics_wireless", NON_VEHICLE, "carregamento sem fio", "receptor universal"),
    _rule("electronics-locator", "electronics_locator", NON_VEHICLE, "localizador de satelite", "satlink"),
    _rule(
        "electronics-lcd",
        "electronics_lcd",
        NON_VEHICLE,
        "tela lcd",
        "tela de cristal liquido",
        excluded_terms=("localizador",),
    ),
    _rule("electronics-smart-sensors", "electronics_smart_sensors", NON_VEHICLE, "sensor wi-fi", "alexa", "google home"),
    _rule("home-chairs", "home_chairs", NON_VEHICLE, "cadeira de escritorio"),
    _rule("home-panels", "home_panels", NON_VEHICLE, "painel ripado"),
    _rule("home-security", "home_security", NON_VEHICLE, "cofre", "seguranca residencial"),
    _rule("pet-training", "pet_training", NON_VEHICLE, "adestramento", "anti latido"),
    _rule("pet-repellents", "pet_repellents", NON_VEHICLE, "espanta cachorro", "repelente ultrassonico"),
    _rule("children-sensory", "children_sensory", NON_VEHICLE, "tapete de agua", "almofada infantil", "bebe"),
    _rule("adult-wellness", "adult_wellness", NON_VEHICLE, "vibrador", "clitoris", "we-vibe"),
    # Automoveis - iluminacao e carroceria primeiro para evitar termos genericos.
    _rule("auto-xenon-lamps", "auto_xenon_lamps", AUTO, "lampada xenon", "lampada de xenonio", "par de lampadas de xenonio"),
    _rule("auto-ballasts", "auto_ballasts", AUTO, "reator eletronico", "modulo de led", "modulo eletronico de alimentacao e controle do farol"),
    _rule("auto-turn-signals", "auto_turn_signals", AUTO, "pisca", "repetidor de seta", "repetidores laterais", "seta led"),
    _rule("auto-reflectors", "auto_reflectors", AUTO, "refletor"),
    _rule("auto-brake-lights", "auto_brake_lights", AUTO, "terceira luz de freio", "luz de freio"),
    _rule("auto-aux-lights", "auto_aux_lights", AUTO, "luz auxiliar", "fita de led", "luz diurna", "drl"),
    _rule("auto-headlight-guides", "auto_headlight_guides", AUTO, "guia farol", "guia de farol"),
    _rule("auto-headlight-protection", "auto_headlight_protection", AUTO, "protetor de farol", "tampa de farol"),
    _rule("auto-grilles", "auto_grilles", AUTO, "grade dianteira", "grade do para choque", "grade de luz", "grade superior", "conjunto de grades", "grade+frizo"),
    _rule(
        "auto-washers",
        "auto_washers",
        AUTO,
        "tampa parachoque esguicho farol",
        "tampa esguicho parachoque",
        "tampa lavador esguicho farol",
        "esguicho",
        "brucutu",
        "lavador",
    ),
    _rule("auto-guides-supports", "auto_guides_supports", AUTO, "guia suporte parachoque", "guia lateral do para-choque", "suporte dianteiro da placa"),
    _rule("auto-bumpers", "auto_bumpers", AUTO, "parachoque", "para-choque", "moldura inferior", "spoiler", "tampa do parabarro"),
    _rule("auto-trim-appliques", "auto_trim_appliques", AUTO, "friso", "frizo", "aplique cromado", "apliques cromados", "filete cromado", "filetes cromados"),
    _rule("auto-emblems", "auto_emblems", AUTO, "emblema", "estrela cromada"),
    _rule("auto-door-handles", "auto_door_handles", AUTO, "macaneta externa", "capas cromadas das macanetas", "capas cromadas para macanetas"),
    _rule("auto-interior-pulls", "auto_interior_pulls", AUTO, "puxador interno"),
    _rule("auto-door-pulls", "auto_door_pulls", AUTO, "puxador", "acabamento do puxador"),
    _rule("auto-hood-hinges", "auto_hood_hinges", AUTO, "dobradica capo", "dobradica de capo", "par dobradica compativel"),
    _rule("auto-tailgate", "auto_tailgate", AUTO, "tampa da cacamba", "tampa de cacamba", "amortecedor a gas", "amortecedor mola gas"),
    _rule("auto-trunk", "auto_trunk", AUTO, "porta malas", "porta-malas", "fechadura", "motor da trava"),
    _rule("auto-fuel-door", "auto_fuel_door", AUTO, "portinhola", "tampa do tanque de combustivel"),
    _rule("auto-exhaust-tips", "auto_exhaust_tips", AUTO, "ponteira de escapamento", "ponteiras de escapamento", "moldura ponteira escapamento"),
    _rule("auto-fasteners", "auto_fasteners", AUTO, "presilha automotiva", "presilhas automotivas"),
    _rule("auto-consoles", "auto_consoles", AUTO, "console do teto", "console central"),
    # Transmissao e freios.
    _rule("auto-shift-tracks", "auto_shift_tracks", AUTO, "esteira"),
    _rule("auto-shift-controls", "auto_shift_controls", AUTO, "manopla", "botao da alavanca", "alavanca de cambio"),
    _rule("auto-transmission-sensors", "auto_transmission_sensors", AUTO, "sensor de posicao da transmissao", "sensor g68", "sensor de velocidade da transmissao", "sensor de velocidade do veiculo"),
    _rule("auto-clutch-actuators", "auto_clutch_actuators", AUTO, "atuador hidraulico embreagem", "atuador de embreagem"),
    _rule("auto-transfer-case", "auto_transfer_case", AUTO, "caixa de transferencia", "engrenagem caixa de transferencia", "filtro haldex"),
    _rule(
        "auto-4x4-actuators",
        "auto_4x4_actuators",
        AUTO,
        "atuador a vacuo do eixo",
        "atuador a vacuo do engate do eixo",
        "atuador 4x4",
    ),
    _rule("auto-freewheel-hubs", "auto_freewheel_hubs", AUTO, "roda livre", "cubos dianteiros"),
    _rule("auto-driveshaft", "auto_driveshaft", AUTO, "cardan", "rolamento coxin mancal"),
    _rule("auto-brake-controls", "auto_brake_controls", AUTO, "botao freio mao", "botao comando freio mao", "botao do freio de estacionamento", "alavanca do freio de estacionamento"),
    _rule("auto-brake-trim", "auto_brake_trim", AUTO, "capa de freio de mao"),
    _rule("auto-brake-vacuum", "auto_brake_vacuum", AUTO, "servo-freio", "servo freio"),
    # Suspensao, direcao, rodas e pneus.
    _rule("auto-control-arms", "auto_control_arms", AUTO, "bandeja inferior", "par de bandejas"),
    _rule("auto-steering-pumps", "auto_steering_pumps", AUTO, "bomba de direcao"),
    _rule("auto-steering-reservoirs", "auto_steering_reservoirs", AUTO, "reservatorio direcao", "reservatorio de direcao"),
    _rule("auto-steering-sensors", "auto_steering_sensors", AUTO, "sensor de pressao da direcao", "sensor direcao hidraulica", "sensor pressao direcao"),
    _rule("auto-steering-couplings", "auto_steering_couplings", AUTO, "acoplamento da direcao", "bucha flexivel"),
    _rule("auto-wheel-trim", "auto_wheel_trim", AUTO, "calota central", "calotas centrais"),
    _rule("auto-tpms", "auto_tpms", AUTO, "sensor de pressao dos pneus", "tpms"),
    # Arrefecimento e climatizacao.
    _rule("auto-radiator-connectors", "auto_radiator_connectors", AUTO, "conector superior da mangueira do radiador", "conector com gargalo"),
    _rule("auto-thermostat-housings", "auto_thermostat_housings", AUTO, "carcaca da valvula termostatica", "carcaca valvula termostatica"),
    _rule("auto-thermostats", "auto_thermostats", AUTO, "valvula termostatica"),
    _rule("auto-thermal-switches", "auto_thermal_switches", AUTO, "interruptor ventoinha", "interruptor termico", "cebolao"),
    _rule("auto-fan-controls", "auto_fan_controls", AUTO, "modulo de controle do eletroventilador", "resistencia de controle do eletroventilador"),
    _rule("auto-cooling-reservoirs", "auto_cooling_reservoirs", AUTO, "reservatorio de expansao", "tampa reservatorio agua", "tampa do radiador"),
    _rule("auto-cooling-hoses", "auto_cooling_hoses", AUTO, "mangueira agua", "mangueira retorno reservatorio", "mangueira de retorno do reservatorio", "tubo de desvio do liquido", "flange de agua", "tubo saida corpo borboleta"),
    _rule("auto-radiators", "auto_radiators", AUTO, "radiador de arrefecimento", "radiador"),
    _rule("auto-climate-sensors", "auto_climate_sensors", AUTO, "sensor temperatura ar condicionado", "sensor de climatizacao"),
    _rule("auto-air-vents", "auto_air_vents", AUTO, "difusor de ar"),
    _rule("auto-cabin-filter-accessories", "auto_cabin_filter_accessories", AUTO, "tampa filtro ar condicionado"),
    # Combustivel, admissao, lubrificacao e emissoes.
    _rule("auto-intake-manifolds", "auto_intake_manifolds", AUTO, "coletor de admissao"),
    _rule("auto-intercooler-hoses", "auto_intercooler_hoses", AUTO, "mangueira intercooler", "mangueira do intercooler", "intercooler", "presilha metalica de retencao da mangueira do intercooler", "trava menor da mangueira do intercooler"),
    _rule("auto-air-hoses", "auto_air_hoses", AUTO, "mangueira filtro ar tbi", "mangueira filtro de ar tbi", "mangueira de entrada de ar"),
    _rule("auto-maf-map", "auto_maf_map", AUTO, "medidor de massa de ar", "medidor fluxo ar", "sensor de pressao map", "sensor de pressao absoluta", "sensor de pressao do coletor", "sensor de pressao da turbina"),
    _rule("auto-throttle", "auto_throttle", AUTO, "sensor de posicao da borboleta", "atuador marcha lenta", "atuador de marcha lenta"),
    _rule("auto-fuel-modules", "auto_fuel_modules", AUTO, "modulo da bomba de combustivel", "flange do modulo da bomba", "tampa e flange", "conjunto de flange e tampa"),
    _rule("auto-fuel-pumps", "auto_fuel_pumps", AUTO, "bomba de combustivel", "bomba combustivel", "refil eletrico"),
    _rule("auto-fuel-level", "auto_fuel_level", AUTO, "boia sensor nivel combustivel", "sensor de nivel de combustivel", "sensor nivel combustivel", "sensor medidor nivel combustivel"),
    _rule("auto-fuel-pressure-sensors", "auto_fuel_pressure_sensors", AUTO, "sensor pressao combustivel", "sensor de pressao da flauta", "sensor de pressao do tubo distribuidor", "sensor de pressao de combustivel"),
    _rule("auto-fuel-valves", "auto_fuel_valves", AUTO, "valvula dosadora", "valvula reguladora de pressao imv", "valvula mecanica limitadora", "valvula moduladora turbina"),
    _rule("auto-fuel-filters", "auto_fuel_filters", AUTO, "filtro combustivel", "filtro de combustivel"),
    _rule("auto-fuel-returns", "auto_fuel_returns", AUTO, "mangueira de retorno de combustivel", "mangueira de retorno dos bicos", "mangueira de retorno do combustivel"),
    _rule("auto-spark-plugs", "auto_spark_plugs", AUTO, "vela de ignicao", "velas de ignicao", "jogo de velas", "jogo 4 velas", "jogo 6 velas"),
    _rule("auto-ignition-coils", "auto_ignition_coils", AUTO, "bobina de ignicao"),
    _rule("auto-ignition-switches", "auto_ignition_switches", AUTO, "comutador eletrico de ignicao", "comutador de ignicao"),
    _rule("auto-oil-filters", "auto_oil_filters", AUTO, "filtro de tela de oleo", "filtros-tela de oleo"),
    _rule("auto-oil-tubes", "auto_oil_tubes", AUTO, "tubo de retorno de oleo"),
    _rule("auto-seals", "auto_seals", AUTO, "retentor", "anel de vedacao", "anel vedacao", "aneis de vedacao", "juntas com telas"),
    _rule("auto-oil-sensors", "auto_oil_sensors", AUTO, "sensor de pressao do oleo", "sensor pressao do oleo", "sensor oleo"),
    _rule("auto-vvt", "auto_vvt", AUTO, "valvula de controle do comando", "valvula solenoide vtec"),
    _rule("auto-evap", "auto_evap", AUTO, "canister", "carvao ativado", "valvula eletromagnetica de purga"),
    _rule("auto-pcv", "auto_pcv", AUTO, "pcv", "diafragma"),
    _rule("auto-vacuum-pumps", "auto_vacuum_pumps", AUTO, "bomba mecanica de vacuo", "bomba de vacuo"),
    _rule("auto-vacuum-valves", "auto_vacuum_valves", AUTO, "valvula de retencao do vacuo", "atuador a vacuo"),
    # Eletrica, comandos, telas e acabamento restante.
    _rule("auto-battery-sensors", "auto_battery_sensors", AUTO, "sensor inteligente de bateria", "sensor de bateria"),
    _rule("auto-battery-terminals", "auto_battery_terminals", AUTO, "terminal bateria", "conector terminal bateria"),
    _rule("auto-voltage-converters", "auto_voltage_converters", AUTO, "conversor de tensao"),
    _rule("auto-bsm-modules", "auto_bsm_modules", AUTO, "modulo bsm"),
    _rule("auto-fuse-boxes", "auto_fuse_boxes", AUTO, "modulo de fusiveis", "caixa de fusiveis"),
    _rule("auto-power-modules", "auto_power_modules", AUTO, "modulo de protecao e distribuicao", "placa de fusiveis bateria", "modulo placa de fusiveis"),
    _rule("auto-window-controls", "auto_window_controls", AUTO, "vidro eletrico", "vidros eletricos", "botao comando vidro", "comando mestre dos vidros"),
    _rule("auto-light-switches", "auto_light_switches", AUTO, "chave interruptora luzes", "interruptor rotativo das luzes"),
    _rule("auto-steering-controls", "auto_steering_controls", AUTO, "botao multifuncional de controle de volante"),
    _rule("auto-start-buttons", "auto_start_buttons", AUTO, "botao de partida", "partida e desligamento"),
    _rule("auto-lcd-displays", "auto_lcd_displays", AUTO, "tela de cristal liquido", "tela lcd do painel"),
    _rule("auto-multimedia-screens", "auto_multimedia_screens", AUTO, "tela de toque", "central multimidia"),
    _rule("auto-audio-cables", "auto_audio_cables", AUTO, "cabo auxiliar de audio"),
    _rule("auto-bluetooth", "auto_bluetooth", AUTO, "transmissor veicular bluetooth"),
    _rule("auto-cruise-control", "auto_cruise_control", AUTO, "piloto automatico", "controle de velocidade"),
    _rule("auto-door-trim", "auto_door_trim", AUTO, "acabamento da porta", "acabamento bege"),
    _rule("auto-shift-trim", "auto_shift_trim", AUTO, "acabamento do cambio", "capa de cambio"),
    _rule("auto-dashboard-trim", "auto_dashboard_trim", AUTO, "acabamento do painel"),
)


def _normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _slug(value: Any, *, fallback: str = "item") -> str:
    return re.sub(r"[^a-z0-9]+", "-", _normalize(value)).strip("-") or fallback


def product_category_id(path: Sequence[str]) -> str:
    return "jk:sku-category:" + ":".join(_slug(part) for part in path)


def taxonomy_nodes() -> tuple[dict[str, Any], ...]:
    """Retorna todos os nos, incluindo pais implicitos e fila de revisao."""

    ordered_paths: dict[tuple[str, ...], None] = {}
    for leaf in (*PRODUCT_CATEGORY_PATHS, REVIEW_PATH):
        for depth in range(1, len(leaf) + 1):
            ordered_paths.setdefault(leaf[:depth], None)
    leaf_paths = set(PRODUCT_CATEGORY_PATHS) | {REVIEW_PATH}
    nodes: list[dict[str, Any]] = []
    for path in ordered_paths:
        child_paths = [
            candidate
            for candidate in ordered_paths
            if len(candidate) == len(path) + 1 and candidate[:-1] == path
        ]
        nodes.append(
            {
                "id": product_category_id(path),
                "label": path[-1],
                "path": list(path),
                "parent_id": product_category_id(path[:-1]) if len(path) > 1 else "",
                "child_ids": [product_category_id(child) for child in child_paths],
                "is_leaf": path in leaf_paths,
                "is_review_queue": path == REVIEW_PATH,
            }
        )
    return tuple(nodes)


def classify_sku_product(
    *,
    sku: str,
    product_name: str,
    application_type: str,
    description: str = "",
    uses: Sequence[str] = (),
    characteristics: Sequence[str] = (),
) -> dict[str, Any]:
    """Classifica um SKU por regras auditaveis ou o envia para revisao."""

    normalized_type = _normalize(application_type)
    normalized_name = _normalize(product_name)
    normalized_context = _normalize(
        " ".join((product_name, description, *uses, *characteristics))
    )
    eligible_rules = [
        rule
        for rule in TAXONOMY_RULES
        if normalized_type in rule.application_types
        and not any(_normalize(term) in normalized_context for term in rule.excluded_terms)
    ]
    # Evidencia direta no nome do produto sempre vence descricao, uso e
    # caracteristicas. O contexto secundario so e consultado quando nenhuma
    # regra aplicavel encontra termo no titulo, evitando que uma mencao
    # incidental antecipe uma categoria mais especifica.
    for evidence_source in (normalized_name, normalized_context):
        best_key: tuple[int, int, int] | None = None
        best_rule: TaxonomyRule | None = None
        best_matches: list[str] = []
        for rule_index, rule in enumerate(eligible_rules):
            occurrences = [
                (evidence_source.find(normalized_term), len(normalized_term), term)
                for term in rule.terms
                if (normalized_term := _normalize(term))
                and normalized_term in evidence_source
            ]
            if not occurrences:
                continue
            earliest = min(position for position, _length, _term in occurrences)
            most_specific = max(
                length for position, length, _term in occurrences if position == earliest
            )
            candidate_key = (earliest, -most_specific, rule_index)
            if best_key is None or candidate_key < best_key:
                best_key = candidate_key
                best_rule = rule
                best_matches = [term for _position, _length, term in occurrences]
        if best_rule is not None:
            path = CATEGORY_PATHS[best_rule.category_key]
            return {
                "taxonomy_version": PRODUCT_CATEGORY_TAXONOMY_VERSION,
                "category_id": product_category_id(path),
                "path": list(path),
                "status": "classified_by_rule",
                "rule_id": best_rule.rule_id,
                "evidence": sorted({_normalize(term) for term in best_matches}),
                "sku": str(sku),
            }
    return {
        "taxonomy_version": PRODUCT_CATEGORY_TAXONOMY_VERSION,
        "category_id": product_category_id(REVIEW_PATH),
        "path": list(REVIEW_PATH),
        "status": "pending_review",
        "rule_id": "no-safe-rule",
        "evidence": [],
        "sku": str(sku),
    }


def taxonomy_tree_lines() -> tuple[str, ...]:
    """Renderiza a arvore aprovada sem depender de plugins do Obsidian."""

    nodes = taxonomy_nodes()
    labels_by_id = {str(node["id"]): str(node["label"]) for node in nodes}
    children_by_parent: dict[str, list[str]] = {}
    for node in nodes:
        if node.get("is_review_queue"):
            continue
        children_by_parent.setdefault(str(node.get("parent_id") or ""), []).append(str(node["id"]))

    lines: list[str] = []

    def visit(node_id: str, depth: int) -> None:
        lines.append("  " * depth + f"- {labels_by_id[node_id]}")
        for child_id in children_by_parent.get(node_id, []):
            visit(child_id, depth + 1)

    for root_id in children_by_parent.get("", []):
        visit(root_id, 0)
    return tuple(lines)
