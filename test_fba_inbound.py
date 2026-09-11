import datetime
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from sweetshelves import fba_readiness as ss_fba_readiness
from sweetshelves import fba_shipments as ss_fba_shipments

from sweetshelves.fba_inventory import (
    _fba_session_item_payload,
)
from sweetshelves.fba_readiness import (
    _fba_catalog_measurement_reference_from_payload,
    _fba_listing_measurement_status,
    _fba_existing_prep_conflict,
    _fba_inbound_activation_message,
    _fba_inbound_unavailable_skus,
    _fba_item_prep_details,
    _fba_missing_prep_mskus,
    _fba_normalize_prep_detail,
    _fba_package_measurement_issues,
    _fba_patch_listing_package_measurements,
)
from sweetshelves.fba_shipments import (
    _fba_amazon_box_label_document,
    _fba_amazon_exception_detail,
    _fba_amazon_payload,
    _fba_amazon_sync_snapshot,
    _fba_amazon_transport_summary,
)
from fba_inbound import (
    FbaInboundValidationError,
    detect_amazon_cancellation,
    transport_option_block_reason,
    US_MARKETPLACE_ID,
    apply_owner_corrections_from_amazon_error,
    build_create_plan_request,
    build_item_label_job,
    build_set_packing_request,
    build_transportation_request,
    normalize_box_drafts,
    reduce_missing_plan_quantity,
    remap_recovery_boxes,
    validate_box_specifications,
)


ADDRESS = {
    "name": "Warehouse",
    "addressLine1": "1 Main St",
    "city": "Miami",
    "stateOrProvinceCode": "FL",
    "postalCode": "33101",
    "countryCode": "US",
    "phoneNumber": "3055550100",
    "email": "shipping@example.com",
}


def ready_plan_session(count):
    return {'items': [{
        'barcode': str(100000000000 + index), 'seller_sku': f'SKU-{index:03}',
        'quantity': 2, 'fba_enablement_status': 'ready', 'prep_type': 'none',
    } for index in range(count)]}


class PrepBatchApi:
    def __init__(self, failure=''):
        self.failure = failure
        self.read_batches, self.write_batches, self.polled, self.created, self.events = [], [], [], [], []

    @staticmethod
    def response(**payload):
        return SimpleNamespace(payload=payload, errors=None)

    def list_prep_details(self, **kwargs):
        batch = kwargs['mskus']
        if len(batch) > 100:
            raise FbaInboundValidationError('Amazon allows at most 100 prep SKUs')
        self.read_batches.append(list(batch))
        if self.failure == 'read' and len(self.read_batches) == 2:
            raise FbaInboundValidationError('second batch failed')
        return self.response(mskuPrepDetails=[])

    def set_prep_details(self, **kwargs):
        batch = [row['msku'] for row in kwargs['mskuPrepDetails']]
        if len(batch) > 100:
            raise FbaInboundValidationError('Amazon allows at most 100 prep SKUs')
        self.write_batches.append(batch)
        self.events.append('write')
        if self.failure == 'write' and len(self.write_batches) == 2:
            raise FbaInboundValidationError('second batch failed')
        return self.response(operationId=str(len(self.write_batches)))

    def get_inbound_operation_status(self, operation_id):
        self.polled.append(int(operation_id))
        self.events.append('poll')
        if self.failure == 'operation' and operation_id == '2':
            return self.response(operationStatus='FAILED', operationProblems=[{'message': 'second batch failed'}])
        return self.response(operationStatus='SUCCESS')

    def create_inbound_plan(self, **kwargs):
        self.created.append(kwargs)
        self.events.append('create')
        return self.response(inboundPlanId='plan-1', operationId='plan-op')


