# Modules and dashboard widgets

The server module catalog defines available workspace sections and their configuration. Installation capabilities, account access and module enablement are separate decisions. A hidden control does not replace server authorization.

Dashboard settings contain versioned widget instances. Each instance has an identity, module type, title, size and module-specific configuration. Layout updates use optimistic concurrency so one window does not silently replace another window's newer settings.

The shared widget shell controls geometry, configuration, entry motion and lifecycle. Module content handles its own data and state. Dispose subscriptions, observers, timers and pending requests when a widget is removed. Pause unnecessary work in the background and coalesce equivalent requests.

New modules must define their server contracts, access checks, localization, widget configuration and export/cleanup behavior. Respect the supported widget sizes and keep existing instance IDs and saved configuration compatible. Mandatory account and administrative sections are not ordinary optional widgets.

External integrations remain optional. Absence of credentials or services must leave unrelated local workspace features available. See [worker deployment](production-workers.md), [localization](localization.md) and [retention](data-retention.md).
