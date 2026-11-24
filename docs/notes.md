# Notes and pages

Notes use nested pages and editable blocks. Pages support titles, icons, covers, favorites, locking, attachments and sharing. The navigation tree, editor and permission model apply to locally created content as well as imported notes.

## Editing

The block editor supports rich text, inline code, code blocks, lists, headings, tables, columns, links and other supported block types. Markdown is an alternate representation of compatible content. Keep the rich document when a Markdown projection cannot express a feature exactly.

Child pages belong to a hierarchy. Moving and ordering pages preserves identity and updates relationships through versioned operations. Page links and block references follow their supported ownership rules. A collapsed branch or filtered view does not delete its content.

Note databases add typed properties, records, named views, filters and sorting. Their access follows the owning page and space. Duplicating or importing records validates their database relationships instead of copying arbitrary foreign IDs.

## Notes assistant

Open **AI** from a blank personal note or **AI → Open chat** from a saved personal note. Describe the work in chat. The assistant can answer a question, draft a separate note, or propose changes to the current one, including rewriting its entire text. Ask it to refine the proposal, then choose **Save note** or type **OK** to save the version shown. Reject a proposal to discard it. A full rewrite keeps the previous version in history and retains existing page shortcuts and attachments. **AI → Use as a source** starts a chat with the current page selected as its reference.

Set `OPENAI_API_KEY` in the server environment and choose one local account in **Administration → Notes assistant**. Choose **Disabled** to revoke access. Space uses the OpenAI Responses API with `store: false`, with the provider's normal data controls still applying. **Context for OpenAI** shows the note excerpts included when sending a message. A personal note synced from Notion can be selected as a reference. The assistant works with plain text from personal notes and its own suggested content. Shared notes, locked notes, the secret vault, database entries and attachment contents are outside its scope. [OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data)

## Collaboration and access

Shared editing uses the existing authorization and document synchronization protocol. Reconnection, conflicts and access revocation must preserve local drafts where recovery is possible. Temporary editor state is not permission to publish or write after access has been removed.

Publications, ordinary private pages and secret-vault pages have different access and recovery rules. Check the sharing dialog before publishing. A locked page is protected against ordinary accidental editing; it does not replace account or space authorization.

Use [document import](note-import-formats.md) for exported archives, [Notion synchronization](notion-sync.md) for a linked external source, [sharing](sharing.md) for access options and [secret vault](secret-vault.md) for protected personal content.
