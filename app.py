#!/usr/bin/env python3
# ════════════════════════════════════════════════════════════════
#   ✦ 𝐗 𝐆𝐈𝐅𝐓 ✦  —  Telegram Giveaway Bot  (Full Inline Admin)
#   v4: Full User List w/ pagination • First names • Ref counters
# ════════════════════════════════════════════════════════════════

# ── SELF-HEALING: install anything missing BEFORE imports ──
import subprocess, sys, importlib.util

def _ensure(pkg, import_name=None):
    import_name = import_name or pkg
    if importlib.util.find_spec(import_name) is None:
        print(f"📦 Installing missing package: {pkg} ...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])

for _p in ("pyTelegramBotAPI", "aiohttp"):
    _ensure(_p, "telebot" if _p == "pyTelegramBotAPI" else _p)

import os
import re
import time
import random
import sqlite3
import threading
import datetime
import html
import math

from telebot import TeleBot, types
from telebot.types import BotCommand

# ────────────────────────── CONFIG ──────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x}
DB_PATH   = os.environ.get("DB_PATH", "xgift.db")
PER_PAGE  = 10   # users per page in USER LIST

bot = TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=True)
BOT_USERNAME = ""

# ────────────────────── FANCY FONTS ─────────────────────────────
def _fontmap(up, low=None, dig=None):
    m = {ord('A') + i: chr(up + i) for i in range(26)}
    if low is not None:
        m.update({ord('a') + i: chr(low + i) for i in range(26)})
    if dig is not None:
        m.update({ord('0') + i: chr(dig + i) for i in range(10)})
    return m

_B  = _fontmap(0x1D400, 0x1D41A, 0x1D7CE)
_M  = _fontmap(0x1D670, 0x1D6AA, 0x1D7F6)
_BS = _fontmap(0x1D4D0, 0x1D4EA)

def bold(t):  return str(t).translate(_B)
def mono(t):  return str(t).translate(_M)
def fancy(t): return str(t).translate(_BS)
def ital(t):  return "<i>" + str(t) + "</i>"
def esc(t):   return html.escape(str(t))
# ⚠️ NEVER apply fonts to URLs — they break the link!

def frame(title_txt):
    return (f"╔═════「 ✦ {bold('X GIFT')} ✦ 」═════╗\n"
            f"{title_txt}\n"
            f"╚═════════════════════════╝")

# ────────────────────────── DATABASE ────────────────────────────
db_lock = threading.Lock()
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row

def q(sql, *params):
    with db_lock:
        cur = db.execute(sql, params)
        db.commit()
        return cur

def init_db():
    q("""CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY, username TEXT,
            first_name TEXT, coins INTEGER DEFAULT 0,
            banned INTEGER DEFAULT 0, last_daily TEXT,
            referrer INTEGER, join_date TEXT, refs INTEGER DEFAULT 0)""")
    q("""CREATE TABLE IF NOT EXISTS channels(
            channel_id INTEGER PRIMARY KEY, title TEXT, username TEXT)""")
    q("""CREATE TABLE IF NOT EXISTS gifts(
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT,
            cost INTEGER, hours INTEGER, end_time INTEGER,
            active INTEGER DEFAULT 1, chan_msg_id INTEGER, grp_msg_id INTEGER)""")
    q("""CREATE TABLE IF NOT EXISTS entries(
            gift_id INTEGER, user_id INTEGER, PRIMARY KEY (gift_id, user_id))""")
    q("""CREATE TABLE IF NOT EXISTS chats(
            key TEXT PRIMARY KEY, chat_id INTEGER, title TEXT)""")
    # migrations for old databases
    for col, default in (("join_date", "TEXT"), ("first_name", "TEXT"), ("refs", "INTEGER DEFAULT 0")):
        try:
            q(f"ALTER TABLE users ADD COLUMN {col} {default}")
        except Exception:
            pass

init_db()

def get_chat(key):
    r = q("SELECT * FROM chats WHERE key=?", key).fetchone()
    return dict(r) if r else None

def set_chat(key, chat_id, title):
    q("INSERT OR REPLACE INTO chats(key, chat_id, title) VALUES(?,?,?)", key, chat_id, title)

def is_admin(uid): return uid in ADMIN_IDS
def get_user(uid): return q("SELECT * FROM users WHERE user_id=?", uid).fetchone()

def get_user_by_username(username):
    return q("SELECT * FROM users WHERE username=?",
             str(username).lower().lstrip("@")).fetchone()

def ensure_user(uid, username, first_name=None):
    r = get_user(uid)
    if r:
        if username and r["username"] != username.lower():
            q("UPDATE users SET username=? WHERE user_id=?", username.lower(), uid)
        if first_name and r["first_name"] != first_name:
            q("UPDATE users SET first_name=? WHERE user_id=?", first_name, uid)
        return dict(get_user(uid)), False
    q("INSERT INTO users(user_id, username, first_name, join_date) VALUES(?,?,?,?)",
      uid, (username or "").lower(), (first_name or ""),
      datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    return dict(get_user(uid)), True

def add_coins(uid, amount):
    q("UPDATE users SET coins = coins + ? WHERE user_id=?", amount, uid)

# ─────────────────── FORCE-JOIN SYSTEM ──────────────────────────
def missing_channels(uid):
    out = []
    for ch in q("SELECT * FROM channels").fetchall():
        try:
            m = bot.get_chat_member(ch["channel_id"], uid)
            if m.status not in ("member", "administrator", "creator"):
                out.append(dict(ch))
        except Exception:
            out.append(dict(ch))
    return out

def join_kb(missing):
    kb = types.InlineKeyboardMarkup(row_width=1)
    for ch in missing:
        if ch.get("username"):
            kb.add(types.InlineKeyboardButton(f"🔗 {ch['title']}",
                    url=f"https://t.me/{ch['username']}"))
        else:
            kb.add(types.InlineKeyboardButton(f"🔗 {ch['title']}", callback_data="noop"))
    kb.add(types.InlineKeyboardButton(f"✅ {bold('I JOINED')}", callback_data="recheck"))
    return kb

# ─────────────────────── USER KEYBOARD ──────────────────────────
def user_panel_kb():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton(f"🎁 {bold('GIFTS')}",   callback_data="gifts_list"),
        types.InlineKeyboardButton(f"💎 {bold('BALANCE')}", callback_data="balance"),
    )
    kb.add(
        types.InlineKeyboardButton(f"🎯 {bold('DAILY')}",   callback_data="daily"),
        types.InlineKeyboardButton(f"👥 {bold('REFERRALS')}", callback_data="ref"),
    )
    return kb

def user_text(u):
    name = u.get("first_name") or u.get("username") or u["user_id"]
    return frame(f"\n  🎉 {bold('Welcome to X Gift!')} ✨\n") + \
        f"\n{bold('➤ User:')}  {esc(name)}\n" \
        f"{bold('➤ Coins:')}  🔷 {bold(u['coins'])}\n\n" \
        f"{fancy('Join giveaways • Earn coins • Win prizes!')} 🍀"

