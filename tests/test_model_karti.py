# -*- coding: utf-8 -*-
import csv
import os
import shutil

import numpy as np
import pandas as pd
import pytest
import xgboost as xgb

from sniper.model_karti import ModelYuvasi, modeli_kartla_kaydet

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def kucuk_model(featurelar, seed=0):
    rng = np.random.default_rng(seed)
    X = pd.DataFrame(rng.normal(size=(200, len(featurelar))), columns=featurelar)
    y = (X.iloc[:, 0] + rng.normal(scale=0.5, size=200) > 0).astype(int)
    m = xgb.XGBClassifier(n_estimators=10, max_depth=2)
    m.fit(X, y)
    return m


def test_kartli_model_yukleme_ve_skor(tmp_path):
    yol = str(tmp_path / 'core_xgboost_model.json')
    modeli_kartla_kaydet(kucuk_model(['Giris_RSI', 'EMA15m_ATR']), yol,
                         {'esik': 0.61, 'surum': 't1', 'ozellikler': ['Giris_RSI', 'EMA15m_ATR']})
    y = ModelYuvasi(yol, 'core', 0.65, 'min')
    msg = y.yenile()
    assert 'kartlı' in msg and y.esik == 0.61 and y.featurelar == ['Giris_RSI', 'EMA15m_ATR']
    s = y.skor({'rsi': 70.0, 'atr_pct': 2.0, 'ema15m_uzaklik': 4.0})
    assert 0.0 <= s <= 1.0
    assert y.blokla_mi(0.60) and not y.blokla_mi(0.62) and not y.blokla_mi(None)
    assert y.yenile() is None   # değişiklik yok


def test_hash_uyusmazsa_eski_model_korunur(tmp_path):
    yol = str(tmp_path / 'm.json')
    modeli_kartla_kaydet(kucuk_model(['Giris_RSI']), yol, {'esik': 0.6})
    y = ModelYuvasi(yol, 'core', 0.65, 'min')
    y.yenile()
    eski_model = y.model
    kucuk_model(['Giris_RSI'], seed=5).save_model(yol)     # kart güncellenmeden model değişti
    assert y.yenile() is None and y.model is eski_model


def test_yasak_featureli_model_reddedilir(tmp_path):
    yol = str(tmp_path / 'm.json')
    kucuk_model(['Giris_RSI', 'AI_Skor']).save_model(yol)
    y = ModelYuvasi(yol, 'core', 0.65, 'min')
    assert 'yüklenemedi' in y.yenile() and not y.yuklu


def test_dosya_silinince_model_devre_disi(tmp_path):
    yol = str(tmp_path / 'filter_model.json')
    kucuk_model(['Giris_RSI']).save_model(yol)
    y = ModelYuvasi(yol, 'filtre', 0.45, 'max')
    y.yenile()
    assert y.yuklu
    os.remove(yol)
    assert 'kaldırıldı' in y.yenile() and not y.yuklu


def test_legacy_modeller_canli_kayitli_skorlari_birebir_uretir(tmp_path):
    """16-20 Eylül'de canlıda CSV'ye yazılmış AI_Skor / Filtre_Skor değerleri,
    yeni skorlama yolu (feature_names + kanonik satır) ile birebir yeniden üretilmeli."""
    yedek = os.path.join(REPO, 'veri', 'sunucu_2026-09-26', 'core_islem_verileri_v2.csv.yedek')
    if not os.path.exists(yedek):
        pytest.skip('veri yok')
    for ad in ('core_xgboost_model.json', 'filter_model.json'):
        shutil.copy(os.path.join(REPO, 'veri', 'sunucu_2026-09-26', ad), tmp_path / ad)
    core = ModelYuvasi(str(tmp_path / 'core_xgboost_model.json'), 'core', 0.65, 'min')
    filtre = ModelYuvasi(str(tmp_path / 'filter_model.json'), 'filtre', 0.45, 'max')
    assert 'legacy' in core.yenile() and 'legacy' in filtre.yenile()
    assert core.featurelar == ['Giris_RSI', 'Giris_Vol_Oran', 'Giris_ATR_Pct', 'Sinyal_Encoded']
    satirlar = list(csv.reader(open(yedek, encoding='utf-8')))[1:]
    kontrol = 0
    for r in satirlar:
        ai, fs = float(r[-2]), float(r[-1])
        metrics = {'rsi': float(r[4]), 'vol_ratio': float(r[5]), 'atr_pct': float(r[6]), 'signal': r[2]}
        assert core.skor(metrics) == pytest.approx(ai, abs=1e-7)
        assert filtre.skor(metrics) == pytest.approx(fs, abs=1e-7)
        kontrol += 1
    assert kontrol == 52
