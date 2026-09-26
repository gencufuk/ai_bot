# PROJE: AI ENSEMBLE SNIPER BOT (V18.4 — GÖLGE MOD + REJİM FİLTRESİ + TEK TİP ETİKET)

> Bu doküman **fiilen çalışan** sistemi anlatır. Henüz yapılmamış hedefler en alttaki
> "PLANLANAN" bölümündedir. (Son güncelleme: 2026-09-26)
> V18.3 → V18.4 analizi, kanıtları ve deploy adımları: `ANALIZ_V18.4.md`.

## Amaç
Binance **Spot** piyasasında hacimli altcoinlerde kısa vadeli (15m) momentum al-sat.
Şu anki öncelik kâr maksimizasyonu değil, **temiz veri toplamak**: AI modelleri henüz
doğrulanmadığı için bot GÖLGE MODDA çalışır (aşağıda).

## Teknoloji
- Python, `ccxt.async_support` (REST polling — WebSocket DEĞİL), asyncio
- Redis (localhost:6379, senkron istemci) — pozisyon/durum saklama
- XGBoost (2 model), pandas + pandas_ta
- Telegram bot (bildirim + `/durum`, `/kar` komutları; sadece yetkili chat_id işlenir)
- API anahtarları `.env` dosyasından (BINANCE_API_KEY, BINANCE_SECRET_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)

## Dosyalar (sunucuda /root altında)
| Dosya | Görev |
|---|---|
| `ai_bot.py` | Ana alım-satım motoru (3 async döngü + bildirim, model izleme, watchdog görevleri). Log öneki: `[CORE V18.4]` |
| `ai_trainer.py` | V3: her gece 03:00 (cron) tek karar modelini eğitir; purged walk-forward CV + istatistiksel/ekonomik kapılar; geçemeyen model kaydedilmez. Rapor: `egitim_raporu.json` |
| `shadow_labeler.py` | V2: her gece 02:45 (cron) core + shadow sinyallerini **tek tip etiketle** etiketler → `etiketli_sinyaller.csv` |
| `backfill_sinyaller.py` | Elle/aylık: geçmiş OHLCV'den sinyal üretir + aynı etiketle etiketler → `etiketli_sinyaller.csv` (Kaynak=backfill) |
| `sniper/` | Ortak modüller: `csv_kayit` (güvenli CSV), `risk_motoru` (çıkış kararları), `ozellikler` (kural + feature), `etiketleme`, `etiket_deposu`, `model_karti` |
| `tools/` | `temiz_kurulum.py` (arşivle-kur; `--guncelle` ile yalnız kod), `veri_birlestir.py` (eski dosyaları tek düzene katar; `--ekle` ile başka makineden eğitim verisi alır), `kurulum_kontrol.py` (ön kontrol), `csv_onar.py` (kolon kayması onarımı), `gecmis_simulasyon.py` / `v184_simulasyon.py` (geçmiş simülasyonları) |
| `core_islem_verileri.csv` | ESKİ V1 işlem kaydı (13 kolon). V18.4 artık yazmıyor (V2 tüm kolonlarını içerir); `tools/veri_birlestir.py` V2'ye katar |
| `core_islem_verileri_v2.csv` | Tek işlem kaydı (başlıklı: V1'in 13 kolonu + genişletilmiş feature'lar + AI skorları). Hesaba özel, makineler arasında paylaşılmaz |
| `shadow_sinyaller.csv` | Kural filtresini geçip **girilmeyen** sinyaller (sebep: AI_RED / TEK_ALIM_KURALI / BAKIYE_YETERSIZ / REJIM_YATAY) |
| `shadow_sinyaller_etiketli.csv` | ESKİ (V1) labeler çıktısı; V18.4'te kullanılmaz, temiz kurulumda arşive kalır |
| `core_xgboost_model.json` (+ `.kart.json`) | Karar modeli. Feature listesi modelin `feature_names`'inden, eşik/metrikler karttan okunur; bot 5 dk'da bir değişikliği kontrol edip yeniden yükler |
| `filter_model.json` | Eski filtre modeli (Ağustos verisini ezberlemiş); temiz kurulumda arşive kalır, V3 trainer da yeni model yayınlayınca `.emekli_<ts>` olarak kenara alır |
| `etiketli_sinyaller.csv` | Tek eğitim dosyası: core + shadow + backfill sinyalleri aynı etiketle (Kaynak kolonu), trainer'ın girdisi. Makineler arasında paylaşılabilir (`veri_birlestir.py --ekle`); aynı Anahtar bir kez sayılır. Eski kurulumdaki ayrı `backfill_sinyaller.csv` varsa trainer onu da okur |
| `ufuk_islem_verileri.csv` | İkinci botun verisi. V18.4 trainer'ı kullanmaz (tek tip etiketli veriyle eğitir); temiz kurulum bu dosyaya dokunmaz |

