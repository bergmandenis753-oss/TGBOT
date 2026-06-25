import os
import base64
import time
import hashlib
from datetime import datetime, timedelta
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ─── Подписка (Telegram Stars) ─────────────────────────────
SUB_PRICE_STARS = 100          # стоимость в Telegram Stars в месяц
SUB_DAYS = 30                  # длительность подписки
SUB_MONTHLY_ACTIONS = 100      # лимит AI-действий в месяц по подписке
SCANNER_FREE = 3               # бесплатных сканов косметики в режиме «Сканер»
WEBAPP_URL = os.getenv("WEBAPP_URL", "")   # URL Mini App (выдаётся Railway после деплоя web-сервиса)

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
VERIFY = False

# ─── Квиз ──────────────────────────────────────────────────

QUIZ_STEPS = [
    {
        "key": "goal",
        "question": "Чего хотите добиться с волосами?\n(например: отрастить, сделать гуще, убрать жирность)",
        "type": "text"
    },
    {
        "key": "problem",
        "question": "Что сейчас беспокоит больше всего?\n(например: выпадение, сухость, перхоть, укладка)",
        "type": "text"
    },
    {
        "key": "wash_frequency",
        "question": "Как часто моете голову?",
        "type": "buttons",
        "options": ["Каждый день", "Через день", "1–2 раза в неделю", "Реже"]
    },
    {
        "key": "age",
        "question": "Сколько вам лет?",
        "type": "text"
    },
    {
        "key": "country",
        "question": "Из какой вы страны и города?\n(нужно, чтобы учесть климат и воду)",
        "type": "text"
    },
]

# ─── База данных ───────────────────────────────────────────

def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    quiz_step INTEGER DEFAULT 0,
                    quiz_done BOOLEAN DEFAULT FALSE,
                    goal TEXT,
                    problem TEXT,
                    age TEXT,
                    gender TEXT,
                    country TEXT,
                    city TEXT,
                    wash_frequency TEXT,
                    styling_time TEXT,
                    hair_analysis TEXT,
                    final_report TEXT,
                    awaiting TEXT DEFAULT 'none',
                    is_subscribed BOOLEAN DEFAULT FALSE,
                    sub_start TIMESTAMP,
                    sub_expires TIMESTAMP,
                    sub_actions_used INTEGER DEFAULT 0,
                    free_plan_used BOOLEAN DEFAULT FALSE,
                    free_hair_used BOOLEAN DEFAULT FALSE,
                    free_product_used BOOLEAN DEFAULT FALSE,
                    mode TEXT,
                    scanner_used INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS products (
                    id SERIAL PRIMARY KEY,
                    image_hash TEXT UNIQUE,
                    product_name TEXT,
                    analysis TEXT,
                    summary TEXT,
                    ingredients TEXT,
                    usage TEXT,
                    scan_count INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT NOW()
                );
                ALTER TABLE products ADD COLUMN IF NOT EXISTS summary TEXT;
                ALTER TABLE products ADD COLUMN IF NOT EXISTS ingredients TEXT;
                ALTER TABLE products ADD COLUMN IF NOT EXISTS usage TEXT;
                CREATE TABLE IF NOT EXISTS user_products (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    product_id INTEGER REFERENCES products(id),
                    uses_product BOOLEAN,
                    created_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(user_id, product_id)
                );
                CREATE TABLE IF NOT EXISTS user_cosmetics (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    product_name TEXT,
                    analysis TEXT,
                    summary TEXT,
                    ingredients TEXT,
                    usage TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS ideas (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    username TEXT,
                    first_name TEXT,
                    text TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS reviews (
                    id SERIAL PRIMARY KEY,
                    product_norm TEXT,
                    product_name TEXT,
                    user_id BIGINT,
                    first_name TEXT,
                    text TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                );
                ALTER TABLE user_cosmetics ADD COLUMN IF NOT EXISTS summary TEXT;
                ALTER TABLE user_cosmetics ADD COLUMN IF NOT EXISTS ingredients TEXT;
                ALTER TABLE user_cosmetics ADD COLUMN IF NOT EXISTS usage TEXT;
                CREATE TABLE IF NOT EXISTS hair_diagnostics (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    rating REAL,
                    hair_type TEXT,
                    density TEXT,
                    ends_state TEXT,
                    scalp TEXT,
                    curl TEXT,
                    shedding TEXT,
                    problems TEXT,
                    full_text TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            # Миграция — добавляем колонки если нет
            for col, coltype in [
                ("username", "TEXT"),
                ("first_name", "TEXT"),
                ("quiz_step", "INTEGER DEFAULT 0"),
                ("quiz_done", "BOOLEAN DEFAULT FALSE"),
                ("goal", "TEXT"),
                ("problem", "TEXT"),
                ("age", "TEXT"),
                ("gender", "TEXT"),
                ("country", "TEXT"),
                ("city", "TEXT"),
                ("wash_frequency", "TEXT"),
                ("styling_time", "TEXT"),
                ("hair_analysis", "TEXT"),
                ("final_report", "TEXT"),
                ("awaiting", "TEXT DEFAULT 'none'"),
                ("is_subscribed", "BOOLEAN DEFAULT FALSE"),
                ("sub_start", "TIMESTAMP"),
                ("sub_expires", "TIMESTAMP"),
                ("sub_actions_used", "INTEGER DEFAULT 0"),
                ("free_plan_used", "BOOLEAN DEFAULT FALSE"),
                ("free_hair_used", "BOOLEAN DEFAULT FALSE"),
                ("free_product_used", "BOOLEAN DEFAULT FALSE"),
                ("mode", "TEXT"),
                ("scanner_used", "INTEGER DEFAULT 0"),
                ("hair_rating", "REAL"),
            ]:
                try:
                    cur.execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} {coltype};")
                except Exception:
                    pass
        conn.commit()
    print("DB initialized")


def get_user(user_id, username=None, first_name=None):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
            if not row:
                cur.execute(
                    "INSERT INTO users (user_id, username, first_name, quiz_step, quiz_done) VALUES (%s, %s, %s, 0, FALSE) RETURNING *",
                    (user_id, username, first_name)
                )
                row = cur.fetchone()
                conn.commit()
            return row


def update_user(user_id, **kwargs):
    if not kwargs:
        return
    fields = ", ".join(f"{k} = %s" for k in kwargs)
    values = list(kwargs.values()) + [user_id]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE users SET {fields} WHERE user_id = %s", values)
            conn.commit()


def get_cached_analysis(image_hash):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, product_name, analysis FROM products WHERE image_hash = %s", (image_hash,))
                row = cur.fetchone()
                if row:
                    cur.execute("UPDATE products SET scan_count = scan_count + 1 WHERE image_hash = %s", (image_hash,))
                    conn.commit()
                return row
    except Exception as e:
        print(f"DB cache error: {e}")
        return None


def save_analysis(image_hash, product_name, analysis, summary="", ingredients="", usage=""):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO products (image_hash, product_name, analysis, summary, ingredients, usage) "
                    "VALUES (%s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (image_hash) DO NOTHING RETURNING id",
                    (image_hash, product_name, analysis, summary, ingredients, usage)
                )
                row = cur.fetchone()
                conn.commit()
                if row:
                    return row["id"]
                cur.execute("SELECT id FROM products WHERE image_hash = %s", (image_hash,))
                return cur.fetchone()["id"]
    except Exception as e:
        print(f"DB save error: {e}")
        return None


def save_user_product(user_id, product_id, uses_product):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO user_products (user_id, product_id, uses_product) VALUES (%s, %s, %s) "
                    "ON CONFLICT (user_id, product_id) DO UPDATE SET uses_product = %s",
                    (user_id, product_id, uses_product, uses_product)
                )
                conn.commit()
    except Exception as e:
        print(f"DB user product error: {e}")


def get_user_products_used(user_id):
    """Список названий средств, которыми пользователь отметил, что пользуется."""
    if not user_id:
        return []
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT p.product_name FROM user_products up "
                    "JOIN products p ON p.id = up.product_id "
                    "WHERE up.user_id = %s AND up.uses_product = TRUE",
                    (user_id,)
                )
                return [r["product_name"] for r in cur.fetchall() if r.get("product_name")]
    except Exception as e:
        print(f"DB products used error: {e}")
        return []


def normalize_name(name):
    """Нормализует название средства для сравнения дублей:
    нижний регистр, убираем «от/by», знаки, сортируем слова."""
    import re
    s = (name or "").lower()
    s = s.replace("ё", "е")
    s = re.sub(r"[^\wа-я0-9 ]", " ", s)
    words = [w for w in s.split() if w not in ("от", "by", "из")]
    return " ".join(sorted(words))


def canonical_product_name(product_name):
    """Сверяет имя с общей базой products: если есть похожее (по норме) — возвращает каноничное имя оттуда."""
    norm = normalize_name(product_name)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT product_name FROM products WHERE product_name IS NOT NULL ORDER BY created_at, id")
                for r in cur.fetchall():
                    if r["product_name"] and normalize_name(r["product_name"]) == norm:
                        return r["product_name"]
    except Exception as e:
        print(f"canonical_product_name error: {e}")
    return product_name


