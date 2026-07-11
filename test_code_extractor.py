"""Tests for code_extractor.py — run with `python -m pytest test_code_extractor.py -q`."""

import os
import re
import subprocess
import sys
from pathlib import Path

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
    apply_path_mappings,
    apply_variable_mappings,
    LANGUAGES,
    StringMaskRegistry,
    mask_strings_java,
    mask_strings_react,
    unmask_strings,
    sanitize,
    load_mappings,
    save_mappings,
    trace,
    write_extracted,
    apply_reversal,
    parse_inline_mappings,
    REVERSAL_MARKER,
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


def test_sanitize_masking_requires_registry():
    # Masking without recording the originals would be irreversible
    with pytest.raises(ValueError):
        sanitize('x = "a";', ORDER_MAPPING, dict(NO_STRIP, mask_strings=True),
                 LANGUAGES["spring"], registry=None)


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


def test_parse_inline_mappings():
    assert parse_inline_mappings(["com.myco=com.example", "Order=Rec=ord"], "--map-var") == [
        {"from": "com.myco", "to": "com.example"},
        {"from": "Order", "to": "Rec=ord"},   # split on FIRST '='
    ]
    assert parse_inline_mappings(None, "--map-var") == []
    with pytest.raises(SystemExit):
        parse_inline_mappings(["no-separator"], "--map-var")
    with pytest.raises(SystemExit):
        parse_inline_mappings(["=empty-from"], "--map-var")


# ─────────────────────────────────────────────
#  Integration fixtures
# ─────────────────────────────────────────────

SPRING_FILES = {
    "com/myco/service/OrderService.java": """\
package com.myco.service;

import com.myco.repo.OrderRepository;
import com.myco.model.Order;
import com.myco.model.OrderStatus;
import com.myco2.external.Billing;
import java.util.List;

/**
 * Handles orders.
 * @author someone
 */
public class OrderService {
    private static final String ORDER_TOPIC = "queue.com.myco.orders";
    private final OrderRepository orderRepository;

    public List<Order> findOrders(long orderId) {
        log.info("loading order {}", orderId);
        // Ordering matters here
        Order myOrder = orderRepository.findById(orderId);
        return List.of(myOrder);
    }
}
""",
    "com/myco/repo/OrderRepository.java": """\
package com.myco.repo;

import com.myco.model.Order;

public interface OrderRepository {
    Order findById(long orderId);
}
""",
    "com/myco/model/Order.java": """\
package com.myco.model;

import com.myco.model.OrderStatus;

public class Order {
    private OrderStatus orderStatus;
    private String note = "orders are precious";
}
""",
    "com/myco/model/OrderStatus.java": """\
package com.myco.model;

public enum OrderStatus { NEW, SHIPPED }
""",
}

SPRING_MAPPING = {
    "package": [{"from": "com.myco", "to": "com.example"}],
    "variable": [{"from": "Order", "to": "Record"}],
    "strings": {},
}

REACT_FILES = {
    "features/payroll/PayrollList.tsx": """\
import { useState } from 'react';
import { usePayroll } from './usePayroll';
import { fetchPayrolls } from '@/features/payroll/api/payrollApi';
import { Payroll } from './types';
import { missing } from './missing';

export function PayrollList() {
    {/* payroll table */}
    const { payrolls } = usePayroll();
    const label = 'Payroll run';
    const tpl = `static payroll template`;
    const dynamic = `count: ${payrolls.length}`;
    console.log('rendering payroll list');
    return <div>{label}</div>;
}
""",
    "features/payroll/usePayroll.ts": """\
import { fetchPayrolls } from '@/features/payroll/api/payrollApi';
import { Payroll } from './types';

export function usePayroll() {
    const payrolls: Payroll[] = [];
    return { payrolls };
}
""",
    "features/payroll/api/payrollApi.ts": """\
import { Payroll } from '../types';

export async function fetchPayrolls(): Promise<Payroll[]> {
    const url = '/api/payrolls';
    return [];
}
""",
    "features/payroll/types/index.ts": """\
export interface Payroll {
    payrollId: string;
    PAYROLL_STATUS: string;
}
""",
}

REACT_MAPPING = {
    "package": [{"from": "features/payroll", "to": "features/feature1"}],
    "variable": [{"from": "Payroll", "to": "Widget"}],
    "strings": {},
}

NO_STRIP = {"strip_comments": False, "strip_javadoc": False,
            "mask_strings": False, "strip_loggers": False}
ALL_STRIP = {k: True for k in NO_STRIP}


def make_spring_project(tmp_path: Path) -> Path:
    src_root = tmp_path / "src" / "main" / "java"
    for rel, content in SPRING_FILES.items():
        f = src_root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
    return src_root


def make_react_project(tmp_path: Path) -> Path:
    src_root = tmp_path / "src"
    for rel, content in REACT_FILES.items():
        f = src_root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
    return src_root


