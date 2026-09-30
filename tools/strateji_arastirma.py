# -*- coding: utf-8 -*-
"""ALIM YÖNTEMİ ARAŞTIRMASI: literatürdeki kripto getiri etkileri Binance günlük verisinde, komisyon dahil.

Soru: botun "son 24 saatte en çok yükseleni al" yaklaşımı yerine hangi alım kuralı geçmişte kazandırırdı?
Kurallar literatürden ÖNCEDEN seçildi ve parametreleri sabit (ANALIZ_V18.4.md §11). Dönem ikiye bölünür:
kuruluş (--ayrim tarihinden önce) ve sınama (sonra); ayrıca yıllara ayrılır. Parametre taraması yalnız
sağlamlık için raporlanır; en iyi sonucu veren seçilmez.

Kurallar: her gün UTC kapanışında karar verilir, ertesi kapanışa kadar tutulur (haftalıklar 7 gün). Seçilen
coinlere eşit ağırlık; seçim yoksa nakit (getiri 0). Komisyon + kayma: dengelemede el değiştiren tutar x --ucret.
  BOT_VEKILI      son 24 saat hacmi 12M+ olanlardan son 24 saatte en çok yükselen 10 (botun radarının günlük vekili)
  DONUS           likit coinlerden son 24 saatte en çok düşen 10 (kısa vadeli dönüş; Zaremba vd. 2021)
  DONUS_TREND     DONUS, yalnız BTC 20 günlük ortalamasının üstündeyken
  BUYUK_MOMENTUM  hacimce en büyük 10 likit coinden son 24 saatte en çok yükselen 3 (Zaremba vd. 2021: en büyük
                  coinlerde günlük momentum)
  HAFTALIK_MOM    7 günde bir: likit coinlerden son 7 günde en çok yükselen 10 (Liu, Tsyvinski, Wu 2022)
  TREND_SEPET     hacimce en büyük 10 likit coinden kendi 20 günlük ortalamasının üstünde olanlar (zaman serisi
                  momentumu; Liu & Tsyvinski 2021, Detzel vd. 2021)
  TREND_DIP       20 günlük ortalamasının üstündeki likit coinlerden son 24 saatte en çok düşen 5 (trend içinde
                  geri çekilme)
  Karşılaştırma:  BTC_TUT (BTC al-tut), SEPET_TUT (hacimce en büyük 10 likit coin, 7 günde bir dengelenir)
Likit: 30 günlük medyan günlük hacim >= --min-hacim ve en az 60 günlük geçmiş. Günlük getiri oynaklığı çok düşük
pariteler (sabit coinler) evrenden çıkarılır.

Bilinen sınırlar:
  - Evren bugünün en hacimli --evren paritesidir: dönem içinde delist olan coinler yok (hayatta kalma yanlılığı).
    Düşenleri alan kurallar bundan en çok yararlanır; batan coinler listede olmadığı için sonuçları iyimserdir.
  - İşlem kapanış fiyatından varsayılır (24 saat açık piyasada kapanıştan hemen sonra işlem neredeyse aynı fiyattır);
    kayma --ucret içindedir.
  - Günlük çözünürlük botun 15 dakikalık kararlarının vekilidir; yön bulmak içindir, botun birebir simülasyonu değildir.

Kullanım (sunucuda, bot çalışırken de olur; API anahtarı gerekmez, yalnız herkese açık günlük fiyat verisi):
  cd /root && venv/bin/python tools/strateji_arastirma.py                 # ~5 dk (150 parite, 2023'ten bugüne)
Çıktılar: strateji_rapor.txt (özet), strateji_rapor.json, strateji_gunluk.csv (kuralların günlük net getirisi)
"""
import argparse
import datetime
import json
import os
import sys
import time
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _yol in (KOK, os.path.join(KOK, 'tools')):
    if _yol not in sys.path:
        sys.path.insert(0, _yol)

import backfill_sinyaller as bf  # noqa: E402
import v184_simulasyon as vs  # noqa: E402

GUN_MS = vs.GUN_MS
vs.TF_MS.setdefault('1d', GUN_MS)   # sunucudaki eski simülatör sürümünde '1d' yoksa
ISINMA_GUN = 90                 # ortalama ve likidite pencereleri için dönem başından önceki geçmiş
YIL = 365
BOT_HACIM = bf.MIN_HACIM_USDT   # botun radarı: son 24 saat hacmi 12M USDT üstü
SABIT_COIN_OYNAKLIK = 0.003     # günlük getiri std'si bunun altındaysa sabit coin sayılır

