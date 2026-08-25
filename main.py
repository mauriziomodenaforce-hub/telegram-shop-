import os
import json
import time
import threading
import uuid  # <-- AGGIUNTO per generare nomi unici per le foto
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

# Dizionario per memorizzare lo stato dell'amministratore durante l'inserimento o modifica
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
    data = {
        "telegram_id": user_id, 
        "username": username or "Anonimo", 
        "points": 50, 
        "trophies": []
    }
    try:
        requests.post(url, headers=headers, json=data)
    except Exception as e:
        print(f"Errore registrazione: {e}")

def db_add_product(product_data):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False, "Mancano SUPABASE_URL o SUPABASE_KEY."
    url = f"{SUPABASE_URL}/rest/v1/products"
    try:
        r = requests.post(url, headers=get_headers(), json=product_data)
        if r.status_code in [200, 201]:
            return True, "OK"
        else:
            return False, f"Errore HTTP {r.status_code}: {r.text}"
    except Exception as e:
        return False, str(e)

# --- NUOVA FUNZIONE: AGGIORNA PRODOTTO ESISTENTE ---
def db_update_product(prod_id, update_data):
    url = f"{SUPABASE_URL}/rest/v1/products?id=eq.{prod_id}"
    try:
        r = requests.patch(url, headers=get_headers(), json=update_data)
        return r.status_code in [200, 204]
    except Exception as e:
        print(f"Errore aggiornamento prodotto: {e}")
        return False

def db_get_products():
    url = f"{SUPABASE_URL}/rest/v1/products?select=*&order=created_at.desc"
    try:
        r = requests.get(url, headers=get_headers())
        if r.status_code == 200:
            return r.json()
        return []
    except Exception as e:
        print(f"Errore caricamento prodotti: {e}")
        return []

def db_toggle_product(prod_id, current_status):
    url = f"{SUPABASE_URL}/rest/v1/products?id=eq.{prod_id}"
    try:
        r = requests.patch(url, headers=get_headers(), json={"in_showcase": not current_status})
        return r.status_code in [200, 204]
    except Exception as e:
        print(f"Errore on/off prodotto: {e}")
        return False

def db_delete_product(prod_id):
    url = f"{SUPABASE_URL}/rest/v1/products?id=eq.{prod_id}"
    try:
        r = requests.delete(url, headers=get_headers())
        return r.status_code in [200, 204]
    except Exception as e:
        print(f"Errore eliminazione prodotto: {e}")
        return False

def db_save_order(user_id, username, cart, total, address):
    url = f"{SUPABASE_URL}/rest/v1/orders"
    data = {
        "user_id": user_id,
        "username": username or "Anonimo",
        "items": cart,
        "total_price": total,
        "address": address,
        "status": "PENDING"
    }
    try:
        r = requests.post(url, headers=get_headers(), json=data)
        if r.status_code in [200, 201]:
            res = r.json()
            if isinstance(res, list) and len(res) > 0:
                return res[0]["id"]
    except Exception as e:
        print(f"Errore salvataggio ordine: {e}")
    return 999

def db_get_all_orders():
    url = f"{SUPABASE_URL}/rest/v1/orders?select=*&order=created_at.desc"
    try:
        r = requests.get(url, headers=get_headers())
        if r.status_code == 200:
            return r.json()
        return []
    except Exception as e:
        print(f"Errore lettura ordini: {e}")
        return []

def db_update_order_status(order_id, status, tracking=""):
    url = f"{SUPABASE_URL}/rest/v1/orders?id=eq.{order_id}"
    payload = {"status": status}
    if tracking:
        payload["tracking_code"] = tracking
    try:
        r = requests.patch(url, headers=get_headers(), json=payload)
        return r.status_code in [200, 204]
    except Exception as e:
        print(f"Errore aggiornamento ordine: {e}")
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
        print(f"Errore aggiornamento punti: {e}")
    return False, 0

