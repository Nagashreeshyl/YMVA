"""AI agent builder: writes a new agent from a plain-English brief, and edits an existing agent
through a chat with the admin.

Gemini works on a simplified, flat view of the agent (AgentSpec) that maps one-to-one onto the
editor's fields. Settings the spec doesn't cover (models, pace, volume...) are kept as they are.
Every result is validated as a real AgentConfig before it's used; if validation fails, Gemini is
shown the error and asked once more.
"""

import asyncio
import json
from typing import Literal

from google import genai
from google.genai import types
from pydantic import BaseModel, Field, ValidationError

from ymva.logging import get_logger
from ymva.voice.config import (
    GEMINI_LIVE_VOICES,
    SARVAM_VOICES,
    AgentConfig,
    Announcement,
    CallLimits,
    TrackedItem,
    Variable,
)
from ymva.voice.languages import LANGUAGES

log = get_logger(__name__)

# Writing an agent is not a live call, but it should still feel quick: no thinking, and the next
# model if one is busy or slow. Measured 25 Oct 2026: 3.5-flash-lite edits in 4-8 s with good
# results; 3.6-flash took 17 s and was often overloaded.
BUILDER_MODELS = ("gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-3.1-flash-lite")
BUILDER_TIMEOUT_SECS = 25.0

FEMALE_SARVAM = ("priya", "ritu", "neha", "pooja", "simran", "kavya", "ishita", "shreya", "roopa",
                 "amelia", "sophia")  # fmt: skip
FEMALE_GEMINI = ("Aoede", "Kore", "Leda", "Zephyr")


class BuilderError(Exception):
    """Gemini couldn't produce a usable agent; the message is safe to show the admin."""


class GreetingSpec(BaseModel):
    language: str = Field(description="Language code, one of the agent's languages")
    text: str = Field(description="The agent's first words in that language")


class VariableSpec(BaseModel):
    name: str = Field(description="snake_case name used as {{name}}")
    description: str = ""
    example: str = ""


class TrackedSpec(BaseModel):
    name: str = Field(description="Short label, e.g. 'Appointment booked'")
    description: str = Field(description="When it applies / what to write down, one line")


class AgentSpec(BaseModel):
    """The parts of an agent the builder writes."""

    name: str = Field(description="Short agent name, e.g. 'Interest reminder - Karnataka'")
    description: str = Field(description="One sentence on what the agent does")
    mode: Literal["pipeline", "gemini_live"]
    speaking_style: Literal["everyday_mix", "pure"]
    system_prompt: str
    languages: list[str]
    default_language: str
    follow_caller_language: bool = Field(
        description="True: switch with the caller's language. False: keep the opening language"
    )
    greetings: list[GreetingSpec]
    variables: list[VariableSpec]
    announcement_enabled: bool
    announcement_text: str
    pipeline_voice: str
    gemini_live_voice: str
    max_duration_seconds: int
    silence_timeout_seconds: int
    office_background: bool
    typing_during_tools: bool
    outcomes: list[TrackedSpec] = Field(
        description="After the call: the possible results; each call gets exactly one"
    )
    details: list[TrackedSpec] = Field(
        description="After the call: facts to write down when the customer says them"
    )


class ToolRequest(BaseModel):
    name: str = Field(description="An existing tool's name to change it, or a new snake_case name")
    when: Literal["during_call", "after_call"] = Field(
        default="during_call",
        description="after_call for sending or saving the call's results (summary, outcome, "
        "details the customer gave) once it ends; during_call when the agent needs it mid-call",
    )
    request: str = Field(
        description="What the tool must do, or what to change, in plain words: when the agent "
        "uses it, what it saves or looks up, and any sheet link, columns or API address the "
        "admin gave. Never a key, token or password."
    )


class TestValue(BaseModel):
    name: str
    value: str


class ToolTest(BaseModel):
    name: str = Field(description="The tool to run now, as a test")
    values: list[TestValue] = Field(
        default_factory=list,
        description="Sample values for its {{placeholders}}, e.g. customer_name: Test Customer",
    )


