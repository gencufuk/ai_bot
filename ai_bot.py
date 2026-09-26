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

from sniper import risk_motoru as risk
from sniper.csv_kayit import satir_ekle
from sniper.model_karti import ModelYuvasi
from sniper.ozellikler import (V2_FEATURE_MAP, SinyalAyarlari, ohlcv_df, ema_son, sinyal_degerlendir,
                               genisletilmis_ozellikler, kapali_mum_hizala)
# .env dosyasındaki verileri sisteme yükle
load_dotenv()

# --- 1. AYARLAR (CORE V18.4 AI ENSEMBLE PRO) ---
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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

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

AI_MIN_OLASILIK = 0.65     # kartsız (legacy) core model eşiği; kartlı modelde eşik karttan gelir
FILTRE_MAX_RISK = 0.45     # kartsız (legacy) filtre modeli eşiği

# GÖLGE MOD: True iken AI skorları hesaplanıp CSV'ye yazılır ama işlem BLOKLANMAZ.
# Model, trainer'da AUC eşiğini geçen bir sürüm çıkana kadar bu modda kalmalı.
# (Bkz. ANALIZ_V18.4.md §2: diskteki iki model de doğrulamadan geçmemiş eski trainer ürünüdür.)
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

# --- V18.4 SAĞLAMLIK AYARLARI ---
MIN_NOTIONAL_USDT = 5.1            # Binance min emir tutarı (+pay)
POZISYON_SENKRON_KORUMA_SN = 600   # cüzdan senkronu bu yaştan genç pozisyonlara dokunmaz (bakiye gecikmesi/yarış)
ZEHIRLI_POZISYON_YAS_SN = 120      # bakiyesi bulunamayan pozisyon ancak bu yaştan büyükse takipten çıkarılır
SINYAL_KAPALI_MUM = False          # True: sinyal son KAPANMIŞ 15m mumla üretilir (repaint yok, backfill ile birebir)
WATCHDOG_VIP_SN = 60               # VIP döngüsü bu süre tur tamamlamazsa Telegram alarmı
WATCHDOG_RADAR_SN = 180
MODEL_KONTROL_SN = 300             # model dosyaları bu aralıkla değişiklik için kontrol edilir (hot reload)
# Bot'un ASLA sahiplenmeyeceği / satmayacağı coinler (elle tutulan bakiyeler): .env -> MANUEL_COINLER=ETH,SOL
MANUEL_COINLER = {c.strip().upper() for c in os.getenv('MANUEL_COINLER', '').split(',') if c.strip()}

YASAKLI_COINLER = ['WLD', 'SPK', '币安', '币安人生', 'XUSD', 'XAUT', 'FDUSD', 'USDC', 'PAXG', 'TUSD', 'EUR', 'GBP', 'DAI', 'TRY', 'TRX', 'BUSD', 'USDP', 'RLUSD']

# Senkron istemci bilinçli olarak korunuyor (localhost'ta çağrı başına ~0.1 ms), ama zaman aşımları
# ŞART: varsayılan socket_timeout=None ile Redis takılırsa (BGSAVE fork, swap) event loop sonsuza
# dek donar ve stop-loss dahil her şey durur. 3 sn sonra hata -> döngü yakalar ve yeniden dener.
db = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True,
                 socket_timeout=3, socket_connect_timeout=3, health_check_interval=30)
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
PIYASA_GENISLIK = None # hacimli USDT paritelerinden 24s pozitif olanların oranı (radar_loop günceller)
REJIM = "YATAY"        # "TREND" | "YATAY" — ilk ölçüm gelene kadar muhafazakâr başlangıç
PREF = "PORTFOY"

# Bir pozisyona ait TÜM Redis alanları. Alımda sıfırlanır, çıkışta tek MULTI/EXEC ile silinir.
POZISYON_ANAHTARLARI = [":islem_listesi", ":max_karlar", ":islem_miktarlari", ":half_sold", ":ai_data",
                        ":giris_zamanlari", ":realize_karlar", ":last_rsi_check", ":last_mom_check",
                        ":zaman_ref", ":adetler"]

RISK_AYAR = risk.RiskAyarlari(fee_rate=FEE_RATE, zarar_orani_balina=ZARAR_ORANI_BALINA,
                              kar_kilidi_oran=KAR_KILIDI_ORAN, max_bekleme_saati=MAX_BEKLEME_SAATI,
                              min_beklenti_orani=MIN_BEKLENTI_ORANI, momentum_olu_saat=MOMENTUM_OLU_SAAT)
SINYAL_AYAR = SinyalAyarlari(max_atr_pct=MAX_ATR_PCT)

# --- YAPAY ZEKA MODELLERİ (hot reload; feature listesi modelin kendisinden okunur) ---
CORE_YUVA = ModelYuvasi(os.path.join(BASE_DIR, 'core_xgboost_model.json'), 'core', AI_MIN_OLASILIK, 'min')
FILTRE_YUVA = ModelYuvasi(os.path.join(BASE_DIR, 'filter_model.json'), 'filtre', FILTRE_MAX_RISK, 'max')

# --- BİLDİRİM KUYRUĞU: risk döngüsü Telegram'ı ASLA beklemez ---
BILDIRIM_KUYRUGU = None
ANA_LOOP = None
_SON_UYARI = {}
SON_TUR = {'vip': time.time(), 'radar': time.time()}


def debug_log(mesaj):
    if AI_GOLGE_MOD: ai_status = "[GÖLGE-MOD]"
    elif CORE_YUVA.yuklu: ai_status = "[AI-AKTİF]"
    else: ai_status = "[VERİ-TOPLAMA]"
    try:
        print(f"[{time.strftime('%H:%M:%S')}] [CORE V18.4] {ai_status} {mesaj}")
        sys.stdout.flush()
    except (OSError, ValueError):
        pass  # stdout kapandıysa (SSH koptu vb.) log yüzünden döngü ölmesin


def _kuyruga_koy(mesaj):
    try:
        BILDIRIM_KUYRUGU.put_nowait(mesaj)
    except asyncio.QueueFull:
        debug_log(f"⚠️ Bildirim kuyruğu dolu, mesaj düştü: {mesaj[:80]}")


