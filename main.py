import os
import json
import time
import threading
import uuid
import sqlite3
import base64
import random
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
import telebot
from telebot import types

# --- VARIABILI D'AMBIENTE E CHIAVI HARDCODED ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '8723865245:AAFuEqkoFCDy2VP0g9H1cF9vs6MZzoGqxV0').strip()
WEB_APP_URL = os.environ.get('WEB_APP_URL', '').strip()
try:
    ADMIN_ID = int(os.environ.get('ADMIN_ID', 8647927043))
except:
    ADMIN_ID = 8647927043

# --- CONFIGURAZIONE LOCALE BOSTON GEORGE ---
DB_PATH = "/root/boston_bot/boston.db"
MEDIA_DIR = "/var/www/html/boston_media"
os.makedirs(MEDIA_DIR, exist_ok=True)
GIVEAWAY_DB = '/root/boston_bot/giveaway_data.json'

bot = telebot.TeleBot(TELEGRAM_TOKEN)
user_states = {}

# ==========================================
# OPSEC: TRACKER SPAZZINO E NOTIFICHE
# ==========================================
def track_msg(user_id, msg_id):
    """Salva l'ID dei messaggi generati nelle liste per poterli cancellare dopo."""
    user_states.setdefault(user_id, {}).setdefault("tracked", []).append(msg_id)

def clear_tracked(user_id):
    """Incenerisce all'istante tutti i messaggi della lista quando clicchi Torna Indietro."""
    state = user_states.get(user_id, {})
    for m_id in state.get("tracked", []):
        try: bot.delete_message(user_id, m_id)
        except: pass
    state["tracked"] = []

def send_admin_notification(chat_id, text, delay=300):
    """Invia notifiche (Ordini/Ticket) che si vaporizzano dopo 5 minuti (300 secondi)."""
    try:
        msg = bot.send_message(chat_id, text, parse_mode="HTML")
        def delete_task():
            try: bot.delete_message(chat_id, msg.message_id)
            except: pass
        t = threading.Timer(delay, delete_task)
        t.daemon = True
        t.start()
    except: pass

def reset_panel_and_notify(user_id, success_text):
    """Mostra un avviso di successo per 5 secondi e resetta la dashboard in sicurezza."""
    clear_tracked(user_id)
    state = user_states.setdefault(user_id, {})
    state["step"] = None
    
    if success_text:
        try:
            temp_msg = bot.send_message(user_id, success_text, parse_mode="HTML")
            threading.Timer(5, lambda: bot.delete_message(user_id, temp_msg.message_id)).start()
        except: pass

    panel_id = state.get("panel_id")
    success = False
    if panel_id:
        try:
            bot.edit_message_text("⚙️ <b>PANNELLO GESTIONALE BOSTON GEORGE</b>\n\nScegli la sezione da gestire:", user_id, panel_id, parse_mode="HTML", reply_markup=get_admin_main_keyboard())
            success = True
        except: pass
    if not success:
        try:
            sent = bot.send_message(user_id, "⚙️ <b>PANNELLO GESTIONALE BOSTON GEORGE</b>\n\nScegli la sezione da gestire:", parse_mode="HTML", reply_markup=get_admin_main_keyboard())
            state["panel_id"] = sent.message_id
        except: pass

# ==========================================
# GESTIONE DATABASE SQLITE
# ==========================================
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (telegram_id INTEGER PRIMARY KEY, username TEXT, points INTEGER DEFAULT 50, trophies TEXT DEFAULT '[]')''')
    c.execute('''CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, category TEXT, description TEXT, price_options TEXT, media_list TEXT, media_url TEXT, media_type TEXT, in_showcase INTEGER DEFAULT 1, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, items TEXT, total_price REAL, address TEXT, status TEXT, tracking_code TEXT, user_message_id INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

init_db()

def db_register_user(user_id, username):
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO users (telegram_id, username, points, trophies) VALUES (?, ?, ?, ?)", (user_id, username or "Anonimo", 50, '[]'))
    conn.commit()
    conn.close()