class FbaInboundHelpersTest(unittest.TestCase):
    def test_plan_prep_batches_preserve_all_skus_and_quantities(self):
        for count in (100, 101, 130, 201):
            with self.subTest(count=count):
                api = PrepBatchApi()
                session = ready_plan_session(count)
                with patch.object(ss_fba_readiness, '_fba_session_listing_health', return_value={'findings': []}), patch.object(ss_fba_shipments, '_fba_amazon_source_from_settings', return_value=ADDRESS):
                    state, _ = ss_fba_shipments._fba_prepare_and_create_plan(
                        api, US_MARKETPLACE_ID, session, {})
                expected = [row['seller_sku'] for row in session['items']]
                self.assertEqual([sku for batch in api.read_batches for sku in batch], expected)
                self.assertEqual([sku for batch in api.write_batches for sku in batch], expected)
                self.assertTrue(all(1 <= len(batch) <= 100 for batch in api.read_batches + api.write_batches))
                self.assertEqual(len(api.created), 1)
                self.assertEqual([row['msku'] for row in api.created[0]['items']], expected)
                self.assertEqual(sum(row['quantity'] for row in api.created[0]['items']), count * 2)
                self.assertEqual(len(state['create_request_summary']['prep_corrections']), count)
                self.assertEqual(api.events[-1], 'create')
                self.assertEqual(api.polled, list(range(1, len(api.write_batches) + 1)))

    def test_plan_is_not_created_if_later_prep_batch_fails(self):
        for failure in ('read', 'write', 'operation'):
            with self.subTest(failure=failure):
                api = PrepBatchApi(failure=failure)
                state = {}
                with patch.object(ss_fba_readiness, '_fba_session_listing_health', return_value={'findings': []}), patch.object(ss_fba_shipments, '_fba_amazon_source_from_settings', return_value=ADDRESS):
                    with self.assertRaisesRegex(FbaInboundValidationError, 'second batch failed'):
                        ss_fba_shipments._fba_prepare_and_create_plan(
                            api, US_MARKETPLACE_ID, ready_plan_session(130), state)
                self.assertEqual(api.created, [])
                self.assertNotIn('inbound_plan_id', state)

    def test_plan_merges_existing_prep_from_every_read_batch(self):
        class ExistingPrepApi(PrepBatchApi):
            def list_prep_details(self, **kwargs):
                super().list_prep_details(**kwargs)
                return self.response(mskuPrepDetails=[{
                    'msku': sku, 'prepCategory': 'FC_PROVIDED',
                    'prepTypes': ['ITEM_BUBBLEWRAP'],
                } for sku in kwargs['mskus']])

        api = ExistingPrepApi()
        with patch.object(ss_fba_readiness, '_fba_session_listing_health', return_value={'findings': []}), patch.object(ss_fba_shipments, '_fba_amazon_source_from_settings', return_value=ADDRESS):
            ss_fba_shipments._fba_prepare_and_create_plan(
                api, US_MARKETPLACE_ID, ready_plan_session(130), {})
        self.assertEqual([len(batch) for batch in api.read_batches], [100, 30])
        self.assertEqual(api.write_batches, [])
        self.assertEqual(len(api.created[0]['items']), 130)

    def test_prep_conflict_in_second_batch_preserves_prior_success(self):
        class ConflictApi(PrepBatchApi):
            def get_inbound_operation_status(self, operation_id):
                response = super().get_inbound_operation_status(operation_id)
                if operation_id == '2':
                    return self.response(operationStatus='FAILED', operationProblems=[{
                        'message': 'prep category and types cannot be updated for msku '
                                   'SKU-105 with existing prep category of FC_PROVIDED',
                    }])
                return response

        api = ConflictApi()
        with patch.object(ss_fba_readiness, '_fba_session_listing_health', return_value={'findings': []}), patch.object(ss_fba_shipments, '_fba_amazon_source_from_settings', return_value=ADDRESS):
            state, _ = ss_fba_shipments._fba_prepare_and_create_plan(
                api, US_MARKETPLACE_ID, ready_plan_session(130), {})
        self.assertEqual([len(batch) for batch in api.write_batches], [100, 30, 29])
        self.assertNotIn('SKU-105', api.write_batches[-1])
        self.assertTrue(set(api.write_batches[0]).isdisjoint(api.write_batches[-1]))
        self.assertEqual(len(api.created[0]['items']), 130)
        corrections = state['create_request_summary']['prep_corrections']
        self.assertEqual(len({row['msku'] for row in corrections}), 130)
        self.assertIn({'msku': 'SKU-105', 'prepCategory': 'FC_PROVIDED', 'preserved': True}, corrections)

    def test_amazon_warning_does_not_reject_small_parcel_transport_request(self):
        response = SimpleNamespace(
            payload={'operationId': 'operation-1'},
            errors=[{
                'severity': 'WARNING',
                'message': 'WARNING: Since pallet and freight info has not been provided, '
                           'we may not be able to produce PCP-LTL options.',
            }],
        )

        self.assertEqual(_fba_amazon_payload(response), {'operationId': 'operation-1'})

    def test_amazon_error_still_rejects_request(self):
        response = SimpleNamespace(
            payload={},
            errors=[{'severity': 'ERROR', 'message': 'ERROR: Invalid shipment'}],
        )

        with self.assertRaisesRegex(FbaInboundValidationError, 'Invalid shipment'):
            _fba_amazon_payload(response)

    def test_amazon_exception_reports_error_instead_of_preceding_warning(self):
        from sp_api.base.exceptions import SellingApiBadRequestException

        exc = SellingApiBadRequestException([{
            'code': 'BadRequest',
            'message': 'WARNING: Pallet info was not provided.',
        }, {
            'code': 'BadRequest',
            'message': "ERROR: DateTime value cannot be in the past.",
        }])

        detail = _fba_amazon_exception_detail(exc)
        self.assertIn('DateTime value cannot be in the past', detail)
        self.assertNotIn('Pallet info', detail)

    def test_final_labels_keep_original_box_numbers_despite_amazon_order(self):
        local = [dict(local_id='BOX-01', packing_group_id='G1', contents=[dict(msku='A', quantity=2)],
                      length_in=10, width_in=8, height_in=6, weight_lb=4),
                 dict(local_id='BOX-08', packing_group_id='G2', contents=[dict(msku='B', quantity=3)],
                      length_in=12, width_in=9, height_in=7, weight_lb=5)]
        def remote(box, box_id):
            return dict(boxId=box_id, items=box['contents'], quantity=1,
                        dimensions=dict(unitOfMeasurement='IN', length=box['length_in'], width=box['width_in'], height=box['height_in']),
                        weight=dict(unit='LB', value=box['weight_lb']))
        amazon = [remote(local[1], 'AMAZON-FIRST'), remote(local[0], 'AMAZON-SECOND')]
        mapped = ss_fba_shipments._fba_match_label_boxes(amazon, local)
        self.assertEqual([r['local_box_id'] for r in mapped], ['BOX-08', 'BOX-01'])
        self.assertEqual(mapped[0]['packing_group_id'], 'G2')
        class Api:
            def list_shipment_boxes(self, *args, **kwargs):
                return SimpleNamespace(payload={'boxes': amazon})
        class Labels:
            def get_labels(self, *args, **kwargs):
                self.request = kwargs
                return SimpleNamespace(payload={'DownloadURL': 'https://labels.example/one.pdf'})
        labels = Labels()
        _, ids = _fba_amazon_box_label_document(Api(), labels, 'P', 'S', 'FBA', local_boxes=local, local_box_id='BOX-08')
        self.assertEqual(ids, ['AMAZON-FIRST'])
        self.assertEqual(labels.request['PackageLabelsToPrint'], 'AMAZON-FIRST')
        with self.assertRaises(FbaInboundValidationError):
            _fba_amazon_box_label_document(Api(), labels, 'P', 'S', 'FBA', local_boxes=local, local_box_id='BOX-99')
        duplicate = dict(local[1], local_id='BOX-09')
        mapped = ss_fba_shipments._fba_match_label_boxes(amazon, local + [duplicate])
        self.assertEqual(mapped[0]['local_box_id'], '')
        self.assertTrue(mapped[0]['match_error'])
        amazon[0]['weight']['value'] = 99
        self.assertTrue(ss_fba_shipments._fba_match_label_boxes(amazon, local)[0]['match_error'])

    def test_box_label_document_uses_amazon_shipment_box_ids(self):
        class FakeInboundApi:
            def list_shipment_boxes(self, plan_id, shipment_id, **kwargs):
                self.call = (plan_id, shipment_id, kwargs)
                return SimpleNamespace(payload={
                    'boxes': [{'boxId': 'FBA-BOX-1'}, {'boxId': 'FBA-BOX-2'}],
                }, errors=None)

        class FakeLegacyApi:
            def get_labels(self, confirmation_id, **kwargs):
                self.call = (confirmation_id, kwargs)
                return SimpleNamespace(payload={'DownloadURL': 'https://labels.example/boxes.pdf'}, errors=None)

        api, legacy_api = FakeInboundApi(), FakeLegacyApi()

        url, box_ids = _fba_amazon_box_label_document(
            api, legacy_api, 'plan-1', 'shipment-1', 'FBA-CONFIRM-1'
        )

        self.assertEqual(url, 'https://labels.example/boxes.pdf')
        self.assertEqual(box_ids, ['FBA-BOX-1', 'FBA-BOX-2'])
        self.assertEqual(api.call, ('plan-1', 'shipment-1', {'pageSize': 1000}))
        self.assertEqual(legacy_api.call, ('FBA-CONFIRM-1', {
            'PageType': 'PackageLabel_Thermal',
            'LabelType': 'UNIQUE',
            'PackageLabelsToPrint': 'FBA-BOX-1,FBA-BOX-2',
        }))

    def test_transportation_sync_uses_amazon_maximum_page_size(self):
        class FakeInboundApi:
            def __init__(self):
                self.transport_page_sizes = []
                self.transport_shipment_ids = []

            @staticmethod
            def response(**payload):
                return SimpleNamespace(payload=payload, errors=None)

            def get_inbound_plan(self, _plan_id):
                return self.response(inboundPlanId='plan-1', status='ACTIVE')

            def list_inbound_plan_items(self, _plan_id, **_kwargs):
                return self.response(items=[])

            def list_packing_options(self, _plan_id, **_kwargs):
                return self.response(packingOptions=[{
                    'packingOptionId': 'packing-1', 'status': 'ACCEPTED', 'packingGroups': [],
                }])

            def list_placement_options(self, _plan_id, **_kwargs):
                return self.response(placementOptions=[{
                    'placementOptionId': 'placement-1', 'status': 'ACCEPTED',
                    'shipmentIds': ['shipment-1', 'shipment-2'],
                }])

            def get_shipment(self, _plan_id, shipment_id):
                return self.response(
                    shipmentId=shipment_id,
                    destination={'warehouseId': 'FC-' + shipment_id[-1]},
                )

            def list_transportation_options(self, _plan_id, **kwargs):
                self.transport_page_sizes.append(kwargs.get('pageSize'))
                self.transport_shipment_ids.append(kwargs.get('shipmentId'))
                return self.response(transportationOptions=[{
                    'transportationOptionId': 'option-' + kwargs['shipmentId'][-1],
                    'shipmentId': kwargs['shipmentId'],
                    'shippingSolution': 'AMAZON_PARTNERED_CARRIER',
                    'shippingMode': 'GROUND_SMALL_PARCEL',
                    'quote': {'cost': {'amount': 12.34, 'code': 'USD'}},
                    'preconditions': [],
                }])

        api = FakeInboundApi()
        state = {
            'inbound_plan_id': 'plan-1',
            'operation': {'status': 'SUCCESS'},
            'boxes_submitted': True,
        }

        refreshed = _fba_amazon_sync_snapshot(api, state)

        self.assertEqual(api.transport_page_sizes, [20, 20])
        self.assertEqual(api.transport_shipment_ids, ['shipment-1', 'shipment-2'])
        self.assertEqual(len(refreshed['transportation_options']), 2)
        self.assertTrue(all(row['can_purchase'] for row in refreshed['transportation_options']))
        self.assertTrue(refreshed['placement_confirmed'])

    def test_transport_summary_marks_only_quoted_partnered_carrier_as_purchasable(self):
        partnered = _fba_amazon_transport_summary({
            'transportationOptionId': 'option-1',
            'shipmentId': 'shipment-1',
            'shippingSolution': 'AMAZON_PARTNERED_CARRIER',
            'shippingMode': 'GROUND_SMALL_PARCEL',
            'quote': {'cost': {'amount': 15.52, 'code': 'USD'}},
            'preconditions': [],
        })
        self_booked = _fba_amazon_transport_summary({
            'transportationOptionId': 'option-2',
            'shipmentId': 'shipment-1',
            'shippingSolution': 'USE_YOUR_OWN_CARRIER',
            'quote': {},
            'preconditions': ['CONFIRMED_DELIVERY_WINDOW'],
        })

        self.assertTrue(partnered['can_purchase'])
        self.assertFalse(self_booked['can_purchase'])

    def test_transport_summary_never_marks_freight_as_purchasable(self):
        freight = _fba_amazon_transport_summary({
            'transportationOptionId': 'option-ltl',
            'shipmentId': 'shipment-1',
            'shippingSolution': 'AMAZON_PARTNERED_CARRIER',
            'shippingMode': 'FREIGHT_LTL',
            'quote': {'cost': {'amount': 95.00, 'code': 'USD'}},
            'preconditions': [],
        })

        self.assertFalse(freight['can_purchase'])

    def test_missing_or_unknown_prep_details_are_classified_before_plan_creation(self):
        prep_by_msku = {
            'already-set': {'msku': 'ALREADY-SET', 'prepCategory': 'NONE'},
            'explicitly-unknown': {
                'msku': 'EXPLICITLY-UNKNOWN', 'prepCategory': 'UNKNOWN'
            },
        }
        self.assertEqual(
            _fba_missing_prep_mskus(
                ['NO-RECORD', 'EXPLICITLY-UNKNOWN', 'ALREADY-SET'], prep_by_msku
            ),
            ['NO-RECORD', 'EXPLICITLY-UNKNOWN'],
        )

    def test_existing_amazon_prep_category_conflict_is_preserved(self):
        conflict = _fba_existing_prep_conflict(
            'Amazon rejected the request: ERROR: Prep category and types cannot be '
            'updated for msku VP-X14A-SPU7-FBA with existing prep category of FC_PROVIDED.'
        )
        self.assertEqual(conflict, {
            'msku': 'VP-X14A-SPU7-FBA',
            'prepCategory': 'FC_PROVIDED',
        })

    def test_amazon_prep_detail_normalizes_live_and_saved_shapes(self):
        live = _fba_normalize_prep_detail({
            'msku': 'SKU-1',
            'prepCategory': 'FC_PROVIDED',
            'prepTypes': ['ITEM_LABELING', 'ITEM_BUBBLEWRAP', 'ITEM_BUBBLEWRAP'],
            'prepOwnerConstraint': 'SELLER_ONLY',
            'labelOwnerConstraint': 'SELLER_ONLY',
        })
        saved = _fba_normalize_prep_detail({
            **live, 'checked': True, 'checked_at': '2026-09-01T12:00:00Z',
        })

        self.assertEqual(live['prep_category'], 'FC_PROVIDED')
        self.assertEqual(live['prep_types'], ['ITEM_LABELING', 'ITEM_BUBBLEWRAP'])
        self.assertEqual(saved['prep_types'], live['prep_types'])
        self.assertEqual(saved['prep_owner_constraint'], 'SELLER_ONLY')
        self.assertEqual(saved['checked_at'], '2026-09-01T12:00:00Z')

    def test_item_prep_lookup_requests_one_exact_msku(self):
        class FakeInboundApi:
            def list_prep_details(self, **kwargs):
                self.kwargs = kwargs
                return SimpleNamespace(payload={'mskuPrepDetails': [{
                    'msku': 'VP-X14A-SPU7-FBA',
                    'prepCategory': 'FC_PROVIDED',
                    'prepTypes': ['ITEM_LABELING', 'ITEM_BUBBLEWRAP'],
                    'prepOwnerConstraint': 'SELLER_ONLY',
                }]}, errors=None)

        api = FakeInboundApi()
        detail = _fba_item_prep_details(
            'VP-X14A-SPU7-FBA', api=api, marketplace_id='ATVPDKIKX0DER'
        )

        self.assertEqual(api.kwargs['mskus'], ['VP-X14A-SPU7-FBA'])
        self.assertEqual(detail['prep_types'], ['ITEM_LABELING', 'ITEM_BUBBLEWRAP'])
        self.assertTrue(detail['checked'])
        self.assertTrue(detail['checked_at'])

    def test_saved_session_keeps_amazon_prep_instructions(self):
        item = _fba_session_item_payload({
            'barcode': '037725523736',
            'seller_sku': 'VP-X14A-SPU7-FBA',
            'quantity': 1,
            'amazon_prep': {
                'msku': 'VP-X14A-SPU7-FBA',
                'prep_category': 'FC_PROVIDED',
                'prep_types': ['ITEM_LABELING', 'ITEM_BUBBLEWRAP'],
                'prep_owner_constraint': 'SELLER_ONLY',
                'checked': True,
                'checked_at': '2026-09-01T12:00:00Z',
            },
        })

        self.assertEqual(item['amazon_prep']['prep_category'], 'FC_PROVIDED')
        self.assertIn('ITEM_BUBBLEWRAP', item['amazon_prep']['prep_types'])
        self.assertEqual(item['amazon_prep']['prep_owner_constraint'], 'SELLER_ONLY')

    def test_package_measurement_errors_are_grouped_by_sku(self):
        rows = _fba_package_measurement_issues({'operation': {'problems': [{
            'code': 'FBA_INB_0004',
            'details': "There's an input error with the resource 'SKU-ONE'.",
            'message': 'Dimensions need to be provided in manufacturer packaging.',
        }, {
            'code': 'FBA_INB_0005',
            'details': "There's an input error with the resource 'SKU-ONE'.",
            'message': 'Weight needs to be provided in manufacturer packaging.',
        }, {
            'code': 'FBA_INB_0005',
            'details': "There's an input error with the resource 'SKU-TWO'.",
            'message': 'Weight needs to be provided in manufacturer packaging.',
        }]}})

        self.assertEqual(rows, [{
            'msku': 'SKU-ONE', 'missing_dimensions': True, 'missing_weight': True,
        }, {
            'msku': 'SKU-TWO', 'missing_dimensions': False, 'missing_weight': True,
        }])

    def test_package_measurement_patch_uses_amazon_us_schema(self):
        class FakeListingsApi:
            def patch_listings_item(self, seller_id, sku, **kwargs):
                self.call = (seller_id, sku, kwargs)
                return SimpleNamespace(payload={'status': 'ACCEPTED', 'issues': []}, errors=None)

        api = FakeListingsApi()
        saved = _fba_patch_listing_package_measurements(
            'SKU-ONE', {'amazon_product_type': 'DRINKING_CUP'},
            {'length_in': 6.25, 'width_in': 5, 'height_in': 4.5, 'weight_lb': 1.2},
            client=(api, 'SELLER-1', 'ATVPDKIKX0DER'),
        )

        self.assertEqual(api.call[0:2], ('SELLER-1', 'SKU-ONE'))
        body = api.call[2]['body']
        self.assertEqual(body['productType'], 'DRINKING_CUP')
        self.assertEqual(body['patches'][0]['path'], '/attributes/item_package_dimensions')
        self.assertEqual(body['patches'][0]['value'][0]['length'], {
            'value': 6.25, 'unit': 'inches',
        })
        self.assertEqual(body['patches'][1]['value'][0]['unit'], 'pounds')
        self.assertEqual(saved['amazon_status'], 'ACCEPTED')

    def test_collect_reads_sdk_pagination_beyond_first_hundred_items(self):
        calls = []
        def api(**kwargs):
            calls.append(kwargs)
            if kwargs.get('paginationToken') == 'page2':
                return SimpleNamespace(payload={'items': list(range(100, 127))}, pagination={}, next_token=None)
            return SimpleNamespace(payload={'items': list(range(100))}, pagination={'nextToken': 'page2'})
        self.assertEqual(ss_fba_shipments._fba_amazon_collect(api, 'items'), list(range(127)))
        self.assertEqual(calls[1]['paginationToken'], 'page2')

    def test_collect_supports_payload_and_sdk_next_token_with_empty_page(self):
        for style in ('payload', 'sdk'):
            calls = []
            def api(**kwargs):
                calls.append(kwargs)
                if kwargs.get('paginationToken'):
                    return SimpleNamespace(payload={'items': ['last']})
                if style == 'payload':
                    return SimpleNamespace(payload={'items': [], 'pagination': {'nextToken': 'next'}})
                return SimpleNamespace(payload={'items': []}, next_token='next')
            self.assertEqual(ss_fba_shipments._fba_amazon_collect(api, 'items'), ['last'])
            self.assertEqual(len(calls), 2)

    def test_collect_refuses_repeated_tokens_and_partial_results(self):
        api = lambda **kwargs: SimpleNamespace(payload={'items': ['item']}, pagination={'nextToken': 'same'})
        with self.assertRaises(FbaInboundValidationError):
            ss_fba_shipments._fba_amazon_collect(api, 'items')
        with self.assertRaises(FbaInboundValidationError):
            ss_fba_shipments._fba_amazon_collect(api, 'items', limit=1)

    def test_listing_measurements_allow_retry_without_catalog_dimensions(self):
        saved = dict(length_in=5, width_in=4, height_in=1, weight_lb=.5)
        payload = {'attributes': {
            'item_package_dimensions': [{'marketplace_id': 'US',
                'length': {'value': 12.7, 'unit': 'centimeters'},
                'width': {'value': 4, 'unit': 'inches'},
                'height': {'value': 1, 'unit': 'inches'}}],
            'item_package_weight': [{'marketplace_id': 'US', 'value': 8, 'unit': 'ounces'}],
        }, 'issues': [{'severity': 'WARNING', 'message': 'Optional description'}]}
        self.assertTrue(_fba_listing_measurement_status(payload, saved, 'US')['listing_ready'])
        self.assertFalse(_fba_listing_measurement_status(payload, saved, 'CA')['listing_ready'])
        self.assertFalse(_fba_listing_measurement_status(payload, dict(saved, length_in=6), 'US')['listing_ready'])
        payload['issues'] = [{'severity': 'ERROR', 'message': 'Approval required'}]
        self.assertFalse(_fba_listing_measurement_status(payload, saved, 'US')['listing_ready'])
        payload['issues'] = []
        del payload['attributes']['item_package_weight']
        self.assertFalse(_fba_listing_measurement_status(payload, saved, 'US')['listing_ready'])

    def test_catalog_measurement_reference_separates_item_and_package_sizes(self):
        reference = _fba_catalog_measurement_reference_from_payload({
            'dimensions': [{
                'marketplaceId': 'ATVPDKIKX0DER',
                'item': {
                    'length': {'value': 4, 'unit': 'inches'},
                    'width': {'value': 4, 'unit': 'inches'},
                    'height': {'value': 4, 'unit': 'inches'},
                },
                'package': {
                    'length': {'value': 5, 'unit': 'inches'},
                    'width': {'value': 5, 'unit': 'inches'},
                    'height': {'value': 5, 'unit': 'inches'},
                    'weight': {'value': 1.25, 'unit': 'pounds'},
                },
            }],
        }, 'ATVPDKIKX0DER')

        self.assertEqual(reference['item']['length']['value'], 4.0)
        self.assertEqual(reference['package']['weight']['unit'], 'pounds')
        self.assertTrue(reference['package_ready'])

    def test_inbound_activation_delay_names_items_and_preserves_scan(self):
        error = (
            "The following MSKUs are not available for inbound. "
            "MSKUs: ['8S-345H-GIBX', 'T0-3XBV-EEYX']"
        )
        mskus = _fba_inbound_unavailable_skus(error)
        self.assertEqual(mskus, ['8S-345H-GIBX', 'T0-3XBV-EEYX'])
        message = _fba_inbound_activation_message(mskus, [{
            'seller_sku': '8S-345H-GIBX',
            'barcode': '047596044292',
            'title': 'Tablecloth',
        }])
        self.assertIn('8S-345H-GIBX · barcode 047596044292 · Tablecloth', message)
        self.assertIn('T0-3XBV-EEYX', message)
        self.assertIn('not rejected', message)
        self.assertIn('no rescan is needed', message)

    def test_create_plan_groups_duplicate_skus_and_forces_us_seller_prep(self):
        session = {
            "batch_name": "Friday run",
            "items": [
                {"barcode": "111", "seller_sku": "SKU-1", "quantity": 2, "label_owner": "AMAZON"},
                {"barcode": "222", "seller_sku": "SKU-1", "quantity": 3, "label_owner": "AMAZON"},
            ],
        }
        payload = build_create_plan_request(session, ADDRESS, US_MARKETPLACE_ID)
        self.assertEqual(payload["name"], "Friday run")
        self.assertEqual(payload["items"], [{
            "msku": "SKU-1", "quantity": 5, "prepOwner": "SELLER", "labelOwner": "SELLER"
        }])

    def test_manufacturer_barcode_uses_no_amazon_label_service(self):
        session = {"items": [{
            "barcode": "111", "seller_sku": "SKU-UPC", "quantity": 1,
            "fba": {"barcode_guidance": {
                "status": "manufacturer_barcode", "checked": True,
                "source": "amazon_sp_api", "instruction": "CanUseOriginalBarcode",
                "identifier_type": "seller_sku", "identifier": "SKU-UPC",
            }},
        }]}
        item = build_create_plan_request(session, ADDRESS, US_MARKETPLACE_ID)["items"][0]
        self.assertEqual(item["prepOwner"], "SELLER")
        self.assertEqual(item["labelOwner"], "NONE")

    def test_amazon_none_prep_rejection_corrects_create_plan_for_retry(self):
        payload = build_create_plan_request(
            {"items": [{
                "barcode": "194137223361", "seller_sku": "94-GMTH-EAPI", "quantity": 1,
            }]},
            ADDRESS,
            US_MARKETPLACE_ID,
        )
        corrections = apply_owner_corrections_from_amazon_error(
            payload,
            "ERROR: 94-GMTH-EAPI does not require prepOwner but SELLER was assigned. "
            "Accepted values: [NONE]",
        )
        self.assertEqual(payload["items"][0]["prepOwner"], "NONE")
        self.assertEqual(corrections, [{
            "msku": "94-GMTH-EAPI",
            "field": "prepOwner",
            "from": "SELLER",
            "to": "NONE",
            "accepted": ["NONE"],
        }])

    def test_carton_payload_preserves_owner_values_returned_by_amazon(self):
        plan_items = [{
            "msku": "94-GMTH-EAPI", "quantity": 1,
            "prepOwner": "NONE", "labelOwner": "SELLER",
        }]
        boxes = [{
            "local_id": "BOX-01", "packing_group_id": "pg-1",
            "length_in": 12, "width_in": 10, "height_in": 8, "weight_lb": 2,
            "contents": [{"msku": "94-GMTH-EAPI", "quantity": 1}],
        }]
        payload, _normalized = build_set_packing_request(boxes, plan_items)
        item = payload["packageGroupings"][0]["boxes"][0]["items"][0]
        self.assertEqual(item["prepOwner"], "NONE")
        self.assertEqual(item["labelOwner"], "SELLER")

    def test_create_plan_requires_every_seller_sku(self):
        with self.assertRaisesRegex(FbaInboundValidationError, "seller SKU"):
            build_create_plan_request(
                {"items": [{"barcode": "111", "quantity": 1}]}, ADDRESS, US_MARKETPLACE_ID
            )

    def test_us_ship_from_requires_two_letter_state_code(self):
        address = dict(ADDRESS, stateOrProvinceCode="FLORIDA")
        with self.assertRaisesRegex(FbaInboundValidationError, "2-letter state code"):
            build_create_plan_request(
                {"items": [{"barcode": "111", "seller_sku": "SKU-1", "quantity": 1}]},
                address,
                US_MARKETPLACE_ID,
            )

    def test_incomplete_carton_can_be_saved_as_local_draft(self):
        boxes = normalize_box_drafts(
            [{"local_id": "box-1", "packing_group_id": "pg-1", "contents": []}],
            [{"msku": "SKU-1", "quantity": 1}],
            allow_incomplete=True,
        )
        self.assertEqual(boxes[0]["local_id"], "BOX-1")
        self.assertIsNone(boxes[0]["length_in"])

    def test_box_payload_requires_exact_counts_and_measurements(self):
        plan_items = [{"msku": "SKU-1", "quantity": 2}]
        boxes = [{
            "local_id": "BOX-01", "packing_group_id": "pg-1",
            "length_in": 12, "width_in": 10, "height_in": 8, "weight_lb": 4.5,
            "contents": [{"msku": "SKU-1", "quantity": 2}],
        }]
        payload, normalized = build_set_packing_request(boxes, plan_items)
        carton = payload["packageGroupings"][0]["boxes"][0]
        self.assertEqual(carton["dimensions"]["unitOfMeasurement"], "IN")
        self.assertEqual(carton["weight"], {"unit": "LB", "value": 4.5})
        self.assertEqual(carton["items"][0]["quantity"], 2)
        self.assertEqual(normalized[0]["local_id"], "BOX-01")

        boxes[0]["contents"][0]["quantity"] = 1
        with self.assertRaisesRegex(FbaInboundValidationError, "do not match"):
            build_set_packing_request(boxes, plan_items)

    def test_missing_item_recovery_reduces_only_unpacked_quantity(self):
        items = [{
            "barcode": "111", "barcode_key": "111", "seller_sku": "SKU-1",
            "quantity": 3, "planned_fba_quantity": 3,
        }]
        updated, detail = reduce_missing_plan_quantity(
            items, "sku-1", 1, packed_quantity=2, barcode_key="111"
        )
        self.assertEqual(updated[0]["quantity"], 2)
        self.assertEqual(updated[0]["planned_fba_quantity"], 2)
        self.assertEqual(detail["packed_quantity"], 2)
        self.assertEqual(items[0]["quantity"], 3, "the saved source rows must not mutate early")

        with self.assertRaisesRegex(FbaInboundValidationError, "Only 1 unpacked"):
            reduce_missing_plan_quantity(items, "SKU-1", 2, packed_quantity=2)

    def test_recovery_preserves_compatible_box_and_splits_changed_group(self):
        boxes = [{
            "local_id": "BOX-01", "packing_group_id": "old-group",
            "length_in": 12, "width_in": 10, "height_in": 8, "weight_lb": 5,
            "contents": [
                {"msku": "SKU-1", "quantity": 2},
                {"msku": "SKU-2", "quantity": 1},
            ],
        }]
        compatible, conflicts = remap_recovery_boxes(boxes, [{
            "packing_group_id": "new-group",
            "items": [{"msku": "SKU-1"}, {"msku": "SKU-2"}],
        }])
        self.assertFalse(conflicts)
        self.assertEqual(compatible[0]["local_id"], "BOX-01")
        self.assertEqual(compatible[0]["packing_group_id"], "new-group")
        self.assertEqual(compatible[0]["weight_lb"], 5)

        split, conflicts = remap_recovery_boxes(boxes, [
            {"packing_group_id": "group-a", "items": [{"msku": "SKU-1"}]},
            {"packing_group_id": "group-b", "items": [{"msku": "SKU-2"}]},
        ])
        self.assertEqual(len(split), 2)
        self.assertEqual(split[0]["local_id"], "BOX-01")
        self.assertIsNone(split[0]["weight_lb"])
        self.assertEqual(conflicts[0]["moves"][0]["msku"], "SKU-2")
        self.assertEqual(conflicts[0]["moves"][0]["to_box_id"], "BOX-02")

    def test_standard_fba_box_limits_are_enforced_and_dimensions_are_canonicalized(self):
        box = {
            "local_id": "BOX-01", "packing_group_id": "pg-1",
            "length_in": 25, "width_in": 36, "height_in": 25, "weight_lb": 50,
            "contents": [{"msku": "SKU-1", "quantity": 1}],
        }
        normalized = validate_box_specifications(box)
        self.assertEqual(
            (normalized["length_in"], normalized["width_in"], normalized["height_in"]),
            (36.0, 25.0, 25.0),
        )

        for field, value in (("length_in", 36.01), ("weight_lb", 50.01)):
            invalid = dict(box, **{field: value})
            with self.subTest(field=field), self.assertRaisesRegex(
                FbaInboundValidationError, "standard U.S. FBA limit"
            ):
                validate_box_specifications(invalid)

    def test_fba_recommended_minimum_does_not_block_a_valid_small_carton(self):
        box = {
            "local_id": "BOX-01", "length_in": 5.99, "width_in": 4,
            "height_in": 1, "weight_lb": 0.5,
            "contents": [{"msku": "SKU-1", "quantity": 1}],
        }
        self.assertEqual(validate_box_specifications(box)["weight_lb"], 0.5)

        with self.assertRaisesRegex(FbaInboundValidationError, "greater than zero"):
            validate_box_specifications(dict(box, length_in="nan"))

    def test_oversized_exception_requires_exactly_one_unit(self):
        box = {
            "local_id": "BOX-01", "length_in": 40, "width_in": 20,
            "height_in": 10, "weight_lb": 55,
            "single_oversize_exception": True,
            "contents": [{"msku": "SKU-1", "quantity": 1}],
        }
        self.assertTrue(validate_box_specifications(box)["single_oversize_exception"])
        box["contents"][0]["quantity"] = 2
        with self.assertRaisesRegex(FbaInboundValidationError, "exactly one unit"):
            validate_box_specifications(box)

    def test_jewelry_and_watch_cartons_cannot_exceed_40_lb(self):
        box = {
            "local_id": "BOX-01", "length_in": 12, "width_in": 10,
            "height_in": 8, "weight_lb": 40.01,
            "contains_jewelry_or_watches": True,
            "contents": [{"msku": "WATCH-1", "quantity": 1}],
        }
        with self.assertRaisesRegex(FbaInboundValidationError, "cannot exceed 40 lb"):
            validate_box_specifications(box)

    def test_transport_request_has_one_configuration_per_shipment(self):
        ready = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        payload = build_transportation_request("po-1", ["sh-1", "sh-2"], ready, ADDRESS)
        self.assertEqual(payload["placementOptionId"], "po-1")
        self.assertEqual(
            [row["shipmentId"] for row in payload["shipmentTransportationConfigurations"]],
            ["sh-1", "sh-2"],
        )

    def test_transport_request_for_today_uses_future_timestamp(self):
        before = datetime.datetime.now(datetime.timezone.utc)
        payload = build_transportation_request(
            "po-1", ["sh-1"], datetime.date.today().isoformat(), ADDRESS
        )
        start = payload["shipmentTransportationConfigurations"][0]["readyToShipWindow"]["start"]
        start_at = datetime.datetime.fromisoformat(start.replace("Z", "+00:00"))

        self.assertGreater(start_at, before)

    def test_item_label_job_uses_amazon_plan_fnsku(self):
        plan_items = [{
            "msku": "SKU-1", "fnsku": "X001234567", "quantity": 2,
            "labelOwner": "SELLER",
        }]
        session_items = [{
            "seller_sku": "SKU-1", "title": "Test item", "condition": "New",
            "fba": {"barcode_guidance": {
                "status": "amazon_barcode", "checked": True,
                "source": "amazon_sp_api", "instruction": "RequiresFNSKULabel",
                "identifier_type": "seller_sku", "identifier": "SKU-1",
            }},
        }]
        job = build_item_label_job(plan_items, session_items, "sku-1")
        self.assertEqual(job["fnsku"], "X001234567")
        self.assertEqual(job["title"], "Test item")

    def test_item_label_job_refuses_unverified_label_requirement(self):
        plan_items = [{
            "msku": "SKU-1", "fnsku": "X001234567", "quantity": 1,
            "labelOwner": "SELLER",
        }]
        session_items = [{
            "seller_sku": "SKU-1",
            "fba": {"barcode_guidance": {"status": "unavailable", "checked": False}},
        }]
        with self.assertRaisesRegex(FbaInboundValidationError, "explicitly required"):
            build_item_label_job(plan_items, session_items, "SKU-1")

        forged_items = [{
            "seller_sku": "SKU-1",
            "fba": {"barcode_guidance": {"status": "amazon_barcode", "checked": True}},
        }]
        with self.assertRaisesRegex(FbaInboundValidationError, "explicitly required"):
            build_item_label_job(plan_items, forged_items, "SKU-1")

    def test_item_label_job_accepts_explicit_inbound_plan_label_instruction(self):
        plan_items = [{
            "msku": "SKU-1", "fnsku": "X001234567", "quantity": 1,
            "labelOwner": "SELLER",
            "prepInstructions": [{"prepType": "ITEM_LABELING", "prepOwner": "SELLER"}],
        }]
        session_items = [{"seller_sku": "SKU-1", "title": "Plan-directed label"}]
        job = build_item_label_job(plan_items, session_items, "SKU-1")
        self.assertEqual(job["fnsku"], "X001234567")

    def test_item_label_job_requires_authoritative_ten_character_fnsku(self):
        plan_items = [{"msku": "SKU-1", "fnsku": "SKU-1", "labelOwner": "SELLER"}]
        session_items = [{
            "seller_sku": "SKU-1",
            "fba": {"barcode_guidance": {
                "status": "amazon_barcode", "checked": True,
                "source": "amazon_sp_api", "instruction": "RequiresFNSKULabel",
                "identifier_type": "seller_sku", "identifier": "SKU-1",
            }},
        }]
        with self.assertRaisesRegex(FbaInboundValidationError, "assigned a printable FNSKU"):
            build_item_label_job(plan_items, session_items, "SKU-1")

    def test_transport_summary_accepts_parcel_and_rejects_freight(self):
        common = {
            'shipmentId': 'shipment-1',
            'shippingSolution': 'AMAZON_PARTNERED_CARRIER',
            'quote': {'cost': {'amount': 15.52, 'code': 'USD'}},
            'preconditions': [],
        }
        parcel = _fba_amazon_transport_summary({
            **common, 'transportationOptionId': 'option-parcel',
            'shippingMode': 'GROUND_SMALL_PARCEL',
        })
        freight = _fba_amazon_transport_summary({
            **common, 'transportationOptionId': 'option-ltl',
            'shippingMode': 'FREIGHT_LTL',
        })
        self.assertTrue(parcel['can_purchase'])
        self.assertFalse(freight['can_purchase'])


