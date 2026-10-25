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


class PipelineSettings(BaseModel):
    stt_provider: Literal["sarvam"] = "sarvam"
    llm_provider: Literal["google"] = "google"
    llm_model: str = Field(default="gemini-2.5-flash", max_length=80)
    tts_provider: Literal["sarvam"] = "sarvam"
    voice: str = "priya"
    speaking_pace: float = Field(default=1.0, ge=0.6, le=1.6)
    temperature: float = Field(default=0.4, ge=0.0, le=1.5)

    @field_validator("voice")
    @classmethod
    def _voice(cls, v: str) -> str:
        if v not in SARVAM_VOICES:
            raise ValueError(f"Unknown Sarvam voice '{v}'")
        return v


class GeminiLiveSettings(BaseModel):
    model: str = Field(
        default="models/gemini-2.5-flash-native-audio-preview-12-2025", max_length=120
    )
    voice: str = "Aoede"
    temperature: float = Field(default=0.6, ge=0.0, le=1.5)

    @field_validator("voice")
    @classmethod
    def _voice(cls, v: str) -> str:
        if v not in GEMINI_LIVE_VOICES:
            raise ValueError(f"Unknown Gemini Live voice '{v}'")
        return v


class CallLimits(BaseModel):
    max_duration_seconds: int = Field(default=600, ge=30, le=3600)
    # Agent says goodbye and hangs up after this much silence from the caller.
    silence_timeout_seconds: int = Field(default=20, ge=5, le=120)


class AgentConfig(BaseModel):
    mode: Literal["pipeline", "gemini_live"] = "pipeline"
    system_prompt: str = Field(min_length=1, max_length=20000)
    # First thing the agent says, per language code. Supports {{variables}}.
    greetings: dict[str, str] = Field(default_factory=dict)
    default_language: str = "en"
    languages: list[str] = Field(default_factory=lambda: ["en", "hi", "kn", "te", "ta"])
    # Optional per-agent override of state -> greeting language.
    state_languages: dict[str, str] = Field(default_factory=dict)
    announcement: Announcement = Field(default_factory=Announcement)
    variables: list[Variable] = Field(default_factory=list)
    pipeline: PipelineSettings = Field(default_factory=PipelineSettings)
    gemini_live: GeminiLiveSettings = Field(default_factory=GeminiLiveSettings)
    limits: CallLimits = Field(default_factory=CallLimits)

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


def render(template: str, variables: dict[str, str]) -> str:
    """Fill {{name}} placeholders; unknown names are left visible so mistakes are noticed."""
    return VARIABLE_RE.sub(lambda m: str(variables.get(m.group(1), m.group(0))), template)
