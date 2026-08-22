"""Context-aware, server-side access control for emulator history.

This module deliberately owns no history.  It evaluates the live emulator
frames supplied by callers, so its audit records can never become a second
source of telemetry or a source of seeded demonstration records.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable


class Disclosure(str, Enum):
    FULL = "FULL"
    LIMITED = "LIMITED"
    SUMMARY = "SUMMARY"
    REDACTED = "REDACTED"
    DENIED = "DENIED"


VALID_INTENTS = {
    "monitoring", "investigation", "incident_response", "recovery",
    "security_analysis", "audit", "general_review",
}


@dataclass(frozen=True)
class Requester:
    """Verified requester attributes; JWT is preferred over legacy API keys."""
    identifier: str
    authenticated: bool
    permissions: frozenset[str]
    role: str = ""
    authentication: str = "legacy"


@dataclass(frozen=True)
class OperationalContext:
    state: str
    active_fault: str | None


@dataclass(frozen=True)
class PolicyDecision:
    disclosure: Disclosure
    sensitivity: str
    reason: str


class HistoryAccessPolicy:
    """Minimum-necessary disclosure policy for real telemetry frames."""

    def classify_sensitivity(self, event: dict[str, Any]) -> str:
        fault = str(event.get("fault_injected") or "").lower()
        if fault == "command_injection":
            return "SECURITY_SENSITIVE"
        if fault or any(event.get(f"{part}_status") == "fault"
                    for part in ("obc", "adcs", "power", "comms")):
            return "SENSITIVE"
        if any(event.get(f"{part}_status") == "degraded"
               for part in ("obc", "adcs", "power", "comms")):
            return "OPERATIONAL"
        return "PUBLIC"

    def evaluate(self, requester: Requester, intent: str,
                 context: OperationalContext, event: dict[str, Any]) -> PolicyDecision:
        sensitivity = self.classify_sensitivity(event)
        relevant = bool(context.active_fault and
                        context.active_fault == event.get("fault_injected"))
        # History is authenticated-only, including routine/public frames. This
        # prevents a ring-buffer endpoint from becoming anonymous operational
        # reconnaissance as classifications evolve.
        if not requester.authenticated:
            return PolicyDecision(Disclosure.DENIED, sensitivity,
                                  "authentication is required for historical telemetry")
        if "history:read" not in requester.permissions:
            return PolicyDecision(Disclosure.DENIED, sensitivity,
                                  "history access is not permitted for this requester")
        if sensitivity == "PUBLIC":
            return PolicyDecision(Disclosure.FULL, sensitivity, "routine telemetry")
        if sensitivity == "OPERATIONAL":
            return PolicyDecision(Disclosure.FULL, sensitivity, "operational telemetry")
        if sensitivity == "SENSITIVE":
            if ("history:sensitive:read" in requester.permissions and relevant and
                    intent in {"investigation", "incident_response", "recovery"}):
                return PolicyDecision(Disclosure.FULL, sensitivity, "active relevant incident")
            if intent in {"audit", "general_review"}:
                return PolicyDecision(Disclosure.SUMMARY, sensitivity,
                                      "detailed incident data is not required for this intent")
            return PolicyDecision(Disclosure.LIMITED, sensitivity,
                                  "some information is not required for the current context")
        # Security-sensitive command-intrusion data is never sent in full via
        # this generic history feed, even to a valid shared-key client.
        if relevant and intent == "security_analysis" and "history:security:read" in requester.permissions:
            return PolicyDecision(Disclosure.LIMITED, sensitivity,
                                  "security details have been minimized")
        if intent in {"audit", "general_review"}:
            return PolicyDecision(Disclosure.SUMMARY, sensitivity,
                                  "security incident detail is not required for this intent")
        return PolicyDecision(Disclosure.REDACTED, sensitivity,
                              "security-sensitive fields are restricted")


_SAFE_FIELDS = {
    "timestamp", "frame_id", "norad_id", "obc_temp_c", "obc_error_count",
    "obc_cpu_pct", "obc_memory_pct", "obc_status", "adcs_rate_deg_s",
    "adcs_wheel_rpm", "adcs_pointing_err_deg", "adcs_status", "power_w",
    "battery_pct", "bus_voltage_v", "power_charging", "power_status",
    "signal_strength_dbm", "comms_status", "beacon_active", "fault_injected",
}
_LIMITED_REMOVE = {"fault_detail", "obc_register", "adcs_quaternion", "comms_uplink", "comms_downlink"}


class PrivacyAuditLog:
    """Audit facade backed by the persistent store configured during startup."""
    def __init__(self) -> None:
        self._store: Any = None

    def configure(self, store: Any) -> None:
        self._store = store

    def record(self, requester: Requester, intent: str, context: OperationalContext,
               event: dict[str, Any], decision: PolicyDecision) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "requester": requester.identifier,
            "role": requester.role,
            "event_id": event.get("frame_id"),
            "intent": intent,
            "context": context.state,
            "sensitivity": decision.sensitivity,
            "decision": decision.disclosure.value,
            "reason": decision.reason,
        }
        if self._store is not None:
            self._store.record(record)

    def records(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._store.records(limit) if self._store is not None else []


policy = HistoryAccessPolicy()
audit_log = PrivacyAuditLog()


def current_context(frame: dict[str, Any]) -> OperationalContext:
    fault = frame.get("fault_injected")
    if fault:
        return OperationalContext("active_fault", str(fault))
    if any(frame.get(f"{part}_status") in {"fault", "degraded"}
           for part in ("obc", "adcs", "power", "comms")):
        return OperationalContext("degraded", None)
    return OperationalContext("nominal", None)


def filter_event(historical_event: dict[str, Any], requester: Requester, intent: str,
                 current_request_context: OperationalContext, *, audit: bool = True) -> dict[str, Any] | None:
    """Filter immutable historical data using separately-derived current state."""
    decision = policy.evaluate(requester, intent, current_request_context, historical_event)
    if audit:
        audit_log.record(requester, intent, current_request_context, historical_event, decision)
    if decision.disclosure is Disclosure.DENIED:
        return None
    if decision.disclosure is Disclosure.FULL:
        result = dict(historical_event)
    elif decision.disclosure is Disclosure.LIMITED:
        result = {key: value for key, value in historical_event.items() if key not in _LIMITED_REMOVE}
    elif decision.disclosure is Disclosure.SUMMARY:
        result = {key: historical_event[key] for key in ("timestamp", "frame_id", "norad_id", "fault_injected") if key in historical_event}
        result["status"] = "historical anomaly" if historical_event.get("fault_injected") else "operational event"
    else:  # REDACTED
        result = {key: historical_event[key] for key in ("timestamp", "frame_id", "norad_id") if key in historical_event}
        result["status"] = "restricted historical event"
    result.update({"access": decision.disclosure.value, "sensitivity": decision.sensitivity})
    if decision.disclosure is not Disclosure.FULL:
        result["access_message"] = "Some information is restricted because it is not required for the current request context."
    return result


def filter_history(events: Iterable[dict[str, Any]], requester: Requester,
                   intent: str, context: OperationalContext) -> list[dict[str, Any]]:
    return [item for event in events if (item := filter_event(event, requester, intent, context)) is not None]