def db_add_user_trophy(target_id, trophy_name):
    url = f"{SUPABASE_URL}/rest/v1/users?telegram_id=eq.{target_id}"
    try:
        r = requests.get(url, headers=get_headers())
        if r.status_code == 200 and len(r.json()) > 0:
            user = r.json()[0]
            trophies = user.get("trophies") or []
            if not isinstance(trophies, list):
                trophies = []
            if trophy_name not in trophies:
                trophies.append(trophy_name)
            requests.patch(url, headers=get_headers(), json={"trophies": trophies})
            return True, trophies
    except Exception as e:
        print(f"Errore assegnazione trofeo: {e}")
    return False, []

# --- GESTIONE GIVEAWAY SUPABASE ---
def db_get_giveaway():
    url = f"{SUPABASE_URL}/rest/v1/giveaway?id=eq.1"
    try:
        r = requests.get(url, headers=get_headers())
        if r.status_code == 200 and len(r.json()) > 0:
            return r.json()[0]
    except Exception:
        pass
    return {"is_active": False, "prize": "N/D", "description": "N/D", "end_date": "N/D", "participants": []}

def db_update_giveaway(payload):
    url = f"{SUPABASE_URL}/rest/v1/giveaway?id=eq.1"
    try:
        r = requests.patch(url, headers=get_headers(), json=payload)
        return r.status_code in [200, 204]
    except Exception:
        return False

# --- NUOVA FUNZIONE: UPLOAD IMMAGINI/VIDEO SU SUPABASE STORAGE ---
def upload_to_supabase_storage(file_bytes, mime_type, file_extension):
    filename = f"media_{int(time.time())}_{uuid.uuid4().hex[:6]}.{file_extension}"
    url = f"{SUPABASE_URL}/storage/v1/object/offerte/{filename}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": mime_type
    }
    try:
        res = requests.post(url, headers=headers, data=file_bytes)
        if res.status_code in [200, 201]:
            public_url = f"{SUPABASE_URL}/storage/v1/object/public/offerte/{filename}"
            return public_url, "OK"
        else:
            return None, f"Codice Errore {res.status_code}: {res.text}"
    except Exception as e:
        return None, f"Eccezione: {str(e)}"


# --- SERVER API PER RICEVERE GLI ORDINI DALLA MINI APP ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

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
            address = data.get("address", "Non specificato")

            # Salva su Supabase
            order_id = db_save_order(user_id, username, cart, total, address)

            # --- ASSEGNAZIONE AUTOMATICA 50 PUNTI PER ORDINE ---
            if user_id and str(user_id) != "0":
                db_update_user_points(int(user_id), 50)

            # Prepara il resoconto degli oggetti
            items_text = "\n".join([f"• {i['qty']}x {i['name']} - €{i['price']}" for i in cart])

            # 1. Invia la notifica al CLIENTE
            user_msg = (
                f"✅ Richiesta #{order_id} inviata al negozio!\n\n"
                f"{items_text}\n"
                f"📍 Indirizzo / Ritrovo: {address}\n"
                f"Totale indicativo: €{total}\n\n"
                "🎁 Hai guadagnato 50 Punti VIP per questo ordine!\n"
                "Un operatore prenderà in carico la tua richiesta a breve."
            )
            if user_id and str(user_id) != "0":
                try:
                    bot.send_message(int(user_id), user_msg)
                except Exception as e:
                    print(f"Errore notifica utente: {e}")

            # 2. Invia la notifica all'ADMIN
            admin_msg = (
                f"🚨 NUOVO ORDINE RICEVUTO! #{order_id}\n\n"
                f"👤 Utente: @{username} (ID: {user_id})\n"
                f"📍 Indirizzo / Ritrovo: {address}\n\n"
                f"📦 Prodotti:\n{items_text}\n\n"
                f"💰 Totale: €{total}"
            )

            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("✅ Accetta", callback_data=f"ord_acc_{order_id}_{user_id}"),
                types.InlineKeyboardButton("❌ Annulla", callback_data=f"ord_cnc_{order_id}_{user_id}"),
                types.InlineKeyboardButton("🚚 Invia Tracking", callback_data=f"ord_trk_{order_id}_{user_id}")
            )

            if ADMIN_ID and ADMIN_ID != 0:
                try:
                    bot.send_message(ADMIN_ID, admin_msg, reply_markup=markup)
                except Exception as e:
                    print(f"Errore notifica admin: {e}")

            # Rispondi alla WebApp
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

