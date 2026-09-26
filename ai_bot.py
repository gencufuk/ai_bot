import ccxt.async_support as ccxt
import pandas as pd
import asyncio
import pandas_ta as ta
import redis
import time
import aiohttp
import json
import sys
import datetime
import os
import traceback
from dotenv import load_dotenv  
# .env dosyasındaki verileri sisteme yükle
load_dotenv()

# --- 1. AYARLAR (CORE V18.3 AI ENSEMBLE PRO) ---
# Keyler artık güvenli bir şekilde .env dosyasından çekiliyor
API_KEY = os.getenv('BINANCE_API_KEY')
SECRET_KEY = os.getenv('BINANCE_SECRET_KEY')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# .env eksikse anlaşılmaz API hataları yerine açılışta net mesajla dur
_eksik = [n for n, v in {'BINANCE_API_KEY': API_KEY, 'BINANCE_SECRET_KEY': SECRET_KEY,
                         'TELEGRAM_TOKEN': TELEGRAM_TOKEN, 'TELEGRAM_CHAT_ID': TELEGRAM_CHAT_ID}.items() if not v]
if _eksik:
    print(f"🚨 KRİTİK: .env dosyasında eksik değişkenler: {', '.join(_eksik)}")
    sys.exit(1)

SABIT_KASA = 20.0          
BALINA_KASA = 40.0         

ZARAR_ORANI_NORMAL = 0.030 
ZARAR_ORANI_BALINA = 0.020 

MIN_HACIM_USDT = 12000000  
MAX_BEKLEME_SAATI = 4.0    
MIN_BEKLENTI_ORANI = 0.005  
DUST_THRESHOLD_USDT = 1.0  
ORPHAN_THRESHOLD_USDT = 10.0 

# Binance Spot Komisyon Oranı (Alış %0.1 + Satış %0.1 = %0.2 Ortalama)
FEE_RATE = 0.001 

AI_MIN_OLASILIK = 0.65
FILTRE_MAX_RISK = 0.45

# GÖLGE MOD: True iken AI skorları hesaplanıp CSV'ye yazılır ama işlem BLOKLANMAZ.
# Model, trainer'da AUC eşiğini geçen bir sürüm çıkana kadar bu modda kalmalı.
AI_GOLGE_MOD = False
SHADOW_LOG_ARALIGI = 900   # aynı sembol için shadow sinyal kaydı en az 15 dk arayla

# --- REJİM (YATAY/TREND) AYARLARI ---
ADX_TREND_ESIK = 20.0      # BTC 15m ADX bunun altındaysa piyasa YATAY sayılır: yeni alım açılmaz (sinyal shadow'a yazılır)
BTC_HISTEREZIS = 0.004     # BTC/EMA200 kararında %0.4'lük bant: whipsaw (sürekli ONAY/BEKLE) önleme
MOMENTUM_OLU_SAAT = 1.5    # yatay rejimde pozisyon bu süre boyunca ±%1 bandında sıkışıp hacim de söndüyse çık

# --- V18.3 KÂR KORUMA AYARLARI ---
# Veri: +%2.5'i görüp geri gelen pozisyonlar (eski veride 73, Zek'in haftasında 11 işlem) hep ~0'da
# kapanıyordu çünkü stop sadece +%0.2'ye çekiliyordu. Artık kâr kilitlenir.
KAR_KILIDI_ORAN = 0.010    # max kâr %2.5'i geçince stop +%1.0'a çekilir (eski: +%0.2)
MAX_ATR_PCT = 0.03         # ATR/fiyat bunun üstündeki coinlere girilmez (eski 0.04; -%5'lik stopların kaynağı)

YASAKLI_COINLER = ['WLD', 'SPK', '币安', '币安人生', 'XUSD', 'XAUT', 'FDUSD', 'USDC', 'PAXG', 'TUSD', 'EUR', 'GBP', 'DAI', 'TRY', 'TRX', 'BUSD', 'USDP', 'RLUSD']

db = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
exchange = ccxt.binance({
    'apiKey': API_KEY, 
    'secret': SECRET_KEY, 
    'enableRateLimit': True,
    'options': {'defaultType': 'spot', 'adjustForTimeDifference': True}
})

RAW_TICKERS, RADAR_USDT = {}, []
BTC_OK = False
PIYASA_DURUM = "❌ BEKLE"
BTC_1H_DEGISIM = 0.0   # BTC'nin son 1 saatlik % değişimi (radar_loop günceller)
BTC_EMA_UZAKLIK = 0.0  # BTC'nin 15m EMA200'e % uzaklığı (radar_loop günceller)
BTC_ADX = 0.0          # BTC 15m ADX(14) — trend gücü (radar_loop günceller)
REJIM = "YATAY"        # "TREND" | "YATAY" — ilk ölçüm gelene kadar muhafazakâr başlangıç
PREF = "PORTFOY"

# --- YAPAY ZEKA MODELLERİ YÜKLEME MEKANİZMASI ---
AI_MODEL = None
FILTER_MODEL = None

if os.path.exists('core_xgboost_model.json'):
    try:
        import xgboost as xgb
        AI_MODEL = xgb.XGBClassifier()
        AI_MODEL.load_model('core_xgboost_model.json')
    except Exception as e:
        print(f"❌ AI Modeli Yüklenemedi: {e}")
        AI_MODEL = None

if os.path.exists('filter_model.json'):
    try:
        import xgboost as xgb
        FILTER_MODEL = xgb.XGBClassifier()
        FILTER_MODEL.load_model('filter_model.json')
    except:
        FILTER_MODEL = None

def debug_log(mesaj):
    if AI_GOLGE_MOD: ai_status = "[GÖLGE-MOD]"
    elif AI_MODEL is not None: ai_status = "[AI-AKTİF]"
    else: ai_status = "[VERİ-TOPLAMA]"
    print(f"[{time.strftime('%H:%M:%S')}] [CORE V18.3] {ai_status} {mesaj}")
    sys.stdout.flush()

