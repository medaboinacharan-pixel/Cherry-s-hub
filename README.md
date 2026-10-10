# Cherry's Hub — with DPI

A responsive personal dashboard with a FastAPI backend, SQLite database, task reminders while the page is open, and DPI assistant.

## Requirements
- Windows 10/11
- Python 3.11 or newer
- Internet only to install packages or use the optional Gemini API

## Run on Windows
1. Extract this folder to a location such as `Documents\Cherrys-Hub`.
2. Install Python from https://www.python.org/downloads/ if needed. During installation, select **Add Python to PATH**.
3. Double-click `run_windows.bat`. The first launch installs dependencies.
4. Open `http://127.0.0.1:8000` in Chrome or Edge.
5. Keep the terminal window open while using the app. Stop the server with Ctrl+C.

## What's included
- Sections: Tasks, Gym, Study, Projects, Notes, Expenses, Files & links
- SQLite persistence in `cherrys_hub.db`
- Add, delete, and complete tasks
- Optional reminder date/time and browser notification permission
- DPI chat with an offline helper for daily planning, tasks, study, gym and expenses
- Optional Gemini-backed chat via backend environment variable
- API docs at `http://127.0.0.1:8000/docs`

## Optional generative AI
The app works without an API key. To enable Gemini:
1. Get a key from Google AI Studio and check the current free-tier availability/limits for your account and region.
2. Set `GEMINI_API_KEY` as a Windows environment variable in the same terminal before starting the app, for example in PowerShell:
   `$env:GEMINI_API_KEY="YOUR_KEY_HERE"`
   `.venv\Scripts\python.exe -m uvicorn main:app --reload`
3. Keep the key secret. Never place it in `static/index.html`, commit it, or share it.
If the API is unavailable or the key is absent, DPI uses the offline fallback.

## Notes / limitations
- This is a local-first starter app. SQLite data stays on this computer. It does not sync to Android automatically.
- Browser reminders work only while the app/page remains open; browser/OS settings can affect delivery. Reliable closed-app push notifications require HTTPS, a service worker, and a push service.
- Do not expose this server to the public internet without adding authentication and security protections.
- To move old entries from the previous HTML-only LifeHub version, export its JSON backup first. This starter app does not automatically import the old browser localStorage data.
