# AI Ensemble Sniper V18.4 — Kurulum (sürükle-bırak)

Kurulum paketi (`ai_bot_v18_4_paket.zip`) **yalnızca** kod dosyalarını ve kurtarılmış iki
geçmiş dosyasını içerir. Sunucudaki hiçbir veri dosyasının (CSV, model, log, `.env`) üzerine yazmaz.

## ⛔ Klasörü silmeyin
`/root` içindeki şu dosyalar botun hafızasıdır; silinirse geri gelmez:

| Dosya | Ne |
|---|---|
| `.env` | API anahtarları |
| `core_islem_verileri.csv`, `core_islem_verileri_v2.csv` | işlem geçmişi |
| `shadow_sinyaller.csv`, `shadow_sinyaller_etiketli.csv` | girilmeyen sinyaller |
| `core_xgboost_model.json`, `filter_model.json` | modeller |
| `*.log`, varsa `*.bozuk_*` / `*.legacy_*` | loglar / kurtarılabilir veri |
| `venv/` | Python ortamı |

Açık pozisyonlar Redis'te tutulur; dosya kopyalamak onları etkilemez.

## ⛔ GitHub reposunun tamamını kopyalamayın
Repodaki CSV/JSON dosyaları sizin eski yüklemenizdir ve sunucudaki güncel verinin üzerine yazar.
Özellikle repodaki `core_islem_verileri_v2.csv` aslında bir **model dosyası** (yükleme hatası).
Sadece paketin içeriğini kopyalayın.

## Adımlar
1. **Botu durdurun** (screen/tmux içinde Ctrl+C ya da `pkill -f ai_bot.py`).
   Açık pozisyonlar Redis'te kalır; bot yeniden başlayınca izlemeye devam eder.
2. **Yedek alın**
   ```bash
   cd /root && tar czf /root/yedek_$(date +%F).tgz --exclude=./venv .
   ```
3. **Paketin İÇİNDEKİLERİ `/root`'a sürükleyin** (WinSCP/FileZilla):
   - `ai_bot.py`, `ai_trainer.py`, `shadow_labeler.py` → üzerine yazılsın
   - `backfill_sinyaller.py`, `sniper/`, `tools/`, iki adet `*.onarildi.csv` → yeni dosyalar
4. **Kontrol**
   ```bash
   /root/venv/bin/python tools/kurulum_kontrol.py
   ```
   ❌ varsa botu başlatmayın.
5. **AI ayarı (önerilen)**
   - Örneklem dışı testte gürültü olduğu ölçülen eski filtre modelini kaldırın: `mv filter_model.json filter_model.json.emekli`.
   - Core model bloklamaya devam eder. V18.4'te engellenen sinyaller de etiketlendiği için bloklama artık eğitim verisini azaltmıyor.
   - Elle tuttuğunuz coin varsa `.env` dosyasına ekleyin: `MANUEL_COINLER=ETH,SOL`. Bot bu coinleri hiçbir koşulda satmaz.
6. **Botu başlatın.** Telegram'a "CORE V18.4 başladı" mesajı gelir.
7. **Veri hattını başlatın** (bot çalışırken de olur)
   ```bash
   /root/venv/bin/python shadow_labeler.py                              # ~500 geçmiş pozisyon + shadow; birkaç dk
   /root/venv/bin/python backfill_sinyaller.py --gun 180 --evren 80     # 10-30 dk; kesilirse kaldığı yerden devam
   /root/venv/bin/python ai_trainer.py --kuru && cat egitim_raporu.json
   ```
8. **Cron değişmez** (02:45 labeler, 03:00 trainer). Trainer kapılardan geçen bir model üretirse
   bot onu 5 dk içinde yükler ve Telegram'dan bildirir.
9. **İsteğe bağlı: V18.4 geçmiş simülasyonu** (bot çalışırken de olur, API anahtarı gerekmez)
   ```bash
   /root/venv/bin/python tools/v184_simulasyon.py --baslangic 2026-08-05 --limit 20   # ~1 dk deneme
   /root/venv/bin/python tools/v184_simulasyon.py --baslangic 2026-08-05              # tam: ~10-20 dk
   cat v184_sim_rapor.txt
   ```
   Fiyat verisi `sim_onbellek/` klasörüne iner (tekrar çalıştırınca hızlıdır). `v184_sim_rapor.txt` dosyasını
   paylaşırsanız sonuçları birlikte yorumlarız.

## Geri dönüş
Botu durdurup yalnızca eski kod dosyalarını geri koyun (veri dosyalarına dokunmayın):
```bash
cd /root && tar xzf yedek_TARIH.tgz ./ai_bot.py ./ai_trainer.py ./shadow_labeler.py
```

## Pakettekiler
- `ai_bot.py`: V18.4 bot
- `ai_trainer.py`: V3 trainer
- `shadow_labeler.py`: V2 labeler
- `backfill_sinyaller.py`: geçmiş sinyal üretimi
- `sniper/`: ortak modüller
- `tools/csv_onar.py`, `tools/kurulum_kontrol.py`, `tools/gecmis_simulasyon.py` (gerçekleşen işlemlerin bütçe replay'i), `tools/v184_simulasyon.py` (V18.4'ün geçmiş fiyatlarla simülasyonu)
- `core_islem_verileri_v2_gecmis.onarildi.csv`: 31 Mayıs – 14 Eylül işlem geçmişiniz. Başka bottan karışan 9 satır ve feature'sız sahiplenilmiş 5 satır ayıklandı. Labeler bunu otomatik okur.
- `core_islem_verileri_v2.csv.yedek.onarildi.csv`: kolon kayması onarılmış 16–20 Eylül V2 kayıtları.
