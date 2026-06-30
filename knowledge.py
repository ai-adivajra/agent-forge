from dataclasses import dataclass, field
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Controlled vocabulary for categories
# ---------------------------------------------------------------------------

ALLOWED_CATEGORIES: set[str] = {
    "Configuration",
    "Installation",
    "Troubleshooting",
    "Workflow",
    "Tool",
    "Capability",
    "Model",
    "Integration",
    "Command",
}

FALLBACK_CATEGORY = "Unknown"


ALLOWED_DOMAINS: set[str] = {
    "openclaw", "workstation", "gaming",
    "datacenter", "development", "general",
}

ALLOWED_PLATFORMS: set[str] = {
    "fedora", "linux", "windows", "macos", "any",
}

ALLOWED_TYPES: set[str] = {
    "observation",   # a confirmed fact or behaviour
    "procedure",     # a validated step-by-step workflow
    "incident",      # a troubleshooting event with outcome
    "capability",    # something the system can or cannot do
    "configuration", # a known-good config state
    "general",       # doesn't fit the above
}


def _normalise_domain(raw: str) -> str:
    v = raw.strip().lower()
    return v if v in ALLOWED_DOMAINS else "general"


def _normalise_platform(raw: str) -> str:
    v = raw.strip().lower()
    return v if v in ALLOWED_PLATFORMS else "linux"


def _normalise_type(raw: str) -> str:
    v = raw.strip().lower()
    return v if v in ALLOWED_TYPES else "general"


def normalise_category(raw: str) -> str:
    """
    Normalise the LLM's category string to the controlled vocabulary.

    Accepts any capitalisation ("configuration", "CONFIGURATION", etc.).
    Returns FALLBACK_CATEGORY if the value is not in the allowed set.
    """
    if not raw:
        return FALLBACK_CATEGORY

    # Title-case match (handles "configuration" → "Configuration")
    candidate = raw.strip().title()
    if candidate in ALLOWED_CATEGORIES:
        return candidate

    # Exact match as a fallback (covers "Tool" already title-cased)
    if raw.strip() in ALLOWED_CATEGORIES:
        return raw.strip()

    return FALLBACK_CATEGORY


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class KnowledgeCandidate:
    """
    One reusable knowledge fragment extracted from an OpenClaw session.
    Fields mirror the JSON schema returned by the LLM (see prompts.py).
    """

    title:      str
    category:   str           # normalised via normalise_category()
    summary:    str
    confidence: int           # 0–100 integer

    domain:     str = "general"      # openclaw | workstation | gaming | datacenter | development | general
    platform:   str = "linux"        # fedora | linux | windows | macos | any
    type:       str = "general"      # observation | procedure | incident | capability | configuration | general

    tool_calls: list[str] = field(default_factory=list)   # OpenClaw tools
    commands:   list[str] = field(default_factory=list)   # shell commands
    files:      list[str] = field(default_factory=list)
    models:     list[str] = field(default_factory=list)
    plugins:    list[str] = field(default_factory=list)
    tags:       list[str] = field(default_factory=list)
    reason:     str       = ""    # LLM's one-line justification
    notes:      str       = ""

    # Set automatically when saving
    created_at:     str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    source_session: str = ""

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict, source_session: str = "") -> "KnowledgeCandidate":
        """Build a KnowledgeCandidate from a raw LLM JSON dict."""

        # confidence: accept int or float, clamp to 0–100
        raw_conf = data.get("confidence", 0)
        try:
            confidence = max(0, min(100, int(float(raw_conf))))
        except (TypeError, ValueError):
            confidence = 0

        return cls(
            title          = data.get("title",      "Untitled"),
            category       = normalise_category(data.get("category", "")),
            summary        = data.get("summary",    ""),
            confidence     = confidence,
            domain         = _normalise_domain(data.get("domain",   "general")),
            platform       = _normalise_platform(data.get("platform", "linux")),
            type           = _normalise_type(data.get("type",     "general")),
            tool_calls     = data.get("tool_calls", []),
            commands       = data.get("commands",   []),
            files          = data.get("files",      []),
            models         = data.get("models",     []),
            plugins        = data.get("plugins",    []),
            tags           = data.get("tags",       []),
            reason         = data.get("reason",     ""),
            notes          = data.get("notes",      ""),
            source_session = source_session,
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "title":          self.title,
            "category":       self.category,
            "summary":        self.summary,
            "confidence":     self.confidence,
            "domain":         self.domain,
            "platform":       self.platform,
            "type":           self.type,
            "tool_calls":     self.tool_calls,
            "commands":       self.commands,
            "files":          self.files,
            "models":         self.models,
            "plugins":        self.plugins,
            "tags":           self.tags,
            "reason":         self.reason,
            "notes":          self.notes,
            "created_at":     self.created_at,
            "source_session": self.source_session,
        }
