# -*- coding: utf-8 -*-
"""
AI TRAINER V3

V2'nin yapısal sorunları (ANALIZ_V18.4.md §2):
  - Etiket gerçekleşen USDT kârıydı: kasa büyüklüğüne (20/40 USDT) ve botun o
    haftaki çıkış mantığına bağlı, sürüm değiştikçe kayan bir hedef.
  - Filtre (Net<-0.1) ve core (Net>+0.1) etiketleri neredeyse birbirinin tümleyeni:
    iki model aynı bilgiyi iki kez öğreniyor, AND'lenince gürültü ikiye katlanıyordu.
  - Tek 80/20 bölme: n_test ~100'de AUC standart hatası ~0.05; her gece yeniden
    denendiği için şanslı bir bölme eninde sonunda 0.55'i geçip rastgele modeli
    yayına alırdı (zaman içinde çoklu test).
  - scale_pos_weight olasılık ölçeğini kaydırıyor, bottaki sabit 0.65/0.45 eşiklerini
    anlamsızlaştırıyordu. Sinyal_Encoded veride %100 sabit (hep MSB).

V3:
  - Veri: etiketli_sinyaller.csv (core + shadow + backfill; tek eğitim dosyası). Henüz birleştirilmemiş eski
    backfill_sinyaller.csv varsa o da okunur (aynı Anahtar bir kez sayılır).
    Hepsi AYNI etiket fonksiyonuyla etiketli (sniper.etiketleme) -> güvenle birleşir.
  - Hedef: y = 1[Etiket_Getiri > 0] (net, komisyon dahil).
  - Tek karar modeli (core). Eski filtre modeli yeni doğrulanmış model yayına alınınca emekliye ayrılır.
  - Doğrulama: zaman sıralı genişleyen pencere walk-forward, etiket penceresi kadar purge,
    episod (aynı sembolde 4 saat içindeki ardışık sinyaller) bazlı bootstrap güven aralığı.
  - Adaylar: depth-1 (stump / GAM benzeri) ve depth-2 XGBoost; lineer baz model (sadece kıyas).
  - Kapılar: havuzlanmış OOS AUC >= MIN_AUC, %95 alt güven sınırı > 0.5, katların çoğunda > 0.5,
    ekonomik test (en kötü %30'u engellemek ortalama getiriyi anlamlı artırıyor mu, permütasyon),
    canlı kanıt (en az MIN_CANLI_ESIK tam kayıtlı canlı olay) ve canlı transfer.
  - Doğrulama, eşik ve kapılar yalnız seçilen feature'ların HEPSİ dolu satırlarla hesaplanır: botun canlıda
    skorlayacağı satırlar bunlardır. Eski kayıtlarda (V18.0–18.3) yeni ölçümler yok; boş hücre satırın
    kaynağını ele verir ve AUC'yi kaynak farkıyla şişirir. Bu satırlar eğitime girer, doğrulamaya girmez.
  - Eşik OPTİMİZE EDİLMEZ: OOS skorlarının ENGELLEME_ORANI kantili (seçim iyimserliği yok);
    yeterli canlı örnek varsa canlı (core+shadow) skorların kantili: backfill kapalı mumla, canlı bot
    kısmi mumla çalıştığı için havuz eşiği canlıda farklı oranda engelleyebilir (raporlanır).
  - Ekonomik test her episodun İLK sinyaliyle yapılır: bot pozisyondayken aynı sembolün sonraki
    sinyallerini zaten işlemez ve çakışan sinyaller bağımsız gözlem değildir.
  - Model + kart (feature listesi, eşik, metrikler, SHA-256) atomik kaydedilir; bot hot-reload eder.
  - egitim_raporu.json: tüm metrikler, kararlar, kaynak dağılım kayması (adversarial validation).

Kullanım: python ai_trainer.py            (cron 03:00)
          python ai_trainer.py --kuru     (rapor üret, modele dokunma)
"""
import argparse
import datetime
import json
import math
import os

import numpy as np
import pandas as pd
import xgboost as xgb

