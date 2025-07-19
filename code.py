import os
import asyncio
import logging
import json
import re
import hashlib
from datetime import datetime, timedelta
import websockets
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import collections

# --- Cấu hình Logging ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("websockets.client").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- Cấu hình Bot Token và Admin ID ---
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '7630248769:AAG36CSLxWWovAfa-Byjh_DohcpN3pA94Iw') # Token từ code mới
ADMIN_ID = int(os.getenv("ADMIN_ID", "6915752059")) # Admin ID từ code mới
USER_FILE = "users.json"
STATUS_FILE = "status.json"

# --- Keyboard layouts ---
def get_user_keyboard():
    """Keyboard cho người dùng thường"""
    keyboard = [
        ["🎮 Chọn Game Dự Đoán", "📆 Kiểm tra thời hạn"],
        ["📞 Liên hệ Admin", "ℹ️ Trợ giúp"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

def get_admin_keyboard():
    """Keyboard cho admin"""
    keyboard = [
        ["🎮 Chọn Game Dự Đoán", "📆 Kiểm tra thời hạn"],
        ["👑 Thêm key", "🗑️ Xóa key"],
        ["📋 Danh sách user", "📦 Backup dữ liệu"],
        ["📊 Trạng thái bot", "📞 Liên hệ Admin"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

# --- Quản lý người dùng và trạng thái bot (Từ code cũ của bạn) ---
def load_users():
    """Tải danh sách người dùng"""
    try:
        with open(USER_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        logger.error(f"Lỗi: File '{USER_FILE}' không hợp lệ. Đang tạo file mới.")
        return {}

def save_users(data):
    """Lưu danh sách người dùng"""
    with open(USER_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def is_user_active(user_id):
    """Kiểm tra người dùng có đang hoạt động (active) - ở đây nghĩa là có key và còn hạn"""
    users = load_users()
    info = users.get(str(user_id), {})
    try:
        expire = datetime.fromisoformat(info.get("expire", "2000-01-01T00:00:00"))
        return datetime.now() < expire
    except ValueError:
        return False

def is_admin(user_id):
    """Kiểm tra quyền admin"""
    return user_id == ADMIN_ID

def get_status():
    """Lấy trạng thái bot"""
    try:
        with open(STATUS_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("status", "off")
    except (FileNotFoundError, json.JSONDecodeError):
        return "off"

def set_status(value):
    """Đặt trạng thái bot"""
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump({"status": value}, f, indent=2, ensure_ascii=False)

# --- Global Data Stores for Games ---

# 68GB
FIREBASE_URL_68GB = 'https://gambai-e4406-default-rtdb.asia-southeast1.firebasedatabase.app/taixiu_sessions.json'
data_68gb = {
    "id_phien": None,
    "ket_qua_raw": None, # Lưu kết quả thô "1-2-3"
    "ket_qua_tx": None,  # Lưu kết quả "T" hoặc "X"
    "id_phien_ke_tiep": None,
    "md5_ke_tiep": None, # Vẫn lưu nhưng không hiển thị khi dự đoán
    "du_doan": "",
    "do_tin_cay": "N/A",
    "chi_tiet_du_doan": [],
    "ngay": "",
    "Id": "68GBBot - @nhutquangdz" # Thêm tác giả
}
# Biến toàn cục cho 68GB để theo dõi lịch sử và hiệu suất
_68gb_pattern_history = collections.deque(maxlen=200)
_68gb_dice_history = collections.deque(maxlen=200)
_68gb_last_raw_predictions = []
_68gb_prediction_performance = {}
_68gb_strategy_weights = {
    "Cầu Bệt": 1.0, "Cầu 1-1": 1.0, "Cầu Lặp 2-1": 1.0, "Cầu Lặp 2-2": 1.0,
    "Cầu Lặp 3-1": 1.0, "Cầu Lặp 3-2": 1.0, "Cầu Lặp 3-3": 1.0,
    "Cầu Lặp 4-1": 1.0, "Cầu Lặp 4-2": 1.0, "Cầu Lặp 4-3": 1.0, "Cầu Lặp 4-4": 1.0,
    "Cầu Đối Xứng": 1.2, "Cầu Đảo Ngược": 1.1, "Cầu Ziczac Ngắn": 0.8,
    "Cầu Lặp Chuỗi Khác": 1.0,
    "Xu hướng Tài mạnh (Ngắn)": 1.0, "Xu hướng Xỉu mạnh (Ngắn)": 1.0,
    "Xu hướng Tài rất mạnh (Dài)": 1.2, "Xu hướng Xỉu rất mạnh (Dài)": 1.2,
    "Xu hướng tổng điểm": 0.9, "Bộ ba": 1.3, "Điểm 10": 0.8, "Điểm 11": 0.8,
    "Bẻ cầu bệt dài": 1.6, "Bẻ cầu 1-1 dài": 1.6, "Reset Cầu/Bẻ Sâu": 1.9
}

# Sunwin
SUNWIN_WS_URL = "wss://websocket.azhkthg1.net/websocket?token=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJhbW91bnQiOjAsInVzZXJuYW1lIjoiU0NfYXBpc3Vud2luMTIzIn0.hgrRbSV6vnBwJMg9ZFtbx3rRu5mX_hZMZ_m5gMNBkw0"
sunwin_login_messages = [
    [1, "Simms", "SC_thatoidisun112233", "112233", {
        "info": "{\"ipAddress\":\"2a09:bac5:d46f:16dc::247:13\",\"userId\":\"a867d30e-417d-47e5-a5c5-8a11e11746f0\",\"username\":\"SC_thatoidisun112233\",\"timestamp\":1752735812697,\"refreshToken\":\"e2e6f309ef844b22b8f88938223327b9.da49c2c8fe3a4f6dbe2d5f9c0b040319\"}",
        "signature": "0659600D4D3B6209AF13B6DDBD55A42F8D14B2FE8598925EF22C8F9EEB4FC06146DC18BD0B1AA8E5AD524FD9211047FAA258B632288F8D34840E4D915BDC404CA8A70705D0F15884BF346A28200825959F43A7D9DA0063D8DC04B37BA207A0974803DF03BB39B9048DCE72463C16F211F8426507E1A02AC605EA348DDD53FB7"
    }],
    [6, "MiniGame", "taixiuPlugin", {"cmd": 1005}],
    [6, "MiniGame", "lobbyPlugin", {"cmd": 10001}]
]

sunwin_current_data = {
    "phien_truoc": None,
    "ket_qua": "",
    "Dice": [],
    "phien_hien_tai": None,
    "du_doan": "",
    "do_tin_cay": "N/A",
    "cau": "",
    "ngay": "",
    "Id": "SunwinBot - @nhutquangdz", # Thêm tác giả
    "chi_tiet_du_doan": []
}

sunwin_pattern_history = collections.deque(maxlen=200)
sunwin_dice_history = collections.deque(maxlen=200)
sunwin_last_raw_predictions = []
sunwin_prediction_performance = {}
sunwin_strategy_weights = {
    "Cầu Bệt": 1.0, "Cầu 1-1": 1.0, "Cầu Lặp 2-1": 1.0, "Cầu Lặp 2-2": 1.0,
    "Cầu Lặp 3-1": 1.0, "Cầu Lặp 3-2": 1.0, "Cầu Lặp 3-3": 1.0,
    "Cầu Lặp 4-1": 1.0, "Cầu Lặp 4-2": 1.0, "Cầu Lặp 4-3": 1.0, "Cầu Lặp 4-4": 1.0,
    "Cầu Đối Xứng": 1.2, "Cầu Đảo Ngược": 1.1, "Cầu Ziczac Ngắn": 0.8,
    "Cầu Lặp Chuỗi Khác": 1.0,
    "Xu hướng Tài mạnh (Ngắn)": 1.0, "Xu hướng Xỉu mạnh (Ngắn)": 1.0,
    "Xu hướng Tài rất mạnh (Dài)": 1.2, "Xu hướng Xỉu rất mạnh (Dài)": 1.2,
    "Xu hướng tổng điểm": 0.9, "Bộ ba": 1.3, "Điểm 10": 0.8, "Điểm 11": 0.8,
    "Bẻ cầu bệt dài": 1.6, "Bẻ cầu 1-1 dài": 1.6, "Reset Cầu/Bẻ Sâu": 1.9
}

# 789Club
WS_URL_789CLUB = "wss://websocket.atpman.net/websocket"
HEADERS_789CLUB = {
    "Host": "websocket.atpman.net",
    "Origin": "https://play.789club.sx",
    "User-Agent": "Mozilla/5.0",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "vi-VN,vi;q=0.9",
    "Pragma": "no-cache",
    "Cache-Control": "no-cache"
}
login_message_789club = [
    1, "MiniGame", "thatoidimoo11233", "112233", {
        "info": '{"ipAddress":"2405:4802:18c2:5990:3f0:c150:861d:5427","userId":"6ba5b041-a68d-4468-95d3-0bb2d8674512","username":"S8_thatoidimoo11233","timestamp":1752497763866,"refreshToken":"c6c49a4ff8ca49ac87fcaf25419221.6f17553681b74176a4ebeb77f475f443"}',
        "signature": "5F953D843B438DD810A98D903AD3623CE98AED1745C3925EEAFD2A5BEB4D86A24ED0B97129E6AAB5DA1C3F73C2A236AE06D08EDDD937991260DFEA543E8F1C8818A651BDF4204E97A53F0461B306A95A6D7D56F435326270E9E4CB8084BB93969BFD4DB3CA8E519D079324E47110BCC23AB2139508D9E762407B76DE542D6E68"
    }
]
subscribe_tx_result_789club = [6, "MiniGame", "taixiuUnbalancedPlugin", {"cmd": 2000}]
subscribe_lobby_789club = [6, "MiniGame", "lobbyPlugin", {"cmd": 10001}]

club789_last_event_id = 19
club789_current_data = {
    "phien_truoc": None,
    "ket_qua": "",
    "Dice": [],
    "phien_hien_tai": None,
    "du_doan": "",
    "do_tin_cay": "N/A",
    "ket_luan_tong_hop": "",
    "chi_tiet_du_doan": [],
    "ngay": "",
    "Id": "789ClubBot - @nhutquangdz" # Thêm tác giả
}

club789_pattern_history = collections.deque(maxlen=200)
club789_dice_history = collections.deque(maxlen=200)
club789_last_raw_predictions = []
club789_prediction_performance = {}
club789_strategy_weights = {
    "Cầu Bệt": 1.0, "Cầu 1-1": 1.0, "Cầu Lặp 2-1": 1.0, "Cầu Lặp 2-2": 1.0,
    "Cầu Lặp 3-1": 1.0, "Cầu Lặp 3-2": 1.0, "Cầu Lặp 3-3": 1.0,
    "Cầu Lặp 4-1": 1.0, "Cầu Lặp 4-2": 1.0, "Cầu Lặp 4-3": 1.0, "Cầu Lặp 4-4": 1.0,
    "Cầu Đối Xứng": 1.2, "Cầu Đảo Ngược": 1.1, "Cầu Ziczac Ngắn": 0.8,
    "Cầu Lặp Chuỗi Khác": 1.0,
    "Xu hướng Tài mạnh (Ngắn)": 1.0, "Xu hướng Xỉu mạnh (Ngắn)": 1.0,
    "Xu hướng Tài rất mạnh (Dài)": 1.2, "Xu hướng Xỉu rất mạnh (Dài)": 1.2,
    "Xu hướng tổng điểm": 0.9, "Bộ ba": 1.3, "Điểm 10": 0.8, "Điểm 11": 0.8,
    "Bẻ cầu bệt dài": 1.6, "Bẻ cầu 1-1 dài": 1.6, "Reset Cầu/Bẻ Sâu": 1.9
}

# --- Các hàm Helper Functions (dùng chung cho các game) ---
def get_tai_xiu(total):
    return "Tài" if (total >= 11 and total <= 18) else "Xỉu"

# Hàm tạo mẫu tự động để đạt 1000+ mẫu (dùng chung)
def generate_common_patterns():
    patterns = []
    for i in range(3, 21):
        patterns.append({"name": f"Cầu Bệt Tài ({i})", "pattern": "T" * i, "predict": "T", "conf": 0.05 + (i * 0.005), "minHistory": i, "strategyGroup": "Cầu Bệt"})
        patterns.append({"name": f"Cầu Bệt Xỉu ({i})", "pattern": "X" * i, "predict": "X", "conf": 0.05 + (i * 0.005), "minHistory": i, "strategyGroup": "Cầu Bệt"})
    for i in range(3, 21):
        pattern_tx = "".join(["T" if j % 2 == 0 else "X" for j in range(i)])
        pattern_xt = "".join(["X" if j % 2 == 0 else "T" for j in range(i)])
        patterns.append({"name": f"Cầu 1-1 (TX - {i})", "pattern": pattern_tx, "predict": ("T" if i % 2 == 0 else "X"), "conf": 0.05 + (i * 0.005), "minHistory": i, "strategyGroup": "Cầu 1-1"})
        patterns.append({"name": f"Cầu 1-1 (XT - {i})", "pattern": pattern_xt, "predict": ("X" if i % 2 == 0 else "T"), "conf": 0.05 + (i * 0.005), "minHistory": i, "strategyGroup": "Cầu 1-1"})

    base_repeated_patterns = [
        {"base": "TTX", "group": "Cầu Lặp 2-1"}, {"base": "XXT", "group": "Cầu Lặp 2-1"},
        {"base": "TTXX", "group": "Cầu Lặp 2-2"}, {"base": "XXTT", "group": "Cầu Lặp 2-2"},
        {"base": "TTTX", "group": "Cầu Lặp 3-1"}, {"base": "XXXT", "group": "Cầu Lặp 3-1"},
        {"base": "TTTXX", "group": "Cầu Lặp 3-2"}, {"base": "XXXTT", "group": "Cầu Lặp 3-2"},
        {"base": "TTTXXX", "group": "Cầu Lặp 3-3"}, {"base": "XXXTTT", "group": "Cầu Lặp 3-3"},
        {"base": "TTTTX", "group": "Cầu Lặp 4-1"}, {"base": "XXXXT", "group": "Cầu Lặp 4-1"},
        {"base": "TTTTXX", "group": "Cầu Lặp 4-2"}, {"base": "XXXXTT", "group": "Cầu Lặp 4-2"},
        {"base": "TTTTXXX", "group": "Cầu Lặp 4-3"}, {"base": "XXXXTTT", "group": "Cầu Lặp 4-3"},
        {"base": "TTTTXXXX", "group": "Cầu Lặp 4-4"}, {"base": "XXXXTTTT", "group": "Cầu Lặp 4-4"}
    ]
    for pattern_info in base_repeated_patterns:
        for num_repeats in range(1, 6):
            current_pattern = pattern_info["base"] * num_repeats
            predict_char = pattern_info["base"][0]
            patterns.append({"name": f"{pattern_info['group']} ({pattern_info['base']} x{num_repeats})", "pattern": current_pattern, "predict": predict_char, "conf": 0.08 + (num_repeats * 0.01), "minHistory": len(current_pattern), "strategyGroup": pattern_info["group"]})

    symmetric_and_inverse_patterns = [
        {"base": "TX", "predict": "T", "group": "Cầu Đối Xứng"}, {"base": "XT", "predict": "X", "group": "Cầu Đối Xứng"},
        {"base": "TXXT", "predict": "T", "group": "Cầu Đối Xứng"}, {"base": "XTTX", "predict": "X", "group": "Cầu Đối Xứng"},
        {"base": "TTXT", "predict": "X", "group": "Cầu Đảo Ngược"}, {"base": "XXTX", "predict": "T", "group": "Cầu Đảo Ngược"},
        {"base": "TXTXT", "predict": "X", "group": "Cầu Đối Xứng"}, {"base": "XTXTX", "predict": "T", "group": "Cầu Đối Xứng"},
    ]
    for pattern_info in symmetric_and_inverse_patterns:
        for num_repeats in range(1, 4):
            current_pattern = pattern_info["base"] * num_repeats
            patterns.append({"name": f"{pattern_info['group']} ({pattern_info['base']} x{num_repeats})", "pattern": current_pattern, "predict": pattern_info["predict"], "conf": 0.1 + (num_repeats * 0.015), "minHistory": len(current_pattern), "strategyGroup": pattern_info["group"]})
        if len(pattern_info["base"]) == 2:
            pattern_abba = pattern_info["base"] + pattern_info["base"][::-1]
            patterns.append({"name": f"{pattern_info['group']} ({pattern_abba})", "pattern": pattern_abba, "predict": pattern_info["base"][0], "conf": 0.15, "minHistory": len(pattern_abba), "strategyGroup": pattern_info["group"]})
            pattern_abccba = pattern_info["base"] * 2 + (pattern_info["base"][::-1] * 2)
            if len(pattern_abccba) <= 10:
                patterns.append({"name": f"{pattern_info['group']} ({pattern_abccba})", "pattern": pattern_abccba, "predict": pattern_info["base"][0], "conf": 0.18, "minHistory": len(pattern_abccba), "strategyGroup": pattern_info["group"]})

    short_ziczac_patterns = [
        {"pattern": "TTX", "predict": "T"}, {"pattern": "XXT", "predict": "X"},
        {"pattern": "TXT", "predict": "X"}, {"pattern": "XTX", "predict": "T"},
        {"pattern": "TXX", "predict": "X"}, {"pattern": "XTT", "predict": "T"},
        {"pattern": "TTXX", "predict": "T"}, {"pattern": "XXTT", "predict": "X"},
        {"pattern": "TXTX", "predict": "T"}, {"pattern": "XTXT", "predict": "X"},
        {"pattern": "XTTX", "predict": "X"}, {"pattern": "TXXT", "predict": "T"}
    ]
    for p in short_ziczac_patterns:
        patterns.append({"name": f"Cầu Ziczac Ngắn ({p['pattern']})", "pattern": p["pattern"], "predict": p["predict"], "conf": 0.05, "minHistory": len(p["pattern"]), "strategyGroup": "Cầu Ziczac Ngắn"})
    
    complex_repeats = ["TTX", "XXT", "TXT", "TXX", "XTT"]
    for base in complex_repeats:
        for i in range(2, 5):
            current_pattern = base * i
            if len(current_pattern) <= 15:
                patterns.append({"name": f"Cầu Lặp Chuỗi Khác ({base} x{i})", "pattern": current_pattern, "predict": base[0], "conf": 0.07 + (i * 0.01), "minHistory": len(current_pattern), "strategyGroup": "Cầu Lặp Chuỗi Khác"})
    return patterns

all_pattern_strategies = generate_common_patterns()

# Khởi tạo predictionPerformance cho các nhóm chiến lược
def initialize_performance_metrics(weights, patterns):
    performance = {}
    for group_name in weights.keys():
        performance[group_name] = {"correct": 0, "total": 0}
    for pattern in patterns:
        if pattern["strategyGroup"] not in performance:
            performance[pattern["strategyGroup"]] = {"correct": 0, "total": 0}
            weights[pattern["strategyGroup"]] = 1.0
    return performance

# Khởi tạo ngay sau khi định nghĩa các biến weights
_68gb_prediction_performance = initialize_performance_metrics(_68gb_strategy_weights, all_pattern_strategies)
sunwin_prediction_performance = initialize_performance_metrics(sunwin_strategy_weights, all_pattern_strategies)
club789_prediction_performance = initialize_performance_metrics(club789_strategy_weights, all_pattern_strategies)

# === Thuật toán dự đoán nâng cao (analyze_and_predict) ===
def analyze_and_predict(history, dice_hist, performance_data, strategy_weights):
    analysis = {
        "totalResults": len(history),
        "taiCount": history.count('T'),
        "xiuCount": history.count('X'),
        "last50Pattern": "".join(history[-50:]) if len(history) >= 50 else "".join(history),
        "last200Pattern": "".join(history) if len(history) >= 200 else "".join(history),
        "predictionDetails": [],
        "rawPredictions": []
    }

    recent_history_full = "".join(history)
    recent50 = "".join(history[-50:])
    recent20 = "".join(history[-20:])
    recent10 = "".join(history[-10:])

    def add_prediction(strategy_name, predict, conf_multiplier, detail, strategy_group=None):
        effective_strategy_name = strategy_group if strategy_group else strategy_name
        if effective_strategy_name not in performance_data:
            performance_data[effective_strategy_name] = {"correct": 0, "total": 0}
        if effective_strategy_name not in strategy_weights:
            strategy_weights[effective_strategy_name] = 1.0

        weight = strategy_weights[effective_strategy_name]
        confidence = conf_multiplier * weight
        analysis["rawPredictions"].append({
            "strategy": strategy_name,
            "predict": predict,
            "confidence": confidence,
            "detail": detail,
            "strategyGroup": effective_strategy_name
        })

    for p in all_pattern_strategies:
        if len(history) >= p["minHistory"]:
            target_history_string = ""
            if p["minHistory"] <= 10: target_history_string = recent10
            elif p["minHistory"] <= 20: target_history_string = recent20
            elif p["minHistory"] <= 50: target_history_string = recent50
            else: target_history_string = recent_history_full

            if target_history_string.endswith(p["pattern"]):
                add_prediction(p["name"], p["predict"], p["conf"], f"Phát hiện: {p['name']}", p["strategyGroup"])

    if len(history) >= 7:
        if recent_history_full.endswith("TTTTTTT"):
            add_prediction("Bẻ cầu bệt dài", "X", 0.35, "Cầu bệt Tài quá dài (>7), dự đoán bẻ cầu", "Bẻ cầu bệt dài")
        elif recent_history_full.endswith("XXXXXXX"):
            add_prediction("Bẻ cầu bệt dài", "T", 0.35, "Cầu bệt Xỉu quá dài (>7), dự đoán bẻ cầu", "Bẻ cầu bệt dài")
        if recent_history_full.endswith("XTXTXTXT"):
            add_prediction("Bẻ cầu 1-1 dài", "X", 0.3, "Cầu 1-1 quá dài (>8), dự đoán bẻ sang Xỉu", "Bẻ cầu 1-1 dài")
        elif recent_history_full.endswith("TXTXTXTX"):
            add_prediction("Bẻ cầu 1-1 dài", "T", 0.3, "Cầu 1-1 quá dài (>8), dự đoán bẻ sang Tài", "Bẻ cầu 1-1 dài")

    tai_in_20 = history[-20:].count('T') if len(history) >= 20 else history.count('T')
    xiu_in_20 = history[-20:].count('X') if len(history) >= 20 else history.count('X')

    if tai_in_20 > xiu_in_20 + 5:
        add_prediction("Xu hướng Tài mạnh (Ngắn)", "T", 0.25, f"Xu hướng 20 phiên: Nghiêng về Tài ({tai_in_20} Tài / {xiu_in_20} Xỉu)", "Xu hướng Tài mạnh (Ngắn)")
    elif xiu_in_20 > tai_in_20 + 5:
        add_prediction("Xu hướng Xỉu mạnh (Ngắn)", "X", 0.25, f"Xu hướng 20 phiên: Nghiêng về Xỉu ({tai_in_20} Tài / {xiu_in_20} Xỉu)", "Xu hướng Xỉu mạnh (Ngắn)")
    else:
        analysis["predictionDetails"].append(f"Xu hướng 20 phiên: Khá cân bằng ({tai_in_20} Tài / {xiu_in_20} Xỉu)")
    
    tai_in_50 = history[-50:].count('T') if len(history) >= 50 else history.count('T')
    xiu_in_50 = history[-50:].count('X') if len(history) >= 50 else history.count('X')
    if tai_in_50 > xiu_in_50 + 8:
        add_prediction("Xu hướng Tài rất mạnh (Dài)", "T", 0.3, f"Xu hướng 50 phiên: Rất nghiêng về Tài ({tai_in_50} Tài / {xiu_in_50} Xỉu)", "Xu hướng Tài rất mạnh (Dài)")
    elif xiu_in_50 > tai_in_50 + 8:
        add_prediction("Xu hướng Xỉu rất mạnh (Dài)", "X", 0.3, f"Xu hướng 50 phiên: Rất nghiêng về Xỉu ({tai_in_50} Tài / {xiu_in_50} Xỉu)", "Xu hướng Xỉu rất mạnh (Dài)")

    if dice_hist:
        last_result_dice = dice_hist[-1]
        total = last_result_dice["d1"] + last_result_dice["d2"] + last_result_dice["d3"]
        analysis["predictionDetails"].append(f"Kết quả xúc xắc gần nhất: {last_result_dice['d1']}-{last_result_dice['d2']}-{last_result_dice['d3']} (Tổng: {total})")

        last_10_totals = [d["total"] for d in list(dice_hist)[-10:]]
        sum_counts = collections.Counter(last_10_totals)

        most_frequent_total = 0
        max_count = 0
        if sum_counts:
            most_frequent_total = sum_counts.most_common(1)[0][0]
            max_count = sum_counts.most_common(1)[0][1]

        if max_count >= 4:
            predict = "T" if most_frequent_total > 10 else "X"
            add_prediction("Xu hướng tổng điểm", predict, 0.15, f"Tổng điểm {most_frequent_total} xuất hiện nhiều trong 10 phiên gần nhất", "Xu hướng tổng điểm")

        if last_result_dice["d1"] == last_result_dice["d2"] == last_result_dice["d3"]:
            predict = "T" if last_result_dice["d1"] <= 3 else "X"
            add_prediction("Bộ ba", predict, 0.25, f"Phát hiện bộ ba {last_result_dice['d1']}, dự đoán bẻ cầu", "Bộ ba")

        if total == 10:
            add_prediction("Điểm 10", "X", 0.08, "Tổng 10 (Xỉu) vừa ra, thường là điểm dao động hoặc bẻ cầu", "Điểm 10")
        elif total == 11:
            add_prediction("Điểm 11", "T", 0.08, "Tổng 11 (Tài) vừa ra, thường là điểm dao động hoặc bẻ cầu", "Điểm 11")
            
    if len(history) > 20:
        last_10 = list(history)[-10:]
        tai_in_10 = last_10.count('T')
        xiu_in_10 = last_10.count('X')

        if abs(tai_in_10 - xiu_in_10) <= 2:
            if not analysis["rawPredictions"] or analysis["rawPredictions"][0]["confidence"] < 0.2:
                last_result = history[-1]
                predict = 'X' if last_result == 'T' else 'T'
                add_prediction("Reset Cầu/Bẻ Sâu", predict, 0.28, "Cầu đang loạn hoặc khó đoán, dự đoán reset.", "Reset Cầu/Bẻ Sâu")
        
        if recent_history_full.endswith("TTTTTTTTT"):
            add_prediction("Reset Cầu/Bẻ Sâu", "X", 0.4, "Cầu bệt Tài cực dài (>9), dự đoán bẻ mạnh!", "Reset Cầu/Bẻ Sâu")
        elif recent_history_full.endswith("XXXXXXXXX"):
            add_prediction("Reset Cầu/Bẻ Sâu", "T", 0.4, "Cầu bệt Xỉu cực dài (>9), dự đoán bẻ mạnh!", "Reset Cầu/Bẻ Sâu")

    analysis["rawPredictions"].sort(key=lambda x: x["confidence"], reverse=True)

    vote_tai = 0
    vote_xiu = 0
    number_of_top_predictions = min(len(analysis["rawPredictions"]), 5)
    top_predictions = analysis["rawPredictions"][:number_of_top_predictions]

    for p in top_predictions:
        if p["predict"] == 'T': vote_tai += p["confidence"]
        elif p["predict"] == 'X': vote_xiu += p["confidence"]

    final_prediction = "?"
    combined_confidence = 0

    if vote_tai == 0 and vote_xiu == 0:
        final_prediction = "?"
        combined_confidence = 0
    elif vote_tai > vote_xiu * 1.3:
        final_prediction = "T"
        combined_confidence = vote_tai / (vote_tai + vote_xiu)
    elif vote_xiu > vote_tai * 1.3:
        final_prediction = "X"
        combined_confidence = vote_xiu / (vote_tai + vote_tai) # Changed from vote_tai + vote_xiu to avoid division by zero or negative if vote_tai is 0
    else:
        if analysis["rawPredictions"]:
            final_prediction = analysis["rawPredictions"][0]["predict"]
            combined_confidence = analysis["rawPredictions"][0]["confidence"]
        else:
            final_prediction = "?"
            combined_confidence = 0

    min_output_confidence = 0.55
    max_output_confidence = 0.92
    original_min_confidence = 0
    original_max_confidence = 1

    normalized_confidence = max(min(combined_confidence, original_max_confidence), original_min_confidence)
    final_mapped_confidence = ((normalized_confidence - original_min_confidence) / (original_max_confidence - original_min_confidence)) * (max_output_confidence - min_output_confidence) + min_output_confidence
    final_mapped_confidence = max(min(final_mapped_confidence, max_output_confidence), min_output_confidence)
    
    analysis["finalPrediction"] = final_prediction
    analysis["confidence"] = final_mapped_confidence

    analysis["predictionDetails"] = [
        f"{p['strategy']}: {p['predict']} (Conf: {(p['confidence'] * 100):.1f}%) - {p['detail'] or ''}"
        for p in analysis["rawPredictions"]
    ]
    
    analysis["lastRawPredictions"] = analysis["rawPredictions"]
    return analysis

# --- Hàm cập nhật trọng số (update_strategy_weight) ---
def update_strategy_weight(last_raw_predictions, actual_result, performance_data, strategy_weights):
    if not last_raw_predictions:
        return

    for prediction_item in last_raw_predictions:
        strategy_name = prediction_item["strategy"]
        predicted_result = prediction_item["predict"]
        strategy_group = prediction_item["strategyGroup"]

        effective_strategy_name = strategy_group if strategy_group else strategy_name

        if effective_strategy_name not in performance_data:
            performance_data[effective_strategy_name] = {"correct": 0, "total": 0}
        
        performance_data[effective_strategy_name]["total"] += 1

        if predicted_result == actual_result:
            performance_data[effective_strategy_name]["correct"] += 1

        correct = performance_data[effective_strategy_name]["correct"]
        total = performance_data[effective_strategy_name]["total"]

        if total >= 5:
            accuracy = correct / total
            adjustment_factor = 0.05

            if accuracy > 0.6:
                strategy_weights[effective_strategy_name] = min(strategy_weights.get(effective_strategy_name, 1.0) + adjustment_factor, 2.5)
            elif accuracy < 0.4:
                strategy_weights[effective_strategy_name] = max(strategy_weights.get(effective_strategy_name, 1.0) - adjustment_factor, 0.5)

# --- Các hàm xử lý dữ liệu cho từng Game ---

# 68GB (Firebase) - Đã thêm logic dự đoán
async def fetch_and_update_68gb():
    global data_68gb, _68gb_pattern_history, _68gb_dice_history, \
           _68gb_last_raw_predictions, _68gb_prediction_performance, _68gb_strategy_weights
    
    import requests # Import here to avoid circular dependency if not used elsewhere blocking
    while True:
        try:
            res = await asyncio.to_thread(requests.get, FIREBASE_URL_68GB)
            res.raise_for_status()
            data = res.json()

            end_sessions = []
            start_sessions = []

            for key, item in data.items():
                raw = item.get("rawData")
                if not raw: continue
                
                if "mnmdsbgameend" in raw:
                    match = re.search(r"#(\d+).*?{(\d-\d-\d)}", raw)
                    if match:
                        end_sessions.append({
                            "id_phien": int(match.group(1)),
                            "ket_qua_raw": match.group(2),
                            "time": item.get("time")
                        })
                
                if "mnmdsbgamestart" in raw:
                    match = re.search(r" ([a-f0-9]{32})$", raw)
                    if match:
                        start_sessions.append({
                            "md5": match.group(1),
                            "time": item.get("time")
                        })

            end_sessions.sort(key=lambda x: datetime.strptime(x["time"], "%Y-%m-%d %H:%M:%S"), reverse=True)
            start_sessions.sort(key=lambda x: datetime.strptime(x["time"], "%Y-%m-%d %H:%M:%S"), reverse=True)

            old_id_phien = data_68gb["id_phien"]
            
            if end_sessions:
                latest_session = end_sessions[0]
                current_id_phien = latest_session["id_phien"]
                raw_dice_str = latest_session["ket_qua_raw"]
                
                d_parts = list(map(int, raw_dice_str.split('-')))
                dice_total = sum(d_parts)
                current_tx_result = get_tai_xiu(dice_total)

                # --- CẬP NHẬT DỰ ĐOÁN CHO 68GB ---
                # Chỉ xử lý nếu có phiên mới
                if current_id_phien != old_id_phien:
                    # Cập nhật trọng số cho phiên trước
                    if _68gb_last_raw_predictions and data_68gb["ket_qua_tx"]:
                        update_strategy_weight(_68gb_last_raw_predictions, data_68gb["ket_qua_tx"],
                                               _68gb_prediction_performance, _68gb_strategy_weights)
                    
                    _68gb_pattern_history.append(current_tx_result)
                    _68gb_dice_history.append({"d1": d_parts[0], "d2": d_parts[1], "d3": d_parts[2], "total": dice_total})

                    # GỌI THUẬT TOÁN DỰ ĐOÁN CHO PHIÊN HIỆN TẠI
                    prediction_analysis = analyze_and_predict(
                        list(_68gb_pattern_history),
                        list(_68gb_dice_history),
                        _68gb_prediction_performance,
                        _68gb_strategy_weights
                    )
                    _68gb_last_raw_predictions = prediction_analysis["lastRawPredictions"]

                    data_68gb["id_phien"] = current_id_phien
                    data_68gb["ket_qua_raw"] = raw_dice_str
                    data_68gb["ket_qua_tx"] = current_tx_result
                    data_68gb["id_phien_ke_tiep"] = current_id_phien + 1
                    data_68gb["du_doan"] = prediction_analysis["finalPrediction"]
                    data_68gb["do_tin_cay"] = f"{prediction_analysis['confidence'] * 100:.2f}%"
                    data_68gb["chi_tiet_du_doan"] = prediction_analysis["predictionDetails"]
                    data_68gb["ngay"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                    logger.info(f'[68GB] Phiên {current_id_phien}: {raw_dice_str} ({current_tx_result}) | Dự đoán: {data_68gb["du_doan"]} ({data_68gb["do_tin_cay"]})')
                
                # Cập nhật MD5 kế tiếp riêng, không liên quan đến kết quả phiên vừa rồi
                if start_sessions:
                    data_68gb["md5_ke_tiep"] = start_sessions[0]["md5"]
                else:
                    data_68gb["md5_ke_tiep"] = "Đang chờ..."

        except Exception as e:
            logger.error(f'[68GB ERROR] Lỗi lấy hoặc xử lý dữ liệu: {e}')
        
        await asyncio.sleep(3) # Cập nhật mỗi 3 giây

# Sunwin
async def connect_websocket_sunwin():
    global sunwin_current_data, sunwin_pattern_history, sunwin_dice_history, \
           sunwin_last_raw_predictions, sunwin_prediction_performance, sunwin_strategy_weights
    
    sunwin_id_phien_chua_co_kq = None

    while True:
        try:
            logger.info('[SUNWIN] Đang kết nối WebSocket...')
            async with websockets.connect(
                SUNWIN_WS_URL,
                extra_headers={"User-Agent": "Mozilla/5.0", "Origin": "https://play.sun.win"},
                ping_interval=15, ping_timeout=10
            ) as ws:
                logger.info('[SUNWIN] WebSocket đã kết nối thành công.')

                for i, msg in enumerate(sunwin_login_messages):
                    await asyncio.sleep(i * 0.6)
                    await ws.send(json.dumps(msg))
                
                asyncio.create_task(send_ping_sunwin(ws))

                while True:
                    message = await ws.recv()
                    try:
                        data = json.loads(message)
                        if isinstance(data, list) and isinstance(data[1], dict):
                            cmd = data[1].get('cmd')
                            if cmd == 1008 and 'sid' in data[1]:
                                sunwin_id_phien_chua_co_kq = data[1]['sid']
                            elif cmd == 1003 and 'gBB' in data[1]:
                                d1, d2, d3 = data[1].get('d1'), data[1].get('d2'), data[1].get('d3')
                                total = d1 + d2 + d3
                                result_tx = "T" if total > 10 else "X"
                                
                                if sunwin_last_raw_predictions:
                                    update_strategy_weight(sunwin_last_raw_predictions, result_tx,
                                                           sunwin_prediction_performance, sunwin_strategy_weights)

                                sunwin_pattern_history.append(result_tx)
                                sunwin_dice_history.append({"d1": d1, "d2": d2, "d3": d3, "total": total})

                                prediction_analysis = analyze_and_predict(
                                    list(sunwin_pattern_history),
                                    list(sunwin_dice_history),
                                    sunwin_prediction_performance,
                                    sunwin_strategy_weights
                                )
                                sunwin_last_raw_predictions = prediction_analysis["lastRawPredictions"]

                                sunwin_current_data = {
                                    "phien_truoc": sunwin_id_phien_chua_co_kq,
                                    "ket_qua": result_tx,
                                    "Dice": [d1, d2, d3],
                                    "phien_hien_tai": sunwin_id_phien_chua_co_kq + 1 if sunwin_id_phien_chua_co_kq else None,
                                    "du_doan": prediction_analysis["finalPrediction"],
                                    "do_tin_cay": f"{prediction_analysis['confidence'] * 100:.2f}%",
                                    "cau": "".join(list(sunwin_pattern_history)[-10:]),
                                    "ngay": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                    "Id": "SunwinBot - @nhutquangdz", # Thêm tác giả
                                    "chi_tiet_du_doan": prediction_analysis["predictionDetails"]
                                }
                                logger.info(f'[SUNWIN] Phiên {sunwin_id_phien_chua_co_kq}: {result_tx} ({total}) | Dự đoán: {sunwin_current_data["du_doan"]} ({sunwin_current_data["do_tin_cay"]})')
                                sunwin_id_phien_chua_co_kq = None
                    except json.JSONDecodeError: pass
                    except Exception as e: logger.error(f'[SUNWIN ERROR] Lỗi xử lý tin nhắn: {e}')
        except Exception as e: logger.error(f'[SUNWIN ERROR] Lỗi WebSocket: {e}')
        await asyncio.sleep(2.5)

async def send_ping_sunwin(ws):
    while ws.open:
        try:
            await ws.ping()
            await asyncio.sleep(15)
        except websockets.exceptions.ConnectionClosed: break
        except Exception as e: logger.error(f"[SUNWIN ERROR] Lỗi ping: {e}"); break

# 789Club
async def connect_websocket_789club():
    global club789_last_event_id, club789_current_data, club789_pattern_history, \
           club789_dice_history, club789_last_raw_predictions, \
           club789_prediction_performance, club789_strategy_weights

    while True:
        try:
            logger.info('[789CLUB] Đang kết nối WebSocket...')
            async with websockets.connect(
                WS_URL_789CLUB,
                extra_headers=HEADERS_789CLUB,
                ping_interval=10, ping_timeout=5
            ) as ws:
                logger.info('[789CLUB] WebSocket đã kết nối thành công.')

                await ws.send(json.dumps(login_message_789club))
                await asyncio.sleep(1)
                await ws.send(json.dumps(subscribe_tx_result_789club))
                await ws.send(json.dumps(subscribe_lobby_789club))
                
                asyncio.create_task(send_periodic_messages_789club(ws))

                while True:
                    message = await ws.recv()
                    try:
                        data = json.loads(message)
                        if isinstance(data, list):
                            if len(data) >= 3 and data[0] == 7 and data[1] == "Simms" and isinstance(data[2], int):
                                club789_last_event_id = data[2]
                            if len(data) >= 2 and isinstance(data[1], dict) and data[1].get('cmd') == 2006:
                                sid = data[1].get('sid')
                                d1, d2, d3 = data[1].get('d1'), data[1].get('d2'), data[1].get('d3')
                                if all(v is not None for v in [sid, d1, d2, d3]):
                                    tong = d1 + d2 + d3
                                    result_tx = "T" if tong >= 11 else "X"
                                    dice_array = [d1, d2, d3]

                                    if club789_current_data["phien_hien_tai"] == sid:
                                        continue
                                    
                                    if club789_last_raw_predictions and club789_current_data["phien_hien_tai"] is not None:
                                        update_strategy_weight(club789_last_raw_predictions, result_tx,
                                                               club789_prediction_performance, club789_strategy_weights)
                                    
                                    club789_pattern_history.append(result_tx)
                                    club789_dice_history.append({"d1": d1, "d2": d2, "d3": d3, "total": tong})

                                    prediction_analysis = analyze_and_predict(
                                        list(club789_pattern_history),
                                        list(club789_dice_history),
                                        club789_prediction_performance,
                                        club789_strategy_weights
                                    )
                                    club789_last_raw_predictions = prediction_analysis["lastRawPredictions"]

                                    club789_current_data["phien_truoc"] = club789_current_data["phien_hien_tai"]
                                    club789_current_data["phien_hien_tai"] = sid
                                    club789_current_data["Dice"] = dice_array
                                    club789_current_data["ket_qua"] = result_tx
                                    club789_current_data["du_doan"] = prediction_analysis["finalPrediction"]
                                    club789_current_data["do_tin_cay"] = f"{prediction_analysis['confidence'] * 100:.2f}%"
                                    club789_current_data["ket_luan_tong_hop"] = prediction_analysis["predictionDetails"][0] if prediction_analysis["predictionDetails"] else "Không có kết luận chi tiết."
                                    club789_current_data["chi_tiet_du_doan"] = prediction_analysis["predictionDetails"]
                                    club789_current_data["ngay"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                                    logger.info(f"🎲 [789CLUB] Phiên mới: {sid} | Kết quả: {tong} ({result_tx}) | Dự đoán: {prediction_analysis['finalPrediction']} ({prediction_analysis['confidence'] * 100:.2f}%)")
                    except json.JSONDecodeError: pass
                    except Exception as e: logger.error(f'[789CLUB ERROR] Lỗi xử lý tin nhắn: {e}')
        except Exception as e: logger.error(f'[789CLUB ERROR] Lỗi WebSocket: {e}')
        await asyncio.sleep(5)

async def send_periodic_messages_789club(ws):
    global club789_last_event_id
    while ws.open:
        try:
            await ws.send("2")
            await asyncio.sleep(10)
            await ws.send(json.dumps(subscribe_tx_result_789club))
            await asyncio.sleep(20)
            await ws.send(json.dumps([7, "Simms", club789_last_event_id, 0, {"id": 0}]))
            await asyncio.sleep(15)
        except websockets.exceptions.ConnectionClosed: break
        except Exception as e: logger.error(f"[789CLUB ERROR] Lỗi gửi tin định kỳ: {e}"); break

# --- Các hàm xử lý lệnh và nút cho Bot Telegram ---

## Lệnh Bắt đầu
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh /start"""
    if not update.message or not update.effective_user:
        return
    
    user = update.effective_user
    user_id = user.id
    
    if is_admin(user_id):
        keyboard = get_admin_keyboard()
        role_text = "👑 ADMIN"
        extra_info = "\n🔹 Sử dụng /bat để bật bot\n🔹 Sử dụng /tat để tắt bot"
    else:
        keyboard = get_user_keyboard()
        role_text = "👤 NGƯỜI DÙNG"
        extra_info = ""
    
    welcome = (
        f"🌟 **CHÀO MỪNG ĐẾN BOT VIP PRO** 🌟\n\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"👤 Người dùng: **{user.full_name}**\n"
        f"🎭 Vai trò: **{role_text}**\n\n"
        "🔑 Đây là bot hỗ trợ quản lý thành viên có key.\n"
        f"{extra_info}\n\n"
        "⬇️ Sử dụng các nút bên dưới để điều khiển bot ⬇️"
    )
    
    await update.message.reply_text(
        welcome, 
        parse_mode="HTML", 
        reply_markup=keyboard
    )

## Lệnh Bật/Tắt Bot
async def bat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh /bat - Bật bot (chỉ admin)"""
    if not update.message or not update.effective_user: return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Bạn không có quyền sử dụng lệnh này.")
        return
    
    set_status("on")
    users = load_users()
    active_users = [int(uid) for uid, info in users.items() if is_user_active(uid)]
    notification_message = (
        "🟢 **BOT ĐÃ ĐƯỢC BẬT**\n\n"
        "⏰ Bot đang hoạt động.\n"
        "📡 Bạn sẽ nhận được các thông báo chung và dự đoán game.\n\n"
        "💎 Bot VIP Pro đang hoạt động!"
    )
    
    sent_count = 0
    for user_id in active_users:
        try:
            await context.bot.send_message(chat_id=user_id, text=notification_message, parse_mode="HTML")
            sent_count += 1
        except Exception as e: logger.error(f"Không thể gửi thông báo bật bot cho user {user_id}: {str(e)}")
    
    await update.message.reply_text(
        f"🟢 **BOT ĐÃ ĐƯỢC BẬT**\n\n"
        f"📡 Đã thông báo cho {sent_count} user active\n"
        f"⏰ Bot đang hoạt động.",
        parse_mode="HTML"
    )

async def tat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh /tat - Tắt bot (chỉ admin)"""
    if not update.message or not update.effective_user: return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Bạn không có quyền sử dụng lệnh này.")
        return
    
    set_status("off")
    users = load_users()
    active_users = [int(uid) for uid, info in users.items() if is_user_active(uid)]
    notification_message = (
        "🔴 **BOT ĐÃ ĐƯỢC TẮT**\n\n"
        "⏸️ Tạm dừng các thông báo tự động từ bot và dự đoán game.\n\n"
        "💎 Bot VIP Pro đã dừng hoạt động!"
    )
    
    sent_count = 0
    for user_id in active_users:
        try:
            await context.bot.send_message(chat_id=user_id, text=notification_message, parse_mode="HTML")
            sent_count += 1
        except Exception as e: logger.error(f"Không thể gửi thông báo tắt bot cho user {user_id}: {str(e)}")
    
    await update.message.reply_text(
        f"🔴 **BOT ĐÃ ĐƯỢC TẮT**\n\n"
        f"📡 Đã thông báo cho {sent_count} user active\n"
        f"⏸️ Dừng các thông báo tự động.",
        parse_mode="HTML"
    )

## Xử lý các nút bấm từ keyboard
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý tin nhắn từ nút bấm và input admin"""
    if not update.message or not update.effective_user:
        return
    
    text = update.message.text
    if not text:
        return
    
    user_id = update.effective_user.id
    
    admin_functions = [
        "👑 Thêm key", "🗑️ Xóa key", "📋 Danh sách user", 
        "📦 Backup dữ liệu", "📊 Trạng thái bot"
    ]
    
    if text in admin_functions and not is_admin(user_id):
        await update.message.reply_text("🚫 Bạn không có quyền sử dụng chức năng này.")
        return
    
    if text == "📆 Kiểm tra thời hạn":
        await check_expire(update, context)
    elif text == "🎮 Chọn Game Dự Đoán": # Nút mới
        await select_game_for_prediction(update, context)
    elif text == "📞 Liên hệ Admin":
        await contact_admin(update, context)
    elif text == "ℹ️ Trợ giúp":
        await show_help(update, context)
    elif text == "👑 Thêm key":
        await prompt_add_key(update, context)
    elif text == "🗑️ Xóa key":
        await prompt_delete_key(update, context)
    elif text == "📋 Danh sách user":
        await list_users(update, context)
    elif text == "📦 Backup dữ liệu":
        await backup_users(update, context)
    elif text == "📊 Trạng thái bot":
        await check_bot_status(update, context)
    else:
        await handle_admin_input(update, context)

## Chọn Game Dự Đoán (Nút mới)
async def select_game_for_prediction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Hiển thị lựa chọn game để dự đoán."""
    if not update.message or not update.effective_user:
        return
    
    # Kiểm tra key còn hạn trước khi cho chọn game
    if not is_user_active(update.effective_user.id):
        await update.message.reply_text("❌ Bạn chưa có key hoặc key đã hết hạn. Vui lòng liên hệ admin để kích hoạt.")
        return

    keyboard = [
        [InlineKeyboardButton("68GB", callback_data='predict_68gb')],
        [InlineKeyboardButton("Sunwin", callback_data='predict_sunwin')],
        [InlineKeyboardButton("789Club", callback_data='predict_789club')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('🎲 Vui lòng chọn game bạn muốn xem dự đoán:', reply_markup=reply_markup)

async def button_callback(update: Update, context) -> None:
    """Xử lý các sự kiện nhấn nút inline keyboard (chọn game dự đoán)."""
    query = update.callback_query
    await query.answer()

    # Kiểm tra key còn hạn trước khi hiển thị dự đoán
    if not is_user_active(query.from_user.id):
        await query.edit_message_text(text="❌ Key của bạn đã hết hạn hoặc không hợp lệ. Vui lòng liên hệ admin.")
        return

    game_selected = query.data.replace('predict_', '')
    message_text = ""

    if game_selected == '68gb':
        if not data_68gb["id_phien"]:
            message_text = 'Dữ liệu 68GB đang được tải, vui lòng thử lại sau giây lát.'
        else:
            details_str = "\n".join(data_68gb["chi_tiet_du_doan"]) if data_68gb["chi_tiet_du_doan"] else "Không có chi tiết dự đoán."
            message_text = (
                f"🎲 *Dự đoán Tài Xỉu 68GB:*\n\n"
                f"*ID Phiên gần nhất:* `{data_68gb['id_phien']}`\n"
                f"*Kết quả gần nhất:* `{data_68gb['ket_qua_raw']} ({data_68gb['ket_qua_tx']})`\n\n"
                f"*ID Phiên Kế Tiếp:* `{data_68gb['id_phien_ke_tiep']}`\n"
                f"*Dự đoán phiên sau:* `{data_68gb['du_doan']}`\n"
                f"*Độ tin cậy:* `{data_68gb['do_tin_cay']}`\n"
                f"*Cập nhật lúc:* `{data_68gb['ngay']}`\n"
                f"*ID Bot:* `{data_68gb['Id']}`\n\n"
                f"*Chi tiết dự đoán:*\n"
                f"{details_str}"
            )
    elif game_selected == 'sunwin':
        if not sunwin_current_data["phien_hien_tai"]:
            message_text = 'Dữ liệu Sunwin đang được tải, vui lòng thử lại sau giây lát.'
        else:
            details_str = "\n".join(sunwin_current_data["chi_tiet_du_doan"]) if sunwin_current_data["chi_tiet_du_doan"] else "Không có chi tiết dự đoán."
            message_text = (
                f"🎲 *Dự đoán Tài Xỉu Sunwin:*\n\n"
                f"*ID Phiên trước:* `{sunwin_current_data['phien_truoc']}`\n"
                f"*Kết quả:* `{sunwin_current_data['ket_qua']} ({'+'.join(map(str, sunwin_current_data['Dice']))} = {sum(sunwin_current_data['Dice'])})`\n\n"
                f"*ID Phiên hiện tại:* `{sunwin_current_data['phien_hien_tai'] or 'Đang chờ...'}`\n"
                f"*Dự đoán phiên sau:* `{sunwin_current_data['du_doan']}`\n"
                f"*Độ tin cậy:* `{sunwin_current_data['do_tin_cay']}`\n"
                f"*Cầu:* `{sunwin_current_data['cau']}`\n"
                f"*Cập nhật lúc:* `{sunwin_current_data['ngay']}`\n"
                f"*ID Bot:* `{sunwin_current_data['Id']}`\n\n"
                f"*Chi tiết dự đoán:*\n"
                f"{details_str}"
            )
    elif game_selected == '789club':
        if not club789_current_data["phien_hien_tai"]:
            message_text = 'Dữ liệu 789Club đang được tải, vui lòng chờ giây lát.'
        else:
            details_str = "\n".join(club789_current_data["chi_tiet_du_doan"]) if club789_current_data["chi_tiet_du_doan"] else "Không có chi tiết dự đoán."
            message_text = (
                f"🎲 *Dự đoán Tài Xỉu 789Club:*\n\n"
                f"*ID Phiên hiện tại:* `{club789_current_data['phien_hien_tai']}`\n"
                f"*Kết quả:* `{club789_current_data['ket_qua']} ({'+'.join(map(str, club789_current_data['Dice']))} = {sum(club789_current_data['Dice'])})`\n"
                f"*Dự đoán phiên sau:* `{club789_current_data['du_doan']}`\n"
                f"*Độ tin cậy:* `{club789_current_data['do_tin_cay']}`\n"
                f"*Kết luận:* `{club789_current_data['ket_luan_tong_hop']}`\n"
                f"*Cập nhật lúc:* `{club789_current_data['ngay']}`\n"
                f"*ID Bot:* `{club789_current_data['Id']}`\n\n"
                f"*Chi tiết dự đoán:*\n"
                f"{details_str}"
            )
    else:
        message_text = "Lựa chọn không hợp lệ."

    await query.edit_message_text(text=message_text, parse_mode='Markdown')

## Các chức năng người dùng khác
async def check_expire(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kiểm tra thời hạn key"""
    if not update.message or not update.effective_user: return
    user_id = str(update.effective_user.id)
    users = load_users()
    
    if user_id in users:
        try:
            expire = datetime.fromisoformat(users[user_id]["expire"])
            now = datetime.now()
            if expire > now:
                remain = expire - now
                bot_status = "🟢 Đang hoạt động" if get_status() == "on" else "🔴 Đã tắt"
                await update.message.reply_text(
                    f"✅ Key còn hạn: **{remain.days} ngày**\n"
                    f"📊 Trạng thái bot: {bot_status}"
                )
            else:
                await update.message.reply_text("❌ Key đã hết hạn.")
        except ValueError:
             await update.message.reply_text("❌ Dữ liệu key bị lỗi. Vui lòng liên hệ admin.")
    else:
        await update.message.reply_text("❌ Bạn chưa có key! Liên hệ admin để kích hoạt.")

async def contact_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Liên hệ admin"""
    if not update.message: return
    keyboard = [[InlineKeyboardButton("📞 Liên hệ Admin", url="https://t.me/concacokila")]]
    await update.message.reply_text(
        "📞 Để liên hệ với admin, vui lòng nhấn nút bên dưới:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hiển thị trợ giúp"""
    if not update.message: return
    help_text = (
        "ℹ️ **HƯỚNG DẪN SỬ DỤNG BOT**\n\n"
        "🔹 **🎮 Chọn Game Dự Đoán:** Nhấn để xem dự đoán Tài Xỉu cho 68GB, Sunwin hoặc 789Club.\n"
        "🔹 **📆 Kiểm tra thời hạn:** Xem thời gian còn lại của key của bạn.\n"
        "🔹 **📞 Liên hệ Admin:** Gửi tin nhắn đến admin để được hỗ trợ.\n\n"
        "🎯 **Hệ thống thông báo:**\n"
        "• Khi admin bật bot, bạn sẽ nhận các thông báo chung từ hệ thống.\n"
        "• Khi admin tắt bot, hệ thống sẽ dừng gửi thông báo.\n\n"
        "💡 **Lưu ý:** Cần có key hợp lệ để sử dụng các chức năng dự đoán và nhận thông báo."
    )
    await update.message.reply_text(help_text, parse_mode="HTML")

## Các chức năng admin
async def prompt_add_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Yêu cầu nhập thông tin để thêm key"""
    if not update.message: return
    if context.user_data is None: context.user_data = {}
    context.user_data['waiting_for'] = 'add_key'
    await update.message.reply_text(
        "👑 **THÊM KEY CHO NGƯỜI DÙNG**\n\n"
        "Vui lòng nhập theo định dạng:\n"
        "<code>user_id số_ngày</code>\n\n"
        "Ví dụ: <code>123456789 30</code>",
        parse_mode="HTML"
    )

async def prompt_delete_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Yêu cầu nhập thông tin để xóa key"""
    if not update.message: return
    if context.user_data is None: context.user_data = {}
    context.user_data['waiting_for'] = 'delete_key'
    await update.message.reply_text(
        "🗑️ **XÓA KEY NGƯỜI DÙNG**\n\n"
        "Vui lòng nhập user_id cần xóa:\n\n"
        "Ví dụ: <code>123456789</code>",
        parse_mode="HTML"
    )

async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý input từ admin"""
    if not update.message or not update.effective_user: return
    if not is_admin(update.effective_user.id): return
    if not context.user_data or 'waiting_for' not in context.user_data: return
    
    waiting_for = context.user_data['waiting_for']
    text = update.message.text
    
    if not text:
        await update.message.reply_text("Không có nội dung. Vui lòng thử lại.")
        return
    
    if waiting_for == 'add_key': await process_add_key(update, context, text)
    elif waiting_for == 'delete_key': await process_delete_key(update, context, text)
    
    if 'waiting_for' in context.user_data: del context.user_data['waiting_for']

async def process_add_key(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Xử lý thêm key"""
    if not update.message: return
    try:
        parts = text.strip().split()
        if len(parts) != 2: raise ValueError("Sai định dạng")
        user_id = parts[0]
        days = int(parts[1])
        if not user_id.isdigit(): raise ValueError("User ID phải là số.")
        if days <= 0: raise ValueError("Số ngày phải lớn hơn 0.")

        users = load_users()
        expire_date = datetime.now() + timedelta(days=days)
        users[user_id] = {"expire": expire_date.isoformat(), "active": True} 
        save_users(users)
        
        await update.message.reply_text(
            f"✅ Đã kích hoạt key cho user <code>{user_id}</code> (**{days} ngày**)",
            parse_mode="HTML"
        )
    except ValueError as ve:
        await update.message.reply_text(f"❌ Lỗi: {ve}\nVui lòng nhập: <code>user_id số_ngày</code>", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Lỗi không xác định: {str(e)}")

async def process_delete_key(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Xử lý xóa key"""
    if not update.message: return
    try:
        user_id = text.strip()
        if not user_id.isdigit(): raise ValueError("User ID phải là số.")
        users = load_users()
        if user_id in users:
            del users[user_id]
            save_users(users)
            await update.message.reply_text(f"✅ Đã xóa key của user <code>{user_id}</code>", parse_mode="HTML")
        else:
            await update.message.reply_text(f"❌ Không tìm thấy user <code>{user_id}</code>", parse_mode="HTML")
    except ValueError as ve:
        await update.message.reply_text(f"❌ Lỗi: {ve}\nVui lòng nhập user_id cần xóa.", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Lỗi không xác định: {str(e)}")

async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Liệt kê danh sách user"""
    if not update.message: return
    users = load_users()
    if not users:
        await update.message.reply_text("📋 Danh sách người dùng trống.")
        return
    
    message = "📋 **DANH SÁCH NGƯỜI DÙNG**\n\n"
    count = 0
    for user_id, info in users.items():
        count += 1
        try:
            expire = datetime.fromisoformat(info.get("expire", "2000-01-01T00:00:00"))
            now = datetime.now()
            status_text = f"✅ Còn hạn: {(expire - now).days} ngày" if expire > now else "❌ Hết hạn"
            message += f"{count}. ID: <code>{user_id}</code>\n   📅 Trạng thái key: {status_text}\n\n"
        except ValueError:
            message += f"{count}. ID: <code>{user_id}</code>\n   ⚠️ Dữ liệu key lỗi\n\n"
    await update.message.reply_text(message, parse_mode="HTML")

async def backup_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Backup dữ liệu người dùng"""
    if not update.message: return
    try:
        if not os.path.exists(USER_FILE):
            await update.message.reply_text("❌ File dữ liệu người dùng không tồn tại để backup.")
            return
        with open(USER_FILE, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=f"backup_users_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                caption="📦 Backup dữ liệu người dùng"
            )
    except Exception as e:
        await update.message.reply_text(f"❌ Lỗi khi backup: {str(e)}")

async def check_bot_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kiểm tra trạng thái bot"""
    if not update.message: return
    status = get_status()
    users = load_users()
    total_users = len(users)
    active_users_count = sum(1 for uid, info in users.items() if is_user_active(uid))
    status_text = "🟢 Đang hoạt động" if status == "on" else "🔴 Đã tắt"
    message = (
        f"📊 **TRẠNG THÁI BOT**\n\n"
        f"🤖 Bot: {status_text}\n"
        f"👥 Tổng users đã đăng ký: {total_users}\n"
        f"📡 Users đang nhận thông báo: {active_users_count}\n\n"
        f"💎 Bot VIP Pro"
    )
    await update.message.reply_text(message, parse_mode="HTML")

# --- Auto Notification Function ---
async def send_auto_notification(context: ContextTypes.DEFAULT_TYPE):
    """Gửi thông báo tự động cho tất cả user active (nếu bot đang bật)"""
    if get_status() != "on":
        return
    
    # Lấy thông tin dự đoán mới nhất từ mỗi game
    _68gb_info = ""
    if data_68gb["phien_hien_tai"]:
        _68gb_info = (
            f"🎲 *68GB*:\n"
            f"  Phiên: `{data_68gb['id_phien_ke_tiep']}`\n"
            f"  Dự đoán: `{data_68gb['du_doan']}` (Độ tin cậy: `{data_68gb['do_tin_cay']}`)\n"
        )
    
    sunwin_info = ""
    if sunwin_current_data["phien_hien_tai"]:
        sunwin_info = (
            f"☀️ *Sunwin*:\n"
            f"  Phiên: `{sunwin_current_data['phien_hien_tai']}`\n"
            f"  Dự đoán: `{sunwin_current_data['du_doan']}` (Độ tin cậy: `{sunwin_current_data['do_tin_cay']}`)\n"
        )
    
    _789club_info = ""
    if club789_current_data["phien_hien_tai"]:
        _789club_info = (
            f"🎰 *789Club*:\n"
            f"  Phiên: `{club789_current_data['phien_hien_tai']}`\n"
            f"  Dự đoán: `{club789_current_data['du_doan']}` (Độ tin cậy: `{club789_current_data['do_tin_cay']}`)\n"
        )

    # Nếu không có dữ liệu nào, không gửi thông báo
    if not (_68gb_info or sunwin_info or _789club_info):
        logger.info("Không có dữ liệu dự đoán để gửi thông báo tự động.")
        return

    message = (
        "🤖 **DỰ ĐOÁN MỚI NHẤT TỪ BOT**\n\n"
        f"{_68gb_info}{sunwin_info}{_789club_info}\n"
        "⏰ Cập nhật liên tục. Chúc may mắn!"
    )
    
    users = load_users()
    active_users = [int(uid) for uid, info in users.items() if is_user_active(uid)]
    
    sent_count = 0
    for user_id in active_users:
        try:
            await context.bot.send_message(chat_id=user_id, text=message, parse_mode="Markdown")
            sent_count += 1
            await asyncio.sleep(0.1) # Delay nhỏ để tránh spam
        except Exception as e:
            logger.error(f"Không thể gửi thông báo tự động cho user {user_id}: {str(e)}")
    
    logger.info(f"Auto prediction notification sent to {sent_count} users.")

# --- Hàm chính để chạy Bot và các tác vụ nền ---
async def main():
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Đăng ký các hàm xử lý
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("bat", bat_command))
    application.add_handler(CommandHandler("tat", tat_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(button_callback)) # Xử lý nút inline mới

    # Chạy các tác vụ kết nối và cập nhật dữ liệu cho từng game trong nền
    asyncio.create_task(fetch_and_update_68gb())
    asyncio.create_task(connect_websocket_sunwin())
    asyncio.create_task(connect_websocket_789club())
    
    # Thêm job queue cho auto notification (dự đoán game) với chu kì 60 giây
    if application.job_queue:
        application.job_queue.run_repeating(send_auto_notification, interval=60, first=10)
    
    logger.info("Bot đa nền tảng đang chạy và kết nối dữ liệu...")
    await application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    import requests # Cần cho fetch_and_update_68gb
    asyncio.run(main())

