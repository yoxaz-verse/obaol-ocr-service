from rq import Worker

from app.config import get_settings
from app.jobs import QUEUE_NAME, reconcile_counters, redis_client


def main() -> None:
    settings = get_settings()
    reconcile_counters(settings)
    Worker([QUEUE_NAME], connection=redis_client(settings)).work(with_scheduler=True)


if __name__ == "__main__":
    main()
