<a id="readme-top"></a>

<br />
<div align="center">
  <a href="https://github.com/Kc1t/alethe-orchestrate-skill">
    <img src="./docs/assets/alethe.png" alt="Logo do Alethe" width="120">
  </a>

  <h1 align="center">alethe-orchestrate</h1>

  <p align="center"><b>Orquestre agentes de código, e prove que valeu a pena.</b></p>

  <p align="center">
    <a href="https://github.com/Kc1t/alethe-orchestrate-skill/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/Kc1t/alethe-orchestrate-skill/actions/workflows/ci.yml/badge.svg"></a>
    <a href="./CHANGELOG.md"><img alt="Versão da skill" src="https://img.shields.io/badge/skill-v20-4f46e5?style=flat-square"></a>
    <a href="./LICENSE"><img alt="Licença" src="https://img.shields.io/badge/license-MIT-blue?style=flat-square"></a>
    <img alt="Python 3.9+ opcional" src="https://img.shields.io/badge/python-3.9%2B%20optional-3776ab?style=flat-square&logo=python&logoColor=white">
    <img alt="Windows, macOS e Linux" src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-555?style=flat-square">
    <a href="https://agentskills.io"><img alt="Agent Skill" src="https://img.shields.io/badge/Agent%20Skill-SKILL.md-111?style=flat-square"></a>
  </p>

  <p align="center"><a href="./README.md">English</a> · <b>Português (BR)</b></p>
</div>

## Instalação

```sh
npx skills add Kc1t/alethe-orchestrate-skill
```

<details>
<summary>Plugin do Claude Code, plugin do Codex ou manual</summary>

```text
/plugin marketplace add Kc1t/alethe-orchestrate-skill
/plugin install alethe-orchestrate@alethe
```

```sh
codex plugin marketplace add Kc1t/alethe-orchestrate-skill
codex plugin add alethe-orchestrate@alethe
```

Ou copie `skills/alethe` para `~/.claude/skills/`, `~/.codex/skills/` ou
`~/.agents/skills/`.

</details>

## Primeiros Passos

1. **Instale** com o comando acima.
2. **`/alethe setup`** confere quais CLIs de agente você tem e se estão logadas, lista os modelos e propõe três perfis: base, savings e aggressive. Nada é salvo antes de você aprovar.
3. **`/alethe always`** (opcional) deixa ligado em todo projeto, para você nunca mais digitar `/alethe`.
4. **Peça o trabalho**: `/alethe migra os endpoints de billing para a v2`, ou só peça com suas palavras enquanto estiver ligado. O líder escolhe um playbook, mostra a prévia do plano e roda os workers.
5. **`/alethe status`** enquanto roda, **`/alethe stop`** para parar.
6. **`/alethe report`** quando terminar: o que cada worker fez, por quê e quanto custou. **`/alethe mark <worker> used`** registra o que você aproveitou.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./docs/assets/drawings/how-it-works-dark.svg">
    <img src="./docs/assets/drawings/how-it-works-light.svg" alt="Você pede ao seu agente para orquestrar uma refatoração. O agente líder escolhe um playbook, escreve os briefs e faz o dry-run do plano; o orchestrate.py roda uma wave de workers: Claude Code no haiku, só leitura; Codex com esforço baixo, escrevendo na própria worktree; Antigravity, só leitura. Eles deixam result.md, diff.patch e outcome.json, e as transcrições completas ficam no disco. O líder lê só os resultados, confere, integra e marca cada um, roda a próxima wave e no fim gera o relatório" width="900">
  </picture>
</p>

## Comandos

Digite depois de `/alethe`.

| Comando | O que faz |
| --- | --- |
| `setup` | Confere suas CLIs e configura tudo |
| `<tarefa>` | Orquestra a tarefa |
| `always` | Deixa ligado em todo projeto |
| `freestyle` | Trabalha normal, só cuida do custo e sugere modelos mais baratos |
| `status` | Onde está |
| `stop` | Para os workers |
| `report` | O que cada worker fez e custou |
| `off` | Desliga aqui |

`/alethe help` lista o resto.

## Deixar Ligado

