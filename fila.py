#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A fila de publicação, vista e mexida à mão (pelo agente ou pelo Rafael).

Uso:
    python fila.py status                 o que saiu e o que vem
    python fila.py pausar 2026-10-23      pausa o post do dia (ou passe o id)
    python fila.py retomar 2026-10-23     volta a agendar um post pausado
    python fila.py remover <id>           tira da fila um post que ainda não saiu
    ... --push                            grava no GitHub em seguida (commit e push)

Também dá para pausar pelo site do GitHub: edite fila.json e troque o "status" do post
de "agendado" para "pausado".
"""

import argparse
import datetime as dt
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from publicar import AQUI, BRT, gravar_fila, ler_fila, quando  # noqa: E402

GH = ["-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential"]


def achar(fila, alvo):
    """Pelo id exato ou pela data (AAAA-MM-DD) do publicar_em."""
    achados = [p for p in fila["posts"] if p["id"] == alvo or p["publicar_em"].startswith(alvo)]
    if not achados:
        raise SystemExit("nenhum post com id ou data %s" % alvo)
    return achados


def git(*args):
    return subprocess.run(["git"] + GH + list(args), cwd=AQUI, check=True, capture_output=True, text=True)


def puxar():
    git("pull", "-q", "--rebase", "origin", "main")


def empurrar(msg):
    git("add", "fila.json")
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=AQUI).returncode == 0:
        print("nada mudou na fila")
        return
    git("commit", "-q", "-m", msg)
    git("push", "-q", "origin", "HEAD:main")
    print("gravado no GitHub")


def status(fila):
    agora = dt.datetime.now(BRT)
    for p in sorted(fila["posts"], key=quando):
        marca = "" if p["status"] != "agendado" or quando(p) > agora else "  (venceu, espera a rodada)"
        print("%s  %-10s  %s%s" % (quando(p).strftime("%d/%m %H:%M"), p["status"], p["id"], marca))
        if p.get("permalink"):
            print("                         %s" % p["permalink"])
        if p.get("erro"):
            print("                         %s" % p["erro"])


def main():
    ap = argparse.ArgumentParser(description="A fila de publicação do Instagram.")
    ap.add_argument("acao", choices=["status", "pausar", "retomar", "remover"])
    ap.add_argument("alvo", nargs="?", help="id do post ou data AAAA-MM-DD")
    ap.add_argument("--push", action="store_true", help="faz pull antes e commit e push depois")
    a = ap.parse_args()
    if a.push:
        puxar()
    fila = ler_fila()
    if a.acao == "status":
        status(fila)
        return
    if not a.alvo:
        raise SystemExit("diga o id ou a data do post")
    mudou = []
    for p in achar(fila, a.alvo):
        if a.acao == "pausar" and p["status"] == "agendado":
            p["status"] = "pausado"
            mudou.append(p["id"])
        elif a.acao == "retomar" and p["status"] in ("pausado", "atrasado", "erro"):
            if quando(p) <= dt.datetime.now(BRT):
                raise SystemExit("%s era para %s, que já passou: mude o publicar_em antes de retomar" % (p["id"], p["publicar_em"]))
            p.update(status="agendado", tentativas=0, erro=None)
            mudou.append(p["id"])
        elif a.acao == "remover" and p["status"] not in ("publicado", "publicando"):
            fila["posts"].remove(p)
            mudou.append(p["id"])
        else:
            print("%s está %s; não dá para %s" % (p["id"], p["status"], a.acao))
    if not mudou:
        return
    gravar_fila(fila)
    print("%s: %s" % (a.acao, ", ".join(mudou)))
    if a.push:
        empurrar("fila: %s %s" % (a.acao, ", ".join(mudou)))


if __name__ == "__main__":
    main()
