"""
Админ-бот (отдельный Telegram-бот) для TGBOT.
Запускается в том же процессе, что и основной бот (фоновый поток).
Доступ только у ADMIN_USER_ID. Использует ту же базу.
Команды: /users, /user <id>, /grant <id>, /revoke <id>, /stats
"""
import os
import time
from datetime import datetime, timedelta

import requests
import psycopg2
from psycopg2.extras import RealDictCursor

ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")  # строкой
DATABASE_URL = os.getenv("DATABASE_URL")
BOT_TOKEN = os.getenv("BOT_TOKEN")  # основной бот — для уведомления пользователя
WEBAPP_URL = os.getenv("WEBAPP_URL", "")
SUB_DAYS = 30

API = f"https://api.telegram.org/bot{ADMIN_BOT_TOKEN}"


def notify_user_premium(target_id):
    """Шлёт пользователю поздравление о Премиуме через основной бот-токен."""
    if not BOT_TOKEN:
        return
    text = (
        "🎉 Поздравляем! Вам открыт Премиум-доступ ✦\n\n"
        "Теперь вам доступно:\n"
        "◦ Персональный план по волосам на неделю\n"
        "◦ Чат с экспертом\n"
        "◦ Безлимитный (в рамках месяца) анализ фото и косметики\n"
        "◦ Общая база косметики в приложении\n\n"
        "Откройте приложение командой /app — желаем красивых волос ✦"
    )
    markup = None
    if WEBAPP_URL:
        markup = {"inline_keyboard": [[{"text": "Открыть приложение ✦", "web_app": {"url": WEBAPP_URL}}]]}
    try:
        payload = {"chat_id": target_id, "text": text}
        if markup:
            payload["reply_markup"] = markup
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                      json=payload, verify=VERIFY, timeout=15)
    except Exception as e:
        print(f"notify_user_premium error: {e}")


def resolve_target(arg):
    """Принимает id или @username, возвращает user_id из базы или None."""
    arg = arg.strip()
    if arg.startswith("@"):
        uname = arg[1:]
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id FROM users WHERE lower(username) = lower(%s)", (uname,))
                row = cur.fetchone()
                return row["user_id"] if row else None
    try:
        return int(arg)
    except ValueError:
        return None

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
VERIFY = False


def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def tg(method, data=None):
    try:
        return requests.post(f"{API}/{method}", json=data, verify=VERIFY, timeout=20).json()
    except Exception as e:
        print(f"admin tg error: {e}")
        return {}