def get_fill_price(order, fallback):
    g = order.get('average')
    if not g:
        fills = order.get('fills', []) or []
        t_mik = sum(f['amount'] for f in fills)
        if t_mik > 0:
            g = sum(f['price'] * f['amount'] for f in fills) / t_mik
    return g if g else fallback

def net_kar_hesapla(giris, cikis, maliyet_usdt):
    # Alış + satış komisyonu dahil net kâr; oran, vip döngüsündeki 'oran' formülüyle birebir aynı
    coins = maliyet_usdt / giris
    net = coins * (cikis * (1 - FEE_RATE) - giris * (1 + FEE_RATE))
    oran = (cikis * (1 - FEE_RATE) - giris * (1 + FEE_RATE)) / (giris * (1 + FEE_RATE))
    return net, oran

# V2 CSV: sinyal anında toplanan genişletilmiş feature'lar (ai_metrics anahtarı -> CSV kolonu)
V2_FEATURE_MAP = {
    'pump_3s': 'Pump_3s', 'pump_6s': 'Pump_6s', 'zirve_uzaklik': 'Zirve_Uzaklik',
    'ema1h_uzaklik': 'EMA1h_Uzaklik', 'ema15m_uzaklik': 'EMA15m_Uzaklik',
    'btc_1h_degisim': 'BTC_1h_Degisim', 'btc_ema_uzaklik': 'BTC_EMA_Uzaklik',
    'saat': 'Saat', 'gun': 'Gun', 'stop_sayisi': 'Stop_Sayisi',
    'sym_adx': 'Sym_ADX', 'btc_adx': 'BTC_ADX',
    'kapali_mum_onay': 'Kapali_Mum_Onay', 'ob_oran': 'OB_Oran',
    'ai_score': 'AI_Skor', 'filter_score': 'Filtre_Skor'
}
V1_COLUMNS = ['Islem_Zamani', 'Sembol', 'Sinyal', 'Kasa_Tipi', 'Giris_RSI', 'Giris_Vol_Oran', 'Giris_ATR_Pct', 'Giris_Fiyat', 'Cikis_Fiyat', 'Kar_Orani', 'Net_Kar_USDT', 'Cikis_Tipi', 'Sure_Saat']

def _append_csv(file_name, row_dict, columns):
    df = pd.DataFrame([row_dict]).reindex(columns=columns)
    if not os.path.isfile(file_name):
        df.to_csv(file_name, index=False, header=True); return
    # Şema değiştiyse (yeni feature kolonu eklendiyse) dosyayı yeni başlığa taşı:
    # eski satırlar yeni kolonlarda boş kalır, başlık-satır kayması yaşanmaz.
    try:
        with open(file_name, encoding='utf-8') as f: mevcut = f.readline().strip().split(',')
        if mevcut != columns:
            pd.read_csv(file_name).reindex(columns=columns).to_csv(file_name, index=False, header=True)
            debug_log(f"🧬 CSV şeması güncellendi: {os.path.basename(file_name)} ({len(mevcut)}→{len(columns)} kolon)")
    except Exception as e:
        # Dosya okunamıyorsa (başlık-satır uyumsuzluğu vb.) bozuk dosyaya eklemeye devam etme:
        # kenara al, temiz dosya aç. Kenara alınan dosya elle onarılabilir.
        bozuk = f"{file_name}.bozuk_{int(time.time())}"
        os.replace(file_name, bozuk)
        debug_log(f"⚠️ CSV bozuk görünüyor, {os.path.basename(bozuk)} olarak kenara alındı; temiz dosya açılıyor ({e})")
        df.to_csv(file_name, index=False, header=True); return
    df.to_csv(file_name, mode='a', index=False, header=False)

def save_trade_to_csv(trade_data, ai=None):
    try:
        abs_path = os.path.dirname(os.path.abspath(__file__))
        # V1: eski trainer/analizlerle uyumluluk için aynen yazılmaya devam eder
        _append_csv(os.path.join(abs_path, 'core_islem_verileri.csv'), trade_data, V1_COLUMNS)
        # V2: genişletilmiş feature'larla başlıklı yeni dosya
        ai = ai or {}
        v2 = dict(trade_data)
        for k, col in V2_FEATURE_MAP.items(): v2[col] = ai.get(k)
        _append_csv(os.path.join(abs_path, 'core_islem_verileri_v2.csv'), v2, V1_COLUMNS + list(V2_FEATURE_MAP.values()))
    except Exception as e:
        debug_log(f"⚠️ CSV Kayıt Hatası: {e}")

def save_shadow_to_csv(sym, sebep, m):
    """Kural filtresini geçen ama pozisyona dönüşmeyen sinyalleri kaydeder.
    Amaç: sadece girilen işlemlerden öğrenme yanlılığını (selection bias) kırmak.
    shadow_labeler.py bu kayıtları sonradan sanal sonuçla etiketler."""
    try:
        if (time.time() - float(db.hget(f"{PREF}:shadow_son", sym) or 0)) < SHADOW_LOG_ARALIGI: return
        db.hset(f"{PREF}:shadow_son", sym, str(time.time()))
        abs_path = os.path.dirname(os.path.abspath(__file__))
        row = {'Ts': int(time.time() * 1000), 'Sinyal_Zamani': str(datetime.datetime.now()),
               'Sembol': sym, 'Sebep': sebep, 'Sinyal': m.get('signal'), 'Fiyat': m.get('fiyat'),
               'Giris_RSI': m.get('rsi'), 'Giris_Vol_Oran': m.get('vol_ratio'), 'Giris_ATR_Pct': m.get('atr_pct')}
        for k, col in V2_FEATURE_MAP.items(): row[col] = m.get(k)
        columns = ['Ts', 'Sinyal_Zamani', 'Sembol', 'Sebep', 'Sinyal', 'Fiyat',
                   'Giris_RSI', 'Giris_Vol_Oran', 'Giris_ATR_Pct'] + list(V2_FEATURE_MAP.values())
        _append_csv(os.path.join(abs_path, 'shadow_sinyaller.csv'), row, columns)
    except Exception as e:
        debug_log(f"⚠️ Shadow CSV Kayıt Hatası: {e}")

