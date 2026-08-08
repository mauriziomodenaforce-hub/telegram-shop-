import os
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
import telebot
from telebot import types

# --- VARIABILI D'AMBIENTE ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '').strip()
WEB_APP_URL = os.environ.get('WEB_APP_URL', '').strip()
SUPABASE_URL = os.environ.get('SUPABASE_URL', '').strip().rstrip('/')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', '').strip()
ADMIN_ID = int(os.environ.get('ADMIN_ID', 0))

bot = telebot.TeleBot(TELEGRAM_TOKEN)
user_states = {}

# --- HELPER SUPABASE REST API ---
def get_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

def db_register_user(user_id, username):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    url = f"{SUPABASE_URL}/rest/v1/users"
    headers = get_headers()
    headers["Prefer"] = "resolution=merge-duplicates"
    data = {"telegram_id": user_id, "username": username or "Anonimo", "points": 50}
    try:
        requests.post(url, headers=headers, json=data)
    except Exception as e:
        print(f"Errore registrazione: {e}")

def db_add_product(product_data):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False, "Mancano SUPABASE_URL o SUPABASE_KEY su Render."
    url = f"{SUPABASE_URL}/rest/v1/products"
    try:
        r = requests.post(url, headers=get_headers(), json=product_data)
        if r.status_code in [200, 201]:
            return True, "OK"
        else:
            return False, f"HTTP {r.status_code}: {r.text}"
    except Exception as e:
        return False, str(e)

def db_get_products():
    url = f"{SUPABASE_URL}/rest/v1/products?select=*&order=created_at.desc"
    try:
        r = requests.get(url, headers=get_headers())
        return r.json() if r.status_code == 200 else []
    except Exception:
        return []

def db_toggle_product(prod_id, current_status):
    url = f"{SUPABASE_URL}/rest/v1/products?id=eq.{prod_id}"
    try:
        r = requests.patch(url, headers=get_headers(), json={"in_showcase": not current_status})
        return r.status_code in [200, 204]
    except Exception:
        return False

def db_delete_product(prod_id):
    url = f"{SUPABASE_URL}/rest/v1/products?id=eq.{prod_id}"
    try:
        r = requests.delete(url, headers=get_headers())
        return r.status_code in [200, 204]
    except Exception:
        return False

def db_save_order(user_id, username, cart, total):
    url = f"{SUPABASE_URL}/rest/v1/orders"
    data = {
        "user_id": user_id,
        "username": username or "Anonimo",
        "items": cart,
        "total_price": total,
        "status": "PENDING"
    }
    try:
        r = requests.post(url, headers=get_headers(), json=data)
        if r.status_code in [200, 201]:
            res = r.json()
            return res[0]["id"] if isinstance(res, list) and len(res) > 0 else 999
    except Exception as e:
        print(f"Errore salvataggio ordine: {e}")
    return 999

def db_update_order_status(order_id, status, tracking=""):
    url = f"{SUPABASE_URL}/rest/v1/orders?id=eq.{order_id}"
    payload = {"status": status}
    if tracking:
        payload["tracking_code"] = tracking
    try:
        r = requests.patch(url, headers=get_headers(), json=payload)
        return r.status_code in [200, 204]
    except Exception:
        return False

def db_update_user_points(target_id, points_delta):
    url = f"{SUPABASE_URL}/rest/v1/users?telegram_id=eq.{target_id}"
    try:
        r = requests.get(url, headers=get_headers())
        if r.status_code == 200 and len(r.json()) > 0:
            current_p = r.json()[0].get("points", 0)
            new_p = max(0, current_p + points_delta)
            requests.patch(url, headers=get_headers(), json={"points": new_p})
            return True, new_p
    except Exception as e:
        print(f"Errore punti: {e}")
    return False, 0

