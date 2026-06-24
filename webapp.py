"""
Telegram Mini App backend (FastAPI).
Запускается как отдельный сервис на Railway: uvicorn webapp:app --host 0.0.0.0 --port $PORT
Использует ту же базу, что и bot.py.
"""
import os
import json
import hmac
import hashlib
import time
from datetime import datetime, timedelta
from urllib.parse import parse_qsl

import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")

app = FastAPI()


# ─── База данных ───────────────────────────────────────────

def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def init_webapp_db():
    """Таблицы для Mini App: недельный план и ежедневные задания."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS weekly_plan (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    plan_json TEXT,
                    week_start DATE,
                    created_at TIMESTAMP DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    day_index INTEGER,
                    title TEXT,
                    done BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    role TEXT,
                    content TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
        conn.commit()
    print("Webapp DB initialized")


# ─── Проверка подписи Telegram initData ────────────────────

def verify_init_data(init_data: str):
    """Проверяет подпись initData от Telegram WebApp. Возвращает dict user или None."""
    if not init_data:
        return None
    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            return None
        # строка для проверки
        data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc_hash = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc_hash, received_hash):
            return None
        user = json.loads(parsed.get("user", "{}"))
        return user
    except Exception as e:
        print(f"verify_init_data error: {e}")
        return None


def get_user_id(request_data: dict):
    """Достаёт и валидирует user_id из initData в теле запроса."""
    init_data = request_data.get("init_data", "")
    user = verify_init_data(init_data)
    if not user or "id" not in user:
        raise HTTPException(status_code=401, detail="Invalid initData")
    return user["id"]


# ─── Доступ (подписка) ─────────────────────────────────────

def subscription_active(user_row):
    if not user_row or not user_row.get("is_subscribed"):
        return False
    expires = user_row.get("sub_expires")
    if not expires:
        return False
    try:
        if isinstance(expires, str):
            expires = datetime.fromisoformat(expires)
        return expires >= datetime.utcnow()
    except Exception:
        return False


def fetch_user(user_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
            return cur.fetchone()


# ─── Эндпоинты данных ──────────────────────────────────────

@app.post("/api/me")
async def api_me(request: Request):
    body = await request.json()
    user_id = get_user_id(body)
    u = fetch_user(user_id)
    return {
        "user_id": user_id,
        "first_name": (u or {}).get("first_name"),
        "is_premium": subscription_active(u),
    }


@app.post("/api/cosmetics")
async def api_cosmetics(request: Request):
    body = await request.json()
    user_id = get_user_id(body)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, product_name, analysis, summary, ingredients, usage, created_at "
                "FROM user_cosmetics WHERE user_id = %s ORDER BY created_at DESC, id DESC",
                (user_id,)
            )
            rows = cur.fetchall()
    items = [{
        "id": r["id"],
        "name": r["product_name"] or "Косметика",
        "summary": r.get("summary") or "",
        "ingredients": r.get("ingredients") or "",
        "usage": r.get("usage") or "",
        "analysis": r["analysis"] or "",
    } for r in rows]
    return {"items": items}


@app.post("/api/cosmetics_all")
async def api_cosmetics_all(request: Request):
    """Общая база косметики (все пользователи). Только для Pro и только новые записи с разбивкой."""
    body = await request.json()
    user_id = get_user_id(body)
    u = fetch_user(user_id)
    if not subscription_active(u):
        raise HTTPException(status_code=403, detail="Premium only")
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, product_name, summary, ingredients, usage FROM products "
                "WHERE summary IS NOT NULL AND summary <> '' "
                "ORDER BY scan_count DESC, created_at DESC LIMIT 200"
            )
            rows = cur.fetchall()
    items = [{
        "id": r["id"],
        "name": r["product_name"] or "Косметика",
        "summary": r.get("summary") or "",
        "ingredients": r.get("ingredients") or "",
        "usage": r.get("usage") or "",
        "analysis": "",
    } for r in rows]
    return {"items": items}


def normalize_name(name):
    import re
    s = (name or "").lower().replace("ё", "е")
    s = re.sub(r"[^\wа-я0-9 ]", " ", s)
    words = [w for w in s.split() if w not in ("от", "by", "из")]
    return " ".join(sorted(words))


@app.post("/api/cosmetic_delete")
async def api_cosmetic_delete(request: Request):
    """Удаление средства из личной базы пользователя."""
    body = await request.json()
    user_id = get_user_id(body)
    cos_id = body.get("id")
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM user_cosmetics WHERE id = %s AND user_id = %s", (cos_id, user_id))
        conn.commit()
    return {"ok": True}


@app.post("/api/reviews")
async def api_reviews(request: Request):
    """Отзывы по средству (общие, по нормализованному названию)."""
    body = await request.json()
    get_user_id(body)
    name = body.get("name") or ""
    norm = normalize_name(name)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT first_name, text, created_at FROM reviews WHERE product_norm = %s "
                "ORDER BY created_at DESC LIMIT 50",
                (norm,)
            )
            rows = cur.fetchall()
    return {"reviews": [{"author": r["first_name"] or "Гость", "text": r["text"]} for r in rows]}


@app.post("/api/review_add")
async def api_review_add(request: Request):
    """Добавить отзыв о средстве."""
    body = await request.json()
    user_id = get_user_id(body)
    u = fetch_user(user_id)
    name = (body.get("name") or "").strip()
    text = (body.get("text") or "").strip()
    if not name or not text:
        return {"ok": False}
    norm = normalize_name(name)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO reviews (product_norm, product_name, user_id, first_name, text) "
                "VALUES (%s, %s, %s, %s, %s)",
                (norm, name, user_id, (u or {}).get("first_name") or "Гость", text)
            )
        conn.commit()
    return {"ok": True}


@app.post("/api/tasks")
async def api_tasks(request: Request):
    body = await request.json()
    user_id = get_user_id(body)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, day_index, title, done FROM tasks WHERE user_id = %s ORDER BY day_index, id",
                (user_id,)
            )
            rows = cur.fetchall()
    tasks = [{"id": r["id"], "day": r["day_index"], "title": r["title"], "done": r["done"]} for r in rows]
    total = len(tasks)
    done = sum(1 for t in tasks if t["done"])
    percent = round(done / total * 100) if total else 0
    return {"tasks": tasks, "total": total, "done": done, "percent": percent}


@app.post("/api/task_toggle")
async def api_task_toggle(request: Request):
    body = await request.json()
    user_id = get_user_id(body)
    task_id = body.get("task_id")
    done = bool(body.get("done"))
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tasks SET done = %s WHERE id = %s AND user_id = %s",
                (done, task_id, user_id)
            )
        conn.commit()
    return {"ok": True}


@app.post("/api/plan")
async def api_plan(request: Request):
    body = await request.json()
    user_id = get_user_id(body)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT plan_json FROM weekly_plan WHERE user_id = %s ORDER BY created_at DESC LIMIT 1",
                (user_id,)
            )
            row = cur.fetchone()
    if not row:
        return {"plan": None}
    try:
        return {"plan": json.loads(row["plan_json"])}
    except Exception:
        return {"plan": None}


@app.post("/api/plan_generate")
async def api_plan_generate(request: Request):
    body = await request.json()
    user_id = get_user_id(body)
    u = fetch_user(user_id)
    if not subscription_active(u):
        raise HTTPException(status_code=403, detail="Premium only")
    blocks = body.get("blocks") or []
    term = body.get("term") or "week"
    wish = (body.get("wish") or "").strip()
    use_cosmetics = bool(body.get("use_cosmetics"))
    plan = generate_week_plan(u, blocks=blocks, term=term, wish=wish, use_cosmetics=use_cosmetics)
    save_plan_and_tasks(user_id, plan)
    return {"plan": plan}


@app.post("/api/chat")
async def api_chat(request: Request):
    body = await request.json()
    user_id = get_user_id(body)
    u = fetch_user(user_id)
    if not subscription_active(u):
        raise HTTPException(status_code=403, detail="Premium only")
    message = (body.get("message") or "").strip()
    if not message:
        return {"reply": ""}
    use_cosmetics = bool(body.get("use_cosmetics"))
    reply = expert_chat(u, message, use_cosmetics=use_cosmetics)
    # сохраняем историю
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO chat_messages (user_id, role, content) VALUES (%s,'user',%s)", (user_id, message))
            cur.execute("INSERT INTO chat_messages (user_id, role, content) VALUES (%s,'assistant',%s)", (user_id, reply))
        conn.commit()
    return {"reply": reply}


@app.post("/api/chat_history")
async def api_chat_history(request: Request):
    body = await request.json()
    user_id = get_user_id(body)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT role, content FROM chat_messages WHERE user_id = %s ORDER BY id LIMIT 50",
                (user_id,)
            )
            rows = cur.fetchall()
    return {"messages": [{"role": r["role"], "content": r["content"]} for r in rows]}


# ─── GPT ───────────────────────────────────────────────────

def _profile_text(u):
    if not u:
        return ""
    parts = []
    for label, key in [("Цель", "goal"), ("Проблема", "problem"), ("Возраст", "age"),
                       ("Пол", "gender"), ("Город", "city"), ("Страна", "country"),
                       ("Частота мытья", "wash_frequency"), ("Время укладки", "styling_time")]:
        if u.get(key):
            parts.append(f"{label}: {u[key]}")
    if u.get("hair_analysis"):
        parts.append(f"Волосы: {u['hair_analysis']}")
    return "\n".join(parts)


BLOCK_LABELS = {
    "care": "уход и мытьё",
    "nutrition": "питание (что есть для волос)",
    "supplements": "БАДы и витамины",
    "masks": "маски и средства",
    "styling": "стайлинг и укладка",
}


def generate_week_plan(u, blocks=None, term="week", wish="", use_cosmetics=False):
    """Возвращает структуру плана. blocks — какие темы включить; term — week/month/day/change."""
    profile = _profile_text(u)
    blocks = blocks or list(BLOCK_LABELS.keys())
    chosen = [BLOCK_LABELS[b] for b in blocks if b in BLOCK_LABELS] or list(BLOCK_LABELS.values())

    cos_block = ""
    if use_cosmetics:
        names = get_user_cosmetic_names(u.get("user_id"))
        if names:
            cos_block = "\nСредства пользователя (используй их в плане): " + ", ".join(names) + "\n"

    wish_block = f"\nПожелание пользователя (учти обязательно): {wish}\n" if wish else ""

    if term == "day":
        structure = ('Верни СТРОГО JSON: {"days":[{"day":"Сегодня","tasks":["...", "..."]}]}. '
                     'Один день, 3–6 коротких конкретных заданий.')
    elif term == "month":
        structure = ('Верни СТРОГО JSON: {"days":[{"day":"Неделя 1","tasks":["..."]}, ... 4 недели ...]}. '
                     'По неделям, 3–5 заданий в неделе.')
    elif term == "change":
        structure = ('Верни СТРОГО JSON: {"days":[{"day":"Что изменить","tasks":["...", "..."]}]}. '
                     'Без привязки к дням — 4–7 ключевых перемен/действий, с которых начать.')
    else:  # week
        structure = ('Верни СТРОГО JSON: {"days":[{"day":"День 1","tasks":["..."]}, ... 7 дней ...]}. '
                     'По 2–4 задания в день.')

    prompt = f"""Ты — эксперт по волосам. Составь персональный план только по выбранным темам: {", ".join(chosen)}.
