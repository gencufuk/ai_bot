# -*- coding: utf-8 -*-
"""STRATEJİ LABORATUVARI: "yükselmeden önce al, AI ile süz, 10 işlemin 7-8'ini kazan, her ay düzenli gelir"
isteğinin önceden kayıtlı kurallarla sınanması (Binance 1 saatlik mumları, komisyon ve kayma dahil).

Soru: canlı bot (son 24 saatte en çok yükselen 10 coini kırılımda alır) simülasyonda 18 ayın 17'sinde zararda.
Coin henüz çok yükselmeden giren, gerçek hacim arayan, yüksek isabet hedefleyen ya da AI ile süzülen bir tasarımın
geçmişte komisyon sonrası kalıcı bir üstünlüğü var mı? Trend ve yatay piyasaya ayrı yöntem daha mı iyi? Kurallar,
parametreler, çıkışlar, maliyetler ve KARAR KURALI veriye bakılmadan sabitlendi (ANALIZ_V18.4.md §12 ve §12.1 ek ön
kaydı). Karar kuralı rapor başlığında aynen yazılır ve her satıra (9 kural + 6 AI varyantı = 15 satır) uygulanır.
Dönem ikiye bölünür: kuruluş (--ayrim tarihinden önceki girişler) ve sınama (sonraki girişler).

Rejim (§12.1; her saat için, yalnız kapanmış BTC 1 saatlik mumlarıyla; karar mumu t'deki değer kullanılır):
  TREND_YUKARI     BTC ADX14 (Wilder) >= 20 ve BTC kapanışı > EMA200 (1s)
  TREND_ASAGI      BTC ADX14 >= 20 ve BTC kapanışı <= EMA200 (ADX yön bilmez; sert düşüş de trenddir)
  YATAY            BTC ADX14 < 20
  BILINMIYOR       ADX ya da EMA200 yok (eksik saat, ısınma): rejime bağlı hiçbir sinyal çıkmaz
  ADX14: up = H[t]-H[t-1], down = L[t-1]-L[t]; +DM = up (up > down ve up > 0), -DM = down (down > up ve down > 0),
  yoksa 0; TR = max(H-L, |H-C[t-1]|, |L-C[t-1]|); ATR/+DM/-DM ve DX ewm(1/14) (eksik saat atlanır; önceki saat
  eksikse o saatin DM'si yok, TR = H-L); +DI/-DI = 100 DM/ATR; DX = 100 |+DI - -DI| / (+DI + -DI); son 15 saat dolu
  değilse ADX yok (boşluktan hemen sonra bayat değer yok).

Kurallar (karar mumu t = yeni KAPANAN 1 saatlik mum; yalnız t ve öncesi kullanılır; giriş t+1 mumunun AÇILIŞINDA):
  BOT_VEKILI       24s hacmi 12M+ olanlar içinde 24s getirisi en yüksek 10'da; kapanış > EMA20; ATR/fiyat <= %3;
                   RSI14 > 55; mum hacmi önceki 14 mum ortalamasının 2.5 katından fazla (canlı botun 1s vekili)
  ERKEN_BIRIKIM    yükselmeden önce: 24s getiri -%3..+%6; kapanış > EMA50 ve EMA20 > EMA50; RSI14 50..68; son 6
                   saatin hacmi önceki 7 günün medyanının 2 katı ve en az 4 saati medyanın üstünde; kapanış önceki 24
                   saatin zirvesinin üstünde; 24s hacim >= --min-hacim; BTC 1s EMA200'ün üstünde
  SIKISMA_KIRILIM  Bollinger genişliği son 30 günün en dar %20'sinde; kapanış üst bandın üstünde; mum hacmi önceki
                   20 mumun ortalamasının 2 katı; 24s getiri <= %8; 24s hacim >= --min-hacim; BTC EMA200 üstünde
  TREND_DIP_RSI2   30 günlük ortalama günlük hacim >= --buyuk-hacim; kapanış > EMA200; RSI2 < 10; BTC EMA200 üstünde
                   (Connors tarzı yüksek isabetli dipten dönüş)
  YUKSEK_ISABET    ERKEN_BIRIKIM girişleri, +%1.5 hedef / -%4 stop / 24 saat (10'da 7-8 hedefinin doğrudan testi)
  YATAY_DONUS      yatay piyasada ortalamaya dönüş: rejim YATAY; coinin kendi ADX14 < 20; kapanış Bollinger alt
                   bandının (SMA20 - 2σ) altında; RSI14 < 30; 24s hacim >= --min-hacim
  REJIM_KOMBO      rejime göre geçiş: TREND_YUKARI'da ERKEN_BIRIKIM (KADEMELI), YATAY'da YATAY_DONUS (ORTA_BANT),
                   TREND_ASAGI ve BILINMIYOR'da alım yok (AI yok)
  REJIM_KOMBO_BOT  aynısı, trend bileşeni BOT_VEKILI (bugünkü botun vekili) (AI yok)
  BTC_TREND        yalnız BTC, günlük kapanışla: kapanış > MA100 x 1.02 al, < MA100 x 0.98 sat (önceden kanıtı olan
                   taban; AI yok)
Her kuralda bir paritede aynı anda tek pozisyon; yeni sinyal ancak önceki işlemin çıkış mumundan SONRAKİ mumda.
Kombolarda bu kural iki bileşen için birlikte geçerli (açık ERKEN işlemi varken YATAY_DONUS sinyali alınmaz, tersi de);
her işlem kendi bileşeninin çıkışıyla kapanır ve 'bilesen' sütununa yazılır (kombo dışı kurallarda bilesen = kural).
Çıkışlar (1 saatlik mumlarla; mum içi yol: yeşil mum açılış->dip->tepe->kapanış, kırmızı açılış->tepe->dip->kapanış):
  KADEMELI    kullanıcının basamakları: zirve kazancı < %5 iken stop giriş -%2; %5-10 zirve -%3; %10-50 zirve -%4;
              %50+ zirve -%10 (stop yalnız yukarı gider); en çok 168 saat
  HEDEF_STOP  hedef giriş +%1.5 (limit emir, kaymasız), sabit stop giriş -%4, en çok 24 saat
  RSI2_CIKIS  sabit stop giriş -%7; kapanışı SMA5'in üstüne çıkan ilk mumun kapanışında (giriş mumu dahil); en çok 48 s
  ORTA_BANT   sabit stop giriş -%3; kapanışı SMA20'ye (orta bant) ulaşan ya da geçen (>=) ilk mumun kapanışında (giriş
              mumu dahil; SMA20 yoksa o mumda sinyal çıkışı yok); en çok 24 saat
Maliyet (k=1): komisyon %0.1/yön; kayma: giriş %0.05, stop %0.15, zaman/sinyal çıkışı %0.05. k=0.5 ve 2 duyarlılık
içindir (komisyon ve tüm kaymalar k ile çarpılır; hedef dolumu hiç kaymaz). İşlem dizisi YALNIZ k=1 ile belirlenir;
k=0.5 ve k=2 getirileri aynı girişten çıkış yeniden simüle edilerek birebir hesaplanır (seviyeler giriş dolumuna bağlı).
Veri sonu: yalnız azami tutma süresi veride kalan girişler alınır (veri sonundaki işlemler sonucuna göre elenmez;
kombolarda bileşenin çıkış tipine göre); --bitis verilmezse girişler son tamamlanmış takvim ayında biter, çıkışlar
sonraki 168 saatin verisiyle simüle edilir.
AI (kural 1-5 ve YATAY_DONUS): ay ay ileri yürüyen XGBoost. M ayının modeli yalnız M başından ÖNCE kapanmış işlemlerle
eğitilir (>= 200 işlem ve sınıf başına >= 30, yoksa o ay 'model yok' ve AI işlem yapmaz); eşik eğitim olasılıklarının
60. yüzdeliği (~en iyi %40). <KURAL>+AI = eşiği geçen işlemler. Yaklaşım: süzgeç işlem listesine sonradan uygulanır;
AI'nın elediği işlemin açık kaldığı sürede engellenen sinyaller geri gelmez. 17 özellik (karar mumu t'de): 24s ve 6s
getiri, RSI14, RSI2, hacim oranları (6 saat / 7 gün medyanı, mum / 14 mum), ATR %, EMA50 ve EMA200 uzaklığı,
Bollinger genişliği yüzdeliği, BTC 24s getirisi ve EMA200 uzaklığı, saat (sin, cos), log 24s hacim, BTC ADX14, coin
ADX14. Veri tek: rejim ayrı dosya/model değil, ADX'ler özellik olarak girer (§12.1).
İstatistik: dönemlere GİRİŞ zamanına göre atanır; beklenti = işlem başına ortalama net getiri; güven aralığı UTC giriş
günü bloklu bootstrap (2000 örnek). Bütçe: --butce (450, 90) USDT, işlem başına --islem (20) USDT, boş slot yoksa
sinyal atlanır, bileşik getiri yok. Raporun 8. bölümü her kuralın işlemlerini karar mumundaki BTC rejimine göre ayırır
(bilgi amaçlı; karar kuralını değiştirmez).

Bilinen sınırlar:
  - Evren bugünün en hacimli --evren paritesidir: dönem içinde delist olan coinler yok (hayatta kalma yanlılığı).
  - 1 saatlik mum içi fiyat yolu yaklaşıktır (gerçek sıra bilinmez); stop/hedef aynı mumdaysa sıra mum renginden.
  - AI süzgeci işlem listesine sonradan uygulanır; süzülen işlemin engellediği sinyaller geri gelmez.
  - BOT_VEKILI canlı botun 1 saatlik vekilidir, botun birebir simülasyonu değildir (bot 15 dakikalık çalışır).
  - Eksik saatler: göstergelerde atlanır (EMA/ATR/RSI/ADX pandas sürümünden bağımsız; RSI ve ADX son n+1 saat dolu
    ister); işlem içinde süreye sayılır, sonraki açılış boşluk sayılır. Sabit coin kararı paritenin ilk 720 dolu
    saatiyle verilir.

Kullanım (sunucuda, bot çalışırken de olur; API anahtarı gerekmez, yalnız herkese açık 1 saatlik fiyat verisi):
  cd /root && venv/bin/python tools/strateji_lab.py                  # 80 parite, 2023'ten son tamamlanmış aya
    (ilk çalıştırma ~2800 API isteğiyle 15-25 dk; sonrakiler önbellekten birkaç dakika; ek bellek ~200-300 MB)
  cd /root && venv/bin/python tools/strateji_lab.py --kurallar ERKEN_BIRIKIM YUKSEK_ISABET --ai-yok   # hızlı deneme
Çıktılar: strateji_lab_rapor.txt (özet), strateji_lab_rapor.json, strateji_lab_islemler.csv (her işlem bir satır;
trend/yatay ayrımı için 'rejim' ve 'bilesen' sütunları: tek dosya, Excel'de süzülür)
"""
import argparse
import heapq
import json
import math
import os
import sys
import time
from collections import Counter
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _yol in (KOK, os.path.join(KOK, 'tools')):
    if _yol not in sys.path:
        sys.path.insert(0, _yol)

import backfill_sinyaller as bf  # noqa: E402
import v184_simulasyon as vs  # noqa: E402

SAAT_MS = vs.SAAT_MS
GUN_MS = vs.GUN_MS
TF = '1h'
ISINMA_GUN = 45                 # göstergeler için dönem başından önceki geçmiş
BTC_ISINMA_GUN = 150            # yalnız BTC: BTC_TREND'in günlük MA100'ü dönem başında hazır olsun
MIN_GECMIS = 720                # bir paritenin ilk sinyalinden önce gereken dolu saat sayısı
DOLULUK = 0.9                   # uzun pencerelerde (168/720 saat) gereken dolu saat oranı (bakım boşlukları)
SABIT_COIN_STD = 0.001          # saatlik getiri std'si bunun altındaysa sabit coin sayılır (BTC hariç)
BOT_HACIM = bf.MIN_HACIM_USDT   # botun radarı: son 24 saat hacmi 12M USDT üstü
RADAR_N = 10

FEE = 0.001                     # komisyon, yön başına
SLIP_GIRIS = 0.0005             # piyasa emriyle giriş (açılış fiyatına)
SLIP_SEVIYE = 0.0015            # stop emri: seviyeden (ya da boşluklu açılıştan) kayma
SLIP_ZAMAN = 0.0005             # zaman/sinyal çıkışı: kapanışa piyasa emri
K_LISTESI = (0.5, 1.0, 2.0)
K_SUTUN = {0.5: 'getiri_k05', 1.0: 'getiri_k1', 2.0: 'getiri_k2'}

KADEME_AZAMI = 168
HS_HEDEF, HS_STOP, HS_AZAMI = 1.015, 0.96, 24
RSI2_STOP, RSI2_AZAMI = 0.93, 48
ORTA_STOP, ORTA_AZAMI = 0.97, 24
AZAMI_TUTMA = {'KADEMELI': KADEME_AZAMI, 'HEDEF_STOP': HS_AZAMI, 'RSI2_CIKIS': RSI2_AZAMI,
               'ORTA_BANT': ORTA_AZAMI}   # saat
EN_UZUN_TUTMA = max(AZAMI_TUTMA.values())
SINYAL_SERISI = {'RSI2_CIKIS': 'sma5', 'ORTA_BANT': 'sma20'}   # sinyal çıkışının kapanışla karşılaştırıldığı seri