class AssistantResult(BaseModel):
    reply: str = Field(description="Short, plain-language answer to the admin")
    changes: list[str] = Field(description="What changed, one short line each; empty if none")
    agent: AgentSpec | None = Field(description="The full updated agent, or null if unchanged")
    tools: list[ToolRequest] = Field(
        default_factory=list, description="Tools to create or change; empty if none"
    )
    remove_tools: list[str] = Field(
        default_factory=list, description="Names of tools this agent should stop using"
    )
    test_tool: ToolTest | None = Field(
        default=None, description="A tool to run right now because the admin asked to test it"
    )


def _voices() -> str:
    def label(voices: tuple[str, ...], female: tuple[str, ...]) -> str:
        return ", ".join(f"{v} ({'female' if v in female else 'male'})" for v in voices)

    return (
        f"Standard-mode voices (Sarvam): {label(SARVAM_VOICES, FEMALE_SARVAM)}.\n"
        f"Gemini Live voices: {label(GEMINI_LIVE_VOICES, FEMALE_GEMINI)}."
    )


GUIDE = f"""\
You design phone voice agents for Yellow Metal, an RBI-licensed NBFC in India that offers gold
loans. The agents make and take real phone calls with customers.

Platform facts:
- Languages (code: name): {", ".join(f"{c}: {i.name}" for c, i in LANGUAGES.items())}.
  The agent replies in whatever language the caller speaks, from its list of languages, and
  switches mid-call. The greeting language comes from the customer's preferred language, else
  their state, else the default language.
- Modes: "gemini_live" (the default: Sarvam hears the caller, Gemini Live thinks and speaks;
  fastest replies and the most natural voice) and "pipeline" (Standard: Sarvam hears and
  speaks, Gemini thinks; Sarvam's Indian voices, slower replies).
- Speaking style: "everyday_mix" (natural code-mixed speech such as Kanglish, Tenglish,
  Hinglish; the default, sounds human) or "pure" (formal single language).
- {_voices()}
  Match the voice to the persona's name and gender.
- Per-call details are written as {{{{name}}}} (snake_case) in the instructions, greetings and
  announcement, and every one must be listed in variables with a description and an example.
  Two optional variables are understood by the platform: preferred_language (a language code)
  and state (the customer's state, which picks the greeting language).
- The platform already adds rules for phone speech (short replies, one question at a time, no
  lists or symbols, never invent details, say goodbye and hang up). Do not repeat them.

How to write system_prompt (plain text, second person, like the example below):
1. Who the agent is: name, role, company, personality.
2. The goal of the call.
3. "How the conversation should go:" as numbered steps, including what to do in each
   condition the admin described (already paid, wrong person, can't pay, asks for a callback,
   angry, asks something the agent can't answer...).
4. "Rules:" covering compliance: polite, never threatening (RBI fair practices), only discuss
   the loan with the customer themselves, never ask for OTPs, PINs, card numbers or passwords.
Keep it focused: under about 400 words.

After the call, the transcript is read and saved with one outcome and some details. Write both
for this agent's own purpose, never copied from another kind of call (a hospital appointment
agent gets outcomes like "Appointment booked", "Rescheduled", "Cancelled", "Wrong person",
"Call back later" and details like "Appointment date", "Doctor"; a payment reminder gets
"Promised to pay", "Already paid"...). 3 to 8 outcomes, each a short label with a one-line
"when it applies"; "Other" is added automatically, so don't list it. 0 to 8 details, each a
short label with what to write; ask for dates as YYYY-MM-DD and times as HH:MM.

Greetings: one per agent language, natural and short (introduce the agent and confirm who
they're speaking to). For "everyday_mix", write them the way people actually talk: the
Indian-language words in their own script, everyday English words (loan, interest, payment,
okay, sir) in English letters.

Defaults unless the admin says otherwise: mode gemini_live, everyday_mix, languages en, kn, te, hi,
ta with default en, a female persona named Priya (voice priya / Aoede), announcement off,
max_duration_seconds 600 (30-3600), silence_timeout_seconds 20 (5-120), office_background
false, typing_during_tools true, follow_caller_language true.

Example of a good system_prompt:
---
You are Priya, a friendly and respectful customer care executive calling from Yellow Metal,
an RBI-licensed NBFC that offers gold loans with no hidden charges.

Your goal on this call is to remind {{{{customer_name}}}} that the interest payment on their gold
loan (loan number {{{{loan_id}}}}) of {{{{due_amount}}}} rupees is due on {{{{due_date}}}}, and to
find out when they will pay.

How the conversation should go:
1. Confirm you are speaking with {{{{customer_name}}}}. If not, politely ask when they can be
reached and end the call. Never share loan details with anyone else.
2. Explain the reason for the call: the interest amount and due date.
3. Ask when they plan to pay. If they give a date, repeat the full date back to confirm it.
4. If they have already paid, thank them and say the team will verify it.
5. If they are facing difficulty, be empathetic, don't pressure them, and say a relationship
manager will call them back.
6. Thank them and end the call politely.

Rules:
- Be warm, calm and brief. Never threaten, argue, or use pressure. Follow RBI fair practices.
- Only discuss this loan with the customer themselves.
- Never ask for card numbers, OTPs, PINs or passwords.
---"""

