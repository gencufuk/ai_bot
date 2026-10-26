# -*- coding: utf-8 -*-
"""MAKİNE HAFIZASI: SQLite veritabanı (şema, göçler, yazma/okuma, saklama süresi), tek kopya kilidi, Binance ham
1 saatlik mum toplayıcı ve evren seçimi. makine.py (tarayıcı + kâğıt işlem + etiket + rapor) bunun üstüne kurulur.

GÜVENLİK: bu modül yalnız herkese açık uç noktaları çağırır (publicGetKlines, load_markets/market). Emir, bakiye ya
da API anahtarı kullanan hiçbir çağrı yoktur; borsa nesnesi anahtarsız kurulmalıdır (makine.py).

Veritabanı (tek dosya, tek bağlantı): WAL günlüğü, synchronous=NORMAL, busy_timeout, yeni dosyada
auto_vacuum=INCREMENTAL (silmelerden sonra incremental_vacuum ile disk geri verilir). Şema sürümü 'meta'
tablosunda ('sema_surumu'); GOCLER listesi ileri yönlü uygulanır (her göç bir kez, tek işlemde; daha yeni sürümlü
dosya açılmaz). Tablolar (ayrıntı GOCLER'de):
  meta         anahtar -> değer (sema_surumu, ilk_baslangic, son_temizlik_gunu, uyari_<tür> ...)
  parite       sembol başına: market_id, ilk görülme, kapsam_ts, sabit coin kararı, durum (AKTIF/YOK)
  mum          ham 1s mumlar (sembol, ts): o h l c v(baz) qv(USDT) islem_adedi alici_qv; yalnız KAPANMIŞ mum
  aday         her tur her evren paritesi için karar mumu t'deki özellikler ve o mumda tetiklenen sinyaller
  etiket       aday satırının ileri etiketleri (makine_ozellikleri.ileri_etiketler)
  defter       ilginç paritelerin emir defteri özeti (makine_ozellikleri.defter_ozellikleri)
  kagit_islem  kâğıt işlemler (BEKLIYOR / ACIK / KAPALI / ATLANDI)
  tarama       her karar saati için tur kaydı (TAMAM / KACIRILDI / HATA)
  olay         hata ve uyarı günlüğü (rapor ve uyarı sınırlaması için)
Zamanlar UTC milisaniye; ts her yerde 1 saatlik mumun AÇILIŞ zamanıdır (karar mumu t = yeni kapanan mum).

Kapsam (parite.kapsam_ts): bu açılış zamanından ÖNCEKİ bütün kapanmış mumlar borsadan çekilmiştir; o aralıkta mum
tablosunda olmayan saat borsada da yoktur (kalıcı boşluk: bakım, işlem durması). Toplayıcı yalnız ileri doğru çeker
(kapsam_ts'den şimdiye), bu yüzden kapsam ancak borsa yanıtında görülen son mumun saatine kadar ilerler: borsa son
kapanmış mumu henüz vermediyse (gecikme) o saat boşluk sayılmaz, sonraki turda yeniden istenir. Etiket ve kâğıt
işlem "mum eksik mi, yoksa henüz gelmedi mi" sorusunu bununla yanıtlar.

Mum toplayıcı (MumToplayici; Binance ham klines): ex.publicGetKlines({'symbol': market_id, 'interval': '1h',
'startTime': ms, 'limit': n}) 12 alan döndürür: açılış zamanı, o, h, l, c, v (baz), kapanış zamanı, qv (USDT),
işlem adedi, alıcı baz hacmi, alıcı USDT hacmi, yok sayılan. Yalnız kapanmış mumlar (açılış + 1 saat <= şimdi)
saklanır; aynı mum yeniden gelirse üzerine yazılır (idempotent). Bir parite ilk görüldüğünde PENCERE (1500) saatlik
geçmiş sayfalanarak (1000'lik) çekilir; sonra yalnız kapsamdan şimdiye (makine kapalıyken oluşan boşluk da en çok
PENCERE saat geriye doldurulur). Ağ hatası / borsa erişilemez / istek sınırı / zaman aşımı: 2, 4, 8 sn bekleyip
yeniden dener (DENEME = 4 istek); yine olmazsa parite bu tur atlanır ve günlüğe yazılır (tur ölmez). Üst üste
ARDISIK_HATA_SINIRI paritede ağ hatası olursa (borsa ya da ağ çökmüş) kalan pariteler bu tur denenmez: aksi halde
150 parite x 14 sn bekleme turu bir saati aşardı. BadSymbol (delist / bilinmeyen sembol): piyasalar turda bir kez
yeniden yüklenip denenir, olmazsa parite 'YOK' işaretlenir ve YOK_BEKLEME (24 saat) boyunca denenmez.

Evren (evren_sec, saf işlev; fetch_tickers sonucu): USDT spot, ASCII sembol, backfill_sinyaller.YASAKLI_COINLER
(strateji_lab/backfill ile aynı alt dizgi kuralı), kaldıraçlı token değil (UP/DOWN/BULL/BEAR eki VE eksiz kısmı
başka bir USDT paritesinin bazı: BTCUP çıkar, JUP/SYRUP kalır), 24s quoteVolume >= min_hacim; en hacimli n + her
zaman BTC/USDT. Sıra strateji_lab ile aynı: BTC önce, sonra (quoteVolume, sembol) azalan (bf.evren_sec); radarın
eşitlik bozması sütun sırasına bağlıdır. Sabit coinler strateji_lab gibi ilk n SEÇİLDİKTEN SONRA çıkarılır.
Açık kâğıt işlemi olan pariteler evrenden çıksa da mumları güncellenir (guncellenecek).
Sabit coin (Hafiza.sabit_karari): strateji_lab kuralı, paritenin veritabanındaki ilk 720 dolu saatinin saatlik
getiri std'si < SABIT_COIN_STD (BTC asla). 720 dolu saat birikince karar kalıcı yazılır; öncesinde eldeki saatlerle
geçici karar verilir (strateji_lab'ın kısa geçmişli pariteye yaptığı gibi). Fark: strateji_lab ilk 720 saati kendi
dönem başından (başlangıç - 45 gün), makine ilk gördüğü andan 1500 saat geriden sayar.

Kayan pencere (Hafiza.izgara, PENCERE = 1500 saat, karar mumu son satır): göstergeler strateji_lab'ın kendi
işlevleriyle (gostergeler, btc_baglami, sinyaller) bu pencerede hesaplanır. Pencereli (rolling) göstergeler son ~740
saatle tamamen belirlenir (bbw_pct: 720 + 20; ort_gunluk_hacim30: 720; hacim medyanı: 168 + 6): tam geçmişle aynı
(kayan toplamın son basamak yuvarlaması dışında). Özyinelemeli ortalamalar (EMA/ATR/RSI/ADX, adjust=False) pencere
başından etkilenir: EMA200'ün başlangıç etkisi (1 - 2/201)^k göreli, k = 1300'de < 3e-6, karar mumunda (k = 1499)
< 4e-7; EMA50/ATR/RSI/ADX'te ihmal edilebilir. Sinyal ancak bir değer eşiğine bu kadar yakınsa strateji_lab'dan
farklı çıkabilir (tests/test_makine.py tam geçmişle karşılaştırır). 'gecmis' (>= 720 dolu saat) pencere içinde
sayılır: pencerede 720'den az dolu saati olan eski parite (uzun işlem durması) sinyal vermez.
"""
import json
import logging
import math
import os
import sqlite3
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import ccxt
import numpy as np