REJIM_ADX_ESIK = 20             # BTC (YATAY_DONUS'ta coinin de) ADX14'ü: >= 20 trend, < 20 yatay (canlı botun eşiği)
REJIMLER = ('TREND_YUKARI', 'TREND_ASAGI', 'YATAY')
REJIM_YOK = 'BILINMIYOR'

KURAL_CIKIS = {'BOT_VEKILI': 'KADEMELI', 'ERKEN_BIRIKIM': 'KADEMELI', 'SIKISMA_KIRILIM': 'KADEMELI',
               'TREND_DIP_RSI2': 'RSI2_CIKIS', 'YUKSEK_ISABET': 'HEDEF_STOP', 'YATAY_DONUS': 'ORTA_BANT',
               'REJIM_KOMBO': 'KOMBO', 'REJIM_KOMBO_BOT': 'KOMBO', 'BTC_TREND': 'GUNLUK_MA100'}
KURALLAR = list(KURAL_CIKIS)
AI_KURALLARI = ['BOT_VEKILI', 'ERKEN_BIRIKIM', 'SIKISMA_KIRILIM', 'TREND_DIP_RSI2', 'YUKSEK_ISABET', 'YATAY_DONUS']
KOMBO_TREND = {'REJIM_KOMBO': 'ERKEN_BIRIKIM', 'REJIM_KOMBO_BOT': 'BOT_VEKILI'}   # TREND_YUKARI bileşeni
KOMBO_YATAY = 'YATAY_DONUS'                                                        # YATAY bileşeni (iki komboda)
KISA = {'BOT_VEKILI': 'BOT', 'ERKEN_BIRIKIM': 'ERKEN', 'SIKISMA_KIRILIM': 'SIKISMA', 'TREND_DIP_RSI2': 'RSI2',
        'YUKSEK_ISABET': 'ISABET', 'YATAY_DONUS': 'YATAY', 'REJIM_KOMBO': 'KOMBO', 'REJIM_KOMBO_BOT': 'KOMBO_B',
        'BTC_TREND': 'BTC_TR'}
KURAL_ACIKLAMA = {
    'BOT_VEKILI': '12M+ hacimli en çok yükselen 10 içinde, EMA20 üstü, ATR<=%3, RSI14>55, hacim 2.5x; KADEMELI',
    'ERKEN_BIRIKIM': '24s -%3..+%6, EMA50 üstü ve EMA20>EMA50, RSI14 50-68, 6 saatlik hacim birikimi 2x, 24s '
                     'zirve kırılımı, BTC EMA200 üstü; KADEMELI',
    'SIKISMA_KIRILIM': 'Bollinger genişliği 30 günün en dar %20si, üst bant kırılımı, hacim 2x, 24s <= %8; KADEMELI',
    'TREND_DIP_RSI2': 'büyük hacimli, EMA200 üstü, RSI2<10 dip alımı; SMA5 üstü kapanışta çık, stop -%7, 48 saat',
    'YUKSEK_ISABET': 'ERKEN_BIRIKIM girişleri; +%1.5 hedef / -%4 stop / 24 saat',
    'YATAY_DONUS': 'BTC rejimi YATAY, coin ADX14<20, kapanış Bollinger alt bandı (SMA20-2σ) altında, RSI14<30, 24s '
                   'hacim yeterli; ORTA_BANT: kapanış >= SMA20 olan ilk mumda çık, stop -%3, 24 saat',
    'REJIM_KOMBO': 'TREND_YUKARI: ERKEN_BIRIKIM (KADEMELI), YATAY: YATAY_DONUS (ORTA_BANT), TREND_ASAGI: alım yok; '
                   'paritede tek pozisyon iki bileşen için birlikte',
    'REJIM_KOMBO_BOT': 'REJIM_KOMBO gibi, trend bileşeni BOT_VEKILI (bugünkü botun vekili)',
    'BTC_TREND': 'BTC günlük kapanış MA100x1.02 üstü al, MA100x0.98 altı sat',
}
OZELLIKLER = ['ret24', 'ret6', 'rsi14', 'rsi2', 'vol_ratio6', 'vol_ratio14', 'atr_pct', 'ema50_uzaklik',
              'ema200_uzaklik', 'bbw_pct', 'btc_ret24', 'btc_ema200_uzaklik', 'saat_sin', 'saat_cos', 'log_vol24',
              'btc_adx14', 'adx14']
CSV_SUTUNLAR = (['kural', 'bilesen', 'rejim', 'sembol', 'giris_ts', 'cikis_ts', 'giris_fiyat', 'cikis_fiyat',
                 'cikis_tipi', 'getiri_k05', 'getiri_k1', 'getiri_k2', 'sure_saat'] + OZELLIKLER
                + ['ai_skor', 'ai_esik', 'ai_secildi'])
CSV_PARCA = 5000                # CSV bu kadar satırlık parçalarla yazılır (tablonun tam kopyası bellekte tutulmaz)
AI_SUTUNLARI = ['giris_ts', 'cikis_ts', 'getiri_k1'] + OZELLIKLER          # ai_ileri_yuruyus'un kullandıkları
ANALIZ_SUTUNLARI = ['kural', 'sembol', 'rejim', 'giris_ts', 'cikis_ts', 'getiri_k05', 'getiri_k1', 'getiri_k2',
                    'sure_saat', 'ai_skor', 'ai_esik', 'ai_secildi']        # analiz ve rapor yalnız bunları kullanır

AI_MIN_ISLEM, AI_MIN_SINIF, AI_YUZDELIK, AI_GERI_AY = 200, 30, 60, 12
BOOTSTRAP_N = 2000
BOOTSTRAP_PARCA = 250_000       # bootstrap çekilişleri bu kadar öğelik parçalarla (bellek); PCG64 akışı parçadan
                                # bağımsız: sonuç tek seferde çekilenle birebir aynı
KRITERLER = {
    1: "sınama n >= 100",
    2: "sınama beklentisi > 0 ve bootstrap P(>0) >= 0.99",
    3: "kuruluş beklentisi > 0",
    4: "sınama aylarının >= %60'ı pozitif",
    5: "k=2 maliyetle sınama beklentisi > 0",
    6: "AI karşılaştırması P(fark>0) >= 0.90",
}
KARAR_KURALI = (
    "KARAR KURALI (önceden kayıtlı; veriye bakılmadan yazıldı, aynen uygulanır):\n"
    "  Bir kural (ya da AI varyantı) ancak şu koşulların HEPSİ sağlanırsa \"GEÇTİ\" sayılır:\n"
    "  (1) sınama döneminde en az 100 işlem;\n"
    "  (2) sınama beklentisi (işlem başına ortalama net getiri) > 0 ve gün bloklu bootstrap P(>0) >= 0.99\n"
    "      (15 satır sınandığı için katı);\n"
    "  (3) kuruluş beklentisi > 0 (AI varyantlarında: modeli olan kuruluş aylarında);\n"
    "  (4) sınama aylarının (en az 1 işlemi olan takvim ayları) en az %60'ı pozitif;\n"
    "  (5) maliyet 2 katındayken (k=2) sınama beklentisi > 0.\n"
    "  AI varyantı ayrıca: AI karşılaştırmasında (aynı aylarda AI - kural beklentisi) P(fark>0) >= 0.90.\n"
    "  Aksi halde \"KALDI\" (sağlanmayan koşullar listelenir). Bilgi (koşul değil): sınama kazanma oranı >= %70\n"
    "  olanlar \"10'da 7 hedefi\" ile işaretlenir.")


# ------------------------------------------------------------------------------------------
# Yardımcılar
# ------------------------------------------------------------------------------------------
def _kaydir(x: np.ndarray, k: int) -> np.ndarray:
    """t'deki değer x[t-k] olur (baştaki k değer NaN)."""
    y = np.full(len(x), np.nan)
    if k == 0:
        y[:] = x
    elif k < len(x):
        y[k:] = x[:-k]
    return y


def _dolu(pencere: int) -> int:
    return int(math.ceil(pencere * DOLULUK))


def _ay_adi(ms) -> str:
    return vs._tarih(int(ms))[:7]


def _ay_bas(ms) -> int:
    return vs._ms(_ay_adi(ms) + '-01')


def _ay_ekle(ms, k: int) -> int:
    y, m = map(int, _ay_adi(ms).split('-'))
    m0 = y * 12 + (m - 1) + k
    return vs._ms(f"{m0 // 12:04d}-{m0 % 12 + 1:02d}-01")


def aylar(a_ms, b_ms) -> List[str]:
    """[a, b) aralığına değen takvim ayları ('YYYY-MM')."""
    sonuc, m = [], _ay_bas(a_ms)
    while m < b_ms:
        sonuc.append(_ay_adi(m))
        m = _ay_ekle(m, 1)
    return sonuc


def _ay_dizisi(ms: np.ndarray) -> np.ndarray:
    return np.asarray(ms, dtype='int64').astype('datetime64[ms]').astype('datetime64[M]').astype(str)


def net_getiri(giris_dolum, cikis_dolum, k=1.0):
    """Komisyon dahil net getiri: çıkış (1 - FEE) / giriş (1 + FEE) - 1 (FEE k ile ölçeklenir)."""
    return cikis_dolum * (1 - FEE * k) / (giris_dolum * (1 + FEE * k)) - 1


# ------------------------------------------------------------------------------------------
# Veri: tam saatlik UTC ızgarası (pencereler satıra değil zamana göre)
# ------------------------------------------------------------------------------------------
def izgaraya_yerlestir(d: np.ndarray, g0: int, n: int):
    """MumDeposu satırları (ts, o, h, l, c, v) -> g0'dan başlayan n saatlik ızgarada O, H, L, C, V dizileri.
    Eksik saat NaN. Sıfır/negatif/sonsuz fiyatlı mumun tamamı, sıfır/negatif hacmin yalnız hacmi NaN olur."""
    M = np.full((n, 5), np.nan)
    if len(d):
        ts = d[:, 0].astype('int64') - g0
        i = ts // SAAT_MS
        uygun = (ts >= 0) & (i < n) & (ts % SAAT_MS == 0)
        M[i[uygun]] = d[uygun, 1:6]
    with np.errstate(invalid='ignore'):
        bozuk = ~((M[:, :4] > 0) & np.isfinite(M[:, :4])).all(axis=1)
        M[bozuk] = np.nan
        M[~(M[:, 4] > 0) | ~np.isfinite(M[:, 4]), 4] = np.nan
    return M[:, 0].copy(), M[:, 1].copy(), M[:, 2].copy(), M[:, 3].copy(), M[:, 4].copy()


def kesit_sutunu(C: np.ndarray, V: np.ndarray):
    """Geçiş 1: bir paritenin 24s getirisi ve 24s USDT hacmi (kesit sıralaması için)."""
    qv = pd.Series(V * C)
    return C / _kaydir(C, 24) - 1, qv.rolling(24, min_periods=24).sum().to_numpy()


def saatlik_oynaklik(C: np.ndarray) -> float:
    with np.errstate(invalid='ignore', divide='ignore'):
        r = C[1:] / C[:-1] - 1
    r = r[np.isfinite(r)]
    return float(r.std()) if len(r) >= 2 else float('nan')


def ilk_gecmis(C: np.ndarray, adet: int = MIN_GECMIS) -> np.ndarray:
    """Paritenin ilk `adet` dolu saatini kapsayan baş bölümü. Sabit coin kararı yalnız bununla verilir: bu saatlerin
    hepsi paritenin ilk olası sinyalinden (MIN_GECMIS dolu saat) önce olduğu için karar geleceğe bakmaz (sınama
    döneminde peg'ini kaybeden bir sabit coin geriye dönük olarak evrene girmez)."""
    dolu = np.flatnonzero(np.isfinite(C))
    return C[:dolu[adet - 1] + 1] if len(dolu) >= adet else C


class Kesit:
    """Geçiş 1'in kesit panelleri (saat x parite, float32): kapanış, 24s getiri, 24s hacim."""

    def __init__(self, semboller, C, R24, V24, sabit, verisiz):
        self.semboller, self.C, self.R24, self.V24 = semboller, C, R24, V24
        self.sabit, self.verisiz = sabit, verisiz


def gecis1(depo, semboller, g0: int, n: int, log=print, koru=('BTC/USDT',)) -> Kesit:
    """Her parite: indir, ızgaraya koy, kesit sütunlarını panele yaz, belleği bırak (disk önbelleği kalır).
    Sabit coin: ilk MIN_GECMIS dolu saatte saatlik getiri std'si < SABIT_COIN_STD (BTC asla çıkarılmaz)."""
    m = len(semboller)
    C = np.full((n, m), np.nan, np.float32)
    R24, V24 = C.copy(), C.copy()
    tut, sabit, verisiz = [], [], []
    for j, s in enumerate(semboller):
        try:
            d = depo.getir(s, TF, g0, g0 + n * SAAT_MS)
        except vs.VeriYok:
            d = np.empty((0, 6))
        _, _, _, c, v = izgaraya_yerlestir(d, g0, n)
        del d
        oyn = saatlik_oynaklik(ilk_gecmis(c))
        if not np.isfinite(oyn):
            verisiz.append(s)
        elif s not in koru and oyn < SABIT_COIN_STD:
            sabit.append(s)
        else:
            r24, v24 = kesit_sutunu(c, v)
            C[:, j], R24[:, j], V24[:, j] = c, r24, v24
            tut.append(j)
        if depo.dizin:
            depo.bosalt(sym=s)
        if (j + 1) % 10 == 0 or j + 1 == m:
            log(f"  geçiş 1 (kesit): {j + 1}/{m} parite ({depo.istek} API isteği)")
    tut = np.asarray(tut, dtype=int)
    return Kesit([semboller[j] for j in tut], C[:, tut], R24[:, tut], V24[:, tut], sabit, verisiz)


