# intento-publicador

Publica no Instagram (@metodointento) os carrosséis que o agente de criativo agenda. É a ponta final do fluxo de `intento-branding/criativos/design/`: o Rafael aprova a prévia, o agente roda `agendar-lote.py --aprovado`, e este repositório publica na hora marcada.

```
agendar-lote.py (intento-branding)
  ├─ JPEG em pdf-host-meta-01/public/ig/...  → URL pública no Vercel
  └─ post em fila.json, status "agendado"    → push aqui
publicar.yml (de hora em hora, :37, 9h às 22h de Brasília)
  └─ publicar.py: o que vence em 75 min espera a hora e sai → status, link e data voltam para fila.json
```

## A fila (`fila.json`)

Uma entrada por post:

| Campo | O que é |
|---|---|
| `id` | O `meta.id` do `peca.json`, como `2026-10-23-aluno-A2-F2-lote2` |
| `publicar_em` | ISO com fuso, como `2026-10-23T19:00:00-03:00` |
| `legenda` | O texto exato, até 2.200 caracteres e 30 hashtags |
| `imagens` | De 2 a 10 itens, cada um com `url` (JPEG público) e `alt_text` (até 1.000 caracteres) |
| `status` | `agendado`, `pausado`, `publicando`, `publicado`, `erro` ou `atrasado` |
| `aprovado_em` | Quando o Rafael aprovou, gravado pelo agente |
| `container_id`, `media_id`, `permalink`, `publicado_em`, `erro`, `tentativas` | Preenchidos pela publicação |

## Pausar, retomar e ver o status

- `python fila.py status`
- `python fila.py pausar 2026-10-23 --push`
- `python fila.py retomar 2026-10-23 --push`
- Pelo site: edite `fila.json` no GitHub e troque `"agendado"` por `"pausado"`. Uma pausa feita até a hora de sair vale, porque o publicador relê a fila antes de publicar.

## Segurança na publicação

- **Post em dobro:** antes de publicar, ele confere os posts recentes da conta. Se a mesma legenda saiu nas últimas 12 horas, só registra o link. Se uma rodada cai depois de montar o carrossel, a próxima reaproveita o contêiner.
- **Atraso:** se a rodada chega mais de 3 horas depois do horário, o post vira `atrasado` e não sai sozinho.
- **Avisos:** erro, atraso e post inválido fazem o workflow falhar, e o GitHub manda e-mail para o dono do repositório.

## Configuração (uma vez)

Secrets do repositório, em Settings > Secrets and variables > Actions:

| Secret | De onde vem |
|---|---|
| `IG_ACCESS_TOKEN` | Token de longa duração da API do Instagram com login do Instagram, do app "Intento Publicador" no developers.facebook.com (painel do app > Instagram > Gerar tokens de acesso). **Nunca vai para o chat.** |
| `IG_USER_ID` | O ID da conta @metodointento, mostrado na mesma tela |
| `GH_PAT_SECRETS` | Token do GitHub de escopo mínimo: fine-grained, só este repositório, permissão "Secrets: Read and write". Usado pelo `renovar-token.yml`. Vale por até 1 ano, então anote a data de vencimento |

O app fica em modo de desenvolvimento, com a @metodointento como testadora do Instagram. Assim não precisa de revisão da Meta, porque só publica na própria conta.

## Testar

- Na Actions, rode o workflow **publicar** à mão com `modo = checar`. Ele confere o token, a conta, o limite de publicação e cada URL da fila (200, `image/jpeg`, até 8 MB), sem publicar nada.
- Para testar a lógica sem rede: `python publicar.py --simular --fila <copia>.json --agora 2026-10-23T18:40:00-03:00`.

## Limites da API (Meta, outubro de 2026)

- Só JPEG, em URL pública.
- Carrossel de 2 a 10 itens, todos cortados pelo formato do primeiro (o nosso é 4:5).
- Até 100 posts publicados pela API em 24 horas.
- O contêiner expira em 24 horas.
- A API não tem agendamento: quem agenda é este repositório.
- Referência: <https://developers.facebook.com/docs/instagram-platform/content-publishing/>