def db_add_product(product_data):
    try:
        conn = get_db()
        conn.execute('''INSERT INTO products (name, category, description, price_options, media_list, media_url, media_type, in_showcase) VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', (product_data.get('name'), product_data.get('category'), product_data.get('description'), json.dumps(product_data.get('price_options', [])), json.dumps(product_data.get('media_list', [])), product_data.get('media_url'), product_data.get('media_type'), 1 if product_data.get('in_showcase', True) else 0))
        conn.commit()
        conn.close()
        return True, "OK"
    except Exception as e: return False, str(e)

def db_update_product(prod_id, update_data):
    try:
        conn = get_db()
        for key, val in update_data.items():
            if isinstance(val, list) or isinstance(val, dict): val = json.dumps(val)
            elif isinstance(val, bool): val = 1 if val else 0
            conn.execute(f"UPDATE products SET {key} = ? WHERE id = ?", (val, prod_id))
        conn.commit()
        conn.close()
        return True
    except: return False

def db_get_products():
    conn = get_db()
    rows = conn.execute("SELECT * FROM products ORDER BY id ASC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def db_toggle_product(prod_id, current_status):
    return db_update_product(prod_id, {"in_showcase": not current_status})

def db_delete_product(prod_id):
    conn = get_db()
    conn.execute("DELETE FROM products WHERE id = ?", (prod_id,))
    conn.commit()
    conn.close()
    return True

def db_update_user_points(target_id, points_delta):
    conn = get_db()
    row = conn.execute("SELECT points FROM users WHERE telegram_id = ?", (target_id,)).fetchone()
    if row:
        new_p = max(0, row['points'] + points_delta)
        conn.execute("UPDATE users SET points = ? WHERE telegram_id = ?", (new_p, target_id))
        conn.commit()
        conn.close()
        return True, new_p
    conn.close()
    return False, 0

def db_add_user_trophy(target_id, trophy_name):
    conn = get_db()
    row = conn.execute("SELECT trophies FROM users WHERE telegram_id = ?", (target_id,)).fetchone()
    if row:
        try: trophies = json.loads(row['trophies'])
        except: trophies = []
        if trophy_name not in trophies:
            trophies.append(trophy_name)
            conn.execute("UPDATE users SET trophies = ? WHERE telegram_id = ?", (json.dumps(trophies), target_id))
            conn.commit()
            conn.close()
            return True, trophies
    conn.close()
    return False, []

def db_save_order(user_id, username, cart, total, address):
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT INTO orders (user_id, username, items, total_price, address, status) VALUES (?, ?, ?, ?, ?, ?)''', (user_id, username, json.dumps(cart), total, address, "PENDING"))
    order_id = c.lastrowid
    conn.commit()
    conn.close()
    return order_id

def db_get_all_orders():
    conn = get_db()
    rows = conn.execute("SELECT * FROM orders ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def db_update_order_status(order_id, status, tracking=""):
    conn = get_db()
    if tracking: conn.execute("UPDATE orders SET status = ?, tracking_code = ? WHERE id = ?", (status, tracking, order_id))
    else: conn.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
    conn.commit()
    conn.close()

def db_get_giveaway():
    if not os.path.exists(GIVEAWAY_DB): return {"is_active": False, "description": "🎁 Evento Esclusivo", "prize": "N/D", "end_date": "Da definire", "participants": {}}
    try:
        with open(GIVEAWAY_DB, 'r') as f: return json.load(f)
    except: return {"is_active": False, "description": "🎁 Evento Esclusivo", "prize": "N/D", "end_date": "Da definire", "participants": {}}

def db_update_giveaway(payload):
    gw = db_get_giveaway()
    gw.update(payload)
    with open(GIVEAWAY_DB, 'w') as f: json.dump(gw, f)
    return True

def upload_to_local_storage(file_bytes, mime_type, file_extension):
    try:
        filename = f"media_{int(time.time())}_{uuid.uuid4().hex[:6]}.{file_extension}"
        filepath = os.path.join(MEDIA_DIR, filename)
        with open(filepath, 'wb') as f: f.write(file_bytes)
        return f"{WEB_APP_URL}/boston_media/{filename}", "OK"
    except Exception as e: return None, str(e)

# ==========================================
# SERVER API REST
# ==========================================
class WebhookAPIHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        
        if '/api/products' in self.path:
            all_prods = db_get_products()
            showcase_prods = [p for p in all_prods if p.get('in_showcase', 1) == 1]
            self.wfile.write(json.dumps(showcase_prods).encode('utf-8'))
        elif self.path.startswith('/api/user/'):
            user_id_str = self.path.split('/')[-1]
            try: user_id = int(user_id_str.replace("ID_", ""))
            except: user_id = user_id_str
            conn = get_db()
            row = conn.execute("SELECT points, trophies FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
            conn.close()
            if row: 
                try: tr = json.loads(row['trophies'])
                except: tr = []
                self.wfile.write(json.dumps({"points": row['points'], "trophies": tr}).encode('utf-8'))
            else: self.wfile.write(json.dumps({"points": 50, "trophies": []}).encode('utf-8'))
        elif '/api/giveaway' in self.path:
            g = db_get_giveaway()
            resp = {"is_active": g.get("is_active", False), "description": g.get("description", ""), "prize": g.get("prize", ""), "end_date": g.get("end_date", ""), "participants_count": len(g.get("participants", {}))}
            self.wfile.write(json.dumps(resp).encode('utf-8'))
        else: self.wfile.write(b'{"status": "ok"}')

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length == 0:
            self.send_response(400)
            self.end_headers()
            return
            
        post_data = self.rfile.read(content_length).decode('utf-8')
        try: data = json.loads(post_data)
        except: data = {}

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

        if self.path == '/api/upload':
            b64_str = data.get("data", "")
            if "," in b64_str: b64_str = b64_str.split(",")[1]
            try:
                img_data = base64.b64decode(b64_str)
                filename = f"receipt_{int(time.time())}_{uuid.uuid4().hex[:6]}.jpg"
                filepath = os.path.join(MEDIA_DIR, filename)
                with open(filepath, 'wb') as f: f.write(img_data)
                public_url = f"{WEB_APP_URL}/boston_media/{filename}"
                self.wfile.write(json.dumps({"url": public_url}).encode('utf-8'))
            except Exception as e: self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
            return
            
        elif self.path == '/api/giveaway/join':
            user_id = str(data.get('id', ''))
            username = data.get('username', 'Anonimo')
            g = db_get_giveaway()
            if not g.get("is_active"):
                self.wfile.write(json.dumps({"success": False, "error": "Evento chiuso al momento."}).encode('utf-8'))
                return
            if user_id in g.get("participants", {}):
                self.wfile.write(json.dumps({"success": False, "error": "Sei già iscritto a questo evento!"}).encode('utf-8'))
                return
            g.setdefault("participants", {})[user_id] = username
            db_update_giveaway(g)
            self.wfile.write(json.dumps({"success": True}).encode('utf-8'))
            return

        elif self.path == '/api/order':
            cart = data.get("cart", [])
            total = data.get("total", 0)
            user_id = data.get("user_id")
            username = data.get("username", "Anonimo")
            address = data.get("address", "Non specificato")

            order_id = db_save_order(user_id, username, cart, total, address)

            # ASSEGNAZIONE AUTOMATICA 50 PUNTI PER ORDINE
            if user_id and str(user_id) != "0":
                db_update_user_points(int(user_id), 50)

            items_text = "\n".join([f"• {i['qty']}x {i['name']} - €{i['price']}" for i in cart])

            user_msg = (
                f"✅ Richiesta #{order_id} inviata al negozio!\n\n"
                f"{items_text}\n"
                f"📍 Indirizzo / Ritrovo: {address}\n"
                f"Totale indicativo: €{total}\n\n"
                "🎁 Hai guadagnato 50 Punti VIP per questo ordine!\n"
                "Un operatore prenderà in carico la tua richiesta a breve."
            )
            if user_id and str(user_id) != "0":
                try: bot.send_message(int(user_id), user_msg)
                except: pass

            # NOTIFICA ADMIN AUTODISTRUGGENTE (5 MINUTI)
            if ADMIN_ID and ADMIN_ID != 0:
                admin_msg = f"🚨 NUOVO ORDINE RICEVUTO! #{order_id}\n\n👤 Utente: @{username} (ID: {user_id})\n📍 Indirizzo / Ritrovo: {address}\n\n📦 Prodotti:\n{items_text}\n\n💰 Totale: €{total}"
                send_admin_notification(ADMIN_ID, admin_msg, delay=300)
                
            self.wfile.write(json.dumps({"success": True, "order_id": order_id}).encode('utf-8'))

def run_health_server():
    # BOSTON GEORGE OPERA SU PORTA 8081 PER NON CONFLITTARE CON IL FALSARIO
    port = int(os.environ.get("PORT", 8081))
    server = HTTPServer(('0.0.0.0', port), WebhookAPIHandler)
    server.serve_forever()

# --- TASTIERE GESTIONALI ---
def get_admin_main_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("📦 Gestione Prodotti & Media", callback_data="m_prod"),
        types.InlineKeyboardButton("🛒 Gestione Ordini Ricevuti", callback_data="m_ord"),
        types.InlineKeyboardButton("📜 Storico Completo Ordini", callback_data="m_hist"),
        types.InlineKeyboardButton("🏆 Punti & Trofei Utenti", callback_data="m_pts"),
        types.InlineKeyboardButton("🎁 Gestione Giveaway", callback_data="m_gw")
    )
    return markup

def get_admin_prod_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("➕ Aggiungi Prodotto", callback_data="p_add"),
        types.InlineKeyboardButton("📋 Lista / Modifica / Elimina", callback_data="p_list"),
        types.InlineKeyboardButton("🔙 Torna al Menu Principale", callback_data="m_main")
    )
    return markup