# ════════════════════════════════════════════════════════════════
#                        ADMIN PANEL (ALL BUTTONS)
# ════════════════════════════════════════════════════════════════
STATES = {}

def back_kb(target="ap"):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton(f"🔙 {bold('BACK TO PANEL')}", callback_data=target))
    return kb

def cancel_kb(target="ap"):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton(f"❌ {bold('CANCEL')}", callback_data="cancel"))
    kb.add(types.InlineKeyboardButton(f"🔙 {bold('BACK TO PANEL')}", callback_data=target))
    return kb

def admin_panel_kb():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton(f"🎁 {bold('CREATE GIFT')}",  callback_data="ap_newgift"),
        types.InlineKeyboardButton(f"🗂 {bold('MANAGE GIFTS')}", callback_data="ap_gifts"),
    )
    kb.add(
        types.InlineKeyboardButton(f"👥 {bold('USERS')}",        callback_data="ap_users"),
        types.InlineKeyboardButton(f"📦 {bold('SEND PRIZE')}",   callback_data="ap_prize"),
    )
    kb.add(
        types.InlineKeyboardButton(f"📢 {bold('FORCE JOIN')}",   callback_data="ap_fj"),
        types.InlineKeyboardButton(f"📌 {bold('ANNOUNCEMENTS')}", callback_data="ap_ann"),
    )
    kb.add(
        types.InlineKeyboardButton(f"📣 {bold('BROADCAST')}",    callback_data="ap_bc"),
        types.InlineKeyboardButton(f"📊 {bold('STATS')}",        callback_data="ap_stats"),
    )
    kb.add(types.InlineKeyboardButton(f"♻️ {bold('REFRESH')}",   callback_data="ap"))
    return kb

def admin_panel_text():
    gifts = q("SELECT COUNT(*) c FROM gifts WHERE active=1").fetchone()["c"]
    users = q("SELECT COUNT(*) c FROM users").fetchone()["c"]
    return frame(f"\n  🛡 {bold('ADMIN CONTROL PANEL')}\n") + \
        f"\n👤 {bold('Users:')} {bold(users)}   |   🎁 {bold('Active Gifts:')} {bold(gifts)}\n\n" \
        f"{fancy('Choose an option below, boss!')} 👑"

@bot.message_handler(commands=["admin"])
def cmd_admin(m):
    if not is_admin(m.from_user.id): return
    STATES.pop(m.from_user.id, None)
    bot.reply_to(m, admin_panel_text(), reply_markup=admin_panel_kb())

def guard(c):
    if is_admin(c.from_user.id):
        return True
    bot.answer_callback_query(c.id, "🚫 Admins only!", show_alert=True)
    return False

@bot.callback_query_handler(func=lambda c: c.data == "ap")
def cb_panel(c):
    if not guard(c): return
    STATES.pop(c.from_user.id, None)
    bot.answer_callback_query(c.id)
    try:
        bot.edit_message_text(admin_panel_text(), c.message.chat.id,
                              c.message.message_id, reply_markup=admin_panel_kb())
    except Exception:
        bot.send_message(c.from_user.id, admin_panel_text(), reply_markup=admin_panel_kb())

@bot.callback_query_handler(func=lambda c: c.data == "cancel")
def cb_cancel(c):
    if not guard(c): return
    STATES.pop(c.from_user.id, None)
    bot.answer_callback_query(c.id, "❌ Cancelled")
    try:
        bot.edit_message_text(admin_panel_text(), c.message.chat.id,
                              c.message.message_id, reply_markup=admin_panel_kb())
    except Exception:
        pass

# ───────────────── 🎁 GIFT CREATION ─────────────────
@bot.callback_query_handler(func=lambda c: c.data == "ap_newgift")
def cb_newgift(c):
    if not guard(c): return
    STATES[c.from_user.id] = {"step": "ng_title", "data": {}}
    bot.answer_callback_query(c.id)
    bot.edit_message_text(
        frame(f"\n 🎁 {bold('CREATE NEW GIFT')} — {bold('STEP 1/3')}\n") +
        f"\n{bold('➤ Send the gift title now:')}\n"
        f"{ital('e.g:')} {mono('Surfshark 1 Month Account')}\n",
        c.message.chat.id, c.message.message_id, reply_markup=cancel_kb())

