import json
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from microsoft_agents.activity import Activity
from microsoft_agents.hosting.core import TurnContext

import bridge.app as gateway
import bridge.html_widgets as widgets
from bridge.html_widgets import START, END, SUBMIT_TOOL, decode_submission, extract_widget_request, render_widget, validate_spec
from bridge.runtime.base import AgentAuthContext, AgentRequest, AgentResponse
from bridge.runtime.hermes import PEOPLE_MESSAGE_POLICY, WIDGET_REPLY_POLICY, bridge_instructions


SPEC = {
    "title": "Scenario planner",
    "html": '<h2>Plan</h2><input id="budget" data-widget-field="budget" value="5">'
            '<button data-hermes-action="discuss">Discuss this plan</button>',
    "css": "h2{font-size:24px}",
    "javascript": "document.getElementById('budget').addEventListener('input',()=>{});",
    "actions": [{"id": "discuss", "label": "Discuss this plan"}],
}


def spec_from_activity(activity):
    return json.loads(activity.text.split("```html-widget\n", 1)[1].split("\n```", 1)[0])


def action_token():
    activity = render_widget(SPEC, session_key="session", user_id="original-owner", conversation_id="chat",
                             fallback="Discuss this illustrative scenario; no allocation is approved.")
    markup = spec_from_activity(activity)["html"]
    config = json.loads(markup.split("const config = ", 1)[1].split(";\n", 1)[0])
    return config["tokens"]["discuss"]


class WidgetEnvironment:
    def setUp(self):
        patcher = patch.dict(os.environ, {"API_SERVER_KEY": "test-key", "WORKER_ID": "otto",
                                         "TEAMS_HTML_WIDGETS_ENABLED": "true"})
        patcher.start()
        self.addCleanup(patcher.stop)
        widgets._issued_capabilities.clear()
        self.addCleanup(widgets._issued_capabilities.clear)


