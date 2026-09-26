# -*- coding: utf-8 -*-
import os

import numpy as np
import pandas as pd
import pytest

import ai_trainer as tr
from sniper import etiket_deposu as depo
from sniper.etiketleme import ETIKET_SURUMU
from sniper.model_karti import ModelYuvasi, kart_oku

T0 = 1_780_000_000_000


def sentetik(n, sinyal_gucu, seed=0, canli_orani=0.1, canli_ema_carpan=1.0):
    rng = np.random.default_rng(seed)
    ts = T0 + np.sort(rng.integers(0, 180 * 86_400_000, n))
    kaynak = np.where(rng.random(n) < canli_orani, 'shadow', 'backfill')
    atr = rng.uniform(0.6, 2.9, n)
    ema15 = atr * rng.uniform(0.5, 5.0, n)            # ATR cinsinden 0.5-5 uzaklık
    ema15 = np.where(kaynak == 'shadow', ema15 * canli_ema_carpan, ema15)   # kovaryat kayması (ilişki aynı)
    rsi = rng.uniform(56, 92, n)
    # sinyal: aşırı uzamış (EMA15m_ATR büyük) ve RSI'ı çok yüksek girişler kötü sonuçlanır
    gizli = -sinyal_gucu * ((ema15 / atr - 2.75) / 1.3 + (rsi - 74) / 10)
    getiri = 0.012 * gizli + rng.normal(0, 0.025, n)
    sonuc = np.where(getiri > 0.02, 'TP', np.where(getiri < -0.02, 'SL', 'ZAMAN'))
    df = pd.DataFrame({
        'Anahtar': [f"X|{t}|{i}" for i, t in enumerate(ts)], 'Kaynak': kaynak, 'Ts': ts,
        'Sembol': rng.choice([f"S{i}/USDT" for i in range(25)], n), 'Sebep': 'BACKFILL', 'Sinyal': 'MSB',
        'Fiyat': 1.0, 'BTC_OK': 1, 'Rejim': 'TREND',
        'Giris_RSI': rsi, 'Giris_Vol_Oran': rng.uniform(2.5, 9, n), 'Giris_ATR_Pct': atr,
        'EMA15m_Uzaklik': ema15, 'EMA1h_Uzaklik': ema15 * 1.4, 'Pump_3s': ema15 * 1.2, 'Pump_6s': ema15 * 1.5,
        'Zirve_Uzaklik': rng.uniform(0, 5, n), 'BTC_1h_Degisim': rng.normal(0, 0.5, n),
        'BTC_ADX': rng.uniform(10, 45, n), 'BTC_EMA_Uzaklik': rng.normal(1, 1, n),
        'Sym_ADX': rng.uniform(10, 60, n), 'Kapali_Mum_Onay': rng.integers(0, 2, n),
        'Piyasa_Genislik': rng.uniform(0.2, 0.8, n), 'Saat': rng.integers(0, 24, n),
        'Etiket_Surumu': ETIKET_SURUMU, 'Etiket_Sonuc': sonuc, 'Etiket_Getiri': getiri,
    })
    return df


def kur(tmp_path, monkeypatch, df):
    yol = tmp_path / 'etiketli.csv'
    depo.yaz(str(yol), df.to_dict('records'))
    for ad, deger in [('CORE_MODEL', tmp_path / 'core_xgboost_model.json'),
                      ('FILTRE_MODEL', tmp_path / 'filter_model.json'),
                      ('LOG_DOSYASI', tmp_path / 'log.txt'), ('RAPOR_DOSYASI', tmp_path / 'rapor.json')]:
        monkeypatch.setattr(tr, ad, str(deger))
    monkeypatch.setattr(tr, 'BOOT_N', 300)
    monkeypatch.setattr(tr, 'PERM_N', 1000)
    return [str(yol)]


def test_gercek_sinyal_yayina_alinir_bot_yukler(tmp_path, monkeypatch):
    yollar = kur(tmp_path, monkeypatch, sentetik(1500, sinyal_gucu=1.0))
    (tmp_path / 'filter_model.json').write_text('{}')          # eski filtre
    rapor = tr.egit(yollar)
    assert rapor['karar'] == 'yayinda', rapor.get('kapilar')
    assert rapor['adaylar'][rapor['secilen']]['oos_auc'] > 0.6
    kart = kart_oku(str(tmp_path / 'core_xgboost_model.json'))
    assert kart['ozellikler'] == rapor['featurelar'] and 'AI_Skor' not in kart['ozellikler']
    assert not (tmp_path / 'filter_model.json').exists()        # emekliye ayrıldı
    assert any(p.startswith('filter_model.json.emekli_') for p in os.listdir(tmp_path))
    # bot tarafı: kartı ve modeli yükleyip canlı metrics sözlüğünden skor üretebilmeli
    yuva = ModelYuvasi(str(tmp_path / 'core_xgboost_model.json'), 'core', 0.65, 'min')
    assert 'kartlı' in yuva.yenile() and yuva.esik == pytest.approx(kart['esik'])
    iyi = {'rsi': 62, 'vol_ratio': 4, 'atr_pct': 1.5, 'ema15m_uzaklik': 1.5, 'ema1h_uzaklik': 2, 'pump_3s': 2,
           'pump_6s': 2, 'zirve_uzaklik': 1, 'btc_1h_degisim': 0, 'btc_adx': 25, 'btc_ema_uzaklik': 1,
           'sym_adx': 30, 'kapali_mum_onay': 1, 'genislik': 0.5, 'saat': 12}
    kotu = dict(iyi, rsi=90, ema15m_uzaklik=7.0)
    assert yuva.skor(iyi) > yuva.skor(kotu)


