# AI Ensemble Sniper V18.4 — Kurulum ve Güncelleme

Paket (`ai_bot_v18_4_paket.zip`) yalnız kod içerir, hiçbir veri dosyasının üzerine yazmaz. İki araç vardır:
- `tools/temiz_kurulum.py`: ilk kurulum (eski bot dosyalarını arşive taşır, yeni sürümü kurar, canlı veriyi geri
  getirir) ve `--guncelle` ile kurulu sistemde yalnız kodu yenileme.
- `tools/veri_birlestir.py`: dağınık veri dosyalarını birleştirme ve başka makineden eğitim verisi alma.

İkisi de önce **kuru** çalışır (planı gösterir, hiçbir şeyi değiştirmez) ve hiçbir dosyayı silmez: eskiler tarihli
arşiv klasörlerine taşınır.

## Veri düzeni: her makinede 3 dosya
| Dosya | Ne | Paylaşılır mı |
|---|---|---|
| `core_islem_verileri_v2.csv` | bu hesabın **tüm** işlem kayıtları (31 Mayıs'tan bugüne). Bot yazar. | Hayır, hesaba özel |
| `shadow_sinyaller.csv` | girilmeyen sinyaller. Bot yazar, etiketleyici okur. | Hayır (etiketlisi eğitim dosyasında) |
| `etiketli_sinyaller.csv` | AI'ın eğitim verisi: gerçek işlemler + girilmeyen sinyaller + geçmiş piyasa sinyalleri (backfill), hepsi aynı yöntemle etiketli | **Evet, iki makinede aynı dosya** |

Ayrıca `core_xgboost_model.json` (AI modeli) ve loglar bulunur. Eski ayrı dosyalar artık kullanılmıyor:
- `core_islem_verileri.csv` (V1): V2 tüm kolonlarını içerdiği için artık yazılmıyor.
- İki `*.onarildi.csv` geçmiş dosyası: V2 işlem kaydına katılır.
- `backfill_sinyaller.csv`: etiketli dosyaya katılır.

`veri_birlestir.py` bu dosyaları bir kez birleştirir.

## Kurulu sistemi güncelleme (yalnız kod)
1. Zip'i `/root`'a yükleyin ve açın. Önceki paket klasörü varsa önce silin:
   ```bash
   cd /root && rm -rf ai_bot_v18_4_paket && unzip ai_bot_v18_4_paket.zip
   ```
2. Botu durdurun: `screen -S btc_bot -X quit`. Backfill veya simülasyon çalışıyorsa bitmesini bekleyin.
3. Kodu güncelleyin. Veri dosyalarına dokunulmaz; eski kod `eski_kod_<tarih>/` klasörüne taşınır:
   ```bash
   python3 /root/ai_bot_v18_4_paket/tools/temiz_kurulum.py --guncelle
   ```
4. **İlk kez tek dosya düzenine geçiyorsanız** veri dosyalarını birleştirin. Önce planı görün, sonra uygulayın:
   ```bash
   cd /root && venv/bin/python tools/veri_birlestir.py
   cd /root && venv/bin/python tools/veri_birlestir.py --uygula
   ```
   Birleştirilen eski dosyalar `veri_arsiv_<tarih>/` klasörüne taşınır.
5. Botu başlatın:
   ```bash
   screen -dmS btc_bot bash -c "source venv/bin/activate && python3 -u ai_bot.py"
   ```
6. Temizlik:
   ```bash
   rm -rf /root/ai_bot_v18_4_paket /root/ai_bot_v18_4_paket.zip
   ```
   Arşiv klasörlerini (`eski_kod_*`, `veri_arsiv_*`, `eski_bot_*`) 1-2 hafta sorunsuz çalışınca silin.

## İlk kurulum (yeni makine ya da eski bot)
1. Botu durdurun. Gece 03:00'e yakın yapmayın.
2. Tam yedek alın. Yedek `.env` içerir, kimseyle paylaşmayın:
   ```bash
   cd /root && tar czf /root/yedek_$(date +%F).tgz --exclude=./venv .
   ```
3. Zip'i `/root`'a yükleyip açın: `cd /root && unzip ai_bot_v18_4_paket.zip`
4. Planı görün:
   ```bash
   python3 /root/ai_bot_v18_4_paket/tools/temiz_kurulum.py
   ```
   Üç liste çıkar: ARŞİVE TAŞINACAK, DOKUNULMAYACAK, TANINMAYAN. Bot klasörü `/root` değilse `--hedef` verin.
5. Uygulayın: aynı komutu `--uygula` ile çalıştırın. Sonda kurulum kontrolü çalışır; ❌ varsa botu başlatmayın.
6. Eski kurulumdan getirilen veri dosyalarını birleştirin. Önce planı görün, sonra uygulayın:
   ```bash
   cd /root && venv/bin/python tools/veri_birlestir.py
   cd /root && venv/bin/python tools/veri_birlestir.py --uygula
   ```
   Makine yeniyse ve birleştirilecek dosya yoksa bu adım bir şey yapmaz.
7. Botu başlatın. Telegram'a "CORE V18.4 başladı" mesajı gelir.
8. **Cron:** `crontab -l` ile bakın. Trainer ve etiketleyici satırları zaten varsa yeni satır eklemeyin; etiketleyici
   03:00 trainer'dan **önce** çalışmalı. Hiç yoksa ekleyin (`crontab -e`):
   ```
   45 2 * * * cd /root && /root/venv/bin/python shadow_labeler.py >> /root/shadow_labeler.log 2>&1
   0 3 * * *  cd /root && /root/venv/bin/python ai_trainer.py >> /root/ai_trainer.log 2>&1
   ```
9. **Veri hattını bir kez çalıştırın** (bot çalışırken de olur). Uzun işleri `screen` içinde çalıştırın;
   `-u` log dosyasının canlı dolmasını sağlar:
   ```bash
   cd /root && venv/bin/python shadow_labeler.py
   screen -dmS backfill bash -c "cd /root && venv/bin/python -u backfill_sinyaller.py --gun 180 --evren 80 > backfill.log 2>&1"
   # bitince:
   venv/bin/python ai_trainer.py --kuru && cat egitim_raporu.json
   ```

Kurulum aracının dokunmadıkları:
- Gizli dosyalar (`.env`, `.ssh` ...) ve `venv/`.
- Redis dosyaları.
- `yedek_*.tgz` yedekleri ve `.sh` betikleri.
- Arşiv klasörleri.
- crontab'da adı geçen dosyalar (ör. cron'un yazdığı loglar).
- `ufuk_islem_verileri.csv` gibi başka bir bota ait olabilecek veriler.
- Tanınmayan her şey.

