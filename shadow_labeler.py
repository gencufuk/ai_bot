# -*- coding: utf-8 -*-
"""
SHADOW LABELER V2 — core + shadow sinyallerini TEK TİP etiketle etiketler.

V1'den farklar:
- Etiket, botun kendi risk parametrelerinden türetilen ATR ölçekli bariyerlerle ve
  komisyon dahil hesaplanır (sniper/etiketleme.py). V1: sabit +%4/-%3, komisyonsuz.
- 1 dakikalık mumlar sinyalin DAKİKASINDAN başlar. V1 `since=ts+1` ile sinyalin
  içinde bulunduğu 15m mumu tamamen atlıyordu: sinyalden sonraki ilk ≤15 dakika
  (stopların çoğunun gerçekleştiği pencere) simülasyona hiç girmiyordu.
- Core işlemler de AYNI fonksiyonla etiketlenir -> core ve shadow güvenle birleşir.
- Durum dosyası yok: çıktıdaki (Anahtar, Etiket_Surumu) çiftleri durumun kendisidir.
  Verisi henüz tamamlanmamış sinyal sonraki çalıştırmada tekrar denenir; etiket
  sürümü değişirse (sniper.etiketleme.ETIKET_SURUMU) her şey otomatik yeniden etiketlenir.
- Çıktı: etiketli_sinyaller.csv. Eski shadow_sinyaller_etiketli.csv'ye dokunulmaz.

Cron: trainer'dan önce, ör. 45 2 * * * (trainer 03:00 ise). API key gerekmez.
"""
import argparse
import csv
import glob
import math
import os
import time
from collections import Counter

import pandas as pd

from sniper import etiket_deposu as depo
from sniper.etiketleme import sinyali_etiketle

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SHADOW_KAYNAK = 'shadow_sinyaller.csv'
CORE_KAYNAK_DESENLERI = ['core_islem_verileri_v2.csv', 'core_islem_verileri_v2*.onarildi.csv']
HEDEF = 'etiketli_sinyaller.csv'

PENCERE_DK = 240                  # 4 saat (MAX_BEKLEME_SAATI), 1m mum
BEKLEME_DK = PENCERE_DK + 15      # sinyal en az bu kadar eski olmalı
VERI_YOK_SAAT = 48                # bu kadar eski olup hâlâ verisi eksikse kalıcı VERI_YOK
KASA = {'BALİNA': 40.0, 'NORMAL': 20.0}
KISMI_CIKISLAR = {'DİNAMİK KISMİ KÂR', '🚀 MOON BAG (%50 VURKAÇ)'}


def _csv_satirlari(yol):
    """Başlıkla aynı uzunluktaki satırlar (kaymış satırlar atlanır ve sayılır)."""
    with open(yol, encoding='utf-8-sig', newline='') as f:
        tum = list(csv.reader(f))
    if not tum:
        return []
    baslik, govde = tum[0], tum[1:]
    uygun = [dict(zip(baslik, r)) for r in govde if len(r) == len(baslik)]
    if len(uygun) != len(govde):
        print(f"⚠️ {os.path.basename(yol)}: {len(govde) - len(uygun)} kaymış satır atlandı (tools/csv_onar.py)")
    return uygun


def _rejim(r):
    adx = depo.sayi(r.get('BTC_ADX'))
    if adx == adx:  # NaN değil
        return 'TREND' if adx >= 20 else 'YATAY'
    return 'YATAY' if r.get('Sebep') == 'REJIM_YATAY' else ''


def shadow_adaylari(yol):
    if not os.path.exists(yol):
        return []
    adaylar = []
    for r in _csv_satirlari(yol):
        try:
            ts = int(float(r['Ts']))
            fiyat = float(r['Fiyat'])
        except (KeyError, TypeError, ValueError):
            continue
        adaylar.append({'Anahtar': f"S|{ts}|{r['Sembol']}", 'Kaynak': 'shadow', 'Ts': ts, 'Sembol': r['Sembol'],
                        'Sebep': r.get('Sebep'), 'Sinyal': r.get('Sinyal'), 'Fiyat': fiyat, 'Kasa_Tipi': '',
                        'BTC_OK': 1, 'Rejim': _rejim(r), **depo.feature_alanlari(r)})
    return adaylar


