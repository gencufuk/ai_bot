# AI Ensemble Sniper — V18.3 Analizi ve V18.4 Çözümleri

> Tarih: 2026-09-26 · Kapsam: `main` dalındaki 17 dosya (kod, modeller, CSV'ler, loglar).
> Bu dokümandaki her sayı repodaki veriden hesaplandı (`veri/`: sunucu kopyası `veri/sunucu_2026-09-26/`, kurtarılmış geçmiş, Ağustos botunun kodu `veri/eski_surum/`); hesaplama yöntemi ilgili bölümde yazılı.
> Kod değişiklikleri `claude/ai-ensemble-sniper-optimization-zmtv9p` dalında; 80 otomatik test
> (pandas 2.3 ve 3.0'da), kritik düzeltmeler mutasyon testiyle doğrulandı, diff bağımsız bir review'dan geçti (§9).

---

## 0. Özet

| # | Bulgu | Etki |
|---|---|---|
| 1 | **"Veri kıtlığı" piyasa durgunluğu değil, bir veri kaybı olayı.** 20 Eylül 22:47'de trainer 655 satır / 512 pozisyon görüyor, 21 Eylül 03:00'te 5 satır / 4 pozisyon. `_append_csv`, başlıksız eski V1 dosyasını "şema göçü" sırasında boşaltıyor (birebir yeniden üretildi). | 512 pozisyonluk eğitim geçmişi pipeline'dan düştü |
| 2 | **Canlıdaki iki model de doğrulamadan geçmemiş eski trainer ürünleri**, üstelik bot **bloklama modunda** (`AI_GOLGE_MOD = False`; doküman "gölge mod aktif" diyor). | Sinyallerin ~%70'i doğrulanmamış modelle bloklanıyor |
| 3 | Filtre modeli, 73 pozisyonluk örneklem dışı veride **saf gürültü** (Spearman 0.01, p=0.52). Core modelin 0.65 eşiği sınırda bir ayrım gösteriyor (tek yönlü p≈0.045, çoklu test düzeltmesi yok, sıra korelasyonu ~0). | Filtre, işlem sayısını azaltıp veri kıtlığını büyütüyor |
| 4 | AUC'nin 0.5'te sıkışmasının yapısal nedenleri: kayan etiket (USDT kârı + değişen çıkış mantığı), birbirinin tümleyeni iki etiket, neredeyse sabit bir feature (`Sinyal_Encoded`: son 163 sinyalin tamamı, 4 aylık tarihçenin %98.8'i MSB), tek 80/20 bölme ve her gece yeniden deneme (çoklu test). | Model mimarisi öğrenebilir durumda değil |
| 5 | VIP döngüsünde **2 kritik arıza modu** var; ikisi de *tüm* pozisyonların stop-loss'unu devre dışı bırakabiliyor (zehirli pozisyon, toplu ticker hatası). Bunlara ek olarak 4 yüksek önemde bulgu var. | Canlı para riski |
| 6 | Shadow etiketleyici sinyalin içinde bulunduğu 15m mumu **tamamen atlıyor**; stopların 17/32'si ilk 30 dakikada gerçekleşiyor. | Shadow etiketleri sistematik olarak yanlı |

**Önerilen sıra:** (1) V18.4'ün kurulumu (`KURULUM.md`, sürükle-bırak paketi) ve gürültü olduğu ölçülen `filter_model.json`'un kaldırılması; (2) `shadow_labeler.py` + `backfill_sinyaller.py` + `ai_trainer.py --kuru`; (3) trainer kapılardan geçen bir model üretince eski core modelin yerini alır.

**Güncelleme (kullanıcının yedeğiyle):** 20 Eylül'de kaybolan V1 tarihçesi, kullanıcının şema değişikliğinden önceki yedeğinden **boşluksuz kurtarıldı** (608 + 52 + 45 = 705 satır; trainer loglarındaki 603 → 655 sayılarıyla birebir tutarlı). Gerçek işlemlerin bütçe kısıtlı yeniden oynatması §10'da; 5/7 Ağustos başlangıçlı sonuçlar ve V18.4 filtrelerinin gerçek girişlere etkisi §10.1–10.2'de. V18.4 çıkış mantığı ve rejim filtresinin fiyat verisiyle simülasyonu için `tools/v184_simulasyon.py` yazıldı (§10.3) ve 26 Eylül'de sunucuda çalıştırıldı (§10.4). Sonuç: eski core modelin vetosu bu dönemde işe yarıyor, V18.4 çıkışları Ağustos'unkilerle başa baş. 30 Eylül'de 6 aylık etiketli sinyallerle zararın kaynağı incelendi (§10.6): satış değil giriş; girişin 6 ayda kalıcı bir üstünlüğü görünmüyor. Aynı gün yapılan 9 aylık piyasa taramasında (§10.7) canlı kurulum %−6.7 kaybettiriyor; 9 ayın yalnız biri (Ağustos) artıda. Literatürden seçilen 9 alım kuralı 2023–2026 günlük verisinde test edildi (§11.1): hiçbiri 2025–26'da komisyon sonrası güvenilir kazanç göstermedi. Kanıtı olan tek yaklaşım BTC'de yavaş bir trend filtresi; bu da düşüşü azaltıyor, günlük kazanç üretmiyor. Kullanıcının "yükselişi erken yakala" fikri 9 ayda test edildi (§11.3): 4 saatlik radar 24 saatlikten kötü, canlı radar değişmedi. Kaybettiren çıkışların %86'sı stop; stopların yarısı alımdan sonraki 32 dakikada. "Daha erken sat" fikri çıkış ayarlarıyla aynı sinyallerde denendi (§11.4): kârı erken almak kötüleştiriyor; yalnız 2 saatlik zaman aşımı tutarlı iyileşme gösteriyor, ama kanıt sınırda. Canlıya almadan önce 2025 verisinde doğrulanacak.

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
`.bozuk_*` varsa veri kurtarılabilir (`python tools/csv_onar.py <dosya> --v1-basliksiz`). Boş satırlar varsa veri o dosyada kalıcı olarak kaybolmuştur.

**Kurtarma yapıldı:** kullanıcının 16 Eylül öncesi yedeği (608 satır, 31 Mayıs – 14 Eylül) + V2 yedeği (52 satır, 16–20 Eylül) + güncel V1 (45 satır, 21–25 Eylül) = tam tarihçe. Yedekte iki kirlilik bulundu ve ayıklandı:
- **Başka bir botun 9 işlemi** (6–14 Eylül): ana botun kodunda olmayan çıkış tipleri (`🏹 KADEMELİ/GÜÇLÜ/İLK TRAILING STOP`), 40 USDT büyüklük, komisyonsuz kayıt, 12–434 saat tutma (BTC 18 gün). REZ/USDT'deki −%30.9'luk kayıp (−12.36 USDT) bunlardan biri. Aynı dosyaya iki botun yazması tehlikelidir; ikinci botun ayrı klasör/Redis öneki kullanması gerekir.
- **5 sahiplenilmiş bakiye**: feature'sız, sinyalsiz pozisyonlar. 18 Temmuz'da bir **stablecoin (USD1)** ve **~178 USDT'lik ENA** bakiyesi sahiplenilip zaman aşımıyla satıldı (−4.02 USDT). Bu, §5 #9'daki riskin gerçekte yaşanmış hâli.

Temiz tarihçe `core_islem_verileri_v2_gecmis.onarildi.csv` olarak pakette; labeler bu adı otomatik okur ve 502 geçmiş pozisyonu tek tip etiketle etiketler (canlı dağılımdan gelen gerçek girişler, backfill'in kısmi mum farkını dengeler).

Ayrıca **31 Mayıs – 5 Ağustos arası tüm işlemler komisyonsuz kaydedilmiş** (276 satır). O dönemin kayıtlı kârı gerçekte olduğundan iyi görünür; §10'daki hesaplar fiyatlardan komisyon dahil yeniden yapıldı.

### 1.4 Diğer veri sorunları
- Yüklenen `core_islem_verileri_v2.csv`, `core_xgboost_model.json` ile **bayt bayt aynı** (md5 `9f70006a…`). Muhtemelen yükleme hatası; gerçek V2 dosyası elimde değil. Yinelenen kopya repodan kaldırıldı; temiz kurulum aracı JSON içeren bir CSV'yi geri getirmez.
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

**Öneri (revize):** İlk sürümde gölge modu önermiştim, çünkü V18.3'te bloklama eğitim verisini kısıyordu. V18.4 labeler'ı engellenen (`AI_RED`) sinyalleri de aynı etiketle işlediği için bu gerekçe ortadan kalktı. Kanıt bu hâliyle: filtre modeli saf gürültü (p=0.52); core modelin 0.65 eşiği zayıf ama pozitif bir ayrım gösteriyor (p≈0.044). Strateji tarihsel olarak başa baş olduğundan (§10) daha az işlem daha düşük varyans demek. Bu yüzden **bloklama açık kalsın (`AI_GOLGE_MOD = False`), ama `filter_model.json` kaldırılsın** (`mv filter_model.json filter_model.json.emekli`). V3 trainer doğrulanmış bir model üretince eski core modelin yerini alır.

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
6. **Eşik optimize edilmez:** OOS skorlarının %30 kantili alınır. Eşik optimizasyonu kendi başına bir overfitting kaynağıdır. ≥50 canlı OOS örnek varsa kantil **canlı** skorlardan alınır: backfill kapalı mumla, canlı bot kısmi mumla çalıştığından havuz eşiği canlıda farklı oranda engelleyebilir. Sentetik kovaryat kaymasında havuz eşiği canlının %46'sını engelliyordu, rapor bunu gösterir. Bot tarafında da son 50 AI kararındaki engelleme oranı hedeften çok saparsa (gölge modda dahil) Telegram uyarısı gelir.
7. **Ekonomik test episodun ilk sinyaliyle:** bot pozisyondayken aynı sembolün sonraki sinyallerini zaten işlemez ve çakışan sinyaller bağımsız değildir.
8. **Adversarial validation:** model canlı veriyi backfill'den ayırt edebiliyorsa (AUC ≫0.5) raporlanır. Kısmi mum etkisini doğrudan gösterir.

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
  - Sinyal dakikanın ortasındaysa o 1m mumun açılış/tepe/dip değerleri kısmen sinyal ÖNCESİNE aittir (kırılım sinyali dakika içi sert bir hareketin hemen ardından gelir). Bu mum yalnızca kapanışıyla, yani sinyalden sonraki ilk kesin fiyatla temsil edilir; aksi hâlde yükselişin başlangıç fiyatı sahte bir stop üretir.
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
| 4 | YÜKSEK | Market emir idempotent değil | Emir borsada gerçekleşir, cevap zaman aşımına uğrar → "başarısız" sayılır → sonraki turda kalanın yarısı **tekrar** satılır ya da aynı coin **iki kez alınır** (ilk lot stopsuz kalır); tam çıkışta satış kaydı hiç düşmez | `newClientOrderId`; yalnızca kesin retler (geçersiz emir, yetersiz bakiye, hatalı istek, yetki, rate-limit, timestamp) "gerçekleşmedi" sayılır, Binance `-1000/-1001/-1006` ("execution status unknown" → ccxt `OperationFailed`) ve yarım okunan cevap dahil diğer her hata ID ile sorgulanır. Teyit edilemeyen alımda 1 saat cooldown + uyarı; 0 dolumlu (EXPIRED) emir başarısız sayılır; emir sürerken cüzdan senkronu o coini sahiplenmez |
| 5 | YÜKSEK | Kısmi satış istisna verince `continue` | LOT_SIZE vb. kalıcı hatada kısmi satış her turda denenir, **tam çıkış (stop) hiç kontrol edilmez**, fiyat stopun altına inse bile | Yarım satış hatasında koruyucu tam çıkış yine denenir |
| 6 | YÜKSEK | Yarı satış sonrası `cikis = 0.005` sabiti | Moon bag +%5.2'de, ATR %2.8 (ilk eşik %5.6) → kalan yarı +%1 kilidi yerine **+%0.5'e kadar** tutulur. V18.3 kilidi 0.002→0.010 yükseltirken bu satır unutulmuş | `max(base_stop, 0.005)` |
| 7 | ORTA | Min-notional altı pozisyonda hiçbir şey yapılmıyor | 1–5 USDT'lik kalıntı exit koşulunda **sonsuza dek** takılır, her 2 sn'de `fetch_balance` (ağırlık 20 → 600/dk boşa) | Takipten çıkarılır + bildirim |
| 8 | ORTA | Cüzdan senkronu: bakiye await edilirken radar alım yapabilir; Redis görüntüsü bakiyeden **sonra** okunuyor | Taze alım Redis'te var, bakiyede yok → "toz" sanılıp silinir → 30 dk sonra yanlış giriş fiyatıyla sahiplenilir, `ai_data` kaybolur | Görüntü önce alınır, 10 dk'dan genç pozisyona dokunulmaz, sahiplenmeden önce canlı kontrol |
| 9 | ORTA | Sahiplenme her >10 USDT'lik coini alır, eski alanları temizlemez | **Elle tutulan coin 4 saat sonra ZAMAN AŞIMI ile satılır**; eski bir `half_sold=1` kalıntısı yeni pozisyonda +%0.5'te anında çıkışa yol açar | Tüm alanlar sıfırlanır, bildirim; `.env`'de `MANUEL_COINLER`: bu coinler sahiplenilmez, alınmaz, V18.3'ün eskiden sahiplendiği pozisyonları dahil takipten çıkarılır ve emir katmanı satışı reddeder |
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
| `tests/` | yeni | 80 test: `pip install pytest fakeredis && python -m pytest tests/ -q` (borsa/Redis/Telegram gerekmez) |

Yeni pip bağımlılığı yok (xgboost, pandas, pandas_ta, ccxt, redis, aiohttp mevcut; `XGBClassifier` scikit-learn'e ihtiyaç duyar, V18.3 de bunu kullandığı için sunucuda kurulu olmalı). Test takımı iki yığında geçti: Python 3.12 + pandas 2.3.3 / numpy 2.2.6 ve pandas 3.0.6 / numpy 2.2.6 (xgboost 3.4.1, pandas_ta 0.4.71b0, ccxt 4.5). pandas_ta 0.4.x zaten Python ≥3.12, pandas ≥2.3.2 istiyor.

---

## 7. Deploy adımları

Önerilen yol **temiz başlangıç**: `KURULUM.md` → `tools/temiz_kurulum.py`. Araç önce kuru çalışır, planı gösterir. Sonra hiçbir şeyi silmeden eski bot dosyalarını `/root/eski_bot_<tarih>/` klasörüne taşır, V18.4'ü kurar ve canlı verileri geri kopyalar: işlem CSV'leri, shadow sinyalleri, core model.
- Getirilmeyenler: filtre modeli, eski loglar ve yedekler.
- Dokunulmayanlar: `.env`, `venv/`, Redis dosyaları, crontab'da geçen dosyalar ve başka bota ait veriler.
- Geri alma: tek komut (`--geri-al`).

Cron: 02:45 labeler, 03:00 trainer. Trainer bir modeli yayına alırsa bot 5 dk içinde otomatik yükler ve Telegram'dan bildirir.

**Yeni modele güven kriteri:** trainer `karar: yayinda` üretmeli; `egitim_raporu.json` içindeki `canli_oos_auc` ve `esik.canli_engelleme_havuz_esigiyle` makul olmalı. Kart eşiği sinyallerin ~%30'unu engelleyecek şekilde seçilir. Canlı engelleme oranı bundan çok saparsa bot Telegram'dan uyarır (dağılım kayması).

## 8. Sonraki adımlar (öncelik sırasıyla)
1. `SINYAL_KAPALI_MUM = True` denemesi (repaint + train/serve paritesi).
2. Risk döngüsü için ayrı ccxt örneği → WebSocket fiyat akışı.
3. Borsa tarafı felaket stopu (§5'teki notla), systemd `WatchdogSec`.
4. Canlı `OB_Oran_Yakin` / `Spread_Bps` / `Hacim_Hizi` ≥300 örneğe ulaşınca feature listesine ekleme ve karşılaştırma.
5. Aylık backfill yenilemesi ve `ETIKET_SURUMU` değişince otomatik yeniden etiketleme.

## 9. Bağımsız review
Diff, gerçek parayla çalıştığı için ayrı bir ajan tarafından sıfırdan incelendi. Bulguların hepsi kod okumasıyla doğrulandı, düzeltildi, her birine regresyon testi yazıldı ve mutasyon testiyle kontrol edildi:

| Bulgu | Önem | Düzeltme |
|---|---|---|
| Binance "execution status unknown" (`-1006`) ccxt'de `OperationFailed`, yani `NetworkError`'ın *üst* sınıfı; ilk sürüm bunu kesin hata sayıyordu → dolmuş alım kaydedilmez, aynı coin tekrar alınırdı | YÜKSEK | Kesin ret listesi dışındaki her hata sorgulanır (§5 #4) |
| `MANUEL_COINLER` satışta kontrol edilmiyordu (V18.3'ün eskiden sahiplendiği pozisyon yine satılırdı) | YÜKSEK | Pozisyon takipten çıkarılır + emir katmanında satış reddi |
| Farklı şemalı core dosyaları birleşince NaN `Pozisyon_Id` tüm eski pozisyonları tek anahtara çökertiyordu | ORTA | NaN→None temizliği |
| Etiket penceresi sinyal öncesi fiyatı içeriyordu (sahte SL) | ORTA | Sinyal dakikası sadece kapanışla |
| Backfill ağırlıklı eşik canlıda farklı oranda engelleyebilir | ORTA | Canlı-kantil eşiği + canlı engelleme oranı izleyicisi |
| 0 dolumlu (EXPIRED) market emri dolmuş sayılıyordu (V18.3'te de vardı) | DÜŞÜK | Başarısız sayılır |
| `csv_onar --birlestir` bot çalışırken satır kaybedebilirdi | DÜŞÜK | İyimser eşzamanlılık kontrolü, "botu durdurun" notu |
| Ekonomik testte örnekler bağımsız sayılıyordu | DÜŞÜK | Episodun ilk sinyali |

Review ayrıca şunları doğruladı: `risk_motoru` ile V18.3 arasında belgelenmemiş davranış farkı yok; VIP döngüsü tek bir pozisyon yüzünden ölemez; backfill'de look-ahead yok; CSV katmanı veri yok etmiyor; kod Python 3.10–3.12, pandas 2.2–3.0 ile çalışıyor.

## 10. Bütçe simülasyonu: gerçek işlem geçmişinin yeniden oynatılması
`tools/gecmis_simulasyon.py` ile 31 Mayıs – 25 Eylül 2026 arasındaki **gerçek** işlemler (gerçek dolum fiyatları ve kayma dahil) bütçe kısıtıyla yeniden oynatıldı. Getiriler komisyon dahil fiyatlardan yeniden hesaplandı. Başka bottan karışan ve sahiplenilmiş satırlar hariç tutuldu.

**Özet:** 538 pozisyon, işlem başına ortalama net getiri **+%0.05**, kazanma oranı **%40**.

| Çıkış tipi | Pozisyon | Toplam (20 USDT kasa) | Ortalama |
|---|---|---|---|
| 📈 Trend takipli çıkış | 154 | **+130.2 USDT** | +%4.15 |
| 🛑 Stop loss | 163 | **−107.1 USDT** | −%3.22 |
| ⏳ Zaman aşımı | 132 | −18.0 USDT | −%0.67 |
| 🛡️ Başa baş koruması | 84 | −1.7 USDT | −%0.10 |

**Sabit kasa (botun ayarı: 20 USDT, balina 40), aylık net kâr:**

| Ay | 100 USDT bütçe | 450 USDT bütçe |
|---|---|---|
| Mayıs (son gün) | −3.1 | −3.1 |
| Haziran | −14.9 | −9.9 |
| Temmuz | −5.8 | −8.4 |
| Ağustos | **+19.8** | **+22.2** |
| Eylül | +4.9 | +4.9 |
| **Toplam (4 ay)** | **+0.96 USDT (%+1.0)** | **+5.76 USDT (%+1.3)** |
| En büyük düşüş | −%26.4 (~−26 USDT) | −%5.2 (~−23 USDT) |
| Bakiye yetmediği için atlanan | 35 işlem | 0 |

**Rastgele bir 30 günlük dönem (89 pencere):**
| | 100 USDT | 450 USDT |
|---|---|---|
| Medyan | +3.6 USDT (%+3.6) | +4.2 USDT (%+0.9) |
| En kötü %10 | −13.3 USDT (−%13) | −13.3 USDT (−%3.0) |
| En iyi %10 | +25.0 USDT (%+25) | +26.0 USDT (%+5.8) |
| Kârlı pencere oranı | %54 | %54 |

**Oransal kasa** (işlem başına özsermayenin %20'si, balinada %40): 4 ayda iki bütçe için de **%+0.8**, en büyük düşüş **−%22.7**. Aylık tutarlar bütçeyle doğrusal ölçeklenir; 450 USDT'de haziran −56, ağustos +79 USDT, 30 günlük medyan +18 USDT (en kötü %10: −54, en iyi %10: +102).

**Yorum:**
- Komisyon dahil strateji 4 ayda **başa baş**. Kârın tamamı trend ayı ağustostan geliyor; haziran ve temmuz zararda. Bir aylık sonuç yazı-tura: 30 günlük pencerelerin %54'ü kârlı.
- **Sabit kasa ile bütçe kârı büyütmez:** 450 USDT'nin zaman ağırlıklı ortalama ~10 USDT'si kullanılıyor, zamanın %72'sinde hiç açık pozisyon yok. 100 USDT'de ise bot bakiye kontrolündeki %1 pay yüzünden aynı anda en fazla 4 pozisyon açabildi ve 35 işlemi atladı.
- V18.3'ün ATR<%3 giriş filtresi geçmişe uygulansaydı sonuç +5.76 → −0.97 USDT olurdu. Elenen 57 yüksek ATR'li işlem net kârlıydı; örnek küçük, kesin sonuç değil.
- **Sınırlar:** işlemler bot sürüm sürüm değişirken yapıldı (V16 → V18.3). Rejim filtresi (17 Eylül'den beri) ve AI bloklamasının geçmiş etkisi bu veriyle ölçülemez; eski modeller bu verinin üzerinde eğitildiği için onları geçmişe uygulamak iyimser olur. V18.4 mantığının geçmiş fiyat verisiyle backtest'i için backfill sinyalleri + `sniper/risk_motoru` kullanılarak sunucuda bir backtest yazılabilir. Geçmiş performans geleceği garanti etmez.

### 10.1 5 / 7 Ağustos başlangıçlı yeniden oynatma
**Bu sonuçlar V18.3 ya da V18.4'ün değil, o tarihlerde çalışan sürümün gerçekleşen sonucudur.** 5 Ağustos – 6 Eylül arası işlemler kullanıcının Ağustos commit'indeki **V18.0.2** ile yapıldı (`veri/eski_surum/`). Gerekçe: CSV kayıtları bu kodun çıkış mantığıyla birebir uyuşuyor; kısmi kâr satışlarının neredeyse hepsi net +%2.5 üstünde, bu sabit %4 kademeyle tutarlı. Tek fark: 3 Ağustos'tan itibaren `Kar_Orani` komisyon dahil yazılıyor, `Net_Kar_USDT` ise brüt kalmış. CSV'deki çıkış tipleri sürüm sınırlarını gösteriyor: "🛡️ BAŞA BAŞ KORUMASI" (kâr kilidi +%0.2) son kez 20 Eylül'de, V18.3'ün "🔒 KÂR KİLİDİ (+%1)" ve "💤 MOMENTUM ÖLDÜ" çıkışları ilk kez 21–22 Eylül'de görülüyor.

| Dönem | Çalışan mantık | Pozisyon | İşlem günü | Ort. net | Kazanma | 20 USDT ile toplam |
|---|---|---|---|---|---|---|
| 5–15 Ağu | **V18.0.2** (kullanıcının Ağustos commit'i): ilk kâr kademesi sabit %4, kilit +%0.2, ATR<%4, rejim filtresi yok, core AI ≥ 0.65 zorunlu | 47 | 9 | +%0.70 | %49 | +6.60 |
| 16–31 Ağu | aynı | 145 | 15 | +%0.53 | %51 | +15.27 |
| 1–15 Eyl (7–15 Eylül'de işlem yok) | aynı | 47 | 5 | +%1.11 | %64 | +10.41 |
| 16–20 Eyl | V18.x: yeni CSV, 17 Eylül'den rejim filtresi, ATR'ye bağlı ilk kademe (%3–6), AI gölge mod; kilit hâlâ +%0.2 | 40 | 5 | −%0.24 | %50 | −1.89 |
| 21–25 Eyl | V18.3 (kilit +%1, momentum çıkışı, ATR<%3) | 33 | 4 | −%0.47 | %39 | −3.12 |

**Bütçe kısıtlı sonuç** (sabit kasa 20/40 USDT; oransal = işlem başına özsermayenin %20'si):

| Başlangıç → bitiş | Pozisyon | 100 USDT sabit | 450 USDT sabit | Oransal (her iki bütçe) | En büyük düşüş |
|---|---|---|---|---|---|
| 5 Ağu → 4 Eyl (30 gün) | 205 | +26.6 USDT (%+26.6) | +26.2 USDT (%+5.8) | %+27.0 (100→+27.1, 450→+121.7) | −%6.0 / −%1.6 / −%6.9 |
| 7 Ağu → 6 Eyl (30 gün) | 217 | +32.7 USDT (%+32.6) | +32.3 USDT (%+7.2) | %+34.9 (100→+34.9, 450→+157.2) | −%6.1 / −%1.6 / −%6.9 |
| 5 Ağu → 25 Eyl (tümü) | 312 | +27.6 USDT (%+27.6) | +27.2 USDT (%+6.0) | %+28.1 (100→+28.1, 450→+126.3) | −%6.0 / −%1.6 / −%6.9 |

**Başlangıç gününe duyarlılık** (100 USDT sabit, 30 günlük giriş penceresi): 1 Ağu +21.9 · 5 Ağu +26.6 · 7 Ağu +32.7 · 10 Ağu +23.9 · 15 Ağu +23.0 · 20 Ağu +17.3 · 24 Ağu +8.3 · 26 Ağu +7.6 USDT. Pencere kaydıkça 16–25 Eylül zararı ve 7–15 Eylül boşluğu içeri girdiği için sonuç düşüyor.

**Yorum:**
- Ağustos, 4 aylık geçmişin tek güçlü ayı (Haziran–Temmuz zararda, §10). 5/7 Ağustos başlangıcı sonucu gördükten sonra seçildiği için bu rakamlar **en iyi durum senaryosudur**, beklenti değil.
- Sabit kasada bütçe kârı büyütmez: 100 ve 450 USDT neredeyse aynı dolar kârını üretir; 450 USDT'de paranın çoğu boşta bekler. Oransal kasada getiri yüzdesi aynı kalır, dolar kârı bütçeyle ölçeklenir, düşüş de.
- 16–25 Eylül (yeni sürümler) zararda. Bu dönemin kısa (73 pozisyon, 9 gün) olduğunu ve piyasa koşullarının da değiştiğini unutmayın: giriş feature'ları bu dönemde belirgin farklı (hacim oranı ort. 2.85 vs ~3.2, ATR 1.68 vs ~1.4).

### 10.2 V18.4 filtreleri gerçek girişlere uygulansaydı (fiyat verisi gerektirmeyenler)
| Filtre | 5 Ağu – 15 Eyl (Ağustos sürümü) | 16–25 Eyl |
|---|---|---|
| ATR > %3 girişleri (V18.3+ bunlara girmez) | 25 pozisyon, **ort. +%1.74** (diğerleri +%0.55) | 5 pozisyon, ort. −%2.96 (3'ü −%5 stop) |
| Core model vetosu (skor < 0.65) | **0 / 239 engellenirdi** | 48 / 73: geçen +%0.40, engellenen −%0.73 |
| Filtre modeli (skor > 0.45) | 39 engellenirdi (−%1.83) — **ezber, geçersiz** | 17 engellenirdi (−%0.37), geçen −%0.33: fark yok |

- **ATR<%3 filtresi** 5 Ağu → 4 Eyl penceresinde 100 USDT sabit kârı +26.6'dan **+17.9 USDT**'ye, oransal 450 USDT kârı +121.7'den **+74.7 USDT**'ye düşürürdü. Yüksek ATR'li 30 girişin ortalaması (+%0.96) diğerlerinden (+%0.38) iyi; fark anlamlı değil (permütasyon p=0.39). V18.3'teki bu değişiklik 18–20 Eylül'deki üç −%5 stoptan sonra yapılmıştı; daha uzun geçmiş onu desteklemiyor. Karar sizin: ATR'yi %4'e geri almak (`MAX_ATR_PCT = 0.04`) daha fazla ve daha oynak işlem demek.
- **Core model:** Ağustos girişlerinin hiçbirini engellemezdi, çünkü Ağustos botu (V18.0.2) **aynı modelle aynı eşikte zaten bloklama yapıyordu**. V18.0.2 kodu `AI_MIN_OLASILIK = 0.65` altındaki sinyali açmıyor. Bu yüzden Ağustos işlemlerinin hepsi eşiğin üstünde; seçilim etkisi var, bu "modelin Ağustos'ta işe yaramadığı" anlamına gelmiyor. 16 Haziran – 4 Ağustos işlemlerinin %8'i, 16–25 Eylül'ün %66'sı eşiğin altında. 16–25 Eylül'de V18.x gölge modda olduğu için filtre kapalıydı. V18.4'ün mevcut ayarı (`AI_GOLGE_MOD = False`) Ağustos davranışını geri getirir. 16–25 Eylül içinde (zaman karışıklığı olmadan) engellediği işlemler daha kötü: tek yönlü permütasyon p=0.05, RSI dilimleri içinde p=0.12. Model büyük ölçüde "düşük RSI + düşük hacim oranı" girişlerini eliyor. Adli bulgu: modelin `base_score=0.45588` = 31/68 ve bu oranı veren tek kronolojik önek ilk 68 satır; model o dönemde AUC **0.99** ile ezber yaparken sonraki her dönemde AUC ≈ 0.5 (16 Haz–31 Tem 0.54, Ağustos 0.50, 1–15 Eyl 0.48, 16–25 Eyl 0.55). Yani **ilk 68 işlemle (31 Mayıs – 15 Haziran) eğitilmiş**; Ağustos ve Eylül onun için gerçekten örneklem dışı. Kanıt zayıf ama tutarlı; §2.3'teki öneri (bloklama açık, filtre modeli kaldırılsın) değişmiyor.
- **Gece eğitimi zaman çizelgesi** (`ai_trainer_history.log`):
  - 6 Eylül 14:55 – 16 Eylül: eski trainer her gece filtre modelini tüm geçmişle yeniden eğitti ("191→196 zararlı işlem ezberlendi").
  - 17–20 Eylül: V2 trainer hiçbir modeli kaydetmedi (AUC < 0.55).
  - 21 Eylül'den itibaren: veri kaybı yüzünden "yetersiz veri".
  - 7–15 Eylül'de hiç işlem yok. Nedeni ezberleyen filtre mi, piyasa mı (BTC'nin EMA200 altında kalması) bu veriden ayrıştırılamıyor; simülasyonun backfill senaryosu o günlerdeki sinyalleri gösterir.
- **Filtre modelinin** Ağustos'taki etkileyici ayrımı ezberdir: 15–16 Eylül'de Ağustos'u da içeren 603 satırla eğitildi (`base_score` = 196/603). Eğitim dönemindeki AUC'si 0.75–0.91, eğitimden sonraki 16–25 Eylül'de 0.50. Simülasyonlarda kullanılmadı.
- **Ölçülemeyenler:** rejim filtresi (BTC 15m ADX<20 iken giriş yok), BTC EMA200 histerezisi ve V18.4 çıkış mantığı (kâr kilidi +%1, momentum çıkışı, yarım satış sonrası taban düzeltmesi). Bunlar için fiyat verisi gerekiyor: §10.3.

### 10.3 V18.4 simülatörü (`tools/v184_simulasyon.py`)
Binance'ten 1 dakikalık fiyat geçmişini indirir ve V18.4'ün kararlarını canlı botun kullandığı kodun aynısıyla (`sniper/risk_motoru.py`) yeniden üretir.

| Senaryo | Ne |
|---|---|
| `GERCEK` | o günkü sürümün gerçekleşen sonucu |
| `ESKI_SIM` | aynı girişler, Ağustos botunun (**V18.0.2**) çıkış mantığıyla simülasyon: sabit %4 ilk kademe, +%0.2 kilit, momentum çıkışı yok, 25 mumluk RSI. 16 Eylül öncesinde `GERCEK` ile farkı **simülatörün hata payıdır** |
| `V1802` | Ağustos botu çalışmaya devam etseydi: V18.0.2 çıkışları + V18.0.2 giriş filtreleri (ATR ≤ %4, BTC > EMA200 histerezissiz, core AI ≥ 0.65) |
| `V184_CIKIS` | aynı girişler, V18.4 çıkış mantığı |
| `V184` | + V18.4 giriş filtreleri: BTC onayı, TREND rejimi, ATR ≤ %3 |
| `V184_AI` | + core model vetosu |
| `B_...` (backfill) | sinyaller geçmiş mumlardan yeniden üretilir (kapalı mum modu, radar: 12M+ hacimli en çok yükselen 10 parite). Ağustos botu ile V18.4 aynı sinyal kuralını ve radarı kullanır, yalnız ATR sınırı farklı; sinyaller ATR ≤ %4 ve BTC durumundan bağımsız üretilir, bütün senaryolar **aynı havuzu** süzer. "Bot baştan bu ayarla çalışsaydı" sorusunun yaklaşık cevabı |
| `B_V184` / `B_REJIMSIZ` | V18.4 (BTC histerezisi, TREND, ATR ≤ %3, V18.4 çıkışı) / yatay rejim filtresi olmadan |
| `B_V184_AI` | `B_V184` + core model vetosu: canlıdaki kurulum, diğerlerinin karşılaştırıldığı taban |
| `B_AI_ATR4` / `B_AI_REJIMSIZ` | `B_V184_AI`, ATR sınırı %4 / yatay rejim filtresi olmadan |
| `B_V1802` | Ağustos botu: BTC > EMA200 histerezissiz + ani çöküş, ATR ≤ %4, eski core model vetosu, V18.0.2 çıkışı |

Her senaryo 100/450 USDT ve sabit/oransal kasa için botun kurallarıyla oynatılır: aynı coinde tek pozisyon, tam çıkıştan sonra 1 saat bekleme, üst üste 2 stopta 24 saat kara liste, bakiye kontrolü. Kısmi satışın parası satış anında kasaya döner.

**Modelleme:** 1m mum yeşilse açılış→dip→tepe→kapanış, kırmızıysa açılış→tepe→dip→kapanış yolu izlenir. Stop, kısmi kâr ve trailing eşikleri tam kesişim fiyatında tetiklenir; dolum bunun %0.15 altından (`--kayma-seviye`, gerçek stoplarda ölçülen ortalama taşma ~%0.17). Zaman aşımı, momentum ve RSI çıkışları o anki fiyattan %0.05 kaymayla. Eski sürüm 4 saatlik süre uzatmasında giriş zamanını sıfırladığı için gerçek giriş anı, dolum fiyatının 1m mum aralığına düştüğü an aranarak bulunur. Yarım satıştan sonra canlıdaki gibi 2 sn sonra aynı fiyattan yeniden değerlendirilir (kalan yarı eşiğin altındaysa hemen satılır). Giriş zamanı doğrulanamayan pozisyonlar hiçbir senaryoya alınmaz (tüm senaryolar aynı küme). AI senaryosu yalnız modelin eğitim verisinden sonra açılan girişlere uygulanır (kartlı modelde dönem karttan okunur) ve modelin feature'ları o kaynakta üretilemiyorsa (ör. Ağustos kayıtlarında V2 feature'ları yok) atlanır.

**V18.0.2 sadakati:** kullanıcının Ağustos commit'indeki karar kodu teste satır satır aktarıldı. `ESKI_SIM` ayarlarıyla çalışan motor 20.000 rastgele durumun hepsinde aynı kararı veriyor (`test_eski_ayarlar_v1802_ile_ayni_karari_verir`).

Testler (`tests/test_v184_simulasyon.py`, 33 test): her çıkış tipi için elle kurulmuş senaryolar, 80 rastgele fiyat yolunda motorun 200 kat yoğun yolla aynı kararı verdiği diferansiyel test, RSI önbelleğinin sonucu değiştirmediği test, bütçe kuralları, önbellek, AI kısıtları ve sahte borsayla uçtan uca çalıştırma; pandas 2.3 ve 3.0'da geçiyor.

**Bağımsız inceleme:** engelleyici hata bulunmadı. Motor, aynı fiyat yolunda 2 saniyelik canlı tarama temposunu taklit eden bir referansla (RSI moon bag, yatay rejim momentumu ve 4 saat zaman aşımı açık) karşılaştırıldı: işlem başına ortalama fark +%0.01–0.03, 1000 yolda çıkış tipleri ~%100 aynı. Eski sürümün süre uzatmalı geçmişi sahte olarak üretilip araca verildiğinde 53/53 giriş zamanı bulundu, Ağustos-sim ile "gerçek" arasındaki fark ort. %0.03. İncelemenin bulduğu düzeltilmiş noktalar: saat dilimi tahmini yanlış eşleşme üretebiliyordu (kaldırıldı; `--saat-farki` ile tüm kayıtlara uygulanır), doğrulanamayan girişler senaryolara giriyordu, çıkış etkisinin güven aralığı simülatör hatasını içeriyordu (artık V18.4 − Ağustos-sim eşleştirilmiş farkı), 30 günlük pencereler bitişten sonraki boş günleri sayıyordu, yeni trainer modeli gelince AI senaryosu eksik feature'la skorlanabilirdi, hiç verisi olmayan girdi setinde çöküyordu.

**Bilinen sınırlar:** BTC rejimi son kapanmış 15m mumla hesaplanır (canlı bot açık mumu da kullanır). Backfill kapalı mum modunda çalışır ve bugün listede olan paritelerle sınırlıdır (delist olanlar yok). Filtre modeli kullanılmaz (ezber).

**Durum:** 26 Eylül'de sunucuda çalıştırıldı; sonuçlar §10.4'te. Bu çalışma ortamından `api.binance.com` erişimi kapalı (proxy 403). Bu yüzden simülasyon ya sunucuda çalıştırılmalı ya da ortamın ağ ayarlarında Binance'e izin verilmeli. Sunucuda (bot çalışırken de olur, API anahtarı gerekmez):
```bash
cd /root && /root/venv/bin/python tools/v184_simulasyon.py --baslangic 2026-08-05 --limit 20   # ~1 dk deneme
cd /root && /root/venv/bin/python tools/v184_simulasyon.py --baslangic 2026-08-05              # tam: ~10-20 dk
/root/venv/bin/python tools/v184_simulasyon.py --baslangic 2026-08-07 --bitis 2026-09-06 --kaynak gercek   # önbellekten, hızlı
```
Çıktı `v184_sim_rapor.txt`: (1) simülatör doğrulaması, (2) filtrelerin gerçek girişlerdeki etkisi, (3) çıkış mantığının etkisi (dönem bazında, %95 güven aralığıyla), (4) her senaryo için 100/450 USDT sonuçları, (5) backfill senaryolarının sinyal başı sonucu ve canlı kuruluma (`B_V184_AI`) göre farkı; ATR %3–4 ve yatay rejim sinyallerinin kendi ortalaması (gün bazlı bootstrap güven aralığıyla). Yalnız backfill, eski raporu ezmeden: `--kaynak backfill --cikti /root/v184_sim_b`. İndirilen veri `sim_onbellek/` klasöründe kalır.
30 Eylül: (6) ay ay sonuç eklendi: canlı kurulumun her bütçe için aylık kârı ve ay sonu bakiyesi, altında tüm senaryoların aylık kârı. Uzun dönem için bellek düşürüldü (mumlar DataFrame'e alınınca depodan bırakılır, 1m veri bellekte birikmez); sonuçlar değişmedi (aynı sentetik veride çıktılar birebir aynı). 250 sembol × 9 ay: sentetik borsada ~4 dk hesap, ~10.500 API isteği, ~0.8 GB ek bellek (kütüphanelerle ~1 GB), ~0.5 GB önbellek. Komut: `--baslangic 2026-01-01 --kaynak backfill --mod sabit --cikti /root/v184_sim_9ay` (KURULUM.md).

### 10.4 Sunucu sonuçları (26 Eylül: 5 Ağustos – 26 Eylül, 312 gerçek pozisyon + 250 coinlik backfill)
Rapor ve log: `veri/sim_2026-09-26/`. Kullanılan AI: canlıdaki eski core model (kartsız, 0.65 eşik; ilk 68 işlemle, 31 May–15 Haz eğitildi, yani bu dönem için örneklem dışı).

- **Simülatör doğrulaması:** V18.0.2 dönemindeki 239 girişte simülasyon ile gerçekleşen arasında korelasyon 0.94, son çıkış tipi uyumu %95. Ortalama: simülasyon %+0.61, gerçek %+0.68 (simülatör hafif temkinli). Stop'larda sapma %0.00.
- **Eski core modelin vetosu bu dönemde işe yarıyor.**
  - Gerçek girişlerde vetolanacak 48 işlemin gerçekleşen ortalaması %−0.73, geçenlerin %+0.65.
  - Backfill'de (100 USDT, sabit kasa): V18.4 AI'sız +2.01 USDT (maxDD %−10.3), AI'lı +11.06 USDT (%−5.5).
  - İki ayrı evrende aynı yön. §2.2'deki sınırda ayrım bulgusu (73 pozisyon) bu genişlikte veriyle güçlendi; canlı bloklama modunun korunmasını destekliyor.
- **Çıkış mantığı:** V18.4 − Ağustos-sim farkı işlem başına −0.03 puan (%95 GA −0.17 … +0.12).
  - 5 Ağu – 15 Eyl: −0.05 (anlamsız).
  - 21 Eylül sonrası: +0.20 (GA +0.03 … +0.39).
  - V18.4 çıkışları kötü dönemde daha az kaybettiriyor, iyi dönemde çok az geride kalıyor.
- **Giriş filtreleri** (gerçek girişlerde, elenenlerin gerçekleşen ortalaması):
  - yatay rejim: 112 işlem eleniyor, elenen %+0.19 / geçen %+0.58;
  - ATR > %3: 30 işlem, elenen %+0.96 / geçen %+0.38 (n küçük);
  - BTC onayı: 32 işlem, elenen %+0.22.

  V18.4 çıkışlarıyla birlikte bu filtreler işlem sayısını 312'den 154'e indiriyor. Toplam kâr neredeyse değişmiyor (+22.39 → +22.29 USDT), maxDD %−6.3'ten %−5.1'e iniyor.
- **Bütçe** (52 gün, sabit kasa 20/40 USDT; USDT ve maxDD 100 USDT bütçe için):

  | Senaryo | USDT | maxDD |
  |---|---|---|
  | GERCEK | +27.11 | %−6.1 |
  | V1802 | +33.30 | %−5.6 |
  | V184 | +22.29 | %−5.1 |
  | V184_AI (canlıdaki kurulum) | +26.40 | %−2.0 |

  450 USDT'de USDT kârı aynı çıkıyor, çünkü sabit kasayla 100 USDT de işlemlerin neredeyse hepsine yetiyor. Oransal kasada (%20) 450 USDT ile V184_AI +121.98 USDT kazanıyor (+%27.1, maxDD %−2.4).
- **V1802'nin önde olmasının yorumu:**
  - 5 Ağu – 15 Eyl arasında V18.4'ün yeni filtreleri (yatay rejim, ATR %3), V18.0.2'nin aldığı kârlı işlemlerin bir kısmını da eliyor.
  - 16 Eylül sonrasında V18.0.2'nin BTC kuralı, o dönemin kötü girişlerinin çoğunu engelliyor.
  - Ancak 16 Eylül sonrasındaki evren o günün botunun (yatay filtreli V18.x) aldığı işlemlerle sınırlı. V18.0.2'nin o dönemde alacağı yatay rejim sinyalleri listede yok.
  - Adil kıyas için backfill evreninde bir V18.0.2 senaryosu gerekiyor; henüz yok.
- **Beklenti:** gerçek evrenin sonuçları iyimser, çünkü o günün botunun AI'ı ve filtreleri girişleri zaten seçmişti. Backfill ise daha kötümser.
  - V184_AI +26.40 USDT, B_V184_AI +11.06 USDT; canlı beklenti muhtemelen ikisinin arasında.
  - Dönemin büyük kısmı iyi geçti. 21 Eylül sonrasında gerçek işlemlerin ortalaması işlem başına %−0.47.

### 10.5 Piyasa taraması: Ağustos botu ve filtre varyantları (26 Eylül, ikinci çalıştırma)
Rapor, log ve sinyal tablosu: `veri/sim_2026-09-26/v184_sim_b_*`.
- 250 coin, 5 Ağu – 26 Eyl, 1293 sinyal (ATR ≤ %4, BTC durumundan bağımsız); V18.4 kuralına uyan 693.
- İlk çalıştırmada da olan üç senaryonun sonucu birebir aynı çıktı.

| Senaryo (100 USDT, sabit kasa) | İşlem | USDT | maxDD | Ağustos | Eylül | Sinyal başı |
|---|---|---|---|---|---|---|
| B_V184 | 239 | +2.01 | %−10.3 | +10.34 | −8.33 | %+0.16 |
| B_REJIMSIZ | 387 | +3.37 | %−11.5 | +11.82 | −8.45 | %+0.08 |
| **B_V184_AI (canlıdaki kurulum)** | 147 | **+11.06** | **%−5.5** | +12.97 | −1.91 | %+0.20 |
| B_AI_ATR4 | 169 | +4.95 | %−9.8 | +12.13 | −7.18 | %+0.03 |
| B_AI_REJIMSIZ | 246 | +12.73 | %−7.4 | +15.71 | −2.98 | %+0.15 |
| B_V1802 (Ağustos botu) | 285 | +14.55 | %−9.3 | +19.32 | −4.77 | %+0.20 |

- **ATR %3–4 sinyalleri zarar ettiriyor.**
  - V18.4 çıkışıyla n=41, ort %−0.93 (%95 GA −2.01 … −0.12, gün bazlı bootstrap); Ağustos çıkışıyla %−0.44.
  - ATR sınırını %4'e açmak canlı kurulumun sonucunu +11.06'dan +4.95 USDT'ye düşürüyor, maxDD %−5.5'ten %−9.8'e çıkıyor.
  - ATR ≤ %3 korunmalı. Bu, V18.4'ün Ağustos'a göre tek net iyileştirmesi.
- **Yatay rejim sinyalleri başa baş.** V18.4 çıkışıyla n=163, ort %+0.07 (GA −0.43 … +0.51). Filtre sinyal başı getiriyi değiştirmiyor; işlem sayısını ve düşüşü azaltıyor.
- **Aynı sinyallerde çıkış kuralı karşılaştırması** (n=444, eşleştirilmiş):
  - Ağustos çıkışı ort %+0.18, V18.4 çıkışı %+0.05; fark +0.12 puan (GA −0.01 … +0.30).
  - Gerçek girişlerde de aynı yön (+0.03, anlamsız): V18.4'ün çıkış değişiklikleri sonucu iyileştirmedi.
  - Olası sebep yatay rejimdeki 1.5 saatlik momentum çıkışı. Yatay sinyaller Ağustos çıkışıyla %+0.31, V18.4 çıkışıyla %+0.07.
- **Ağustos botu sinyal başına canlı kurulumla aynı:** %+0.20'ye %+0.20, fark GA −0.46 … +0.49.
  - Kabaca iki kat fazla işlem yaptığı için iyi ayda daha çok kazanıyor, kötü ayda daha çok kaybediyor: Ağustos +19.3 / Eylül −4.8; canlı kurulum +13.0 / −1.9.
  - Ağustos botunun fazladan aldığı 242 sinyalin ortalaması %+0.15. Bunlardan ATR %3–4 olan 57'si %−0.24, yatay rejimdeki 168'i %+0.31.
- **AI vetosu sinyal başına zayıf, bütçe oynatmasında belirgin.**
  - Sinyal başına geçen %+0.20, engellenen %+0.12; kazanma oranı %48'e %39.
  - Bütçe oynatmasında AI'sız +2.01, AI'lı +11.06 USDT; Eylül'de −8.33'ten −1.91'e.
- **Karma ayar tahmini.** CSV'den tek bacaklı bütçe oynatmasıyla hesaplandı; gerçek senaryolarla karşılaştırıldığında sapma 0.1 ile 3.7 USDT arasında.
  - Canlı giriş filtreleri, yatay filtre olmadan, Ağustos çıkışıyla: ≈ +17.4 USDT (maxDD %−6.1, Eylül −0.3).
  - Ağustos botu, ATR %3 ile: ≈ +14.6.
  - Canlı giriş filtreleri, Ağustos çıkışıyla: ≈ +12.7.
  - En iyisi dokuz denemenin en iyisi olduğu için iyimser; tek bir 52 günlük dönemde bu farklar gürültü aralığında.
- **Karar (26 Eylül):** canlı kurulum değiştirilmedi. Aynı analiz 2–3 hafta sonra yeni dönemi de kapsayarak tekrarlanacak. Karma ayar (yatay filtre kaldırılır, Ağustos çıkış ayarları kullanılır, ATR ≤ %3 ve AI korunur) yeni dönemde de önde kalırsa uygulanacak.

### 10.6 Altı aylık etiketli sinyaller: zarar alımdan mı satıştan mı? (30 Eylül)
Veri: kullanıcının sunucusundaki `etiketli_sinyaller.csv`. Toplam 2.923 sinyal: 2.295 piyasa taraması (31 Mart – 25 Eylül, canlı radarın "hacim > 12M, 24 saatte en çok yükselen 10" seçimi), 543 gerçek işlem, 85 reddedilen sinyal. Etiket V18.4'ün bariyerleriyle hesaplanıyor: stop %2.5–5.5, hedef %3–6, %2.5'te +%1 kilidi, 4 saat; komisyon dahil. "Canlı ayar" filtresi: TREND, ATR ≤ %3, eski core modelin skoru ≥ 0.65. Skorlar aynı model dosyasıyla (md5 `9f70006a…`) yeniden hesaplandı.

- **Aylık sonuç** (canlı ayar, n=652, işlem başı %):

  | | Nis | May | Haz | Tem | Ağu | Eyl | Toplam |
  |---|---|---|---|---|---|---|---|
  | Etiket (tarama) | −0.17 | −0.17 | +0.02 | −0.25 | +0.17 | −0.07 | −0.08 |
  | Gerçek işlemler (botun kendi çıkışı) | – | – | −0.21* | −0.25* | +0.75 | +0.15 | |

  \* 4 Ağustos'a kadarki işlem kayıtları komisyonsuz (brüt) yazılmış; netleri ≈ −0.4. Kayıtlı toplam kâr (+21.1 USDT) Ağustos'tan geliyor (+29.5); diğer aylar toplamda −8.4.
- **Satış sebep değil:**
  - Stopla kapanan sinyallerde stoptan önce görülen en yüksek net kârın medyanı %+0.38. %+1.5'e çıkabilen %13, %+2'ye çıkabilen %2. Stopların %35'i ilk 30, %54'ü ilk 60 dakikada.
  - Tüm sinyallerin %44'ü alımdan sonra %+1'i hiç görmüyor.
  - Hedefe ulaşanların %39'u önce %−1'in, %20'si %−1.5'in altına iniyor. Stopu sıkılaştırmak kazananları da keser.
  - Gerçek çıkışlar (trailing, moon bag) aynı girişlerde sabit hedefli etiketten ay bazında 0.1–0.6 puan daha iyi.
- **Giriş özellikleri** (15 özellik; çeyrek eşikleri Nisan–Temmuz'dan alındı, Ağustos–Eylül'de sınandı):
  - Son 3–6 saatte çok yükselen, EMA'dan uzaklaşmış ve ATR'si yüksek sinyallerde stop oranı iki dönemde de artıyor: en düşük çeyrekte %1–10, en yüksek çeyrekte %40–47. Ancak hedefe ulaşma da artıyor; ortalama getiri çeyrekler arasında düz.
  - Hacim oranı ≥ 2.84 taramada iki dönemde de daha iyi (Ağu–Eyl farkı +1.02 puan, gün bazlı %95 GA +0.16 … +1.90). Gerçek işlemlerde tekrarlanmıyor (GA −0.65 … +0.54). Canlı ölçüm taramadakiyle aynı değil: gerçek işlemlerde medyan 2.66.
  - Botun kendi son 10/20/30 sinyalinin sonucuna göre işlem açıp açmamak (rejim anahtarı) sonucu iyileştirmiyor.
- **Sonuç:** Kayıp tek bir parametreden ya da çıkış mantığından gelmiyor. "Günün en çok yükselen 10 coinini kırılımda almak" girişinin 6 ayda kalıcı bir üstünlüğü görünmüyor; kazanç güçlü yükseliş dönemlerine (Ağustos) bağlı. Mevcut feature'larla iyi ve kötü sinyal ayrılamıyor; bu, trainer'ın AUC ≈ 0.5 bulgusuyla tutarlı.
- **Seçenekler:** (1) olduğu gibi devam; (2) işlem tutarını düşürüp veri toplamaya devam (yarım satışlar 5.1 USDT alt sınırına takılmasın diye en az ~12 USDT); (3) farklı giriş fikirlerini (ör. kırılım anında değil geri çekilmede almak) sunucudaki fiyat verisiyle, dönem ayrımlı test etmek.

### 10.7 Dokuz aylık piyasa taraması (30 Eylül: 1 Ocak – 30 Eylül 2026)
Rapor: `veri/arastirma_2026-09-30/v184_sim_9ay_rapor.txt`. İçerik:
- 250 coin, yalnız Binance fiyat verisi (işlem kayıtları kullanılmadı).
- Sabit kasa: işlem başına 20 USDT, balina 40.
- Stop kayması %0.15 varsayıldı; canlıda son günlerde ~%0.4 görüldü, yani gerçek sonuç daha kötü olurdu.

**Canlı kurulum (`B_V184_AI`), ay ay** (kâr USDT, 450 USDT bütçe):

| Oca | Şub | Mar | Nis | May | Haz | Tem | Ağu | Eyl | Toplam |
|---|---|---|---|---|---|---|---|---|---|
| −7.43 | −1.24 | −11.13 | −8.05 | −8.90 | −2.41 | −2.42 | +15.38 | −4.03 | **−30.23** |

- 450 USDT ile 685 işlem sonunda 419.77 USDT (%−6.7, en büyük düşüş %−10.0).
- 100 USDT ile 616 işlem sonunda 68.05 USDT (%−31.9, en büyük düşüş %−44.2).
- Dokuz ayın yalnız biri (Ağustos) artıda; kazanma oranı %43.

**Diğer senaryolar da zararda** (450 USDT):

| Senaryo | Sonuç (USDT) |
|---|---|
| AI'sız V18.4 | −99.70 |
| Yatay filtresiz | −145.20 |
| ATR %4 | −28.21 |
| AI + yatay filtresiz | −49.07 |
| Ağustos botu | −33.90 |

**Sinyal başına:**
- Canlı kurulum %−0.10 (GA %−0.39 … +0.20).
- AI'sız V18.4 %−0.25 (GA %−0.47 … −0.02), yani anlamlı biçimde eksi.
- AI vetosu zararı azaltıyor (fark +0.15 puan) ama kazandırmıyor.

§10.4–10.5'teki 52 günlük sonuçların artıda çıkması, o dönemin içinde Ağustos'un bulunmasındandı.

## 11. Alım yöntemi araştırması: literatür ve test planı (30 Eylül)
Soru: "son 24 saatte en çok yükseleni kırılımda al" girişi 6 ayda kalıcı bir üstünlük göstermedi (§10.6). Literatür bu konuda ne diyor, hangi alternatifler denenmeli?

**Hakemli çalışmalar:**
- **Kısa vadeli dönüş.** [Zaremba, Bilgin, Long, Mercik, Szczygielski (2021), *International Review of Financial Analysis* 78](https://ideas.repec.org/a/eee/finana/v78y2021ics1057521921002349.html). Veri: 3.600'den fazla coinin günlük fiyatı. Bulgular:
  - Bir gün önce en az getiren coinler, en çok getirenlerden belirgin biçimde daha iyi getiri sağlıyor.
  - Etkinin kaynağı coinlerin büyük çoğunluğunun likit olmaması.
  - En büyük ve en likit birkaç coinde tersine, günlük momentum var.
  - Botun radarındaki coinler (son 24 saatte en çok yükselen 10, çoğu orta ve küçük coin), bu çalışmanın düşük getiri beklediği grup.
- **Gün içi.** Kriptoda gün içi momentum ve dönüş birlikte var. Dönüş, yatırımcıların bilgi içermeyen haberlere aşırı tepkisiyle açıklanıyor ([*North American Journal of Economics and Finance* 62, 2022](https://ideas.repec.org/a/eee/ecofin/v62y2022ics1062940822000833.html)).
- **Anormal günden sonra.** BTC, ETH ve LTC'de anormal getirili bir günün ertesinde fiyat hareketleri büyüyor; aynı yönde işlem kârlı ([Caporale & Plastun (2020), *Financial Markets and Portfolio Management*](https://link.springer.com/article/10.1007/s11408-020-00357-1)). Bulgu büyük coinler için.
- **Zaman serisi momentumu ve ilgi.** BTC, ETH ve XRP getirileri kendi geçmiş getirileri ve yatırımcı ilgisiyle tahmin edilebiliyor ([Liu & Tsyvinski (2021), *Review of Financial Studies* 34(6)](https://www.nber.org/papers/w24877)).
- **Kesitsel faktörler.** Piyasa, büyüklük ve momentum (haftalık ufuk) faktörleri coinler arası getiri farklarını açıklıyor ([Liu, Tsyvinski & Wu (2022), *Journal of Finance* 77(2)](https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.13119)).
- **Teknik analiz.** Fiyatın hareketli ortalamasına oranı, BTC'nin günlük getirisini hem örneklem içinde hem dışında tahmin ediyor ([Detzel, Liu, Strauss, Zhou, Zhu (2021), *Financial Management*](https://onlinelibrary.wiley.com/doi/epdf/10.1111/fima.12310)).
- **Maliyet.** Her gün dengelenen kurallar günde portföyün yaklaşık 1–1.5 katını el değiştirir. İşlem başına %0.15 maliyetle bu günde %0.15–0.25 gider demek; brüt üstünlüğün bunu aşması gerekir. Momentum kârlarının kalıcılığı da tartışmalı ([*Financial Markets and Portfolio Management*, 2025](https://link.springer.com/article/10.1007/s11408-025-00474-9)).

**Test planı** (`tools/strateji_arastirma.py`, Binance günlük verisi, sunucuda çalışır):
- **Kurallar** (önceden seçildi, parametreleri sabit):
  - `BOT_VEKILI`: botun radarının günlük vekili.
  - `DONUS`, `DONUS_TREND`: dünün en çok düşenleri; trendli sürümde yalnız BTC 20 günlük ortalamasının üstündeyken.
  - `BUYUK_MOMENTUM`: en büyük 10 coinden dünün en çok yükselen 3'ü.
  - `HAFTALIK_MOM`: 7 günde bir, son 7 günün kazananları.
  - `TREND_SEPET`: en büyük coinlerden 20 günlük ortalamasının üstündekiler.
  - `TREND_DIP`: trenddeki coinlerden dünün en çok düşenleri.
  - Karşılaştırma: BTC al-tut ve haftalık dengelenen sepet.
- **Dönemler:** kuruluş 2023–2024, sınama 2025'ten bugüne. Ayrıca yıl yıl sonuç, parametre varyantları (yalnız bilgi amaçlı) ve komisyon duyarlılığı (%0.075 / %0.15 / %0.25) raporlanır.
- **Sınır:** evren bugünün paritelerinden oluşuyor, delist olan coinler yok. Düşenleri alan kuralların sonucu bu yüzden iyimser.
- **Doğrulama:** sentetik veride kuralların gelecek bilgisi kullanmadığı, portföy muhasebesi (kayma, devir, ücret) ve içine bilinen bir etki gömülmüş veride o etkinin bulunduğu test edildi (`tests/test_strateji_arastirma.py`).
- Sonuçlar sunucu çalıştırmasından sonra §11.1'e yazılacak.

### 11.1 Sonuçlar (30 Eylül, sunucu çalıştırması)
Rapor ve günlük getiriler: `veri/arastirma_2026-09-30/strateji_rapor.txt`, `strateji_gunluk.csv`. Kapsam: 145 parite (3 sabit coin çıkarıldı), 2 Ocak 2023 – 29 Eylül 2026, işlem başına %0.15 komisyon + kayma.

| Kural | 2023–24 toplam (maxDD) | 2025–26 toplam (maxDD) | 2025–26 Sharpe |
|---|---|---|---|
| BOT_VEKILI | %−36 (−74) | %−89 (−95) | −1.05 |
| DONUS | %−50 (−85) | %−96 (−97) | −1.80 |
| DONUS_TREND | %−49 (−79) | %−83 (−87) | −1.45 |
| BUYUK_MOMENTUM | %+41 (−81) | %−76 (−87) | −0.63 |
| HAFTALIK_MOM | %+226 (−66) | %−69 (−81) | −0.51 |
| TREND_SEPET | %+45 (−71) | %+7 (−66) | +0.44 |
| TREND_DIP | %+10 (−82) | %−94 (−96) | −1.43 |
| BTC_TUT | %+463 (−26) | %−11 (−53) | +0.07 |
| SEPET_TUT | %+343 (−44) | %−42 (−72) | −0.09 |

**Yorum:**
- **2023–24 yükseliş piyasası.** BTC'yi alıp tutmak (%+463) ve büyük coin sepeti (%+343) tüm aktif kuralları geride bıraktı. Haftalık momentum kuruluş döneminde %+226 yaptı, sınamada %−69'a döndü. Momentum kârları döneme bağlı; literatürdeki tartışmayla uyumlu.
- **Günlük dönüş sınamada tutmadı.** `DONUS` (dünün en çok düşenleri) likit Binance coinlerinde komisyon sonrası iki dönemde de zararda, sınamada GA −72 … −10 baz puan/gün. Evrende batan coinler yok ve bu, sonucu `DONUS` lehine çarpıtır; buna rağmen zararda.
- **Botun yaklaşımının günlük vekili** (`BOT_VEKILI`) sınamada günde −25 baz puan kaybettiriyor. Bu, §10.6–10.7'deki sonuçla ve canlı işlemlerle tutarlı.
- **2025–26'da artıda kalan tek aktif kural `TREND_SEPET`** (%+7). Ama güvenilir değil:
  - Düşüşü %−66.
  - Getirisi birkaç aya toplanmış (Eki 2025 +%46, Ağu–Eyl 2026 +%31/+%52).
  - Parametre varyantlarında sınama Sharpe'ı −0.39 … −0.03.
- **Keşif amaçlı ek kontrol** (önceden seçilmemişti; Detzel vd. 2021'in BTC'deki fiyat/ortalama bulgusu): BTC'yi yalnız N günlük ortalamasının üstündeyken tutmak, altındayken nakitte kalmak (geçişte %0.15 ücret). BTC al-tut karşılaştırması: 2023–24 %+463 (düşüş %−26), 2025–26 %−11 (düşüş %−53).

  | Ortalama | 2023–24 | 2025–26 | 2025–26 düşüş | Geçiş/yıl |
  |---|---|---|---|---|
  | MA20 | %+71 | %+0 | %−29 | 43 |
  | MA50 | %+184 | %+21 | %−26 | 21 |
  | MA100 | %+80 | %+15 | %−25 | 14 |
  | MA200 | %+134 | %−1 | %−32 | 9 |

  Hiçbir varyant iki dönemde de zarar etmiyor ve 2025–26'daki düşüş al-tutun yarısına iniyor. Getiri ise mütevazı: 90 USDT için 2025–26'da MA50'de ayda ~1 USDT. MA seçimi sonuca belirgin etki ediyor; sınama dönemi 21 ay.
- **Sonuç:** Test edilen kısa vadeli alım kurallarının hiçbiri, kurulduktan sonraki dönemde (2025–26) komisyon sonrası güvenilir biçimde kazandırmadı. Kanıtı olan tek yaklaşım yavaş bir trend filtresi. O da piyasada kalma kararını iyileştiriyor (düşüşü yarıya indiriyor), günlük kazanç üretmiyor.

### 11.2 Mevcut botu iyileştirme (30 Eylül)
**Veride denenen fikirler.** Kaynaklar: 6 aylık etiketli sinyaller, canlı ayara uyan 652 sinyal. İkiye bölündü: Nisan–Haziran ve Temmuz–Eylül.
- **Daha erken satmak** (aynı stopla sabit kâr hedefi, 4 saat): kazanma oranı %44'ten %59–67'ye çıkıyor, ama sinyal başı ortalama kötüleşiyor. Kaybedenler yine tam stop yiyor, kazançlar ise sınırlanıyor; kârı birkaç büyük kazanan taşıyor.

  | Çıkış | Sinyal başı | Kazanma |
  |---|---|---|
  | Mevcut etiket çıkışı | %−0.08 | %44 |
  | Hedef %0.6 | %−0.21 | %67 |
  | Hedef %1 | %−0.17 | %59 |
  | Hedef %1.5 | %−0.15 | %52 |
  | Hedef %2 | %−0.18 | %46 |

  Uygulanmadı.
- **Daha erken girmek** (son 3 saatte az yükselmiş ya da 15 dakikalık EMA'ya yakın sinyaller): tutarlı bir etki yok. Bir yarıda en iyi olan çeyrek, diğerinde en kötü. Uygulanmadı.
- **BTC günlük trend filtresi** (50 günlük ortalamanın altındayken alım yok):
  - Botun yaklaşımının günlük vekilinde 2025–26 zararını %−89'dan %−54'e indiriyor, ama artıya çevirmiyor. 2023–24'te faydası yok.
  - Sinyal verisinde tutarsız: Nisan–Haziran'da trendli günler daha kötü (%−0.15'e %−0.08), Temmuz–Eylül'de biraz daha iyi.
  - Kanıt yetersiz; uygulanmadı.

**Uygulanan iyileştirmeler** (strateji aynı; maliyet ve doğruluk):
- **Hızlı stop satışı.** Takip edilen adet biliniyorsa satıştan önce bakiye sorulmaz; bir istek turu kazanılır. Bakiye yetersizse (ör. komisyon coin'den kesildiyse) eski, bakiye sorgulu yola düşülür. Diğer hatalarda eskisi gibi bir sonraki turda tekrar denenir; belirsiz sonuçta aynı turda ikinci satış yapılmaz.
- **Kara liste sayacı gerçekten "peş peşe" sayar.** Stop dışı her çıkış sayacı sıfırlar; eskiden yalnız trend çıkışı sıfırlıyordu. Simülatörün bütçe oynatması da aynı kurala geçti.
- **Telegram fiyatları ~6 anlamlı basamakla yazılır.** Eskiden 4 ondalıkla yazıldığı için ucuz coinlerde giriş ve çıkış fiyatı aynı görünüyordu (`0.0007 ➔ 0.0007`).
- **Kartsız model sürümü dosya içeriğinden türetilir** (`legacy-<sha256[:8]>`). Aynı model iki sunucuda aynı adı taşır.
- **BTC bağlamı vektörel hesaplanır.** Pencere-başı EMA kapalı formla, ADX tüm seriden. Eski döngüyle kararlar birebir aynı (testte iki seri; EMA farkı ~1e-13, ADX ~1e-6); ~130 kat hızlı. 9 aylık simülasyonda sunucuda ~1 saat süren adım yarım dakikaya iner.
- **Simülatöre `--radar-saat` deneyi eklendi.** Kullanıcının "yükselişi erken yakala" fikri için: radar son N saatte en çok yükselenleri tarar (canlı bot 24). Sonuç §11.3'te: 4 saatlik radar daha kötü.

**Kullanıcı tarafı:** Binance'te komisyonu BNB ile ödemek ücreti %25 düşürür (işlem başına %0.1 → %0.075). 9 aylık simülasyonun 685 işleminde (20 USDT) bu ~6.9 USDT eder; −30.2 USDT'lik zararın yaklaşık dörtte biri.

### 11.3 "Yükselişi erken yakala" deneyi: 4 saatlik radar (30 Eylül, sunucu)
**Kurulum.** Sunucuda aynı kodla iki çalıştırma yapıldı: 1 Ocak – 29 Eylül girişleri (`--bitis 2026-09-30`), 250 coin. Tek fark radarın penceresi: canlı bot son 24 saatte en çok yükselen 10 pariteye bakar, deney son 4 saattekilere. Raporlar `veri/arastirma_2026-09-30/v184_sim_9ay_r24_rapor.txt` ve `..._r4_rapor.txt`. 24 saatlik çalıştırma önceki 9 aylık raporla neredeyse aynı (−30.10 / −30.23 USDT). Küçük fark, kara liste kuralı düzeltmesinden (§11.2) ve bitiş anından geliyor.

**Canlı kurulum (B_V184_AI):**

| | 24 saat (canlı) | 4 saat (deney) |
|---|---|---|
| Sinyal (bütçe kuralı olmadan) | 1032 | 1374 |
| Sinyal başı net | %−0.10 | %−0.19 |
| Kazanan | %44 | %42 |
| 450 USDT: işlem / kâr / en büyük düşüş | 684 / −30.10 / %−10.1 | 938 / −47.16 / %−13.2 |
| 100 USDT: işlem / kâr | 607 / −35.66 | 734 / −41.84 |

**Fark gerçek mi?** İki çalıştırma aynı günleri yaşadığı için gün bloklu bootstrap aynı günleri iki tarafa birlikte örnekler (4000 tekrar):
- Sinyal başı fark (4s − 24s): −0.09 puan, %95 GA [−0.22, +0.03].
- Günlük toplam (işlem başına 20 USDT): −0.19 USDT/gün, %95 GA [−0.34, −0.03]. 4 saatlik radar daha çok işlem açıyor ve her işlem ortalamada daha kötü; günlük zarar belirgin biçimde artıyor.
- Yarılar: 17 Mayıs'a kadar +0.06 puan [−0.08, +0.20], sonrasında −0.24 puan [−0.45, −0.04]. Hiçbir yarıda anlamlı iyileşme yok.

**Neden?** Aynı giriş aynı çıkışı verir (979 ortak sinyalde iki çalıştırmanın getirisi ve AI skoru birebir aynı). Fark, yalnız bir radarın bulduğu sinyallerden geliyor:

| Sinyal | n | Sinyal başı | Kazanan | Girişte 24s değişim (medyan) |
|---|---|---|---|---|
| İki radarda da | 979 | %−0.12 | %44 | %8.8 |
| Yalnız 4s radar ("erken") | 395 | %−0.36 | %37 | %3.5 |
| Yalnız 24s radar | 53 | %+0.33 | %42 | %4.9 |

Erken yakalanan coinlerin çoğunda yükseliş devam etmiyor. **Sonuç: canlı radar 24 saat kalır.**

**Zarar nereden geliyor?** 24 saatlik çalıştırmada canlı kuruluma giren 1032 sinyalin çıkış tipleri:

| Çıkış | Pay | Ort. net | Toplam (20 USDT) | Medyan süre |
|---|---|---|---|---|
| Stop | %36 | %−2.87 | −213.2 | 0.5 saat |
| Zaman aşımı (4 saatte < %0.5) | %21 | %−0.76 | −33.2 | 4.0 saat |
| Momentum öldü (yatay rejim) | %4 | %−0.15 | −1.2 | 2.5 saat |
| Kâr kilidi (+%1) | %13 | %+0.85 | +22.1 | 0.6 saat |
| Moon bag + takip | %7 | %+5.95 | +85.7 | 1.5 saat |
| Kısmi kâr + takip | %19 | %+3.00 | +119.5 | 1.9 saat |

Kaybettiren çıkışların toplamının %86'sı stoplardan geliyor; stopların yarısı alımdan sonraki 32 dakika içinde. Kârı ise az sayıda büyük kazanan taşıyor. Bu yüzden kârı erken almak (§11.2: sabit hedefler) sonucu kötüleştirdi.

**Giriş özellikleri** (girişteki 24s değişim, RSI, hacim oranı, ATR, AI skoru, balina, saat). Her biri kovalara ayrıldı; iki yarıda aynı yönde ve yeterli sayıda olan etki arandı:
- AI filtresi olmayan havuzda (2235 sinyal) skoru 0.65'in altındaki üç kova da iki yarıda eksi (sinyal başı %−0.22 ile %−0.45). Mevcut AI filtresi işe yarıyor; önceki raporla tutarlı (B_V184_AI − B_V184 = +0.15 puan [+0.02, +0.29]).
- Eşiği 0.90'a çıkarmak: sinyal başı +0.14 puan [−0.17, +0.46], günlük +0.14 USDT [−0.19, +0.41]. Anlamlı değil, işlem sayısı %70 düşüyor. Uygulanmadı.
- BTC günlük trend kapısı (dünkü kapanış 20/50/100 günlük ortalamanın üstünde): üç uzunlukta da yarılar arasında tutarsız. Uygulanmadı.
- 17–23 UTC girişleri iki yarıda da artıda (+%0.23 / +%0.17, n=240). Ancak ~30 kova denendi, biri tesadüfen tutarlı çıkabilir. Keşif bulgusu olarak kaldı, uygulanmadı.

**Sonraki deney: "daha erken sat", çıkış tarafı.** Simülatöre `--cikis-deneyi` eklendi. Canlı kurulumun AYNI sinyalleri başka çıkış ayarlarıyla da oynatılır; her deney bir `B_<AD>` senaryosudur. Raporun 5. bölümü canlı kuruluma göre farkı aynı günlerle verir. Aynı ayarla çalışan bir deney canlı kurulumla birebir aynı işlemleri üretir; bu testle doğrulandı. Önerilen varyantlar:
- `STOP20`: stop tabanı %2 (canlı %2.5).
- `STOP15`: stop tabanı %1.5. Gerekçe: stop olan işlemler neredeyse hiç yükselmiyor (§10.6: stop olanların medyan tepe kârı +%0.38).
- `ZAMAN2`: zaman aşımı 2 saat (canlı 4).
- `KISMI2`: ilk kısmi kâr eşiği %2–4 (canlı %3–6).
- `KILIT15`: kâr kilidi tetiği %1.5 (canlı %2.5).
- `ERKEN`: son üçü birlikte.

Karar kuralı önceden belli: bir varyant yalnız 9 ayın tamamında ve iki yarıda ayrı ayrı canlı kurulumdan iyiyse, günlük fark GA'sı sıfırı dışlıyorsa ve bütçe sonucunda da iyileşme varsa önerilir. Canlı ayar kullanıcı onayı olmadan değişmez.

### 11.4 "Daha erken sat" deneyi: çıkış ayarları (30 Eylül, sunucu)
**Kurulum.** Canlı kurulumun aynı 1032 sinyali altı farklı çıkış ayarıyla da oynatıldı (`--cikis-deneyi`). Tek çalıştırma, 1 Ocak – 29 Eylül. Taban (B_V184_AI) önceki çalıştırmayla birebir aynı çıktı (−30.10 / −35.66 USDT). Rapor: `veri/arastirma_2026-09-30/v184_sim_9ay_cikis_rapor.txt`. Sinyal başı fark eşleştirilmiştir: aynı sinyalin iki çıkışı arasındaki fark. Güven aralığı gün bloklu bootstrap ile hesaplandı.

| Varyant | Değişiklik | Sinyal başı fark, puan [%95 GA] | 1. yarı | 2. yarı | 450 USDT | 100 USDT | maxDD (450) |
|---|---|---|---|---|---|---|---|
| canlı | — | taban: %−0.10 | | | −30.10 | −35.66 | %−10.1 |
| STOP20 | stop tabanı %2 (canlı %2.5) | −0.01 [−0.06, +0.04] | −0.03 | +0.01 | −28.14 | −27.07 | %−9.3 |
| STOP15 | stop tabanı %1.5 | +0.00 [−0.06, +0.07] | +0.02 | −0.02 | −22.13 | −22.33 | %−8.2 |
| **ZAMAN2** | zaman aşımı 2 saat (canlı 4) | **+0.05 [−0.01, +0.10]** | +0.05 | +0.05 | **−15.36** | **−16.37** | %−7.7 |
| KISMI2 | ilk kısmi kâr eşiği %2–4 (canlı %3–6) | −0.01 [−0.04, +0.01] | +0.00 | −0.03 | −30.01 | −27.61 | %−9.9 |
| KILIT15 | kâr kilidi tetiği %1.5 (canlı %2.5) | −0.13 [−0.27, +0.01] | −0.09 | −0.17 | −35.24 | −40.06 | %−9.7 |
| ERKEN | son üçü birlikte | −0.08 [−0.23, +0.06] | −0.06 | −0.10 | −26.58 | −27.63 | %−8.4 |

**Bulgular:**
- **Kârı erken almak** (KILIT15, KISMI2, ERKEN) sonucu kötüleştiriyor ya da değiştirmiyor. Kazanma oranı %44'ten %53'e çıkıyor ama ortalama düşüyor. §11.2 ile tutarlı.
- **Daha sıkı stop** (STOP20, STOP15) sinyal başına etkisiz. STOP15'in bütçedeki iyileşmesi (−22 USDT) sinyallerin sonucundan gelmiyor; işlem sırası değişiyor (erken çıkan coin'e yeniden giriş, kara liste). Güvenilir değil.
- **Tutarlı çıkan tek varyant ZAMAN2:** pozisyon 2 saatte hâlâ %0.5 kârın altındaysa kapatılıyor (canlıda 4 saat bekleniyor). 286 işlem değişiyor:
  - 188 zaman aşımı daha erken ve daha az zararla kapanıyor (%−0.81 → %−0.58; +8.9 USDT),
  - 57 işlem stopa ulaşmadan kapanıyor (%−2.65 → %−1.26; +15.8 USDT),
  - karşılığında 22 kazanan erken kesiliyor (−13.3 USDT) ve 19 momentum çıkışı biraz kötüleşiyor (−1.2 USDT).
  - Net +10.2 USDT; sinyal başına +0.05 puan, günlük +0.06 USDT [−0.01, +0.13].
  - Sinyal başına 9 ayın 8'inde, bütçe oynatmasında 9 ayın 9'unda artıda. En büyük 5 kazanç çıkarılınca da +0.04 puan.
  - Mekanizma makul: kazananlar çoğunlukla ilk 2 saatte belli oluyor (medyan süre: kâr kilidi 0.6, moon bag 1.5, kısmi kâr 1.9 saat).
- **Ama kanıt sınırda.** GA sıfırı kıl payı içeriyor (P(fark > 0) = 0.96). Üstelik altı varyantın en iyisi seçildi. §11.3'teki karar kuralının "GA sıfırı dışlasın" şartı karşılanmıyor.

**Karar:** canlı ayar değişmedi. ZAMAN2 bağımsız bir dönemde (Nisan–Aralık 2025) doğrulanacak; komşu değerler (1.5 ve 3 saat) de denenecek. Önceden belirlenen kural: 2025'te de ZAMAN2'nin sinyal başı farkı artıda ve bütçe sonucu tabandan iyiyse `max_bekleme_saati` 4 → 2 önerilir (kullanıcı onayıyla). Bu değişiklik zararı azaltır, stratejiyi kârlı yapmaz: 9 ayda −30 yerine −15 USDT.
