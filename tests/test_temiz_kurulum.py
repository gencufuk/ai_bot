# -*- coding: utf-8 -*-
"""tools/temiz_kurulum.py: eski kurulumu arşivleyip V18.4'ü kurar; hiçbir şeyi silmez."""
import datetime
import json
import os
import shutil
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, 'tools'))
import temiz_kurulum as tk  # noqa: E402

V1_BASLIK = 'Islem_Zamani,Sembol,Sinyal,Kasa_Tipi,Giris_RSI,Giris_Vol_Oran,Giris_ATR_Pct,Giris_Fiyat,Cikis_Fiyat,' \
            'Kar_Orani,Net_Kar_USDT,Cikis_Tipi,Sure_Saat\n'


def _yaz(yol, icerik):
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    with open(yol, 'w', encoding='utf-8') as f:
        f.write(icerik)


@pytest.fixture
def sunucu(tmp_path, monkeypatch):
    kok = tmp_path / 'root'
    # sistem ve korunanlar
    _yaz(str(kok / '.env'), 'BINANCE_API_KEY=x\n')
    _yaz(str(kok / '.ssh' / 'authorized_keys'), 'ssh-ed25519 AAAA\n')
    _yaz(str(kok / '.bashrc'), '# bash\n')
    _yaz(str(kok / 'venv' / 'pyvenv.cfg'), 'home = /usr/bin\n')
    _yaz(str(kok / 'dump.rdb'), 'REDIS')
    _yaz(str(kok / 'yedek_2026-09-26.tgz'), 'tgz')
    _yaz(str(kok / 'baslat.sh'), '#!/bin/sh\n')
    _yaz(str(kok / 'baska_proje' / 'x.py'), 'print(1)\n')
    _yaz(str(kok / 'notlar.xyz'), 'not')
    # eski bot
    _yaz(str(kok / 'ai_bot.py'), '# CORE V18.3 eski\n')
    _yaz(str(kok / 'ai_trainer.py'), '# eski trainer\n')
    _yaz(str(kok / 'ai_trainer_history.log'), 'log\n')
    _yaz(str(kok / 'core_islem_verileri.csv'), V1_BASLIK + '2026-09-25 12:00:00,A/USDT,MSB,NORMAL,70,3,1,1,1.01,0.8,0.16,X,1\n')
    _yaz(str(kok / 'core_islem_verileri_v2.csv'), V1_BASLIK)
    _yaz(str(kok / 'shadow_sinyaller.csv'), 'Ts,Sembol\n1,A/USDT\n')
    _yaz(str(kok / 'shadow_sinyaller_etiketli.csv'), 'eski\n')
    _yaz(str(kok / 'core_xgboost_model.json'), json.dumps({'learner': {}}))
    _yaz(str(kok / 'filter_model.json'), json.dumps({'learner': {}}))
    _yaz(str(kok / 'core_islem_verileri.csv.legacy_123'), 'eski veri\n')
    _yaz(str(kok / 'nohup.out'), 'çıktı\n')
    _yaz(str(kok / '__pycache__' / 'a.pyc'), 'x')
    # paket (repodaki gerçek dosyalardan)
    paket = kok / 'ai_bot_v18_4_paket'
    for ad in tk.KOD + tk.GECMIS:
        kaynak = os.path.join(REPO, ad) if ad in tk.KOD else os.path.join(REPO, 'veri', ad)
        if os.path.isdir(kaynak):
            shutil.copytree(kaynak, str(paket / ad), ignore=shutil.ignore_patterns('__pycache__'))
        else:
            os.makedirs(str(paket), exist_ok=True)
            shutil.copy2(kaynak, str(paket / ad))
    _yaz(str(kok / 'ai_bot_v18_4_paket.zip'), 'zip')
    monkeypatch.setattr(tk, 'calisan_bot_surecleri', lambda *a, **k: [])
    monkeypatch.setattr(tk, 'crontab_metni', lambda: '')
    return str(kok), str(paket)