def save_user_cosmetic(user_id, product_name, analysis, summary="", ingredients="", usage=""):
    """Сохраняет скан косметики в личную базу. Имя приводится к каноничному (из общей базы),
    дубли (по нормализованному имени) не создаёт — обновляет."""
    try:
        product_name = canonical_product_name(product_name)
        norm = normalize_name(product_name)
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, product_name FROM user_cosmetics WHERE user_id = %s", (user_id,))
                for r in cur.fetchall():
                    if normalize_name(r["product_name"]) == norm:
                        cur.execute(
                            "UPDATE user_cosmetics SET product_name=%s, analysis=%s, summary=%s, ingredients=%s, usage=%s WHERE id=%s",
                            (product_name, analysis, summary, ingredients, usage, r["id"])
                        )
                        conn.commit()
                        return r["id"]
                cur.execute(
                    "INSERT INTO user_cosmetics (user_id, product_name, analysis, summary, ingredients, usage) "
                    "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                    (user_id, product_name, analysis, summary, ingredients, usage)
                )
                row = cur.fetchone()
                conn.commit()
                return row["id"] if row else None
    except Exception as e:
        print(f"DB save cosmetic error: {e}")
        return None


def get_user_cosmetics(user_id):
    """Список косметики пользователя (по порядку добавления)."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, product_name, analysis, summary, ingredients, usage FROM user_cosmetics "
                    "WHERE user_id = %s ORDER BY created_at, id",
                    (user_id,)
                )
                return cur.fetchall()
    except Exception as e:
        print(f"DB get cosmetics error: {e}")
        return []


def save_idea(user_id, username, first_name, text):
    """Сохраняет идею пользователя."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO ideas (user_id, username, first_name, text) VALUES (%s, %s, %s, %s)",
                    (user_id, username, first_name, text)
                )
                conn.commit()
    except Exception as e:
        print(f"DB save idea error: {e}")


def notify_admin(text):
    """Отправляет сообщение администратору через админ-бота (если настроен)."""
    admin_token = os.getenv("ADMIN_BOT_TOKEN")
    admin_id = os.getenv("ADMIN_USER_ID")
    if not admin_token or not admin_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{admin_token}/sendMessage",
            json={"chat_id": int(admin_id), "text": text},
            verify=VERIFY, timeout=15
        )
    except Exception as e:
        print(f"notify_admin error: {e}")


# ─── Подписка и лимиты ─────────────────────────────────────

# Тип действия -> поле бесплатного флага в БД
FREE_FLAG = {
    "plan": "free_plan_used",
    "hair": "free_hair_used",
    "product": "free_product_used",
}


def subscription_active(user):
    """Активна ли подписка (не истёк срок и есть остаток действий)."""
    if not user.get("is_subscribed"):
        return False
    expires = user.get("sub_expires")
    if not expires:
        return False
    try:
        if isinstance(expires, str):
            expires = datetime.fromisoformat(expires)
        if expires < datetime.utcnow():
            return False
    except Exception:
        return False
    return (user.get("sub_actions_used") or 0) < SUB_MONTHLY_ACTIONS


def sub_actions_left(user):
    if not subscription_active(user):
        return 0
    return SUB_MONTHLY_ACTIONS - (user.get("sub_actions_used") or 0)


def can_use_ai(user, action):
    """Можно ли выполнить AI-действие.
    Возвращает (True/False, способ: 'free' | 'sub' | None)."""
    # Бесплатный «первый раз» для данного типа действия
    flag = FREE_FLAG.get(action)
    if flag and not user.get(flag):
        return True, "free"
    # Иначе — по активной подписке
    if subscription_active(user):
        return True, "sub"
    return False, None


def consume_ai_action(user_id, way, action):
    """Списывает использование: либо бесплатный флаг, либо +1 к счётчику подписки."""
    if way == "free":
        flag = FREE_FLAG.get(action)
        if flag:
            update_user(user_id, **{flag: True})
    elif way == "sub":
        u = get_user(user_id)
        update_user(user_id, sub_actions_used=(u.get("sub_actions_used") or 0) + 1)


def activate_subscription(user_id):
    """Активирует/продлевает подписку на SUB_DAYS дней, обнуляет счётчик."""
    now = datetime.utcnow()
    expires = now + timedelta(days=SUB_DAYS)
    update_user(
        user_id,
        is_subscribed=True,
        sub_start=now.isoformat(),
        sub_expires=expires.isoformat(),
        sub_actions_used=0,
    )


# ─── Telegram ──────────────────────────────────────────────

def tg(method, data=None):
    resp = requests.post(f"{API_URL}/{method}", json=data, verify=VERIFY)
    return resp.json()


def send_message(chat_id, text, reply_markup=None, parse_mode=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if parse_mode:
        payload["parse_mode"] = parse_mode
    tg("sendMessage", payload)


def send_subscription_invoice(chat_id):
    """Счёт на оплату подписки через Telegram Stars (валюта XTR)."""
    tg("sendInvoice", {
        "chat_id": chat_id,
        "title": "Премиум-подписка ✦",
        "description": (
            f"{SUB_MONTHLY_ACTIONS} разборов и анализов фото в месяц. "
            "Персональные планы, анализ волос и косметики без ограничений в рамках лимита."
        ),
        "payload": "premium_monthly",
        "currency": "XTR",
        "prices": [{"label": "Подписка на месяц", "amount": SUB_PRICE_STARS}],
    })


def offer_subscription(chat_id, reason=""):
    """Показывает мягкое предложение оформить подписку + кнопку оплаты."""
    text = (
        (reason + "\n\n" if reason else "")
        + "✦ Премиум-доступ\n\n"
        f"Бесплатный лимит исчерпан. Подписка открывает {SUB_MONTHLY_ACTIONS} "
        "AI-действий в месяц — персональные планы и анализ фото волос и косметики.\n\n"
        f"Стоимость: {SUB_PRICE_STARS} ⭐ Telegram Stars в месяц."
    )
    send_message(chat_id, text, reply_markup={"inline_keyboard": [[
        {"text": f"Оформить за {SUB_PRICE_STARS} ⭐", "callback_data": "buy_sub"}
    ]]})


def send_quiz_question(chat_id, step_index):
    step = QUIZ_STEPS[step_index]
    total = len(QUIZ_STEPS)
    done = step_index + 1
    # Простой короткий заголовок
    text = f"Вопрос {done}/{total}\n\n{step['question']}"

    if step["type"] == "buttons":
        keyboard = [[{"text": opt, "callback_data": f"quiz_{step['key']}_{i}"}]
                    for i, opt in enumerate(step["options"])]
        send_message(chat_id, text, reply_markup={"inline_keyboard": keyboard})
    else:
        send_message(chat_id, text)


def download_file(file_path):
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    resp = requests.get(url, verify=VERIFY)
    return resp.content


# ─── OpenAI ────────────────────────────────────────────────

def build_profile_text(user, include_hair=False, include_products=False):
    """Собирает текстовый профиль пользователя для промтов."""
    profile = ""
    if user.get("goal"):
        profile += f"Цель пользователя: {user['goal']}\n"
    if user.get("problem"):
        profile += f"Проблема с волосами: {user['problem']}\n"
    if user.get("age"):
        profile += f"Возраст: {user['age']}\n"
    if user.get("gender"):
        profile += f"Пол: {user['gender']}\n"
    if user.get("country") or user.get("city"):
        location = ", ".join(filter(None, [user.get("city"), user.get("country")]))
        profile += f"Местоположение: {location}\n"
    if user.get("wash_frequency"):
        profile += f"Частота мытья головы: {user['wash_frequency']}\n"
    if user.get("styling_time"):
        profile += f"Время на укладку: {user['styling_time']}\n"
    if include_hair and user.get("hair_analysis"):
        profile += f"\nХарактеристика волос (по фото):\n{user['hair_analysis']}\n"
    if include_products:
        products = get_user_products_used(user.get("user_id"))
        if products:
            profile += "\nСредства, которыми пользуется человек:\n"
            for p in products:
                profile += f"· {p}\n"
    return profile


def build_analysis_prompt(user=None):
    # Общий разбор средства (без привязки к профилю — кэшируется и переиспользуется всеми).
    return """Ты — эксперт по косметике для волос. Анализируй фото средства честно и по делу.
Это ОБЩИЙ разбор средства, не подстраивай его под конкретного человека.
Ответь СТРОГО в таком формате, ровно с этими разделителями, без лишнего текста, без эмодзи кроме · — :

===NAME===
Официальное название продукта в формате «Бренд Линейка» — строго как на упаковке, без рекламных слов и описаний аромата/объёма. Например: «Aveda Nutri-Plenish Leave-In Conditioner». Одна строка.
===SUMMARY===
Оценка X/10 и 2–3 строки сути: для какого типа волос, главный плюс и минус.
===INGREDIENTS===
5–7 ключевых компонентов, каждый с новой строки: — Компонент: зачем, польза или осторожность
===USAGE===
Как пользоваться: на сухие/влажные волосы, во время мытья или после, как часто, сколько держать, частые ошибки. 4–6 коротких пунктов с —.
===END==="""


def parse_cosmetic_analysis(text):
    """Разбирает ответ GPT на name/summary/ingredients/usage по разделителям."""
    def section(tag_start, tag_end):
        try:
            s = text.index(tag_start) + len(tag_start)
            e = text.index(tag_end, s)
            return text[s:e].strip()
        except ValueError:
            return ""
    name = section("===NAME===", "===SUMMARY===")
    summary = section("===SUMMARY===", "===INGREDIENTS===")
    ingredients = section("===INGREDIENTS===", "===USAGE===")
    usage = section("===USAGE===", "===END===")
    if not usage:  # на случай если модель не закрыла ===END===
        usage = section("===USAGE===", "\x00") or text.split("===USAGE===")[-1].strip()
    return {
        "name": (name or "Косметика")[:120],
        "summary": summary,
        "ingredients": ingredients,
        "usage": usage,
    }


def analyze_image(image_bytes, user=None):
    image_b64 = base64.standard_b64encode(image_bytes).decode()
    prompt = build_analysis_prompt(user or {})
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        json={
            "model": "gpt-4o-mini",
            "max_tokens": 1100,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}", "detail": "low"}},
                {"type": "text", "text": prompt}
            ]}]
        },
        verify=VERIFY
    )
    return resp.json()["choices"][0]["message"]["content"]


