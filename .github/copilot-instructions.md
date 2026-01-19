# JK Gestor - AI Coding Instructions

## Project Overview
**JK Gestor** is a **Streamlit-based business management system** that integrates with Bling (e-commerce/ERP) and Google Sheets. It handles promotions, inventory, sales tracking, and user authentication for a product/audio equipment reseller business.

**Key Stack:** Python 3, Streamlit, Google Sheets API, Bling API (OAuth), SQLite, Pandas, Plotly

---

## Architecture & Key Components

### 1. **Entry Point & Core Application**
- **[app.py](app.py)** - Main Streamlit app (~551 lines)
  - OAuth callback processor for Bling integration
  - Session/cache management for Google Sheets connections
  - Logging setup (rotating file handler in `info/jk_sistema.log`)
  - Modular imports: `login`, `atualizacao`, `promo`, `renovacao`, `etiquetas`
  - **Critical:** Uses `st.cache_resource` for expensive operations (Google auth, DB connections) and `st.cache_data` for data with TTL

### 2. **Authentication Layer** 
- **[login.py](login.py)** - User authentication + session management
  - Streamlit Authenticator integration with `streamlit-authenticator==0.5.1`
  - Google Sheets OAuth via service account (`credentials.json`)
  - Users stored in Google Sheets; bcrypt password hashing
  - **Config:** `info/config_sheet.json` stores `spreadsheet_id` after setup

### 3. **Integration System**
- **[integracoes.py](integracoes.py)** - API integration management
  - Stores per-store API credentials in JSON (`info/lojas_config.json`)
  - OAuth state management with `REDIRECT_URI = "http://localhost:8501"`
  - Generic multi-API pattern: `atualizar_api_loja(nome_loja, api_nome, dados_api)`
  - **Pattern:** Each store can have multiple integrations (Bling, Google, etc.)

### 4. **Bling Integration** (E-Commerce/ERP)
- **[vendas.py](vendas.py)** - Sales data sync (~491 lines)
  - **SQLite DB:** `info/vendas_historico.db` - normalized sales history
  - Unique ID format: `{loja_conta}_{numero_pedido}_{sku}`
  - Fetches sales from Bling API v3, caches in local DB to avoid duplicate API calls
  - Columns: data, loja_conta, canal, numero, situacao, sku, produto, quantidade, valor, mes_ano

- **[estoque.py](estoque.py)** - Inventory management
  - Similar pattern to vendas.py

### 5. **Promotions & Pricing**
- **[promo.py](promo.py)** - Promotion calculation engine (~552 lines)
  - Processes MercadoLivre & custom price formats
  - **Database:** `DB_TAXAS` dict with product category → fee mappings (Clássico/Premium tiers)
  - Default fees: 12-18% depending on category and tier
  - Column model mappings: `COLUNAS_DO_MODELO_ANUNCIOS`, `COLUNAS_DO_MODELO_PROMO`
  - Handles merged cells in Excel via `openpyxl`

### 6. **Supporting Modules**
- **[renovacao.py](renovacao.py)** - Renewal/subscription logic
- **[etiquetas.py](etiquetas.py)** - Label generation for shipping/products
- **[atualizacao.py](atualizacao.py)** - Version management & auto-update from Google Sheets
  - Watches `SPREADSHEET_ID_CLIENTES = '1oyLYMd059baSs2KZJ8jqld68Y03a3pNRs32xvZlowNk'` for updates

---

## Critical Patterns & Conventions

### **File Structure**
- **`info/` folder** - All persistent data (credentials, configs, databases, logs)
  - `credentials.json` - Google service account (gitignore this)
  - `config_sheet.json` - Active spreadsheet ID
  - `integracoes.json` - Integration configs per store
  - `bling_contas.json` - Bling account mappings
  - `bling_produtos_db.json` - Product catalog cache
  - `vendas_historico.db` - SQLite sales history
  - `jk_sistema.log` - Rotating log (5MB max, 3 backups)

- **`img/` folder** - UI assets (logo, background)

### **Caching Strategy** (Streamlit-critical)
```python
@st.cache_resource  # Use for: Google client, DB connections (persist across reruns)
def autenticar_google_sheets():
    ...

@st.cache_data      # Use for: DataFrames, lists (data with optional TTL)
def buscar_usuarios():
    ...
```
**Why:** Streamlit reruns entire script on interaction. Caching prevents redundant Google auth or DB queries.

### **API Session Management**
```python
def _requests_session_with_retry(total_retries=3, backoff_factor=0.3):
    # Automatic retry on 429, 500, 502, 503, 504
    # Use for Bling API calls to handle transient failures
```

### **OAuth Flow (Bling)**
1. Redirect to: `https://www.bling.com.br/Api/v3/oauth/authorize?client_id=...&redirect_uri=http://localhost:8501`
2. Callback processes `?code=...&state=...`
3. Exchange code for token via `trocar_code_por_token()`
4. Store access_token + refresh_token in `integracoes.json`
5. **State param stores loja name** for multi-store setups

### **Error Handling Pattern**
```python
def _safe_read_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.exception("Error reading JSON %s", path)
        return None
```
**Convention:** Always catch, log with context, return safe default (None/empty dict)

