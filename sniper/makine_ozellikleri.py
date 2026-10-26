# -*- coding: utf-8 -*-
"""MAKİNE: yeni mum ve hacim kalitesi özellikleri, ileri etiketler, emir defteri özeti (saf numpy, nedensel).

Girdi: tam saatlik UTC ızgarasındaki diziler (eksik saat NaN; sniper.hafiza.Hafiza.izgara kurar, strateji_lab'ın
izgaraya_yerlestir kuralıyla: bozuk fiyatlı mumun tamamı NaN). Özellikler karar mumu t'de YALNIZ <= t mumlarını
kullanır (tests/test_makine.py geleceği bozarak doğrular). Etiketler (ileri_etiketler) bilerek t+1..t+24 mumlarını
kullanır; yalnız 'etiket' tablosuna yazılır, hiçbir sinyalde ya da özellikte kullanılmaz.
strateji_lab'ın özellikleri (ret24, rsi14, adx14, ...) burada YOK: onlar strateji_lab.gostergeler/ozellikler ile
aynı koddan alınır (tek kod ilkesi); bu modül yalnız yeni olanları tanımlar.

Mum özellikleri (karar mumu t; R = H - L, gövde b = |C - O|, üst fitil u = H - max(O, C), alt fitil
a = min(O, C) - L; mum eksikse ya da R = 0 ise NULL):
  ret1        C[t] / C[t-1] - 1
  govde       b / R            ust_fitil   u / R            alt_fitil   a / R
Desenler (karar mumunda 1/0; gereken mumlardan biri eksikse, tek mum desenlerinde R = 0 ise NULL). Önceki 6 saatin
getirisi g6 = C[t-1] / C[t-7] - 1 (karar mumundan ÖNCEKİ eğilim):
  doji            b <= 0.1 R
  cekic           a >= 2b ve u <= 0.5b ve a > 0 ve g6 < 0          (düşüşten sonra uzun alt fitil; b = 0 ise u = 0
                                                                   gerekir: yusufçuk doji de çekiç sayılır)
  kayan_yildiz    u >= 2b ve a <= 0.5b ve u > 0 ve g6 > 0          (yükselişten sonra uzun üst fitil; aynası)
  yutan_boga      t-1 kırmızı (C < O), t yeşil (C > O), O[t] <= C[t-1] ve C[t] >= O[t-1], b[t] > b[t-1]
  yutan_ayi       t-1 yeşil, t kırmızı, O[t] >= C[t-1] ve C[t] <= O[t-1], b[t] > b[t-1]
  uc_beyaz_asker  t-2, t-1, t yeşil; C[t-2] < C[t-1] < C[t]; O[t-1] > O[t-2] ve O[t-1] <= C[t-2];
                  O[t] > O[t-1] ve O[t] <= C[t-1]
Hacim kalitesi (Binance ham klines alanları: qv = USDT hacmi (alan 7), n = işlem adedi (alan 8), aqv = alıcı
(taker buy) USDT hacmi (alan 10); fiyatı bozuk/eksik saatte NaN, sıfır hacimli saat 0 olarak kalır):
  islem_orani        n[t] / medyan(n[t-173..t-6])
  buyuk_lot_orani    (qv/n)[t] / medyan((qv/n)[t-173..t-6])    işlem başına ortalama USDT (n = 0 saatte tanımsız)
  alici_payi         aqv[t] / qv[t]
  alici_payi6        toplam(aqv[t-5..t]) / toplam(qv[t-5..t])  (6 saatin hepsi dolu olmalı)
  hacim_yogunlugu24  max(qv[t-23..t]) / toplam(qv[t-23..t])    (1/24 = eşit dağılım, 1'e yakın = tek ani hacim;
                                                               24 saatin hepsi dolu olmalı)
  168 saatlik medyanlar strateji_lab'daki vol_ratio6 medyanıyla aynı pencere ve doluluk kuralıyla (en az %90 dolu,
  DOLULUK; pandas rolling(168, min_periods=152).median() kaydırma 6 ile birebir; dizinin başında pencere kısalır).
  Payda 0 ya da sonuç sonlu değilse NULL.
İleri etiketler (karar mumu t'nin kapanışı c0 = C[t]'ye göre; ufuk 24 saat):
  ileri_1s / ileri_4s / ileri_24s   C[t+k] / c0 - 1 (o saat eksikse NULL)
  max_yukari_24s   max(H[t+1..t+24]) / c0 - 1        max_asagi_24s   min(L[t+1..t+24]) / c0 - 1
  ucus10   +%10'a (c0 x 1.10) -%5'ten (c0 x 0.95) ÖNCE dokunursa 1; önce -%5'e dokunursa ya da ikisine de
           dokunmazsa 0. ucus20: +%20 / -%7. Mum içi sıra strateji_lab.cikis_simule ile aynı: yeşil (C >= O)
           açılış->dip->tepe->kapanış, kırmızı açılış->tepe->dip->kapanış (açılışta boşluk o noktada dokunur).
  t+24 mumu ızgarada yoksa (henüz kapanmadı ya da çekilmedi) etiket yok: sonra denenir. C[t] eksikse ya da
  t+1..t+24 içinde AZAMI_EKSIK'ten (2) çok eksik saat varsa etiket kalıcı NULL (eksik_saat yazılır); en çok 2 eksik
  saat atlanır.
Emir defteri (ccxt fetch_order_book; orta = (en iyi alış + en iyi satış) / 2):
  spread_bps       (en iyi satış - en iyi alış) / orta x 1e4
  al_derinlik_k    alış seviyelerinden fiyatı >= orta x (1 - k/100) olanların USDT toplamı (fiyat x miktar), k = 1, 2
  sat_derinlik_k   satış seviyelerinden fiyatı <= orta x (1 + k/100) olanların USDT toplamı
  dengesizlik_1    (al_derinlik_1 - sat_derinlik_1) / (al_derinlik_1 + sat_derinlik_1)
  kapsam_bps       iki taraftaki en derin seviyenin ortaya uzaklığının küçüğü (bps). 100'den (200'den) küçükse %1 (%2)
                   derinliği defter sınırıyla (limit=100 seviye) kesilmiştir: değer alt sınırdır (likit paritelerde
                   olağan; sonradan ayıklanabilsin diye yazılır).
"""
import math
from typing import Dict, Optional, Sequence

