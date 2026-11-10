# Localization

The interface supports Russian (`ru`) and English (`en`). `locales/languages.json` defines the languages, locale identifiers and default language, currently `ru`. Interface language and documentation language are separate: maintained repository documentation is English.

Translation namespaces live under `locales/<language>/`. Generated `locales/resources.js` supplies the client catalog. Client components use `tText`, `tHtml` and the shared i18n helpers; server responses use the server localization layer. Preserve placeholders, plural forms and escaping rules when changing a key.

Store machine-readable values, statuses and identifiers independently of translated display labels. Existing API compatibility must not depend on the selected interface language. Translate new user-visible strings in both catalogs and avoid duplicating whole screens for each language.

```sh
npm run i18n:check
npm run i18n:resources
npm run build
node --test tests/i18n.test.mjs
```

The checks compare namespaces, keys, interpolation and source coverage. Regenerate resources after catalog changes and inspect the resulting diff. Test formatting, fallback behavior, translated menus, empty states and validation errors in both languages. English documentation should describe controls by their English labels or stable API identifiers.
