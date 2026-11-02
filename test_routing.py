#!/usr/bin/env python3
"""
Проверка роутинга: задаёт Jev типовые задачи и сравнивает его выбор с ожидаемым.
Сами модели не вызываются — платится только Jev, это доли копейки.

Запуск (из той же папки, где лежит jev_router.py):
    python3 test_routing.py
    python3 test_routing.py "своя задача для проверки"
"""

import sys
import jev_router as r

# (ожидаемый уровень, задача) — допиши свои типичные запросы
CASES = [
    ("haiku", "покажи, что лежит в папке Загрузки"),
    ("haiku", "открой polza.ai/status и скажи, работает ли GPT-6.1 Sol"),
    ("haiku", "переименуй все скриншоты на рабочем столе по дате"),
    ("haiku", "как будет по-английски «закрывающие документы»?"),
    ("sol", "напиши скрипт на питоне, который скачивает курс доллара и пишет его в Google Sheets"),
    ("sol", "вот ошибка KeyError: 'user_id' в моём телеграм-боте, исправь"),
    ("sol", "разбери этот CSV с заказами и посчитай выручку по месяцам"),
    ("sonnet", "бот иногда отвечает дважды на одно сообщение, но не всегда, логи ничего не показывают — найди причину"),
    ("sonnet", "добавь в Forge-мод с торговцем сохранение прогресса целей между перезапусками сервера, затронь все нужные классы"),
    ("sonnet", "разберись в этом чужом репозитории на 40 файлов и объясни, как устроена авторизация"),
    ("opus", "перепиши кешбэк-бота с Python на Go целиком, спроектируй архитектуру и перенеси все функции"),
    ("opus", "мы два дня не можем найти утечку памяти в сервере, все очевидные варианты уже проверены"),
]


def decide(task):
    tier, confidence, probs = r.ask_jev("Задача пользователя:\n" + task)
    picked = tier
    if confidence < r.UNSURE:
        for name in r.ORDER:
            if probs.get(name, 0) >= r.CANDIDATE:
                picked = name
    return tier, picked, confidence, probs


def show(expected, task):
    tier, picked, confidence, probs = decide(task)
    shares = "  ".join("%s %.2f" % (name, probs.get(name, 0)) for name in r.ORDER)
    if expected is None:
        mark = "  "
    elif picked == expected:
        mark = "ok"
    else:
        mark = "XX"
    escalated = " (Jev: %s, не уверен)" % tier if picked != tier else ""
    print("%s  %-6s%s  уверенность %.2f  |  %s" % (mark, picked, escalated, confidence, shares))
    print("    " + task[:100])
    if expected is not None and picked != expected:
        print("    ожидалось: " + expected)
    return picked == expected


if __name__ == "__main__":
    if not r.KEY:
        raise SystemExit("Задай ключ: export POLZA_API_KEY=sk-polza-...")
    if len(sys.argv) > 1:
        show(None, " ".join(sys.argv[1:]))
    else:
        hits = 0
        for expected, task in CASES:
            if show(expected, task):
                hits += 1
        print("\nСовпало %d из %d" % (hits, len(CASES)))
