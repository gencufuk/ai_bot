# -*- coding: utf-8 -*-
"""ai_bot.py VIP/alım/cüzdan senkronu entegrasyon testleri (fakeredis + sahte borsa).
Her senaryo V18.3'te yaşanabilen somut bir arızayı kapsar."""
import asyncio
import csv
import json
import math
import os
import time

import ccxt
import fakeredis
import pandas as pd
import pytest

for _k in ('BINANCE_API_KEY', 'BINANCE_SECRET_KEY', 'TELEGRAM_TOKEN', 'TELEGRAM_CHAT_ID'):
    os.environ.setdefault(_k, 'test')

import ai_bot  # noqa: E402

P = 'PORTFOY'
_ORIJINAL_SLEEP = asyncio.sleep


class SahteBorsa:
    def __init__(self):
        self.fiyatlar, self.bakiye, self.emirler, self.kayitli = {}, {'USDT': 1000.0}, [], {}
        self.hata_kuyrugu = []          # sıradaki create_* çağrılarında fırlatılacak istisnalar
        self.belirsiz_sonraki = False   # emri GERÇEKLEŞTİR, sonra RequestTimeout fırlat
        self.sonra_firlat = None        # emri GERÇEKLEŞTİR, sonra bu istisnayı fırlat (ör. -1006)
        self.sifir_dolum_sonraki = False  # EXPIRED, 0 dolum döndür (bakiye değişmez)
        self.sorgu_sayisi = 0
        self.kotu_semboller = set()
        self.ohlcv = {}
        self.ticker_cagri = 0
        self.bakiye_cagri = 0

    def amount_to_precision(self, sym, amt):
        v = math.floor(float(amt) * 1000 + 1e-9) / 1000
        if v <= 0:   # ccxt davranışı: sıfıra yuvarlanan miktar InvalidOrder fırlatır
            raise ccxt.InvalidOrder(f'amount of {sym} must be greater than minimum amount precision')
        return f"{v:.3f}"

    async def fetch_tickers(self, symbols=None):
        self.ticker_cagri += 1
        if symbols and self.kotu_semboller & set(symbols):
            raise ccxt.BadSymbol('binance {"code":-1121,"msg":"Invalid symbol."}')
        return {s: {'last': p} for s, p in self.fiyatlar.items() if symbols is None or s in symbols}

    async def fetch_ticker(self, s):
        if s in self.kotu_semboller:
            raise ccxt.BadSymbol('Invalid symbol.')
        return {'last': self.fiyatlar[s]}

    async def fetch_balance(self):
        self.bakiye_cagri += 1
        b = {'free': dict(self.bakiye), 'used': {c: 0.0 for c in self.bakiye}, 'total': dict(self.bakiye)}
        b.update({c: {'free': v, 'used': 0.0, 'total': v} for c, v in self.bakiye.items()})
        return b

    def _emir(self, sym, taraf, amount, params):
        if self.hata_kuyrugu:
            raise self.hata_kuyrugu.pop(0)
        if self.sifir_dolum_sonraki:
            self.sifir_dolum_sonraki = False
            return {'id': 'x', 'symbol': sym, 'side': taraf, 'amount': float(amount), 'filled': 0.0,
                    'average': None, 'cost': 0.0, 'status': 'expired', 'fees': []}
        coin, amount, fiyat = sym.split('/')[0], float(amount), self.fiyatlar[sym]
        fees = []
        if taraf == 'sell':
            if amount > self.bakiye.get(coin, 0) + 1e-12:
                raise ccxt.InsufficientFunds('Account has insufficient balance')
            self.bakiye[coin] -= amount
            self.bakiye['USDT'] += amount * fiyat
        else:
            ucret = amount * 0.001      # komisyon alınan coin'den kesilir
            self.bakiye[coin] = self.bakiye.get(coin, 0) + amount - ucret
            self.bakiye['USDT'] -= amount * fiyat
            fees = [{'currency': coin, 'cost': ucret}]
        o = {'id': str(len(self.emirler)), 'clientOrderId': (params or {}).get('newClientOrderId'), 'symbol': sym,
             'side': taraf, 'amount': amount, 'filled': amount, 'average': fiyat, 'cost': amount * fiyat,
             'status': 'closed', 'fees': fees}
        self.emirler.append(o)
        self.kayitli[o['clientOrderId']] = o
        if self.belirsiz_sonraki:
            self.belirsiz_sonraki = False
            raise ccxt.RequestTimeout('binance POST https://api.binance.com/api/v3/order timed out')
        if self.sonra_firlat is not None:
            e, self.sonra_firlat = self.sonra_firlat, None
            raise e
        return o

    async def create_market_sell_order(self, sym, amount, params=None):
        return self._emir(sym, 'sell', amount, params)

    async def create_market_buy_order(self, sym, amount, params=None):
        return self._emir(sym, 'buy', amount, params)

    async def fetch_order(self, id, sym, params=None):
        self.sorgu_sayisi += 1
        cid = (params or {}).get('origClientOrderId')
        if cid in self.kayitli:
            return self.kayitli[cid]
        raise ccxt.OrderNotFound('Order does not exist.')

    async def fetch_ohlcv(self, sym, tf, limit=100):
        return self.ohlcv.get((sym, tf), [])[-limit:]

    async def fetch_order_book(self, sym, limit=5):
        p = self.fiyatlar[sym]
        return {'bids': [[p * 0.999, 1000]], 'asks': [[p * 1.001, 1000]]}