def bildir(mesaj):
    """Telegram mesajını kuyruğa atar ve hemen döner. Thread-safe (CSV thread'lerinden de çağrılır)."""
    if BILDIRIM_KUYRUGU is None or ANA_LOOP is None:
        debug_log(f"(bildirim) {mesaj}")
        return
    try:
        ANA_LOOP.call_soon_threadsafe(_kuyruga_koy, mesaj)
    except RuntimeError:
        debug_log(f"(bildirim, loop kapalı) {mesaj}")


def seyrek_bildir(anahtar, mesaj, aralik=1800):
    """Aynı sorun için en fazla `aralik` saniyede bir bildirim (hata fırtınasında Telegram spam'i yok)."""
    simdi = time.time()
    if simdi - _SON_UYARI.get(anahtar, 0) >= aralik:
        _SON_UYARI[anahtar] = simdi
        bildir(mesaj)


def _f(x, varsayilan):
    try:
        return float(x) if x is not None and x != '' else varsayilan
    except (TypeError, ValueError):
        return varsayilan


def get_fill_price(order, fallback):
    g = order.get('average')
    if not g:
        filled, cost = order.get('filled'), order.get('cost')
        if filled and cost:
            g = cost / filled
    if not g:
        # ccxt birleşik emirde 'fills' yoktur; ham Binance cevabı info['fills'] içindedir
        fills = (order.get('info') or {}).get('fills') or []
        t_mik = sum(float(f['qty']) for f in fills)
        if t_mik > 0:
            g = sum(float(f['price']) * float(f['qty']) for f in fills) / t_mik
    return float(g) if g else fallback


def net_kar_hesapla(giris, cikis, maliyet_usdt):
    # Alış + satış komisyonu dahil net kâr; oran, risk motorundaki net_oran formülüyle birebir aynı
    coins = maliyet_usdt / giris
    net = coins * (cikis * (1 - FEE_RATE) - giris * (1 + FEE_RATE))
    oran = risk.net_oran(giris, cikis, FEE_RATE)
    return net, oran


V1_COLUMNS = ['Islem_Zamani', 'Sembol', 'Sinyal', 'Kasa_Tipi', 'Giris_RSI', 'Giris_Vol_Oran', 'Giris_ATR_Pct', 'Giris_Fiyat', 'Cikis_Fiyat', 'Kar_Orani', 'Net_Kar_USDT', 'Cikis_Tipi', 'Sure_Saat']
# V2'ye pozisyon kimliği: kısmi + tam çıkış satırları float eşleştirmesi yerine bununla birleştirilir
V2_ISLEM_KOLONLARI = ['Pozisyon_Id', 'Giris_Ts', 'Cikis_Ts']
V2_COLUMNS = V1_COLUMNS + list(V2_FEATURE_MAP.values()) + V2_ISLEM_KOLONLARI
SHADOW_COLUMNS = ['Ts', 'Sinyal_Zamani', 'Sembol', 'Sebep', 'Sinyal', 'Fiyat',
                  'Giris_RSI', 'Giris_Vol_Oran', 'Giris_ATR_Pct'] + list(V2_FEATURE_MAP.values())


def _trade_csv_yaz(trade_data, ai, ek):
    # V1: eski trainer/analizlerle uyumluluk için aynen yazılmaya devam eder
    satir_ekle(os.path.join(BASE_DIR, 'core_islem_verileri.csv'), trade_data, V1_COLUMNS, uyari=bildir)
    v2 = dict(trade_data)
    for k, col in V2_FEATURE_MAP.items(): v2[col] = ai.get(k)
    v2.update(ek)
    satir_ekle(os.path.join(BASE_DIR, 'core_islem_verileri_v2.csv'), v2, V2_COLUMNS, uyari=bildir)


async def save_trade_to_csv(trade_data, ai=None, ek=None):
    """Dosya I/O (fsync dahil) event loop dışında yapılır; hata asla yukarı sızmaz."""
    try:
        await asyncio.to_thread(_trade_csv_yaz, trade_data, ai or {}, ek or {})
    except Exception as e:
        debug_log(f"⚠️ CSV Kayıt Hatası: {e}")
        seyrek_bildir('csv_trade', f"🚨 İşlem CSV'ye yazılamadı: {e}")


async def save_shadow_to_csv(sym, sebep, m):
    """Kural filtresini geçen ama pozisyona dönüşmeyen sinyalleri kaydeder.
    Amaç: sadece girilen işlemlerden öğrenme yanlılığını (selection bias) kırmak.
    shadow_labeler.py bu kayıtları sonradan sanal sonuçla etiketler."""
    try:
        if (time.time() - float(db.hget(f"{PREF}:shadow_son", sym) or 0)) < SHADOW_LOG_ARALIGI: return
        db.hset(f"{PREF}:shadow_son", sym, str(time.time()))
        row = {'Ts': int(time.time() * 1000), 'Sinyal_Zamani': str(datetime.datetime.now()),
               'Sembol': sym, 'Sebep': sebep, 'Sinyal': m.get('signal'), 'Fiyat': m.get('fiyat'),
               'Giris_RSI': m.get('rsi'), 'Giris_Vol_Oran': m.get('vol_ratio'), 'Giris_ATR_Pct': m.get('atr_pct')}
        for k, col in V2_FEATURE_MAP.items(): row[col] = m.get(k)
        await asyncio.to_thread(satir_ekle, os.path.join(BASE_DIR, 'shadow_sinyaller.csv'), row, SHADOW_COLUMNS, bildir)
    except Exception as e:
        debug_log(f"⚠️ Shadow CSV Kayıt Hatası: {e}")


