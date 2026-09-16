from copy import deepcopy
from dataclasses import asdict
from datetime import date
from pathlib import Path
import json
import unittest

from integrator.api import ApiError, InvoiceMissing, FgoAPI
from integrator.config import Settings
from integrator.demo import demo_orders, demo_mapping, DemoTrendyol, DemoFgo
from integrator.domain import build_draft
from integrator.service import Service
from integrator.store import Store, encoded
from support import workspace_temp


def split_order():
    original = demo_orders()[0]
    original.update(shipmentPackageStatus="UnPacked", packageGrossAmount="363", packageTotalPrice="363")
    original["lines"][0].update(quantity=3, discountDetails=[{"lineItemId": str(i), "lineItemPrice": "121"} for i in range(101,104)])
    children = []
    for i in range(3):
        child = deepcopy(original)
        child.update(shipmentPackageId=991001+i, shipmentPackageStatus="Delivered", packageGrossAmount="121", packageTotalPrice="121", originPackageIds=[990001], createdBy="split")
        child["lines"][0].update(quantity=1, discountDetails=[{"lineItemId": str(101+i), "lineItemPrice":"121"}])
        children.append(child)
    return original, children


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(workspace_temp())
        self.store = Store(folder / "history.sqlite3")
        self.store.put_orders(demo_orders())
        self.service = Service(Settings(), self.store, demo_mapping())

    def test_three_split_packages_same_order_client_line_and_price_emit_separately(self):
        original, children = split_order()
        self.store.put_orders([original, *children])
        drafts, errors = self.service.prepare([str(c["shipmentPackageId"]) for c in children])
        self.assertEqual(errors, [])
        self.assertEqual(len(drafts), 3)
        self.assertEqual(len({d.payload["IdExtern"] for d in drafts}), 3)
        for d in drafts:
            self.service.issue(d)
        self.assertEqual(len(self.store.records()), 3)
        with self.assertRaises(ValueError):
            self.service.issue(drafts[0])

    def test_same_physical_item_in_two_packages_is_blocked(self):
        original, children = split_order()
        children[1]["lines"][0]["discountDetails"] = deepcopy(children[0]["lines"][0]["discountDetails"])
        self.store.put_orders([original,*children])
        _, errors = self.service.prepare(["991001","991002"])
        self.assertEqual(len(errors), 1)
        self.service.issue(self.service.draft(children[0]))
        with self.assertRaisesRegex(ValueError,"deja rezervat"):
            self.service.draft(children[1])

    def test_legacy_claims_migrate_without_losing_invoice(self):
        original, children = split_order()
        self.store.put_orders([original,*children])
        draft = build_draft(children[0],demo_mapping(),Settings())
        draft.source_lines = [str(children[0]["lines"][0]["lineId"])]
        self.store.reserve(draft)
        self.store.transition(draft.package_id,"issued",{"series":"X","number":"1","url":"https://example.test/invoice.pdf"})
        reopened = Store(self.store.path)
        self.assertEqual(reopened.record(draft.package_id)["draft"]["source_lines"], ["item:800001:101"])
        self.assertEqual(reopened.record(draft.package_id)["invoice"]["number"], "1")
        Service(Settings(),reopened,demo_mapping()).draft(children[1])

    def issue_first(self):
        self.service.issue(self.service.draft(self.store.order("990001")))
        return self.store.record("990001")["invoice"]

    def test_deleted_trendyol_link_is_uploaded_again_with_same_fgo_invoice(self):
        invoice = self.issue_first()
        self.service.upload("990001")
        raw=self.store.order("990001")
        raw.pop("invoiceLink")
        raw["invoiceStatus"]="NotInvoiced"
        self.store.put_orders([raw])
        self.assertEqual(self.service.upload("990001"),"Încărcată în Trendyol")
        self.assertEqual(self.store.record("990001")["invoice"],invoice)

    def test_both_deleted_are_archived_and_can_be_reissued(self):
        invoice = self.issue_first()
        class MissingFgo(DemoFgo):
            def print_invoice(self,*args):
                raise InvoiceMissing("Factura nu exista")
        self.service._fgo=MissingFgo()
        self.service.refresh_invoice("990001",self.store.order("990001"))
        rec=self.store.record("990001")
        self.assertEqual(rec["state"],"missing")
        self.assertIsNone(rec["invoice"])
        with self.store.connect() as db:
            self.assertEqual(json.loads(db.execute("select invoice from invoice_archive").fetchone()[0]),invoice)
        self.service._fgo=DemoFgo()
        self.service.issue(self.service.draft(self.store.order("990001")))
        self.assertEqual(self.store.record("990001")["state"],"issued")

    def test_transport_or_permission_error_never_unlocks_invoice(self):
        invoice=self.issue_first()
        class FailingFgo:
            def print_invoice(self,*args):
                raise ApiError("HTTP 403")
        self.service._fgo=FailingFgo()
        with self.assertRaises(ApiError):
            self.service.refresh_invoice("990001",self.store.order("990001"))
        self.assertEqual(self.store.record("990001")["invoice"],invoice)
        with self.assertRaises(ValueError): self.service.draft(self.store.order("990001"))

    def test_fgo_deleted_but_trendyol_present_does_not_unlock(self):
        self.issue_first()
        class MissingFgo(DemoFgo):
            def print_invoice(self,*args): raise InvoiceMissing("Factura nu exista")
        self.service._fgo=MissingFgo()
        raw=self.store.order("990001")
        raw["invoiceStatus"]="Invoiced"
        self.service.refresh_invoice("990001",raw)
        self.assertEqual(self.store.record("990001")["state"],"remote_conflict")
        with self.assertRaises(ValueError): self.service.draft(self.store.order("990001"))

    def test_sync_checks_existing_invoices(self):
        self.issue_first()
        self.store.transition("990001","uploaded")
        _,errors=self.service.sync(date.today(),date.today())
        self.assertEqual(errors,[])
        self.assertEqual(self.store.record("990001")["state"],"issued")

    def test_only_explicit_fgo_absence_is_classified_missing(self):
        class ResponseTransport:
            def __init__(self,message): self.message=message
            def redact(self,message): return message
            def call(self,*args,**kwargs): return 200,{"Success":False,"Message":self.message}
        for message,exception in [("Factura nu exista",InvoiceMissing),("Utilizatorul nu exista",ApiError),("Acces refuzat",ApiError)]:
            api=FgoAPI(Settings(),ResponseTransport(message))
            with self.assertRaises(exception) as cm: api.print_invoice("X","1")
            if exception is ApiError: self.assertNotIsInstance(cm.exception,InvoiceMissing)