from sniper import etiket_deposu as depo
from sniper.csv_kayit import atomik_metin_yaz
from sniper.etiketleme import ETIKET_SURUMU
from sniper.model_karti import json_uyumlu, modeli_kartla_kaydet
from sniper.ozellikler import ozellik_matrisi

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ETIKET_DOSYALARI = ['etiketli_sinyaller.csv', 'backfill_sinyaller.csv']   # ikincisi yalnız eski kurulumlarda
LOG_DOSYASI = os.path.join(BASE_DIR, 'ai_trainer_history.log')
RAPOR_DOSYASI = os.path.join(BASE_DIR, 'egitim_raporu.json')
CORE_MODEL = os.path.join(BASE_DIR, 'core_xgboost_model.json')
FILTRE_MODEL = os.path.join(BASE_DIR, 'filter_model.json')

MIN_ORNEK = 150            # bundan az sinyalle eğitim yapılmaz
MIN_SINIF = 30             # her sınıftan en az bu kadar örnek
MIN_AUC = 0.55             # havuzlanmış OOS AUC alt sınırı
CV_KAT = 5                 # walk-forward test katı sayısı (küçük veride otomatik azalır)
ETIKET_PENCERE_MS = 4 * 3_600_000
ENGELLEME_ORANI = 0.30     # model en düşük skorlu bu orandaki sinyalleri engeller
EKONOMIK_P = 0.05
BOOT_N, PERM_N = 2000, 5000
SADECE_BTC_OK = True       # canlı bot BTC_OK=False iken sinyal analiz etmez: dağılımı eşle
CANLI_KAYNAKLAR = ('core', 'shadow')
CANLI_AGIRLIK = 1.0        # canlı örneklere ek ağırlık (dağılım kaymasında >1 denenebilir)
MIN_CANLI_ESIK = 50        # tam kayıtlı canlı OOS olay (episod) sayısı: altında model yayınlanmaz; eşik canlı kantilden
ESKI_FILTREYI_EMEKLI_ET = True

# Varsayılan feature'lar: geçmişe dönük üretilebilen (backfill) ve ölçekten bağımsız olanlar.
# Canlıya özgü (OB_Oran, Spread_Bps, Mum_Ilerleme, Stop_Sayisi) yeterli canlı veri birikince eklenmeli.
FEATURES_KOMPAKT = ['Giris_RSI', 'Log_Vol_Oran', 'Giris_ATR_Pct', 'EMA15m_ATR', 'Pump3s_ATR',
                    'Zirve_ATR', 'BTC_1h_Degisim', 'BTC_ADX']
FEATURES_GENIS = FEATURES_KOMPAKT + ['Pump6s_ATR', 'EMA1h_ATR', 'BTC_EMA_Uzaklik', 'Sym_ADX',
                                     'Kapali_Mum_Onay', 'Piyasa_Genislik', 'Saat_Sin', 'Saat_Cos']
GENIS_ESIK = 400           # bu kadar örnekten sonra geniş set (≈ sınıf başına 15+ olay/feature)

ADAYLAR = {
    'xgb_d1': dict(n_estimators=300, learning_rate=0.03, max_depth=1, min_child_weight=3,
                   subsample=0.8, colsample_bytree=0.8, reg_lambda=5.0),
    'xgb_d2': dict(n_estimators=200, learning_rate=0.03, max_depth=2, min_child_weight=5,
                   subsample=0.8, colsample_bytree=0.7, reg_lambda=10.0, gamma=0.5),
}


def log(mesaj):
    satir = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {mesaj}"
    print(satir)
    try:
        with open(LOG_DOSYASI, 'a', encoding='utf-8') as f:
            f.write(satir + '\n')
    except OSError:
        pass


# --------------------------------------------------------------------------------------
# İstatistik yardımcıları (sklearn bağımlılığı yok)
# --------------------------------------------------------------------------------------
def auc(y, s):
    """Mann-Whitney U tabanlı AUC; tek sınıf varsa None."""
    y = np.asarray(y, dtype=int)
    r = pd.Series(np.asarray(s, dtype=float)).rank(method='average').to_numpy()
    n1 = int(y.sum())
    n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return None
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def episod_bootstrap(y, s, ep, n=BOOT_N, seed=0):
    """Episod bazlı (blok) bootstrap AUC güven aralığı: ardışık, pencereleri çakışan
    sinyaller bağımsız sayılmaz."""
    rng = np.random.default_rng(seed)
    y, s, ep = np.asarray(y), np.asarray(s), np.asarray(ep)
    uniq = np.unique(ep)
    indeks = {e: np.flatnonzero(ep == e) for e in uniq}
    degerler = []
    for _ in range(n):
        sec = rng.choice(uniq, size=len(uniq), replace=True)
        i = np.concatenate([indeks[e] for e in sec])
        a = auc(y[i], s[i])
        if a is not None:
            degerler.append(a)
    if not degerler:
        return None, None
    return float(np.percentile(degerler, 2.5)), float(np.percentile(degerler, 97.5))


