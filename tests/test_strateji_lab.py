# -*- coding: utf-8 -*-
"""tools/strateji_lab.py: gelecek bilgisi kullanılmaması, gösterge tanımları, çıkış motoru, tek pozisyon kuralı,
ileri yürüyen AI (sızıntı yok), bootstrap ve karar kuralı, bütçe muhasebesi, gömülü etki ve uçtan uca çalıştırma."""
import json
import os
import sys

import ccxt
import numpy as np
import pandas as pd
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, 'tools'))
import strateji_lab as sl  # noqa: E402

SAAT = sl.SAAT_MS
GUN = sl.GUN_MS
T0 = 1_672_531_200_000                  # 2023-01-01 00:00 UTC
SLIP_G, SLIP_S, SLIP_Z, FEE = sl.SLIP_GIRIS, sl.SLIP_SEVIYE, sl.SLIP_ZAMAN, sl.FEE


# ------------------------------------------------------------------------------------------
# Sentetik veri
# ------------------------------------------------------------------------------------------
def rastgele_mumlar(n, tohum, s0=100.0, sigma=0.01, hacim=1e6, hacim_sigma=0.7):
    """Rastgele yürüyüş 1 saatlik mumlar: O, H, L, C, V (V adet; saatlik USDT hacmi ~ hacim)."""
    rng = np.random.default_rng(tohum)
    c = s0 * np.exp(np.cumsum(rng.normal(0, sigma, n)))
    o = np.r_[s0, c[:-1]]
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, sigma / 3, n)))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, sigma / 3, n)))
    v = hacim * np.exp(rng.normal(0, hacim_sigma, n)) / c
    return o, h, l, c, v


def birikim_piyasasi(n, tohum, yukselis=True, faz=0, hacim=1e6):
    """ERKEN_BIRIKIM kurulumları gömülü piyasa: 76 saat hafif yükseliş, 24 saat dar yatay (son 5 saati 3 kat hacim),
    sonra 24 saatlik zirvenin %0.3 üstünde 3 kat hacimli kırılım mumu. Kırılımdan sonraki 170 saat (en uzun tutma
    168 saatten uzun): yukselis=True ise 10 saatte +%8, yavaş düşüş ve sakin seyir; False ise rastgele yürüyüş
    (sürüklenmesiz: hiçbir çıkışın beklentisi yok). Hacim başka yerde sabit (başka sinyal çıkmaz)."""
    rng = np.random.default_rng(tohum)
    c, vk = [100.0], [1.0]
    for _ in range(faz):
        c.append(c[-1] * (1 + rng.normal(0, 0.001)))
        vk.append(1.0)
    while len(c) < n:
        for _ in range(76):
            c.append(c[-1] * (1 + 0.001 + rng.normal(0, 0.001)))
            vk.append(1.0)
        for i in range(24):
            c.append(c[-1] * (1 + (0.0015 if i % 2 else -0.0015)))
            vk.append(3.0 if i >= 19 else 1.0)
        tepe = max(max(a, b) for a, b in zip(c[-25:-1], c[-24:])) * 1.001
        c.append(tepe * 1.003)
        vk.append(3.0)
        if yukselis:
            adimlar = [0.0077] * 10 + list(-0.0014 + rng.normal(0, 0.002, 60)) + list(rng.normal(0, 0.001, 100))
        else:
            adimlar = rng.normal(0, 0.005, 170)
        for x in adimlar:
            c.append(c[-1] * (1 + x))
            vk.append(1.0)
    c, vk = np.array(c[:n]), np.array(vk[:n])
    o = np.r_[c[0], c[:-1]]
    return (o, np.maximum(o, c) * 1.001, np.minimum(o, c) * 0.999, c,
            hacim * vk * np.exp(rng.normal(0, 0.05, n)) / c)


def donus_piyasasi(n, tohum, donus=True, faz=0, hacim=1e6, aralik=80):
    """YATAY_DONUS kurulumları gömülü piyasa: yatay coin (OU gürültüsü, phi 0.8, sigma %0.1; ADX düşük), her `aralik`
    saatte tek mumluk -%3 düşüş (Bollinger alt bandı altı, RSI14 < 30, ADX < 20). donus=True: sonraki 4 saatte eski
    düzeye döner (ortalamaya dönüş); False: 12 saat sürüklenmesiz rastgele yürüyüş (sigma %0.5), düzey orada kalır
    (çıkışın beklentisi yok). Hacim sabit."""
    rng = np.random.default_rng(tohum)
    x, duzey, c, plan = 0.0, 100.0, [], {}
    while len(c) < n:
        j = len(c)
        if j >= faz + aralik and (j - faz) % aralik == 0:
            onceki, duzey = duzey, duzey * 0.97
            if donus:
                plan.update({j + k: duzey + (onceki - duzey) * k / 4 for k in range(1, 5)})
            else:
                d = duzey
                for k in range(1, 13):
                    d *= np.exp(rng.normal(0, 0.005))
                    plan[j + k] = d
        duzey = plan.pop(j, duzey)
        x = 0.8 * x + rng.normal(0, 0.001)
        c.append(duzey * np.exp(x))
    c = np.array(c[:n])
    o = np.r_[c[0], c[:-1]]
    return o, np.maximum(o, c) * 1.001, np.minimum(o, c) * 0.999, c, hacim * np.exp(rng.normal(0, 0.05, n)) / c


def yatay_btc(n, tohum=0, s0=20000.0):
    """Trendsiz BTC: sabit düzey çevresinde bağımsız gürültü (sigma %0.3): saatlerin ~%90'ı YATAY (ADX < 20)."""
    c = s0 * (1 + np.random.default_rng(tohum).normal(0, 0.003, n))
    o = np.r_[c[0], c[:-1]]
    return o, np.maximum(o, c) * 1.001, np.minimum(o, c) * 0.999, c, np.full(n, 5e7) / c


def _hepsini_hesapla(piyasa, ts):
    """piyasa: {sembol: (O, H, L, C, V)}, ilk sembol BTC -> kesit, radar, BTC bağlamı (ADX ve rejim dahil),
    göstergeler, sinyaller (YATAY_DONUS dahil), kombo bileşen sinyalleri, özellikler."""
    semboller = list(piyasa)
    kes = [sl.kesit_sutunu(piyasa[s][3], piyasa[s][4]) for s in semboller]
    R24, V24 = np.column_stack([k[0] for k in kes]), np.column_stack([k[1] for k in kes])
    radar = sl.radar_ilk10(R24, V24)
    _, bh, bl, bc, _ = piyasa[semboller[0]]
    btc = sl.btc_baglami(bc, bh, bl)
    sonuc = {'radar': radar, 'btc': btc, 'g': {}, 's': {}, 'oz': {}}
    for j, s in enumerate(semboller):
        g = sl.gostergeler(*piyasa[s])
        sonuc['g'][s] = g
        sonuc['s'][s] = sl.sinyaller(g, btc['ok'], radar[:, j], 5e6, 2e7, btc['rejim'])
        for kombo in sl.KOMBO_TREND:
            for bilesen, (x, _) in sl.kombo_bilesenleri(kombo, sonuc['s'][s], btc['rejim']).items():
                sonuc['s'][s][f"{kombo}:{bilesen}"] = x
        sonuc['oz'][s] = sl.ozellikler(g, btc, ts, np.arange(len(ts)))
    return sonuc


def _gelecegi_degistir(piyasa, oynaklik, t0, tohum):
    """t0'dan SONRAKİ mumlar bağımsız bir rastgele yürüyüşle değiştirilir. Fiyat düzeyi t0 kapanışı civarında kalır
    (sinyaller yine çıkabilsin) ama açılış boşluğu, mum içi biçim (renk, gövde, fitil) ve her mumun hacmi değişir:
    gelecekteki bir mumun yalnız biçimini (ör. sonraki mumun rengini) kullanan sızıntı da görünür. Gelecekte mum da
    silinir (t0+1, t0+2 ve saatlerin ~%3'ü; bütün alanlar NaN): bir sonraki mumun yalnız VARLIĞINA bakan sızıntı da."""
    rng = np.random.default_rng(tohum)
    yeni = {}
    for j, (s, mum) in enumerate(piyasa.items()):
        sigma, hacim = oynaklik[s]
        o2, h2, l2, c2, v2 = rastgele_mumlar(len(mum[0]), tohum * 100 + j, 1.0, sigma, hacim)
        f = mum[3][t0] / c2[t0] * rng.uniform(0.97, 1.03)
        yeni[s] = tuple(np.r_[x[:t0 + 1], (y * f)[t0 + 1:]] for x, y in zip(mum[:4], (o2, h2, l2, c2))) + \
            (np.r_[mum[4][:t0 + 1], (v2 / f)[t0 + 1:]],)
        sil = np.r_[t0 + 1:t0 + 3, t0 + 3 + np.flatnonzero(rng.random(len(mum[0]) - t0 - 3) < 0.03)]
        for x in yeni[s]:
            x[sil[sil < len(x)]] = np.nan
    return yeni


