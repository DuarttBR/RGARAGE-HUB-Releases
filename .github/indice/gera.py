"""Índice de releases do catálogo do RGARAGE HUB.

O hub de cada pessoa consultava o GitHub repositório por repositório (mais de mil) e, sem token, estourava o
limite de 60 consultas por hora: o catálogo ficava com versões velhas ou perdia as releases. Este script roda numa
GitHub Action a cada 2 horas, lê as releases de todos os repositórios do catálogo pela API GraphQL (poucas consultas,
50 repositórios por vez) e gera um arquivo só, que o hub baixa pelo CDN do GitHub sem gastar limite de ninguém.

  python gera.py catalogo/repos.json saida/releases.json.gz      (token em GITHUB_TOKEN)

Formato (JSON compactado em gzip):
  {"v": 1, "gerado": "2026-10-24T22:00:00Z", "repos": {"dono/nome": [[tag, nome|null, pre(0/1), publicado(ISO), [[arquivo, bytes], ...]], ...]}}
Repositório que não existe mais (ou foi renomeado) fica de fora: o hub consulta esses do jeito antigo.
"""
import datetime
import gzip
import json
import os
import sys
import time
import urllib.request

API = 'https://api.github.com/graphql'
LOTE = 50
Q_REPO = '''{a}: repository(owner: {o}, name: {n}) {{
  releases(first: 6, orderBy: {{field: CREATED_AT, direction: DESC}}) {{
    nodes {{ tagName name isPrerelease isDraft publishedAt releaseAssets(first: 60) {{ nodes {{ name size }} }} }}
  }}
}}'''


def consulta(token, query):
    body = json.dumps({'query': query}).encode()
    for tent in range(5):
        req = urllib.request.Request(API, data=body, headers={'Authorization': f'bearer {token}', 'Content-Type': 'application/json',
                                                               'User-Agent': 'RGARAGE-HUB-indice'})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            msg = e.read().decode('utf-8', 'ignore')[:200]
            print(f'  HTTP {e.code} (tentativa {tent + 1}): {msg}', flush=True)
            time.sleep(10 * (tent + 1))
        except Exception as e:
            print(f'  erro (tentativa {tent + 1}): {e}', flush=True)
            time.sleep(10 * (tent + 1))
    raise SystemExit('a API do GitHub não respondeu')


def main():
    repos_file, out_file = sys.argv[1], sys.argv[2]
    token = os.environ['GITHUB_TOKEN']
    repos = sorted({r.strip().lower() for r in json.load(open(repos_file, encoding='utf-8')) if r.count('/') == 1})
    print(len(repos), 'repositórios', flush=True)
    idx, faltam, custo = {}, 0, 0
    for i in range(0, len(repos), LOTE):
        lote = repos[i:i + LOTE]
        partes = [Q_REPO.format(a=f'r{k}', o=json.dumps(r.split('/')[0]), n=json.dumps(r.split('/')[1])) for k, r in enumerate(lote)]
        d = consulta(token, '{ rateLimit { cost remaining } ' + '\n'.join(partes) + ' }')
        data = d.get('data') or {}
        custo += (data.get('rateLimit') or {}).get('cost', 0)
        for k, r in enumerate(lote):
            node = data.get(f'r{k}')
            if not node:
                faltam += 1
                continue
            rels = []
            for rel in (node.get('releases') or {}).get('nodes') or []:
                if rel.get('isDraft'):
                    continue
                assets = [[a['name'], a['size']] for a in (rel.get('releaseAssets') or {}).get('nodes') or []]
                nome = rel.get('name') or None
                rels.append([rel['tagName'], nome if nome and nome != rel['tagName'] else None, 1 if rel.get('isPrerelease') else 0, rel.get('publishedAt'), assets])
            idx[r] = rels
        print(f'  {min(i + LOTE, len(repos))}/{len(repos)} (custo {custo}, restam {(data.get("rateLimit") or {}).get("remaining")})', flush=True)
    os.makedirs(os.path.dirname(os.path.abspath(out_file)), exist_ok=True)
    doc = {'v': 1, 'gerado': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'repos': idx}
    raw = json.dumps(doc, ensure_ascii=False, separators=(',', ':')).encode()
    with gzip.open(out_file, 'wb', compresslevel=9) as f:
        f.write(raw)
    print(f'ok: {len(idx)} repositórios, {sum(len(v) for v in idx.values())} releases, {faltam} sem acesso; '
          f'{len(raw) / 1e6:.1f} MB -> {os.path.getsize(out_file) / 1e6:.2f} MB gz', flush=True)


if __name__ == '__main__':
    main()
