# config.py

from pathlib import Path

# --- Telethon API credentials  ---
TELETHON_API_ID = 26392569            # int
TELETHON_API_HASH = "5beef5aaadd0946ec359d6bfc3644f22"   # str

# === пути и файлы ===
BASE_DIR       = Path(__file__).resolve().parent
BOT_LIST_FILE  = BASE_DIR / "bots.txt"     # список путей к папкам ботов
PIDS_FILE      = BASE_DIR / "pids.json"    # сюда пишем текущие PID'ы
LOG_FILE       = BASE_DIR / "logs.txt"     # ОДИН общий лог

# === как запускать ботов ===
PYTHON_EXE     = "python"                  # при необходимости укажи полный путь
LAUNCH_MODE    = "powershell"              # "direct" | "cmd" | "powershell"

# === Telegram для стартовой сводки (с тегами) ===
LOG_BOT_TOKEN  = "7532640677:AAHFOaR5JJCYBE1QB-Q-9xgQQLVAOl7tI2k"
LOG_CHAT_ID    = -1002739648696

# === Telegram для /status (без тегов) ===
STATUS_BOT_TOKEN = "7532640677:AAHFOaR5JJCYBE1QB-Q-9xgQQLVAOl7tI2k"
STATUS_CHAT_IDS  = {-1002739648696}       # set() из ID чатов, где разрешён /status

# === тайминги ===
TELEGRAM_TIMEOUT    = 15.0
START_GRACE_SECONDS = 1.5
RETRIES             = 3
BACKOFF             = 0.75

# === Windows flags ===
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008

# Кто имеет право нажимать кнопку "Рестарт всех"
ADMIN_USER_ID = 7514615252  # <— замени на свой Telegram user_id

# Текст callback-данных для кнопки
RESTART_ALL_CB = "restart_all"

# Callback для обновления статуса
RELOAD_STATUS_CB = "reload_status"

# Callback-ключ для выбора одного бота при обновлении не-URL-переменных
UPDATE_ONE_PREFIX = "update_one:"  # формат callback: update_one:<idx>:<var>

# Кнопка "Рестарт бота" и её callback'и
RESTART_ONE_CB      = "restart_one"      # открыть список ботов
RESTART_ONE_PREFIX  = "restart_one:"     # префикс для конкретного выбора, напр. restart_one:5
BACK_TO_STATUS_CB   = "back_to_status"   # вернуться к статусу (без действий)

# Сколько кнопок-батов в ряду в меню выбора
RESTART_ONE_COLS    = 2                  # 2 колонки — удобно для длинных имён
# Сколько ботов максимум показывать на одной странице (без пагинации)
RESTART_ONE_MAX     = 60                 # хватит с запасом

# ✏️ Обновление доменов (только админ)
UPDATE_DOMAINS_CB      = "update_domains_menu"       # открыть меню "что обновлять"
UPDATE_VAR_WEBAPP1_CB  = "update_var:WEBAPP_URL_1"   # выбрать WEBAPP_URL_1
UPDATE_VAR_PROMO_CB    = "update_var:PROMOCODE_WEBAPP_URL"  # выбрать PROMOCODE_WEBAPP_URL
UPDATE_CANCEL_CB       = "update_cancel"             # отменить ввод нового URL
UPDATE_VAR_BOT_TOKEN_CB  = "updvar:BOT_TOKEN"
UPDATE_VAR_IMAGE_CB      = "updvar:IMAGE_FILE_ID"

# какие переменные – URL, какие – «секреты», какие – ЧИСЛОВЫЕ (без кавычек)
URL_VARS    = {"WEBAPP_URL_1", "PROMOCODE_WEBAPP_URL", "WEBAPP_URL_2"}
SECRET_VARS = {"BOT_TOKEN"}
INT_VARS    = {"ADMIN_ID", "REFERRAL_NOTIFY_CHAT_ID"}  # ← без кавычек

# HTTP/Telegram tuning
HTTP_POOL_SIZE   = 30
HTTP_RETRIES     = 2
HTTP_BACKOFF     = 0.3
TG_REQ_TIMEOUT   = 5.0
TG_LONGPOLL_TIMEOUT = 25

CATEGORY_RULES = [
    ("BotKazino", "BotKazino"),
    ("GGBET",     "GGBET"),
    ("1WIN",      "1WIN"),
]

# ─────────────────────────────────────────────────────────
# КНОПКИ/ПРЕФИКСЫ
CREATE_NEW_CB           = "create_new"
CREATE_SET_PREFIX       = "create_set:"
CREATE_RUN_PREFIX       = "create_run:"
CREATE_IMAGE_PREFIX     = "create_img:"
CREATE_AUTOCONF_PREFIX  = "create_autoconf:"
CREATE_BOTFATHER_CB = "create_botfather"

# Перечень переменных для авто-конфига (информационная константа — можно и в sozdanie.py держать)
AUTOCONF_VARS = ("BOT_TOKEN","WEBAPP_URL_1","PROMOCODE_WEBAPP_URL","WEBAPP_URL_2","ADMIN_ID","REFERRAL_NOTIFY_CHAT_ID")
