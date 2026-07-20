from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form, Request, Header
from fastapi.encoders import jsonable_encoder
import hashlib
import os
import ssl
import tempfile
from pathlib import Path


def _configurar_ca_bundle_windows() -> None:
    if os.name != "nt" or os.environ.get("JK_DISABLE_WINDOWS_CA_BUNDLE") == "1":
        return
    try:
        import certifi
    except Exception:
        return
    if not hasattr(ssl, "enum_certificates"):
        return
    try:
        certifi_path = Path(certifi.where())
        base = certifi_path.read_bytes()
        digest = hashlib.sha1(str(certifi_path).encode("utf-8", errors="ignore") + base[:4096]).hexdigest()[:12]
        bundle_path = Path(tempfile.gettempdir()) / f"jk_sistema_ca_bundle_{digest}.pem"
        if not bundle_path.exists() or bundle_path.stat().st_mtime < certifi_path.stat().st_mtime:
            with open(bundle_path, "wb") as out:
                out.write(base)
                out.write(b"\n")
                seen: set[bytes] = set()
                for store_name in ("ROOT", "CA"):
                    try:
                        certificates = ssl.enum_certificates(store_name)
                    except Exception:
                        continue
                    for cert, encoding, _trust in certificates:
                        if encoding != "x509_asn" or cert in seen:
                            continue
                        seen.add(cert)
                        out.write(ssl.DER_cert_to_PEM_cert(cert).encode("ascii"))
        bundle_text = str(bundle_path)
        os.environ["JK_CA_BUNDLE"] = bundle_text
        os.environ["SSL_CERT_FILE"] = bundle_text
        os.environ["REQUESTS_CA_BUNDLE"] = bundle_text
        os.environ["CURL_CA_BUNDLE"] = bundle_text
        os.environ["GRPC_DEFAULT_SSL_ROOTS_FILE_PATH"] = bundle_text
    except Exception:
        return


_configurar_ca_bundle_windows()

from fastapi.responses import StreamingResponse, RedirectResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import logging
import gspread
import google.auth
from google.oauth2 import id_token as google_id_token
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.auth.exceptions import GoogleAuthError
try:
    from google.auth import impersonated_credentials
except Exception:
    impersonated_credentials = None
import bcrypt
import os
import socket
import io
import json
import zipfile
import csv
import copy
import html as html_lib
import pandas as pd
import uuid
import numpy as np
import datetime as dt
import unicodedata
import re
from difflib import SequenceMatcher
import openpyxl
from openpyxl.drawing.image import Image as XLImage
from openpyxl.cell.cell import MergedCell
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from datetime import datetime, timedelta
import requests
import urllib3
from dotenv import load_dotenv, dotenv_values
from bs4 import BeautifulSoup
import base64
import time
import random
import sqlite3
import functools
import fnmatch
from urllib.parse import quote, quote_plus, urlencode, urlparse, parse_qs, unquote
import asyncio
import shutil
import threading
import multiprocessing
import queue
import math
import subprocess
import sys
import tempfile
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, TimeoutError as FuturesTimeoutError
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options as ChromeOptions

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service

from backend.routers import (
    ConfiguracoesRouterConfig,
    FrontendRouterConfig,
    create_admin_usuarios_router,
    create_cadastro_router,
    create_codex_console_router,
    create_configuracoes_router,
    create_context_hub_router,
    create_estoque_router,
    EtiquetasRouterConfig,
    create_etiquetas_router,
    create_favoritos_router,
    create_frontend_router,
    FullRouterConfig,
    create_full_router,
    create_ia_router,
    create_impostos_router,
    create_importacoes_router,
    InfraRouterConfig,
    create_infra_router,
    IntegracoesRouterConfig,
    create_integracoes_router,
    MercadoLivreRouterConfig,
    create_mercado_livre_router,
    create_medias_compras_router,
    create_perguntas_pos_venda_router,
    create_promocoes_router,
    RenovacaoRouterConfig,
    create_renovacao_router,
    create_sala_reuniao_router,
    create_shared_sync_router,
    create_whatsapp_bridge_router,
    include_feature_routers,
    mount_static_assets,
)
from backend.lifecycle import register_startup_events
from backend.services.bling import BLING_SESSION, _BlingAdaptiveLimiter, _bling_get_with_adaptive_limit
from backend.services.configuracoes_drive_sync import (
    configure_configuracoes_drive_sync_context,
    _drive_sync_carregar_estado,
    _drive_sync_contexto_usuario,
    _drive_sync_listar_backups,
    _drive_sync_local_snapshot,
    _drive_sync_restaurar_ultimo,
    _drive_sync_upload_backup,
    _google_drive_token_linkado,
)
from backend.services.configuracoes import (
    CONFIG_GLOBAIS_DEFAULT,
    _carregar_configuracoes_globais,
    _carregar_configuracoes_globais_firebase,
    _carregar_configuracoes_globais_local,
    _configuracoes_chaves_sensiveis,
    _configuracoes_globais_ler_firebase,
    _configuracoes_globais_salvar_firebase_bloqueante,
    _firebase_configuracoes_globais_collection_name,
    _firebase_configuracoes_globais_db,
    _firebase_configuracoes_globais_doc_id,
    _normalizar_configuracoes_globais,
    _normalizar_ia_modo,
    _salvar_configuracoes_globais,
    _salvar_configuracoes_globais_firebase,
    _salvar_configuracoes_globais_firebase_background,
    _salvar_configuracoes_globais_local,
    configure_configuracoes_context,
)
from backend.services.env_config import (
    _agent_service_only,
    _env_bool,
    _env_config_bool,
    _env_config_value,
    _request_eh_local,
    _resolver_redirect_uri_bling,
    _resolver_redirect_uri_publica,
    configure_env_context,
)
from backend.services.secure_credentials import (
    delete_secret as _secure_delete_secret,
    read_any_secret as _secure_read_any_secret,
    read_secret as _secure_read_secret,
    secrets_status as _secure_secrets_status,
    secure_store_available as _secure_store_available,
    write_secret as _secure_write_secret,
    write_secrets_bundle as _secure_write_secrets_bundle,
)
from backend.services.favoritos import (
    _favoritos_arquivo_planilhas_lojas,
    _favoritos_arquivo_skus_ocultos,
    _favoritos_carregar_planilhas_lojas,
    _favoritos_ml_persist_cache_apply,
    _favoritos_ml_persist_cache_clean_payload,
    _favoritos_ml_persist_cache_meta,
    _favoritos_ml_persist_cache_path,
    _favoritos_ml_persist_cache_read,
    _favoritos_ml_persist_cache_slug,
    _favoritos_ml_persist_cache_write,
    _favoritos_normalizar_planilha_loja_item,
    _favoritos_normalizar_skus_ocultos,
    _favoritos_normalizar_url_planilha_google,
    _favoritos_salvar_planilhas_lojas,
    _favoritos_usuario_slug,
    configure_favoritos_context,
)
from backend.services.favoritos_planilhas_colar import favoritos_colar_historico_planilha
from backend.services import favoritos_endpoints as favoritos_endpoint_service
from backend.services import perguntas_pos_venda_endpoints as perguntas_pos_venda_endpoint_service
from backend.services import perguntas_pos_venda_codex as perguntas_pos_venda_codex_service
for _endpoint_name in perguntas_pos_venda_endpoint_service.PERGUNTAS_POS_VENDA_ENDPOINTS:
    globals()[_endpoint_name] = getattr(perguntas_pos_venda_endpoint_service, _endpoint_name)
