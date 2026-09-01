from datetime import datetime, timezone
from decimal import Decimal
import enum
import logging
from typing import Any, Dict, Optional, Tuple
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db.models import Checkout, Payment, CheckoutStatus
from app.db.repositories import PaymentRepository, CheckoutRepository
from app.services.binance import BinanceService
from app.utils.money import to_decimal

logger = logging.getLogger(__name__)


class VerificationResult(str, enum.Enum):
    PAID = "PAID"
    UNDERPAID = "UNDERPAID"
    OVERPAID_REVIEW = "OVERPAID_REVIEW"
    INVALID_TXID = "INVALID_TXID"
    UNCONFIRMED_PENDING = "UNCONFIRMED_PENDING"
    ALREADY_USED_TXID = "ALREADY_USED_TXID"
    ALREADY_PAID = "ALREADY_PAID"
    EXPIRED_CHECKOUT = "EXPIRED_CHECKOUT"
    BINANCE_ERROR = "BINANCE_ERROR"


# Mapping for common network alias variations
NETWORK_ALIASES = {
    "TRC20": ["TRC20", "TRX", "TRON"],
    "BEP20": ["BEP20", "BSC", "BNB"],
    "ERC20": ["ERC20", "ETH", "ETHEREUM"],
    "SOL": ["SOL", "SOLANA"],
    "MATIC": ["MATIC", "POLYGON"],
    "ARB": ["ARB", "ARBITRUM"],
}


def normalize_network(net: str) -> str:
    """Normalize network string for consistent matching."""
    cleaned = (net or "").strip().upper()
    for standard, aliases in NETWORK_ALIASES.items():
        if cleaned in aliases:
            return standard
    return cleaned


