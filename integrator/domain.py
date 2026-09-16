from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import uuid

from .config import Settings
from .mapping import Product

CENT = Decimal("0.01")
LIMIT_RON = Decimal("46337")
READY_STATUSES = {"Shipped", "Delivered"}


def dec(value, label="valoare") -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError(f"Lipsește {label}.")
    try:
        d = Decimal(str(value).replace(",", "."))
    except InvalidOperation:
        raise ValueError(f"{label}: valoare numerică invalidă.") from None
    if not d.is_finite():
        raise ValueError(f"{label}: valoare numerică invalidă.")
    return d


def money(d):
    return dec(d).quantize(CENT, rounding=ROUND_HALF_UP)


def package_id(raw):
    if raw.get("_provider") == "emag":
        return f"emag:{raw['_market']}:{int(raw['_emag']['id'])}"
    value = raw.get("shipmentPackageId", raw.get("id"))
    if isinstance(value, bool) or not str(value).isdigit() or int(value) <= 0:
        raise ValueError("Lipsește un shipmentPackageId valid.")
    return str(value)


def country(raw):
    return str((raw.get("shipmentAddress") or {}).get("countryCode") or "").upper()


def source_claims(raw):
    """Claim individual physical items when Trendyol exposes split item IDs."""
    result = []
    for line in raw.get("lines", []):
        line_id = str(line.get("lineId", line.get("id", "")))
        if not line_id or line_id == "None":
            raise ValueError("Identificator de linie lipsă.")
        details = line.get("discountDetails") or []
        item_ids = [str(d.get("lineItemId") or "") for d in details]
        if any(item_ids):
            if not all(item_ids) or len(item_ids) != dec(line.get("quantity")):
                raise ValueError("Identificatorii bucăților din colet sunt incompleți.")
            result.extend(f"item:{line_id}:{item_id}" for item_id in item_ids)
        else:
            result.append(line_id)  # Older responses have only a line-level identity.
    if len(result) != len(set(result)):
        raise ValueError("Identificator de produs sursă duplicat în colet.")
    return result


def remote_invoice_present(raw):
    return bool(raw.get("invoiceLink")) or raw.get("invoiceStatus") in {"Received", "Invoiced", "Rejected"}


