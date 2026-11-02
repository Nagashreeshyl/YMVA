"""Agent configuration: the versioned JSON stored in AgentVersion.config."""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from ymva.voice.languages import LANGUAGES

SARVAM_VOICES = (
    "priya", "ritu", "neha", "pooja", "simran", "kavya", "ishita", "shreya", "roopa",
    "amelia", "sophia", "shubh", "aditya", "rahul", "rohan", "amit", "dev", "ratan",
    "varun", "manan", "sumit", "kabir", "aayan", "ashutosh", "advait",
)  # fmt: skip
GEMINI_LIVE_VOICES = ("Aoede", "Kore", "Leda", "Zephyr", "Puck", "Charon", "Fenrir", "Orus")

# Checked against Google's API on 25 Oct 2026: older models (e.g. gemini-2.5-flash) are closed
# to new accounts. Fastest first.
# Only models whose thinking can be switched off: a voice agent must answer straight away.
LLM_MODELS = (
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.6-flash",
)
# Measured from the caller going quiet to the agent's first sound (thinking off):
# gemini-3.8-live 0.7-0.9 s, gemini-3.1-flash-live-preview 0.7-1.1 s, 2.5 native audio 3-5 s.
GEMINI_LIVE_MODELS = ("models/gemini-3.8-live",)
RETIRED_LIVE_MODELS = {
    # Preview; 3.8 Live is Google's generally available, recommended low-latency voice model.
    "models/gemini-3.1-flash-live-preview": "models/gemini-3.8-live",
    "models/gemini-2.5-flash-native-audio-latest": "models/gemini-3.8-live",
    "models/gemini-2.5-flash-native-audio-preview-12-2025": "models/gemini-3.8-live",
    "models/gemini-2.5-flash-native-audio-preview-09-2025": "models/gemini-3.8-live",
    # Always reasons before speaking; far too slow for a phone call.
    "models/gemini-3.8-live-extended-thinking": "models/gemini-3.8-live",
}
RETIRED_LLM_MODELS = {
    "gemini-2.5-flash": "gemini-3.5-flash-lite",
    "gemini-2.5-flash-lite": "gemini-3.5-flash-lite",
    # Can't run with thinking off (its lowest level is "low").
    "gemini-3.8-flash": "gemini-3.5-flash-lite",
}

VARIABLE_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


class Variable(BaseModel):
    """A per-call value the agent uses, e.g. customer_name or due_amount."""

    name: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$", max_length=40)
    description: str = Field(default="", max_length=200)
    example: str = Field(default="", max_length=120)


class Announcement(BaseModel):
    enabled: bool = False
    text: str = Field(
        default="This call may be recorded for quality and training purposes.", max_length=500
    )
    # Optional versions per language code; the greeting language's version is spoken first.
    translations: dict[str, str] = Field(default_factory=dict)


class Transfer(BaseModel):
    """Hand the call to a person when the caller asks for one (phone calls only)."""

    enabled: bool = False
    # "branch_manager": the manager of the customer's branch (the call's "branch" detail,
    # see Settings > Branches); `phone` is then used for customers whose branch isn't listed.
    to: Literal["number", "branch_manager"] = "number"
    phone: str = Field(default="", max_length=20)  # E.164, e.g. +918012345678
    when: str = Field(
        default="The caller asks to speak to a person, or needs help you can't give",
        max_length=500,
    )

    @property
    def number(self) -> str | None:
        """The phone number in E.164, or None until a complete one is entered (drafts save
        while typing, so an unfinished number isn't an error)."""
        v = "".join(ch for ch in self.phone if ch.isdigit() or ch == "+")
        if len(v) == 10 and not v.startswith("+"):  # Indian number without the country code
            v = "+91" + v
        return v if re.fullmatch(r"\+\d{11,15}", v) else None


