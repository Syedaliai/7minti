# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**ProdSeller Telegram Reseller Bot** — An async Python Telegram bot for reselling digital/AI products via the ProdSeller API. Features strict cryptocurrency payment verification (Binance deposit history), atomic idempotency protections, encryption at rest for credentials, and structured database state machines.

- **Language**: Python 3.12+
- **Framework**: `python-telegram-bot` v21 (async API)
- **Database**: SQLAlchemy 2.0 async with PostgreSQL/asyncpg (SQLite/aiosqlite for dev)
- **Testing**: pytest, pytest-asyncio, respx

---

## Common Development Commands

### Setup & Installation
```bash
# Create venv and install deps
python -m venv venv
.\venv\Scripts\activate  # Windows
source venv/bin/activate  # Linux/macOS
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with valid API keys and deposit address

# Run migrations
alembic upgrade head
```

### Running the Bot
```bash
# Development (polling mode)
python -m app.main

# Production (Docker)
docker-compose up -d --build
docker-compose logs -f bot
```

### Testing
```bash
# Run all tests
pytest tests/ -v

# Run single test file
pytest tests/test_pricing.py -v

# Run single test function
pytest tests/test_pricing.py::test_selling_price_calculation -v

# With coverage
pytest tests/ --cov=app --cov-report=term-missing
```

### Database Operations
```bash
# Create new migration
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head

# Rollback one migration
alembic downgrade -1
```

---

## High-Level Architecture

### Layered Structure
```
app/
├── main.py                 # Bot bootstrap, handler registration, lifecycle hooks
├── config.py               # Pydantic Settings (validated env config)
├── logging_config.py       # Structured logging with sensitive data redaction
├── bot/
│   ├── handlers/           # Telegram command & callback handlers (14 modules)
│   ├── keyboards/          # Inline & reply keyboard builders
│   └── middleware/         # Rate limiting (sliding window)
├── services/
│   ├── pricing.py          # Single source of truth for Decimal markup math
│   ├── encryption.py       # Fernet 256-bit credential encryption
│   ├── prodseller.py       # Async ProdSeller client with retry/429 handling
│   ├── binance.py          # Async Binance HMAC-SHA256 deposit client
│   ├── payment_verifier.py # 11-point deposit verification engine
│   ├── checkout_service.py # Fresh quote creation & QR generation
│   ├── order_service.py    # Supplier fulfillment with Idempotency-Key
│   └── reconciliation.py   # Crash recovery for stuck orders/checkouts
├── db/
│   ├── base.py             # DeclarativeBase
│   ├── session.py          # Async engine & session factory (WAL for SQLite)
│   ├── models.py           # User, Checkout, Payment, Order, Audit models
│   └── repositories.py     # Transaction-safe async repository layer
└── utils/
    ├── money.py            # Decimal helpers & formatting
    ├── telegram.py         # HTML escaping & text sanitization
    ├── security.py         # TxID validation
    └── ids.py              # UUID & Idempotency-Key generators
```

### Key Architectural Patterns

**1. Pricing Engine (`app/services/pricing.py`)**
- Authoritative: `SELLING_PRICE = CURRENT_PRODSELLER_PRICE + PAYMENT_COMMISSION`
- Uses supplier `price` (cost), **never** `publicPrice`
- All monetary calculations use `Decimal` with `ROUND_HALF_UP`

**2. Payment Verification (`app/services/payment_verifier.py`)**
- 11-point Binance deposit verification: TxID match, address, coin (USDT), network, status=1 (credited), confirmations, amount
- `txid` has **UNIQUE DB constraint** to prevent replay attacks
- Read-only Binance API (least privilege)

**3. Idempotency Protection (`app/services/order_service.py`)**
- Generates stable `Idempotency-Key`: `tg_<user_id>_<checkout_id>` (max 100 chars)
- Persisted **before** calling ProdSeller `POST /orders`
- Retries reuse same key to prevent duplicate supplier charges

**4. Crash Recovery (`app/services/reconciliation.py`)**
- Startup reconciliation for stuck `PROCESSING` orders and `VERIFYING` checkouts
- Background reconciliation jobs

**5. Security (`app/services/encryption.py`)**
- Fernet 256-bit symmetric encryption for delivered credentials at rest
- Logging middleware redacts API keys, Binance signatures, delivered keys
- User authorization: User A cannot query User B's orders

---

## Database Models (Key Entities)

- **User** — Telegram user profile, balance, blocked status, admin flag
- **Checkout** — Quote with expiry, status (PENDING/VERIFYING/PAID/EXPIRED), deposit address
- **Payment** — Binance deposit record, 11 verification fields, unique `txid`
- **Order** — Supplier order with `idempotency_key`, status, encrypted credentials
- **AuditLog** — Immutable audit trail for all state transitions

---

## Handler Routing (app/main.py)

Commands registered via `CommandHandler`, callbacks via `CallbackQueryHandler` with regex patterns:
- **Navigation**: `nav:home`, `nav:deposit`, `nav:search`, `nav:support`
- **Catalog**: `catalog:<page>`, `prod:<product_id>`, `qty_(inc|dec):<id>:<qty>`
- **Checkout**: `buy:<product_id>:<qty>`, `paid:<checkout_id>`
- **Orders**: `orders:<page>`, `order_view:<order_id>`, `order_keys:<order_id>`
- **Admin**: `admin:dashboard`, `admin:analytics`, `admin:users:<page>`, etc.
- **SMS/WhatsApp OTP**: `sms:*`, `wa:*` patterns

Text messages routed in `handle_text_message()` based on `context.user_data` flags.

---

## Configuration (.env)

Required variables (see `.env.example`):
- `TELEGRAM_BOT_TOKEN`, `PRODSELLER_API_KEY`, `PAYMENT_ADDRESS`
- `BINANCE_API_KEY`, `BINANCE_API_SECRET`
- `DATABASE_URL` (default: SQLite `sqlite+aiosqlite:///./reseller_bot.db`)
- `ADMIN_TELEGRAM_IDS`, `SUPPORT_USERNAME`, `ENCRYPTION_KEY`
- `PAYMENT_COMMISSION` (default: 0.20), `PAYMENT_COIN` (USDT), `PAYMENT_NETWORK` (TRC20)

Generate encryption key:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

## Testing Notes

- Tests use in-memory SQLite (`sqlite+aiosqlite:///:memory:`)
- Fixtures in `tests/conftest.py` configure env vars before app import
- `respx` used for mocking HTTP calls (ProdSeller, Binance APIs)
- Test modules: pricing, payment_verification, duplicate_txid, idempotency, orders, prodseller

---

## Important Implementation Details

- **Concurrency**: SQLAlchemy async sessions with `expire_on_commit=False`, `autoflush=False`
- **SQLite**: WAL mode + `busy_timeout=60000` for multi-user access
- **Connection Pool**: PostgreSQL uses `pool_size=50`, `max_overflow=100`
- **Rate Limiting**: Sliding window in `app/bot/middleware/rate_limit.py`
- **Error Handling**: Global error handler in `main.py` logs unhandled exceptions