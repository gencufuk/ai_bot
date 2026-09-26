# -*- coding: utf-8 -*-
"""V18.4 kurulumunu sunucuda doğrular. Bot DURDURULMUŞKEN, bot klasöründe çalıştırın:

    cd /root && /root/venv/bin/python tools/kurulum_kontrol.py

Hiçbir dosyayı değiştirmez. Her kontrol ✅ (tamam) / ⚠️ (dikkat) / ❌ (bot bu hâliyle başlatılmamalı).
"""
import csv
import glob
import importlib
import os
import sys

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KOK)
SONUC = {'✅': 0, '⚠️': 0, '❌': 0}


def yaz(durum, mesaj):
    SONUC[durum] += 1
    print(f"{durum} {mesaj}")


def python_ve_paketler():
    v = sys.version_info
    yaz('✅' if v >= (3, 10) else '❌', f"Python {v.major}.{v.minor}.{v.micro}")
    dagitim = {'sklearn': 'scikit-learn', 'dotenv': 'python-dotenv', 'pandas_ta': 'pandas_ta'}
    for paket in ['pandas', 'numpy', 'xgboost', 'sklearn', 'pandas_ta', 'ccxt', 'redis', 'aiohttp', 'dotenv']:
        try:
            importlib.import_module(paket)
            try:
                from importlib.metadata import version
                surum = version(dagitim.get(paket, paket))
            except Exception:  # noqa: BLE001
                surum = ''
            yaz('✅', f"{paket} {surum}")
        except Exception as e:  # noqa: BLE001
            yaz('❌', f"{paket} import edilemedi: {e}")
    try:
        import pandas as pd
        if tuple(int(x) for x in pd.__version__.split('.')[:2]) < (2, 0):
            yaz('❌', "pandas >= 2.0 gerekli")
    except Exception:  # noqa: BLE001
        pass


def kod_dosyalari():
    for ad in ['ai_bot.py', 'ai_trainer.py', 'shadow_labeler.py', 'backfill_sinyaller.py']:
        yol = os.path.join(KOK, ad)
        if not os.path.exists(yol):
            yaz('❌', f"{ad} yok")
            continue
        icerik = open(yol, encoding='utf-8', errors='replace').read()
        eski = (ad == 'ai_bot.py' and 'V18.4' not in icerik) or (ad == 'ai_trainer.py' and 'AI TRAINER V3' not in icerik) \
            or (ad == 'shadow_labeler.py' and 'LABELER V2' not in icerik)
        yaz('❌' if eski else '✅', f"{ad}{' ESKİ SÜRÜM — yeni dosya kopyalanmamış' if eski else ''}")
    for modul in ['csv_kayit', 'risk_motoru', 'ozellikler', 'etiketleme', 'etiket_deposu', 'model_karti']:
        try:
            importlib.import_module(f'sniper.{modul}')
            yaz('✅', f"sniper/{modul}.py")
        except Exception as e:  # noqa: BLE001
            yaz('❌', f"sniper/{modul}.py yüklenemedi (sniper klasörü kopyalandı mı?): {e}")


def env_ve_redis():
    yol = os.path.join(KOK, '.env')
    if not os.path.exists(yol):
        yaz('❌', ".env yok (API anahtarları). Eski .env dosyasını SİLMEYİN / geri koyun.")
        return
    anahtarlar = {s.split('=', 1)[0].strip() for s in open(yol, encoding='utf-8', errors='replace') if '=' in s}
    eksik = {'BINANCE_API_KEY', 'BINANCE_SECRET_KEY', 'TELEGRAM_TOKEN', 'TELEGRAM_CHAT_ID'} - anahtarlar
    yaz('❌' if eksik else '✅', f".env {'eksik: ' + ', '.join(sorted(eksik)) if eksik else 'tamam (değerler gösterilmez)'}")
    if 'MANUEL_COINLER' in anahtarlar:
        yaz('✅', ".env MANUEL_COINLER tanımlı")
    try:
        import redis
        r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True, socket_timeout=3)
        r.ping()
        yaz('✅', f"Redis bağlantısı | açık pozisyon: {r.hlen('PORTFOY:islem_listesi')}")
    except Exception as e:  # noqa: BLE001
        yaz('❌', f"Redis'e bağlanılamadı: {e}")


