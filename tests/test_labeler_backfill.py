# -*- coding: utf-8 -*-
import csv
import os

import numpy as np
import pandas as pd
import pytest

import backfill_sinyaller as bf
import shadow_labeler as sl
from sniper import etiket_deposu as depo

T0 = 1_789_000_000_000 - (1_789_000_000_000 % 3_600_000)   # saat başına hizalı


def _tf(df1m, dk):
    g = df1m.groupby(df1m['ts'] // (dk * 60_000) * (dk * 60_000))
    return pd.DataFrame({'ts': g['ts'].first().index, 'o': g['o'].first().values, 'h': g['h'].max().values,
                         'l': g['l'].min().values, 'c': g['c'].last().values, 'v': g['v'].sum().values})


def seri_uret(seed, n_dk, bas=1.0, trend=0.0, pompa_dk=()):
    rng = np.random.default_rng(seed)
    r = rng.normal(trend, 0.0012, n_dk)
    for p in pompa_dk:
        r[p:p + 5] += 0.006      # 5 dakikada ~%3 pompa
    c = bas * np.exp(np.cumsum(r))
    o = np.r_[bas, c[:-1]]
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.0005, n_dk)))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.0005, n_dk)))
    v = rng.lognormal(13, 0.3, n_dk)
    for p in pompa_dk:
        v[p:p + 15] *= 12
    return pd.DataFrame({'ts': T0 + 60_000 * np.arange(n_dk), 'o': o, 'h': h, 'l': l, 'c': c, 'v': v})


class SahteBorsa:
    def __init__(self, seriler_1m):
        self.veri = {}
        for s, d in seriler_1m.items():
            self.veri[(s, '1m')] = d
            self.veri[(s, '15m')] = _tf(d, 15)
            self.veri[(s, '1h')] = _tf(d, 60)
        self.cagri = 0

    def fetch_ohlcv(self, sym, tf, since=None, limit=1000):
        self.cagri += 1
        d = self.veri[(sym, tf)]
        if since is not None:
            d = d[d['ts'] >= since]
        return d.head(limit).values.tolist()

    def fetch_tickers(self):
        return {s: {'quoteVolume': 1e9} for (s, tf) in self.veri if tf == '1m'}

    def kes(self, son_ms):
        """Dünyayı son_ms anında dondurur: 1m veri son_ms'de kesilip 15m/1h yeniden toplulaştırılır.
        O anda açık olan 1h mumu (canlıdaki gibi) KISMİ veriyle var olur."""
        return SahteBorsa({s: d[d['ts'] + 60_000 <= son_ms].copy()
                           for (s, tf), d in self.veri.items() if tf == '1m'})


@pytest.fixture(scope='module')
def borsa():
    n = 60 * 24 * 12                       # 12 gün 1m
    pompalar = list(range(60 * 24 * 5, n - 600, 457))
    return SahteBorsa({'BTC/USDT': seri_uret(1, n, 60000, trend=0.00002),
                       'AAA/USDT': seri_uret(2, n, 1.0, pompa_dk=pompalar),
                       'BBB/USDT': seri_uret(3, n, 50.0, pompa_dk=pompalar[::2])})


def test_backfill_sinyal_uretir_ve_etiketler(borsa, tmp_path):
    hedef = str(tmp_path / 'bf.csv')
    simdi = T0 + 12 * 86_400_000
    sayac = bf.calistir(borsa, gun=5, evren_n=2, etiket_tf='1m', hedef=hedef, simdi_ms=simdi,
                        semboller=['AAA/USDT', 'BBB/USDT'])
    assert sayac['etiketli'] >= 5
    df = depo.oku([hedef])
    assert set(df['Kaynak']) == {'backfill'} and df['Anahtar'].is_unique
    assert df['Etiket_Sonuc'].isin(['TP', 'SL', 'KILIT', 'ZAMAN']).all()
    for k in ['Giris_RSI', 'Giris_Vol_Oran', 'Pump_3s', 'BTC_ADX', 'Piyasa_Genislik']:
        assert pd.to_numeric(df[k], errors='coerce').notna().all(), k
    assert (pd.to_numeric(df['Mum_Ilerleme']) == 1.0).all()          # kapalı mum modu
    assert pd.to_numeric(df['OB_Oran'], errors='coerce').isna().all()  # geçmişte üretilemez
    # devam ettirilebilir: ikinci çalıştırma yeni satır yazmaz
    assert bf.calistir(borsa, gun=5, evren_n=2, etiket_tf='1m', hedef=hedef, simdi_ms=simdi,
                       semboller=['AAA/USDT', 'BBB/USDT'])['etiketli'] == 0


