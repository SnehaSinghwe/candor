"""Setup check: python3 doctor.py   (prints OS, Python, SDK, key presence, and makes ONE tiny Gemini call)."""
import os, platform, sys
from candor import safety

print(f"OS: {platform.platform()}\nPython: {sys.version.split()[0]}", "(OK)" if sys.version_info >= (3, 10) else "(Gemini SDK needs 3.10+; core still works on 3.9)")
try:
    import google.genai as g; print("google-genai: installed", getattr(g, "__version__", ""))
except ImportError:
    print("google-genai: NOT installed -> pip install -r requirements.txt")
key = bool(os.environ.get("GEMINI_API_KEY"))
print("GEMINI_API_KEY: " + ("set (value hidden)" if key else "not set (put it in .env)"), "| model:", safety.gemini_model())
if key:
    out = safety.gemini_json('Reply with JSON only: {"ok": true}', "ping", 50)
    print("Gemini call:", "OK -> " + out.strip()[:60] if out else "FAILED (see [candor] line above; try another GEMINI_MODEL)")
