from contextlib import asynccontextmanager
from datetime import datetime, date, timedelta
import re
from pathlib import Path
import json
import os
import sqlite3
import uuid
import time

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from openai import OpenAI


# =========================================================
# CONFIGURATION
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "cherrys_hub.db"
STATIC_DIR = BASE_DIR / "static"

GEMINI_MODEL = "gemini-3.8-flash"
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

SECTIONS = {
    "tasks", "gym", "notes", "expenses",
    "projects", "files", "study",
}


# =========================================================
# DATABASE
# =========================================================

def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with connect() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                id TEXT PRIMARY KEY,
                section TEXT NOT NULL,
                title TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                entry_date TEXT NOT NULL DEFAULT '',
                amount REAL,
                done INTEGER NOT NULL DEFAULT 0,
                reminder_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not STATIC_DIR.exists():
        raise RuntimeError(f"Static folder not found: {STATIC_DIR}")
    init_db()
    yield


app = FastAPI(
    title="Cherry's Hub API",
    version="1.1.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# =========================================================
# REQUEST MODELS
# =========================================================

class EntryIn(BaseModel):
    section: str
    title: str = Field(min_length=1, max_length=120)
    details: str = Field(default="", max_length=5000)
    entry_date: str = ""
    amount: float | None = None
    done: bool = False
    reminder_at: str = ""


class EntryUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    details: str | None = Field(default=None, max_length=5000)
    entry_date: str | None = None
    amount: float | None = None
    done: bool | None = None
    reminder_at: str | None = None


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


# =========================================================
# HELPERS
# =========================================================

def row_to_entry(row):
    item = dict(row)
    item["done"] = bool(item["done"])
    return item


def get_entries():
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM entries ORDER BY created_at DESC"
        ).fetchall()
    return [row_to_entry(row) for row in rows]


def save_chat_message(role: str, message: str):
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as db:
        db.execute(
            """
            INSERT INTO chat_messages (role, message, created_at)
            VALUES (?, ?, ?)
            """,
            (role, message, now),
        )


# =========================================================
# HOME AND HEALTH
# =========================================================

@app.get("/")
def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "app": "Cherry's Hub",
        "assistant": "DPI",
        "ai_provider": "ollama",
        "ai_model": OLLAMA_MODEL,
    }


# =========================================================
# ENTRIES
# =========================================================

@app.get("/api/entries")
def list_entries(section: str | None = None):
    with connect() as db:
        if section:
            if section not in SECTIONS:
                raise HTTPException(400, "Unknown section")
            rows = db.execute(
                "SELECT * FROM entries WHERE section=? ORDER BY created_at DESC",
                (section,),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM entries ORDER BY created_at DESC"
            ).fetchall()
    return [row_to_entry(row) for row in rows]