def analyze_cosmetic(image_bytes):
    """Анализ средства с общим кэшем по названию (экономия токенов).
    1) дешёвый запрос — только имя; 2) если есть в общей базе — берём готовый разбор;
    3) иначе — полный разбор 1 раз и сохраняем в общую базу."""
    name = detect_product_name(image_bytes)
    cached = find_cached_by_name(name)
    if cached:
        return {
            "name": cached["product_name"] or name,
            "summary": cached.get("summary") or "",
            "ingredients": cached.get("ingredients") or "",
            "usage": cached.get("usage") or "",
            "analysis": cached.get("analysis") or "",
        }
    analysis = analyze_image(image_bytes)
    parsed = parse_cosmetic_analysis(analysis)
    parsed["analysis"] = analysis
    image_hash = hashlib.md5(image_bytes).hexdigest()
    save_analysis(image_hash, parsed["name"], analysis,
                  summary=parsed["summary"], ingredients=parsed["ingredients"], usage=parsed["usage"])
    return parsed


def _store_search_links(query, country):
    """Готовые ссылки-поиски по магазинам региона (без скрапинга, всегда рабочие)."""
    from urllib.parse import quote_plus
    q = quote_plus(query)
    c = (country or "").strip().lower()
    links = []
    if any(x in c for x in ["украин", "ukrain", "україн"]):
        links.append(("Rozetka", f"https://rozetka.com.ua/search/?text={q}"))
        links.append(("Prom.ua", f"https://prom.ua/search?search_term={q}"))
    elif any(x in c for x in ["росси", "russia", "рф"]):
        links.append(("Wildberries", f"https://www.wildberries.ru/catalog/0/search.aspx?search={q}"))
        links.append(("Ozon", f"https://www.ozon.ru/search/?text={q}"))
    elif any(x in c for x in ["казах", "kazakh"]):
        links.append(("Kaspi", f"https://kaspi.kz/shop/search/?text={q}"))
    elif any(x in c for x in ["беларус", "belarus"]):
        links.append(("Wildberries", f"https://www.wildberries.by/catalog/0/search.aspx?search={q}"))
    else:
        links.append(("Amazon", f"https://www.amazon.com/s?k={q}"))
    links.append(("Google Покупки", f"https://www.google.com/search?tbm=shop&q={q}"))
    return links


def find_analogs(cosmetic, user):
    """Подбирает аналоги (дешевле/лучше) с учётом состава и региона пользователя.
    GPT даёт сами аналоги, затем добавляем ссылки-поиски по магазинам региона."""
    name = cosmetic.get("product_name") or "средство"
    ingredients = (cosmetic.get("ingredients") or "")[:400]
    summary = (cosmetic.get("summary") or "")[:300]
    country = user.get("country") or ""
    city = user.get("city") or ""
    region = ", ".join(filter(None, [city, country])) or "не указан"

    prompt = (
        f"Ты — эксперт по косметике для волос. Пользователь отсканировал средство:\n"
        f"Название: {name}\n"
        f"Суть: {summary}\n"
        f"Состав (ключевое): {ingredients}\n"
        f"Регион пользователя: {region}\n\n"
        "Предложи 3 аналога этого средства по действию и составу:\n"
        "— 1 ДЕШЕВЛЕ (бюджетная замена с похожим эффектом)\n"
        "— 1 ПОХОЖЕЕ по цене, но качественнее/удачнее по составу\n"
        "— 1 на свой выбор (лучший по соотношению цена/качество)\n"
        "Выбирай бренды, которые реально продаются в этом регионе. "
        "Для каждого: название (Бренд + средство), 1 строка почему это хорошая замена, "
        "и пометка ценовой категории (бюджет / средний / премиум).\n"
        "Ответь СТРОГО так, без вступлений:\n"
        "1. <Бренд Название> — <почему> (категория)\n"
        "2. ...\n"
        "3. ...\n"
        "В конце 1 короткая строка-совет на что смотреть при выборе."
    )
    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={
                "model": "gpt-4o-mini",
                "max_tokens": 500,
                "messages": [{"role": "user", "content": prompt}]
            },
            verify=VERIFY, timeout=60,
        )
        body = resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"find_analogs gpt error: {e}")
        body = "Не удалось подобрать аналоги сейчас."

    import re
    picks = re.findall(r"^\s*\d+\.\s*([^—\-\n]+)", body, flags=re.MULTILINE)
    links_text = ""
    seen = set()
    for pick in (picks[:3] or [name]):
        q = pick.strip(" .—-")
        if not q or q.lower() in seen:
            continue
        seen.add(q.lower())
        links = _store_search_links(q, country)
        links_text += f"\n🔎 {q}:\n" + "\n".join(f"  · {t}: {url}" for t, url in links) + "\n"

    region_note = f"\n\n📍 Регион: {region}" if region != "не указан" else (
        "\n\n📍 Регион не указан — ссылки общие. Заполните город в профиле (/restart), "
        "чтобы искать в ваших магазинах."
    )
    return f"✦ Аналоги для «{name}»\n\n{body}\n{links_text}{region_note}"


def detect_product_name(image_bytes):
    """Дешёвый запрос: только официальное название средства по фото (мало токенов)."""
    image_b64 = base64.standard_b64encode(image_bytes).decode()
    prompt = (
        "На фото косметическое средство. Ответь ТОЛЬКО его официальным названием "
        "в формате «Бренд Линейка Тип», строго как на упаковке. "
        "ОБЯЗАТЕЛЬНО убери: название аромата/отдушки (например Delightful Honey Bloom, Fresh Aroma), "
        "объём, рекламные слова. Оставь только бренд, линейку и тип средства. "
        "Порядок слов: сначала бренд, потом линейка, потом тип. "
        "Если бренд не виден — опиши тип средства кратко. Одна строка, без кавычек."
    )
    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={
                "model": "gpt-4o-mini",
                "max_tokens": 30,
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}", "detail": "low"}},
                    {"type": "text", "text": prompt}
                ]}]
            },
            verify=VERIFY
        )
        return resp.json()["choices"][0]["message"]["content"].strip().strip('"')[:120]
    except Exception as e:
        print(f"detect_product_name error: {e}")
        return "Косметика"


def _name_words(name):
    """Множество значимых слов имени (без стоп- и описательных слов)."""
    import re
    s = (name or "").lower().replace("ё", "е")
    s = re.sub(r"[^\wа-я0-9 ]", " ", s)
    stop = {"от", "by", "из", "for", "the", "and", "с", "и", "delightful", "honey",
            "bloom", "fresh", "aroma", "scent", "ml", "мл", "объем", "объём"}
    return {w for w in s.split() if w and w not in stop and not w.isdigit()}


def same_product_name(a, b):
    """Одно ли это средство: точное совпадение слов или вхождение короткого имени в длинное."""
    wa, wb = _name_words(a), _name_words(b)
    if not wa or not wb:
        return False
    if wa == wb:
        return True
    inter = wa & wb
    small = wa if len(wa) <= len(wb) else wb
    big = wb if small is wa else wa
    if small.issubset(big):
        return len(inter) >= 2 or len(small) >= 2
    return False


