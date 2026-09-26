# -*- coding: utf-8 -*-
"""
BACKFILL — geçmiş OHLCV'den sinyal üretir ve tek tip etiketle etiketler.

Veri kıtlığının kök çözümü: giriş kuralı tamamen OHLCV'ye dayalı olduğu için geçmiş
aylardaki sinyaller yeniden üretilebilir. Canlıda 10 günde ~70 pozisyon birikirken,
80 sembollük evrende 6 ay geriye gitmek binlerce etiketli örnek verir.

Parite: kural + feature'lar sniper.ozellikler'den (canlı botla AYNI kod), KAPALI MUM
modunda (ai_bot.SINYAL_KAPALI_MUM=True ile birebir). BTC bağlamı (EMA200 histerezisi,
ani çöküş, ADX rejimi) ve "hacmi 12M+ olan en çok yükselen 10 parite" taraması da
geçmişe dönük emüle edilir. Canlı bot varsayılan olarak KAPANMAMIŞ mumla sinyal
ürettiğinden canlı ile backfill arasında dağılım farkı vardır; trainer bunu raporlar.

Geçmişte üretilemeyen feature'lar (OB_Oran, Spread_Bps, OB_Oran_Yakin, Stop_Sayisi)
boş kalır; trainer'ın varsayılan feature listesi bunları kullanmaz.

Bilinen yanlılık: evren BUGÜNÜN en hacimli paritelerinden seçilir (survivorship).
Geçmiş hacim filtresi (>12M) uygulanarak kısmen azaltılır.

Çıktı etiketleyicinin de yazdığı tek eğitim dosyasıdır (etiketli_sinyaller.csv, Kaynak=backfill).
Eski sürümün ayrı backfill_sinyaller.csv dosyası varsa tools/veri_birlestir.py onu bu dosyaya katar.

Kullanım (sunucuda, API key gerekmez; çıktı yarıda kalsa da devam ettirilebilir):
  python backfill_sinyaller.py --gun 180 --evren 80
  python backfill_sinyaller.py --gun 30 --evren 20 --etiket-tf 15m     # hızlı deneme
"""
import argparse
import datetime
import os
import time
from collections import Counter

import numpy as np
import pandas as pd
import pandas_ta as ta

from sniper import etiket_deposu as depo
from sniper.etiketleme import sinyali_etiketle
from sniper.ozellikler import (MUM_15M_MS, SinyalAyarlari, bir_saati_hizala, ema_son, genisletilmis_ozellikler,
                               kanonik_satir, ohlcv_df, sinyal_degerlendir)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HEDEF = 'etiketli_sinyaller.csv'   # tek eğitim dosyası (etiketleyiciyle ortak; Kaynak kolonu: backfill)
TF_MS = {'1m': 60_000, '15m': MUM_15M_MS, '1h': 3_600_000}

# ai_bot.py ile aynı değerler
MIN_HACIM_USDT = 12_000_000
RADAR_TOP_N = 10
ADX_TREND_ESIK = 20.0
BTC_HISTEREZIS = 0.004
YASAKLI_COINLER = ['WLD', 'SPK', 'XUSD', 'XAUT', 'FDUSD', 'USDC', 'PAXG', 'TUSD', 'EUR', 'GBP', 'DAI', 'TRY',
                   'TRX', 'BUSD', 'USDP', 'RLUSD']
PENCERE_15M = 99       # canlı: limit=100, kapalı mum modunda canlı mum atılır -> 99
PENCERE_1H = 50
PENCERE_BTC = 300
ISINMA_MUM = 300       # hesap pencereleri için ek geçmiş (15m)


def sayfali_ohlcv(ex, sym, tf, bas_ms, bit_ms, limit=1000):
    adim = TF_MS[tf]
    parcalar, since = [], int(bas_ms)
    while since < bit_ms:
        parca = ex.fetch_ohlcv(sym, tf, since=since, limit=limit)
        if not parca:
            break
        parcalar.extend(parca)
        yeni_since = int(parca[-1][0]) + adim
        if yeni_since <= since:
            break
        since = yeni_since
    df = ohlcv_df(parcalar).drop_duplicates('ts').sort_values('ts')
    return df[(df['ts'] >= bas_ms) & (df['ts'] < bit_ms)].reset_index(drop=True)


def evren_sec(ex, n):
    tickers = ex.fetch_tickers()
    adaylar = []
    for s, d in tickers.items():
        if not s.endswith('/USDT') or not all(ord(c) < 128 for c in s) or any(y in s for y in YASAKLI_COINLER):
            continue
        adaylar.append((float(d.get('quoteVolume') or 0), s))
    return [s for _, s in sorted(adaylar, reverse=True) if s != 'BTC/USDT'][:n]


