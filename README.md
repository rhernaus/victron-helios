## API

Current endpoints:

- `GET /health`: service status
- `GET /config`: returns current config (with secrets redacted)
- `PUT /config`: updates configuration; validates invariants
- `GET /status`: returns automation state and timestamps
- `GET /plan`: returns the latest plan (404 if not ready)
- `POST /pause`: pause automation
- `POST /resume`: resume automation
- `GET /metrics`: Prometheus metrics