def test_backfill_look_ahead_yok(borsa, tmp_path):
    """Sinyal anından SONRAKİ tüm veri silinse de feature'lar birebir aynı olmalı."""
    simdi = T0 + 12 * 86_400_000
    hedef = str(tmp_path / 'tam.csv')
    bf.calistir(borsa, gun=5, evren_n=2, etiket_tf='15m', hedef=hedef, simdi_ms=simdi,
                semboller=['AAA/USDT', 'BBB/USDT'])
    tam = depo.oku([hedef]).sort_values('Ts').reset_index(drop=True)
    saat_basi = tam[tam['Ts'] % 3_600_000 == 0].head(2)
    diger = tam[tam['Ts'] % 3_600_000 != 0].head(2)
    ornekler = pd.concat([saat_basi, diger])
    assert len(ornekler) >= 3
    for _, ornek in ornekler.iterrows():
        _ayni_featurelar(borsa, ornek)


def _ayni_featurelar(borsa, ornek):
    T = int(ornek['Ts'])
    # Canlı bot sinyali T+ε anında görür: o an açılmış mumlar (kısmi veriyle) listededir ve
    # hizalama tarafından atılır/üzerine yazılır. Dünyayı T+1 dk'da dondurmak bunu birebir taklit eder;
    # tam veriyle (T+saatler) aynı feature çıkması, T sonrası verinin kullanılmadığını kanıtlar.
    kesik = borsa.kes(T + 60_000)
    son = T + 86_400_000          # kesik dünyada T+1 dk sonrası zaten yok; üst sınır serbest
    df15 = bf.sayfali_ohlcv(kesik, ornek['Sembol'], '15m', T - 400 * 900_000, son)
    df1h = bf.sayfali_ohlcv(kesik, ornek['Sembol'], '1h', T - 200 * 3_600_000, son)
    btc = bf.sayfali_ohlcv(kesik, 'BTC/USDT', '15m', T - 700 * 900_000, son)
    ctx = bf.btc_baglami(btc)
    seriler = {s: bf.sayfali_ohlcv(kesik, s, '15m', T - 400 * 900_000, son) for s in ['AAA/USDT', 'BBB/USDT']}
    ilk_n, gen = bf.radar_paneli(seriler)
    satirlar = bf.sinyalleri_uret(ornek['Sembol'], df15, df1h, ctx, ilk_n, gen, T - 900_000, False,
                                  bf.SinyalAyarlari())
    yeniden = [s for s in satirlar if s['Anahtar'] == ornek['Anahtar']]
    assert len(yeniden) == 1
    for k in ['Giris_RSI', 'Giris_Vol_Oran', 'Giris_ATR_Pct', 'Pump_3s', 'EMA1h_Uzaklik', 'EMA15m_Uzaklik',
              'Zirve_Uzaklik', 'BTC_1h_Degisim', 'BTC_EMA_Uzaklik', 'BTC_ADX', 'Sym_ADX', 'Piyasa_Genislik']:
        assert float(yeniden[0][k]) == pytest.approx(float(ornek[k]), rel=1e-9, abs=1e-9), k