async def telegram_mesaj_gonder(session, m):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        async with session.get(url, params={'chat_id': TELEGRAM_CHAT_ID, 'text': m, 'parse_mode': 'Markdown'}, timeout=10) as resp:
            data = await resp.json()
            if not data.get('ok', True): 
                debug_log(f"❌ TELEGRAM GÖNDERME HATASI: {data.get('description')}")
            return data
    except Exception as e: 
        debug_log(f"❌ TELEGRAM BAĞLANTI HATASI: {e}")

def add_to_total_profit(amount):
    try:
        current = db.get(f"{PREF}:toplam_kar")
        current_float = 0.0 if current is None else float(current)
        new_total = current_float + float(amount)
        db.set(f"{PREF}:toplam_kar", str(new_total))
        return new_total
    except Exception as e:
        debug_log(f"⚠️ Toplam kâr Redis'e yazılamadı: {e}")
        return amount

async def check_wallet_sync(session):
    # KRİTİK: RAW_TICKERS henüz dolmadıysa fiyatlar 0 döner ve tüm pozisyon
    # kayıtları "toz" sanılıp silinir. Ticker verisi gelmeden tarama yapma.
    if not RAW_TICKERS:
        debug_log("⏳ Ticker verisi henüz hazır değil, cüzdan taraması ertelendi.")
        return False
    debug_log("🔄 Cüzdan Taraması...")
    try:
        bal_data = await exchange.fetch_balance()
        cuzdan = {c: float(v) for c, v in bal_data.get('total', {}).items() if float(v or 0) > 0}
        is_list = db.hgetall(f"{PREF}:islem_listesi") or {}
        for sym in list(is_list.keys()):
            coin = sym.split('/')[0]
            fiyat = RAW_TICKERS.get(sym, {}).get('last') or 0
            if fiyat <= 0: continue  # fiyatı bilinmeyen pozisyona dokunma
            if (cuzdan.get(coin, 0) * fiyat) < DUST_THRESHOLD_USDT:
                for k in [":islem_listesi", ":max_karlar", ":islem_miktarlari", ":half_sold", ":ai_data", ":giris_zamanlari", ":realize_karlar", ":last_rsi_check", ":last_mom_check", ":stop_counts"]: db.hdel(f"{PREF}{k}", sym)
        for coin, miktar in cuzdan.items():
            sym = f"{coin}/USDT"
            if any(y in sym for y in YASAKLI_COINLER) or sym in is_list or coin in ["USDT", "BNB"]: continue
            fiyat = RAW_TICKERS.get(sym, {}).get('last', 0)
            if fiyat > 0 and (miktar * fiyat) > ORPHAN_THRESHOLD_USDT:
                db.hset(f"{PREF}:islem_listesi", sym, str(fiyat)); db.hset(f"{PREF}:islem_miktarlari", sym, str(miktar * fiyat)); db.hset(f"{PREF}:giris_zamanlari", sym, str(time.time()))
        return True
    except Exception as e:
        debug_log(f"⚠️ Cüzdan Tarama Hatası: {e}")
        return False

