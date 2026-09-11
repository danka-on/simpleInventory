"""Carton freight preflight: surcharge cliffs, density, mode and tiny SKUs.

These checks are pure arithmetic on submitted carton measurements, so they are
exercised directly rather than through the inbound plan. The cellar 3 case below
is a real shipment: 11 cartons that clear every carrier cliff yet bill at nearly
three times their scale weight, which is the case the density check exists for.
"""
import unittest

from sweetshelves.fba_shipments import (
    _FBA_DENSITY_BREAK_EVEN_LB_FT3,
    _fba_carton_freight_findings,
    _fba_carton_geometry,
)


def box(local_id, length, width, height, weight, contents=None):
    return {
        'local_id': local_id, 'length_in': length, 'width_in': width, 'height_in': height,
        'weight_lb': weight,
        'contents': contents if contents is not None else [{'msku': 'M-' + local_id, 'quantity': 10}],
    }


def codes(findings):
    return {row['code'] for row in findings}


def by_code(findings, code):
    return next(row for row in findings if row['code'] == code)


# The real cellar 3 cartons, 122 units over 11 boxes at 4.3 lb/ft3.
CELLAR3 = [
    box('BOX-01', 34, 22, 18, 33.0, [{'msku': 'AF-F499-GXGK', 'quantity': 13}]),
    box('BOX-02', 34, 22, 18, 30.0, [{'msku': 'R4-K6ZF-1ATA', 'quantity': 7},
                                     {'msku': 'AD-DXVX-MFDL', 'quantity': 1},
                                     {'msku': '6D-95TR-7EMI', 'quantity': 1}]),
    box('BOX-03', 34, 22, 18, 31.3, [{'msku': 'AF-F499-GXGK', 'quantity': 7},
                                     {'msku': 'XK-PQ59-TA3O', 'quantity': 5}]),
    box('BOX-04', 34, 22, 18.5, 31.3, [{'msku': 'AF-F499-GXGK', 'quantity': 5},
                                       {'msku': 'XK-PQ59-TA3O', 'quantity': 7}]),
    box('BOX-05', 23, 20, 18, 18.2, [{'msku': 'XK-PQ59-TA3O', 'quantity': 5},
                                     {'msku': 'U5-0FBA-W0XU', 'quantity': 2}]),
    box('BOX-06', 24, 15, 15, 28.2, [{'msku': 'OW-BUK2-TVB7', 'quantity': 9},
                                     {'msku': 'CO-LWUC-322H', 'quantity': 1}]),
    box('BOX-08', 34, 22, 18, 33.0, [{'msku': 'R4-K6ZF-1ATA', 'quantity': 10},
                                     {'msku': 'AD-DXVX-MFDL', 'quantity': 1}]),
    box('BOX-09', 34, 22, 19, 33.0, [{'msku': 'AD-DXVX-MFDL', 'quantity': 4},
                                     {'msku': 'R4-K6ZF-1ATA', 'quantity': 8}]),
    box('BOX-10', 36, 22, 18, 36.4, [{'msku': 'AD-DXVX-MFDL', 'quantity': 12}]),
    box('BOX-11', 36, 22, 19, 37.0, [{'msku': '6D-95TR-7EMI', 'quantity': 9},
                                     {'msku': 'AD-DXVX-MFDL', 'quantity': 3}]),
    box('BOX-12', 34, 23, 19, 37.0, [{'msku': '6D-95TR-7EMI', 'quantity': 12}]),
]


class CartonGeometryTests(unittest.TestCase):
    def test_sides_are_sorted_longest_first_regardless_of_entry_order(self):
        # Girth and the handling rules are defined on longest/second-longest, not
        # on whichever field the operator happened to type a number into.
        flat = _fba_carton_geometry(box('B1', 18, 36, 22, 30.0))
        tall = _fba_carton_geometry(box('B2', 36, 22, 18, 30.0))
        self.assertEqual(flat['sides'], [36, 22, 18])
        self.assertEqual(flat['girth'], tall['girth'])

    def test_girth_is_longest_plus_twice_the_other_two(self):
        self.assertEqual(_fba_carton_geometry(box('B1', 36, 22, 18, 30.0))['girth'], 116)

    def test_unmeasured_carton_is_skipped_rather_than_called_zero_density(self):
        self.assertIsNone(_fba_carton_geometry(box('B1', 0, 0, 0, 0)))
        self.assertIsNone(_fba_carton_geometry(box('B1', 34, 22, 18, 0)))
        self.assertIsNone(_fba_carton_geometry({'local_id': 'B1'}))
        self.assertIsNone(_fba_carton_geometry('not a box'))

    def test_non_numeric_measurements_do_not_raise(self):
        self.assertIsNone(_fba_carton_geometry(box('B1', 'wide', 22, 18, 30.0)))


