# -*- coding: utf-8 -*-
"""
AI TRAINER V2
- Kısmi kâr + tam çıkış satırlarını pozisyon bazında birleştirir (aynı pozisyon iki kez sayılmaz)
- Zaman bazlı train/test ayrımı ile AUC doğrulaması yapar; başarısız model KAYDEDİLMEZ (eskisi korunur)
- Sınıf dengesizliğini scale_pos_weight ile düzeltir
- Hem filter_model.json (zarar filtresi) hem core_xgboost_model.json (kâr modeli) eğitir
"""
import os
import datetime
import pandas as pd
import xgboost as xgb

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KAYNAK_DOSYALAR = ['core_islem_verileri.csv', 'ufuk_islem_verileri.csv']
LOG_DOSYASI = os.path.join(BASE_DIR, 'ai_trainer_history.log')

COLS = ['Islem_Zamani', 'Sembol', 'Sinyal', 'Kasa_Tipi', 'Giris_RSI', 'Giris_Vol_Oran',
        'Giris_ATR_Pct', 'Giris_Fiyat', 'Cikis_Fiyat', 'Kar_Orani', 'Net_Kar_USDT',
        'Cikis_Tipi', 'Sure_Saat']
FEATURES = ['Giris_RSI', 'Giris_Vol_Oran', 'Giris_ATR_Pct']

ZARAR_ESIGI = -0.1       # pozisyonun TOPLAM net kârı bunun altındaysa "kötü işlem" (filtre hedefi)
KAR_ESIGI = 0.1          # core model için "iyi işlem" eşiği
TEST_ORANI = 0.2         # zaman bazlı doğrulama: kronolojik son %20 test kümesi
MIN_POZISYON = 100       # bundan az pozisyonla eğitim yapılmaz
MIN_AUC = 0.55           # test AUC bunun altındaysa model kaydedilmez
BASLANGIC_TARIHI = None  # ör. '2026-08-01': eski bot versiyonlarının verisini dışlamak için


def log(mesaj):
    satir = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {mesaj}"
    print(satir)
    try:
        with open(LOG_DOSYASI, 'a', encoding='utf-8') as f:
            f.write(satir + '\n')
    except OSError:
        pass


def auc_hesapla(y_true, y_score):
    # Mann-Whitney U tabanlı AUC (sklearn bağımlılığı olmadan)
    y = pd.Series(list(y_true)).reset_index(drop=True)
    r = pd.Series(list(y_score)).rank(method='average')
    n1 = int(y.sum())
    n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return None
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def veriyi_yukle():
    parcalar = []
    for ad in KAYNAK_DOSYALAR:
        yol = os.path.join(BASE_DIR, ad)
        if os.path.exists(yol):
            parcalar.append(pd.read_csv(yol, header=None, names=COLS))
    if not parcalar:
        return None
    df = pd.concat(parcalar, ignore_index=True)
    # Olası başlık satırları / bozuk kayıtlar sayıya çevrilemez -> NaN -> aşağıda düşer
    for k in FEATURES + ['Giris_Fiyat', 'Net_Kar_USDT']:
        df[k] = pd.to_numeric(df[k], errors='coerce')
    df['Islem_Zamani'] = pd.to_datetime(df['Islem_Zamani'], errors='coerce')
    df = df.dropna(subset=FEATURES + ['Giris_Fiyat', 'Net_Kar_USDT', 'Islem_Zamani'])
    if BASLANGIC_TARIHI:
        df = df[df['Islem_Zamani'] >= pd.Timestamp(BASLANGIC_TARIHI)]
    return df


