# -*- coding: utf-8 -*-
import pytest

from sniper.etiketleme import simule_et, sinyali_etiketle, bariyerler, EtiketAyarlari
from sniper.risk_motoru import RiskAyarlari, net_oran

R = RiskAyarlari()
F = R.fee_rate


def fiyat(giris, net):
    return (net * giris * (1 + F) + giris * (1 + F)) / (1 - F)


def mum(o, h, l, c):
    return [0, o, h, l, c, 1.0]


def duz(giris, n=240):
    return [mum(giris, giris, giris, giris) for _ in range(n)]


def test_tp_once():
    g = 1.0
    m = duz(g, 10) + [mum(g, fiyat(g, 0.031), g, g)] + duz(g, 229)
    s = simule_et(g, m, sl=0.025, tp=0.03)
    assert s['sonuc'] == 'TP' and s['getiri'] == pytest.approx(0.03) and s['mum'] == 10


def test_sl_once_ve_ayni_mumda_muhafazakar():
    g = 1.0
    s = simule_et(g, duz(g, 5) + [mum(g, g, fiyat(g, -0.03), g)] + duz(g, 234), sl=0.025, tp=0.03)
    assert s['sonuc'] == 'SL' and s['getiri'] == pytest.approx(-0.025)
    ikisi = [mum(g, fiyat(g, 0.05), fiyat(g, -0.05), g)] + duz(g, 239)
    assert simule_et(g, ikisi, sl=0.025, tp=0.03)['sonuc'] == 'SL'


def test_gap_acilis_bariyer_otesinden_cikar():
    g = 1.0
    acilis = fiyat(g, -0.06)
    s = simule_et(g, [mum(g, g, g, g), mum(acilis, acilis, acilis, acilis)] + duz(g, 238), sl=0.025, tp=0.03)
    assert s['sonuc'] == 'SL' and s['getiri'] == pytest.approx(-0.06)


def test_kar_kilidi():
    g = 1.0
    yukari = fiyat(g, 0.028)
    # tepe ve kilit altı kapanış aynı mumda: kapanış tepeden sonra -> kilitten çıkış
    m = [mum(g, yukari, g, g)] + duz(g, 239)
    s = simule_et(g, m, sl=0.025, tp=0.04)
    assert s['sonuc'] == 'KILIT' and s['getiri'] == pytest.approx(0.01) and s['mum'] == 0
    s2 = simule_et(g, m, sl=0.025, tp=0.04, ayar=EtiketAyarlari(kilit_aktif=False))
    assert s2['sonuc'] == 'ZAMAN'
    # tepe sonrası kilit üstünde kapanış, sonraki mumda kilit altına iniş
    ust = fiyat(g, 0.02)
    m3 = [mum(g, yukari, g, ust), mum(ust, ust, fiyat(g, 0.005), ust)] + duz(ust, 238)
    s3 = simule_et(g, m3, sl=0.025, tp=0.04)
    assert s3['sonuc'] == 'KILIT' and s3['mum'] == 1 and s3['getiri'] == pytest.approx(0.01)


def test_zaman_asimi_ve_eksik_pencere():
    g = 1.0
    s = simule_et(g, duz(g, 240), sl=0.025, tp=0.03)
    assert s['sonuc'] == 'ZAMAN' and s['getiri'] == pytest.approx(net_oran(g, g, F)) and s['mum'] == 239
    assert simule_et(g, duz(g, 100), sl=0.025, tp=0.03) is None


def test_15m_mumlarla_pencere():
    g = 1.0
    s = simule_et(g, duz(g, 16), sl=0.025, tp=0.03, mum_suresi_dk=15)
    assert s['sonuc'] == 'ZAMAN' and s['mum'] == 15


def test_bariyerler_botun_risk_parametrelerini_yansitir():
    assert bariyerler(1.0, False) == (pytest.approx(0.025), pytest.approx(0.03))
    assert bariyerler(3.0, False) == (pytest.approx(0.045), pytest.approx(0.06))
    assert bariyerler(3.0, True)[0] == pytest.approx(0.02)


def test_sinyali_etiketle_kayma():
    s = sinyali_etiketle(1.0, 1.0, False, duz(1.0, 240))
    assert s['giris'] == pytest.approx(1.0005) and s['getiri'] < net_oran(1.0, 1.0, F)
    s2 = sinyali_etiketle(1.0, 1.0, False, duz(1.0, 240), kayma_uygula=False)
    assert s2['giris'] == 1.0
