from copy import deepcopy
from decimal import Decimal
from datetime import date

from .mapping import Product


def demo_mapping():
    return {"0001234567890": Product("0001234567890", "Organizator pentru sertar", fgo_code="ORG-01", fgo_verified=True),
            "5940000000022": Product("5940000000022", "Suport pentru telefon", fgo_code="SUP-02", fgo_verified=True)}


def demo_orders():
    result = []
    for index, (market, customer, currency, unit, qty, barcode) in enumerate([
        ("RO", "Client demonstrativ România", "RON", "121", 2, "0001234567890"),
        ("BG", "Client demonstrativ Bulgaria", "EUR", "24.20", 1, "5940000000022"),
        ("GR", "Client demonstrativ Grecia", "EUR", "36.30", 2, "0001234567890"),
        ("RO", "Client demonstrativ nemapat", "RON", "60.50", 1, "DEMO-NEMAPAT"),
    ], 1):
        address = {"fullName": customer, "city": {"RO": "Bucuresti", "BG": "Sofia", "GR": "Athina"}[market], "countyName": "Bucuresti" if market == "RO" else "", "fullAddress": "Adresă fictivă pentru demonstrație 1", "countryCode": market}
        result.append({"shipmentPackageId": 990000+index, "orderNumber": f"DEMO-{index:04}", "_ordered_on": date.today().isoformat(), "invoiceStatus": "NotInvoiced", "shipmentAddress": address, "invoiceAddress": deepcopy(address), "commercial": False, "micro": False,
                       "currencyCode": currency, "shipmentPackageStatus": "Delivered" if index % 2 else "Shipped", "customerId": 900+index, "packageTotalPrice": str(Decimal(unit)*qty), "packageGrossAmount": str(Decimal(unit)*qty), "packageSellerDiscount": "0", "packageTyDiscount": "0", "paymentMethod": "Card (demo)", "_market": market,
                       "lines": [{"lineId": 800000+index, "barcode": barcode, "productName": "Titlu produs din Trendyol", "quantity": qty, "lineUnitPrice": unit, "currencyCode": currency, "orderLineItemStatusName": "Delivered" if index % 2 else "Shipped", "lineTyDiscount": 0}]})
    return result


class DemoTrendyol:
    def __init__(self, store):
        self.store = store

    def fetch(self, market, start, end):
        return [x for x in demo_orders() if x["_market"] == market]

    def get_package(self, pid, market):
        raw = self.store.order(pid)
        return raw

    def upload(self, pid, market, url):
        raw = self.store.order(pid)
        raw.update(invoiceLink=url, invoiceStatus="Invoiced")
        self.store.put_orders([raw])


class DemoFgo:
    def validate_articles(self, content):
        return None

    def issue(self, payload):
        number = payload["IdExtern"].replace("-", "")[:8].upper()
        return {"series": "DEMO", "number": number, "url": "https://example.invalid/demo/"+number+".pdf"}

    def print_invoice(self, series, number):
        return {"series": series, "number": number, "url": "https://example.invalid/demo/"+number+".pdf"}
