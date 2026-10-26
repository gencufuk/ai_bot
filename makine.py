# -*- coding: utf-8 -*-
"""MAKİNE (faz 1, KÂĞIT MOD): bütün piyasayı her saat tarayan, gördüğü her şeyi veritabanına ("hafıza") yazan ve
strateji_lab'ın 8 saatlik kuralını YALNIZ KÂĞIT ÜZERİNDE işleten program. Gerçek emir yoktur.

Neden: strateji_lab (ANALIZ_V18.4.md §12-12.2) komisyon sonrası kalıcı kârlı bir kural bulamadı. Makine şimdiden
veri biriktirir (sonra veri madenciliği / AI için: ham mumlar, her saat her paritenin özellikleri, ileri etiketler,
emir defteri) ve kuralları canlı akışta kâğıt üzerinde izler. Gerçek işlem ancak bir kural kanıtlanırsa, ayrı bir
aşamada ve ayrı bir kararla düşünülür.

GÜVENLİK (mutlak): makine hiçbir koşulda emir vermez, bakiye sormaz, API anahtarı yüklemez. ccxt istemcisi anahtarsız
kurulur (borsa_kur) ve SaltOkunurBorsa ile sarılır: yalnız herkese açık okuma uçları (fetch_tickers, publicGetKlines,
fetch_order_book, fetch_time, load_markets/market) geçer, başka her öznitelik (alt çizgili adlar dahil)
PermissionError verir. .env dosyasından yalnız TELEGRAM_TOKEN ve TELEGRAM_CHAT_ID alınır (baş/son boşluk atılır);
Telegram belirteci günlükte ve hata mesajlarında maskelenir (kaçışlı ve URL kodlu biçimleri dahil).

Tur (her UTC saat başından ~20 sn sonra; t = yeni KAPANAN 1 saatlik mumun açılışı = karar mumu):
  1) kapalıyken geçen karar saatleri tarama tablosuna KACIRILDI yazılır (o saatlerde geriye dönük işlem açılmaz);
     borsa saati (fetch_time) yerel saatle karşılaştırılır (5 sn'den büyük farkta uyarı) ve diskteki boş alan
     denetlenir (1 GB altında uyarı)
  2) evren: fetch_tickers (turda bir kez) -> hafiza.evren_sec: USDT spot, ASCII sembol, yasaklı/kaldıraçlı değil, 24s
     hacmi >= --min-hacim, en hacimli --evren parite + her zaman BTC/USDT. Açık kâğıt işlemi olan (ve etiketi süren)
     pariteler evrenden çıksa da güncellenir. Ticker alınamazsa son başarılı evren kullanılır. Piyasalar (markets)
     her UTC günün ilk turunda ve ticker'larda yüklü piyasalarda olmayan yeni bir USDT kimliği (ccxt'nin eğiksiz
     'YENIUSDT' anahtarı: süreç başladıktan sonra listelenmiş parite) görülünce yeniden yüklenir; ikinci durumda
     ticker'lar yeniden çekilir (yeni listelemeler hemen evrene girer, 'active' bilgisi tazelenir).
  3) mumlar: hafiza.MumToplayici (ilk görülmede 1500 saat, sonra kapsamdan şimdiye; ağ hatasında yeniden deneme,
     olmazsa parite bu tur atlanır, tur ölmez; kapanmış mum kuralı ve istek sınırı hafiza belgesinde). Borsa son
     kapanan mumu henüz vermediyse bir kez kısa beklenip yeniden çekilir. BTC'nin karar mumu yoksa ya da evren
     paritelerinin yarısından çoğunda yoksa tur HATA sayılır (MUM_YOK uyarısı; yeni kâğıt işlem açılmaz; karar
     mumu zamanındaysa tur YENIDEN_DENE_MS sonra yeniden denenir).
  4) sabit coinler strateji_lab kuralıyla evrenden çıkar (sıra korunur: radarın eşitlik bozması sütun sırasına bağlı)
  5) pencereler: her parite için son PENCERE (1500) saatlik tam saatlik ızgara (eksik saat NaN). Veritabanından kurulur
     ve bellekte tutulur (PencereOnbellegi: her tur yalnız yeni mumlar okunur; satır sayısı tutmazsa baştan okunur)
  6) göstergeler, sinyaller, kombolar, radar: strateji_lab'ın KENDİ işlevleri (tek kod ilkesi: gostergeler,
     btc_baglami, sinyaller, kombo_bilesenleri, kesit_sutunu, cikis_simule; radar sırası
     makine_ozellikleri.radar_siralari, sıra <= 10 = radar_ilk10). Kural parametreleri lab'ın ön kayıtlı
     varsayılanlarıdır (min hacim 5M, büyük hacim 20M USDT); --min-hacim yalnız evren seçimi içindir.
  7) kâğıt işlemler (aşağıda)
  8) aday satırları (HER evren paritesi: 15 lab özelliği + 15 yeni mum/hacim özelliği + BTC rejimi, radar ve evren
     sırası, o mumda tetiklenen stratejiler; karar mumu olmayan parite için de satır yazılır: karar_mumu = 0,
     özellikler NULL, sinyaller ''), ilginç paritelerin emir defteri, ileri etiketler, tur kaydı; günde bir
     saklama temizliği; günde bir rapor (Telegram). evren_sira (ve evrenin kendisi) turun başındaki 24s ticker
     hacminden gelir: karar mumundan sonraki ~20 sn'nin (geç turda daha fazlasının) işlemlerini de içerir; geç tur
     satırları aday.olusturma_ms ile ayıklanabilir (olusturma_ms - ts - 1 saat > 15 dk).
  Tanım sürümü: makine açılışta özellik/etiket/kural tanımlarının özetini (elle artırılan OZELLIK_SURUMU, kaynak
  dosyaların sha256'sı, kural/evren parametreleri, --evren/--min-hacim/--defter-max) tanim tablosuna yazar; aday,
  etiket, kagit_islem ve tarama satırları tanim_id taşır. Tanım değişince günlüğe ve olay tablosuna (TANIM) yazılır.
  Tur adımları ayrı veritabanı işlemleridir ve yeniden çalıştırılabilir (upsert, tekil kâğıt sinyali, durumsuz
  çıkış): yarıda kalan ya da hatalı tur, karar mumu hâlâ zamanındaysa yeniden denenir.

Kâğıt işlem (strateji_lab.calistir ile BİREBİR; tests/test_makine.py aynı mumlarda işlem işlem karşılaştırır):
  Stratejiler: BOT_VEKILI, ERKEN_BIRIKIM, SIKISMA_KIRILIM, TREND_DIP_RSI2, YUKSEK_ISABET, YATAY_DONUS, REJIM_KOMBO,
  REJIM_KOMBO_BOT (faz 1'de AI varyantı ve BTC_TREND yok). Karar mumu t'de sinyal -> BEKLIYOR. t+1 mumu kapanıp
  saklanınca (sonraki tur) giriş = t+1 AÇILIŞI x (1 + SLIP_GIRIS) ile ACIK; t+1 mumu kalıcı olarak yoksa ATLANDI (lab
  da o sinyali atlar ve sonrakileri engellemez). Giriş mumu GIRIS_BEKLE_MS (48 saat) sonra hâlâ gelmediyse (parite
  delist / BadSymbol ya da işlemi durdurulmuş: borsa yeni mum vermiyor; paritenin çekimi o tur hatasızsa) da
  ATLANDI. ACIK işlemin çıkışı HER TUR girişten bugüne saklı mumlarla strateji_lab.cikis_simule (k=1) ile yeniden
  simüle edilir (durumsuz; henüz gelmemiş saat NaN olduğundan erken karar verilmez). İşlemin ızgarası işleme
  bağlıdır (girişten ISLEM_ONCESI_SAAT önce başlar, veritabanından): sinyal çıkışının SMA5/SMA20'si her turda aynı
  başlangıçtan hesaplanır, bu yüzden bir mumdaki çıkış kararı hangi turda (yeniden başlatma, kesinti sonrası)
  değerlendirildiğine bağlı değildir. Çıkış bulunursa KAPALI: cikis_ts = çıkış mumunun kapanışı (lab gibi), getiri
  k=1 net, sure_saat = çıkış - giriş mumu + 1. Bir paritede strateji başına tek pozisyon: yeni sinyal ancak önceki
  işlemin çıkış mumundan SONRAKİ karar mumunda (cikis_ts <= t). Kombolarda bu kural iki bileşen için birlikte
  geçerli; aynı mumda iki bileşen birden çıkarsa trend bileşeni alınır. Tur içi sıra: bekleyenler -> çıkışlar ->
  yeni sinyaller (lab'ın aday sırasıyla aynı sonuç).
  Yeni işlem yalnız (a) girişi makinenin İLK başlangıcının (meta.ilk_baslangic) saat başından sonra olan karar
  mumlarında ve (b) tur, karar mumunun kapanışından en çok ZAMANINDA_MS (15 dk) sonra başladıysa açılır: makine
  kapalıyken ya da geç kalınarak işlenen karar saatleri geriye dönük işlem açmaz (özellik ve etiket yine yazılır).
  Çıkışlar ise geri doldurulan mumlar dahil her zaman değerlendirilir. Yeni işlem açabilen ilk turun karar mumu
  meta.ilk_islem_ts'dir (ilk başlangıç saat başından 15 dk'dan sonraysa ilk tur geç kalır ve ilk_islem_ts sonraki
  saattir). Lab karşılığı: --baslangic = ilk_islem_ts + 1 saat (ilk işlemin giriş mumu), aynı mumlar, aynı evren;
  lab'daki veri sonu kuralı (azami tutma süresi veride kalmalı) makinede yoktur. Ondalık eşitlikte (kapanış SMA'ya
  matematiksel olarak eşit: fiyat adımlı veride seyrek) birebir lab eşliği mümkün değildir: pandas'ın kayan
  ortalaması son basamakta dizinin başladığı yere bağlıdır, lab'ınki de kendi dönem başına.
  Delist (BadSymbol, parite 'YOK') paritedeki açık işlem: azami tutma süresi dolduysa son saklı kapanıştan
  (SLIP_ZAMAN kaymalı) 'VERI_YOK' çıkış tipiyle kapatılır (gerçek çıkış fiyatı bilinmez; raporda ayrı görünür).
  İşlemi durdurulan (Binance BREAK: BadSymbol yok, yeni mum yok) paritede aynı kapanış azami süreden VERI_YOK_BEKLE_MS
  (24 saat) sonra yapılır (paritenin çekimi o tur hatasızsa).

Rapor (--rapor; döngü her gün --rapor-saati-utc'de bir kez Telegram'a gönderir; düz metin, 4000 karakterlik
parçalar): son 24 saat (turlar, hatalar, evren, strateji başına sinyal, açılan/kapanan kâğıt işlem ve USDT), başlangıçtan
beri strateji başına kâğıt sonuçları (işlem başına ISLEM_USDT, bileşik değil), son 30 günün dedektör karnesi (sinyal
verilen satırlarda 24 saatte +%10'a -%5'ten önce dokunma oranı ve tabana göre kat), veritabanı boyutu, son hata.
Uyarı: tur hatası, karar mumu yokluğu (MUM_YOK), istek sınırı, saat farkı, disk, döngü hatası (tür başına 6 saatte en
çok bir Telegram mesajı). Uyarı yolu veritabanından bağımsızdır (disk dolu / veritabanı yazılamıyor olsa da gider):
sınırlama zamanı bellekte tutulur (veritabanına yazmak en iyi çabadır) ve yalnız gönderim BAŞARILI olunca işlenir;
başarısız gönderim UYARI_YENIDEN_MS (15 dk) sonra yeniden denenir. Günlük rapor da gönderildiği günü bellekte tutar
(veritabanına yazılamasa da günde bir kez) ve veritabanı yazma hatası varsa raporun sonuna yazılır. Yazılamayan tur
kaydı bellekte tutulup veritabanı düzelince yazılır (KACIRILDI sayılmaz).

Bilinen sınırlar:
  - Pencere 1500 saattir: özyinelemeli göstergelerde (EMA200) pencere başının etkisi karar mumunda < 4e-7 göreli
    (1300. mumdan sonra < 1e-5; tests/test_makine.py). Kayan ortalamalar (SMA5/SMA20, bbw_pct'nin yüzdeliği) tam
    geçmişle son basamakta farklı olabilir; sinyal ve aday özelliği ancak değer eşiğe tam eşitse değişir.
  - Kapalıyken geçen karar saatlerinin aday satırları yazılmaz (tarama'da KACIRILDI): o saatlerin evreni ve ticker
    hacmi bilinmez; mumları geri doldurulur.
  - Lab'ın veri sonu kuralı makinede yoktur (makine her işlemi sonuna kadar izler); karşılaştırma bu yüzden azami
    tutma süresi veride kalan girişlerle yapılır.
  - Kâğıt dolum, lab gibi giriş mumunun açılışıdır (gerçekte tur ~20 sn sonra çalışır; kayma payı SLIP_GIRIS).
  - Emir defterinin geçmişi yoktur: makine şimdi toplar ki birkaç ay sonra sınanabilsin.

Kullanım (sunucuda; API anahtarı gerekmez, yalnız herkese açık piyasa verisi; bot çalışırken de olur):
  cd /root && venv/bin/python makine.py --tek-tur      # bir tur (ilk çalıştırmada ~1500 saatlik geçmiş: birkaç dk)
  cd /root && screen -dmS makine bash -c "venv/bin/python -u makine.py"   # sürekli (her saat başı + 20 sn)
  cd /root && venv/bin/python makine.py --durum        # son turlar, açık kâğıt işlemler (salt okunur)
  cd /root && venv/bin/python makine.py --rapor [--telegram]
Güncelleme (tools/temiz_kurulum.py --guncelle makine çalışırken reddeder): önce makineyi durdurun
  screen -S makine -X quit
güncellemeden sonra yeniden başlatın (yukarıdaki screen komutu). Makine çalışmıyorsa günlük rapor gelmez ve geçen
saatler KACIRILDI olur: güncellemeden sonra 'makine.py --durum' ile son turun zamanına bakın.
Dosyalar (veritabanının klasöründe): makine.db (+ -wal/-shm), makine.db.lock (tek kopya kilidi), makine.log (5 MB x 3).
"""
import argparse
import hashlib
import json
import logging
import logging.handlers
import math
import os
import shutil
import signal
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