ZAMAN = datetime.datetime(2026, 9, 27, 10, 0, 0)


def _icerik(yol):
    with open(yol, encoding='utf-8') as f:
        return f.read()


def test_kuru_calistirma_hicbir_seyi_degistirmez(sunucu, capsys):
    kok, paket = sunucu
    once = sorted(os.listdir(kok))
    assert tk.kur(kok, paket, uygula=False, kontrol=False, simdi=ZAMAN) == 0
    assert sorted(os.listdir(kok)) == once
    cikti = capsys.readouterr().out
    assert 'ARŞİVE TAŞINACAK' in cikti and 'filter_model.json' in cikti and 'KURU ÇALIŞTIRMA' in cikti


def test_uygula_arsivler_kurar_ve_canli_veriyi_getirir(sunucu):
    kok, paket = sunucu
    eski_csv = _icerik(os.path.join(kok, 'core_islem_verileri.csv'))
    assert tk.kur(kok, paket, uygula=True, kontrol=False, simdi=ZAMAN) == 0
    arsiv = os.path.join(kok, 'eski_bot_20260927_100000')
    # dokunulmayanlar yerinde
    for ad in ('.env', '.ssh', '.bashrc', 'venv', 'dump.rdb', 'yedek_2026-09-26.tgz', 'baslat.sh', 'baska_proje',
               'notlar.xyz', 'ai_bot_v18_4_paket', 'ai_bot_v18_4_paket.zip'):
        assert os.path.exists(os.path.join(kok, ad)), ad
    # yeni kod kurulu
    assert 'V18.4' in _icerik(os.path.join(kok, 'ai_bot.py'))
    assert os.path.exists(os.path.join(kok, 'sniper', 'risk_motoru.py'))
    assert os.path.exists(os.path.join(kok, 'tools', 'temiz_kurulum.py'))
    for ad in tk.GECMIS:
        assert os.path.exists(os.path.join(kok, ad))
    # canlı veri geri geldi, asılları arşivde
    assert _icerik(os.path.join(kok, 'core_islem_verileri.csv')) == eski_csv
    for ad in ('core_islem_verileri.csv', 'core_islem_verileri_v2.csv', 'shadow_sinyaller.csv', 'core_xgboost_model.json'):
        assert os.path.exists(os.path.join(kok, ad)) and os.path.exists(os.path.join(arsiv, ad)), ad
    # getirilmeyenler yalnız arşivde
    for ad in ('filter_model.json', 'shadow_sinyaller_etiketli.csv', 'ai_trainer_history.log', 'nohup.out',
               'core_islem_verileri.csv.legacy_123', '__pycache__'):
        assert not os.path.exists(os.path.join(kok, ad)) and os.path.exists(os.path.join(arsiv, ad)), ad
    assert _icerik(os.path.join(arsiv, 'ai_bot.py')) == '# CORE V18.3 eski\n'
    manifest = json.loads(_icerik(os.path.join(arsiv, 'kurulum_manifest.json')))
    assert 'core_xgboost_model.json' in manifest['geri_getirilen'] and not manifest['reddedilen']


def test_csv_adli_model_dosyasi_geri_getirilmez(sunucu, capsys):
    kok, paket = sunucu
    _yaz(os.path.join(kok, 'core_islem_verileri_v2.csv'), json.dumps({'learner': {}}))
    assert tk.kur(kok, paket, uygula=True, kontrol=False, simdi=ZAMAN) == 0
    assert not os.path.exists(os.path.join(kok, 'core_islem_verileri_v2.csv'))
    assert 'JSON içeriyor' in capsys.readouterr().out


def test_bot_calisiyorsa_uygulamaz(sunucu, monkeypatch):
    kok, paket = sunucu
    monkeypatch.setattr(tk, 'calisan_bot_surecleri', lambda *a, **k: ['1234 /root/venv/bin/python ai_bot.py'])
    once = sorted(os.listdir(kok))
    assert tk.kur(kok, paket, uygula=True, kontrol=False, simdi=ZAMAN) == 3
    assert sorted(os.listdir(kok)) == once
    assert tk.kur(kok, paket, uygula=False, kontrol=False, simdi=ZAMAN) == 0     # plan yine gösterilir


