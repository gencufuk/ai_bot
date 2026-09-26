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

# --- 1. AYARLAR (CORE V18.0.2 AI ENSEMBLE PRO) ---
# Keyler artık güvenli bir şekilde .env dosyasından çekiliyor
API_KEY = os.getenv('BINANCE_API_KEY')
SECRET_KEY = os.getenv('BINANCE_SECRET_KEY')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

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
    ai_status = "[AI-AKTİF]" if AI_MODEL is not None else "[VERİ-TOPLAMA]"
    print(f"[{time.strftime('%H:%M:%S')}] [CORE V18.0.2] {ai_status} {mesaj}")
    sys.stdout.flush()

def save_trade_to_csv(trade_data):
    try:
        abs_path = os.path.dirname(os.path.abspath(__file__))
        file_name = os.path.join(abs_path, 'core_islem_verileri.csv')
        columns = ['Islem_Zamani', 'Sembol', 'Sinyal', 'Kasa_Tipi', 'Giris_RSI', 'Giris_Vol_Oran', 'Giris_ATR_Pct', 'Giris_Fiyat', 'Cikis_Fiyat', 'Kar_Orani', 'Net_Kar_USDT', 'Cikis_Tipi', 'Sure_Saat']
        df = pd.DataFrame([trade_data]).reindex(columns=columns)
        if not os.path.isfile(file_name): df.to_csv(file_name, index=False, header=True)
        else: df.to_csv(file_name, mode='a', index=False, header=False)
    except Exception as e:
        debug_log(f"⚠️ CSV Kayıt Hatası: {e}")

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
    except: return amount

async def check_wallet_sync(session):
    debug_log("🔄 Cüzdan Taraması...")
    try:
        bal_data = await exchange.fetch_balance()
        cuzdan = {c: float(v) for c, v in bal_data.get('total', {}).items() if float(v or 0) > 0}
        is_list = db.hgetall(f"{PREF}:islem_listesi") or {}
        for sym in list(is_list.keys()):
            coin = sym.split('/')[0]
            if (cuzdan.get(coin, 0) * RAW_TICKERS.get(sym, {}).get('last', 0)) < DUST_THRESHOLD_USDT:
                for k in [":islem_listesi", ":max_karlar", ":islem_miktarlari", ":half_sold", ":ai_data", ":giris_zamanlari", ":realize_karlar"]: db.hdel(f"{PREF}{k}", sym)
        for coin, miktar in cuzdan.items():
            sym = f"{coin}/USDT"
            if any(y in sym for y in YASAKLI_COINLER) or sym in is_list or coin in ["USDT", "BNB"]: continue
            fiyat = RAW_TICKERS.get(sym, {}).get('last', 0)
            if fiyat > 0 and (miktar * fiyat) > ORPHAN_THRESHOLD_USDT:
                db.hset(f"{PREF}:islem_listesi", sym, str(fiyat)); db.hset(f"{PREF}:islem_miktarlari", sym, str(miktar * fiyat)); db.hset(f"{PREF}:giris_zamanlari", sym, str(time.time()))
    except Exception as e:
        debug_log(f"⚠️ Cüzdan Tarama Hatası: {e}")

