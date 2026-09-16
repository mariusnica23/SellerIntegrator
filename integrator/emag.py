"""eMAG Marketplace API v4.5.1: orders and invoice attachments, seller fulfillment."""
import base64
from datetime import date, timedelta
from decimal import Decimal

from .api import ApiError, Transport, valid_invoice_url
from .domain import dec, money, package_id
from .mapping import Product
from .service import Service

BASES = {market: f"https://marketplace-api.emag.{domain}/api-3" for market,domain in (("RO","ro"),("BG","bg"),("HU","hu"))}
STATUSES = {0:"Cancelled",1:"Created",2:"Picking",3:"Prepared",4:"Shipped",5:"Returned"}
DEFAULT_CURRENCY = {"RO":"RON","BG":"EUR","HU":"HUF"}


def country_code(value):
    text = str(value or "").strip().casefold()
    return {"romania":"RO","românia":"RO","bulgaria":"BG","българия":"BG","hungary":"HU","ungaria":"HU","magyarország":"HU"}.get(text,text.upper())


def ean_values(value):
    if isinstance(value,str):value=[value]
    if not isinstance(value,list) or not value or any(not isinstance(x,str) or not x.strip().isascii() or not x.strip().isdigit() or not 6<=len(x.strip())<=14 for x in value):
        raise ValueError("eMAG: EAN lipsă sau invalid în datele produsului; completează codurile EAN în eMAG.")
    return list(dict.fromkeys(x.strip() for x in value))


def envelope(order, market, attachments=None):
    customer = order.get("customer") or {}
    bill = {"fullName":customer.get("billing_name") or customer.get("name"),"countryCode":country_code(customer.get("billing_country")),"city":customer.get("billing_city"),"countyName":customer.get("billing_suburb"),"fullAddress":customer.get("billing_street")}
    attachments = order.get("attachments", []) if attachments is None else attachments
    invoices = [a for a in attachments if str(a.get("type")) == "1"]
    currency = next((p.get("currency") for p in order.get("products",[]) if p.get("currency")), DEFAULT_CURRENCY[market])
    raw = {"_provider":"emag","_market":market,"_emag":order,"_ordered_on":str(order.get("date") or "")[:10],"orderNumber":str(order["id"]),"shipmentPackageId":order["id"],"invoiceAddress":bill,"shipmentAddress":{"countryCode":country_code(customer.get("shipping_country"))},"commercial":customer.get("legal_entity") != 0,"micro":False,"currencyCode":currency,"shipmentPackageStatus":STATUSES.get(order.get("status"),"Unknown"),"invoiceStatus":"Invoiced" if invoices else "NotInvoiced","invoiceLink":invoices[0].get("url","") if invoices else "","paymentMethod":{1:"Ramburs",2:"Transfer bancar",3:"Card"}.get(order.get("payment_mode_id"),""),"lines":[]}
    for product in order.get("products",[]):
        eans = product.get("ean") or order.get("_offer_eans", {}).get(str(product.get("product_id")), [])
        barcode = eans if isinstance(eans, str) else ", ".join(str(e) for e in eans) if isinstance(eans, list) else ""
        raw["lines"].append({"barcode":barcode,"productName":product.get("name", ""),"quantity":product.get("quantity"),"lineId":f"emag-{market}-{order['id']}-{product.get('id')}","orderLineItemStatusName":raw["shipmentPackageStatus"]})
    return raw


