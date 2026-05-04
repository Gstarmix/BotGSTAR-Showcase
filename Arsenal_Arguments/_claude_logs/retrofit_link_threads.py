"""
retrofit_link_threads.py — Retrofit Y.9/Y.11/Y.12/Y.13 pour les drops
historiques de `🔗・liens` :

- Y.9  : crée un fil sur le message d'origine si absent.
- Y.11 : forward les embeds Pipeline + "✅ Dossier indexé" depuis #logs
         vers le fil (skip ceux déjà présents).
- Y.12 : archive le fil à la fin.
- Y.13 : remove_user(author) pour ne pas auto-follow.

Usage :
    python retrofit_link_threads.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

GUILD_ID = 1466806132998672466
LIENS_CHANNEL_ID = 1498918445763268658
LOGS_CHANNEL_ID = 1493760267300110466
LOGS_SCAN_CAP = 12000
UA = "BotGSTAR-RetrofitY9/1.3"

PMAP_PATH = Path(__file__).resolve().parent.parent / 'datas' / 'arsenal_published_threads.json'

_TIKTOK_RESOLVE_CACHE: dict[str, str] = {}


def resolve_tiktok_short(url: str) -> str:
    """HEAD redirect pour `vm.tiktok.com/X` ou `vt.tiktok.com/X` →
    `tiktok.com/@user/video/<numeric>`. No-op sinon. Cache mémoire."""
    if 'vm.tiktok.com' not in url and 'vt.tiktok.com' not in url:
        return url
    if url in _TIKTOK_RESOLVE_CACHE:
        return _TIKTOK_RESOLVE_CACHE[url]
    try:
        req = urllib.request.Request(
            url, method='HEAD',
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                                   'Chrome/120.0.0.0 Safari/537.36'},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            final = resp.geturl().split('?')[0].split('#')[0]
        _TIKTOK_RESOLVE_CACHE[url] = final
        return final
    except Exception as e:
        print(f'  ⚠ resolve_tiktok_short({url}) : {e}')
        _TIKTOK_RESOLVE_CACHE[url] = url
        return url

env_file = Path(r'C:\Users\Gstar\OneDrive\Documents\BotGSTAR\.env')
if env_file.exists():
    for line in env_file.read_text(encoding='utf-8').splitlines():
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
TOKEN = os.environ['DISCORD_BOT_TOKEN']

URL_RE = re.compile(r'https?://[^\s<>]+')
DOSSIER_ID_RE = re.compile(r'ID `([^`]+)`')

PLATFORM_PATTERNS = [
    ("TikTok", re.compile(r'tiktok\.com/@[\w.-]+/video/(\d+)', re.I)),
    ("TikTok", re.compile(r'(?:vm|vt)\.tiktok\.com/([\w-]+)', re.I)),
    ("Instagram", re.compile(r'instagram\.com/(?:p|reel|reels)/([\w-]+)', re.I)),
    ("YouTube", re.compile(r'(?:youtube\.com/watch\?v=|youtu\.be/)([\w-]+)', re.I)),
    ("X", re.compile(r'(?:twitter|x)\.com/\w+/status/(\d+)', re.I)),
    ("Reddit", re.compile(r'reddit\.com/r/\w+/comments/([\w]+)', re.I)),
    ("Threads", re.compile(r'threads\.(?:net|com)/@[\w.-]+/post/([\w-]+)', re.I)),
]


def api(method: str, path: str, body=None):
    for attempt in range(3):
        req = urllib.request.Request(
            f'https://discord.com/api/v10{path}',
            method=method,
            headers={
                'Authorization': f'Bot {TOKEN}',
                'User-Agent': UA,
                'Content-Type': 'application/json',
            },
            data=json.dumps(body).encode() if body else None,
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                if r.status == 204:
                    return None
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                payload = json.loads(e.read() or b"{}")
                retry_after = payload.get('retry_after', 2)
                time.sleep(retry_after + 0.5)
                continue
            raise
    raise RuntimeError("3 retries 429 épuisés")


def extract_id_from_url(url: str) -> tuple[str | None, str | None]:
    # v1.2 : résoudre d'abord les short links TikTok pour obtenir l'ID
    # numérique avec lequel le pipeline a indexé le Dossier (sinon on
    # cherche `ZNRgTocL3` quand le bot a publié sur `7610...`).
    url = resolve_tiktok_short(url)
    for plat, pat in PLATFORM_PATTERNS:
        m = pat.search(url)
        if m:
            return plat, m.group(1)
    return None, None


def scan_logs() -> tuple[dict, dict]:
    """Scanne #logs en arrière, retourne :
    - pipeline_runs_by_cid : dict content_id → list[(ts, embed)]
    - dossier_indexes_by_cid : dict content_id → embed
    """
    print(f'Scan #logs (cap {LOGS_SCAN_CAP} msgs)…')
    pipeline_runs = []
    current_run = None
    dossier_embeds = {}  # cid → embed
    before = None
    total_msgs = 0
    while True:
        path = f'/channels/{LOGS_CHANNEL_ID}/messages?limit=100'
        if before:
            path += f'&before={before}'
        msgs = api('GET', path)
        if not msgs:
            break
        for m in msgs:
            total_msgs += 1
            for em in m.get('embeds', []):
                title = em.get('title', '')
                # Pipeline embed
                if title.startswith('⚙️ Pipeline | '):
                    step_title = title.replace('⚙️ Pipeline | ', '')
                    if step_title == 'Démarrage':
                        desc = em.get('description', '') or ''
                        m_url = re.search(r'`(https?://[^\s`]+)`', desc)
                        if m_url:
                            url = m_url.group(1)
                            plat, cid = extract_id_from_url(url)
                            if cid:
                                if current_run is not None:
                                    current_run['embeds'].insert(0, (m['timestamp'], em))
                                    current_run['url'] = url
                                    current_run['content_id'] = cid
                                    pipeline_runs.append(current_run)
                                else:
                                    pipeline_runs.append({
                                        'url': url, 'content_id': cid,
                                        'embeds': [(m['timestamp'], em)],
                                    })
                                current_run = {'url': None, 'content_id': None, 'embeds': []}
                                continue
                    if current_run is None:
                        current_run = {'url': None, 'content_id': None, 'embeds': []}
                    current_run['embeds'].insert(0, (m['timestamp'], em))
                # Dossier indexé embed (publisher)
                elif title.startswith('✅ Dossier indexé'):
                    desc = em.get('description', '') or ''
                    m_id = DOSSIER_ID_RE.search(desc)
                    if m_id:
                        cid = m_id.group(1)
                        # Garder le PLUS RÉCENT par cid (= la 1ère rencontre en lecture newest-first)
                        if cid not in dossier_embeds:
                            dossier_embeds[cid] = em
        before = msgs[-1]['id']
        if len(msgs) < 100:
            break
        if total_msgs >= LOGS_SCAN_CAP:
            print(f'  ⚠ stop scan à {total_msgs} msgs (cap atteint)')
            break

    by_cid = {}
    for run in pipeline_runs:
        cid = run['content_id']
        if not cid: continue
        if cid not in by_cid or run['embeds'][0][0] > by_cid[cid]['embeds'][0][0]:
            by_cid[cid] = run
    print(f'  Scanné {total_msgs} msgs : {len(by_cid)} runs Pipeline + {len(dossier_embeds)} Dossier indexé')
    return by_cid, dossier_embeds


def scan_liens_messages() -> list[dict]:
    """Scanne #liens, retourne les messages utilisateur avec URL +
    leur état (thread, author_id)."""
    print(f'Scan #liens…')
    out = []
    before = None
    while True:
        path = f'/channels/{LIENS_CHANNEL_ID}/messages?limit=100'
        if before:
            path += f'&before={before}'
        msgs = api('GET', path)
        if not msgs:
            break
        for m in msgs:
            if m.get('author', {}).get('bot'):
                continue
            urls = URL_RE.findall(m.get('content', ''))
            if not urls:
                continue
            url_infos = []
            for u in urls:
                plat, cid = extract_id_from_url(u)
                if cid:
                    url_infos.append({'url': u, 'platform': plat, 'content_id': cid})
            if not url_infos:
                continue
            out.append({
                'message_id': m['id'],
                'channel_id': LIENS_CHANNEL_ID,
                'urls': url_infos,
                'thread': m.get('thread'),  # full thread object si existe, else None
                'author_id': m.get('author', {}).get('id'),
            })
        before = msgs[-1]['id']
        if len(msgs) < 100:
            break
    print(f'  {len(out)} messages utilisateur avec URL')
    return out


def post_embed(channel_id: str, embed_data: dict) -> bool:
    payload = {'embeds': [{
        'title': embed_data.get('title'),
        'description': embed_data.get('description'),
        'color': embed_data.get('color'),
        'fields': embed_data.get('fields', []),
        'footer': embed_data.get('footer'),
        'timestamp': embed_data.get('timestamp'),
    }]}
    try:
        api('POST', f'/channels/{channel_id}/messages', payload)
        return True
    except Exception as e:
        print(f'    ✗ post embed fail : {e}')
        return False


def get_thread_existing_titles(thread_id: str) -> set[str]:
    """Liste les titres d'embeds déjà présents dans le fil (pour skip
    duplication)."""
    titles = set()
    try:
        msgs = api('GET', f'/channels/{thread_id}/messages?limit=50')
        for m in msgs:
            for em in m.get('embeds', []):
                t = em.get('title', '')
                if t:
                    titles.add(t)
    except Exception:
        pass
    return titles


def load_pmap() -> dict:
    """v1.3 : charge la map source_id → thread_id (Y.15) pour pouvoir
    construire le Dossier indexé même quand l'embed n'est plus dans la
    fenêtre #logs scannée. Clé : `{platform_lower}::{source_id}`."""
    if not PMAP_PATH.exists():
        return {}
    try:
        return json.loads(PMAP_PATH.read_text(encoding='utf-8'))
    except Exception as e:
        print(f'  ⚠ load_pmap : {e}')
        return {}