@pytest.fixture
def ortam(tmp_path, monkeypatch):
    borsa, db = SahteBorsa(), fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(ai_bot, 'exchange', borsa)
    monkeypatch.setattr(ai_bot, 'db', db)
    monkeypatch.setattr(ai_bot, 'BASE_DIR', str(tmp_path))
    monkeypatch.setattr(ai_bot, 'REJIM', 'TREND')
    monkeypatch.setattr(ai_bot, '_SON_UYARI', {})

    async def hizli_sleep(t=0, *a, **k):
        await _ORIJINAL_SLEEP(0)
    monkeypatch.setattr(asyncio, 'sleep', hizli_sleep)
    return borsa, db, tmp_path


def calistir(coro_fn):
    async def ana():
        ai_bot.ANA_LOOP = asyncio.get_running_loop()
        ai_bot.BILDIRIM_KUYRUGU = asyncio.Queue()
        sonuc = await coro_fn()
        for _ in range(5):
            await _ORIJINAL_SLEEP(0)
        mesajlar = []
        while not ai_bot.BILDIRIM_KUYRUGU.empty():
            mesajlar.append(ai_bot.BILDIRIM_KUYRUGU.get_nowait())
        return sonuc, mesajlar
    try:
        return asyncio.run(ana())
    finally:
        ai_bot.ANA_LOOP = ai_bot.BILDIRIM_KUYRUGU = None


def pozisyon(db, sym, giris, *, adet=None, maliyet=20.0, max_kar=0.0, half=False, yas_sn=3600,
             atr=1.5, whale=False):
    db.hset(f'{P}:islem_listesi', sym, str(giris))
    db.hset(f'{P}:islem_miktarlari', sym, str(maliyet))
    db.hset(f'{P}:max_karlar', sym, str(max_kar))
    db.hset(f'{P}:giris_zamanlari', sym, str(time.time() - yas_sn))
    db.hset(f'{P}:ai_data', sym, json.dumps({'atr_pct': atr, 'is_whale': whale, 'signal': 'MSB', 'rsi': 70.0,
                                              'vol_ratio': 3.0, 'pump_3s': 4.0}))
    if half:
        db.hset(f'{P}:half_sold', sym, '1')
    if adet is not None:
        db.hset(f'{P}:adetler', sym, str(adet))


def satirlar(yol):
    with open(yol, encoding='utf-8') as f:
        return list(csv.DictReader(f))


def test_zehirli_pozisyon_diger_stoplari_durduramaz(ortam):
    """V18.3: bakiyesi sıfırlanmış A pozisyonunda amount_to_precision(0) InvalidOrder fırlatıyor,
    istisna try DIŞINDA olduğu için tüm tur çöküyor ve B'nin stop-loss'u hiç çalışmıyordu."""
    borsa, db, _ = ortam
    borsa.fiyatlar = {'AAA/USDT': 0.9, 'BBB/USDT': 0.9}           # ikisi de -%10: stop
    borsa.bakiye.update({'AAA': 0.0, 'BBB': 20.0})                 # A elle satılmış / çift satış
    pozisyon(db, 'AAA/USDT', 1.0, adet=20.0)
    pozisyon(db, 'BBB/USDT', 1.0, adet=20.0)
    _, mesajlar = calistir(ai_bot.vip_turu)
    assert [o['symbol'] for o in borsa.emirler] == ['BBB/USDT']    # B satıldı
    assert db.hlen(f'{P}:islem_listesi') == 0                      # A takipten çıkarıldı, B kapandı
    assert any('AAA/USDT TAKİPTEN ÇIKARILDI' in m for m in mesajlar)
    assert any('STOP LOSS' in m and 'BBB/USDT' in m for m in mesajlar)


