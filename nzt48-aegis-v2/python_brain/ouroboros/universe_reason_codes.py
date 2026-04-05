"""Universe Reason Codes — Central enum/constants for all universe pipeline decisions.

Every admit/reject/quarantine/publish/rollback/rotation decision uses codes from
this module. Keeps audit trails consistent and diffable across runs.

Usage:
    from python_brain.ouroboros.universe_reason_codes import (
        AdmitReason, RejectReason, QuarantineReason, RetireReason,
        PublishBlockReason, DegradedReason, RotationReason, RollbackReason,
    )
"""
from __future__ import annotations


# ═══════════════════════════════════════════════════════════════════════════════
# Admissibility — why an instrument was ADMITTED
# ═══════════════════════════════════════════════════════════════════════════════
class AdmitReason:
    PASSED_ALL_GATES = "admit:passed_all_gates"
    EMERGENCY_BASELINE = "admit:emergency_baseline"


# ═══════════════════════════════════════════════════════════════════════════════
# Admissibility — why an instrument was REJECTED
# ═══════════════════════════════════════════════════════════════════════════════
class RejectReason:
    # Identity gates
    INVALID_SEC_TYPE = "reject:invalid_sec_type"
    EXCHANGE_NOT_ALLOWED = "reject:exchange_not_allowed"
    CURRENCY_NOT_ALLOWED = "reject:currency_not_allowed"
    DUPLICATE_CROSS_LISTING = "reject:duplicate_cross_listing"

    # Tradability gates
    PRICE_BELOW_MIN = "reject:price_below_min"
    ADV_BELOW_MIN = "reject:adv_below_min"
    TURNOVER_BELOW_MIN = "reject:turnover_below_min"
    HALTED = "reject:halted"
    EXPIRED = "reject:expired"
    DELISTED = "reject:delisted"
    UNSUPPORTED_STATUS = "reject:unsupported_status"

    # Product structure gates
    IS_RIGHT = "reject:is_right"
    IS_WARRANT = "reject:is_warrant"
    IS_PREFERRED = "reject:is_preferred"
    IS_STRUCTURED_NOTE = "reject:is_structured_note"

    # Resolution gates
    CONTRACT_UNRESOLVABLE = "reject:contract_unresolvable"
    CON_ID_MISSING = "reject:con_id_missing"
    STALE_CONTRACT_DETAILS = "reject:stale_contract_details"


# ═══════════════════════════════════════════════════════════════════════════════
# Quarantine — why an instrument was QUARANTINED
# ═══════════════════════════════════════════════════════════════════════════════
class QuarantineReason:
    RESOLUTION_FAILED_REPEATED = "quarantine:resolution_failed_3x"
    BROKER_REJECTION_REPEATED = "quarantine:broker_rejection_repeated"
    DELISTED_DETECTED = "quarantine:delisted_detected"
    ZERO_LIQUIDITY_EXTENDED = "quarantine:zero_liquidity_extended"
    MISSING_BARS_EXTENDED = "quarantine:missing_bars_extended"
    RANKING_IRRELEVANT_EXTENDED = "quarantine:ranking_irrelevant_extended"
    CLAUDE_REVIEW_REQUIRED = "quarantine:claude_review_required"  # advisory only, needs deterministic corroboration
    INVALID_STATUS = "quarantine:invalid_status"


# ═══════════════════════════════════════════════════════════════════════════════
# Retirement — why an instrument was RETIRED
# ═══════════════════════════════════════════════════════════════════════════════
class RetireReason:
    QUARANTINE_EXPIRED = "retire:quarantine_exceeded_threshold"
    DELISTED_CONFIRMED = "retire:delisted_confirmed"
    EXCHANGE_REMOVED = "retire:exchange_removed_from_allowlist"
    UNUSABLE_REPEATED = "retire:unusable_repeated"
    MANUAL = "retire:manual"


