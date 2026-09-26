# -*- coding: utf-8 -*-
"""
SHADOW LABELER
- shadow_sinyaller.csv'deki (botun girmediği sinyaller) 5 saatten eski, henüz
  etiketlenmemiş satırları alır, sinyal sonrası 4 saatlik 15m mumlarla sanal
  sonucu hesaplar ve shadow_sinyaller_etiketli.csv'ye EKLER (append-only,
  bot yazmaya devam ederken güvenlidir).
- İlerleme shadow_labeler_state.txt'de tutulur; aynı satır iki kez etiketlenmez.
- Cron'da trainer'dan ÖNCE çalıştırın, ör: 45 2 * * * (trainer 03:00 ise).
- Yalnızca herkese açık OHLCV verisi kullanır, API key gerektirmez.
"""
import csv
import os
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KAYNAK = os.path.join(BASE_DIR, 'shadow_sinyaller.csv')
HEDEF = os.path.join(BASE_DIR, 'shadow_sinyaller_etiketli.csv')
DURUM_DOSYASI = os.path.join(BASE_DIR, 'shadow_labeler_state.txt')

BEKLEME_SAAT = 5        # sinyalin üzerinden en az bu kadar zaman geçmiş olmalı
PENCERE_MUM = 16        # simülasyon penceresi: 16 x 15m = 4 saat (botun MAX_BEKLEME_SAATI)
TP_PCT = 0.04           # sanal kâr al: botun "kısmi kâr" bölgesi
SL_PCT = -0.03          # sanal stop: botun tipik stop bölgesi


def sanal_sonuc(entry, mumlar):
    """Basit bracket simülasyonu. Muhafazakâr varsayım: aynı mumda hem TP hem SL
    seviyesi görülürse STOP sayılır (mum içi sıralama bilinemez)."""
    max_k, min_k = 0.0, 0.0
    for ts, o, h, l, c, v in mumlar[:PENCERE_MUM]:
        max_k = max(max_k, h / entry - 1)
        min_k = min(min_k, l / entry - 1)
        if (l / entry - 1) <= SL_PCT:
            return SL_PCT * 100, max_k * 100, min_k * 100
        if (h / entry - 1) >= TP_PCT:
            return TP_PCT * 100, max_k * 100, min_k * 100
    son_kapanis = mumlar[min(PENCERE_MUM, len(mumlar)) - 1][4]
    return (son_kapanis / entry - 1) * 100, max_k * 100, min_k * 100


def main():
    import ccxt  # fonksiyon içinde: modül testlerde borsasız da import edilebilsin

    if not os.path.exists(KAYNAK):
        print("Etiketlenecek shadow verisi yok.")
        return

    son_ts = 0
    if os.path.exists(DURUM_DOSYASI):
        try:
            son_ts = int(open(DURUM_DOSYASI).read().strip() or 0)
        except ValueError:
            son_ts = 0

    with open(KAYNAK, encoding='utf-8') as f:
        satirlar = list(csv.DictReader(f))

    simdi_ms = int(time.time() * 1000)
    sinir_ms = simdi_ms - BEKLEME_SAAT * 3600 * 1000

    exchange = ccxt.binance({'enableRateLimit': True})
    yeni, islenen_ts = [], son_ts

    for r in satirlar:
        r.pop(None, None)  # başlıktan uzun satırların fazla alanları etiket kolonlarına karışmasın
        try:
            ts = int(float(r['Ts']))
        except (KeyError, ValueError, TypeError):
            continue
        if ts <= son_ts:
            continue           # zaten etiketlendi
        if ts > sinir_ms:
            break              # kayıtlar kronolojik: gerisi de çok taze
        try:
            mumlar = exchange.fetch_ohlcv(r['Sembol'], '15m', since=ts + 1, limit=PENCERE_MUM + 1)
            if len(mumlar) < PENCERE_MUM:
                if ts < simdi_ms - 48 * 3600 * 1000:
                    islenen_ts = ts
                    continue   # 48 saattir veri tamamlanmadıysa muhtemelen delist: atla
                break          # veri henüz tamam değil, sonraki çalışmada dene
            entry = float(r['Fiyat'])
            sonuc, max_k, min_k = sanal_sonuc(entry, mumlar)
            r.update({'Sanal_Sonuc_Pct': round(sonuc, 3),
                      'Max_Kar_Pct': round(max_k, 3),
                      'Min_Kar_Pct': round(min_k, 3)})
            yeni.append(r)
            islenen_ts = ts
        except Exception as e:
            print(f"⚠️ {r.get('Sembol')} etiketlenemedi, atlandı: {e}")
            islenen_ts = ts    # bozuk satırda takılıp kalma

    if yeni:
        alanlar = list(yeni[0].keys())
        if os.path.exists(HEDEF):
            with open(HEDEF, encoding='utf-8') as f:
                mevcut = next(csv.reader(f), [])
            if mevcut and mevcut != alanlar:
                # Kaynak şeması değişmiş (yeni feature kolonu): hedef dosyayı birleşik
                # kolon setine taşı, eski satırlar yeni kolonlarda boş kalır
                with open(HEDEF, encoding='utf-8') as f:
                    eski = list(csv.DictReader(f))
                alanlar = list(dict.fromkeys(mevcut + alanlar))
                with open(HEDEF, 'w', encoding='utf-8', newline='') as f:
                    w = csv.DictWriter(f, fieldnames=alanlar, extrasaction='ignore')
                    w.writeheader()
                    w.writerows(eski)
                print(f"🧬 Etiketli CSV şeması güncellendi ({len(mevcut)}→{len(alanlar)} kolon).")
        yaz_baslik = not os.path.exists(HEDEF)
        with open(HEDEF, 'a', encoding='utf-8', newline='') as f:
            w = csv.DictWriter(f, fieldnames=alanlar, extrasaction='ignore')
            if yaz_baslik:
                w.writeheader()
            w.writerows(yeni)

    if islenen_ts > son_ts:
        with open(DURUM_DOSYASI, 'w') as f:
            f.write(str(islenen_ts))

    print(f"✅ {len(yeni)} shadow sinyali etiketlendi (toplam kaynak: {len(satirlar)}).")


if __name__ == "__main__":
    main()
