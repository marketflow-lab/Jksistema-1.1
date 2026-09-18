"""Prevent operational modules from silently reintroducing writer-maintenance reads."""
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_OWNERS = {
    "backend/services/integracoes.py",
    "backend/services/integracoes_api.py",
    "backend/services/store_listing_service.py",
    "backend/services/store_oauth_refresh.py",
    "backend/services/shared_sync_bundle.py",
    "backend/services/shared_sync_apply_scope.py",
    "backend/services/cadastro_lojas_produtos.py",
    "backend/services/cadastro_importacao.py",
}


def test_operational_modules_do_not_import_or_call_maintenance_loader():
    violations = []
    for path in [ROOT / "backend_api.py", *(ROOT / "backend").rglob("*.py")]:
        relative = path.relative_to(ROOT).as_posix()
        if relative in CANONICAL_OWNERS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "backend.services.integracoes":
                if any(alias.name in {"carregar_lojas", "buscar_loja"} for alias in node.names):
                    violations.append((relative, node.lineno))
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in {"integracoes", "integracoes_service"}
                    and node.func.attr in {"carregar_lojas", "buscar_loja"}):
                violations.append((relative, node.lineno))
    assert violations == []
