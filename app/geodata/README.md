# Public city reference

`cities.sqlite.gz` contains public geographic reference data, not account data or search history. It is generated from the GeoNames cities500 extract (settlements with population over 500 plus administrative seats), including alternate names and IANA time zones. The dated source and database SHA-256 checksums and record count are in `metadata.json`.

Source: https://download.geonames.org/export/dump/cities500.zip
Format: https://download.geonames.org/export/dump/readme.txt
Attribution: GeoNames, https://www.geonames.org/
License: Creative Commons Attribution 4.0, https://creativecommons.org/licenses/by/4.0/
Country display names are generated using Node.js Intl.DisplayNames (ICU/Unicode CLDR).

Rebuild from the project root with `.venv/bin/python scripts/build-city-reference.py`. This downloads public reference files over verified HTTPS, builds a temporary SQLite FTS index, then writes the compressed index and metadata. It does not connect to the application database. Review updated metadata and multilingual searches before committing a refreshed extract.

At runtime the bundled index is unpacked atomically into `DATA_DIR/public-reference/cities-<checksum>.sqlite` and opened read-only. It contains only the public reference, so it is unencrypted; account data and external-provider caches remain encrypted. Queries are never written to this index. No network request or API key is needed for indexed settlements. External geocoding remains a fallback for locations absent from the extract. This reference index does not change the application's database schema or backup format.