def make_generated_dir(tmp_path: Path) -> tuple:
    """A minimal 'AI-generated tests' directory plus the mapping to reverse it."""
    generated = tmp_path / "generated-tests"
    generated.mkdir()
    (generated / "RecordTest.java").write_text("class RecordTest { Record r; }", encoding="utf-8")
    return generated, dict(SPRING_MAPPING, language="spring")


def tree_snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def assert_fully_sanitized(out_dir: Path, mapping: dict, lang: dict):
    """
    A sanitized tree is a fixed point of the mapping: applying it again must
    change nothing, in any file's content or path. (Near-misses like com.myco2
    or 'Ordering' are intentionally untouched, so substring checks don't apply.)
    """
    for p in out_dir.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(out_dir).as_posix()
        mapped = str(apply_path_mappings(rel, mapping, lang))
        assert mapped in (rel, rel.replace("/", os.sep)), f"path not fully sanitized: {rel}"
        if p.suffix in (".java", ".ts", ".tsx", ".js", ".jsx"):
            content = p.read_text(encoding="utf-8")
            assert apply_all_mappings(content, mapping) == content, \
                f"content not fully sanitized: {rel}"


# ─────────────────────────────────────────────
#  Integration: trace → sanitize → reverse
# ─────────────────────────────────────────────

def test_spring_trace_and_sanitize(tmp_path, capsys):
    src_root = make_spring_project(tmp_path)
    entry = src_root / "com/myco/service/OrderService.java"
    deps = trace(entry, "com.myco", src_root, LANGUAGES["spring"])

    assert set(deps) == {"com.myco.service.OrderService", "com.myco.repo.OrderRepository",
                         "com.myco.model.Order", "com.myco.model.OrderStatus"}
    # com.myco2.* must not be traced (dot-boundary on the base package)
    assert not any("myco2" in mid for mid in deps)

    out_dir = tmp_path / "extracted"
    registry = StringMaskRegistry()
    write_extracted(deps, SPRING_MAPPING, ALL_STRIP, out_dir, LANGUAGES["spring"], registry)

    assert_fully_sanitized(out_dir, SPRING_MAPPING, LANGUAGES["spring"])
    assert (out_dir / "com/example/service/RecordService.java").exists()
    assert (out_dir / "com/example/model/RecordStatus.java").exists()
    # masked strings recorded with sanitized content
    assert any("queue.com.example.records" in v for v in registry.to_dict().values())


def test_react_trace_and_sanitize(tmp_path):
    src_root = make_react_project(tmp_path)
    entry = src_root / "features/payroll/PayrollList.tsx"
    deps = trace(entry, "@", src_root, LANGUAGES["react"])

    found = {mid for mid, info in deps.items() if info["path"] is not None}
    assert found == {"features/payroll/PayrollList.tsx", "features/payroll/usePayroll.ts",
                     "features/payroll/api/payrollApi.ts", "features/payroll/types/index.ts"}
    missing = {mid for mid, info in deps.items() if info["path"] is None}
    assert missing == {"features/payroll/missing"}

    out_dir = tmp_path / "extracted"
    registry = StringMaskRegistry()
    write_extracted(deps, REACT_MAPPING, ALL_STRIP, out_dir, LANGUAGES["react"], registry)

    assert_fully_sanitized(out_dir, REACT_MAPPING, LANGUAGES["react"])
    assert (out_dir / "features/feature1/WidgetList.tsx").exists()
    assert (out_dir / "features/feature1/useWidget.ts").exists()
    # import specifiers were renamed, not masked
    content = (out_dir / "features/feature1/WidgetList.tsx").read_text(encoding="utf-8")
    assert "'@/features/feature1/api/widgetApi'" in content
    # interpolated template kept (renamed), static strings masked
    assert "${widgets.length}" in content


@pytest.mark.parametrize("lang_key,builder,entry_rel,scope,mapping", [
    ("spring", make_spring_project, "com/myco/service/OrderService.java", "com.myco",
     SPRING_MAPPING),
    ("react", make_react_project, "features/payroll/PayrollList.tsx", "@",
     REACT_MAPPING),
])
def test_full_round_trip(tmp_path, lang_key, builder, entry_rel, scope, mapping):
    """sanitize (no strips, masking on) → reverse → byte-identical restoration."""
    lang = LANGUAGES[lang_key]
    src_root = builder(tmp_path)
    originals = tree_snapshot(src_root)

    deps = trace(src_root / entry_rel, scope, src_root, lang)
    out_dir = tmp_path / "extracted"
    registry = StringMaskRegistry()
    options = dict(NO_STRIP, mask_strings=True)
    write_extracted(deps, mapping, options, out_dir, lang, registry)
    assert_fully_sanitized(out_dir, mapping, lang)

    full_mapping = dict(mapping, strings=registry.to_dict(), language=lang_key)
    result = apply_reversal(out_dir, full_mapping, lang, backup=False)
    assert result["warnings"] == []
    assert result["strings_restored"] > 0

    restored = {rel: content for rel, content in tree_snapshot(out_dir).items()
                if rel != REVERSAL_MARKER}
    # every traced original file came back under its original relative path
    traced_rel = {info["path"].relative_to(src_root).as_posix()
                  for info in deps.values() if info["path"] is not None}
    assert set(restored) == traced_rel
    for rel, content in restored.items():
        # sanitize() strips outer whitespace; content must otherwise be identical
        assert content.decode("utf-8") == originals[rel].decode("utf-8").strip(), f"mismatch in {rel}"


