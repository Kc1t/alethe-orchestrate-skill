# Alethe Orchestrate: arquitetura e funcionalidades

Documento de análise do repositório (versão 21 da skill) e plano para o widget de Windows.

## 1. O que é

Uma **Agent Skill** (`SKILL.md`) e plugin para Claude Code e Codex. Um agente "líder" planeja o trabalho e entrega
tarefas limitadas para outros agentes de CLI (**Claude Code, Codex, Antigravity, Kimi** ou qualquer CLI),
seguindo *playbooks* que economizam contexto e cota. No fim, gera um relatório com tokens e custo reais.

Não há servidor, banco de dados nem dependência externa: é **Python stdlib** (3.9+) chamando CLIs como subprocessos,
sem shell e sem rede, mais duas páginas HTML offline.

## 2. Estrutura do repositório

```text
.claude-plugin/ .codex-plugin/ .agents/    manifestos de plugin e marketplace
hooks/hooks.json                            hook UserPromptSubmit -> orchestrate.py hook
skills/alethe/
├── SKILL.md                                instruções que o agente líder lê
├── playbooks.yaml                          11 playbooks (estratégias) embutidos
├── prices.json                             preços por modelo (custo/forecast)
├── schemas/plan.schema.json                contrato do plano de execução
├── scripts/orchestrate.py                  runner (~5.800 linhas, único módulo de lógica)
├── scripts/diagram.py                      gera diagramas do plano
├── assets/editor.html, report.html         editor de playbooks e relatório (HTML offline)
├── assets/brands/                          logos dos provedores
└── references/*.md                         manuais carregados sob demanda (routing, providers, ...)
tools/check_playbooks.py                    validador de playbooks (CI)
tests/                                      pytest + CLIs falsas (fake_cli.py etc.)
docs/assets/                                imagens e desenhos do README
```

## 3. Arquitetura

```text
Usuário ──/alethe──▶ Agente líder (Claude Code / Codex)
                       │ lê SKILL.md + playbook escolhido
                       │ escreve plan.json (workers, briefs, modos, budgets)
                       ▼
              orchestrate.py  (validate → dry-run → run)
                       │ ThreadPool, até 16 workers, timeout 1200 s
        ┌──────────────┼───────────────┬──────────────┐
        ▼              ▼               ▼              ▼
     claude         codex            agy            kimi / custom
   (plan/accept)  (sandbox)        (plan/accept)   (contrato + git check)
        │  cada worker: git worktree próprio se escreve
        ▼
   disco: state.json, outcome.json, result.md, diff.patch, stdout/stderr.log
                       ▲
 Líder lê só result.md ┘ → mark used/partial/discarded → report
```

### Camadas do `orchestrate.py`

| Camada | Responsabilidade |
|---|---|
| **Playbooks** | Parser YAML próprio (sem PyYAML), validação estrutural, cache, merge do playbook do usuário (`alethe-playbooks.yaml`) sobre o embutido |
| **Policy** | `alethe-orchestrate.json`: providers, tiers de modelo (cheap/standard/strong), budgets, limiares de cota |
| **Plano** | `validate_plan`: IDs, providers, estratégia, limites, paths relativos, prompts; `dry-run` mostra o que rodaria sem executar |
| **Execução** | `run_batch` / `run_worker`: monta o comando de cada CLI, aplica modo leitura vs escrita, worktrees git, timeout, job object no Windows, parada via arquivo `stop` |
| **Salvaguardas** | Orçamento por wave e por orquestração, teto de custo nos workers Claude, bloqueio de flags inseguras, tripwire (git detecta worker "read-only" que escreveu), sem subagentes aninhados |
| **Cota** | Lê uso de Claude e Codex antes de cada wave, prevê consumo, recusa iniciar passando da linha de parada |
| **Observabilidade** | `status`, `wait`, `report` (md/json/html), `journal.jsonl` com cada mudança de rota e seu motivo |
| **Modos / hook** | `always`/`on`/`freestyle`: o hook `UserPromptSubmit` injeta lembrete de objetivo e regras a cada prompt; expira após 12 h sem uso |
| **Editor** | `editor.html` autocontido: edita playbooks, simula cota, copia o YAML de volta |

### Modelo de dados em disco

Base: `%APPDATA%\Alethe\` (Windows) ou `~/.config/alethe/`; sobrescrevível por `ALETHE_ORCHESTRATE_POLICY` e
`ALETHE_ORCHESTRATE_HOME`.

```text
orchestrations/
├── current.json                 {folder, updatedAt}  → orquestração ativa
└── <orquestração>/
    ├── orchestration.json, journal.jsonl, budget.json
    └── 01-explore/
        ├── state.json           status do batch e de cada worker, ao vivo
        ├── plan.json
        └── workers/<id>/        prompt.txt, result.md, outcome.json, diff.patch, mark.json, logs