import numpy as np

SAAT_MS = 3_600_000
DOLULUK = 0.9                   # strateji_lab.DOLULUK ile aynı (test eşitliği doğrular)
MEDYAN_PENCERE, MEDYAN_GECIKME = 168, 6      # medyan(x[t-173..t-6])
UFUK = 24                       # ileri etiket ufku (saat)
AZAMI_EKSIK = 2                 # ufuk içinde atlanabilecek en çok eksik saat
UCUS = {'ucus10': (1.10, 0.95), 'ucus20': (1.20, 0.93)}   # (hedef, stop) kapanışa göre
RADAR_ESIK = 12_000_000         # strateji_lab.BOT_HACIM (botun radarı: 24s hacmi 12M USDT üstü)

MUM_OZELLIKLERI = ('ret1', 'govde', 'ust_fitil', 'alt_fitil')
DESENLER = ('doji', 'cekic', 'kayan_yildiz', 'yutan_boga', 'yutan_ayi', 'uc_beyaz_asker')
HACIM_OZELLIKLERI = ('islem_orani', 'buyuk_lot_orani', 'alici_payi', 'alici_payi6', 'hacim_yogunlugu24')
YENI_OZELLIKLER = MUM_OZELLIKLERI + DESENLER + HACIM_OZELLIKLERI
ETIKETLER = ('ileri_1s', 'ileri_4s', 'ileri_24s', 'max_yukari_24s', 'max_asagi_24s', 'ucus10', 'ucus20')
DEFTER_ALANLARI = ('spread_bps', 'al_derinlik_1', 'sat_derinlik_1', 'al_derinlik_2', 'sat_derinlik_2',
                   'dengesizlik_1', 'kapsam_bps')


# ------------------------------------------------------------------------------------------
# Yardımcılar
# ------------------------------------------------------------------------------------------
def _dizi(x) -> np.ndarray:
    return np.asarray(x, dtype=float)


def _kaydir(x: np.ndarray, k: int) -> np.ndarray:
    """t'deki değer x[t-k] olur (baştaki k değer NaN; strateji_lab._kaydir ile aynı)."""
    y = np.full(len(x), np.nan)
    if k == 0:
        y[:] = x
    elif k < len(x):
        y[k:] = x[:-k]
    return y