async def telegram_mesaj_gonder(session, m):
    """POST + JSON (uzun /durum mesajları URL limitine takılmaz). 429'da bekler; Markdown parse
    hatasında (sembol/hata metnindeki '_' vb.) mesajı düz metin olarak yeniden gönderir."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': m[:4000], 'parse_mode': 'Markdown'}
    for _ in range(3):
        try:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json(content_type=None)
            if data.get('ok', True):
                return data
            kod = data.get('error_code')
            if kod == 429:
                await asyncio.sleep(float((data.get('parameters') or {}).get('retry_after', 3)))
                continue
            if kod == 400 and 'parse_mode' in payload:
                payload.pop('parse_mode')
                continue
            debug_log(f"❌ TELEGRAM GÖNDERME HATASI: {data.get('description')}")
            return data
        except Exception as e:
            debug_log(f"❌ TELEGRAM BAĞLANTI HATASI: {e}")
            await asyncio.sleep(2)


async def bildirim_gorevi():
    async with aiohttp.ClientSession() as session:
        while True:
            mesaj = await BILDIRIM_KUYRUGU.get()
            await telegram_mesaj_gonder(session, mesaj)


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


def _pozisyonu_sifirla_ve_yaz(pipe, sym, alanlar):
    """Önce pozisyonun TÜM alanlarını siler (çökmüş eski çalışmalardan kalan half_sold vb.
    yeni pozisyona sızmasın), sonra verilen alanları yazar. Çağıran pipeline'ı execute eder."""
    for k in POZISYON_ANAHTARLARI: pipe.hdel(f"{PREF}{k}", sym)
    for k, v in alanlar.items(): pipe.hset(f"{PREF}{k}", sym, v)


async def check_wallet_sync(session):
    # KRİTİK: RAW_TICKERS henüz dolmadıysa fiyatlar 0 döner ve tüm pozisyon
    # kayıtları "toz" sanılıp silinir. Ticker verisi gelmeden tarama yapma.
    if not RAW_TICKERS:
        debug_log("⏳ Ticker verisi henüz hazır değil, cüzdan taraması ertelendi.")
        return False
    debug_log("🔄 Cüzdan Taraması...")
    try:
        # Yarış düzeltmesi: Redis anlık görüntüsü bakiyeden ÖNCE alınır. V18.3'te bakiye await
        # edilirken radar yeni alım yaparsa, alım Redis'te görünüp bakiyede görünmediği için
        # taze pozisyon "toz" sanılıp siliniyordu.
        is_list = db.hgetall(f"{PREF}:islem_listesi") or {}
        giris_z = db.hgetall(f"{PREF}:giris_zamanlari") or {}
        bal_data = await exchange.fetch_balance()
        simdi = time.time()
        cuzdan = {c: float(v) for c, v in bal_data.get('total', {}).items() if float(v or 0) > 0}
        for sym in list(is_list.keys()):
            if simdi - _f(giris_z.get(sym), 0.0) < POZISYON_SENKRON_KORUMA_SN: continue
            coin = sym.split('/')[0]
            fiyat = RAW_TICKERS.get(sym, {}).get('last') or 0
            if fiyat <= 0: continue  # fiyatı bilinmeyen pozisyona dokunma
            if (cuzdan.get(coin, 0) * fiyat) < DUST_THRESHOLD_USDT:
                pipe = db.pipeline(transaction=True)
                for k in POZISYON_ANAHTARLARI + [":stop_counts"]: pipe.hdel(f"{PREF}{k}", sym)
                pipe.execute()
                debug_log(f"🧹 {sym} bakiyede yok, takipten çıkarıldı.")
        for coin, miktar in cuzdan.items():
            sym = f"{coin}/USDT"
            if any(y in sym for y in YASAKLI_COINLER) or sym in is_list or coin in ["USDT", "BNB"]: continue
            if coin.upper() in MANUEL_COINLER: continue
            if db.hexists(f"{PREF}:islem_listesi", sym): continue  # anlık görüntüden sonra açılmış pozisyon
            fiyat = RAW_TICKERS.get(sym, {}).get('last', 0)
            if fiyat > 0 and (miktar * fiyat) > ORPHAN_THRESHOLD_USDT:
                pipe = db.pipeline(transaction=True)
                _pozisyonu_sifirla_ve_yaz(pipe, sym, {":islem_listesi": str(fiyat), ":islem_miktarlari": str(miktar * fiyat),
                                                      ":giris_zamanlari": str(simdi), ":max_karlar": "0.0",
                                                      ":adetler": str(miktar)})
                pipe.execute()
                bildir(f"🧲 **SAHİPSİZ BAKİYE SAHİPLENİLDİ**: {sym} (~{miktar * fiyat:.2f} USDT). Giriş fiyatı bilinmediği "
                       f"için anlık fiyat baz alındı. Elle tutuyorsan .env'de MANUEL_COINLER={coin} ekle.")
        return True
    except Exception as e:
        debug_log(f"⚠️ Cüzdan Tarama Hatası: {e}")
        return False


def _model_skorlari(metrics, sym):
    for yuva, anahtar in ((CORE_YUVA, 'ai_score'), (FILTRE_YUVA, 'filter_score')):
        if not yuva.yuklu:
            continue
        try:
            metrics[anahtar] = yuva.skor(metrics)
        except Exception as e:
            debug_log(f"⚠️ {yuva.rol} Model Tahmin Hatası ({sym}): {e}")
            seyrek_bildir(f"model:{yuva.rol}", f"🚨 {yuva.rol} modeli skor üretemiyor: {e}")
    metrics['ai_model_surum'] = CORE_YUVA.surum


