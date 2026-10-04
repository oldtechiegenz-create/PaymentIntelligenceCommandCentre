"""Pydantic request models for the live ingestion + simulation control endpoints.

Validation happens here (whitelists, payload shape) so malformed input never
reaches engine/apply_event.py or the database.
"""
from __future__ import annotations

import json
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

MAX_RAW_PAYLOAD_BYTES = 65536

# The 10 ISO 20022 message types in scope (see ref_iso_message_type; excludes
# head.001, which is a header carried alongside every CBPR+ message, not a
# message type in its own right).
ALLOWED_MESSAGE_TYPES = {
    "pain.001", "pacs.008", "pacs.009", "pacs.002", "pacs.004",
    "camt.052", "camt.053", "camt.054", "camt.056", "camt.029",
}

# ref_payment_state.state \u2014 the state machine's 13 authoritative states.
ALLOWED_STATES = {
    "INITIATED", "ACCEPTED", "SCREENING", "SENT", "IN_TRANSIT",
    "CORRESPONDENT_PROCESSING", "SETTLEMENT_PENDING", "COMPLETED",
    "REJECTED", "RETURNED", "INVESTIGATION", "FAILED", "CANCELLED",
}

ALLOWED_SPEED_CODES = {"1x", "2x", "5x", "10x"}


class NewPaymentFields(BaseModel):
    """Identity fields required to create a brand-new payment on a payment_id-less push.

    Only the fields with no sensible default are required; everything else
    falls back to a plain default in pipeline.py (fx_rate=1, credit_ccy=debit_ccy, etc).
    """

    domain: str
    rail: str
    payment_type: str
    settlement_method: str
    legal_entity_id: Optional[str] = None  # defaults via generator.legal_entity_for() if omitted
    debtor: str
    creditor: str
    amount: float = Field(gt=0)
    debit_ccy: str
    credit_ccy: Optional[str] = None
    initiated_at: str
    settlement_date: Optional[str] = None
    business_msg_id: Optional[str] = None
    instruction_id: Optional[str] = None
    end_to_end_id: Optional[str] = None
    uetr: Optional[str] = None
    scheme: Optional[str] = None
    country: Optional[str] = None
    purpose: Optional[str] = None
    address_format: Optional[str] = None
    priority: str = "NORM"
    clearing_system: Optional[str] = None
    ultimate_debtor: Optional[str] = None
    debtor_account: Optional[str] = None
    debtor_country: Optional[str] = None
    ultimate_creditor: Optional[str] = None
    creditor_account: Optional[str] = None
    creditor_country: Optional[str] = None
    debtor_agent: Optional[str] = None
    correspondent: Optional[str] = None
    intermediary: Optional[str] = None
    creditor_agent: Optional[str] = None
    bic: Optional[str] = None
    segment: Optional[str] = None
    payment_initiation_dept: Optional[str] = None
    owner: Optional[str] = None
    nostro: Optional[str] = None


class IngestEventRequest(BaseModel):
    """One pushed ISO-shaped event: a target state plus descriptive ISO metadata.

    `state` drives the engine (engine/apply_event.py already operates in terms
    of the 13 state-machine states, not raw ISO status codes \u2014 the same
    iso_tx_status is shared by several states, e.g. ACSP by SENT/IN_TRANSIT/
    CORRESPONDENT_PROCESSING/SETTLEMENT_PENDING, so it can't be reversed back
    to a unique state on its own). `msg_type`/`tx_sts`/`reason_code`/
    `orgnl_*` are descriptive ISO fields carried through to the event's
    `iso_message` display string and `iso_payload` audit blob.
    """

    payment_id: Optional[str] = None
    state: str
    msg_type: str
    tx_sts: Optional[str] = None
    reason_code: Optional[str] = None
    orgnl_end_to_end_id: Optional[str] = None
    orgnl_uetr: Optional[str] = None
    description: Optional[str] = None
    failure_injected: bool = False
    new_payment: Optional[NewPaymentFields] = None

    @field_validator("state")
    @classmethod
    def validate_state(cls, v: str) -> str:
        if v not in ALLOWED_STATES:
            raise ValueError(f"Unknown state {v!r}; must be one of {sorted(ALLOWED_STATES)}")
        return v

    @field_validator("msg_type")
    @classmethod
    def validate_msg_type(cls, v: str) -> str:
        if v not in ALLOWED_MESSAGE_TYPES:
            raise ValueError(f"Unknown msg_type {v!r}; must be one of {sorted(ALLOWED_MESSAGE_TYPES)}")
        return v

    @model_validator(mode="after")
    def validate_new_payment_required(self) -> "IngestEventRequest":
        if self.payment_id is None and self.new_payment is None:
            raise ValueError("new_payment is required when payment_id is omitted")
        return self


class SimStartRequest(BaseModel):
    scenario_code: str
    speed_code: str = "1x"

    @field_validator("speed_code")
    @classmethod
    def validate_speed_code(cls, v: str) -> str:
        if v not in ALLOWED_SPEED_CODES:
            raise ValueError(f"Unknown speed_code {v!r}; must be one of {sorted(ALLOWED_SPEED_CODES)}")
        return v


def validate_payload_size(payload: dict) -> None:
    size = len(json.dumps(payload))
    if size > MAX_RAW_PAYLOAD_BYTES:
        raise ValueError(f"payload too large ({size} bytes, max {MAX_RAW_PAYLOAD_BYTES})")
