import datetime
import unittest
from types import SimpleNamespace

from sweetshelves.fba_inventory import (
    _fba_session_item_payload,
)
from sweetshelves.fba_readiness import (
    _fba_catalog_measurement_reference_from_payload,
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


class FbaInboundHelpersTest(unittest.TestCase):
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