# ───────────────── 🗂 MANAGE GIFTS ─────────────────
@bot.callback_query_handler(func=lambda c: c.data == "ap_gifts")
def cb_gifts_mgr(c):
    if not guard(c): return
    gifts = q("SELECT * FROM gifts WHERE active=1 ORDER BY end_time").fetchall()
    bot.answer_callback_query(c.id)
    if not gifts:
        return bot.edit_message_text(
            frame(f"\n 🗂 {bold('MANAGE GIFTS')}\n") + f"\n📭 {bold('No active giveaways.')}",
            c.message.chat.id, c.message.message_id, reply_markup=back_kb())
    kb = types.InlineKeyboardMarkup(row_width=1)
    txt = frame(f"\n 🗂 {bold('MANAGE GIFTS')}\n") + "\n"
    for g in gifts:
        left = max(0, int((g["end_time"] - time.time()) // 3600))
        joined = q("SELECT COUNT(*) c FROM entries WHERE gift_id=?", g["id"]).fetchone()["c"]
        txt += (f"\n🆔 {mono(g['id'])} | 🎁 {bold(esc(g['title']))}\n"
                f"   💰 🔷{bold(g['cost'])} | ⏰ {bold(left)}h | 👥 {bold(joined)}\n")
        kb.add(types.InlineKeyboardButton(
            f"🏁 {bold('END NOW')}  #{g['id']} — {g['title'][:18]}",
            callback_data=f"endg_{g['id']}"))
        kb.add(types.InlineKeyboardButton(
            f"🗑 {bold('DELETE')}  #{g['id']}", callback_data=f"delg_{g['id']}"))
    kb.add(types.InlineKeyboardButton(f"🔙 {bold('BACK TO PANEL')}", callback_data="ap"))
    bot.edit_message_text(txt, c.message.chat.id, c.message.message_id, reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("endg_"))
def cb_endg(c):
    if not guard(c): return
    gid = int(c.data.split("_")[1])
    g = q("SELECT * FROM gifts WHERE id=? AND active=1", gid).fetchone()
    if not g:
        return bot.answer_callback_query(c.id, "❌ Already finished!", show_alert=True)
    bot.answer_callback_query(c.id, "🏁 Ending & picking winner...")
    finish_gift(dict(g))
    bot.send_message(c.from_user.id,
        frame(f"\n 🏁 {bold('GIFT ENDED BY ADMIN')}\n") +
        f"\n🎁 {bold(esc(g['title']))}\n🏆 {ital('Winner announced publicly!')}",
        reply_markup=back_kb())

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("delg_"))
def cb_delg(c):
    if not guard(c): return
    gid = int(c.data.split("_")[1])
    q("DELETE FROM gifts WHERE id=?", gid)
    q("DELETE FROM entries WHERE gift_id=?", gid)
    bot.answer_callback_query(c.id, "🗑 Gift deleted!")
    try:
        bot.edit_message_reply_markup(c.message.chat.id, c.message.message_id, reply_markup=None)
    except Exception:
        pass

# ───────────────── 📦 SEND PRIZE ─────────────────
@bot.callback_query_handler(func=lambda c: c.data == "ap_prize")
def cb_prize(c):
    if not guard(c): return
    STATES[c.from_user.id] = {"step": "prize_target", "data": {}}
    bot.answer_callback_query(c.id)
    bot.edit_message_text(
        frame(f"\n 📦 {bold('SEND PRIZE')} — {bold('STEP 1/2')}\n") +
        f"\n{bold('➤ Send the winner:')} {mono('@username')} {ital('or')} {mono('123456789')}\n"
        f"{ital('(the prize post will be delivered to them directly)')} 🎁",
        c.message.chat.id, c.message.message_id, reply_markup=cancel_kb())

# ───────────────── 📢 FORCE JOIN ─────────────────
@bot.callback_query_handler(func=lambda c: c.data == "ap_fj")
def cb_fj(c):
    if not guard(c): return
    chans = q("SELECT * FROM channels").fetchall()
    kb = types.InlineKeyboardMarkup(row_width=1)
    txt = frame(f"\n 📢 {bold('FORCE JOIN MANAGEMENT')}\n")
    if chans:
        txt += f"\n{bold('➤ Current required channels:')}\n"
        for ch in chans:
            txt += f"   • {bold(esc(ch['title']))}\n"
            kb.add(types.InlineKeyboardButton(
                f"🗑 {bold('REMOVE')}  {ch['title'][:26]}",
                callback_data=f"delch_{ch['channel_id']}"))
    else:
        txt += f"\n{ital('No channels added yet.')}\n"
    kb.add(types.InlineKeyboardButton(f"➕ {bold('ADD CHANNEL')}", callback_data="fj_add"))
    kb.add(types.InlineKeyboardButton(f"🔙 {bold('BACK TO PANEL')}", callback_data="ap"))
    bot.answer_callback_query(c.id)
    bot.edit_message_text(txt, c.message.chat.id, c.message.message_id, reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data == "fj_add")
def cb_fj_add(c):
    if not guard(c): return
    STATES[c.from_user.id] = {"step": "fj_add"}
    bot.answer_callback_query(c.id)
    bot.edit_message_text(
        frame(f"\n ➕ {bold('ADD FORCE-JOIN CHANNEL')}\n") +
        f"\n{bold('➤ Now FORWARD any post')} from the channel here,\n"
        f"{ital('or send its @username / numeric ID:')}\n",
        c.message.chat.id, c.message.message_id, reply_markup=cancel_kb())

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("delch_"))
def cb_delch(c):
    if not guard(c): return
    q("DELETE FROM channels WHERE channel_id=?", int(c.data.split("_")[1]))
    bot.answer_callback_query(c.id, "🗑 Removed!")
    try:
        bot.edit_message_reply_markup(c.message.chat.id, c.message.message_id, reply_markup=None)
    except Exception:
        pass

# ───────────────── 📌 ANNOUNCE TARGETS ─────────────────
@bot.callback_query_handler(func=lambda c: c.data == "ap_ann")
def cb_ann(c):
    if not guard(c): return
    chan, grp = get_chat("announce_channel"), get_chat("announce_group")
    txt = frame(f"\n 📌 {bold('ANNOUNCEMENT TARGETS')}\n") + \
        f"\n📢 {bold('Channel:')}  {bold(esc(chan['title'])) if chan else ital('not set ❌')}\n" \
        f"👥 {bold('Group:')}    {bold(esc(grp['title'])) if grp else ital('not set ❌')}\n\n" \
        f"{ital('New gifts + winners are posted in BOTH & pinned in group!')} 🏆"
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton(f"📢 {bold('SET CHANNEL')}", callback_data="set_chan"))
    kb.add(types.InlineKeyboardButton(f"👥 {bold('SET GROUP')}",   callback_data="set_grp"))
    if chan:
        kb.add(types.InlineKeyboardButton(f"🗑 {bold('CLEAR CHANNEL')}", callback_data="clr_chan"))
    if grp:
        kb.add(types.InlineKeyboardButton(f"🗑 {bold('CLEAR GROUP')}", callback_data="clr_grp"))
    kb.add(types.InlineKeyboardButton(f"🔙 {bold('BACK TO PANEL')}", callback_data="ap"))
    bot.answer_callback_query(c.id)
    bot.edit_message_text(txt, c.message.chat.id, c.message.message_id, reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data in ("set_chan", "set_grp"))
def cb_set_ann(c):
    if not guard(c): return
    is_chan = c.data == "set_chan"
    STATES[c.from_user.id] = {"step": "set_chan" if is_chan else "set_grp"}
    bot.answer_callback_query(c.id)
    bot.edit_message_text(
        frame(f"\n 📌 {bold('SET ' + ('CHANNEL' if is_chan else 'GROUP'))}\n") +
        f"\n{bold('➤ FORWARD any post')} from the {'channel' if is_chan else 'group'} here,\n"
        f"{ital('or send its @username / ID:')}\n",
        c.message.chat.id, c.message.message_id, reply_markup=cancel_kb())

@bot.callback_query_handler(func=lambda c: c.data in ("clr_chan", "clr_grp"))
def cb_clr_ann(c):
    if not guard(c): return
    q("DELETE FROM chats WHERE key=?",
      "announce_channel" if c.data == "clr_chan" else "announce_group")
    bot.answer_callback_query(c.id, "🗑 Cleared!")
    cb_ann(c)

def _resolve_chat(m):
    chat = None
    if m.forward_from_chat:
        chat = m.forward_from_chat
    elif m.text and m.text.strip():
        try:
            chat = bot.get_chat(m.text.strip())
        except Exception:
            chat = None
    return chat