def test_satis_takip_edilen_adedi_asmaz(ortam):
    """V18.3 cüzdandaki TÜM serbest bakiyeyi satıyordu; elle tutulan 50 adet de satılırdı."""
    borsa, db, _ = ortam
    borsa.fiyatlar = {'CCC/USDT': 0.95}
    borsa.bakiye['CCC'] = 70.0                                     # 20 bot + 50 elle
    pozisyon(db, 'CCC/USDT', 1.0, adet=20.0)
    calistir(ai_bot.vip_turu)
    assert len(borsa.emirler) == 1 and borsa.emirler[0]['amount'] == pytest.approx(20.0)
    assert borsa.bakiye['CCC'] == pytest.approx(50.0)


def test_legacy_pozisyon_adetsiz_eski_davranis(ortam):
    borsa, db, _ = ortam
    borsa.fiyatlar = {'LEG/USDT': 0.95}
    borsa.bakiye['LEG'] = 19.98
    pozisyon(db, 'LEG/USDT', 1.0)                                  # adetler alanı yok (V18.3'te açılmış)
    calistir(ai_bot.vip_turu)
    assert borsa.emirler[0]['amount'] == pytest.approx(19.98)


def test_kismi_kar_durum_ve_csv(ortam):
    borsa, db, tmp = ortam
    borsa.fiyatlar = {'DDD/USDT': 1.036}                           # max %5, şimdi ~%3.4 net: kısmi
    borsa.bakiye['DDD'] = 20.0
    pozisyon(db, 'DDD/USDT', 1.0, adet=20.0, max_kar=0.05)
    calistir(ai_bot.vip_turu)
    assert borsa.emirler[0]['amount'] == pytest.approx(10.0)
    assert db.hget(f'{P}:half_sold', 'DDD/USDT') == '1'
    assert float(db.hget(f'{P}:islem_miktarlari', 'DDD/USDT')) == pytest.approx(10.0)
    assert float(db.hget(f'{P}:adetler', 'DDD/USDT')) == pytest.approx(10.0)
    assert not (tmp / 'core_islem_verileri.csv').exists()          # tek işlem kaydı: V1 artık yazılmıyor
    v2 = satirlar(tmp / 'core_islem_verileri_v2.csv')
    assert v2[0]['Cikis_Tipi'] == 'DİNAMİK KISMİ KÂR' and all(k in v2[0] for k in ai_bot.V1_COLUMNS)
    assert v2[0]['Pozisyon_Id'].startswith('DDD/USDT|') and v2[0]['Pump_3s'] == '4.0'
    assert float(v2[0]['Sure_Saat']) == pytest.approx(1.0, abs=0.02)


def test_belirsiz_ag_hatasinda_cift_satis_yok(ortam):
    """Emir borsada gerçekleşip cevap zaman aşımına uğrarsa V18.3 satışı başarısız sayıp
    bir sonraki turda KALAN bakiyenin yarısını tekrar satıyordu."""
    borsa, db, _ = ortam
    borsa.fiyatlar = {'EEE/USDT': 1.036}
    borsa.bakiye['EEE'] = 20.0
    pozisyon(db, 'EEE/USDT', 1.0, adet=20.0, max_kar=0.05)
    borsa.belirsiz_sonraki = True
    calistir(ai_bot.vip_turu)
    assert len(borsa.emirler) == 1
    assert db.hget(f'{P}:half_sold', 'EEE/USDT') == '1'            # teyit edildi, durum işlendi
    calistir(ai_bot.vip_turu)                                       # sonraki tur: tekrar satış YOK
    assert len(borsa.emirler) == 1