# --- SERVER API ED ORDINI ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(b"Bot & Admin Panel 100% Active")

    def do_POST(self):
        if self.path == '/api/order':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))

            cart = data.get("cart", [])
            total = data.get("total", 0)
            user_id = data.get("user_id")
            username = data.get("username", "Anonimo")

            order_id = db_save_order(user_id, username, cart, total)

            # Notifica all'Utente
            if user_id:
                try:
                    bot.send_message(
                        user_id,
                        f"🎉 **Ordine #{order_id} Inviato con Successo!**\n\n"
                        f"Totale: **€{total}**\n"
                        "Un operatore prenderà in carico la tua richiesta a breve."
                    )
                except Exception as e:
                    print(f"Errore notifica utente: {e}")

            # Notifica all'Admin
            items_text = "\n".join([f"• {i['name']} ({i['qty']}) - €{i['price']}" for i in cart])
            admin_msg = (
                f"🚨 **NUOVO ORDINE RICEVUTO! #{order_id}**\n\n"
                f"👤 Utente: @{username} (`{user_id}`)\n"
                f"📦 Prodotti:\n{items_text}\n\n"
                f"💰 **Totale: €{total}**"
            )

            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("✅ Accetta", callback_data=f"ord_acc_{order_id}_{user_id}"),
                types.InlineKeyboardButton("❌ Annulla", callback_data=f"ord_cnc_{order_id}_{user_id}"),
                types.InlineKeyboardButton("🚚 Invia Tracking", callback_data=f"ord_trk_{order_id}_{user_id}")
            )
            try:
                bot.send_message(ADMIN_ID, admin_msg, parse_mode='Markdown', reply_markup=markup)
            except Exception as e:
                print(f"Errore notifica admin: {e}")

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "order_id": order_id}).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_health_server, daemon=True).start()

# --- MENU ADMIN ---
def get_admin_main_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("📦 Gestione Prodotti & Media", callback_data="m_prod"),
        types.InlineKeyboardButton("🛒 Gestione Ordini", callback_data="m_ord"),
        types.InlineKeyboardButton("🏆 Punti & Trofei Utenti", callback_data="m_pts"),
        types.InlineKeyboardButton("🎨 Grafica & Info Shop", callback_data="m_cfg")
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

# --- COMANDI USER ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.chat.id
    username = message.from_user.username
    db_register_user(user_id, username)

    welcome_text = (
        "👋 **Benvenuti nello shop di Boston George 420!**\n\n"
        "Qui troverete tutti i prodotti ideali per voi o per il vostro business.\n\n"
        "📦 Tutti i PRODOTTI sono in pronta consegna\n"
        "🤝 Consegna a mano disponibile\n\n"
        "🚚 **Spedizioni da:**\n"
        "🇮🇹 Italia | 🇪🇸 Spagna | 🇳🇱 Olanda |\n"
        "🇺🇸 USA\n\n"
        "🛍️ Cliccate in basso per aprire la vetrina!"
    )

    markup = types.InlineKeyboardMarkup()
    if WEB_APP_URL:
        btn = types.InlineKeyboardButton("🛍 Apri la vetrina", web_app=types.WebAppInfo(WEB_APP_URL))
        markup.add(btn)

    bot.send_message(user_id, welcome_text, parse_mode='Markdown', reply_markup=markup)

# --- COMANDO ADMIN ---
@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if message.chat.id != ADMIN_ID:
        bot.reply_to(message, "⛔️ Accesso negato. Pannello riservato all'Amministratore.")
        return

    bot.send_message(
        message.chat.id,
        "⚙️ **PANNELLO GESTIONALE AMMINISTRATORE**\n\nScegli la sezione da gestire:",
        parse_mode='Markdown',
        reply_markup=get_admin_main_keyboard()
    )