### **Data Normalization**
- Text normalization: `unicodedata.normalize('NFKD', ...)` (used in promo.py)
- Column renaming: handles duplicate column names from Excel imports
- Date format: `strftime('%Y-%m-%d')` for consistency

---

## Developer Workflows

### **Setup & Installation**
```bash
pip install -r requirements.txt
# requires: credentials.json in info/ folder (Google service account JSON)
```

### **Running the App**
```bash
streamlit run app.py
```
- Listens on `http://localhost:8501`
- Logs to `info/jk_sistema.log`

### **Configuration After Setup**
1. Place Google service account JSON as `info/credentials.json`
2. On first run, app stores selected spreadsheet ID in `info/config_sheet.json`
3. Bling OAuth callbacks redirect to `http://localhost:8501` (update in `REDIRECT_URI` if hosting remotely)

### **Adding a New Module**
- Create file in root: `new_feature.py`
- Import in `app.py`: `import new_feature`
- Use same config patterns: `PASTA_INFO = "info"`, `CREDENTIALS_FILE` path
- Leverage `@st.cache_resource` for Google/DB connections
- Log via `logger` from app.py or create module-specific logger

### **Database Operations**
- **SQLite** uses `INSERT OR REPLACE` to upsert by unique ID
- Always use parameterized queries: `cursor.execute('...?...', (values,))`
- Call `get_db_connection()` to get fresh connection (no persistent pool needed in Streamlit)

---

## Integration Points & External Dependencies

### **Google Sheets API**
- **Scopes:** `spreadsheets`, `drive`
- **Auth:** Service account (from `credentials.json`)
- **Usage:** Store user list, config, client data
- **Caching:** Credentials authenticated once per session via `@st.cache_resource`

### **Bling API v3**
- **OAuth 2.0** with `code` grant type
- **Base URL:** `https://www.bling.com.br/Api/v3/`
- **Token exchange:** `POST /oauth/token` with Basic auth (base64 client_id:secret)
- **Endpoints used:** Sales, inventory, product catalog
- **Local DB:** Deduplicate by `{account}_{order}_{sku}` to avoid double-syncing
- **Error handling:** Auto-retry on transient failures via Session + Retry adapter

### **MercadoLivre Integration** (promo.py)
- Parses auction/listing format from Excel exports
- Calculates competitive pricing with category-specific fee tiers
- No direct API integration; input is CSV/Excel files from ML

### **Streamlit Built-ins**
- `st.cache_resource` / `st.cache_data` - Session/data caching
- `st.query_params` - OAuth callback handling
- `st.session_state` - Cross-page state persistence
- UI components: `st.sidebar`, `st.columns`, `st.buttons`, custom HTML/CSS

---

## Testing & Debugging

### **Logging Locations**
- **File log:** `info/jk_sistema.log` (rotating, 5MB max)
- **Console:** Streamlit terminal output
- **Module-level:** Most modules print to console via `print()` or `logger`

### **Cache Debugging**
- Clear cache: Delete `__pycache__/` or use Streamlit UI cache clear button
- Monitor cache hits: Check execution time (cached calls are instant)

### **OAuth Debugging**
- Check `integracoes.json` for stored tokens/status
- Verify `REDIRECT_URI` matches Bling app settings
- Watch query params: `st.query_params` should contain `code` + `state`

### **Database Integrity**
- Inspect `vendas_historico.db` with SQLite browser
- Check unique ID format: `{account}_{order}_{sku}` prevents duplicates
- Verify mes_ano format matches business logic

---

## Code Style & Conventions

- **Type hints:** Used in app.py function signatures (e.g., `-> Optional[gspread.Client]`)
- **Encoding:** Always `encoding='utf-8'` for file I/O
- **Path handling:** Use `os.path.join()` for cross-platform compatibility
- **Constants:** UPPER_SNAKE_CASE, defined at module level (PASTA_INFO, CONFIG_FILE, etc.)
- **Caching:** Prefer `@st.cache_resource` for connections, `@st.cache_data` for data
- **Error messages:** Include context (file path, operation type) in logs
- **Data validation:** Check for None, empty strings, and type correctness before processing

---

## Quick Reference

| Aspect | Pattern | File |
|--------|---------|------|
| **Auth** | Google OAuth + Streamlit Authenticator | login.py |
| **Bling Sync** | OAuth → token exchange → API call → SQLite | vendas.py |
| **Config Storage** | JSON in `info/` folder | app.py, login.py, promo.py |
| **Caching** | `@st.cache_resource` (auth), `@st.cache_data` (data) | app.py |
| **Retries** | HTTPAdapter + Retry on transient errors | app.py (_requests_session_with_retry) |
| **Errors** | Log exception + return safe default | app.py (_safe_read_json) |
| **Multi-Store** | Store name in OAuth state param + JSON keys | integracoes.py |

---

## Recent Changes & Known Issues

- Version: 1.5 (see `VERSAO_SISTEMA` in app.py)
- Auto-update system checks Google Sheets for new versions (atualizacao.py)
- SQLite schema includes `mes_ano` field for filtering sales by month/year
- Bling API throttling: 3 retries with 0.3s backoff recommended

**For clarification on any pattern or integration point, reference the specific files listed above.**
