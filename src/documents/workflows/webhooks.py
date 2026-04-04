import logging

import httpx
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from paperless.network import format_host_for_url
from paperless.network import is_public_ip
from paperless.network import resolve_hostname_ips
from paperless.network import validate_outbound_http_url

logger = logging.getLogger("paperless.workflows.webhooks")


def _update_workflow_step(
    run_step_id: int | None,
    *,
    status: str,
    message: str,
    error: str = "",
    response_payload: dict | None = None,
) -> None:
    if run_step_id is None:
        return

    from documents.models import WorkflowRun
    from documents.models import WorkflowRunStep

    step = (
        WorkflowRunStep.objects.select_related("workflow_run")
        .filter(pk=run_step_id)
        .first()
    )
    if step is None:
        return

    step.status = status
    step.message = message
    step.error = error
    step.response_payload = response_payload
    step.finished_at = timezone.now()
    step.save(
        update_fields=[
            "status",
            "message",
            "error",
            "response_payload",
            "finished_at",
        ],
    )

    workflow_run = step.workflow_run
    steps = list(workflow_run.steps.all())
    if any(s.status == WorkflowRunStep.WorkflowRunStepStatus.FAILED for s in steps):
        failed_step = next(
            s for s in steps if s.status == WorkflowRunStep.WorkflowRunStepStatus.FAILED
        )
        workflow_run.status = WorkflowRun.WorkflowRunStatus.FAILED
        workflow_run.finished_at = failed_step.finished_at or timezone.now()
        workflow_run.current_step_order = failed_step.order
        workflow_run.message = failed_step.message or "Workflow failed"
        workflow_run.error = failed_step.error
    elif any(s.status == WorkflowRunStep.WorkflowRunStepStatus.RUNNING for s in steps):
        running_step = next(
            s
            for s in steps
            if s.status == WorkflowRunStep.WorkflowRunStepStatus.RUNNING
        )
        workflow_run.status = WorkflowRun.WorkflowRunStatus.RUNNING
        workflow_run.finished_at = None
        workflow_run.current_step_order = running_step.order
        workflow_run.message = running_step.message or "Workflow is running"
        workflow_run.error = ""
    else:
        workflow_run.status = WorkflowRun.WorkflowRunStatus.SUCCESS
        workflow_run.finished_at = timezone.now()
        workflow_run.current_step_order = None
        workflow_run.message = step.message or "Workflow completed"
        workflow_run.error = ""

    workflow_run.save(
        update_fields=[
            "status",
            "finished_at",
            "current_step_order",
            "message",
            "error",
        ],
    )


class WebhookTransport(httpx.HTTPTransport):
    """
    Transport that resolves/validates hostnames and rewrites to a vetted IP
    while keeping Host/SNI as the original hostname.
    """

    def __init__(
        self,
        hostname: str,
        *args,
        allow_internal: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.hostname = hostname
        self.allow_internal = allow_internal

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        hostname = request.url.host

        if not hostname:
            raise httpx.ConnectError("No hostname in request URL")

        try:
            ips = resolve_hostname_ips(hostname)
        except ValueError as e:
            raise httpx.ConnectError(str(e)) from e

        if not self.allow_internal:
            for ip_str in ips:
                if not is_public_ip(ip_str):
                    raise httpx.ConnectError(
                        f"Connection blocked: {hostname} resolves to a non-public address",
                    )

        ip_str = ips[0]
        formatted_ip = format_host_for_url(ip_str)

        new_headers = httpx.Headers(request.headers)
        if "host" in new_headers:
            del new_headers["host"]
        new_headers["Host"] = hostname
        new_url = request.url.copy_with(host=formatted_ip)

        request = httpx.Request(
            method=request.method,
            url=new_url,
            headers=new_headers,
            content=request.stream,
            extensions=request.extensions,
        )
        request.extensions["sni_hostname"] = hostname

        return super().handle_request(request)


@shared_task(
    bind=True,
    retry_backoff=True,
    autoretry_for=(httpx.HTTPStatusError,),
    max_retries=3,
    throws=(httpx.HTTPError,),
)
def send_webhook(
    self,
    url: str,
    data: str | dict,
    headers: dict,
    files: dict,
    *,
    as_json: bool = False,
    run_step_id: int | None = None,
):
    try:
        parsed = validate_outbound_http_url(
            url,
            allowed_schemes=settings.WEBHOOKS_ALLOWED_SCHEMES,
            allowed_ports=settings.WEBHOOKS_ALLOWED_PORTS,
            # Internal-address checks happen in transport to preserve ConnectError behavior.
            allow_internal=True,
        )
    except ValueError as e:
        logger.warning("Webhook blocked: %s", e)
        raise

    hostname = parsed.hostname
    if hostname is None:  # pragma: no cover
        raise ValueError("Invalid URL scheme or hostname.")

    transport = WebhookTransport(
        hostname=hostname,
        allow_internal=settings.WEBHOOKS_ALLOW_INTERNAL_REQUESTS,
    )

    try:
        post_args = {
            "url": url,
            "headers": {
                k: v for k, v in (headers or {}).items() if k.lower() != "host"
            },
            "files": files or None,
        }
        if as_json:
            post_args["json"] = data
        elif isinstance(data, dict):
            post_args["data"] = data
        else:
            post_args["content"] = data

        with httpx.Client(
            transport=transport,
            timeout=5.0,
            follow_redirects=False,
        ) as client:
            response = client.post(
                **post_args,
            )
            response.raise_for_status()
            logger.info(
                f"Webhook sent to {url}",
            )
            _update_workflow_step(
                run_step_id,
                status="success",
                message=f"Webhook sent to {url}",
                response_payload={
                    "status_code": response.status_code,
                },
            )
    except Exception as e:
        logger.error(
            f"Failed attempt sending webhook to {url}: {e}",
        )
        if (
            not isinstance(e, httpx.HTTPStatusError)
            or self.request.retries >= self.max_retries
        ):
            _update_workflow_step(
                run_step_id,
                status="failed",
                message=f"Webhook failed for {url}",
                error=str(e),
            )
        raise e
    finally:
        transport.close()
