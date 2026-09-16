from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal
import unittest

from integrator.config import Settings
from integrator.domain import build_draft, package_id, signature, LIMIT_RON
from integrator.emag import envelope, invoice_order, EmagAPI, EmagService
from integrator.mapping import Product
from integrator.reports import sales_report
from integrator.store import Store
from integrator.demo import demo_orders, demo_mapping, DemoFgo
from integrator.api import ApiError
from support import workspace_temp


def emag_order(market="RO"):
    return envelope({"id":12345,"type":3,"status":4,"is_complete":1,"is_storno":0,"date":date.today().isoformat()+" 12:00:00","payment_mode_id":1,"customer":{"legal_entity":0,"billing_name":"Client exemplu","billing_country":market,"shipping_country":market,"billing_city":"Oras","billing_suburb":"Ilfov","billing_street":"Strada Exemplu 1"},"products":[{"id":991,"product_id":123,"status":1,"name":"Produs exemplu","quantity":3,"sale_price":"10.00","currency":{"RO":"RON","BG":"EUR","HU":"HUF"}[market]}],"shipping_tax":"0","vouchers":[]},market)


class EmagTests(unittest.TestCase):
    def setUp(self):
        self.raw=emag_order()
        self.mapping={"123":Product("123","Produs FGO",fgo_code="SKU-1",fgo_verified=True)}
        self.settings=Settings()

    def test_net_price_converted_once_and_id_scoped_to_market(self):
        for market in ("RO","BG","HU"):
            d=build_draft(emag_order(market),self.mapping,self.settings)
            self.assertEqual(d.total,Decimal("36.30"))
            self.assertEqual(d.payload["Continut"][0]["NrProduse"],3)
            self.assertEqual(d.package_id,f"emag:{market}:12345")
        self.assertNotEqual(package_id(emag_order("RO")),package_id(emag_order("BG")))

    def test_vouchers_seller_discount_and_emag_compensation(self):
        p=self.raw["_emag"]["products"][0]
        p["product_voucher_split"]=[{"voucher_id":1,"value":"-2","vat_value":"-0.42","offered_by":"seller"},{"voucher_id":2,"value":"-1","vat_value":"-0.21","offered_by":"eMAG"}]
        d=build_draft(self.raw,self.mapping,self.settings)
        self.assertEqual(d.total,Decimal("33.88"))
        self.assertEqual(d.customer_paid,Decimal("32.67"))
        self.assertEqual(d.subsidy,Decimal("1.21"))
        p["product_voucher_split"][0].pop("offered_by")
        with self.assertRaisesRegex(ValueError,"finanțatorul"):build_draft(self.raw,self.mapping,self.settings)

    def test_shipping_requires_explicit_tax_semantics(self):
        self.raw["_emag"]["shipping_tax"]="10"
        with self.assertRaisesRegex(ValueError,"shipping_tax"):build_draft(self.raw,self.mapping,self.settings)
        self.mapping["__transport__"]=Product("__transport__","Transport",fgo_code="TR",fgo_verified=True)
        d=build_draft(self.raw,self.mapping,replace(self.settings,emag_shipping_tax_mode="cu_tva"))
        self.assertEqual(d.total,Decimal("46.30"))
        self.assertEqual(len(d.source_lines),2)
        with workspace_temp() as folder:
            store=Store(folder/"history.sqlite3");store.put_orders([self.raw]);store.reserve(d)
            self.assertEqual(Store(store.path).record(d.package_id)["draft"]["source_lines"],d.source_lines)

    def test_cancellation_storno_b2b_and_unpaid_card_block(self):
        for key,value in (("type",2),("is_storno",1),("status",0),("is_complete",0)):
            order=deepcopy(self.raw["_emag"]);order[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):build_draft(envelope(order,"RO"),self.mapping,self.settings)
        order=deepcopy(self.raw["_emag"]);order["customer"]["legal_entity"]=1
        with self.assertRaises(ValueError):build_draft(envelope(order,"RO"),self.mapping,self.settings)
        order=deepcopy(self.raw["_emag"]);order.update(payment_mode_id=3,payment_status=0)
        with self.assertRaisesRegex(ValueError,"plata"):build_draft(envelope(order,"RO"),self.mapping,self.settings)

    def test_attachment_and_changed_product_block_duplicate_upload(self):
        with self.assertRaisesRegex(ValueError,"deja o factură"):
            build_draft(envelope(self.raw["_emag"],"RO",[{"type":1,"url":"https://example.test/a.pdf"}]),self.mapping,self.settings)
        before=signature(self.raw)
        self.raw["_emag"]["products"][0]["status"]=0
        self.assertNotEqual(before,signature(self.raw))

    def test_api_read_filters_and_attachment_write_contract(self):
        class Transport:
            def __init__(self):self.calls=[]
            def call(self,*args,**kwargs):
                self.calls.append((args,kwargs));return 200,{"isError":False,"results":[]}
            def redact(self,x):return str(x)
        t=Transport();api=EmagAPI(replace(self.settings,mode="production",emag_user_ro="example",emag_password_ro="example-password"),t)
        self.assertEqual(api.fetch("RO",date.today(),date.today()),[])
        payload=t.calls[0][0][3]
        self.assertEqual(payload["type"],3);self.assertNotIn("data",payload)
        self.assertTrue(t.calls[0][1]["read_only"])
        api.upload("emag:RO:12345","RO","https://example.test/invoice.pdf")
        args,kwargs=t.calls[-1]
        self.assertEqual(args[3]["data"][0]["order_id"],12345)
        self.assertFalse(kwargs["read_only"])
        self.assertTrue(args[1].endswith("/order/attachments/save"))

    def test_shared_budget_counts_both_marketplaces(self):
        class Other:
            def used_ron(self):return LIMIT_RON
        s=replace(self.settings,mode="production",emag_series_bg="BG",fx_date=date.today().isoformat(),eur_ron="5",previous_sales_ron="0",opening_sales_ron="0")
        with workspace_temp() as folder:
            service=EmagService(s,Store(folder/"history.sqlite3"),self.mapping,other_stores=(Other(),))
            with self.assertRaisesRegex(ValueError,"Plafonul"):service.draft(emag_order("BG"))

    def test_cent_allocations_never_go_negative(self):
        self.raw["_emag"]["products"][0].update(quantity=7,sale_price="0.004")
        raw,_=invoice_order(self.raw,self.mapping,self.settings)
        self.assertEqual(sum(x["lineItemPrice"] for x in raw["lines"][0]["discountDetails"]),Decimal("0.03"))
        self.assertTrue(all(x["lineItemPrice"]>=0 for x in raw["lines"][0]["discountDetails"]))


