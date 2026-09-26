# -*- coding: utf-8 -*-
"""tools/veri_birlestir.py: tek işlem kaydı + tek (paylaşılan) eğitim dosyası."""
import csv
import datetime
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, 'tools'))
import veri_birlestir as vb  # noqa: E402

ZAMAN = datetime.datetime(2026, 9, 27, 12, 0, 0)
V2_BASLIK = vb.V1 + ['Pump_3s', 'Pozisyon_Id', 'Giris_Ts', 'Cikis_Ts']


def _yaz(yol, baslik, satirlar):
    with open(yol, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        if baslik:
            w.writerow(baslik)
        w.writerows(satirlar)


def _oku(yol):
    with open(yol, encoding='utf-8', newline='') as f:
        return list(csv.DictReader(f))


def _islem(zaman, sembol, cikis='📈 TREND TAKİPLİ ÇIKIŞ', ek=()):
    return [zaman, sembol, 'MSB', 'NORMAL', 70, 3, 1.2, 1.0, 1.02, 1.8, 0.36, cikis, 1.5] + list(ek)


def _etiket(anahtar, kaynak='backfill', sonuc='TP', surum=None):
    r = {k: '' for k in vb.TUM_KOLONLAR}
    r.update({'Anahtar': anahtar, 'Kaynak': kaynak, 'Ts': '1', 'Sembol': 'A/USDT', 'Etiket_Surumu': surum or vb.ETIKET_SURUMU,
              'Etiket_Sonuc': sonuc, 'Etiket_Getiri': '' if sonuc == vb.VERI_YOK else '0.03'})
    return [r[k] for k in vb.TUM_KOLONLAR]


@pytest.fixture
def kok(tmp_path, monkeypatch):
    monkeypatch.setattr(vb.tk, 'calisan_bot_surecleri', lambda *a, **k: [])
    # hedef V2 kaydı: 21-25 Eylül (bot yazıyor)
    _yaz(tmp_path / 'core_islem_verileri_v2.csv', V2_BASLIK, [
        _islem('2026-09-21 00:41:56.042043', 'SUI/USDT', ek=(4.0, 'SUI/USDT|1', '', '')),
        _islem('2026-09-25 12:16:05.896999', 'ENA/USDT', ek=(2.0, 'ENA/USDT|2', '', ''))])
    # V1: biri V2'de zaten var, biri yok
    _yaz(tmp_path / 'core_islem_verileri.csv', vb.V1, [
        _islem('2026-09-21 00:41:56.042043', 'SUI/USDT'),
        _islem('2026-09-23 10:00:00.000001', 'XRP/USDT')])
    # kurtarılmış geçmiş: V2 dosyasında olmayan bir kolon (AI_Skor) içeriyor
    _yaz(tmp_path / 'core_islem_verileri_v2_gecmis.onarildi.csv', vb.V1 + ['AI_Skor'], [
        _islem('2026-08-05 05:51:51.401104', 'BICO/USDT', ek=(0.8,)),
        _islem('2026-06-01 10:00:00.000000', 'TON/USDT', ek=(0.7,))])
    _yaz(tmp_path / 'etiketli_sinyaller.csv', vb.TUM_KOLONLAR, [_etiket('C|a', 'core'), _etiket('S|b', 'shadow')])
    _yaz(tmp_path / 'backfill_sinyaller.csv', vb.TUM_KOLONLAR, [_etiket('B|1'), _etiket('B|2'), _etiket('S|b', 'shadow')])
    return tmp_path


def test_kuru_calistirma_dosyalara_dokunmaz(kok, capsys):
    once = {p: open(kok / p, 'rb').read() for p in os.listdir(kok)}
    assert vb.calistir(str(kok), simdi=ZAMAN) == 0
    assert {p: open(kok / p, 'rb').read() for p in os.listdir(kok)} == once
    cikti = capsys.readouterr().out
    assert 'KURU ÇALIŞTIRMA' in cikti and 'sonuç: 5 satır' in cikti and 'sonuç: 4 satır' in cikti


def test_birlestirme_tek_islem_kaydi_ve_tek_egitim_dosyasi(kok):
    assert vb.calistir(str(kok), uygula=True, simdi=ZAMAN) == 0
    islem = _oku(kok / 'core_islem_verileri_v2.csv')
    # 2 mevcut + 1 yeni V1 + 2 geçmiş; SUI tekrarı yok; kronolojik
    assert [r['Sembol'] for r in islem] == ['TON/USDT', 'BICO/USDT', 'SUI/USDT', 'XRP/USDT', 'ENA/USDT']
    assert islem[2]['Pump_3s'] == '4.0'                                   # V2'deki dolu kayıt korunur
    assert islem[1]['AI_Skor'] == '0.8' and 'AI_Skor' in islem[0]         # yeni kolon sona eklendi, veri kaybı yok
    egitim = _oku(kok / 'etiketli_sinyaller.csv')
    assert sorted(r['Anahtar'] for r in egitim) == ['B|1', 'B|2', 'C|a', 'S|b']
    arsiv = kok / 'veri_arsiv_20260927_120000'
    assert sorted(os.listdir(arsiv)) == ['backfill_sinyaller.csv', 'core_islem_verileri.csv',
                                         'core_islem_verileri_v2_gecmis.onarildi.csv']
    assert sorted(p for p in os.listdir(kok) if p.endswith('.csv')) == ['core_islem_verileri_v2.csv',
                                                                         'etiketli_sinyaller.csv']
    # ikinci çalıştırma: birleştirilecek bir şey kalmadı, veri değişmez
    assert vb.calistir(str(kok), uygula=True, simdi=ZAMAN + datetime.timedelta(minutes=1)) == 0
    assert len(_oku(kok / 'core_islem_verileri_v2.csv')) == 5 and len(_oku(kok / 'etiketli_sinyaller.csv')) == 4


def test_baska_makineden_egitim_verisi_ekle(kok, tmp_path_factory):
    disari = tmp_path_factory.mktemp('arkadas') / 'etiketli_sinyaller.csv'
    _yaz(disari, vb.TUM_KOLONLAR, [_etiket('C|arkadas1', 'core'), _etiket('C|a', 'core')])
    gelen = kok / 'gelen_etiketli.csv'
    _yaz(gelen, vb.TUM_KOLONLAR, [_etiket('C|arkadas2', 'core'), _etiket('S|b', 'shadow')])
    once_islem = open(kok / 'core_islem_verileri_v2.csv', 'rb').read()
    assert vb.calistir(str(kok), ekle=[str(disari), str(gelen)], uygula=True, simdi=ZAMAN) == 0
    anahtarlar = sorted(r['Anahtar'] for r in _oku(kok / 'etiketli_sinyaller.csv'))
    assert anahtarlar == ['C|a', 'C|arkadas1', 'C|arkadas2', 'S|b']
    assert open(kok / 'core_islem_verileri_v2.csv', 'rb').read() == once_islem     # işlem kaydına dokunulmaz
    assert (kok / 'backfill_sinyaller.csv').exists()                               # --ekle yalnız verilen dosyalar
    assert not gelen.exists() and (kok / 'veri_arsiv_20260927_120000' / 'gelen_etiketli.csv').exists()
    assert disari.exists()                                                         # bot klasörü dışına dokunulmaz


def test_guncel_ve_gercek_etiket_tercih_edilir(kok):
    _yaz(kok / 'etiketli_sinyaller.csv', vb.TUM_KOLONLAR, [_etiket('C|a', 'core', sonuc=vb.VERI_YOK),
                                                           _etiket('S|b', 'shadow', surum='eski-v0')])
    _yaz(kok / 'backfill_sinyaller.csv', vb.TUM_KOLONLAR, [_etiket('C|a', 'core'), _etiket('S|b', 'shadow')])
    assert vb.calistir(str(kok), uygula=True, simdi=ZAMAN) == 0
    egitim = {r['Anahtar']: r for r in _oku(kok / 'etiketli_sinyaller.csv')}
    assert egitim['C|a']['Etiket_Sonuc'] == 'TP' and egitim['S|b']['Etiket_Surumu'] == vb.ETIKET_SURUMU


def test_kaymis_kaynak_atlanir_digerleri_birlesir(kok, capsys):
    with open(kok / 'core_islem_verileri.csv', 'a', encoding='utf-8') as f:
        f.write('2026-09-24 00:00:00,BOZUK/USDT,MSB\n')                         # kaymış satır
    assert vb.calistir(str(kok), uygula=True, simdi=ZAMAN) == 0
    assert (kok / 'core_islem_verileri.csv').exists()                            # arşivlenmedi
    assert 'ATLANDI' in capsys.readouterr().out
    assert len(_oku(kok / 'core_islem_verileri_v2.csv')) == 4                      # 2 mevcut + 2 geçmiş


def test_basliksiz_eski_v1_okunur(tmp_path):
    _yaz(tmp_path / 'v1.csv', None, [_islem('2026-06-01 10:00:00', 'A/USDT')])
    baslik, satirlar = vb.csv_oku(str(tmp_path / 'v1.csv'))
    assert baslik == vb.V1 and satirlar[0]['Sembol'] == 'A/USDT'


def test_bot_calisirken_islem_kaydi_birlestirilmez(kok, monkeypatch):
    monkeypatch.setattr(vb.tk, 'calisan_bot_surecleri', lambda *a, **k: ['123 /root/venv/bin/python -u ai_bot.py'])
    once = open(kok / 'core_islem_verileri_v2.csv', 'rb').read()
    assert vb.calistir(str(kok), uygula=True, simdi=ZAMAN) == 3
    assert open(kok / 'core_islem_verileri_v2.csv', 'rb').read() == once
    # eğitim verisi almak bot çalışırken de olur
    gelen = kok / 'gelen.csv'
    _yaz(gelen, vb.TUM_KOLONLAR, [_etiket('C|yeni', 'core')])
    assert vb.calistir(str(kok), ekle=[str(gelen)], uygula=True, simdi=ZAMAN) == 0
