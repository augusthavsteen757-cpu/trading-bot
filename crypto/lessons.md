# Lektier fra Analytikeren

Skrives kun efter 2 lignende tilfaelde. [+] = hvad der virker, [-] = hvad der skal undgaas.

- 2026-11-07 12:38 [-] [teknisk fejl: APIError: {"code":50310000,"message":"service unav] Tilfoej retry med eksponentiel backoff (3 forsoeg: 2s, 4s, 8s) ved APIError 50310000; efter 3 fejl afbrydes handlen og logges. Tjek ogsaa broker-statussiden foer ordreafgivelse. (aarsag: Broker-API returnerer 503 (kode 50310000, service unavailable) to gange inden for 5 minutter, sandsynligvis midlertidig udbyder-nedetid uden retry-logik i ordrekaldet.)
