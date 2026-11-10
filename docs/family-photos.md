# Optional Family Photo integration

Family Photo is a separately operated Telegram archive service. Configure `FAMILY_PHOTO_URL` and `FAMILY_PHOTO_TOKEN` to connect it. Empty values leave this integration unavailable while local chat and other workspace features remain usable.

The site owner grants eligible accounts access and selects permitted groups. The server checks both the site's grant and the external archive's capabilities before exposing metadata, media or supported conversation actions. The installation token stays on the server.

The timeline uses archive metadata to build its date navigation and loads media progressively. Searches, counts, thumbnails, originals and Telegram actions depend on the external service's supported API and permissions. Do not treat a displayed group identifier as authorization.

The site stores account grants and integration settings; archive messages and original media remain in Family Photo storage. A full site backup is not a backup of that external archive. Operate and back up the external service separately.

Use HTTPS and an endpoint trusted by the operator. Diagnose the site and archive service independently, with redacted request information. Do not publish Telegram session files, bot credentials or the integration token in repository content or browser-facing configuration.
