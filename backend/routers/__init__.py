"""FastAPI routers grouped by application module."""

from .admin_usuarios import create_admin_usuarios_router
from .cadastro import create_cadastro_router
from .codex_console import create_codex_console_router
from .configuracoes import ConfiguracoesRouterConfig, create_configuracoes_router
from .estoque import create_estoque_router
from .etiquetas import EtiquetasRouterConfig, create_etiquetas_router
from .favoritos import create_favoritos_router
from .frontend import FrontendRouterConfig, create_frontend_router, mount_static_assets
from .full import FullRouterConfig, create_full_router
from .ia import create_ia_router
from .impostos import create_impostos_router
from .importacoes import create_importacoes_router
from .infra import InfraRouterConfig, create_infra_router
from .integracoes import IntegracoesRouterConfig, create_integracoes_router
from .mercado_livre import MercadoLivreRouterConfig, create_mercado_livre_router
from .medias_compras import create_medias_compras_router
from .perguntas_pos_venda import create_perguntas_pos_venda_router
from .promocoes import create_promocoes_router
from .renovacao import RenovacaoRouterConfig, create_renovacao_router
from .registry import include_feature_routers
from .sala_reuniao import create_sala_reuniao_router
from .shared_sync import create_shared_sync_router
from .vendas import create_vendas_router

__all__ = [
    "FrontendRouterConfig",
    "ConfiguracoesRouterConfig",
    "FullRouterConfig",
    "InfraRouterConfig",
    "IntegracoesRouterConfig",
    "MercadoLivreRouterConfig",
    "RenovacaoRouterConfig",
    "create_admin_usuarios_router",
    "create_cadastro_router",
    "create_codex_console_router",
    "create_configuracoes_router",
    "create_estoque_router",
    "EtiquetasRouterConfig",
    "create_etiquetas_router",
    "create_favoritos_router",
    "create_frontend_router",
    "create_full_router",
    "create_ia_router",
    "create_impostos_router",
    "create_importacoes_router",
    "create_infra_router",
    "create_integracoes_router",
    "create_mercado_livre_router",
    "create_medias_compras_router",
    "create_perguntas_pos_venda_router",
    "create_promocoes_router",
    "create_renovacao_router",
    "create_sala_reuniao_router",
    "create_shared_sync_router",
    "create_vendas_router",
    "include_feature_routers",
    "mount_static_assets",
]
