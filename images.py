"""Картинки: TMDB (постеры, кадры, фото актёров, трейлеры), картинка из статьи, фирменный баннер."""
import io
import os
import random
import re
import textwrap
import unicodedata
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
TMDB = "https://api.themoviedb.org/3"
TMDB_IMG = "https://image.tmdb.org/t/p/"
FONTS = Path(__file__).parent / "fonts"

# ---------- TMDB ----------

def _tmdb(path, **params):
    key = os.getenv("TMDB_API_KEY", "").strip()
    if not key:
        print("  ⚠️ TMDB: секрет TMDB_API_KEY пустой или не задан", flush=True)
        return None
    headers = {"accept": "application/json"}
    if key.startswith("eyJ"):  # «API Read Access Token» (длинный)
        headers["Authorization"] = f"Bearer {key}"
    else:  # «API Key» (короткий)
        params["api_key"] = key
    try:
        r = requests.get(TMDB + path, params=params, headers=headers, timeout=20)
    except requests.RequestException as e:
        print(f"  ⚠️ TMDB: сеть: {e}", flush=True)
        return None
    if not r.ok:
        print(f"  ⚠️ TMDB {path}: {r.status_code} {r.text[:200]}", flush=True)
        return None
    return r.json()


def tmdb_lookup(kind: str, query: str, year=None, exclude=()):
    """Возвращает (url_картинки, tmdb_id, media) или (None, None, None).

    exclude — множество URL картинок, которые уже использовались в постах:
    они пропускаются, а для того же фильма/сериала ищется другой кадр.
    """
    if not query or kind not in ("movie", "tv", "person"):
        return None, None, None
    res = _lookup(kind, query, year, set(exclude))
    print(f"  TMDB [{kind}] «{query}» {year or ''} → {'найдено' if res[0] else 'нет картинки'}", flush=True)
    return res