async def analyze_market(sym):
    """Kural filtresini geçen sinyalin tüm feature'larını ve AI skorlarını döndürür.
    ALIM/RED KARARI BURADA VERİLMEZ — karar radar_loop'tadır. Böylece reddedilen
    sinyaller de feature'larıyla shadow CSV'ye yazılabilir.
    Kural ve feature'lar sniper.ozellikler'de: backfill ve trainer AYNI kodu kullanır."""
    try:
        # 1. MTF Analizi: 1 saatlik grafikte trend yönü kontrolü
        df_1h = ohlcv_df(await exchange.fetch_ohlcv(sym, '1h', limit=50))
        df = ohlcv_df(await exchange.fetch_ohlcv(sym, '15m', limit=100))
        if SINYAL_KAPALI_MUM:
            df, df_1h = kapali_mum_hizala(df, df_1h)
        temel = sinyal_degerlendir(df, ema_son(df_1h['c'], 20), SINYAL_AYAR)
        if temel is None: return None

        # Emir defteri sadece kural geçtikten sonra çekilir (V18.3 ile aynı API maliyeti)
        try:
            ob = await exchange.fetch_order_book(sym, limit=20)
        except Exception:
            ob = None
        metrics = genisletilmis_ozellikler(
            df, df_1h, temel, simdi=datetime.datetime.now(), simdi_ms=int(time.time() * 1000),
            btc_1h_degisim=BTC_1H_DEGISIM, btc_ema_uzaklik=BTC_EMA_UZAKLIK, btc_adx=BTC_ADX,
            stop_sayisi=int(db.hget(f"{PREF}:stop_counts", sym) or 0), ob=ob,
            genislik=PIYASA_GENISLIK, kapali_mum_modu=SINYAL_KAPALI_MUM)

        # AI skorları: hesaplanır ve kaydedilir; bloklamayı radar_loop yapar (gölge modda hiç yapmaz)
        _model_skorlari(metrics, sym)
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
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    r = await resp.json(content_type=None)

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
                                elif CORE_YUVA.yuklu: ai_msg = f"🤖 **Yapay Zekâ Entegreli** ({CORE_YUVA.surum})"
                                else: ai_msg = "📝 **Veri Toplama Modunda**"
                                msg = f"🏢 **AI SNIPER & MOON BAG (V18.4 PRO)**\n🧠 Durum: `{ai_msg}`\n💼 **Aktif Fon:** `{(await exchange.fetch_balance())['total'].get('USDT', 0):.2f} USDT`\n📊 **Piyasa:** `{PIYASA_DURUM}`\n\n"
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
                                ai_info = "\n*(Yapay zekâ filtrelemesi devrededir)*" if (CORE_YUVA.yuklu and not AI_GOLGE_MOD) else ""
                                await telegram_mesaj_gonder(session, f"📊 **KÜMÜLATİF KÂR**: `{kar_val:.2f} USDT`{ai_info}")
                            except Exception as e:
                                await telegram_mesaj_gonder(session, f"⚠️ Kâr verisi alınırken hata: {str(e)}")

            except Exception as e:
                # last_id SIFIRLANMAZ: sıfırlanırsa Telegram'daki eski komutlar tekrar işlenir
                debug_log(f"⚠️ Telegram Dinleyici Hatası: {e}")
                await asyncio.sleep(5)
            await asyncio.sleep(1)


# ----------------------------------------------------------------------------------------
# EMİR KATMANI: idempotent market emirleri
# ----------------------------------------------------------------------------------------
async def _guvenli_market_emri(sym, taraf, miktar):
    """clientOrderId ile emir verir. Ağ hatası/zaman aşımında sonuç BELİRSİZDİR (emir borsaya
    ulaşmış olabilir): aynı ID ile sorgulanır, dolduysa emir döner. V18.3'te bu durumda emir
    'başarısız' sayılıp tekrarlanıyordu -> çift kısmi satış / kaydı olmayan satış."""
    cid = f"sn{int(time.time() * 1000)}{os.urandom(3).hex()}"
    params = {'newClientOrderId': cid}
    try:
        if taraf == 'sell':
            return await exchange.create_market_sell_order(sym, miktar, params=params)
        return await exchange.create_market_buy_order(sym, miktar, params=params)
    except (ccxt.NetworkError, asyncio.TimeoutError) as e:
        debug_log(f"⚠️ {sym} {taraf} emri belirsiz ({type(e).__name__}); {cid} sorgulanıyor...")
        for bekle in (1, 2, 4):
            await asyncio.sleep(bekle)
            try:
                o = await exchange.fetch_order(None, sym, params={'origClientOrderId': cid})
            except (ccxt.OrderNotFound, ccxt.NetworkError):
                continue
            if o and float(o.get('filled') or 0) > 0:
                debug_log(f"✅ {sym} {taraf} emri borsada gerçekleşmiş ({cid}).")
                return o
            if o and o.get('status') in ('canceled', 'rejected', 'expired'):
                break
        raise


def _alinan_net_adet(order, istenen, coin):
    """Gerçekte cüzdana giren adet: dolan miktar - (komisyon coin'den kesildiyse) komisyon."""
    filled = float(order.get('filled') or istenen)
    ucretler = order.get('fees') or ([order['fee']] if order.get('fee') else [])
    ucret = sum(float(f.get('cost') or 0) for f in ucretler if f and f.get('currency') == coin)
    return max(filled - ucret, 0.0)


async def _satilabilir_miktar(sym, adet, oran):
    """(satılacak miktar, serbest bakiye). Pozisyonun takip edilen adedi biliniyorsa ASLA onu aşmaz:
    V18.3 cüzdandaki TÜM serbest bakiyeyi satıyordu (aynı coin elle tutuluyorsa onu da satardı)."""
    bal = await exchange.fetch_balance()
    serbest = float((bal.get(sym.split('/')[0]) or {}).get('free') or 0)
    baz = min(serbest, adet) if adet is not None else serbest
    try:
        miktar = float(exchange.amount_to_precision(sym, baz * oran))
    except ccxt.InvalidOrder:
        miktar = 0.0  # V18.3: bu istisna try DIŞINDAYDI -> tüm VIP turu çöküyor, diğer pozisyonların stopu çalışmıyordu
    return miktar, serbest


def _pozisyonu_kapat(sym, cooldown=True):
    pipe = db.pipeline(transaction=True)
    if cooldown: pipe.hset(f"{PREF}:cooldowns", sym, str(time.time() + 3600))
    for k in POZISYON_ANAHTARLARI: pipe.hdel(f"{PREF}{k}", sym)
    pipe.execute()


