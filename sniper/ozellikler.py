# -*- coding: utf-8 -*-
"""Sinyal kuralı + feature hesaplaması: TEK KAYNAK.

Canlı bot (ai_bot.analyze_market), backfill_sinyaller.py ve ai_trainer.py aynı
fonksiyonları kullanır. Eğitimde görülen feature ile canlıda hesaplanan feature
arasında fark (train/serve skew) oluşamaz.

Mum konvansiyonu: `df15`'in SON satırı "sinyal mumu"dur.
  - Canlı mod (V18.3 davranışı): son satır henüz KAPANMAMIŞ mumdur.
  - Kapalı mum modu (backfill / SINYAL_KAPALI_MUM=True): çağıran canlı mumu
    atıp son kapanmış mumu verir.
"""
import datetime
import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
import pandas_ta as ta

OHLCV_KOLONLARI = ['ts', 'o', 'h', 'l', 'c', 'v']
MUM_15M_MS = 15 * 60 * 1000

# metrics anahtarı -> CSV / model kolonu (kanonik feature adı)
V1_METRIK_KOLON = {'rsi': 'Giris_RSI', 'vol_ratio': 'Giris_Vol_Oran', 'atr_pct': 'Giris_ATR_Pct'}
# Sıra önemli değil (CSV'ler başlıkla okunur) ama yeni anahtarlar SONA eklenmeli
V2_FEATURE_MAP = {
    'pump_3s': 'Pump_3s', 'pump_6s': 'Pump_6s', 'zirve_uzaklik': 'Zirve_Uzaklik',
    'ema1h_uzaklik': 'EMA1h_Uzaklik', 'ema15m_uzaklik': 'EMA15m_Uzaklik',
    'btc_1h_degisim': 'BTC_1h_Degisim', 'btc_ema_uzaklik': 'BTC_EMA_Uzaklik',
    'saat': 'Saat', 'gun': 'Gun', 'stop_sayisi': 'Stop_Sayisi',
    'sym_adx': 'Sym_ADX', 'btc_adx': 'BTC_ADX',
    'kapali_mum_onay': 'Kapali_Mum_Onay', 'ob_oran': 'OB_Oran',
    'ai_score': 'AI_Skor', 'filter_score': 'Filtre_Skor',
    # V18.4 ile eklenenler (sadece kayıt; mevcut modeller kullanmaz)
    'sinyal_ts': 'Sinyal_Ts', 'mum_ilerleme': 'Mum_Ilerleme', 'spread_bps': 'Spread_Bps',
    'ob_oran_yakin': 'OB_Oran_Yakin', 'genislik': 'Piyasa_Genislik', 'kapali_mum_modu': 'Kapali_Mum_Modu',
    'ai_model_surum': 'AI_Model_Surum',
}
METRIK_KOLON = {**V1_METRIK_KOLON, **V2_FEATURE_MAP}

# Model feature'ı olarak ASLA kullanılmayacak kolonlar: eski modelin skoru yeni modele
# girerse model kendi geçmişini öğrenir (sızıntı); zaman damgası/mod bayrağı kimlik bilgisidir.
YASAK_FEATURELAR = {'AI_Skor', 'Filtre_Skor', 'AI_Model_Surum', 'Sinyal_Ts', 'Kapali_Mum_Modu'}
SINYAL_KODLARI = {'MSB': 1, 'Engulf': 2}


@dataclass(frozen=True)
class SinyalAyarlari:
    rsi_esik: float = 55.0
    hacim_esik: float = 2.5
    max_atr_pct: float = 0.03       # ai_bot.MAX_ATR_PCT
    balina_hacim: float = 3.5
    balina_rsi_ust: float = 65.0
    min_mum: int = 30


def ohlcv_df(ohlcv) -> pd.DataFrame:
    df = pd.DataFrame(ohlcv, columns=OHLCV_KOLONLARI)
    for k in OHLCV_KOLONLARI:
        df[k] = pd.to_numeric(df[k], errors='coerce')
    return df


