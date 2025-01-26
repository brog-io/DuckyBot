import logging
import json
from datetime import datetime, UTC


class DetailedFormatter(logging.Formatter):
    def format(self, record):
        # Add timestamp in ISO format
        record.timestamp = datetime.now(datetime.UTC).isoformat()

        # Create a structured log entry
        log_entry = {
            "timestamp": record.timestamp,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add extra contextual information if available
        if hasattr(record, "extra"):
            log_entry["extra"] = record.extra

        # Add exception information if available
        if record.exc_info:
            log_entry["exception"] = {
                "type": str(record.exc_info[0].__name__),
                "message": str(record.exc_info[1]),
                "traceback": self.formatException(record.exc_info),
            }

        return json.dumps(log_entry)
