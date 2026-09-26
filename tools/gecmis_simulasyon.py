# -*- coding: utf-8 -*-
"""Botun GERÇEKLEŞMİŞ işlem geçmişini bütçe kısıtıyla yeniden oynatır.

Neden "yeniden oynatma"? Kayıtlı işlemler gerçek dolum fiyatlarını (kayma dahil) içerir;
bu, herhangi bir sentetik backtestten daha dürüst bir kanıttır. Yapılanlar:
  1. Temizlik
     - Ana botun kodunda olmayan çıkış tipleri (🏹 ... TRAILING STOP) ve aynı dönemde
       komisyonsuz yazılmış 40 USDT'lik satırlar: BAŞKA BİR BOTUN işlemleri -> ayrılır.
     - Feature'ı olmayan satırlar: sahiplenilmiş (sinyalsiz) bakiyeler -> ayrılır.
  2. Getiri, kayıtlı Net_Kar_USDT'den DEĞİL, giriş/çıkış fiyatlarından komisyon dahil
     yeniden hesaplanır. (5 Ağustos 2026 öncesi satırlar komisyonsuz kaydedilmişti.)
     Kısmi satış (moon bag / dinamik kısmi) pozisyonun yarısı, kalan çıkış diğer yarısıdır.
  3. Bütçe kısıtlı yeniden oynatma: işlemler gerçek giriş/çıkış zamanlarıyla sıralanır;
     giriş anında serbest bakiye yetmezse işlem atlanır (botun BAKIYE_YETERSIZ davranışı).
       --mod sabit : botun sabit kasası (NORMAL 20 / BALİNA 40 USDT) — bütçe sadece eşzamanlı
                     pozisyon sayısını sınırlar
       --mod oransal: işlem başına anlık özsermayenin %X'i (balinada 2 katı)

Kullanım:
  python tools/gecmis_simulasyon.py eski_core.csv core_islem_verileri_v2.csv.yedek core_islem_verileri.csv \
         --butce 100 450 --mod sabit oransal
  # V18.4'ün çevrimdışı uygulanabilen giriş filtreleriyle, 30 günlük pencere:
  python tools/gecmis_simulasyon.py ... --baslangic 2026-08-05 --bitis 2026-09-04 --max-atr 3 \
         --ai-model core_xgboost_model.json

Fiyat verisi gerektiren V18.4 simülasyonu (yeni çıkış mantığı, BTC rejim filtresi): tools/v184_simulasyon.py
"""
import argparse
import csv
import os
import sys

import pandas as pd

V1 = ['Islem_Zamani', 'Sembol', 'Sinyal', 'Kasa_Tipi', 'Giris_RSI', 'Giris_Vol_Oran', 'Giris_ATR_Pct',
      'Giris_Fiyat', 'Cikis_Fiyat', 'Kar_Orani', 'Net_Kar_USDT', 'Cikis_Tipi', 'Sure_Saat']
FEE = 0.001
KISMI_TIPLER = {'DİNAMİK KISMİ KÂR', '🚀 MOON BAG (%50 VURKAÇ)'}
SABIT_KASA = {'NORMAL': 20.0, 'BALİNA': 40.0}
MIN_NOTIONAL = 5.1


def oku(yollar):
    parcalar = []
    for yol in yollar:
        with open(yol, encoding='utf-8-sig', newline='') as f:
            satirlar = [r for r in csv.reader(f) if r]
        if satirlar and satirlar[0] and satirlar[0][0] == 'Islem_Zamani':
            satirlar = satirlar[1:]
        # V1 alanları her şema sürümünde ilk 13 kolondur (kaymalar V2 kısmındaydı)
        uygun = [r[:13] for r in satirlar if len(r) >= 13]
        parcalar.append(pd.DataFrame(uygun, columns=V1).assign(_dosya=os.path.basename(yol)))
    df = pd.concat(parcalar, ignore_index=True)
    df = df.drop_duplicates(['Islem_Zamani', 'Sembol', 'Cikis_Tipi']).copy()
    for k in V1[4:11] + ['Sure_Saat']:
        df[k] = pd.to_numeric(df[k], errors='coerce')
    df['t'] = pd.to_datetime(df['Islem_Zamani'], errors='coerce', format='mixed')
    return df.dropna(subset=['t', 'Giris_Fiyat', 'Cikis_Fiyat']).sort_values('t').reset_index(drop=True)


def temizle(df):
    g, c = df['Giris_Fiyat'], df['Cikis_Fiyat']
    brut = (df['Kar_Orani'] - (c / g - 1) * 100).abs() < 1e-6
    net_baslangic = df.loc[~brut, 't'].min()      # bot bu tarihten itibaren komisyon dahil yazıyor
    yabanci = df['Cikis_Tipi'].str.startswith('🏹') | (brut & (df['t'] > net_baslangic))
    sahipsiz = df[['Giris_RSI', 'Giris_Vol_Oran', 'Giris_ATR_Pct']].isna().any(axis=1) & ~yabanci
    rapor = {'toplam_satir': len(df), 'yabanci_satir': int(yabanci.sum()), 'sahipsiz_satir': int(sahipsiz.sum()),
             'komisyonsuz_kayitli_satir': int((brut & ~yabanci).sum()),
             'komisyon_dahil_kayit_baslangici': str(net_baslangic)}
    return df[~yabanci & ~sahipsiz].copy(), df[yabanci].copy(), df[sahipsiz].copy(), rapor