# ───────────────── 👥 USER MANAGEMENT ─────────────────
@bot.callback_query_handler(func=lambda c: c.data == "ap_users")
def cb_users(c):
    if not guard(c): return
    users = q("SELECT COUNT(*) c FROM users").fetchone()["c"]
    banned = q("SELECT COUNT(*) c FROM users WHERE banned=1").fetchone()["c"]
    txt = frame(f"\n 👥 {bold('USER MANAGEMENT')}\n") + \
        f"\n👤 {bold('Total users:')} {bold(users)}\n🚫 {bold('Banned:')} {bold(banned)}\n\n" \
        f"{fancy('Pick an action below:')} ⚡️"
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton(f"🔨 {bold('BAN USER')}",   callback_data="us_ban"),
        types.InlineKeyboardButton(f"🕊 {bold('UNBAN USER')}", callback_data="us_unban"),
    )
    kb.add(
        types.InlineKeyboardButton(f"➕ {bold('ADD COINS')}",    callback_data="us_coins_add"),
        types.InlineKeyboardButton(f"➖ {bold('REMOVE COINS')}", callback_data="us_coins_rem"),
    )
    kb.add(
        types.InlineKeyboardButton(f"🔍 {bold('USER INFO')}",    callback_data="us_info"),
        types.InlineKeyboardButton(f"📋 {bold('USER LIST')}",    callback_data="us_list_0"),
    )
    kb.add(types.InlineKeyboardButton(f"🔙 {bold('BACK TO PANEL')}", callback_data="ap"))
    bot.answer_callback_query(c.id)
    bot.edit_message_text(txt, c.message.chat.id, c.message.message_id, reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data in ("us_ban", "us_unban",
                                                       "us_coins_add", "us_coins_rem",
                                                       "us_info"))
def cb_user_action(c):
    if not guard(c): return
    mapping = {
        "us_ban":       ("ban_wait",   "🔨 BAN USER",
                         f"{bold('➤ Send the user:')} {mono('@username')} {ital('or')} {mono('123456789')}"),
        "us_unban":     ("unban_wait", "🕊 UNBAN USER",
                         f"{bold('➤ Send the user:')} {mono('@username')} {ital('or')} {mono('123456789')}"),
        "us_coins_add": ("coins_wait", "➕ ADD COINS",
                         f"{bold('➤ Send:')} {mono('@user +5')} {ital('or')} {mono('123456789 +50')}"),
        "us_coins_rem": ("coins_wait", "➖ REMOVE COINS",
                         f"{bold('➤ Send:')} {mono('@user -3')} {ital('or')} {mono('123456789 -10')}"),
        "us_info":      ("info_wait",  "🔍 USER INFO",
                         f"{bold('➤ Send the user:')} {mono('@username')} {ital('or')} {mono('123456789')}"),
    }
    step, title, hint = mapping[c.data]
    STATES[c.from_user.id] = {"step": step}
    bot.answer_callback_query(c.id)
    bot.edit_message_text(frame(f"\n {title}\n") + f"\n{hint}\n",
        c.message.chat.id, c.message.message_id, reply_markup=cancel_kb())

# ═══════════ 📋 FULL USER LIST WITH PAGINATION ═══════════
@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("us_list_"))
def cb_user_list(c):
    if not guard(c): return
    page = int(c.data.split("_")[2])
    total = q("SELECT COUNT(*) c FROM users").fetchone()["c"]
    pages = max(1, math.ceil(total / PER_PAGE))
    page = max(0, min(page, pages - 1))
    rows = q("SELECT * FROM users ORDER BY user_id LIMIT ? OFFSET ?",
             PER_PAGE, page * PER_PAGE).fetchall()

    txt = frame(f"\n 📋 {bold('ALL USERS')}\n") + \
        f"\n👤 {bold('Total:')} {bold(total)}  |  📄 {bold('Page:')} {bold(page + 1)}/{bold(pages)}\n" + \
        "─────────────────────\n"
    if not rows:
        txt += f"{ital('No users yet.')}"
    for i, u in enumerate(rows, start=page * PER_PAGE + 1):
        status = "🚫" if u["banned"] else "✅"
        name = u["first_name"] or "—"
        uname = f"@{u['username']}" if u["username"] else ital("no username")
        jdate = u["join_date"] or "—"
        txt += (
            f"\n{bold(f'{i}:')} {status} 👤 {bold(esc(name))}\n"
            f"     📛 {esc(uname)}\n"
            f"     🆔 {mono(u['user_id'])}\n"
            f"     💎 {bold(u['coins'])}  |  👥 {bold(u['refs'] or 0)} refs\n"
            f"     📅 {esc(jdate)}\n"
        )
    kb = types.InlineKeyboardMarkup(row_width=2)
    nav = []
    if page > 0:
        nav.append(types.InlineKeyboardButton(f"⬅️ {bold('PREV')}",
                    callback_data=f"us_list_{page - 1}"))
    else:
        nav.append(types.InlineKeyboardButton("➖", callback_data="noop"))
    if page < pages - 1:
        nav.append(types.InlineKeyboardButton(f"{bold('NEXT')} ➡️",
                    callback_data=f"us_list_{page + 1}"))
    else:
        nav.append(types.InlineKeyboardButton("➖", callback_data="noop"))
    kb.row(*nav)
    kb.add(types.InlineKeyboardButton(f"🔙 {bold('BACK TO USERS')}", callback_data="ap_users"))
    bot.answer_callback_query(c.id)
    try:
        bot.edit_message_text(txt, c.message.chat.id, c.message.message_id,
                              reply_markup=kb, disable_web_page_preview=True)
    except Exception:
        bot.send_message(c.from_user.id, txt, reply_markup=kb, disable_web_page_preview=True)

def resolve_user(text):
    t = text.strip()
    if t.startswith("@"):
        return get_user_by_username(t)
    if t.isdigit():
        return get_user(int(t))
    return None

def user_profile_text(u):
    pend = q("SELECT COUNT(*) c FROM users WHERE referrer=?", u["user_id"]).fetchone()["c"]
    gcount = q("SELECT COUNT(*) c FROM entries WHERE user_id=?", u["user_id"]).fetchone()["c"]
    name = u["first_name"] or "—"
    uname = f"@{u['username']}" if u["username"] else ital("no username")
    return frame(f"\n 🔍 {bold('USER PROFILE')}\n") + \
        f"\n👤 {bold('Name:')}  {bold(esc(name))}\n" \
        f"📛 {bold('Username:')}  {esc(uname)}\n" \
        f"🆔 {bold('ID:')}  {mono(u['user_id'])}\n" \
        f"📅 {bold('Joined:')}  {bold(u['join_date'] or 'unknown')}\n" \
        f"💎 {bold('Coins:')}  🔷 {bold(u['coins'])}\n" \
        f"{'🚫' if u['banned'] else '✅'} {bold('Status:')}  {bold('BANNED') if u['banned'] else bold('Active')}\n" \
        f"🎯 {bold('Last daily:')}  {bold(u['last_daily'] or 'never')}\n" \
        f"👥 {bold('Successful referrals:')}  👤 {bold(u['refs'] or 0)}\n" \
        f"⏳ {bold('Pending referrals:')}  👤 {bold(pend)}\n" \
        f"🎟 {bold('Gift entries:')}  {bold(gcount)}"

