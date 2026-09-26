# -*- coding: utf-8 -*-
"""Kolon kayması yaşamış CSV yedeklerini deterministik olarak onarır.

Sorun: V2 feature seti büyüdükçe (Sym_ADX/BTC_ADX, sonra Kapali_Mum_Onay/OB_Oran)
yeni kolonlar AI_Skor'dan ÖNCE eklendi ama dosya başlığı güncellenmedi. Sonuç:
25 kolonluk başlığın altında 27/29 alanlı satırlar. Her şema sürümünün alan sayısı
farklı olduğundan, satırın hangi sürümle yazıldığı alan sayısından kesin bilinir.

Kullanım:
  python tools/csv_onar.py core_islem_verileri_v2.csv.yedek
      -> core_islem_verileri_v2.csv.yedek.onarildi.csv üretir (girdi değişmez)
  python tools/csv_onar.py core_islem_verileri_v2.csv.yedek --birlestir core_islem_verileri_v2.csv
      -> onarılan satırları hedef dosyaya tekrarsız ekler (atomik; hedefin yedeği alınır).
         BOTU DURDURARAK çalıştırın: dosya okunurken bot yeni satır eklerse işlem iptal edilir.
  python tools/csv_onar.py core_islem_verileri.csv.legacy_123 --v1-basliksiz
      -> başlıksız eski V1 dosyasına başlık ekler

Tanınmayan alan sayısına sahip satırlar <girdi>.onarilamayan.csv dosyasına yazılır.
"""
import argparse
import csv
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sniper.csv_kayit import atomik_yaz  # noqa: E402

V1_TEMEL = ['Islem_Zamani', 'Sembol', 'Sinyal', 'Kasa_Tipi', 'Giris_RSI', 'Giris_Vol_Oran',
            'Giris_ATR_Pct', 'Giris_Fiyat', 'Cikis_Fiyat', 'Kar_Orani', 'Net_Kar_USDT',
            'Cikis_Tipi', 'Sure_Saat']
SHADOW_TEMEL = ['Ts', 'Sinyal_Zamani', 'Sembol', 'Sebep', 'Sinyal', 'Fiyat',
                'Giris_RSI', 'Giris_Vol_Oran', 'Giris_ATR_Pct']

_F_A = ['Pump_3s', 'Pump_6s', 'Zirve_Uzaklik', 'EMA1h_Uzaklik', 'EMA15m_Uzaklik',
        'BTC_1h_Degisim', 'BTC_EMA_Uzaklik', 'Saat', 'Gun', 'Stop_Sayisi']
_SKOR = ['AI_Skor', 'Filtre_Skor']
# Botun tarihsel feature şeması sürümleri (ai_bot.py V2_FEATURE_MAP geçmişi)
FEATURE_SURUMLERI = [
    _F_A + _SKOR,                                                        # v2a
    _F_A + ['Sym_ADX', 'BTC_ADX'] + _SKOR,                                # v2b
    _F_A + ['Sym_ADX', 'BTC_ADX', 'Kapali_Mum_Onay', 'OB_Oran'] + _SKOR,  # v2c
]


def surum_haritasi(temel):
    return {len(temel) + len(f): temel + f for f in FEATURE_SURUMLERI}


def tur_belirle(baslik):
    if baslik and baslik[0] == 'Islem_Zamani':
        return 'core', V1_TEMEL
    if baslik and baslik[0] == 'Ts':
        return 'shadow', SHADOW_TEMEL
    raise SystemExit(f"Tanınmayan başlık: {baslik[:3]}... (core V2 veya shadow bekleniyordu)")


def onar(satirlar, temel):
    harita = surum_haritasi(temel)
    hedef_kolonlar = list(harita[max(harita)])
    onarilan, onarilamayan = [], []
    for r in satirlar:
        sema = harita.get(len(r))
        if sema is None:
            onarilamayan.append(r)
            continue
        d = dict(zip(sema, r))
        onarilan.append([d.get(k, '') for k in hedef_kolonlar])
    return hedef_kolonlar, onarilan, onarilamayan


