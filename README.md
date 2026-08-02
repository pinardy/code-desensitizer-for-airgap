# Code Desensitizer for Airgap environments

<img src="assets/code-desensitizer-cli.png" alt="Extractor CLI" width="450" />

## Problem Statement

There could be poor performance when generating test cases on-premise due to existing infrastructure’s performance. This could be due to model choice, lack of GPUs, etc.

## Solution

We can utilize online AI coding assistants or agents to generate the test cases instead of relying on the on-premise's coding assistance which may not have good performance.

***Supported project types: Spring Boot (Java) and React (JavaScript/TypeScript). The general approach can be adapted to other languages and frameworks as well.***

***Angular (TypeScript) is also supported, including external component templates and styles.***

## Workflow

1. Desensitize relevant on-premise source code with a custom on-premise script
2. Bring out desensitized source code to internet
3. Use online AI coding assistants/agents to generate test cases
4. Bring generated test back into airgap env
5. Run `reverse` to restore the original names

## Requirements

- Python 3.9 or newer
- No third-party packages are required (tests use `pytest`)

## Usage

The script supports an interactive menu and two CLI subcommands:

```bash
# Interactive mode
python code_extractor.py

# CLI subcommands
python code_extractor.py trace --entry path/to/MyService.java --base com.mycompany --src src/main/java --out ./extracted
python code_extractor.py trace --entry src/components/MyWidget.tsx --src src --out ./extracted
python code_extractor.py trace --entry src/app/widgets/widget.component.ts --lang angular --src src --out ./extracted
python code_extractor.py reverse --mapping ./extracted/mapping.json --dir ./generated-tests
```

The project language is inferred from the entry file's extension (`.java` → Spring Boot, `.js/.jsx/.ts/.tsx` → React) and can be overridden with `--lang spring|react`.

Angular is inferred from conventional Angular filenames such as `.component.ts`, `.service.ts`, `.module.ts`, `.directive.ts`, `.pipe.ts`, `.guard.ts`, `.resolver.ts`, `.interceptor.ts`, and `.routes.ts`. For other Angular `.ts` files, pass `--lang angular` explicitly.

> **Caveat:** some of these conventions (`.service.ts`, `.module.ts`) are also used by NestJS backends, which will therefore be inferred as Angular. Pass `--lang react` (or the appropriate language) to override when tracing a non-Angular TypeScript project.

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

Angular appears as a third project type and uses path aliases, JSDoc, and `console.*` handling like other TypeScript projects.

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

# Angular
python code_extractor.py trace \
	--entry src/app/features/payroll/payroll-list.component.ts \
	--lang angular \
	--src src \
	--out ./extracted \
	--mapping mapping.json