@app.post("/api/entries")
def create_entry(payload: EntryIn):
    title = payload.title.strip()
    if payload.section not in SECTIONS:
        raise HTTPException(400, "Unknown section")
    if not title:
        raise HTTPException(400, "Title cannot be empty")
    if payload.section == "expenses":
        if payload.amount is None or payload.amount < 0:
            raise HTTPException(400, "Enter a valid non-negative expense amount")

    item_id = str(uuid.uuid4())
    created = datetime.now().isoformat(timespec="seconds")

    with connect() as db:
        db.execute(
            """
            INSERT INTO entries (
                id, section, title, details, entry_date,
                amount, done, reminder_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id, payload.section, title, payload.details.strip(),
                payload.entry_date, payload.amount, int(payload.done),
                payload.reminder_at, created,
            ),
        )
        row = db.execute(
            "SELECT * FROM entries WHERE id=?", (item_id,)
        ).fetchone()
    return row_to_entry(row)


@app.patch("/api/entries/{item_id}")
def update_entry(item_id: str, payload: EntryUpdate):
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(400, "No fields supplied")

    allowed = {
        "title", "details", "entry_date",
        "amount", "done", "reminder_at",
    }
    if set(fields) - allowed:
        raise HTTPException(400, "Unsupported fields")

    if "title" in fields:
        if fields["title"] is None or not fields["title"].strip():
            raise HTTPException(400, "Title cannot be empty")
        fields["title"] = fields["title"].strip()

    if "details" in fields and fields["details"] is None:
        raise HTTPException(400, "Details cannot be null")

    if "done" in fields and fields["done"] is not None:
        fields["done"] = int(fields["done"])

    if "amount" in fields and fields["amount"] is not None:
        if fields["amount"] < 0:
            raise HTTPException(400, "Amount cannot be negative")

    sql = ", ".join(f"{key}=?" for key in fields)
    values = list(fields.values()) + [item_id]

    with connect() as db:
        cur = db.execute(
            f"UPDATE entries SET {sql} WHERE id=?", values
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Entry not found")
        row = db.execute(
            "SELECT * FROM entries WHERE id=?", (item_id,)
        ).fetchone()
    return row_to_entry(row)


@app.delete("/api/entries/{item_id}")
def delete_entry(item_id: str):
    with connect() as db:
        cur = db.execute("DELETE FROM entries WHERE id=?", (item_id,))
    if cur.rowcount == 0:
        raise HTTPException(404, "Entry not found")
    return {"deleted": True}


# =========================================================
# OFFLINE DPI FALLBACK
# =========================================================

def local_dpi_reply(message: str, entries: list[dict]) -> str:
    text = message.lower()
    today = date.today().isoformat()

    open_tasks = [
        e for e in entries
        if e["section"] == "tasks" and not e["done"]
    ]
    today_items = [
        e for e in entries if e.get("entry_date") == today
    ]

    # UI/UX requests are checked before general study/plan keywords.
    if any(term in text for term in (
        "ui/ux", "ui ux", "ux design", "ui design",
        "user interface", "user experience", "figma",
    )):
        return (
            "Here is your practical 30-minute UI/UX study plan:\n\n"
            "00-05 min — Learn: Review visual hierarchy, contrast, "
            "spacing, and typography.\n\n"
            "05-10 min — Observe: Study a mobile login screen and "
            "identify its input fields, button, and text hierarchy.\n\n"
            "10-20 min — Create: Open Figma and design a mobile "
            "login screen with email, password, Login, and "
            "Forgot Password elements.\n\n"
            "20-25 min — Improve: Fix alignment, spacing, readability, "
            "and button visibility.\n\n"
            "25-30 min — Review: Find and fix three design issues "
            "and save your work.\n\n"
            "Goal: Finish one simple login screen. Start now!"
        )

    if any(term in text for term in ("task", "pending", "unfinished", "todo")):
        result = f"You have {len(open_tasks)} unfinished task(s)."
        if open_tasks:
            result += " Start with: " + "; ".join(
                e["title"] for e in open_tasks[:5]
            ) + "."
        else:
            result += " Nice work—your task list is clear."
        return result

    if any(term in text for term in ("expense", "spent", "money", "budget")):
        total = sum(
            float(e.get("amount") or 0)
            for e in entries if e["section"] == "expenses"
        )
        return (
            f"Your saved expenses total ₹{total:,.2f}. "
            "Add each expense to keep this total up to date."
        )

    if any(term in text for term in ("gym", "workout", "fitness")):
        gym = [e for e in entries if e["section"] == "gym"]
        return (
            f"You have {len(gym)} gym note(s) saved. Warm up, train "
            "your planned muscle groups with controlled form, and "
            "log your sets, reps, and weights."
        )

    if any(term in text for term in (
        "study", "exam", "coding", "python", "javascript"
    )):
        study = [e for e in entries if e["section"] == "study"]
        return (
            f"You have {len(study)} study item(s) saved. Choose one "
            "topic, study for 25 minutes, solve a practice question, "
            "and write down what you learned."
        )

    if any(term in text for term in ("today", "plan", "what should", "schedule")):
        parts = ["Here's a practical plan from your Cherry's Hub data:"]
        if today_items:
            parts.append(
                "For today: " + "; ".join(
                    e["title"] for e in today_items[:5]
                ) + "."
            )
        if open_tasks:
            parts.append(
                "Open tasks: " + "; ".join(
                    e["title"] for e in open_tasks[:5]
                ) + "."
            )
        if not today_items and not open_tasks:
            parts.append(
                "Your saved entries do not contain scheduled items "
                "or unfinished tasks yet."
            )
        parts.append(
            "Choose one important task, work for 25 minutes, "
            "take a 5-minute break, and review your progress."
        )
        return " ".join(parts)

    return (
        "I'm DPI, your assistant inside Cherry's Hub. I can help "
        "organize tasks, create study plans, review gym routines, "
        "and summarize saved expenses.\n\n"
        "Offline mode is active, so responses are generated locally."
    )



# =========================================================
# LOCAL OLLAMA AI — DPI
# =========================================================

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
OLLAMA_MODEL = "llama3.2:3b"


def gemini_reply(message: str, entries: list[dict]) -> str:
    """Generate DPI's reply using local Ollama."""

    instructions = (
        "You are DPI, Cherry's personal AI assistant inside Cherry's Hub. "
        "Be warm, supportive, practical, and concise. "
        "Reply in the same language as the user, including Telugu, "
        "Hindi, English, or mixed languages. "
        "Help with college, coding, gym, projects, habits, and everyday life. "
        "Do not pretend to be human. "
        "Use the saved entries below when relevant. "
        "Never claim that you created or changed a task yourself. "
        "Task changes are handled by the application's backend."
    )

    prompt = (
        "Saved entries from Cherry's Hub:\n"
        + json.dumps(entries[:100], ensure_ascii=False, default=str)
        + "\n\nUser message:\n"
        + message
    )

    try:
        response = httpx.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "stream": False,
                "messages": [
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": prompt},
                ],
                "options": {
                    "temperature": 0.7,
                    "num_predict": 500,
                },
            },
            timeout=180.0,
        )
        response.raise_for_status()

        data = response.json()
        reply = data.get("message", {}).get("content", "").strip()

        if reply:
            return reply

        return local_dpi_reply(message, entries)

    except httpx.ConnectError:
        print("DPI: Ollama is not running.", flush=True)
        return (
            "DPI's local AI is not running right now. "
            "Please start Ollama and try again. "
            + local_dpi_reply(message, entries)
        )

    except (httpx.TimeoutException, httpx.HTTPStatusError, ValueError) as exc:
        print("DPI Ollama error:", type(exc).__name__, flush=True)
        return (
            "DPI's AI is temporarily unavailable. "
            + local_dpi_reply(message, entries)
        )

    except Exception as exc:
        print("DPI unexpected error:", type(exc).__name__, flush=True)
        return local_dpi_reply(message, entries)




