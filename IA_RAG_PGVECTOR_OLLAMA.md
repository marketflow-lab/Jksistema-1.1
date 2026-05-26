# IA com contexto via PostgreSQL + pgvector + Ollama

Fluxo implementado:

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

Quando o RAG estiver ativo, o endpoint atual `/api/ia/chat` busca contexto semanticamente antes de chamar a IA. Se PostgreSQL/Ollama falharem, o chat continua funcionando sem contexto recuperado e registra o erro no log.