def _norm_title(s):
    """Название в нижнем регистре без пунктуации — для сравнения с запросом."""
    s = re.sub(r"[«»\"'„“”\-–—:;,.!?()]", " ", (s or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def _latin_only(s):
    """True, если в строке только латинские буквы (и прочие небуквенные символы)."""
    for ch in s:
        if ch.isalpha() and not unicodedata.name(ch, "").startswith("LATIN"):
            return False
    return True


def _match_score(query, m):
    """Насколько результат поиска подходит под запрос: 0 — другой объект.

    Берём не «первый результат с картинкой», а проверяем название, иначе
    «Blade» превращается в «Blade Runner 2049», «God of War» — в китайскую
    дораму, а несуществующий в базе фильм — в мультфильм про зайца.
    """
    q = _norm_title(query)
    if not q:
        return 0
    orig = _norm_title(m.get("original_title") or m.get("original_name"))
    loc = _norm_title(m.get("title") или m.get("name"))
    score = 0
    if orig and orig == q:
        score = 4
    elif loc and loc == q:
        score = 3
    else:
        # фраза целиком, по границам слов: «avatar» в «avatar fire and ash» — ок,
        # но «blade» в «blade runner 2049» — только если точного совпадения нет
        for t, sc in ((orig, 2), (loc, 1)):
            if not t:
                continue
            if len(t) >= len(q) and f" {q} " in f" {t} ":
                score = max(score, sc)
            elif len(t) < len(q) and f" {t} " in f" {q} ":
                score = max(score, sc)
    if not score:
        return 0
    # латинский запрос не должен приводить на объект с нелатинским оригинальным
    # названием («God of War» → дорама 战神龙婿): лучше баннер, чем чужая картинка
    if _latin_only(q) and orig and not _latin_only(orig):
        return 0
    return score


def _lookup(kind, query, year, exclude):
    if kind == "person":
        data = _tmdb("/search/person", query=query, language="ru-RU")
        for p in (data or {}).get("results", [])[:5]:
            if p.get("profile_path"):
                url = TMDB_IMG + "h632" + p["profile_path"]
                if url not in exclude:
                    return url, p["id"], "person"
        return None, None, None
    params = {"query": query, "language": "ru-RU"}
    if year:
        params["year" if kind == "movie" else "first_air_date_year"] = year

    def search():
        return (_tmdb(f"/search/{kind}", **params) or {}).get("results", [])

    def pick(results):
        # сначала объекты, чьё название реально совпадает с запросом;
        # среди них — более популярные (обычно и есть нужный фильм)
        scored = sorted(
            (m for m in results if _match_score(query, m) > 0),
            key=lambda m: (-_match_score(query, m), -(m.get("popularity") or 0)),
        )
        for m in scored[:5]:
            url = None
            if m.get("backdrop_path"):
                url = TMDB_IMG + "w1280" + m["backdrop_path"]
            elif m.get("poster_path"):
                url = TMDB_IMG + "w780" + m["poster_path"]
            if not url:
                continue
            if url in exclude:
                alt = _alt_image(kind, m["id"], exclude)
                if alt:
                    return alt, m["id"], kind
                continue
            return url, m["id"], kind
        return None, None, None

    res = pick(search())
    if not res[0] and year:  # год мог быть указан неточно
        params.pop("year", None), params.pop("first_air_date_year", None)
        res = pick(search())
    return res


def _alt_image(kind, mid, exclude):
    """Другой кадр/постер того же фильма или сериала, если основной уже был в постах."""
    data = _tmdb(f"/{kind}/{mid}/images") or {}
    for group, size in (("backdrops", "w1280"), ("posters", "w780")):
        imgs = sorted(data.get(group, []), key=lambda b: -b.get("vote_average", 0))
        for b in imgs:
            url = TMDB_IMG + size + b["file_path"]
            if url not in exclude:
                return url
    return None


def tmdb_trailer(tmdb_id, media):
    if not tmdb_id or media not in ("movie", "tv"):
        return None
    for lang in ("ru-RU", "en-US"):
        vids = (_tmdb(f"/{media}/{tmdb_id}/videos", language=lang) or {}).get("results", [])
        vids = [v for v in vids if v.get("site") == "YouTube" and v.get("type") in ("Trailer", "Teaser")]
        vids.sort(key=lambda v: (v.get("type") == "Trailer", v.get("official", False), v.get("published_at", "")), reverse=True)
        if vids:
            return f"https://www.youtube.com/watch?v={vids[0]['key']}"
    return None

# Издания, у которых главная картинка статьи — чистое фото без логотипа и надписей.
# У многих русских сайтов (Film.ru, KinoNews и др.) на картинке их логотип и заголовок — такие не берём.
CLEAN_OG = ("variety.com", "deadline.com", "hollywoodreporter.com", "indiewire.com", "collider.com", "slashfilm.com")


def clean_og(url):
    from urllib.parse import urlparse
    host = urlparse(url or "").netloc.lower()
    return any(host == d or host.endswith("." + d) for d in CLEAN_OG)

# ---------- скачивание ----------

def download_image(url: str):
    """Скачивает картинку и приводит к JPEG. None, если картинка битая/слишком маленькая."""
    if not url:
        return None
    try:
        r = requests.get(url, headers=UA, timeout=25)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content))
        img.load()
    except Exception:
        return None
    if img.width < 400 or img.height < 250:
        return None
    img = img.convert("RGB")
    img.thumbnail((1600, 1600))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()

# ---------- фирменный баннер ----------
# Стиль канала: тёмный экран после титров, янтарный луч проектора,
# бегущие строки титров на фоне, крупный заголовок шрифтом Unbounded.

BG = (11, 10, 15)
AMBER = (255, 176, 32)
WHITE = (246, 243, 236)
MUTED = (150, 146, 140)
W, H = 1280, 720


def _font(name, size, weight):
    path = FONTS / f"{name}.ttf"
    try:
        f = ImageFont.truetype(str(path), size)
        f.set_variation_by_name(weight)
        return f
    except Exception:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size)


