# -*- coding: utf-8 -*-
import csv
import os
import subprocess
import sys
import time

import numpy as np
import pandas as pd
import pytest

from sniper.csv_kayit import satir_ekle, hucre, baslik_gibi

V1 = ['Islem_Zamani', 'Sembol', 'Sinyal', 'Kasa_Tipi', 'Giris_RSI', 'Giris_Vol_Oran', 'Giris_ATR_Pct',
      'Giris_Fiyat', 'Cikis_Fiyat', 'Kar_Orani', 'Net_Kar_USDT', 'Cikis_Tipi', 'Sure_Saat']
LEGACY = ("2026-06-01 10:00:00,ABC/USDT,MSB,NORMAL,65.1,3.2,1.5,0.5,0.51,2.0,0.4,DİNAMİK KISMİ KÂR,1.2\n"
          "2026-06-01 12:00:00,DEF/USDT,MSB,NORMAL,70.2,2.9,1.1,1.5,1.46,-2.6,-0.52,🛑 STOP LOSS (%-2.5),0.4\n"
          "2026-06-02 09:00:00,GHI/USDT,Engulf,BALİNA,60.0,4.0,0.9,3.0,3.1,3.1,1.2,📈 TREND TAKİPLİ ÇIKIŞ,2.0\n")
YENI = dict(zip(V1, ['2026-09-21 00:41:56', 'SUI/USDT', 'MSB', 'NORMAL', 68.3, 3.25, 1.27, 0.9185, 0.9348,
                     1.57, 0.156, 'DİNAMİK KISMİ KÂR', 0.45]))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def oku(yol):
    with open(yol, encoding='utf-8', newline='') as f:
        return list(csv.reader(f))


def eski_append_csv(file_name, row_dict, columns):
    """ai_bot.py V18.3'teki _append_csv'nin birebir kopyası (karakterizasyon için)."""
    df = pd.DataFrame([row_dict]).reindex(columns=columns)
    if not os.path.isfile(file_name):
        df.to_csv(file_name, index=False, header=True); return
    try:
        with open(file_name, encoding='utf-8') as f: mevcut = f.readline().strip().split(',')
        if mevcut != columns:
            pd.read_csv(file_name).reindex(columns=columns).to_csv(file_name, index=False, header=True)
    except Exception:
        os.replace(file_name, f"{file_name}.bozuk_{int(time.time())}")
        df.to_csv(file_name, index=False, header=True); return
    df.to_csv(file_name, mode='a', index=False, header=False)


def test_eski_fonksiyon_basliksiz_v1_verisini_yok_ediyordu(tmp_path):
    """21 Eylül'deki 512 -> 4 pozisyon düşüşünün mekanizması."""
    yol = tmp_path / 'core.csv'
    yol.write_text(LEGACY, encoding='utf-8')
    eski_append_csv(str(yol), YENI, V1)
    govde = oku(yol)[1:]
    assert len(govde) == 3                                    # 3 eski satırın 1'i başlık diye yutuldu
    assert govde[0] == [''] * 13 and govde[1] == [''] * 13    # kalanlar tamamen boşaltıldı
    assert govde[2][1] == 'SUI/USDT'


def test_basliksiz_v1_dosyaya_baslik_eklenir_veri_korunur(tmp_path):
    yol = tmp_path / 'core.csv'
    yol.write_text(LEGACY, encoding='utf-8')
    uyarilar = []
    satir_ekle(str(yol), YENI, V1, uyari=uyarilar.append)
    s = oku(yol)
    assert s[0] == V1
    assert [r[1] for r in s[1:]] == ['ABC/USDT', 'DEF/USDT', 'GHI/USDT', 'SUI/USDT']
    assert s[2][11] == '🛑 STOP LOSS (%-2.5)'
    assert uyarilar and 'başlık eklendi' in uyarilar[0]
    df = pd.read_csv(yol)
    assert df['Net_Kar_USDT'].tolist() == [0.4, -0.52, 1.2, 0.156]


def test_basliksiz_tutarsiz_dosya_aynen_arsivlenir(tmp_path):
    yol = tmp_path / 'core.csv'
    icerik = "2026-05-01 10:00:00,OLD/USDT,MSB,65.1,3.2\n" + LEGACY
    yol.write_text(icerik, encoding='utf-8')
    uyarilar = []
    satir_ekle(str(yol), YENI, V1, uyari=uyarilar.append)
    arsivler = [p for p in os.listdir(tmp_path) if '.legacy_' in p]
    assert len(arsivler) == 1
    assert (tmp_path / arsivler[0]).read_text(encoding='utf-8') == icerik   # bayt bayt aynı
    s = oku(yol)
    assert s[0] == V1 and len(s) == 2 and s[1][1] == 'SUI/USDT'
    assert uyarilar and 'AYNEN arşivlendi' in uyarilar[0]


