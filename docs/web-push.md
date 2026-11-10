# Web Push notifications

Web Push is optional and depends on browser support, HTTPS, service-worker registration and user permission. Configure the external origin with `PUBLIC_URL`. `WEB_PUSH_ENABLED=false` disables sending without removing the rest of the workspace.

Enable notifications from the account settings in a supported browser or installed Web App. Permission requests are initiated by the user's action. Denied or unavailable permission must be shown accurately; the application cannot bypass an operating-system or browser restriction.

Subscriptions and notification records are associated with the account and installation. Delivery and retries are bounded. Push is a notification channel, not an independent source of chat-read state.

Reading messages in the conversation reconciles associated notifications and supported delivered notifications. Opening the notification list, marking a notification read or dismissing a push does not by itself mark source messages read. A notification link must still pass the normal content and account access checks.

When diagnosing delivery, inspect permission, subscription, service-worker state, external origin, server connectivity and browser background restrictions. Never include subscription credentials, tokens or private message text in a report. See [security](security.md) and [collaboration](social-collaboration.md).