def get_cancel_keyboard():
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 Torna al Menu", callback_data="m_main"))
    return markup

def get_media_done_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("✅ Fine Caricamento Media", callback_data="done_media"),
        types.InlineKeyboardButton("🔙 Annulla e Torna al Menu", callback_data="m_main")
    )
    return markup

# --- COMANDI UTENTE NORMALE ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.chat.id
    try: bot.delete_message(user_id, message.message_id)
    except: pass
    username = message.from_user.username
    threading.Thread(target=db_register_user, args=(user_id, username), daemon=True).start()

    welcome_text = (
        "👋 Benvenuti nello shop di Boston George 420!\n\n"
        "Qui troverete tutti i prodotti ideali per voi o per il vostro business.\n\n"
        "📦 Tutti i PRODOTTI sono in pronta consegna\n"
        "🤝 Consegna a mano disponibile\n\n"
        "🚚 Spedizioni da:\n"
        "🇮🇹 Italia | 🇪🇸 Spagna | 🇳🇱 Olanda | 🇺🇸 USA\n\n"
        "🛍️ Cliccate in basso per aprire la vetrina!"
    )

    markup = types.InlineKeyboardMarkup()
    if WEB_APP_URL:
        markup.add(types.InlineKeyboardButton("🛍 Apri la vetrina", web_app=types.WebAppInfo(WEB_APP_URL)))
    bot.send_message(user_id, welcome_text, reply_markup=markup)

