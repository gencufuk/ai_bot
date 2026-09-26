# -*- coding: utf-8 -*-
import random

import pytest

from sniper import risk_motoru as rm
from sniper.risk_motoru import Pozisyon, RiskAyarlari, seviyeleri_hesapla, kararlar

A = RiskAyarlari()
SIMDI = 1_800_000_000.0


def fiyat_icin(giris, hedef_oran, fee=0.001):
    """net_oran(giris, fiyat) == hedef_oran olacak fiyat."""
    return (hedef_oran * giris * (1 + fee) + giris * (1 + fee)) / (1 - fee)


def poz(**kw):
    d = dict(sembol='X/USDT', giris=1.0, max_kar=0.0, half_sold=False,
             giris_zamani=SIMDI - 600, atr_pct=1.5, is_whale=False)
    d.update(kw)
    return Pozisyon(**d)


def calis(p, oran, rejim='TREND', rsi_tetik=False, simdi=SIMDI):
    sev = seviyeleri_hesapla(p, fiyat_icin(p.giris, oran), A)
    return sev, kararlar(p, sev, simdi, rejim, A, rsi_tetik)


# --------------------------------------------------------------------------------------
# V18.3 referansı: ai_bot.py vip_cuzdan_loop'taki karar mantığının birebir kopyası
# (satışların hep gerçekleşebildiği varsayımıyla; I/O yerine karar etiketi döner)
# --------------------------------------------------------------------------------------
def v183_karar(a_fiyat, m_k, tick, half_sold, gecen_saat, atr_pct, is_w, rsi_tetik, rejim, mom_zamani):
    FEE_RATE, ZARAR_ORANI_BALINA, KAR_KILIDI_ORAN = 0.001, 0.020, 0.010
    MAX_BEKLEME_SAATI, MIN_BEKLENTI_ORANI, MOMENTUM_OLU_SAAT = 4.0, 0.005, 1.5
    a = a_fiyat
    oran = ((tick * (1 - FEE_RATE)) - (a * (1 + FEE_RATE))) / (a * (1 + FEE_RATE))
    if oran > m_k: m_k = oran
    dinamik_stop = max(0.025, min(0.055, (atr_pct * 1.5) / 100))
    aktif_zarar_orani = ZARAR_ORANI_BALINA if is_w else dinamik_stop
    ilk_esik = max(0.03, min(0.06, (atr_pct * 2.0) / 100))
    if m_k >= 0.025: base_stop = KAR_KILIDI_ORAN
    else: base_stop = -aktif_zarar_orani
    if rsi_tetik and not half_sold:
        return ('MOON',)
    if m_k >= 0.20: kismi, cikis = m_k - 0.05, max(base_stop, m_k - 0.10)
    elif m_k >= 0.10: kismi, cikis = m_k - 0.02, max(base_stop, m_k - 0.05)
    elif m_k >= ilk_esik: kismi, cikis = m_k - 0.015, max(base_stop, m_k - 0.03)
    else: kismi, cikis = 999.0, (0.005 if half_sold else base_stop)
    if m_k >= ilk_esik and oran <= kismi and not half_sold and not rsi_tetik:
        return ('KISMI',)
    if oran <= cikis:
        if m_k >= ilk_esik: exit_msg = "📈 TREND TAKİPLİ ÇIKIŞ"
        elif half_sold: exit_msg = "🛡️ GÜVENLİ ÇIKIŞ"
        elif m_k >= 0.025: exit_msg = "🔒 KÂR KİLİDİ (+%1)"
        else: exit_msg = f"🛑 STOP LOSS (%-{aktif_zarar_orani*100:.1f})"
        return ('EXIT', exit_msg)
    elif gecen_saat >= MAX_BEKLEME_SAATI and not half_sold:
        if oran < MIN_BEKLENTI_ORANI: return ('EXIT', "⏳ ZAMAN AŞIMI")
        return ('UZAT',)
    elif (rejim == "YATAY" and not half_sold and gecen_saat >= MOMENTUM_OLU_SAAT
          and abs(oran) < 0.01 and mom_zamani):
        return ('MOM',)
    return ('NONE',)


