import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import telebot
from telebot import types

# Server web integrato per il piano GRATUITO di Render
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
    welcome_text = (
        "👋 **Benvenuto nello shop di BostonGeorge!**\n\n"
        "Qui troverai tutti i prodotti che ti servono per te o per il tuo business.\n\n"
        "📦 - Tutti i PRODOTTI sono in pronta consegna\n"
        "🇮🇹 - Spedizione Da ITALIA\n"
        "🇪🇸 - Spedizione Da Spagna\n"
        "🇨🇿 - Spedizione Da Repubblica ceca"
    )
    
    markup = types.InlineKeyboardMarkup()
    if WEB_APP_URL:
        web_app_info = types.WebAppInfo(WEB_APP_URL)
        btn = types.InlineKeyboardButton("🛍 Apri la vetrina", web_app=web_app_info)
        markup.add(btn)
    
    bot.send_message(message.chat.id, welcome_text, parse_mode='Markdown', reply_markup=markup)

@bot.message_handler(commands=['admin'])
def admin_panel(message):
    bot.reply_to(message, "⚙️ **Pannello Gestionale Attivo**")

print("🤖 Bot Telegram avviato H24...")
bot.infinity_polling()