# --- CALLBACK QUERY HANDLER ---
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    user_id = call.message.chat.id
    if user_id != ADMIN_ID:
        return

    data = call.data

    if data == "m_main":
        bot.edit_message_text("⚙️ **PANNELLO GESTIONALE AMMINISTRATORE**", user_id, call.message.message_id, parse_mode='Markdown', reply_markup=get_admin_main_keyboard())

    elif data == "m_prod":
        bot.edit_message_text("📦 **GESTIONE PRODOTTI & MEDIA**\n\nCosa desideri fare?", user_id, call.message.message_id, parse_mode='Markdown', reply_markup=get_admin_prod_keyboard())

    elif data == "m_ord":
        bot.edit_message_text("🛒 **GESTIONE ORDINI**\n\nGli ordini ricevuti dalla Web App compaiono direttamente qui in chat con i tasti di accettazione e invio tracking.", user_id, call.message.message_id, parse_mode='Markdown', reply_markup=get_admin_main_keyboard())

    elif data == "m_pts":
        bot.send_message(user_id, "🏆 **GESTIONE PUNTI UTENTE**\n\nPer assegnare o rimuovere punti invia un messaggio con questo formato:\n`/punti ID_UTENTE QUANTITA`\n*(Esempio: `/punti 12345678 100`)*", parse_mode='Markdown')

    elif data == "m_cfg":
        bot.send_message(user_id, "🎨 **GRAFICA & INFO SHOP**\n\nPuoi personalizzare il video del banner e il logo sostituendo i link in `index.html` su GitHub.", parse_mode='Markdown')

    elif data == "p_add":
        markup = types.InlineKeyboardMarkup(row_width=2)
        cats = ["🇮🇹 Italia", "🇪🇸 Spagna", "🇳🇱 Olanda", "🇺🇸 USA"]
        btns = [types.InlineKeyboardButton(c, callback_data=f"addcat_{c}") for c in cats]
        markup.add(*btns)
        bot.edit_message_text("Seleziona la **categoria** per il nuovo prodotto:", user_id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)

    elif data.startswith("addcat_"):
        cat = data.replace("addcat_", "")
        user_states[user_id] = {"category": cat, "step": "WAITING_MEDIA"}
        bot.edit_message_text(f"Categoria scelta: **{cat}**\n\n📸 Ora invia la **Foto o Video** del prodotto.", user_id, call.message.message_id, parse_mode='Markdown')

    elif data == "p_list":
        prods = db_get_products()
        if not prods:
            bot.send_message(user_id, "📭 Nessun prodotto presente nel database.")
            return

        for p in prods:
            status_str = "🟢 In Vetrina" if p.get("in_showcase") else "🔴 Nascosto"
            msg = f"📦 **{p.get('name')}**\n🏷 Categoria: {p.get('category')}\n👁 Stato: {status_str}"
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("👁️ Attiva/Disattiva", callback_data=f"tog_{p['id']}_{p.get('in_showcase')}"),
                types.InlineKeyboardButton("🗑️ Elimina", callback_data=f"del_{p['id']}")
            )
            bot.send_message(user_id, msg, parse_mode='Markdown', reply_markup=markup)

    elif data.startswith("tog_"):
        parts = data.split("_")
        p_id, curr_st = parts[1], parts[2] == 'True'
        if db_toggle_product(p_id, curr_st):
            bot.answer_callback_query(call.id, "✅ Stato aggiornato!")
            bot.delete_message(user_id, call.message.message_id)
        else:
            bot.answer_callback_query(call.id, "❌ Errore aggiornamento.")

    elif data.startswith("del_"):
        p_id = data.split("_")[1]
        if db_delete_product(p_id):
            bot.answer_callback_query(call.id, "🗑️ Prodotto eliminato!")
            bot.delete_message(user_id, call.message.message_id)
        else:
            bot.answer_callback_query(call.id, "❌ Errore eliminazione.")

    elif data.startswith("ord_acc_"):
        _, _, o_id, u_id = data.split("_")
        db_update_order_status(o_id, "ACCEPTED")
        if u_id and u_id != "0":
            bot.send_message(int(u_id), f"✅ **Il tuo ordine #{o_id} è stato confermato!**\nInizieremo subito la preparazione.")
        bot.answer_callback_query(call.id, "Ordine accettato")

    elif data.startswith("ord_cnc_"):
        _, _, o_id, u_id = data.split("_")
        db_update_order_status(o_id, "CANCELLED")
        if u_id and u_id != "0":
            bot.send_message(int(u_id), f"❌ **Il tuo ordine #{o_id} è stato annullato.** Contatta il supporto per chiarimenti.")
        bot.answer_callback_query(call.id, "Ordine annullato")

    elif data.startswith("ord_trk_"):
        _, _, o_id, u_id = data.split("_")
        user_states[user_id] = {"step": "WAITING_TRACKING", "target_order": o_id, "target_user": u_id}
        bot.send_message(user_id, f"🚚 Invia ora il **Codice di Tracking** per l'Ordine #{o_id}:")

