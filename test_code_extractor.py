"""Tests for code_extractor.py — run with `python -m pytest test_code_extractor.py -q`."""

import re

import pytest

from code_extractor import (
    to_snake_case,
    to_upper_snake_case,
    to_kebab_case,
    to_pascal_case,
    to_camel_case,
    pluralize,
    singularize,
    generate_name_variations,
    Renamer,
    package_mapping_patterns,
    variable_mapping_patterns,
    build_content_rules,
    build_path_rules,
    validate_mappings,
    apply_all_mappings,
    apply_variable_mappings,
    LANGUAGES,
    StringMaskRegistry,
    mask_strings_java,
    mask_strings_react,
    unmask_strings,
    load_mappings,
    save_mappings,
)


# ─────────────────────────────────────────────
#  Case conversions
# ─────────────────────────────────────────────

@pytest.mark.parametrize("name,expected", [
    ("Ingredient", "ingredient"),
    ("IngredientService", "ingredient_service"),
    ("ingredientId", "ingredient_id"),
    ("ingredient", "ingredient"),
    ("ingredient_service", "ingredient_service"),
])
def test_to_snake_case(name, expected):
    assert to_snake_case(name) == expected


@pytest.mark.parametrize("name,expected", [
    ("Ingredient", "INGREDIENT"),
    ("IngredientService", "INGREDIENT_SERVICE"),
])
def test_to_upper_snake_case(name, expected):
    assert to_upper_snake_case(name) == expected


@pytest.mark.parametrize("name,expected", [
    ("IngredientRow", "ingredient-row"),
    ("Ingredient", "ingredient"),
])
def test_to_kebab_case(name, expected):
    assert to_kebab_case(name) == expected


@pytest.mark.parametrize("name,expected", [
    ("ingredient", "Ingredient"),
    ("ingredient_service", "IngredientService"),
    ("Ingredient", "Ingredient"),
    ("", ""),
])
def test_to_pascal_case(name, expected):
    assert to_pascal_case(name) == expected


@pytest.mark.parametrize("name,expected", [
    ("Ingredient", "ingredient"),
    ("IngredientService", "ingredientService"),
    ("ingredient_service", "ingredientService"),
])
def test_to_camel_case(name, expected):
    assert to_camel_case(name) == expected


# ─────────────────────────────────────────────
#  Pluralize / singularize
# ─────────────────────────────────────────────

@pytest.mark.parametrize("word,expected", [
    ("ingredient", "ingredients"),
    ("Ingredient", "Ingredients"),
    ("INGREDIENT", "INGREDIENTS"),
    ("branch", "branches"),
    ("class", "classes"),
    ("box", "boxes"),
    ("category", "categories"),
    ("day", "days"),
    ("status", "statuses"),
    ("Status", "Statuses"),
    ("STATUS", "STATUSES"),
    ("child", "children"),
    ("index", "indices"),
])
def test_pluralize(word, expected):
    assert pluralize(word) == expected


@pytest.mark.parametrize("word,expected", [
    ("ingredients", "ingredient"),
    ("Ingredients", "Ingredient"),
    ("INGREDIENTS", "INGREDIENT"),
    ("branches", "branch"),       # ch/sh precedence bug in the old code
    ("dishes", "dish"),
    ("classes", "class"),
    ("boxes", "box"),
    ("categories", "category"),
    ("CATEGORIES", "CATEGORY"),
    ("statuses", "status"),
    ("children", "child"),
    ("indices", "index"),
    # Guards: these must NOT be truncated
    ("status", "status"),
    ("analysis", "analysis"),
    ("address", "address"),
    ("branch", "branch"),         # old bug: any ch-ending word lost 2 chars
])
def test_singularize(word, expected):
    assert singularize(word) == expected


@pytest.mark.parametrize("word", ["order", "Order", "branch", "category", "status", "child"])
def test_plural_round_trip(word):
    assert singularize(pluralize(word)) == word


# ─────────────────────────────────────────────
#  Name variations
# ─────────────────────────────────────────────

def test_generate_name_variations_ingredient():
    variations = set(generate_name_variations("Ingredient"))
    # Every case family and its plural must be covered
    for expected in ("Ingredient", "Ingredients", "ingredient", "ingredients",
                     "INGREDIENT", "INGREDIENTS"):
        assert expected in variations


def test_generate_name_variations_compound():
    variations = set(generate_name_variations("PayrollReport"))
    for expected in ("PayrollReport", "payrollReport", "payroll_report",
                     "PAYROLL_REPORT", "payroll-report"):
        assert expected in variations


def test_generate_name_variations_no_empty():
    assert "" not in generate_name_variations("Ingredient")


