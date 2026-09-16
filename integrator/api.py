from __future__ import annotations

import base64
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
import socket
import time
from urllib import error, parse, request

from .domain import package_id


class ApiError(Exception):
    def __init__(self, message, status=None, uncertain=False):
        super().__init__(message)
        self.status = status
        self.uncertain = uncertain


class InvoiceMissing(ApiError):
    """FGO explicitly confirmed that the requested invoice does not exist."""


def json_wire(value):
    """Serialize Decimal as exact JSON numbers, without binary floating point."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Număr JSON invalid.")
        return format(value, "f")
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(str(k)) + ":" + json_wire(v) for k, v in value.items()) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(json_wire(v) for v in value) + "]"
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def invoice_wire_payload(payload):
    """FGO validates the lexical VAT value against its nomenclature (21 / 11)."""
    content = []
    for item in payload["Continut"]:
        try:
            vat = Decimal(str(item["CotaTVA"]))
        except (InvalidOperation, KeyError):
            raise ValueError("Cota TVA lipsește sau este invalidă.") from None
        if not vat.is_finite() or vat not in {Decimal("21"), Decimal("11")}:
            raise ValueError("Cota TVA trebuie să fie 21 sau 11 în regimul configurat.")
        # Decimal('0.2100') * 100 retains its scale. Send integer 21, never 21.0000.
        # Keep monetary values and the original preview/history payload unchanged.
        content.append({**item, "CotaTVA": int(vat)})
    return {**payload, "Continut": content}


def valid_invoice_url(url):
    u = parse.urlsplit(str(url))
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not host or u.username or u.password or u.port not in {None, 443}:
        raise ValueError("Factura trebuie să aibă un link HTTPS public valid.")
    if host in {"localhost", "127.0.0.1", "::1"} or "." not in host:
        raise ValueError("Linkul facturii nu poate fi o adresă locală.")
    return str(url)


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Transport:
    def __init__(self, secret_values=()):
        self.opener = request.build_opener(NoRedirect())
        self.secrets = [s for s in secret_values if s]
        self.last_calls = {}

    def redact(self, message):
        result = str(message)
        for secret in self.secrets:
            result = result.replace(secret, "[ascuns]")
        result = re.sub(r'https?://\S+', '[link]', result)
        return result[:700]

    def call(self, method, url, headers=None, body=None, interval=1.1, read_only=False):
        host = parse.urlsplit(url).hostname
        attempts = 3 if read_only else 1
        for attempt in range(attempts):
            time.sleep(max(0, self.last_calls.get(host, 0) + interval - time.monotonic()))
            self.last_calls[host] = time.monotonic()
            data = json_wire(body).encode("utf-8") if body is not None else None
            h = {"Accept": "application/json", "Content-Type": "application/json", **(headers or {})}
            req = request.Request(url, data=data, headers=h, method=method)
            try:
                with self.opener.open(req, timeout=30) as response:
                    status = response.status
                    raw = response.read(8 * 1024 * 1024 + 1)
                    if len(raw) > 8 * 1024 * 1024:
                        raise ApiError("Răspuns API prea mare.", uncertain=not read_only)
                    try:
                        payload = json.loads(raw.decode("utf-8-sig"), parse_float=Decimal) if raw.strip() else {}
                    except (ValueError, UnicodeError):
                        raise ApiError("API-ul a returnat un răspuns care nu este JSON.", status, not read_only) from None
                    return status, payload
            except error.HTTPError as exc:
                if read_only and exc.code in {429, 500, 502, 503, 504} and attempt + 1 < attempts:
                    retry = exc.headers.get("Retry-After", "")
                    time.sleep(min(30, int(retry) if retry.isdigit() else 2 ** (attempt+1)))
                    continue
                # Response text is not persisted: it may contain credentials or customer data.
                explanation = {401: "Autentificare respinsă. Verifică cheile și mediul.", 403: "Acces refuzat. Verifică permisiunile/IP-ul pentru mediul de test.", 409: "Conflict: factura sau linkul poate exista deja. Verifică asocierea.", 429: "Limită API atinsă. Reîncearcă mai târziu.", 426: "Endpointul necesită actualizare."}.get(exc.code, "Cerere respinsă de serviciu. Verifică setările și documentul.")
                raise ApiError(f"HTTP {exc.code}: {explanation}", exc.code, not read_only and exc.code >= 500) from None
            except (error.URLError, TimeoutError, socket.timeout, ConnectionError, OSError):
                if read_only and attempt + 1 < attempts:
                    time.sleep(2 ** (attempt+1))
                    continue
                raise ApiError("Conexiune întreruptă sau timp de răspuns depășit.", uncertain=not read_only) from None


class TrendyolAPI:
    def __init__(self, settings, transport=None):
        settings.require_credentials("trendyol")
        self.settings = settings
        self.transport = transport or Transport((settings.api_key, settings.api_secret))
        self.base = "https://apigw.trendyol.com" if settings.mode == "production" else "https://stageapigw.trendyol.com"

    def headers(self, market):
        token = base64.b64encode(f"{self.settings.api_key}:{self.settings.api_secret}".encode()).decode()
        return {"Authorization": "Basic " + token, "User-Agent": f"{self.settings.seller_id} - SelfIntegration", "storeFrontCode": market}

    def _orders(self, market, params):
        url = f"{self.base}/integration/order/sellers/{self.settings.seller_id}/v2/orders?{parse.urlencode(params)}"
        _, result = self.transport.call("GET", url, self.headers(market), interval=2.1, read_only=True)
        if not isinstance(result, dict) or not isinstance(result.get("content"), list):
            raise ApiError("Structură neașteptată pentru lista de comenzi.")
        return result

    def fetch(self, market, start: date, end: date):
        today = date.today()
        if start > end or start < today - timedelta(days=29) or end > today:
            raise ValueError("Trendyol permite ultimele 30 de zile. Alege un interval valid, fără date viitoare.")
        # Broad UTC boundaries cover local timezone edges; duplicates are merged by package ID.
        first = datetime.combine(start, datetime.min.time(), timezone.utc) - timedelta(hours=3)
        last = datetime.combine(end + timedelta(days=1), datetime.min.time(), timezone.utc)
        result = {}
        def window(a, b):
            params = {"startDate": a, "endDate": b, "size": 200, "page": 0, "orderByField": "PackageLastModifiedDate", "orderByDirection": "ASC"}
            page = self._orders(market, params)
            total = int(page.get("totalElements", 0))
            if total > 10000:
                if b-a <= 1:
                    raise ApiError("Peste 10.000 de pachete la aceeași dată; este necesar serviciul Stream.")
                mid = (a+b)//2
                window(a, mid)
                window(mid+1, b)
                return
            pages = int(page.get("totalPages", 1))
            if pages > 50 or pages < 0:
                raise ApiError("Paginare inconsistentă; sincronizarea a fost oprită.")
            for p in range(max(1, pages)):
                if p:
                    params["page"] = p
                    page = self._orders(market, params)
                    if int(page.get("totalElements", 0)) > 10000:
                        raise ApiError("Lista s-a mărit în timpul sincronizării. Rulează din nou pe un interval mai scurt.")
                if p < pages-1 and not page["content"]:
                    raise ApiError("Pagină intermediară goală; reia sincronizarea.")
                for raw in page["content"]:
                    raw["_market"] = market
                    result[package_id(raw)] = raw
        cursor = int(first.timestamp()*1000)
        stop = int(last.timestamp()*1000)-1
        while cursor <= stop:
            boundary = min(stop, cursor + 13*86400000 - 1)
            window(cursor, boundary)
            cursor = boundary+1
        return list(result.values())

    def get_package(self, pid, market):
        data = self._orders(market, {"shipmentPackageIds": pid, "size": 200, "page": 0})
        matches = [r for r in data["content"] if package_id(r) == str(pid)]
        if len(matches) != 1:
            raise ApiError("Pachetul nu mai poate fi regăsit univoc în Trendyol. Verifică vechimea sau modificarea lui.")
        matches[0]["_market"] = market
        return matches[0]

    def upload(self, pid, market, url):
        url = valid_invoice_url(url)
        endpoint = f"{self.base}/integration/sellers/{self.settings.seller_id}/seller-invoice-links"
        status, _ = self.transport.call("POST", endpoint, self.headers(market), {"invoiceLink": url, "shipmentPackageId": int(pid)}, interval=2.1)
        if status != 201:
            raise ApiError(f"Trendyol a răspuns HTTP {status}; confirmarea 201 lipsește.", status, True)


class FgoAPI:
    def __init__(self, settings, transport=None):
        settings.require_credentials("fgo")
        self.settings = settings
        self.transport = transport or Transport((settings.fgo_key,))
        self.base = "https://api.fgo.ro/v1" if settings.mode == "production" else "https://api-testuat.fgo.ro/v1"
        self.articles = {}

    def auth(self, suffix):
        return {"CodUnic": self.settings.cui, "Hash": hashlib.sha1((self.settings.cui+self.settings.fgo_key+suffix).encode("utf-8")).hexdigest().upper(), "PlatformaUrl": self.settings.platform_url}

    def _invoice(self, endpoint, payload, suffix, read_only=False):
        _, response = self.transport.call("POST", self.base+endpoint, body={**payload, **self.auth(suffix)}, read_only=read_only)
        if not isinstance(response, dict):
            raise ApiError("Răspuns FGO invalid.", uncertain=not read_only)
        inv = response.get("Factura") or {}
        if response.get("Success") is not True:
            msg = self.transport.redact(response.get("Message") or "FGO a respins operațiunea.")
            if endpoint == "/factura/print" and response.get("Success") is False and not inv and msg.strip().rstrip(".!").casefold() in {"factura nu exista", "factura nu există"}:
                raise InvoiceMissing("FGO confirmă: factura nu există.")
            # A duplicate response can contain an existing invoice. Do not attach blindly.
            raise ApiError("FGO: "+msg, uncertain=bool(inv))
        if not isinstance(inv, dict) or not inv.get("Serie") or not inv.get("Numar"):
            raise ApiError("FGO nu a returnat seria și numărul; verifică emiterea în FGO.", uncertain=not read_only)
        link = inv.get("Link")
        if not link:
            raise ApiError("FGO nu a returnat linkul facturii. Asociază documentul după verificare.", uncertain=not read_only)
        try:
            valid_invoice_url(link)
        except ValueError as exc:
            raise ApiError(str(exc), uncertain=not read_only) from None
        return {"series": str(inv["Serie"]), "number": str(inv["Numar"]), "url": str(link)}

    def issue(self, payload):
        return self._invoice("/factura/emitere", invoice_wire_payload(payload), payload["Client"]["Denumire"])

    def get_article(self, code):
        if code not in self.articles:
            _, response = self.transport.call("POST", self.base+"/articol/get", body={**self.auth(""), "CodArticol": code}, interval=5.1, read_only=True)
            result = response.get("Result") if isinstance(response, dict) else None
            if not isinstance(response, dict) or response.get("Success") is not True or not isinstance(result, dict):
                raise ValueError(f"Articolul FGO {code} nu a putut fi citit. Verifică acest cod și accesul API FGO.")
            if result.get("CodConta") and str(result["CodConta"]).strip() != code:
                raise ValueError(f"FGO a returnat alt cod pentru articolul {code}; asocierea a fost oprită.")
            name = str(result.get("Nume") or "").strip()
            unit = str(result.get("UM") or "").strip()
            if not name or len(name) > 1000 or not unit or len(unit) > 5:
                raise ValueError(f"Articolul FGO {code} are denumire sau UM lipsă / prea lungă. Completează articolul în FGO.")
            try:
                vat = Decimal(str(result.get("CotaTva")))
            except InvalidOperation:
                raise ValueError(f"Lipsește cota TVA pentru articolul FGO {code}.") from None
            if not vat.is_finite():
                raise ValueError(f"Cota TVA a articolului FGO {code} este invalidă.")
            if vat in {Decimal("0.21"), Decimal("0.11")}:
                vat *= 100  # The article API returns a fraction; invoice lines use percent.
            if vat not in {Decimal("21"), Decimal("11")}:
                raise ValueError(f"Articolul FGO {code} are o cotă TVA neacceptată în regimul configurat. Verifică articolul în FGO.")
            self.articles[code] = {"name": name, "unit": unit, "vat": Decimal(int(vat))}
        return self.articles[code]

    def validate_articles(self, content):
        for item in content:
            code = item.get("CodArticol")
            if not code:
                continue
            article = self.get_article(code)
            if article["name"] != item["Denumire"] or article["unit"] != item["UM"] or article["vat"] != item["CotaTVA"]:
                raise ValueError(f"Articolul FGO {code} s-a modificat. Preia din nou articolele FGO și verifică previzualizarea înainte de emitere.")

    def print_invoice(self, series, number):
        invoice = self._invoice("/factura/print", {"Serie": series, "Numar": number}, number, read_only=True)
        if invoice["series"] != series or invoice["number"] != number:
            raise ApiError("FGO a returnat altă serie sau alt număr decât cele solicitate.")
        return invoice