for _endpoint_name in favoritos_endpoint_service.FAVORITOS_ENDPOINTS:
    globals()[_endpoint_name] = getattr(favoritos_endpoint_service, _endpoint_name)
del _endpoint_name
from backend.services.full import configure_full_context
from backend.services.full_calendario import configure_full_calendario_context
from backend.services.full_mercadolivre import configure_full_mercadolivre_context
from backend.services.infra import (
    _chave_comparacao_versao,
    _normalizar_versao_app,
)
from backend.services.impostos import (
    SISCOMEX_AMBIENTES,
    _ajustar_pis_cofins_monofasico_revenda,
    _aliquotas_pis_cofins_por_regime,
    _formatar_ncm_pontos,
    _normalizar_aliquota_percentual,
    _normalizar_auth_header_type_siscomex,
    _normalizar_coluna_ncm_excel,
    _normalizar_loja_siscomex,
    _normalizar_perfil_siscomex,
    _normalizar_role_type_siscomex,
    _normalizar_siscomex_ambiente,
    _normalizar_tipo_operacao_siscomex,
    _siscomex_scope_key,
    _siscomex_scope_payload,
)
from backend.services.integracoes import (
    auth_bling_exchange,
    auth_bling_get_link,
    auth_ml_exchange,
    auth_ml_get_link,
    atualizar_api_loja,
    buscar_loja,
    carregar_lojas,
    configure_integracoes_context,
    desconectar_api_loja,
    ler_temp_auth,
    limpar_temp_auth,
    salvar_lojas,
    salvar_temp_auth,
    _integracoes_converter_legado,
    _integracoes_coletar_legadas,
    _integracoes_ler_json,
    _integracoes_merge_sem_sobrescrever,
    _integracoes_mesclar_legadas,
    _integracoes_nome_equivalente,
    _integracoes_nome_normalizado,
    _integracoes_normalizar_oauth_compartilhado_lojas,
    _integracoes_pode_criar_lojas_legadas,
    _integracoes_valor_preenchido,
)
from backend.services.ia import (
    _extrair_b64_openai_image_response,
    _extrair_texto_openai_response,
    _ia_chat_extrair_limite_top,
    _ia_chat_extrair_opcoes_top_dias_sem_venda,
    _ia_chat_bloco_prompt_analise_especialista,
    _ia_chat_deve_anexar_vendas_db_contexto,
    _ia_chat_extrair_texto_anexo,
    _ia_chat_mensagem_contextual,
    _ia_chat_normalizar_anexos,
    _ia_chat_pede_analise_especialista_vendas,
    _ia_chat_pede_anomalia,
    _ia_chat_pede_comparativo_periodo,
    _ia_chat_pede_consulta_bling,
    _ia_chat_pede_consulta_devolucoes,
    _ia_chat_pede_consulta_estoque,
    _ia_chat_pede_consulta_margem,
    _ia_chat_pede_consulta_mercado_livre,
    _ia_chat_pede_consulta_produto,
    _ia_chat_pede_consulta_vendas,
    _ia_chat_pede_dias_sem_venda,
    _ia_chat_pede_geracao_imagem,
    _ia_chat_pede_imagem_produto,
    _ia_chat_pede_info_cadastro_produto,
    _ia_chat_pede_lucro_periodo,
    _ia_chat_pede_noticias,
    _ia_chat_pede_previsao_ruptura_estoque,
    _ia_chat_pede_recorte_mensal,
    _ia_chat_pede_serie_temporal,
    _ia_chat_pede_status_integracoes,
    _ia_chat_pede_taxa_devolucao,
    _ia_chat_pede_ticket_medio,
    _ia_chat_pede_top_dias_sem_venda,
    _ia_chat_pede_vendas_por_loja_virtual,
    _ia_chat_resumo_historico,
    _ia_chat_tem_imagem,
    _ia_contexto_desativa_recursos_chat,
    _ia_normalizar_data_iso_chat,
    _ia_normalizar_periodo_chat,
    _ia_parse_data_iso_flex,
    _ia_periodo_do_mes,
    _ia_subtrair_meses,
    _ia_ultimo_dia_mes,
)
from backend.services.mercadolivre import (
    _cache_invalidar_loja,
    _ml_cache_get,
    _ml_cache_set,
    _ml_contar_itens_promocao_status,
    _ml_extrair_contagem_campanha,
    _ml_listar_promocoes_ativas_payload,
    _ml_http_invalidar_session,
    _ml_http_request,
    listar_anuncios_mercado_livre,
    MercadoLivreServiceConfig,
    configure_mercado_livre_context,
)
from backend.core import AppPaths
from backend.modules.vendas import (
    LegacyBlingVendasAdapter,
    VendasModuleDependencies,
    create_vendas_module,
    install_default_vendas_module,
)
from backend.services.renovacao import (
    RenovacaoServiceConfig,
    configure_renovacao_context,
    find_sheet_name,
    _renovacao_agendamento_atual,
    _renovacao_agendamento_put_payload,
    _renovacao_atualizar_periodo_campanha_ml,
    _renovacao_criar_campanha_manual_ml,
    _renovacao_criar_ou_completar_proximo_mes,
    _renovacao_deletar_campanha_ml,
    _renovacao_iniciar_agendamento_background,
    _renovacao_listar_campanhas_usuario_payload,
    _renovacao_sincronizar_promocao_existente,
    _renovacao_sincronizar_promocao_iniciar_payload,
    _renovacao_sync_job_get,
)
from backend.schemas import (
    LoginRequest,
    GoogleLoginRequest,
    LoginResponse,
    AdminUserUpsertRequest,
    AdminUserPasswordRequest,
    UserChangePasswordRequest,
    AdminUserMaxMachinesRequest,
    AdminUserPermissionsRequest,
    AdminUserStatusRequest,
    AdminUserMessageRequest,
    UserChatAttachment,
    UserChatMessageRequest,
    UserChatTypingRequest,
    MachinePresenceHeartbeatRequest,
    SharedSyncScopeConfigRequest,
    SharedSyncConfigRequest,
    SharedSyncRunRequest,
    SharedSyncMachineConfigRequest,
    SharedSyncUserInviteCreateRequest,
    SharedSyncUserInviteActionRequest,
    SharedSyncUserLinkUpdateRequest,
    SharedSyncUserLinkRunRequest,
    IAChatAttachment,
    IAChatRequest,
    IAAgentQueryRequest,
    IATreinamentoPerguntasPosVendaRequest,
    IATreinamentoPerguntasPosVendaSimularRequest,
    PerguntasLojaConfigRequest,
    PerguntasLojasConfigLoteRequest,
    PerguntasAprovacaoRequest,
    PerguntasGerarRespostaRequest,
    PerguntasEnviarRespostaRequest,
    MLQuestionsV2ProcessRequest,
    MLQuestionsV2ReviewActionRequest,
    PosVendaMensagemRequest,
    PosVendaGerarRespostaRequest,
    IARagDocumento,
    IARagIndexRequest,
    IARagReindexRequest,
    PromoRequest,
    MLPrecoRequest,
    MLEstoqueRequest,
    MLStatusRequest,
    MLPromocaoRequest,
    MLPromocoesItensRequest,
    FavoritosEfetivarPromocaoRequest,
    FavoritosValidarEfetivacaoItemRequest,
    FavoritosValidarEfetivacaoRequest,
    RenovacaoCampanhaSincronizarRequest,
    RenovacaoAgendamentoRequest,
    MLDescricaoRequest,
    MLRespostaPerguntaRequest,
    EstoqueSyncRequest,
    EstoqueLancamentosSyncRequest,
    EstoqueLancamentosSyncLoteRequest,
    SiscomexConfigRequest,
    CadastroProdutoRequest,
    ImpostoRegraRequest,
    ImpostosSimulacaoRequest,
    SimuladorCalculoRequest,
    SiscomexFundamentoOpcionalRequest,
    SiscomexConsultaRequest,
    VendasQuery,
    VendasSyncRequest,
    MediasComprasItem,
    MediasComprasRequest,
    ListaCompraRequest,
    ListaPedidoUpdateRequest,
    ListaPedidoStatusRequest,
    ListaPedidoAddSkuRequest,
    ListaPedidoPreferenciasColunasRequest,
    MediasComprasSkusOcultosRequest,
    EstoquePreferenciasColunasRequest,
    PromoPreferenciasColunasRequest,
    FavoritosSearchRequest,
    FavoritosPrimeiraPaginaRequest,
    FavoritosEnriquecerDatasRequest,
    FavoritosSkuDescricoesRequest,
    FavoritosSkuPesquisaRequest,
    FavoritosSkuPesquisaIAItem,
    FavoritosSkusPesquisaIARequest,
    FavoritosRankingIARequest,
    FavoritosSkusOcultosRequest,
    FavoritosVendedoresIgnoradosRequest,
    FavoritosAnunciosIgnoradosRequest,
    FavoritosHistoricoRequest,
    FavoritosHistoricoRealtimeSyncRequest,
    FavoritosPlanilhaLojaItem,
    FavoritosPlanilhasLojasRequest,
    FavoritosPlanilhaColarHistoricoRequest,
    IASalvarConversaRequest,
    SiscomexAliquotasRequest,
)
from jose import JWTError, jwt
import secrets
import hashlib
from typing import Any, Optional, Callable
from ml_questions_gemini import (
    AIAnswer,
    GeminiQuestionsSettings,
    MercadoLivreWebhookReceiver,
    QuestionAnswerOrchestrator,
    context_from_agent_input,
)
from ml_questions_gemini.parser import AIResponseParser
try:
    import firebase_admin
    from firebase_admin import auth as firebase_auth
    from firebase_admin import credentials as firebase_credentials
    from firebase_admin import firestore as firebase_firestore
    from firebase_admin import db as firebase_realtime_db
