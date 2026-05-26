# 📁 Estrutura de Armazenamento por Cliente

## ✅ Sim! O Sistema Está Funcionando Corretamente

O sistema **JAÁ salva os bancos de dados em pastas separadas por número do cliente** (client_id), conforme configurado no backend.

---

## 📊 Estrutura Atual

```
info/
├── bling_conf.json                    ← Config global Bling
├── bling_contas.json                  ← Contas Bling (LEGADO)
├── credentials.json                   ← Google Sheets (LEGADO)
├── integracoes.json                   ← Integrações (LEGADO)
├── lojas_config.json                  ← Lojas (LEGADO)
├── vendas_historico.db                ← DB Vendas (LEGADO)
├── 
├── 000001/                            ← CLIENTE 1 (client_id = "000001")
│   ├── vendas_historico.db            ← Vendas deste cliente
│   ├── lojas_config.json              ← Lojas deste cliente
│   └── [outros arquivos...]
│
├── 000002/                            ← CLIENTE 2 (client_id = "000002")
│   ├── vendas_historico.db            ← Vendas deste cliente
│   ├── lojas_config.json              ← Lojas deste cliente
│   └── [outros arquivos...]
│
└── [NOVO-CLIENTE]/                    ← Cada novo cliente ✨
    ├── vendas_historico.db            ← Seu banco de dados
    ├── lojas_config.json              ← Suas lojas
    └── [outros arquivos...]
```

---

## 🔍 Como Funciona o Sistema

### **1. Login → Obtenção do Client ID**

```python
# backend_api.py - linha 1738 (função /api/login)

LoginResponse(
    user_id=user_id,
    usuario=nome_usuario,
    client_id=dados.client_id,  # ← AQUI! Número do cliente
    permissions=permissões,
    access_token=token_jwt
)
```

O `client_id` vem do número do cliente na **planilha Google Sheets de Clientes**.

### **2. Frontend Armazena Client ID**

```javascript
// vendas.html, integracoes.html, estoque.html

const userData = JSON.parse(localStorage.getItem('user_data'));
const clientId = userData.client_id;  // Exemplo: "000002"

// Toda requisição envia:
headers: {
    'X-Client-ID': clientId  // ← Identifica qual cliente está usando
}
```

### **3. Backend Cria Pasta Automática**

```python
# backend_api.py - linhas 136-141 (função get_tenant_path)

def get_tenant_path(client_id: str):
    """Retorna o caminho para a pasta de dados do cliente 
       e a cria se não existir."""
    tenant_path = os.path.join(PASTA_INFO, client_id)  # info/000002/
    if not os.path.exists(tenant_path):
        os.makedirs(tenant_path)  # ← Cria automaticamente
    return tenant_path
```

### **4. Bancos de Dados Salvos por Cliente**

```python
# backend_api.py - linha 330 (função _get_vendas_db)

def _get_vendas_db(client_id: str):
    tenant_path = get_tenant_path(client_id)  # info/000002/
    db_path = os.path.join(tenant_path, "vendas_historico.db")
    # → Resultado: info/000002/vendas_historico.db
    conn = sqlite3.connect(db_path)
    # ...cria as tabelas...
    return db_path
```

---

## 📍 Locais dos Arquivos por Cliente

| Arquivo | Cliente 1 | Cliente 2 |
|---------|-----------|-----------|
| **Vendas** | `info/000001/vendas_historico.db` | `info/000002/vendas_historico.db` |
| **Lojas** | `info/000001/lojas_config.json` | `info/000002/lojas_config.json` |
| **Integrações** | `info/000001/integracoes.json` | `info/000002/integracoes.json` |

---

## 🔐 Isolamento de Dados

### **Cliente 1 (000001) NUNCA vê dados de Cliente 2 (000002)**

```python
# backend_api.py - função get_tenant_id() - linha 127

def get_tenant_id(x_client_id: str = Header(...)):
    """Dependência FastAPI para extrair o ID do cliente do cabeçalho."""
    if not x_client_id:
        raise HTTPException(status_code=400, 
                          detail="Header X-Client-ID é obrigatório.")
    # ...validação...
    return x_client_id  # ← Usado em TODAS as rotas protegidas
```

Isso significa:
- ✅ `GET /api/vendas?client_id=000001` → Retorna vendas de 000001
- ✅ `GET /api/vendas?client_id=000002` → Retorna vendas de 000002
- ❌ Cliente 000001 não consegue acessar dados de 000002 (isolamento automático)