async def analyze_market(sym):
    try:
        # 1. MTF (Multi-Timeframe) Analizi: 1 Saatlik Grafikte Trend Yönü Kontrolü (YENİ EKLENDİ)
        ohlcv_1h = await exchange.fetch_ohlcv(sym, '1h', limit=50)
        df_1h = pd.DataFrame(ohlcv_1h, columns=['ts','o','h','l','c','v'])
        ema_20_1h = ta.ema(df_1h['c'], length=20).iloc[-1]
        
        ohlcv = await exchange.fetch_ohlcv(sym, '15m', limit=100)
        df = pd.DataFrame(ohlcv, columns=['ts','o','h','l','c','v'])
        df['rsi'], df['atr'] = ta.rsi(df['c'], length=14), ta.atr(df['h'], df['l'], df['c'], length=14)

        rsi_val, atr_val, price = df['rsi'].iloc[-1], df['atr'].iloc[-1], df['c'].iloc[-1]
        
        # 1H trend filtresi: Eğer fiyat 1 Saatlik EMA20'nin altındaysa risklidir, girme.
        if price < ema_20_1h: return None

        if (atr_val / price) > 0.04: return None 
        msb = price > df['h'].iloc[-10:-2].max()
        eng = df['c'].iloc[-2] < df['o'].iloc[-2] and df['c'].iloc[-1] > df['o'].iloc[-1] and df['o'].iloc[-1] < df['c'].iloc[-2]
        vol, av_v = df['v'].iloc[-1], df['v'].rolling(14).mean().iloc[-1]
        vol_ratio = vol / av_v if av_v > 0 else 0

        if rsi_val > 55 and vol_ratio > 2.5 and (msb or eng):
            is_whale = (vol_ratio > 3.5) and (rsi_val < 65)
            atr_pct_val = (atr_val / price) * 100

            ai_score = 1.0
            if AI_MODEL is not None and FILTER_MODEL is not None:
                try:
                    sig_enc = 1 if msb else (2 if eng else 0)
                    feat_df = pd.DataFrame([[float(rsi_val), float(vol_ratio), float(atr_pct_val)]], 
                                           columns=['Giris_RSI', 'Giris_Vol_Oran', 'Giris_ATR_Pct'])
                    
                    feat_main = feat_df.copy()
                    feat_main['Sinyal_Encoded'] = sig_enc
                    
                    ai_score = float(AI_MODEL.predict_proba(feat_main)[0][1])
                    filter_score = float(FILTER_MODEL.predict_proba(feat_df)[0][1])

                    if ai_score < AI_MIN_OLASILIK or filter_score > 0.45:
                        debug_log(f"🛡️ AI Filtre Reddetti: {sym} | Kâr İht: %{ai_score*100:.1f} | Zarar Riski: %{filter_score*100:.1f}")
                        return None
                except Exception as e:
                    debug_log(f"⚠️ AI Model Tahmin Hatası: {e}")
                    return None # Hata varsa işlemi reddet (Güvenlik Önlemi)

            elif AI_MODEL is not None: 
                try:
                    sig_enc = 1 if msb else (2 if eng else 0)
                    feat_df = pd.DataFrame([[float(rsi_val), float(vol_ratio), float(atr_pct_val), sig_enc]], 
                                           columns=['Giris_RSI', 'Giris_Vol_Oran', 'Giris_ATR_Pct', 'Sinyal_Encoded'])
                    ai_score = float(AI_MODEL.predict_proba(feat_df)[0][1])
                    if ai_score < AI_MIN_OLASILIK:
                        return None
                except: return None

            return {
                "signal": "MSB" if msb else "Engulf", 
                "rsi": float(rsi_val), 
                "vol_ratio": float(vol_ratio), 
                "atr_pct": float(atr_pct_val), 
                "is_whale": bool(is_whale),
                "ai_score": float(ai_score)
            }
        return None
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
                        cmd = message.get('text', '').lower()
                        
                        if cmd == "/durum":
                            try:
                                is_list = db.hgetall(f"{PREF}:islem_listesi") or {}
                                ai_msg = "🤖 **Yapay Zekâ Entegreli**" if AI_MODEL is not None else "📝 **Veri Toplama Modunda**"
                                msg = f"🏢 **AI SNIPER & MOON BAG (V18.0.2 PRO)**\n🧠 Durum: `{ai_msg}`\n💼 **Aktif Fon:** `{(await exchange.fetch_balance())['total'].get('USDT', 0):.2f} USDT`\n📊 **Piyasa:** `{PIYASA_DURUM}`\n\n"
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
                last_id = 0
                await asyncio.sleep(5)
            await asyncio.sleep(1)

