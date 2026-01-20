# CLAUDE.md - AI Assistant Guide for S4all_BOT

Last Updated: 2026-01-20

## Overview

**S4all_BOT** is a Telegram Business Messages bot designed for B2B e-commerce order management and customer support in the supplement/pharmaceutical industry. It features intelligent intent classification, order processing, FAQ management, and product catalog access.

### Quick Facts
- **Language**: Python 3.12.3
- **Framework**: python-telegram-bot 21.7 (with Business Messages support)
- **Web Server**: Flask 3.0.0 + Gunicorn 21.2.0
- **Database**: PostgreSQL with SQLAlchemy 2.0.23 ORM
- **Deployment**: Render.com (webhook-based)
- **Architecture**: Async-first, stateless webhook handlers

---

## Project Structure

```
/home/user/S4all_BOT/
├── main.py                     # Core bot logic (1,431 lines)
│   ├── Flask app & webhook endpoint
│   ├── Telegram command handlers
│   ├── Message routing & intent processing
│   └── Admin panel commands
│
├── intent_classifier.py        # NLP intent classification (650 lines)
│   ├── IntentType enum (5 intent types)
│   ├── Priority-based classification pipeline
│   └── Fuzzy matching for products/cities
│
├── database.py                 # PostgreSQL ORM layer (357 lines)
│   ├── SQLAlchemy models
│   ├── CRUD operations
│   └── Session management
│
├── wsgi.py                     # WSGI entry point (80 lines)
│   ├── Gunicorn application wrapper
│   ├── Async event loop setup
│   └── Bot initialization on startup
│
├── requirements.txt            # Python dependencies
├── pyproject.toml             # Project metadata
├── runtime.txt                # Python 3.11.0 (Render.com)
├── .python-version            # Python 3.12.3 (local dev)
├── uv.lock                    # UV package manager lock
│
└── Data Files (JSON)
    ├── citta_italiane.json    # Italian cities database
    ├── faq.json               # FAQ cache
    ├── access_code.json       # Authorization token (legacy)
    ├── authorized_users.json  # User list (legacy)
    ├── user_tags.json         # Customer tags (legacy)
    └── ordini_confermati.json # Orders (legacy)
```

### File Sizes & Complexity
- **main.py**: 54 KB - High complexity, central orchestrator
- **intent_classifier.py**: 27 KB - Medium complexity, NLP logic
- **database.py**: 11 KB - Low complexity, data layer

---

## Architecture & Design Patterns

### 1. Message Flow Pipeline

```
Telegram Update
    ↓
POST /webhook (Flask)
    ↓
bot_application.update_queue.put_nowait(update)
    ↓
Handler Routing (filters)
    ├── BusinessMessageFilter → handle_business_message()
    ├── filters.ChatType.PRIVATE → handle_private_message()
    ├── filters.ChatType.GROUPS → handle_group_message()
    └── CallbackQueryHandler → handle_callback_query()
    ↓
IntentClassifier.calcola_intenzione(text)
    ↓
Response Generation (fuzzy_search_faq / fuzzy_search_lista / etc.)
    ↓
send_message() / reply_text()
    ↓
Database Recording (orders, tags, audit logs)
```

### 2. Intent Classification Strategy

**Priority-Based Pipeline** (early exit on high confidence):

1. **PRIORITY 1: Richiesta Lista** (confidence: 0.95)
   - Explicit patterns: "voglio lista", "manda lista", "hai la lista?"
   - Direct command detection

2. **PRIORITY 2: Ordine Reale** (threshold: ≥3 points)
   - **Point-based scoring system**:
     - Price (€/$) → +3 points
     - Quantity (1x, due, tre) → +2 points
     - Separators (,;) → +1-2 points
     - Location/Address → +1 point
     - Italian city → +1 point
     - Payment method → +2 points
     - Product from catalog → +2 points
   - Excludes questions about ordering ("come faccio ordine?")

3. **PRIORITY 3: Domanda FAQ** (threshold: ≥0.65)
   - Question words: quando, dove, come, perché, cosa, chi, quale
   - Shipping inquiries: "tempi spedizione", "quando arriva"
   - Info requests: "vorrei sapere", "ho bisogno di"

4. **PRIORITY 4: Ricerca Prodotto** (threshold: ≥0.30)
   - Single-word queries
   - Product keywords from catalog
   - Patterns: "hai la...", "quanto costa..."

5. **PRIORITY 5: Saluto** (confidence: 0.80)
   - Greetings: ciao, buongiorno, buonasera