Getirilmeyenler:
- `filter_model.json`: Ağustos verisini ezberlemiş filtre modeli.
- Eski etiketleyici çıktısı, eski loglar ve yedek kopyaları.

## AI ayarı
V18.4 `AI_GOLGE_MOD = False` ile core modele göre bloklar (skor < 0.65 → alım yok). Ağustos'taki V18.0.2
de aynı modelle aynı eşikte bloklama yapıyordu; bu ayar Ağustos davranışını korur.

Gece eğitimi "ezberleme" yapmaz. V3 trainer yeni bir modeli yalnız örneklem dışı testleri geçerse yayınlar;
geçemezse mevcut model kalır. Çoğu gece "yayınlanmadı" görmeniz normaldir. Testler yalnız tüm ölçümleri dolu
kayıtlarla yapılır: eski formatlı işlem kayıtlarında (31 Mayıs – 25 Eylül) yeni ölçümler yok. V18.4'ün tam
kayıtlı en az 50 canlı sinyali birikmeden yeni model yayınlanmaz.

Elle tuttuğunuz coin varsa `.env` dosyasına ekleyin: `MANUEL_COINLER=ETH,SOL`. Bot bu coinleri hiçbir koşulda satmaz.

## İkinci bir hesapta / başka sunucuda aynı bot
Kod ve eğitim verisi paylaşılır; hesap bilgileri ve işlem kayıtları paylaşılmaz.

1. **Kurulum:** "İlk kurulum" adımlarıyla yapılır. Yeni bir makinede önce Python ortamı ve Redis
   (`apt install redis-server`) kurulmalı. Eksik paketleri `tools/kurulum_kontrol.py` listeler. xgboost
   sürümü diğer makineyle aynı olsun (3.4).
2. **`.env`:** o hesabın kendi değerleri girilir.
   - Binance API anahtarı: yalnız Spot işlem izni; para çekme KAPALI; IP kısıtı bu sunucunun IP'si.
   - Telegram: ayrı bir bot token'ı. Aynı token iki makinede çalışırsa komutlar çakışır.
   - **Elle tutulan coinler:** `MANUEL_COINLER=BTC,ETH,...` yazılmalı. Yazılmazsa bot, spot cüzdandaki 10 USDT
     üstü her coini (USDT ve BNB hariç) 30 dakika içinde sahiplenir ve kendi kurallarıyla satar. En temizi bot
     hesabında yalnız USDT (+ komisyon için BNB) tutmaktır.
3. **Sunucu saati UTC olsun.** `date` ile bakın; UTC değilse `timedatectl set-timezone Etc/UTC` çalıştırın.
   İşlem kayıtları ve AI'ın saat feature'ları UTC varsayar.