def satir_getirisi(g, c):
    return (c * (1 - FEE) - g * (1 + FEE)) / (g * (1 + FEE))


def pozisyonlar(df):
    anahtar = ['Sembol', 'Giris_Fiyat', 'Giris_RSI', 'Giris_Vol_Oran', 'Giris_ATR_Pct']
    kayitlar = []
    for _, grp in df.groupby(anahtar, sort=False):
        grp = grp.sort_values('t')
        kalan, getiri, bacaklar = 1.0, 0.0, []
        for _, r in grp.iterrows():
            pay = 0.5 * kalan if (r['Cikis_Tipi'] in KISMI_TIPLER and len(grp) > 1 and kalan > 0.5 - 1e-9) else kalan
            g_satir = satir_getirisi(r['Giris_Fiyat'], r['Cikis_Fiyat'])
            getiri += pay * g_satir
            bacaklar.append((r['t'], pay, g_satir, r['Cikis_Tipi']))
            kalan -= pay
            if kalan <= 1e-9:
                break
        if kalan > 1e-9:   # yalnız kısmi satış kaydı var: kalan maliyetinden iade sayılır (oynat ile aynı varsayım)
            bacaklar.append((grp['t'].max(), kalan, 0.0, bacaklar[-1][3]))
        ilk = grp.iloc[0]
        giris = (grp['t'] - pd.to_timedelta(grp['Sure_Saat'].fillna(0), unit='h')).min()
        kayitlar.append({'sembol': ilk['Sembol'], 'giris': giris, 'cikis': grp['t'].max(),
                         'kasa_tipi': 'BALİNA' if str(ilk['Kasa_Tipi']).upper().startswith('BAL') else 'NORMAL',
                         'getiri': getiri, 'atr_pct': ilk['Giris_ATR_Pct'], 'n_satir': len(grp),
                         'cikislar': ' + '.join(grp['Cikis_Tipi']), 'giris_fiyat': ilk['Giris_Fiyat'],
                         'rsi': ilk['Giris_RSI'], 'vol_oran': ilk['Giris_Vol_Oran'], 'sinyal': ilk['Sinyal'],
                         'bacaklar': bacaklar})
    return pd.DataFrame(kayitlar).sort_values('giris').reset_index(drop=True)