def _islem_satiri(sym, ai, kasa_tipi, a, g_satis, g_oran, net_kd, cikis_tipi, sure_saat):
    return {"Islem_Zamani": str(datetime.datetime.now()), "Sembol": sym, "Sinyal": ai.get("signal"),
            "Kasa_Tipi": kasa_tipi, "Giris_RSI": ai.get("rsi"), "Giris_Vol_Oran": ai.get("vol_ratio"),
            "Giris_ATR_Pct": ai.get("atr_pct"), "Giris_Fiyat": a, "Cikis_Fiyat": g_satis,
            "Kar_Orani": g_oran * 100, "Net_Kar_USDT": net_kd, "Cikis_Tipi": cikis_tipi,
            "Sure_Saat": round(sure_saat, 2)}


def _islem_ek(sym, p):
    return {'Pozisyon_Id': f"{sym}|{int(p.giris_zamani * 1000)}", 'Giris_Ts': int(p.giris_zamani * 1000),
            'Cikis_Ts': int(time.time() * 1000)}


# ----------------------------------------------------------------------------------------
# VIP CÜZDAN: karar sniper.risk_motoru'nda (saf, testli), burada sadece veri + emir
# ----------------------------------------------------------------------------------------
_POZ_ALANLARI = ['max_karlar', 'half_sold', 'giris_zamanlari', 'zaman_ref', 'islem_miktarlari',
                 'ai_data', 'last_rsi_check', 'last_mom_check', 'adetler']


def _pozisyon_durumlari(semboller):
    """Tüm pozisyonların durumunu TEK Redis round-trip'te okur (V18.3: sembol başına ~8 ayrı çağrı)."""
    pipe = db.pipeline(transaction=False)
    for alan in _POZ_ALANLARI: pipe.hmget(f"{PREF}:{alan}", semboller)
    sonuc = pipe.execute()
    return {sym: {alan: sonuc[i][j] for i, alan in enumerate(_POZ_ALANLARI)} for j, sym in enumerate(semboller)}


async def _fiyatlari_getir(semboller):
    try:
        return await exchange.fetch_tickers(semboller)
    except Exception as e:
        # Tek bir sorunlu sembol (ör. delist) toplu isteği düşürür -> V18.3'te TÜM stoplar devre dışı kalıyordu
        debug_log(f"⚠️ Toplu ticker hatası ({e}); semboller tek tek çekiliyor.")
        sonuc = {}
        for s in semboller:
            try:
                sonuc[s] = await exchange.fetch_ticker(s)
            except Exception as e2:
                seyrek_bildir(f"ticker:{s}", f"🚨 {s} fiyatı alınamıyor, pozisyon İZLENEMİYOR: {e2}")
        return sonuc


async def _yarim_sat(sym, p, e, tick, i_mik, adet, ai, kasa_tipi, simdi):
    """'SATILDI' | 'KUCUK' (min-notional altı, V18.3'teki gibi sonraki kontrole geçilir) | 'HATA'"""
    tahmini = adet if adet is not None else (i_mik / p.giris)
    if tahmini * e.oran * tick <= MIN_NOTIONAL_USDT: return 'KUCUK'  # API çağrısı yapmadan ön kontrol
    satilacak_miktar, _ = await _satilabilir_miktar(sym, adet, e.oran)
    if satilacak_miktar * tick <= MIN_NOTIONAL_USDT: return 'KUCUK'
    try:
        order = await _guvenli_market_emri(sym, 'sell', satilacak_miktar)
    except Exception as ex:
        debug_log(f"⚠️ {e.mesaj} Satış Hatası ({sym}): {ex}")
        return 'HATA'
    g_satis = get_fill_price(order, tick)
    satilan = float(order.get('filled') or satilacak_miktar)
    net_kd, g_oran = net_kar_hesapla(p.giris, g_satis, i_mik / 2)
    try:
        pipe = db.pipeline(transaction=True)
        pipe.hset(f"{PREF}:half_sold", sym, "1")
        pipe.hset(f"{PREF}:realize_karlar", sym, str(net_kd))
        pipe.hset(f"{PREF}:islem_miktarlari", sym, str(i_mik / 2))
        if adet is not None: pipe.hset(f"{PREF}:adetler", sym, str(max(adet - satilan, 0.0)))
        pipe.execute()
    except Exception as ex:
        bildir(f"🚨 {sym} yarısı SATILDI ama Redis güncellenemedi ({ex}). Pozisyonu kontrol et!")
    add_to_total_profit(net_kd)
    await save_trade_to_csv(_islem_satiri(sym, ai, kasa_tipi, p.giris, g_satis, g_oran, net_kd, e.mesaj,
                                          (simdi - p.giris_zamani) / 3600), ai, _islem_ek(sym, p))
    if e.tip == risk.MOON_BAG:
        bildir(f"🚀 **MOON BAG (RSI ŞİŞTİ)**: {sym}\n🛒 `{p.giris:.4f} ➔ {g_satis:.4f}`\n💸 Cebe: `+{net_kd:.2f} USDT` (%{g_oran*100:.2f})\n*💎 Kalan %50 ile trend takip ediliyor!*")
    else:
        bildir(f"💰 **DİNAMİK KISMİ KÂR**: {sym}\n🛒 `{p.giris:.4f} ➔ {g_satis:.4f}`\n💸 Cebe: `+{net_kd:.2f} USDT` (%{g_oran*100:.2f})")
    return 'SATILDI'


