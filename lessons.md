# Lektier fra Analytikeren

Skrives kun efter 2 lignende tilfaelde. [+] = hvad der virker, [-] = hvad der skal undgaas.

- 2026-11-05 15:49 [-] [tab: nyhed_op i high_vol] Koeb ikke nyhed_op i high_vol naar rel_volumen_1h < 0,5 eller atr_percentil_30d > 90. Kraev rel_volumen_1h >= 1,5 som bekraeftelse paa nyheden, ellers spring over. (aarsag: Begge nyhed_op-koeb skete paa ekstremt lav rel_volumen_1h (0,13 og 0,06) og ekstrem volatilitet (atr_percentil_30d 99,7 og 93,4); nyheden blev ikke bekraeftet af volumen, og stop blev ramt.)
