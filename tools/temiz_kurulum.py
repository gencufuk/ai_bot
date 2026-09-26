# -*- coding: utf-8 -*-
"""TEMİZ BAŞLANGIÇ: /root'taki eski bot dosyalarını arşive taşır, V18.4'ü kurar, canlı verileri geri getirir.

Hiçbir şey SİLİNMEZ. Eski bot dosyalarının tamamı /root/eski_bot_<tarih>/ klasörüne TAŞINIR. Yeni sürüm
1-2 hafta sorunsuz çalışınca bu klasör tek komutla silinebilir (komut sonda yazdırılır).

Dokunulmayanlar: gizli dosya ve klasörler (.env, .ssh, .bashrc ...), venv/ (pyvenv.cfg içeren her klasör),
Redis dosyaları (*.rdb, *.aof), yedek_*.tgz, önceki arşivler, snap/, .sh betikleri, crontab'da adı geçen
dosyalar, başka bir botun verisi olabilecek *islem_verileri*.csv dosyaları (ör. ufuk_islem_verileri.csv),
bu paket ve tanınmayan her şey (liste hâlinde gösterilir). Açık pozisyonlar Redis'te durduğu için etkilenmez.
Taşınacak bir dosyayı çalıştıran süreç varsa (bot, trainer ya da aynı klasördeki başka bir bot) uygulanmaz.

Yeni sürümün kullandığı canlı veri eski kurulumdan KOPYALANIR (arşivdeki asılları durur):
  core_islem_verileri.csv, core_islem_verileri_v2.csv, shadow_sinyaller.csv, core_xgboost_model.json
  (+ varsa etiketli_sinyaller.csv, backfill_sinyaller.csv, egitim_raporu.json, model kartı)
Bilerek getirilmeyenler: filter_model.json (Ağustos verisini ezberlemiş eski filtre), eski loglar,
eski etiketleyici çıktısı, *.yedek / *.bak kopyaları.

Kullanım (bot DURDURULMUŞKEN):
  cd /root && unzip ai_bot_v18_4_paket.zip
  python3 ai_bot_v18_4_paket/tools/temiz_kurulum.py            # KURU: yalnız planı gösterir
  python3 ai_bot_v18_4_paket/tools/temiz_kurulum.py --uygula   # uygular
Geri almak:
  python3 /root/tools/temiz_kurulum.py --geri-al /root/eski_bot_<tarih>
"""
import argparse
import datetime
import fnmatch
import json
import os
import shutil
import subprocess
import sys

PAKET = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KOD = ['ai_bot.py', 'ai_trainer.py', 'shadow_labeler.py', 'backfill_sinyaller.py', 'sniper', 'tools']
GECMIS = ['core_islem_verileri_v2_gecmis.onarildi.csv', 'core_islem_verileri_v2.csv.yedek.onarildi.csv']
CANLI_VERI = ['core_islem_verileri.csv', 'core_islem_verileri_v2.csv', 'shadow_sinyaller.csv',
              'core_xgboost_model.json', 'core_xgboost_model.kart.json', 'etiketli_sinyaller.csv',
              'backfill_sinyaller.csv', 'egitim_raporu.json']
GETIRILMEYEN = {
    'filter_model.json': "eski filtre modeli: Ağustos verisini ezberlemiş, örneklem dışında gürültü (ANALIZ §2)",
    'shadow_sinyaller_etiketli.csv': "eski etiketleyicinin çıktısı; yeni sürüm etiketli_sinyaller.csv üretir",
}
BOT_UZANTILARI = ('.py', '.pyc', '.csv', '.json', '.log', '.txt', '.md', '.out', '.pkl', '.bak', '.yedek',
                  '.tmp', '.old', '.orig')
BOT_ADLARI = ('*.bozuk_*', '*.legacy_*', '*.emekli*', '*.yedek*', '*.onarildi*', 'nohup.out', 'screenlog.*',
              'v184_sim_*')
BOT_KLASORLERI = ('__pycache__', 'sniper', 'tools', 'tests', 'dagitim', 'sim_onbellek')
DOKUNMA_ADLARI = ('*.rdb', '*.aof', 'appendonlydir', 'yedek_*', 'eski_bot_*', 'v184_kaldirilan_*', 'snap',
                  'venv', 'env', 'ai_bot_v18_4_paket*', '*.sh')


def _eslesir(ad, desenler):
    return any(fnmatch.fnmatch(ad, d) for d in desenler)


KENDI_VERIMIZ = ('core_islem_verileri*',)