```

Arguments:

- `--entry`: Path to the entry source file
- `--lang`: `spring` or `react`, defaulting to inference from the entry file extension
- `--lang angular`: Select Angular explicitly when its framework cannot be inferred from a conventional filename
- `--base`: (Spring) Base package to trace within — required for Spring Boot
- `--alias`: (React) Path alias that maps to the source root, defaulting to `@`
- `--alias`: (Angular) Path alias that maps to the source root, defaulting to `@`
- `--src`: Source root directory, defaulting to `src/main/java` (Spring) or `src` (React)
- `--src`: For Angular, the source root defaults to `src`
- `--out`: Output directory for sanitized files, defaulting to `./extracted`
- `--mapping`: Optional `mapping.json` file to reuse existing mappings
- `--map-package FROM=TO`: Add a package/path mapping inline (repeatable)
- `--map-var FROM=TO`: Add a variable/class mapping inline (repeatable)
- `--test-framework`: (React) `jest` or `vitest` for the generated prompt, defaulting to `jest`
- `--dry-run`: Show what would be written (with per-file substitution counts) without writing anything
- `--keep-comments`: Keep comments instead of stripping them
- `--keep-doc-tags`: Keep `@author` / `@since` Javadoc/JSDoc tags instead of stripping them
- `--keep-loggers`: Keep logger statements (`log.*` for Java, `console.*` for React) instead of stripping them
- Angular also uses `--keep-loggers` for `console.*` statements
- `--mask-strings`: Replace string literals with `STR_n` placeholders (import specifiers are never masked); the originals are recorded in `mapping.json` and restored by `reverse`

> **Changed defaults:** comments, doc tags, and loggers are now stripped by default on the CLI (matching interactive mode). The old `--strip-javadoc` / `--strip-loggers` flags are accepted as no-ops for backward compatibility.

How dependencies are traced:

- **Spring Boot**: `import` statements that stay within the `--base` package are followed; classes are grouped as controllers/services/models/repositories/utils/enums.
- **React**: relative imports (`./`, `../`) and alias imports (`@/...`) are resolved with extension inference (`.tsx/.ts/.jsx/.js`) and `index.*` resolution; bare modules (`react`, `axios`, ...) and asset imports (`.css`, `.svg`, ...) are skipped. Modules are grouped as components/hooks/contexts/api/types/utils.
- **Angular**: TypeScript imports are resolved like React imports. In addition, literal `templateUrl`, `styleUrl`, and `styleUrls` references are followed so external component templates and styles travel with the TypeScript dependency graph. Files are grouped as components/templates/styles/services/modules/routing/directives/pipes/guards/resolvers/interceptors/state/types/utils.

The trace step writes these artifacts into the output directory:

- Sanitized source files
- `mapping.json` (includes the project language so `reverse` auto-detects it, and — when `--mask-strings` is used — the masked string literals for restoration)
- `CLAUDE_PROMPT.txt`
- `REVERSE_INSTRUCTIONS.txt` (the exact `reverse` command to run later)

> **Security note:** `mapping.json` maps the sanitized names back to the sensitive originals. Keep it inside the airgap — never share it alongside the sanitized files. The repository's `.gitignore` excludes `extracted/` and `mapping.json` for this reason.

<img src="assets/code-desensitizer-results.png" alt="Extractor results" width="650" />


### 3. Generate tests externally

Copy the extracted sanitized source files and the generated `CLAUDE_PROMPT.txt` into your online coding assistant or agent of choice. For Spring Boot the prompt asks for JUnit 5 tests with Mockito and AssertJ; for React it asks for Jest or Vitest tests with React Testing Library.

For Angular, the generated prompt asks for Jasmine tests using Angular TestBed and includes referenced component templates and styles in the extraction set.

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
- `--dry-run`: Preview every rename and content change (with substitution and string-restore counts) without writing anything
- `--no-backup`: Skip the automatic timestamped backup copy of the target directory
- `--force`: Re-run on a directory that was already reversed with this mapping

Safety behavior:

- Before changing anything, `reverse` copies the whole target directory to `<dir>.backup-<timestamp>` (disable with `--no-backup`).
- String literals masked by `--mask-strings` are restored first, then names are un-renamed — so sensitive values inside strings come back exactly.
- A `.code_extractor_reversed.json` marker records the mapping fingerprint; running `reverse` twice with the same mapping is refused (double-applying renames could corrupt names) unless you pass `--force`.

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

**1. Define the mappings** — interactively, in a `mapping.json`, or inline with `--map-package` / `--map-var`:

```json
{
  "package":  [{ "from": "features/payroll", "to": "features/feature1" }],
  "variable": [{ "from": "Ingredient", "to": "Item" }]
}
```

**2. Trace and extract** (language is inferred from the `.tsx` extension; add `--dry-run` first to preview):

```bash
python code_extractor.py trace \
	--entry src/features/payroll/components/IngredientList.tsx \
	--src src \
	--out ./extracted \
	--mapping mapping.json \
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
├── REVERSE_INSTRUCTIONS.txt         # the reverse command to run later
└── mapping.json                     # ← keep inside the airgap: contains the original names
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

## Example walkthrough (Angular)

Suppose the airgapped Angular project contains a sensitive `payroll` feature:

```
src/
└── app/payroll/
    ├── PayrollList.component.ts       # entry
    ├── PayrollList.component.html     # templateUrl dependency
    ├── PayrollList.component.scss     # styleUrls dependency
    ├── Payroll.service.ts
    ├── PayrollStatus.pipe.ts
    └── Payroll.model.ts
```