def ekonomik_test(getiri, skor, engelleme_orani=ENGELLEME_ORANI, n=PERM_N, seed=1):
    """En düşük skorlu %X engellenirse ortalama getiri ne kadar artar; skorlar karıştırılarak
    aynı artışın şansla elde edilme olasılığı (tek yönlü p)."""
    rng = np.random.default_rng(seed)
    getiri, skor = np.asarray(getiri, float), np.asarray(skor, float)
    esik = float(np.quantile(skor, engelleme_orani))
    gecen = skor >= esik
    fark = float(getiri[gecen].mean() - getiri.mean())
    say = 0
    for _ in range(n):
        p = rng.permutation(skor)
        if getiri[p >= esik].mean() - getiri.mean() >= fark:
            say += 1
    return {'esik_oos': esik, 'gecen_oran': float(gecen.mean()), 'ort_getiri_hepsi': float(getiri.mean()),
            'ort_getiri_gecen': float(getiri[gecen].mean()), 'ort_getiri_engellenen': float(getiri[~gecen].mean()),
            'artis': fark, 'p': (say + 1) / (n + 1)}


# --------------------------------------------------------------------------------------
# Veri
# --------------------------------------------------------------------------------------
def episodlar(df, pencere_ms=ETIKET_PENCERE_MS):
    """Aynı sembolde bir öncekinden < pencere sonra gelen sinyal aynı episoda aittir
    (etiket pencereleri çakışır -> bağımsız gözlem değildir)."""
    ep = np.empty(len(df), dtype=object)
    for sym, g in df.groupby('Sembol', sort=False):
        ts = g['Ts'].to_numpy()
        sira = np.argsort(ts, kind='stable')
        yeni = np.r_[True, np.diff(ts[sira]) >= pencere_ms]
        no = np.cumsum(yeni)
        etiket = np.empty(len(g), dtype=object)
        etiket[sira] = [f"{sym}#{k}" for k in no]
        ep[df.index.get_indexer(g.index)] = etiket
    return ep


def veriyi_hazirla(yollar):
    df = depo.oku(yollar, ETIKET_SURUMU)
    if df.empty:
        return df
    df = df[df['Etiket_Sonuc'].isin(['TP', 'SL', 'KILIT', 'ZAMAN'])].copy()
    df = df.dropna(subset=['Ts', 'Etiket_Getiri'])
    if SADECE_BTC_OK and 'BTC_OK' in df.columns:
        btc_ok = pd.to_numeric(df['BTC_OK'], errors='coerce').fillna(1)
        df = df[btc_ok == 1]
    df = df.sort_values('Ts', kind='stable').reset_index(drop=True)
    df['y'] = (df['Etiket_Getiri'] > 0).astype(int)
    df['episod'] = episodlar(df)
    boyut = df.groupby('episod')['episod'].transform('size')
    df['w'] = 1.0 / boyut                                         # benzersizlik ağırlığı
    df.loc[df['Kaynak'].isin(CANLI_KAYNAKLAR), 'w'] *= CANLI_AGIRLIK
    return df


def feature_sec(df, aday_liste):
    X = ozellik_matrisi(df, aday_liste)
    secilen, elenen = [], {}
    for f in aday_liste:
        s = X[f]
        if s.isna().mean() > 0.5:
            elenen[f] = 'eksik>%50'
        elif s.nunique(dropna=True) <= 1:
            elenen[f] = 'sabit'
        else:
            secilen.append(f)
    return secilen, elenen


