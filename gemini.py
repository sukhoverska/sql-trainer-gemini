"""Мінімальний клієнт Gemini API (REST, без сторонніх бібліотек) + промпти тренажера.

Ключ: змінна середовища GEMINI_API_KEY (створюється безкоштовно на https://aistudio.google.com).
Модель: змінна GEMINI_MODEL (за замовчуванням аліас 'gemini-flash-latest').
Якщо ключа немає - працює офлайн-режим із вбудованими завданнями (для демо без інтернету).
"""
import json
import os
import random
import time
import urllib.error
import urllib.request

API_BASE = "https://generativelanguage.googleapis.com/v1beta"
SYSTEM_PROMPT = (
    "Ти - доброзичливий викладач SQL для студентів. Відповідай українською, коротко і по суті. "
    "Працюєш з базою даних SQLite, схема якої буде в запиті. Пиши лише SQL, сумісний із SQLite."
)


class GeminiError(Exception):
    pass


class GeminiClient:
    def __init__(self, api_key=None, model=None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "").strip()
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-flash-latest")

    @property
    def offline(self):
        return not self.api_key

    def _request(self, method, path, body=None):
        req = urllib.request.Request(
            f"{API_BASE}/{path}", method=method,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")
            try:
                detail = json.loads(detail)["error"]["message"]
            except Exception:
                pass
            raise GeminiError(f"HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise GeminiError(f"Немає з'єднання з Gemini API: {e.reason}") from e

    def _candidate_models(self):
        """Доступні текстові 'flash'-моделі акаунта (для заміни, якщо поточна недоступна)."""
        data = self._request("GET", "models?pageSize=200")
        names = [m["name"].split("/", 1)[1] for m in data.get("models", [])
                 if "generateContent" in m.get("supportedGenerationMethods", [])]
        bad = ("image", "tts", "live", "audio", "embedding", "robotics", "computer")
        flash = [n for n in names if "flash" in n and not any(b in n for b in bad)]
        return flash or names

    def _post_with_retries(self, model, body):
        """3 спроби з паузою, якщо модель перевантажена (503) або ліміт запитів (429)."""
        for attempt in range(3):
            try:
                return self._request("POST", f"models/{model}:generateContent", body)
            except GeminiError as e:
                busy = any(code in str(e) for code in ("HTTP 503", "HTTP 429", "HTTP 500"))
                if not busy or attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))

    def generate(self, prompt, as_json=False, temperature=0.7):
        body = {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature},
        }
        if as_json:
            body["generationConfig"]["responseMimeType"] = "application/json"
        try:
            data = self._post_with_retries(self.model, body)
        except GeminiError as e:
            # модель не знайдена або перевантажена - пробуємо інші доступні flash-моделі
            if not any(code in str(e) for code in ("HTTP 404", "HTTP 503", "HTTP 429", "HTTP 500")):
                raise
            last_error = e
            data = None
            for alt in [m for m in self._candidate_models() if m != self.model][:4]:
                try:
                    data = self._post_with_retries(alt, body)
                    self.model = alt  # запам'ятовуємо модель, що спрацювала
                    print(f"[Gemini] перемкнулися на модель {alt}")
                    break
                except GeminiError as e2:
                    last_error = e2
            if data is None:
                raise last_error
        try:
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        except (KeyError, IndexError):
            raise GeminiError(f"Неочікувана відповідь моделі: {json.dumps(data)[:300]}")
        if as_json:
            text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
            return json.loads(text)
        return text.strip()


# ---------------------------------------------------------------- промпти тренажера

LEVELS = {
    "easy": "легкий: SELECT, WHERE, ORDER BY, LIMIT, одна таблиця",
    "medium": "середній: JOIN двох-трьох таблиць, GROUP BY, агрегатні функції, HAVING",
    "hard": "складний: підзапити, LEFT JOIN з пошуком відсутніх даних, CTE (WITH), "
            "віконні функції, кілька рівнів агрегації",
}


