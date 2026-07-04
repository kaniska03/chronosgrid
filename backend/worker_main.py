"""Worker service entrypoint (docker compose `worker`)."""
from worker.main import main

if __name__ == "__main__":
    main()
