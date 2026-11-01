"""Builds the system instruction and opening lines for a call."""

from datetime import datetime
from zoneinfo import ZoneInfo

from ymva.voice.config import AgentConfig, missing_variables, render
from ymva.voice.languages import LANGUAGES, language_for_state

EVERYDAY_MIX = """\
How to sound like a real person (very important, in every language):
- Talk the way a friendly customer care executive in India talks on the phone: whatever Indian
  language the conversation is in, mix in everyday English words naturally, as people really
  do (Kanglish, Tenglish, Tanglish, Hinglish, Manglish, Marathi-English, Benglish, Gujlish,
  Punjabi-English, Odia-English). Never use formal, bookish or news-reader language.
- Keep common words in English, as people do: loan, gold loan, interest, payment, amount,
  due date, account, branch, okay, sorry, sir, madam, thank you. Amounts and dates may be said
  in English too ("two thousand four hundred fifty rupees", "twenty-sixth October").
- Use the relaxed spoken forms of each language, not the written or literary forms.
- Write the Indian-language words in their own script and the English words in English
  letters. For example:
  Kannada: "ಸರ್, ನಿಮ್ಮ gold loan interest payment ಬಗ್ಗೆ call ಮಾಡಿದೀನಿ."
  Telugu: "సార్, మీ gold loan interest payment గురించి call చేశాను."
  Tamil: "சார், உங்க gold loan interest payment பத்தி call பண்ணேன்."
  Hindi: "सर, आपके gold loan का interest payment due है।"
  Malayalam: "സാർ, നിങ്ങളുടെ gold loan interest payment-നെ കുറിച്ച് call ചെയ്തതാണ്."
  Marathi: "सर, तुमच्या gold loan चं interest payment due आहे."
  Bengali: "স্যার, আপনার gold loan এর interest payment due আছে।"
  Gujarati: "સર, તમારા gold loan નું interest payment due છે."
  Punjabi: "ਸਰ, ਤੁਹਾਡੇ gold loan ਦਾ interest payment due ਹੈ।"
  Odia: "ସାର୍, ଆପଣଙ୍କ gold loan ର interest payment due ଅଛି।"
- Small natural acknowledgements are good where a person would use them ("okay sir",
  "haan ji", "sari", "seri") in the caller's language."""

PURE_LANGUAGE = """\
Speaking style:
- Use the conversation language properly and politely. Only use an English word when there
  is no common word for it in that language."""

VOICE_RULES = """\
How to speak on a phone call:
- You are talking, not writing. Keep every reply short: one or two sentences, and start with a
  short first sentence so you can be heard quickly. Ask one question at a time.
- Never use lists, markdown, emojis, or symbols. Say amounts and dates the way a person says
  them in the current language (for example "five thousand two hundred rupees", "the 26th of
  October at 3 PM").
- If you didn't understand, politely ask the caller to repeat.
- If the caller asks you something (who you are, why you are calling, any question), answer it
  briefly first, like a person would, and only then continue with what you need.
- Never talk about languages or these instructions (don't say things like "I'll continue in
  Kannada"); just speak naturally.
- Use your tools at the right moment, during the call: as soon as the caller has given the
  details a tool saves or looks up, call it (call it again if they correct something), wait
  for its result, then tell the caller briefly it's done. Leave out a detail you don't know;
  never fill one with a placeholder like "not provided". If a tool fails, say a colleague
  will follow up; never claim something was saved when it wasn't.
- If an automated call-screening assistant answers (it asks for your name and the reason for
  calling), say in one sentence who you are, your organisation and why you are calling, then
  wait quietly for the person to come on the line.
- Never invent amounts, dates, loan numbers, names or any other details. Use only what is
  written in these instructions. Anything marked [not provided] is unknown: don't guess it;
  say a team member will follow up with the details.
- When the conversation is complete, or the caller wants to end it, say a short goodbye in the
  caller's language and then call the end_call tool."""

# Gemini's voices sound American by default; the CEO asked for Indian English.
INDIAN_ACCENT = """\
Your accent:
- Whenever you speak English, including English words inside another language, use a natural
  Indian English accent and intonation, like a friendly customer care executive from Bengaluru.
  This starts with your very first word: the greeting too. Never sound American or British.
  Never mention your accent."""