def prompt_new_task(schema, level, topic, previous_titles):
    return f"""{schema}

Склади ОДНЕ нове навчальне завдання з SQL.
Рівень: {LEVELS[level]}.
{f"Тема, на якій зосередитись: {topic}." if topic else ""}
Не повторюй ці завдання: {"; ".join(previous_titles[-10:]) or "-"}.

Вимоги:
- формулювання зрозуміле студенту, без підказки готового SQL;
- результат запиту має бути непорожнім на цих даних;
- якщо важливий порядок рядків - явно вкажи сортування у формулюванні.

Поверни JSON:
{{"title": "коротка назва", "task": "формулювання завдання",
  "topic": "основна тема (напр. GROUP BY)", "solution_sql": "еталонний запит SQLite",
  "order_matters": true/false}}"""


def prompt_feedback(schema, task, user_sql, verdict, details, user_preview, expected_preview):
    return f"""{schema}

Завдання студенту: {task}
Запит студента:
{user_sql}

Автоматична перевірка: {verdict}. {details}
Перші рядки результату студента: {user_preview}
Перші рядки очікуваного результату: {expected_preview}

Дай відгук (до 6 речень):
- якщо правильно - похвали і коротко поясни, чому запит працює; якщо можна написати краще - покажи як;
- якщо неправильно - поясни, у чому саме помилка, і дай навідну підказку,
  але НЕ пиши повний правильний запит."""


def prompt_hint(schema, task, user_sql, hint_number):
    return f"""{schema}

Завдання студенту: {task}
Поточна спроба студента: {user_sql or "(ще нічого не написав)"}

Дай підказку №{hint_number}. Що більший номер, то конкретніша підказка
(1 - яка ідея/конструкція потрібна, 2 - які таблиці і зв'язки, 3 - каркас запиту з пропусками).
Не пиши повний готовий запит. До 3 речень."""


def prompt_explain(schema, task, solution_sql):
    return f"""{schema}

Завдання: {task}
Еталонний розв'язок:
{solution_sql}

Поясни розв'язок крок за кроком (до 6 пунктів), як для студента."""


# ---------------------------------------------------------------- офлайн-режим

OFFLINE_TASKS = {
    "easy": [
        {"title": "Дорогі товари", "topic": "WHERE, ORDER BY", "order_matters": True,
         "task": "Виведіть назви та ціни товарів, дорожчих за 5000 грн, від найдорожчого до найдешевшого.",
         "solution_sql": "SELECT name, price FROM products WHERE price > 5000 ORDER BY price DESC"},
        {"title": "Клієнти зі Львова", "topic": "WHERE", "order_matters": False,
         "task": "Виведіть імена всіх клієнтів зі Львова.",
         "solution_sql": "SELECT name FROM customers WHERE city = 'Львів'"},
    ],
    "medium": [
        {"title": "Замовлення по містах", "topic": "JOIN, GROUP BY", "order_matters": False,
         "task": "Для кожного міста порахуйте кількість замовлень, зроблених клієнтами з цього міста.",
         "solution_sql": "SELECT c.city, COUNT(*) FROM orders o JOIN customers c ON c.id = o.customer_id GROUP BY c.city"},
        {"title": "Виручка за категоріями", "topic": "JOIN, SUM", "order_matters": False,
         "task": "Порахуйте загальну суму проданих товарів (ціна × кількість) для кожної категорії, "
                 "не враховуючи скасовані замовлення.",
         "solution_sql": "SELECT p.category, SUM(p.price * oi.quantity) FROM order_items oi "
                         "JOIN products p ON p.id = oi.product_id JOIN orders o ON o.id = oi.order_id "
                         "WHERE o.status <> 'cancelled' GROUP BY p.category"},
    ],
    "hard": [
        {"title": "Клієнти без замовлень", "topic": "LEFT JOIN", "order_matters": False,
         "task": "Знайдіть імена клієнтів, які не зробили жодного замовлення.",
         "solution_sql": "SELECT c.name FROM customers c LEFT JOIN orders o ON o.customer_id = c.id WHERE o.id IS NULL"},
        {"title": "Товари дорожчі за середню ціну категорії", "topic": "підзапити", "order_matters": False,
         "task": "Виведіть назви товарів, ціна яких вища за середню ціну товарів їхньої категорії.",
         "solution_sql": "SELECT name FROM products p WHERE price > "
                         "(SELECT AVG(price) FROM products WHERE category = p.category)"},
    ],
}


def offline_task(level, previous_titles):
    pool = [t for t in OFFLINE_TASKS[level] if t["title"] not in previous_titles] or OFFLINE_TASKS[level]
    return dict(random.choice(pool))