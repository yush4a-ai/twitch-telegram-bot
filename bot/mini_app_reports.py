"""Existing per-stream report settings, with the same recipient and permission rules."""
import asyncio
from aiohttp import web
from .mini_app_auth import verified_payload
from .streamer_community import check_community_permission
from .mini_app_viewer import normalize_twitch_login


def install_report_routes(app,db,bot_token,bot):
    async def item(user,chat,login,title,channel):
        cursor=await db.conn.execute('SELECT auto_report_enabled,channel_report_enabled,report_format FROM tracked_channels WHERE chat_id=? AND twitch_login=?',(chat,login))
        row=await cursor.fetchone()
        recipient=await db.resolve_post_recipient(chat,login)
        return {'login':login,'chat_id':chat,'title':title,'available':row is not None,
                'enabled':bool(row[1 if channel else 0]) if row else False,
                'format':row[2] if row else 'brief',
                'recipient':'self' if recipient==user else 'channel' if recipient==chat and chat<0 else 'linked_private' if recipient and recipient>0 else 'none'}

    async def placement(user,chat):
        identity=await db.get_streamer_identity(user)
        stored=await db.list_streamer_communities(user) if identity else []
        row=next((row for row in stored if row[0]==chat),None)
        if row is None:raise web.HTTPForbidden(content_type='application/json',text='{"error":"placement_denied"}')
        permission=await check_community_permission(bot,chat,user) if bot else None
        if permission is None or permission.status=='network_error':raise web.HTTPServiceUnavailable(content_type='application/json',text='{"error":"verification_unavailable"}')
        if permission.community is None or permission.community.chat_type!=row[2]:raise web.HTTPForbidden(content_type='application/json',text='{"error":"permission_denied"}')
        return identity[1],row[1],row[2]=='channel'

    async def reports(request):
        user,values,status=await verified_payload(request,bot_token)
        if status!=200:return web.json_response({'error':'unauthorized'},status=status)
        if set(values)!={'init_data'}:return web.json_response({'error':'invalid_settings'},status=400)
        if request.match_info['report_role']=='viewer':
            items=[await item(user,user,login,login,False) for login in await db.list_channels(user)]
        else:
            semaphore=asyncio.Semaphore(3)
            async def checked(row):
                async with semaphore:
                    try:
                        login,title,channel=await placement(user,row[0])
                        return await item(user,row[0],login,title,channel)
                    except (web.HTTPForbidden,web.HTTPServiceUnavailable) as error:
                        return {'chat_id':row[0],'title':row[1],'available':False,
                                'permission_status':'network_error' if error.status==503 else 'permission_denied'}
            items=await asyncio.gather(*(checked(row) for row in await db.list_streamer_communities(user)))
        return web.json_response({'items':items})

    async def save(request):
        user,values,status=await verified_payload(request,bot_token)
        if status!=200:return web.json_response({'error':'unauthorized'},status=status)
        viewer=request.match_info['report_role']=='viewer';key='login' if viewer else 'chat_id'
        enabled,format_=values.get('enabled'),values.get('format')
        if set(values)!={'init_data',key,'enabled','format'} or type(enabled) is not bool or format_ not in ('brief','full'):
            return web.json_response({'error':'invalid_settings'},status=400)
        if viewer:
            chat=user;login=normalize_twitch_login(values.get('login'));title=login;channel=False
            if login is None:return web.json_response({'error':'invalid_settings'},status=400)
        else:
            chat=values.get('chat_id')
            if type(chat) is not int or chat>=0:return web.json_response({'error':'invalid_settings'},status=400)
            login,title,channel=await placement(user,chat)
        try:
            saved=await db.save_report_preferences(chat,login,enabled,format_,channel=channel)
        except Exception:
            return web.json_response({'error':'save_unavailable'},status=503)
        if not saved:return web.json_response({'error':'not_subscribed'},status=404)
        return web.json_response({'item':await item(user,chat,login,title,channel)})

    app.router.add_post('/app/api/{report_role:viewer|streamer}/reports',reports)
    app.router.add_post('/app/api/{report_role:viewer|streamer}/reports/save',save)
