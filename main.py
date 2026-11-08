"""«Сцена после титров» — автопостинг новостей киноиндустрии в Telegram.

Один запуск = один пост:
  1. собрать свежие новости из лент;
  2. выбрать самую интересную тему, которой ещё не было;
  3. проверить достоверность (второй источник / отраслевое издание);
  4. написать пост и сверить каждый факт с исходными статьями;
  5. подобрать картинку и опубликовать.

Запуск:  python main.py            — обычная работа
         python main.py --dry-run  — ничего не отправлять, сохранить пост в папку preview/
         python main.py --check-feeds — проверить, какие ленты работают
"""
import calendar
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests
from bs4 import BeautifulSoup

import images
import llm
import telegram
from sources import FEEDS, GOOGLE_NEWS, TRUSTED

ROOT = Path(__file__).parent
STATE_FILE = ROOT / "state.json"
MSK = timezone(timedelta(hours=3))
CHANNEL_NAME = "Сцена после титров"
CAPTION_LIMIT = 1024

# ---------------------------------------------------------------- состояние

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"posts": []}


def save_state(state):
    cutoff = time.time() - 7 * 86400
    state["posts"] = [p for p in state["posts"] if p["ts"] > cutoff]
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")

# ---------------------------------------------------------------- сбор новостей

def clean(text, limit=None):
    text = BeautifulSoup(text or "", "html.parser").get_text(" ")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] if limit else text


def fetch_feed(name, url):
    try:
        r = requests.get(url, headers=images.UA, timeout=20)
        r.raise_for_status()
    except requests.RequestException as e:
        return [], str(e)[:120]
    feed = feedparser.parse(r.content)
    items = []
    for e in feed.entries:
        ts = e.get("published_parsed") or e.get("updated_parsed")
        if not ts:
            continue
        title = clean(e.get("title"))
        source = name
        if url.startswith("https://news.google.com"):
            src = (e.get("source") or {}).get("title")
            if src:
                source = src
                title = title.removesuffix(f" - {src}")
        items.append({
            "title": title,
            "url": e.get("link"),
            "summary": clean(e.get("summary"), 300),
            "source": source,
            "ts": calendar.timegm(ts),
            "aggregator": url.startswith("https://news.google.com"),
        })
    return items, None


def collect(state, max_age_h):
    seen_urls = {u for p in state["posts"] for u in p.get("urls", [])}
    all_items = []
    for name, url in {**FEEDS, **GOOGLE_NEWS}.items():
        items, err = fetch_feed(name, url)
        if err:
            print(f"  ⚠️ {name}: {err}")
        all_items += items
    now = time.time()
    fresh, titles = [], set()
    for it in sorted(all_items, key=lambda x: -x["ts"]):
        key = re.sub(r"\W+", "", it["title"].lower())[:60]
        if now - it["ts"] > max_age_h * 3600 or it["url"] in seen_urls or key in titles or not it["title"]:
            continue
        titles.add(key)
        fresh.append(it)
    return fresh

# ---------------------------------------------------------------- статья

def fetch_article(url):
    """Текст статьи и её главная картинка (og:image)."""
    if not url or "news.google.com" in url:
        return "", None
    try:
        r = requests.get(url, headers=images.UA, timeout=20)
        r.raise_for_status()
    except requests.RequestException:
        return "", None
    soup = BeautifulSoup(r.text, "html.parser")
    og = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "twitter:image"})
    og_image = og.get("content") if og else None
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
        tag.decompose()
    root = soup.find("article") or soup
    paras = [p.get_text(" ", strip=True) for p in root.find_all("p")]
    text = "\n".join(p for p in paras if len(p) > 60)
    return text[:7000], og_image

# ---------------------------------------------------------------- промпты

EDITOR = f"""Ты — главный редактор Telegram-канала «{CHANNEL_NAME}» о киноиндустрии:
фильмы, сериалы, актёры, режиссёры, кастинги, трейлеры, кассовые сборы, премии, фестивали, стриминги.
Аудитория — русскоязычные любители кино. Канал публикует 15–20 новостей в день, поэтому темы должны быть разнообразными."""

