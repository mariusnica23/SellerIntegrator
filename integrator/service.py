from __future__ import annotations

from datetime import date
from dataclasses import replace
from decimal import Decimal

from .api import ApiError, InvoiceMissing, FgoAPI, TrendyolAPI
from .demo import DemoFgo, DemoTrendyol
from .domain import READY_STATUSES, build_draft, fiscal_budget, signature, package_id, remote_invoice_present
from .store import claim_conflicts


def issue_batch(drafts, services, progress=lambda _: None):
    """Attempt each approved package once; a failure never retries that invoice."""
    count, failures, attempted = 0, [], set()
    for draft in drafts:
        pid = draft.package_id
        if pid in attempted:
            continue
        attempted.add(pid)
        progress(f"Emit factura pentru {pid}…")
        try:
            services[pid].issue(draft)
            count += 1
        except Exception as exc:
            failures.append(f"{pid}: {exc}")
    return count, failures


class Service:
    def __init__(self, settings, store, mapping, trendyol=None, fgo=None, other_stores=(), catalog=None):
        self.settings, self.store, self.mapping = settings, store, mapping
        self._trendyol, self._fgo = trendyol, fgo
        self.other_stores = other_stores
        self.catalog = catalog
        if catalog and settings.mode != "demo":
            self.mapping = catalog.hydrate(settings.fgo_scope(), mapping)

    @property
    def trendyol(self):
        if self._trendyol is None:
            self._trendyol = DemoTrendyol(self.store) if self.settings.mode == "demo" else TrendyolAPI(self.settings)
        return self._trendyol

    @property
    def fgo(self):
        if self._fgo is None:
            self._fgo = DemoFgo() if self.settings.mode == "demo" else FgoAPI(self.settings, catalog=self.catalog)
        return self._fgo

    def sync(self, start, end, progress=lambda _: None):
        all_rows, failures = [], []
        for market in self.settings.markets.split(","):
            market = market.strip()
            try:
                progress(f"Preiau comenzile din {market}…")
                rows = self.trendyol.fetch(market, start, end)
                self.store.put_orders(rows)
                all_rows.extend(rows)
            except (ApiError, ValueError) as exc:
                failures.append(f"{market}: {exc}")
        barcodes = {str(line.get("barcode") or "") for raw in all_rows if raw.get("shipmentPackageStatus", raw.get("status")) in READY_STATUSES and not raw.get("invoiceLink") for line in raw.get("lines", [])}
        failures.extend(self.resolve_articles(barcodes, progress))
        # Refresh every locally associated invoice, including packages outside the date filter.
        fresh_by_id = {package_id(row): row for row in all_rows}
        for pid, rec in self.store.records().items():
            if not rec["invoice"]:
                continue
            try:
                progress(f"Verific factura FGO și încărcarea în Trendyol • pachet {pid}…")
                raw = fresh_by_id.get(pid)
                if raw is None:
                    raw = self.trendyol.get_package(pid, rec["draft"]["country"])
                    self.store.put_orders([raw])
                self.refresh_invoice(pid, raw)
            except (ApiError, ValueError) as exc:
                self.store.transition(pid, self.store.record(pid)["state"], error=f"Verificare nereușită: {exc}")
                failures.append(f"Pachet {pid}: {exc}")
        return len(all_rows), failures

    @staticmethod
    def invoice_absent(raw):
        return not remote_invoice_present(raw) and (raw.get("invoiceStatus") == "NotInvoiced" or "invoiceLink" in raw)

    def refresh_invoice(self, pid, raw):
        rec = self.store.record(pid)
        if not rec or not rec["invoice"]:
            return
        inv = rec["invoice"]
        try:
            current = self.fgo.print_invoice(inv["series"], inv["number"])
        except InvoiceMissing:
            if self.invoice_absent(raw):
                self.store.mark_missing(pid)
                return
            self.store.transition(pid, "remote_conflict", error="Factura lipsește din FGO, dar Trendyol nu confirmă lipsa ei. Verifică factura din Trendyol, apoi actualizează comenzile.")
            return
        existing = raw.get("invoiceLink")
        if existing and existing not in {current["url"], inv["url"]}:
            self.store.transition(pid, "remote_invoice", current, error="Factură prezentă în Trendyol, cu link diferit de cel FGO. Existența este confirmată; identitatea documentului trebuie verificată înainte de înlocuire.")
        elif existing:
            self.store.transition(pid, "uploaded", current)
        elif self.invoice_absent(raw):
            self.store.transition(pid, "issued", current, error="Factura există în FGO, dar lipsește din Trendyol. Poți încărca din nou.")
        elif raw.get("invoiceStatus") == "Received":
            self.store.transition(pid, "upload_uncertain", current, error="Trendyol verifică factura primită. Actualizează comenzile pentru rezultat.")
        else:
            self.store.transition(pid, "upload_uncertain", current, error="Factura există în FGO; starea/linkul din Trendyol necesită verificare înaintea unei noi încărcări.")

    def resolve_articles(self, barcodes=None, progress=lambda _: None, force=False):
        if self.settings.mode == "demo":
            return []
        chosen = [p for barcode, p in self.mapping.items() if p.fgo_code and (barcodes is None or barcode in barcodes)]
        if not chosen:
            return []
        failures = []
        grouped = {}
        for p in chosen:
            grouped.setdefault(p.fgo_code, []).append(p)
        for index, (code, products) in enumerate(grouped.items(), 1):
            progress(f"Preiau articolul FGO {index}/{len(grouped)} • {code}…")
            try:
                article = self.catalog.article(self.settings.fgo_scope(), code) if self.catalog and not force else None
                if article is None:
                    if force and self.catalog:
                        self.catalog.forget(self.settings.fgo_scope(), "fgo", code)
                    for p in products:
                        self.mapping[p.barcode] = replace(p, name="", unit="", fgo_verified=False)
                    fgo = self.fgo
                    if force and hasattr(fgo, "articles"):
                        fgo.articles.pop(code, None)
                    article = fgo.get_article(code)
                    if self.catalog:
                        self.catalog.put(self.settings.fgo_scope(), "fgo", code, article)
                for p in products:
                    self.mapping[p.barcode] = replace(p, **article, fgo_verified=True)
            except ApiError as exc:
                failures.append(f"FGO: {exc}")
                break  # Do not repeat rejected authentication for every mapped product.
            except ValueError as exc:
                failures.append(str(exc))
        return failures

    def draft(self, raw, extra=Decimal("0")):
        draft = build_draft(raw, self.mapping, self.settings)
        self.store.assert_available(draft.package_id, draft.source_lines, raw.get("originPackageIds"))
        if draft.country != "RO" and draft.net_ron is not None:
            used = self.store.used_ron() + sum((s.used_ron() for s in self.other_stores), Decimal("0"))
            fiscal_budget(self.settings, used, extra+draft.net_ron)
        return draft

    def prepare(self, ids):
        # Whole batch validation before the first financial write.
        ready, errors, extra = [], [], Decimal("0")
        claimed = set()
        for pid in dict.fromkeys(ids):
            try:
                raw = self.store.order(pid)
                draft = self.draft(raw, extra)
                if any(claim_conflicts(a, b) for a in claimed for b in draft.source_lines):
                    raise ValueError("Lotul conține același produs sursă în două pachete.")
                claimed.update(draft.source_lines)
                ready.append(draft)
                if draft.net_ron is not None:
                    extra += draft.net_ron
            except ValueError as exc:
                errors.append(f"Pachet {pid}: {exc}")
        return ready, errors

    def issue(self, draft):
        self.settings.require_credentials("fgo")
        # Authenticate/preflight constructors before any reservation.
        fgo, trendyol = self.fgo, self.trendyol
        if draft.payload["DataEmitere"] != date.today().isoformat():
            raise ValueError("Data s-a schimbat. Refaceți previzualizarea.")
        fresh = trendyol.get_package(draft.package_id, draft.country)
        self.store.put_orders([fresh])
        current = self.draft(fresh)
        if current.fingerprint != draft.fingerprint or current.payload != draft.payload or current.net_ron != draft.net_ron:
            raise ValueError("Comanda sau configurarea s-a modificat. Refaceți previzualizarea.")
        if hasattr(fgo, "validate_articles"):
            fgo.validate_articles(draft.payload["Continut"])
        self.store.reserve(draft)
        try:
            invoice = fgo.issue(draft.payload)
        except ApiError as exc:
            self.store.transition(draft.package_id, "uncertain" if exc.uncertain or exc.status == 409 else "rejected", error=str(exc))
            raise
        except Exception:
            self.store.transition(draft.package_id, "uncertain", error="Rezultat necunoscut. Verifică factura în FGO înaintea unei alte emiteri.")
            raise
        self.store.transition(draft.package_id, "issued", invoice)
        return invoice

    def upload(self, pid):
        rec = self.store.record(pid)
        if not rec or not rec["invoice"]:
            raise ValueError("Pachetul nu are o factură FGO asociată.")
        if rec["state"] not in {"issued", "uploaded", "remote_invoice", "upload_failed", "upload_uncertain", "remote_conflict"}:
            raise ValueError("Starea facturii necesită verificare în Istoric.")
        inv = rec["invoice"]
        fresh = self.trendyol.get_package(pid, rec["draft"]["country"])
        self.store.put_orders([fresh])
        self.refresh_invoice(pid, fresh)
        rec = self.store.record(pid)
        if not rec["invoice"] or rec["state"] == "remote_conflict":
            raise ValueError(rec["error"])
        inv = rec["invoice"]
        existing = fresh.get("invoiceLink")
        if existing:
            if existing == inv["url"]:
                self.store.transition(pid, "uploaded")
                return "Link confirmat în Trendyol"
            self.store.transition(pid, "remote_invoice", error="Există deja un alt link de factură în marketplace. Verifică manual.")
            raise ValueError("Există alt link în Trendyol; asocierea nu a fost schimbată.")
        if not self.invoice_absent(fresh):
            raise ValueError("Trendyol nu confirmă lipsa facturii. Verifică factura sau așteaptă finalizarea validării în Trendyol.")
        status = fresh.get("shipmentPackageStatus", fresh.get("status"))
        if status not in READY_STATUSES | {"Invoiced"}:
            raise ValueError(f"Starea pachetului este {status}. Verifică factura și eventualul storno.")
        if signature(fresh) != rec["draft"]["fingerprint"]:
            raise ValueError("Conținutul pachetului s-a schimbat după facturare. Verifică documentul înainte de încărcare.")
        self.store.transition(pid, "uploading")
        try:
            self.trendyol.upload(pid, rec["draft"]["country"], inv["url"])
        except ApiError as exc:
            self.store.transition(pid, "upload_uncertain" if exc.uncertain else "upload_failed", error=str(exc))
            raise
        except Exception:
            self.store.transition(pid, "upload_uncertain", error="Rezultat necunoscut la încărcare. Reia pentru verificarea linkului.")
            raise
        self.store.transition(pid, "uploaded")
        return "Încărcată în Trendyol"

    def reconcile(self, pid, series, number):
        rec = self.store.record(pid)
        if not rec or rec["state"] not in {"uncertain", "rejected"}:
            raise ValueError("Asocierea manuală este disponibilă pentru emiteri incerte/respinse.")
        if not series.strip() or not number.strip():
            raise ValueError("Completează seria și numărul facturii existente.")
        invoice = self.fgo.print_invoice(series.strip(), number.strip())
        for other_id, other in self.store.records().items():
            if other_id != pid and other["invoice"] and (other["invoice"]["series"], other["invoice"]["number"]) == (invoice["series"], invoice["number"]):
                raise ValueError("Factura este deja asociată altui pachet.")
        # Reclaim lines after a previously rejected call before accepting manual association.
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.store.assert_claims(db, pid, rec["draft"]["source_lines"])
            for line_id in rec["draft"]["source_lines"]:
                owner = db.execute("SELECT package_id FROM line_claims WHERE line_id=?", (line_id,)).fetchone()
                if owner and owner["package_id"] != pid:
                    raise ValueError("Linia a fost facturată între timp în alt pachet.")
                db.execute("INSERT OR IGNORE INTO line_claims VALUES (?,?)", (line_id, pid))
        self.store.transition(pid, "issued", invoice)
        return invoice

    def confirm_not_issued(self, pid):
        rec = self.store.record(pid)
        if not rec or rec["state"] != "uncertain":
            raise ValueError("Operațiunea se aplică numai emiterilor incerte.")
        self.store.transition(pid, "rejected", error="Operatorul a verificat în FGO că factura NU există; reemiterea este permisă cu același IdExtern.")
