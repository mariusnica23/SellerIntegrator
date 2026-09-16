SELLERINTEGRATOR — GHID DE UTILIZARE
Versiunea 16.09.2026 | Windows | Trendyol + eMAG → FGO

1. TRENDYOL ȘI eMAG — ACEEAȘI STRUCTURĂ
Ambele taburi au Comenzi & facturare și Istoric. Sus se află perioada, Preia comenzi, Configurare și Mapare comună. Caută după comandă, colet, client sau EAN. Acțiunile de facturare și încărcare se aplică platformei deschise.

TRENDYOL
Alege perioada și apasă Preia comenzi. Aplicația citește piețele active, actualizează datele locale și verifică existența facturilor FGO asociate și încărcarea lor în Trendyol. Intervalul API este limitat la ultimele 30 de zile; comenzile preluate anterior rămân în istoricul local.
Caută numărul comenzii sau barcode-ul. Selectează cu Ctrl / Shift. Previzualizare arată clientul, produsele, cantitățile, TVA, moneda și totalul.
Facturează comenzile selectate: deschide verificarea lotului selectat.
Emite facturile verificate: creează efectiv documentele în FGO în mediul production. În demo simulează.
Încarcă facturile în Trendyol: transmite documentele deja emise, după confirmare.

Numărul comenzii este unic, dar o comandă poate avea mai multe colete. Fiecare colet are propriul ID și propria factură. Bucățile individuale se verifică separat la split: clientul, produsul și suma identice nu blochează trei colete distincte. O bucată deja facturată nu poate fi facturată din nou.

2. DE FACTURAT
Lista comună pentru ambele platforme, cu o coloană Platformă și toate câmpurile care vor fi trimise la emitere: client, adresă, țară, EAN, produse, cod FGO, cantitate, UM, TVA, total, monedă, serie și verificări. Conține colete Trendyol în expediere/livrate și comenzi eMAG finalizate fără factură asociată sau cu emitere respinsă / factură confirmată ca ștearsă.
Actualizează ambele platforme folosește perioadele din taburile respective; eMAG este omis dacă nu are utilizator configurat. Poți selecta comenzi de pe ambele platforme în același lot; fiecare comandă/colet are propria factură.
Bifează comenzile dorite și apasă Facturează comenzi selectate. Se selectează toate liniile coletului. Bara orizontală permite vizualizarea tuturor coloanelor.
Problemele lotului trebuie corectate înaintea emiterii. Simpla prezență în listă nu înseamnă că documentul poate fi emis.
După confirmarea previzualizării, o eroare la o comandă nu oprește încercarea celorlalte comenzi selectate. Rezultatul afișează separat comenzile cu erori. O emitere cu rezultat incert nu este reîncercată automat; rămâne pentru verificare în FGO.
Pauzele dintre cererile API se păstrează pentru întregul lot, inclusiv între platforme și conexiuni noi. La FGO, cererile de facturare sunt distanțate cu minimum 1,1 secunde. HTTP 409 singur nu dovedește că există o factură: în Istoric apare Verifică în FGO. Dacă documentul există, folosește Asociază factură existentă; dacă ai verificat că nu există, confirmă lipsa din Istoric înainte de reemitere. Erorile 409 salvate de versiunile vechi sunt trecute automat la verificare.

3. eMAG
Configurarea se face în Parametrizare → Date de bază & conexiuni → eMAG RO / BG / HU. Completează utilizator, parolă și serie FGO pentru fiecare piață activă, apoi Salvează.
Acces API: eMAG Marketplace → Contul meu → Profil → Detalii tehnice → Adrese IP → Adaugă un nou IP. Folosește IP-ul public al rețelei calculatorului pe care rulează programul. Utilizatorul trebuie să aibă drepturi API. Dacă acestea lipsesc, solicită activarea la suportul eMAG. Câmpul API Code și adresele callback nu sunt cerute de aplicație.

Preia comenzi eMAG: filtrează după data modificării, maximum un an, fără date viitoare. Fluxul facturează comenzi finalizate (status API 4), livrate de vânzător (tip 3), către persoane fizice. Finalizată nu reprezintă o confirmare separată a livrării către client.
Mapare comună: folosește EAN-ul din coloana ean_emag. Dacă lipsește din comandă, aplicația citește EAN-ul prin product_offer/read folosind product_id numai pentru identificarea ofertei API. Produsul facturat este ales după EAN. Dacă mai multe EAN-uri ale aceleiași oferte indică articole FGO diferite, facturarea este oprită.
Facturează comenzile selectate: previzualizează și confirmă. Încarcă facturile în eMAG: atașează documentele FGO deja emise.
Istoric eMAG: documente, stări, deschidere PDF și recuperarea emiterilor incerte după verificarea FGO.