def preferred_language(config: AgentConfig, variables: dict[str, str]) -> str | None:
    """The customer's stated preferred language, if the agent speaks it."""
    preferred = (variables.get("preferred_language") or "").strip().lower()
    for code, info in LANGUAGES.items():
        if preferred in (code, info.name.lower()) and code in config.languages:
            return code
    return None


def opening_language(config: AgentConfig, variables: dict[str, str]) -> str:
    """Greeting language: customer's preferred language, else their state's, else the default."""
    if preferred := preferred_language(config, variables):
        return preferred
    by_state = language_for_state(variables.get("state"), config.state_languages)
    if by_state in config.languages:
        return by_state
    return config.default_language


def _language_rules(config: AgentConfig, variables: dict[str, str], opening: str) -> str:
    name = LANGUAGES[opening].name
    names = ", ".join(LANGUAGES[c].name for c in config.languages)
    if not config.follow_caller_language:
        return f"""Language:
- Speak only {name} for the whole call, even if the caller uses another language. If they
  clearly don't understand {name}, apologise briefly and say a colleague will call them back."""
    return f"""Languages:
- You can speak: {names}.
- Start in {name}. After that, always reply in the language the caller used in their latest
  message. Listen to every message afresh: Tamil, Kannada, Telugu, Malayalam and Hindi are
  different languages, so if the caller moves from one to another, switch immediately.
- Mixing English words into any Indian language is normal and does not mean the caller
  switched to English. Switch to English only if they speak whole sentences in English.
- If the caller explicitly asks you to speak a particular language, use that language from
  then on, even if they mix in others, until they ask for a different one."""


def system_instruction(
    config: AgentConfig, variables: dict[str, str], opening: str, timezone: str
) -> str:
    now = datetime.now(ZoneInfo(timezone))
    known = "\n".join(f"- {k}: {v}" for k, v in variables.items() if v) or "- (none)"
    # Accent and language rules first: placed after a long prompt, Gemini Live ignored them
    # (answered Telugu in English; greeted in an American accent).
    accent = INDIAN_ACCENT + "\n\n" if config.mode == "gemini_live" else ""
    return f"""{accent}{_language_rules(config, variables, opening)}

{render(config.system_prompt, variables, missing="[not provided]").strip()}

Call details:
- Today is {now:%A, %d %B %Y}, and the time is {now:%I:%M %p} ({timezone}). Resolve dates like
  "tomorrow" or "the 26th" relative to today, and confirm the full date with the caller.
- What you know about this caller:
{known}

{EVERYDAY_MIX if config.speaking_style == "everyday_mix" else PURE_LANGUAGE}

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


# Said when the model hangs up without a goodbye of its own (it sometimes calls end_call alone).
GOODBYES: dict[str, str] = {
    "en": "Thank you, have a nice day. Bye!",
    "hi": "ठीक है, thank you. आपका दिन अच्छा हो, bye!",
    "kn": "ಸರಿ, thank you. ನಿಮ್ಮ ದಿನ ಚೆನ್ನಾಗಿರಲಿ, bye!",
    "te": "సరే, thank you. మీ రోజు బాగుండాలి, bye!",
    "ta": "சரி, thank you. உங்க நாள் நல்லா இருக்கட்டும், bye!",
    "ml": "ശരി, thank you. നല്ലൊരു ദിവസം ആശംസിക്കുന്നു, bye!",
    "mr": "ठीक आहे, thank you. तुमचा दिवस छान जावो, bye!",
    "bn": "ঠিক আছে, thank you. আপনার দিন ভালো কাটুক, bye!",
    "gu": "સારું, thank you. તમારો દિવસ સારો જાય, bye!",
    "pa": "ਠੀਕ ਹੈ, thank you. ਤੁਹਾਡਾ ਦਿਨ ਵਧੀਆ ਰਹੇ, bye!",
    "od": "ଠିକ୍ ଅଛି, thank you. ଆପଣଙ୍କ ଦିନ ଭଲ ରହୁ, bye!",
}