# --- WIZARD INPUT ---
@bot.message_handler(content_types=['photo', 'video'])
def handle_media(message):
    user_id = message.chat.id
    if user_id != ADMIN_ID or user_states.get(user_id, {}).get("step") != "WAITING_MEDIA":
        return

    if message.photo:
        file_id = message.photo[-1].file_id
        media_type = 'image'
    else:
        file_id = message.video.file_id
        media_type = 'video'

    file_info = bot.get_file(file_id)
    file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_info.file_path}"

    user_states[user_id]["media_url"] = file_url
    user_states[user_id]["media_type"] = media_type
    user_states[user_id]["step"] = "WAITING_NAME"

    bot.reply_to(message, "✅ Media caricato!\n\n📝 Ora invia il **Nome** del prodotto:")

@bot.message_handler(func=lambda m: m.chat.id == ADMIN_ID)
def handle_admin_text(message):
    user_id = message.chat.id
    state = user_states.get(user_id, {})
    step = state.get("step")

    if message.text.startswith("/punti"):
        try:
            parts = message.text.split(" ")
            target_id = int(parts[1])
            delta = int(parts[2])
            ok, new_total = db_update_user_points(target_id, delta)
            if ok:
                bot.reply_to(message, f"🏆 **Punti aggiornati!**\nUtente `{target_id}`: ora ha **{new_total} punti**.")
                bot.send_message(target_id, f"🎉 Hai ricevuto **{delta} punti**! Il tuo totale è ora: **{new_total} punti**.")
            else:
                bot.reply_to(message, "❌ Utente non trovato nel database.")
        except Exception:
            bot.reply_to(message, "❌ Formato errato. Usa: `/punti ID_UTENTE QUANTITA`")
        return

    if step == "WAITING_NAME":
        user_states[user_id]["name"] = message.text
        user_states[user_id]["step"] = "WAITING_PRICES"
        bot.reply_to(
            message,
            "📝 Nome registrato!\n\n💰 Invia i **prezzi/quantità** nel seguente formato:\n`10g - 50, 25g - 100, 50g - 180`",
            parse_mode='Markdown'
        )

    elif step == "WAITING_PRICES":
        try:
            clean_text = message.text.replace("–", "-").replace("—", "-")
            raw = clean_text.split(",")
            prices = []
            for r in raw:
                p = r.split("-")
                prices.append({"qty": p[0].strip(), "price": float(p[1].strip().replace("€", "").strip())})
        except Exception:
            bot.reply_to(message, "❌ Formato non valido. Esempio corretto: `10g - 50, 25g - 100`")
            return

        st = user_states[user_id]
        prod_payload = {
            "name": st["name"],
            "category": st["category"],
            "media_url": st["media_url"],
            "media_type": st["media_type"],
            "price_options": prices,
            "description": f"Prodotto in pronta consegna da {st['category']}",
            "in_showcase": True
        }

        success, err_msg = db_add_product(prod_payload)
        if success:
            bot.reply_to(message, f"🎉 **PRODOTTO PUBBLICATO IN VETRINA!**\n\n📦 **{st['name']}**\n🏷️ Categoria: {st['category']}")
        else:
            bot.reply_to(message, f"❌ **Errore Supabase:**\n`{err_msg}`", parse_mode='Markdown')

        user_states.pop(user_id, None)

    elif step == "WAITING_TRACKING":
        tracking_code = message.text.strip()
        o_id = state.get("target_order")
        u_id = state.get("target_user")

        db_update_order_status(o_id, "SHIPPED", tracking_code)
        if u_id and u_id != "0":
            bot.send_message(
                int(u_id),
                f"🚚 **IL TUO ORDINE #{o_id} È STATO SPEDITO!**\n\nCodice di Tracking: `{tracking_code}`",
                parse_mode='Markdown'
            )
        bot.reply_to(message, f"✅ Tracking per Ordine #{o_id} inviato all'acquirente!")
        user_states.pop(user_id, None)

print("🤖 Avvio Bot Admin in corso...")
bot.remove_webhook()
bot.infinity_polling(skip_pending=True)

bot.remove_webhook()
bot.infinity_polling(skip_pending=True)