ASSISTANT_RULES = """\
You are now YMA, the Yellow Metal Assistant: the admin's assistant inside the agent editor.
The admin asks you to change the agent or asks questions about it.
- When they ask for a change, return the full updated agent in "agent" (every field, with only
  the requested changes made, everything else exactly as it was) and list each change in
  "changes" in plain words, the way the editor names things ("Greeting language: Telugu",
  "Added Tamil", "Voice: Rahul", "New step for customers who already paid"). Never use field
  names, language codes or JSON.
- When they only ask a question, or the request is unclear, answer in "reply", set "agent" to
  null and "changes" to [] (ask one short clarifying question if needed).
- Keep "reply" to one to three short sentences, friendly and plain. No markdown, field
  names, language codes or {{placeholders}} (say "the customer", "the due date").
- Outcomes and details are the editor's "After the call" section ("Outcomes", "Details to
  write down"). When the admin asks about after-call results, outcomes, details, or what gets
  saved or written down, change those lists, not the instructions. Name such changes like
  "Outcome added: Appointment booked", "Detail removed: Paid on".
- If you add a {{variable}}, add it to variables too; if you stop using one, remove it.
- Tools let the agent act during or after a call: save to a Google Sheet, look something up,
  call the company's API or database. When the admin wants the agent to do such a thing, put
  it in "tools" (a new tool, or an existing tool's name to change it), and say in the
  instructions when the agent uses it. The tool is created and switched on for this agent
  for you; don't list it in "changes" yourself. A sheet link, column names or an API address
  the admin types go into the tool's request.
- Sending or saving a call's results (a summary, the outcome, what the customer said) to a
  sheet, Telegram, email or CRM is an after_call tool: it runs once the call ends with the
  full summary and details, so don't add a step for it in the instructions.
- When the admin no longer wants a tool, put its name in "remove_tools" and take it out of
  the instructions.
- When the admin asks to test or try a tool ("send me a test message"), put it in
  "test_tool" with realistic sample values, set "agent" to null and "changes" to []. The
  result is added to your reply for you; don't claim it worked.
- Never ask the admin to type a key, token or password in the chat. When a tool needs one,
  say a secure key field appears below the chat for it.
- The admin's edits are saved as a draft; they still test and publish themselves."""


def to_spec(config: AgentConfig, name: str, description: str) -> AgentSpec:
    return AgentSpec(
        name=name,
        description=description,
        mode=config.mode,
        speaking_style=config.speaking_style,
        system_prompt=config.system_prompt,
        languages=config.languages,
        default_language=config.default_language,
        follow_caller_language=config.follow_caller_language,
        greetings=[GreetingSpec(language=k, text=v) for k, v in config.greetings.items()],
        variables=[VariableSpec(**v.model_dump()) for v in config.variables],
        announcement_enabled=config.announcement.enabled,
        announcement_text=config.announcement.text,
        pipeline_voice=config.pipeline.voice,
        gemini_live_voice=config.gemini_live.voice,
        max_duration_seconds=config.limits.max_duration_seconds,
        silence_timeout_seconds=config.limits.silence_timeout_seconds,
        office_background=config.atmosphere.room_tone,
        typing_during_tools=config.atmosphere.typing_while_thinking,
        outcomes=[TrackedSpec(**o.model_dump()) for o in config.analysis.outcomes],
        details=[TrackedSpec(**d.model_dump()) for d in config.analysis.details],
    )


