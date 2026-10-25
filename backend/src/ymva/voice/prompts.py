"""Builds the system instruction and opening lines for a call."""

from datetime import datetime
from zoneinfo import ZoneInfo

from ymva.voice.config import AgentConfig, missing_variables, render
from ymva.voice.languages import LANGUAGES, language_for_state

VOICE_RULES = """\
How to speak on a phone call:
- You are talking, not writing. Keep every reply short: one or two sentences, and start with a
  short first sentence so you can be heard quickly. Ask one question at a time.
- Never use lists, markdown, emojis, or symbols. Say amounts and dates the way a person says
  them in the current language (for example "five thousand two hundred rupees", "the 26th of
  October at 3 PM").
- If you didn't understand, politely ask the caller to repeat.
- Never invent amounts, dates, loan numbers, names or any other details. Use only what is
  written in these instructions. Anything marked [not provided] is unknown: don't guess it;
  say a team member will follow up with the details.
- When the conversation is complete, or the caller wants to end it, say a short goodbye in the
  caller's language and then call the end_call tool."""


def opening_language(config: AgentConfig, variables: dict[str, str]) -> str:
    """Greeting language: customer's preferred language, else their state's, else the default."""
    preferred = (variables.get("preferred_language") or "").strip().lower()
    for code, info in LANGUAGES.items():
        if preferred in (code, info.name.lower()) and code in config.languages:
            return code
    by_state = language_for_state(variables.get("state"), config.state_languages)
    if by_state in config.languages:
        return by_state
    return config.default_language


def system_instruction(
    config: AgentConfig, variables: dict[str, str], opening: str, timezone: str
) -> str:
    now = datetime.now(ZoneInfo(timezone))
    names = ", ".join(LANGUAGES[c].name for c in config.languages)
    known = "\n".join(f"- {k}: {v}" for k, v in variables.items() if v) or "- (none)"
    return f"""{render(config.system_prompt, variables, missing="[not provided]").strip()}

Call details:
- Today is {now:%A, %d %B %Y}, and the time is {now:%I:%M %p} ({timezone}). Resolve dates like
  "tomorrow" or "the 26th" relative to today, and confirm the full date with the caller.
- What you know about this caller:
{known}

Languages:
- You can speak: {names}.
- Start in {LANGUAGES[opening].name}. After that, always reply in the language the caller used in
  their latest message. If they switch language, switch immediately with them.
- If the caller mixes English words into another language, reply in that other language.

{VOICE_RULES}"""


def opening_lines(
    config: AgentConfig, variables: dict[str, str], opening: str
) -> tuple[str | None, str | None]:
    """(announcement, greeting) to speak first. A missing greeting means the model greets."""
    announcement = None
    if config.announcement.enabled:
        text = config.announcement.translations.get(opening) or config.announcement.text
        announcement = render(text, variables).strip() or None
    greeting = config.greetings.get(opening)
    # A scripted greeting that needs a detail we don't have is left to the model instead of
    # being read out with a gap.
    if not greeting or missing_variables(greeting, variables):
        return announcement, None
    return announcement, render(greeting, variables).strip() or None
