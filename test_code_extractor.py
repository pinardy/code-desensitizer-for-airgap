"""Tests for code_extractor.py — run with `python -m pytest test_code_extractor.py -q`."""

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