# Avvia il server in background
threading.Thread(target=run_health_server, daemon=True).start()


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
    username = message.from_user.username
    db_register_user(user_id, username)

    welcome_text = (
        "👋 Benvenuti nello shop di Boston George 420!\n\n"
        "Qui troverete tutti i prodotti ideali per voi o per il vostro business.\n\n"
        "🤝 Consegna a mano disponibile (Meet Up)\n"
        "🚚 Spedizioni (Ship)\n\n"
        "🛍️ Cliccate in basso per aprire la vetrina!"
    )

    markup = types.InlineKeyboardMarkup()
    if WEB_APP_URL:
        btn = types.InlineKeyboardButton("🛍 Apri la vetrina", web_app=types.WebAppInfo(WEB_APP_URL))
        markup.add(btn)

    bot.send_message(user_id, welcome_text, reply_markup=markup)


# --- COMANDI AMMINISTRATORE ---
@bot.message_handler(commands=['admin', 'cancel', 'menu'])
def admin_panel(message):
    user_id = message.chat.id
    if user_id != ADMIN_ID:
        bot.reply_to(message, "⛔️ Accesso negato. Area riservata all'Amministratore.")
        return

    # Pulisce eventuali stati in sospeso
    user_states.pop(user_id, None)

    bot.send_message(
        user_id, 
        "⚙️ PANNELLO GESTIONALE AMMINISTRATORE\n\nScegli la sezione da gestire:", 
        reply_markup=get_admin_main_keyboard()
    )