# ───────────────── 📣 BROADCAST ─────────────────
@bot.callback_query_handler(func=lambda c: c.data == "ap_bc")
def cb_bc(c):
    if not guard(c): return
    STATES[c.from_user.id] = {"step": "bc_wait"}
    bot.answer_callback_query(c.id)
    bot.edit_message_text(
        frame(f"\n 📣 {bold('BROADCAST TO ALL USERS')}\n") +
        f"\n{bold('➤ Now send or FORWARD')} the message you want\nto deliver to ALL users. 📨\n"
        f"{ital('(text, photo, video... anything!)')}\n",
        c.message.chat.id, c.message.message_id, reply_markup=cancel_kb())

@bot.callback_query_handler(func=lambda c: c.data == "bc_go")
def cb_bc_go(c):
    if not guard(c): return
    st = STATES.get(c.from_user.id)
    if not st or st["step"] != "bc_confirm":
        return bot.answer_callback_query(c.id, "⚠️ Expired! Start again.", show_alert=True)
    chat_id, msg_id = st["data"]["chat_id"], st["data"]["msg_id"]
    STATES.pop(c.from_user.id, None)
    bot.answer_callback_query(c.id, "📣 Broadcasting...")
    users = q("SELECT user_id FROM users WHERE banned=0").fetchall()
    status = bot.send_message(c.from_user.id, f"📣 {bold('Broadcasting...')} ⏳")
    ok = fail = 0
    for u in users:
        try:
            bot.copy_message(u["user_id"], chat_id, msg_id)
            ok += 1
        except Exception:
            fail += 1
        time.sleep(0.05)
    bot.send_message(c.from_user.id,
        frame(f"\n 📣 {bold('BROADCAST DONE')}\n") +
        f"\n✅ {bold('Delivered:')} {bold(ok)}\n❌ {bold('Failed:')} {bold(fail)}\n👤 {bold('Total:')} {bold(len(users))}",
        reply_markup=back_kb())
    try:
        bot.delete_message(c.from_user.id, status.message_id)
    except Exception:
        pass

@bot.callback_query_handler(func=lambda c: c.data == "bc_no")
def cb_bc_no(c):
    if not guard(c): return
    STATES.pop(c.from_user.id, None)
    bot.answer_callback_query(c.id, "❌ Broadcast cancelled")
    bot.edit_message_text(admin_panel_text(), c.message.chat.id,
                          c.message.message_id, reply_markup=admin_panel_kb())

# ───────────────── 📊 STATS ─────────────────
@bot.callback_query_handler(func=lambda c: c.data == "ap_stats")
def cb_stats(c):
    if not guard(c): return
    users  = q("SELECT COUNT(*) c FROM users").fetchone()["c"]
    banned = q("SELECT COUNT(*) c FROM users WHERE banned=1").fetchone()["c"]
    gifts  = q("SELECT COUNT(*) c FROM gifts WHERE active=1").fetchone()["c"]
    coins  = q("SELECT COALESCE(SUM(coins),0) c FROM users").fetchone()["c"]
    entries= q("SELECT COUNT(*) c FROM entries").fetchone()["c"]
    refs   = q("SELECT COALESCE(SUM(refs),0) c FROM users").fetchone()["c"]
    bot.answer_callback_query(c.id)
    bot.edit_message_text(
        frame(f"\n 📊 {bold('LIVE STATISTICS')}\n") +
        f"\n👤 {bold('Users:')}        {bold(users)}\n"
        f"🚫 {bold('Banned:')}       {bold(banned)}\n"
        f"🎁 {bold('Active gifts:')} {bold(gifts)}\n"
        f"🎟 {bold('Total entries:')} {bold(entries)}\n"
        f"👥 {bold('Total referrals:')} {bold(refs)}\n"
        f"💎 {bold('Coins in circulation:')} 🔷 {bold(coins)}\n",
        c.message.chat.id, c.message.message_id, reply_markup=back_kb())

# ════════════════════════════════════════════════════════════════
#               ADMIN TEXT HANDLER (STATE MACHINE)
# ════════════════════════════════════════════════════════════════
@bot.message_handler(func=lambda m: is_admin(m.from_user.id) and m.from_user.id in STATES,
                     content_types=["text", "photo", "video", "audio", "document",
                                    "animation", "sticker", "voice", "video_note"])
