# -*- coding: utf-8 -*-
"""tools/strateji_arastirma.py: gelecek bilgisi kullanılmaması, portföy muhasebesi ve uçtan uca çalıştırma."""
import json
import os
import sys

import ccxt
import numpy as np
import pandas as pd
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, 'tools'))
import strateji_arastirma as sa  # noqa: E402

GUN = sa.GUN_MS
T0 = 1_672_531_200_000 - 120 * GUN          # 2023-01-01'den 120 gün önce (UTC gece yarısı)


def _panel(n_gun=400, n_coin=25, tohum=0, donus=0.0):
    """Sentetik günlük panel. donus > 0: küçük coinlerde dünkü getirinin tersi yönde bugünkü getiri."""
    rng = np.random.default_rng(tohum)
    idx = pd.to_datetime(T0 + GUN * np.arange(n_gun), unit='ms')
    kolon = ['BTC/USDT'] + [f'C{i:02d}/USDT' for i in range(n_coin)]
    r = rng.normal(0.0005, 0.03, (n_gun, len(kolon)))
    for t in range(1, n_gun):
        r[t, 6:] -= donus * r[t - 1, 6:]
    C = pd.DataFrame(100 * np.exp(np.cumsum(r, axis=0)), index=idx, columns=kolon)
    V = pd.DataFrame(np.linspace(9e8, 6e6, len(kolon))[None, :].repeat(n_gun, 0), index=idx, columns=kolon)
    return C, V


def test_kurallar_gelecek_bilgisi_kullanmaz():
    C, V = _panel()
    t0 = 250
    w_once = {ad: k(sa.Panel(C, V, 5e6))[0].iloc[:t0 + 1] for ad, k in sa.KURALLAR.items()}
    C2, V2 = C.copy(), V.copy()
    C2.iloc[t0 + 1:] *= np.random.default_rng(9).uniform(0.5, 2.0, C2.iloc[t0 + 1:].shape)   # gelecek değişti
    V2.iloc[t0 + 1:] *= 3
    for ad, k in sa.KURALLAR.items():
        w_sonra = k(sa.Panel(C2, V2, 5e6))[0].iloc[:t0 + 1]
        pd.testing.assert_frame_equal(w_once[ad], w_sonra, check_names=False, obj=ad)


def test_secim_esit_agirlik_ve_uygunluk():
    skor = pd.DataFrame([[3.0, 1.0, 2.0, np.nan], [0.0, 0.0, 0.0, 0.0]], columns=list('abcd'))
    uygun = pd.DataFrame([[True, True, False, True], [False] * 4], columns=list('abcd'))
    w = sa._sec(skor, uygun, 2, True)
    assert w.iloc[0].tolist() == [0.5, 0.5, 0.0, 0.0]        # c uygun değil, d skorsuz
    assert w.iloc[1].sum() == 0                              # uygun coin yok: nakit
    assert sa._sec(skor, uygun, 1, False).iloc[0].tolist() == [0.0, 1.0, 0.0, 0.0]


def test_simule_kayma_devir_ve_ucret():
    idx = pd.date_range('2024-01-01', periods=4)
    R1 = pd.DataFrame({'a': [np.nan, 0.10, 0.0, 0.0], 'b': [np.nan, 0.0, 0.0, 0.0]}, index=idx)
    hedef = pd.DataFrame({'a': [0.5, 0.0, 0.5, 0.0], 'b': [0.0, 0.0, 0.5, 0.0]}, index=idx)
    dengele = pd.Series([True, False, True, False], index=idx)
    s = sa.simule(hedef, dengele, R1, 0.001)
    # gün 0: %50 a alınır (devir 0.5); gün 1: a %10 -> portföy +%5, a'nın payı 0.55/1.05
    assert s['devir'].iloc[1] == pytest.approx(0.5) and s['getiri'].iloc[1] == pytest.approx(0.05 - 0.0005)
    w_a = 0.55 / 1.05
    # gün 2 dengelemesi: a w_a -> 0.5, b 0 -> 0.5
    assert s['devir'].iloc[3] == pytest.approx(abs(0.5 - w_a) + 0.5)
    assert s['getiri'].iloc[3] == pytest.approx(-s['devir'].iloc[3] * 0.001)
    assert s['getiri'].iloc[2] == 0 and s['devir'].iloc[2] == 0   # dengeleme yok, getiri yok