def _son(seri) -> float:
    if seri is None:
        return float('nan')
    try:
        v = float(seri.iloc[-1])
    except (IndexError, TypeError, ValueError):
        return float('nan')
    return v


def ema_son(kapanislar: pd.Series, uzunluk: int) -> float:
    return _son(ta.ema(kapanislar, length=uzunluk))


def bir_saati_hizala(df1h: pd.DataFrame, sinyal_kapanis_ms: float, fiyat: float) -> pd.DataFrame:
    """1h serisini sinyal anına hizalar: o anda açık olan 1h mumunun 'kapanışı' = sinyal
    fiyatı (canlı botta canlı 1h mumunun kapanışı zaten anlık fiyattır). Backfill'de
    gelecekteki 1h kapanışını kullanmayı (look-ahead) engeller."""
    d1 = df1h[df1h['ts'] < sinyal_kapanis_ms].reset_index(drop=True).copy()
    if not d1.empty:
        d1.loc[d1.index[-1], 'c'] = float(fiyat)
    return d1


def kapali_mum_hizala(df15: pd.DataFrame, df1h: pd.DataFrame):
    """Canlı bot, kapalı mum modu: canlı 15m mumu atar, 1h'yi son kapanmış 15m muma hizalar."""
    d15 = df15.iloc[:-1].reset_index(drop=True)
    if d15.empty:
        return d15, df1h.iloc[0:0]
    kapanis_ms = float(d15['ts'].iloc[-1]) + MUM_15M_MS
    return d15, bir_saati_hizala(df1h, kapanis_ms, float(d15['c'].iloc[-1]))


def sinyal_degerlendir(df15: pd.DataFrame, ema20_1h: float,
                       ayar: SinyalAyarlari = SinyalAyarlari()) -> Optional[dict]:
    """V18.3 analyze_market kural filtresi. Geçmezse None, geçerse ara değerler."""
    if len(df15) < ayar.min_mum:
        return None
    # V18.3: kısa geçmişte ta.ema None döner -> .iloc hatası -> "Analiz Hatası" + None.
    # Aynı sonuç, log gürültüsü olmadan:
    if not math.isfinite(ema20_1h):
        return None
    rsi_val = _son(ta.rsi(df15['c'], length=14))
    atr_val = _son(ta.atr(df15['h'], df15['l'], df15['c'], length=14))
    price = float(df15['c'].iloc[-1])

    if price < ema20_1h:
        return None
    if (atr_val / price) > ayar.max_atr_pct:
        return None
    msb = bool(price > df15['h'].iloc[-10:-2].max())
    eng = bool(df15['c'].iloc[-2] < df15['o'].iloc[-2] and df15['c'].iloc[-1] > df15['o'].iloc[-1]
               and df15['o'].iloc[-1] < df15['c'].iloc[-2])
    vol = float(df15['v'].iloc[-1])
    av_v = _son(df15['v'].rolling(14).mean())
    vol_ratio = vol / av_v if av_v > 0 else 0

    if not (rsi_val > ayar.rsi_esik and vol_ratio > ayar.hacim_esik and (msb or eng)):
        return None
    return {'price': price, 'rsi': rsi_val, 'atr': atr_val, 'msb': msb, 'eng': eng,
            'vol_ratio': float(vol_ratio), 'av_v': av_v, 'ema20_1h': float(ema20_1h),
            'is_whale': bool(vol_ratio > ayar.balina_hacim and rsi_val < ayar.balina_rsi_ust)}