async def vip_cuzdan_loop():
    debug_log("🛡️ Moon Bag & Risk Koruması Aktif")
    last_sync = 0
    async with aiohttp.ClientSession() as t_session:
        while True:
            try:
                if (time.time() - last_sync) > 1800: await check_wallet_sync(t_session); last_sync = time.time()
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

                    if m_k >= 0.025: base_stop = 0.002 
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
                                # GERÇEK SATIŞ FİYATINI BULMA EKLENDİ
                                g_satis = order.get('average')
                                fills = order.get('fills', [])
                                if (not g_satis or g_satis == 0) and fills:
                                    t_mik = sum([f['amount'] for f in fills])
                                    if t_mik > 0: g_satis = sum([f['price'] * f['amount'] for f in fills]) / t_mik
                                if not g_satis or g_satis == 0: g_satis = tick

                                db.hset(f"{PREF}:half_sold", sym, "1")
                                net_kd = (g_satis - a) * ((i_mik / 2) / a)
                                g_oran = (g_satis - a) / a
                                
                                add_to_total_profit(net_kd); db.hset(f"{PREF}:realize_karlar", sym, str(net_kd)); db.hset(f"{PREF}:islem_miktarlari", sym, str(i_mik / 2))
                                save_trade_to_csv({"Islem_Zamani": str(datetime.datetime.now()), "Sembol": sym, "Sinyal": ai.get("signal"), "Kasa_Tipi": kasa_tipi, "Giris_RSI": ai.get("rsi"), "Giris_Vol_Oran": ai.get("vol_ratio"), "Giris_ATR_Pct": ai.get("atr_pct"), "Giris_Fiyat": a, "Cikis_Fiyat": g_satis, "Kar_Orani": g_oran*100, "Net_Kar_USDT": net_kd, "Cikis_Tipi": "🚀 MOON BAG (%50 VURKAÇ)", "Sure_Saat": round(gecen_saat, 2)})
                                await telegram_mesaj_gonder(t_session, f"🚀 **MOON BAG (RSI ŞİŞTİ)**: {sym}\n🛒 `{a:.4f} ➔ {g_satis:.4f}`\n💸 Cebe: `+{net_kd:.2f} USDT` (%{g_oran*100:.2f})\n*💎 Kalan %50 ile trend takip ediliyor!*")
                            except Exception as e:
                                debug_log(f"⚠️ MOON BAG Satış Hatası ({sym}): {e}")
                            continue

                    # 🎯 DİNAMİK MAKAS
                    if m_k >= 0.20: kismi, cikis = m_k - 0.05, max(base_stop, m_k - 0.10)
                    elif m_k >= 0.10: kismi, cikis = m_k - 0.02, max(base_stop, m_k - 0.05)
                    elif m_k >= 0.04: kismi, cikis = m_k - 0.015, max(base_stop, m_k - 0.03)
                    else: kismi, cikis = 999.0, (0.005 if half_sold else base_stop)

                    # NORMAL KISMİ KÂR
                    if m_k >= 0.04 and oran <= kismi and not half_sold and not rsi_vurkac_tetiklendi:
                        bal = await exchange.fetch_balance()
                        f_bal = bal.get(sym.split('/')[0], {}).get('free', 0)
                        satilacak_miktar = float(exchange.amount_to_precision(sym, f_bal / 2))
                        
                        if (satilacak_miktar * tick) > 5.1:
                            try:
                                order = await exchange.create_market_sell_order(sym, satilacak_miktar)
                                # GERÇEK SATIŞ FİYATINI BULMA EKLENDİ
                                g_satis = order.get('average')
                                fills = order.get('fills', [])
                                if (not g_satis or g_satis == 0) and fills:
                                    t_mik = sum([f['amount'] for f in fills])
                                    if t_mik > 0: g_satis = sum([f['price'] * f['amount'] for f in fills]) / t_mik
                                if not g_satis or g_satis == 0: g_satis = tick

                                db.hset(f"{PREF}:half_sold", sym, "1")
                                net_kd = (g_satis - a) * ((i_mik / 2) / a)
                                g_oran = (g_satis - a) / a
                                
                                add_to_total_profit(net_kd); db.hset(f"{PREF}:realize_karlar", sym, str(net_kd)); db.hset(f"{PREF}:islem_miktarlari", sym, str(i_mik / 2))
                                save_trade_to_csv({"Islem_Zamani": str(datetime.datetime.now()), "Sembol": sym, "Sinyal": ai.get("signal"), "Kasa_Tipi": kasa_tipi, "Giris_RSI": ai.get("rsi"), "Giris_Vol_Oran": ai.get("vol_ratio"), "Giris_ATR_Pct": ai.get("atr_pct"), "Giris_Fiyat": a, "Cikis_Fiyat": g_satis, "Kar_Orani": g_oran*100, "Net_Kar_USDT": net_kd, "Cikis_Tipi": "DİNAMİK KISMİ KÂR", "Sure_Saat": round(gecen_saat, 2)})
                                await telegram_mesaj_gonder(t_session, f"💰 **DİNAMİK KISMİ KÂR**: {sym}\n🛒 `{a:.4f} ➔ {g_satis:.4f}`\n💸 Cebe: `+{net_kd:.2f} USDT` (%{g_oran*100:.2f})")
                            except Exception as e:
                                debug_log(f"⚠️ KISMİ KAR Satış Hatası ({sym}): {e}")
                            continue

                    # TAM ÇIKIŞ SİNYALLERİ (Stop-Loss vb.)
                    if oran <= cikis: 
                        if m_k >= 0.04: exit_msg = "📈 TREND TAKİPLİ ÇIKIŞ"
                        elif half_sold: exit_msg = "🛡️ GÜVENLİ ÇIKIŞ"
                        elif m_k >= 0.025: exit_msg = "🛡️ BAŞA BAŞ KORUMASI"
                        else: exit_msg = f"🛑 STOP LOSS (%-{aktif_zarar_orani*100:.1f})"
                        is_exit = True
                    elif gecen_saat >= MAX_BEKLEME_SAATI and not half_sold:
                        if oran < MIN_BEKLENTI_ORANI: is_exit, exit_msg = True, "⏳ ZAMAN AŞIMI"
                        else: db.hset(f"{PREF}:giris_zamanlari", sym, str(time.time()))

                    if is_exit:
                        f_bal = (await exchange.fetch_balance()).get(sym.split('/')[0], {}).get('free', 0)
                        satilacak_miktar = float(exchange.amount_to_precision(sym, f_bal))
                        
                        if (satilacak_miktar * tick) > 5.1:
                            try:
                                order = await exchange.create_market_sell_order(sym, satilacak_miktar)
                                
                                # GERÇEK SATIŞ FİYATINI BULMA EKLENDİ
                                g_satis = order.get('average')
                                fills = order.get('fills', [])
                                if (not g_satis or g_satis == 0) and fills:
                                    t_mik = sum([f['amount'] for f in fills])
                                    if t_mik > 0: g_satis = sum([f['price'] * f['amount'] for f in fills]) / t_mik
                                if not g_satis or g_satis == 0: g_satis = tick

                                net_kd = (g_satis - a) * (i_mik / a)
                                g_oran = (g_satis - a) / a
                                add_to_total_profit(net_kd)
                                
                                save_trade_to_csv({"Islem_Zamani": str(datetime.datetime.now()), "Sembol": sym, "Sinyal": ai.get("signal"), "Kasa_Tipi": kasa_tipi, "Giris_RSI": ai.get("rsi"), "Giris_Vol_Oran": ai.get("vol_ratio"), "Giris_ATR_Pct": ai.get("atr_pct"), "Giris_Fiyat": a, "Cikis_Fiyat": g_satis, "Kar_Orani": g_oran*100, "Net_Kar_USDT": net_kd, "Cikis_Tipi": exit_msg, "Sure_Saat": round(gecen_saat, 2)})

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
                                for k in [":islem_listesi", ":max_karlar", ":islem_miktarlari", ":half_sold", ":ai_data", ":giris_zamanlari", ":realize_karlar", ":last_rsi_check"]: db.hdel(f"{PREF}{k}", sym)
                                
                            except Exception as e:
                                debug_log(f"⚠️ TAM ÇIKIŞ Hatası ({sym}): {e}")
                await asyncio.sleep(2)
            except Exception as e: 
                debug_log(f"⚠️ Risk Hatası (Genel): {e}")
                await asyncio.sleep(5)

