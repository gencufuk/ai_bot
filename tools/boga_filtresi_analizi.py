# -*- coding: utf-8 -*-
"""ANALIZ §15: yükseliş-ayı filtreleri (F1 BOGA_PIYASA, F2 KENDI_14G, F3 IKISI), gelecek bilgisi yok."""
import sys
import numpy as np
import pandas as pd

islem_yolu, gunluk_yolu = sys.argv[1], sys.argv[2]
d = pd.read_csv(islem_yolu, usecols=['kural', 'giris_ts', 'cikis_ts', 'getiri_k1', 'getiri_k2'])
d['giris'] = pd.to_datetime(d['giris_ts'])
d['cikis'] = pd.to_datetime(d['cikis_ts'])
g = pd.read_csv(gunluk_yolu, index_col=0, parse_dates=True)['BTC_TUT'].fillna(0.0)
P = (1 + g).cumprod()                       # gün d satırı: d kapanışı (d-1 -> d getirisi)
boga = ((P > P.rolling(50, min_periods=50).mean()) & (P / P.shift(30) - 1 > 0)).astype(float)
boga[P.rolling(50, min_periods=50).mean().isna() | P.shift(30).isna()] = np.nan
boga_dun = boga.shift(1)                    # giriş günü D için: D-1 kapanışındaki durum

AYRIM = pd.Timestamp('2025-01-01')
RNG = np.random.default_rng(0)


def gun_bootstrap(r, gun, n=2000):
    _, inv = np.unique(gun, return_inverse=True)
    top, adet = np.bincount(inv, weights=r), np.bincount(inv).astype(float)
    idx = RNG.integers(0, len(top), size=(n, len(top)))
    ist = top[idx].sum(1) / adet[idx].sum(1)
    return np.percentile(ist, 2.5), np.percentile(ist, 97.5), (ist > 0).mean()


def ozet(x):
    if not len(x):
        return {'n': 0}
    r = x['getiri_k1'].to_numpy()
    lo, hi, p = gun_bootstrap(r, x['giris'].dt.normalize().to_numpy())
    ay = x.groupby(x['giris'].dt.to_period('M'))['getiri_k1'].sum()
    return {'n': len(x), 'ort': r.mean() * 100, 'ga': (lo * 100, hi * 100), 'p': p, 'kaz': (r > 0).mean() * 100,
            'k2': x['getiri_k2'].mean() * 100, 'pozay': (ay > 0).mean() * 100, 'ay': f"{(ay > 0).sum()}/{len(ay)}"}


def filtreler(x):
    x = x.sort_values('giris').reset_index(drop=True)
    gun = x['giris'].dt.normalize()
    f1 = gun.map(boga_dun)
    # F2: girişten önceki 14 günde KAPANMIŞ işlemler (aynı kuralın filtresiz kâğıt işlemleri)
    c = x.sort_values('cikis')
    cik, rr = c['cikis'].to_numpy(), c['getiri_k1'].to_numpy()
    kum = np.r_[0.0, np.cumsum(rr)]
    gir = x['giris'].to_numpy()
    bas = np.searchsorted(cik, gir - np.timedelta64(14, 'D'), 'left')
    son = np.searchsorted(cik, gir, 'right')          # cikis <= giris
    n = son - bas
    ort = np.where(n > 0, (kum[son] - kum[bas]) / np.maximum(n, 1), np.nan)
    f2 = pd.Series((n >= 5) & (ort > 0), index=x.index)
    f1b = (f1.fillna(0.0).astype(float) > 0.5)
    return x, {'FILTRESIZ': pd.Series(True, index=x.index), 'F1_BOGA_PIYASA': f1b, 'F2_KENDI_14G': f2,
               'F3_IKISI': f1b & f2}


def karar(s, k):
    kalan = []
    if s.get('n', 0) < 100: kalan.append(1)
    if not (s.get('n') and s['ort'] > 0 and s['p'] >= 0.99): kalan.append(2)
    if not (k.get('n') and k['ort'] > 0): kalan.append(3)
    if not (s.get('n') and s['pozay'] >= 60): kalan.append(4)
    if not (s.get('n') and s['k2'] > 0): kalan.append(5)
    return 'GEÇTİ' if not kalan else 'KALDI ' + ','.join(map(str, kalan))


def yaz(o):
    if not o.get('n'):
        return f"{0:5d}"
    return (f"{o['n']:5d} %{o['ort']:+6.3f} [{o['ga'][0]:+5.2f},{o['ga'][1]:+5.2f}] P={o['p']:.3f} kaz %{o['kaz']:4.1f} "
            f"k2 %{o['k2']:+6.3f} poz.ay {o['ay']:>5s}")


for kural in ['BOT_VEKILI', 'ERKEN_BIRIKIM', 'SIKISMA_KIRILIM', 'BOT_GERCEK', 'REJIM_KOMBO_BOT']:
    x, F = filtreler(d[d['kural'] == kural])
    print(f"\n== {kural}" + ('  (KARAR)' if kural == 'BOT_VEKILI' else '  (bilgi)'))
    for ad, m in F.items():
        k = ozet(x[m & (x['giris'] < AYRIM)]); s = ozet(x[m & (x['giris'] >= AYRIM)])
        print(f"  {ad:15s} kuruluş {yaz(k)}\n  {'':15s} sınama  {yaz(s)}   -> {karar(s, k) if ad != 'FILTRESIZ' else ''}")
    if kural == 'BOT_VEKILI':
        ay = x.groupby(x['giris'].dt.to_period('M'))
        tablo = pd.DataFrame({ad: x[m].groupby(x[m]['giris'].dt.to_period('M'))['getiri_k1'].sum() * 20
                              for ad, m in F.items()}).fillna(0.0)
        tablo['F1_aktif_gun%'] = [100 * boga_dun.reindex(pd.date_range(str(p.start_time.date()), str(p.end_time.date()))).fillna(0).astype(float).mean() for p in tablo.index]
        print("\n  BOT_VEKILI ay ay USDT (20 USDT/işlem, slot sınırı yok) ve F1'in açık olduğu gün oranı:")
        print(tablo.round(1).to_string())
