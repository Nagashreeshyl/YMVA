"""Spotting voicemail and network recordings ("the number you have dialled is switched off") at
the start of a phone call, so the agent hangs up and the number is retried instead of the agent
talking to a recording."""

import re

# Phrases only recordings say. A person saying "I'm busy" or "not available now" is a real
# answer, so those are deliberately not here.
# shortcut: fixed phrase list from Indian network messages; add phrases if a recording slips by.
_PHRASES = (
    # English
    "leave a message", "leave your message", "leave the message", "after the tone",
    "after the beep", "voicemail", "voice mail", "mailbox", "number you have dialled",
    "number you have dialed", "number you are calling", "number you are trying",
    "subscriber you", "subscriber is", "currently switched off", "is switched off",
    "not reachable", "out of coverage", "outside the coverage", "does not exist",
    "is not in service", "please try again later", "please try later",
    "unable to take your call", "not answering your call", "call has been forwarded",
    # Hindi
    "स्विच ऑफ", "पहुंच से बाहर", "पहुँच से बाहर", "कवरेज क्षेत्र", "जिस नंबर", "जिस नम्बर",
    "डायल किया गया", "संदेश छोड़", "बीप के बाद", "कृपया थोड़ी देर बाद",
    # Kannada
    "ಸ್ವಿಚ್ ಆಫ್", "ವ್ಯಾಪ್ತಿ ಪ್ರದೇಶದ ಹೊರಗ", "ನೀವು ಕರೆ ಮಾಡಿದ", "ನೀವು ಡಯಲ್ ಮಾಡಿದ",
    # Telugu
    "స్విచ్ ఆఫ్", "పరిధికి వెలుపల", "మీరు డయల్ చేసిన", "మీరు కాల్ చేస్తున్న",
    # Tamil
    "ஸ்விட்ச் ஆஃப்", "தொடர்பு எல்லைக்கு", "நீங்கள் அழைக்கும்", "நீங்கள் டயல் செய்த",
    # Malayalam
    "സ്വിച്ച് ഓഫ്", "പരിധിക്ക് പുറത്ത്", "നിങ്ങൾ വിളിക്കുന്ന", "നിങ്ങൾ ഡയൽ ചെയ്ത",
)  # fmt: skip

VOICEMAIL = "voicemail"  # the call's end_reason when the agent hung up on a recording

# Only the first caller turns of a call are checked; later, these words are a real person.
CHECK_TURNS = 2


def is_voicemail(text: str) -> bool:
    said = re.sub(r"\s+", " ", text.lower()).strip()
    return any(p in said for p in _PHRASES)
