"""Durable pending jobs; database stays in the private workspace, outside Git."""
import json
import sqlite3


def message_reference(message):
    """Persist routing data, excluding preview objects and SDK default sentinels."""
    data = {"message_id": message.message_id, "date": int(message.date.timestamp()),
            "chat": {"id": message.chat.id, "type": message.chat.type}}
    if message.from_user:
        data["from"] = {"id": message.from_user.id, "is_bot": message.from_user.is_bot,
                        "first_name": "User"}
    for field in ["message_thread_id", "is_topic_message", "business_connection_id"]:
        value = getattr(message, field, None)
        if value is not None:
            data[field] = value
    if message.direct_messages_topic:
        data["direct_messages_topic"] = {"topic_id": message.direct_messages_topic.topic_id}
    return data


class JobStore:
    def __init__(self, path):
        self.path = path

    def initialize(self):
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        self.path.chmod(0o600)

    def put(self, item):
        data = json.dumps({
            "task_type": item.task_type, "payload": item.payload,
            "message": message_reference(item.message),
            "status_msg": message_reference(item.status_msg),
        })
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO jobs(id, data) VALUES (?, ?)", (item.job_id, data))

    def pending(self):
        with sqlite3.connect(self.path) as db:
            return [(job_id, json.loads(data)) for job_id, data in db.execute("SELECT id, data FROM jobs ORDER BY rowid")]

    def remove(self, job_id):
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
