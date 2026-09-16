# Solicitare acces automat la rapoarte FGO

Text pregătit pentru suportul FGO; nu a fost trimis automat.

**Subiect: Acces API rapoarte de vânzări pentru aplicația Windows SellerIntegrator**

Bună ziua,

Folosesc API-ul FGO pentru emiterea facturilor din aplicația desktop SellerIntegrator. Doresc să citesc automat toate facturile emise în FGO într-un an sau într-o perioadă selectată, inclusiv cele emise în afara aplicației, pentru vânzări pe țări și top 5 produse.

Vă rog să confirmați:

1. Ce interfață și ce abonament permit listarea facturilor pe perioadă, cu paginare și detalii pe produse?
2. Care este documentația metodelor, filtrelor, limitelor și răspunsurilor? Sunt necesare: identificator stabil, serie/număr, data emiterii, starea/anularea/storno și referința documentului corectat, țara clientului, moneda, cursul, totalurile cu/fără TVA, codul articolului, denumirea, cantitatea, reducerile și TVA pe fiecare linie.
3. Poate fi folosit pentru aceasta serviciul `https://mcp.fgo.ro/mcp`? Dacă da, vă rog să furnizați documentația instrumentelor și să înregistrați un client OAuth propriu pentru SellerIntegrator, aplicație publică Windows, cu Authorization Code + PKCE S256 și redirect loopback local. Sunt necesare client_id, scope-urile, regulile redirect și condițiile de refresh/revocare.
4. Dacă rapoartele sunt disponibile prin API-ul existent cu cheie privată, care sunt endpointurile și drepturile necesare?

Integrarea solicitată este numai pentru citirea rapoartelor. Nu doresc export/import manual de fișiere.

Mulțumesc.

## Documentație consultată

- [API public FGO](https://api.fgo.ro/v1/testing.html)
- [Integrare FGO cu Claude](https://www.fgo.ro/integrare-claude/)
- [Integrare FGO cu ChatGPT](https://www.fgo.ro/integrare-chatgpt/)

Identificatorii OAuth publicați pentru Claude/ChatGPT aparțin acelor integrări; nu sunt reutilizați de SellerIntegrator. Rapoartele din versiunea curentă a aplicației folosesc comenzile locale, nu toate facturile FGO.