def _tracked(item: TrackedSpec) -> TrackedItem:
    return TrackedItem(name=item.name.strip()[:60], description=item.description.strip()[:300])


def from_spec(spec: AgentSpec, base: AgentConfig) -> AgentConfig:
    """Apply the spec on top of `base`. Raises ValidationError if the result can't run."""
    data = base.model_dump(mode="json")
    languages = [c.strip().lower() for c in spec.languages]
    announcement = Announcement(
        enabled=spec.announcement_enabled,
        text=spec.announcement_text or base.announcement.text,
        translations={k: v for k, v in base.announcement.translations.items() if k in languages},
    )
    data.update(
        mode=spec.mode,
        speaking_style=spec.speaking_style,
        system_prompt=spec.system_prompt,
        languages=languages,
        default_language=spec.default_language.strip().lower(),
        follow_caller_language=spec.follow_caller_language,
        greetings={g.language.strip().lower(): g.text.strip() for g in spec.greetings if g.text},
        variables=[
            Variable(
                name=v.name, description=v.description[:200], example=v.example[:120]
            ).model_dump()
            for v in spec.variables
        ],
        announcement=announcement.model_dump(),
        state_languages={k: v for k, v in base.state_languages.items() if v in languages},
    )
    data["pipeline"]["voice"] = spec.pipeline_voice.strip().lower()
    data["gemini_live"]["voice"] = spec.gemini_live_voice.strip().capitalize()
    data["limits"] = {
        "max_duration_seconds": min(3600, max(30, spec.max_duration_seconds)),
        "silence_timeout_seconds": min(120, max(5, spec.silence_timeout_seconds)),
    }
    data["atmosphere"]["room_tone"] = spec.office_background
    data["atmosphere"]["typing_while_thinking"] = spec.typing_during_tools
    data["analysis"]["outcomes"] = [_tracked(o).model_dump() for o in spec.outcomes]
    data["analysis"]["details"] = [_tracked(d).model_dump() for d in spec.details]
    return AgentConfig.model_validate(data)


def _problems(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or 'agent'}: {e['msg']}" for e in error.errors()
    )


async def ask_gemini[T: BaseModel](
    api_key: str, system: str, contents: list[types.Content], schema: type[T]
) -> T:
    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json",
        response_schema=schema,
        temperature=0.4,
        thinking_config=types.ThinkingConfig(thinking_level="minimal"),
    )
    last: Exception | None = None
    for model in BUILDER_MODELS:
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(model=model, contents=contents, config=config),
                BUILDER_TIMEOUT_SECS,
            )
            return schema.model_validate_json(response.text or "")
        except Exception as exc:  # busy model, bad JSON: try the next one
            log.warning("builder model failed", model=model, error=str(exc)[:300])
            last = exc
    raise BuilderError(
        "The AI builder isn't responding right now. Try again in a moment."
    ) from last


def _text(role: str, text: str) -> types.Content:
    return types.Content(role=role, parts=[types.Part(text=text)])


async def generate_agent(
    brief: str, api_key: str, base: AgentConfig
) -> tuple[AgentSpec, AgentConfig]:
    """Write a whole agent from the admin's description."""
    contents = [_text("user", f"Design a voice agent from this description:\n\n{brief}")]
    for _ in range(2):
        spec = await ask_gemini(api_key, GUIDE, contents, AgentSpec)
        try:
            return spec, from_spec(spec, base)
        except ValidationError as error:
            contents += [
                _text("model", spec.model_dump_json()),
                _text("user", f"That agent isn't valid: {_problems(error)}. Fix it."),
            ]
    raise BuilderError("The AI wrote an agent that doesn't fit the platform. Try rewording it.")