6. **FALLBACK** (confidence: 0.10)
   - No match found

### 3. Database Architecture

**Hybrid Storage Model**:
- **Primary**: PostgreSQL (production data)
- **Fallback**: JSON files (backward compatibility, cache)

**PostgreSQL Tables**:
```sql
-- Customer classification tags
user_tags (
    user_id VARCHAR(50) PRIMARY KEY,
    tag VARCHAR(20) NOT NULL,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
)

-- Bot access control
authorized_users (
    user_id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(200),
    username VARCHAR(100),
    created_at TIMESTAMP
)

-- Order records
ordini_confermati (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(50) NOT NULL,
    user_name VARCHAR(200),
    username VARCHAR(100),
    message TEXT,
    chat_id VARCHAR(50),
    message_id VARCHAR(50),
    data VARCHAR(20),      -- YYYY-MM-DD
    ora VARCHAR(20),       -- HH:MM:SS
    timestamp TIMESTAMP
)

-- Application configuration
app_config (
    key VARCHAR(100) PRIMARY KEY,
    value TEXT,
    updated_at TIMESTAMP
)
```

### 4. Async/Await Patterns

**Critical Rules**:
- All Telegram bot handlers are `async def`
- Use `await` for all bot API calls
- Flask routes are synchronous (webhook receives then queues)
- Event loop managed in wsgi.py startup

**Example Handler Pattern**:
```python
async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user_id = message.from_user.id
    text = message.text

    # Sync operations (database, intent classification)
    intent_result = intent_classifier.calcola_intenzione(text)

    # Async operations (Telegram API)
    await message.reply_text(response)
```

---

## Key Modules

### main.py (54 KB)

**Entry Point Functions**:
- `setup_bot()` - Async initialization (called from wsgi.py)
  - Database setup
  - Intent classifier initialization
  - Handler registration
  - Webhook configuration

**Command Handlers** (all async):

**User Commands**:
```python
/start [CODE]  # Authorization with access code
/help          # Display FAQ & regulations
/lista         # Show product catalog
```

**Admin Commands**:
```python
/aggiorna_faq       # Refresh FAQ from JustPaste.it
/aggiorna_lista     # Refresh product list from JustPaste.it
/cambia_codice      # Generate new access token (secrets.token_hex)
/clearordini [N]    # Delete orders older than N days
/genera_link        # Create t.me/bot?start=CODE link
/lista_autorizzati  # List all authorized users
/listtags           # List customers with tags
/ordini             # View today's orders
/revoca ID          # Revoke user access
/removetag ID       # Remove customer tag
/admin_help         # Show admin command reference
```

**Message Handlers**:
```python
handle_business_message()  # Telegram Business Messages
handle_private_message()   # Private DMs
handle_group_message()     # Group/channel messages
handle_callback_query()    # Inline button callbacks
```

**Business Message Detection**:
- Custom filter: `BusinessMessageFilter` (excludes callbacks)
- Auto-detects admin vs customer in conversation
- Supports `/reg TAG` command for customer registration
- Tag-based whitelist: `['aff', 'jgor5', 'ig5', 'sp20']`

**Utility Functions**:
```python
fetch_markdown_from_html(url)     # Scrape JustPaste.it with BeautifulSoup
parse_faq(markdown_text)          # Regex-based emoji-structured parsing
update_faq_from_web()             # Sync FAQ from web → JSON
update_lista_from_web()           # Sync product list from web → file
load_lista()                      # Load product list from cache
fuzzy_search_faq(query)           # Pattern + similarity-based FAQ search
fuzzy_search_lista(query)         # Intelligent product search
calculate_similarity(text1, text2) # SequenceMatcher fuzzy matching
normalize_text(text)              # Text preprocessing
```

**Configuration Constants**:
```python
BOT_TOKEN            # os.environ.get('BOT_TOKEN')
ADMIN_CHAT_ID        # int(os.environ.get('ADMIN_CHAT_ID'))
WEBHOOK_URL          # os.environ.get('WEBHOOK_URL')
PORT                 # int(os.environ.get('PORT', 10000))

ALLOWED_TAGS         # ['aff', 'jgor5', 'ig5', 'sp20']
FUZZY_THRESHOLD      # 0.6
FAQ_CONFIDENCE_THRESHOLD    # 0.65
LISTA_CONFIDENCE_THRESHOLD  # 0.30

LISTA_URL            # "https://justpaste.it/lista_4all"
PASTE_URL            # "https://justpaste.it/faq_4all"

PAYMENT_KEYWORDS     # ['bonifico', 'usdt', 'crypto', 'bitcoin', ...]
```

