"""LifeLedger anchoring client — Protocol + Mock + Web3 (Polygon Amoy).

Anchors the passport hash on-chain so a unit's history is tamper-evident.
Mock anchoring (deterministic pseudo tx hash, no chain) keeps the demo safe and
unblocked; flip `USE_REAL_LEDGER=true` with a funded `LIFELEDGER_PRIVATE_KEY` to
write real transactions. web3 is imported lazily so the mock path needs no deps.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from app.core.config import settings


@dataclass
class AnchorResult:
    tx_hash: str
    on_chain: bool


class LedgerClient(Protocol):
    def anchor(self, *, unit_id: str, passport_hash: str) -> AnchorResult: ...


class MockLedger:
    """Deterministic local anchor — same hash in ⇒ same tx out (idempotent demo)."""

    def anchor(self, *, unit_id: str, passport_hash: str) -> AnchorResult:
        digest = hashlib.sha256(f"{unit_id}:{passport_hash}".encode()).hexdigest()
        return AnchorResult(tx_hash="0x" + digest, on_chain=False)


class Web3Ledger:
    def __init__(self) -> None:
        from web3 import Web3  # lazy: only when real anchoring is enabled

        self._w3 = Web3(Web3.HTTPProvider(settings.polygon_rpc_url))
        self._acct = self._w3.eth.account.from_key(settings.lifeledger_private_key)

    def anchor(self, *, unit_id: str, passport_hash: str) -> AnchorResult:
        # Minimal data-tx anchor: write the hash as calldata to self (no contract
        # needed for a demo registry). A deployed registry contract can replace this.
        tx = {
            "from": self._acct.address,
            "to": settings.lifeledger_contract_address or self._acct.address,
            "value": 0,
            "nonce": self._w3.eth.get_transaction_count(self._acct.address),
            "gas": 60000,
            "gasPrice": self._w3.eth.gas_price,
            "chainId": self._w3.eth.chain_id,
            "data": "0x" + passport_hash,
        }
        signed = self._acct.sign_transaction(tx)
        tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
        return AnchorResult(tx_hash=tx_hash.hex(), on_chain=True)


def get_ledger_client() -> LedgerClient:
    if settings.use_real_ledger and settings.lifeledger_private_key:
        return Web3Ledger()
    return MockLedger()