KURAL_ACIKLAMA = {
    'BOT_VEKILI': 'son 24 saatte en çok yükselen 10 (botun radarının günlük vekili)',
    'DONUS': 'son 24 saatte en çok düşen 10 (kısa vadeli dönüş)',
    'DONUS_TREND': 'DONUS, yalnız BTC 20 günlük ortalamanın üstündeyken',
    'BUYUK_MOMENTUM': 'en büyük 10 coinden son 24 saatte en çok yükselen 3',
    'HAFTALIK_MOM': '7 günde bir, son 7 günde en çok yükselen 10',
    'TREND_SEPET': 'en büyük 10 coinden 20 günlük ortalamasının üstündekiler',
    'TREND_DIP': 'ortalamasının üstündekilerden son 24 saatte en çok düşen 5',
    'BTC_TUT': 'BTC al-tut',
    'SEPET_TUT': 'en büyük 10 coin, haftalık dengelenen sepet',
}


# ------------------------------------------------------------------------------------------
# Veri
# ------------------------------------------------------------------------------------------
def veri_yukle(depo, semboller, bas_ms, bit_ms, log=print):
    """(kapanış, günlük USDT hacmi) panelleri: satır = günlük mumun AÇILIŞ tarihi (UTC), sütun = parite."""
    kapanis, hacim = {}, {}
    for n, s in enumerate(semboller, 1):
        try:
            d = depo.getir(s, '1d', bas_ms - ISINMA_GUN * GUN_MS, bit_ms)
        except vs.VeriYok:
            continue
        if len(d) >= 30:
            idx = pd.to_datetime(d[:, 0].astype('int64'), unit='ms')
            kapanis[s] = pd.Series(d[:, 4], index=idx)
            hacim[s] = pd.Series(d[:, 5] * d[:, 4], index=idx)
        if n % 25 == 0:
            log(f"  günlük veri: {n}/{len(semboller)} parite ({depo.istek} API isteği)")
    C = pd.DataFrame(kapanis).sort_index()
    V = pd.DataFrame(hacim).reindex(C.index)
    return C, V


def sabit_coinleri_ayikla(C: pd.DataFrame, V: pd.DataFrame, koru=('BTC/USDT',)):
    oynaklik = C.pct_change(fill_method=None).std()
    sabit = [s for s, o in oynaklik.items() if s not in koru and (not np.isfinite(o) or o < SABIT_COIN_OYNAKLIK)]
    return C.drop(columns=sabit), V.drop(columns=sabit), sabit


# ------------------------------------------------------------------------------------------
# Kurallar: her fonksiyon (hedef ağırlıklar, dengeleme günleri) döndürür. Hedef[t] yalnız t kapanışına kadarki
# veriyle hesaplanır (gelecek bilgisi yok; tests/test_strateji_arastirma.py doğrular).
# ------------------------------------------------------------------------------------------
def _sec(skor: pd.DataFrame, uygun: pd.DataFrame, k: int, en_yuksek: bool = True) -> pd.DataFrame:
    sira = skor.where(uygun).rank(axis=1, ascending=not en_yuksek, method='first')
    secim = (sira <= k).astype(float)
    return secim.div(secim.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)


def _her_gun(index) -> pd.Series:
    return pd.Series(True, index=index)


def _her_hafta(index) -> pd.Series:
    return pd.Series(np.arange(len(index)) % 7 == 0, index=index)


class Panel:
    def __init__(self, C: pd.DataFrame, V: pd.DataFrame, min_hacim: float):
        self.C, self.V = C, V
        self.R1 = C.pct_change(fill_method=None)
        self.likit = ((V.rolling(30, min_periods=20).median() >= min_hacim)
                      & (C.notna().cumsum() >= 60) & C.notna())
        # hacim sırası: 30 günlük medyan hacme göre en büyükler (o günün bilgisiyle)
        self.hacim_sira = V.rolling(30, min_periods=20).median().where(self.likit).rank(axis=1, ascending=False)
        self.ma20 = C.rolling(20, min_periods=20).mean()
        self.btc_trend = (C['BTC/USDT'] > self.ma20['BTC/USDT']) if 'BTC/USDT' in C else pd.Series(True, index=C.index)


