import streamlit as st
from groq import Groq
from datetime import datetime
import PyPDF2
from docx import Document
import io
import json
import re
import base64
from google.cloud import firestore
from google.oauth2 import service_account

# ============================================================
# 1. PAGE SETUP
# ============================================================
st.set_page_config(page_title="Study Buddy", page_icon="logo.png", layout="wide")

# --- 1b. BACKGROUND IMAGE ---
def set_background(image_path):
    with open(image_path, "rb") as f:
        img_data = base64.b64encode(f.read()).decode()
    st.markdown(
        f"""
        <style>
        .stApp {{
            background-image: url("data:image/png;base64,{img_data}");
            background-size: 320px;
            background-position: center center;
            background-repeat: no-repeat;
            background-attachment: fixed;
            background-color: #000000;
        }}

        /* Keep chat/quiz/flashcard content readable over the background */
        [data-testid="stChatMessage"], .stContainer, div[data-testid="stVerticalBlockBorderWrapper"] {{
            background-color: rgba(20, 20, 20, 0.85);
            border-radius: 12px;
        }}

        /* Sidebar stays solid dark so controls are legible */
        [data-testid="stSidebar"] {{
            background-color: rgba(10, 10, 10, 0.97);
        }}
        </style>
        """,
        unsafe_allow_html=True
    )

set_background("background.png")

# ============================================================
# 2. CLOUD DATABASE CONNECTION (Firestore)
# ============================================================
@st.cache_resource
def get_db():
    cred_dict = dict(st.secrets["firebase"])
    if "\\n" in cred_dict["private_key"]:
        cred_dict["private_key"] = cred_dict["private_key"].replace("\\n", "\n")
    creds = service_account.Credentials.from_service_account_info(cred_dict)
    return firestore.Client(credentials=creds, project=cred_dict["project_id"])

try:
    db = get_db()
    DB_AVAILABLE = True
except Exception as e:
    st.sidebar.warning(f"Cloud sync unavailable: {e}")
    DB_AVAILABLE = False

# ============================================================
# 3. PER-USER IDENTITY (fixes the "shared history" problem)
# ============================================================
if "user_id" not in st.session_state:
    st.session_state.user_id = None

if st.session_state.user_id is None:
    st.title("📚 Study Buddy")
    st.write("Enter a name or ID to keep your study materials separate from everyone else's.")
    entered_id = st.text_input("Your Study ID", placeholder="e.g. adrito_notes")
    if st.button("Continue", type="primary") and entered_id.strip():
        st.session_state.user_id = entered_id.strip().replace(" ", "_").lower()
        st.rerun()
    st.stop()

USER_ID = st.session_state.user_id

# ============================================================
# 4. GROQ CLIENT
# ============================================================
try:
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
except Exception:
    st.error("Missing Groq API Key in secrets!")
    st.stop()

MODEL = "openai/gpt-oss-120b"  # solid default for structured JSON generation

