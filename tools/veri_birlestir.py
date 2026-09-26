# -*- coding: utf-8 -*-
"""VERİ BİRLEŞTİRME: dağınık CSV'leri iki ana dosyada toplar, başka bir makinenin eğitim verisini içeri alır.

Hedef düzen (her makinede 3 veri dosyası):
  core_islem_verileri_v2.csv  bu hesabın TÜM işlem kayıtları (bot yazar; hesaba özeldir, paylaşılmaz)
  shadow_sinyaller.csv        girilmeyen sinyaller (bot yazar, etiketleyici okur)
  etiketli_sinyaller.csv      AI'ın eğitim verisi: core + shadow + backfill (makineler arasında PAYLAŞILAN dosya)

Birleştirme (--uygula, bot DURMUŞKEN):
  core_islem_verileri.csv (V1, artık yazılmıyor) + core_islem_verileri*.onarildi.csv (kurtarılmış geçmiş)
      -> core_islem_verileri_v2.csv  (aynı işlem satırı [zaman + sembol + çıkış tipi] bir kez yazılır)
  backfill_sinyaller.csv -> etiketli_sinyaller.csv  (aynı Anahtar bir kez yazılır)
Kaynak dosyalar SİLİNMEZ, veri_arsiv_<tarih>/ klasörüne taşınır.

Başka makinenin eğitim verisini almak (bot çalışırken de olur):
  venv/bin/python tools/veri_birlestir.py --ekle /root/gelen_etiketli.csv --uygula
İki makine etiketli_sinyaller.csv dosyalarını karşılıklı gönderip --ekle ile aldıkça eğitim verileri aynı olur.
İşlem kayıtları hesaba özel olduğu için --ekle yalnız eğitim verisini birleştirir.

Kullanım (bot klasöründe, botun Python'uyla: pandas gerekir):
  venv/bin/python tools/veri_birlestir.py            # KURU: yalnız planı gösterir
  venv/bin/python tools/veri_birlestir.py --uygula
"""
import argparse
import csv
import datetime
import glob
import os
import shutil
import sys

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _yol in (KOK, os.path.join(KOK, 'tools')):
    if _yol not in sys.path:
        sys.path.insert(0, _yol)

import temiz_kurulum as tk  # noqa: E402
try:
    from sniper.csv_kayit import atomik_yaz, dosya_kilidi  # noqa: E402
    from sniper.etiket_deposu import TUM_KOLONLAR, VERI_YOK  # noqa: E402
    from sniper.etiketleme import ETIKET_SURUMU  # noqa: E402
except ImportError as _e:   # sistem python3'ünde pandas / pandas_ta yok; botun venv'i gerekir
    sys.exit(f"❌ {_e}\nBu aracı botun Python'uyla çalıştırın:  cd {KOK} && venv/bin/python tools/veri_birlestir.py")

V1 = ['Islem_Zamani', 'Sembol', 'Sinyal', 'Kasa_Tipi', 'Giris_RSI', 'Giris_Vol_Oran', 'Giris_ATR_Pct',
      'Giris_Fiyat', 'Cikis_Fiyat', 'Kar_Orani', 'Net_Kar_USDT', 'Cikis_Tipi', 'Sure_Saat']
ISLEM_HEDEF = 'core_islem_verileri_v2.csv'
ISLEM_KAYNAKLARI = ['core_islem_verileri.csv', 'core_islem_verileri*.onarildi.csv']
EGITIM_HEDEF = 'etiketli_sinyaller.csv'
EGITIM_KAYNAKLARI = ['backfill_sinyaller.csv']


def csv_oku(yol):
    """(başlık, [satır sözlükleri]). Başlıksız eski V1 dosyası V1 kolonlarıyla okunur. Başlıkla aynı uzunlukta
    olmayan satır varsa ValueError (kaymış dosya birleştirilmez: tools/csv_onar.py)."""
    with open(yol, encoding='utf-8-sig', newline='') as f:
        satirlar = [r for r in csv.reader(f) if r and any(x.strip() for x in r)]
    if not satirlar:
        return [], []
    if satirlar[0][0] in ('Islem_Zamani', 'Anahtar', 'Ts'):
        baslik, govde = satirlar[0], satirlar[1:]
    elif all(len(r) == len(V1) for r in satirlar):
        baslik, govde = list(V1), satirlar
    else:
        raise ValueError("başlık yok ve satırlar V1 biçiminde değil")
    kayik = sum(1 for r in govde if len(r) != len(baslik))
    if kayik:
        raise ValueError(f"{kayik} satır başlıkla aynı uzunlukta değil (kolon kayması; tools/csv_onar.py ile onarın)")
    return list(baslik), [dict(zip(baslik, r)) for r in govde]