def kural_bot_vekili(p: Panel, k=10):
    uygun = (p.V > BOT_HACIM) & (p.C.notna().cumsum() >= 60) & p.R1.notna()
    return _sec(p.R1, uygun, k, True), _her_gun(p.C.index)


def kural_donus(p: Panel, k=10):
    return _sec(p.R1, p.likit & p.R1.notna(), k, False), _her_gun(p.C.index)


def kural_donus_trend(p: Panel, k=10):
    w, d = kural_donus(p, k)
    return w.mul(p.btc_trend.astype(float), axis=0), d


def kural_buyuk_momentum(p: Panel, k=3, buyuk=10):
    return _sec(p.R1, p.likit & (p.hacim_sira <= buyuk) & p.R1.notna(), k, True), _her_gun(p.C.index)


def kural_haftalik_mom(p: Panel, k=10, gun=7):
    r = p.C / p.C.shift(gun) - 1
    return _sec(r, p.likit & r.notna(), k, True), _her_hafta(p.C.index)


def kural_trend_sepet(p: Panel, buyuk=10, ma=20):
    ort = p.ma20 if ma == 20 else p.C.rolling(ma, min_periods=ma).mean()
    secim = (p.likit & (p.hacim_sira <= buyuk) & (p.C > ort)).astype(float)
    return secim.div(secim.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0), _her_gun(p.C.index)


def kural_trend_dip(p: Panel, k=5, ma=20):
    ort = p.ma20 if ma == 20 else p.C.rolling(ma, min_periods=ma).mean()
    return _sec(p.R1, p.likit & (p.C > ort) & p.R1.notna(), k, False), _her_gun(p.C.index)


def kural_btc_tut(p: Panel):
    w = pd.DataFrame(0.0, index=p.C.index, columns=p.C.columns)
    if 'BTC/USDT' in w:
        w['BTC/USDT'] = 1.0
    return w, pd.Series(np.arange(len(w)) == 0, index=w.index)


def kural_sepet_tut(p: Panel, buyuk=10):
    secim = (p.likit & (p.hacim_sira <= buyuk)).astype(float)
    return secim.div(secim.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0), _her_hafta(p.C.index)


KURALLAR: Dict[str, Callable] = {
    'BOT_VEKILI': kural_bot_vekili, 'DONUS': kural_donus, 'DONUS_TREND': kural_donus_trend,
    'BUYUK_MOMENTUM': kural_buyuk_momentum, 'HAFTALIK_MOM': kural_haftalik_mom, 'TREND_SEPET': kural_trend_sepet,
    'TREND_DIP': kural_trend_dip, 'BTC_TUT': kural_btc_tut, 'SEPET_TUT': kural_sepet_tut,
}
# Sağlamlık taraması (yalnız raporlanır, seçim yapılmaz)
VARYANTLAR = {
    'BOT_VEKILI': [{'k': 5}, {'k': 20}],
    'DONUS': [{'k': 5}, {'k': 20}],
    'BUYUK_MOMENTUM': [{'k': 1}, {'k': 5, 'buyuk': 20}],
    'HAFTALIK_MOM': [{'gun': 14}, {'gun': 28}, {'k': 5}],
    'TREND_SEPET': [{'ma': 50}, {'buyuk': 20}],
    'TREND_DIP': [{'ma': 50}, {'k': 10}],
}


# ------------------------------------------------------------------------------------------
# Portföy simülasyonu
# ------------------------------------------------------------------------------------------
def simule(hedef: pd.DataFrame, dengele: pd.Series, R1: pd.DataFrame, ucret: float) -> pd.DataFrame:
    """hedef[t]: t kapanışında kurulacak ağırlıklar (yalnız dengeleme günlerinde kullanılır). Aradaki günlerde
    ağırlıklar fiyatla kayar (al-tut). Dönen: t+1 satırında t -> t+1 net getirisi ve o gün el değiştiren oran."""
    W = hedef.to_numpy(dtype=float)
    R = R1.to_numpy(dtype=float)
    D = dengele.to_numpy(dtype=bool)
    w = np.zeros(W.shape[1])
    getiri, devir = np.zeros(len(W)), np.zeros(len(W))
    for t in range(len(W) - 1):
        maliyet = 0.0
        if D[t]:
            yeni = np.nan_to_num(W[t])
            devir[t + 1] = np.abs(yeni - w).sum()
            maliyet = devir[t + 1] * ucret
            w = yeni
        r = np.nan_to_num(R[t + 1])          # verisi olmayan gün (nadir): fiyat değişmemiş sayılır
        getiri[t + 1] = float(w @ r) - maliyet
        deger = w * (1 + r)
        toplam = (1.0 - w.sum()) + deger.sum()
        w = deger / toplam if toplam > 0 else np.zeros_like(w)
    return pd.DataFrame({'getiri': getiri, 'devir': devir}, index=hedef.index)