def find_cached_by_name(product_name):
    """Ищет готовый разбор в общей базе products по «умному» совпадению имени.
    Возвращает dict с product_name/analysis/summary/ingredients/usage или None."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT product_name, analysis, summary, ingredients, usage FROM products "
                    "WHERE summary IS NOT NULL AND summary <> '' ORDER BY created_at, id"
                )
                for r in cur.fetchall():
                    if same_product_name(r["product_name"], product_name):
                        return r
    except Exception as e:
        print(f"find_cached_by_name error: {e}")
    return None


def classify_photo(image_bytes):
    """Определяет, что на фото: волосы/селфи или косметическое средство.
    Возвращает 'hair' или 'product'."""
    image_b64 = base64.standard_b64encode(image_bytes).decode()
    prompt = (
        "Посмотри на фото и определи, что на нём изображено. "
        "Ответь СТРОГО одним словом без знаков препинания:\n"
        "· hair — если это волосы, причёска, голова или селфи человека\n"
        "· product — если это косметическое/уходовое средство (флакон, тюбик, банка, упаковка)\n"
        "Если сомневаешься между двумя — выбери то, что занимает большую часть кадра."
    )
    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={
                "model": "gpt-4o-mini",
                "max_tokens": 5,
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}", "detail": "low"}},
                    {"type": "text", "text": prompt}
                ]}]
            },
            verify=VERIFY
        )
        answer = resp.json()["choices"][0]["message"]["content"].strip().lower()
        return "hair" if "hair" in answer else "product"
    except Exception as e:
        print(f"Classify error: {e}")
        return "product"


def analyze_hair(image_bytes_list):
    """Структурированная диагностика волос по 1–3 фото (визуальная оценка)."""
    if not isinstance(image_bytes_list, (list, tuple)):
        image_bytes_list = [image_bytes_list]
    content = []
    for img in image_bytes_list[:3]:
        b64 = base64.standard_b64encode(img).decode()
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"}})
    n = len(content)
    photos_note = f"Дано {n} фото волос (разные ракурсы)." if n > 1 else "Дано 1 фото волос."
    prompt = (
        "Ты — деликатный эксперт-трихолог. " + photos_note + " "
        "Это ВИЗУАЛЬНАЯ оценка по фото — не выдумывай то, чего не видно, и не оценивай внешность человека. "
        "Оцени только волосы. Ответь СТРОГО в формате с разделителями, без лишнего текста, без эмодзи-галочек:\n\n"
        "===TYPE===\nТип волос (прямые/волнистые/кудрявые/курчавые), одна строка.\n"
        "===DENSITY===\nГустота (ниже средней/средняя/выше средней), одна строка.\n"
        "===ENDS===\nСостояние кончиков (здоровые/секущиеся/ломкие/сухие), одна строка.\n"
        "===SCALP===\nЖирность кожи головы (если видно: сухая/нормальная/склонна к жирности; иначе «не видно»), одна строка.\n"
        "===CURL===\nНаличие и тип завитка (если видно), одна строка.\n"
        "===SHEDDING===\nПризнаки выпадения (осторожно: «возможны признаки» / «не заметно по фото»), одна строка.\n"
        "===PROBLEMS===\nОсновные проблемы — 2–4 пункта через запятую (сухость, пушение, тусклость и т.п.).\n"
        "===RATING===\nОбщий рейтинг состояния волос числом от 0 до 10 с одной десятой (например 6.8). Только число.\n"
        "===SUMMARY===\n2–3 тёплые строки: что в целом с волосами и на что обратить внимание.\n"
        "===END==="
    )
    content.append({"type": "text", "text": prompt})
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        json={
            "model": "gpt-4o-mini",
            "max_tokens": 700,
            "messages": [{"role": "user", "content": content}]
        },
        verify=VERIFY
    )
    text = resp.json()["choices"][0]["message"]["content"]
    return parse_hair_diagnosis(text)


def parse_hair_diagnosis(text):
    """Разбирает структурированный ответ диагностики волос."""
    def sec(a, b):
        try:
            s = text.index(a) + len(a)
            e = text.index(b, s)
            return text[s:e].strip()
        except ValueError:
            return ""
    d = {
        "hair_type": sec("===TYPE===", "===DENSITY==="),
        "density": sec("===DENSITY===", "===ENDS==="),
        "ends_state": sec("===ENDS===", "===SCALP==="),
        "scalp": sec("===SCALP===", "===CURL==="),
        "curl": sec("===CURL===", "===SHEDDING==="),
        "shedding": sec("===SHEDDING===", "===PROBLEMS==="),
        "problems": sec("===PROBLEMS===", "===RATING==="),
        "summary": sec("===SUMMARY===", "===END==="),
    }
    rating_raw = sec("===RATING===", "===SUMMARY===")
    import re
    m = re.search(r"(\d+(?:[.,]\d+)?)", rating_raw)
    d["rating"] = float(m.group(1).replace(",", ".")) if m else None
    if d["rating"] is not None:
        d["rating"] = max(0.0, min(10.0, d["rating"]))
    d["full_text"] = format_hair_diagnosis(d)
    return d


def format_hair_diagnosis(d):
    """Красивый текст диагностики для отправки и хранения."""
    lines = ["✦ Диагностика волос", "(визуальная оценка по фото)", ""]
    rows = [
        ("Тип волос", d.get("hair_type")),
        ("Густота", d.get("density")),
        ("Кончики", d.get("ends_state")),
        ("Кожа головы", d.get("scalp")),
        ("Завиток", d.get("curl")),
        ("Выпадение", d.get("shedding")),
    ]
    for label, val in rows:
        if val and val.lower() not in ("не видно", "—", ""):
            lines.append(f"· {label}: {val}")
    if d.get("rating") is not None:
        lines.append("")
        lines.append(f"★ Общий рейтинг: {d['rating']:.1f}/10")
    if d.get("problems"):
        lines.append("")
        lines.append("Основные проблемы:")
        for p in [x.strip() for x in d["problems"].replace("\n", ",").split(",") if x.strip()]:
            lines.append(f"— {p}")
    if d.get("summary"):
        lines.append("")
        lines.append(d["summary"])
    return "\n".join(lines)


def save_hair_diagnosis(user_id, d):
    """Сохраняет диагностику в историю и обновляет текущее состояние пользователя."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO hair_diagnostics "
                    "(user_id, rating, hair_type, density, ends_state, scalp, curl, shedding, problems, full_text) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (user_id, d.get("rating"), d.get("hair_type"), d.get("density"), d.get("ends_state"),
                     d.get("scalp"), d.get("curl"), d.get("shedding"), d.get("problems"), d.get("full_text"))
                )
            conn.commit()
    except Exception as e:
        print(f"save_hair_diagnosis error: {e}")
    update_user(user_id, hair_analysis=d.get("full_text"), hair_rating=d.get("rating"))


def todays_diagnosis(user_id):
    """Диагностика, сделанная сегодня (кэш на день). None если сегодня ещё не было."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT rating, full_text FROM hair_diagnostics "
                    "WHERE user_id = %s AND created_at::date = (NOW() AT TIME ZONE 'UTC')::date "
                    "ORDER BY created_at DESC, id DESC LIMIT 1",
                    (user_id,)
                )
                return cur.fetchone()
    except Exception as e:
        print(f"todays_diagnosis error: {e}")
        return None


def previous_diagnosis(user_id):
    """Предыдущая диагностика (для сравнения прогресса). None если первая."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT rating, problems, created_at FROM hair_diagnostics "
                    "WHERE user_id = %s ORDER BY created_at DESC, id DESC LIMIT 2",
                    (user_id,)
                )
                rows = cur.fetchall()
        return rows[1] if len(rows) >= 2 else None
    except Exception as e:
        print(f"previous_diagnosis error: {e}")
        return None


def generate_final_report(user):
    """Итоговый разбор: краткая суть + подробный план через разделители (1 вызов)."""
    profile = build_profile_text(user, include_hair=True, include_products=True)
    prompt = f"""Ты — консультант по волосам. Честно, по делу, без лести и воды. Не оценивай человека — оценивай ситуацию с волосами.

Данные:
{profile}

Дай ответ СТРОГО в формате с разделителями, без лишнего текста, оформление только · — :

===SHORT===
Суть в 2–3 коротких строках: что происходит и главный совет. Просто и понятно.
===STEP===
Один конкретный шаг, с которого начать сегодня (одна строка).
===FULL===
Подробный план под цель, кратко по пунктам:
Уход: частота мытья, вода, техника.
Средства: что искать в составе, что заменить (учти используемые).
Питание: ключевые нутриенты с примерами продуктов.
Без выдумок. Если данных мало — скажи прямо.
===END==="""
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        json={
            "model": "gpt-4o-mini",
            "max_tokens": 1100,
            "messages": [{"role": "user", "content": prompt}]
        },
        verify=VERIFY
    )
    return resp.json()["choices"][0]["message"]["content"]