def islem_anahtari(r):
    return (r.get('Islem_Zamani', '').strip(), r.get('Sembol', '').strip(), r.get('Cikis_Tipi', '').strip())


def _dolu(r):
    return sum(1 for v in r.values() if str(v).strip())


def islem_tercihi(yeni, eski):
    """Aynı işlemin daha dolu kaydı (ör. feature'lı V2 satırı) V1 satırına tercih edilir."""
    return _dolu(yeni) > _dolu(eski)


def egitim_tercihi(yeni, eski):
    """Aynı sinyalde güncel etiket sürümü ve gerçek etiket (VERI_YOK değil) tercih edilir."""
    if yeni.get('Etiket_Surumu') == ETIKET_SURUMU and eski.get('Etiket_Surumu') != ETIKET_SURUMU:
        return True
    return eski.get('Etiket_Sonuc') == VERI_YOK and yeni.get('Etiket_Sonuc') not in ('', VERI_YOK)


def _zaman(r):
    try:
        return datetime.datetime.fromisoformat(r.get('Islem_Zamani', '').strip())
    except ValueError:
        return datetime.datetime.max


def birlestir(hedef, kaynaklar, anahtar, tercih, varsayilan_baslik, sirala=None):
    """Dosyalara dokunmaz. Dönen: (başlık, satırlar, rapor, hatalı kaynaklar)."""
    if os.path.exists(hedef):
        baslik, satirlar = csv_oku(hedef)
    else:
        baslik, satirlar = [], []
    baslik = baslik or list(varsayilan_baslik)
    indeks = {}
    for i, r in enumerate(satirlar):
        indeks.setdefault(anahtar(r), i)
    rapor, hatali = {}, {}
    for yol in kaynaklar:
        try:
            kb, ks = csv_oku(yol)
        except (ValueError, OSError) as e:
            hatali[yol] = str(e)
            continue
        baslik += [k for k in kb if k not in baslik]
        eklenen = guncellenen = 0
        for r in ks:
            a = anahtar(r)
            if a not in indeks:
                indeks[a] = len(satirlar)
                satirlar.append(r)
                eklenen += 1
            elif tercih(r, satirlar[indeks[a]]):
                satirlar[indeks[a]] = r
                guncellenen += 1
        rapor[yol] = {'okunan': len(ks), 'eklenen': eklenen, 'guncellenen': guncellenen}
    if sirala:
        satirlar.sort(key=sirala)
    return baslik, satirlar, rapor, hatali


def _kaynaklar(kok, desenler, haric):
    yollar = sorted({p for d in desenler for p in glob.glob(os.path.join(kok, d))})
    return [p for p in yollar if os.path.abspath(p) != os.path.abspath(haric)]


def _yaz_plan(baslik, hedef, once, rapor, hatali, sonuc):
    print(f"\n{baslik} -> {os.path.basename(hedef)} (şu an {once} satır)")
    for yol, r in rapor.items():
        ek = f", {r['guncellenen']} daha dolu kayıtla güncellenir" if r['guncellenen'] else ''
        print(f"   {os.path.basename(yol):48s} {r['okunan']:6d} satır, {r['eklenen']:6d} yeni{ek}")
    for yol, neden in hatali.items():
        print(f"   ⚠️ {os.path.basename(yol)} ATLANDI: {neden}")
    if not rapor and not hatali:
        print("   birleştirilecek dosya yok")
    print(f"   sonuç: {sonuc} satır")