# --- GESTIONE DEI PULSANTI INLINE ---
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    user_id = call.message.chat.id
    if user_id != ADMIN_ID:
        return

    data = call.data

    if data == "m_main":
        user_states.pop(user_id, None)
        bot.edit_message_text("⚙️ PANNELLO GESTIONALE AMMINISTRATORE", user_id, call.message.message_id, reply_markup=get_admin_main_keyboard())

    elif data == "m_gw":
        user_states.pop(user_id, None)
        gw = db_get_giveaway()
        st_val = gw.get("is_active", False)
        status = "🟢 ATTIVO" if st_val else "🔴 INATTIVO"
        
        msg = (
            f"🎁 GESTIONE GIVEAWAY\n\n"
            f"Stato: {status}\n"
            f"Premio in Palio: {gw.get('prize', 'N/D')}\n"
            f"Descrizione: {gw.get('description', 'N/D')}\n"
            f"Scadenza: {gw.get('end_date', 'N/D')}\n"
            f"Iscritti Totali: {len(gw.get('participants', []))}"
        )
        
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
        user_states[user_id] = {"step": "WAITING_GW_PRIZE"}
        bot.send_message(user_id, "🏆 Scrivi il nuovo PREMIO in palio per il Giveaway:", reply_markup=get_cancel_keyboard())
        
    elif data == "gw_desc":
        user_states[user_id] = {"step": "WAITING_GW_DESC"}
        bot.send_message(user_id, "📝 Scrivi la nuova DESCRIZIONE (es. Partecipa all'estrazione esclusiva):", reply_markup=get_cancel_keyboard())
        
    elif data == "gw_date":
        user_states[user_id] = {"step": "WAITING_GW_DATE"}
        bot.send_message(user_id, "⏳ Scrivi la SCADENZA (es. 25 Dicembre 2026):", reply_markup=get_cancel_keyboard())

    elif data == "m_prod":
        user_states.pop(user_id, None)
        bot.edit_message_text("📦 GESTIONE PRODOTTI & MEDIA\n\nCosa desideri fare?", user_id, call.message.message_id, reply_markup=get_admin_prod_keyboard())

    elif data == "m_ord":
        user_states.pop(user_id, None)
        bot.edit_message_text("🛒 GESTIONE ORDINI RICEVUTI\n\nGli ordini arrivano in chat in tempo reale.", user_id, call.message.message_id, reply_markup=get_admin_main_keyboard())

    elif data == "m_hist":
        user_states.pop(user_id, None)
        orders = db_get_all_orders()
        if not orders:
            bot.send_message(user_id, "📭 Nessun ordine presente nello storico.", reply_markup=get_cancel_keyboard())
            return
        
        bot.send_message(user_id, f"📜 STORICO COMPLETO ORDINI ({len(orders)} totali):")
        status_map = {
            "PENDING": "⏳ In Attesa",
            "ACCEPTED": "✅ Confermato",
            "SHIPPED": "🚚 Spedito",
            "CANCELLED": "❌ Annullato"
        }

        for o in orders:
            st = status_map.get(o.get('status'), o.get('status'))
            items = o.get('items', [])
            if isinstance(items, str):
                try: 
                    items = json.loads(items)
                except Exception: 
                    items = []
            
            items_str = "\n".join([f"  • {i['name']} ({i['qty']}) - €{i['price']}" for i in items]) if items else "  • Nessun dettaglio"
            
            card_msg = (
                f"🛒 ORDINE #{o.get('id')}\n"
                f"👤 Utente: @{o.get('username')} (ID: {o.get('user_id')})\n"
                f"📍 Indirizzo: {o.get('address', 'N/D')}\n"
                f"📌 Stato: {st}\n"
                f"🚚 Tracking: {o.get('tracking_code', 'N/D')}\n\n"
                f"📦 Prodotti:\n{items_str}\n\n"
                f"💰 Totale: €{o.get('total_price')}"
            )

            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("✅ Accetta", callback_data=f"ord_acc_{o['id']}_{o.get('user_id')}"),
                types.InlineKeyboardButton("❌ Annulla", callback_data=f"ord_cnc_{o['id']}_{o.get('user_id')}"),
                types.InlineKeyboardButton("🚚 Tracking", callback_data=f"ord_trk_{o['id']}_{o.get('user_id')}")
            )
            try:
                bot.send_message(user_id, card_msg, reply_markup=markup)
            except Exception:
                pass
                
        bot.send_message(user_id, "👇 Fine dello storico ordini:", reply_markup=get_cancel_keyboard())

    elif data == "m_pts":
        user_states.pop(user_id, None)
        msg = (
            "🏆 GESTIONE PUNTI & TROFEI\n\n"
            "• Assegna Punti:\n/punti ID_UTENTE QUANTITA\n\n"
            "• Assegna Trofeo:\n/trofeo ID_UTENTE NOME_TROFEO"
        )
        bot.send_message(user_id, msg, reply_markup=get_cancel_keyboard())

    elif data == "p_add":
        user_states.pop(user_id, None)
        markup = types.InlineKeyboardMarkup(row_width=1)
        cats = [
            "🤝 Roma (Meet Up)",
            "🤝 Fondi (Meet Up)",
            "🇮🇹 Italia (Ship)",
            "🇪🇸 Spagna (Ship)",
            "🇳🇱 Olanda (Ship)",
            "🇺🇸 USA (Ship)"
        ]
        markup.add(*[types.InlineKeyboardButton(c, callback_data=f"addcat_{c}") for c in cats])
        markup.add(types.InlineKeyboardButton("🔙 Torna al Menu Principale", callback_data="m_main"))
        bot.edit_message_text("Seleziona la categoria del prodotto:", user_id, call.message.message_id, reply_markup=markup)

    elif data.startswith("addcat_"):
        cat = data.replace("addcat_", "")
        user_states[user_id] = {"category": cat, "step": "WAITING_MEDIA", "media_list": []}
        bot.edit_message_text(
            f"Categoria: {cat}\n\n"
            f"📸 Invia ORA una o più Foto/Video del prodotto.\n\n"
            f"Puoi inviarne quanti ne vuoi. Quando hai finito, premi **✅ Fine Caricamento Media** in basso.",
            user_id, call.message.message_id, reply_markup=get_media_done_keyboard()
        )

    # --- AGGIUNTA MODIFICA PRODOTTO ---
    elif data == "p_list":
        user_states.pop(user_id, None)
        prods = db_get_products()
        if not prods:
            bot.send_message(user_id, "📭 Nessun prodotto presente nel database.", reply_markup=get_cancel_keyboard())
            return
            
        for p in prods:
            st_val = p.get('in_showcase', True)
            status_str = '🟢 In Vetrina' if st_val else '🔴 Nascosto'
            msg = f"📦 {p.get('name')}\n🏷 Categoria: {p.get('category')}\n👁 Stato: {status_str}"
            
            # Qui abbiamo aggiunto il tasto Modifica
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("👁️ On/Off", callback_data=f"tog_{p['id']}_{st_val}"),
                types.InlineKeyboardButton("✏️ Modifica", callback_data=f"edit_{p['id']}")
            )
            markup.add(types.InlineKeyboardButton("🗑️ Elimina", callback_data=f"del_{p['id']}"))
            bot.send_message(user_id, msg, reply_markup=markup)
            
        bot.send_message(user_id, "👇 Opzioni di navigazione:", reply_markup=get_cancel_keyboard())

    # --- MENU DI MODIFICA (Sotto-categorie) ---
    elif data.startswith("edit_"):
        p_id = data.split("_")[1]
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("✏️ Modifica Nome", callback_data=f"edname_{p_id}"),
            types.InlineKeyboardButton("📝 Modifica Descrizione", callback_data=f"eddesc_{p_id}"),
            types.InlineKeyboardButton("💰 Modifica Prezzi", callback_data=f"edprc_{p_id}"),
            types.InlineKeyboardButton("📸 Sostituisci Foto/Video", callback_data=f"edmedia_{p_id}"),
            types.InlineKeyboardButton("🔙 Torna alla Lista", callback_data="p_list")
        )
        bot.edit_message_text("Cosa vuoi modificare di questo prodotto?", user_id, call.message.message_id, reply_markup=markup)

    elif data.startswith("edname_"):
        p_id = data.split("_")[1]
        user_states[user_id] = {"step": "EDIT_NAME", "target_product": p_id}
        bot.send_message(user_id, "✏️ Scrivi il NUOVO NOME per questo prodotto:", reply_markup=get_cancel_keyboard())

    elif data.startswith("eddesc_"):
        p_id = data.split("_")[1]
        user_states[user_id] = {"step": "EDIT_DESC", "target_product": p_id}
        bot.send_message(user_id, "📝 Scrivi la NUOVA DESCRIZIONE per questo prodotto:", reply_markup=get_cancel_keyboard())

    elif data.startswith("edprc_"):
        p_id = data.split("_")[1]
        user_states[user_id] = {"step": "EDIT_PRICES", "target_product": p_id}
        bot.send_message(user_id, "💰 Scrivi le NUOVE VARIANTI DI PREZZO.\nEsempio: 10g - 50, 25g - 100", reply_markup=get_cancel_keyboard())

    elif data.startswith("edmedia_"):
        p_id = data.split("_")[1]
        user_states[user_id] = {"step": "WAITING_MEDIA_EDIT", "target_product": p_id, "media_list": []}
        bot.send_message(user_id, "📸 Invia ORA le nuove foto o video (questo cancellerà quelle vecchie).\nPremi Fine quando hai caricato tutto.", reply_markup=get_media_done_keyboard())

    elif data.startswith("tog_"):
        parts = data.split("_")
        p_id = parts[1]
        curr_st = parts[2] == 'True'
        new_st = not curr_st
        
        if db_toggle_product(p_id, curr_st):
            bot.answer_callback_query(call.id, "✅ Stato aggiornato con successo!")
            
            msg_text = call.message.text
            if "🟢 In Vetrina" in msg_text:
                new_text = msg_text.replace("🟢 In Vetrina", "🔴 Nascosto")
            elif "🔴 Nascosto" in msg_text:
                new_text = msg_text.replace("🔴 Nascosto", "🟢 In Vetrina")
            else:
                new_text = msg_text

            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("👁️ On/Off", callback_data=f"tog_{p_id}_{new_st}"),
                types.InlineKeyboardButton("✏️ Modifica", callback_data=f"edit_{p_id}")
            )
            markup.add(types.InlineKeyboardButton("🗑️ Elimina", callback_data=f"del_{p_id}"))
            try:
                bot.edit_message_text(new_text, user_id, call.message.message_id, reply_markup=markup)
            except Exception as e:
                print(f"Errore modifica messaggio: {e}")
        else:
            bot.answer_callback_query(call.id, "❌ Errore durante l'aggiornamento.")

    elif data.startswith("del_"):
        p_id = data.split("_")[1]
        if db_delete_product(p_id):
            bot.answer_callback_query(call.id, "🗑️ Prodotto eliminato definitivamente!")
            try:
                bot.delete_message(user_id, call.message.message_id)
            except Exception:
                pass
        else:
            bot.answer_callback_query(call.id, "❌ Errore durante l'eliminazione.")

    elif data == "done_media":
        st = user_states.get(user_id, {})
        if not st.get("media_list"):
            bot.answer_callback_query(call.id, "❌ Invia almeno un file multimediale prima di continuare!", show_alert=True)
            return
        
        # Se stiamo creando un prodotto nuovo:
        if st.get("step") == "WAITING_MEDIA":
            st["step"] = "WAITING_NAME"
            bot.send_message(
                user_id, 
                f"✅ Hai caricato {len(st['media_list'])} file!\n\n📝 Ora invia il NOME del prodotto:", 
                reply_markup=get_cancel_keyboard()
            )
        # Se stiamo MODIFICANDO le foto di un prodotto esistente:
        elif st.get("step") == "WAITING_MEDIA_EDIT":
            p_id = st["target_product"]
            media_list = st["media_list"]
            first_url = media_list[0]["url"] if media_list else ""
            first_type = media_list[0]["type"] if media_list else "image"
            
            db_update_product(p_id, {"media_list": media_list, "media_url": first_url, "media_type": first_type})
            bot.send_message(user_id, "✅ Foto/Video aggiornati con successo nel prodotto!", reply_markup=get_admin_main_keyboard())
            user_states.pop(user_id, None)

    elif data.startswith("ord_acc_"):
        parts = data.split("_")
        o_id, u_id = parts[2], parts[3]
        db_update_order_status(o_id, "ACCEPTED")
        if u_id and u_id != "0":
            try:
                bot.send_message(int(u_id), f"✅ Il tuo ordine #{o_id} è stato confermato dal venditore!")
            except Exception:
                pass
        bot.answer_callback_query(call.id, "✅ Ordine Accettato e cliente avvisato!")

    elif data.startswith("ord_cnc_"):
        parts = data.split("_")
        o_id, u_id = parts[2], parts[3]
        db_update_order_status(o_id, "CANCELLED")
        if u_id and u_id != "0":
            try:
                bot.send_message(int(u_id), f"❌ Attenzione: Il tuo ordine #{o_id} è stato annullato.")
            except Exception:
                pass
        bot.answer_callback_query(call.id, "❌ Ordine Annullato!")

    elif data.startswith("ord_trk_"):
        parts = data.split("_")
        o_id, u_id = parts[2], parts[3]
        user_states[user_id] = {"step": "WAITING_TRACKING", "target_order": o_id, "target_user": u_id}
        bot.send_message(user_id, f"🚚 Invia ora il Codice di Tracking per l'Ordine #{o_id}:", reply_markup=get_cancel_keyboard())


