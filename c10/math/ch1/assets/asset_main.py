from __future__ import annotations

import base64
import json
import os
import re
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.parser import BytesParser
from email.policy import default
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
import urllib.parse

ROOT = Path(__file__).resolve().parent.parent
HOST = "127.0.0.1"
PORT = 8787

def load_dotenv():
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value

load_dotenv()

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "questionText": {"type": "string"},
                    "warn": {"type": "string", "enum": ["yes", "no"]},
                    "source": {
                        "type": "object",
                        "properties": {
                            "exercise": {"type": "string"},
                            "number": {"type": "string"},
                        },
                        "required": ["exercise", "number"],
                    },
                    "options": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["questionText", "warn", "source", "options"],
            },
        }
    },
    "required": ["questions"],
}

EXTRACTION_PROMPT = """You are the extraction stage of an NCERT mathematics content pipeline.

Your ONLY job is to transcribe and structure the questions visible in the supplied question-page images.

Rules:
1. Extract ALL questions under ALL exercises visible in the supplied pages.
2. Preserve exercise names/numbers and question numbering exactly as printed.
3. Do NOT solve any question.
4. Do NOT paraphrase, improve, correct, normalize, or invent question content.
5. Preserve mathematical notation as accurately as possible in plain text/Unicode.
6. If a question contains a diagram, graph, figure, construction, photograph, or any other visual that is necessary to understand the question:
   - transcribe the surrounding printed text;
   - insert "[IMAGE/DIAGRAM — HUMAN REVIEW REQUIRED]";
   - set warn="yes".
7. NEVER infer, describe, reconstruct, or guess information contained only in the visual.
8. If the visual is decorative and irrelevant, warn may remain "no".
9. If uncertain whether something is image-dependent, set warn="yes".
10. Extract MCQ options separately in printed order. For non-MCQ questions use an empty options array.
11. Do not merge separately numbered questions.
12. Do not omit a question because its image is unclear. Extract what is legible and set warn="yes".
13. Return ONLY the requested JSON structure.
"""

def now():
    return datetime.now(timezone.utc).isoformat()