def pozisyonlara_indirge(df):
    # Kısmi kâr + tam çıkış aynı pozisyonun parçalarıdır; giriş bilgileri birebir aynı
    # olduğundan bu kolonlarla gruplayıp net kârları toplamak pozisyonun gerçek sonucunu verir.
    df = df.copy()
    df['Sinyal'] = df['Sinyal'].fillna('')
    grup = ['Sembol', 'Giris_Fiyat'] + FEATURES + ['Sinyal']
    poz = df.groupby(grup, as_index=False).agg(
        Net_Kar_USDT=('Net_Kar_USDT', 'sum'),
        Islem_Zamani=('Islem_Zamani', 'min'))
    return poz.sort_values('Islem_Zamani').reset_index(drop=True)


def model_egit_ve_dogrula(X, y, model_adi, dosya):
    """X kronolojik sıralı olmalı. Doğrulama geçerse tüm veriyle eğitip kaydeder."""
    n_test = max(1, int(len(X) * TEST_ORANI))
    X_tr, X_te = X.iloc[:-n_test], X.iloc[-n_test:]
    y_tr, y_te = y.iloc[:-n_test], y.iloc[-n_test:]

    if y_tr.nunique() < 2 or y_te.nunique() < 2:
        log(f"⚠️ {model_adi}: train/test kümelerinde her iki sınıf da yok, eğitim atlandı.")
        return

    def yeni_model(y_ref):
        pos = int(y_ref.sum())
        agirlik = (len(y_ref) - pos) / pos if pos > 0 else 1.0
        return xgb.XGBClassifier(
            n_estimators=100, max_depth=3, learning_rate=0.05,
            subsample=0.8, random_state=42, eval_metric='logloss',
            scale_pos_weight=agirlik)

    model = yeni_model(y_tr)
    model.fit(X_tr, y_tr)
    skorlar = model.predict_proba(X_te)[:, 1]
    auc = auc_hesapla(y_te, skorlar)

    if auc is None:
        log(f"⚠️ {model_adi}: AUC hesaplanamadı, eğitim atlandı.")
        return
    if auc < MIN_AUC:
        log(f"🛑 {model_adi}: Test AUC {auc:.3f} < {MIN_AUC} — model KAYDEDİLMEDİ, eski model korunuyor.")
        return

    # Doğrulama geçti -> dağıtım için tüm veriyle yeniden eğit ve kaydet
    final = yeni_model(y)
    final.fit(X, y)
    final.save_model(os.path.join(BASE_DIR, dosya))
    log(f"✅ {model_adi}: Test AUC {auc:.3f} | {len(X)} pozisyon ({int(y.sum())} pozitif) | {dosya} güncellendi.")


def main():
    df = veriyi_yukle()
    if df is None or df.empty:
        log("❌ Eğitilecek veri bulunamadı.")
        return

    poz = pozisyonlara_indirge(df)
    log(f"📊 {len(df)} işlem satırı -> {len(poz)} benzersiz pozisyon.")
    if len(poz) < MIN_POZISYON:
        log(f"⚠️ Yetersiz veri: {len(poz)} pozisyon (< {MIN_POZISYON}), eğitim yapılmadı.")
        return

    # 1) FİLTRE MODELİ: zarar ettiren piyasa koşullarını tanır (1 = kötü işlem)
    y_filtre = (poz['Net_Kar_USDT'] < ZARAR_ESIGI).astype(int)
    model_egit_ve_dogrula(poz[FEATURES], y_filtre, 'Filtre Beyni', 'filter_model.json')

    # 2) CORE MODEL: kâr ettiren işlemleri tanır (1 = iyi işlem); sinyal tipi de feature
    #    (Bot tarafındaki kolon sırasıyla birebir aynı: FEATURES + Sinyal_Encoded)
    poz = poz.copy()
    poz['Sinyal_Encoded'] = poz['Sinyal'].map({'MSB': 1, 'Engulf': 2}).fillna(0).astype(int)
    y_core = (poz['Net_Kar_USDT'] > KAR_ESIGI).astype(int)
    model_egit_ve_dogrula(poz[FEATURES + ['Sinyal_Encoded']], y_core, 'Core Beyin', 'core_xgboost_model.json')


if __name__ == "__main__":
    main()