Com `always` ou `on`, cada prompt lembra o líder do seu objetivo e das suas regras, e ele decide a cada pedido se passa o trabalho adiante.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./docs/assets/drawings/stay-on-dark.svg">
    <img src="./docs/assets/drawings/stay-on-light.svg" alt="Uma conversa. A primeira mensagem diz /alethe a partir de agora, cuidado com o banco, e o líder liga o modo cuidadoso e guarda a frase como regra. As mensagens seguintes não precisam de /alethe: um hook lembra o líder do objetivo, do cuidado e das regras. Ele corrige um typo sozinho, orquestra a mudança de 40 endpoints com scouts baratos e writers em worktrees, e responde como está indo com um status curto" width="820">
  </picture>
</p>

## Entrar no Meio da Tarefa

Ela entra no meio do fluxo de outra skill: a spec e o diff até ali viram o plano.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./docs/assets/drawings/mid-task-dark.svg">
    <img src="./docs/assets/drawings/mid-task-light.svg" alt="Um tasks.md de um fluxo spec-driven com as tarefas 1 a 3 prontas. O /alethe entra na tarefa 4. O líder usa a spec e o diff até ali como plano e manda as tarefas 4 e 5 para fora: quatro writers no Codex, uma worktree por grupo, dois workers Claude haiku arrumando os testes, e a parte acoplada fica com o líder. A tarefa 6, a revisão, continua com o fluxo original" width="860">
  </picture>
</p>

## Mudar o Rumo

O playbook é um ponto de partida. `skip`, `add`, `switch` e `freeform` mudam ele entre as waves, e o relatório lista cada mudança com o motivo.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./docs/assets/drawings/change-course-dark.svg">
    <img src="./docs/assets/drawings/change-course-light.svg" alt="Em cima, tracejado: o playbook scout-then-build, com scout, plan, build e review. Embaixo, o que rodou: scout, plan, uma wave livre para corrigir o script de build porque o bug estava lá, build, e review riscado porque o diff tinha três linhas. O journal.jsonl registra cada mudança com o motivo, e o relatório mostra todas em Changes to the plan" width="820">
  </picture>
</p>

## Playbooks

| Playbook | Use quando | O que economiza |
|---|---|---|
| `direct` | A tarefa é pequena e clara, até uns 3 arquivos | Todo o custo de orquestrar |
| `cascade` | Uma mudança mecânica com checagem automática | Quase todo o custo, e as novas tentativas são raras |
| `scout-then-build` | Código desconhecido, muito para ler e pouco para escrever | O seu contexto: scouts devolvem resumos, não arquivos |
| `plan-big-execute-small` | Até uns 10 leituras, auditorias ou checagens independentes | O tier forte só divide e sintetiza |
| `audit-at-scale` | Dezenas ou centenas de itens parecidos | O volume roda no tier barato |
| `parallel-worktrees` | Módulos independentes com arquivos separados | Tempo, não tokens |
| `plan-execute` | Uma feature construída em passos em sequência | O tier forte só é cobrado no planejamento |
| `advisor-escalation` | Uma tarefa mecânica longa com poucas decisões difíceis | Um conselheiro forte só nos pontos de decisão |
| `best-of-n` | Uma correção difícil e importante, que vale várias tentativas | Nada: compra confiabilidade |
| `review-gate` | Um diff pronto antes do merge | Revisão forte só em diffs que valem a pena |
| `complete` | Qualquer outra coisa; as condições decidem por unidade | Cada unidade roda no papel mais barato que serve |

## Agentes