KOK = os.path.dirname(os.path.abspath(__file__))
for _yol in (KOK, os.path.join(KOK, 'tools')):
    if _yol not in sys.path:
        sys.path.insert(0, _yol)

import strateji_lab as sl  # noqa: E402  (tek kod ilkesi: göstergeler, sinyaller, çıkışlar oradan)
from sniper import hafiza as hz  # noqa: E402
from sniper import makine_ozellikleri as mo  # noqa: E402

LOG = logging.getLogger('makine')

SAAT_MS = sl.SAAT_MS
GUN_MS = sl.GUN_MS
DAKIKA_MS = 60_000
BEKLEME_MS = 20_000             # saat başından sonra tur başlamadan önce bekleme (son mum borsada yerleşsin)
ZAMANINDA_MS = 15 * DAKIKA_MS   # karar mumu kapanışından bu süre içinde başlayan tur yeni kâğıt işlem açabilir
GECIKME_BEKLE_SN = 5            # son kapanan mumu henüz gelmeyen pariteler bu kadar beklenip bir kez yeniden çekilir
YENIDEN_DENE_MS = DAKIKA_MS     # hatalı tur (zamanındaysa) bu aralıkla yeniden denenir
AZAMI_DENEME = 3                # bir karar saati için en çok tur denemesi
UYKU_PARCA_SN = 1.0             # bekleme parça parça: durdurma isteği en geç bu kadar sonra görülür
GIRIS_BEKLE_MS = 48 * SAAT_MS   # giriş mumu bu kadar sonra hâlâ gelmeyen BEKLIYOR işlem ATLANDI
VERI_YOK_BEKLE_MS = GUN_MS      # işlemi durdurulan paritede açık işlem azami süreden bu kadar sonra VERI_YOK
EKSIK_ORAN = 0.5                # evren paritelerinin bundan fazlasında karar mumu yoksa tur HATA
ISLEM_USDT = 20.0               # rapordaki kâğıt işlem büyüklüğü (USDT, bileşik değil)
KARNE_GUN = 30                  # dedektör karnesi penceresi
UYARI_ARALIK_MS = 6 * SAAT_MS   # aynı türden uyarı en çok bu aralıkla
UYARI_YENIDEN_MS = 15 * DAKIKA_MS   # gönderilemeyen uyarı en erken bu kadar sonra yeniden denenir
SAAT_FARKI_ESIK_MS = 5_000      # yerel saat ile borsa saati arasındaki fark bundan büyükse uyarı
DISK_ALT_SINIR = 1 << 30        # veritabanı klasöründe bundan az boş alan kalınca uyarı (bayt)
TELEGRAM_PARCA = 4000           # Telegram mesaj sınırı (4096) altında
HATA_ORNEK = 5                  # tur kaydına yazılan en çok hata örneği
OZELLIK_SURUMU = 1              # özellik/etiket/kural tanımları anlamca değişince elle artırılır (tanim tablosu)
BTC_OZELLIKLERI = tuple(k for k in hz.LAB_OZELLIKLERI if k.startswith('btc_'))   # karar mumu olmayan satıra da

KAGIT_STRATEJILER = ['BOT_VEKILI', 'ERKEN_BIRIKIM', 'SIKISMA_KIRILIM', 'TREND_DIP_RSI2', 'YUKSEK_ISABET',
                     'YATAY_DONUS', 'REJIM_KOMBO', 'REJIM_KOMBO_BOT']
_LAB = sl.arguman_ayristirici().parse_args([])          # kuralların ön kayıtlı parametreleri (lab varsayılanları)
SINYAL_MIN_HACIM, BUYUK_HACIM = float(_LAB.min_hacim), float(_LAB.buyuk_hacim)
TELEGRAM_ANAHTARLARI = ('TELEGRAM_TOKEN', 'TELEGRAM_CHAT_ID')


# ------------------------------------------------------------------------------------------
# Borsa (anahtarsız, salt okunur)
# ------------------------------------------------------------------------------------------
class SaltOkunurBorsa:
    """Borsa nesnesinin yalnız herkese açık okuma uçlarını geçiren sarmalayıcı. Listede olmayan HER öznitelik
    (emir, bakiye, hesap uçları, alt çizgili adlar ve __dict__ dahil: __getattribute__ hepsini keser) PermissionError
    verir; öznitelik yazılamaz ve silinemez. İç istemci yalnız ad karıştırılmış bir yuvada (__slots__) durur, normal
    öznitelik erişimiyle alınamaz. Makine borsayı yalnız bununla kullanır (Makine kendisi sarar)."""
    __slots__ = ('_SaltOkunurBorsa__ex',)
    IZINLI = frozenset({'fetch_tickers', 'publicGetKlines', 'fetch_order_book', 'fetch_time', 'load_markets', 'market',
                        'markets', 'id', 'rateLimit', 'last_response_headers'})

    def __init__(self, ex):
        object.__setattr__(self, '_SaltOkunurBorsa__ex', ex)

    def __getattribute__(self, ad):
        if ad not in SaltOkunurBorsa.IZINLI:
            raise PermissionError(f"makine yalnız herkese açık okuma uçlarını kullanır: '{ad}' engellendi")
        return getattr(object.__getattribute__(self, '_SaltOkunurBorsa__ex'), ad)

    def __setattr__(self, ad, deger):
        raise PermissionError("salt okunur borsa nesnesi değiştirilemez")

    def __delattr__(self, ad):
        raise PermissionError("salt okunur borsa nesnesi değiştirilemez")


def borsa_kur():
    """Anahtarsız ccxt Binance spot istemcisi (yalnız herkese açık piyasa verisi; strateji_lab.main ile aynı ayar)."""
    import ccxt
    ex = ccxt.binance({'enableRateLimit': True, 'options': {'defaultType': 'spot'}})
    ex.rateLimit = max(ex.rateLimit or 0, 100)   # botla aynı IP limiti paylaşılıyor
    return ex


# ------------------------------------------------------------------------------------------
# Telegram ve günlük
# ------------------------------------------------------------------------------------------
def telegram_ayarlari(kok: str = KOK, ortam=None) -> Tuple[Optional[str], Optional[str]]:
    """(token, chat_id): ortam değişkeni, yoksa <kok>/.env (python-dotenv varsa). ai_bot.py'deki load_dotenv ile aynı
    öncelik (ortam önce). .env'nin başka hiçbir anahtarı tutulmaz."""
    ortam = os.environ if ortam is None else ortam
    dosya: Dict[str, Optional[str]] = {}
    yol = os.path.join(kok, '.env')
    if os.path.exists(yol):
        try:
            from dotenv import dotenv_values
            tum = dotenv_values(yol)
            dosya = {k: tum.get(k) for k in TELEGRAM_ANAHTARLARI}
            del tum
        except ImportError:
            LOG.warning("python-dotenv yok: Telegram ayarı yalnız ortam değişkenlerinden okunur")
    deger = [(ortam.get(k) or dosya.get(k) or '').strip() or None for k in TELEGRAM_ANAHTARLARI]
    return deger[0], deger[1]