if __name__ == "__main__":
    unittest.main()


class PlanHealthTest(unittest.TestCase):
    NOT_VALID = (
        "[{'code': 'BadRequest', 'message': 'ERROR: The following MSKUs are not valid: [SKU-BAD].', "
        "'details': ''}]"
    )
    PENDING = (
        'ERROR: The following MSKUs are not available for inbound. If these were recently added, '
        'please try again later. MSKUs: [SKU-NEW, SKU-2].'
    )

    def test_prep_error_parser_classifies_amazon_messages(self):
        self.assertEqual(
            ss_fba_readiness._fba_prep_error_mskus(Exception(self.NOT_VALID)), ('invalid', ['SKU-BAD'])
        )
        self.assertEqual(
            ss_fba_readiness._fba_prep_error_mskus(Exception(self.PENDING)), ('unavailable', ['SKU-NEW', 'SKU-2'])
        )
        self.assertEqual(_fba_inbound_unavailable_skus(Exception(self.PENDING)), ['SKU-NEW', 'SKU-2'])
        self.assertEqual(_fba_inbound_unavailable_skus(Exception(self.NOT_VALID)), [])
        self.assertEqual(ss_fba_readiness._fba_prep_error_mskus(Exception('Something went wrong')), ('', []))
        validation = Exception(
            "1 validation error detected: Value '[MskuPrepDetailInput(msku=SKU-9, prepCategory=NONE, "
            "prepTypes=[ITEM_NO_PREP])]' at 'request.mskuPrepDetails' failed to satisfy constraint"
        )
        self.assertNotIn(ss_fba_readiness._fba_prep_error_mskus(validation)[0], ('invalid', 'unavailable'))

    def _fake_api(self):
        api = SimpleNamespace(calls=[])

        def list_prep_details(**kwargs):
            api.calls.append(list(kwargs['mskus']))
            if 'SKU-BAD' in kwargs['mskus']:
                raise Exception(self.NOT_VALID)
            return SimpleNamespace(
                payload={'mskuPrepDetails': [{'msku': msku} for msku in kwargs['mskus']]}, errors=None
            )
        api.list_prep_details = list_prep_details
        return api

    def _fake_listings(self):
        listings = SimpleNamespace(calls=[])

        def search_listings_items(seller_id, **kwargs):
            # Amazon reads this parameter as one comma-delimited string.
            identifiers = kwargs['identifiers'].split(',')
            listings.calls.append(identifiers)
            rows = []
            for sku in identifiers:
                if sku == 'SKU-BAD':
                    continue
                channel = 'DEFAULT' if sku == 'SKU-FBM' else 'AMAZON_NA'
                rows.append({
                    'sku': sku, 'summaries': [{}],
                    'fulfillmentAvailability': [{'fulfillmentChannelCode': channel, 'quantity': 1}],
                })
            return SimpleNamespace(payload={'items': rows}, errors=None)
        listings.search_listings_items = search_listings_items
        return listings, 'SELLER', US_MARKETPLACE_ID

    def test_plan_health_names_deleted_listing_and_its_carton(self):
        api = self._fake_api()
        health = ss_fba_shipments._fba_plan_health(
            api, US_MARKETPLACE_ID,
            [
                {'msku': 'SKU-OK', 'quantity': 2, 'asin': 'B0OK'},
                {'msku': 'SKU-BAD', 'quantity': 1, 'asin': 'B0BAD', 'fnsku': 'X00BAD'},
                {'msku': 'SKU-FBM', 'quantity': 1},
            ],
            session_items=[{'seller_sku': 'SKU-BAD', 'title': 'Waterford Decanter', 'barcode': '701587419475'}],
            boxes=[{'local_id': 'BOX-04', 'contents': [
                {'msku': 'SKU-BAD', 'quantity': 1}, {'msku': 'SKU-OK', 'quantity': 2},
            ]}],
            listings=self._fake_listings(),
        )
        self.assertEqual(api.calls, [['SKU-OK', 'SKU-BAD', 'SKU-FBM'], ['SKU-OK', 'SKU-FBM']])
        self.assertEqual(health['blocking_count'], 1)
        self.assertEqual(health['checked_count'], 3)
        self.assertEqual(
            [(row['msku'], row['code'], row['severity']) for row in health['findings']],
            [('SKU-BAD', 'listing_missing', 'blocking'), ('SKU-FBM', 'merchant_fulfilled', 'warning')],
        )
        bad = health['findings'][0]
        self.assertEqual(bad['box_ids'], ['BOX-04'])
        self.assertEqual(bad['fnsku'], 'X00BAD')
        self.assertEqual(bad['quantity'], 1)
        for text in ('SKU-BAD', 'Waterford Decanter', '701587419475', 'BOX-04', 'not valid'):
            self.assertIn(text, bad['message'])
        self.assertIn('SKU-BAD', health['summary'])
        self.assertIn('BOX-04', health['summary'])
        self.assertIn('merchant-fulfilled', health['findings'][1]['message'])

    def test_plan_health_ignores_amazon_outages(self):
        def fail(*args, **kwargs):
            raise Exception('InternalServerError')
        api = SimpleNamespace(list_prep_details=fail)
        listings = SimpleNamespace(search_listings_items=fail)
        health = ss_fba_shipments._fba_plan_health(
            api, US_MARKETPLACE_ID, [{'msku': 'SKU-OK', 'quantity': 1}],
            listings=(listings, 'SELLER', US_MARKETPLACE_ID),
        )
        self.assertEqual(health['findings'], [])
        self.assertEqual(health['blocking_count'], 0)
        self.assertEqual(health['summary'], '')

    def test_failed_placement_is_diagnosed_once_and_explained(self):
        state = {
            'operation': {'id': 'op-1', 'kind': 'generate_placement', 'status': 'FAILED', 'problems': [
                {'code': 'InternalServerError', 'message': 'ERROR: Something went wrong.'},
            ]},
            'plan': {'marketplaceIds': [US_MARKETPLACE_ID]},
            'plan_items': [{'msku': 'SKU-BAD', 'quantity': 1}], 'boxes': [],
            'last_error': 'ERROR: Something went wrong.',
        }
        health = {
            'findings': [{'msku': 'SKU-BAD', 'severity': 'blocking'}], 'blocking_count': 1,
            'summary': 'Plan check found 1 item Amazon can no longer ship: SKU-BAD',
        }
        with patch.object(ss_fba_shipments, '_fba_plan_health', return_value=dict(health)) as check:
            state = ss_fba_shipments._fba_diagnose_failed_operation(object(), state)
            state = ss_fba_shipments._fba_diagnose_failed_operation(object(), state)
        self.assertEqual(check.call_count, 1)
        self.assertEqual(state['last_error'], health['summary'])
        self.assertEqual(state['plan_health']['operation_id'], 'op-1')
        self.assertEqual(state['plan_health']['trigger'], 'generate_placement')
        untouched = {'operation': {'id': 'op-2', 'kind': 'create_plan', 'status': 'FAILED'}, 'plan_items': []}
        with patch.object(ss_fba_shipments, '_fba_plan_health') as check:
            ss_fba_shipments._fba_diagnose_failed_operation(object(), untouched)
        check.assert_not_called()

    def test_plan_creation_names_deleted_listings(self):
        message = self.NOT_VALID.replace('SKU-BAD', 'SKU-001')

        class InvalidApi(PrepBatchApi):
            def list_prep_details(self, **kwargs):
                raise Exception(message)

        api = InvalidApi()
        with patch.object(ss_fba_readiness, '_fba_session_listing_health', return_value={'findings': []}), patch.object(ss_fba_shipments, '_fba_amazon_source_from_settings', return_value=ADDRESS):
            with self.assertRaisesRegex(FbaInboundValidationError, 'no longer recognizes.*SKU-001'):
                ss_fba_shipments._fba_prepare_and_create_plan(api, US_MARKETPLACE_ID, ready_plan_session(3), {})
        self.assertEqual(api.created, [])

    def test_scan_listing_check_stops_only_definite_rejections(self):
        ss_fba_shipments._FBA_SCAN_LISTING_CACHE.clear()

        def reject(**kwargs):
            raise Exception(self.NOT_VALID)

        def outage(**kwargs):
            raise Exception('boom')

        rejecting = SimpleNamespace(list_prep_details=reject)
        down = SimpleNamespace(list_prep_details=outage)
        fine = SimpleNamespace(list_prep_details=lambda **kwargs: SimpleNamespace(
            payload={'mskuPrepDetails': []}, errors=None))
        try:
            message = ss_fba_shipments._fba_scan_listing_problem(
                'SKU-BAD', api=rejecting, marketplace_id=US_MARKETPLACE_ID)
            self.assertIn('no longer recognizes Seller SKU SKU-BAD', message)
            self.assertEqual(ss_fba_shipments._fba_scan_listing_problem(
                'SKU-BAD', api=down, marketplace_id=US_MARKETPLACE_ID), message)
            self.assertEqual(ss_fba_shipments._fba_scan_listing_problem(
                'SKU-OK', api=down, marketplace_id=US_MARKETPLACE_ID), '')
            self.assertEqual(ss_fba_shipments._fba_scan_listing_problem(
                'SKU-OK', api=fine, marketplace_id=US_MARKETPLACE_ID), '')
        finally:
            ss_fba_shipments._FBA_SCAN_LISTING_CACHE.clear()

    def test_readiness_treats_merchant_only_fnsku_as_needing_enablement(self):
        listings = Mock()
        listings.get_listings_item.return_value = Mock(errors=None, payload={
            'summaries': [{'productType': 'PICTURE_FRAME', 'status': ['BUYABLE'], 'fnSku': 'X0059HIRRX'}],
            'issues': [],
            'fulfillmentAvailability': [{'fulfillmentChannelCode': 'DEFAULT', 'quantity': 1}],
            'attributes': {'fulfillment_availability': [{'fulfillment_channel_code': 'DEFAULT', 'quantity': 1}]},
        })
        client = (listings, 'SELLER', US_MARKETPLACE_ID)
        with (patch.object(ss_fba_readiness, '_fba_inventory_fnsku', return_value='X0059HIRRX'),
              patch.object(ss_fba_readiness, '_fba_item_prep_details', return_value={'checked': True, 'error': ''})):
            result = ss_fba_readiness._fba_listing_readiness('DS-IOM1-7N19', force_refresh=True, client=client)
        self.assertEqual(result['status'], 'needs_enablement')
        self.assertIn('merchant-fulfilled', result['activation_note'])
        listings.get_listings_item.return_value = Mock(errors=None, payload={
            'summaries': [{'productType': 'PICTURE_FRAME', 'status': ['BUYABLE'], 'fnSku': 'X0059HIRRX'}],
            'issues': [],
            'fulfillmentAvailability': [{'fulfillmentChannelCode': 'AMAZON_NA'}],
            'attributes': {},
        })
        with (patch.object(ss_fba_readiness, '_fba_inventory_fnsku', return_value='X0059HIRRX'),
              patch.object(ss_fba_readiness, '_fba_item_prep_details', return_value={'checked': True, 'error': ''})):
            result = ss_fba_readiness._fba_listing_readiness('DS-IOM1-7N19', force_refresh=True, client=client)
        self.assertEqual(result['status'], 'ready')

    NOT_FOUND = "[{'code': 'NOT_FOUND', 'message': \"SKU 'GONE-1' not found\"}]"

    def test_readiness_lets_a_missing_listing_propagate_for_relisting(self):
        listings = Mock()
        listings.get_listings_item.side_effect = Exception(self.NOT_FOUND)
        with (patch.object(ss_fba_readiness, '_fba_inventory_fnsku', return_value='X00GONE'),
              patch.object(ss_fba_readiness, '_fba_item_prep_details', return_value={'checked': True, 'error': ''})):
            with self.assertRaisesRegex(Exception, 'NOT_FOUND'):
                ss_fba_readiness._fba_listing_readiness('GONE-1', force_refresh=True, client=(listings, 'SELLER', US_MARKETPLACE_ID))

    def test_readiness_fails_when_inbound_service_rejects_the_sku(self):
        listings = Mock()
        listings.get_listings_item.return_value = Mock(errors=None, payload={
            'summaries': [{'productType': 'PITCHER', 'status': ['BUYABLE'], 'fnSku': 'X00STALE'}],
            'issues': [], 'fulfillmentAvailability': [{'fulfillmentChannelCode': 'AMAZON_NA'}], 'attributes': {},
        })
        rejected = {'checked': False, 'error': "ERROR: The following MSKUs are not valid: [SKU-DEAD]."}
        with (patch.object(ss_fba_readiness, '_fba_inventory_fnsku', return_value='X00STALE'),
              patch.object(ss_fba_readiness, '_fba_item_prep_details', return_value=rejected)):
            result = ss_fba_readiness._fba_listing_readiness('SKU-DEAD', force_refresh=True, client=(listings, 'SELLER', US_MARKETPLACE_ID))
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(result['listing_missing'])
        self.assertIn('SKU-DEAD', result['error'])

    def test_session_listing_health_flags_counted_items_and_clears_them_again(self):
        items = [
            {'seller_sku': 'SKU-OK', 'title': 'Fine', 'fba_enablement_status': 'ready', 'amazon_fnsku': 'X001'},
            {'seller_sku': 'SKU-BAD', 'title': 'Gone', 'fba_enablement_status': 'ready', 'amazon_fnsku': 'X002'},
            {'seller_sku': 'SKU-FBM', 'title': 'Merchant', 'fba_enablement_status': 'ready', 'amazon_fnsku': 'X003'},
        ]
        health = ss_fba_shipments._fba_plan_health(
            self._fake_api(), US_MARKETPLACE_ID, items, session_items=items, listings=self._fake_listings(),
        )
        changed = ss_fba_readiness._fba_apply_session_listing_health(items, health)
        self.assertEqual(changed, ['SKU-BAD', 'SKU-FBM'])
        self.assertEqual(items[0]['fba_enablement_status'], 'ready')
        self.assertEqual(items[1]['fba_enablement_status'], 'failed')
        self.assertEqual(items[1]['fba_listing_check'], 'listing_missing')
        self.assertIn('Gone', items[1]['fba_enablement_error'])
        self.assertEqual(items[1]['inbound_error'], items[1]['fba_enablement_error'])
        self.assertEqual(items[2]['fba_enablement_status'], 'needs_enablement')
        self.assertIn('merchant-fulfilled', items[2]['fba_activation_note'])
        self.assertEqual(ss_fba_readiness._fba_apply_session_listing_health(items, health), [])
        self.assertEqual(ss_fba_readiness._fba_apply_session_listing_health(items, {'findings': []}), ['SKU-BAD', 'SKU-FBM'])
        self.assertEqual(items[1]['fba_enablement_status'], 'ready')
        self.assertEqual(items[1]['fba_listing_check'], '')
        self.assertEqual(items[2]['fba_enablement_status'], 'ready')

    def test_plan_creation_is_refused_when_amazon_rejects_a_counted_item(self):
        api = PrepBatchApi()
        health = {'findings': [
            {'msku': 'SKU-001', 'code': 'listing_missing', 'severity': 'blocking', 'title': 'Gone glass', 'message': 'gone'},
            {'msku': 'SKU-002', 'code': 'merchant_fulfilled', 'severity': 'warning', 'title': 'FBM glass', 'message': 'fbm'},
        ], 'blocking_count': 1}
        with (patch.object(ss_fba_readiness, '_fba_session_listing_health', return_value=health),
              patch.object(ss_fba_shipments, '_fba_amazon_source_from_settings', return_value=ADDRESS)):
            with self.assertRaises(ss_fba_shipments.FbaPlanItemsUnavailable) as caught:
                ss_fba_shipments._fba_prepare_and_create_plan(api, US_MARKETPLACE_ID, ready_plan_session(3), {})
        message = str(caught.exception)
        self.assertIn('SKU-001 (Gone glass)', message)
        self.assertIn('merchant-fulfilled', message)
        self.assertIn('SKU-002 (FBM glass)', message)
        self.assertIs(caught.exception.health, health)
        self.assertEqual(api.created, [])
        self.assertEqual(api.read_batches, [])


