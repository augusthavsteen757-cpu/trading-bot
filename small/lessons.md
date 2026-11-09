# Lektier fra Analytikeren

Skrives kun efter 2 lignende tilfaelde. [+] = hvad der virker, [-] = hvad der skal undgaas.

- 2026-11-07 12:39 [-] [teknisk fejl: APIError: {"code":50310000,"message":"service unav] Tilfoej retry med eksponentiel backoff (2s, 4s, 8s, max 3 forsoeg) ved HTTP 503/50310000; log fejlen og spring handlen over, hvis alle forsoeg fejler. Tjek broker-statusside foer ordre. (aarsag: Broker/API returnerede 503 service unavailable (kode 50310000) to gange inden for 5 minutter; ordrekald fejlede uden retry. Ikke en strategifejl, men midlertidig udbyder-nedetid.)