Prețul sale_price din eMAG este fără TVA; aplicația adaugă TVA articolului FGO o singură dată. Reducerile seller scad suma facturii; reducerile finanțate de eMAG sunt incluse când politica include este activă. Voucherele fără distribuție/finanțator clar sunt oprite pentru verificare.
Pentru transport nenul, configurează codul articolului FGO de transport și dacă shipping_tax include TVA (cu_tva / fara_tva). Reducerile de transport cer verificare separată.
B2B, FBE, SGR și storno se procesează separat. Integrarea nu modifică stocuri, prețuri sau statusuri eMAG și nu creează AWB-uri.

4. PARAMETRIZARE
Salvează (Ctrl+S) păstrează câmpurile la fiecare deschidere. Reîncarcă setările revine la ultima salvare. Parolele sunt protejate cu Windows DPAPI. La salvare se păstrează și copia precedentă settings.backup.json.
Datele sunt în %LOCALAPPDATA%/TrendyolFGO. Programul poate fi actualizat fără ștergerea lor. Nu distribui folderul de date altor persoane.
Într-o instalare portabilă, dacă există folderul Date lângă TrendyolFGO.exe, acesta este folosit automat atât la dublu clic, cât și din scurtătură. Păstrează acel folder la actualizare. Butonul Deschide folderul de date arată locația activă.

Mapare Excel comună: trei coloane TEXT — cod_fgo, ean_trendyol, ean_emag. Un rând asociază articolul FGO cu EAN-ul folosit pe fiecare platformă; una dintre cele două platforme poate rămâne goală. Același EAN poate apărea o singură dată pe platformă. Mai multe EAN-uri pot indica același articol FGO, pe rânduri separate. Păstrează zerourile de la început.
Exportă Excel comun creează fișierul cu toate asocierile salvate. Importă Excel comun adaugă rândurile noi și actualizează asocierile pentru codurile incluse; rândurile omise și mapările celeilalte platforme rămân în sistem. Un cod asociat unui alt articol FGO folosește datele noului articol. Baza locală catalog.sqlite3 păstrează maparea chiar dacă Excelul original nu mai este disponibil.
Denumirea, UM și TVA sunt citite o singură dată pentru fiecare articol FGO și se păstrează la redeschidere, separat după firmă și mediu. Preia doar articolele noi citește doar codurile care nu sunt salvate. Reîmprospătează din FGO recitește explicit toate articolele mapate: folosește-l după schimbări de denumire, UM sau TVA în FGO. Prețul și cantitatea continuă să vină din comandă.
EAN-urile ofertelor eMAG citite prin API sunt păstrate local, separat după cont și piață. Dacă o comandă furnizează EAN-uri actualizate, acestea înlocuiesc valorile salvate pentru ofertă. Verificările comenzilor și existenței facturilor continuă la actualizare și înaintea operațiunilor.
Coloana ean_trendyol acceptă și coduri barcode alfanumerice, păstrate exact ca TEXT. Maparea Trendyol veche cu barcode și cod FGO este convertită automat când nu există o mapare eMAG veche. Originalul și parametrizările sunt păstrate. Identificatorii product_id vechi nu sunt transformați în EAN-uri presupuse.
Conexiuni API: mediu demo / test / production, Seller ID și cheile Trendyol; CUI fără RO, cheia privată FGO, URL-ul integrării înregistrat în FGO și seriile pentru fiecare țară. URL-ul integrării este un identificator acceptat în contul FGO; nu inventa un site comercial.
Istoricul Trendyol este separat după mediu, CUI și Seller ID; istoricul eMAG după mediu și CUI. Schimbarea identității poate afișa alt profil. Revenirea la datele anterioare regăsește istoricul.

TVA & plafon UE: confirmă regimul fiscal, completează anul, vânzările UE anterioare și externe aplicației, cursurile și data lor. Soldurile nu trebuie să dubleze facturile înregistrate în aplicație. Monitorizarea cumulează profilurile active Trendyol și eMAG.
EUR direct în FGO: se aplică numai sumelor deja în EUR. Suma se transmite către FGO în EUR; cursul local și verificarea plafonului sunt omise. RON rămâne RON. Plafonul trebuie urmărit separat. Facturile EUR fără echivalent RON local nu sunt considerate zero; revenirea la monitorizare, inclusiv pentru HUF, necesită reconcilierea lor.
Curs HUF: se completează pentru 1 HUF, nu pentru 100 HUF, în configurarea eMAG. Actualizează și data cursului în TVA & plafon UE.