Профиль:
{profile}{cos_block}{wish_block}
{structure}
Задания короткие, конкретные, выполнимые. Только про волосы и связанное здоровье/питание. Без пояснений вне JSON."""
    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={
                "model": "gpt-4o-mini",
                "max_tokens": 1200,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=60,
        )
        content = resp.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as e:
        print(f"generate_week_plan error: {e}")
        return {"days": []}


def save_plan_and_tasks(user_id, plan):
    """Сохраняет план и раскладывает задания по дням (пересоздаёт задания)."""
    week_start = datetime.utcnow().date()
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO weekly_plan (user_id, plan_json, week_start) VALUES (%s, %s, %s)",
                (user_id, json.dumps(plan, ensure_ascii=False), week_start)
            )
            # очищаем старые задания и создаём новые
            cur.execute("DELETE FROM tasks WHERE user_id = %s", (user_id,))
            for i, day in enumerate(plan.get("days", [])):
                for title in day.get("tasks", []):
                    cur.execute(
                        "INSERT INTO tasks (user_id, day_index, title, done) VALUES (%s, %s, %s, FALSE)",
                        (user_id, i, title)
                    )
        conn.commit()


def get_user_cosmetic_names(user_id):
    """Список названий косметики пользователя (без состава)."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT product_name FROM user_cosmetics "
                    "WHERE user_id = %s AND product_name IS NOT NULL ORDER BY product_name",
                    (user_id,)
                )
                return [r["product_name"] for r in cur.fetchall() if r["product_name"]]
    except Exception as e:
        print(f"get_user_cosmetic_names error: {e}")
        return []