def _ikili(kosul: np.ndarray, gecerli: np.ndarray) -> np.ndarray:
    """Desen dizisi: geçerli saatte 1.0 / 0.0, geçersiz (hesaplanamayan) saatte NaN."""
    return np.where(gecerli, kosul.astype(float), np.nan)


def _sonlu(x: np.ndarray) -> np.ndarray:
    """Sonlu olmayan sonuçlar (0'a bölme, eksik saat) NaN: veritabanına NULL yazılır."""
    x = np.asarray(x, dtype=float)
    return np.where(np.isfinite(x), x, np.nan)


def _idx(n: int, idx) -> np.ndarray:
    return np.arange(n) if idx is None else np.asarray(idx, dtype=int).reshape(-1)


def _pencereler(x: np.ndarray, pencere: int, idx: np.ndarray, gecikme: int = 0) -> np.ndarray:
    """Her i için x[i-gecikme-pencere+1 .. i-gecikme] (dizinin başından önceki ve sonrası NaN): (len(idx), pencere)."""
    if not len(idx):
        return np.empty((0, pencere))
    dolgu = np.concatenate([np.full(pencere + gecikme - 1, np.nan), x])
    gorunum = np.lib.stride_tricks.sliding_window_view(dolgu, pencere)
    return gorunum[idx]                         # satır i: dolgu[i .. i+pencere-1] = x[i-gecikme-pencere+1 .. i-gecikme]


def _kayan_medyan(x: np.ndarray, idx: np.ndarray, pencere: int = MEDYAN_PENCERE,
                  gecikme: int = MEDYAN_GECIKME) -> np.ndarray:
    """medyan(x[i-gecikme-pencere+1 .. i-gecikme]); pencerede en az ceil(pencere x DOLULUK) dolu değer gerekir
    (pandas rolling(pencere, min_periods).median() kaydırma gecikme ile aynı: NaN'ler atlanır, dizinin başında
    pencere kısalır)."""
    w = _pencereler(x, pencere, idx, gecikme)
    sonuc = np.full(len(idx), np.nan)
    yeter = np.isfinite(w).sum(axis=1) >= int(math.ceil(pencere * DOLULUK))
    if yeter.any():
        sonuc[yeter] = np.nanmedian(w[yeter], axis=1)
    return sonuc


def _tam_toplam(x: np.ndarray, idx: np.ndarray, pencere: int) -> np.ndarray:
    """toplam(x[i-pencere+1..i]); pencerenin hepsi dolu değilse NaN."""
    w = _pencereler(x, pencere, idx)
    return w.sum(axis=1)                        # bir NaN bile varsa toplam NaN


# ------------------------------------------------------------------------------------------
# Özellikler
# ------------------------------------------------------------------------------------------
def mum_ozellikleri(O, H, L, C, idx=None) -> Dict[str, np.ndarray]:
    """ret1, govde, fitiller ve 6 desen (modül belgesindeki tanımlar). idx verilirse yalnız o saatler (sıra korunur);
    desenler 1.0 / 0.0 / NaN (hesaplanamaz)."""
    O, H, L, C = (_dizi(x) for x in (O, H, L, C))
    dolu = np.isfinite(O) & np.isfinite(H) & np.isfinite(L) & np.isfinite(C)
    with np.errstate(invalid='ignore', divide='ignore'):
        R = H - L
        b = np.abs(C - O)
        u = H - np.maximum(O, C)
        a = np.minimum(O, C) - L
        sekil = dolu & (R > 0)
        g6 = _kaydir(C, 1) / _kaydir(C, 7) - 1
        O1, C1, b1 = _kaydir(O, 1), _kaydir(C, 1), _kaydir(b, 1)
        O2, C2 = _kaydir(O, 2), _kaydir(C, 2)
        dolu1 = _kaydir(dolu.astype(float), 1) == 1
        dolu2 = _kaydir(dolu.astype(float), 2) == 1
        yesil, kirmizi = C > O, C < O
        oz = {
            'ret1': C / C1 - 1,
            'govde': np.where(sekil, b / R, np.nan),
            'ust_fitil': np.where(sekil, u / R, np.nan),
            'alt_fitil': np.where(sekil, a / R, np.nan),
            'doji': _ikili(b <= 0.1 * R, sekil),
            'cekic': _ikili((a >= 2 * b) & (u <= 0.5 * b) & (a > 0) & (g6 < 0), sekil & np.isfinite(g6)),
            'kayan_yildiz': _ikili((u >= 2 * b) & (a <= 0.5 * b) & (u > 0) & (g6 > 0), sekil & np.isfinite(g6)),
            'yutan_boga': _ikili((C1 < O1) & yesil & (O <= C1) & (C >= O1) & (b > b1), dolu & dolu1),
            'yutan_ayi': _ikili((C1 > O1) & kirmizi & (O >= C1) & (C <= O1) & (b > b1), dolu & dolu1),
            'uc_beyaz_asker': _ikili((C2 > O2) & (C1 > O1) & yesil & (C2 < C1) & (C1 < C)
                                     & (O1 > O2) & (O1 <= C2) & (O > O1) & (O <= C1), dolu & dolu1 & dolu2),
        }
    i = _idx(len(C), idx)
    return {k: _sonlu(v[i]) if k in MUM_OZELLIKLERI else v[i] for k, v in oz.items()}


