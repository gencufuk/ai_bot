# -*- coding: utf-8 -*-
import os
import sys

import pandas as pd
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, 'tools'))
import gecmis_simulasyon as gs  # noqa: E402
import kurulum_kontrol as kk  # noqa: E402

V1 = gs.V1


def _yaz(yol, satirlar, baslik=True):
    with open(yol, 'w', encoding='utf-8') as f:
        if baslik:
            f.write(','.join(V1) + '\n')
        for r in satirlar:
            f.write(','.join(str(x) for x in r) + '\n')


def test_kurulum_kontrol_json_iceren_csvyi_yakalar(tmp_path, monkeypatch):
    (tmp_path / 'core_islem_verileri_v2.csv').write_text('{"learner": {}}', encoding='utf-8')
    _yaz(tmp_path / 'core_islem_verileri.csv', [['2026-09-21 00:00:00', 'A/USDT', 'MSB', 'NORMAL', 60, 3, 1, 1, 1.1, 10, 2, 'X', 1]])
    monkeypatch.setattr(kk, 'KOK', str(tmp_path))
    monkeypatch.setattr(kk, 'SONUC', {'✅': 0, '⚠️': 0, '❌': 0})
    kk.csv_dosyalari()
    assert kk.SONUC['❌'] == 1 and kk.SONUC['✅'] == 1


def test_simulasyon_komisyonu_fiyatlardan_hesaplar_ve_yabanci_satirlari_ayiklar(tmp_path):
    satirlar = [
        # komisyonsuz kaydedilmiş dönem (Kar_Orani = brüt): +%2 brüt -> net ~%1.796
        ['2026-06-01 10:00:00', 'A/USDT', 'MSB', 'NORMAL', 60, 3, 1, 1.0, 1.02, 2.0, 0.4, '📈 TREND TAKİPLİ ÇIKIŞ', 1.0],
        # komisyon dahil dönem: kısmi + tam çıkış tek pozisyon
        ['2026-08-10 10:00:00', 'B/USDT', 'MSB', 'NORMAL', 61, 3, 1, 1.0, 1.05, gs.satir_getirisi(1.0, 1.05) * 100, 0.5, 'DİNAMİK KISMİ KÂR', 1.0],
        ['2026-08-10 11:00:00', 'B/USDT', 'MSB', 'NORMAL', 61, 3, 1, 1.0, 1.01, gs.satir_getirisi(1.0, 1.01) * 100, 0.1, '📈 TREND TAKİPLİ ÇIKIŞ', 2.0],
        # başka bot: bilinmeyen çıkış tipi + komisyon dönemi sonrası brüt kayıt
        ['2026-09-06 13:00:00', 'C/USDT', 'MSB', 'NORMAL', 62, 3, 1, 1.0, 1.10, 10.0, 4.0, '🏹 KADEMELİ TRAILING STOP (%2)', 0.5],
        ['2026-09-10 13:00:00', 'D/USDT', 'MSB', 'NORMAL', 62, 3, 1, 1.0, 0.70, -30.0, -12.0, '🛑 STOP LOSS (%-5.0)', 0.0],
    ]
    _yaz(tmp_path / 'h.csv', satirlar, baslik=False)
    df = gs.oku([str(tmp_path / 'h.csv')])
    ana, yabanci, sahipsiz, rapor = gs.temizle(df)
    assert len(yabanci) == 2 and set(yabanci['Sembol']) == {'C/USDT', 'D/USDT'}
    poz = gs.pozisyonlar(ana).set_index('sembol')
    assert poz.loc['A/USDT', 'getiri'] == pytest.approx(gs.satir_getirisi(1.0, 1.02))
    beklenen_b = 0.5 * gs.satir_getirisi(1.0, 1.05) + 0.5 * gs.satir_getirisi(1.0, 1.01)
    assert poz.loc['B/USDT', 'getiri'] == pytest.approx(beklenen_b)


def test_butce_kisiti_eszamanli_pozisyonu_sinirlar():
    t0 = pd.Timestamp('2026-08-01')
    poz = pd.DataFrame([{'sembol': f'S{i}', 'giris': t0 + pd.Timedelta(minutes=i), 'cikis': t0 + pd.Timedelta(hours=5),
                         'kasa_tipi': 'NORMAL', 'getiri': 0.01, 'atr_pct': 1.0, 'n_satir': 1, 'cikislar': 'X'} for i in range(8)])
    islemler, egri, atlanan = gs.oynat(poz, 100, 'sabit')
    assert len(islemler) == 4 and atlanan == 4          # 100 USDT: 20'lik 4 pozisyon (%1 pay ile 5. sığmaz)
    islemler, _, atlanan = gs.oynat(poz, 450, 'sabit')
    assert len(islemler) == 8 and atlanan == 0
