# -*- coding: utf-8 -*-
"""Güvenli CSV kayıt katmanı.

Eski ai_bot.py::_append_csv şu iki yolla veri yok ediyordu:
  1. Başlıksız (eski V1 formatı) dosyada ilk veri satırını başlık sanıp
     `pd.read_csv(...).reindex(columns)` ile TÜM satırları boş (',,,,') satıra
     çeviriyordu. Logda sadece "şema güncellendi (13→13 kolon)" görünüyordu.
  2. Başlık-satır kaymasında dosyayı `.bozuk_<ts>` adıyla kenara alıyordu;
     trainer bu dosyaları hiç okumadığı için veri pipeline'dan sessizce düşüyordu.

Bu modülün garantileri:
  - Mevcut bir veri hücresi ASLA silinmez veya kaydırılmaz.
  - Şema değişikliği yalnızca yeni kolonları SONA ekler; dosya atomik yeniden
    yazılır (geçici dosya + fsync + os.replace). Yarıda kalan yazım dosyayı bozmaz.
  - Başlıksız eski dosyada tüm satırlar beklenen kolon sayısındaysa başlık eklenir
    (veri yerinde korunur); değilse dosya AYNEN arşivlenir ve `uyari` callback'i
    ile yüksek sesle bildirilir.
  - Satır uzunlukları başlıkla uyuşmayan (zaten kaymış) dosyaya yeni satır
    eklenmez; dosya aynen arşivlenir ve bildirilir.
  - Aynı süreç içindeki thread'ler dosya başına kilitle sıralanır
    (asyncio.to_thread ile event loop dışında çağrılabilir).
"""
import csv
import math
import os
import tempfile
import threading
import time
from typing import Callable, Iterable, List, Optional, Sequence

_KILITLER = {}
_KILITLER_KILIDI = threading.Lock()


def _kilit(yol: str) -> threading.Lock:
    with _KILITLER_KILIDI:
        return _KILITLER.setdefault(os.path.abspath(yol), threading.Lock())


def hucre(deger) -> str:
    """Python/numpy değerini CSV hücresine çevirir (None/NaN -> boş)."""
    if deger is None:
        return ''
    if isinstance(deger, float) and math.isnan(deger):
        return ''
    if isinstance(deger, bool):
        return str(int(deger))
    try:  # numpy skalerleri: str(np.float64(0.1)) == '0.1'
        import numpy as np
        if isinstance(deger, np.generic):
            if isinstance(deger, np.floating) and np.isnan(deger):
                return ''
            if isinstance(deger, np.bool_):
                return str(int(deger))
    except ImportError:  # pragma: no cover
        pass
    return str(deger)


def atomik_yaz(yol: str, baslik: Sequence[str], satirlar: Iterable[Sequence[str]]) -> None:
    """Dosyayı geçici dosyaya yazıp fsync'ler ve os.replace ile yerine koyar.
    Okuyucular ya eski ya yeni dosyayı görür, yarım dosya asla görmez."""
    dizin = os.path.dirname(os.path.abspath(yol))
    fd, gecici = tempfile.mkstemp(prefix='.' + os.path.basename(yol) + '.', suffix='.tmp', dir=dizin)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
            w = csv.writer(f, lineterminator='\n')
            w.writerow(list(baslik))
            w.writerows(satirlar)
            f.flush()
            os.fsync(f.fileno())
        os.replace(gecici, yol)
    except BaseException:
        try:
            os.unlink(gecici)
        except OSError:
            pass
        raise


def atomik_metin_yaz(yol: str, metin: str) -> None:
    """Küçük durum dosyaları (labeler state vb.) için atomik yazım."""
    dizin = os.path.dirname(os.path.abspath(yol))
    fd, gecici = tempfile.mkstemp(prefix='.' + os.path.basename(yol) + '.', suffix='.tmp', dir=dizin)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(metin)
            f.flush()
            os.fsync(f.fileno())
        os.replace(gecici, yol)
    except BaseException:
        try:
            os.unlink(gecici)
        except OSError:
            pass
        raise


def _arsivle(yol: str, etiket: str) -> str:
    hedef = f"{yol}.{etiket}_{int(time.time())}"
    os.replace(yol, hedef)
    return hedef


def _satirlari_oku(yol: str) -> List[List[str]]:
    with open(yol, encoding='utf-8-sig', newline='') as f:
        return list(csv.reader(f))