def ask_groq(system_prompt, user_prompt, json_mode=False):
    kwargs = dict(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content

def safe_json_parse(raw_text):
    """Strip code fences if present and parse JSON, raising a clear error if it fails."""
    cleaned = re.sub(r"^```(json)?|```$", "", raw_text.strip(), flags=re.MULTILINE).strip()
    return json.loads(cleaned)

# ============================================================
# 5. FILE PARSING
# ============================================================
def extract_text(file):
    fname = file.name.lower()
    try:
        if fname.endswith(('.txt', '.md')):
            return file.read().decode("utf-8")
        elif fname.endswith('.pdf'):
            reader = PyPDF2.PdfReader(file)
            return "\n".join(p.extract_text() for p in reader.pages if p.extract_text())
        elif fname.endswith('.docx'):
            doc = Document(io.BytesIO(file.read()))
            return "\n".join(p.text for p in doc.paragraphs)
    except Exception as e:
        st.error(f"File read error: {e}")
    return ""

# ============================================================
# 6. FIRESTORE HELPERS (per-user, per-material)
# ============================================================
def save_material(material_name, data):
    if not DB_AVAILABLE:
        return
    try:
        db.collection("study_buddy_users").document(USER_ID) \
          .collection("materials").document(material_name).set(data, merge=True)
    except Exception as e:
        st.sidebar.error(f"Save failed: {e}")

def load_materials():
    if not DB_AVAILABLE:
        return {}
    try:
        docs = db.collection("study_buddy_users").document(USER_ID).collection("materials").stream()
        return {d.id: d.to_dict() for d in docs}
    except Exception:
        return {}

# ============================================================
# 7. WELCOME POPUP
# ============================================================
if "popup_shown" not in st.session_state:
    st.session_state.popup_shown = False

@st.dialog("👋 Welcome!")
def show_welcome_box():
    st.markdown("""
    ### Hello! This is Adrito's Study Buddy.
    This chatbot is made by **Adrito Roy** and is open source.

    This buddy has cloud storage and the study chats sync with the cloud to your designated Study ID.
    Please note that if the Study Buddy exceeds 1 million messages in total, the study chat storage
    will reset because there is a fixed quota, and you will lose all of your saved study chats.

    🌐 **Repository link:**
    [study-buddy](https://github.com/windows7lover2000015/study-buddy/blob/main)

    🚀 **My other project(s):**
    [AI Chatbot](https://adritos-ai-chatbot.streamlit.app/)
    """)
    st.divider()
    if st.button("Ok!", use_container_width=True, type="primary"):
        st.session_state.popup_shown = True
        st.rerun()

if not st.session_state.popup_shown:
    show_welcome_box()

# ============================================================
# 8. SIDEBAR — upload + material picker
# ============================================================
with st.sidebar:
    st.image("logo.png", width=110)
    st.title("📚 Study Buddy")
    st.caption(f"Signed in as **{USER_ID}**")
    if st.button("Switch ID"):
        st.session_state.user_id = None
        st.rerun()

    st.divider()
    uploaded_file = st.file_uploader("Upload notes", type=['txt', 'md', 'pdf', 'docx'])

    if "materials" not in st.session_state:
        st.session_state.materials = load_materials()

    if uploaded_file:
        material_name = uploaded_file.name
        if material_name not in st.session_state.materials:
            with st.spinner("Reading file..."):
                text = extract_text(uploaded_file)
            if text:
                st.session_state.materials[material_name] = {"text": text}
                save_material(material_name, {"text": text, "uploaded_at": datetime.now().isoformat()})
                st.success(f"Added: {material_name}")

    st.divider()
    st.subheader("Your materials")
    material_names = list(st.session_state.materials.keys())
    if not material_names:
        st.info("Upload a file to get started.")
        st.stop()

    selected_material = st.radio("Pick one to study", material_names, key="selected_material")

current = st.session_state.materials[selected_material]
material_text = current.get("text", "")

# ============================================================
# 9. MAIN AREA — TABS
# ============================================================
st.title(f"📖 {selected_material}")
tab_summary, tab_quiz, tab_flash, tab_chat = st.tabs(
    ["📝 Summary", "🎯 Quiz", "🗂️ Flashcards", "💬 Ask Questions"]
)

# ---------- SUMMARY TAB ----------
with tab_summary:
    if "summary" not in current:
        if st.button("Generate Summary", type="primary"):
            with st.spinner("Summarizing..."):
                summary = ask_groq(
                    "You are a study assistant. Summarize the given notes into a short overview "
                    "paragraph followed by 5-10 concise key-point bullets in markdown. Be accurate "
                    "and do not invent information not present in the notes.",
                    material_text[:15000],
                )
                current["summary"] = summary
                save_material(selected_material, {"summary": summary})
                st.rerun()
    else:
        st.markdown(current["summary"])
        if st.button("Regenerate Summary"):
            del current["summary"]
            st.rerun()

# ---------- QUIZ TAB ----------
with tab_quiz:
    if "quiz" not in current:
        num_q = st.slider("Number of questions", 3, 30, 5)
        if st.button("Generate Quiz", type="primary"):
            with st.spinner("Writing quiz..."):
                raw = ask_groq(
                    "You are a quiz generator. Based ONLY on the provided notes, create "
                    f"{num_q} multiple-choice questions. Respond ONLY with valid JSON in this exact "
                    'shape: {"questions": [{"question": str, "options": [str, str, str, str], '
                    '"correct_index": int, "explanation": str}]}. No markdown, no commentary.',
                    material_text[:15000],
                    json_mode=True,
                )
                try:
                    quiz_data = safe_json_parse(raw)
                    current["quiz"] = quiz_data["questions"]
                    save_material(selected_material, {"quiz": quiz_data["questions"]})
                    st.rerun()
                except Exception as e:
                    st.error(f"Couldn't parse quiz output, try again. ({e})")
    else:
        quiz = current["quiz"]
        if "quiz_answers" not in st.session_state:
            st.session_state.quiz_answers = {}

        for i, q in enumerate(quiz):
            st.markdown(f"**{i+1}. {q['question']}**")
            choice = st.radio(
                "Choose one:", q["options"], key=f"quiz_{selected_material}_{i}",
                index=None, label_visibility="collapsed"
            )
            if choice is not None:
                chosen_idx = q["options"].index(choice)
                st.session_state.quiz_answers[i] = chosen_idx
                if chosen_idx == q["correct_index"]:
                    st.success(f"Correct! {q['explanation']}")
                else:
                    st.error(f"Not quite. Correct answer: **{q['options'][q['correct_index']]}**. {q['explanation']}")
            st.divider()

        if st.session_state.quiz_answers:
            score = sum(
                1 for i, ans in st.session_state.quiz_answers.items()
                if ans == quiz[i]["correct_index"]
            )
            st.metric("Score", f"{score} / {len(quiz)}")

        if st.button("Regenerate Quiz"):
            del current["quiz"]
            st.session_state.quiz_answers = {}
            st.rerun()

# ---------- FLASHCARDS TAB ----------
with tab_flash:
    if "flashcards" not in current:
        num_f = st.slider("Number of flashcards", 5, 30, 10)
        if st.button("Generate Flashcards", type="primary"):
            with st.spinner("Making flashcards..."):
                raw = ask_groq(
                    "You are a flashcard generator. Based ONLY on the provided notes, create "
                    f"{num_f} flashcards covering key terms/concepts. Respond ONLY with valid JSON: "
                    '{"cards": [{"front": str, "back": str}]}. No markdown, no commentary.',
                    material_text[:15000],
                    json_mode=True,
                )
                try:
                    card_data = safe_json_parse(raw)
                    current["flashcards"] = card_data["cards"]
                    save_material(selected_material, {"flashcards": card_data["cards"]})
                    st.rerun()
                except Exception as e:
                    st.error(f"Couldn't parse flashcard output, try again. ({e})")
    else:
        cards = current["flashcards"]
        if "card_index" not in st.session_state:
            st.session_state.card_index = 0
        if "card_flipped" not in st.session_state:
            st.session_state.card_flipped = False

        idx = st.session_state.card_index % len(cards)
        card = cards[idx]

        st.caption(f"Card {idx + 1} of {len(cards)}")
        face_text = card["back"] if st.session_state.card_flipped else card["front"]
        st.container(border=True).markdown(f"### {face_text}")

        c1, c2, c3 = st.columns(3)
        if c1.button("⬅️ Prev"):
            st.session_state.card_index = (idx - 1) % len(cards)
            st.session_state.card_flipped = False
            st.rerun()
        if c2.button("🔄 Flip"):
            st.session_state.card_flipped = not st.session_state.card_flipped
            st.rerun()
        if c3.button("Next ➡️"):
            st.session_state.card_index = (idx + 1) % len(cards)
            st.session_state.card_flipped = False
            st.rerun()

        if st.button("Regenerate Flashcards"):
            del current["flashcards"]
            st.session_state.card_index = 0
            st.rerun()

# ---------- CHAT Q&A TAB ----------
with tab_chat:
    chat_key = f"chat_{selected_material}"
    if chat_key not in st.session_state:
        st.session_state[chat_key] = current.get("chat_history", [])

    for msg in st.session_state[chat_key]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if q := st.chat_input("Ask something about this material..."):
        st.session_state[chat_key].append({"role": "user", "content": q})
        with st.chat_message("user"):
            st.markdown(q)

        with st.chat_message("assistant"):
            placeholder = st.empty()
            full_res = ""
            system_prompt = (
                "You are a study tutor. Answer the user's question using ONLY the notes provided "
                "below. If the answer isn't in the notes, say so honestly rather than guessing.\n\n"
                f"[NOTES]\n{material_text[:15000]}"
            )
            stream = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "system", "content": system_prompt}] +
                         st.session_state[chat_key][-10:],
                stream=True,
            )
            for chunk in stream:
                if chunk.choices[0].delta.content:
                    full_res += chunk.choices[0].delta.content
                    placeholder.markdown(full_res + "▌")
            placeholder.markdown(full_res)
            st.session_state[chat_key].append({"role": "assistant", "content": full_res})
            save_material(selected_material, {"chat_history": st.session_state[chat_key]})