The component imports its TypeScript dependencies and references its external resources through Angular metadata:

```ts
@Component({
  selector: 'app-payroll-list',
  templateUrl: './PayrollList.component.html',
  styleUrls: ['./PayrollList.component.scss'],
})
export class PayrollListComponent {}
```

**1. Define Angular path and identifier mappings:**

```json
{
  "package":  [{ "from": "app/payroll", "to": "app/feature1" }],
  "variable": [{ "from": "Payroll", "to": "Widget" }]
}
```

The variable mapping covers Angular class names, selectors, template bindings, filenames, and common case variations. For example, `PayrollService`, `payrollStatus`, and `app-payroll-list` become `WidgetService`, `widgetStatus`, and `app-widget-list`.

**2. Preview and extract the Angular feature:**

```bash
python code_extractor.py trace \
	--entry src/app/payroll/PayrollList.component.ts \
	--lang angular \
	--src src \
	--out ./extracted \
	--mapping mapping.json \
	--dry-run

python code_extractor.py trace \
	--entry src/app/payroll/PayrollList.component.ts \
	--lang angular \
	--src src \
	--out ./extracted \
	--mapping mapping.json
```

`--lang angular` is optional for conventional Angular filenames such as `.component.ts`, but is recommended when the entry file has a generic `.ts` name.

The Angular tracer follows:

- Relative and configured alias TypeScript imports.
- Barrel exports and literal dynamic imports handled by the TypeScript resolver.
- Literal `templateUrl`, `styleUrl`, and `styleUrls` references below the source root.

The sanitized output preserves the Angular feature structure:

```
extracted/
├── app/feature1/
│   ├── WidgetList.component.ts
│   ├── WidgetList.component.html
│   ├── WidgetList.component.scss
│   ├── Widget.service.ts
│   ├── WidgetStatus.pipe.ts
│   └── Widget.model.ts
├── CLAUDE_PROMPT.txt                 # Jasmine + Angular TestBed prompt
├── REVERSE_INSTRUCTIONS.txt
└── mapping.json                      # keep inside the airgap
```

Angular TypeScript comments and `console.*` statements follow the same CLI options as other source types. HTML comments and component stylesheet comments are also stripped by default.

When `--mask-strings` is enabled, ordinary TypeScript literals are replaced with reversible `STR_n` placeholders. Angular *structural* strings are kept readable after applying configured mappings, but only when they appear inside a real Angular metadata decorator (`@Component`, `@Directive`, `@Pipe`, `@Injectable`, `@NgModule`) or property decorator (`@Input`, `@Output`, ...). Inside those spans the following are preserved:

- Component selectors.
- Inline templates and styles (`template`, `styles`, `styleUrls`).
- `templateUrl`, `styleUrl`, and `styleUrls` paths.
- Route paths and redirects declared in the decorator.
- Pipe names, `providedIn`, animation DSL, and common decorator aliases.

This preserves the relationships between the component, template, stylesheet, routes, directives, and pipes for external analysis. The exemptions are deliberately scoped to decorator context: identically shaped keys or calls in ordinary code — `{ name: '...' }`, `db.query('...')`, `cond ? name : '...'` — are masked like any other literal, so masking never fails open on everyday TypeScript.

Note that `--mask-strings` applies only to TypeScript (`.ts`) files. It does **not** apply to `.html` templates or `.css`/`.scss`/`.sass`/`.less`/`.styl` stylesheets at all — literal text and attributes in those files are transformed through explicit mappings rather than blanket string masking, so sensitive free text there is not automatically masked.

**3. Generate Angular tests externally.** Carry out the sanitized Angular files and `CLAUDE_PROMPT.txt`, but keep `mapping.json` inside the airgap. The prompt requests isolated Jasmine tests using Angular TestBed and mocks for injected collaborators.

**4. Restore original terminology inside the airgap:**

```bash
python code_extractor.py reverse \
	--mapping ./extracted/mapping.json \
	--dir ./generated-tests
```

