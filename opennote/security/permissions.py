"""Ordered allow / deny / ask permission engine (opencode-style).

Permissions are checked immediately before a tool is dispatched, so the model
can never perform an action the operator has not authorized. The engine is
deliberately small and dependency-free; the TUI and CLI supply an interactive
*handler* (with Allow once / Allow always / Reject), while non-interactive
callers fail closed — a configured ``deny`` always wins.

Precedence (later rules override earlier ones; last match wins):

1. a conservative base policy
2. ordered rules from ``opennote.json``
3. rules from the active agent's frontmatter ``permission:`` block
4. durable approvals saved by "Allow always"

This module never reads files itself. Config/agent loading is the caller's job.
"""
from __future__ import annotations

import fnmatch
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("opennote.security.permissions")

ALLOW = "allow"
DENY = "deny"
ASK = "ask"
_VALID_EFFECTS = (ALLOW, DENY, ASK)

# --- Built-in action names -------------------------------------------------
READ = "read"
SEARCH = "search"
WEBFETCH = "webfetch"
WEBSEARCH = "websearch"
SKILL = "skill"
SCRIPT = "script"
TASK = "task"
MEMORY = "memory"
PLUGIN = "plugin"
EDIT = "edit"
SHELL = "shell"

TOOL_ACTIONS: Dict[str, str] = {
    "search": SEARCH,
    "list_sources": SEARCH,
    "web_search": WEBSEARCH,
    "read_page": WEBFETCH,
    "skill": SKILL,
    "run_skill_script": SCRIPT,
    "task": TASK,
    "memory_search": MEMORY,
    "submit_grounded_answer": SEARCH,  # internal, never surfaced as a gate
}


@dataclass(frozen=True)
class PermissionRule:
    action: str
    resource: str = "*"
    effect: str = ALLOW
    source: str = "config"

    def to_dict(self) -> Dict[str, str]:
        return {"action": self.action, "resource": self.resource, "effect": self.effect}

    @classmethod
    def from_dict(cls, data: Dict[str, Any], source: str = "config") -> "PermissionRule":
        effect = str(data.get("effect", ALLOW)).strip().lower()
        if effect not in _VALID_EFFECTS:
            raise ValueError(f"Invalid permission effect {effect!r}; expected allow/deny/ask")
        return cls(
            action=str(data.get("action", "*")),
            resource=str(data.get("resource", "*")),
            effect=effect,
            source=source,
        )


def _base_policy() -> List[PermissionRule]:
    """Sensible defaults: reading is fine; side-effectful actions ask; code
    execution and file mutation are denied outright unless explicitly enabled."""
    return [
        PermissionRule("*", "*", ALLOW, source="default"),
        PermissionRule(WEBFETCH, "*", ALLOW, source="default"),
        PermissionRule(WEBSEARCH, "*", ALLOW, source="default"),
        PermissionRule(SCRIPT, "*", ASK, source="default"),
        PermissionRule(PLUGIN, "*", ASK, source="default"),
        PermissionRule(TASK, "*", ASK, source="default"),
        PermissionRule(MEMORY, "*", ASK, source="default"),
        PermissionRule(EDIT, "*", DENY, source="default"),
        PermissionRule(SHELL, "*", DENY, source="default"),
    ]


def normalize_agent_permission(perm: Any, agent_name: str = "agent") -> List[PermissionRule]:
    """Convert an agent's frontmatter ``permission:`` block into rules.

    Accepts either a mapping ``{action: effect}`` / ``{action: [rules]}`` or a
    list of ``{action, resource, effect}`` mappings — matching the shapes people
    naturally write in YAML.
    """
    src = f"agent:{agent_name}"
    rules: List[PermissionRule] = []
    if perm is None:
        return rules
    if isinstance(perm, list):
        for item in perm:
            if isinstance(item, dict):
                try:
                    rules.append(PermissionRule.from_dict(item, source=src))
                except ValueError:
                    continue
        return rules
    if not isinstance(perm, dict):
        return rules
    for action, value in perm.items():
        if isinstance(value, str):
            try:
                rules.append(PermissionRule.from_dict({"action": action, "effect": value}, src))
            except ValueError:
                continue
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    data = dict(item)
                    data.setdefault("action", action)
                    try:
                        rules.append(PermissionRule.from_dict(data, src))
                    except ValueError:
                        continue
        elif isinstance(value, dict):
            data = dict(value)
            data.setdefault("action", action)
            try:
                rules.append(PermissionRule.from_dict(data, src))
            except ValueError:
                continue
    return rules


def _norm(value: str) -> str:
    # Case-insensitive on Windows for path-like resources; harmless elsewhere.
    return os.path.normcase(str(value))


def rule_matches(rule: PermissionRule, action: str, resource: str) -> bool:
    if not fnmatch.fnmatchcase(_norm(action), _norm(rule.action)):
        return False
    pattern = str(rule.resource)
    target = str(resource)
    # opencode convention: "git status *" also matches bare "git status".
    if pattern.endswith(" *") and _norm(target) == _norm(pattern[:-2].strip()):
        return True
    return fnmatch.fnmatchcase(_norm(target), _norm(pattern))


@dataclass
class PermissionRequest:
    action: str
    resource: str = "*"
    tool_name: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)
    agent: str = ""

    def describe(self) -> str:
        lines = [f"Action: {self.action}", f"Resource: {self.resource}"]
        if self.tool_name:
            lines.append(f"Tool: {self.tool_name}")
        if self.agent:
            lines.append(f"Agent: {self.agent}")
        return "\n".join(lines)