def radar_ilk10(R24: np.ndarray, V24: np.ndarray, esik: float = BOT_HACIM, k: int = RADAR_N) -> np.ndarray:
    """Her saatte 24s hacmi >= esik olanlar içinde 24s getirisi en yüksek k parite (eşitlikte sütun sırası)."""
    with np.errstate(invalid='ignore'):
        uygun = (V24 >= esik) & np.isfinite(R24)
    if not R24.shape[1]:
        return uygun
    skor = np.where(uygun, R24.astype(np.float64), -np.inf)
    sec = np.argsort(-skor, axis=1, kind='stable')[:, :min(k, R24.shape[1])]
    ilk = np.zeros(R24.shape, dtype=bool)
    np.put_along_axis(ilk, sec, True, axis=1)
    return ilk & uygun


# ------------------------------------------------------------------------------------------
# Göstergeler (hepsi nedensel: t'deki değer yalnız <= t mumlarından; tests/test_strateji_lab.py doğrular)
# ------------------------------------------------------------------------------------------
def _ewm(x: np.ndarray, alpha: float) -> np.ndarray:
    """ewm(alpha, adjust=False) yalnız dolu saatler üzerinde: eksik saat atlanır (ignore_na=True anlamı) ve sonucu NaN.
    Dizi NaN içermeden hesaplandığı için sonuç pandas sürümünden bağımsızdır (pandas 3, NaN satırlardan sonraki
    değerin ağırlığını 2.x'ten farklı veriyor; sunucu 2.x, geliştirme 3.x)."""
    x = np.asarray(x, dtype=float)
    y = np.full(len(x), np.nan)
    dolu = np.isfinite(x)
    if dolu.any():
        y[dolu] = pd.Series(x[dolu]).ewm(alpha=alpha, adjust=False).mean().to_numpy()
    return y


def _ema(C: np.ndarray, n: int) -> np.ndarray:
    return _ewm(C, 2.0 / (n + 1))


def _rsi(C: np.ndarray, n: int) -> np.ndarray:
    """Wilder RSI: saatlik kazanç/kayıpların ewm(alpha=1/n, adjust=False) ortalaması. Önceki saatin kapanışı yoksa o
    saatin değişimi yoktur (atlanır); RSI ancak son n+1 saatin kapanışlarının hepsi doluysa verilir. Böylece eksik
    saatlerden sonra boşluktaki fiyat hareketini görmeyen bayat bir RSI ile sinyal üretilmez."""
    C = np.asarray(C, dtype=float)
    d = C - _kaydir(C, 1)
    ag = _ewm(np.clip(d, 0, None), 1.0 / n)
    al = _ewm(np.clip(-d, 0, None), 1.0 / n)
    with np.errstate(divide='ignore', invalid='ignore'):
        rsi = 100.0 - 100.0 / (1.0 + ag / al)
    rsi[(al == 0) & (ag == 0)] = 50.0
    rsi[pd.Series(np.isfinite(C)).rolling(n + 1, min_periods=n + 1).sum().to_numpy() != n + 1] = np.nan
    return rsi


def _adx(H: np.ndarray, L: np.ndarray, C: np.ndarray, n: int = 14) -> np.ndarray:
    """Wilder ADX(n). up = H[t] - H[t-1], down = L[t-1] - L[t]; +DM = up (up > down ve up > 0), -DM = down (down > up
    ve down > 0), yoksa 0. TR = max(H-L, |H-C[t-1]|, |L-C[t-1]|). Önceki saat eksikse (ya da ilk mumda) TR = H-L
    (atr_pct gibi) ve DM tanımsızdır (o saat DM ortalamalarında atlanır). ATR, +DM, -DM ve DX _ewm(1/n) ile (eksik saat
    atlanır); +DI = 100 +DMs/ATR, -DI = 100 -DMs/ATR (ATR 0 ise hareket yok: 0); DX = 100 |+DI - -DI| / (+DI + -DI)
    (toplam 0 ise 0); ADX = _ewm(DX, 1/n). _rsi gibi ADX ancak son n+1 saatin hepsi doluysa verilir: boşluktan hemen
    sonra, boşluktaki hareketi görmeyen bayat bir değerle rejim/sinyal üretilmez."""
    H, L, C = (np.asarray(x, dtype=float) for x in (H, L, C))
    dolu = np.isfinite(H) & np.isfinite(L) & np.isfinite(C)
    H, L, C = (np.where(dolu, x, np.nan) for x in (H, L, C))
    onceki_c = _kaydir(C, 1)
    with np.errstate(invalid='ignore'):
        up, down = H - _kaydir(H, 1), _kaydir(L, 1) - L
        tanimsiz = ~(np.isfinite(up) & np.isfinite(down))          # bu saat ya da önceki saat eksik
        pdm = np.where((up > down) & (up > 0), up, 0.0)
        mdm = np.where((down > up) & (down > 0), down, 0.0)
        pdm[tanimsiz] = np.nan
        mdm[tanimsiz] = np.nan
        tr = np.fmax(H - L, np.fmax(np.abs(H - onceki_c), np.abs(L - onceki_c)))   # önceki kapanış yoksa H - L
    atr, pdms, mdms = _ewm(tr, 1.0 / n), _ewm(pdm, 1.0 / n), _ewm(mdm, 1.0 / n)
    with np.errstate(invalid='ignore', divide='ignore'):
        pdi, mdi = 100.0 * pdms / atr, 100.0 * mdms / atr
        sifir = (atr == 0) & np.isfinite(pdms)                   # hiç fiyat hareketi yok (DM <= TR: DM'ler de 0)
        pdi[sifir] = 0.0
        mdi[sifir] = 0.0
        toplam = pdi + mdi
        dx = np.where(toplam > 0, 100.0 * np.abs(pdi - mdi) / toplam, 0.0)
    dx[~np.isfinite(toplam)] = np.nan
    adx = _ewm(dx, 1.0 / n)
    adx[pd.Series(dolu).rolling(n + 1, min_periods=n + 1).sum().to_numpy() != n + 1] = np.nan
    return adx


def rejim_belirle(adx: np.ndarray, C: np.ndarray, ema200: np.ndarray) -> np.ndarray:
    """Saatlik BTC rejimi (§12.1): ADX >= REJIM_ADX_ESIK ve kapanış > EMA200 TREND_YUKARI; ADX >= eşik ve kapanış <=
    EMA200 TREND_ASAGI; ADX < eşik YATAY; ADX, kapanış ya da EMA200 yoksa BILINMIYOR."""
    rejim = np.full(len(C), REJIM_YOK, dtype='<U12')
    with np.errstate(invalid='ignore'):
        bilinen = np.isfinite(adx) & np.isfinite(C) & np.isfinite(ema200)
        trend = bilinen & (adx >= REJIM_ADX_ESIK)
        rejim[trend & (C > ema200)] = 'TREND_YUKARI'
        rejim[trend & (C <= ema200)] = 'TREND_ASAGI'
        rejim[bilinen & (adx < REJIM_ADX_ESIK)] = 'YATAY'
    return rejim


def btc_baglami(C: np.ndarray, H: Optional[np.ndarray] = None, L: Optional[np.ndarray] = None) -> Dict[str, np.ndarray]:
    """BTC 1s bağlamı: btc_ok (kapanış > EMA200), 24s getiri, EMA200 uzaklığı, ADX14 ve rejim (H/L verilmezse ADX yok
    ve rejim her saatte BILINMIYOR: rejime bağlı sinyal çıkmaz)."""
    C = np.asarray(C, dtype=float)
    ema200 = _ema(C, 200)
    adx = _adx(H, L, C) if H is not None and L is not None else np.full(len(C), np.nan)
    with np.errstate(invalid='ignore', divide='ignore'):
        return {'ok': C > ema200, 'ret24': C / _kaydir(C, 24) - 1, 'ema200_uzaklik': C / ema200 - 1, 'adx14': adx,
                'rejim': rejim_belirle(adx, C, ema200)}


def saat_rejimi(rejim: np.ndarray, g0: int, ms) -> np.ndarray:
    """g0'dan başlayan saatlik ızgaradaki rejim dizisinden, verilen saatlerin (ms) rejimi; ızgara dışı: BILINMIYOR."""
    i = (np.asarray(ms, dtype='int64') - int(g0)) // SAAT_MS
    icinde = (i >= 0) & (i < len(rejim))
    sonuc = np.full(len(i), REJIM_YOK, dtype='<U12')
    sonuc[icinde] = rejim[i[icinde]]
    return sonuc


def gostergeler(O, H, L, C, V) -> Dict[str, np.ndarray]:
    c, qv = pd.Series(C), V * C
    qs = pd.Series(qv)
    g = {'c': C, 'qv': qv}
    with np.errstate(invalid='ignore', divide='ignore'):
        g['vol24'] = qs.rolling(24, min_periods=24).sum().to_numpy()
        g['ret24'] = C / _kaydir(C, 24) - 1
        g['ret6'] = C / _kaydir(C, 6) - 1
        for n in (20, 50, 200):
            g[f'ema{n}'] = _ema(C, n)
        g['sma5'] = c.rolling(5, min_periods=5).mean().to_numpy()
        g['rsi14'], g['rsi2'] = _rsi(C, 14), _rsi(C, 2)
        onceki = _kaydir(C, 1)
        tr = np.fmax(H - L, np.fmax(np.abs(H - onceki), np.abs(L - onceki)))   # önceki kapanış yoksa H - L
        g['atr_pct'] = _ewm(tr, 1.0 / 14) / C
        ort20 = c.rolling(20, min_periods=20).mean().to_numpy()
        sd20 = c.rolling(20, min_periods=20).std(ddof=0).to_numpy()
        g['sma20'] = ort20                                   # Bollinger orta bandı (ORTA_BANT çıkışı)
        g['bb_ust'] = ort20 + 2 * sd20
        g['bb_alt'] = ort20 - 2 * sd20
        g['adx14'] = _adx(H, L, C, 14)
        bbw = 4 * sd20 / ort20
        # bbw_pct[t]: bbw[t-720..t-1] içinde bbw[t-1]'e eşit ya da küçük olanların oranı (önceki mumun yüzdeliği)
        g['bbw_pct'] = _kaydir(pd.Series(bbw).rolling(720, min_periods=_dolu(720))
                               .rank(method='max', pct=True).to_numpy(), 1)
        med = _kaydir(qs.rolling(168, min_periods=_dolu(168)).median().to_numpy(), 6)   # qv[t-173..t-6] medyanı
        g['vol_ratio6'] = qs.rolling(6, min_periods=6).mean().to_numpy() / med
        g['surekli6'] = sum((_kaydir(qv, i) > med).astype(float) for i in range(6))
        g['vol_ratio14'] = qv / _kaydir(qs.rolling(14, min_periods=14).mean().to_numpy(), 1)
        g['qv_ort20'] = _kaydir(qs.rolling(20, min_periods=20).mean().to_numpy(), 1)
        g['high24_prev'] = _kaydir(pd.Series(H).rolling(24, min_periods=24).max().to_numpy(), 1)
        g['ort_gunluk_hacim30'] = qs.rolling(720, min_periods=_dolu(720)).mean().to_numpy() * 24
    g['gecmis'] = np.cumsum(np.isfinite(C)) >= MIN_GECMIS
    return g


def sinyaller(g: dict, btc_ok: np.ndarray, radar: Optional[np.ndarray], min_hacim: float,
              buyuk_hacim: float, rejim: Optional[np.ndarray] = None) -> Dict[str, np.ndarray]:
    """Karar mumu t'deki sinyaller (bool). NaN içeren karşılaştırmalar yanlıştır: her girdi sonlu olmalı. radar yoksa
    BOT_VEKILI, rejim (BTC) yoksa YATAY_DONUS üretilmez."""
    c = g['c']
    ok = g['gecmis'] & np.isfinite(c)
    s = {}
    with np.errstate(invalid='ignore'):
        if radar is not None:
            s['BOT_VEKILI'] = (ok & radar & (c > g['ema20']) & (g['atr_pct'] <= 0.03) & (g['rsi14'] > 55)
                               & (g['vol_ratio14'] > 2.5))
        erken = (ok & btc_ok & (g['ret24'] >= -0.03) & (g['ret24'] <= 0.06) & (c > g['ema50'])
                 & (g['ema20'] > g['ema50']) & (g['rsi14'] >= 50) & (g['rsi14'] <= 68) & (g['vol_ratio6'] >= 2.0)
                 & (g['surekli6'] >= 4) & (c > g['high24_prev']) & (g['vol24'] >= min_hacim))
        s['ERKEN_BIRIKIM'] = erken
        s['SIKISMA_KIRILIM'] = (ok & btc_ok & (g['bbw_pct'] <= 0.20) & (c > g['bb_ust'])
                                & (g['qv'] >= 2 * g['qv_ort20']) & (g['ret24'] <= 0.08) & (g['vol24'] >= min_hacim))
        s['TREND_DIP_RSI2'] = (ok & btc_ok & (g['ort_gunluk_hacim30'] >= buyuk_hacim) & (c > g['ema200'])
                               & (g['rsi2'] < 10))
        s['YUKSEK_ISABET'] = erken.copy()
        if rejim is not None:
            s['YATAY_DONUS'] = (ok & (rejim == 'YATAY') & (g['adx14'] < REJIM_ADX_ESIK) & (c < g['bb_alt'])
                                & (g['rsi14'] < 30) & (g['vol24'] >= min_hacim))
    return s