async def analyze_market(sym):
    """Kural filtresini geçen sinyalin tüm feature'larını ve AI skorlarını döndürür.
    ALIM/RED KARARI BURADA VERİLMEZ — karar radar_loop'tadır. Böylece reddedilen
    sinyaller de feature'larıyla shadow CSV'ye yazılabilir."""
    try:
        # 1. MTF Analizi: 1 saatlik grafikte trend yönü kontrolü
        ohlcv_1h = await exchange.fetch_ohlcv(sym, '1h', limit=50)
        df_1h = pd.DataFrame(ohlcv_1h, columns=['ts','o','h','l','c','v'])
        ema_20_1h = ta.ema(df_1h['c'], length=20).iloc[-1]

        ohlcv = await exchange.fetch_ohlcv(sym, '15m', limit=100)
        df = pd.DataFrame(ohlcv, columns=['ts','o','h','l','c','v'])
        if len(df) < 30: return None  # yeni listelenen coin: feature'lar için yetersiz geçmiş
        df['rsi'], df['atr'] = ta.rsi(df['c'], length=14), ta.atr(df['h'], df['l'], df['c'], length=14)

        rsi_val, atr_val, price = df['rsi'].iloc[-1], df['atr'].iloc[-1], df['c'].iloc[-1]

        # 1H trend filtresi: Eğer fiyat 1 Saatlik EMA20'nin altındaysa risklidir, girme.
        if price < ema_20_1h: return None

        if (atr_val / price) > MAX_ATR_PCT: return None
        msb = price > df['h'].iloc[-10:-2].max()
        eng = df['c'].iloc[-2] < df['o'].iloc[-2] and df['c'].iloc[-1] > df['o'].iloc[-1] and df['o'].iloc[-1] < df['c'].iloc[-2]
        vol, av_v = df['v'].iloc[-1], df['v'].rolling(14).mean().iloc[-1]
        vol_ratio = vol / av_v if av_v > 0 else 0

        if not (rsi_val > 55 and vol_ratio > 2.5 and (msb or eng)): return None

        is_whale = (vol_ratio > 3.5) and (rsi_val < 65)
        atr_pct_val = (atr_val / price) * 100

        # --- GENİŞLETİLMİŞ FEATURE SETİ (V2 CSV + shadow kaydı için) ---
        simdi = datetime.datetime.now()
        ema_20_15m = ta.ema(df['c'], length=20).iloc[-1]
        try:
            sym_adx = float(ta.adx(df['h'], df['l'], df['c'], length=14)['ADX_14'].iloc[-1])
        except Exception:
            sym_adx = 0.0
        zirve_24h = df['h'].iloc[-96:].max()
        pump_3s_ref = df['c'].iloc[-13] if len(df) >= 13 else df['c'].iloc[0]
        pump_6s_ref = df['c'].iloc[-25] if len(df) >= 25 else df['c'].iloc[0]
        # Ölçüm feature'ları (henüz karar vermiyor, sadece kaydediliyor):
        # - Kapanmış mum teyidi: bir önceki TAMAMLANMIŞ mum yeşil ve hacmi ortalamanın üstünde mü?
        #   (canlı mumla üretilen sinyalin fakeout payını ölçmek için)
        kapali_mum_onay = int(df['c'].iloc[-2] > df['o'].iloc[-2] and df['v'].iloc[-2] > av_v)
        # - Emir defteri dengesi: alıcı/satıcı USDT hacmi (ilk 20 kademe); >1 alıcı baskın
        try:
            ob = await exchange.fetch_order_book(sym, limit=20)
            bid_v = sum(p * q for p, q in ob['bids']); ask_v = sum(p * q for p, q in ob['asks'])
            ob_oran = float(bid_v / ask_v) if ask_v > 0 else 0.0
        except Exception:
            ob_oran = 0.0
        metrics = {
            "signal": "MSB" if msb else "Engulf",
            "fiyat": float(price),
            "rsi": float(rsi_val),
            "vol_ratio": float(vol_ratio),
            "atr_pct": float(atr_pct_val),
            "is_whale": bool(is_whale),
            "pump_3s": float((price / pump_3s_ref - 1) * 100),          # son 3 saatlik yükseliş (pump gecikmesi)
            "pump_6s": float((price / pump_6s_ref - 1) * 100),          # son 6 saatlik yükseliş
            "zirve_uzaklik": float((zirve_24h - price) / zirve_24h * 100) if zirve_24h > 0 else 0.0,
            "ema1h_uzaklik": float((price / ema_20_1h - 1) * 100),
            "ema15m_uzaklik": float((price / ema_20_15m - 1) * 100) if ema_20_15m > 0 else 0.0,
            "btc_1h_degisim": float(BTC_1H_DEGISIM),
            "btc_ema_uzaklik": float(BTC_EMA_UZAKLIK),
            "saat": simdi.hour,
            "gun": simdi.weekday(),
            "stop_sayisi": int(db.hget(f"{PREF}:stop_counts", sym) or 0),
            "sym_adx": float(sym_adx),
            "btc_adx": float(BTC_ADX),
            "kapali_mum_onay": kapali_mum_onay,
            "ob_oran": ob_oran,
            "ai_score": None,
            "filter_score": None,
        }

        # AI skorları: hesaplanır ve kaydedilir; bloklamayı radar_loop yapar (gölge modda hiç yapmaz)
        sig_enc = 1 if msb else (2 if eng else 0)
        if AI_MODEL is not None:
            try:
                feat_main = pd.DataFrame([[float(rsi_val), float(vol_ratio), float(atr_pct_val), sig_enc]],
                                         columns=['Giris_RSI', 'Giris_Vol_Oran', 'Giris_ATR_Pct', 'Sinyal_Encoded'])
                metrics["ai_score"] = float(AI_MODEL.predict_proba(feat_main)[0][1])
            except Exception as e:
                debug_log(f"⚠️ AI Model Tahmin Hatası ({sym}): {e}")
        if FILTER_MODEL is not None:
            try:
                feat_f = pd.DataFrame([[float(rsi_val), float(vol_ratio), float(atr_pct_val)]],
                                      columns=['Giris_RSI', 'Giris_Vol_Oran', 'Giris_ATR_Pct'])
                metrics["filter_score"] = float(FILTER_MODEL.predict_proba(feat_f)[0][1])
            except Exception as e:
                debug_log(f"⚠️ Filtre Model Tahmin Hatası ({sym}): {e}")

        return metrics
    except Exception as e:
        debug_log(f"⚠️ Analiz Hatası ({sym}): {e}")
        return None

async def telegram_handler():
    global RAW_TICKERS, RADAR_USDT, BTC_OK, PIYASA_DURUM
    last_id = 0
    debug_log("📞 Telegram dinleyicisi başlatıldı...")
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
                params = {'offset': last_id + 1, 'timeout': 10}
                async with session.get(url, params=params, timeout=15) as resp: 
                    r = await resp.json()

                if r.get('result'):
                    for u in r['result']:
                        last_id = u['update_id']
                        message = u.get('message', {})
                        # Sadece yetkili sohbetten gelen komutları işle
                        if str(message.get('chat', {}).get('id', '')) != str(TELEGRAM_CHAT_ID): continue
                        cmd = message.get('text', '').lower()
                        
                        if cmd == "/durum":
                            try:
                                is_list = db.hgetall(f"{PREF}:islem_listesi") or {}
                                if AI_GOLGE_MOD: ai_msg = "👁️ **Gölge Mod** (AI skor kaydediyor, bloklamıyor)"
                                elif AI_MODEL is not None: ai_msg = "🤖 **Yapay Zekâ Entegreli**"
                                else: ai_msg = "📝 **Veri Toplama Modunda**"
                                msg = f"🏢 **AI SNIPER & MOON BAG (V18.3 PRO)**\n🧠 Durum: `{ai_msg}`\n💼 **Aktif Fon:** `{(await exchange.fetch_balance())['total'].get('USDT', 0):.2f} USDT`\n📊 **Piyasa:** `{PIYASA_DURUM}`\n\n"
                                if not is_list: msg += "💤 _Pozisyon yok._"
                                for s, a_str in is_list.items():
                                    a, tick = float(a_str), RAW_TICKERS.get(s, {}).get('last', float(a_str))
                                    i_mik = float(db.hget(f"{PREF}:islem_miktarlari", s) or SABIT_KASA)
                                    # Kâr hesaplamasına tahmini borsa komisyonu eklendi
                                    kz = (((tick * (1 - FEE_RATE)) - (a * (1 + FEE_RATE))) / a) * 100
                                    m_k, yari_satildi = float(db.hget(f"{PREF}:max_karlar", s) or 0.0) * 100, db.hget(f"{PREF}:half_sold", s) == "1"
                                    w_icon = " 🐋" if i_mik > 30 else ""
                                    msg += (f"{'🟢' if kz >= 0 else '🔴'} **{s}**{' (💎)' if yari_satildi else ''}{w_icon}\n🛒 `{a:.4f} ➔ {tick:.4f}`\n💸 Anlık: `{((tick - a) * (i_mik / a)):+.2f} USDT` (Net: %{kz:.2f})\n⏳ `{(time.time() - float(db.hget(f'{PREF}:giris_zamanlari', s) or time.time())) / 3600:.1f}s` | Max: `%{m_k:.2f}`\n➖➖➖➖\n")
                                await telegram_mesaj_gonder(session, msg)
                            except Exception as e:
                                await telegram_mesaj_gonder(session, f"⚠️ /durum verisi alınırken hata: {str(e)}")

                        elif cmd == "/kar": 
                            try:
                                toplam_kar = db.get(f"{PREF}:toplam_kar")
                                kar_val = float(toplam_kar) if toplam_kar else 0.0
                                ai_info = "\n*(Yapay zekâ filtrelemesi devrededir)*" if AI_MODEL is not None else ""
                                await telegram_mesaj_gonder(session, f"📊 **KÜMÜLATİF KÂR**: `{kar_val:.2f} USDT`{ai_info}")
                            except Exception as e:
                                await telegram_mesaj_gonder(session, f"⚠️ Kâr verisi alınırken hata: {str(e)}")

            except Exception as e:
                # last_id SIFIRLANMAZ: sıfırlanırsa Telegram'daki eski komutlar tekrar işlenir
                debug_log(f"⚠️ Telegram Dinleyici Hatası: {e}")
                await asyncio.sleep(5)
            await asyncio.sleep(1)

