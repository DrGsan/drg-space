# Note import formats

The import dialog accepts the supported archive and document formats and shows a preview before adding content. Imports create notes and resources through the normal storage and authorization paths.

| Export format | Useful for | Important limits |
| --- | --- | --- |
| Notion HTML with subpages and files | Page hierarchy, rich blocks, artwork and attachments | Missing external resources cannot be reconstructed from the archive |
| Notion Markdown/CSV | Portable text and database rows | Exported text does not contain every layout, view or decoration setting |
| Outline and generic Markdown | Supported exported documents and attachments | Tool-specific metadata and plugin behavior depend on the actual export |

Use a complete export with subpages and files when preserving layout is important. Select the ZIP, wait for archive inspection, review its structure and warnings, then confirm the import. The server validates paths, limits and block structures; archive inspection does not execute embedded scripts.

Internal links are resolved to imported pages where possible. External or unsupported targets remain identifiable links. Missing assets and unsupported structures are reported rather than silently replaced with successful results.

Compare a fresh import with the source at a similar content width, checking hierarchy, columns, code, lists, tables, links, icons, covers and attachments. Differences caused by absent export data remain limitations of that export. See [notes](notes.md) and [Notion synchronization](notion-sync.md).