**Flask Routes**:
```python
@app.route('/')                      # Status page
@app.route('/health')                # Health check (Render.com)
@app.route('/webhook', methods=['POST'])  # Telegram webhook
```

---

### intent_classifier.py (27 KB)

**Core Classes**:
```python
class IntentType(Enum):
    RICHIESTA_LISTA = "lista"
    INVIO_ORDINE = "ordine"
    DOMANDA_FAQ = "faq"
    RICERCA_PRODOTTO = "ricerca"
    SALUTO = "saluto"
    FALLBACK = "fallback"

@dataclass
class IntentResult:
    intent: IntentType
    confidence: float
    reason: str
    matched_keywords: List[str]
```

**Classification Algorithm**:

**Order Detection Logic** (see line 266-474 in intent_classifier.py):
```python
def _check_ordine_reale(text, is_list_context=False):
    points = 0

    # Check 1: Price indicators (€, $, euro, euri) → +3 points
    if re.search(r'[€$]|\beuri?\b|\beuro\b', text):
        points += 3

    # Check 2: Quantity patterns (1x, due pezzi, 2 conf) → +2 points
    if re.search(r'\d+\s*x|x\s*\d+|\bdue\b|\btre\b|\bquattro\b|\bpezz', text):
        points += 2

    # Check 3: Separators (commas, semicolons) → +1-2 points
    # Check 4: Location words (via, indirizzo, cap) → +1 point
    # Check 5: Italian cities → +1 point
    # Check 6: Payment methods → +2 points
    # Check 7: Product from catalog → +2 points

    return points >= 3  # Threshold
```

**Fuzzy Matching Pattern**:
```python
def calculate_similarity(text1: str, text2: str) -> float:
    return SequenceMatcher(None, text1.lower(), text2.lower()).ratio()

# Usage: if calculate_similarity(query, product) >= THRESHOLD:
```

**Italian Cities Database**:
- Loaded from `citta_italiane.json` at initialization
- Used for address validation in order detection
- Fallback to minimal list if JSON missing

---

### database.py (11 KB)

**Session Management**:
```python
engine = create_engine(DATABASE_URL, pool_pre_ping=True, echo=False)
SessionLocal = scoped_session(sessionmaker(...))

# Usage pattern:
def some_operation():
    session = SessionLocal()
    try:
        result = session.query(...).all()
        session.commit()
        return result
    except Exception as e:
        session.rollback()
        raise
    finally:
        session.close()
```

**CRUD Functions**:

**User Tags** (main.py:96-99):
```python
get_user_tag(user_id: int) -> str
set_user_tag(user_id: int, tag: str)
remove_user_tag(user_id: int)
load_user_tags() -> dict
```

**Authorized Users**:
```python
is_user_authorized(user_id: int) -> bool
authorize_user(user_id: int, name: str, username: str)
revoke_user(user_id: int)
load_authorized_users() -> list
```

**Orders**:
```python
add_ordine_confermato(user_id, user_name, username, message, chat_id, message_id)
get_ordini_oggi() -> list
clear_old_orders(days: int)
```

**Configuration**:
```python
get_config(key: str, default=None) -> str
set_config(key: str, value: str)
load_access_code() -> str
save_access_code(code: str)
```

**Connection Details**:
- Uses `pool_pre_ping=True` for connection health checks
- Automatic Render.com URL fix: `postgres://` → `postgresql://`
- Scoped sessions for thread safety

---

### wsgi.py (2.3 KB)

**Purpose**: Gunicorn entry point for production deployment

**Key Features**:
- Imports Flask app from main.py
- Creates new asyncio event loop
- Executes `setup_bot()` synchronously at startup
- Logs all registered Flask routes
- Exposes `app` variable for Gunicorn

**Critical Pattern**:
```python
# Create event loop and run async bot setup
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
loop.run_until_complete(setup_bot())

# Now Flask app is ready with bot handlers registered
# Gunicorn command: gunicorn wsgi:app
```

---

## Development Workflows

### Setting Up Local Development

1. **Clone & Install**:
```bash
git clone <repository_url>
cd S4all_BOT
python3.12 -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
```