async def vip_cuzdan_loop():
    debug_log("🛡️ Moon Bag & Risk Koruması Aktif")
    last_sync = 0
    async with aiohttp.ClientSession() as t_session:
        while True:
            try:
                if (time.time() - last_sync) > 1800:
                    if await check_wallet_sync(t_session): last_sync = time.time()
                is_list = db.hgetall(f"{PREF}:islem_listesi") or {}
                if not is_list: await asyncio.sleep(2); continue
                vip_tickers = await exchange.fetch_tickers(list(is_list.keys()))

                for sym, a_str in list(is_list.items()):
                    if not (tick := vip_tickers.get(sym, {}).get('last')): continue
                    a, m_k = float(a_str), float(db.hget(f"{PREF}:max_karlar", sym) or 0.0)
                    
                    # Net Oran (Komisyon Düşülmüş) Hesaplaması Eklendi
                    oran = ((tick * (1 - FEE_RATE)) - (a * (1 + FEE_RATE))) / (a * (1 + FEE_RATE))
                    
                    if oran > m_k: db.hset(f"{PREF}:max_karlar", sym, str(oran)); m_k = oran
                    half_sold, g_zaman = db.hget(f"{PREF}:half_sold", sym) == "1", float(db.hget(f"{PREF}:giris_zamanlari", sym) or time.time())
                    gecen_saat, is_exit, exit_msg = (time.time() - g_zaman) / 3600, False, ""
                    i_mik = float(db.hget(f"{PREF}:islem_miktarlari", sym) or SABIT_KASA)

                    try: ai = json.loads(db.hget(f"{PREF}:ai_data", sym) or "{}")
                    except: ai = {}
                    is_w = ai.get("is_whale", False)
                    kasa_tipi = "BALİNA" if is_w else "NORMAL"

                    atr_pct = float(ai.get("atr_pct", 2.5))
                    dinamik_stop = max(0.025, min(0.055, (atr_pct * 1.5) / 100))
                    aktif_zarar_orani = ZARAR_ORANI_BALINA if is_w else dinamik_stop

                    # ATR bazlı ilk kâr kademesi (~2xATR): sakin coinde ulaşılabilir hedef,
                    # oynak coinde erken tıraşlamayı önleyen geniş hedef (eski sabit %4 yerine)
                    ilk_esik = max(0.03, min(0.06, (atr_pct * 2.0) / 100))

                    # KÂR KİLİDİ: +%2.5 görüldüyse stop +%1.0'a çekilir; kâr geri verilmez
                    if m_k >= 0.025: base_stop = KAR_KILIDI_ORAN
                    else: base_stop = -aktif_zarar_orani

                    rsi_vurkac_tetiklendi = False
                    if oran >= 0.05 and not half_sold and (time.time() - float(db.hget(f"{PREF}:last_rsi_check", sym) or 0)) > 60:
                        try:
                            ohlcv_o = await exchange.fetch_ohlcv(sym, '15m', limit=25)
                            df_o = pd.DataFrame(ohlcv_o, columns=['ts','o','h','l','c','v'])
                            if ta.rsi(df_o['c'], length=14).iloc[-1] >= 83: rsi_vurkac_tetiklendi = True
                            db.hset(f"{PREF}:last_rsi_check", sym, str(time.time()))
                        except Exception as e:
                            debug_log(f"⚠️ RSI Kontrol Hatası ({sym}): {e}")

                    # 🚀 MOON BAG MANTIĞI: %50'sini Sat
                    if rsi_vurkac_tetiklendi and not half_sold:
                        bal = await exchange.fetch_balance()
                        f_bal = bal.get(sym.split('/')[0], {}).get('free', 0)
                        satilacak_miktar = float(exchange.amount_to_precision(sym, f_bal / 2))
                        
                        if (satilacak_miktar * tick) > 5.1:
                            try:
                                order = await exchange.create_market_sell_order(sym, satilacak_miktar)
                                g_satis = get_fill_price(order, tick)

                                db.hset(f"{PREF}:half_sold", sym, "1")
                                net_kd, g_oran = net_kar_hesapla(a, g_satis, i_mik / 2)
                                
                                add_to_total_profit(net_kd); db.hset(f"{PREF}:realize_karlar", sym, str(net_kd)); db.hset(f"{PREF}:islem_miktarlari", sym, str(i_mik / 2))
                                save_trade_to_csv({"Islem_Zamani": str(datetime.datetime.now()), "Sembol": sym, "Sinyal": ai.get("signal"), "Kasa_Tipi": kasa_tipi, "Giris_RSI": ai.get("rsi"), "Giris_Vol_Oran": ai.get("vol_ratio"), "Giris_ATR_Pct": ai.get("atr_pct"), "Giris_Fiyat": a, "Cikis_Fiyat": g_satis, "Kar_Orani": g_oran*100, "Net_Kar_USDT": net_kd, "Cikis_Tipi": "🚀 MOON BAG (%50 VURKAÇ)", "Sure_Saat": round(gecen_saat, 2)}, ai)
                                await telegram_mesaj_gonder(t_session, f"🚀 **MOON BAG (RSI ŞİŞTİ)**: {sym}\n🛒 `{a:.4f} ➔ {g_satis:.4f}`\n💸 Cebe: `+{net_kd:.2f} USDT` (%{g_oran*100:.2f})\n*💎 Kalan %50 ile trend takip ediliyor!*")
                            except Exception as e:
                                debug_log(f"⚠️ MOON BAG Satış Hatası ({sym}): {e}")
                            continue

                    # 🎯 DİNAMİK MAKAS (ilk kademe ATR'a göre ölçekli)
                    if m_k >= 0.20: kismi, cikis = m_k - 0.05, max(base_stop, m_k - 0.10)
                    elif m_k >= 0.10: kismi, cikis = m_k - 0.02, max(base_stop, m_k - 0.05)
                    elif m_k >= ilk_esik: kismi, cikis = m_k - 0.015, max(base_stop, m_k - 0.03)
                    else: kismi, cikis = 999.0, (0.005 if half_sold else base_stop)

                    # NORMAL KISMİ KÂR
                    if m_k >= ilk_esik and oran <= kismi and not half_sold and not rsi_vurkac_tetiklendi:
                        bal = await exchange.fetch_balance()
                        f_bal = bal.get(sym.split('/')[0], {}).get('free', 0)
                        satilacak_miktar = float(exchange.amount_to_precision(sym, f_bal / 2))
                        
                        if (satilacak_miktar * tick) > 5.1:
                            try:
                                order = await exchange.create_market_sell_order(sym, satilacak_miktar)
                                g_satis = get_fill_price(order, tick)

                                db.hset(f"{PREF}:half_sold", sym, "1")
                                net_kd, g_oran = net_kar_hesapla(a, g_satis, i_mik / 2)
                                
                                add_to_total_profit(net_kd); db.hset(f"{PREF}:realize_karlar", sym, str(net_kd)); db.hset(f"{PREF}:islem_miktarlari", sym, str(i_mik / 2))
                                save_trade_to_csv({"Islem_Zamani": str(datetime.datetime.now()), "Sembol": sym, "Sinyal": ai.get("signal"), "Kasa_Tipi": kasa_tipi, "Giris_RSI": ai.get("rsi"), "Giris_Vol_Oran": ai.get("vol_ratio"), "Giris_ATR_Pct": ai.get("atr_pct"), "Giris_Fiyat": a, "Cikis_Fiyat": g_satis, "Kar_Orani": g_oran*100, "Net_Kar_USDT": net_kd, "Cikis_Tipi": "DİNAMİK KISMİ KÂR", "Sure_Saat": round(gecen_saat, 2)}, ai)
                                await telegram_mesaj_gonder(t_session, f"💰 **DİNAMİK KISMİ KÂR**: {sym}\n🛒 `{a:.4f} ➔ {g_satis:.4f}`\n💸 Cebe: `+{net_kd:.2f} USDT` (%{g_oran*100:.2f})")
                            except Exception as e:
                                debug_log(f"⚠️ KISMİ KAR Satış Hatası ({sym}): {e}")
                            continue

                    # TAM ÇIKIŞ SİNYALLERİ (Stop-Loss vb.)
                    if oran <= cikis:
                        if m_k >= ilk_esik: exit_msg = "📈 TREND TAKİPLİ ÇIKIŞ"
                        elif half_sold: exit_msg = "🛡️ GÜVENLİ ÇIKIŞ"
                        elif m_k >= 0.025: exit_msg = "🔒 KÂR KİLİDİ (+%1)"
                        else: exit_msg = f"🛑 STOP LOSS (%-{aktif_zarar_orani*100:.1f})"
                        is_exit = True
                    elif gecen_saat >= MAX_BEKLEME_SAATI and not half_sold:
                        if oran < MIN_BEKLENTI_ORANI: is_exit, exit_msg = True, "⏳ ZAMAN AŞIMI"
                        else: db.hset(f"{PREF}:giris_zamanlari", sym, str(time.time()))
                    elif (REJIM == "YATAY" and not half_sold and gecen_saat >= MOMENTUM_OLU_SAAT
                          and abs(oran) < 0.01
                          and (time.time() - float(db.hget(f"{PREF}:last_mom_check", sym) or 0)) > 600):
                        # 💤 MOMENTUM ÖLDÜ (sadece YATAY rejimde devrede): pozisyon 1.5 saattir
                        # girişin ±%1 bandında sıkışmış VE hacim sönmüşse 4 saati bekleme, çık.
                        # Trend rejiminde bu kural HİÇ çalışmaz.
                        try:
                            ohlcv_m = await exchange.fetch_ohlcv(sym, '15m', limit=20)
                            df_m = pd.DataFrame(ohlcv_m, columns=['ts','o','h','l','c','v'])
                            vol_son3 = float(df_m['v'].iloc[-3:].mean())
                            vol_ort = float(df_m['v'].rolling(14).mean().iloc[-1])
                            db.hset(f"{PREF}:last_mom_check", sym, str(time.time()))
                            if vol_ort > 0 and vol_son3 < vol_ort:
                                is_exit, exit_msg = True, "💤 MOMENTUM ÖLDÜ (YATAY REJİM)"
                        except Exception as e:
                            debug_log(f"⚠️ Momentum Kontrol Hatası ({sym}): {e}")

                    if is_exit:
                        f_bal = (await exchange.fetch_balance()).get(sym.split('/')[0], {}).get('free', 0)
                        satilacak_miktar = float(exchange.amount_to_precision(sym, f_bal))
                        
                        if (satilacak_miktar * tick) > 5.1:
                            try:
                                order = await exchange.create_market_sell_order(sym, satilacak_miktar)
                                g_satis = get_fill_price(order, tick)

                                net_kd, g_oran = net_kar_hesapla(a, g_satis, i_mik)
                                add_to_total_profit(net_kd)
                                
                                save_trade_to_csv({"Islem_Zamani": str(datetime.datetime.now()), "Sembol": sym, "Sinyal": ai.get("signal"), "Kasa_Tipi": kasa_tipi, "Giris_RSI": ai.get("rsi"), "Giris_Vol_Oran": ai.get("vol_ratio"), "Giris_ATR_Pct": ai.get("atr_pct"), "Giris_Fiyat": a, "Cikis_Fiyat": g_satis, "Kar_Orani": g_oran*100, "Net_Kar_USDT": net_kd, "Cikis_Tipi": exit_msg, "Sure_Saat": round(gecen_saat, 2)}, ai)

                                if "STOP" in exit_msg:
                                    count = int(db.hget(f"{PREF}:stop_counts", sym) or 0) + 1
                                    if count >= 2:
                                        db.hset(f"{PREF}:kara_liste", sym, str(time.time() + 86400)); db.hdel(f"{PREF}:stop_counts", sym)
                                        await telegram_mesaj_gonder(t_session, f"🚫 **KARA LİSTE**: {sym} peş peşe 2 kez stop ettirdi. 24 Saat uzak durulacak.")
                                    else: db.hset(f"{PREF}:stop_counts", sym, str(count))
                                elif "TREND" in exit_msg or "VURKAÇ" in exit_msg or "KISMİ" in exit_msg or "MOON" in exit_msg:
                                    db.hdel(f"{PREF}:stop_counts", sym)

                                await telegram_mesaj_gonder(t_session, f"{'🟢' if net_kd >= 0 else '🔴'} **{exit_msg}**: {sym}\n🛒 `{a:.4f} ➔ {g_satis:.4f}`\n💸 Net: `{net_kd:+.2f} USDT` (Net: %{g_oran*100:.2f})")
                                
                                db.hset(f"{PREF}:cooldowns", sym, str(time.time() + 3600))
                                for k in [":islem_listesi", ":max_karlar", ":islem_miktarlari", ":half_sold", ":ai_data", ":giris_zamanlari", ":realize_karlar", ":last_rsi_check", ":last_mom_check"]: db.hdel(f"{PREF}{k}", sym)
                                
                            except Exception as e:
                                debug_log(f"⚠️ TAM ÇIKIŞ Hatası ({sym}): {e}")
                await asyncio.sleep(2)
            except Exception as e: 
                debug_log(f"⚠️ Risk Hatası (Genel): {e}")
                await asyncio.sleep(5)