def test_belirsiz_hata_emir_borsada_yoksa_basarisiz_sayilir(ortam):
    borsa, db, _ = ortam
    borsa.fiyatlar = {'QQQ/USDT': 1.036}
    borsa.bakiye['QQQ'] = 20.0
    pozisyon(db, 'QQQ/USDT', 1.0, adet=20.0, max_kar=0.05)
    borsa.hata_kuyrugu = [ccxt.RequestTimeout('timed out')]         # emir borsaya HİÇ ulaşmadı
    calistir(ai_bot.vip_turu)
    assert borsa.emirler == [] and not db.hexists(f'{P}:half_sold', 'QQQ/USDT')
    calistir(ai_bot.vip_turu)                                        # sonraki turda normal satış
    assert len(borsa.emirler) == 1 and db.hget(f'{P}:half_sold', 'QQQ/USDT') == '1'


def test_takip_adedi_sifirsa_cuzdanin_tamamina_dusulmez(ortam):
    borsa, db, _ = ortam
    borsa.fiyatlar = {'RRR/USDT': 0.9}
    borsa.bakiye['RRR'] = 50.0                                       # tamamı elle tutulan bakiye
    pozisyon(db, 'RRR/USDT', 1.0, adet=0.0)
    calistir(ai_bot.vip_turu)
    assert borsa.emirler == [] and borsa.bakiye['RRR'] == 50.0
    assert not db.hexists(f'{P}:islem_listesi', 'RRR/USDT')


def test_yarim_satis_hatasi_stopu_engellemez(ortam):
    """V18.3: kısmi satış hata verince 'continue' ile tam çıkış kontrolü hiç yapılmıyordu."""
    borsa, db, _ = ortam
    borsa.fiyatlar = {'FFF/USDT': 1.0}                              # max %5'ti, şimdi ~-0.2%: kısmi + tam çıkış
    borsa.bakiye['FFF'] = 20.0
    pozisyon(db, 'FFF/USDT', 1.0, adet=20.0, max_kar=0.05)
    borsa.hata_kuyrugu = [ccxt.InvalidOrder('Filter failure: LOT_SIZE')]
    _, mesajlar = calistir(ai_bot.vip_turu)
    assert len(borsa.emirler) == 1 and borsa.emirler[0]['amount'] == pytest.approx(20.0)
    assert not db.hexists(f'{P}:islem_listesi', 'FFF/USDT')
    assert any('TREND TAKİPLİ ÇIKIŞ' in m for m in mesajlar)


def test_toplu_ticker_hatasi_tum_stoplari_dusurmez(ortam):
    borsa, db, _ = ortam
    borsa.fiyatlar = {'GGG/USDT': 0.9, 'DEAD/USDT': 1.0}
    borsa.bakiye.update({'GGG': 20.0, 'DEAD': 20.0})
    borsa.kotu_semboller = {'DEAD/USDT'}
    pozisyon(db, 'GGG/USDT', 1.0, adet=20.0)
    pozisyon(db, 'DEAD/USDT', 1.0, adet=20.0)
    _, mesajlar = calistir(ai_bot.vip_turu)
    assert [o['symbol'] for o in borsa.emirler] == ['GGG/USDT']
    assert any('DEAD/USDT fiyatı alınamıyor' in m for m in mesajlar)


def test_zaman_asimi_uzatmasi_giris_zamanini_bozmaz(ortam):
    borsa, db, _ = ortam
    borsa.fiyatlar = {'HHH/USDT': 1.008}                            # net ~%0.6 >= %0.5: uzat
    borsa.bakiye['HHH'] = 20.0
    pozisyon(db, 'HHH/USDT', 1.0, adet=20.0, yas_sn=4.2 * 3600)
    giris_once = db.hget(f'{P}:giris_zamanlari', 'HHH/USDT')
    calistir(ai_bot.vip_turu)
    assert borsa.emirler == []
    assert db.hget(f'{P}:giris_zamanlari', 'HHH/USDT') == giris_once
    assert float(db.hget(f'{P}:zaman_ref', 'HHH/USDT')) == pytest.approx(time.time(), abs=5)


