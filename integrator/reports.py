"""Read-only sales reports from cached marketplace orders, never mixed currencies."""
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from .domain import country, dec, money, package_id, source_claims


def order_day(raw):
    if raw.get("_ordered_on"):
        return str(raw["_ordered_on"])[:10]
    stamp = raw.get("orderDate")
    return datetime.fromtimestamp(float(stamp) / 1000).date().isoformat() if stamp else None


def sales_report(rows, mapping, start, end, market="Toate", include_shipped=False):
    countries, products, errors = {}, {}, []
    seen_packages, seen_items = set(), set()
    statuses = {"Delivered", "Shipped"} if include_shipped else {"Delivered"}
    for raw in rows:
        if raw.get("shipmentPackageStatus", raw.get("status")) not in statuses:
            continue
        pid = package_id(raw)
        try:
            day = order_day(raw)
            if not day:
                raise ValueError("lipsește data comenzii")
            if not start <= day <= end or market not in {"Toate", country(raw)} or pid in seen_packages:
                continue
            claims = source_claims(raw)
            if seen_items.intersection(claims):
                raise ValueError("bucăți regăsite în mai multe colete; verifică split-ul")
            currency = str(raw.get("currencyCode") or "")
            if currency not in {"RON", "EUR", "BGN", "HUF"}:
                raise ValueError("monedă necunoscută")
            lines = []
            for line in raw.get("lines", []):
                if line.get("orderLineItemStatusName") not in {"Shipped", "Delivered", "Invoiced"}:
                    raise ValueError("stare produs neeligibilă")
                qty = dec(line.get("quantity"))
                if qty <= 0:
                    raise ValueError("cantitate invalidă")
                details = line.get("discountDetails") or []
                if details and len(details) != qty:
                    raise ValueError("detalii cantitate incomplete")
                subsidy = sum((dec(d.get("lineItemTyDiscount", 0)) for d in details), Decimal("0"))
                if not details and dec(line.get("lineTyDiscount", 0)):
                    raise ValueError("reducere fără detalii")
                amount = money(dec(line.get("lineUnitPrice")) * qty + subsidy)
                if amount < 0:
                    raise ValueError("valoare negativă")
                code = str(line.get("barcode") or "")
                product = mapping.get(code)
                name = (product.name if product else "") or str(line.get("productName") or code)
                report_code = product.fgo_code if product and product.fgo_code else code
                lines.append((report_code, name, qty, amount, code == "__transport__"))
            total = sum((line[3] for line in lines), Decimal("0"))
            target = money(dec(raw.get("packageGrossAmount")) - dec(raw.get("packageSellerDiscount")))
            if total != target:
                raise ValueError("totalul produselor nu corespunde sumei de vânzare")
            key = (country(raw), currency)
            bucket = countries.setdefault(key, {"country": key[0], "currency": currency, "orders": set(), "packages": 0, "quantity": Decimal("0"), "total": Decimal("0")})
            bucket["orders"].add((raw.get("_provider", "trendyol"), str(raw.get("orderNumber"))))
            bucket["packages"] += 1
            bucket["quantity"] += sum((line[2] for line in lines if not line[4]), Decimal("0"))
            bucket["total"] += total
            for code, name, qty, amount, shipping in lines:
                if shipping:
                    continue
                product = products.setdefault((code,currency), {"code":code,"name":name,"currency":currency,"quantity":Decimal("0"),"total":Decimal("0")})
                product["quantity"] += qty
                product["total"] += amount
            seen_packages.add(pid)
            seen_items.update(claims)
        except (ValueError, TypeError, OverflowError, OSError) as exc:
            errors.append(f"Pachet {pid}: {exc}")
    top = []
    for currency in sorted({p["currency"] for p in products.values()}):
        ranked = sorted((p for p in products.values() if p["currency"] == currency), key=lambda p: (-p["total"],-p["quantity"],p["code"]))[:5]
        top.extend({**p,"rank":rank} for rank,p in enumerate(ranked,1))
    return [dict(v,orders=len(v["orders"])) for _,v in sorted(countries.items())], top, errors
