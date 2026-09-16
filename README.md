# SellerIntegrator pentru Windows

Aplicație desktop în română: comenzi Trendyol și eMAG, facturi FGO, mapare Excel și rapoarte de vânzări.

## Descarcă și pornește

1. Descarcă `SellerIntegrator-Windows.zip` din [Releases](https://github.com/mariusnica23/SellerIntegrator/releases).
2. Dezarhivează întregul folder și pornește `TrendyolFGO.exe`. Păstrează `_internal` lângă executabil.
3. Prima pornire folosește exemple locale în modul demo. Configurează propriile conturi și apasă **Salvează**.

Nu este necesară instalarea separată a Python. Datele se păstrează în `%LOCALAPPDATA%/TrendyolFGO`, separat de program; actualizarea executabilului nu le șterge. Cheile și parolele sunt criptate cu Windows DPAPI, pentru utilizatorul Windows curent.

Pentru o instalare portabilă configurată cu folderul `Date` lângă executabil, acesta devine folderul de date folosit la pornire. Păstrează-l la actualizare și nu îl distribui altor persoane: conține configurarea și istoricul propriu. Arhivele publicate pe GitHub nu includ acest folder.

Instrucțiuni pentru fiecare tab: [GHID.md](GHID.md).

## Funcții

- Trendyol RO/BG/GR: selectarea coletelor Shipped/Delivered, verificarea sumei și emiterea FGO.
- Colete split: o factură per pachet, cu rezervarea bucăților individuale; același număr de comandă poate avea mai multe facturi.
- Actualizare: verifică facturile FGO asociate local și prezența lor în marketplace. Facturile șterse pot fi refăcute numai după confirmarea lipsei lor în ambele sisteme. Erorile de rețea nu deblochează emiterea.
- Taburi **Trendyol** și **eMAG** cu aceeași structură: comenzi, căutare, previzualizare, facturare, încărcare și istoric.
- **De facturat** reunește comenzile eligibile din ambele platforme, cu o coloană Platformă și facturarea lotului selectat.
- eMAG RO/BG/HU: preluare comenzi finalizate, livrate de vânzător; emitere și atașare facturi.
- Un singur Excel: `cod_fgo`, `ean_trendyol`, `ean_emag`, toate ca TEXT. EAN-ul eMAG este citit din oferta produsului dacă lipsește din comandă.
- Import cumulativ: asocierile omise dintr-un Excel nou rămân salvate. Codurile barcode alfanumerice Trendyol sunt acceptate.
- Catalog local persistent: denumirea, UM, TVA și EAN-urile ofertelor deja citite sunt reutilizate după redeschidere. Butonul **Reîmprospătează din FGO** recitește articolele după modificări în FGO. Verificările comenzilor și facturilor rămân active.
- Rapoarte: vânzări pe țări și top 5 produse după valoare, separat pe monede, cu filtre de perioadă și marketplace.
- Previzualizare și confirmare explicită înaintea emiterii. Încărcarea facturii este un pas separat.

## Domeniu și limite

Regimul implementat: firmă înregistrată normal în scopuri de TVA în România, stabilită numai în România, expedieri din România către persoane fizice, fără opțiune OSS / taxare la destinație și sub plafonul UE aplicabil. Articole FGO cu TVA 21% sau 11%. Regimul trebuie verificat pentru fiecare firmă.

Nu include OSS, B2B, FBE, SGR, storno automat, retururi parțiale automate ori reconcilierea recipiselor ANAF. Rapoartele reprezintă comenzile sincronizate, nu contabilitatea completă. Seriile, cursurile și soldurile externe se configurează de operator.

Rapoartele complete din FGO prin API nu sunt încă disponibile în aplicație: documentația API publică nu descrie listarea tuturor facturilor pe perioadă. Accesul la interfața financiară FGO necesită autorizare OAuth și documentarea datelor disponibile pentru această aplicație. Cererea pregătită pentru furnizor: [ACCES_RAPOARTE_FGO.md](ACCES_RAPOARTE_FGO.md).

Opțiunea **EUR direct în FGO** trimite sumele în EUR fără curs și fără monitorizarea plafonului pentru acele facturi. RON rămâne RON; HUF necesită curs și un istoric fiscal reconciliat. Plafonul monitorizat cumulează facturile din profilurile active Trendyol și eMAG.

Integrarea eMAG este testată cu răspunsuri simulate conform documentației API v4.5.1. Accesul, formatul efectiv al comenzilor și acceptarea linkului PDF trebuie validate în contul utilizatorului. Accesul API necesită drepturi pentru utilizator și autorizarea IP-ului public.

## Dezvoltare și testare

Python 3.12 pe Windows, cu Tkinter:

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python run.py --data-dir .qa/development
.venv/Scripts/python -m unittest discover -s tests
```

Testele folosesc date sintetice și adaptoare simulate; nu emit facturi externe. Testele interfeței și DPAPI necesită Windows.

Build:

```powershell
python -m pip install -r requirements.txt pyinstaller==6.16.0
python -m PyInstaller --noconfirm TrendyolFGO.spec
```

GitHub Actions execută testele și construiește arhiva Windows. Un tag `v*` publică arhiva în Releases.

Repository-ul și distribuția nu conțin credențiale, istoricul comenzilor ori mapări comerciale reale.
