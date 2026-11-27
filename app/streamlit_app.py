import csv
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

try:
    for name, value in st.secrets.items():
        if isinstance(value, str):
            os.environ.setdefault(name, value)
except Exception:
    pass

APP_FOLDER = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_FOLDER.parent / "src"))
import config
from pipeline import Pipeline

ASSETS = APP_FOLDER / "assets"

st.set_page_config(
    page_title="NyayaSaathi · Know your rights",
    page_icon=str(ASSETS / "icon.svg"),
    layout="centered",
)
st.logo(str(ASSETS / "logo.svg"), icon_image=str(ASSETS / "icon.svg"), size="large")

TOPICS = [
    (":material/work:", "Work and wages", "Unpaid salary, minimum wage, overtime, equal pay"),
    (":material/home:", "Home and rent", "Deposits, eviction, rent hikes, water and power cuts"),
    (":material/shopping_bag:", "Shopping and services", "Faulty goods, bad service, overcharging"),
    (":material/shield_person:", "Safety at home", "Domestic violence, right to stay, money support"),
]

SUGGESTIONS = {
    ":material/payments: Salary not paid": "My employer has not paid my salary for two months. What can I do?",
    ":material/home: Deposit not returned": "My landlord is not returning my security deposit.",
    ":material/shopping_bag: Broken online order": "My online order arrived broken and the seller won't refund me.",
    ":material/shield_person: Husband hits me": "My husband hits me. What can I do?",
    ":material/gavel: Free lawyer": "I can't afford a lawyer. Where can I get free help?",
}

HELPLINES = [
    (":material/emergency:", "112", "Emergency, police, ambulance"),
    (":material/woman:", "181", "Women helpline"),
    (":material/support_agent:", "14454", "Tele-Law, free legal advice"),
    (":material/gavel:", "15100", "Legal Services Authority (NALSA)"),
    (":material/shopping_bag:", "1915", "National Consumer Helpline"),
]

DOMAIN = {
    "labour": ("Work and wages", ":material/work:"),
    "tenancy": ("Home and rent", ":material/home:"),
    "consumer": ("Shopping and services", ":material/shopping_bag:"),
    "domestic": ("Safety at home", ":material/shield_person:"),
    "legal_aid": ("Free legal help", ":material/gavel:"),
}

MODE = {
    "llm": ("AI answer, fact-checked", ":material/auto_awesome:", "violet",
            "Written by Gemini from the sources below. Every number, helpline and section "
            "in it was checked against them."),
    "curated": ("Expert summary", ":material/article:", "gray",
                "A hand-written summary from our knowledge base."),
    "guarded": ("Expert summary", ":material/shield:", "orange",
                "The AI answer mentioned a fact that is not in our sources, so the "
                "hand-written summary is shown instead."),
}

VERIFIED = {
    "act_text": ("Checked against the Act", ":material/verified:", "green",
                 "This summary was checked against the official text of the law."),
    "partial": ("Partly checked", ":material/rule:", "orange",
                "Some facts come from official rules or websites, not the Act itself."),
    "secondary": ("Not yet checked against the Act", ":material/info:", "gray",
                  "Based on a reliable summary; the official law text is not in our "
                  "library yet."),
}


@st.cache_resource(show_spinner="Loading the law library...")
def load_pipeline():
    return Pipeline()


def new_chat():
    st.session_state.messages = []


