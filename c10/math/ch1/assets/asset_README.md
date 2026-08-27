# NCERT Solutions Maker

The local gateway intentionally uses **only Python's standard library**.
There is no `pip install` step and no FastAPI/Uvicorn dependency.

Run `run.bat`.

The gateway:
- serves the HTML at http://127.0.0.1:8787
- reads GEMINI_API_KEY from `.env`
- sends question images directly to Gemini's REST API
- returns structured question JSON
- keeps the API key on the Python side

Required `.env`:
GEMINI_API_KEY=your_key_here

Optional:
GEMINI_MODEL=gemini-2.5-flash
