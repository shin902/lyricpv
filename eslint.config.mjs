import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: ["node_modules/**", "coverage/**", "dist/**", "graphify-out/**"],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["sdk/**/*.{js,mjs,ts}"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
    rules: {
      "array-callback-return": ["error", { checkForEach: true }],
    },
  },
);
