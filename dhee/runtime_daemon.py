"""Entry point for the local Dhee runtime daemon."""

from __future__ import annotations

from dhee.runtime import serve_forever


def main() -> None:
    serve_forever()


if __name__ == "__main__":
    main()
