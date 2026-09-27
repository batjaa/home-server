#!/usr/bin/env python3
"""Preserve existing ASUS forwards and ensure public HTTP reaches SWAG."""
import urllib.request, urllib.parse, http.cookiejar, ssl, subprocess, json, secrets, hashlib
BASE='https://192.168.50.1:8443/'
jar=http.cookiejar.CookieJar()
client=urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl._create_unverified_context()),urllib.request.HTTPCookieProcessor(jar))
def req(path,data=None,js=False):
 if data is not None: data=(json.dumps(data) if js else urllib.parse.urlencode(data)).encode()
 r=urllib.request.Request(BASE+path,data=data,headers={'Referer':BASE+'Main_Login.asp','Content-Type':'application/json' if js else 'application/x-www-form-urlencoded','User-Agent':'Mozilla/5.0'})
 return client.open(r,timeout=15).read().decode()
def login():
 item=json.loads(subprocess.check_output(['op','item','get','iocrll3kw4i3swbps2pwrzhh6i','--vault','Private','--format','json']))
 f={v['id']:v.get('value','') for v in item['fields']}
 ident=secrets.token_hex(5);cnonce=secrets.token_hex(16)
 nonce=json.loads(req('get_Nonce.cgi',{'id':ident},True))['nonce']
 digest=hashlib.sha256(f'{f["username"]}:{nonce}:{f["password"]}:{cnonce}'.encode()).hexdigest()
 result=req('login_v2.cgi',{'id':ident,'cnonce':cnonce,'login_authorization':digest,'login_captcha':'','next_page':'index.asp'})
 if not any(c.name in ('asus_token','asus_s_token') for c in jar): raise RuntimeError('Router login failed')
def read(keys):
 return json.loads(req('appGet.cgi?'+urllib.parse.urlencode({'hook':';'.join('nvram_get('+k+')' for k in keys)})))

def forwarding_rules():
 value=json.loads(req('appGet.cgi?'+urllib.parse.urlencode({'hook':'nvram_char_to_ascii(vts_rulelist,vts_rulelist)'})))['vts_rulelist']
 return urllib.parse.unquote(value)

def main():
 import argparse
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('command',choices=['check','apply','version'])
 args=parser.parse_args()
 if args.command=='version': print('dev');return
 login()
 try:
  old=forwarding_rules()
  if read(['vts_enable_x'])['vts_enable_x']!='1': raise RuntimeError('Port forwarding is disabled; inspect router before continuing')
  existing=[row.split('>') for row in old.split('<') if row]
  for row in existing:
   if len(row)<6: raise RuntimeError('Unexpected router rule format')
   if row[1]=='80' and row[4] in ('TCP','BOTH'):
    if row[2:4]==['192.168.50.20','80'] and row[5]=='': print('OK HTTP already forwards to SWAG');return
    raise RuntimeError('Conflicting public port 80 rule; preserving it')
   if row[4] in ('TCP','BOTH') and any(c in row[1] for c in ':,-'):
    raise RuntimeError('Port ranges require manual conflict inspection')
  if args.command=='check': print('MISSING TCP 80 -> 192.168.50.20:80');return
  desired=old+'<Jolly HTTP>80>192.168.50.20>80>TCP>'
  payload={'action_mode':'apply','rc_service':'restart_firewall','vts_rulelist':desired}
  request=urllib.request.Request(BASE+'applyapp.cgi',data=urllib.parse.quote(json.dumps(payload),safe='').encode(),headers={'Referer':BASE+'Advanced_VirtualServer_Content.asp','Content-Type':'application/x-www-form-urlencoded','User-Agent':'Mozilla/5.0'})
  client.open(request,timeout=30).read()
  if forwarding_rules()!=desired: raise RuntimeError('Router did not retain the expected rule list')
  print('CHANGED TCP 80 -> 192.168.50.20:80; existing forwards preserved')
 finally: req('Logout.asp')

if __name__=='__main__': main()