def parse_final_report(text):
    """Разбирает разбор на short/step/full."""
    def section(a, b):
        try:
            s = text.index(a) + len(a)
            e = text.index(b, s)
            return text[s:e].strip()
        except ValueError:
            return ""
    short = section("===SHORT===", "===STEP===")
    step = section("===STEP===", "===FULL===")
    full = section("===FULL===", "===END===")
    if not full:
        full = text.split("===FULL===")[-1].replace("===END===", "").strip()
    if not short:  # модель не дала разделители — отдадим всё как есть
        short = text.strip()
    return {"short": short, "step": step, "full": full}


# ─── Обработчики ───────────────────────────────────────────

def handle_start(msg):
    chat_id = msg["chat"]["id"]
    user_id = msg["from"]["id"]
    username = msg["from"].get("username", "")
    first_name = msg["from"].get("first_name", "")

    # Deep-link из Mini App: /start diag → сразу диагностика волос
    parts = (msg.get("text") or "").split(maxsplit=1)
    payload = parts[1].strip() if len(parts) > 1 else ""
    if payload == "diag":
        get_user(user_id, username=username, first_name=first_name)
        handle_diagnostics(msg)
        return

    name = first_name or "дорогая"

    send_message(chat_id,
        f"Привет, {name} ✦\n\n"
        "Я помогу с волосами и разберу состав косметики — честно и по делу.",
        reply_markup={"inline_keyboard": [[
            {"text": "Продолжить ✨", "callback_data": "continue_start"}
        ]]}
    )

    # Создаём пользователя при необходимости и сбрасываем состояние
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id FROM users WHERE user_id = %s", (user_id,))
                exists = cur.fetchone()
                if exists:
                    cur.execute(
                        "UPDATE users SET username = %s, first_name = %s, awaiting = 'none' WHERE user_id = %s",
                        (username, first_name, user_id)
                    )
                else:
                    cur.execute(
                        "INSERT INTO users (user_id, username, first_name, quiz_step, quiz_done) VALUES (%s, %s, %s, 0, FALSE)",
                        (user_id, username, first_name)
                    )
                conn.commit()
    except Exception as e:
        print(f"Error in start: {e}")


def send_mode_picker(chat_id):
    """Главная развилка: как человек хочет использовать бота."""
    send_message(chat_id,
        "Как вы хотите использовать бота?\n\n"
        "🔍 Сканер косметики — присылаете фото средства и узнаёте о нём всё: "
        "состав, для каких волос подходит, плюсы и минусы.\n\n"
        "✦ Улучшить состояние волос — небольшой опрос, разбор ваших волос "
        "и персональный план ухода, питания и подбора средств.",
        reply_markup={"inline_keyboard": [
            [{"text": "🔍 Сканер косметики", "callback_data": "mode_scanner"}],
            [{"text": "✦ Улучшить состояние волос", "callback_data": "mode_hair"}],
        ]}
    )


def start_scanner_mode(chat_id, user_id):
    update_user(user_id, mode="scanner", awaiting="none")
    send_message(chat_id,
        "🔍 Режим «Сканер косметики»\n\n"
        "Пришлите фото любого косметического средства — я разберу его состав, "
        "оценю и подскажу, для каких волос оно подходит.\n\n"
        "Вся отсканированная косметика сохраняется — открыть список можно командой /cosmetics."
    )


def start_hair_mode(chat_id, user_id, restart_quiz=True):
    update_user(user_id, mode="hair")
    if restart_quiz:
        update_user(user_id, quiz_step=0, quiz_done=False, awaiting="none")
        send_message(chat_id,
            "✦ Отлично! Давайте пройдём небольшой опрос — это займёт пару минут "
            "и поможет мне дать точные рекомендации."
        )
        time.sleep(1)
        send_quiz_question(chat_id, 0)


def handle_quiz_text_answer(msg, user):
    chat_id = msg["chat"]["id"]
    user_id = msg["from"]["id"]
    step_index = user["quiz_step"]

    if step_index >= len(QUIZ_STEPS):
        return False

    step = QUIZ_STEPS[step_index]
    if step["type"] != "text":
        return False

    # Сохраняем ответ
    update_user(user_id, **{step["key"]: msg["text"]})

    # Следующий шаг
    next_step = step_index + 1
    if next_step >= len(QUIZ_STEPS):
        finish_quiz(chat_id, user_id)
    else:
        update_user(user_id, quiz_step=next_step)
        send_quiz_question(chat_id, next_step)

    return True


def handle_quiz_callback(callback, user):
    query_id = callback["id"]
    user_id = callback["from"]["id"]
    chat_id = callback["message"]["chat"]["id"]
    data = callback.get("data", "")

    tg("answerCallbackQuery", {"callback_query_id": query_id})

    # Защита от повторного нажатия: если квиз уже завершён — игнорируем.
    fresh = get_user(user_id)
    if fresh.get("quiz_done"):
        return False

    # data = "quiz_KEY_INDEX" — KEY может содержать подчёркивания (wash_frequency, styling_time),
    # поэтому парсим с конца: последний сегмент — индекс, всё между "quiz_" и ним — ключ.
    if not data.startswith("quiz_"):
        return False
    body = data[len("quiz_"):]
    key, sep, index_str = body.rpartition("_")
    if not sep or not index_str.isdigit():
        return False
    option_index = int(index_str)

    # Находим шаг по key
    step = next((s for s in QUIZ_STEPS if s["key"] == key), None)
    if not step or step["type"] != "buttons":
        return False

    value = step["options"][option_index]
    update_user(user_id, **{key: value})

    step_index = QUIZ_STEPS.index(step)
    next_step = step_index + 1

    if next_step >= len(QUIZ_STEPS):
        finish_quiz(chat_id, user_id)
    else:
        update_user(user_id, quiz_step=next_step)
        send_quiz_question(chat_id, next_step)

    return True


def finish_quiz(chat_id, user_id):
    update_user(user_id, quiz_done=True, quiz_step=len(QUIZ_STEPS), awaiting="hair_photo")
    send_message(chat_id,
        "✦ Профиль создан\n\n"
        "Благодарю за ответы — теперь я знаю вас лучше.\n\n"
        "Чтобы разбор был точнее, пришлите фото своих волос или селфи — "
        "я оценю их структуру и особенности. Это по желанию.\n\n"
        "◦ Отправьте фото — или нажмите «Пропустить»",
        reply_markup={"inline_keyboard": [[
            {"text": "Пропустить", "callback_data": "skip_hair"}
        ]]}
    )


def deliver_final_report(chat_id, user_id):
    """Генерирует и отправляет итоговый персональный разбор по всей базе."""
    update_user(user_id, awaiting="none")

    user = get_user(user_id)
    allowed, way = can_use_ai(user, "plan")
    if not allowed:
        offer_subscription(chat_id, "Первый персональный план вы уже получили.")
        return

    send_message(chat_id,
        "✦ Готовлю ваш персональный разбор...\n\n"
        "◦ Изучаю профиль\n"
        "◦ Сверяю привычки и средства\n"
        "◦ Составляю план"
    )
    try:
        report = generate_final_report(user)
        parsed = parse_final_report(report)
        # Сохраняем полный план для кнопки «Подробнее»
        update_user(user_id, final_report=parsed["full"] or report)
        consume_ai_action(user_id, way, "plan")

        short = "✦ Ваш разбор\n\n" + parsed["short"]
        if parsed["step"]:
            short += "\n\n▸ Первый шаг: " + parsed["step"]
        kb = []
        if parsed["full"]:
            kb.append([{"text": "Подробнее ▾", "callback_data": "report_full"}])
        send_message(chat_id, short, reply_markup={"inline_keyboard": kb} if kb else None)
        send_message(chat_id, "◦ Пришлите фото косметики — разберу состав. Или откройте /app")
    except Exception as e:
        print(f"Error final report: {e}")
        send_message(chat_id,
            "◦ Не удалось составить разбор сейчас. Попробуйте ещё раз чуть позже."
        )


