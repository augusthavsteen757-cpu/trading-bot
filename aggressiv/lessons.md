# Lektier fra Analytikeren

Skrives kun efter 2 lignende tilfaelde. [+] = hvad der virker, [-] = hvad der skal undgaas.

- 2026-11-07 12:38 [-] [teknisk fejl: APIError: {"code":50310000,"message":"service unav] Wrap API-kald i retry: ved kode 50310000 forsoeg igen 3 gange med 5/15/45 sek. pause; efter 3 fejl spring handlen over, log den og tjek broker-statussiden. (aarsag: Broker-API returnerede 503 'service unavailable' to gange inden for 4 minutter; midlertidig udfald hos udbyderen, ikke en fejl i strategien eller ordrelogikken.)
- 2026-11-09 14:42 [-] [teknisk fejl: RuntimeError: For lidt data for CKX: 207 dag-bars, 6 t] Tjek foer signal: kraev mindst 50 time-bars og 200 dag-bars. Ellers spring CKX over, log 'utilstraekkelig data' i stedet for RuntimeError, og vent paa mere historik. (aarsag: CKX har kun 6 time-bars (207 dag-bars) - for lidt intradag-historik til features; koden forsoeger at handle alligevel og kaster RuntimeError begge gange.)
