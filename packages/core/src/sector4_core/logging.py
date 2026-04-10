import logging


class StructuredFormatter(logging.Formatter):
    RESERVED = {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
    }

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        extras = []
        for key, value in sorted(record.__dict__.items()):
            if key in self.RESERVED:
                continue
            extras.append(f"{key}={value}")
        suffix = f" {' '.join(extras)}" if extras else ""
        return (
            f"{self.formatTime(record)} level={record.levelname} "
            f"logger={record.name} msg={record.message}{suffix}"
        )


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(handler)
