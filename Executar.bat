@echo off
TITLE JK Sistema de Gestao Launcher
echo Iniciando o sistema JK...

:: 1. Garante que o diretório de trabalho é a pasta do arquivo
cd /d "%~dp0"

:: 2. Inicia o Streamlit minimizado apontando para app.py
:: O parametro --server.headless true evita que o streamlit tente abrir o navegador padrão sozinho
start /min cmd /c "python -m streamlit run app.py --server.headless true"

:: 3. Espera 4 segundos para o servidor subir
timeout /t 4 >nul

:: 4. Abre o navegador em modo APLICATIVO
:: Se preferir Chrome, mude 'msedge' para 'chrome'
start msedge --new-window --app=http://localhost:8501

exit 