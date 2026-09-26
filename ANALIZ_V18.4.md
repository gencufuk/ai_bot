# AI Ensemble Sniper — V18.3 Analizi ve V18.4 Çözümleri

> Tarih: 2026-09-26 · Kapsam: `main` dalındaki 17 dosya (kod, modeller, CSV'ler, loglar).
> Bu dokümandaki her sayı repodaki veriden hesaplandı; hesaplama yöntemi ilgili bölümde yazılı.
> Kod değişiklikleri `claude/ai-ensemble-sniper-optimization-zmtv9p` dalında; 69 otomatik test.

---

## 0. Özet

| # | Bulgu | Etki |
|---|---|---|
| 1 | **"Veri kıtlığı" piyasa durgunluğu değil, bir veri kaybı olayı.** 20 Eylül 22:47'de trainer 655 satır / 512 pozisyon görüyor, 21 Eylül 03:00'te 5 satır / 4 pozisyon. `_append_csv`, başlıksız eski V1 dosyasını "şema göçü" sırasında boşaltıyor (birebir yeniden üretildi). | 512 pozisyonluk eğitim geçmişi pipeline'dan düştü |
| 2 | **Canlıdaki iki model de doğrulamadan geçmemiş eski trainer ürünleri**, üstelik bot **bloklama modunda** (`AI_GOLGE_MOD = False`; doküman "gölge mod aktif" diyor). | Sinyallerin ~%70'i doğrulanmamış modelle bloklanıyor |
| 3 | Filtre modeli, 73 pozisyonluk örneklem dışı veride **saf gürültü** (Spearman 0.01, p=0.52). Core modelin 0.65 eşiği sınırda bir ayrım gösteriyor (tek yönlü p≈0.045, çoklu test düzeltmesi yok, sıra korelasyonu ~0). | Filtre, işlem sayısını azaltıp veri kıtlığını büyütüyor |
| 4 | AUC'nin 0.5'te sıkışmasının yapısal nedenleri: kayan etiket (USDT kârı + değişen çıkış mantığı), birbirinin tümleyeni iki etiket, sabit bir feature (`Sinyal_Encoded`: 163/163 sinyal MSB), tek 80/20 bölme ve her gece yeniden deneme (çoklu test). | Model mimarisi öğrenebilir durumda değil |
| 5 | VIP döngüsünde **2 kritik arıza modu** var; ikisi de *tüm* pozisyonların stop-loss'unu devre dışı bırakabiliyor (zehirli pozisyon, toplu ticker hatası). Bunlara ek olarak 4 yüksek önemde bulgu var. | Canlı para riski |
| 6 | Shadow etiketleyici sinyalin içinde bulunduğu 15m mumu **tamamen atlıyor**; stopların 17/32'si ilk 30 dakikada gerçekleşiyor. | Shadow etiketleri sistematik olarak yanlı |

**Önerilen sıra:** (1) bugün `AI_GOLGE_MOD = True` ve V18.4'ün risk düzeltmeleri; (2) kayıp verinin sunucuda aranması (§1.3); (3) `shadow_labeler.py` + `backfill_sinyaller.py` + `ai_trainer.py --kuru`; (4) trainer bir modeli kapılardan geçirirse bloklama moduna geçiş.

---

## 1. Veri kaybı olayı (Kritik sorun 2'nin gerçek nedeni)

### 1.1 Kanıt
- `ai_trainer_history.log`: `2026-09-20 22:47:07 → 655 işlem satırı -> 512 benzersiz pozisyon`, ardından `2026-09-21 03:00:04 → 5 işlem satırı -> 4 benzersiz pozisyon`. Piyasa bir gecede 508 pozisyonu geri almaz.
- `ai_trainer.log`: pandas `UserWarning: Could not infer format` uyarısı **21 Eylül'de başlıyor**. Trainer V1 CSV'yi `header=None` ile okuyor; bu uyarı dosyanın başına `Islem_Zamani` metinli bir başlık satırı geldiğini gösteriyor.
- Mevcut `core_islem_verileri.csv` başlıklı ve 21 Eylül 00:41'den başlıyor.

### 1.2 Mekanizma (birebir yeniden üretildi: `tests/test_csv_kayit.py::test_eski_fonksiyon_basliksiz_v1_verisini_yok_ediyordu`)
V18.3 `_append_csv`, dosyanın ilk satırı `columns` ile eşleşmeyince `pd.read_csv(file).reindex(columns=columns).to_csv(...)` çalıştırıyor. Eski V1 dosyası başlıksız olduğundan:
1. İlk **veri satırı** başlık kabul ediliyor ve kayboluyor,
2. Başlık adları hiçbir kolonla eşleşmediği için `reindex` **tüm satırları boşaltıyor** (`,,,,,,,,,,,,`),
3. Logda yalnızca `🧬 CSV şeması güncellendi (13→13 kolon)` görünüyor.

İkinci bir yol daha var: başlık-satır uzunluğu uyuşmazsa dosya `.bozuk_<ts>` adıyla kenara alınıyor ve trainer bu dosyayı hiç okumuyor.

### 1.3 Sunucuda yapılacak kontrol
```bash
cd /root
ls -la *.bozuk_* *.legacy_* 2>/dev/null          # kenara alınmış dosya var mı?
grep -c '^,,,,,,,,,,,,$' core_islem_verileri.csv   # boşaltılmış satır sayısı
grep -n "CSV şeması güncellendi\|CSV bozuk görünüyor" <bot log dosyası>
```
`.bozuk_*` varsa veri kurtarılabilir (`python tools/csv_onar.py <dosya> --v1-basliksiz`). Boş satırlar varsa veri o dosyada kalıcı olarak kaybolmuştur; VPS snapshot'ı/yedek aranmalı. Kaybolan veri V1 formatındaydı (3 feature), bu yüzden §4'teki backfill onu fazlasıyla telafi eder.

### 1.4 Diğer veri sorunları
- Yüklenen `core_islem_verileri_v2.csv`, `core_xgboost_model.json` ile **bayt bayt aynı** (md5 `9f70006a…`). Muhtemelen yükleme hatası; gerçek V2 dosyası elimde değil.
- `core_islem_verileri_v2.csv.yedek`: 25 kolonluk başlık altında 50 satır 27 alanlı (Sym_ADX/BTC_ADX başlık güncellenmeden AI_Skor'dan önce eklenmiş). **Deterministik olarak onarılabilir**: her şema sürümünün alan sayısı farklı. `tools/csv_onar.py` 52/52 satırı onarıyor (test: 16 Eylül satırlarında ADX boş, skorlar kaymamış).
- Shadow tarafı zaten onarılmış (yedeklerdeki 38/38 ve 30/30 satır güncel dosyalarda).

**V18.4 çözümü (`sniper/csv_kayit.py`):** mevcut hücre asla silinmez/kaydırılmaz; yeni kolonlar sona eklenir; şema değişikliği atomik yazılır (geçici dosya + fsync + `os.replace`); başlıksız dosyaya (tüm satırlar tutarlıysa) başlık eklenir, değilse dosya **bayt bayt arşivlenip Telegram'a bildirilir**; yarım kalan son satıra yapışma olmaz. CSV I/O `asyncio.to_thread` ile event loop dışında yapılır.

---

## 2. AI tarafında mevcut durum: neden AUC 0.45–0.52?

### 2.1 Canlıdaki modeller
| Model | Kanıt | Sonuç |
|---|---|---|
| `filter_model.json` | xgboost 3.4.0, depth 3, `scale_pos_weight=1`, `base_score=0.32504` = 196/603 | 15–16 Eylül'deki **eski** trainer (`"196 zararli islem ezberlendi"`); holdout yok |
| `core_xgboost_model.json` | xgboost 3.2.0, depth 4, `scale_pos_weight=1` | V2 trainer'dan bile eski (V2: depth 3 + ağırlık); `Sinyal_Encoded`'a **0 split** |

V2 trainer hiçbir modeli kaydetmediği için (tüm AUC'ler < 0.55) bu iki eski model duruyor. 16–20 Eylül'de CSV'ye kaydedilmiş canlı `AI_Skor`/`Filtre_Skor` değerleri diskteki modellerle birebir eşleşiyor, yani aşağıdaki test gerçek bir örneklem dışı testtir (`tests/test_model_karti.py`).

### 2.2 Örneklem dışı performans (73 pozisyon, 16–25 Eylül; iki model de bu tarihlerden önce eğitildi)
| Ölçü | Core | Filtre |
|---|---|---|
| AUC (hedef Net>+0.1 / Net<−0.1) | 0.548 (%95 GA 0.40–0.69) | 0.465 (%95 GA 0.34–0.60) |
| Spearman (skor, Net_Kar) | 0.076 (p=0.53) | 0.011 (p=0.93) |
| Bloklama kuralıyla engellenen vs geçen ort. PnL farkı | +0.245 USDT, **tek yönlü p=0.044** | −0.007 USDT, p=0.52 |

Birlikte uygulanan kural (`core<0.65 veya filtre>0.45`) 73 işlemin 51'ini (%70) bloklardı: engellenenler −7.96, geçenler +2.11 USDT. Bu etkinin tamamı core eşiğinden geliyor; filtrenin katkısı sıfır. p=0.044 tek yönlü ve çoklu test düzeltmesi yapılmamış bir değer, sıra korelasyonu da sıfır. Yani bu bir **edge kanıtı sayılmaz**, sadece "core modelin 0.65 üstü dilimi tamamen rastgele olmayabilir" ipucu.

### 2.3 Mod çelişkisi
Kod `AI_GOLGE_MOD = False`, doküman "GÖLGE MOD aktif". 23 Eylül'e kadar açılan işlemlerin 22'si bloklama kuralına takılırdı ama açıldı (gölge mod açıktı). 25 Eylül'deki işlemlerin tamamı eşiği geçenler ve ilk `AI_RED` kaydı 25 Eylül 11:14'te. Yani **bloklama 23–25 Eylül arasında açılmış**. Son günlerde hissedilen işlem azlığının bir kısmı bu.

### 2.4 Yapısal nedenler
1. **Kayan etiket.** Etiket gerçekleşen `Net_Kar_USDT`. Bu değer kasaya bağlı (balina 40, normal 20 USDT; ±0.1 USDT eşiği birinde %0.25, diğerinde %0.5) ve botun o haftaki çıkış mantığına bağlı (V18.3 kâr kilidini +%0.2'den +%1'e çekti, MAX_ATR_PCT 0.04→0.03). Aynı sinyal, bot sürümüne göre farklı etiket alıyor.
2. **İki model, tek bilgi.** `y_filtre = Net<−0.1`, `y_core = Net>+0.1`; ölü bölge dışında birbirinin tümleyeni. İki gürültülü kapıyı AND'lemek ensemble değil, gürültüyü ikiye katlamak.
3. **Sabit feature.** 163/163 sinyal `MSB` (eşzamanlı MSB+Engulf durumunda etiket hep MSB; hacim+RSI şartı zaten kırılım anlamına geliyor). `Sinyal_Encoded` bilgi taşımıyor.
4. **Doğrulama tasarımı.** ~500 pozisyonda n_test≈100 → AUC standart hatası ≈0.05. Trainer her gece yeniden denediği için şanslı bir bölme eninde sonunda 0.55'i geçer. Somut örnek: V3 testindeki **saf gürültü** verisinde tek bir kat 0.565–0.588 AUC verdi; V2'nin tek bölmesi o katı görseydi rastgele bir modeli yayına alırdı.
5. **`scale_pos_weight` + sabit eşik.** Ağırlık olasılık ölçeğini kaydırır; bottaki 0.65/0.45 eşikleri ölçeğe bağlı olduğu için anlamsızlaşır.
6. **Train/serve farkı.** Sinyal kapanmamış mumda üretiliyor: mumun 2. dakikasında 2.5× hacim ile 14. dakikasında 2.5× hacim çok farklı şeyler, ama model ikisini aynı görüyor.

**Beklenti yönetimi:** Bu tür momentum sinyallerinde gerçekçi örneklem dışı AUC 0.55–0.62 aralığıdır. Değer yüksek AUC'den değil, **en kötü %20–30'u veto etmekten** gelir. Bu yüzden V3 trainer AUC'nin yanında ekonomik testi de kapı olarak kullanıyor.

---

## 3. Soru 1 — Feature engineering, overfitting, sınıf dengesizliği

### 3.1 Hangi V2 feature'ları?
66 etiketli shadow sinyalinde tek değişkenli AUC'lerin hepsi 0.42–0.63 bandında (en yüksek `BTC_1h_Degisim` 0.63). **139 örnekle feature seçmek istatistiksel olarak mümkün değil**; bu yüzden seçim ilke bazlı yapıldı ve asıl kararı backfill verisi verecek.

**Kullanılacaklar** (geçmişe dönük üretilebilir + ölçekten bağımsız; `sniper/ozellikler.py::turetilmis_ozellikler`):
- `Giris_RSI`, `Log_Vol_Oran`, `Giris_ATR_Pct`
- **ATR cinsinden uzaklıklar:** `EMA15m_ATR`, `EMA1h_ATR`, `Pump3s_ATR`, `Pump6s_ATR`, `Zirve_ATR`. Ham yüzde uzaklık, oynaklığı farklı coinler arasında karşılaştırılamaz: %4 uzaklık PEPE'de sıradan, BCH'de aşırıdır. ATR'ye bölmek "hareket kendi oynaklığına göre ne kadar gerilmiş" sorusunu cevaplar.
- BTC bağlamı: `BTC_1h_Degisim`, `BTC_EMA_Uzaklik`, `BTC_ADX`; mikro rejim: `Sym_ADX`, `Kapali_Mum_Onay`
- **Yeni:** `Piyasa_Genislik` (hacimli paritelerde 24s pozitif olanların oranı; ticker verisinden bedava), `Saat_Sin/Cos` (döngüsel; ham `Saat` 23→0 sıçraması yapar)
- **Yeni, canlı kayıt:** `Mum_Ilerleme` ve `Hacim_Hizi` (= Vol_Oran / mum ilerlemesi). Kısmi mum sorununu doğrudan ölçer.

**Kullanılmayacaklar:**
- `AI_Skor`, `Filtre_Skor`: eski modelin çıktısı yeni modele girerse sızıntı olur (kodda yasak listesinde).
- `Sinyal_Encoded`: sabit.
- `OB_Oran`: sadece 28 dolu örnek var ve 20 kademenin toplamı orta fiyattan uzak likiditenin baskısı altında. Yerine `OB_Oran_Yakin` (orta fiyatın ±%1'i) ve `Spread_Bps` kaydediliyor. Canlıda ≥300 örnek birikince denenmeli (geçmişe dönük üretilemezler).
- `Stop_Sayisi`: bot durumu, seyrek, geçmişte üretilemez.

Veride 400 örneğe kadar 8 feature'lık kompakt set, sonrasında 16 feature'lık geniş set kullanılıyor (her sınıfta feature başına ≥15 olay kuralı).

### 3.2 Overfitting'e karşı katmanlar (`ai_trainer.py`)
1. **Purged walk-forward CV:** zaman sıralı, genişleyen pencere; test başlangıcından önceki 4 saat (etiket penceresi) eğitimden atılır.
2. **Episod gruplama:** aynı sembolde 4 saat içindeki ardışık sinyaller tek episod sayılır (22/66 shadow sinyali böyle). Bootstrap episod bazlı yapılır, eğitimde benzersizlik ağırlığı 1/episod_boyutu uygulanır.
3. **Düşük kapasite:** depth-1 (stump, GAM benzeri, etkileşim öğrenemez) ve depth-2 adaylar; `min_child_weight` (lojistik hessian ≤0.25 → yaprak başına ≥12–20 örnek), L2, `subsample`/`colsample`, düşük öğrenme hızı.
4. **Lineer baz model** (numpy L2-lojistik): ağaç bunu geçemiyorsa derinlik aşırı öğrenmedir.
5. **Kapılar:** havuzlanmış OOS AUC ≥0.55 **ve** %95 alt güven sınırı >0.5 **ve** katların çoğunda >0.5 **ve** ekonomik test (en kötü %30'u engellemek ortalama net getiriyi artırıyor mu, permütasyon p<0.05) **ve** canlı alt kümede AUC ≥0.5.
6. **Eşik optimize edilmez:** OOS skorlarının %30 kantili alınır. Eşik optimizasyonu kendi başına bir overfitting kaynağıdır.
7. **Adversarial validation:** model canlı veriyi backfill'den ayırt edebiliyorsa (AUC ≫0.5) raporlanır. Kısmi mum etkisini doğrudan gösterir.

### 3.3 SMOTE? — Hayır
- **Dengesizlik sorun değil:** pozitif oran %34–52. SMOTE ciddi dengesizlik (≤%5) aracıdır.
- **Zaman serisinde sızıntı:** CV'den önce uygulanırsa sentetik örnekler geleceğe ait noktalardan interpolasyonla üretilir.
- **Sahte piyasa durumları:** RSI 80 ile RSI 60 arasında interpolasyonla üretilen "sinyal" hiçbir zaman var olmamış bir OHLCV yolunu temsil eder.
- **Kalibrasyonu bozar**, eşikleri anlamsızlaştırır (`scale_pos_weight` gibi).

Gerçek veri çoğaltma yolu **backfill** (§4). Ağırlıklandırma gerekiyorsa `scale_pos_weight` yerine benzersizlik ağırlığı (uygulandı) ve isteğe bağlı olarak |getiri| ağırlığı kullanılmalı.

---

## 4. Soru 2 — Shadow + core birleştirme: mantıklı mı, nasıl güvenli?

**Evet, ama yalnızca tek tip etiketle.** Mevcut hâliyle birleştirmek zararlı olurdu:
- Shadow'ların 61/66'sı `REJIM_YATAY` (BTC_ADX<20), core'ların tamamı TREND rejiminde.
- Shadow etiketi: sabit +%4/−%3, komisyonsuz, 4 saat. Core etiketi: gerçekleşen USDT kârı, dinamik çıkış.
- İkisi birleştirilirse `BTC_ADX` "hangi etiketleyiciden geldi"nin mükemmel bir vekili olur. Model trade kalitesini değil **etiket kaynağını** öğrenir (confounding).

### 4.1 Uygulanan çözüm
- **`sniper/etiketleme.py`:** tek etiket fonksiyonu. Bariyerler botun kendi risk parametreleri: alt bariyer ATR stopu (%2.5–5.5, balina %2), üst bariyer ilk kâr kademesi (%3–6), +%2.5 görülünce +%1 kâr kilidi, 4 saat pencere, komisyon dahil, aynı mumda iki bariyer görülürse muhafazakâr (SL), gap açılışta açılış fiyatından çıkış. Hedef `y = 1[net getiri > 0]`.
- **`shadow_labeler.py` V2:** core pozisyonlarını da **aynı fonksiyonla** etiketler.
  - **V1 bug'ı:** `since=ts+1` ile 15m mumlar isteniyordu; Binance `startTime`'dan sonra açılan mumu döndürdüğü için sinyalin içinde bulunduğu 15m mum tamamen atlanıyordu. Stopların 17/32'si ilk 30 dakikada (medyan 0.48 saat) gerçekleştiğinden bu, shadow etiketlerini sistematik olarak iyimser yapıyordu. V2, 1m mumları sinyalin dakikasından başlatır.
  - Durum dosyası yok: çıktıdaki `(Anahtar, Etiket_Surumu)` çiftleri durumun kendisidir. Verisi eksik sinyal tekrar denenir; etiket tanımı değişince her şey otomatik yeniden etiketlenir (girdiler OHLCV olduğu için ucuzdur).
- **`sniper/etiket_deposu.py`:** core/shadow/backfill ortak şeması; `Kaynak` kolonu modele **feature olarak verilmez**.

### 4.2 Veri kıtlığının kök çözümü: backfill (`backfill_sinyaller.py`)
Giriş kuralı tamamen OHLCV'ye dayalı, dolayısıyla geçmiş sinyaller **canlıyla aynı kodla** yeniden üretilebilir. BTC EMA200 histerezisi, ani çöküş koruması, ADX rejimi ve "12M+ hacimli, en çok yükselen 10 parite" taraması geçmişe dönük emüle edilir. 80 sembol × 180 gün, 34 pozisyon yerine **binlerce** etiketli örnek demek. Bu, sorudaki "transfer learning" fikrinin sağlam hâli: backfill ile öğren, canlı alt kümede doğrula (trainer canlı OOS AUC'yi ayrıca raporluyor ve kapı olarak kullanıyor).
- **Look-ahead testi:** dünya sinyalden 1 dk sonra dondurulduğunda üretilen feature'lar tam veriyle üretilenlerle birebir aynı (saat başı sinyaller dahil).
- **Bilinen yanlılıklar:** evren bugünün en hacimli paritelerinden seçiliyor (survivorship; geçmiş hacim filtresi kısmen azaltıyor). Backfill kapalı mumla çalışıyor, canlı bot kısmi mumla; adversarial validation bu farkı ölçer.
- **Tam parite için strateji kararı:** `SINYAL_KAPALI_MUM = True` (bot, V18.4). Repaint biter, backfill ile birebir aynı sinyal üretilir. Bedeli: giriş, mum kapanışını bekler.

### 4.3 Rejim filtresi hakkında bir uyarı
V1 etiketleriyle `REJIM_YATAY` shadow'ları ortalama **+%0.98** (medyan +%1.28, %61 pozitif) görünüyor. Bu, yatay filtrenin kazanan sinyalleri elediğine işaret *edebilir*. Ancak V1 etiketi komisyonsuz, ilk 15 dakikayı atlıyor ve 22/66 sinyal çakışıyor. **ADX_TREND_ESIK'i bu veriyle kalibre etmeyin**; V2 etiketleri gelince `python shadow_labeler.py` çıktısındaki Kaynak/Sebep özet tablosuyla karşılaştırın.

---

## 5. Soru 3 — VIP Cüzdan: mantık hataları, yarış durumları, bloklayıcılar

V18.3 çıkış mantığı saf bir modüle (`sniper/risk_motoru.py`) taşındı. **200.000 rastgele durumluk diferansiyel test**, yeni motorun V18.3 ile birebir aynı kararı verdiğini gösteriyor. Tek istisna aşağıdaki #6'daki bilinçli düzeltme. Her bulgu `tests/test_bot_entegrasyon.py`'de fakeredis + ccxt davranışını taklit eden sahte borsa ile senaryo testi olarak var. Mutasyon testiyle doğrulandı: düzeltmeler geri alınınca ilgili testler kırılıyor.

| # | Önem | V18.3'teki sorun | Senaryo | V18.4 |
|---|---|---|---|---|
| 1 | **KRİTİK** | `amount_to_precision` try **dışında** (satır 450/477/523); ccxt 0/küçük miktarda `InvalidOrder` fırlatır | Bakiyesi sıfırlanmış tek pozisyon (elle satış, teyit edilemeyen satış, ileride borsa-stop emrinde kilitli coin) exit koşuluna girer → tüm tur çöker → **diğer pozisyonların stop'u çalışmaz**, 30 dk'lık cüzdan senkronuna kadar | Sembol bazında izolasyon; sıfır/toz bakiye yakalanır, pozisyon takipten çıkarılır + bildirim |
| 2 | **KRİTİK** | Toplu `fetch_tickers(semboller)` tek sembolde hata verirse (delist, BadSymbol) tüm istek düşer | Tek delist edilen coin → hiçbir pozisyonun fiyatı yok → **tüm stoplar kapalı** | Sembol bazında geri dönüş + "izlenemiyor" alarmı |
| 3 | YÜKSEK | Satış miktarı cüzdandaki **tüm serbest bakiye** | Aynı coin elle tutuluyorsa ya da ikinci bot aynı hesabı kullanıyorsa onlar da satılır | Alımda net adet kaydedilir (komisyon düşülmüş), satış onu aşmaz. Eski pozisyonlar eski davranışta |
| 4 | YÜKSEK | Market emir idempotent değil | Emir borsada gerçekleşir, cevap zaman aşımına uğrar → "başarısız" sayılır → sonraki turda kalanın yarısı **tekrar** satılır; tam çıkışta satış kaydı hiç düşmez | `newClientOrderId` + belirsiz hatada ID ile sorgu |
| 5 | YÜKSEK | Kısmi satış istisna verince `continue` | LOT_SIZE vb. kalıcı hatada kısmi satış her turda denenir, **tam çıkış (stop) hiç kontrol edilmez**, fiyat stopun altına inse bile | Yarım satış hatasında koruyucu tam çıkış yine denenir |
| 6 | YÜKSEK | Yarı satış sonrası `cikis = 0.005` sabiti | Moon bag +%5.2'de, ATR %2.8 (ilk eşik %5.6) → kalan yarı +%1 kilidi yerine **+%0.5'e kadar** tutulur. V18.3 kilidi 0.002→0.010 yükseltirken bu satır unutulmuş | `max(base_stop, 0.005)` |
| 7 | ORTA | Min-notional altı pozisyonda hiçbir şey yapılmıyor | 1–5 USDT'lik kalıntı exit koşulunda **sonsuza dek** takılır, her 2 sn'de `fetch_balance` (ağırlık 20 → 600/dk boşa) | Takipten çıkarılır + bildirim |
| 8 | ORTA | Cüzdan senkronu: bakiye await edilirken radar alım yapabilir; Redis görüntüsü bakiyeden **sonra** okunuyor | Taze alım Redis'te var, bakiyede yok → "toz" sanılıp silinir → 30 dk sonra yanlış giriş fiyatıyla sahiplenilir, `ai_data` kaybolur | Görüntü önce alınır, 10 dk'dan genç pozisyona dokunulmaz, sahiplenmeden önce canlı kontrol |
| 9 | ORTA | Sahiplenme her >10 USDT'lik coini alır, eski alanları temizlemez | **Elle tutulan coin 4 saat sonra ZAMAN AŞIMI ile satılır**; eski bir `half_sold=1` kalıntısı yeni pozisyonda +%0.5'te anında çıkışa yol açar | Tüm alanlar sıfırlanır, bildirim, `.env`'de `MANUEL_COINLER` |
| 10 | ORTA | `await telegram_mesaj_gonder` risk döngüsünün içinde (10 sn timeout) | Telegram yavaşken bir stop mesajı diğer pozisyonların kontrolünü 10 sn'ye kadar geciktirir | Kuyruk + ayrı görev; POST/JSON, 429'da bekleme, Markdown hatasında düz metin |
| 11 | ORTA | Senkron Redis, `socket_timeout=None` | Redis takılırsa (BGSAVE fork, swap) **event loop sonsuza dek donar** | 3 sn timeout; pozisyon durumu tek round-trip'te okunur (önce pozisyon başına ~8 çağrı) |
| 12 | ORTA | Pozisyon durumu 12 ayrı hash'te, tek tek HSET/HDEL | Temizlik ortasında hata → yarım durum (ör. kalan `half_sold`) | Alım/çıkış/sahiplenme tek MULTI/EXEC; alımda eski alanlar silinir |
| 13 | DÜŞÜK | Zaman aşımı uzatması `giris_zamanlari`'nı sıfırlıyor | CSV'deki `Sure_Saat` yanlış; core etiketleme gerçek girişi bulamaz | Ayrı `zaman_ref` |
| 14 | DÜŞÜK | Momentum kontrolü canlı mumun kısmi hacmini içeriyor | Mumun başında "hacim söndü" sistematik tetiklenir | Kapanmış mumlar |
| 15 | DÜŞÜK | Moon bag RSI'ı 25 mumla (giriş 100 mum) | Wilder RSI ısınmıyor, iki RSI farklı ölçekte | 100 mum |
| 16 | DÜŞÜK | `get_fill_price`'taki `fills` dalı ölü (ccxt birleşik emirde `trades` var) | `average` yoksa dolum fiyatı yerine ask kullanılır | `cost/filled` ve `info.fills` |

**Event loop bloklayıcıları, ölçülmüş olarak:**
- En büyüğü: radar'ın her 15 sn'de tüm sembollerle yaptığı `fetch_tickers()`. ~1.5 MB JSON + ccxt parse ≈ **100 ms senkron CPU** (bu makinede; küçük VPS'te 2–3×). Bu süre boyunca VIP döngüsü çalışamaz.
- İkincisi: tek `ccxt` örneği ve ortak hız sınırlayıcı. Radar patlaması (10 aday × 3 çağrı) VIP'in fiyat isteğini kuyrukta bekletir.

Öneri: risk döngüsü için ayrı bir `ccxt` örneği, orta vadede WebSocket (`ccxt.pro` `watch_tickers` / `!miniTicker@arr`).

**Planlanan "hibrit felaket stopu" için kritik not:** coin borsada `STOP_LOSS_LIMIT` emrinde kilitliyken `free=0` olur; V18.3 kodunda bu doğrudan #1'i (tüm döngünün çökmesi) tetiklerdi. V18.4'te çökme yok, ama tasarım **satıştan önce borsa stop emrini iptal etmeli** ve iptal→satış arasını idempotent yönetmelidir.

**Diğer gözlemler:**
- `SOXLB/USDT`, `SNDKB/USDT` (fiyat 149 / 1894) hisse tokeni gibi görünüyor ve ikisi de stop oldu. Seans dışı fiyatlanan varlıklar momentum mantığına uymaz; kontrol edip yasaklı listeye eklemeyi düşünün.
- `YASAKLI_COINLER` alt dize eşleşmesi kullanıyor ('EUR', içinde EUR geçen her sembolü eler).
- `ufuk_islem_verileri.csv` ikinci bir bot olduğunu gösteriyor. Aynı Binance hesabını veya aynı Redis `PORTFOY` önekini kullanıyorsa botlar birbirinin coinlerini sahiplenir/satar (#3, #9).

---

## 6. Dal içeriği

| Dosya | Durum | Özet |
|---|---|---|
| `ai_bot.py` | değişti (V18.4) | §5 düzeltmeleri, model kartı + hot reload, watchdog, açılış bildirimi, yeni kayıt feature'ları. **Strateji parametreleri ve `AI_GOLGE_MOD` değiştirilmedi** |
| `ai_trainer.py` | yeniden yazıldı (V3) | §3.2 |
| `shadow_labeler.py` | yeniden yazıldı (V2) | §4.1 |
| `backfill_sinyaller.py` | yeni | §4.2 |
| `sniper/*.py` | yeni | csv_kayit, risk_motoru, ozellikler, etiketleme, etiket_deposu, model_karti |
| `tools/csv_onar.py` | yeni | kaymış yedeklerin onarımı |
| `tests/` | yeni | 69 test (`python -m pytest tests/ -q`) |

Yeni pip bağımlılığı yok (xgboost, pandas, pandas_ta, ccxt, redis, aiohttp mevcut). pandas ≥2.0 gerekli (loglardaki uyarı sunucuda 2.x olduğunu gösteriyor).

---

## 7. Deploy adımları

```bash
# 0) Yedek + botu durdur
cp -a /root /root_yedek_$(date +%F)

# 1) Kayıp veri kontrolü (§1.3)

# 2) Dosyaları kopyala: ai_bot.py ai_trainer.py shadow_labeler.py backfill_sinyaller.py sniper/ tools/

# 3) V2 yedeğini onar (labeler *.onarildi.csv dosyalarını otomatik okur)
python tools/csv_onar.py core_islem_verileri_v2.csv.yedek

# 4) Karar: ai_bot.py -> AI_GOLGE_MOD = True (önerilen; §2.2). İsteğe bağlı: filter_model.json'u yeniden adlandır.
#    Elle tuttuğun coin varsa .env: MANUEL_COINLER=ETH,SOL

# 5) Botu başlat (systemd önerilir: Restart=always)

# 6) Etiketle + backfill + kuru eğitim
python shadow_labeler.py
python backfill_sinyaller.py --gun 180 --evren 80      # uzun sürer, kesilirse kaldığı yerden devam eder
python ai_trainer.py --kuru && cat egitim_raporu.json
```
Cron değişmez (02:45 labeler, 03:00 trainer). Trainer bir modeli yayına alırsa bot 5 dk içinde otomatik yükler ve Telegram'dan bildirir.

**Gölge moddan çıkış kriteri:** trainer `karar: yayinda` üretmeli, **ve** gölge modda en az 1–2 hafta boyunca canlı skorlarla gerçekleşen sonuçlar tutarlı olmalı (`egitim_raporu.json` → `canli_oos_auc`). Kart eşiği, sinyallerin ~%30'unu engelleyecek şekilde seçilir; canlıdaki `AI_RED` oranı bundan çok saparsa dağılım kaymıştır.

## 8. Sonraki adımlar (öncelik sırasıyla)
1. `SINYAL_KAPALI_MUM = True` denemesi (repaint + train/serve paritesi).
2. Risk döngüsü için ayrı ccxt örneği → WebSocket fiyat akışı.
3. Borsa tarafı felaket stopu (§5'teki notla), systemd `WatchdogSec`.
4. Canlı `OB_Oran_Yakin` / `Spread_Bps` / `Hacim_Hizi` ≥300 örneğe ulaşınca feature listesine ekleme ve karşılaştırma.
5. Aylık backfill yenilemesi ve `ETIKET_SURUMU` değişince otomatik yeniden etiketleme.