def send(chat_id, text, markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if markup:
        payload["reply_markup"] = markup
    tg("sendMessage", payload)


def is_admin(user_id):
    return ADMIN_USER_ID and str(user_id) == str(ADMIN_USER_ID)


def fmt_sub(u):
    if not u.get("is_subscribed"):
        return "нет"
    exp = u.get("sub_expires")
    try:
        if isinstance(exp, str):
            exp = datetime.fromisoformat(exp)
        if exp and exp < datetime.utcnow():
            return "истекла"
        return f"до {exp.strftime('%d.%m.%Y')}" if exp else "активна"
    except Exception:
        return "активна"


def cmd_users(chat_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, username, first_name, is_subscribed, sub_expires, mode "
                        "FROM users ORDER BY created_at")
            rows = cur.fetchall()
    if not rows:
        send(chat_id, "Пользователей пока нет.")
        return
    lines = [f"👥 Пользователи: {len(rows)}", ""]
    for u in rows:
        handle = f"@{u['username']}" if u.get("username") else "—"
        lines.append(
            f"• {u.get('first_name') or '—'} ({handle})\n"
            f"  id: {u['user_id']} · режим: {u.get('mode') or '—'} · подписка: {fmt_sub(u)}"
        )
    send(chat_id, "\n".join(lines))


def cmd_user(chat_id, target_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE user_id = %s", (target_id,))
            u = cur.fetchone()
            if not u:
                send(chat_id, "Пользователь не найден.")
                return
            cur.execute("SELECT COUNT(*) AS c FROM user_cosmetics WHERE user_id = %s", (target_id,))
            cos_count = cur.fetchone()["c"]
    handle = f"@{u['username']}" if u.get("username") else "—"
    lines = [
        f"👤 {u.get('first_name') or '—'} ({handle})",
        f"id: {u['user_id']}",
        f"режим: {u.get('mode') or '—'}",
        f"подписка: {fmt_sub(u)} · использовано действий: {u.get('sub_actions_used') or 0}",
        "",
        "Профиль:",
        f"— цель: {u.get('goal') or '—'}",
        f"— проблема: {u.get('problem') or '—'}",
        f"— возраст: {u.get('age') or '—'} · пол: {u.get('gender') or '—'}",
        f"— город: {u.get('city') or '—'}, {u.get('country') or '—'}",
        f"— мытьё: {u.get('wash_frequency') or '—'} · укладка: {u.get('styling_time') or '—'}",
        f"— скан косметики: {cos_count}",
    ]
    if u.get("hair_analysis"):
        lines += ["", "Волосы:", u["hair_analysis"]]
    send(chat_id, "\n".join(lines),
         markup={"inline_keyboard": [[
             {"text": "Выдать Премиум", "callback_data": f"agrant_{u['user_id']}"},
             {"text": "Снять", "callback_data": f"arevoke_{u['user_id']}"},
         ]]})


def grant(target_id):
    now = datetime.utcnow()
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET is_subscribed=TRUE, sub_start=%s, sub_expires=%s, sub_actions_used=0 "
                "WHERE user_id=%s",
                (now.isoformat(), (now + timedelta(days=SUB_DAYS)).isoformat(), target_id)
            )
            updated = cur.rowcount
            conn.commit()
    return updated


def revoke(target_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET is_subscribed=FALSE, sub_expires=NULL WHERE user_id=%s", (target_id,))
            updated = cur.rowcount
            conn.commit()
    return updated


def cmd_ideas(chat_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT username, first_name, text, created_at FROM ideas "
                        "ORDER BY created_at DESC LIMIT 30")
            rows = cur.fetchall()
    if not rows:
        send(chat_id, "Идей пока нет.")
        return
    lines = [f"💡 Идеи: {len(rows)}", ""]
    for r in rows:
        handle = f"@{r['username']}" if r.get("username") else "—"
        when = r["created_at"].strftime("%d.%m %H:%M") if r.get("created_at") else ""
        lines.append(f"• {r.get('first_name') or '—'} ({handle}) · {when}\n  {r.get('text') or ''}")
    send(chat_id, "\n".join(lines))


def _norm_name(name):
    """Нормализация названия для поиска дублей (как в bot.py)."""
    import re
    s = (name or "").lower().replace("ё", "е")
    s = re.sub(r"[^\wа-я0-9 ]", " ", s)
    words = [w for w in s.split() if w not in ("от", "by", "из")]
    return " ".join(sorted(words))


def _name_words(name):
    """Множество значимых слов имени (без стоп-слов и описательных)."""
    import re
    s = (name or "").lower().replace("ё", "е")
    s = re.sub(r"[^\wа-я0-9 ]", " ", s)
    stop = {"от", "by", "из", "for", "the", "and", "с", "и", "delightful", "honey",
            "bloom", "fresh", "aroma", "scent", "ml", "мл", "объем", "объём"}
    return {w for w in s.split() if w and w not in stop and not w.isdigit()}


def _same_product(a, b):
    """Считает два названия одним средством, если значимые слова одного — подмножество другого
    (и пересечение содержательное, минимум 2 общих слова или полное вхождение короткого)."""
    wa, wb = _name_words(a), _name_words(b)
    if not wa or not wb:
        return False
    if wa == wb:
        return True
    inter = wa & wb
    small = wa if len(wa) <= len(wb) else wb
    big = wb if small is wa else wa
    # короткое имя целиком входит в длинное → один продукт
    if small.issubset(big):
        return len(inter) >= 2 or len(small) >= 2
    return False


def cmd_dedupe(chat_id):
    """Чистит дубли в общей базе products. Сначала точная группировка по нормализованному
    имени, затем слияние «почти-дублей» по вхождению значимых слов (разная длина названий)."""
    removed = 0
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, product_name, summary FROM products ORDER BY id")
            rows = cur.fetchall()
    # формируем группы: каждая новая запись либо примыкает к существующей группе, либо создаёт новую
    groups = []  # список списков rows
    for r in rows:
        placed = False
        for g in groups:
            if _same_product(r["product_name"], g[0]["product_name"]):
                g.append(r)
                placed = True
                break
        if not placed:
            groups.append([r])

    to_delete = []
    kept_lines = []
    for g in groups:
        if len(g) < 2:
            continue
        # оставляем запись с заполненным summary и самым коротким названием
        g_sorted = sorted(
            g, key=lambda x: (0 if (x.get("summary") or "").strip() else 1, len(x["product_name"] or ""))
        )
        keep = g_sorted[0]
        kept_lines.append(f"✓ {keep['product_name']}")
        for r in g_sorted[1:]:
            to_delete.append(r["id"])

    if to_delete:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM products WHERE id = ANY(%s)", (to_delete,))
                removed = cur.rowcount
                conn.commit()

    if removed:
        send(chat_id, f"🧹 Удалено дублей в общей базе: {removed}\n\nОставлены:\n" + "\n".join(kept_lines))
    else:
        send(chat_id, "Дублей в общей базе не найдено ✓")


def cmd_stats(chat_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM users")
            total = cur.fetchone()["c"]
            cur.execute("SELECT COUNT(*) AS c FROM users WHERE is_subscribed=TRUE AND sub_expires > NOW()")
            subs = cur.fetchone()["c"]
            cur.execute("SELECT COUNT(*) AS c FROM users WHERE quiz_done=TRUE")
            quiz_done = cur.fetchone()["c"]
            cur.execute("SELECT COUNT(*) AS c FROM user_cosmetics")
            cosmetics = cur.fetchone()["c"]
            cur.execute("SELECT COUNT(*) AS c FROM products")
            products = cur.fetchone()["c"]
    send(chat_id,
         "📊 Статистика\n\n"
         f"Пользователей: {total}\n"
         f"С активной подпиской: {subs}\n"
         f"Прошли квиз: {quiz_done}\n"
         f"Сканов косметики (личных): {cosmetics}\n"
         f"Продуктов в общей базе: {products}")


def handle_update(update):
    if "callback_query" in update:
        cq = update["callback_query"]
        uid = cq["from"]["id"]
        chat_id = cq["message"]["chat"]["id"]
        data = cq.get("data", "")
        tg("answerCallbackQuery", {"callback_query_id": cq["id"]})
        if not is_admin(uid):
            return
        if data.startswith("agrant_"):
            tid = int(data.split("_")[1])
            n = grant(tid)
            if n:
                notify_user_premium(tid)
                send(chat_id, "✓ Премиум выдан на 30 дней. Пользователь уведомлён.")
            else:
                send(chat_id, "Не найдено.")
        elif data.startswith("arevoke_"):
            tid = int(data.split("_")[1])
            n = revoke(tid)
            send(chat_id, "✓ Премиум снят." if n else "Не найдено.")
        return

    msg = update.get("message", {})
    if not msg:
        return
    uid = msg.get("from", {}).get("id")
    chat_id = msg.get("chat", {}).get("id")
    text = (msg.get("text") or "").strip()
    if not uid or not chat_id:
        return

    if not is_admin(uid):
        send(chat_id, "⛔ Доступ запрещён.")
        return

    cmd = text.split()[0].split("@")[0] if text.startswith("/") else ""
    arg = text.split()[1] if len(text.split()) > 1 else None

    if cmd in ("/start", "/help"):
        send(chat_id,
             "🔐 Админ-панель TGBOT\n\n"
             "/users — список пользователей и статусы\n"
             "/user <id|@логин> — карточка пользователя\n"
             "/grant <id|@логин> — выдать Премиум на 30 дней\n"
             "/revoke <id|@логин> — снять Премиум\n"
             "/ideas — идеи от пользователей\n"
             "/stats — общая статистика\n"
             "/dedupe — удалить дубли в общей базе косметики")
    elif cmd == "/users":
        cmd_users(chat_id)
    elif cmd == "/ideas":
        cmd_ideas(chat_id)
    elif cmd == "/stats":
        cmd_stats(chat_id)
    elif cmd == "/dedupe":
        cmd_dedupe(chat_id)
    elif cmd == "/user":
        if not arg:
            send(chat_id, "Использование: /user <id|@логин>")
        else:
            tid = resolve_target(arg)
            if tid is None:
                send(chat_id, "Пользователь не найден. Укажите id или @логин из /users.")
            else:
                cmd_user(chat_id, tid)
    elif cmd == "/grant":
        if not arg:
            send(chat_id, "Использование: /grant <id|@логин>\nСписок — /users")
        else:
            tid = resolve_target(arg)
            if tid is None:
                send(chat_id, "Пользователь не найден. Укажите id или @логин из /users.")
            else:
                n = grant(tid)
                if n:
                    notify_user_premium(tid)
                    send(chat_id, "✓ Премиум выдан на 30 дней. Пользователь уведомлён.")
                else:
                    send(chat_id, "Пользователь не найден.")
    elif cmd == "/revoke":
        if not arg:
            send(chat_id, "Использование: /revoke <id|@логин>")
        else:
            tid = resolve_target(arg)
            if tid is None:
                send(chat_id, "Пользователь не найден. Укажите id или @логин из /users.")
            else:
                n = revoke(tid)
                send(chat_id, "✓ Премиум снят." if n else "Пользователь не найден.")
    else:
        send(chat_id, "Неизвестная команда. /help — список команд.")


def run_admin_bot():
    if not ADMIN_BOT_TOKEN:
        print("ADMIN_BOT_TOKEN not set — admin bot disabled")
        return
    print("Admin bot started...")
    offset = None
    while True:
        try:
            params = {"timeout": 30, "allowed_updates": ["message", "callback_query"]}
            if offset:
                params["offset"] = offset
            resp = requests.get(f"{API}/getUpdates", params=params, timeout=35, verify=VERIFY)
            for update in resp.json().get("result", []):
                offset = update["update_id"] + 1
                try:
                    handle_update(update)
                except Exception as e:
                    print(f"admin handle error: {e}")
        except Exception as e:
            print(f"admin loop error: {e}")
            time.sleep(3)


if __name__ == "__main__":
    run_admin_bot()
