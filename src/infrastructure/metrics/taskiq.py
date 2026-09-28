"""Taskiq broker middleware.

`pre_send` runs wherever a task is kicked (the web process), `post_execute` / `on_error` only
in the worker, so each deployment exports the half it actually performs.
"""

from typing import Any

from taskiq import TaskiqMessage, TaskiqResult
from taskiq.abc.middleware import TaskiqMiddleware

from .registry import (
    TASKIQ_TASK_DURATION,
    TASKIQ_TASK_ERRORS,
    TASKIQ_TASKS_EXECUTED,
    TASKIQ_TASKS_SENT,
)


class MetricsMiddleware(TaskiqMiddleware):
    def pre_send(self, message: TaskiqMessage) -> TaskiqMessage:
        TASKIQ_TASKS_SENT.labels(message.task_name).inc()
        return message

    def post_execute(self, message: TaskiqMessage, result: TaskiqResult[Any]) -> None:
        TASKIQ_TASKS_EXECUTED.labels(
            message.task_name, "error" if result.is_err else "success"
        ).inc()
        execution_time = getattr(result, "execution_time", None)
        if isinstance(execution_time, (int, float)):
            TASKIQ_TASK_DURATION.labels(message.task_name).observe(float(execution_time))

    def on_error(
        self,
        message: TaskiqMessage,
        result: TaskiqResult[Any],
        exception: BaseException,
    ) -> None:
        TASKIQ_TASK_ERRORS.labels(message.task_name, type(exception).__name__).inc()