async def edit_agent(
    *,
    config: AgentConfig,
    name: str,
    description: str,
    history: list[tuple[str, str]],
    message: str,
    api_key: str,
    tools: str = "",
) -> tuple[AssistantResult, AgentConfig | None]:
    """One assistant turn. Returns the reply and the updated config (None if unchanged).
    `tools` describes the tools that exist, so the assistant can reuse or change them."""
    current = to_spec(config, name, description)
    contents = [_text("user" if role == "user" else "model", text) for role, text in history]
    contents.append(
        _text(
            "user",
            f"Current agent:\n{json.dumps(current.model_dump(), ensure_ascii=False)}\n\n"
            f"Tools that exist:\n{tools or '(none)'}\n\n"
            f"Admin: {message}",
        )
    )
    system = f"{GUIDE}\n\n{ASSISTANT_RULES}"
    for _ in range(2):
        result = await ask_gemini(api_key, system, contents, AssistantResult)
        if result.agent is None or result.agent == current:
            return result.model_copy(update={"agent": None}), None
        try:
            return result, from_spec(result.agent, config)
        except ValidationError as error:
            contents += [
                _text("model", result.model_dump_json()),
                _text("user", f"That agent isn't valid: {_problems(error)}. Fix it."),
            ]
    raise BuilderError("I couldn't make that change in a way that works. Try rewording it.")


# ---- Talk it through: build an agent from a spoken conversation -----------------------

INTERVIEWER_NAME = "YMA agent builder"

INTERVIEWER_PROMPT = """\
You are YMA, the Yellow Metal Assistant: YMVA's agent designer. The person on the line is an
admin at Yellow Metal who wants
to create a new AI phone agent. Your job is to understand exactly how they want that agent
to behave, by talking it through with them, so it can be built afterwards from this
conversation. You don't build anything yourself and you are not the new agent.

Find out, one short question at a time, in a natural order:
- What the agent is for: who it calls (or who calls it), why, and what a successful call
  achieves.
- Who the agent is: its name, female or male voice, and how it should speak (tone, how
  formal, which languages, everyday mixed language or pure).
- How the call should go, step by step: how it opens, what it says or asks next, and how it
  ends.
- What it already knows about each customer before the call (for example their name, loan
  number, amount due, due date) and what it must find out from them.
- The dos and don'ts, and what to do when things don't go to plan: wrong person, customer
  busy, refuses, angry, asks something it can't answer, wants a callback or a human.
- What results to note after each call (for example "promised to pay" with the date).

How to talk with them:
- Let them explain in their own words; don't read out this list. People pause while they
  think: if what they said sounds unfinished, just say "mm-hm" or "go on" and let them
  continue. After each answer, confirm
  in one sentence what you understood, then ask about the next thing.
- Notice what they haven't covered and ask about it. When they aren't sure, suggest a
  sensible choice and ask if that's fine.
- Never ask about technical settings (models, API keys, numbers).
- When you have the whole picture, summarise the agent in a few sentences and ask if
  anything should change. When they're happy, tell them to end the call: the agent will be
  built from this conversation, and they can still edit everything afterwards."""


def interviewer() -> AgentConfig:
    """The built-in agent that interviews an admin about the agent they want."""
    return AgentConfig(
        system_prompt=INTERVIEWER_PROMPT,
        greetings={
            "en": "Hi! I'm YMA, the Yellow Metal Assistant. Tell me about the agent you want "
            "to create: what should it do, and who will it talk to?"
        },
        limits=CallLimits(
            max_duration_seconds=1800, silence_timeout_seconds=90, reply_after_pause_seconds=1.5
        ),
    )


def brief_from_interview(lines: list[tuple[str, str]]) -> str:
    """The interview as a brief for generate_agent. `lines` are (speaker, text)."""
    transcript = "\n".join(f"{who}: {text}" for who, text in lines)
    return (
        "The admin described the agent they want by talking it through with an interviewer. "
        "Build the agent from everything the admin said: their words decide, the "
        "interviewer's lines are only context (use a suggestion the admin agreed to). Keep "
        "every step, rule and detail they mentioned, and where they left something out, "
        "choose sensible defaults.\n\n" + transcript
    )