def show_profile(chat_id, user_id, username="", first_name=""):
    """Показывает пользователю его сохранённый профиль."""

    user = get_user(user_id, username=username, first_name=first_name)

    if not user.get("quiz_done"):
        send_message(chat_id,
            "◦ Ваш профиль ещё не заполнен.\n"
            "Напишите /start, чтобы пройти небольшой опрос."
        )
        return

    lines = ["✦ Ваш профиль", ""]

    def add(label, value):
        if value:
            lines.append(f"— {label}: {value}")

    handle = user.get("username")
    add("Логин", f"@{handle}" if handle else None)
    add("Имя", user.get("first_name"))
    add("Возраст", user.get("age"))
    add("Пол", user.get("gender"))
    location = ", ".join(filter(None, [user.get("city"), user.get("country")]))
    add("Местоположение", location)

    lines.append("")
    lines.append("✦ Цели и привычки")
    lines.append("")
    add("Цель", user.get("goal"))
    add("Что беспокоит", user.get("problem"))
    add("Частота мытья", user.get("wash_frequency"))
    add("Время на укладку", user.get("styling_time"))

    if user.get("hair_analysis"):
        lines.append("")
        lines.append("✦ Характеристика волос")
        lines.append("")
        lines.append(user["hair_analysis"])

    products = get_user_products_used(user_id)
    if products:
        lines.append("")
        lines.append("✦ Средства, которыми вы пользуетесь")
        lines.append("")
        for p in products:
            lines.append(f"· {p}")

    # Статус подписки
    lines.append("")
    lines.append("✦ Подписка")
    lines.append("")
    if subscription_active(user):
        expires = user.get("sub_expires")
        try:
            if isinstance(expires, str):
                expires = datetime.fromisoformat(expires)
            date_str = expires.strftime("%d.%m.%Y")
        except Exception:
            date_str = "—"
        lines.append("— Статус: активна")
        lines.append(f"— Действует до: {date_str}")
        lines.append(f"— Осталось действий: {sub_actions_left(user)} из {SUB_MONTHLY_ACTIONS}")
    else:
        lines.append("— Статус: бесплатный доступ")
        free_left = []
        if not user.get("free_plan_used"):
            free_left.append("план")
        if not user.get("free_hair_used"):
            free_left.append("фото волос")
        if not user.get("free_product_used"):
            free_left.append("фото средства")
        if free_left:
            lines.append("— Бесплатно ещё доступно: " + ", ".join(free_left))
        else:
            lines.append(f"— Бесплатный лимит исчерпан · подписка {SUB_PRICE_STARS} ⭐/мес")

    send_message(chat_id, "\n".join(lines),
        reply_markup={"inline_keyboard": [[
            {"text": "Продолжить ›", "callback_data": "menu_open"}
        ]]}
    )


def handle_stats(msg):
    """Команда /stats из обычного сообщения."""
    chat_id = msg["chat"]["id"]
    frm = msg.get("from", {})
    show_profile(chat_id, frm["id"], frm.get("username", ""), frm.get("first_name", ""))


def send_action_menu(chat_id):
    """Меню действий после «Продолжить»."""
    send_message(chat_id,
        "Что хотите сделать дальше?",
        reply_markup={"inline_keyboard": [
            [{"text": "✦ Приступить к разбору волос", "callback_data": "act_report"}],
            [{"text": "📷 Прислать фото (волосы или средство)", "callback_data": "act_photo"}],
            [{"text": "🔍 Моя косметика", "callback_data": "cosmetics_list"}],
            [{"text": "👤 Мой профиль", "callback_data": "act_profile"}],
            [{"text": "✦ Приложение (Премиум)", "callback_data": "open_app"}],
            [{"text": "🔄 Сменить режим", "callback_data": "home"}],
        ]}
    )


def send_cosmetics_list(chat_id, user_id):
    """Нумерованный список косметики пользователя с кнопками выбора."""
    items = get_user_cosmetics(user_id)
    if not items:
        send_message(chat_id,
            "◦ Ваша база косметики пуста.\n"
            "Отсканируйте средство — и оно появится здесь.",
            reply_markup={"inline_keyboard": [[
                {"text": "🏠 В меню", "callback_data": "home"}
            ]]}
        )
        return

    lines = ["🔍 Ваша косметика", ""]
    buttons = []
    row = []
    for i, c in enumerate(items, start=1):
        name = c.get("product_name") or "Косметика"
        lines.append(f"{i}. {name}")
        row.append({"text": str(i), "callback_data": f"cos_{c['id']}"})
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([{"text": "🏠 В меню", "callback_data": "home"}])

    lines.append("")
    lines.append("Выберите номер, чтобы посмотреть детали.")
    send_message(chat_id, "\n".join(lines), reply_markup={"inline_keyboard": buttons})


def handle_cosmetics(msg):
    """Команда /cosmetics."""
    chat_id = msg["chat"]["id"]
    user_id = msg.get("from", {}).get("id")
    get_user(user_id,
             username=msg.get("from", {}).get("username", ""),
             first_name=msg.get("from", {}).get("first_name", ""))
    send_cosmetics_list(chat_id, user_id)


def handle_app(msg):
    """Команда /app — открыть Mini App (только для подписчиков)."""
    chat_id = msg["chat"]["id"]
    user_id = msg.get("from", {}).get("id")
    user = get_user(user_id,
                    username=msg.get("from", {}).get("username", ""),
                    first_name=msg.get("from", {}).get("first_name", ""))
    if not WEBAPP_URL:
        send_message(chat_id, "◦ Приложение пока недоступно.")
        return
    if not subscription_active(user):
        offer_subscription(chat_id, "Приложение с планом и чатом эксперта доступно по подписке.")
        return
    send_message(chat_id,
        "✦ Ваше приложение готово\n\n"
        "Внутри: план на неделю, прогресс с заданиями, ваша косметика и чат с экспертом.",
        reply_markup={"inline_keyboard": [[
            {"text": "Открыть приложение ✦", "web_app": {"url": WEBAPP_URL}}
        ]]}
    )


def handle_restart(msg):
    """Команда /restart — сброс профиля и квиза (косметика и подписка не трогаются)."""
    chat_id = msg["chat"]["id"]
    user_id = msg.get("from", {}).get("id")
    get_user(user_id,
             username=msg.get("from", {}).get("username", ""),
             first_name=msg.get("from", {}).get("first_name", ""))
    # Обнуляем профиль и состояние
    update_user(
        user_id,
        quiz_step=0, quiz_done=False, awaiting="none", mode=None,
        goal=None, problem=None, age=None, gender=None,
        country=None, city=None, wash_frequency=None, styling_time=None,
        hair_analysis=None, final_report=None,
    )
    send_message(chat_id, "♻️ Профиль сброшен. Начинаем заново.")
    handle_start(msg)


def handle_idea(msg):
    """Команда /idea — пользователь предлагает идею."""
    chat_id = msg["chat"]["id"]
    user_id = msg.get("from", {}).get("id")
    user = get_user(user_id,
                    username=msg.get("from", {}).get("username", ""),
                    first_name=msg.get("from", {}).get("first_name", ""))
    update_user(user_id, awaiting="idea")
    send_message(chat_id,
        "💡 Напишите вашу идею или пожелание одним сообщением — "
        "я передам её разработчику."
    )


def handle_diagnostics(msg):
    """Команда /diagnostics — новая диагностика волос (фото → структурный разбор)."""
    chat_id = msg["chat"]["id"]
    user_id = msg.get("from", {}).get("id")
    user = get_user(user_id,
                    username=msg.get("from", {}).get("username", ""),
                    first_name=msg.get("from", {}).get("first_name", ""))
    update_user(user_id, mode="hair", awaiting="hair_photo")
    prev = previous_diagnosis(user_id)
    intro = "✦ Диагностика волос\n\n"
    if prev and prev.get("rating") is not None:
        intro += f"Прошлый рейтинг: {prev['rating']:.1f}/10. Сравним с новым.\n\n"
    send_message(chat_id,
        intro +
        "Пришлите фото волос — желательно при дневном свете, волосы распущены.\n"
        "Для точности можно прислать 2–3 фото: спереди, сверху и сбоку "
        "(я учту последнее присланное фото как основное).\n\n"
        "Это визуальная оценка по фото — не диагноз."
    )


def save_and_forward_idea(msg, user):
    """Сохраняет идею и пересылает администратору."""
    chat_id = msg["chat"]["id"]
    user_id = msg["from"]["id"]
    text = msg.get("text", "").strip()
    username = msg["from"].get("username", "")
    first_name = msg["from"].get("first_name", "")
    update_user(user_id, awaiting="none")
    if not text:
        send_message(chat_id, "◦ Пустое сообщение. Попробуйте ещё раз: /idea")
        return
    save_idea(user_id, username, first_name, text)
    handle = f"@{username}" if username else "—"
    notify_admin(f"💡 Новая идея\n\nОт: {first_name or '—'} ({handle}), id {user_id}\n\n{text}")
    send_message(chat_id, "✦ Спасибо! Ваша идея отправлена разработчику.")


def process_hair_photo(chat_id, user_id, image_bytes, then_report=True):
    """Анализ фото волос → запись в БД. При then_report — затем выдать разбор."""
    user = get_user(user_id)
    allowed, way = can_use_ai(user, "hair")
    if not allowed:
        offer_subscription(chat_id, "Бесплатный анализ волос вы уже использовали.")
        return

    # Кэш на день: если сегодня уже была диагностика — показываем её, без нового запроса к GPT
    today = todays_diagnosis(user_id)
    if today:
        send_message(chat_id,
            (today.get("full_text") or "Диагностика готова.") +
            "\n\n(Сегодня вы уже делали диагностику — показываю её. Новая будет доступна завтра.)"
        )
        return

    send_message(chat_id, "✦ Провожу диагностику волос...")
    try:
        prev = previous_diagnosis(user_id)
        d = analyze_hair(image_bytes)
        save_hair_diagnosis(user_id, d)
        consume_ai_action(user_id, way, "hair")
        send_message(chat_id, d.get("full_text") or "Диагностика готова.")
        # сравнение с прошлой диагностикой (прогресс)
        if prev and prev.get("rating") is not None and d.get("rating") is not None:
            diff = d["rating"] - prev["rating"]
            arrow = "↑" if diff > 0 else ("↓" if diff < 0 else "→")
            send_message(chat_id,
                f"📈 Динамика рейтинга: {prev['rating']:.1f} → {d['rating']:.1f} {arrow}")
    except Exception as e:
        print(f"Error hair analysis: {e}")
        send_message(chat_id, "◦ Не удалось разобрать фото волос. Попробуйте другое фото.")
    if then_report:
        deliver_final_report(chat_id, user_id)


