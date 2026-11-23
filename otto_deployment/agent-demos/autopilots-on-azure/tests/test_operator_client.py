import json
import unittest
from unittest.mock import patch

import httpx

from scripts.operator_client import invoke_response


class OperatorClientTests(unittest.TestCase):
    def test_submit_once_then_poll_without_holding_a_long_http_request(self):
        requests = []
        polls = 0
        def handle(request):
            nonlocal polls
            requests.append(request)
            if request.method == "POST":
                self.assertEqual(json.loads(request.content)["requestId"], "one")
                return httpx.Response(202, json={"requestId": "one", "jobId": "job-one"})
            polls += 1
            if polls == 1:
                return httpx.Response(202, json={"status": "pending", "requestId": "one"})
            return httpx.Response(200, json={"requestId": "one", "response": "actual result"})
        with httpx.Client(transport=httpx.MockTransport(handle)) as client, patch("scripts.operator_client.time.sleep"):
            response = invoke_response(client, "https://gateway/invoke", body={"requestId": "one", "message": "hello"})
        self.assertEqual(response.json()["response"], "actual result")
        self.assertEqual([request.method for request in requests], ["POST", "GET", "GET"])

    def test_lost_admission_response_recovers_by_existing_identity_without_resubmitting(self):
        methods = []
        def handle(request):
            methods.append(request.method)
            if request.method == "POST":
                raise httpx.ReadTimeout("The server may have committed admission.", request=request)
            return httpx.Response(200, json={"requestId": "one", "response": "saved"})
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            response = invoke_response(client, "https://gateway/invoke", body={"requestId": "one"})
        self.assertEqual(methods, ["POST", "GET"])
        self.assertEqual(response.json()["response"], "saved")

    def test_confirmed_admission_cannot_disappear_into_a_new_model_request(self):
        def handle(request):
            if request.method == "POST":
                return httpx.Response(202, json={"requestId": "one", "jobId": "job"})
            return httpx.Response(404, json={"detail": "missing"})
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            with self.assertRaisesRegex(RuntimeError, "disappeared"):
                invoke_response(client, "https://gateway/invoke", body={"requestId": "one"})

    def test_conflict_is_returned_without_retrying(self):
        requests = []
        def handle(request):
            requests.append(request)
            return httpx.Response(409, json={"detail": "Changed payload."})
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            response = invoke_response(client, "https://gateway/invoke", body={"requestId": "one"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(requests), 1)


if __name__ == "__main__":
    unittest.main()