def admin_state(m):
    st = STATES[m.from_user.id]
    step, data = st["step"], st.get("data", {})

    if step == "ng_title":
        if not m.text: return
        data["title"] = m.text.strip()
        st["data"], st["step"] = data, "ng_cost"
        return bot.reply_to(m,
            f"✅ {bold('Title saved:')} {bold(esc(data['title']))}\n\n"
            f"💰 {bold('STEP 2/3')} — {ital('Join cost in coins (1–1000):')}\n"
            f"{ital('e.g:')} {mono('3')}", reply_markup=cancel_kb())

    if step == "ng_cost":
        if not m.text or not m.text.strip().isdigit() or not (1 <= int(m.text) <= 1000):
            return bot.reply_to(m, f"⚠️ {bold('Send a number between 1 and 1000!')}")
        data["cost"] = int(m.text)
        st["data"], st["step"] = data, "ng_hours"
        return bot.reply_to(m,
            f"✅ {bold('Cost:')} 🔷 {bold(data['cost'])} coins\n\n"
            f"⏰ {bold('STEP 3/3')} — {ital('Duration in hours:')}\n"
            f"{ital('e.g:')} {mono('12')}", reply_markup=cancel_kb())

    if step == "ng_hours":
        if not m.text or not m.text.strip().isdigit() or int(m.text) < 1:
            return bot.reply_to(m, f"⚠️ {bold('Send a valid number of hours!')}")
        data["hours"] = int(m.text)
        STATES.pop(m.from_user.id)
        end_time = int(time.time()) + data["hours"] * 3600
        cur = q("INSERT INTO gifts(title, cost, hours, end_time, active) VALUES(?,?,?,?,1)",
                data["title"], data["cost"], data["hours"], end_time)
        gid = cur.lastrowid
        bot.reply_to(m,
            frame(f"\n ✅ {bold('GIFT CREATED!')}\n") +
            f"\n🆔 {mono(gid)}\n🎁 {bold(esc(data['title']))}\n"
            f"💰 Cost: 🔷 {bold(data['cost'])}\n⏰ Duration: {bold(data['hours'])} hours\n\n"
            f"📢 {ital('Posted to channel & group!')}",
            reply_markup=back_kb())
        post_gift(gid)
        return

    if step == "fj_add":
        chat = _resolve_chat(m)
        if not chat or chat.type not in ("channel", "group", "supergroup"):
            return bot.reply_to(m, f"❌ {bold('Cannot access that chat!')}\n"
                                   f"{ital('Add the bot there first & try again.')}")
        q("INSERT OR REPLACE INTO channels(channel_id, title, username) VALUES(?,?,?)",
          chat.id, chat.title, chat.username)
        STATES.pop(m.from_user.id)
        return bot.reply_to(m,
            frame(f"\n ✅ {bold('FORCE-JOIN ADDED')}\n") +
            f"\n📢 {bold(esc(chat.title))}\n🆔 {mono(chat.id)}\n\n"
            f"{ital('All users must join before participating!')} 🔒",
            reply_markup=back_kb())

    if step in ("set_chan", "set_grp"):
        chat = _resolve_chat(m)
        if not chat or chat.type not in ("channel", "group", "supergroup"):
            return bot.reply_to(m, f"❌ {bold('Cannot access that chat!')}\n"
                                   f"{ital('Make sure the bot is inside it, then try again.')}")
        key = "announce_channel" if step == "set_chan" else "announce_group"
        set_chat(key, chat.id, chat.title or str(chat.id))
        STATES.pop(m.from_user.id)
        return bot.reply_to(m,
            frame(f"\n ✅ {bold(('CHANNEL' if step == 'set_chan' else 'GROUP') + ' SET')}\n") +
            f"\n📢 {bold(esc(chat.title or chat.id))}\n🆔 {mono(chat.id)}",
            reply_markup=back_kb())

    if step in ("ban_wait", "unban_wait"):
        if not m.text: return
        u = resolve_user(m.text)
        if not u:
            return bot.reply_to(m, f"❌ {bold('User not found in database!')}")
        banning = step == "ban_wait"
        if banning and u["user_id"] in ADMIN_IDS:
            return bot.reply_to(m, f"⚠️ {bold('You cannot ban an admin!')} 😅")
        q("UPDATE users SET banned=? WHERE user_id=?", 1 if banning else 0, u["user_id"])
        STATES.pop(m.from_user.id)
        return bot.reply_to(m,
            frame(f"\n {'🔨 BANNED' if banning else '🕊 UNBANNED'}\n") +
            f"\n👤 {bold(esc(u['first_name'] or u['username'] or u['user_id']))}\n"
            f"🆔 {mono(u['user_id'])}",
            reply_markup=back_kb())

    if step == "coins_wait":
        if not m.text: return
        parts = m.text.split()
        if len(parts) != 2 or not re.match(r"^[+-]?\d+$", parts[1]):
            return bot.reply_to(m,
                f"⚠️ {bold('Wrong format!')} {ital('Send like:')} {mono('@user +5')}")
        u = resolve_user(parts[0])
        if not u:
            return bot.reply_to(m, f"❌ {bold('User not found in database!')}")
        amount = int(parts[1])
        add_coins(u["user_id"], amount)
        STATES.pop(m.from_user.id)
        return bot.reply_to(m,
            frame(f"\n 💎 {bold('COINS UPDATED')}\n") +
            f"\n👤 {bold(esc(u['first_name'] or u['username'] or u['user_id']))}\n"
            f"🆔 {mono(u['user_id'])}\n"
            f"{'➕' if amount >= 0 else '➖'} Amount: 🔷 {bold(abs(amount))}\n"
            f"💎 New balance: 🔷 {bold(get_user(u['user_id'])['coins'])}",
            reply_markup=back_kb())

    if step == "info_wait":
        if not m.text: return
        u = resolve_user(m.text)
        if not u:
            return bot.reply_to(m, f"❌ {bold('User not found in database!')}")
        STATES.pop(m.from_user.id)
        return bot.reply_to(m, user_profile_text(dict(u)), reply_markup=back_kb())

    if step == "prize_target":
        if not m.text: return
        u = resolve_user(m.text)
        if not u:
            return bot.reply_to(m, f"❌ {bold('User not found in database!')}")
        st["step"] = "prize_content"
        st["data"] = {"target": u["user_id"]}
        return bot.reply_to(m,
            frame(f"\n 📦 {bold('SEND PRIZE')} — {bold('STEP 2/2')}\n") +
            f"\n👤 {bold('Target:')} {esc(u['first_name'] or u['username'] or u['user_id'])} "
            f"({mono(u['user_id'])})\n\n"
            f"{bold('➤ Now send the prize content:')}\n"
            f"{ital('text • photo • photo+caption • file • video • anything!')} 🎁",
            reply_markup=cancel_kb())

    if step == "prize_content":
        target = st.get("data", {}).get("target")
        if not target:
            STATES.pop(m.from_user.id)
            return bot.reply_to(m, f"⚠️ {bold('Session expired! Start again.')}",
                                reply_markup=back_kb())
        STATES.pop(m.from_user.id)
        try:
            bot.copy_message(target, m.chat.id, m.message_id)
            try:
                bot.send_message(target,
                    frame(f"\n 🎁 {bold('PRIZE DELIVERED')}\n") +
                    f"\n📦 {ital('Your prize is above!')} ⬆️\n"
                    f"🎉 {bold('Congratulations again!')} 👑")
            except Exception:
                pass
            return bot.reply_to(m,
                frame(f"\n ✅ {bold('PRIZE SENT!')}\n") +
                f"\n👤 {bold('Delivered to:')} {mono(target)}\n"
                f"📦 {ital('The user got your content + a notice!')}",
                reply_markup=back_kb())
        except Exception:
            return bot.reply_to(m,
                f"❌ {bold('Could not deliver!')} {ital('User may have blocked the bot.')}",
                reply_markup=back_kb())

    if step == "bc_wait":
        STATES[m.from_user.id] = {"step": "bc_confirm",
                                  "data": {"chat_id": m.chat.id, "msg_id": m.message_id}}
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton(f"🚀 {bold('SEND IT!')}", callback_data="bc_go"),
            types.InlineKeyboardButton(f"❌ {bold('CANCEL')}",   callback_data="bc_no"),
        )
        return bot.reply_to(m,
            f"👀 {bold('PREVIEW ABOVE')} ⬆️\n\n"
            f"{ital('This message will be sent to ALL users. Confirm?')} 📣",
            reply_markup=kb)

# ════════════════════════════════════════════════════════════════
#                        USER SIDE
# ════════════════════════════════════════════════════════════════
@bot.message_handler(commands=["start"])
def cmd_start(m):
    global BOT_USERNAME
    if not m.from_user: return
    u, created = ensure_user(m.from_user.id, m.from_user.username,
                             getattr(m.from_user, "first_name", None))
    if u["banned"]:
        return bot.reply_to(m, frame(f"\n 🚫 {bold('YOU ARE BANNED!')}"))

    ref_id = None
    if m.text and len(m.text.split()) > 1 and m.text.split()[1].startswith("ref_"):
        try: ref_id = int(m.text.split()[1][4:])
        except ValueError: ref_id = None

    if created and ref_id and ref_id != m.from_user.id:
        referrer = get_user(ref_id)
        if referrer and not referrer["banned"]:
            q("UPDATE users SET referrer=? WHERE user_id=?", ref_id, m.from_user.id)

    miss = missing_channels(m.from_user.id)
    if miss:
        return bot.reply_to(m,
            frame(f"\n 🔒 {bold('JOIN REQUIRED')} 🔒\n") +
            f"\n{bold('➤ Join these channels first to use the bot:')}\n",
            reply_markup=join_kb(miss))

    maybe_reward_referral(m.from_user.id)

    kb = user_panel_kb()
    if is_admin(m.from_user.id):
        kb.add(types.InlineKeyboardButton(f"🛡 {bold('ADMIN PANEL')}", callback_data="ap"))
    bot.reply_to(m, user_text(dict(get_user(m.from_user.id))), reply_markup=kb)

