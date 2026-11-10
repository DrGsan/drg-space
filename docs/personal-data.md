# Personal export and import

Personal transfer moves supported data belonging to the current account. It is distinct from a full administrative backup and from importing a document archive into Notes.

Exports use a password-protected `.drgpersonal` archive. Supported ordinary notes, hierarchy, attachments, applicable settings and personal module data are included according to the export selection. Shared or external records are governed by their ownership and transfer rules. Secret-vault content and grants are excluded and reported before export.

To import, choose the archive, enter its password and review the proposed contents and warnings. The application validates authenticated archive structure, paths, object sizes and relationships before changing data. Supported database changes apply transactionally. Imported objects receive destination identities and encryption; the archive does not supply the installation's live keys.

Conflicts and unsupported items must be reviewed rather than treated as a complete transfer. Imported content does not acquire broader sharing permissions automatically. External integration credentials, live sessions and administrative unlock grants are not a portable login mechanism.

Keep the archive password separately and retain the file until the imported data has been checked. Downloaded archives are outside the application's cleanup policy. Use [full backup and restore](admin-backups.md) for whole-installation recovery and [note import formats](note-import-formats.md) for Notion or Markdown document imports.