def test_paketin_kendisinden_calistirilmali(sunucu):
    kok, paket = sunucu
    assert tk.kur(paket, paket, uygula=True, kontrol=False) == 2


def test_geri_al_eski_kurulumu_getirir_yeni_veriyi_kaybetmez(sunucu):
    kok, paket = sunucu
    assert tk.kur(kok, paket, uygula=True, kontrol=False, simdi=ZAMAN) == 0
    arsiv = os.path.join(kok, 'eski_bot_20260927_100000')
    with open(os.path.join(kok, 'core_islem_verileri.csv'), 'a', encoding='utf-8') as f:   # V18.4 yeni işlem yazdı
        f.write('2026-09-28 09:00:00,B/USDT,MSB,NORMAL,70,3,1,1,1.02,1.8,0.36,Y,1\n')
    _yaz(os.path.join(kok, 'etiketli_sinyaller.csv'), 'yeni\n')
    assert tk.geri_al(kok, arsiv, simdi=datetime.datetime(2026, 9, 29)) == 0
    assert _icerik(os.path.join(kok, 'ai_bot.py')) == '# CORE V18.3 eski\n'
    assert os.path.exists(os.path.join(kok, 'filter_model.json')) and not os.path.exists(arsiv)
    kaldirilan = os.path.join(kok, 'v184_kaldirilan_20260929_000000')
    assert 'B/USDT' in _icerik(os.path.join(kaldirilan, 'core_islem_verileri.csv'))
    assert os.path.exists(os.path.join(kaldirilan, 'etiketli_sinyaller.csv'))
    assert 'V18.4' in _icerik(os.path.join(kaldirilan, 'ai_bot.py'))
    for ad in ('.env', 'venv', 'dump.rdb', 'baska_proje'):
        assert os.path.exists(os.path.join(kok, ad))


def test_geri_al_yalniz_bu_aracin_arsivini_kabul_eder(tmp_path):
    (tmp_path / 'rastgele').mkdir()
    assert tk.geri_al(str(tmp_path), str(tmp_path / 'rastgele')) == 2


def test_cron_ve_baska_botun_dosyalarina_dokunmaz(sunucu, monkeypatch):
    kok, paket = sunucu
    _yaz(os.path.join(kok, 'ufuk_bot.py'), '# ikinci bot\n')
    _yaz(os.path.join(kok, 'ufuk.log'), 'log\n')
    _yaz(os.path.join(kok, 'ufuk_islem_verileri.csv'), V1_BASLIK)
    monkeypatch.setattr(tk, 'crontab_metni', lambda: (
        "*/5 * * * * /root/venv/bin/python /root/ufuk_bot.py >> /root/ufuk.log 2>&1\n"
        "0 3 * * * cd /root && /root/venv/bin/python ai_trainer.py\n"))
    assert tk.kur(kok, paket, uygula=True, kontrol=False, simdi=ZAMAN) == 0
    for ad in ('ufuk_bot.py', 'ufuk.log', 'ufuk_islem_verileri.csv'):
        assert os.path.exists(os.path.join(kok, ad)), ad
    assert 'AI TRAINER V3' in _icerik(os.path.join(kok, 'ai_trainer.py'))      # cron'daki kendi dosyamız yenilenir


def test_calisan_surec_tespiti(monkeypatch):
    class Sonuc:
        stdout = ("  123 /root/venv/bin/python /root/ufuk_bot.py\n"
                  "  456 python3 ai_bot_v18_4_paket/tools/temiz_kurulum.py --uygula\n"
                  "  789 /usr/bin/python3 /opt/baska.py\n"
                  " 1011 /root/venv/bin/python ai_bot.py\n")
    monkeypatch.setattr(tk.subprocess, 'run', lambda *a, **k: Sonuc())
    bulunan = tk.calisan_bot_surecleri({'ufuk_bot.py'})
    assert bulunan == ['123 /root/venv/bin/python /root/ufuk_bot.py', '1011 /root/venv/bin/python ai_bot.py']