def kombo_bilesenleri(kural: str, s: Dict[str, np.ndarray], rejim: Optional[np.ndarray]):
    """REJIM_KOMBO / REJIM_KOMBO_BOT: {bileşen: (sinyal, çıkış tipi)}, öncelik sırasıyla (trend bileşeni önce).
    TREND_YUKARI saatinde trend bileşeninin (ERKEN_BIRIKIM / BOT_VEKILI) sinyali KADEMELI ile, YATAY saatinde
    YATAY_DONUS sinyali ORTA_BANT ile; TREND_ASAGI ve BILINMIYOR: alım yok. Bileşen sinyali yoksa (radar ya da rejim
    verilmemiş) None."""
    trend = KOMBO_TREND[kural]
    if rejim is None or trend not in s or KOMBO_YATAY not in s:
        return None
    return {trend: (s[trend] & (rejim == 'TREND_YUKARI'), KURAL_CIKIS[trend]),
            KOMBO_YATAY: (s[KOMBO_YATAY] & (rejim == 'YATAY'), KURAL_CIKIS[KOMBO_YATAY])}


def ozellikler(g: dict, btc: dict, ts: np.ndarray, idx) -> Dict[str, np.ndarray]:
    """AI özellikleri, karar mumu t'de (saat: giriş mumu t+1'in UTC saati)."""
    idx = np.asarray(idx, dtype=int)
    saat = ((ts[idx] + SAAT_MS) // SAAT_MS) % 24
    c = g['c'][idx]
    with np.errstate(invalid='ignore', divide='ignore'):
        return {'ret24': g['ret24'][idx], 'ret6': g['ret6'][idx], 'rsi14': g['rsi14'][idx], 'rsi2': g['rsi2'][idx],
                'vol_ratio6': g['vol_ratio6'][idx], 'vol_ratio14': g['vol_ratio14'][idx],
                'atr_pct': g['atr_pct'][idx], 'ema50_uzaklik': c / g['ema50'][idx] - 1,
                'ema200_uzaklik': c / g['ema200'][idx] - 1, 'bbw_pct': g['bbw_pct'][idx],
                'btc_ret24': btc['ret24'][idx], 'btc_ema200_uzaklik': btc['ema200_uzaklik'][idx],
                'saat_sin': np.sin(2 * np.pi * saat / 24), 'saat_cos': np.cos(2 * np.pi * saat / 24),
                'log_vol24': np.log(g['vol24'][idx]),
                'btc_adx14': btc['adx14'][idx] if 'adx14' in btc else np.full(len(idx), np.nan),
                'adx14': g['adx14'][idx]}


# ------------------------------------------------------------------------------------------
# Çıkış motoru
# ------------------------------------------------------------------------------------------
def kademe_stopu(tepe: float, giris: float) -> float:
    """Kullanıcının basamakları: zirve kazancı g'ye göre stop seviyesi (yalnız yukarı kaydırılarak kullanılır)."""
    g = tepe / giris - 1
    if g < 0.05:
        return giris * 0.98
    if g < 0.10:
        return tepe * 0.97
    if g < 0.50:
        return tepe * 0.96
    return tepe * 0.90


def cikis_simule(tip: str, O, H, L, C, S, e: int, k: float = 1.0):
    """e mumunun açılışında girilen işlemi 1 saatlik mumlarla kapatır. O/H/L/C/S: liste ya da dizi (NaN = eksik saat;
    eksik saat atlanır ama tutma süresine sayılır, sonraki mumun açılışı boşluk sayılır). S, sinyal çıkışının serisi
    (SINYAL_SERISI): RSI2_CIKIS'te SMA5 (kapanış > SMA5), ORTA_BANT'ta SMA20 (kapanış >= SMA20); diğer çıkışlarda
    kullanılmaz. S o mumda eksikse sinyal çıkışı o mumda yoktur. Mum içi yol: yeşil A->D->Y->K, kırmızı A->Y->D->K.
    Her noktada: (a) nokta <= stop ise çık (açılışta boşluksa açılıştan, değilse stop seviyesinden; kayma SLIP_SEVIYE);
    hedef varsa nokta >= hedef ise hedeften (açılış zaten üstündeyse açılıştan; kaymasız); (b) zirve ve stop
    güncellenir. Sinyal çıkışı mumun kapanışında (stoptan sonra) bakılır, giriş mumu dahil. Azami sürenin son mumunun
    kapanışında çıkılır; o mum eksikse süre dolduktan sonraki ilk mumun AÇILIŞINDAN (stop/hedef o mumda artık işlemez).
    Dönen: (çıkış mumu, giriş dolumu, çıkış dolumu, çıkış tipi) ya da None (veri bitti, işlem açık)."""
    n = len(O)
    giris = O[e] * (1 + SLIP_GIRIS * k)
    kademeli, rsi2, orta = tip == 'KADEMELI', tip == 'RSI2_CIKIS', tip == 'ORTA_BANT'
    if kademeli:
        stop, hedef, azami = giris * 0.98, math.inf, KADEME_AZAMI
    elif tip == 'HEDEF_STOP':
        stop, hedef, azami = giris * HS_STOP, giris * HS_HEDEF, HS_AZAMI
    elif rsi2:
        stop, hedef, azami = giris * RSI2_STOP, math.inf, RSI2_AZAMI
    elif orta:
        stop, hedef, azami = giris * ORTA_STOP, math.inf, ORTA_AZAMI
    else:
        raise ValueError(f"bilinmeyen çıkış: {tip}")
    ilk_stop, tepe, son = stop, 0.0, e + azami - 1
    kayma_s, kayma_z = 1 - SLIP_SEVIYE * k, 1 - SLIP_ZAMAN * k
    for b in range(e, n):
        o = O[b]
        if not o == o:          # eksik saat (süreye sayılır)
            continue
        if b > son:             # azami sürenin son mumu eksikti: süre dolduktan sonraki ilk fiyattan çık
            return b, giris, o * kayma_z, 'ZAMAN'
        h, l, c = H[b], L[b], C[b]
        yol = (o, l, h, c) if c >= o else (o, h, l, c)
        for i in range(4):
            p = yol[i]
            if p <= stop:
                return b, giris, (p if i == 0 else stop) * kayma_s, ('IZ_STOP' if stop > ilk_stop else 'STOP')
            if p >= hedef:
                return b, giris, (p if i == 0 else hedef), 'HEDEF'
            if kademeli and p > tepe:
                tepe = p
                yeni = kademe_stopu(tepe, giris)
                if yeni > stop:
                    stop = yeni
        if rsi2 and c > S[b]:
            return b, giris, c * kayma_z, 'SINYAL'
        if orta and c >= S[b]:
            return b, giris, c * kayma_z, 'SINYAL'
        if b >= son:
            return b, giris, c * kayma_z, 'ZAMAN'
    return None


def _liste(x):
    return x.tolist() if hasattr(x, 'tolist') else x                # hızlı skaler erişim


def _islem_dizisi(adaylar, O, H, L, C, seriler: dict, ilk_e: int, son_e: dict):
    """Ortak döngü. adaylar: [(t, çıkış tipi, bileşen)] t'ye göre artan, her t'de en çok bir aday. Paritede tek
    pozisyon: aday ancak önceki işlemin (hangi bileşenden olursa olsun) çıkış mumundan sonraki mumda alınır. Giriş mumu
    e = t+1: ilk_e <= e <= son_e[tip] ve açılışı dolu olmalı. seriler: tip -> sinyal çıkışı serisi (SINYAL_SERISI)."""
    sonuc, acik, son_cikis = [], 0, -1
    for t, tip, bilesen in adaylar:
        if t + 1 < ilk_e or t <= son_cikis:
            continue
        e = t + 1
        if e > son_e[tip]:
            continue
        if not O[e] == O[e]:
            continue
        S = seriler.get(tip)
        sim1 = cikis_simule(tip, O, H, L, C, S, e, 1.0)
        if sim1 is None:            # veri bitti, işlem açık: hariç (pozisyon sonraki sinyalleri de engeller)
            acik += 1
            break
        b, giris, cikis, ctip = sim1
        r = []
        for k in K_LISTESI:
            sim = sim1 if k == 1.0 else cikis_simule(tip, O, H, L, C, S, e, k)
            if sim is None:         # k=1 kapandı, bu k veri sonuna dek açık: son kapanıştan değerlenir
                son_kapanis = next(x for x in reversed(C) if x == x)
                sim = (None, O[e] * (1 + SLIP_GIRIS * k), son_kapanis * (1 - SLIP_ZAMAN * k), None)
            r.append(net_getiri(sim[1], sim[2], k))
        sonuc.append((t, e, b, giris, cikis, ctip, r[0], r[1], r[2], bilesen))
        son_cikis = b
    return sonuc, acik


def kural_islemleri(sinyal: np.ndarray, tip: str, O, H, L, C, S5, ilk_e: int = 0, son_e: Optional[int] = None):
    """Sinyallerden işlem dizisi: paritede tek pozisyon, yeni sinyal ancak çıkış mumundan sonraki mumda. Giriş mumu
    eksikse sinyal atlanır; giriş mumu ilk_e..son_e aralığında olmalı (çağıran son_e'yi azami tutma süresi verinin
    içinde kalacak şekilde verir; veri sonundaki girişler sonuçlarına göre elenmesin diye). Dizi YALNIZ k=1 ile
    belirlenir; k=0.5/2 getirileri aynı girişten yeniden simüle edilir (seviyeler giriş dolumuna bağlı). k=1 kapanıp
    başka bir k'da işlem veri sonuna dek açık kalırsa (seviye farkı) o k'nın getirisi son kapanıştan hesaplanır.
    S5: sinyal çıkışının serisi (RSI2_CIKIS: SMA5, ORTA_BANT: SMA20; diğerlerinde kullanılmaz).
    Dönen: ([(t, e, b, giris, cikis, tip, r05, r1, r2)], bitişte açık kalan (hariç tutulan) sayısı)."""
    O, H, L, C, S5 = (_liste(x) for x in (O, H, L, C, S5))
    n = len(O)
    son_e = n - 1 if son_e is None else min(son_e, n - 1)
    adaylar = [(int(t), tip, None) for t in np.flatnonzero(sinyal)]
    sonuc, acik = _islem_dizisi(adaylar, O, H, L, C, {tip: S5}, ilk_e, {tip: son_e})
    return [x[:9] for x in sonuc], acik


def kombo_islemleri(bilesenler: Dict[str, tuple], O, H, L, C, seriler: Dict[str, object], ilk_e: int = 0,
                    son_e: Optional[Dict[str, int]] = None):
    """Rejime göre geçiş (REJIM_KOMBO*). bilesenler: {bileşen adı: (sinyal dizisi, çıkış tipi)}, öncelik sırasıyla.
    Paritede tek pozisyon İKİ bileşen için birlikte geçerli: yeni sinyal (hangi bileşenden olursa olsun) ancak önceki
    işlemin çıkış mumundan sonraki mumda. Aynı mumda iki bileşen birden sinyal verirse (rejimler ayrık olduğundan
    olmamalı) öncelikli (ilk) bileşen alınır. Her işlem kendi bileşeninin çıkış tipiyle kapanır; son_e (veri sonu
    kuralı) çıkış tipine göre: {tip: son giriş mumu}. seriler: {tip: sinyal çıkışı serisi}. k=0.5/2 tek kuraldaki gibi.
    Dönen: ([(t, e, b, giris, cikis, tip, r05, r1, r2, bileşen)], bitişte açık kalan sayısı)."""
    O, H, L, C = (_liste(x) for x in (O, H, L, C))
    seriler = {tip: _liste(x) for tip, x in seriler.items()}
    n = len(O)
    adlar = list(bilesenler)
    kod = np.full(n, -1, dtype=np.int16)
    for i in range(len(adlar) - 1, -1, -1):                          # öncelikli bileşen en son yazılır
        kod[np.asarray(bilesenler[adlar[i]][0], dtype=bool)] = i
    son = {}
    for _, tip in bilesenler.values():
        son[tip] = n - 1 if son_e is None or tip not in son_e else min(son_e[tip], n - 1)
    adaylar = [(int(t), bilesenler[adlar[kod[t]]][1], adlar[kod[t]]) for t in np.flatnonzero(kod >= 0)]
    return _islem_dizisi(adaylar, O, H, L, C, seriler, ilk_e, son)


def islem_tablosu(kural, sembol: str, satirlar, ts: np.ndarray, oz: Optional[dict],
                  rejim: Optional[np.ndarray] = None) -> pd.DataFrame:
    """İşlem satırları -> tablo. kural: kural adı ya da satır satır kural adları (listesi). 'rejim': karar mumu t'deki
    BTC rejimi (verilmezse BILINMIYOR); 'bilesen': satırın 10. öğesi (kombo işleminin bileşeni) varsa o, yoksa kural."""
    if not satirlar:
        return pd.DataFrame()
    sutun = list(zip(*satirlar))
    t, e, b, giris, cikis, ctip, r05, r1, r2 = (np.asarray(x) for x in sutun[:9])
    t = t.astype(int)
    veri = {'kural': kural, 'bilesen': list(sutun[9]) if len(sutun) > 9 else kural,
            'rejim': rejim[t] if rejim is not None else REJIM_YOK, 'sembol': sembol, 'giris_ts': ts[e.astype(int)],
            'cikis_ts': ts[b.astype(int)] + SAAT_MS, 'giris_fiyat': giris.astype(float),
            'cikis_fiyat': cikis.astype(float), 'cikis_tipi': ctip, 'getiri_k05': r05.astype(float),
            'getiri_k1': r1.astype(float), 'getiri_k2': r2.astype(float), 'sure_saat': (b - e + 1).astype(float)}
    for ad in OZELLIKLER:
        veri[ad] = oz[ad] if oz is not None else np.nan
    return pd.DataFrame(veri)


def parite_islemleri(sembol, O, H, L, C, V, ts, btc, radar_sutunu, kurallar, min_hacim, buyuk_hacim, ilk_e,
                     analiz_sonu_i=None):
    """Geçiş 2: bir paritenin göstergeleri, sinyalleri ve işlemleri. Dönen: ([tüm kuralların işlemleri tek tabloda,
    özelliklerle] ya da işlem yoksa [], {kural: bitişte açık kalan}). Giriş mumu: ilk_e <= e < analiz_sonu_i ve azami
    tutma süresi veride kalmalı (e + azami <= veri uzunluğu; kombolarda işlemin bileşeninin çıkış tipine göre). Kombo
    istenirse bileşen sinyalleri (kurallarda olmasalar da) hesaplanır."""
    g = gostergeler(O, H, L, C, V)
    rejim = btc.get('rejim')
    s = sinyaller(g, btc['ok'], radar_sutunu, min_hacim, buyuk_hacim, rejim)
    listeler = [O.tolist(), H.tolist(), L.tolist(), C.tolist()]
    seriler = {tip: g[ad].tolist() for tip, ad in SINYAL_SERISI.items()}
    giris_siniri = len(O) if analiz_sonu_i is None else min(analiz_sonu_i, len(O))     # bu mumdan itibaren giriş yok
    hepsi, kural_adi, acik = [], [], {}
    for kural in kurallar:
        if kural in KOMBO_TREND:
            bilesenler = kombo_bilesenleri(kural, s, rejim)
            if bilesenler is None:
                continue
            son_e = {tip: min(giris_siniri - 1, len(O) - AZAMI_TUTMA[tip]) for _, tip in bilesenler.values()}
            satirlar, acik[kural] = kombo_islemleri(bilesenler, *listeler, seriler, ilk_e=ilk_e, son_e=son_e)
        elif kural in s:
            tip = KURAL_CIKIS[kural]
            son_e = min(giris_siniri - 1, len(O) - AZAMI_TUTMA[tip])
            satirlar, acik[kural] = kural_islemleri(s[kural], tip, *listeler, seriler.get(tip), ilk_e=ilk_e,
                                                    son_e=son_e)
            satirlar = [x + (kural,) for x in satirlar]                  # bileşen = kuralın kendisi
        else:
            continue
        hepsi += satirlar
        kural_adi += [kural] * len(satirlar)
    if not hepsi:
        return [], acik
    oz = ozellikler(g, btc, ts, [x[0] for x in hepsi])                  # tek tablo: kural başına tablo kurulmaz
    return [islem_tablosu(kural_adi, sembol, hepsi, ts, oz, rejim)], acik


# ------------------------------------------------------------------------------------------
# BTC_TREND: günlük kapanışlar 1s ızgarasından (yalnız 24 saati tam günler)
# ------------------------------------------------------------------------------------------
def btc_gunluk(ts: np.ndarray, C: np.ndarray):
    """(gün başı ms, gün kapanışı) — gün yalnız 24 saatin hepsi varsa sayılır; kapanış son saatin kapanışı."""
    df = pd.DataFrame({'gun': ts // GUN_MS, 'c': C})
    grp = df.groupby('gun')['c']
    tam = grp.count() == 24
    kap = grp.last()[tam]
    return kap.index.to_numpy(dtype='int64') * GUN_MS, kap.to_numpy(dtype=float)


def btc_trend_islemleri(gun_bas: np.ndarray, kapanis: np.ndarray, ilk_giris_ms: int,
                        son_giris_ms: Optional[int] = None):
    """Histerezisli MA100: kapanış > MA100*1.02 al, < MA100*0.98 sat; dolumlar o kapanıştan (kaymalı). Giriş
    zamanı [ilk_giris_ms, son_giris_ms) içinde olmalı; veri sonunda hâlâ açık olan işlem hariç (sayısı döner).
    'rejim' burada BILINMIYOR; calistir karar saatindeki (giriş - 1 saat) BTC rejimini yazar (saat_rejimi)."""
    ma = pd.Series(kapanis).rolling(100, min_periods=100).mean().to_numpy()
    son_giris = math.inf if son_giris_ms is None else son_giris_ms
    satirlar, poz = [], None
    for i in range(len(kapanis)):
        if not np.isfinite(ma[i]):
            continue
        c, zaman = float(kapanis[i]), int(gun_bas[i]) + GUN_MS
        if poz is None:
            if ilk_giris_ms <= zaman < son_giris and c > ma[i] * 1.02:
                poz = (zaman, c)
        elif c < ma[i] * 0.98:
            z0, c0 = poz
            r = [net_getiri(c0 * (1 + SLIP_GIRIS * k), c * (1 - SLIP_ZAMAN * k), k) for k in K_LISTESI]
            satirlar.append({'kural': 'BTC_TREND', 'bilesen': 'BTC_TREND', 'rejim': REJIM_YOK, 'sembol': 'BTC/USDT',
                             'giris_ts': z0, 'cikis_ts': zaman, 'giris_fiyat': c0 * (1 + SLIP_GIRIS),
                             'cikis_fiyat': c * (1 - SLIP_ZAMAN), 'cikis_tipi': 'MA100', 'getiri_k05': r[0],
                             'getiri_k1': r[1], 'getiri_k2': r[2], 'sure_saat': (zaman - z0) / SAAT_MS})
            poz = None
    df = pd.DataFrame(satirlar)
    for ad in OZELLIKLER:
        df[ad] = np.nan
    return df, int(poz is not None)


# ------------------------------------------------------------------------------------------
# AI: ay ay ileri yürüyen model
# ------------------------------------------------------------------------------------------
def model_kur():
    try:
        from xgboost import XGBClassifier
        return XGBClassifier(n_estimators=200, max_depth=3, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8,
                             min_child_weight=5, random_state=0, n_jobs=2, eval_metric='logloss')
    except ImportError:
        from sklearn.ensemble import HistGradientBoostingClassifier
        return HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=200, random_state=0)


def ai_ileri_yuruyus(df: pd.DataFrame, ayrim_ms: int, son_ms: int, model_kurucu: Callable = model_kur):
    """Tek kuralın işlemleri -> (skor, eşik) dizileri (df sırasıyla; modelsiz ayda NaN) ve ay kayıtları.
    M ayı (ayrim-12 aydan sona): eğitim = çıkışı M başından ÖNCE olan işlemler (etiket: k=1 net getiri > 0);
    >= 200 işlem ve sınıf başına >= 30 yoksa 'model yok'. Eşik: modelin kendi eğitim olasılıklarının 60. yüzdeliği.
    df'de giris_ts, cikis_ts, getiri_k1 ve OZELLIKLER yeterli. Eğitim ve hedef satırları tek predict_proba çağrısıyla
    puanlanır (olasılık satır satır hesaplanır; iki ayrı çağrıyla birebir aynı, çağrı başı sabit maliyet bir kez)."""
    skor, esik = np.full(len(df), np.nan), np.full(len(df), np.nan)
    giris, cikis = df['giris_ts'].to_numpy(dtype='int64'), df['cikis_ts'].to_numpy(dtype='int64')
    X = df[OZELLIKLER].to_numpy(dtype=np.float32)
    y = (df['getiri_k1'].to_numpy() > 0).astype(int)
    kayitlar = []
    M = _ay_ekle(_ay_bas(ayrim_ms), -AI_GERI_AY)
    while M < son_ms:
        S = _ay_ekle(M, 1)
        hedef = (giris >= M) & (giris < S)
        egit = cikis < M
        n1 = int(y[egit].sum())
        n0 = int(egit.sum()) - n1
        kayit = {'ay': _ay_adi(M), 'egitim': int(egit.sum()), 'islem': int(hedef.sum()), 'model': False}
        if egit.sum() >= AI_MIN_ISLEM and min(n0, n1) >= AI_MIN_SINIF:
            kayit['model'] = True
            if hedef.any():
                model = model_kurucu()
                Xe = X[egit]
                model.fit(Xe, y[egit])
                p = model.predict_proba(np.concatenate([Xe, X[hedef]]))[:, 1]
                e = float(np.percentile(p[:len(Xe)], AI_YUZDELIK))
                skor[hedef] = p[len(Xe):]
                esik[hedef] = e
                kayit.update(esik=e, secilen=int((skor[hedef] >= e).sum()))
        kayitlar.append(kayit)
        M = S
    return skor, esik, kayitlar


def auc(skor: np.ndarray, etiket: np.ndarray) -> Optional[float]:
    m = np.isfinite(skor)
    if m.sum() < 2 or len(set(etiket[m].tolist())) < 2:
        return None
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score(etiket[m], skor[m]))


# ------------------------------------------------------------------------------------------
# İstatistik
# ------------------------------------------------------------------------------------------
def gun_bootstrap(r: np.ndarray, giris_ts: np.ndarray, n: int = BOOTSTRAP_N, tohum: int = 0) -> dict:
    """UTC giriş günü bloklu bootstrap: günler yerine koyarak çekilir; istatistik = toplam r / işlem sayısı."""
    _, inv = np.unique(np.asarray(giris_ts, dtype='int64') // GUN_MS, return_inverse=True)
    top, adet = np.bincount(inv, weights=r), np.bincount(inv).astype(float)
    D, rng, ist = len(top), np.random.default_rng(tohum), np.empty(n)
    parca = max(1, BOOTSTRAP_PARCA // D)
    for i in range(0, n, parca):
        idx = rng.integers(0, D, size=(min(parca, n - i), D))
        ist[i:i + len(idx)] = top[idx].sum(1) / adet[idx].sum(1)
    return {'ga95_pct': [float(np.percentile(ist, 2.5)) * 100, float(np.percentile(ist, 97.5)) * 100],
            'p_pozitif': float((ist > 0).mean())}


def istatistik(df: pd.DataFrame) -> dict:
    """Bir satırın (kural/AI varyantı) bir dönemdeki işlem istatistikleri (getiriler %)."""
    n = len(df)
    if not n:
        return {'n': 0}
    r = df['getiri_k1'].to_numpy(dtype=float)
    kaz, kay = r[r > 0], r[r <= 0]
    sira = np.lexsort((df['giris_ts'].to_numpy(), df['cikis_ts'].to_numpy()))
    seri = en_uzun = 0
    for x in r[sira]:
        seri = seri + 1 if x <= 0 else 0
        en_uzun = max(en_uzun, seri)
    ay_top = pd.Series(r).groupby(_ay_dizisi(df['giris_ts'].to_numpy())).sum()
    o = {'n': n, 'kazanma_pct': float((r > 0).mean()) * 100,
         'ort_kazanc_pct': float(kaz.mean()) * 100 if len(kaz) else None,
         'ort_kayip_pct': float(kay.mean()) * 100 if len(kay) else None,
         'pf': float(kaz.sum() / -kay.sum()) if len(kay) and kay.sum() < 0 else None,
         'pf_sonsuz': bool(len(kaz) and not (kay < 0).any()),        # kayıp yok: PF sınırsız (JSON'da None)
         'beklenti_pct': float(r.mean()) * 100,
         'beklenti_k05_pct': float(df['getiri_k05'].mean()) * 100,
         'beklenti_k2_pct': float(df['getiri_k2'].mean()) * 100,
         'medyan_sure_saat': float(np.median(df['sure_saat'])), 'kayip_serisi': int(en_uzun),
         'ay': int(len(ay_top)), 'pozitif_ay': int((ay_top > 0).sum()),
         'pozitif_ay_pct': float((ay_top > 0).mean()) * 100}
    o['rr'] = (o['ort_kazanc_pct'] / -o['ort_kayip_pct']
               if o['ort_kazanc_pct'] is not None and o['ort_kayip_pct'] not in (None, 0.0) else None)
    o.update(gun_bootstrap(r, df['giris_ts'].to_numpy()))
    return o


def karar_ver(sinama: dict, kurulus: dict, ai_mi: bool = False, ai_p: Optional[float] = None) -> dict:
    """Önceden kayıtlı karar kuralı (KARAR_KURALI)."""
    n = sinama.get('n', 0)
    kalan = []
    if n < 100:
        kalan.append(1)
    if not (n and sinama['beklenti_pct'] > 0 and sinama['p_pozitif'] >= 0.99):
        kalan.append(2)
    if not (kurulus.get('n', 0) and kurulus['beklenti_pct'] > 0):
        kalan.append(3)
    if not (n and sinama['pozitif_ay_pct'] >= 60):
        kalan.append(4)
    if not (n and sinama['beklenti_k2_pct'] > 0):
        kalan.append(5)
    if ai_mi and not (ai_p is not None and ai_p >= 0.90):
        kalan.append(6)
    return {'sonuc': 'KALDI' if kalan else 'GEÇTİ', 'kalan': [f"({i}) {KRITERLER[i]}" for i in kalan],
            'hedef_70': bool(n and sinama['kazanma_pct'] >= 70)}


def ai_karsilastir(df: pd.DataFrame, n: int = BOOTSTRAP_N, tohum: int = 0) -> dict:
    """df: kuralın modelli aylardaki sınama işlemleri (ai_secildi sütunuyla). Fark = AI beklentisi - kural beklentisi;
    giriş günleri ortak çekilir (iki seri aynı günlerle). AI'nın hiç işlemi olmayan örnek 'pozitif değil' sayılır."""
    if not len(df) or not df['ai_secildi'].any():
        return {'n_kural': int(len(df)), 'n_ai': 0, 'fark_pct': None, 'ga95_pct': None, 'p_pozitif': 0.0}
    r = df['getiri_k1'].to_numpy(dtype=float)
    s = df['ai_secildi'].to_numpy(dtype=bool)
    _, inv = np.unique(df['giris_ts'].to_numpy(dtype='int64') // GUN_MS, return_inverse=True)
    tk, ak = np.bincount(inv, weights=r), np.bincount(inv).astype(float)
    ta, aa = np.bincount(inv, weights=r * s, minlength=len(tk)), np.bincount(inv, weights=s.astype(float),
                                                                            minlength=len(tk))
    D, rng, fark = len(tk), np.random.default_rng(tohum), np.empty(n)
    parca = max(1, BOOTSTRAP_PARCA // D)
    with np.errstate(invalid='ignore', divide='ignore'):
        for i in range(0, n, parca):
            idx = rng.integers(0, D, size=(min(parca, n - i), D))
            fark[i:i + len(idx)] = ta[idx].sum(1) / aa[idx].sum(1) - tk[idx].sum(1) / ak[idx].sum(1)
    sonlu = fark[np.isfinite(fark)]
    return {'n_kural': int(len(df)), 'n_ai': int(s.sum()), 'beklenti_kural_pct': float(r.mean()) * 100,
            'beklenti_ai_pct': float(r[s].mean()) * 100, 'fark_pct': float(r[s].mean() - r.mean()) * 100,
            'ga95_pct': ([float(np.percentile(sonlu, 2.5)) * 100, float(np.percentile(sonlu, 97.5)) * 100]
                         if len(sonlu) else None),
            'p_pozitif': float((np.nan_to_num(fark, nan=-1.0) > 0).mean())}


# ------------------------------------------------------------------------------------------
# Bütçe oynatma
# ------------------------------------------------------------------------------------------
def butce_oynat(df: pd.DataFrame, butce: float, islem: float) -> np.ndarray:
    """Girişe göre kronolojik; slot = floor(butce/islem). Giriş anında açık pozisyon < slot ise alınır; aynı anda
    biten pozisyonun slotu girişten önce boşalır. Dönen: alındı maskesi (df sırasıyla)."""
    slot = int(butce // islem)
    alindi = np.zeros(len(df), dtype=bool)
    if slot <= 0 or not len(df):
        return alindi
    g, c = df['giris_ts'].to_numpy(dtype='int64'), df['cikis_ts'].to_numpy(dtype='int64')
    sira = np.lexsort((df['sembol'].astype(str).to_numpy(), g))
    acik: list = []
    for i in sira:
        while acik and acik[0] <= g[i]:
            heapq.heappop(acik)
        if len(acik) < slot:
            alindi[i] = True
            heapq.heappush(acik, int(c[i]))
    return alindi


def butce_ozeti(df: pd.DataFrame, islem: float, ay_listesi: List[str]) -> dict:
    """df: dönemin alınan işlemleri. Aylık ortalama dönemin TÜM takvim aylarına bölünür; işlemsiz ay pozitif sayılmaz
    (1. bölümdeki poz.ay'dan farkı: orada yalnız işlemli aylar sayılır). Düşüş: çıkış sırasıyla birikimli gerçekleşen
    kâr/zararın zirveden en büyük düşüşü (USDT)."""
    pnl = df['getiri_k1'].to_numpy(dtype=float) * islem
    ay_adi = _ay_dizisi(df['giris_ts'].to_numpy())
    ay = pd.Series(pnl).groupby(ay_adi).sum() if len(df) else pd.Series(dtype=float)
    ay = ay.reindex(ay_listesi, fill_value=0.0)
    adet = pd.Series(ay_adi).value_counts().reindex(ay_listesi, fill_value=0) if len(df) else \
        pd.Series(0, index=ay_listesi)
    birikim = np.r_[0.0, np.cumsum(pnl[np.argsort(df['cikis_ts'].to_numpy(), kind='stable')])]
    return {'alinan': int(len(df)), 'toplam_usdt': float(pnl.sum()),
            'aylik_ort_usdt': float(pnl.sum()) / max(len(ay_listesi), 1),
            'pozitif_ay': int((ay > 0).sum()), 'ay': int(len(ay)),
            'pozitif_ay_pct': float((ay > 0).mean()) * 100 if len(ay) else 0.0,
            'max_dusus_usdt': float((birikim - np.maximum.accumulate(birikim)).min()),
            'aylik': {k: float(v) for k, v in ay.items()}, 'aylik_adet': {k: int(v) for k, v in adet.items()}}


# ------------------------------------------------------------------------------------------
# Analiz: satırlar (kurallar + AI varyantları), dönemler, bütçe, karar
# ------------------------------------------------------------------------------------------
def donemler(bas_ms: int, ayrim_ms: int, son_ms: int) -> Dict[str, tuple]:
    d = {'kurulus': (bas_ms, ayrim_ms), 'sinama': (ayrim_ms, son_ms)}
    for yil in range(int(_ay_adi(bas_ms)[:4]), int(_ay_adi(son_ms - 1)[:4]) + 1):
        d[str(yil)] = (max(bas_ms, vs._ms(f"{yil}-01-01")), min(son_ms, vs._ms(f"{yil + 1}-01-01")))
    return d


REJIM_ALANLARI = ('n', 'kazanma_pct', 'beklenti_pct', 'ga95_pct', 'p_pozitif')


def rejim_tablosu(df: pd.DataFrame, don: Dict[str, tuple]) -> dict:
    """Bir kuralın (AI'sız) işlemleri, kuruluş ve sınamada karar mumundaki BTC rejimine göre: n, kazanma %, beklenti %,
    %95 GA ve P(>0) (gün bloklu bootstrap, istatistik() ile aynı). BILINMIYOR yalnız işlemi varsa. Bilgi amaçlıdır."""
    g = df['giris_ts'].to_numpy(dtype='int64')
    rj = df['rejim'].astype(str).to_numpy() if 'rejim' in df.columns else np.full(len(df), REJIM_YOK)
    sonuc = {}
    for d in ('kurulus', 'sinama'):
        a, b = don[d]
        m = (g >= a) & (g < b)
        sonuc[d] = {}
        for r in REJIMLER + (REJIM_YOK,):
            mm = m & (rj == r)
            if r == REJIM_YOK and not mm.any():
                continue
            o = istatistik(df[mm])
            sonuc[d][r] = {k: o[k] for k in REJIM_ALANLARI if k in o}
    return sonuc


def rejim_dagilimi(rejim: np.ndarray, ts: np.ndarray, don: Dict[str, tuple]) -> dict:
    """Her dönemin (giriş penceresi [a, b)) saatlerinin BTC rejimine göre payı (%); BILINMIYOR yalnız varsa."""
    sonuc = {}
    for d, (a, b) in don.items():
        r = rejim[(ts >= a) & (ts < b)]
        o = {'saat': int(len(r))}
        for k in REJIMLER + (REJIM_YOK,):
            adet = int((r == k).sum())
            if k != REJIM_YOK or adet:
                o[k] = adet / len(r) * 100 if len(r) else 0.0
        sonuc[d] = o
    return sonuc


def analiz(islemler: pd.DataFrame, kurallar, ai_acik: bool, bas_ms, ayrim_ms, son_ms, butceler, islem) -> dict:
    don = donemler(bas_ms, ayrim_ms, son_ms)
    islemler = islemler[[k for k in ANALIZ_SUTUNLARI if k in islemler.columns]]   # dilimler küçük kalsın (bellek)
    satirlar = {}
    for kural in kurallar:
        dk = islemler[islemler['kural'] == kural]
        satirlar[kural] = dk
        if ai_acik and kural in AI_KURALLARI:
            satirlar[kural + '+AI'] = dk[dk['ai_secildi'].astype(bool)]
    sonuc = {'sonuclar': {}, 'maliyet': {}, 'butce': {}, 'aylik': {}, 'aylik_adet': {}, 'ai': {}, 'karar': {},
             'rejim': {kural: rejim_tablosu(satirlar[kural], don) for kural in kurallar}}
    sinama_aylari = aylar(ayrim_ms, son_ms)
    for ad, df in satirlar.items():
        g = df['giris_ts'].to_numpy(dtype='int64')
        sonuc['sonuclar'][ad] = {d: istatistik(df[(g >= a) & (g < b)]) for d, (a, b) in don.items()}
        sn = sonuc['sonuclar'][ad]['sinama']
        sonuc['maliyet'][ad] = {'0.5': sn.get('beklenti_k05_pct'), '1': sn.get('beklenti_pct'),
                                '2': sn.get('beklenti_k2_pct')}
        sonuc['butce'][ad] = {}
        for bt in butceler:
            al = df[butce_oynat(df, bt, islem)]
            ga = al['giris_ts'].to_numpy(dtype='int64')
            sonuc['butce'][ad][f"{bt:g}"] = {d: butce_ozeti(al[(ga >= a) & (ga < b)], islem, aylar(a, b))
                                             for d, (a, b) in don.items()}
        ilk = sonuc['butce'][ad][f"{butceler[0]:g}"]['sinama'] if butceler else {}
        sonuc['aylik'][ad] = ilk.get('aylik', {})
        sonuc['aylik_adet'][ad] = ilk.get('aylik_adet', {})
    for ad in satirlar:
        kural, ai_mi = ad.replace('+AI', ''), ad.endswith('+AI')
        ai_p = None
        if ai_mi:
            dk = satirlar[kural]
            gk = dk['giris_ts'].to_numpy(dtype='int64')
            modelli = dk[(gk >= ayrim_ms) & np.isfinite(dk['ai_esik'].to_numpy(dtype=float))]
            et = (dk['getiri_k1'].to_numpy() > 0).astype(int)
            sk = dk['ai_skor'].to_numpy(dtype=float)
            kar = ai_karsilastir(modelli)
            kar['auc_kurulus'] = auc(sk[gk < ayrim_ms], et[gk < ayrim_ms])
            kar['auc_sinama'] = auc(sk[gk >= ayrim_ms], et[gk >= ayrim_ms])
            yil = _ay_dizisi(gk).astype('U4') if len(gk) else np.array([], dtype='U4')
            kar['auc_yillar'] = {y: auc(sk[yil == y], et[yil == y]) for y in sorted(set(yil.tolist()))}
            sonuc['ai'][kural] = kar
            ai_p = kar['p_pozitif']
        s = sonuc['sonuclar'][ad]
        sonuc['karar'][ad] = karar_ver(s['sinama'], s['kurulus'], ai_mi, ai_p)
    sonuc['donemler'] = {d: [vs._tarih(a), vs._tarih(b)] for d, (a, b) in don.items()}
    sonuc['sinama_aylari'] = sinama_aylari
    return sonuc


# ------------------------------------------------------------------------------------------
# Rapor
# ------------------------------------------------------------------------------------------
def _f(v, fmt, bos='-'):
    return fmt.format(v) if v is not None and not (isinstance(v, float) and not np.isfinite(v)) else bos


def _kes(x: float, basamak: int = 1) -> float:
    """Gösterim için aşağı yuvarlama: eşikle karşılaştırılan sayılar (kazanma %, pozitif ay %) yukarı yuvarlanıp
    karar kuralıyla çelişmesin (ör. %69.96 '70.0' görünüp 10'da 7 işareti olmaması)."""
    k = 10 ** basamak
    return math.floor(x * k + 1e-6) / k


def _pf(o: dict, anahtar: str) -> str:
    """PF ya da R:R hücresi: kayıp yoksa sınırsız ('∞'), hesaplanamıyorsa '-'."""
    return '∞' if o.get('pf_sonsuz') else _f(o[anahtar], '{:.2f}')


DONEM_ADI = {'kurulus': 'kuruluş', 'sinama': 'sınama'}


def rapor_metni(meta: dict, an: dict, ai_aylar: dict) -> str:
    don_yil = [d for d in an['donemler'] if d.isdigit()]
    y = [f"STRATEJİ LABORATUVARI | girişler {meta['baslangic']} → {meta['bitis']} (çıkışlar {meta['veri_sonu']}'e "
         f"kadarki veriyle) | kuruluş/sınama ayrımı {meta['ayrim']} | 1 saatlik mumlar",
         f"Evren: bugünün en hacimli {meta['evren']} USDT paritesi + BTC; kullanılan {meta['parite']} parite "
         f"(sabit coin çıkarılan: {meta['sabit_coin']}, verisi olmayan: {meta['verisiz']}) | "
         f"min hacim {meta['min_hacim'] / 1e6:.0f}M, büyük hacim {meta['buyuk_hacim'] / 1e6:.0f}M USDT | "
         f"API isteği: {meta['istek']} | süre {meta['sure_sn']:.0f} sn | sürümler: "
         + ', '.join(f"{k} {v}" for k, v in meta.get('surumler', {}).items()),
         f"Maliyet (k=1): komisyon %{FEE * 100:.2f}/yön, kayma giriş %{SLIP_GIRIS * 100:.2f} / stop "
         f"%{SLIP_SEVIYE * 100:.2f} / zaman-sinyal %{SLIP_ZAMAN * 100:.2f}; hedef (limit) kaymasız.",
         "Getiriler işlem başına net %; dönemlere GİRİŞ zamanına göre atanır. Bitişte açık kalan (hariç tutulan) "
         "işlem: " + ', '.join(f"{KISA.get(k, k)} {v}" for k, v in meta['acik_kalan'].items()),
         '', '0) ' + KARAR_KURALI, '',
         "1) KURALLAR — kuruluş ve sınama dönemi (beklenti = işlem başına ortalama net getiri; GA: gün bloklu "
         "bootstrap; kazan% ve poz.ay aşağı yuvarlanır; ∞: hiç kayıp yok; poz.ay: en az 1 işlemi olan aylar)",
         f"   {'satır':19s} {'dönem':8s} {'n':>6s} {'kazan%':>6s} {'ort.kaz':>7s} {'ort.kay':>7s} {'R:R':>5s} "
         f"{'PF':>5s} {'beklenti':>8s} {'%95 GA':>17s} {'P(>0)':>6s} {'med.s':>5s} {'k.seri':>6s} {'poz.ay':>11s}"]
    for ad, d in an['sonuclar'].items():
        for dn in ('kurulus', 'sinama'):
            o = d.get(dn, {})
            if not o.get('n'):
                y.append(f"   {ad:19s} {DONEM_ADI[dn]:8s} {0:6d}")
                continue
            ga = f"[{o['ga95_pct'][0]:+.2f}, {o['ga95_pct'][1]:+.2f}]"
            y.append(f"   {ad:19s} {DONEM_ADI[dn]:8s} {o['n']:6d} {_kes(o['kazanma_pct']):5.1f}% "
                     f"{_f(o['ort_kazanc_pct'], '{:+.2f}%'):>7s} {_f(o['ort_kayip_pct'], '{:+.2f}%'):>7s} "
                     f"{_pf(o, 'rr'):>5s} {_pf(o, 'pf'):>5s} "
                     f"{o['beklenti_pct']:+7.3f}% {ga:>17s} {o['p_pozitif']:6.4f} {o['medyan_sure_saat']:5.0f} "
                     f"{o['kayip_serisi']:6d} {_kes(o['pozitif_ay_pct'], 0):4.0f}% ({o['pozitif_ay']}/{o['ay']})")
    y += ['', "2) YIL YIL — her hücre: beklenti % / kazanma % / n (girişin yılına göre)",
          f"   {'satır':19s} " + ' '.join(f"{yil:>22s}" for yil in don_yil)]
    for ad, d in an['sonuclar'].items():
        hucre = []
        for yil in don_yil:
            o = d.get(yil, {})
            hucre.append(f"{o['beklenti_pct']:+.3f}% / {_kes(o['kazanma_pct'], 0):.0f}% / {o['n']}"
                         if o.get('n') else '-')
        y.append(f"   {ad:19s} " + ' '.join(f"{h:>22s}" for h in hucre))
    y += ['', "3) MALİYET DUYARLILIĞI — sınama beklentisi (%), maliyet katsayısı k (komisyon ve kaymalar x k)",
          f"   {'satır':19s} " + ' '.join(f"{'k=' + format(k, 'g'):>9s}" for k in K_LISTESI)]
    for ad, d in an['maliyet'].items():
        y.append(f"   {ad:19s} " + ' '.join(f"{_f(d[format(k, 'g')], '{:+.3f}%'):>9s}" for k in K_LISTESI))
    butceler = list(next(iter(an['butce'].values())).keys()) if an['butce'] else []
    y += ['', f"4) BÜTÇE ({'/'.join(butceler)} USDT, işlem başına {meta['islem']:g} USDT; slot doluysa sinyal atlanır; "
              "aylık ort. dönemin tüm takvim aylarına bölünür; poz.ay: kârlı ay / dönemin TÜM takvim ayları, işlemsiz "
              "ay pozitif sayılmaz — 1. bölümdeki poz.ay yalnız işlemli ayları sayar)",
          f"   {'satır':19s} {'bütçe':>5s} {'dönem':8s} {'alınan':>6s} {'toplam':>10s} {'aylık ort':>10s} "
          f"{'poz.ay':>7s} {'maks.düşüş':>10s}"]
    for ad, d in an['butce'].items():
        for bt, dd in d.items():
            for dn in ('kurulus', 'sinama'):
                o = dd[dn]
                y.append(f"   {ad:19s} {bt:>5s} {DONEM_ADI[dn]:8s} {o['alinan']:6d} {o['toplam_usdt']:+10.2f} "
                         f"{o['aylik_ort_usdt']:+10.2f} {str(o['pozitif_ay']) + '/' + str(o['ay']):>7s} "
                         f"{o['max_dusus_usdt']:10.2f}")
    sutun = list(an['aylik'])
    kurallar5 = list(dict.fromkeys(s.replace('+AI', '') for s in sutun))
    lejant = "   Sütunlar: " + ', '.join(f"{KISA.get(k, k)}={k}" for k in kurallar5) + "; +AI: AI varyantı"
    if 'YATAY_DONUS' in kurallar5:
        lejant += " (YATAY sütunu YATAY_DONUS kuralıdır, BTC rejimi değil; rejime göre ayrım 8. bölümde)"
    y += ['', f"5) AYLIK — sınama dönemi, {butceler[0] if butceler else '-'} USDT bütçe, ay ay gerçekleşen USDT "
              "(girişin ayına göre; '-': o ay hiç işlem alınmadı)", lejant,
          f"   {'ay':7s} " + ' '.join(f"{KISA.get(s.replace('+AI', ''), s) + ('+AI' if s.endswith('+AI') else ''):>10s}"
                                    for s in sutun)]
    adet = an.get('aylik_adet', {})
    for ay in an['sinama_aylari']:
        y.append(f"   {ay:7s} " + ' '.join(f"{an['aylik'][s].get(ay, 0.0):+10.2f}" if adet.get(s, {}).get(ay, 0)
                                            else f"{'-':>10s}" for s in sutun))
    y += ['', "6) AI — ay ay ileri yürüyen model (M ayı yalnız M'den önce kapanmış işlemlerle); karşılaştırma sınama "
              "döneminin modelli aylarında, aynı işlemler üzerinde"]
    if not an['ai']:
        y.append("   (AI kapalı ya da AI'lı kural seçilmedi)")
    for kural, o in an['ai'].items():
        ka = ai_aylar.get(kural, [])
        modelli = sum(1 for x in ka if x['model'])
        y.append(f"   {kural:16s} modelli ay {modelli}/{len(ka)} | AUC kuruluş {_f(o.get('auc_kurulus'), '{:.3f}')} "
                 f"sınama {_f(o.get('auc_sinama'), '{:.3f}')} | sınama: kural n={o['n_kural']} "
                 f"{_f(o.get('beklenti_kural_pct'), '{:+.3f}%')}, AI n={o['n_ai']} "
                 f"{_f(o.get('beklenti_ai_pct'), '{:+.3f}%')} | fark {_f(o.get('fark_pct'), '{:+.3f}%')} "
                 + (f"[{o['ga95_pct'][0]:+.3f}, {o['ga95_pct'][1]:+.3f}]" if o.get('ga95_pct') else '[-]')
                 + f" P(>0)={o['p_pozitif']:.4f}")
    y += ['', "7) KARAR (önceden kayıtlı kural; ayrıntı 0. bölümde)"]
    gecen = 0
    for ad, k in an['karar'].items():
        gecen += k['sonuc'] == 'GEÇTİ'
        hedef = "  [10'da 7 hedefi: sınama kazanma >= %70]" if k['hedef_70'] else ''
        y.append(f"   {ad:19s} {k['sonuc']:6s}" + (f" — sağlanmayan: {'; '.join(k['kalan'])}" if k['kalan'] else '')
                 + hedef)
    y.append(f"   SONUÇ: {len(an['karar'])} satırdan {gecen} tanesi GEÇTİ.")
    esik = REJIM_ADX_ESIK
    y += ['', "8) REJİM — girişteki BTC rejimine göre (bilgi amaçlı; karar kuralını değiştirmez). Rejim karar mumunda: "
              f"BTC 1s ADX14 >= {esik} ve kapanış > EMA200 TREND_YUKARI; ADX14 >= {esik} ve kapanış <= EMA200 "
              f"TREND_ASAGI; ADX14 < {esik} YATAY; GA ve P(>0) gün bloklu bootstrap"]
    dag = an.get('rejim_dagilim', {})
    if dag:
        y.append("   Saatlerin rejim dağılımı: " + ' | '.join(
            f"{DONEM_ADI.get(d, d)} " + ', '.join(f"{k} %{v:.1f}" for k, v in o.items() if k != 'saat')
            + f" ({o['saat']} saat)" for d, o in dag.items()))
    y.append(f"   {'satır':19s} {'dönem':8s} {'rejim':12s} {'n':>6s} {'kazan%':>6s} {'beklenti':>8s} {'%95 GA':>17s} "
             f"{'P(>0)':>6s}")
    for kural, d in an.get('rejim', {}).items():
        for dn in ('kurulus', 'sinama'):
            for r, o in d.get(dn, {}).items():
                if not o.get('n'):
                    y.append(f"   {kural:19s} {DONEM_ADI[dn]:8s} {r:12s} {0:6d}")
                    continue
                ga = f"[{o['ga95_pct'][0]:+.2f}, {o['ga95_pct'][1]:+.2f}]"
                y.append(f"   {kural:19s} {DONEM_ADI[dn]:8s} {r:12s} {o['n']:6d} {_kes(o['kazanma_pct']):5.1f}% "
                         f"{o['beklenti_pct']:+7.3f}% {ga:>17s} {o['p_pozitif']:6.4f}")
    y += ['', 'Kurallar: ' + ' | '.join(f"{k}: {v}" for k, v in KURAL_ACIKLAMA.items()),
          "AI varyantı: <KURAL>+AI = kuralın, o ayın modelinin eşiğini geçen işlemleri (kombolarda ve BTC_TREND'de AI "
          "yok).",
          'Bilinen sınırlar:',
          '  - Evren bugünün en hacimli paritelerinden oluşur; dönem içinde delist olanlar yok (hayatta kalma '
          'yanlılığı, sonuçlar iyimser).',
          '  - 1 saatlik mum içi yol yaklaşıktır: yeşil mumda önce dip, kırmızıda önce tepe varsayılır.',
          '  - AI süzgeci işlem listesine sonradan uygulanır; süzülen işlemin engellediği sinyaller geri gelmez.',
          '  - BOT_VEKILI canlı botun 1 saatlik vekilidir; bot 15 dakikalık mumlarla ve kendi çıkışlarıyla çalışır.',
          f"  - Uzun pencereler (168/720 saat) eksik saatlere karşı en az %{DOLULUK * 100:.0f} dolulukla hesaplanır; "
          f"parite ilk sinyalden önce en az {MIN_GECMIS} dolu saat ister. EMA/ATR/RSI/ADX eksik saati atlar; RSI ve "
          "ADX son n+1 saatin hepsi doluysa hesaplanır (rejim yoksa BILINMIYOR: rejime bağlı sinyal yok).",
          '  - İşlem içindeki eksik saatler: atlanır ama tutma süresine sayılır, sonraki mumun açılışı boşluk sayılır '
          '(stop altındaysa oradan dolar); azami sürenin son mumu eksikse sonraki ilk açılıştan çıkılır; SMA5/SMA20 '
          'eksikken RSI2/ORTA_BANT sinyal çıkışı yoktur.',
          '  - Veri sonu: yalnız azami tutma süresi (168/24/48 saat; kombolarda işlemin bileşeninin çıkışına göre) '
          'veride kalan girişler alınır, böylece veri sonundaki işlemler sonuçlarına göre elenmez; --bitis '
          'verilmezse analiz son tamamlanmış takvim ayında biter. BTC_TREND\'de veri sonunda açık kalan işlem '
          'hariçtir.',
          '  - Rejim BTC\'nin 1 saatlik ADX14\'üyle belirlenir (canlı bot 15 dakikalık ADX kullanır); eşik (20) ve '
          'kombo eşlemesi veriden değil, baştan teoriye göre seçildi (ANALIZ §12.1).',
          f"  - Sabit coin kararı paritenin ilk {MIN_GECMIS} dolu saatiyle verilir (ilk olası sinyalden önce; "
          "geleceğe bakmaz)."]
    return '\n'.join(y)


def _tarih_dizisi(ms) -> np.ndarray:
    """vs._tarih'in vektör hâli: UTC 'YYYY-MM-DD HH:MM'."""
    s = np.datetime_as_string(np.asarray(ms, dtype='int64').astype('datetime64[ms]'), unit='m')
    return np.char.replace(s, 'T', ' ') if len(s) else s


def islemleri_yaz(islemler: pd.DataFrame, yol: str):
    """İşlem tablosu -> CSV (CSV_SUTUNLAR; giriş/çıkış zamanı 'YYYY-MM-DD HH:MM' UTC). CSV_PARCA satırlık parçalarla
    yazılır: tablonun tamamının biçimlenmiş kopyası bellekte tutulmaz (çıktı tek seferde yazılanla aynı)."""
    with open(yol, 'w', encoding='utf-8', newline='') as f:
        for i in range(0, max(len(islemler), 1), CSV_PARCA):
            parca = islemler.iloc[i:i + CSV_PARCA][CSV_SUTUNLAR].copy()
            for kol in ('giris_ts', 'cikis_ts'):
                parca[kol] = _tarih_dizisi(parca[kol].to_numpy())
            parca.to_csv(f, header=i == 0, index=False)


# ------------------------------------------------------------------------------------------
def donem_sinirlari(bitis: Optional[str], simdi_ms: int):
    """(analiz sonu, veri sonu), ikisi de saat başı (ms). Girişler analiz sonundan önce olmalı; veri, en uzun tutma
    (EN_UZUN_TUTMA saat) de içinde kalsın diye analiz sonu + o süreye kadar (şimdiyi geçmeden) indirilir.
    --bitis verilmezse analiz sonu, (şimdi - EN_UZUN_TUTMA) anının ayının başıdır: son TAMAMLANMIŞ takvim ayı (yarım
    son ay ve veri sonunda sonucuna göre seçilmiş işlemler aylık tabloya ve 4. kritere girmesin)."""
    simdi = (int(simdi_ms) // SAAT_MS) * SAAT_MS           # henüz kapanmamış mumun açılışı
    if bitis:
        son = min((vs._ms(bitis) // SAAT_MS) * SAAT_MS, simdi)
    else:
        son = _ay_bas(simdi - EN_UZUN_TUTMA * SAAT_MS)
    return son, min(simdi, son + EN_UZUN_TUTMA * SAAT_MS)


def calistir(ex, a, simdi_ms=None, log=print):
    t_bas = time.time()
    simdi_ms = int(simdi_ms if simdi_ms is not None else time.time() * 1000)
    cikti_dizini = os.path.dirname(os.path.abspath(a.cikti))
    os.makedirs(cikti_dizini, exist_ok=True)            # hatalı --cikti uzun hesaptan sonra değil, başta görünsün
    if not os.access(cikti_dizini, os.W_OK):
        raise PermissionError(f"çıktı dizinine yazılamıyor: {cikti_dizini}")
    bas_ms, ayrim_ms = vs._ms(a.baslangic), vs._ms(a.ayrim)
    son_ms, g1 = donem_sinirlari(a.bitis, simdi_ms)      # girişler [bas, son_ms); veri [g0, g1)
    g0 = ((bas_ms - ISINMA_GUN * GUN_MS) // SAAT_MS) * SAAT_MS
    n = (g1 - g0) // SAAT_MS
    if n <= MIN_GECMIS or son_ms <= bas_ms:
        raise ValueError("dönem çok kısa")
    ts = g0 + SAAT_MS * np.arange(n, dtype=np.int64)
    ilk_e = int(-(-(bas_ms - g0) // SAAT_MS))           # ilk giriş mumu: açılışı >= baslangic
    son_i = int((son_ms - g0) // SAAT_MS)               # ilk giriş YAPILAMAYAN mum: açılışı >= analiz sonu
    kurallar = [k for k in KURALLAR if k in a.kurallar]
    cift_kurallari = [k for k in kurallar if k != 'BTC_TREND']
    depo = vs.MumDeposu(ex, a.onbellek, simdi_ms=simdi_ms)
    # yalnız BTC_TREND seçildiyse evren gerekmez (BTC zaten indirilir)
    semboller = ['BTC/USDT'] + (bf.evren_sec(ex, a.evren) if cift_kurallari else [])
    log(f"1 saatlik veri: {len(semboller)} parite, {vs._tarih(g0)[:10]} → {vs._tarih(g1)} (ısınma {ISINMA_GUN} gün; "
        f"girişler {vs._tarih(son_ms)} öncesi)...")

    # BTC bağlamı bir kez (BTC_TREND'in MA100'ü için daha uzun ısınma; ADX ve rejim için H/L de)
    bg0 = g0 - (BTC_ISINMA_GUN - ISINMA_GUN) * GUN_MS
    nb = (g1 - bg0) // SAAT_MS
    _, Hb, Lb, Cb, _ = izgaraya_yerlestir(depo.getir('BTC/USDT', TF, bg0, g1), bg0, nb)
    ofs = (g0 - bg0) // SAAT_MS
    btc = {k: v[ofs:] for k, v in btc_baglami(Cb, Hb, Lb).items()}
    gun_bas, gun_kap = btc_gunluk(bg0 + SAAT_MS * np.arange(nb, dtype=np.int64), Cb)
    del Cb, Hb, Lb
    if depo.dizin:
        depo.bosalt(sym='BTC/USDT')

    if cift_kurallari:
        kesit = gecis1(depo, semboller, g0, n, log)
        log(f"  sabit coin çıkarıldı: {len(kesit.sabit)} {kesit.sabit} | verisi olmayan: {len(kesit.verisiz)}")
        # REJIM_KOMBO_BOT'un trend bileşeni BOT_VEKILI: kurallarda olmasa da radar gerekir
        radar = radar_ilk10(kesit.R24, kesit.V24) if {'BOT_VEKILI', 'REJIM_KOMBO_BOT'} & set(kurallar) else None
        kesit.R24 = kesit.V24 = None        # radar hesaplandı; paneller bırakılır
    else:
        kesit, radar = Kesit(['BTC/USDT'], None, None, None, [], []), None

    tablolar, acik = [], Counter()
    m = len(kesit.semboller)
    for j, s in enumerate(kesit.semboller if cift_kurallari else []):
        O, H, L, C, V = izgaraya_yerlestir(depo.getir(s, TF, g0, g1), g0, n)
        tb, ac = parite_islemleri(s, O, H, L, C, V, ts, btc, radar[:, j] if radar is not None else None,
                                  cift_kurallari, a.min_hacim, a.buyuk_hacim, ilk_e, son_i)
        tablolar += tb
        acik.update(ac)
        if depo.dizin:
            depo.bosalt(sym=s)
        if (j + 1) % 10 == 0 or j + 1 == m:
            log(f"  geçiş 2 (sinyal + işlem): {j + 1}/{m} parite ({depo.istek} API isteği, "
                f"{sum(len(t) for t in tablolar)} işlem)")
    del radar
    if 'BTC_TREND' in kurallar:
        bt, ac = btc_trend_islemleri(gun_bas, gun_kap, bas_ms, son_ms)
        if len(bt):                 # rejim: karar saati = karar gününün son saati (giriş - 1 saat)
            bt['rejim'] = saat_rejimi(btc['rejim'], g0, bt['giris_ts'].to_numpy(dtype='int64') - SAAT_MS)
        tablolar.append(bt)
        acik['BTC_TREND'] += ac
    tablolar = [t for t in tablolar if len(t)]
    islemler = (pd.concat(tablolar, ignore_index=True) if tablolar
                else pd.DataFrame(columns=CSV_SUTUNLAR[:-3]))
    islemler = islemler.sort_values(['kural', 'giris_ts', 'sembol'], kind='stable').reset_index(drop=True)
    for kol in ('giris_ts', 'cikis_ts'):
        islemler[kol] = islemler[kol].astype('int64')
    islemler['ai_skor'] = np.nan
    islemler['ai_esik'] = np.nan
    islemler['ai_secildi'] = False

    ai_aylar = {}
    if a.ai:
        for kural in [k for k in kurallar if k in AI_KURALLARI]:
            m_k = (islemler['kural'] == kural).to_numpy()
            skor, esik, ai_aylar[kural] = ai_ileri_yuruyus(islemler.loc[m_k, AI_SUTUNLARI], ayrim_ms, son_ms)
            islemler.loc[m_k, 'ai_skor'] = skor
            islemler.loc[m_k, 'ai_esik'] = esik
            with np.errstate(invalid='ignore'):
                islemler.loc[m_k, 'ai_secildi'] = skor >= esik
            log(f"  AI {kural}: {sum(x['model'] for x in ai_aylar[kural])}/{len(ai_aylar[kural])} ayda model")

    an = analiz(islemler, kurallar, a.ai, bas_ms, ayrim_ms, son_ms, a.butce, a.islem)
    an['rejim_dagilim'] = rejim_dagilimi(btc['rejim'], ts, {d: v for d, v in donemler(bas_ms, ayrim_ms, son_ms).items()
                                                            if d in DONEM_ADI})
    meta = {'baslangic': vs._tarih(bas_ms)[:10], 'bitis': vs._tarih(son_ms), 'veri_sonu': vs._tarih(g1),
            'ayrim': a.ayrim, 'evren': a.evren,
            'parite': m, 'sabit_coin': len(kesit.sabit), 'sabit_coinler': kesit.sabit,
            'verisiz': len(kesit.verisiz), 'min_hacim': a.min_hacim, 'buyuk_hacim': a.buyuk_hacim,
            'butce': a.butce, 'islem': a.islem, 'kurallar': kurallar, 'ai': bool(a.ai), 'istek': depo.istek,
            'acik_kalan': {k: int(acik.get(k, 0)) for k in kurallar}, 'islem_sayisi': int(len(islemler)),
            'maliyet': {'FEE': FEE, 'SLIP_GIRIS': SLIP_GIRIS, 'SLIP_SEVIYE': SLIP_SEVIYE, 'SLIP_ZAMAN': SLIP_ZAMAN},
            'rejim_adx_esik': REJIM_ADX_ESIK, 'ozellikler': list(OZELLIKLER),
            'karar_kurali': KARAR_KURALI, 'olusturma': vs._tarih(simdi_ms), 'surumler': kutuphane_surumleri(),
            'sure_sn': time.time() - t_bas}
    metin = rapor_metni(meta, an, ai_aylar)
    with open(a.cikti + '_rapor.txt', 'w', encoding='utf-8') as f:
        f.write(metin + '\n')
    with open(a.cikti + '_rapor.json', 'w', encoding='utf-8') as f:
        json.dump({'meta': meta, **an, 'ai_aylar': ai_aylar}, f, ensure_ascii=False, indent=1, default=str)
    islemleri_yaz(islemler, a.cikti + '_islemler.csv')
    log('\n' + metin)
    log(f"\nÇıktılar: {a.cikti}_rapor.txt, {a.cikti}_rapor.json, {a.cikti}_islemler.csv")
    return {'meta': meta, 'analiz': an, 'islemler': islemler, 'kesit': kesit, 'ai_aylar': ai_aylar, 'btc': btc}


def kutuphane_surumleri() -> dict:
    """Sonuçları etkileyebilecek kütüphanelerin sürümleri (rapor ve JSON'a yazılır; farklı makinede fark izlenebilsin)."""
    import sklearn
    s = {'python': sys.version.split()[0], 'numpy': np.__version__, 'pandas': pd.__version__,
         'sklearn': sklearn.__version__}
    try:
        import xgboost
        s['xgboost'] = xgboost.__version__
    except ImportError:
        s['xgboost'] = 'yok (sklearn HistGradientBoosting)'
    return s


def _pozitif(tip, ad):
    def kontrol(x):
        v = tip(x)
        if not v > 0:
            raise argparse.ArgumentTypeError(f"{ad} sıfırdan büyük olmalı: {x}")
        return v
    return kontrol


def _negatif_olmayan_tam(x):
    v = int(x)
    if v < 0:
        raise argparse.ArgumentTypeError(f"--evren negatif olamaz: {x}")
    return v


def arguman_ayristirici():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--baslangic', default='2023-01-01')
    ap.add_argument('--bitis', default=None,
                    help='girişlerin bitişi (hariç); varsayılan: son tamamlanmış takvim ayının sonu')
    ap.add_argument('--ayrim', default='2025-01-01', help='kuruluş / sınama ayrımı (girişe göre)')
    ap.add_argument('--evren', type=_negatif_olmayan_tam, default=80, help='bugünün en hacimli N USDT paritesi (+ BTC)')
    ap.add_argument('--min-hacim', type=float, default=5e6,
                    help='ERKEN/SIKISMA/YATAY_DONUS: 24 saatlik hacim alt sınırı (USDT)')
    ap.add_argument('--buyuk-hacim', type=float, default=2e7,
                    help='TREND_DIP_RSI2: 30 günlük ortalama günlük hacim alt sınırı (USDT)')
    ap.add_argument('--butce', type=_pozitif(float, '--butce'), nargs='+', default=[450.0, 90.0], help='bütçeler (USDT)')
    ap.add_argument('--islem', type=_pozitif(float, '--islem'), default=20.0, help='işlem başına USDT')
    ap.add_argument('--kurallar', nargs='+', choices=KURALLAR, default=list(KURALLAR), help='çalıştırılacak kurallar')
    ap.add_argument('--ai', dest='ai', action='store_true', default=True, help='AI varyantları (varsayılan açık)')
    ap.add_argument('--ai-yok', dest='ai', action='store_false', help='AI varyantlarını hesaplama')
    ap.add_argument('--onbellek', default=os.path.join(KOK, 'sim_onbellek'))
    ap.add_argument('--cikti', default=os.path.join(KOK, 'strateji_lab'))
    return ap


def main(argv=None):
    a = arguman_ayristirici().parse_args(argv)
    import ccxt
    ex = ccxt.binance({'enableRateLimit': True, 'options': {'defaultType': 'spot'}})
    ex.rateLimit = max(ex.rateLimit or 0, 100)   # botla aynı IP limiti paylaşılıyor
    try:
        calistir(ex, a)
    except (ccxt.NetworkError, ccxt.ExchangeNotAvailable, vs.VeriYok) as e:
        print(f"\n❌ Binance fiyat verisine erişilemedi: {type(e).__name__}: {str(e)[:300]}\n"
              f"   Bot sunucusunda çalıştırın. İndirilen veri {a.onbellek} içinde; tekrar çalıştırınca kaldığı yerden "
              f"sürer.")
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
