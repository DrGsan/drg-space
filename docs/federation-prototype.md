# Isolated federation verification

The historical runner name is retained for compatibility. It exercises the actual application and built production assets in disposable Docker/Compose installations, rather than a separate experimental API.

```sh
npm run build
.venv/bin/python scripts/federation-prototype.py
```

Use Python 3.14 with the project's dependencies, Docker/Compose, Node.js and Playwright Chromium. The runner creates a random Compose project, encrypted SQLCipher and PostgreSQL installations, test identities and an isolated TURN relay. It verifies cross-server discovery, access, collaborative data and media scenarios. Working environments, data and production volumes must not be supplied to this fixture.

The test owns its temporary directories, containers and volumes and cleans them through its finalization path. Inspect a failed run before retrying and remove only resources bearing that run's project identity. This harness is not a production deployment command. See [federation configuration](federation.md).