def test_cron_log_dosyalari_ve_cron_onerisi(sunucu, monkeypatch, capsys):
    kok, paket = sunucu
    _yaz(os.path.join(kok, 'ai_trainer.log'), 'eski log\n')
    cron = ("0 3 * * * /root/venv/bin/python /root/ai_trainer.py >> /root/ai_trainer.log 2>&1\n"
            "45 2 * * * /root/venv/bin/python /root/shadow_labeler.py >> /root/shadow_labeler.log 2>&1\n")
    monkeypatch.setattr(tk, 'crontab_metni', lambda: cron)
    assert tk.kur(kok, paket, uygula=True, kontrol=False, simdi=ZAMAN) == 0
    cikti = capsys.readouterr().out
    assert os.path.exists(os.path.join(kok, 'ai_trainer.log'))
    assert 'cron işinizin log dosyası' in cikti and 'yeni satır EKLEMEYİN' in cikti
    assert 'EKLEMEYİN' not in tk.cron_onerisi('0 3 * * * python /root/ai_trainer.py', '/root')
    assert 'shadow_labeler.py' in tk.cron_onerisi('', '/root') and 'ai_trainer.py' in tk.cron_onerisi('', '/root')


def test_guncelle_yalniz_kodu_yeniler(sunucu):
    kok, paket = sunucu
    assert tk.kur(kok, paket, uygula=True, kontrol=False, simdi=ZAMAN) == 0
    with open(os.path.join(kok, 'core_islem_verileri_v2.csv'), 'a', encoding='utf-8') as f:
        f.write('2026-09-28 09:00:00,B/USDT,MSB,NORMAL,70,3,1,1,1.02,1.8,0.36,Y,1\n')     # bot yeni işlem yazdı
    once_veri = {ad: _icerik(os.path.join(kok, ad)) for ad in ('core_islem_verileri_v2.csv', 'shadow_sinyaller.csv')}
    _yaz(os.path.join(paket, 'ai_bot.py'), '# CORE V18.4 yeni surum\n')
    assert tk.guncelle(kok, paket, simdi=datetime.datetime(2026, 9, 28, 10)) == 0
    assert _icerik(os.path.join(kok, 'ai_bot.py')) == '# CORE V18.4 yeni surum\n'
    eski = os.path.join(kok, 'eski_kod_20260928_100000')
    assert 'V18.4' in _icerik(os.path.join(eski, 'ai_bot.py')) and os.path.exists(os.path.join(eski, 'sniper'))
    assert {ad: _icerik(os.path.join(kok, ad)) for ad in once_veri} == once_veri          # veri aynen duruyor
    assert os.path.exists(os.path.join(kok, 'sniper', 'risk_motoru.py'))


def test_guncelle_bot_calisirken_ve_kurulum_yokken_reddeder(sunucu, monkeypatch, tmp_path):
    kok, paket = sunucu
    assert tk.guncelle(str(tmp_path), paket) == 2                       # kurulu bot yok
    monkeypatch.setattr(tk, 'calisan_bot_surecleri', lambda *a, **k: ['1 python3 -u ai_bot.py'])
    assert tk.guncelle(kok, paket) == 3


def test_gecmis_dosyasiz_paket_kurulur(sunucu):
    kok, paket = sunucu
    for ad in tk.GECMIS:
        os.remove(os.path.join(paket, ad))
    assert tk.kur(kok, paket, uygula=True, kontrol=False, simdi=ZAMAN) == 0
    assert 'V18.4' in _icerik(os.path.join(kok, 'ai_bot.py'))
    assert not any(os.path.exists(os.path.join(kok, ad)) for ad in tk.GECMIS)