def btc_baglami(btc: pd.DataFrame) -> pd.DataFrame:
    """Her kapanmış BTC 15m mumu için canlı radar_loop'un ürettiği bağlam (kapalı mum modu)."""
    kayitlar, btc_ok = [], False
    c, h = btc['c'].to_numpy(), btc['h'].to_numpy()
    for i in range(len(btc)):
        if i < PENCERE_BTC - 1:
            kayitlar.append(None)
            continue
        pencere = btc.iloc[i - PENCERE_BTC + 1:i + 1]
        ema = ema_son(pencere['c'], 200)
        try:
            adx = float(ta.adx(pencere['h'], pencere['l'], pencere['c'], length=14)['ADX_14'].iloc[-1])
        except Exception:
            adx = 0.0
        tepe = h[i - 2:i + 1].max()
        if (tepe - c[i]) / tepe > 0.015:
            btc_ok = False
        elif c[i] > ema * (1 + BTC_HISTEREZIS):
            btc_ok = True
        elif c[i] < ema * (1 - BTC_HISTEREZIS):
            btc_ok = False
        kayitlar.append({'kapanis': int(btc['ts'].iloc[i]) + MUM_15M_MS,
                         'btc_1h_degisim': float((c[i] / c[i - 4] - 1) * 100),
                         'btc_ema_uzaklik': float((c[i] / ema - 1) * 100) if ema > 0 else 0.0,
                         'btc_adx': adx, 'btc_ok': bool(btc_ok),
                         'rejim': 'TREND' if adx >= ADX_TREND_ESIK else 'YATAY'})
    return pd.DataFrame([k for k in kayitlar if k]).set_index('kapanis')


def on_filtre(df: pd.DataFrame, ayar: SinyalAyarlari) -> pd.Series:
    """Vektörel, GEVŞEK ön eleme (kesin kontrol pencereli sinyal_degerlendir ile yapılır)."""
    rsi = ta.rsi(df['c'], length=14)
    atr = ta.atr(df['h'], df['l'], df['c'], length=14)
    av = df['v'].rolling(14).mean()
    msb = df['c'] > df['h'].shift(2).rolling(8).max()
    eng = (df['c'].shift(1) < df['o'].shift(1)) & (df['c'] > df['o']) & (df['o'] < df['c'].shift(1))
    return ((rsi > ayar.rsi_esik - 5) & (df['v'] / av > ayar.hacim_esik * 0.98)
            & (atr / df['c'] <= ayar.max_atr_pct * 1.05) & (msb | eng)).fillna(False)


def radar_paneli(seriler: dict) -> tuple:
    """Her kapanış anında: canlı radarın 'hacim>12M, 24s değişime göre ilk 10' seçimi + piyasa genişliği."""
    deg, hac = {}, {}
    for s, df in seriler.items():
        idx = df['ts'] + MUM_15M_MS
        deg[s] = pd.Series((df['c'] / df['c'].shift(96) - 1).to_numpy(), index=idx)
        hac[s] = pd.Series((df['v'] * df['c']).rolling(96).sum().to_numpy(), index=idx)
    deg, hac = pd.DataFrame(deg), pd.DataFrame(hac)
    uygun = hac > MIN_HACIM_USDT
    sira = deg.where(uygun).rank(axis=1, ascending=False, method='first')
    ilk_n = sira <= RADAR_TOP_N
    genislik = (deg.where(uygun) > 0).sum(axis=1) / uygun.sum(axis=1).replace(0, np.nan)
    return ilk_n, genislik