def expert_chat(u, message, use_cosmetics=False):
    profile = _profile_text(u)
    system = (
        "Ты — личный эксперт по волосам. Отвечай честно, по делу, тёплым тоном, "
        "без лести. Только про волосы и связанное здоровье. Профиль пользователя:\n" + profile
    )
    if use_cosmetics:
        names = get_user_cosmetic_names(u.get("user_id"))
        if names:
            system += (
                "\n\nСредства, которые есть у пользователя (только названия — "
                "сам определи их назначение и как применять):\n"
                + "\n".join(f"· {n}" for n in names)
                + "\n\nКогда уместно — советуй на основе ИМЕННО этих средств "
                  "(чем помыть, чем уложить, в каком порядке). Если для задачи "
                  "среди них чего-то не хватает — мягко скажи об этом."
            )
        else:
            system += ("\n\nУ пользователя пока нет сохранённых средств. "
                       "Если он спрашивает про свои средства — предложи отсканировать их "
                       "в режиме «Сканер косметики».")
    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={
                "model": "gpt-4o-mini",
                "max_tokens": 800,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": message},
                ]
            },
            timeout=60,
        )
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"expert_chat error: {e}")
        return "Не удалось ответить сейчас. Попробуйте ещё раз чуть позже."


# ─── Отдача Mini App ───────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    with open(os.path.join(os.path.dirname(__file__), "miniapp.html"), encoding="utf-8") as f:
        return f.read()


@app.get("/health")
async def health():
    return {"ok": True}


def _init_db_bg():
    for _ in range(10):
        try:
            init_webapp_db()
            return
        except Exception as e:
            print(f"Webapp DB init failed: {e}")
            time.sleep(5)


@app.on_event("startup")
def startup():
    import threading
    threading.Thread(target=_init_db_bg, daemon=True).start()