def katlar(ts, k, pencere_ms=ETIKET_PENCERE_MS, min_egitim_orani=0.4):
    """Genişleyen pencere walk-forward. Test bloğu başlangıcından önceki `pencere_ms` içinde
    kalan eğitim örnekleri atılır (purge): etiketleri test dönemine taşar."""
    n = len(ts)
    bas = int(n * min_egitim_orani)
    sinirlar = np.linspace(bas, n, k + 1).astype(int)
    sonuc = []
    for i in range(k):
        t_bas, t_bit = sinirlar[i], sinirlar[i + 1]
        if t_bit - t_bas < 5:
            continue
        test_t0 = ts[t_bas]
        egitim = np.flatnonzero(ts[:t_bas] < test_t0 - pencere_ms)
        sonuc.append((egitim, np.arange(t_bas, t_bit)))
    return sonuc


# --------------------------------------------------------------------------------------
# Modeller
# --------------------------------------------------------------------------------------
def xgb_model(params):
    return xgb.XGBClassifier(objective='binary:logistic', eval_metric='logloss', random_state=42,
                             tree_method='hist', n_jobs=2, **params)


def lineer_baz(X_tr, y_tr, w_tr, X_te, lam=1.0):
    """Standartlaştırılmış, L2 cezalı lojistik regresyon (Newton/IRLS, numpy). Sadece KIYAS içindir:
    ağaç modeli bunu anlamlı geçemiyorsa sinyal büyük ölçüde doğrusaldır ve derinlik aşırı öğrenmedir.
    (xgboost gblinear 3.4 itibarıyla deprecated olduğu için kullanılmıyor.)"""
    med = X_tr.median()
    mu, sd = X_tr.fillna(med).mean(), X_tr.fillna(med).std().replace(0, 1).fillna(1)
    z = lambda X: np.c_[np.ones(len(X)), ((X.fillna(med) - mu) / sd).fillna(0.0).to_numpy()]
    A, y, w = z(X_tr), np.asarray(y_tr, float), np.asarray(w_tr, float)
    ceza = lam * np.r_[0.0, np.ones(A.shape[1] - 1)]
    beta = np.zeros(A.shape[1])
    for _ in range(50):
        p = 1 / (1 + np.exp(-np.clip(A @ beta, -30, 30)))
        g = A.T @ (w * (p - y)) + ceza * beta
        H = (A * (w * p * (1 - p))[:, None]).T @ A + np.diag(ceza + 1e-9)
        adim = np.linalg.solve(H, g)
        beta -= adim
        if np.abs(adim).max() < 1e-8:
            break
    return 1 / (1 + np.exp(-np.clip(z(X_te) @ beta, -30, 30)))


def capraz_dogrula(df, X, aday, degerlendir=None):
    """Walk-forward OOS skorları; kat AUC'leri yalnız `degerlendir` satırlarında (varsayılan hepsi)."""
    ts, y, w = df['Ts'].to_numpy(), df['y'].to_numpy(), df['w'].to_numpy()
    degerlendir = np.ones(len(df), bool) if degerlendir is None else np.asarray(degerlendir, bool)
    k = CV_KAT if len(df) >= 500 else 3
    oos = np.full(len(df), np.nan)
    kat_auc = []
    for egitim, test in katlar(ts, k):
        if len(egitim) < 50 or len(np.unique(y[egitim])) < 2:
            continue
        if aday == 'lineer':
            oos[test] = lineer_baz(X.iloc[egitim], y[egitim], w[egitim], X.iloc[test])
        else:
            m = xgb_model(ADAYLAR[aday])
            m.fit(X.iloc[egitim], y[egitim], sample_weight=w[egitim])
            oos[test] = m.predict_proba(X.iloc[test])[:, 1]
        d = test[degerlendir[test]]
        kat_auc.append(auc(y[d], oos[d]) if len(d) else None)
    return oos, kat_auc