def _gizli_bicimleri(g: str) -> List[str]:
    """Gizli değerin günlüğe düşebileceği biçimleri: kendisi, boşluksuz hali, repr kaçışlı hali (kontrol karakteri
    \\n gibi yazılır) ve URL kodlu halleri. Uzundan kısaya (önce uzun biçim maskelensin)."""
    bicimler = {g, g.strip(), repr(g)[1:-1], urllib.parse.quote(g), urllib.parse.quote(g, safe='')}
    return sorted((b for b in bicimler if b), key=len, reverse=True)


def maskele(metin: str, gizliler: Iterable[Optional[str]]) -> str:
    """Metindeki gizli değerleri (Telegram belirteci; _gizli_bicimleri) '***' ile değiştirir."""
    metin = str(metin)
    for g in gizliler:
        if g:
            for b in _gizli_bicimleri(g):
                metin = metin.replace(b, '***')
    return metin


def parcala(metin: str, azami: int = TELEGRAM_PARCA) -> List[str]:
    """Metni satır sınırlarından en çok `azami` karakterlik parçalara böler (tek satır uzunsa satır da bölünür)."""
    parcalar, bu = [], ''
    for satir in metin.split('\n'):
        while len(satir) > azami:
            if bu:
                parcalar.append(bu)
                bu = ''
            parcalar.append(satir[:azami])
            satir = satir[azami:]
        aday = satir if not bu else bu + '\n' + satir
        if len(aday) > azami:
            parcalar.append(bu)
            bu = satir
        else:
            bu = aday
    if bu or not parcalar:
        parcalar.append(bu)
    return parcalar


class Telegram:
    """Düz metin Telegram gönderici (parse_mode yok). acici: urllib.request.urlopen benzeri (testte sahte). Hata
    yükselmez: False döner ve belirteç maskelenmiş olarak günlüğe yazılır."""

    def __init__(self, token: Optional[str], chat_id: Optional[str], acici: Optional[Callable] = None,
                 zaman_asimi: float = 15.0):
        self.token = token.strip() if token else token
        self.chat_id = chat_id.strip() if chat_id else chat_id
        self.zaman_asimi = zaman_asimi
        self.acici = acici or urllib.request.urlopen

    @property
    def hazir(self) -> bool:
        return bool(self.token and self.chat_id)

    def gonder(self, metin: str) -> bool:
        if not self.hazir:
            return False
        for parca in parcala(metin):
            veri = urllib.parse.urlencode({'chat_id': self.chat_id, 'text': parca}).encode('utf-8')
            istek = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/sendMessage", data=veri)
            try:
                with self.acici(istek, timeout=self.zaman_asimi) as yanit:
                    durum = getattr(yanit, 'status', 200)
                    if durum != 200:
                        raise RuntimeError(f"HTTP {durum}")
            except Exception as e:  # noqa: BLE001 - rapor gönderilemedi: makine durmaz
                LOG.warning("Telegram mesajı gönderilemedi: "
                            + maskele(f"{type(e).__name__}: {str(e)[:300]}", [self.token]))
                return False
        return True


class MaskeliBicim(logging.Formatter):
    """Biçimlenmiş günlük satırında (ileti, argümanlar, hata izi) gizli değerleri maskeler; UTC zaman."""
    converter = time.gmtime

    def __init__(self, gizliler: Sequence[Optional[str]] = ()):
        super().__init__('%(asctime)s %(levelname)s %(name)s: %(message)s', '%Y-%m-%d %H:%M:%S')
        self.gizliler = [g for g in gizliler if g]

    def format(self, record):
        return maskele(super().format(record), self.gizliler)


def gunluk_kur(dosya: Optional[str] = None, gizliler: Sequence[Optional[str]] = (), akis=None) -> None:
    """'makine' günlüğü: stdout (ya da akis) + dosya verilirse RotatingFileHandler (5 MB x 3). Gizliler maskelenir."""
    LOG.setLevel(logging.INFO)
    LOG.propagate = False
    for eski in list(LOG.handlers):
        LOG.removeHandler(eski)
        eski.close()
    isleyiciler: List[logging.Handler] = [logging.StreamHandler(akis or sys.stdout)]
    if dosya:
        isleyiciler.append(logging.handlers.RotatingFileHandler(dosya, maxBytes=5 * 1024 * 1024, backupCount=3,
                                                                encoding='utf-8'))
    for i in isleyiciler:
        i.setFormatter(MaskeliBicim(gizliler))
        LOG.addHandler(i)


# ------------------------------------------------------------------------------------------
# Pencereler (bellekte, veritabanından)
# ------------------------------------------------------------------------------------------
class PencereOnbellegi:
    """Paritelerin son PENCERE saatlik mumları bellekte (satırlar: ts, o, h, l, c, v, qv, islem_adedi, alici_qv).
    Her istekte yalnız önbellekteki son mumdan sonrakiler okunur; sonra pencerenin satır sayısı veritabanındakiyle
    karşılaştırılır, tutmazsa (ör. geriye doğru boşluk dolduruldu) pencere baştan okunur. Toplayıcı mevcut mumları
    değiştirmez, yalnız kapsamdan (son mumdan) sonrasını ekler: önbellek veritabanıyla birebir aynı ızgarayı verir
    (Hafiza.izgara ile aynı; testte doğrulanır)."""

    def __init__(self, hafiza: hz.Hafiza, pencere: int = hz.PENCERE):
        self.h, self.pencere = hafiza, int(pencere)
        self._d: Dict[str, Tuple[int, np.ndarray]] = {}       # sembol -> (tam okunan alt sınır, satırlar)
        self.tam_okuma = 0

    def izgara(self, sembol: str, son_ts: int) -> hz.Izgara:
        bas, bit = int(son_ts) - (self.pencere - 1) * SAAT_MS, int(son_ts) + SAAT_MS
        kayit = self._d.get(sembol)
        d = None
        if kayit is not None and kayit[0] <= bas:
            eski = kayit[1]
            alt = int(eski[-1, 0]) + SAAT_MS if len(eski) else bas
            yeni = self.h.mumlar(sembol, max(alt, bas), bit) if alt < bit else np.empty((0, 9))
            d = np.vstack([eski[eski[:, 0] >= bas], yeni]) if len(yeni) else eski[eski[:, 0] >= bas]
            n = self.h.c.execute('SELECT COUNT(*) FROM mum WHERE sembol = ? AND ts >= ? AND ts < ?',
                                 (sembol, bas, bit)).fetchone()[0]
            if int(n) != int((d[:, 0] < bit).sum()):
                d = None
        if d is None:
            d = self.h.mumlar(sembol, bas, bit)
            self.tam_okuma += 1
        self._d[sembol] = (bas, d)
        return hz.izgaraya_koy(d[d[:, 0] < bit], bas, self.pencere)

    def birak(self, tut: Iterable[str]) -> None:
        """Yalnız tut'taki pariteler bellekte kalır (evrenden çıkanlar bırakılır)."""
        tut = set(tut)
        for s in [s for s in self._d if s not in tut]:
            del self._d[s]


