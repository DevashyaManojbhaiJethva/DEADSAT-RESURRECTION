"""Isolated tests for privacy, JWT and persistence; no production data is seeded."""
import base64
import hashlib
import hmac
import json
import tempfile
from pathlib import Path

from history_privacy import (
    Disclosure, OperationalContext, Requester, audit_log, current_context,
    filter_event, filter_history, policy,
)
from jwt_auth import JWTValidationError, authenticate_jwt
from privacy_audit import PrivacyAuditStore


AUTH = Requester("test-key", True, frozenset({"history:read", "history:sensitive:read", "history:security:read"}), role="analyst", authentication="jwt")
ANON = Requester("bench", False, frozenset())
NOMINAL = {"timestamp": 1, "frame_id": 1, "norad_id": 28654, "obc_status": "nominal",
           "adcs_status": "nominal", "power_status": "nominal", "comms_status": "nominal"}
SENSITIVE = {**NOMINAL, "frame_id": 2, "fault_injected": "SEU", "fault_detail": {"register": "0x3F"},
             "obc_register": "0x3F", "adcs_quaternion": [1, 2, 3, 4], "comms_uplink": True}
SECURITY = {**SENSITIVE, "frame_id": 3, "fault_injected": "command_injection"}


def test_normal_history_is_full():
    assert filter_event(NOMINAL, AUTH, "monitoring", current_context(NOMINAL))["access"] == "FULL"


def test_sensitive_event_passes_through_policy():
    assert policy.classify_sensitivity(SENSITIVE) == "SENSITIVE"


def test_context_changes_decision_for_same_event():
    active = OperationalContext("active_fault", "SEU")
    nominal = OperationalContext("nominal", None)
    assert policy.evaluate(AUTH, "investigation", active, SENSITIVE).disclosure is Disclosure.FULL
    assert policy.evaluate(AUTH, "investigation", nominal, SENSITIVE).disclosure is Disclosure.LIMITED


def test_intent_changes_disclosure_for_same_event():
    context = OperationalContext("active_fault", "SEU")
    assert policy.evaluate(AUTH, "recovery", context, SENSITIVE).disclosure is Disclosure.FULL
    assert policy.evaluate(AUTH, "audit", context, SENSITIVE).disclosure is Disclosure.SUMMARY


def test_limited_response_removes_sensitive_fields_server_side():
    result = filter_event(SENSITIVE, AUTH, "monitoring", OperationalContext("nominal", None))
    assert result["access"] == "LIMITED"
    assert "fault_detail" not in result and "obc_register" not in result and "adcs_quaternion" not in result


def test_unauthenticated_history_is_denied_for_every_sensitivity():
    context = OperationalContext("active_fault", "command_injection")
    assert filter_event(NOMINAL, ANON, "monitoring", context) is None
    assert filter_event({**NOMINAL, "obc_status": "degraded"}, ANON, "monitoring", context) is None
    assert filter_event(SECURITY, ANON, "monitoring", context) is None


def test_authenticated_public_history_is_full_and_no_permission_is_denied():
    assert filter_event(NOMINAL, AUTH, "monitoring", current_context(NOMINAL))["access"] == "FULL"
    no_history = Requester("limited", True, frozenset(), role="viewer", authentication="jwt")
    assert filter_event(NOMINAL, no_history, "monitoring", current_context(NOMINAL)) is None


def test_rest_style_collection_has_no_raw_bypass():
    result = filter_history([NOMINAL, SECURITY], AUTH, "monitoring", OperationalContext("nominal", None))
    assert result[1]["access"] == "REDACTED"
    assert "fault_detail" not in result[1]


def test_websocket_style_per_frame_filter_has_no_raw_bypass():
    result = filter_event(SECURITY, AUTH, "security_analysis", OperationalContext("active_fault", "command_injection"))
    assert result["access"] == "LIMITED"
    assert "fault_detail" not in result


def test_privacy_decisions_are_audited_without_payload():
    with tempfile.TemporaryDirectory() as directory:
        audit_log.configure(PrivacyAuditStore(Path(directory) / "audit.sqlite3"))
        before = len(audit_log.records())
        filter_event(SENSITIVE, AUTH, "monitoring", OperationalContext("nominal", None))
        record = audit_log.records()[-1]
        assert len(audit_log.records()) == before + 1
        assert record["event_id"] == "2" and record["decision"] == "LIMITED"
        assert "fault_detail" not in record
    audit_log.configure(None)


def _token(claims, secret="test-secret"):
    encode = lambda value: base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).rstrip(b"=").decode()
    body = f"{encode({'alg': 'HS256', 'typ': 'JWT'})}.{encode(claims)}"
    signature = base64.urlsafe_b64encode(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
    return f"{body}.{signature}"


def test_valid_jwt_identity_and_permissions_are_verified():
    requester = authenticate_jwt(_token({"sub": "operator-42", "role": "security_analyst", "permissions": ["history:read", "history:security:read"], "iat": 1, "exp": 9999999999}), secret="test-secret", now=2)
    assert requester.identifier == "operator-42" and requester.role == "security_analyst"
    assert "history:security:read" in requester.permissions and requester.authentication == "jwt"


def test_expired_and_tampered_jwts_are_rejected():
    expired = _token({"sub": "operator-42", "role": "viewer", "permissions": [], "exp": 1})
    try:
        authenticate_jwt(expired, secret="test-secret", now=2)
        assert False, "expired token accepted"
    except JWTValidationError:
        pass


def test_malformed_jwt_and_wrong_token_type_are_rejected():
    for token in ("not-a-token", "a.b.c", _token({"sub": "operator", "exp": 99, "permissions": []}, secret="other")):
        try:
            authenticate_jwt(token, secret="test-secret", now=2)
            assert False, "invalid token accepted"
        except JWTValidationError:
            pass
    websocket = _token({"sub": "operator", "role": "viewer", "permissions": [], "typ": "websocket", "exp": 99})
    assert authenticate_jwt(websocket, secret="test-secret", now=2, expected_token_type="websocket").identifier == "operator"
    token = _token({"sub": "operator-42", "role": "viewer", "permissions": ["history:read"], "exp": 9999999999})
    parts = token.split(".")
    parts[1] = ("A" if parts[1][0] != "A" else "B") + parts[1][1:]
    tampered = ".".join(parts)
    try:
        authenticate_jwt(tampered, secret="test-secret", now=2)
        assert False, "tampered token accepted"
    except JWTValidationError:
        pass


def test_permissions_cause_a_different_legitimate_privacy_decision():
    limited_user = Requester("viewer", True, frozenset({"history:read"}), role="viewer", authentication="jwt")
    context = OperationalContext("active_fault", "SEU")
    assert policy.evaluate(AUTH, "investigation", context, SENSITIVE).disclosure is Disclosure.FULL
    assert policy.evaluate(limited_user, "investigation", context, SENSITIVE).disclosure is Disclosure.LIMITED


def test_audit_store_survives_reinitialization():
    before = len(audit_log.records())
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "audit.sqlite3"
        first = PrivacyAuditStore(path)
        first.record({"timestamp": "2026-01-01T00:00:00Z", "requester": "test", "role": "viewer", "event_id": 7, "intent": "monitoring", "context": "nominal", "sensitivity": "SENSITIVE", "decision": "LIMITED", "reason": "test"})
        assert PrivacyAuditStore(path).records()[0]["event_id"] == "7"
