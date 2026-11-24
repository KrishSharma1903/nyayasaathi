HELP = ("Free help: Tele-Law (14454) or your District Legal Services Authority (15100). "
        "If you are in danger, call 112.")

MESSAGES = {
    "empty": "Please type your question in a few words.",
    "out_of_scope": ("This question is outside what I can help with (I cover labour, "
                     "tenancy, consumer and domestic-violence rights). " + HELP),
    "low_confidence": "I am not sure about this one, so I won't guess. " + HELP,
    "no_support": ("I could not find a clear answer for this in my sources, so I won't "
                   "guess. " + HELP),
}


def reply(reason: str) -> str:
    return MESSAGES.get(reason, MESSAGES["low_confidence"])