SELECT_RULES = """Выбери ОДНУ новость для следующего поста.
Критерии (по важности):
1. Интересна широкой аудитории: громкие франшизы, известные актёры и режиссёры, трейлеры, сборы, премии, неожиданные повороты.
2. Свежая и конкретная (есть факт: кто, что, когда). Не бери обзоры-мнения, списки «10 лучших», рецензии, спортивные/политические темы.
3. НЕ повторяет темы, уже опубликованные в канале (список ниже) — даже если источник другой.
4. Лучше, если новость подтверждают несколько источников из списка.
5. Чередуй рубрики: не бери ту же рубрику, что у двух последних постов, если есть достойная альтернатива.
6. Предпочитай новости про конкретный фильм/сериал/человека — к ним легко найти фото.

Верни JSON:
{"pick": <номер новости>,
 "same_story": [<номера других новостей из списка ПРО ТО ЖЕ САМОЕ событие, из других источников>],
 "rubric": "<короткая рубрика: Кастинг | Трейлер | Касса | Премьера | Сериалы | Премии | Фестивали | Слухи | Индустрия | Россия>"}
Если подходящих новостей нет совсем — верни {"pick": null}."""

WRITE_RULES = """Напиши пост по материалам ниже.

СТИЛЬ: новостной, но живой — чтобы было интересно читать. Без канцелярита, без воды, без оценочных «шедевр/провал», если это не цитата.
ЗАГОЛОВОК: говорящий и цепляющий — сразу понятно, о чём новость, и хочется прочитать. Главное имя/название — в заголовке. До 80 символов. Без кликбейта, который обманывает. Без точки в конце.
ТЕКСТ: 2–3 коротких абзаца, всего 400–700 символов. Первый абзац — суть (кто, что, когда). Дальше — важные детали и контекст (почему это интересно, что известно о проекте). В начале абзацев можно 1 уместный эмодзи (🎬🎥🍿⭐️🏆💰📅🔥😱🎭📺👀).
ФАКТЫ: используй ТОЛЬКО факты из материалов. Ничего не придумывай: ни дат, ни цифр, ни имён, ни цитат. Если чего-то нет в материалах — не пиши об этом.
Если в материалах сказано «по слухам / инсайдеры сообщают / reportedly» — так и подай.
ИМЕНА И НАЗВАНИЯ: имена — по-русски, как принято в российских СМИ и на Кинопоиске. Фильмы — русское прокатное название, а при первом упоминании зарубежного фильма — оригинальное в скобках: «Дюна» (Dune). Деньги: «$150 млн».
ЭМОДЗИ: эмодзи заголовка НЕ повторяй в начале первого абзаца.
ХЭШТЕГИ: 1–3 штуки, по-русски, без пробелов (#Кастинг, #Marvel, #Сборы).

Верни JSON:
{"emoji": "<1 эмодзи для заголовка>",
 "title": "<заголовок>",
 "body": "<текст, абзацы разделены \\n\\n>",
 "hashtags": ["#..."],
 "is_rumor": <true если это слухи/инсайд/неподтверждённая информация>,
 "official": <true если в материалах есть официальный источник: заявление студии/стриминга/компании, пресс-релиз, слова самих создателей или актёров, официальный трейлер, данные о сборах>,
 "is_trailer_news": <true если главная новость — вышел трейлер/тизер>,
 "image": {"kind": "movie" | "tv" | "person" | "none",
           "query": "<ОРИГИНАЛЬНОЕ (обычно английское) название фильма/сериала или имя человека — главный визуальный герой новости>",
           "year": <год выхода фильма/сериала или null>,
           "fallback_kind": "movie" | "tv" | "person" | "none",
           "fallback_query": "<запасной вариант, если нового проекта ещё нет в базах: оригинальный фильм франшизы, главный актёр или режиссёр>"},
 "banner_tag": "<рубрика 1–2 слова для баннера>"}"""

CHECK_RULES = """Ты — фактчекер. Сверь черновик поста с исходными материалами.
Найди каждое утверждение (даты, цифры, имена, роли, названия, цитаты, события), которого НЕТ в материалах или которое им противоречит.
Если всё подтверждается — верни черновик как есть. Если есть проблемы — исправь или удали эти утверждения,
сохранив стиль, заголовок (если он корректен) и объём.
Верни JSON: {"ok": <true если правок не было>, "problems": ["..."], "title": "...", "body": "..."}"""

# ---------------------------------------------------------------- пост

def visible_len(html_text):
    return len(html.unescape(re.sub(r"<[^>]+>", "", html_text)))


