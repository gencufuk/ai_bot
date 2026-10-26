# -*- coding: utf-8 -*-
"""makine (kâğıt modda piyasa tarayıcı): hafıza (şema, göç, upsert, saklama süresi, tek kopya kilidi), ham klines
toplayıcı (ilk geçmiş, boşluk doldurma, yalnız kapanmış mum, kapsam, ağ hatası, BadSymbol), evren kuralları, yeni
mum/hacim özellikleri (nedensellik, desenler, hacim kalitesi), ileri etiketler, emir defteri özeti, kayan pencerenin
strateji_lab göstergelerine etkisi ve makine.py: kâğıt işlemlerin strateji_lab.calistir ile birebir aynılığı, yeniden
başlatma ve kesinti, evren/hata kuralları, güvenlik (anahtarsız salt okunur borsa, belirteç maskeleme), rapor ve
Telegram, döngü zamanlaması, komut satırı, performans. Sonda inceleme bulgularının regresyon testleri: kayan pencere
ve değişen evrenle parite, PENCERE'den uzun kesinti, turdan bağımsız sinyal çıkışı, gelmeyen giriş mumu, işlemi duran
parite, saat sapması (kısmi mum), yeni listeleme, disk dolu, karar mumu yokluğu, döngü hatası, uyarı hakkı, bozuk
veritabanı, istek sınırı (418/429), belirteç biçimleri, rapor penceresi, kesintide etiket, tanım sürümü. Borsa
sahtedir (ağ yok); saat testte ilerletilir (gerçek bekleme yok)."""
import io
import json
import logging
import os
import sqlite3
import subprocess
import sys
import textwrap
import time

import ccxt
import numpy as np
import pandas as pd
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _yol in (REPO, os.path.join(REPO, 'tools')):
    if _yol not in sys.path:
        sys.path.insert(0, _yol)
import makine as mk  # noqa: E402
import strateji_lab as sl  # noqa: E402
from sniper import hafiza as hz  # noqa: E402
from sniper import makine_ozellikleri as mo  # noqa: E402

SAAT = hz.SAAT_MS
GUN = hz.GUN_MS
T0 = 1_767_225_600_000                  # 2026-01-01 00:00 UTC
SN = 1000


# ------------------------------------------------------------------------------------------
# Sentetik veri ve sahte borsa
# ------------------------------------------------------------------------------------------
def sentetik(n, tohum, s0=10.0, sigma=0.01, hacim=1e6, ts0=T0):
    """Rastgele yürüyüş 1 saatlik mumlar, ham klines sütunlarıyla: (n, 9) = ts, o, h, l, c, v (baz), qv (USDT),
    islem_adedi, alici_qv."""
    rng = np.random.default_rng(tohum)
    c = s0 * np.exp(np.cumsum(rng.normal(0, sigma, n)))
    o = np.r_[s0, c[:-1]]
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, sigma / 3, n)))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, sigma / 3, n)))
    qv = hacim * np.exp(rng.normal(0, 0.6, n))
    v = qv / ((o + c) / 2)
    adet = rng.poisson(qv / 500.0) + 1
    aqv = qv * rng.uniform(0.3, 0.7, n)
    return np.column_stack([ts0 + SAAT * np.arange(n, dtype=np.int64), o, h, l, c, v, qv, adet, aqv])


def kline_satiri(r, acik=False):
    """(9,) satır -> Binance'in 12 alanlı klines satırı (sayılar metin, zamanlar tam sayı). acik: henüz kapanmamış mum
    (kısmi değerler: saklanırsa test yakalar)."""
    ts, o, h, l, c, v, qv, adet, aqv = r
    if acik:
        c, v, qv, adet, aqv = (o + c) / 2, v / 3, qv / 3, adet // 3, aqv / 3
    return [int(ts), repr(float(o)), repr(float(h)), repr(float(l)), repr(float(c)), repr(float(v)),
            int(ts) + SAAT - 1, repr(float(qv)), int(adet), repr(float(v) / 2), repr(float(aqv)), '0']