# ─────────────────────────────────────────────
#  Single-pass substitution engine
# ─────────────────────────────────────────────

ORDER_MAPPING = {
    "package": [{"from": "com.myco", "to": "com.example"}],
    "variable": [{"from": "Order", "to": "Record"}],
}


def test_package_boundaries():
    src = "package com.myco.service; import com.myco2.Thing; // mycom.myco_x"
    out = apply_all_mappings(src, {"package": [{"from": "com.myco", "to": "com.example"}], "variable": []})
    assert "com.example.service" in out
    assert "com.myco2" in out          # near-miss untouched
    assert "mycom.myco_x" in out       # not a boundary match


def test_package_inside_strings_and_comments():
    # Intentional: sensitive names must not survive anywhere, including strings
    src = '@ComponentScan("com.myco") // scan com.myco'
    out = apply_all_mappings(src, {"package": [{"from": "com.myco", "to": "com.example"}], "variable": []})
    assert "com.myco" not in out
    assert out.count("com.example") == 2


@pytest.mark.parametrize("src,expected", [
    ("Order order = new Order();", "Record record = new Record();"),
    ("OrderService", "RecordService"),
    ("ORDER_STATUS", "RECORD_STATUS"),
    ("myOrder", "myRecord"),
    ("orderId", "recordId"),
    ("List<Order> orders", "List<Record> records"),
    ("order_service", "record_service"),
    ("order-row.tsx", "record-row.tsx"),
])
def test_variable_compound_forms(src, expected):
    assert apply_variable_mappings(src, ORDER_MAPPING["variable"]) == expected


def test_variable_near_miss_untouched():
    # 'Ordering' is not a variation of 'Order'
    assert apply_variable_mappings("Ordering matters", ORDER_MAPPING["variable"]) == "Ordering matters"


def test_longest_mapping_wins():
    mappings = [{"from": "Order", "to": "Record"}, {"from": "OrderItem", "to": "Entry"}]
    out = apply_variable_mappings("OrderItem item; Order o;", mappings)
    assert out == "Entry item; Record o;"
    # order of the mapping list must not matter
    out2 = apply_variable_mappings("OrderItem item; Order o;", list(reversed(mappings)))
    assert out2 == out


def test_no_transitive_resubstitution():
    # A -> B and B -> C in one mapping set: an A that became B must NOT continue to C
    mappings = [{"from": "Alpha", "to": "Beta"}, {"from": "Beta", "to": "Gamma"}]
    out = apply_variable_mappings("Alpha Beta", mappings)
    assert out == "Beta Gamma"


def test_sanitize_idempotent():
    src = "package com.myco; class OrderService { Order order; String s = \"com.myco.Order\"; }"
    once = apply_all_mappings(src, ORDER_MAPPING)
    twice = apply_all_mappings(once, ORDER_MAPPING)
    assert once == twice


def test_reverse_round_trip_content():
    src = ("package com.myco.orders;\n"
           "public class OrderService {\n"
           "    private static final String ORDER_TYPE = \"standard\";\n"
           "    Order myOrder; List<Order> orders; long orderId;\n"
           "}\n")
    forward = apply_all_mappings(src, ORDER_MAPPING)
    assert "Order" not in forward and "order" not in forward and "ORDER" not in forward
    reversed_mapping = {
        "package": [{"from": "com.example", "to": "com.myco"}],
        "variable": [{"from": "Record", "to": "Order"}],
    }
    assert apply_all_mappings(forward, reversed_mapping) == src


def test_renamer_apply_count():
    rules = tuple(package_mapping_patterns("com.myco", "com.example"))
    renamer = Renamer(rules)
    out, count = renamer.apply_count("com.myco and com.myco again, com.myco2 no")
    assert count == 2
    assert "com.myco2" in out


def test_renamer_literal_replacement_chars():
    # Replacement containing backslash / dollar must be inserted literally
    renamer = Renamer([(re.escape("com/myco"), "com\\example$1")])
    assert renamer.apply("com/myco") == "com\\example$1"


def test_path_rules_both_separators():
    react = LANGUAGES["react"]
    rules = build_path_rules(
        {"package": [{"from": "features/payroll", "to": "features/feature1"}], "variable": []},
        react,
    )
    renamer = Renamer(rules)
    assert renamer.apply("src/features/payroll/List.tsx") == "src/features/feature1/List.tsx"
    assert renamer.apply("src\\features\\payroll\\List.tsx") == "src\\features\\feature1\\List.tsx"


