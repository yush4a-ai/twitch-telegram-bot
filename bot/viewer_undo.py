"""Short-lived, owner-bound undo snapshots. Called inside the database write lock."""
import hashlib
import json
import secrets
import time

TRACKED_SETTINGS = ('notify_enabled','preview_enabled','added_at','auto_report_enabled',
                    'channel_report_enabled','post_recipient_chat_id','report_format',
                    'raid_detection_enabled','quiet_hours_exempt',
                    'is_live','last_stream_id','last_message_id','last_message_kind',
                    'media_transition_pending','media_transition_target_kind','live_post_ended',
                    'last_title','offline_since','stream_started_at','last_seen_live_at',
                    'last_stream_ended_at','peak_viewers','viewer_sum','viewer_samples',
                    'stats_sent','followers_at_start','last_broadcaster_id')
TABLES = {
    'viewer_alert_filters': ('games_json','title_keywords_json','exclude_keywords_json','version','updated_at'),
    'category_alert_preferences': ('enabled','category_ids_json','category_names_json','version','updated_at'),
    'viewer_plan_priority': ('priority',),
    'viewer_folder_memberships': ('folder_id','updated_at'),
    'viewer_video_selections': ('broadcaster_id','position'),
    'viewer_favorites': (),
}


async def migrate(conn):
    await conn.execute('CREATE TABLE IF NOT EXISTS viewer_restored_sessions ('
                       'telegram_user_id INTEGER NOT NULL, twitch_login TEXT NOT NULL, stream_id TEXT NOT NULL, '
                       'PRIMARY KEY(telegram_user_id,twitch_login), FOREIGN KEY(telegram_user_id,twitch_login) '
                       'REFERENCES tracked_channels(chat_id,twitch_login) ON DELETE CASCADE) WITHOUT ROWID')
    await conn.execute('CREATE TABLE IF NOT EXISTS viewer_unfollow_undo ('
                       'token_hash TEXT PRIMARY KEY, telegram_user_id INTEGER NOT NULL, '
                       'twitch_login TEXT NOT NULL, payload TEXT NOT NULL, expires_at REAL NOT NULL) WITHOUT ROWID')
    await conn.execute('CREATE INDEX IF NOT EXISTS idx_viewer_undo_owner '
                       'ON viewer_unfollow_undo(telegram_user_id,twitch_login)')
    # Every subscription creation/removal path invalidates earlier snapshots, including ABA.
    for event, reference in [('INSERT','NEW'),('DELETE','OLD')]:
        await conn.execute(f'CREATE TRIGGER IF NOT EXISTS viewer_undo_invalidate_{event.lower()} '
                           f'AFTER {event} ON tracked_channels BEGIN DELETE FROM viewer_unfollow_undo '
                           f'WHERE telegram_user_id={reference}.chat_id AND twitch_login={reference}.twitch_login; END')
    await conn.execute('CREATE TRIGGER IF NOT EXISTS viewer_undo_explicit_notify '
                       'AFTER UPDATE OF notify_enabled ON tracked_channels WHEN OLD.notify_enabled=0 AND NEW.notify_enabled=1 '
                       'BEGIN DELETE FROM viewer_restored_sessions WHERE telegram_user_id=NEW.chat_id AND twitch_login=NEW.twitch_login; END')
    await conn.execute("INSERT OR IGNORE INTO schema_migrations VALUES ('mini_011_viewer_undo',?)",(time.time(),))


async def snapshot(conn,user,login):
    cursor=await conn.execute('SELECT '+','.join(TRACKED_SETTINGS)+' FROM tracked_channels WHERE chat_id=? AND twitch_login=?',(user,login))
    row=await cursor.fetchone()
    if row is None:return None
    result={'settings':list(row),'rows':{}}
    for table,columns in TABLES.items():
        cursor=await conn.execute('SELECT '+(','.join(columns) or '1')+f' FROM {table} WHERE telegram_user_id=? AND twitch_login=?',(user,login))
        row=await cursor.fetchone()
        if row is not None:result['rows'][table]=list(row) if columns else []
    if 'viewer_folder_memberships' in result['rows']:
        cursor=await conn.execute('SELECT version FROM viewer_folders WHERE telegram_user_id=? AND id=?',(user,result['rows']['viewer_folder_memberships'][0]))
        row=await cursor.fetchone();result['folder_version']=row[0] if row else None
    return result


