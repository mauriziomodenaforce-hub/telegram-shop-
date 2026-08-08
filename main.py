import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import telebot
from telebot import types

# Server web integrato per mantenere attivo il bot su Render
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Online 24/7")

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# Avvia il server web in background
threading.Thread(target=run_health_server, daemon=True).start()

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
WEB_APP_URL = os.environ.get('WEB_APP_URL')

bot = telebot.TeleBot(TELEGRAM_TOKEN)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    print(f"Ricevuto /start da {message.chat.id}")
    welcome_text = (
        "👋 **Benvenuti nello shop di Boston George 420!**\n\n"
        "Qui troverete tutti i prodotti ideali per voi o per il vostro business.\n\n"
        "📦 Tutti i PRODOTTI sono in pronta consegna\n"
        "🤝 Consegna a mano disponibile\n\n"
        "🚚 **Spedizioni da:**\n"
        "🇮🇹 Italia\n"
        "🇪🇸 Spagna\n"
        "🇳🇱 Olanda\n"
        "🇺🇸 USA\n\n"
        "🛍️ Cliccate in basso per aprire la vetrina!"
    )
    
    markup = types.InlineKeyboardMarkup()
    if WEB_APP_URL:
        clean_url = WEB_APP_URL.strip()
        web_app_info = types.WebAppInfo(clean_url)
        btn = types.InlineKeyboardButton("🛍 Apri la vetrina", web_app=web_app_info)
        markup.add(btn)
    
    bot.send_message(message.chat.id, welcome_text, parse_mode='Markdown', reply_markup=markup)

@bot.message_handler(commands=['admin'])
def admin_panel(message):
    bot.reply_to(message, "⚙️ **Pannello Gestionale Attivo**")

print("🤖 Reset Webhook e avvio Bot...")
bot.remove_webhook()
bot.infinity_polling(skip_pending=True)