class ReportTests(unittest.TestCase):
    def test_split_counts_one_order_three_units_and_currencies_separate(self):
        rows=[]
        for i in range(3):
            raw=deepcopy(demo_orders()[0]);raw.update(shipmentPackageId=100+i,shipmentPackageStatus="Delivered",packageGrossAmount="121",packageTotalPrice="121")
            raw["lines"][0].update(quantity=1,discountDetails=[{"lineItemId":i+1,"lineItemPrice":"121"}])
            rows.append(raw)
        countries,top,errors=sales_report(rows,demo_mapping(),"2000-01-01","2100-01-01")
        self.assertFalse(errors);self.assertEqual(countries[0]["orders"],1)
        self.assertEqual(countries[0]["packages"],3);self.assertEqual(countries[0]["total"],Decimal("363"))
        self.assertEqual(top[0]["quantity"],3)
        rows[-1]["lines"][0]["discountDetails"][0]["lineItemId"]=1
        countries,_,errors=sales_report(rows,demo_mapping(),"2000-01-01","2100-01-01")
        self.assertEqual(countries[0]["packages"],2);self.assertEqual(len(errors),1)

    def test_emag_shipping_excluded_from_product_ranking(self):
        raw=emag_order();raw["_emag"]["shipping_tax"]="100"
        mapping={"123":Product("123","Produs",fgo_verified=True),"__transport__":Product("__transport__","Transport",fgo_verified=True)}
        raw,mapping=invoice_order(raw,mapping,Settings(emag_shipping_tax_mode="cu_tva"))
        countries,top,errors=sales_report([raw],mapping,"2000-01-01","2100-01-01",include_shipped=True)
        self.assertFalse(errors);self.assertEqual(countries[0]["total"],Decimal("136.30"))
        self.assertEqual(countries[0]["quantity"],3);self.assertEqual(len(top),1)

    def test_status_date_and_currency_filters(self):
        rows=demo_orders()
        a,_,_=sales_report(rows,demo_mapping(),"2000-01-01","2100-01-01",include_shipped=True)
        self.assertTrue(a);self.assertEqual(len({(v['country'],v['currency']) for v in a}),len(a))
        a,top,errors=sales_report(rows,demo_mapping(),"2000-01-01","2000-01-02",include_shipped=True)
        self.assertEqual((a,top,errors),([],[],[]))


if __name__=="__main__":unittest.main()