def maybe_reward_referral(uid):
    u = get_user(uid)
    if not u or not u["referrer"]: return False
    referrer = get_user(u["referrer"])
    if not referrer or referrer["banned"]: return False
    add_coins(uid, 1)
    add_coins(referrer["user_id"], 3)
    q("UPDATE users SET refs = COALESCE(refs,0) + 1 WHERE user_id=?", referrer["user_id"])
    q("UPDATE users SET referrer=NULL WHERE user_id=?", uid)
    try:
        bot.send_message(uid,
            f"🎁 {bold('Welcome!')} You got 🔷 {bold(1)} coin for joining!\n"
            f"👑 {bold('Referral reward sent to your inviter!')}")
    except Exception: pass
    try:
        bot.send_message(referrer["user_id"],
            f"🎉 {bold('New Referral!')} 👥\n"
            f"💎 You earned 🔷 {bold(3)} coins!\n"
            f"💎 New balance: 🔷 {bold(get_user(referrer['user_id'])['coins'])}")
    except Exception: pass
    return True

@bot.callback_query_handler(func=lambda c: c.data == "recheck")
def cb_recheck(c):
    miss = missing_channels(c.from_user.id)
    if miss:
        return bot.answer_callback_query(c.id, "❌ You have NOT joined all channels!", show_alert=True)
    maybe_reward_referral(c.from_user.id)
    u = get_user(c.from_user.id)
    bot.answer_callback_query(c.id, "✅ Verified!")
    kb = user_panel_kb()
    if is_admin(c.from_user.id):
        kb.add(types.InlineKeyboardButton(f"🛡 {bold('ADMIN PANEL')}", callback_data="ap"))
    bot.send_message(c.from_user.id, user_text(dict(u)), reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data == "noop")
def cb_noop(c):
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c: c.data == "balance")
def cb_balance(c):
    u = get_user(c.from_user.id)
    if u and u["banned"]:
        return bot.answer_callback_query(c.id, "🚫 You are banned!")
    bot.answer_callback_query(c.id)
    bot.send_message(c.from_user.id,
        frame(f"\n 💎 {bold('YOUR BALANCE')}\n") +
        f"\n{bold('➤ Coins:')}  🔷 {bold(u['coins'])}\n\n"
        f"{ital('Earn more: daily bonus + referrals!')} 🍀")

@bot.callback_query_handler(func=lambda c: c.data == "daily")
def cb_daily(c):
    u = get_user(c.from_user.id)
    if u and u["banned"]:
        return bot.answer_callback_query(c.id, "🚫 You are banned!")
    today = datetime.date.today().isoformat()
    if u["last_daily"] == today:
        return bot.answer_callback_query(c.id, "⏳ Already claimed today! Come back tomorrow.", show_alert=True)
    add_coins(c.from_user.id, 1)
    q("UPDATE users SET last_daily=? WHERE user_id=?", today, c.from_user.id)
    bot.answer_callback_query(c.id, "🎁 +1 Coin claimed!")
    bot.send_message(c.from_user.id,
        frame(f"\n 🎯 {bold('DAILY BONUS CLAIMED')}\n") +
        f"\n💎 Reward:  🔷 {bold(1)} coin\n"
        f"💎 Balance:  🔷 {bold(get_user(c.from_user.id)['coins'])}")

# ✅ FIXED REFERRAL — clean link, no fancy fonts, + share button
@bot.callback_query_handler(func=lambda c: c.data == "ref")
def cb_ref(c):
    u = get_user(c.from_user.id)
    if u and u["banned"]:
        return bot.answer_callback_query(c.id, "🚫 You are banned!")
    count = q("SELECT COALESCE(refs,0) c FROM users WHERE user_id=?", c.from_user.id).fetchone()["c"]
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{c.from_user.id}"
    share = f"https://t.me/share/url?url={link}&text=Join%20X%20Gift%20and%20win%20prizes!%20%F0%9F%8E%81"
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton(
        f"📤 {bold('SHARE MY LINK')}", url=share))
    bot.answer_callback_query(c.id)
    bot.send_message(c.from_user.id,
        frame(f"\n 👥 {bold('REFERRAL SYSTEM')}\n") +
        f"\n{bold('➤ Successful referrals:')}  👤 {bold(count)}\n"
        f"{bold('➤ Per referral:')}  🔷 {bold('+1')} you / 🔷 {bold('+3')} inviter\n\n"
        f"{ital('Your personal link:')} 👇\n"
        f"{link}\n\n"
        f"{ital('Tap SHARE or copy the link above!')} 🍀",
        reply_markup=kb, disable_web_page_preview=True)

