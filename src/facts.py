import re

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
    "thirty": 30, "forty": 40, "forty-eight": 48, "fifty": 50, "hundred": 100,
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "twice": 2, "double": 2,
}
NUMBER_WORD_PATTERN = re.compile(r"\b(" + "|".join(sorted(NUMBER_WORDS, key=len, reverse=True)) + r")\b")
NUMBER_PATTERN = re.compile(r"\b(\d+(?:\.\d+)?)(?:\s*(lakh|crore))?\b")
SECTION_PATTERN = re.compile(r"\b(?:section|sec\.|s\.)\s*(\d+[A-Z]?)", re.I)
MARKER_PATTERN = re.compile(r"\[\d+(?:\s*[,–-]\s*\d+)*\]|\(\d{1,3}\)|\([ivx]+\)", re.I)


def normalise(text: str) -> str:
    text = text.lower().replace(",", "")
    return NUMBER_WORD_PATTERN.sub(lambda match: str(NUMBER_WORDS[match.group(1)]), text)


def facts(text: str) -> dict:
    sections = {match.group(1).upper() for match in SECTION_PATTERN.finditer(text)}
    numbers, helplines = set(), set()
    for match in NUMBER_PATTERN.finditer(normalise(MARKER_PATTERN.sub(" ", SECTION_PATTERN.sub(" ", text)))):
        value, unit = match.group(1), match.group(2)
        if unit:
            numbers.add(f"{value} {unit}")
        elif len(value) >= 4 and not 1900 <= int(float(value)) <= 2099:
            helplines.add(value)
        elif not (len(value) == 4 and 1900 <= int(value) <= 2099):
            numbers.add(value)
    return {"numbers": numbers, "helplines": helplines, "sections": sections}


def unsupported(answer: str, source: str, cited_sections: set = frozenset()) -> list:
    answer_facts, source_facts = facts(answer), facts(source)
    problems = [f"number '{item}'" for item in sorted(answer_facts["numbers"] - source_facts["numbers"])]
    problems += [f"helpline {item}" for item in sorted(answer_facts["helplines"] - source_facts["helplines"])]
    problems += [f"section {item}" for item in sorted(answer_facts["sections"] - set(cited_sections) - source_facts["sections"])]
    return problems


def section_numbers(citations) -> set:
    return {re.match(r"\d+[A-Z]?", citation.split("s. ")[-1]).group(0) for citation in citations}
