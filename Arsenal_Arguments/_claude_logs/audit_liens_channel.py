"""
audit_liens_channel.py — Audit complet des incohérences dans le
salon `🔗・liens` et fix automatique avec `--apply`.

Catégories détectées (= bug catégorisé pour ce drop) :

  A. user msg avec URL mais sans fil           → créer fil + post embeds
  B. fil sans `✅ Dossier indexé`               → post depuis #logs ou pmap Y.15
  C. fil sans `⚙️ Pipeline | Démarrage`         → post pipeline embeds depuis #logs
  D. fil sans `⚙️ Pipeline terminé`             → post final embed depuis #logs (si dispo)
  E. auteur encore membre du fil               → DELETE thread-members (silence ping Y.13)
  F. fil non archivé après pipeline complet    → PATCH archived=true (Y.12)
  G. system msg "X a commencé un fil"          → DELETE (Y.10)
  H. fil vide (zéro embed)                     → traité comme A
  I. 🔄 sans ✅/❌ (= pipeline jamais terminé)   → log only
  J. réaction ❌ posée par le bot               → flip en ✅ (post-fix audit)
  K. msg URL posté par un bot                  → log only
  M. ✅ Dossier indexé pas le DERNIER embed     → re-post en fin de fil

Usage :
    python audit_liens_channel.py             # dry-run + récap
    python audit_liens_channel.py --apply     # exécute les fixes
    python audit_liens_channel.py --report    # JSON détaillé sur stdout
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
from collections import Counter, defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

GUILD_ID = 1466806132998672466
LIENS_CHANNEL_ID = 1498918445763268658
LOGS_CHANNEL_ID = 1493760267300110466
LOGS_SCAN_CAP = 15000
UA = "BotGSTAR-AuditLiens/1.0"

env_file = Path(r'C:\Users\Gstar\OneDrive\Documents\BotGSTAR\.env')
if env_file.exists():
    for line in env_file.read_text(encoding='utf-8').splitlines():
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
TOKEN = os.environ['DISCORD_BOT_TOKEN']

PMAP_PATH = Path(__file__).resolve().parent.parent / 'datas' / 'arsenal_published_threads.json'

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

PIPELINE_EMBED_KEYS = [
    "Démarrage",
    "Download",
    "Transcription Whisper",
    "Résumé Claude",
    "Publication Discord",
    "Pipeline terminé",
]

_TIKTOK_RESOLVE_CACHE: dict[str, str] = {}


def resolve_tiktok_short(url: str) -> str:
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
    except Exception:
        _TIKTOK_RESOLVE_CACHE[url] = url
        return url


def api(method: str, path: str, body=None):
    last_err = None
    for attempt in range(6):
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
        except (urllib.error.URLError, ConnectionResetError, TimeoutError) as e:
            last_err = e
            time.sleep(min(2 ** attempt, 30))
            continue
    raise RuntimeError(f"6 retries épuisés (dernière erreur : {last_err})")


def extract_id(url: str) -> tuple[str | None, str | None]:
    url = resolve_tiktok_short(url)
    for plat, pat in PLATFORM_PATTERNS:
        m = pat.search(url)
        if m:
            return plat, m.group(1)
    return None, None


def scan_logs():
    """Scanne #logs newest-first, retourne :
       - pipeline_runs_by_cid : cid → {url, embeds: [(ts, embed_data)]}
       - dossier_by_cid : cid → embed
    """
    print(f'[scan #logs] cap {LOGS_SCAN_CAP} msgs…', flush=True)
    pipeline_runs = []
    current_run = None
    dossier_embeds = {}
    before = None
    total = 0
    while True:
        path = f'/channels/{LOGS_CHANNEL_ID}/messages?limit=100'
        if before:
            path += f'&before={before}'
        msgs = api('GET', path)
        if not msgs:
            break
        for m in msgs:
            total += 1
            for em in m.get('embeds', []):
                title = em.get('title', '') or ''
                if title.startswith('⚙️ Pipeline | '):
                    step_title = title.replace('⚙️ Pipeline | ', '')
                    if step_title == 'Démarrage':
                        desc = em.get('description', '') or ''
                        m_url = re.search(r'`(https?://[^\s`]+)`', desc)
                        if m_url:
                            url = m_url.group(1)
                            _, cid = extract_id(url)
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
                elif title.startswith('✅ Dossier indexé'):
                    desc = em.get('description', '') or ''
                    m_id = DOSSIER_ID_RE.search(desc)
                    if m_id:
                        cid = m_id.group(1)
                        if cid not in dossier_embeds:  # garde le plus récent
                            dossier_embeds[cid] = em
        before = msgs[-1]['id']
        if len(msgs) < 100:
            break
        if total >= LOGS_SCAN_CAP:
            print(f'  ⚠ stop scan à {total} msgs (cap atteint)', flush=True)
            break
    by_cid = {}
    for run in pipeline_runs:
        cid = run['content_id']
        if not cid:
            continue
        if cid not in by_cid or run['embeds'][0][0] > by_cid[cid]['embeds'][0][0]:
            by_cid[cid] = run
    print(f'  {total} msgs scannés : {len(by_cid)} runs Pipeline · {len(dossier_embeds)} Dossier indexé', flush=True)
    return by_cid, dossier_embeds


def scan_liens():
    """Scanne EXHAUSTIVEMENT #liens, retourne tous les messages utilisateur
    avec URL, plus une liste séparée des system messages thread_created."""
    print(f'[scan #liens] exhaustif…', flush=True)
    user_msgs = []
    system_thread_created = []
    bot_msgs_with_url = []
    before = None
    total = 0
    while True:
        path = f'/channels/{LIENS_CHANNEL_ID}/messages?limit=100'
        if before:
            path += f'&before={before}'
        msgs = api('GET', path)
        if not msgs:
            break
        for m in msgs:
            total += 1
            mtype = m.get('type', 0)
            # type 18 = THREAD_CREATED (system message)
            if mtype == 18:
                system_thread_created.append(m)
                continue
            urls = URL_RE.findall(m.get('content', ''))
            url_infos = []
            for u in urls:
                plat, cid = extract_id(u)
                if cid:
                    url_infos.append({'url': u, 'platform': plat, 'content_id': cid})
            if not url_infos:
                continue
            entry = {
                'message_id': m['id'],
                'channel_id': LIENS_CHANNEL_ID,
                'urls': url_infos,
                'thread': m.get('thread'),
                'author_id': m.get('author', {}).get('id'),
                'author_bot': m.get('author', {}).get('bot', False),
                'reactions': [r.get('emoji', {}).get('name', '') for r in (m.get('reactions') or [])],
                'timestamp': m.get('timestamp', ''),
            }
            if entry['author_bot']:
                bot_msgs_with_url.append(entry)
            else:
                user_msgs.append(entry)
        before = msgs[-1]['id']
        if len(msgs) < 100:
            break
    print(f'  {total} msgs scannés : {len(user_msgs)} user-URL · '
          f'{len(system_thread_created)} system thread_created · '
          f'{len(bot_msgs_with_url)} bot-URL', flush=True)
    return user_msgs, system_thread_created, bot_msgs_with_url


def fetch_thread_state(thread_id: str):
    """Récupère titres d'embeds + archived state + ordre des messages.
    Retourne None si le fil est inaccessible (404 supprimé). `messages`
    est dans l'ordre Discord (newest-first) avec embeds raw pour pouvoir
    re-poster en cas de re-order."""
    try:
        thread_obj = api('GET', f'/channels/{thread_id}')
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    archived = (thread_obj.get('thread_metadata') or {}).get('archived', False)
    msgs = []
    try:
        # Limit 100 OK pour la grande majorité des fils.
        msgs = api('GET', f'/channels/{thread_id}/messages?limit=100') or []
    except urllib.error.HTTPError:
        msgs = []
    embed_titles = []
    dossier_msg = None
    dossier_msg_index_from_latest = None  # 0 = latest, 1 = avant-dernier, ...
    for idx, m in enumerate(msgs):
        for em in m.get('embeds', []):
            t = em.get('title') or ''
            if t:
                embed_titles.append(t)
                if t.startswith('✅ Dossier indexé') and dossier_msg is None:
                    dossier_msg = m
                    dossier_msg_index_from_latest = idx
    return {
        'archived': archived,
        'embed_titles': embed_titles,
        'msg_count': len(msgs),
        'messages': msgs,
        'dossier_msg': dossier_msg,
        'dossier_msg_index_from_latest': dossier_msg_index_from_latest,
    }


def fetch_thread_members(thread_id: str) -> set[str]:
    """Liste les user_ids membres d'un fil. Retourne set vide en cas d'erreur."""
    try:
        members = api('GET', f'/channels/{thread_id}/thread-members') or []
        return {str(m.get('user_id', '')) for m in members if m.get('user_id')}
    except urllib.error.HTTPError:
        return set()


def load_pmap() -> dict:
    if not PMAP_PATH.exists():
        return {}
    try:
        return json.loads(PMAP_PATH.read_text(encoding='utf-8'))
    except Exception:
        return {}


def build_dossier_from_pmap(plat: str, cid: str, pmap: dict) -> dict | None:
    """Lookup pmap entry par cid. Essaie plusieurs clés car le publisher
    écrit `::cid` quand `parse_analysis` ne récupère pas la plateforme
    depuis le résumé (cas SRC_ : X, YouTube, Reddit, Threads — leur
    summary file n'a pas de header `PLATEFORME — …`)."""
    keys_to_try = [
        f"{(plat or '').lower()}::{cid}",
        f"::{cid}",  # bug platform-vide pour X / YouTube / Reddit / Threads
        f"unknown::{cid}",  # variante observée (rares cas)
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
        'color': 0x57F287,
        'fields': [
            {'name': 'Titre', 'value': str(title)[:80] or 'N/A', 'inline': True},
            {'name': 'Source', 'value': '_(reconstruit Y.15)_', 'inline': True},
        ],
    }


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
        print(f'    ✗ post embed fail : {e}', flush=True)
        return False


def audit_one(lm: dict, runs_by_cid: dict, dossier_by_cid: dict, pmap: dict) -> dict:
    """Retourne un dict {message_id, content_id, platform, problems: [str], fixable: bool}."""
    primary = lm['urls'][0]
    cid = primary['content_id']
    plat = primary['platform']
    problems = []
    state = None
    thread_id = None

    # A. fil manquant
    thread = lm.get('thread')
    if not thread:
        problems.append('A_no_fil')
    else:
        thread_id = thread['id']
        state = fetch_thread_state(thread_id)
        if state is None:
            problems.append('Z_thread_404')  # cas rare : fil supprimé
        else:
            titles = state['embed_titles']

            # B. Dossier indexé manquant
            has_dossier = any('Dossier indexé' in t for t in titles)
            if not has_dossier:
                problems.append('B_no_dossier')
            else:
                # M. Dossier indexé existe mais n'est pas le dernier message
                # (idx 0 = newest dans state['messages']). Si idx > 0, il y a
                # des messages plus récents → ré-ordonner.
                if state.get('dossier_msg_index_from_latest', 0) > 0:
                    problems.append('M_dossier_not_last')

            # C. pas de Démarrage
            if not any(t.startswith('⚙️ Pipeline | Démarrage') for t in titles):
                problems.append('C_no_demarrage')

            # D. pas de Pipeline terminé
            if not any('⚙️ Pipeline | Pipeline terminé' in t or
                       '⚙️ Pipeline | Pipeline terminé (avec erreurs)' in t
                       for t in titles):
                problems.append('D_no_terminé')

            # H. fil vide
            if not titles and state['msg_count'] == 0:
                problems.append('H_empty')

            # F. pas archivé alors que pipeline est complet
            if not state['archived']:
                has_terminé = any('Pipeline terminé' in t for t in titles)
                if has_terminé:
                    problems.append('F_not_archived')

            # E. auteur encore membre
            if lm.get('author_id'):
                members = fetch_thread_members(thread_id)
                if str(lm['author_id']) in members:
                    problems.append('E_author_member')

    # I. réaction 🔄 sans ✅/❌
    rxs = lm.get('reactions', [])
    if '🔄' in rxs and '✅' not in rxs and '❌' not in rxs:
        problems.append('I_stuck_loading')

    # J. réaction ❌ posée par le bot → flip en ✅. Le user veut la réaction
    # finale en succès car les fixes audit ont rétabli le pipeline complet.
    if '❌' in rxs:
        problems.append('J_x_reaction')

    return {
        'message_id': lm['message_id'],
        'content_id': cid,
        'platform': plat,
        'thread_id': thread_id,
        'problems': problems,
        'has_dossier_logs': cid in dossier_by_cid,
        'has_dossier_pmap': bool(build_dossier_from_pmap(plat, cid, pmap)),
        'has_pipeline_logs': cid in runs_by_cid,
    }


def fix_one(lm: dict, audit: dict, runs_by_cid: dict, dossier_by_cid: dict,
            pmap: dict, dry_run: bool) -> list[str]:
    """Applique les fixes pour les problèmes détectés. Retourne la liste
    des actions (tentées ou faites)."""
    actions = []
    primary = lm['urls'][0]
    cid = primary['content_id']
    plat = primary['platform']
    msg_id = lm['message_id']
    thread_id = audit.get('thread_id')

    problems = set(audit['problems'])

    # A/H : créer fil + post pipeline embeds
    if 'A_no_fil' in problems or 'H_empty' in problems:
        run = runs_by_cid.get(cid)
        if not run:
            actions.append('skip A/H : aucun run pipeline trouvé en logs')
        else:
            if 'A_no_fil' in problems:
                if dry_run:
                    actions.append(f'CREATE fil (cid={cid})')
                else:
                    try:
                        thread_name = f"📱 {plat} · {cid[:60]}"[:100]
                        t = api('POST',
                                f'/channels/{LIENS_CHANNEL_ID}/messages/{msg_id}/threads',
                                {'name': thread_name, 'auto_archive_duration': 60})
                        thread_id = t['id']
                        actions.append(f'created fil {thread_id}')
                    except urllib.error.HTTPError as e:
                        actions.append(f'create_thread fail : {e}')
                        return actions
            if thread_id:
                if dry_run:
                    actions.append(f'POST {len(run["embeds"])} pipeline embeds')
                else:
                    for ts, em in run['embeds']:
                        post_embed(thread_id, em)
                        time.sleep(0.4)
                    actions.append(f'posted {len(run["embeds"])} pipeline embeds')

    # B : Dossier indexé manquant
    if 'B_no_dossier' in problems and thread_id:
        em = dossier_by_cid.get(cid)
        source = 'logs'
        if not em:
            em = build_dossier_from_pmap(plat, cid, pmap)
            source = 'pmap'
        if em:
            if dry_run:
                actions.append(f'POST Dossier indexé ({source})')
            else:
                if post_embed(thread_id, em):
                    actions.append(f'posted Dossier indexé ({source})')
                time.sleep(0.4)
        else:
            actions.append('skip B : ni #logs ni pmap n\'a l\'embed')

    # C/D : pipeline embeds manquants (et pas couvert par A/H)
    if ('A_no_fil' not in problems and 'H_empty' not in problems
            and ('C_no_demarrage' in problems or 'D_no_terminé' in problems)
            and thread_id):
        run = runs_by_cid.get(cid)
        if run:
            # Re-fetch les titres existants pour ne poster que les manquants
            state = fetch_thread_state(thread_id)
            existing = set(state['embed_titles']) if state else set()
            to_post = [(ts, em) for ts, em in run['embeds']
                       if (em.get('title') or '') not in existing]
            if to_post:
                if dry_run:
                    actions.append(f'POST {len(to_post)} pipeline embeds manquants')
                else:
                    for ts, em in to_post:
                        post_embed(thread_id, em)
                        time.sleep(0.4)
                    actions.append(f'posted {len(to_post)} pipeline embeds manquants')
        else:
            actions.append('skip C/D : aucun run pipeline trouvé en logs')

    # E : auteur encore membre
    if 'E_author_member' in problems and thread_id and lm.get('author_id'):
        if dry_run:
            actions.append(f'DELETE author {lm["author_id"]} from fil')
        else:
            try:
                api('DELETE', f'/channels/{thread_id}/thread-members/{lm["author_id"]}')
                actions.append('removed author from fil')
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    actions.append(f'remove_author fail : {e.code}')

    # F : archiver
    if 'F_not_archived' in problems and thread_id:
        if dry_run:
            actions.append('PATCH archived=true')
        else:
            try:
                api('PATCH', f'/channels/{thread_id}', {'archived': True})
                actions.append('archived')
            except urllib.error.HTTPError as e:
                actions.append(f'archive fail : {e.code}')

    # J : flip réaction ❌ en ✅ sur le message #liens. Demande user :
    # le pipeline complet (post-fixes audit) est success, donc ✅.
    if 'J_x_reaction' in problems:
        if dry_run:
            actions.append('FLIP ❌ → ✅')
        else:
            cross = '%E2%9D%8C'  # ❌
            check = '%E2%9C%85'  # ✅
            ch = lm['channel_id']
            ok_remove = ok_add = False
            try:
                api('DELETE', f'/channels/{ch}/messages/{msg_id}/reactions/{cross}/@me')
                ok_remove = True
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    ok_remove = True  # déjà absent
                else:
                    actions.append(f'remove ❌ fail : {e.code}')
            try:
                api('PUT', f'/channels/{ch}/messages/{msg_id}/reactions/{check}/@me')
                ok_add = True
            except urllib.error.HTTPError as e:
                actions.append(f'add ✅ fail : {e.code}')
            if ok_remove and ok_add:
                actions.append('flipped ❌→✅')
            time.sleep(0.4)

    # M : Dossier indexé n'est pas le dernier message → delete + re-post
    # à la fin pour que ce soit la dernière chose visible dans le fil.
    # On re-fetch l'état car d'autres fixes (D, F) peuvent avoir posté
    # des nouveaux embeds entre-temps. Discord refuse DELETE sur un fil
    # archivé (400) → on un-archive d'abord, puis on re-archive.
    fil_was_archived_before_m = False
    if 'M_dossier_not_last' in problems and thread_id:
        latest_state = fetch_thread_state(thread_id)
        d_msg = latest_state.get('dossier_msg') if latest_state else None
        fil_was_archived_before_m = bool(latest_state and latest_state.get('archived'))
        if d_msg and d_msg.get('embeds'):
            d_embed = next((em for em in d_msg['embeds']
                             if 'Dossier indexé' in (em.get('title') or '')),
                           None)
            if d_embed:
                if dry_run:
                    actions.append('DELETE+RE-POST Dossier indexé en fin de fil')
                else:
                    # Étape 1 : un-archive si besoin (sinon DELETE = 400)
                    if fil_was_archived_before_m:
                        try:
                            api('PATCH', f'/channels/{thread_id}', {'archived': False})
                            time.sleep(0.4)
                        except urllib.error.HTTPError as e:
                            actions.append(f'un-archive fail : {e.code}')
                    # Étape 2 : DELETE
                    deleted = False
                    try:
                        api('DELETE', f'/channels/{thread_id}/messages/{d_msg["id"]}')
                        deleted = True
                    except urllib.error.HTTPError as e:
                        if e.code == 404:
                            deleted = True
                        else:
                            actions.append(f'delete dossier fail : {e.code}')
                    # Étape 3 : POST embed à la fin
                    if deleted:
                        if post_embed(thread_id, d_embed):
                            actions.append('re-posted Dossier indexé en fin')
                        time.sleep(0.4)

    # Re-archivage si M a un-archive un fil qui était archivé avant.
    if fil_was_archived_before_m and thread_id and not dry_run:
        try:
            api('PATCH', f'/channels/{thread_id}', {'archived': True})
            actions.append('re-archived after M')
        except urllib.error.HTTPError as e:
            actions.append(f're-archive after M fail : {e.code}')

    return actions


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='exécute les fixes (sinon dry-run)')
    ap.add_argument('--report', action='store_true', help='dump JSON détaillé sur stdout')
    args = ap.parse_args()

    runs_by_cid, dossier_by_cid = scan_logs()
    pmap = load_pmap()
    print(f'[pmap Y.15] {len(pmap)} entrées chargées')
    user_msgs, system_thread_created, bot_msgs = scan_liens()

    # G : system messages "X a commencé un fil" → suppression
    if system_thread_created:
        print(f'\n[G] {len(system_thread_created)} system messages thread_created à supprimer')
        for sm in system_thread_created:
            if args.apply:
                try:
                    api('DELETE', f'/channels/{LIENS_CHANNEL_ID}/messages/{sm["id"]}')
                    print(f'  msg={sm["id"]} : DELETED')
                except urllib.error.HTTPError as e:
                    print(f'  msg={sm["id"]} : delete fail {e.code}')
                time.sleep(0.4)
            else:
                print(f'  msg={sm["id"]} : would DELETE')

    # K : bot URL messages → log only
    if bot_msgs:
        print(f'\n[K] {len(bot_msgs)} messages URL postés par un bot (log only):')
        for bm in bot_msgs[:10]:
            print(f'  msg={bm["message_id"]} author_bot urls={[u["url"] for u in bm["urls"]]}')

    # Audit chaque user message
    print(f'\n[audit user msgs] {len(user_msgs)} à auditer…')
    audits = []
    counter = Counter()
    for lm in user_msgs:
        a = audit_one(lm, runs_by_cid, dossier_by_cid, pmap)
        audits.append(a)
        for p in a['problems']:
            counter[p] += 1

    print('\n=== Récap par catégorie ===')
    cat_labels = {
        'A_no_fil': 'A. Pas de fil sur le msg',
        'B_no_dossier': 'B. Dossier indexé manquant',
        'C_no_demarrage': 'C. Démarrage pipeline manquant',
        'D_no_terminé': 'D. Pipeline terminé manquant',
        'E_author_member': 'E. Auteur encore membre du fil',
        'F_not_archived': 'F. Fil non archivé (pipeline complet)',
        'H_empty': 'H. Fil vide',
        'I_stuck_loading': 'I. 🔄 sans ✅/❌ (pipeline jamais terminé)',
        'J_x_reaction': 'J. Réaction ❌ → flip en ✅',
        'M_dossier_not_last': 'M. Dossier indexé n\'est pas le dernier embed',
        'Z_thread_404': 'Z. Fil supprimé (référence orpheline)',
    }
    for code, label in cat_labels.items():
        n = counter[code]
        if n:
            print(f'  [{code}] {n:3d}  {label}')

    fixable_codes = {'A_no_fil', 'B_no_dossier', 'C_no_demarrage', 'D_no_terminé',
                     'E_author_member', 'F_not_archived', 'H_empty',
                     'J_x_reaction', 'M_dossier_not_last'}
    n_fixable = sum(1 for a in audits if any(p in fixable_codes for p in a['problems']))
    print(f'\n[fixable] {n_fixable} messages avec ≥1 problème fixable')

    # Actions
    print(f'\n[fixes] mode={"APPLY" if args.apply else "DRY-RUN"}')
    for a in audits:
        if not any(p in fixable_codes for p in a['problems']):
            continue
        lm = next(x for x in user_msgs if x['message_id'] == a['message_id'])
        actions = fix_one(lm, a, runs_by_cid, dossier_by_cid, pmap, dry_run=not args.apply)
        if actions:
            problems_str = ','.join(a['problems'])
            print(f'  msg={a["message_id"]} cid={a["content_id"]} '
                  f'[{problems_str}] → {" | ".join(actions)}')

    if args.report:
        print('\n=== JSON REPORT ===')
        print(json.dumps({
            'system_thread_created': len(system_thread_created),
            'bot_url_messages': len(bot_msgs),
            'user_messages': len(user_msgs),
            'category_counts': dict(counter),
            'audits': audits,
        }, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