def calistir(kok=KOK, ekle=(), uygula=False, simdi=None):
    kok = os.path.abspath(kok)
    ekle = [os.path.abspath(p) for p in ekle]
    yalniz_egitim = bool(ekle)
    islem_hedef, egitim_hedef = os.path.join(kok, ISLEM_HEDEF), os.path.join(kok, EGITIM_HEDEF)
    for p in ekle:
        if not os.path.exists(p):
            print(f"❌ {p} bulunamadı")
            return 2

    for hedef in (islem_hedef, egitim_hedef):
        if os.path.exists(hedef):
            try:
                csv_oku(hedef)
            except ValueError as e:
                print(f"❌ {os.path.basename(hedef)} okunamadı: {e}. Önce onarın; hiçbir şey değiştirilmedi.")
                return 2

    planlar = []
    if not yalniz_egitim:
        kaynak = _kaynaklar(kok, ISLEM_KAYNAKLARI, islem_hedef)
        once = len(csv_oku(islem_hedef)[1]) if os.path.exists(islem_hedef) else 0
        b, s, r, h = birlestir(islem_hedef, kaynak, islem_anahtari, islem_tercihi, V1, sirala=_zaman)
        _yaz_plan("İŞLEM KAYDI (bu hesabın işlemleri)", islem_hedef, once, r, h, len(s))
        planlar.append((islem_hedef, b, s, r, islem_anahtari))
    kaynak = ekle + ([] if yalniz_egitim else _kaynaklar(kok, EGITIM_KAYNAKLARI, egitim_hedef))
    once = len(csv_oku(egitim_hedef)[1]) if os.path.exists(egitim_hedef) else 0
    b, s, r, h = birlestir(egitim_hedef, kaynak, lambda x: x.get('Anahtar', ''), egitim_tercihi, TUM_KOLONLAR)
    _yaz_plan("EĞİTİM VERİSİ (makineler arasında paylaşılan)", egitim_hedef, once, r, h, len(s))
    planlar.append((egitim_hedef, b, s, r, lambda x: x.get('Anahtar', '')))

    tasinacak = [y for _, _, _, r, _ in planlar for y in r if os.path.dirname(y) == kok]
    damga = (simdi or datetime.datetime.now()).strftime('%Y%m%d_%H%M%S')
    arsiv = os.path.join(kok, f'veri_arsiv_{damga}')
    if tasinacak:
        print(f"\nBirleştirilen kaynaklar arşive taşınacak: {', '.join(os.path.basename(y) for y in tasinacak)}"
              f"\n   -> {arsiv}")
    disarida = [y for _, _, _, r, _ in planlar for y in r if os.path.dirname(y) != kok]
    if disarida:
        print(f"Bot klasörü dışındaki dosyaya dokunulmaz: {', '.join(disarida)}")

    adlar = {'shadow_labeler.py', 'backfill_sinyaller.py', 'ai_trainer.py'} | (set() if yalniz_egitim else {'ai_bot.py'})
    surecler = [s for s in (tk.calisan_bot_surecleri() or []) if any(a in s for a in adlar)]
    if surecler:
        kim = "etiketleyici/backfill/trainer" if yalniz_egitim else "bot ya da gece işleri"
        print(f"\n{'❌' if uygula else '⚠️'} {kim} çalışıyor; bitmesini bekleyin ya da durdurun:\n   " + "\n   ".join(surecler))
        if uygula:
            return 3
    if not uygula:
        print("\nKURU ÇALIŞTIRMA: hiçbir şey değiştirilmedi. Uygulamak için aynı komutu --uygula ile çalıştırın.")
        return 0

    for hedef, baslik, satirlar, rapor, anahtar in planlar:
        if not rapor:
            continue
        with dosya_kilidi(hedef):
            atomik_yaz(hedef, baslik, [[r.get(k, '') for k in baslik] for r in satirlar])
        # doğrulama: her kaynak satırının anahtarı hedefte olmalı; değilse kaynak ARŞİVLENMEZ
        yazilan = {anahtar(r) for r in csv_oku(hedef)[1]}
        for yol in list(rapor):
            eksik = sum(1 for r in csv_oku(yol)[1] if anahtar(r) not in yazilan)
            if eksik:
                print(f"❌ {os.path.basename(yol)}: {eksik} satır hedefte bulunamadı; kaynak yerinde bırakıldı")
                tasinacak = [y for y in tasinacak if y != yol]
    if tasinacak:
        os.makedirs(arsiv, exist_ok=True)
        for yol in tasinacak:
            shutil.move(yol, os.path.join(arsiv, os.path.basename(yol)))
    print(f"\n✅ Birleştirme tamam. İşlem kaydı: {ISLEM_HEDEF}, eğitim verisi: {EGITIM_HEDEF}."
          + (f"\n   Kaynaklar: {arsiv} (sorun yoksa 1-2 hafta sonra silebilirsiniz)" if tasinacak else ''))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--kok', default=KOK, help='bot klasörü (varsayılan: bu aracın bir üst klasörü)')
    ap.add_argument('--ekle', nargs='+', default=[], metavar='DOSYA',
                    help='başka makineden gelen etiketli_sinyaller.csv (yalnız eğitim verisi birleştirilir)')
    ap.add_argument('--uygula', action='store_true', help='planı uygula (yoksa yalnız gösterir)')
    a = ap.parse_args(argv)
    return calistir(a.kok, a.ekle, a.uygula)


if __name__ == '__main__':
    sys.exit(main())