# ------------------------------------------------------------------------------------------
# Gelecek bilgisi ve tanımlar
# ------------------------------------------------------------------------------------------
def test_gostergeler_sinyaller_ve_ozellikler_gelecegi_kullanmaz():
    """Birçok kesme noktası t0 (her kuralın ve kombo bileşeninin sinyal verdiği mumlar dahil): t0'dan sonrası bağımsız
    bir gelecekle değiştirilince (mum silme dahil) t0 ve öncesindeki radar, BTC bağlamı (ADX, rejim), göstergeler (ADX,
    SMA20, alt bant), sinyaller ve özellikler aynı kalmalı. Bir paritede kesme 1700'den hemen önce 3 saatlik boşluk var:
    ADX'i yok olan saatler (boşluktan sonraki 14 saat) kesmeyi kapsar; geriden doldurma (bfill) gibi sızıntı görünür."""
    n = 2200
    ts = T0 + SAAT * np.arange(n, dtype=np.int64)
    piyasa = {'BTC/USDT': rastgele_mumlar(n, 0, 20000, 0.004, 5e7)}
    oynaklik = {'BTC/USDT': (0.004, 5e7)}
    for i in range(13):
        piyasa[f'C{i:02d}/USDT'] = rastgele_mumlar(n, i + 1, 10.0, 0.012, 3e5 * (i + 1))
        oynaklik[f'C{i:02d}/USDT'] = (0.012, 3e5 * (i + 1))
    piyasa['G00/USDT'] = rastgele_mumlar(n, 70, 10.0, 0.012, 2e6)
    for x in piyasa['G00/USDT']:
        x[1695:1698] = np.nan                                                   # kesme 1700'den 2 saat önce biten boşluk
    oynaklik['G00/USDT'] = (0.012, 2e6)
    for i in range(2):                                                          # ERKEN_BIRIKIM kurulumları
        piyasa[f'E{i:02d}/USDT'] = birikim_piyasasi(n, 50 + i, faz=29 * i, hacim=1e7)
        oynaklik[f'E{i:02d}/USDT'] = (0.004, 1e7)
    for i in range(2):                                                          # YATAY_DONUS kurulumları
        piyasa[f'D{i:02d}/USDT'] = donus_piyasasi(n, 60 + i, faz=13 * i)
        oynaklik[f'D{i:02d}/USDT'] = (0.002, 1e6)
    once = _hepsini_hesapla(piyasa, ts)
    assert np.isnan(once['g']['G00/USDT']['adx14'][1695:1712]).all()           # 1700, ADX'siz saatlerin içinde
    kesmeler = {1500, 1700}
    kombo_anahtar = [f"{k}:{b}" for k, t in sl.KOMBO_TREND.items() for b in (t, sl.KOMBO_YATAY)]
    for kural in ('BOT_VEKILI', 'ERKEN_BIRIKIM', 'SIKISMA_KIRILIM', 'TREND_DIP_RSI2', 'YUKSEK_ISABET', 'YATAY_DONUS',
                  *kombo_anahtar):
        anlar = sorted({int(t) for s in piyasa for t in np.flatnonzero(once['s'][s][kural]) if 800 <= t < n - 2})
        assert anlar, kural                                                     # her kural en az bir kesmede sinyalde
        kesmeler.update(anlar[::max(1, len(anlar) // 5)][:5])
    for ad in ('TREND_YUKARI', 'TREND_ASAGI', 'YATAY'):
        assert (once['btc']['rejim'][800:] == ad).mean() > 0.1, ad             # üç rejim de var
    assert np.isfinite(once['g']['D00/USDT']['adx14'][800:]).all()
    for t0 in sorted(kesmeler):
        sonra = _hepsini_hesapla(_gelecegi_degistir(piyasa, oynaklik, t0, t0), ts)
        k = t0 + 1
        np.testing.assert_array_equal(once['radar'][:k], sonra['radar'][:k], err_msg=f"radar t0={t0}")
        assert not np.array_equal(once['radar'][k:], sonra['radar'][k:])       # bozma gerçekten etkili
        for ad in once['btc']:
            np.testing.assert_array_equal(once['btc'][ad][:k], sonra['btc'][ad][:k], err_msg=f"btc {ad} t0={t0}")
        for s in piyasa:
            for ad, x in once['g'][s].items():
                np.testing.assert_array_equal(x[:k], sonra['g'][s][ad][:k], err_msg=f"{s} {ad} t0={t0}")
            for kural, x in once['s'][s].items():
                np.testing.assert_array_equal(x[:k], sonra['s'][s][kural][:k], err_msg=f"{s} {kural} t0={t0}")
            for ad, x in once['oz'][s].items():
                np.testing.assert_array_equal(x[:k], sonra['oz'][s][ad][:k], err_msg=f"{s} {ad} t0={t0}")


def test_gosterge_tanimlari_kaba_hesapla_ayni():
    n = 1600
    o, h, l, c, v = rastgele_mumlar(n, 3)
    for dizi in (o, h, l, c, v):
        dizi[[400, 401, 1000]] = np.nan                                         # eksik saatler
    g = sl.gostergeler(o, h, l, c, v)
    qv = v * c
    # Bollinger genişliğinin yüzdeliği: bbw[t-720..t-1] içinde bbw[t-1]'e eşit/küçük olanların oranı
    sd = pd.Series(c).rolling(20, min_periods=20).std(ddof=0).to_numpy()
    bbw = 4 * sd / pd.Series(c).rolling(20, min_periods=20).mean().to_numpy()
    for t in (760, 900, 1100, 1599):
        w = bbw[t - 720:t]
        w = w[np.isfinite(w)]
        beklenen = (w <= bbw[t - 1]).sum() / len(w) if len(w) >= 648 and np.isfinite(bbw[t - 1]) else np.nan
        assert g['bbw_pct'][t] == pytest.approx(beklenen, nan_ok=True), t
    t = 1500
    med = np.nanmedian(qv[t - 173:t - 5])
    assert g['vol_ratio6'][t] == pytest.approx(qv[t - 5:t + 1].mean() / med)
    assert g['surekli6'][t] == (qv[t - 5:t + 1] > med).sum()
    assert g['vol_ratio14'][t] == pytest.approx(qv[t] / qv[t - 14:t].mean())
    assert g['qv_ort20'][t] == pytest.approx(qv[t - 20:t].mean())
    assert g['high24_prev'][t] == pytest.approx(h[t - 24:t].max())
    assert g['vol24'][t] == pytest.approx(qv[t - 23:t + 1].sum())
    assert g['ret24'][t] == pytest.approx(c[t] / c[t - 24] - 1) and g['ret6'][t] == pytest.approx(c[t] / c[t - 6] - 1)
    assert g['ort_gunluk_hacim30'][t] == pytest.approx(np.nanmean(qv[t - 719:t + 1]) * 24)
    assert g['sma5'][t] == pytest.approx(c[t - 4:t + 1].mean())
    assert g['sma20'][t] == pytest.approx(c[t - 19:t + 1].mean())
    assert g['bb_alt'][t] == pytest.approx(c[t - 19:t + 1].mean() - 2 * c[t - 19:t + 1].std(ddof=0))
    assert g['bb_ust'][t] == pytest.approx(c[t - 19:t + 1].mean() + 2 * c[t - 19:t + 1].std(ddof=0))
    assert np.isnan(g['sma20'][1000 + 19]) and np.isfinite(g['sma20'][1000 + 20])
    assert np.isnan(g['vol24'][1000 + 23]) and np.isfinite(g['vol24'][1000 + 24])  # pencere zamana göre
    # coinin ADX'i (YATAY_DONUS koşulu) son 15 saat dolu değilse yok: boşluktan sonraki 14 saatte bayat ya da sonraki
    # değerle doldurulmuş ADX yok, 15. saatte var
    assert np.isfinite(g['adx14'][[399, 999]]).all()
    assert np.isnan(g['adx14'][400:416]).all() and np.isfinite(g['adx14'][416])
    assert np.isnan(g['adx14'][1000:1015]).all() and np.isfinite(g['adx14'][1015])
    s = sl.sinyaller(g, np.ones(n, bool), None, 0, 0, np.full(n, 'YATAY'))
    assert not s['YATAY_DONUS'][1000:1015].any()
    assert np.isnan(g['ret24'][1000 + 24]) and np.isnan(g['ret24'][1000])
    # EMA ve Wilder RSI: elle döngü (eksik saat öncesi bölümde)
    ema, rsi_g, rsi_l = c[0], 0.0, 0.0
    a = 2 / 21
    for i in range(1, 400):
        ema = a * c[i] + (1 - a) * ema
        d = c[i] - c[i - 1]
        if i == 1:
            rsi_g, rsi_l = max(d, 0), max(-d, 0)
        else:
            rsi_g = rsi_g + (max(d, 0) - rsi_g) / 14
            rsi_l = rsi_l + (max(-d, 0) - rsi_l) / 14
    assert g['ema20'][399] == pytest.approx(ema)
    assert g['rsi14'][399] == pytest.approx(100 - 100 / (1 + rsi_g / rsi_l))
    assert not g['gecmis'][718] and g['gecmis'][719 + 2]                       # 720 dolu saat (2 eksik)


def test_ema_atr_rsi_eksik_saati_atlar_pandas_surumunden_bagimsiz():
    """Eksik saat atlanır (ignore_na=True anlamı): pandas 2.x ve 3.x NaN satırdan sonraki ağırlığı farklı verdiği için
    sonuç açık bir özyinelemeyle tanımlanır ve elle hesapla aynı olmalı."""
    np.testing.assert_allclose(sl._ewm(np.array([1.0, np.nan, np.nan, 3.0]), 0.5), [1.0, np.nan, np.nan, 2.0])
    n = 900
    o, h, l, c, v = rastgele_mumlar(n, 11)
    for dizi in (o, h, l, c, v):
        dizi[[300, 301, 302, 600]] = np.nan
    g = sl.gostergeler(o, h, l, c, v)
    ema, atr, a = None, None, 2 / 21
    onceki = np.nan
    for i in range(n):
        if np.isnan(c[i]):
            onceki = np.nan
            continue
        ema = c[i] if ema is None else a * c[i] + (1 - a) * ema
        tr = h[i] - l[i] if np.isnan(onceki) else max(h[i] - l[i], abs(h[i] - onceki), abs(l[i] - onceki))
        atr = tr if atr is None else atr + (tr - atr) / 14
        onceki = c[i]
        if i in (303, 304, 601, 899):
            assert g['ema20'][i] == pytest.approx(ema, rel=1e-12), i
            assert g['atr_pct'][i] == pytest.approx(atr / c[i], rel=1e-12), i
    assert np.isnan(g['ema20'][301]) and np.isnan(g['rsi14'][301])


def test_eksik_saatten_sonra_bayat_rsi_ile_sinyal_yok():
    """İki düşüş mumu (RSI2 ~2), 3 eksik saat, sonra son kapanışın %6 üstünde ilk mum: eski RSI2 boşluğu görmeden ~2
    kalıyor ve TREND_DIP_RSI2 sinyal veriyordu. RSI, son n+1 saatin kapanışı dolu değilse yoktur."""
    n = 1500
    rng = np.random.default_rng(1)
    c = 100 * np.exp(np.cumsum(np.full(n, 0.0005) + rng.normal(0, 0.002, n)))
    c[1000], c[1001] = c[999] * 0.99, c[999] * 0.98
    c[1002:1005] = np.nan
    c[1005] = c[1001] * 1.06
    c[1006:] = c[1005] * np.exp(np.cumsum(rng.normal(0, 0.002, n - 1006)))
    o = np.r_[c[0], c[:-1]]
    o[1005] = c[1005]
    h, l = np.fmax(o, c) * 1.001, np.fmin(o, c) * 0.999
    v = np.full(n, 1e6) / c
    for x in (o, h, l, v):
        x[1002:1005] = np.nan
    g = sl.gostergeler(o, h, l, c, v)
    s = sl.sinyaller(g, np.ones(n, bool), None, 0, 0)
    assert g['rsi2'][1001] < 10 and s['TREND_DIP_RSI2'][1001]
    assert np.isnan(g['rsi2'][1005:1007]).all() and np.isfinite(g['rsi2'][1007])  # c[t-2..t] dolu olmalı
    assert np.isnan(g['rsi14'][1005:1019]).all() and np.isfinite(g['rsi14'][1019])
    assert not s['TREND_DIP_RSI2'][1005:1007].any()


def test_izgara_eksik_saat_ve_bozuk_veri():
    g0 = T0
    d = np.array([[g0, 1, 2, 0.5, 1.5, 10], [g0 + 2 * SAAT, 1, 2, 0.5, 1.5, 0],     # sıfır hacim
                  [g0 + 3 * SAAT, 0, 2, 0.5, 1.5, 10], [g0 + 4 * SAAT + 5, 1, 2, 1, 1, 1],  # sıfır fiyat, hizasız
                  [g0 - SAAT, 1, 1, 1, 1, 1], [g0 + 9 * SAAT, 1, 1, 1, 1, 1]])          # ızgara dışı
    O, H, L, C, V = sl.izgaraya_yerlestir(d, g0, 5)
    assert C[0] == 1.5 and V[0] == 10
    assert np.isnan(C[1]) and np.isnan(V[1])                                    # eksik saat
    assert C[2] == 1.5 and np.isnan(V[2])                                       # yalnız hacim NaN
    assert np.isnan([O[3], H[3], L[3], C[3], V[3]]).all()                       # bozuk mum tamamen NaN
    assert np.isnan(C[4])


def test_radar_ilk10_hacim_esigi_ve_siralama():
    R = np.array([[0.1, 0.5, 0.3, 0.9, np.nan], [0.2, 0.1, 0.3, 0.0, 0.4]])
    V = np.array([[2e7, 2e7, 2e7, 1e7, 2e7], [2e7] * 5])
    r = sl.radar_ilk10(R, V, esik=1.2e7, k=2)
    assert r[0].tolist() == [False, True, True, False, False]                   # 0.9 hacimsiz, NaN dışarıda
    assert r[1].tolist() == [False, False, True, False, True]


# ------------------------------------------------------------------------------------------
# Çıkış motoru
# ------------------------------------------------------------------------------------------
def _yol(mumlar):
    return [list(x) for x in zip(*mumlar)]


def test_kademeli_ilk_stop_yuzde2_kaymali():
    O, H, L, C = _yol([(100, 100.5, 97.9, 98.5)])                               # kırmızı: A -> Y -> D
    b, giris, cikis, tip = sl.cikis_simule('KADEMELI', O, H, L, C, None, 0, 1.0)
    assert giris == pytest.approx(100 * (1 + SLIP_G))
    assert (b, tip) == (0, 'STOP') and cikis == pytest.approx(giris * 0.98 * (1 - SLIP_S))
    r = sl.net_getiri(giris, cikis, 1.0)
    assert r == pytest.approx(cikis * (1 - FEE) / (giris * (1 + FEE)) - 1)


def test_bosluklu_acilis_stopun_altinda_acilistan_dolar():
    O, H, L, C = _yol([(100, 100.2, 99.8, 100), (95, 96, 94, 95.5)])
    b, giris, cikis, tip = sl.cikis_simule('KADEMELI', O, H, L, C, None, 0, 1.0)
    assert (b, tip) == (1, 'STOP') and cikis == pytest.approx(95 * (1 - SLIP_S))


def test_kademe_stop_seviyeleri():
    assert sl.kademe_stopu(104.9, 100) == pytest.approx(98.0)
    assert sl.kademe_stopu(105.0, 100) == pytest.approx(105 * 0.97)
    assert sl.kademe_stopu(109.9, 100) == pytest.approx(109.9 * 0.97)
    assert sl.kademe_stopu(110.0, 100) == pytest.approx(110 * 0.96)
    assert sl.kademe_stopu(149.9, 100) == pytest.approx(149.9 * 0.96)
    assert sl.kademe_stopu(150.0, 100) == pytest.approx(135.0)


KADEME_MUMLARI = [(100, 100, 100, 100), (100, 106, 99.9, 105.9), (105.9, 112, 105, 111), (111, 149, 110, 148),
                  (148, 150, 147, 149.5)]


def test_kademeli_basamaklar_ve_stop_yalniz_yukari():
    # zirve 149 -> stop 149*0.96 = 143.04; zirve 150 (%50) -> 150*0.90 = 135 olurdu ama stop aşağı inmez
    O, H, L, C = _yol(KADEME_MUMLARI + [(149.5, 149.6, 140, 141)])
    b, giris, cikis, tip = sl.cikis_simule('KADEMELI', O, H, L, C, None, 0, 0.0)
    assert giris == 100 and (b, tip) == (5, 'IZ_STOP') and cikis == pytest.approx(149 * 0.96)
    # %70 zirve: 170*0.90 = 153 > 143.04 -> stop yükselir
    O, H, L, C = _yol(KADEME_MUMLARI + [(149.5, 170, 149, 169), (169, 169.5, 152, 152.5)])
    b, _, cikis, tip = sl.cikis_simule('KADEMELI', O, H, L, C, None, 0, 0.0)
    assert (b, tip) == (6, 'IZ_STOP') and cikis == pytest.approx(153.0)
    # her basamak: 106 -> 102.82, 112 -> 107.52
    O, H, L, C = _yol(KADEME_MUMLARI[:2] + [(105.9, 106, 102.5, 103)])
    assert sl.cikis_simule('KADEMELI', O, H, L, C, None, 0, 0.0)[2] == pytest.approx(106 * 0.97)
    O, H, L, C = _yol(KADEME_MUMLARI[:3] + [(111, 111.5, 107, 108)])
    assert sl.cikis_simule('KADEMELI', O, H, L, C, None, 0, 0.0)[2] == pytest.approx(112 * 0.96)


def test_yesil_ve_kirmizi_mum_yol_sirasi():
    duz = (100, 100, 100, 100)
    # yeşil: A(103) -> D(102.5, stop hâlâ 98) -> Y(106, stop 102.82) -> K(104): çıkış yok, veri biter -> açık
    O, H, L, C = _yol([duz, (103, 106, 102.5, 104)])
    assert sl.cikis_simule('KADEMELI', O, H, L, C, None, 0, 0.0) is None
    # kırmızı: A -> Y(106, stop 102.82) -> D(102.5): aynı mumda stop
    O, H, L, C = _yol([duz, (105, 106, 102.5, 104)])
    b, _, cikis, tip = sl.cikis_simule('KADEMELI', O, H, L, C, None, 0, 0.0)
    assert (b, tip) == (1, 'IZ_STOP') and cikis == pytest.approx(106 * 0.97)
    # yeşil mumda yeni zirve sonrası kapanış yeni stopun altında: dolum stop seviyesinden (kapanıştan değil)
    O, H, L, C = _yol([duz, (100, 106, 99.5, 102)])
    b, _, cikis, tip = sl.cikis_simule('KADEMELI', O, H, L, C, None, 0, 0.0)
    assert (b, tip) == (1, 'IZ_STOP') and cikis == pytest.approx(102.82)


def test_kademeli_168_saat_zaman_cikisi_ve_eksik_saat():
    duz = [(100, 100.1, 99.9, 100)] * 168
    O, H, L, C = _yol(duz)
    b, giris, cikis, tip = sl.cikis_simule('KADEMELI', O, H, L, C, None, 0, 1.0)
    assert (b, tip) == (167, 'ZAMAN') and cikis == pytest.approx(100 * (1 - SLIP_Z))
    assert sl.cikis_simule('KADEMELI', *_yol(duz[:167]), None, 0, 1.0) is None   # veri bitti: açık
    O, H, L, C = _yol(duz + [(100, 100.1, 99.9, 100)])
    for x in (O, H, L, C):
        x[50] = x[167] = float('nan')                                           # eksik saatler süreye sayılır
    assert sl.cikis_simule('KADEMELI', O, H, L, C, None, 0, 1.0)[0] == 168


def test_azami_surenin_son_mumu_eksikse_sonraki_acilista_zaman_cikisi():
    nan = float('nan')
    # KADEMELI: 168. mum (b=167) eksik; b=168 açılış 100, dip 97 (stop 98'in altında): stop artık işlemez
    O, H, L, C = _yol([(100, 100.1, 99.9, 100)] * 167 + [(nan,) * 4, (100, 100.2, 97, 99.5)])
    assert sl.cikis_simule('KADEMELI', O, H, L, C, None, 0, 1.0)[0::2] == (168, pytest.approx(100 * (1 - SLIP_Z)))
    assert sl.cikis_simule('KADEMELI', O, H, L, C, None, 0, 1.0)[3] == 'ZAMAN'
    # HEDEF_STOP: 24. mum eksik, sonraki mum hedefi geçer: 24 saat dolduğu için hedef yazılmaz
    O, H, L, C = _yol([(100, 100.2, 99.9, 100)] * 23 + [(nan,) * 4, (100, 102, 99.9, 101.9)])
    b, _, cikis, tip = sl.cikis_simule('HEDEF_STOP', O, H, L, C, None, 0, 1.0)
    assert (b, tip) == (24, 'ZAMAN') and cikis == pytest.approx(100 * (1 - SLIP_Z))
    # RSI2_CIKIS: 48. mum eksik
    O, H, L, C = _yol([(100, 100.5, 99.5, 100)] * 47 + [(nan,) * 4, (99, 99.5, 98, 98.5)])
    b, _, cikis, tip = sl.cikis_simule('RSI2_CIKIS', O, H, L, C, [101.0] * 49, 0, 1.0)
    assert (b, tip) == (48, 'ZAMAN') and cikis == pytest.approx(99 * (1 - SLIP_Z))


def test_hedef_stop_mum_ici_sira_ve_bosluk():
    duz = (100, 100, 100, 100)
    O, H, L, C = _yol([(100, 102, 95, 101)])                                    # yeşil: önce dip -> stop
    b, giris, cikis, tip = sl.cikis_simule('HEDEF_STOP', O, H, L, C, None, 0, 0.0)
    assert tip == 'STOP' and cikis == pytest.approx(96.0)
    O, H, L, C = _yol([(100, 102, 95, 99)])                                     # kırmızı: önce tepe -> hedef
    assert sl.cikis_simule('HEDEF_STOP', O, H, L, C, None, 0, 0.0)[2:] == (pytest.approx(101.5), 'HEDEF')
    O, H, L, C = _yol([(100, 102, 99, 99.5)])                                   # hedef dolumu hiç kaymaz
    _, giris, cikis, tip = sl.cikis_simule('HEDEF_STOP', O, H, L, C, None, 0, 1.0)
    assert tip == 'HEDEF' and cikis == pytest.approx(giris * 1.015)
    O, H, L, C = _yol([duz, (103, 104, 102.5, 103.5)])                          # hedefin üstünde açılış
    b, _, cikis, tip = sl.cikis_simule('HEDEF_STOP', O, H, L, C, None, 0, 2.0)
    assert (b, tip) == (1, 'HEDEF') and cikis == 103
    O, H, L, C = _yol([duz, (94, 95, 93, 94.5)])                                # stopun altında açılış
    assert sl.cikis_simule('HEDEF_STOP', O, H, L, C, None, 0, 1.0)[2:] == (pytest.approx(94 * (1 - SLIP_S)), 'STOP')
    O, H, L, C = _yol([(100, 100.5, 99.5, 100)] * 24)
    assert sl.cikis_simule('HEDEF_STOP', O, H, L, C, None, 0, 1.0)[0::3] == (23, 'ZAMAN')


def test_rsi2_cikisi_giris_mumu_dahil_kapanista():
    O, H, L, C = _yol([(100, 101.2, 99.5, 101)])
    b, giris, cikis, tip = sl.cikis_simule('RSI2_CIKIS', O, H, L, C, [100.5], 0, 1.0)
    assert (b, tip) == (0, 'SINYAL') and cikis == pytest.approx(101 * (1 - SLIP_Z))
    O, H, L, C = _yol([(100, 100.5, 99, 99.5), (99.5, 101, 99, 100.8)])
    S5 = [100.0, 100.9]                                                         # 100.8 SMA5'in altında
    assert sl.cikis_simule('RSI2_CIKIS', O, H, L, C, S5, 0, 1.0) is None
    S5 = [100.0, 100.5]
    assert sl.cikis_simule('RSI2_CIKIS', O, H, L, C, S5, 0, 1.0)[0::3] == (1, 'SINYAL')
    O, H, L, C = _yol([(100, 101, 92, 100.8)])                                  # aynı mumda stop önce gelir
    _, giris, cikis, tip = sl.cikis_simule('RSI2_CIKIS', O, H, L, C, [99.0], 0, 1.0)
    assert tip == 'STOP' and cikis == pytest.approx(giris * 0.93 * (1 - SLIP_S))
    O, H, L, C = _yol([(100, 100.5, 99.5, 100)] * 48)
    assert sl.cikis_simule('RSI2_CIKIS', O, H, L, C, [101.0] * 48, 0, 1.0)[0::3] == (47, 'ZAMAN')


def test_maliyet_katsayisi_k_ile_olceklenir_ve_getiri_formulu():
    O, H, L, C = _yol([(1.0, 1.0, 1.0, 1.0), (100, 100.5, 97.0, 98.0)])      # t=0 sinyali, giriş 1. mumda
    satirlar, acik = sl.kural_islemleri(np.array([True, False]), 'KADEMELI', O, H, L, C, None)
    satir = satirlar[0]
    assert acik == 0 and satir[:3] == (0, 1, 1)
    for k, r in zip(sl.K_LISTESI, satir[6:]):
        giris = 100 * (1 + SLIP_G * k)
        cikis = giris * 0.98 * (1 - SLIP_S * k)
        assert r == pytest.approx(cikis * (1 - FEE * k) / (giris * (1 + FEE * k)) - 1, abs=1e-12)
    assert satir[3] == pytest.approx(100.05) and satir[5] == 'STOP'
    ts = T0 + SAAT * np.arange(2, dtype=np.int64)
    df = sl.islem_tablosu('ERKEN_BIRIKIM', 'A/USDT', satirlar, ts, None)
    assert df['giris_ts'][0] == ts[1] and df['cikis_ts'][0] == ts[1] + SAAT  # çıkış: çıkış mumunun SONU
    assert df['sure_saat'][0] == 1


def test_islem_dizisi_yalniz_k1_ile_belirlenir():
    """1. işlem: k=1 stopu (96.048) 2. mumda vurulur, k=0.5 stopu (96.024) vurulmaz ve k=0.5 veri sonuna dek açık kalır.
    Eskiden işlem atılıyor ve döngü kırılıyordu (2. işlem de kayboluyordu). Doğrusu: 2 işlem, açık 0; k=0.5 getirisi
    son kapanıştan."""
    n = 12
    O = [100.0, 100.0, 100.0] + [98.0] * (n - 3)
    H = [100.2, 100.2, 100.1] + [98.3] * (n - 3)
    L = [99.9, 99.9, 96.03] + [97.8] * (n - 3)
    C = [100.0, 100.0, 99.0] + [98.0] * (n - 3)
    H[6] = 99.6
    sinyal = np.zeros(n, bool)
    sinyal[[0, 4]] = True
    assert sl.cikis_simule('HEDEF_STOP', O, H, L, C, None, 1, 0.5) is None
    satirlar, acik = sl.kural_islemleri(sinyal, 'HEDEF_STOP', O, H, L, C, None)
    assert acik == 0 and [x[:3] for x in satirlar] == [(0, 1, 2), (4, 5, 6)]
    assert [x[5] for x in satirlar] == ['STOP', 'HEDEF']
    r05 = sl.net_getiri(100 * (1 + SLIP_G * 0.5), 98.0 * (1 - SLIP_Z * 0.5), 0.5)
    assert satirlar[0][6] == pytest.approx(r05)
    g1 = 100 * (1 + SLIP_G)
    assert satirlar[0][7] == pytest.approx(sl.net_getiri(g1, g1 * 0.96 * (1 - SLIP_S), 1.0))


def test_veri_sonundaki_girisler_sonucuna_gore_secilmez():
    """Azami tutma süresi veride kalmayan girişler alınmaz (çabuk kapansa bile): yoksa veri sonunda yalnız çabuk
    kapanan işlemler kalır (KADEMELI'de hızlı stoplar, HEDEF_STOP'ta hızlı hedefler)."""
    n = 40
    mumlar = [(100, 100.2, 99.8, 100)] * n
    for b in (12, 22):
        mumlar[b] = (100, 102, 99.8, 100)                                       # hedef bu mumlarda
    O, H, L, C = _yol(mumlar)
    sinyal = np.zeros(n, dtype=bool)
    sinyal[[10, 20]] = True
    satirlar, acik = sl.kural_islemleri(sinyal, 'HEDEF_STOP', O, H, L, C, None, son_e=n - sl.HS_AZAMI)
    assert [(x[0], x[2]) for x in satirlar] == [(10, 12)] and acik == 0
    # parite_islemleri: analiz sonu ve veri sonu ayrı ayrı uygulanır
    o, h, l, c, v = rastgele_mumlar(1600, 5)
    ts = T0 + SAAT * np.arange(1600, dtype=np.int64)
    btc = sl.btc_baglami(np.linspace(100, 200, 1600))
    tablolar, _ = sl.parite_islemleri('A/USDT', o, h, l, c, v, ts, btc, None, ['TREND_DIP_RSI2', 'YUKSEK_ISABET'],
                                      0, 0, 0, 1500)
    df = pd.concat(tablolar)
    son = df['giris_ts'].max()
    assert len(df) > 20 and son < ts[1500] and son >= ts[1400]
    tablolar, _ = sl.parite_islemleri('A/USDT', o, h, l, c, v, ts, btc, None, ['TREND_DIP_RSI2'], 0, 0, 0, None)
    df = pd.concat(tablolar)
    assert df['giris_ts'].max() <= ts[1600 - sl.RSI2_AZAMI]


def test_tek_pozisyon_sonraki_sinyal_cikistan_sonra_ve_acik_kalan_haric():
    n = 40
    mumlar = [(100, 100.2, 99.8, 100)] * n
    for b in (5, 9):
        mumlar[b] = (100, 102, 99.8, 100)                                       # hedef (+%1.5) bu mumlarda
    O, H, L, C = _yol(mumlar)
    for x in (O, H, L, C):
        x[31] = float('nan')                                                    # t=30 sinyalinin giriş mumu yok
    sinyal = np.zeros(n, dtype=bool)
    sinyal[[0, 1, 2, 5, 6, 7, 30, 38]] = True
    satirlar, acik = sl.kural_islemleri(sinyal, 'HEDEF_STOP', O, H, L, C, None)
    assert [(s[0], s[1], s[2]) for s in satirlar] == [(0, 1, 5), (6, 7, 9)]   # t=5 çıkış mumu: engelli
    assert acik == 1                                                            # t=38: veri bitti, hariç
    satirlar, _ = sl.kural_islemleri(sinyal, 'HEDEF_STOP', O, H, L, C, None, ilk_e=3)
    assert satirlar[0][0] == 2                                                  # giriş ilk_e'den önce olamaz


def test_btc_gunluk_ve_btc_trend_histerezis():
    ts = T0 + SAAT * np.arange(24 * 3 + 5, dtype=np.int64)
    C = np.arange(len(ts), dtype=float)
    C[30] = np.nan                                                              # 2. gün eksik
    gun, kap = sl.btc_gunluk(ts, C)
    assert gun.tolist() == [T0, T0 + 2 * GUN] and kap.tolist() == [23.0, 71.0]
    kapanis = np.r_[np.full(100, 100.0), [103.0], np.full(9, 105.0), [95.0], np.full(5, 104.0)]
    gun_bas = T0 + GUN * np.arange(len(kapanis), dtype=np.int64)
    df, acik = sl.btc_trend_islemleri(gun_bas, kapanis, T0)
    assert len(df) == 1 and acik == 1                                           # ikinci giriş açık kaldı
    s = df.iloc[0]
    assert s['giris_ts'] == T0 + 101 * GUN and s['cikis_ts'] == T0 + 111 * GUN
    for k, kol in sl.K_SUTUN.items():
        assert s[kol] == pytest.approx(sl.net_getiri(103 * (1 + SLIP_G * k), 95 * (1 - SLIP_Z * k), k))
    df2, _ = sl.btc_trend_islemleri(gun_bas, kapanis, T0 + 103 * GUN)
    assert df2.iloc[0]['giris_ts'] == T0 + 103 * GUN                            # dönem başından önce giriş yok
    df3, acik3 = sl.btc_trend_islemleri(gun_bas, kapanis, T0, T0 + 101 * GUN)
    assert len(df3) == 0 and acik3 == 0                                         # analiz sonundan sonra giriş yok
    # 100. gün kapanışı 102.5, o günün MA100'ü 100.025 (eşik 102.03): girilir. Ertesi günün 150'si MA'ya karışırsa
    # (MA 102.525, eşik 104.6) o gün girilmez: MA yalnız o güne kadarki kapanışlarla
    kapanis = np.r_[np.full(100, 100.0), [102.5], np.full(3, 150.0), np.full(60, 50.0)]
    gun_bas = T0 + GUN * np.arange(len(kapanis), dtype=np.int64)
    df4, _ = sl.btc_trend_islemleri(gun_bas, kapanis, T0)
    assert df4.iloc[0]['giris_ts'] == T0 + 101 * GUN and df4.iloc[0]['cikis_ts'] == T0 + 105 * GUN


# ------------------------------------------------------------------------------------------
# Rejim (ANALIZ §12.1): ADX, BTC rejimi, YATAY_DONUS, ORTA_BANT çıkışı, rejime göre geçiş (kombolar)
# ------------------------------------------------------------------------------------------
def _adx_dongu(H, L, C, n=14):
    """Bağımsız düz döngü (Wilder ADX): eksik saat atlanır; önceki saat eksikse TR = H - L ve DM yok; DI'lar ATR 0 ise
    0; DX toplam 0 ise 0; ADX yalnız son n+1 saat doluysa."""
    out = np.full(len(C), np.nan)
    atr = pdm = mdm = adx = onceki = None
    son_eksik = -1
    for i in range(len(C)):
        if not (np.isfinite(H[i]) and np.isfinite(L[i]) and np.isfinite(C[i])):
            onceki, son_eksik = None, i
            continue
        h, l, c = H[i], L[i], C[i]
        if onceki is None:
            tr, dm = h - l, None
        else:
            ph, pl, pc = onceki
            tr = max(h - l, abs(h - pc), abs(l - pc))
            up, dn = h - ph, pl - l
            dm = (up if up > dn and up > 0 else 0.0, dn if dn > up and dn > 0 else 0.0)
        atr = tr if atr is None else atr + (tr - atr) / n
        if dm is not None:
            pdm = dm[0] if pdm is None else pdm + (dm[0] - pdm) / n
            mdm = dm[1] if mdm is None else mdm + (dm[1] - mdm) / n
            pdi, mdi = (100 * pdm / atr, 100 * mdm / atr) if atr > 0 else (0.0, 0.0)
            dx = 100 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi > 0 else 0.0
            adx = dx if adx is None else adx + (dx - adx) / n
            if i - son_eksik > n:
                out[i] = adx
        onceki = (h, l, c)
    return out


def test_adx_elle_dongu_ve_pandas_ta_ile_ayni():
    """ADX iki bağımsız kaynağa karşı: (1) düz Python döngüsü, eksik saatlerle birlikte (rel 1e-9, NaN yerleri aynı);
    (2) canlı botun kullandığı pandas_ta.adx (boşluksuz veride, 300 mumluk ısınmadan sonra fark < 0.5; başlangıç
    tohumu farkı ısınmada söner, ölçülen fark ~1e-8). Fiyat adımına yuvarlanmış veride (DM eşitlikleri) de."""
    import pandas_ta as ta
    n = 2000
    o, h, l, c, v = rastgele_mumlar(n, 3)
    ref = ta.adx(pd.Series(h), pd.Series(l), pd.Series(c), length=14)['ADX_14'].to_numpy(dtype=float)
    a = sl._adx(h, l, c)
    assert np.isnan(a[:14]).all() and np.isfinite(a[14:]).all()
    assert np.abs(a[300:] - ref[300:]).max() < 0.5
    np.testing.assert_allclose(a, _adx_dongu(h, l, c), rtol=1e-9)
    for x in (h, l, c):
        x[[400, 401, 402, 1000]] = np.nan
    a, d = sl._adx(h, l, c), _adx_dongu(h, l, c)
    np.testing.assert_allclose(a, d, rtol=1e-9)                                 # NaN yerleri de aynı olmalı
    assert np.isnan(a[400:417]).all() and np.isfinite(a[417])                   # son 15 saat dolu: 403..417
    # düz fiyat (ATR 0): DI'lar 0, DX 0, ADX 0 (NaN değil); H = L = C
    duz = np.full(50, 100.0)
    assert (sl._adx(duz, duz, duz)[14:] == 0).all()
    # fiyat adımına (tick) yuvarlanmış veri (gerçek borsa gibi): up == down > 0 eşitlikleri sık; tanım gereği iki DM de
    # 0 (">" kesin). 1/128 adımı ikili sistemde tam: farklar ve eşitlikler kayan noktada da kesin
    o, h, l, c, v = rastgele_mumlar(n, 9, 10.0, 0.002)
    h, l, c = (np.round(x * 128) / 128 for x in (h, l, c))
    up, dn = h[1:] - h[:-1], l[:-1] - l[1:]
    assert ((up == dn) & (up > 0)).sum() >= 30
    a = sl._adx(h, l, c)
    np.testing.assert_allclose(a, _adx_dongu(h, l, c), rtol=1e-9)
    ref = ta.adx(pd.Series(h), pd.Series(l), pd.Series(c), length=14)['ADX_14'].to_numpy(dtype=float)
    assert np.abs(a[300:] - ref[300:]).max() < 0.5


def test_adx_ve_rejim_gelecegi_kullanmaz_bosluktan_sonra_bayat_deger_yok():
    """t0'dan sonrası (H/L/C) değiştirilince t0'a kadarki ADX ve rejim aynı; boşluktan hemen sonra (son 15 saat dolu
    değilken) ADX ve rejim yok: boşluktaki sert hareketi görmeyen eski değerle rejim/sinyal üretilmez."""
    n = 1500
    o, h, l, c, v = rastgele_mumlar(n, 21, 20000, 0.004, 5e7)
    once_adx, once = sl._adx(h, l, c), sl.btc_baglami(c, h, l)
    for t0 in (300, 777, 1200, 1498):
        o2, h2, l2, c2, _ = rastgele_mumlar(n, t0, c[t0], 0.02)
        H, L, C = (np.r_[x[:t0 + 1], y[t0 + 1:]] for x, y in ((h, h2), (l, l2), (c, c2)))
        sonra = sl.btc_baglami(C, H, L)
        np.testing.assert_array_equal(sl._adx(H, L, C)[:t0 + 1], once_adx[:t0 + 1])
        for ad in ('adx14', 'rejim', 'ok'):
            np.testing.assert_array_equal(sonra[ad][:t0 + 1], once[ad][:t0 + 1], err_msg=f"{ad} t0={t0}")
        if t0 < n - 20:
            assert not np.array_equal(sonra['adx14'][t0 + 1:], once['adx14'][t0 + 1:])
    # boşluk: 3 saat eksik, sonra %8'lik sıçrama; eski ADX boşluğu görmeden aynen sürerdi
    h, l, c = h.copy(), l.copy(), c.copy()
    h[1000:1003] = l[1000:1003] = c[1000:1003] = np.nan
    c[1003:] *= 1.08
    h[1003:] *= 1.08
    l[1003:] *= 1.08
    b = sl.btc_baglami(c, h, l)
    assert np.isnan(b['adx14'][1000:1017]).all() and np.isfinite(b['adx14'][1017])   # son 15 saat: 1003..1017
    assert (b['rejim'][1000:1017] == 'BILINMIYOR').all() and b['rejim'][1017] != 'BILINMIYOR'
    assert np.isfinite(b['adx14'][999]) and b['rejim'][999] != 'BILINMIYOR'


def test_rejim_etiketleri_yukari_asagi_yatay():
    """Kurulmuş BTC serileri: istikrarlı yükseliş TREND_YUKARI, düşüş TREND_ASAGI, trendsiz gürültü YATAY (ısınmadan
    sonra saatlerin büyük çoğunluğunda). Eşik: ADX >= 20 trend; kapanış = EMA200 TREND_ASAGI; H/L yoksa BILINMIYOR."""
    n = 1500
    rng = np.random.default_rng(1)
    t = np.arange(n)
    for yon, beklenen in ((1, 'TREND_YUKARI'), (-1, 'TREND_ASAGI')):
        c = 20000 * np.exp(yon * 0.002 * t + rng.normal(0, 0.001, n))
        o = np.r_[c[0], c[:-1]]
        b = sl.btc_baglami(c, np.maximum(o, c) * 1.001, np.minimum(o, c) * 0.999)
        assert (b['rejim'][300:] == beklenen).mean() > 0.95, yon
    o, h, l, c, _ = yatay_btc(n)
    b = sl.btc_baglami(c, h, l)
    assert (b['rejim'][300:] == 'YATAY').mean() > 0.8
    assert set(b['rejim'][300:]) <= {'YATAY', 'TREND_YUKARI', 'TREND_ASAGI'}
    assert sl.btc_baglami(c)['rejim'].tolist() == ['BILINMIYOR'] * n            # H/L verilmedi: ADX yok
    # tanım, sınırlarda: ADX tam 20 trenddir; kapanış tam EMA200 ise TREND_ASAGI; NaN -> BILINMIYOR
    adx = np.array([19.999, 20.0, 20.0, 35.0, np.nan, 10.0, 10.0])
    C = np.array([100.0, 101.0, 100.0, 99.0, 100.0, 100.0, np.nan])
    ema = np.array([100.0, 100.0, 100.0, 100.0, 100.0, np.nan, 100.0])
    assert sl.rejim_belirle(adx, C, ema).tolist() == ['YATAY', 'TREND_YUKARI', 'TREND_ASAGI', 'TREND_ASAGI',
                                                       'BILINMIYOR', 'BILINMIYOR', 'BILINMIYOR']


def test_yatay_donus_sinyal_kosullari():
    """YATAY_DONUS: geçmiş + sonlu kapanış, BTC rejimi YATAY, coin ADX14 < 20, kapanış < alt bant, RSI14 < 30, 24s hacim
    >= min_hacim. Her koşul tek başına bozulunca sinyal kaybolur (sınırlar dahil)."""
    n = 1000
    o, h, l, c, v = rastgele_mumlar(n, 4)
    t = 900

    def sinyal(**degis):
        g = {k: np.array(x) for k, x in sl.gostergeler(o, h, l, c, v).items()}   # yazılabilir kopyalar
        g['c'][t], g['bb_alt'][t], g['rsi14'][t], g['adx14'][t], g['vol24'][t] = 100.0, 100.5, 25.0, 15.0, 6e6
        rejim = np.full(n, 'YATAY', dtype='<U12')
        for ad, x in degis.items():
            (rejim if ad == 'rejim' else g[ad])[t] = x
        s = sl.sinyaller(g, np.ones(n, bool), None, 5e6, 2e7, rejim)
        return bool(s['YATAY_DONUS'][t])
    assert sinyal()
    assert sinyal(adx14=19.99) and sinyal(vol24=5e6) and sinyal(rsi14=29.99) and sinyal(c=100.49)
    for degis in ({'rejim': 'TREND_YUKARI'}, {'rejim': 'TREND_ASAGI'}, {'rejim': 'BILINMIYOR'}, {'adx14': 20.0},
                  {'adx14': np.nan}, {'c': 100.5}, {'c': np.nan}, {'bb_alt': np.nan}, {'rsi14': 30.0},
                  {'rsi14': np.nan}, {'vol24': 5e6 - 1}, {'vol24': np.nan}, {'gecmis': False}):
        assert not sinyal(**degis), degis
    g = sl.gostergeler(o, h, l, c, v)
    assert 'YATAY_DONUS' not in sl.sinyaller(g, np.ones(n, bool), None, 5e6, 2e7)   # rejim yok: kural yok


def test_orta_bant_cikisi_elle():
    """ORTA_BANT: kapanış >= SMA20 olan ilk mumun kapanışında (giriş mumu dahil, eşitlik dahil); stop giriş -%3 mum içi
    yol kuralıyla (stop, kapanıştaki sinyalden önce), boşluklu açılış açılıştan dolar; SMA20 yoksa o mumda sinyal yok;
    en çok 24 saat (son mum eksikse sonraki açılış)."""
    nan = float('nan')
    O, H, L, C = _yol([(100, 101.2, 99.5, 101)])
    b, giris, cikis, tip = sl.cikis_simule('ORTA_BANT', O, H, L, C, [101.0], 0, 1.0)  # eşitlik: çıkar
    assert (b, tip) == (0, 'SINYAL') and cikis == pytest.approx(101 * (1 - SLIP_Z)) and giris == pytest.approx(100.05)
    assert sl.cikis_simule('ORTA_BANT', O, H, L, C, [101.0001], 0, 1.0) is None       # altında: çıkmaz
    O, H, L, C = _yol([(100, 100.5, 99, 99.5), (99.5, 101, 99, 100.8), (100.8, 101.5, 100.5, 101.2)])
    S = [100.0, nan, 101.0]                                                     # 1. mumda SMA20 yok
    assert sl.cikis_simule('ORTA_BANT', O, H, L, C, S, 0, 1.0)[0::3] == (2, 'SINYAL')
    for mum in ((100, 102, 96.9, 101.5), (100, 102, 96.9, 99)):                 # yeşil ve kırmızı: önce stop
        _, giris, cikis, tip = sl.cikis_simule('ORTA_BANT', *_yol([mum]), [99.0], 0, 1.0)
        assert tip == 'STOP' and cikis == pytest.approx(giris * 0.97 * (1 - SLIP_S))
    O, H, L, C = _yol([(100, 100, 97.2, 97.3)])                                 # -%2.7: KADEMELI'de stop olurdu
    assert sl.cikis_simule('ORTA_BANT', O, H, L, C, [99.0], 0, 1.0) is None
    O, H, L, C = _yol([(100, 100.2, 99.8, 99.9), (96, 96.5, 95.5, 96.2)])         # stopun altında açılış
    assert sl.cikis_simule('ORTA_BANT', O, H, L, C, [101.0] * 2, 0, 2.0)[2:] == (pytest.approx(96 * (1 - 2 * SLIP_S)),
                                                                                'STOP')
    O, H, L, C = _yol([(100, 100.5, 99.5, 100)] * 25)
    assert sl.cikis_simule('ORTA_BANT', O, H, L, C, [101.0] * 25, 0, 1.0)[0::3] == (23, 'ZAMAN')
    assert sl.cikis_simule('ORTA_BANT', *_yol([(100, 100.5, 99.5, 100)] * 23), [101.0] * 23, 0, 1.0) is None
    O, H, L, C = _yol([(100, 100.5, 99.5, 100)] * 23 + [(nan,) * 4, (99, 99.5, 96, 98.5)])
    b, _, cikis, tip = sl.cikis_simule('ORTA_BANT', O, H, L, C, [101.0] * 25, 0, 1.0)
    assert (b, tip) == (24, 'ZAMAN') and cikis == pytest.approx(99 * (1 - SLIP_Z))   # süre doldu: stop işlemez
    O, H, L, C = _yol([(100, 100.5, 99.5, 100)] * 24)                           # son mumda sinyal de varsa SINYAL
    assert sl.cikis_simule('ORTA_BANT', O, H, L, C, [101.0] * 23 + [100.0], 0, 1.0)[0::3] == (23, 'SINYAL')
    # RSI2_CIKIS değişmedi: eşitlikte çıkmaz (kesin büyük)
    assert sl.cikis_simule('RSI2_CIKIS', *_yol([(100, 101.2, 99.5, 101)]), [101.0], 0, 1.0) is None


def test_orta_bant_maliyet_k_ile_birebir():
    """k=0.5/1/2: giriş O(1 + SLIP_GIRIS k), stop giriş(k) x 0.97 x (1 - SLIP_SEVIYE k), sinyal çıkışı kapanış x
    (1 - SLIP_ZAMAN k); getiri komisyon k ile. Seviyeler giriş dolumuna bağlı."""
    O, H, L, C = _yol([(1.0, 1.0, 1.0, 1.0), (100, 100.5, 96.0, 96.5)])
    satirlar, acik = sl.kural_islemleri(np.array([True, False]), 'ORTA_BANT', O, H, L, C, [99.0, 99.0])
    assert acik == 0 and satirlar[0][:3] == (0, 1, 1) and satirlar[0][5] == 'STOP'
    for k, r in zip(sl.K_LISTESI, satirlar[0][6:]):
        giris = 100 * (1 + SLIP_G * k)
        assert r == pytest.approx(sl.net_getiri(giris, giris * 0.97 * (1 - SLIP_S * k), k), abs=1e-12)
    O, H, L, C = _yol([(1.0, 1.0, 1.0, 1.0), (100, 101, 99.5, 100.6)])
    satirlar, _ = sl.kural_islemleri(np.array([True, False]), 'ORTA_BANT', O, H, L, C, [99.0, 100.6])
    assert satirlar[0][5] == 'SINYAL'
    for k, r in zip(sl.K_LISTESI, satirlar[0][6:]):
        assert r == pytest.approx(sl.net_getiri(100 * (1 + SLIP_G * k), 100.6 * (1 - SLIP_Z * k), k), abs=1e-12)


def _kombo_mumlari(n=80):
    """Düz mumlar (100); 10. ve 40. mumlarda dip 97 (KADEMELI stopu -%2 vurulur, ORTA_BANT -%3 vurulmaz)."""
    mumlar = [(100, 100.2, 99.9, 100)] * n
    for b in (10, 40):
        mumlar[b] = (100, 100.1, 97.4, 97.6)
    mumlar[11] = mumlar[41] = (97.6, 100.1, 97.5, 100)
    return _yol(mumlar)


def test_kombo_tek_pozisyon_iki_bilesen_icin_ve_cikis_bilesene_gore():
    """Trend bileşeni t=5'te girer (KADEMELI); açıkken (t=7) ve çıkış mumunda (t=10) gelen YATAY_DONUS sinyali alınmaz;
    t=11'deki alınır (ORTA_BANT) ve açıkken gelen trend sinyali (t=13) alınmaz. Aynı -%2.6'lık dip trend işlemini
    (KADEMELI -%2) durdurur, yatay işlemi (ORTA_BANT -%3) durdurmaz. Aynı mumda iki sinyal: trend bileşeni."""
    O, H, L, C = _kombo_mumlari()
    n = len(O)
    S = [100.1] * n
    S[16] = S[70] = 99.9                                                        # 16. ve 70. mum: kapanış >= SMA20
    trend, yatay = np.zeros(n, bool), np.zeros(n, bool)
    trend[[5, 13, 30, 60]] = True
    yatay[[7, 10, 11, 36, 60]] = True
    bilesen = {'ERKEN_BIRIKIM': (trend, 'KADEMELI'), 'YATAY_DONUS': (yatay, 'ORTA_BANT')}
    satirlar, acik = sl.kombo_islemleri(bilesen, O, H, L, C, {'ORTA_BANT': S}, son_e={'KADEMELI': n - 1,
                                                                                        'ORTA_BANT': n - 1})
    assert [(x[0], x[2], x[5], x[9]) for x in satirlar] == [
        (5, 10, 'STOP', 'ERKEN_BIRIKIM'), (11, 16, 'SINYAL', 'YATAY_DONUS'), (30, 40, 'STOP', 'ERKEN_BIRIKIM')]
    assert acik == 1                    # t=60'ta ikisi birden: trend (KADEMELI) alınır, veri sonuna dek açık kalır
    yatay_once = {'YATAY_DONUS': (yatay, 'ORTA_BANT'), 'ERKEN_BIRIKIM': (trend, 'KADEMELI')}
    satirlar, acik = sl.kombo_islemleri(yatay_once, O, H, L, C, {'ORTA_BANT': S})
    assert satirlar[-1][0::9] == (60, 'YATAY_DONUS') and satirlar[-1][2] == 70 and acik == 0   # öncelik sırası
    assert satirlar[0][4] == pytest.approx(100 * (1 + SLIP_G) * 0.98 * (1 - SLIP_S))
    # tersi: yatay işlem açıkken gelen trend sinyali alınmaz; 10. mumdaki dip yatay işlemi durdurmaz
    trend[:], yatay[:] = False, False
    yatay[[3, 50]], trend[[8, 12, 51]] = True, True
    S = [100.1] * n
    S[12] = 99.9
    satirlar, _ = sl.kombo_islemleri(bilesen, O, H, L, C, {'ORTA_BANT': S})
    assert [(x[0], x[2], x[5], x[9]) for x in satirlar] == [(3, 12, 'SINYAL', 'YATAY_DONUS'),
                                                            (50, 74, 'ZAMAN', 'YATAY_DONUS')]
    # veri sonu kuralı bileşenin çıkış tipine göre: KADEMELI'nin 168 saati sığmaz, ORTA_BANT'ın 24 saati sığar
    trend[:], yatay[:] = False, False
    trend[20], yatay[45] = True, True
    satirlar, _ = sl.kombo_islemleri(bilesen, O, H, L, C, {'ORTA_BANT': S},
                                     son_e={'KADEMELI': n - 168, 'ORTA_BANT': n - 24})
    assert [(x[0], x[9]) for x in satirlar] == [(45, 'YATAY_DONUS')]


def test_kombo_ayrik_bilesenler_birlesimi_ve_k_degerleri():
    """Bileşen işlemleri hiç çakışmıyorsa kombo dizisi, iki bileşenin tek başına işlem dizilerinin birleşimidir (k=0.5/2
    getirileri dahil birebir)."""
    n = 3000
    o, h, l, c, v = rastgele_mumlar(n, 8, 10.0, 0.01)
    S = pd.Series(c).rolling(20, min_periods=20).mean().to_numpy()
    trend, yatay = np.zeros(n, bool), np.zeros(n, bool)
    trend[np.arange(100, n - 200, 400)] = True                                  # KADEMELI en çok 168 saat
    yatay[np.arange(300, n - 200, 400)] = True
    bilesen = {'BOT_VEKILI': (trend, 'KADEMELI'), 'YATAY_DONUS': (yatay, 'ORTA_BANT')}
    kombo, _ = sl.kombo_islemleri(bilesen, o, h, l, c, {'ORTA_BANT': S, 'RSI2_CIKIS': S * 0})
    a, _ = sl.kural_islemleri(trend, 'KADEMELI', o, h, l, c, None)
    b, _ = sl.kural_islemleri(yatay, 'ORTA_BANT', o, h, l, c, S)
    beklenen = sorted([x + ('BOT_VEKILI',) for x in a] + [x + ('YATAY_DONUS',) for x in b])
    assert len(kombo) == trend.sum() + yatay.sum() and kombo == beklenen
    assert {x[5] for x in b} >= {'SINYAL'} and {x[5] for x in a} & {'STOP', 'IZ_STOP'}


def test_parite_islemleri_kombo_rejim_bilesen_ve_veri_sonu(monkeypatch):
    """parite_islemleri (sinyaller elle verilir): kombo, trend bileşenini yalnız TREND_YUKARI saatlerinde, YATAY_DONUS'u
    yalnız YATAY saatlerinde alır; TREND_ASAGI ve BILINMIYOR saatlerinde iki bileşen de alım yapmaz. Bileşen kuralları
    istenmese de kombo hesaplanır. 'rejim' karar mumundaki rejim, 'bilesen' işlemin bileşeni. Veri sonu kuralı
    bileşenin çıkış tipine göre (KADEMELI 168, ORTA_BANT 24 saat); analiz sonundan sonra kombo girişi de yok."""
    n = 2400
    o, h, l, c, v = rastgele_mumlar(n, 12, 10.0, 0.01, 2e6)
    ts = T0 + SAAT * np.arange(n, dtype=np.int64)
    rejim = np.array(['TREND_YUKARI', 'YATAY', 'TREND_ASAGI', 'BILINMIYOR'] * (n // 400 + 1),
                     dtype='<U12').repeat(100)[:n]                              # 100 saatlik bloklar
    btc = sl.btc_baglami(np.linspace(100, 200, n))
    btc['rejim'], btc['adx14'] = rejim, np.full(n, 25.0)
    erken, bot, yatay = np.zeros(n, bool), np.zeros(n, bool), np.zeros(n, bool)
    for blok in range(n // 100):
        erken[blok * 100 + 10] = True                                           # her blokta (her rejimde)
        bot[blok * 100 + 20] = True
        yatay[blok * 100 + 60] = True                                           # gerçek kural yalnız YATAY'da verir
    yatay[n - 40] = True                                                        # son blok BILINMIYOR: yine alınmaz
    rejim[n - 30] = 'YATAY'
    yatay[n - 30] = True                                                        # ORTA_BANT 24 saati sığar
    rejim[n - 100:n - 60], erken[n - 90], bot[n - 90] = 'TREND_YUKARI', True, True   # KADEMELI 168 saati sığmaz
    o_sin = sl.sinyaller

    def sinyaller(g, btc_ok, radar, min_hacim, buyuk_hacim, rejim_=None):
        s = o_sin(g, btc_ok, radar, min_hacim, buyuk_hacim, rejim_)
        s.update(ERKEN_BIRIKIM=erken, BOT_VEKILI=bot, YATAY_DONUS=yatay)
        return s
    monkeypatch.setattr(sl, 'sinyaller', sinyaller)
    tablolar, acik = sl.parite_islemleri('A/USDT', o, h, l, c, v, ts, btc, np.ones(n, bool),
                                         ['REJIM_KOMBO', 'REJIM_KOMBO_BOT', 'ERKEN_BIRIKIM'], 0, 0, 800)
    df = pd.concat(tablolar, ignore_index=True)
    karar = ((df['giris_ts'] - SAAT - T0) // SAAT).to_numpy()                   # karar mumu t
    assert (df['rejim'].to_numpy() == rejim[karar]).all()
    for kombo, trend_ad, trend in (('REJIM_KOMBO', 'ERKEN_BIRIKIM', erken), ('REJIM_KOMBO_BOT', 'BOT_VEKILI', bot)):
        k = df[df['kural'] == kombo]
        kt = karar[(df['kural'] == kombo).to_numpy()]
        assert set(k['bilesen']) == {trend_ad, 'YATAY_DONUS'}
        assert (k['rejim'][k['bilesen'] == trend_ad] == 'TREND_YUKARI').all()
        assert (k['rejim'][k['bilesen'] == 'YATAY_DONUS'] == 'YATAY').all()
        assert set(k['cikis_tipi'][k['bilesen'] == 'YATAY_DONUS']) <= {'SINYAL', 'STOP', 'ZAMAN'}
        beklenen = sorted(t for t in np.flatnonzero((trend & (rejim == 'TREND_YUKARI')) | (yatay & (rejim == 'YATAY')))
                          if 800 <= t + 1 and t + 1 <= n - (168 if trend[t] else 24))
        assert sorted(kt.tolist()) == beklenen                                  # ayrık: bileşen sinyallerinin hepsi
        assert n - 30 in kt and n - 90 not in kt
    e = df[df['kural'] == 'ERKEN_BIRIKIM']
    assert set(e['bilesen']) == {'ERKEN_BIRIKIM'} and set(e['rejim']) == {'TREND_YUKARI', 'YATAY', 'TREND_ASAGI',
                                                                          'BILINMIYOR'}
    assert set(acik) == {'REJIM_KOMBO', 'REJIM_KOMBO_BOT', 'ERKEN_BIRIKIM'}
    # YATAY bileşeni ORTA_BANT ile SMA20'ye göre kapanır: tek başına hesaplanan dizinin aynısı (çakışma yok)
    sma20 = pd.Series(c).rolling(20, min_periods=20).mean().to_numpy()
    y1, _ = sl.kural_islemleri(yatay & (rejim == 'YATAY'), 'ORTA_BANT', o, h, l, c, sma20, ilk_e=800, son_e=n - 24)
    k = df[(df['kural'] == 'REJIM_KOMBO') & (df['bilesen'] == 'YATAY_DONUS')]
    assert k['cikis_tipi'].tolist() == [x[5] for x in y1] and 'SINYAL' in k['cikis_tipi'].tolist()
    np.testing.assert_allclose(k[['getiri_k05', 'getiri_k1', 'getiri_k2']].to_numpy(), [x[6:9] for x in y1])
    assert (k['sure_saat'] <= 24).all()
    # analiz sonu (analiz_sonu_i) kombolarda da: ondan sonra giriş yok. Yalnız veri sonu (ORTA_BANT 24 saat) t=2160 ve
    # t=2370'teki YATAY_DONUS girişlerine izin verir (sınırsız çağrıda var); önceki işlemler aynen kalır
    son_i = n - 250
    kombo_mu = df['kural'].isin(list(sl.KOMBO_TREND)).to_numpy()
    giris_i = ((df['giris_ts'] - T0) // SAAT).to_numpy()
    assert {2161, 2371} <= set(giris_i[kombo_mu & (giris_i >= son_i)].tolist())
    tablolar, _ = sl.parite_islemleri('A/USDT', o, h, l, c, v, ts, btc, np.ones(n, bool),
                                      ['REJIM_KOMBO', 'REJIM_KOMBO_BOT'], 0, 0, 800, son_i)
    d2 = pd.concat(tablolar, ignore_index=True)
    assert len(d2) and (((d2['giris_ts'] - T0) // SAAT) < son_i).all()
    pd.testing.assert_frame_equal(d2, df[kombo_mu & (giris_i < son_i)].reset_index(drop=True))
    # rejim yoksa (BTC bağlamında 'rejim' anahtarı yok) kombo ve YATAY_DONUS hesaplanmaz
    monkeypatch.undo()
    del btc['rejim']
    tablolar, acik = sl.parite_islemleri('A/USDT', o, h, l, c, v, ts, btc, None, ['REJIM_KOMBO', 'YATAY_DONUS'],
                                         0, 0, 800)
    assert tablolar == [] and acik == {}


# ------------------------------------------------------------------------------------------
# İleri yürüyen AI
# ------------------------------------------------------------------------------------------
class CasusModel:
    egitimler = []

    def fit(self, X, y):
        CasusModel.egitimler.append(X[:, 0].astype(int).copy())
        return self

    def predict_proba(self, X):
        p = 1 / (1 + np.exp(-X[:, 1].astype(float)))
        return np.column_stack([1 - p, p])


def _ai_islemleri(n=700, tohum=0):
    rng = np.random.default_rng(tohum)
    giris = np.sort(rng.integers(vs_ms('2023-06-01'), vs_ms('2025-06-01'), n)).astype('int64') // SAAT * SAAT
    df = pd.DataFrame({'giris_ts': giris, 'cikis_ts': giris + SAAT * rng.integers(1, 24 * 20, n),
                       'getiri_k1': rng.normal(0, 0.01, n)})
    for ad in sl.OZELLIKLER:
        df[ad] = rng.normal(0, 1, n)
    df['ret24'] = np.arange(n)                                                  # işlem kimliği (casus için)
    df.loc[100, 'cikis_ts'] = vs_ms('2024-06-01')                               # tam ay başında kapanan işlem
    return df


def vs_ms(t):
    return sl.vs._ms(t)


def test_ai_ileri_yuruyus_sizinti_yok_esik_egitimden():
    df = _ai_islemleri()
    CasusModel.egitimler = []
    ayrim = vs_ms('2025-01-01')
    skor, esik, kayit = sl.ai_ileri_yuruyus(df, ayrim, vs_ms('2025-07-01'), model_kurucu=CasusModel)
    assert kayit[0]['ay'] == '2024-01' and kayit[-1]['ay'] == '2025-06'
    egitimler = iter(CasusModel.egitimler)
    modelli = 0
    for k in kayit:
        M = vs_ms(k['ay'] + '-01')
        S = sl._ay_ekle(M, 1)
        hedef = ((df['giris_ts'] >= M) & (df['giris_ts'] < S)).to_numpy()
        beklenen = np.flatnonzero((df['cikis_ts'] < M).to_numpy())
        y = df['getiri_k1'].to_numpy()[beklenen] > 0
        yeterli = len(beklenen) >= 200 and min(y.sum(), (~y).sum()) >= 30
        assert k['model'] == yeterli and k['egitim'] == len(beklenen)
        if not yeterli:
            assert np.isnan(skor[hedef]).all() and np.isnan(esik[hedef]).all()   # model yok: AI işlem yapmaz
            continue
        modelli += 1
        if not k['islem']:
            continue
        ids = next(egitimler)
        assert np.array_equal(np.sort(ids), beklenen)                           # yalnız M'den önce kapananlar
        assert (df['cikis_ts'].to_numpy()[ids] < M).all()
        p_eg = 1 / (1 + np.exp(-df['ret6'].to_numpy()[beklenen].astype(np.float32).astype(float)))
        assert esik[hedef] == pytest.approx(np.percentile(p_eg, 60))          # eşik yalnız eğitimden
        assert skor[hedef] == pytest.approx(1 / (1 + np.exp(-df['ret6'].to_numpy()[hedef])), rel=1e-6)
    assert 0 < modelli < len(kayit)
    assert df.loc[100, 'giris_ts'] < vs_ms('2024-06-01') and next(k for k in kayit if k['ay'] == '2024-06')['model']
    # M'den sonra kapanan işlemlerin etiketleri değişse de M'nin skorları aynı (gerçek model)
    df2 = df.copy()
    M = vs_ms('2024-09-01')
    df2.loc[df2['cikis_ts'] >= M, 'getiri_k1'] *= -1
    s1, e1, _ = sl.ai_ileri_yuruyus(df, ayrim, M + 31 * GUN)
    s2, e2, _ = sl.ai_ileri_yuruyus(df2, ayrim, M + 31 * GUN)
    m = ((df['giris_ts'] >= M) & (df['giris_ts'] < sl._ay_ekle(M, 1))).to_numpy()
    assert m.any() and np.isfinite(s1[m]).all()
    np.testing.assert_array_equal(s1[m], s2[m])
    np.testing.assert_array_equal(e1[m], e2[m])


def test_model_kur_xgboost_yoksa_sklearn(monkeypatch):
    from sklearn.ensemble import HistGradientBoostingClassifier
    monkeypatch.setitem(sys.modules, 'xgboost', None)                           # import xgboost -> ImportError
    m = sl.model_kur()
    assert isinstance(m, HistGradientBoostingClassifier)
    rng = np.random.default_rng(0)
    X = rng.normal(0, 1, (300, len(sl.OZELLIKLER))).astype(np.float32)
    m.fit(X, (X[:, 0] > 0).astype(int))
    assert m.predict_proba(X).shape == (300, 2)
    assert sl.kutuphane_surumleri()['xgboost'].startswith('yok')


def test_ai_az_veri_ya_da_tek_sinif_model_yok():
    df = _ai_islemleri(150)
    skor, esik, kayit = sl.ai_ileri_yuruyus(df, vs_ms('2025-01-01'), vs_ms('2025-07-01'), model_kurucu=CasusModel)
    assert not any(k['model'] for k in kayit) and np.isnan(skor).all()
    df = _ai_islemleri()
    df['getiri_k1'] = np.where(np.arange(len(df)) % 50 == 0, -0.01, 0.01)        # kayıp sınıfı < 30
    _, _, kayit = sl.ai_ileri_yuruyus(df, vs_ms('2025-01-01'), vs_ms('2025-07-01'), model_kurucu=CasusModel)
    assert not any(k['model'] for k in kayit)


# ------------------------------------------------------------------------------------------
# İstatistik, bootstrap, karar kuralı, bütçe
# ------------------------------------------------------------------------------------------
def _islem_df(giris_saat, cikis_saat, r, sembol='A/USDT', kural='ERKEN_BIRIKIM'):
    g = T0 + SAAT * np.asarray(giris_saat, dtype=np.int64)
    c = T0 + SAAT * np.asarray(cikis_saat, dtype=np.int64)
    r = np.asarray(r, dtype=float)
    sembol = sembol if isinstance(sembol, list) else [sembol] * len(r)
    return pd.DataFrame({'kural': kural, 'sembol': sembol, 'giris_ts': g, 'cikis_ts': c, 'getiri_k1': r,
                         'getiri_k05': r + 0.001, 'getiri_k2': r - 0.002, 'sure_saat': (c - g) / SAAT})


def test_istatistik_tanimlari():
    # girişe göre: +, -, -, +, - ; çıkışa göre: -, +, -, +, - (1. işlem 2.'den sonra kapanıyor)
    giris = [0, 24, 48, 24 * 40, 24 * 41]
    cikis = [30, 25, 50, 24 * 40 + 5, 24 * 41 + 2]
    df = _islem_df(giris, cikis, [0.02, -0.01, -0.01, 0.03, -0.02])
    o = sl.istatistik(df)
    assert o['n'] == 5 and o['kazanma_pct'] == pytest.approx(40)
    assert o['ort_kazanc_pct'] == pytest.approx(2.5) and o['ort_kayip_pct'] == pytest.approx(-4 / 3)
    assert o['rr'] == pytest.approx(2.5 / (4 / 3)) and o['pf'] == pytest.approx(1.25)
    assert o['beklenti_pct'] == pytest.approx(0.2) and o['beklenti_k2_pct'] == pytest.approx(0.0)
    assert o['kayip_serisi'] == 1                                               # çıkış sırasıyla
    assert (o['ay'], o['pozitif_ay']) == (2, 1) and o['pozitif_ay_pct'] == pytest.approx(50)  # Ocak toplamı 0
    assert o['medyan_sure_saat'] == pytest.approx(2)                          # süreler 30, 1, 2, 5, 2


def test_gun_bloklu_bootstrap():
    o = sl.gun_bootstrap(np.full(30, 0.01), T0 + GUN * np.arange(30))
    assert o['ga95_pct'] == pytest.approx([1.0, 1.0]) and o['p_pozitif'] == 1.0
    # A günü 10 işlem +%1, B günü 1 işlem -%1: günler birlikte çekilir -> P(>0) = P(en az bir A) = 0.75
    giris = np.r_[np.full(10, T0), [T0 + GUN]]
    o = sl.gun_bootstrap(np.r_[np.full(10, 0.01), [-0.01]], giris)
    assert o['p_pozitif'] == pytest.approx(0.75, abs=0.03)
    assert o['ga95_pct'][0] == pytest.approx(-1.0) and o['ga95_pct'][1] == pytest.approx(1.0)


def _ozet(n=150, b=0.5, p=0.995, ay=70.0, k2=0.2, kaz=55.0):
    return {'n': n, 'beklenti_pct': b, 'p_pozitif': p, 'pozitif_ay_pct': ay, 'beklenti_k2_pct': k2, 'kazanma_pct': kaz}


def test_gun_bootstrap_naif_dongu_ile_ayni():
    """Aynı çekilişlerle naif hesap: istatistik = toplam r / işlem sayısı; %95 GA 2.5/97.5 yüzdelik; P(>0) kesin."""
    rng = np.random.default_rng(8)
    giris = T0 + GUN * rng.integers(0, 40, 120) + SAAT * rng.integers(0, 24, 120)
    r = rng.normal(0.001, 0.01, 120)
    o = sl.gun_bootstrap(r, giris)
    gun = (giris // GUN).astype(np.int64)
    gunler = np.unique(gun)
    idx = np.random.default_rng(0).integers(0, len(gunler), size=(sl.BOOTSTRAP_N, len(gunler)))
    ist = np.array([np.concatenate([r[gun == gunler[i]] for i in satir]).mean() for satir in idx])
    assert o['ga95_pct'] == pytest.approx([np.percentile(ist, 2.5) * 100, np.percentile(ist, 97.5) * 100], rel=1e-9)
    assert o['p_pozitif'] == (ist > 0).mean()
    assert sl.gun_bootstrap(np.zeros(10), T0 + GUN * np.arange(10))['p_pozitif'] == 0.0   # 0 pozitif değil


def test_bootstrap_parca_boyundan_bagimsiz(monkeypatch):
    """Bootstrap çekilişleri bellek için parça parça (BOOTSTRAP_PARCA öğe) yapılır. PCG64 akışı parçalamadan
    bağımsız: sonuç parça boyundan bağımsız, tek seferde çekilenle birebir aynı (gün bootstrap'ı ve AI karşılaştırması)."""
    rng = np.random.default_rng(8)
    giris = T0 + GUN * rng.integers(0, 300, 900) + SAAT * rng.integers(0, 24, 900)
    r = rng.normal(0.001, 0.01, 900)
    df = pd.DataFrame({'giris_ts': giris, 'getiri_k1': r, 'ai_secildi': rng.random(900) < 0.4})
    ref_b, ref_k = sl.gun_bootstrap(r, giris), sl.ai_karsilastir(df)
    assert sl.BOOTSTRAP_PARCA // 300 < sl.BOOTSTRAP_N                           # varsayılan da parçalı çeker
    for parca in (1, 299, 1000, 10 ** 9):
        monkeypatch.setattr(sl, 'BOOTSTRAP_PARCA', parca)
        assert sl.gun_bootstrap(r, giris) == ref_b and sl.ai_karsilastir(df) == ref_k, parca


def test_ai_karsilastir_ayni_gunler_naif_dongu_ile_ayni():
    rng = np.random.default_rng(9)
    n = 150
    giris = T0 + GUN * rng.integers(0, 50, n) + SAAT * rng.integers(0, 24, n)
    df = pd.DataFrame({'giris_ts': giris, 'getiri_k1': rng.normal(0, 0.01, n), 'ai_secildi': rng.random(n) < 0.4})
    o = sl.ai_karsilastir(df)
    r, sec = df['getiri_k1'].to_numpy(), df['ai_secildi'].to_numpy()
    gun = giris // GUN
    gunler = np.unique(gun)
    idx = np.random.default_rng(0).integers(0, len(gunler), size=(sl.BOOTSTRAP_N, len(gunler)))
    fark = []
    for satir in idx:                                                           # AI ve kural AYNI günlerle
        m = np.concatenate([np.flatnonzero(gun == gunler[i]) for i in satir])
        fark.append(r[m][sec[m]].mean() - r[m].mean() if sec[m].any() else np.nan)
    fark = np.array(fark)
    assert o['n_kural'] == n and o['n_ai'] == sec.sum()
    assert o['fark_pct'] == pytest.approx((r[sec].mean() - r.mean()) * 100)
    assert o['p_pozitif'] == pytest.approx(np.mean(np.nan_to_num(fark, nan=-1) > 0))
    sonlu = fark[np.isfinite(fark)]
    assert o['ga95_pct'] == pytest.approx([np.percentile(sonlu, 2.5) * 100, np.percentile(sonlu, 97.5) * 100])
    df['ai_secildi'] = True                                                     # fark her örnekte tam 0: pozitif değil
    assert sl.ai_karsilastir(df)['p_pozitif'] == 0.0


def test_karar_kurali_kriterleri():
    iyi = _ozet()
    k = sl.karar_ver(iyi, _ozet(b=0.1))
    assert k['sonuc'] == 'GEÇTİ' and k['kalan'] == [] and not k['hedef_70']
    durumlar = [((_ozet(n=99), iyi), 1), ((_ozet(p=0.989), iyi), 2), ((_ozet(b=-0.01, p=0.999), iyi), 2),
                ((iyi, _ozet(b=-0.1)), 3), ((iyi, {'n': 0}), 3), ((_ozet(ay=59.9), iyi), 4),
                ((_ozet(k2=-0.01), iyi), 5)]
    for (s, kr), kriter in durumlar:
        k = sl.karar_ver(s, kr)
        assert k['sonuc'] == 'KALDI' and [x[:3] for x in k['kalan']] == [f"({kriter})"], (kriter, k)
    assert sl.karar_ver(iyi, iyi, ai_mi=True, ai_p=0.89)['kalan'] == [f"(6) {sl.KRITERLER[6]}"]
    assert sl.karar_ver(iyi, iyi, ai_mi=True, ai_p=0.90)['sonuc'] == 'GEÇTİ'
    assert sl.karar_ver(iyi, iyi, ai_mi=True, ai_p=None)['sonuc'] == 'KALDI'
    assert sl.karar_ver(_ozet(kaz=70.0), iyi)['hedef_70']
    k = sl.karar_ver({'n': 0}, {'n': 0})
    assert [x[:3] for x in k['kalan']] == ['(1)', '(2)', '(3)', '(4)', '(5)']
    # sınırların tam üstü geçer (n = 100, P = 0.99 = 1980/2000, %60.0 pozitif ay)
    p = float((np.arange(2000) < 1980).mean())
    assert sl.karar_ver(_ozet(n=100, p=p, ay=60.0), iyi)['sonuc'] == 'GEÇTİ'
    assert sl.karar_ver(_ozet(n=100, p=p, ay=60.0), iyi, ai_mi=True, ai_p=0.9)['sonuc'] == 'GEÇTİ'
    for s, kr, kriter in ((_ozet(b=0.0), iyi, 2), (_ozet(k2=0.0), iyi, 5), (iyi, _ozet(b=0.0), 3)):
        assert [x[:3] for x in sl.karar_ver(s, kr)['kalan']] == [f"({kriter})"]  # 0 pozitif değil


def test_analiz_kurulmus_islem_listelerinde_dogru_karar():
    rng = np.random.default_rng(4)
    gun = np.arange(0, 600)                                                     # 2023-01-01'den ~20 ay, günde 1
    iyi = _islem_df(gun * 24 + 3, gun * 24 + 9, np.where(rng.random(600) < 0.8, 0.01, -0.005))
    gurultu = _islem_df(gun * 24 + 3, gun * 24 + 9, rng.normal(-0.001, 0.01, 600), kural='SIKISMA_KIRILIM')
    islemler = pd.concat([iyi, gurultu], ignore_index=True)
    islemler['ai_skor'], islemler['ai_esik'], islemler['ai_secildi'] = np.nan, np.nan, False
    an = sl.analiz(islemler, ['ERKEN_BIRIKIM', 'SIKISMA_KIRILIM'], False, T0, vs_ms('2024-01-01'),
                   T0 + 600 * GUN, [450.0, 90.0], 20.0)
    assert an['karar']['ERKEN_BIRIKIM']['sonuc'] == 'GEÇTİ' and an['karar']['ERKEN_BIRIKIM']['hedef_70']
    k = an['karar']['SIKISMA_KIRILIM']
    assert k['sonuc'] == 'KALDI' and any(x.startswith('(2)') for x in k['kalan'])
    assert set(an['sonuclar']['ERKEN_BIRIKIM']) == {'kurulus', 'sinama', '2023', '2024'}
    assert an['sonuclar']['ERKEN_BIRIKIM']['sinama']['n'] == 600 - 365
    # 90 USDT bütçe = 4 slot; günde 1 işlem, 6 saat: hepsi alınır
    b = an['butce']['ERKEN_BIRIKIM']['90']['sinama']
    assert b['alinan'] == 235 and b['toplam_usdt'] == pytest.approx(20 * iyi['getiri_k1'].to_numpy()[365:].sum())


def test_donemlere_ve_aylara_giris_zamanina_gore_atanir():
    ms = vs_ms
    g = np.array([ms('2023-12-31 23:00'), ms('2024-01-31 23:00'), ms('2024-02-10')], dtype=np.int64)
    c = np.array([ms('2024-01-01 05:00'), ms('2024-02-01 03:00'), ms('2024-02-10 05:00')], dtype=np.int64)
    r = np.array([0.05, 0.02, -0.01])
    df = pd.DataFrame({'kural': 'ERKEN_BIRIKIM', 'sembol': 'A/USDT', 'giris_ts': g, 'cikis_ts': c, 'getiri_k1': r,
                       'getiri_k05': r, 'getiri_k2': r, 'sure_saat': 1.0, 'ai_skor': np.nan, 'ai_esik': np.nan,
                       'ai_secildi': False})
    an = sl.analiz(df, ['ERKEN_BIRIKIM'], False, ms('2023-12-01'), ms('2024-01-01'), ms('2024-03-01'), [450.0], 20.0)
    d = an['sonuclar']['ERKEN_BIRIKIM']
    assert (d['kurulus']['n'], d['2023']['n'], d['sinama']['n'], d['2024']['n']) == (1, 1, 2, 2)
    assert (d['sinama']['ay'], d['sinama']['pozitif_ay']) == (2, 1)            # Ocak +0.02, Şubat -0.01
    b = an['butce']['ERKEN_BIRIKIM']['450']['sinama']
    assert b['aylik'] == pytest.approx({'2024-01': 0.4, '2024-02': -0.2})
    assert b['aylik_adet'] == {'2024-01': 1, '2024-02': 1}


def test_ai_satiri_ve_karsilastirma_yalniz_modelli_aylar():
    """AI karşılaştırması: sınama döneminin YALNIZ modeli olan aylarındaki kural işlemleri; +AI satırının kuruluş
    istatistiği (3. kriter) yalnız AI'nın seçtiği kuruluş işlemleriyle."""
    rng = np.random.default_rng(3)
    satir = []
    for ay, modelli, r_sec, r_diger in (('2023-10', True, -0.01, 0.03), ('2023-11', True, -0.01, 0.03),
                                         ('2024-01', True, 0.02, -0.01), ('2024-02', True, 0.02, -0.01),
                                         ('2024-03', False, 0.05, 0.05), ('2024-04', False, 0.05, 0.05)):
        for i in range(20):
            gts = vs_ms(f"{ay}-01") + GUN * i + 5 * SAAT
            sec = modelli and i % 2 == 0
            satir.append((gts, (r_sec if sec else r_diger) + rng.normal(0, 1e-4), np.nan if not modelli else 0.5,
                          sec))
    g, r, esik, sec = map(np.array, zip(*satir))
    df = pd.DataFrame({'kural': 'ERKEN_BIRIKIM', 'sembol': 'A/USDT', 'giris_ts': g.astype(np.int64),
                       'cikis_ts': g.astype(np.int64) + 3 * SAAT, 'getiri_k1': r, 'getiri_k05': r, 'getiri_k2': r,
                       'sure_saat': 3.0, 'ai_skor': np.where(sec, 0.9, 0.1), 'ai_esik': esik, 'ai_secildi': sec})
    an = sl.analiz(df, ['ERKEN_BIRIKIM'], True, vs_ms('2023-10-01'), vs_ms('2024-01-01'), vs_ms('2024-05-01'),
                   [450.0], 20.0)
    ai = an['ai']['ERKEN_BIRIKIM']
    sinama_modelli = (g >= vs_ms('2024-01-01')) & np.isfinite(esik)
    assert ai['n_kural'] == 40 and ai['n_ai'] == 20
    assert ai['beklenti_kural_pct'] == pytest.approx(r[sinama_modelli].mean() * 100)
    assert ai['fark_pct'] > 0
    kr = an['sonuclar']['ERKEN_BIRIKIM+AI']['kurulus']
    assert kr['n'] == 20 and kr['beklenti_pct'] < 0                            # kural kuruluşu ise pozitif
    assert an['sonuclar']['ERKEN_BIRIKIM']['kurulus']['beklenti_pct'] > 0
    assert any(x.startswith('(3)') for x in an['karar']['ERKEN_BIRIKIM+AI']['kalan'])
    assert not any(x.startswith('(3)') for x in an['karar']['ERKEN_BIRIKIM']['kalan'])


def test_butce_dususu_cikis_sirasiyla():
    # giriş sırası: A(-1) B(+1) C(-1) -> en büyük düşüş -1; çıkış sırası: B(+1) C(-1) A(-1) -> -2
    df = _islem_df([0, 1, 3], [10, 2, 4], [-0.05, 0.05, -0.05], sembol=['A/USDT', 'B/USDT', 'C/USDT'])
    assert sl.butce_ozeti(df, 20.0, ['2023-01'])['max_dusus_usdt'] == pytest.approx(-2.0)


def _rapor_meta():
    return {'baslangic': '2023-01-01', 'bitis': '2024-07-01 00:00', 'veri_sonu': '2024-07-08 00:00', 'ayrim': '2024-01-01',
            'evren': 80, 'parite': 2, 'sabit_coin': 0, 'verisiz': 0, 'min_hacim': 5e6, 'buyuk_hacim': 2e7, 'istek': 0,
            'sure_sn': 1.0, 'surumler': {'pandas': pd.__version__}, 'acik_kalan': {}, 'islem': 20.0}


def test_rapor_gosterimi_karar_kuraliyla_celismez():
    """P(>0) 4 basamak (0.9895, 0.990 değil); kazanma ve pozitif ay aşağı yuvarlanır (%69.96 -> 69.9); kayıpsız
    satırda PF/R:R '∞'; dönem adları Türkçe; bütçede poz.ay sayıyla; aylık tabloda işlemsiz ay '-'."""
    kur = _islem_df(np.arange(5) * 24 * 7, np.arange(5) * 24 * 7 + 3, [0.01] * 5)               # hepsi kazanç
    gun = np.r_[np.arange(0, 20), np.arange(31, 51), np.arange(91, 111)]                        # Oca, Şub, Nis
    sin = _islem_df(365 * 24 + gun * 24, 365 * 24 + gun * 24 + 3, np.where(gun % 3 == 0, -0.01, 0.01))
    df = pd.concat([kur, sin], ignore_index=True)
    df['ai_skor'], df['ai_esik'], df['ai_secildi'] = np.nan, np.nan, False
    an = sl.analiz(df, ['ERKEN_BIRIKIM'], False, T0, vs_ms('2024-01-01'), vs_ms('2024-07-01'), [450.0], 20.0)
    s = an['sonuclar']['ERKEN_BIRIKIM']['sinama']
    s['p_pozitif'], s['kazanma_pct'], s['pozitif_ay_pct'] = 0.9895, 69.96, 59.99
    metin = sl.rapor_metni(_rapor_meta(), an, {})
    satirlar = metin.splitlines()
    kur_s = next(x for x in satirlar if x.startswith(f"   {'ERKEN_BIRIKIM':19s} kuruluş"))
    sin_s = next(x for x in satirlar if x.startswith(f"   {'ERKEN_BIRIKIM':19s} sınama"))
    assert kur_s.count('∞') == 2 and ' 100.0%' in kur_s
    assert '0.9895' in sin_s and '0.990 ' not in sin_s
    assert ' 69.9%' in sin_s and '70.0%' not in sin_s and ' 59% ' in sin_s
    assert ' kurulus ' not in metin and ' sinama ' not in metin
    b = an['butce']['ERKEN_BIRIKIM']['450']['sinama']
    assert (b['pozitif_ay'], b['ay']) == (3, 6)
    assert any(x.startswith(f"   {'ERKEN_BIRIKIM':19s}   450 sınama") and ' 3/6 ' in x for x in satirlar)
    aylik = {x.split()[0]: x.split()[1] for x in satirlar if x.startswith('   2024-0')}
    assert aylik['2024-03'] == '-' and aylik['2024-05'] == '-' and aylik['2024-01'] != '-'
    assert an['sonuclar']['ERKEN_BIRIKIM']['kurulus']['pf_sonsuz'] and not s['pf_sonsuz']


def test_islemleri_yaz_parcali_ayni_csv(tmp_path, monkeypatch):
    """İşlem CSV'si parça parça yazılır (tablonun biçimlenmiş tam kopyası bellekte tutulmaz): çıktı tek seferde yazılanla
    bayt bayt aynı (başlık bir kez; zamanlar vs._tarih ile 'YYYY-MM-DD HH:MM' UTC); işlem yoksa yalnız başlık."""
    rng = np.random.default_rng(5)
    n = 23
    df = pd.DataFrame({k: rng.normal(0, 1, n) for k in sl.CSV_SUTUNLAR})
    df['kural'], df['sembol'], df['cikis_tipi'] = 'REJIM_KOMBO', 'A/USDT', 'SINYAL'
    df['bilesen'] = rng.choice(['ERKEN_BIRIKIM', 'YATAY_DONUS'], n)
    df['rejim'] = rng.choice(list(sl.REJIMLER) + [sl.REJIM_YOK], n)
    df['giris_ts'] = T0 + SAAT * rng.integers(0, 40000, n)
    df['cikis_ts'] = df['giris_ts'] + SAAT * rng.integers(1, 168, n)
    df['ai_secildi'] = rng.random(n) < 0.5
    df.loc[[3, 9], 'adx14'] = np.nan
    tek = df[sl.CSV_SUTUNLAR].copy()
    for kol in ('giris_ts', 'cikis_ts'):
        tek[kol] = [sl.vs._tarih(x) for x in df[kol].to_numpy()]
    tek.to_csv(tmp_path / 'tek.csv', index=False)
    monkeypatch.setattr(sl, 'CSV_PARCA', 7)                                    # 23 satır: 4 parça
    sl.islemleri_yaz(df, str(tmp_path / 'parca.csv'))
    assert (tmp_path / 'parca.csv').read_bytes() == (tmp_path / 'tek.csv').read_bytes()
    sl.islemleri_yaz(df.iloc[:0], str(tmp_path / 'bos.csv'))
    assert (tmp_path / 'bos.csv').read_text(encoding='utf-8').splitlines() == [','.join(sl.CSV_SUTUNLAR)]


def test_arguman_dogrulama():
    ap = sl.arguman_ayristirici()
    for arg in (['--islem', '0'], ['--islem', '-5'], ['--butce', '450', '-1'], ['--butce', '0'], ['--evren', '-3']):
        with pytest.raises(SystemExit):
            ap.parse_args(arg)
    a = ap.parse_args(['--evren', '0', '--islem', '5', '--butce', '100'])
    assert (a.evren, a.islem, a.butce) == (0, 5.0, [100.0])


def test_donem_sinirlari_son_tamamlanmis_ay():
    simdi = vs_ms('2026-10-04 06:30')
    son, veri = sl.donem_sinirlari(None, simdi)
    assert son == vs_ms('2026-09-01') and veri == son + 168 * SAAT              # 1-7 Ekim: Eylül henüz tamam değil
    son, veri = sl.donem_sinirlari(None, vs_ms('2026-10-09 12:00'))
    assert son == vs_ms('2026-10-01') and veri == son + 168 * SAAT
    son, veri = sl.donem_sinirlari('2026-10-02', simdi)
    assert son == vs_ms('2026-10-02') and veri == vs_ms('2026-10-04 06:00')    # veri şimdiyi geçmez
    son, veri = sl.donem_sinirlari('2027-01-01', simdi)
    assert son == veri == vs_ms('2026-10-04 06:00')


def test_butce_slot_ayni_anda_bosalma_ve_kar_zarar():
    df = _islem_df([0, 1, 2, 3, 3, 5], [5, 3, 4, 6, 7, 8], [0.10, -0.05, 0.20, 0.05, 0.30, 0.01],
                   sembol=['A/USDT', 'B/USDT', 'C/USDT', 'A/USDT', 'Z/USDT', 'B/USDT'])
    alindi = sl.butce_oynat(df, 40.0, 20.0)                                     # 2 slot
    # C: 2 slot dolu; D: B aynı saatte kapanır, slot önce boşalır; E: aynı saatte D'den sonra, dolu; F: A 5'te kapanır
    assert alindi.tolist() == [True, True, False, True, False, True]
    o = sl.butce_ozeti(df[alindi], 20.0, ['2023-01', '2023-02'])
    assert o['alinan'] == 4 and o['toplam_usdt'] == pytest.approx(20 * (0.10 - 0.05 + 0.05 + 0.01))
    assert o['aylik_ort_usdt'] == pytest.approx(o['toplam_usdt'] / 2)            # işlemsiz ay da sayılır
    assert o['pozitif_ay_pct'] == pytest.approx(50)
    assert o['max_dusus_usdt'] == pytest.approx(-1.0)                          # çıkış sırası: B(-1) önce
    assert sl.butce_oynat(df, 19.0, 20.0).sum() == 0
    assert sl.butce_oynat(df, 450.0, 20.0).all()


# ------------------------------------------------------------------------------------------
# Uçtan uca: sentetik 1 saatlik borsa
# ------------------------------------------------------------------------------------------
class SaatlikBorsa:
    """Sentetik 1 saatlik mumlar: MumDeposu ile aynı arayüz (fetch_ohlcv / fetch_tickers). tickers verilirse evren
    sabittir (veriden bağımsız); verisi olmayan sembol BadSymbol verir. İstenen semboller kaydedilir."""

    def __init__(self, veri, tickers=None):
        self.veri, self.istek, self.tickers, self.istenen, self.ticker_istegi = veri, 0, tickers, set(), 0

    def fetch_tickers(self):
        self.ticker_istegi += 1
        if self.tickers is not None:
            return self.tickers
        return {s: {'quoteVolume': float(d[-24:, 4] @ d[-24:, 5])} for s, d in self.veri.items()}

    def fetch_ohlcv(self, sym, tf, since=None, limit=1000):
        self.istek += 1
        self.istenen.add(sym)
        if sym not in self.veri or tf != '1h':
            raise ccxt.BadSymbol(sym)
        d = self.veri[sym]
        i = int(np.searchsorted(d[:, 0], since))
        return d[i:i + limit].tolist()


VERI_BAS = vs_ms('2022-08-01')
VERI_BIT = vs_ms('2023-10-01')


def _satirlar(ts, mumlar):
    return np.column_stack([ts, *mumlar])


def _arguman(tmp_path, *ek):
    return sl.arguman_ayristirici().parse_args(
        ['--baslangic', '2023-01-01', '--bitis', '2023-10-01', '--onbellek', str(tmp_path / 'onb'),
         '--cikti', str(tmp_path / 'lab'), '--evren', '20', *ek])


SIMDI = VERI_BIT + 5 * SAAT


def test_uctan_uca_rastgele_piyasa_hicbir_kural_gecmez(tmp_path):
    ts = np.arange(VERI_BAS, VERI_BIT, SAAT, dtype=np.int64)
    n = len(ts)
    veri = {'BTC/USDT': _satirlar(ts, rastgele_mumlar(n, 0, 20000, 0.005, 5e7))}
    for i in range(9):
        veri[f'C{i:02d}/USDT'] = _satirlar(ts, rastgele_mumlar(n, i + 1, 10.0, 0.012, 1e6))
    sabit = np.column_stack([ts, np.ones(n), np.full(n, 1.0002), np.full(n, 0.9998),
                             1 + np.random.default_rng(7).normal(0, 0.0001, n), np.full(n, 3e6)])
    i = int(np.searchsorted(ts, vs_ms('2023-08-10')))
    sabit[i:i + 4, 1:5] = np.array([0.87, 0.92, 0.96, 0.99])[:, None]          # sınamada peg kaybı (USDC 2023 gibi)
    assert sl.saatlik_oynaklik(sabit[:, 4]) > sl.SABIT_COIN_STD                 # tüm geçmişe bakılsa evrende kalırdı
    veri['USDX/USDT'] = sabit                                                   # sabit coin: çıkarılmalı
    veri['BOS/USDT'] = np.empty((0, 6))                                         # borsa boş veri döndürüyor
    liste = vs_ms('2023-04-01')
    yeni = _satirlar(ts, rastgele_mumlar(n, 20, 5.0, 0.012, 1e6))
    veri['YENI/USDT'] = yeni[ts >= liste]                                       # dönem ortasında listelenen
    bosluk = _satirlar(ts, rastgele_mumlar(n, 21, 5.0, 0.012, 1e6))
    gb, gs = vs_ms('2023-03-10'), vs_ms('2023-03-10 12:00')
    veri['BOSLUK/USDT'] = bosluk[(ts < gb) | (ts >= gs)]                        # 12 saat eksik
    tickers = {s: {'quoteVolume': float(d[-24:, 4] @ d[-24:, 5])} for s, d in veri.items()}
    tickers['YOK/USDT'] = {'quoteVolume': 1.0}                                  # verisi yok: BadSymbol
    ex = SaatlikBorsa(veri, tickers)
    a = _arguman(tmp_path, '--ayrim', '2023-07-01')
    sonuc = sl.calistir(ex, a, simdi_ms=SIMDI, log=lambda *x: None)
    meta, an, isl = sonuc['meta'], sonuc['analiz'], sonuc['islemler']
    assert meta['sabit_coin'] == 1 and meta['sabit_coinler'] == ['USDX/USDT']
    assert 'USDX/USDT' not in sonuc['kesit'].semboller and meta['parite'] == 12
    assert meta['verisiz'] == 2 and {'YOK/USDT', 'BOS/USDT'} <= ex.istenen
    # veri sonu: girişler analiz sonundan önce ve azami tutma süresi veride (sonucuna göre seçilmiş işlem yok)
    assert meta['bitis'].startswith('2023-10-01') and meta['veri_sonu'] == sl.vs._tarih(SIMDI)
    azami = isl['bilesen'].map(lambda k: sl.AZAMI_TUTMA.get(sl.KURAL_CIKIS[k], 0)).to_numpy()   # komboda: bileşen
    assert (azami[(isl['kural'] != 'BTC_TREND').to_numpy()] > 0).all()
    cift = (isl['kural'] != 'BTC_TREND').to_numpy()
    assert (isl['giris_ts'] < VERI_BIT).all()
    assert (isl['giris_ts'].to_numpy()[cift] + azami[cift] * SAAT <= SIMDI).all()
    assert all(v == 0 for k, v in meta['acik_kalan'].items() if k != 'BTC_TREND')
    assert set(meta['surumler']) >= {'numpy', 'pandas', 'sklearn', 'xgboost'}
    assert ex.istek > 0
    # dönem ortasında listelenen: ilk sinyal 720 dolu saatten sonra
    y = isl[isl['sembol'] == 'YENI/USDT']
    assert len(y) and (y['giris_ts'] >= liste + 720 * SAAT).all()
    b = isl[isl['sembol'] == 'BOSLUK/USDT']
    assert len(b) and not ((b['giris_ts'] >= gb) & (b['giris_ts'] < gs)).any()
    assert (isl['giris_ts'] >= vs_ms('2023-01-01')).all() and (isl['cikis_ts'] <= vs_ms('2023-10-01')).all()
    assert set(isl['kural']) >= {'BOT_VEKILI', 'TREND_DIP_RSI2', 'YATAY_DONUS', 'REJIM_KOMBO_BOT'}
    # rastgele yürüyüşte hiçbir satır geçmez (15 satır: 9 kural + 6 AI varyantı)
    assert set(an['karar']) == set(sl.KURALLAR) | {k + '+AI' for k in sl.AI_KURALLARI} and len(an['karar']) == 15
    assert all(k['sonuc'] == 'KALDI' for k in an['karar'].values())
    assert any(x['model'] for x in sonuc['ai_aylar']['TREND_DIP_RSI2'])
    assert set(sonuc['ai_aylar']) == set(sl.AI_KURALLARI)                      # kombolarda ve BTC_TREND'de AI yok
    # rejim ve bileşen: karar mumundaki BTC rejimi; kombo dışında bileşen = kural
    g0 = ((vs_ms('2023-01-01') - sl.ISINMA_GUN * GUN) // SAAT) * SAAT
    cift = isl[isl['kural'] != 'BTC_TREND']
    assert (cift['rejim'].to_numpy() == sonuc['btc']['rejim'][(cift['giris_ts'].to_numpy() - SAAT - g0) // SAAT]).all()
    tek = ~isl['kural'].isin(list(sl.KOMBO_TREND))
    assert (isl.loc[tek, 'bilesen'] == isl.loc[tek, 'kural']).all()
    assert set(isl.loc[isl['kural'] == 'REJIM_KOMBO_BOT', 'bilesen']) <= {'BOT_VEKILI', 'YATAY_DONUS'}
    assert (isl.loc[(isl['bilesen'] == 'YATAY_DONUS'), 'rejim'] == 'YATAY').all()
    assert (isl.loc[isl['kural'].isin(list(sl.KOMBO_TREND)) & (isl['bilesen'] != 'YATAY_DONUS'), 'rejim']
            == 'TREND_YUKARI').all()
    metin = (tmp_path / 'lab_rapor.txt').read_text(encoding='utf-8')
    for bolum in ('0) KARAR KURALI', '1) KURALLAR', '2) YIL YIL', '3) MALİYET', '4) BÜTÇE', '5) AYLIK', '6) AI',
                  '7) KARAR', '8) REJİM', 'Saatlerin rejim dağılımı', 'Bilinen sınırlar'):
        assert bolum in metin, bolum
    assert sl.KARAR_KURALI in metin and '(15 satır sınandığı için katı)' in sl.KARAR_KURALI
    satirlar = metin.splitlines()
    lejant = satirlar[next(i for i, x in enumerate(satirlar) if x.startswith('5) AYLIK')) + 1]  # kısaltmalar açık
    assert lejant.startswith('   Sütunlar: BOT=BOT_VEKILI, ') and 'YATAY=YATAY_DONUS' in lejant
    assert 'YATAY sütunu YATAY_DONUS kuralıdır, BTC rejimi değil' in lejant
    i7, i8 = satirlar.index('7) KARAR (önceden kayıtlı kural; ayrıntı 0. bölümde)'), \
        next(i for i, x in enumerate(satirlar) if x.startswith('8) REJİM'))
    karar = [x for x in satirlar[i7 + 1:i8] if x.strip() and not x.strip().startswith('SONUÇ')]
    assert len(karar) == 15 and 'SONUÇ: 15 satırdan 0 tanesi GEÇTİ.' in metin
    bolum8 = satirlar[i8:satirlar.index('', i8)]
    for kural in sl.KURALLAR:                                                   # AI'sız her satır, iki dönem, üç rejim
        for donem in ('kuruluş', 'sınama'):
            for rejim in sl.REJIMLER:
                bas = f"   {kural:19s} {donem:8s} {rejim:12s}"
                assert any(x.startswith(bas) for x in bolum8), (kural, donem, rejim)
    assert not any('+AI' in x for x in bolum8)
    rapor = json.loads((tmp_path / 'lab_rapor.json').read_text(encoding='utf-8'))
    assert rapor['meta']['parite'] == 12 and set(rapor['karar']) == set(an['karar'])
    assert set(rapor['rejim']) == set(sl.KURALLAR) and set(rapor['rejim_dagilim']) == {'kurulus', 'sinama'}
    for kural in sl.KURALLAR:                                                   # rejimlere bölünen = dönemin tamamı
        for d in ('kurulus', 'sinama'):
            assert sum(o['n'] for o in rapor['rejim'][kural][d].values()) == an['sonuclar'][kural][d]['n'], (kural, d)
    dag = rapor['rejim_dagilim']                                                # giriş penceresinin saatleri
    assert (dag['kurulus']['saat'], dag['sinama']['saat']) == (181 * 24, 92 * 24)
    i0, i1 = (int((vs_ms(x) - g0) // SAAT) for x in ('2023-07-01', '2023-10-01'))
    for r in sl.REJIMLER:
        assert dag['sinama'][r] == pytest.approx((sonuc['btc']['rejim'][i0:i1] == r).mean() * 100)
    assert sum(dag['sinama'][r] for r in sl.REJIMLER) == pytest.approx(100)
    assert set(rapor['meta']['ozellikler']) == set(sl.OZELLIKLER) and len(sl.OZELLIKLER) == 17
    csv = pd.read_csv(tmp_path / 'lab_islemler.csv')
    assert list(csv.columns) == sl.CSV_SUTUNLAR and len(csv) == len(isl)
    assert {'rejim', 'bilesen'} <= set(csv.columns) and set(csv['rejim']) <= set(sl.REJIMLER) | {'BILINMIYOR'}
    assert (csv['bilesen'].to_numpy() == isl['bilesen'].to_numpy()).all()
    ai = csv[csv['kural'] == 'TREND_DIP_RSI2']
    assert (ai['ai_secildi'] == (ai['ai_skor'] >= ai['ai_esik'])).all()
    # yalnız REJIM_KOMBO_BOT istenince de bileşen sinyalleri (BOT_VEKILI radarı dahil) hesaplanır: aynı işlemler
    kb = sl.calistir(SaatlikBorsa(veri, tickers), _arguman(tmp_path, '--ayrim', '2023-07-01', '--ai-yok', '--kurallar',
                                                           'REJIM_KOMBO_BOT', '--cikti', str(tmp_path / 'kb')),
                     simdi_ms=SIMDI, log=lambda *x: None)['islemler']
    once = isl[isl['kural'] == 'REJIM_KOMBO_BOT'].reset_index(drop=True)
    assert 'BOT_VEKILI' in set(once['bilesen'])
    pd.testing.assert_frame_equal(kb[sl.CSV_SUTUNLAR[:-3]].reset_index(drop=True), once[sl.CSV_SUTUNLAR[:-3]])
    # ikinci çalıştırma yalnız önbellekten (borsada olmayan sembol önbelleğe yazılamaz: yalnız o yeniden sorulur)
    ex.istek, ex.istenen = 0, set()
    yeni_dizin = tmp_path / 'yeni' / 'alt'                                      # yoksa başta oluşturulur
    sl.calistir(ex, _arguman(tmp_path, '--ayrim', '2023-07-01', '--ai-yok', '--kurallar', 'BOT_VEKILI', 'BTC_TREND',
                             '--cikti', str(yeni_dizin / 'lab')), simdi_ms=SIMDI, log=lambda *x: None)
    assert ex.istenen == {'YOK/USDT'} and ex.istek == 1
    assert all((yeni_dizin / ('lab' + son)).exists() for son in ('_rapor.txt', '_rapor.json', '_islemler.csv'))


def test_uctan_uca_yalniz_btc_trend_evren_indirmez_ve_islemsiz_calisir(tmp_path):
    """Yalnız BTC_TREND: evren seçilmez, yalnız BTC indirilir; hiç işlem yoksa da üç çıktı yazılır."""
    ts = np.arange(VERI_BAS, VERI_BIT, SAAT, dtype=np.int64)
    n = len(ts)
    veri = {'BTC/USDT': _satirlar(ts, (np.full(n, 2e4), np.full(n, 2.001e4), np.full(n, 1.999e4), np.full(n, 2e4),
                                       np.full(n, 2e3)))}                       # yatay: MA100 x 1.02 hiç aşılmaz
    for i in range(4):
        veri[f'C{i:02d}/USDT'] = _satirlar(ts, rastgele_mumlar(n, i + 1, 10.0, 0.012, 1e6))
    ex = SaatlikBorsa(veri)
    a = _arguman(tmp_path, '--bitis', '2023-02-15', '--kurallar', 'BTC_TREND')
    sonuc = sl.calistir(ex, a, simdi_ms=SIMDI, log=lambda *x: None)
    assert ex.istenen == {'BTC/USDT'} and ex.ticker_istegi == 0
    assert sonuc['meta']['parite'] == 1 and len(sonuc['islemler']) == 0
    csv = pd.read_csv(tmp_path / 'lab_islemler.csv')
    assert list(csv.columns) == sl.CSV_SUTUNLAR and len(csv) == 0
    metin = (tmp_path / 'lab_rapor.txt').read_text(encoding='utf-8')
    assert '7) KARAR' in metin and 'BTC_TREND' in metin
    assert json.loads((tmp_path / 'lab_rapor.json').read_text(encoding='utf-8'))['karar']['BTC_TREND']['sonuc'] == 'KALDI'


def test_uctan_uca_bitis_verilmezse_son_tamamlanmis_ay(tmp_path):
    """--bitis yok, 'şimdi' 2023-10-01 05:00: Eylül'ün son girişlerinin 168 saati henüz veride değil, analiz Eylül
    başında biter (yarım ay yok); çıkışlar sonraki 168 saatin verisiyle."""
    ts = np.arange(VERI_BAS, VERI_BIT, SAAT, dtype=np.int64)
    n = len(ts)
    veri = {'BTC/USDT': _satirlar(ts, rastgele_mumlar(n, 0, 20000, 0.005, 5e7))}
    for i in range(3):
        veri[f'C{i:02d}/USDT'] = _satirlar(ts, rastgele_mumlar(n, i + 1, 10.0, 0.012, 3e6))
    a = sl.arguman_ayristirici().parse_args(['--baslangic', '2023-01-01', '--ayrim', '2023-07-01', '--onbellek',
                                             str(tmp_path / 'onb'), '--cikti', str(tmp_path / 'lab'), '--ai-yok',
                                             '--kurallar', 'TREND_DIP_RSI2', 'BOT_VEKILI'])
    sonuc = sl.calistir(SaatlikBorsa(veri), a, simdi_ms=SIMDI, log=lambda *x: None)
    eylul = vs_ms('2023-09-01')
    assert sonuc['meta']['bitis'].startswith('2023-09-01')
    assert sonuc['meta']['veri_sonu'].startswith('2023-09-08')
    assert sonuc['analiz']['sinama_aylari'][-1] == '2023-08'
    isl = sonuc['islemler']
    assert len(isl) and (isl['giris_ts'] < eylul).all()


def test_uctan_uca_gomulu_etki_bulunur(tmp_path):
    """ERKEN_BIRIKIM kurulumlarının ardından yükseliş gelen piyasada kural sınamada kazanır ve karar kuralını geçer;
    aynı kurulumlar rastgele devam ederse geçemez."""
    ts = np.arange(VERI_BAS, VERI_BIT, SAAT, dtype=np.int64)
    n = len(ts)
    btc = 20000 * np.exp(np.cumsum(np.full(n, 0.0002)))
    btc_o = np.r_[btc[0], btc[:-1]]
    sonuclar = {}
    for yukselis in (True, False):
        veri = {'BTC/USDT': _satirlar(ts, (btc_o, btc * 1.001, btc_o * 0.999, btc, np.full(n, 5e7) / btc))}
        for i in range(8):
            veri[f'E{i:02d}/USDT'] = _satirlar(ts, birikim_piyasasi(n, 30 + i, yukselis, faz=37 * i))
        a = _arguman(tmp_path / str(yukselis), '--ayrim', '2023-04-01', '--ai-yok', '--kurallar', 'ERKEN_BIRIKIM',
                     'YUKSEK_ISABET')
        os.makedirs(tmp_path / str(yukselis), exist_ok=True)
        sonuclar[yukselis] = sl.calistir(SaatlikBorsa(veri), a, simdi_ms=SIMDI, log=lambda *x: None)['analiz']
    an = sonuclar[True]
    s = an['sonuclar']['ERKEN_BIRIKIM']['sinama']
    assert s['n'] >= 100 and s['beklenti_pct'] > 1.0 and s['p_pozitif'] == 1.0
    assert an['karar']['ERKEN_BIRIKIM']['sonuc'] == 'GEÇTİ'
    assert an['karar']['YUKSEK_ISABET']['hedef_70']
    assert an['butce']['ERKEN_BIRIKIM']['450']['sinama']['aylik_ort_usdt'] > 0
    rastgele = sonuclar[False]
    assert abs(rastgele['sonuclar']['ERKEN_BIRIKIM']['sinama']['n'] - s['n']) <= 5   # aynı kurulumlar
    assert all(k['sonuc'] == 'KALDI' for k in rastgele['karar'].values())


def test_uctan_uca_yatay_donus_gomulu_etki_bulunur(tmp_path):
    """Trendsiz BTC (saatlerin ~%90'ı YATAY) ve dipten sonra ortalamaya dönen yatay coinler: YATAY_DONUS sınamada
    kazanır ve geçer; trend bileşeni hiç sinyal vermediği için REJIM_KOMBO işlemleri YATAY_DONUS'unkilerin aynısıdır.
    Aynı dipler rastgele devam ederse (dönüş yok) yeni satırların hiçbiri geçmez."""
    ts = np.arange(VERI_BAS, VERI_BIT, SAAT, dtype=np.int64)
    n = len(ts)
    sonuclar = {}
    for donus in (True, False):
        veri = {'BTC/USDT': _satirlar(ts, yatay_btc(n))}
        for i in range(8):
            veri[f'D{i:02d}/USDT'] = _satirlar(ts, donus_piyasasi(n, 30 + i, donus, faz=11 * i))
        a = _arguman(tmp_path / str(donus), '--ayrim', '2023-04-01', '--ai-yok', '--kurallar', 'YATAY_DONUS',
                     'REJIM_KOMBO', 'REJIM_KOMBO_BOT')
        os.makedirs(tmp_path / str(donus), exist_ok=True)
        sonuclar[donus] = sl.calistir(SaatlikBorsa(veri), a, simdi_ms=SIMDI, log=lambda *x: None)
    an, isl = sonuclar[True]['analiz'], sonuclar[True]['islemler']
    s = an['sonuclar']['YATAY_DONUS']['sinama']
    assert s['n'] >= 100 and s['beklenti_pct'] > 1.0 and s['p_pozitif'] == 1.0
    assert an['karar']['YATAY_DONUS']['sonuc'] == 'GEÇTİ'
    assert an['rejim_dagilim']['sinama']['YATAY'] > 80
    assert an['rejim']['YATAY_DONUS']['sinama']['YATAY']['n'] == s['n']             # hepsi YATAY rejiminde
    assert all(an['rejim']['YATAY_DONUS']['sinama'][r]['n'] == 0 for r in ('TREND_YUKARI', 'TREND_ASAGI'))
    y = isl[isl['kural'] == 'YATAY_DONUS'].reset_index(drop=True)
    assert set(y['cikis_tipi']) >= {'SINYAL'} and (y['sure_saat'] <= 24).all() and (y['rejim'] == 'YATAY').all()
    k = isl[isl['kural'] == 'REJIM_KOMBO'].reset_index(drop=True)
    assert set(k['bilesen']) == {'YATAY_DONUS'}
    sutun = [x for x in sl.CSV_SUTUNLAR[:-3] if x != 'kural']
    pd.testing.assert_frame_equal(k[sutun], y[sutun])
    rastgele = sonuclar[False]['analiz']
    r = rastgele['sonuclar']['YATAY_DONUS']['sinama']
    assert r['n'] >= 100 and r['beklenti_pct'] < 0                            # aynı dipler, dönüş yok
    assert all(k['sonuc'] == 'KALDI' for k in rastgele['karar'].values())


def test_uctan_uca_btc_trend_rejimi_karar_saatinden(tmp_path):
    """BTC_TREND işleminin rejimi karar saatinin (karar gününün son saati = giriş - 1 saat) rejimidir. Giriş gününün
    ilk 3 saati ve 12. saati eksik: giriş günü boyunca (ADX son 15 saati ister) rejim BILINMIYOR; rejim girişten ya da
    giriş gününün herhangi bir saatinden (ör. gün sonundan) okunsaydı BILINMIYOR çıkardı."""
    ts = np.arange(VERI_BAS, VERI_BIT, SAAT, dtype=np.int64)
    n = len(ts)
    rng = np.random.default_rng(3)
    c = 20000 * (1 + rng.normal(0, 0.001, n))
    yuksel, gir, dus = (int(np.searchsorted(ts, vs_ms(x))) for x in ('2023-03-01', '2023-03-02', '2023-03-15'))
    c[yuksel:gir] *= np.linspace(1.0, 1.03, gir - yuksel)                       # 1 Mart: kapanış MA100 x 1.02 üstü
    c[gir:] *= 1.03
    c[dus:] *= 0.92                                                             # 15 Mart: MA100 x 0.98 altı, çıkış
    o = np.r_[c[0], c[:-1]]
    d = _satirlar(ts, (o, np.maximum(o, c) * 1.001, np.minimum(o, c) * 0.999, c, np.full(n, 5e7) / c))
    d = np.delete(d, [gir, gir + 1, gir + 2, gir + 12], axis=0)
    a = _arguman(tmp_path, '--ai-yok', '--kurallar', 'BTC_TREND')
    sonuc = sl.calistir(SaatlikBorsa({'BTC/USDT': d}), a, simdi_ms=SIMDI, log=lambda *x: None)
    isl = sonuc['islemler']
    assert len(isl) == 1 and isl['giris_ts'][0] == vs_ms('2023-03-02') and isl['bilesen'][0] == 'BTC_TREND'
    g0 = ((vs_ms('2023-01-01') - sl.ISINMA_GUN * GUN) // SAAT) * SAAT
    i = int((vs_ms('2023-03-02') - g0) // SAAT)
    assert (sonuc['btc']['rejim'][i:i + 24] == 'BILINMIYOR').all() and sonuc['btc']['rejim'][i - 1] == 'TREND_YUKARI'
    assert isl['rejim'][0] == sonuc['btc']['rejim'][i - 1] == 'TREND_YUKARI'
    assert np.isnan(isl[sl.OZELLIKLER].to_numpy(dtype=float)).all()
    assert sonuc['analiz']['rejim']['BTC_TREND']['kurulus']['TREND_YUKARI']['n'] == 1


# ------------------------------------------------------------------------------------------
# Uçtan uca gelecek bilgisi: calistir'in kendi akışı (kesit/radar, BTC dilimi, AI) T'den sonrasını kullanmaz
# ------------------------------------------------------------------------------------------
class _Yakala:
    """calistir içindeki radar, her paritenin geçiş 2'ye GERÇEKTEN verilen girdileri (mumlar, BTC bağlamı, radar
    sütunu), göstergeler ve sinyalleri yakalar (modül fonksiyonları sarılarak)."""

    def __init__(self, monkeypatch):
        self.radar, self.girdi, self.g, self.s, self._sembol = [], {}, {}, {}, None
        o_radar, o_par, o_g, o_s = sl.radar_ilk10, sl.parite_islemleri, sl.gostergeler, sl.sinyaller

        def radar(*a, **k):
            self.radar.append(o_radar(*a, **k))
            return self.radar[-1]

        def par(sembol, O, H, L, C, V, ts, btc, radar_sutunu, *a, **k):
            self._sembol = sembol
            self.girdi[sembol] = {'O': O, 'H': H, 'L': L, 'C': C, 'V': V, 'radar': radar_sutunu,
                                  **{'btc_' + ad: x for ad, x in btc.items()}}
            return o_par(sembol, O, H, L, C, V, ts, btc, radar_sutunu, *a, **k)

        def gos(*a, **k):
            self.g[self._sembol] = o_g(*a, **k)
            return self.g[self._sembol]

        def sin(*a, **k):
            self.s[self._sembol] = o_s(*a, **k)
            return self.s[self._sembol]
        for ad, f in (('radar_ilk10', radar), ('parite_islemleri', par), ('gostergeler', gos), ('sinyaller', sin)):
            monkeypatch.setattr(sl, ad, f)


def _esit(x, y):
    x, y = np.asarray(x), np.asarray(y)
    return np.array_equal(x, y, equal_nan=x.dtype.kind == 'f')


def _bozulmus(d, T, rng, sil_saat=()):
    """d: (ts, o, h, l, c, v) satırları. T'den itibaren O, H, L, C ayrı ayrı x0.5..2 (H/L yeniden sınırlanır), hacim
    x0.2..5; T'den sonraki satırların ~%3'ü ve sil_saat'teki saatler (T'ye göre) silinir (mum yok)."""
    d2 = d.copy()
    m = d2[:, 0] >= T
    d2[m, 1:5] *= rng.uniform(0.5, 2.0, (m.sum(), 4))
    d2[m, 2] = np.maximum(d2[m, 2], d2[m][:, [1, 4]].max(axis=1))
    d2[m, 3] = np.minimum(d2[m, 3], d2[m][:, [1, 4]].min(axis=1))
    d2[m, 5] *= rng.uniform(0.2, 5.0, m.sum())
    sil = (d2[:, 0] > T) & (rng.random(len(d2)) < 0.03)
    return d2[~(sil | np.isin(d2[:, 0], T + SAAT * np.asarray(sil_saat, dtype=np.int64)))]


def test_uctan_uca_calistir_gelecek_bilgisi_kullanmaz(tmp_path, monkeypatch):
    """T'den itibaren TÜM paritelerin (BTC dahil) mumları değiştirilir (mum içi biçim ve hacim de) ve bir kısmı
    silinir (BTC'nin T, T+1, T+2, T+12 saatleri: B'de rejim T'den sonraki gün boyu BILINMIYOR). Evren sabit. GAP
    paritesinde T'den 2 saat önce biten 3 saatlik boşluk var (ADX'siz saatler T'yi kapsar). T öncesindeki radar, BTC
    bağlamı, göstergeler, sinyaller; girişi T'den önce ya da TAM T'de olan işlemlerin karar anı alanları (varlık, rejim,
    bileşen, özellikler, AI skoru, eşiği ve seçimi; giriş fiyatı yalnız T'den önce), T'ye kadar kapanan işlemlerin
    tamamı ve T öncesi AI ayları iki çalıştırmada aynı olmalı. İkinci kesme T2 = bir BTC_TREND girişi: o işlemin
    karar saatindeki alanları da (rejim karar saatinden; giriş günü B2'de BILINMIYOR)."""
    bas, bit, T = vs_ms('2022-08-01'), vs_ms('2024-03-01'), vs_ms('2023-12-01')
    ts = np.arange(bas, bit, SAAT, dtype=np.int64)
    n = len(ts)
    veri = {'BTC/USDT': _satirlar(ts, rastgele_mumlar(n, 0, 20000, 0.005, 5e7))}
    for i in range(9):
        veri[f'C{i:02d}/USDT'] = _satirlar(ts, rastgele_mumlar(n, i + 1, 10.0, 0.012, 1e6 * (0.5 + i / 6)))
    for i in range(3):
        veri[f'E{i:02d}/USDT'] = _satirlar(ts, birikim_piyasasi(n, 30 + i, True, faz=37 * i))
    for i in range(2):
        veri[f'D{i:02d}/USDT'] = _satirlar(ts, donus_piyasasi(n, 40 + i, faz=7 * i))
    bosluk = _satirlar(ts, rastgele_mumlar(n, 77, 5.0, 0.012, 1e6))
    veri['GAP/USDT'] = bosluk[((ts < vs_ms('2023-11-20')) | (ts >= vs_ms('2023-11-21 06:00')))
                              & ((ts < T - 5 * SAAT) | (ts >= T - 2 * SAAT))]
    veri['NEW/USDT'] = _satirlar(ts, rastgele_mumlar(n, 78, 5.0, 0.012, 1.5e6))[ts >= vs_ms('2023-05-01')]
    veri['USDX/USDT'] = np.column_stack([ts, np.ones(n), np.full(n, 1.0002), np.full(n, 0.9998),
                                         1 + np.random.default_rng(7).normal(0, 0.0001, n), np.full(n, 3e6)])
    tickers = {s: {'quoteVolume': 1e9 - i} for i, s in enumerate(veri)}
    rng = np.random.default_rng(123)
    bozuk = {s: _bozulmus(d, T, rng, (0, 1, 2, 12) if s == 'BTC/USDT' else ()) for s, d in veri.items()}
    silinen_T = {'BTC/USDT'}                    # B'de T mumu olmayan parite: oradaki T girişleri (giriş mumu yok) düşer

    def kos(ad, data):
        with monkeypatch.context() as mp:
            yak = _Yakala(mp)
            a = sl.arguman_ayristirici().parse_args(
                ['--baslangic', '2023-01-01', '--bitis', '2024-03-01', '--ayrim', '2023-10-01', '--onbellek',
                 str(tmp_path / (ad + '_onb')), '--cikti', str(tmp_path / ad), '--evren', '30'])
            r = sl.calistir(SaatlikBorsa(data, tickers), a, simdi_ms=bit + 5 * SAAT, log=lambda *x: None)
        r['yak'] = yak
        return r

    A, B = kos('orijinal', veri), kos('bozuk', bozuk)
    g0 = ((vs_ms('2023-01-01') - sl.ISINMA_GUN * GUN) // SAAT) * SAAT
    iT = int((T - g0) // SAAT)                                                  # aracın ızgarasında T
    assert A['kesit'].semboller == B['kesit'].semboller
    assert A['meta']['sabit_coinler'] == B['meta']['sabit_coinler'] == ['USDX/USDT']
    ra, rb = A['yak'].radar[0], B['yak'].radar[0]
    assert _esit(ra[:iT], rb[:iT]) and not _esit(ra[iT:], rb[iT:]) and ra[:iT].any()
    for k in A['btc']:
        assert _esit(A['btc'][k][:iT], B['btc'][k][:iT]), k
        assert not _esit(A['btc'][k][iT:], B['btc'][k][iT:]), k
    assert set(A['yak'].girdi) == set(A['kesit'].semboller)
    radar_degisti = False
    for s, girdi in A['yak'].girdi.items():                                    # kullanılan radar sütunu, BTC, mumlar
        for k, x in girdi.items():
            assert _esit(x[:iT], B['yak'].girdi[s][k][:iT]), (s, k)
        radar_degisti |= not _esit(girdi['radar'][iT:], B['yak'].girdi[s]['radar'][iT:])
    assert radar_degisti
    for s in A['yak'].g:
        for k, x in A['yak'].g[s].items():
            assert _esit(x[:iT], B['yak'].g[s][k][:iT]), (s, k)
        for k, x in A['yak'].s[s].items():
            assert _esit(x[:iT], B['yak'].s[s][k][:iT]), (s, k)
    anahtar = ['kural', 'sembol', 'giris_ts']
    ia, ib = A['islemler'], B['islemler']

    def karar_ani(isl):                         # girişi T'den önce ya da tam T'de (karar mumu T - 1 saat, değişmedi)
        m = (isl['giris_ts'] < T) | ((isl['giris_ts'] == T) & ~isl['sembol'].isin(silinen_T))
        return isl[m].set_index(anahtar).sort_index()
    pa, pb = karar_ani(ia), karar_ani(ib)
    assert pa.index.equals(pb.index)
    tam_T = (pa.index.get_level_values('giris_ts') == T)
    assert tam_T.sum() >= 2 and {'BOT_VEKILI', 'REJIM_KOMBO_BOT'} <= set(pa.index.get_level_values('kural')[tam_T])
    assert np.isfinite(pa['ai_skor'].to_numpy()[tam_T]).any()                 # T'de giren AI skorlu işlem de var
    assert set(pa.index.get_level_values('kural')) >= {'BOT_VEKILI', 'TREND_DIP_RSI2', 'ERKEN_BIRIKIM', 'YATAY_DONUS',
                                                       'REJIM_KOMBO', 'REJIM_KOMBO_BOT'}
    assert {(k, b) for k, b in zip(pa.index.get_level_values('kural'), pa['bilesen'])} >= {
        ('REJIM_KOMBO', 'ERKEN_BIRIKIM'), ('REJIM_KOMBO', 'YATAY_DONUS'), ('REJIM_KOMBO_BOT', 'BOT_VEKILI'),
        ('REJIM_KOMBO_BOT', 'YATAY_DONUS')}                                     # iki kombonun iki bileşeni de
    assert set(pa['rejim']) >= set(sl.REJIMLER)
    assert np.isfinite(pa['ai_skor']).sum() > 0                                # T öncesinde AI skorlu işlemler var
    for kol in sl.OZELLIKLER + ['ai_skor', 'ai_esik', 'ai_secildi', 'rejim', 'bilesen']:   # karar anı alanları
        assert _esit(pa[kol].to_numpy(), pb[kol].to_numpy()), kol
    assert _esit(pa['giris_fiyat'].to_numpy()[~tam_T], pb['giris_fiyat'].to_numpy()[~tam_T])   # T açılışı değişti
    cift = pa.index.get_level_values('kural') != 'BTC_TREND'                   # BTC_TREND'de özellik yok (NaN)
    assert np.isfinite(pa['btc_adx14'][cift]).all() and np.isfinite(pa['adx14'][cift]).mean() > 0.99
    ka = pa[pa['cikis_ts'] <= T]
    assert len(ka) and len(ka) < len(pa)
    for kol in ['cikis_ts', 'cikis_fiyat', 'cikis_tipi', 'getiri_k05', 'getiri_k1', 'getiri_k2', 'sure_saat']:
        assert _esit(ka[kol].to_numpy(), pb.loc[ka.index, kol].to_numpy()), kol
    assert not ia[ia['giris_ts'] >= T].reset_index(drop=True)[['getiri_k1']].equals(
        ib[ib['giris_ts'] >= T].reset_index(drop=True)[['getiri_k1']])
    for kural, kayit in A['ai_aylar'].items():
        once = [x for x in kayit if vs_ms(x['ay'] + '-01') < T]
        assert once == [x for x in B['ai_aylar'][kural] if vs_ms(x['ay'] + '-01') < T], kural
    assert any(x['model'] for k in A['ai_aylar'] for x in A['ai_aylar'][k] if vs_ms(x['ay'] + '-01') < T)
    # ikinci kesme T2 = sınamadaki ilk BTC_TREND girişi (karar: önceki günün kapanışı; dolum o kapanıştan). B2'de BTC
    # T2'den itibaren bozuk ve T2, T2+1, T2+2, T2+12 saatleri eksik: giriş günü boyunca rejim BILINMIYOR
    T2 = int(ia.loc[(ia['kural'] == 'BTC_TREND') & (ia['giris_ts'] >= vs_ms('2023-10-01')), 'giris_ts'].iloc[0])
    a2 = sl.arguman_ayristirici().parse_args(
        ['--baslangic', '2023-01-01', '--bitis', '2024-03-01', '--ayrim', '2023-10-01', '--onbellek',
         str(tmp_path / 'b2_onb'), '--cikti', str(tmp_path / 'b2'), '--kurallar', 'BTC_TREND', '--ai-yok'])
    B2 = sl.calistir(SaatlikBorsa({'BTC/USDT': _bozulmus(veri['BTC/USDT'], T2, rng, (0, 1, 2, 12))}), a2,
                     simdi_ms=bit + 5 * SAAT, log=lambda *x: None)
    iT2 = int((T2 - g0) // SAAT)
    for k in A['btc']:
        assert _esit(A['btc'][k][:iT2], B2['btc'][k][:iT2]), k
    assert (B2['btc']['rejim'][iT2:iT2 + 24] == 'BILINMIYOR').all()
    assert not (A['btc']['rejim'][iT2 - 1:iT2 + 24] == 'BILINMIYOR').any()
    xa = ia[(ia['kural'] == 'BTC_TREND') & (ia['giris_ts'] <= T2)].reset_index(drop=True)
    xb = B2['islemler'][B2['islemler']['giris_ts'] <= T2].reset_index(drop=True)
    assert len(xa) == len(xb) >= 2 and xa['giris_ts'].iloc[-1] == T2
    for kol in ['kural', 'bilesen', 'rejim', 'giris_ts', 'giris_fiyat'] + sl.OZELLIKLER:
        assert _esit(xa[kol].to_numpy(), xb[kol].to_numpy()), kol
    kapandi = (xa['cikis_ts'] <= T2).to_numpy()
    assert kapandi.sum() >= 1
    for kol in ['cikis_ts', 'cikis_fiyat', 'getiri_k05', 'getiri_k1', 'getiri_k2', 'sure_saat']:
        assert _esit(xa[kol].to_numpy()[kapandi], xb[kol].to_numpy()[kapandi]), kol
