from __future__ import annotations

import concurrent.futures
import os
import signal
import socket
import threading
import time
import uuid

import canvas_store
import generation_queue
import observability
import schema_migrations
import server
import tenant_context
import tenant_registry


class GenerationWorker:
    def __init__(
        self,
        *,
        worker_id: str = "",
        concurrency: int | None = None,
        poll_seconds: float | None = None,
        lease_seconds: int | None = None,
        admission_seconds: int | None = None,
    ):
        self.worker_id = str(worker_id or "").strip() or (
            f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        )
        self.concurrency = max(
            1,
            int(
                concurrency
                if concurrency is not None
                else os.getenv("CANVAS_WORKER_CONCURRENCY", "8")
            ),
        )
        self.poll_seconds = max(
            0.1,
            float(
                poll_seconds
                if poll_seconds is not None
                else os.getenv("CANVAS_WORKER_POLL_SECONDS", "0.5")
            ),
        )
        self.lease_seconds = max(
            15,
            int(
                lease_seconds
                if lease_seconds is not None
                else os.getenv("CANVAS_WORKER_LEASE_SECONDS", "90")
            ),
        )
        self.admission_seconds = max(
            self.lease_seconds,
            int(
                admission_seconds
                if admission_seconds is not None
                else os.getenv(
                    "CANVAS_WORKER_ADMISSION_TTL_SECONDS",
                    "300",
                )
            ),
        )
        self.stop_event = threading.Event()
        self.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=self.concurrency,
            thread_name_prefix=f"generation-{self.worker_id[:12]}",
        )
        self.futures: set[concurrent.futures.Future] = set()
        self._futures_lock = threading.Lock()
        self._tenant_cursor = 0
        self._last_recovery = 0.0
        self._last_heartbeat = 0.0

    def request_stop(self):
        self.stop_event.set()

    def _contexts(self):
        return [
            tenant_context.make_context(tenant["id"], tenant["slug"])
            for tenant in tenant_registry.list_tenants(include_disabled=False)
        ]

    def verify_schema(self):
        failures = []
        for context in self._contexts():
            with tenant_context.use_context(context):
                connection = canvas_store._connect()
                try:
                    report = schema_migrations.verify_schema(
                        connection,
                        "canvas",
                    )
                finally:
                    connection.close()
            if not report["ok"]:
                failures.append({
                    **report,
                    "tenant": context.slug,
                })
        control = tenant_registry._connect()
        try:
            control_report = schema_migrations.verify_schema(
                control,
                "control",
            )
        finally:
            control.close()
        if not control_report["ok"]:
            failures.append({
                **control_report,
                "tenant": "control",
            })
        if failures:
            raise RuntimeError(
                "Worker 数据库迁移未就绪：" + repr(failures)
            )

    def _active_count(self) -> int:
        with self._futures_lock:
            self.futures = {
                future for future in self.futures if not future.done()
            }
            return len(self.futures)

    def heartbeat(self):
        generation_queue.heartbeat_worker(
            self.worker_id,
            active_tasks=self._active_count(),
            metadata={
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "concurrency": self.concurrency,
                "releaseCommit": server.release_commit(),
            },
        )
        self._last_heartbeat = time.monotonic()

    def _recover_expired(self):
        if time.monotonic() - self._last_recovery < 15:
            return
        for context in self._contexts():
            with tenant_context.use_context(context):
                generation_queue.recover_expired_queue_claims()
        self._last_recovery = time.monotonic()

    def _claim_and_admit(self):
        contexts = self._contexts()
        if not contexts:
            return None
        count = len(contexts)
        for offset in range(count):
            index = (self._tenant_cursor + offset) % count
            context = contexts[index]
            with tenant_context.use_context(context):
                task = generation_queue.claim_next_generation_task(
                    self.worker_id,
                    lease_seconds=self.lease_seconds,
                )
                if not task:
                    continue
                try:
                    admission = generation_queue.acquire_generation_admission(
                        task["id"],
                        task["claimToken"],
                        provider=str(task.get("provider") or ""),
                        model=str(task.get("model") or ""),
                        kind=str(task.get("kind") or ""),
                        lease_seconds=self.admission_seconds,
                    )
                except Exception:
                    generation_queue.release_generation_task_claim(
                        task["id"],
                        task["claimToken"],
                        requeue=True,
                        error="准入失败，任务已重新排队",
                    )
                    raise
                self._tenant_cursor = (index + 1) % count
                if admission.get("acquired"):
                    return context, task, admission
        return None

    def _execute(self, context, task, admission):
        try:
            server.execute_admitted_generation_task(
                task["id"],
                context.tenant_id,
                context.slug,
                task["claimToken"],
                task=task,
            )
        except Exception as exc:
            try:
                with tenant_context.use_context(context):
                    current = canvas_store.get_generation_task(task["id"]) or {}
                    if current.get("status") in {
                        "queued",
                        "submitting",
                        "running",
                    }:
                        provider_task_id = str(
                            current.get("provider_task_id") or ""
                        ).strip()
                        canvas_store.finish_generation_task(
                            task["id"],
                            status=(
                                "interrupted"
                                if provider_task_id
                                else "failed"
                            ),
                            phase=(
                                "结果待确认"
                                if provider_task_id
                                else "任务异常"
                            ),
                            error=str(exc),
                            delivery_status=(
                                "pending"
                                if provider_task_id
                                else "failed"
                            ),
                        )
                    generation_queue.release_generation_task_claim(
                        task["id"],
                        task["claimToken"],
                        error=str(exc),
                    )
            except Exception as cleanup_error:
                observability.log_event(
                    "generation-worker",
                    "execution.cleanup_failed",
                    severity="error",
                    workerId=self.worker_id,
                    tenantId=context.tenant_id,
                    taskId=task["id"],
                    error=str(cleanup_error),
                )
            observability.log_event(
                "generation-worker",
                "execution.failed",
                severity="error",
                workerId=self.worker_id,
                tenantId=context.tenant_id,
                taskId=task["id"],
                error=str(exc),
            )
        finally:
            generation_queue.release_generation_admission(
                task["id"],
                task["claimToken"],
            )

    def run_once(self) -> bool:
        self.heartbeat()
        self._recover_expired()
        if self._active_count() >= self.concurrency:
            return False
        claim = self._claim_and_admit()
        if not claim:
            return False
        context, task, _admission = claim
        future = self.executor.submit(
            self._execute,
            context,
            task,
            _admission,
        )
        with self._futures_lock:
            self.futures.add(future)
        return True

    def run_forever(self):
        self.verify_schema()
        observability.log_event(
            "generation-worker",
            "worker.started",
            workerId=self.worker_id,
            concurrency=self.concurrency,
            releaseCommit=server.release_commit(),
        )
        while not self.stop_event.is_set():
            try:
                processed = self.run_once()
            except Exception as exc:
                observability.log_event(
                    "generation-worker",
                    "worker.loop_error",
                    severity="error",
                    workerId=self.worker_id,
                    error=str(exc),
                )
                processed = False
            if not processed:
                self.stop_event.wait(self.poll_seconds)
        self.shutdown()

    def shutdown(self):
        self.stop_event.set()
        deadline = time.monotonic() + max(
            5.0,
            float(os.getenv("CANVAS_WORKER_DRAIN_SECONDS", "30")),
        )
        while self._active_count() and time.monotonic() < deadline:
            time.sleep(0.2)
        self.executor.shutdown(wait=False, cancel_futures=False)
        try:
            self.heartbeat()
        except Exception:
            pass
        observability.log_event(
            "generation-worker",
            "worker.stopped",
            workerId=self.worker_id,
            activeTasks=self._active_count(),
        )


def main() -> int:
    worker = GenerationWorker()

    def stop(_signum, _frame):
        worker.request_stop()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    worker.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