from . import makine_ozellikleri as mo

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _yol in (KOK, os.path.join(KOK, 'tools')):
    if _yol not in sys.path:
        sys.path.insert(0, _yol)

import strateji_lab as sl  # noqa: E402  (tek kod ilkesi: ızgara ve sabit coin kuralı oradan)

LOG = logging.getLogger('makine.hafiza')

SAAT_MS = sl.SAAT_MS
GUN_MS = sl.GUN_MS
PENCERE = 1500                  # bellekteki kayan pencere ve ilk geçmiş (saat); >= MIN_GECMIS 720 + ısınma
SAYFA = 1000                    # klines sayfa boyu (Binance üst sınırı)
DENEME = 4                      # ağ hatasında toplam istek sayısı (2, 4, 8 sn bekleme)
ARDISIK_HATA_SINIRI = 5         # üst üste bu kadar paritede ağ hatası: turun kalan pariteleri denenmez
YOK_BEKLEME_MS = GUN_MS         # BadSymbol alan parite bu süre denenmez
MUM_EK_GUN = 70                 # mum saklama = aday_gun + bu (>= PENCERE 62.5 gün)
OLAY_GUN = 90                   # olay saklama
ETIKET_GERI_GUN = 14            # bu kadar günden eski etiketsiz aday satırına bakılmaz (paritesi evrenden çıkmış)
AG_HATALARI = (ccxt.NetworkError, ccxt.ExchangeNotAvailable, ccxt.RateLimitExceeded, ccxt.RequestTimeout)
KALDIRACLI_EKLER = ('UP', 'DOWN', 'BULL', 'BEAR')
KAGIT_DURUMLARI = ('BEKLIYOR', 'ACIK', 'KAPALI', 'ATLANDI')

LAB_OZELLIKLERI = tuple(k for k in sl.OZELLIKLER if not k.startswith('saat_'))   # aday'a yazılan 15 lab özelliği

# ------------------------------------------------------------------------------------------
# Şema ve göçler (yalnız sona ekle; yazılmış bir göç değiştirilmez)
# ------------------------------------------------------------------------------------------
GOCLER: List[Tuple[int, str, List[str]]] = [
    (1, 'ilk şema', [
        """CREATE TABLE IF NOT EXISTS meta (anahtar TEXT PRIMARY KEY, deger TEXT) WITHOUT ROWID""",
        """CREATE TABLE parite (
             sembol TEXT PRIMARY KEY,
             market_id TEXT,
             ilk_gorulme_ms INTEGER,           -- makinenin pariteyi ilk gördüğü an
             kapsam_ts INTEGER,                -- bu açılıştan önceki tüm kapanmış mumlar çekildi
             sabit INTEGER,                    -- 1 sabit coin, 0 değil, NULL kesin karar yok
             durum TEXT NOT NULL DEFAULT 'AKTIF',   -- AKTIF / YOK (BadSymbol)
             durum_ms INTEGER,
             son_hata TEXT) WITHOUT ROWID""",
        """CREATE TABLE mum (
             sembol TEXT NOT NULL, ts INTEGER NOT NULL,
             o REAL, h REAL, l REAL, c REAL, v REAL, qv REAL, islem_adedi INTEGER, alici_qv REAL,
             PRIMARY KEY (sembol, ts)) WITHOUT ROWID""",
        """CREATE TABLE aday (
             ts INTEGER NOT NULL, sembol TEXT NOT NULL,
             ret24 REAL, ret6 REAL, rsi14 REAL, rsi2 REAL, vol_ratio6 REAL, vol_ratio14 REAL, atr_pct REAL,
             ema50_uzaklik REAL, ema200_uzaklik REAL, bbw_pct REAL, adx14 REAL, log_vol24 REAL,
             btc_ret24 REAL, btc_ema200_uzaklik REAL, btc_adx14 REAL,
             btc_rejim TEXT, radar_sira INTEGER, evren_sira INTEGER,
             ret1 REAL, govde REAL, ust_fitil REAL, alt_fitil REAL,
             doji INTEGER, cekic INTEGER, kayan_yildiz INTEGER, yutan_boga INTEGER, yutan_ayi INTEGER,
             uc_beyaz_asker INTEGER,
             islem_orani REAL, buyuk_lot_orani REAL, alici_payi REAL, alici_payi6 REAL, hacim_yogunlugu24 REAL,
             sinyaller TEXT NOT NULL DEFAULT '',     -- o mumda tetiklenen stratejiler, virgülle ('' = yok)
             PRIMARY KEY (ts, sembol)) WITHOUT ROWID""",
        """CREATE TABLE etiket (
             ts INTEGER NOT NULL, sembol TEXT NOT NULL,
             ileri_1s REAL, ileri_4s REAL, ileri_24s REAL, max_yukari_24s REAL, max_asagi_24s REAL,
             ucus10 INTEGER, ucus20 INTEGER,
             eksik_saat INTEGER, olusturma_ms INTEGER,
             PRIMARY KEY (ts, sembol)) WITHOUT ROWID""",
        """CREATE TABLE defter (
             ts INTEGER NOT NULL, sembol TEXT NOT NULL, alinma_ms INTEGER,
             spread_bps REAL, al_derinlik_1 REAL, sat_derinlik_1 REAL, al_derinlik_2 REAL, sat_derinlik_2 REAL,
             dengesizlik_1 REAL, kapsam_bps REAL,
             PRIMARY KEY (ts, sembol)) WITHOUT ROWID""",
        """CREATE TABLE kagit_islem (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             strateji TEXT NOT NULL, bilesen TEXT NOT NULL, sembol TEXT NOT NULL,
             sinyal_ts INTEGER NOT NULL, giris_ts INTEGER NOT NULL, giris_fiyat REAL,
             durum TEXT NOT NULL CHECK (durum IN ('BEKLIYOR', 'ACIK', 'KAPALI', 'ATLANDI')),
             cikis_ts INTEGER, cikis_fiyat REAL, cikis_tipi TEXT, getiri REAL, sure_saat REAL,
             rejim TEXT, olusturma_ms INTEGER NOT NULL, guncelleme_ms INTEGER,
             UNIQUE (strateji, sembol, sinyal_ts))""",
        """CREATE INDEX kagit_durum ON kagit_islem (durum, strateji, sembol)""",
        """CREATE TABLE tarama (
             ts INTEGER PRIMARY KEY,           -- karar mumunun açılışı
             durum TEXT NOT NULL,              -- TAMAM / KACIRILDI / HATA
             baslangic_ms INTEGER, bitis_ms INTEGER,
             evren INTEGER, guncellenen INTEGER, mum_hatasi INTEGER, sinyal INTEGER,
             acilan INTEGER, kapanan INTEGER, etiketlenen INTEGER, defter INTEGER,
             ayrinti TEXT)                     -- JSON (ör. strateji başına sinyal sayısı, hata özeti)""",
        """CREATE TABLE olay (
             id INTEGER PRIMARY KEY, ms INTEGER NOT NULL, tur TEXT NOT NULL, sembol TEXT, mesaj TEXT)""",
        """CREATE INDEX olay_ms ON olay (ms)""",
    ]),
]
SEMA_SURUMU = GOCLER[-1][0]
TABLOLAR = ('meta', 'parite', 'mum', 'aday', 'etiket', 'defter', 'kagit_islem', 'tarama', 'olay')
ANAHTARLAR = {'parite': ('sembol',), 'mum': ('sembol', 'ts'), 'aday': ('ts', 'sembol'), 'etiket': ('ts', 'sembol'),
              'defter': ('ts', 'sembol'), 'tarama': ('ts',)}


