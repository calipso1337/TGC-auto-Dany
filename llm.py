"""Тексты постов пишет Google Gemini (бесплатный тариф)."""
import json
import os
import re
import time

import requests

API = "https://generativelanguage.googleapis.com/v1beta"
PREFERRED = ["gemini-flash-latest", "gemini-2.5-flash", "gemini-flash-lite-latest", "gemini-2.5-flash-lite"]
_models_cache = None


def _headers():
    return {"x-goog-api-key": os.environ["GEMINI_API_KEY"].strip()}


def available_models():
    """Спрашиваем у Google, какие модели доступны этому ключу, и выбираем подходящие flash-модели."""
    global _models_cache
    if _models_cache is not None:
        return _models_cache
    names = []
    try:
        r = requests.get(f"{API}/models", params={"pageSize": 1000}, headers=_headers(), timeout=30)
        if r.ok:
            names = [m["name"].removeprefix("models/") for m in r.json().get("models", [])
                     if "generateContent" in m.get("supportedGenerationMethods", [])]
        else:
            print(f"  ⚠️ Gemini: не удалось получить список моделей: {r.status_code} {r.text[:300]}", flush=True)
    except requests.RequestException as e:
        print(f"  ⚠️ Gemini: сеть: {e}", flush=True)

    def ok(n):
        return "flash" in n and not re.search(r"image|tts|audio|live|thinking|exp|embedding", n)

    def ver(n):
        m = re.search(r"gemini-(\d+(?:\.\d+)?)", n)
        return float(m.group(1)) if m else 0

    order = [os.getenv("GEMINI_MODEL")] + [m for m in PREFERRED if not names or m in names]
    # остальные доступные flash-модели: новые версии раньше, «lite» и «preview» — в конце
    order += sorted((n for n in names if ok(n)), key=lambda n: ("lite" in n, "preview" in n, -ver(n)))
    seen, result = set(), []
    for m in order:
        if m and m not in seen:
            seen.add(m)
            result.append(m)
    _models_cache = result[:6]
    print(f"  Модели Gemini: {', '.join(_models_cache)}", flush=True)
    return _models_cache


def _retry_delay(text):
    m = re.search(r'"retryDelay":\s*"(\d+)', text)
    return int(m.group(1)) if m else 20


def ask_json(system: str, prompt: str, temperature: float = 0.6) -> dict:
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"},
    }
    errors = []
    for model in available_models():
        for attempt in range(3):
            try:
                r = requests.post(f"{API}/models/{model}:generateContent", headers=_headers(), json=body, timeout=120)
            except requests.RequestException as e:
                errors.append(f"{model}: сеть: {e}")
                time.sleep(5)
                continue
            if r.ok:
                data = r.json()
                try:
                    parts = data["candidates"][0]["content"]["parts"]
                    text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
                    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
                    return json.loads(text)
                except (KeyError, IndexError, json.JSONDecodeError) as e:
                    errors.append(f"{model}: странный ответ ({e}): {str(data)[:200]}")
                    continue
            msg = f"{model}: {r.status_code} {r.text[:400]}"
            print("  ⚠️ " + msg.replace("\n", " "), flush=True)
            errors.append(msg)
            if r.status_code in (400, 401, 403):  # проблема с ключом — другие модели не помогут
                raise RuntimeError("Gemini отклонил ключ. Проверьте секрет GEMINI_API_KEY.\n" + msg)
            if r.status_code == 429:
                delay = _retry_delay(r.text)
                if "limit: 0" in r.text or delay > 60 or attempt == 2:
                    break  # у этой модели нет бесплатной квоты — следующая
                time.sleep(delay + 2)
                continue
            if r.status_code >= 500:
                time.sleep(10 * (attempt + 1))
                continue
            break  # 404 и прочее — следующая модель
    raise RuntimeError("Gemini не ответил ни одной моделью:\n" + "\n".join(errors[-6:]))