async def _tam_cikis(sym, p, exit_msg, tick, i_mik, adet, ai, kasa_tipi, simdi):
    satilacak_miktar, serbest = await _satilabilir_miktar(sym, adet, 1.0)
    if satilacak_miktar * tick <= MIN_NOTIONAL_USDT:
        # V18.3: hiçbir şey yapılmıyordu -> pozisyon sonsuza dek takılı kalıyor, her 2 sn'de fetch_balance
        if serbest * tick < DUST_THRESHOLD_USDT and (simdi - p.giris_zamani) < ZEHIRLI_POZISYON_YAS_SN:
            return  # taze alım: bakiye henüz yansımamış olabilir, sonraki turda tekrar dene
        _pozisyonu_kapat(sym)
        bildir(f"⚠️ **{sym} TAKİPTEN ÇIKARILDI** ({exit_msg} tetiklendi): satılabilir bakiye "
               f"{serbest:.8g} (~{serbest * tick:.2f} USDT) min emir tutarının altında veya cüzdanda yok. "
               f"Elle satılmış ya da önceki satış teyit edilememiş olabilir.")
        return
    try:
        order = await _guvenli_market_emri(sym, 'sell', satilacak_miktar)
    except Exception as ex:
        debug_log(f"⚠️ TAM ÇIKIŞ Hatası ({sym}): {ex}")
        seyrek_bildir(f"cikis:{sym}", f"🚨 {sym} {exit_msg} satışı BAŞARISIZ, tekrar denenecek: {ex}", aralik=300)
        return
    g_satis = get_fill_price(order, tick)
    net_kd, g_oran = net_kar_hesapla(p.giris, g_satis, i_mik)
    try:
        _pozisyonu_kapat(sym)  # önce durum: aynı pozisyon ikinci kez satılmaya çalışılmasın
    except Exception as ex:
        bildir(f"🚨 {sym} SATILDI ama Redis temizlenemedi ({ex}). Pozisyonu kontrol et!")
    add_to_total_profit(net_kd)
    try:
        if "STOP" in exit_msg:
            count = int(db.hget(f"{PREF}:stop_counts", sym) or 0) + 1
            if count >= 2:
                db.hset(f"{PREF}:kara_liste", sym, str(time.time() + 86400)); db.hdel(f"{PREF}:stop_counts", sym)
                bildir(f"🚫 **KARA LİSTE**: {sym} peş peşe 2 kez stop ettirdi. 24 Saat uzak durulacak.")
            else: db.hset(f"{PREF}:stop_counts", sym, str(count))
        elif "TREND" in exit_msg:
            db.hdel(f"{PREF}:stop_counts", sym)
    except Exception as ex:
        debug_log(f"⚠️ Stop sayacı güncellenemedi ({sym}): {ex}")
    await save_trade_to_csv(_islem_satiri(sym, ai, kasa_tipi, p.giris, g_satis, g_oran, net_kd, exit_msg,
                                          (simdi - p.giris_zamani) / 3600), ai, _islem_ek(sym, p))
    bildir(f"{'🟢' if net_kd >= 0 else '🔴'} **{exit_msg}**: {sym}\n🛒 `{p.giris:.4f} ➔ {g_satis:.4f}`\n💸 Net: `{net_kd:+.2f} USDT` (Net: %{g_oran*100:.2f})")


async def _pozisyonu_isle(sym, a_str, tick, ham, simdi):
    try: ai = json.loads(ham['ai_data'] or "{}")
    except Exception: ai = {}
    if not isinstance(ai, dict): ai = {}
    p = risk.Pozisyon(sembol=sym, giris=float(a_str), max_kar=_f(ham['max_karlar'], 0.0),
                      half_sold=ham['half_sold'] == "1", giris_zamani=_f(ham['giris_zamanlari'], simdi),
                      zaman_ref=_f(ham['zaman_ref'], None), atr_pct=_f(ai.get('atr_pct'), 2.5),
                      is_whale=bool(ai.get('is_whale', False)),
                      son_rsi_kontrol=_f(ham['last_rsi_check'], 0.0), son_mom_kontrol=_f(ham['last_mom_check'], 0.0))
    i_mik = _f(ham['islem_miktarlari'], SABIT_KASA)
    adet = _f(ham['adetler'], None)
    kasa_tipi = "BALİNA" if p.is_whale else "NORMAL"

    sev = risk.seviyeleri_hesapla(p, tick, RISK_AYAR)
    if sev.max_kar > p.max_kar: db.hset(f"{PREF}:max_karlar", sym, str(sev.max_kar))

    rsi_tetik = False
    if risk.rsi_kontrolu_gerekli(p, sev, simdi, RISK_AYAR):
        try:
            # limit=100: giriş RSI'ı ile aynı ısınma (V18.3'te 25 mum -> Wilder RSI yakınsamıyordu)
            df_o = ohlcv_df(await exchange.fetch_ohlcv(sym, '15m', limit=100))
            rsi_tetik = float(ta.rsi(df_o['c'], length=14).iloc[-1]) >= RISK_AYAR.moon_bag_rsi
        except Exception as e:
            debug_log(f"⚠️ RSI Kontrol Hatası ({sym}): {e}")
        finally:
            db.hset(f"{PREF}:last_rsi_check", sym, str(time.time()))

    for e in risk.kararlar(p, sev, simdi, REJIM, RISK_AYAR, rsi_tetik):
        if e.tip in (risk.MOON_BAG, risk.KISMI_KAR):
            sonuc = await _yarim_sat(sym, p, e, tick, i_mik, adet, ai, kasa_tipi, simdi)
            if sonuc == 'SATILDI': return
            continue  # KUCUK: V18.3 fall-through | HATA: koruyucu tam çıkış yine denenir (stop atlanmaz)
        if e.tip == risk.TAM_CIKIS:
            await _tam_cikis(sym, p, e.mesaj, tick, i_mik, adet, ai, kasa_tipi, simdi)
        elif e.tip == risk.ZAMAN_UZAT:
            db.hset(f"{PREF}:zaman_ref", sym, str(simdi))  # giriş zamanı değişmez (CSV süresi doğru kalır)
        elif e.tip == risk.MOMENTUM_KONTROL:
            # 💤 MOMENTUM ÖLDÜ (sadece YATAY rejimde): kapanmış mumlarla ölçülür
            oldu = None
            try:
                df_m = ohlcv_df(await exchange.fetch_ohlcv(sym, '15m', limit=20))
                oldu = risk.momentum_oldu_mu(df_m['v'].iloc[:-1].tolist())
            except Exception as ex:
                debug_log(f"⚠️ Momentum Kontrol Hatası ({sym}): {ex}")
            finally:
                db.hset(f"{PREF}:last_mom_check", sym, str(time.time()))
            if oldu:
                await _tam_cikis(sym, p, e.mesaj, tick, i_mik, adet, ai, kasa_tipi, simdi)
        return


