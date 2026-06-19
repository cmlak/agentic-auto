import json
import base64
import logging
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .tasks import process_draft_rule_task

logger = logging.getLogger(__name__)

@csrf_exempt
@require_POST
def draft_rule_webhook(request):
    """
    Receives a push notification from Google Pub/Sub for the 'draft-rules-topic'.
    Decodes the message and dispatches it to a Celery worker.
    """
    try:
        # 1. Decode the incoming request body from Pub/Sub
        body = json.loads(request.body)
        message = body.get('message', {})
        data = message.get('data')

        if not data:
            logger.warning("Webhook received an empty message payload.")
            return HttpResponseBadRequest("Bad Request: No data in message.")

        # 2. Decode the Base64 payload and pass to the Celery task
        payload = json.loads(base64.b64decode(data).decode('utf-8'))
        process_draft_rule_task.delay(payload)

        logger.info(f"Webhook successfully dispatched draft rule task for: {payload.get('title')}")
        return HttpResponse(status=204)  # 204 No Content is standard for successful webhooks

    except Exception as e:
        logger.error(f"CRITICAL: Webhook processing failed: {e}", exc_info=True)
        # Return a 500-level error so Pub/Sub knows to retry the delivery
        return HttpResponse("Webhook Error", status=500)