@bot.callback_query_handler(func=lambda c: c.data == "gifts_list")
def cb_gifts_list(c):
    u = get_user(c.from_user.id)
    if u and u["banned"]:
        return bot.answer_callback_query(c.id, "🚫 You are banned!")
    gifts = q("SELECT * FROM gifts WHERE active=1 ORDER BY end_time").fetchall()
    bot.answer_callback_query(c.id)
    if not gifts:
        return bot.send_message(c.from_user.id,
            frame(f"\n 🎁 {bold('ACTIVE GIVEAWAYS')}\n") +
            f"\n{ital('No active giveaways right now... stay tuned!')} ⏳")
    kb = types.InlineKeyboardMarkup(row_width=1)
    txt = frame(f"\n 🎁 {bold('ACTIVE GIVEAWAYS')}\n") + "\n"
    for g in gifts:
        left = int((g["end_time"] - time.time()) // 3600) + 1
        joined = q("SELECT COUNT(*) c FROM entries WHERE gift_id=?", g["id"]).fetchone()["c"]
        txt += (f"\n🆔 {mono(g['id'])} | 🎁 {bold(esc(g['title']))}\n"
                f"   💰 Cost: 🔷 {bold(g['cost'])}  |  ⏰ {bold(left)}h left\n"
                f"   👥 Participants: {bold(joined)}\n")
        kb.add(types.InlineKeyboardButton(
            f"🔴 JOIN  #{g['id']} — {g['title'][:22]}", callback_data=f"join_{g['id']}"))
    bot.send_message(c.from_user.id, txt, reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("join_"))
def cb_join(c):
    gid = int(c.data.split("_")[1])
    u = get_user(c.from_user.id)
    if not u or u["banned"]:
        return bot.answer_callback_query(c.id, "🚫 You are banned!", show_alert=True)
    g = q("SELECT * FROM gifts WHERE id=? AND active=1", gid).fetchone()
    if not g:
        return bot.answer_callback_query(c.id, "❌ This giveaway is over!", show_alert=True)
    miss = missing_channels(c.from_user.id)
    if miss:
        bot.answer_callback_query(c.id, "🔒 Join required channels first!", show_alert=True)
        return bot.send_message(c.from_user.id, bold("🔒 Join these first:"),
                                reply_markup=join_kb(miss))
    if q("SELECT 1 FROM entries WHERE gift_id=? AND user_id=?", gid, c.from_user.id).fetchone():
        return bot.answer_callback_query(c.id, "✋ You already joined this giveaway!", show_alert=True)
    if u["coins"] < g["cost"]:
        return bot.answer_callback_query(
            c.id, f"❌ Not enough coins! Need 🔷{g['cost']}, you have 🔷{u['coins']}", show_alert=True)
    add_coins(c.from_user.id, -g["cost"])
    q("INSERT INTO entries(gift_id, user_id) VALUES(?,?)", gid, c.from_user.id)
    joined = q("SELECT COUNT(*) c FROM entries WHERE gift_id=?", gid).fetchone()["c"]
    bot.answer_callback_query(c.id, f"🎉 You joined! -{g['cost']} coins")
    bot.send_message(c.from_user.id,
        frame(f"\n ✅ {bold('JOINED SUCCESSFULLY')}\n") +
        f"\n🎁 {bold(esc(g['title']))}\n"
        f"💎 Cost paid:  🔷 {bold(g['cost'])}\n"
        f"👥 Participants:  {bold(joined)}\n"
        f"🍀 {ital('Good luck! Winner announced when it ends!')}")
    for mid, key in ((g["chan_msg_id"], "announce_channel"), (g["grp_msg_id"], "announce_group")):
        ch = get_chat(key)
        if mid and ch:
            try:
                bot.edit_message_reply_markup(ch["chat_id"], mid,
                    reply_markup=types.InlineKeyboardMarkup().add(
                        types.InlineKeyboardButton(f"🔴 {bold('PARTICIPATE')}  👥 {joined}",
                                                   callback_data=f"join_{gid}")))
            except Exception:
                pass

# ───────────────────── POST & FINISH GIFT ───────────────────────
def post_gift(gid):
    """Posts the new giveaway to BOTH channel & group"""
    g = dict(q("SELECT * FROM gifts WHERE id=?", gid).fetchone())
    joined = q("SELECT COUNT(*) c FROM entries WHERE gift_id=?", gid).fetchone()["c"]
    end = datetime.datetime.fromtimestamp(g["end_time"]).strftime("%H:%M • %d %b %Y")
    txt = frame(f"\n 🎉 {bold('NEW GIVEAWAY')} 🎉\n") + \
        f"\n🎁 {bold('Prize:')}  {bold(esc(g['title']))}\n" \
        f"💰 {bold('Join Cost:')}  🔷 {bold(g['cost'])} coins\n" \
        f"⏰ {bold('Ends at:')}  {bold(end)}  ({bold(g['hours'])}h)\n" \
        f"👥 {bold('Participants:')}  {bold(joined)}\n\n" \
        f"{ital('Tap the button below to join!')} 🍀"
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton(
        f"🔴 {bold('PARTICIPATE')}  👥 {joined}", callback_data=f"join_{gid}"))
    for key in ("announce_channel", "announce_group"):
        ch = get_chat(key)
        if ch:
            try:
                msg = bot.send_message(ch["chat_id"], txt, reply_markup=kb)
                col = "chan_msg_id" if key == "announce_channel" else "grp_msg_id"
                q(f"UPDATE gifts SET {col}=? WHERE id=?", msg.message_id, gid)
                print(f"✅ gift #{gid} posted to {key}")
            except Exception as e:
                print(f"⚠️ post_gift -> {key} failed: {e}")

def finish_gift(g):
    q("UPDATE gifts SET active=0 WHERE id=?", g["id"])
    entries = q("SELECT * FROM entries WHERE gift_id=?", g["id"]).fetchall()
    body = frame(f"\n ⏰ {bold('GIVEAWAY ENDED')}\n") + \
        f"\n🎁 {bold(esc(g['title']))}\n👥 Participants: {bold(len(entries))}\n"
    if not entries:
        body += f"\n{ital('Unfortunately, nobody joined.')} 😔"
    else:
        winner = random.choice(entries)
        wu = get_user(winner["user_id"])
        mention = (f"@{wu['username']}" if wu and wu["username"]
                   else f'<a href="tg://user?id={winner["user_id"]}">User</a>')
        body += (f"\n🏆 {bold('WINNER:')}  👑 {mention}\n\n"
                 f"🎊 {bold('Congratulations!')} Contact the admins to claim your prize! 🎊")
        try:
            bot.send_message(winner["user_id"],
                frame(f"\n 🏆 {bold('YOU WON!')} 🏆\n") +
                f"\n🎁 {bold(esc(g['title']))}\n"
                f"👑 {ital('You are the lucky winner!')}\n\n"
                f"📦 {bold('Your prize information will be sent to you soon!')}\n"
                f"🔔 {ital('Keep your inbox open...')} ✨")
        except Exception: pass
    chan, grp = get_chat("announce_channel"), get_chat("announce_group")
    if chan:
        try: bot.send_message(chan["chat_id"], body)
        except Exception: pass
    if grp:
        try:
            msg = bot.send_message(grp["chat_id"], body)
            bot.pin_chat_message(grp["chat_id"], msg.message_id, disable_notification=False)
        except Exception: pass
    q("DELETE FROM gifts WHERE id=?", g["id"])
    q("DELETE FROM entries WHERE gift_id=?", g["id"])

def scheduler():
    while True:
        try:
            rows = q("SELECT * FROM gifts WHERE active=1 AND end_time<=?",
                     int(time.time())).fetchall()
            for g in rows:
                try:
                    finish_gift(dict(g))
                except Exception as e:
                    print("finish_gift error:", e)
        except Exception as e:
            print("scheduler error:", e)
        time.sleep(15)

# ─────────────────────────── MAIN ───────────────────────────────
if __name__ == "__main__":
    if not BOT_TOKEN:
        raise SystemExit("❌ Set BOT_TOKEN environment variable!")
    me = bot.get_me()
    BOT_USERNAME = me.username

    # 🔵 Blue /START command menu next to the chat input
    try:
        bot.set_my_commands([
            BotCommand("start", "🏠 Open Main Menu"),
            BotCommand("admin", "🛡 Admin Panel"),
        ])
        print("✅ Bot commands menu registered")
    except Exception as e:
        print("⚠️ set_my_commands failed:", e)

    threading.Thread(target=scheduler, daemon=True).start()
    print("✦ X GIFT BOT IS RUNNING ✦")
    bot.infinity_polling(timeout=30, long_polling_timeout=35, skip_pending=True)
