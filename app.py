"""SQL-тренажер на основі Gemini API (PoC).
Запуск:  python app.py   ->  http://127.0.0.1:5000
"""
import os
import secrets

from flask import Flask, jsonify, render_template, request, session

import db
from gemini import (GeminiClient, GeminiError, offline_task, prompt_explain, prompt_feedback,
                    prompt_hint, prompt_new_task)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", secrets.token_hex(16))
gemini = GeminiClient()

if not os.path.exists(db.DB_PATH):
    db.create_database()

# Еталонні розв'язки тримаємо на сервері, а не в браузері, щоб їх не можна було підглянути.
TASKS = {}


def preview(result, n=5):
    if not result.get("ok"):
        return result.get("error", "")
    return {"columns": result["columns"], "rows": result["rows"][:n]}


def current_task():
    task = TASKS.get(session.get("task_id"))
    if not task:
        raise LookupError("Спочатку отримайте завдання")
    return task


@app.errorhandler(GeminiError)
def gemini_error(e):
    return jsonify({"error": f"Помилка Gemini API: {e}"}), 502


@app.errorhandler(LookupError)
def no_task(e):
    return jsonify({"error": str(e)}), 400


@app.get("/")
def index():
    return render_template("index.html", offline=gemini.offline)


@app.get("/api/schema")
def schema():
    return jsonify(db.schema_for_ui())


@app.post("/api/task")
def new_task():
    data = request.get_json(force=True)
    level = data.get("level", "easy")
    if level not in ("easy", "medium", "hard"):
        level = "easy"
    topic = (data.get("topic") or "").strip()[:100]
    history = session.get("history", [])

    task = None
    for _ in range(3):  # модель інколи помиляється - перевіряємо еталон на реальній БД
        candidate = (offline_task(level, history) if gemini.offline else
                     gemini.generate(prompt_new_task(db.SCHEMA_DESCRIPTION, level, topic, history),
                                     as_json=True))
        check = db.run_query(candidate.get("solution_sql", ""))
        if check["ok"] and check["rows"]:
            task = candidate
            break
    if not task:
        return jsonify({"error": "Не вдалося згенерувати коректне завдання, спробуйте ще раз"}), 502

    task_id = secrets.token_hex(8)
    task["level"] = level
    task["hints"] = 0
    TASKS[task_id] = task
    session["task_id"] = task_id
    session["history"] = (history + [task["title"]])[-20:]
    return jsonify({"title": task["title"], "task": task["task"], "topic": task.get("topic", ""),
                    "level": level})


@app.post("/api/run")
def run():
    """Просто виконати запит і показати результат (без перевірки)."""
    return jsonify(db.run_query(request.get_json(force=True).get("sql", "")))


@app.post("/api/check")
def check():
    task = current_task()
    sql = request.get_json(force=True).get("sql", "")
    user = db.run_query(sql)
    expected = db.run_query(task["solution_sql"])
    correct, details = db.compare_results(user, expected, task.get("order_matters", False))
    if not user["ok"]:
        details = f"Помилка SQL: {user['error']}"

    if gemini.offline:
        feedback = ("Правильно! Результат збігається з еталонним." if correct else
                    f"Поки що неправильно. {details}. (Офлайн-режим: детальний відгук "
                    f"доступний після додавання GEMINI_API_KEY.)")
    else:
        feedback = gemini.generate(prompt_feedback(
            db.SCHEMA_DESCRIPTION, task["task"], sql,
            "ПРАВИЛЬНО" if correct else "НЕПРАВИЛЬНО", details,
            preview(user), preview(expected)), temperature=0.4)

    if correct:
        session["solved"] = session.get("solved", 0) + 1
    return jsonify({"correct": correct, "details": details, "feedback": feedback,
                    "result": user, "solved": session.get("solved", 0)})


@app.post("/api/hint")
def hint():
    task = current_task()
    task["hints"] = min(task["hints"] + 1, 3)
    if gemini.offline:
        text = f"Підказка (офлайн): зверніть увагу на тему «{task.get('topic', '')}»."
    else:
        text = gemini.generate(prompt_hint(db.SCHEMA_DESCRIPTION, task["task"],
                                           request.get_json(force=True).get("sql", ""),
                                           task["hints"]), temperature=0.5)
    return jsonify({"hint": text, "number": task["hints"]})


@app.post("/api/solution")
def solution():
    task = current_task()
    explanation = ("" if gemini.offline else
                   gemini.generate(prompt_explain(db.SCHEMA_DESCRIPTION, task["task"],
                                                  task["solution_sql"]), temperature=0.3))
    return jsonify({"sql": task["solution_sql"], "explanation": explanation,
                    "result": db.run_query(task["solution_sql"])})


if __name__ == "__main__":
    print("Режим:", "ОФЛАЙН (без GEMINI_API_KEY)" if gemini.offline else f"Gemini ({gemini.model})")
    app.run(debug=True)
