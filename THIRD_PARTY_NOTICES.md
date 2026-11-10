# Third-party notices

The project's Elastic License 2.0 applies to its own code. It does not replace the licences of dependencies, fonts, geographic reference data or other third-party materials.

Frontend dependency licence texts and package/source metadata are distributed in `web/public/assets/licenses/` and copied into the web build. In particular, BlockNote is MPL-2.0; LiveKit client is Apache-2.0; KaTeX, Mermaid, Yjs and Lottie use MIT licences. Their original notices are preserved. Source packages and exact versions are specified by `package-lock.json`. After changing dependencies, run `npm ci` and `npm run licenses:update`, then review the notice changes. The web build checks that notices match the installed, pinned versions.

Python dependencies and versions are in `requirements.lock.txt`; installed wheels retain their distribution metadata/licences. The Docker image installs these packages using pip rather than re-licensing their files.

The bundled city index is public GeoNames reference data under CC BY 4.0. See `app/geodata/README.md` and `metadata.json` for attribution, source and checksums.

Application artwork and third-party trademarks have their own rights and attribution requirements. The source-code license does not grant rights in third-party logos or trademarks. Retain applicable attribution when adding or replacing assets.
