import json
import unittest
from unittest.mock import Mock, patch

from sqlmodel import SQLModel

from app.db import engine, get_session
from app.models import Mission, SlackChannelBinding
from app.services.notifications import emit_controlplane_event


class NotificationsTests(unittest.TestCase):
    def setUp(self):
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        with get_session() as session:
            session.add(Mission(id="mission-a", name="mission-a", owners="owner@example.com"))
            session.add(
                SlackChannelBinding(
                    provider="slack",
                    mission_id="mission-a",
                    channel_id="C1",
                    channel_name="ops",
                    channel_metadata_json=json.dumps({}),
                )
            )
            session.commit()

    def test_inbound_received_events_do_not_fanout_to_chat(self):
        provider = Mock()
        with patch("app.services.notifications.get_chat_provider", return_value=provider):
            emit_controlplane_event(req=None, event_type="slack.event.received", payload={"mission_id": "mission-a"})
        provider.send_event_notification.assert_not_called()

    def test_high_value_events_fanout_to_chat(self):
        provider = Mock()
        with patch("app.services.notifications.get_chat_provider", return_value=provider):
            emit_controlplane_event(
                req=None,
                event_type="approval.approved",
                payload={"mission_id": "mission-a", "approval_request_id": 42},
            )
        provider.send_event_notification.assert_called_once()


if __name__ == "__main__":
    unittest.main()
