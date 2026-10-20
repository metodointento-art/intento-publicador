#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Publica no Instagram os carrosséis da fila (fila.json), na hora marcada.

Roda no GitHub Actions de hora em hora (.github/workflows/publicar.yml). Em cada rodada:
  - o post "agendado" que vence nos próximos 30 minutos espera a hora e sai;
  - o que passou mais de 3 horas do horário vira "atrasado" e não sai sozinho;
  - "pausado" nunca sai. Antes de publicar, a rodada relê a fila no GitHub, e uma
    pausa feita durante a espera vale.

Contra post em dobro: antes de publicar, confere os posts recentes da conta. Se já existe
um com a mesma legenda nas últimas 12 horas, só registra o link.

Uso:
    python publicar.py              publica o que vence agora (no Actions)
    python publicar.py --checar     confere token, conta, limite, URLs e legendas, sem publicar
    python publicar.py --simular    roda a fila contra uma API falsa, sem rede e sem git
                                    (com --agora "2026-10-23T18:40:00-03:00" para mudar o relógio)

Variáveis: IG_ACCESS_TOKEN e IG_USER_ID (secrets do repositório), IG_API_VERSION (padrão v23.0).
API: Instagram com login do Instagram, graph.instagram.com
(https://developers.facebook.com/docs/instagram-platform/content-publishing/).
"""

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
FILA = os.path.join(AQUI, "fila.json")
VERSAO = os.environ.get("IG_API_VERSION", "v23.0")
BASE = "https://graph.instagram.com/" + VERSAO
BRT = dt.timezone(dt.timedelta(hours=-3))
ANTES = dt.timedelta(hours=4)
DEPOIS = dt.timedelta(hours=3)
MAX_TENTATIVAS = 3
MAX_LEGENDA, MAX_HASHTAGS, MAX_ALT, MAX_BYTES = 2200, 30, 1000, 8 * 1024 * 1024
NO_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"


class ErroAPI(Exception):
    def __init__(self, msg, transitorio=False):
        super().__init__(msg)
        self.transitorio = transitorio


# ------------------------------------------------------------------ a API de verdade
class API:
    def __init__(self, token, ig_id):
        self.token, self.ig = token, ig_id

    def _req(self, metodo, caminho, params=None, tentativas=3):
        params = dict(params or {}, access_token=self.token)
        url = BASE + caminho
        dados = None
        if metodo == "GET":
            url += "?" + urllib.parse.urlencode(params)
        else:
            dados = urllib.parse.urlencode(params).encode()
        for n in range(tentativas):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, data=dados, method=metodo), timeout=60) as r:
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                corpo = e.read().decode(errors="replace")
                try:
                    erro = json.loads(corpo).get("error", {})
                except ValueError:
                    erro = {"message": corpo[:300]}
                transitorio = e.code >= 500 or erro.get("is_transient") or erro.get("code") in (1, 2, 4, 17, 341)
                if transitorio and n < tentativas - 1:
                    time.sleep(5 * (n + 1))
                    continue
                raise ErroAPI("%s %s: %s (code %s, subcode %s)" % (
                    metodo, caminho, erro.get("message", "erro"), erro.get("code"), erro.get("error_subcode")), bool(transitorio))
            except urllib.error.URLError as e:
                if n < tentativas - 1:
                    time.sleep(5 * (n + 1))
                    continue
                raise ErroAPI("%s %s: %s" % (metodo, caminho, e.reason), True)

    def conta(self):
        return self._req("GET", "/me", {"fields": "user_id,username"})

    def limite(self):
        return self._req("GET", "/%s/content_publishing_limit" % self.ig, {"fields": "quota_usage,config"})

    def recentes(self):
        return self._req("GET", "/%s/media" % self.ig, {"fields": "id,caption,timestamp,permalink", "limit": 12}).get("data", [])

    def item(self, url, alt):
        p = {"image_url": url, "is_carousel_item": "true"}
        if alt:
            p["alt_text"] = alt
        return self._req("POST", "/%s/media" % self.ig, p)["id"]

    def carrossel(self, filhos, legenda):
        return self._req("POST", "/%s/media" % self.ig,
                         {"media_type": "CAROUSEL", "children": ",".join(filhos), "caption": legenda})["id"]

    def status(self, cid):
        return self._req("GET", "/%s" % cid, {"fields": "status_code,status"})

    def publicar(self, cid):
        return self._req("POST", "/%s/media_publish" % self.ig, {"creation_id": cid})["id"]

    def link(self, mid):
        return self._req("GET", "/%s" % mid, {"fields": "permalink,timestamp"})


# ------------------------------------------------------------------ a API falsa, para testar a fila sem rede
class APISimulada:
    def __init__(self):
        self.posts, self.n, self.containers = [], 0, {}

    def _id(self):
        self.n += 1
        return "sim%d" % self.n

    def conta(self):
        return {"user_id": "sim", "username": "metodointento (simulado)"}

    def limite(self):
        return {"data": [{"quota_usage": len(self.posts), "config": {"quota_total": 100}}]}

    def recentes(self):
        return list(reversed(self.posts))[:12]

    def item(self, url, alt):
        i = self._id()
        self.containers[i] = "FINISHED"
        return i

    def carrossel(self, filhos, legenda):
        i = self._id()
        self.containers[i] = "FINISHED"
        self.legendas = getattr(self, "legendas", {})
        self.legendas[i] = legenda
        return i

    def status(self, cid):
        return {"status_code": self.containers.get(cid, "EXPIRED")}

    def publicar(self, cid):
        if self.containers.get(cid) == "PUBLISHED":
            raise ErroAPI("container já publicado")
        self.containers[cid] = "PUBLISHED"
        mid = self._id()
        self.posts.append({"id": mid, "caption": self.legendas[cid],
                           "timestamp": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000"),
                           "permalink": "https://www.instagram.com/p/%s/" % mid})
        return mid

    def link(self, mid):
        return {"permalink": "https://www.instagram.com/p/%s/" % mid}


# ------------------------------------------------------------------ a fila
def ler_fila(caminho=FILA):
    with open(caminho, encoding="utf-8") as f:
        return json.load(f)


def gravar_fila(fila, caminho=FILA):
    with open(caminho, "w", encoding="utf-8", newline="\n") as f:
        json.dump(fila, f, ensure_ascii=False, indent=2)
        f.write("\n")


def quando(post):
    return dt.datetime.fromisoformat(post["publicar_em"])


def agora_brt():
    return dt.datetime.now(BRT).replace(microsecond=0)


def validar(post):
    """Os limites da Meta e da casa, sem rede."""
    problemas = []
    imgs = post.get("imagens", [])
    if not 2 <= len(imgs) <= 10:
        problemas.append("%d imagens (o carrossel aceita de 2 a 10)" % len(imgs))
    leg = post.get("legenda", "")
    if not leg.strip():
        problemas.append("legenda vazia")
    if len(leg) > MAX_LEGENDA:
        problemas.append("legenda com %d caracteres (até %d)" % (len(leg), MAX_LEGENDA))
    if leg.count("#") > MAX_HASHTAGS:
        problemas.append("mais de %d hashtags" % MAX_HASHTAGS)
    for i, im in enumerate(imgs, 1):
        if not im.get("url", "").startswith("https://"):
            problemas.append("imagem %d sem URL https" % i)
        if not im.get("url", "").lower().endswith((".jpg", ".jpeg")):
            problemas.append("imagem %d não é JPEG (a API só aceita JPEG)" % i)
        if len(im.get("alt_text", "")) > MAX_ALT:
            problemas.append("alt text da imagem %d com mais de %d caracteres" % (i, MAX_ALT))
    try:
        if quando(post).tzinfo is None:
            problemas.append("publicar_em sem fuso horário")
    except (KeyError, ValueError):
        problemas.append("publicar_em inválido")
    return problemas


def conferir_url(url):
    """200, image/jpeg e até 8 MB, do jeito que a Meta vai baixar."""
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "intento-publicador"})
        with urllib.request.urlopen(req, timeout=30) as r:
            tipo = r.headers.get("Content-Type", "")
            tamanho = int(r.headers.get("Content-Length") or 0)
    except urllib.error.HTTPError as e:
        return "HTTP %d" % e.code
    except urllib.error.URLError as e:
        return str(e.reason)
    if not tipo.startswith("image/jpeg"):
        return "Content-Type %s" % tipo
    if tamanho > MAX_BYTES:
        return "%.1f MB (até 8 MB)" % (tamanho / 1048576)
    return ""


# ------------------------------------------------------------------ git (só no Actions)
def git(*args, checar=True):
    return subprocess.run(["git"] + list(args), cwd=AQUI, check=checar, capture_output=True, text=True)


def fila_remota():
    """A fila como está no GitHub agora, para valer a pausa feita durante a espera."""
    if not NO_ACTIONS:
        return None
    git("fetch", "-q", "origin", "main")
    return json.loads(git("show", "origin/main:fila.json").stdout)


def gravar_no_github(mudancas, mensagem):
    """Reaplica os campos alterados sobre a fila mais nova do GitHub, para não brigar com um push do agente."""
    if not NO_ACTIONS or not mudancas:
        return
    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    for tentativa in range(4):
        git("fetch", "-q", "origin", "main")
        git("reset", "-q", "--hard", "origin/main")
        fila = ler_fila()
        por_id = {p["id"]: p for p in fila["posts"]}
        for pid, campos in mudancas.items():
            if pid in por_id:
                por_id[pid].update(campos)
        gravar_fila(fila)
        git("add", "fila.json")
        if git("diff", "--cached", "--quiet", checar=False).returncode == 0:
            return
        git("commit", "-q", "-m", mensagem)
        if git("push", "-q", "origin", "HEAD:main", checar=False).returncode == 0:
            return
        time.sleep(3 + 3 * tentativa)
    raise SystemExit("não consegui gravar a fila no GitHub depois de 4 tentativas")


# ------------------------------------------------------------------ publicar um post
def esperar_container(api, cid, limite_s=180):
    inicio = time.time()
    while True:
        st = api.status(cid).get("status_code")
        if st in ("FINISHED", "PUBLISHED"):
            return st
        if st in ("ERROR", "EXPIRED"):
            raise ErroAPI("contêiner %s ficou %s" % (cid, st))
        if time.time() - inicio > limite_s:
            raise ErroAPI("contêiner %s não ficou pronto em %d s" % (cid, limite_s), True)
        time.sleep(4)


def ja_publicado(api, post):
    """Um post com a mesma legenda nas últimas 12 horas: já saiu, por esta fila ou à mão."""
    inicio = post["legenda"].strip()[:80]
    limite = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=12)
    for m in api.recentes():
        try:
            t = dt.datetime.strptime(m.get("timestamp", ""), "%Y-%m-%dT%H:%M:%S%z")
        except ValueError:
            continue
        if t >= limite and (m.get("caption") or "").strip()[:80] == inicio:
            return m
    return None


def publicar_post(api, post, mudancas, log):
    existente = ja_publicado(api, post)
    if existente:
        mudancas[post["id"]] = {"status": "publicado", "media_id": existente["id"], "permalink": existente.get("permalink"),
                                "publicado_em": agora_brt().isoformat(), "erro": "já estava no perfil; só registrei o link"}
        log("  já estava publicado: %s" % existente.get("permalink"))
        return
    cid = post.get("container_id")
    if cid and post.get("status") == "publicando":
        try:
            st = esperar_container(api, cid, 30)
        except ErroAPI:
            st, cid = None, None
        if st == "PUBLISHED":
            raise ErroAPI("o contêiner %s já foi publicado, mas o post não aparece nos recentes; confira o perfil" % cid)
    if not cid or post.get("status") != "publicando":
        filhos = []
        usar_alt = True
        for im in post["imagens"]:
            try:
                filhos.append(api.item(im["url"], im.get("alt_text") if usar_alt else None))
            except ErroAPI as e:
                if usar_alt and "alt_text" in str(e):
                    usar_alt = False
                    log("  a API recusou o alt_text; sigo sem ele")
                    filhos.append(api.item(im["url"], None))
                else:
                    raise
        for f in filhos:
            esperar_container(api, f)
        cid = api.carrossel(filhos, post["legenda"])
        esperar_container(api, cid)
        post["container_id"], post["status"] = cid, "publicando"
    mid = api.publicar(cid)
    link = api.link(mid).get("permalink")
    mudancas[post["id"]] = {"status": "publicado", "container_id": cid, "media_id": mid, "permalink": link,
                            "publicado_em": agora_brt().isoformat(), "erro": None}
    log("  publicado: %s" % link)


# ------------------------------------------------------------------ a rodada
def resumo(linhas):
    texto = "\n".join(linhas)
    print(texto)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("```\n" + texto + "\n```\n")


def checar(api, fila):
    linhas, falhas = [], 0
    try:
        c = api.conta()
        linhas.append("conta: @%s (user_id %s)" % (c.get("username"), c.get("user_id")))
        lim = (api.limite().get("data") or [{}])[0]
        linhas.append("limite de publicação: %s de %s nas últimas 24 h" % (
            lim.get("quota_usage"), (lim.get("config") or {}).get("quota_total")))
    except ErroAPI as e:
        linhas.append("ERRO na API: %s" % e)
        falhas += 1
    for p in sorted(fila["posts"], key=quando):
        if p.get("status") not in ("agendado", "pausado"):
            continue
        probs = validar(p)
        for i, im in enumerate(p.get("imagens", []), 1):
            r = conferir_url(im.get("url", "")) if not isinstance(api, APISimulada) else ""
            if r:
                probs.append("imagem %d: %s" % (i, r))
        linhas.append("%s  %-9s  %s  %s" % (p["publicar_em"], p["status"], p["id"], "ok" if not probs else "; ".join(probs)))
        falhas += bool(probs)
    resumo(linhas)
    return falhas


def rodar(api, fila, agora, esperar=True):
    linhas, mudancas, falhou = [], {}, False
    log = linhas.append
    for p in fila["posts"]:
        if p.get("status") == "agendado" and agora - quando(p) > DEPOIS:
            mudancas[p["id"]] = {"status": "atrasado", "erro": "a rodada chegou %s depois do horário" % (agora - quando(p))}
            log("%s atrasado, não publiquei: %s" % (p["id"], p["publicar_em"]))
            falhou = True
    vencem = sorted((p for p in fila["posts"] if p.get("status") in ("agendado", "publicando")
                     and p["id"] not in mudancas and quando(p) - agora <= ANTES), key=quando)
    if not vencem:
        log("nada vence nos próximos %d minutos" % (ANTES.seconds // 60))
    for p in vencem:
        espera = (quando(p) - agora_brt()).total_seconds() if esperar else 0
        if espera > 0:
            log("%s: espero %d min até %s" % (p["id"], espera // 60, p["publicar_em"]))
            time.sleep(espera)
        remota = fila_remota()
        if remota is not None:
            atual = {x["id"]: x for x in remota["posts"]}.get(p["id"])
            if not atual or atual.get("status") not in ("agendado", "publicando"):
                log("%s: mudou para %s durante a espera; não publiquei" % (p["id"], (atual or {}).get("status", "removido")))
                continue
            p.update(atual)
        problemas = validar(p)
        if problemas:
            mudancas[p["id"]] = {"status": "erro", "erro": "; ".join(problemas)}
            log("%s: não publiquei: %s" % (p["id"], "; ".join(problemas)))
            falhou = True
            continue
        log("%s: publicando" % p["id"])
        try:
            publicar_post(api, p, mudancas, log)
        except ErroAPI as e:
            n = int(p.get("tentativas") or 0) + 1
            fim = (not e.transitorio) or n >= MAX_TENTATIVAS
            mudancas[p["id"]] = {"status": "erro" if fim else p.get("status", "agendado"), "tentativas": n, "erro": str(e),
                                 "container_id": p.get("container_id")}
            log("  ERRO (%s): %s" % ("desisti" if fim else "tento de novo na próxima rodada", e))
            falhou = True
        gravar_no_github({p["id"]: mudancas[p["id"]]}, "fila: %s %s" % (p["id"], mudancas[p["id"]]["status"]))
    gravar_no_github({k: v for k, v in mudancas.items() if v.get("status") == "atrasado"}, "fila: posts atrasados")
    return linhas, mudancas, falhou


def main():
    ap = argparse.ArgumentParser(description="Publica os carrosséis da fila no Instagram.")
    ap.add_argument("--checar", action="store_true", help="confere tudo sem publicar")
    ap.add_argument("--simular", action="store_true", help="API falsa, sem rede e sem git")
    ap.add_argument("--agora", help="relógio da simulação, em ISO com fuso")
    ap.add_argument("--fila", default=FILA, help="caminho da fila (para testes)")
    a = ap.parse_args()

    fila = ler_fila(a.fila)
    agora = dt.datetime.fromisoformat(a.agora) if a.agora else agora_brt()
    if not a.checar and not any(p.get("status") in ("agendado", "publicando") and quando(p) - agora <= ANTES
                                for p in fila["posts"]):
        # Sem post para sair nesta rodada, nem o token é preciso: a rodada termina limpa, sem e-mail de falha.
        resumo(["nada vence nos próximos %d minutos" % (ANTES.seconds // 60)])
        return
    if a.simular:
        global NO_ACTIONS
        NO_ACTIONS = False
        api = APISimulada()
    else:
        token, ig = os.environ.get("IG_ACCESS_TOKEN"), os.environ.get("IG_USER_ID")
        if not token or not ig:
            raise SystemExit("faltam os secrets IG_ACCESS_TOKEN e IG_USER_ID")
        api = API(token, ig)
    if a.checar:
        sys.exit(1 if checar(api, fila) else 0)
    linhas, mudancas, falhou = rodar(api, fila, agora, esperar=not a.simular)
    if a.simular:
        for pid, campos in mudancas.items():
            for p in fila["posts"]:
                if p["id"] == pid:
                    p.update(campos)
        gravar_fila(fila, a.fila)
    resumo(linhas)
    sys.exit(1 if falhou else 0)


if __name__ == "__main__":
    main()