def ob_ozellikleri(order_book: Optional[dict]) -> dict:
    """ob_oran: V18.3 formülü (ilk 20 kademe bid/ask USDT hacim oranı).
    spread_bps ve ob_oran_yakin (orta fiyatın ±%1'i) V18.4 ile eklendi.
    Hata/eksik veri -> None (V18.3 0.0 yazıyordu; 0 geçerli bir değer gibi görünüp modeli yanıltır)."""
    sonuc = {'ob_oran': None, 'spread_bps': None, 'ob_oran_yakin': None}
    if not order_book:
        return sonuc
    try:
        bids = [(float(e[0]), float(e[1])) for e in order_book.get('bids') or []]
        asks = [(float(e[0]), float(e[1])) for e in order_book.get('asks') or []]
        bid_v = sum(p * q for p, q in bids)
        ask_v = sum(p * q for p, q in asks)
        sonuc['ob_oran'] = float(bid_v / ask_v) if ask_v > 0 else None
        if bids and asks:
            orta = (bids[0][0] + asks[0][0]) / 2
            if orta > 0:
                sonuc['spread_bps'] = float((asks[0][0] - bids[0][0]) / orta * 1e4)
                yb = sum(p * q for p, q in bids if p >= orta * 0.99)
                ya = sum(p * q for p, q in asks if p <= orta * 1.01)
                sonuc['ob_oran_yakin'] = float(yb / ya) if ya > 0 else None
    except (TypeError, ValueError, IndexError):
        pass
    return sonuc


def _nan_none(x):
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else x


def genisletilmis_ozellikler(df15: pd.DataFrame, df1h: pd.DataFrame, temel: dict, *,
                             simdi: datetime.datetime, simdi_ms: int,
                             btc_1h_degisim: float, btc_ema_uzaklik: float, btc_adx: float,
                             stop_sayisi: int, ob: Optional[dict] = None,
                             genislik: Optional[float] = None, kapali_mum_modu: bool = False) -> dict:
    """V18.3 analyze_market'in metrics sözlüğü (+ V18.4 kayıt feature'ları).
    ai_score / filter_score burada None'dır; skorlama ayrı yapılır."""
    price, av_v = temel['price'], temel['av_v']
    ema_20_15m = ema_son(df15['c'], 20)
    try:
        sym_adx = float(ta.adx(df15['h'], df15['l'], df15['c'], length=14)['ADX_14'].iloc[-1])
        if not math.isfinite(sym_adx):
            sym_adx = None
    except Exception:
        sym_adx = None
    zirve_24h = float(df15['h'].iloc[-96:].max())
    pump_3s_ref = df15['c'].iloc[-13] if len(df15) >= 13 else df15['c'].iloc[0]
    pump_6s_ref = df15['c'].iloc[-25] if len(df15) >= 25 else df15['c'].iloc[0]
    kapali_mum_onay = int(df15['c'].iloc[-2] > df15['o'].iloc[-2] and df15['v'].iloc[-2] > av_v)
    mum_acilis = float(df15['ts'].iloc[-1])
    mum_ilerleme = 1.0 if kapali_mum_modu else min(1.0, max(0.0, (simdi_ms - mum_acilis) / MUM_15M_MS))
    obf = ob_ozellikleri(ob)
    return {
        "signal": "MSB" if temel['msb'] else "Engulf",
        "fiyat": float(price),
        "rsi": float(temel['rsi']),
        "vol_ratio": float(temel['vol_ratio']),
        "atr_pct": float((temel['atr'] / price) * 100),
        "is_whale": bool(temel['is_whale']),
        "pump_3s": float((price / pump_3s_ref - 1) * 100),
        "pump_6s": float((price / pump_6s_ref - 1) * 100),
        "zirve_uzaklik": float((zirve_24h - price) / zirve_24h * 100) if zirve_24h > 0 else 0.0,
        "ema1h_uzaklik": float((price / temel['ema20_1h'] - 1) * 100),
        "ema15m_uzaklik": float((price / ema_20_15m - 1) * 100) if ema_20_15m > 0 else 0.0,
        "btc_1h_degisim": float(btc_1h_degisim),
        "btc_ema_uzaklik": float(btc_ema_uzaklik),
        "saat": simdi.hour,
        "gun": simdi.weekday(),
        "stop_sayisi": int(stop_sayisi),
        "sym_adx": sym_adx,
        "btc_adx": float(btc_adx),
        "kapali_mum_onay": kapali_mum_onay,
        "ob_oran": obf['ob_oran'],
        "ai_score": None,
        "filter_score": None,
        "sinyal_ts": int(simdi_ms),
        "mum_ilerleme": float(mum_ilerleme),
        "spread_bps": obf['spread_bps'],
        "ob_oran_yakin": obf['ob_oran_yakin'],
        "genislik": _nan_none(genislik),
        "kapali_mum_modu": int(bool(kapali_mum_modu)),
    }


