"""Tests for the ordered allow/deny/ask permission engine."""
import pytest

from opennote.security.permissions import (
    ALLOW,
    ASK,
    DENY,
    SCRIPT,
    WEBFETCH,
    PermissionDenied,
    PermissionEngine,
    PermissionRequest,
    PermissionResponse,
    build_permission_request,
    normalize_agent_permission,
    rule_matches,
)


def test_base_policy_defaults():
    eng = PermissionEngine()
    assert eng.static_effect("read", "notes.txt") == ALLOW
    assert eng.static_effect(WEBFETCH, "https://example.com") == ALLOW
    assert eng.static_effect(SCRIPT, "skill/foo.py") == ASK
    assert eng.static_effect("edit", "*") == DENY


def test_last_matching_rule_wins():
    eng = PermissionEngine(
        rules=[
            {"action": "*", "resource": "*", "effect": "deny"},
            {"action": "webfetch", "resource": "example.com/*", "effect": "allow"},
        ]
    )
    assert eng.static_effect("webfetch", "example.com/a") == ALLOW
    assert eng.static_effect("webfetch", "other.com/a") == DENY


def test_wildcard_matching():
    assert rule_matches(_r("script", "diagrams/*", ALLOW), "script", "diagrams/foo.py")
    assert rule_matches(_r("script", "diagrams/*", ALLOW), "script", "diagrams/") is True
    assert rule_matches(_r("git status *", "*", ALLOW), "git status", "*") is False
    # shell-style "ending in ' *'" matches the bare command too
    r = _r("shell", "git status *", ALLOW)
    assert rule_matches(r, "shell", "git status")
    assert rule_matches(r, "shell", "git status --short")


def _r(action, resource, effect):
    from opennote.security.permissions import PermissionRule

    return PermissionRule(action=action, resource=resource, effect=effect)


def test_agent_permission_normalization():
    rules = normalize_agent_permission({"script": "deny", "skill": "allow"}, "reviewer")
    effects = {r.action: r.effect for r in rules}
    assert effects["script"] == DENY
    assert effects["skill"] == ALLOW
    assert all(r.source == "agent:reviewer" for r in rules)

    listed = normalize_agent_permission(
        [{"action": "webfetch", "resource": "*.evil.com", "effect": "deny"}], "x"
    )
    assert listed[0].resource == "*.evil.com"


def test_handler_once_allow():
    seen = []

    def handler(req):
        seen.append(req)
        return "once"

    eng = PermissionEngine(handler=handler)
    decision = eng.check(PermissionRequest(SCRIPT, "foo.py", "run_skill_script"))
    assert decision.allowed
    assert len(eng.saved_rules) == 0
    assert seen and seen[0].action == SCRIPT


def test_handler_always_saves_rule():
    saved = []

    def handler(req):
        return "always"

    eng = PermissionEngine(handler=handler, save_approval=saved.append)
    req = PermissionRequest(SCRIPT, "foo.py", "run_skill_script")
    assert eng.check(req).allowed
    assert len(eng.saved_rules) == 1
    assert saved == [{"action": SCRIPT, "resource": "foo.py", "effect": ALLOW}]
    # subsequent checks see the saved allow
    assert eng.static_effect(SCRIPT, "foo.py") == ALLOW


def test_handler_reject_denies():
    eng = PermissionEngine(handler=lambda req: "reject")
    decision = eng.check(PermissionRequest(SCRIPT, "x"))
    assert not decision.allowed
    with pytest.raises(PermissionDenied):
        eng.require(PermissionRequest(SCRIPT, "x"))


def test_no_interactive_approver_fails_closed():
    eng = PermissionEngine()  # no handler
    decision = eng.check(PermissionRequest(SCRIPT, "x"))
    assert not decision.allowed
    assert "no interactive approver" in decision.message.lower()
    # explicit allow still works non-interactively
    eng2 = PermissionEngine(rules=[{"action": SCRIPT, "resource": "*", "effect": "allow"}])
    assert eng2.require(PermissionRequest(SCRIPT, "x")).allowed


def test_denied_effect_never_calls_handler():
    called = []
    eng = PermissionEngine(
        rules=[{"action": SCRIPT, "resource": "*", "effect": "deny"}],
        handler=lambda req: called.append(req) or "once",
    )
    decision = eng.check(PermissionRequest(SCRIPT, "x"))
    assert not decision.allowed
    assert called == []


def test_build_permission_request_resources():
    q = build_permission_request("search", {"query": "hello"})
    assert q.action == "search" and q.resource == "hello"
    u = build_permission_request("read_page", {"url": "https://example.com/a"})
    assert u.action == WEBFETCH and u.resource == "https://example.com/a"
    s = build_permission_request("run_skill_script", {"skill": "diagrams", "script": "a.py"})
    assert s.action == SCRIPT and s.resource == "diagrams/a.py"
    p = build_permission_request("some_plugin_tool", {})
    assert p.action == "plugin" and p.resource == "some_plugin_tool"


def test_permission_response_parse():
    assert PermissionResponse.parse("always").choice == "always"
    assert PermissionResponse.parse("REJECT").choice == "reject"
    assert PermissionResponse.parse("yes").choice == "once"