| Agente | CLI | Só leitura | Escrita | Retomar | Informa |
|:-:|---|---|---|:-:|---|
| <img src="./docs/assets/agents/claude-code.png" width="28" height="28" alt=""><br>**Claude Code** | `claude` | permission mode `plan` | `acceptEdits` | ✓ | Tokens e custo |
| <img src="./docs/assets/agents/codex.png" width="28" height="28" alt=""><br>**Codex** | `codex` | sandbox `read-only` | sandbox `workspace-write` | ✓ | Tokens |
| <img src="./docs/assets/agents/antigravity.png" width="28" height="28" alt=""><br>**Antigravity** | `agy` | modo `plan` | modo `accept-edits` | ✓ | Tokens |
| <picture><source media="(prefers-color-scheme: dark)" srcset="./docs/assets/agents/kimi-dark.svg"><img src="./docs/assets/agents/kimi-light.svg" width="28" height="28" alt=""></picture><br>**Kimi Code** | `kimi` | Contrato, conferido depois | Modo prompt, na própria worktree | ✓ | Nada |
| <picture><source media="(prefers-color-scheme: dark)" srcset="./docs/assets/agents/shell-dark.svg"><img src="./docs/assets/agents/shell-light.svg" width="28" height="28" alt=""></picture><br>**Qualquer outra CLI** | o seu adapter | Contrato, conferido depois | A flag de escrita mais restrita dela | | O que a saída trouxer |

Qualquer outra CLI com modo não interativo, como Kiro, Copilot CLI ou Gemini CLI, roda como worker customizado.

## Por Que É Diferente

- **Gasto real, nunca estimado:** tokens e custo como cada CLI informou, separados entre resultados usados e descartados.
- **Limites aplicados:** orçamento por wave e por orquestração, teto de custo nos workers Claude, sem subagentes.
- **Nunca deixa a cota estourar:** lê o uso do Claude e do Codex antes de cada wave, não inicia workers passada a linha de parada e te pergunta, com opções, antes de gastar além dela.
- **Workers enxutos e conferidos:** sem servidores MCP nos workers Codex e Claude, e o git pega um worker só leitura que escreveu.
- **Fácil de auditar:** só a biblioteca padrão do Python, sem shell, sem rede, sem scripts de instalação.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./docs/assets/drawings/savings-dark.svg">
    <img src="./docs/assets/drawings/savings-light.svg" alt="Dois gráficos de barras desenhados à mão. À esquerda: um worker Claude haiku na mesma tarefa de uma linha usou 76.160 tokens e US$ 0,16 na versão 11, e 38.157 tokens e US$ 0,08 na versão 12. À direita: numa execução real com dois workers, 5,0 KB de transcrições ficaram no disco e o líder leu 595 bytes de resultados" width="820">
  </picture>
</p>

## O Relatório

`/alethe report html` abre o mesmo relatório como página: um mapa de roteamento de cada wave,
passando pelo líder, até os provedores que ela usou, com a fatia de tokens de cada um, depois os
workers e o que falta.

> **Orchestration report**
>
> **Check the report on real CLIs**, playbook `research`, 1 wave, 2026-10-18 21:20 to 21:20
>
> - **Result:** 2 of 2 workers succeeded
> - **Time:** 16s running, 29s of worker time
> - **Tokens:** 108,820 reported, 65,836 of them from cache
> - **Cost:** $0.16 reported by claude; codex reports no cost
> - **Lead context:** 5.0 KB of worker transcripts stayed on disk, 595 B of results to read
>
> | Worker | Role | Ran on | Mode | Status | Time | Tokens | Cost |
> | --- | --- | --- | --- | --- | --- | --- | --- |
> | `claude-haiku` | cheapest Claude model for a one-line answer | claude, haiku | read | succeeded | 12s | 76,160 | $0.16 |
> | `codex-low` | default Codex model at low effort | codex, default model, low | read | succeeded | 16s | 32,660 | - |

<details>
<summary>Comandos do runner e arquivos</summary>

O seu agente roda estes comandos por você, a partir de
`skills/alethe/scripts/orchestrate.py`:

| Etapa | Comando | O que faz |
| --- | --- | --- |
| Setup | `doctor`, `playbook`, `editor`, `policy`, `mode` | Confere as CLIs, mostra, abre ou edita playbooks e preferências, deixa ligado |
| Plano | `scaffold`, `validate`, `dry-run` | Cria, valida e mostra a prévia de um plano |
| Execução | `new`, `run --in current`, `budget`, `note`, `stop` | Roda as waves de uma orquestração |
| Depois | `status`, `mark`, `report`, `list`, `use` | Explica e avalia o que rodou |