# --- GESTIONE INVIO FOTO E VIDEO (ORA SI SALVANO SU SUPABASE) ---
@bot.message_handler(content_types=['photo', 'video'])
def handle_media(message):
    user_id = message.chat.id
    if user_id != ADMIN_ID:
        return
        
    state = user_states.get(user_id, {})
    if state.get("step") not in ["WAITING_MEDIA", "WAITING_MEDIA_EDIT"]:
        return

    # Avvisa l'utente che il bot sta elaborando
    wait_msg = bot.reply_to(message, "⏳ Elaborazione... sto caricando la foto sul tuo server Supabase, attendi...")

    if message.photo:
        file_id = message.photo[-1].file_id
        media_type = 'image'
        mime = 'image/jpeg'
        ext = 'jpg'
    else:
        file_id = message.video.file_id
        media_type = 'video'
        mime = 'video/mp4'
        ext = 'mp4'

    # 1. Recupera il file da Telegram
    file_info = bot.get_file(file_id)
    file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_info.file_path}"
    
    try:
        # Scarica in memoria
        file_bytes = requests.get(file_url).content
        
        # 2. Carica definitivamente su Supabase Storage!
        public_url = upload_to_supabase_storage(file_bytes, mime, ext)
        
        if public_url:
            if "media_list" not in user_states[user_id]:
                user_states[user_id]["media_list"] = []
                
            user_states[user_id]["media_list"].append({"url": public_url, "type": media_type})
            tot = len(user_states[user_id]["media_list"])
            
            bot.edit_message_text(
                f"✅ Salvato per sempre!\n📸 Media #{tot} aggiunto correttamente.\n\nPuoi inviare altri file oppure premere **✅ Fine Caricamento Media** in basso per proseguire.", 
                user_id, wait_msg.message_id, reply_markup=get_media_done_keyboard()
            )
        else:
            bot.edit_message_text("❌ Si è verificato un errore durante il caricamento su Supabase.", user_id, wait_msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"❌ Errore scaricamento da Telegram: {e}", user_id, wait_msg.message_id)