class PaymentVerifier:
    """Comprehensive 11-point cryptocurrency deposit verification engine."""

    def __init__(
        self,
        binance_service: BinanceService,
        payment_repo: PaymentRepository,
        checkout_repo: CheckoutRepository,
    ):
        self.binance_service = binance_service
        self.payment_repo = payment_repo
        self.checkout_repo = checkout_repo
        # Access session via repository for rollback on integrity errors
        self.session = payment_repo.session

    async def verify_txid(
        self,
        checkout: Checkout,
        txid: str,
        user_telegram_id: int,
    ) -> Tuple[VerificationResult, str, Optional[Dict[str, Any]]]:
        """Verify customer submitted TxID against authoritative Binance deposit history.

        Verification Checks:
        1. Checkout is not expired
        2. Checkout is not already completed/paid
        3. TxID has not already been used for another checkout (DB UNIQUE constraint check)
        4. TxID exists in Binance deposit history
        5. TxID exactly matches submitted string
        6. Deposit belongs to configured Binance deposit address
        7. Coin matches configured payment coin (e.g., USDT)
        8. Network matches configured payment network (e.g., TRC20)
        9. Deposit status is successful/credited (Binance status 1)
        10. Confirmations have been satisfied
        11. Deposited amount is sufficient (equal or greater than expected)
        """
        clean_txid = txid.strip()

        # 1. Expiry check
        now = datetime.now(timezone.utc)
        if checkout.expires_at and checkout.expires_at < now:
            logger.info("Checkout %s expired at %s", checkout.id, checkout.expires_at)
            await self.checkout_repo.update_status(checkout.id, CheckoutStatus.EXPIRED)
            return VerificationResult.EXPIRED_CHECKOUT, "This checkout has expired. Please create a new order.", None

        # 2. Already completed check
        if checkout.status in (CheckoutStatus.PAID.value, CheckoutStatus.OVERPAID_REVIEW.value):
            return VerificationResult.ALREADY_PAID, "This checkout has already been paid and processed.", None

        # 3. Duplicate TxID check across system
        existing_payment = await self.payment_repo.get_by_txid(clean_txid)
        if existing_payment:
            if existing_payment.checkout_id != checkout.id:
                logger.warning(
                    "Duplicate TxID reuse attempt: txid=%s used on checkout=%s attempted on checkout=%s",
                    clean_txid,
                    existing_payment.checkout_id,
                    checkout.id,
                )
                return VerificationResult.ALREADY_USED_TXID, "This TxID has already been used for another payment. Duplicate payments are not allowed.", None

        # Fetch records from Binance (First check Binance Pay history, then on-chain deposits)
        matching_pay_tx = None
        matching_deposit = None
        dep_coin = checkout.coin.upper()
        dep_network = checkout.network.upper()
        deposited_amount = Decimal("0")
        binance_status_code = None
        safe_metadata = {}

        try:
            # 1. Query Binance Pay transactions (for Binance Pay Order ID / UID transfers)
            try:
                pay_records = await self.binance_service.get_pay_transactions(limit=100)
                for prec in pay_records:
                    p_order_id = str(prec.get("orderId", "")).strip()
                    p_tx_id = str(prec.get("transactionId", "")).strip()
                    if clean_txid.lower() in (p_order_id.lower(), p_tx_id.lower()):
                        matching_pay_tx = prec
                        break
            except Exception as pay_err:
                logger.warning("Could not fetch Binance Pay records: %s", pay_err)

            # 2. If not found in Binance Pay, check on-chain deposit history
            if not matching_pay_tx:
                deposits = await self.binance_service.get_deposit_history(coin=checkout.coin, limit=50)
                for record in deposits:
                    record_txid = str(record.get("txId", "")).strip()
                    if record_txid.lower() == clean_txid.lower():
                        matching_deposit = record
                        break
        except Exception as ex:
            logger.error("Failed to query Binance for TxID/OrderID %s: %s", clean_txid, ex)
            return VerificationResult.BINANCE_ERROR, "Unable to verify payment with Binance right now. Please try again in a few moments or contact support.", None

        if not matching_pay_tx and not matching_deposit:
            logger.info("Order ID / TxID %s not found in Binance Pay or deposit history", clean_txid)
            return VerificationResult.INVALID_TXID, "❌ <b>Order ID / TxID not found on Binance.</b>\nPlease ensure you have sent USDT to our Binance Pay UID or deposit address and entered the correct Order ID.", None

        if matching_pay_tx:
            # Verified via Binance Pay
            dep_coin = str(matching_pay_tx.get("currency", "USDT")).upper()
            dep_network = "Binance Pay"
            deposited_amount = to_decimal(matching_pay_tx.get("amount", "0"))
            pay_status = str(matching_pay_tx.get("status", "")).upper()
            binance_status_code = pay_status

            # Validate currency
            if dep_coin != checkout.coin.upper():
                return VerificationResult.INVALID_TXID, f"Currency mismatch: payment received in {dep_coin}, expected {checkout.coin}.", matching_pay_tx

            # Validate status
            if pay_status not in ("SUCCESS", "PAID", "COMPLETED", "1"):
                return VerificationResult.UNCONFIRMED_PENDING, f"Binance Pay status is {pay_status}. Please wait until payment is completed.", matching_pay_tx

            # Validate receiver UID if available
            receiver_info = matching_pay_tx.get("receiverInfo") or {}
            receiver_uid = str(receiver_info.get("binanceId") or receiver_info.get("accountId") or "").strip()
            if settings.BINANCE_UID and receiver_uid and receiver_uid != settings.BINANCE_UID.strip():
                logger.warning("Binance Pay receiver UID mismatch: received %s, expected %s", receiver_uid, settings.BINANCE_UID)
                return VerificationResult.INVALID_TXID, "Payment was sent to an incorrect Binance UID.", matching_pay_tx

            safe_metadata = {
                "type": "binance_pay",
                "orderId": matching_pay_tx.get("orderId", clean_txid),
                "transactionId": matching_pay_tx.get("transactionId"),
                "currency": dep_coin,
                "amount": str(deposited_amount),
                "status": pay_status,
                "transactionTime": matching_pay_tx.get("transactionTime"),
                "receiver": receiver_uid,
            }
        else:
            # Verified via On-chain deposit
            # Verify deposit address
            deposit_address = str(matching_deposit.get("address", "")).strip()
            expected_address = settings.PAYMENT_ADDRESS.strip()
            if deposit_address and expected_address and deposit_address.lower() != expected_address.lower():
                logger.warning(
                    "Deposit address mismatch: received at %s, expected %s",
                    deposit_address,
                    expected_address,
                )
                return VerificationResult.INVALID_TXID, "Payment was not sent to our designated deposit address.", matching_deposit

            # Coin check
            dep_coin = str(matching_deposit.get("coin", "")).upper()
            if dep_coin != checkout.coin.upper():
                logger.warning("Coin mismatch: received %s, expected %s", dep_coin, checkout.coin)
                return VerificationResult.INVALID_TXID, f"Currency mismatch: payment received in {dep_coin}, expected {checkout.coin}.", matching_deposit

            # Network check
            dep_network = normalize_network(str(matching_deposit.get("network", "")))
            expected_network = normalize_network(checkout.network)
            if dep_network != expected_network:
                logger.warning("Network mismatch: received on %s, expected %s", dep_network, expected_network)
                return VerificationResult.INVALID_TXID, f"Network mismatch: transfer was made via {dep_network}, but order required {expected_network}.", matching_deposit

            # Status check
            binance_status_code = matching_deposit.get("status")
            if binance_status_code != 1:
                logger.info("Deposit %s is pending confirmation (status: %s)", clean_txid, binance_status_code)
                return VerificationResult.UNCONFIRMED_PENDING, "Transaction is detected on Binance but is still awaiting required network confirmations. Please try verifying again in 1-2 minutes.", matching_deposit

            deposited_amount = to_decimal(matching_deposit.get("amount", "0"))
            safe_metadata = {
                "type": "on_chain_deposit",
                "txId": clean_txid,
                "coin": dep_coin,
                "network": dep_network,
                "amount": str(deposited_amount),
                "status": binance_status_code,
                "insertTime": matching_deposit.get("insertTime"),
            }
        expected_amount = to_decimal(checkout.expected_total)

        # Check for underpayment
        if deposited_amount < expected_amount:
            logger.warning(
                "Underpayment detected on checkout %s: received %s, expected %s",
                checkout.id,
                deposited_amount,
                expected_amount,
            )
            # Create payment record as UNDERPAID
            try:
                await self.payment_repo.create(
                    checkout_id=checkout.id,
                    user_id=checkout.user_id,
                    txid=clean_txid,
                    coin=checkout.coin,
                    network=checkout.network,
                    expected_amount=expected_amount,
                    received_amount=deposited_amount,
                    status=CheckoutStatus.UNDERPAID.value,
                    binance_status=str(binance_status_code),
                    verified_at=now,
                    raw_metadata=safe_metadata,
                )
            except IntegrityError:
                # Another request already inserted this TxID - treat as duplicate
                logger.warning("Race condition: TxID %s already exists in DB during UNDERPAID creation", clean_txid)
                await self.session.rollback()
                return VerificationResult.ALREADY_USED_TXID, "This TxID has already been used for another payment. Duplicate payments are not allowed.", None
            await self.checkout_repo.update_status(checkout.id, CheckoutStatus.UNDERPAID)
            diff = expected_amount - deposited_amount
            return (
                VerificationResult.UNDERPAID,
                f"⚠️ <b>Underpayment Detected!</b>\n\n"
                f"Expected: <code>{expected_amount} {checkout.coin}</code>\n"
                f"Received: <code>{deposited_amount} {checkout.coin}</code>\n"
                f"Short by: <code>{diff} {checkout.coin}</code>\n\n"
                f"Your order cannot be fulfilled automatically. Please contact @{settings.SUPPORT_USERNAME} for assistance.",
                safe_metadata,
            )

        # Check for significant overpayment (> 1.00 USDT difference)
        is_overpaid = deposited_amount > expected_amount
        overpayment_diff = deposited_amount - expected_amount
        is_significant_overpayment = overpayment_diff >= Decimal("1.00")

        payment_status = CheckoutStatus.OVERPAID_REVIEW.value if is_significant_overpayment else CheckoutStatus.PAID.value

        # Persist verified payment
        try:
            await self.payment_repo.create(
                checkout_id=checkout.id,
                user_id=checkout.user_id,
                txid=clean_txid,
                coin=checkout.coin,
                network=checkout.network,
                expected_amount=expected_amount,
                received_amount=deposited_amount,
                status=payment_status,
                binance_status=str(binance_status_code),
                verified_at=now,
                raw_metadata=safe_metadata,
            )
        except IntegrityError:
            # Another request already inserted this TxID - treat as duplicate
            logger.warning("Race condition: TxID %s already exists in DB during PAID creation", clean_txid)
            await self.session.rollback()
            return VerificationResult.ALREADY_USED_TXID, "This TxID has already been used for another payment. Duplicate payments are not allowed.", None

        checkout_final_status = CheckoutStatus.OVERPAID_REVIEW if is_significant_overpayment else CheckoutStatus.PAID
        await self.checkout_repo.update_status(checkout.id, checkout_final_status)

        if is_significant_overpayment:
            logger.info("Overpayment on checkout %s: received %s, expected %s", checkout.id, deposited_amount, expected_amount)
            return (
                VerificationResult.OVERPAID_REVIEW,
                f"✅ <b>Payment Verified (Overpayment Recorded)</b>\n\n"
                f"Expected: <code>{expected_amount} {checkout.coin}</code>\n"
                f"Received: <code>{deposited_amount} {checkout.coin}</code>\n"
                f"Overpaid: <code>{overpayment_diff} {checkout.coin}</code>\n\n"
                f"Your order is proceeding to fulfillment. Contact support if you need an overpayment credit.",
                safe_metadata,
            )

        return VerificationResult.PAID, "Payment successfully verified!", safe_metadata