class SahteBorsa:
    """Binance'in herkese açık arayüzünün sahtesi. veri: {sembol: (n, 9)}; simdi_ms (borsanın saati) testte
    ilerletilir. publicGetKlines startTime/limit'e uyar ve yalnız simdi'de var olan mumları verir (açılışı <= simdi;
    açık mum kısmi değerlerle). Binance gibi sürmekte olan mumu da verir: veride yoksa ve parite son saate kadar
    işlem gördüyse (son kapanmış saat veride) son mumdan türetilen açık mum eklenir; işlemi duran paritede eklenmez.
    fetch_tickers ccxt gibi yüklenmemiş (gizli) paritenin anahtarını eğiksiz kimlikle verir. hata_plani[sembol]:
    sırayla yükseltilecek hatalar (None = o istek başarılı). Emir, bakiye ve özel uç çağrıları AssertionError verir
    ve yasak_cagri'ya kaydedilir (makine asla çağırmamalı; hatayı yakalasa da test görür)."""

    def __init__(self, veri, simdi_ms, tickers=None):
        self.veri, self.simdi_ms, self.tickers = veri, int(simdi_ms), tickers
        self.markets = {s: {'id': s.replace('/', ''), 'symbol': s, 'spot': True, 'active': True} for s in veri}
        self._kimlik = {m['id']: s for s, m in self.markets.items()}
        self.istekler, self.hata_plani, self.yukleme = [], {}, 0
        self.gizli = set()                      # market()'te görünmeyen (henüz yüklenmemiş) semboller
        self.yasak_cagri = []                   # emir/bakiye/özel uç denemeleri (makine yakalasa da kaydedilir)

    def load_markets(self, reload=False):
        self.yukleme += 1
        self.gizli = set()
        return self.markets

    def market(self, sembol):
        if sembol not in self.markets or sembol in self.gizli:
            raise ccxt.BadSymbol(f"binance does not have market symbol {sembol}")
        return self.markets[sembol]

    def publicGetKlines(self, params):
        assert set(params) == {'symbol', 'interval', 'startTime', 'limit'} and params['interval'] == '1h'
        assert 1 <= params['limit'] <= 1000
        s = self._kimlik[params['symbol']]
        self.istekler.append((s, params['startTime'], params['limit']))
        plan = self.hata_plani.get(s)
        if plan:
            hata = plan.pop(0)
            if hata is not None:
                raise hata
        d = self.veri[s]
        d = d[d[:, 0] <= self.simdi_ms]
        acik = (self.simdi_ms // SAAT) * SAAT
        if len(d) and d[-1, 0] == acik - SAAT:              # sürmekte olan mum (veride yok): Binance onu da verir
            d = np.vstack([d, np.r_[acik, d[-1, 1:]][None, :]])
        d = d[d[:, 0] >= params['startTime']][:params['limit']]
        return [kline_satiri(r, acik=r[0] + SAAT > self.simdi_ms) for r in d]

    def fetch_tickers(self):
        """ccxt gibi: yüklü piyasalarda olmayan (gizli) paritenin anahtarı eğiksiz borsa kimliğidir ('YENIUSDT')."""
        t = self.tickers if self.tickers is not None else {
            s: {'quoteVolume': float(d[d[:, 0] < self.simdi_ms][-24:, 6].sum())} for s, d in self.veri.items()}
        return {(s.replace('/', '') if s in self.gizli else s): v for s, v in t.items()}

    def fetch_time(self):
        return self.simdi_ms                    # borsanın saati (testte yerel saatten farklı verilebilir)

    def fetch_ohlcv(self, sym, tf, since=None, limit=1000):
        """strateji_lab'ın MumDeposu'nun kullandığı uç (lab karşılaştırması için; makine kullanmaz)."""
        if sym not in self.veri or tf != '1h':
            raise ccxt.BadSymbol(sym)
        d = self.veri[sym]
        return d[(d[:, 0] >= since) & (d[:, 0] <= self.simdi_ms)][:limit, :6].tolist()

    def fetch_order_book(self, sembol, limit=100):
        c = float(self.veri[sembol][self.veri[sembol][:, 0] < self.simdi_ms][-1, 4])
        return {'bids': [[c * (1 - 0.001 * k), 10.0] for k in range(1, limit + 1)],
                'asks': [[c * (1 + 0.001 * k), 10.0] for k in range(1, limit + 1)]}

    def _yasak(self, *a, **k):
        self.yasak_cagri.append(('emir/bakiye', a, k))
        raise AssertionError('makine emir/bakiye/özel uç çağırdı')

    create_order = cancel_order = fetch_balance = fetch_my_trades = fetch_open_orders = edit_order = withdraw = _yasak
    create_market_buy_order = create_limit_sell_order = cancel_all_orders = fetch_orders = _yasak

    def __getattr__(self, ad):
        if ad.lower().startswith(('private', 'sapi', 'fapi', 'dapi', 'papi', 'create_', 'cancel_', 'edit_')):
            self.yasak_cagri.append(ad)
            raise AssertionError(f'özel uç çağrıldı: {ad}')
        raise AttributeError(ad)


def _hafiza(tmp_path, ad='m.db', **k):
    return hz.Hafiza(str(tmp_path / ad), **k)


def _bekle_kaydi():
    kayit = []
    return kayit, kayit.append


# ------------------------------------------------------------------------------------------
# Hafıza: şema, göç, pragmalar, salt okunur
# ------------------------------------------------------------------------------------------
def _sema(yol):
    c = sqlite3.connect(yol)
    try:
        return sorted(c.execute("SELECT type, name, sql FROM sqlite_master").fetchall(), key=str)
    finally:
        c.close()


def test_sema_pragmalar_ve_goc_idempotent(tmp_path):
    yol = str(tmp_path / 'm.db')
    h = hz.Hafiza(yol)
    assert h.c.execute('PRAGMA auto_vacuum').fetchone()[0] == 2                     # INCREMENTAL
    assert h.c.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    assert h.c.execute('PRAGMA synchronous').fetchone()[0] == 1                     # NORMAL
    assert h.c.execute('PRAGMA busy_timeout').fetchone()[0] == 30000
    assert h.sema_surumu == hz.SEMA_SURUMU and h.meta_oku('sema_surumu') == str(hz.SEMA_SURUMU)
    assert set(hz.TABLOLAR) <= {r[0] for r in h.c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    h.meta_yaz('ilk_baslangic', 123)
    h.kapat()
    once = _sema(yol)
    for _ in range(2):                                                              # yeniden açmak hiçbir şey değiştirmez
        h = hz.Hafiza(yol)
        assert h.sema_surumu == hz.SEMA_SURUMU and h.meta_oku('ilk_baslangic') == '123'
        h.kapat()
    assert _sema(yol) == once
    # ileri göç: yalnız bir kez uygulanır (iki kez uygulansa ALTER 'duplicate column' verirdi)
    yeni = hz.GOCLER + [(hz.SEMA_SURUMU + 1, 'deneme', ['ALTER TABLE tarama ADD COLUMN deneme INTEGER'])]
    for _ in range(2):
        h = hz.Hafiza(yol, gocler=yeni)
        assert h.sema_surumu == hz.SEMA_SURUMU + 1 and 'deneme' in h._sutunlar['tarama']
        h.kapat()
    # eski kod daha yeni şemayı açmaz (yazarak da okuyarak da)
    with pytest.raises(hz.SemaHatasi, match='daha yeni'):
        hz.Hafiza(yol)
    with pytest.raises(hz.SemaHatasi):
        hz.Hafiza(yol, salt_okunur=True)
    # yarım göç kalmaz: hatalı göç tamamen geri alınır, sürüm değişmez
    bozuk = yeni + [(hz.SEMA_SURUMU + 2, 'bozuk', ['CREATE TABLE yarim (x INTEGER)', 'BU SQL DEGIL'])]
    with pytest.raises(sqlite3.OperationalError):
        hz.Hafiza(yol, gocler=bozuk)
    h = hz.Hafiza(yol, gocler=yeni)
    assert h.sema_surumu == hz.SEMA_SURUMU + 1
    assert not h.sorgu("SELECT name FROM sqlite_master WHERE name = 'yarim'")
    h.kapat()


def test_aday_semasi_ozellik_listeleriyle_ayni(tmp_path):
    h = _hafiza(tmp_path)
    beklenen = (['ts', 'sembol'] + list(hz.LAB_OZELLIKLERI) + ['btc_rejim', 'radar_sira', 'evren_sira']
                + list(mo.YENI_OZELLIKLER) + ['sinyaller', 'karar_mumu', 'olusturma_ms', 'tanim_id'])
    assert sorted(h._sutunlar['aday']) == sorted(beklenen) and len(h._sutunlar['aday']) == len(beklenen)
    assert len(hz.LAB_OZELLIKLERI) == 15 and not any(k.startswith('saat_') for k in hz.LAB_OZELLIKLERI)
    tip = {r['name']: r['type'] for r in h.sorgu('PRAGMA table_info(aday)')}
    assert all(tip[k] == 'INTEGER' for k in mo.DESENLER + ('radar_sira', 'evren_sira', 'karar_mumu', 'olusturma_ms',
                                                            'tanim_id'))
    assert all('tanim_id' in h._sutunlar[t] for t in ('aday', 'etiket', 'kagit_islem', 'tarama'))
    assert all(tip[k] == 'REAL' for k in hz.LAB_OZELLIKLERI + mo.MUM_OZELLIKLERI + mo.HACIM_OZELLIKLERI)
    assert set(h._sutunlar['etiket']) >= set(mo.ETIKETLER) | {'eksik_saat'}
    assert set(h._sutunlar['defter']) >= set(mo.DEFTER_ALANLARI)
    assert mo.DOLULUK == sl.DOLULUK and mo.RADAR_ESIK == sl.BOT_HACIM
    h.kapat()


def test_salt_okunur_acilis(tmp_path):
    with pytest.raises(FileNotFoundError, match='veritabanı yok'):
        hz.Hafiza(str(tmp_path / 'yok.db'), salt_okunur=True)
    h = _hafiza(tmp_path)
    h.meta_yaz('x', 'y')
    r = hz.Hafiza(h.yol, salt_okunur=True)                                          # yazan açıkken okunur (WAL)
    assert r.meta_oku('x') == 'y' and r.sayimlar()['meta'] == 2
    with pytest.raises(sqlite3.OperationalError):
        r.meta_yaz('x', 'z')
    with pytest.raises(sqlite3.OperationalError):
        r.c.execute("INSERT INTO meta VALUES ('a', 'b')")
    h.meta_yaz('x', 'z')
    assert r.meta_oku('x') == 'z'
    r.kapat()
    h.kapat()


def test_islem_geri_alma_ve_ic_ice_savepoint(tmp_path):
    h = _hafiza(tmp_path)
    with pytest.raises(RuntimeError):
        with h.islem():
            h.meta_yaz('a', 1)
            raise RuntimeError('x')
    assert h.meta_oku('a') is None
    with h.islem():
        h.meta_yaz('b', 1)
        try:
            with h.islem():
                h.meta_yaz('c', 1)
                raise ValueError('iç')
        except ValueError:
            pass
        h.meta_yaz('d', 1)
    assert (h.meta_oku('b'), h.meta_oku('c'), h.meta_oku('d')) == ('1', None, '1')
    assert not h.c.in_transaction
    h.kapat()


def test_upsert_nan_null_ve_bilinmeyen_sutun(tmp_path):
    h = _hafiza(tmp_path)
    h.aday_yaz([{'ts': T0, 'sembol': 'A/USDT', 'ret24': np.float64(0.05), 'rsi14': np.nan, 'doji': 1.0,
                 'radar_sira': np.int64(3), 'log_vol24': -np.inf, 'sinyaller': 'BOT_VEKILI,ERKEN_BIRIKIM'}])
    r = h.adaylar(T0)[0]
    assert r['ret24'] == 0.05 and r['rsi14'] is None and r['log_vol24'] is None and r['radar_sira'] == 3
    assert r['doji'] == 1 and isinstance(r['doji'], int) and r['sinyaller'] == 'BOT_VEKILI,ERKEN_BIRIKIM'
    h.aday_yaz([{'ts': T0, 'sembol': 'A/USDT', 'ret24': 0.07}])                    # yalnız verilen sütun güncellenir
    r = h.adaylar(T0)[0]
    assert r['ret24'] == 0.07 and r['radar_sira'] == 3 and h.sayimlar()['aday'] == 1
    h.aday_yaz([{'ts': T0, 'sembol': 'B/USDT'}])
    assert h.adaylar(T0)[1]['sinyaller'] == ''
    with pytest.raises(ValueError, match='bilinmeyen sütun'):
        h.aday_yaz([{'ts': T0, 'sembol': 'A/USDT', 'rsi_14': 1.0}])
    with pytest.raises(ValueError, match='anahtar'):
        h.etiket_yaz([{'ts': T0, 'ileri_1s': 0.1}])
    h.kapat()


def test_kagit_islem_tekil_guncelle_ve_durum_kisiti(tmp_path):
    h = _hafiza(tmp_path)
    k = {'strateji': 'REJIM_KOMBO', 'bilesen': 'YATAY_DONUS', 'sembol': 'A/USDT', 'sinyal_ts': T0,
         'giris_ts': T0 + SAAT, 'durum': 'BEKLIYOR', 'rejim': 'YATAY', 'olusturma_ms': T0 + SAAT + 20 * SN}
    i = h.kagit_ekle(k)
    assert i == 1 and h.kagit_ekle(k) is None and h.sayimlar()['kagit_islem'] == 1     # aynı sinyal iki kez açılmaz
    assert h.kagit_ekle({**k, 'strateji': 'YATAY_DONUS', 'bilesen': 'YATAY_DONUS'}) == 2
    h.kagit_guncelle(i, durum='ACIK', giris_fiyat=np.float64(1.5), guncelleme_ms=T0 + 2 * SAAT)
    assert [x['id'] for x in h.kagit_islemler('ACIK')] == [1]
    assert [x['id'] for x in h.kagit_islemler(('ACIK', 'BEKLIYOR'))] == [1, 2]
    assert [x['id'] for x in h.kagit_islemler(strateji='YATAY_DONUS', sembol='A/USDT')] == [2]
    assert h.kagit_islemler('ACIK')[0]['giris_fiyat'] == 1.5
    with pytest.raises(sqlite3.IntegrityError):
        h.kagit_guncelle(i, durum='YANLIS')
    with pytest.raises(ValueError):
        h.kagit_ekle({**k, 'id': 9})
    with pytest.raises(ValueError):
        h.kagit_guncelle(i, kar=1.0)
    h.kapat()


def test_tarama_olay_ve_uyari_sinirlamasi(tmp_path):
    h = _hafiza(tmp_path, saat=lambda: T0 + 5)
    h.tarama_yaz({'ts': T0, 'durum': 'KACIRILDI'})
    h.tarama_yaz({'ts': T0 + SAAT, 'durum': 'TAMAM', 'evren': 150, 'ayrinti': {'sinyal': {'BOT_VEKILI': 2}}})
    h.tarama_yaz({'ts': T0, 'durum': 'TAMAM'})                                        # aynı saat: güncellenir
    t = h.taramalar(T0, T0 + 2 * SAAT)
    assert [x['durum'] for x in t] == ['TAMAM', 'TAMAM'] and t[1]['ayrinti'] == '{"sinyal": {"BOT_VEKILI": 2}}'
    assert h.taramalar(T0 + SAAT) == t[1:]
    h.olay_yaz('HATA', 'x' * 5000, 'A/USDT')
    h.olay_yaz('UYARI', 'y', ms=T0 + 10)
    o = h.olaylar()
    assert [x['tur'] for x in o] == ['UYARI', 'HATA'] and o[1]['ms'] == T0 + 5 and len(o[1]['mesaj']) == 2000
    assert [x['tur'] for x in h.olaylar(tur='HATA')] == ['HATA'] and len(h.olaylar(limit=1)) == 1
    # tanım sürümü: aynı içerik aynı kimlik; değişince yeni kimlik ve önceki kimlik döner
    assert h.tanim_kaydet({'a': 1, 'b': [1, 2]}, T0) == (1, None)
    assert h.tanim_kaydet({'b': [1, 2], 'a': 1}, T0 + 5) == (1, None) and h.sayimlar()['tanim'] == 1
    assert h.tanim_kaydet({'a': 2}, T0 + 9) == (2, 1) and h.meta_oku('son_tanim_id') == '2'
    assert h.tanim_kaydet({'a': 1, 'b': [1, 2]}, T0 + 10) == (1, 2)                 # eski tanıma dönüş
    assert h.sorgu('SELECT ilk_ms FROM tanim ORDER BY id') == [{'ilk_ms': T0}, {'ilk_ms': T0 + 9}]
    h.kapat()


# ------------------------------------------------------------------------------------------
# Hafıza: saklama süresi ve tek kopya kilidi
# ------------------------------------------------------------------------------------------
def test_saklama_yalniz_eski_satirlari_siler_gunde_bir_kez(tmp_path):
    h = _hafiza(tmp_path)
    simdi = T0 + 500 * GUN
    gun = 10
    sinir, mum_sinir, olay_sinir = simdi - gun * GUN, simdi - (gun + hz.MUM_EK_GUN) * GUN, simdi - hz.OLAY_GUN * GUN
    zamanlar = [sinir - SAAT, sinir, sinir + SAAT]
    for t in zamanlar:
        h.aday_yaz([{'ts': t, 'sembol': 'A/USDT'}])
        h.etiket_yaz([{'ts': t, 'sembol': 'A/USDT', 'ileri_1s': 0.0}])
        h.defter_yaz([{'ts': t, 'sembol': 'A/USDT', 'spread_bps': 1.0}])
        h.tarama_yaz({'ts': t - 400 * GUN, 'durum': 'TAMAM'})
        h.kagit_ekle({'strateji': 'BOT_VEKILI', 'bilesen': 'BOT_VEKILI', 'sembol': 'A/USDT', 'sinyal_ts': t - 400 * GUN,
                      'giris_ts': t, 'durum': 'KAPALI', 'olusturma_ms': t})
    h.parite_yaz('A/USDT')
    d = sentetik(3, 0)
    for t in (mum_sinir - SAAT, mum_sinir, mum_sinir + SAAT):
        for s in ('A/USDT', 'Z/USDT'):                                               # Z: parite tablosunda yok
            h.mum_yaz(s, np.r_[t, d[0, 1:]][None, :])
    for t in (olay_sinir - 1, olay_sinir):
        h.olay_yaz('HATA', 'x', ms=t)
    silinen = h.gunluk_temizlik(simdi, aday_gun=gun)
    assert silinen == {'aday': 1, 'etiket': 1, 'defter': 1, 'mum': 2, 'olay': 1}
    assert [r['ts'] for r in h.sorgu('SELECT ts FROM aday ORDER BY ts')] == zamanlar[1:]
    assert [r['ts'] for r in h.sorgu('SELECT ts FROM etiket ORDER BY ts')] == zamanlar[1:]
    assert [r['ts'] for r in h.sorgu('SELECT ts FROM defter ORDER BY ts')] == zamanlar[1:]
    assert h.sorgu('SELECT MIN(ts) AS m FROM mum')[0]['m'] == mum_sinir and h.sayimlar()['mum'] == 4
    assert h.sorgu('SELECT MIN(ms) AS m FROM olay')[0]['m'] == olay_sinir
    assert h.sayimlar()['kagit_islem'] == 3 and h.sayimlar()['tarama'] == 3        # bunlar silinmez
    assert h.gunluk_temizlik(simdi + GUN - simdi % GUN - 1, aday_gun=gun) is None   # aynı UTC günü: yapılmaz
    assert h.gunluk_temizlik(simdi + GUN, aday_gun=gun) is not None
    assert h.c.execute('PRAGMA freelist_count').fetchone()[0] == 0                  # incremental_vacuum
    assert h.boyut() > 0
    h.kapat()


def test_saklama_sonrasi_disk_geri_verilir(tmp_path):
    h = _hafiza(tmp_path)
    satirlar = [{'ts': T0 + i * SAAT, 'sembol': f'S{j:03d}/USDT', 'ret24': 0.1 * i, 'rsi14': 50.0, 'adx14': 20.0}
                for i in range(200) for j in range(50)]
    h.aday_yaz(satirlar)
    h.c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    once = os.path.getsize(h.yol)
    h.temizle(T0 + 400 * GUN + 300 * SAAT, aday_gun=400)
    assert h.sayimlar()['aday'] == 0 and os.path.getsize(h.yol) < once / 2
    h.kapat()


def test_tek_kopya_kilidi(tmp_path):
    yol = str(tmp_path / 'm.db.lock')
    k1 = hz.TekKopya(yol).al()
    assert open(yol, encoding='utf-8').read().strip() == str(os.getpid())
    with pytest.raises(hz.ZatenCalisiyor, match=f'zaten çalışıyor \\(PID {os.getpid()}'):
        hz.TekKopya(yol).al()
    k1.birak()
    with hz.TekKopya(yol):                                                           # bırakılınca yeniden alınır
        pass
    # başka süreç tutarken alınamaz; süreç ölünce (bayat kilit dosyası kalsa da) alınır
    kod = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {REPO!r})
        from sniper.hafiza import TekKopya
        kilit = TekKopya({yol!r}).al()
        print('tamam', flush=True)
        sys.stdin.readline()
    """)
    p = subprocess.Popen([sys.executable, '-c', kod], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == 'tamam'
        with pytest.raises(hz.ZatenCalisiyor, match=f'PID {p.pid}'):
            hz.TekKopya(yol).al()
    finally:
        p.stdin.close()
        p.wait(timeout=30)
    assert os.path.exists(yol)
    hz.TekKopya(yol).al().birak()


# ------------------------------------------------------------------------------------------
# Mumlar: ızgara
# ------------------------------------------------------------------------------------------
def test_mum_upsert_idempotent_ve_izgara_strateji_lab_ile_ayni(tmp_path):
    h = _hafiza(tmp_path)
    d = sentetik(30, 1)
    d[5, 4] = 0.0                                       # bozuk fiyat: mumun tamamı NaN
    d[7, 5] = 0.0                                       # sıfır baz hacim: yalnız V NaN (strateji_lab)
    d[8, 6:9] = 0.0                                     # işlemsiz saat: qv, adet, alıcı 0 kalır
    d = np.delete(d, [10, 11], axis=0)                  # 2 eksik saat
    assert h.mum_yaz('A/USDT', d) == 28 and h.mum_yaz('A/USDT', d) == 28 and h.sayimlar()['mum'] == 28
    d2 = d.copy()
    d2[0, 4] = 99.0
    h.mum_yaz('A/USDT', d2[:1])
    assert h.mumlar('A/USDT', T0, T0 + SAAT)[0, 4] == 99.0 and h.sayimlar()['mum'] == 28
    h.mum_yaz('A/USDT', d[:1])
    assert h.son_mum_ts('A/USDT') == T0 + 29 * SAAT and h.son_mum_ts('B/USDT') is None
    np.testing.assert_array_equal(h.mumlar('A/USDT', T0, T0 + 30 * SAAT), d)
    g = h.izgara('A/USDT', T0 + 29 * SAAT, n=40)                                    # pencere veriden uzun: baş NaN
    beklenen = sl.izgaraya_yerlestir(d[:, :6], T0 - 10 * SAAT, 40)
    for x, y in zip((g.O, g.H, g.L, g.C, g.V), beklenen):
        np.testing.assert_array_equal(x, y)
    assert g.ts[0] == T0 - 10 * SAAT and g.ts[-1] == T0 + 29 * SAAT and len(g.ts) == 40
    i = 10 + np.arange(30)
    assert np.isnan(g.C[10 + 5]) and np.isnan(g.QV[10 + 5]) and np.isnan(g.N[10 + 5]) and np.isnan(g.AQV[10 + 5])
    assert np.isnan(g.V[10 + 7]) and g.QV[10 + 7] == d[7, 6]
    assert g.QV[10 + 8] == 0 and g.N[10 + 8] == 0 and g.AQV[10 + 8] == 0
    assert np.isnan(g.C[[20, 21]]).all() and np.isnan(g.N[[20, 21]]).all()
    dolu = np.isfinite(g.C[i])
    assert dolu.sum() == 27
    ok = np.isfinite(g.QV)
    assert set(g.ts[ok].tolist()) == set(d[(d[:, 4] > 0), 0].astype(np.int64).tolist())
    np.testing.assert_array_equal(g.QV[ok], d[d[:, 4] > 0, 6])
    assert h.izgara('A/USDT', T0 + 29 * SAAT, n=40).C.tobytes() == g.C.tobytes()
    assert set(h.izgaralar(['A/USDT', 'B/USDT'], T0 + 29 * SAAT, 40)) == {'A/USDT', 'B/USDT'}
    assert np.isnan(h.izgara('B/USDT', T0, 5).C).all()
    h.kapat()


# ------------------------------------------------------------------------------------------
# Mum toplayıcı (ham klines)
# ------------------------------------------------------------------------------------------
def test_klines_ayristir():
    d = sentetik(4, 2)
    ham = [kline_satiri(r) for r in d[::-1]] + [kline_satiri(d[0])]                 # ters sıra ve tekrar
    ham += [[1, '2'], None, [T0, 'x', '1', '1', '1', '1', 0, '1', 1, '1', '1', '0'],
            [T0 + 9 * SAAT, 'inf', '1', '1', '1', '1', 0, '1', 1, '1', '1', '0']]
    a = hz.klines_ayristir(ham)
    np.testing.assert_array_equal(a, d)
    assert hz.klines_ayristir([]).shape == (0, 9) and hz.klines_ayristir(None).shape == (0, 9)


def test_ilk_gorulmede_1500_saat_sayfali_yalniz_kapanmis_mum(tmp_path):
    d = sentetik(2000, 3)
    simdi = int(d[1800, 0]) + 20 * SN                                               # 1800. mum açık
    ex = SahteBorsa({'A/USDT': d}, simdi)
    h = _hafiza(tmp_path, saat=lambda: simdi)
    t = hz.MumToplayici(ex, h, uyku=lambda s: pytest.fail('beklenmemeli'))
    s = t.guncelle(['A/USDT'], simdi)
    assert s['yeni'] == {'A/USDT': 1500} and not s['hata'] and s['istek'] == 2
    assert ex.istekler == [('A/USDT', int(d[300, 0]), 1000), ('A/USDT', int(d[1300, 0]), 501)]
    np.testing.assert_array_equal(h.mumlar('A/USDT', 0, 2 ** 62), d[300:1800])     # açık mum yok, değerler birebir
    p = h.parite_oku('A/USDT')
    assert p['kapsam_ts'] == d[1800, 0] and p['market_id'] == 'AUSDT' and p['ilk_gorulme_ms'] == simdi
    assert p['durum'] == 'AKTIF' and p['sabit'] == 0
    # aynı saat içinde yeniden: istek yok
    assert t.guncelle(['A/USDT'], simdi + 30 * 60 * SN)['istek'] == 0
    # sonraki saat: yalnız yeni mum (kapsamdan), tek istek
    ex.istekler.clear()
    simdi += SAAT
    ex.simdi_ms = simdi
    assert t.guncelle(['A/USDT'], simdi)['yeni'] == {'A/USDT': 1}
    assert ex.istekler == [('A/USDT', int(d[1800, 0]), 2)]
    # 10 saat kapalı kaldı: boşluk doldurulur
    ex.istekler.clear()
    simdi += 10 * SAAT
    ex.simdi_ms = simdi
    assert t.guncelle(['A/USDT'], simdi)['yeni'] == {'A/USDT': 10} and len(ex.istekler) == 1
    np.testing.assert_array_equal(h.mumlar('A/USDT', 0, 2 ** 62), d[300:1811])
    assert h.parite_oku('A/USDT')['kapsam_ts'] == d[1811, 0]
    h.kapat()


def test_uzun_kesintide_en_cok_pencere_kadar_geri_doldurulur(tmp_path):
    d = sentetik(4000, 4)
    simdi = int(d[1600, 0]) + 20 * SN
    ex = SahteBorsa({'A/USDT': d}, simdi)
    h = _hafiza(tmp_path)
    t = hz.MumToplayici(ex, h)
    t.guncelle(['A/USDT'], simdi)
    simdi = int(d[3900, 0]) + 20 * SN                                               # 2300 saat kapalı
    ex.simdi_ms = simdi
    ex.istekler.clear()
    assert t.guncelle(['A/USDT'], simdi)['yeni'] == {'A/USDT': 1500}
    assert ex.istekler[0][1] == d[2400, 0]
    ts = h.mumlar('A/USDT', 0, 2 ** 62)[:, 0]
    assert len(ts) == 3000 and ts[1499] == d[1599, 0] and ts[1500] == d[2400, 0]   # arada boşluk kalır
    h.kapat()


def test_kapsam_borsa_gecikmesinde_ilerlemez_kalici_boslukta_ilerler(tmp_path):
    d = sentetik(1600, 5)
    bakim = [1200, 1201, 1202]                                                      # borsada kalıcı boşluk
    veri = np.delete(d, bakim, axis=0)
    gecikmeli = veri[veri[:, 0] < d[1549, 0]]                                       # son kapanmış mum henüz yok
    simdi = int(d[1550, 0]) + 20 * SN
    ex = SahteBorsa({'A/USDT': gecikmeli}, simdi)
    h = _hafiza(tmp_path)
    t = hz.MumToplayici(ex, h)
    t.guncelle(['A/USDT'], simdi)
    assert h.parite_oku('A/USDT')['kapsam_ts'] == d[1549, 0]                         # 1549 boşluk sayılmadı
    ex.veri['A/USDT'] = veri
    ex.istekler.clear()
    t.guncelle(['A/USDT'], simdi + 5 * SN)                                          # aynı saat: geciken mum gelir
    assert ex.istekler == [('A/USDT', int(d[1549, 0]), 2)]
    assert h.son_mum_ts('A/USDT') == d[1549, 0] and h.parite_oku('A/USDT')['kapsam_ts'] == d[1550, 0]
    g = h.izgara('A/USDT', int(d[1549, 0]), 400)
    assert np.isnan(g.C[-350:-347]).all() and np.isfinite(g.C[-347:]).all()
    h.kapat()


def test_ag_hatasi_yeniden_dener_sonra_parite_atlanir_tur_olmez(tmp_path):
    veri = {s: sentetik(1000, i) for i, s in enumerate(['A/USDT', 'B/USDT', 'C/USDT'])}
    simdi = int(veri['A/USDT'][900, 0]) + 20 * SN
    ex = SahteBorsa(veri, simdi)
    ex.hata_plani['A/USDT'] = [ccxt.NetworkError('ağ'), ccxt.RequestTimeout('zaman aşımı')]
    ex.hata_plani['B/USDT'] = [ccxt.ExchangeNotAvailable('bakım')] * 4
    ex.hata_plani['C/USDT'] = [ccxt.RateLimitExceeded('429'), None]
    h = _hafiza(tmp_path)
    bekleme, uyku = _bekle_kaydi()
    s = hz.MumToplayici(ex, h, uyku=uyku).guncelle(['A/USDT', 'B/USDT', 'C/USDT'], simdi)
    assert s['yeni'] == {'A/USDT': 900, 'C/USDT': 900} and list(s['hata']) == ['B/USDT']
    assert s['hata']['B/USDT'].startswith('ExchangeNotAvailable') and not s['yok'] and not s['atlanan']
    assert bekleme == [2, 4] + [2, 4, 8] + [2]
    assert s['istek'] == 3 + 4 + 2
    assert h.parite_oku('B/USDT')['son_hata'].startswith('ExchangeNotAvailable')
    assert h.parite_oku('B/USDT')['kapsam_ts'] is None and h.son_mum_ts('B/USDT') is None
    # başka bir hata türü (borsa hatası) de turu öldürmez; yeniden denenmez
    ex.hata_plani['B/USDT'] = [ccxt.ExchangeError('beklenmeyen')]
    bekleme.clear()
    s = hz.MumToplayici(ex, h, uyku=uyku).guncelle(['B/USDT'], simdi)
    assert s['hata']['B/USDT'].startswith('ExchangeError') and bekleme == []
    s = hz.MumToplayici(ex, h, uyku=uyku).guncelle(['B/USDT'], simdi)               # sonraki tur: alınır
    assert s['yeni'] == {'B/USDT': 900}
    h.kapat()


def test_sayfa_yarida_kalirsa_kapsam_yazilana_kadar(tmp_path):
    d = sentetik(2000, 6)
    simdi = int(d[1800, 0]) + 20 * SN
    ex = SahteBorsa({'A/USDT': d}, simdi)
    ex.hata_plani['A/USDT'] = [None] + [ccxt.NetworkError('ağ')] * 4
    h = _hafiza(tmp_path)
    t = hz.MumToplayici(ex, h, uyku=lambda s: None)
    s = t.guncelle(['A/USDT'], simdi)
    assert 'A/USDT' in s['hata'] and h.sayimlar()['mum'] == 1000
    assert h.parite_oku('A/USDT')['kapsam_ts'] == d[1300, 0]
    ex.istekler.clear()
    assert t.guncelle(['A/USDT'], simdi)['yeni'] == {'A/USDT': 500} and ex.istekler[0][1] == d[1300, 0]
    np.testing.assert_array_equal(h.mumlar('A/USDT', 0, 2 ** 62), d[300:1800])
    h.kapat()


def test_ust_uste_ag_hatasi_turun_kalanini_keser(tmp_path):
    semboller = [f'S{i:02d}/USDT' for i in range(12)]
    veri = {s: sentetik(200, i) for i, s in enumerate(semboller)}
    simdi = int(veri[semboller[0]][150, 0]) + 20 * SN
    ex = SahteBorsa(veri, simdi)
    ex.hata_plani[semboller[0]] = [ccxt.NetworkError('ağ')] * 4                     # tek hata: sayaç sıfırlanır
    for s in semboller[2:]:
        ex.hata_plani[s] = [ccxt.NetworkError('ağ')] * 4
    h = _hafiza(tmp_path)
    s = hz.MumToplayici(ex, h, uyku=lambda x: None).guncelle(semboller, simdi)
    assert list(s['yeni']) == [semboller[1]] and list(s['hata']) == [semboller[0]] + semboller[2:7]
    assert s['atlanan'] == semboller[7:] and s['istek'] == 4 + 1 + 5 * 4
    h.kapat()


def test_badsymbol_parite_birakilir_ve_yeni_listeleme_icin_piyasalar_yenilenir(tmp_path):
    veri = {s: sentetik(300, i) for i, s in enumerate(['A/USDT', 'YENI/USDT', 'DELIST/USDT'])}
    simdi = int(veri['A/USDT'][250, 0]) + 20 * SN
    ex = SahteBorsa(veri, simdi)
    ex.gizli = {'YENI/USDT'}                                    # piyasalar yüklendikten sonra listelenmiş
    ex.hata_plani['DELIST/USDT'] = [ccxt.BadSymbol('Invalid symbol.')]
    h = _hafiza(tmp_path)
    t = hz.MumToplayici(ex, h)
    s = t.guncelle(['A/USDT', 'YENI/USDT', 'YOK/USDT', 'DELIST/USDT'], simdi)
    assert s['yeni'] == {'A/USDT': 250, 'YENI/USDT': 250} and s['yok'] == ['YOK/USDT', 'DELIST/USDT']
    assert ex.yukleme == 1 and not s['hata']                                        # turda tek yeniden yükleme
    p = h.parite_oku('YOK/USDT')
    assert p['durum'] == 'YOK' and p['durum_ms'] == simdi and 'BadSymbol' in p['son_hata']
    assert h.parite_oku('DELIST/USDT')['durum'] == 'YOK'
    ex.istekler.clear()
    s = t.guncelle(['YOK/USDT', 'DELIST/USDT'], simdi + 23 * SAAT)                  # bekleme süresinde denenmez
    assert s['yok'] == ['YOK/USDT', 'DELIST/USDT'] and s['istek'] == 0 and not ex.istekler
    ex.simdi_ms = simdi + 24 * SAAT
    s = t.guncelle(['DELIST/USDT'], simdi + 24 * SAAT)                              # 24 saat sonra yeniden: geri döndü
    assert s['yeni'] == {'DELIST/USDT': 274} and h.parite_oku('DELIST/USDT')['durum'] == 'AKTIF'
    h.kapat()


def test_sabit_coin_karari_strateji_lab_kurali(tmp_path):
    n = 1000
    d = sentetik(n, 7)
    sabit = d.copy()
    rng = np.random.default_rng(0)
    sabit[:, 4] = 1 + rng.normal(0, 0.0001, n)
    sabit[:, 1] = np.r_[1.0, sabit[:-1, 4]]
    sabit[:, 2] = np.maximum(sabit[:, 1], sabit[:, 4]) * 1.0001
    sabit[:, 3] = np.minimum(sabit[:, 1], sabit[:, 4]) * 0.9999
    sabit[800:804, 1:5] = 0.9                                                       # peg kaybı ilk 720 saatten sonra
    peg = sabit.copy()
    peg[300:304, 1:5] = 0.9                                                         # peg kaybı ilk 720 saatin içinde
    veri = {'BTC/USDT': sabit.copy(), 'USDX/USDT': sabit, 'A/USDT': d, 'YENI/USDT': sabit[-100:].copy(), 'PEG/USDT': peg}
    simdi = int(d[-1, 0]) + SAAT + 20 * SN
    ex = SahteBorsa(veri, simdi)
    h = _hafiza(tmp_path)
    hz.MumToplayici(ex, h).guncelle(list(veri), simdi)
    assert sl.saatlik_oynaklik(sabit[:, 4]) > sl.SABIT_COIN_STD                       # tümüne bakılsa sabit değil
    assert h.parite_oku('USDX/USDT')['sabit'] == 1 and h.parite_oku('A/USDT')['sabit'] == 0
    assert h.parite_oku('BTC/USDT')['sabit'] == 0                                   # BTC asla
    assert h.parite_oku('PEG/USDT')['sabit'] == 0
    assert h.sabit_karari('YENI/USDT') is True and h.parite_oku('YENI/USDT')['sabit'] is None   # geçici karar
    assert h.sabit_karari('HIC/USDT') is None
    # kalıcı karar tam 720. dolu saatte (bozuk mum sayılmaz)
    z = sabit[:721].copy()
    z[10, 4] = -1.0
    h.mum_yaz('Z/USDT', z[:720])
    assert h.sabit_karari('Z/USDT') is True and h.parite_oku('Z/USDT') is None      # geçici: yazılmaz
    h.mum_yaz('Z/USDT', z[720:])
    assert h.sabit_karari('Z/USDT') is True and h.parite_oku('Z/USDT')['sabit'] == 1
    h.kapat()


def test_kaynakta_emir_ya_da_anahtar_yok():
    """Kaynakta (büyük/küçük harf duyarsız) emir, bakiye, hesap, para çekme ve özel uç adları ya da anahtar okuma
    yok (ccxt'nin örtük özel uçları: private*, sapi*, fapi*, dapi*, papi*)."""
    yasaklar = ('create_', 'cancel_', 'edit_order', 'fetch_balance', 'fetch_my_trades', 'fetch_open_orders',
                'fetch_closed_orders', 'fetch_orders', 'fetch_order(', 'withdraw', 'transfer', 'apikey', 'secret',
                'password', 'binance_api_key', 'binance_secret_key', 'private', 'sapi', 'fapi', 'dapi', 'papi',
                'margin', 'leverage')
    for ad in ('makine.py', 'sniper/hafiza.py', 'sniper/makine_ozellikleri.py'):
        kaynak = open(os.path.join(REPO, ad), encoding='utf-8').read().lower()
        for yasak in yasaklar:
            assert yasak not in kaynak, (ad, yasak)


# ------------------------------------------------------------------------------------------
# Evren
# ------------------------------------------------------------------------------------------
def test_evren_kurallari():
    t = {'BTC/USDT': {'quoteVolume': 1.0},                                          # hacmi düşük de olsa her zaman
         'ETH/USDT': {'quoteVolume': 9e8}, 'XRP/USDT': {'quoteVolume': 8e8}, 'EOS/USDT': {'quoteVolume': 7e6},
         'USDC/USDT': {'quoteVolume': 5e9}, 'FDUSD/USDT': {'quoteVolume': 4e9}, 'WLD/USDT': {'quoteVolume': 3e8},
         'BTCUP/USDT': {'quoteVolume': 6e8}, 'ETHDOWN/USDT': {'quoteVolume': 6e8}, 'XRPBULL/USDT': {'quoteVolume': 6e8},
         'EOSBEAR/USDT': {'quoteVolume': 6e8},
         'JUP/USDT': {'quoteVolume': 5e7}, 'SYRUP/USDT': {'quoteVolume': 4e7},       # gerçek coin: ek benzerliği
         '币安人生/USDT': {'quoteVolume': 9e9}, 'ETH/BTC': {'quoteVolume': 9e9}, 'BTC/USDT:USDT': {'quoteVolume': 9e9},
         'AZ/USDT': {'quoteVolume': 4.9e6}, 'ESIK/USDT': {'quoteVolume': 5e6}, 'BOS/USDT': {'quoteVolume': None},
         'KAPALI/USDT': {'quoteVolume': 6e7}, 'VADELI/USDT': {'quoteVolume': 6e7},
         'E1/USDT': {'quoteVolume': 2e7}, 'E2/USDT': {'quoteVolume': 2e7}}
    piyasalar = {'KAPALI/USDT': {'spot': True, 'active': False}, 'VADELI/USDT': {'spot': False, 'active': True}}
    e = hz.evren_sec(t, 5e6, 100, piyasalar=piyasalar)
    assert e.semboller == ['BTC/USDT', 'ETH/USDT', 'XRP/USDT', 'JUP/USDT', 'SYRUP/USDT', 'E2/USDT', 'E1/USDT',
                           'EOS/USDT', 'ESIK/USDT']
    assert e.sira == {'ETH/USDT': 1, 'XRP/USDT': 2, 'JUP/USDT': 3, 'SYRUP/USDT': 4, 'E2/USDT': 5, 'E1/USDT': 6,
                      'EOS/USDT': 7, 'ESIK/USDT': 8, 'BTC/USDT': 9}
    assert e.guncellenecek == e.semboller and e.hacim['ETH/USDT'] == 9e8
    # ilk n, sonra sabit coinler çıkar (strateji_lab gibi: evren küçülür); korunanlar güncellenir
    e = hz.evren_sec(t, 5e6, 3, korunan=['AZ/USDT', 'ETH/USDT', 'AZ/USDT'], haric={'XRP/USDT'}, piyasalar=piyasalar)
    assert e.semboller == ['BTC/USDT', 'ETH/USDT', 'JUP/USDT']
    assert e.guncellenecek == ['BTC/USDT', 'ETH/USDT', 'JUP/USDT', 'AZ/USDT']
    assert hz.evren_sec({}, 5e6, 10).semboller == ['BTC/USDT']                      # BTC tickers'ta yoksa da
    assert hz.kaldiracli_mi('1INCHUP', {'1INCH'}) and not hz.kaldiracli_mi('UP', {''})


def test_evren_sirasi_backfill_evren_sec_ile_ayni():
    rng = np.random.default_rng(3)
    t = {f'C{i:02d}/USDT': {'quoteVolume': float(rng.integers(1, 5)) * 1e7} for i in range(30)}   # eşitlikler var
    t['BTC/USDT'] = {'quoteVolume': 2e7}
    t['USDC/USDT'] = {'quoteVolume': 9e9}

    class Ex:
        def fetch_tickers(self):
            return t
    assert hz.evren_sec(t, 0, 12).semboller == ['BTC/USDT'] + sl.bf.evren_sec(Ex(), 12)


# ------------------------------------------------------------------------------------------
# Yeni özellikler
# ------------------------------------------------------------------------------------------
def _yeni(d):
    return mo.yeni_ozellikler(d[:, 1], d[:, 2], d[:, 3], d[:, 4], d[:, 6], d[:, 7], d[:, 8])


def _ayni(a, b):
    return np.array_equal(np.asarray(a, float), np.asarray(b, float), equal_nan=True)


def test_yeni_ozellikler_gelecegi_kullanmaz_ve_idx_ayni():
    d = sentetik(700, 8)
    d[np.random.default_rng(1).random(700) < 0.04, 1:] = np.nan                     # eksik saatler
    d[[100, 101], 6:9] = 0.0
    tam = _yeni(d)
    assert set(tam) == set(mo.YENI_OZELLIKLER)
    for k in mo.YENI_OZELLIKLER:                                                    # her özellik bir yerde hesaplanmış
        assert np.isfinite(tam[k][200:]).any(), k
    for k in mo.DESENLER:
        assert set(np.unique(tam[k][np.isfinite(tam[k])])) <= {0.0, 1.0}
    rng = np.random.default_rng(2)
    for t in (5, 30, 180, 181, 400, 699):
        bozuk = d.copy()
        bozuk[t + 1:, 1:] *= rng.uniform(0.5, 1.5, (len(d) - t - 1, 8))           # gelecek tamamen başka
        bozuk[t + 1:t + 5, 1:] = np.nan
        for x in (_yeni(bozuk), _yeni(d[:t + 1])):
            for k in mo.YENI_OZELLIKLER:
                assert _ayni(x[k][:t + 1], tam[k][:t + 1]), (t, k)
        idx = [t, 0, t // 2]
        tek = mo.yeni_ozellikler(*(d[:, j] for j in (1, 2, 3, 4, 6, 7, 8)), idx=idx)   # yalnız istenen saatler
        for k in mo.YENI_OZELLIKLER:
            assert _ayni(tek[k], tam[k][idx]), (t, k)


def test_hacim_medyani_pandas_rolling_ile_ayni_ve_yuzde_90_doluluk():
    rng = np.random.default_rng(4)
    x = rng.lognormal(5, 1, 900)
    x[rng.random(900) < 0.08] = np.nan
    x[300:318] = np.nan                                                             # bir pencerede %90'ın altı
    beklenen = sl._kaydir(pd.Series(x).rolling(168, min_periods=sl._dolu(168)).median().to_numpy(), 6)
    np.testing.assert_allclose(mo._kayan_medyan(x, np.arange(900)), beklenen, rtol=0, atol=0, equal_nan=True)
    # tam sınır: 168 saatin 152'si dolu -> değer var, 151 -> NULL
    n = 200
    N = np.full(n, 10.0)
    N[-1] = 30.0
    t = n - 1
    N[t - 173:t - 173 + 16] = np.nan
    assert mo.hacim_kalitesi(np.ones(n), N, np.ones(n), idx=[t])['islem_orani'][0] == 3.0
    N[t - 173 + 16] = np.nan
    assert np.isnan(mo.hacim_kalitesi(np.ones(n), N, np.ones(n), idx=[t])['islem_orani'][0])


def _bir_mum(o, h, l, c):
    oz = mo.mum_ozellikleri([o], [h], [l], [c])
    return {k: v[0] for k, v in oz.items()}


def test_mum_ozellikleri_elle():
    oz = mo.mum_ozellikleri([100, 100, 100], [100, 104, 102], [100, 99, 99], [100, 102, 99.5])
    assert oz['ret1'][1] == pytest.approx(0.02) and np.isnan(oz['ret1'][0])
    assert oz['govde'][1] == pytest.approx(2 / 5) and oz['ust_fitil'][1] == pytest.approx(2 / 5)
    assert oz['alt_fitil'][1] == pytest.approx(1 / 5)
    assert oz['govde'][2] == pytest.approx(0.5 / 3) and oz['ust_fitil'][2] == pytest.approx(2 / 3)
    assert oz['alt_fitil'][2] == pytest.approx(0.5 / 3)
    for k in ('govde', 'ust_fitil', 'alt_fitil', 'doji'):                          # H == L: biçim yok
        assert np.isnan(oz[k][0])
    eksik = mo.mum_ozellikleri([100, np.nan, 100], [101] * 3, [99] * 3, [100, np.nan, 100.5])
    assert np.isnan(eksik['ret1'][1:]).all() and np.isnan(eksik['govde'][1])


def _seri(mumlar):
    """[(o, h, l, c), ...] -> O, H, L, C; son mum karar mumu."""
    return tuple(np.asarray(x, dtype=float) for x in zip(*mumlar))


def _desen(mumlar, ad):
    return mo.mum_ozellikleri(*_seri(mumlar))[ad][-1]


DUSUS = [(107 - i, 107.2 - i, 105.8 - i, 106 - i) for i in range(7)]               # 7 saat düşüş: C 106 -> 100
YUKSELIS = [(93 + i, 94.2 + i, 92.8 + i, 94 + i) for i in range(7)]                 # 7 saat yükseliş: C 94 -> 100


def test_desenler_olumlu_ve_olumsuz():
    # doji: |C-O| <= 0.1 (H-L)
    assert _desen([(100, 101.25, 98.75, 100.25)], 'doji') == 1                       # tam sınır: b = 0.1 R
    assert _desen([(100, 101.25, 98.75, 100.26)], 'doji') == 0 and _desen([(100, 101, 99, 99.9)], 'doji') == 1
    # çekiç: alt fitil >= 2 gövde, üst <= 0.5 gövde, önceki 6 saat düşüş
    cekic = (100, 101.2, 97, 101)                                                    # b 1, a 3, u 0.2
    assert _desen(DUSUS + [cekic], 'cekic') == 1
    assert _desen(YUKSELIS + [cekic], 'cekic') == 0                                  # eğilim yanlış
    assert _desen(DUSUS + [(100, 101.6, 97, 101)], 'cekic') == 0                     # üst fitil 0.6 > 0.5 b
    assert _desen(DUSUS + [(100, 101.2, 98.1, 101)], 'cekic') == 0                   # alt fitil 1.9 < 2 b
    assert _desen(DUSUS + [(100, 100, 98, 100)], 'cekic') == 1                       # yusufçuk doji
    assert np.isnan(_desen(DUSUS[-5:] + [cekic], 'cekic'))                           # önceki 6 saat yok
    assert np.isnan(_desen(DUSUS + [(100, 100, 100, 100)], 'cekic'))                 # H == L
    # kayan yıldız: aynası, önceki 6 saat yükseliş
    yildiz = (100, 104, 98.8, 99)                                                    # b 1, u 4, a 0.2
    assert _desen(YUKSELIS + [yildiz], 'kayan_yildiz') == 1
    assert _desen(DUSUS + [yildiz], 'kayan_yildiz') == 0
    assert _desen(YUKSELIS + [(100, 104, 98.4, 99)], 'kayan_yildiz') == 0           # alt fitil 0.6 > 0.5 b
    assert _desen(YUKSELIS + [cekic], 'kayan_yildiz') == 0
    # yutan boğa: kırmızıdan sonra onu yutan yeşil
    assert _desen([(101, 101.5, 99.5, 100), (99.8, 102, 99.5, 101.5)], 'yutan_boga') == 1
    assert _desen([(101, 101.5, 99.5, 100), (100, 102, 99.5, 101)], 'yutan_boga') == 0   # gövde eşit (1 = 1)
    assert _desen([(101, 101.5, 99.5, 100), (100.2, 102, 99.5, 101.5)], 'yutan_boga') == 0   # O > önceki C
    assert _desen([(100, 101.5, 99.5, 101), (99.8, 102, 99.5, 101.5)], 'yutan_boga') == 0   # önceki yeşil
    assert np.isnan(_desen([(np.nan,) * 4, (99.8, 102, 99.5, 101.5)], 'yutan_boga'))
    # yutan ayı: yeşilden sonra onu yutan kırmızı
    assert _desen([(100, 101.5, 99.5, 101), (101.2, 101.5, 99, 99.5)], 'yutan_ayi') == 1
    assert _desen([(100, 101.5, 99.5, 101), (101.2, 101.5, 99, 100.1)], 'yutan_ayi') == 0   # C > önceki O
    assert _desen([(100, 101.5, 99.5, 101), (101.2, 101.5, 99, 99.5)], 'yutan_boga') == 0
    # üç beyaz asker
    asker = [(100, 101.2, 99.9, 101), (100.5, 102.2, 100.4, 102), (101.5, 103.2, 101.4, 103)]
    assert _desen(asker, 'uc_beyaz_asker') == 1
    assert _desen(asker[:2] + [(102.1, 103.2, 101.9, 103)], 'uc_beyaz_asker') == 0   # açılış önceki kapanışın üstü
    assert _desen(asker[:2] + [(100.4, 103.2, 100.3, 103)], 'uc_beyaz_asker') == 0   # açılış önceki açılışın altı
    assert _desen([asker[0], (101.1, 102.2, 101, 102), asker[2]], 'uc_beyaz_asker') == 0   # 2. açılış 1. kapanış üstü
    assert _desen([asker[0], (99.9, 102.2, 99.8, 102), asker[2]], 'uc_beyaz_asker') == 0    # 2. açılış 1. açılış altı
    assert _desen(asker[:2] + [(101.5, 102.2, 101.4, 102)], 'uc_beyaz_asker') == 0   # kapanış artmıyor
    assert _desen([(101, 101.2, 99.9, 100.5)] + asker[1:], 'uc_beyaz_asker') == 0    # ilki kırmızı
    assert np.isnan(_desen(asker[1:], 'uc_beyaz_asker'))


def test_hacim_kalitesi_elle():
    n = 200
    t = n - 1
    QV = np.full(n, 1000.0)
    N = np.full(n, 10.0)
    AQV = np.full(n, 500.0)
    oz = {k: v[0] for k, v in mo.hacim_kalitesi(QV, N, AQV, idx=[t]).items()}
    assert oz == {'islem_orani': 1.0, 'buyuk_lot_orani': 1.0, 'alici_payi': 0.5, 'alici_payi6': 0.5,
                  'hacim_yogunlugu24': 1 / 24}
    QV[t], N[t], AQV[t] = 30000.0, 30.0, 27000.0                                      # 3 kat işlem, lot 10 kat
    oz = {k: v[0] for k, v in mo.hacim_kalitesi(QV, N, AQV, idx=[t]).items()}
    assert oz['islem_orani'] == 3.0 and oz['buyuk_lot_orani'] == 10.0 and oz['alici_payi'] == 0.9
    assert oz['alici_payi6'] == pytest.approx((5 * 500 + 27000) / (5 * 1000 + 30000))
    assert oz['hacim_yogunlugu24'] == pytest.approx(30000 / (23 * 1000 + 30000))
    QV[t - 3:t] = 0.0                                                               # gelecek etkisi yok, t-6'dan önce
    QV[t - 30] = 1e9                                                                # medyanı oynatmaz
    N[t - 10] = 0.0                                                                 # işlemsiz saat: lot tanımsız
    oz2 = {k: v[0] for k, v in mo.hacim_kalitesi(QV, N, AQV, idx=[t]).items()}
    assert oz2['islem_orani'] == 3.0 and oz2['buyuk_lot_orani'] == 10.0
    assert oz2['hacim_yogunlugu24'] == pytest.approx(30000 / (20 * 1000 + 30000))
    sifir = mo.hacim_kalitesi(np.zeros(n), np.zeros(n), np.zeros(n), idx=[t])
    assert all(np.isnan(v[0]) for v in sifir.values())                              # 0/0: NULL
    QV[t - 5] = np.nan
    oz3 = {k: v[0] for k, v in mo.hacim_kalitesi(QV, N, AQV, idx=[t]).items()}
    assert np.isnan(oz3['alici_payi6']) and np.isnan(oz3['hacim_yogunlugu24']) and oz3['alici_payi'] == 0.9
    kisa = mo.hacim_kalitesi(QV[:20], N[:20], AQV[:20])
    assert np.isnan(kisa['islem_orani']).all() and np.isnan(kisa['hacim_yogunlugu24']).all()
    assert mo.hacim_kalitesi([], [], [])['islem_orani'].shape == (0,)


def test_radar_siralari_radar_ilk10_ile_ayni():
    rng = np.random.default_rng(5)
    R = rng.normal(0, 0.05, (300, 25)).round(3)                                     # yuvarlama: eşitlikler
    V = rng.uniform(5e6, 3e7, (300, 25))
    R[rng.random(R.shape) < 0.05] = np.nan
    V[:, 3] = 12e6                                                                  # tam eşik
    V[:, 4] = 11_999_999.5                                                          # float32'de 12e6: radara girer
    ilk10 = sl.radar_ilk10(R.astype(np.float32), V.astype(np.float32))
    for t in range(300):
        sira = mo.radar_siralari(R[t], V[t])
        np.testing.assert_array_equal(np.nan_to_num(sira, nan=99) <= 10, ilk10[t])
        uygun = np.isfinite(sira)
        assert sorted(sira[uygun].tolist()) == list(range(1, uygun.sum() + 1))
    assert mo.radar_siralari([0.1, 0.1, 0.2], [2e7, 2e7, 1e6]).tolist()[:2] == [1.0, 2.0]
    assert np.isfinite(mo.radar_siralari(R[0], V[0])[4]) == np.isfinite(R[0, 4])


# ------------------------------------------------------------------------------------------
# İleri etiketler
# ------------------------------------------------------------------------------------------
def _duz(n=40, c=100.0):
    O, H, L, C = (np.full(n, c) for _ in range(4))
    H = H * 1.001
    L = L * 0.999
    return O, H, L, C


def test_ileri_etiketler_elle():
    O, H, L, C = _duz()
    i = 5
    C[i + 1], C[i + 4], C[i + 24] = 101.0, 97.0, 105.0
    H[i + 10], L[i + 12] = 108.0, 96.0
    e = mo.ileri_etiketler(O, H, L, C, i)
    assert e['ileri_1s'] == pytest.approx(0.01) and e['ileri_4s'] == pytest.approx(-0.03)
    assert e['ileri_24s'] == pytest.approx(0.05) and e['max_yukari_24s'] == pytest.approx(0.08)
    assert e['max_asagi_24s'] == pytest.approx(-0.04) and e['ucus10'] == 0 and e['ucus20'] == 0
    assert e['eksik_saat'] == 0
    H[i + 30] = 200.0                                                               # ufuk dışı: etkisiz
    assert mo.ileri_etiketler(O, H, L, C, i) == e
    # +%10 önce: 1; sonra -%5 gelse de
    O, H, L, C = _duz()
    H[i + 3], L[i + 7] = 110.5, 90.0
    assert mo.ileri_etiketler(O, H, L, C, i)['ucus10'] == 1
    # -%5 önce: 0
    O, H, L, C = _duz()
    H[i + 7], L[i + 3] = 125.0, 94.9
    e = mo.ileri_etiketler(O, H, L, C, i)
    assert e['ucus10'] == 0 and e['ucus20'] == 1                                    # -%7'ye inmedi: +%20 önce
    # aynı mumda ikisi: yeşil mum A->D->Y->K (önce dip) 0; kırmızı A->Y->D->K (önce tepe) 1
    for o, c, beklenen in ((100.0, 101.0, 0), (101.0, 100.0, 1)):
        O, H, L, C = _duz()
        O[i + 2], C[i + 2], H[i + 2], L[i + 2] = o, c, 111.0, 94.0
        assert mo.ileri_etiketler(O, H, L, C, i)['ucus10'] == beklenen, (o, c)
    # açılış boşluğu hedefin üstünde: açılışta dokunur
    O, H, L, C = _duz()
    O[i + 2], H[i + 2], L[i + 2], C[i + 2] = 112.0, 112.5, 90.0, 111.0              # yeşil ama açılış zaten +%10
    assert mo.ileri_etiketler(O, H, L, C, i)['ucus10'] == 1
    # tam eşik dokunuşu sayılır
    O, H, L, C = _duz()
    H[i + 2] = 100.0 * 1.10
    assert mo.ileri_etiketler(O, H, L, C, i)['ucus10'] == 1


def test_ileri_etiketler_eksik_saat_ve_erken():
    O, H, L, C = _duz()
    i = 5
    assert mo.ileri_etiketler(O[:i + 24], H[:i + 24], L[:i + 24], C[:i + 24], i) is None   # t+24 henüz yok
    assert mo.ileri_etiketler(O[:i + 25], H[:i + 25], L[:i + 25], C[:i + 25], i) is not None
    for x in (O, H, L, C):
        x[[i + 1, i + 9]] = np.nan
    e = mo.ileri_etiketler(O, H, L, C, i)
    assert e['eksik_saat'] == 2 and e['ileri_1s'] is None and e['ileri_4s'] == 0.0 and e['ucus10'] == 0
    for x in (O, H, L, C):
        x[i + 24] = np.nan
    e = mo.ileri_etiketler(O, H, L, C, i)
    assert e['eksik_saat'] == 3 and all(e[k] is None for k in mo.ETIKETLER)          # kalıcı NULL
    O, H, L, C = _duz()
    C[i] = np.nan
    assert all(v is None for k, v in mo.ileri_etiketler(O, H, L, C, i).items() if k != 'eksik_saat')


def test_etiketle_veritabaninda_kapsamla(tmp_path):
    d = sentetik(400, 9, sigma=0.03)
    d = np.delete(d, [250, 251, 300, 301, 302], axis=0)                             # 2 ve 3 saatlik boşluk
    simdi = int(T0 + 350 * SAAT) + 20 * SN
    ex = SahteBorsa({'A/USDT': d}, simdi)
    h = _hafiza(tmp_path)
    t = hz.MumToplayici(ex, h)
    t.guncelle(['A/USDT'], simdi)
    aday_ts = [T0 + k * SAAT for k in (200, 230, 240, 280, 300, 325, 326)]
    h.aday_yaz([{'ts': x, 'sembol': 'A/USDT'} for x in aday_ts])
    assert h.etiketle(simdi) == 6                                                   # 326: t+24 (350) henüz açık
    g = h.izgara_aralik('A/USDT', T0, T0 + 350 * SAAT)
    for x in aday_ts[:6]:
        r = h.sorgu('SELECT * FROM etiket WHERE ts = ?', (x,))
        beklenen = mo.ileri_etiketler(g.O, g.H, g.L, g.C, (x - T0) // SAAT)
        assert {k: r[0][k] for k in beklenen} == beklenen and r[0]['olusturma_ms'] == simdi
    assert h.sorgu('SELECT eksik_saat FROM etiket WHERE ts = ?', (T0 + 240 * SAAT,))[0]['eksik_saat'] == 2
    for x in (280, 300):                                                            # 3 eksik saat / C[t] eksik
        r = h.sorgu('SELECT * FROM etiket WHERE ts = ?', (T0 + x * SAAT,))[0]
        assert r['ileri_24s'] is None and r['ucus10'] is None and r['max_yukari_24s'] is None
    assert h.sorgu('SELECT eksik_saat FROM etiket WHERE ts = ?', (T0 + 280 * SAAT,))[0]['eksik_saat'] == 3
    assert h.sorgu('SELECT ileri_1s FROM etiket WHERE ts = ?', (T0 + 325 * SAAT,))[0]['ileri_1s'] is not None
    assert h.etiketle(simdi) == 0                                                    # idempotent
    # kapsam ilerlemeden (borsa gecikmesi) etiket yazılmaz
    simdi2 = int(T0 + 351 * SAAT) + 20 * SN
    assert h.parite_oku('A/USDT')['kapsam_ts'] == T0 + 350 * SAAT and h.etiket_bekleyenler(simdi2) == []
    assert h.etiketle(simdi2) == 0
    ex.simdi_ms = simdi2
    t.guncelle(['A/USDT'], simdi2)
    assert h.etiketle(simdi2) == 1
    h.kapat()


# ------------------------------------------------------------------------------------------
# Emir defteri
# ------------------------------------------------------------------------------------------
def test_defter_ozellikleri_elle():
    defter = {'bids': [[99.9, 10], [99.5, 5], [98.5, 100], [97.0, 1000]],
              'asks': [[100.1, 10], [100.6, 5], [101.5, 100], [103.5, 1000, 3]]}
    o = mo.defter_ozellikleri(defter)
    assert o['spread_bps'] == pytest.approx(20.0)
    assert o['al_derinlik_1'] == pytest.approx(999 + 497.5) and o['sat_derinlik_1'] == pytest.approx(1001 + 503)
    assert o['al_derinlik_2'] == pytest.approx(999 + 497.5 + 9850) and o['sat_derinlik_2'] == pytest.approx(1504 + 10150)
    assert o['dengesizlik_1'] == pytest.approx((1496.5 - 1504) / (1496.5 + 1504))
    assert o['kapsam_bps'] == pytest.approx(300.0) and set(o) == set(mo.DEFTER_ALANLARI)
    assert mo.defter_ozellikleri({'bids': [], 'asks': [[1, 1]]}) is None
    assert mo.defter_ozellikleri({'bids': [[101, 1]], 'asks': [[100, 1]]}) is None   # çapraz
    assert mo.defter_ozellikleri(None) is None
    assert mo.defter_ozellikleri({'bids': [[100, 0]], 'asks': [[100.2, 0]]})['dengesizlik_1'] is None
    ex = SahteBorsa({'A/USDT': sentetik(30, 0)}, T0 + 10 * SAAT)
    o = mo.defter_ozellikleri(ex.fetch_order_book('A/USDT', limit=100))
    assert o['kapsam_bps'] == pytest.approx(1000.0, rel=1e-6) and o['spread_bps'] == pytest.approx(20.0)


# ------------------------------------------------------------------------------------------
# Kayan pencere ve aday satırı
# ------------------------------------------------------------------------------------------
def test_1500_saatlik_pencere_gostergeleri_tam_gecmisle_ayni():
    """Pencere başı yalnız özyinelemeli ortalamaları (EMA/ATR/RSI/ADX) etkiler: EMA200'ün başlangıç etkisi
    (1 - 2/201)^k; k = 1300'de < 3e-6, karar mumunda (k = 1499) < 4e-7 (göreli fark < 1e-5 sınanır). Pencereli
    (rolling) göstergeler son 740 saatle tamamen belirlenir. Sinyal ancak bir değer eşiğe 1e-6'dan yakınsa değişebilir."""
    n, P = 3200, hz.PENCERE
    rng = np.random.default_rng(11)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.012, n) + 0.0004 * np.sin(np.arange(n) / 200)))
    o = np.r_[100, c[:-1]]
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.004, n)))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.004, n)))
    v = 1e6 * np.exp(rng.normal(0, 0.8, n)) / c
    tam = sl.gostergeler(o, h, l, c, v)
    btc_tam = sl.btc_baglami(c, h, l)
    for son in (n - 1, n - 400):
        a = son - P + 1
        p = sl.gostergeler(o[a:son + 1], h[a:son + 1], l[a:son + 1], c[a:son + 1], v[a:son + 1])
        btc = sl.btc_baglami(c[a:son + 1], h[a:son + 1], l[a:son + 1])
        k = np.arange(1300, P)
        assert np.max(np.abs(p['ema200'][k] / tam['ema200'][a + k] - 1)) < 1e-5
        assert np.max(np.abs(btc['ema200_uzaklik'][k] - btc_tam['ema200_uzaklik'][a + k])) < 1e-5
        for ad in ('ema20', 'ema50', 'rsi14', 'rsi2', 'atr_pct', 'adx14'):
            np.testing.assert_allclose(p[ad][k], tam[ad][a + k], rtol=1e-9, err_msg=ad)
        for ad in ('bbw_pct', 'vol_ratio6', 'vol_ratio14', 'surekli6', 'ort_gunluk_hacim30', 'high24_prev', 'vol24'):
            np.testing.assert_allclose(p[ad][k], tam[ad][a + k], rtol=1e-12, err_msg=ad)
        assert (btc['rejim'][k] == btc_tam['rejim'][a + k]).all()
        s_p = sl.sinyaller(p, btc['ok'], None, 5e6, 2e7, btc['rejim'])
        s_t = sl.sinyaller(tam, btc_tam['ok'], None, 5e6, 2e7, btc_tam['rejim'])
        for kural in s_p:
            assert (s_p[kural][k] == s_t[kural][a + k]).all(), kural


