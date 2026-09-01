from decimal import Decimal
from typing import Optional, Set
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # Telegram
    TELEGRAM_BOT_TOKEN: str = Field(..., description="Telegram Bot Token from BotFather")
    TELEGRAM_PROXY_URL: Optional[str] = Field(default=None, description="Optional HTTP/SOCKS proxy for Telegram API")

    # ProdSeller Supplier API
    PRODSELLER_API_KEY: str = Field(..., description="ProdSeller API Key (psk_...)")
    PRODSELLER_BASE_URL: str = Field(default="https://prodseller.com/v1", description="ProdSeller API base URL")

    # Pricing & Payment
    PAYMENT_COMMISSION: Decimal = Field(default=Decimal("0.20"), description="Profit commission added to supplier price")
    PAYMENT_COIN: str = Field(default="USDT", description="Accepted cryptocurrency symbol")
    PAYMENT_NETWORK: str = Field(default="TRC20", description="Cryptocurrency blockchain network (TRC20, BEP20, ERC20, etc.)")
    PAYMENT_ADDRESS: str = Field(..., description="Binance deposit address for receiving funds")

    # Binance API
    BINANCE_API_KEY: str = Field(..., description="Binance API Key with read-only deposit permissions")
    BINANCE_API_SECRET: str = Field(..., description="Binance API Secret")
    BINANCE_BASE_URL: str = Field(default="https://api.binance.com", description="Binance API base endpoint")
    BINANCE_UID: Optional[str] = Field(default=None, description="Binance Pay UID / User ID")

    # SMSPool API
    SMSPOOL_API_KEY: str = Field(default="", description="SMSPool.net API key for SMS OTP service")
    SMS_COMMISSION_RATE: Decimal = Field(default=Decimal("0.60"), description="Commission rate added on YouTube SMSPool price (e.g. 0.60 = 60%)")
    OPENAI_SMS_COMMISSION_RATE: Decimal = Field(default=Decimal("0.80"), description="Commission rate added on OpenAI/ChatGPT/Codex SMSPool price (e.g. 0.80 = 80%)")
    NVIDIA_SMS_COMMISSION_RATE: Decimal = Field(default=Decimal("0.80"), description="Commission rate added on Nvidia SMSPool price (e.g. 0.80 = 80%)")

    # GrizzlySMS API — WhatsApp OTP numbers (USA & UK only)
    GRIZZLYSMS_API_KEY: str = Field(default="", description="GrizzlySMS API key for WhatsApp OTP number service")
    GRIZZLYSMS_COMMISSION_RATE: Decimal = Field(default=Decimal("0.80"), description="80% commission markup on GrizzlySMS raw price (e.g. 0.80 = 80%)")

    # Database
    DATABASE_URL: str = Field(
        default="sqlite+aiosqlite:///./reseller_bot.db",
        description="Async SQLAlchemy database connection URL",
    )

    # Administration
    ADMIN_TELEGRAM_IDS: str = Field(
        default="",
        description="Comma-separated Telegram user IDs of administrators",
    )

    # Force Channel Subscription Guard
    REQUIRED_CHANNEL_ID: Optional[str] = Field(
        default=None,
        description="Telegram channel username (e.g. @mychannel) or numeric ID (-100xxxx) for mandatory subscription",
    )
    REQUIRED_CHANNEL_LINK: Optional[str] = Field(
        default=None,
        description="Telegram channel direct invite link (e.g. https://t.me/mychannel)",
    )

    # Support & Contact
    SUPPORT_USERNAME: str = Field(default="support", description="Customer support Telegram username without @")

    # Security & Encryption
    ENCRYPTION_KEY: str = Field(
        ...,
        description="Fernet 32-url-safe base64-encoded key for encrypting delivered credentials at rest",
    )

    # Caching & Lifecycles
    PRODUCT_CACHE_TTL: int = Field(default=60, description="Product catalog cache TTL in seconds")
    CHECKOUT_EXPIRY_MINUTES: int = Field(default=30, description="Checkout quote valid window in minutes")
    LOG_LEVEL: str = Field(default="INFO", description="Logging level (DEBUG, INFO, WARNING, ERROR)")

    @property
    def admin_ids_set(self) -> Set[int]:
        """Return admin IDs as a set of integers."""
        if not self.ADMIN_TELEGRAM_IDS.strip():
            return set()
        ids = set()
        for item in self.ADMIN_TELEGRAM_IDS.split(","):
            cleaned = item.strip()
            if cleaned.isdigit():
                ids.add(int(cleaned))
        return ids

    @field_validator("PAYMENT_COMMISSION", "SMS_COMMISSION_RATE", "OPENAI_SMS_COMMISSION_RATE", "NVIDIA_SMS_COMMISSION_RATE", "GRIZZLYSMS_COMMISSION_RATE", mode="before")
    @classmethod
    def parse_commission(cls, v) -> Decimal:
        return Decimal(str(v))


settings = Settings()
