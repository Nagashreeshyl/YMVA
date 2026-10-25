"""Starting points for new agents."""

from ymva.voice.config import AgentConfig, Analysis, Announcement, TrackedItem, Variable

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
            "en": "Hello! This is Priya from Yellow Metal. Am I speaking with {{customer_name}}?",
            "kn": "ನಮಸ್ಕಾರ! ನಾನು Yellow Metal ಇಂದ Priya ಮಾತಾಡ್ತಿದೀನಿ. {{customer_name}} ಅವರ ಜೊತೆ ಮಾತಾಡ್ತಿದೀನಾ?",
            "te": "నమస్కారం! నేను Yellow Metal నుంచి Priya మాట్లాడుతున్నాను. {{customer_name}} గారేనా మాట్లాడేది?",
            "hi": "नमस्ते! मैं Yellow Metal से Priya बोल रही हूँ। क्या मेरी बात {{customer_name}} जी से हो रही है?",
            "ta": "வணக்கம்! நான் Yellow Metal-ல இருந்து Priya பேசறேன். {{customer_name}} அவங்க கிட்ட பேசறேனா?",
        },
        default_language="en",
        languages=["en", "kn", "te", "hi", "ta"],
        announcement=Announcement(enabled=False),
        analysis=Analysis(
            outcomes=[
                TrackedItem(name="Promised to pay", description="Gave a date to pay the interest"),
                TrackedItem(name="Already paid", description="Says the interest is already paid"),
                TrackedItem(
                    name="Needs help", description="Facing difficulty or wants a manager to call"
                ),
                TrackedItem(name="Call back later", description="Asked to talk at another time"),
                TrackedItem(name="Wrong person", description="Not the customer"),
                TrackedItem(name="Refused", description="Won't pay or ended the call"),
            ],
            details=[
                TrackedItem(
                    name="Promised payment date",
                    description="The date they will pay, as YYYY-MM-DD",
                ),
                TrackedItem(name="Paid on", description="If already paid: when, as YYYY-MM-DD"),
                TrackedItem(name="Call back at", description="When to call back, if they asked"),
                TrackedItem(
                    name="Reason for difficulty", description="Why they can't pay, in brief"
                ),
            ],
        ),
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


# One short, natural line per language in the everyday mixed style the agents use.
VOICE_PREVIEW_LINES = {
    "en": "Hello! This is {name} from Yellow Metal. Your gold loan interest is due on the thirtieth. Is that okay?",
    "kn": "ನಮಸ್ಕಾರ! ನಾನು Yellow Metal ಇಂದ {name} ಮಾತಾಡ್ತಿದೀನಿ. ನಿಮ್ಮ gold loan interest thirtieth ಗೆ due ಇದೆ, okay ನಾ?",
    "te": "నమస్కారం! నేను Yellow Metal నుంచి {name} మాట్లాడుతున్నాను. మీ gold loan interest thirtieth కి due ఉంది, okay నా?",
    "ta": "வணக்கம்! நான் Yellow Metal-ல இருந்து {name} பேசறேன். உங்க gold loan interest thirtieth-க்கு due இருக்கு, okay வா?",
    "hi": "नमस्ते! मैं Yellow Metal से {name} बोल रही हूँ। आपका gold loan interest thirtieth को due है, okay?",
    "ml": "നമസ്കാരം! ഞാൻ Yellow Metal-ൽ നിന്ന് {name} ആണ്. നിങ്ങളുടെ gold loan interest thirtieth-ന് due ആണ്, okay അല്ലേ?",
    "mr": "नमस्कार! मी Yellow Metal मधून {name} बोलतेय. तुमचं gold loan interest thirtieth ला due आहे, okay ना?",
    "bn": "নমস্কার! আমি Yellow Metal থেকে {name} বলছি। আপনার gold loan interest thirtieth এ due আছে, okay তো?",
    "gu": "નમસ્તે! હું Yellow Metal માંથી {name} બોલું છું. તમારું gold loan interest thirtieth એ due છે, okay ને?",
    "pa": "ਸਤ ਸ੍ਰੀ ਅਕਾਲ! ਮੈਂ Yellow Metal ਤੋਂ {name} ਬੋਲ ਰਹੀ ਹਾਂ। ਤੁਹਾਡਾ gold loan interest thirtieth ਨੂੰ due ਹੈ, okay?",
    "od": "ନମସ୍କାର! ମୁଁ Yellow Metal ରୁ {name} କହୁଛି। ଆପଣଙ୍କ gold loan interest thirtieth ରେ due ଅଛି, okay ତ?",
}