def test_aday_ozellikleri_ve_yazimi(tmp_path):
    d = sentetik(hz.PENCERE + 10, 12)
    b = sentetik(hz.PENCERE + 10, 13, s0=60000)
    h = _hafiza(tmp_path)
    h.mum_yaz('A/USDT', d)
    h.mum_yaz('BTC/USDT', b)
    son = int(d[-1, 0])
    izg, bi = h.izgara('A/USDT', son), h.izgara('BTC/USDT', son)
    g = sl.gostergeler(izg.O, izg.H, izg.L, izg.C, izg.V)
    btc = sl.btc_baglami(bi.C, bi.H, bi.L)
    i = len(izg.ts) - 1
    oz = hz.aday_ozellikleri(g, btc, izg, i)
    assert list(oz) == list(hz.LAB_OZELLIKLERI) + list(mo.YENI_OZELLIKLER)
    lab = sl.ozellikler(g, btc, izg.ts, [i])
    assert all(_ayni(oz[k], lab[k][0]) for k in hz.LAB_OZELLIKLERI)
    yeni = _yeni(h.mumlar('A/USDT', 0, 2 ** 62))
    assert all(_ayni(oz[k], yeni[k][-1]) for k in mo.YENI_OZELLIKLER)
    assert np.isfinite([oz[k] for k in hz.LAB_OZELLIKLERI]).all()
    h.aday_yaz([{'ts': son, 'sembol': 'A/USDT', **oz, 'btc_rejim': str(btc['rejim'][i]), 'radar_sira': None,
                 'evren_sira': 3, 'sinyaller': ''}])
    r = h.adaylar(son)[0]
    assert r['ret24'] == oz['ret24'] and r['evren_sira'] == 3 and r['btc_rejim'] in sl.REJIMLER + (sl.REJIM_YOK,)
    h.kapat()


