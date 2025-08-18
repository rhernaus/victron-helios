import asyncio
import os
import uvicorn

from helios.server import create_app


def main() -> None:
    port = int(os.environ.get("HELIOS_PORT", "8000"))
    host = os.environ.get("HELIOS_HOST", "0.0.0.0")
    app = create_app()
    uvicorn.run(app, host=host, port=port, lifespan="on")


if __name__ == "__main__":
    main()

