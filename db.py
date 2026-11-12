"""Навчальна база даних інтернет-магазину + безпечне виконання SQL-запитів."""
import os
import random
import sqlite3
import time

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shop.db")
MAX_ROWS = 200          # скільки рядків максимально повертаємо користувачу
QUERY_TIMEOUT_SEC = 3   # захист від "вічних" запитів

SCHEMA_SQL = """
CREATE TABLE customers (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    city        TEXT NOT NULL,
    registered  DATE NOT NULL
);
CREATE TABLE products (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,
    category  TEXT NOT NULL,
    price     REAL NOT NULL
);
CREATE TABLE orders (
    id           INTEGER PRIMARY KEY,
    customer_id  INTEGER NOT NULL REFERENCES customers(id),
    order_date   DATE NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('new', 'paid', 'shipped', 'cancelled'))
);
CREATE TABLE order_items (
    order_id    INTEGER NOT NULL REFERENCES orders(id),
    product_id  INTEGER NOT NULL REFERENCES products(id),
    quantity    INTEGER NOT NULL,
    PRIMARY KEY (order_id, product_id)
);
"""

CUSTOMERS = ["Олена Коваль", "Андрій Мельник", "Ірина Шевчук", "Максим Бойко", "Софія Ткаченко",
             "Дмитро Кравченко", "Юлія Олійник", "Тарас Лисенко", "Марія Савченко", "Богдан Руденко",
             "Анна Петренко", "Віктор Мороз", "Катерина Павленко", "Олег Марченко", "Наталія Гончар",
             "Сергій Поліщук", "Оксана Литвин", "Роман Захарченко", "Дарина Бондар", "Іван Клименко"]
CITIES = ["Київ", "Львів", "Одеса", "Харків", "Дніпро", "Стрий"]
PRODUCTS = [("Ноутбук Lenovo", "Електроніка", 32000), ("Смартфон Samsung", "Електроніка", 18500),
            ("Навушники JBL", "Електроніка", 2400), ("Монітор Dell", "Електроніка", 9800),
            ("Миша Logitech", "Аксесуари", 850), ("Клавіатура HyperX", "Аксесуари", 2900),
            ("Рюкзак для ноутбука", "Аксесуари", 1600), ("Кавоварка Philips", "Побутова техніка", 7400),
            ("Чайник Tefal", "Побутова техніка", 1300), ("Пилосос Xiaomi", "Побутова техніка", 6900),
            ("Книга «Вивчаємо SQL»", "Книги", 650), ("Книга «Python для всіх»", "Книги", 720),
            ("Настільна лампа", "Дім", 980), ("Плед", "Дім", 1100), ("Чашка-термос", "Дім", 450)]
STATUSES = ["new", "paid", "paid", "shipped", "shipped", "shipped", "cancelled"]

SCHEMA_DESCRIPTION = """Таблиці бази даних інтернет-магазину (SQLite):
customers(id, name, city, registered)            -- клієнти; registered - дата реєстрації 'YYYY-MM-DD'
products(id, name, category, price)              -- товари; price у гривнях
orders(id, customer_id -> customers.id, order_date, status)   -- замовлення; status: new|paid|shipped|cancelled
order_items(order_id -> orders.id, product_id -> products.id, quantity)  -- позиції замовлення
Категорії товарів: Електроніка, Аксесуари, Побутова техніка, Книги, Дім.
Міста: Київ, Львів, Одеса, Харків, Дніпро, Стрий. Дати замовлень: 2025 рік."""


