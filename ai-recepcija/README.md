# Glasnik AI – landing stranica + C++ server

Stranica (`index.html`) i mali HTTP server u C++17 (`server.cpp`) bez vanjskih biblioteka.

## Pokretanje
```
g++ -std=c++17 -O2 -pthread -o glasnik server.cpp
./glasnik 8080        # pokreni iz ovog foldera
```
Otvori http://localhost:8080

## Što server radi
- `GET /` poslužuje `index.html`
- `POST /api/kontakt` validira i sprema upit iz forme u `upiti.jsonl` (jedan JSON po retku)
- `GET /healthz` provjera da radi

## Što još nije napravljeno
- Demo chat je i dalje scriptan (nema AI-ja iza njega)
- Nema HTTPS-a (stavi iza nginx/Caddy) niti slanja e-maila o novom upitu
- Telefonski asistent nije dio ovog koda