@dataclass
class PermissionResponse:
    choice: str  # "once" | "always" | "reject"

    @classmethod
    def parse(cls, value: Any) -> "PermissionResponse":
        text = str(value).strip().lower()
        if text in ("always", "allow always", "allow_always"):
            return cls("always")
        if text in ("reject", "deny", "no", "once_allow_no"):
            return cls("reject")
        return cls("once")  # "once" / "yes" / "allow" / anything else → once


@dataclass
class PermissionDecision:
    effect: str
    rule: Optional[PermissionRule] = None
    message: str = ""
    #: False when an "always" approval could not be written to disk. The
    #: action is still allowed for this session, but the operator must know
    #: the approval will not survive a restart (silent fallback = bug).
    persisted: bool = True

    @property
    def allowed(self) -> bool:
        return self.effect == ALLOW


class PermissionDenied(RuntimeError):
    def __init__(self, request: PermissionRequest, message: str = "Permission denied"):
        self.request = request
        super().__init__(f"{message} ({request.action} on {request.resource})")


PermissionHandler = Callable[[PermissionRequest], PermissionResponse]
ApprovalSaver = Callable[[Dict[str, str]], None]


class PermissionEngine:
    """Evaluate an action against ordered rules, optionally prompting."""

    def __init__(
        self,
        rules: Optional[List[Dict[str, str]]] = None,
        agent_rules: Optional[List[PermissionRule]] = None,
        saved_rules: Optional[List[PermissionRule]] = None,
        handler: Optional[PermissionHandler] = None,
        save_approval: Optional[ApprovalSaver] = None,
    ):
        self.handler = handler
        self.save_approval = save_approval
        self.last_decision: Optional[PermissionDecision] = None
        self.saved_rules: List[PermissionRule] = list(saved_rules or [])
        configured: List[PermissionRule] = []
        for item in rules or []:
            try:
                configured.append(PermissionRule.from_dict(item, source="config"))
            except ValueError:
                continue
        self.rules: List[PermissionRule] = (
            _base_policy() + configured + list(agent_rules or []) + self.saved_rules
        )

    # -- pure evaluation ----------------------------------------------------
    def _match(self, action: str, resource: str) -> Optional[PermissionRule]:
        matched: Optional[PermissionRule] = None
        for rule in self.rules:
            if rule_matches(rule, action, resource):
                matched = rule  # last match wins
        return matched

    def static_effect(self, action: str, resource: str = "*") -> str:
        """Rule-based effect without prompting (``ask`` stays ``ask``).

        Used to hide tools the operator has hard-denied from the model's schema.
        """
        rule = self._match(action, resource)
        return rule.effect if rule else ASK

    def check(
        self,
        request: PermissionRequest,
        *,
        interactive: Optional[bool] = None,
    ) -> PermissionDecision:
        if interactive is None:
            interactive = self.handler is not None
        rule = self._match(request.action, request.resource)
        effect = rule.effect if rule else ASK
        self.last_decision = None
        if effect == ASK:
            if not interactive or self.handler is None:
                self.last_decision = PermissionDecision(
                    DENY,
                    rule,
                    "Approval required but no interactive approver is available.",
                )
                return self.last_decision
            response = PermissionResponse.parse(self.handler(request))
            if response.choice == "reject":
                self.last_decision = PermissionDecision(DENY, rule, "Rejected by user.")
                return self.last_decision
            if response.choice == "always":
                saved = PermissionRule(request.action, request.resource, ALLOW, source="saved")
                self.saved_rules.append(saved)
                self.rules.append(saved)
                persisted = True
                message = "Approved always."
                if self.save_approval is not None:
                    try:
                        self.save_approval(saved.to_dict())
                    except Exception as exc:
                        # A failed save must not turn an approved action into a
                        # denial, but it must be visible (result field + log).
                        persisted = False
                        message = f"Approved for this session; could not persist approval: {exc}"
                        logger.warning("Could not save permission approval: %s", exc)
                self.last_decision = PermissionDecision(ALLOW, saved, message, persisted=persisted)
                return self.last_decision
            self.last_decision = PermissionDecision(ALLOW, rule, "Approved once.")
            return self.last_decision
        source = rule.source if rule else "default"
        self.last_decision = PermissionDecision(effect, rule, f"{effect} by {source}.")
        return self.last_decision

    def require(
        self,
        request: PermissionRequest,
        *,
        interactive: Optional[bool] = None,
    ) -> PermissionDecision:
        decision = self.check(request, interactive=interactive)
        if not decision.allowed:
            raise PermissionDenied(request, decision.message or "Permission denied")
        return decision


# ---------------------------------------------------------------------------
# Tool → permission adapters
# ---------------------------------------------------------------------------

def _resource_for(tool_name: str, kwargs: Dict[str, Any]) -> str:
    if tool_name in ("search", "web_search", "skill", "task", "memory_search"):
        for key in ("query", "name", "subagent", "skill"):
            if kwargs.get(key):
                return str(kwargs[key])
        return "*"
    if tool_name == "read_page":
        return str(kwargs.get("url", "*"))
    if tool_name == "run_skill_script":
        skill = kwargs.get("skill", "")
        script = kwargs.get("script", "")
        return f"{skill}/{script}" if skill or script else "*"
    return tool_name


def build_permission_request(
    tool_name: str,
    kwargs: Optional[Dict[str, Any]] = None,
    agent: str = "",
) -> PermissionRequest:
    kwargs = dict(kwargs or {})
    action = TOOL_ACTIONS.get(tool_name, PLUGIN)
    return PermissionRequest(
        action=action,
        resource=_resource_for(tool_name, kwargs),
        tool_name=tool_name,
        arguments=kwargs,
        agent=agent,
    )
