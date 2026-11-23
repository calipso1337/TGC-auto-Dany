"""Тесты подбора картинок: защита от повторения несоответствий картинок постам.

История ошибок, которые ловят эти тесты:
  - «Blade» попадал на «Blade Runner 2049» (кадр с Джой вместо Блэйда);
  - «God of War» попадал на китайскую одноимённую дораму (战神龙婿);
  - фильма нет в базах («The Heart in Winter») — брался случайный мультфильм.

Сеть и ключ TMDB не нужны: _tmdb подставлен фиктивный.

Запуск:  python test_images.py
Выходной код 0 и строка «ВСЕ ПРОШЛИ» — всё хорошо; иначе — логика подбора сломана.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import images  # noqa: E402

W1280 = images.TMDB_IMG + "w1280/"
W780 = images.TMDB_IMG + "w780/"
H632 = images.TMDB_IMG + "h632/"


def fake_tmdb(path, **params):
    q = params.get("query", "")
    if "Blade" in q:
        # точный объект есть, но популярнее другой — старая логика брала популярнее
        return {"results": [
            {"id": 1, "title": "Бегущий по лезвию 2049", "original_title": "Blade Runner 2049",
             "backdrop_path": "/br2049.jpg", "popularity": 60.0},
            {"id": 2, "title": "Блэйд", "original_title": "Blade",
             "backdrop_path": "/blade1998.jpg", "popularity": 15.0},
        ]}
    if "God of War" in q:
        # китайская дорама с локализованным совпадением раньше побеждала
        return {"results": [
            {"id": 3, "name": "God of War", "original_name": "战神龙婿",
             "backdrop_path": "/chinese.jpg", "popularity": 5.0},
            {"id": 4, "name": "God of War", "original_name": "God of War",
             "backdrop_path": "/gow.jpg", "popularity": 30.0},
        ]}
    if "Heart in Winter" in q:
        # фильма нет в базах: фаззи-выдача про зайцев не должна подходить
        return {"results": [
            {"id": 5, "title": "Зима и зайцы", "original_title": "Winter Bunnies",
             "backdrop_path": "/bunny.jpg", "popularity": 2.0},
        ]}
    if "Dune" in q:
        return {"results": [
            {"id": 6, "title": "Дюна: Часть вторая", "original_title": "Dune: Part Two",
             "backdrop_path": "/dune2.jpg", "popularity": 80.0},
            {"id": 7, "title": "Дюна", "original_title": "Dune",
             "backdrop_path": "/dune1.jpg", "popularity": 50.0},
        ]}
    if q == "Дюна":  # кириллический запрос — латинский оригинал допустим
        return {"results": [
            {"id": 8, "title": "Дюна", "original_title": "Dune",
             "backdrop_path": "/dune1.jpg", "popularity": 50.0},
        ]}
    if "Steven Spielberg" in q:
        return {"results": [
            {"id": 9, "name": "Steven Spielberg", "profile_path": "/spielberg.jpg"},
        ]}
    return {"results": []}


images._tmdb = fake_tmdb
images._alt_image = lambda kind, mid, exclude: W1280 + "alt.jpg" if mid == 2 else None


def main():
    tests = [
        # (kind, query, year, exclude, ожидаемый файл в URL или None, комментарий)
        ("movie", "Blade", None, None, "blade1998.jpg", "точное совпадение важнее популярности"),
        ("tv", "God of War", None, None, "gow.jpg", "не китайская дорама"),
        ("movie", "The Heart in Winter", 2028, None, None, "нет в базах → None, а не зайцы"),
        ("movie", "The Heart in Winter", None, None, None, "без года тоже None"),
        ("movie", "Dune", None, None, "dune1.jpg", "точное совпадение важнее сиквела"),
        ("movie", "Дюна", None, None, "dune1.jpg", "кириллический запрос ок"),
        ("movie", "Blade", None, W1280 + "blade1998.jpg", "alt.jpg", "исключённый кадр → другой кадр того же фильма"),
        ("person", "Steven Spielberg", None, None, "spielberg.jpg", "фото человека как раньше"),
    ]
    ok = True
    for kind, query, year, exclude, expected, note in tests:
        url, mid, media = images._lookup(kind, query, year, {exclude} if exclude else set())
        got = Path(url or "").name or None
        passed = got == expected
        ok &= passed
        mark = "✅" if passed else "❌"
        print(f"{mark} {kind:6} «{query}» (year={year}) → {got or 'None'}   # {note}")
        if not passed:
            print(f"   ожидалось: {expected}")
    print("\nВСЕ ПРОШЛИ" if ok else "\nЕСТЬ ОШИБКИ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
