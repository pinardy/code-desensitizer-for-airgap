# Code Desensitizer for Airgap environments

<img src="assets/spring-extractor-cli.png" alt="Extractor CLI" width="450" />

## Problem Statement

There could be poor performance when generating test cases on-premise due to existing infrastructure’s performance. This could be due to model choice, lack of GPUs, etc.

## Solution

We can utilize online AI coding assistants or agents to generate the test cases instead of relying on the on-premise's coding assistance which may not have good performance.

***Supported project types: Spring Boot (Java) and React (JavaScript/TypeScript). The general approach can be adapted to other languages and frameworks as well.***

## Workflow

1. Desensitize relevant on-premise source code with a custom on-premise script
2. Bring out desensitized source code to internet
3. Use online AI coding assistants/agents to generate test cases
4. Bring generated test back into airgap env
5. Run reverse sanitization script

## Requirements

- Python 3.7 or newer
- No third-party packages are required

## Usage

The script supports an interactive menu and two CLI subcommands:

```bash
# Interactive mode
python code_extractor.py

# CLI subcommands
python code_extractor.py trace --entry path/to/MyService.java --base com.mycompany --src src/main/java --out ./extracted
python code_extractor.py trace --entry src/components/MyWidget.tsx --src src --out ./extracted
python code_extractor.py reverse --mapping ./extracted/mapping.json --dir ./generated-tests
```

The project language is inferred from the entry file's extension (`.java` → Spring Boot, `.js/.jsx/.ts/.tsx` → React) and can be overridden with `--lang spring|react`.

### 1. Interactive mode

Run the script without arguments:

```bash
python code_extractor.py
```

Then choose one of the following:

- `1` to trace dependencies and extract sanitized source files
- `2` to reverse sanitization on generated test files
- `3` to exit

The trace flow first asks which project type you are extracting (Spring Boot or React), then adapts its prompts (base package vs. path alias, Javadoc vs. JSDoc, logger vs. `console.*`, and — for React — the test framework for the generated prompt).

### 2. Trace and extract

Use the `trace` command to collect the entry file and its internal dependencies, sanitize the source, and write the extracted files to an output directory.

```bash
# Spring Boot
python code_extractor.py trace \
	--entry src/main/java/com/myco/OrderService.java \
	--base com.myco \
	--src src/main/java \
	--out ./extracted \
	--mapping mapping.json

# React
python code_extractor.py trace \
	--entry src/features/payroll/components/OrderList.tsx \
	--src src \
	--out ./extracted \
	--mapping mapping.json \
	--test-framework vitest
```

Arguments:

- `--entry`: Path to the entry source file
- `--lang`: `spring` or `react`, defaulting to inference from the entry file extension
- `--base`: (Spring) Base package to trace within — required for Spring Boot
- `--alias`: (React) Path alias that maps to the source root, defaulting to `@`
- `--src`: Source root directory, defaulting to `src/main/java` (Spring) or `src` (React)
- `--out`: Output directory for sanitized files, defaulting to `./extracted`
- `--mapping`: Optional `mapping.json` file to reuse existing mappings
- `--test-framework`: (React) `jest` or `vitest` for the generated prompt, defaulting to `jest`
- `--keep-comments`: Keep comments instead of stripping them
- `--strip-javadoc`: Remove `@author` and `@since` Javadoc/JSDoc tags
- `--mask-strings`: Replace string literals with placeholders (import specifiers are never masked)
- `--strip-loggers`: Remove logger statements (`log.*` for Java, `console.*` for React)

How dependencies are traced:

- **Spring Boot**: `import` statements that stay within the `--base` package are followed; classes are grouped as controllers/services/models/repositories/utils/enums.
- **React**: relative imports (`./`, `../`) and alias imports (`@/...`) are resolved with extension inference (`.tsx/.ts/.jsx/.js`) and `index.*` resolution; bare modules (`react`, `axios`, ...) and asset imports (`.css`, `.svg`, ...) are skipped. Modules are grouped as components/hooks/contexts/api/types/utils.

The trace step writes these artifacts into the output directory:

- Sanitized source files
- `mapping.json` (includes the project language so `reverse` auto-detects it)
- `CLAUDE_PROMPT.txt`
- `reverse_sanitize.sh`
- `reverse_sanitize.ps1`

<img src="assets/spring-extractor-results.png" alt="Extractor results" width="650" />


### 3. Generate tests externally

Copy the extracted sanitized source files and the generated `CLAUDE_PROMPT.txt` into your online coding assistant or agent of choice. For Spring Boot the prompt asks for JUnit 5 tests with Mockito and AssertJ; for React it asks for Jest or Vitest tests with React Testing Library.

### 4. Reverse sanitization

After you bring the generated tests back into the airgap environment, use the `reverse` command to restore the original names:

```bash
python code_extractor.py reverse \
	--mapping ./extracted/mapping.json \
	--dir ./generated-tests
```

Arguments:

- `--mapping`: Path to the saved `mapping.json`
- `--dir`: Directory containing the generated test files
- `--lang`: Optional override; normally the language is read from `mapping.json`