def core_pozisyonlari(yollar):
    """V2 işlem satırlarını pozisyonlara indirger (kısmi + tam çıkış). Yalnızca KAPANMIŞ pozisyonlar."""
    satirlar, gorulen = [], set()
    for yol in yollar:
        for r in _csv_satirlari(yol):
            a = (r.get('Islem_Zamani'), r.get('Sembol'), r.get('Cikis_Tipi'))
            if a in gorulen:
                continue
            gorulen.add(a)
            satirlar.append(r)
    if not satirlar:
        return []
    df = pd.DataFrame(satirlar)
    for k in ['Giris_Fiyat', 'Net_Kar_USDT', 'Sure_Saat', 'Giris_RSI', 'Giris_Vol_Oran', 'Giris_ATR_Pct']:
        df[k] = pd.to_numeric(df[k], errors='coerce') if k in df.columns else float('nan')
    # Islem_Zamani sunucu yerel saatiyle yazılıyor; sunucu UTC (shadow Ts ile doğrulandı)
    df['_t'] = pd.to_datetime(df['Islem_Zamani'], errors='coerce', format='mixed').dt.tz_localize('UTC')
    df = df.dropna(subset=['_t', 'Giris_Fiyat', 'Net_Kar_USDT'])
    cikis_ms = (df['_t'] - pd.Timestamp(0, tz='UTC')) // pd.Timedelta(milliseconds=1)   # çözünürlükten bağımsız
    df['_giris_ms'] = (cikis_ms - (df['Sure_Saat'].fillna(0) * 3600_000)).round().astype('int64')
    if 'Giris_Ts' in df.columns:
        gts = pd.to_numeric(df['Giris_Ts'], errors='coerce')
        df['_giris_ms'] = gts.fillna(df['_giris_ms']).astype('int64')
    pid = df['Pozisyon_Id'] if 'Pozisyon_Id' in df.columns else pd.Series('', index=df.index)
    yedek_anahtar = (df['Sembol'].astype(str) + '|' + df['Giris_Fiyat'].astype(str) + '|' +
                     df['Giris_RSI'].astype(str) + '|' + df['Giris_Vol_Oran'].astype(str))
    df['_grup'] = pid.where(pid.fillna('') != '', yedek_anahtar)

    adaylar = []
    for _, g in df.groupby('_grup', sort=False):
        g = g.sort_values('_t')
        if all(c in KISMI_CIKISLAR for c in g['Cikis_Tipi']):
            continue  # pozisyon hâlâ açık: gerçekleşen sonuç eksik
        # Farklı şemadaki dosyalar birleşince eksik kolonlar NaN olur; NaN "truthy" olduğundan
        # `x or varsayilan` kalıbı yanlış çalışır -> önce NaN'ları None'a çevir.
        ilk = {k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in g.iloc[0].to_dict().items()}
        giris_ms = int(g['_giris_ms'].min())
        pozisyon_id = ilk.get('Pozisyon_Id') or f"{ilk['Sembol']}|{giris_ms}"
        kasa_tipi = ilk.get('Kasa_Tipi') or 'NORMAL'
        adaylar.append({'Anahtar': f"C|{pozisyon_id}", 'Kaynak': 'core', 'Ts': giris_ms, 'Sembol': ilk['Sembol'],
                        'Sebep': 'ISLEM', 'Sinyal': ilk.get('Sinyal'), 'Fiyat': float(ilk['Giris_Fiyat']),
                        'Kasa_Tipi': kasa_tipi, 'BTC_OK': 1, 'Rejim': _rejim(ilk),
                        'Pozisyon_Id': pozisyon_id,
                        'Gercek_Getiri': round(float(g['Net_Kar_USDT'].sum()) / KASA.get(kasa_tipi, 20.0), 6),
                        'Gercek_Cikis': ' + '.join(g['Cikis_Tipi'].astype(str)),
                        **depo.feature_alanlari(ilk)})
    return adaylar


