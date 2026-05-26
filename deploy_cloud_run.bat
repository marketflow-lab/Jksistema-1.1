@echo off
setlocal

set PROJECT_ID=jk-sistema-prod-20260525
set REGION=southamerica-east1
set SERVICE=jk-sistema-api
set SQL_INSTANCE=jk-sistema-postgres
set INFO_BUCKET=jk-sistema-prod-20260525-info
set RUNTIME_SERVICE_ACCOUNT=1077918177671-compute@developer.gserviceaccount.com
set IMAGE=southamerica-east1-docker.pkg.dev/%PROJECT_ID%/jk-sistema/%SERVICE%:latest
set SERVICE_URL=https://jk-sistema-api-1077918177671.southamerica-east1.run.app

rem Necessario nesta maquina por erro local de certificado do gcloud.
set CLOUDSDK_AUTH_DISABLE_SSL_VALIDATION=true

call gcloud.cmd config set project %PROJECT_ID% || exit /b 1

call gcloud.cmd builds submit . ^
  --tag=%IMAGE% ^
  --region %REGION% ^
  --project %PROJECT_ID% || exit /b 1

call gcloud.cmd run deploy %SERVICE% ^
  --image=%IMAGE% ^
  --project %PROJECT_ID% ^
  --region %REGION% ^
  --allow-unauthenticated ^
  --service-account %RUNTIME_SERVICE_ACCOUNT% ^
  --add-cloudsql-instances %PROJECT_ID%:%REGION%:%SQL_INSTANCE% ^
  --add-volume name=jk-info,type=cloud-storage,bucket=%INFO_BUCKET% ^
  --add-volume-mount volume=jk-info,mount-path=/mnt/jk-info ^
  --memory 8Gi ^
  --cpu 4 ^
  --timeout 900 ^
  --concurrency 20 ^
  --set-env-vars JK_INFO_DIR=/mnt/jk-info,JK_AUTO_NGROK_REDIRECT=false,JK_REDIRECT_URI=%SERVICE_URL%/auth/callback,IA_RAG_ENABLED=false,IA_WEB_SEARCH_ENABLED=true,ML_VERIFY_SSL=false,BLING_VERIFY_SSL=false,VERTEX_AI_PROJECT_ID=%PROJECT_ID%,VERTEX_AI_LOCATION=us-central1 ^
  --update-secrets DATABASE_URL=jk-database-url:latest,IA_VECTOR_DATABASE_URL=jk-database-url:latest,OPENAI_API_KEY=jk-openai-api-key:latest,GEMINI_API_KEY=jk-gemini-api-key:latest,DEEPSEEK_API_KEY=jk-deepseek-api-key:latest

if errorlevel 1 exit /b %errorlevel%

echo.
echo Deploy concluido. Copie a URL acima e configure o callback do Mercado Livre:
echo %SERVICE_URL%/auth/callback
