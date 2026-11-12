"""Gemini diagnostics for the AI tutor (prints model list and response times; never prints the key)."""
import os
import time

import requests

KEY = os.environ.get("GEMINI_API_KEY", "")
H = {"x-goog-api-key": KEY, "content-type": "application/json"}
BASE = "https://generativelanguage.googleapis.com/v1beta"

r = requests.get(f"{BASE}/models?pageSize=200", headers=H, timeout=30)
print("list models:", r.status_code)
names = [m["name"].split("/")[-1] for m in r.json().get("models", []) if "generateContent" in m.get("supportedGenerationMethods", [])]
print("flash models:", [n for n in names if "flash" in n][:40])
for model in ("gemini-flash-latest", "gemini-flash-lite-latest", "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"):
    t0 = time.time()
    try:
        r = requests.post(f"{BASE}/models/{model}:generateContent", headers=H, timeout=90,
                          json={"contents": [{"parts": [{"text": "తెలుగులో ఒక వాక్యం: stop-loss అంటే ఏమిటి?"}]}],
                                "generationConfig": {"maxOutputTokens": 300}})
        txt = r.text[:160].replace("\n", " ")
        print(f"{model}: HTTP {r.status_code} in {time.time() - t0:.1f}s :: {txt}")
    except Exception as exc:
        print(f"{model}: {type(exc).__name__} after {time.time() - t0:.1f}s")