def mumlari_getir(ex, sembol, ts_ms):
    """Sinyalin dakikasından başlayan 1m mumlar (V1 sinyalin 15m mumunu tamamen atlıyordu).
    Sinyal dakikanın ortasındaysa o mumun açılış/tepe/dip değerleri kısmen SİNYAL ÖNCESİNE aittir
    (kırılım sinyali tam da dakika içi sert bir hareketin ardından gelir): o mum yalnızca kapanışıyla,
    yani sinyalden sonraki ilk kesin fiyatla temsil edilir."""
    bas = (int(ts_ms) // 60_000) * 60_000
    mumlar = [list(m) for m in ex.fetch_ohlcv(sembol, '1m', since=bas, limit=PENCERE_DK + 1)]
    if mumlar and int(mumlar[0][0]) < int(ts_ms):
        k = mumlar[0][4]
        mumlar[0] = [mumlar[0][0], k, k, k, k, mumlar[0][5]]
    return mumlar


def etiketle(ex, adaylar, simdi_ms, hedef, flush_n=200):
    import ccxt
    yeni, sayac = [], Counter()
    for a in adaylar:
        try:
            mumlar = mumlari_getir(ex, a['Sembol'], a['Ts'])
        except ccxt.BadSymbol:
            yeni.append({**a, **depo.etiket_alanlari(None)}); sayac['veri_yok'] += 1
            continue
        except Exception as e:  # ağ hatası vb.: sonraki çalıştırmada tekrar denenir
            print(f"⚠️ {a['Sembol']} mumları alınamadı, atlandı: {e}")
            sayac['hata'] += 1
            continue
        s = sinyali_etiketle(a['Fiyat'], depo.sayi(a.get('Giris_ATR_Pct'), 2.5), depo.is_whale_tahmini(a),
                             mumlar, kayma_uygula=(a['Kaynak'] != 'core'))
        if s is None:
            if simdi_ms - a['Ts'] > VERI_YOK_SAAT * 3600_000:
                yeni.append({**a, **depo.etiket_alanlari(None)}); sayac['veri_yok'] += 1
            else:
                sayac['bekliyor'] += 1
            continue
        yeni.append({**a, **depo.etiket_alanlari(s)}); sayac[a['Kaynak']] += 1
        if len(yeni) >= flush_n:
            depo.yaz(hedef, yeni); yeni = []
    depo.yaz(hedef, yeni)
    return sayac


def ozet(hedef):
    df = depo.oku([hedef])
    df = df[df['Etiket_Sonuc'] != depo.VERI_YOK]
    if df.empty:
        return
    print("\n📊 Etiket özeti (net getiri, komisyon dahil):")
    g = df.groupby(['Kaynak', 'Sebep']).agg(n=('Anahtar', 'size'), ort_getiri=('Etiket_Getiri', 'mean'),
                                           pozitif=('Etiket_Getiri', lambda s: (s > 0).mean()))
    print(g.round(4).to_string())
    core = df[df['Kaynak'] == 'core'].dropna(subset=['Gercek_Getiri'])
    if len(core) >= 5:
        r = core['Etiket_Getiri'].corr(core['Gercek_Getiri'], method='spearman')
        print(f"Core: etiket ↔ gerçekleşen getiri Spearman = {r:.2f} (n={len(core)}) — etiketin temsil gücü")


def main():
    ap = argparse.ArgumentParser(description="Core + shadow sinyallerini tek tip etiketle etiketler.")
    ap.add_argument('--limit', type=int, default=0, help='en fazla N sinyal etiketle (0 = hepsi)')
    a = ap.parse_args()
    import ccxt
    ex = ccxt.binance({'enableRateLimit': True})
    hedef = os.path.join(BASE_DIR, HEDEF)
    core_yollari = sorted({p for d in CORE_KAYNAK_DESENLERI for p in glob.glob(os.path.join(BASE_DIR, d))})
    etiketli = depo.etiketli_anahtarlar(hedef)
    simdi_ms = int(time.time() * 1000)
    adaylar = shadow_adaylari(os.path.join(BASE_DIR, SHADOW_KAYNAK)) + core_pozisyonlari(core_yollari)
    hazir = sorted((x for x in adaylar if x['Anahtar'] not in etiketli and x['Ts'] <= simdi_ms - BEKLEME_DK * 60_000),
                   key=lambda x: x['Ts'])
    if a.limit:
        hazir = hazir[:a.limit]
    sayac = etiketle(ex, hazir, simdi_ms, hedef)
    print(f"✅ Etiketlendi: shadow={sayac['shadow']} core={sayac['core']} | veri yok={sayac['veri_yok']} | "
          f"bekliyor={sayac['bekliyor']} | hata={sayac['hata']} (aday: {len(hazir)}, toplam kaynak: {len(adaylar)})")
    ozet(hedef)


if __name__ == "__main__":
    main()
