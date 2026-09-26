# -*- coding: utf-8 -*-
"""Birleşik etiketli sinyal şeması (core + shadow + backfill tek tabloda).

Her satır = bir sinyal. Kaynak ne olursa olsun aynı feature kolonları ve aynı
etiket fonksiyonunun (sniper.etiketleme) çıktısı bulunur. Trainer yalnızca bu
şemayı okur; hangi kaynaktan geldiği `Kaynak` kolonundadır ve modele feature
olarak VERİLMEZ (sadece teşhis / ağırlıklandırma için).
"""
import csv
import math
import os
from typing import Dict, Iterable, List, Optional, Set

import pandas as pd

from .csv_kayit import satirlari_ekle
from .etiketleme import ETIKET_SURUMU
from .ozellikler import METRIK_KOLON

TEMEL_KOLONLAR = ['Anahtar', 'Kaynak', 'Ts', 'Sembol', 'Sebep', 'Sinyal', 'Fiyat', 'Kasa_Tipi',
                  'BTC_OK', 'Rejim']
FEATURE_KOLONLARI = list(dict.fromkeys(METRIK_KOLON.values()))   # kanonik feature kolonları
CORE_KOLONLARI = ['Pozisyon_Id', 'Gercek_Getiri', 'Gercek_Cikis']
ETIKET_KOLONLARI = ['Etiket_Surumu', 'Etiket_Sonuc', 'Etiket_Getiri', 'Etiket_Max', 'Etiket_Min',
                    'Etiket_Mum', 'Etiket_SL', 'Etiket_TP', 'Etiket_Giris']
TUM_KOLONLAR = TEMEL_KOLONLAR + FEATURE_KOLONLARI + CORE_KOLONLARI + ETIKET_KOLONLARI

VERI_YOK = 'VERI_YOK'


def etiket_alanlari(sonuc: Optional[dict]) -> dict:
    if not sonuc:
        return {'Etiket_Surumu': ETIKET_SURUMU, 'Etiket_Sonuc': VERI_YOK}
    return {'Etiket_Surumu': sonuc['surum'], 'Etiket_Sonuc': sonuc['sonuc'],
            'Etiket_Getiri': round(sonuc['getiri'], 6), 'Etiket_Max': round(sonuc['max_net'], 6),
            'Etiket_Min': round(sonuc['min_net'], 6), 'Etiket_Mum': sonuc['mum'],
            'Etiket_SL': round(sonuc['sl'], 6), 'Etiket_TP': round(sonuc['tp'], 6),
            'Etiket_Giris': sonuc['giris']}


def etiketli_anahtarlar(yol: str, surum: str = ETIKET_SURUMU) -> Set[str]:
    """Bu etiket sürümüyle zaten etiketlenmiş (veya kalıcı VERI_YOK) anahtarlar."""
    if not os.path.exists(yol):
        return set()
    sonuc = set()
    with open(yol, encoding='utf-8-sig', newline='') as f:
        for r in csv.DictReader(f):
            if r.get('Etiket_Surumu') == surum and r.get('Anahtar'):
                sonuc.add(r['Anahtar'])
    return sonuc


def yaz(yol: str, satirlar: List[dict], uyari=None) -> None:
    satirlari_ekle(yol, satirlar, TUM_KOLONLAR, uyari)


def oku(yollar: Iterable[str], surum: str = ETIKET_SURUMU) -> pd.DataFrame:
    """Birden çok etiket dosyasını okur; her Anahtar için bu sürümün SON satırını tutar.
    Başlıkla aynı uzunlukta olmayan (kaymış) satırlar atlanır ve sayısı raporlanır."""
    parcalar = []
    for yol in yollar:
        if not os.path.exists(yol):
            continue
        with open(yol, encoding='utf-8-sig', newline='') as f:
            satirlar = list(csv.reader(f))
        if not satirlar:
            continue
        baslik, govde = satirlar[0], satirlar[1:]
        uygun = [r for r in govde if len(r) == len(baslik)]
        if len(uygun) != len(govde):
            print(f"⚠️ {os.path.basename(yol)}: {len(govde) - len(uygun)} kaymış satır atlandı")
        parcalar.append(pd.DataFrame(uygun, columns=baslik))
    if not parcalar:
        return pd.DataFrame(columns=TUM_KOLONLAR)
    df = pd.concat(parcalar, ignore_index=True)
    if 'Etiket_Surumu' not in df.columns or 'Anahtar' not in df.columns:
        return pd.DataFrame(columns=TUM_KOLONLAR)
    df = df[df['Etiket_Surumu'] == surum]
    df = df.drop_duplicates('Anahtar', keep='last').reset_index(drop=True)
    for k in ['Ts', 'Fiyat', 'Etiket_Getiri', 'Etiket_Max', 'Etiket_Min', 'Etiket_Mum', 'Etiket_SL',
              'Etiket_TP', 'Etiket_Giris', 'Gercek_Getiri']:
        if k in df.columns:
            df[k] = pd.to_numeric(df[k], errors='coerce')
    return df


def is_whale_tahmini(satir: dict) -> bool:
    """Balina bayrağı: core'da Kasa_Tipi'nden, sinyallerde V18.3 kuralından (hacim>3.5x ve RSI<65)."""
    kasa = str(satir.get('Kasa_Tipi') or '')
    if kasa:
        return kasa.upper().startswith('BAL')
    try:
        return float(satir.get('Giris_Vol_Oran')) > 3.5 and float(satir.get('Giris_RSI')) < 65
    except (TypeError, ValueError):
        return False


def sayi(x, varsayilan=float('nan')) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else varsayilan
    except (TypeError, ValueError):
        return varsayilan


def feature_alanlari(kaynak: Dict[str, object]) -> dict:
    """Kaynak satırdan (CSV DictReader satırı) kanonik feature kolonlarını çeker."""
    return {k: kaynak.get(k) for k in FEATURE_KOLONLARI if kaynak.get(k) not in (None, '')}