def save_feedback(message_id: str):
    message = next(saved for saved in st.session_state.messages if saved["id"] == message_id)
    rating = {0: "down", 1: "up"}.get(st.session_state.get(f"feedback_{message_id}"))
    if rating is None:
        return
    result = message["result"]
    config.FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    is_new_file = not config.FEEDBACK_PATH.exists()
    with config.FEEDBACK_PATH.open("a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        if is_new_file:
            writer.writerow(["time_utc", "question", "answer", "fallback", "reason", "mode",
                        "score", "rating"])
        writer.writerow([datetime.now(timezone.utc).isoformat(timespec="seconds"), message["question"],
                    result["answer"], result["fallback"], result.get("reason"), result.get("mode"),
                    f"{result['score']:.3f}", rating])
    st.toast("Thanks for your feedback!", icon=":material/favorite:")


def search_details(message: dict, bot: Pipeline):
    result = message["result"]
    outcome = f"refused ({result['reason']})" if result["fallback"] else result["mode"]
    st.markdown(f"**Search:** {bot.retriever.backend} · confidence {result['score']:.2f} "
                f"(needs {bot.threshold:.2f}) · **answer:** {outcome}")
    if result.get("issues"):
        st.markdown("**Fact guard flagged:** " + ", ".join(result["issues"]))
    st.markdown("**Closest topics:**\n" + "\n".join(
        f"- {chunk['title']} · {score:.2f}" for chunk, score in result["hits"][:3]))


def details_label(message: dict) -> str:
    result = message["result"]
    if result["fallback"]:
        return f"Searched the law · {message['seconds']:.1f} s"
    return f"Searched {len(result['sources'])} sources · {message['seconds']:.1f} s"


def render_answer(message: dict):
    result = message["result"]
    for note in result["notes"]:
        st.error(note, icon=":material/emergency:")
    if result["fallback"]:
        st.warning(result["answer"], icon=":material/help:")
        return

    st.markdown(result["answer"])
    if result["mode"] != "llm" and result["next"]:
        with st.container(border=True):
            st.markdown(f":material/directions: **What to do next**  \n{result['next']}")

    with st.container(horizontal=True, gap="small"):
        label, icon, color, help_text = MODE[result["mode"]]
        st.badge(label, icon=icon, color=color, help=help_text)
        label, icon, color, help_text = VERIFIED[result["verified"]]
        st.badge(label, icon=icon, color=color, help=help_text)
        topic, icon = DOMAIN.get(result["domain"], (result["domain"], ":material/balance:"))
        st.badge(topic, icon=icon, color="blue")

    with st.expander(f"Sources ({len(result['sources'])})", icon=":material/menu_book:"):
        for source in result["sources"]:
            link = f" · [open]({source['url']})" if source.get("url") else ""
            st.markdown(f"**[{source['n']}] {source['cite']}**{link}")
            st.caption(source["text"])
    st.caption(result["disclaimer"])


def render_message(message: dict, bot: Pipeline):
    with st.chat_message("user", avatar=":material/person:"):
        st.markdown(message["question"])
    with st.chat_message("assistant", avatar=str(ASSETS / "icon.svg")):
        with st.expander(details_label(message), icon=":material/travel_explore:", type="compact"):
            search_details(message, bot)
        render_answer(message)
        st.feedback("thumbs", key=f"feedback_{message['id']}", on_change=save_feedback, args=(message["id"],))


def welcome():
    columns = st.columns(2)
    for index, (icon, title, description) in enumerate(TOPICS):
        with columns[index % 2].container(border=True):
            st.markdown(f"{icon} **{title}**")
            st.caption(description)
    st.space("small")
    picked = st.pills("Try asking", list(SUGGESTIONS), key="suggestion")
    return SUGGESTIONS[picked] if picked else None


st.session_state.setdefault("messages", [])

with st.sidebar:
    st.button("New chat", icon=":material/add_comment:", width="stretch", on_click=new_chat)
    st.subheader("Need help now?", anchor=False)
    for icon, number, name in HELPLINES:
        st.markdown(f"{icon} **{number}** · {name}")
    with st.expander("How NyayaSaathi works", icon=":material/info:"):
        st.markdown(
            "1. Finds the closest checked summary and the matching sections of the official "
            "Act texts.\n"
            "2. Gemini writes a simple answer from those sources only.\n"
            "3. Every number and section in the answer is checked against the sources. If "
            "anything does not match, you see the checked summary instead.\n"
            "4. If it is not sure, it says so and points you to free legal help.")
    st.caption(config.DISCLAIMER)

prompt = st.chat_input("Describe your problem in your own words", submit_mode="disable")

st.title("Know your rights", anchor=False)
st.caption("Simple answers from Indian law about work, rent, shopping and safety at home.")

bot = load_pipeline()

if not st.session_state.messages:
    intro = st.empty()
    with intro.container():
        prompt = welcome() or prompt
    if prompt:
        intro.empty()

for message in st.session_state.messages:
    render_message(message, bot)

if prompt:
    message = {"id": uuid.uuid4().hex[:8], "question": prompt}
    with st.chat_message("user", avatar=":material/person:"):
        st.markdown(prompt)
    with st.chat_message("assistant", avatar=str(ASSETS / "icon.svg")):
        with st.status(":shimmer[Searching the law]", type="compact") as status:
            start_time = time.perf_counter()
            message["result"] = bot.ask(prompt)
            message["seconds"] = time.perf_counter() - start_time
            search_details(message, bot)
            status.update(label=details_label(message), state="complete")
        render_answer(message)
        st.feedback("thumbs", key=f"feedback_{message['id']}", on_change=save_feedback, args=(message["id"],))
    st.session_state.messages.append(message)