class CartonCliffTests(unittest.TestCase):
    def test_large_package_fires_above_130_girth_and_not_at_it(self):
        # 36 + 2*(24+23) == 130 exactly, which is inside the limit.
        self.assertNotIn('carton_large_package', codes(_fba_carton_freight_findings([box('B1', 36, 24, 23, 40)])))
        self.assertIn('carton_large_package', codes(_fba_carton_freight_findings([box('B1', 36, 24, 24, 40)])))

    def test_girth_margin_warns_only_in_the_band_below_the_limit(self):
        self.assertIn('carton_girth_margin', codes(_fba_carton_freight_findings([box('B1', 36, 24, 21, 40)])))
        self.assertNotIn('carton_girth_margin', codes(_fba_carton_freight_findings([box('B1', 34, 22, 18, 40)])))
        # Once it is over the limit the surcharge finding replaces the heads-up.
        over = codes(_fba_carton_freight_findings([box('B1', 36, 24, 24, 40)]))
        self.assertNotIn('carton_girth_margin', over)

    def test_additional_handling_fires_on_either_side_independently(self):
        self.assertIn('carton_additional_handling',
                      codes(_fba_carton_freight_findings([box('B1', 50, 10, 8, 40)])))
        self.assertIn('carton_additional_handling',
                      codes(_fba_carton_freight_findings([box('B1', 34, 31, 8, 40)])))
        self.assertNotIn('carton_additional_handling',
                         codes(_fba_carton_freight_findings([box('B1', 36, 22, 18, 40)])))

    def test_overweight_fires_above_50_lb_and_names_mech_lift_above_100(self):
        self.assertNotIn('carton_overweight', codes(_fba_carton_freight_findings([box('B1', 24, 18, 12, 50)])))
        team = _fba_carton_freight_findings([box('B1', 24, 18, 12, 60)])
        self.assertIn('carton_overweight', codes(team))
        self.assertIn('team-lift', by_code(team, 'carton_overweight')['message'])
        self.assertNotIn('mechanical-lift', by_code(team, 'carton_overweight')['message'])
        mech = _fba_carton_freight_findings([box('B1', 24, 18, 12, 120)])
        self.assertIn('mechanical-lift', by_code(mech, 'carton_overweight')['message'])

    def test_one_finding_per_rule_listing_every_carton_that_broke_it(self):
        # Six oversized cartons must not produce six rows of the same advice.
        findings = _fba_carton_freight_findings([box('B%d' % index, 36, 24, 24, 40) for index in range(6)])
        large = [row for row in findings if row['code'] == 'carton_large_package']
        self.assertEqual(len(large), 1)
        self.assertEqual(len(large[0]['box_ids']), 6)

    def test_cellar_three_clears_every_cliff(self):
        # The real shipment trips no dimension or weight rule; only density.
        findings = codes(_fba_carton_freight_findings(CELLAR3))
        self.assertNotIn('carton_large_package', findings)
        self.assertNotIn('carton_additional_handling', findings)
        self.assertNotIn('carton_overweight', findings)
        self.assertNotIn('carton_girth_margin', findings)
        self.assertIn('carton_low_density', findings)


class CartonDensityTests(unittest.TestCase):
    def test_break_even_is_the_divisor_expressed_in_pounds_per_cubic_foot(self):
        self.assertAlmostEqual(_FBA_DENSITY_BREAK_EVEN_LB_FT3, 1728.0 / 139.0, places=6)

    def test_dense_freight_is_billed_on_weight_and_raises_nothing(self):
        # 24x18x12 is 3.0 ft3; 60 lb is 20 lb/ft3, well above break-even.
        self.assertNotIn('carton_low_density', codes(_fba_carton_freight_findings([box('B1', 24, 18, 12, 60)])))

    def test_light_bulky_freight_reports_the_dim_multiplier(self):
        findings = _fba_carton_freight_findings(CELLAR3)
        row = by_code(findings, 'carton_low_density')
        self.assertIn('4.3 lb/ft3', row['title'])
        self.assertIn('2.9x', row['message'])
        self.assertIn('348 lb actual', row['message'])
        # Splitting is cube-neutral, so the advice must not suggest it.
        self.assertIn('Splitting cartons will not help', row['message'])

    def test_density_names_the_loosest_cartons_to_repack_first(self):
        row = by_code(_fba_carton_freight_findings(CELLAR3), 'carton_low_density')
        self.assertEqual(len(row['box_ids']), 3)
        self.assertIn('BOX-05', row['box_ids'])
        self.assertNotIn('BOX-06', row['box_ids'])