def build_dossier_from_pmap(plat: str, cid: str, pmap: dict) -> dict | None:
    """v1.3 : reconstruit un embed minimal `✅ Dossier indexé` à partir
    de la pmap si l'embed original n'est plus dans #logs. Le titre exact
    et la note seront perdus mais le lien Discord vers le thread reste
    fonctionnel.

    v1.4 : essaie plusieurs clés car le publisher écrit `::cid` quand
    `parse_analysis` ne récupère pas la plateforme (cas SRC_ : X,
    YouTube, Reddit, Threads — leur summary file n'a pas de header
    `PLATEFORME — …`)."""
    keys_to_try = [
        f"{(plat or '').lower()}::{cid}",
        f"::{cid}",
        f"unknown::{cid}",
    ]
    entry = None
    for k in keys_to_try:
        entry = pmap.get(k)
        if entry:
            break
    if not entry:
        return None
    thread_id = entry.get('thread_id')
    if not thread_id:
        return None
    title = entry.get('title') or '(reconstruit)'
    return {
        'title': '✅ Dossier indexé',
        'description': (
            f"ID `{cid}`\n"
            f"🔗 [Ouvrir](https://discord.com/channels/{GUILD_ID}/{thread_id})"
        ),
        'color': 0x57F287,  # vert Discord
        'fields': [
            {'name': 'Titre', 'value': str(title)[:80] or 'N/A', 'inline': True},
            {'name': 'Source', 'value': '_(reconstruit depuis Y.15 map)_', 'inline': True},
        ],
    }


