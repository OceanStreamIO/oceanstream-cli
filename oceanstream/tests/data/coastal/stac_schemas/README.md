# Offline STAC schemas

`index.json` maps each canonical schema URL to its unchanged JSON document, downloaded on 2026-09-15. Tests load these resources into a local JSON Schema registry; network access is not needed. The declared versions are STAC 1.0.0, Processing 1.1.0 and EO 1.1.0, with their referenced GeoJSON and JSON Schema draft-07 documents.

Upstream sources and licensing:

- [STAC specification](https://github.com/radiantearth/stac-spec/tree/v1.0.0), Apache-2.0.
- [Processing extension](https://github.com/stac-extensions/processing/tree/v1.1.0), Apache-2.0.
- [EO extension](https://github.com/stac-extensions/eo/tree/v1.1.0), Apache-2.0.
- [GeoJSON schemas](https://github.com/geojson/schema), upstream public schema documents.
- [JSON Schema draft-07](https://json-schema.org/draft-07/schema), JSON Schema specification license.

Schema documents are test fixtures, excluded from the library wheel. Update versions and validate all declared extensions together when changing the STAC emitter.
