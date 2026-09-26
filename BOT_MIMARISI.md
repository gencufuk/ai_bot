# PROJE: AI ENSEMBLE SNIPER BOT (V18.2 — GÖLGE MOD + REJİM FİLTRESİ)

> Bu doküman **fiilen çalışan** sistemi anlatır. Henüz yapılmamış hedefler en alttaki
> "PLANLANAN" bölümündedir. (Son güncelleme: 2026-09-17)

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
| `ai_bot.py` | Ana alım-satım motoru (3 async döngü). Log öneki: `[CORE V18.0.2]` |
| `ai_trainer.py` | Her gece 03:00 (cron) iki modeli eğitir; **AUC ≥ 0.55 doğrulamasını geçemeyen model kaydedilmez** |
| `shadow_labeler.py` | Her gece 02:45 (cron) girilmeyen sinyalleri sanal sonuçla etiketler |
| `core_islem_verileri.csv` | V1 işlem kaydı (13 kolon, eski format — uyumluluk için yazılmaya devam ediyor) |
| `core_islem_verileri_v2.csv` | V2 işlem kaydı (başlıklı, 25 kolon: V1 + genişletilmiş feature'lar + AI skorları) |
| `shadow_sinyaller.csv` | Kural filtresini geçip **girilmeyen** sinyaller (sebep: AI_RED / TEK_ALIM_KURALI / BAKIYE_YETERSIZ / REJIM_YATAY) |
| `shadow_sinyaller_etiketli.csv` | Labeler çıktısı: shadow sinyaller + sanal sonuç |
| `core_xgboost_model.json` | Ana model (kâr olasılığı; 4 feature: RSI, Vol_Oran, ATR_Pct, Sinyal_Encoded) |
| `filter_model.json` | Filtre modeli (zarar riski; 3 feature) |
| `ufuk_islem_verileri.csv` | İkinci botun verisi — trainer havuzuna girer (⚠️ o bot eski kodla çalışıyorsa komisyonsuz kâr yazar) |

CSV/state dosyalarını ELLE OLUŞTURMAYIN — kod ilk ihtiyaçta başlığıyla oluşturur;
elle açılan boş dosya başlıksız kalır ve pipeline bozulur.

## AI Modları
- **GÖLGE MOD (şu an aktif, `AI_GOLGE_MOD = True`):** Model skorları her sinyalde
  hesaplanır, loglanır ve CSV'lere yazılır ama **işlem bloklanmaz**. Sebep: mevcut
  modeller doğrulamada AUC ~0.46-0.50 (rastgele) çıktı; bloklama = rastgele işlem elemek.
- **Bloklama modu (`AI_GOLGE_MOD = False`):** ai_score < 0.65 (`AI_MIN_OLASILIK`) veya
  filter_score > 0.45 (`FILTRE_MAX_RISK`) ise işlem reddedilir ve shadow'a `AI_RED` yazılır.
  Bu moda geçiş şartı: trainer'da AUC eşiğini geçen bir model.

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
   cooldown. 30 dk'da bir cüzdan senkronizasyonu (ticker verisi hazır olmadan çalışmaz).
3. **telegram_handler (1 sn):** Sadece `TELEGRAM_CHAT_ID`'den gelen `/durum` ve `/kar`
   komutlarını işler; update offset hata durumunda sıfırlanmaz.

## Veri / Eğitim Pipeline'ı (gece cron sırası)
```
02:45 shadow_labeler.py : 5 saatten eski shadow sinyalleri → sinyal sonrası 4 saatlik
                          15m mumlarla bracket simülasyonu (+%4 TP / -%3 SL, aynı mumda
                          ikisi de görülürse muhafazakârca SL) → etiketli CSV'ye append
03:00 ai_trainer.py     : V1 CSV'leri okur → kısmi+tam çıkışları POZİSYON bazında
                          birleştirir → kronolojik son %20 test, AUC hesabı →
                          AUC ≥ 0.55 ise tüm veriyle yeniden eğitip kaydeder,
                          değilse ESKİ MODEL KORUNUR (log: ai_trainer_history.log)
```
Trainer henüz V1 feature'larıyla eğitiyor; v2 + etiketli shadow verisi yeterince
birikince (birkaç yüz satır) genişletilmiş feature'larla eğitime geçilecek.

## Redis Key Şeması (prefix: `PORTFOY`)
`:islem_listesi` (sym→giriş fiyatı), `:islem_miktarlari` (USDT maliyet), `:max_karlar`,
`:half_sold`, `:ai_data` (sinyal feature'ları JSON), `:giris_zamanlari`, `:realize_karlar`,
`:last_rsi_check`, `:last_mom_check`, `:stop_counts`, `:kara_liste`, `:cooldowns`,
`:toplam_kar`, `:shadow_son`

## Bilinen Sınırlar
- **Borsa tarafında stop-loss emri YOK** — tüm koruma botun canlı olmasına bağlı.
- Sinyaller kapanmamış (canlı) 15m mumla üretiliyor (repaint riski).
- 3.5 aylık geriye dönük veri: sistem komisyon dahil yaklaşık başa baş; kârın tamamı
  tek aydan (Ağustos 2026). En büyük zarar kalemi ZAMAN AŞIMI çıkışları.

## PLANLANAN (henüz yapılmadı)
1. **Hibrit felaket stopu:** Alımdan sonra borsaya ~%-10 statik STOP_LOSS_LIMIT; her
   satıştan önce iptal, kısmi satış sonrası yeniden koyma, vip döngüsünde mutabakat.
2. Trainer'ın v2 + shadow etiketli veriyle genişletilmiş feature'larda eğitilmesi;
   AUC eşiği geçilince gölge moddan bloklama moduna geçiş.
3. systemd servisi (Restart=always) + açılış/kapanış Telegram bildirimi.
4. Shadow verisi birikince `ADX_TREND_ESIK` kalibrasyonu (20 mi, 23 mü; ya da "kasa yarıya" yumuşatması).
5. ccxt.pro WebSocket'e geçiş, dinamik kasa (bakiye yüzdesi) — düşük öncelik.