def test_reversal_of_ai_generated_test_file(tmp_path):
    generated = tmp_path / "generated-tests"
    generated.mkdir()
    (generated / "RecordServiceTest.java").write_text(
        'package com.example.service;\n'
        'import com.example.model.Record;\n'
        'class RecordServiceTest {\n'
        '    Record myRecord; long recordId;\n'
        '    String topic = "STR_0";\n'
        "    String other = 'STR_1';\n"
        '}\n', encoding="utf-8")

    mapping = dict(SPRING_MAPPING,
                   strings={"STR_0": '"queue.com.example.records"', "STR_1": '"plain"'},
                   language="spring")
    result = apply_reversal(generated, mapping, LANGUAGES["spring"], backup=False)

    restored_file = generated / "OrderServiceTest.java"
    assert restored_file.exists()
    content = restored_file.read_text(encoding="utf-8")
    assert "package com.myco.service;" in content
    assert "Order myOrder; long orderId;" in content
    # string restored, then un-renamed back to the original sensitive value
    assert '"queue.com.myco.orders"' in content
    assert '"plain"' in content
    assert result["strings_restored"] == 2


def test_reverse_twice_refused_and_force_is_noop(tmp_path):
    generated, mapping = make_generated_dir(tmp_path)

    first = apply_reversal(generated, mapping, LANGUAGES["spring"], backup=False)
    assert first["changed"] and not first["refused"]
    snapshot = tree_snapshot(generated)

    second = apply_reversal(generated, mapping, LANGUAGES["spring"], backup=False)
    assert second["refused"]
    assert tree_snapshot(generated) == snapshot

    forced = apply_reversal(generated, mapping, LANGUAGES["spring"], backup=False, force=True)
    assert forced["changed"] == []      # engine is idempotent — nothing left to change
    files_only = {k: v for k, v in tree_snapshot(generated).items() if k != REVERSAL_MARKER}
    assert files_only == {k: v for k, v in snapshot.items() if k != REVERSAL_MARKER}


def test_reverse_dry_run_writes_nothing(tmp_path):
    generated, mapping = make_generated_dir(tmp_path)

    before = tree_snapshot(generated)
    result = apply_reversal(generated, mapping, LANGUAGES["spring"], dry_run=True)
    assert result["changed"] and result["renamed"]
    assert tree_snapshot(generated) == before
    assert not (generated / REVERSAL_MARKER).exists()
    assert not list(tmp_path.glob("*.backup-*"))


def test_reverse_creates_backup(tmp_path):
    generated, mapping = make_generated_dir(tmp_path)
    before = tree_snapshot(generated)

    apply_reversal(generated, mapping, LANGUAGES["spring"])
    backups = list(tmp_path.glob("generated-tests.backup-*"))
    assert len(backups) == 1
    assert tree_snapshot(backups[0]) == before


def test_sanitized_output_is_idempotent(tmp_path):
    src_root = make_spring_project(tmp_path)
    deps = trace(src_root / "com/myco/service/OrderService.java", "com.myco", src_root,
                 LANGUAGES["spring"])
    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    write_extracted(deps, SPRING_MAPPING, NO_STRIP, out1, LANGUAGES["spring"])

    # sanitize the sanitized output again: re-trace from the extracted tree
    deps2 = trace(out1 / "com/example/service/RecordService.java", "com.example", out1,
                  LANGUAGES["spring"])
    write_extracted(deps2, SPRING_MAPPING, NO_STRIP, out2, LANGUAGES["spring"])
    assert tree_snapshot(out1) == tree_snapshot(out2)


# ─────────────────────────────────────────────
#  CLI smoke test
# ─────────────────────────────────────────────

def test_cli_trace_dry_run(tmp_path):
    src_root = make_spring_project(tmp_path)
    out_dir = tmp_path / "extracted"
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "code_extractor.py"), "trace",
         "--entry", str(src_root / "com/myco/service/OrderService.java"),
         "--base", "com.myco", "--src", str(src_root), "--out", str(out_dir),
         "--map-package", "com.myco=com.example", "--map-var", "Order=Record",
         "--mask-strings", "--dry-run"],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Would write" in proc.stdout
    assert "No files written" in proc.stdout
    assert not out_dir.exists()