# ------------------------------------------------------------------------------------------
# Döngü yardımcıları ve rapor sorguları
# ------------------------------------------------------------------------------------------
def _kagit(h, strateji, sembol, sinyal_ts, durum, **k):
    return h.kagit_ekle({'strateji': strateji, 'bilesen': k.pop('bilesen', strateji), 'sembol': sembol,
                         'sinyal_ts': sinyal_ts, 'giris_ts': sinyal_ts + SAAT, 'durum': durum,
                         'olusturma_ms': sinyal_ts + SAAT + 20 * SN, **k})


def test_ilk_baslangic_mum_durumu_ve_nan_mum(tmp_path):
    h = _hafiza(tmp_path)
    assert h.ilk_baslangic() is None
    assert h.ilk_baslangic(T0 + 5) == T0 + 5 and h.ilk_baslangic(T0 + 99) == T0 + 5 and h.ilk_baslangic() == T0 + 5
    d = sentetik(10, 1)
    d[3, 4] = 0.0                                                                   # bozuk fiyat
    d[4, 7:9] = np.nan                                                              # eksik ek alan: NULL yazılır
    h.mum_yaz('A/USDT', np.delete(d[:8], 5, axis=0))
    h.parite_yaz('A/USDT', kapsam_ts=int(d[8, 0]))                                  # 8 ve sonrası henüz çekilmedi
    assert [h.mum_durumu('A/USDT', int(d[i, 0])) for i in (2, 3, 5, 7, 8)] == ['VAR', 'YOK', 'YOK', 'VAR',
                                                                                 'BEKLENIYOR']
    assert h.mum_durumu('B/USDT', T0) == 'BEKLENIYOR'
    r = h.sorgu('SELECT islem_adedi, alici_qv FROM mum WHERE ts = ?', (int(d[4, 0]),))[0]
    assert r == {'islem_adedi': None, 'alici_qv': None}
    g = h.izgara('A/USDT', int(d[9, 0]), 10)
    assert np.isnan(g.N[4]) and np.isnan(g.AQV[4]) and np.isfinite(g.C[4])
    h.kapat()


def test_korunacak_pariteler_ve_sabit_ayikla(tmp_path):
    h = _hafiza(tmp_path)
    simdi = T0 + 100 * SAAT + 20 * SN
    _kagit(h, 'BOT_VEKILI', 'ACIK/USDT', T0, 'ACIK')
    _kagit(h, 'BOT_VEKILI', 'BEK/USDT', T0, 'BEKLIYOR')
    _kagit(h, 'BOT_VEKILI', 'KAP/USDT', T0, 'KAPALI')
    _kagit(h, 'BOT_VEKILI', 'ATL/USDT', T0, 'ATLANDI')
    h.aday_yaz([{'ts': simdi - 26 * SAAT, 'sembol': 'YENI/USDT'}, {'ts': simdi - 27 * SAAT, 'sembol': 'ESKI/USDT'},
                {'ts': simdi - SAAT, 'sembol': 'ACIK/USDT'}])
    assert h.korunacak_pariteler(simdi) == ['ACIK/USDT', 'BEK/USDT', 'YENI/USDT']
    # sabit_ayikla: sıra korunur, sabit ve verisiz çıkar, BTC asla sabit değil
    n = 800
    rng = np.random.default_rng(0)
    sabit = sentetik(n, 1)
    sabit[:, 4] = 1 + rng.normal(0, 0.0001, n)
    sabit[:, 1] = np.r_[1.0, sabit[:-1, 4]]
    sabit[:, 2] = np.maximum(sabit[:, 1], sabit[:, 4]) * 1.0001
    sabit[:, 3] = np.minimum(sabit[:, 1], sabit[:, 4]) * 0.9999
    for s, d in (('BTC/USDT', sabit), ('USDX/USDT', sabit), ('A/USDT', sentetik(n, 2)), ('B/USDT', sentetik(n, 3))):
        h.mum_yaz(s, d)
    kalan, sabitler, verisiz = h.sabit_ayikla(['BTC/USDT', 'B/USDT', 'USDX/USDT', 'HIC/USDT', 'A/USDT', 'B/USDT'])
    assert kalan == ['BTC/USDT', 'B/USDT', 'A/USDT'] and sabitler == ['USDX/USDT'] and verisiz == ['HIC/USDT']
    h.kapat()


def test_kacirilanlari_yaz(tmp_path):
    h = _hafiza(tmp_path)
    assert h.kacirilanlari_yaz(T0 + 5 * SAAT) == []                                  # ilk çalıştırma
    h.tarama_yaz({'ts': T0, 'durum': 'TAMAM'})
    assert h.kacirilanlari_yaz(T0 + SAAT) == []                                     # kesinti yok
    h.tarama_yaz({'ts': T0 + SAAT, 'durum': 'TAMAM'})
    assert h.kacirilanlari_yaz(T0 + 5 * SAAT) == [T0 + k * SAAT for k in (2, 3, 4)]
    assert [r['durum'] for r in h.taramalar()] == ['TAMAM', 'TAMAM'] + ['KACIRILDI'] * 3
    h.tarama_yaz({'ts': T0 + 5 * SAAT, 'durum': 'TAMAM'})
    assert h.kacirilanlari_yaz(T0 + 9 * SAAT, alt_ts=T0 + 8 * SAAT) == [T0 + 8 * SAAT]
    assert h.kacirilanlari_yaz(T0 + 9 * SAAT) == []                                 # yalnız son kayıttan sonrası
    assert not h.taramalar(T0 + 6 * SAAT, T0 + 8 * SAAT)                            # alt_ts öncesi yazılmadı
    h.tarama_yaz({'ts': T0 + 9 * SAAT, 'durum': 'TAMAM'})
    assert h.kacirilanlari_yaz(T0 + 9 * SAAT) == [] and h.kacirilanlari_yaz(T0 + 10 * SAAT) == []
    assert h.tarama_ozeti(T0)['KACIRILDI'] == 4
    h.kapat()


def test_kagit_ozeti(tmp_path):
    h = _hafiza(tmp_path)
    gun = T0 + 10 * GUN
    kapali = [('ERKEN_BIRIKIM', 'A/USDT', 0.05, gun - 30 * SAAT), ('ERKEN_BIRIKIM', 'B/USDT', -0.02, gun - 2 * SAAT),
              ('ERKEN_BIRIKIM', 'C/USDT', 0.01, gun), ('ERKEN_BIRIKIM', 'D/USDT', 0.0, gun - SAAT),
              ('REJIM_KOMBO', 'A/USDT', -0.03, gun - 5 * SAAT)]
    for i, (st, s, r, cikis) in enumerate(kapali):
        _kagit(h, st, s, cikis - 10 * SAAT - i * SAAT, 'KAPALI', giris_fiyat=1.0, cikis_ts=cikis, getiri=r,
               cikis_tipi='STOP', cikis_fiyat=1.0 + r)
    _kagit(h, 'ERKEN_BIRIKIM', 'E/USDT', gun - 3 * SAAT, 'ACIK', giris_fiyat=2.0)
    _kagit(h, 'ERKEN_BIRIKIM', 'F/USDT', gun - SAAT, 'BEKLIYOR')
    _kagit(h, 'YATAY_DONUS', 'G/USDT', gun - 4 * SAAT, 'ATLANDI')
    tum = h.kagit_ozeti()
    e = tum['ERKEN_BIRIKIM']
    assert (e['kapali'], e['kazanan'], e['acik'], e['bekliyor'], e['acilan']) == (4, 2, 1, 1, 5)
    assert e['kazanma_orani'] == 0.5 and e['beklenti'] == pytest.approx(0.01)
    assert e['toplam_usdt'] == pytest.approx(0.04 * 20)
    assert tum['REJIM_KOMBO']['toplam_usdt'] == pytest.approx(-0.6) and tum['REJIM_KOMBO']['kazanma_orani'] == 0
    assert tum['YATAY_DONUS'] == {'kapali': 0, 'kazanan': 0, 'kazanma_orani': None, 'beklenti': None,
                                  'toplam_usdt': 0.0, 'acilan': 0, 'atlandi': 1, 'acik': 0, 'bekliyor': 0}
    son24 = h.kagit_ozeti(islem_usdt=10, bas_ts=gun - 24 * SAAT, bit_ts=gun)       # gun dahil değil
    e = son24['ERKEN_BIRIKIM']
    assert (e['kapali'], e['kazanan']) == (2, 0) and e['toplam_usdt'] == pytest.approx(-0.2)
    assert e['acik'] == 1 and e['bekliyor'] == 1                                    # anlık durum aralıktan bağımsız
    assert e['acilan'] == 4                                                         # girişi aralıkta: B, C, D, E
    assert set(son24) == set(tum)
    h.kapat()


def test_dedektor_karnesi_ve_sinyal_sayilari(tmp_path):
    h = _hafiza(tmp_path)
    satir, etiket = [], []
    for i in range(20):                                                             # 20 satır, 4'ü uçtu: taban %20
        sinyal = {0: 'ERKEN_BIRIKIM,YUKSEK_ISABET', 1: 'ERKEN_BIRIKIM', 2: 'BOT_VEKILI', 5: 'ERKEN_BIRIKIM'}.get(i, '')
        satir.append({'ts': T0 + i * SAAT, 'sembol': 'A/USDT', 'sinyaller': sinyal})
        etiket.append({'ts': T0 + i * SAAT, 'sembol': 'A/USDT', 'ucus10': int(i in (0, 1, 7, 9)),
                       'max_yukari_24s': 0.01 * i})
    satir.append({'ts': T0 + 30 * SAAT, 'sembol': 'A/USDT', 'sinyaller': 'BOT_VEKILI'})   # etiketi yok
    satir.append({'ts': T0 + 31 * SAAT, 'sembol': 'A/USDT', 'sinyaller': 'BOT_VEKILI'})   # kalıcı NULL etiket
    etiket.append({'ts': T0 + 31 * SAAT, 'sembol': 'A/USDT', 'ucus10': None, 'eksik_saat': 5})
    h.aday_yaz(satir)
    h.etiket_yaz(etiket)
    k = h.dedektor_karnesi(T0)
    assert k['taban']['n'] == 20 and k['taban']['ucus10_orani'] == pytest.approx(0.2)
    assert k['taban']['max_yukari_ort'] == pytest.approx(0.095)
    assert list(k['strateji']) == ['BOT_VEKILI', 'ERKEN_BIRIKIM', 'YUKSEK_ISABET']
    e = k['strateji']['ERKEN_BIRIKIM']
    assert e['n'] == 3 and e['ucus10_orani'] == pytest.approx(2 / 3) and e['kat'] == pytest.approx(10 / 3)
    assert e['max_yukari_ort'] == pytest.approx(0.02)
    assert k['strateji']['BOT_VEKILI'] == {'n': 1, 'ucus10_orani': 0.0, 'max_yukari_ort': pytest.approx(0.02),
                                           'kat': 0.0}
    assert h.dedektor_karnesi(T0 + SAAT, T0 + 3 * SAAT)['strateji'] == {
        'BOT_VEKILI': {'n': 1, 'ucus10_orani': 0.0, 'max_yukari_ort': pytest.approx(0.02), 'kat': 0.0},
        'ERKEN_BIRIKIM': {'n': 1, 'ucus10_orani': 1.0, 'max_yukari_ort': pytest.approx(0.01), 'kat': 2.0}}
    bos = h.dedektor_karnesi(T0 + 100 * SAAT)
    assert bos == {'taban': {'n': 0, 'ucus10_orani': None, 'max_yukari_ort': None}, 'strateji': {}}
    assert h.sinyal_sayilari(T0) == {'BOT_VEKILI': 3, 'ERKEN_BIRIKIM': 3, 'YUKSEK_ISABET': 1}
    assert h.sinyal_sayilari(T0 + SAAT, T0 + 31 * SAAT) == {'BOT_VEKILI': 2, 'ERKEN_BIRIKIM': 2}
    h.kapat()


def test_tarama_ozeti(tmp_path):
    h = _hafiza(tmp_path)
    assert h.tarama_ozeti(T0) == {'TAMAM': 0, 'KACIRILDI': 0, 'HATA': 0, 'mum_hatasi': 0, 'sinyal': 0, 'acilan': 0,
                                  'kapanan': 0, 'etiketlenen': 0, 'defter': 0, 'son_ts': None, 'evren': None}
    h.tarama_yaz({'ts': T0, 'durum': 'TAMAM', 'evren': 150, 'mum_hatasi': 2, 'sinyal': 3, 'acilan': 1, 'defter': 5})
    h.tarama_yaz({'ts': T0 + SAAT, 'durum': 'TAMAM', 'evren': 149, 'mum_hatasi': 0, 'sinyal': 1, 'kapanan': 1,
                  'etiketlenen': 150})
    h.tarama_yaz({'ts': T0 + 2 * SAAT, 'durum': 'HATA', 'ayrinti': {'hata': 'x'}})
    h.tarama_yaz({'ts': T0 + 3 * SAAT, 'durum': 'KACIRILDI'})
    o = h.tarama_ozeti(T0)
    assert (o['TAMAM'], o['HATA'], o['KACIRILDI'], o['evren'], o['son_ts']) == (2, 1, 1, 149, T0 + 3 * SAAT)
    assert (o['mum_hatasi'], o['sinyal'], o['acilan'], o['kapanan'], o['etiketlenen'], o['defter']) == (2, 4, 1, 1,
                                                                                                         150, 5)
    assert h.tarama_ozeti(T0, T0 + SAAT)['evren'] == 150 and h.tarama_ozeti(T0, T0 + SAAT)['TAMAM'] == 1
    h.kapat()


def test_ilginc_sec():
    satirlar = [{'sembol': 'SINYAL/USDT', 'sinyaller': 'TREND_DIP_RSI2', 'vol_ratio14': 0.5, 'ret1': -0.001},
                {'sembol': 'HACIM3/USDT', 'sinyaller': '', 'vol_ratio14': 3.0, 'ret1': 0.0},
                {'sembol': 'HACIM5/USDT', 'sinyaller': '', 'vol_ratio14': np.float64(5.0), 'ret1': None},
                {'sembol': 'SINIR/USDT', 'sinyaller': '', 'vol_ratio14': 2.0, 'ret1': 0.02},     # eşitlik ilginç değil
                {'sembol': 'DUSUS/USDT', 'sinyaller': '', 'vol_ratio14': None, 'ret1': -0.021},
                {'sembol': 'YUKSELIS/USDT', 'sinyaller': '', 'vol_ratio14': 1.5, 'ret1': 0.03},
                {'sembol': 'NAN/USDT', 'sinyaller': '', 'vol_ratio14': np.nan, 'ret1': np.nan},
                {'sembol': 'SIRADAN/USDT', 'sinyaller': '', 'vol_ratio14': 1.0, 'ret1': 0.0}]
    assert hz.ilginc_sec(satirlar) == ['SINYAL/USDT', 'HACIM5/USDT', 'HACIM3/USDT', 'YUKSELIS/USDT', 'DUSUS/USDT']
    assert hz.ilginc_sec(satirlar, azami=2) == ['SINYAL/USDT', 'HACIM5/USDT']
    assert hz.ilginc_sec(satirlar, azami=0) == [] and hz.ilginc_sec([]) == []


def test_defter_topla_yeniden_dener_ve_turu_oldurmez(tmp_path):
    veri = {s: sentetik(50, i) for i, s in enumerate(['A/USDT', 'B/USDT', 'C/USDT', 'D/USDT'])}
    simdi = T0 + 40 * SAAT + 20 * SN
    ex = SahteBorsa(veri, simdi)
    cagri = []
    asil = ex.fetch_order_book

    def defter(sembol, limit=100):
        cagri.append((sembol, limit))
        if sembol == 'B/USDT' and sum(c[0] == 'B/USDT' for c in cagri) == 1:
            raise ccxt.RequestTimeout('zaman aşımı')                                 # bir kez: yeniden denenir
        if sembol == 'C/USDT':
            raise ccxt.ExchangeError('beklenmeyen')                                  # yeniden denenmez
        if sembol == 'D/USDT':
            return {'bids': [], 'asks': [[1.0, 1.0]]}
        return asil(sembol, limit)
    ex.fetch_order_book = defter
    h = _hafiza(tmp_path)
    bekleme, uyku = _bekle_kaydi()
    s = hz.MumToplayici(ex, h, uyku=uyku).defter_topla(['A/USDT', 'B/USDT', 'C/USDT', 'D/USDT', 'A/USDT'],
                                                       T0 + 39 * SAAT, simdi)
    assert s['yazilan'] == 2 and list(s['hata']) == ['C/USDT'] and s['bos'] == ['D/USDT'] and s['istek'] == 5
    assert bekleme == [2] and all(lim == hz.DEFTER_LIMIT for _, lim in cagri)
    r = h.sorgu('SELECT * FROM defter ORDER BY sembol')
    assert [x['sembol'] for x in r] == ['A/USDT', 'B/USDT'] and all(x['ts'] == T0 + 39 * SAAT for x in r)
    assert r[0]['alinma_ms'] == simdi and r[0]['spread_bps'] == pytest.approx(20.0)
    beklenen = mo.defter_ozellikleri(asil('B/USDT', 100))
    assert {k: r[1][k] for k in mo.DEFTER_ALANLARI} == beklenen
    # ağ çöktü: üst üste hatada kalanlar denenmez
    ex.fetch_order_book = lambda sembol, limit=100: (_ for _ in ()).throw(ccxt.NetworkError('ağ'))
    s = hz.MumToplayici(ex, h, uyku=lambda x: None, ardisik_hata_siniri=2).defter_topla(
        ['A/USDT', 'B/USDT', 'C/USDT', 'D/USDT'], T0 + 40 * SAAT, simdi)
    assert list(s['hata']) == ['A/USDT', 'B/USDT'] and s['atlanan'] == ['C/USDT', 'D/USDT'] and s['yazilan'] == 0
    h.kapat()


def test_uzun_kesintiden_sonra_eski_satirlar_da_etiketlenir(tmp_path):
    """Makine 20 gün kapalı kaldı (14 günden uzun): geri doldurulan mumlarla kesinti öncesi son satırlar etiketlenir;
    PENCERE'den eski kalanlara (mumu hiç gelmeyecek) bakılmaz."""
    d = sentetik(2000, 14)
    simdi = int(d[1000, 0]) + 20 * SN
    ex = SahteBorsa({'A/USDT': d}, simdi)
    h = _hafiza(tmp_path)
    t = hz.MumToplayici(ex, h)
    t.guncelle(['A/USDT'], simdi)
    h.aday_yaz([{'ts': int(d[k, 0]), 'sembol': 'A/USDT'} for k in range(990, 1000)])
    assert h.etiketle(simdi) == 0
    simdi = int(d[1000 + 20 * 24, 0]) + 20 * SN
    ex.simdi_ms = simdi
    t.guncelle(['A/USDT'], simdi)
    assert h.etiketle(simdi) == 10 and h.sorgu('SELECT COUNT(*) AS n FROM etiket WHERE ileri_24s IS NOT NULL')[0]['n'] == 10
    assert hz.ETIKET_GERI_GUN * 24 >= hz.PENCERE + 24
    h.kapat()


# ------------------------------------------------------------------------------------------
# makine.py: kâğıt işlemler strateji_lab ile birebir (sentetik piyasa, simüle saat)
# ------------------------------------------------------------------------------------------
P_N, P_S = 1150, 900                    # veri uzunluğu (saat) ve makinenin ilk başlangıcı (P_S. saatin 20. saniyesi)
P_YENIDEN = 1080                        # bu turdan sonra veritabanının kopyası alınır (yeniden başlatma testi)
P_KESINTI, P_KESINTI_SAAT = 1110, 6     # kesinti: bu karar mumundan başlayarak 6 karar saati makine kapalı
KAGIT_ALANLARI = ('strateji', 'bilesen', 'sembol', 'sinyal_ts', 'giris_ts', 'giris_fiyat', 'cikis_ts', 'cikis_fiyat',
                  'cikis_tipi', 'getiri', 'sure_saat', 'rejim')


def _ham(ts, o, h, l, c, v, rng):
    """O/H/L/C/V (V baz hacim) -> ham klines sütunları (n, 9); USDT hacmi, işlem adedi, alıcı payı türetilir."""
    qv = v * (o + c) / 2
    adet = np.maximum(1, np.round(qv / 400 * rng.uniform(0.7, 1.3, len(c))))
    return np.column_stack([ts, o, h, l, c, v, qv, adet, qv * rng.uniform(0.35, 0.65, len(c))])


def _btc_rejimli(n, tohum, trend=40, yatay=80):
    """Rejim değiştiren BTC: `trend` saat düzenli yükseliş (ADX yüksek: TREND_YUKARI), `yatay` saat sabit düzey
    çevresinde bağımsız gürültü (ADX düşer: YATAY); kapanış hep EMA200 üstünde (btc_ok)."""
    rng = np.random.default_rng(tohum)
    k = np.arange(n)
    duzey = 20000 * np.exp(np.cumsum(np.where(k % (trend + yatay) < trend, 0.003, 0.0)))
    c = duzey * (1 + rng.normal(0, 0.002, n))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.001, n)))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.001, n)))
    return o, h, l, c, 5e7 / c * np.exp(rng.normal(0, 0.2, n))


def _birikim(n, tohum, faz, yukselis, donem=120, hacim=1e6):
    """Her `donem` saatte ERKEN_BIRIKIM / SIKISMA_KIRILIM kurulumu: hafif yükseliş, 24 saat dar yatay (son 5 saati 3x
    hacim), 24 saatlik zirvenin %0.3 üstünde 3x hacimli kırılım; ardından yukselis ise 10 saatte +%8 ve geri çekilme,
    değilse rastgele."""
    rng = np.random.default_rng(tohum)
    c, vk = [100.0], [1.0]
    for _ in range(faz):
        c.append(c[-1] * (1 + rng.normal(0, 0.002)))
        vk.append(1.0)
    while len(c) < n:
        for _ in range(donem - 65):
            c.append(c[-1] * (1 + 0.001 + rng.normal(0, 0.001)))
            vk.append(1.0)
        for i in range(24):
            c.append(c[-1] * (1 + (0.0015 if i % 2 else -0.0015)))
            vk.append(3.0 if i >= 19 else 1.0)
        tepe = max(max(a, b) for a, b in zip(c[-25:-1], c[-24:])) * 1.001
        c.append(tepe * 1.003)
        vk.append(3.0)
        adim = [0.0077] * 10 + list(-0.004 + rng.normal(0, 0.003, 30)) if yukselis else list(rng.normal(0, 0.006, 40))
        for x in adim:
            c.append(c[-1] * (1 + x))
            vk.append(1.0)
    c, vk = np.array(c[:n]), np.array(vk[:n])
    o = np.r_[c[0], c[:-1]]
    return o, np.maximum(o, c) * 1.001, np.minimum(o, c) * 0.999, c, hacim * vk * np.exp(rng.normal(0, 0.05, n)) / c


def _donus(n, tohum, faz, aralik=50, hacim=1e6):
    """YATAY_DONUS kurulumu: OU gürültüsü (coin ADX'i düşük), her `aralik` saatte tek mumluk -%3, 4 saatte geri dönüş."""
    rng = np.random.default_rng(tohum)
    x, duzey, c, plan = 0.0, 100.0, [], {}
    while len(c) < n:
        j = len(c)
        if j >= faz + aralik and (j - faz) % aralik == 0:
            onceki, duzey = duzey, duzey * 0.97
            plan.update({j + k: duzey + (onceki - duzey) * k / 4 for k in range(1, 5)})
        duzey = plan.pop(j, duzey)
        x = 0.8 * x + rng.normal(0, 0.001)
        c.append(duzey * np.exp(x))
    c = np.array(c[:n])
    o = np.r_[c[0], c[:-1]]
    return o, np.maximum(o, c) * 1.001, np.minimum(o, c) * 0.999, c, hacim * np.exp(rng.normal(0, 0.05, n)) / c