async def vip_turu():
    """VIP döngüsünün tek turu: tüm açık pozisyonları bir kez değerlendirir."""
    is_list = db.hgetall(f"{PREF}:islem_listesi") or {}
    if not is_list:
        return
    semboller = list(is_list.keys())
    vip_tickers = await _fiyatlari_getir(semboller)
    durumlar = _pozisyon_durumlari(semboller)
    for sym, a_str in is_list.items():
        # Sembol bazında izolasyon: bir pozisyonun hatası diğerlerinin stopunu durduramaz
        try:
            tick = (vip_tickers.get(sym) or {}).get('last')
            if not tick: continue
            await _pozisyonu_isle(sym, a_str, float(tick), durumlar[sym], time.time())
        except Exception as e:
            debug_log(f"⚠️ Risk Hatası ({sym}): {e}\n{traceback.format_exc(limit=3)}")
            seyrek_bildir(f"risk:{sym}", f"🚨 {sym} pozisyonu işlenirken hata: {e}", aralik=600)


async def vip_cuzdan_loop():
    debug_log("🛡️ Moon Bag & Risk Koruması Aktif")
    last_sync = 0
    async with aiohttp.ClientSession() as t_session:
        while True:
            try:
                if (time.time() - last_sync) > 1800:
                    if await check_wallet_sync(t_session): last_sync = time.time()
                await vip_turu()
                SON_TUR['vip'] = time.time()
                await asyncio.sleep(2)
            except Exception as e:
                debug_log(f"⚠️ Risk Hatası (Genel): {e}")
                await asyncio.sleep(5)


async def _alim_yap(sym, ai_metrics, aktif_kasa):
    """Market alım + pozisyon durumunu tek MULTI/EXEC ile yazar. Alım gerçekleştiyse True."""
    ai_s, is_w = ai_metrics.get("ai_score"), ai_metrics.get("is_whale", False)
    try:
        ask = (await exchange.fetch_order_book(sym, limit=5))['asks'][0][0]

        # Borsa kurallarına göre miktar yuvarlama (Precision Format)
        raw_amount = aktif_kasa / ask
        formatted_amount = float(exchange.amount_to_precision(sym, raw_amount))

        if formatted_amount * ask <= MIN_NOTIONAL_USDT:  # Binance Min Emir Tutarı (Notional) Kontrolü
            return False
        order = await _guvenli_market_emri(sym, 'buy', formatted_amount)
    except Exception as e:
        debug_log(f"⚠️ Alım Emri Başarısız ({sym}): {e}")
        return False

    gerceklesen_fiyat = get_fill_price(order, ask)
    adet = _alinan_net_adet(order, formatted_amount, sym.split('/')[0])
    try:
        pipe = db.pipeline(transaction=True)
        _pozisyonu_sifirla_ve_yaz(pipe, sym, {
            ":islem_listesi": str(gerceklesen_fiyat),
            ":islem_miktarlari": str(formatted_amount * gerceklesen_fiyat),
            ":max_karlar": "0.0", ":giris_zamanlari": str(time.time()),
            ":ai_data": json.dumps(ai_metrics), ":adetler": str(adet)})
        pipe.execute()
    except Exception as ex:
        debug_log(f"🚨 {sym} alındı ama Redis'e yazılamadı: {ex}")
        bildir(f"🚨 **{sym} ALINDI ama Redis'e yazılamadı** ({ex}). Cüzdan senkronu "
               f"≤30 dk içinde sahiplenecek (giriş fiyatı kaybolur). Kontrol et!")
        return True  # alım GERÇEKLEŞTİ: bu turda ikinci alım yapılmasın

    msg = f"🟢 **YENİ POZİSYON**: {sym}\nFiyat: `{gerceklesen_fiyat:.4f}`\nKasa: `{formatted_amount * gerceklesen_fiyat:.2f} USDT`"
    if ai_s is not None: msg += f"\n🧠 AI Skoru: %{ai_s*100:.1f}" + (" _(gölge mod)_" if AI_GOLGE_MOD else "")
    if is_w: msg += "\n🐋 **BALİNA TESPİT EDİLDİ!**"
    bildir(msg)
    return True


