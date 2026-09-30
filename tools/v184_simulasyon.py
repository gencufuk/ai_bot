# -*- coding: utf-8 -*-
"""V18.4 GEÇMİŞ SİMÜLASYONU: yeni sürüm geçmişte çalışsaydı ne olurdu?

tools/gecmis_simulasyon.py gerçekleşen işlemleri OLDUĞU GİBİ oynatır (o günkü sürümün çıkışlarıyla).
Bu araç fiyat geçmişini Binance'ten indirir ve V18.4'ün kararlarını yeniden üretir. Çıkış kararlarını
canlı botun kullandığı kodun AYNISI verir (sniper.risk_motoru).

Kaynaklar (--kaynak):
  gercek    Botun gerçekten açtığı pozisyonlar; giriş zamanı ve gerçek dolum fiyatı korunur.
              GERCEK      o günkü sürümün gerçekleşen sonucu (fiyatlardan, komisyon dahil)
              ESKI_SIM    aynı girişler, Ağustos botunun (V18.0.2) çıkış mantığıyla SİMÜLE: ilk kâr kademesi
                          sabit %4, kâr kilidi +%0.2, momentum çıkışı yok, 25 mumluk RSI. 16 Eylül öncesinde
                          GERCEK ile farkı = simülatörün hata payı
              V1802       Ağustos botu çalışmaya devam etseydi: V18.0.2 çıkışları + V18.0.2 giriş filtreleri
                          (ATR <= %4, BTC > EMA200 histerezissiz + ani çöküş koruması, core AI >= 0.65)
              V184_CIKIS  aynı girişler, V18.4 çıkış motoru
              V184        + V18.4 giriş filtreleri: BTC onayı (EMA200 histerezisi, ani çöküş koruması),
                          TREND rejimi (BTC 15m ADX >= 20), ATR <= %3
              V184_AI     + core model vetosu (model dosyası varsa)
  backfill  Sinyaller geçmiş 15m mumlardan yeniden üretilir (kapalı mum modu; radar: 24s hacmi 12M+ olan en çok
            yükselen 10 parite). Ağustos botu ile V18.4 aynı sinyal kuralını ve radarı kullanır, yalnız ATR sınırı
            farklıdır: sinyaller ATR <= %4 ve BTC durumundan bağımsız üretilir, senaryolar AYNI havuzu süzer.
            "Bot baştan bu ayarla çalışsaydı" sorusunun yaklaşık cevabı.
              B_V184         BTC onayı (histerezis), TREND rejimi, ATR <= %3, V18.4 çıkışı
              B_REJIMSIZ     B_V184, yatay rejim filtresi olmadan
              B_V184_AI      B_V184 + core model vetosu (canlıdaki kurulum)
              B_AI_ATR4      B_V184_AI, ATR sınırı %4
              B_AI_REJIMSIZ  B_V184_AI, yatay rejim filtresi olmadan
              B_V1802        Ağustos botu: BTC > EMA200 (histerezissiz) + ani çöküş koruması, ATR <= %4,
                             eski core model vetosu, V18.0.2 çıkışı

Bütçe: her senaryo, botun kurallarıyla oynatılır: aynı coinde tek pozisyon, tam çıkıştan sonra 1 saat
bekleme, üst üste 2 stopta 24 saat kara liste, serbest bakiye >= kasa x 1.01. Kısmi satışın parası satış
anında kasaya döner.

Fiyat yolu: 1m mum yeşilse açılış→dip→tepe→kapanış, kırmızıysa açılış→tepe→dip→kapanış. Düşen bacakta
stop/kısmi eşiği kesilirse karar tam eşik fiyatında verilir; dolum = eşik x (1 - --kayma-seviye)
(canlıda 2 sn'lik tarama gecikmesi + piyasa emri kayması). Mum eşiğin altında açılırsa açılıştan,
zaman/momentum/RSI çıkışları o anki fiyattan (--kayma-zaman). Zaman tetikleri dakikada 4 noktada
değerlendirilir (canlı bot 2 sn'de bir).

Bilinen sınırlar:
  - Eski sürümler 4 saatlik süre uzatmasında giriş zamanını sıfırlıyordu (CSV'deki Sure_Saat uzatmadan
    itibaren). Gerçek giriş, dolum fiyatının 1m mum aralığına düştüğü an aranarak bulunur (k x 4 saat geri);
    bulunamayanlar raporda sayılır.
  - BTC rejimi son KAPANMIŞ 15m mumla hesaplanır (canlı bot açık mumu da kullanır).
  - backfill: kapalı mum modu (canlı varsayılan açık mum), evren bugünkü paritelerdir (delist olanlar yok).
  - Filtre modeli (filter_model.json) uygulanmaz: Ağustos verisiyle eğitildiği için Ağustos'a uygulanması
    geleceği görmek olur (ANALIZ_V18.4.md §2).

Kullanım (sunucuda, bot çalışırken de olur; API anahtarı gerekmez, yalnız herkese açık fiyat verisi):
  cd /root && /root/venv/bin/python tools/v184_simulasyon.py --baslangic 2026-08-05
  /root/venv/bin/python tools/v184_simulasyon.py --baslangic 2026-08-07 --bitis 2026-09-06 --kaynak gercek
  /root/venv/bin/python tools/v184_simulasyon.py --baslangic 2026-08-05 --limit 20      # 1 dk'lık deneme
İndirilen mumlar sim_onbellek/ klasöründe tutulur; ikinci çalıştırma çok daha hızlıdır.
Çıktılar: v184_sim_rapor.txt (özet), v184_sim_rapor.json, v184_sim_pozisyonlar.csv, v184_sim_backfill.csv
Yalnız piyasa taraması, eski raporu ezmeden:  ... --kaynak backfill --cikti /root/v184_sim_b
Uzun dönem (ör. 9 ay, 250 sembol; 1-2 saat, ~1 GB disk önbelleği, ~1 GB bellek; screen içinde çalıştırın):
  ... --baslangic 2026-01-01 --kaynak backfill --mod sabit --cikti /root/v184_sim_9ay
Raporun 6. bölümü ay ay kâr ve ay sonu bakiyesini verir.
"""
import argparse
import datetime
import glob
import json
import os
import pickle
import sys
import time
from collections import Counter
from dataclasses import dataclass, replace
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _yol in (KOK, os.path.join(KOK, 'tools')):
    if _yol not in sys.path:
        sys.path.insert(0, _yol)

import backfill_sinyaller as bf  # noqa: E402
import gecmis_simulasyon as gs  # noqa: E402
from sniper import etiket_deposu as depo_etiket  # noqa: E402
from sniper import risk_motoru as risk  # noqa: E402
from sniper.ozellikler import MUM_15M_MS, SinyalAyarlari, ohlcv_df, ozellik_matrisi  # noqa: E402

DK_MS = 60_000
SAAT_MS = 3_600_000
GUN_MS = 86_400_000
TF_MS = {'1m': DK_MS, '15m': MUM_15M_MS, '1h': SAAT_MS, '4h': 4 * SAAT_MS, '1d': GUN_MS}

# ai_bot.py V18.4 RISK_AYAR ile aynı (tests/test_v184_simulasyon.py karşılaştırır)
RISK_V184 = risk.RiskAyarlari(fee_rate=0.001, zarar_orani_balina=0.020, kar_kilidi_oran=0.010,
                              max_bekleme_saati=4.0, min_beklenti_orani=0.005, momentum_olu_saat=1.5)
# Ağustos botu = V18.0.2 (kullanıcının Ağustos commit'i): ilk kâr kademesi ATR'den bağımsız sabit %4
# ("elif m_k >= 0.04"), kâr kilidi +%0.2 ("🛡️ BAŞA BAŞ KORUMASI"), momentum çıkışı yok, RSI 25 mumla.
# tests/test_v184_simulasyon.py bu ayarların V18.0.2 koduyla aynı kararı verdiğini doğrular.
RISK_ESKI = replace(RISK_V184, kar_kilidi_oran=0.002, momentum_olu_saat=1e9, ilk_esik_min=0.04, ilk_esik_max=0.04)
MAX_ATR_V184 = 3.0            # ai_bot.MAX_ATR_PCT (ATR/fiyat > %3 ise sinyal yok)
MAX_ATR_V1802 = 4.0           # V18.0.2: (atr_val / price) > 0.04 ise girmez
COOLDOWN_MS = SAAT_MS         # _pozisyonu_kapat: tam çıkıştan sonra 1 saat
KARA_LISTE_MS = GUN_MS        # üst üste 2 stop -> 24 saat
ALIM_KAYMASI = 0.0005         # backfill: sinyal fiyatı -> market alım dolumu (etiketleme ile aynı)
YOL_OFSET_MS = (0, 20_000, 40_000, 59_000)
UZATMA_MS = 4 * SAAT_MS
VARSAYILAN_DESENLER = ['core_islem_verileri.csv', 'core_islem_verileri_v2.csv', '*.onarildi.csv']
# ESKI_SIM V18.0.2'yi modeller: V18.0.2'nin son işlemi 6 Eylül; 16 Eylül'den itibaren V18.x (V2 CSV, 17 Eylül'den
# rejim filtresi, ATR'ye bağlı ilk kâr kademesi, AI gölge mod), 21 Eylül'den V18.3. Doğrulama yalnız 16 Eylül
# öncesi açılan pozisyonlarla yapılır.
ESKI_SURUM_BITIS = '2026-09-16'
SENARYO_ACIKLAMA = {
    'GERCEK': 'gerçekleşen (o günkü sürüm)',
    'ESKI_SIM': 'aynı girişler, V18.0.2 (Ağustos) çıkışları (doğrulama)',
    'V1802': 'Ağustos botu devam etseydi (V18.0.2 çıkış + giriş filtreleri)',
    'V184_CIKIS': 'aynı girişler, V18.4 çıkışı',
    'V184': 'V18.4 çıkışı + giriş filtreleri',
    'V184_AI': 'V184 + core model vetosu',
    'B_V184': 'backfill sinyalleri, V18.4',
    'B_REJIMSIZ': 'backfill, yatay rejim filtresi yok',
    'B_V184_AI': 'backfill, V18.4 + AI (canlıdaki kurulum)',
    'B_AI_ATR4': 'backfill, V18.4 + AI, ATR sınırı %4',
    'B_AI_REJIMSIZ': 'backfill, V18.4 + AI, yatay rejim filtresi yok',
    'B_V1802': 'backfill, Ağustos botu (V18.0.2 giriş filtreleri + çıkışı)',
}
B_SENARYOLAR = ['B_V184', 'B_REJIMSIZ', 'B_V184_AI', 'B_AI_ATR4', 'B_AI_REJIMSIZ', 'B_V1802']
B_AI_SENARYOLARI = ('B_V184_AI', 'B_AI_ATR4', 'B_AI_REJIMSIZ')