def signature(raw):
    """Only fields affecting invoice value/identity; lifecycle is checked separately."""
    if raw.get("_provider") == "emag":
        data = raw["_emag"]
        values = {k: data.get(k) for k in ("id", "type", "customer", "shipping_tax", "shipping_tax_voucher_split", "vouchers", "payment_mode_id", "is_storno")}
        values["products"] = [{k:v for k,v in p.items() if k not in {"modified", "created"}} for p in data.get("products", [])]
        values["market"] = raw["_market"]
        return hashlib.sha256(json.dumps(values, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()
    keys = ("orderNumber", "invoiceAddress", "shipmentAddress", "currencyCode", "packageGrossAmount", "packageTotalPrice", "packageTyDiscount", "packageSellerDiscount", "commercial", "micro", "totalSgrFee", "originPackageIds", "supplierId", "customerId", "paymentMethod")
    values = {k: raw.get(k) for k in keys}
    values["package_id"] = package_id(raw)
    line_keys = ("lineId", "id", "barcode", "quantity", "lineUnitPrice", "currencyCode", "discountDetails", "lineSgrFee", "lineTyDiscount")
    values["lines"] = [{k: line.get(k) for k in line_keys} for line in raw.get("lines", [])]
    return hashlib.sha256(json.dumps(values, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


@dataclass
class Draft:
    package_id: str
    order_number: str
    country: str
    currency: str
    total: Decimal
    net: Decimal
    vat: Decimal
    customer_paid: Decimal
    subsidy: Decimal
    net_ron: Decimal | None  # None: EUR sent directly; local RON equivalent is unknown.
    payload: dict
    fingerprint: str
    source_lines: list[str]


def build_draft(raw: dict, mapping: dict[str, Product], settings: Settings, issued_on: date | None = None) -> Draft:
    if raw.get("_provider") == "emag":
        from .emag import invoice_order
        raw, mapping = invoice_order(raw, mapping, settings)
    issued_on = issued_on or date.today()
    pid = package_id(raw)
    target = country(raw)
    status = raw.get("shipmentPackageStatus", raw.get("status"))
    if status not in READY_STATUSES:
        raise ValueError(f"Stare {status}: nu se emite automat. Verifică pachetul în Trendyol.")
    if remote_invoice_present(raw):
        raise ValueError("Pachetul are deja o factură în Trendyol. Asociază factura existentă din Istoric.")
    if target not in ({"RO", "BG", "HU"} if raw.get("_provider") == "emag" else {"RO", "BG", "GR"}):
        raise ValueError("Țara de livrare nu este RO, BG sau GR.")
    if raw.get("commercial") is not False:
        raise ValueError("Este necesar un pachet B2C cu commercial=false. Facturile B2B se verifică separat.")
    if raw.get("micro") is True:
        raise ValueError("Pachet micro-export: fluxul pentru expedieri din România nu se aplică.")
    if settings.mode != "demo":
        # International responses can return supplierId=0 as a placeholder.
        identities = [raw.get("supplierId")]
        for line in raw.get("lines") or []:
            identities.extend([line.get("sellerId"), line.get("merchantId")])
        identifiers = [str(v) for v in identities if v is not None and str(v) not in {"0", ""}]
        if any(v != settings.seller_id for v in identifiers):
            raise ValueError("Seller ID din produsele comenzii nu corespunde contului configurat.")
    if not settings.fiscal_confirmed:
        raise ValueError("Regimul fiscal trebuie actualizat în Setări.")
    if issued_on < date(2025, 8, 1):
        raise ValueError("Versiunea aceasta nu emite cu o dată anterioară lui 01.08.2025.")
    address = raw.get("invoiceAddress") or {}
    billing_country = str(address.get("countryCode") or "").upper()
    if billing_country != target:
        raise ValueError("Țara adresei de facturare diferă de livrare sau lipsește. Verificare manuală necesară.")
    if address.get("company") or address.get("taxNumber") or raw.get("taxNumber"):
        raise ValueError("Date de companie prezente: verifică dacă este o factură B2B.")
    name = str(address.get("fullName") or " ".join(str(address.get(k) or "") for k in ("firstName", "lastName"))).strip()
    locality = str(address.get("city") or "").strip()
    street = str(address.get("fullAddress") or " ".join(str(address.get(k) or "") for k in ("address1", "address2"))).strip()
    county = str(address.get("countyName") or "").strip()
    if target == "RO" and not county and locality.lower() in {"bucuresti", "bucurești", "bucharest"}:
        county = "Bucuresti"
    if not name or len(name) > 255 or not locality or not street or len(street) > 500:
        raise ValueError("Date de facturare incomplete sau prea lungi: nume, adresă, localitate.")
    if target == "RO" and not county:
        raise ValueError("Lipsește județul din invoiceAddress.countyName. Completează datele în Trendyol.")
    currency = raw.get("currencyCode")
    if currency not in {"RON", "EUR", "BGN", "HUF"}:
        raise ValueError(f"Moneda {currency!r} nu este acceptată în acest flux.")
    if dec(raw.get("totalSgrFee", 0), "SGR") != 0:
        raise ValueError("Comandă cu garanție SGR: necesită poziție fiscală separată, neconfigurată.")
    series = getattr(settings, f"{'emag_' if raw.get('_provider') == 'emag' else ''}series_{target.lower()}")
    if settings.mode == "demo":
        series = "DEMO"
    if not series or len(series) > 50:
        raise ValueError(f"Completează seria FGO pentru {target} în Setări.")
    lines = raw.get("lines")
    if not isinstance(lines, list) or not lines:
        raise ValueError("Comanda nu are produse.")
    content, source_ids = [], []
    paid_total = subsidy_total = net = total = Decimal("0")
    for line in lines:
        barcode = str(line.get("barcode") or "").strip()
        if barcode not in mapping:
            raise ValueError(f"Barcode nemapat: {barcode or '(lipsește)'}. Completează Excelul și reimportă.")
        product = mapping[barcode]
        if product.fgo_code and not product.fgo_verified and settings.mode != "demo":
            raise ValueError(f"Articolul FGO {product.fgo_code} nu este verificat. Preia articolele din FGO în Parametrizare → Mapare Excel.")
        if not product.name or not product.unit:
            raise ValueError(f"Articolul pentru {barcode} nu are denumire sau UM. Verifică maparea și articolul FGO.")
        qty = dec(line.get("quantity"), "cantitate")
        if qty <= 0 or qty != qty.to_integral_value():
            raise ValueError(f"{barcode}: cantitate invalidă pentru pachet Trendyol.")
        if line.get("orderLineItemStatusName") not in READY_STATUSES | {"Invoiced"}:
            raise ValueError(f"{barcode}: stare produs neeligibilă sau lipsă.")
        line_id = line.get("lineId", line.get("id"))
        if line_id is None or str(line_id) in source_ids:
            raise ValueError("Identificator de linie lipsă sau duplicat.")
        source_ids.append(str(line_id))
        if line.get("currencyCode") != currency:
            raise ValueError("Moneda produsului nu corespunde monedei pachetului.")
        if dec(line.get("lineSgrFee", 0)) != 0:
            raise ValueError("Produs cu SGR; verificare fiscală separată necesară.")
        unit = dec(line.get("lineUnitPrice"), "lineUnitPrice")
        if unit < 0:
            raise ValueError("Preț negativ: utilizează fluxul manual de storno.")
        details = line.get("discountDetails") or []
        # Per-item detail avoids ambiguous average/total discount semantics for quantity > 1.
        if details:
            if len(details) != int(qty):
                raise ValueError("discountDetails nu corespunde cantității. Verifică pachetul.")
            paid = sum((dec(d.get("lineItemPrice"), "lineItemPrice") for d in details), Decimal("0"))
            subsidy = sum((dec(d.get("lineItemTyDiscount", 0)) for d in details), Decimal("0"))
            if abs(money(paid) - money(unit * qty)) > CENT:
                raise ValueError("Prețul unitar nu se reconciliază cu discountDetails.")
        else:
            if dec(line.get("lineTyDiscount", 0)) != 0:
                raise ValueError("Discount Trendyol fără detalii individuale; calculul nu poate fi verificat.")
            paid, subsidy = unit * qty, Decimal("0")
        if paid < 0 or subsidy < 0:
            raise ValueError("Valori de reducere invalide.")
        if subsidy and settings.subsidy_policy != "include":
            raise ValueError("Reducere finanțată de Trendyol: confirmă în Setări includerea compensației în valoarea facturii, cu contabilul.")
        gross = money(paid + subsidy)
        base = money(gross / (1 + product.vat / 100))
        entry = {"Denumire": product.name, "NrProduse": qty, "UM": product.unit, "CotaTVA": product.vat, "PretTotal": gross}
        if product.fgo_code:
            entry["CodArticol"] = product.fgo_code
        content.append(entry)
        net += base
        total += gross
        paid_total += money(paid)
        subsidy_total += money(subsidy)
    if paid_total != money(raw.get("packageTotalPrice")):
        raise ValueError(f"Total nereconciliat: produsele au {paid_total}, pachetul {raw.get('packageTotalPrice')}. Transportul/ajustările necesită verificare.")
    if subsidy_total != money(raw.get("packageTyDiscount", 0)):
        raise ValueError("Reducerile Trendyol nu se reconciliază între produse și pachet.")
    if total <= 0:
        raise ValueError("Totalul trebuie să fie pozitiv.")
    sales = dec(raw.get("packageGrossAmount"), "valoarea vânzărilor / packageGrossAmount")
    seller_discount = dec(raw.get("packageSellerDiscount"), "reducerea comerciantului / packageSellerDiscount")
    if seller_discount < 0 or seller_discount > sales:
        raise ValueError("Reducerea comerciantului este invalidă.")
    invoice_target = money(sales - seller_discount)
    if total != invoice_target:
        raise ValueError(f"Suma de facturat Trendyol este {invoice_target} {currency}, iar produsele însumează {total}. Emiterea este oprită.")
    fx = Decimal("1")
    direct_eur = settings.eur_direct_fgo and currency == "EUR"
    if target != "RO" and currency != "RON" and not direct_eur:
        if settings.mode == "demo":
            fx = Decimal("5") if currency == "EUR" else Decimal("2.56")
        else:
            if settings.fx_date != issued_on.isoformat():
                raise ValueError("Completează cursul RON valabil la data emiterii și data cursului în Setări.")
            fx = dec({"EUR": settings.eur_ron, "BGN": settings.bgn_ron, "HUF": settings.huf_ron}[currency], "curs valutar")
            if fx <= 0:
                raise ValueError("Cursul valutar trebuie să fie pozitiv.")
    client = {"Denumire": name, "Tara": target, "Localitate": locality, "Adresa": street, "Tip": "PF", "Strain": target != "RO"}
    if county and target == "RO":
        client["Judet"] = county
    # No invented CNP; customerId is an account identifier, not a taxpayer ID.
    marketplace = "eMAG" if raw.get("_provider") == "emag" else "Trendyol"
    note = f"{marketplace} {target} | Comanda {raw.get('orderNumber', '')} | Pachet {pid}"
    if raw.get("paymentMethod"):
        note += f" | Plata: {raw['paymentMethod']}"
    payload = {"Serie": series, "Valuta": currency, "TipFactura": "Factura", "DataEmitere": issued_on.isoformat(), "Client": client, "Continut": content,
               "VerificareDuplicat": True, "IdExtern": str(uuid.uuid5(uuid.NAMESPACE_URL, f"trendyol-fgo/{settings.mode}/{settings.seller_id}/{pid}")), "Explicatii": note[:2000], "TvaLaIncasare": settings.cash_vat}
    net_ron = Decimal("0") if target == "RO" else (None if direct_eur else money(net*fx))
    return Draft(pid, str(raw.get("orderNumber", "")), target, currency, total, net, total-net, paid_total, subsidy_total, net_ron, payload, signature(raw), source_claims(raw))


def fiscal_budget(settings, used_ron, extra_ron, issued_on=None):
    today = issued_on or date.today()
    if settings.mode == "demo":
        return
    if settings.fiscal_year != str(today.year):
        raise ValueError("Actualizează anul și soldurile fiscale în Setări.")
    previous = dec(settings.previous_sales_ron, "vânzări UE anul precedent (RON fără TVA)")
    opening = dec(settings.opening_sales_ron, "vânzări UE înaintea folosirii aplicației")
    other = dec(settings.other_sales_ron, "vânzări UE externe aplicației după soldul inițial")
    if min(previous, opening, other) < 0:
        raise ValueError("Soldurile fiscale nu pot fi negative.")
    if previous > LIMIT_RON or opening + other + dec(used_ron) + dec(extra_ron) > LIMIT_RON:
        raise ValueError("Plafonul UE de 46.337 RON fără TVA este depășit. Oprește regimul RO pentru vânzările externe și configurează regimul de destinație cu contabilul; OSS nu este implementat.")
