from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from integrator.api import ApiError
from integrator.config import Settings
from integrator.demo import demo_orders, demo_mapping
from integrator.service import Service, issue_batch
from integrator.store import Store
from support import workspace_temp


class BatchTests(TestCase):
    def test_conflict_does_not_skip_other_platform_or_retry_failed_package(self):
        first,second=Mock(),Mock()
        first.issue.side_effect=ApiError("HTTP 409",409,True)
        a,b=SimpleNamespace(package_id="1"),SimpleNamespace(package_id="emag:RO:2")
        count,errors=issue_batch([a,b,a],{"1":first,"emag:RO:2":second})
        self.assertEqual(count,1)
        self.assertEqual(errors,["1: HTTP 409"])
        first.issue.assert_called_once_with(a)
        second.issue.assert_called_once_with(b)

    def test_uncertain_invoice_stays_reserved_while_independent_package_completes(self):
        with workspace_temp() as folder:
            store=Store(folder/"history.sqlite3")
            rows=demo_orders()[:2]
            store.put_orders(rows)
            api=Mock()
            api.issue.side_effect=[ApiError("Conflict",409,True),{"series":"DEMO","number":"2","url":"https://example.test/invoice.pdf"}]
            service=Service(Settings(),store,demo_mapping(),fgo=api)
            drafts,errors=service.prepare(["990001","990002"])
            self.assertFalse(errors)
            count,errors=issue_batch(drafts,{d.package_id:service for d in drafts})
            self.assertEqual(count,1)
            self.assertEqual(len(errors),1)
            self.assertEqual(store.record("990001")["state"],"uncertain")
            self.assertEqual(store.record("990002")["state"],"issued")
            with self.assertRaises(ValueError):service.draft(rows[0])
            self.assertEqual(api.issue.call_count,2)