def process_product_photo(chat_id, user, image_bytes):
    """Анализ фото косметического средства."""
    user_id = user["user_id"]

    allowed, way = can_use_ai(user, "product")
    if not allowed:
        offer_subscription(chat_id, "Бесплатный анализ косметики вы уже использовали.")
        return

    send_message(chat_id,
        "✦ Анализирую средство...\n\n"
        "◦ Изучаю состав\n"
        "◦ Готовлю отчёт"
    )
    try:
        parsed = analyze_cosmetic(image_bytes)
        consume_ai_action(user_id, way, "product")

        # Сохраняем в личную базу косметики
        cos_id = save_user_cosmetic(
            user_id, parsed["name"], parsed.get("analysis", ""),
            summary=parsed["summary"], ingredients=parsed["ingredients"], usage=parsed["usage"]
        )

        head = f"✦ {parsed['name']}\n\n{parsed['summary']}".strip()
        detail_row = []
        if cos_id and parsed["ingredients"]:
            detail_row.append({"text": "Подробнее ▾", "callback_data": f"cosing_{cos_id}"})
        if cos_id and parsed["usage"]:
            detail_row.append({"text": "Как использовать", "callback_data": f"cosuse_{cos_id}"})
        kb = [detail_row] if detail_row else []
        if cos_id:
            kb.append([{"text": "🔎 Поиск аналогов ✦", "callback_data": f"analogs_{cos_id}"}])
        send_message(chat_id, head, reply_markup={"inline_keyboard": kb} if kb else None)
    except Exception as e:
        print(f"Error analyzing product: {e}")
        send_message(chat_id,
            "◦ Произошла ошибка при анализе.\n"
            "Пожалуйста, попробуйте ещё раз или отправьте другое фото."
        )


def offer_premium_scanner(chat_id):
    """Предложение премиума в режиме сканера (без указания числа лимита)."""
    send_message(chat_id,
        "✦ Бесплатный лимит исчерпан\n\n"
        "Вы использовали все бесплатные сканирования. С Премиумом лимит "
        "становится гораздо больше — сканируйте косметику и сохраняйте её в свою базу "
        "практически без ограничений.\n\n"
        f"Стоимость: {SUB_PRICE_STARS} ⭐ Telegram Stars в месяц.",
        reply_markup={"inline_keyboard": [[
            {"text": "Открыть Премиум", "callback_data": "buy_sub"}
        ]]}
    )


def process_scanner_photo(chat_id, user, image_bytes):
    """Режим «Сканер»: анализ косметики + сохранение в личную базу пользователя."""
    user_id = user["user_id"]

    # Доступ: бесплатно SCANNER_FREE сканов, далее — подписка
    if subscription_active(user):
        way = "sub"
    elif (user.get("scanner_used") or 0) < SCANNER_FREE:
        way = "free_scanner"
    else:
        offer_premium_scanner(chat_id)
        return

    send_message(chat_id,
        "🔍 Сканирую косметику...\n\n"
        "◦ Изучаю состав\n"
        "◦ Готовлю отчёт"
    )
    try:
        parsed = analyze_cosmetic(image_bytes)
        product_name = parsed["name"]

        # Сохраняем в личную базу косметики (с разбивкой) и получаем id записи
        cos_id = save_user_cosmetic(
            user_id, product_name, parsed.get("analysis", ""),
            summary=parsed["summary"], ingredients=parsed["ingredients"], usage=parsed["usage"]
        )

        # Списываем использование
        if way == "sub":
            consume_ai_action(user_id, "sub", "product")
        else:
            update_user(user_id, scanner_used=(user.get("scanner_used") or 0) + 1)

        # Краткое описание + кнопки
        head = f"✦ {product_name}\n\n{parsed['summary']}".strip()
        buttons = []
        if cos_id:
            row = []
            if parsed["ingredients"]:
                row.append({"text": "Подробнее ▾", "callback_data": f"cosing_{cos_id}"})
            if parsed["usage"]:
                row.append({"text": "Как использовать", "callback_data": f"cosuse_{cos_id}"})
            if row:
                buttons.append(row)
            buttons.append([{"text": "🔎 Поиск аналогов ✦", "callback_data": f"analogs_{cos_id}"}])
        send_message(chat_id, head,
                     reply_markup={"inline_keyboard": buttons} if buttons else None)
        send_message(chat_id, "◦ Сохранено в вашу базу — /cosmetics или приложение /app")
    except Exception as e:
        print(f"Error scanner: {e}")
        send_message(chat_id,
            "◦ Произошла ошибка при анализе. Попробуйте ещё раз или другое фото."
        )


def handle_photo(msg):
    chat_id = msg["chat"]["id"]
    user_id = msg["from"]["id"]
    username = msg["from"].get("username", "")
    first_name = msg["from"].get("first_name", "")

    user = get_user(user_id, username=username, first_name=first_name)

    # Режим ещё не выбран — показываем развилку
    if not user.get("mode"):
        send_mode_picker(chat_id)
        return

    # Скачиваем фото один раз
    try:
        photo = msg["photo"][-1]
        file_info = tg("getFile", {"file_id": photo["file_id"]})
        image_bytes = download_file(file_info["result"]["file_path"])
    except Exception as e:
        print(f"Error downloading photo: {e}")
        send_message(chat_id, "◦ Не удалось загрузить фото. Попробуйте ещё раз.")
        return

    # Бот явно ждёт фото волос (после квиза ИЛИ запрос диагностики из Mini App) —
    # это приоритетнее режима; диагностика доступна всем.
    if user.get("awaiting") == "hair_photo":
        process_hair_photo(chat_id, user_id, image_bytes, then_report=bool(user.get("quiz_done")))
        update_user(user_id, awaiting="none")
        return

    # Режим «Сканер косметики» — любое фото трактуем как косметику
    if user.get("mode") == "scanner":
        process_scanner_photo(chat_id, user, image_bytes)
        return

    # Режим «Волосы»: если квиз не пройден — просим завершить
    if not user.get("quiz_done"):
        step_index = user.get("quiz_step", 0)
        send_message(chat_id,
            "◦ Прежде чем начать анализ, завершите небольшой опрос.\n"
            "Это займёт пару минут и поможет дать персональные рекомендации."
        )
        send_quiz_question(chat_id, step_index)
        return

    # Иначе сами определяем, что на фото: волосы или средство
    kind = classify_photo(image_bytes)
    if kind == "hair":
        process_hair_photo(chat_id, user_id, image_bytes, then_report=False)
    else:
        process_product_photo(chat_id, user, image_bytes)