# --- WIZARD TESTUALE PER L'ADMIN ---
@bot.message_handler(func=lambda m: m.chat.id == ADMIN_ID)
def handle_admin_text(message):
    user_id = message.chat.id
    state = user_states.get(user_id, {})
    step = state.get("step")

    # Comando per assegnare punti
    if message.text and message.text.startswith("/punti"):
        try:
            parts = message.text.split()
            target_user = int(parts[1])
            qty = int(parts[2])
            ok, new_total = db_update_user_points(target_user, qty)
            if ok:
                bot.reply_to(message, f"✅ Operazione completata! L'utente {target_user} ora ha {new_total} punti.")
                bot.send_message(target_user, f"🎉 Complimenti! Hai ricevuto {qty} punti.")
            else:
                bot.reply_to(message, "❌ Errore: Utente non trovato.")
        except Exception:
            bot.reply_to(message, "❌ Formato errato. Usa: /punti ID_UTENTE QUANTITA")
        return

    # Comando per assegnare trofei
    if message.text and message.text.startswith("/trofeo"):
        try:
            parts = message.text.split(" ", 2)
            target_user = int(parts[1])
            trophy_name = parts[2]
            ok, _ = db_add_user_trophy(target_user, trophy_name)
            if ok:
                bot.reply_to(message, f"✅ Trofeo '{trophy_name}' assegnato con successo!")
                bot.send_message(target_user, f"🥇 NUOVO TROFEO SBLOCCATO: {trophy_name}!\nControlla la bacheca nell'App.")
            else:
                bot.reply_to(message, "❌ Errore: Utente non trovato.")
        except Exception:
            bot.reply_to(message, "❌ Formato errato. Usa: /trofeo ID_UTENTE NOME_TROFEO")
        return

    # --- SALVATAGGIO INPUT GIVEAWAY ---
    if step == "WAITING_GW_PRIZE":
        db_update_giveaway({"prize": message.text})
        bot.reply_to(message, "✅ Premio aggiornato con successo!", reply_markup=get_admin_main_keyboard())
        user_states.pop(user_id, None)
        return
    elif step == "WAITING_GW_DESC":
        db_update_giveaway({"description": message.text})
        bot.reply_to(message, "✅ Descrizione aggiornata con successo!", reply_markup=get_admin_main_keyboard())
        user_states.pop(user_id, None)
        return
    elif step == "WAITING_GW_DATE":
        db_update_giveaway({"end_date": message.text})
        bot.reply_to(message, "✅ Scadenza aggiornata con successo!", reply_markup=get_admin_main_keyboard())
        user_states.pop(user_id, None)
        return

    # --- RISPOSTE ALLA FUNZIONE MODIFICA ---
    if step == "EDIT_NAME":
        db_update_product(state["target_product"], {"name": message.text})
        bot.reply_to(message, "✅ Nome aggiornato con successo!", reply_markup=get_admin_main_keyboard())
        user_states.pop(user_id, None)

    elif step == "EDIT_DESC":
        db_update_product(state["target_product"], {"description": message.text})
        bot.reply_to(message, "✅ Descrizione aggiornata con successo!", reply_markup=get_admin_main_keyboard())
        user_states.pop(user_id, None)

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
            if not prices:
                raise ValueError("Nessun formato valido.")
            db_update_product(state["target_product"], {"price_options": prices})
            bot.reply_to(message, "✅ Prezzi aggiornati con successo!", reply_markup=get_admin_main_keyboard())
            user_states.pop(user_id, None)
        except Exception:
            bot.reply_to(message, "❌ Formato errato. Esempio corretto: 10g - 50, 25g - 100", reply_markup=get_cancel_keyboard())
            return

    # --- LOGICA STANDARD DI CREAZIONE NUOVO PRODOTTO ---
    elif step == "WAITING_NAME":
        state["name"] = message.text
        state["step"] = "WAITING_DESC"
        bot.reply_to(
            message, 
            "✍️ Nome salvato. Ora invia la DESCRIZIONE del prodotto.\n(Puoi scrivere username come @ilboston per renderli cliccabili nell'app):", 
            reply_markup=get_cancel_keyboard()
        )

    elif step == "WAITING_DESC":
        state["desc"] = message.text
        state["step"] = "WAITING_PRICES"
        bot.reply_to(
            message, 
            "💰 Ultimo step. Invia i PREZZI e le VARIANTI (Esempio: 10g - 50, 25g - 100):", 
            reply_markup=get_cancel_keyboard()
        )

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
                    
            if not prices:
                raise ValueError("Nessun formato di prezzo valido individuato.")
                
        except Exception as e:
            bot.reply_to(
                message, 
                "❌ Formato non riconosciuto.\nEsempio corretto: 10g - 50, 25g - 100", 
                reply_markup=get_cancel_keyboard()
            )
            return

        media_list = state.get("media_list", [])
        first_url = media_list[0]["url"] if media_list else ""
        first_type = media_list[0]["type"] if media_list else "image"

        payload = {
            "name": state["name"],
            "category": state["category"],
            "media_list": media_list,
            "media_url": first_url,
            "media_type": first_type,
            "price_options": prices,
            "description": state.get("desc", ""),
            "in_showcase": True
        }

        success, err_msg = db_add_product(payload)
        
        if success:
            bot.reply_to(
                message, 
                f"🎉 PRODOTTO PUBBLICATO IN VETRINA!\n📦 Nome: {state['name']}\n📸 Media caricati: {len(media_list)}", 
                reply_markup=get_admin_main_keyboard()
            )
        else:
            bot.reply_to(
                message, 
                f"❌ ERRORE DATABASE (SUPABASE):\n{err_msg}\n\n⚠️ IMPORTANTE: Hai eseguito il comando SQL per creare la colonna 'media_list'?", 
                reply_markup=get_admin_main_keyboard()
            )
            
        user_states.pop(user_id, None)

    elif step == "WAITING_TRACKING":
        tracking_code = message.text.strip()
        order_id = state["target_order"]
        target_user = state["target_user"]
        
        db_update_order_status(order_id, "SHIPPED", tracking_code)
        
        if target_user and str(target_user) != "0":
            try:
                bot.send_message(int(target_user), f"🚚 IL TUO ORDINE #{order_id} È STATO SPEDITO!\n\nCodice Tracking: {tracking_code}")
            except Exception:
                pass
                
        bot.reply_to(message, f"✅ Codice di Tracking per l'ordine #{order_id} inviato correttamente al cliente!", reply_markup=get_admin_main_keyboard())
        user_states.pop(user_id, None)


print("🤖 Avvio Bot Admin in corso...")
while True:
    try:
        bot.remove_webhook()
        bot.infinity_polling(skip_pending=True, timeout=20, long_polling_timeout=20)
    except Exception as e:
        print(f"Errore di connessione a Telegram: {e}. Riavvio in corso...")
        time.sleep(3)