```

`state.json` é escrito de forma atômica (`atomic_write_json`) e é a **interface natural para qualquer UI externa**.

## 4. Funcionalidades

**Comandos do usuário** (`/alethe ...`): `setup`, `<tarefa>`, `always`, `freestyle`, `status`, `stop`, `report`, `off`, `editor`, `mark`.

**Playbooks:** `direct`, `cascade`, `scout-then-build`, `plan-big-execute-small`, `audit-at-scale`, `parallel-worktrees`,
`plan-execute`, `advisor-escalation`, `best-of-n`, `review-gate`, `complete`.

**Mudança de rota no meio do caminho:** `skip`, `add`, `switch`, `freeform` entre waves, com justificativa no relatório.

**Join mid-task:** entra no meio do workflow de outra skill, usando spec e diff como plano.

**Providers:** Claude Code, Codex, Antigravity (`agy`), Kimi, e CLIs customizadas (adapters). Resume suportado nos quatro primeiros.

**Custo real:** tokens e custo conforme reportados por cada CLI, separados em usado e descartado; nunca estimados.

**Runner CLI** (`python scripts/orchestrate.py ...`): `probe, doctor, strategies, policy, playbook, usage, models, scaffold,
validate, dry-run, new, run, wait, budget, note, status, stop, mark, report, list, use, editor, mode, hook`.

**Qualidade:** testes em `tests/` com CLIs falsas, CI em `.github/workflows/ci.yml`, validação de playbooks em `tools/`.

## 5. Novo requisito: widget de barra para Windows

### Objetivo

Um programa de Windows que vira **uma barra retangular fina** (sempre no topo) com **personagens pixelados trabalhando
no PC**. O personagem exibido muda conforme a IA que está trabalhando no momento: Claude Code, Codex, Antigravity, Kimi,
custom. Sem worker ativo, o personagem dorme/descansa.

### Fonte de dados (sem alterar o runner)

O widget só **lê arquivos** que o `orchestrate.py` já grava:

1. Ler `orchestrations/current.json` → pasta ativa.
2. Fazer polling (~1 s) dos `state.json` das waves dessa pasta.
3. Workers com `status == "running"` → `provider` (`claude`, `codex`, `agy`, `kimi`, custom) decide o personagem.
4. Vários ativos ao mesmo tempo → vários personagens lado a lado, ou rodízio.
5. `status` do batch (`queued`, `running`, `succeeded`, `failed`) → animação (digitando, comemorando, erro).

Limitação a decidir: o **agente líder** (a sessão interativa do Claude Code/Codex) não aparece em `state.json`, só os
workers. Para mostrar o líder também, opções: (a) um comando `orchestrate.py lead-active <provider>` chamado pelo hook
existente, gravando um arquivo pequeno; (b) detectar processos `claude.exe`/`codex.exe` com CPU ativa. Recomendado: (a),
é só uma linha no hook já existente.

### Tecnologia

| Opção | Prós | Contras |
|---|---|---|
| **Python + tkinter** (recomendada para MVP) | Zero dependência, coerente com o projeto (stdlib only), janela sem borda + `-topmost` + `-transparentcolor` | Visual menos polido, sprites via `PhotoImage` com zoom inteiro |
| Electron | Fácil animar sprites com CSS/canvas, node já instalado | ~100 MB, pesado para uma barra |
| Tauri (Rust) | Leve e bonito, `.exe` pequeno | Exige instalar Rust, não existe no ambiente hoje |

Ponto de decisão: se o objetivo é distribuir um `.exe` para outras pessoas sem exigir Python, vale Tauri. Para uso
próprio e iterar rápido, tkinter.

### Design proposto

```text
widget/
├── alethe_bar.py        janela + loop de polling + animação (~200 linhas)
├── sprites/             um PNG de sprite sheet por provider (ex.: 16x16 px, 4 frames de "trabalhando")
└── README.md
```

- Janela: `overrideredirect(True)`, `-topmost`, altura ~48 px, largura ~400 px, arrastável, posição salva.
- Cada personagem: sprite sheet com estados `idle`, `work`, `done`, `error`; ampliado com *nearest neighbor* para manter o pixel art.
- Cor/identidade por provider: laranja (Claude), preto/branco (Codex), azul (Antigravity), etc., alinhadas com `docs/assets/agents/`.
- Texto mínimo: nome do worker e tempo decorrido; clique abre `report.html` da orquestração atual.
- Empacotamento: `pyinstaller --onefile --noconsole` e atalho na pasta Inicializar para abrir com o Windows.

### Etapas

1. **MVP:** barra + leitura de `current.json`/`state.json` + 1 sprite por provider + estado idle/work.
2. Estados `done`/`error`, múltiplos personagens simultâneos, arrastar e salvar posição.
3. Hook do líder (`lead-active`) para mostrar a IA principal.
4. Empacotar `.exe` e iniciar com o Windows.

### Pendências para o usuário

- Os sprites precisam ser criados (à mão, em editor de pixel art, ou gerados). Posso gerar um conjunto inicial em código.
- Confirmar se o widget deve ficar **dentro deste repositório** (`widget/`) ou em um projeto separado que apenas lê a pasta de dados.
- Confirmar tkinter (MVP) vs Tauri (distribuição).