async def radar_loop():
    global RAW_TICKERS, RADAR_USDT, BTC_OK, PIYASA_DURUM
    async with aiohttp.ClientSession() as t_session:
        while True:
            try:
                RAW_TICKERS = await exchange.fetch_tickers()
                try:
                    df_btc = pd.DataFrame(await exchange.fetch_ohlcv('BTC/USDT', '15m', limit=300), columns=['ts','o','h','l','c','v'])
                    cur_btc, last_ema = float(df_btc['c'].iloc[-1]), float(ta.ema(df_btc['c'], length=200).iloc[-1])

                    recent_high = df_btc['h'].iloc[-3:].max()
                    flash_crash = ((recent_high - cur_btc) / recent_high) > 0.015 

                    if flash_crash: BTC_OK, PIYASA_DURUM = False, "🚨 ANİ ÇÖKÜŞ (KORUMA)"
                    elif cur_btc > last_ema: BTC_OK, PIYASA_DURUM = True, "✅ ONAY"
                    else: BTC_OK, PIYASA_DURUM = False, "❌ BEKLE"

                    debug_log(f"🔍 BTC: {cur_btc:.1f} | EMA200: {last_ema:.1f} | DURUM: {PIYASA_DURUM}")
                except Exception as e: 
                    debug_log(f"⚠️ BTC Kontrol Hatası: {e}")
                    BTC_OK = False

                res = [{'s': s, 'c': float(d.get('percentage', 0))} for s, d in RAW_TICKERS.items() if all(ord(c) < 128 for c in s) and not any(y in s for y in YASAKLI_COINLER) and s.endswith('/USDT') and float(d.get('quoteVolume', 0)) > MIN_HACIM_USDT]
                RADAR_USDT = [x['s'] for x in sorted(res, key=lambda x: x['c'], reverse=True)[:10]]
                if BTC_OK:
                    is_list = db.hgetall(f"{PREF}:islem_listesi") or {}
                    for sym in RADAR_USDT:
                        if time.time() < float(db.hget(f"{PREF}:cooldowns", sym) or 0.0) or sym in is_list: continue
                        if float(db.hget(f"{PREF}:kara_liste", sym) or 0.0) > time.time(): continue 

                        if (ai_metrics := await analyze_market(sym)):
                            is_w = ai_metrics.get("is_whale", False)
                            aktif_kasa = BALINA_KASA if is_w else SABIT_KASA

                            if (await exchange.fetch_balance())['total'].get("USDT", 0) >= aktif_kasa:
                                try:
                                    ask = (await exchange.fetch_order_book(sym, limit=5))['asks'][0][0]
                                    
                                    # HATA GİDERİLDİ: Borsa kurallarına göre miktar yuvarlama (Precision Format)
                                    raw_amount = aktif_kasa / ask
                                    formatted_amount = float(exchange.amount_to_precision(sym, raw_amount))
                                    
                                    if formatted_amount * ask > 5.1: # Binance Min Emir Tutarı (Notional) Kontrolü
                                        order = await exchange.create_market_buy_order(sym, formatted_amount)
                                        
                                        # HATA GİDERİLDİ: Gerçekleşen fiyattan (Fill Price) maliyet hesaplama
                                        gerceklesen_fiyat = order.get('average')
                                        if not gerceklesen_fiyat or gerceklesen_fiyat == 0:
                                            fills = order.get('fills', [])
                                            if fills:
                                                toplam_odenen = sum([f['price'] * f['amount'] for f in fills])
                                                toplam_miktar = sum([f['amount'] for f in fills])
                                                if toplam_miktar > 0: gerceklesen_fiyat = toplam_odenen / toplam_miktar
                                            if not gerceklesen_fiyat or gerceklesen_fiyat == 0: gerceklesen_fiyat = ask
                                        
                                        db.hset(f"{PREF}:islem_listesi", sym, str(gerceklesen_fiyat)) 
                                        db.hset(f"{PREF}:islem_miktarlari", sym, str(formatted_amount * gerceklesen_fiyat)) 
                                        db.hset(f"{PREF}:max_karlar", sym, "0.0") 
                                        db.hset(f"{PREF}:giris_zamanlari", sym, str(time.time())) 
                                        db.hset(f"{PREF}:ai_data", sym, json.dumps(ai_metrics))

                                        msg = f"🟢 **YENİ POZİSYON**: {sym}\nFiyat: `{gerceklesen_fiyat:.4f}`\nKasa: `{formatted_amount * gerceklesen_fiyat:.2f} USDT`"
                                        if AI_MODEL is not None: msg += f"\n🧠 AI Güven Skoru: %{ai_metrics.get('ai_score', 1.0)*100:.1f}"
                                        if is_w: msg += "\n🐋 **BALİNA TESPİT EDİLDİ!**"
                                        await telegram_mesaj_gonder(t_session, msg)
                                        break
                                except Exception as e:
                                    debug_log(f"⚠️ Alım Emri Başarısız ({sym}): {e}")
                await asyncio.sleep(15) 
            except Exception as e: debug_log(f"⚠️ Radar Hatası: {e}"); await asyncio.sleep(30)

async def start():
    # HATA GİDERİLDİ: Lot ve Fiyat hesaplamaları için piyasa metrikleri önceden yüklendi
    await exchange.load_markets()
    
    if AI_MODEL is None:
        debug_log("🚨 KRİTİK HATA: core_xgboost_model.json bulunamadı veya hatalı!")
        debug_log("🚨 Bot, AI Ensemble yapısında tasarlandığı için model dosyaları ZORUNLUDUR.")
        sys.exit(1) # Model yoksa çalışmayı durdurarak random işlem yapmasını engeller
        
    await asyncio.gather(radar_loop(), vip_cuzdan_loop(), telegram_handler())

if __name__ == "__main__":
    for h in YASAKLI_COINLER:
        for k in [":islem_listesi", ":max_karlar", ":half_sold", ":ai_data", ":islem_miktarlari", ":giris_zamanlari", ":realize_karlar", ":last_rsi_check"]:
            try: db.hdel(f"{PREF}{k}", f"{h}/USDT")
            except: pass
    debug_log("🏢 CORE V18.0.2 PRO YAPAY ZEKÂ DESTEKLİ SNIPER AKTİF!")
    try: asyncio.run(start())
    except KeyboardInterrupt: pass
