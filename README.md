# Tradingbot (Alpaca PAPER, kun BTC/USD)

Kun falske penge. Botten koerer en runde hver time:

1. Scout, Nyheder, Stemning og Chart vurderer markedet (claude-haiku-5-5).
2. Chef beslutter KOEB, SAELG eller HOLD.
3. Skeptiker kan nedlaegge veto mod et koeb.
4. Koeb er max 10 % af kontoen, altid med stop-loss 3 % under koebsprisen.
5. Alt logges i `trades.csv`, inkl. sammenligning med koeb-og-behold.
6. Fejl-analytiker koerer kun efter en teknisk fejl eller en tabshandel. Den skriver en kort lektie i
   `lessons.md`, og Chef og Skeptiker laeser de 5 nyeste lektier hver runde. Samme tekniske fejl
   analyseres kun een gang, saa API-forbruget forbliver lavt.

AI-forbrug: ca. 0,5 USD/md. Botten stopper selv AI-kald ved 7,50 USD/md.

## Test uden noegler
    pip install -r requirements.txt
    python bot.py --test

## Koer paa GitHub hver time (anbefalet, gratis, din pc maa vaere slukket)
1. Laeg filerne i et privat GitHub-repo.
2. Repo > Settings > Secrets and variables > Actions > New repository secret.
   Opret tre: `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ANTHROPIC_API_KEY`.
3. Actions-fanen > "Tradingbot hver time" > Run workflow (foerste test).

## Koer paa egen computer
Kopier `.env.example` til `.env`, udfyld selv noeglerne, og koer `python bot.py`.
