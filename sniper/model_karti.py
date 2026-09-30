# -*- coding: utf-8 -*-
"""Model kartı + çalışma anında yeniden yüklenebilir model yuvası.

V18.3'teki kırılganlıklar:
  - Bot feature listesini ve sırasını ELLE kodluyordu. Trainer yeni feature'larla
    model kaydederse predict_proba her sinyalde hata verir -> ai_score None ->
    bloklama modunda TÜM sinyaller sessizce reddedilirdi.
  - Model sadece açılışta yükleniyordu; gece eğitilen model bot yeniden
    başlatılana kadar devreye girmiyordu.
  - Eşik (0.65 / 0.45) sabitti; scale_pos_weight ile eğitilen bir modelin olasılık
    ölçeği kaydığında bu eşikler anlamsızlaşır.

Çözüm:
  - Feature listesi modelin KENDİ feature_names alanından okunur; vektör
    sniper.ozellikler ile kanonik adlardan kurulur.
  - Trainer model yanına `<model>.kart.json` yazar: eşik, yön, metrikler ve modelin
    SHA-256'sı. Kart-model hash'i uyuşmazsa (yazım sürüyor / elle değiştirildi) yeni
    model devreye alınmaz, eskisi çalışmaya devam eder.
  - Kartsız (legacy) model: botun sabit eşikleriyle çalışır (V18.3 davranışı).
"""
import datetime
import hashlib
import json
import os
from typing import Optional

import pandas as pd

from .csv_kayit import atomik_metin_yaz
from .ozellikler import kanonik_satir, ozellik_matrisi

KART_SEMA_SURUMU = 1


def kart_yolu(model_yolu: str) -> str:
    kok, _ = os.path.splitext(model_yolu)
    return kok + '.kart.json'


def dosya_hash(yol: str) -> str:
    h = hashlib.sha256()
    with open(yol, 'rb') as f:
        for parca in iter(lambda: f.read(1 << 20), b''):
            h.update(parca)
    return h.hexdigest()


def kart_oku(model_yolu: str) -> Optional[dict]:
    yol = kart_yolu(model_yolu)
    if not os.path.exists(yol):
        return None
    with open(yol, encoding='utf-8') as f:
        kart = json.load(f)
    if kart.get('kart_sema_surumu') != KART_SEMA_SURUMU:
        raise ValueError(f"desteklenmeyen kart şema sürümü: {kart.get('kart_sema_surumu')}")
    return kart


def json_uyumlu(o):
    """numpy skalerlerini (np.bool_, np.int64, np.float32...) JSON'a uygun Python tiplerine çevirir."""
    try:
        import numpy as np
        if isinstance(o, np.generic):
            return o.item()
        if isinstance(o, np.ndarray):
            return o.tolist()
    except ImportError:  # pragma: no cover
        pass
    return str(o)


def modeli_kartla_kaydet(model, model_yolu: str, kart: dict) -> dict:
    """XGBoost modelini ve kartını atomik kaydeder (önce model, sonra hash'li kart)."""
    gecici = model_yolu + '.tmp.json'          # xgboost formatı uzantıdan anlar
    model.save_model(gecici)
    os.replace(gecici, model_yolu)
    kart = dict(kart)
    kart['kart_sema_surumu'] = KART_SEMA_SURUMU
    kart['model_dosyasi'] = os.path.basename(model_yolu)
    kart['model_sha256'] = dosya_hash(model_yolu)
    kart.setdefault('olusturma', datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'))
    atomik_metin_yaz(kart_yolu(model_yolu), json.dumps(kart, ensure_ascii=False, indent=2, default=json_uyumlu))
    return kart


class ModelYuvasi:
    """Bir modeli (core veya filtre) tutar; dosya değişince yeniden yükler.

    yon='min': skor < esik ise blokla (core: iyi işlem olasılığı)
    yon='max': skor > esik ise blokla (filtre: kötü işlem olasılığı)"""

    def __init__(self, model_yolu: str, rol: str, varsayilan_esik: float, yon: str):
        assert yon in ('min', 'max')
        self.model_yolu, self.rol, self.varsayilan_esik, self.yon = model_yolu, rol, varsayilan_esik, yon
        self.model = None
        self.kart: Optional[dict] = None
        self.featurelar = None
        self.esik = varsayilan_esik
        self.surum = None
        self._imza = None

    @property
    def yuklu(self) -> bool:
        return self.model is not None

    def _imza_al(self):
        def mt(p):
            try:
                return os.stat(p).st_mtime_ns
            except FileNotFoundError:
                return None
        return mt(self.model_yolu), mt(kart_yolu(self.model_yolu))

    def yenile(self) -> Optional[str]:
        """Dosyalar değiştiyse yükler. Değişiklik/hata varsa bildirim metni döner.
        Yükleme başarısız olursa ÖNCEKİ model çalışmaya devam eder."""
        imza = self._imza_al()
        if imza == self._imza:
            return None
        ad = os.path.basename(self.model_yolu)
        if imza[0] is None:
            self._imza = imza
            if self.model is not None:
                self.model, self.kart, self.featurelar, self.surum = None, None, None, None
                self.esik = self.varsayilan_esik
                return f"ℹ️ {ad} kaldırıldı: {self.rol} modeli devre dışı."
            return None
        try:
            import xgboost as xgb
            kart = kart_oku(self.model_yolu)
            if kart is not None and kart.get('model_sha256') != dosya_hash(self.model_yolu):
                return None  # model/kart yazımı sürüyor olabilir; imza güncellenmez, sonraki turda tekrar denenir
            model = xgb.XGBClassifier()
            model.load_model(self.model_yolu)
            featurelar = list(model.get_booster().feature_names or [])
            if not featurelar:
                raise ValueError("modelde feature_names yok (DataFrame ile eğitilmemiş)")
            if kart is not None and kart.get('ozellikler') and list(kart['ozellikler']) != featurelar:
                raise ValueError("kart feature listesi modelinkiyle uyuşmuyor")
            ozellik_matrisi(pd.DataFrame([{}]), featurelar)   # yasak feature kontrolü
        except Exception as e:  # noqa: BLE001 - her hata eski modeli korumalı
            self._imza = imza
            return f"⚠️ {ad} yüklenemedi, {'önceki model korunuyor' if self.model is not None else 'model devre dışı'}: {e}"
        self.model, self.kart, self.featurelar, self._imza = model, kart, featurelar, imza
        self.esik = float(kart['esik']) if kart and kart.get('esik') is not None else self.varsayilan_esik
        # Kartsız modelde sürüm dosya İÇERİĞİNDEN: aynı model iki sunucuda aynı adı taşır (eskiden değişiklik
        # zamanıydı; kopyalanan dosya her makinede farklı 'legacy-<zaman>' gösteriyordu)
        self.surum = (kart or {}).get('surum') or f"legacy-{dosya_hash(self.model_yolu)[:8]}"
        kaynak = 'kartlı' if kart else 'kartsız/legacy'
        return (f"🧠 {self.rol} modeli yüklendi ({kaynak}): {len(featurelar)} feature, "
                f"eşik {self.esik:.3f} ({'<' if self.yon == 'min' else '>'} ise blok)")

    def skor(self, metrics: dict) -> Optional[float]:
        if self.model is None:
            return None
        X = ozellik_matrisi(pd.DataFrame([kanonik_satir(metrics)]), self.featurelar)
        return float(self.model.predict_proba(X)[0][1])

    def blokla_mi(self, skor: Optional[float]) -> bool:
        if skor is None:
            return False
        return skor < self.esik if self.yon == 'min' else skor > self.esik
