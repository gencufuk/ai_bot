# -*- coding: utf-8 -*-
"""AI Ensemble Sniper ortak modülleri.

ai_bot.py (canlı), shadow_labeler.py, backfill_sinyaller.py ve ai_trainer.py aynı
feature / etiket / model kartı tanımlarını buradan kullanır. Böylece eğitimde
kullanılan feature ile canlıda hesaplanan feature birebir aynı koddan gelir
(train/serve skew olmaz).

Sunucuda bu klasör ai_bot.py ile aynı dizinde (/root/sniper/) durmalıdır.
"""
