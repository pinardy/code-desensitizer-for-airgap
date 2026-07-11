#!/usr/bin/env python3
"""
Code Extractor & Sanitizer (Spring Boot Java / React)
------------------------------------------------------
Traces dependencies from an entry source file, sanitizes sensitive package/path
and class/variable names, and produces a reversal script to undo mappings on
generated test files.

Usage:
  python code_extractor.py trace   --entry path/to/MyService.java --base com.mycompany --src src/main/java --out ./extracted
  python code_extractor.py trace   --entry src/components/MyWidget.tsx --lang react --src src --out ./extracted
  python code_extractor.py reverse --mapping mapping.json --dir ./generated-tests
  python code_extractor.py         (interactive menu)

Requirements: Python 3.7+, no third-party packages needed.
"""

import os
import re
import sys
import json
import shutil
import hashlib
import argparse
from datetime import datetime
from pathlib import Path


# ─────────────────────────────────────────────
#  ANSI colours (disabled on Windows if needed)
# ─────────────────────────────────────────────
USE_COLOR = sys.platform != "win32" or os.environ.get("TERM")

def c(text, code):
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text

def green(t):  return c(t, "32")
def yellow(t): return c(t, "33")
def cyan(t):   return c(t, "36")
def red(t):    return c(t, "31")
def bold(t):   return c(t, "1")
def dim(t):    return c(t, "2")


# ─────────────────────────────────────────────
#  Java import / class parsing
# ─────────────────────────────────────────────

IMPORT_RE   = re.compile(r"^import\s+(?:static\s+)?([a-zA-Z0-9_.]+);", re.MULTILINE)
PACKAGE_RE  = re.compile(r"^package\s+([a-zA-Z0-9_.]+);", re.MULTILINE)
CLASS_RE    = re.compile(r"(?:public\s+)?(?:class|interface|enum|@interface)\s+(\w+)")


def fqn_to_relative_path(fqn: str) -> str:
    return fqn.replace(".", os.sep) + ".java"


def find_source_file(fqn: str, src_root: Path) -> "Path | None":
    """Search src_root for the .java file matching fqn."""
    rel = fqn_to_relative_path(fqn)
    candidate = src_root / rel
    if candidate.exists():
        return candidate
    # Fallback: glob by filename only
    class_name = fqn.split(".")[-1] + ".java"
    matches = list(src_root.rglob(class_name))
    return matches[0] if matches else None


def java_module_id(source: str, file_path: Path, src_root: Path) -> str:
    """Module identity for Java: the fully-qualified class name."""
    pkg_m = PACKAGE_RE.search(source)
    cls_m = CLASS_RE.search(source)
    pkg   = pkg_m.group(1) if pkg_m else ""
    cls   = cls_m.group(1) if cls_m else file_path.stem
    return f"{pkg}.{cls}" if pkg else cls


def java_find_deps(source: str, file_path: Path, scope: str, src_root: Path) -> list:
    """Return (fqn, resolved_path|None) for imports within the base package (scope)."""
    deps = []
    for m in IMPORT_RE.finditer(source):
        fqn = m.group(1)
        if not fqn.startswith(scope):
            continue
        class_name = fqn.split(".")[-1]
        if class_name == "*" or not class_name[0].isupper():
            continue
        deps.append((fqn, find_source_file(fqn, src_root)))
    return deps


def classify_java(module_id: str, source: str) -> str:
    lower = module_id.lower()
    if any(k in lower for k in ("repository", "repo", "dao")):    return "repository"
    if any(k in lower for k in ("service",)):                      return "service"
    if any(k in lower for k in ("controller", "rest", "resource")): return "controller"
    if any(k in lower for k in ("enum",)):                         return "enum"
    if any(k in lower for k in ("util", "helper", "config", "configuration")): return "util"
    return "model"


# ─────────────────────────────────────────────
#  React import / module parsing
# ─────────────────────────────────────────────

REACT_EXTS = [".tsx", ".ts", ".jsx", ".js"]

REACT_ASSET_EXTS = {
    ".css", ".scss", ".sass", ".less", ".styl",
    ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp",
    ".json", ".md", ".txt", ".yaml", ".yml",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".mp3", ".mp4", ".wav", ".webm",
}

REACT_IMPORT_RES = [
    # import X from '...'; import { A, B } from '...'; import '...'; import type X from '...'
    re.compile(r"^[ \t]*import\s+(?:type\s+)?(?:[^'\"\n]*?\bfrom\s*)?['\"]([^'\"\n]+)['\"]", re.MULTILINE),
    # export { A } from '...'; export * from '...'
    re.compile(r"^[ \t]*export\s+(?:type\s+)?[^'\"\n]*?\bfrom\s*['\"]([^'\"\n]+)['\"]", re.MULTILINE),
    # require('...'), dynamic import('...')
    re.compile(r"\b(?:require|import)\(\s*['\"]([^'\"\n]+)['\"]\s*\)"),
]

# Sentinel: import points outside the project (bare module or asset) — skipped.
EXTERNAL = object()


def resolve_react_import(spec: str, importing_file: Path, src_root: Path, alias: str = "@"):
    """
    Resolve an import specifier to a source file.
    Returns a Path, None (in-project but not found), or EXTERNAL (bare module / asset).
    """
    if spec.startswith("."):
        base = importing_file.parent / spec
    elif alias and (spec == alias or spec.startswith(alias + "/")):
        base = src_root / spec[len(alias):].lstrip("/")
    else:
        return EXTERNAL  # bare module: react, axios, @myco/ui, ...

    if Path(spec).suffix.lower() in REACT_ASSET_EXTS:
        return EXTERNAL

    base = Path(os.path.normpath(str(base)))
    candidates = []
    if base.suffix.lower() in (".js", ".jsx", ".ts", ".tsx"):
        candidates.append(base)
        # NodeNext-style: './foo.js' in source may be foo.ts/foo.tsx on disk
        if base.suffix.lower() in (".js", ".jsx"):
            candidates.append(base.with_suffix(".ts"))
            candidates.append(base.with_suffix(".tsx"))
    else:
        for ext in REACT_EXTS:
            candidates.append(base.with_name(base.name + ext))
        for ext in REACT_EXTS:
            candidates.append(base / ("index" + ext))

    for cand in candidates:
        if cand.is_file():
            return cand
    return None


def react_module_id(source: str, file_path: Path, src_root: Path) -> str:
    """Module identity for React: the src-root-relative path in posix form."""
    try:
        return file_path.resolve().relative_to(src_root.resolve()).as_posix()
    except ValueError:
        return file_path.name


def react_find_deps(source: str, file_path: Path, scope: str, src_root: Path) -> list:
    """Return (module_id, resolved_path|None) for in-project imports. scope = path alias."""
    specs = []
    for rx in REACT_IMPORT_RES:
        for m in rx.finditer(source):
            spec = m.group(1)
            if spec not in specs:
                specs.append(spec)

    deps = []
    for spec in specs:
        resolved = resolve_react_import(spec, file_path, src_root, alias=scope or "@")
        if resolved is EXTERNAL:
            continue
        if resolved is None:
            # In-project but unresolvable: keep a normalized id so it shows as ✗
            if spec.startswith("."):
                guess = Path(os.path.normpath(str(file_path.parent / spec)))
                try:
                    dep_id = guess.resolve().relative_to(src_root.resolve()).as_posix()
                except ValueError:
                    dep_id = spec
            else:
                dep_id = spec
            deps.append((str(dep_id), None))
        else:
            deps.append((react_module_id("", resolved, src_root), resolved))
    return deps


def classify_react(module_id: str, source: str) -> str:
    p = module_id.lower()
    parts = p.split("/")
    stem = Path(module_id).stem
    if re.match(r"use[A-Z]", stem) or "hooks" in parts:
        return "hook"
    if "context" in p or "provider" in p:
        return "context"
    if p.endswith(".d.ts") or any(seg in ("types", "interfaces", "models") for seg in parts):
        return "types"
    if any(seg in ("api", "apis", "services", "clients") for seg in parts):
        return "api"
    if any(seg in ("utils", "util", "helpers", "lib") for seg in parts):
        return "util"
    if p.endswith((".tsx", ".jsx")) or "components" in parts or re.search(r"<[A-Z][A-Za-z0-9]*[\s/>]", source):
        return "component"
    if re.search(r"\b(axios|fetch)\s*[.(]", source):
        return "api"
    return "module"


