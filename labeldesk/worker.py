import logging
import time

from labeldesk.db import init
from labeldesk.selection import tick

logger = logging.getLogger(__name__)


def main():
    logging.basicConfig(level=logging.INFO)
    init()
    while True:
        try:
            if not tick():
                time.sleep(1)
        except Exception:
            logger.exception("Отбор не завершён; снимок остаётся в очереди")
            time.sleep(1)


if __name__ == "__main__":
    main()