def baslik_gibi(ilk_satir: Sequence[str], bilinen_kolonlar: Sequence[str]) -> bool:
    """İlk satır gerçekten bir başlık mı? Eski V1 dosyaları başlıksızdı ve ilk
    satırları veriydi ('2026-06-01 10:00:00,ABC/USDT,...')."""
    if not ilk_satir:
        return False
    bilinen = set(bilinen_kolonlar)
    eslesen = sum(1 for k in ilk_satir if k in bilinen)
    return ilk_satir[0] in bilinen and eslesen * 2 >= len(ilk_satir)


def _sona_ekle(yol: str, degerler: Sequence[str]) -> None:
    # Önceki bir çökme son satırı '\n' olmadan bıraktıysa yeni satır ona yapışmasın
    with open(yol, 'rb') as f:
        f.seek(0, os.SEEK_END)
        son_bayt = b'\n'
        if f.tell() > 0:
            f.seek(-1, os.SEEK_END)
            son_bayt = f.read(1)
    with open(yol, 'a', encoding='utf-8', newline='') as f:
        if son_bayt != b'\n':
            f.write('\n')
        csv.writer(f, lineterminator='\n').writerow(list(degerler))
        f.flush()
        os.fsync(f.fileno())


def satir_ekle(yol: str, satir: dict, kolonlar: Sequence[str],
               uyari: Optional[Callable[[str], None]] = None) -> None:
    """`satir` sözlüğünü `yol` CSV'sine ekler. Açıklama için modül docstring'ine bakın.

    `uyari(mesaj)`: veri korunması için olağandışı bir işlem yapıldığında çağrılır
    (arşivleme, başlık ekleme, şema genişletme). Bot bunu Telegram'a iletir."""
    uyari = uyari or (lambda _m: None)
    kolonlar = list(kolonlar)
    ad = os.path.basename(yol)
    with _kilit(yol):
        if not os.path.isfile(yol) or os.path.getsize(yol) == 0:
            atomik_yaz(yol, kolonlar, [[hucre(satir.get(k)) for k in kolonlar]])
            return

        with open(yol, encoding='utf-8-sig', newline='') as f:
            baslik = next(csv.reader(f), [])

        if baslik != kolonlar:
            if not baslik_gibi(baslik, kolonlar):
                # Başlıksız eski format: veriyi ASLA yeniden yorumlama
                tum = _satirlari_oku(yol)
                if tum and all(len(r) == len(kolonlar) for r in tum):
                    atomik_yaz(yol, kolonlar, tum)
                    uyari(f"🧬 {ad}: başlıksız eski dosyaya başlık eklendi, {len(tum)} satır yerinde korundu.")
                else:
                    arsiv = _arsivle(yol, 'legacy')
                    uyari(f"🚨 {ad}: başlıksız ve kolon sayısı tutarsız dosya AYNEN arşivlendi -> "
                          f"{os.path.basename(arsiv)} ({len(tum)} satır). tools/csv_onar.py ile onarın.")
                    atomik_yaz(yol, kolonlar, [])
                baslik = kolonlar
            else:
                tum = _satirlari_oku(yol)
                govde = tum[1:]
                kayik = sum(1 for r in govde if len(r) != len(baslik))
                if kayik:
                    arsiv = _arsivle(yol, 'bozuk')
                    uyari(f"🚨 {ad}: {kayik} satır başlıkla aynı uzunlukta değil (kolon kayması). Dosya AYNEN "
                          f"arşivlendi -> {os.path.basename(arsiv)}; tools/csv_onar.py ile onarın.")
                    atomik_yaz(yol, kolonlar, [])
                    baslik = kolonlar
                else:
                    eksik = [k for k in kolonlar if k not in baslik]
                    if eksik:
                        yeni_baslik = baslik + eksik
                        atomik_yaz(yol, yeni_baslik, [r + [''] * len(eksik) for r in govde])
                        uyari(f"🧬 {ad}: şema genişletildi (+{', '.join(eksik)}), {len(govde)} satır korundu.")
                        baslik = yeni_baslik
                    # eksik yoksa: dosyada kodun artık yazmadığı kolonlar var; bunlar boş kalır, silinmez

        _sona_ekle(yol, [hucre(satir.get(k)) for k in baslik])