def yeni_ilk_karar(eylemler):
    if not eylemler:
        return ('NONE',)
    e = eylemler[0]
    return {rm.MOON_BAG: ('MOON',), rm.KISMI_KAR: ('KISMI',), rm.ZAMAN_UZAT: ('UZAT',),
            rm.MOMENTUM_KONTROL: ('MOM',)}.get(e.tip, ('EXIT', e.mesaj))


def bug_duzeltmesi_devrede(p, sev):
    return p.half_sold and sev.max_kar < sev.ilk_esik and sev.base_stop > A.yarim_sonrasi_taban


def test_diferansiyel_v183_ile_ayni_karar():
    rnd = random.Random(1234)
    fark_bug_disi, fark_bug = 0, 0
    for _ in range(200_000):
        giris = 10 ** rnd.uniform(-6, 3)
        m_k = rnd.choice([0.0, rnd.uniform(-0.01, 0.30)])
        oran = rnd.uniform(-0.08, 0.30)
        tick = fiyat_icin(giris, oran)
        half = rnd.random() < 0.3
        saat = rnd.choice([rnd.uniform(0, 1.4), rnd.uniform(1.5, 3.9), rnd.uniform(4.0, 8.0)])
        atr = rnd.uniform(0.3, 3.5)
        whale = rnd.random() < 0.15
        rsi = rnd.random() < 0.2
        rejim = rnd.choice(['TREND', 'YATAY'])
        mom_zamani = rnd.random() < 0.5
        p = Pozisyon('X/USDT', giris, max_kar=m_k, half_sold=half, giris_zamani=SIMDI - saat * 3600,
                     atr_pct=atr, is_whale=whale, son_mom_kontrol=(0.0 if mom_zamani else SIMDI - 10))
        sev = seviyeleri_hesapla(p, tick, A)
        yeni = yeni_ilk_karar(kararlar(p, sev, SIMDI, rejim, A, rsi))
        eski = v183_karar(giris, m_k, tick, half, saat, atr, whale, rsi, rejim, mom_zamani)
        if yeni != eski:
            if bug_duzeltmesi_devrede(p, sev):
                fark_bug += 1
            else:
                fark_bug_disi += 1
                pytest.fail(f"beklenmeyen fark: yeni={yeni} eski={eski} p={p} oran={oran}")
    assert fark_bug_disi == 0
    assert fark_bug > 0   # düzeltme gerçekten bazı durumları değiştiriyor


def test_normal_stop_loss():
    _, e = calis(poz(atr_pct=1.5), -0.026)
    assert e[0].tip == rm.TAM_CIKIS and e[0].mesaj == "🛑 STOP LOSS (%-2.5)"
    _, e = calis(poz(atr_pct=1.5), -0.024)
    assert e == []


def test_atr_olcekli_stop_ve_balina():
    _, e = calis(poz(atr_pct=3.0), -0.04)          # stop %4.5
    assert e == []
    _, e = calis(poz(atr_pct=3.0), -0.046)
    assert e[0].mesaj == "🛑 STOP LOSS (%-4.5)"
    _, e = calis(poz(atr_pct=3.0, is_whale=True), -0.021)
    assert e[0].mesaj == "🛑 STOP LOSS (%-2.0)"


def test_kar_kilidi():
    p = poz(atr_pct=1.8, max_kar=0.030)             # ilk eşik %3.6, kilit devrede
    _, e = calis(p, 0.011)
    assert e == []
    _, e = calis(p, 0.0099)
    assert e[0].tip == rm.TAM_CIKIS and e[0].mesaj == rm.MSG_KILIT