def build_caption(post, src_url, trailer, rumor, limit):
    e = html.escape
    channel = os.getenv("TELEGRAM_CHANNEL", "").lstrip("@")
    signature = f'<a href="https://t.me/{e(channel)}">🎬 {CHANNEL_NAME}</a>' if channel and not channel.lstrip("-").isdigit() else f"🎬 {CHANNEL_NAME}"
    links = [f'🔗 <a href="{e(src_url)}">Источник</a>'] if src_url else []
    if trailer:
        links.append(f'▶️ <a href="{e(trailer)}">Трейлер</a>')
    tags = " ".join(t if t.startswith("#") else "#" + t for t in post.get("hashtags", [])[:3])
    tags = re.sub(r"[^\w#\s]", "", tags)

    body = post["body"].strip()
    while True:
        parts = [f"<b>{e(post.get('emoji', '🎬'))} {e(post['title'].strip().rstrip('.'))}</b>", e(body)]
        if rumor:
            parts.append("🤫 <i>Пока это слухи — ждём официального подтверждения.</i>")
        tail = " · ".join(links)
        if tags:
            tail += ("\n" if tail else "") + tags
        parts += [tail, signature]
        text = "\n\n".join(p for p in parts if p)
        if visible_len(text) <= limit or len(body) <= 120:
            return text
        # слишком длинно — убираем последнее предложение
        body = body.rstrip()
        ends = [m.end() for m in re.finditer(r"[.!?…]+(?=\s)", body)]
        if ends:
            body = body[: ends[-1]]
        else:
            body = body[: max(100, len(body) - (visible_len(text) - limit) - 2)].rstrip() + "…"


def make_post(state):
    recent = sorted(state["posts"], key=lambda p: -p["ts"])
    recent_titles = [p["title"] for p in recent if time.time() - p["ts"] < 48 * 3600][:40]
    recent_rubrics = [p.get("rubric", "") for p in recent[:2]]

    for max_age in (12, 24):
        cands = collect(state, max_age)
        print(f"Свежих новостей за {max_age} ч: {len(cands)}")
        if len(cands) >= 5:
            break
    if not cands:
        print("Нет свежих новостей — пропускаю запуск.")
        return None
    cands = cands[:90]

    listing = "\n".join(
        f"[{i}] ({c['source']}, {int((time.time() - c['ts']) / 3600)} ч назад) {c['title']} — {c['summary'][:160]}"
        for i, c in enumerate(cands))
    sel = llm.ask_json(EDITOR, f"{SELECT_RULES}\n\nУЖЕ ОПУБЛИКОВАНО (не повторять):\n" +
                       ("\n".join("- " + t for t in recent_titles) or "—") +
                       f"\n\nРубрики двух последних постов: {', '.join(recent_rubrics) or '—'}\n\nНОВОСТИ:\n{listing}", 0.4)
    pick = sel.get("pick")
    if pick is None or not (0 <= int(pick) < len(cands)):
        print("Редактор не нашёл подходящей новости.")
        return None
    main_item = cands[int(pick)]
    same = [cands[int(i)] for i in sel.get("same_story", []) if str(i).isdigit() and 0 <= int(i) < len(cands) and int(i) != int(pick)]
    story = [main_item] + same
    sources = {s["source"] for s in story}
    confirmed = len(sources) >= 2 or bool(sources & TRUSTED)
    print(f"Тема: {main_item['title']}  [{main_item['source']}]  источников: {len(sources)}  подтверждено: {confirmed}")

    # материалы: полный текст основной статьи + до двух подтверждающих
    materials, og_image, src_url = [], None, None
    for s in sorted(story, key=lambda s: s["aggregator"])[:3]:
        text, og = fetch_article(s["url"])
        if not src_url and text:
            src_url, og_image = s["url"], og
        materials.append(f"### {s['source']}: {s['title']}\n{s['summary']}\n{text}")
    src_url = src_url or main_item["url"]
    mat = "\n\n".join(materials)[:14000]

    post = llm.ask_json(EDITOR, f"{WRITE_RULES}\n\nМАТЕРИАЛЫ:\n{mat}", 0.7)
    check = llm.ask_json(EDITOR, f"{CHECK_RULES}\n\nЧЕРНОВИК:\nЗаголовок: {post['title']}\n{post['body']}\n\nМАТЕРИАЛЫ:\n{mat}", 0.2)
    if not check.get("ok"):
        print("Фактчек поправил:", check.get("problems"))
    post["title"], post["body"] = check.get("title") or post["title"], check.get("body") or post["body"]

    rumor = bool(post.get("is_rumor")) or (not confirmed and not post.get("official"))

    # убрать повтор эмодзи заголовка в начале текста
    em = (post.get("emoji") or "").strip()
    if em and post["body"].lstrip().startswith(em):
        post["body"] = post["body"].lstrip()[len(em):].lstrip()

    # картинка: TMDB (постер/кадр/фото) → картинка из статьи → фирменный баннер
    im = post.get("image") or {}
    tmdb_url, tmdb_id, media = images.tmdb_lookup(im.get("kind"), im.get("query"), im.get("year"))
    if not tmdb_url and im.get("kind") in ("movie", "tv"):  # новый сериал мог завестись как фильм и наоборот
        other = "tv" if im["kind"] == "movie" else "movie"
        tmdb_url, tmdb_id, media = images.tmdb_lookup(other, im.get("query"), im.get("year"))
    trailer = images.tmdb_trailer(tmdb_id, media) if post.get("is_trailer_news") else None
    fb_url, _, _ = (None, None, None) if tmdb_url else images.tmdb_lookup(im.get("fallback_kind"), im.get("fallback_query"))
    # картинки из статей берём только у изданий без логотипов и надписей на фото
    if og_image and not images.clean_og(src_url):
        og_image = None
    order = [("tmdb", tmdb_url), ("article", og_image), ("tmdb", fb_url)]
    photo, image_src = None, "banner"
    for kind, url in order:
        photo = images.download_image(url)
        if photo:
            image_src = kind
            break
    if not photo:
        photo = images.make_banner(post["title"], post.get("banner_tag") or sel.get("rubric") or "Новости")

    return {
        "post": post, "photo": photo, "image_src": image_src, "trailer": trailer, "rumor": rumor,
        "src_url": src_url, "rubric": sel.get("rubric", ""),
        "urls": [s["url"] for s in story],
    }


