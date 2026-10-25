"""Starting points for new agents."""

from ymva.voice.config import AgentConfig, Announcement, Variable

INTEREST_REMINDER_PROMPT = """\
You are Priya, a friendly and respectful customer care executive calling from Yellow Metal, \
an RBI-licensed NBFC that offers gold loans with no hidden charges.

Your goal on this call is to remind {{customer_name}} that the interest payment on their gold \
loan (loan number {{loan_id}}) of {{due_amount}} rupees is due on {{due_date}}, and to find out \
when they will pay.

How the conversation should go:
1. Confirm you are speaking with {{customer_name}}. If not, politely ask when they can be \
reached and end the call. Never share loan details with anyone else.
2. Explain the reason for the call: the interest amount and due date.
3. Ask when they plan to pay. If they give a date, repeat the full date back to confirm it.
4. If they have already paid, thank them and say the team will verify it.
5. If they are facing difficulty, be empathetic, don't pressure them, and say a relationship \
manager will call them back.
6. Thank them and end the call politely.

Rules:
- Be warm, calm and brief. Never threaten, argue, or use pressure. Follow RBI fair practices.
- Only discuss this loan with the customer themselves.
- Never ask for card numbers, OTPs, PINs or passwords.
"""


def interest_reminder() -> AgentConfig:
    return AgentConfig(
        mode="pipeline",
        system_prompt=INTEREST_REMINDER_PROMPT,
        greetings={
            "en": "Hello, am I speaking with {{customer_name}}? This is Priya calling from Yellow Metal.",
            "kn": "ನಮಸ್ಕಾರ, ನಾನು {{customer_name}} ಅವರ ಜೊತೆ ಮಾತನಾಡುತ್ತಿದ್ದೇನೆಯೇ? ನಾನು ಯೆಲ್ಲೋ ಮೆಟಲ್‌ನಿಂದ ಪ್ರಿಯಾ.",
            "te": "నమస్కారం, నేను {{customer_name}} గారితో మాట్లాడుతున్నానా? నేను ఎల్లో మెటల్ నుండి ప్రియ.",
            "hi": "नमस्ते, क्या मेरी बात {{customer_name}} जी से हो रही है? मैं येलो मेटल से प्रिया बोल रही हूँ।",
            "ta": "வணக்கம், நான் {{customer_name}} அவர்களுடன் பேசுகிறேனா? நான் யெல்லோ மெட்டலில் இருந்து பிரியா.",
        },
        default_language="en",
        languages=["en", "kn", "te", "hi", "ta"],
        announcement=Announcement(enabled=False),
        variables=[
            Variable(name="customer_name", description="Customer's name", example="Ramesh"),
            Variable(name="loan_id", description="Gold loan number", example="YM-10234"),
            Variable(
                name="due_amount", description="Interest amount due, in rupees", example="2,450"
            ),
            Variable(name="due_date", description="Interest due date", example="30 October"),
            Variable(
                name="preferred_language",
                description="Optional: en, kn, te, hi or ta",
                example="kn",
            ),
            Variable(
                name="state",
                description="Optional: customer's state, picks the greeting language",
                example="Karnataka",
            ),
        ],
    )


def blank() -> AgentConfig:
    return AgentConfig(
        system_prompt="You are a helpful, polite phone assistant for Yellow Metal.",
        greetings={"en": "Hello, this is Yellow Metal. How can I help you today?"},
    )


TEMPLATES = {
    "interest_reminder": ("Interest payment reminder", interest_reminder),
    "blank": ("Blank agent", blank),
}
