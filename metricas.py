"""Testa se o token do Instagram lê as métricas da conta e dos posts.

    python metricas.py --teste     diz o que funciona e o que falta, sem mostrar nenhum número

O repositório é público, e o log do Actions também. Por isso o teste só escreve "ok" ou o erro de cada
métrica, nunca o valor. Guardar os números de verdade pede outro caminho, que não fique público.

Variáveis: IG_ACCESS_TOKEN e IG_USER_ID (secrets do repositório), IG_API_VERSION (padrão v23.0).
"""
import json, os, sys, urllib.error, urllib.parse, urllib.request

VERSAO = os.environ.get("IG_API_VERSION", "v23.0")
BASE = "https://graph.instagram.com/" + VERSAO
FEED = ["reach", "views", "saved", "shares", "total_interactions", "likes", "comments"]
REELS = FEED + ["ig_reels_avg_watch_time", "ig_reels_video_view_total_time"]
CONTA = ["reach", "views", "accounts_engaged", "total_interactions", "follows_and_unfollows"]


def get(caminho, params):
    url = BASE + caminho + "?" + urllib.parse.urlencode(dict(params, access_token=os.environ["IG_ACCESS_TOKEN"]))
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return json.loads(r.read().decode()), None
    except urllib.error.HTTPError as e:
        try:
            erro = json.loads(e.read().decode(errors="replace")).get("error", {})
        except ValueError:
            erro = {}
        return None, "%s (code %s, subcode %s)" % (erro.get("message", "HTTP %s" % e.code), erro.get("code"), erro.get("error_subcode"))
    except urllib.error.URLError as e:
        return None, str(e.reason)


def testar_metricas(caminho, metricas, extra=None):
    """Pede uma métrica por vez, para separar a que a conta não tem da que o token não pode ler."""
    res = {}
    for m in metricas:
        dados, erro = get(caminho, dict(extra or {}, metric=m))
        res[m] = "ok" if dados is not None else erro
    return res


def main():
    if "--teste" not in sys.argv[1:]:
        sys.exit(__doc__)
    ig = os.environ["IG_USER_ID"]
    conta, erro = get("/me", {"fields": "user_id,username,followers_count,media_count"})
    print("conta:", "@" + conta["username"] if conta else erro)
    if conta:
        print("  seguidores e total de posts:", "lidos" if "followers_count" in conta else "não vieram")

    print("\nmétricas da conta, últimos 28 dias:")
    for m, r in testar_metricas("/%s/insights" % ig, CONTA, {"period": "day", "metric_type": "total_value"}).items():
        print("  %-24s %s" % (m, r))

    posts, erro = get("/%s/media" % ig, {"fields": "id,timestamp,media_type,media_product_type", "limit": 6})
    if erro:
        print("\nlista de posts:", erro)
        return
    funciona = False
    for p in posts.get("data", []):
        tipo = p.get("media_product_type", "FEED")
        res = testar_metricas("/%s/insights" % p["id"], REELS if tipo == "REELS" else FEED)
        funciona = funciona or any(v == "ok" for v in res.values())
        print("\npost de %s (%s, %s):" % (p["timestamp"][:10], p.get("media_type"), tipo))
        for m, r in res.items():
            print("  %-32s %s" % (m, r))
    print("\nRESULTADO:", "o token lê as métricas dos posts" if funciona else "o token NÃO lê as métricas dos posts")


if __name__ == "__main__":
    main()