# ─────────────────────────────────────────────
#  Variable/Class name mapping
# ─────────────────────────────────────────────

def to_snake_case(name: str) -> str:
    """Convert CamelCase to snake_case (Ingredient → ingredient, IngredientService → ingredient_service)"""
    # Insert underscore before capitals (except first char)
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()


def to_upper_snake_case(name: str) -> str:
    """Convert to UPPER_SNAKE_CASE (Ingredient → INGREDIENT)"""
    return to_snake_case(name).upper()


def to_kebab_case(name: str) -> str:
    """Convert to kebab-case (IngredientRow → ingredient-row) — common for React file names"""
    return to_snake_case(name).replace("_", "-")


def to_pascal_case(name: str) -> str:
    """Convert snake_case or other to PascalCase (ingredient → Ingredient, ingredient_service → IngredientService)"""
    # Handle snake_case
    if "_" in name:
        return "".join(w.capitalize() for w in name.split("_"))
    # Already PascalCase or needs first letter capitalized
    return name[0].upper() + name[1:] if name else name


def to_camel_case(name: str) -> str:
    """Convert to camelCase (Ingredient → ingredient, IngredientService → ingredientService)"""
    pascal = to_pascal_case(name)
    return pascal[0].lower() + pascal[1:] if pascal else pascal


# Heuristic identifier plural/singular pairs — this is for generating name
# variations of code identifiers, not a linguistics library. Unknown words
# fall through to the simple suffix rules; truly unknown forms stay unchanged.
IRREGULAR_PLURALS = {
    "child": "children", "person": "people", "status": "statuses",
    "index": "indices", "matrix": "matrices", "analysis": "analyses",
    "criterion": "criteria", "datum": "data",
}
IRREGULAR_SINGULARS = {v: k for k, v in IRREGULAR_PLURALS.items()}


def _match_case(pattern_word: str, replacement: str) -> str:
    """Re-apply pattern_word's case shape (UPPER / Capitalized / lower) to replacement."""
    if pattern_word.isupper():
        return replacement.upper()
    if pattern_word[:1].isupper():
        return replacement.capitalize()
    return replacement


def pluralize(word: str) -> str:
    """Heuristic English pluralization for identifiers."""
    lower = word.lower()
    if lower in IRREGULAR_PLURALS:
        return _match_case(word, IRREGULAR_PLURALS[lower])

    def suffix(s: str) -> str:
        return s.upper() if word.isupper() else s

    # Simple rules
    if lower.endswith(("s", "x", "z", "ch", "sh")):
        return word + suffix("es")
    elif lower.endswith("y") and len(word) > 1 and word[-2].lower() not in "aeiou":
        return word[:-1] + suffix("ies")
    else:
        return word + suffix("s")


def singularize(word: str) -> str:
    """Heuristic English singularization for identifiers."""
    lower = word.lower()
    if lower in IRREGULAR_SINGULARS:
        return _match_case(word, IRREGULAR_SINGULARS[lower])

    # Simple rules
    if lower.endswith("ies") and len(word) > 3 and word[-4].lower() not in "aeiou":
        return word[:-3] + ("Y" if word.isupper() else "y")
    elif lower.endswith(("ches", "shes", "xes", "zes", "sses")):
        return word[:-2]
    elif lower.endswith("s") and len(word) > 1 and not lower.endswith(("ss", "us", "is")):
        return word[:-1]
    else:
        return word


def generate_name_variations(base_name: str) -> list:
    """
    Generate common variations of a class/variable name.
    E.g., 'Ingredient' → ['Ingredient', 'ingredient', 'ingredients', 'INGREDIENT', 'INGREDIENTS']
    """
    variations = set()

    # PascalCase forms
    pascal = to_pascal_case(base_name)
    variations.add(pascal)
    variations.add(pluralize(pascal))
    variations.add(singularize(pascal))

    # camelCase forms
    camel = to_camel_case(base_name)
    if camel and camel != pascal:
        variations.add(camel)
        variations.add(pluralize(camel))
        variations.add(singularize(camel))

    # UPPER_SNAKE_CASE forms
    upper_snake = to_upper_snake_case(base_name)
    variations.add(upper_snake)
    variations.add(pluralize(upper_snake))
    variations.add(singularize(upper_snake))

    # snake_case forms
    snake = to_snake_case(base_name)
    if snake != base_name and snake != camel.lower():
        variations.add(snake)
        variations.add(pluralize(snake))
        variations.add(singularize(snake))

    # kebab-case forms (React file names: ingredient-row.tsx)
    kebab = to_kebab_case(base_name)
    variations.add(kebab)
    variations.add(pluralize(kebab))
    variations.add(singularize(kebab))

    # Remove empty strings and the base name itself if it's just a case variant
    variations.discard("")

    return sorted(list(variations))


def variable_mapping_patterns(from_name: str, to_name: str) -> list:
    """
    Expand one variable mapping into ordered (regex_pattern, replacement) pairs
    covering all name variations, word boundaries, and compound identifiers.
    The lookaround syntax is valid in Python re, Perl, and .NET regex alike, so
    the same pairs drive apply_variable_mappings and the generated shell scripts.
    """
    pairs = []
    for from_var in generate_name_variations(from_name):
        # Determine the correct form for the 'to' name based on the 'from' form
        is_plural = (singularize(from_var) != from_var)

        if from_var.isupper():
            to_var = to_upper_snake_case(to_name)
        elif "-" in from_var:
            to_var = to_kebab_case(to_name)
        elif "_" in from_var and not from_var[0].isupper():
            to_var = to_snake_case(to_name)
        elif from_var[0].isupper():
            to_var = to_pascal_case(to_name)
        else:
            to_var = to_camel_case(to_name)
        if is_plural:
            to_var = pluralize(to_var)

        # UPPER_SNAKE_CASE as prefix in compound names (INGREDIENT in INGREDIENT_TYPE)
        if from_var.isupper() and "_" not in from_var:
            pairs.append((re.escape(from_var) + r"(?=_[A-Z])", to_var))

        # Standalone, with word boundaries (underscore excluded from the lookahead
        # for consistency with compound names)
        pairs.append((r"(?<![a-zA-Z0-9_])" + re.escape(from_var) + r"(?![a-zA-Z0-9])", to_var))

        # PascalCase compounds (IngredientService → ItemService, myIngredient → myItem)
        if "_" not in from_var and "-" not in from_var and from_var[0].isupper() and len(from_var) > 1:
            pairs.append((re.escape(from_var) + r"(?=[A-Z])", to_var))
            pairs.append((r"(?<=[a-z])" + re.escape(from_var), to_var))

        # camelCase prefixes (ingredientId → itemId)
        if from_var and from_var[0].islower() and "-" not in from_var and len(from_var) > 1:
            pairs.append((re.escape(from_var) + r"(?=[A-Z])", to_var))

    return pairs


# ─────────────────────────────────────────────
#  Single-pass substitution engine
# ─────────────────────────────────────────────

_LOOKAROUND_RE = re.compile(r"\(\?<?[=!][^)]*\)")


def _pattern_core_len(pattern: str) -> int:
    """Length of a pattern's literal core, ignoring zero-width lookarounds."""
    return len(_LOOKAROUND_RE.sub("", pattern))


class Renamer:
    """
    Single-pass, order-independent substitution engine.

    All (pattern, replacement) rules are compiled into ONE alternation regex and
    applied with a single re.sub, so a replacement is never re-scanned by another
    rule. That makes application idempotent and independent of mapping order —
    provided no 'to' value collides with another mapping's 'from' variations
    (validate_mappings warns about that).

    Rules are sorted longest-literal-core-first so that at any position the most
    specific rule wins (OrderItem beats Order). Replacements are returned from a
    callback, so they are always literal — '\\' and '$' in names are safe.
    """

    def __init__(self, rules):
        self._replacements = {}
        parts = []
        seen = set()
        ordered = sorted(enumerate(rules),
                         key=lambda t: (-_pattern_core_len(t[1][0]), t[0]))
        for _, (pattern, replacement) in ordered:
            if pattern in seen:
                continue  # first (most specific / earliest) rule wins
            seen.add(pattern)
            group = f"g{len(self._replacements)}"
            self._replacements[group] = replacement
            parts.append(f"(?P<{group}>{pattern})")
        self._regex = re.compile("|".join(parts)) if parts else None

    def _lookup(self, match) -> str:
        return self._replacements[match.lastgroup]

    def apply(self, text: str) -> str:
        if self._regex is None:
            return text
        return self._regex.sub(self._lookup, text)

    def apply_count(self, text: str) -> "tuple[str, int]":
        """Like apply, but also returns the number of substitutions made."""
        if self._regex is None:
            return text, 0
        count = 0

        def counting_lookup(match):
            nonlocal count
            count += 1
            return self._replacements[match.lastgroup]

        return self._regex.sub(counting_lookup, text), count


