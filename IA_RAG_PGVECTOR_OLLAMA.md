# IA com contexto local, PostgreSQL + pgvector opcional

## Modo recomendado: local embutido

O sistema agora consegue rodar o RAG local sem instalar PostgreSQL, Docker ou Ollama.
O índice fica em SQLite dentro da pasta do cliente:

```text
info/<client_id>/ia_rag_local.db
```

Fluxo local:

```text
App
-> backend FastAPI
-> SQLite local com vetores por hashing
-> busca local retorna contexto
-> IA responde usando contexto da tela + contexto recuperado
```

Configure:

```env
IA_RAG_ENABLED=true
IA_RAG_BACKEND=local
IA_RAG_TOP_K=5
```

O cliente desktop instalado já inicia o backend com `IA_RAG_BACKEND=local`, então outros computadores não precisam instalar serviços extras para usar o RAG local.

Endpoints:

```http
GET /api/ia/rag/status
POST /api/ia/rag/reindexar
POST /api/ia/rag/indexar
```

Quando o RAG local estiver ativo, `/api/ia/chat` recupera contexto do arquivo `ia_rag_local.db`.
Se o índice ainda estiver vazio, a IA continua funcionando com o contexto da tela e demais funções internas.

## Modo opcional: PostgreSQL + pgvector + Ollama

Esse modo continua disponível para Cloud SQL ou para instalações locais que já usam PostgreSQL.

Fluxo PostgreSQL:

```text
App
-> backend FastAPI
-> PostgreSQL com pgvector
-> Ollama gera embeddings locais
-> busca semantica retorna contexto
-> IA responde usando contexto da tela + contexto recuperado
```

## 1. Instalar dependencias Python

```powershell
pip install -r requirements.txt
```

## 2. Subir PostgreSQL com pgvector

Exemplo com Docker:

```powershell
docker run --name jk-pgvector `
  -e POSTGRES_PASSWORD=5627 `
  -e POSTGRES_DB=jk_ia `
  -p 55432:5432 `
  -d pgvector/pgvector:pg18-trixie
```

## 3. Preparar Ollama

Instale o Ollama e baixe um modelo de embedding:

```powershell
ollama pull nomic-embed-text
```

O backend usa `http://127.0.0.1:11434` por padrao.

## 4. Configurar o `.env`

Adicione:

```env
IA_RAG_ENABLED=true
IA_RAG_BACKEND=postgres
IA_VECTOR_DATABASE_URL=postgresql://postgres:5627@127.0.0.1:55432/jk_ia
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_EMBED_MODEL=nomic-embed-text
IA_RAG_TOP_K=5
```

Mantenha tambem:

```env
OPENAI_API_KEY=sua_chave
OPENAI_MODEL=gpt-5.4-nano
```

## 5. Endpoints criados

Status:

```http
GET /api/ia/rag/status
```

Indexar documentos:

```http
POST /api/ia/rag/indexar
```

Payload:

```json
{
  "documents": [
    {
      "title": "Politica de estoque",
      "source": "manual",
      "content": "Texto que a IA deve recuperar depois.",
      "metadata": {
        "modulo": "estoque"
      }
    }
  ]
}
```

Quando o RAG PostgreSQL estiver ativo, o endpoint atual `/api/ia/chat` busca contexto semanticamente antes de chamar a IA. Se PostgreSQL/Ollama falharem, o chat continua funcionando sem contexto recuperado e registra o erro no log.