class PipelineSettings(BaseModel):
    stt_provider: Literal["sarvam"] = "sarvam"
    llm_provider: Literal["google"] = "google"
    llm_model: str = Field(default="gemini-3.5-flash-lite", max_length=80)
    # Used automatically if the main model fails during a call.
    backup_llm_model: str = Field(default="gemini-3.1-flash-lite", max_length=80)
    tts_provider: Literal["sarvam"] = "sarvam"
    voice: str = "priya"
    speaking_pace: float = Field(default=1.0, ge=0.6, le=1.6)
    temperature: float = Field(default=0.4, ge=0.0, le=1.5)

    @field_validator("llm_model", "backup_llm_model")
    @classmethod
    def _model(cls, v: str) -> str:
        return RETIRED_LLM_MODELS.get(v, v)

    @field_validator("voice")
    @classmethod
    def _voice(cls, v: str) -> str:
        if v not in SARVAM_VOICES:
            raise ValueError(f"Unknown Sarvam voice '{v}'")
        return v


class GeminiLiveSettings(BaseModel):
    @field_validator("model")
    @classmethod
    def _model(cls, v: str) -> str:
        return RETIRED_LIVE_MODELS.get(v, v)

    model: str = Field(default="models/gemini-3.8-live", max_length=120)
    voice: str = "Aoede"
    temperature: float = Field(default=0.6, ge=0.0, le=1.5)

    @field_validator("voice")
    @classmethod
    def _voice(cls, v: str) -> str:
        if v not in GEMINI_LIVE_VOICES:
            raise ValueError(f"Unknown Gemini Live voice '{v}'")
        return v


class Atmosphere(BaseModel):
    """Sounds mixed under the agent's voice, only where they make the call feel more human."""

    # A real call-centre murmur under the whole call.
    room_tone: bool = True
    # Faint typing only while the agent is using a tool (looking something up, saving).
    typing_while_thinking: bool = True
    volume: float = Field(default=0.6, ge=0.0, le=1.0)


class TrackedItem(BaseModel):
    """An outcome or detail read from the transcript after the call, named in plain words."""

    name: str = Field(max_length=60)
    description: str = Field(default="", max_length=300)


class Analysis(BaseModel):
    """What to write down after every call: a summary, one outcome and the details asked for."""

    # Every agent starts with the same payment-call set, so outcomes add up across agents.
    outcomes: list[TrackedItem] = Field(
        default_factory=lambda: [
            TrackedItem(name="Promised to pay", description="Gave a date to pay the interest"),
            TrackedItem(name="Already paid", description="Says the interest is already paid"),
            TrackedItem(
                name="Needs help", description="Facing difficulty or wants a manager to call"
            ),
            TrackedItem(name="Call back later", description="Asked to talk at another time"),
            TrackedItem(name="Wrong person", description="Not the customer"),
            TrackedItem(name="Refused", description="Won't pay or ended the call"),
        ],
        max_length=20,
    )
    details: list[TrackedItem] = Field(
        default_factory=lambda: [
            TrackedItem(
                name="Promised payment date", description="The date they will pay, as YYYY-MM-DD"
            ),
            TrackedItem(name="Paid on", description="If already paid: when, as YYYY-MM-DD"),
            TrackedItem(name="Call back at", description="When to call back, if they asked"),
            TrackedItem(name="Reason for difficulty", description="Why they can't pay, in brief"),
        ],
        max_length=20,
    )

    # Tools (e.g. a Sheets row, a CRM webhook) that receive the results after every call.
    send_to: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("outcomes", "details")
    @classmethod
    def _unique_names(cls, items: list[TrackedItem]) -> list[TrackedItem]:
        # A row still being typed in the editor has no name yet; it's simply not saved.
        items = [i for i in items if i.name.strip()]
        names = [i.name.strip().lower() for i in items]
        if len(names) != len(set(names)):
            raise ValueError("Each name must be different")
        return items


class CallLimits(BaseModel):
    max_duration_seconds: int = Field(default=600, ge=30, le=3600)
    # Agent says goodbye and hangs up after this much silence from the caller.
    silence_timeout_seconds: int = Field(default=20, ge=5, le=120)
    # How long the caller must pause before the agent replies. Short for snappy phone calls;
    # the agent builder's interview waits longer so people can think mid-explanation.
    reply_after_pause_seconds: float = Field(default=0.2, ge=0.2, le=3.0)