# --- COMANDI AMMINISTRATORE ---
@bot.message_handler(commands=['admin', 'cancel', 'menu'])
def admin_panel(message):
    user_id = message.chat.id
    if user_id != ADMIN_ID: return
    try: bot.delete_message(user_id, message.message_id)
    except: pass
    
    clear_tracked(user_id)
    state = user_states.setdefault(user_id, {})
    state["step"] = None
    
    try:
        sent = bot.send_message(user_id, "⚙️ <b>PANNELLO GESTIONALE BOSTON GEORGE</b>\n\nScegli la sezione da gestire:", parse_mode="HTML", reply_markup=get_admin_main_keyboard())
        state["panel_id"] = sent.message_id
    except: pass

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    user_id = call.message.chat.id
    if user_id != ADMIN_ID: return
    data = call.data
    state = user_states.setdefault(user_id, {})

    # PULIZIA LISTE (SPAZZINO) QUANDO NAVIGHI VIA DAL PANNELLO
    if data in ["m_main", "m_gw", "m_ord", "m_hist", "m_pts", "m_prod", "p_add", "p_list"]:
        state["panel_id"] = call.message.message_id
        clear_tracked(user_id)

    if data == "m_main":
        clear_tracked(user_id)
        state["step"] = None
        panel_id = state.get("panel_id")
        success = False
        if panel_id:
            try:
                bot.edit_message_text("⚙️ <b>PANNELLO GESTIONALE BOSTON GEORGE</b>\n\nScegli la sezione da gestire:", user_id, panel_id, parse_mode="HTML", reply_markup=get_admin_main_keyboard())
                success = True
            except: pass
        if not success:
            try:
                sent = bot.send_message(user_id, "⚙️ <b>PANNELLO GESTIONALE BOSTON GEORGE</b>\n\nScegli la sezione da gestire:", parse_mode="HTML", reply_markup=get_admin_main_keyboard())
                state["panel_id"] = sent.message_id
            except: pass

    elif data == "m_gw":
        gw = db_get_giveaway()
        st_val = gw.get("is_active", False)
        status = "🟢 ATTIVO" if st_val else "🔴 INATTIVO"
        msg = f"🎁 GESTIONE GIVEAWAY\n\nStato: {status}\nPremio in Palio: {gw.get('prize', 'N/D')}\nDescrizione: {gw.get('description', 'N/D')}\nScadenza: {gw.get('end_date', 'N/D')}\nIscritti Totali: {len(gw.get('participants', {}))}"
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(f"👁️ Cambia Stato (Attiva/Disattiva)", callback_data=f"gw_tog_{not st_val}"),
            types.InlineKeyboardButton("🏆 Imposta Premio", callback_data="gw_prize"),
            types.InlineKeyboardButton("📝 Imposta Descrizione", callback_data="gw_desc"),
            types.InlineKeyboardButton("⏳ Imposta Scadenza", callback_data="gw_date"),
            types.InlineKeyboardButton("🔙 Torna al Menu", callback_data="m_main")
        )
        bot.edit_message_text(msg, user_id, call.message.message_id, reply_markup=markup)

    elif data.startswith("gw_tog_"):
        new_st = data.split("_")[2] == 'True'
        db_update_giveaway({"is_active": new_st})
        bot.answer_callback_query(call.id, "✅ Stato Giveaway Aggiornato!")
        call.data = "m_gw"
        handle_callbacks(call)

    elif data == "gw_prize":
        state["step"] = "WAITING_GW_PRIZE"
        try:
            sent = bot.send_message(user_id, "🏆 Scrivi il nuovo PREMIO in palio per il Giveaway:", reply_markup=get_cancel_keyboard())
            track_msg(user_id, sent.message_id)
        except: pass
    elif data == "gw_desc":
        state["step"] = "WAITING_GW_DESC"
        try:
            sent = bot.send_message(user_id, "📝 Scrivi la nuova DESCRIZIONE (es. Partecipa all'estrazione esclusiva):", reply_markup=get_cancel_keyboard())
            track_msg(user_id, sent.message_id)
        except: pass
    elif data == "gw_date":
        state["step"] = "WAITING_GW_DATE"
        try:
            sent = bot.send_message(user_id, "⏳ Scrivi la SCADENZA (es. 25 Dicembre 2026):", reply_markup=get_cancel_keyboard())
            track_msg(user_id, sent.message_id)
        except: pass

    elif data == "m_ord":
        orders = [o for o in db_get_all_orders() if o.get('status') == 'PENDING']
        bot.edit_message_text("🛒 <b>GESTIONE ORDINI IN ATTESA</b>\n\nGli ordini arrivano in chat in tempo reale.", user_id, call.message.message_id, parse_mode="HTML")
        if not orders:
            try:
                sent = bot.send_message(user_id, "✅ Nessun ordine in attesa al momento.", reply_markup=get_cancel_keyboard())
                track_msg(user_id, sent.message_id)
            except: pass
            return
        
        for o in orders:
            items = json.loads(o.get('items', '[]')) if isinstance(o.get('items'), str) else o.get('items', [])
            items_str = "\n".join([f"  • {i['name']} ({i['qty']}) - €{i['price']}" for i in items]) if items else "  • Nessun dettaglio"
            
            card_msg = f"🛒 ORDINE #{o.get('id')}\n👤 Utente: @{o.get('username')} (ID: {o.get('user_id')})\n📍 Indirizzo: {o.get('address', 'N/D')}\n\n📦 Prodotti:\n{items_str}\n\n💰 Totale: €{o.get('total_price')}"
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("✅ Accetta", callback_data=f"ord_acc_{o['id']}_{o.get('user_id')}"),
                types.InlineKeyboardButton("❌ Annulla", callback_data=f"ord_cnc_{o['id']}_{o.get('user_id')}"),
                types.InlineKeyboardButton("🚚 Invia Tracking", callback_data=f"ord_trk_{o['id']}_{o.get('user_id')}")
            )
            try:
                sent = bot.send_message(user_id, card_msg, reply_markup=markup)
                track_msg(user_id, sent.message_id)
            except: pass
        try:
            sent = bot.send_message(user_id, "👇 Fine ordini in attesa:", reply_markup=get_cancel_keyboard())
            track_msg(user_id, sent.message_id)
        except: pass

    elif data == "m_hist":
        orders = db_get_all_orders()
        bot.edit_message_text("📜 STORICO COMPLETO ORDINI", user_id, call.message.message_id)
        if not orders:
            try:
                sent = bot.send_message(user_id, "📭 Nessun ordine presente nello storico.", reply_markup=get_cancel_keyboard())
                track_msg(user_id, sent.message_id)
            except: pass
            return
        
        status_map = {"PENDING": "⏳ In Attesa", "ACCEPTED": "✅ Confermato", "SHIPPED": "🚚 Spedito", "CANCELLED": "❌ Annullato"}
        for o in orders[:20]:
            st = status_map.get(o.get('status'), o.get('status'))
            items = json.loads(o.get('items', '[]')) if isinstance(o.get('items'), str) else o.get('items', [])
            items_str = "\n".join([f"  • {i['name']} ({i['qty']}) - €{i['price']}" for i in items]) if items else "  • Nessun dettaglio"
            
            card_msg = f"🛒 ORDINE #{o.get('id')}\n👤 Utente: @{o.get('username')}\n📍 Indirizzo: {o.get('address', 'N/D')}\n📌 Stato: {st}\n🚚 Tracking: {o.get('tracking_code', 'N/D')}\n\n📦 Prodotti:\n{items_str}\n\n💰 Totale: €{o.get('total_price')}"
            try:
                sent = bot.send_message(user_id, card_msg)
                track_msg(user_id, sent.message_id)
            except: pass
                
        try:
            sent = bot.send_message(user_id, "👇 Fine dello storico ordini:", reply_markup=get_cancel_keyboard())
            track_msg(user_id, sent.message_id)
        except: pass

    elif data == "m_pts":
        msg = "🏆 GESTIONE PUNTI & TROFEI\n\n• Assegna Punti:\n<code>/punti ID_UTENTE QUANTITA</code>\n\n• Assegna Trofeo:\n<code>/trofeo ID_UTENTE NOME_TROFEO</code>"
        bot.edit_message_text(msg, user_id, call.message.message_id, parse_mode="HTML", reply_markup=get_cancel_keyboard())

    elif data == "m_prod":
        bot.edit_message_text("📦 GESTIONE PRODOTTI & MEDIA\n\nCosa desideri fare?", user_id, call.message.message_id, reply_markup=get_admin_prod_keyboard())

    elif data == "p_add":
        markup = types.InlineKeyboardMarkup(row_width=1)
        cats = ["🤝 Roma (Meet Up)", "🤝 Fondi (Meet Up)", "🤝 Terracina (Meet Up)", "🇮🇹 Italia (Ship)", "🇪🇸 Spagna (Ship)", "🇳🇱 Olanda (Ship)", "🇺🇸 USA (Ship)"]
        markup.add(*[types.InlineKeyboardButton(c, callback_data=f"addcat_{c}") for c in cats])
        markup.add(types.InlineKeyboardButton("🔙 Torna al Menu Principale", callback_data="m_main"))
        bot.edit_message_text("Seleziona la categoria del prodotto:", user_id, call.message.message_id, reply_markup=markup)

    elif data.startswith("addcat_"):
        cat = data.replace("addcat_", "")
        state.update({"category": cat, "step": "WAITING_MEDIA", "media_list": []})
        bot.edit_message_text(f"Categoria: {cat}\n\n📸 Invia ORA una o più Foto/Video del prodotto.\n\nPuoi inviarne quanti ne vuoi. Quando hai finito, premi ✅ Fine Caricamento Media in basso.", user_id, call.message.message_id, reply_markup=get_media_done_keyboard())

    elif data == "p_list":
        prods = db_get_products()
        bot.edit_message_text("📋 <b>LISTA PRODOTTI IN VETRINA</b>", user_id, call.message.message_id, parse_mode="HTML")
        if not prods:
            try:
                sent = bot.send_message(user_id, "📭 Nessun prodotto presente nel database.", reply_markup=get_cancel_keyboard())
                track_msg(user_id, sent.message_id)
            except: pass
            return
            
        for p in prods:
            st_val = p.get('in_showcase', True)
            status_str = '🟢 In Vetrina' if st_val else '🔴 Nascosto'
            msg = f"📦 {p.get('name')}\n🏷 Categoria: {p.get('category')}\n👁 Stato: {status_str}"
            
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("👁️ On/Off", callback_data=f"tog_{p['id']}_{st_val}"),
                types.InlineKeyboardButton("✏️ Modifica", callback_data=f"edit_{p['id']}")
            )
            markup.add(types.InlineKeyboardButton("🗑️ Elimina", callback_data=f"del_{p['id']}"))
            try:
                sent = bot.send_message(user_id, msg, reply_markup=markup)
                track_msg(user_id, sent.message_id)
            except: pass
            
        try:
            sent = bot.send_message(user_id, "👇 Opzioni di navigazione:", reply_markup=get_cancel_keyboard())
            track_msg(user_id, sent.message_id)
        except: pass

    elif data.startswith("edit_"):
        p_id = data.split("_")[1]
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("✏️ Modifica Nome", callback_data=f"edname_{p_id}"),
            types.InlineKeyboardButton("📝 Modifica Descrizione", callback_data=f"eddesc_{p_id}"),
            types.InlineKeyboardButton("💰 Modifica Prezzi", callback_data=f"edprc_{p_id}"),
            types.InlineKeyboardButton("📸 Sostituisci Foto/Video", callback_data=f"edmedia_{p_id}"),
            types.InlineKeyboardButton("🔙 Torna al Menu Principale", callback_data="m_main")
        )
        clear_tracked(user_id)
        bot.edit_message_text("Cosa vuoi modificare di questo prodotto?", user_id, call.message.message_id, reply_markup=markup)

    elif data.startswith("edname_"):
        p_id = data.split("_")[1]
        state.update({"step": "EDIT_NAME", "target_product": p_id})
        try:
            sent = bot.send_message(user_id, "✏️ Scrivi il NUOVO NOME per questo prodotto:", reply_markup=get_cancel_keyboard())
            track_msg(user_id, sent.message_id)
        except: pass

    elif data.startswith("eddesc_"):
        p_id = data.split("_")[1]
        state.update({"step": "EDIT_DESC", "target_product": p_id})
        try:
            sent = bot.send_message(user_id, "📝 Scrivi la NUOVA DESCRIZIONE per questo prodotto:", reply_markup=get_cancel_keyboard())
            track_msg(user_id, sent.message_id)
        except: pass

    elif data.startswith("edprc_"):
        p_id = data.split("_")[1]
        state.update({"step": "EDIT_PRICES", "target_product": p_id})
        try:
            sent = bot.send_message(user_id, "💰 Scrivi le NUOVE VARIANTI DI PREZZO.\nEsempio: 10g - 50, 25g - 100", reply_markup=get_cancel_keyboard())
            track_msg(user_id, sent.message_id)
        except: pass

    elif data.startswith("edmedia_"):
        p_id = data.split("_")[1]
        state.update({"step": "WAITING_MEDIA_EDIT", "target_product": p_id, "media_list": []})
        try:
            sent = bot.send_message(user_id, "📸 Invia ORA le nuove foto o video (questo cancellerà quelle vecchie).\nPremi Fine quando hai caricato tutto.", reply_markup=get_media_done_keyboard())
            track_msg(user_id, sent.message_id)
        except: pass

    elif data.startswith("tog_"):
        parts = data.split("_")
        p_id = parts[1]
        curr_st = parts[2] == 'True'
        new_st = not curr_st
        
        if db_toggle_product(p_id, curr_st):
            bot.answer_callback_query(call.id, "✅ Stato aggiornato con successo!")
            msg_text = call.message.text
            if "🟢 In Vetrina" in msg_text: new_text = msg_text.replace("🟢 In Vetrina", "🔴 Nascosto")
            elif "🔴 Nascosto" in msg_text: new_text = msg_text.replace("🔴 Nascosto", "🟢 In Vetrina")
            else: new_text = msg_text

            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("👁️ On/Off", callback_data=f"tog_{p_id}_{new_st}"),
                types.InlineKeyboardButton("✏️ Modifica", callback_data=f"edit_{p_id}")
            )
            markup.add(types.InlineKeyboardButton("🗑️ Elimina", callback_data=f"del_{p_id}"))
            try: bot.edit_message_text(new_text, user_id, call.message.message_id, reply_markup=markup)
            except: pass

    elif data.startswith("del_"):
        p_id = data.split("_")[1]
        if db_delete_product(p_id):
            bot.answer_callback_query(call.id, "🗑️ Prodotto eliminato definitivamente!")
            try: bot.delete_message(user_id, call.message.message_id)
            except: pass

    elif data == "done_media":
        if not state.get("media_list"):
            bot.answer_callback_query(call.id, "❌ Invia almeno un file multimediale prima di continuare!", show_alert=True)
            return
        
        if state.get("step") == "WAITING_MEDIA":
            state["step"] = "WAITING_NAME"
            try:
                sent = bot.send_message(user_id, f"✅ Hai caricato {len(state['media_list'])} file!\n\n📝 Ora invia il NOME del prodotto:", reply_markup=get_cancel_keyboard())
                track_msg(user_id, sent.message_id)
            except: pass
        elif state.get("step") == "WAITING_MEDIA_EDIT":
            p_id = state["target_product"]
            media_list = state["media_list"]
            first_url = media_list[0]["url"] if media_list else ""
            first_type = media_list[0]["type"] if media_list else "image"
            
            db_update_product(p_id, {"media_list": media_list, "media_url": first_url, "media_type": first_type})
            reset_panel_and_notify(user_id, "✅ Foto/Video aggiornati con successo nel prodotto!")

    elif data.startswith("ord_acc_"):
        parts = data.split("_")
        o_id, u_id = parts[2], parts[3]
        db_update_order_status(o_id, "ACCEPTED")
        if u_id and u_id != "0":
            try: bot.send_message(int(u_id), f"✅ Il tuo ordine #{o_id} è stato confermato dal venditore!")
            except: pass
        bot.answer_callback_query(call.id, "✅ Ordine Accettato e cliente avvisato!")
        call.data = "m_ord"
        handle_callbacks(call)

    elif data.startswith("ord_cnc_"):
        parts = data.split("_")
        o_id, u_id = parts[2], parts[3]
        db_update_order_status(o_id, "CANCELLED")
        if u_id and u_id != "0":
            try: bot.send_message(int(u_id), f"❌ Attenzione: Il tuo ordine #{o_id} è stato annullato.")
            except: pass
        bot.answer_callback_query(call.id, "❌ Ordine Annullato!")
        call.data = "m_ord"
        handle_callbacks(call)

    elif data.startswith("ord_trk_"):
        parts = data.split("_")
        o_id, u_id = parts[2], parts[3]
        state.update({"step": "WAITING_TRACKING", "target_order": o_id, "target_user": u_id})
        clear_tracked(user_id)
        try:
            sent = bot.send_message(user_id, f"🚚 Invia ora il Codice di Tracking per l'Ordine #{o_id}:", reply_markup=get_cancel_keyboard())
            track_msg(user_id, sent.message_id)
        except: pass