CSV/state dosyalarını ELLE OLUŞTURMAYIN — kod ilk ihtiyaçta başlığıyla oluşturur.
V18.4 CSV katmanı mevcut veriyi asla silmez: şema değişince yeni kolonlar sona eklenir; başlıksız
veya kaymış dosya bayt bayt arşivlenip (`.legacy_<ts>` / `.bozuk_<ts>`) Telegram'a bildirilir.
Etiketleyici ve backfill aynı eğitim dosyasına yazar: her ekleme süreçler arası dosya kilidi (`flock`) altında
yapılır, aynı anda çalışsalar da satır kaybolmaz.

## AI Modları
- **GÖLGE MOD (`AI_GOLGE_MOD = True`; kodda şu an `False`. Öneri: bloklama açık kalsın ama gürültü olan `filter_model.json` kaldırılsın, bkz. ANALIZ §2.3):** Model skorları her sinyalde
  hesaplanır, loglanır ve CSV'lere yazılır ama **işlem bloklanmaz**. Sebep: mevcut
  modeller doğrulamada AUC ~0.46-0.50 (rastgele) çıktı; bloklama = rastgele işlem elemek.
- **Bloklama modu (`AI_GOLGE_MOD = False`):** skor < eşik (kartlı modelde karttaki eşik, kartsızda
  0.65) veya filtre skoru > 0.45 ise işlem reddedilir ve shadow'a `AI_RED` yazılır. Model hiç yüklü
  değilse yeni alım yapılmaz (`AI_MODEL_YOK`). Bu moda geçiş şartı: V3 trainer'ın yayına aldığı,
  gölge modda 1-2 hafta tutarlılığı izlenmiş bir model.

## Rejim Filtresi (YATAY / TREND)
- BTC 15m **ADX(14)** ölçülür: ADX < 20 (`ADX_TREND_ESIK`) → **YATAY**, üstü → **TREND**.
- **YATAY rejimde yeni alım açılmaz**; sinyaller `REJIM_YATAY` sebebiyle shadow'a yazılır —
  filtrenin gerçek maliyeti/kazancı labeler'ın sanal sonuçlarıyla ölçülür (eşik buna göre kalibre edilecek).
- BTC/EMA200 kararında **histerezis** var (`BTC_HISTEREZIS` %0.4): EMA'nın %0.4 üstünde
  ONAY'a geçer, %0.4 altında BEKLE'ye döner; aradaki bantta son durum korunur (whipsaw önleme).
- Gerekçe: geçmiş veri, stratejinin tüm kârının trend dönemlerinden geldiğini, yatay
  dönemlerin (özellikle ZAMAN AŞIMI çıkışları) sistematik zarar yazdığını gösteriyor.
- Trend başlangıcında ADX'in gecikmesi nedeniyle ilk 1-2 mum kaçırılabilir; bu bilinçli takas.

## ai_bot.py Çalışma Akışı (3 paralel döngü)
1. **radar_loop (15 sn):** Tüm ticker'ları çeker; BTC koruması (15m EMA200 üstü = ONAY,
   son 3 mumda %1.5+ düşüş = ANİ ÇÖKÜŞ) ve BTC feature'larını günceller. Hacmi > 12M USDT
   olan en çok yükselen 10 USDT paritesini tarar. Sinyal kuralı: RSI(14)>55 + hacim >
   2.5x ortalama + (MSB kırılımı veya Engulfing) + fiyat 1h EMA20 üstünde + ATR < %4.
   Sinyal anında genişletilmiş feature seti toplanır (pump_3s/6s, zirve uzaklığı,
   EMA uzaklıkları, BTC durumu, saat/gün, stop sicili, AI skorları). Döngü başına
   **tek alım** (market buy, gerçekleşen fill fiyatı kaydedilir); kasa: 20 USDT
   (`SABIT_KASA`) / balina sinyalinde 40 USDT. Girilmeyen sinyaller shadow CSV'ye
   yazılır (sembol başına 15 dk tekrar koruması).
2. **vip_cuzdan_loop (2 sn):** Açık pozisyonları izler. Tüm oranlar **komisyon dahil**
   (FEE_RATE 0.001, alış+satış). Çıkış mantığı: ATR bazlı dinamik stop (%2.5-%5.5,
   balinada %2.0) → max kâr %2.5'i geçince başa baş koruması → kademeli trailing makas
   (ilk kademe ATR bazlı ~2xATR, %3-%6 arası; üst kademeler %10/%20) → RSI ≥ 83'te %50
   sat (MOON BAG) → **YATAY rejimde** pozisyon 1.5 saat ±%1 bandında sıkışıp hacim de
   söndüyse MOMENTUM ÖLDÜ çıkışı (trend rejiminde bu kural devre dışı) → 4 saat sonunda
   kârsızsa ZAMAN AŞIMI çıkışı. Peş peşe 2 stop = 24 saat kara liste; her çıkışta 1 saat
   cooldown. 30 dk'da bir cüzdan senkronizasyonu (ticker verisi hazır olmadan çalışmaz;
   10 dk'dan genç pozisyona dokunmaz; sahiplenilen bakiye Telegram'a bildirilir; `.env`
   `MANUEL_COINLER` hariç tutulur).
   V18.4: karar mantığı `sniper/risk_motoru.py`'de (saf, testli). Sembol bazında hata
   izolasyonu; satış alımda kaydedilen adedi (`:adetler`) aşmaz; emirler `newClientOrderId`
   ile idempotent; yarı satış sonrası taban kâr kilidini ezmez; zaman aşımı uzatması
   `:zaman_ref`'i günceller (giriş zamanı değişmez); momentum kontrolü kapanmış mumlarla.