# =========================================================
# DPI CHAT
# =========================================================


def try_create_task_from_chat(message: str):
    """Create a task from explicit add/create/save-task commands."""
    raw = message.strip()

    patterns = [
        r"^(?:dpi[, ]*)?(?:please\s+)?(?:add|create|save|put)\s+(?:a\s+)?(?:new\s+)?task\s+(?:to\s+)?(.+)$",
        r"^(?:dpi[, ]*)?(?:please\s+)?(?:add|create|save|put)\s+(.+?)\s+to\s+my\s+tasks$",
        r"^(?:dpi[, ]*)?(?:please\s+)?remind\s+me\s+to\s+(.+)$",
        r"^(?:dpi[, ]*)?(?:please\s+)?i\s+need\s+to\s+remember\s+to\s+(.+)$",
    ]

    match = None

    for pattern in patterns:
        match = re.match(pattern, raw, flags=re.I)
        if match:
            break

    if not match:
        return None

    title = match.group(1).strip().strip(" .!?")

    if not title:
        return None

    now = datetime.now()
    target_date = None
    hour = None
    minute = None

    # Extract the target date.
    if re.search(r"\btomorrow\b", title, re.I):
        target_date = now.date() + timedelta(days=1)

    elif re.search(r"\btoday\b", title, re.I):
        target_date = now.date()

    else:
        dm = re.search(
            r"\b(?:on\s+)?(20\d{2}-\d{2}-\d{2})\b",
            title,
            flags=re.I,
        )

        if dm:
            try:
                target_date = date.fromisoformat(dm.group(1))
            except ValueError:
                target_date = None

    # Extract 12-hour time, such as 7 PM or 7:30 AM.
    tm = re.search(
        r"\b(?:at\s*)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b",
        title,
        flags=re.I,
    )

    if tm:
        h = int(tm.group(1))
        m = int(tm.group(2) or "0")
        meridiem = tm.group(3).lower()

        if 1 <= h <= 12 and 0 <= m <= 59:
            hour = (h % 12) + (12 if meridiem == "pm" else 0)
            minute = m

    else:
        # Extract 24-hour time, such as 19:00 or 07:30.
        tm24 = re.search(
            r"\bat\s+([01]?\d|2[0-3]):([0-5]\d)\b",
            title,
            flags=re.I,
        )

        if tm24:
            hour = int(tm24.group(1))
            minute = int(tm24.group(2))

    # Remove date and time phrases from the task title.
    cleaned = title

    cleaned = re.sub(
        r"\btomorrow\b|\btoday\b",
        "",
        cleaned,
        flags=re.I,
    )

    cleaned = re.sub(
        r"\bon\s+20\d{2}-\d{2}-\d{2}\b",
        "",
        cleaned,
        flags=re.I,
    )

    cleaned = re.sub(
        r"\b(?:at\s*)?\d{1,2}(?::\d{2})?\s*(?:am|pm)\b",
        "",
        cleaned,
        flags=re.I,
    )

    cleaned = re.sub(
        r"\bat\s+([01]?\d|2[0-3]):([0-5]\d)\b",
        "",
        cleaned,
        flags=re.I,
    )

    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-")

    if cleaned:
        title = cleaned[:120]
    else:
        return None

    entry_date = target_date.isoformat() if target_date else ""
    reminder_at = ""

    if target_date and hour is not None and minute is not None:
        reminder_at = datetime.combine(
            target_date,
            datetime.min.time(),
        ).replace(
            hour=hour,
            minute=minute,
        ).isoformat(timespec="minutes")

    item_id = str(uuid.uuid4())
    created = datetime.now().isoformat(timespec="seconds")

    with connect() as db:
        db.execute(
            """
            INSERT INTO entries
            (id, section, title, details, entry_date, amount,
             done, reminder_at, created_at)
            VALUES (?, 'tasks', ?, '', ?, NULL, 0, ?, ?)
            """,
            (
                item_id,
                title,
                entry_date,
                reminder_at,
                created,
            ),
        )

        row = db.execute(
            "SELECT * FROM entries WHERE id = ?",
            (item_id,),
        ).fetchone()

    return row_to_entry(row)