def create_database(path=DB_PATH):
    """Створює файл БД з детермінованими (однаковими щоразу) даними."""
    if os.path.exists(path):
        os.remove(path)
    rnd = random.Random(2025)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA_SQL)
    for i, name in enumerate(CUSTOMERS, 1):
        con.execute("INSERT INTO customers VALUES (?,?,?,?)",
                    (i, name, rnd.choice(CITIES), f"2024-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}"))
    for i, (name, cat, price) in enumerate(PRODUCTS, 1):
        con.execute("INSERT INTO products VALUES (?,?,?,?)", (i, name, cat, price))
    for oid in range(1, 81):
        # кілька клієнтів (17-20) спеціально без замовлень - для задач на LEFT JOIN
        con.execute("INSERT INTO orders VALUES (?,?,?,?)",
                    (oid, rnd.randint(1, 16), f"2025-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}",
                     rnd.choice(STATUSES)))
        for pid in rnd.sample(range(1, len(PRODUCTS) + 1), rnd.randint(1, 4)):
            con.execute("INSERT INTO order_items VALUES (?,?,?)", (oid, pid, rnd.randint(1, 3)))
    con.commit()
    con.close()


def schema_for_ui():
    """Список таблиць і колонок для бокової панелі."""
    con = sqlite3.connect(DB_PATH)
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY rowid")]
    result = []
    for t in tables:
        cols = [(c[1], c[2]) for c in con.execute(f"PRAGMA table_info({t})")]
        count = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        result.append({"table": t, "columns": cols, "rows": count})
    con.close()
    return result


def _authorizer(action, *_):
    """Дозволяємо лише читання: жодних INSERT/UPDATE/DELETE/DROP/ATTACH."""
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
               sqlite3.SQLITE_RECURSIVE}
    return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY


def run_query(sql):
    """Виконує запит у режимі лише для читання.
    Повертає dict: {ok, columns, rows, truncated} або {ok: False, error}."""
    sql = (sql or "").strip().rstrip(";")
    if not sql:
        return {"ok": False, "error": "Порожній запит"}
    if ";" in sql:
        return {"ok": False, "error": "Можна виконувати лише один запит за раз"}
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.set_authorizer(_authorizer)
    deadline = time.time() + QUERY_TIMEOUT_SEC
    con.set_progress_handler(lambda: 1 if time.time() > deadline else 0, 10000)
    try:
        cur = con.execute(sql)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(MAX_ROWS + 1)
        truncated = len(rows) > MAX_ROWS
        return {"ok": True, "columns": columns, "rows": [list(r) for r in rows[:MAX_ROWS]],
                "truncated": truncated}
    except sqlite3.DatabaseError as e:
        msg = str(e)
        if "interrupted" in msg:
            msg = f"Запит виконувався довше {QUERY_TIMEOUT_SEC} с і був зупинений"
        elif "not authorized" in msg:
            msg = "Дозволені лише SELECT-запити (тренажер працює в режимі читання)"
        return {"ok": False, "error": msg}
    finally:
        con.close()


def _normalize(rows):
    norm = []
    for r in rows:
        norm.append(tuple(round(v, 2) if isinstance(v, float) else v for v in r))
    return norm


def compare_results(user, expected, order_matters=False):
    """Порівнює результат користувача з еталонним. Назви колонок не важливі,
    важливі значення (і порядок рядків, якщо в завданні є сортування)."""
    if not user["ok"]:
        return False, "Запит містить помилку"
    u, e = _normalize(user["rows"]), _normalize(expected["rows"])
    if len(user["columns"]) != len(expected["columns"]):
        return False, (f"Кількість колонок не збігається: у вас {len(user['columns'])}, "
                       f"очікується {len(expected['columns'])}")
    if len(u) != len(e):
        return False, f"Кількість рядків не збігається: у вас {len(u)}, очікується {len(e)}"
    if order_matters:
        return (u == e), ("" if u == e else "Дані правильні не повністю або порядок рядків інший")
    key = lambda r: tuple((v is None, str(type(v)), str(v)) for v in r)
    same = sorted(u, key=key) == sorted(e, key=key)
    return same, ("" if same else "Значення в результаті відрізняються від очікуваних")


if __name__ == "__main__":
    create_database()
    print("Створено", DB_PATH)