Angular reversal processes `.ts`, `.html`, `.css`, `.scss`, `.sass`, `.less`, and `.styl` files. It restores content and paths, creates a timestamped backup by default, and writes the same reversal marker used by the other project types.

## Workflow Summary

1. Run `trace` to extract and sanitize the relevant source code.
2. Send the sanitized source and prompt file to an online AI assistant.
3. Save the generated tests in a local directory.
4. Run `reverse` to restore the original names in the generated tests.

## mapping.json schema (v2)

```json
{
  "version": 2,
  "language": "spring",
  "package":  [{ "from": "com.classified", "to": "com.example" }],
  "variable": [{ "from": "Ingredient", "to": "Item" }],
  "strings":  { "STR_0": "\"jdbc:oracle:thin:@prod-db:1521\"" }
}
```

- `package`: boundary-aware substitutions — Java packages like `com.classified → com.example`, or React path segments like `features/payroll → features/feature1`. `com.classified` matches inside `com.classified.service` and `"com.classified"`, but never inside `com.classified2`.
- For Angular, `package` mappings operate on source-tree path segments such as `app/payroll → app/feature1`.
- `variable`: name mappings that automatically cover PascalCase, camelCase, snake_case, UPPER_SNAKE, kebab-case, plural/singular, and compound identifiers such as `IngredientService`, `INGREDIENT_TYPE`, or `ingredient-row.tsx`.
- `strings`: written by `--mask-strings` — maps each `STR_n` placeholder back to the original literal so `reverse` can restore it (in any quote style the generated tests use).
- Older formats (a bare list, or a dict without `version`/`strings`) still load and are upgraded on save.

## Notes

- The extractor traces imports that stay within the provided base package (Spring, dot-bounded so `com.myco` does not capture `com.myco2.*`) or resolve inside the source root (React).
- Angular TypeScript imports also resolve inside the source root; component metadata adds literal template and stylesheet dependencies.
- All renames are applied in a single pass: replacements are never re-scanned by other mappings, so sanitize and reverse are idempotent and independent of mapping order. Conflicting mapping sets (duplicates, self-maps, one mapping's output overlapping another's input) are reported as warnings before writing.
- Names are intentionally replaced inside string literals and comments too — a sensitive name must not survive anywhere in the sanitized output.
- Older `mapping.json` files without a `language` field are treated as Spring Boot.
- If a source file cannot be located automatically, the script reports it during extraction.
- Non-UTF-8 source files are read with replacement characters and a loud warning (reversal may not restore such files exactly).

## Development

```bash
python -m pytest test_code_extractor.py -q
```

## Limitations

- **JS regex literals** are not understood by the comment/string scanner — a `//` or quote inside one (e.g. `/foo\/bar/`) can confuse comment stripping. Rare in React code.
- **Template literals with `${...}` interpolation** are never masked by `--mask-strings`.
- **Multi-line logger calls** (`console.log(...)` or `log.info(...)` spanning lines) are not stripped.
- **Path aliases**: only a single alias (default `@`) mapping to the source root is supported — tsconfig `paths` entries and monorepo workspace packages (`@myco/ui`) are treated as external and skipped.
- **Dynamic imports** with non-literal arguments (`import(someVar)`) cannot be resolved.
- **CSS/asset files** are skipped entirely — CSS class names and asset filenames are not sanitized (except where they appear as strings in the traced source).
- **Java text blocks** (`"""..."""`) and char literals are not masked by `--mask-strings`.
- **Angular structural strings** (`selector`, templates/styles, component resource paths, and route paths) are renamed but intentionally not replaced with `STR_n`; masking them would break the relationships a model needs to analyse. Literal text and attributes in external HTML/stylesheets are also handled by explicit term mappings rather than blanket string masking.
- **Angular metadata resolution** follows literal component resource paths only. Computed decorator metadata and template-only dependencies introduced indirectly through an NgModule are not discovered unless their TypeScript files are reachable through imports.
- **Reversal is heuristic**: if an AI-generated test invents an identifier that happens to collide with a mapping's replacement name, `reverse` will rename it too. Use `--dry-run` to preview; a backup is always taken by default.