def ozet(g: pd.Series, devir: pd.Series, butce: float = 90.0) -> dict:
    g = g.dropna()
    if len(g) < 2:
        return {'gun': int(len(g))}
    egri = (1 + g).cumprod()
    std = float(g.std())
    return {'gun': int(len(g)), 'toplam_pct': float(egri.iloc[-1] - 1) * 100,
            'yillik_pct': float(egri.iloc[-1] ** (YIL / len(g)) - 1) * 100,
            'sharpe': float(g.mean() / std * np.sqrt(YIL)) if std > 0 else None,
            'max_dusus_pct': float((egri / egri.cummax() - 1).min()) * 100,
            'pozitif_gun_pct': float((g > 0).mean()) * 100,
            'gunluk_ort_bps': float(g.mean()) * 1e4,
            'gunluk_devir': float(devir.reindex(g.index).mean()),
            'gunluk_usdt': float(g.mean()) * butce}


def blok_bootstrap_ga(g: pd.Series, blok=7, n=2000, tohum=0) -> Optional[list]:
    """Ortalama günlük getirinin %95 güven aralığı (7 günlük bloklar: oynaklık kümelenmesine dayanıklı)."""
    x = g.dropna().to_numpy()
    if len(x) < 4 * blok:
        return None
    rng = np.random.default_rng(tohum)
    nb = len(x) // blok
    bloklar = x[:nb * blok].reshape(nb, blok)
    ort = bloklar[rng.integers(0, nb, size=(n, nb))].mean(axis=(1, 2))
    return [float(np.percentile(ort, 2.5)) * 1e4, float(np.percentile(ort, 97.5)) * 1e4]


def donem_ozetleri(sonuc: pd.DataFrame, bas: pd.Timestamp, ayrim: pd.Timestamp, butce: float) -> dict:
    g, dv = sonuc['getiri'], sonuc['devir']
    g = g[g.index > bas]                      # ilk sonuç günü: dönem başındaki kapanıştan sonraki gün
    donemler = {'tum': g, 'kurulus': g[g.index < ayrim], 'sinama': g[g.index >= ayrim]}
    for yil in sorted(set(g.index.year)):
        donemler[str(yil)] = g[g.index.year == yil]
    cikti = {ad: ozet(x, dv, butce) for ad, x in donemler.items() if len(x) >= 2}
    for ad in ('kurulus', 'sinama', 'tum'):
        if ad in cikti:
            cikti[ad]['ort_ga95_bps'] = blok_bootstrap_ga(donemler[ad])
    return cikti