def test_donus_etkisini_bulur_ve_bot_vekili_kaybeder():
    C, V = _panel(n_gun=700, n_coin=30, tohum=3, donus=0.35)
    p = sa.Panel(C, V, 5e6)
    V_bot = V.copy()
    V_bot[:] = 5e7                                            # hepsi botun 12M hacim eşiğinin üstünde
    p_bot = sa.Panel(C, V_bot, 5e6)
    ort = {}
    for ad, k, pan in (('DONUS', sa.kural_donus, p), ('BOT_VEKILI', sa.kural_bot_vekili, p_bot)):
        h, d = k(pan)
        ort[ad] = sa.simule(h, d, pan.R1, 0.0015)['getiri'].iloc[200:].mean()
    assert ort['DONUS'] > 0.003 and ort['BOT_VEKILI'] < -0.003


def test_ozet_ve_bootstrap():
    g = pd.Series(np.r_[np.full(100, 0.01), np.full(100, -0.005)],
                  index=pd.date_range('2024-01-01', periods=200))
    o = sa.ozet(g, pd.Series(1.0, index=g.index), butce=90)
    assert o['gunluk_ort_bps'] == pytest.approx(25.0) and o['gunluk_usdt'] == pytest.approx(0.225)
    assert o['pozitif_gun_pct'] == pytest.approx(50.0) and o['max_dusus_pct'] < 0
    ga = sa.blok_bootstrap_ga(g)
    assert ga[0] < 25.0 < ga[1]
    assert sa.blok_bootstrap_ga(g.head(10)) is None


class GunlukBorsa:
    """Sentetik günlük mumlar: MumDeposu ile aynı arayüz (fetch_ohlcv / fetch_tickers)."""

    def __init__(self, C, V):
        self.veri = {}
        for s in C:
            c = C[s].to_numpy()
            o = np.r_[c[0], c[:-1]]
            ts = np.asarray((C.index - pd.Timestamp('1970-01-01')) // pd.Timedelta(milliseconds=1), dtype='int64')
            self.veri[s] = np.column_stack([ts, o, np.maximum(o, c) * 1.01, np.minimum(o, c) * 0.99, c,
                                            V[s].to_numpy() / c])
        self.istek = 0

    def fetch_tickers(self):
        return {s: {'quoteVolume': float(d[-1, 5] * d[-1, 4])} for s, d in self.veri.items()}

    def fetch_ohlcv(self, sym, tf, since=None, limit=1000):
        self.istek += 1
        if sym not in self.veri or tf != '1d':
            raise ccxt.BadSymbol(sym)
        d = self.veri[sym]
        return d[d[:, 0] >= since][:limit].tolist()


def test_uctan_uca_rapor(tmp_path):
    C, V = _panel(n_gun=1100, n_coin=12, tohum=5, donus=0.2)
    C['USDX/USDT'] = 1.0 + np.random.default_rng(1).normal(0, 0.0002, len(C))   # sabit coin: evrenden çıkmalı
    V['USDX/USDT'] = 5e8
    ex = GunlukBorsa(C, V)
    simdi = int(C.index[-1].value // 10 ** 6) + GUN + 3_600_000
    a = sa.arguman_ayristirici().parse_args(['--baslangic', '2023-01-01', '--ayrim', '2024-06-01', '--evren', '20',
                                             '--onbellek', str(tmp_path / 'onb'), '--cikti', str(tmp_path / 's')])
    sonuc = sa.calistir(ex, a, simdi_ms=simdi, log=lambda *x: None)
    assert sonuc['meta']['sabit_coin'] == 1 and 'USDX/USDT' not in sonuc['panel'].C
    assert set(sonuc['sonuclar']) == set(sa.KURALLAR)
    for ad, d in sonuc['sonuclar'].items():
        assert {'tum', 'kurulus', 'sinama', '2023', '2024'} <= set(d), ad
    metin = (tmp_path / 's_rapor.txt').read_text(encoding='utf-8')
    for bolum in ('1) KURALLAR', '2) YIL YIL', '3) SAĞLAMLIK', '4) KOMİSYON'):
        assert bolum in metin
    rapor = json.loads((tmp_path / 's_rapor.json').read_text(encoding='utf-8'))
    assert rapor['meta']['parite'] == 13
    g = pd.read_csv(tmp_path / 's_gunluk.csv', index_col=0, parse_dates=True)
    assert list(g.columns) == list(sa.KURALLAR) and g.index.min() > pd.Timestamp('2023-01-01')
    # BTC al-tut, BTC'nin kendi getirisiyle aynı (ücret yalnız ilk alımda, dönem öncesinde)
    btc = C['BTC/USDT'].pct_change()
    assert np.allclose(g['BTC_TUT'].to_numpy(), btc.reindex(g.index).to_numpy(), atol=1e-12)
    # ikinci çalıştırma önbellekten: yeni istek yok
    ex.istek = 0
    sa.calistir(ex, a, simdi_ms=simdi, log=lambda *x: None)
    assert ex.istek == 0