def test_saf_gurultu_reddedilir(tmp_path, monkeypatch):
    yollar = kur(tmp_path, monkeypatch, sentetik(1500, sinyal_gucu=0.0, seed=5))
    rapor = tr.egit(yollar)
    assert rapor['karar'] == 'reddedildi'
    assert not (tmp_path / 'core_xgboost_model.json').exists()


def test_yetersiz_veri_modele_dokunmaz(tmp_path, monkeypatch):
    yollar = kur(tmp_path, monkeypatch, sentetik(120, sinyal_gucu=1.0))
    assert tr.egit(yollar)['karar'] == 'yetersiz_veri'


def test_kuru_mod_kaydetmez(tmp_path, monkeypatch):
    yollar = kur(tmp_path, monkeypatch, sentetik(1500, sinyal_gucu=1.0))
    assert tr.egit(yollar, kuru=True)['karar'] == 'kuru_gecti'
    assert not (tmp_path / 'core_xgboost_model.json').exists()


def test_purge_etiket_penceresini_egitimden_atar():
    ts = np.arange(0, 100) * 3_600_000                     # saatlik sinyaller
    for egitim, test in tr.katlar(ts, 3):
        assert ts[egitim].max() < ts[test].min() - tr.ETIKET_PENCERE_MS


def test_episodlar():
    df = pd.DataFrame({'Sembol': ['A', 'A', 'A', 'B', 'A'],
                       'Ts': [0, 3_600_000, 20_000_000, 0, 40_000_000]})
    ep = tr.episodlar(df)
    assert ep[0] == ep[1] != ep[2] and ep[3] != ep[0] and ep[4] != ep[2]


def test_ekonomik_test_sans_eseri_artisi_yakalamaz():
    rng = np.random.default_rng(0)
    g = rng.normal(0, 0.02, 400)
    assert tr.ekonomik_test(g, rng.random(400), n=1000)['p'] > 0.05
    assert tr.ekonomik_test(g, g + rng.normal(0, 0.02, 400), n=1000)['p'] < 0.01


def test_esik_yeterli_canli_ornek_varsa_canli_kantilden(tmp_path, monkeypatch):
    """Backfill (kapalı mum) ile canlı (kısmi mum) dağılımı kayınca havuz eşiği canlıda hedeften farklı
    oranda engeller; yeterli canlı OOS örnek varsa eşik canlı skorların kantilinden alınır."""
    df = sentetik(1500, sinyal_gucu=1.0, seed=7, canli_orani=0.1, canli_ema_carpan=1.6)
    yollar = kur(tmp_path, monkeypatch, df)
    rapor = tr.egit(yollar)
    assert rapor['karar'] == 'yayinda'
    e = rapor['esik']
    assert e['esik_kaynagi'] == 'canli' and e['n_canli_oos'] >= tr.MIN_CANLI_ESIK
    assert e['canli_engelleme_havuz_esigiyle'] > 0.4           # havuz eşiği canlıda fazla engellerdi
    assert kart_oku(str(tmp_path / 'core_xgboost_model.json'))['esik'] == pytest.approx(e['esik'])
    assert rapor['ekonomik']['n_episod'] <= rapor['adaylar'][rapor['secilen']]['n_oos']


def test_iki_hesabin_ayni_sinyali_canli_esikte_tek_olay_sayilir(tmp_path, monkeypatch):
    """İki makinenin eğitim verisi birleşince aynı canlı sinyal iki satır olur (aynı coin, saniyeler arayla,
    farklı anahtar). Canlı eşiğe geçiş satır sayısıyla değil bağımsız olay (episod) sayısıyla verilir."""
    df = sentetik(1500, sinyal_gucu=1.0, seed=7, canli_orani=0.035)
    ikinci = df[df['Kaynak'] == 'shadow'].copy()
    ikinci['Anahtar'] = ikinci['Anahtar'] + '|ikinci_hesap'
    ikinci['Ts'] = ikinci['Ts'] + 30_000
    yollar = kur(tmp_path, monkeypatch, pd.concat([df, ikinci], ignore_index=True))
    e = tr.egit(yollar, kuru=True)['esik']
    assert e['n_canli_oos'] >= tr.MIN_CANLI_ESIK > e['n_canli_episod']
    assert abs(e['n_canli_oos'] - 2 * e['n_canli_episod']) <= 2
    assert e['esik_kaynagi'] == 'havuz'
