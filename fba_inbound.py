"""Pure helpers for the Sweet Shelves Amazon FBA inbound workflow.

The Flask routes and SP-API client live in ``app.py``.  This module deliberately
contains only validation and payload shaping so the irreversible Amazon calls
can be tested without credentials or network access.
"""

from __future__ import annotations

import datetime as _datetime
import copy
import math
import re as _re
from collections import defaultdict


class FbaInboundValidationError(ValueError):
    """A user-correctable problem in an inbound workflow payload."""


US_MARKETPLACE_ID = "ATVPDKIKX0DER"

# U.S. FBA shipment-carton rules effective June 20, 2025. Dimensions are
# compared after ordering the sides longest-to-shortest so a user cannot
# accidentally bypass (or trip) a limit by rotating a carton.
FBA_MAX_BOX_DIMENSIONS_IN = (36.0, 25.0, 25.0)
FBA_MAX_BOX_WEIGHT_LB = 50.0
FBA_JEWELRY_WATCH_MAX_BOX_WEIGHT_LB = 40.0


def _text(value, limit=500):
    return str(value or "").strip()[: max(1, int(limit))]


def _positive_int(value, field):
    if isinstance(value, bool):
        raise FbaInboundValidationError(f"{field} must be a positive whole number")
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise FbaInboundValidationError(f"{field} must be a positive whole number")
    if number <= 0:
        raise FbaInboundValidationError(f"{field} must be a positive whole number")
    return number


def _positive_float(value, field):
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        raise FbaInboundValidationError(f"{field} must be greater than zero")
    if not math.isfinite(number) or number <= 0:
        raise FbaInboundValidationError(f"{field} must be greater than zero")
    return round(number, 3)


