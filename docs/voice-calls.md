# Calls and optional transports

Private and federated calls use WebRTC with a configured coturn service. Group invitation rooms require LiveKit. These services are optional: without them the rest of the workspace remains available. In this version private calls are unavailable without a healthy configured TURN service; group creation is hidden and new group joins are rejected without LiveKit. Existing legacy conversations may finish.

## coturn

Set CALL_TURN_URLS (comma-separated turn:/turns: URLs) and CALL_TURN_SECRET to match coturn's shared authentication secret. CALL_STUN_URLS is optional STUN configuration. Use a dedicated TURN hostname such as turn.example.com. Configure TLS/certificates on coturn itself for turns:; opening a TLS port does not enable TLS.

Typical ports are 3478 TCP/UDP, an explicitly configured UDP relay range, and optionally 5349 for TURN/TLS. NAT, firewall, external-ip and relay range must match your actual deployment. Do not route TURN through an HTTP reverse proxy or CDN. Never copy another installation's external IP, shared secret or router settings.

## LiveKit

Set CALL_LIVEKIT_ENABLED=true, CALL_LIVEKIT_URL=wss://livekit.example.com, CALL_LIVEKIT_API_KEY and CALL_LIVEKIT_API_SECRET. The optional CALL_LIVEKIT_API_URL is used only by the server; omit it to use the public HTTPS origin, or supply a trusted internal endpoint. The secret is never returned to clients.

The reverse proxy must support HTTPS/WSS for the signalling endpoint. Configure LiveKit's advertised address, UDP media (for example 7882/UDP), TCP fallback (for example 7881/TCP), and the matching firewall/NAT. Exposing the WebSocket endpoint alone does not carry media. Consult https://docs.livekit.io/transport/self-hosting/ports-firewall/ for the selected LiveKit deployment.

Group tokens are short-lived and scoped to the invited room and allowed media. The current group limit is eight participants. The SFU is a trusted server in this transport model; transport encryption does not imply end-to-end media encryption. This integration does not enable server recording.

## Clients and diagnostics

Calls are supported by the web and installable Web App. Browser audio-output selection and earpiece/speaker behavior depend on the actual browser and operating system; a simulator is not a physical route test. Headphone/Bluetooth and Wi-Fi/cellular transitions should be tested on real devices.

When reporting a problem include the client/OS version, transport, time and connection diagnostics, never recordings or secret credentials. Verify private and group transports separately.
