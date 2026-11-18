"""Loopback-only, single-process development entrypoint: python -m workbench."""
import argparse
import ipaddress


def main():
    parser = argparse.ArgumentParser(description="Local-only Informatica review workbench")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    try:
        loopback = args.host == "localhost" or ipaddress.ip_address(args.host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        parser.error("Remote hosting is disabled until authenticated authorization is implemented")
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    import uvicorn
    from workbench.app import create_app

    # Never reload or use multiple workers: one process owns the local job ledger.
    uvicorn.run(create_app(), host=args.host, port=args.port, workers=1)


if __name__ == "__main__":
    main()