def handle_callback(callback):
    query_id = callback["id"]
    user_id = callback["from"]["id"]
    chat_id = callback["message"]["chat"]["id"]
    data = callback.get("data", "")

    user = get_user(user_id)

    # Квиз
    if data.startswith("quiz_"):
        handle_quiz_callback(callback, user)
        return

    tg("answerCallbackQuery", {"callback_query_id": query_id})

    # Кнопка «Продолжить» после приветствия
    if data == "continue_start":
        send_mode_picker(chat_id)
        return

    # Полный план по кнопке «Подробнее»
    if data == "report_full":
        full = (user or {}).get("final_report") or ""
        send_message(chat_id, ("✦ Подробный план\n\n" + full) if full else "◦ План пока недоступен.")
        return

    # Выбор режима
    if data == "mode_scanner":
        start_scanner_mode(chat_id, user_id)
        return
    if data == "mode_hair":
        start_hair_mode(chat_id, user_id, restart_quiz=True)
        return
    if data == "home":
        send_mode_picker(chat_id)
        return

    # Состав конкретной косметики: cosing_<id>
    if data.startswith("cosing_"):
        try:
            cid = int(data.split("_")[1])
        except (ValueError, IndexError):
            return
        match = next((c for c in get_user_cosmetics(user_id) if c["id"] == cid), None)
        if match:
            text = match.get("ingredients") or match.get("analysis") or "Нет данных о составе."
            send_message(chat_id, "✦ Состав · " + (match.get("product_name") or "") + "\n\n" + text)
        return
    # Как использовать: cosuse_<id>
    if data.startswith("cosuse_"):
        try:
            cid = int(data.split("_")[1])
        except (ValueError, IndexError):
            return
        match = next((c for c in get_user_cosmetics(user_id) if c["id"] == cid), None)
        if match:
            text = match.get("usage") or "Нет инструкции по применению."
            send_message(chat_id, "✦ Как использовать · " + (match.get("product_name") or "") + "\n\n" + text)
        return
    # Поиск аналогов (Pro): analogs_<id>
    if data.startswith("analogs_"):
        try:
            cid = int(data.split("_")[1])
        except (ValueError, IndexError):
            return
        u = get_user(user_id)
        if not subscription_active(u):
            offer_subscription(chat_id, "Поиск аналогов доступен в Премиуме.")
            return
        match = next((c for c in get_user_cosmetics(user_id) if c["id"] == cid), None)
        if not match:
            return
        send_message(chat_id, "🔎 Ищу аналоги — дешевле и лучше...")
        try:
            text = find_analogs(match, u)
            send_message(chat_id, text)
        except Exception as e:
            print(f"analogs error: {e}")
            send_message(chat_id, "Не удалось найти аналоги сейчас. Попробуйте позже.")
        return

    # Открыть конкретную косметику из списка: cos_<id>
    if data.startswith("cos_"):
        try:
            cos_id = int(data.split("_")[1])
        except (ValueError, IndexError):
            return
        items = get_user_cosmetics(user_id)
        match = next((c for c in items if c["id"] == cos_id), None)
        if match:
            body = match.get("summary") or match.get("analysis") or ""
            detail_row = []
            if match.get("ingredients"):
                detail_row.append({"text": "Подробнее ▾", "callback_data": f"cosing_{cos_id}"})
            if match.get("usage"):
                detail_row.append({"text": "Как использовать", "callback_data": f"cosuse_{cos_id}"})
            kb = []
            if detail_row:
                kb.append(detail_row)
            kb.append([
                {"text": "‹ К списку", "callback_data": "cosmetics_list"},
                {"text": "🏠 В меню", "callback_data": "home"},
            ])
            send_message(chat_id,
                f"✦ {match.get('product_name') or 'Косметика'}\n\n" + body,
                reply_markup={"inline_keyboard": kb}
            )
        return
    if data == "cosmetics_list":
        send_cosmetics_list(chat_id, user_id)
        return

    # Пропуск фото волос → сразу к итоговому разбору
    if data == "skip_hair":
        if user.get("awaiting") == "hair_photo":
            deliver_final_report(chat_id, user_id)
        return

    # Меню после /stats
    if data == "menu_open":
        send_action_menu(chat_id)
        return
    if data == "act_report":
        deliver_final_report(chat_id, user_id)
        return
    if data == "act_photo":
        send_message(chat_id,
            "📷 Пришлите фото — волос/селфи или косметического средства.\n"
            "Я сама пойму, что на фото, и сделаю нужный разбор."
        )
        return
    if data == "act_profile":
        frm = callback.get("from", {})
        show_profile(chat_id, user_id, frm.get("username", ""), frm.get("first_name", ""))
        return
    if data == "open_app":
        if not WEBAPP_URL:
            send_message(chat_id, "◦ Приложение пока недоступно.")
        elif not subscription_active(user):
            offer_subscription(chat_id, "Приложение с планом и чатом эксперта доступно по подписке.")
        else:
            send_message(chat_id,
                "✦ Откройте приложение — план на неделю, прогресс, косметика и чат с экспертом.",
                reply_markup={"inline_keyboard": [[
                    {"text": "Открыть приложение ✦", "web_app": {"url": WEBAPP_URL}}
                ]]}
            )
        return

    # Оплата подписки
    if data == "buy_sub":
        send_subscription_invoice(chat_id)
        return

    if data.startswith("uses_yes_") or data.startswith("uses_no_"):
        uses = data.startswith("uses_yes_")
        product_id = int(data.split("_")[-1])
        save_user_product(user_id, product_id, uses)
        if uses:
            send_message(chat_id, "✦ Записала в вашу коллекцию ◦")
        else:
            send_message(chat_id, "◦ Поняла, спасибо за ответ.")


# ─── Главный цикл ──────────────────────────────────────────

def main():
    print("Initializing DB...")
    for attempt in range(10):
        try:
            init_db()
            break
        except Exception as e:
            print(f"DB attempt {attempt+1} failed: {e}")
            time.sleep(5)

    print("Bot started...")
    offset = None

    while True:
        try:
            params = {"timeout": 30, "allowed_updates": ["message", "callback_query", "pre_checkout_query"]}
            if offset:
                params["offset"] = offset

            resp = requests.get(f"{API_URL}/getUpdates", params=params, timeout=35, verify=VERIFY)
            data = resp.json()

            for update in data.get("result", []):
                offset = update["update_id"] + 1

                # Подтверждение оплаты до списания Stars — нужно ответить ok
                if "pre_checkout_query" in update:
                    pcq = update["pre_checkout_query"]
                    tg("answerPreCheckoutQuery", {"pre_checkout_query_id": pcq["id"], "ok": True})
                    continue

                if "callback_query" in update:
                    handle_callback(update["callback_query"])
                    continue

                msg = update.get("message", {})
                if not msg:
                    continue

                # Успешная оплата → активируем подписку
                if msg.get("successful_payment"):
                    uid = msg.get("from", {}).get("id")
                    cid = msg.get("chat", {}).get("id")
                    if uid:
                        activate_subscription(uid)
                        markup = None
                        if WEBAPP_URL:
                            markup = {"inline_keyboard": [[
                                {"text": "Открыть приложение ✦", "web_app": {"url": WEBAPP_URL}}
                            ]]}
                        send_message(cid,
                            "✦ Подписка активирована\n\n"
                            f"Вам доступно {SUB_MONTHLY_ACTIONS} AI-действий на {SUB_DAYS} дней.\n"
                            "Открыто приложение с планом на неделю и чатом эксперта — команда /app.",
                            reply_markup=markup
                        )
                    continue

                chat_id = msg.get("chat", {}).get("id")
                if not chat_id:
                    continue

                user_id = msg.get("from", {}).get("id")
                if not user_id:
                    continue

                text = msg.get("text", "")
                command = text.split()[0].split("@")[0] if text.startswith("/") else ""

                if command == "/start":
                    handle_start(msg)
                elif command == "/restart":
                    handle_restart(msg)
                elif command in ("/stats", "/stas", "/profile"):
                    handle_stats(msg)
                elif command in ("/cosmetics", "/cosmetic"):
                    handle_cosmetics(msg)
                elif command == "/app":
                    handle_app(msg)
                elif command == "/idea":
                    handle_idea(msg)
                elif command in ("/diagnostics", "/diagnostic", "/diag"):
                    handle_diagnostics(msg)
                elif msg.get("photo"):
                    handle_photo(msg)
                elif msg.get("text"):
                    user = get_user(user_id)
                    # Ждём текст идеи — приоритет над остальным
                    if user.get("awaiting") == "idea":
                        save_and_forward_idea(msg, user)
                    # Режим не выбран — показываем развилку
                    elif not user.get("mode"):
                        send_mode_picker(chat_id)
                    # Режим «Сканер» — ждём фото косметики
                    elif user.get("mode") == "scanner":
                        send_message(chat_id, "🔍 Пришлите фото косметического средства — я его разберу.")
                    # Режим «Волосы»: квиз
                    elif not user.get("quiz_done"):
                        handled = handle_quiz_text_answer(msg, user)
                        if not handled:
                            send_message(chat_id, "◦ Ответьте на вопрос опроса текстом.")
                    elif user.get("awaiting") == "hair_photo":
                        send_message(chat_id,
                            "◦ Пришлите фото волос или селфи — или нажмите «Пропустить».",
                            reply_markup={"inline_keyboard": [[
                                {"text": "Пропустить", "callback_data": "skip_hair"}
                            ]]}
                        )
                    else:
                        send_message(chat_id, "◦ Отправьте фото средства — или откройте меню: /stats")

        except Exception as e:
            print(f"Error: {e}")
            time.sleep(3)


def start_admin_bot_bg():
    """Запускает админ-бота в фоне, если задан ADMIN_BOT_TOKEN."""
    if not os.getenv("ADMIN_BOT_TOKEN"):
        return
    try:
        import threading
        from adminbot import run_admin_bot
        threading.Thread(target=run_admin_bot, daemon=True).start()
        print("Admin bot launched in background")
    except Exception as e:
        print(f"Admin bot launch failed: {e}")


if __name__ == "__main__":
    port = os.getenv("PORT")
    if port:
        # Web-сервис Railway: uvicorn в главном потоке (чтобы порт отвечал на healthcheck),
        # бот (long-polling) и админ-бот — в фоновых демон-потоках.
        import threading
        threading.Thread(target=main, daemon=True).start()
        print("Bot started in background thread")
        start_admin_bot_bg()
        import uvicorn
        from webapp import app as webapp_app
        uvicorn.run(webapp_app, host="0.0.0.0", port=int(port), log_level="warning")
    else:
        # Без PORT — бот + админ-бот
        start_admin_bot_bg()
        main()