def hacim_kalitesi(QV, N, AQV, idx=None) -> Dict[str, np.ndarray]:
    """İşlem adedi, lot büyüklüğü, alıcı payı ve hacim yoğunluğu (modül belgesindeki tanımlar). idx verilirse yalnız o
    saatler hesaplanır (canlı döngüde yalnız karar mumu: 168 saatlik medyan her saat için yeniden kurulmaz)."""
    QV, N, AQV = (_dizi(x) for x in (QV, N, AQV))
    i = _idx(len(QV), idx)
    with np.errstate(invalid='ignore', divide='ignore'):
        lot = np.where(N > 0, QV / N, np.nan)                      # işlem başına USDT (işlemsiz saatte tanımsız)
        w24 = _pencereler(QV, 24, i)                               # bir saat eksikse max ve toplam NaN
        return {
            'islem_orani': _sonlu(N[i] / _kayan_medyan(N, i)),
            'buyuk_lot_orani': _sonlu(lot[i] / _kayan_medyan(lot, i)),
            'alici_payi': _sonlu(AQV[i] / QV[i]),
            'alici_payi6': _sonlu(_tam_toplam(AQV, i, 6) / _tam_toplam(QV, i, 6)),
            'hacim_yogunlugu24': _sonlu(w24.max(axis=1) / w24.sum(axis=1)),
        }


def yeni_ozellikler(O, H, L, C, QV, N, AQV, idx=None) -> Dict[str, np.ndarray]:
    """mum_ozellikleri + hacim_kalitesi (YENI_OZELLIKLER sırasıyla)."""
    oz = mum_ozellikleri(O, H, L, C, idx)
    oz.update(hacim_kalitesi(QV, N, AQV, idx))
    return {k: oz[k] for k in YENI_OZELLIKLER}


def radar_siralari(R24, V24, esik: float = RADAR_ESIK) -> np.ndarray:
    """Bir saatin kesiti (parite sütunları): 24s hacmi >= esik olanlar içinde 24s getirisine göre sıra (1 = en çok
    yükselen), uygun olmayan NaN. strateji_lab.radar_ilk10 ile birebir: paneller float32 tutulduğu için değerler önce
    float32'ye çevrilir, eşitlikte sütun sırası (kararlı sıralama); radar_ilk10 = (sıra <= 10)."""
    R = np.asarray(R24, dtype=np.float32).astype(np.float64)
    V = np.asarray(V24, dtype=np.float32)
    with np.errstate(invalid='ignore'):
        uygun = (V >= esik) & np.isfinite(R)
    skor = np.where(uygun, R, -np.inf)
    sira = np.empty(len(R))
    sira[np.argsort(-skor, kind='stable')] = np.arange(1, len(R) + 1)
    return np.where(uygun, sira, np.nan)


# ------------------------------------------------------------------------------------------
# İleri etiketler (yalnız etiket tablosu: özellik değildir)
# ------------------------------------------------------------------------------------------
def _ucus(O, H, L, C, i: int, hedef: float, stop: float, son: int) -> int:
    """i+1..son mumlarında (eksik atlanır) mum içi yolla önce hedefe (1) mi stopa (0) mı dokunuluyor; hiçbiri 0."""
    for b in range(i + 1, son + 1):
        o = O[b]
        if not o == o:
            continue
        h, l, c = H[b], L[b], C[b]
        for p in ((o, l, h, c) if c >= o else (o, h, l, c)):
            if p >= hedef:
                return 1
            if p <= stop:
                return 0
    return 0