except Exception:
    firebase_admin = None
    firebase_auth = None
    firebase_credentials = None
    firebase_firestore = None
    firebase_realtime_db = None
try:
    from cryptography.fernet import Fernet, InvalidToken
except Exception:
    Fernet = None
    InvalidToken = Exception

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_PASTA_INFO_ENV = (os.getenv("JK_INFO_DIR") or "").strip()
PASTA_INFO = _PASTA_INFO_ENV if _PASTA_INFO_ENV else os.path.join(BASE_DIR, "info")
if not os.path.isabs(PASTA_INFO):
    PASTA_INFO = os.path.join(BASE_DIR, PASTA_INFO)
os.makedirs(PASTA_INFO, exist_ok=True)

try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:
    psycopg = None
    dict_row = None

# Armazenamento temporÃƒÆ’Ã‚Â¡rio em memÃƒÆ’Ã‚Â³ria para arquivos gerados (UUID -> bytes)
TEMP_FILES_STORAGE = {}
# Metadados dos arquivos temporÃƒÂ¡rios (UUID -> mime/filename)
TEMP_FILES_META = {}
# Cache de Excel de listas de pedidos jÃƒÂ¡ geradas: chave -> bytes
LISTA_PEDIDO_XLSX_CACHE: dict[str, bytes] = {}
LISTA_PEDIDO_XLSX_CACHE_MAX_ITENS = 40
# Flags de cancelamento de sincronizaÃƒÆ’Ã‚Â§ÃƒÆ’Ã‚Â£o por cliente
SYNC_CANCEL_FLAGS = {}
# Progresso de sincronizaÃƒÆ’Ã‚Â§ÃƒÆ’Ã‚Â£o por cliente
SYNC_PROGRESS = {}
# Logs de sincronizaÃƒÆ’Ã‚Â§ÃƒÆ’Ã‚Â£o por cliente (linhas para a UI)
SYNC_LOGS = {}
# Metadados da sincronizaÃƒÂ§ÃƒÂ£o de vendas por cliente (loja/perÃƒÂ­odo)
SYNC_META = {}
# Contexto temporario para transformar progresso de um dia em progresso global
SYNC_DAY_CONTEXT = {}
# Controle interno para permitir sincronizar vendas de ate 2 contas ao mesmo tempo
SYNC_MAX_ACTIVE_VENDAS = 2
SYNC_ACTIVE_LOCK = threading.RLock()
SYNC_STATE_LOCK = threading.RLock()
SYNC_THREAD_CONTEXT = threading.local()
GOOGLE_LOGIN_STATES = {}
GOOGLE_LOGIN_RESULTS = {}
GOOGLE_LOGIN_STATE_LOCK = threading.RLock()
FIREBASE_AUTH_LOCK = threading.RLock()
FIREBASE_AUTH_APP = None
FIREBASE_AUTH_DB = None
FIREBASE_AUTH_LAST_ERROR = ""
BACKEND_READ_CACHE_LOCK = threading.RLock()
BACKEND_READ_CACHE: dict[str, tuple[float, Any]] = {}
MACHINE_PRESENCE_LOCK = threading.RLock()
MACHINE_PRESENCE_AUTO_TOUCH_LAST: dict[str, float] = {}
ADMIN_MESSAGES_LOCK = threading.RLock()
USER_CHAT_MESSAGES_LOCK = threading.RLock()
USER_STATUS_LOCK = threading.RLock()
SHARED_SYNC_USER_LINKS_LOCK = threading.RLock()
# Jobs de sincronizaÃƒÂ§ÃƒÂ£o de NCM no Cadastro por tarefa
SYNC_NCM_JOBS = {}
# Controle de sincronizaÃƒÂ§ÃƒÂ£o de vendas ativa por cliente (para background threading)
SYNC_ACTIVE = {}
# Controle de sincronizaÃƒÂ§ÃƒÂ£o de estoque em background por cliente
ESTOQUE_SYNC_ACTIVE = {}
ESTOQUE_SYNC_PROGRESS = {}
ESTOQUE_SYNC_LOGS = {}
ESTOQUE_SYNC_META = {}
ESTOQUE_SYNC_CANCEL_FLAGS = {}
ESTOQUE_LANC_SYNC_ACTIVE = {}
ESTOQUE_LANC_SYNC_PROGRESS = {}
ESTOQUE_LANC_SYNC_LOGS = {}
ESTOQUE_LANC_SYNC_META = {}
ML_ITEM_SHIPPING_CACHE = {}
ML_ITEM_SHIPPING_CACHE_TTL = 900
ML_LISTING_FEE_CACHE = {}
ML_LISTING_FEE_CACHE_TTL = 900
ML_LOCAL_PRODUCTS_CACHE = {}
ML_LOCAL_PRODUCTS_CACHE_TTL = 900
ML_ITEM_PROMOTIONS_CACHE = {}
ML_ITEM_PROMOTIONS_CACHE_TTL = 900
# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),  # Logs no console
        logging.FileHandler(os.path.join(PASTA_INFO, "backend.log"), encoding='utf-8')
    ]
)
logger = logging.getLogger("jk_sistema")
load_dotenv()