def test_sema_genisletme_eski_satirlari_kaydirmaz(tmp_path):
    yol = tmp_path / 'v2.csv'
    eski_kol = V1 + ['Pump_3s', 'AI_Skor']
    with open(yol, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(eski_kol)
        w.writerow(['t0', 'X/USDT', 'MSB', 'NORMAL', 60, 3, 1, 1, 1.1, 10, 2, 'TP', 1, 5.5, 0.7])
    yeni_kol = V1 + ['Pump_3s', 'Sym_ADX', 'AI_Skor']
    satir_ekle(str(yol), dict(YENI, Pump_3s=1.0, Sym_ADX=30.0, AI_Skor=0.2), yeni_kol)
    df = pd.read_csv(yol)
    assert list(df.columns) == eski_kol + ['Sym_ADX']          # yeni kolon SONA eklendi
    assert df.loc[0, 'AI_Skor'] == 0.7 and pd.isna(df.loc[0, 'Sym_ADX'])
    assert df.loc[1, 'Sym_ADX'] == 30.0 and df.loc[1, 'AI_Skor'] == 0.2


def test_kaymis_dosya_sema_degisiminde_yeniden_yazilmaz_arsivlenir(tmp_path):
    """Eski bot 25 kolon başlık altına 27 alanlı satır yazmıştı. Şema değişikliği
    gerektiğinde böyle bir dosya yeniden yorumlanmaz, bayt bayt arşivlenir."""
    yol = tmp_path / 'v2.csv'
    with open(yol, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(V1 + ['AI_Skor'])
        w.writerow(['x'] * 14)
        w.writerow(['x'] * 16)   # kaymış satır
    once = yol.read_bytes()
    uyarilar = []
    satir_ekle(str(yol), YENI, V1 + ['AI_Skor', 'Sym_ADX'], uyari=uyarilar.append)
    arsiv = [p for p in os.listdir(tmp_path) if '.bozuk_' in p]
    assert len(arsiv) == 1 and (tmp_path / arsiv[0]).read_bytes() == once
    assert 'kolon kayması' in uyarilar[0]
    assert oku(yol)[0] == V1 + ['AI_Skor', 'Sym_ADX'] and len(oku(yol)) == 2


def test_ayni_semada_hizli_yol_mevcut_satirlara_dokunmaz(tmp_path):
    yol = tmp_path / 'v2.csv'
    with open(yol, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(V1)
        w.writerow(['x'] * 13)
    once = yol.read_bytes()
    satir_ekle(str(yol), YENI, V1)
    assert yol.read_bytes().startswith(once)
    assert oku(yol)[2][1] == 'SUI/USDT'


def test_yarim_kalan_son_satira_yapismaz(tmp_path):
    yol = tmp_path / 'c.csv'
    yol.write_text(','.join(V1) + '\n' + ','.join(['a'] * 13), encoding='utf-8')  # sonda \n yok
    satir_ekle(str(yol), YENI, V1)
    s = oku(yol)
    assert len(s) == 3 and s[2][1] == 'SUI/USDT'


def test_hucre_donusumleri():
    assert hucre(None) == '' and hucre(float('nan')) == '' and hucre(np.float64('nan')) == ''
    assert hucre(np.float64(0.1)) == '0.1' and hucre(True) == '1' and hucre(np.bool_(False)) == '0'
    assert hucre(7) == '7'


def test_baslik_gibi():
    assert baslik_gibi(V1, V1)
    assert not baslik_gibi(LEGACY.splitlines()[0].split(','), V1)


def test_onarim_araci_gercek_v2_yedegini_onarir(tmp_path):
    kaynak = os.path.join(REPO, 'core_islem_verileri_v2.csv.yedek')
    if not os.path.exists(kaynak):
        pytest.skip('yedek dosyası yok')
    hedef = tmp_path / 'v2.yedek'
    hedef.write_bytes(open(kaynak, 'rb').read())
    r = subprocess.run([sys.executable, os.path.join(REPO, 'tools', 'csv_onar.py'), str(hedef)],
                       capture_output=True, text=True, check=True)
    assert '52 satır onarıldı, 0 satır tanınamadı' in r.stdout
    df = pd.read_csv(str(hedef) + '.onarildi.csv')
    assert len(df) == 52 and len(df.columns) == 29
    assert df['BTC_ADX'].dropna().between(0, 100).all()
    assert df['AI_Skor'].between(0, 1).all() and df['Filtre_Skor'].between(0, 1).all()
    # 16 Eylül satırlarında ADX kolonları yoktu -> boş kalmalı, skorlar kaymamalı
    ilk = df.iloc[0]
    assert pd.isna(ilk['Sym_ADX']) and abs(ilk['AI_Skor'] - 0.7787207961082458) < 1e-12
