"""Keep the latest uploaded installer; preserve tags, source history and notes."""
import json
import os
import re
import time
import urllib.request

MARKER = '此页仅作历史更新声明'
PACKAGE = re.compile(r'feibi-feiba-pet-switch-v\d+\.\d+\.\d+-windows\.zip')


def version(tag):
    if not re.fullmatch(r'v\d+\.\d+\.\d+',tag):
        return None
    return tuple(map(int,tag[1:].split('.')))


def retirement_plan(releases, latest):
    current = version(latest['tag_name'])
    expected = 'feibi-feiba-pet-switch-' + latest['tag_name'] + '-windows.zip'
    if current is None or not any(a['name']==expected and a.get('state')=='uploaded' for a in latest['assets']):
        raise ValueError('Latest stable installer is not uploaded; nothing may be removed')
    return [(release,[asset for asset in release['assets'] if PACKAGE.fullmatch(asset['name'])])
            for release in releases if release['id']!=latest['id'] and not release['draft']
            and not release['prerelease'] and version(release['tag_name']) is not None
            and version(release['tag_name'])<current]


def main():
    repository = os.environ['GITHUB_REPOSITORY']
    token = os.environ['GITHUB_TOKEN']
    event_tag = os.environ['RELEASE_TAG']
    base = 'https://api.github.com/repos/' + repository
    def api(path, method='GET', body=None):
        payload = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(base+path,data=payload,method=method,headers={
            'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
            'Content-Type':'application/json','User-Agent':'pet-release-retention'})
        with urllib.request.urlopen(request,timeout=60) as response:
            return None if response.status==204 else json.load(response)
    for attempt in range(30):
        latest = api('/releases/latest')
        if latest['tag_name']!=event_tag:
            print('A different stable release is latest; leaving its downloads untouched.')
            return
        try:
            retirement_plan([],latest)
            break
        except ValueError:
            if attempt==29:
                raise
            print('Waiting for the latest installer to finish uploading…',flush=True)
            time.sleep(10)
    releases = []
    page = 1
    while True:
        batch = api('/releases?per_page=100&page='+str(page))
        releases.extend(batch)
        if len(batch)<100:
            break
        page += 1
    for release, assets in retirement_plan(releases,latest):
        if MARKER not in (release.get('body') or ''):
            notes = ('## 历史更新声明\n\n'+MARKER+'，不再提供旧安装包。\n\n'
                     '**只下载一个：[最新完整安装包](https://github.com/'+repository+'/releases/latest)。**\n\n'
                     '已有宠物请复用，不要逐个版本重复创建或导入。GitHub自动生成的历史源码归档不是安装包。\n\n'
                     '<details><summary>当时的更新记录</summary>\n\n'+(release.get('body') or '')+'\n\n</details>')
            api('/releases/'+str(release['id']),'PATCH',{'name':release['tag_name']+' · 历史更新声明（请下载最新版）','body':notes})
        for asset in assets:
            api('/releases/assets/'+str(asset['id']),'DELETE')
        print('Retired '+release['tag_name']+': '+str(len(assets))+' old installer(s).',flush=True)


if __name__=='__main__':
    main()