def test_bug_yarim_satis_sonrasi_kilit_ezilmez():
    """Moon bag %5.2'de yarıyı sattı, ATR %2.8 -> ilk eşik %5.6 > max kâr.
    V18.3 kalan yarıyı +%0.5'e kadar bekliyordu (kilit +%1.0 ezildi)."""
    p = poz(atr_pct=2.8, max_kar=0.052, half_sold=True)
    sev, e = calis(p, 0.008)
    assert sev.cikis == pytest.approx(0.010)
    assert e[0].tip == rm.TAM_CIKIS and e[0].mesaj == rm.MSG_GUVENLI
    eski = v183_karar(1.0, 0.052, fiyat_icin(1.0, 0.008), True, 0.2, 2.8, False, False, 'TREND', False)
    assert eski == ('NONE',)   # eski kod burada pozisyonu tutmaya devam ediyordu


def test_kismi_kar_ve_gap_down():
    p = poz(atr_pct=1.5, max_kar=0.050)             # ilk eşik %3, kismi %3.5, çıkış %2
    _, e = calis(p, 0.034)
    assert [x.tip for x in e] == [rm.KISMI_KAR] and e[0].oran == 0.5
    _, e = calis(p, 0.015)                          # tek pollda iki seviyeyi birden kırdı
    assert [x.tip for x in e] == [rm.KISMI_KAR, rm.TAM_CIKIS]
    assert e[1].mesaj == rm.MSG_TREND


def test_moon_bag_kismi_kari_bastirir():
    p = poz(atr_pct=1.5, max_kar=0.07)
    _, e = calis(p, 0.054, rsi_tetik=True)
    assert [x.tip for x in e] == [rm.MOON_BAG]
    _, e = calis(poz(atr_pct=1.5, max_kar=0.07, half_sold=True), 0.054, rsi_tetik=True)
    assert all(x.tip != rm.MOON_BAG for x in e)


def test_ust_kademeler():
    sev, _ = calis(poz(max_kar=0.12), 0.11)
    assert sev.kismi == pytest.approx(0.10) and sev.cikis == pytest.approx(0.07)
    sev, _ = calis(poz(max_kar=0.25), 0.24)
    assert sev.kismi == pytest.approx(0.20) and sev.cikis == pytest.approx(0.15)


def test_zaman_asimi_ve_uzatma_referansi():
    p = poz(giris_zamani=SIMDI - 4.1 * 3600)
    _, e = calis(p, 0.001)
    assert e[0].tip == rm.TAM_CIKIS and e[0].mesaj == rm.MSG_ZAMAN
    _, e = calis(p, 0.006)
    assert e[0].tip == rm.ZAMAN_UZAT
    # uzatıldıktan sonra zaman_ref yeni referanstır, giriş zamanı değişmez
    p2 = poz(giris_zamani=SIMDI - 5 * 3600, zaman_ref=SIMDI - 3600)
    _, e = calis(p2, 0.001)
    assert e == []


def test_momentum_sadece_yatay_rejimde():
    p = poz(giris_zamani=SIMDI - 1.6 * 3600)
    _, e = calis(p, 0.002, rejim='YATAY')
    assert e[0].tip == rm.MOMENTUM_KONTROL
    _, e = calis(p, 0.002, rejim='TREND')
    assert e == []
    p.son_mom_kontrol = SIMDI - 100
    _, e = calis(p, 0.002, rejim='YATAY')
    assert e == []


def test_rsi_kontrolu_gerekli():
    p = poz()
    sev = seviyeleri_hesapla(p, fiyat_icin(1.0, 0.051), A)
    assert rm.rsi_kontrolu_gerekli(p, sev, SIMDI, A)
    p.son_rsi_kontrol = SIMDI - 30
    assert not rm.rsi_kontrolu_gerekli(p, sev, SIMDI, A)


def test_momentum_oldu_mu():
    assert rm.momentum_oldu_mu([100] * 11 + [50, 50, 50]) is True
    assert rm.momentum_oldu_mu([100] * 14) is False
    assert rm.momentum_oldu_mu([100] * 5) is None
    assert rm.momentum_oldu_mu([0] * 14) is None


def test_net_oran_formulu_ai_bot_ile_ayni():
    giris, cikis, fee = 0.918510138248848, 0.9348, 0.001
    beklenen = (cikis * (1 - fee) - giris * (1 + fee)) / (giris * (1 + fee))
    assert rm.net_oran(giris, cikis, fee) == pytest.approx(beklenen, rel=1e-15)