class CancelledPlanRestoreTests(unittest.TestCase):
    """Shipping cancelled in Seller Central must stop looking like a live shipment."""

    @staticmethod
    def shipment(shipment_id, status, warehouse='LBE1'):
        return {
            'shipmentId': shipment_id,
            'shipmentConfirmationId': 'FBA' + shipment_id.upper(),
            'status': status,
            'destination': {'warehouseId': warehouse},
        }

    def test_live_plan_reports_no_cancellation(self):
        self.assertIsNone(detect_amazon_cancellation(
            {'status': 'ACTIVE'},
            [self.shipment('s1', 'WORKING'), self.shipment('s2', 'IN_TRANSIT')],
        ))

    def test_voided_plan_is_restorable_with_destinations_preserved(self):
        result = detect_amazon_cancellation(
            {'status': 'VOIDED'},
            [self.shipment('s1', 'CANCELLED', 'LBE1'), self.shipment('s2', 'CANCELLED', 'SCK8')],
        )
        self.assertTrue(result['restorable'])
        self.assertTrue(result['plan_cancelled'])
        self.assertFalse(result['partial'])
        self.assertEqual(result['destinations'], ['LBE1', 'SCK8'])
        self.assertEqual([row['shipmentConfirmationId'] for row in result['cancelled_shipments']],
                         ['FBAS1', 'FBAS2'])

    def test_every_shipment_cancelled_is_restorable_on_an_active_plan(self):
        result = detect_amazon_cancellation({'status': 'ACTIVE'}, [self.shipment('s1', 'DELETED')])
        self.assertTrue(result['restorable'])
        self.assertFalse(result['plan_cancelled'])

    def test_partly_cancelled_plan_is_reported_but_never_restorable(self):
        result = detect_amazon_cancellation(
            {'status': 'ACTIVE'},
            [self.shipment('s1', 'CANCELLED'), self.shipment('s2', 'IN_TRANSIT')],
        )
        self.assertFalse(result['restorable'])
        self.assertTrue(result['partial'])
        self.assertEqual([row['shipmentId'] for row in result['live_shipments']], ['s2'])

    def test_sync_clears_a_confirmed_shipment_amazon_cancelled(self):
        class FakeInboundApi:
            @staticmethod
            def response(**payload):
                return SimpleNamespace(payload=payload, errors=None)

            def get_inbound_plan(self, _plan_id):
                return self.response(inboundPlanId='plan-1', status='VOIDED')

            def list_inbound_plan_items(self, _plan_id, **_kwargs):
                return self.response(items=[])

            def list_packing_options(self, _plan_id, **_kwargs):
                return self.response(packingOptions=[{
                    'packingOptionId': 'packing-1', 'status': 'ACCEPTED', 'packingGroups': [],
                }])

            def list_placement_options(self, _plan_id, **_kwargs):
                return self.response(placementOptions=[{
                    'placementOptionId': 'placement-1', 'status': 'ACCEPTED',
                    'shipmentIds': ['shipment-1'],
                }])

            def get_shipment(self, _plan_id, shipment_id):
                return self.response(
                    shipmentId=shipment_id, status='CANCELLED',
                    shipmentConfirmationId='FBA19PL7W2P5',
                    destination={'warehouseId': 'SCK8'},
                    selectedTransportationOptionId='to-1',
                )

            def list_transportation_options(self, _plan_id, **kwargs):
                return self.response(transportationOptions=[])

            def list_shipment_boxes(self, _plan_id, _shipment_id, **_kwargs):
                raise AssertionError('carton labels must not be fetched for a cancelled plan')

        state = {
            'inbound_plan_id': 'plan-1',
            'operation': {'status': 'SUCCESS'},
            'boxes_submitted': True,
            'transport_confirmed': True,
            'selected_transportation': [{'shipmentId': 'shipment-1'}],
        }

        refreshed = _fba_amazon_sync_snapshot(FakeInboundApi(), state)

        self.assertFalse(refreshed['transport_confirmed'])
        self.assertEqual(refreshed['selected_transportation'], [])
        self.assertEqual(refreshed['stage'], 'plan_cancelled')
        self.assertTrue(refreshed['amazon_cancelled']['restorable'])
        self.assertEqual(
            refreshed['amazon_cancelled']['cancelled_shipments'][0]['shipmentConfirmationId'],
            'FBA19PL7W2P5',
        )

    def test_sync_keeps_a_live_confirmed_shipment(self):
        class FakeInboundApi:
            def __init__(self):
                self.label_calls = []

            @staticmethod
            def response(**payload):
                return SimpleNamespace(payload=payload, errors=None)

            def get_inbound_plan(self, _plan_id):
                return self.response(inboundPlanId='plan-1', status='ACTIVE')

            def list_inbound_plan_items(self, _plan_id, **_kwargs):
                return self.response(items=[])

            def list_packing_options(self, _plan_id, **_kwargs):
                return self.response(packingOptions=[{
                    'packingOptionId': 'packing-1', 'status': 'ACCEPTED', 'packingGroups': [],
                }])

            def list_placement_options(self, _plan_id, **_kwargs):
                return self.response(placementOptions=[{
                    'placementOptionId': 'placement-1', 'status': 'ACCEPTED',
                    'shipmentIds': ['shipment-1'],
                }])

            def get_shipment(self, _plan_id, shipment_id):
                return self.response(
                    shipmentId=shipment_id, status='IN_TRANSIT',
                    destination={'warehouseId': 'HIA1'},
                    selectedTransportationOptionId='to-1',
                )

            def list_transportation_options(self, _plan_id, **_kwargs):
                return self.response(transportationOptions=[])

            def list_shipment_boxes(self, _plan_id, shipment_id, **_kwargs):
                self.label_calls.append(shipment_id)
                return self.response(boxes=[])

        api = FakeInboundApi()
        refreshed = _fba_amazon_sync_snapshot(api, {
            'inbound_plan_id': 'plan-1', 'operation': {'status': 'SUCCESS'}, 'boxes_submitted': True,
        })

        self.assertTrue(refreshed['transport_confirmed'])
        self.assertEqual(refreshed['stage'], 'transport_confirmed')
        self.assertNotIn('amazon_cancelled', refreshed)
        self.assertEqual(api.label_calls, ['shipment-1'])