@bot.message_handler(content_types=['photo', 'video'])
def handle_media(message):
    user_id = message.chat.id
    if user_id != ADMIN_ID: return
    
    try: bot.delete_message(user_id, message.message_id)
    except: pass
    
    state = user_states.get(user_id, {})
    if state.get("step") not in ["WAITING_MEDIA", "WAITING_MEDIA_EDIT"]: return

    try:
        wait_msg = bot.send_message(user_id, "⏳ Salvataggio sul Server Locale in corso...")
        track_msg(user_id, wait_msg.message_id)
    except: wait_msg = None

    if message.photo:
        file_id = message.photo[-1].file_id
        media_type, mime, ext = 'image', 'image/jpeg', 'jpg'
    else:
        if message.video.file_size > 20 * 1024 * 1024:
            if wait_msg:
                try: bot.edit_message_text("❌ IL VIDEO PESA PIÙ DI 20MB. Telegram lo blocca. Comprimilo.", user_id, wait_msg.message_id)
                except: pass
            return
        file_id = message.video.file_id
        media_type, mime, ext = 'video', 'video/mp4', 'mp4'

    try:
        file_info = bot.get_file(file_id)
        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_info.file_path}"
        file_bytes = requests.get(file_url).content
        
        public_url, err = upload_to_local_storage(file_bytes, mime, ext)
        if public_url:
            if "media_list" not in user_states[user_id]: user_states[user_id]["media_list"] = []
            user_states[user_id]["media_list"].append({"url": public_url, "type": media_type})
            tot = len(user_states[user_id]["media_list"])
            if wait_msg:
                try: bot.edit_message_text(f"✅ Salvato Localmente!\n📸 Media #{tot} aggiunto.\nContinua o premi Fine.", user_id, wait_msg.message_id, reply_markup=get_media_done_keyboard())
                except: pass
        else:
            if wait_msg:
                try: bot.edit_message_text(f"❌ ERRORE SERVER:\n{err}", user_id, wait_msg.message_id)
                except: pass
    except Exception as e:
        if wait_msg:
            try: bot.edit_message_text(f"❌ Errore scaricamento: {e}", user_id, wait_msg.message_id)
            except: pass

