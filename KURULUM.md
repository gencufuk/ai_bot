# AI Ensemble Sniper V18.4 — Temiz Kurulum

Paket (`ai_bot_v18_4_paket.zip`) yalnız kod dosyalarını ve kurtarılmış iki geçmiş dosyasını içerir.
Kurulum aracı (`tools/temiz_kurulum.py`) **hiçbir dosyayı silmez**:
- eski bot dosyalarını tarihli bir arşiv klasörüne (`/root/eski_bot_<tarih>/`) taşır,
- yeni sürümü kurar,
- yeni sürümün kullandığı canlı verileri arşivden geri kopyalar.

## Adımlar
1. **Botu durdurun** (screen/tmux içinde Ctrl+C ya da `pkill -f ai_bot.py`). Açık pozisyonlar Redis'te
   kalır; bot yeniden başlayınca izlemeye devam eder. Gece 03:00'e yakın yapmayın (trainer çalışmasın).
2. **Tam yedek alın.** Yedek `.env` içerir, kimseyle paylaşmayın:
   ```bash
   cd /root && tar czf /root/yedek_$(date +%F).tgz --exclude=./venv .
   ```
3. **Zip'i `/root`'a yükleyip açın** (WinSCP/FileZilla ile sürükleyin):
   ```bash
   cd /root && unzip ai_bot_v18_4_paket.zip
   ```
4. **Planı görün.** Bu adım hiçbir şeyi değiştirmez:
   ```bash
   python3 /root/ai_bot_v18_4_paket/tools/temiz_kurulum.py
   ```
   Üç liste çıkar: ARŞİVE TAŞINACAK, DOKUNULMAYACAK, TANINMAYAN. Taşınacaklar arasında başka bir işte
   kullandığınız dosya varsa önce onu başka bir yere alın.
5. **Uygulayın:**
   ```bash
   python3 /root/ai_bot_v18_4_paket/tools/temiz_kurulum.py --uygula
   ```
   Sonda kurulum kontrolü çalışır. ❌ varsa botu başlatmayın.
6. **Botu başlatın.** Telegram'a "CORE V18.4 başladı" mesajı gelir.
7. **Cron:** `crontab -l` ile bakın. Trainer ve etiketleyici satırları zaten varsa yeni satır eklemeyin; aynı dosya
   adlarını çalıştırdıkları için artık yeni sürümü çalıştırırlar. Yalnız etiketleyicinin 03:00 trainer'dan **önce**
   çalıştığını kontrol edin. Hiç yoksa ekleyin (`crontab -e`):
   ```
   45 2 * * * cd /root && /root/venv/bin/python shadow_labeler.py >> /root/shadow_labeler.log 2>&1
   0 3 * * *  cd /root && /root/venv/bin/python ai_trainer.py >> /root/ai_trainer.log 2>&1
   ```
   Araç cron'un yazdığı log dosyalarına (ör. `ai_trainer.log`, `shadow_labeler.log`) dokunmaz; bunlar zararsızdır.
8. **Veri hattını bir kez çalıştırın** (bot çalışırken de olur):
   ```bash
   cd /root && venv/bin/python shadow_labeler.py
   venv/bin/python backfill_sinyaller.py --gun 180 --evren 80     # 10-30 dk; kesilirse kaldığı yerden devam
   venv/bin/python ai_trainer.py --kuru && cat egitim_raporu.json
   ```
9. **Temizlik:**
   ```bash
   rm -rf /root/ai_bot_v18_4_paket /root/ai_bot_v18_4_paket.zip   # hemen
   rm -rf /root/eski_bot_<tarih>                                    # 1-2 hafta sorunsuz çalışınca
   ```

