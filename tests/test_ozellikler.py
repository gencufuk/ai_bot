# -*- coding: utf-8 -*-
"""sniper.ozellikler'in V18.3 analyze_market ile birebir aynı feature ürettiğinin kanıtı."""
import datetime
import math
import warnings

import numpy as np
import pandas as pd
import pandas_ta as ta
import pytest

from sniper import ozellikler as oz

warnings.filterwarnings('ignore')
MAX_ATR_PCT = 0.03


def v183_analyze(df_1h, df, ob, simdi, btc, stop_sayisi):
    """ai_bot.py V18.3 analyze_market gövdesi (I/O çıkarılmış, mantık birebir)."""
    ema_20_1h = ta.ema(df_1h['c'], length=20).iloc[-1]
    if len(df) < 30: return None
    df = df.copy()
    df['rsi'], df['atr'] = ta.rsi(df['c'], length=14), ta.atr(df['h'], df['l'], df['c'], length=14)
    rsi_val, atr_val, price = df['rsi'].iloc[-1], df['atr'].iloc[-1], df['c'].iloc[-1]
    if price < ema_20_1h: return None
    if (atr_val / price) > MAX_ATR_PCT: return None
    msb = price > df['h'].iloc[-10:-2].max()
    eng = df['c'].iloc[-2] < df['o'].iloc[-2] and df['c'].iloc[-1] > df['o'].iloc[-1] and df['o'].iloc[-1] < df['c'].iloc[-2]
    vol, av_v = df['v'].iloc[-1], df['v'].rolling(14).mean().iloc[-1]
    vol_ratio = vol / av_v if av_v > 0 else 0
    if not (rsi_val > 55 and vol_ratio > 2.5 and (msb or eng)): return None
    is_whale = (vol_ratio > 3.5) and (rsi_val < 65)
    atr_pct_val = (atr_val / price) * 100
    ema_20_15m = ta.ema(df['c'], length=20).iloc[-1]
    try:
        sym_adx = float(ta.adx(df['h'], df['l'], df['c'], length=14)['ADX_14'].iloc[-1])
    except Exception:
        sym_adx = 0.0
    zirve_24h = df['h'].iloc[-96:].max()
    pump_3s_ref = df['c'].iloc[-13] if len(df) >= 13 else df['c'].iloc[0]
    pump_6s_ref = df['c'].iloc[-25] if len(df) >= 25 else df['c'].iloc[0]
    kapali_mum_onay = int(df['c'].iloc[-2] > df['o'].iloc[-2] and df['v'].iloc[-2] > av_v)
    try:
        bid_v = sum(p * q for p, q in ob['bids']); ask_v = sum(p * q for p, q in ob['asks'])
        ob_oran = float(bid_v / ask_v) if ask_v > 0 else 0.0
    except Exception:
        ob_oran = 0.0
    return {
        "signal": "MSB" if msb else "Engulf", "fiyat": float(price), "rsi": float(rsi_val),
        "vol_ratio": float(vol_ratio), "atr_pct": float(atr_pct_val), "is_whale": bool(is_whale),
        "pump_3s": float((price / pump_3s_ref - 1) * 100), "pump_6s": float((price / pump_6s_ref - 1) * 100),
        "zirve_uzaklik": float((zirve_24h - price) / zirve_24h * 100) if zirve_24h > 0 else 0.0,
        "ema1h_uzaklik": float((price / ema_20_1h - 1) * 100),
        "ema15m_uzaklik": float((price / ema_20_15m - 1) * 100) if ema_20_15m > 0 else 0.0,
        "btc_1h_degisim": float(btc[0]), "btc_ema_uzaklik": float(btc[1]),
        "saat": simdi.hour, "gun": simdi.weekday(), "stop_sayisi": int(stop_sayisi),
        "sym_adx": float(sym_adx), "btc_adx": float(btc[2]), "kapali_mum_onay": kapali_mum_onay,
        "ob_oran": ob_oran, "ai_score": None, "filter_score": None,
    }


def yeni_analyze(df_1h, df, ob, simdi, btc, stop_sayisi):
    temel = oz.sinyal_degerlendir(df, oz.ema_son(df_1h['c'], 20), oz.SinyalAyarlari(max_atr_pct=MAX_ATR_PCT))
    if temel is None:
        return None
    return oz.genisletilmis_ozellikler(df, df_1h, temel, simdi=simdi, simdi_ms=int(df['ts'].iloc[-1]) + 300_000,
                                       btc_1h_degisim=btc[0], btc_ema_uzaklik=btc[1], btc_adx=btc[2],
                                       stop_sayisi=stop_sayisi, ob=ob)