class CartonShipmentAdviceTests(unittest.TestCase):
    def test_ltl_is_suggested_at_about_a_pallet(self):
        self.assertIn('carton_consider_ltl', codes(_fba_carton_freight_findings(CELLAR3)))
        self.assertNotIn('carton_consider_ltl',
                         codes(_fba_carton_freight_findings([box('B1', 24, 18, 12, 40)])))

    def test_ltl_is_suggested_on_weight_alone_when_freight_is_dense(self):
        dense = [box('B%d' % index, 24, 18, 12, 45) for index in range(8)]
        findings = codes(_fba_carton_freight_findings(dense))
        self.assertIn('carton_consider_ltl', findings)
        self.assertNotIn('carton_low_density', findings)

    def test_tiny_quantity_skus_are_named(self):
        row = by_code(_fba_carton_freight_findings(CELLAR3), 'carton_straggler_skus')
        self.assertIn('CO-LWUC-322H x1', row['message'])
        self.assertIn('U5-0FBA-W0XU x2', row['message'])
        self.assertNotIn('AF-F499-GXGK', row['message'])

    def test_an_all_singleton_shipment_is_a_deliberate_top_up_not_a_warning(self):
        tiny = [box('B1', 20, 16, 12, 30, [{'msku': 'M1', 'quantity': 1}, {'msku': 'M2', 'quantity': 2}])]
        self.assertNotIn('carton_straggler_skus', codes(_fba_carton_freight_findings(tiny)))


class CartonFindingContractTests(unittest.TestCase):
    def test_nothing_here_can_ever_block_a_shipment(self):
        # An expensive carton is still a carton Amazon accepts. Only a dead
        # listing may gate the placement step.
        awful = [box('B1', 60, 40, 36, 200, [{'msku': 'M1', 'quantity': 1}])]
        findings = _fba_carton_freight_findings(awful)
        self.assertTrue(findings)
        self.assertTrue(all(row['severity'] == 'warning' for row in findings))

    def test_findings_carry_the_fields_the_plan_health_panel_renders(self):
        for row in _fba_carton_freight_findings(CELLAR3):
            self.assertEqual(row['msku'], '')       # not MSKU-scoped
            self.assertTrue(row['title'])           # so the panel has a heading
            self.assertTrue(row['message'])
            self.assertEqual(row['quantity'], 0)    # no remove-and-rebuild button
            self.assertIsInstance(row['box_ids'], list)

    def test_empty_and_malformed_input_is_quiet(self):
        self.assertEqual(_fba_carton_freight_findings([]), [])
        self.assertEqual(_fba_carton_freight_findings(None), [])
        self.assertEqual(_fba_carton_freight_findings('boxes'), [])
        self.assertEqual(_fba_carton_freight_findings([box('B1', 0, 0, 0, 0)]), [])
        self.assertEqual(_fba_carton_freight_findings([{'length_in': 34, 'width_in': 22,
                                                       'height_in': 18, 'weight_lb': 30}]), [])


class CartonLimitsMatchTheUiTests(unittest.TestCase):
    """The Pack step re-implements these thresholds in JavaScript to give live
    feedback while cartons are still open. Two copies can drift, so pin them."""

    def test_js_carton_limits_match_the_python_constants(self):
        from pathlib import Path
        import re

        from sweetshelves import fba_shipments

        template = (Path(__file__).parent / 'templates' / 'fba_prep.html').read_text(encoding='utf-8')
        block = re.search(r'const CARTON_LIMITS=\{(.*?)\};', template, re.S)
        self.assertIsNotNone(block, 'CARTON_LIMITS not found in fba_prep.html')
        js = {key: float(value) for key, value in re.findall(r'(\w+):([\d.]+)', block.group(1))}
        expected = {
            'dimDivisor': fba_shipments._FBA_DIM_DIVISOR,
            'largePackageGirthIn': fba_shipments._FBA_LARGE_PACKAGE_GIRTH_IN,
            'largePackageMinBillableLb': fba_shipments._FBA_LARGE_PACKAGE_MIN_BILLABLE_LB,
            'girthMarginIn': fba_shipments._FBA_GIRTH_MARGIN_IN,
            'additionalHandlingLongestIn': fba_shipments._FBA_ADDITIONAL_HANDLING_LONGEST_IN,
            'additionalHandlingSecondIn': fba_shipments._FBA_ADDITIONAL_HANDLING_SECOND_IN,
            'additionalHandlingWeightLb': fba_shipments._FBA_ADDITIONAL_HANDLING_WEIGHT_LB,
            'mechLiftLb': fba_shipments._FBA_MECH_LIFT_LB,
            'ltlCubeFt3': fba_shipments._FBA_LTL_CUBE_FT3,
            'ltlWeightLb': fba_shipments._FBA_LTL_WEIGHT_LB,
            'stragglerUnits': fba_shipments._FBA_STRAGGLER_UNITS,
        }
        self.assertEqual(js, expected)


if __name__ == '__main__':
    unittest.main()