A política e os seus playbooks ficam em `%APPDATA%\Alethe\` no Windows e em `~/.config/alethe/`
nos outros sistemas, com uma pasta por orquestração dentro de `orchestrations/`:

```text
<pasta>/
├── budget.json              limites para todas as waves, opcional
├── 01-explore/
│   ├── state.json           a execução num relance, ao vivo enquanto roda
│   ├── plan.json            o plano resolvido, sem o texto das tarefas
│   └── workers/<id>/
│       ├── prompt.txt       o brief que o worker recebeu
│       ├── result.md        a resposta final dele
│       ├── outcome.json     status, tipo de falha, métricas, tripwire
│       ├── diff.patch       o que um writer mudou
│       ├── mark.json        used, partial ou discarded, opcional
│       └── stdout.log, stderr.log
└── 02-build/
```

</details>

## Vídeo

<table>
  <tr>
    <th width="38%">Vídeo</th>
    <th>O que você vai ver</th>
  </tr>
  <tr>
    <td>
      <a href="https://www.youtube.com/watch?v=8jvrucR7QCU&amp;t=54s">
        <img src="https://i.ytimg.com/vi/8jvrucR7QCU/hqdefault.jpg" alt="Stop Using One AI Agent at a Time: Orchestrate AI Agents" width="100%">
      </a>
    </td>
    <td>
      <strong><a href="https://www.youtube.com/watch?v=8jvrucR7QCU&amp;t=54s">Stop Using One AI Agent at a Time: Orchestrate AI Agents</a></strong>
      <br><br>
      Uma introdução prática a coordenar vários agentes de IA de código, e ao isolamento com git
      worktree que evita que um atrapalhe o outro.
      <br><br>
      <sub>Kauã Miguel - Dev · Português</sub>
    </td>
  </tr>
</table>

## Exemplo de Playbook

`scout-then-build`: scouts baratos e só leitura mapeiam o código, depois um worker escreve a mudança.
`/alethe editor` abre os seus playbooks assim, numa página offline onde você troca de perfil,
simula a cota e edita um passo, depois copia o YAML de volta. Funciona também sem Python.

<p align="center">
  <img src="./docs/assets/playbook-scout-then-build.png" alt="O playbook scout-then-build como fluxo: planner, quatro scouts, um checker quando discordam, planner, builder e um fixer quando as checagens falham" width="100%">
</p>

<details>
<summary>O YAML por trás</summary>

```yaml
playbooks:
  scout-then-build:
    name: Scout, then build
    goal: Cheap read-only scouts map the code; one standard worker writes the change.
    pickWhen: Unfamiliar code with much to read and little to write.
    saves: Scouts hand back short summaries instead of raw files filling your context.
    avoidWhen: You already know which files change.
    next: review-gate
    phases:
      - id: brief
        role: planner
        who: lead
        tier: strong
        readOnly: true
        does: Name the areas to map and send one scout per area.
      - id: scout
        role: scout
        tier: cheap
        readOnly: true
        workers: 4
        runner: research
        does: One area per scout; keep each slice well under 100K tokens.
        returns: A summary under 1.5K tokens with file:line pointers.
        gate: The scout reports agree.
      - id: settle
        role: checker
        tier: standard
        effort: high
        readOnly: true
        runner: economy
        after: scout
        if: conflict
        then: recheck
        does: Settle only the contradiction, from the cited lines.
      - id: plan
        role: planner
        who: lead
        tier: strong
        readOnly: true
        does: Turn the summaries into one concrete change plan.
      - id: build
        role: builder
        tier: standard
        readOnly: false
        runner: economy
        does: Implement the plan.
        returns: Changed files, checks run, failures and risks.
        gate: Checks pass.
      - id: fix
        role: fixer
        tier: strong
        readOnly: false
        runner: economy
        after: build
        if: checks-failed
        then: recheck
        does: Fix from the failure excerpt and the diff.
```

</details>

## Licença

MIT, veja [`LICENSE`](LICENSE). O nome e o logo **Alethe** e as marcas dos fornecedores pertencem aos donos. Parte do [Alethe](https://github.com/Kc1t/alethe-agents) · [Changelog](CHANGELOG.md) · [Issues](https://github.com/Kc1t/alethe-orchestrate-skill/issues)

<p align="right">(<a href="#readme-top">voltar ao topo</a>)</p>
