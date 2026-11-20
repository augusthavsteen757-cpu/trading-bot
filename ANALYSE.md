# Daglig analyse (én rettelse pr. dag)

Regel: kun ÉN ændring pr. dag, så vi kan se hvad der virkede. Kun paper.
Næste dags analyse starter med at tjekke gårsdagens rettelse.

## 20. nov 2026 (fredag + ugeresultat)

**Ugens rettelser:** Options-pausen efter tab (PR #61) virker. Reglen om ingen ORB i nedtrend (PR #63) er stadig ikke blevet brugt, fordi alle ORB-signaler var i range eller high_vol. Logning af forsvundne options (PR #64) er i drift.

**Godt**
- Aggressiv: +2,55 % i dag, nu +0,93 % i alt. SOXS (ORB, markedet faldt) gav +2.963 USD (halvdel ved +1R, resten ved dagens slut), og RBLX gav et delsalg på +200.
- Options: +2,90 % i dag, nu +3,81 % i alt. Den ejer en AAPL-put (købt 8,45, nu ca. 10,90).

**Dårligt**
- Aggressiv: TSLA -245 og PLTR -369 (stop efter 20 min).
- Hoved: EQIX -84. Small og krypto: ingen handler.
- **Options-kontoen ændrede sig hvert 5. min (100.759 til 102.378 USD) før AAPL-putten blev købt kl. 17.27, mens botten ikke ejede noget.** Der ligger altså options hos Alpaca, som botten ikke kender. De har intet stop og står ikke i statistikken. Det forklarer også, at AAPL-callen "forsvandt" 19. nov.

**Ugeresultat (16.-20. nov, lukkede handler):** Aggressiv 21 handler, 7 vundet, +424 USD. Hoved 2 handler, -180 USD. Small 1 handel, -78 USD. Options 1 handel, -335 USD (plus de ukendte positioner). Krypto 0 handler.
Kontoen i alt: aggressiv +0,93 %, options +3,81 %, krypto -0,01 %, hoved -0,78 %, small -0,82 %.

**Rettelse i dag:** Options-botten adopterer nu positioner, den finder hos Alpaca, men ikke kender. De får samme stop- og gevinstregler, skrives i trades.csv ("ADOPTERET") og vises i OVERSIGT.md. Hvis en ukendt position er over +50 %, sælges den ved næste kørsel (gevinstreglen).

**Tjek mandag:** Hvad options-botten adopterede, og om gevinsten er sikret.

## 19. nov 2026 (torsdag)

**Gårsdagens rettelse (aggressiv: ingen ORB i nedtrend, PR #63):** Den er ikke testet endnu. I dag kom der kun ORB-signaler i aktier i "range" (AMD, SMCI), så reglen blev ikke brugt.

**Godt**
- Options: +1,28 % i dag og nu +0,79 % i alt (var -0,41 %). AAPL-callen (købt 7,82 den 17/11) er væk fra kontoen, og kontoen steg tilsvarende.

**Dårligt**
- Aggressiv: AMD -482 og SMCI -261 (begge ORB i range, stop ramt efter 5 og 20 min). -0,69 % i dag, nu -1,58 % i alt.
- Options: AAPL-callen forsvandt hos Alpaca uden et salg fra botten, så den mangler i closed_trades.csv. Statistikken siger stadig "0 af 5 vundet", selvom handlen tjente penge.
- Hoved, small, krypto: ingen handler.

**Rettelse i dag:** Options-botten husker nu sidste pris på hver position. Hvis en position forsvinder hos Alpaca (solgt uden for botten), skrives den i closed_trades.csv med den sidst kendte pris og årsagen "solgt uden for botten". AAPL-handlen kan ikke genskabes præcist, fordi prisen ikke blev gemt.

**Tjek i morgen (fredag):** Om aggressiv springer ORB i nedtrend over. Ugens resultat for alle bots.

## 18. nov 2026 (onsdag)

**Gårsdagens punkter:** Options-pausen holder stadig (ingen AMZN-køb). Options ejer stadig AAPL-call (købt 7,80) og er -0,53 % i alt. Small-ORB har ikke handlet, så 5 %-loftet er stadig utestet.

**Godt**
- Aggressiv: CRWD gav +103 USD (halvdel ved +1R, resten ved målet 3R).

**Dårligt**
- Aggressiv: -1,45 % i dag, nu -0,90 % i alt. SMCI -508, RIVN -459, RIOT -351, ARM -219.
- SMCI og RIVN var ORB-køb i aktier i **nedtrend** (trend_ned). Siden start har aggressiv lavet 4 ORB-køb i nedtrend, 0 vundet, i alt -995 USD. ORB i "range" har til sammenligning givet +1.743 USD (11 af 21 vundet).
- Hoved, small, krypto: ingen handler.

**Rettelse i dag:** Den aggressive bot laver ikke ORB-køb i aktier, der er i nedtrend (ORB_SKIP_TREND_NED=1, kan slås fra med Actions-variablen AGGRESSIV_ORB_SKIP_TREND_NED=0). Alle andre køb er uændrede, så den bliver ikke meget strammere.

**Tjek i morgen:** At aggressiv springer ORB i nedtrend over (note i trades.csv), og hvordan AAPL-call ender.

## 17. nov 2026 (tirsdag)

**Gårsdagens rettelse (options-pause efter tab, PR #61):** Virker. AMZN gav et køb-signal kl. 15.25 og 15.30 (bullish), men blev blokeret med "tabte i aktien 2026-11-16, pause i 7 dage".

**Godt**
- Options: +0,62 % i dag. Ejer en AAPL-call (købt 16.15).
- Aggressiv: resten af SHOP blev solgt med +55 USD. Stop på købspris efter delsalg virkede, så hele SHOP-handlen gav +192 USD.

**Dårligt**
- Small: OUST nyhed_op -78 USD (stop ramt). Small har nu 15 handler med 4 vundne og -596 USD i alt.
- Hoved og krypto: ingen handler.

**Rettelse i dag:** Ingen. Kun ét nyt tab (OUST), og det er ikke nok bevis. Small-ORB (5 %) har stadig ikke handlet siden rettelsen.

**Tjek i morgen:** Hvordan AAPL-call ender, og om small-ORB handler med 5 %.

## 16. nov 2026 (mandag, data 14.-16. nov)

**Rettelse 14. nov (PR #60):** Raket-scanneren køber ikke omvendte fonde (fx SOXS), medmindre RAKET_INVERSE=1. Markedsfilteret køber dem stadig, når markedet falder.

**Godt**
- Aggressiv: +0,17 % i dag, +0,62 % i alt. SHOP halvsolgt med +137 USD, resten kører med stop på købspris.

**Dårligt**
- Options: 0 ud af 5 handler vundet, -0,98 % i alt. AMZN-calls har tabt 3 gange i træk (5/11, 11/11, 16/11), og hver gang blev der købt igen kort efter et tab.
- Hoved: AMZN -96 USD, -0,69 % i alt. Small: ingen handler. Krypto: flad.

**Rettelse i dag:** Options-botten holder 7 dages pause i en aktie efter et tab. Det læses fra closed_trades.csv og kan ændres med OPT_LOSS_COOLDOWN_DAYS.

**Tjek i morgen:** At options-botten ikke køber AMZN før 23. nov, og at small-ORB holder sig til max 5 %.

## 13. nov 2026 (fredag, data 13. nov + fredagstjek)

**Gårsdagens rettelse (small-ORB max 5 %):** Ikke testet endnu. Small-botten handlede ikke i dag.

**Fredagstjek, aggressiv**
- Delsalg (PR #53): virker. Det skete 5 gange (SMCI x2, CRWG, COIN, AMD) og sikrede +1.491 USD. Resten af de samme handler gav +870 USD.
- Volumen-krav (PR #54): virker. Det blokerede 11 køb 11.-12. nov. Aktierne faldt i snit 1,38 % til lukketid. Kun ARM (+4,3 %) var en misset vinder.

**Godt**
- Options og hoved: rolige dage, ingen nye tab.

**Dårligt**
- Aggressiv: -1,05 % i dag. Hele tabet kom fra én raket-handel i en omvendt fond (SOXS -984 USD, tidsstop). I alt er den nu +0,44 %, og bremsen er slået til (halv størrelse).
- Ingen dobbelt indsats til aggressiv: den har ikke fortjent det endnu.

**Rettelse i dag:** Ingen. De omvendte fonde har i alt givet plus (5. nov ca. +2.760, i dag -984), så der er ikke klart bevis for at lave dem om.

## 12. nov 2026 (data 12. nov)

**Gårsdagens rettelse (options, intet genkøb samme dag som salg):** Ikke testet i dag. Options solgte intet. Den ejer stadig AMZN-call fra 11. nov.

**Godt**
- Aggressiv: +0,09 % i dag, +1,51 % i alt.
- Hoved: EQIX nåede målet (+51 USD). Dagen endte i +0,01 %.

**Dårligt**
- Small: -0,70 % i dag. 3 ORB-handler tabte (RGTI -297, OUST -191, RCAT -130). Små aktier svinger så meget, at ORB-stoppet bliver ramt.
- ORB på small i alt: 14 handler, 4 vundet, -518 USD. Uden den ene store vinder (ACHR +708) er det -1.226 USD.

**Rettelse i dag (small)**
- ORB-handler på small får halv størrelse: max 5 % af kontoen i stedet for 10 % (aftalt 3. nov hvis ORB blev ved med at tabe).
- Tjek i morgen: er small-ORB-handlerne cirka halvt så store?

## 11. nov 2026 aften (data 11. nov)

**Gårsdagens rettelse (options, ingen salg første 30 min):** Blev ikke rigtig testet. AMZN-call blev solgt kl. 16.22 dansk tid (efter vinduet) ved -40 %, fordi AMZN faldt fra 274 til 268.

**Godt**
- Aggressiv: +1,87 % i dag (CRWG +400, AMD +109). I alt nu +1,41 %. Selv-tjek er tilbage på fuld fart.
- Hoved: handlede igen, 4 handler, +58 USD (SPY nåede målet +40, AMD +53).

**Dårligt**
- Options: 4 af 4 handler tabt i alt (-1.410 USD), konto -1,94 %. I samme minut som AMZN-call blev stoppet ud, købte botten en NY AMZN-call for 800 USD. Det er en fejl: "allerede handlet i dag" gjaldt kun køb, ikke salg.
- Small: RCAT -56 USD (tidsstop).

**Rettelse i dag (options)**
- En aktie der er solgt i dag kan ikke købes igen samme dag.
- Tjek i morgen: ingen køb i samme aktie samme dag som et salg.

## 11. nov 2026 (data 9.-10. nov)

**Godt**
- Hoved-botten: -0,65 % i alt, mens "køb og behold" af samme aktier er -5,0 %. Den sad stille i et faldende marked. Godt.
- Small-botten: +0,03 % i alt (+156 USD på lukkede handler).

**Dårligt**
- Hoved + small: 0 handler 9.-10. nov. AI sagde nej til alle 39 + 23 setups. SPY-filteret blokerede ORB 116 gange. Ikke rettet endnu (nok rigtigt i et faldende marked).
- Hoved: AI-runderne (20/dag) var brugt kl. ca. 17 dansk tid 9. nov, så 313 setups blev aldrig vurderet.
- Aggressiv: 10. nov 4 tab (CRWD, AMD, AVGO, COIN). Rører den ikke før fredagstjek 13. nov (PR #53/#54/#55 testes).
- Options: 3 handler, 3 tab, -1.070 USD. To stop-salg skete 2 og 25 min efter åbning.

**Rettelse i dag (options)**
- Ingen almindelige salg de første 30 min efter åbning (urolige optionspriser). Kun nødstop ved -60 %.
- Tjek i morgen: blev der solgt før 16.00 dansk tid? Ramte stop senere på dagen alligevel?
