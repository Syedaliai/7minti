import os
import pytest
import pytest_asyncio
from decimal import Decimal
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

# Configure test environment variables before importing app
os.environ["TELEGRAM_BOT_TOKEN"] = "123456789:TEST_BOT_TOKEN"
os.environ["PRODSELLER_API_KEY"] = "psk_test_key_123456789"
os.environ["PRODSELLER_BASE_URL"] = "https://prodseller.com/v1"
os.environ["PAYMENT_COMMISSION"] = "0.20"
os.environ["PAYMENT_COIN"] = "USDT"
os.environ["PAYMENT_NETWORK"] = "TRC20"
os.environ["PAYMENT_ADDRESS"] = "TTestBinanceDepositAddress123"
os.environ["BINANCE_API_KEY"] = "test_binance_api_key"
os.environ["BINANCE_API_SECRET"] = "test_binance_api_secret"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["ADMIN_TELEGRAM_IDS"] = "123456,987654"
os.environ["SUPPORT_USERNAME"] = "test_support"
os.environ["ENCRYPTION_KEY"] = "gu_E91Pq5cKpFmQxVvX-e8iS3A_eJ5q1o7fX1u6i2bQ="

from app.db.base import Base


@pytest_asyncio.fixture
async def async_engine():
    """Create in-memory SQLite async engine for tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(async_engine):
    """Provide clean AsyncSession per test."""
    session_factory = async_sessionmaker(bind=async_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()