def modeller():
    from sniper.model_karti import ModelYuvasi
    for ad, rol, esik, yon in [('core_xgboost_model.json', 'core', 0.65, 'min'), ('filter_model.json', 'filtre', 0.45, 'max')]:
        yol = os.path.join(KOK, ad)
        if not os.path.exists(yol):
            yaz('⚠️' if rol == 'filtre' else '⚠️', f"{ad} yok ({'filtre devre dışı' if rol == 'filtre' else 'bloklama modunda bot başlamaz'})")
            continue
        y = ModelYuvasi(yol, rol, esik, yon)
        mesaj = y.yenile() or ''
        if y.yuklu:
            ek = ' — ANALIZ §2: doğrulanmamış eski filtre, kaldırılması önerilir' if rol == 'filtre' and not y.kart else ''
            yaz('⚠️' if ek else '✅', f"{ad}: {len(y.featurelar)} feature, eşik {y.esik:.3f} ({'kartlı' if y.kart else 'legacy'}){ek}")
        else:
            yaz('❌', f"{ad}: {mesaj}")


def csv_dosyalari():
    for yol in sorted(glob.glob(os.path.join(KOK, '*.csv'))):
        ad = os.path.basename(yol)
        if ad.startswith('v184_sim_'):
            continue  # simülasyon çıktısı, bot verisi değil
        with open(yol, 'rb') as f:
            bas = f.read(64)
        if bas.lstrip().startswith(b'{'):
            yaz('❌', f"{ad}: CSV değil, JSON içeriyor (model dosyası üstüne mi kopyalandı?). Yedekten geri alın.")
            continue
        with open(yol, encoding='utf-8-sig', newline='', errors='replace') as f:
            satirlar = list(csv.reader(f))
        if not satirlar:
            yaz('⚠️', f"{ad}: boş")
            continue
        baslik, govde = satirlar[0], satirlar[1:]
        basliksiz = baslik[0] not in ('Islem_Zamani', 'Ts', 'Anahtar')
        kayik = sum(1 for r in govde if len(r) != len(baslik))
        bos = sum(1 for r in govde if r and not any(x.strip() for x in r))
        if basliksiz:
            yaz('⚠️', f"{ad}: başlıksız eski format ({len(satirlar)} satır) — V18.4 ilk yazımda başlık ekler/arşivler")
        elif kayik or bos:
            yaz('⚠️', f"{ad}: {len(govde)} satır, {kayik} kaymış, {bos} tamamen boş satır (tools/csv_onar.py)")
        else:
            yaz('✅', f"{ad}: {len(govde)} satır, başlık tutarlı")
    arsiv = glob.glob(os.path.join(KOK, '*.bozuk_*')) + glob.glob(os.path.join(KOK, '*.legacy_*'))
    for yol in arsiv:
        yaz('⚠️', f"arşivlenmiş dosya: {os.path.basename(yol)} — içinde kurtarılabilir veri olabilir")


def main():
    print(f"Bot klasörü: {KOK}\n")
    for baslik, fn in [('Python ve paketler', python_ve_paketler), ('Kod dosyaları', kod_dosyalari),
                       ('.env ve Redis', env_ve_redis), ('Modeller', modeller), ('Veri dosyaları', csv_dosyalari)]:
        print(f"--- {baslik}")
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            yaz('❌', f"{baslik} kontrolü çalışmadı: {e}")
        print()
    print(f"Özet: ✅ {SONUC['✅']}  ⚠️ {SONUC['⚠️']}  ❌ {SONUC['❌']}")
    if SONUC['❌']:
        print("❌ olan maddeler düzeltilmeden bot başlatılmamalı.")
    return 1 if SONUC['❌'] else 0


if __name__ == '__main__':
    sys.exit(main())