def test_kilit_yarim_satistan_sonra_ezilmez(ortam):
    borsa, db, _ = ortam
    borsa.fiyatlar = {'III/USDT': 1.0105}                           # net ~%0.85 < kilit %1.0
    borsa.bakiye['III'] = 10.0
    pozisyon(db, 'III/USDT', 1.0, adet=10.0, maliyet=10.0, max_kar=0.052, half=True, atr=2.8)
    _, mesajlar = calistir(ai_bot.vip_turu)
    assert len(borsa.emirler) == 1                                   # V18.3 burada +%0.5'e kadar beklerdi
    assert any('GÜVENLİ ÇIKIŞ' in m for m in mesajlar)


def test_min_notional_altinda_takili_kalmaz(ortam):
    borsa, db, _ = ortam
    borsa.fiyatlar = {'JJJ/USDT': 0.5}
    borsa.bakiye['JJJ'] = 8.0                                        # 4 USDT: satılamaz
    pozisyon(db, 'JJJ/USDT', 1.0, adet=8.0)
    _, mesajlar = calistir(ai_bot.vip_turu)
    assert borsa.emirler == [] and not db.hexists(f'{P}:islem_listesi', 'JJJ/USDT')
    assert any('TAKİPTEN ÇIKARILDI' in m for m in mesajlar)


def test_taze_pozisyon_bakiye_gecikmesinde_kapatilmaz(ortam):
    borsa, db, _ = ortam
    borsa.fiyatlar = {'KKK/USDT': 0.9}
    borsa.bakiye['KKK'] = 0.0                                        # alım henüz bakiyeye yansımadı
    pozisyon(db, 'KKK/USDT', 1.0, adet=20.0, yas_sn=5)
    calistir(ai_bot.vip_turu)
    assert db.hexists(f'{P}:islem_listesi', 'KKK/USDT')


def test_alim_atomik_yazar_ve_eski_alanlari_temizler(ortam):
    borsa, db, _ = ortam
    borsa.fiyatlar = {'LLL/USDT': 2.0}
    db.hset(f'{P}:half_sold', 'LLL/USDT', '1')                      # çökmüş eski çalışmadan kalıntı
    db.hset(f'{P}:zaman_ref', 'LLL/USDT', '123')
    metrics = {'fiyat': 2.0, 'atr_pct': 1.2, 'is_whale': False, 'signal': 'MSB', 'ai_score': None}
    alindi, mesajlar = calistir(lambda: ai_bot._alim_yap('LLL/USDT', metrics, 20.0))
    assert alindi and borsa.emirler[0]['side'] == 'buy'
    assert not db.hexists(f'{P}:half_sold', 'LLL/USDT') and not db.hexists(f'{P}:zaman_ref', 'LLL/USDT')
    miktar = borsa.emirler[0]['amount']
    assert float(db.hget(f'{P}:adetler', 'LLL/USDT')) == pytest.approx(miktar * 0.999)
    assert any('YENİ POZİSYON' in m for m in mesajlar)


def test_cuzdan_senkronu_taze_pozisyonu_silmez_eskiyi_temizler(ortam):
    borsa, db, _ = ortam
    borsa.fiyatlar = {'MMM/USDT': 1.0, 'NNN/USDT': 1.0, 'OOO/USDT': 2.0}
    ai_bot.RAW_TICKERS = {s: {'last': p} for s, p in borsa.fiyatlar.items()}
    borsa.bakiye.update({'MMM': 0.0, 'NNN': 0.0, 'OOO': 30.0})
    pozisyon(db, 'MMM/USDT', 1.0, yas_sn=30)                         # taze: bakiye gecikmeli
    pozisyon(db, 'NNN/USDT', 1.0, yas_sn=3600)                       # eski: gerçekten yok
    db.hset(f'{P}:half_sold', 'OOO/USDT', '1')                      # sahipsiz bakiyenin eski kalıntısı
    try:
        sonuc, mesajlar = calistir(lambda: ai_bot.check_wallet_sync(None))
    finally:
        ai_bot.RAW_TICKERS = {}
    assert sonuc is True
    assert db.hexists(f'{P}:islem_listesi', 'MMM/USDT') and not db.hexists(f'{P}:islem_listesi', 'NNN/USDT')
    assert db.hget(f'{P}:islem_listesi', 'OOO/USDT') == '2.0' and not db.hexists(f'{P}:half_sold', 'OOO/USDT')
    assert any('SAHİPSİZ BAKİYE' in m for m in mesajlar)