2. **Configure Environment Variables**:
Create `.env` file:
```bash
BOT_TOKEN=your_telegram_bot_token
ADMIN_CHAT_ID=your_telegram_user_id
WEBHOOK_URL=https://your-app.onrender.com
DATABASE_URL=postgresql://user:pass@host:5432/dbname
PORT=10000
```

3. **Initialize Database**:
```python
python -c "from database import init_db; init_db()"
```

4. **Run Development Server**:
```bash
# Option 1: Flask development server (webhook mode)
python main.py

# Option 2: WSGI server (production-like)
gunicorn wsgi:app --bind 0.0.0.0:10000
```

### Testing the Bot

**Manual Testing**:
1. Set webhook: Bot does this automatically in `setup_bot()`
2. Send message to bot on Telegram
3. Check logs for intent classification and response

**Testing Intent Classification**:
```python
from intent_classifier import IntentClassifier

classifier = IntentClassifier()
result = classifier.calcola_intenzione("Voglio 2x proteine e 1x bcaa, Via Roma 10 Milano, bonifico")
print(f"Intent: {result.intent}, Confidence: {result.confidence}")
# Expected: INVIO_ORDINE, ~0.85
```

**Testing Database Operations**:
```python
import database as db

db.init_db()
db.authorize_user(123456, "Test User", "testuser")
print(db.is_user_authorized(123456))  # True
```

### Common Development Tasks

**1. Adding a New Intent Type**:

a. Add to `IntentType` enum in `intent_classifier.py`:
```python
class IntentType(Enum):
    # ... existing types ...
    NEW_INTENT = "new_intent"
```

b. Add detection method in `IntentClassifier`:
```python
def _check_new_intent(self, text_norm: str) -> tuple:
    patterns = [r'\bpattern1\b', r'\bpattern2\b']
    # ... detection logic ...
    return matched, confidence, reason, keywords
```

c. Add priority check in `calcola_intenzione()`:
```python
# Add in appropriate priority level
matched, conf, reason, kw = self._check_new_intent(text_norm)
if matched and conf > threshold:
    return IntentResult(IntentType.NEW_INTENT, conf, reason, kw)
```

d. Handle in message handlers (`main.py`):
```python
if intent_result.intent == IntentType.NEW_INTENT:
    response = "Handle new intent here"
    await message.reply_text(response)
```

**2. Adding a New Admin Command**:

```python
# In main.py

async def comando_nuovo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /nuovo command"""
    user_id = update.effective_user.id

    # Admin check
    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ Non autorizzato")
        return

    # Command logic
    try:
        result = do_something()
        await update.message.reply_text(f"✅ {result}")
    except Exception as e:
        logger.error(f"Errore comando_nuovo: {e}")
        await update.message.reply_text(f"❌ Errore: {e}")

# Register in setup_bot()
def setup_bot():
    # ... existing handlers ...
    application.add_handler(CommandHandler("nuovo", comando_nuovo))
```

**3. Updating FAQ or Product List**:

Two methods:

a. **Via Bot Command** (recommended):
- Admin sends `/aggiorna_faq` or `/aggiorna_lista`
- Bot fetches from JustPaste.it and updates cache

b. **Manually Edit JSON**:
- Edit `faq.json` or update content on JustPaste.it
- Bot reads from cache first, falls back to web fetch

**4. Adding a New Database Table**:

```python
# In database.py

class NewModel(Base):
    __tablename__ = 'new_table'

    id = Column(Integer, primary_key=True, autoincrement=True)
    field1 = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

# CRUD functions
def add_new_record(field1_value):
    session = SessionLocal()
    try:
        record = NewModel(field1=field1_value)
        session.add(record)
        session.commit()
        return record.id
    except Exception as e:
        session.rollback()
        raise
    finally:
        session.close()
```

Then call `init_db()` to create table.

---

## Configuration & Environment Variables

### Required Environment Variables

```bash
# Telegram Bot API
BOT_TOKEN          # Get from @BotFather
ADMIN_CHAT_ID      # Your Telegram user ID (numeric)

# Webhook (production)
WEBHOOK_URL        # Public HTTPS URL (e.g., https://s4all-bot.onrender.com)
PORT               # Server port (default: 10000)

# Database
DATABASE_URL       # PostgreSQL connection string
                   # Format: postgresql://user:pass@host:5432/dbname
```

### Optional Configuration

**Threshold Tuning** (main.py:51-54):
```python
FUZZY_THRESHOLD = 0.6              # Fuzzy matching threshold
FAQ_CONFIDENCE_THRESHOLD = 0.65    # FAQ intent confidence
LISTA_CONFIDENCE_THRESHOLD = 0.30  # Product search confidence
```

