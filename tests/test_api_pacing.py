from io import BytesIO
from unittest import TestCase
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from integrator.api import ApiError, RequestPacer, Transport
from integrator.config import Settings
from integrator.demo import demo_mapping, demo_orders
from integrator.service import Service
from integrator.store import Store
from support import workspace_temp


class Clock:
    def __init__(self):
        self.now = 100.0
        self.waits = []

    def monotonic(self):
        return self.now

    def sleep(self, delay):
        self.waits.append(delay)
        self.now += delay


class Response(BytesIO):
    status = 200

    def __init__(self):
        super().__init__(b'{"Success":true}')


class ApiPacingTests(TestCase):
    def setUp(self):
        self.clock = Clock()
        self.enterContext(patch('integrator.api.REQUEST_PACER', RequestPacer()))
        self.enterContext(patch('integrator.api.time.monotonic', self.clock.monotonic))
        self.enterContext(patch('integrator.api.time.sleep', self.clock.sleep))

    def transport(self, calls):
        transport = Transport()
        def opened(req, **kwargs):
            calls.append((req.full_url, self.clock.now))
            return Response()
        transport.opener = Mock()
        transport.opener.open.side_effect = opened
        return transport

    def test_new_clients_in_same_batch_share_fgo_limit(self):
        calls = []
        for _ in range(3):
            # The combined queue used to construct a fresh client per invoice.
            self.transport(calls).call('POST', 'https://api.fgo.ro/v1/factura/emitere', body={})
        self.assertEqual(len(calls), 3)
        for before, after in zip(calls, calls[1:]):
            self.assertGreaterEqual(after[1] - before[1], 1.099)

    def test_article_and_invoice_limits_survive_client_change(self):
        calls = []
        self.transport(calls).call('POST', 'https://api.fgo.ro/v1/articol/get', interval=5.1, read_only=True)
        self.transport(calls).call('POST', 'https://api.fgo.ro/v1/factura/emitere')
        self.assertGreaterEqual(calls[1][1] - calls[0][1], 5.099)

    def test_marketplace_and_fgo_use_separate_limits(self):
        calls = []
        self.transport(calls).call('GET', 'https://apigw.trendyol.com/orders', interval=2.1, read_only=True)
        self.transport(calls).call('POST', 'https://api.fgo.ro/v1/factura/emitere')
        self.assertEqual(calls[0][1], calls[1][1])
        self.assertEqual(self.clock.waits, [])

    def test_http_conflict_is_unknown_outcome_and_never_retried(self):
        transport = Transport()
        transport.opener = Mock()
        transport.opener.open.side_effect = HTTPError('https://api.fgo.ro/v1/factura/emitere', 409, 'Conflict', {}, None)
        with self.assertRaises(ApiError) as error:
            transport.call('POST', 'https://api.fgo.ro/v1/factura/emitere', body={})
        self.assertEqual(error.exception.status, 409)
        self.assertTrue(error.exception.uncertain)
        self.assertIn('api.fgo.ro', str(error.exception))
        self.assertNotIn('factura sau linkul poate exista', str(error.exception))
        transport.opener.open.assert_called_once()

    def test_failed_request_is_also_followed_by_provider_pause(self):
        transport = Transport()
        transport.opener = Mock()
        transport.opener.open.side_effect = HTTPError('https://api.fgo.ro/v1/factura/emitere', 409, 'Conflict', {}, None)
        with self.assertRaises(ApiError):
            transport.call('POST', 'https://api.fgo.ro/v1/factura/emitere')
        calls = []
        self.transport(calls).call('POST', 'https://api.fgo.ro/v1/factura/emitere')
        self.assertGreaterEqual(calls[0][1], 101.099)

    def test_legacy_conflict_is_not_offered_for_automatic_reissue(self):
        with workspace_temp() as folder:
            store = Store(folder / 'history.sqlite3')
            raw = demo_orders()[0]
            store.put_orders([raw])
            service = Service(Settings(), store, demo_mapping())
            draft = service.draft(raw)
            store.reserve(draft)
            store.transition(draft.package_id, 'rejected', error='HTTP 409: Conflict')
            store.recover()
            store.recover()
            self.assertEqual(store.record(draft.package_id)['state'], 'uncertain')
            with store.connect() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM line_claims WHERE package_id=?', (draft.package_id,)).fetchone()[0], len(draft.source_lines))
            with self.assertRaises(ValueError):
                service.draft(raw)
            service.confirm_not_issued(draft.package_id)
            store.recover()
            self.assertEqual(service.draft(raw).payload['IdExtern'], draft.payload['IdExtern'])

    def test_service_blocks_conflict_even_with_legacy_transport_flag(self):
        with workspace_temp() as folder:
            store = Store(folder / 'history.sqlite3')
            raw = demo_orders()[0]
            store.put_orders([raw])
            fgo = Mock()
            fgo.issue.side_effect = ApiError('HTTP 409', 409, False)
            service = Service(Settings(), store, demo_mapping(), fgo=fgo)
            draft = service.draft(raw)
            with self.assertRaises(ApiError):
                service.issue(draft)
            self.assertEqual(store.record(draft.package_id)['state'], 'uncertain')
            with self.assertRaises(ValueError):
                service.draft(raw)
            fgo.issue.assert_called_once()
