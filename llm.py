"""Тексты постов пишет Google Gemini (бесплатный тариф)."""
import json
import os
import time

import requests

MODELS = [m for m in [os.getenv("GEMINI_MODEL"), "gemini-flash-latest", "gemini-2.5-flash", "gemini-2.0-flash"] if m]
URL = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"


def ask_json(system: str, prompt: str, temperature: float = 0.6) -> dict:
    key = os.environ["GEMINI_API_KEY"]
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"},
    }
    last_err = None
    for model in MODELS:
        for attempt in range(4):
            try:
                r = requests.post(URL.format(model), headers={"x-goog-api-key": key}, json=body, timeout=120)
            except requests.RequestException as e:
                last_err = e
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code == 404:  # модель недоступна — пробуем следующую
                last_err = f"{model}: 404"
                break
            if r.status_code in (429, 500, 502, 503, 504):
                last_err = f"{model}: {r.status_code} {r.text[:200]}"
                time.sleep(15 * (attempt + 1))
                continue
            if not r.ok:
                raise RuntimeError(f"Gemini {model}: {r.status_code} {r.text[:500]}")
            data = r.json()
            try:
                parts = data["candidates"][0]["content"]["parts"]
            except (KeyError, IndexError):
                last_err = f"{model}: пустой ответ {str(data)[:300]}"
                continue
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            try:
                return json.loads(text)
            except json.JSONDecodeError as e:
                last_err = f"{model}: невалидный JSON: {e}"
                continue
    raise RuntimeError(f"Gemini не ответил: {last_err}")