def _rastgele(n, tohum, s0=10.0, sigma=0.012, egim=0.0004, hacim=1.5e6):
    """Yukarı eğilimli rastgele yürüyüş, oynak hacim (TREND_DIP_RSI2 ve BOT_VEKILI sinyalleri)."""
    rng = np.random.default_rng(tohum)
    c = s0 * np.exp(np.cumsum(rng.normal(egim, sigma, n)))
    o = np.r_[s0, c[:-1]]
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, sigma / 3, n)))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, sigma / 3, n)))
    return o, h, l, c, hacim * np.exp(rng.normal(0, 0.7, n)) / c


def _kagit_piyasasi():
    """BTC + 20 parite: 6 kurulumlu (yarısında kırılım sonrası yükseliş, yarısında rastgele), 4 dipten dönen, 8
    rastgele, 250. saatte listelenen bir parite ve bir sabit coin. R1'de 3 saatlik boşluk (1000-1002. saatler).
    Radar (12M+ hacimli en çok yükselen 10) her saatte 19 uygun parite arasından seçer."""
    ts = T0 + SAAT * np.arange(P_N, dtype=np.int64)
    rng = np.random.default_rng(99)
    veri = {'BTC/USDT': _ham(ts, *_btc_rejimli(P_N, 0), rng)}
    for i in range(6):
        veri[f'E{i}/USDT'] = _ham(ts, *_birikim(P_N, 10 + i, 19 * i, i % 2 == 0), rng)
    for i in range(4):
        veri[f'D{i}/USDT'] = _ham(ts, *_donus(P_N, 20 + i, 13 * i), rng)
    for i in range(8):
        veri[f'R{i}/USDT'] = _ham(ts, *_rastgele(P_N, 30 + i), rng)
    veri['L0/USDT'] = _ham(ts, *_rastgele(P_N, 40, s0=3.0), rng)[250:]
    c = 1 + rng.normal(0, 0.0001, P_N)
    o = np.r_[1.0, c[:-1]]
    veri['USDX/USDT'] = _ham(ts, o, np.maximum(o, c) * 1.0001, np.minimum(o, c) * 0.9999, c, np.full(P_N, 3e6), rng)
    veri['R1/USDT'] = np.delete(veri['R1/USDT'], [1000, 1001, 1002], axis=0)
    return veri


def _lab_kagit(veri, n, ilk_e, kacan=(), evren_maske=None):
    """strateji_lab iç işlevleriyle (calistir'in geçiş 1-2'si: sabit coin, float32 kesit ve radar, BTC bağlamı,
    göstergeler, sinyaller, kombo bileşenleri, kural_islemleri / kombo_islemleri) kâğıt stratejilerinin işlemleri,
    veri sonu kesmesi OLMADAN (veri bitince açık kalan işlem 'acik' sayılır: makinede ACIK). Izgara T0'dan.
    kacan: sinyalleri silinen karar mumları (makine kapalıydı). evren_maske: {sembol: bool[n]} saat saat makinenin
    evreni (dışındaki saatte radar girdisi NaN ve sinyal yok; açık işlemin çıkışı yine tam veriyle). Dönen: kapanan
    işlemler (KAGIT_ALANLARI sırasıyla, sıralı), {(strateji, sembol): açık kalan}, {(sembol, t ms): sinyal adları},
    ayrıntı (ızgaralar, göstergeler, radar, btc, semboller)."""
    ts = T0 + SAAT * np.arange(n, dtype=np.int64)
    izg, semboller = {}, []
    for s in veri:                                           # tickers sırası = veri sırası (BTC önce)
        x = sl.izgaraya_yerlestir(veri[s][:, :6], T0, n)
        oyn = sl.saatlik_oynaklik(sl.ilk_gecmis(x[3]))
        if np.isfinite(oyn) and (s == 'BTC/USDT' or oyn >= sl.SABIT_COIN_STD):
            izg[s] = x
            semboller.append(s)
    kes = [sl.kesit_sutunu(izg[s][3], izg[s][4]) for s in semboller]
    R24 = np.column_stack([k[0] for k in kes]).astype(np.float32)
    if evren_maske is not None:
        for j, s in enumerate(semboller):
            R24[~evren_maske[s], j] = np.nan
    radar = sl.radar_ilk10(R24, np.column_stack([k[1] for k in kes]).astype(np.float32))
    _, bh, bl, bc, _ = izg['BTC/USDT']
    btc = sl.btc_baglami(bc, bh, bl)
    kacan = np.asarray(sorted(kacan), dtype=int)
    kapali, acik, sinyal, gler = [], {}, {}, {}
    for j, s in enumerate(semboller):
        O, H, L, C, V = izg[s]
        g = gler[s] = sl.gostergeler(O, H, L, C, V)
        sg = sl.sinyaller(g, btc['ok'], radar[:, j], mk.SINYAL_MIN_HACIM, mk.BUYUK_HACIM, btc['rejim'])
        for k in sg:
            sg[k] = sg[k].copy()
            sg[k][kacan] = False
            if evren_maske is not None:
                sg[k] &= evren_maske[s]
        seri = {tip: g[ad] for tip, ad in sl.SINYAL_SERISI.items()}
        for strateji in mk.KAGIT_STRATEJILER:
            if strateji in sl.KOMBO_TREND:
                bil = sl.kombo_bilesenleri(strateji, sg, btc['rejim'])
                satirlar, acik[(strateji, s)] = sl.kombo_islemleri(bil, O, H, L, C, seri, ilk_e=ilk_e)
                isaret = np.logical_or.reduce([x for x, _ in bil.values()])
            else:
                tip = sl.KURAL_CIKIS[strateji]
                satirlar, acik[(strateji, s)] = sl.kural_islemleri(sg[strateji], tip, O, H, L, C, seri.get(tip),
                                                                   ilk_e=ilk_e)
                satirlar = [x + (strateji,) for x in satirlar]
                isaret = sg[strateji]
            for t, e, b, giris, cikis, ctip, _, r1, _, bilesen in satirlar:
                kapali.append((strateji, bilesen, s, int(ts[t]), int(ts[e]), float(giris), int(ts[b]) + SAAT,
                               float(cikis), ctip, float(r1), float(b - e + 1), str(btc['rejim'][t])))
            for t in np.flatnonzero(isaret):
                sinyal.setdefault((s, int(ts[t])), []).append(strateji)
    return (sorted(kapali), acik, {k: ','.join(v) for k, v in sinyal.items()},
            {'izg': izg, 'g': gler, 'radar': radar, 'btc': btc, 'semboller': semboller, 'ts': ts})


def _kagit_tuple(r):
    return tuple(r[k] for k in KAGIT_ALANLARI)


def _makine_kur(veri, tickers, yol, simdi, **k):
    saat = [int(simdi)]
    ex = SahteBorsa(veri, saat[0], tickers=tickers)
    h = hz.Hafiza(yol, saat=lambda: saat[0])
    m = mk.Makine(ex, h, saat=lambda: saat[0], uyku=k.pop('uyku', lambda x: None), **k)

    def ilerle(simdi_ms):
        saat[0] = ex.simdi_ms = int(simdi_ms)
    return ex, h, m, ilerle


@pytest.fixture(scope='module')
def kagit_kosusu(tmp_path_factory):
    """Makine P_S. saatte (20. sn) ilk kez başlar ve veri sonuna kadar saat saat çalışır (simüle saat). Bir sinyalin
    giriş mumu borsada yok (makine ATLANDI, lab atlar). P_YENIDEN turundan sonra veritabanının kopyası alınır.
    Aynı mumlarda strateji_lab.calistir de çalıştırılır (--baslangic = ilk başlangıcın saat başı)."""
    dizin = tmp_path_factory.mktemp('kagit')
    veri = _kagit_piyasasi()
    kapali, _, _, _ = _lab_kagit(veri, P_N, P_S)
    t_sil = next(x[3] for x in kapali if x[0] == 'TREND_DIP_RSI2' and x[2] == 'R3/USDT'
                 and T0 + (P_S + 20) * SAAT <= x[3] < T0 + (P_YENIDEN - 20) * SAAT)
    veri['R3/USDT'] = veri['R3/USDT'][veri['R3/USDT'][:, 0] != t_sil + SAAT]       # giriş mumu yok
    tickers = {s: {'quoteVolume': 1e9 - 1e6 * i} for i, s in enumerate(veri)}
    # evren hacim sınırı (--min-hacim) kuralların 5M'lik ön kayıtlı sınırından farklı: kurallar ondan etkilenmemeli
    ex, h, m, ilerle = _makine_kur(veri, tickers, str(dizin / 'm.db'), T0 + P_S * SAAT + 20 * SN, evren=50,
                                   min_hacim=3e7)
    kopya = str(dizin / 'kopya.db')
    sure = time.perf_counter()
    turlar = []
    for k in range(P_S, P_N + 1):
        ilerle(T0 + k * SAAT + 20 * SN)
        turlar.append(m.tur_guvenli())
        if k == P_YENIDEN:
            hedef = sqlite3.connect(kopya)
            h.c.backup(hedef)
            hedef.close()
    sure = time.perf_counter() - sure
    son_t = T0 + (P_N - 1) * SAAT
    pencere_ayni = all(all(np.array_equal(getattr(m.pencereler.izgara(s, son_t), a),
                                          getattr(h.izgara(s, son_t), a), equal_nan=True)
                           for a in ('ts', 'O', 'H', 'L', 'C', 'V', 'QV', 'N', 'AQV')) for s in veri)
    lab_ex = SahteBorsa(veri, T0 + P_N * SAAT + 20 * SN, tickers=tickers)
    a = sl.arguman_ayristirici().parse_args(
        ['--baslangic', sl.vs._tarih(T0 + P_S * SAAT), '--bitis', sl.vs._tarih(T0 + P_N * SAAT),
         '--ayrim', sl.vs._tarih(T0 + (P_S + 100) * SAAT), '--evren', '50', '--ai-yok',
         '--onbellek', str(dizin / 'onb'), '--cikti', str(dizin / 'lab'),
         '--kurallar', *mk.KAGIT_STRATEJILER])
    lab = sl.calistir(lab_ex, a, simdi_ms=T0 + P_N * SAAT + 20 * SN, log=lambda *x: None)
    yield {'veri': veri, 'tickers': tickers, 'h': h, 'm': m, 'ex': ex, 'lab_ex': lab_ex, 'turlar': turlar,
           'kopya': kopya,
           't_sil': t_sil, 'sure': sure, 'lab': lab, 'pencere_ayni': pencere_ayni}
    h.kapat()


def _sigar(x, n=P_N):
    """İşlemin azami tutma süresi veride kalıyor mu (calistir'in veri sonu kuralı; kombolarda en uzun: KADEMELI)."""
    tip = 'KADEMELI' if x[0] in sl.KOMBO_TREND else sl.KURAL_CIKIS[x[0]]
    return (x[4] - T0) // SAAT + sl.AZAMI_TUTMA[tip] <= n