def send_json(handler, status, payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(data)

def read_multipart(handler):
    length = int(handler.headers.get("Content-Length", "0"))
    body = handler.rfile.read(length)
    content_type = handler.headers.get("Content-Type", "")
    if "multipart/form-data" not in content_type:
        raise ValueError("Expected multipart/form-data.")

    fake_headers = (
        f"Content-Type: {content_type}\r\n"
        "MIME-Version: 1.0\r\n\r\n"
    ).encode("utf-8")
    message = BytesParser(policy=default).parsebytes(fake_headers + body)

    fields = {}
    files = {}
    for part in message.iter_parts():
        disposition = part.get("Content-Disposition", "")
        name_match = re.search(r'name="([^"]*)"', disposition)
        if not name_match:
            continue
        name = name_match.group(1)
        filename_match = re.search(r'filename="([^"]*)"', disposition)
        payload = part.get_payload(decode=True) or b""
        if filename_match:
            item = {
                "filename": filename_match.group(1),
                "content_type": part.get_content_type(),
                "data": payload,
            }
            files.setdefault(name, []).append(item)
        else:
            charset = part.get_content_charset() or "utf-8"
            fields[name] = payload.decode(charset, errors="replace")
    return fields, files

def gemini_extract(image_files):
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY is not configured. Add GEMINI_API_KEY=... to .env and restart the server."
        )

    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
    parts = [{"text": EXTRACTION_PROMPT}]

    for item in image_files:
        data = item["data"]
        if not data:
            continue
        mime = item["content_type"] or "image/png"
        parts.append({
            "inline_data": {
                "mime_type": mime,
                "data": base64.b64encode(data).decode("ascii"),
            }
        })

    if len(parts) == 1:
        raise RuntimeError("The uploaded question images were empty.")

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseSchema": EXTRACTION_SCHEMA,
        },
    }

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        + model
        + ":generateContent?key="
        + urllib.parse.quote(key, safe="")
    )
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini API error ({e.code}): {detail}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach Gemini: {e.reason}")

    result = json.loads(raw)
    try:
        text = result["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("Gemini returned no text content: " + json.dumps(result)[:2000])

    try:
        return json.loads(text), model
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Gemini returned invalid JSON: {e}")

class Handler(BaseHTTPRequestHandler):
    server_version = "NCERTSolutionsMaker/0.3"

    def log_message(self, fmt, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {fmt % args}")

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/api/health":
            send_json(self, 200, {
                "ok": True,
                "time": now(),
                "geminiConfigured": bool(os.getenv("GEMINI_API_KEY")),
                "geminiModel": os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            })
            return

        if path == "/api/providers":
            send_json(self, 200, {
                "providers": [{
                    "family": "Gemini",
                    "configured": bool(os.getenv("GEMINI_API_KEY")),
                    "model": os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
                }]
            })
            return

        self.serve_static(path)

    def serve_static(self, path):
        relative = path.lstrip("/") or "index.html"
        target = (ROOT / relative).resolve()

        try:
            target.relative_to(ROOT.resolve())
        except ValueError:
            self.send_error(403)
            return

        if target.is_dir():
            target = target / "index.html"

        if not target.exists() or not target.is_file():
            self.send_error(404)
            return

        mime = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".md": "text/plain; charset=utf-8",
        }.get(target.suffix.lower(), "application/octet-stream")

        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/api/extract":
            try:
                fields, files = read_multipart(self)
                question_images = files.get("question_images", [])
                if not question_images:
                    raise ValueError("No question images were uploaded.")

                payload, model = gemini_extract(question_images)
                questions = []

                for i, q in enumerate(payload.get("questions", [])):
                    source = q.get("source") or {}
                    questions.append({
                        "id": f"q_{i+1}_{os.urandom(4).hex()}",
                        "candidateId": f"candidate_{os.urandom(4).hex()}",
                        "questionText": q.get("questionText", ""),
                        "warn": "yes" if q.get("warn") == "yes" else "no",
                        "options": q.get("options") or [],
                        "source": {
                            "exercise": source.get("exercise", ""),
                            "number": source.get("number", ""),
                        },
                        "extraction": {
                            "status": "done",
                            "provider": "Gemini",
                            "model": model,
                            "timestamp": now(),
                        },
                        "pipeline": {
                            "solvers": [],
                            "verification": {"status": "pending"},
                            "review": {"status": "pending"},
                            "final": {"status": "pending"},
                        },
                    })

                send_json(self, 200, {
                    "status": "done",
                    "provider": "Gemini",
                    "model": model,
                    "chapter": {
                        "class": fields.get("className", ""),
                        "subject": fields.get("subject", ""),
                        "chapter": fields.get("chapter", ""),
                    },
                    "questions": questions,
                })
            except Exception as e:
                print("EXTRACTION ERROR:", repr(e))
                send_json(self, 500, {"error": str(e)})
            return

        if path == "/api/pipeline/solve":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                solvers = [{
                    "status": "done",
                    "modelFamily": family,
                    "solutionText": f"[STUB] Connect {family} adapter for independent solution generation.",
                } for family in ["Gemini", "DeepSeek", "Qwen"]]
                send_json(self, 200, {"pipeline": {
                    "solvers": solvers,
                    "verification": {"status": "uncertain", "result": "UNCERTAIN"},
                    "review": {
                        "bookRelevance": {
                            "status": "pending",
                            "reason": "Book relevance checker not connected yet.",
                        }
                    },
                    "final": {
                        "status": "done",
                        "candidate": {
                            "solutionText": solvers[0]["solutionText"],
                            "modelFamily": "Gemini",
                        },
                    },
                }})
            except Exception as e:
                send_json(self, 500, {"error": str(e)})
            return

        self.send_error(404)

if __name__ == "__main__":
    print("=" * 52)
    print("  NCERT Solutions Maker - Local Gateway")
    print("=" * 52)
    print(f"  Python: {os.sys.version.split()[0]}")
    print(f"  URL:    http://{HOST}:{PORT}")
    print(f"  Gemini: {'configured' if os.getenv('GEMINI_API_KEY') else 'NOT configured'}")
    print()
    print("  No pip packages are required.")
    print("  Press Ctrl+C to stop.")
    print("=" * 52)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
