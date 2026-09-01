# ProdSeller Telegram Reseller Bot

A production-ready, asynchronous Python Telegram Bot for reselling digital and AI products sourced dynamically from the [ProdSeller API](https://prodseller.com/api-docs/). Built with strict cryptocurrency payment verification (Binance deposit history), atomic idempotency protections against double charges, encryption at rest for customer credentials, and structured database state machines.

---

## 🌟 Key Architecture & Highlights

- **Dynamic Catalog & Pricing Engine**:
  - Authoritative selling price is calculated in real time: `SELLING_PRICE = CURRENT_PRODSELLER_PRICE + 0.20 USDT`.
  - Calculated strictly using supplier `price` (cost), **never** `publicPrice`.
  - All monetary and balance calculations use Python `Decimal` with `ROUND_HALF_UP` precision (no float rounding errors).
- **11-Point Binance Payment Verification**:
  - Automatically queries official Binance deposit history API (`/sapi/v1/capital/deposit/hisrec`) with HMAC-SHA256 signatures.
  - Verifies TxID match, deposit address, coin (`USDT`), network (e.g. `TRC20`), status (`1` = credited), confirmation depth, and deposited amount.
  - `txid` has a strict **`UNIQUE` database constraint** to prevent transaction reuse / replay attacks.
  - Zero automated withdrawals or fund movements (least-privilege read-only API access).
- **Supplier Idempotency Protection**:
  - Generates a unique, stable `Idempotency-Key` (max 100 characters: `tg_<user_id>_<checkout_id>`).
  - Persisted in the database **before** calling ProdSeller `POST /orders`.
  - Retries and network timeouts reuse the exact same key to prevent duplicate supplier orders.
- **Crash Recovery & Reconciliation**:
  - Startup and background routines automatically reconcile orders stuck in `PROCESSING` or checkouts in `VERIFYING`.
- **Security & Data Privacy**:
  - Digital credentials / license keys are encrypted at rest using Fernet 256-bit symmetric encryption.
  - Logging middleware redacts API keys, Binance signatures, and delivered account keys.
  - User authorization ensures User A can never query User B's orders.
- **Admin Control Center**:
  - Real-time supplier balance queries (`GET /balance`), live sales metrics, and revenue stats inside Telegram.

---

## 📋 Technology Stack

- **Language:** Python 3.12+
- **Telegram Bot Framework:** `python-telegram-bot` (v21 async API)
- **HTTP Client:** `httpx` (async client with retry backoff and rate limit handling)
- **Database & ORM:** `SQLAlchemy 2.0` (async) with `PostgreSQL` / `asyncpg` (SQLite / `aiosqlite` for local dev)
- **Configuration & Validation:** `pydantic-settings` & `pydantic v2`
- **Migrations:** `Alembic`
- **Security & Crypto:** `cryptography` (Fernet) & `qrcode[pil]`
- **Testing:** `pytest`, `pytest-asyncio`, `respx`

---

## 📁 Project Structure

```text
d:\Tel Bot\
├── app/
│   ├── __init__.py
│   ├── main.py                  # Bot bootstrap, router, lifecycle hooks
│   ├── config.py                # Pydantic Settings configuration & validation
│   ├── logging_config.py        # Structured logging with sensitive data redaction filter
│   ├── bot/
│   │   ├── __init__.py
│   │   ├── handlers/            # Telegram command & callback handlers
│   │   │   ├── start.py         # /start, registration, hub
│   │   │   ├── products.py      # Catalog pagination & product detail view
│   │   │   ├── search.py        # Keyword search
│   │   │   ├── checkout.py      # Authoritative price fetch & QR checkout quote
│   │   │   ├── payment.py       # "I Have Paid", TxID verification & delivery
│   │   │   ├── orders.py        # "My Orders" history & decrypted credential viewing
│   │   │   ├── support.py       # Support contact
│   │   │   └── admin.py         # Admin control panel & metrics
│   │   ├── keyboards/           # Inline & Reply keyboards
│   │   │   ├── main.py
│   │   │   ├── products.py
│   │   │   ├── checkout.py
│   │   │   └── admin.py
│   │   └── middleware/
│   │       └── rate_limit.py    # Sliding window anti-spam rate limiters
│   ├── services/
│   │   ├── pricing.py           # Single source of truth for all Decimal markup math
│   │   ├── encryption.py        # Fernet credential encryption at rest
│   │   ├── prodseller.py        # Async ProdSeller client with 429/retry handling
│   │   ├── binance.py           # Async Binance HMAC-SHA256 deposit history client
│   │   ├── payment_verifier.py  # 11-point deposit verification engine
│   │   ├── checkout_service.py  # Fresh quote creation & QR generation
│   │   ├── order_service.py     # Supplier fulfillment with Idempotency-Key
│   │   └── reconciliation.py   # Crash recovery for stuck orders
│   ├── db/
│   │   ├── base.py              # DeclarativeBase
│   │   ├── session.py           # AsyncSession generator & engine
│   │   ├── models.py            # User, Checkout, Payment, Order, Audit models
│   │   └── repositories.py      # Transaction-safe async repository layer
│   └── utils/
│       ├── money.py             # Decimal helpers & formatting
│       ├── telegram.py          # HTML escaping & text sanitization
│       ├── security.py          # TxID validation
│       └── ids.py               # UUID and Idempotency-Key generators
├── tests/
│   ├── conftest.py              # Test database and async fixtures
│   ├── test_pricing.py          # Pricing math, Decimal precision, publicPrice tests
│   ├── test_payment_verification.py # 11-point verification tests (valid, underpaid, unconfirmed)
│   ├── test_duplicate_txid.py   # Replay attack prevention tests
│   ├── test_idempotency.py      # Idempotency-Key persistence & reuse tests
│   ├── test_orders.py           # Authorization isolation & multi-key delivery tests
│   └── test_prodseller.py       # Mocked HTTP status codes (400-500) tests
├── alembic/                     # Database migrations
├── Dockerfile                   # Multi-stage production container
├── docker-compose.yml           # Bot + PostgreSQL service composition
├── requirements.txt             # Project dependencies
├── .env.example                 # Environment configuration template
└── README.md
```

---

## ⚙️ Configuration (.env)

Create a `.env` file in the root directory (refer to `.env.example`):

| Variable | Description | Example |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Token generated from [@BotFather](https://t.me/BotFather) | `123456789:ABCdefGh...` |
| `PRODSELLER_API_KEY` | Your ProdSeller API key (`psk_...`) | `psk_1234567890abcdef...` |
| `PRODSELLER_BASE_URL`| Base endpoint | `https://prodseller.com/v1` |
| `PAYMENT_COMMISSION` | Unit profit commission | `0.20` |
| `PAYMENT_COIN`       | Settlement cryptocurrency | `USDT` |
| `PAYMENT_NETWORK`    | Blockchain network | `TRC20` (or `BEP20`, `ERC20`) |
| `PAYMENT_ADDRESS`    | Your Binance deposit address | `TXYZ1234567890...` |
| `BINANCE_API_KEY`    | Read-only Binance API Key | `your_binance_api_key` |
| `BINANCE_API_SECRET` | Binance API Secret | `your_binance_secret` |
| `DATABASE_URL`       | Async database connection URL | `postgresql+asyncpg://user:pass@localhost:5432/reseller_bot` |
| `ADMIN_TELEGRAM_IDS` | Comma-separated admin Telegram IDs | `123456789,987654321` |
| `SUPPORT_USERNAME`   | Support contact handle (no `@`) | `MySupportTeam` |
| `ENCRYPTION_KEY`     | Base64 Fernet key for credentials | Generate via Python snippet below |
| `PRODUCT_CACHE_TTL`  | Catalog cache TTL in seconds | `60` |

### How to Generate an `ENCRYPTION_KEY`:
Run this in your terminal:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

## 🚀 Installation & Local Setup

### 1. Clone & Setup Virtual Environment
```bash
git clone <repository_url>
cd "d:/Tel Bot"

python -m venv venv
# On Windows:
.\venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure Environment
```bash
cp .env.example .env
# Edit .env and enter your valid API keys and deposit address
```

### 3. Run Database Migrations
```bash
alembic upgrade head
```

### 4. Run Automated Tests
```bash
pytest tests/ -v
```

### 5. Start the Bot
```bash
python -m app.main
```

---

## 🐳 Docker Deployment

To run in production with PostgreSQL:

```bash
docker-compose up -d --build
```

Check application logs:
```bash
docker-compose logs -f bot
```

---

## 🛡️ Security Best Practices

1. **Binance API Least-Privilege:** Create a dedicated API key on Binance with **only** the `Enable Reading` permission checked. Disable `Spot & Margin Trading`, `Enable Withdrawals`, and `Universal Transfer`.
2. **Double-Purchase Protection:** Even if a user double-clicks "I Have Paid" or repeats network requests, row-level locks and `idempotency_key` guarantees only **one** supplier charge is executed.
3. **No Float Accounting:** Every balance, supplier cost, customer quote, and commission calculation uses exact `Decimal` arithmetic.
4. **Delivered Secret Isolation:** License keys sent to users are never logged to console in plaintext and are encrypted at rest with Fernet.
