import tempfile
from pathlib import Path
from openminion.modules.session.storage.repository import create_sqlite_cron_repository
from openminion.modules.task import TaskManager

repo = create_sqlite_cron_repository(db_path=Path(tempfile.mkdtemp())/"t.db")
mgr = TaskManager.from_cron_repository(repo)
for tz in ("UTC", "Asia/Kuala_Lumpur", None):
    try:
        t = mgr.create_task(name=f"daily-{tz}", instruction="x", schedule={"kind":"cron","expr":"0 9 * * *", **({"tz": tz} if tz else {})})
        print(f"tz={tz!r}: OK")
    except Exception as e:
        print(f"tz={tz!r}: {type(e).__name__}: {e}")