class TransportOptionVisibilityTests(unittest.TestCase):
    """Every quote Amazon returns is shown; only partnered small parcel is buyable."""

    def test_partnered_small_parcel_stays_purchasable_without_a_reason(self):
        summary = _fba_amazon_transport_summary({
            'transportationOptionId': 'to-1', 'shipmentId': 'sh-1',
            'shippingSolution': 'AMAZON_PARTNERED_CARRIER', 'shippingMode': 'GROUND_SMALL_PARCEL',
            'carrier': {'name': 'UPS', 'alphaCode': 'UPSN'},
            'quote': {'cost': {'amount': 28.44, 'code': 'USD'}},
        })
        self.assertTrue(summary['can_purchase'])
        self.assertEqual(summary['block_reason'], '')
        self.assertEqual(summary['quote']['cost']['amount'], 28.44)

    def test_self_booked_option_is_kept_with_its_price_and_reason(self):
        summary = _fba_amazon_transport_summary({
            'transportationOptionId': 'to-2', 'shipmentId': 'sh-1',
            'shippingSolution': 'USE_YOUR_OWN_CARRIER', 'shippingMode': 'GROUND_SMALL_PARCEL',
            'carrier': {'name': 'Seller carrier'},
            'quote': {'cost': {'amount': 0, 'code': 'USD'}},
        })
        self.assertFalse(summary['can_purchase'])
        self.assertIn('Book this yourself', summary['block_reason'])
        self.assertEqual(summary['quote']['cost']['amount'], 0)

    def test_freight_and_precondition_reasons_name_the_blocker(self):
        freight = transport_option_block_reason(
            {'shippingSolution': 'AMAZON_PARTNERED_CARRIER', 'shippingMode': 'LTL_FREIGHT'},
            can_purchase=False,
        )
        self.assertIn('ltl freight', freight)
        blocked = transport_option_block_reason(
            {
                'shippingSolution': 'AMAZON_PARTNERED_CARRIER', 'shippingMode': 'GROUND_SMALL_PARCEL',
                'preconditions': ['PALLET_INFORMATION_REQUIRED'],
            },
            can_purchase=False,
        )
        self.assertIn('PALLET_INFORMATION_REQUIRED', blocked)
        self.assertEqual(
            transport_option_block_reason({'shippingSolution': 'AMAZON_PARTNERED_CARRIER'}, can_purchase=True),
            '',
        )


if __name__ == '__main__':
    unittest.main()