def _yaz(yol, kolonlar, satirlar):
    with open(yol, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(kolonlar)
        w.writerows(satirlar)


def test_labeler_shadow_ve_core_ayni_etiketle(borsa, tmp_path):
    d1 = borsa.veri[('AAA/USDT', '1m')]
    i = 60 * 24 * 3 + 7
    ts, fiyat = int(d1['ts'].iloc[i]) + 25_000, float(d1['c'].iloc[i])
    shadow = tmp_path / 'shadow.csv'
    _yaz(shadow, ['Ts', 'Sinyal_Zamani', 'Sembol', 'Sebep', 'Sinyal', 'Fiyat', 'Giris_RSI', 'Giris_Vol_Oran',
                  'Giris_ATR_Pct', 'BTC_ADX'],
         [[ts, 'x', 'AAA/USDT', 'REJIM_YATAY', 'MSB', fiyat, 70, 3, 1.2, 15.0],
          [ts + 3_600_000, 'x', 'AAA/USDT', 'AI_RED', 'MSB', fiyat, 70, 3, 1.2, 25.0]])
    giris = pd.Timestamp(ts, unit='ms')
    core = tmp_path / 'core_islem_verileri_v2.csv'
    v1 = ['Islem_Zamani', 'Sembol', 'Sinyal', 'Kasa_Tipi', 'Giris_RSI', 'Giris_Vol_Oran', 'Giris_ATR_Pct',
          'Giris_Fiyat', 'Cikis_Fiyat', 'Kar_Orani', 'Net_Kar_USDT', 'Cikis_Tipi', 'Sure_Saat']
    _yaz(core, v1 + ['BTC_ADX'], [
        [str(giris + pd.Timedelta(hours=1)), 'AAA/USDT', 'MSB', 'NORMAL', 70, 3, 1.2, fiyat, fiyat * 1.03, 3, 0.3, 'DİNAMİK KISMİ KÂR', 1.0, 30],
        [str(giris + pd.Timedelta(hours=2)), 'AAA/USDT', 'MSB', 'NORMAL', 70, 3, 1.2, fiyat, fiyat * 1.02, 2, 0.2, '📈 TREND TAKİPLİ ÇIKIŞ', 2.0, 30],
        # hâlâ açık pozisyon (sadece kısmi satış): etiketlenmemeli
        [str(giris + pd.Timedelta(hours=3)), 'BBB/USDT', 'MSB', 'NORMAL', 60, 3, 1.0, 50, 51, 2, 0.2, 'DİNAMİK KISMİ KÂR', 1.0, 22],
    ])
    adaylar = sl.shadow_adaylari(str(shadow)) + sl.core_pozisyonlari([str(core)])
    assert [a['Kaynak'] for a in adaylar] == ['shadow', 'shadow', 'core']
    c = adaylar[2]
    assert abs(c['Ts'] - ts) <= 1000 and c['Gercek_Getiri'] == pytest.approx(0.5 / 20)
    assert c['Rejim'] == 'TREND' and adaylar[0]['Rejim'] == 'YATAY'

    hedef = str(tmp_path / 'etiketli.csv')
    sayac = sl.etiketle(borsa, adaylar, T0 + 12 * 86_400_000, hedef)
    assert sayac['shadow'] == 2 and sayac['core'] == 1
    df = depo.oku([hedef])
    assert set(df['Kaynak']) == {'shadow', 'core'} and df['Etiket_Sonuc'].notna().all()
    s_giris = df.loc[df['Kaynak'] == 'shadow', 'Etiket_Giris'].iloc[0]
    c_giris = df.loc[df['Kaynak'] == 'core', 'Etiket_Giris'].iloc[0]
    assert s_giris == pytest.approx(fiyat * 1.0005) and c_giris == pytest.approx(fiyat)   # kayma sadece sinyalde
    # idempotent: etiketlenmiş anahtarlar bir daha aday olmaz
    etiketli = depo.etiketli_anahtarlar(hedef)
    assert all(a['Anahtar'] in etiketli for a in adaylar)


def test_labeler_sinyal_dakikasindan_baslar(borsa):
    """V1 since=ts+1 ile 15m mumda sinyal mumunu atlıyordu; V2 sinyalin dakikasını dahil eder."""
    d1 = borsa.veri[('AAA/USDT', '1m')]
    ts = int(d1['ts'].iloc[1000]) + 30_000
    mumlar = sl.mumlari_getir(borsa, 'AAA/USDT', ts)
    assert mumlar[0][0] == int(d1['ts'].iloc[1000]) and len(mumlar) == sl.PENCERE_DK + 1


def test_bozuk_satirlar_atlanir(tmp_path):
    yol = tmp_path / 'shadow.csv'
    _yaz(yol, ['Ts', 'Sembol', 'Fiyat', 'Sebep'], [[1, 'A/USDT', 1.0, 'X'], [2, 'B/USDT', 1.0, 'X', 'fazla']])
    assert [a['Sembol'] for a in sl.shadow_adaylari(str(yol))] == ['A/USDT']


# ------------------------------------------------------------------------------------------
# Bağımsız review bulguları (regresyon)
# ------------------------------------------------------------------------------------------
def test_farkli_semali_dosyalarda_nan_pozisyon_id_anahtarlari_cokertmez(tmp_path):
    """Onarılmış eski yedekte Pozisyon_Id yok, yeni V2'de var: pandas eksik kolonu NaN yapar;
    NaN 'truthy' olduğundan tüm eski pozisyonlar tek 'C|nan' anahtarına çöküyordu."""
    v1 = ['Islem_Zamani', 'Sembol', 'Sinyal', 'Kasa_Tipi', 'Giris_RSI', 'Giris_Vol_Oran', 'Giris_ATR_Pct',
          'Giris_Fiyat', 'Cikis_Fiyat', 'Kar_Orani', 'Net_Kar_USDT', 'Cikis_Tipi', 'Sure_Saat']
    eski = tmp_path / 'core_islem_verileri_v2.csv.yedek.onarildi.csv'
    _yaz(eski, v1, [['2026-09-18 10:00:00', f'S{i}/USDT', 'MSB', 'NORMAL', 60 + i, 3, 1, 1.0 + i, 1, 0, -0.5,
                     '🛑 STOP LOSS (%-2.5)', 0.5] for i in range(5)])
    yeni = tmp_path / 'core_islem_verileri_v2.csv'
    _yaz(yeni, v1 + ['Pozisyon_Id', 'Giris_Ts'],
         [['2026-09-25 10:00:00', 'NEW/USDT', 'MSB', 'NORMAL', 60, 3, 1, 1.0, 0.97, -3, -0.6, 'STOP', 1.0,
           'NEW/USDT|1790330400000', 1790330400000]])
    adaylar = sl.core_pozisyonlari([str(eski), str(yeni)])
    anahtarlar = [a['Anahtar'] for a in adaylar]
    assert len(anahtarlar) == 6 and len(set(anahtarlar)) == 6 and 'C|nan' not in anahtarlar
    assert 'C|NEW/USDT|1790330400000' in anahtarlar


def test_sinyal_dakikasinin_sinyal_oncesi_fiyati_etiketi_bozmaz():
    """Sinyal, dakika içindeki +%3'lük pompanın 40 sn sonrasında: o 1m mumun açılış/dibi sinyalden
    ÖNCEYE ait. Eskiden 'gap' kuralıyla sahte SL -%3.2 çıkıyordu; fiyat sonra hep yükseldiği hâlde."""
    T = 1_790_000_040_000 - (1_790_000_040_000 % 60_000)
    ts_sinyal = T + 40_000
    mumlar = [[T, 1.000, 1.031, 1.000, 1.030, 1e6]]
    p = 1.030
    for i in range(1, 241):
        p *= 1.0003
        mumlar.append([T + i * 60_000, p, p * 1.001, p * 0.9995, p, 1e5])

    class Ex:
        def fetch_ohlcv(self, s, tf, since, limit):
            assert since == T
            return mumlar[:limit]
    alinan = sl.mumlari_getir(Ex(), 'X/USDT', ts_sinyal)
    assert alinan[0][1:5] == [1.030] * 4                     # sadece kapanış (sinyal sonrası ilk kesin fiyat)
    e = sl.sinyali_etiketle(1.030, 1.5, False, alinan, kayma_uygula=True)
    assert e['sonuc'] == 'TP' and e['getiri'] > 0
    hizali = sl.mumlari_getir(Ex(), 'X/USDT', T)              # dakika başı sinyal: mum olduğu gibi kalır
    assert hizali[0] == mumlar[0]