class SemaHatasi(RuntimeError):
    """Veritabanı bu koddan daha yeni bir şema sürümüyle yazılmış."""


def _deger(x):
    """Python/numpy değeri -> SQLite değeri (NaN/inf -> NULL, numpy sayıları -> Python sayıları)."""
    if x is None or isinstance(x, (str, bytes)):
        return x
    if isinstance(x, (bool, np.bool_)):
        return int(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        x = float(x)
        return x if math.isfinite(x) else None
    if isinstance(x, (dict, list, tuple)):
        return json.dumps(x, ensure_ascii=False, sort_keys=True)
    raise TypeError(f"veritabanına yazılamayan değer: {type(x).__name__}")


def _simdi_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class Izgara:
    """Bir paritenin tam saatlik UTC ızgarası (eksik saat NaN). O/H/L/C/V strateji_lab.izgaraya_yerlestir ile
    birebir (bozuk fiyatlı mumun tamamı, sıfır/eksi hacmin yalnız V'si NaN); QV/N/AQV ham klines ekleri (fiyatı
    bozuk/eksik saatte NaN, eksi/sonsuz değer NaN, sıfır kalır)."""
    ts: np.ndarray
    O: np.ndarray
    H: np.ndarray
    L: np.ndarray
    C: np.ndarray
    V: np.ndarray
    QV: np.ndarray
    N: np.ndarray
    AQV: np.ndarray


def izgaraya_koy(d: np.ndarray, g0: int, n: int) -> Izgara:
    """mum satırları (ts, o, h, l, c, v, qv, islem_adedi, alici_qv) -> g0'dan başlayan n saatlik Izgara."""
    d = np.asarray(d, dtype=float).reshape(-1, 9)
    O, H, L, C, V = sl.izgaraya_yerlestir(d[:, :6], g0, n)
    E = np.full((n, 3), np.nan)
    if len(d):
        rel = d[:, 0].astype('int64') - int(g0)
        i = rel // SAAT_MS
        uygun = (rel >= 0) & (i < n) & (rel % SAAT_MS == 0)
        E[i[uygun]] = d[uygun, 6:9]
    with np.errstate(invalid='ignore'):
        E[~np.isfinite(C)] = np.nan
        E[~np.isfinite(E) | (E < 0)] = np.nan
    return Izgara(int(g0) + SAAT_MS * np.arange(n, dtype=np.int64), O, H, L, C, V, E[:, 0].copy(), E[:, 1].copy(),
                  E[:, 2].copy())


def aday_ozellikleri(g: dict, btc: dict, izg: Izgara, i: int) -> dict:
    """Karar mumu i için aday satırının özellik sütunları: strateji_lab.ozellikler'in 15 özelliği (saat_sin/cos hariç;
    tanımlar orada, tek kod) + makine_ozellikleri.yeni_ozellikler (yalnız i hesaplanır). g: sl.gostergeler(izg.O, ...)
    çıktısı, btc: aynı ızgaraya (aynı ts) hizalı sl.btc_baglami çıktısı. btc_rejim, radar_sira, evren_sira ve
    sinyaller çağırandan gelir. Değerler float (NaN -> veritabanında NULL; desenler 1.0/0.0 -> INTEGER)."""
    lab = sl.ozellikler(g, btc, izg.ts, [i])
    yeni = mo.yeni_ozellikler(izg.O, izg.H, izg.L, izg.C, izg.QV, izg.N, izg.AQV, idx=[i])
    return {**{k: float(lab[k][0]) for k in LAB_OZELLIKLERI}, **{k: float(v[0]) for k, v in yeni.items()}}


def sabit_coin_mu(C) -> Optional[bool]:
    """strateji_lab kuralı: ilk MIN_GECMIS dolu saatin saatlik getiri std'si < SABIT_COIN_STD. Hesaplanamazsa None."""
    oyn = sl.saatlik_oynaklik(sl.ilk_gecmis(np.asarray(C, dtype=float)))
    return None if not np.isfinite(oyn) else bool(oyn < sl.SABIT_COIN_STD)


# ------------------------------------------------------------------------------------------
# Hafıza (SQLite)
# ------------------------------------------------------------------------------------------
class Hafiza:
    """Makinenin tek veritabanı bağlantısı. salt_okunur=True: --durum / --rapor için (göç yapılmaz, yazılamaz; yazan
    makine çalışırken de okunabilir: WAL). Yazmalar islem() içinde toplanır (her tur adımı bir işlem); islem()
    dışında çağrılan yazma yöntemleri kendi küçük işlemlerini açar."""

    def __init__(self, yol: str, salt_okunur: bool = False, saat: Callable[[], int] = _simdi_ms,
                 gocler: Optional[list] = None):
        self.yol, self.salt_okunur, self.saat = yol, salt_okunur, saat
        self._derinlik = 0
        if salt_okunur:
            if not os.path.exists(yol):
                raise FileNotFoundError(f"veritabanı yok: {yol} (makine hiç çalışmamış)")
            self.c = sqlite3.connect(f"file:{yol}?mode=ro", uri=True, timeout=30, isolation_level=None)
        else:
            yeni = not os.path.exists(yol) or os.path.getsize(yol) == 0
            self.c = sqlite3.connect(yol, timeout=30, isolation_level=None)
            if yeni:
                self.c.execute('PRAGMA auto_vacuum=INCREMENTAL')       # tablo oluşmadan önce verilmeli
            self.c.execute('PRAGMA journal_mode=WAL')
            self.c.execute('PRAGMA synchronous=NORMAL')
        self.c.row_factory = sqlite3.Row
        self.c.execute('PRAGMA busy_timeout=30000')
        if salt_okunur:
            self.sema_surumu = int(self.meta_oku('sema_surumu', 0) or 0)
            if self.sema_surumu > SEMA_SURUMU:
                raise SemaHatasi(f"veritabanı şema sürümü {self.sema_surumu}, bu kod {SEMA_SURUMU}: kodu güncelleyin")
        else:
            self._goc(GOCLER if gocler is None else gocler)
        self._sutunlar = {t: [r['name'] for r in self.c.execute(f'PRAGMA table_info({t})')] for t in TABLOLAR}

    # --- bağlantı ve işlem ---------------------------------------------------------------
    def _goc(self, gocler):
        """Şema göçleri: meta.sema_surumu'ndan büyük her göç sırayla, tek işlemde (yarım göç kalmaz)."""
        with self.islem():
            self.c.execute("CREATE TABLE IF NOT EXISTS meta (anahtar TEXT PRIMARY KEY, deger TEXT) WITHOUT ROWID")
            surum = int(self.meta_oku('sema_surumu', 0) or 0)
            en_son = max([s for s, _, _ in gocler] + [0])
            if surum > en_son:
                raise SemaHatasi(f"veritabanı şema sürümü {surum}, bu kod {en_son}: daha yeni bir makine sürümüyle "
                                 f"yazılmış, kodu güncelleyin")
            for s, aciklama, komutlar in sorted(gocler, key=lambda x: x[0]):
                if s <= surum:
                    continue
                for k in komutlar:
                    self.c.execute(k)
                self.meta_yaz('sema_surumu', s)
                LOG.info(f"veritabanı şeması {s}. sürüme geçti ({aciklama})")
                surum = s
        self.sema_surumu = surum

    @contextmanager
    def islem(self):
        """Yazma işlemi (BEGIN IMMEDIATE ... COMMIT; hata olursa ROLLBACK). İç içe çağrı SAVEPOINT olur: iç blok hata
        verirse yalnız onun yazdıkları geri alınır."""
        if self.salt_okunur:
            raise sqlite3.OperationalError("salt okunur hafızaya yazılamaz")
        if self._derinlik:
            ad = f"sp{self._derinlik}"
            self.c.execute(f'SAVEPOINT {ad}')
            self._derinlik += 1
            try:
                yield self.c
            except BaseException:
                self.c.execute(f'ROLLBACK TO {ad}')
                self.c.execute(f'RELEASE {ad}')
                raise
            else:
                self.c.execute(f'RELEASE {ad}')
            finally:
                self._derinlik -= 1
            return
        self.c.execute('BEGIN IMMEDIATE')
        self._derinlik = 1
        try:
            yield self.c
        except BaseException:
            self._derinlik = 0
            self.c.execute('ROLLBACK')
            raise
        self._derinlik = 0
        try:
            self.c.execute('COMMIT')
        except BaseException:
            self.c.execute('ROLLBACK')
            raise

    def kapat(self):
        if self.c is None:
            return
        if not self.salt_okunur:
            try:
                self.c.execute('PRAGMA optimize')
            except sqlite3.Error:
                pass
        self.c.close()
        self.c = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.kapat()

    def sorgu(self, sql: str, parametre: Sequence = ()) -> List[dict]:
        """Rapor ve durum için serbest okuma: satırlar dict listesi."""
        return [dict(r) for r in self.c.execute(sql, tuple(parametre))]

    def _yaz(self, tablo: str, satirlar: Iterable[dict]) -> int:
        """Upsert: anahtar çakışırsa verilen sütunların üzerine yazılır (aynı satır iki kez yazılırsa sonuç aynı).
        Satırlarda olmayan sütunlar yeni satırda NULL/varsayılan, var olan satırda dokunulmaz (toplu yazımda
        satırların sütunlarının birleşimi kullanılır: bir satırda eksik sütun NULL yazılır)."""
        satirlar = list(satirlar)
        if not satirlar:
            return 0
        sutun = list(dict.fromkeys(k for s in satirlar for k in s))
        bilinmeyen = [k for k in sutun if k not in self._sutunlar[tablo]]
        if bilinmeyen:
            raise ValueError(f"{tablo}: bilinmeyen sütun {bilinmeyen}")
        anahtar = ANAHTARLAR[tablo]
        eksik = [k for k in anahtar if k not in sutun]
        if eksik:
            raise ValueError(f"{tablo}: anahtar sütunu eksik {eksik}")
        guncel = [k for k in sutun if k not in anahtar]
        sql = (f"INSERT INTO {tablo} ({', '.join(sutun)}) VALUES ({', '.join('?' * len(sutun))}) "
               f"ON CONFLICT ({', '.join(anahtar)}) DO "
               + (f"UPDATE SET {', '.join(f'{k} = excluded.{k}' for k in guncel)}" if guncel else 'NOTHING'))
        with self.islem():
            self.c.executemany(sql, [tuple(_deger(s.get(k)) for k in sutun) for s in satirlar])
        return len(satirlar)

    # --- meta ----------------------------------------------------------------------------
    def meta_oku(self, anahtar: str, varsayilan=None):
        r = self.c.execute('SELECT deger FROM meta WHERE anahtar = ?', (anahtar,)).fetchone()
        return r[0] if r is not None else varsayilan

    def meta_yaz(self, anahtar: str, deger) -> None:
        with self.islem():
            self.c.execute('INSERT INTO meta (anahtar, deger) VALUES (?, ?) ON CONFLICT (anahtar) DO UPDATE SET '
                           'deger = excluded.deger', (anahtar, None if deger is None else str(deger)))

    # --- parite --------------------------------------------------------------------------
    def parite_oku(self, sembol: str) -> Optional[dict]:
        r = self.c.execute('SELECT * FROM parite WHERE sembol = ?', (sembol,)).fetchone()
        return dict(r) if r is not None else None

    def pariteler(self, durum: Optional[str] = None) -> Dict[str, dict]:
        sql, p = 'SELECT * FROM parite', ()
        if durum is not None:
            sql, p = sql + ' WHERE durum = ?', (durum,)
        return {r['sembol']: dict(r) for r in self.c.execute(sql + ' ORDER BY sembol', p)}

    def parite_yaz(self, sembol: str, **alanlar) -> None:
        """Yoksa ekler; varsa yalnız verilen alanları günceller."""
        self._yaz('parite', [{'sembol': sembol, **alanlar}])

    def sabit_karari(self, sembol: str, koru: Sequence[str] = ('BTC/USDT',)) -> Optional[bool]:
        """Sabit coin mu (modül belgesi): kalıcı karar varsa o; yoksa ilk MIN_GECMIS dolu saatle (yeterliyse kalıcı
        yazılır) ya da eldekiyle geçici karar. Mumu hiç yoksa None. koru'daki semboller asla sabit sayılmaz."""
        p = self.parite_oku(sembol)
        if p is not None and p.get('sabit') is not None:
            return bool(p['sabit'])
        if sembol in koru:
            if p is not None:
                self.parite_yaz(sembol, sabit=0)
            return False
        gecerli = 'o > 0 AND h > 0 AND l > 0 AND c > 0'
        r = self.c.execute(f'SELECT ts FROM mum WHERE sembol = ? AND {gecerli} ORDER BY ts LIMIT 1 OFFSET ?',
                           (sembol, sl.MIN_GECMIS - 1)).fetchone()
        kesin = r is not None
        sql = 'SELECT ts, o, h, l, c, v FROM mum WHERE sembol = ?' + (' AND ts <= ?' if kesin else '') + ' ORDER BY ts'
        d = np.asarray(self.c.execute(sql, (sembol, r[0]) if kesin else (sembol,)).fetchall(), dtype=float)
        if not len(d):
            return None
        d = d.reshape(-1, 6)
        g0 = int(d[0, 0])
        _, _, _, C, _ = sl.izgaraya_yerlestir(d, g0, int((d[-1, 0] - g0) // SAAT_MS) + 1)
        karar = sabit_coin_mu(C)
        if karar is not None and kesin:
            self.parite_yaz(sembol, sabit=int(karar))
        return karar

    # --- mum -----------------------------------------------------------------------------
    def mum_yaz(self, sembol: str, d: np.ndarray) -> int:
        """klines_ayristir satırları (ts, o, h, l, c, v, qv, islem_adedi, alici_qv) -> mum (upsert, idempotent)."""
        d = np.asarray(d, dtype=float).reshape(-1, 9)
        if not len(d):
            return 0
        satirlar = [(sembol, int(r[0]), *map(float, r[1:7]), int(r[7]), float(r[8])) for r in d.tolist()]
        with self.islem():
            self.c.executemany(
                'INSERT INTO mum (sembol, ts, o, h, l, c, v, qv, islem_adedi, alici_qv) VALUES (?,?,?,?,?,?,?,?,?,?) '
                'ON CONFLICT (sembol, ts) DO UPDATE SET o = excluded.o, h = excluded.h, l = excluded.l, '
                'c = excluded.c, v = excluded.v, qv = excluded.qv, islem_adedi = excluded.islem_adedi, '
                'alici_qv = excluded.alici_qv', satirlar)
        return len(satirlar)

    def son_mum_ts(self, sembol: str) -> Optional[int]:
        r = self.c.execute('SELECT MAX(ts) FROM mum WHERE sembol = ?', (sembol,)).fetchone()
        return int(r[0]) if r is not None and r[0] is not None else None

    def mumlar(self, sembol: str, bas_ts: int, bit_ts: int) -> np.ndarray:
        """[bas_ts, bit_ts) aralığında açılan mumlar: (n, 9) = ts, o, h, l, c, v, qv, islem_adedi, alici_qv."""
        d = self.c.execute('SELECT ts, o, h, l, c, v, qv, islem_adedi, alici_qv FROM mum WHERE sembol = ? AND '
                           'ts >= ? AND ts < ? ORDER BY ts', (sembol, int(bas_ts), int(bit_ts))).fetchall()
        return np.asarray(d, dtype=float).reshape(-1, 9)

    def izgara_aralik(self, sembol: str, bas_ts: int, bit_ts: int) -> Izgara:
        """[bas_ts, bit_ts) saatlik ızgarası (bas_ts saat başı olmalı)."""
        n = max(0, int((int(bit_ts) - int(bas_ts) + SAAT_MS - 1) // SAAT_MS))
        return izgaraya_koy(self.mumlar(sembol, bas_ts, bit_ts), bas_ts, n)

    def izgara(self, sembol: str, son_ts: int, n: int = PENCERE) -> Izgara:
        """son_ts (dahil) ile biten n saatlik kayan pencere (göstergeler bunun üstünde hesaplanır)."""
        return self.izgara_aralik(sembol, int(son_ts) - (n - 1) * SAAT_MS, int(son_ts) + SAAT_MS)

    def izgaralar(self, semboller: Iterable[str], son_ts: int, n: int = PENCERE) -> Dict[str, Izgara]:
        return {s: self.izgara(s, son_ts, n) for s in semboller}

    # --- aday, etiket, defter --------------------------------------------------------------
    def aday_yaz(self, satirlar: Iterable[dict]) -> int:
        """Her satır: ts, sembol + aday sütunları (LAB_OZELLIKLERI, yeni özellikler, btc_rejim, radar_sira,
        evren_sira, sinyaller). NaN -> NULL. Aynı (ts, sembol) yeniden yazılırsa üzerine yazılır."""
        return self._yaz('aday', satirlar)

    def adaylar(self, ts: int) -> List[dict]:
        return self.sorgu('SELECT * FROM aday WHERE ts = ? ORDER BY sembol', (int(ts),))

    def etiket_yaz(self, satirlar: Iterable[dict]) -> int:
        return self._yaz('etiket', satirlar)

    def defter_yaz(self, satirlar: Iterable[dict]) -> int:
        return self._yaz('defter', satirlar)

    def etiket_bekleyenler(self, simdi_ms: int, limit: int = 5000,
                           geri_gun: int = ETIKET_GERI_GUN) -> List[Tuple[int, str]]:
        """Etiketi artık hesaplanabilecek aday satırları (eskiden yeniye): t+24 mumu kapanmış ve çekilmiş
        (parite.kapsam_ts > ts + 24 saat), henüz etiket satırı yok, ts son geri_gun gün içinde."""
        ust = int(simdi_ms) - (mo.UFUK + 1) * SAAT_MS
        r = self.c.execute(
            'SELECT a.ts, a.sembol FROM aday a JOIN parite p ON p.sembol = a.sembol '
            'WHERE a.ts >= ? AND a.ts <= ? AND p.kapsam_ts > a.ts + ? '
            'AND NOT EXISTS (SELECT 1 FROM etiket e WHERE e.ts = a.ts AND e.sembol = a.sembol) '
            'ORDER BY a.ts, a.sembol LIMIT ?',
            (ust - int(geri_gun) * GUN_MS, ust, mo.UFUK * SAAT_MS, int(limit))).fetchall()
        return [(int(x[0]), x[1]) for x in r]

    def etiketle(self, simdi_ms: int, limit: int = 5000, geri_gun: int = ETIKET_GERI_GUN) -> int:
        """Bekleyen aday satırlarını etiketler (makine_ozellikleri.ileri_etiketler; ızgara paritenin kapsamıyla
        sınırlı: henüz çekilmemiş saat 'eksik' sayılmaz). Dönen: yazılan etiket satırı (kalıcı NULL olanlar dahil)."""
        bekleyen: Dict[str, List[int]] = {}
        for ts, s in self.etiket_bekleyenler(simdi_ms, limit, geri_gun):
            bekleyen.setdefault(s, []).append(ts)
        satirlar = []
        for s, tsler in bekleyen.items():
            kapsam = int(self.parite_oku(s)['kapsam_ts'])
            g = self.izgara_aralik(s, min(tsler), min(kapsam, max(tsler) + (mo.UFUK + 1) * SAAT_MS))
            for ts in tsler:
                e = mo.ileri_etiketler(g.O, g.H, g.L, g.C, int((ts - g.ts[0]) // SAAT_MS))
                if e is not None:
                    satirlar.append({'ts': ts, 'sembol': s, **e, 'olusturma_ms': int(simdi_ms)})
        return self.etiket_yaz(satirlar)

    # --- kâğıt işlem -----------------------------------------------------------------------
    def kagit_ekle(self, kayit: dict) -> Optional[int]:
        """Yeni kâğıt işlem; (strateji, sembol, sinyal_ts) zaten varsa eklenmez ve None döner (yeniden başlatmada
        aynı sinyal iki kez açılmaz)."""
        bilinmeyen = [k for k in kayit if k not in self._sutunlar['kagit_islem'] or k == 'id']
        if bilinmeyen:
            raise ValueError(f"kagit_islem: bilinmeyen sütun {bilinmeyen}")
        sutun = list(kayit)
        with self.islem():
            # önce bak: INSERT OR IGNORE yok sayılan satırda da AUTOINCREMENT sayacını ilerletir (kimlikte boşluk)
            if self.c.execute('SELECT 1 FROM kagit_islem WHERE strateji = ? AND sembol = ? AND sinyal_ts = ?',
                              (kayit.get('strateji'), kayit.get('sembol'), _deger(kayit.get('sinyal_ts')))).fetchone():
                return None
            cur = self.c.execute(f"INSERT INTO kagit_islem ({', '.join(sutun)}) VALUES ({', '.join('?' * len(sutun))})",
                                 tuple(_deger(kayit[k]) for k in sutun))
            return int(cur.lastrowid)

    def kagit_guncelle(self, islem_id: int, **alanlar) -> None:
        bilinmeyen = [k for k in alanlar if k not in self._sutunlar['kagit_islem'] or k == 'id']
        if bilinmeyen:
            raise ValueError(f"kagit_islem: bilinmeyen sütun {bilinmeyen}")
        if not alanlar:
            return
        with self.islem():
            self.c.execute(f"UPDATE kagit_islem SET {', '.join(f'{k} = ?' for k in alanlar)} WHERE id = ?",
                           tuple(_deger(v) for v in alanlar.values()) + (int(islem_id),))

    def kagit_islemler(self, durum=None, strateji: Optional[str] = None, sembol: Optional[str] = None) -> List[dict]:
        """Kâğıt işlemler (id sırasıyla). durum: tek durum ya da durumlar dizisi."""
        kosul, p = [], []
        if durum is not None:
            durumlar = (durum,) if isinstance(durum, str) else tuple(durum)
            kosul.append(f"durum IN ({', '.join('?' * len(durumlar))})")
            p += durumlar
        for ad, deger in (('strateji', strateji), ('sembol', sembol)):
            if deger is not None:
                kosul.append(f'{ad} = ?')
                p.append(deger)
        return self.sorgu('SELECT * FROM kagit_islem' + (' WHERE ' + ' AND '.join(kosul) if kosul else '')
                          + ' ORDER BY id', p)

    # --- tarama ve olay --------------------------------------------------------------------
    def tarama_yaz(self, kayit: dict) -> None:
        """Karar saati başına tur kaydı (ts anahtar; aynı saat yeniden yazılırsa güncellenir). ayrinti dict ise JSON."""
        self._yaz('tarama', [kayit])

    def taramalar(self, bas_ts: Optional[int] = None, bit_ts: Optional[int] = None) -> List[dict]:
        """[bas_ts, bit_ts) karar saatlerinin tur kayıtları (ts sırasıyla)."""
        return self.sorgu('SELECT * FROM tarama WHERE ts >= ? AND ts < ? ORDER BY ts',
                          (-1 if bas_ts is None else int(bas_ts), 2 ** 62 if bit_ts is None else int(bit_ts)))

    def olay_yaz(self, tur: str, mesaj: str, sembol: Optional[str] = None, ms: Optional[int] = None) -> None:
        """Hata/uyarı günlüğü (tur: ör. HATA, UYARI, MUM_HATASI, TELEGRAM). mesaj kısaltılır (2000 karakter)."""
        with self.islem():
            self.c.execute('INSERT INTO olay (ms, tur, sembol, mesaj) VALUES (?, ?, ?, ?)',
                           (int(self.saat() if ms is None else ms), tur, sembol, str(mesaj)[:2000]))

    def olaylar(self, bas_ms: Optional[int] = None, tur: Optional[str] = None, limit: Optional[int] = None) -> List[dict]:
        """Olaylar (yeniden eskiye)."""
        kosul, p = ['ms >= ?'], [-1 if bas_ms is None else int(bas_ms)]
        if tur is not None:
            kosul.append('tur = ?')
            p.append(tur)
        return self.sorgu('SELECT * FROM olay WHERE ' + ' AND '.join(kosul) + ' ORDER BY ms DESC, id DESC'
                          + (' LIMIT ?' if limit is not None else ''), p + ([int(limit)] if limit is not None else []))

    def uyari_izinli(self, tur: str, simdi_ms: int, aralik_ms: int = 6 * SAAT_MS) -> bool:
        """Uyarı sınırlaması: bu türden son uyarı aralik_ms'den eskiyse True döner ve şimdiyi kaydeder."""
        anahtar = f'uyari_{tur}'
        son = self.meta_oku(anahtar)
        if son is not None and int(simdi_ms) - int(son) < aralik_ms:
            return False
        self.meta_yaz(anahtar, int(simdi_ms))
        return True

    # --- istatistik ve saklama --------------------------------------------------------------
    def sayimlar(self) -> Dict[str, int]:
        return {t: int(self.c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]) for t in TABLOLAR}

    def boyut(self) -> int:
        """Veritabanı dosyası + WAL (bayt)."""
        return sum(os.path.getsize(self.yol + ek) for ek in ('', '-wal') if os.path.exists(self.yol + ek))

    def temizle(self, simdi_ms: int, aday_gun: int = 400) -> Dict[str, int]:
        """Saklama süresi: aday/etiket/defter aday_gun günden, mum aday_gun + MUM_EK_GUN günden, olay OLAY_GUN günden
        eski satırlar silinir (sınır dahil değil: ts == sınır kalır). kagit_islem, tarama, parite ve meta silinmez.
        Sonra incremental_vacuum ile boş sayfalar diske geri verilir ve WAL kısaltılır. Dönen: tablo başına silinen."""
        simdi_ms = int(simdi_ms)
        sinir = simdi_ms - int(aday_gun) * GUN_MS
        mum_sinir = simdi_ms - (int(aday_gun) + MUM_EK_GUN) * GUN_MS
        silinen = {}
        with self.islem():
            for t in ('aday', 'etiket', 'defter'):
                silinen[t] = self.c.execute(f'DELETE FROM {t} WHERE ts < ?', (sinir,)).rowcount
            silinen['mum'] = 0
            for (s,) in self.c.execute('SELECT DISTINCT sembol FROM parite').fetchall():   # PK (sembol, ts) ile
                silinen['mum'] += self.c.execute('DELETE FROM mum WHERE sembol = ? AND ts < ?', (s, mum_sinir)).rowcount
            silinen['mum'] += self.c.execute('DELETE FROM mum WHERE ts < ? AND sembol NOT IN (SELECT sembol FROM '
                                             'parite)', (mum_sinir,)).rowcount
            silinen['olay'] = self.c.execute('DELETE FROM olay WHERE ms < ?', (simdi_ms - OLAY_GUN * GUN_MS,)).rowcount
        if any(silinen.values()):
            self.c.execute('PRAGMA incremental_vacuum').fetchall()     # her boş sayfa bir adım: sonuna kadar
            self.c.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchall()
        return silinen

    def gunluk_temizlik(self, simdi_ms: int, aday_gun: int = 400) -> Optional[Dict[str, int]]:
        """temizle'yi UTC günde en çok bir kez çalıştırır (meta.son_temizlik_gunu); bugün yapıldıysa None."""
        gun = int(simdi_ms) // GUN_MS
        if self.meta_oku('son_temizlik_gunu') == str(gun):
            return None
        sonuc = self.temizle(simdi_ms, aday_gun)
        self.meta_yaz('son_temizlik_gunu', gun)
        return sonuc


# ------------------------------------------------------------------------------------------
# Tek kopya kilidi
# ------------------------------------------------------------------------------------------
class ZatenCalisiyor(RuntimeError):
    """Aynı veritabanında başka bir makine kopyası çalışıyor."""


class TekKopya:
    """<db>.lock üzerinde fcntl.flock (LOCK_EX | LOCK_NB): ikinci kopya hemen ZatenCalisiyor alır. Kilit süreç
    ölünce çekirdek tarafından bırakılır (bayat kilit dosyası sorun değildir); dosya silinmez, içinde PID yazar.
    Dikkat: kilit nesnesi süreç boyunca bir değişkende tutulmalı; çöpe giderse dosya kapanır ve kilit bırakılır."""

    def __init__(self, yol: str):
        self.yol, self._f = yol, None

    def al(self) -> 'TekKopya':
        import fcntl
        f = os.fdopen(os.open(self.yol, os.O_RDWR | os.O_CREAT, 0o644), 'r+', encoding='utf-8')
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.seek(0)
            pid = f.read().strip()
            f.close()
            raise ZatenCalisiyor(f"Makine zaten çalışıyor (PID {pid or '?'}; kilit dosyası {self.yol}). İkinci kopya "
                                 f"başlatılmadı. Durum için: makine.py --durum") from None
        f.seek(0)
        f.truncate()
        f.write(f"{os.getpid()}\n")
        f.flush()
        self._f = f
        return self

    def birak(self) -> None:
        if self._f is None:
            return
        import fcntl
        try:
            fcntl.flock(self._f.fileno(), fcntl.LOCK_UN)
        finally:
            self._f.close()
            self._f = None

    def __enter__(self):
        return self.al()

    def __exit__(self, *a):
        self.birak()


# ------------------------------------------------------------------------------------------
# Mum toplayıcı (Binance ham klines)
# ------------------------------------------------------------------------------------------
def klines_ayristir(ham) -> np.ndarray:
    """Binance klines satırları (12 alan, çoğu metin) -> (n, 9) = ts, o, h, l, c, v, qv, islem_adedi, alici_qv,
    açılışa göre sıralı, tekrarsız. Bozuk satır (eksik alan, sayı değil, sonsuz) atlanır."""
    satirlar = []
    for r in ham or []:
        try:
            satirlar.append((float(int(r[0])), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]),
                             float(r[7]), float(int(float(r[8]))), float(r[10])))
        except (TypeError, ValueError, IndexError, OverflowError):
            continue
    d = np.asarray(satirlar, dtype=float).reshape(-1, 9)
    d = d[np.isfinite(d).all(axis=1)]
    _, i = np.unique(d[:, 0], return_index=True)
    return d[i]


def _kisa(e: BaseException) -> str:
    return f"{type(e).__name__}: {str(e)[:200]}"


class MumToplayici:
    """Ham 1s klines -> mum tablosu (modül belgesi). ex: anahtarsız ccxt binance (ya da aynı arayüzlü sahte borsa:
    market(sembol)['id'], load_markets(yenile), publicGetKlines(params)). uyku: bekleme işlevi (testte sahte)."""

    def __init__(self, ex, hafiza: Hafiza, uyku: Callable[[float], None] = time.sleep, deneme: int = DENEME,
                 pencere: int = PENCERE, sayfa: int = SAYFA, ardisik_hata_siniri: int = ARDISIK_HATA_SINIRI,
                 yok_bekleme_ms: int = YOK_BEKLEME_MS):
        self.ex, self.h, self.uyku, self.deneme = ex, hafiza, uyku, deneme
        self.pencere, self.sayfa = pencere, sayfa
        self.ardisik_hata_siniri, self.yok_bekleme_ms = ardisik_hata_siniri, yok_bekleme_ms
        self.istek = 0
        self._yenilendi = False

    def _istek(self, fn, *a, **k):
        """Ağ hatalarında 2, 4, 8 sn bekleyip yeniden dener; son denemenin hatası yükselir."""
        for deneme in range(self.deneme):
            try:
                self.istek += 1
                return fn(*a, **k)
            except AG_HATALARI as e:
                if deneme == self.deneme - 1:
                    raise
                bekle = 2 ** (deneme + 1)
                LOG.warning(f"ağ hatası ({_kisa(e)}); {bekle} sn sonra yeniden denenecek")
                self.uyku(bekle)

    def market_id(self, sembol: str) -> str:
        """Borsanın sembol kimliği (BTC/USDT -> BTCUSDT). Bilinmeyen sembolde piyasalar turda bir kez yeniden yüklenir
        (yeni listelenen parite); yine yoksa BadSymbol yükselir."""
        if not getattr(self.ex, 'markets', None):
            self._istek(self.ex.load_markets)
        try:
            return self.ex.market(sembol)['id']
        except ccxt.BadSymbol:
            if self._yenilendi:
                raise
            self._yenilendi = True
            self._istek(self.ex.load_markets, True)
            return self.ex.market(sembol)['id']

    def cek(self, sembol: str, simdi_ms: int) -> int:
        """Bir paritenin kapsamından şimdiye kadarki kapanmış mumlarını çeker ve yazar (sayfa sayfa; her sayfa kendi
        işleminde, yarıda kalırsa kapsam yazılan yere kadar ilerler). Dönen: yazılan mum sayısı. Hatalar yükselir."""
        simdi_ms = int(simdi_ms)
        acik = (simdi_ms // SAAT_MS) * SAAT_MS              # henüz kapanmamış mumun açılışı
        en_eski = acik - self.pencere * SAAT_MS
        p = self.h.parite_oku(sembol)
        kapsam = p.get('kapsam_ts') if p else None
        if kapsam is None:
            son = self.h.son_mum_ts(sembol)
            kapsam = son + SAAT_MS if son is not None else None
        bas = en_eski if kapsam is None else max(int(kapsam), en_eski)
        if bas >= acik:
            return 0
        mid = self.market_id(sembol)
        if p is None or p.get('ilk_gorulme_ms') is None:
            self.h.parite_yaz(sembol, market_id=mid, ilk_gorulme_ms=simdi_ms)
        yazilan = 0
        while bas < acik:
            limit = int(min(self.sayfa, (acik - bas) // SAAT_MS + 1))   # +1: açık mum da gelebilir (ayıklanır)
            ham = self._istek(self.ex.publicGetKlines, {'symbol': mid, 'interval': '1h', 'startTime': int(bas),
                                                         'limit': limit})
            d = klines_ayristir(ham)
            d = d[(d[:, 0] >= bas) & (d[:, 0] % SAAT_MS == 0)]
            if not len(d):
                break                                       # bu saatten sonra borsada mum yok (ya da henüz yok)
            kapali = d[d[:, 0] + SAAT_MS <= simdi_ms]
            with self.h.islem():
                yazilan += self.h.mum_yaz(sembol, kapali)
                # kapsam yalnız borsanın gösterdiği son mumun saatine kadar (açık mum geldiyse şimdiye kadar)
                self.h.parite_yaz(sembol, kapsam_ts=min(acik, int(d[-1, 0]) + SAAT_MS), market_id=mid,
                                  durum='AKTIF')
            if len(ham) < limit:
                break
            bas = int(d[-1, 0]) + SAAT_MS
        return yazilan

    def guncelle(self, semboller: Iterable[str], simdi_ms: int) -> dict:
        """Her paritenin mumlarını günceller; hiçbir parite hatası yükselmez (tur ölmez). Başarılı paritelerde sabit
        coin kararı da güncellenir. Dönen: {'yeni': {sembol: yazılan}, 'hata': {sembol: mesaj}, 'yok': [sembol]
        (BadSymbol ya da bekleme süresinde), 'atlanan': [sembol] (üst üste ağ hatası: denenmedi), 'istek': n}."""
        self._yenilendi = False
        sonuc = {'yeni': {}, 'hata': {}, 'yok': [], 'atlanan': [], 'istek': 0}
        istek0, ardisik = self.istek, 0
        for s in dict.fromkeys(semboller):
            if ardisik >= self.ardisik_hata_siniri:
                sonuc['atlanan'].append(s)
                continue
            p = self.h.parite_oku(s)
            if p and p.get('durum') == 'YOK' and int(simdi_ms) - int(p.get('durum_ms') or 0) < self.yok_bekleme_ms:
                sonuc['yok'].append(s)
                continue
            try:
                sonuc['yeni'][s] = self.cek(s, simdi_ms)
                ardisik = 0
            except ccxt.BadSymbol as e:
                ardisik = 0
                self.h.parite_yaz(s, durum='YOK', durum_ms=int(simdi_ms), son_hata=_kisa(e))
                sonuc['yok'].append(s)
                LOG.warning(f"{s}: borsada yok, bırakıldı ({_kisa(e)})")
                continue
            except Exception as e:  # noqa: BLE001 - bir paritenin hatası turu durdurmaz
                ardisik = ardisik + 1 if isinstance(e, AG_HATALARI) else 0
                sonuc['hata'][s] = _kisa(e)
                LOG.warning(f"{s}: mumlar alınamadı, bu tur atlandı ({_kisa(e)})")
                try:
                    self.h.parite_yaz(s, son_hata=_kisa(e))
                except sqlite3.Error:
                    pass
                continue
            try:
                self.h.sabit_karari(s)
            except sqlite3.Error as e:
                LOG.warning(f"{s}: sabit coin kararı yazılamadı ({_kisa(e)})")
        if sonuc['atlanan']:
            LOG.error(f"üst üste {self.ardisik_hata_siniri} paritede ağ hatası: {len(sonuc['atlanan'])} parite bu tur "
                      f"denenmedi")
        sonuc['istek'] = self.istek - istek0
        return sonuc


# ------------------------------------------------------------------------------------------
# Evren
# ------------------------------------------------------------------------------------------
@dataclass
class Evren:
    semboller: List[str]            # BTC/USDT önce, sonra (quoteVolume, sembol) azalan: strateji_lab sütun sırası
    sira: Dict[str, int]            # evren_sira: kurallara uyan pariteler içinde quoteVolume sırası (1 = en hacimli)
    hacim: Dict[str, float]         # 24s quoteVolume (USDT)
    guncellenecek: List[str]        # semboller + korunan (açık kâğıt işlemi olan) pariteler: mumları her tur çekilir


def _sayi(x) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else 0.0
    except (TypeError, ValueError):
        return 0.0


def kaldiracli_mi(baz: str, bazlar) -> bool:
    """Kaldıraçlı token (BTCUP, ETHDOWN, XRPBULL, EOSBEAR): ek UP/DOWN/BULL/BEAR VE eksiz kısım (en az 2 harf) başka
    bir USDT paritesinin bazı. JUP (J), SYRUP (SYR paritesi yok) gibi gerçek coinler kalır."""
    for ek in KALDIRACLI_EKLER:
        on = baz[:-len(ek)]
        if baz.endswith(ek) and len(on) >= 2 and on in bazlar:
            return True
    return False


def evren_sec(tickers: Dict[str, dict], min_hacim: float, n: int, korunan: Iterable[str] = (),
              haric: Iterable[str] = (), piyasalar: Optional[Dict[str, dict]] = None,
              yasakli: Optional[Sequence[str]] = None) -> Evren:
    """fetch_tickers sonucundan evren (modül belgesi). haric: sabit coinler (strateji_lab gibi ilk n seçildikten
    sonra çıkarılır). piyasalar (ex.markets) verilirse spot olmayan ya da aktif olmayan pariteler de çıkar."""
    yasakli = sl.bf.YASAKLI_COINLER if yasakli is None else yasakli
    usdt = [s for s in tickers if isinstance(s, str) and s.endswith('/USDT') and s.count('/') == 1]
    bazlar = {s[:-5] for s in usdt}
    adaylar = []
    for s in usdt:
        if not s.isascii() or any(y in s for y in yasakli) or kaldiracli_mi(s[:-5], bazlar):
            continue
        m = (piyasalar or {}).get(s)
        if m is not None and (m.get('spot') is False or m.get('active') is False):
            continue
        qv = _sayi((tickers[s] or {}).get('quoteVolume'))
        if qv >= min_hacim or s == 'BTC/USDT':
            adaylar.append((qv, s))
    sirali = [s for _, s in sorted(adaylar, reverse=True)]       # bf.evren_sec ile aynı sıra (eşitlikte sembol)
    haric = set(haric)
    secilen = [s for s in [s for s in sirali if s != 'BTC/USDT'][:max(int(n), 0)] if s not in haric]
    semboller = ['BTC/USDT'] + secilen
    korunan = [k for k in dict.fromkeys(korunan) if k not in semboller]
    return Evren(semboller=semboller, sira={s: i + 1 for i, s in enumerate(sirali)},
                 hacim={s: _sayi((tickers.get(s) or {}).get('quoteVolume')) for s in semboller},
                 guncellenecek=semboller + korunan)