@app.post("/api/chat")
def chat(payload: ChatIn):
    message = payload.message.strip()

    if not message:
        raise HTTPException(400, "Message cannot be empty")

    # Create explicit task commands directly in SQLite first.
    created_entry = try_create_task_from_chat(message)
    entries = get_entries()

    if created_entry:
        when = ""

        if created_entry.get("reminder_at"):
            when = " for " + datetime.fromisoformat(
                created_entry["reminder_at"]
            ).strftime("%b %d at %I:%M %p").lstrip("0")

        elif created_entry.get("entry_date"):
            when = " for " + date.fromisoformat(
                created_entry["entry_date"]
            ).strftime("%b %d")

        reply = (
            f"Done! I've added '{created_entry['title']}'"
            f"{when} to your tasks."
        )

        save_chat_message("user", message)
        save_chat_message("assistant", reply)

        return {
            "reply": reply,
            "created_entry": created_entry,
            "entries": entries,
        }

    # Use local Ollama for ordinary conversation.
    reply = gemini_reply(message, entries)

    save_chat_message("user", message)
    save_chat_message("assistant", reply)

    return {
        "reply": reply,
        "created_entry": None,
        "entries": entries,
    }


# =========================================================
# CHAT HISTORY
# =========================================================

@app.get("/api/chat/history")
def chat_history():
    with connect() as db:
        rows = db.execute(
            """
            SELECT role, message, created_at
            FROM chat_messages
            ORDER BY id DESC
            LIMIT 40
            """
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


# =========================================================
# DASHBOARD SUMMARY
# =========================================================

@app.get("/api/summary")
def summary():
    entries = get_entries()
    tasks = [e for e in entries if e["section"] == "tasks"]
    expenses = sum(
        float(e.get("amount") or 0)
        for e in entries if e["section"] == "expenses"
    )

    return {
        "tasks_done": sum(1 for e in tasks if e["done"]),
        "tasks_total": len(tasks),
        "expenses": round(expenses, 2),
        "entries_total": len(entries),
    }