class AgentConfig(BaseModel):
    mode: Literal["pipeline", "gemini_live"] = "gemini_live"  # the main mode; pipeline = Standard
    # "everyday_mix": talk like people actually do (Kanglish, Tenglish, Hinglish).
    # "pure": formal, single-language speech.
    speaking_style: Literal["everyday_mix", "pure"] = "everyday_mix"
    system_prompt: str = Field(min_length=1, max_length=20000)
    # First thing the agent says, per language code. Supports {{variables}}.
    greetings: dict[str, str] = Field(default_factory=dict)
    default_language: str = "en"
    languages: list[str] = Field(default_factory=lambda: ["en", "hi", "kn", "te", "ta"])
    # Off: the whole call stays in the opening language (preferred, else state, else default).
    follow_caller_language: bool = True
    # Optional per-agent override of state -> greeting language.
    state_languages: dict[str, str] = Field(default_factory=dict)
    announcement: Announcement = Field(default_factory=Announcement)
    transfer: Transfer = Field(default_factory=Transfer)
    variables: list[Variable] = Field(default_factory=list)
    pipeline: PipelineSettings = Field(default_factory=PipelineSettings)
    gemini_live: GeminiLiveSettings = Field(default_factory=GeminiLiveSettings)
    limits: CallLimits = Field(default_factory=CallLimits)
    atmosphere: Atmosphere = Field(default_factory=Atmosphere)
    analysis: Analysis = Field(default_factory=Analysis)
    # Names of tools (from the Tools page) the agent may use during calls.
    tools: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("languages")
    @classmethod
    def _languages(cls, v: list[str]) -> list[str]:
        unknown = [c for c in v if c not in LANGUAGES]
        if unknown:
            raise ValueError(f"Unsupported languages: {', '.join(unknown)}")
        if not v:
            raise ValueError("Pick at least one language")
        return list(dict.fromkeys(v))

    @model_validator(mode="after")
    def _consistent(self) -> "AgentConfig":
        if self.default_language not in self.languages:
            raise ValueError("The default language must be one of the agent's languages")
        bad = [c for c in self.greetings if c not in self.languages]
        if bad:
            raise ValueError(f"Greetings given for languages the agent doesn't speak: {bad}")
        bad_states = [c for c in self.state_languages.values() if c not in self.languages]
        if bad_states:
            raise ValueError(f"State languages must be agent languages: {bad_states}")
        return self


def render(template: str, variables: dict[str, str], missing: str | None = None) -> str:
    """Fill {{name}} placeholders. Missing values become `missing` if given, otherwise the
    placeholder is left visible so mistakes are noticed."""

    def value(m: re.Match[str]) -> str:
        found = variables.get(m.group(1))
        if found:
            return str(found)
        return missing if missing is not None else m.group(0)

    return VARIABLE_RE.sub(value, template)


def detected_variables(config: "AgentConfig") -> list[str]:
    """Every {{name}} used in the instructions, greetings or announcement, in order of use."""
    texts = [
        config.system_prompt,
        *config.greetings.values(),
        config.announcement.text,
        *config.announcement.translations.values(),
    ]
    seen: dict[str, None] = {}
    for text in texts:
        for match in VARIABLE_RE.finditer(text):
            seen.setdefault(match.group(1), None)
    return list(seen)


# Filled in by YMVA itself (or picked as State/Language), so apps needn't send them.
AUTOMATIC_DETAILS = {"call_date", "call_time", "customer_phone", "preferred_language", "state"}


def call_details(config: "AgentConfig") -> list[Variable]:
    """Per-call details an app starting a call must send: used or declared, minus automatic."""
    described = {v.name: v for v in config.variables}
    if config.transfer.enabled and config.transfer.to == "branch_manager":
        described.setdefault(
            "branch", Variable(name="branch", description="Branch where the customer took the loan")
        )
    return [
        described.get(name, Variable(name=name))
        for name in dict.fromkeys([*detected_variables(config), *described])
        if name not in AUTOMATIC_DETAILS
    ]


def missing_variables(template: str, variables: dict[str, str]) -> set[str]:
    return {m.group(1) for m in VARIABLE_RE.finditer(template) if not variables.get(m.group(1))}
