import re

EMERGENCY = re.compile(
    r"\b(beat(s|ing)? me|hit(s|ting)? me|kill(ing)? me|threaten(s|ed)? to kill|"
    r"in danger|(feel|feeling|am|i'm|not) (unsafe|safe at home)|"
    r"abus(e|ed|es|ing)|violen(ce|t)|molest|rape|acid)\b", re.I)
SELF_HARM = re.compile(
    r"\b(suicide|kill myself|end my life|want to die)\b", re.I)

EMERGENCY_NOTE = ("If you are in danger right now, call 112 (emergency). "
                  "Women's helpline: 181.")
SELF_HARM_NOTE = ("You are not alone. Please call 112, or the Tele-MANAS mental health "
                  "helpline 14416, or talk to someone you trust right now.")


def check(question: str) -> dict:
    text = (question or "").strip()
    return {
        "empty": len(text) < 3,
        "self_harm": bool(SELF_HARM.search(text)),
        "emergency": bool(EMERGENCY.search(text)),
    }