def ai_skorlari(poz, model_yolu):
    """Botun kullandığı ModelYuvasi ile giriş anındaki feature'lardan skor (kayıtlı feature'lar yeterliyse)."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from sniper.model_karti import ModelYuvasi
    yuva = ModelYuvasi(model_yolu, 'core', 0.65, 'min')
    mesaj = yuva.yenile()
    if not yuva.yuklu:
        raise SystemExit(f"Model yüklenemedi: {mesaj}")
    skor = poz.apply(lambda r: yuva.skor({'rsi': r['rsi'], 'vol_ratio': r['vol_oran'], 'atr_pct': r['atr_pct'],
                                          'signal': r['sinyal']}), axis=1)
    return skor, yuva


def oynat(poz, butce, mod='sabit', oran=0.20):
    """Olay güdümlü yeniden oynatma. Dönen: işlem tablosu, özsermaye eğrisi, atlanan sayısı."""
    olaylar = sorted([(p.giris, 1, i) for i, p in enumerate(poz.itertuples())] +
                     [(p.cikis, 0, i) for i, p in enumerate(poz.itertuples())])   # aynı anda önce çıkış
    nakit, acik, alinan, atlanan = float(butce), {}, [], 0
    ozsermaye = []
    for t, tur, i in olaylar:
        p = poz.iloc[i]
        if tur == 1:
            if mod == 'sabit':
                boyut = SABIT_KASA[p['kasa_tipi']]
            else:
                acik_deger = sum(b for b, _ in acik.values())
                boyut = (nakit + acik_deger) * oran * (2 if p['kasa_tipi'] == 'BALİNA' else 1)
            if boyut < MIN_NOTIONAL or nakit < boyut * 1.01:
                atlanan += 1
                continue
            nakit -= boyut
            acik[i] = (boyut, t)
        elif i in acik:
            boyut, _ = acik.pop(i)
            kar = boyut * p['getiri']
            nakit += boyut + kar
            alinan.append({'giris': p['giris'], 'cikis': t, 'sembol': p['sembol'], 'boyut': boyut,
                           'getiri': p['getiri'], 'kar': kar})
            ozsermaye.append((t, nakit + sum(b for b, _ in acik.values())))
    islemler = pd.DataFrame(alinan)
    egri = pd.DataFrame(ozsermaye, columns=['t', 'ozsermaye']).set_index('t')['ozsermaye']
    return islemler, egri, atlanan


def max_dusus(egri, butce):
    seri = pd.concat([pd.Series([butce]), egri.reset_index(drop=True)])
    zirve = seri.cummax()
    return float(((seri - zirve) / zirve).min())


def pencere_30g(islemler, bas, bit):
    gunluk = islemler.set_index('cikis')['kar'].resample('D').sum()
    gunluk = gunluk.reindex(pd.date_range(bas.normalize(), bit.normalize(), freq='D'), fill_value=0.0)
    return gunluk.rolling(30).sum().dropna()


def rapor_yaz(poz, butce, mod, oran):
    islemler, egri, atlanan = oynat(poz, butce, mod, oran)
    ay = islemler.groupby(islemler['cikis'].dt.to_period('M')).agg(
        islem=('kar', 'size'), kar_usdt=('kar', 'sum'), kazanma=('kar', lambda s: (s > 0).mean()))
    son = float(egri.iloc[-1]) if len(egri) else butce
    p30 = pencere_30g(islemler, poz['giris'].min(), poz['cikis'].max())
    return {'butce': butce, 'mod': mod, 'islem': len(islemler), 'atlanan': atlanan,
            'toplam_kar': son - butce, 'toplam_getiri': son / butce - 1, 'max_dusus': max_dusus(egri, butce),
            'aylik': ay, 'p30': p30}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('dosyalar', nargs='+')
    ap.add_argument('--butce', type=float, nargs='+', default=[100.0, 450.0])
    ap.add_argument('--mod', nargs='+', default=['sabit', 'oransal'], choices=['sabit', 'oransal'])
    ap.add_argument('--oran', type=float, default=0.20, help='oransal mod: işlem başına özsermaye payı')
    ap.add_argument('--max-atr', type=float, default=None, help='ör. 3.0: V18.3+ giriş filtresi (ATR%%<3)')
    ap.add_argument('--baslangic', default=None, help='ör. 2026-08-05: bu tarihten sonra AÇILAN pozisyonlar')
    ap.add_argument('--bitis', default=None, help='ör. 2026-09-04: bu tarihten ÖNCE açılan pozisyonlar')
    ap.add_argument('--ai-model', default=None, help='ör. core_xgboost_model.json: skoru eşiğin altındakileri çıkar')
    ap.add_argument('--ai-esik', type=float, default=None, help='varsayılan: model kartındaki eşik, yoksa 0.65')
    a = ap.parse_args()

    df = oku(a.dosyalar)
    ana, yabanci, sahipsiz, tem = temizle(df)
    poz = pozisyonlar(ana)
    if a.max_atr is not None:
        poz = poz[poz['atr_pct'] <= a.max_atr].reset_index(drop=True)   # bot: ATR/fiyat > esik ise girmez
    if a.baslangic:
        poz = poz[poz['giris'] >= pd.Timestamp(a.baslangic)].reset_index(drop=True)
    if a.bitis:
        poz = poz[poz['giris'] < pd.Timestamp(a.bitis)].reset_index(drop=True)
    if a.ai_model:
        skor, yuva = ai_skorlari(poz, a.ai_model)
        esik = a.ai_esik if a.ai_esik is not None else yuva.esik
        engel = skor < esik
        print(f"AI veto ({os.path.basename(a.ai_model)}, eşik {esik:.2f}): {int(engel.sum())}/{len(poz)} pozisyon "
              + (f"engellenirdi | engellenenlerin ort. net getirisi %{poz.loc[engel, 'getiri'].mean() * 100:.3f}"
                 if engel.any() else "engellenirdi"))
        poz = poz[~engel].reset_index(drop=True)
    print(f"Satır: {tem['toplam_satir']} | başka bot: {tem['yabanci_satir']} | sahiplenilmiş: {tem['sahipsiz_satir']} | "
          f"komisyonsuz kayıtlı (düzeltildi): {tem['komisyonsuz_kayitli_satir']}")
    print(f"Pozisyon: {len(poz)} | {poz['giris'].min():%Y-%m-%d} → {poz['cikis'].max():%Y-%m-%d} | "
          f"ort. net getiri %{poz['getiri'].mean() * 100:.3f} | kazanma %{(poz['getiri'] > 0).mean() * 100:.1f}")
    for mod in a.mod:
        for b in a.butce:
            r = rapor_yaz(poz, b, mod, a.oran)
            print(f"\n=== Bütçe {b:.0f} USDT | mod: {mod} ===")
            print(f"İşlem {r['islem']} (bakiye yetmediği için atlanan {r['atlanan']}) | toplam {r['toplam_kar']:+.2f} USDT "
                  f"(%{r['toplam_getiri'] * 100:+.1f}) | en büyük düşüş %{r['max_dusus'] * 100:.1f}")
            print(r['aylik'].round(3).to_string())
            p = r['p30']
            print(f"Rastgele 30 günlük pencere ({len(p)} pencere): medyan {p.median():+.2f} | %10 {p.quantile(.1):+.2f} | "
                  f"%90 {p.quantile(.9):+.2f} | kârlı pencere oranı %{(p > 0).mean() * 100:.0f} USDT")


if __name__ == '__main__':
    sys.exit(main())