# --- CONFIGURAÃƒÆ’Ã¢â‚¬Â¡ÃƒÆ’Ã¢â‚¬Â¢ES ---
PASTA_IMG = os.path.join(BASE_DIR, "img")
CREDENTIALS_FILE = os.path.join(PASTA_INFO, 'credentials.json')
SPREADSHEET_ID_CLIENTES = '1oyLYMd059baSs2KZJ8jqld68Y03a3pNRs32xvZlowNk'
CONFIG_FILE = os.path.join(PASTA_INFO, 'config_sheet.json')
ARQUIVO_DB_PRODUTOS = os.path.join(PASTA_INFO, "produtos_compilado.csv")
ARQUIVO_DB_CADASTRO_PRODUTOS = os.path.join(PASTA_INFO, "cadastro_produtos.csv")
ARQUIVO_DB_VENDAS = os.path.join(PASTA_INFO, "vendas_historico.db")
ARQUIVO_CACHE_USUARIOS = os.path.join(PASTA_INFO, "usuarios_cache.json")
ARQUIVO_USUARIOS_LOCAL = os.path.join(PASTA_INFO, "usuarios_local.json")
ARQUIVO_AUTH_DB = os.path.join(PASTA_INFO, "auth_users.db")
ARQUIVO_FIREBASE_SERVICE_ACCOUNT = os.path.join(PASTA_INFO, "firebase-service-account.json")
ARQUIVO_MACHINE_PRESENCE = os.path.join(PASTA_INFO, "machine_presence.json")
ARQUIVO_ADMIN_MESSAGES = os.path.join(PASTA_INFO, "admin_messages.json")
ARQUIVO_USER_CHAT_MESSAGES = os.path.join(PASTA_INFO, "user_chat_messages.json")
ARQUIVO_USER_CHAT_TYPING = os.path.join(PASTA_INFO, "user_chat_typing.json")
ARQUIVO_USER_STATUS = os.path.join(PASTA_INFO, "user_status.json")
ARQUIVO_SHARED_SYNC_USER_INVITES = os.path.join(PASTA_INFO, "shared_sync_user_invites.json")
ARQUIVO_SHARED_SYNC_USER_LINKS = os.path.join(PASTA_INFO, "shared_sync_user_links.json")
ARQUIVO_CONFIG_GLOBAIS = os.path.join(PASTA_INFO, "configuracoes_globais.json")
ARQUIVO_VERTEX_AGENT_API_KEY = os.path.join(PASTA_INFO, "vertex_agent_api_key.txt")
VERTEX_AI_AUTH_MODE_ENV_KEYS = (
    "VERTEX_AI_AUTH_MODE",
    "IA_VERTEX_AUTH_MODE",
    "JK_IA_AUTH_MODE",
)
ARQUIVO_NCM_XLSX = os.path.join(BASE_DIR, "NCM.xlsx")
ARQUIVO_NCM1_XLSX = os.path.join(BASE_DIR, "NCM1.xlsx")
SPREADSHEET_ID_SISTEMA_FIXO = '1Kj8ioVDpjLDH2kTSKWvBYKX4-R5zryTj2Ka5_G2irO0'
SPREADSHEET_ID_FOTOS_SKU = '1fehfm3TPRfM8PIVqRwGAZQHcSsf3p6vxKTKhM4B1lvM'
SPREADSHEET_GID_FOTOS_SKU = 630209700
SPREADSHEET_ID_CONCORRENTES = '18iMXghYIcTGrbpbyl8Nb4LCQMUFE0a1nD4XAUP5jZIg'

# --- Configs de redirecionamento OAuth ---

# Callback deve usar a mesma porta do backend (uvicorn).
# Pode ser sobrescrito por variÃƒÂ¡vel de ambiente JK_REDIRECT_URI.
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8001/auth/callback"
REDIRECT_URI = os.getenv("JK_REDIRECT_URI", DEFAULT_REDIRECT_URI).strip()
configure_env_context(
    base_dir=BASE_DIR,
    default_redirect_uri=DEFAULT_REDIRECT_URI,
    redirect_uri=REDIRECT_URI,
)

PERMISSION_KEYS = [
    'analise_promo', 'renovacao_fixa', 'vendas', 'estoque', 'integracao',
    'etiquetas', 'full', 'favoritos', 'avant', 'perguntas_pos_venda', 'anuncios_ml', 'medias_compras', 'mercado_full',
    'cadastro', 'impostos', 'configuracoes', 'importacoes', 'simulador', 'sala_reuniao', 'admin_usuarios'
]

VERSAO_MINIMA_APP_PADRAO = "1.0.102"


def versao_minima_app_backend() -> str:
    for chave in ("JK_APP_MIN_VERSION", "JK_APP_MINIMUM_VERSION", "VERSAO_MINIMA_APP"):
        versao = _normalizar_versao_app(os.getenv(chave, ""))
        if versao:
            return versao
    return _normalizar_versao_app(VERSAO_MINIMA_APP_PADRAO)


def _payload_update_required_app(app_version: Optional[Any]) -> dict:
    versao_minima = versao_minima_app_backend()
    versao_atual = _normalizar_versao_app(app_version)
    return {
        "success": False,
        "code": "update_required",
        "reason": "app_version_below_minimum" if versao_atual else "app_version_required",
        "message": (
            "Atualize o JK Sistema para continuar. "
            f"Versao minima exigida: {versao_minima}."
        ),
        "current_version": versao_atual,
        "minimum_version": versao_minima,
    }


def _validar_versao_minima_app_ou_426(app_version: Optional[Any]) -> str:
    versao_minima = versao_minima_app_backend()
    if not versao_minima:
        return _normalizar_versao_app(app_version)
    versao_atual = _normalizar_versao_app(app_version)
    if not versao_atual or _chave_comparacao_versao(versao_atual) < _chave_comparacao_versao(versao_minima):
        raise HTTPException(
            status_code=426,
            detail=_payload_update_required_app(app_version),
            headers={
                "X-JK-Update-Required": "1",
                "X-JK-Min-Version": versao_minima,
            },
        )
    return versao_atual


app = FastAPI(title="JK Sistema API")
include_feature_routers(app)

# ConfiguraÃƒÆ’Ã‚Â§ÃƒÆ’Ã‚Â£o CORS (Permite que o Frontend acesse o Backend)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Em produÃƒÆ’Ã‚Â§ÃƒÆ’Ã‚Â£o, especifique a URL do frontend
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


_infra_router_config = InfraRouterConfig(
    app_version=lambda: os.getenv("JK_APP_VERSION", ""),
    firebase_configured=lambda: bool(_firebase_tem_configuracao()),
    firebase_active=lambda: bool(_firebase_deve_usar()),
    firebase_live_features=lambda: bool(_firebase_live_features_ativas()),
    firebase_last_error=lambda: _admin_usuarios_module.firebase_auth_last_error(),
    minimum_app_version=versao_minima_app_backend,
    base_dir=BASE_DIR,
    pasta_info=PASTA_INFO,
)
app.include_router(create_infra_router(_infra_router_config))


# --- MODELOS DE DADOS (Pydantic) ---









































# --- JWT ---
def _carregar_ou_gerar_jwt_secret() -> str:
    secret_file = os.path.join(PASTA_INFO, "jwt_secret.key")
    if os.path.exists(secret_file):
        with open(secret_file, "r") as f:
            s = f.read().strip()
        if s:
            return s
    s = secrets.token_hex(64)
    with open(secret_file, "w") as f:
        f.write(s)
    return s