## Kurulumdan sonra `/root`
| Dosya | Ne | Kaynak |
|---|---|---|
| `ai_bot.py`, `ai_trainer.py`, `shadow_labeler.py`, `backfill_sinyaller.py`, `sniper/`, `tools/` | kod | paket |
| `.env`, `venv/` | API anahtarları, Python ortamı | dokunulmaz |
| `core_islem_verileri.csv`, `core_islem_verileri_v2.csv` | işlem kayıtları (bot yazar) | eski kurulumdan kopyalanır |
| `core_islem_verileri_v2_gecmis.onarildi.csv`, `core_islem_verileri_v2.csv.yedek.onarildi.csv` | kurtarılmış 31 Mayıs – 20 Eylül geçmişi | paket |
| `shadow_sinyaller.csv` | girilmeyen sinyaller (bot yazar) | eski kurulumdan kopyalanır |
| `core_xgboost_model.json` | core AI modeli (Haziran'dan beri aynı dosya; Ağustos botu da bunu kullanıyordu) | eski kurulumdan kopyalanır |
| `etiketli_sinyaller.csv`, `backfill_sinyaller.csv`, `egitim_raporu.json`, `*.log` | eğitim verisi, rapor, loglar | çalıştıkça oluşur |

**Getirilmeyenler (arşivde kalır):**
- `filter_model.json`: Ağustos verisini ezberlemiş filtre modeli.
- `shadow_sinyaller_etiketli.csv`: eski etiketleyicinin çıktısı.
- Eski loglar, `*.yedek` ve `*.bak` kopyaları, eski kod.

**Dokunulmayanlar:**
- Gizli dosyalar (`.env`, `.ssh` ...) ve `venv/`.
- Redis dosyaları (`*.rdb`, `*.aof`).
- `yedek_*.tgz` yedekleri ve `.sh` betikleri.
- crontab'da adı geçen dosyalar.
- `ufuk_islem_verileri.csv` gibi başka bir bota ait olabilecek veriler.
- Tanınmayan her şey.

Taşınacak dosyalardan birini çalıştıran bir süreç varsa (bot, trainer ya da aynı klasördeki başka bir bot) araç uygulamayı reddeder.

## AI ayarı
V18.4 `AI_GOLGE_MOD = False` ile core modele göre bloklar (skor < 0.65 → alım yok). Ağustos'taki V18.0.2
de aynı modelle aynı eşikte bloklama yapıyordu. Yani bu ayar Ağustos davranışını korur.

Gece eğitimi artık "ezberleme" yapmaz. V3 trainer yeni bir modeli yalnız örneklem dışı testleri geçerse
yayınlar, geçemezse mevcut model kalır. Çoğu gece "yayınlanmadı" görmeniz normaldir. Ezberleyen model
geçmişte mükemmel, gelecekte yazı-tura olur: eski filtre modeli eğitildiği dönemde AUC 0.75–0.91,
sonrasında 0.50.

Elle tuttuğunuz coin varsa `.env` dosyasına ekleyin: `MANUEL_COINLER=ETH,SOL`. Bot bu coinleri hiçbir koşulda satmaz.

## İsteğe bağlı: geçmiş simülasyonu
Bot çalışırken de olur, API anahtarı gerekmez:
```bash
cd /root && venv/bin/python tools/v184_simulasyon.py --baslangic 2026-08-05 --limit 20   # ~1 dk deneme
cd /root && venv/bin/python tools/v184_simulasyon.py --baslangic 2026-08-05              # tam: ~10-20 dk
cat v184_sim_rapor.txt
```
Rapor, Ağustos botunun (V18.0.2) ve V18.4'ün aynı fiyatlarda ne yapacağını karşılaştırır.

## İkinci bir hesapta / başka sunucuda aynı bot
Kod ve eğitim verisi paylaşılabilir; hesap bilgileri ve işlem kayıtları paylaşılmaz.

1. **Kurulum:** yukarıdaki adımlarla yapılır. Makinede eski bir bot varsa `temiz_kurulum.py` kullanın; bot
   klasörü `/root` değilse `--hedef` verin. Yeni bir makinede Python ortamı ve Redis yoksa önce onları kurun
   (`apt install redis-server`). Eksik paketleri `tools/kurulum_kontrol.py` listeler. xgboost sürümü diğer
   makineyle aynı olsun (3.4): model dosyası paylaşılırken sorun çıkmasın.
2. **`.env`:** o hesabın kendi değerleri girilir.
   - Binance API anahtarı: yalnız Spot işlem izni; para çekme KAPALI; IP kısıtı bu sunucunun IP'si.
   - Telegram: ayrı bir bot token'ı. Aynı token iki makinede çalışırsa Telegram komutları çakışır.
   - **Elle tutulan coinler:** `MANUEL_COINLER=BTC,ETH,...` yazılmalı. Yazılmazsa bot, spot cüzdandaki 10 USDT
     üstü her coini (USDT ve BNB hariç) 30 dakika içinde sahiplenir ve kendi kurallarıyla satar. En temizi bot
     hesabında yalnız USDT (+ komisyon için BNB) tutmaktır.
3. **Sunucu saati UTC olsun.** `date` ile bakın; UTC değilse `timedatectl set-timezone Etc/UTC` çalıştırın.
   İşlem kayıtları ve AI'ın saat feature'ları UTC varsayar; cron saatleri de UTC olur.
4. **Kurulumdan SONRA ilk makineden kopyalayın:**
   - `core_xgboost_model.json`: zorunlu. Bloklama modunda model yoksa bot alım yapmaz.
   - `etiketli_sinyaller.csv` ve `backfill_sinyaller.csv`: eğitim verisi. Etiketler fiyat verisinden hesaplandığı
     için iki hesabın verisi aynı derecede geçerlidir; backfill piyasa verisidir, iki makinede de aynıdır.

   Kopyalamayın:
   - `core_islem_verileri*.csv`: işlem kayıtları hesaba özeldir; karışırsa o hesabın istatistiği bozulur.
   - `shadow_sinyaller.csv`: zaten etiketli dosyanın içinde.
   - `.env` ve loglar.

   Örnek:
   ```bash
   scp /root/core_xgboost_model.json /root/etiketli_sinyaller.csv /root/backfill_sinyaller.csv root@DIGER_IP:/root/
   ```
5. **Cron:** aynı saatlerle kurulur (02:45 etiketleyici, 03:00 trainer).

İki hesap aynı sinyallere neredeyse aynı anda girer. Bu teknik bir sorun değildir, ama sonuçlar birbiriyle
yüksek korelasyonlu olur: kötü bir hafta ikisini birden vurur.

## Geri dönüş
```bash
python3 /root/tools/temiz_kurulum.py --geri-al /root/eski_bot_<tarih>
```
Eski kurulum yerine döner. V18.4 dosyaları ve V18.4 döneminde yazılan veriler `v184_kaldirilan_<tarih>/`
klasörüne taşınır; hiçbir şey silinmez.

## Pakettekiler
- `ai_bot.py` (V18.4), `ai_trainer.py` (V3), `shadow_labeler.py` (V2), `backfill_sinyaller.py`, `sniper/`
- `tools/`:
  - `temiz_kurulum.py`: bu kurulum
  - `kurulum_kontrol.py`
  - `csv_onar.py`
  - `gecmis_simulasyon.py`
  - `v184_simulasyon.py`
- `core_islem_verileri_v2_gecmis.onarildi.csv`: 31 Mayıs – 14 Eylül işlem geçmişiniz. Başka bottan karışan
  9 satır ve feature'sız sahiplenilmiş 5 satır ayıklandı. Labeler bunu otomatik okur.
- `core_islem_verileri_v2.csv.yedek.onarildi.csv`: kolon kayması onarılmış 16–20 Eylül V2 kayıtları.
