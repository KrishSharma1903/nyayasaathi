import re

OUT_OF_SCOPE = {
    "criminal": r"\b(fir|police|arrest(ed)?|bail|murder|theft|stolen|stole|robbery|jail|"
                r"custody of accused)\b",
    "cyber": r"\b(cyber|scam(med)?|hack(ed|er)?|otp|phishing|upi fraud|bank fraud|online fraud)\b",
    "property": r"\b(land(?!-?\s?line)|plot|ancestral|inheritance|last will|will deed|"
                r"property (papers|dispute)|mutation)\b",
    "family_court": r"\b(divorce|child custody|alimony)\b",
}

IN_SCOPE = re.compile(
    r"\b(landlord|landlady|tenant|tenancy|rent(ed|al)?|deposit|evict(ed|ion)?|"
    r"salary|wages?|employer|boss|overtime|contractor|worker|labour|gig|"
    r"husband|wife|in-?laws?|domestic|dowry|beat(s|ing)?|hit(s|ting)?|"
    r"shop(keeper)?|seller|refund|product|consumer|defective|warranty|mrp|"
    r"legal aid|free lawyer)\b", re.I)


def classify(question: str):
    text = question.lower()
    if IN_SCOPE.search(text):
        return None
    for pattern in OUT_OF_SCOPE.values():
        if re.search(pattern, text):
            return "out_of_scope"
    return None