def fix_message(lm: dict, runs_by_cid: dict, dossier_by_cid: dict,
                pmap: dict, dry_run: bool):
    """Applique Y.9/Y.11/Y.12/Y.13 sur un message #liens."""
    primary = lm['urls'][0]
    cid = primary['content_id']
    msg_id = lm['message_id']

    # 1. Récupérer ou créer le fil
    thread = lm.get('thread')
    actions = []

    if not thread:
        # Vérifier qu'on a au moins UN run pipeline pour le créer
        run = runs_by_cid.get(cid)
        if not run:
            # Pas de run trouvé dans la fenêtre scannée → fil vide pas
            # utile, on skip.
            return f'  msg={msg_id} : pas de fil + pas de run trouvé → skip'
        thread_name = f"📱 {primary['platform']} · {cid[:60]}"[:100]
        if dry_run:
            actions.append(f'CREATE thread "{thread_name}"')
            actions.append(f'POST {len(run["embeds"])} pipeline embeds')
        else:
            try:
                t = api('POST', f'/channels/{lm["channel_id"]}/messages/{msg_id}/threads',
                        {'name': thread_name, 'auto_archive_duration': 60})
                thread_id = t['id']
                actions.append(f'created thread {thread_id}')
                # Post pipeline embeds
                for ts, em in run['embeds']:
                    post_embed(thread_id, em)
                    time.sleep(0.4)
                actions.append(f'posted {len(run["embeds"])} pipeline embeds')
                thread = {'id': thread_id}
            except urllib.error.HTTPError as e:
                return f'  msg={msg_id} : create_thread fail : {e}'
    else:
        thread_id = thread['id']

    if not thread:
        return ' '.join([f'msg={msg_id}'] + actions)

    # 2. Forward Dossier indexé si manquant
    # v1.3 : on regarde DEUX sources d'embed dans cet ordre :
    #   (a) #logs scan (dossier_by_cid) — embed exact tel que posté
    #   (b) pmap Y.15 fallback — reconstruit minimal mais fiable
    # On ne post qu'UN des deux, jamais les deux.
    dossier_em = dossier_by_cid.get(cid)
    source_label = 'logs'
    if not dossier_em:
        dossier_em = build_dossier_from_pmap(primary['platform'], cid, pmap)
        source_label = 'pmap'
    if dossier_em:
        existing_titles = get_thread_existing_titles(thread['id'])
        already_has_dossier = any('Dossier indexé' in t for t in existing_titles)
        if not already_has_dossier:
            if dry_run:
                actions.append(f'would POST Dossier indexé embed ({source_label})')
            else:
                if post_embed(thread['id'], dossier_em):
                    actions.append(f'posted Dossier indexé ({source_label})')
                time.sleep(0.4)

    # 3. Remove author from thread (Y.13) — silence ping
    if lm.get('author_id'):
        if dry_run:
            actions.append(f'would remove author {lm["author_id"]}')
        else:
            try:
                api('DELETE', f'/channels/{thread["id"]}/thread-members/{lm["author_id"]}')
                actions.append('removed author from thread')
            except urllib.error.HTTPError as e:
                if e.code != 404:  # 404 = déjà absent, OK
                    actions.append(f'remove_author fail : {e.code}')

    # 4. Archive thread (Y.12)
    if dry_run:
        actions.append('would ARCHIVE')
    else:
        try:
            api('PATCH', f'/channels/{thread["id"]}', {'archived': True})
            actions.append('archived')
        except urllib.error.HTTPError as e:
            actions.append(f'archive fail : {e.code}')

    return f'  msg={msg_id} cid={cid} : {" | ".join(actions)}'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    runs_by_cid, dossier_by_cid = scan_logs()
    pmap = load_pmap()
    print(f'  pmap Y.15 : {len(pmap)} entrées chargées')
    liens_msgs = scan_liens_messages()

    print(f'\nProcessing {len(liens_msgs)} messages…')
    for lm in liens_msgs:
        result = fix_message(lm, runs_by_cid, dossier_by_cid, pmap, args.dry_run)
        print(result)


if __name__ == '__main__':
    main()