async def video_version(conn,user):
    cursor=await conn.execute('SELECT version FROM viewer_video_selection_state WHERE telegram_user_id=?',(user,))
    row=await cursor.fetchone()
    return row[0] if row else 0


async def issue(conn,user,login,payload):
    now=time.time()
    await conn.execute('DELETE FROM viewer_unfollow_undo WHERE expires_at<=?',(now,))
    payload['video_version']=await video_version(conn,user)
    token=secrets.token_urlsafe(32);expires=now+60
    await conn.execute('INSERT INTO viewer_unfollow_undo VALUES (?,?,?,?,?)',
                       (hashlib.sha256(token.encode()).hexdigest(),user,login,json.dumps(payload),expires))
    return {'undo_token':token,'undo_expires_at':expires}


async def restore(conn,user,token,limit):
    digest=hashlib.sha256(token.encode()).hexdigest();now=time.time()
    cursor=await conn.execute('SELECT twitch_login,payload,expires_at FROM viewer_unfollow_undo '
                              'WHERE token_hash=? AND telegram_user_id=?',(digest,user))
    saved=await cursor.fetchone()
    if saved is None or saved[2]<=now:return 'undo_unavailable',None
    login,payload=saved[0],json.loads(saved[1])
    cursor=await conn.execute('SELECT COUNT(*) FROM tracked_channels WHERE chat_id=?',(user,))
    if (await cursor.fetchone())[0]>=limit:return 'channel_limit',None
    cursor=await conn.execute('SELECT 1 FROM tracked_channels WHERE chat_id=? AND twitch_login=?',(user,login))
    if await cursor.fetchone():return 'undo_conflict',None
    rows=payload['rows']
    if 'viewer_video_selections' in rows and await video_version(conn,user)!=payload['video_version']:
        return 'undo_conflict',None
    if 'viewer_folder_memberships' in rows:
        cursor=await conn.execute('SELECT version FROM viewer_folders WHERE telegram_user_id=? AND id=?',(user,rows['viewer_folder_memberships'][0]))
        row=await cursor.fetchone()
        if not row or row[0]!=payload.get('folder_version'):return 'undo_conflict',None
    # Preferences for an absent row indicate a newer writer: never overwrite them.
    for table in TABLES:
        cursor=await conn.execute(f'SELECT 1 FROM {table} WHERE telegram_user_id=? AND twitch_login=?',(user,login))
        if await cursor.fetchone():return 'undo_conflict',None
    columns=('chat_id','twitch_login',*TRACKED_SETTINGS)
    await conn.execute('INSERT INTO tracked_channels ('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')',(user,login,*payload['settings']))
    settings=dict(zip(TRACKED_SETTINGS,payload['settings']))
    if settings['last_stream_id'] and settings['last_message_id'] is None:
        await conn.execute('INSERT INTO viewer_restored_sessions VALUES (?,?,?)',(user,login,settings['last_stream_id']))
    for table,values in rows.items():
        columns=TABLES[table]
        if 'version' in columns:values[columns.index('version')]+=1
        if 'updated_at' in columns:values[columns.index('updated_at')]=now
        all_columns=('telegram_user_id','twitch_login',*columns)
        await conn.execute(f'INSERT INTO {table} ('+','.join(all_columns)+') VALUES ('+','.join('?' for _ in all_columns)+')',(user,login,*values))
    if 'viewer_video_selections' in rows:
        await conn.execute('UPDATE viewer_video_selection_state SET version=version+1 WHERE telegram_user_id=?',(user,))
    # The INSERT trigger consumed the token. History, jobs and sent reminders stay untouched.
    return 'restored',login