_RENAMER_CACHE = {}


def _cached_renamer(rules: tuple) -> Renamer:
    renamer = _RENAMER_CACHE.get(rules)
    if renamer is None:
        renamer = _RENAMER_CACHE[rules] = Renamer(rules)
    return renamer


def package_mapping_patterns(frm: str, to: str) -> list:
    """
    Boundary-guarded pattern for a package / path-segment mapping.
    '.', '/', quotes and whitespace all count as boundaries, so com.myco still
    matches inside com.myco.service and "com.myco", but not inside com.myco2.
    """
    return [(r"(?<![A-Za-z0-9_])" + re.escape(frm) + r"(?![A-Za-z0-9_])", to)]


def build_variable_rules(var_mappings: list) -> tuple:
    rules = []
    for m in var_mappings:
        if m.get("from") and m.get("to"):
            rules.extend(variable_mapping_patterns(m["from"], m["to"]))
    return tuple(rules)


def build_content_rules(mapping_dict: dict) -> tuple:
    """Rules for file contents: package mappings + variable mappings.

    Names are intentionally replaced inside strings and comments too — a
    sensitive name must not survive anywhere in the sanitized output.
    """
    rules = []
    for m in mapping_dict.get("package", []):
        if m.get("from") and m.get("to"):
            rules.extend(package_mapping_patterns(m["from"], m["to"]))
    rules.extend(build_variable_rules(mapping_dict.get("variable", [])))
    return tuple(rules)


def build_path_rules(mapping_dict: dict, lang: dict) -> tuple:
    """Rules for file paths: language-specific package path variants + variable mappings."""
    rules = []
    for m in mapping_dict.get("package", []):
        if m.get("from") and m.get("to"):
            for f_variant, t_variant in lang["pkg_path_variants"](m["from"], m["to"]):
                rules.extend(package_mapping_patterns(f_variant, t_variant))
    rules.extend(build_variable_rules(mapping_dict.get("variable", [])))
    return tuple(rules)


def validate_mappings(mapping_dict: dict) -> list:
    """
    Return human-readable warnings for mapping sets that cannot behave predictably:
    duplicate 'from' values, from == to, and collisions where one mapping's 'to'
    would be matched by another mapping's 'from' (the engine never re-scans
    replacements, so such cascades no longer happen — warn instead).
    """
    warnings = []
    pkg = [m for m in mapping_dict.get("package", []) if m.get("from") and m.get("to")]
    var = [m for m in mapping_dict.get("variable", []) if m.get("from") and m.get("to")]

    for kind, mappings in (("package", pkg), ("variable", var)):
        seen = {}
        for m in mappings:
            if m["from"] == m["to"]:
                warnings.append(f"{kind} mapping '{m['from']}' maps to itself")
            if m["from"] in seen and seen[m["from"]] != m["to"]:
                warnings.append(f"duplicate {kind} mapping for '{m['from']}' "
                                f"('{seen[m['from']]}' vs '{m['to']}') — the first one wins")
            seen.setdefault(m["from"], m["to"])

    for m1 in pkg:
        for m2 in pkg:
            if m1 is not m2 and m2["from"] in m1["to"]:
                warnings.append(f"package mapping '{m2['from']}' → '{m2['to']}' would have "
                                f"cascaded onto the output of '{m1['from']}' → '{m1['to']}'; "
                                f"replacements are applied in a single pass, so it will not")

    var_variations = [(m, set(generate_name_variations(m["from"])),
                       set(generate_name_variations(m["to"]))) for m in var]
    for m1, _, to_vars1 in var_variations:
        for m2, from_vars2, _ in var_variations:
            if m1 is not m2 and to_vars1 & from_vars2:
                warnings.append(f"variable mapping '{m2['from']}' → '{m2['to']}' overlaps the "
                                f"output of '{m1['from']}' → '{m1['to']}' "
                                f"({', '.join(sorted(to_vars1 & from_vars2))}); "
                                f"replacements are applied in a single pass, so it will not cascade")
    return warnings


def apply_variable_mappings(source: str, var_mappings: list) -> str:
    """
    Apply variable/class name mappings with word boundary awareness.
    Handles both standalone names and compound names (e.g., IngredientService, INGREDIENT_TYPE).
    Preserves plural/singular forms during mapping.
    """
    return _cached_renamer(build_variable_rules(var_mappings)).apply(source)


# ─────────────────────────────────────────────
#  Recursive dependency tracer
# ─────────────────────────────────────────────

def trace(entry_path: Path, scope: str, src_root: Path, lang: dict) -> dict:
    """
    BFS from entry_path, following in-project imports.
    scope: base package (spring) or path alias (react).
    Returns {module_id: {"path": Path|None, "type": str, "source": str}}
    """
    visited = {}
    queue   = [entry_path]

    while queue:
        current = queue.pop(0)
        try:
            source = read_source(current)
        except Exception as e:
            print(red(f"  [!] Cannot read {current}: {e}"))
            continue

        module_id = lang["module_id"](source, current, src_root)
        if module_id in visited:
            continue

        visited[module_id] = {
            "path":   current,
            "type":   lang["classify"](module_id, source),
            "source": source,
        }

        for dep_id, dep_path in lang["find_deps"](source, current, scope, src_root):
            if dep_id not in visited:
                if dep_path:
                    queue.append(dep_path)
                else:
                    visited[dep_id] = {
                        "path":   None,
                        "type":   lang["classify"](dep_id, ""),
                        "source": "",
                    }

    return visited


# ─────────────────────────────────────────────
#  Sanitization
# ─────────────────────────────────────────────

MAPPING_SCHEMA_VERSION = 2


