@echo off
REM =====================================================
REM Sincronizar arquivos HTML da raiz para /static
REM =====================================================

echo.
echo Sincronizando arquivos HTML...
echo.

REM Lista de arquivos HTML para sincronizar
setlocal enabledelayedexpansion
set arquivos=^
    vendas.html ^
    vendas_sku.html ^
    devolucoes.html ^
    devolucoes_sku.html ^
    estoque.html ^
    renovacao.html ^
    integracoes.html ^
    promo.html ^
    frontend_promo.html ^
    frontend_etiquetas.html ^
    debug_vendas.html ^
    dashboard.html

for %%f in (%arquivos%) do (
    if exist "%%f" (
        copy "%%f" "static\%%f" /Y > nul
        echo ✓ %%f
    ) else (
        echo ✗ %%f (nao encontrado)
    )
)

echo.
echo Sincronizacao concluida!
echo.