# ----------------------------------------------------------------------------------
# Kanonik satır + türetilmiş feature'lar (trainer ve bot ortak)
# ----------------------------------------------------------------------------------
def kanonik_satir(metrics: dict) -> dict:
    """Bot metrics sözlüğünü CSV/model kolon adlarına çevirir."""
    satir = {kolon: metrics.get(anahtar) for anahtar, kolon in METRIK_KOLON.items()}
    satir['Sinyal'] = metrics.get('signal')
    return satir


def _sayisal(df: pd.DataFrame, kolon: str) -> pd.Series:
    if kolon not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return pd.to_numeric(df[kolon], errors='coerce').astype(float)


def turetilmis_ozellikler(df: pd.DataFrame) -> pd.DataFrame:
    """Kanonik kolonlardan ölçekten bağımsız türetilmiş feature'lar.
    Farklı volatilitedeki coinleri karşılaştırılabilir kılar (uzaklıklar ATR cinsinden)."""
    out = df.copy()
    atr = _sayisal(df, 'Giris_ATR_Pct').where(lambda s: s > 0)
    out['Log_Vol_Oran'] = np.log(_sayisal(df, 'Giris_Vol_Oran').clip(lower=1e-6))
    for kaynak, hedef in [('EMA15m_Uzaklik', 'EMA15m_ATR'), ('EMA1h_Uzaklik', 'EMA1h_ATR'),
                          ('Pump_3s', 'Pump3s_ATR'), ('Pump_6s', 'Pump6s_ATR'),
                          ('Zirve_Uzaklik', 'Zirve_ATR')]:
        out[hedef] = _sayisal(df, kaynak) / atr
    saat = _sayisal(df, 'Saat')
    out['Saat_Sin'] = np.sin(2 * np.pi * saat / 24)
    out['Saat_Cos'] = np.cos(2 * np.pi * saat / 24)
    ilerleme = _sayisal(df, 'Mum_Ilerleme')
    # Kısmi mumdaki hacim oranını tam muma ölçekle (mumun 2. dakikasında 2.5x ile 14. dakikasında 2.5x aynı şey değil)
    out['Hacim_Hizi'] = _sayisal(df, 'Giris_Vol_Oran') / ilerleme.clip(lower=1 / 15)
    # Legacy core modelin 4. feature'ı (V18.3: 1 if msb else 2). Veride %100 MSB olduğu için bilgi taşımaz.
    sinyal = df['Sinyal'] if 'Sinyal' in df.columns else pd.Series(None, index=df.index, dtype=object)
    out['Sinyal_Encoded'] = sinyal.map(SINYAL_KODLARI).fillna(0).astype(float)
    return out


def ozellik_matrisi(df: pd.DataFrame, featurelar) -> pd.DataFrame:
    """Kanonik+türetilmiş tablodan modelin beklediği sırada float matris."""
    yasak = set(featurelar) & YASAK_FEATURELAR
    if yasak:
        raise ValueError(f"Model feature listesinde yasak kolon: {sorted(yasak)}")
    t = turetilmis_ozellikler(df)
    return pd.DataFrame({f: _sayisal(t, f) for f in featurelar}, index=df.index)