## Example walkthrough (React)

Suppose the airgapped project contains a sensitive feature called `payroll` with an `Ingredient` domain:

```
src/
├── features/payroll/
│   ├── components/
│   │   ├── IngredientList.tsx      # entry — imports the files below
│   │   └── ingredient-row.tsx
│   ├── hooks/useIngredient.ts
│   ├── api/ingredientApi.ts
│   └── types/index.ts
└── utils/format.ts                  # imported via the @/ alias
```

**1. Define the mappings** (interactively, or in a `mapping.json`):

```json
{
  "package":  [{ "from": "features/payroll", "to": "features/feature1" }],
  "variable": [{ "from": "Ingredient", "to": "Item" }]
}
```

**2. Trace and extract** (language is inferred from the `.tsx` extension):

```bash
python code_extractor.py trace \
	--entry src/features/payroll/components/IngredientList.tsx \
	--src src \
	--out ./extracted \
	--mapping mapping.json \
	--strip-loggers \
	--test-framework vitest
```

The tool follows the relative, alias (`@/utils/format`), and barrel (`../types` → `types/index.ts`) imports, skips `react`/`axios` and `./styles.css`, and writes a fully renamed tree — one variable mapping covers every variation automatically:

```
extracted/
├── features/feature1/
│   ├── components/ItemList.tsx      # IngredientList → ItemList
│   ├── components/item-row.tsx      # ingredient-row → item-row (kebab-case)
│   ├── hooks/useItem.ts             # useIngredient → useItem (hook compound)
│   ├── api/itemApi.ts               # ingredientApi → itemApi
│   └── types/index.ts               # INGREDIENT_LIMIT → ITEM_LIMIT
├── utils/format.ts
├── CLAUDE_PROMPT.txt                # ready-made prompt (Vitest + React Testing Library)
├── mapping.json                     # ← keep these three inside the airgap:
├── reverse_sanitize.sh              # ← they contain the original names
└── reverse_sanitize.ps1             # ←
```

For example, `IngredientList.tsx` comes out as:

```tsx
import { ItemRow } from './item-row';
import { useItem } from '../hooks/useItem';
import { Item } from '../types';

export function ItemList() {
  const { items, loading } = useItem();
  ...
```

**3. Generate tests online.** Carry out only the sanitized source files and `CLAUDE_PROMPT.txt`, paste them into the online assistant, and save the tests it writes (e.g. `ItemList.test.tsx`) under `./generated-tests/features/feature1/components/`.

**4. Reverse inside the airgap** (language is read from `mapping.json`):

```bash
python code_extractor.py reverse \
	--mapping ./extracted/mapping.json \
	--dir ./generated-tests
```

The test is renamed to `features/payroll/components/IngredientList.test.tsx` and every identifier is restored — `ItemRow` → `IngredientRow`, `useItem` → `useIngredient`, `items` → `ingredients`, `ITEM_LIMIT` → `INGREDIENT_LIMIT` — ready to run against the real code.

The Spring Boot flow is identical, except the entry file is a `.java` class, `--base com.mycompany` bounds the import tracing, and `package` mappings are Java packages (e.g. `com.classified → com.example`).

## Workflow Summary

1. Run `trace` to extract and sanitize the relevant source code.
2. Send the sanitized source and prompt file to an online AI assistant.
3. Save the generated tests in a local directory.
4. Run `reverse` to restore the original names in the generated tests.

## Notes

- The extractor traces imports that stay within the provided base package (Spring) or resolve inside the source root (React).
- `mapping.json` has two mapping lists: `package` (plain substitutions — Java packages like `com.classified → com.example`, or React path segments like `features/payroll → features/feature1`) and `variable` (name mappings that automatically cover PascalCase, camelCase, snake_case, UPPER_SNAKE, kebab-case, plural/singular, and compound identifiers such as `IngredientService`, `INGREDIENT_TYPE`, or `ingredient-row.tsx`).
- The generated `reverse_sanitize.sh` / `.ps1` scripts apply the same variation-aware reversal as `python code_extractor.py reverse` (the bash script uses `perl`, which is available on virtually all Linux/macOS systems).
- Older `mapping.json` files without a `language` field are treated as Spring Boot.
- If a source file cannot be located automatically, the script reports it during extraction.

## Limitations

- **JS regex literals** are not understood by the comment/string scanner — a `//` or quote inside one (e.g. `/foo\/bar/`) can confuse comment stripping. Rare in React code.
- **Template literals with `${...}` interpolation** are never masked by `--mask-strings`.
- **Multi-line logger calls** (`console.log(...)` or `log.info(...)` spanning lines) are not stripped.
- **Path aliases**: only a single alias (default `@`) mapping to the source root is supported — tsconfig `paths` entries and monorepo workspace packages (`@myco/ui`) are treated as external and skipped.
- **Dynamic imports** with non-literal arguments (`import(someVar)`) cannot be resolved.
- **CSS/asset files** are skipped entirely — CSS class names and asset filenames are not sanitized (except where they appear as strings in the traced source).
- The PowerShell reversal script is generated but has not been exercised on a live PowerShell.