def sinyalleri_uret(sym, df15, df1h, btc_ctx, ilk_n, genislik, bas_ms, btc_ok_disi, ayar):
    aday = on_filtre(df15, ayar)
    satirlar = []
    for i in np.flatnonzero(aday.to_numpy()):
        if i < PENCERE_15M - 1:
            continue
        kapanis = int(df15['ts'].iloc[i]) + MUM_15M_MS
        if kapanis < bas_ms or kapanis not in btc_ctx.index:
            continue
        ctx = btc_ctx.loc[kapanis]
        if not btc_ok_disi and not ctx['btc_ok']:
            continue
        if kapanis not in ilk_n.index or sym not in ilk_n.columns or not bool(ilk_n.at[kapanis, sym]):
            continue  # canlı radar bu anda bu sembolü taramazdı
        pencere = df15.iloc[i - PENCERE_15M + 1:i + 1].reset_index(drop=True)
        fiyat = float(pencere['c'].iloc[-1])
        h_bitis = (kapanis // TF_MS['1h']) * TF_MS['1h']
        w1h = df1h[df1h['ts'] <= h_bitis].tail(PENCERE_1H)
        w1h = bir_saati_hizala(w1h, kapanis, fiyat)
        temel = sinyal_degerlendir(pencere, ema_son(w1h['c'], 20), ayar)
        if temel is None:
            continue
        m = genisletilmis_ozellikler(
            pencere, w1h, temel, simdi=datetime.datetime.fromtimestamp(kapanis / 1000, datetime.timezone.utc).replace(tzinfo=None),
            simdi_ms=kapanis, btc_1h_degisim=ctx['btc_1h_degisim'], btc_ema_uzaklik=ctx['btc_ema_uzaklik'],
            btc_adx=ctx['btc_adx'], stop_sayisi=0, ob=None,
            genislik=float(genislik.get(kapanis, np.nan)), kapali_mum_modu=True)
        satir = {k: v for k, v in kanonik_satir(m).items() if v is not None}
        satir['Stop_Sayisi'] = None   # bot durumu: geçmişte bilinmez
        satir.update({'Anahtar': f"B|{kapanis}|{sym}", 'Kaynak': 'backfill', 'Ts': kapanis, 'Sembol': sym,
                      'Sebep': 'BACKFILL', 'Sinyal': m['signal'], 'Fiyat': fiyat, 'Kasa_Tipi': '',
                      'BTC_OK': int(bool(ctx['btc_ok'])), 'Rejim': ctx['rejim'], '_i': int(i)})
        satirlar.append(satir)
    return satirlar


def etiketle(ex, sym, satirlar, df15, etiket_tf):
    sonuc = []
    for s in satirlar:
        i = s.pop('_i')
        if etiket_tf == '15m':
            mumlar = df15.iloc[i + 1:i + 17][['ts', 'o', 'h', 'l', 'c', 'v']].to_numpy().tolist()
            e = sinyali_etiketle(s['Fiyat'], depo.sayi(s.get('Giris_ATR_Pct'), 2.5), depo.is_whale_tahmini(s),
                                 mumlar, mum_suresi_dk=15)
        else:
            mumlar = ex.fetch_ohlcv(sym, '1m', since=int(s['Ts']), limit=241)
            e = sinyali_etiketle(s['Fiyat'], depo.sayi(s.get('Giris_ATR_Pct'), 2.5), depo.is_whale_tahmini(s), mumlar)
        if e is not None:
            sonuc.append({**s, **depo.etiket_alanlari(e)})
    return sonuc


def calistir(ex, gun, evren_n, etiket_tf='1m', btc_ok_disi=False, hedef=None, simdi_ms=None, semboller=None):
    hedef = hedef or os.path.join(BASE_DIR, HEDEF)
    simdi_ms = simdi_ms or int(time.time() * 1000)
    bit_ms = ((simdi_ms - 5 * 3_600_000) // MUM_15M_MS) * MUM_15M_MS    # etiket penceresi tamamlanmış olsun
    bas_ms = bit_ms - gun * 86_400_000
    veri_bas = bas_ms - ISINMA_MUM * MUM_15M_MS
    ayar = SinyalAyarlari()

    semboller = semboller or evren_sec(ex, evren_n)
    utc = datetime.timezone.utc
    print(f"🌍 Evren: {len(semboller)} sembol | {datetime.datetime.fromtimestamp(bas_ms / 1000, utc):%Y-%m-%d} → "
          f"{datetime.datetime.fromtimestamp(bit_ms / 1000, utc):%Y-%m-%d %H:%M} UTC")
    btc = sayfali_ohlcv(ex, 'BTC/USDT', '15m', veri_bas, bit_ms + 17 * MUM_15M_MS)
    btc_ctx = btc_baglami(btc)
    seriler15 = {s: sayfali_ohlcv(ex, s, '15m', veri_bas, bit_ms + 17 * MUM_15M_MS) for s in semboller}
    seriler15 = {s: d for s, d in seriler15.items() if len(d) > PENCERE_15M}
    ilk_n, genislik = radar_paneli(seriler15)

    mevcut = depo.etiketli_anahtarlar(hedef)
    sayac = Counter()
    for s, df15 in seriler15.items():
        df1h = sayfali_ohlcv(ex, s, '1h', veri_bas - PENCERE_1H * TF_MS['1h'], bit_ms + TF_MS['1h'])
        sinyaller = [x for x in sinyalleri_uret(s, df15, df1h, btc_ctx, ilk_n, genislik, bas_ms, btc_ok_disi, ayar)
                     if x['Ts'] <= bit_ms and x['Anahtar'] not in mevcut]
        etiketli = etiketle(ex, s, sinyaller, df15, etiket_tf)
        depo.yaz(hedef, etiketli)
        sayac['sinyal'] += len(sinyaller)
        sayac['etiketli'] += len(etiketli)
        print(f"  {s}: {len(etiketli)} yeni etiketli sinyal")
    print(f"✅ Backfill bitti: {sayac['etiketli']}/{sayac['sinyal']} sinyal etiketlendi -> {os.path.basename(hedef)}")
    return sayac


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--gun', type=int, default=180)
    ap.add_argument('--evren', type=int, default=80, help='en hacimli N USDT paritesi')
    ap.add_argument('--etiket-tf', choices=['1m', '15m'], default='1m',
                    help='1m: canlı labeler ile aynı çözünürlük (önerilen); 15m: hızlı deneme')
    ap.add_argument('--btc-ok-disi', action='store_true', help='BTC_OK=False anlarındaki sinyalleri de yaz')
    a = ap.parse_args()
    import ccxt
    calistir(ccxt.binance({'enableRateLimit': True}), a.gun, a.evren, a.etiket_tf, a.btc_ok_disi)


if __name__ == '__main__':
    main()