**Allowed Customer Tags** (main.py:49):
```python
ALLOWED_TAGS = ['aff', 'jgor5', 'ig5', 'sp20']
# Add new tags here for customer classification
```

**Content URLs** (main.py:45-46):
```python
LISTA_URL = "https://justpaste.it/lista_4all"
PASTE_URL = "https://justpaste.it/faq_4all"
```

---

## Deployment (Render.com)

### Deployment Configuration

**Build Command**:
```bash
pip install -r requirements.txt
```

**Start Command**:
```bash
gunicorn wsgi:app
```

**Runtime**:
- `runtime.txt` specifies Python 3.11.0
- Local development uses Python 3.12.3

### Webhook Setup

Bot automatically configures webhook in `setup_bot()`:
```python
await bot_application.bot.set_webhook(
    url=f"{WEBHOOK_URL}/webhook",
    allowed_updates=["message", "callback_query", "business_message"]
)
```

**Manual Webhook Management**:
```bash
# Check webhook status
curl https://api.telegram.org/bot<TOKEN>/getWebhookInfo

# Delete webhook
curl https://api.telegram.org/bot<TOKEN>/deleteWebhook

# Set webhook
curl -X POST https://api.telegram.org/bot<TOKEN>/setWebhook \
  -d url=https://your-app.onrender.com/webhook
```

### Health Check

Render.com pings `/health` endpoint:
```python
@app.route('/health')
def health():
    return jsonify({"status": "ok"}), 200
```

---

## Testing & Debugging

### Logging

**Log Levels**:
```python
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
```

**Key Log Points**:
- Intent classification results
- Database operations
- Webhook requests
- Handler execution
- Errors with stack traces

**Viewing Logs**:
- **Render.com**: Dashboard → Logs tab
- **Local**: Console output

### Common Issues & Solutions

**1. Webhook Not Receiving Updates**:
- Check `WEBHOOK_URL` is correct and HTTPS
- Verify bot token is valid
- Check Render service is running
- Inspect `/webhook` route logs

**2. Database Connection Errors**:
- Verify `DATABASE_URL` environment variable
- Check PostgreSQL service is running
- Look for `postgres://` vs `postgresql://` URL format
- Test connection: `psql $DATABASE_URL`

**3. Intent Misclassification**:
- Check threshold values (may need tuning)
- Verify product list is loaded (`load_lista()`)
- Test with `calcola_intenzione()` directly
- Review matched keywords in `IntentResult`

**4. Business Messages Not Working**:
- Ensure bot is connected to Telegram Business account
- Verify `python-telegram-bot>=21.7` (Business API support)
- Check `BusinessMessageFilter` is registered
- Confirm admin detection logic

**5. Order Not Saving**:
- Check callback handler for "pay_ok_" prefix
- Verify database connection
- Review `add_ordine_confermato()` logs
- Check PostgreSQL table exists

---

## Important Notes for AI Assistants

### Code Modification Guidelines

