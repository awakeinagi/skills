// Flat config (ESLint 9). Run: npm run lint
//
// Docstring policy mirrors the Python side (AGENTS.md § Documentation
// standard): every exported symbol and React component carries a TSDoc
// block. Types live in the signature, never restated in prose — hence
// `require-param-type` / `require-returns-type` are off.
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import jsdoc from "eslint-plugin-jsdoc";

export default tseslint.config(
  {
    ignores: [
      "node_modules/**",
      // Vite build output — committed, but not ours to lint.
      "../../skills/wysiwyg-grilling/scripts/web/**",
    ],
  },

  js.configs.recommended,
  ...tseslint.configs.recommended,
  jsdoc.configs["flat/recommended-typescript"],

  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: {
      parserOptions: { ecmaFeatures: { jsx: true } },
      globals: {
        window: "readonly",
        document: "readonly",
        fetch: "readonly",
        console: "readonly",
        setTimeout: "readonly",
        clearTimeout: "readonly",
        setInterval: "readonly",
        clearInterval: "readonly",
        requestAnimationFrame: "readonly",
        Blob: "readonly",
        URL: "readonly",
        HTMLElement: "readonly",
      },
    },
    plugins: { "react-hooks": reactHooks, jsdoc },
    settings: { jsdoc: { mode: "typescript" } },
    rules: {
      ...reactHooks.configs.recommended.rules,

      // ---- documentation ----------------------------------------------
      "jsdoc/require-jsdoc": [
        "error",
        {
          publicOnly: true,
          require: {
            FunctionDeclaration: true,
            ArrowFunctionExpression: true,
            FunctionExpression: true,
            ClassDeclaration: true,
            MethodDefinition: true,
          },
          contexts: ["TSInterfaceDeclaration", "TSTypeAliasDeclaration"],
        },
      ],
      "jsdoc/require-description": "error",
      // Destructured props would demand `@param root0.elements`; the props
      // type documents those fields instead.
      "jsdoc/require-param": ["error", { checkDestructured: false }],
      "jsdoc/require-param-description": "error",
      "jsdoc/require-returns": "error",
      "jsdoc/require-returns-description": "error",
      // The TS signature is the source of truth for types.
      "jsdoc/require-param-type": "off",
      "jsdoc/require-returns-type": "off",
      "jsdoc/no-types": "error",

      // ---- typing ------------------------------------------------------
      // 68 pre-existing `any`s (AGENTS.md § Known debt). Warn now so new
      // ones are visible; raise to "error" once api.ts is typed.
      "@typescript-eslint/no-explicit-any": "warn",
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
      "@typescript-eslint/consistent-type-imports": "error",
      eqeqeq: ["error", "smart"],
      "no-var": "error",
      "prefer-const": "error",
    },
  },

  // -------------------------------------------------------------------
  // DEBT BURN-DOWN — mirrors [tool.ruff.lint.per-file-ignores] in
  // pyproject.toml. These are the files that predate the standard, so the
  // documentation rules are demoted to warnings *here only*. Any new file
  // gets them as errors. Delete a file from this list once `npm run lint`
  // is clean for it; delete the whole block when the list empties.
  //
  // Remaining at time of writing (see AGENTS.md § Known debt):
  //   jsdoc/require-jsdoc      6    jsdoc/require-returns    5
  //   jsdoc/multiline-blocks   6    no-unused-vars           2
  //   no-explicit-any        101    react-hooks/deps        14
  // -------------------------------------------------------------------
  {
    files: [
      "src/App.tsx",
      "src/api.ts",
      "src/main.tsx",
      "src/components/HistoryGraph.tsx",
      "src/components/QuestionUI.tsx",
      "src/components/Rail.tsx",
      "src/components/Thumb.tsx",
    ],
    rules: {
      "jsdoc/require-jsdoc": "warn",
      "jsdoc/require-returns": "warn",
      "jsdoc/require-param": "warn",
      "jsdoc/require-description": "warn",
      "@typescript-eslint/no-unused-vars": "warn",
      "@typescript-eslint/consistent-type-imports": "warn",
    },
  },

  // Node-side build script.
  {
    files: ["scripts/**/*.mjs", "vite.config.ts"],
    rules: { "jsdoc/require-jsdoc": "off" },
  },
);
