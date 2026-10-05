"""Durable pending jobs; database stays in the private workspace, outside Git."""
import json
import sqlite3


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
            "message": item.message.model_dump(mode="json", exclude_none=True),
            "status_msg": item.status_msg.model_dump(mode="json", exclude_none=True),
        })
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO jobs(id, data) VALUES (?, ?)", (item.job_id, data))

    def pending(self):
        with sqlite3.connect(self.path) as db:
            return [(job_id, json.loads(data)) for job_id, data in db.execute("SELECT id, data FROM jobs ORDER BY rowid")]

    def remove(self, job_id):
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
