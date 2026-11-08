"""Тексты постов пишет Google Gemini (бесплатный тариф)."""
import json
import os
import re
import time

import requests

API = "https://generativelanguage.googleapis.com/v1beta"
ALIASES = ["gemini-flash-latest", "gemini-flash-lite-latest"]  # перегружены чаще — в конце
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

    # самые новые flash-модели первыми; «lite» и «preview» — после; псевдонимы-«latest» — в самом конце
    order = [os.getenv("GEMINI_MODEL")]
    order += sorted((n for n in names if ok(n) and n not in ALIASES), key=lambda n: ("preview" in n, "lite" in n, -ver(n)))
    order += ALIASES
    seen, result = set(), []
    for m in order:
        if m and m not in seen:
            seen.add(m)
            result.append(m)
    _models_cache = result[:8]
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
    models = available_models()
    errors = []
    for round_ in range(2):  # два прохода по всем моделям; между ними пауза
        if round_:
            print("  Все модели заняты, жду 30 с и пробую ещё раз…", flush=True)
            time.sleep(30)
        for model in list(models):
            try:
                r = requests.post(f"{API}/models/{model}:generateContent", headers=_headers(), json=body, timeout=90)
            except requests.RequestException as e:
                errors.append(f"{model}: сеть: {e}")
                continue
            if r.ok:
                data = r.json()
                try:
                    parts = data["candidates"][0]["content"]["parts"]
                    text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
                    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
                    result = json.loads(text)
                except (KeyError, IndexError, json.JSONDecodeError) as e:
                    errors.append(f"{model}: странный ответ ({e}): {str(data)[:200]}")
                    continue
                models.remove(model)
                models.insert(0, model)  # следующий запрос — сразу в рабочую модель
                print(f"  ✓ ответила {model}", flush=True)
                return result
            short = re.sub(r"\s+", " ", r.text)[:200]
            print(f"  ⚠️ {model}: {r.status_code} {short}", flush=True)
            errors.append(f"{model}: {r.status_code} {short}")
            if r.status_code in (400, 401, 403) and "API key" in r.text:
                raise RuntimeError("Gemini отклонил ключ. Проверьте секрет GEMINI_API_KEY.\n" + short)
            if r.status_code == 404:
                models.remove(model)  # модель недоступна — больше не пробуем
    raise RuntimeError("Gemini не ответил ни одной моделью:\n" + "\n".join(errors[-6:]))