# ------------------------------------------------------------------------------------------
def rapor_metni(meta, sonuclar, varyantlar, ucret_duyarlilik) -> str:
    f = lambda v, fmt: (fmt.format(v) if v is not None else '-')  # noqa: E731
    y = [f"ALIM YÖNTEMİ ARAŞTIRMASI | {meta['baslangic']} → {meta['bitis']} | kuruluş/sınama ayrımı {meta['ayrim']}",
         f"Evren: bugünün en hacimli {meta['evren']} USDT paritesi + BTC; kullanılan {meta['parite']} parite "
         f"(sabit coin çıkarılan: {meta['sabit_coin']}) | likit: 30g medyan hacim >= {meta['min_hacim'] / 1e6:.0f}M USDT | "
         f"komisyon+kayma: işlem başına %{meta['ucret'] * 100:.2f} | API isteği: {meta['istek']}",
         "Getiriler komisyon dahil, günlük; 'sınama' dönemi kurallar seçildikten sonraki veridir. "
         f"{meta['butce']:.0f} USDT sütunu: o bütçeyle günlük ortalama kâr/zarar.", '',
         "1) KURALLAR — kuruluş ve sınama dönemi",
         f"   {'kural':15s} {'dönem':8s} {'yıllık':>8s} {'sharpe':>6s} {'maxDD':>7s} {'günlük ort':>10s} "
         f"{'%95 GA (bps)':>16s} {'poz.gün':>7s} {'devir':>5s} {meta['butce']:.0f}USDT/gün"]
    for ad, d in sonuclar.items():
        for don in ('kurulus', 'sinama'):
            o = d.get(don)
            if not o or 'yillik_pct' not in o:
                continue
            ga = o.get('ort_ga95_bps')
            ga_txt = f"[{ga[0]:+.1f}, {ga[1]:+.1f}]" if ga else '-'
            y.append(f"   {ad:15s} {don:8s} {o['yillik_pct']:+7.1f}% {f(o['sharpe'], '{:+.2f}'):>6s} "
                     f"{o['max_dusus_pct']:6.1f}% {o['gunluk_ort_bps']:+8.1f}bp {ga_txt:>16s} {o['pozitif_gun_pct']:6.0f}% "
                     f"{o['gunluk_devir']:5.2f} {o['gunluk_usdt']:+8.3f}")
    yillar = sorted({k for d in sonuclar.values() for k in d if k.isdigit()})
    y += ['', "2) YIL YIL YILLIK GETİRİ (%, komisyon dahil; yarım yıllar yıllığa çevrilmiştir)",
          f"   {'kural':15s} " + ' '.join(f"{yil:>8s}" for yil in yillar)]
    for ad, d in sonuclar.items():
        y.append(f"   {ad:15s} " + ' '.join(f"{d[yil]['yillik_pct']:+7.1f}%" if yil in d and 'yillik_pct' in d[yil]
                                             else f"{'-':>8s}" for yil in yillar))
    y += ['', "3) SAĞLAMLIK: parametre varyantları (yalnız bilgi; seçim yapılmaz) — Sharpe kuruluş / sınama"]
    for ad, liste in varyantlar.items():
        y.append(f"   {ad:15s} " + ' | '.join(
            f"{v['parametre']}: {f(v['kurulus'], '{:+.2f}')} / {f(v['sinama'], '{:+.2f}')}" for v in liste))
    y += ['', "4) KOMİSYON DUYARLILIĞI — sınama dönemi Sharpe (işlem başına ücret)",
          f"   {'kural':15s} " + ' '.join(f"{'%' + format(u * 100, '.3f'):>8s}" for u in meta['ucret_listesi'])]
    for ad, d in ucret_duyarlilik.items():
        y.append(f"   {ad:15s} " + ' '.join(f"{f(s, '{:+.2f}'):>8s}" for s in d))
    y += ['', 'Kurallar: ' + ' | '.join(f"{k}: {v}" for k, v in KURAL_ACIKLAMA.items()),
          'NOT: evren bugünün paritelerinden oluşur (delist olan yok); düşenleri alan kuralların sonucu iyimserdir.']
    return '\n'.join(y)