# ------------------------------------------------------------------------------------------
# Sinyal yardımcıları
# ------------------------------------------------------------------------------------------
def son_karar_mumu(simdi_ms: int) -> int:
    """Şimdiye göre yeni kapanan 1 saatlik mumun açılışı (karar mumu t)."""
    return (int(simdi_ms) // SAAT_MS) * SAAT_MS - SAAT_MS


def karar_sinyalleri(s: Dict[str, np.ndarray], rejim: np.ndarray, i: int = -1) -> Dict[str, str]:
    """strateji_lab.sinyaller çıktısından i. mumda (karar mumu) tetiklenen kâğıt stratejileri: {strateji: bileşen}.
    Kombolarda bileşen strateji_lab.kombo_bilesenleri'nin öncelik sırasıyla (trend bileşeni önce), diğerlerinde
    bileşen = strateji. Sıra KAGIT_STRATEJILER."""
    sonuc = {}
    for k in KAGIT_STRATEJILER:
        if k in sl.KOMBO_TREND:
            bilesenler = sl.kombo_bilesenleri(k, s, rejim)
            for ad, (x, _) in (bilesenler or {}).items():
                if x[i]:
                    sonuc[k] = ad
                    break
        elif k in s and s[k][i]:
            sonuc[k] = k
    return sonuc


class Durduruldu(Exception):
    """Durdurma isteği (SIGTERM/SIGINT): tur bir adım bitince bırakılır."""


# ------------------------------------------------------------------------------------------
# Makine
# ------------------------------------------------------------------------------------------
class Makine:
    """Tarayıcı + kâğıt işlem + etiket + rapor. ex: borsa (SaltOkunurBorsa ile sarılır), hafiza: yazılabilir Hafiza.
    saat / uyku testte sahte (simüle saat: gerçek bekleme yok). disk_alt_sinir: boş alan uyarısı eşiği (bayt)."""

    def __init__(self, ex, hafiza: hz.Hafiza, evren: int = 150, min_hacim: float = 5e6, defter_max: int = 30,
                 rapor_saati_utc: int = 6, aday_gun: int = 400, telegram: Optional[Telegram] = None,
                 saat: Optional[Callable[[], int]] = None, uyku: Callable[[float], None] = time.sleep,
                 pencere: int = hz.PENCERE, disk_alt_sinir: int = DISK_ALT_SINIR):
        self.ex = ex if isinstance(ex, SaltOkunurBorsa) else SaltOkunurBorsa(ex)
        self.h = hafiza
        self.evren, self.min_hacim, self.defter_max = int(evren), float(min_hacim), int(defter_max)
        self.rapor_saati, self.aday_gun = int(rapor_saati_utc), int(aday_gun)
        self.telegram = telegram
        self.saat = saat or hafiza.saat
        self.uyku = uyku
        self.pencere = int(pencere)
        self.disk_alt_sinir = int(disk_alt_sinir)
        self.durdu = False
        self.toplayici = hz.MumToplayici(self.ex, hafiza, uyku=uyku, pencere=pencere)
        self.toplayici.dur = lambda: self.durdu
        self.pencereler = PencereOnbellegi(hafiza, pencere)
        simdi = int(self.saat())
        self.ilk = hafiza.ilk_baslangic(simdi)
        # ilk kâğıt işlemin karar mumu: girişi ilk başlangıcın saat başında olan mum (lab'da --baslangic)
        self.islem_alt_ts = (self.ilk // SAAT_MS) * SAAT_MS - SAAT_MS
        self._deneme: Dict[int, Tuple[int, int]] = {}         # karar saati -> (deneme sayısı, son denemenin bitişi)
        # veritabanından bağımsız durum (disk dolu / veritabanı yazılamıyor olsa da uyarı ve rapor çalışsın)
        self._uyari_son: Dict[str, int] = {}                  # tür -> son BAŞARILI uyarı
        self._uyari_deneme: Dict[str, int] = {}               # tür -> son başarısız gönderim denemesi
        self._rapor_gunu: Optional[int] = None                # son gönderilen (ya da işaretlenen) rapor günü
        self._yazilamayan: Dict[int, dict] = {}               # karar saati -> yazılamayan tur kaydı
        self._son_db_hatasi: Optional[Tuple[int, str]] = None  # (ms, mesaj): son veritabanı yazma hatası
        self._piyasa_gunu: Optional[int] = None               # piyasaların son yeniden yüklendiği UTC günü
        self._bilinmeyen_kimlik: set = set()                  # piyasa yenilemesine yol açmış ticker kimlikleri
        self.saat_farki_ms: Optional[int] = None              # son ölçülen yerel - borsa saati (ms)
        self.tanim_id = self._tanim_kaydet(simdi)

    # --- yardımcılar ------------------------------------------------------------------------
    def durdur(self, *_):
        if not self.durdu:
            LOG.info("durdurma isteği alındı: sürmekte olan adım bitince çıkılacak")
        self.durdu = True

    def _durdu_mu(self):
        if self.durdu:
            raise Durduruldu()

    def _kapsam(self, sembol: str) -> Optional[int]:
        p = self.h.parite_oku(sembol)
        return None if not p or p.get('kapsam_ts') is None else int(p['kapsam_ts'])

    def tanim_icerigi(self) -> dict:
        """Tanım sürümünün içeriği (modül belgesi): elle artırılan sürüm, tanımları belirleyen kaynak dosyaların
        sha256'sı (ilk 12 hane) ve kural/evren/etiket parametreleri."""
        kaynaklar = {'strateji_lab': sl.__file__, 'v184_simulasyon': sl.vs.__file__,
                     'backfill_sinyaller': sl.bf.__file__, 'makine_ozellikleri': mo.__file__, 'hafiza': hz.__file__,
                     'makine': os.path.abspath(__file__)}
        ozet = {}
        for ad, yol in sorted(kaynaklar.items()):
            try:
                with open(yol, 'rb') as f:
                    ozet[ad] = hashlib.sha256(f.read()).hexdigest()[:12]
            except OSError:
                ozet[ad] = None
        return {'ozellik_surumu': OZELLIK_SURUMU, 'kaynak': ozet,
                'parametre': {'stratejiler': KAGIT_STRATEJILER, 'sinyal_min_hacim': SINYAL_MIN_HACIM,
                              'buyuk_hacim': BUYUK_HACIM, 'radar_n': sl.RADAR_N, 'radar_esik': mo.RADAR_ESIK,
                              'slip': [sl.SLIP_GIRIS, sl.SLIP_SEVIYE, sl.SLIP_ZAMAN], 'fee': sl.FEE,
                              'azami_tutma': sl.AZAMI_TUTMA, 'ufuk': mo.UFUK, 'ucus': mo.UCUS,
                              'azami_eksik': mo.AZAMI_EKSIK, 'pencere': self.pencere, 'evren': self.evren,
                              'min_hacim': self.min_hacim, 'defter_max': self.defter_max}}

    def _tanim_kaydet(self, simdi: int) -> Optional[int]:
        """Tanım sürümünü yazar; değiştiyse günlüğe ve olay tablosuna (TANIM) yazar. Yazılamazsa None (satırlar
        tanimsiz yazılır, makine durmaz)."""
        try:
            kimlik, onceki = self.h.tanim_kaydet(self.tanim_icerigi(), simdi)
        except sqlite3.Error as e:
            LOG.warning(f"tanım sürümü yazılamadı ({type(e).__name__}: {e})")
            return None
        if onceki is not None:
            mesaj = (f"tanım değişti: {onceki} -> {kimlik} (kaynak ya da parametre; bundan sonraki satırlar "
                     f"tanim_id = {kimlik})")
            LOG.warning(mesaj)
            try:
                self.h.olay_yaz('TANIM', mesaj, ms=simdi)
            except sqlite3.Error as e:
                self._db_hatasi(e, simdi)
        return kimlik

    def _db_hatasi(self, e: BaseException, simdi: int) -> None:
        """Veritabanı hatası bellekte tutulur (günlük rapor ve uyarı metni için)."""
        if isinstance(e, sqlite3.Error):
            self._son_db_hatasi = (int(simdi), f"{type(e).__name__}: {str(e)[:200]}")

    def _uyar(self, tur: str, mesaj: str, simdi: int) -> bool:
        """Tür başına UYARI_ARALIK_MS'de en çok bir BAŞARILI Telegram uyarısı (modül belgesi). Veritabanından
        bağımsız: sınırlama zamanı bellekte; veritabanı yalnız yeniden başlatmada önceki zamanı bilmek için (okuma
        ve yazma en iyi çaba). Gönderilemezse UYARI_YENIDEN_MS sonra yeniden denenir."""
        if self.telegram is None or not self.telegram.hazir:
            return False
        son = self._uyari_son.get(tur)
        if son is None:
            try:
                deger = self.h.meta_oku(f'uyari_{tur}')
                son = int(deger) if deger is not None else None
            except (sqlite3.Error, ValueError):
                son = None
        if son is not None and int(simdi) - son < UYARI_ARALIK_MS:
            return False
        deneme = self._uyari_deneme.get(tur)
        if deneme is not None and int(simdi) - deneme < UYARI_YENIDEN_MS:
            return False
        if not self.telegram.gonder(f"MAKİNE UYARISI ({tur}) {sl.vs._tarih(simdi)} UTC\n{mesaj}"):
            self._uyari_deneme[tur] = int(simdi)
            return False
        self._uyari_son[tur] = int(simdi)
        self._uyari_deneme.pop(tur, None)
        try:
            self.h.meta_yaz(f'uyari_{tur}', int(simdi))
        except sqlite3.Error as e:
            LOG.warning(f"uyarı zamanı veritabanına yazılamadı ({type(e).__name__}: {e})")
        return True

    def _olay_ve_uyari(self, tur: str, mesaj: str, simdi: int, olay_turu: str = 'HATA') -> None:
        """Olay kaydı (en iyi çaba) ve ondan BAĞIMSIZ olarak uyarı."""
        try:
            self.h.olay_yaz(olay_turu, mesaj, ms=simdi)
        except Exception as e:  # noqa: BLE001 - veritabanı yazılamıyor olabilir: uyarı yine gönderilir
            self._db_hatasi(e, simdi)
            LOG.exception("olay kaydı yazılamadı")
        self._uyar(tur, mesaj, simdi)

    def _saat_denetimi(self, simdi: int) -> Optional[int]:
        """Borsa saati (fetch_time, ağırlık 1) ile yerel saat farkı (ms, + = yerel ileri); fark SAAT_FARKI_ESIK_MS'i
        aşarsa uyarı. Alınamazsa None (tur sürer)."""
        try:
            sunucu = int(self.ex.fetch_time())
        except Exception:  # noqa: BLE001 - en iyi çaba
            return None
        fark = int(self.saat()) - sunucu
        self.saat_farki_ms = fark
        if abs(fark) > SAAT_FARKI_ESIK_MS:
            mesaj = (f"yerel saat borsa saatinden {fark / 1000:+.1f} sn farklı: sunucu saatini NTP ile eşitleyin "
                     f"(kapanmış mum kararı yanıttaki sonraki muma göre verildiği için veri bozulmaz; döngü saat başı "
                     f"beklemesine farkı ekler)")
            LOG.warning(mesaj)
            self._olay_ve_uyari('SAAT', mesaj, simdi, olay_turu='UYARI')
        return fark

    def _disk_denetimi(self, simdi: int) -> Optional[int]:
        """Veritabanı klasöründeki boş alan (bayt); disk_alt_sinir'in altındaysa DISK uyarısı."""
        klasor = os.path.dirname(os.path.abspath(self.h.yol))
        try:
            bos = int(shutil.disk_usage(klasor).free)
        except OSError:
            return None
        if bos < self.disk_alt_sinir:
            mesaj = f"diskte {bos / 1e9:.2f} GB boş alan kaldı ({klasor}): veritabanı yakında yazılamayabilir"
            LOG.warning(mesaj)
            self._olay_ve_uyari('DISK', mesaj, simdi, olay_turu='UYARI')
        return bos

    def _piyasalari_tazele(self, tickers: dict, simdi: int) -> dict:
        """Piyasaları her UTC günün ilk turunda ve ticker'larda yüklü piyasalarda olmayan yeni bir USDT kimliği
        (ccxt eğiksiz 'YENIUSDT' anahtarı verir) görülünce yeniden yükler; ikinci durumda ticker'lar yeniden çekilir.
        Yükleme başarısızsa eldeki ticker'larla sürer."""
        gun = int(simdi) // GUN_MS
        yeni = {k for k in tickers if isinstance(k, str) and '/' not in k and k.endswith('USDT')}
        yeni -= self._bilinmeyen_kimlik
        if not yeni and self._piyasa_gunu == gun:
            return tickers
        try:
            self.toplayici.cagir(self.ex.load_markets, True)
            self._piyasa_gunu = gun
            self._bilinmeyen_kimlik |= yeni
            if not yeni:
                return tickers
            LOG.info(f"yüklü piyasalarda olmayan {len(yeni)} parite görüldü ({', '.join(sorted(yeni)[:5])}): "
                     f"piyasalar yenilendi")
            return self.toplayici.cagir(self.ex.fetch_tickers)
        except Exception as e:  # noqa: BLE001 - eldeki ticker'larla sürer
            LOG.warning(f"piyasalar yenilenemedi ({type(e).__name__}: {str(e)[:200]})")
            return tickers

    def _evren(self, korunan: List[str], simdi: int) -> Tuple[hz.Evren, bool]:
        """(Evren, yedek mi): fetch_tickers alınamazsa son başarılı evren (meta.son_evren) kullanılır."""
        try:
            tickers = self._piyasalari_tazele(self.toplayici.cagir(self.ex.fetch_tickers), simdi)
        except Exception as e:  # noqa: BLE001 - yedek evrenle sürer
            onceki = self.h.meta_oku('son_evren')
            if not onceki:
                raise
            o = json.loads(onceki)
            LOG.warning(f"ticker alınamadı ({type(e).__name__}: {str(e)[:200]}); son evren kullanılıyor")
            self.h.olay_yaz('UYARI', f"ticker alınamadı, son evren kullanıldı: {type(e).__name__}: {str(e)[:300]}")
            semboller = list(o['semboller'])
            return hz.Evren(semboller, dict(o['sira']), {}, semboller + [k for k in korunan if k not in semboller]), True
        ev = hz.evren_sec(tickers, self.min_hacim, self.evren, korunan=korunan,
                          piyasalar=getattr(self.ex, 'markets', None))
        self.h.meta_yaz('son_evren', json.dumps({'semboller': ev.semboller, 'sira': ev.sira}, ensure_ascii=False))
        return ev, False

    def _bos_satir(self, t: int, s: str, ev: hz.Evren, btc_oz: dict, rejim_t: str, simdi: int) -> dict:
        """Karar mumu olmayan evren paritesinin aday satırı: BTC özellikleri (btc_oz: o turun BTC satırından),
        rejim, evren sırası, karar_mumu = 0; paritenin kendi özellikleri NULL, sinyal yok."""
        return {'ts': t, 'sembol': s, **btc_oz, 'btc_rejim': rejim_t, 'radar_sira': None,
                'evren_sira': ev.sira.get(s), 'sinyaller': '', 'karar_mumu': 0, 'olusturma_ms': simdi,
                'tanim_id': self.tanim_id}

    # --- tur ----------------------------------------------------------------------------------
    def tur(self, simdi: Optional[int] = None) -> dict:
        """Karar mumu t = son_karar_mumu(simdi) için bir tur (modül belgesi). Dönen: tarama kaydı (ayrinti dict).
        Hatalar yükselir (tur_guvenli yakalar); durdurma isteğinde Durduruldu."""
        simdi = int(self.saat() if simdi is None else simdi)
        t = son_karar_mumu(simdi)
        sure, z0 = {}, time.perf_counter()
        zamaninda = simdi - (t + SAAT_MS) <= ZAMANINDA_MS
        islem_izni = zamaninda and t >= self.islem_alt_ts
        kacan = self.h.kacirilanlari_yaz(t)
        if kacan:
            LOG.warning(f"{len(kacan)} karar saati kaçırıldı ({sl.vs._tarih(kacan[0])} - {sl.vs._tarih(kacan[-1])} "
                        f"UTC): o saatlerde kâğıt işlem açılmadı")
        if not zamaninda:
            LOG.warning(f"tur geç başladı ({(simdi - t - SAAT_MS) / DAKIKA_MS:.0f} dk): bu karar saatinde yeni kâğıt "
                        f"işlem açılmaz")
        saat_farki = self._saat_denetimi(simdi)
        self._disk_denetimi(simdi)

        # 1) evren ve mumlar
        korunan = self.h.korunacak_pariteler(simdi)
        ev, yedek = self._evren(korunan, simdi)
        m = self.toplayici.guncelle(ev.guncellenecek, simdi)
        geciken = [s for s in ev.semboller if s in m['yeni'] and self._kapsam(s) == t]
        if geciken and not self.durdu and not self.toplayici.sinirda():   # son mum henüz yok: kısa bekle, yeniden çek
            self.uyku(GECIKME_BEKLE_SN)
            m2 = self.toplayici.guncelle(geciken, int(self.saat()))
            for s, n in m2['yeni'].items():
                m['yeni'][s] = m['yeni'].get(s, 0) + n
            for s, e in m2['hata'].items():
                m['hata'].setdefault(s, e)
            m['istek'] += m2['istek']
        sure['mum'] = time.perf_counter() - z0
        self._durdu_mu()
        kalan, sabit, verisiz = self.h.sabit_ayikla(ev.semboller)
        basarisiz = set(m['hata']) | set(m['atlanan']) | set(m['durdurulan'])

        # 2) pencereler, BTC bağlamı, radar, göstergeler, sinyaller, aday satırları
        z1 = time.perf_counter()
        tum = list(dict.fromkeys(['BTC/USDT'] + kalan))
        izg = {s: self.pencereler.izgara(s, t) for s in tum}
        self.pencereler.birak(tum)
        b = izg['BTC/USDT']
        i = len(b.ts) - 1
        btc = sl.btc_baglami(b.C, b.H, b.L)
        rejim_t = str(btc['rejim'][i])
        kesit = [sl.kesit_sutunu(izg[s].C, izg[s].V) for s in kalan]
        radar_sira = mo.radar_siralari([k[0][i] for k in kesit], [k[1][i] for k in kesit]) if kalan else np.empty(0)
        sinyal_t, satirlar, mumsuz = {}, [], []
        sinyal_sayisi: Dict[str, int] = {}
        for j, s in enumerate(kalan):
            z = izg[s]
            if not np.isfinite(z.C[i]):          # karar mumu yok (alınamadı ya da işlem durdu): boş satır, sinyal yok
                mumsuz.append(s)
                continue
            g = sl.gostergeler(z.O, z.H, z.L, z.C, z.V)
            radar = np.zeros(len(z.C), dtype=bool)
            radar[i] = bool(radar_sira[j] <= sl.RADAR_N)
            sg = karar_sinyalleri(sl.sinyaller(g, btc['ok'], radar, SINYAL_MIN_HACIM, BUYUK_HACIM, btc['rejim']),
                                  btc['rejim'], i)
            sinyal_t[s] = sg
            for k in sg:
                sinyal_sayisi[k] = sinyal_sayisi.get(k, 0) + 1
            satirlar.append({'ts': t, 'sembol': s, **hz.aday_ozellikleri(g, btc, z, i), 'btc_rejim': rejim_t,
                             'radar_sira': radar_sira[j], 'evren_sira': ev.sira.get(s), 'sinyaller': ','.join(sg),
                             'karar_mumu': 1, 'olusturma_ms': simdi, 'tanim_id': self.tanim_id})
        # karar mumu olmayan evren paritelerinin (verisiz: hiç mumu yok, ilk çekim başarısız / delist) satırları
        btc_oz = {k: r[k] for r in satirlar if r['sembol'] == 'BTC/USDT' for k in BTC_OZELLIKLERI}
        satirlar += [self._bos_satir(t, s, ev, btc_oz, rejim_t, simdi) for s in mumsuz + verisiz]
        sure['hesap'] = time.perf_counter() - z1
        self._durdu_mu()

        # karar mumu denetimi: BTC'nin ya da evren paritelerinin çoğunun karar mumu yoksa tur HATA (yeni işlem yok)
        eksik = mumsuz + verisiz
        evren_n = len(kalan) + len(verisiz)
        neden = None
        if evren_n and len(eksik) == evren_n:
            neden = 'hiç mum alınamadı'
        elif not np.isfinite(b.C[i]):
            neden = 'BTC karar mumu yok'
        elif evren_n and len(eksik) / evren_n > EKSIK_ORAN:
            neden = f"{len(eksik)}/{evren_n} paritede karar mumu yok"
        if neden and self.toplayici.sinirda():
            neden += f" (istek sınırı: {self.toplayici.son_sinir})"
        if neden:
            islem_izni = False

        # 3) kâğıt işlemler
        z2 = time.perf_counter()
        kagit = self._kagit(t, kalan, sinyal_t, rejim_t, simdi, islem_izni, basarisiz)
        if islem_izni and self.h.meta_oku('ilk_islem_ts') is None:
            self.h.meta_yaz('ilk_islem_ts', t)          # lab karşılığı: --baslangic = bu + 1 saat (modül belgesi)
        sure['kagit'] = time.perf_counter() - z2
        self._durdu_mu()

        # 4) aday satırları, emir defteri, etiketler, saklama
        z3 = time.perf_counter()
        self.h.aday_yaz(satirlar)
        defter = self.toplayici.defter_topla(hz.ilginc_sec([r for r in satirlar if r['karar_mumu']],
                                                           self.defter_max), t, int(self.saat()))
        etiketlenen = self.h.etiketle(simdi, tanim_id=self.tanim_id)
        temizlik = self.h.gunluk_temizlik(simdi, self.aday_gun)
        sure['yaz'] = time.perf_counter() - z3

        hatalar = dict(list(m['hata'].items())[:HATA_ORNEK])
        ayrinti = {'sinyal': dict(sorted(sinyal_sayisi.items())), 'islem_izni': islem_izni, 'gec': not zamaninda,
                   'yedek_evren': yedek, 'yeni_bekleyen': kagit['yeni'], 'atlandi': kagit['atlandi'],
                   'sabit': sabit, 'verisiz': verisiz, 'mumsuz': len(mumsuz), 'mumsuz_ornek': mumsuz[:HATA_ORNEK],
                   'yok': m['yok'][:HATA_ORNEK], 'hata': hatalar, 'atlanan': len(m['atlanan']),
                   'istek': m['istek'] + defter['istek'], 'kacirilan': len(kacan),
                   'saat_farki_ms': saat_farki, 'sure_sn': {k: round(v, 3) for k, v in sure.items()}}
        if temizlik:
            ayrinti['temizlik'] = temizlik
        if neden:
            ayrinti['neden'] = neden
        kayit = {'ts': t, 'durum': 'HATA' if neden else 'TAMAM', 'baslangic_ms': simdi,
                 'bitis_ms': int(self.saat()), 'evren': len(kalan),
                 'guncellenen': sum(1 for n in m['yeni'].values() if n > 0),
                 'mum_hatasi': len(m['hata']) + len(m['atlanan']), 'sinyal': sum(sinyal_sayisi.values()),
                 'acilan': kagit['acilan'], 'kapanan': kagit['kapanan'], 'etiketlenen': etiketlenen,
                 'defter': defter['yazilan'], 'ayrinti': ayrinti, 'tanim_id': self.tanim_id}
        self.h.tarama_yaz(kayit)
        self._yazilamayan.pop(t, None)          # aynı saatin önceki denemesinden kalan yazılamamış kayıt artık geçersiz
        sure['toplam'] = time.perf_counter() - z0
        LOG.info(f"tur {sl.vs._tarih(t)} UTC: evren {len(kalan)}, güncellenen {kayit['guncellenen']}, mum hatası "
                 f"{kayit['mum_hatasi']}, sinyal {kayit['sinyal']}, yeni kâğıt {kagit['yeni']}, açılan "
                 f"{kagit['acilan']}, kapanan {kagit['kapanan']}, etiket {etiketlenen}, defter {defter['yazilan']}, "
                 f"{m['istek'] + defter['istek']} istek, {sure['toplam']:.1f} sn"
                 + ('' if islem_izni else ' (yeni kâğıt işlem yok: ' + ('geç' if not zamaninda else neden or
                                                                          'ilk başlangıç öncesi') + ')'))
        if neden:
            mesaj = f"karar saati {sl.vs._tarih(t)} UTC: {neden} ({len(m['hata'])} hata, {len(m['atlanan'])} atlanan)"
            LOG.error(mesaj)
            self._olay_ve_uyari('MUM_YOK', mesaj, simdi)
        return kayit

    def _tarama_kaydet(self, kayit: dict, simdi: int) -> None:
        """Tur kaydını yazar; yazılamazsa bellekte tutar (sonraki turun başında yeniden denenir)."""
        try:
            self.h.tarama_yaz(kayit)
            self._yazilamayan.pop(int(kayit['ts']), None)
        except Exception as e:  # noqa: BLE001
            self._db_hatasi(e, simdi)
            self._yazilamayan[int(kayit['ts'])] = kayit
            LOG.exception("tur kaydı yazılamadı (bellekte tutuluyor)")

    def tur_guvenli(self, simdi: Optional[int] = None) -> dict:
        """tur(); hiçbir hata yükselmez: hata tur kaydına (HATA) ve olay tablosuna yazılır, uyarı gönderilir (ikisi
        birbirinden ve veritabanından bağımsız). Durdurma isteğinde tur 'YARIM' kaydedilir (zamanındaysa sonraki
        başlatmada yeniden yapılır). Önceki turlardan yazılamamış kayıtlar önce yazılır."""
        simdi = int(self.saat() if simdi is None else simdi)
        t = son_karar_mumu(simdi)
        for kayit in [self._yazilamayan[k] for k in sorted(self._yazilamayan) if k != t]:
            self._tarama_kaydet(kayit, simdi)
        try:
            return self.tur(simdi)
        except Durduruldu:
            kayit = {'ts': t, 'durum': 'YARIM', 'baslangic_ms': simdi, 'bitis_ms': int(self.saat()),
                     'ayrinti': {'neden': 'durdurma isteği'}}
        except Exception as e:  # noqa: BLE001 - döngü asla ölmez
            self._db_hatasi(e, simdi)
            mesaj = f"karar saati {sl.vs._tarih(t)} UTC turu başarısız: {type(e).__name__}: {str(e)[:500]}"
            LOG.exception(mesaj)
            kayit = {'ts': t, 'durum': 'HATA', 'baslangic_ms': simdi, 'bitis_ms': int(self.saat()),
                     'ayrinti': {'hata': f"{type(e).__name__}: {str(e)[:500]}"}}
            self._olay_ve_uyari('TUR_HATASI', mesaj, simdi)
        self._tarama_kaydet(kayit, simdi)
        return kayit

    # --- kâğıt işlem --------------------------------------------------------------------------
    def _pozisyonlar(self) -> Dict[Tuple[str, str], Tuple[int, Optional[int]]]:
        """(strateji, sembol) -> (BEKLIYOR/ACIK işlem sayısı, son KAPALI işlemin cikis_ts'i)."""
        return {(r['strateji'], r['sembol']): (int(r['acik'] or 0), r['son_cikis']) for r in self.h.sorgu(
            "SELECT strateji, sembol, SUM(durum IN ('BEKLIYOR', 'ACIK')) AS acik, "
            "MAX(CASE WHEN durum = 'KAPALI' THEN cikis_ts END) AS son_cikis FROM kagit_islem GROUP BY strateji, sembol")}

    def _kagit(self, t: int, kalan: List[str], sinyal_t: Dict[str, Dict[str, str]], rejim_t: str, simdi: int,
               islem_izni: bool, basarisiz: Iterable[str] = ()) -> dict:
        """Bekleyenler -> çıkışlar -> yeni sinyaller (modül belgesi), tek veritabanı işleminde. basarisiz: bu tur
        mumları çekilemeyen pariteler (giriş mumu 'gelmeyecek' ya da VERI_YOK kararı onlarda verilmez)."""
        basarisiz = set(basarisiz)
        say = {'acilan': 0, 'kapanan': 0, 'atlandi': 0, 'yeni': 0}
        with self.h.islem():
            for k in self.h.kagit_islemler('BEKLIYOR'):
                s, giris_ts = k['sembol'], int(k['giris_ts'])
                if giris_ts > t:
                    continue
                durum = self.h.mum_durumu(s, giris_ts)
                if durum == 'VAR':
                    o = float(self.h.mumlar(s, giris_ts, giris_ts + SAAT_MS)[0, 1])
                    self.h.kagit_guncelle(k['id'], durum='ACIK', giris_fiyat=o * (1 + sl.SLIP_GIRIS),
                                          guncelleme_ms=simdi)
                    say['acilan'] += 1
                elif durum == 'YOK' or (s not in basarisiz and simdi >= giris_ts + SAAT_MS + GIRIS_BEKLE_MS):
                    # giriş mumu kalıcı olarak yok (ya da 48 saattir gelmiyor: delist / işlem durdu): lab gibi atlanır
                    if durum != 'YOK':
                        LOG.warning(f"{s}: {k['strateji']} giriş mumu {GIRIS_BEKLE_MS // SAAT_MS} saattir gelmedi "
                                    f"(parite delist ya da işlemi durdurulmuş): ATLANDI")
                    self.h.kagit_guncelle(k['id'], durum='ATLANDI', guncelleme_ms=simdi)
                    say['atlandi'] += 1
            for k in self.h.kagit_islemler('ACIK'):
                cikis = self._cikis(k, t, simdi, basarisiz)
                if cikis is not None:
                    self.h.kagit_guncelle(k['id'], durum='KAPALI', guncelleme_ms=simdi, **cikis)
                    say['kapanan'] += 1
            if not islem_izni:
                return say
            pozisyon = self._pozisyonlar()
            for strateji in KAGIT_STRATEJILER:
                for s in kalan:
                    bilesen = sinyal_t.get(s, {}).get(strateji)
                    if bilesen is None:
                        continue
                    acik, son_cikis = pozisyon.get((strateji, s), (0, None))
                    if acik or (son_cikis is not None and int(son_cikis) > t):     # tek pozisyon kuralı
                        continue
                    if self.h.kagit_ekle({'strateji': strateji, 'bilesen': bilesen, 'sembol': s, 'sinyal_ts': t,
                                          'giris_ts': t + SAAT_MS, 'durum': 'BEKLIYOR', 'rejim': rejim_t,
                                          'olusturma_ms': simdi, 'tanim_id': self.tanim_id}) is not None:
                        say['yeni'] += 1
        return say

    def _cikis(self, k: dict, t: int, simdi: int, basarisiz: Iterable[str] = ()) -> Optional[dict]:
        """ACIK işlemin çıkışı: işlemin kendi ızgarasında (girişten ISLEM_ONCESI_SAAT önce başlar, t'de biter;
        veritabanından) strateji_lab.cikis_simule (k=1). Sinyal çıkışının serisi (SMA5/SMA20) aynı ızgarada
        strateji_lab.gostergeler ile: başlangıcı işleme bağlı olduğundan bir mumdaki karar turdan bağımsızdır (modül
        belgesi). Çıkış yoksa None (veri gelmiyorsa azami süreden sonra VERI_YOK kapanışı)."""
        s, tip = k['sembol'], sl.KURAL_CIKIS[k['bilesen']]
        e = hz.ISLEM_ONCESI_SAAT
        z = self.h.izgara_aralik(s, int(k['giris_ts']) - e * SAAT_MS, int(t) + SAAT_MS)
        seri = None
        if tip in sl.SINYAL_SERISI:
            seri = sl.gostergeler(z.O, z.H, z.L, z.C, z.V)[sl.SINYAL_SERISI[tip]].tolist()
        sim = sl.cikis_simule(tip, z.O.tolist(), z.H.tolist(), z.L.tolist(), z.C.tolist(), seri, e, 1.0)
        if sim is None:
            return self._veri_yok_kapanisi(k, tip, z, e, simdi, basarisiz)
        b, giris, cikis, ctip = sim
        return {'giris_fiyat': float(giris), 'cikis_ts': int(z.ts[b]) + SAAT_MS, 'cikis_fiyat': float(cikis),
                'cikis_tipi': ctip, 'getiri': float(sl.net_getiri(giris, cikis, 1.0)), 'sure_saat': float(b - e + 1)}

    def _veri_yok_kapanisi(self, k: dict, tip: str, z: hz.Izgara, e: int, simdi: int,
                           basarisiz: Iterable[str] = ()) -> Optional[dict]:
        """Çıkış için gereken mumlar gelmiyor: delist (parite 'YOK') paritede azami tutma süresi dolunca, işlemi
        durdurulan (BREAK: hata yok ama yeni mum yok) paritede ondan VERI_YOK_BEKLE_MS sonra (paritenin çekimi bu
        tur hatasızsa) son saklı kapanıştan VERI_YOK ile kapanır."""
        p = self.h.parite_oku(k['sembol']) or {}
        yok = p.get('durum') == 'YOK'
        sinir = int(k['giris_ts']) + sl.AZAMI_TUTMA[tip] * SAAT_MS + (0 if yok else VERI_YOK_BEKLE_MS)
        if simdi < sinir or (not yok and k['sembol'] in set(basarisiz)):
            return None
        dolu = np.flatnonzero(np.isfinite(z.C[e:]))
        if not len(dolu) or k.get('giris_fiyat') is None:
            return None
        b = e + int(dolu[-1])
        giris, cikis = float(k['giris_fiyat']), float(z.C[b]) * (1 - sl.SLIP_ZAMAN)
        LOG.warning(f"{k['sembol']} {'borsada yok' if yok else 'yeni mum vermiyor'}: {k['strateji']} kâğıt işlemi son "
                    f"kapanıştan kapatıldı (VERI_YOK)")
        return {'cikis_ts': int(z.ts[b]) + SAAT_MS, 'cikis_fiyat': cikis, 'cikis_tipi': 'VERI_YOK',
                'getiri': float(sl.net_getiri(giris, cikis, 1.0)), 'sure_saat': float(b - e + 1)}

    # --- günlük rapor ve döngü ------------------------------------------------------------------
    def gunluk_rapor(self, simdi: Optional[int] = None) -> Optional[bool]:
        """UTC günde bir kez, --rapor-saati-utc'den sonra (makine o saatte kapalıysa açılınca) raporu gönderir.
        Dönen: None (zamanı değil / bugün gönderildi), True (gönderildi), False (gönderilemedi ya da Telegram ayarı
        yok). Gönderilemezse sonraki turda yeniden denenir; ayar yoksa gün işaretlenir (rapor günlüğe yazılır).
        Gönderilen gün bellekte de tutulur (veritabanına yazılamasa da günde bir kez); veritabanı yazma hatası
        varsa raporun sonuna eklenir."""
        simdi = int(self.saat() if simdi is None else simdi)
        gun = simdi // GUN_MS
        if (simdi % GUN_MS) // SAAT_MS < self.rapor_saati or self._rapor_gunu == gun:
            return None
        try:
            if self.h.meta_oku('son_rapor_gunu') == str(gun):
                self._rapor_gunu = gun
                return None
        except sqlite3.Error:
            pass
        metin = rapor_metni(self.h, simdi)
        if self._son_db_hatasi is not None:
            ms, mesaj = self._son_db_hatasi
            metin += f"\nVERİTABANI YAZMA HATASI (bu süreçte son: {sl.vs._tarih(ms)} UTC): {mesaj}"
        if self.telegram is None or not self.telegram.hazir:
            LOG.info("Telegram ayarı yok: günlük rapor yalnız günlüğe yazıldı\n" + metin)
            sonuc = False
        elif self.telegram.gonder(metin):
            LOG.info("günlük rapor Telegram'a gönderildi")
            sonuc = True
        else:
            try:
                self.h.olay_yaz('TELEGRAM', 'günlük rapor gönderilemedi (sonraki turda yeniden denenecek)', ms=simdi)
            except sqlite3.Error as e:
                self._db_hatasi(e, simdi)
            return False
        self._rapor_gunu = gun
        try:
            self.h.meta_yaz('son_rapor_gunu', gun)
        except sqlite3.Error as e:
            self._db_hatasi(e, simdi)
            LOG.warning(f"rapor günü veritabanına yazılamadı ({type(e).__name__}: {e})")
        return sonuc

    def _yapilacak_mi(self, t: int, simdi: int) -> bool:
        """Karar saati t için tur gerekiyor mu. Kaydı yoksa ya da YARIM ise evet (geç de olsa: veri toplanır, yeni
        kâğıt işlem açılmaz). HATA (ya da kayıt yazılamadı) ise yalnız zamanındayken, bu süreçte en çok AZAMI_DENEME
        deneme ve denemeler arası YENIDEN_DENE_MS."""
        r = self.h.sorgu('SELECT durum FROM tarama WHERE ts = ?', (t,))
        durum = r[0]['durum'] if r else self._yazilamayan.get(t, {}).get('durum')
        deneme, son = self._deneme.get(t, (0, 0))
        if durum in ('TAMAM', 'KACIRILDI') or deneme >= AZAMI_DENEME:
            return False
        zamaninda = simdi - (t + SAAT_MS) <= ZAMANINDA_MS
        if not deneme:
            return durum != 'HATA' or zamaninda
        return zamaninda and simdi - son >= YENIDEN_DENE_MS

    def _uyu_kadar(self, hedef_ms: int) -> None:
        while not self.durdu:
            kalan = (int(hedef_ms) - int(self.saat())) / 1000.0
            if kalan <= 0:
                return
            self.uyku(min(UYKU_PARCA_SN, kalan))

    def calis(self) -> None:
        """Sonsuz döngü: her saat başından BEKLEME_MS sonra yeni kapanan karar mumu için tur, sonra günlük rapor
        kontrolü. Durdurma isteğinde (SIGTERM/SIGINT) sürmekte olan adım bitince döner. Döngünün kendi hatası
        (ör. veritabanı okunamadı) süreci öldürmez: günlüğe yazılır, uyarı gönderilir, YENIDEN_DENE_MS sonra sürer."""
        LOG.info(f"makine başladı (kâğıt mod: gerçek emir yok) | evren {self.evren}, min hacim "
                 f"{self.min_hacim / 1e6:g}M | ilk başlangıç {sl.vs._tarih(self.ilk)} UTC | rapor "
                 f"{self.rapor_saati:02d}:00 UTC | Telegram "
                 f"{'açık' if self.telegram and self.telegram.hazir else 'yok'} | tanım {self.tanim_id}")
        while not self.durdu:
            try:
                simdi = int(self.saat())
                t = son_karar_mumu(simdi)
                # yerel saat borsadan ileriyse (son ölçüm) saat başı beklemesine fark eklenir (en çok 5 dk)
                bekleme = BEKLEME_MS + min(max(self.saat_farki_ms or 0, 0), 5 * DAKIKA_MS)
                hazir = simdi >= t + SAAT_MS + bekleme
                if hazir and self._yapilacak_mi(t, simdi):
                    deneme = self._deneme.get(t, (0, 0))[0] + 1
                    self.tur_guvenli(simdi)
                    self._deneme = {t: (deneme, int(self.saat()))}     # yeniden deneme bitişten YENIDEN_DENE_MS sonra
                    if self.durdu:
                        break
                    try:
                        self.gunluk_rapor()
                    except Exception:  # noqa: BLE001 - rapor hatası döngüyü durdurmaz
                        LOG.exception("günlük rapor hazırlanamadı")
                    continue
                hedef = t + 2 * SAAT_MS + bekleme if hazir else t + SAAT_MS + bekleme
                deneme, son = self._deneme.get(t, (0, 0))
                if hazir and deneme and self._yapilacak_mi(t, son + YENIDEN_DENE_MS):
                    hedef = min(hedef, son + YENIDEN_DENE_MS)       # hatalı tur: zamanındayken yeniden dene
                self._uyu_kadar(hedef)
            except Exception as e:  # noqa: BLE001 - 24/7 süreç: döngü hatası süreci öldürmez
                simdi = int(self.saat())
                self._db_hatasi(e, simdi)
                mesaj = f"döngü hatası: {type(e).__name__}: {str(e)[:300]} ({YENIDEN_DENE_MS // 1000} sn sonra sürecek)"
                LOG.exception(mesaj)
                self._uyar('DONGU_HATASI', mesaj, simdi)
                self._uyu_kadar(simdi + YENIDEN_DENE_MS)
        LOG.info("makine durdu")


# ------------------------------------------------------------------------------------------
# Rapor ve durum metinleri (yalnız okuma: salt okunur hafızayla da çalışır)
# ------------------------------------------------------------------------------------------
def _yuzde(x, basamak: int = 1, isaret: bool = False) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return '-'
    return f"{x * 100:{'+' if isaret else ''}.{basamak}f}%"


def _mb(bayt: int) -> str:
    return f"{bayt / 1e6:.1f} MB"


def rapor_metni(h: hz.Hafiza, simdi: int, islem_usdt: float = ISLEM_USDT) -> str:
    """Günlük rapor (düz metin; modül belgesi). Yalnız Hafiza'nın rapor sorgularını kullanır."""
    simdi = int(simdi)
    saat0 = (simdi // SAAT_MS) * SAAT_MS
    bas24 = simdi - 24 * SAAT_MS
    ilk, ilk_islem = h.meta_oku('ilk_baslangic'), h.meta_oku('ilk_islem_ts')
    y = ["MAKİNE (kâğıt mod: gerçek emir yok)",
         f"{sl.vs._tarih(simdi)} UTC | ilk başlangıç: {sl.vs._tarih(int(ilk)) + ' UTC' if ilk else '-'}"
         + (f" | lab karşılığı --baslangic {sl.vs._tarih(int(ilk_islem) + SAAT_MS)} UTC" if ilk_islem else ''), '']
    tr = h.tarama_ozeti(saat0 - 24 * SAAT_MS, saat0)
    hata_olay = len(h.olaylar(bas_ms=bas24, tur='HATA'))
    diger = {k: v for k, v in tr.items() if k in ('YARIM',) and v}
    y.append("SON 24 SAAT")
    y.append(f"  Turlar: {tr['TAMAM']} tamam, {tr['KACIRILDI']} kaçırıldı, {tr['HATA']} hatalı"
             + ''.join(f", {v} {k.lower()}" for k, v in diger.items())
             + f" | evren: {tr['evren'] if tr['evren'] is not None else '-'} parite | hata kaydı: {hata_olay}"
             + f" | mum hatası: {tr['mum_hatasi']}")
    sinyal = h.sinyal_sayilari(saat0 - 24 * SAAT_MS, saat0)
    y.append("  Sinyaller: " + (', '.join(f"{k} {v}" for k, v in sinyal.items()) if sinyal else 'yok'))
    # kapanan: çıkış mumunun kapanışı son 24 saatte; açılan: giriş mumu son 24 turun karar saatlerinde (tur t, girişi
    # t olan BEKLIYOR işlemi ACIK yapar: pencere tur penceresiyle aynı, ilk turun açtıkları da sayılır)
    son = h.kagit_ozeti(islem_usdt, bas24, simdi + 1)
    acilis = h.kagit_ozeti(islem_usdt, saat0 - 24 * SAAT_MS, saat0)
    acilan = sum(o['acilan'] for o in acilis.values())
    kapanan = sum(o['kapali'] for o in son.values())
    toplam = sum(o['toplam_usdt'] for o in son.values())
    y.append(f"  Kâğıt işlem ({islem_usdt:g} USDT/işlem): açılan {acilan}, kapanan {kapanan}, kapananların sonucu "
             f"{toplam:+.2f} USDT")
    for ad, o in son.items():
        a = acilis.get(ad, {}).get('acilan', 0)
        if a or o['kapali']:
            y.append(f"    {ad:16s} açılan {a:3d}  kapanan {o['kapali']:3d}  {o['toplam_usdt']:+8.2f} USDT")
    y += ['', f"BAŞLANGIÇTAN BERİ (kâğıt, {islem_usdt:g} USDT/işlem, bileşik değil)",
          f"  {'strateji':16s} {'kapalı':>6s} {'kazan%':>7s} {'beklenti':>9s} {'toplam USDT':>11s} {'açık':>5s} "
          f"{'bekleyen':>8s}"]
    tum = h.kagit_ozeti(islem_usdt)
    if not tum:
        y.append("  henüz kâğıt işlem yok")
    for ad in [k for k in KAGIT_STRATEJILER if k in tum] + [k for k in tum if k not in KAGIT_STRATEJILER]:
        o = tum[ad]
        y.append(f"  {ad:16s} {o['kapali']:6d} {_yuzde(o['kazanma_orani']):>7s} {_yuzde(o['beklenti'], 3, True):>9s} "
                 f"{o['toplam_usdt']:+11.2f} {o['acik']:5d} {o['bekliyor']:8d}")
    veri_yok = h.sorgu("SELECT COUNT(*) AS n FROM kagit_islem WHERE cikis_tipi = 'VERI_YOK'")[0]['n']
    if veri_yok:
        y.append(f"  ({veri_yok} işlem delist nedeniyle son kapanıştan kapatıldı: VERI_YOK)")
    karne = h.dedektor_karnesi(simdi - KARNE_GUN * GUN_MS)
    tb = karne['taban']
    y += ['', f"DEDEKTÖR KARNESİ (son {KARNE_GUN} gün, etiketli satırlar; uçuş10 = 24 saatte +%10'a -%5'ten önce "
              "dokunma; kat = strateji uçuş10 / taban)",
          f"  taban: n {tb['n']}, uçuş10 {_yuzde(tb['ucus10_orani'], 2)}, ort. en yüksek "
          f"{_yuzde(tb['max_yukari_ort'], 2, True)}"]
    if karne['strateji']:
        y.append(f"  {'strateji':16s} {'n':>6s} {'uçuş10':>7s} {'ort.yükseliş':>12s} {'kat':>6s}")
        for ad, o in karne['strateji'].items():
            kat = f"{o['kat']:.2f}" if o['kat'] is not None else '-'
            y.append(f"  {ad:16s} {o['n']:6d} {_yuzde(o['ucus10_orani'], 2):>7s} "
                     f"{_yuzde(o['max_yukari_ort'], 2, True):>12s} {kat:>6s}")
    else:
        y.append("  etiketli sinyal yok")
    say = h.sayimlar()
    y += ['', f"VERİTABANI: {_mb(h.boyut())} | " + ', '.join(f"{k} {say[k]}" for k in
                                                              ('mum', 'aday', 'etiket', 'defter', 'kagit_islem',
                                                               'tarama', 'olay'))]
    son_hata = h.olaylar(tur='HATA', limit=1)
    y.append("SON HATA: " + (f"{sl.vs._tarih(son_hata[0]['ms'])} UTC: {son_hata[0]['mesaj'][:300]}" if son_hata
                             else 'yok'))
    return '\n'.join(y)


def durum_metni(h: hz.Hafiza, simdi: int, tur_sayisi: int = 12) -> str:
    """--durum: veritabanı, son turlar, açık/bekleyen kâğıt işlemler, strateji özeti, son hatalar (salt okunur)."""
    ilk, tanim = h.meta_oku('ilk_baslangic'), h.meta_oku('son_tanim_id')
    say = h.sayimlar()
    y = [f"MAKİNE DURUMU (kâğıt mod: gerçek emir yok) | {sl.vs._tarih(int(simdi))} UTC",
         f"Veritabanı: {h.yol} | {_mb(h.boyut())} | şema {h.sema_surumu} | tanım {tanim or '-'} | ilk başlangıç "
         f"{sl.vs._tarih(int(ilk)) + ' UTC' if ilk else '-'}",
         "Satırlar: " + ', '.join(f"{k} {v}" for k, v in say.items()), '', f"Son {tur_sayisi} tur:"]
    turlar = h.sorgu('SELECT * FROM tarama ORDER BY ts DESC LIMIT ?', (int(tur_sayisi),))
    if not turlar:
        y.append("  henüz tur yok")
    for r in reversed(turlar):
        sure = ''
        if r['baslangic_ms'] and r['bitis_ms']:
            sure = f" {(r['bitis_ms'] - r['baslangic_ms']) / 1000:.1f} sn"
        y.append(f"  {sl.vs._tarih(r['ts'])} {r['durum']:9s} evren {r['evren'] if r['evren'] is not None else '-':>3} "
                 f"mum hatası {r['mum_hatasi'] or 0:3d} sinyal {r['sinyal'] or 0:3d} açılan {r['acilan'] or 0:2d} "
                 f"kapanan {r['kapanan'] or 0:2d} etiket {r['etiketlenen'] or 0:4d} defter {r['defter'] or 0:2d}"
                 + sure)
    acik = h.kagit_islemler(('BEKLIYOR', 'ACIK'))
    y += ['', f"Açık / bekleyen kâğıt işlemler ({len(acik)}):"]
    for k in acik:
        son = h.sorgu('SELECT c FROM mum WHERE sembol = ? ORDER BY ts DESC LIMIT 1', (k['sembol'],))
        anlik = ''
        if k['giris_fiyat'] and son and son[0]['c']:
            anlik = f" | şimdi {_yuzde(son[0]['c'] / k['giris_fiyat'] - 1, 2, True)} (maliyetsiz)"
        bilesen = f" ({k['bilesen']})" if k['bilesen'] != k['strateji'] else ''
        y.append(f"  {k['strateji']}{bilesen} {k['sembol']} {k['durum']} giriş {sl.vs._tarih(k['giris_ts'])} UTC"
                 + (f" @ {k['giris_fiyat']:.8g}" if k['giris_fiyat'] else '') + anlik)
    if not acik:
        y.append("  yok")
    rapor = rapor_metni(h, simdi).split('\n')
    i = next(j for j, x in enumerate(rapor) if x.startswith('BAŞLANGIÇTAN BERİ'))
    j = next(j for j, x in enumerate(rapor) if x.startswith('DEDEKTÖR'))
    y += [''] + rapor[i:j - 1]
    hatalar = h.olaylar(tur='HATA', limit=5)
    y += ['', f"Son hatalar ({len(hatalar)}):"] + [f"  {sl.vs._tarih(o['ms'])} UTC {o['mesaj'][:200]}" for o in hatalar]
    if not hatalar:
        y.append("  yok")
    return '\n'.join(y)


# ------------------------------------------------------------------------------------------
# Komut satırı
# ------------------------------------------------------------------------------------------
def _pozitif_tam(x):
    v = int(x)
    if v <= 0:
        raise argparse.ArgumentTypeError(f"sıfırdan büyük olmalı: {x}")
    return v


def _saat(x):
    v = int(x)
    if not 0 <= v <= 23:
        raise argparse.ArgumentTypeError(f"0-23 arası olmalı: {x}")
    return v


def arguman_ayristirici():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    kip = ap.add_mutually_exclusive_group()
    kip.add_argument('--tek-tur', action='store_true', help='bir tur çalış ve çık')
    kip.add_argument('--durum', action='store_true', help='veritabanı, son turlar, açık kâğıt işlemler (salt okunur)')
    kip.add_argument('--rapor', action='store_true', help='günlük raporu yazdır (salt okunur)')
    ap.add_argument('--telegram', action='store_true', help='--rapor ile: raporu Telegram\'a da gönder')
    ap.add_argument('--db', default=os.path.join(KOK, 'makine.db'), help='veritabanı dosyası')
    ap.add_argument('--evren', type=_pozitif_tam, default=150, help='en hacimli N USDT paritesi (+ BTC)')
    ap.add_argument('--min-hacim', type=float, default=5e6, help='evren: 24 saatlik hacim alt sınırı (USDT)')
    ap.add_argument('--defter-max', type=int, default=hz.DEFTER_AZAMI, help='turda en çok emir defteri sayısı')
    ap.add_argument('--rapor-saati-utc', type=_saat, default=6, help='günlük rapor saati (UTC; 6 = 09:00 TSİ)')
    ap.add_argument('--aday-gun', type=_pozitif_tam, default=400,
                    help='aday/etiket/defter saklama (gün); mum bu + 70 gün, olay 90 gün')
    return ap


def _bozuk_db_mesaji(yol: str, e: BaseException) -> str:
    return (f"veritabanı bozuk ya da açılamıyor: {yol} ({type(e).__name__}: {str(e)[:200]}). Yedekten dönün ya da "
            f"dosyayı (ve -wal/-shm) başka yere taşıyıp makineyi yeniden başlatın (yeni veritabanı kurulur).")


def main(argv=None) -> int:
    a = arguman_ayristirici().parse_args(argv)
    token, chat_id = telegram_ayarlari()
    if a.durum or a.rapor:
        gunluk_kur(gizliler=[token])
        try:
            h = hz.Hafiza(a.db, salt_okunur=True)
        except (FileNotFoundError, hz.SemaHatasi) as e:
            print(f"{e}")
            return 2
        except sqlite3.DatabaseError as e:
            print(_bozuk_db_mesaji(a.db, e))
            return 2
        with h:
            simdi = int(time.time() * 1000)
            try:
                metin = durum_metni(h, simdi) if a.durum else rapor_metni(h, simdi)
            except sqlite3.DatabaseError as e:
                print(_bozuk_db_mesaji(a.db, e))
                return 2
            print(metin)
            if a.durum:
                return 0
            if a.telegram:
                tg = Telegram(token, chat_id)
                if not tg.hazir:
                    print("Telegram ayarı yok (TELEGRAM_TOKEN / TELEGRAM_CHAT_ID): gönderilmedi")
                    return 1
                return 0 if tg.gonder(metin) else 1
        return 0
    kilit = hz.TekKopya(a.db + '.lock')
    try:
        kilit.al()
    except hz.ZatenCalisiyor as e:
        print(str(e))
        return 3
    h = None
    try:
        gunluk_kur(os.path.join(os.path.dirname(os.path.abspath(a.db)), 'makine.log'), gizliler=[token])
        try:
            h = hz.Hafiza(a.db)
        except hz.SemaHatasi as e:
            LOG.error(str(e))
            return 2
        except sqlite3.DatabaseError as e:
            mesaj = _bozuk_db_mesaji(a.db, e)
            LOG.error(mesaj)
            Telegram(token, chat_id).gonder(f"MAKİNE UYARISI (VERITABANI)\n{mesaj}")     # veritabanından bağımsız
            return 2
        m = Makine(borsa_kur(), h, evren=a.evren, min_hacim=a.min_hacim, defter_max=a.defter_max,
                   rapor_saati_utc=a.rapor_saati_utc, aday_gun=a.aday_gun, telegram=Telegram(token, chat_id))
        for sinyal_adi in ('SIGTERM', 'SIGINT'):
            signal.signal(getattr(signal, sinyal_adi), m.durdur)
        if a.tek_tur:
            kayit = m.tur_guvenli()
            return 0 if kayit['durum'] == 'TAMAM' else 1
        m.calis()
        return 0
    finally:
        if h is not None:
            h.kapat()
        kilit.birak()


if __name__ == '__main__':
    sys.exit(main())