async def radar_loop():
    global RAW_TICKERS, RADAR_USDT, BTC_OK, PIYASA_DURUM, BTC_1H_DEGISIM, BTC_EMA_UZAKLIK, BTC_ADX, REJIM
    async with aiohttp.ClientSession() as t_session:
        while True:
            try:
                RAW_TICKERS = await exchange.fetch_tickers()
                try:
                    df_btc = pd.DataFrame(await exchange.fetch_ohlcv('BTC/USDT', '15m', limit=300), columns=['ts','o','h','l','c','v'])
                    cur_btc, last_ema = float(df_btc['c'].iloc[-1]), float(ta.ema(df_btc['c'], length=200).iloc[-1])
                    BTC_1H_DEGISIM = float((df_btc['c'].iloc[-1] / df_btc['c'].iloc[-5] - 1) * 100)
                    BTC_EMA_UZAKLIK = float((cur_btc / last_ema - 1) * 100) if last_ema > 0 else 0.0

                    # REJİM TESPİTİ: ADX düşükse piyasa yönsüz (YATAY) demektir
                    try:
                        BTC_ADX = float(ta.adx(df_btc['h'], df_btc['l'], df_btc['c'], length=14)['ADX_14'].iloc[-1])
                    except Exception:
                        BTC_ADX = 0.0
                    REJIM = "TREND" if BTC_ADX >= ADX_TREND_ESIK else "YATAY"

                    recent_high = df_btc['h'].iloc[-3:].max()
                    flash_crash = ((recent_high - cur_btc) / recent_high) > 0.015

                    if flash_crash:
                        BTC_OK, PIYASA_DURUM = False, "🚨 ANİ ÇÖKÜŞ (KORUMA)"
                    else:
                        # HİSTEREZİS: EMA200'ün %0.4 üstünde ONAY'a geç, %0.4 altında BEKLE'ye dön;
                        # aradaki bantta son durum korunur (EMA çevresi testeresinde sürekli mod değişimi biter)
                        if cur_btc > last_ema * (1 + BTC_HISTEREZIS): BTC_OK = True
                        elif cur_btc < last_ema * (1 - BTC_HISTEREZIS): BTC_OK = False
                        PIYASA_DURUM = ("✅ ONAY" if BTC_OK else "❌ BEKLE") + (" | TREND" if REJIM == "TREND" else " | YATAY")

                    debug_log(f"🔍 BTC: {cur_btc:.1f} | EMA200: {last_ema:.1f} | ADX: {BTC_ADX:.1f} | DURUM: {PIYASA_DURUM}")
                except Exception as e:
                    debug_log(f"⚠️ BTC Kontrol Hatası: {e}")
                    BTC_OK = False

                res = [{'s': s, 'c': float(d.get('percentage', 0))} for s, d in RAW_TICKERS.items() if all(ord(c) < 128 for c in s) and not any(y in s for y in YASAKLI_COINLER) and s.endswith('/USDT') and float(d.get('quoteVolume', 0)) > MIN_HACIM_USDT]
                RADAR_USDT = [x['s'] for x in sorted(res, key=lambda x: x['c'], reverse=True)[:10]]
                if BTC_OK:
                    is_list = db.hgetall(f"{PREF}:islem_listesi") or {}
                    alim_yapildi = False
                    for sym in RADAR_USDT:
                        if time.time() < float(db.hget(f"{PREF}:cooldowns", sym) or 0.0) or sym in is_list: continue
                        if float(db.hget(f"{PREF}:kara_liste", sym) or 0.0) > time.time(): continue

                        ai_metrics = await analyze_market(sym)
                        if not ai_metrics: continue

                        # --- AI KARARI ---
                        # Gölge modda skor sadece loglanır/kaydedilir, işlem BLOKLANMAZ.
                        ai_s, f_s = ai_metrics.get("ai_score"), ai_metrics.get("filter_score")
                        if not AI_GOLGE_MOD and AI_MODEL is not None:
                            if ai_s is None or ai_s < AI_MIN_OLASILIK or (f_s is not None and f_s > FILTRE_MAX_RISK):
                                kar_txt = f"%{ai_s*100:.1f}" if ai_s is not None else "hesaplanamadı"
                                risk_txt = f" | Zarar Riski: %{f_s*100:.1f}" if f_s is not None else ""
                                debug_log(f"🛡️ AI Filtre Reddetti: {sym} | Kâr İht: {kar_txt}{risk_txt}")
                                save_shadow_to_csv(sym, "AI_RED", ai_metrics)
                                continue
                        elif AI_GOLGE_MOD and ai_s is not None:
                            risk_txt = f" | Zarar Riski: %{f_s*100:.1f}" if f_s is not None else ""
                            debug_log(f"👁️ Gölge AI Skoru: {sym} | Kâr İht: %{ai_s*100:.1f}{risk_txt}")

                        # YATAY REJİM FİLTRESİ: alım açılmaz, sinyal shadow'a yazılır.
                        # Filtrenin gerçek maliyeti/kazancı labeler'ın sanal sonuçlarıyla ölçülecek.
                        if REJIM == "YATAY":
                            debug_log(f"😴 Yatay Rejim, alım pas geçildi: {sym} (BTC ADX: {BTC_ADX:.1f})")
                            save_shadow_to_csv(sym, "REJIM_YATAY", ai_metrics)
                            continue

                        # Döngü başına tek alım kuralı: alınmayan diğer sinyaller shadow'a yazılır
                        if alim_yapildi:
                            save_shadow_to_csv(sym, "TEK_ALIM_KURALI", ai_metrics)
                            continue

                        is_w = ai_metrics.get("is_whale", False)
                        aktif_kasa = BALINA_KASA if is_w else SABIT_KASA

                        # 'free' bakiye: açık emirlerde kilitli USDT sayılmaz; %1 pay slipaj/komisyon için
                        if (await exchange.fetch_balance())['free'].get("USDT", 0) < aktif_kasa * 1.01:
                            save_shadow_to_csv(sym, "BAKIYE_YETERSIZ", ai_metrics)
                            continue
                        try:
                            ask = (await exchange.fetch_order_book(sym, limit=5))['asks'][0][0]

                            # Borsa kurallarına göre miktar yuvarlama (Precision Format)
                            raw_amount = aktif_kasa / ask
                            formatted_amount = float(exchange.amount_to_precision(sym, raw_amount))

                            if formatted_amount * ask > 5.1: # Binance Min Emir Tutarı (Notional) Kontrolü
                                order = await exchange.create_market_buy_order(sym, formatted_amount)
                                gerceklesen_fiyat = get_fill_price(order, ask)

                                db.hset(f"{PREF}:islem_listesi", sym, str(gerceklesen_fiyat))
                                db.hset(f"{PREF}:islem_miktarlari", sym, str(formatted_amount * gerceklesen_fiyat))
                                db.hset(f"{PREF}:max_karlar", sym, "0.0")
                                db.hset(f"{PREF}:giris_zamanlari", sym, str(time.time()))
                                db.hset(f"{PREF}:ai_data", sym, json.dumps(ai_metrics))

                                msg = f"🟢 **YENİ POZİSYON**: {sym}\nFiyat: `{gerceklesen_fiyat:.4f}`\nKasa: `{formatted_amount * gerceklesen_fiyat:.2f} USDT`"
                                if ai_s is not None: msg += f"\n🧠 AI Skoru: %{ai_s*100:.1f}" + (" _(gölge mod)_" if AI_GOLGE_MOD else "")
                                if is_w: msg += "\n🐋 **BALİNA TESPİT EDİLDİ!**"
                                await telegram_mesaj_gonder(t_session, msg)
                                alim_yapildi = True
                        except Exception as e:
                            debug_log(f"⚠️ Alım Emri Başarısız ({sym}): {e}")
                await asyncio.sleep(15) 
            except Exception as e: debug_log(f"⚠️ Radar Hatası: {e}"); await asyncio.sleep(30)