def veri_uret(rng, n15=100, n1h=50, pompa=True):
    fiyat0 = 10 ** rng.uniform(-3, 2)
    vol = rng.uniform(0.002, 0.012)

    def yuru(n, bas, s):
        c = bas * np.cumprod(1 + rng.normal(0.0003, s, n))
        o = np.r_[bas, c[:-1]]
        h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, s / 2, n)))
        l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, s / 2, n)))
        v = rng.lognormal(10, 0.6, n)
        return o, h, l, c, v

    o, h, l, c, v = yuru(n15, fiyat0, vol)
    if pompa:
        c[-1] = c[-2] * (1 + rng.uniform(0.0, 0.05)); h[-1] = max(h[-1], c[-1]); v[-1] *= rng.uniform(1, 8)
    ts0 = 1_789_000_000_000
    df = pd.DataFrame({'ts': ts0 + 900_000 * np.arange(n15), 'o': o, 'h': h, 'l': l, 'c': c, 'v': v})
    o1, h1, l1, c1, v1 = yuru(n1h, c[-1] * rng.uniform(0.9, 1.05), vol * 2)
    df1 = pd.DataFrame({'ts': ts0 + 3_600_000 * np.arange(n1h), 'o': o1, 'h': h1, 'l': l1, 'c': c1, 'v': v1})
    ob = {'bids': [[c[-1] * (1 - 0.0005 * i), rng.uniform(1, 100)] for i in range(1, 21)],
          'asks': [[c[-1] * (1 + 0.0005 * i), rng.uniform(1, 100)] for i in range(1, 21)]}
    return df1, df, ob


def test_v183_ile_birebir_ayni_feature():
    rng = np.random.default_rng(7)
    simdi = datetime.datetime(2026, 9, 21, 13, 5)
    btc = (0.12, 1.3, 24.5)
    gecen = 0
    for _ in range(600):
        df1, df, ob = veri_uret(rng, pompa=rng.random() < 0.8)
        eski = v183_analyze(df1, df, ob, simdi, btc, 1)
        yeni = yeni_analyze(df1, df, ob, simdi, btc, 1)
        assert (eski is None) == (yeni is None)
        if eski is None:
            continue
        gecen += 1
        for k, v in eski.items():
            if isinstance(v, float):
                assert yeni[k] == pytest.approx(v, rel=1e-12, abs=1e-12), k
            else:
                assert yeni[k] == v, k
    assert gecen >= 50, f"kural çok az geçti ({gecen}); test verisi yetersiz"


def test_kisa_1h_gecmis_none_doner():
    rng = np.random.default_rng(1)
    df1, df, ob = veri_uret(rng, n1h=15)
    assert oz.sinyal_degerlendir(df, oz.ema_son(df1['c'], 20)) is None


def test_ob_hatasi_none_yazilir_sifir_degil():
    assert oz.ob_ozellikleri(None)['ob_oran'] is None
    assert oz.ob_ozellikleri({'bids': [['x', 1]], 'asks': []})['ob_oran'] is None
    r = oz.ob_ozellikleri({'bids': [[99, 1], [98, 1]], 'asks': [[101, 1], [120, 5]]})
    assert r['spread_bps'] == pytest.approx(200.0)
    assert r['ob_oran_yakin'] == pytest.approx(99 / 101)


def test_turetilmis_ve_yasak_featurelar():
    df = pd.DataFrame([{'Giris_ATR_Pct': 2.0, 'EMA15m_Uzaklik': 4.0, 'Giris_Vol_Oran': 3.0,
                        'Mum_Ilerleme': 0.2, 'Saat': 6, 'Sinyal': 'MSB'}])
    X = oz.ozellik_matrisi(df, ['EMA15m_ATR', 'Hacim_Hizi', 'Saat_Sin', 'Sinyal_Encoded', 'Pump_3s'])
    assert X.loc[0, 'EMA15m_ATR'] == 2.0 and X.loc[0, 'Hacim_Hizi'] == pytest.approx(15.0)
    assert X.loc[0, 'Saat_Sin'] == pytest.approx(1.0) and X.loc[0, 'Sinyal_Encoded'] == 1.0
    assert math.isnan(X.loc[0, 'Pump_3s'])
    with pytest.raises(ValueError):
        oz.ozellik_matrisi(df, ['Giris_RSI', 'AI_Skor'])