class HtmlWidgetTests(WidgetEnvironment, unittest.TestCase):
    def test_model_html_is_rendered_with_fixed_host_protocol_and_offline_branding(self):
        visible, spec = extract_widget_request("Text fallback\n" + START + json.dumps(SPEC) + END)
        activity = render_widget(spec, session_key="session", user_id="owner", conversation_id="chat", fallback=visible)
        serialized = activity.model_dump(by_alias=True)
        self.assertEqual(serialized["textFormat"], "extendedmarkdown")
        payload = spec_from_activity(activity)
        self.assertEqual(payload["type"], "widget/mcp-ui")
        self.assertIn(SPEC["javascript"], payload["html"])
        self.assertIn("ui/initialize", payload["html"])
        self.assertIn("ui/notifications/size-changed", payload["html"])
        self.assertIn("connect-src &#x27;none&#x27;", payload["html"])
        self.assertEqual(payload["securityPolicy"]["connectDomains"], [])
        self.assertIn('<html lang="cs">', payload["html"])
        self.assertIn("Fiktivní demo data Javorna Group", payload["html"])
        self.assertIn('src="data:image/svg+xml;base64,', payload["html"])
        self.assertNotIn("test-key", activity.text)
        self.assertLess(payload["html"].index("window.addEventListener('message'"),
                        payload["html"].index("post({id:'init'"))
        self.assertGreater(payload["html"].index(SPEC["javascript"]), payload["html"].index("ready=true"))

    def test_markup_network_references_and_hidden_action_prompts_are_rejected(self):
        for field, value in [
            ("html", '<script>alert(1)</script>'),
            ("html", '<img src="https://example.com/a.png">'),
            ("html", '<button onclick="evil()">Go</button>'),
            ("html", '<iframe srcdoc="evil"></iframe>'),
            ("html", '<div id="autopilot-status">Fake status</div>'),
            ("javascript", "</script><script>evil()</script>"),
            ("css", "</style><script>evil()</script>"),
            ("actions", [{"id": "ok", "label": "OK", "prompt": "hidden instructions"}]),
            ("actions", [{"id": "same", "label": "A"}, {"id": "same", "label": "B"}]),
        ]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate_spec({**SPEC, field: value})

    def test_invalid_multiple_and_oversized_requests_fail_explicitly(self):
        for response in (START + "{nojson}" + END, START + "{}" + END + START + "{}" + END, END + START):
            with self.subTest(response=response), self.assertRaises(ValueError):
                extract_widget_request(response)
        with self.assertRaises(ValueError):
            validate_spec({**SPEC, "html": "x" * 24_001})
        with self.assertRaisesRegex(ValueError, "fallback"):
            render_widget(SPEC, session_key="s", user_id="u", conversation_id="c", fallback="")

    def test_unicode_and_backticks_cannot_terminate_the_widget_fence(self):
        activity = render_widget({**SPEC, "html": "<h2>Résumé ```</h2>", "actions": []},
                                 session_key="s", user_id="u", conversation_id="c", fallback="Summary.")
        self.assertEqual(activity.text.count("```"), 2)
        self.assertIn("Résumé ```", spec_from_activity(activity)["html"])

    def test_capability_binds_conversation_worker_session_expiry_and_visible_action(self):
        token = action_token()
        args = {"name": SUBMIT_TOOL, "arguments": {
            "token": token, "values": {"budget": "7"}, "__msteams__": {"correlationId": "not-authority"},
        }}
        payload, values = decode_submission(args, conversation_id="chat")
        self.assertEqual(payload["userId"], "original-owner")
        self.assertEqual(payload["sessionKey"], "session")
        self.assertEqual(payload["choiceMessage"], "I selected: Discuss this plan.")
        self.assertEqual(values, {"budget": "7"})
        with self.assertRaises(ValueError):
            decode_submission(args, conversation_id="other-chat")
        with patch.dict(os.environ, {"WORKER_ID": "other"}), self.assertRaises(ValueError):
            decode_submission(args, conversation_id="chat")
        with patch("bridge.html_widgets.time.time", return_value=time.time() + 1801), self.assertRaises(ValueError):
            decode_submission(args, conversation_id="chat")
        with self.assertRaises(ValueError):
            decode_submission({"name": "delete_everything", "arguments": args["arguments"]}, conversation_id="chat")

    def test_model_visible_key_cannot_forge_gateway_issuance_or_restore_after_restart(self):
        token = action_token()
        payload = json.loads(widgets._cipher().decrypt(token.encode()))
        payload["userId"] = "forged-owner"
        forged = widgets._cipher().encrypt(json.dumps(payload).encode()).decode()
        with self.assertRaisesRegex(ValueError, "not issued"):
            decode_submission({"name": SUBMIT_TOOL, "arguments": {"token": forged}}, conversation_id="chat")
        widgets._issued_capabilities.clear()
        with self.assertRaisesRegex(ValueError, "not issued"):
            decode_submission({"name": SUBMIT_TOOL, "arguments": {"token": token}}, conversation_id="chat")

    def test_capability_cache_fails_explicitly_at_capacity_and_prunes_expiry(self):
        action_token()
        with patch.object(widgets, "MAX_CAPABILITIES", 1), self.assertRaisesRegex(RuntimeError, "outstanding"):
            action_token()
        with patch("bridge.html_widgets.time.time", return_value=time.time() + 1801):
            action_token()
        self.assertEqual(len(widgets._issued_capabilities), 1)

    def test_fields_are_bounded_text_not_a_hidden_tool_invocation(self):
        token = action_token()
        for fields in ({"x": {"tool": "delete"}}, {"x": "a" * 601}, {str(i): "v" for i in range(11)}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                decode_submission({"name": SUBMIT_TOOL, "arguments": {"token": token, "values": fields}},
                                  conversation_id="chat")

    def test_widget_policy_is_gated_personal_non_private_and_retains_file_authorship(self):
        request = AgentRequest(prompt="status", conversation_id="c", user_id="u", source="teams_personal",
                               must_answer=True, metadata={"visualArtifactScope": "a" * 32})
        hint = bridge_instructions(request)
        self.assertIn(WIDGET_REPLY_POLICY, hint)
        self.assertIn("do not send lengthy text-only", hint)
        self.assertIn("follow-up answers after a widget selection", hint)
        self.assertIn("never replace approval controls with widget buttons", hint)
        self.assertIn("teams-html-widget", hint)
        self.assertIn("VISUAL_DOCUMENT_REQUEST", hint)
        for source, metadata in (("teams_group", {}), ("teams_personal", {"attachmentsPrivate": True}),
                                 ("teams_personal", {"persistenceDisabled": True}), ("cron", {})):
            private = bridge_instructions(AgentRequest(prompt="p", conversation_id="c", user_id="u", source=source,
                                                      must_answer=True, metadata=metadata))
            self.assertNotIn("HTML_WIDGET_REQUEST", private)
        with patch.dict(os.environ, {"TEAMS_HTML_WIDGETS_ENABLED": "false"}):
            self.assertNotIn("HTML_WIDGET_REQUEST", bridge_instructions(request))

    def test_people_message_policy_follows_teams_collaboration_not_widget_gate(self):
        for source, widgets_enabled in (("teams_personal", "false"), ("cron", "true")):
            request = AgentRequest(prompt="p", conversation_id="c", user_id="u", source=source, must_answer=True)
            with self.subTest(source=source), patch.dict(os.environ, {
                "M365_TEAMS_COLLABORATION_ENABLED": "true", "TEAMS_HTML_WIDGETS_ENABLED": widgets_enabled,
            }):
                hint = bridge_instructions(request)
                self.assertIn(PEOPLE_MESSAGE_POLICY, hint)
                self.assertIn("adaptiveCardJson", hint)
        with patch.dict(os.environ, {"M365_TEAMS_COLLABORATION_ENABLED": "false", "WORKIQ_TEAMS_MCP_URL": ""}):
            self.assertNotIn(PEOPLE_MESSAGE_POLICY, bridge_instructions(request))


class WidgetInvokeTests(WidgetEnvironment, unittest.IsolatedAsyncioTestCase):
    async def test_durable_ingress_returns_real_sdk_widget_ack_without_bypassing_queue(self):
        ctx = TurnContext(gateway.agent365_adapter, Activity.model_validate({
            "type": "invoke", "id": "selection", "name": "htmlwidget/calltool", "channelId": "msteams",
            "recipient": {"id": "otto"}, "serviceUrl": "https://smba.trafficmanager.net/teams/",
            "conversation": {"id": "chat", "conversationType": "personal"}, "from": {"id": "owner"},
            "value": {"name": SUBMIT_TOOL, "arguments": {"token": action_token()}},
        }))
        with (
            patch.object(gateway.durable_transport, "enabled", return_value=True),
            patch.object(gateway.durable_transport, "settings", return_value=SimpleNamespace(activities_queue="activities")),
            patch.object(gateway.event_worker, "enqueue_activity", AsyncMock(return_value="correlation")) as enqueue,
            patch.object(gateway.event_worker, "start_work") as work,
            patch.object(gateway, "start_background_turn") as background,
        ):
            await gateway.agent365_app.on_turn(ctx)
        enqueue.assert_awaited_once_with(ctx)
        work.assert_called_once_with("activities", "correlation",
                                     gateway.process_durable_activity, gateway.process_scheduled_message)
        background.assert_not_called()
        response = gateway.agent365_adapter._process_turn_results(ctx)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body["responseType"], "htmlwidget/calltoolresult")
        self.assertEqual(response.body["callToolResult"]["structuredContent"], {"status": "queued"})
        self.assertFalse(response.body["callToolResult"]["isError"])

    async def test_durable_ingress_rejects_invalid_or_disabled_widget_before_enqueue(self):
        token = action_token()
        for chat, gate, supplied in (("chat", "false", token), ("other-chat", "true", token),
                                     ("chat", "true", "expired")):
            ctx = TurnContext(gateway.agent365_adapter, Activity.model_validate({
                "type": "invoke", "name": "htmlwidget/calltool", "channelId": "msteams",
                "recipient": {"id": "otto"}, "serviceUrl": "https://smba.trafficmanager.net/teams/",
                "conversation": {"id": chat, "conversationType": "personal"}, "from": {"id": "owner"},
                "value": {"name": SUBMIT_TOOL, "arguments": {"token": supplied}},
            }))
            with (
                self.subTest(chat=chat, gate=gate),
                patch.dict(os.environ, {"TEAMS_HTML_WIDGETS_ENABLED": gate}),
                patch.object(gateway.durable_transport, "enabled", return_value=True),
                patch.object(gateway.event_worker, "enqueue_activity", AsyncMock()) as enqueue,
                patch.object(gateway.event_worker, "start_work") as work,
            ):
                await gateway.agent365_app.on_turn(ctx)
            enqueue.assert_not_awaited()
            work.assert_not_called()
            result = gateway.agent365_adapter._process_turn_results(ctx).body["callToolResult"]
            self.assertTrue(result["isError"])
            self.assertIn("could not be accepted", result["content"][0]["text"])

    async def test_queued_widget_rejection_is_reported_in_original_chat(self):
        activity = Activity.model_validate({
            "type": "invoke", "id": "selection", "name": "htmlwidget/calltool", "channelId": "msteams",
            "recipient": {"id": "otto"}, "serviceUrl": "https://smba.trafficmanager.net/teams/",
            "conversation": {"id": "chat", "conversationType": "personal"}, "from": {"id": "owner"},
        })
        conversation = SimpleNamespace(
            validate=lambda: None, claims=[], conversation_reference=activity.get_conversation_reference(),
        )
        ctx = SimpleNamespace(activity=None, turn_state={}, send_activity=AsyncMock())
        error = "Widget selection could not be accepted: The widget capability was not issued by this gateway or has expired."

        async def continue_conversation(app_id, continuation, execute):
            await execute(ctx)

        async def reject(context):
            context.turn_state[gateway.agent365_adapter.INVOKE_RESPONSE_KEY] = Activity(
                type="invokeResponse", value={"status": 200, "body": widgets.tool_result(error=error)},
            )

        with (
            patch.dict(os.environ, {"AGENT365_BLUEPRINT_CLIENT_ID": "blueprint"}),
            patch("microsoft_agents.hosting.core.app.proactive.Conversation.from_json_to_store_item",
                  return_value=conversation),
            patch.object(gateway.agent365_adapter, "continue_conversation", side_effect=continue_conversation),
            patch.object(gateway.agent365_app, "on_turn", side_effect=reject),
        ):
            await gateway.process_durable_activity({
                "activity": activity.model_dump(mode="json", by_alias=True, exclude_none=True), "conversation": {},
            })
        ctx.send_activity.assert_awaited_once_with(error)

    async def test_model_output_is_sent_only_when_enabled_in_personal_non_private_chat(self):
        auth = AgentAuthContext(selected_mode="agent_identity", available_modes=("agent_identity",),
                                conversation_boundary="one_to_one")
        for enabled, chat, private, expected in (
            ("true", "personal", False, True), ("false", "personal", False, False),
            ("true", "groupChat", False, False), ("true", "personal", True, False),
        ):
            ctx = SimpleNamespace(activity=Activity.model_validate({
                "type": "message", "id": "request", "channelId": "msteams",
                "conversation": {"id": "chat", "conversationType": chat}, "from": {"id": "owner"},
            }), send_activity=AsyncMock(return_value=SimpleNamespace(id="widget-activity")))
            direct = AsyncMock(return_value="sent")
            with (
                self.subTest(enabled=enabled, chat=chat, private=private),
                patch.dict(os.environ, {"TEAMS_HTML_WIDGETS_ENABLED": enabled}),
                patch.object(gateway, "agent_auth_context", return_value=auth),
                patch.object(gateway, "invoke_agent_runtime", return_value=AgentResponse(
                    text="Your illustrative planner.\n" + START + json.dumps(SPEC) + END,
                )),
                patch.object(gateway, "delivery_reference_metadata", return_value={"boundary": "one_to_one"}),
                patch.object(gateway, "send_typing_indicators", new_callable=AsyncMock),
                patch.object(gateway, "owner_personal_chat", return_value=False),
                patch.object(gateway, "send_teams_response", direct),
                patch.object(gateway, "send_proactive_activity", AsyncMock(return_value={"activityId": "sent"})) as proactive,
                patch.object(gateway, "pending_document_card_activity", AsyncMock(return_value=None)),
            ):
                await gateway.run_agent_runtime_for_teams(
                    ctx, conversation_id="chat", session_key="session", message="Status",
                    attachments=[SimpleNamespace(metadata=lambda: {})] if private else None,
                )
            if expected:
                sent = ctx.send_activity.await_args.args[0]
                self.assertEqual(sent.text_format, "extendedmarkdown")
                self.assertNotIn(START, sent.text)
                self.assertEqual(spec_from_activity(sent)["name"], "Scenario planner")
            else:
                ctx.send_activity.assert_not_awaited()
                fallback = proactive.await_args.args[2] if private else direct.await_args.args[1]
                self.assertIn("Your illustrative planner.", fallback)
                self.assertIn("unavailable", fallback)
                self.assertNotIn(START, fallback)

    async def test_continuation_reuses_original_session_without_approval_authority(self):
        token = {"interactionId": "a" * 32, "sessionKey": "session", "conversationId": "chat",
                 "userId": "original-owner", "choiceMessage": "I selected: Discuss this plan."}
        with (
            patch.object(gateway, "invoke_agent_runtime", return_value=AgentResponse(
                text="Updated planner.\n" + START + json.dumps(SPEC) + END,
            )) as invoke,
            patch.object(gateway, "remember_teams_event"),
            patch.object(gateway, "send_proactive_activity", return_value={"activityId": "followup"}) as send,
        ):
            await gateway.continue_widget_selection(
                token=token, values={"budget": "7"}, delivery={"boundary": "one_to_one"},
                auth=AgentAuthContext(selected_mode="agent_identity", available_modes=("agent_identity",),
                                      conversation_boundary="one_to_one"),
            )
        self.assertEqual(invoke.await_args.kwargs["session_key"], "session")
        self.assertEqual(invoke.await_args.kwargs["user_id"], "original-owner")
        self.assertIn('"budget": "7"', invoke.await_args.kwargs["message"])
        self.assertIn("does not authorize budget approval", invoke.await_args.kwargs["message"])
        self.assertIn(WIDGET_REPLY_POLICY, invoke.await_args.kwargs["message"])
        self.assertEqual(send.await_args.kwargs["activity"].text_format, "extendedmarkdown")

    async def test_brief_callback_answers_remain_text_without_forced_widgets(self):
        token = {"interactionId": "a" * 32, "sessionKey": "session", "conversationId": "chat",
                 "userId": "owner", "choiceMessage": "Discuss."}
        for gate in ("true", "false"):
            with self.subTest(gate=gate), \
                 patch.dict(os.environ, {"TEAMS_HTML_WIDGETS_ENABLED": gate}), \
                 patch.object(gateway, "remember_teams_event"), \
                 patch.object(gateway, "invoke_agent_runtime", return_value=AgentResponse(text="Which period?")) as invoke, \
                 patch.object(gateway, "send_proactive_activity", AsyncMock(return_value={"activityId": "reply"})) as send:
                await gateway.continue_widget_selection(
                    token=token, values={}, delivery={},
                    auth=AgentAuthContext(selected_mode="agent_identity", available_modes=("agent_identity",),
                                          conversation_boundary="one_to_one"),
                )
            activity = send.await_args.kwargs["activity"]
            self.assertEqual(activity.text, "Which period?")
            self.assertNotEqual(activity.text_format, "extendedmarkdown")
            self.assertEqual(WIDGET_REPLY_POLICY in invoke.await_args.kwargs["message"], gate == "true")

    async def test_widget_first_follow_up_preserves_governed_card_path(self):
        token = {"interactionId": "a" * 32, "sessionKey": "session", "conversationId": "chat",
                 "userId": "owner", "choiceMessage": "Discuss."}
        card = {"title": "Governed confirmation"}
        card_activity = Activity(type="message", text="Governed confirmation")
        with patch.object(gateway, "remember_teams_event"), \
             patch.object(gateway, "invoke_agent_runtime", return_value=AgentResponse(text="Review the confirmation.")), \
             patch.object(gateway, "extract_interaction_request", return_value=("Review the confirmation.", card)), \
             patch.object(gateway, "render_card", return_value=(card_activity, "card-id")) as render, \
             patch.object(gateway, "render_widget") as widget, \
             patch.object(gateway, "send_proactive_activity", AsyncMock(return_value={"activityId": "reply"})) as send:
            await gateway.continue_widget_selection(
                token=token, values={}, delivery={},
                auth=AgentAuthContext(selected_mode="agent_identity", available_modes=("agent_identity",),
                                      conversation_boundary="one_to_one"),
            )
        render.assert_called_once()
        widget.assert_not_called()
        self.assertIs(send.await_args.kwargs["activity"], card_activity)

    async def test_widget_transport_failure_preserves_useful_plain_text_and_reports_failure(self):
        ctx = SimpleNamespace(activity={"from": {"id": "owner"}}, send_activity=AsyncMock(side_effect=RuntimeError("rejected")))
        auth = AgentAuthContext(selected_mode="agent_identity", available_modes=("agent_identity",),
                                conversation_boundary="one_to_one")
        with patch.object(gateway, "agent_auth_context", return_value=auth), \
             patch.object(gateway, "teams_conversation_type", return_value="personal"), \
             patch.object(gateway, "teams_runtime_source", return_value="teams_personal"), \
             patch.object(gateway, "delivery_reference_metadata", return_value={}), \
             patch.object(gateway, "send_typing_indicators", AsyncMock()), \
             patch.object(gateway, "owner_personal_chat", return_value=False), \
             patch.object(gateway, "invoke_agent_runtime", return_value=AgentResponse(
                 text="Useful budget summary.\n" + START + json.dumps(SPEC) + END,
             )), \
             patch.object(gateway, "send_teams_response", AsyncMock(return_value="text-id")) as send:
            await gateway.run_agent_runtime_for_teams(ctx, conversation_id="chat", session_key="s", message="Status")
        self.assertIn("Widget delivery failed.", send.await_args.args[1])
        self.assertIn("Useful budget summary.", send.await_args.args[1])
        self.assertNotIn(START, send.await_args.args[1])

    async def test_continuation_rejects_mixed_widget_and_file_markers_without_leaking_them(self):
        token = {"interactionId": "a" * 32, "sessionKey": "session", "conversationId": "chat",
                 "userId": "owner", "choiceMessage": "Discuss."}
        response = START + json.dumps(SPEC) + END + "<VISUAL_DOCUMENT_REQUEST>" + "a" * 32 + "</VISUAL_DOCUMENT_REQUEST>"
        with patch.object(gateway, "remember_teams_event"), \
             patch.object(gateway, "invoke_agent_runtime", return_value=AgentResponse(text=response)), \
             patch.object(gateway, "send_proactive_activity", AsyncMock()) as send:
            with self.assertRaises(ValueError):
                await gateway.continue_widget_selection(
                    token=token, values={}, delivery={},
                    auth=AgentAuthContext(selected_mode="agent_identity", available_modes=("agent_identity",),
                                          conversation_boundary="one_to_one"),
                )
        self.assertNotIn(START, send.await_args.args[2])
        self.assertNotIn("<VISUAL_DOCUMENT_REQUEST>", send.await_args.args[2])

    async def test_real_sdk_invoke_ack_and_single_claim_ignore_client_identity_metadata(self):
        token = action_token()
        ctx = TurnContext(gateway.agent365_adapter, Activity.model_validate({
            "type": "invoke", "name": "htmlwidget/calltool", "channelId": "msteams",
            "recipient": {"id": "otto"}, "serviceUrl": "https://smba.trafficmanager.net/teams/",
            "conversation": {"id": "chat", "conversationType": "personal"},
            "from": {"id": "spoofed", "aadObjectId": "not-an-authority"},
            "value": {"name": SUBMIT_TOOL, "arguments": {"token": token, "values": {"budget": "7"}}},
        }))
        adapter = SimpleNamespace(claim_interaction_action=AsyncMock(side_effect=[
            {"claimed": True}, {"claimed": False},
        ]))
        tasks = []
        with (
            patch.object(gateway, "runtime_adapter", return_value=adapter),
            patch.object(gateway, "delivery_reference_metadata", return_value={"boundary": "one_to_one"}),
            patch.object(gateway, "start_background_turn", side_effect=tasks.append),
            patch.object(gateway, "continue_widget_selection", new_callable=AsyncMock) as continuation,
        ):
            await gateway.handle_teams_invoke(ctx, None)
            response = gateway.agent365_adapter._process_turn_results(ctx)
            self.assertEqual(response.status, 200)
            self.assertEqual(response.body["responseType"], "htmlwidget/calltoolresult")
            self.assertFalse(response.body["callToolResult"]["isError"])
            self.assertEqual(continuation.call_args.kwargs["auth"].invoking_user_id, "original-owner")
            await gateway.handle_teams_invoke(ctx, None)
            self.assertEqual(len(tasks), 1)
        for coroutine in tasks:
            await coroutine

    async def test_disabled_unknown_expired_and_cross_chat_submissions_have_mcp_error(self):
        token = action_token()
        for name, chat, gate, supplied_token in (
            ("arbitrary.tool", "chat", "true", token),
            (SUBMIT_TOOL, "other-chat", "true", token),
            (SUBMIT_TOOL, "chat", "false", token),
            (SUBMIT_TOOL, "chat", "true", "expired"),
        ):
            ctx = TurnContext(gateway.agent365_adapter, Activity.model_validate({
                "type": "invoke", "name": "htmlwidget/calltool", "channelId": "msteams",
                "recipient": {"id": "otto"}, "serviceUrl": "https://smba.trafficmanager.net/teams/",
                "from": {"id": "owner"}, "conversation": {"id": chat, "conversationType": "personal"},
                "value": {"name": name, "arguments": {"token": supplied_token}},
            }))
            with self.subTest(name=name, chat=chat, gate=gate), \
                 patch.dict(os.environ, {"TEAMS_HTML_WIDGETS_ENABLED": gate}), \
                 patch.object(gateway, "start_background_turn") as schedule:
                await gateway.handle_teams_invoke(ctx, None)
            schedule.assert_not_called()
            result = gateway.agent365_adapter._process_turn_results(ctx).body["callToolResult"]
            self.assertTrue(result["isError"])
            self.assertIsInstance(result["structuredContent"], dict)

    async def test_unsolicited_model_context_message_is_not_a_new_user_turn(self):
        for value in ({"method": "ui/update-model-context", "params": {"content": "approve"}},
                      {"type": "widgetModelContext", "data": {"method": "ui/update-model-context"}}):
            ctx = SimpleNamespace(activity={"value": value})
            with patch.object(gateway, "accept_learning_action", AsyncMock()) as accept:
                await gateway.handle_teams_message(ctx, None)
            accept.assert_not_awaited()
