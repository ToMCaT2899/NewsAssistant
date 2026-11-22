import prettier from "eslint-config-prettier";
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import vue from "eslint-plugin-vue";

export default [
  { ignores: ["dist/**", "node_modules/**"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...vue.configs["flat/recommended"],
  {
    files: ["**/*.vue"],
    languageOptions: {
      parserOptions: { parser: tseslint.parser },
      globals: {
        window: "readonly",
        crypto: "readonly",
        setTimeout: "readonly",
        clearTimeout: "readonly",
      },
    },
    rules: {
      "vue/html-self-closing": "off",
      "vue/max-attributes-per-line": "off",
    },
  },
  {
    files: ["src/App.vue"],
    rules: { "vue/multi-word-component-names": "off" },
  },
  prettier,
];