def _ms(ts) -> int:
    return int((pd.Timestamp(ts) - pd.Timestamp('1970-01-01')) // pd.Timedelta(milliseconds=1))


def _tarih(ms) -> str:
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).strftime('%Y-%m-%d %H:%M')


# ------------------------------------------------------------------------------------------
# Mum deposu
# ------------------------------------------------------------------------------------------
class VeriYok(Exception):
    """Sembol borsada yok (delist) ya da veri alınamadı."""


class MumDeposu:
    """fetch_ohlcv sonuçlarını bellekte ve (dizin verilirse) diskte tutar; aynı aralık iki kez indirilmez.
    Henüz kapanmamış mum asla saklanmaz."""

    def __init__(self, ex, dizin=None, simdi_ms=None, deneme=4):
        self.ex, self.dizin, self.deneme = ex, dizin, deneme
        self.simdi_ms = int(simdi_ms if simdi_ms is not None else time.time() * 1000)
        self._veri: Dict[tuple, np.ndarray] = {}
        self._kapsam: Dict[tuple, list] = {}
        self._yok = set()
        self.istek = 0
        if dizin:
            os.makedirs(dizin, exist_ok=True)

    def _dosya(self, sym, tf):
        return os.path.join(self.dizin, f"{sym.replace('/', '_').replace(':', '_')}_{tf}.pkl")

    def _yukle(self, sym, tf):
        if (sym, tf) in self._veri:
            return
        veri, kapsam = np.empty((0, 6)), []
        if self.dizin and os.path.exists(self._dosya(sym, tf)):
            try:
                with open(self._dosya(sym, tf), 'rb') as f:
                    d = pickle.load(f)
                veri = np.asarray(d['veri'], dtype=float).reshape(-1, 6)
                kapsam = [(int(x), int(y)) for x, y in d['kapsam']]
            except Exception:  # noqa: BLE001 - bozuk önbellek yeniden indirilir
                veri, kapsam = np.empty((0, 6)), []
        self._veri[(sym, tf)], self._kapsam[(sym, tf)] = veri, kapsam

    def _kaydet(self, sym, tf):
        if not self.dizin:
            return
        yol = self._dosya(sym, tf)
        with open(yol + '.tmp', 'wb') as f:
            pickle.dump({'veri': self._veri[(sym, tf)], 'kapsam': self._kapsam[(sym, tf)]}, f)
        os.replace(yol + '.tmp', yol)

    @staticmethod
    def eksikler(kapsam, a, b):
        eksik, bas = [], a
        for x, y in sorted(kapsam):
            if y <= bas or x >= b:
                continue
            if x > bas:
                eksik.append((bas, x))
            bas = max(bas, y)
            if bas >= b:
                break
        if bas < b:
            eksik.append((bas, b))
        return eksik

    @staticmethod
    def birlestir(kapsam):
        sonuc = []
        for x, y in sorted(kapsam):
            if sonuc and x <= sonuc[-1][1]:
                sonuc[-1] = (sonuc[-1][0], max(sonuc[-1][1], y))
            else:
                sonuc.append((x, y))
        return sonuc

    def _cek(self, sym, tf, since):
        import ccxt
        for deneme in range(self.deneme):
            try:
                self.istek += 1
                return self.ex.fetch_ohlcv(sym, tf, since=int(since), limit=1000)
            except ccxt.BadSymbol as e:
                raise VeriYok(f"{sym}: {e}") from e
            except ccxt.OperationFailed:   # ağ/zaman aşımı/limit: bekle ve tekrar dene
                if deneme == self.deneme - 1:
                    raise
                time.sleep(2 ** (deneme + 1))
        return []

    def getir(self, sym, tf, a, b) -> np.ndarray:
        """[a, b) aralığında açılan KAPANMIŞ mumlar: ndarray (n, 6) = ts, o, h, l, c, v."""
        if sym in self._yok:
            raise VeriYok(sym)
        adim = TF_MS[tf]
        acik_mum = (self.simdi_ms // adim) * adim          # henüz kapanmamış mumun açılışı
        a, b = (int(a) // adim) * adim, min(int(b), acik_mum)
        if b <= a:
            return np.empty((0, 6))
        self._yukle(sym, tf)
        anahtar = (sym, tf)
        eksik = self.eksikler(self._kapsam[anahtar], a, b)
        if eksik:
            yeni, kapsam = [], list(self._kapsam[anahtar])
            try:
                for x, y in eksik:
                    since, son_kapsam = x, y
                    while since < y:
                        parca = self._cek(sym, tf, since)
                        if not parca:
                            break
                        yeni.extend(parca)
                        son = int(parca[-1][0])
                        if son + adim <= since:
                            break
                        since = son + adim
                        son_kapsam = max(son_kapsam, min(since, acik_mum))
                    kapsam.append((x, son_kapsam))
            except VeriYok:
                self._yok.add(sym)
                raise
            if yeni:
                arr = np.asarray([r[:6] for r in yeni], dtype=float)
                arr = arr[arr[:, 0] < acik_mum]
                eski = self._veri[anahtar]
                birlesik = np.vstack([eski, arr]) if len(eski) else arr
                _, idx = np.unique(birlesik[:, 0], return_index=True)
                self._veri[anahtar] = birlesik[idx]
            self._kapsam[anahtar] = self.birlestir(kapsam)
            self._kaydet(sym, tf)
        v = self._veri[anahtar]
        if not len(v):
            return v
        i, j = np.searchsorted(v[:, 0], [a, b], side='left')
        return v[i:j]

    def bosalt(self, tf=None, sym=None):
        """Bellekteki mumları bırakır; disk önbelleği kalır ve gerekirse yeniden okunur. Uzun dönemlerde (ör. 9 ay,
        250 sembol) tüm mumları bellekte tutmak bot ile aynı sunucuda 1 GB'ı aşıyordu."""
        for k in [k for k in self._veri if (tf is None or k[1] == tf) and (sym is None or k[0] == sym)]:
            del self._veri[k], self._kapsam[k]

    def getir_df(self, sym, tf, a, b) -> pd.DataFrame:
        df = ohlcv_df(self.getir(sym, tf, a, b).tolist())
        df['ts'] = df['ts'].astype('int64')
        return df


# ------------------------------------------------------------------------------------------
# BTC bağlamı (radar_loop'un BTC_OK ve REJIM kararları, kapalı mumla)
# ------------------------------------------------------------------------------------------
class BtcBaglami:
    def __init__(self, btc_df: pd.DataFrame):
        self.df = bf.btc_baglami(btc_df)
        self.kapanis = self.df.index.to_numpy(dtype='int64')
        self.btc_ok = self.df['btc_ok'].to_numpy(dtype=bool)
        self.rejim = self.df['rejim'].astype(str).to_numpy()
        self.adx = self.df['btc_adx'].to_numpy(dtype=float)
        # V18.0.2 kuralı: fiyat EMA200 üstünde ve ani çöküş yok (histerezis yok)
        c, h = btc_df['c'].to_numpy(dtype=float), btc_df['h'].to_numpy(dtype=float)
        tepe = pd.Series(h).rolling(3, min_periods=1).max().to_numpy()
        cokus = dict(zip(btc_df['ts'].to_numpy(dtype='int64') + MUM_15M_MS, (tepe - c) / tepe > 0.015))
        self.btc_ok_eski = ((self.df['btc_ema_uzaklik'].to_numpy(dtype=float) > 0)
                            & ~np.array([bool(cokus.get(int(k), False)) for k in self.kapanis], dtype=bool))

    def _i(self, t_ms):
        return int(np.searchsorted(self.kapanis, t_ms, side='right')) - 1

    def durum(self, t_ms):
        i = self._i(t_ms)
        if i < 0:
            return None
        return bool(self.btc_ok[i]), str(self.rejim[i]), float(self.adx[i])

    def eski_ok(self, t_ms):
        i = self._i(t_ms)
        return None if i < 0 else bool(self.btc_ok_eski[i])

    def rejim_at(self, t_ms):
        i = self._i(t_ms)
        return 'YATAY' if i < 0 else str(self.rejim[i])


# ------------------------------------------------------------------------------------------
# Çıkış motoru: canlı _pozisyonu_isle'nin fiyat yolu üzerinde tekrarı
# ------------------------------------------------------------------------------------------
def rsi_son(kapanislar) -> Optional[float]:
    import pandas_ta as ta
    if len(kapanislar) < 15:
        return None
    r = ta.rsi(pd.Series(kapanislar, dtype=float), length=14)
    if r is None or pd.isna(r.iloc[-1]):
        return None
    return float(r.iloc[-1])


@dataclass
class SimSonucu:
    getiri: float        # ilk maliyete göre net getiri (komisyon + satış kayması dahil)
    bacaklar: list       # [(t_ms, pay, dolum_fiyati, mesaj)]
    durum: str           # KAPANDI | SINIR (--max-saat doldu, son fiyattan kapatıldı) | ACIK (veri bitti)
    max_kar: float

    @property
    def cikis_ms(self):
        return self.bacaklar[-1][0]

    @property
    def son_mesaj(self):
        return self.bacaklar[-1][3]

    @property
    def cikislar(self):
        return ' + '.join(b[3] for b in self.bacaklar)


class CikisMotoru:
    def __init__(self, ayar: risk.RiskAyarlari = RISK_V184, kayma_seviye=0.0015, kayma_zaman=0.0005,
                 max_saat=96.0, rsi_mum=100):
        self.ayar, self.kayma_seviye, self.kayma_zaman = ayar, kayma_seviye, kayma_zaman
        self.max_saat, self.rsi_mum = max_saat, rsi_mum

    def simule_et(self, giris: float, giris_ms: int, atr_pct: float, is_whale: bool, m1, m15,
                  rejim_at: Callable[[int], str]) -> Optional[SimSonucu]:
        """m1: 1m mumlar (ts artan, giriş dakikasını içeren). m15: 15m mumlar (girişten >= rsi_mum önce başlayan).
        Giriş dakikasının açılış/tepe/dibi kısmen giriş ÖNCESİNE ait olduğundan o mumdan yalnız kapanış kullanılır."""
        a = self.ayar
        p = risk.Pozisyon(sembol='', giris=float(giris), giris_zamani=giris_ms / 1000.0,
                          atr_pct=float(atr_pct), is_whale=bool(is_whale))
        m15 = np.asarray(m15, dtype=float).reshape(-1, 6)
        m15_kapanis = m15[:, 0] + MUM_15M_MS
        bacaklar, kalan = [], [1.0]
        # RSI son (canlı) fiyatta monoton artan: aynı kapalı mumlarla, eşiğin altında kaldığı bir fiyattan
        # daha düşük fiyatta tekrar hesaplamaya gerek yok (sonuç birebir aynı, hesap ~10 kat az)
        rsi_red = {'kova': None, 'max_fiyat': float('-inf')}
        # BTC rejimi yalnız 15m mum kapanışlarında değişir: kova başına bir kez sorulur
        rejim_kova = {'kova': None, 'rejim': None}

        def rejim(t_ms):
            kova = t_ms // MUM_15M_MS
            if rejim_kova['kova'] != kova:
                rejim_kova.update(kova=kova, rejim=rejim_at(t_ms))
            return rejim_kova['rejim']

        def kapali(t_ms):
            return int(np.searchsorted(m15_kapanis, t_ms, side='right'))

        def sat(t_ms, pay, fiyat, mesaj, kesisim):
            kayma = self.kayma_seviye if kesisim else self.kayma_zaman
            bacaklar.append((int(t_ms), pay, fiyat * (1 - kayma), mesaj))
            kalan[0] -= pay

        def degerlendir(fiyat, t_ms, kesisim=False):
            """VIP döngüsünün bir turu (ai_bot._pozisyonu_isle). Tamamen kapandıysa True. Yarım satış olursa
            canlıdaki gibi sonraki tur (2 sn sonra) aynı fiyatla hemen tekrarlanır: ör. moon bag'den sonra
            trailing eşiği zaten aşılmışsa kalan yarı da satılır."""
            sonuc = tur(fiyat, t_ms, kesisim)
            if sonuc == 'YARIM':
                sonuc = tur(fiyat, t_ms + 2_000, kesisim)
            return sonuc == 'KAPANDI'

        def tur(fiyat, t_ms, kesisim):
            simdi = t_ms / 1000.0
            sev = risk.seviyeleri_hesapla(p, fiyat, a)
            p.max_kar = max(p.max_kar, sev.max_kar)
            rsi_tetik = False
            if risk.rsi_kontrolu_gerekli(p, sev, simdi, a):
                n = kapali(t_ms)
                if rsi_red['kova'] != n:
                    rsi_red.update(kova=n, max_fiyat=float('-inf'))
                if not (self.rsi_onbellek and fiyat <= rsi_red['max_fiyat']):
                    r = rsi_son(m15[max(0, n - (self.rsi_mum - 1)):n, 4].tolist() + [fiyat])
                    rsi_tetik = r is not None and r >= a.moon_bag_rsi
                    if not rsi_tetik:
                        rsi_red['max_fiyat'] = max(rsi_red['max_fiyat'], fiyat)
                p.son_rsi_kontrol = simdi
            for e in risk.kararlar(p, sev, simdi, rejim(t_ms), a, rsi_tetik):
                if e.tip in (risk.MOON_BAG, risk.KISMI_KAR):
                    sat(t_ms, kalan[0] * e.oran, fiyat, e.mesaj, kesisim)
                    p.half_sold = True
                    return 'YARIM'
                if e.tip == risk.TAM_CIKIS:
                    sat(t_ms, kalan[0], fiyat, e.mesaj, kesisim)
                    return 'KAPANDI'
                if e.tip == risk.ZAMAN_UZAT:
                    p.zaman_ref = simdi
                elif e.tip == risk.MOMENTUM_KONTROL:
                    p.son_mom_kontrol = simdi
                    n = kapali(t_ms)
                    if risk.momentum_oldu_mu(m15[max(0, n - 19):n, 5].tolist()):
                        sat(t_ms, kalan[0], fiyat, e.mesaj, kesisim)
                        return 'KAPANDI'
                return None
            return None

        def esik_fiyati(oran):
            return p.giris * (1 + a.fee_rate) * (1 + oran) / (1 - a.fee_rate)

        def dusen_bacak(fa, ta, fb, tb):
            """fa -> fb (fb < fa) doğrusal düşüşte kısmi/stop eşiklerini tam kesişim fiyatında değerlendirir."""
            cur = fa
            while True:
                sev = risk.seviyeleri_hesapla(p, cur, a)
                esikler = [sev.cikis]
                if sev.max_kar >= sev.ilk_esik and not p.half_sold:
                    esikler.append(sev.kismi)
                adaylar = [f for f in (esik_fiyati(x) for x in esikler) if fb < f < cur]
                if not adaylar:
                    return False
                f = max(adaylar)
                t = int(round(ta + (tb - ta) * (fa - f) / (fa - fb)))
                cur = f * (1 - 1e-12)
                if degerlendir(cur, t, kesisim=True):
                    return True

        sinir_ms = giris_ms + self.max_saat * SAAT_MS
        son = None
        for fiyat, t, surekli in self._noktalar(m1, giris_ms):
            if t >= sinir_ms:
                return self._bitir(p, bacaklar, kalan, son, 'SINIR')
            if self.kesisim_ekle and surekli and fiyat < son[0] and dusen_bacak(son[0], son[1], fiyat, t):
                return self._sonuc(p, bacaklar, 'KAPANDI')
            if degerlendir(fiyat, t):
                return self._sonuc(p, bacaklar, 'KAPANDI')
            son = (fiyat, t)
        return self._bitir(p, bacaklar, kalan, son, 'ACIK')

    kesisim_ekle = True
    rsi_onbellek = True

    @staticmethod
    def _noktalar(m1, giris_ms):
        """(fiyat, t_ms, önceki noktadan sürekli bacak mı). Giriş dakikasından yalnız kapanış."""
        for ts, o, h, l, c, _v in np.asarray(m1, dtype=float).reshape(-1, 6).tolist():
            ts = int(ts)
            if ts + DK_MS <= giris_ms:
                continue
            if ts < giris_ms:
                yield c, max(ts + YOL_OFSET_MS[-1], giris_ms), False
                continue
            yol = (o, l, h, c) if c >= o else (o, h, l, c)
            for j, (f, d) in enumerate(zip(yol, YOL_OFSET_MS)):
                yield f, ts + d, j > 0

    def _bitir(self, p, bacaklar, kalan, son, durum):
        if son is None:
            return None
        if kalan[0] > 1e-12:
            bacaklar.append((int(son[1]), kalan[0], son[0] * (1 - self.kayma_zaman), f'SIM_{durum}'))
        return self._sonuc(p, bacaklar, durum)

    def _sonuc(self, p, bacaklar, durum):
        getiri = sum(pay * risk.net_oran(p.giris, dolum, self.ayar.fee_rate) for _, pay, dolum, _ in bacaklar)
        return SimSonucu(float(getiri), bacaklar, durum, float(p.max_kar))


def simule_parcali(motor: CikisMotoru, depo: MumDeposu, sym, giris, giris_ms, atr_pct, is_whale, m15, rejim_at,
                   parca_dk=1000) -> Optional[SimSonucu]:
    """1m veriyi önce ~16 saatlik tek parça indirir (pozisyonların çoğu bu sürede kapanır); pozisyon hâlâ
    açıksa kalan süre tek seferde indirilip simülasyon baştan (aynı sonuçla) tekrarlanır."""
    bas = giris_ms - 3 * DK_MS
    hedef = int(giris_ms + motor.max_saat * SAAT_MS + DK_MS)
    son = min(bas + parca_dk * DK_MS, hedef)
    while True:
        s = motor.simule_et(giris, giris_ms, atr_pct, is_whale, depo.getir(sym, '1m', bas, son), m15, rejim_at)
        if s is None or s.durum != 'ACIK' or son >= hedef or son >= depo.simdi_ms - DK_MS:
            return s
        son = hedef


# ------------------------------------------------------------------------------------------
# Bütçe kısıtlı yeniden oynatma (botun giriş kurallarıyla)
# ------------------------------------------------------------------------------------------
def oynat(islemler: List[dict], butce: float, mod: str = 'sabit', oran: float = 0.20, kurallar: bool = True):
    """islemler: {sembol, giris_ms, kasa_tipi, bacaklar: [(t_ms, pay, getiri)], son_mesaj} — sıralama önceliği
    listedeki sıradır (aynı anda gelen sinyallerde radar sırası). Dönen: (işlemler, özsermaye eğrisi, atlanan,
    dönem sonunda açık kalan sayısı)."""
    olaylar = []
    for i, x in enumerate(islemler):
        g = int(x['giris_ms'])
        olaylar.append((g, 1, i, -1))
        for j, b in enumerate(x['bacaklar']):
            olaylar.append((max(int(b[0]), g + 1), 0, i, j))
    olaylar.sort()
    nakit = float(butce)
    acik, acik_sembol = {}, {}
    cooldown, kara, stop_sayac = {}, {}, Counter()
    atlanan, alinan, egri = Counter(), [], []

    def ozsermaye():
        return nakit + sum(b * k for b, k, _ in acik.values())

    for t, tur, i, j in olaylar:
        x = islemler[i]
        s = x['sembol']
        if tur == 1:
            if kurallar:
                if s in acik_sembol:
                    atlanan['acik_pozisyon'] += 1
                    continue
                if t < cooldown.get(s, -1):
                    atlanan['cooldown'] += 1
                    continue
                if t < kara.get(s, -1):
                    atlanan['kara_liste'] += 1
                    continue
            balina = x['kasa_tipi'] == 'BALİNA'
            if mod == 'sabit':
                boyut = gs.SABIT_KASA['BALİNA' if balina else 'NORMAL']
            else:
                boyut = ozsermaye() * oran * (2 if balina else 1)
            if boyut < gs.MIN_NOTIONAL or nakit < boyut * 1.01:
                atlanan['bakiye'] += 1
                continue
            nakit -= boyut
            acik[i] = [boyut, 1.0, 0.0]
            acik_sembol[s] = i
        elif i in acik:
            boyut, kalan, kar = acik[i]
            pay, getiri = x['bacaklar'][j][1], x['bacaklar'][j][2]
            nakit += boyut * pay * (1 + getiri)
            kar += boyut * pay * getiri
            kalan -= pay
            if j == len(x['bacaklar']) - 1 or kalan <= 1e-9:
                del acik[i]
                acik_sembol.pop(s, None)
                cooldown[s] = t + COOLDOWN_MS
                mesaj = x.get('son_mesaj') or ''
                if 'STOP' in mesaj:
                    stop_sayac[s] += 1
                    if stop_sayac[s] >= 2:
                        kara[s], stop_sayac[s] = t + KARA_LISTE_MS, 0
                else:                  # ai_bot._tam_cikis: stop dışı her çıkış "peş peşe" sayacını sıfırlar
                    stop_sayac[s] = 0
                alinan.append({'sembol': s, 'giris_ms': int(x['giris_ms']), 'cikis_ms': t, 'boyut': boyut,
                               'kar': kar, 'getiri': kar / boyut})
            else:
                acik[i] = [boyut, kalan, kar]
            egri.append((t, ozsermaye()))
    df = pd.DataFrame(alinan, columns=['sembol', 'giris_ms', 'cikis_ms', 'boyut', 'kar', 'getiri'])
    seri = pd.Series([v for _, v in egri], index=pd.to_datetime([t for t, _ in egri], unit='ms'), dtype=float)
    return df, seri, atlanan, len(acik)


def butce_ozeti(islemler, butce, mod, oran, bas_ms, bit_ms):
    """Özsermaye gerçekleşmiş nakit + açık pozisyonların MALİYETİ üzerinden (açık pozisyonun anlık zararı
    düşüşe yansımaz). 30 günlük pencereler en geç --bitis gününde biter (sonrasında yeni giriş yok)."""
    df, egri, atlanan, acik = oynat(islemler, butce, mod, oran)
    sim_son = sum(1 for x in islemler if str(x.get('son_mesaj', '')).startswith('SIM_'))
    son = float(egri.iloc[-1]) if len(egri) else float(butce)
    ilk30 = float(df.loc[df['cikis_ms'] < bas_ms + 30 * GUN_MS, 'kar'].sum()) if len(df) else 0.0
    if len(df):
        gunluk = df.assign(g=pd.to_datetime(df['cikis_ms'], unit='ms').dt.floor('D')).groupby('g')['kar'].sum()
        gunler = pd.date_range(pd.to_datetime(bas_ms, unit='ms').floor('D'),
                               pd.to_datetime(bit_ms, unit='ms').floor('D'), freq='D')
        p30 = gunluk.reindex(gunler, fill_value=0.0).rolling(30).sum().dropna()
        aylik = df.assign(ay=pd.to_datetime(df['cikis_ms'], unit='ms').dt.strftime('%Y-%m')).groupby('ay').agg(
            islem=('kar', 'size'), kar_usdt=('kar', 'sum'), kazanma=('kar', lambda s: float((s > 0).mean())))
    else:
        p30, aylik = pd.Series(dtype=float), pd.DataFrame()
    return {'butce': butce, 'mod': mod, 'islem': int(len(df)), 'atlanan': dict(atlanan), 'acik_kalan': acik,
            'sim_sonu_kapatilan': sim_son,
            'toplam_kar': son - butce, 'getiri_pct': (son / butce - 1) * 100,
            'max_dusus_pct': gs.max_dusus(egri, butce) * 100 if len(egri) else 0.0,
            'ilk_30_gun_kar': ilk30,
            'p30_medyan': float(p30.median()) if len(p30) else None,
            'p30_p10': float(p30.quantile(0.1)) if len(p30) else None,
            'p30_p90': float(p30.quantile(0.9)) if len(p30) else None,
            'aylik': {k: {kk: float(vv) for kk, vv in v.items()} for k, v in aylik.to_dict('index').items()}}


# ------------------------------------------------------------------------------------------
# AI
# ------------------------------------------------------------------------------------------
def ai_yukle(yol, log=print):
    """(ModelYuvasi, eğitim verisinin son zamanı ms | None). Kartlı modelde eğitim dönemi karttan okunur:
    AI senaryoları yalnız bu tarihten SONRA açılan pozisyonlara uygulanır (aksi hâlde model ezberlediği
    sinyalleri skorlar). Kartsız (eski) modelin dönemi bilinmez; mevcut core_xgboost_model.json için adli
    analiz: ilk 68 işlem, 31 Mayıs - 15 Haziran (ANALIZ_V18.4.md §10.2)."""
    if not yol or yol == 'yok' or not os.path.exists(yol):
        return None, None
    from sniper.model_karti import ModelYuvasi
    yuva = ModelYuvasi(yol, 'core', 0.65, 'min')
    mesaj = yuva.yenile()
    if not yuva.yuklu:
        log(f"⚠️ AI modeli yüklenemedi, AI senaryosu atlanıyor: {mesaj}")
        return None, None
    donem = ((yuva.kart or {}).get('veri') or {}).get('donem')
    egitim_son = _ms(donem[1]) if donem else None
    if egitim_son:
        log(f"AI modeli {yuva.surum}: eğitim verisi {donem[0]} → {donem[1]}; AI senaryoları yalnız bu tarihten sonrası")
    else:
        log(f"AI modeli kartsız (eski): eğitim dönemi bilinmiyor; {len(yuva.featurelar)} feature {yuva.featurelar}")
    return yuva, egitim_son


def ai_uygun_mu(yuva, satirlar: List[dict]) -> Optional[str]:
    """Modelin feature'larından biri bu kaynakta hiç üretilemiyorsa (ör. Ağustos kayıtlarında V2 feature'ları yok)
    skor canlıdakiyle aynı olmaz: sebep metni döner, uygunsa None."""
    if yuva is None or not satirlar:
        return 'model yok' if yuva is None else 'satır yok'
    X = ozellik_matrisi(pd.DataFrame(satirlar), yuva.featurelar)
    eksik = [f for f in yuva.featurelar if X[f].isna().all()]
    return f"bu kaynakta üretilemeyen feature(lar): {eksik}" if eksik else None


def ai_skoru(yuva, kanonik: dict) -> Optional[float]:
    """Botla aynı feature hattı (ozellik_matrisi); kanonik = CSV kolon adlarıyla satır."""
    try:
        return float(yuva.model.predict_proba(ozellik_matrisi(pd.DataFrame([kanonik]), yuva.featurelar))[0][1])
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------------------------------
# Kaynak 1: gerçek girişler
# ------------------------------------------------------------------------------------------
def dosyalari_bul(verilen):
    yollar = list(verilen) if verilen else sorted({p for d in VARSAYILAN_DESENLER
                                                   for p in glob.glob(os.path.join(KOK, d))})
    uygun = []
    for y in yollar:
        with open(y, 'rb') as f:
            if f.read(64).lstrip().startswith(b'{'):
                print(f"⚠️ {os.path.basename(y)} CSV değil (JSON içeriyor), atlandı")
                continue
        uygun.append(y)
    return uygun


def giris_bul(depo: MumDeposu, sym, tahmini_ms, fiyat, tol=0.001, max_uzatma=6):
    """Gerçek giriş anını dolum fiyatından doğrular. Dönen (giris_ms, uzatma_k) veya None.
    k: eski sürümün süre uzatmasında sıfırladığı giriş zamanı için geri kaydırma (k x 4 saat).
    Sunucu saati UTC (shadow CSV'de Sinyal_Zamani - Ts = 0); saat dilimi tahmini yapılmaz, çünkü
    tek tek pozisyonlarda denemek yalnız yanlış eşleşme üretir (gerekirse --saat-farki hepsine uygulanır)."""
    for k in range(max_uzatma + 1):
        t = tahmini_ms - k * UZATMA_MS
        m = depo.getir(sym, '1m', t - 3 * DK_MS, t + 2 * DK_MS)
        if len(m) and np.any((m[:, 3] * (1 - tol) <= fiyat) & (fiyat <= m[:, 2] * (1 + tol))):
            return t, k
    return None


def cikis_ailesi(mesaj: str) -> str:
    m = str(mesaj or '')
    for anahtar, aile in [('STOP', 'STOP'), ('TREND', 'TREND'), ('KISMİ', 'KISMI'), ('MOON', 'MOON'),
                          ('ZAMAN', 'ZAMAN'), ('KİLİDİ', 'KILIT'), ('BAŞA BAŞ', 'KILIT'), ('GÜVENLİ', 'KILIT'),
                          ('MOMENTUM', 'MOMENTUM'), ('SIM_', 'SIM_SON')]:
        if anahtar in m:
            return aile
    return 'DIGER'


def gercek_kaynak(depo, btc: BtcBaglami, poz: pd.DataFrame, motorlar: Dict[str, CikisMotoru], yuva, log=print,
                  saat_farki=0.0):
    satirlar = []
    kaydir = int(round(saat_farki * SAAT_MS))
    for n, (_, r) in enumerate(poz.iterrows(), 1):
        sym, fiyat, tahmini = r['sembol'], float(r['giris_fiyat']), _ms(r['giris']) + kaydir
        k = {'sembol': sym, 'giris_csv': str(r['giris']), 'kasa_tipi': r['kasa_tipi'], 'atr_pct': float(r['atr_pct']),
             'rsi': float(r['rsi']), 'vol_oran': float(r['vol_oran']), 'giris_fiyat': fiyat,
             'gercek_getiri': float(r['getiri']), 'gercek_cikislar': r['cikislar'], 'veri': 'VAR',
             '_gercek_bacaklar': [(_ms(t) + kaydir, pay, g, mesaj) for t, pay, g, mesaj in r['bacaklar']]}
        try:
            bulunan = giris_bul(depo, sym, tahmini, fiyat)
            giris_ms, uz = bulunan if bulunan else (tahmini, None)
            k.update(giris_ms=giris_ms, giris=_tarih(giris_ms), uzatma_k=uz, giris_dogrulandi=bulunan is not None)
            m15 = depo.getir(sym, '15m', giris_ms - 101 * MUM_15M_MS,
                             giris_ms + max(m.max_saat for m in motorlar.values()) * SAAT_MS + MUM_15M_MS)
            for ad, motor in motorlar.items():
                s = simule_parcali(motor, depo, sym, fiyat, giris_ms, k['atr_pct'], r['kasa_tipi'] == 'BALİNA',
                                   m15, btc.rejim_at)
                if s is None:
                    raise VeriYok(f"{sym} 1m verisi yok")
                k.update({f'{ad}_getiri': s.getiri, f'{ad}_cikislar': s.cikislar, f'{ad}_durum': s.durum,
                          f'{ad}_sure_saat': (s.cikis_ms - giris_ms) / SAAT_MS, f'_{ad}_bacaklar': s.bacaklar})
        except VeriYok as e:
            k['veri'] = f'YOK: {e}'
            satirlar.append(k)
            continue
        d = btc.durum(giris_ms)
        k['btc_ok'], k['rejim'], k['btc_adx'] = d if d else (None, None, None)
        k['btc_ok_eski'] = btc.eski_ok(giris_ms)
        k['ai_skor'] = ai_skoru(yuva, {'Giris_RSI': k['rsi'], 'Giris_Vol_Oran': k['vol_oran'],
                                       'Giris_ATR_Pct': k['atr_pct'], 'Sinyal': r['sinyal']}) if yuva else None
        satirlar.append(k)
        if n % 25 == 0 or n == len(poz):
            log(f"  {n}/{len(poz)} pozisyon simüle edildi ({depo.istek} API isteği)")
    return pd.DataFrame(satirlar)


def kullanilabilir(tablo: pd.DataFrame) -> pd.DataFrame:
    """Fiyat verisi olan ve giriş zamanı dolum fiyatıyla doğrulanan pozisyonlar (tüm senaryolar AYNI küme)."""
    if tablo.empty or 'giris_ms' not in tablo.columns or 'giris_dogrulandi' not in tablo.columns:
        return tablo.iloc[0:0]
    return tablo[(tablo['veri'] == 'VAR') & (tablo['giris_dogrulandi'].fillna(False).astype(bool))]


def _dogru_mu(x) -> bool:
    return x is not None and pd.notna(x) and bool(x)


def gercek_senaryolari(tablo: pd.DataFrame, yuva, ai_bas_ms=None) -> Dict[str, List[dict]]:
    """Karşılaştırılabilirlik için tüm senaryolar AYNI pozisyon kümesinden türetilir. ai_bas_ms: AI modelinin
    eğitim verisi bitişi; V184_AI yalnız bundan sonra açılanları içerir (örneklem içi skorlama olmasın).
    V1802'nin AI filtresi yalnız kartsız (Ağustos'ta kullanılan eski) modelle uygulanır."""
    sen = {ad: [] for ad in ['GERCEK', 'ESKI_SIM', 'V1802', 'V184_CIKIS', 'V184', 'V184_AI']}
    eski_ai = yuva if (yuva is not None and not getattr(yuva, 'kart', None)) else None
    if yuva is None:
        sen.pop('V184_AI')
    t = kullanilabilir(tablo)
    for _, r in t.sort_values('giris_ms').iterrows() if len(t) else []:
        ortak = {'sembol': r['sembol'], 'giris_ms': int(r['giris_ms']), 'kasa_tipi': r['kasa_tipi']}
        g = r['_gercek_bacaklar']
        sen['GERCEK'].append({**ortak, 'bacaklar': [(b[0], b[1], b[2]) for b in g], 'son_mesaj': g[-1][3]})
        for ad in ('ESKI_SIM', 'V184'):
            b = r[f'_{ad}_bacaklar']
            islem = {**ortak, 'bacaklar': [(x[0], x[1], risk.net_oran(r['giris_fiyat'], x[2], RISK_V184.fee_rate))
                                           for x in b], 'son_mesaj': b[-1][3]}
            if ad == 'ESKI_SIM':
                sen['ESKI_SIM'].append(islem)
                if (_dogru_mu(r.get('btc_ok_eski')) and r['atr_pct'] <= MAX_ATR_V1802
                        and (eski_ai is None or (pd.notna(r['ai_skor']) and not eski_ai.blokla_mi(float(r['ai_skor']))))):
                    sen['V1802'].append(islem)
                continue
            sen['V184_CIKIS'].append(islem)
            if _dogru_mu(r['btc_ok']) and r['rejim'] == 'TREND' and r['atr_pct'] <= MAX_ATR_V184:
                sen['V184'].append(islem)
                if (yuva is not None and pd.notna(r['ai_skor']) and not yuva.blokla_mi(float(r['ai_skor']))
                        and (ai_bas_ms is None or r['giris_ms'] > ai_bas_ms)):
                    sen['V184_AI'].append(islem)
    return sen


# ------------------------------------------------------------------------------------------
# Kaynak 2: backfill sinyalleri
# ------------------------------------------------------------------------------------------
def backfill_kaynak(depo, ex, btc: BtcBaglami, bas_ms, bit_ms, evren_n, motor: CikisMotoru, yuva,
                    semboller=None, log=print, motor_eski: Optional[CikisMotoru] = None, radar_mum: int = 96):
    """Sinyaller Ağustos kapsamında üretilir (ATR <= %4, BTC durumundan bağımsız); senaryo filtreleri
    backfill_senaryolari'nda uygulanır. V18.4 çıkışı V18.4 BTC onayı olan sinyallere, V18.0.2 çıkışı
    (motor_eski) Ağustos botunun gireceği sinyallere (eski BTC kuralı + eski AI) simüle edilir."""
    semboller = semboller or bf.evren_sec(ex, evren_n)
    veri_bas = bas_ms - bf.ISINMA_MUM * MUM_15M_MS
    seriler15 = {}
    for n, s in enumerate(semboller, 1):
        try:
            seriler15[s] = depo.getir_df(s, '15m', veri_bas, bit_ms + int(motor.max_saat * SAAT_MS) + MUM_15M_MS)
        except VeriYok:
            continue
        depo.bosalt('15m', s)   # DataFrame'e kopyalandı; depodaki ikinci kopya uzun dönemde yüzlerce MB'a çıkıyordu
        if n % 50 == 0:
            log(f"  15m veri: {n}/{len(semboller)} sembol ({depo.istek} API isteği)")
    seriler15 = {s: d for s, d in seriler15.items() if len(d) > bf.PENCERE_15M}
    # bit_ms sonrası satırlar (--bitis geçmişteyse çıkış simülasyonu için) önceki anların sırasını değiştirmez:
    # değişim/hacim geriye dönük, sıralama satır içi. Sinyaller zaten yalnız bit_ms'e kadar üretilir.
    ilk_n, genislik = bf.radar_paneli(seriler15, pencere=radar_mum)
    ayar = SinyalAyarlari(max_atr_pct=max(MAX_ATR_V184, MAX_ATR_V1802) / 100)
    sinyaller = []
    for s, df15 in seriler15.items():
        df15_sinyal = df15[df15['ts'] + MUM_15M_MS <= bit_ms].reset_index(drop=True)
        try:
            df1h = depo.getir_df(s, '1h', veri_bas - bf.PENCERE_1H * SAAT_MS, bit_ms + SAAT_MS)
        except VeriYok:
            continue
        for x in bf.sinyalleri_uret(s, df15_sinyal, df1h, btc.df, ilk_n, genislik, bas_ms, True, ayar):
            i = x.pop('_i')
            x['degisim_24s'] = float(df15_sinyal['c'].iloc[i] / df15_sinyal['c'].iloc[i - 96] - 1) if i >= 96 else 0.0
            sinyaller.append(x)
        depo.bosalt('1h')
    del ilk_n, genislik
    v184 = [x for x in sinyaller if x.get('BTC_OK') and depo_etiket.sayi(x.get('Giris_ATR_Pct'), 99.0) <= MAX_ATR_V184]
    log(f"  {len(sinyaller)} sinyal üretildi (ATR <= %{MAX_ATR_V1802:.0f}, BTC durumundan bağımsız); V18.4 kuralına "
        f"uyan {len(v184)} ({sum(x['Rejim'] == 'TREND' for x in v184)} TREND rejiminde); çıkışlar simüle ediliyor...")
    if yuva is not None:
        neden = ai_uygun_mu(yuva, sinyaller[:2000])
        if neden:
            log(f"⚠️ Backfill AI senaryosu atlandı: {neden}")
            yuva = None
    # Ağustos botunun AI'ı yalnız kartsız (Ağustos'ta kullanılan eski) modeldir; yeni kartlı model uygulanmaz.
    eski_ai = yuva if (yuva is not None and not getattr(yuva, 'kart', None)) else None
    satirlar = []
    for n, x in enumerate(sorted(sinyaller, key=lambda z: (z['Ts'], -z['degisim_24s'])), 1):
        if n % 200 == 0:
            log(f"  {n}/{len(sinyaller)} sinyal ({depo.istek} API isteği)")
        if n % 50 == 0:
            depo.bosalt('1m')
        sym, ts = x['Sembol'], int(x['Ts'])
        ai_skor = ai_skoru(yuva, {k: x.get(k) for k in x}) if yuva else None
        btc_ok, btc_ok_eski = bool(x.get('BTC_OK')), bool(btc.eski_ok(ts))
        eski_ai_gecer = eski_ai is None or (ai_skor is not None and not eski_ai.blokla_mi(ai_skor))
        gerek = {'V184': (motor, btc_ok),
                 'ESKI': (motor_eski, motor_eski is not None and btc_ok_eski and eski_ai_gecer)}
        if not any(g for _, g in gerek.values()):
            continue
        giris = float(x['Fiyat']) * (1 + ALIM_KAYMASI)
        balina = depo_etiket.is_whale_tahmini(x)
        d15 = seriler15[sym]
        m15 = d15[(d15['ts'] >= ts - 101 * MUM_15M_MS)
                  & (d15['ts'] <= ts + int(motor.max_saat * SAAT_MS) + MUM_15M_MS)].to_numpy(dtype=float)
        satir = {'sembol': sym, 'giris_ms': ts, 'giris': _tarih(ts), 'giris_fiyat': giris,
                 'kasa_tipi': 'BALİNA' if balina else 'NORMAL', 'rejim': x['Rejim'], 'btc_ok': btc_ok,
                 'btc_ok_eski': btc_ok_eski, 'atr_pct': x.get('Giris_ATR_Pct'), 'rsi': x.get('Giris_RSI'),
                 'vol_oran': x.get('Giris_Vol_Oran'), 'degisim_24s': x['degisim_24s'], 'ai_skor': ai_skor}
        simule = False
        for ad, (m, g) in gerek.items():
            if not g:
                continue
            try:
                s = simule_parcali(m, depo, sym, giris, ts, depo_etiket.sayi(x.get('Giris_ATR_Pct'), 2.5), balina,
                                   m15, btc.rejim_at)
            except VeriYok:
                s = None
            if s is None:
                continue
            simule = True
            satir.update({f'{ad}_getiri': s.getiri, f'{ad}_cikislar': s.cikislar, f'{ad}_durum': s.durum,
                          f'{ad}_sure_saat': (s.cikis_ms - ts) / SAAT_MS, f'_{ad}_bacaklar': s.bacaklar})
        if simule:
            satirlar.append(satir)
    return pd.DataFrame(satirlar), yuva


def backfill_senaryolari(tablo: pd.DataFrame, yuva, ai_bas_ms=None) -> Dict[str, List[dict]]:
    """Bütün backfill senaryoları AYNI sinyal havuzunu süzer (bkz. modül açıklaması). V18.4 senaryoları
    V18.4 çıkışını, B_V1802 V18.0.2 çıkışını kullanır. B_V1802'nin AI'ı yalnız kartsız eski modeldir."""
    sen = {ad: [] for ad in B_SENARYOLAR}
    if yuva is None:
        for ad in B_AI_SENARYOLARI:
            sen.pop(ad)
    eski_ai = yuva if (yuva is not None and not getattr(yuva, 'kart', None)) else None
    if tablo.empty:
        return sen

    def islem(r, bacaklar):
        return {'sembol': r['sembol'], 'giris_ms': int(r['giris_ms']), 'kasa_tipi': r['kasa_tipi'],
                'bacaklar': [(x[0], x[1], risk.net_oran(r['giris_fiyat'], x[2], RISK_V184.fee_rate)) for x in bacaklar],
                'son_mesaj': bacaklar[-1][3]}

    for _, r in tablo.sort_values(['giris_ms', 'degisim_24s'], ascending=[True, False]).iterrows():
        skor = r.get('ai_skor')
        skor = float(skor) if skor is not None and pd.notna(skor) else None
        b = r.get('_V184_bacaklar')
        if isinstance(b, list) and b and _dogru_mu(r.get('btc_ok')):
            x = islem(r, b)
            atr3, trend = r['atr_pct'] <= MAX_ATR_V184, r['rejim'] == 'TREND'
            ai = (yuva is not None and skor is not None and not yuva.blokla_mi(skor)
                  and (ai_bas_ms is None or r['giris_ms'] > ai_bas_ms))
            if atr3:
                sen['B_REJIMSIZ'].append(x)
                if trend:
                    sen['B_V184'].append(x)
            if ai:
                if atr3 and trend:
                    sen['B_V184_AI'].append(x)
                if trend and r['atr_pct'] <= MAX_ATR_V1802:
                    sen['B_AI_ATR4'].append(x)
                if atr3:
                    sen['B_AI_REJIMSIZ'].append(x)
        e = r.get('_ESKI_bacaklar')
        if (isinstance(e, list) and e and _dogru_mu(r.get('btc_ok_eski')) and r['atr_pct'] <= MAX_ATR_V1802
                and (eski_ai is None or (skor is not None and not eski_ai.blokla_mi(skor)))):
            sen['B_V1802'].append(islem(r, e))
    return sen


def _islem_getirisi(islem) -> float:
    return float(sum(p * g for _, p, g in islem['bacaklar']))


def _gun_bootstrap(seriler, n=2000, tohum=0):
    """Gün bloklu bootstrap: aynı gündeki sinyaller (aynı piyasa hareketi) birlikte yeniden örneklenir.
    seriler: ad -> (getiriler, gün numaraları). Bütün adlar AYNI gün örnekleriyle: farkların GA'sı doğrudan."""
    dolu = [g for _, g in seriler.values() if len(g)]
    if not dolu:
        return {}
    gunler = np.unique(np.concatenate(dolu))
    rng = np.random.default_rng(tohum)
    w = rng.multinomial(len(gunler), np.full(len(gunler), 1.0 / len(gunler)), size=n).astype(float)
    sonuc = {}
    for ad, (v, g) in seriler.items():
        yer = np.searchsorted(gunler, g)
        top = np.bincount(yer, weights=v, minlength=len(gunler))
        say = np.bincount(yer, minlength=len(gunler)).astype(float)
        with np.errstate(invalid='ignore', divide='ignore'):
            sonuc[ad] = (w @ top) / (w @ say)
    return sonuc


def _orneklem_ga(ornek):
    ornek = np.asarray(ornek, dtype=float)
    ornek = ornek[np.isfinite(ornek)]
    if len(ornek) < 100:
        return [None, None]
    return [float(np.quantile(ornek, 0.025)), float(np.quantile(ornek, 0.975))]


def backfill_karsilastirma(senaryolar) -> dict:
    """Backfill senaryolarının sinyal başı sonucu (bütçe kuralları olmadan, her sinyal tek başına) ve canlıdaki
    kuruluma (B_V184_AI) göre: ATR %3-4 ve yatay rejim sinyallerinin kendi ortalaması, her senaryonun farkı."""
    b = {ad: v for ad, v in senaryolar.items() if ad.startswith('B_') and v}
    if not b:
        return {}

    def seri(islemler):
        return (np.array([_islem_getirisi(x) for x in islemler]), np.array([x['giris_ms'] // GUN_MS for x in islemler]))

    seriler = {ad: seri(v) for ad, v in b.items()}
    eklenen = {}
    taban = b.get('B_V184_AI')
    if taban:
        t = {(x['sembol'], x['giris_ms']) for x in taban}
        for ad, etiket in (('B_AI_ATR4', 'atr_3_4'), ('B_AI_REJIMSIZ', 'yatay_rejim')):
            fazla = [x for x in b.get(ad, []) if (x['sembol'], x['giris_ms']) not in t]
            if fazla:
                seriler[etiket], eklenen[etiket] = seri(fazla), ad
    ornek = _gun_bootstrap(seriler)
    ozet = {'senaryolar': {}, 'eklenen': {}, 'fark': {}}
    for ad, (v, _g) in seriler.items():
        kayit = {'n': int(len(v)), 'ort': float(v.mean()), 'kazanan': float((v > 0).mean()),
                 'ga': _orneklem_ga(ornek[ad])}
        (ozet['eklenen'] if ad in eklenen else ozet['senaryolar'])[ad] = kayit
    if taban:
        for ad in b:
            if ad != 'B_V184_AI':
                ozet['fark'][ad] = {'ort': float(seriler[ad][0].mean() - seriler['B_V184_AI'][0].mean()),
                                    'ga': _orneklem_ga(ornek[ad] - ornek['B_V184_AI'])}
    return ozet


# ------------------------------------------------------------------------------------------
# Rapor
# ------------------------------------------------------------------------------------------
def _pct(x):
    return 'yok' if x is None or (isinstance(x, float) and np.isnan(x)) else f"%{x * 100:+.2f}"


def _bootstrap_ga(fark, n=4000, tohum=0):
    fark = np.asarray(fark, dtype=float)
    if len(fark) < 5:
        return None, None
    rng = np.random.default_rng(tohum)
    ort = rng.choice(fark, size=(n, len(fark)), replace=True).mean(axis=1)
    return float(np.quantile(ort, 0.025)), float(np.quantile(ort, 0.975))


def dogrulama_ozeti(tablo: pd.DataFrame) -> dict:
    t = kullanilabilir(tablo)
    if t.empty:
        return {}
    t = t[pd.to_datetime(t['giris_ms'], unit='ms') < pd.Timestamp(ESKI_SURUM_BITIS)]
    if t.empty:
        return {}
    g, s = t['gercek_getiri'].to_numpy(), t['ESKI_SIM_getiri'].to_numpy()
    ga = t['gercek_cikislar'].str.split(' + ', regex=False).str[-1].map(cikis_ailesi)
    sa = t['ESKI_SIM_cikislar'].str.split(' + ', regex=False).str[-1].map(cikis_ailesi)
    aile = pd.DataFrame({'aile': ga, 'fark': s - g}).groupby('aile')['fark'].agg(['size', 'mean'])
    return {'n': int(len(t)), 'gercek_ort': float(g.mean()), 'sim_ort': float(s.mean()),
            'mutlak_fark_ort': float(np.abs(s - g).mean()),
            'korelasyon': float(np.corrcoef(g, s)[0, 1]) if len(t) > 2 else None,
            'cikis_ailesi_uyumu': float((ga.to_numpy() == sa.to_numpy()).mean()),
            'aileye_gore_sapma': {k: {'n': int(v['size']), 'ort_fark': float(v['mean'])} for k, v in aile.iterrows()}}


def filtre_ozeti(tablo: pd.DataFrame, yuva, ai_bas_ms=None) -> dict:
    t = kullanilabilir(tablo)
    if t.empty:
        return {}
    kosullar = {'btc_onayi_yok': (t['btc_ok'] != True, t), 'yatay_rejim': (t['rejim'] != 'TREND', t),  # noqa: E712
                'atr_3_ustu': (t['atr_pct'] > MAX_ATR_V184, t)}
    if yuva is not None:
        ta = t[t['giris_ms'] > ai_bas_ms] if ai_bas_ms else t
        kosullar['ai_veto'] = (ta['ai_skor'].map(lambda x: bool(pd.isna(x) or yuva.blokla_mi(float(x)))), ta)
    sonuc = {}
    for ad, (m, x) in kosullar.items():
        m = m.astype(bool)
        sonuc[ad] = {'n': int(len(x)), 'engellenen': int(m.sum()),
                     'engellenenin_gercek_ort': float(x.loc[m, 'gercek_getiri'].mean()) if m.any() else None,
                     'gecenin_gercek_ort': float(x.loc[~m, 'gercek_getiri'].mean()) if (~m).any() else None}
    return sonuc


def cikis_etkisi(tablo: pd.DataFrame) -> dict:
    t = kullanilabilir(tablo)
    if t.empty:
        return {}
    sonuc = {}
    donemler = [('tum', None, None), ('05Ağu-15Eyl', '2026-08-05', '2026-09-16'), ('16Eyl-20Eyl', '2026-09-16', '2026-09-21'),
                ('21Eyl+', '2026-09-21', None)]
    zaman = pd.to_datetime(t['giris_ms'], unit='ms')
    for ad, a, b in donemler:
        m = pd.Series(True, index=t.index)
        if a:
            m &= zaman >= pd.Timestamp(a)
        if b:
            m &= zaman < pd.Timestamp(b)
        x = t[m]
        if len(x) < 3:
            continue
        # Aynı simülatörle iki çıkış mantığı: simülatörün kendi hatası farkta büyük ölçüde sadeleşir
        esli = (x['V184_getiri'] - x['ESKI_SIM_getiri']).to_numpy()
        sonuc[ad] = {'n': int(len(x)), 'gercek_ort': float(x['gercek_getiri'].mean()),
                     'eski_sim_ort': float(x['ESKI_SIM_getiri'].mean()), 'v184_ort': float(x['V184_getiri'].mean()),
                     'v184_eski_sim_farki': float(esli.mean()), 'v184_eski_sim_ga95': list(_bootstrap_ga(esli)),
                     'v184_gercek_farki': float((x['V184_getiri'] - x['gercek_getiri']).mean())}
    return sonuc


ANA_SENARYO_SIRASI = ('B_V184_AI', 'V184_AI', 'B_V184', 'V184')   # ay ay tablonun ayrıntılı gösterdiği senaryo


def aylik_bolum(butce_tablosu) -> List[str]:
    """Rapora ay ay tablo: ana senaryo (canlı kurulum) her bütçe için ay sonu bakiyesiyle, altında tüm senaryoların
    aylık kârı. Sabit kasa tercih edilir (canlı bot sabit tutarla işlem açar). Kâr, işlemin KAPANDIĞI aya yazılır."""
    if not butce_tablosu:
        return []
    modlar = [r['mod'] for r in butce_tablosu]
    mod = 'sabit' if 'sabit' in modlar else modlar[0]
    satirlar = [r for r in butce_tablosu if r['mod'] == mod]
    aylar = sorted({ay for r in satirlar for ay in (r.get('aylik') or {})})
    if not aylar:
        return []
    aylar = [str(p) for p in pd.period_range(aylar[0], aylar[-1], freq='M')]   # işlemsiz aylar da görünsün
    adlar = list(dict.fromkeys(r['senaryo'] for r in satirlar))
    ana = next((a for a in ANA_SENARYO_SIRASI if a in adlar), adlar[0])
    bos = {'islem': 0, 'kar_usdt': 0.0, 'kazanma': 0.0}
    y = ['', f"6) AY AY SONUÇ ({'sabit kasa: işlem başına 20 USDT, balina 40' if mod == 'sabit' else mod + ' kasa'}; "
             f"kâr, işlemin kapandığı aya yazılır)"]
    for r in sorted((r for r in satirlar if r['senaryo'] == ana), key=lambda r: -r['butce']):
        y += [f"   {ana}: {SENARYO_ACIKLAMA.get(ana, '')}; {r['butce']:.0f} USDT ile başlasaydı:",
              f"   {'ay':8s} {'işlem':>5s} {'kâr USDT':>9s} {'kazanan':>8s} {'ay sonu bakiye':>15s}"]
        bakiye, kazanan = r['butce'], 0.0
        for ay in aylar:
            v = r['aylik'].get(ay, bos)
            bakiye += v['kar_usdt']
            kazanan += v['kazanma'] * v['islem']
            oran = f"{v['kazanma'] * 100:7.0f}%" if v['islem'] else '       -'
            y.append(f"   {ay:8s} {int(v['islem']):5d} {v['kar_usdt']:+9.2f} {oran} {bakiye:15.2f}")
        y.append(f"   {'TOPLAM':8s} {r['islem']:5d} {r['toplam_kar']:+9.2f} "
                 f"{(kazanan / r['islem'] * 100 if r['islem'] else 0):7.0f}% {r['butce'] + r['toplam_kar']:15.2f}"
                 f"   (getiri {r['getiri_pct']:+.1f}%, en büyük düşüş {r['max_dusus_pct']:.1f}%)")
    butce = max(r['butce'] for r in satirlar)
    tablo = {r['senaryo']: r for r in satirlar if r['butce'] == butce}
    y += [f"   Tüm senaryolar, aylık kâr (USDT; {butce:.0f} USDT bütçe):",
          f"   {'ay':8s} " + ' '.join(f"{ad:>13s}" for ad in tablo)]
    for ay in aylar:
        y.append(f"   {ay:8s} " + ' '.join(f"{r['aylik'].get(ay, bos)['kar_usdt']:+13.2f}" for r in tablo.values()))
    y += [f"   {'TOPLAM':8s} " + ' '.join(f"{r['toplam_kar']:+13.2f}" for r in tablo.values()),
          f"   {'işlem':8s} " + ' '.join(f"{r['islem']:13d}" for r in tablo.values()),
          f"   {'maxDD':8s} " + ' '.join(f"{r['max_dusus_pct']:12.1f}%" for r in tablo.values())]
    return y


def rapor_metni(meta, dogrulama, filtreler, etki, butce_tablosu, backfill=None) -> str:
    y = [f"V18.4 GEÇMİŞ SİMÜLASYONU | {meta['baslangic']} → {meta['bitis']} | {meta['olusturma']}",
         f"Ayarlar: kayma seviye %{meta['kayma_seviye'] * 100:.2f} / zaman %{meta['kayma_zaman'] * 100:.2f} | "
         f"max süre {meta['max_saat']:.0f} saat | AI: {meta['ai'] or 'yok'} | API isteği: {meta['istek']}"]
    y += [f"NOT: {n}" for n in meta.get('notlar', [])]
    if 'gercek' in meta:
        g = meta['gercek']
        y += ['', f"GERÇEK GİRİŞLER: {g['pozisyon']} pozisyon | fiyat verisi yok: {g['veri_yok']} | giriş zamanı "
                  f"doğrulanan: {g['dogrulanan']} (süre uzatması düzeltilen: {g['uzatma_duzeltilen']}) | "
                  f"doğrulanamayan (senaryolara alınmadı): {g['dogrulanamayan']}"]
    if dogrulama:
        d = dogrulama
        y += ['', f"1) SİMÜLATÖR DOĞRULAMASI — {ESKI_SURUM_BITIS} öncesi girişler (V18.0.2 dönemi): V18.0.2 çıkış "
                  f"mantığıyla simülasyon vs gerçekleşen",
              f"   n={d['n']} | ort. net getiri: gerçek {_pct(d['gercek_ort'])}, simülasyon {_pct(d['sim_ort'])} | "
              f"pozisyon başına ort. mutlak fark {_pct(d['mutlak_fark_ort'])} | korelasyon "
              f"{(d['korelasyon'] if d['korelasyon'] is not None else float('nan')):.2f} | "
              f"son çıkış tipi uyumu %{d['cikis_ailesi_uyumu'] * 100:.0f}",
              "   gerçek çıkış tipine göre ort. sapma (sim - gerçek): " +
              ', '.join(f"{k} {_pct(v['ort_fark'])} (n={v['n']})" for k, v in d['aileye_gore_sapma'].items())]
    if filtreler:
        y += ['', "2) V18.4 GİRİŞ FİLTRELERİ gerçek girişlere uygulansaydı (engellenenlerin GERÇEKLEŞEN ort. getirisi)"]
        for ad, v in filtreler.items():
            y.append(f"   {ad:14s}: {v['engellenen']:4d}/{v['n']:<4d} engellenirdi | engellenen ort "
                     f"{_pct(v['engellenenin_gercek_ort'])} | geçen ort {_pct(v['gecenin_gercek_ort'])}")
    if etki:
        y += ['', "3) ÇIKIŞ MANTIĞI (aynı girişler), pozisyon başına ort. net getiri. Asıl karşılaştırma "
                  "'V18.4 - Ağustos-sim': iki mantık da aynı simülatörle, simülatör hatası büyük ölçüde sadeleşir"]
        for ad, v in etki.items():
            ga = v['v184_eski_sim_ga95']
            ga_txt = f"[{_pct(ga[0])}, {_pct(ga[1])}]" if ga[0] is not None else '(n<5)'
            y.append(f"   {ad:12s} n={v['n']:3d} | gerçek {_pct(v['gercek_ort'])} | Ağustos-sim {_pct(v['eski_sim_ort'])} | "
                     f"V18.4-sim {_pct(v['v184_ort'])} | V18.4 - Ağustos-sim {_pct(v['v184_eski_sim_farki'])} "
                     f"%95 GA {ga_txt}")
    y += ['', "4) BÜTÇE SONUÇLARI (botun kurallarıyla). ilk30g: başlangıçtan 30 gün içinde kapanan işlemlerin kârı; "
              "maxDD gerçekleşmiş nakit + açık pozisyon maliyeti üzerinden (açık pozisyonun anlık zararı hariç)",
          f"   {'senaryo':13s} {'bütçe':>5s} {'mod':8s} {'işlem':>5s} {'atlanan':>7s} {'toplam':>8s} {'getiri':>7s} "
          f"{'maxDD':>6s} {'ilk30g':>7s} {'30g medyan':>10s} {'30g %10':>8s} {'30g %90':>8s}"]
    for r in butce_tablosu:
        f = (lambda v: f"{v:+.2f}" if v is not None else '  -')
        y.append(f"   {r['senaryo']:13s} {r['butce']:5.0f} {r['mod']:8s} {r['islem']:5d} {sum(r['atlanan'].values()):7d} "
                 f"{r['toplam_kar']:+8.2f} {r['getiri_pct']:+6.1f}% {r['max_dusus_pct']:5.1f}% {r['ilk_30_gun_kar']:+7.2f} "
                 f"{f(r['p30_medyan']):>10s} {f(r['p30_p10']):>8s} {f(r['p30_p90']):>8s}")
    sim_sonu = {r['senaryo']: r['sim_sonu_kapatilan'] for r in butce_tablosu if r.get('sim_sonu_kapatilan')}
    if sim_sonu:
        y.append(f"   Süre sınırında/veri sonunda son fiyattan kapatılan pozisyon: {sim_sonu}")
    if backfill:
        ga = lambda g: f"[{_pct(g[0])}, {_pct(g[1])}]"  # noqa: E731
        y += ['', "5) PİYASA TARAMASI (backfill) — sinyal başı net getiri, bütçe kuralları olmadan (her sinyal tek "
                  "başına; %95 GA gün bazlı bootstrap)",
              f"   {'senaryo':13s} {'sinyal':>6s} {'ort.':>8s} {'kazanan':>8s}   {'%95 GA':22s} canlı kuruluma "
              f"(B_V184_AI) göre fark"]
        for ad, k in backfill['senaryolar'].items():
            f = backfill['fark'].get(ad)
            fark = f"{_pct(f['ort'])} {ga(f['ga'])}" if f else ('(taban)' if ad == 'B_V184_AI' else '')
            y.append(f"   {ad:13s} {k['n']:6d} {_pct(k['ort']):>8s} {k['kazanan'] * 100:7.0f}%   {ga(k['ga']):22s} {fark}")
        adlar = {'atr_3_4': 'ATR %3-4 arası sinyaller (B_AI_ATR4 ile eklenen)',
                 'yatay_rejim': 'yatay rejim sinyalleri (B_AI_REJIMSIZ ile eklenen)'}
        for ad, k in backfill['eklenen'].items():
            y.append(f"   {adlar.get(ad, ad)}: n={k['n']} | ort {_pct(k['ort'])} {ga(k['ga'])} | "
                     f"kazanan %{k['kazanan'] * 100:.0f}")
    y += aylik_bolum(butce_tablosu)
    y += ['', 'Senaryolar: ' + ' | '.join(f"{k}: {v}" for k, v in SENARYO_ACIKLAMA.items()
                                          if any(r['senaryo'] == k for r in butce_tablosu))]
    return '\n'.join(y)


# ------------------------------------------------------------------------------------------
def calistir(ex, a, simdi_ms=None, log=print):
    simdi_ms = int(simdi_ms if simdi_ms is not None else time.time() * 1000)
    bas_ms = _ms(a.baslangic)
    bit_ms = min(_ms(a.bitis), simdi_ms) if a.bitis else simdi_ms
    depo = MumDeposu(ex, a.onbellek, simdi_ms=simdi_ms)
    yuva, ai_egitim_son = ai_yukle(a.ai_model, log)
    ortak = dict(kayma_seviye=a.kayma_seviye, kayma_zaman=a.kayma_zaman, max_saat=a.max_saat)
    motor_v184 = CikisMotoru(RISK_V184, rsi_mum=100, **ortak)
    motorlar = {'ESKI_SIM': CikisMotoru(RISK_ESKI, rsi_mum=25, **ortak), 'V184': motor_v184}

    log(f"BTC 15m verisi indiriliyor ve rejim hesaplanıyor ({_tarih(bas_ms)} → {_tarih(bit_ms)})...")
    btc = BtcBaglami(depo.getir_df('BTC/USDT', '15m', bas_ms - (bf.PENCERE_BTC + bf.ISINMA_MUM) * MUM_15M_MS,
                                   bit_ms + int(a.max_saat * SAAT_MS) + MUM_15M_MS))
    notlar = []
    if yuva is not None and ai_egitim_son is None:
        notlar.append("AI modeli kartsız: eğitim dönemi bilinmiyor. Mevcut eski core model için adli analiz: ilk 68 "
                      "işlem (31 May-15 Haz); bu iki haftadaki girişlerin AI skoru örneklem içi olabilir, diğer "
                      "dönemler örneklem dışı.")
    if yuva is not None and ai_egitim_son is not None and ai_egitim_son >= bit_ms:
        notlar.append(f"AI modeli simülasyon dönemini de içeren veriyle eğitilmiş ({_tarih(ai_egitim_son)}'e kadar): "
                      f"AI senaryoları atlandı (örneklem içi olurdu).")
        yuva = None
    meta = {'baslangic': _tarih(bas_ms), 'bitis': _tarih(bit_ms), 'olusturma': _tarih(simdi_ms),
            'kayma_seviye': a.kayma_seviye, 'kayma_zaman': a.kayma_zaman, 'max_saat': a.max_saat,
            'ai': (f"{os.path.basename(a.ai_model)} (eşik {yuva.esik:.2f}"
                   + (f", yalnız {_tarih(ai_egitim_son)} sonrası girişler" if ai_egitim_son else '') + ')')
            if yuva else None, 'notlar': notlar}
    senaryolar, dogrulama, filtreler, etki = {}, {}, {}, {}
    yazilacak = []

    if 'gercek' in a.kaynak:
        dosyalar = dosyalari_bul(a.dosyalar)
        if not dosyalar:
            raise SystemExit("İşlem CSV dosyası bulunamadı.")
        df = gs.oku(dosyalar)
        ana, _yab, _sah, tem = gs.temizle(df)
        poz = gs.pozisyonlar(ana)
        poz = poz[(poz['giris'] >= pd.Timestamp(a.baslangic))]
        if a.bitis:
            poz = poz[poz['giris'] < pd.Timestamp(a.bitis)]
        poz = poz.reset_index(drop=True)
        if a.limit:
            poz = poz.head(a.limit)
        if poz.empty:
            raise SystemExit("Seçilen tarih aralığında pozisyon yok.")
        log(f"Gerçek girişler: {len(poz)} pozisyon ({', '.join(os.path.basename(d) for d in dosyalar)}); "
            f"ayıklanan: başka bot {tem['yabanci_satir']}, sahiplenilmiş {tem['sahipsiz_satir']} satır")
        yuva_a = yuva
        if yuva is not None:
            neden = ai_uygun_mu(yuva, [{'Giris_RSI': r['rsi'], 'Giris_Vol_Oran': r['vol_oran'],
                                        'Giris_ATR_Pct': r['atr_pct'], 'Sinyal': r['sinyal']} for _, r in poz.iterrows()])
            if neden:
                notlar.append(f"Gerçek girişlerde AI senaryosu atlandı: {neden} (eski CSV kayıtlarında bu feature'lar yok)")
                yuva_a = None
        tablo = gercek_kaynak(depo, btc, poz, motorlar, yuva_a, log, saat_farki=a.saat_farki)
        kul = kullanilabilir(tablo)
        var = tablo[tablo['veri'] == 'VAR']
        meta['gercek'] = {'pozisyon': int(len(tablo)), 'veri_yok': int((tablo['veri'] != 'VAR').sum()),
                          'dogrulanan': int(len(kul)),
                          'uzatma_duzeltilen': int((pd.to_numeric(kul.get('uzatma_k'), errors='coerce').fillna(0) > 0).sum())
                          if len(kul) else 0,
                          'dogrulanamayan': int(len(var) - len(kul))}
        if len(var) and len(kul) < 0.8 * len(var):
            notlar.append(f"Giriş zamanlarının yalnız {len(kul)}/{len(var)}'i doğrulanabildi: CSV saatleri UTC değilse "
                          f"--saat-farki ile tekrar deneyin (ör. sunucu UTC+3 ise --saat-farki -3).")
        senaryolar.update(gercek_senaryolari(tablo, yuva_a, ai_egitim_son))
        dogrulama, filtreler, etki = dogrulama_ozeti(tablo), filtre_ozeti(tablo, yuva_a, ai_egitim_son), cikis_etkisi(tablo)
        yazilacak.append((tablo, a.cikti + '_pozisyonlar.csv'))

    if 'backfill' in a.kaynak:
        notlar.append(f"Piyasa taraması evreni bugünün en hacimli {a.evren} paritesi: dönem içinde delist olan coinler "
                      f"yok, sonuç biraz iyimser olabilir.")
        if a.radar_saat != 24:
            notlar.append(f"DENEY: radar son {a.radar_saat:g} saatte en çok yükselen 10 pariteyi tarar (canlı bot: 24 saat).")
        log(f"Backfill: evren seçiliyor (en hacimli {a.evren} USDT paritesi) ve sinyaller üretiliyor...")
        try:
            btablo, yuva_b = backfill_kaynak(depo, ex, btc, bas_ms, bit_ms, a.evren, motor_v184, yuva, log=log,
                                             motor_eski=motorlar['ESKI_SIM'],
                                             radar_mum=max(1, int(round(a.radar_saat * 4))))
            senaryolar.update(backfill_senaryolari(btablo, yuva_b, ai_egitim_son))
            yazilacak.append((btablo, a.cikti + '_backfill.csv'))
        except Exception as e:  # noqa: BLE001 - gerçek kaynak raporu yine yazılsın
            log(f"⚠️ Backfill çalışmadı: {type(e).__name__}: {e}")
            notlar.append(f"Backfill çalışmadı: {type(e).__name__}: {e}")

    meta['istek'] = depo.istek
    butce_tablosu = []
    for ad, islemler in senaryolar.items():
        for mod in a.mod:
            for b in a.butce:
                butce_tablosu.append({'senaryo': ad, **butce_ozeti(islemler, b, mod, a.oran, bas_ms, bit_ms)})
    bozet = backfill_karsilastirma(senaryolar)
    metin = rapor_metni(meta, dogrulama, filtreler, etki, butce_tablosu, bozet)
    for tablo_, yol in yazilacak:
        tablo_.drop(columns=[c for c in tablo_.columns if c.startswith('_')]).to_csv(yol, index=False)
    with open(a.cikti + '_rapor.txt', 'w', encoding='utf-8') as f:
        f.write(metin + '\n')
    with open(a.cikti + '_rapor.json', 'w', encoding='utf-8') as f:
        json.dump({'meta': meta, 'dogrulama': dogrulama, 'filtreler': filtreler, 'cikis_etkisi': etki,
                   'butce': butce_tablosu, 'backfill_karsilastirma': bozet}, f, ensure_ascii=False, indent=1,
                  default=str)
    log('\n' + metin)
    log(f"\nÇıktılar: {a.cikti}_rapor.txt, {a.cikti}_rapor.json" + ''.join(f", {y}" for _, y in yazilacak))
    return {'meta': meta, 'dogrulama': dogrulama, 'filtreler': filtreler, 'cikis_etkisi': etki,
            'butce': butce_tablosu, 'senaryolar': senaryolar, 'backfill_karsilastirma': bozet}


def arguman_ayristirici():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('dosyalar', nargs='*', help='işlem CSV\'leri (varsayılan: bot klasöründeki core_islem_verileri*.csv '
                                                've *.onarildi.csv)')
    ap.add_argument('--baslangic', default='2026-08-05', help='bu tarihten itibaren açılan pozisyonlar / sinyaller')
    ap.add_argument('--bitis', default=None, help='bu tarihten ÖNCE açılanlar (varsayılan: bugün)')
    ap.add_argument('--kaynak', nargs='+', choices=['gercek', 'backfill'], default=['gercek', 'backfill'])
    ap.add_argument('--butce', type=float, nargs='+', default=[100.0, 450.0])
    ap.add_argument('--mod', nargs='+', choices=['sabit', 'oransal'], default=['sabit', 'oransal'])
    ap.add_argument('--oran', type=float, default=0.20, help='oransal mod: işlem başına özsermaye payı')
    ap.add_argument('--ai-model', default=os.path.join(KOK, 'core_xgboost_model.json'), help="'yok': AI senaryosu yok")
    ap.add_argument('--evren', type=int, default=250, help='backfill: en hacimli N USDT paritesi')
    ap.add_argument('--max-saat', type=float, default=96.0, help='pozisyon bu süreden sonra son fiyattan kapatılır')
    ap.add_argument('--radar-saat', type=float, default=24.0,
                    help='backfill radarı: son N saatte en çok yükselen 10 parite (canlı bot 24; deney: 4 = erken yakalama)')
    ap.add_argument('--kayma-seviye', type=float, default=0.0015)
    ap.add_argument('--kayma-zaman', type=float, default=0.0005)
    ap.add_argument('--onbellek', default=os.path.join(KOK, 'sim_onbellek'))
    ap.add_argument('--cikti', default=os.path.join(KOK, 'v184_sim'))
    ap.add_argument('--limit', type=int, default=None, help='hızlı deneme: ilk N gerçek pozisyon')
    ap.add_argument('--saat-farki', type=float, default=0.0,
                    help='CSV saatleri UTC değilse TÜM kayıtlara eklenecek saat (ör. UTC+3 sunucu için -3)')
    return ap


def main(argv=None):
    a = arguman_ayristirici().parse_args(argv)
    import ccxt
    ex = ccxt.binance({'enableRateLimit': True, 'options': {'defaultType': 'spot'}})
    ex.rateLimit = max(ex.rateLimit or 0, 100)   # botla aynı IP limiti paylaşılıyor: yavaş ve güvenli
    try:
        calistir(ex, a)
    except (ccxt.NetworkError, ccxt.ExchangeNotAvailable, ccxt.AuthenticationError, VeriYok) as e:
        print(f"\n❌ Binance fiyat verisine erişilemedi: {type(e).__name__}: {str(e)[:300]}\n"
              f"   Bu makineden api.binance.com erişimi var mı? (Bot sunucusunda çalıştırın.) "
              f"İndirilen veri {a.onbellek} içinde saklandı; tekrar çalıştırınca kaldığı yerden devam eder.")
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