def crontab_metni():
    try:
        r = subprocess.run(['crontab', '-l'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True)
    except FileNotFoundError:
        return ''
    return r.stdout if r.returncode == 0 else ''


def _cronda_geciyor(ad, cron):
    kelimeler = {os.path.basename(k.strip('\'";&|<>()')) for k in cron.split()}
    return ad in kelimeler


def siniflandir(hedef, paket=PAKET, cron=''):
    """(taşınacak, dokunulmayan, tanınmayan): her biri [(ad, neden)]."""
    tasinacak, dokunma, bilinmeyen = [], [], []
    paket_gercek = os.path.realpath(paket)
    for ad in sorted(os.listdir(hedef)):
        yol = os.path.join(hedef, ad)
        if os.path.realpath(yol) == paket_gercek:
            dokunma.append((ad, 'kurulum paketi'))
        elif cron and ad not in KOD and _cronda_geciyor(ad, cron):
            dokunma.append((ad, "crontab'da kullanılıyor (başka bir iş olabilir)"))
        elif fnmatch.fnmatch(ad, '*islem_verileri*.csv') and not _eslesir(ad, KENDI_VERIMIZ):
            dokunma.append((ad, 'başka bir botun verisi olabilir: gerekmiyorsa elle taşıyın'))
        elif ad.startswith('.'):
            dokunma.append((ad, 'gizli dosya/klasör (.env, ssh, ayarlar)'))
        elif os.path.isdir(yol) and os.path.exists(os.path.join(yol, 'pyvenv.cfg')):
            dokunma.append((ad, 'Python sanal ortamı'))
        elif _eslesir(ad, DOKUNMA_ADLARI):
            dokunma.append((ad, 'korunan (Redis / yedek / arşiv / betik)'))
        elif os.path.isdir(yol):
            (tasinacak if ad in BOT_KLASORLERI else bilinmeyen).append(
                (ad, 'bot klasörü' if ad in BOT_KLASORLERI else 'tanınmayan klasör'))
        elif ad.lower().endswith(BOT_UZANTILARI) or _eslesir(ad, BOT_ADLARI):
            tasinacak.append((ad, 'bot dosyası'))
        else:
            bilinmeyen.append((ad, 'tanınmayan dosya'))
    return tasinacak, dokunma, bilinmeyen


def veri_gecerli_mi(yol):
    """CSV adıyla kaydedilmiş model dosyası (bu projede yaşandı) ya da bozuk model geri getirilmez."""
    with open(yol, 'rb') as f:
        bas = f.read(64).lstrip()
    if yol.endswith('.csv'):
        return (False, "CSV değil, JSON içeriyor (model dosyası CSV adıyla kaydedilmiş)") if bas.startswith(b'{') \
            else (True, '')
    if os.path.basename(yol) == 'core_xgboost_model.json':
        try:
            with open(yol, encoding='utf-8') as f:
                return (True, '') if 'learner' in json.load(f) else (False, "XGBoost modeli değil")
        except Exception as e:  # noqa: BLE001
            return False, f"okunamadı: {e}"
    return True, ''


def calisan_bot_surecleri(adlar=None):
    """Komut satırında bu dosya adlarından biri geçen süreçler (bot, trainer, labeler ya da aynı klasördeki
    başka bir bot). ps yoksa None."""
    adlar = set(adlar or []) | {'ai_bot.py', 'ai_trainer.py', 'shadow_labeler.py', 'backfill_sinyaller.py'}
    try:
        r = subprocess.run(['ps', '-eo', 'pid=,args='], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           universal_newlines=True)
    except FileNotFoundError:
        return None
    kendim, sonuc = {os.getpid(), os.getppid()}, []
    for satir in r.stdout.splitlines():
        parca = satir.strip().split(None, 1)
        if len(parca) < 2 or not parca[0].isdigit() or int(parca[0]) in kendim:
            continue
        if {os.path.basename(t) for t in parca[1].split()} & adlar:
            sonuc.append(satir.strip())
    return sonuc


def paket_kontrol(paket):
    eksik = [a for a in KOD + GECMIS if not os.path.exists(os.path.join(paket, a))]
    if eksik:
        return f"paket eksik: {eksik}"
    with open(os.path.join(paket, 'ai_bot.py'), encoding='utf-8', errors='replace') as f:
        if 'V18.4' not in f.read():
            return "paketteki ai_bot.py V18.4 değil"
    return None


def _yaz_liste(baslik, liste):
    print(f"\n{baslik} ({len(liste)}):")
    for ad, neden in liste:
        print(f"   {ad:52s} {neden}")
    if not liste:
        print("   -")


def kur(hedef, paket=PAKET, uygula=False, kontrol=True, simdi=None):
    hedef, paket = os.path.abspath(hedef), os.path.abspath(paket)
    if os.path.realpath(hedef) == os.path.realpath(paket):
        print("❌ Bu komut kurulum PAKETİNDEN çalıştırılmalı (ör. python3 /root/ai_bot_v18_4_paket/tools/temiz_kurulum.py)")
        return 2
    hata = paket_kontrol(paket)
    if hata:
        print(f"❌ {hata}")
        return 2
    tasinacak, dokunma, bilinmeyen = siniflandir(hedef, paket, crontab_metni())
    cakisan = [a for a in KOD + GECMIS if os.path.exists(os.path.join(hedef, a))
               and a not in {ad for ad, _ in tasinacak}]
    if cakisan:
        print(f"❌ Hedefte taşınamayan ve yeni dosyalarla aynı adlı öğeler var: {cakisan}. Önce bunları elle kaldırın.")
        return 2
    damga = (simdi or datetime.datetime.now()).strftime('%Y%m%d_%H%M%S')
    arsiv = os.path.join(hedef, f'eski_bot_{damga}')
    tasinan_adlar = {ad for ad, _ in tasinacak}
    getirilecek, reddedilen = [], []
    for ad in (a for a in CANLI_VERI if a in tasinan_adlar):
        gecerli, neden = veri_gecerli_mi(os.path.join(hedef, ad))
        (getirilecek.append(ad) if gecerli else reddedilen.append((ad, neden)))

    print(f"Hedef: {hedef}\nPaket: {paket}\nArşiv: {arsiv}")
    _yaz_liste("ARŞİVE TAŞINACAK (eski bot dosyaları)", tasinacak)
    _yaz_liste("DOKUNULMAYACAK", dokunma)
    _yaz_liste("TANINMAYAN — dokunulmayacak, gerekirse elle taşıyın", bilinmeyen)
    print(f"\nKURULACAK (paketten): {', '.join(KOD + GECMIS)}")
    print(f"ARŞİVDEN GERİ KOPYALANACAK canlı veri: {', '.join(getirilecek) or '-'}")
    for ad, neden in reddedilen:
        print(f"⚠️ GETİRİLMEYECEK: {ad} — {neden}; arşivde kalır, inceleyin")
    for ad, neden in GETIRILMEYEN.items():
        if ad in tasinan_adlar:
            print(f"GETİRİLMEYECEK: {ad} — {neden}")
    kurtarilabilir = [ad for ad, _ in tasinacak if _eslesir(ad, ('*.bozuk_*', '*.legacy_*'))]
    if kurtarilabilir:
        print(f"ℹ️ Arşivde kurtarılabilir veri olabilir (tools/csv_onar.py): {', '.join(kurtarilabilir)}")

    surecler = calisan_bot_surecleri({ad for ad, _ in tasinacak if ad.endswith(('.py', '.sh'))})
    if surecler:
        print("\n" + ("❌" if uygula else "⚠️") + " Taşınacak dosyaları kullanan süreçler var; uygulamadan önce durdurun "
              "(Ctrl+C / pkill -f ai_bot.py):\n   " + "\n   ".join(surecler))
        if uygula:
            return 3
    if surecler is None:
        print("\n⚠️ ps bulunamadı: botun DURDUĞUNDAN emin olun.")
    if not uygula:
        print("\nKURU ÇALIŞTIRMA: hiçbir şey değiştirilmedi. Uygulamak için aynı komutu --uygula ile çalıştırın.")
        return 0

    os.makedirs(arsiv)
    for ad, _ in tasinacak:
        shutil.move(os.path.join(hedef, ad), os.path.join(arsiv, ad))
    for ad in KOD + GECMIS:
        kaynak = os.path.join(paket, ad)
        if os.path.isdir(kaynak):
            shutil.copytree(kaynak, os.path.join(hedef, ad), ignore=shutil.ignore_patterns('__pycache__'))
        else:
            shutil.copy2(kaynak, os.path.join(hedef, ad))
    getirilen = []
    for ad in getirilecek:
        shutil.copy2(os.path.join(arsiv, ad), os.path.join(hedef, ad))
        getirilen.append(ad)
    with open(os.path.join(arsiv, 'kurulum_manifest.json'), 'w', encoding='utf-8') as f:
        json.dump({'tarih': damga, 'hedef': hedef, 'tasinan': sorted(tasinan_adlar), 'kurulan': KOD + GECMIS,
                   'geri_getirilen': getirilen, 'reddedilen': reddedilen}, f, ensure_ascii=False, indent=1)
    print(f"\n✅ {len(tasinacak)} öğe arşivlendi, V18.4 kuruldu, geri getirilen veri: {', '.join(getirilen) or '-'}")
    for ad, neden in reddedilen:
        print(f"⚠️ {ad} GETİRİLMEDİ: {neden}. Arşivdeki dosyayı inceleyin.")
    if 'core_xgboost_model.json' not in getirilen:
        print("⚠️ core_xgboost_model.json yok: bot bloklama modunda (AI_GOLGE_MOD=False) yeni alım yapmaz.")

    if kontrol:
        python = os.path.join(hedef, 'venv', 'bin', 'python')
        python = python if os.path.exists(python) else sys.executable
        print(f"\n--- Kurulum kontrolü ({python} tools/kurulum_kontrol.py)")
        subprocess.run([python, os.path.join(hedef, 'tools', 'kurulum_kontrol.py')], cwd=hedef)

    print(f"""
SONRAKİ ADIMLAR
1) Botu her zamanki komutunuzla başlatın (Telegram'a "CORE V18.4" mesajı gelir), ör.:
     cd {hedef} && nohup {hedef}/venv/bin/python ai_bot.py >> bot.log 2>&1 &
2) Gece işleri (crontab -e): mevcut 03:00 trainer satırınız aynı kalabilir; etiketleyiciyi ondan ÖNCE ekleyin
     45 2 * * * cd {hedef} && {hedef}/venv/bin/python shadow_labeler.py >> labeler.log 2>&1
     0 3 * * *  cd {hedef} && {hedef}/venv/bin/python ai_trainer.py >> trainer.log 2>&1
3) Bir kereye mahsus veri hattı (bot çalışırken de olur):
     cd {hedef} && venv/bin/python shadow_labeler.py && venv/bin/python backfill_sinyaller.py --gun 180 --evren 80
4) Paket klasörü ve zip artık gereksiz:  rm -rf {paket} {paket}.zip
5) 1-2 hafta sorunsuz çalışınca arşivi silin:  rm -rf {arsiv}
Geri almak için:  python3 {hedef}/tools/temiz_kurulum.py --geri-al {arsiv}""")
    return 0


def geri_al(hedef, arsiv, simdi=None):
    hedef, arsiv = os.path.abspath(hedef), os.path.abspath(arsiv)
    if not os.path.exists(os.path.join(arsiv, 'kurulum_manifest.json')):
        print(f"❌ {arsiv} bu araçla oluşturulmuş bir arşiv değil (kurulum_manifest.json yok)")
        return 2
    surecler = calisan_bot_surecleri()
    if surecler:
        print("❌ Çalışan bot süreçleri var, önce durdurun:\n   " + "\n   ".join(surecler))
        return 3
    damga = (simdi or datetime.datetime.now()).strftime('%Y%m%d_%H%M%S')
    kaldirilan = os.path.join(hedef, f'v184_kaldirilan_{damga}')
    # V18.4 dosyaları ve V18.4 döneminde yazılan veriler kaldırılan klasörüne (kaybolmaz)
    tasinacak, _, _ = siniflandir(hedef, paket=os.path.join(hedef, '__yok__'), cron=crontab_metni())
    os.makedirs(kaldirilan)
    for ad, _ in tasinacak:
        shutil.move(os.path.join(hedef, ad), os.path.join(kaldirilan, ad))
    for ad in os.listdir(arsiv):
        if ad != 'kurulum_manifest.json':
            shutil.move(os.path.join(arsiv, ad), os.path.join(hedef, ad))
    os.remove(os.path.join(arsiv, 'kurulum_manifest.json'))
    os.rmdir(arsiv)
    print(f"✅ Eski kurulum geri yüklendi. V18.4 dosyaları ve V18.4 döneminde yazılan veriler: {kaldirilan}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--hedef', default='/root', help='botun çalıştığı klasör (varsayılan /root)')
    ap.add_argument('--uygula', action='store_true', help='planı uygula (yoksa yalnız gösterir)')
    ap.add_argument('--geri-al', metavar='ARSIV', help='bu araçla oluşturulmuş arşivi geri yükle')
    ap.add_argument('--kontrolsuz', action='store_true', help='sonda kurulum_kontrol.py çalıştırma')
    a = ap.parse_args(argv)
    if a.geri_al:
        return geri_al(a.hedef, a.geri_al)
    return kur(a.hedef, uygula=a.uygula, kontrol=not a.kontrolsuz)


if __name__ == '__main__':
    sys.exit(main())
