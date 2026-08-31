import datetime
import unittest

from app import (
    _fba_inbound_activation_message,
    _fba_inbound_unavailable_skus,
    _fba_missing_prep_mskus,
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


if __name__ == "__main__":
    unittest.main()