def publish(res, dry_run):
    mode = os.getenv("POST_MODE", "moderation").strip().lower()
    chat = os.getenv("ADMIN_CHAT_ID") if mode != "auto" else os.getenv("TELEGRAM_CHANNEL")
    use_preview = bool(res["trailer"])
    caption = build_caption(res["post"], res["src_url"], res["trailer"], res["rumor"], 4096 if use_preview else CAPTION_LIMIT)

    if dry_run:
        out = ROOT / "preview"
        out.mkdir(exist_ok=True)
        stamp = datetime.now(MSK).strftime("%H%M%S")
        (out / f"{stamp}.jpg").write_bytes(res["photo"])
        (out / f"{stamp}.html").write_text(caption, encoding="utf-8")
        print("\n----- ПОСТ -----\n" + caption + f"\n----- картинка: {res['image_src']} → preview/{stamp}.jpg")
        return
    if not chat:
        raise SystemExit("Не задан чат: для POST_MODE=moderation нужен секрет ADMIN_CHAT_ID, для auto — TELEGRAM_CHANNEL")
    if use_preview:
        telegram.send_with_preview(chat, caption, res["trailer"])
    else:
        try:
            telegram.send_photo(chat, res["photo"], caption)
        except RuntimeError as e:
            print("Фото не отправилось, шлю баннер:", e)
            telegram.send_photo(chat, images.make_banner(res["post"]["title"], res["rubric"]), caption)
    print(f"✅ Опубликовано в {chat} (режим {mode}), картинка: {res['image_src']}")


def check_feeds():
    for name, url in {**FEEDS, **GOOGLE_NEWS}.items():
        items, err = fetch_feed(name, url)
        print(f"{'✅' if items else '❌'} {name}: {len(items)} записей {err or ''}")


def main():
    sys.stdout.reconfigure(line_buffering=True)  # лог в реальном времени
    if "--check-feeds" in sys.argv:
        return check_feeds()
    dry = "--dry-run" in sys.argv
    hour = datetime.now(MSK).hour
    if not dry and 1 < hour < 6 and "--force" not in sys.argv:
        print("Ночь по Москве — не публикуем.")
        return
    state = load_state()
    res = make_post(state)
    if not res:
        return
    publish(res, dry)
    if not dry:
        state["posts"].append({"ts": time.time(), "title": res["post"]["title"], "rubric": res["rubric"],
                               "urls": res["urls"], "image": res["image_src"]})
        save_state(state)


if __name__ == "__main__":
    main()