# ═══════════════════════════════════════════════════════════════════════════════
# Publish — why a publish was BLOCKED
# ═══════════════════════════════════════════════════════════════════════════════
class PublishBlockReason:
    MAX_ADDITIONS_EXCEEDED = "block:max_additions_exceeded"
    MAX_REMOVALS_EXCEEDED = "block:max_removals_exceeded"
    ACTIVE_DELTA_EXCEEDED = "block:active_universe_delta_exceeded"
    EXCHANGE_COVERAGE_DROP = "block:exchange_coverage_drop"
    SINGLE_EXCHANGE_CONCENTRATION = "block:single_exchange_concentration"
    SINGLE_CURRENCY_CONCENTRATION = "block:single_currency_concentration"
    LEVERAGED_SHARE_EXCEEDED = "block:leveraged_share_exceeded"
    UNRESOLVED_RATIO_EXCEEDED = "block:unresolved_ratio_exceeded"
    WATCHLIST_CHURN_EXCEEDED = "block:watchlist_churn_exceeded"
    TOML_PARSE_INVALID = "block:toml_parse_invalid"
    JSON_SCHEMA_INVALID = "block:json_schema_invalid"
    WATCHLIST_EMPTY = "block:watchlist_empty"
    LIVE_100_EXCEEDED = "block:live_100_exceeded"
    SHORTLIST_EXCEEDED = "block:shortlist_exceeded"
    LIVE_NOT_SUBSET_SHORTLIST = "block:live_not_subset_shortlist"
    LIVE_NOT_SUBSET_ADMISSIBLE = "block:live_not_subset_admissible"
    RUST_ACK_TIMEOUT = "block:rust_ack_timeout"
    RUST_ACK_HASH_MISMATCH = "block:rust_ack_hash_mismatch"


# ═══════════════════════════════════════════════════════════════════════════════
# Degraded — why system is in DEGRADED mode
# ═══════════════════════════════════════════════════════════════════════════════
class DegradedReason:
    IBKR_UNAVAILABLE = "degraded:ibkr_unavailable"
    GEMINI_UNAVAILABLE = "degraded:gemini_unavailable"
    CLAUDE_UNAVAILABLE = "degraded:claude_unavailable"
    PARTIAL_EXCHANGE_FAILURE = "degraded:partial_exchange_failure"
    EMERGENCY_BASELINE_ACTIVE = "degraded:emergency_baseline_active"
    SCANNER_BUSY = "degraded:scanner_busy_client_collision"
    RESOLUTION_BURST_FAILURE = "degraded:resolution_burst_failure"


# ═══════════════════════════════════════════════════════════════════════════════
# Rotation — why an instrument was added/removed from live_100
# ═══════════════════════════════════════════════════════════════════════════════
class RotationReason:
    # Additions to live_100
    HIGH_RANK_ADMISSION = "rotation:high_rank_admission"
    STRATEGY_DEMAND = "rotation:strategy_demand"
    DARK_HORSE_TACTICAL = "rotation:dark_horse_tactical"
    EXCHANGE_OPENING = "rotation:exchange_opening"
    PRE_POSITION_NEXT_SESSION = "rotation:pre_position_next_session"

    # Removals from live_100
    RANK_DECAYED = "rotation:rank_decayed"
    EXCHANGE_CLOSED = "rotation:exchange_closed"
    CHURN_LIMIT_EVICTION = "rotation:churn_limit_eviction"
    REPLACED_BY_HIGHER_RANK = "rotation:replaced_by_higher_rank"
    RESIDENCY_EXPIRED = "rotation:residency_expired"

    # Protection
    STRATEGY_PROTECTED = "rotation:strategy_protected_no_evict"
    MIN_RESIDENCY_HOLD = "rotation:min_residency_hold"
    COOLDOWN_ACTIVE = "rotation:cooldown_active_no_readd"


# ═══════════════════════════════════════════════════════════════════════════════
# Rollback — why a rollback occurred
# ═══════════════════════════════════════════════════════════════════════════════
class RollbackReason:
    RUST_ACK_TIMEOUT = "rollback:rust_ack_timeout"
    RUST_ACK_HASH_MISMATCH = "rollback:rust_ack_hash_mismatch"
    RUST_ACK_COUNT_MISMATCH = "rollback:rust_ack_count_mismatch"
    GUARDRAIL_TRIPPED_LATE = "rollback:guardrail_tripped_late"
    PUBLISH_IO_ERROR = "rollback:publish_io_error"