def dagilim_kaymasi(df, X):
    """Adversarial validation: model canlı (core+shadow) ile backfill'i ayırt edebiliyor mu?
    AUC ~0.5: aynı dağılım; yüksek: backfill canlıyı temsil etmiyor (ör. kapanmamış mum etkisi)."""
    kaynak = df['Kaynak'].isin(CANLI_KAYNAKLAR).astype(int).to_numpy()
    if kaynak.sum() < 30 or (1 - kaynak).sum() < 30:
        return None
    rng = np.random.default_rng(3)
    kat = rng.integers(0, 3, len(df))
    skor = np.zeros(len(df))
    for i in range(3):
        tr, te = kat != i, kat == i
        m = xgb_model(dict(n_estimators=100, learning_rate=0.1, max_depth=2))
        m.fit(X[tr], kaynak[tr])
        skor[te] = m.predict_proba(X[te])[:, 1]
    m = xgb_model(dict(n_estimators=100, learning_rate=0.1, max_depth=2)).fit(X, kaynak)
    onem = pd.Series(m.get_booster().get_score(importance_type='gain')).sort_values(ascending=False)
    return {'auc': auc(kaynak, skor), 'en_ayirt_edici': onem.head(5).round(3).to_dict()}


# --------------------------------------------------------------------------------------
def egit(yollar, kuru=False):
    rapor = {'tarih': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
             'etiket_surumu': ETIKET_SURUMU, 'karar': None}
    df = veriyi_hazirla(yollar)
    if df.empty:
        log("❌ Etiketli veri yok. Önce: python shadow_labeler.py (ve önerilen: python backfill_sinyaller.py)")
        rapor['karar'] = 'veri_yok'
        return rapor
    rapor['veri'] = {'n': int(len(df)), 'pozitif': int(df['y'].sum()), 'episod': int(df['episod'].nunique()),
                     'kaynak': df['Kaynak'].value_counts().to_dict(),
                     'donem': [str(pd.Timestamp(df['Ts'].min(), unit='ms')), str(pd.Timestamp(df['Ts'].max(), unit='ms'))],
                     'sonuc_dagilimi': df['Etiket_Sonuc'].value_counts().to_dict()}
    log(f"📊 {len(df)} etiketli sinyal ({df['episod'].nunique()} episod) | kaynak: {rapor['veri']['kaynak']} | "
        f"pozitif oran: {df['y'].mean():.2f}")
    n_poz, n_neg = int(df['y'].sum()), int(len(df) - df['y'].sum())
    if len(df) < MIN_ORNEK or min(n_poz, n_neg) < MIN_SINIF:
        log(f"⚠️ Yetersiz veri: {len(df)} sinyal ({n_poz}+/{n_neg}-); gereken {MIN_ORNEK} ve sınıf başına "
            f"{MIN_SINIF}. Çözüm: python backfill_sinyaller.py --gun 180 — eğitim yapılmadı, model korunuyor.")
        rapor['karar'] = 'yetersiz_veri'
        return rapor

    aday_feat = FEATURES_GENIS if len(df) >= GENIS_ESIK else FEATURES_KOMPAKT
    featurelar, elenen = feature_sec(df, aday_feat)
    X = ozellik_matrisi(df, featurelar)
    rapor['featurelar'], rapor['elenen_featurelar'] = featurelar, elenen
    # Botun canlıda skorlayacağı satırlar: seçilen feature'ların hepsi dolu (bkz. modül açıklaması).
    tam = X.notna().all(axis=1).to_numpy()
    canli_kaynak = df['Kaynak'].isin(CANLI_KAYNAKLAR).to_numpy()
    rapor['tam_satir'] = {'n': int(tam.sum()), 'canli': int((tam & canli_kaynak).sum()),
                          'canli_eksik': int((~tam & canli_kaynak).sum())}
    eksik = X[canli_kaynak].isna().mean() if canli_kaynak.any() else pd.Series(dtype=float)
    rapor['canli_eksik_feature'] = {f: round(float(v), 3) for f, v in
                                    eksik[eksik > 0].sort_values(ascending=False).items()}
    if rapor['tam_satir']['canli_eksik']:
        log(f"🧩 {rapor['tam_satir']['canli_eksik']} canlı satırda seçilen feature'lardan en az biri boş (eski kayıt "
            f"formatı): eğitime girer, doğrulamaya girmez. En eksik: {list(rapor['canli_eksik_feature'])[:3]}")
    if tam.sum() < MIN_ORNEK:
        log(f"⚠️ Tüm feature'ları dolu satır {int(tam.sum())} (gereken {MIN_ORNEK}): doğrulanamaz, model korunuyor.")
        rapor['karar'] = 'yetersiz_veri'
        return rapor
    rapor['dagilim_kaymasi'] = dagilim_kaymasi(df[tam], X[tam])
    if rapor['dagilim_kaymasi']:
        log(f"🔎 Canlı↔backfill ayırt edilebilirliği AUC {rapor['dagilim_kaymasi']['auc']:.2f} "
            f"(0.5 ideal); en ayırt edici: {list(rapor['dagilim_kaymasi']['en_ayirt_edici'])[:3]}")
    else:
        log("🔎 Tam kayıtlı canlı satır 30'dan az: canlı↔backfill dağılım farkı henüz ölçülemiyor.")

    y, ep = df['y'].to_numpy(), df['episod'].to_numpy()
    sonuclar = {}
    for aday in ['lineer'] + list(ADAYLAR):
        oos, kat_auc = capraz_dogrula(df, X, aday, tam)
        m = ~np.isnan(oos) & tam
        a = auc(y[m], oos[m]) if m.sum() else None
        alt, ust = episod_bootstrap(y[m], oos[m], ep[m]) if a is not None else (None, None)
        sonuclar[aday] = {'oos_auc': a, 'ci95': [alt, ust], 'kat_auc': kat_auc, 'n_oos': int(m.sum()), '_oos': oos}
        log(f"   {aday:7s}: OOS AUC {a if a is None else round(a, 3)} (95% GA {alt and round(alt, 3)}–{ust and round(ust, 3)}) "
            f"| katlar {[round(x, 3) for x in kat_auc if x is not None]}")

    agac = {k: v for k, v in sonuclar.items() if k != 'lineer' and v['oos_auc'] is not None}
    if not agac:
        log("⚠️ Çapraz doğrulama yapılamadı (katlarda tek sınıf). Model korunuyor.")
        rapor['karar'] = 'cv_basarisiz'
        return rapor
    en_iyi = max(agac, key=lambda k: agac[k]['oos_auc'])
    s = sonuclar[en_iyi]
    oos = s['_oos']
    m = ~np.isnan(oos) & tam
    ilk_sinyal = m & ~df['episod'].where(m).duplicated().to_numpy()     # her episodun ilk tam satırı
    eko = ekonomik_test(df['Etiket_Getiri'].to_numpy()[ilk_sinyal], oos[ilk_sinyal])
    eko['n_episod'] = int(ilk_sinyal.sum())
    canli = m & canli_kaynak
    # Canlı kanıt bağımsız OLAY sayısıyla ölçülür: iki makinenin eğitim verisi birleşince aynı sinyal iki satır
    # olur (aynı coin, dakikalar arayla); aynı episoddaki satırlar tek olaydır.
    n_canli = int(df.loc[canli, 'episod'].nunique())
    canli_auc = auc(y[canli], oos[canli]) if n_canli >= 40 else None
    esik_havuz = float(np.quantile(oos[m], ENGELLEME_ORANI))
    esik_bilgi = {'esik_havuz': esik_havuz, 'n_canli_oos': int(canli.sum()), 'n_canli_episod': n_canli,
                  'canli_engelleme_havuz_esigiyle': float((oos[canli] < esik_havuz).mean()) if canli.sum() else None}
    if n_canli >= MIN_CANLI_ESIK:
        esik_bilgi.update(esik=float(np.quantile(oos[canli], ENGELLEME_ORANI)), esik_kaynagi='canli')
    else:
        esik_bilgi.update(esik=esik_havuz, esik_kaynagi='havuz')
    if esik_bilgi['canli_engelleme_havuz_esigiyle'] is not None and n_canli >= 20:
        log(f"   havuz eşiği canlı sinyallerin %{esik_bilgi['canli_engelleme_havuz_esigiyle'] * 100:.0f}'ini engellerdi "
            f"(hedef %{ENGELLEME_ORANI * 100:.0f}); eşik kaynağı: {esik_bilgi['esik_kaynagi']}")
    kat_ok = sum(1 for x in s['kat_auc'] if x is not None and x > 0.5)

    kapilar = {
        'auc': s['oos_auc'] >= MIN_AUC,
        'auc_alt_sinir': (s['ci95'][0] or 0) > 0.5,
        'kat_kararliligi': kat_ok >= math.ceil(0.6 * len(s['kat_auc'])),
        'ekonomik': eko['artis'] > 0 and eko['p'] < EKONOMIK_P,
        'canli_transfer': canli_auc is None or n_canli < 100 or canli_auc >= 0.5,
        'canli_kanit': n_canli >= MIN_CANLI_ESIK,
    }
    rapor.update({'adaylar': {k: {kk: vv for kk, vv in v.items() if kk != '_oos'} for k, v in sonuclar.items()},
                  'secilen': en_iyi, 'ekonomik': eko, 'canli_oos_auc': canli_auc, 'kapilar': kapilar,
                  'lineer_kiyas': sonuclar['lineer']['oos_auc'], 'esik': esik_bilgi})
    log(f"🧪 Seçilen {en_iyi}: engellenen %{ENGELLEME_ORANI * 100:.0f} -> ort. net getiri "
        f"{eko['ort_getiri_hepsi'] * 100:+.2f}% → {eko['ort_getiri_gecen'] * 100:+.2f}% (p={eko['p']:.3f}) | "
        f"canlı OOS AUC: {canli_auc if canli_auc is None else round(canli_auc, 3)}")

    if not kapilar['canli_kanit']:
        log(f"   canlı kanıt: {n_canli} tam kayıtlı canlı olay (gereken {MIN_CANLI_ESIK}); V18.4 kayıtları "
            f"biriktikçe dolar.")
    if not all(kapilar.values()):
        basarisiz = [k for k, v in kapilar.items() if not v]
        log(f"🛑 Kapılar geçilemedi ({', '.join(basarisiz)}): model KAYDEDİLMEDİ, eski model korunuyor.")
        rapor['karar'] = 'reddedildi'
        return rapor
    if kuru:
        log("🧪 --kuru: tüm kapılar geçti ama model kaydedilmedi.")
        rapor['karar'] = 'kuru_gecti'
        return rapor

    final = xgb_model(ADAYLAR[en_iyi])
    final.fit(X, y, sample_weight=df['w'].to_numpy())
    surum = f"v3-{datetime.datetime.now(datetime.timezone.utc):%Y%m%d%H%M}-{en_iyi}"
    kart = {'surum': surum, 'rol': 'core', 'yon': 'min', 'esik': esik_bilgi['esik'], 'ozellikler': featurelar,
            'hedef': 'Etiket_Getiri > 0', 'etiket_surumu': ETIKET_SURUMU,
            'hedef_engelleme_orani': ENGELLEME_ORANI,
            'dogrulama': {k: rapor[k] for k in ('adaylar', 'secilen', 'ekonomik', 'canli_oos_auc', 'kapilar', 'esik')},
            'veri': rapor['veri'], 'xgboost_surumu': xgb.__version__,
            'onem_gain': {k: round(v, 4) for k, v in final.get_booster().get_score(importance_type='gain').items()}}
    modeli_kartla_kaydet(final, CORE_MODEL, kart)
    log(f"✅ Core model yayına alındı: {surum} | OOS AUC {s['oos_auc']:.3f} | eşik {esik_bilgi['esik']:.3f} "
        f"({esik_bilgi['esik_kaynagi']}) | "
        f"{len(featurelar)} feature")
    rapor['karar'], rapor['surum'] = 'yayinda', surum
    if ESKI_FILTREYI_EMEKLI_ET and os.path.exists(FILTRE_MODEL):
        hedef = f"{FILTRE_MODEL}.emekli_{int(datetime.datetime.now().timestamp())}"
        os.replace(FILTRE_MODEL, hedef)
        log(f"🗄️ Doğrulanmamış eski filtre modeli emekliye ayrıldı -> {os.path.basename(hedef)}")
    return rapor


def main():
    ap = argparse.ArgumentParser(description="AI Trainer V3")
    ap.add_argument('--kuru', action='store_true', help='rapor üret, modeli kaydetme')
    a = ap.parse_args()
    rapor = egit([os.path.join(BASE_DIR, d) for d in ETIKET_DOSYALARI], kuru=a.kuru)
    try:
        atomik_metin_yaz(RAPOR_DOSYASI, json.dumps(rapor, ensure_ascii=False, indent=2, default=json_uyumlu))
    except OSError as e:
        log(f"⚠️ Rapor yazılamadı: {e}")


if __name__ == "__main__":
    main()
