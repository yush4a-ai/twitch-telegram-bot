import io,zipfile,shutil
upload=pathlib.Path(UPLOAD).resolve()
assert upload.parent==pathlib.Path('/tmp') and upload.name.startswith('twitchsignal-telegram-ui-')
encoded=''.join(p.read_text() for p in sorted(upload.glob('part-*')))
payload=base64.b64decode(encoded,validate=True)
assert hashlib.sha256(payload).hexdigest()==ZIP_SHA
backup=pathlib.Path(BACKUP).resolve()
assert backup.parent==pathlib.Path('/data/backups') and backup.name.startswith('telegram-ui-20261003-')
assert hashlib.sha256(backup.read_bytes()).hexdigest()==BACKUP_SHA
def q(name):return '"'+name.replace('"','""')+'"'
def snapshot(path,columns=None):
 c=sqlite3.connect(path)
 try:
  assert c.execute('PRAGMA integrity_check').fetchone()==('ok',)
  assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
  tables=columns or {r[0]:[x[1] for x in c.execute('PRAGMA table_info('+q(r[0])+')')] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
  result={}
  for name,cols in tables.items():
   rows=c.execute('SELECT '+','.join(map(q,cols))+' FROM '+q(name)).fetchall()
   if name=='schema_migrations' and columns:
    rows=[r for r in rows if r[0] in initial_versions]
   values=sorted(json.dumps(row,ensure_ascii=True,separators=(',',':'),default=lambda b:{'bytes':base64.b64encode(b).decode()}) for row in rows)
   result[name]={'columns':cols,'count':len(rows),'sha256':hashlib.sha256('\n'.join(values).encode()).hexdigest()}
  return result
 finally:c.close()
with tempfile.TemporaryDirectory(prefix='twitchsignal-migration-copy-',dir='/tmp') as directory:
 root=pathlib.Path(directory); package=root/'package';package.mkdir()
 with zipfile.ZipFile(io.BytesIO(payload)) as archive:
  assert sorted(archive.namelist())==sorted(SOURCE_HASHES)
  for name in archive.namelist():
   data=archive.read(name);assert hashlib.sha256(data).hexdigest()==SOURCE_HASHES[name]
   path=package/name;assert path.resolve().is_relative_to(package.resolve());path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
 sys.path.insert(0,str(package))
 assert not any(n=='bot' or n.startswith('bot.') for n in sys.modules)
 import bot.database as module
 assert pathlib.Path(module.__file__).resolve()==(package/'bot/database.py').resolve()
 from bot.database import Database
 migrated=root/'migrated.db'; failed=root/'failed.db';shutil.copyfile(backup,migrated);shutil.copyfile(backup,failed)
 before=snapshot(backup);columns={n:v['columns'] for n,v in before.items()}
 with sqlite3.connect(backup) as c:initial_versions=[r[0] for r in c.execute('SELECT version FROM schema_migrations')]
 async def run():
  db=Database(str(migrated),os.environ['TOKEN_ENCRYPTION_KEY']);await db.connect()
  try:
   versions=await db.schema_versions(); tokens=await (await db.conn.execute('SELECT access_token,refresh_token FROM twitch_user_tokens')).fetchall()
   checked=sum(1 for row in tokens for token in row if token.startswith('fernet:v1:') and isinstance(db._decrypt_token(token),str))
   assert checked==sum(token.startswith('fernet:v1:') for row in tokens for token in row)
  finally:await db.close()
  assert snapshot(migrated,columns)==before,'Existing table IDs and data changed'
  repeat=Database(str(migrated),os.environ['TOKEN_ENCRYPTION_KEY']);await repeat.connect()
  try:assert await repeat.schema_versions()==versions
  finally:await repeat.close()
  assert snapshot(migrated,columns)==before,'Reopen changed existing data'
  full_after=snapshot(migrated)
  original=module.migrate_plus_payments
  async def inject(conn,*,now):
   await original(conn,now=now)
   raise RuntimeError('Injected post-migration failure')
  module.migrate_plus_payments=inject
  try:
   try:await Database(str(failed),os.environ['TOKEN_ENCRYPTION_KEY']).connect()
   except RuntimeError as error:assert str(error)=='Injected post-migration failure'
   else:raise AssertionError('Failure injection did not abort')
  finally:module.migrate_plus_payments=original
  assert snapshot(failed,columns)==before,'Failure changed legacy rows'
  with sqlite3.connect(failed) as c:
   assert [r[0] for r in c.execute('SELECT version FROM schema_migrations')]==initial_versions
  assert snapshot(failed)==before,'Failure changed current snapshot tables or rows'
  assert hashlib.sha256(backup.read_bytes()).hexdigest()==BACKUP_SHA
  return {'status':'PASS','backup_sha256':BACKUP_SHA,'all_existing_data_preserved':True,'tables_before':len(before),'tables_after':len(full_after),'existing_table_digests':before,'schema_versions':versions,'added_versions':sorted(set(versions)-set(initial_versions)),'foreign_key_check':[],'integrity':'ok','repeat_reopen':True,'injected_failure_rollback':True,'fernet_fields_checked':checked,'key_exported':False,'active_db_replaced':False,'network_sends':0}
 result=asyncio.run(run())
assert not root.exists()
result['temp_copy_removed']=True
shutil.rmtree(upload)
print('TS_QA_JSON='+json.dumps(result))