---

## 📝 Funções Relacionadas

| Função | Arquivo | Linha | Propósito |
|--------|---------|-------|-----------|
| `get_tenant_id()` | backend_api.py | 127 | Extrai client_id do header |
| `get_tenant_path()` | backend_api.py | 136 | Cria pasta info/cliente/ |
| `carregar_lojas()` | backend_api.py | 143 | Lê lojas do cliente |
| `salvar_lojas()` | backend_api.py | 155 | Salva lojas do cliente |
| `_get_vendas_db()` | backend_api.py | 328 | Abre DB vendas do cliente |
| `_get_notas_entrada_db()` | backend_api.py | 574 | Abre DB notas do cliente |

---

## 🧪 Testando a Estrutura

### **Método 1: Via Terminal**

```bash
# Listar pastas de clientes
cd info/
dir

# Exemplo de saída:
# 000001/
# 000002/
# 000003/

# Ver conteúdo do cliente 000002
dir info/000002/

# Exemplo de saída:
# vendas_historico.db
# lojas_config.json
# integracoes.json
```

### **Método 2: Via Script Python**

```python
import os
import json

PASTA_INFO = "info"

for cliente_id in os.listdir(PASTA_INFO):
    cliente_path = os.path.join(PASTA_INFO, cliente_id)
    if os.path.isdir(cliente_path):
        print(f"\n📁 Cliente: {cliente_id}")
        arquivos = os.listdir(cliente_path)
        for arquivo in arquivos:
            tamanho = os.path.getsize(
                os.path.join(cliente_path, arquivo)
            ) / 1024  # KB
            print(f"   - {arquivo} ({tamanho:.1f} KB)")
```

---

## 🚀 Como Adicionar um Novo Cliente

1. **Na planilha Google Sheets "Clientes":**
   - Adicione uma nova linha com: `numero_cliente | nome_usuario | senha`
   - Exemplo: `000005 | jose_silva | senha123`

2. **No primeiro login de jose_silva:**
   - Backend recebe `client_id = "000005"`
   - Função `get_tenant_path("000005")` cria automaticamente `info/000005/`
   - Primeira sincronização cria: `info/000005/vendas_historico.db`

3. **Pronto!** ✅
   - Dados de josé sempre salvos em `info/000005/`
   - Isolado de outros clientes

---

## 💡 Benefícios da Estrutura Multi-Cliente

| Benefício | Descrição |
|-----------|-----------|
| **🔐 Segurança** | Cada cliente só acessa seus próprios dados |
| **📦 Escalabilidade** | Adicionar novo cliente é automático |
| **🗑️ Backup** | Fazer backup de um cliente: copiar `info/000002/` |
| **🔄 Restauração** | Restaurar cliente: copiar pasta de backup |
| **📊 Manutenção** | Verificar saúde por cliente facilmente |
| **⚡ Performance** | DBs menores (por cliente) = mais rápido |

---

## ⚠️ Avisos Importantes

### **Arquivos Legados ainda em info/**

Os seguintes arquivos ainda estão na raiz de `info/`:
- `vendas_historico.db` ← OBSOLETO (use cliente-específico)
- `lojas_config.json` ← OBSOLETO (use cliente-específico)  
- `integracoes.json` ← OBSOLETO (use cliente-específico)

**Recomendação:** Apague-os para evitar confusão!

```bash
cd info/
del vendas_historico.db
del lojas_config.json
del integracoes.json
```

### **Pasta "Nova pasta"**

Pode ser apagada:
```bash
rmdir "Nova pasta"
```

---

## 📋 Checklist de Configuração

- ✅ Backend usa `get_tenant_path(client_id)` para isolamento
- ✅ Frontend envia `X-Client-ID` em todas requisições
- ✅ Cada cliente tem sua pasta `info/cliente_id/`
- ✅ Bancos de dados salvos por cliente
- ✅ Armazenamento isolado e seguro

---

## 🎯 Conclusão

**SIM, o sistema está salvando corretamente em pastas por cliente!**

A estrutura está funcionando conforme esperado:
1. Cada cliente recebe um `client_id` (número na planilha)
2. Frontend usa esse ID em toda requisição
3. Backend automaticamente cria pasta `info/cliente_id/`
4. Todos os arquivos do cliente ficam nessa pasta
5. Isolamento automático e seguro

**Status:** ✅ Implementado e Funcionando