def test_validate_mappings_warnings():
    warnings = validate_mappings({
        "package": [{"from": "com.myco", "to": "com.myco"}],
        "variable": [
            {"from": "Alpha", "to": "Beta"},
            {"from": "Beta", "to": "Gamma"},
            {"from": "Alpha", "to": "Delta"},
        ],
    })
    text = "\n".join(warnings)
    assert "maps to itself" in text
    assert "duplicate variable mapping for 'Alpha'" in text
    assert "overlaps the output" in text


def test_validate_mappings_clean():
    assert validate_mappings(ORDER_MAPPING) == []


# ─────────────────────────────────────────────
#  String masking / unmasking
# ─────────────────────────────────────────────

def test_mask_unmask_java_round_trip():
    src = ('String url = "jdbc:oracle:thin:@prod-db:1521";\n'
           'String quoted = "he said \\"hi\\"\\n";\n'
           'String tricky = "contains STR_1 inside";\n')
    registry = StringMaskRegistry()
    masked = mask_strings_java(src, registry)
    assert "jdbc:oracle" not in masked
    assert 'STR_0' in masked
    restored, count = unmask_strings(masked, registry.to_dict())
    assert restored == src
    assert count == 3


def test_mask_java_global_uniqueness_and_dedupe():
    registry = StringMaskRegistry()
    m1 = mask_strings_java('String a = "one";', registry)
    m2 = mask_strings_java('String b = "two"; String c = "one";', registry)
    assert '"STR_0"' in m1
    assert '"STR_1"' in m2          # counter continues across files
    assert '"STR_0"' in m2          # identical literal reuses its token
    assert registry.to_dict() == {"STR_0": '"one"', "STR_1": '"two"'}


def test_mask_java_no_catastrophic_backtracking():
    import time
    # A long line with an unterminated quote used to hang the old regex
    src = '"' + "a" * 50000
    start = time.monotonic()
    mask_strings_java(src, StringMaskRegistry())
    assert time.monotonic() - start < 2.0


def test_mask_unmask_react_round_trip():
    src = ("import { api } from '@/features/payroll/api';\n"
           "const label = 'Payroll run';\n"
           "const tpl = `static template`;\n"
           "const dynamic = `run ${id} done`;\n")
    registry = StringMaskRegistry()
    masked = mask_strings_react(src, registry)
    assert "'@/features/payroll/api'" in masked   # import specifier untouched
    assert "Payroll run" not in masked
    assert "static template" not in masked
    assert "${id}" in masked                       # interpolated template untouched
    restored, _ = unmask_strings(masked, registry.to_dict())
    assert restored == src


def test_unmask_any_quote_style():
    # AI-generated tests may restyle quotes around the token
    strings = {"STR_0": '"original"'}
    restored, count = unmask_strings("a('STR_0') b(\"STR_0\") c(`STR_0`)", strings)
    assert restored == 'a("original") b("original") c("original")'
    assert count == 3


def test_unmask_unknown_token_left_alone():
    restored, count = unmask_strings('x = "STR_99";', {"STR_0": '"a"'})
    assert restored == 'x = "STR_99";'
    assert count == 0


def test_registry_seeding_continues_numbering():
    registry = StringMaskRegistry({"STR_0": '"a"', "STR_7": '"b"'})
    assert registry.add('"new"') == "STR_8"
    assert registry.add('"a"') == "STR_0"


# ─────────────────────────────────────────────
#  mapping.json schema
# ─────────────────────────────────────────────

def test_load_mappings_legacy_list(tmp_path):
    f = tmp_path / "mapping.json"
    f.write_text('[{"from": "com.myco", "to": "com.example"}]', encoding="utf-8")
    loaded = load_mappings(f)
    assert loaded["package"] == [{"from": "com.myco", "to": "com.example"}]
    assert loaded["variable"] == []
    assert loaded["strings"] == {}
    assert loaded["version"] == 2


def test_load_mappings_v1_dict(tmp_path):
    f = tmp_path / "mapping.json"
    f.write_text('{"package": [], "variable": [{"from": "Order", "to": "Record"}], "language": "spring"}',
                 encoding="utf-8")
    loaded = load_mappings(f)
    assert loaded["variable"] == [{"from": "Order", "to": "Record"}]
    assert loaded["strings"] == {}
    assert loaded["language"] == "spring"


def test_save_load_mappings_v2_round_trip(tmp_path):
    f = tmp_path / "mapping.json"
    mapping = dict(ORDER_MAPPING, language="spring", strings={"STR_0": '"x"'})
    save_mappings(mapping, f)
    loaded = load_mappings(f)
    assert loaded["version"] == 2
    assert loaded["strings"] == {"STR_0": '"x"'}
    assert loaded["package"] == ORDER_MAPPING["package"]