def ileri_etiketler(O, H, L, C, i: int, ufuk: int = UFUK, azami_eksik: int = AZAMI_EKSIK) -> Optional[dict]:
    """Karar mumu i'nin ileri etiketleri (modül belgesi). Dönen: None (t+ufuk mumu ızgarada yok: sonra denenir) ya da
    {ETIKETLER..., 'eksik_saat'}; C[i] eksikse ya da eksik saat > azami_eksik ise etiketler None (kalıcı NULL).
    O/H/L/C: kapsanan son saate kadar kurulmuş ızgara (hafiza.Hafiza.izgara; kapsam dışı saatler dizide olmamalı)."""
    O, H, L, C = (_dizi(x) for x in (O, H, L, C))
    son = i + ufuk
    if i < 0 or son >= len(C):
        return None
    dolu = np.isfinite(O[i + 1:son + 1]) & np.isfinite(C[i + 1:son + 1])
    eksik = int(ufuk - dolu.sum())
    bos = {k: None for k in ETIKETLER}
    c0 = C[i]
    if not (np.isfinite(c0) and c0 > 0) or eksik > azami_eksik:
        return {**bos, 'eksik_saat': eksik}
    sonuc = {'eksik_saat': eksik}
    for k in (1, 4, 24):
        x = C[i + k] / c0 - 1 if k <= ufuk else np.nan
        sonuc[f'ileri_{k}s'] = float(x) if np.isfinite(x) else None
    hh, ll = H[i + 1:son + 1][dolu], L[i + 1:son + 1][dolu]
    sonuc['max_yukari_24s'] = float(hh.max() / c0 - 1)
    sonuc['max_asagi_24s'] = float(ll.min() / c0 - 1)
    for ad, (h, s) in UCUS.items():
        sonuc[ad] = _ucus(O, H, L, C, i, c0 * h, c0 * s, son)
    return {**{k: sonuc[k] for k in ETIKETLER}, 'eksik_saat': eksik}


# ------------------------------------------------------------------------------------------
# Emir defteri özeti
# ------------------------------------------------------------------------------------------
def _seviyeler(taraf: Sequence) -> np.ndarray:
    """[[fiyat, miktar, ...], ...] -> (n, 2); bozuk/eksi satırlar atılır."""
    if not taraf:
        return np.empty((0, 2))
    try:
        d = np.asarray([(float(x[0]), float(x[1])) for x in taraf], dtype=float).reshape(-1, 2)
    except (TypeError, ValueError, IndexError):
        return np.empty((0, 2))
    return d[np.isfinite(d).all(axis=1) & (d[:, 0] > 0) & (d[:, 1] >= 0)]


def defter_ozellikleri(defter: Optional[dict]) -> Optional[dict]:
    """ccxt emir defteri -> DEFTER_ALANLARI (modül belgesi). İki taraftan biri boşsa ya da defter çaprazsa None."""
    if not defter:
        return None
    al, sat = _seviyeler(defter.get('bids')), _seviyeler(defter.get('asks'))
    if not len(al) or not len(sat):
        return None
    en_al, en_sat = al[:, 0].max(), sat[:, 0].min()
    if en_sat < en_al:
        return None
    orta = (en_al + en_sat) / 2
    o = {'spread_bps': (en_sat - en_al) / orta * 1e4}
    for k in (1, 2):
        o[f'al_derinlik_{k}'] = float((al[:, 0] * al[:, 1])[al[:, 0] >= orta * (1 - k / 100)].sum())
        o[f'sat_derinlik_{k}'] = float((sat[:, 0] * sat[:, 1])[sat[:, 0] <= orta * (1 + k / 100)].sum())
    top = o['al_derinlik_1'] + o['sat_derinlik_1']
    o['dengesizlik_1'] = (o['al_derinlik_1'] - o['sat_derinlik_1']) / top if top > 0 else None
    o['kapsam_bps'] = float(min(orta - al[:, 0].min(), sat[:, 0].max() - orta) / orta * 1e4)
    return {k: (float(v) if v is not None else None) for k, v in o.items()}