3. **telegram_handler (1 sn):** Sadece `TELEGRAM_CHAT_ID`'den gelen `/durum` ve `/kar`
   komutlarını işler; update offset hata durumunda sıfırlanmaz.
4. **Yardımcı görevler (V18.4):** `bildirim_gorevi` (risk döngüsü Telegram'ı beklemez; kuyruk),
   `model_izleme_gorevi` (5 dk; model/kart değişince yeniden yükler, hatada eskisini korur),
   `watchdog_gorevi` (VIP 60 sn / radar 180 sn tur tamamlamazsa alarm).

## Veri / Eğitim Pipeline'ı (gece cron sırası)
```
02:45 shadow_labeler.py : 4s15dk'dan eski shadow sinyalleri + KAPANMIŞ core pozisyonları →
                          sinyal dakikasından başlayan 240 adet 1m mumla tek tip etiket
                          (ATR stop / ilk kâr eşiği / +%1 kâr kilidi / 4 saat, komisyon dahil)
                          → etiketli_sinyaller.csv
03:00 ai_trainer.py     : etiketli_sinyaller.csv → purged walk-forward
                          CV (episod bazlı bootstrap) → kapılar: OOS AUC ≥ 0.55, %95 alt sınır > 0.5,
                          katların çoğunda > 0.5, ekonomik permütasyon testi, canlı transfer,
                          canlı kanıt (≥50 tam kayıtlı canlı olay). Doğrulama yalnız tüm feature'ları
                          dolu satırlarla (eski formatlı kayıtlar eğitime girer, doğrulamaya girmez) →
                          geçerse model + kart atomik kaydedilir, bot hot-reload eder;
                          geçmezse ESKİ MODEL KORUNUR (egitim_raporu.json, ai_trainer_history.log)
(elle) backfill_sinyaller.py --gun 180 --evren 80 : geçmiş sinyaller (kapalı mum modu) → etiketli_sinyaller.csv
(elle) tools/veri_birlestir.py --ekle gelen_etiketli.csv --uygula : diğer makinenin eğitim verisini katar
```

## Redis Key Şeması (prefix: `PORTFOY`)
`:islem_listesi` (sym→giriş fiyatı), `:islem_miktarlari` (USDT maliyet), `:max_karlar`,
`:half_sold`, `:ai_data` (sinyal feature'ları JSON), `:giris_zamanlari`, `:realize_karlar`,
`:last_rsi_check`, `:last_mom_check`, `:zaman_ref` (V18.4, zaman aşımı referansı),
`:adetler` (V18.4, pozisyonun net coin adedi), `:stop_counts`, `:kara_liste`, `:cooldowns`,
`:toplam_kar`, `:shadow_son`.
Bir pozisyonun alanları (`POZISYON_ANAHTARLARI`) alımda sıfırlanıp tek MULTI/EXEC ile yazılır,
çıkışta tek MULTI/EXEC ile silinir.

## Bilinen Sınırlar
- **Borsa tarafında stop-loss emri YOK** — tüm koruma botun canlı olmasına bağlı.
- Sinyaller varsayılan olarak kapanmamış (canlı) 15m mumla üretiliyor (repaint riski;
  backfill kapalı mumla çalıştığından dağılım farkı). `SINYAL_KAPALI_MUM = True` ile birebir parite.
- 3.5 aylık geriye dönük veri: sistem komisyon dahil yaklaşık başa baş; kârın tamamı
  tek aydan (Ağustos 2026). En büyük zarar kalemi ZAMAN AŞIMI çıkışları.

## PLANLANAN (henüz yapılmadı)
1. **Hibrit felaket stopu:** Alımdan sonra borsaya ~%-10 statik STOP_LOSS_LIMIT; her
   satıştan önce iptal, kısmi satış sonrası yeniden koyma, vip döngüsünde mutabakat.
2. ~~Trainer'ın v2 + shadow etiketli veriyle eğitilmesi~~ (V18.4: tek tip etiket + backfill +
   trainer V3 yapıldı). Kalan: V3'ün yayına aldığı modelin gölge modda izlenip bloklamaya geçiş.
3. systemd servisi (Restart=always) + açılış/kapanış Telegram bildirimi.
4. Shadow verisi birikince `ADX_TREND_ESIK` kalibrasyonu (20 mi, 23 mü; ya da "kasa yarıya" yumuşatması).
5. ccxt.pro WebSocket'e geçiş, dinamik kasa (bakiye yüzdesi) — düşük öncelik.