def test_manuel_coin_sahiplenilmez(ortam, monkeypatch):
    borsa, db, _ = ortam
    monkeypatch.setattr(ai_bot, 'MANUEL_COINLER', {'ETH'})
    ai_bot.RAW_TICKERS = {'ETH/USDT': {'last': 3000.0}}
    borsa.bakiye['ETH'] = 1.0
    try:
        calistir(lambda: ai_bot.check_wallet_sync(None))
    finally:
        ai_bot.RAW_TICKERS = {}
    assert not db.hexists(f'{P}:islem_listesi', 'ETH/USDT')


def test_shadow_kaydi_mevcut_dosyayi_yeni_kolonlarla_genisletir(ortam):
    _, db, tmp = ortam
    eski_kolonlar = ai_bot.SHADOW_COLUMNS[:25]
    with open(tmp / 'shadow_sinyaller.csv', 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(eski_kolonlar)
        w.writerow(['1'] + ['x'] * 24)
    m = {'signal': 'MSB', 'fiyat': 1.0, 'rsi': 60.0, 'vol_ratio': 3.0, 'atr_pct': 1.0, 'mum_ilerleme': 0.4}
    calistir(lambda: ai_bot.save_shadow_to_csv('PPP/USDT', 'REJIM_YATAY', m))
    df = pd.read_csv(tmp / 'shadow_sinyaller.csv')
    assert list(df.columns[:25]) == eski_kolonlar and 'Mum_Ilerleme' in df.columns
    assert len(df) == 2 and df.loc[1, 'Mum_Ilerleme'] == 0.4 and df.loc[0, 'Sembol'] == 'x'


# ------------------------------------------------------------------------------------------
# Bağımsız review bulguları (regresyon)
# ------------------------------------------------------------------------------------------
BILINMEYEN = 'binance {"code":-1006,"msg":"An unexpected response was received from the message bus. Execution status unknown."}'


def test_1006_durumu_bilinmeyen_alim_dolmussa_bulunur_tek_alim(ortam):
    """-1006 ccxt'de OperationFailed (NetworkError'ın ÜST sınıfı): eskiden kesin hata sayılıyor,
    dolmuş alım kaydedilmiyor ve sonraki radar turunda aynı coin tekrar alınıyordu."""
    borsa, db, _ = ortam
    borsa.fiyatlar = {'UNK/USDT': 2.0}
    borsa.sonra_firlat = ccxt.OperationFailed(BILINMEYEN)
    metrics = {'fiyat': 2.0, 'atr_pct': 1.2, 'is_whale': False, 'signal': 'MSB', 'ai_score': None}
    alindi, _ = calistir(lambda: ai_bot._alim_yap('UNK/USDT', metrics, 20.0))
    assert alindi and len(borsa.emirler) == 1 and borsa.sorgu_sayisi >= 1
    assert db.hexists(f'{P}:islem_listesi', 'UNK/USDT')
    assert float(db.hget(f'{P}:adetler', 'UNK/USDT')) == pytest.approx(borsa.bakiye['UNK'])
    assert not db.exists(f'{P}:bekleyen_alim:UNK/USDT')


def test_yarim_cevap_exchangeerror_de_belirsiz_sayilir(ortam):
    borsa, db, _ = ortam
    borsa.fiyatlar = {'PAY/USDT': 1.036}
    borsa.bakiye['PAY'] = 20.0
    pozisyon(db, 'PAY/USDT', 1.0, adet=20.0, max_kar=0.05)
    borsa.sonra_firlat = ccxt.ExchangeError('aiohttp ClientPayloadError: Response payload is not completed')
    calistir(ai_bot.vip_turu)
    assert len(borsa.emirler) == 1 and db.hget(f'{P}:half_sold', 'PAY/USDT') == '1'
    calistir(ai_bot.vip_turu)
    assert len(borsa.emirler) == 1          # ikinci (çift) yarım satış yok


def test_teyit_edilemeyen_alim_cooldown_ve_uyari(ortam):
    borsa, db, _ = ortam
    borsa.fiyatlar = {'NOK/USDT': 2.0}
    borsa.hata_kuyrugu = [ccxt.OperationFailed(BILINMEYEN)]      # emir borsaya hiç ulaşmadı
    metrics = {'fiyat': 2.0, 'atr_pct': 1.2, 'is_whale': False, 'signal': 'MSB', 'ai_score': None}
    alindi, mesajlar = calistir(lambda: ai_bot._alim_yap('NOK/USDT', metrics, 20.0))
    assert alindi is True                    # bu turda ikinci alım yapılmasın
    assert borsa.emirler == [] and not db.hexists(f'{P}:islem_listesi', 'NOK/USDT')
    assert float(db.hget(f'{P}:cooldowns', 'NOK/USDT')) > time.time() + 3000
    assert any('ALIM SONUCU BELİRSİZ' in m for m in mesajlar)


def test_kesin_ret_sorgulanmaz(ortam):
    borsa, db, _ = ortam
    borsa.fiyatlar = {'INS/USDT': 2.0}
    borsa.hata_kuyrugu = [ccxt.InsufficientFunds('Account has insufficient balance')]
    metrics = {'fiyat': 2.0, 'atr_pct': 1.2, 'is_whale': False, 'signal': 'MSB', 'ai_score': None}
    alindi, _ = calistir(lambda: ai_bot._alim_yap('INS/USDT', metrics, 20.0))
    assert alindi is False and borsa.sorgu_sayisi == 0
    assert not db.hexists(f'{P}:cooldowns', 'INS/USDT')


def test_sifir_dolumlu_market_emri_pozisyonu_kapatmaz(ortam):
    borsa, db, _ = ortam
    borsa.fiyatlar = {'EXP/USDT': 0.9}
    borsa.bakiye['EXP'] = 20.0
    pozisyon(db, 'EXP/USDT', 1.0, adet=20.0)
    borsa.sifir_dolum_sonraki = True
    calistir(ai_bot.vip_turu)
    assert db.hexists(f'{P}:islem_listesi', 'EXP/USDT') and borsa.bakiye['EXP'] == 20.0
    calistir(ai_bot.vip_turu)                # sonraki tur normal satış
    assert not db.hexists(f'{P}:islem_listesi', 'EXP/USDT') and borsa.bakiye['EXP'] == 0.0


def test_manuel_coin_eskiden_sahiplenilmis_pozisyon_asla_satilmaz(ortam, monkeypatch):
    """V18.3 cüzdan senkronu ETH'yi (adetsiz) sahiplenmişti; kullanıcı sonra MANUEL_COINLER=ETH ekliyor."""
    borsa, db, _ = ortam
    monkeypatch.setattr(ai_bot, 'MANUEL_COINLER', {'ETH'})
    borsa.fiyatlar = {'ETH/USDT': 2880.0}
    borsa.bakiye['ETH'] = 2.0
    pozisyon(db, 'ETH/USDT', 3000.0, maliyet=6000.0)             # -%4: stop koşulu
    _, mesajlar = calistir(ai_bot.vip_turu)
    assert borsa.emirler == [] and borsa.bakiye['ETH'] == 2.0
    assert not db.hexists(f'{P}:islem_listesi', 'ETH/USDT')
    assert any('MANUEL_COINLER' in m for m in mesajlar)
    with pytest.raises(ccxt.InvalidOrder):                       # tek geçiş noktası da satmayı reddeder
        calistir(lambda: ai_bot._guvenli_market_emri('ETH/USDT', 'sell', 1.0))


def test_bekleyen_alim_cuzdan_senkronunda_sahiplenilmez(ortam):
    borsa, db, _ = ortam
    ai_bot.RAW_TICKERS = {'PND/USDT': {'last': 2.0}}
    borsa.bakiye['PND'] = 10.0                                    # alım borsada gerçekleşti, cevap bekleniyor
    db.set(f'{P}:bekleyen_alim:PND/USDT', '1', ex=300)
    try:
        _, mesajlar = calistir(lambda: ai_bot.check_wallet_sync(None))
    finally:
        ai_bot.RAW_TICKERS = {}
    assert not db.hexists(f'{P}:islem_listesi', 'PND/USDT') and not any('SAHİPSİZ' in m for m in mesajlar)


def test_canli_engelleme_orani_hedeften_saparsa_uyarir(ortam, monkeypatch):
    class Yuva:
        kart = {'hedef_engelleme_orani': 0.3}
    monkeypatch.setattr(ai_bot, 'CORE_YUVA', Yuva())
    ai_bot.AI_KARARLARI.clear()

    async def besle():
        for i in range(40):
            ai_bot._engelleme_orani_izle(i % 10 != 0)             # %90 engelleme
    _, mesajlar = calistir(besle)
    ai_bot.AI_KARARLARI.clear()
    assert any('engelleme oranı %90' in m for m in mesajlar)


def test_stop_satisi_bakiye_sorgusu_beklemeden_takip_adedini_satar(ortam):
    """Stopta her saniye fiyat demek: takip edilen adet biliniyorsa satıştan önce bakiye sorulmaz."""
    borsa, db, _ = ortam
    borsa.fiyatlar = {'HZL/USDT': 0.95}                             # -%5: stop
    borsa.bakiye['HZL'] = 20.0
    pozisyon(db, 'HZL/USDT', 1.0, adet=20.0)
    _, mesajlar = calistir(ai_bot.vip_turu)
    assert len(borsa.emirler) == 1 and borsa.emirler[0]['amount'] == pytest.approx(20.0)
    assert borsa.bakiye_cagri == 0
    assert not db.hexists(f'{P}:islem_listesi', 'HZL/USDT') and any('STOP LOSS' in m for m in mesajlar)


def test_hizli_satis_reddedilirse_bakiyedeki_miktar_satilir(ortam):
    """Takip adedi bakiyeden fazlaysa (ör. komisyon coin'den kesildi) borsa reddeder: bakiye sorgulu yol."""
    borsa, db, _ = ortam
    borsa.fiyatlar = {'KSR/USDT': 0.95}
    borsa.bakiye['KSR'] = 19.9
    pozisyon(db, 'KSR/USDT', 1.0, adet=20.0)
    _, mesajlar = calistir(ai_bot.vip_turu)
    assert len(borsa.emirler) == 1 and borsa.emirler[0]['amount'] == pytest.approx(19.9)
    assert borsa.bakiye_cagri == 1 and borsa.bakiye['KSR'] == pytest.approx(0.0)
    assert any('STOP LOSS' in m for m in mesajlar)


def test_hizli_satis_belirsizse_ayni_turda_ikinci_satis_denenmez(ortam):
    borsa, db, _ = ortam
    borsa.fiyatlar = {'BLR/USDT': 0.95}
    borsa.bakiye['BLR'] = 20.0
    pozisyon(db, 'BLR/USDT', 1.0, adet=20.0)
    borsa.hata_kuyrugu = [ccxt.NetworkError('bağlantı koptu')]      # emir borsaya ulaşmadı, sorgu da bulamaz
    calistir(ai_bot.vip_turu)
    assert borsa.emirler == [] and borsa.bakiye_cagri == 0
    assert db.hexists(f'{P}:islem_listesi', 'BLR/USDT')             # sonraki turda tekrar denenecek


def test_stop_sayaci_stop_disi_cikista_sifirlanir(ortam):
    """'Peş peşe 2 stop': arada kâr kilidiyle kapanan işlem sayacı sıfırlar (eskiden yalnız trend çıkışı)."""
    borsa, db, _ = ortam
    db.hset(f'{P}:stop_counts', 'SAY/USDT', '1')                    # önceki bir stop
    borsa.fiyatlar = {'SAY/USDT': 1.008}                            # max %3 görmüş, +%0.6 net: kâr kilidi
    borsa.bakiye['SAY'] = 20.0
    pozisyon(db, 'SAY/USDT', 1.0, adet=20.0, max_kar=0.026, atr=2.0)
    _, mesajlar = calistir(ai_bot.vip_turu)
    assert any('KÂR KİLİDİ' in m for m in mesajlar)
    assert not db.hexists(f'{P}:stop_counts', 'SAY/USDT') and not db.hexists(f'{P}:kara_liste', 'SAY/USDT')


def test_fiyat_yaz_ucuz_coinde_de_anlamli_basamak():
    assert ai_bot.fiyat_yaz(0.00070123) == '0.00070123'
    assert ai_bot.fiyat_yaz(0.0000123) == '0.0000123'                # bilimsel gösterim yok
    assert ai_bot.fiyat_yaz(5.387) == '5.387' and ai_bot.fiyat_yaz(15.001) == '15.001'
    assert ai_bot.fiyat_yaz(84386.02) == '84386.02' and ai_bot.fiyat_yaz(100.0) == '100'
    assert ai_bot.fiyat_yaz(None) == 'None' and ai_bot.fiyat_yaz(0) == '0.0'