**DO**:
- Read files before editing (ALWAYS use Read tool first)
- Preserve async/await patterns in handlers
- Test intent classification changes thoroughly
- Update thresholds cautiously (affects user experience)
- Commit changes with descriptive messages
- Use database.py functions (don't write raw SQL)

**DON'T**:
- Modify database.py without testing migrations
- Change ALLOWED_TAGS without admin approval
- Break backward compatibility with JSON files
- Remove error handling or logging
- Push to main branch (use feature branches)
- Hardcode credentials or tokens

### Security Considerations

**Sensitive Data** (NEVER commit):
- `.env` file (contains BOT_TOKEN, DATABASE_URL)
- `access_code.json` (authorization token)
- `authorized_users.json` (user data)
- Any JSON file with real user data

**Access Control**:
- Admin commands: Always check `user_id == ADMIN_CHAT_ID`
- User authorization: Check `is_user_authorized(user_id)`
- Business messages: Verify customer tags in ALLOWED_TAGS

**Input Validation**:
- Sanitize user input before database insertion
- Validate callback_query data format
- Check message text length before processing

### Performance Optimization

**Current Bottlenecks**:
1. Web scraping (JustPaste.it) - cached in JSON
2. Fuzzy matching - O(n) for each product
3. Database queries - use indexes on user_id

**Optimization Strategies**:
- Use LRU cache for frequent queries
- Implement product search index
- Batch database operations
- Consider Redis for session state

### Testing Checklist

Before committing changes:
- [ ] Intent classification still works
- [ ] Database operations don't fail
- [ ] Admin commands require authorization
- [ ] Order confirmation flow works
- [ ] FAQ search returns results
- [ ] Product list loads correctly
- [ ] Logs are informative
- [ ] No credentials in code

### Git Workflow

**Branch Naming**:
- Feature: `feature/description`
- Bugfix: `bugfix/description`
- Hotfix: `hotfix/description`

**Commit Message Format**:
```
type: Brief description (50 chars)

Detailed explanation if needed.

- Bullet points for multiple changes
- Reference issues: Fixes #123
```

**Types**: feat, fix, docs, refactor, test, chore

### Common Patterns

**Error Handling**:
```python
try:
    result = risky_operation()
    logger.info(f"✅ Success: {result}")
except SpecificException as e:
    logger.error(f"❌ Error in operation: {e}")
    await message.reply_text("Si è verificato un errore")
```

**Database Operations**:
```python
session = SessionLocal()
try:
    # Query/insert/update operations
    session.commit()
except Exception as e:
    session.rollback()
    logger.error(f"Database error: {e}")
    raise
finally:
    session.close()
```

**Telegram API Calls**:
```python
async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    text = message.text

    # Always await Telegram API calls
    await message.reply_text(response)
    await context.bot.send_message(chat_id=..., text=...)
```

---

## Quick Reference

### File Navigation

| File | Lines | Purpose |
|------|-------|---------|
| main.py:30-65 | 35 | Configuration & constants |
| main.py:71-89 | 18 | BusinessMessageFilter |
| main.py:200-300 | 100 | Command handlers |
| main.py:500-700 | 200 | Message handlers |
| main.py:1400-1431 | 31 | setup_bot() |
| intent_classifier.py:12-19 | 7 | IntentType enum |
| intent_classifier.py:66-150 | 84 | Pattern definitions |
| intent_classifier.py:266-474 | 208 | Order detection logic |
| database.py:40-80 | 40 | SQLAlchemy models |
| database.py:99-200 | 101 | CRUD functions |

### Key Dependencies

```
python-telegram-bot==21.7  # Telegram Bot API + Business Messages
Flask==3.0.0              # Web server framework
gunicorn==21.2.0          # WSGI server
beautifulsoup4==4.12.2    # HTML parsing
requests==2.31.0          # HTTP client
sqlalchemy==2.0.23        # ORM
psycopg2-binary==2.9.9    # PostgreSQL driver
```

### Useful Commands

```bash
# Run bot locally
python main.py

# Run with Gunicorn
gunicorn wsgi:app --bind 0.0.0.0:10000

# Initialize database
python -c "from database import init_db; init_db()"

# Test intent classifier
python -c "from intent_classifier import IntentClassifier; c=IntentClassifier(); print(c.calcola_intenzione('voglio lista'))"

# Check database connection
python -c "from database import engine; print(engine.connect())"

# View PostgreSQL tables
psql $DATABASE_URL -c "\dt"

# Tail logs on Render
render logs -t
```

---

## Glossary

| Term | Definition |
|------|------------|
| **Business Messages** | Telegram premium feature for B2B communication (admin-customer pairs) |
| **Intent** | User's goal/purpose (order, FAQ, product search, etc.) |
| **Fuzzy Matching** | Approximate string matching (handles typos, variations) |
| **Webhook** | HTTP endpoint receiving real-time Telegram updates |
| **WSGI** | Web Server Gateway Interface (Python web standard) |
| **ORM** | Object-Relational Mapping (SQLAlchemy database abstraction) |
| **JustPaste.it** | Web-based CMS for FAQ/product list (external content source) |
| **Tag** | Customer classification label (aff, jgor5, ig5, sp20) |
| **Confidence** | Intent classification certainty score (0.0-1.0) |
| **Callback Query** | Telegram inline button press event |

---

## Version History

| Date | Changes |
|------|---------|
| 2026-01-20 | Initial CLAUDE.md creation - comprehensive documentation |

---

## Contact & Support

**Admin**: Check `ADMIN_CHAT_ID` environment variable
**Repository**: /home/user/S4all_BOT
**Deployment**: Render.com
**Database**: PostgreSQL (via DATABASE_URL)

For issues or questions:
1. Check logs first (`logger.info/error` statements)
2. Review this documentation
3. Test locally before deploying
4. Consult admin for business logic questions

---

**End of CLAUDE.md**
