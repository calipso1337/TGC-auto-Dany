"""Источники новостей. Можно добавлять/удалять ленты — бот переживёт, если какая-то не работает.
Проверить, какие ленты живы:  python main.py --check-feeds
"""
from urllib.parse import quote

# Отраслевые издания: первыми публикуют официальные новости (кастинг, сборы, даты).
# Новость от них считается достоверной даже без второго подтверждения.
TRUSTED = {"Variety", "Deadline", "The Hollywood Reporter"}

FEEDS = {
    # Англоязычные
    "Variety": "https://variety.com/v/film/feed/",
    "Deadline": "https://deadline.com/feed/",
    "The Hollywood Reporter": "https://www.hollywoodreporter.com/c/movies/feed/",
    "IndieWire": "https://www.indiewire.com/feed/",
    "Collider": "https://collider.com/feed/",
    "/Film": "https://www.slashfilm.com/feed/",
    "Screen Rant": "https://screenrant.com/feed/",
    # Русскоязычные
    "KinoNews": "https://www.kinonews.ru/rss/",
    "Мир фантастики": "https://www.mirf.ru/feed/",
    "Film.ru": "https://www.film.ru/rss.xml",
}


def _gnews(q, lang="ru"):
    region = {"ru": "hl=ru&gl=RU&ceid=RU:ru", "en": "hl=en-US&gl=US&ceid=US:en"}[lang]
    return f"https://news.google.com/rss/search?q={quote(q)}+when:1d&{region}"


# Google Новости — агрегатор: помогают находить темы и подтверждать новости вторым источником.
GOOGLE_NEWS = {
    "Google Новости: кино": _gnews("фильм OR кино премьера"),
    "Google Новости: сериалы": _gnews("сериал новый сезон"),
    "Google Новости: сборы": _gnews("кассовые сборы фильм"),
    "Google News: movies": _gnews("movie casting OR trailer OR box office", "en"),
}