def _as_bool(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def validate_box_specifications(box):
    """Validate a normalized carton against current U.S. FBA limits.

    Amazon permits the standard dimension/weight limits to be exceeded only
    when the carton contains one individual oversized unit. The exception is
    explicit because unit count alone cannot prove that Amazon classifies the
    catalog item as oversized.
    """
    box = box if isinstance(box, dict) else {}
    local_id = _text(box.get("local_id") or "Box", 40)
    dimensions = sorted(
        (
            _positive_float(box.get("length_in"), f"Length for {local_id}"),
            _positive_float(box.get("width_in"), f"Width for {local_id}"),
            _positive_float(box.get("height_in"), f"Height for {local_id}"),
        ),
        reverse=True,
    )
    weight = _positive_float(box.get("weight_lb"), f"Weight for {local_id}")
    contains_jewelry_or_watches = _as_bool(box.get("contains_jewelry_or_watches"))
    if contains_jewelry_or_watches and weight > FBA_JEWELRY_WATCH_MAX_BOX_WEIGHT_LB:
        raise FbaInboundValidationError(
            f"{local_id} contains jewelry or watches and cannot exceed 40 lb"
        )

    exceeds_dimensions = any(
        actual > maximum for actual, maximum in zip(dimensions, FBA_MAX_BOX_DIMENSIONS_IN)
    )
    exceeds_weight = weight > FBA_MAX_BOX_WEIGHT_LB
    uses_exception = _as_bool(box.get("single_oversize_exception"))
    unit_count = sum(
        int(item.get("quantity") or 0)
        for item in (box.get("contents") or [])
        if isinstance(item, dict)
    )
    if exceeds_dimensions or exceeds_weight:
        if not uses_exception:
            raise FbaInboundValidationError(
                f"{local_id} exceeds the standard U.S. FBA limit of 36 x 25 x 25 in and 50 lb; "
                "use another carton or mark the single oversized-unit exception"
            )
        if unit_count != 1:
            raise FbaInboundValidationError(
                f"{local_id} can use the oversized exception only when it contains exactly one unit"
            )
    elif uses_exception and unit_count != 1:
        raise FbaInboundValidationError(
            f"{local_id} is marked as a single oversized unit but contains {unit_count} units"
        )

    # Send canonical longest/middle/shortest sides to Amazon regardless of how
    # the carton was oriented when it was measured.
    box["length_in"], box["width_in"], box["height_in"] = dimensions
    box["weight_lb"] = weight
    box["single_oversize_exception"] = uses_exception
    box["contains_jewelry_or_watches"] = contains_jewelry_or_watches
    return box


def normalize_source_address(raw):
    raw = raw if isinstance(raw, dict) else {}
    result = {
        "name": _text(raw.get("name"), 50),
        "companyName": _text(raw.get("companyName") or raw.get("company_name"), 50),
        "addressLine1": _text(raw.get("addressLine1") or raw.get("address_line1"), 180),
        "addressLine2": _text(raw.get("addressLine2") or raw.get("address_line2"), 60),
        "city": _text(raw.get("city"), 30),
        "districtOrCounty": _text(raw.get("districtOrCounty") or raw.get("district_or_county"), 30),
        "stateOrProvinceCode": _text(
            raw.get("stateOrProvinceCode") or raw.get("state_or_province_code"), 30
        ).upper(),
        "postalCode": _text(raw.get("postalCode") or raw.get("postal_code"), 30),
        "countryCode": _text(raw.get("countryCode") or raw.get("country_code") or "US", 2).upper(),
        "phoneNumber": _text(raw.get("phoneNumber") or raw.get("phone_number"), 30),
        "email": _text(raw.get("email"), 100),
    }
    return {key: value for key, value in result.items() if value}


def missing_source_address_fields(raw, *, require_contact=False):
    address = normalize_source_address(raw)
    labels = {
        "name": "name",
        "addressLine1": "street address",
        "city": "city",
        "stateOrProvinceCode": "state",
        "postalCode": "postal code",
        "countryCode": "country",
    }
    missing = [label for key, label in labels.items() if not address.get(key)]
    if (
        address.get("countryCode") == "US"
        and address.get("stateOrProvinceCode")
        and len(address["stateOrProvinceCode"]) != 2
    ):
        missing.append("2-letter state code")
    if require_contact:
        if not address.get("phoneNumber"):
            missing.append("phone number")
        if not address.get("email"):
            missing.append("email")
    return missing


def _item_owners(raw, marketplace_id):
    fba = raw.get("fba") if isinstance(raw.get("fba"), dict) else {}
    guidance = fba.get("barcode_guidance") if isinstance(fba.get("barcode_guidance"), dict) else {}
    msku = _text(raw.get("msku") or raw.get("seller_sku"), 255)
    asin = _text(raw.get("asin"), 30).upper()
    guidance_status = (
        _text(guidance.get("status"), 40).lower()
        if amazon_barcode_guidance_is_authoritative(guidance, msku=msku, asin=asin)
        else "unavailable"
    )

    # Amazon ended US prep and labeling services in 2026.  A manufacturer
    # barcode can still use NONE for label owner; otherwise Sweet Shelves is
    # responsible for the FNSKU label.
    prep_owner = "SELLER"
    label_owner = "NONE" if guidance_status == "manufacturer_barcode" else "SELLER"
    if marketplace_id != US_MARKETPLACE_ID:
        requested_prep = _text(raw.get("prep_owner") or "SELLER", 20).upper()
        requested_label = _text(raw.get("label_owner") or label_owner, 20).upper()
        prep_owner = requested_prep if requested_prep in {"AMAZON", "SELLER", "NONE"} else "SELLER"
        label_owner = requested_label if requested_label in {"AMAZON", "SELLER", "NONE"} else label_owner

    # Items returned by Amazon after plan creation carry the accepted owner
    # values. Preserve those when building later carton payloads instead of
    # reverting to local defaults.
    amazon_prep_owner = _text(raw.get("prepOwner"), 20).upper()
    amazon_label_owner = _text(raw.get("labelOwner"), 20).upper()
    if amazon_prep_owner in {"AMAZON", "SELLER", "NONE"}:
        prep_owner = amazon_prep_owner
    if amazon_label_owner in {"AMAZON", "SELLER", "NONE"}:
        label_owner = amazon_label_owner
    return prep_owner, label_owner


_OWNER_REJECTION_RE = _re.compile(
    r"ERROR:\s*(?P<msku>.+?)\s+(?:does\s+not\s+require|requires).*?"
    r"(?P<field>prepOwner|labelOwner).*?Accepted\s+values:\s*\[(?P<accepted>[^\]]+)\]",
    _re.IGNORECASE,
)


def apply_owner_corrections_from_amazon_error(request_body, error):
    """Apply Amazon's explicit accepted owner values to a create-plan body."""
    request_body = request_body if isinstance(request_body, dict) else {}
    items = request_body.get("items") if isinstance(request_body.get("items"), list) else []
    if not items:
        return []

    corrections = []
    message = str(error or "")
    for match in _OWNER_REJECTION_RE.finditer(message):
        msku = _text(match.group("msku"), 255)
        field_raw = _text(match.group("field"), 20).casefold()
        field = "prepOwner" if field_raw == "prepowner" else "labelOwner"
        accepted = []
        for raw_value in match.group("accepted").split(","):
            value = _text(raw_value, 20).strip("'\"").upper()
            if value in {"NONE", "SELLER", "AMAZON"} and value not in accepted:
                accepted.append(value)
        if not msku or not accepted:
            continue

        item = next(
            (
                row for row in items
                if isinstance(row, dict)
                and _text(row.get("msku"), 255).casefold() == msku.casefold()
            ),
            None,
        )
        if item is None:
            continue
        current = _text(item.get(field), 20).upper()
        replacement = next((value for value in ("NONE", "SELLER", "AMAZON") if value in accepted), accepted[0])
        if replacement == current:
            continue
        item[field] = replacement
        corrections.append({
            "msku": msku,
            "field": field,
            "from": current,
            "to": replacement,
            "accepted": accepted,
        })
    return corrections


def amazon_barcode_guidance_is_authoritative(guidance, *, msku="", asin="", instruction=""):
    """Return True only for an exact, identifier-matched Amazon SP-API instruction."""
    guidance = guidance if isinstance(guidance, dict) else {}
    actual_instruction = _text(guidance.get("instruction"), 80).casefold()
    expected_instruction = _text(instruction, 80).casefold()
    if actual_instruction not in {"requiresfnskulabel", "canuseoriginalbarcode"}:
        return False
    if expected_instruction and actual_instruction != expected_instruction:
        return False
    expected_status = {
        "requiresfnskulabel": "amazon_barcode",
        "canuseoriginalbarcode": "manufacturer_barcode",
    }[actual_instruction]
    if (
        _text(guidance.get("status"), 40).casefold() != expected_status
        or guidance.get("checked") is not True
        or _text(guidance.get("source"), 40).casefold() != "amazon_sp_api"
    ):
        return False

    identifier_type = _text(guidance.get("identifier_type"), 30).casefold()
    identifier = _text(guidance.get("identifier"), 160)
    if identifier_type == "seller_sku":
        return bool(msku) and identifier.casefold() == _text(msku, 255).casefold()
    if identifier_type == "asin":
        return bool(asin) and identifier.casefold() == _text(asin, 30).casefold()
    return False


def build_create_plan_request(session, source_address, marketplace_id):
    """Build and validate the body accepted by createInboundPlan."""
    session = session if isinstance(session, dict) else {}
    items = session.get("items") if isinstance(session.get("items"), list) else []
    if not items:
        raise FbaInboundValidationError("Add at least one item before creating the Amazon plan")
    address = normalize_source_address(source_address)
    missing = missing_source_address_fields(address)
    if missing:
        raise FbaInboundValidationError("Complete the ship-from " + ", ".join(missing))

    grouped = {}
    missing_skus = []
    for raw in items:
        raw = raw if isinstance(raw, dict) else {}
        msku = _text(raw.get("seller_sku") or raw.get("msku"), 255)
        barcode = _text(raw.get("barcode_display") or raw.get("barcode"), 160)
        if not msku:
            missing_skus.append(barcode or "unknown item")
            continue
        quantity = _positive_int(raw.get("quantity"), f"Quantity for {barcode or msku}")
        expiration = _text(raw.get("expiration_date") or raw.get("expiration"), 10)
        if expiration:
            try:
                _datetime.date.fromisoformat(expiration)
            except ValueError:
                raise FbaInboundValidationError(f"Expiration for {barcode or msku} must use YYYY-MM-DD")
        prep_owner, label_owner = _item_owners(raw, marketplace_id)
        group_key = (msku.casefold(), expiration, prep_owner, label_owner)
        if group_key not in grouped:
            grouped[group_key] = {
                "msku": msku,
                "quantity": 0,
                "prepOwner": prep_owner,
                "labelOwner": label_owner,
            }
            if expiration:
                grouped[group_key]["expiration"] = expiration
        grouped[group_key]["quantity"] += quantity

    if missing_skus:
        preview = ", ".join(missing_skus[:8])
        suffix = "…" if len(missing_skus) > 8 else ""
        raise FbaInboundValidationError(
            f"Match every item to an Amazon seller SKU first: {preview}{suffix}"
        )

    name = _text(session.get("batch_name") or session.get("session_name") or "Sweet Shelves FBA", 40)
    return {
        "name": name,
        "sourceAddress": address,
        "destinationMarketplaces": [_text(marketplace_id, 20)],
        "items": list(grouped.values()),
    }


def normalize_box_drafts(raw_boxes, plan_items=None, *, allow_incomplete=False):
    """Validate client-side cartons and return a compact persistent shape."""
    rows = raw_boxes if isinstance(raw_boxes, list) else []
    if len(rows) > 500:
        raise FbaInboundValidationError("A session cannot contain more than 500 boxes")

    allowed_mskus = None
    if isinstance(plan_items, list):
        allowed_mskus = {
            _text(item.get("msku") or item.get("seller_sku"), 255).casefold()
            for item in plan_items
            if isinstance(item, dict) and _text(item.get("msku") or item.get("seller_sku"), 255)
        }

    boxes = []
    seen_ids = set()
    for index, raw in enumerate(rows, 1):
        raw = raw if isinstance(raw, dict) else {}
        local_id = _text(raw.get("local_id") or raw.get("box_number") or f"BOX-{index}", 40).upper()
        if not local_id:
            local_id = f"BOX-{index}"
        key = local_id.casefold()
        if key in seen_ids:
            raise FbaInboundValidationError(f"Box name {local_id} is duplicated")
        seen_ids.add(key)

        packing_group_id = _text(raw.get("packing_group_id"), 38)
        if not packing_group_id:
            raise FbaInboundValidationError(f"Choose a packing group for {local_id}")

        contents = []
        contents_by_msku = defaultdict(int)
        for content in raw.get("contents") if isinstance(raw.get("contents"), list) else []:
            content = content if isinstance(content, dict) else {}
            msku = _text(content.get("msku") or content.get("seller_sku"), 255)
            if not msku:
                continue
            if allowed_mskus is not None and msku.casefold() not in allowed_mskus:
                raise FbaInboundValidationError(f"{msku} in {local_id} is not part of this Amazon plan")
            contents_by_msku[msku] += _positive_int(
                content.get("quantity") or content.get("quantityInBox"),
                f"Packed quantity for {msku}",
            )
        for msku, quantity in contents_by_msku.items():
            contents.append({"msku": msku, "quantity": quantity})

        def measurement(value, label):
            if allow_incomplete and (value is None or not str(value).strip()):
                return None
            return _positive_float(value, label)

        box = {
            "local_id": local_id,
            "packing_group_id": packing_group_id,
            "length_in": measurement(raw.get("length_in"), f"Length for {local_id}"),
            "width_in": measurement(raw.get("width_in"), f"Width for {local_id}"),
            "height_in": measurement(raw.get("height_in"), f"Height for {local_id}"),
            "weight_lb": measurement(raw.get("weight_lb"), f"Weight for {local_id}"),
            "single_oversize_exception": _as_bool(raw.get("single_oversize_exception")),
            "contains_jewelry_or_watches": _as_bool(raw.get("contains_jewelry_or_watches")),
            "contents": contents,
        }
        if not allow_incomplete:
            validate_box_specifications(box)
        boxes.append(box)
    return boxes


def validate_box_totals(boxes, plan_items):
    expected = defaultdict(int)
    for item in plan_items if isinstance(plan_items, list) else []:
        if not isinstance(item, dict):
            continue
        msku = _text(item.get("msku") or item.get("seller_sku"), 255)
        if msku:
            expected[msku] += _positive_int(item.get("quantity"), f"Quantity for {msku}")
    packed = defaultdict(int)
    for box in boxes:
        for item in box.get("contents") or []:
            packed[_text(item.get("msku"), 255)] += int(item.get("quantity") or 0)
    problems = []
    for msku in sorted(set(expected) | set(packed), key=str.casefold):
        if expected[msku] != packed[msku]:
            problems.append({"msku": msku, "expected": expected[msku], "packed": packed[msku]})
    if problems:
        detail = ", ".join(
            f"{row['msku']} {row['packed']}/{row['expected']}" for row in problems[:10]
        )
        raise FbaInboundValidationError("Box counts do not match the plan: " + detail)
    return {"expected_units": sum(expected.values()), "packed_units": sum(packed.values())}


def reduce_missing_plan_quantity(items, msku, quantity, *, packed_quantity=0, barcode_key=""):
    """Remove only units that have not been physically scanned into a carton.

    The inbound plan groups quantities by MSKU, while the prep page can retain
    more than one barcode row for that MSKU.  Prefer the row selected by the
    operator, then consume any remaining reduction from sibling rows.
    """
    requested_msku = _text(msku, 255)
    remove_quantity = _positive_int(quantity, "Missing quantity")
    packed_quantity = max(0, int(packed_quantity or 0))
    preferred_barcode_key = _text(barcode_key, 160).casefold()
    rows = copy.deepcopy(items if isinstance(items, list) else [])

    matching_indexes = [
        index for index, row in enumerate(rows)
        if isinstance(row, dict)
        and _text(row.get("seller_sku") or row.get("msku"), 255).casefold()
            == requested_msku.casefold()
    ]
    if not requested_msku or not matching_indexes:
        raise FbaInboundValidationError("That Seller SKU is not in the saved Amazon plan")

    planned_quantity = sum(int(rows[index].get("quantity") or 0) for index in matching_indexes)
    removable_quantity = max(0, planned_quantity - packed_quantity)
    if remove_quantity > removable_quantity:
        raise FbaInboundValidationError(
            f"Only {removable_quantity} unpacked unit(s) of {requested_msku} can be removed; "
            f"{packed_quantity} unit(s) already have saved carton scans"
        )
    if remove_quantity >= planned_quantity:
        if packed_quantity:
            raise FbaInboundValidationError(
                f"{requested_msku} cannot be removed completely because {packed_quantity} unit(s) are packed"
            )

    def row_priority(index):
        row_key = _text(rows[index].get("barcode_key") or rows[index].get("barcode"), 160).casefold()
        return (0 if preferred_barcode_key and row_key == preferred_barcode_key else 1, index)

    remaining = remove_quantity
    changed_rows = []
    for index in sorted(matching_indexes, key=row_priority):
        if remaining <= 0:
            break
        row = rows[index]
        old_quantity = max(0, int(row.get("quantity") or 0))
        taken = min(old_quantity, remaining)
        new_quantity = old_quantity - taken
        remaining -= taken
        row["quantity"] = new_quantity
        if "planned_fba_quantity" in row:
            row["planned_fba_quantity"] = max(
                0, min(new_quantity, int(row.get("planned_fba_quantity") or 0) - taken)
            )
        changed_rows.append({
            "barcode": _text(row.get("barcode_display") or row.get("barcode"), 160),
            "old_quantity": old_quantity,
            "new_quantity": new_quantity,
        })

    rows = [row for row in rows if not (
        isinstance(row, dict)
        and _text(row.get("seller_sku") or row.get("msku"), 255).casefold()
            == requested_msku.casefold()
        and int(row.get("quantity") or 0) <= 0
    )]
    return rows, {
        "msku": requested_msku,
        "removed_quantity": remove_quantity,
        "old_quantity": planned_quantity,
        "new_quantity": planned_quantity - remove_quantity,
        "packed_quantity": packed_quantity,
        "changed_rows": changed_rows,
    }


def remap_recovery_boxes(saved_boxes, packing_groups):
    """Assign preserved cartons to replacement-plan packing groups.

    A carton is compatible only when all of its saved physical scans belong to
    one replacement group. Empty cartons are retained and assigned to the
    first current group; they contain no physical work that can be invalidated.
    """
    group_by_msku = {}
    group_ids = []
    for group in packing_groups if isinstance(packing_groups, list) else []:
        if not isinstance(group, dict):
            continue
        group_id = _text(group.get("packing_group_id") or group.get("packingGroupId"), 38)
        if not group_id:
            continue
        group_ids.append(group_id)
        for item in group.get("items") if isinstance(group.get("items"), list) else []:
            if not isinstance(item, dict):
                continue
            item_msku = _text(item.get("msku") or item.get("seller_sku"), 255).casefold()
            if item_msku:
                group_by_msku[item_msku] = group_id

    restored = copy.deepcopy(saved_boxes if isinstance(saved_boxes, list) else [])
    used_ids = {
        _text(box.get("local_id"), 40).upper()
        for box in restored if isinstance(box, dict) and _text(box.get("local_id"), 40)
    }

    def next_box_id():
        number = 1
        while f"BOX-{number:02d}" in used_ids:
            number += 1
        value = f"BOX-{number:02d}"
        used_ids.add(value)
        return value

    conflicts = []
    additional_boxes = []
    for box in list(restored):
        if not isinstance(box, dict):
            continue
        carton_groups = defaultdict(list)
        unknown_mskus = []
        for item in box.get("contents") if isinstance(box.get("contents"), list) else []:
            if not isinstance(item, dict) or int(item.get("quantity") or 0) <= 0:
                continue
            item_msku = _text(item.get("msku") or item.get("seller_sku"), 255)
            group_id = group_by_msku.get(item_msku.casefold())
            if group_id:
                carton_groups[group_id].append(copy.deepcopy(item))
            else:
                unknown_mskus.append(item_msku or "unknown item")
        if unknown_mskus:
            conflicts.append({
                "box_id": _text(box.get("local_id") or "Box", 40),
                "packing_group_ids": sorted(carton_groups),
                "unknown_mskus": unknown_mskus,
                "moves": [],
            })
            continue
        if len(carton_groups) > 1:
            source_box_id = _text(box.get("local_id") or "Box", 40).upper()
            partitions = sorted(
                carton_groups.items(),
                key=lambda row: (-sum(int(item.get("quantity") or 0) for item in row[1]), row[0]),
            )
            keep_group, keep_contents = partitions[0]
            box["packing_group_id"] = keep_group
            box["contents"] = keep_contents
            box["weight_lb"] = None
            moves = []
            split_box_ids = [source_box_id]
            for group_id, contents in partitions[1:]:
                new_box_id = next_box_id()
                additional_boxes.append({
                    "local_id": new_box_id,
                    "packing_group_id": group_id,
                    "length_in": None,
                    "width_in": None,
                    "height_in": None,
                    "weight_lb": None,
                    "single_oversize_exception": False,
                    "contains_jewelry_or_watches": False,
                    "contents": contents,
                })
                split_box_ids.append(new_box_id)
                for content in contents:
                    moves.append({
                        "msku": _text(content.get("msku") or content.get("seller_sku"), 255),
                        "from_box_id": source_box_id,
                        "to_box_id": new_box_id,
                        "packing_group_id": group_id,
                    })
            conflicts.append({
                "box_id": source_box_id,
                "packing_group_ids": sorted(carton_groups),
                "unknown_mskus": [],
                "moves": moves,
                "split_box_ids": split_box_ids,
            })
        elif carton_groups:
            box["packing_group_id"] = next(iter(carton_groups))
        elif group_ids:
            box["packing_group_id"] = group_ids[0]
    restored.extend(additional_boxes)
    return restored, conflicts


def build_set_packing_request(boxes, plan_items, *, marketplace_id=US_MARKETPLACE_ID):
    boxes = normalize_box_drafts(boxes, plan_items)
    if not boxes:
        raise FbaInboundValidationError("Add at least one box")
    validate_box_totals(boxes, plan_items)

    item_by_msku = {}
    for item in plan_items:
        if not isinstance(item, dict):
            continue
        msku = _text(item.get("msku") or item.get("seller_sku"), 255)
        if msku:
            item_by_msku[msku.casefold()] = item

    groupings = {}
    for box in boxes:
        group_id = box["packing_group_id"]
        grouping = groupings.setdefault(group_id, {"packingGroupId": group_id, "boxes": []})
        api_items = []
        for content in box["contents"]:
            source = item_by_msku.get(content["msku"].casefold(), {})
            prep_owner, label_owner = _item_owners(source, marketplace_id)
            api_item = {
                "msku": content["msku"],
                "quantity": content["quantity"],
                "prepOwner": prep_owner,
                "labelOwner": label_owner,
            }
            expiration = _text(source.get("expiration") or source.get("expiration_date"), 10)
            if expiration:
                api_item["expiration"] = expiration
            api_items.append(api_item)
        grouping["boxes"].append({
            "contentInformationSource": "BOX_CONTENT_PROVIDED",
            "dimensions": {
                "unitOfMeasurement": "IN",
                "length": box["length_in"],
                "width": box["width_in"],
                "height": box["height_in"],
            },
            "weight": {"unit": "LB", "value": box["weight_lb"]},
            "quantity": 1,
            "items": api_items,
        })
    return {"packageGroupings": list(groupings.values())}, boxes


def build_transportation_request(placement_option_id, shipment_ids, ready_date, contact):
    placement_option_id = _text(placement_option_id, 38)
    if not placement_option_id:
        raise FbaInboundValidationError("Select a placement option first")
    try:
        parsed_date = _datetime.date.fromisoformat(_text(ready_date, 10))
    except ValueError:
        raise FbaInboundValidationError("Ready-to-ship date must use YYYY-MM-DD")
    if parsed_date < _datetime.date.today():
        raise FbaInboundValidationError("Ready-to-ship date cannot be in the past")
    contact = normalize_source_address(contact)
    missing = missing_source_address_fields(contact, require_contact=True)
    if missing:
        raise FbaInboundValidationError("Complete the ship-from " + ", ".join(missing))
    shipment_ids = [_text(value, 38) for value in shipment_ids or [] if _text(value, 38)]
    if not shipment_ids:
        raise FbaInboundValidationError("Amazon did not return any shipments for this placement")
    now_utc = _datetime.datetime.now(_datetime.timezone.utc)
    if parsed_date == _datetime.date.today():
        # A date-only UI value previously became midnight UTC, which Amazon
        # rejects as soon as that day has started. Keep today's selection but
        # send a timestamp safely ahead of the current instant.
        start_at = now_utc + _datetime.timedelta(minutes=5)
    else:
        start_at = _datetime.datetime.combine(
            parsed_date, _datetime.time.min, tzinfo=_datetime.timezone.utc
        )
    start = start_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    contact_information = {
        "name": contact["name"],
        "phoneNumber": contact["phoneNumber"],
        "email": contact["email"],
    }
    return {
        "placementOptionId": placement_option_id,
        "shipmentTransportationConfigurations": [
            {
                "shipmentId": shipment_id,
                "readyToShipWindow": {"start": start},
                "contactInformation": contact_information,
            }
            for shipment_id in shipment_ids
        ],
    }


def build_item_label_job(plan_items, session_items, msku, *, authoritative_guidance=None):
    """Validate an automatic FNSKU print against Amazon and session data.

    The browser supplies only the MSKU.  The printable FNSKU always comes from
    Amazon's saved inbound-plan item response so an arbitrary client value can
    never be sent to the printer as an Amazon product label.
    """
    requested_msku = _text(msku, 255)
    if not requested_msku:
        raise FbaInboundValidationError("Seller SKU is required for the Amazon label")

    plan_item = next((
        row for row in plan_items if isinstance(row, dict)
        and _text(row.get("msku") or row.get("seller_sku"), 255).casefold()
            == requested_msku.casefold()
    ), None)
    if not plan_item:
        raise FbaInboundValidationError("Amazon has not returned this SKU in the inbound plan")

    session_item = next((
        row for row in session_items if isinstance(row, dict)
        and _text(row.get("seller_sku") or row.get("msku"), 255).casefold()
            == requested_msku.casefold()
    ), None)
    if not session_item:
        raise FbaInboundValidationError("This seller SKU is not in the saved prep session")

    fba = session_item.get("fba") if isinstance(session_item.get("fba"), dict) else {}
    saved_guidance = fba.get("barcode_guidance") if isinstance(fba.get("barcode_guidance"), dict) else {}
    guidance = authoritative_guidance if isinstance(authoritative_guidance, dict) else saved_guidance
    prep_instructions = plan_item.get("prepInstructions") or plan_item.get("prep_instructions") or []
    plan_requires_label = any(
        isinstance(instruction, dict)
        and _text(instruction.get("prepType") or instruction.get("prep_type"), 80).upper()
            == "ITEM_LABELING"
        and _text(instruction.get("prepOwner") or instruction.get("prep_owner"), 20).upper()
            in {"", "SELLER"}
        for instruction in prep_instructions
    )
    guidance_requires_label = amazon_barcode_guidance_is_authoritative(
        guidance,
        msku=requested_msku,
        asin=_text(plan_item.get("asin"), 30).upper(),
        instruction="RequiresFNSKULabel",
    )
    if not guidance_requires_label and not plan_requires_label:
        raise FbaInboundValidationError("Amazon has not explicitly required an FNSKU label for this item")

    fnsku = str(plan_item.get("fnsku") or "").strip().upper()
    if len(fnsku) != 10 or not fnsku.isalnum():
        raise FbaInboundValidationError("Amazon has not assigned a printable FNSKU yet; refresh the plan")

    label_owner = _text(plan_item.get("labelOwner") or plan_item.get("label_owner"), 20).upper()
    if label_owner and label_owner != "SELLER":
        raise FbaInboundValidationError("Amazon does not show this item as seller-labeled")

    return {
        "msku": _text(plan_item.get("msku") or requested_msku, 255),
        "fnsku": fnsku,
        "title": _text(session_item.get("title") or plan_item.get("productName") or requested_msku, 180),
        "condition": _text(session_item.get("condition") or "New", 40),
    }


def summarize_money(rows):
    total_by_currency = defaultdict(float)
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        value = row.get("value") if isinstance(row.get("value"), dict) else row.get("amount")
        value = value if isinstance(value, dict) else {}
        code = _text(value.get("code") or value.get("currencyCode") or "USD", 8).upper()
        try:
            total_by_currency[code] += float(value.get("amount") or value.get("value") or 0)
        except (TypeError, ValueError):
            continue
    return [
        {"currency": code, "amount": round(amount, 2)}
        for code, amount in sorted(total_by_currency.items())
    ]


def option_state(option):
    return _text((option or {}).get("status"), 40).upper()


# A plan or shipment Amazon reports in one of these states can never ship again.
# Restoring means rebuilding a replacement plan, not editing the dead one.
FBA_DEAD_SHIPMENT_STATUSES = frozenset({
    "CANCELLED", "CANCELED", "DELETED", "VOID", "VOIDED", "ABANDONED", "CLOSED",
})
FBA_DEAD_PLAN_STATUSES = frozenset({"VOIDED", "VOID", "CANCELLED", "CANCELED", "DELETED"})


def detect_amazon_cancellation(plan, shipments):
    """Describe an inbound plan that was cancelled outside this app.

    Cancelling shipping in Seller Central kills the Amazon plan while the
    physical work — packed cartons, carton scans, printed FNSKU labels — stays
    valid.  Returns ``None`` while the plan is still usable, otherwise a summary
    whose ``restorable`` flag says the whole plan is dead and may be rebuilt.  A
    partially cancelled plan is reported but never auto-restored, because its
    live shipments still belong to Amazon.
    """
    plan = plan if isinstance(plan, dict) else {}
    rows = [row for row in (shipments or []) if isinstance(row, dict)]
    plan_status = _text(plan.get("status"), 40).upper()
    plan_dead = plan_status in FBA_DEAD_PLAN_STATUSES
    cancelled = []
    live = []
    for row in rows:
        status = _text(row.get("status"), 40).upper()
        entry = {
            "shipmentId": _text(row.get("shipmentId"), 38),
            "shipmentConfirmationId": _text(row.get("shipmentConfirmationId"), 40),
            "status": status,
            "warehouseId": _text((row.get("destination") or {}).get("warehouseId"), 40).upper(),
        }
        (cancelled if status in FBA_DEAD_SHIPMENT_STATUSES else live).append(entry)
    if not plan_dead and not cancelled:
        return None
    every_shipment_dead = bool(rows) and not live
    destinations = []
    for entry in cancelled + live:
        if entry["warehouseId"] and entry["warehouseId"] not in destinations:
            destinations.append(entry["warehouseId"])
    return {
        "plan_status": plan_status,
        "plan_cancelled": plan_dead,
        "cancelled_shipments": cancelled,
        "live_shipments": live,
        "destinations": destinations,
        # A dead plan is restorable even with no shipments: nothing can ship from it.
        "restorable": bool(plan_dead or every_shipment_dead),
        "partial": bool(cancelled and live and not plan_dead),
    }


def transport_option_block_reason(option, *, can_purchase=None):
    """Explain in one sentence why an option cannot be bought inside this app."""
    option = option if isinstance(option, dict) else {}
    if can_purchase is None:
        can_purchase = False
    if can_purchase:
        return ""
    solution = _text(option.get("shippingSolution"), 80).upper()
    mode = _text(option.get("shippingMode"), 80).upper()
    preconditions = [
        _text(value, 120) for value in (option.get("preconditions") or []) if _text(value, 120)
    ]
    if solution and solution != "AMAZON_PARTNERED_CARRIER":
        return "Book this yourself with the carrier — Amazon does not sell it through this API."
    if mode and mode != "GROUND_SMALL_PARCEL":
        readable = mode.replace("_", " ").lower()
        return f"Amazon partnered {readable} is arranged in Seller Central, not here."
    if preconditions:
        return "Amazon requires first: " + ", ".join(preconditions) + "."
    return "Amazon did not return a confirmed price for this option."