JWT_SECRET = _carregar_ou_gerar_jwt_secret()
JWT_ALGORITHM = "HS256"
JWT_DECODE_OPTIONS = {"verify_exp": False}

def criar_access_token(username: str, client_id: str, machine_id: Optional[str] = None) -> str:
    payload = {"sub": username, "client_id": client_id}
    if machine_id:
        payload["machine_id"] = str(machine_id).strip()
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decodificar_access_token(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM], options=JWT_DECODE_OPTIONS)

    



























# Mercado Livre legacy core helpers lives in backend.services.mercadolivre_legacy_core.
from backend.services import mercadolivre_legacy_core as _mercadolivre_legacy_core_module
_mercadolivre_legacy_core_module.configure_mercadolivre_legacy_core_runtime(sys.modules[__name__])
globals().update({name: getattr(_mercadolivre_legacy_core_module, name) for name in _mercadolivre_legacy_core_module.__all__})


def _normalizar_sku_mes(sku: str) -> str:
    """Normaliza SKU: converte mÃƒÂªs abreviado para numÃƒÂ©rico e aplica zero-padding apenas em SKUs de 1 dÃƒÂ­gito.
    Ex: 4-JAN -> 4-1, 1 -> 001, 9 -> 009, 10 -> 10, 99 -> 99."""
    sku_txt = str(sku or "").strip()
    # Converte SKUs com mÃƒÂªs abreviado (ex: 4-JAN -> 4-1)
    m = re.match(r"^\s*(\d+)\s*[-/]\s*([A-Za-z]{3})\s*$", sku_txt)
    if m:
        mes_map = {
            "JAN": "1", "FEV": "2", "MAR": "3", "ABR": "4", "MAI": "5", "JUN": "6",
            "JUL": "7", "AGO": "8", "SET": "9", "OUT": "10", "NOV": "11", "DEZ": "12"
        }
        dia = str(int(m.group(1)))
        mes = m.group(2).upper()
        if mes in mes_map:
            sku_txt = f"{dia}-{mes_map[mes]}"
    # Zero-padding apenas para SKUs de 1 dÃƒÂ­gito (ex: 1 -> 001, 9 -> 009); 10-99 nÃ£o recebem zero ÃƒÂ  esquerda
    if re.match(r"^\d+$", sku_txt) and len(sku_txt) < 2:
        sku_txt = sku_txt.zfill(3)
    return sku_txt


# Favoritos core helpers lives in backend.services.favoritos_core.
from backend.services import favoritos_core as _favoritos_core_module
_favoritos_core_module.configure_favoritos_core_runtime(sys.modules[__name__])
globals().update({name: getattr(_favoritos_core_module, name) for name in _favoritos_core_module.__all__})


# Admin Usuarios service logic lives in backend.services.admin_usuarios_* .
from backend.services import admin_usuarios as _admin_usuarios_module
_admin_usuarios_module.configure_admin_usuarios_runtime(sys.modules[__name__])
globals().update({
    name: getattr(_admin_usuarios_module, name)
    for name in _admin_usuarios_module.__all__
    if name != "configure_admin_usuarios_runtime" and hasattr(_admin_usuarios_module, name)
})







# IA service logic lives in backend.services.ia.
from backend.services import ia as _ia_module
_ia_module.configure_ia_runtime(sys.modules[__name__])
globals().update({name: getattr(_ia_module, name) for name in _ia_module.__all__ if hasattr(_ia_module, name)})
configure_configuracoes_context(
    arquivo_config_globais=ARQUIVO_CONFIG_GLOBAIS,
    logger=logger,
    openai_api_key=_ia_module._obter_openai_api_key,
    deepseek_api_key=_ia_module._obter_deepseek_api_key,
    gemini_api_key=_ia_module._obter_gemini_api_key,
    agent_api_key=_ia_module._vertex_ai_agent_api_key,
    env_texto=_env_texto,
    firebase_deve_usar=_firebase_deve_usar,
    firebase_db=_firebase_db,
    firebase_access_obrigatorio=_firebase_access_obrigatorio,
)

# Perguntas e pos-venda core helpers lives in backend.services.perguntas_pos_venda_core.
from backend.services import perguntas_pos_venda_core as _perguntas_pos_venda_core_module
_perguntas_pos_venda_core_module.configure_perguntas_pos_venda_core_runtime(sys.modules[__name__])
globals().update({name: getattr(_perguntas_pos_venda_core_module, name) for name in _perguntas_pos_venda_core_module.__all__})















