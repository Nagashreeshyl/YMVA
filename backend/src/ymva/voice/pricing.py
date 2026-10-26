"""Estimated AI cost of a call, from the usage Pipecat reports.

Prices are list prices (pay-as-you-go), checked 26 Oct 2026 against Google's and Sarvam's
published rates and third-party price trackers; update them here when the providers change
them. Telephony charges are billed by the phone provider and not included.
"""

from typing import Any

USD_PER_INR = 1 / 88.0

# USD per 1M tokens: (text input, text output, audio input, audio output)
GEMINI_LIVE = (0.75, 4.50, 3.00, 12.00)
# USD per 1M tokens: (input, output). Unknown models are priced like the dearest listed one.
GEMINI_TEXT = {
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3.6-flash": (0.50, 3.00),
}
SARVAM_STT_INR_PER_MIN = 0.49
SARVAM_TTS_INR_PER_1K_CHARS = 3.00


def estimate(mode: str, llm_model: str, usage: dict[str, float]) -> dict[str, float]:
    """Cost per part, in USD, plus "total"."""
    prompt, completion = usage.get("llm_prompt_tokens", 0), usage.get("llm_completion_tokens", 0)
    if mode == "gemini_live":
        text_in, text_out, audio_in, audio_out = GEMINI_LIVE
        # Calls recorded before audio tokens were counted: price all input as audio
        # (an upper bound), and all output as audio (it is: the agent speaks).
        a_in = usage.get("llm_input_audio_tokens", prompt)
        a_out = usage.get("llm_output_audio_tokens", completion)
        parts = {
            "gemini_live": (
                a_in * audio_in
                + max(0.0, prompt - a_in) * text_in
                + a_out * audio_out
                + max(0.0, completion - a_out) * text_out
            )
            / 1e6
        }
    else:
        rate_in, rate_out = GEMINI_TEXT.get(llm_model, max(GEMINI_TEXT.values()))
        parts = {
            "gemini": (prompt * rate_in + completion * rate_out) / 1e6,
            "sarvam_stt": usage.get("stt_seconds", 0) / 60 * SARVAM_STT_INR_PER_MIN * USD_PER_INR,
            "sarvam_tts": usage.get("tts_characters", 0)
            / 1000
            * SARVAM_TTS_INR_PER_1K_CHARS
            * USD_PER_INR,
        }
    parts = {k: round(v, 6) for k, v in parts.items()}
    parts["total"] = round(sum(parts.values()), 6)
    return parts


def call_cost(config: dict[str, Any] | None, usage: dict[str, float]) -> dict[str, float]:
    config = config or {}
    return estimate(
        config.get("mode", "pipeline"),
        config.get("pipeline", {}).get("llm_model", "gemini-3.5-flash-lite"),
        usage,
    )
