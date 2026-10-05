"""Read-only проверка сохранности legacy reports после cutover."""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
import post_cutover_check as p  # noqa: E402

BODY = """
db=sqlite3.connect('file:/data/bot.db?mode=ro',uri=True);db.execute('PRAGMA query_only=ON')
tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
report_tables=[t for t in tables if 'report' in t.lower()]
result={'report_tables':report_tables}
cols=[r[1] for r in db.execute('PRAGMA table_info(stream_history)')]
result['stream_history_columns']=cols
for name in ('report_path','report_html','report_json','html_path'):
 if name in cols:
  result['stream_history_non_null_'+name]=db.execute('SELECT count(*) FROM stream_history WHERE "%s" IS NOT NULL'%name).fetchone()[0]
result['counts']={t:db.execute('SELECT count(*) FROM "%s"'%t).fetchone()[0] for t in report_tables}
db.close()
print('TS_QA_JSON='+json.dumps(result))
"""

if __name__ == "__main__":
    result = p.remote(BODY)
    path = HERE / "POST-CUTOVER-DATA.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["reports"] = result
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