async def radar_loop():
    global RAW_TICKERS, RADAR_USDT, BTC_OK, PIYASA_DURUM, BTC_1H_DEGISIM, BTC_EMA_UZAKLIK, BTC_ADX, REJIM, PIYASA_GENISLIK
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

                uygun = [(s, d) for s, d in RAW_TICKERS.items() if all(ord(c) < 128 for c in s) and not any(y in s for y in YASAKLI_COINLER) and s.endswith('/USDT') and float(d.get('quoteVolume') or 0) > MIN_HACIM_USDT]
                res = [{'s': s, 'c': float(d.get('percentage') or 0)} for s, d in uygun]
                PIYASA_GENISLIK = (sum(1 for x in res if x['c'] > 0) / len(res)) if res else None
                RADAR_USDT = [x['s'] for x in sorted(res, key=lambda x: x['c'], reverse=True)[:10]]
                if BTC_OK:
                    is_list = db.hgetall(f"{PREF}:islem_listesi") or {}
                    alim_yapildi = False
                    for sym in RADAR_USDT:
                        if time.time() < float(db.hget(f"{PREF}:cooldowns", sym) or 0.0) or sym in is_list: continue
                        if float(db.hget(f"{PREF}:kara_liste", sym) or 0.0) > time.time(): continue
                        if sym.split('/')[0].upper() in MANUEL_COINLER: continue

                        ai_metrics = await analyze_market(sym)
                        if not ai_metrics: continue

                        # --- AI KARARI ---
                        # Gölge modda skor sadece loglanır/kaydedilir, işlem BLOKLANMAZ.
                        ai_s, f_s = ai_metrics.get("ai_score"), ai_metrics.get("filter_score")
                        if not AI_GOLGE_MOD:
                            if not CORE_YUVA.yuklu:
                                # Bloklama modunda model yoksa işlem açılmaz (V18.3'ün açılış kontrolüyle tutarlı)
                                seyrek_bildir('model_yok', "🚨 Bloklama modunda core model yüklü değil: yeni alım yapılmıyor.")
                                await save_shadow_to_csv(sym, "AI_MODEL_YOK", ai_metrics)
                                continue
                            if ai_s is None or CORE_YUVA.blokla_mi(ai_s) or FILTRE_YUVA.blokla_mi(f_s):
                                kar_txt = f"%{ai_s*100:.1f}" if ai_s is not None else "hesaplanamadı"
                                risk_txt = f" | Zarar Riski: %{f_s*100:.1f}" if f_s is not None else ""
                                debug_log(f"🛡️ AI Filtre Reddetti: {sym} | Kâr İht: {kar_txt}{risk_txt}")
                                await save_shadow_to_csv(sym, "AI_RED", ai_metrics)
                                continue
                        elif ai_s is not None:
                            risk_txt = f" | Zarar Riski: %{f_s*100:.1f}" if f_s is not None else ""
                            debug_log(f"👁️ Gölge AI Skoru: {sym} | Kâr İht: %{ai_s*100:.1f}{risk_txt}")

                        # YATAY REJİM FİLTRESİ: alım açılmaz, sinyal shadow'a yazılır.
                        # Filtrenin gerçek maliyeti/kazancı labeler'ın sanal sonuçlarıyla ölçülecek.
                        if REJIM == "YATAY":
                            debug_log(f"😴 Yatay Rejim, alım pas geçildi: {sym} (BTC ADX: {BTC_ADX:.1f})")
                            await save_shadow_to_csv(sym, "REJIM_YATAY", ai_metrics)
                            continue

                        # Döngü başına tek alım kuralı: alınmayan diğer sinyaller shadow'a yazılır
                        if alim_yapildi:
                            await save_shadow_to_csv(sym, "TEK_ALIM_KURALI", ai_metrics)
                            continue

                        is_w = ai_metrics.get("is_whale", False)
                        aktif_kasa = BALINA_KASA if is_w else SABIT_KASA

                        # 'free' bakiye: açık emirlerde kilitli USDT sayılmaz; %1 pay slipaj/komisyon için
                        if (await exchange.fetch_balance())['free'].get("USDT", 0) < aktif_kasa * 1.01:
                            await save_shadow_to_csv(sym, "BAKIYE_YETERSIZ", ai_metrics)
                            continue
                        if await _alim_yap(sym, ai_metrics, aktif_kasa):
                            alim_yapildi = True
                SON_TUR['radar'] = time.time()
                await asyncio.sleep(15)
            except Exception as e: debug_log(f"⚠️ Radar Hatası: {e}"); await asyncio.sleep(30)


async def model_izleme_gorevi():
    """Gece eğitilen model bot yeniden başlatılmadan devreye girer; bozuk dosya eski modeli düşürmez."""
    while True:
        await asyncio.sleep(MODEL_KONTROL_SN)
        for yuva in (CORE_YUVA, FILTRE_YUVA):
            try:
                mesaj = yuva.yenile()
            except Exception as e:
                mesaj = f"⚠️ {yuva.rol} modeli kontrol edilemedi: {e}"
            if mesaj:
                debug_log(mesaj); bildir(mesaj)


async def watchdog_gorevi():
    """Döngülerden biri takılırsa (ör. askıda kalan bir await) Telegram'dan haber verir."""
    alarmda = set()
    while True:
        await asyncio.sleep(15)
        simdi = time.time()
        for ad, esik in (('vip', WATCHDOG_VIP_SN), ('radar', WATCHDOG_RADAR_SN)):
            gecikme = simdi - SON_TUR[ad]
            if gecikme > esik and ad not in alarmda:
                alarmda.add(ad)
                bildir(f"🚨 **WATCHDOG**: {ad} döngüsü {gecikme:.0f} sn'dir tur tamamlayamadı!")
            elif gecikme <= esik and ad in alarmda:
                alarmda.discard(ad)
                bildir(f"✅ WATCHDOG: {ad} döngüsü normale döndü.")


async def start():
    global BILDIRIM_KUYRUGU, ANA_LOOP
    ANA_LOOP = asyncio.get_running_loop()
    BILDIRIM_KUYRUGU = asyncio.Queue(maxsize=500)
    # Redis yoksa bot sessizce garip davranmasın, açılışta net hata ver
    try:
        db.ping()
    except Exception as e:
        debug_log(f"🚨 KRİTİK HATA: Redis'e bağlanılamadı (localhost:6379): {e}")
        sys.exit(1)

    for yuva in (CORE_YUVA, FILTRE_YUVA):
        mesaj = yuva.yenile()
        if mesaj: debug_log(mesaj)

    if not CORE_YUVA.yuklu:
        if AI_GOLGE_MOD:
            debug_log("ℹ️ Model dosyası yok ama GÖLGE MOD açık: bot kural tabanlı çalışıp veri toplayacak.")
        else:
            debug_log("🚨 KRİTİK HATA: core_xgboost_model.json bulunamadı veya hatalı!")
            debug_log("🚨 AI bloklama modu (AI_GOLGE_MOD=False) için model dosyası ZORUNLUDUR.")
            sys.exit(1) # Model yoksa çalışmayı durdurarak random işlem yapmasını engeller

    # Lot ve fiyat hesaplamaları için piyasa metrikleri önceden yüklenir
    await exchange.load_markets()
    bildir(f"🏢 CORE V18.4 başladı | {'GÖLGE MOD' if AI_GOLGE_MOD else 'BLOKLAMA MODU'} | "
           f"core: {CORE_YUVA.surum or 'yok'} | filtre: {FILTRE_YUVA.surum or 'yok'}")
    try:
        await asyncio.gather(radar_loop(), vip_cuzdan_loop(), telegram_handler(),
                             bildirim_gorevi(), model_izleme_gorevi(), watchdog_gorevi())
    finally:
        await exchange.close()

if __name__ == "__main__":
    for h in YASAKLI_COINLER:
        for k in POZISYON_ANAHTARLARI:
            try: db.hdel(f"{PREF}{k}", f"{h}/USDT")
            except: pass
    debug_log("🏢 CORE V18.4 PRO YAPAY ZEKÂ DESTEKLİ SNIPER AKTİF!")
    try: asyncio.run(start())
    except KeyboardInterrupt: pass
