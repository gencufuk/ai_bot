# -*- coding: utf-8 -*-
"""makine (kâğıt modda piyasa tarayıcı) parçaları: hafıza (şema, göç, upsert, saklama süresi, tek kopya kilidi), ham
klines toplayıcı (ilk geçmiş, boşluk doldurma, yalnız kapanmış mum, kapsam, ağ hatası, BadSymbol), evren kuralları,
yeni mum/hacim özellikleri (nedensellik, desenler, hacim kalitesi), ileri etiketler, emir defteri özeti ve kayan
pencerenin strateji_lab göstergelerine etkisi. Borsa sahtedir (ağ yok); saat testte ilerletilir (gerçek bekleme yok)."""
import os
import sqlite3
import subprocess
import sys
import textwrap

import ccxt
import numpy as np
import pandas as pd
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _yol in (REPO, os.path.join(REPO, 'tools')):
    if _yol not in sys.path:
        sys.path.insert(0, _yol)
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
    """Binance'in herkese açık arayüzünün sahtesi. veri: {sembol: (n, 9)}; simdi_ms testte ilerletilir.
    publicGetKlines startTime/limit'e uyar ve yalnız simdi'de var olan mumları verir (açılışı <= simdi; açık mum kısmi
    değerlerle). hata_plani[sembol]: sırayla yükseltilecek hatalar (None = o istek başarılı). Emir, bakiye ve özel uç
    çağrıları AssertionError verir (makine asla çağırmamalı)."""

    def __init__(self, veri, simdi_ms, tickers=None):
        self.veri, self.simdi_ms, self.tickers = veri, int(simdi_ms), tickers
        self.markets = {s: {'id': s.replace('/', ''), 'symbol': s, 'spot': True, 'active': True} for s in veri}
        self._kimlik = {m['id']: s for s, m in self.markets.items()}
        self.istekler, self.hata_plani, self.yukleme = [], {}, 0
        self.gizli = set()                      # market()'te görünmeyen (henüz yüklenmemiş) semboller

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
        d = d[(d[:, 0] >= params['startTime']) & (d[:, 0] <= self.simdi_ms)][:params['limit']]
        return [kline_satiri(r, acik=r[0] + SAAT > self.simdi_ms) for r in d]

    def fetch_tickers(self):
        if self.tickers is not None:
            return self.tickers
        return {s: {'quoteVolume': float(d[d[:, 0] < self.simdi_ms][-24:, 6].sum())} for s, d in self.veri.items()}

    def fetch_order_book(self, sembol, limit=100):
        c = float(self.veri[sembol][self.veri[sembol][:, 0] < self.simdi_ms][-1, 4])
        return {'bids': [[c * (1 - 0.001 * k), 10.0] for k in range(1, limit + 1)],
                'asks': [[c * (1 + 0.001 * k), 10.0] for k in range(1, limit + 1)]}

    def _yasak(self, *a, **k):
        raise AssertionError('makine emir/bakiye/özel uç çağırdı')

    create_order = cancel_order = fetch_balance = fetch_my_trades = fetch_open_orders = _yasak

    def __getattr__(self, ad):
        if ad.startswith(('private', 'sapi', 'fapiPrivate')):
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
                + list(mo.YENI_OZELLIKLER) + ['sinyaller'])
    assert sorted(h._sutunlar['aday']) == sorted(beklenen) and len(h._sutunlar['aday']) == len(beklenen)
    assert len(hz.LAB_OZELLIKLERI) == 15 and not any(k.startswith('saat_') for k in hz.LAB_OZELLIKLERI)
    tip = {r['name']: r['type'] for r in h.sorgu('PRAGMA table_info(aday)')}
    assert all(tip[k] == 'INTEGER' for k in mo.DESENLER + ('radar_sira', 'evren_sira'))
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
    assert h.uyari_izinli('TUR_HATASI', T0)
    assert not h.uyari_izinli('TUR_HATASI', T0 + 6 * SAAT - 1)
    assert h.uyari_izinli('MUM_YOK', T0 + SAAT)                                      # türler bağımsız
    assert h.uyari_izinli('TUR_HATASI', T0 + 6 * SAAT)
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
    for ad in ('sniper/hafiza.py', 'sniper/makine_ozellikleri.py'):
        kaynak = open(os.path.join(REPO, ad), encoding='utf-8').read()
        for yasak in ('create_order', 'cancel_order', 'fetch_balance', 'apiKey', 'secret', 'BINANCE_API_KEY',
                      'BINANCE_SECRET_KEY', 'private'):
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