5. ISTORIC ȘI FACTURI ȘTERSE — ÎN FIECARE TAB DE PLATFORMĂ
Preia comenzi verifică documentele asociate local chiar și când comanda nu apare în intervalul ales.
Factura există în FGO, dar a fost ștearsă din marketplace: revine la Emisă în FGO; selecteaz-o și încarcă din nou. Nu este necesară o factură nouă.
Factura a fost ștearsă din ambele sisteme: după confirmarea explicită FGO și confirmarea lipsei din marketplace, apare Lipsește din FGO — de refăcut. Documentul vechi rămâne arhivat local; poți reface previzualizarea.
Factura lipsește în FGO, dar este prezentă în marketplace: aplicația cere verificare înainte de reemitere.
Factură prezentă în marketplace cu link diferit: existența este confirmată, dar identitatea documentului nu poate fi stabilită numai după URL. Aplicația nu îl înlocuiește automat.
Erorile de autentificare, rețea sau răspuns necunoscut nu înseamnă că factura a fost ștearsă.

Verifică în FGO: răspunsul la emitere s-a pierdut. Verifică în contul FGO. Dacă există, folosește Asociază după verificare, cu seria și numărul. Dacă nu există, butonul Am verificat: factura NU există permite reluarea. Nu confirma lipsa fără verificare.
Backup istoric salvează baza de facturi a platformei. Pentru o copie completă păstrează și catalog.sqlite3, settings.json și folderul profiles din folderul de date. DPAPI permite decriptarea cheilor numai cu utilizatorul Windows corespunzător.

6. RAPOARTE
Alege datele de început/sfârșit sau un an și Tot anul, apoi țara și marketplace-ul. Actualizează raportul recalculează din comenzile stocate local; pentru date recente folosește mai întâi Preia comenzi.
Sursa curentă este afișată explicit: comenzi locale. Totalurile nu reprezintă toate facturile FGO pe an. Preluarea completă, exclusiv automată prin API, necesită acces și documentație suplimentare de la FGO; API-ul public de facturare nu documentează listarea facturilor pe perioadă. Vezi ACCES_RAPOARTE_FGO.md pentru cererea pregătită către furnizor.
Trendyol: implicit colete livrate; poți include și în expediere. eMAG: comenzi finalizate, fără confirmare separată de livrare.
Vânzări pe țări: comenzi distincte, colete, bucăți și valoare cu TVA după reducerile comerciantului, înainte de comision. Monedele se afișează separat; nu se adună RON cu EUR sau HUF.
Top 5: produse după valoarea vânzărilor, separat pe monedă. Același cod FGO grupează produsele mapate între marketplace-uri. Transportul intră în totalul țării, dar nu în topul produselor și nici în numărul bucăților.
Coletele split se numără separat, dar aceeași comandă o singură dată pe țară/monedă. Bucățile duplicate și totalurile nereconciliate sunt excluse cu explicație.
Anulările și retururile complete sunt excluse. Retururile parțiale, toate cheltuielile și facturile emise în afara aplicației nu sunt reconciliate automat. Raportul nu este un calcul de profit sau un registru contabil.

7. GHID & FISCALITATE
Acest tab afișează instrucțiunile de mai sus. Regimul implementat este limitat la scenariul B2C cu expedieri din România, fără OSS/taxare la destinație. FGO aplică automatizările e-Factura din propriul cont; trimiterea linkului către marketplace este o operațiune distinctă.
Cota standard configurată este 21%; articolele eligibile pot avea 11%. Plafonul UE folosit este 46.337 RON fără TVA, cumulat pentru operațiunile eligibile din toate țările UE și toate canalele. Verifică încadrarea și soldurile cu contabilul.

Surse oficiale:
eMAG API: https://marketplace-api.emag.ro/api-doc
Acces eMAG: https://marketplace.emag.ro/infocenter/integrarea-conturilor-emag-si-baselinker/
FGO API: https://api.fgo.ro/v1/testing.html
Trendyol: https://developers.trendyol.com/v2.0/docs/get-order-packages-getshipmentpackages
Plafon UE / OSS: https://vat-one-stop-shop.ec.europa.eu/one-stop-shop_en
Formular ANAF: https://static.anaf.ro/static/10/Anaf/formulare/D_010_OPANAF_2420_2025.pdf
Cote TVA ANAF: https://static.anaf.ro/static/3/Ploiesti/20251104100501_comunicat.pdf