def calistir(ex, a, simdi_ms=None, log=print):
    simdi_ms = int(simdi_ms if simdi_ms is not None else time.time() * 1000)
    bas_ms = vs._ms(a.baslangic)
    bit_ms = min(vs._ms(a.bitis), simdi_ms) if a.bitis else simdi_ms
    depo = vs.MumDeposu(ex, a.onbellek, simdi_ms=simdi_ms)
    semboller = ['BTC/USDT'] + bf.evren_sec(ex, a.evren)
    log(f"Günlük veri indiriliyor: {len(semboller)} parite, {vs._tarih(bas_ms)[:10]} → {vs._tarih(bit_ms)[:10]}...")
    C, V = veri_yukle(depo, semboller, bas_ms, bit_ms, log)
    C, V, sabit = sabit_coinleri_ayikla(C, V)
    panel = Panel(C, V, a.min_hacim)
    bas, ayrim = pd.Timestamp(vs._tarih(bas_ms)[:10]), pd.Timestamp(a.ayrim)
    sonuclar, gunluk, varyantlar, ucret_duyarlilik = {}, {}, {}, {}
    for ad, kural in KURALLAR.items():
        hedef, dengele = kural(panel)
        s = simule(hedef, dengele, panel.R1, a.ucret)
        sonuclar[ad] = donem_ozetleri(s, bas, ayrim, a.butce)
        gunluk[ad] = s['getiri'][s.index > bas]
        ucret_duyarlilik[ad] = []
        for u in a.ucret_listesi:
            su = simule(hedef, dengele, panel.R1, u)
            o = donem_ozetleri(su, bas, ayrim, a.butce).get('sinama', {})
            ucret_duyarlilik[ad].append(o.get('sharpe'))
        if ad in VARYANTLAR:
            varyantlar[ad] = []
            for prm in VARYANTLAR[ad]:
                hv, dv = kural(panel, **prm)
                ov = donem_ozetleri(simule(hv, dv, panel.R1, a.ucret), bas, ayrim, a.butce)
                varyantlar[ad].append({'parametre': ','.join(f"{k}={v}" for k, v in prm.items()),
                                       'kurulus': (ov.get('kurulus') or {}).get('sharpe'),
                                       'sinama': (ov.get('sinama') or {}).get('sharpe')})
    meta = {'baslangic': vs._tarih(bas_ms)[:10], 'bitis': vs._tarih(bit_ms)[:10], 'ayrim': a.ayrim,
            'evren': a.evren, 'parite': int(C.shape[1]), 'sabit_coin': len(sabit), 'min_hacim': a.min_hacim,
            'ucret': a.ucret, 'ucret_listesi': a.ucret_listesi, 'butce': a.butce, 'istek': depo.istek,
            'olusturma': vs._tarih(simdi_ms)}
    metin = rapor_metni(meta, sonuclar, varyantlar, ucret_duyarlilik)
    with open(a.cikti + '_rapor.txt', 'w', encoding='utf-8') as f:
        f.write(metin + '\n')
    with open(a.cikti + '_rapor.json', 'w', encoding='utf-8') as f:
        json.dump({'meta': meta, 'sonuclar': sonuclar, 'varyantlar': varyantlar,
                   'ucret_duyarlilik': ucret_duyarlilik}, f, ensure_ascii=False, indent=1, default=str)
    pd.DataFrame(gunluk).to_csv(a.cikti + '_gunluk.csv', index_label='tarih')
    log('\n' + metin)
    log(f"\nÇıktılar: {a.cikti}_rapor.txt, {a.cikti}_rapor.json, {a.cikti}_gunluk.csv")
    return {'meta': meta, 'sonuclar': sonuclar, 'varyantlar': varyantlar, 'ucret_duyarlilik': ucret_duyarlilik,
            'gunluk': gunluk, 'panel': panel}


def arguman_ayristirici():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--baslangic', default='2023-01-01')
    ap.add_argument('--bitis', default=None, help='varsayılan: bugün')
    ap.add_argument('--ayrim', default='2025-01-01', help='kuruluş / sınama dönemi ayrımı')
    ap.add_argument('--evren', type=int, default=150, help='bugünün en hacimli N USDT paritesi (+ BTC)')
    ap.add_argument('--min-hacim', type=float, default=5e6, help='likitlik: 30 günlük medyan günlük hacim (USDT)')
    ap.add_argument('--ucret', type=float, default=0.0015, help='işlem başına komisyon + kayma (0.0015 = %%0.15)')
    ap.add_argument('--ucret-listesi', type=float, nargs='+', default=[0.00075, 0.0015, 0.0025])
    ap.add_argument('--butce', type=float, default=90.0, help='raporda günlük USDT karşılığı için bütçe')
    ap.add_argument('--onbellek', default=os.path.join(KOK, 'sim_onbellek'))
    ap.add_argument('--cikti', default=os.path.join(KOK, 'strateji'))
    return ap


def main(argv=None):
    a = arguman_ayristirici().parse_args(argv)
    import ccxt
    ex = ccxt.binance({'enableRateLimit': True, 'options': {'defaultType': 'spot'}})
    ex.rateLimit = max(ex.rateLimit or 0, 100)   # botla aynı IP limiti paylaşılıyor
    try:
        calistir(ex, a)
    except (ccxt.NetworkError, ccxt.ExchangeNotAvailable, vs.VeriYok) as e:
        print(f"\n❌ Binance fiyat verisine erişilemedi: {type(e).__name__}: {str(e)[:300]}\n"
              f"   Bot sunucusunda çalıştırın. İndirilen veri {a.onbellek} içinde; tekrar çalıştırınca kaldığı yerden sürer.")
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