def _wrap(draw, text, font, max_w, max_lines):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = f"{cur} {w}".strip()
        if draw.textlength(test, font=font) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".,:;!?") + "…"
    return lines


def make_banner(title: str, tag: str = "НОВОСТИ", seed=None) -> bytes:
    rnd = random.Random(seed or title)
    img = Image.new("RGB", (W, H), BG)

    # 1) «титры» на фоне: колонки коротких строк, как в финальных титрах
    credits = Image.new("L", (W, H), 0)
    cd = ImageDraw.Draw(credits)
    for col_x in (760, 1010):
        y = rnd.randint(-40, 0)
        while y < H:
            if rnd.random() < 0.18:
                y += 34
                continue
            lw, rw = rnd.randint(50, 150), rnd.randint(60, 170)
            cd.rounded_rectangle((col_x - 12 - lw, y, col_x - 12, y + 7), 3, fill=rnd.randint(40, 90))
            cd.rounded_rectangle((col_x + 12, y, col_x + 12 + rw, y + 7), 3, fill=rnd.randint(60, 120))
            y += 22
    credits = credits.filter(ImageFilter.GaussianBlur(1.2))
    # гасим титры к краям и к левой части, чтобы не мешали тексту
    fade = Image.linear_gradient("L").rotate(90).resize((W, H))  # слева светло → справа темно
    fade = Image.eval(fade, lambda v: 255 - v)
    credits = Image.composite(credits, Image.new("L", (W, H), 0), fade)
    img.paste((70, 66, 62), mask=credits)

    # 2) луч проектора из правого верхнего угла
    beam = Image.new("L", (W, H), 0)
    ImageDraw.Draw(beam).polygon([(W + 80, -60), (W - 40, -60), (330, H + 40), (760, H + 40)], fill=70)
    beam = beam.filter(ImageFilter.GaussianBlur(90))
    img.paste(AMBER, mask=beam)

    # 3) виньетка и плёночное зерно
    vign = Image.new("L", (W, H), 0)
    ImageDraw.Draw(vign).ellipse((-W * 0.25, -H * 0.4, W * 1.25, H * 1.4), fill=255)
    vign = vign.filter(ImageFilter.GaussianBlur(160))
    img = Image.composite(img, Image.new("RGB", (W, H), (0, 0, 0)), vign)
    noise = Image.effect_noise((W, H), 28).convert("L")
    img = Image.blend(img, Image.merge("RGB", (noise, noise, noise)), 0.05)

    d = ImageDraw.Draw(img)
    pad = 80

    # 4) логотип-строка канала
    logo = _font("Unbounded", 22, "Bold")
    d.ellipse((pad, 66, pad + 14, 80), fill=AMBER)
    d.text((pad + 28, 60), "СЦЕНА ПОСЛЕ ТИТРОВ", font=logo, fill=WHITE)

    # 5) плашка-рубрика
    tag_font = _font("Unbounded", 20, "Bold")
    tag = (tag or "НОВОСТИ").upper()[:22]
    tw = d.textlength(tag, font=tag_font)
    d.rounded_rectangle((pad, 150, pad + tw + 36, 194), 22, fill=AMBER)
    d.text((pad + 18, 159), tag, font=tag_font, fill=BG)

    # 6) заголовок: подбираем размер, чтобы влез в 4 строки
    max_w = W - pad * 2 - 140
    for size in (64, 58, 52, 46, 40):
        hf = _font("Unbounded", size, "Bold")
        lines = _wrap(d, title, hf, max_w, 4)
        if len(lines) <= 4 and all(d.textlength(l, font=hf) <= max_w for l in lines):
            break
    y = 236
    for line in lines:
        d.text((pad, y), line, font=hf, fill=WHITE)
        y += int(size * 1.22)

    # 7) нижняя строка
    foot = _font("Manrope", 22, "SemiBold")
    d.line((pad, H - 86, pad + 64, H - 86), fill=AMBER, width=4)
    d.text((pad, H - 70), "Самое интересное — после титров", font=foot, fill=MUTED)

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()