def test_kagit_islemler_strateji_lab_ile_birebir(kagit_kosusu):
    """PARİTE: makinenin saat saat açtığı kâğıt işlemler, aynı mumlarda strateji_lab'ın işlemleriyle birebir aynı
    (strateji, bileşen, parite, sinyal/giriş/çıkış zamanı, fiyatlar, çıkış tipi, k=1 net getiri, süre, rejim; float
    eşitliği). (1) iç işlevler veri sonu kesmesi olmadan: bütün kapanan işlemler ve açık kalanlar; (2) calistir'in
    kendisi: azami tutma süresi veride kalan girişler. Ayrıca aday satırları (sinyaller, radar, rejim, lab ve yeni
    özellikler) ve makinenin yazdığı ileri etiketler tam veriyle hesaplananın aynısı."""
    k = kagit_kosusu
    h, veri = k['h'], k['veri']
    print(f"\nkâğıt parite koşusu: {len(k['turlar'])} tur, {len(veri)} parite, {k['sure']:.1f} sn "
          f"({k['sure'] / len(k['turlar']) * 1000:.0f} ms/tur)")
    assert all(r['durum'] == 'TAMAM' for r in k['turlar'])
    assert not k['ex'].yasak_cagri and not k['lab_ex'].yasak_cagri      # emir/bakiye/özel uç hiç denenmedi
    assert [r['ts'] for r in h.taramalar()] == [T0 + (j - 1) * SAAT for j in range(P_S, P_N + 1)]
    assert h.ilk_baslangic() == T0 + P_S * SAAT + 20 * SN
    assert k['pencere_ayni']                                     # bellekteki pencere = veritabanı ızgarası
    isl = h.kagit_islemler()
    assert min(r['sinyal_ts'] for r in isl) >= T0 + (P_S - 1) * SAAT    # ilk başlangıçtan önce işlem yok
    kapali, acik, sinyal, ay = _lab_kagit(veri, P_N, P_S)
    makine_kapali = sorted(_kagit_tuple(r) for r in isl if r['durum'] == 'KAPALI')
    assert makine_kapali == kapali and len(kapali) > 100
    say = {}
    for r in isl:
        if r['durum'] == 'ACIK':
            say[(r['strateji'], r['sembol'])] = say.get((r['strateji'], r['sembol']), 0) + 1
    assert say == {x: v for x, v in acik.items() if v}
    for r in isl:                                                # açık işlemin girişi: giriş mumu açılışı + kayma
        if r['durum'] == 'ACIK':
            d = veri[r['sembol']]
            assert r['giris_fiyat'] == d[d[:, 0] == r['giris_ts']][0, 1] * (1 + sl.SLIP_GIRIS)
    assert all(r['sinyal_ts'] == T0 + (P_N - 1) * SAAT for r in isl if r['durum'] == 'BEKLIYOR')
    atlandi = [r for r in isl if r['durum'] == 'ATLANDI']       # giriş mumu yok: lab da atladı
    assert atlandi and all((r['sembol'], r['sinyal_ts']) == ('R3/USDT', k['t_sil']) for r in atlandi)
    assert not any(x[2] == 'R3/USDT' and x[3] == k['t_sil'] for x in kapali)
    # calistir'in kendisi (veri sonu kuralı: azami tutma süresi veride kalan girişler)
    df = k['lab']['islemler']
    lab = sorted((r.kural, r.bilesen, r.sembol, int(r.giris_ts) - SAAT, int(r.giris_ts), float(r.giris_fiyat),
                  int(r.cikis_ts), float(r.cikis_fiyat), r.cikis_tipi, float(r.getiri_k1), float(r.sure_saat),
                  r.rejim) for r in df.itertuples())
    tek = [x for x in lab if x[0] not in sl.KOMBO_TREND]
    assert tek == [x for x in makine_kapali if x[0] not in sl.KOMBO_TREND and _sigar(x)]
    assert [x for x in lab if _sigar(x)] == [x for x in makine_kapali if _sigar(x)]
    assert set(lab) <= set(makine_kapali)
    sayim = {s: sum(x[0] == s for x in lab) for s in mk.KAGIT_STRATEJILER}
    print(f"calistir işlemleri: {sayim}")
    assert all(v >= 2 for v in sayim.values()), sayim           # 8 stratejinin hepsi işlem açtı
    assert {x[1] for x in lab if x[0] == 'REJIM_KOMBO'} == {'ERKEN_BIRIKIM', 'YATAY_DONUS'}
    assert {x[1] for x in makine_kapali if x[0] == 'REJIM_KOMBO_BOT'} == {'BOT_VEKILI', 'YATAY_DONUS'}
    assert {x[8] for x in makine_kapali} >= {'STOP', 'IZ_STOP', 'HEDEF', 'SINYAL', 'ZAMAN'}
    assert 'USDX/USDT' in k['lab']['meta']['sabit_coinler']
    # aday satırları: sinyaller, radar, rejim, evren sırası, özellikler (tam veriyle aynı)
    adaylar = h.sorgu('SELECT * FROM aday ORDER BY ts, sembol')
    assert {r['sembol'] for r in adaylar} == set(ay['semboller']) and 'USDX/USDT' not in ay['semboller']
    # her saat her evren paritesi satır alır; karar mumu olmayan saatler (R1'in boşluğu, R3'ün silinen mumu)
    # karar_mumu = 0 ile
    assert len(adaylar) == (P_N - P_S + 1) * len(ay['semboller'])
    assert [r['karar_mumu'] for r in adaylar] == [int(np.isfinite(ay['izg'][r['sembol']][3][(r['ts'] - T0) // SAAT]))
                                                  for r in adaylar]
    assert sum(r['karar_mumu'] == 0 for r in adaylar) == 4
    assert all(r['tanim_id'] == k['m'].tanim_id is not None for r in adaylar)
    assert all(r['olusturma_ms'] == r['ts'] + SAAT + 20 * SN for r in adaylar)
    sira = {s: i + 1 for i, s in enumerate(veri)}
    for r in adaylar:
        i, j = (r['ts'] - T0) // SAAT, ay['semboller'].index(r['sembol'])
        assert r['sinyaller'] == sinyal.get((r['sembol'], r['ts']), ''), (r['sembol'], i)
        assert (r['radar_sira'] is not None and r['radar_sira'] <= 10) == bool(ay['radar'][i, j])
        assert r['btc_rejim'] == ay['btc']['rejim'][i] and r['evren_sira'] == sira[r['sembol']]
    assert sum(bool(r['sinyaller']) for r in adaylar) > 100
    for s in ('E0/USDT', 'R1/USDT', 'L0/USDT'):
        satir = [r for r in adaylar if r['sembol'] == s and r['karar_mumu']]
        idx = [(r['ts'] - T0) // SAAT for r in satir]
        lab_oz = sl.ozellikler(ay['g'][s], ay['btc'], ay['ts'], idx)
        d = veri[s]
        yeni = mo.yeni_ozellikler(*(hz.izgaraya_koy(d, T0, P_N).__dict__[a] for a in ('O', 'H', 'L', 'C', 'QV', 'N',
                                                                                     'AQV')), idx=idx)
        for ad in hz.LAB_OZELLIKLERI:
            assert _ayni([np.nan if r[ad] is None else r[ad] for r in satir], lab_oz[ad]), (s, ad)
        for ad in mo.YENI_OZELLIKLER:
            assert _ayni([np.nan if r[ad] is None else r[ad] for r in satir], yeni[ad]), (s, ad)
    # ileri etiketler: t+24 mumu gelenler etiketli ve tam veriyle aynı
    etiket = {(r['ts'], r['sembol']): r for r in h.sorgu('SELECT * FROM etiket')}
    beklenen = [(r['ts'], r['sembol']) for r in adaylar if r['ts'] <= T0 + (P_N - 25) * SAAT]
    assert sorted(etiket) == sorted(beklenen)
    for (t, s), r in list(etiket.items())[::7]:
        O, H, L, C = ay['izg'][s][:4]
        e = mo.ileri_etiketler(O, H, L, C, (t - T0) // SAAT)
        assert {x: r[x] for x in e} == e
    assert h.meta_oku('son_temizlik_gunu') is not None and h.sayimlar()['defter'] > 0
    assert {r['tanim_id'] for r in etiket.values()} == {r['tanim_id'] for r in isl} == {k['m'].tanim_id}


def test_yeniden_baslatma_ve_kesinti(kagit_kosusu):
    """YENİDEN BAŞLATMA: P_YENIDEN turundan sonraki veritabanı kopyası yeni süreç nesneleriyle (Hafiza, Makine, borsa)
    sürdürülür; P_KESINTI'den başlayarak 6 karar saati makine kapalı. Kesintiden önce karar verilen işlemler (çıkışı
    kesinti sırasında ya da sonrasında, geri doldurulan mumlarla hesaplananlar dahil) kesintisiz koşunun aynısı;
    kaçırılan karar saatlerinde geriye dönük işlem yok, tarama'da KACIRILDI; bütün işlemler, kaçırılan saatlerin
    sinyalleri silinmiş strateji_lab işlemleriyle birebir."""
    k = kagit_kosusu
    simdi = T0 + (P_YENIDEN + 1) * SAAT + 20 * SN
    ex, h, m, ilerle = _makine_kur(k['veri'], k['tickers'], k['kopya'], simdi, evren=50, min_hacim=3e7)
    assert h.ilk_baslangic() == T0 + P_S * SAAT + 20 * SN and m.islem_alt_ts == T0 + (P_S - 1) * SAAT
    kacan = set(range(P_KESINTI, P_KESINTI + P_KESINTI_SAAT))
    for j in range(P_YENIDEN + 1, P_N + 1):
        if j - 1 in kacan:                                       # karar mumu j-1: makine kapalı
            continue
        ilerle(T0 + j * SAAT + 20 * SN)
        assert m.tur_guvenli()['durum'] == 'TAMAM'
    durum = {r['ts']: r['durum'] for r in h.taramalar()}
    assert len(durum) == P_N - P_S + 1
    assert all((durum[T0 + i * SAAT] == 'KACIRILDI') == (i in kacan) for i in range(P_S - 1, P_N))
    assert not h.sorgu('SELECT 1 FROM aday WHERE ts >= ? AND ts < ?',
                       (T0 + P_KESINTI * SAAT, T0 + (P_KESINTI + P_KESINTI_SAAT) * SAAT))
    sinir, son = T0 + P_KESINTI * SAAT, T0 + (P_KESINTI + P_KESINTI_SAAT) * SAAT
    yeni, eski = h.kagit_islemler(), k['h'].kagit_islemler()
    oncesi = lambda liste: sorted(_kagit_tuple(r) + (r['durum'],) for r in liste if r['sinyal_ts'] < sinir)  # noqa
    assert oncesi(yeni) == oncesi(eski)
    assert any(sinir <= r['sinyal_ts'] < son for r in eski)                       # kesintisiz koşu o saatlerde açtı
    assert not any(sinir <= r['sinyal_ts'] < son for r in yeni)                   # geriye dönük işlem yok
    assert any(r['giris_ts'] < sinir and r['cikis_ts'] is not None and r['cikis_ts'] > sinir for r in yeni)
    kapali, acik, _, _ = _lab_kagit(k['veri'], P_N, P_S, kacan={i for i in kacan})
    assert sorted(_kagit_tuple(r) for r in yeni if r['durum'] == 'KAPALI') == kapali
    say = {}
    for r in yeni:
        if r['durum'] == 'ACIK':
            say[(r['strateji'], r['sembol'])] = say.get((r['strateji'], r['sembol']), 0) + 1
    assert say == {x: v for x, v in acik.items() if v}
    assert m.pencereler.tam_okuma >= len(k['veri']) - 1                           # yeni süreç: pencereler baştan
    assert not ex.yasak_cagri
    h.kapat()


def test_pencere_onbellegi_veritabaniyla_ayni(tmp_path):
    """Önbellek yalnız yeni mumları okur; geriye doğru boşluk doldurulursa (satır sayısı tutmaz) ya da daha eski bir
    pencere istenirse baştan okur. Her durumda Hafiza.izgara ile birebir."""
    d = sentetik(400, 5)
    h = _hafiza(tmp_path)
    h.mum_yaz('A/USDT', np.delete(d[:300], [150, 151], axis=0))
    p = mk.PencereOnbellegi(h, 200)

    def ayni(son):
        a, b = p.izgara('A/USDT', T0 + son * SAAT), h.izgara('A/USDT', T0 + son * SAAT, 200)
        return all(np.array_equal(getattr(a, x), getattr(b, x), equal_nan=True) for x in b.__dict__)
    assert ayni(299) and p.tam_okuma == 1
    h.mum_yaz('A/USDT', d[300:310])
    assert ayni(309) and p.tam_okuma == 1                                           # yalnız yeni mumlar
    assert ayni(309) and p.tam_okuma == 1
    h.mum_yaz('A/USDT', d[150:152])                                                 # pencere içinde geriye dolum
    assert ayni(309) and p.tam_okuma == 2 and np.isfinite(p.izgara('A/USDT', T0 + 309 * SAAT).C[50:52]).all()
    assert ayni(305) and p.tam_okuma == 3                                           # daha eski pencere: baştan
    assert ayni(320) and p.tam_okuma == 3                                           # gelecek saatler NaN
    p.birak([])
    assert ayni(320) and p.tam_okuma == 4
    h.kapat()


# ------------------------------------------------------------------------------------------
# makine.py: evren, korunan pariteler, hatalar, uyarılar
# ------------------------------------------------------------------------------------------
class SahteTelegram:
    hazir = True

    def __init__(self, sonuc=True):
        self.mesajlar, self.sonuc = [], sonuc

    def gonder(self, metin):
        self.mesajlar.append(metin)
        return self.sonuc


def test_makine_evren_korunan_badsymbol_ag_hatasi_ve_saklama(tmp_path):
    """Evren: kaldıraçlı (XRPUP: XRP paritesi var), yasaklı (USDC), düşük hacimli (AZ, XRP) ve ilk N'in dışındaki (C) yok;
    BadSymbol (DELIST) bırakılır; ağ hatası alan parite (B) 2/4/8 sn bekleyip 4 denemeden sonra atlanır, tur ölmez.
    Evrenden çıkmış ama açık kâğıt işlemi olan parite (X) güncellenir ve çıkışı hesaplanır. Günlük temizlik eski
    aday satırını siler."""
    n = 130
    adlar = ['BTC/USDT', 'A/USDT', 'B/USDT', 'C/USDT', 'XRPUP/USDT', 'USDC/USDT', 'AZ/USDT', 'X/USDT', 'XRP/USDT']
    veri = {s: sentetik(n, i + 1, s0=(60000.0 if i == 0 else 10.0)) for i, s in enumerate(adlar)}
    tickers = {'BTC/USDT': {'quoteVolume': 1e9}, 'DELIST/USDT': {'quoteVolume': 6e8}, 'A/USDT': {'quoteVolume': 5e8},
               'B/USDT': {'quoteVolume': 4e8}, 'C/USDT': {'quoteVolume': 3e8}, 'XRPUP/USDT': {'quoteVolume': 9e8},
               'USDC/USDT': {'quoteVolume': 9e9}, 'AZ/USDT': {'quoteVolume': 1e6}, 'X/USDT': {'quoteVolume': 2e6},
               'XRP/USDT': {'quoteVolume': 1e6}}
    simdi = T0 + (n - 1) * SAAT + 20 * SN                                        # karar mumu n-2
    bekle = []
    ex, h, m, ilerle = _makine_kur(veri, tickers, str(tmp_path / 'm.db'), simdi, evren=3, aday_gun=2,
                                   uyku=bekle.append)
    ex.hata_plani['B/USDT'] = [ccxt.NetworkError('bağlantı koptu')] * 4
    e = 100
    giris = float(veri['X/USDT'][e, 1]) * (1 + sl.SLIP_GIRIS)
    h.kagit_ekle({'strateji': 'YUKSEK_ISABET', 'bilesen': 'YUKSEK_ISABET', 'sembol': 'X/USDT',
                  'sinyal_ts': T0 + (e - 1) * SAAT, 'giris_ts': T0 + e * SAAT, 'giris_fiyat': giris, 'durum': 'ACIK',
                  'olusturma_ms': T0})
    h.aday_yaz([{'ts': simdi - 5 * GUN, 'sembol': 'ESKI/USDT'}, {'ts': simdi - GUN - 3 * SAAT, 'sembol': 'YENI/USDT'}])
    r = m.tur_guvenli()
    assert r['durum'] == 'TAMAM' and r['evren'] == 2 and r['mum_hatasi'] == 1 and bekle == [2, 4, 8]
    assert r['ayrinti']['yok'] == ['DELIST/USDT'] and list(r['ayrinti']['hata']) == ['B/USDT']
    assert set(r['ayrinti']['verisiz']) == {'DELIST/USDT', 'B/USDT'}
    assert h.parite_oku('DELIST/USDT')['durum'] == 'YOK' and h.parite_oku('X/USDT')['kapsam_ts'] == T0 + (n - 1) * SAAT
    for s in ('C/USDT', 'XRPUP/USDT', 'USDC/USDT', 'AZ/USDT', 'XRP/USDT'):
        assert h.parite_oku(s) is None, s
    ady = {x['sembol']: x for x in h.adaylar(T0 + (n - 2) * SAAT)}
    assert {s for s, x in ady.items() if x['karar_mumu'] == 1} == {'BTC/USDT', 'A/USDT'}
    # karar mumu olmayan evren pariteleri (B ağ hatası, DELIST borsada yok) de satır alır: özellikler NULL
    assert set(ady) == {'BTC/USDT', 'A/USDT', 'B/USDT', 'DELIST/USDT'}
    for s in ('B/USDT', 'DELIST/USDT'):
        x = ady[s]
        assert x['karar_mumu'] == 0 and x['ret24'] is None and x['rsi14'] is None and x['sinyaller'] == ''
        assert x['evren_sira'] == hz.evren_sec(tickers, 5e6, 3).sira[s]
        assert x['btc_rejim'] == ady['BTC/USDT']['btc_rejim']
        assert x['btc_ret24'] == ady['BTC/USDT']['btc_ret24'] and x['olusturma_ms'] == simdi
    x = h.kagit_islemler(sembol='X/USDT')[0]
    d = veri['X/USDT']
    b, g, c, tip = sl.cikis_simule('HEDEF_STOP', *(d[:, i].tolist() for i in (1, 2, 3, 4)), None, e)
    assert x['durum'] == 'KAPALI' and (x['cikis_ts'], x['cikis_fiyat'], x['cikis_tipi']) == (T0 + (b + 1) * SAAT, c, tip)
    assert x['getiri'] == sl.net_getiri(g, c) and x['giris_fiyat'] == g
    assert not h.sorgu("SELECT 1 FROM aday WHERE sembol = 'ESKI/USDT'")         # aday_gun=2: 5 günlük silindi
    assert h.sorgu("SELECT 1 FROM aday WHERE sembol = 'YENI/USDT'")
    assert json.loads(h.meta_oku('son_evren'))['semboller'] == ['BTC/USDT', 'DELIST/USDT', 'A/USDT', 'B/USDT']
    # ticker alınamazsa son evrenle sürer (UYARI olayı)
    ilerle(simdi + SAAT)
    ex.tickers = None
    ex.fetch_tickers = lambda: (_ for _ in ()).throw(ccxt.NetworkError('ticker yok'))
    r = m.tur_guvenli()
    assert r['durum'] == 'TAMAM' and r['ayrinti']['yedek_evren'] and h.olaylar(tur='UYARI')
    assert {x['sembol'] for x in h.adaylar(T0 + (n - 1) * SAAT) if x['karar_mumu']} == {'BTC/USDT', 'A/USDT',
                                                                                          'B/USDT'}   # B düzeldi
    h.kapat()


def test_tur_hatasi_ve_mum_yok_uyarisi_sinirli(tmp_path):
    """Tur hatası (ilk turda ticker yok: yedek evren de yok) ve hiç mum alınamaması: tarama HATA, olay HATA, Telegram
    uyarısı tür başına 6 saatte bir; döngü ölmez."""
    veri = {'BTC/USDT': sentetik(50, 1, s0=60000.0), 'A/USDT': sentetik(50, 2)}
    tg = SahteTelegram()
    simdi = T0 + 50 * SAAT + 20 * SN
    ex, h, m, ilerle = _makine_kur(veri, None, str(tmp_path / 'm.db'), simdi, telegram=tg)
    ex.fetch_tickers = lambda: (_ for _ in ()).throw(ccxt.ExchangeNotAvailable('bakım'))
    r = m.tur_guvenli()
    assert r['durum'] == 'HATA' and 'ExchangeNotAvailable' in r['ayrinti']['hata']
    assert h.taramalar()[0]['durum'] == 'HATA' and len(h.olaylar(tur='HATA')) == 1
    assert len(tg.mesajlar) == 1 and 'TUR_HATASI' in tg.mesajlar[0]
    ilerle(simdi + SAAT)
    assert m.tur_guvenli()['durum'] == 'HATA' and len(tg.mesajlar) == 1           # 6 saat dolmadı
    ilerle(simdi + 6 * SAAT)
    assert m.tur_guvenli()['durum'] == 'HATA' and len(tg.mesajlar) == 2
    # hiç mum alınamadı (borsa ağ hatası veriyor): tur kaydı HATA, MUM_YOK uyarısı
    del ex.fetch_tickers
    ex.tickers = {s: {'quoteVolume': 1e9} for s in veri}
    ex.hata_plani = {s: [ccxt.NetworkError('ağ')] * 8 for s in veri}
    ilerle(simdi + 7 * SAAT)
    r = m.tur_guvenli()
    assert r['durum'] == 'HATA' and r['ayrinti']['neden'] == 'hiç mum alınamadı' and r['mum_hatasi'] == 2
    assert 'MUM_YOK' in tg.mesajlar[-1] and len(tg.mesajlar) == 3
    h.kapat()


def test_gec_tur_ve_ilk_baslangic_oncesi_yeni_islem_yok(tmp_path, monkeypatch):
    """Karar mumunun kapanışından 15 dakikadan sonra başlayan tur ve makinenin ilk başlangıcından önceki karar mumu:
    özellik ve sinyal yazılır, yeni kâğıt işlem açılmaz. Zamanında tur aynı sinyalle BEKLIYOR açar."""
    monkeypatch.setattr(mk, 'karar_sinyalleri', lambda s, rejim, i=-1: {'TREND_DIP_RSI2': 'TREND_DIP_RSI2'})
    veri = {'BTC/USDT': sentetik(60, 1, s0=60000.0), 'A/USDT': sentetik(60, 2)}
    tickers = {s: {'quoteVolume': 1e9} for s in veri}
    # geç tur (16 dk)
    ex, h, m, ilerle = _makine_kur(veri, tickers, str(tmp_path / 'gec.db'), T0 + 58 * SAAT + 16 * 60 * SN)
    r = m.tur_guvenli()
    assert r['durum'] == 'TAMAM' and r['ayrinti']['gec'] and not r['ayrinti']['islem_izni']
    assert not h.kagit_islemler() and {x['sinyaller'] for x in h.adaylar(T0 + 57 * SAAT)} == {'TREND_DIP_RSI2'}
    # ilk başlangıç saat başından 16 dk sonra: ilk işlem açabilen tur sonraki saatin turu (lab karşılığı onun girişi)
    assert h.meta_oku('ilk_islem_ts') is None and 'lab karşılığı' not in mk.rapor_metni(h, T0 + 59 * SAAT)
    ilerle(T0 + 59 * SAAT + 20 * SN)
    assert m.tur_guvenli()['ayrinti']['islem_izni'] and h.meta_oku('ilk_islem_ts') == str(T0 + 58 * SAAT)
    assert 'lab karşılığı --baslangic 2026-01-03 11:00 UTC' in mk.rapor_metni(h, T0 + 59 * SAAT + 20 * SN)
    h.kapat()
    # ilk başlangıçtan önceki karar mumu
    ex, h, m, ilerle = _makine_kur(veri, tickers, str(tmp_path / 'once.db'), T0 + 58 * SAAT + 20 * SN)
    ex.simdi_ms = T0 + 56 * SAAT + 20 * SN
    r = m.tur_guvenli(T0 + 56 * SAAT + 20 * SN)
    assert not r['ayrinti']['islem_izni'] and not h.kagit_islemler()
    ilerle(T0 + 58 * SAAT + 20 * SN)                                                 # zamanında, başlangıçta
    r = m.tur_guvenli()
    assert r['ayrinti']['islem_izni'] and r['ayrinti']['yeni_bekleyen'] == 2
    assert {(x['sembol'], x['durum'], x['sinyal_ts']) for x in h.kagit_islemler()} == {
        ('BTC/USDT', 'BEKLIYOR', T0 + 57 * SAAT), ('A/USDT', 'BEKLIYOR', T0 + 57 * SAAT)}
    h.kapat()


def test_delist_paritedeki_acik_islem_azami_sureden_sonra_veri_yok_ile_kapanir(tmp_path):
    """Borsada artık olmayan (BadSymbol) paritenin açık işlemi: azami tutma süresi dolmadan açık kalır, dolunca son
    saklı kapanıştan (SLIP_ZAMAN) VERI_YOK ile kapanır."""
    n = 60
    d = sentetik(n, 3, sigma=0.0)                                                   # düz: stop tetiklenmez
    veri = {'BTC/USDT': sentetik(n + 200, 1, s0=60000.0)}
    tickers = {'BTC/USDT': {'quoteVolume': 1e9}}
    ex, h, m, ilerle = _makine_kur(veri, tickers, str(tmp_path / 'm.db'), T0 + (n + 100) * SAAT + 20 * SN)
    h.mum_yaz('ESKI/USDT', d)
    h.parite_yaz('ESKI/USDT', kapsam_ts=T0 + n * SAAT, market_id='ESKIUSDT')
    giris = float(d[10, 1]) * (1 + sl.SLIP_GIRIS)
    h.kagit_ekle({'strateji': 'ERKEN_BIRIKIM', 'bilesen': 'ERKEN_BIRIKIM', 'sembol': 'ESKI/USDT',
                  'sinyal_ts': T0 + 9 * SAAT, 'giris_ts': T0 + 10 * SAAT, 'giris_fiyat': giris, 'durum': 'ACIK',
                  'olusturma_ms': T0})
    m.tur_guvenli()
    assert h.parite_oku('ESKI/USDT')['durum'] == 'YOK' and h.kagit_islemler('ACIK')       # 168 saat dolmadı
    ilerle(T0 + (10 + 168) * SAAT + 20 * SN)
    m.tur_guvenli()
    x = h.kagit_islemler(sembol='ESKI/USDT')[0]
    c = float(d[-1, 4]) * (1 - sl.SLIP_ZAMAN)
    assert x['durum'] == 'KAPALI' and x['cikis_tipi'] == 'VERI_YOK' and x['cikis_fiyat'] == c
    assert x['cikis_ts'] == T0 + n * SAAT and x['getiri'] == sl.net_getiri(giris, c) and x['sure_saat'] == n - 10
    assert 'VERI_YOK' in mk.rapor_metni(h, T0 + 200 * SAAT)
    h.kapat()


# ------------------------------------------------------------------------------------------
# makine.py: güvenlik
# ------------------------------------------------------------------------------------------
def test_borsa_anahtarsiz_ve_salt_okunur(monkeypatch):
    monkeypatch.setenv('BINANCE_API_KEY', 'ortamdaki-anahtar')
    monkeypatch.setenv('BINANCE_SECRET_KEY', 'ortamdaki-gizli')
    ex = mk.borsa_kur()
    assert not ex.apiKey and not ex.secret and not ex.password and not ex.uid
    assert ex.options['defaultType'] == 'spot' and ex.enableRateLimit
    s = mk.SaltOkunurBorsa(ex)
    for ad in ('create_order', 'cancel_order', 'fetch_balance', 'fetch_my_trades', 'fetch_open_orders', 'withdraw',
               'privateGetAccount', 'sapiGetCapitalConfigGetall', 'apiKey', 'secret', 'fetch_ohlcv', 'sign',
               '_ex', '__dict__', '_SaltOkunurBorsa__ex', 'create_market_buy_order'):   # iç istemciye yol yok
        with pytest.raises(PermissionError):
            getattr(s, ad)
    with pytest.raises(PermissionError):
        del s.fetch_tickers
    with pytest.raises(PermissionError):
        s.apiKey = 'x'
    assert s.fetch_tickers == ex.fetch_tickers and s.publicGetKlines == ex.publicGetKlines
    m = mk.Makine(SahteBorsa({}, T0), hz.Hafiza(':memory:'), saat=lambda: T0)
    assert isinstance(m.ex, mk.SaltOkunurBorsa) and m.toplayici.ex is m.ex
    with pytest.raises(PermissionError):
        m.ex.create_order('BTC/USDT', 'market', 'buy', 1)
    m.h.kapat()


def test_telegram_ayari_yalniz_iki_anahtar(tmp_path):
    (tmp_path / '.env').write_text("BINANCE_API_KEY=anahtar\nTELEGRAM_TOKEN=dosya-belirtec\nTELEGRAM_CHAT_ID=42\n",
                                   encoding='utf-8')
    assert mk.telegram_ayarlari(str(tmp_path), ortam={}) == ('dosya-belirtec', '42')
    assert mk.telegram_ayarlari(str(tmp_path), ortam={'TELEGRAM_TOKEN': 'ortam'}) == ('ortam', '42')
    assert mk.telegram_ayarlari(str(tmp_path / 'yok'), ortam={}) == (None, None)


@pytest.fixture
def gunluk_geri_al():
    eski = (list(mk.LOG.handlers), mk.LOG.propagate, mk.LOG.level)
    yield
    for i in list(mk.LOG.handlers):
        mk.LOG.removeHandler(i)
        i.close()
    for i in eski[0]:
        mk.LOG.addHandler(i)
    mk.LOG.propagate, mk.LOG.level = eski[1], eski[2]


def test_belirtec_gunlukte_ve_hatalarda_maskelenir(tmp_path, gunluk_geri_al):
    belirtec = '123456789:GIZLI-belirtec-XYZ'
    akis = io.StringIO()
    mk.gunluk_kur(str(tmp_path / 'makine.log'), gizliler=[belirtec], akis=akis)

    def acici(istek, timeout=None):
        raise OSError(f"bağlantı kurulamadı: {istek.full_url}")                    # URL'de belirteç var
    tg = mk.Telegram(belirtec, '42', acici=acici)
    assert tg.gonder('deneme') is False
    mk.LOG.error(f"elle yazılmış {belirtec}")
    try:
        raise RuntimeError(f"iz {belirtec}")
    except RuntimeError:
        mk.LOG.exception('hata izi')
    logging.getLogger('makine.hafiza').warning(f"alt günlük {belirtec}")
    for i in mk.LOG.handlers:
        i.flush()
    cikti = akis.getvalue() + (tmp_path / 'makine.log').read_text(encoding='utf-8')
    assert belirtec not in cikti and cikti.count('***') >= 5 and 'Telegram mesajı gönderilemedi' in cikti


def test_telegram_gonderimi_duz_metin_ve_parcalama():
    istekler = []

    class Yanit:
        def __init__(self, durum):
            self.status = durum

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def acici(istek, timeout=None):
        istekler.append(istek)
        return Yanit(200)
    tg = mk.Telegram('B', 'C', acici=acici)
    metin = '\n'.join(f"satır {i} " + 'x' * 90 for i in range(100))               # ~10 bin karakter
    assert tg.gonder(metin)
    import urllib.parse
    govde = [urllib.parse.parse_qs(i.data.decode('utf-8')) for i in istekler]
    assert len(istekler) == 3 and all(i.full_url == 'https://api.telegram.org/botB/sendMessage' for i in istekler)
    assert all(set(g) == {'chat_id', 'text'} and len(g['text'][0]) <= 4000 for g in govde)   # parse_mode yok
    assert '\n'.join(g['text'][0] for g in govde) == metin
    assert mk.parcala('a' * 9000) == ['a' * 4000, 'a' * 4000, 'a' * 1000] and mk.parcala('') == ['']
    assert not mk.Telegram(None, 'C', acici=acici).gonder('x') and len(istekler) == 3
    assert not mk.Telegram('B', 'C', acici=lambda i, timeout=None: Yanit(500)).gonder('x')


# ------------------------------------------------------------------------------------------
# makine.py: rapor, döngü, komut satırı, performans
# ------------------------------------------------------------------------------------------
def _rapor_veritabani(h, simdi):
    saat0 = (simdi // SAAT) * SAAT
    for k in range(1, 25):
        durum = 'KACIRILDI' if k in (3, 4) else 'HATA' if k == 5 else 'TAMAM'
        h.tarama_yaz({'ts': saat0 - k * SAAT, 'durum': durum, 'evren': 150 if durum == 'TAMAM' else None,
                      'mum_hatasi': 1 if k == 6 else 0})
    h.tarama_yaz({'ts': saat0 - 30 * SAAT, 'durum': 'HATA'})                         # 24 saatten eski
    h.olay_yaz('HATA', 'deneme hatası', ms=simdi - 2 * SAAT)
    h.aday_yaz([{'ts': saat0 - 2 * SAAT, 'sembol': f'S{i}/USDT', 'sinyaller': 'BOT_VEKILI'} for i in range(3)]
               + [{'ts': saat0 - 2 * SAAT, 'sembol': 'E/USDT', 'sinyaller': 'ERKEN_BIRIKIM,YUKSEK_ISABET'}])
    gun = saat0 - 5 * GUN
    h.aday_yaz([{'ts': gun, 'sembol': f'T{i}/USDT', 'sinyaller': 'BOT_VEKILI' if i < 2 else ''} for i in range(10)])
    h.etiket_yaz([{'ts': gun, 'sembol': f'T{i}/USDT', 'ucus10': int(i in (0, 5)), 'max_yukari_24s': 0.01 * i}
                  for i in range(10)])
    for i, (r, cikis) in enumerate(((0.05, simdi - 3 * SAAT), (-0.02, simdi - 3 * GUN), (0.01, simdi - 2 * GUN))):
        _kagit(h, 'BOT_VEKILI', f'S{i}/USDT', cikis - 10 * SAAT, 'KAPALI', giris_fiyat=1.0, cikis_ts=cikis, getiri=r,
               cikis_tipi='STOP')
    _kagit(h, 'BOT_VEKILI', 'S9/USDT', saat0 - 3 * SAAT, 'ACIK', giris_fiyat=1.0)
    _kagit(h, 'ERKEN_BIRIKIM', 'E/USDT', saat0 - 2 * SAAT, 'BEKLIYOR')


def test_rapor_metni_bolumler_ve_sayilar(tmp_path):
    h = _hafiza(tmp_path)
    simdi = T0 + 10 * GUN + 6 * SAAT + 25 * SN
    h.meta_yaz('ilk_baslangic', T0 + 20 * SN)
    _rapor_veritabani(h, simdi)
    metin = mk.rapor_metni(h, simdi)
    s = metin.split('\n')
    assert s[0] == 'MAKİNE (kâğıt mod: gerçek emir yok)' and 'ilk başlangıç: 2026-01-01 00:00 UTC' in s[1]
    assert "  Turlar: 21 tamam, 2 kaçırıldı, 1 hatalı | evren: 150 parite | hata kaydı: 1 | mum hatası: 1" in s
    assert "  Sinyaller: BOT_VEKILI 3, ERKEN_BIRIKIM 1, YUKSEK_ISABET 1" in s
    assert "  Kâğıt işlem (20 USDT/işlem): açılan 2, kapanan 1, kapananların sonucu +1.00 USDT" in s
    assert ['BOT_VEKILI', 'açılan', '2', 'kapanan', '1', '+1.00', 'USDT'] in [x.split() for x in s if x.startswith('    ')]
    satir = next(x for x in s if x.startswith('  BOT_VEKILI') and 'kapalı' not in x and 'açılan' not in x)
    assert satir.split() == ['BOT_VEKILI', '3', '66.7%', '+1.333%', '+0.80', '1', '0'], satir
    assert next(x for x in s if x.startswith('  ERKEN_BIRIKIM')).split() == ['ERKEN_BIRIKIM', '0', '-', '-', '+0.00',
                                                                               '0', '1']
    assert '  taban: n 10, uçuş10 20.00%, ort. en yüksek +4.50%' in s
    assert next(x for x in s if x.startswith('  BOT_VEKILI') and '%' in x and x.split()[1] == '2').split() == [
        'BOT_VEKILI', '2', '50.00%', '+0.50%', '2.50']
    assert any(x.startswith('VERİTABANI: ') and 'kagit_islem 5' in x for x in s)
    assert s[-1].startswith('SON HATA: ') and 'deneme hatası' in s[-1]
    bos = hz.Hafiza(str(tmp_path / 'bos.db'))
    m = mk.rapor_metni(bos, simdi)
    assert 'henüz kâğıt işlem yok' in m and 'etiketli sinyal yok' in m and m.endswith('SON HATA: yok')
    bos.kapat()
    d = mk.durum_metni(h, simdi)
    assert 'MAKİNE DURUMU' in d and 'BOT_VEKILI S9/USDT ACIK' in d and 'ERKEN_BIRIKIM E/USDT BEKLIYOR' in d
    assert 'BAŞLANGIÇTAN BERİ' in d and 'deneme hatası' in d
    h.kapat()


def test_gunluk_rapor_gunde_bir_kez(tmp_path):
    veri = {'BTC/USDT': sentetik(10, 1, s0=60000.0)}
    tg = SahteTelegram()
    simdi = T0 + 10 * GUN + 5 * SAAT + 30 * 60 * SN
    ex, h, m, ilerle = _makine_kur(veri, None, str(tmp_path / 'm.db'), simdi, telegram=tg)
    assert m.gunluk_rapor() is None and not tg.mesajlar                          # 06:00 UTC'den önce
    ilerle(simdi + SAAT)
    assert m.gunluk_rapor() is True and len(tg.mesajlar) == 1
    assert tg.mesajlar[0].startswith('MAKİNE (kâğıt mod: gerçek emir yok)')
    ilerle(simdi + 10 * SAAT)
    assert m.gunluk_rapor() is None and len(tg.mesajlar) == 1                    # aynı gün
    tg.sonuc = False
    ilerle(simdi + 25 * SAAT)
    assert m.gunluk_rapor() is False and h.olaylar(tur='TELEGRAM')                # gönderilemedi: işaretlenmez
    tg.sonuc = True
    ilerle(simdi + 26 * SAAT)
    assert m.gunluk_rapor() is True and len(tg.mesajlar) == 3
    m.telegram = None                                                             # ayar yok: gün işaretlenir
    ilerle(simdi + 49 * SAAT)
    assert m.gunluk_rapor() is False and m.gunluk_rapor() is None
    h.kapat()


def test_dongu_saat_basi_yeniden_deneme_rapor_ve_durdurma(tmp_path):
    """calis(): saat başından 20 sn sonra tur; ilk tur hata verirse (ticker yok) 60 sn sonra yeniden denenir; günlük
    rapor 06:00 UTC'den sonraki ilk turda, günde bir kez; durdurma isteğinde döngü döner. Gerçek bekleme yok."""
    veri = {'BTC/USDT': sentetik(200, 1, s0=60000.0), 'A/USDT': sentetik(200, 2)}
    tg = SahteTelegram()
    bitis = T0 + 175 * SAAT + 30 * 60 * SN
    durum = {'m': None}

    def uyku(sn):
        ilerle(saat_oku() + int(sn * 1000))
        if saat_oku() >= bitis:
            durum['m'].durdur()
    ex, h, m, ilerle = _makine_kur(veri, {s: {'quoteVolume': 1e9} for s in veri}, str(tmp_path / 'm.db'),
                                   T0 + 150 * SAAT + 5 * SN, telegram=tg, uyku=uyku)
    saat_oku = m.saat
    durum['m'] = m
    hata = [ccxt.NetworkError('ticker yok')] * 4
    asil = ex.fetch_tickers

    def tickers():
        if hata:
            raise hata.pop()
        return asil()
    ex.fetch_tickers = tickers
    m.calis()
    t = h.taramalar()
    assert [r['ts'] for r in t] == [T0 + k * SAAT for k in range(149, 175)]
    assert all(r['durum'] == 'TAMAM' for r in t) and len(h.olaylar(tur='HATA')) == 1
    assert t[0]['baslangic_ms'] == T0 + 150 * SAAT + 20 * SN + 14 * SN + 60 * SN  # 2+4+8 sn deneme, 60 sn sonra
    assert all(r['baslangic_ms'] == r['ts'] + SAAT + 20 * SN for r in t[1:])
    assert len(tg.mesajlar) == 3 and 'TUR_HATASI' in tg.mesajlar[0]               # uyarı + 2 günlük rapor
    assert [x.split('\n')[0] for x in tg.mesajlar[1:]] == ['MAKİNE (kâğıt mod: gerçek emir yok)'] * 2
    assert h.meta_oku('son_rapor_gunu') == str((T0 + 174 * SAAT) // GUN) and m.durdu
    h.kapat()


def test_durdurma_istegi_turu_yarim_birakir_yeniden_yapilir(tmp_path):
    veri = {s: sentetik(80, i + 1) for i, s in enumerate(['BTC/USDT', 'A/USDT', 'B/USDT'])}
    tickers = {s: {'quoteVolume': 1e9} for s in veri}
    simdi = T0 + 80 * SAAT + 20 * SN
    ex, h, m, ilerle = _makine_kur(veri, tickers, str(tmp_path / 'm.db'), simdi)
    asil = ex.publicGetKlines

    def kesen(params):
        m.durdur()                                                                # SIGTERM ilk istekte geldi
        return asil(params)
    ex.publicGetKlines = kesen
    r = m.tur_guvenli()
    assert r['durum'] == 'YARIM' and h.taramalar()[0]['durum'] == 'YARIM' and not h.adaylar(T0 + 79 * SAAT)
    assert h.parite_oku('BTC/USDT')['kapsam_ts'] == T0 + 80 * SAAT and h.parite_oku('A/USDT') is None
    ex.publicGetKlines = asil
    m2 = mk.Makine(ex, hz.Hafiza(h.yol, saat=m.saat), saat=m.saat, uyku=lambda x: None)   # yeniden başlatma
    assert m2._yapilacak_mi(T0 + 79 * SAAT, simdi)
    assert m2.tur_guvenli()['durum'] == 'TAMAM' and len(m2.h.adaylar(T0 + 79 * SAAT)) == 3
    assert not m2._yapilacak_mi(T0 + 79 * SAAT, simdi)
    m2.h.kapat()
    h.kapat()


def test_komut_satiri(tmp_path, monkeypatch, capsys, gunluk_geri_al):
    """--durum/--rapor salt okunur (veritabanı yoksa 2), --tek-tur sahte borsayla bir tur (kilit, günlük dosyası,
    SIGTERM/SIGINT işleyicisi), ikinci kopya Türkçe mesajla 3 döner, --rapor --telegram ayarsız 1 döner."""
    db = str(tmp_path / 'makine.db')
    assert mk.main(['--durum', '--db', db]) == 2 and 'veritabanı yok' in capsys.readouterr().out
    simdi = int(time.time() * 1000)
    ts0 = (simdi // SAAT) * SAAT - 300 * SAAT
    veri = {s: sentetik(300, i + 1, ts0=ts0) for i, s in enumerate(['BTC/USDT', 'A/USDT'])}
    ex = SahteBorsa(veri, simdi + SAAT, tickers={s: {'quoteVolume': 1e9} for s in veri})
    isleyiciler = []
    monkeypatch.setattr(mk, 'borsa_kur', lambda: ex)
    monkeypatch.setattr(mk, 'telegram_ayarlari', lambda *a, **k: (None, None))
    monkeypatch.setattr(mk.signal, 'signal', lambda s, f: isleyiciler.append((s, f)))
    assert mk.main(['--tek-tur', '--db', db, '--evren', '5']) == 0
    assert [s for s, _ in isleyiciler] == [mk.signal.SIGTERM, mk.signal.SIGINT]
    assert os.path.exists(str(tmp_path / 'makine.log')) and 'tur ' in (tmp_path / 'makine.log').read_text('utf-8')
    with hz.TekKopya(db + '.lock'):                                                # kilit bırakılmış: alınabilir
        assert mk.main(['--tek-tur', '--db', db]) == 3
    assert 'zaten çalışıyor' in capsys.readouterr().out
    h = hz.Hafiza(db, salt_okunur=True)
    assert [r['durum'] for r in h.taramalar()] == ['TAMAM'] and len(h.adaylar(ts0 + 299 * SAAT)) == 2
    h.kapat()
    assert mk.main(['--durum', '--db', db]) == 0 and 'MAKİNE DURUMU' in capsys.readouterr().out
    assert mk.main(['--rapor', '--db', db]) == 0
    assert capsys.readouterr().out.startswith('MAKİNE (kâğıt mod: gerçek emir yok)')
    assert mk.main(['--rapor', '--telegram', '--db', db]) == 1
    with pytest.raises(SystemExit):
        mk.main(['--durum', '--rapor'])


def test_performans_150_parite_1500_saat(tmp_path):
    """Bir tur: 150 parite x 1500 saatlik pencere (göstergeler, sinyaller, 30 özellik, radar, kâğıt işlem, yazım).
    Mumlar önceden veritabanında; turda her parite için 1 yeni mum çekilir. Süre raporlanır."""
    N, P = 150, hz.PENCERE
    semboller = ['BTC/USDT'] + [f'P{i:03d}/USDT' for i in range(N - 1)]
    veri = {s: sentetik(P + 2, i, s0=(60000.0 if i == 0 else 10.0), hacim=2e6) for i, s in enumerate(semboller)}
    tickers = {s: {'quoteVolume': 1e9 - i} for i, s in enumerate(semboller)}
    ex, h, m, ilerle = _makine_kur(veri, tickers, str(tmp_path / 'm.db'), T0 + P * SAAT + 20 * SN)
    with h.islem():
        for s in semboller:
            h.mum_yaz(s, veri[s][:P - 1])
            h.parite_yaz(s, kapsam_ts=T0 + (P - 1) * SAAT, market_id=s.replace('/', ''), ilk_gorulme_ms=T0)
    z = time.perf_counter()
    r1 = m.tur_guvenli()
    ilk = time.perf_counter() - z
    ilerle(T0 + (P + 1) * SAAT + 20 * SN)
    z = time.perf_counter()
    r2 = m.tur_guvenli()
    sonraki = time.perf_counter() - z
    print(f"\n150 parite x 1500 saat: ilk tur {ilk:.2f} sn (pencereler veritabanından), sonraki tur {sonraki:.2f} sn; "
          f"süre dağılımı {r2['ayrinti']['sure_sn']}")
    assert r1['durum'] == r2['durum'] == 'TAMAM' and r2['evren'] == N and r2['guncellenen'] == N
    assert len(h.adaylar(T0 + P * SAAT)) == N and r2['defter'] <= 30
    assert sonraki < 20
    h.kapat()


# ------------------------------------------------------------------------------------------
# Kayan pencere ve değişen evrenle parite; inceleme bulgularının regresyon testleri
# ------------------------------------------------------------------------------------------
D_N, D_S = 1640, 1520                   # veri PENCERE'den uzun: her turun penceresi verinin içinden başlar (kayar)


def test_kagit_parite_kayan_pencere_ve_degisen_evren(tmp_path):
    """PARİTE (2): veri PENCERE'den uzun (pencere başı her tur kayar), evren saat saat değişir (pariteler hacimle
    çıkıp geri girer; RSI2/ORTA_BANT işlemi açıkken evrenden çıkan pariteler dahil), radar eşitlikleri (birebir ve
    3 katı ölçekli kopyalar: float32 kesit). Karşılaştırma: strateji_lab iç işlevleri tam veriyle, makinenin saat saat
    evreni maskesiyle (aday satırlarından). İşlemler, açık kalanlar, sinyaller ve radar birebir."""
    ts = T0 + SAAT * np.arange(D_N, dtype=np.int64)
    rng = np.random.default_rng(55)
    veri = {'BTC/USDT': _ham(ts, *_btc_rejimli(D_N, 5, trend=35, yatay=70), rng)}
    for i in range(3):
        veri[f'E{i}/USDT'] = _ham(ts, *_birikim(D_N, 60 + i, 17 * i + 3, i % 2 == 0, donem=100 + 7 * i), rng)
    for i in range(2):
        veri[f'D{i}/USDT'] = _ham(ts, *_donus(D_N, 70 + i, 11 * i + 2, aralik=40 + 3 * i), rng)
    for i in range(4):
        veri[f'R{i}/USDT'] = _ham(ts, *_rastgele(D_N, 80 + i, sigma=0.008 + 0.002 * i), rng)
    o, hh, ll, c, v = _rastgele(D_N, 90, sigma=0.015, egim=0.0008, hacim=2e6)
    baz = _ham(ts, o, hh, ll, c, v * np.where(rng.random(D_N) < 0.08, 6.0, 1.0), rng)
    veri['K0/USDT'], veri['K1/USDT'] = baz.copy(), baz.copy()      # birebir kopya: tam eşitlik
    z = baz.copy()
    z[:, 1:5] *= 3.0
    z[:, 5] /= 3.0
    veri['Z0/USDT'] = z                                            # 3 katı: float64'te farklı, float32'de eşit R24
    semboller = list(veri)
    temel = {s: 1e9 - 1e6 * i for i, s in enumerate(semboller)}
    # evrenden çıkışlar (tur saati aralığı): sinyal çıkışlı işlemin girişinden hemen sonra + sabit pencereler
    dusus = [('E1/USDT', D_S + 10, D_S + 50), ('K1/USDT', D_S + 20, D_S + 35), ('R2/USDT', D_S + 60, D_S + 200)]
    gorulen = {s for s, _, _ in dusus}
    for x in _lab_kagit(veri, D_N, D_S)[0]:
        if x[1] in ('TREND_DIP_RSI2', 'YATAY_DONUS') and x[10] >= 3 and x[2] not in gorulen and len(dusus) < 6:
            k = (x[3] - T0) // SAAT
            dusus.append((x[2], k + 2, k + 40))
            gorulen.add(x[2])

    class Borsa(SahteBorsa):
        def fetch_tickers(self):
            k = (self.simdi_ms - T0) // SAAT
            return {s: {'quoteVolume': 1e6 if any(a <= k < b for x, a, b in dusus if x == s) else temel[s]}
                    for s in semboller}
    saat = [T0 + D_S * SAAT + 20 * SN]
    ex = Borsa(veri, saat[0])
    h = hz.Hafiza(str(tmp_path / 'm.db'), saat=lambda: saat[0])
    m = mk.Makine(ex, h, evren=60, min_hacim=3e7, saat=lambda: saat[0], uyku=lambda x: None)
    for k in range(D_S, D_N + 1):
        saat[0] = ex.simdi_ms = T0 + k * SAAT + 20 * SN
        assert m.tur_guvenli()['durum'] == 'TAMAM'
    assert not ex.yasak_cagri
    adaylar = h.sorgu('SELECT ts, sembol, sinyaller, radar_sira, karar_mumu FROM aday')
    maske = {s: np.zeros(D_N, bool) for s in semboller}
    for r in adaylar:
        maske[r['sembol']][(r['ts'] - T0) // SAAT] = True
    assert sum(not maske[s][k - 1] for s, a, b in dusus for k in range(a, min(b, D_N + 1))) > 100   # evren değişti
    isl = h.kagit_islemler()
    kapali, acik, sinyal, ay = _lab_kagit(veri, D_N, D_S, evren_maske=maske)
    makine_kapali = sorted(_kagit_tuple(r) for r in isl if r['durum'] == 'KAPALI')
    assert makine_kapali == kapali and len(kapali) > 40
    say = {}
    for r in isl:
        if r['durum'] == 'ACIK':
            say[(r['strateji'], r['sembol'])] = say.get((r['strateji'], r['sembol']), 0) + 1
    assert say == {x: v for x, v in acik.items() if v}
    # evrenden çıkmışken kapanan sinyal çıkışlı işlemler (korunan paritenin çıkışı) gerçekten sınandı
    disarida = [x for x in kapali if x[8] in ('SINYAL', 'STOP', 'ZAMAN') and x[1] in ('TREND_DIP_RSI2', 'YATAY_DONUS')
                and not maske[x[2]][(x[6] - T0) // SAAT - 1]]
    assert disarida
    for r in adaylar:
        i, j = (r['ts'] - T0) // SAAT, ay['semboller'].index(r['sembol'])
        assert r['sinyaller'] == sinyal.get((r['sembol'], r['ts']), ''), (r['sembol'], i)
        assert (r['radar_sira'] is not None and r['radar_sira'] <= 10) == bool(ay['radar'][i, j]), (r['sembol'], i)
    h.kapat()


def _islem_ekle(h, strateji, sembol, d, e, durum='ACIK'):
    """d: (n, 9) veri (T0'dan); e: giriş mumu indeksi. ACIK ise giriş dolumu O[e] x (1 + SLIP_GIRIS)."""
    k = {'strateji': strateji, 'bilesen': strateji, 'sembol': sembol, 'sinyal_ts': int(d[e - 1, 0]),
         'giris_ts': int(d[e, 0]), 'durum': durum, 'olusturma_ms': int(d[e, 0])}
    if durum == 'ACIK':
        k['giris_fiyat'] = float(d[e, 1]) * (1 + sl.SLIP_GIRIS)
    kimlik = h.kagit_ekle(k)
    assert kimlik is not None
    return kimlik


def _lab_cikis(d, strateji, e):
    """strateji_lab'ın tam ızgaradaki (T0'dan) çıkışı: (cikis_ts, cikis_fiyat, cikis_tipi, sure_saat)."""
    O, H, L, C, V = sl.izgaraya_yerlestir(d[:, :6], T0, int((d[-1, 0] - T0) // SAAT) + 1)
    tip = sl.KURAL_CIKIS[strateji]
    seri = sl.gostergeler(O, H, L, C, V)[sl.SINYAL_SERISI[tip]].tolist() if tip in sl.SINYAL_SERISI else None
    b, _, c, ctip = sl.cikis_simule(tip, O.tolist(), H.tolist(), L.tolist(), C.tolist(), seri, e)
    return T0 + (b + 1) * SAAT, c, ctip, float(b - e + 1)


def test_pencereden_uzun_kesintide_acik_ve_bekleyen_islemler_dogru_kapanir(tmp_path):
    """Makine PENCERE'den (1500 saat) uzun kapalı kaldı. Açık/bekleyen işlemli paritede geri doldurma kapsamdan
    (girişlerden) başlar: bekleyen işlemin giriş mumu çekilir (ATLANDI değil), bütün işlemler tam veriyle strateji_lab
    çıkışının aynısıyla kapanır. Eskiden hiç çekilmeyen saatler boşluk sayılıyor, işlemler kesinti sonrası ilk mumdan
    yanlış ZAMAN çıkışıyla kapanıyor, bekleyen işlem ATLANDI oluyordu. İşlemsiz parite yine en çok PENCERE saat."""
    n = 3400
    veri = {'BTC/USDT': sentetik(n, 1, s0=60000.0), 'X/USDT': sentetik(n, 2)}
    ex, h, m, ilerle = _makine_kur(veri, {s: {'quoteVolume': 1e9} for s in veri}, str(tmp_path / 'm.db'),
                                   T0 + 1600 * SAAT + 20 * SN)
    assert m.tur_guvenli()['durum'] == 'TAMAM'
    d = veri['X/USDT']
    islemler = [('YUKSEK_ISABET', 1590, 'ACIK'), ('TREND_DIP_RSI2', 1596, 'ACIK'), ('ERKEN_BIRIKIM', 1600, 'BEKLIYOR')]
    kimlik = [_islem_ekle(h, st, 'X/USDT', d, e, durum) for st, e, durum in islemler]
    ilerle(T0 + 3300 * SAAT + 20 * SN)                                           # 1700 saat kapalı
    ex.istekler.clear()
    assert m.tur_guvenli()['durum'] == 'TAMAM'
    assert min(x[1] for x in ex.istekler if x[0] == 'X/USDT') == T0 + 1600 * SAAT          # kapsamdan
    assert min(x[1] for x in ex.istekler if x[0] == 'BTC/USDT') == T0 + (3300 - hz.PENCERE) * SAAT
    hepsi = {x['id']: x for x in h.kagit_islemler(sembol='X/USDT')}
    for (st, e, _), i in zip(islemler, kimlik):
        x = hepsi[i]
        assert x['durum'] == 'KAPALI' and x['giris_fiyat'] == d[e, 1] * (1 + sl.SLIP_GIRIS), st
        assert (x['cikis_ts'], x['cikis_fiyat'], x['cikis_tipi'], x['sure_saat']) == _lab_cikis(d, st, e), st
    h.kapat()


def _adimli_seri(n=3200):
    """Fiyat adımlı (0.001) seri: kapanış SMA5'e matematiksel olarak eşit olabilir (pandas'ın kayan ortalaması son
    basamakta dizinin başladığı yere bağlı). İnceleme senaryosunun aynı üreteci (tohum 0, üçüncü seri)."""
    rng = np.random.default_rng(0)
    seriler = [np.round(p0 * np.exp(np.cumsum(rng.normal(0, sig, n))) / tk) * tk
               for p0, sig, tk in ((0.00001234, 0.004, 1e-8), (0.2345, 0.002, 1e-4), (1.234, 0.0015, 1e-3))]
    c = seriler[2]
    o = np.r_[c[0], c[:-1]]
    ts = T0 + SAAT * np.arange(n, dtype=np.int64)
    return _ham(ts, o, np.maximum(o, c), np.minimum(o, c), c, np.full(n, 1e6), np.random.default_rng(1))


def test_sinyal_cikisi_degerlendirildigi_turdan_bagimsiz(tmp_path):
    """Açık TREND_DIP_RSI2 işleminin çıkışı aynı saklı mumlarla her turda aynı karara varır: işlemin ızgarası (ve
    SMA5'i) girişe bağlı başlar. Eskiden her turun 1500 saatlik penceresi kullanılıyordu: 2167. turda çıkış yokken
    2168/2169. turlar 2168. mumda, 2170. tur (kesintiden sonra) geriye dönük 2167. mumda kapatıyordu."""
    d = _adimli_seri()
    h = _hafiza(tmp_path)
    h.mum_yaz('X/USDT', d)
    h.parite_yaz('X/USDT', kapsam_ts=int(d[-1, 0]) + SAAT, market_id='XUSDT')
    m = mk.Makine(SahteBorsa({}, T0), h, saat=lambda: T0)
    e = 2165
    islem = {'strateji': 'TREND_DIP_RSI2', 'bilesen': 'TREND_DIP_RSI2', 'sembol': 'X/USDT', 'giris_ts': int(d[e, 0]),
             'giris_fiyat': float(d[e, 1]) * (1 + sl.SLIP_GIRIS)}
    sonuc = {t: m._cikis(islem, int(d[t, 0]), int(d[t, 0]) + SAAT + 20 * SN) for t in range(e, e + 12)}
    ilk = next(t for t, r in sonuc.items() if r is not None)                    # çıkışın ilk görüldüğü tur
    assert all(r is None for t, r in sonuc.items() if t < ilk)
    assert all(r == sonuc[ilk] for t, r in sonuc.items() if t >= ilk)
    assert sonuc[ilk]['cikis_ts'] == int(d[ilk, 0]) + SAAT                        # kendi turunda, geriye dönük değil
    h.kapat()


def test_bekleyen_islem_giris_mumu_gelmeyen_paritede_atlanir(tmp_path):
    """İşlemi duran paritede (yeni mum yok, BadSymbol yok) BEKLIYOR işlem 48 saat bekler (çekim hatalıysa bekleme
    sürer), sonra ATLANDI olur ve parite korunmaz (eskiden sonsuza dek BEKLIYOR ve korunuyordu)."""
    n = 300
    d = sentetik(n, 3)
    veri = {'BTC/USDT': sentetik(n + 100, 1, s0=60000.0), 'ESKI/USDT': d}
    ex, h, m, ilerle = _makine_kur(veri, {'BTC/USDT': {'quoteVolume': 1e9}}, str(tmp_path / 'm.db'),
                                   T0 + n * SAAT + 20 * SN)
    m.toplayici.guncelle(['ESKI/USDT'], T0 + n * SAAT + 20 * SN)
    assert h.parite_oku('ESKI/USDT')['kapsam_ts'] == T0 + n * SAAT                 # 300. mum (giriş) henüz yok
    _islem_ekle(h, 'TREND_DIP_RSI2', 'ESKI/USDT', np.r_[d, np.r_[T0 + n * SAAT, d[-1, 1:]][None, :]], n,
                'BEKLIYOR')
    ilerle(T0 + (n + 1) * SAAT + 20 * SN)
    m.tur_guvenli()
    assert h.kagit_islemler(sembol='ESKI/USDT')[0]['durum'] == 'BEKLIYOR'
    ilerle(T0 + (n + 49) * SAAT + 20 * SN)                                       # 48 saat geçti ama çekim hatalı
    ex.hata_plani['ESKI/USDT'] = [ccxt.NetworkError('ağ')] * 4
    m.tur_guvenli()
    assert h.kagit_islemler(sembol='ESKI/USDT')[0]['durum'] == 'BEKLIYOR'
    ilerle(T0 + (n + 50) * SAAT + 20 * SN)
    r = m.tur_guvenli()
    assert h.kagit_islemler(sembol='ESKI/USDT')[0]['durum'] == 'ATLANDI' and r['ayrinti']['atlandi'] == 1
    assert 'ESKI/USDT' not in h.korunacak_pariteler(T0 + (n + 50) * SAAT + 20 * SN)
    h.kapat()


def test_islemi_durdurulan_paritede_acik_islem_veri_yok_ile_kapanir(tmp_path):
    """Binance BREAK (parite borsada duruyor, BadSymbol yok, yeni mum yok): açık işlem azami tutma süresinden 24 saat
    sonra son saklı kapanıştan VERI_YOK ile kapanır (eskiden sonsuza dek ACIK kalıyordu)."""
    n = 60
    d = sentetik(n, 3, sigma=0.0)                                                   # düz: stop tetiklenmez
    veri = {'BTC/USDT': sentetik(n + 300, 1, s0=60000.0), 'ESKI/USDT': d}
    ex, h, m, ilerle = _makine_kur(veri, {'BTC/USDT': {'quoteVolume': 1e9}}, str(tmp_path / 'm.db'),
                                   T0 + (n + 10) * SAAT + 20 * SN)
    h.mum_yaz('ESKI/USDT', d)
    h.parite_yaz('ESKI/USDT', kapsam_ts=T0 + n * SAAT, market_id='ESKIUSDT')
    _islem_ekle(h, 'ERKEN_BIRIKIM', 'ESKI/USDT', d, 10)
    m.tur_guvenli()
    ilerle(T0 + (10 + 168 + 23) * SAAT + 20 * SN)
    m.tur_guvenli()
    x = h.kagit_islemler(sembol='ESKI/USDT')[0]
    assert x['durum'] == 'ACIK' and h.parite_oku('ESKI/USDT')['durum'] == 'AKTIF'
    ilerle(T0 + (10 + 168 + 24) * SAAT + 20 * SN)
    m.tur_guvenli()
    x = h.kagit_islemler(sembol='ESKI/USDT')[0]
    c = float(d[-1, 4]) * (1 - sl.SLIP_ZAMAN)
    assert x['durum'] == 'KAPALI' and x['cikis_tipi'] == 'VERI_YOK' and x['cikis_fiyat'] == c
    assert x['cikis_ts'] == T0 + n * SAAT and x['sure_saat'] == n - 10
    h.kapat()


def test_yerel_saat_ileriyken_acik_karar_mumu_saklanmaz(tmp_path):
    """Yerel saat borsadan 30 sn ileri: karar mumu borsada hâlâ açık. Kısmi mum saklanmaz, kapsam onun saatinde kalır,
    borsa kapatınca doğru değerlerle saklanır (eskiden kısmi mum kalıcı saklanıyordu). Makine turu: karar mumu yok ->
    HATA + MUM_YOK ve SAAT uyarıları, 60 sn sonraki deneme TAMAM ve doğru değerler."""
    d = sentetik(1700, 21)
    yerel = int(d[1600, 0]) + 20 * SN
    ex = SahteBorsa({'A/USDT': d}, yerel - 30 * SN)
    h = _hafiza(tmp_path)
    t = hz.MumToplayici(ex, h)
    t.guncelle(['A/USDT'], yerel)
    assert h.son_mum_ts('A/USDT') == d[1598, 0] and h.parite_oku('A/USDT')['kapsam_ts'] == d[1599, 0]
    ex.simdi_ms = yerel + 30 * SN                                                   # borsa 1599'u kapattı
    t.guncelle(['A/USDT'], yerel + 60 * SN)
    np.testing.assert_array_equal(h.mumlar('A/USDT', 0, 2 ** 62), d[100:1600])
    assert h.parite_oku('A/USDT')['kapsam_ts'] == d[1600, 0]
    # borsanın akışı son mumdan sonrasını vermiyor ama kapanışının üzerinden KAPANIS_PAYI geçti: kapanmış sayılır
    ex.veri['A/USDT'] = d[:1602]
    ex.simdi_ms = int(d[1603, 0]) + 20 * SN
    t.guncelle(['A/USDT'], int(d[1603, 0]) + 20 * SN)
    assert h.son_mum_ts('A/USDT') == d[1601, 0] and h.parite_oku('A/USDT')['kapsam_ts'] == d[1602, 0]
    h.kapat()
    # makine turu
    veri = {'BTC/USDT': sentetik(400, 1, s0=60000.0), 'A/USDT': sentetik(400, 2)}
    tg = SahteTelegram()
    yerel = T0 + 300 * SAAT + 20 * SN
    ex, h, m, ilerle = _makine_kur(veri, {s: {'quoteVolume': 1e9} for s in veri}, str(tmp_path / 'm2.db'), yerel,
                                   telegram=tg)
    ex.simdi_ms = yerel - 30 * SN
    r = m.tur_guvenli()
    assert r['durum'] == 'HATA' and r['ayrinti']['neden'] == 'hiç mum alınamadı' and not r['ayrinti']['islem_izni']
    assert r['ayrinti']['saat_farki_ms'] == 30 * SN
    assert any('(SAAT)' in x for x in tg.mesajlar) and any('(MUM_YOK)' in x for x in tg.mesajlar)
    ilerle(yerel + 60 * SN)
    ex.simdi_ms = yerel + 30 * SN
    r = m.tur_guvenli()
    assert r['durum'] == 'TAMAM'
    for s, x in veri.items():
        np.testing.assert_array_equal(h.mumlar(s, 0, 2 ** 62), x[:300])
        assert h.adaylar(T0 + 299 * SAAT)[s == 'BTC/USDT']['ret1'] == x[299, 4] / x[298, 4] - 1
    h.kapat()


def test_surec_basladiktan_sonra_listelenen_parite_evrene_girer(tmp_path):
    """Yüklü piyasalarda olmayan parite ccxt'de eğiksiz anahtarla ('YENIUSDT') gelir: makine piyasaları yeniden
    yükler, ticker'ları yeniden çeker ve yeni pariteyi aynı turda evrene alır (eskiden yeniden başlatılana kadar hiç
    görmüyordu). Piyasalar ayrıca her UTC günün ilk turunda yenilenir."""
    adlar = ['BTC/USDT', 'A/USDT', 'YENI/USDT']
    veri = {s: sentetik(200, i + 1, s0=(60000.0 if i == 0 else 10.0)) for i, s in enumerate(adlar)}
    tickers = {'BTC/USDT': {'quoteVolume': 1e9}, 'A/USDT': {'quoteVolume': 5e8}, 'YENI/USDT': {'quoteVolume': 9e8}}
    simdi = T0 + 150 * SAAT + 20 * SN                                               # UTC günün 06:00 turu
    ex, h, m, ilerle = _makine_kur(veri, tickers, str(tmp_path / 'm.db'), simdi)
    m.tur_guvenli()
    yukleme = ex.yukleme
    assert 'YENI/USDT' in {x['sembol'] for x in h.adaylar(T0 + 149 * SAAT)}
    ex.gizli = {'YENI/USDT'}                                            # piyasa yüklemesinden sonra listelendi
    assert 'YENIUSDT' in ex.fetch_tickers()
    ilerle(simdi + SAAT)
    m.tur_guvenli()
    assert ex.yukleme == yukleme + 1
    satir = {x['sembol']: x for x in h.adaylar(T0 + 150 * SAAT)}
    assert set(satir) == set(adlar) and satir['YENI/USDT']['karar_mumu'] == 1
    assert satir['YENI/USDT']['evren_sira'] == 2
    ilerle(simdi + 2 * SAAT)
    m.tur_guvenli()
    assert ex.yukleme == yukleme + 1                                                # aynı gün, yeni kimlik yok
    ilerle(simdi + 18 * SAAT)                                                       # ertesi UTC günü
    m.tur_guvenli()
    assert ex.yukleme == yukleme + 2
    h.kapat()


def test_veritabani_yazilamazken_uyari_ve_gunluk_rapor_gider(tmp_path):
    """Disk dolu: her yazma 'database or disk is full'. Tur hatası uyarısı yine gider (6 saatte bir), günlük rapor
    günde bir kez gider ve yazma hatasını söyler; veritabanı düzelince yazılamayan tur kayıtları HATA olarak yazılır
    (KACIRILDI değil). Eskiden hiç uyarı gitmiyor, rapor her tur yeniden gönderiliyordu."""
    from contextlib import contextmanager
    veri = {s: sentetik(400, i + 1, s0=(60000.0 if i == 0 else 10.0)) for i, s in enumerate(['BTC/USDT', 'A/USDT'])}
    tg = SahteTelegram()
    ex, h, m, ilerle = _makine_kur(veri, {s: {'quoteVolume': 1e9} for s in veri}, str(tmp_path / 'm.db'),
                                   T0 + 290 * SAAT + 20 * SN, telegram=tg, rapor_saati_utc=0)
    for k in range(290, 293):
        ilerle(T0 + k * SAAT + 20 * SN)
        assert m.tur_guvenli()['durum'] == 'TAMAM'
        m.gunluk_rapor()
    n0 = len(tg.mesajlar)

    @contextmanager
    def dolu():
        raise sqlite3.OperationalError('database or disk is full')
        yield
    h.islem = dolu
    durumlar = []
    for k in range(293, 324):                                                       # 31 saat (UTC gün dönümü dahil)
        ilerle(T0 + k * SAAT + 20 * SN)
        durumlar.append(m.tur_guvenli()['durum'])
        m.gunluk_rapor()
    yeni = tg.mesajlar[n0:]
    assert set(durumlar) == {'HATA'}
    assert sum('UYARISI (TUR_HATASI)' in x for x in yeni) == 6                       # 293, 299, 305, 311, 317, 323
    rapor = [x for x in yeni if x.startswith('MAKİNE (')]
    assert len(rapor) == 1 and 'VERİTABANI YAZMA HATASI' in rapor[0] and 'disk is full' in rapor[0]
    del h.islem                                                                     # disk boşaldı
    ilerle(T0 + 323 * SAAT + 80 * SN)                                               # aynı saatin yeniden denemesi
    assert m.tur_guvenli()['durum'] == 'TAMAM'
    ilerle(T0 + 324 * SAAT + 20 * SN)
    assert m.tur_guvenli()['durum'] == 'TAMAM'
    durum = {r['ts']: r['durum'] for r in h.taramalar()}
    assert [durum[T0 + k * SAAT] for k in range(292, 322)] == ['HATA'] * 30
    assert durum[T0 + 322 * SAAT] == durum[T0 + 323 * SAAT] == 'TAMAM'           # bellekteki eski HATA ezmedi
    h.kapat()


def test_btc_ya_da_butun_karar_mumlari_yoksa_tur_hata_ve_yeniden_denenir(tmp_path, monkeypatch):
    """BTC'nin karar mumu alınamadı (diğerleri tamam): tur HATA, MUM_YOK uyarısı, yeni kâğıt işlem yok; 60 sn sonraki
    deneme (zamanında) TAMAM ve işlemleri açar. Borsa hiç mum vermiyor (boş yanıt, istisna yok): HATA, güncellenen 0.
    Eskiden ikisi de TAMAM yazılıyor, sinyaller BTC'siz hesaplanıyor ve uyarı gitmiyordu."""
    monkeypatch.setattr(mk, 'karar_sinyalleri', lambda s, rejim, i=-1: {'TREND_DIP_RSI2': 'TREND_DIP_RSI2'})
    veri = {s: sentetik(400, i + 1, s0=(60000.0 if i == 0 else 10.0)) for i, s in enumerate(['BTC/USDT', 'A/USDT',
                                                                                            'B/USDT'])}
    tg = SahteTelegram()
    simdi = T0 + 300 * SAAT + 20 * SN
    ex, h, m, ilerle = _makine_kur(veri, {s: {'quoteVolume': 1e9} for s in veri}, str(tmp_path / 'm.db'), simdi,
                                   telegram=tg)
    ex.hata_plani['BTC/USDT'] = [ccxt.RequestTimeout('zaman aşımı')] * 4
    r = m.tur_guvenli()
    assert r['durum'] == 'HATA' and r['ayrinti']['neden'] == 'BTC karar mumu yok' and not r['ayrinti']['islem_izni']
    assert not h.kagit_islemler() and any('(MUM_YOK)' in x for x in tg.mesajlar)
    assert m._yapilacak_mi(T0 + 299 * SAAT, simdi + 60 * SN)
    ilerle(simdi + 60 * SN)
    r = m.tur_guvenli()
    assert r['durum'] == 'TAMAM' and r['ayrinti']['islem_izni']
    assert {x['sembol'] for x in h.kagit_islemler('BEKLIYOR')} == set(veri)
    ilerle(simdi + SAAT)
    ex.publicGetKlines = lambda params: []
    r = m.tur_guvenli()
    assert r['durum'] == 'HATA' and r['ayrinti']['neden'] == 'hiç mum alınamadı' and r['guncellenen'] == 0
    h.kapat()


def test_dongu_veritabani_okuma_hatasinda_olmez(tmp_path):
    """calis(): turun dışındaki bir veritabanı okuması (karar saati denetimi) bir kez hata verir: süreç ölmez,
    DONGU_HATASI uyarısı gider, 60 sn sonra sürer ve sonraki saatler işlenir (eskiden calis istisnayla çıkıyordu)."""
    veri = {s: sentetik(400, i + 1, s0=(60000.0 if i == 0 else 10.0)) for i, s in enumerate(['BTC/USDT', 'A/USDT'])}
    tg = SahteTelegram()
    bitis = T0 + 306 * SAAT + 30 * 60 * SN
    durum = {}

    def uyku(sn):
        ilerle(durum['m'].saat() + int(sn * 1000))
        if durum['m'].saat() >= bitis:
            durum['m'].durdur()
    ex, h, m, ilerle = _makine_kur(veri, {s: {'quoteVolume': 1e9} for s in veri}, str(tmp_path / 'm.db'),
                                   T0 + 300 * SAAT + 25 * SN, telegram=tg, uyku=uyku)
    durum['m'] = m
    asil, sayac = h.sorgu, [0]

    def sorgu(sql, p=()):
        if sql.startswith('SELECT durum FROM tarama'):
            sayac[0] += 1
            if sayac[0] == 3:
                raise sqlite3.OperationalError('database is locked')
        return asil(sql, p)
    h.sorgu = sorgu
    m.calis()
    assert [r['ts'] for r in h.taramalar()] == [T0 + k * SAAT for k in range(299, 306)]
    assert all(r['durum'] == 'TAMAM' for r in h.taramalar())
    assert sum('(DONGU_HATASI)' in x for x in tg.mesajlar) == 1 and m.durdu
    h.kapat()


def test_basarisiz_uyari_hakki_harcamaz(tmp_path):
    """Telegram'a gönderilemeyen uyarı 6 saatlik hakkı harcamaz: 15 dk sonra yeniden denenir; başarılı uyarıdan
    sonra 6 saat yeni uyarı yok; yeniden başlatmada son başarılı uyarının zamanı veritabanından bilinir."""
    tg = SahteTelegram(sonuc=False)
    h = _hafiza(tmp_path)
    m = mk.Makine(SahteBorsa({}, T0), h, saat=lambda: T0, telegram=tg)
    assert m._uyar('MUM_YOK', 'a', T0) is False and len(tg.mesajlar) == 1
    assert m._uyar('MUM_YOK', 'a', T0 + 10 * 60 * SN) is False and len(tg.mesajlar) == 1      # 15 dk dolmadı
    tg.sonuc = True
    assert m._uyar('MUM_YOK', 'a', T0 + 15 * 60 * SN) is True and len(tg.mesajlar) == 2
    assert m._uyar('TUR_HATASI', 'b', T0 + 16 * 60 * SN) is True                            # türler bağımsız
    assert m._uyar('MUM_YOK', 'a', T0 + 6 * SAAT) is False
    assert m._uyar('MUM_YOK', 'a', T0 + 15 * 60 * SN + 6 * SAAT) is True
    m2 = mk.Makine(SahteBorsa({}, T0), h, saat=lambda: T0, telegram=tg)
    assert m2._uyar('MUM_YOK', 'a', T0 + 15 * 60 * SN + 7 * SAAT) is False
    assert m2._uyar('MUM_YOK', 'a', T0 + 15 * 60 * SN + 12 * SAAT) is True
    h.kapat()


def test_bozuk_veritabani_turkce_mesajla_2_doner(tmp_path, monkeypatch, capsys, gunluk_geri_al):
    db = str(tmp_path / 'makine.db')
    with open(db, 'wb') as f:
        f.write(b'bu bir sqlite dosyasi degil' * 100)
    monkeypatch.setattr(mk, 'telegram_ayarlari', lambda *a, **k: (None, None))
    monkeypatch.setattr(mk, 'borsa_kur', lambda: SahteBorsa({}, T0))
    for arg in (['--durum'], ['--rapor'], ['--tek-tur']):
        assert mk.main(arg + ['--db', db]) == 2, arg
        assert 'veritabanı bozuk ya da açılamıyor' in capsys.readouterr().out, arg
    with hz.TekKopya(db + '.lock'):                                                 # kilit bırakıldı
        pass


def test_istek_siniri_418_yeniden_denenmez_429_sonra_beklenir(tmp_path):
    """418 (IP yasağı): yeniden deneme yok, turun kalan pariteleri ve ticker isteği gönderilmez, bekleme bitince
    sürer. 429: 2/4/8 sn geri çekilerek denenir; tükenirse Retry-After kadar istek yok (eskiden ikisi de sıradan ağ
    hatası gibi parite başına 4 kez deneniyordu)."""
    veri = {s: sentetik(200, i) for i, s in enumerate(['A/USDT', 'B/USDT', 'C/USDT'])}
    saat = [int(veri['A/USDT'][150, 0]) + 20 * SN]
    ex = SahteBorsa(veri, saat[0])
    h = _hafiza(tmp_path, saat=lambda: saat[0])
    bekleme, uyku = _bekle_kaydi()
    t = hz.MumToplayici(ex, h, uyku=uyku)

    def ilerle(ms):
        saat[0] = ex.simdi_ms = ms
    ex.hata_plani['A/USDT'] = [ccxt.DDoSProtection('418 IP banned')]
    s = t.guncelle(list(veri), saat[0])
    assert list(s['hata']) == ['A/USDT'] and s['atlanan'] == ['B/USDT', 'C/USDT'] and s['istek'] == 1
    assert bekleme == [] and t.sinir_bitis_ms == saat[0] + hz.YASAK_BEKLEME_SN * 1000
    with pytest.raises(hz.IstekSiniri):
        t.cagir(ex.fetch_tickers)
    ilerle(saat[0] + hz.YASAK_BEKLEME_SN * 1000 - 1)
    s = t.guncelle(list(veri), saat[0])
    assert s['istek'] == 0 and s['atlanan'] == list(veri)
    ilerle(saat[0] + 1)
    assert set(t.guncelle(list(veri), saat[0])['yeni']) == set(veri)
    ex.hata_plani['B/USDT'] = [ccxt.RateLimitExceeded('429')] * 4
    ex.last_response_headers = {'Retry-After': '30'}
    ilerle(saat[0] + SAAT)
    s = t.guncelle(list(veri), saat[0])
    assert bekleme == [2, 4, 8] and list(s['yeni']) == ['A/USDT'] and list(s['hata']) == ['B/USDT']
    assert s['atlanan'] == ['C/USDT'] and t.sinir_bitis_ms == saat[0] + 30 * SN
    h.kapat()


def test_belirtec_bosluk_ve_kontrol_karakteriyle_de_maskelenir(tmp_path, gunluk_geri_al):
    """Ortam değişkenindeki belirtecin sonunda satır sonu var: değer temizlenir; günlükte kaçışlı (repr) ve URL kodlu
    biçimleri de maskelenir (eskiden http.client'ın InvalidURL iletisindeki '\\n'li biçim maskelenmiyordu)."""
    belirtec = '123456:GIZLI-govde\n'
    assert mk.telegram_ayarlari(str(tmp_path), ortam={'TELEGRAM_TOKEN': belirtec, 'TELEGRAM_CHAT_ID': ' 42 '}) == (
        '123456:GIZLI-govde', '42')
    akis = io.StringIO()
    mk.gunluk_kur(None, gizliler=[belirtec], akis=akis)
    mk.LOG.warning(f"InvalidURL: URL can't contain control characters. {('/bot' + belirtec + '/sendMessage')!r}")
    import urllib.parse
    mk.LOG.warning(f"istek: https://x/{urllib.parse.quote(belirtec, safe='')}")
    import http.client

    def acici(istek, timeout=None):
        raise http.client.InvalidURL(f"URL can't contain control characters. {istek.full_url!r}")
    assert mk.Telegram(belirtec, '42', acici=acici).gonder('x') is False
    cikti = akis.getvalue()
    assert 'GIZLI-govde' not in cikti and cikti.count('***') >= 3


def test_rapor_ilk_turun_actigi_islemi_de_sayar(tmp_path):
    """Günlük raporun 'açılan'ı son 24 turun açtıkları: penceredeki ilk tur (karar saati saat0 - 24 saat) girişi
    saat0 - 24 saat olan işlemi ACIK yapar; eskiden bu işlem hiçbir günün raporunda sayılmıyordu."""
    h = _hafiza(tmp_path)
    simdi = T0 + 10 * GUN + 6 * SAAT + 20 * SN
    saat0 = T0 + 10 * GUN + 6 * SAAT
    for i, giris in enumerate((saat0 - 25 * SAAT, saat0 - 24 * SAAT, saat0 - SAAT)):
        _kagit(h, 'BOT_VEKILI', f'S{i}/USDT', giris - SAAT, 'ACIK', giris_fiyat=1.0)
    assert "  Kâğıt işlem (20 USDT/işlem): açılan 2, kapanan 0, kapananların sonucu +0.00 USDT" in \
        mk.rapor_metni(h, simdi).split('\n')
    h.kapat()


def test_kesintide_evrenden_cikan_paritenin_satirlari_etiketlenir(tmp_path):
    """Parite evrendeyken makine durdu, kesintide hacmi düştü (evrenden çıktı): yeniden başlayınca son aday
    satırlarının etiketi için mumları yine çekilir ve satırlar etiketlenir; sonra korunmaz. Eskiden yalnız son 26
    saatte satırı olan parite korunuyordu: bu satırlar hiç etiketlenmiyordu (hacmi düşen paritelere karşı yanlılık)."""
    veri = {s: sentetik(400, i + 1, s0=(60000.0 if i == 0 else 10.0)) for i, s in enumerate(['BTC/USDT', 'A/USDT',
                                                                                            'D/USDT'])}
    tickers = {s: {'quoteVolume': 1e9} for s in veri}
    ex, h, m, ilerle = _makine_kur(veri, tickers, str(tmp_path / 'm.db'), T0 + 300 * SAAT + 20 * SN)
    for k in range(300, 306):
        ilerle(T0 + k * SAAT + 20 * SN)
        m.tur_guvenli()
    tickers['D/USDT'] = {'quoteVolume': 1e6}                                        # kesintide evrenden çıktı
    simdi = T0 + 336 * SAAT + 20 * SN                                               # 30 saat kapalı
    ilerle(simdi)
    m.tur_guvenli()
    e = h.sorgu("SELECT ts, ileri_24s FROM etiket WHERE sembol = 'D/USDT' ORDER BY ts")
    assert [r['ts'] for r in e] == [T0 + k * SAAT for k in range(299, 305)] and all(r['ileri_24s'] is not None
                                                                                   for r in e)
    assert 'D/USDT' not in h.korunacak_pariteler(simdi + SAAT)
    h.kapat()


def test_tanim_surumu_satirlara_yazilir_ve_degisince_kaydedilir(tmp_path):
    """Tanım sürümü (kaynak özetleri + parametreler) tanim tablosunda; aday, kâğıt işlem, etiket ve tur satırları
    kimliğini taşır. --evren değişince yeni tanım ve TANIM olayı; eski parametrelere dönünce eski kimlik."""
    veri = {s: sentetik(400, i + 1, s0=(60000.0 if i == 0 else 10.0)) for i, s in enumerate(['BTC/USDT', 'A/USDT'])}
    tickers = {s: {'quoteVolume': 1e9} for s in veri}
    ex, h, m, ilerle = _makine_kur(veri, tickers, str(tmp_path / 'm.db'), T0 + 300 * SAAT + 20 * SN, evren=5)
    for k in range(300, 326):
        ilerle(T0 + k * SAAT + 20 * SN)
        m.tur_guvenli()
    assert m.tanim_id == 1 and h.meta_oku('son_tanim_id') == '1'
    icerik = json.loads(h.sorgu('SELECT icerik FROM tanim WHERE id = 1')[0]['icerik'])
    assert icerik['ozellik_surumu'] == mk.OZELLIK_SURUMU and icerik['parametre']['evren'] == 5
    assert icerik['parametre']['sinyal_min_hacim'] == mk.SINYAL_MIN_HACIM and icerik['parametre']['ufuk'] == mo.UFUK
    assert set(icerik['kaynak']) == {'strateji_lab', 'v184_simulasyon', 'backfill_sinyaller', 'makine_ozellikleri',
                                     'hafiza', 'makine'} and all(icerik['kaynak'].values())
    for tablo in ('aday', 'etiket', 'tarama'):
        assert h.sorgu(f'SELECT DISTINCT tanim_id FROM {tablo}') == [{'tanim_id': 1}], tablo
    m2 = mk.Makine(ex, h, saat=m.saat, evren=6)
    assert m2.tanim_id == 2 and 'tanım değişti: 1 -> 2' in h.olaylar(tur='TANIM')[0]['mesaj']
    assert mk.Makine(ex, h, saat=m.saat, evren=5).tanim_id == 1 and len(h.olaylar(tur='TANIM')) == 2
    h.kapat()