@bot.message_handler(func=lambda m: m.chat.id == ADMIN_ID)
def handle_admin_text(message):
    user_id = message.chat.id
    
    try: bot.delete_message(user_id, message.message_id)
    except: pass
    
    state = user_states.get(user_id, {})
    step = state.get("step")

    if message.text and message.text.startswith("/punti"):
        try:
            parts = message.text.split()
            target_user = int(parts[1])
            qty = int(parts[2])
            ok, new_total = db_update_user_points(target_user, qty)
            if ok:
                reset_panel_and_notify(user_id, f"✅ Operazione completata! L'utente {target_user} ora ha {new_total} punti.")
                try: bot.send_message(target_user, f"🎉 Complimenti! Hai ricevuto {qty} punti.")
                except: pass
            else: reset_panel_and_notify(user_id, "❌ Errore: Utente non trovato.")
        except: reset_panel_and_notify(user_id, "❌ Formato errato. Usa: /punti ID_UTENTE QUANTITA")
        return

    if message.text and message.text.startswith("/trofeo"):
        try:
            parts = message.text.split(" ", 2)
            target_user = int(parts[1])
            trophy_name = parts[2]
            ok, _ = db_add_user_trophy(target_user, trophy_name)
            if ok:
                reset_panel_and_notify(user_id, f"✅ Trofeo '{trophy_name}' assegnato con successo all'utente {target_user}!")
                try: bot.send_message(target_user, f"🥇 NUOVO TROFEO SBLOCCATO: {trophy_name}!\nControlla la bacheca nell'App.")
                except: pass
            else: reset_panel_and_notify(user_id, "❌ Errore: Utente non trovato.")
        except: reset_panel_and_notify(user_id, "❌ Formato errato. Usa: /trofeo ID_UTENTE NOME_TROFEO")
        return

    if step == "WAITING_GW_PRIZE":
        db_update_giveaway({"prize": message.text})
        reset_panel_and_notify(user_id, "✅ Premio aggiornato con successo!")
        return
    elif step == "WAITING_GW_DESC":
        db_update_giveaway({"description": message.text})
        reset_panel_and_notify(user_id, "✅ Descrizione aggiornata con successo!")
        return
    elif step == "WAITING_GW_DATE":
        db_update_giveaway({"end_date": message.text})
        reset_panel_and_notify(user_id, "✅ Scadenza aggiornata con successo!")
        return

    if step == "EDIT_NAME":
        db_update_product(state["target_product"], {"name": message.text})
        reset_panel_and_notify(user_id, "✅ Nome aggiornato con successo!")
        return

    elif step == "EDIT_DESC":
        db_update_product(state["target_product"], {"description": message.text})
        reset_panel_and_notify(user_id, "✅ Descrizione aggiornata con successo!")
        return

    elif step == "EDIT_PRICES":
        try:
            clean_text = message.text.replace("–", "-").replace("—", "-").replace("):", "").replace(")", "").strip()
            raw_variants = clean_text.split(",")
            prices = []
            for r in raw_variants:
                if "-" in r:
                    qty = r.split("-")[0].strip()
                    price_str = r.split("-")[1].replace("€", "").strip()
                    prices.append({"qty": qty, "price": float(price_str)})
            if not prices: raise ValueError("Nessun formato valido.")
            db_update_product(state["target_product"], {"price_options": prices})
            reset_panel_and_notify(user_id, "✅ Prezzi aggiornati con successo!")
        except:
            clear_tracked(user_id)
            try:
                sent = bot.send_message(user_id, "❌ Formato errato. Esempio corretto: 10g - 50, 25g - 100", reply_markup=get_cancel_keyboard())
                track_msg(user_id, sent.message_id)
            except: pass
        return

    elif step == "WAITING_NAME":
        state["name"] = message.text
        state["step"] = "WAITING_DESC"
        clear_tracked(user_id)
        try:
            sent = bot.send_message(user_id, "✍️ Nome salvato. Ora invia la DESCRIZIONE del prodotto.\n(Puoi scrivere username come @ilboston per renderli cliccabili nell'app):", reply_markup=get_cancel_keyboard())
            track_msg(user_id, sent.message_id)
        except: pass

    elif step == "WAITING_DESC":
        state["desc"] = message.text
        state["step"] = "WAITING_PRICES"
        clear_tracked(user_id)
        try:
            sent = bot.send_message(user_id, "💰 Ultimo step. Invia i PREZZI e le VARIANTI (Esempio: 10g - 50, 25g - 100):", reply_markup=get_cancel_keyboard())
            track_msg(user_id, sent.message_id)
        except: pass

    elif step == "WAITING_PRICES":
        try:
            clean_text = message.text.replace("–", "-").replace("—", "-").replace("):", "").replace(")", "").strip()
            raw_variants = clean_text.split(",")
            prices = []
            for r in raw_variants:
                if "-" in r:
                    qty = r.split("-")[0].strip()
                    price_str = r.split("-")[1].replace("€", "").strip()
                    prices.append({"qty": qty, "price": float(price_str)})
            if not prices: raise ValueError("Nessun formato valido.")
        except:
            clear_tracked(user_id)
            try:
                sent = bot.send_message(user_id, "❌ Formato non riconosciuto.\nEsempio corretto: 10g - 50, 25g - 100", reply_markup=get_cancel_keyboard())
                track_msg(user_id, sent.message_id)
            except: pass
            return

        media_list = state.get("media_list", [])
        first_url = media_list[0]["url"] if media_list else ""
        first_type = media_list[0]["type"] if media_list else "image"

        payload = {
            "name": state["name"], "category": state["category"],
            "media_list": media_list, "media_url": first_url,
            "media_type": first_type,
            "price_options": prices, "description": state.get("desc", ""), "in_showcase": True
        }
        success, err_msg = db_add_product(payload)
        if success: reset_panel_and_notify(user_id, f"🎉 PRODOTTO PUBBLICATO IN VETRINA!\n📦 Nome: {state['name']}")
        else: reset_panel_and_notify(user_id, f"❌ ERRORE DATABASE:\n{err_msg}")

    elif step == "WAITING_TRACKING":
        tracking_code = message.text.strip()
        order_id = state["target_order"]
        target_user = state["target_user"]
        db_update_order_status(order_id, "SHIPPED", tracking_code)
        if target_user and str(target_user) != "0":
            try: bot.send_message(int(target_user), f"🚚 IL TUO ORDINE #{order_id} È STATO SPEDITO!\n\nCodice Tracking: {tracking_code}")
            except: pass
        reset_panel_and_notify(user_id, f"✅ Codice di Tracking per l'ordine #{order_id} inviato al cliente!")

print("🤖 Avvio Bot Boston George (SERVER LOCALE API + DB) in corso...")
while True:
    try:
        bot.remove_webhook()
        time.sleep(2)
        bot.infinity_polling(skip_pending=True, timeout=20, long_polling_timeout=20)
    except Exception as e:
        print(f"Errore di connessione: {e}. Riavvio...")
        time.sleep(5)