def anahtar(tur, kolonlar, satir):
    d = dict(zip(kolonlar, satir))
    if tur == 'core':
        return (d.get('Islem_Zamani'), d.get('Sembol'), d.get('Cikis_Tipi'))
    return (d.get('Ts'), d.get('Sembol'))


def _imza(yol):
    st = os.stat(yol)
    return st.st_size, st.st_mtime_ns


def birlestir(tur, kolonlar, satirlar, hedef):
    imza = _imza(hedef)
    with open(hedef, encoding='utf-8-sig', newline='') as f:
        mevcut = list(csv.reader(f))
    h_baslik, h_govde = mevcut[0], mevcut[1:]
    kayik = sum(1 for r in h_govde if len(r) != len(h_baslik))
    if kayik:
        raise SystemExit(f"Hedef dosyada {kayik} kaymış satır var; önce hedefi onarın.")
    birlesik = h_baslik + [k for k in kolonlar if k not in h_baslik]
    pad = len(birlesik) - len(h_baslik)
    govde = [r + [''] * pad for r in h_govde]
    gorulen = {anahtar(tur, birlesik, r) for r in govde}
    eklenen = 0
    for r in satirlar:
        d = dict(zip(kolonlar, r))
        yeni = [d.get(k, '') for k in birlesik]
        a = anahtar(tur, birlesik, yeni)
        if a in gorulen:
            continue
        gorulen.add(a)
        govde.append(yeni)
        eklenen += 1
    zaman_kolonu = 'Islem_Zamani' if tur == 'core' else 'Ts'
    i = birlesik.index(zaman_kolonu)
    govde.sort(key=lambda r: (float(r[i]) if tur == 'shadow' and r[i] else 0.0, r[i]))
    yedek = f"{hedef}.onarim_oncesi_{int(time.time())}"
    shutil.copy2(hedef, yedek)
    if _imza(hedef) != imza:   # iyimser eşzamanlılık: bot bu arada satır eklediyse ezme
        raise SystemExit(f"{hedef} okunurken değişti (bot çalışıyor olabilir). Botu durdurup tekrar deneyin; "
                         f"hiçbir şey yazılmadı.")
    atomik_yaz(hedef, birlesik, govde)
    return eklenen, yedek


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('girdi')
    ap.add_argument('--birlestir', metavar='HEDEF', help='onarılan satırları bu CSV ile tekrarsız birleştir')
    ap.add_argument('--v1-basliksiz', action='store_true', help='girdi başlıksız eski V1 dosyası')
    a = ap.parse_args()

    with open(a.girdi, encoding='utf-8-sig', newline='') as f:
        satirlar = list(csv.reader(f))
    if not satirlar:
        raise SystemExit("Girdi boş.")

    if a.v1_basliksiz:
        tur, kolonlar = 'core', V1_TEMEL
        uygun = [r for r in satirlar if len(r) == len(V1_TEMEL) and any(x.strip() for x in r)]
        onarilamayan = [r for r in satirlar if len(r) != len(V1_TEMEL)]
    else:
        tur, temel = tur_belirle(satirlar[0])
        govde = [r for r in satirlar[1:] if any(x.strip() for x in r)]
        kolonlar, uygun, onarilamayan = onar(govde, temel)

    print(f"{a.girdi}: {len(uygun)} satır onarıldı, {len(onarilamayan)} satır tanınamadı ({tur}).")
    if onarilamayan:
        yol = a.girdi + '.onarilamayan.csv'
        with open(yol, 'w', encoding='utf-8', newline='') as f:
            csv.writer(f, lineterminator='\n').writerows(onarilamayan)
        print(f"  tanınamayanlar -> {yol}")

    if a.birlestir:
        eklenen, yedek = birlestir(tur, kolonlar, uygun, a.birlestir)
        print(f"  {a.birlestir}: {eklenen} yeni satır eklendi (önceki hali: {yedek})")
    else:
        cikti = a.girdi + '.onarildi.csv'
        atomik_yaz(cikti, kolonlar, uygun)
        print(f"  çıktı -> {cikti}")


if __name__ == '__main__':
    main()