async def start():
    # Redis yoksa bot sessizce garip davranmasın, açılışta net hata ver
    try:
        db.ping()
    except Exception as e:
        debug_log(f"🚨 KRİTİK HATA: Redis'e bağlanılamadı (localhost:6379): {e}")
        sys.exit(1)

    if AI_MODEL is None:
        if AI_GOLGE_MOD:
            debug_log("ℹ️ Model dosyası yok ama GÖLGE MOD açık: bot kural tabanlı çalışıp veri toplayacak.")
        else:
            debug_log("🚨 KRİTİK HATA: core_xgboost_model.json bulunamadı veya hatalı!")
            debug_log("🚨 AI bloklama modu (AI_GOLGE_MOD=False) için model dosyası ZORUNLUDUR.")
            sys.exit(1) # Model yoksa çalışmayı durdurarak random işlem yapmasını engeller

    # Lot ve fiyat hesaplamaları için piyasa metrikleri önceden yüklenir
    await exchange.load_markets()
    try:
        await asyncio.gather(radar_loop(), vip_cuzdan_loop(), telegram_handler())
    finally:
        await exchange.close()

if __name__ == "__main__":
    for h in YASAKLI_COINLER:
        for k in [":islem_listesi", ":max_karlar", ":half_sold", ":ai_data", ":islem_miktarlari", ":giris_zamanlari", ":realize_karlar", ":last_rsi_check"]:
            try: db.hdel(f"{PREF}{k}", f"{h}/USDT")
            except: pass
    debug_log("🏢 CORE V18.3 PRO YAPAY ZEKÂ DESTEKLİ SNIPER AKTİF!")
    try: asyncio.run(start())
    except KeyboardInterrupt: pass