async def get_tenant_id(request: Request, authorization: Optional[str] = Header(default=None)):
    """Extrai o client_id do JWT e valida a permissÃƒÂ£o do mÃƒÂ³dulo acessado."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token de autenticaÃ§Ã£o ausente. FaÃ§a o login novamente.", headers={"WWW-Authenticate": "Bearer"})
    token = authorization[len("Bearer "):]
    try:
        payload = decodificar_access_token(token)
        username: str = payload.get("sub")
        client_id: str = payload.get("client_id")
        if not client_id:
            raise HTTPException(status_code=401, detail="Token invalido: client_id ausente.")
        if not username:
            raise HTTPException(status_code=401, detail="Token invalido: usuÃƒÂ¡rio ausente.")

        permissao_necessaria = _permissao_exigida_por_rota(getattr(request.url, "path", ""), request.method)
        if permissao_necessaria:
            permissoes = await asyncio.to_thread(_carregar_permissoes_usuario, username, client_id)
            if not _permissoes_autorizam_rota(permissoes, permissao_necessaria):
                permissoes_rotulo = " ou ".join(_normalizar_permissoes_exigidas(permissao_necessaria))
                logger.warning(f"[AUTH] Acesso negado para usuÃƒÂ¡rio='{username}' em rota='{request.url.path}' (permissÃƒÂ£o requerida: {permissoes_rotulo})")
                raise HTTPException(
                    status_code=403,
                    detail=f"Acesso negado: usuÃƒÂ¡rio sem permissÃƒÂ£o para o mÃƒÂ³dulo '{permissoes_rotulo}'."
                )

        await asyncio.to_thread(
            _machine_presence_auto_touch,
            username,
            client_id,
            request,
            str(payload.get("machine_id") or "").strip(),
        )
        request.state.username = username
        request.state.client_id = client_id
        request.state.auth_payload = dict(payload)
        return client_id
    except JWTError:
        raise HTTPException(
            status_code=401,
            detail="SessÃ£o expirada ou invÃ¡lida. FaÃ§a o login novamente.",
            headers={"WWW-Authenticate": "Bearer"},
        )






# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# IA Ã¢â‚¬â€ Endpoints de PersistÃƒÂªncia de Conversas
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â



# Bling/vendas/notas helpers live in backend.services.bling_vendas.
from backend.services.bling_vendas import (
    configure_bling_vendas_context,
    _bling_refresh_token,
    _bling_marcar_oauth_invalido,
    _bling_salvar_oauth_valido,
    _bling_renovar_token_loja,
    _bling_executar_com_refresh,
    _bling_listar_produtos,
    _bling_obter_ncm_cest_produto,
    _carregar_mapeamento_unidades,
    _salvar_mapeamento_unidades,
    _bling_buscar_unidade_negocio,
    _bling_map_canais_venda_basico,
    _bling_map_unidades_por_canais,
    _bling_map_lojas_virtuais,
    _normalizar_nome_loja_virtual_candidato,
    _remover_prefixo_unidade_nome,
    _normalizar_cnpj,
    _carregar_mapeamento_lojas_virtuais_cliente,
    _resolver_nome_loja_virtual,
    _normalizar_unidade_negocio_ml,
    _normalizar_unidade_devolucao_entrada,
    _eh_unidade_sintetica_sistema,
    _eh_devolucao_nota_entrada,
    _eh_devolucao_por_cfop_itens,
    _tipo_devolucao_cfop_full_estoque,
    _devolucao_deve_ir_para_ml_full,
    _classificar_unidade_virtual_devolucao,
    _sql_filtro_unidade_devolucao,
    _sql_filtro_loja_notas_entrada,
    _bling_obter_numero_nf,
    _bling_map_depositos,
    _bling_saldos,
    _variacoes_nome_loja,
    _variacoes_nome_loja_nocase,
    _sql_match_variacoes,
    _sql_filtro_unidade_com_mapa,
    _sql_filtro_unidade_vendas,
    _sql_filtro_loja_vendas,
    _slug_loja_para_arquivo,
    _get_vendas_db_path,
    _listar_bancos_vendas_tenant,
    _chave_deduplicacao_venda,
    _deduplicar_vendas_consolidadas,
    _deve_excluir_venda_ebazar,
    _get_vendas_db,
    _bling_listar_vendas,
    _bling_listar_naturezas,
    _normalizar_texto,
    _bling_obter_detalhes_nf,
    _extrair_codigo_origem_nf,
    _bling_listar_notas_entrada,
    _bling_listar_vendas_fallback_nf_saida,
    _get_notas_entrada_db,
)





































































































































































































USER_CHAT_TYPING_TTL_SECONDS = 6
USER_CHAT_ATTACHMENT_MAX_COUNT = 6
USER_CHAT_ATTACHMENT_MAX_BYTES = 700 * 1024
USER_CHAT_ATTACHMENT_TOTAL_MAX_BYTES = 900 * 1024
USER_CHAT_REMOTE_HISTORY_CHECK_TTL_SECONDS = 10 * 60
USER_CHAT_REMOTE_HISTORY_CHECK_CACHE: dict[str, int] = {}





























































































































































































































































































































































































































































































































































































































































































































































_admin_usuarios_module.configure_admin_usuarios_runtime(sys.modules[__name__])
app.include_router(create_admin_usuarios_router())

# WhatsApp Cloud API bridge: the public gateway stays at Cloudflare, while all
# Joao Pretinho processing remains on this authenticated local runtime.
from backend.services import whatsapp_bridge as _whatsapp_bridge_module
_whatsapp_bridge_iniciar_background = _whatsapp_bridge_module.whatsapp_bridge_iniciar_background
_whatsapp_bridge_parar_background = _whatsapp_bridge_module.whatsapp_bridge_parar_background
app.include_router(create_whatsapp_bridge_router())

# Promocoes service logic lives in backend.services.promocoes_*.
from backend.services import promocoes_common as _promocoes_common_module
from backend.services import promocoes_core as _promocoes_core_module
from backend.services import promocoes_api as _promocoes_api_module

for _promocoes_module in (_promocoes_common_module, _promocoes_core_module, _promocoes_api_module):
    _configure = getattr(_promocoes_module, f"configure_{_promocoes_module.__name__.rsplit(chr(46), 1)[-1]}_runtime", None)
    if callable(_configure):
        _configure(sys.modules[__name__])
    globals().update({name: getattr(_promocoes_module, name) for name in getattr(_promocoes_module, "__all__", ())})
del _promocoes_module, _configure

app.include_router(create_promocoes_router())

# Renovacao service logic lives in backend.services.renovacao.

configure_renovacao_context(
    RenovacaoServiceConfig(
        logger=logger,
        pasta_info=PASTA_INFO,
        get_tenant_path=lambda client_id: get_tenant_path(client_id),
        agent_service_only=_agent_service_only,
        obter_cfg_ml=_obter_cfg_ml,
        ml_api_request=_ml_api_request,
        ml_parse_error_detail=_ml_parse_error_detail,
        ml_extrair_contagem_campanha=_ml_extrair_contagem_campanha,
        ml_contar_itens_promocao_status=_ml_contar_itens_promocao_status,
        ml_extrair_preco_promocao_raw=_ml_extrair_preco_promocao_raw,
        parse_float_flex=_parse_float_flex,
        ml_listar_ids_anuncios_ativos=_ml_listar_ids_anuncios_ativos,
        ml_obter_promocoes_item=_ml_obter_promocoes_item,
        ml_extrair_ids_promocoes_item=_ml_extrair_ids_promocoes_item,
        ml_encontrar_promocao_raw_item=_ml_encontrar_promocao_raw_item,
        ml_listar_itens_promocao_com_raw=_ml_listar_itens_promocao_com_raw,
        ml_buscar_itens_batch=_ml_buscar_itens_batch,
        ml_obter_item_promocao_raw=_ml_obter_item_promocao_raw,
        cache_invalidar_loja=_cache_invalidar_loja,
    )
)

_renovacao_router_config = RenovacaoRouterConfig(
    get_tenant_id=get_tenant_id,
    listar_campanhas_usuario=_renovacao_listar_campanhas_usuario_payload,
    criar_ou_completar_proximo_mes=_renovacao_criar_ou_completar_proximo_mes,
    criar_campanha_manual=_renovacao_criar_campanha_manual_ml,
    atualizar_periodo_campanha=_renovacao_atualizar_periodo_campanha_ml,
    deletar_campanha=_renovacao_deletar_campanha_ml,
    sincronizar_promocao_existente=_renovacao_sincronizar_promocao_existente,
    iniciar_sincronizacao_promocao=_renovacao_sincronizar_promocao_iniciar_payload,
    sync_job_get=_renovacao_sync_job_get,
    agendamento_atual=_renovacao_agendamento_atual,
    agendamento_put=_renovacao_agendamento_put_payload,
)
app.include_router(create_renovacao_router(_renovacao_router_config))


# --- ENDPOINTS DA API ---

# --- ENDPOINTS DE INTEGRAÃƒÆ’Ã¢â‚¬Â¡ÃƒÆ’Ã†â€™O ---

# --- HELPER FUNCTIONS PARA TENANT E LOJAS ---

def get_tenant_path(client_id: str):
    """Retorna o caminho para a pasta de dados do cliente e a cria se nÃ£o existir."""
    tenant_path = os.path.join(PASTA_INFO, client_id)
    if not os.path.exists(tenant_path):
        os.makedirs(tenant_path, exist_ok=True)
    return tenant_path


# Context Hub uses the authenticated tenant at the HTTP boundary. Startup only
# primes explicitly allowlisted tenants; every other tenant is initialized
# lazily after a full-admin request.
from backend.services import context_hub as _context_hub_module

_CONTEXT_HUB_SURFACE = (
    os.getenv("JK_CONTEXT_HUB_SURFACE") or "development"
).strip().lower()
_CONTEXT_HUB_RUNTIME_CONFIG = _context_hub_module.configure_context_hub(
    base_dir=BASE_DIR,
    info_root=PASTA_INFO,
    surface=_CONTEXT_HUB_SURFACE,
)
app.include_router(create_context_hub_router())


def _context_hub_clientes_iniciais() -> tuple[str, ...]:
    raw = os.getenv("JK_CONTEXT_HUB_BOOTSTRAP_CLIENTS", "000002")
    clients: list[str] = []
    for value in re.split(r"[,;\s]+", raw or ""):
        client_id = value.strip()
        if client_id and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", client_id):
            clients.append(client_id)
    return tuple(dict.fromkeys(clients))


def _context_hub_iniciar_background() -> None:
    def _worker() -> None:
        for client_id in _context_hub_clientes_iniciais():
            try:
                _context_hub_module.rebuild_context(
                    client_id,
                    base_dir=_CONTEXT_HUB_RUNTIME_CONFIG.base_dir,
                    info_root=_CONTEXT_HUB_RUNTIME_CONFIG.info_root,
                    surface=_CONTEXT_HUB_RUNTIME_CONFIG.surface,
                    reason=(
                        "installed_startup"
                        if _CONTEXT_HUB_SURFACE == "installed"
                        else "development_startup"
                    ),
                )
                if _CONTEXT_HUB_SURFACE == "development":
                    _context_hub_module.start_context_hub_watcher(
                        client_id,
                        base_dir=_CONTEXT_HUB_RUNTIME_CONFIG.base_dir,
                        info_root=_CONTEXT_HUB_RUNTIME_CONFIG.info_root,
                        surface=_CONTEXT_HUB_RUNTIME_CONFIG.surface,
                    )
            except Exception as exc:
                logger.warning(
                    "Context Hub startup bloqueado para tenant %s (%s).",
                    client_id,
                    type(exc).__name__,
                )

    threading.Thread(
        target=_worker,
        name="jk-context-hub-startup",
        daemon=True,
    ).start()


def _context_hub_parar_background() -> None:
    _context_hub_module.stop_all_context_hub_watchers()


# O Shared Sync depende de get_tenant_path e, por isso, so pode configurar o
# runtime depois que todos os helpers de tenant estiverem definidos.
from backend.services import shared_sync as _shared_sync_module
_shared_sync_module.configure_shared_sync_runtime(sys.modules[__name__])
globals().update({
    name: getattr(_shared_sync_module, name)
    for name in _shared_sync_module.__all__
    if name != "configure_shared_sync_runtime" and hasattr(_shared_sync_module, name)
})
app.include_router(create_shared_sync_router())


configure_bling_vendas_context(
    pasta_info=PASTA_INFO,
    get_tenant_path=get_tenant_path,
    atualizar_api_loja=atualizar_api_loja,
    propagar_lojas_integracoes_cliente=_shared_sync_propagar_lojas_integracoes_cliente,
    logger_instance=logger,
)
configure_configuracoes_drive_sync_context(
    logger_ref=logger,
    payload_sessao_por_authorization=_payload_sessao_por_authorization,
    carregar_usuarios_sheets_fn=carregar_usuarios_sheets,
    normalizar_email_fn=_normalizar_email,
    google_oauth_carregar_tokens=_google_oauth_carregar_tokens,
    google_oauth_salvar_tokens_usuario=_google_oauth_salvar_tokens_usuario,
    google_oauth_parse_expiry=_google_oauth_parse_expiry,
    google_login_client_id=_google_login_client_id,
    google_login_client_secret=_google_login_client_secret,
    get_tenant_path_fn=get_tenant_path,
)
configure_full_context(get_tenant_path=get_tenant_path)
configure_favoritos_context(
    get_tenant_path=get_tenant_path,
    logger=logger,
    chave_loja_favoritos=_chave_loja_favoritos,
)


def _migrar_arquivo_legado_para_tenant(client_id: str, nome_arquivo: str, caminho_legado: str):
    """Move arquivo legado da raiz info/ para info/<client_id>/ quando necessÃƒÂ¡rio."""
    tenant_path = get_tenant_path(client_id)
    destino = os.path.join(tenant_path, nome_arquivo)
    if os.path.exists(destino):
        return destino
    if not (caminho_legado and os.path.exists(caminho_legado)):
        return destino

    try:
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        shutil.move(caminho_legado, destino)
        logger.info(f"[MIGRACAO] {nome_arquivo} movido para tenant {client_id}")
    except Exception as e:
        # Fallback seguro para ambientes com lock no arquivo legado.
        try:
            shutil.copy2(caminho_legado, destino)
            logger.warning(f"[MIGRACAO] {nome_arquivo} copiado para tenant {client_id} (origem preservada): {e}")
        except Exception as e2:
            logger.warning(f"[MIGRACAO] Falha ao migrar {nome_arquivo} para tenant {client_id}: {e2}")
    return destino


# Integracoes service/router wiring. Legacy public helpers are imported from backend.services.integracoes.
configure_integracoes_context(
    logger_ref=logger,
    pasta_info=PASTA_INFO,
    get_tenant_path=get_tenant_path,
    normalizar_integracao_conectada=lambda servico, dados: _shared_sync_normalizar_integracao_conectada(servico, dados),
    resolver_redirect_uri_publica=_resolver_redirect_uri_publica,
    resolver_redirect_uri_bling=_resolver_redirect_uri_bling,
    bling_session=BLING_SESSION,
)
app.include_router(create_integracoes_router(IntegracoesRouterConfig(
    get_tenant_id=get_tenant_id,
    logger=logger,
    resolver_redirect_uri_publica=_resolver_redirect_uri_publica,
    resolver_redirect_uri_bling=_resolver_redirect_uri_bling,
    shared_sync_propagar_lojas_integracoes_cliente=lambda client_id, machine_id: _shared_sync_propagar_lojas_integracoes_cliente(client_id, machine_id),
)))










































# Continued Perguntas e pos-venda core helpers lives in backend.services.perguntas_pos_venda_core.




# Refresh extracted legacy modules after late backend_api helpers are defined.
_promocoes_common_module.configure_promocoes_common_runtime(sys.modules[__name__])
_promocoes_core_module.configure_promocoes_core_runtime(sys.modules[__name__])
_promocoes_api_module.configure_promocoes_api_runtime(sys.modules[__name__])
_mercadolivre_legacy_core_module.configure_mercadolivre_legacy_core_runtime(sys.modules[__name__])
_favoritos_core_module.configure_favoritos_core_runtime(sys.modules[__name__])
_perguntas_pos_venda_core_module.configure_perguntas_pos_venda_core_runtime(sys.modules[__name__])
perguntas_pos_venda_codex_service.configure_perguntas_pos_venda_codex_runtime(sys.modules[__name__])
perguntas_pos_venda_endpoint_service.configure_perguntas_pos_venda_endpoints_runtime(sys.modules[__name__])
app.include_router(create_perguntas_pos_venda_router())




























configure_mercado_livre_context(MercadoLivreServiceConfig(
    logger=logger,
    obter_cfg_ml=_obter_cfg_ml,
    ml_api_request=_ml_api_request,
    ml_api_request_com_retry=_ml_api_request_com_retry,
    ml_parse_error_detail=_ml_parse_error_detail,
    ml_buscar_itens_batch=_ml_buscar_itens_batch,
    ml_montar_detalhe_anuncio_listagem=_ml_montar_detalhe_anuncio_listagem,
    ml_obter_preco_detalhado=_ml_obter_preco_detalhado,
    ml_obter_frete_detalhado=_ml_obter_frete_detalhado,
    ml_obter_taxas_anuncio=_ml_obter_taxas_anuncio,
    ml_estimar_taxa_fixa_por_preco=_ml_estimar_taxa_fixa_por_preco,
    parse_float_flex=_parse_float_flex,
    formatar_moeda_br=formatar_moeda_br,
    normalizar_texto=normalizar_texto,
))
_mercado_livre_router_config = MercadoLivreRouterConfig(get_tenant_id=get_tenant_id)
app.include_router(create_mercado_livre_router(_mercado_livre_router_config))




# Continued Favoritos core helpers lives in backend.services.favoritos_core.

def _enriquecer_link_html(url: str, max_retries: int = 1, delay: int = 1):
    for tentativa in range(max_retries):
        try:
            resp = requests.get(url, headers=_ml_headers(), timeout=10, verify=False)
            if resp.status_code != 200:
                time.sleep(delay)
                continue
            
            soup = BeautifulSoup(resp.text, "lxml")
            
            titulo = None
            meta_title = soup.find("meta", {"property": "og:title"})
            if meta_title and meta_title.get("content"):
                titulo = meta_title.get("content").strip()
            
            preco = None
            meta_price = soup.find("meta", {"property": "product:price:amount"})
            if meta_price and meta_price.get("content"):
                preco = meta_price.get("content").strip()
            
            vendas = None
            
            return {
                "url": url,
                "titulo": titulo or "",
                "preco": preco or "",
                "vendas": vendas
            }
        except Exception:
            time.sleep(delay)
    
    return {"url": url, "titulo": "", "preco": "", "vendas": None}


favoritos_endpoint_service.configure_favoritos_endpoints_runtime(sys.modules[__name__])
app.include_router(create_favoritos_router())

# --- ENDPOINTS ETIQUETAS ---









_etiquetas_router_config = EtiquetasRouterConfig(
    get_tenant_id=get_tenant_id,
    temp_files_storage=TEMP_FILES_STORAGE,
)
app.include_router(create_etiquetas_router(_etiquetas_router_config))


# Refresh IA service after tenant/runtime helpers are available.
_ia_module.configure_ia_runtime(sys.modules[__name__])
globals().update({name: getattr(_ia_module, name) for name in _ia_module.__all__ if hasattr(_ia_module, name)})

_configuracoes_router_config = ConfiguracoesRouterConfig(
    get_tenant_id=get_tenant_id,
    carregar_configuracoes_globais=_carregar_configuracoes_globais,
    salvar_configuracoes_globais=_salvar_configuracoes_globais,
    normalizar_ia_modelo_padrao=_normalizar_ia_modelo_padrao,
    normalizar_ia_modo=_normalizar_ia_modo,
    vertex_modelo_nome_curto=_vertex_modelo_nome_curto,
    salvar_ia_provider_api_key=_salvar_ia_provider_api_key,
    salvar_vertex_agent_api_key=_salvar_vertex_agent_api_key,
    ia_secrets_publicar_no_provisionador_se_configurado=_ia_secrets_publicar_no_provisionador_se_configurado,
)
app.include_router(create_configuracoes_router(_configuracoes_router_config))


# Sala de Reuniao service logic lives in backend.services.sala_reuniao.
from backend.services import sala_reuniao as _sala_reuniao_module
_sala_reuniao_module.configure_sala_reuniao_runtime(sys.modules[__name__])
globals().update({name: getattr(_sala_reuniao_module, name) for name in _sala_reuniao_module.__all__})
app.include_router(create_sala_reuniao_router())

# Impostos service logic lives in backend.services.impostos.
from backend.services import impostos as _impostos_module
_impostos_module.configure_impostos_runtime(sys.modules[__name__])
globals().update({name: getattr(_impostos_module, name) for name in _impostos_module.__all__ if hasattr(_impostos_module, name)})
app.include_router(create_impostos_router())

# --- ENDPOINTS ESTOQUE (MIGRAÃƒÆ’Ã¢â‚¬Â¡ÃƒÆ’Ã†â€™O) ---

# Estoque service logic lives in backend.services.estoque_*.
from backend.services import estoque as _estoque_module
_estoque_module.configure_estoque_runtime(sys.modules[__name__])
globals().update({name: getattr(_estoque_module, name) for name in _estoque_module.__all__ if hasattr(_estoque_module, name)})



# Full service logic lives in backend.services.full_*.
configure_full_calendario_context(
    logger_ref=logger,
    env_config_bool=_env_config_bool,
    cache_get=_ml_cache_get,
    cache_set=_ml_cache_set,
)
configure_full_mercadolivre_context(
    logger_ref=logger,
    carregar_lojas_fn=lambda client_id: carregar_lojas(client_id),
    ml_oauth_status=_ml_oauth_status,
    ml_cache_get=_ml_cache_get,
    ml_cache_set=_ml_cache_set,
    ml_favoritos_api_request=_ml_favoritos_api_request,
    ml_parse_error_detail=_ml_parse_error_detail,
    ml_extrair_variacoes_resumo=_ml_extrair_variacoes_resumo,
    ml_extrair_sku=_ml_extrair_sku,
    obter_cfg_ml=_obter_cfg_ml,
    ml_favoritos_listar_todos_itens_ativos_loja=_ml_favoritos_listar_todos_itens_ativos_loja,
)
app.include_router(create_full_router(FullRouterConfig(
    get_tenant_id=get_tenant_id,
    listar_estoque=listar_estoque,
    logger=logger,
)))
app.include_router(create_estoque_router())



# Cadastro service logic lives in backend.services.cadastro.
from backend.services import cadastro as _cadastro_module
_cadastro_module.configure_cadastro_runtime(sys.modules[__name__])
globals().update({name: getattr(_cadastro_module, name) for name in _cadastro_module.__all__})


# --- ENDPOINTS VENDAS (LISTAGEM) ---

# Vendas is composed explicitly; legacy import paths delegate to this instance.
_vendas_paths = AppPaths.create(base_dir=BASE_DIR, info_dir=PASTA_INFO, logger=logger)
_vendas_domain = install_default_vendas_module(create_vendas_module(VendasModuleDependencies(
    get_tenant_id=get_tenant_id,
    paths=_vendas_paths,
    logger=logger,
    legacy=LegacyBlingVendasAdapter(),
    max_active_sync=2,
)))
app.include_router(_vendas_domain.router)

# Medias Compras endpoints live in backend.services.medias_compras.
from backend.services import medias_compras as _medias_compras_module
_medias_compras_module.configure_medias_compras_runtime(sys.modules[__name__])
globals().update({
    name: getattr(_medias_compras_module, name)
    for name in _medias_compras_module.__all__
    if hasattr(_medias_compras_module, name) and name not in {"PASTA_INFO", "get_tenant_path"}
})
app.include_router(create_medias_compras_router())
app.include_router(create_importacoes_router(sys.modules[__name__]))

_frontend_router_config = FrontendRouterConfig(
    base_dir=BASE_DIR,
    static_dir="static",
    img_dir=PASTA_IMG,
    chrome_extensions_dir="extensoes_chrome",
)
app.include_router(create_frontend_router(_frontend_router_config))





app.include_router(create_cadastro_router())


_ia_module.configure_ia_runtime(sys.modules[__name__])
globals().update({name: getattr(_ia_module, name) for name in _ia_module.__all__ if hasattr(_ia_module, name)})
app.include_router(create_ia_router())


def _codex_console_recuperar_fila_background():
    from backend.services import codex_console as _codex_console_service

    return _codex_console_service.codex_console_recuperar_fila_background()


# --- ARQUIVOS ESTÃƒÆ’Ã‚ÂTICOS (FRONTEND) ---
register_startup_events(
    app,
    sys.modules[__name__],
    extra_handlers=(_vendas_domain.prepare_databases, _context_hub_iniciar_background),
    extra_shutdown_handlers=(_context_hub_parar_background,),
)
mount_static_assets(app, _frontend_router_config)