def load_mappings(mapping_file: Path) -> dict:
    """
    Load mappings and normalize to the v2 schema:
      {"version": 2, "language": ..., "package": [...], "variable": [...], "strings": {...}}
    Accepts the legacy bare-list format (package mappings only) and v1 dicts
    (no 'version'/'strings' keys).
    """
    with open(mapping_file, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        data = {"package": data}
    data.setdefault("package", [])
    data.setdefault("variable", [])
    data.setdefault("strings", {})
    data["version"] = MAPPING_SCHEMA_VERSION
    return data


def save_mappings(mappings: dict, mapping_file: Path):
    """Save mappings in the v2 schema (see load_mappings)."""
    mappings = {"version": MAPPING_SCHEMA_VERSION, **mappings}
    mappings["version"] = MAPPING_SCHEMA_VERSION
    with open(mapping_file, "w", encoding="utf-8") as f:
        json.dump(mappings, f, indent=2)
    print(green(f"  Mappings saved → {mapping_file}"))


def get_language(mapping_dict: dict, cli_flag: "str | None" = None) -> str:
    """Language precedence: explicit CLI flag > mapping.json 'language' > default (spring)."""
    key = cli_flag or mapping_dict.get("language") or DEFAULT_LANG
    return key if key in LANGUAGES else DEFAULT_LANG


def apply_all_mappings(source: str, mapping_dict: dict) -> str:
    """Apply both package and variable mappings to source code in a single pass."""
    return _cached_renamer(build_content_rules(mapping_dict)).apply(source)


def apply_all_mappings_count(source: str, mapping_dict: dict) -> "tuple[str, int]":
    """Like apply_all_mappings, but also returns the substitution count (for dry runs)."""
    return _cached_renamer(build_content_rules(mapping_dict)).apply_count(source)


def apply_path_mappings(path_value, mapping_dict: dict, lang: dict) -> Path:
    """Apply package/path and variable mappings to a path string or Path."""
    return Path(_cached_renamer(build_path_rules(mapping_dict, lang)).apply(str(path_value)))


def rename_source_path(file_path: Path, mapping_dict: dict, lang: dict) -> Path:
    """Rename a source file path, including parent folders, via path mappings."""
    renamed_path = apply_path_mappings(file_path, mapping_dict, lang)
    if renamed_path.name == file_path.name and renamed_path.parent == file_path.parent:
        return file_path
    return renamed_path


def java_output_rel_path(module_id: str, mapping_dict: dict) -> Path:
    """Output path for a Java class: sanitized FQN → package-directory path."""
    pkg_rules = []
    for m in mapping_dict.get("package", []):
        if m.get("from") and m.get("to"):
            pkg_rules.extend(package_mapping_patterns(m["from"], m["to"]))
    var_mappings = mapping_dict.get("variable", [])

    sanitized_fqn = _cached_renamer(tuple(pkg_rules)).apply(module_id)

    pkg_name, sep, class_name = sanitized_fqn.rpartition(".")
    if sep:
        class_name = apply_variable_mappings(class_name, var_mappings)
        sanitized_fqn = f"{pkg_name}.{class_name}"
    else:
        sanitized_fqn = apply_variable_mappings(sanitized_fqn, var_mappings)

    return apply_path_mappings(Path(fqn_to_relative_path(sanitized_fqn)), mapping_dict, LANGUAGES["spring"])


def react_output_rel_path(module_id: str, mapping_dict: dict) -> Path:
    """Output path for a React module: the relative path with mappings applied."""
    return apply_path_mappings(Path(module_id), mapping_dict, LANGUAGES["react"])


def strip_comments_java(source: str) -> str:
    # Block comments (including Javadoc)
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    # Line comments
    source = re.sub(r"//[^\n]*", "", source)
    # Collapse excessive blank lines
    source = re.sub(r"\n{3,}", "\n\n", source)
    return source


def strip_javadoc_tags(source: str) -> str:
    return re.sub(r"@(author|since|version|see)\b[^\n]*", "", source, flags=re.IGNORECASE)


class StringMaskRegistry:
    """
    Run-wide registry of masked string literals, so STR_n tokens are unique
    across all files of a run and can be restored on reversal.

    Stores the FULL original literal including its quote characters; identical
    literals share one token. Seed with a previously saved mapping's 'strings'
    section so re-runs never reuse an existing index for a different literal.
    """

    def __init__(self, existing: "dict | None" = None):
        self._by_token = dict(existing or {})     # "STR_0" -> '"literal"'
        self._by_literal = {v: k for k, v in self._by_token.items()}
        self._next = 0
        for token in self._by_token:
            m = re.fullmatch(r"STR_(\d+)", token)
            if m:
                self._next = max(self._next, int(m.group(1)) + 1)

    def add(self, literal: str) -> str:
        """Register a literal (quotes included) and return its bare token, e.g. 'STR_7'."""
        token = self._by_literal.get(literal)
        if token is None:
            token = f"STR_{self._next}"
            self._next += 1
            self._by_token[token] = literal
            self._by_literal[literal] = token
        return token

    def to_dict(self) -> dict:
        return dict(self._by_token)


# Linear-time string-literal regex (unrolled loop — no nested quantifiers, so no
# catastrophic backtracking on pathological input). Known limits: Java text
# blocks (\"\"\") and char literals are not masked.
JAVA_STRING_RE = re.compile(r'"[^"\\\n]*(?:\\.[^"\\\n]*)*"')

MASK_TOKEN_RE = re.compile(r"([\"'`])STR_(\d+)\1")
BARE_MASK_TOKEN_RE = re.compile(r"\bSTR_\d+\b")


def mask_strings_java(source: str, registry: StringMaskRegistry) -> str:
    def replacer(m):
        return f'"{registry.add(m.group(0))}"'
    return JAVA_STRING_RE.sub(replacer, source)


def unmask_strings(text: str, strings: dict) -> "tuple[str, int]":
    """
    Restore masked literals: any quoted STR_n token ("STR_1", 'STR_1' or `STR_1`,
    regardless of which quote style the original had) becomes the recorded
    original literal, verbatim. Returns (text, restored_count).
    """
    if not strings:
        return text, 0
    count = 0

    def replacer(m):
        nonlocal count
        original = strings.get(f"STR_{m.group(2)}")
        if original is None:
            return m.group(0)  # unknown token — leave untouched
        count += 1
        return original

    return MASK_TOKEN_RE.sub(replacer, text), count


def strip_loggers_java(source: str) -> str:
    return re.sub(r"[ \t]*log\.(debug|info|warn|error|trace)\([^;]+\);\n?", "\n", source)


# ── JS/TS scanner-based passes ──
#
# Regex-only stripping is unsafe for JS ('//' inside URLs, template literals),
# so a small character scanner tokenizes the source first.
# Known limitation: regex literals (/foo\/bar/) are not tracked.

def _scan_js(source: str) -> list:
    """
    Tokenize JS/TS source into (kind, text) segments.
    kind ∈ {"code", "line_comment", "block_comment", "string", "template"}.
    """
    segments = []
    n = len(source)
    i = 0
    code_start = 0

    def emit_code(upto):
        if upto > code_start:
            segments.append(("code", source[code_start:upto]))

    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            end = source.find("\n", i)
            end = n if end == -1 else end
            emit_code(i)
            segments.append(("line_comment", source[i:end]))
            i = end
            code_start = i
        elif ch == "/" and nxt == "*":
            end = source.find("*/", i + 2)
            end = n if end == -1 else end + 2
            emit_code(i)
            segments.append(("block_comment", source[i:end]))
            i = end
            code_start = i
        elif ch in ("'", '"'):
            j = i + 1
            closed = False
            while j < n:
                cj = source[j]
                if cj == "\\":
                    j += 2
                    continue
                if cj == ch:
                    closed = True
                    break
                if cj == "\n":
                    break
                j += 1
            if closed:
                emit_code(i)
                segments.append(("string", source[i:j + 1]))
                i = j + 1
                code_start = i
            else:
                i += 1  # unterminated — leave as code
        elif ch == "`":
            j = i + 1
            depth = 0  # ${...} nesting
            closed = False
            while j < n:
                cj = source[j]
                if cj == "\\":
                    j += 2
                    continue
                if cj == "$" and j + 1 < n and source[j + 1] == "{":
                    depth += 1
                    j += 2
                    continue
                if cj == "}" and depth > 0:
                    depth -= 1
                elif cj == "`" and depth == 0:
                    closed = True
                    break
                j += 1
            if closed:
                emit_code(i)
                segments.append(("template", source[i:j + 1]))
                i = j + 1
                code_start = i
            else:
                i += 1
        else:
            i += 1

    emit_code(n)
    return segments


def strip_comments_react(source: str) -> str:
    # JSX comments: {/* ... */} — remove the braces too, not just the comment
    source = re.sub(r"\{\s*/\*.*?\*/\s*\}", "", source, flags=re.DOTALL)
    parts = [text for kind, text in _scan_js(source) if kind not in ("line_comment", "block_comment")]
    result = "".join(parts)
    result = re.sub(r"[ \t]+\n", "\n", result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result


def mask_strings_react(source: str, registry: StringMaskRegistry) -> str:
    """
    Mask string literals — but never import/export/require specifiers,
    and never template literals containing ${...} interpolation.
    """
    out = []
    line_tail = [""]  # text emitted since the last newline

    def emit(text):
        out.append(text)
        nl = text.rfind("\n")
        if nl == -1:
            line_tail[0] += text
        else:
            line_tail[0] = text[nl + 1:]

    for kind, text in _scan_js(source):
        if kind == "string":
            tail = line_tail[0]
            is_specifier = (
                re.match(r"\s*(import|export)\b", tail)
                or re.search(r"\bfrom\s*$", tail)
                or re.search(r"\b(require|import)\s*\(\s*$", tail)
            )
            if is_specifier:
                emit(text)
            else:
                q = text[0]
                emit(f"{q}{registry.add(text)}{q}")
        elif kind == "template" and "${" not in text:
            emit(f"`{registry.add(text)}`")
        else:
            emit(text)

    return "".join(out)


def strip_loggers_react(source: str) -> str:
    return re.sub(r"[ \t]*console\.(log|info|warn|error|debug|trace)\([^;\n]*\);?\n?", "\n", source)


def sanitize(source: str, mapping_dict: dict, options: dict, lang: dict,
             registry: "StringMaskRegistry | None" = None) -> str:
    # Masking runs after renaming, so recorded literals contain sanitized names.
    # Reversal unmasks first, then un-renames — restoring the originals exactly.
    source = apply_all_mappings(source, mapping_dict)
    if options.get("strip_comments"):   source = lang["strip_comments"](source)
    if options.get("strip_javadoc"):    source = lang["strip_doc_tags"](source)
    if options.get("mask_strings"):     source = lang["mask_strings"](source, registry if registry is not None else StringMaskRegistry())
    if options.get("strip_loggers"):    source = lang["strip_loggers"](source)
    return source.strip()


# ─────────────────────────────────────────────
#  Reversal
# ─────────────────────────────────────────────

def reverse_mappings(mapping_dict: dict) -> dict:
    """Reverse both package and variable mappings for reversal."""
    reversed_dict = {
        "package": [{"from": m["to"], "to": m["from"]} for m in mapping_dict.get("package", []) if m.get("from") and m.get("to")],
        "variable": [{"from": m["to"], "to": m["from"]} for m in mapping_dict.get("variable", []) if m.get("from") and m.get("to")]
    }
    return reversed_dict


def _glob_sources(target_dir: Path, lang: dict) -> list:
    """All source files for the language, deepest paths first."""
    files = []
    for ext in lang["extensions"]:
        files.extend(target_dir.rglob(f"*{ext}"))
    return sorted(files, key=lambda path: len(path.parts), reverse=True)


REVERSAL_MARKER = ".code_extractor_reversed.json"


def read_source(path: Path) -> str:
    """Read a source file as strict UTF-8; fall back with a loud warning instead of silent corruption."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8", errors="replace")
        print(yellow(f"  [!] {path}: not valid UTF-8 — {text.count(chr(0xFFFD))} character(s) replaced. "
                     f"Reversal may not restore this file exactly."))
        return text


def _mapping_fingerprint(mapping_dict: dict) -> str:
    canonical = json.dumps(mapping_dict, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def apply_reversal(target_dir: Path, mapping_dict: dict, lang: dict,
                   dry_run: bool = False, backup: bool = True, force: bool = False) -> dict:
    """
    Restore original names in all source files under target_dir:
    unmask string literals first, then un-rename identifiers/packages, then
    rename file paths. Refuses to run twice on the same directory with the
    same mapping (marker file) unless force=True.
    """
    result = {"renamed": [], "changed": [], "warnings": [], "strings_restored": 0}
    reversed_maps = reverse_mappings(mapping_dict)
    strings = mapping_dict.get("strings", {})
    fingerprint = _mapping_fingerprint(mapping_dict)

    marker_file = target_dir / REVERSAL_MARKER
    if marker_file.exists() and not force and not dry_run:
        try:
            marker = json.loads(marker_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            marker = {}
        if marker.get("mapping_sha256") == fingerprint:
            print(red(f"  This directory was already reversed with this mapping on "
                      f"{marker.get('timestamp', 'an earlier run')}."))
            print(red("  Re-running could corrupt names. Use --force to override."))
            result["warnings"].append("already reversed — refused (use --force)")
            return result

    source_files = _glob_sources(target_dir, lang)
    if not source_files:
        print(yellow("  No matching source files found in target directory."))
        return result

    if not strings:
        for sf in source_files:
            if BARE_MASK_TOKEN_RE.search(read_source(sf)):
                result["warnings"].append(f"{sf}: contains STR_n placeholders but the mapping has "
                                          f"no 'strings' section (predates string masking support?) "
                                          f"— placeholders will be left as-is")

    if backup and not dry_run:
        backup_dir = target_dir.with_name(
            target_dir.name + ".backup-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
        shutil.copytree(target_dir, backup_dir)
        print(green(f"  Backup → {backup_dir}"))

    # Content first (on stable paths), then file renames.
    for sf in source_files:
        original = read_source(sf)
        unmasked, restored = unmask_strings(original, strings)
        updated, substitutions = apply_all_mappings_count(unmasked, reversed_maps)
        leftover = sorted(set(BARE_MASK_TOKEN_RE.findall(updated)))
        if leftover and strings:
            result["warnings"].append(f"{sf}: unrestorable placeholder(s) left in place: "
                                      f"{', '.join(leftover)}")
        if updated != original:
            result["changed"].append(sf)
            result["strings_restored"] += restored
            if dry_run:
                print(f"  Would update: {sf}  "
                      f"({substitutions} substitution(s), {restored} string(s) restored)")
            else:
                sf.write_text(updated, encoding="utf-8")

    renamed_count = 0
    for sf in _glob_sources(target_dir, lang):
        renamed_path = rename_source_path(sf, reversed_maps, lang)
        if renamed_path != sf:
            if renamed_path.exists():
                print(yellow(f"  [skip] file rename collision: {sf.name} -> {renamed_path.name}"))
                result["warnings"].append(f"rename collision: {sf} -> {renamed_path}")
                continue
            result["renamed"].append((sf, renamed_path))
            if dry_run:
                print(f"  Would rename: {sf} -> {renamed_path}")
            else:
                renamed_path.parent.mkdir(parents=True, exist_ok=True)
                sf.rename(renamed_path)
                renamed_count += 1

    for w in result["warnings"]:
        print(yellow(f"  [!] {w}"))

    if dry_run:
        print(bold(f"  {len(result['renamed'])} file(s) to rename, "
                   f"{len(result['changed'])} file(s) to update. No changes written (--dry-run)."))
    else:
        marker_file.write_text(json.dumps({
            "mapping_sha256": fingerprint,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "renamed": renamed_count,
            "changed": len(result["changed"]),
        }, indent=2), encoding="utf-8")
        print(green(f"  Reversal complete — {renamed_count} file(s) renamed, "
                    f"{len(result['changed'])}/{len(source_files)} files updated."))
    return result


# ─────────────────────────────────────────────
#  Prompt templates
# ─────────────────────────────────────────────

def java_prompt_template(deps: dict, mapping_dict: dict, test_framework: str = None) -> str:
    var_mappings = mapping_dict.get("variable", [])
    dep_classes = [
        apply_variable_mappings(fqn.split(".")[-1], var_mappings)
        for fqn, i in deps.items() if i["type"] != "controller"
    ]
    return f"""I have a Spring Boot service I need unit tests for.

Please generate comprehensive JUnit 5 unit tests using Mockito.

Requirements:
- Use @ExtendWith(MockitoExtension.class) — no @SpringBootTest
- Cover: happy path, edge cases, null/empty inputs, exception scenarios
- Use AssertJ assertions (assertThat)
- Mock all dependencies: {', '.join(dep_classes) if dep_classes else '[see classes below]'}
- Do not assume any Spring context or infrastructure is running

Here are the sanitized source files:

[paste the contents of each extracted file below this line]
"""


def react_prompt_template(deps: dict, mapping_dict: dict, test_framework: str = "jest") -> str:
    var_mappings = mapping_dict.get("variable", [])
    dep_modules = []
    for mid, i in deps.items():
        if i["type"] == "component":
            continue
        p = Path(mid)
        # index.ts barrels are better identified by their directory (types/index.ts → types)
        name = p.stem
        if name == "index" and p.parent.name:
            name = p.parent.name
        dep_modules.append(apply_variable_mappings(name, var_mappings))
    if test_framework == "vitest":
        fw_label, mock_fn = "Vitest", "vi.mock"
    else:
        fw_label, mock_fn = "Jest", "jest.mock"
    return f"""I have a React component/module I need unit tests for.

Please generate comprehensive unit tests using {fw_label} and React Testing Library.

Requirements:
- Use @testing-library/react and @testing-library/user-event
- Mock dependency modules with {mock_fn}: {', '.join(dep_modules) if dep_modules else '[see modules below]'}
- Cover: rendering, user interaction, edge cases, and async/API error paths
- No real network calls — mock api/service modules directly
- Avoid snapshot-only tests; assert on visible behavior

Here are the sanitized source files:

[paste the contents of each extracted file below this line]
"""


# ─────────────────────────────────────────────
#  Language registry
# ─────────────────────────────────────────────

JAVA_TYPE_ORDER  = ["controller", "service", "model", "repository", "util", "enum"]
JAVA_TYPE_LABELS = {
    "controller":  "Controllers / entry points",
    "service":     "Services",
    "model":       "Domain models",
    "repository":  "Repository interfaces (interface only — no impl)",
    "util":        "Utilities / helpers",
    "enum":        "Enums / constants",
}

REACT_TYPE_ORDER  = ["component", "hook", "context", "api", "types", "util", "module"]
REACT_TYPE_LABELS = {
    "component": "Components",
    "hook":      "Hooks",
    "context":   "Contexts / providers",
    "api":       "API / service clients",
    "types":     "Types / interfaces",
    "util":      "Utilities / helpers",
    "module":    "Other modules",
}


def _java_pkg_path_variants(frm: str, to: str) -> list:
    return [(frm.replace(".", os.sep), to.replace(".", os.sep))]


def _react_pkg_path_variants(frm: str, to: str) -> list:
    variants = [(frm, to)]
    alt = (frm.replace("/", os.sep), to.replace("/", os.sep))
    if alt != variants[0]:
        variants.append(alt)
    return variants


LANGUAGES = {
    "spring": {
        "key":               "spring",
        "label":             "Spring Boot (Java)",
        "extensions":        [".java"],
        "default_src":       "src/main/java",
        "src_markers":       ["src/main/java", "src\\main\\java"],
        "entry_hint":        ".java",
        "pkg_mapping_title": "Package / token mappings",
        "pkg_mapping_hint":  "e.g. com.classified → com.example",
        "doc_tag_label":     "Javadoc",
        "logger_label":      "logger statements (log.*)",
        "module_id":         java_module_id,
        "find_deps":         java_find_deps,
        "id_to_rel_path":    fqn_to_relative_path,
        "output_rel_path":   java_output_rel_path,
        "classify":          classify_java,
        "type_order":        JAVA_TYPE_ORDER,
        "type_labels":       JAVA_TYPE_LABELS,
        "strip_comments":    strip_comments_java,
        "strip_doc_tags":    strip_javadoc_tags,
        "mask_strings":      mask_strings_java,
        "strip_loggers":     strip_loggers_java,
        "pkg_path_variants": _java_pkg_path_variants,
        "pkg_to_posix":      lambda s: s.replace(".", "/"),
        "prompt_template":   java_prompt_template,
    },
    "react": {
        "key":               "react",
        "label":             "React (JS/TS)",
        "extensions":        [".tsx", ".ts", ".jsx", ".js"],
        "default_src":       "src",
        "src_markers":       [f"{os.sep}src{os.sep}", "/src/", "\\src\\"],
        "entry_hint":        ".js/.jsx/.ts/.tsx",
        "pkg_mapping_title": "Path / module segment mappings",
        "pkg_mapping_hint":  "e.g. features/payroll → features/feature1",
        "doc_tag_label":     "JSDoc",
        "logger_label":      "console.* statements",
        "module_id":         react_module_id,
        "find_deps":         react_find_deps,
        "id_to_rel_path":    lambda mid: mid,
        "output_rel_path":   react_output_rel_path,
        "classify":          classify_react,
        "type_order":        REACT_TYPE_ORDER,
        "type_labels":       REACT_TYPE_LABELS,
        "strip_comments":    strip_comments_react,
        "strip_doc_tags":    strip_javadoc_tags,
        "mask_strings":      mask_strings_react,
        "strip_loggers":     strip_loggers_react,
        "pkg_path_variants": _react_pkg_path_variants,
        "pkg_to_posix":      lambda s: s,
        "prompt_template":   react_prompt_template,
    },
}

DEFAULT_LANG = "spring"


def infer_language(entry: str) -> str:
    ext = Path(entry).suffix.lower()
    for key, lang_def in LANGUAGES.items():
        if ext in lang_def["extensions"]:
            return key
    return DEFAULT_LANG


# ─────────────────────────────────────────────
#  Output helpers
# ─────────────────────────────────────────────

def print_checklist(deps: dict, lang: dict):
    grouped = {}
    for module_id, info in deps.items():
        t = info["type"]
        grouped.setdefault(t, []).append((module_id, info["path"]))

    print()
    print(bold("═══ Extraction checklist ═══"))
    for t in lang["type_order"]:
        if t not in grouped:
            continue
        print(f"\n{cyan(lang['type_labels'].get(t, t))}")
        for module_id, path in grouped[t]:
            status = green("✓ found") if path else red("✗ not found")
            rel    = str(path) if path else lang["id_to_rel_path"](module_id)
            print(f"  [ ] {rel}  {dim(status)}")

    missing = [(mid, i) for mid, i in deps.items() if i["path"] is None]
    if missing:
        print()
        print(yellow(f"  {len(missing)} file(s) could not be located automatically."))
        print(yellow("  Locate them manually or adjust --src."))


def write_extracted(deps: dict, mapping_dict: dict, options: dict, out_dir: Path, lang: dict,
                    registry: "StringMaskRegistry | None" = None):
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for module_id, info in deps.items():
        if info["path"] is None:
            print(yellow(f"  [skip] {module_id} — source not found"))
            continue

        sanitized = sanitize(info["source"], mapping_dict, options, lang, registry)
        rel_path  = lang["output_rel_path"](module_id, mapping_dict)
        dest      = out_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(sanitized, encoding="utf-8")
        written += 1
        print(green(f"  ✓ {rel_path}"))

    print()
    print(bold(f"  {written} file(s) written to: {out_dir}"))


def write_claude_prompt(deps: dict, out_dir: Path, lang: dict, mapping_dict: dict, test_framework: str = None):
    prompt = lang["prompt_template"](deps, mapping_dict, test_framework)
    prompt_file = out_dir / "CLAUDE_PROMPT.txt"
    prompt_file.write_text(prompt, encoding="utf-8")
    print(green(f"  Prompt template → {prompt_file}"))


def write_reversal_script(mapping_dict: dict, out_dir: Path, lang: dict):
    """Write a platform-aware reversal script for both package and variable mappings."""
    reversed_maps = reverse_mappings(mapping_dict)
    pkg_reversed = reversed_maps.get("package", [])
    var_reversed = reversed_maps.get("variable", [])
    exts = lang["extensions"]

    if len(exts) == 1:
        find_expr = f'-name "*{exts[0]}"'
    else:
        find_expr = "\\( " + " -o ".join(f'-name "*{e}"' for e in exts) + " \\)"

    # Bash version
    bash_lines = [
        "#!/usr/bin/env bash",
        f"# Reverse sanitization mappings on generated test files — {lang['label']}",
        "",
        "rename_files() {",
        "  while IFS= read -r -d '' file; do",
        "    new_path=\"$file\"",
    ]

    for m in pkg_reversed:
        frm = lang["pkg_to_posix"](m["from"])
        to = lang["pkg_to_posix"](m["to"])
        bash_lines.append(f'    new_path=$(printf %s "$new_path" | sed "s|{frm}|{to}|g")')

    for m in var_reversed:
        for pattern, repl in variable_mapping_patterns(m["from"], m["to"]):
            pat = pattern.replace("/", r"\/")
            bash_lines.append(f"    new_path=$(printf %s \"$new_path\" | perl -pe 's/{pat}/{repl}/g')")

    bash_lines.extend([
        "    if [[ \"$new_path\" != \"$file\" ]]; then",
        "      mkdir -p \"$(dirname \"$new_path\")\"",
        "      mv \"$file\" \"$new_path\"",
        "    fi",
        f"  done < <(find . {find_expr} -print0)",
        "}",
        "",
        "rename_files",
        "",
        f"FILES=$(find . {find_expr})",
        "",
    ])

    if pkg_reversed:
        bash_lines.append("# Package/path mappings")
        for m in pkg_reversed:
            frm = m["from"].replace(".", r"\.").replace("/", r"\/")
            to  = m["to"].replace(".", r"\.").replace("/", r"\/")
            bash_lines.append(f'# {m["from"]} → {m["to"]}')
            # perl -pi is portable across GNU/BSD (sed -i and \b are not)
            bash_lines.append(f"perl -pi -e 's/{frm}/{to}/g' $FILES")
        bash_lines.append("")

    if var_reversed:
        bash_lines.append("# Variable/class name mappings (all case/plural/compound variations)")
        for m in var_reversed:
            bash_lines.append(f'# {m["from"]} → {m["to"]}')
            for pattern, repl in variable_mapping_patterns(m["from"], m["to"]):
                pat = pattern.replace("/", r"\/")
                bash_lines.append(f"perl -pi -e 's/{pat}/{repl}/g' $FILES")
        bash_lines.append("")

    bash_file = out_dir / "reverse_sanitize.sh"
    bash_file.write_text("\n".join(bash_lines), encoding="utf-8")
    bash_file.chmod(0o755)

    # PowerShell version
    if len(exts) == 1:
        ps_glob = f'Get-ChildItem -Recurse -Filter "*{exts[0]}"'
    else:
        ps_glob = "Get-ChildItem -Recurse -File -Include " + ",".join(f"*{e}" for e in exts)

    def ps_path_pairs(m):
        if lang["key"] == "spring":
            return [(m["from"].replace(".", os.sep), m["to"].replace(".", os.sep))]
        pairs = [(m["from"], m["to"])]
        alt = (m["from"].replace("/", "\\"), m["to"].replace("/", "\\"))
        if alt != pairs[0]:
            pairs.append(alt)
        return pairs

    ps_lines = [f"# Reverse sanitization mappings on generated test files — {lang['label']}", f"$files = {ps_glob}", "foreach ($f in $files) {", "    $newPath = $f.FullName"]

    for m in pkg_reversed:
        for frm, to in ps_path_pairs(m):
            ps_lines.append(f'    $newPath = $newPath -creplace [regex]::Escape("{frm}"), "{to}"')

    for m in var_reversed:
        for pattern, repl in variable_mapping_patterns(m["from"], m["to"]):
            ps_lines.append(f"    $newPath = $newPath -creplace '{pattern}', '{repl}'")
    ps_lines.extend([
        '    if ($newPath -ne $f.FullName) {',
        '        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $newPath) | Out-Null',
        '        Move-Item -LiteralPath $f.FullName -Destination $newPath',
        '    }',
        '}',
        f"$files = {ps_glob}",
        'foreach ($f in $files) {',
        '    $content = Get-Content $f.FullName -Raw'
    ])

    if pkg_reversed:
        ps_lines.append("    # Package/path mappings")
        for m in pkg_reversed:
            ps_lines.append(f'    # {m["from"]} → {m["to"]}')
            ps_lines.append(f'    $content = $content -creplace [regex]::Escape("{m["from"]}"), "{m["to"]}"')

    if var_reversed:
        ps_lines.append("    # Variable/class name mappings (all case/plural/compound variations)")
        for m in var_reversed:
            ps_lines.append(f'    # {m["from"]} → {m["to"]}')
            for pattern, repl in variable_mapping_patterns(m["from"], m["to"]):
                ps_lines.append(f"    $content = $content -creplace '{pattern}', '{repl}'")

    ps_lines += ["    Set-Content $f.FullName $content", "}"]

    ps_file = out_dir / "reverse_sanitize.ps1"
    ps_file.write_text("\n".join(ps_lines), encoding="utf-8")

    print(green(f"  Bash reversal script  → {bash_file}"))
    print(green(f"  PowerShell reversal   → {ps_file}"))


# ─────────────────────────────────────────────
#  Interactive menu
# ─────────────────────────────────────────────

def prompt_language() -> str:
    keys = list(LANGUAGES.keys())
    print()
    print(bold("Project type"))
    for i, key in enumerate(keys, 1):
        print(f"  {i}. {LANGUAGES[key]['label']}")
    choice = input(f"  Choice [1-{len(keys)}]: ").strip()
    try:
        return keys[int(choice) - 1]
    except (ValueError, IndexError):
        return keys[0]


def prompt_mappings(lang: dict) -> list:
    mappings = []
    print()
    print(bold(lang["pkg_mapping_title"]))
    print(dim(f"  Enter sensitive→safe substitutions ({lang['pkg_mapping_hint']})"))
    print(dim("  Press Enter with an empty 'from' to finish.\n"))
    while True:
        frm = input("  Sensitive string (from): ").strip()
        if not frm:
            break
        to = input("  Safe replacement  (to) : ").strip()
        if to:
            mappings.append({"from": frm, "to": to})
            print(green(f"    Mapped: {frm} → {to}"))
    return mappings


def prompt_variable_mappings() -> list:
    mappings = []
    print()
    print(bold("Variable/Class name mappings"))
    print(dim("  Rename classes and variables with automatic handling of variations"))
    print(dim("  E.g., 'Ingredient' → 'Item' will handle: Ingredient, ingredient, ingredients,"))
    print(dim("        IngredientService, INGREDIENT_TYPE, ingredient_service, ingredient-row, etc."))
    print(dim("  Press Enter with an empty 'from' to finish.\n"))
    while True:
        frm = input("  Original name (from): ").strip()
        if not frm:
            break
        to = input("  New name      (to) : ").strip()
        if to:
            # Show user what variations will be created
            from_vars = generate_name_variations(frm)
            mappings.append({"from": frm, "to": to})
            print(green(f"    Mapped: {frm} → {to}"))
            print(dim(f"      From variations: {', '.join(from_vars[:5])}{'...' if len(from_vars) > 5 else ''}"))
    return mappings


def prompt_options(lang: dict) -> dict:
    print()
    print(bold("Sanitization options"))
    def ask(label, default=True):
        yn = "Y/n" if default else "y/N"
        ans = input(f"  {label} [{yn}]: ").strip().lower()
        return (ans != "n") if default else (ans == "y")

    return {
        "strip_comments": ask("Strip all comments"),
        "strip_javadoc":  ask(f"Remove @author / @since {lang['doc_tag_label']} tags"),
        "mask_strings":   ask("Mask string literals", default=False),
        "strip_loggers":  ask(f"Remove {lang['logger_label']}"),
    }


def prompt_test_framework(lang: dict) -> str:
    if lang["key"] != "react":
        return None
    print()
    print(bold("Test framework"))
    print("  1. Jest + React Testing Library")
    print("  2. Vitest + React Testing Library")
    choice = input("  Choice [1/2]: ").strip()
    return "vitest" if choice == "2" else "jest"


def interactive_trace():
    lang_key = prompt_language()
    lang = LANGUAGES[lang_key]

    print()
    entry_str = input(bold(f"  Path to entry file ({lang['entry_hint']}): ")).strip().strip('"')
    entry_path = Path(entry_str)
    if not entry_path.exists():
        print(red(f"  File not found: {entry_path}"))
        return

    if lang_key == "spring":
        scope = input(bold("  Base package (e.g. com.mycompany.project): ")).strip()
    else:
        scope = input(bold("  Path alias for the src root [@]: ")).strip() or "@"

    # Try to auto-detect src root
    src_guess = ""
    for marker in lang["src_markers"]:
        idx = str(entry_path).find(marker)
        if idx != -1:
            src_guess = str(entry_path)[: idx + len(marker)].rstrip("/\\")
            break

    src_str = input(bold(f"  Source root [{src_guess or lang['default_src']}]: ")).strip().strip('"')
    src_root = Path(src_str or src_guess or lang["default_src"])

    out_str = input(bold("  Output directory [./extracted]: ")).strip().strip('"')
    out_dir = Path(out_str or "./extracted")

    # Mapping file
    default_map = out_dir / "mapping.json"
    map_str = input(bold(f"  Mapping file [{default_map}] (leave blank to define now): ")).strip().strip('"')
    mapping_file = Path(map_str) if map_str else None

    if mapping_file and mapping_file.exists():
        mapping_dict = load_mappings(mapping_file)
        pkg_count = len(mapping_dict.get("package", []))
        var_count = len(mapping_dict.get("variable", []))
        print(green(f"  Loaded {pkg_count} package mapping(s) and {var_count} variable mapping(s) from {mapping_file}"))
    else:
        pkg_mappings = prompt_mappings(lang)
        var_mappings = prompt_variable_mappings()
        mapping_dict = {"package": pkg_mappings, "variable": var_mappings}

    options = prompt_options(lang)
    test_framework = prompt_test_framework(lang)

    print()
    print(bold("Tracing dependencies..."))
    deps = trace(entry_path, scope, src_root, lang)
    print(green(f"  Found {len(deps)} module(s)."))

    print_checklist(deps, lang)

    print()
    confirm = input(bold("  Write sanitized files to output directory? [Y/n]: ")).strip().lower()
    if confirm == "n":
        print(yellow("  Aborted — no files written."))
        return

    print()
    print(bold("Writing sanitized files..."))
    registry = StringMaskRegistry(mapping_dict.get("strings"))
    write_extracted(deps, mapping_dict, options, out_dir, lang, registry)
    write_claude_prompt(deps, out_dir, lang, mapping_dict, test_framework)

    # Save mappings for reversal (language recorded so `reverse` auto-detects it)
    mapping_dict["language"] = lang_key
    mapping_dict["strings"] = registry.to_dict()
    final_map = out_dir / "mapping.json"
    save_mappings(mapping_dict, final_map)
    write_reversal_script(mapping_dict, out_dir, lang)

    print()
    print(bold("═══ Done ═══"))
    print(f"  Extracted files : {out_dir}")
    print(f"  Mapping file    : {final_map}")
    print(f"  Claude prompt   : {out_dir / 'CLAUDE_PROMPT.txt'}")
    print()
    print(dim("  Next steps:"))
    print(dim("  1. Paste each extracted file + CLAUDE_PROMPT.txt into Claude"))
    print(dim("  2. Save the generated tests to ./generated-tests/"))
    print(dim("  3. Run:  python code_extractor.py reverse --mapping mapping.json --dir ./generated-tests"))


def interactive_reverse():
    print()
    map_str = input(bold("  Path to mapping.json: ")).strip().strip('"')
    mapping_file = Path(map_str)
    if not mapping_file.exists():
        print(red(f"  File not found: {mapping_file}"))
        return

    dir_str = input(bold("  Directory containing generated test files: ")).strip().strip('"')
    target_dir = Path(dir_str)
    if not target_dir.exists():
        print(red(f"  Directory not found: {target_dir}"))
        return

    mapping_dict = load_mappings(mapping_file)
    lang = LANGUAGES[get_language(mapping_dict)]
    pkg_count = len(mapping_dict.get("package", []))
    var_count = len(mapping_dict.get("variable", []))
    print(green(f"  Loaded {pkg_count} package mapping(s) and {var_count} variable mapping(s)"))
    print(green(f"  Language: {lang['label']}"))
    print()
    print(bold("Reversing mappings..."))
    apply_reversal(target_dir, mapping_dict, lang)


def interactive_menu():
    print()
    print(bold("╔══════════════════════════════════════════╗"))
    print(bold("║  Code Extractor & Sanitizer (Java/React) ║"))
    print(bold("╚══════════════════════════════════════════╝"))
    print()
    print("  1. Trace dependencies & extract sanitized files")
    print("  2. Reverse sanitization on generated test files")
    print("  3. Exit")
    print()
    choice = input("  Choice [1/2/3]: ").strip()
    if choice == "1":
        interactive_trace()
    elif choice == "2":
        interactive_reverse()
    else:
        print("Bye.")


# ─────────────────────────────────────────────
#  CLI entry point
# ─────────────────────────────────────────────

def cmd_trace(args):
    entry_path = Path(args.entry)
    if not entry_path.exists():
        print(red(f"Entry file not found: {entry_path}"))
        sys.exit(1)

    lang_key = args.lang or infer_language(args.entry)
    lang = LANGUAGES[lang_key]

    if lang_key == "spring" and not args.base:
        print(red("--base is required for Spring Boot (Java) projects"))
        sys.exit(1)
    scope = args.base if lang_key == "spring" else (args.alias or "@")

    src_root = Path(args.src) if args.src else Path(lang["default_src"])
    out_dir  = Path(args.out)

    mapping_dict = {"package": [], "variable": []}
    if args.mapping and Path(args.mapping).exists():
        mapping_dict = load_mappings(Path(args.mapping))
        pkg_count = len(mapping_dict.get("package", []))
        var_count = len(mapping_dict.get("variable", []))
        print(green(f"Loaded {pkg_count} package mapping(s) and {var_count} variable mapping(s) from {args.mapping}"))

    options = {
        "strip_comments": not args.keep_comments,
        "strip_javadoc":  args.strip_javadoc,
        "mask_strings":   args.mask_strings,
        "strip_loggers":  args.strip_loggers,
    }

    print(bold(f"Language: {lang['label']}"))
    print(bold(f"Tracing from: {entry_path}"))
    deps = trace(entry_path, scope, src_root, lang)
    print(green(f"Found {len(deps)} module(s)."))

    print_checklist(deps, lang)

    print()
    print(bold("Writing sanitized files..."))
    registry = StringMaskRegistry(mapping_dict.get("strings"))
    write_extracted(deps, mapping_dict, options, out_dir, lang, registry)
    write_claude_prompt(deps, out_dir, lang, mapping_dict, args.test_framework)

    mapping_dict["language"] = lang_key
    mapping_dict["strings"] = registry.to_dict()
    final_map = out_dir / "mapping.json"
    save_mappings(mapping_dict, final_map)
    write_reversal_script(mapping_dict, out_dir, lang)


def cmd_reverse(args):
    mapping_file = Path(args.mapping)
    target_dir   = Path(args.dir)

    if not mapping_file.exists():
        print(red(f"Mapping file not found: {mapping_file}"))
        sys.exit(1)
    if not target_dir.exists():
        print(red(f"Target directory not found: {target_dir}"))
        sys.exit(1)

    mapping_dict = load_mappings(mapping_file)
    lang = LANGUAGES[get_language(mapping_dict, args.lang)]
    pkg_count = len(mapping_dict.get("package", []))
    var_count = len(mapping_dict.get("variable", []))
    print(green(f"Loaded {pkg_count} package mapping(s) and {var_count} variable mapping(s)"))
    print(green(f"Language: {lang['label']}"))
    apply_reversal(target_dir, mapping_dict, lang)


def main():
    if len(sys.argv) == 1:
        interactive_menu()
        return

    parser = argparse.ArgumentParser(
        description="Code extractor and sanitizer (Spring Boot Java / React)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Interactive mode
  python code_extractor.py

  # Trace and extract (Spring Boot — inferred from the .java entry file)
  python code_extractor.py trace \\
    --entry src/main/java/com/myco/OrderService.java \\
    --base com.myco \\
    --src src/main/java \\
    --out ./extracted \\
    --mapping mapping.json

  # Trace and extract (React — inferred from the .tsx entry file)
  python code_extractor.py trace \\
    --entry src/components/OrderList.tsx \\
    --src src \\
    --out ./extracted \\
    --test-framework vitest

  # Reverse sanitization on generated tests (language read from mapping.json)
  python code_extractor.py reverse \\
    --mapping ./extracted/mapping.json \\
    --dir ./generated-tests
""")

    sub = parser.add_subparsers(dest="command")

    t = sub.add_parser("trace", help="Trace dependencies and extract sanitized files")
    t.add_argument("--entry",          required=True, help="Path to the entry source file")
    t.add_argument("--lang",           choices=list(LANGUAGES), default=None,
                   help="Project language (default: inferred from --entry extension)")
    t.add_argument("--base",           default=None, help="[spring] Base package to trace within (e.g. com.mycompany)")
    t.add_argument("--alias",          default="@",  help="[react] Path alias that maps to the src root (default: @)")
    t.add_argument("--src",            default=None, help="Source root directory (default: src/main/java for spring, src for react)")
    t.add_argument("--out",            default="./extracted",   help="Output directory for sanitized files")
    t.add_argument("--mapping",        default=None,            help="Path to mapping.json (optional)")
    t.add_argument("--test-framework", choices=["jest", "vitest"], default="jest",
                   help="[react] Test framework for the generated prompt (default: jest)")
    t.add_argument("--keep-comments",  action="store_true",     help="Do not strip comments")
    t.add_argument("--strip-javadoc",  action="store_true",     help="Remove @author/@since tags")
    t.add_argument("--mask-strings",   action="store_true",     help="Mask string literals")
    t.add_argument("--strip-loggers",  action="store_true",     help="Remove logger statements")

    r = sub.add_parser("reverse", help="Reverse sanitization mappings on generated test files")
    r.add_argument("--mapping", required=True, help="Path to mapping.json")
    r.add_argument("--dir",     required=True, help="Directory containing generated test files")
    r.add_argument("--lang",    choices=list(LANGUAGES), default=None,
                   help="Override language (default: read from mapping.json)")

    args = parser.parse_args()
    if args.command == "trace":
        cmd_trace(args)
    elif args.command == "reverse":
        cmd_reverse(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