def invoice_order(raw, mapping, settings):
    order,market = raw["_emag"],raw["_market"]
    if order.get("type") != 3 or order.get("is_complete") == 0 or order.get("is_storno"):
        raise ValueError("eMAG: sunt acceptate comenzi complete, livrate de vânzător, fără storno.")
    if order.get("payment_mode_id") == 3 and order.get("payment_status") != 1:
        raise ValueError("eMAG: plata online nu este confirmată.")
    lines, resolved = [], {}
    sales = paid_total = subsidy_total = seller_discount = Decimal("0")
    seen_vouchers = set()
    for product in order.get("products",[]):
        if product.get("status") == 0:
            continue
        if product.get("status") != 1 or product.get("recycle_warranties"):
            raise ValueError("eMAG: stare produs necunoscută sau garanție SGR; verifică separat.")
        code = str(product.get("product_id") or "")
        if settings.unified_mapping_path:
            value = product.get("ean") or order.get("_offer_eans", {}).get(code)
            if not value and order.get("_ean_error"):
                raise ValueError(order["_ean_error"])
            eans=ean_values(value)
            matches=[mapping[e] for e in eans if e in mapping]
            if not matches:raise ValueError(f"eMAG: niciun EAN al produsului {code} nu este mapat în Excelul comun.")
            if len({a.fgo_code for a in matches})!=1:raise ValueError(f"eMAG: EAN-urile produsului {code} indică articole FGO diferite.")
            article=matches[0]
        else:
            article = mapping.get(f"{market}:{code}") or mapping.get(code)
        if not article:
            raise ValueError(f"eMAG {market}: product_id {code} nemapat în Excel.")
        if article.fgo_code and not article.fgo_verified and settings.mode != "demo":
            raise ValueError(f"Articolul FGO {article.fgo_code} nu este verificat. Preia articolele eMAG din FGO.")
        qty, price = dec(product.get("quantity")), dec(product.get("sale_price"))
        if qty <= 0 or qty != qty.to_integral_value() or price < 0:
            raise ValueError("eMAG: cantitate sau preț invalid.")
        currency = product.get("currency") or DEFAULT_CURRENCY[market]
        if currency != raw["currencyCode"]:
            raise ValueError("eMAG: monede diferite în aceeași comandă.")
        if product.get("vat") is not None:
            vat = dec(product["vat"])
            if vat <= 1: vat *= 100
            if vat != article.vat:
                raise ValueError("eMAG: TVA din comandă diferă de articolul FGO; verifică regimul fiscal.")
        gross = money(price * qty * (1 + article.vat / 100))
        seller = subsidy = Decimal("0")
        for voucher in product.get("product_voucher_split") or []:
            discount = dec(voucher.get("value")) + dec(voucher.get("vat_value"))
            if discount > 0:
                raise ValueError("eMAG: reducerea voucherului trebuie să fie negativă.")
            who = str(voucher.get("offered_by") or "").strip().casefold()
            if who not in {"emag", "seller"}:
                raise ValueError("eMAG: lipsește finanțatorul voucherului (eMAG/seller).")
            seen_vouchers.add(str(voucher.get("voucher_id")))
            if who == "emag": subsidy -= discount
            else: seller -= discount
        paid = money(gross - seller - subsidy)
        if paid < 0:
            raise ValueError("eMAG: reducerile depășesc valoarea produsului.")
        key = f"emag-{market}-{code}"
        resolved[key] = article
        # Individual cent allocations retain the exact paid/subsidized line totals.
        details = []
        paid_cents, subsidy_cents = int(paid*100), int(money(subsidy)*100)
        for index in range(int(qty)):
            p = Decimal(paid_cents // int(qty) + (index < paid_cents % int(qty))) / 100
            s = Decimal(subsidy_cents // int(qty) + (index < subsidy_cents % int(qty))) / 100
            details.append({"lineItemPrice":p,"lineItemTyDiscount":s})
        lines.append({"barcode":key,"productName":article.name or product.get("name", ""),"quantity":qty,"lineUnitPrice":paid/qty,"currencyCode":currency,"lineId":f"emag-{market}-{order['id']}-{product['id']}","orderLineItemStatusName":raw["shipmentPackageStatus"],"discountDetails":details,"lineTyDiscount":subsidy})
        sales += gross; paid_total += paid; subsidy_total += money(subsidy); seller_discount += money(seller)
    # A voucher not distributed at product level must not disappear from the invoice.
    if any(str(v.get("voucher_id")) not in seen_vouchers and (dec(v.get("sale_price",0)) or dec(v.get("sale_price_vat",0))) for v in order.get("vouchers") or []):
        raise ValueError("eMAG: voucher fără distribuție completă pe produse; verifică manual.")
    shipping = dec(order.get("shipping_tax",0))
    if shipping < 0 or order.get("shipping_tax_voucher_split"):
        raise ValueError("eMAG: reducerile/ajustările de transport necesită verificare separată.")
    if shipping:
        article = mapping.get("__transport__")
        if not article or not article.fgo_verified or not settings.emag_shipping_tax_mode:
            raise ValueError("eMAG: configurează articolul FGO pentru transport și dacă shipping_tax include TVA.")
        gross = money(shipping * (1+article.vat/100)) if settings.emag_shipping_tax_mode == "fara_tva" else money(shipping)
        resolved["__transport__"] = article
        lines.append({"barcode":"__transport__","productName":article.name,"quantity":1,"lineUnitPrice":gross,"currencyCode":raw["currencyCode"],"lineId":f"emag-{market}-{order['id']}-transport","orderLineItemStatusName":raw["shipmentPackageStatus"]})
        sales += gross; paid_total += gross
    return {**raw,"lines":lines,"packageGrossAmount":sales,"packageSellerDiscount":seller_discount,"packageTotalPrice":paid_total,"packageTyDiscount":subsidy_total}, resolved


class EmagAPI:
    def __init__(self, settings, transport=None):
        self.settings = settings
        self.transport = transport or Transport([getattr(settings,f"emag_password_{m.lower()}") for m in BASES])
        self.ean_cache = {}

    def enrich_eans(self,order,market,refresh=False):
        if not self.settings.unified_mapping_path:return order
        order.pop("_ean_error", None)
        # Enrichment is metadata: do not mutate products or historical fingerprints.
        order["_offer_eans"] = {}
        for product in order.get("products",[]):
            if product.get("status")==0:continue
            if product.get("ean"):
                ean_values(product["ean"]);continue
            code=product.get("product_id");key=(market,str(code))
            if key not in self.ean_cache or refresh:
                rows=self.call(market,"/product_offer/read",{"id":int(code),"itemsPerPage":100,"currentPage":1}).get("results")
                matches=[p for p in rows if str(p.get("id"))==str(code)] if isinstance(rows,list) else []
                if len(matches)!=1:raise ValueError(f"eMAG {market}: produsul {code} nu poate fi identificat pentru citirea EAN.")
                eans=ean_values(matches[0].get("ean"))
                if not eans:raise ValueError(f"eMAG {market}: produsul {code} nu are EAN returnat de API.")
                self.ean_cache[key]=eans
            order["_offer_eans"][str(code)]=list(self.ean_cache[key])
        return order

    def call(self, market, path, payload, read_only=True):
        if self.settings.mode != "production":
            raise ValueError("eMAG: conexiunea API este disponibilă în mediul production; nu există un mediu test configurat.")
        user,password = (getattr(self.settings,f"emag_{key}_{market.lower()}") for key in ("user","password"))
        if not user or not password:
            raise ValueError(f"Completează utilizatorul și parola API eMAG {market} în Parametrizare.")
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        _,result = self.transport.call("POST",BASES[market]+path,{"Authorization":"Basic "+token},payload,interval=0.4,read_only=read_only)
        if not isinstance(result,dict) or result.get("isError") is not False:
            raise ApiError("eMAG: "+self.transport.redact(result.get("messages", "Răspuns invalid") if isinstance(result,dict) else "Răspuns invalid"),uncertain=not read_only)
        return result

    def fetch(self, market, start, end):
        if start > end or end > date.today() or (end-start).days>366:
            raise ValueError("eMAG: alege un interval de maximum un an, fără date viitoare.")
        result={}; cursor=start
        while cursor <= end:
            stop = min(end,cursor+timedelta(days=27))
            pages_seen=set()
            for page in range(1,1001):
                rows=self.call(market,"/order/read",{"type":3,"modifiedAfter":cursor.isoformat()+" 00:00:00","modifiedBefore":stop.isoformat()+" 23:59:59","currentPage":page,"itemsPerPage":100}).get("results")
                if not isinstance(rows,list): raise ApiError("eMAG: lista comenzilor are un format necunoscut.")
                identity=tuple(str(r.get("id")) for r in rows)
                if identity and identity in pages_seen: raise ApiError("eMAG: pagina de comenzi se repetă; sincronizarea a fost oprită.")
                pages_seen.add(identity)
                for order in rows:
                    # A missing EAN blocks only that invoice; keep the order visible for correction.
                    try:self.enrich_eans(order,market)
                    except (ApiError,ValueError,TypeError) as exc:order["_ean_error"]=str(exc)
                    raw=envelope(order,market); result[package_id(raw)]=raw
                if len(rows)<100: break
            else: raise ApiError("eMAG: prea multe pagini; restrânge perioada.")
            cursor=stop+timedelta(days=1)
        return list(result.values())

    def get_package(self, pid, market):
        order_id=int(pid.rsplit(":",1)[1])
        rows=self.call(market,"/order/read",{"id":order_id,"type":3}).get("results",[])
        rows=[r for r in rows if r.get("id")==order_id]
        if len(rows)!=1: raise ApiError("Comanda nu mai poate fi identificată univoc în eMAG.")
        attachments=self.call(market,"/order/attachments/read",{"order_id":order_id,"order_type":3}).get("results")
        if not isinstance(attachments,list): raise ApiError("eMAG: atașamentele nu au putut fi verificate.")
        order=self.enrich_eans(rows[0],market,refresh=True)
        return envelope(order,market,attachments)

    def upload(self,pid,market,url):
        self.call(market,"/order/attachments/save",{"data":[{"order_id":int(pid.rsplit(":",1)[1]),"order_type":3,"type":1,"name":"Factura FGO.pdf","url":valid_invoice_url(url)}]},read_only=False)


class EmagService(Service):
    def __init__(self,settings,store,mapping,emag=None,fgo=None,other_stores=()):
        super().__init__(settings,store,mapping,trendyol=emag or EmagAPI(settings),fgo=fgo,other_stores=other_stores)
        if settings.emag_shipping_code and ("__transport__" not in self.mapping or self.mapping["__transport__"].fgo_code != settings.emag_shipping_code):
            self.mapping["__transport__"] = Product("__transport__","",fgo_code=settings.emag_shipping_code)

    def sync(self,start,end,progress=lambda _:None):
        count,errors=0,[]
        for market in dict.fromkeys(m.strip() for m in self.settings.emag_markets.split(",")):
            try:
                progress(f"Preiau comenzile eMAG {market}…")
                rows=self.trendyol.fetch(market,start,end)
                self.store.put_orders(rows); count+=len(rows)
            except (ApiError,ValueError) as exc: errors.append(f"eMAG {market}: {exc}")
        errors.extend(self.resolve_articles(progress=progress))
        for pid,rec in self.store.records().items():
            if not rec["invoice"]: continue
            try:
                progress(f"Verific factura eMAG • {pid}…")
                raw=self.trendyol.get_package(pid,rec["draft"]["country"]); self.store.put_orders([raw]); self.refresh_invoice(pid,raw)
            except (ApiError,ValueError) as exc: errors.append(f"{pid}: {exc}")
        return count,errors
