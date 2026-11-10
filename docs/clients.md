# Web and installable Web App

The same server serves desktop browsers, mobile browsers and the installable Web App. Build the client with npm run build; installable metadata and the notification service worker are included in the build.

Production access requires HTTPS. Mobile browser capabilities such as Web Push, microphone permissions and output-device selection depend on the actual operating system and browser. The Web App stores its server session securely and uses the same authorization and access checks as the browser interface.

A local installation binds to localhost by default. Public access, reverse proxies and notifications must use the configured PUBLIC_URL origin. Server APIs and existing client protocols retain their compatibility identifiers.
