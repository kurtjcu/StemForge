"""StemForge launcher — starts the FastAPI server with uvicorn."""

import uvicorn


def main() -> None:
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8765,
        log_level="info",
    )


if __name__ == "__main__":
    main()