4. **İlk makineden iki dosya alın** (kurulumdan SONRA):
   - `core_xgboost_model.json`: zorunlu. Bloklama modunda model yoksa bot alım yapmaz. Doğrudan `/root`'a kopyalanır.
   - `etiketli_sinyaller.csv`: eğitim verisi. Gelen dosya farklı bir adla kopyalanıp birleştirilir:
     ```bash
     # ilk makinede:
     scp /root/core_xgboost_model.json root@DIGER_IP:/root/
     scp /root/etiketli_sinyaller.csv root@DIGER_IP:/root/gelen_etiketli.csv
     # ikinci makinede:
     cd /root && venv/bin/python tools/veri_birlestir.py --ekle /root/gelen_etiketli.csv --uygula
     ```
   İşlem kaydı (`core_islem_verileri_v2.csv`) ve `.env` kopyalanmaz.
5. **Cron:** aynı saatlerle kurulur.

**Eğitim verisini ortak tutmak.** Her makine kendi işlemlerini kendi eğitim dosyasına ekler. Arada bir (ör.
haftada bir) iki taraf `etiketli_sinyaller.csv` dosyasını karşıya `gelen_etiketli.csv` adıyla gönderir. Karşı taraf
`--ekle` ile alır. Aynı sinyal iki kez yazılmaz, gelen dosya işlendikten sonra arşive kalkar. Bu yapıldıkça iki
makinedeki eğitim verisi aynı olur ve iki hesabın işlemlerinden birlikte öğrenir. Etiketler fiyat verisinden
hesaplandığı için iki hesabın verisi aynı derecede geçerlidir.

İki bot aynı sinyale dakikalar arayla girerse bu olay eğitim verisinde iki satır olur (biri her hesaptan).
Trainer aynı coinde 4 saat içindeki sinyalleri tek olay (episod) sayar: eğitimde toplam ağırlıkları 1'dir,
doğrulama ve canlı veri eşikleri de olay sayısıyla hesaplanır.

**İşlem kaydı neden paylaşılmaz.** AI `core_islem_verileri_v2.csv` dosyasından doğrudan öğrenmez. Etiketleyici her
gece bu dosyadaki kapanmış işlemleri etiketleyip eğitim dosyasına ekler; eğitim dosyası paylaşıldığı için iki hesabın
işlemleri iki AI'a da zaten ulaşır. İşlem kaydını da birleştirmek öğrenmeye bir şey eklemez, ama:
- iki hesabın kâr/zarar kaydı karışır, hangi işlemin kimin olduğu ayırt edilemez;
- simülasyonlar (`tools/v184_simulasyon.py`, `tools/gecmis_simulasyon.py`) işlemleri tek hesap ve tek bütçeyle
  oynatır; iki hesabın aynı coine aynı anda girişi tek hesapta çakışır ve sonuçlar anlamsızlaşır.

Bot işlem yaparken bu dosyayı okumaz, yalnız yazar; yani paylaşmak alım-satımı bozmaz, yalnız kayıtları karıştırır.

İki hesap aynı sinyallere neredeyse aynı anda girer. Bu teknik bir sorun değildir, ama sonuçlar yüksek
korelasyonlu olur: kötü bir hafta ikisini birden vurur.

## İsteğe bağlı: geçmiş simülasyonu
Bot çalışırken de olur, API anahtarı gerekmez. Backfill ile aynı anda çalıştırmayın; ikisi de Binance'ten
yoğun veri çeker.
```bash
screen -dmS sim bash -c "cd /root && venv/bin/python -u tools/v184_simulasyon.py --baslangic 2026-08-05 > sim.log 2>&1"
cat /root/v184_sim_rapor.txt      # bitince
```

## Geri dönüş
- Kod güncellemesini geri almak: botu durdurun, `eski_kod_<tarih>/` içindekileri `/root`'a geri taşıyın.
- İlk kurulumu geri almak: `python3 /root/tools/temiz_kurulum.py --geri-al /root/eski_bot_<tarih>`.
  V18.4 dosyaları ve V18.4 döneminde yazılan veriler `v184_kaldirilan_<tarih>/` klasörüne taşınır.

## Pakettekiler
- `ai_bot.py` (V18.4), `ai_trainer.py` (V3), `shadow_labeler.py` (V2), `backfill_sinyaller.py`, `sniper/`
- `tools/`:
  - `temiz_kurulum.py`: kurulum ve güncelleme
  - `veri_birlestir.py`: veri birleştirme
  - `kurulum_kontrol.py`
  - `csv_onar.py`
  - `gecmis_simulasyon.py`
  - `v184_simulasyon.py`
